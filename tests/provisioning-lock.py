#!/usr/bin/env python3
"""Exercise provisioning lock validation and MongoDB drift resolution."""

import copy
import importlib.util
import sys
from pathlib import Path
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "guest" / "provisioning-lock.py"
SPEC = importlib.util.spec_from_file_location("provisioning_lock", TOOL)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def expect_error(label, function, evidence):
    try:
        function()
    except ValueError as exc:
        if evidence not in str(exc):
            raise AssertionError(
                "{} expected {!r}, got {!r}".format(label, evidence, str(exc))
            )
        print("OK rejected={} evidence={!r}".format(label, evidence))
        return
    raise AssertionError("{} did not fail".format(label))


def versions_for(lock, replacements=None):
    replacements = replacements or {}
    values = {}
    for package, family_name in lock["mongodb"]["packages"].items():
        family = lock["mongodb"]["families"][family_name]
        values[package] = replacements.get(
            package,
            [family["preferredVersion"]],
        )
    return values


def resolve(lock, installed=None, available=None):
    with mock.patch.object(
        MODULE, "installed_versions", return_value=installed or {}
    ), mock.patch.object(
        MODULE,
        "available_versions",
        side_effect=lambda package: available[package],
    ):
        return MODULE.resolve_mongodb(lock)


def main():
    lock = yaml.safe_load((ROOT / "provisioning.lock.yaml").read_text(encoding="utf-8"))
    MODULE.validate_lock(lock)
    print("OK provisioning lock schema")

    component_lock_path = ROOT / "components.lock.yaml"
    component_lock = yaml.safe_load(component_lock_path.read_text(encoding="utf-8"))
    selected = ["NFs/nwdaf", "NFs/nrf", "NFs/adrf"]
    expected = {
        item["path"]: item["commit"] for item in component_lock["components"]
    }
    records = ["{}={}".format(path, expected[path]) for path in selected]
    resolved = MODULE.load_component_revisions(component_lock_path, records)
    assert resolved == [
        {"path": path, "revision": expected[path]} for path in sorted(selected)
    ]
    expect_error(
        "component-revision-mismatch",
        lambda: MODULE.load_component_revisions(
            component_lock_path, ["NFs/nwdaf=" + "0" * 40]
        ),
        "component revision differs",
    )
    expect_error(
        "duplicate-component-revision",
        lambda: MODULE.load_component_revisions(
            component_lock_path, [records[0], records[0]]
        ),
        "duplicate component revision",
    )
    print("OK selected component revision inventory")

    exact = resolve(lock, available=versions_for(lock))
    assert exact["source"] == "repository" and exact["drift"] is False
    print("OK preferred MongoDB package set")

    drift_versions = {
        "server": "8.0.29",
        "shell": "2.9.1",
        "tools": "100.16.1",
    }
    available = {
        package: [drift_versions[family]]
        for package, family in lock["mongodb"]["packages"].items()
    }
    drift = resolve(lock, available=available)
    assert drift["drift"] is True
    assert drift["packages"]["mongodb-org"] == "8.0.29"
    assert drift["packages"]["mongodb-mongosh"] == "2.9.1"
    assert drift["packages"]["mongodb-database-tools"] == "100.16.1"
    print("OK compatible repository drift warns and resolves")

    installed = {
        package: drift_versions[family]
        for package, family in lock["mongodb"]["packages"].items()
    }
    preserved = resolve(lock, installed=installed, available={})
    assert preserved["source"] == "installed" and preserved["drift"] is True
    assert preserved["packages"] == dict(sorted(installed.items()))
    print("OK compatible installed drift is preserved")

    partial = dict(installed)
    partial.pop("mongodb-mongosh")
    expect_error(
        "partial-install",
        lambda: resolve(lock, installed=partial, available={}),
        "partial MongoDB installation",
    )

    incompatible = dict(installed)
    incompatible["mongodb-org"] = "8.2.0"
    expect_error(
        "incompatible-series",
        lambda: resolve(lock, installed=incompatible, available={}),
        "outside compatible prefix",
    )

    mixed = dict(installed)
    mixed["mongodb-org"] = "8.0.30"
    expect_error(
        "mixed-server-cohort",
        lambda: resolve(lock, installed=mixed, available={}),
        "mixed versions",
    )

    missing_preferred_and_compatible = versions_for(lock)
    for package, family in lock["mongodb"]["packages"].items():
        if family == "server":
            missing_preferred_and_compatible[package] = ["8.2.0"]
    expect_error(
        "no-compatible-version",
        lambda: resolve(lock, available=missing_preferred_and_compatible),
        "no common server MongoDB package version",
    )

    invalid = copy.deepcopy(lock)
    invalid["go"]["sha256"] = "0" * 63
    expect_error(
        "go-checksum",
        lambda: MODULE.validate_lock(invalid),
        "64 lowercase hex digits",
    )
    invalid = copy.deepcopy(lock)
    invalid["go"]["version"] = "1.26.2/../../etc"
    invalid["go"]["archive"] = "go1.26.2/../../etc.linux-amd64.tar.gz"
    invalid["go"]["url"] = "https://go.dev/dl/" + invalid["go"]["archive"]
    expect_error(
        "unsafe-go-version",
        lambda: MODULE.validate_lock(invalid),
        "stable semantic version",
    )
    invalid = copy.deepcopy(lock)
    invalid["mongodb"]["packages"].pop("mongodb-org-server")
    expect_error(
        "mongodb-package-coverage",
        lambda: MODULE.validate_lock(invalid),
        "packages mismatch",
    )

    common = (ROOT / "scripts" / "guest" / "common.sh").read_text(encoding="utf-8")
    core = (ROOT / "scripts" / "guest" / "core.sh").read_text(encoding="utf-8")
    assert "go_version=1.26.2" not in common
    assert "apt-get install -y mongodb-org\n" not in core
    assert "provisioning.lock.yaml" in common and "provisioning.lock.yaml" in core
    assert "resolve-mongodb" in core
    assert "write_provisioning_manifest" in core
    assert "write_provisioning_manifest" in (ROOT / "scripts" / "guest" / "path.sh").read_text(encoding="utf-8")
    print("OK Guest scripts consume the lock without floating primary installs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
