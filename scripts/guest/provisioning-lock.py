#!/usr/bin/env python3
"""Validate and resolve the Guest provisioning dependency lock."""

import argparse
import datetime
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

import yaml


HEX64 = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION = re.compile(r"^[0-9a-f]{40}$")
FINGERPRINT = re.compile(r"^[0-9A-F]{40}$")
GO_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)+(?:[-+~][A-Za-z0-9.+:~-]+)?$")
SERVER_PACKAGES = {
    "mongodb-org",
    "mongodb-org-database",
    "mongodb-org-server",
    "mongodb-org-shell",
    "mongodb-org-mongos",
    "mongodb-org-tools",
    "mongodb-org-database-tools-extra",
}
REQUIRED_PACKAGES = SERVER_PACKAGES | {
    "mongodb-mongosh",
    "mongodb-database-tools",
}


def load_lock(path):
    with Path(path).open(encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise ValueError("provisioning lock must be a mapping")
    validate_lock(value)
    return value


def require_string(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError("{} must be a non-empty string".format(label))
    return value


def validate_lock(lock):
    if lock.get("schemaVersion") != 1:
        raise ValueError("unsupported provisioning lock schema")
    expected_platform = {
        "os": "ubuntu",
        "release": "22.04",
        "architecture": "amd64",
    }
    if lock.get("platform") != expected_platform:
        raise ValueError("platform must be exactly {!r}".format(expected_platform))

    go = lock.get("go", {})
    version = require_string(go.get("version"), "go.version")
    if not GO_VERSION.fullmatch(version):
        raise ValueError("go.version must be a stable semantic version")
    archive = require_string(go.get("archive"), "go.archive")
    url = require_string(go.get("url"), "go.url")
    expected_archive = "go{}.linux-amd64.tar.gz".format(version)
    if archive != expected_archive:
        raise ValueError("go.archive must be {}".format(expected_archive))
    if url != "https://go.dev/dl/" + archive:
        raise ValueError("go.url must use the canonical go.dev archive URL")
    if not isinstance(go.get("sha256"), str) or not HEX64.fullmatch(go["sha256"]):
        raise ValueError("go.sha256 must contain 64 lowercase hex digits")

    mongodb = lock.get("mongodb", {})
    if mongodb.get("driftPolicy") != "warn-compatible":
        raise ValueError("mongodb.driftPolicy must be warn-compatible")
    repository = mongodb.get("repository", {})
    expected_repository = {
        "url": "https://repo.mongodb.org/apt/ubuntu",
        "distribution": "jammy",
        "series": "8.0",
        "signingKeyUrl": "https://www.mongodb.org/static/pgp/server-8.0.asc",
    }
    for field, expected in expected_repository.items():
        if repository.get(field) != expected:
            raise ValueError(
                "mongodb.repository.{} must be {}".format(field, expected)
            )
    fingerprint = repository.get("signingKeyFingerprint")
    if not isinstance(fingerprint, str) or not FINGERPRINT.fullmatch(fingerprint):
        raise ValueError(
            "mongodb.repository.signingKeyFingerprint must contain 40 uppercase hex digits"
        )

    families = mongodb.get("families", {})
    if set(families) != {"server", "shell", "tools"}:
        raise ValueError("mongodb families must be server, shell, and tools")
    for name, family in families.items():
        preferred = family.get("preferredVersion")
        prefix = family.get("compatiblePrefix")
        if not isinstance(preferred, str) or not VERSION.fullmatch(preferred):
            raise ValueError("mongodb family {} preferredVersion is invalid".format(name))
        if not isinstance(prefix, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*\.", prefix):
            raise ValueError("mongodb family {} compatiblePrefix is invalid".format(name))
        if not preferred.startswith(prefix):
            raise ValueError(
                "mongodb family {} preferredVersion is outside its compatiblePrefix".format(
                    name
                )
            )

    packages = mongodb.get("packages", {})
    if set(packages) != REQUIRED_PACKAGES:
        missing = sorted(REQUIRED_PACKAGES - set(packages))
        extra = sorted(set(packages) - REQUIRED_PACKAGES)
        raise ValueError(
            "mongodb packages mismatch missing={} extra={}".format(missing, extra)
        )
    if {name for name, family in packages.items() if family == "server"} != SERVER_PACKAGES:
        raise ValueError("MongoDB server package cohort is incomplete")
    if packages.get("mongodb-mongosh") != "shell":
        raise ValueError("mongodb-mongosh must use the shell family")
    if packages.get("mongodb-database-tools") != "tools":
        raise ValueError("mongodb-database-tools must use the tools family")
    unknown = sorted(set(packages.values()) - set(families))
    if unknown:
        raise ValueError("MongoDB packages use unknown families: {}".format(unknown))


def nested_get(value, dotted):
    current = value
    for key in dotted.split("."):
        current = current[key]
    if isinstance(current, (dict, list)):
        print(json.dumps(current, sort_keys=True))
    else:
        print(current)


def installed_versions(packages):
    installed = {}
    for package in packages:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}\t${Version}", package],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            status, version = result.stdout.split("\t", 1)
            if status == "install ok installed":
                installed[package] = version.strip()
    return installed


def available_versions(package):
    result = subprocess.run(
        ["apt-cache", "madison", package],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("apt-cache failed for {}: {}".format(package, result.stderr.strip()))
    return [
        fields[1].strip()
        for line in result.stdout.splitlines()
        if len(fields := line.split("|")) >= 3
    ]


def version_sort(versions):
    ordered = []
    for version in versions:
        inserted = False
        for index, existing in enumerate(ordered):
            newer = subprocess.run(
                ["dpkg", "--compare-versions", version, "gt", existing],
                check=False,
            ).returncode == 0
            if newer:
                ordered.insert(index, version)
                inserted = True
                break
        if not inserted:
            ordered.append(version)
    return ordered


def resolve_mongodb(lock):
    mongodb = lock["mongodb"]
    packages = mongodb["packages"]
    families = mongodb["families"]
    installed = installed_versions(packages)
    if installed and set(installed) != set(packages):
        missing = sorted(set(packages) - set(installed))
        raise ValueError(
            "partial MongoDB installation is unsafe; missing packages: {}".format(
                ", ".join(missing)
            )
        )

    resolved = {}
    source = "installed" if installed else "repository"
    drift = False
    reasons = []
    if installed:
        resolved = installed
    else:
        available = {name: available_versions(name) for name in packages}
        for family_name, family in families.items():
            members = [name for name, selected in packages.items() if selected == family_name]
            compatible_sets = [
                {
                    version
                    for version in available[name]
                    if version.startswith(family["compatiblePrefix"])
                }
                for name in members
            ]
            common = set.intersection(*compatible_sets) if compatible_sets else set()
            preferred = family["preferredVersion"]
            if preferred in common:
                selected = preferred
            elif common:
                selected = version_sort(common)[0]
                drift = True
                reasons.append(
                    "{} preferred {} unavailable; resolved {}".format(
                        family_name, preferred, selected
                    )
                )
            else:
                raise ValueError(
                    "no common {} MongoDB package version matches {}".format(
                        family_name, family["compatiblePrefix"]
                    )
                )
            for name in members:
                resolved[name] = selected

    for package, version in resolved.items():
        family_name = packages[package]
        family = families[family_name]
        if not version.startswith(family["compatiblePrefix"]):
            raise ValueError(
                "{} version {} is outside compatible prefix {}".format(
                    package, version, family["compatiblePrefix"]
                )
            )
        if version != family["preferredVersion"]:
            drift = True
            reasons.append(
                "{} installed {} instead of preferred {}".format(
                    package, version, family["preferredVersion"]
                )
            )
    server_versions = {resolved[name] for name in SERVER_PACKAGES}
    if len(server_versions) != 1:
        raise ValueError(
            "MongoDB server cohort has mixed versions: {}".format(
                ", ".join(sorted(server_versions))
            )
        )
    return {
        "source": source,
        "drift": drift,
        "reasons": sorted(set(reasons)),
        "packages": {name: resolved[name] for name in sorted(resolved)},
    }


def actual_go_version():
    result = subprocess.run(
        ["/usr/local/go/bin/go", "version"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("installed Go binary is unavailable")
    fields = result.stdout.strip().split()
    if len(fields) < 4 or not fields[2].startswith("go"):
        raise ValueError("cannot parse installed Go version")
    return fields[2][2:], fields[3]


def load_component_revisions(lock_path, records):
    with Path(lock_path).open(encoding="utf-8") as stream:
        lock = yaml.safe_load(stream)
    if not isinstance(lock, dict) or lock.get("schemaVersion") != 1:
        raise ValueError("unsupported component lock schema")
    expected = {}
    for item in lock.get("components", []):
        if not isinstance(item, dict):
            raise ValueError("component lock entries must be mappings")
        path = require_string(item.get("path"), "component path")
        revision = require_string(item.get("commit"), "component commit")
        if not GIT_REVISION.fullmatch(revision):
            raise ValueError("component commit must be a full lowercase Git revision")
        if path in expected:
            raise ValueError("duplicate component lock path: {}".format(path))
        expected[path] = revision

    selected = {}
    for record in records:
        path, separator, revision = record.partition("=")
        if not separator or path not in expected:
            raise ValueError("unknown component revision record: {}".format(record))
        if revision != expected[path]:
            raise ValueError(
                "component revision differs for {}: expected {}, got {}".format(
                    path, expected[path], revision
                )
            )
        if path in selected:
            raise ValueError("duplicate component revision record: {}".format(path))
        selected[path] = revision
    if not selected:
        raise ValueError("component revision inventory must not be empty")
    return [
        {"path": path, "revision": selected[path]}
        for path in sorted(selected)
    ]


def write_manifest(
    lock_path, machine, include_mongodb, components_lock, component_records, output
):
    lock = load_lock(lock_path)
    version, target = actual_go_version()
    expected_target = "linux/amd64"
    if version != lock["go"]["version"] or target != expected_target:
        raise ValueError(
            "installed Go identity differs: expected go{} {}, got go{} {}".format(
                lock["go"]["version"], expected_target, version, target
            )
        )
    os_release = {}
    with Path("/etc/os-release").open(encoding="utf-8") as stream:
        for line in stream:
            if "=" in line:
                key, value = line.rstrip().split("=", 1)
                os_release[key] = value.strip('"')
    manifest = {
        "schemaVersion": 1,
        "machine": machine,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "platform": {
            "os": os_release.get("ID"),
            "release": os_release.get("VERSION_ID"),
            "architecture": subprocess.check_output(
                ["dpkg", "--print-architecture"], text=True
            ).strip(),
            "kernel": platform.release(),
        },
        "go": {
            "requestedVersion": lock["go"]["version"],
            "resolvedVersion": version,
            "archive": lock["go"]["archive"],
            "drift": False,
        },
        "components": load_component_revisions(components_lock, component_records),
    }
    if include_mongodb:
        resolved = resolve_mongodb(lock)
        manifest["mongodb"] = {
            "driftPolicy": lock["mongodb"]["driftPolicy"],
            "drift": resolved["drift"],
            "reasons": resolved["reasons"],
            "packages": resolved["packages"],
        }
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("lock")
    get = subparsers.add_parser("get")
    get.add_argument("lock")
    get.add_argument("field")
    resolve = subparsers.add_parser("resolve-mongodb")
    resolve.add_argument("lock")
    manifest = subparsers.add_parser("write-manifest")
    manifest.add_argument("lock")
    manifest.add_argument("--machine", required=True)
    manifest.add_argument("--include-mongodb", action="store_true")
    manifest.add_argument("--components-lock", required=True)
    manifest.add_argument("--component", action="append", required=True)
    manifest.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        lock = load_lock(args.lock)
        if args.command == "validate":
            print("OK provisioning lock")
        elif args.command == "get":
            nested_get(lock, args.field)
        elif args.command == "resolve-mongodb":
            print(json.dumps(resolve_mongodb(lock), sort_keys=True))
        elif args.command == "write-manifest":
            write_manifest(
                args.lock,
                args.machine,
                args.include_mongodb,
                args.components_lock,
                args.component,
                args.output,
            )
    except (KeyError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
