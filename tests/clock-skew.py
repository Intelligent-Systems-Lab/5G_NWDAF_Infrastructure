#!/usr/bin/env python3
"""Verify exact Guest clock inventory and skew rejection."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "clock_skew", ROOT / "scripts" / "host" / "clock-skew.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def rejected(records, machines, tolerance):
    try:
        MODULE.validate(records, machines, tolerance)
    except ValueError:
        return
    raise AssertionError("invalid clock samples were accepted")


def main() -> int:
    machines = ["core", "path-a", "path-b", "path-c"]
    assert MODULE.validate(
        ["core|1000", "path-a|1100", "path-b|900", "path-c|1050"],
        machines,
        1000,
    ) == 200
    rejected(["core|1000", "path-a|2500", "path-b|900", "path-c|1050"], machines, 1000)
    rejected(["core|1000", "path-a|1100", "path-b|900"], machines, 1000)
    rejected(["core|1000", "core|1001", "path-a|1100", "path-b|900", "path-c|1050"], machines, 1000)
    print("OK exact Guest clock inventory and skew threshold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
