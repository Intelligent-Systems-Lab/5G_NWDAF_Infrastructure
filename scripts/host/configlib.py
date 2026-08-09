#!/usr/bin/env python3
"""Shared, host-only configuration helpers."""

import hashlib
import ipaddress
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def load_yaml(path):
    with Path(path).open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    return {} if value is None else value


def dump_yaml(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(value, stream, sort_keys=False, default_flow_style=False)


def resolve_path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def resolve_config_dir(testbed, explicit=None):
    if explicit:
        return resolve_path(explicit)
    local_path = ROOT / "testbed.local.yaml"
    if local_path.exists():
        configured = load_yaml(local_path).get("config", {}).get("directory")
        if configured:
            return resolve_path(configured)
    return resolve_path(testbed.get("config", {}).get("directory", "config/default"))


def load_scenario_definition(value):
    path = resolve_path(value).resolve()
    if path != ROOT and ROOT not in path.parents:
        raise ValueError("scenario definition must remain inside the repository")
    scenario = load_yaml(path)
    return path, scenario


def resolve_config_scenario(config_dir):
    manifest = load_yaml(Path(config_dir) / "manifest.yaml")
    metadata = manifest.get("scenario", {})
    definition = metadata.get("definition")
    if not isinstance(definition, str) or not definition:
        raise ValueError("config manifest scenario.definition is required")
    path, scenario = load_scenario_definition(definition)
    if metadata.get("name") != scenario.get("name"):
        raise ValueError("config manifest scenario name does not match its definition")
    expected_hash = metadata.get("definitionHash")
    actual_hash = canonical_sha256(scenario)
    if expected_hash != actual_hash:
        raise ValueError("config manifest scenario definition hash is stale")
    return path, scenario


def load_local_settings():
    local_path = ROOT / "testbed.local.yaml"
    return load_yaml(local_path) if local_path.exists() else {}


def resolve_ml_bind_address(testbed):
    local = load_local_settings()
    return local.get("host", {}).get(
        "mlBindAddress", testbed.get("mlRuntime", {}).get("bindAddress")
    )


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(directory):
    digest = hashlib.sha256()
    for path in sorted(Path(directory).rglob("*.yaml")):
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def canonical_sha256(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def guest_network_configs(testbed):
    """Build the role-specific guest alias files from one topology definition."""
    machines = testbed["machines"]
    networks = testbed["networks"]
    aliases = {name: [] for name in machines}

    def add(machine, owner, endpoint_name, endpoint):
        network_name = endpoint["network"]
        address = endpoint["address"]
        anchor = machines[machine]["interfaces"][network_name]
        if address == anchor:
            return
        aliases[machine].append({
            "owner": owner,
            "endpoint": endpoint_name,
            "network": network_name,
            "address": address,
            "prefixLength": ipaddress.ip_network(
                networks[network_name]["cidr"]
            ).prefixlen,
            "anchor": anchor,
        })

    for owner, service in testbed["coreServices"].items():
        for endpoint_name in ("sbi", "n2", "n4", "endpoint"):
            endpoint = service.get(endpoint_name)
            if isinstance(endpoint, dict) and {"network", "address"} <= set(endpoint):
                add("core", owner, endpoint_name, endpoint)

    for path_name, path in testbed["paths"].items():
        machine = path["machine"]
        for endpoint_name in ("n2", "n3"):
            add(machine, "gnb-" + path_name, endpoint_name, path["gnb"][endpoint_name])
        for endpoint_name in ("n3", "n4", "n6", "eventExposure"):
            add(machine, "upf-" + path_name, endpoint_name, path["upf"][endpoint_name])

    for owner, service in testbed["analytics"].items():
        if owner.startswith("nwdaf-"):
            add(service["machine"], owner, "sbi", service["sbi"])

    callback = testbed["consumer"]["callback"]
    add(
        testbed["consumer"]["machine"],
        "nwdaf-consumer",
        "callback",
        {"network": callback["network"], "address": callback["bindAddress"]},
    )

    return {
        machine: {
            "schemaVersion": 1,
            "machine": machine,
            "aliases": machine_aliases,
        }
        for machine, machine_aliases in aliases.items()
    }


def get_path(value, keys):
    current = value
    for key in keys:
        current = current[key]
    return current


def set_path(value, keys, replacement):
    current = value
    for key in keys[:-1]:
        current = current[key]
    current[keys[-1]] = replacement
