#!/usr/bin/env python3
"""Focused protocol training-control behavior tests."""

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "host"))
SPEC = importlib.util.spec_from_file_location(
    "fl_control", ROOT / "scripts" / "host" / "fl-control.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


RUN_ID = "11111111-1111-4111-8111-111111111111"


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, payload=None):
        self.requests.append((method, url, payload))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(status_code, **body):
    return MODULE.Response(status_code, body, {})


def contract():
    return MODULE.Contract(
        config_dir=ROOT,
        config_set="selected",
        config_hash="identity",
        training_mode="hierarchical",
        services=("pymtlf-root", "pymtlf-leaf-a1"),
        retained_services=("pymtlf-root", "pymtlf-leaf-a1"),
        coordinator_service="pymtlf-root",
        coordinator_endpoint="http://127.0.0.1:5100",
        model_families=("image-classification-mnist",),
        request_timeout_seconds=30,
    )


def training_body(**changes):
    value = {
        "requestId": RUN_ID,
        "modelFamilyId": "image-classification-mnist",
        "mode": "hierarchical",
        "participantSource": "static",
        "triggerSource": "private_api",
        "state": "PREPARING",
    }
    value.update(changes)
    return value


def main():
    assert MODULE.canonical_run_id(RUN_ID) == RUN_ID
    try:
        MODULE.canonical_run_id("run-1")
    except MODULE.ControlError:
        pass
    else:
        raise AssertionError("non-UUID run identity was accepted")

    http = FakeHttp(
        [
            response(200, status="ready"),
            response(202, **training_body()),
        ]
    )
    value = MODULE.Controller(contract(), http).training_start(RUN_ID, None)
    assert value["state"] == "PREPARING"
    assert http.requests[-1][2] == {
        "requestId": RUN_ID,
        "modelFamilyId": "image-classification-mnist",
    }

    idempotent = FakeHttp(
        [
            response(200, status="ready"),
            response(409, cause="ALREADY_EXISTS"),
            response(200, **training_body(state="COMPLETE")),
        ]
    )
    assert MODULE.Controller(contract(), idempotent).training_start(
        RUN_ID, None
    )["state"] == "COMPLETE"

    ambiguous = FakeHttp(
        [
            response(200, status="ready"),
            MODULE.TransportError("timeout"),
            response(200, **training_body()),
        ]
    )
    assert MODULE.Controller(contract(), ambiguous).training_start(
        RUN_ID, None
    )["requestId"] == RUN_ID

    invalid = FakeHttp([response(200, **training_body(mode="flat"))])
    try:
        MODULE.Controller(contract(), invalid).training_status(RUN_ID)
    except MODULE.ControlError as exc:
        assert "hierarchical" in str(exc)
    else:
        raise AssertionError("non-hierarchical training resource was accepted")

    print("FL_CONTROL_TEST protocol_training_contract status=passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
