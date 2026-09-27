#!/usr/bin/env python3
"""Validate a generated protocol-driven hierarchical config set."""

import argparse
import copy
import subprocess
import sys
from pathlib import Path

from configlib import (
    ROOT,
    deployment_kind,
    expected_runtime_inventory,
    guest_network_configs,
    image_dataset_name,
    image_scenario_contract,
    load_runtime_manifest,
    load_yaml,
    nwdaf_definitions,
    protocol_topology,
    resolve_config_dir,
    resolve_config_scenario,
    resolve_path,
    selected_seed_source,
    sha256_tree,
)


class Check:
    def __init__(self):
        self.errors = []

    def equal(self, label, actual, expected):
        if actual != expected:
            self.errors.append(
                "{}: expected {!r}, got {!r}".format(label, expected, actual)
            )

    def true(self, label, condition):
        if not condition:
            self.errors.append(label)


def uri(host, port):
    return "http://{}:{}".format(host, port)


def required_files(testbed):
    required = {
        "nrfcfg.yaml",
        "adrfcfg.yaml",
        "manifest.yaml",
        "compose.yaml",
        "topology/protocol-hierarchical.yaml",
    }
    required.update(
        "network/{}.yaml".format(machine) for machine in testbed["machines"]
    )
    for definition in nwdaf_definitions(testbed):
        suffix = definition["unit"][len("nwdaf-"):]
        required.add("nwdafcfg-{}.yaml".format(suffix))
        required.add(definition["backends"]["mtlf"] + ".yaml")
    return required


def check_data_paths(check, name, config):
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


def check_manifest(check, testbed_path, testbed, config_dir, actual_files, scenario):
    try:
        manifest = load_runtime_manifest(config_dir)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        check.true("invalid runtime manifest: {}".format(exc), False)
        return None

    try:
        definition = testbed_path.relative_to(ROOT).as_posix()
    except ValueError:
        definition = str(testbed_path)
    check.equal("manifest topology name", manifest.get("topology", {}).get("name"), testbed["name"])
    check.equal(
        "manifest topology kind",
        manifest.get("topology", {}).get("kind"),
        deployment_kind(testbed),
    )
    check.equal(
        "manifest topology definition",
        manifest.get("topology", {}).get("definition"),
        definition,
    )

    device_policy = manifest.get("renderOptions", {}).get("mlDevicePolicy")
    check.true(
        "manifest renderOptions.mlDevicePolicy must be cpu or gpu",
        device_policy in ("cpu", "gpu"),
    )
    selected = copy.deepcopy(testbed)
    if device_policy == "cpu":
        for service in selected["mlRuntime"]["services"].values():
            if service["device"] != "cpu":
                service["device"] = "cpu"
    try:
        expected_runtime = expected_runtime_inventory(selected, scenario)
    except (KeyError, TypeError, ValueError) as exc:
        check.true("cannot rebuild runtime inventory: {}".format(exc), False)
        return manifest
    check.equal("manifest exact runtime inventory", manifest.get("runtime"), expected_runtime)

    expected_scenario = copy.deepcopy(scenario)
    expected_scenario["definition"] = manifest.get("scenario", {}).get("definition")
    check.equal("manifest selected scenario", manifest.get("scenario"), expected_scenario)

    coordinator = expected_runtime["coordinatorContainer"]
    descriptor = load_yaml(config_dir / (coordinator + ".yaml"))[
        "model_provision"
    ]["seed_models"][0]
    selected_seed = selected_seed_source(scenario)
    check.equal(
        "manifest seed restoration",
        manifest.get("seedRestoration"),
        {
            "coordinatorContainer": coordinator,
            "canonicalSource": (
                selected_seed[1]
                if selected_seed
                else "/opt/app/seed_models/image_classification/"
                + scenario["workload"]["dataset"]
            ),
            "modelId": descriptor["model_id"],
            "modelInteroperability": descriptor["model_interoperability"],
            "artifactKey": descriptor["artifact_key"],
        },
    )

    dataset_root = ROOT / ".generated" / "image-datasets" / image_dataset_name(scenario)
    check.equal(
        "manifest datasets",
        manifest.get("datasets"),
        {
            "root": str(dataset_root),
            "validation": str(dataset_root / "validation.npz"),
            "heldOut": str(dataset_root / "held-out.npz"),
            "leaves": {
                item["unit"]: str(
                    dataset_root / "leaves" / (item["unit"] + ".npz")
                )
                for item in nwdaf_definitions(testbed)
                if item["role"] == "leaf"
            },
        },
    )
    check.equal(
        "manifest generated files",
        manifest.get("generated", {}).get("files"),
        sorted(path for path in actual_files if path != "manifest.yaml"),
    )
    return manifest


