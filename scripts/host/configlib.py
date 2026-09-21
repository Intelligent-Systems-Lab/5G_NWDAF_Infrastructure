#!/usr/bin/env python3
"""Shared, host-only configuration helpers."""

import ipaddress
import copy
import math
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
    if kind not in (
        "production-flat", "static-flat", "static-hierarchical",
        "protocol-hierarchical",
    ):
        raise ValueError(
            "analytics.topology must be production-flat, static-flat, "
            "static-hierarchical, or protocol-hierarchical"
        )
    return kind


def selected_machine_names(testbed):
    """Return the validated machine inventory in declaration order."""
    machines = testbed.get("machines")
    if not isinstance(machines, dict) or not machines:
        raise ValueError("selected TESTBED machines must be a non-empty mapping")
    names = list(machines)
    for name, definition in machines.items():
        if not isinstance(name, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) is None:
            raise ValueError("selected TESTBED contains an invalid machine name")
        if not isinstance(definition, dict):
            raise ValueError("machines.{} must be an object".format(name))
    return names


def scenario_profile(scenario):
    """Return the explicit workload discriminator for a schema-v2 scenario."""
    workload = scenario.get("workload")
    if isinstance(workload, dict):
        profile = workload.get("profile")
        if profile == "image-classification":
            return profile
        raise ValueError("scenario workload.profile is invalid")
    return "ue-communication"


def image_dataset_name(scenario):
    """Return the image dataset directory selected by the scenario."""
    dataset_id = scenario["partition"].get("datasetId")
    if dataset_id is None:
        return scenario["name"]
    if not isinstance(dataset_id, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]*", dataset_id) is None:
        raise ValueError("partition.datasetId must be a single directory name")
    return dataset_id


