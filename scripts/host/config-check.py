#!/usr/bin/env python3
"""Validate one complete native config set against a testbed definition."""

import argparse
import copy
import ipaddress
import json
import subprocess
import sys
import uuid
from pathlib import Path

from configlib import (
    ROOT, SCENARIO_SCHEMA, get_path, deployment_kind,
    expected_runtime_inventory, guest_network_configs,
    image_scenario_contract, load_runtime_manifest, load_yaml, nwdaf_definitions,
    protocol_topology, resolve_config_dir,
    repository_relative_paths, resolve_config_scenario, resolve_ml_bind_address,
    resolve_mobile_identities, resolve_path, resolve_scenario_profile_paths,
    sha256_tree,
)
from datasetlib import resolve_dataset_spec


REQUIRED = {
    "nrfcfg.yaml", "amfcfg.yaml", "ausfcfg.yaml", "nssfcfg.yaml", "pcfcfg.yaml",
    "smfcfg.yaml", "udmcfg.yaml", "udrcfg.yaml", "uerouting.yaml", "upfcfg-a.yaml",
    "upfcfg-b.yaml", "nwdafcfg-a.yaml", "nwdafcfg-b.yaml", "nwdafcfg-c.yaml",
    "adrfcfg.yaml", "webuicfg.yaml", "pyanlf-a.yaml", "pyanlf-b.yaml", "pymtlf-a.yaml",
    "pymtlf-b.yaml", "pymtlf-c.yaml", "consumer.yaml", "manifest.yaml",
    "ueransim/gnb-a.yaml", "ueransim/gnb-b.yaml", "ueransim/ue1.yaml",
    "ueransim/ue2.yaml", "ueransim/ue3.yaml", "ueransim/ue4.yaml",
    "ueransim/ue5.yaml", "ueransim/ue6.yaml",
    "network/core.yaml", "network/path-a.yaml", "network/path-b.yaml",
    "compose.yaml",
    "subscriber/ue-subscribers.json", "subscriber/group-memberships.json",
}


def required_files(testbed):
    kind = deployment_kind(testbed)
    if kind == "protocol-hierarchical":
        required = {
            "nrfcfg.yaml", "adrfcfg.yaml", "manifest.yaml", "compose.yaml",
            "topology/protocol-hierarchical.yaml",
        }
        required.update(
            "network/{}.yaml".format(machine) for machine in testbed["machines"]
        )
        for definition in nwdaf_definitions(testbed):
            required.add("nwdafcfg-{}.yaml".format(definition["unit"][len("nwdaf-"):]))
            required.add(definition["backends"]["mtlf"] + ".yaml")
        return required
    if kind == "production-flat":
        return set(REQUIRED)
    required = {
        "nrfcfg.yaml", "amfcfg.yaml", "ausfcfg.yaml", "nssfcfg.yaml",
        "pcfcfg.yaml", "smfcfg.yaml", "udmcfg.yaml", "udrcfg.yaml",
        "uerouting.yaml", "upfcfg-a.yaml", "upfcfg-b.yaml", "adrfcfg.yaml",
        "webuicfg.yaml", "manifest.yaml", "compose.yaml",
        "subscriber/ue-subscribers.json", "subscriber/group-memberships.json",
        "network/core.yaml", "network/path-a.yaml", "network/path-b.yaml",
        "ueransim/gnb-a.yaml", "ueransim/gnb-b.yaml",
        "topology/{}.yaml".format(kind),
    }
    required.update("ueransim/ue{}.yaml".format(index) for index in range(1, 9))
    for definition in nwdaf_definitions(testbed):
        required.add("nwdafcfg-{}.yaml".format(definition["unit"][len("nwdaf-"):]))
        required.add(definition["backends"]["mtlf"] + ".yaml")
    return required


class Check:
    def __init__(self):
        self.errors = []

    def equal(self, label, actual, expected):
        if str(actual) != str(expected):
            self.errors.append("{}: expected {!r}, got {!r}".format(label, expected, actual))

    def true(self, label, condition):
        if not condition:
            self.errors.append(label)


def uri(host, port):
    return "http://{}:{}".format(host, port)


def is_lowercase_uuid4(value):
    if not isinstance(value, str) or value != value.lower():
        return False
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return (
        str(parsed) == value
        and parsed.version == 4
        and parsed.variant == uuid.RFC_4122
    )


def check_pymtlf_data_paths(check, name, config):
    root = Path("/var/lib/5g-nwdaf-infrastructure") / name
    paths = {
        "artifact root": config["storage"]["artifact_root"],
        "model state": config["model_state"]["directory"],
        "publication journal": config["publication"]["directory"],
        "FL workspace": config["federated_learning"]["workspace_root"],
    }
    for label, value in paths.items():
        path = Path(value)
        check.true("{} {} must be absolute".format(name, label), path.is_absolute())
        check.true(
            "{} {} must be inside {}".format(name, label, root),
            path != root and root in path.parents,
        )


def check_consumer_native(check, config_path):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "nwdaf-consumer" / "consumer.py"),
            "--config",
            str(config_path),
            "validate",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    detail = (result.stderr or result.stdout).strip()
    valid = result.returncode == 0
    check.true(
        "consumer native validation failed{}".format(
            ": " + detail if detail else ""
        ),
        valid,
    )
    return valid


