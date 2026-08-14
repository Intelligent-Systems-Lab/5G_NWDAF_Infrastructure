#!/usr/bin/env python3
"""Shared, host-only configuration helpers."""

import hashlib
import ipaddress
import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCENARIO_SCHEMA = 2


def resolve_mobile_identities(testbed):
    """Resolve PLMN-derived identities from one non-redundant topology contract."""
    mobile = testbed.get("mobileNetwork", {})
    plmn = mobile.get("plmn", {})
    mcc = plmn.get("mcc")
    mnc = plmn.get("mnc")
    if not isinstance(mcc, str) or re.fullmatch(r"[0-9]{3}", mcc) is None:
        raise ValueError("mobileNetwork.plmn.mcc must contain exactly 3 digits")
    if not isinstance(mnc, str) or re.fullmatch(r"[0-9]{2,3}", mnc) is None:
        raise ValueError("mobileNetwork.plmn.mnc must contain 2 or 3 digits")

    group = mobile.get("internalGroup", {})
    service_id = group.get("serviceId")
    local_id = group.get("localId")
    if not isinstance(service_id, str) or re.fullmatch(r"[A-Fa-f0-9]{8}", service_id) is None:
        raise ValueError("mobileNetwork.internalGroup.serviceId must contain 8 hex digits")
    if (
        not isinstance(local_id, str)
        or re.fullmatch(r"(?:[A-Fa-f0-9]{2}){1,10}", local_id) is None
    ):
        raise ValueError(
            "mobileNetwork.internalGroup.localId must contain 2 to 20 hex digits in octets"
        )

    msin_width = 15 - len(mcc) - len(mnc)
    path_supis = {}
    all_numbers = []
    for path_name in ("a", "b"):
        numbers = testbed.get("paths", {}).get(path_name, {}).get(
            "subscriberNumbers"
        )
        if not isinstance(numbers, list) or len(numbers) != 3:
            raise ValueError(
                "paths.{}.subscriberNumbers must contain exactly 3 integers".format(
                    path_name
                )
            )
        resolved = []
        for number in numbers:
            if (
                not isinstance(number, int)
                or isinstance(number, bool)
                or number <= 0
                or number >= 10 ** msin_width
            ):
                raise ValueError(
                    "paths.{}.subscriberNumbers values must fit the {}-digit MSIN".format(
                        path_name, msin_width
                    )
                )
            resolved.append("imsi-{}{}{:0{}d}".format(mcc, mnc, number, msin_width))
            all_numbers.append(number)
        path_supis[path_name] = resolved
    if len(all_numbers) != len(set(all_numbers)):
        raise ValueError("subscriberNumbers must be unique across both Paths")

    return {
        "plmn": {"mcc": mcc, "mnc": mnc},
        "plmnDigits": mcc + mnc,
        "internalGroupId": "{}-{}-{}-{}".format(
            service_id, mcc, mnc, local_id
        ),
        "pathSupis": path_supis,
        "supis": path_supis["a"] + path_supis["b"],
    }


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
    configured = testbed.get("config", {}).get("directory")
    if not isinstance(configured, str) or not configured:
        raise ValueError("selected testbed config.directory is required")
    if explicit:
        return resolve_path(explicit)
    return resolve_path(configured)


def load_scenario_definition(value):
    path = resolve_path(value).resolve()
    if path != ROOT and ROOT not in path.parents:
        raise ValueError("scenario definition must remain inside the repository")
    scenario = load_yaml(path)
    if scenario.get("schemaVersion") != SCENARIO_SCHEMA:
        raise ValueError(
            "unsupported scenario schema: expected {}, got {}".format(
                SCENARIO_SCHEMA, scenario.get("schemaVersion")
            )
        )
    return path, scenario


def resolve_scenario_profile_paths(scenario_path, scenario):
    """Resolve Path A/B traffic profiles relative to their scenario definition."""
    references = scenario.get("trafficProfiles")
    if not isinstance(references, dict) or sorted(references) != ["a", "b"]:
        raise ValueError("scenario trafficProfiles must contain Path A and B")

    resolved = {}
    for path_name in ("a", "b"):
        reference = references[path_name]
        if not isinstance(reference, str) or not reference:
            raise ValueError(
                "scenario trafficProfiles.{} must be a non-empty relative path".format(
                    path_name
                )
            )
        relative = Path(reference)
        if relative.is_absolute():
            raise ValueError(
                "scenario trafficProfiles.{} must be relative to scenario.yaml".format(
                    path_name
                )
            )
        profile_path = (Path(scenario_path).parent / relative).resolve()
        if profile_path == ROOT or ROOT not in profile_path.parents:
            raise ValueError(
                "scenario trafficProfiles.{} escapes the repository: {}".format(
                    path_name, reference
                )
            )
        if not profile_path.is_file():
            raise ValueError(
                "scenario trafficProfiles.{} does not exist: {}".format(
                    path_name, reference
                )
            )
        resolved[path_name] = profile_path
    return resolved


def repository_relative_paths(paths):
    """Return stable repository-relative provenance strings for resolved paths."""
    return {
        name: path.relative_to(ROOT).as_posix()
        for name, path in paths.items()
    }


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
    resolve_scenario_profile_paths(path, scenario)
    return path, scenario


def resolve_ml_bind_address(testbed):
    address = testbed.get("mlRuntime", {}).get("bindAddress")
    if not isinstance(address, str) or not address:
        raise ValueError("selected testbed mlRuntime.bindAddress is required")
    return address


def resolve_ml_device_policy(config_dir):
    manifest = load_yaml(Path(config_dir) / "manifest.yaml")
    policy = manifest.get("runtime", {}).get("mlDevicePolicy")
    if policy not in ("cpu", "gpu"):
        raise ValueError("runtime.mlDevicePolicy must be cpu or gpu")
    return policy


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(directory):
    digest = hashlib.sha256()
    for path in sorted(path for path in Path(directory).rglob("*") if path.is_file()):
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def canonical_sha256(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def config_generator_source_hash():
    digest = hashlib.sha256()
    directory = ROOT / "scripts" / "host"
    for name in ("config-render.py", "configlib.py"):
        path = directory / name
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


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
