#!/usr/bin/env python3
"""Synthetic contract tests for static Flat operator lifecycle control."""

import datetime as dt
import importlib.util
import subprocess
import sys
import tempfile
from collections import defaultdict, deque
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/host"))
SPEC = importlib.util.spec_from_file_location(
    "fl_control", ROOT / "scripts/host/fl-control.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

RUN_ID = "11111111-1111-4111-8111-111111111111"
NOW = dt.datetime(2026, 8, 27, 12, 0, tzinfo=dt.timezone.utc)


class FakeHttp:
    def __init__(self):
        self.routes = defaultdict(deque)
        self.calls = []

    def add(self, method, url, value):
        self.routes[(method, url)].append(value)

    def request(self, method, url, payload=None):
        self.calls.append((method, url, payload))
        values = self.routes[(method, url)]
        if not values:
            raise AssertionError("unexpected HTTP call: {} {}".format(method, url))
        value = values.popleft()
        if isinstance(value, Exception):
            raise value
        return value


def response(status_code, **body):
    return MODULE.Response(status=status_code, body=body, headers={})


def contract():
    owners = tuple(
        MODULE.Owner(
            position=position,
            service="pymtlf-client-{}".format(position),
            endpoint="http://client-{}".format(position),
            profile_id="data-owner-{}".format(position),
            internal_group_id="group-{}".format(position),
            supis=("imsi-{}a".format(position), "imsi-{}b".format(position)),
        )
        for position in range(1, 5)
    )
    return MODULE.Contract(
        config_dir=ROOT / "config/local/test",
        config_set="test",
        config_hash="a" * 64,
        services=("pymtlf-server",) + tuple(owner.service for owner in owners),
        coordinator_service="pymtlf-server",
        coordinator_endpoint="http://server",
        owners=owners,
        model_families=("ue-communication-default",),
        minimum_samples=1,
        closure_budget_seconds=10,
        preparation_window_seconds=3600,
    )


def collection(owner, state="COLLECTING", **overrides):
    value = {
        "requestId": RUN_ID,
        "collectionProfileId": owner.profile_id,
        "state": state,
        "resolvedUeCount": 2,
        "activePeerResourceCount": 1,
        "pendingCleanupPeerResourceCount": 0,
        "recordCount": 1,
        "observationCount": 1,
        "descriptorState": "ACTIVE",
        "cleanupPending": False,
        "stopTime": "2026-08-27T11:59:00Z",
    }
    value.update(overrides)
    return value


def add_ready(http, selected, coordinator=False):
    for owner in selected.owners:
        http.add("GET", owner.endpoint + "/health/ready", response(200, status="ready"))
    if coordinator:
        http.add("GET", selected.coordinator_endpoint + "/health/ready", response(200, status="ready"))


def add_retained_preflight(http, selected, *, stop_time="2026-08-27T11:59:00Z"):
    for owner in selected.owners:
        retained = collection(
            owner,
            state="RETAINED",
            activePeerResourceCount=0,
            descriptorState="RETAINED",
            stopTime=stop_time,
        )
        http.add(
            "GET",
            owner.endpoint + MODULE.COLLECTION_PATH + "/" + RUN_ID,
            response(200, **retained),
        )


def test_collection_start_and_exact_partial_rollback():
    selected = contract()
    http = FakeHttp()
    add_ready(http, selected)
    for owner in selected.owners:
        http.add(
            "POST",
            owner.endpoint + MODULE.COLLECTION_PATH,
            response(202, **collection(owner, state="PENDING")),
        )
        http.add(
            "GET",
            owner.endpoint + MODULE.COLLECTION_PATH + "/" + RUN_ID,
            response(200, **collection(owner)),
        )
    controller = MODULE.Controller(selected, http, sleep=lambda _value: None)
    values = controller.collection_start(RUN_ID)
    assert [value["state"] for value in values] == ["COLLECTING"] * 4

    http = FakeHttp()
    add_ready(http, selected)
    first, second = selected.owners[:2]
    http.add("POST", first.endpoint + MODULE.COLLECTION_PATH, response(202, **collection(first)))
    http.add(
        "POST",
        second.endpoint + MODULE.COLLECTION_PATH,
        response(503, cause="SERVICE_NOT_AVAILABLE", detail="not ready"),
    )
    http.add("DELETE", first.endpoint + MODULE.COLLECTION_PATH + "/" + RUN_ID, response(202))
    http.add("DELETE", second.endpoint + MODULE.COLLECTION_PATH + "/" + RUN_ID, response(404))
    http.add(
        "GET",
        first.endpoint + MODULE.COLLECTION_PATH + "/" + RUN_ID,
        response(
            200,
            **collection(
                first,
                state="TERMINATED",
                activePeerResourceCount=0,
                descriptorState="NONE",
            ),
        ),
    )
    try:
        MODULE.Controller(selected, http).collection_start(RUN_ID)
    except MODULE.ControlError as error:
        assert "pymtlf-client-2 collection POST failed" in str(error)
    else:
        raise AssertionError("partial collection create was accepted")
    deletes = [call for call in http.calls if call[0] == "DELETE"]
    assert [call[1] for call in deletes] == [
        first.endpoint + MODULE.COLLECTION_PATH + "/" + RUN_ID,
        second.endpoint + MODULE.COLLECTION_PATH + "/" + RUN_ID,
    ]


def test_partial_rollback_reports_cleanup_that_does_not_converge():
    selected = contract()
    http = FakeHttp()
    owner = selected.owners[0]
    http.add("DELETE", owner.endpoint + MODULE.COLLECTION_PATH + "/" + RUN_ID, response(202))
    http.add(
        "GET",
        owner.endpoint + MODULE.COLLECTION_PATH + "/" + RUN_ID,
        response(
            200,
            **collection(
                owner,
                state="STOPPING",
                cleanupPending=True,
                pendingCleanupPeerResourceCount=1,
            ),
        ),
    )
    ticks = iter((0, 0, 11))
    failures = MODULE.Controller(
        selected,
        http,
        sleep=lambda _value: None,
        monotonic=lambda: next(ticks),
    )._rollback_collections([owner], RUN_ID)
    assert len(failures) == 1
    assert "pymtlf-client-1 state=STOPPING" in failures[0]
    assert "pending=1" in failures[0]


def test_ambiguous_create_reuses_same_identity_and_stop_requires_retention():
    selected = contract()
    http = FakeHttp()
    add_ready(http, selected)
    for index, owner in enumerate(selected.owners):
        if index == 0:
            http.add(
                "POST",
                owner.endpoint + MODULE.COLLECTION_PATH,
                MODULE.TransportError("timeout"),
            )
            http.add(
                "GET",
                owner.endpoint + MODULE.COLLECTION_PATH + "/" + RUN_ID,
                response(200, **collection(owner)),
            )
        else:
            http.add(
                "POST",
                owner.endpoint + MODULE.COLLECTION_PATH,
                response(202, **collection(owner)),
            )
        http.add(
            "GET",
            owner.endpoint + MODULE.COLLECTION_PATH + "/" + RUN_ID,
            response(200, **collection(owner)),
        )
    MODULE.Controller(selected, http, sleep=lambda _value: None).collection_start(RUN_ID)
    posted_ids = [call[2]["requestId"] for call in http.calls if call[0] == "POST"]
    assert posted_ids == [RUN_ID] * 4

    http = FakeHttp()
    add_ready(http, selected)
    for owner in selected.owners:
        http.add("DELETE", owner.endpoint + MODULE.COLLECTION_PATH + "/" + RUN_ID, response(202))
        retained = collection(
            owner,
            state="RETAINED",
            activePeerResourceCount=0,
            descriptorState="RETAINED",
        )
        http.add(
            "GET",
            owner.endpoint + MODULE.COLLECTION_PATH + "/" + RUN_ID,
            response(200, **retained),
        )
    values = MODULE.Controller(selected, http, sleep=lambda _value: None).collection_stop(RUN_ID)
    assert all(value["descriptorState"] == "RETAINED" for value in values)


def test_training_preflight_start_status_and_family_rejection():
    selected = contract()
    http = FakeHttp()
    add_ready(http, selected, coordinator=True)
    add_retained_preflight(http, selected)
    training = {
        "requestId": RUN_ID,
        "modelFamilyId": "ue-communication-default",
        "mode": "flat",
        "participantSource": "static",
        "triggerSource": "manual",
        "state": "PREPARING",
    }
    http.add("POST", "http://server" + MODULE.TRAINING_PATH, response(202, **training))
    value = MODULE.Controller(selected, http, now=lambda: NOW).training_start(RUN_ID, None)
    assert value == training

    http = FakeHttp()
    http.add(
        "GET",
        "http://server" + MODULE.TRAINING_PATH + "/" + RUN_ID,
        response(200, **dict(training, state="COMPLETE", completedRounds=2, candidateDigest="b" * 64)),
    )
    status = MODULE.Controller(selected, http).training_status(RUN_ID)
    assert status["state"] == "COMPLETE"
    assert status["completedRounds"] == 2
    try:
        MODULE.Controller(selected, FakeHttp()).model_family("wrong-family")
    except MODULE.ControlError as error:
        assert "does not match" in str(error)
    else:
        raise AssertionError("wrong model family was accepted")

    http = FakeHttp()
    add_retained_preflight(http, selected, stop_time="2026-08-27T10:00:00Z")
    try:
        MODULE.Controller(selected, http, now=lambda: NOW).retained_collections(RUN_ID)
    except MODULE.ControlError as error:
        assert "outside the preparation window" in str(error)
    else:
        raise AssertionError("stale retained descriptor was accepted")


def test_training_ambiguous_retry_and_http_failures():
    selected = contract()
    training = {
        "requestId": RUN_ID,
        "modelFamilyId": "ue-communication-default",
        "mode": "flat",
        "participantSource": "static",
        "triggerSource": "manual",
        "state": "PREPARING",
    }

    http = FakeHttp()
    add_ready(http, selected, coordinator=True)
    add_retained_preflight(http, selected)
    http.add(
        "POST",
        "http://server" + MODULE.TRAINING_PATH,
        MODULE.TransportError("timeout"),
    )
    http.add(
        "GET",
        "http://server" + MODULE.TRAINING_PATH + "/" + RUN_ID,
        response(200, **training),
    )
    value = MODULE.Controller(selected, http, now=lambda: NOW).training_start(RUN_ID, None)
    assert value == training
    assert [call[2]["requestId"] for call in http.calls if call[0] == "POST"] == [RUN_ID]

    http = FakeHttp()
    add_ready(http, selected, coordinator=True)
    add_retained_preflight(http, selected)
    http.add("POST", "http://server" + MODULE.TRAINING_PATH, response(409))
    http.add(
        "GET",
        "http://server" + MODULE.TRAINING_PATH + "/" + RUN_ID,
        response(200, **training),
    )
    assert MODULE.Controller(selected, http, now=lambda: NOW).training_start(
        RUN_ID, None
    ) == training

    for status_code in (404, 422, 503):
        http = FakeHttp()
        add_ready(http, selected, coordinator=True)
        add_retained_preflight(http, selected)
        http.add(
            "POST",
            "http://server" + MODULE.TRAINING_PATH,
            response(status_code, cause="EXPECTED", detail="rejected"),
        )
        try:
            MODULE.Controller(selected, http, now=lambda: NOW).training_start(
                RUN_ID, None
            )
        except MODULE.ControlError as error:
            assert "status={}".format(status_code) in str(error)
        else:
            raise AssertionError("training HTTP {} was accepted".format(status_code))


def test_runtime_identity_and_generated_contract_tampering_fail_closed():
    original_run = MODULE.subprocess.run
    identity_commands = []
    try:
        def fake_run(command, **_kwargs):
            identity_commands.append(command)
            return subprocess.CompletedProcess(
                command, 0 if len(identity_commands) == 1 else 1,
                stdout="", stderr="selected mismatch"
            )

        MODULE.subprocess.run = fake_run
        MODULE.verify_runtime_identity(contract())
        assert "--identity-only" in identity_commands[0]
        assert "--require-running-selected" in identity_commands[0]
        try:
            MODULE.verify_runtime_identity(contract())
        except MODULE.ControlError as error:
            assert "selected mismatch" in str(error)
        else:
            raise AssertionError("wrong active config identity was accepted")
    finally:
        MODULE.subprocess.run = original_run

    with tempfile.TemporaryDirectory(prefix="fl-control-tamper-") as temporary:
        output = Path(temporary)
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/host/config-render.py"),
                "--testbed",
                "testbed.static-flat.yaml",
                "--name",
                "flat",
                "--scenario",
                "experiments/examples/fl-closure-smoke/scenario.yaml",
                "--output-root",
                str(output),
                "--ml-device",
                "cpu",
                "--webconsole",
                "false",
            ],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        config_dir = output / "flat"
        manifest_path = config_dir / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        original_manifest = manifest_path.read_text(encoding="utf-8")
        manifest["runtime"]["hostContainers"] = []
        manifest_path.write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )
        try:
            MODULE.load_contract("testbed.static-flat.yaml", str(config_dir))
        except (MODULE.ControlError, ValueError) as error:
            assert "hostContainers" in str(error) or "Host inventory" in str(error)
        else:
            raise AssertionError("empty Host inventory was accepted")

        manifest_path.write_text(original_manifest, encoding="utf-8")
        client_path = config_dir / "pymtlf-client-1.yaml"
        client = yaml.safe_load(client_path.read_text(encoding="utf-8"))
        client["federated_learning"]["client"]["training_data"][
            "collection_profiles"
        ][0]["profile_id"] = "stale-profile"
        client_path.write_text(yaml.safe_dump(client, sort_keys=False), encoding="utf-8")
        try:
            MODULE.load_contract("testbed.static-flat.yaml", str(config_dir))
        except MODULE.ControlError as error:
            assert "profile identity is stale" in str(error)
        else:
            raise AssertionError("unknown collection profile was accepted")


