#!/usr/bin/env python3
"""Shared, host-only configuration helpers."""

import hashlib
import ipaddress
import json
import re
import sys
import uuid
from pathlib import Path

import yaml


SHARED_ROOT = Path(__file__).resolve().parents[1] / "shared"
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

from config_hash import sha256_tree


ROOT = Path(__file__).resolve().parents[2]
SCENARIO_SCHEMA = 2


def deployment_kind(testbed):
    """Return the selected complete TESTBED deployment contract."""
    kind = testbed.get("analytics", {}).get("topology", "production-flat")
    if kind not in ("production-flat", "static-flat", "static-hierarchical"):
        raise ValueError("analytics.topology must be production-flat, static-flat, or static-hierarchical")
    return kind


def nwdaf_definitions(testbed):
    """Resolve the ordered logical NWDAF inventory from the selected TESTBED."""
    analytics = testbed.get("analytics", {})
    definitions = []
    for unit, item in analytics.items():
        if not unit.startswith("nwdaf-"):
            continue
        if not isinstance(item, dict):
            raise ValueError("analytics.{} must be an object".format(unit))
        definition = dict(item)
        definition["unit"] = unit
        definitions.append(definition)
    if not definitions:
        raise ValueError("selected TESTBED must declare at least one NWDAF")
    return definitions


def _service_kind(testbed, unit):
    if unit in testbed.get("coreServices", {}) or unit == "mongodb":
        return "core"
    for prefix, kind in (("upf-", "upf"), ("nwdaf-", "nwdaf"), ("gnb-", "gnb"), ("ue", "ue")):
        if unit.startswith(prefix):
            return kind
    raise ValueError("placement contains unsupported Guest unit: {}".format(unit))


def _guest_service_start_rank(service):
    """Preserve the production dependency order for manifest-driven lifecycle."""
    unit = service["unit"]
    core_order = {
        "mongodb": 0,
        "nrf": 10,
        "nssf": 11,
        "udr": 12,
        "udm": 13,
        "ausf": 14,
        "pcf": 15,
        "amf": 16,
        "smf": 30,
        "adrf": 40,
    }
    if unit in core_order:
        return core_order[unit]
    return {
        "upf": 20,
        "nwdaf": 50,
        "gnb": 60,
        "ue": 70,
    }[service["kind"]]