def check_subscriber_fixtures(check, testbed, config_dir, identities):
    manifest = load_yaml(config_dir / "manifest.yaml")
    metadata = manifest.get("subscriberData", {})
    config_root = config_dir.resolve()
    paths = {}
    for name in ("subscribers", "groups"):
        relative = metadata.get(name)
        candidate = (config_root / relative).resolve() if isinstance(relative, str) else None
        valid = (
            candidate is not None
            and candidate != config_root
            and config_root in candidate.parents
            and candidate.is_file()
        )
        check.true("subscriberData.{} must select a file inside the config set".format(name), valid)
        if valid:
            paths[name] = candidate
    if len(paths) != 2:
        return
    subscriber_path = paths["subscribers"]
    group_path = paths["groups"]
    try:
        subscribers = json.loads(subscriber_path.read_text(encoding="utf-8"))
        groups = json.loads(group_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        check.true("cannot load config subscriber fixtures: {}".format(exc), False)
        return

    expected_supis = identities["supis"]
    records = subscribers.get("subscribers", [])
    fixture_supis = [record.get("supi") for record in records if isinstance(record, dict)]
    fixture_gpsis = [record.get("gpsi") for record in records if isinstance(record, dict)]
    defaults = subscribers.get("defaults", {})
    authentication = defaults.get("authentication", {})
    expected_plmn = identities["plmnDigits"]

    check.equal("subscriber fixture schema", subscribers.get("schemaVersion"), 1)
    check.equal("subscriber fixture PLMN", subscribers.get("servingPlmnId"), expected_plmn)
    check.equal("subscriber fixture SUPIs", fixture_supis, expected_supis)
    check.true(
        "subscriber fixture GPSIs must be unique",
        len(fixture_gpsis) == len(set(fixture_gpsis)) == len(expected_supis),
    )
    check.equal("subscriber fixture S-NSSAI", defaults.get("snssai"), testbed["mobileNetwork"]["snssai"])
    check.equal("subscriber fixture DNN", defaults.get("dnn"), testbed["mobileNetwork"]["dnn"])

    group_records = groups.get("groups", [])
    check.equal("group fixture schema", groups.get("schemaVersion"), 1)
    if deployment_kind(testbed) == "production-flat":
        check.equal("group fixture count", len(group_records), 1)
    if deployment_kind(testbed) == "production-flat" and len(group_records) == 1:
        check.equal(
            "group fixture ID", group_records[0].get("intGroupId"),
            identities["internalGroupId"],
        )
        check.equal(
            "group fixture SUPIs",
            [item.get("supi") for item in group_records[0].get("ueIdList", [])],
            expected_supis,
        )
    elif deployment_kind(testbed) != "production-flat":
        expected = expected_runtime_inventory(testbed)["dataOwners"]
        check.equal("group fixture count", len(group_records), 4)
        check.equal(
            "static ownership groups",
            [{
                "intGroupId": item.get("intGroupId"),
                "supis": [entry.get("supi") for entry in item.get("ueIdList", [])],
            } for item in group_records],
            [{"intGroupId": item["internalGroupId"], "supis": item["supis"]} for item in expected],
        )

    for index, expected_supi in enumerate(expected_supis, 1):
        ue = load_yaml(config_dir / "ueransim" / "ue{}.yaml".format(index))
        check.equal("UE{} fixture SUPI".format(index), ue.get("supi"), expected_supi)
        check.equal("UE{} fixture key".format(index), ue.get("key"), authentication.get("permanentKeyValue"))
        check.equal("UE{} fixture OP type".format(index), ue.get("opType"), "OPC")
        check.equal("UE{} fixture OPc".format(index), ue.get("op"), authentication.get("opcValue"))
        check.equal("UE{} fixture AMF".format(index), ue.get("amf"), authentication.get("authenticationManagementField"))
        check.equal("UE{} fixture DNN".format(index), ue["sessions"][0].get("apn"), defaults.get("dnn"))
        ue_snssai = dict(ue["sessions"][0].get("slice", {}))
        sd = str(ue_snssai.get("sd", ""))
        ue_snssai["sd"] = sd[2:] if sd.startswith("0x") else sd
        check.equal("UE{} fixture S-NSSAI".format(index), ue_snssai, defaults.get("snssai"))


def check_manifest_exact(check, testbed_path, testbed, config_dir, actual_files):
    try:
        manifest = load_runtime_manifest(config_dir)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        check.true("invalid runtime manifest: {}".format(exc), False)
        return None
    topology = manifest.get("topology", {})
    try:
        definition = testbed_path.relative_to(ROOT).as_posix()
    except ValueError:
        definition = str(testbed_path)
    check.equal("manifest topology name", topology.get("name"), testbed.get("name"))
    check.equal("manifest topology kind", topology.get("kind"), deployment_kind(testbed))
    check.equal("manifest topology definition", topology.get("definition"), definition)
    policy = manifest.get("renderOptions", {}).get("mlDevicePolicy")
    check.true("manifest renderOptions.mlDevicePolicy must be cpu or gpu", policy in ("cpu", "gpu"))
    selected = copy.deepcopy(testbed)
    if policy == "cpu":
        for name, service in selected["mlRuntime"]["services"].items():
            if name.startswith("pymtlf-") and service["device"] != "cpu":
                service["device"] = "cpu"
    try:
        expected = expected_runtime_inventory(selected)
    except (KeyError, TypeError, ValueError) as exc:
        check.true("cannot rebuild runtime inventory: {}".format(exc), False)
        return manifest
    check.equal("manifest exact runtime inventory", manifest.get("runtime"), expected)
    coordinator = expected["coordinatorContainer"]
    descriptor = load_yaml(config_dir / (coordinator + ".yaml")).get(
        "model_provision", {}
    ).get("seed_models", [{}])[0]
    check.equal(
        "manifest seed restoration identity",
        manifest.get("seedRestoration"),
        {
            "coordinatorContainer": coordinator,
            "canonicalSource": (
                "/opt/app/seed_models/image_classification/"
                + manifest.get("scenario", {}).get("workload", {}).get("dataset", "")
                if deployment_kind(testbed) == "protocol-hierarchical"
                else "/opt/app/seed_models/initial"
            ),
            "modelId": descriptor.get("model_id"),
            "modelInteroperability": descriptor.get("model_interoperability"),
            "artifactKey": descriptor.get("artifact_key"),
        },
    )
    generated = manifest.get("generated")
    if generated is not None:
        check.equal(
            "manifest generated files", generated.get("files"),
            sorted(path for path in actual_files if path != "manifest.yaml"),
        )
    return manifest


def check_static_pymtlf_native(check, config_dir, kind, coordinator_id, services):
    interpreter = ROOT / "ML" / "PyMTLF" / ".venv" / "bin" / "python"
    if not interpreter.is_file():
        check.true("PyMTLF project interpreter is unavailable", False)
        return
    program = r'''import hashlib
import sys
import tempfile
from pathlib import Path
from py_mtlf.config import load_settings
from py_mtlf.core.fl_topology import StaticFlatTopologyPlanner, StaticTopologyPlanner
from tools.import_seed_model import _build_bundle
root, kind, coordinator, *names = sys.argv[1:]
for name in names:
    load_settings(root + "/" + name + ".yaml")
settings = load_settings(root + "/" + names[0] + ".yaml")
path = settings.federated_learning.topology.config_file
if kind == "static-flat":
    StaticFlatTopologyPlanner.load(path).build(server_nf_instance_id=coordinator)
else:
    StaticTopologyPlanner.load(path).build(root_nf_instance_id=coordinator)
seed = settings.model_provision.seed_models[0]
with tempfile.TemporaryDirectory(prefix="static-seed-check-") as temporary:
    bundle = Path(temporary) / "seed.tar.gz"
    _build_bundle(Path("seed_models/initial"), bundle, model_id=seed.model_id,
                  event=seed.event, model_interoperability=seed.model_interoperability)
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == seed.artifact_key
'''
    result = subprocess.run(
        [str(interpreter), "-c", program, str(config_dir), kind, coordinator_id] + services,
        cwd=ROOT / "ML" / "PyMTLF", text=True, capture_output=True, check=False,
    )
    detail = (result.stderr or result.stdout).strip()
    check.true(
        "PyMTLF native settings/topology validation failed{}".format(
            ": " + detail if detail else ""
        ),
        result.returncode == 0,
    )


def check_static_specific(check, testbed, config_dir, runtime, scenario):
    kind = deployment_kind(testbed)
    definitions = nwdaf_definitions(testbed)
    expected_device = "cpu" if runtime["mlDevicePolicy"] == "cpu" else "cuda:0"
    owners = {item["position"]: item for item in testbed["analytics"]["dataOwners"]}
    for item in definitions:
        suffix = item["unit"][len("nwdaf-"):]
        native = load_yaml(config_dir / "nwdafcfg-{}.yaml".format(suffix))["configuration"]
        check.equal(item["unit"] + " identity", native.get("nfInstanceId"), item["nfInstanceId"])
        check.equal(item["unit"] + " SBI register", native.get("sbi", {}).get("registerIPv4"), item["sbi"]["address"])
        check.equal(item["unit"] + " SBI bind", native.get("sbi", {}).get("bindingIPv4"), item["sbi"]["address"])
        check.equal(item["unit"] + " SBI port", native.get("sbi", {}).get("port"), item["sbi"]["port"])
        for service, port in (("anlf", 8090), ("mtlf", 8091)):
            internal = native[service]["server"]
            check.equal(item["unit"] + " " + service + " address", internal.get("registerIPv4"), item["sbi"]["address"])
            check.equal(item["unit"] + " " + service + " port", internal.get("port"), port)
        capability = {
            "server": "FL_SERVER", "root": "FL_SERVER", "branch": "FL_SERVER_AND_CLIENT",
            "client": "FL_CLIENT", "leaf": "FL_CLIENT",
        }[item["role"]]
        check.equal(
            item["unit"] + " FL capability",
            native["nwdafInfo"]["mlAnalyticsList"][0].get("flCapabilityType"), capability,
        )
        expected_tracking_areas = None
        if kind == "static-flat" and item["role"] == "client":
            owner = owners[item["dataOwner"]]
            expected_tracking_areas = [{
                "plmnId": dict(testbed["mobileNetwork"]["plmn"]),
                "tac": testbed["paths"][owner["path"]]["tai"]["tac"],
            }]
        check.equal(
            item["unit"] + " registration tracking areas",
            native["nwdafInfo"]["mlAnalyticsList"][0].get("trackingAreaList"),
            expected_tracking_areas,
        )
        service = item["backends"]["mtlf"]
        definition = testbed["mlRuntime"]["services"][service]
        config = load_yaml(config_dir / (service + ".yaml"))
        fl = config["federated_learning"]
        check.equal(service + " port", config["server"]["port"], definition["publishedPort"])
        check.equal(
            service + " containing NWDAF", config["containing_nwdaf"]["internal_api_root"],
            uri(item["sbi"]["address"], 8091),
        )
        check_pymtlf_data_paths(check, service, config)
        if item["role"] in ("client", "leaf"):
            owner = owners[item["dataOwner"]]
            training_data = fl["client"]["training_data"]
            check.equal(
                service + " owned group",
                training_data["collection_profiles"][0]["target_ue"].get("intGroupIds"),
                [group_id_for_owner(testbed, owner)],
            )
            check.equal(service + " device", fl["client"]["training"].get("device"), expected_device)
        elif item["role"] == "branch":
            check.true(service + " must configure FL server", isinstance(fl.get("server"), dict))
            check.true(service + " must configure FL client", isinstance(fl.get("client"), dict))
            check.true(service + " must not own a dataset", "dataset" not in config)
            check.true(service + " must not own orchestration", "orchestration" not in fl)
        else:
            check.equal(service + " participant source", fl.get("orchestration", {}).get("participant_source"), "static")
            check.equal(service + " orchestration mode", fl.get("orchestration", {}).get("mode"), "flat" if kind == "static-flat" else "hierarchical")
            check.equal(service + " private trigger", fl.get("training_trigger", {}).get("private_api", {}).get("enabled"), True)
            check.equal(
                service + " fitting rounds", fl["server"]["round_count"],
                scenario["training"]["fittingRounds"],
            )
    topology = load_yaml(config_dir / "topology" / (kind + ".yaml"))
    if kind == "static-flat":
        check.equal(
            "flat topology clients",
            [entry.get("nf_instance_id") for entry in topology.get("clients", [])],
            [item["nfInstanceId"] for item in definitions if item["role"] == "client"],
        )
    else:
        leaves = {item["dataOwner"]: item["nfInstanceId"] for item in definitions if item["role"] == "leaf"}
        branches = [item for item in definitions if item["role"] == "branch"]
        check.equal(
            "hierarchical topology branches",
            [entry.get("nf_instance_id") for entry in topology.get("branches", [])],
            [item["nfInstanceId"] for item in branches],
        )
        check.equal(
            "hierarchical Branch-to-Leaf edges",
            [[leaf.get("nf_instance_id") for leaf in entry.get("leaves", [])] for entry in topology.get("branches", [])],
            [[leaves[position] for position in item["leaves"]] for item in branches],
        )
    coordinator = next(item for item in definitions if item["role"] in ("server", "root"))
    services = [runtime["coordinatorContainer"]] + [
        name for name in runtime["hostContainers"] if name != runtime["coordinatorContainer"]
    ]
    check_static_pymtlf_native(check, config_dir, kind, coordinator["nfInstanceId"], services)


def group_id_for_owner(testbed, owner):
    mobile = testbed["mobileNetwork"]
    return "{}-{}-{}-{}".format(
        mobile["internalGroup"]["serviceId"], mobile["plmn"]["mcc"],
        mobile["plmn"]["mnc"], owner["groupLocalId"],
    )


def check_protocol_native(check, config_dir, runtime, scenario):
    interpreter = ROOT / "ML" / "PyMTLF" / ".venv" / "bin" / "python"
    if not interpreter.is_file():
        check.true("PyMTLF project interpreter is unavailable", False)
        return
    services = runtime["hostContainers"]
    coordinator = runtime["coordinatorContainer"]
    root_id = next(
        item["nfInstanceId"] for item in runtime["nwdafs"] if item["role"] == "root"
    )
    program = r'''import sys
import tempfile
from pathlib import Path
from py_mtlf.config import load_settings
from py_mtlf.core.artifacts import ArtifactRepository
from py_mtlf.core.fl_topology import StaticTopologyPlanner
from py_mtlf.core.seed_import import build_seed_bundle
config_root, coordinator, root_id, dataset, *names = sys.argv[1:]
settings = {name: load_settings(Path(config_root) / (name + ".yaml")) for name in names}
root = settings[coordinator]
assignment = StaticTopologyPlanner.load(
    root.federated_learning.topology.config_file
).build(root_nf_instance_id=root_id)
assert len(assignment.branch_groups) == 3
seed = root.model_provision.seed_models[0]
with tempfile.TemporaryDirectory(prefix="protocol-seed-check-") as temporary:
    temporary = Path(temporary)
    bundle = temporary / "seed.tar.gz"
    build_seed_bundle(
        Path("seed_models/image_classification") / dataset,
        bundle,
        model_id=seed.model_id,
        event=seed.event,
        model_interoperability=seed.model_interoperability,
    )
    repository = ArtifactRepository(temporary / "artifacts", root.artifact)
    repository.open()
    metadata = repository.publish(bundle)
    assert metadata.key == seed.artifact_key
'''
    result = subprocess.run(
        [
            str(interpreter), "-c", program, str(config_dir), coordinator,
            root_id, scenario["workload"]["dataset"], *services,
        ],
        cwd=ROOT / "ML" / "PyMTLF", text=True, capture_output=True, check=False,
    )
    detail = (result.stderr or result.stdout).strip()
    check.true(
        "PyMTLF native settings/topology/seed validation failed{}".format(
            ": " + detail if detail else ""
        ),
        result.returncode == 0,
    )


def check_protocol(testbed_path, testbed, config_dir, check):
    check.equal("schemaVersion", testbed.get("schemaVersion"), 1)
    check.equal(
        "canonical machine set", list(testbed.get("machines", {})),
        ["core", "path-a", "path-b", "path-c"],
    )
    check.equal(
        "placement groups", list(testbed.get("placement", {})),
        ["core", "path-a", "path-b", "path-c", "host-containers"],
    )
    check.true("TLS must remain disabled", not testbed.get("security", {}).get("tls"))
    check.true("OAuth must remain disabled", not testbed.get("security", {}).get("oauth"))
    try:
        runtime = expected_runtime_inventory(testbed)
    except (KeyError, TypeError, ValueError) as exc:
        check.true("invalid TESTBED runtime inventory: {}".format(exc), False)
        return finish(check, testbed_path, config_dir)
    check.equal("Guest machine count", len(runtime["guestMachines"]), 4)
    check.equal("Guest NWDAF count", len(runtime["nwdafs"]), 11)
    check.equal("Host PyMTLF count", len(runtime["hostContainers"]), 11)
    check.equal("ML volume count", len(runtime["mlVolumes"]), 11)

    required = required_files(testbed)
    missing = sorted(name for name in required if not (config_dir / name).is_file())
    check.true("missing config files: {}".format(", ".join(missing)), not missing)
    if missing:
        return finish(check, testbed_path, config_dir)
    actual_files = {
        path.relative_to(config_dir).as_posix()
        for path in config_dir.rglob("*") if path.is_file()
    }
    check.true(
        "generated config file set differs: missing={} extra={}".format(
            sorted(required - actual_files), sorted(actual_files - required)
        ),
        actual_files == required,
    )
    try:
        _scenario_path, scenario = resolve_config_scenario(config_dir)
        image_scenario_contract(scenario)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        check.true("invalid image scenario contract: {}".format(exc), False)
        return finish(check, testbed_path, config_dir)
    check.equal("scenario schema", scenario.get("schemaVersion"), SCENARIO_SCHEMA)
    check.equal("scenario accepted rounds", scenario["training"]["acceptedRounds"], 2)

    manifest = check_manifest_exact(check, testbed_path, testbed, config_dir, actual_files)
    if manifest is None:
        return finish(check, testbed_path, config_dir)
    check.equal("manifest scenario name", manifest.get("scenario", {}).get("name"), scenario["name"])
    check.equal("manifest scenario workload", manifest.get("scenario", {}).get("workload"), scenario["workload"])
    dataset_root = ROOT / ".generated" / "image-datasets" / scenario["name"]
    expected_datasets = {
        "root": str(dataset_root),
        "validation": str(dataset_root / "validation.npz"),
        "heldOut": str(dataset_root / "held-out.npz"),
        "leaves": {
            item["unit"]: str(dataset_root / "leaves" / (item["unit"] + ".npz"))
            for item in nwdaf_definitions(testbed) if item["role"] == "leaf"
        },
    }
    check.equal("manifest image datasets", manifest.get("datasets"), expected_datasets)
    check.equal("manifest PseudoDriver profiles", manifest.get("constraints", {}).get("pseudoDriverProfiles"), {})
    check.equal(
        "native protocol topology",
        load_yaml(config_dir / "topology" / "protocol-hierarchical.yaml"),
        protocol_topology(testbed),
    )
    for machine, expected in guest_network_configs(testbed, include_consumer=False).items():
        check.equal(
            "{} network aliases".format(machine),
            load_yaml(config_dir / "network" / (machine + ".yaml")), expected,
        )

    nrf_uri = uri(
        testbed["coreServices"]["nrf"]["sbi"]["address"],
        testbed["coreServices"]["nrf"]["sbi"]["port"],
    )
    ml_services = testbed["mlRuntime"]["services"]
    for definition in nwdaf_definitions(testbed):
        unit = definition["unit"]
        native = load_yaml(
            config_dir / "nwdafcfg-{}.yaml".format(unit[len("nwdaf-"):])
        )["configuration"]
        check.equal(unit + " ID", native.get("nfInstanceId"), definition["nfInstanceId"])
        check.equal(unit + " SBI address", native.get("sbi", {}).get("registerIPv4"), definition["sbi"]["address"])
        check.equal(unit + " SBI port", native.get("sbi", {}).get("port"), definition["sbi"]["port"])
        check.equal(unit + " NRF", native.get("nrfUri"), nrf_uri)
        check.equal(unit + " AnLF disabled", native.get("anlfBackend"), {"enabled": False})
        backend_name = definition["backends"]["mtlf"]
        check.equal(
            unit + " MTLF backend", native.get("mtlfBackend", {}).get("endpoint"),
            uri(testbed["mlRuntime"]["advertisedAddress"], ml_services[backend_name]["publishedPort"]),
        )
        capability = native["nwdafInfo"]["mlAnalyticsList"][0]
        check.equal(
            unit + " FL capability", capability.get("flCapabilityType"),
            {"root": "FL_SERVER", "branch": "FL_SERVER_AND_CLIENT", "leaf": "FL_CLIENT"}[definition["role"]],
        )
        check.equal(unit + " analytics event", capability.get("mlAnalyticsIds"), [scenario["workload"]["event"]])
        if definition["role"] == "leaf":
            check.equal(unit + " TAI", capability.get("trackingAreaList", [None])[0].get("tac"), definition["tai"])
        pymtlf = load_yaml(config_dir / (backend_name + ".yaml"))
        check.equal(backend_name + " port", pymtlf["server"]["port"], ml_services[backend_name]["containerPort"])
        check.equal(backend_name + " containing NWDAF", pymtlf["containing_nwdaf"]["internal_api_root"], uri(definition["sbi"]["address"], 8091))
        check_pymtlf_data_paths(check, backend_name, pymtlf)
        client = pymtlf.get("federated_learning", {}).get("client")
        if definition["role"] == "leaf":
            check.equal(backend_name + " dataset", client.get("training_data", {}).get("dataset"), scenario["workload"]["dataset"])
            check.equal(backend_name + " shard", client.get("training_data", {}).get("shard_path"), "/data/train.npz")
        elif definition["role"] == "branch":
            check.true(backend_name + " must not own a local shard", not client.get("training_data", {}).get("shard_path"))

    check_protocol_native(check, config_dir, runtime, scenario)
    interpreter = ROOT / "ML" / "PyMTLF" / ".venv" / "bin" / "python"
    dataset_result = subprocess.run(
        [
            str(interpreter), str(ROOT / "scripts" / "host" / "image_dataset.py"),
            "--testbed", str(testbed_path), "--config-dir", str(config_dir), "check",
        ],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    detail = (dataset_result.stderr or dataset_result.stdout).strip()
    check.true(
        "PyMTLF native image dataset validation failed{}".format(
            ": " + detail if detail else ""
        ),
        dataset_result.returncode == 0,
    )
    return finish(check, testbed_path, config_dir)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--testbed", required=True)
    parser.add_argument("--config-dir")
    args = parser.parse_args()

    testbed_path = resolve_path(args.testbed)
    testbed = load_yaml(testbed_path)
    config_dir = resolve_config_dir(testbed, args.config_dir)
    check = Check()

    if deployment_kind(testbed) == "protocol-hierarchical":
        return check_protocol(testbed_path, testbed, config_dir, check)

    try:
        identities = resolve_mobile_identities(testbed)
    except (KeyError, TypeError, ValueError) as exc:
        check.true("invalid mobile identity contract: {}".format(exc), False)
        return finish(check, testbed_path, config_dir)
    plmn = identities["plmn"]
    group_id = identities["internalGroupId"]
    path_supis = identities["pathSupis"]
    expected_supis = identities["supis"]

    check.true(
        "mobileNetwork.internalGroupId is redundant; use mobileNetwork.internalGroup",
        "internalGroupId" not in testbed.get("mobileNetwork", {}),
    )
    check.true(
        "consumer.target.internalGroupId is redundant; it is derived from mobileNetwork",
        "internalGroupId" not in testbed.get("consumer", {}).get("target", {}),
    )
    for path_name in ("a", "b"):
        path = testbed.get("paths", {}).get(path_name, {})
        check.true(
            "paths.{}.tai.plmn is redundant; it is derived from mobileNetwork.plmn".format(
                path_name
            ),
            "plmn" not in path.get("tai", {}),
        )
        check.true(
            "paths.{}.ues is redundant; use subscriberNumbers".format(path_name),
            "ues" not in path,
        )

    check.equal("schemaVersion", testbed.get("schemaVersion"), 1)
    check.equal("machine set", sorted(testbed.get("machines", {})), ["core", "path-a", "path-b"])
    check.true("TLS/certificate support must remain disabled", not testbed.get("security", {}).get("tls"))
    host_safety = testbed.get("hostSafety", {})
    for key in ("reserveMemoryMiB", "minimumFreeSwapMiB", "minimumFreeStorageGiB"):
        check.true("hostSafety.{} must be a positive integer".format(key), isinstance(host_safety.get(key), int) and host_safety[key] > 0)
    check.true(
        "hostSafety.swapPolicy must be 'warn' or 'require'",
        host_safety.get("swapPolicy") in ("warn", "require"),
    )
    delivery = testbed.get("operations", {}).get("pyanlfDelivery", {})
    delivery_limits = {
        "analyticsReportTimeoutSeconds": 300,
        "analyticsWorkerStopTimeoutSeconds": 3600,
        "accuracyReportTimeoutSeconds": 300,
        "runtimeCompletionTimeoutSeconds": 300,
    }
    for field, maximum in delivery_limits.items():
        value = delivery.get(field)
        check.true(
            "operations.pyanlfDelivery.{} must be an integer from 1 to {}".format(
                field, maximum
            ),
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 < value <= maximum,
        )
    check.true(
        "PyAnLF analytics worker stop timeout must cover request timeout",
        isinstance(delivery.get("analyticsWorkerStopTimeoutSeconds"), int)
        and isinstance(delivery.get("analyticsReportTimeoutSeconds"), int)
        and delivery["analyticsWorkerStopTimeoutSeconds"]
        >= delivery["analyticsReportTimeoutSeconds"],
    )

    adrf_definition = testbed.get("coreServices", {}).get("adrf", {})
    adrf_instance_id = adrf_definition.get("nfInstanceId")
    check.true(
        "coreServices.adrf.nfInstanceId must be a lowercase UUIDv4",
        is_lowercase_uuid4(adrf_instance_id),
    )
    analytics_instance_ids = [
        definition.get("nfInstanceId")
        for definition in testbed.get("analytics", {}).values()
        if isinstance(definition, dict)
    ]
    check.true(
        "ADRF NF instance ID must be unique across the testbed",
        adrf_instance_id not in analytics_instance_ids,
    )
    adrf_database = adrf_definition.get("mongodb", {}).get("database")
    check.true(
        "coreServices.adrf.mongodb.database must be non-empty",
        isinstance(adrf_database, str) and bool(adrf_database),
    )
    storage_value = adrf_definition.get("modelStorage", {}).get("localDirectory")
    storage_path = Path(storage_value) if isinstance(storage_value, str) else Path(".")
    runtime_root = Path("/var/lib/5g-nwdaf-infrastructure")
    check.true("ADRF model storage must be absolute", storage_path.is_absolute())
    check.true(
        "ADRF model storage must remain inside {}".format(runtime_root),
        storage_path != runtime_root and runtime_root in storage_path.parents,
    )

    try:
        selected_runtime = expected_runtime_inventory(testbed)
    except (KeyError, TypeError, ValueError) as exc:
        check.true("invalid TESTBED runtime inventory: {}".format(exc), False)
        return finish(check, testbed_path, config_dir)
    check.equal(
        "placement groups", sorted(testbed.get("placement", {})),
        ["core", "host-containers", "path-a", "path-b"],
    )

    ml_runtime = testbed.get("mlRuntime", {})
    check.equal("ML runtime engine", ml_runtime.get("engine"), "docker-compose-v2")
    check.equal("ML runtime network", ml_runtime.get("networkMode"), "bridge")
    expected_ml_names = sorted(selected_runtime["hostContainers"])
    check.equal("ML runtime services", sorted(ml_runtime.get("services", {})), expected_ml_names)
    advertised_address = ml_runtime.get("advertisedAddress")
    bind_address = resolve_ml_bind_address(testbed)
    sbi_network = ipaddress.ip_network(testbed["networks"]["sbi"]["cidr"])
    try:
        advertised_inside = ipaddress.ip_address(advertised_address) in sbi_network
    except (TypeError, ValueError):
        advertised_inside = False
    check.true(
        "ML advertised address {} outside SBI network".format(advertised_address),
        advertised_inside,
    )
    try:
        bind_ip = ipaddress.ip_address(bind_address)
        valid_bind = not bind_ip.is_unspecified and not bind_ip.is_multicast
    except (TypeError, ValueError):
        valid_bind = False
    check.true("ML bind address {} is invalid".format(bind_address), valid_bind)

    published_endpoints = []
    for name, service in ml_runtime.get("services", {}).items():
        check.true("{} image must be pyanlf or pymtlf".format(name), service.get("image") in ("pyanlf", "pymtlf"))
        for field in ("publishedPort", "containerPort"):
            check.true("{}.{} must be a valid port".format(name, field), isinstance(service.get(field), int) and 0 < service[field] < 65536)
        check.true("{}.device must be cpu or cuda:0".format(name), service.get("device") in ("cpu", "cuda:0"))
        published_endpoints.append((advertised_address, service.get("publishedPort")))
    check.true("duplicate ML published endpoint", len(published_endpoints) == len(set(published_endpoints)))

    selected_required = required_files(testbed)
    missing = sorted(name for name in selected_required if not (config_dir / name).is_file())
    check.true("missing config files: {}".format(", ".join(missing)), not missing)
    if missing:
        return finish(check, testbed_path, config_dir)
    actual_files = {
        path.relative_to(config_dir).as_posix()
        for path in config_dir.rglob("*") if path.is_file()
    }
    check.true(
        "generated config file set differs: missing={} extra={}".format(
            sorted(selected_required - actual_files), sorted(actual_files - selected_required)
        ),
        actual_files == selected_required,
    )

    manifest = load_yaml(config_dir / "manifest.yaml")
    ml_device_policy = manifest.get("runtime", {}).get("mlDevicePolicy")
    check.true(
        "manifest runtime.mlDevicePolicy must be cpu or gpu",
        ml_device_policy in ("cpu", "gpu"),
    )
    webconsole_enabled = manifest.get("optionalServices", {}).get(
        "webconsole", {}
    ).get("enabled")
    check.true(
        "manifest optionalServices.webconsole.enabled must be boolean",
        isinstance(webconsole_enabled, bool),
    )

    try:
        scenario_path, scenario = resolve_config_scenario(config_dir)
        profile_paths = resolve_scenario_profile_paths(scenario_path, scenario)
        profile_sources = repository_relative_paths(profile_paths)
    except (KeyError, OSError, ValueError) as exc:
        check.true("invalid scenario contract: {}".format(exc), False)
        return finish(check, testbed_path, config_dir)
    check.equal("scenario schema", scenario.get("schemaVersion"), SCENARIO_SCHEMA)
    check.true(
        "scenario kind must be business-acceptance or bounded-smoke",
        scenario.get("kind") in ("business-acceptance", "bounded-smoke"),
    )
    check.true(
        "scenario warmStartMode must identify its data responsibility",
        scenario.get("warmStartMode") in ("inference-only", "inference-and-training"),
    )
    check.equal("scenario traffic paths", sorted(scenario.get("trafficProfiles", {})), ["a", "b"])
    for path_name, profile_path in profile_paths.items():
        check.true(
            "scenario Path {} traffic profile is available".format(path_name),
            profile_path.is_file(),
        )
    for section, fields in {
        "monitoring": ("reportPeriodSeconds", "minimumReferenceReports", "decisionWindowSize", "requiredHits"),
        "training": (
            "minimumSamples", "localEpochs", "fittingRounds",
            "preparationDataWindowSeconds", "closureBudgetSeconds",
        ),
    }.items():
        values = scenario.get(section, {})
        for field in fields:
            value = values.get(field)
            check.true(
                "scenario {}.{} must be a positive integer".format(section, field),
                isinstance(value, int) and not isinstance(value, bool) and value > 0,
            )
    check.true(
        "scenario samplingIntervalSeconds must be a positive integer",
        isinstance(scenario.get("samplingIntervalSeconds"), int)
        and not isinstance(scenario.get("samplingIntervalSeconds"), bool)
        and scenario["samplingIntervalSeconds"] > 0,
    )
    check.equal(
        "scenario performance gate",
        scenario.get("training", {}).get("enforcePerformanceGate"),
        False,
    )
    if check.errors:
        return finish(check, testbed_path, config_dir)
    sampling = scenario["samplingIntervalSeconds"]
    monitoring = scenario["monitoring"]
    training = scenario["training"]
    seed = json.loads(
        (ROOT / "ML" / "PyMTLF" / "seed_models" / "initial" / "config.json").read_text(
            encoding="utf-8"
        )
    )
    seed_model = seed["model"]
    seed_inference = seed["inference"]

    check_subscriber_fixtures(check, testbed, config_dir, identities)
    dataset_diagnostics = []
    try:
        dataset_spec = resolve_dataset_spec(
            testbed, config_dir, diagnostics=dataset_diagnostics
        )
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        check.true("invalid PseudoDriver dataset contract: {}".format(exc), False)
        dataset_spec = None
    for diagnostic in dataset_diagnostics:
        check.true("PseudoDriver dataset contract: {}".format(diagnostic), False)

    all_addresses = []
    networks = testbed["networks"]
    for machine_name, machine in testbed["machines"].items():
        for network_name, address in machine["interfaces"].items():
            check.true(
                "{}.{} outside {}".format(machine_name, address, networks[network_name]["cidr"]),
                ipaddress.ip_address(address) in ipaddress.ip_network(networks[network_name]["cidr"]),
            )
            all_addresses.append(("machine {} {}".format(machine_name, network_name), address))

    expected_network_configs = guest_network_configs(
        testbed, include_consumer=deployment_kind(testbed) == "production-flat"
    )
    for machine_name, expected in expected_network_configs.items():
        actual = load_yaml(config_dir / "network" / (machine_name + ".yaml"))
        check.equal("{} network aliases".format(machine_name), actual, expected)
        for alias in expected["aliases"]:
            all_addresses.append((
                "{} {} {}".format(machine_name, alias["owner"], alias["endpoint"]),
                alias["address"],
            ))

    core_files = {
        "nrf": "nrfcfg.yaml", "nssf": "nssfcfg.yaml", "udr": "udrcfg.yaml",
        "udm": "udmcfg.yaml", "ausf": "ausfcfg.yaml", "pcf": "pcfcfg.yaml",
        "amf": "amfcfg.yaml", "smf": "smfcfg.yaml", "adrf": "adrfcfg.yaml",
    }
    nrf = testbed["coreServices"]["nrf"]["sbi"]
    nrf_uri = uri(nrf["address"], nrf["port"])
    mongo = testbed["coreServices"]["mongodb"]
    mongo_uri = "mongodb://{}:{}".format(
        mongo["endpoint"]["address"], mongo["endpoint"]["port"]
    )
    webconsole_definition = testbed.get("optionalServices", {}).get(
        "webconsole", {}
    )
    webconsole_endpoint = webconsole_definition.get("endpoint", {})
    check.equal("WebConsole machine", webconsole_definition.get("machine"), "core")
    check.equal(
        "WebConsole endpoint network",
        webconsole_endpoint.get("network"),
        "management",
    )
    check.equal(
        "WebConsole management address",
        webconsole_endpoint.get("address"),
        testbed["machines"]["core"]["interfaces"]["management"],
    )
    check.true(
        "WebConsole port must be valid",
        isinstance(webconsole_endpoint.get("port"), int)
        and not isinstance(webconsole_endpoint.get("port"), bool)
        and 0 < webconsole_endpoint["port"] < 65536,
    )
    webui = load_yaml(config_dir / "webuicfg.yaml")
    webui_config = webui.get("configuration", {})
    check.equal("WebConsole MongoDB URL", webui_config.get("mongodb", {}).get("url"), mongo_uri)
    check.equal(
        "WebConsole MongoDB database",
        webui_config.get("mongodb", {}).get("name"),
        mongo["database"],
    )
    check.equal("WebConsole NRF URI", webui_config.get("nrfUri"), nrf_uri)
    check.equal(
        "WebConsole HTTP endpoint",
        webui_config.get("webServer"),
        {
            "scheme": "http",
            "ipv4Address": webconsole_endpoint.get("address"),
            "port": webconsole_endpoint.get("port"),
        },
    )
    check.equal(
        "WebConsole billing compatibility settings",
        webui_config.get("billingServer"),
        {
            "enable": True,
            "hostIPv4": "127.0.0.1",
            "listenPort": 2121,
            "portRange": {"start": 2123, "end": 2130},
            "basePath": "/tmp/webconsole",
            "port": 2122,
        },
    )
    for name, filename in core_files.items():
        cfg = load_yaml(config_dir / filename)
        endpoint = testbed["coreServices"][name]["sbi"]
        sbi = get_path(cfg, ["configuration", "sbi"])
        check.equal(filename + " register", sbi["registerIPv4"], endpoint["address"])
        check.equal(filename + " bind", sbi["bindingIPv4"], endpoint["address"])
        check.equal(filename + " port", sbi["port"], endpoint["port"])
        if name != "nrf":
            check.equal(filename + " NRF", cfg["configuration"]["nrfUri"], nrf_uri)

    nrf_config = load_yaml(config_dir / "nrfcfg.yaml")["configuration"]
    check.equal("NRF MongoDB URL", nrf_config["MongoDBUrl"], mongo_uri)
    check.equal("NRF MongoDB database", nrf_config["MongoDBName"], mongo["database"])
    check.equal("NRF default PLMN", nrf_config.get("DefaultPlmnId"), plmn)
    for filename in ("udrcfg.yaml", "pcfcfg.yaml"):
        mongodb = load_yaml(config_dir / filename)["configuration"]["mongodb"]
        check.equal(filename + " MongoDB URL", mongodb["url"], mongo_uri)
        check.equal(filename + " MongoDB database", mongodb["name"], mongo["database"])
    adrf_mongodb = load_yaml(config_dir / "adrfcfg.yaml")["configuration"]["mongodb"]
    check.equal("ADRF MongoDB URL", adrf_mongodb["url"], mongo_uri)
    check.equal("ADRF MongoDB database", adrf_mongodb["name"], adrf_database)
    adrf = load_yaml(config_dir / "adrfcfg.yaml")["configuration"]
    check.equal(
        "ADRF NF instance ID",
        adrf.get("nfInstanceId"),
        adrf_instance_id,
    )
    check.equal("ADRF name", adrf.get("adrfName"), "ADRF")
    check.equal(
        "ADRF service names",
        adrf.get("serviceNameList"),
        ["nadrf-mlmodelmanagement", "nadrf-datamanagement"],
    )
    check.equal(
        "ADRF PLMN and S-NSSAI",
        adrf.get("plmnSupportList"),
        [{
            "plmnId": dict(plmn),
            "snssaiList": [dict(testbed["mobileNetwork"]["snssai"])],
        }],
    )
    check.equal("ADRF locality", adrf.get("locality"), "dual-tai")
    check.true(
        "ADRF heartbeat interval must be positive",
        isinstance(adrf.get("heartbeatInterval"), int)
        and adrf["heartbeatInterval"] > 0,
    )
    check.equal(
        "ADRF retrieval contract",
        adrf.get("retrieval"),
        {
            "corrIdBatchSize": 100,
            "oneIdPerFetch": True,
            "snapshot": {"enabled": True},
        },
    )
    check.equal(
        "ADRF model storage",
        adrf.get("mlModelStorage", {}).get("localDirectory"),
        storage_value,
    )

    udm = load_yaml(config_dir / "udmcfg.yaml")["configuration"]
    if deployment_kind(testbed) == "production-flat":
        expected_group_ranges = [{"start": group_id, "end": group_id}]
    else:
        group_ids = [item["internalGroupId"] for item in selected_runtime["dataOwners"]]
        expected_group_ranges = [{"start": min(group_ids), "end": max(group_ids)}]
    check.equal(
        "UDM Internal Group range",
        udm.get("internalGroupIdentifiersRanges"),
        expected_group_ranges,
    )

    ausf = load_yaml(config_dir / "ausfcfg.yaml")["configuration"]
    check.equal("AUSF supported PLMN", ausf.get("plmnSupportList"), [plmn])

    snssai = testbed["mobileNetwork"]["snssai"]
    nssf = load_yaml(config_dir / "nssfcfg.yaml")["configuration"]
    check.equal("NSSF supported PLMN", nssf.get("supportedPlmnList"), [plmn])
    check.equal(
        "NSSF supported S-NSSAI in PLMN",
        nssf.get("supportedNssaiInPlmnList"),
        [{"plmnId": dict(plmn), "supportedSnssaiList": [dict(snssai)]}],
    )
    check.equal(
        "NSSF NSI S-NSSAI",
        [item.get("snssai") for item in nssf.get("nsiList", [])],
        [snssai],
    )
    expected_tais = [
        {"plmnId": dict(plmn), "tac": testbed["paths"][name]["tai"]["tac"]}
        for name in ("a", "b")
    ]
    check.equal(
        "NSSF TAIs",
        nssf.get("taList"),
        [
            {
                "tai": tai,
                "accessType": "3GPP_ACCESS",
                "supportedSnssaiList": [dict(snssai)],
            }
            for tai in expected_tais
        ],
    )

    amf = load_yaml(config_dir / "amfcfg.yaml")["configuration"]
    check.equal("AMF N2", amf["ngapIpList"], [testbed["coreServices"]["amf"]["n2"]["address"]])
    expected_tacs = [testbed["paths"][name]["tai"]["tac"] for name in ("a", "b")]
    check.equal("AMF TAIs", amf.get("supportTaiList"), expected_tais)
    check.equal("AMF served GUAMI PLMN", amf["servedGuamiList"][0].get("plmnId"), plmn)
    check.equal(
        "AMF supported PLMN and S-NSSAI",
        amf.get("plmnSupportList"),
        [{"plmnId": dict(plmn), "snssaiList": [dict(snssai)]}],
    )

    smf = load_yaml(config_dir / "smfcfg.yaml")["configuration"]
    check.equal("SMF N4", smf["pfcp"]["listenAddr"], testbed["coreServices"]["smf"]["n4"]["address"])
    check.equal("SMF NRF registration", smf.get("nrfRegistrationEnabled"), True)
    check.equal("SMF URR period", smf.get("urrPeriod"), sampling)
    check.equal("SMF PLMN list", smf.get("plmnList"), [plmn])
    ueransim_snssai = dict(snssai)
    sd = str(ueransim_snssai["sd"])
    ueransim_snssai["sd"] = sd if sd.startswith("0x") else "0x" + sd
    for name in ("a", "b"):
        path = testbed["paths"][name]
        upf = load_yaml(config_dir / ("upfcfg-{}.yaml".format(name)))
        node = smf["userplaneInformation"]["upNodes"]["UPF-{}".format(name.upper())]
        check.equal("UPF {} N4".format(name), upf["pfcp"]["addr"], path["upf"]["n4"]["address"])
        check.equal("UPF {} node ID".format(name), upf["pfcp"]["nodeID"], path["upf"]["n4"]["address"])
        check.equal("SMF UPF {} N4".format(name), node["nodeID"], path["upf"]["n4"]["address"])
        check.equal("SMF UPF {} address".format(name), node["addr"], path["upf"]["n4"]["address"])
        check.equal("UPF {} N3".format(name), upf["gtpu"]["ifList"][0]["addr"], path["upf"]["n3"]["address"])
        check.equal("UPF {} GTP interface".format(name), upf["gtpu"]["ifList"][0].get("ifname"), path["upf"]["gtpInterface"])
        check.equal("UPF {} pool".format(name), upf["dnnList"][0]["cidr"], path["upf"]["uePool"])
        check.equal(
            "SMF UPF {} TAI".format(name),
            node.get("tais"),
            [{"plmnId": dict(plmn), "tac": path["tai"]["tac"]}],
        )
        check.equal("SMF UPF {} EES".format(name), node["nupfEeApiRoot"], uri(path["upf"]["eventExposure"]["address"], path["upf"]["eventExposure"]["port"]))
        check.true("UPF {} pseudo driver disabled".format(name), upf["ees"]["enabled"])
        check.equal(
            "UPF {} Event Exposure listener".format(name),
            upf["ees"]["listenAddr"],
            "{}:{}".format(
                path["upf"]["eventExposure"]["address"],
                path["upf"]["eventExposure"]["port"],
            ),
        )
        check.equal("UPF {} reporting period".format(name), upf["ees"]["periodSec"], sampling)
        check.equal(
            "UPF {} PseudoDriver directory".format(name),
            upf["ees"]["parquetDir"],
            path["upf"]["pseudoDriver"]["dataset"]["guestDirectory"],
        )

        pseudo = path["upf"]["pseudoDriver"]
        dataset = pseudo.get("dataset", {})
        check.equal("UPF {} dataset artifact".format(name), dataset.get("file"), "traffic.parquet")
        check.true(
            "UPF {} replay headroom must be at least 512 MiB".format(name),
            isinstance(dataset.get("minimumReplayHeadroomMiB"), int) and dataset["minimumReplayHeadroomMiB"] >= 512,
        )

        gnb = load_yaml(config_dir / "ueransim" / ("gnb-{}.yaml".format(name)))
        check.equal("gNB {} MCC".format(name), gnb.get("mcc"), plmn["mcc"])
        check.equal("gNB {} MNC".format(name), gnb.get("mnc"), plmn["mnc"])
        check.equal("gNB {} TAC".format(name), str(gnb["tac"]).zfill(6), path["tai"]["tac"])
        check.equal("gNB {} N2".format(name), gnb["ngapIp"], path["gnb"]["n2"]["address"])
        check.equal("gNB {} N3".format(name), gnb["gtpIp"], path["gnb"]["n3"]["address"])
        check.equal("gNB {} S-NSSAI".format(name), gnb["slices"], [ueransim_snssai])
        check.equal("gNB {} cell access type".format(name), gnb.get("cellAccessType"), "nr")

    uerouting = load_yaml(config_dir / "uerouting.yaml")
    for path_name in ("a", "b"):
        check.equal(
            "UE routing Path {} members".format(path_name),
            uerouting["ueRoutingInfo"]["path-" + path_name].get("members"),
            path_supis[path_name],
        )

    for index, expected_supi in enumerate(expected_supis, 1):
        ue = load_yaml(config_dir / "ueransim" / ("ue{}.yaml".format(index)))
        path_name = "a" if index <= len(path_supis["a"]) else "b"
        check.equal("UE{} SUPI".format(index), ue["supi"], expected_supi)
        check.equal("UE{} MCC".format(index), ue.get("mcc"), plmn["mcc"])
        check.equal("UE{} MNC".format(index), ue.get("mnc"), plmn["mnc"])
        check.equal("UE{} gNB".format(index), ue["gnbSearchList"], [testbed["paths"][path_name]["gnb"]["n2"]["address"]])
        check.equal("UE{} session S-NSSAI".format(index), ue["sessions"][0]["slice"], ueransim_snssai)
        check.equal("UE{} configured S-NSSAI".format(index), ue["configured-nssai"], [ueransim_snssai])
        check.equal("UE{} default S-NSSAI".format(index), ue["default-nssai"], [ueransim_snssai])
        check.equal("UE{} network namespace".format(index), ue.get("useNamespace"), False)
        check.equal(
            "UE{} UAC access identities".format(index), ue.get("uacAic"),
            {"mps": False, "mcs": False},
        )
        check.equal(
            "UE{} UAC access classes".format(index), ue.get("uacAcc"),
            {
                "normalClass": 0, "class11": False, "class12": False,
                "class13": False, "class14": False, "class15": False,
            },
        )

    manifest = check_manifest_exact(check, testbed_path, testbed, config_dir, actual_files)
    if manifest is None:
        return finish(check, testbed_path, config_dir)
    if deployment_kind(testbed) != "production-flat":
        check_static_specific(check, testbed, config_dir, manifest["runtime"], scenario)
        return finish(check, testbed_path, config_dir)

    nwdaf_native = {}
    for name in ("a", "b", "c"):
        native = load_yaml(config_dir / ("nwdafcfg-{}.yaml".format(name)))["configuration"]
        nwdaf_native[name] = native
        expected = testbed["analytics"]["nwdaf-{}".format(name)]
        check.equal("NWDAF {} ID".format(name), native["nfInstanceId"], expected["nfInstanceId"])
        check.equal("NWDAF {} register".format(name), native["sbi"]["registerIPv4"], expected["sbi"]["address"])
        check.equal("NWDAF {} bind".format(name), native["sbi"]["bindingIPv4"], expected["sbi"]["address"])
        check.equal("NWDAF {} port".format(name), native["sbi"]["port"], expected["sbi"]["port"])
        check.equal("NWDAF {} NRF".format(name), native["nrfUri"], nrf_uri)
        for service, port in (("anlf", 8090), ("mtlf", 8091)):
            internal = native[service]["server"]
            check.equal(
                "NWDAF {} {} register".format(name, service),
                internal["registerIPv4"], expected["sbi"]["address"],
            )
            check.equal(
                "NWDAF {} {} bind".format(name, service),
                internal["bindingIPv4"], expected["sbi"]["address"],
            )
            check.equal(
                "NWDAF {} {} port".format(name, service), internal["port"], port,
            )
        if name in ("a", "b"):
            check.equal(
                "NWDAF {} tracking TAI".format(name),
                native["nwdafInfo"]["mlAnalyticsList"][0].get(
                    "trackingAreaList"
                ),
                [{"plmnId": dict(plmn), "tac": expected["tai"]}],
            )
            check.equal(
                "NWDAF {} AnLF backend".format(name), native["anlfBackend"]["endpoint"],
                uri(testbed["analytics"]["backends"]["pyanlf-" + name]["address"], testbed["analytics"]["backends"]["pyanlf-" + name]["port"]),
            )
        check.equal(
            "NWDAF {} MTLF backend".format(name), native["mtlfBackend"]["endpoint"],
            uri(testbed["analytics"]["backends"]["pymtlf-" + name]["address"], testbed["analytics"]["backends"]["pymtlf-" + name]["port"]),
        )

    backends = testbed["analytics"]["backends"]
    services = ml_runtime["services"]
    mtlf_c = load_yaml(config_dir / "pymtlf-c.yaml")
    seed_descriptor = mtlf_c["model_provision"]["seed_models"][0]
    expected_model_id = seed_descriptor["model_id"]
    expected_interoperability = seed_descriptor["model_interoperability"]
    for backend_name in expected_ml_names:
        backend = backends.get(backend_name, {})
        check.equal("{} runtime".format(backend_name), backend.get("runtime"), "host-container")
        check.equal("{} advertised address".format(backend_name), backend.get("address"), advertised_address)
        check.equal("{} published port".format(backend_name), backend.get("port"), services[backend_name]["publishedPort"])

    for name in ("a", "b"):
        anlf_name = "pyanlf-" + name
        mtlf_name = "pymtlf-" + name
        anlf = load_yaml(config_dir / (anlf_name + ".yaml"))
        anlf_endpoint = backends[anlf_name]
        mtlf_endpoint = backends[mtlf_name]
        check.equal(anlf_name + " bind", anlf["server"]["binding_host"], "0.0.0.0")
        check.equal(anlf_name + " container port", anlf["server"]["port"], services[anlf_name]["containerPort"])
        check.equal(anlf_name + " model device", anlf["model"].get("device"), services[anlf_name]["device"])
        expected_anlf_server = nwdaf_native[name]["anlf"]["server"]
        check.equal(
            anlf_name + " containing NWDAF",
            anlf.get("containing_nwdaf", {}).get("internal_api_root"),
            uri(expected_anlf_server["registerIPv4"], expected_anlf_server["port"]),
        )
        check.equal(
            anlf_name + " containing NWDAF timeout",
            anlf.get("containing_nwdaf", {}).get("request_timeout_seconds"), 30,
        )
        check.equal(anlf_name + " callback", anlf["collection"]["callback_base_uri"], uri(anlf_endpoint["address"], anlf_endpoint["port"]))
        check.equal(
            anlf_name + " model provision callback",
            anlf["model_provision"]["callback_uri"],
            uri(anlf_endpoint["address"], anlf_endpoint["port"])
            + "/internal/v1/ml-model-provision/notifications",
        )
        check.equal(anlf_name + " MongoDB fallback disabled", anlf.get("mongodb", {}).get("enabled"), False)
        check.equal(anlf_name + " MongoDB URL", anlf["mongodb"]["url"], mongo_uri)
        check.equal(anlf_name + " MongoDB database", anlf["mongodb"]["database"], mongo["database"])
        check.equal(anlf_name + " sampling", anlf["analytics"]["ue_communication"]["sampling_interval_seconds"], sampling)
        check.equal(
            anlf_name + " analytics report delivery",
            anlf["analytics"].get("report_delivery"),
            {
                "request_timeout_seconds": delivery["analyticsReportTimeoutSeconds"],
                "max_attempts": 4,
                "retry_interval_seconds": 1,
                "worker_stop_timeout_seconds": delivery[
                    "analyticsWorkerStopTimeoutSeconds"
                ],
            },
        )
        check.equal(anlf_name + " ground-truth interval", anlf["accuracy_monitor"]["ground_truth_check_interval_seconds"], sampling)
        check.equal(anlf_name + " accuracy report period", anlf["accuracy_monitor"]["report_period_seconds"], monitoring["reportPeriodSeconds"])
        check.equal(
            anlf_name + " accuracy report delivery",
            anlf["accuracy_monitor"].get("report_delivery"),
            {
                "request_timeout_seconds": delivery["accuracyReportTimeoutSeconds"],
                "max_attempts": 3,
                "retry_interval_seconds": 1,
            },
        )
        check.equal(
            anlf_name + " runtime completion delivery",
            anlf.get("runtime_completion_delivery"),
            {
                "request_timeout_seconds": delivery[
                    "runtimeCompletionTimeoutSeconds"
                ],
                "retry_interval_seconds": 1,
            },
        )
        check.true(
            anlf_name + " report period cannot collect minimum matched predictions",
            monitoring["reportPeriodSeconds"] // sampling
            >= anlf["accuracy_monitor"]["min_matched_predictions"],
        )
        check.equal(anlf_name + " default model ID", anlf["model"]["default_model_unique_id"], expected_model_id)
        check.equal(anlf_name + " model input size", anlf["model"]["input_size"], seed_model["input_size"])
        check.equal(anlf_name + " model output size", anlf["model"]["output_size"], seed_model["output_size"])
        check.equal(anlf_name + " model channels", anlf["model"]["num_channels"], seed_model["num_channels"])
        check.equal(
            anlf_name + " analytics input window",
            anlf["analytics"]["ue_communication"]["input_window"],
            seed_inference["seq_length"],
        )
        check.equal(
            anlf_name + " analytics output window",
            anlf["analytics"]["ue_communication"]["output_window"],
            seed_inference["out_seq_len"],
        )
        check.equal(
            anlf_name + " model interoperability",
            anlf["model_provision"]["model_interoperability"],
            expected_interoperability,
        )
        check.equal(
            anlf_name + " model provider interoperability",
            anlf["model_provider"]["model_interoperability_vendor_ids"],
            [expected_interoperability],
        )
        check.equal(
            anlf_name + " artifact origins", anlf["model"]["artifact_download"]["allowed_origins"],
            [
                uri(mtlf_endpoint["address"], mtlf_endpoint["port"]),
                uri(backends["pymtlf-c"]["address"], backends["pymtlf-c"]["port"]),
                uri(testbed["coreServices"]["adrf"]["sbi"]["address"], testbed["coreServices"]["adrf"]["sbi"]["port"]),
            ],
        )

        mtlf = load_yaml(config_dir / (mtlf_name + ".yaml"))
        check.equal(mtlf_name + " runtime mode", mtlf.get("runtime", {}).get("mode"), "federated")
        check.equal(mtlf_name + " bind", mtlf["server"]["binding_host"], "0.0.0.0")
        check.equal(mtlf_name + " container port", mtlf["server"]["port"], services[mtlf_name]["containerPort"])
        expected_mtlf_server = nwdaf_native[name]["mtlf"]["server"]
        check.equal(
            mtlf_name + " containing NWDAF",
            mtlf.get("containing_nwdaf", {}).get("internal_api_root"),
            uri(expected_mtlf_server["registerIPv4"], expected_mtlf_server["port"]),
        )
        check.equal(
            mtlf_name + " containing NWDAF timeout",
            mtlf.get("containing_nwdaf", {}).get("request_timeout_seconds"), 30,
        )
        check.equal(mtlf_name + " public URL", mtlf["artifact"]["public_base_url"], uri(mtlf_endpoint["address"], mtlf_endpoint["port"]))
        check.equal(
            mtlf_name + " FL public URL",
            mtlf["federated_learning"]["public_base_url"],
            uri(mtlf_endpoint["address"], mtlf_endpoint["port"]),
        )
        client = mtlf["federated_learning"]["client"]
        check.equal(
            mtlf_name + " collection trigger",
            client.get("training_data", {}).get("collection_trigger"),
            "consumer_subscription",
        )
        check.equal(
            mtlf_name + " FL interoperability",
            client["model_interoperability_ids"],
            [expected_interoperability],
        )
        expected_device = "cpu" if ml_device_policy == "cpu" else "cuda:0"
        check.equal(mtlf_name + " device", client["training"]["device"], expected_device)
        check.true(
            mtlf_name + " client training must not own epochs",
            "epochs" not in client["training"],
        )
        check.equal(mtlf_name + " retrieval window", mtlf["dataset"]["retrieval_window_seconds"], training["preparationDataWindowSeconds"])
        check.equal(mtlf_name + " dataset MongoDB URL", mtlf["dataset"]["mongodb"]["url"], mongo_uri)
        check.equal(mtlf_name + " dataset MongoDB database", mtlf["dataset"]["mongodb"]["database"], mongo["database"])
        check_pymtlf_data_paths(check, mtlf_name, mtlf)

    mtlf_c_endpoint = backends["pymtlf-c"]
    check.equal("pymtlf-c runtime mode", mtlf_c.get("runtime", {}).get("mode"), "federated")
    orchestration = mtlf_c["federated_learning"].get("orchestration", {})
    check.equal("pymtlf-c orchestration mode", orchestration.get("mode"), "flat")
    check.equal(
        "pymtlf-c participant source",
        orchestration.get("participant_source"),
        "monitor_scopes",
    )
    training_trigger = mtlf_c["federated_learning"].get("training_trigger", {})
    check.equal(
        "pymtlf-c degradation training trigger",
        training_trigger.get("degradation", {}).get("enabled"),
        True,
    )
    check.equal(
        "pymtlf-c private training trigger",
        training_trigger.get("private_api", {}).get("enabled"),
        False,
    )
    check.equal("pymtlf-c bind", mtlf_c["server"]["binding_host"], "0.0.0.0")
    check.equal("pymtlf-c container port", mtlf_c["server"]["port"], services["pymtlf-c"]["containerPort"])
    expected_mtlf_c_server = nwdaf_native["c"]["mtlf"]["server"]
    check.equal(
        "pymtlf-c containing NWDAF",
        mtlf_c.get("containing_nwdaf", {}).get("internal_api_root"),
        uri(expected_mtlf_c_server["registerIPv4"], expected_mtlf_c_server["port"]),
    )
    check.equal(
        "pymtlf-c containing NWDAF timeout",
        mtlf_c.get("containing_nwdaf", {}).get("request_timeout_seconds"), 30,
    )
    check.equal("pymtlf-c public URL", mtlf_c["artifact"]["public_base_url"], uri(mtlf_c_endpoint["address"], mtlf_c_endpoint["port"]))
    check.equal(
        "pymtlf-c FL public URL",
        mtlf_c["federated_learning"]["public_base_url"],
        uri(mtlf_c_endpoint["address"], mtlf_c_endpoint["port"]),
    )
    check.equal("pymtlf-c monitor watchdog grace", mtlf_c["model_monitor"].get("watchdog_grace_seconds"), 300)
    check.equal("pymtlf-c missed report threshold", mtlf_c["model_monitor"].get("missed_report_threshold"), 2)
    check_pymtlf_data_paths(check, "pymtlf-c", mtlf_c)
    check.equal(
        "pymtlf-c seed models",
        mtlf_c.get("model_provision", {}).get("seed_models"),
        [{
            "family_id": "ue-communication-default",
            "model_id": 1,
            "artifact_key": "a2c796a001e2da2461418f80b01d7d1e33f0e3349c2817d92286f09e67aa6bef",
            "event": "UE_COMMUNICATION",
            "event_filter": {},
            "target_ue": None,
            "model_interoperability": "001122",
            "use_case_context": "",
        }],
    )
    server = mtlf_c["federated_learning"]["server"]
    check.equal(
        "pymtlf-c training callback",
        server["callback_uri"],
        uri(mtlf_c_endpoint["address"], mtlf_c_endpoint["port"])
        + "/internal/v1/ml-model-training/notifications",
    )
    check.equal(
        "pymtlf-c monitor callback",
        mtlf_c["model_monitor"]["callback_uri"],
        uri(mtlf_c_endpoint["address"], mtlf_c_endpoint["port"])
        + "/internal/v1/ml-model-monitor/notifications",
    )
    check.equal("pymtlf-c fitting rounds", server["round_count"], training["fittingRounds"])
    check.equal(
        "pymtlf-c client training epochs",
        server.get("client_training", {}).get("epochs"),
        training["localEpochs"],
    )
    check.equal("pymtlf-c preparation window", server["preparation_data_window_seconds"], training["preparationDataWindowSeconds"])
    check.equal("pymtlf-c performance gate", server["final_validation"]["enforce_performance_gate"], training["enforcePerformanceGate"])
    check.equal("pymtlf-c monitor period", mtlf_c["model_monitor"]["report_period_seconds"], monitoring["reportPeriodSeconds"])
    check.equal("pymtlf-c minimum references", mtlf_c["accuracy_policy"]["min_reference_samples"], monitoring["minimumReferenceReports"])
    check.equal("pymtlf-c decision window", mtlf_c["accuracy_policy"]["decision_window_size"], monitoring["decisionWindowSize"])
    check.equal("pymtlf-c required hits", mtlf_c["accuracy_policy"]["required_hits"], monitoring["requiredHits"])
    for name in ("a", "b"):
        client = load_yaml(config_dir / ("pymtlf-{}.yaml".format(name)))[
            "federated_learning"
        ]["client"]
        fallback = client["fallback_deadlines"]
        check.equal(
            "pymtlf-{} preparation deadline".format(name),
            fallback["preparation_timeout_seconds"],
            server["preparation_timeout_seconds"],
        )
        check.equal(
            "pymtlf-{} round deadline".format(name),
            fallback["round_timeout_seconds"],
            server["round_timeout_seconds"],
        )
    check.equal(
        "pymtlf-c client origins", mtlf_c["federated_learning"]["artifact_download"]["allowed_origins"],
        [
            uri(backends[name]["address"], backends[name]["port"])
            for name in ("pymtlf-a", "pymtlf-b", "pymtlf-c")
        ],
    )

    manifest = load_yaml(config_dir / "manifest.yaml")
    check.equal("manifest schema", manifest.get("schemaVersion"), 1)
    check.equal("manifest scenario name", manifest.get("scenario", {}).get("name"), scenario["name"])
    check.equal("manifest scenario kind", manifest.get("scenario", {}).get("kind"), scenario["kind"])
    check.equal("manifest guest machines", manifest.get("runtime", {}).get("guestMachines"), sorted(testbed["machines"]))
    check.equal("manifest Host containers", manifest.get("runtime", {}).get("hostContainers"), testbed["placement"]["host-containers"])
    check.equal(
        "manifest PseudoDriver profiles",
        manifest.get("constraints", {}).get("pseudoDriverProfiles"),
        profile_sources,
    )
    for path_name in ("a", "b"):
        pseudo = testbed["paths"][path_name]["upf"]["pseudoDriver"]
        dataset = pseudo["dataset"]
        expected_manifest_dataset = {
            "profile": profile_sources[path_name],
            "guestDirectory": dataset["guestDirectory"],
        }
        check.equal(
            "manifest path-{} dataset".format(path_name),
            manifest.get("datasets", {}).get("path-" + path_name),
            expected_manifest_dataset,
        )
    if dataset_spec is not None:
        check.equal("dataset set paths", sorted(dataset_spec["paths"]), ["path-a", "path-b"])
    generated = manifest.get("generated")
    if generated is not None:
        check.equal(
            "manifest generated files",
            generated.get("files"),
            sorted(
                path.relative_to(config_dir).as_posix()
                for path in config_dir.rglob("*")
                if path.is_file() and path.name != "manifest.yaml"
            ),
        )

    consumer = load_yaml(config_dir / "consumer.yaml")
    if not check_consumer_native(check, config_dir / "consumer.yaml"):
        return finish(check, testbed_path, config_dir)
    check.equal("consumer NRF", consumer["nrfUri"], nrf_uri)
    check.equal(
        "consumer requester NF type",
        consumer["requesterNfType"],
        testbed["consumer"]["requesterNfType"],
    )
    check.equal("consumer target NF type", consumer["discovery"]["targetNfType"], "NWDAF")
    check.equal(
        "consumer discovery service",
        consumer["discovery"]["serviceName"],
        testbed["consumer"]["discovery"]["serviceName"],
    )
    check.equal(
        "consumer discovery event",
        consumer["discovery"]["event"],
        testbed["consumer"]["discovery"]["event"],
    )
    check.equal("consumer PLMN", consumer["target"]["plmn"], plmn)
    check.equal("consumer group", consumer["target"]["internalGroupId"], group_id)
    check.equal("consumer paths", [p["tac"] for p in consumer["target"]["paths"]], expected_tacs)
    check.equal(
        "consumer reporting method",
        consumer["reporting"]["method"],
        testbed["consumer"]["reporting"]["method"],
    )
    check.equal("consumer reporting period", consumer["reporting"]["periodSeconds"], sampling)
    callback = testbed["consumer"]["callback"]
    check.equal("consumer callback bind", consumer["callback"]["bindAddress"], callback["bindAddress"])
    check.equal(
        "consumer callback URI",
        consumer["callback"]["advertisedUri"],
        "http://{}:{}{}".format(
            callback["advertisedAddress"], callback["port"], callback["path"]
        ),
    )
    state_file = Path(consumer["stateFile"])
    check.true("consumer state file must be absolute", state_file.is_absolute())

    addresses_only = [address for _, address in all_addresses]
    duplicates = sorted({address for address in addresses_only if addresses_only.count(address) > 1})
    check.true("duplicate topology addresses: {}".format(", ".join(duplicates)), not duplicates)
    return finish(check, testbed_path, config_dir)


def finish(check, testbed_path, config_dir):
    if check.errors:
        for error in check.errors:
            print("ERROR: " + error, file=sys.stderr)
        return 1
    print("OK testbed={}".format(testbed_path))
    print("OK config={} sha256={}".format(config_dir, sha256_tree(config_dir)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