def test_contract_is_manifest_driven_and_rejects_other_topologies():
    with tempfile.TemporaryDirectory(prefix="fl-control-") as temporary:
        output = Path(temporary)
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/host/config-render.py"),
                "--testbed",
                "testbed.static-flat.yaml",
                "--name",
                "flat",
                "--scenario",
                "experiments/examples/fl-closure-smoke/scenario.yaml",
                "--output-root",
                str(output),
                "--ml-device",
                "cpu",
                "--webconsole",
                "false",
            ],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        selected = MODULE.load_contract("testbed.static-flat.yaml", str(output / "flat"))
        assert [owner.profile_id for owner in selected.owners] == [
            "data-owner-1",
            "data-owner-2",
            "data-owner-3",
            "data-owner-4",
        ]
        assert len({supi for owner in selected.owners for supi in owner.supis}) == 8
        try:
            MODULE.load_contract(
                "testbed.static-hierarchical.yaml", "config/local/phase2-static-hfl-v4"
            )
        except (MODULE.ControlError, ValueError) as error:
            assert "static-flat" in str(error)
        else:
            raise AssertionError("static HFL config was accepted by Phase 3 control")


def main():
    assert MODULE.canonical_run_id(RUN_ID) == RUN_ID
    for invalid in (
        "",
        "11111111-1111-1111-8111-111111111111",
        "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
    ):
        try:
            MODULE.canonical_run_id(invalid)
        except MODULE.ControlError:
            pass
        else:
            raise AssertionError("invalid RUN_ID was accepted: " + invalid)
    test_collection_start_and_exact_partial_rollback()
    test_partial_rollback_reports_cleanup_that_does_not_converge()
    test_ambiguous_create_reuses_same_identity_and_stop_requires_retention()
    test_training_preflight_start_status_and_family_rejection()
    test_training_ambiguous_retry_and_http_failures()
    test_runtime_identity_and_generated_contract_tampering_fail_closed()
    test_contract_is_manifest_driven_and_rejects_other_topologies()
    print("FL_CONTROL_TEST status=passed owners=4")


if __name__ == "__main__":
    main()