def check_core_configs(check, testbed, config_dir):
    core = testbed["coreServices"]
    mongo_uri = "mongodb://{}:{}".format(
        core["mongodb"]["endpoint"]["address"],
        core["mongodb"]["endpoint"]["port"],
    )
    nrf = load_yaml(config_dir / "nrfcfg.yaml")["configuration"]
    check.equal("NRF MongoDB name", nrf.get("MongoDBName"), core["mongodb"]["database"])
    check.equal("NRF MongoDB URL", nrf.get("MongoDBUrl"), mongo_uri)
    check.equal("NRF PLMN", nrf.get("DefaultPlmnId"), testbed["mobileNetwork"]["plmn"])
    for field in ("registerIPv4", "bindingIPv4", "port"):
        expected = (
            core["nrf"]["sbi"]["address"]
            if field != "port"
            else core["nrf"]["sbi"]["port"]
        )
        check.equal("NRF SBI {}".format(field), nrf.get("sbi", {}).get(field), expected)
    check.equal("NRF OAuth", nrf.get("sbi", {}).get("oauth"), False)

    adrf = load_yaml(config_dir / "adrfcfg.yaml")["configuration"]
    check.equal("ADRF identity", adrf.get("nfInstanceId"), core["adrf"]["nfInstanceId"])
    check.equal(
        "ADRF NRF",
        adrf.get("nrfUri"),
        uri(core["nrf"]["sbi"]["address"], core["nrf"]["sbi"]["port"]),
    )
    check.equal(
        "ADRF MongoDB",
        adrf.get("mongodb"),
        {"name": core["adrf"]["mongodb"]["database"], "url": mongo_uri},
    )
    check.equal(
        "ADRF model storage",
        adrf.get("mlModelStorage"),
        {"localDirectory": core["adrf"]["modelStorage"]["localDirectory"]},
    )
    for field in ("registerIPv4", "bindingIPv4", "port"):
        expected = (
            core["adrf"]["sbi"]["address"]
            if field != "port"
            else core["adrf"]["sbi"]["port"]
        )
        check.equal("ADRF SBI {}".format(field), adrf.get("sbi", {}).get(field), expected)


def check_native_configs(check, testbed, config_dir, scenario, runtime):
    nrf_uri = uri(
        testbed["coreServices"]["nrf"]["sbi"]["address"],
        testbed["coreServices"]["nrf"]["sbi"]["port"],
    )
    services = testbed["mlRuntime"]["services"]

    for definition in nwdaf_definitions(testbed):
        unit = definition["unit"]
        suffix = unit[len("nwdaf-"):]
        native = load_yaml(config_dir / ("nwdafcfg-{}.yaml".format(suffix)))[
            "configuration"
        ]
        check.equal(unit + " identity", native.get("nfInstanceId"), definition["nfInstanceId"])
        check.equal(unit + " NRF", native.get("nrfUri"), nrf_uri)
        check.equal(unit + " AnLF disabled", native.get("anlfBackend"), {"enabled": False})
        check.equal(unit + " SBI address", native.get("sbi", {}).get("registerIPv4"), definition["sbi"]["address"])
        check.equal(unit + " SBI port", native.get("sbi", {}).get("port"), definition["sbi"]["port"])
        capability = native["nwdafInfo"]["mlAnalyticsList"][0]
        check.equal(
            unit + " FL capability",
            capability.get("flCapabilityType"),
            {
                "root": "FL_SERVER",
                "branch": "FL_SERVER_AND_CLIENT",
                "leaf": "FL_CLIENT",
            }[definition["role"]],
        )
        check.equal(
            unit + " analytics event",
            capability.get("mlAnalyticsIds"),
            [scenario["workload"]["event"]],
        )
        if definition["role"] == "leaf":
            check.equal(
                unit + " TAI",
                capability.get("trackingAreaList", [{}])[0].get("tac"),
                definition["tai"],
            )

        backend_name = definition["backends"]["mtlf"]
        backend = services[backend_name]
        check.equal(
            unit + " MTLF endpoint",
            native.get("mtlfBackend", {}).get("endpoint"),
            uri(testbed["mlRuntime"]["advertisedAddress"], backend["publishedPort"]),
        )
        pymtlf = load_yaml(config_dir / (backend_name + ".yaml"))
        check.equal(backend_name + " port", pymtlf["server"]["port"], backend["containerPort"])
        check.equal(
            backend_name + " containing NWDAF",
            pymtlf["containing_nwdaf"]["internal_api_root"],
            uri(definition["sbi"]["address"], 8091),
        )
        check_data_paths(check, backend_name, pymtlf)
        fl = pymtlf["federated_learning"]
        client = fl.get("client")
        server = fl.get("server")
        if client is not None:
            expected_device = (
                "cpu"
                if runtime["mlDevicePolicy"] == "cpu"
                else backend["device"]
            )
            check.equal(
                backend_name + " training device",
                client.get("training", {}).get("device"),
                expected_device,
            )
        if server is not None:
            check.equal(
                backend_name + " round timeout",
                server.get("round_timeout_seconds"),
                testbed["operations"]["roundTimeoutSeconds"],
            )
            check.equal(
                backend_name + " accepted rounds",
                server.get("round_count"),
                scenario["training"]["acceptedRounds"],
            )
            check.equal(
                backend_name + " local epochs",
                server.get("client_training", {}).get("epochs"),
                scenario["training"]["localEpochs"],
            )
        if definition["role"] == "leaf":
            check.equal(
                backend_name + " dataset",
                client.get("training_data", {}).get("dataset"),
                scenario["workload"]["dataset"],
            )
            check.equal(
                backend_name + " shard",
                client.get("training_data", {}).get("shard_path"),
                "/data/train.npz",
            )
        elif definition["role"] == "branch":
            check.true(
                backend_name + " must not own a local shard",
                not client.get("training_data", {}).get("shard_path"),
            )


