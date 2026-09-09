#!/usr/bin/env python3
"""Operate manifest-selected static Flat or Hierarchical FL requests."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from configlib import (
    load_runtime_manifest,
    load_yaml,
    expected_runtime_inventory,
    resolve_config_dir,
    resolve_config_scenario,
    resolve_path,
    sha256_tree,
)


ROOT = Path(__file__).resolve().parents[2]
COLLECTION_PATH = "/internal/v1/training-data-collections"
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
class Owner:
    position: int
    service: str
    endpoint: str
    profile_id: str
    internal_group_id: str
    supis: tuple[str, ...]


@dataclass(frozen=True)
class Contract:
    config_dir: Path
    config_set: str
    config_hash: str
    deployment_kind: str
    training_mode: str
    services: tuple[str, ...]
    coordinator_service: str
    coordinator_endpoint: str
    owners: tuple[Owner, ...]
    model_families: tuple[str, ...]
    minimum_samples: int
    closure_budget_seconds: int
    preparation_window_seconds: int


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
            raise TransportError("{} {} failed: {}".format(method, url, type(error).__name__)) from error


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
        raise ControlError("{} must declare an HTTP artifact.public_base_url".format(service))
    if endpoint.endswith("/"):
        endpoint = endpoint[:-1]
    return endpoint


def load_contract(testbed_name: str, explicit_config: str = "") -> Contract:
    testbed_path = resolve_path(testbed_name)
    testbed = load_yaml(testbed_path)
    config_dir = resolve_config_dir(testbed, explicit_config or None).resolve()
    manifest = load_runtime_manifest(config_dir)
    runtime = manifest["runtime"]
    expected_runtime = expected_runtime_inventory(testbed)
    for key in (
        "deploymentKind",
        "nwdafs",
        "dataOwners",
        "hostContainers",
        "subscriptions",
        "coordinatorContainer",
    ):
        if runtime.get(key) != expected_runtime.get(key):
            raise ControlError(
                "runtime.{} does not exactly match selected TESTBED".format(key)
            )
    kind = runtime.get("deploymentKind")
    if kind not in ("static-flat", "static-hierarchical"):
        raise ControlError(
            "FL control requires deploymentKind=static-flat or static-hierarchical"
        )
    if runtime.get("subscriptions") != "none":
        raise ControlError("static FL control requires runtime.subscriptions=none")

    mode = "flat" if kind == "static-flat" else "hierarchical"
    owner_role = "client" if kind == "static-flat" else "leaf"
    coordinator_role = "server" if kind == "static-flat" else "root"

    services = tuple(runtime["hostContainers"])
    coordinator_service = runtime.get("coordinatorContainer")
    if not isinstance(coordinator_service, str) or coordinator_service not in services:
        raise ControlError("static coordinatorContainer is not in the Host inventory")

    nwdafs = runtime.get("nwdafs")
    data_owners = runtime.get("dataOwners")
    if not isinstance(nwdafs, list) or not isinstance(data_owners, list):
        raise ControlError("static manifest must declare NWDAFs and data owners")
    by_backend = {
        item.get("backends", {}).get("mtlf"): item
        for item in nwdafs
        if isinstance(item, dict)
    }
    analytics = testbed.get("analytics")
    if not isinstance(analytics, dict):
        raise ControlError("selected TESTBED analytics inventory is missing")
    participants_by_owner = {}
    for nwdaf in nwdafs:
        if nwdaf.get("role") != owner_role:
            continue
        source = analytics.get(nwdaf.get("unit"), {})
        position = source.get("dataOwner") if isinstance(source, dict) else None
        if not isinstance(position, int) or position in participants_by_owner:
            raise ControlError(
                "static {}-to-data-owner mapping is invalid".format(owner_role)
            )
        participants_by_owner[position] = nwdaf
    if set(participants_by_owner) != {1, 2, 3, 4}:
        raise ControlError("static topology must map exact data owners 1-4")

    branches = [item for item in nwdafs if item.get("role") == "branch"]
    branch_services = tuple(item["backends"]["mtlf"] for item in branches)
    if kind == "static-flat" and branches:
        raise ControlError("static Flat must not declare Branch NWDAFs")
    if kind == "static-hierarchical" and len(branches) != 2:
        raise ControlError("static Hierarchical must declare exactly two Branch NWDAFs")
    expected_services = (
        (coordinator_service,)
        + branch_services
        + tuple(
            participants_by_owner[position]["backends"]["mtlf"]
            for position in range(1, 5)
        )
    )
    if services != expected_services:
        raise ControlError(
            "static Host inventory must be coordinator, ordered Branches, then owners 1-4"
        )
    if set(by_backend) != set(expected_services):
        raise ControlError("static NWDAF-to-PyMTLF backend mapping is incomplete")
    if by_backend[coordinator_service].get("role") != coordinator_role:
        raise ControlError(
            "static {} coordinator NWDAF must have role={}".format(kind, coordinator_role)
        )

    positions = [item.get("position") for item in data_owners if isinstance(item, dict)]
    if positions != [1, 2, 3, 4]:
        raise ControlError("static data owners must be ordered positions 1-4")
    seen_supis: set[str] = set()
    owners = []
    for item in data_owners:
        position = item["position"]
        nwdaf = participants_by_owner[position]
        service = nwdaf["backends"]["mtlf"]
        supis = item.get("supis")
        if not isinstance(supis, list) or len(supis) != 2 or len(set(supis)) != 2:
            raise ControlError("data owner {} must own exactly two unique SUPIs".format(position))
        if seen_supis.intersection(supis):
            raise ControlError("static data-owner SUPIs must be disjoint")
        seen_supis.update(supis)

        native = load_yaml(config_dir / (service + ".yaml"))
        client = native.get("federated_learning", {}).get("client", {})
        training_data = client.get("training_data", {})
        profiles = training_data.get("collection_profiles")
        if training_data.get("collection_trigger") != "private_api" or not isinstance(profiles, list) or len(profiles) != 1:
            raise ControlError("{} must declare one private collection profile".format(service))
        profile = profiles[0]
        expected_profile = "data-owner-{}".format(position)
        if profile.get("profile_id") != expected_profile:
            raise ControlError("{} collection profile identity is stale".format(service))
        groups = profile.get("target_ue", {}).get("intGroupIds")
        if groups != [item.get("internalGroupId")]:
            raise ControlError("{} collection group does not match the manifest owner".format(service))
        owners.append(
            Owner(
                position=position,
                service=service,
                endpoint=_public_endpoint(native, service),
                profile_id=expected_profile,
                internal_group_id=item["internalGroupId"],
                supis=tuple(supis),
            )
        )

    if kind == "static-hierarchical":
        _validate_hierarchical_branches(config_dir, branch_services)

    server = load_yaml(config_dir / (coordinator_service + ".yaml"))
    orchestration = server.get("federated_learning", {}).get("orchestration", {})
    trigger = server.get("federated_learning", {}).get("training_trigger", {})
    if orchestration != {"mode": mode, "participant_source": "static"}:
        raise ControlError(
            "selected coordinator must declare {} + static orchestration".format(mode)
        )
    if trigger.get("private_api", {}).get("enabled") is not True:
        raise ControlError("selected coordinator private training trigger is disabled")
    if kind == "static-hierarchical":
        _validate_hierarchical_topology(
            config_dir,
            testbed,
            server,
            nwdafs,
        )
    families = server.get("model_provision", {}).get("seed_models")
    family_ids = tuple(item.get("family_id") for item in families or [] if isinstance(item, dict))
    if not family_ids or len(family_ids) != len(set(family_ids)) or any(not item for item in family_ids):
        raise ControlError("selected coordinator must declare unique seed model families")
    server_settings = server.get("federated_learning", {}).get("server", {})
    preparation_window = server_settings.get("preparation_data_window_seconds")
    if not isinstance(preparation_window, int) or preparation_window <= 0:
        raise ControlError("selected coordinator preparation data window is invalid")

    _, scenario = resolve_config_scenario(config_dir)
    training = scenario.get("training", {})
    minimum_samples = training.get("minimumSamples")
    closure_budget = training.get("closureBudgetSeconds")
    if not isinstance(minimum_samples, int) or minimum_samples <= 0:
        raise ControlError("scenario training.minimumSamples must be positive")
    if not isinstance(closure_budget, int) or closure_budget <= 0:
        raise ControlError("scenario training.closureBudgetSeconds must be positive")

    return Contract(
        config_dir=config_dir,
        config_set=config_dir.name,
        config_hash=sha256_tree(config_dir),
        deployment_kind=kind,
        training_mode=mode,
        services=services,
        coordinator_service=coordinator_service,
        coordinator_endpoint=_public_endpoint(server, coordinator_service),
        owners=tuple(owners),
        model_families=family_ids,
        minimum_samples=minimum_samples,
        closure_budget_seconds=closure_budget,
        preparation_window_seconds=preparation_window,
    )


def _validate_hierarchical_branches(
    config_dir: Path, branch_services: tuple[str, ...]
) -> None:
    for service in branch_services:
        native = load_yaml(config_dir / (service + ".yaml"))
        fl = native.get("federated_learning", {})
        if not isinstance(fl.get("server"), dict) or not isinstance(fl.get("client"), dict):
            raise ControlError("{} must declare both FL client and server roles".format(service))
        training_data = fl["client"].get("training_data", {})
        if training_data.get("collection_trigger") != "consumer_subscription":
            raise ControlError("{} must not own a private collection".format(service))
        if "collection_profiles" in training_data:
            raise ControlError("{} must not declare collection profiles".format(service))
        if "orchestration" in fl or "training_trigger" in fl:
            raise ControlError("{} must not own Root orchestration".format(service))


def _validate_hierarchical_topology(
    config_dir: Path,
    testbed: dict,
    root: dict,
    nwdafs: list[dict],
) -> None:
    topology_config = root.get("federated_learning", {}).get("topology", {})
    topology_path = "topology/static-hierarchical.yaml"
    if topology_config != {"strategy": "static", "config_file": topology_path}:
        raise ControlError("selected Root must reference the generated static Hierarchical topology")

    by_unit = {item.get("unit"): item for item in nwdafs}
    analytics = testbed["analytics"]
    leaves = {}
    for unit, source in analytics.items():
        if not unit.startswith("nwdaf-") or not isinstance(source, dict):
            continue
        if source.get("role") == "leaf":
            leaves[source.get("dataOwner")] = by_unit[unit]["nfInstanceId"]
    branches = []
    for unit, source in analytics.items():
        if not unit.startswith("nwdaf-") or not isinstance(source, dict):
            continue
        if source.get("role") != "branch":
            continue
        positions = source.get("leaves")
        if (
            not isinstance(positions, list)
            or len(positions) != 2
            or len(set(positions)) != 2
            or any(position not in leaves for position in positions)
        ):
            raise ControlError("static Hierarchical Branch-to-Leaf mapping is invalid")
        branches.append(
            {
                "nf_instance_id": by_unit[unit]["nfInstanceId"],
                "leaves": [
                    {"nf_instance_id": leaves[position]} for position in positions
                ],
            }
        )
    expected = {
        "version": 1,
        "admission": {"mode": "complete_required"},
        "branches": branches,
    }
    if load_yaml(config_dir / topology_path) != expected:
        raise ControlError(
            "generated static Hierarchical topology does not exactly match selected TESTBED"
        )


def verify_runtime_identity(contract: Contract) -> None:
    command = [
        sys.executable,
        str(ROOT / "scripts/host/ml-status.py"),
        "--project",
        "5g-nwdaf-infrastructure",
        "--services",
        ",".join(contract.services),
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
        raise ControlError("selected/active ML config identity check failed: {}".format(detail))


def _request_path(endpoint: str, base: str, run_id: str | None = None) -> str:
    suffix = base if run_id is None else base + "/" + run_id
    return endpoint + suffix


def _problem(response: Response) -> str:
    return "status={} cause={} detail={}".format(
        response.status,
        response.body.get("cause", "unknown"),
        response.body.get("detail", "unknown"),
    )


def _validate_collection(owner: Owner, run_id: str, response: Response) -> dict:
    if response.status != 200:
        raise ControlError("{} collection GET failed: {}".format(owner.service, _problem(response)))
    body = response.body
    if body.get("requestId") != run_id or body.get("collectionProfileId") != owner.profile_id:
        raise ControlError("{} collection resource identity mismatch".format(owner.service))
    return body


def _parse_time(value: object, field: str) -> dt.datetime:
    if not isinstance(value, str):
        raise ControlError("collection {} is missing".format(field))
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ControlError("collection {} is invalid".format(field)) from error
    if parsed.tzinfo is None:
        raise ControlError("collection {} must include a timezone".format(field))
    return parsed


class Controller:
    def __init__(
        self,
        contract: Contract,
        http: object,
        *,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], dt.datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        poll_seconds: float = 2.0,
    ):
        self.contract = contract
        self.http = http
        self.sleep = sleep
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self.monotonic = monotonic
        self.poll_seconds = poll_seconds

    def require_ready(self, *, coordinator: bool = False) -> None:
        endpoints = [owner.endpoint for owner in self.contract.owners]
        if coordinator:
            endpoints.append(self.contract.coordinator_endpoint)
        for endpoint in endpoints:
            response = self.http.request("GET", endpoint + "/health/ready")
            if response.status != 200 or response.body.get("status") != "ready":
                raise ControlError("{} is not ready: {}".format(endpoint, _problem(response)))

    def collection_status(self, run_id: str) -> tuple[dict, ...]:
        values = []
        for owner in self.contract.owners:
            response = self.http.request(
                "GET", _request_path(owner.endpoint, COLLECTION_PATH, run_id)
            )
            values.append(_validate_collection(owner, run_id, response))
        return tuple(values)

    def collection_start(self, run_id: str) -> tuple[dict, ...]:
        self.require_ready()
        attempted: list[Owner] = []
        try:
            for owner in self.contract.owners:
                attempted.append(owner)
                self._create_collection(owner, run_id)
            return self._wait_collections(
                run_id,
                lambda value: value.get("state") == "COLLECTING",
                "COLLECTING",
            )
        except ControlError as error:
            failures = self._rollback_collections(attempted, run_id)
            suffix = "; rollback incomplete: " + ", ".join(failures) if failures else ""
            raise ControlError(str(error) + suffix) from error

    def _create_collection(self, owner: Owner, run_id: str) -> None:
        url = _request_path(owner.endpoint, COLLECTION_PATH)
        payload = {"requestId": run_id, "collectionProfileId": owner.profile_id}
        try:
            response = self.http.request("POST", url, payload)
        except TransportError:
            existing = self.http.request(
                "GET", _request_path(owner.endpoint, COLLECTION_PATH, run_id)
            )
            if existing.status == 200:
                _validate_collection(owner, run_id, existing)
                return
            if existing.status != 404:
                raise
            response = self.http.request("POST", url, payload)
        if response.status == 202:
            if response.body.get("requestId") != run_id or response.body.get("collectionProfileId") != owner.profile_id:
                raise ControlError("{} collection POST returned a mismatched resource".format(owner.service))
            return
        if response.status == 409:
            existing = self.http.request(
                "GET", _request_path(owner.endpoint, COLLECTION_PATH, run_id)
            )
            _validate_collection(owner, run_id, existing)
            return
        raise ControlError("{} collection POST failed: {}".format(owner.service, _problem(response)))

    def collection_stop(self, run_id: str) -> tuple[dict, ...]:
        self.require_ready()
        for owner in self.contract.owners:
            response = self.http.request(
                "DELETE", _request_path(owner.endpoint, COLLECTION_PATH, run_id)
            )
            if response.status != 202:
                raise ControlError("{} collection DELETE failed: {}".format(owner.service, _problem(response)))
        return self._wait_collections(run_id, _retained, "RETAINED with exact cleanup")

    def _wait_collections(
        self,
        run_id: str,
        accepted: Callable[[dict], bool],
        expected: str,
    ) -> tuple[dict, ...]:
        deadline = self.monotonic() + self.contract.closure_budget_seconds
        last = ()
        while self.monotonic() <= deadline:
            last = self.collection_status(run_id)
            failures = [
                "owner-{}={}".format(owner.position, value.get("state"))
                for owner, value in zip(self.contract.owners, last)
                if value.get("state") in {"FAILED", "TERMINATED"}
            ]
            if failures:
                raise ControlError("collection entered terminal failure: " + ", ".join(failures))
            if all(accepted(value) for value in last):
                return last
            self.sleep(self.poll_seconds)
        states = ", ".join(
            "owner-{}={}".format(owner.position, value.get("state", "unknown"))
            for owner, value in zip(self.contract.owners, last)
        )
        raise ControlError("collection did not reach {} within closure budget: {}".format(expected, states))

    def _rollback_collections(self, owners: list[Owner], run_id: str) -> list[str]:
        failures = []
        pending: dict[Owner, str] = {}
        for owner in owners:
            try:
                response = self.http.request(
                    "DELETE", _request_path(owner.endpoint, COLLECTION_PATH, run_id)
                )
                if response.status == 202:
                    pending[owner] = "DELETE accepted"
                elif response.status != 404:
                    failures.append("{} delete {}".format(owner.service, response.status))
            except ControlError as error:
                failures.append("{} {}".format(owner.service, error))
        deadline = self.monotonic() + self.contract.closure_budget_seconds
        while pending and self.monotonic() <= deadline:
            for owner in tuple(pending):
                try:
                    response = self.http.request(
                        "GET", _request_path(owner.endpoint, COLLECTION_PATH, run_id)
                    )
                    if response.status == 404:
                        pending.pop(owner)
                        continue
                    value = _validate_collection(owner, run_id, response)
                    pending[owner] = "state={} peers={} pending={} cleanup={}".format(
                        value.get("state", "unknown"),
                        value.get("activePeerResourceCount", "unknown"),
                        value.get("pendingCleanupPeerResourceCount", "unknown"),
                        value.get("cleanupPending", "unknown"),
                    )
                    if _rollback_complete(value):
                        pending.pop(owner)
                except ControlError as error:
                    pending[owner] = str(error)
            if pending:
                self.sleep(self.poll_seconds)
        failures.extend(
            "{} {}".format(owner.service, detail)
            for owner, detail in pending.items()
        )
        return failures

    def retained_collections(self, run_id: str) -> tuple[dict, ...]:
        values = self.collection_status(run_id)
        for owner, value in zip(self.contract.owners, values):
            if not _retained(value):
                raise ControlError("{} collection is not retained and cleanup-complete".format(owner.service))
            if value.get("recordCount", 0) < self.contract.minimum_samples:
                raise ControlError("{} collection has insufficient records".format(owner.service))
            if value.get("observationCount", 0) < self.contract.minimum_samples:
                raise ControlError("{} collection has insufficient observations".format(owner.service))
            stopped = _parse_time(value.get("stopTime"), "stopTime")
            age = (self.now() - stopped).total_seconds()
            if age < 0 or age >= self.contract.preparation_window_seconds:
                raise ControlError("{} retained descriptor is outside the preparation window".format(owner.service))
        return values

    def training_start(self, run_id: str, family: str | None) -> dict:
        self.require_ready(coordinator=True)
        self.retained_collections(run_id)
        family_id = self.model_family(family)
        url = _request_path(self.contract.coordinator_endpoint, TRAINING_PATH)
        payload = {"requestId": run_id, "modelFamilyId": family_id}
        try:
            response = self.http.request("POST", url, payload)
        except TransportError:
            existing = self.http.request(
                "GET", _request_path(self.contract.coordinator_endpoint, TRAINING_PATH, run_id)
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
                "GET", _request_path(self.contract.coordinator_endpoint, TRAINING_PATH, run_id)
            )
            if existing.status == 200:
                return self._validate_training(run_id, family_id, existing)
        raise ControlError("training POST failed: {}".format(_problem(response)))

    def training_status(self, run_id: str, family: str | None = None) -> dict:
        family_id = self.model_family(family) if family else None
        response = self.http.request(
            "GET", _request_path(self.contract.coordinator_endpoint, TRAINING_PATH, run_id)
        )
        if response.status != 200:
            raise ControlError("training GET failed: {}".format(_problem(response)))
        return self._validate_training(run_id, family_id, response)

    def model_family(self, supplied: str | None) -> str:
        if supplied:
            if supplied not in self.contract.model_families:
                raise ControlError("MODEL_FAMILY_ID does not match selected coordinator config")
            return supplied
        if len(self.contract.model_families) != 1:
            raise ControlError("MODEL_FAMILY_ID is required when selected config has zero or multiple families")
        return self.contract.model_families[0]

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
                "training resource is not static {} private-API flow".format(
                    self.contract.training_mode
                )
            )
        return body


def _retained(value: dict) -> bool:
    return (
        value.get("state") == "RETAINED"
        and value.get("descriptorState") == "RETAINED"
        and value.get("activePeerResourceCount") == 0
        and value.get("pendingCleanupPeerResourceCount", 0) == 0
        and value.get("cleanupPending") is False
    )


def _rollback_complete(value: dict) -> bool:
    return (
        value.get("state") in {"RETAINED", "TERMINATED", "FAILED"}
        and value.get("activePeerResourceCount") == 0
        and value.get("pendingCleanupPeerResourceCount", 0) == 0
        and value.get("cleanupPending") is False
    )


def _print_collection(contract: Contract, values: tuple[dict, ...]) -> None:
    for owner, value in zip(contract.owners, values):
        print(
            "FL COLLECTION owner={} service={} profile={} state={} ues={} peers={} records={} observations={} descriptor={} cleanup_pending={}".format(
                owner.position,
                owner.service,
                owner.profile_id,
                value.get("state", "unknown"),
                value.get("resolvedUeCount", "unknown"),
                value.get("activePeerResourceCount", "unknown"),
                value.get("recordCount", "unknown"),
                value.get("observationCount", "unknown"),
                value.get("descriptorState", "unknown"),
                str(value.get("cleanupPending", "unknown")).lower(),
            )
        )


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
    parser.add_argument(
        "action",
        choices=(
            "collection-start",
            "collection-status",
            "collection-stop",
            "training-start",
            "training-status",
        ),
    )
    parser.add_argument("--testbed", required=True)
    parser.add_argument("--config-dir", default="")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model-family-id", default="")
    args = parser.parse_args()
    try:
        run_id = canonical_run_id(args.run_id)
        contract = load_contract(args.testbed, args.config_dir)
        verify_runtime_identity(contract)
        controller = Controller(contract, HttpClient(timeout=min(30, contract.closure_budget_seconds)))
        if args.action == "collection-start":
            _print_collection(contract, controller.collection_start(run_id))
        elif args.action == "collection-status":
            controller.require_ready()
            _print_collection(contract, controller.collection_status(run_id))
        elif args.action == "collection-stop":
            _print_collection(contract, controller.collection_stop(run_id))
        elif args.action == "training-start":
            _print_training(controller.training_start(run_id, args.model_family_id or None))
        else:
            controller.require_ready(coordinator=True)
            _print_training(controller.training_status(run_id, args.model_family_id or None))
    except (ControlError, ValueError) as error:
        print("ERROR {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
