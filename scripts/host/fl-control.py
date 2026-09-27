#!/usr/bin/env python3
"""Operate protocol-driven hierarchical FL training requests."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from configlib import (
    expected_runtime_inventory,
    load_runtime_manifest,
    load_yaml,
    resolve_config_dir,
    resolve_config_scenario,
    resolve_path,
    sha256_tree,
)


ROOT = Path(__file__).resolve().parents[2]
TRAINING_PATH = "/internal/v1/federated-learning/training-requests"


class ControlError(RuntimeError):
    """A fail-closed operator-contract violation."""


class TransportError(ControlError):
    """An ambiguous HTTP transport failure."""


@dataclass(frozen=True)
class Response:
    status: int
    body: dict
    headers: dict[str, str]


@dataclass(frozen=True)
class Contract:
    config_dir: Path
    config_set: str
    config_hash: str
    training_mode: str
    services: tuple[str, ...]
    retained_services: tuple[str, ...]
    coordinator_service: str
    coordinator_endpoint: str
    model_families: tuple[str, ...]
    request_timeout_seconds: int


class HttpClient:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def request(self, method: str, url: str, payload: dict | None = None) -> Response:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as value:
                return Response(
                    value.status,
                    _json_body(value.read(), url),
                    dict(value.headers.items()),
                )
        except urllib.error.HTTPError as error:
            return Response(
                error.code,
                _json_body(error.read(), url, allow_empty=True),
                dict(error.headers.items()),
            )
        except (TimeoutError, urllib.error.URLError, OSError) as error:
            raise TransportError(
                "{} {} failed: {}".format(method, url, type(error).__name__)
            ) from error


def _json_body(raw: bytes, url: str, *, allow_empty: bool = False) -> dict:
    if not raw and allow_empty:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ControlError("{} returned an invalid JSON body".format(url)) from error
    if not isinstance(value, dict):
        raise ControlError("{} returned a non-object JSON body".format(url))
    return value


def canonical_run_id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as error:
        raise ControlError("RUN_ID must be a canonical lowercase UUIDv4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ControlError("RUN_ID must be a canonical lowercase UUIDv4")
    return value


def _public_endpoint(config: dict, service: str) -> str:
    endpoint = config.get("artifact", {}).get("public_base_url")
    if not isinstance(endpoint, str) or not endpoint.startswith("http://"):
        raise ControlError(
            "{} must declare an HTTP artifact.public_base_url".format(service)
        )
    return endpoint.rstrip("/")


def load_contract(testbed_name: str, explicit_config: str = "") -> Contract:
    testbed = load_yaml(resolve_path(testbed_name))
    config_dir = resolve_config_dir(testbed, explicit_config or None).resolve()
    manifest = load_runtime_manifest(config_dir)
    runtime = manifest["runtime"]
    _scenario_path, scenario = resolve_config_scenario(config_dir)
    expected = expected_runtime_inventory(testbed, scenario)
    for key in (
        "deploymentKind",
        "nwdafs",
        "hostContainers",
        "coordinatorContainer",
    ):
        if runtime.get(key) != expected.get(key):
            raise ControlError(
                "runtime.{} does not exactly match selected TESTBED".format(key)
            )
    if runtime.get("deploymentKind") != "protocol-hierarchical":
        raise ControlError("FL control requires protocol-hierarchical deployment")

    services = tuple(runtime["hostContainers"])
    coordinator = runtime.get("coordinatorContainer")
    if not isinstance(coordinator, str) or coordinator not in services:
        raise ControlError("coordinatorContainer is not in the Host inventory")
    roots = [item for item in runtime["nwdafs"] if item.get("role") == "root"]
    if len(roots) != 1 or roots[0].get("backends", {}).get("mtlf") != coordinator:
        raise ControlError("runtime must map one Root NWDAF to the coordinator")

    server = load_yaml(config_dir / (coordinator + ".yaml"))
    orchestration = server.get("federated_learning", {}).get("orchestration", {})
    trigger = server.get("federated_learning", {}).get("training_trigger", {})
    if orchestration != {"mode": "hierarchical", "participant_source": "static"}:
        raise ControlError("Root must declare hierarchical static orchestration")
    if trigger.get("private_api", {}).get("enabled") is not True:
        raise ControlError("Root private training trigger is disabled")

    families = server.get("model_provision", {}).get("seed_models")
    family_ids = tuple(
        item.get("family_id") for item in families or [] if isinstance(item, dict)
    )
    if len(family_ids) != 1 or not family_ids[0]:
        raise ControlError("Root must declare exactly one model family")
    settings = server.get("federated_learning", {}).get("server", {})
    round_count = settings.get("round_count")
    round_timeout = settings.get("round_timeout_seconds")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in (round_count, round_timeout)
    ):
        raise ControlError("Root training limits are invalid")
    if scenario["training"]["acceptedRounds"] != round_count:
        raise ControlError("Root round count differs from the scenario")

    return Contract(
        config_dir=config_dir,
        config_set=config_dir.name,
        config_hash=sha256_tree(config_dir),
        training_mode="hierarchical",
        services=services,
        retained_services=tuple(runtime["resetScope"]["hostContainers"]),
        coordinator_service=coordinator,
        coordinator_endpoint=_public_endpoint(server, coordinator),
        model_families=family_ids,
        request_timeout_seconds=min(30, round_timeout),
    )


def verify_runtime_identity(contract: Contract) -> None:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "host" / "ml-status.py"),
        "--project",
        "5g-nwdaf-infrastructure",
        "--services",
        ",".join(contract.services),
        "--retained-services",
        ",".join(contract.retained_services),
        "--coordinator",
        contract.coordinator_service,
        "--config-set",
        contract.config_set,
        "--config-hash",
        contract.config_hash,
        "--identity-only",
        "--require-running-selected",
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "unknown identity mismatch"
        raise ControlError(
            "selected/active ML config identity check failed: {}".format(detail)
        )


def _request_path(endpoint: str, run_id: str | None = None) -> str:
    return endpoint + TRAINING_PATH + ("" if run_id is None else "/" + run_id)


def _problem(response: Response) -> str:
    return "status={} cause={} detail={}".format(
        response.status,
        response.body.get("cause", "unknown"),
        response.body.get("detail", "unknown"),
    )


class Controller:
    def __init__(self, contract: Contract, http: object):
        self.contract = contract
        self.http = http

    def require_ready(self) -> None:
        endpoint = self.contract.coordinator_endpoint
        response = self.http.request("GET", endpoint + "/health/ready")
        if response.status != 200 or response.body.get("status") != "ready":
            raise ControlError("{} is not ready: {}".format(endpoint, _problem(response)))

    def model_family(self, supplied: str | None) -> str:
        if supplied:
            if supplied not in self.contract.model_families:
                raise ControlError(
                    "MODEL_FAMILY_ID does not match selected coordinator config"
                )
            return supplied
        return self.contract.model_families[0]

    def training_start(self, run_id: str, family: str | None) -> dict:
        self.require_ready()
        family_id = self.model_family(family)
        url = _request_path(self.contract.coordinator_endpoint)
        payload = {"requestId": run_id, "modelFamilyId": family_id}
        try:
            response = self.http.request("POST", url, payload)
        except TransportError:
            existing = self.http.request(
                "GET", _request_path(self.contract.coordinator_endpoint, run_id)
            )
            if existing.status == 200:
                return self._validate_training(run_id, family_id, existing)
            if existing.status != 404:
                raise
            response = self.http.request("POST", url, payload)
        if response.status == 202:
            return self._validate_training(run_id, family_id, response)
        if response.status == 409:
            existing = self.http.request(
                "GET", _request_path(self.contract.coordinator_endpoint, run_id)
            )
            if existing.status == 200:
                return self._validate_training(run_id, family_id, existing)
        raise ControlError("training POST failed: {}".format(_problem(response)))

    def training_status(self, run_id: str, family: str | None = None) -> dict:
        family_id = self.model_family(family) if family else None
        response = self.http.request(
            "GET", _request_path(self.contract.coordinator_endpoint, run_id)
        )
        if response.status != 200:
            raise ControlError("training GET failed: {}".format(_problem(response)))
        return self._validate_training(run_id, family_id, response)

    def _validate_training(
        self, run_id: str, family: str | None, response: Response
    ) -> dict:
        body = response.body
        if body.get("requestId") != run_id:
            raise ControlError("training resource request identity mismatch")
        if family is not None and body.get("modelFamilyId") != family:
            raise ControlError("training resource model family mismatch")
        if (
            body.get("mode") != self.contract.training_mode
            or body.get("participantSource") != "static"
            or body.get("triggerSource") != "private_api"
        ):
            raise ControlError(
                "training resource is not hierarchical static private-API flow"
            )
        return body


def _print_training(value: dict) -> None:
    print(
        "FL TRAINING request={} family={} mode={} participants={} state={} current_round={} completed_rounds={} candidate={} failure_cause={} failure_detail={}".format(
            value.get("requestId", "unknown"),
            value.get("modelFamilyId", "unknown"),
            value.get("mode", "unknown"),
            value.get("participantSource", "unknown"),
            value.get("state", "unknown"),
            value.get("currentRound", "none"),
            value.get("completedRounds", "none"),
            value.get("candidateDigest", "none"),
            value.get("failureCause", "none"),
            str(value.get("failureDetail", "none")).replace("\n", " "),
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("training-start", "training-status"))
    parser.add_argument("--testbed", required=True)
    parser.add_argument("--config-dir", default="")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model-family-id", default="")
    args = parser.parse_args()
    try:
        run_id = canonical_run_id(args.run_id)
        contract = load_contract(args.testbed, args.config_dir)
        verify_runtime_identity(contract)
        controller = Controller(
            contract, HttpClient(timeout=contract.request_timeout_seconds)
        )
        if args.action == "training-start":
            value = controller.training_start(run_id, args.model_family_id or None)
        else:
            controller.require_ready()
            value = controller.training_status(run_id, args.model_family_id or None)
        _print_training(value)
    except (ControlError, ValueError) as error:
        print("ERROR {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
