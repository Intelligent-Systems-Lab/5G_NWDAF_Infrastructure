#!/usr/bin/env python3
"""Focused status rendering tests for protocol PyMTLF containers."""

import contextlib
import importlib.util
import io
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ml_status", ROOT / "scripts" / "host" / "ml-status.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def main():
    with tempfile.TemporaryDirectory(prefix="5g-ml-status-") as temporary:
        config = Path(temporary) / "pymtlf-leaf.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "federated_learning": {
                        "client": {"training": {"device": "cuda:0"}}
                    }
                }
            ),
            encoding="utf-8",
        )
        container = {
            "Mounts": [
                {
                    "Destination": MODULE.CONFIG_TARGET,
                    "Type": "bind",
                    "Source": str(config),
                }
            ],
            "Config": {"Labels": {"com.docker.compose.service": "pymtlf-leaf-a1"}},
        }
        assert MODULE.configured_device(container) == "cuda:0"

    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        MODULE.print_fl_summary(
            {},
            "pymtlf-root",
            deployment_kind="protocol-hierarchical",
        )
    output = stream.getvalue()
    assert "container=absent" in output
    assert "outcome=not-started" in output

    print("ML_STATUS_TEST protocol_status status=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