def expected_runtime_inventory(testbed):
    """Build the exact generated runtime inventory from one trusted TESTBED."""
    kind = deployment_kind(testbed)
    machines = ["core", "path-a", "path-b"]
    if sorted(testbed.get("machines", {})) != sorted(machines):
        raise ValueError("selected TESTBED must declare exactly core, path-a, and path-b")
    placement = testbed.get("placement", {})
    guest_services = []
    for machine in machines:
        units = placement.get(machine)
        if not isinstance(units, list) or not units:
            raise ValueError("placement.{} must be a non-empty list".format(machine))
        for unit in units:
            if unit == "nwdaf-consumer":
                continue
            guest_services.append({
                "machine": machine,
                "unit": unit,
                "kind": _service_kind(testbed, unit),
            })
    guest_services.sort(key=_guest_service_start_rank)

    identities = resolve_mobile_identities(testbed)
    ue_inventory = []
    index = 0
    for path_name in ("a", "b"):
        machine = testbed["paths"][path_name]["machine"]
        for supi in identities["pathSupis"][path_name]:
            index += 1
            ue_inventory.append({
                "unit": "ue{}".format(index),
                "machine": machine,
                "path": path_name,
                "supi": supi,
            })

    nwdafs = []
    endpoint_identities = set()
    instance_ids = set()
    backend_names = []
    coordinator = None
    for item in nwdaf_definitions(testbed):
        role = item.get("role")
        if role not in ("fl-client", "fl-server", "client", "server", "root", "branch", "leaf"):
            raise ValueError("{} has an invalid role".format(item["unit"]))
        try:
            parsed = uuid.UUID(item.get("nfInstanceId", ""))
        except (AttributeError, ValueError) as error:
            raise ValueError("{} has an invalid NF Instance ID".format(item["unit"])) from error
        if parsed.version != 4 or str(parsed) != item["nfInstanceId"]:
            raise ValueError("{} NF Instance ID must be a lowercase UUIDv4".format(item["unit"]))
        if item["nfInstanceId"] in instance_ids:
            raise ValueError("NWDAF NF Instance IDs must be unique")
        instance_ids.add(item["nfInstanceId"])
        sbi = item.get("sbi", {})
        endpoint = (sbi.get("address"), sbi.get("port"))
        if endpoint in endpoint_identities:
            raise ValueError("NWDAF SBI endpoints must be unique")
        endpoint_identities.add(endpoint)
        backends = item.get("backends")
        if not isinstance(backends, dict) or "mtlf" not in backends:
            raise ValueError("{} must declare its MTLF backend".format(item["unit"]))
        backend_names.extend(backends.values())
        if role in ("fl-server", "server", "root"):
            if coordinator is not None:
                raise ValueError("selected TESTBED must declare exactly one coordinator")
            coordinator = backends["mtlf"]
        nwdafs.append({
            "unit": item["unit"],
            "machine": item["machine"],
            "role": role,
            "nfInstanceId": item["nfInstanceId"],
            "sbi": dict(sbi),
            "internalApi": {
                "anlf": {"address": sbi["address"], "port": 8090},
                "mtlf": {"address": sbi["address"], "port": 8091},
            },
            "backends": dict(backends),
        })
    if coordinator is None:
        raise ValueError("selected TESTBED must declare one coordinator NWDAF")
    expected_roles = {
        "production-flat": ["fl-client", "fl-client", "fl-server"],
        "static-flat": ["client", "client", "client", "client", "server"],
        "static-hierarchical": ["branch", "branch", "leaf", "leaf", "leaf", "leaf", "root"],
    }[kind]
    if sorted(item["role"] for item in nwdafs) != sorted(expected_roles):
        raise ValueError("selected TESTBED NWDAF roles do not match its topology")

    containers = placement.get("host-containers")
    if not isinstance(containers, list) or not containers:
        raise ValueError("placement.host-containers must be a non-empty list")
    services = testbed.get("mlRuntime", {}).get("services", {})
    if set(containers) != set(services) or len(containers) != len(services):
        raise ValueError("placement.host-containers must exactly match mlRuntime.services")
    if sorted(backend_names) != sorted(containers):
        raise ValueError("NWDAF backends must map one-to-one to Host containers")
    published = set()
    volumes = []
    total_cpus = 0.0
    total_memory = 0
    gpu_participants = 0
    for name in containers:
        service = services[name]
        port = service.get("publishedPort")
        if not isinstance(port, int) or port in published:
            raise ValueError("ML published ports must be unique integers")
        published.add(port)
        volume = service.get("volume")
        if not isinstance(volume, dict) or set(volume) != {"name", "target"}:
            raise ValueError("mlRuntime.services.{}.volume requires name and target".format(name))
        image = service.get("image")
        if image not in ("pyanlf", "pymtlf"):
            raise ValueError("mlRuntime.services.{}.image must be pyanlf or pymtlf".format(name))
        volumes.append({
            "name": volume["name"],
            "image": "5g-nwdaf-infrastructure/{}:local".format(image),
        })
        cpus = service.get("cpus")
        memory = service.get("memoryMiB")
        if not isinstance(cpus, (int, float)) or isinstance(cpus, bool) or cpus <= 0:
            raise ValueError("mlRuntime.services.{}.cpus must be positive".format(name))
        if not isinstance(memory, int) or isinstance(memory, bool) or memory <= 0:
            raise ValueError("mlRuntime.services.{}.memoryMiB must be positive".format(name))
        total_cpus += float(cpus)
        total_memory += memory
        if str(service.get("device", "")).startswith("cuda"):
            gpu_participants += 1
    if len({item["name"] for item in volumes}) != len(volumes):
        raise ValueError("ML volume names must be unique")

    owners = []
    if kind != "production-flat":
        by_number = dict(zip(identities["subscriberNumbers"], identities["supis"]))
        declared = testbed.get("analytics", {}).get("dataOwners")
        if not isinstance(declared, list) or len(declared) != 4:
            raise ValueError("static TESTBED requires four analytics.dataOwners")
        seen_numbers = []
        for owner in declared:
            numbers = owner.get("subscriberNumbers")
            if owner.get("position") not in (1, 2, 3, 4) or owner.get("path") not in ("a", "b"):
                raise ValueError("static data owner position/path is invalid")
            if not isinstance(numbers, list) or len(numbers) != 2:
                raise ValueError("each static data owner must own two subscribers")
            seen_numbers.extend(numbers)
            local_id = owner.get("groupLocalId")
            group = testbed["mobileNetwork"]["internalGroup"]
            owners.append({
                "position": owner["position"],
                "path": owner["path"],
                "internalGroupId": "{}-{}-{}-{}".format(
                    group["serviceId"], identities["plmn"]["mcc"], identities["plmn"]["mnc"], local_id
                ),
                "subscriberNumbers": list(numbers),
                "supis": [by_number[number] for number in numbers],
            })
        if sorted(seen_numbers) != sorted(identities["subscriberNumbers"]):
            raise ValueError("static data owners must cover every subscriber exactly once")
        positions = {owner["position"] for owner in owners}
        assigned = [
            item.get("dataOwner") for item in nwdaf_definitions(testbed)
            if item.get("role") in ("client", "leaf")
        ]
        if set(assigned) != positions or len(assigned) != len(positions):
            raise ValueError("static Client/Leaf positions must map one-to-one to data owners")
        if kind == "static-hierarchical":
            branch_leaves = [
                position for item in nwdaf_definitions(testbed)
                if item.get("role") == "branch" for position in item.get("leaves", [])
            ]
            if sorted(branch_leaves) != sorted(positions):
                raise ValueError("HFL Branch leaves must cover every Leaf position exactly once")

    safety = testbed.get("hostSafety", {})
    build_overhead = safety.get("containerBuildOverheadMemoryMiB")
    gpu_memory = safety.get("minimumGpuMemoryMiB")
    if not isinstance(build_overhead, int) or build_overhead < 0:
        raise ValueError("hostSafety.containerBuildOverheadMemoryMiB must be non-negative")
    if not isinstance(gpu_memory, int) or gpu_memory < 0:
        raise ValueError("hostSafety.minimumGpuMemoryMiB must be non-negative")
    runtime = {
        "deploymentKind": kind,
        "guestMachines": machines,
        "guestServices": guest_services,
        "nwdafs": nwdafs,
        "ues": ue_inventory,
        "dataOwners": owners,
        "hostContainers": list(containers),
        "mlVolumes": volumes,
        "subscriptions": "consumer" if kind == "production-flat" else "none",
        "coordinatorContainer": coordinator,
        "mlDevicePolicy": "gpu" if gpu_participants else "cpu",
        "capacity": {
            "guestCpus": sum(machine["resources"]["cpus"] for machine in testbed["machines"].values()),
            "guestMemoryMiB": sum(machine["resources"]["memoryMiB"] for machine in testbed["machines"].values()),
            "hostContainerCpus": total_cpus,
            "hostContainerMemoryMiB": total_memory,
            "containerBuildOverheadMemoryMiB": build_overhead,
            "gpuParticipants": gpu_participants,
            "minimumGpuMemoryMiB": gpu_memory if gpu_participants else 0,
            "publishedPorts": sorted(published),
        },
    }
    runtime["resetScope"] = {
        "guestServices": list(guest_services),
        "hostContainers": list(containers),
        "mlVolumes": list(volumes),
        "nrf": {
            "database": testbed["coreServices"]["mongodb"]["database"],
            "collections": ["NfProfile", "urilist"],
            "nfType": "ADRF",
        },
        "adrf": {
            "database": testbed["coreServices"]["adrf"]["mongodb"]["database"],
            "collections": ["data_store_records", "mlmodel_store_records"],
            "modelStorage": testbed["coreServices"]["adrf"]["modelStorage"]["localDirectory"],
            "nfInstanceId": testbed["coreServices"]["adrf"]["nfInstanceId"],
        },
    }
    return runtime


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
        if not isinstance(numbers, list) or not numbers:
            raise ValueError(
                "paths.{}.subscriberNumbers must contain at least one integer".format(
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
        "subscriberNumbers": list(all_numbers),
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


def load_runtime_manifest(config_dir):
    """Load and validate the scenario-owned process inventory."""
    manifest = load_yaml(Path(config_dir) / "manifest.yaml")
    runtime = manifest.get("runtime", {})
    machines = runtime.get("guestMachines")
    services = runtime.get("guestServices")
    containers = runtime.get("hostContainers")
    volumes = runtime.get("mlVolumes")
    if machines != ["core", "path-a", "path-b"]:
        raise ValueError("runtime.guestMachines must be core, path-a, path-b")
    if not isinstance(services, list) or not services:
        raise ValueError("runtime.guestServices must be a non-empty list")
    seen_units = set()
    for item in services:
        if not isinstance(item, dict):
            raise ValueError("runtime.guestServices entries must be objects")
        machine = item.get("machine")
        unit = item.get("unit")
        kind = item.get("kind")
        if machine not in machines:
            raise ValueError("runtime Guest service has an unknown machine")
        if not isinstance(unit, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", unit):
            raise ValueError("runtime Guest service has an invalid unit")
        if kind not in ("core", "upf", "nwdaf", "gnb", "ue"):
            raise ValueError("runtime Guest service has an invalid kind")
        identity = (machine, unit)
        if identity in seen_units:
            raise ValueError("runtime Guest services must be unique")
        seen_units.add(identity)
    if not isinstance(containers, list) or not containers or len(containers) != len(set(containers)):
        raise ValueError("runtime.hostContainers must be a non-empty unique list")
    if any(
        not isinstance(item, str)
        or not re.fullmatch(r"(?:pyanlf|pymtlf)-[a-z0-9][a-z0-9-]*", item)
        for item in containers
    ):
        raise ValueError("runtime.hostContainers contains an invalid service")
    if not isinstance(volumes, list):
        raise ValueError("runtime.mlVolumes must be a list")
    seen_volumes = set()
    for item in volumes:
        if not isinstance(item, dict) or set(item) != {"name", "image"}:
            raise ValueError("runtime.mlVolumes entries require name and image")
        if item["image"] not in (
            "5g-nwdaf-infrastructure/pyanlf:local",
            "5g-nwdaf-infrastructure/pymtlf:local",
        ):
            raise ValueError("runtime ML volume image is not owned by this project")
        if (
            not isinstance(item["name"], str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", item["name"])
            or item["name"] in seen_volumes
        ):
            raise ValueError("runtime ML volume names must be unique and valid")
        seen_volumes.add(item["name"])
    subscriptions = runtime.get("subscriptions")
    if subscriptions not in ("consumer", "none"):
        raise ValueError("runtime.subscriptions must be consumer or none")
    coordinator = runtime.get("coordinatorContainer")
    if not isinstance(coordinator, str) or coordinator not in containers:
        raise ValueError("runtime.coordinatorContainer must select a Host container")
    if runtime.get("deploymentKind") not in (
        "production-flat", "static-flat", "static-hierarchical"
    ):
        raise ValueError("runtime.deploymentKind is invalid")
    if not isinstance(runtime.get("nwdafs"), list) or not runtime["nwdafs"]:
        raise ValueError("runtime.nwdafs must be a non-empty list")
    if not isinstance(runtime.get("ues"), list) or not runtime["ues"]:
        raise ValueError("runtime.ues must be a non-empty list")
    if not isinstance(runtime.get("dataOwners"), list):
        raise ValueError("runtime.dataOwners must be a list")
    capacity = runtime.get("capacity")
    if not isinstance(capacity, dict):
        raise ValueError("runtime.capacity must be an object")
    for field in (
        "guestCpus", "guestMemoryMiB", "hostContainerCpus",
        "hostContainerMemoryMiB", "containerBuildOverheadMemoryMiB",
        "gpuParticipants", "minimumGpuMemoryMiB",
    ):
        value = capacity.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ValueError("runtime.capacity.{} must be non-negative".format(field))
    ports = capacity.get("publishedPorts")
    if not isinstance(ports, list) or len(ports) != len(set(ports)):
        raise ValueError("runtime.capacity.publishedPorts must be a unique list")
    reset_scope = runtime.get("resetScope")
    if not isinstance(reset_scope, dict):
        raise ValueError("runtime.resetScope must be an object")
    if reset_scope.get("guestServices") != services:
        raise ValueError("runtime.resetScope.guestServices must match Guest inventory")
    if reset_scope.get("hostContainers") != containers:
        raise ValueError("runtime.resetScope.hostContainers must match Host inventory")
    if reset_scope.get("mlVolumes") != volumes:
        raise ValueError("runtime.resetScope.mlVolumes must match ML volume inventory")
    return manifest


def runtime_guest_services(config_dir):
    return load_runtime_manifest(config_dir)["runtime"]["guestServices"]


def runtime_host_containers(config_dir):
    return load_runtime_manifest(config_dir)["runtime"]["hostContainers"]


def runtime_ml_volumes(config_dir):
    return load_runtime_manifest(config_dir)["runtime"]["mlVolumes"]


def runtime_subscriptions(config_dir):
    return load_runtime_manifest(config_dir)["runtime"]["subscriptions"]


def runtime_coordinator_container(config_dir):
    return load_runtime_manifest(config_dir)["runtime"]["coordinatorContainer"]


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def config_generator_source_hash():
    digest = hashlib.sha256()
    for path in (
        ROOT / "scripts" / "host" / "config-render.py",
        ROOT / "scripts" / "host" / "configlib.py",
        ROOT / "scripts" / "shared" / "config_hash.py",
    ):
        name = path.relative_to(ROOT).as_posix()
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def guest_network_configs(testbed, analytics=None, include_consumer=True):
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

    analytics = testbed["analytics"] if analytics is None else analytics
    for owner, service in analytics.items():
        if owner.startswith("nwdaf-"):
            add(service["machine"], owner, "sbi", service["sbi"])

    if include_consumer:
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