def check_compose(check, testbed, config_dir, scenario, runtime):
    compose = load_yaml(config_dir / "compose.yaml")
    services = compose.get("services", {})
    selected = runtime["hostContainers"]
    check.equal("Compose service set", set(services), set(selected))
    check.equal("Compose network", compose.get("networks"), {"ml": {"driver": "bridge"}})
    check.equal(
        "Compose volumes",
        set(compose.get("volumes", {})),
        {item["name"] for item in runtime["mlVolumes"]},
    )
    revision = next(
        item["commit"]
        for item in load_yaml(ROOT / "components.lock.yaml")["components"]
        if item["path"] == "ML/PyMTLF"
    )
    definitions = {
        item["backends"]["mtlf"]: item for item in nwdaf_definitions(testbed)
    }
    dataset_root = ROOT / ".generated" / "image-datasets" / image_dataset_name(scenario)
    selected_seed = selected_seed_source(scenario)
    for name in selected:
        service = services.get(name, {})
        expected = testbed["mlRuntime"]["services"][name]
        check.equal(name + " image", service.get("image"), "5g-nwdaf-infrastructure/pymtlf:local")
        check.equal(name + " build target", service.get("build", {}).get("target"), "pymtlf")
        check.equal(
            name + " component revision",
            service.get("build", {}).get("args", {}).get("COMPONENT_REVISION"),
            revision,
        )
        check.true(name + " root filesystem must be read-only", service.get("read_only") is True)
        check.equal(name + " restart policy", service.get("restart"), "no")
        check.equal(name + " memory limit", service.get("mem_limit"), "{}m".format(expected["memoryMiB"]))
        check.equal(name + " CPU limit", service.get("cpus"), float(expected["cpus"]))
        check.equal(
            name + " runtime",
            service.get("runtime"),
            (
                "nvidia"
                if runtime["mlDevicePolicy"] == "gpu"
                and str(expected["device"]).startswith("cuda")
                else None
            ),
        )
        mounts = service.get("volumes", [])
        config_mounts = [item for item in mounts if item.get("target") == "/etc/5g-nwdaf/config.yaml"]
        check.equal(name + " config mount count", len(config_mounts), 1)
        role = definitions[name]["role"]
        data_mounts = [item for item in mounts if item.get("target", "").startswith("/data/")]
        expected_data = None
        if role == "root":
            expected_data = (str(dataset_root / "validation.npz"), "/data/validation.npz")
        elif role == "leaf":
            expected_data = (
                str(dataset_root / "leaves" / (definitions[name]["unit"] + ".npz")),
                "/data/train.npz",
            )
        check.equal(name + " dataset mount count", len(data_mounts), 1 if expected_data else 0)
        if expected_data and data_mounts:
            check.equal(name + " dataset source", data_mounts[0].get("source"), expected_data[0])
            check.equal(name + " dataset target", data_mounts[0].get("target"), expected_data[1])
        seed_mounts = [
            item for item in mounts
            if item.get("target") == "/opt/app/seed_models/selected"
        ]
        check.equal(
            name + " selected seed mount count",
            len(seed_mounts),
            1 if selected_seed and role == "root" else 0,
        )