def image_scenario_contract(scenario):
    """Validate and return the bounded image-classification run contract."""
    if scenario_profile(scenario) != "image-classification":
        raise ValueError("scenario is not an image-classification workload")
    workload = scenario["workload"]
    dataset = workload.get("dataset")
    expected = {
        "mnist": {
            "modelInteroperability": "pymtlf-image-classification-mnist",
            "modelFamilyId": "image-classification-mnist",
            "seedModelId": 1001,
        },
        "cifar10": {
            "modelInteroperability": "pymtlf-image-classification-cifar10",
            "modelFamilyId": "image-classification-cifar10",
            "seedModelId": 1002,
        },
    }
    if dataset not in expected:
        raise ValueError("workload.dataset must be mnist or cifar10")
    if workload.get("event") != "X_IMAGE_CLASSIFICATION":
        raise ValueError("image workload event must be X_IMAGE_CLASSIFICATION")
    for field, value in expected[dataset].items():
        if workload.get(field) != value:
            raise ValueError("workload.{} does not match {}".format(field, dataset))
    artifact_key = workload.get("seedArtifactKey")
    if not isinstance(artifact_key, str) or re.fullmatch(r"[0-9a-f]{64}", artifact_key) is None:
        raise ValueError("workload.seedArtifactKey must use the component-native artifact identity")
    partition = scenario.get("partition", {})
    if "datasetId" in partition:
        image_dataset_name(scenario)
    for field in ("samplesPerLeaf", "validationSamples", "heldOutSamples"):
        value = partition.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError("partition.{} must be a positive integer".format(field))
    leaf_labels = partition.get("leafLabels")
    leaf_class_counts = partition.get("leafClassCounts")
    if "leafLabels" in partition and "leafClassCounts" in partition:
        raise ValueError("partition.leafLabels and leafClassCounts are mutually exclusive")
    if "leafLabels" not in partition and "leafClassCounts" not in partition:
        if partition["samplesPerLeaf"] % 10:
            raise ValueError("partition.samplesPerLeaf must be divisible by ten classes")
    elif "leafLabels" in partition:
        if not isinstance(leaf_labels, dict) or not leaf_labels:
            raise ValueError("partition.leafLabels must map Leaf names to class lists")
        for leaf, labels in leaf_labels.items():
            if (
                not isinstance(leaf, str)
                or not leaf
                or "/" in leaf
                or leaf in ("validation", "held-out")
            ):
                raise ValueError("partition.leafLabels contains an invalid artifact name")
            if (
                not isinstance(labels, list)
                or not labels
                or any(
                    not isinstance(label, int)
                    or isinstance(label, bool)
                    or label not in range(10)
                    for label in labels
                )
                or len(set(labels)) != len(labels)
                or partition["samplesPerLeaf"] % len(labels)
            ):
                raise ValueError(
                    "partition.leafLabels.{} must contain distinct classes with an equal quota".format(leaf)
                )
    else:
        if not isinstance(leaf_class_counts, dict) or not leaf_class_counts:
            raise ValueError("partition.leafClassCounts must map Leaf names to class quotas")
        for leaf, counts in leaf_class_counts.items():
            if (
                not isinstance(leaf, str)
                or not leaf
                or "/" in leaf
                or leaf in ("validation", "held-out")
            ):
                raise ValueError("partition.leafClassCounts contains an invalid artifact name")
            if (
                not isinstance(counts, dict)
                or not counts
                or any(
                    not isinstance(label, int)
                    or isinstance(label, bool)
                    or label not in range(10)
                    or not isinstance(count, int)
                    or isinstance(count, bool)
                    or count <= 0
                    for label, count in counts.items()
                )
                or sum(counts.values()) != partition["samplesPerLeaf"]
            ):
                raise ValueError(
                    "partition.leafClassCounts.{} must have positive class quotas totaling samplesPerLeaf".format(leaf)
                )
    validation_source = partition.get("validationSource", "official-test")
    if validation_source not in ("official-train", "official-test"):
        raise ValueError("partition.validationSource must be official-train or official-test")
    if (leaf_labels is not None or leaf_class_counts is not None) and validation_source != "official-train":
        raise ValueError("partition Leaf quotas require official-train validation")
    if partition["validationSamples"] % 10:
        raise ValueError("partition.validationSamples must be divisible by ten classes")
    if validation_source == "official-test" and partition["heldOutSamples"] % 10:
        raise ValueError("partition.heldOutSamples must be divisible by ten classes")
    seed = partition.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError("partition.seed must be a non-negative integer")
    training = scenario.get("training", {})
    for field in ("acceptedRounds", "batchSize", "localEpochs"):
        value = training.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError("training.{} must be a positive integer".format(field))
    learning_rate = training.get("learningRate")
    if (
        not isinstance(learning_rate, (int, float))
        or isinstance(learning_rate, bool)
        or learning_rate <= 0
    ):
        raise ValueError("training.learningRate must be positive")
    if "proximalMu" in training:
        proximal_mu = training["proximalMu"]
        if (
            not isinstance(proximal_mu, (int, float))
            or isinstance(proximal_mu, bool)
            or not math.isfinite(proximal_mu)
            or proximal_mu < 0
        ):
            raise ValueError("training.proximalMu must be finite and non-negative")
    if "device" in training:
        raise ValueError(
            "image-classification scenario must not duplicate TESTBED-owned device selection"
        )
    topology = scenario.get("topology")
    if not isinstance(topology, dict) or set(topology) != {"onBranchFailure"}:
        raise ValueError("scenario.topology.onBranchFailure is required")
    if topology["onBranchFailure"] not in ("replace_branch", "reparent_leaves_to_root"):
        raise ValueError("scenario.topology.onBranchFailure is invalid")
    fault = scenario.get("fault")
    observation = scenario.get("observation")
    if fault is None:
        if observation is not None:
            raise ValueError("normal image scenario must not define fault observation")
        return scenario
    if not isinstance(fault, dict) or set(fault) != {
        "normalAcceptedRounds", "stopNodes",
    }:
        raise ValueError("fault contract has invalid fields")
    normal_rounds = fault["normalAcceptedRounds"]
    if (
        not isinstance(normal_rounds, int)
        or isinstance(normal_rounds, bool)
        or not 0 < normal_rounds < training["acceptedRounds"]
    ):
        raise ValueError("fault.normalAcceptedRounds must precede the final accepted round")
    stop_nodes = fault["stopNodes"]
    if (
        not isinstance(stop_nodes, list)
        or not stop_nodes
        or any(not isinstance(node, str) or not node for node in stop_nodes)
        or len(stop_nodes) != len(set(stop_nodes))
    ):
        raise ValueError("fault.stopNodes must contain distinct node names")
    if not isinstance(observation, dict) or set(observation) != {
        "pollIntervalMilliseconds", "heartbeatSeconds",
    }:
        raise ValueError("fault observation contract has invalid fields")
    for field in ("pollIntervalMilliseconds", "heartbeatSeconds"):
        value = observation[field]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError("observation.{} must be a positive integer".format(field))
    return scenario