def check_pymtlf_native(check, config_dir, runtime, scenario, branch_group_count):
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
config_root, coordinator, root_id, source, branch_group_count, *names = sys.argv[1:]
settings = {name: load_settings(Path(config_root) / (name + ".yaml")) for name in names}
root = settings[coordinator]
assignment = StaticTopologyPlanner.load(root.federated_learning.topology.config_file).build(
    root_nf_instance_id=root_id
)
assert len(assignment.branch_groups) == int(branch_group_count)
seed = root.model_provision.seed_models[0]
with tempfile.TemporaryDirectory(prefix="protocol-seed-check-") as temporary:
    temporary = Path(temporary)
    bundle = temporary / "seed.tar.gz"
    build_seed_bundle(
        Path(source), bundle, model_id=seed.model_id, event=seed.event,
        model_interoperability=seed.model_interoperability,
    )
    repository = ArtifactRepository(temporary / "artifacts", root.artifact)
    repository.open()
    assert repository.publish(bundle).key == seed.artifact_key
'''
    source = selected_seed_source(scenario)
    if source is None:
        source = (
            ROOT / "ML" / "PyMTLF" / "seed_models" / "image_classification"
            / scenario["workload"]["dataset"],
            None,
        )
    result = subprocess.run(
        [
            str(interpreter),
            "-c",
            program,
            str(config_dir),
            coordinator,
            root_id,
            str(source[0]),
            str(branch_group_count),
            *services,
        ],
        cwd=ROOT / "ML" / "PyMTLF",
        text=True,
        capture_output=True,
        check=False,
    )
    detail = (result.stderr or result.stdout).strip()
    check.true(
        "PyMTLF native settings/topology/seed validation failed{}".format(
            ": " + detail if detail else ""
        ),
        result.returncode == 0,
    )


def finish(check, testbed_path, config_dir):
    if check.errors:
        for error in check.errors:
            print("ERROR: " + error, file=sys.stderr)
        return 1
    print("OK testbed={}".format(testbed_path))
    print("OK config={} sha256={}".format(config_dir, sha256_tree(config_dir)))
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--testbed", required=True)
    parser.add_argument("--config-dir")
    args = parser.parse_args()

    testbed_path = resolve_path(args.testbed)
    testbed = load_yaml(testbed_path)
    check = Check()
    try:
        deployment_kind(testbed)
        config_dir = resolve_config_dir(testbed, args.config_dir)
        required = required_files(testbed)
    except (KeyError, TypeError, ValueError) as exc:
        check.true("invalid protocol testbed definition: {}".format(exc), False)
        return finish(check, testbed_path, Path(args.config_dir or "."))

    missing = sorted(name for name in required if not (config_dir / name).is_file())
    check.true("missing config files: {}".format(", ".join(missing)), not missing)
    if missing:
        return finish(check, testbed_path, config_dir)
    actual_files = {
        path.relative_to(config_dir).as_posix()
        for path in config_dir.rglob("*")
        if path.is_file()
    }
    check.equal("generated config file set", actual_files, required)

    try:
        _scenario_path, scenario = resolve_config_scenario(config_dir)
        image_scenario_contract(scenario)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        check.true("invalid image scenario contract: {}".format(exc), False)
        return finish(check, testbed_path, config_dir)

    manifest = check_manifest(
        check, testbed_path, testbed, config_dir, actual_files, scenario
    )
    if manifest is None:
        return finish(check, testbed_path, config_dir)
    check_core_configs(check, testbed, config_dir)
    check.equal(
        "native protocol topology",
        load_yaml(config_dir / "topology" / "protocol-hierarchical.yaml"),
        protocol_topology(
            testbed,
            scenario["training"]["localEpochs"],
            scenario["training"].get("proximalMu"),
            scenario["topology"]["onBranchFailure"],
        ),
    )
    for machine, expected in guest_network_configs(testbed).items():
        check.equal(
            "{} network aliases".format(machine),
            load_yaml(config_dir / "network" / (machine + ".yaml")),
            expected,
        )
    check_native_configs(check, testbed, config_dir, scenario, manifest["runtime"])
    check_compose(check, testbed, config_dir, scenario, manifest["runtime"])
    check_pymtlf_native(
        check,
        config_dir,
        manifest["runtime"],
        scenario,
        len(testbed["analytics"]["protocolTopology"]["branchGroups"]),
    )
    return finish(check, testbed_path, config_dir)


if __name__ == "__main__":
    sys.exit(main())