def resolve_fault_targets(testbed, scenario):
    """Resolve ordered active stop targets from the selected topology."""
    image_scenario_contract(scenario)
    fault = scenario.get("fault")
    if fault is None:
        raise ValueError("scenario does not define a fault")
    protocol_topology(
        testbed,
        scenario["training"]["localEpochs"],
        scenario["training"].get("proximalMu"),
        scenario["topology"]["onBranchFailure"],
    )
    groups = testbed["analytics"]["protocolTopology"]["branchGroups"]
    definitions = {item["unit"]: item for item in nwdaf_definitions(testbed)}
    matches = []
    for group in groups:
        enabled = [item for item in group["branches"] if item["enabled"]]
        primary = max(enabled, key=lambda item: item["priority"])
        if primary["node"] == fault["stopNodes"][0]:
            matches.append((group, enabled))
    if len(matches) != 1:
        raise ValueError("first fault.stopNodes entry must be an active highest-priority Branch")
    group, enabled = matches[0]
    enabled_leaves = {item["node"] for item in group["leaves"] if item["enabled"]}
    if any(node not in enabled_leaves for node in fault["stopNodes"][1:]):
        raise ValueError("remaining fault.stopNodes entries must be active Leaves in the same group")
    replacements = sorted(enabled, key=lambda item: item["priority"], reverse=True)[1:]
    if scenario["topology"]["onBranchFailure"] == "replace_branch" and not replacements:
        raise ValueError("replace_branch fault requires a replacement candidate")
    targets = []
    for node in fault["stopNodes"]:
        definition = definitions[node]
        targets.append({
            "nfInstanceId": definition["nfInstanceId"],
            "unit": node,
            "machine": definition["machine"],
            "service": definition["backends"]["mtlf"],
        })
    return {
        "group": group["name"],
        "targets": targets,
        "replacement": (
            copy.deepcopy(definitions[replacements[0]["node"]])
            if replacements else None
        ),
    }


def protocol_topology(testbed, local_epochs, proximal_mu=None, on_branch_failure="replace_branch"):
    """Resolve logical node references into the current PyMTLF topology contract."""
    if deployment_kind(testbed) != "protocol-hierarchical":
        raise ValueError("selected TESTBED is not protocol-hierarchical")
    if (
        not isinstance(local_epochs, int)
        or isinstance(local_epochs, bool)
        or local_epochs <= 0
    ):
        raise ValueError("Leaf local epochs must be a positive integer")
    if on_branch_failure not in ("replace_branch", "reparent_leaves_to_root"):
        raise ValueError("protocol on_branch_failure is invalid")
    definitions = {item["unit"]: item for item in nwdaf_definitions(testbed)}
    source = testbed.get("analytics", {}).get("protocolTopology")
    if not isinstance(source, dict):
        raise ValueError("analytics.protocolTopology must be an object")
    groups = source.get("branchGroups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("analytics.protocolTopology.branchGroups must be non-empty")

    def resolved_strategy(value):
        strategy = copy.deepcopy(value)
        if proximal_mu is not None:
            if not isinstance(strategy, dict) or strategy.get("method") != "fedProx":
                raise ValueError("training.proximalMu requires a fedProx topology strategy")
            parameters = strategy.get("method_parameters")
            if not isinstance(parameters, dict):
                raise ValueError("fedProx topology strategy has no method parameters")
            parameters["proximal_mu"] = proximal_mu
        return strategy

    def resolve_candidate(candidate, role):
        if not isinstance(candidate, dict):
            raise ValueError("protocol topology candidate must be an object")
        node = candidate.get("node")
        if node not in definitions or definitions[node].get("role") != role:
            raise ValueError("protocol topology references an invalid {} node".format(role))
        if role == "branch":
            report = candidate.get("reportAfter")
            if (
                not isinstance(report, dict)
                or not isinstance(report.get("count"), int)
                or isinstance(report.get("count"), bool)
                or report["count"] <= 0
                or report.get("unit") != "round"
            ):
                raise ValueError("branch {} has an invalid reportAfter".format(node))
            report_after = {"count": report["count"], "unit": "round"}
        else:
            if "reportAfter" in candidate:
                raise ValueError(
                    "leaf {} must not define TESTBED reportAfter".format(node)
                )
            report_after = {"count": local_epochs, "unit": "epoch"}
        priority = candidate.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise ValueError("{} {} priority must be an integer".format(role, node))
        if not isinstance(candidate.get("enabled"), bool):
            raise ValueError("{} {} enabled must be boolean".format(role, node))
        return {
            "nf_instance_id": definitions[node]["nfInstanceId"],
            "enabled": candidate["enabled"],
            "priority": priority,
            "report_after": report_after,
        }

    native_groups = []
    seen_nodes = []
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("protocol branch group must be an object")
        branches = [resolve_candidate(item, "branch") for item in group.get("branches", [])]
        leaves = [resolve_candidate(item, "leaf") for item in group.get("leaves", [])]
        if not branches or not leaves:
            raise ValueError("protocol branch groups require Branches and Leaves")
        seen_nodes.extend(item.get("node") for item in group.get("branches", []))
        seen_nodes.extend(item.get("node") for item in group.get("leaves", []))
        native_groups.append({
            "branches": branches,
            "policy": copy.deepcopy(group.get("policy")),
            "strategy": resolved_strategy(group.get("strategy")),
            "leaves": leaves,
        })
    expected_nodes = {
        unit for unit, item in definitions.items() if item.get("role") in ("branch", "leaf")
    }
    if len(seen_nodes) != len(set(seen_nodes)) or set(seen_nodes) != expected_nodes:
        raise ValueError("protocol topology must cover every Branch and Leaf exactly once")
    return {
        "on_branch_failure": on_branch_failure,
        "policy": copy.deepcopy(source.get("policy")),
        "strategy": resolved_strategy(source.get("strategy")),
        "branch_groups": native_groups,
    }


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


def expected_runtime_inventory(testbed, scenario=None):
    """Build the exact generated runtime inventory from one trusted TESTBED."""
    kind = deployment_kind(testbed)
    machines = selected_machine_names(testbed)
    placement = testbed.get("placement", {})
    if set(placement) != set(machines) | {"host-containers"}:
        raise ValueError("placement machine inventory must exactly match machines")
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

    identities = None if kind == "protocol-hierarchical" else resolve_mobile_identities(testbed)
    ue_inventory = []
    if identities is not None:
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
        if item.get("machine") not in machines:
            raise ValueError("{} references an unknown machine".format(item["unit"]))
        if item["unit"] not in placement[item["machine"]]:
            raise ValueError("{} placement does not match its machine".format(item["unit"]))
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
        "protocol-hierarchical": [
            "root", "branch", "branch", "branch", "branch",
            "leaf", "leaf", "leaf", "leaf", "leaf", "leaf",
        ],
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
        if service.get("device") not in ("cpu", "cuda:0"):
            raise ValueError("mlRuntime.services.{}.device must be cpu or cuda:0".format(name))
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
    if kind in ("static-flat", "static-hierarchical"):
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
    if kind == "protocol-hierarchical":
        accelerated = {
            item["backends"]["mtlf"]
            for item in nwdaf_definitions(testbed)
            if item["role"] in ("root", "leaf")
        }
        actual_accelerated = {
            name for name in containers if services[name]["device"] == "cuda:0"
        }
        if actual_accelerated not in (set(), accelerated):
            raise ValueError(
                "protocol runtime must assign CUDA to Root and all Leaves, or use CPU for all"
            )
        if actual_accelerated and gpu_memory < 8192:
            raise ValueError(
                "protocol GPU runtime requires at least 8192 MiB pre-start free memory"
            )
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
    if kind == "protocol-hierarchical":
        runtime["capacity"]["guestDiskGiB"] = sum(
            machine["resources"]["diskGiB"]
            for machine in testbed["machines"].values()
        )
    runtime["resetScope"] = {
        "guestServices": copy.deepcopy(guest_services),
        "hostContainers": list(containers),
        "mlVolumes": copy.deepcopy(volumes),
        "nrf": {
            "database": testbed["coreServices"]["mongodb"]["database"],
            "collections": ["NfProfile", "urilist"],
        },
        "adrf": {
            "database": testbed["coreServices"]["adrf"]["mongodb"]["database"],
            "collections": ["data_store_records", "mlmodel_store_records"],
            "modelStorage": testbed["coreServices"]["adrf"]["modelStorage"]["localDirectory"],
            "nfInstanceId": testbed["coreServices"]["adrf"]["nfInstanceId"],
        },
    }
    if kind == "protocol-hierarchical":
        runtime["resetScope"]["nrf"]["nfInstanceIds"] = [
            item["nfInstanceId"] for item in nwdafs
        ] + [testbed["coreServices"]["adrf"]["nfInstanceId"]]
    else:
        runtime["resetScope"]["nrf"]["nfType"] = "ADRF"
    if kind == "protocol-hierarchical" and scenario is not None:
        image_scenario_contract(scenario)
        definitions = {item["unit"]: item for item in nwdaf_definitions(testbed)}
        fault_group = (
            resolve_fault_targets(testbed, scenario)["group"]
            if scenario.get("fault") is not None else None
        )
        inactive_units = set()
        for group in testbed["analytics"]["protocolTopology"]["branchGroups"]:
            enabled = [item for item in group["branches"] if item["enabled"]]
            primary = max(enabled, key=lambda item: item["priority"])
            for candidate in enabled:
                if candidate is primary:
                    continue
                if (
                    fault_group == group["name"]
                    and scenario["topology"]["onBranchFailure"] == "replace_branch"
                ):
                    continue
                inactive_units.add(candidate["node"])
        inactive_containers = {
            definitions[unit]["backends"]["mtlf"] for unit in inactive_units
        }
        runtime["guestServices"] = [
            item for item in guest_services if item["unit"] not in inactive_units
        ]
        runtime["hostContainers"] = [
            name for name in containers if name not in inactive_containers
        ]
        active_volumes = {
            services[name]["volume"]["name"] for name in runtime["hostContainers"]
        }
        runtime["mlVolumes"] = [
            item for item in volumes if item["name"] in active_volumes
        ]
        active_services = [services[name] for name in runtime["hostContainers"]]
        runtime["capacity"].update({
            "hostContainerCpus": sum(float(item["cpus"]) for item in active_services),
            "hostContainerMemoryMiB": sum(item["memoryMiB"] for item in active_services),
            "gpuParticipants": sum(item["device"] == "cuda:0" for item in active_services),
            "publishedPorts": sorted(item["publishedPort"] for item in active_services),
        })
    return runtime


def selected_component_paths(testbed, runtime, optional_services=None):
    """Return only source repositories reachable from the selected runtime."""
    paths = set()
    for service in runtime["guestServices"]:
        unit = service["unit"]
        kind = service["kind"]
        if unit == "mongodb":
            continue
        if kind == "nwdaf":
            paths.add("NFs/nwdaf")
        elif kind == "upf":
            paths.update(("NFs/upf", "kernel/gtp5g"))
        elif kind in ("gnb", "ue"):
            paths.add("RAN/UERANSIM")
        elif kind == "core" and unit in {
            "nrf", "nssf", "udr", "udm", "ausf", "pcf", "amf", "smf", "adrf",
        }:
            paths.add("NFs/" + unit)
        else:
            raise ValueError("unsupported selected Guest component: " + unit)
    images = testbed["mlRuntime"]["services"]
    for service in runtime["hostContainers"]:
        image = images[service]["image"]
        if image == "pyanlf":
            paths.add("ML/PyAnLF")
        elif image == "pymtlf":
            paths.add("ML/PyMTLF")
        else:
            raise ValueError("unsupported selected Host component: " + image)
    if (optional_services or {}).get("webconsole", {}).get("enabled") is True:
        paths.add("webconsole")
    return sorted(paths)


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
    if scenario_profile(scenario) == "image-classification":
        return {}
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
    if scenario_profile(scenario) == "ue-communication":
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
    if (
        not isinstance(machines, list)
        or not machines
        or len(machines) != len(set(machines))
        or any(
            not isinstance(machine, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9-]*", machine) is None
            for machine in machines
        )
    ):
        raise ValueError("runtime.guestMachines must be a non-empty unique safe list")
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
        "production-flat", "static-flat", "static-hierarchical",
        "protocol-hierarchical",
    ):
        raise ValueError("runtime.deploymentKind is invalid")
    if not isinstance(runtime.get("nwdafs"), list) or not runtime["nwdafs"]:
        raise ValueError("runtime.nwdafs must be a non-empty list")
    if not isinstance(runtime.get("ues"), list):
        raise ValueError("runtime.ues must be a list")
    if runtime["deploymentKind"] != "protocol-hierarchical" and not runtime["ues"]:
        raise ValueError("runtime.ues must be non-empty for UE deployments")
    if not isinstance(runtime.get("dataOwners"), list):
        raise ValueError("runtime.dataOwners must be a list")
    capacity = runtime.get("capacity")
    if not isinstance(capacity, dict):
        raise ValueError("runtime.capacity must be an object")
    capacity_fields = [
        "guestCpus", "guestMemoryMiB", "hostContainerCpus",
        "hostContainerMemoryMiB", "containerBuildOverheadMemoryMiB",
        "gpuParticipants", "minimumGpuMemoryMiB",
    ]
    if runtime.get("deploymentKind") == "protocol-hierarchical":
        capacity_fields.append("guestDiskGiB")
    for field in capacity_fields:
        value = capacity.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ValueError("runtime.capacity.{} must be non-negative".format(field))
    ports = capacity.get("publishedPorts")
    if not isinstance(ports, list) or len(ports) != len(set(ports)):
        raise ValueError("runtime.capacity.publishedPorts must be a unique list")
    reset_scope = runtime.get("resetScope")
    if not isinstance(reset_scope, dict):
        raise ValueError("runtime.resetScope must be an object")
    if not all(item in reset_scope.get("guestServices", []) for item in services):
        raise ValueError("runtime.guestServices must be within resetScope")
    if not all(item in reset_scope.get("hostContainers", []) for item in containers):
        raise ValueError("runtime.hostContainers must be within resetScope")
    if not all(item in reset_scope.get("mlVolumes", []) for item in volumes):
        raise ValueError("runtime.mlVolumes must be within resetScope")
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

    placement = testbed["placement"]
    for owner, service in testbed["coreServices"].items():
        owner_machines = [
            machine for machine in machines
            if owner in placement.get(machine, [])
        ]
        if len(owner_machines) != 1:
            raise ValueError("core service {} must have exactly one placement".format(owner))
        for endpoint_name in ("sbi", "n2", "n4", "endpoint"):
            endpoint = service.get(endpoint_name)
            if isinstance(endpoint, dict) and {"network", "address"} <= set(endpoint):
                add(owner_machines[0], owner, endpoint_name, endpoint)

    for path_name, path in testbed.get("paths", {}).items():
        machine = path["machine"]
        for endpoint_name in ("n2", "n3"):
            add(machine, "gnb-" + path_name, endpoint_name, path["gnb"][endpoint_name])
        for endpoint_name in ("n3", "n4", "n6", "eventExposure"):
            add(machine, "upf-" + path_name, endpoint_name, path["upf"][endpoint_name])

    analytics = testbed["analytics"] if analytics is None else analytics
    for owner, service in analytics.items():
        if owner.startswith("nwdaf-"):
            add(service["machine"], owner, "sbi", service["sbi"])

    if include_consumer and "consumer" in testbed:
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
