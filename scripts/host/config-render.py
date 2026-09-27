#!/usr/bin/env python3
"""Render a protocol-driven hierarchical deployment into native configs."""

import argparse
import copy
import shutil
import subprocess
import sys

from configlib import (
    ROOT,
    dump_yaml,
    deployment_kind,
    expected_runtime_inventory,
    guest_network_configs,
    image_dataset_name,
    image_scenario_contract,
    load_scenario_definition,
    load_yaml,
    nwdaf_definitions,
    protocol_topology,
    resolve_path,
    selected_seed_source,
)


TEMPLATE_ROOT = ROOT / "config" / "templates"


def uri(host, port):
    return "http://{}:{}".format(host, port)


def endpoint_uri(endpoint):
    return uri(endpoint["address"], endpoint["port"])


def write(directory, name, value):
    dump_yaml(directory / name, value)


def protocol_origins(testbed, scenario):
    """Derive each PyMTLF artifact allowlist from the recursive topology."""
    definitions = {item["unit"]: item for item in nwdaf_definitions(testbed)}
    services = testbed["mlRuntime"]["services"]
    address = testbed["mlRuntime"]["advertisedAddress"]
    origins = {
        unit: uri(address, services[item["backends"]["mtlf"]]["publishedPort"])
        for unit, item in definitions.items()
    }
    root = next(unit for unit, item in definitions.items() if item["role"] == "root")
    topology = testbed["analytics"]["protocolTopology"]
    branch_units = [
        candidate["node"]
        for group in topology["branchGroups"]
        for candidate in group["branches"]
    ]
    allowed = {root: [origins[unit] for unit in branch_units]}
    if scenario["topology"]["onBranchFailure"] == "reparent_leaves_to_root":
        allowed[root].extend(
            origins[item["node"]]
            for group in topology["branchGroups"]
            for item in group["leaves"]
        )
    for group in topology["branchGroups"]:
        branches = [item["node"] for item in group["branches"]]
        leaves = [item["node"] for item in group["leaves"]]
        for branch in branches:
            allowed[branch] = [origins[root]] + [origins[leaf] for leaf in leaves]
        for leaf in leaves:
            allowed[leaf] = [origins[branch] for branch in branches]
    adrf = endpoint_uri(testbed["coreServices"]["adrf"]["sbi"])
    return {unit: values + [adrf] for unit, values in allowed.items()}


def render_protocol_nwdaf(testbed, output, item, scenario):
    template = (
        "nwdafcfg-root.yaml"
        if item["role"] == "root"
        else "nwdafcfg-branch-leaf.yaml"
    )
    config = load_yaml(TEMPLATE_ROOT / template)
    native = config["configuration"]
    address = item["sbi"]["address"]
    native["nwdafName"] = item["unit"].upper()
    native["nfInstanceId"] = item["nfInstanceId"]
    native["sbi"].update({
        "scheme": "http",
        "registerIPv4": address,
        "bindingIPv4": address,
        "port": item["sbi"]["port"],
    })
    native["nrfRegistrationEnabled"] = True
    native["nrfUri"] = endpoint_uri(testbed["coreServices"]["nrf"]["sbi"])
    for service, port in (("anlf", 8090), ("mtlf", 8091)):
        native[service]["server"].update({
            "registerIPv4": address,
            "bindingIPv4": address,
            "port": port,
        })
    capability = {
        "root": "FL_SERVER",
        "branch": "FL_SERVER_AND_CLIENT",
        "leaf": "FL_CLIENT",
    }[item["role"]]
    analytics = {
        "mlAnalyticsIds": [scenario["workload"]["event"]],
        "mlModelInterInfo": {"vendorList": ["001122"]},
        "flCapabilityType": capability,
    }
    if item["role"] == "leaf":
        analytics.update({
            "trackingAreaList": [{
                "plmnId": dict(testbed["mobileNetwork"]["plmn"]),
                "tac": item["tai"],
            }],
            "nfTypeList": ["UPF"],
        })
    native["nwdafInfo"] = {"mlAnalyticsList": [analytics]}
    native["serviceNameList"] = (
        ["nnwdaf-mlmodelprovision", "nnwdaf-mlmodelmonitor"]
        if item["role"] == "root"
        else ["nnwdaf-mlmodeltraining"]
    )
    native["anlfBackend"] = {"enabled": False}
    backend = testbed["mlRuntime"]["services"][item["backends"]["mtlf"]]
    native["mtlfBackend"] = {
        "enabled": True,
        "endpoint": uri(
            testbed["mlRuntime"]["advertisedAddress"],
            backend["publishedPort"],
        ),
        "requestTimeout": testbed["operations"]["serviceReadyTimeoutSeconds"],
    }
    config["info"]["description"] = "Protocol-driven {} NWDAF".format(
        item["role"]
    )
    write(output, "nwdafcfg-{}.yaml".format(item["unit"][len("nwdaf-"):]), config)


def render_protocol_pymtlf(testbed, output, item, scenario, allowed_origins):
    template = {
        "root": "fl-server-hierarchy.yaml",
        "branch": "fl-server-client.yaml",
        "leaf": "fl-client.yaml",
    }[item["role"]]
    config = load_yaml(ROOT / "ML" / "PyMTLF" / "config" / template)
    service_name = item["backends"]["mtlf"]
    service = testbed["mlRuntime"]["services"][service_name]
    volume_root = service["volume"]["target"]
    public_origin = uri(
        testbed["mlRuntime"]["advertisedAddress"], service["publishedPort"]
    )
    config["server"] = {
        "binding_host": "0.0.0.0",
        "port": service["containerPort"],
    }
    config["runtime"] = {"mode": "federated"}
    config["containing_nwdaf"] = {
        "internal_api_root": uri(item["sbi"]["address"], 8091),
        "request_timeout_seconds": 30,
    }
    config["storage"] = {"artifact_root": volume_root + "/artifacts"}
    config["model_state"] = {"directory": volume_root + "/model-state"}
    config["publication"] = {"directory": volume_root + "/publications"}
    config["artifact"]["public_base_url"] = public_origin
    fl = config["federated_learning"]
    fl["workspace_root"] = volume_root + "/fl-workspaces"
    fl["public_base_url"] = public_origin
    fl["artifact_download"] = {
        "allowed_origins": list(allowed_origins),
        "timeout_seconds": testbed["operations"]["roundTimeoutSeconds"],
    }
    fl["experiment_recording"] = {
        "directory": volume_root + "/experiment-records"
    }
    if "server" in fl:
        fl["server"].update({
            "callback_uri": (
                public_origin + "/internal/v1/ml-model-training/notifications"
            ),
            "preparation_timeout_seconds": testbed["operations"][
                "preparationTimeoutSeconds"
            ],
            "preparation_data_window_seconds": testbed["operations"][
                "preparationDataWindowSeconds"
            ],
            "round_timeout_seconds": testbed["operations"][
                "roundTimeoutSeconds"
            ],
            "round_count": scenario["training"]["acceptedRounds"],
            "max_active_processes": 1,
            "client_training": {"epochs": scenario["training"]["localEpochs"]},
        })
    if item["role"] == "root":
        fl["topology"] = {
            "strategy": "static",
            "config_file": "topology/protocol-hierarchical.yaml",
        }
        fl["training_trigger"] = {
            "degradation": {"enabled": False},
            "private_api": {"enabled": True},
        }
        fl["experiment_recording"]["validation"] = {
            "dataset": scenario["workload"]["dataset"],
            "path": "/data/validation.npz",
            "device": service["device"],
            "batch_size": 128,
        }
        fl.pop("strategy", None)
        config["model_provision"]["seed_models"] = [{
            "family_id": scenario["workload"]["modelFamilyId"],
            "model_id": scenario["workload"]["seedModelId"],
            "artifact_key": scenario["workload"]["seedArtifactKey"],
            "event": scenario["workload"]["event"],
            "event_filter": {},
            "target_ue": None,
            "model_interoperability": scenario["workload"][
                "modelInteroperability"
            ],
            "use_case_context": "",
        }]
        config["model_monitor"]["callback_uri"] = (
            public_origin + "/internal/v1/ml-model-monitor/notifications"
        )
    elif item["role"] == "branch":
        fl["client"]["training"]["device"] = service["device"]
        fl.pop("orchestration", None)
        fl.pop("topology", None)
        fl.pop("training_trigger", None)
        config.pop("model_provision", None)
        config.pop("model_monitor", None)
        config.pop("accuracy_policy", None)
    else:
        client = fl["client"]
        client["workload"] = {"profile": "image_classification"}
        client["training_data"] = {
            "collection_trigger": "local",
            "dataset": scenario["workload"]["dataset"],
            "shard_path": "/data/train.npz",
        }
        client["model_interoperability_ids"] = [
            scenario["workload"]["modelInteroperability"]
        ]
        client["training"].update({
            "device": service["device"],
            "batch_size": scenario["training"]["batchSize"],
            "learning_rate": scenario["training"]["learningRate"],
            "validation_ratio": 0.1,
            "random_seed": scenario["partition"]["seed"],
        })
        config.pop("dataset", None)
    write(output, service_name + ".yaml", config)


def render_protocol(testbed, output, scenario):
    output.mkdir(parents=True)
    core = testbed["coreServices"]
    mongo_uri = "mongodb://{}:{}".format(
        core["mongodb"]["endpoint"]["address"],
        core["mongodb"]["endpoint"]["port"],
    )
    nrf_uri = endpoint_uri(core["nrf"]["sbi"])
    nrf = load_yaml(TEMPLATE_ROOT / "nrfcfg.yaml")
    nrf["configuration"]["MongoDBName"] = core["mongodb"]["database"]
    nrf["configuration"]["MongoDBUrl"] = mongo_uri
    nrf["configuration"]["DefaultPlmnId"] = dict(
        testbed["mobileNetwork"]["plmn"]
    )
    nrf["configuration"]["sbi"].update({
        "registerIPv4": core["nrf"]["sbi"]["address"],
        "bindingIPv4": core["nrf"]["sbi"]["address"],
        "port": core["nrf"]["sbi"]["port"],
        "oauth": False,
    })
    write(output, "nrfcfg.yaml", nrf)

    adrf = load_yaml(TEMPLATE_ROOT / "adrfcfg.yaml")
    adrf_native = adrf["configuration"]
    adrf_native["nfInstanceId"] = core["adrf"]["nfInstanceId"]
    adrf_native["nrfUri"] = nrf_uri
    adrf_native["sbi"].update({
        "registerIPv4": core["adrf"]["sbi"]["address"],
        "bindingIPv4": core["adrf"]["sbi"]["address"],
        "port": core["adrf"]["sbi"]["port"],
        "oauth": False,
    })
    adrf_native["mongodb"] = {
        "name": core["adrf"]["mongodb"]["database"],
        "url": mongo_uri,
    }
    adrf_native["mlModelStorage"] = {
        "localDirectory": core["adrf"]["modelStorage"]["localDirectory"]
    }
    adrf_native["plmnSupportList"] = [{
        "plmnId": dict(testbed["mobileNetwork"]["plmn"]),
        "snssaiList": [dict(testbed["mobileNetwork"]["snssai"])],
    }]
    adrf_native["locality"] = "protocol-hierarchical"
    write(output, "adrfcfg.yaml", adrf)

    write(
        output,
        "topology/protocol-hierarchical.yaml",
        protocol_topology(
            testbed,
            scenario["training"]["localEpochs"],
            scenario["training"].get("proximalMu"),
            scenario["topology"]["onBranchFailure"],
        ),
    )
    for machine, network in guest_network_configs(testbed).items():
        write(output, "network/{}.yaml".format(machine), network)
    origins = protocol_origins(testbed, scenario)
    for item in nwdaf_definitions(testbed):
        render_protocol_nwdaf(testbed, output, item, scenario)
        render_protocol_pymtlf(
            testbed, output, item, scenario, origins[item["unit"]]
        )


def render_compose(testbed, output, runtime, scenario):
    common_environment = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "NNPACK_DISABLE": "1",
        "MALLOC_ARENA_MAX": "2",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }
    component_locks = {
        item["path"]: item["commit"]
        for item in load_yaml(ROOT / "components.lock.yaml")["components"]
    }
    revision = component_locks["ML/PyMTLF"]
    definitions = {
        item["backends"]["mtlf"]: item for item in nwdaf_definitions(testbed)
    }
    dataset_root = (
        ROOT / ".generated" / "image-datasets" / image_dataset_name(scenario)
    )
    services = {}
    for name in runtime["hostContainers"]:
        definition = testbed["mlRuntime"]["services"][name]
        environment = dict(common_environment)
        environment["SERVICE_PORT"] = str(definition["containerPort"])
        service = {
            "init": True,
            "restart": "no",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "pids_limit": 256,
            "tmpfs": ["/tmp:rw,noexec,nosuid,size=64m"],
            "networks": ["ml"],
            "logging": {
                "driver": "local",
                "options": {"max-size": "10m", "max-file": "3"},
            },
            "image": "5g-nwdaf-infrastructure/pymtlf:local",
            "build": {
                "context": "${REPOSITORY_ROOT:?REPOSITORY_ROOT must be set}",
                "dockerfile": "containers/ml/Dockerfile",
                "target": "pymtlf",
                "args": {"COMPONENT_REVISION": revision},
            },
            "environment": environment,
            "cpus": float(definition["cpus"]),
            "mem_limit": "{}m".format(definition["memoryMiB"]),
            "ports": [{
                "target": definition["containerPort"],
                "published": str(definition["publishedPort"]),
                "host_ip": (
                    "${ML_BIND_ADDRESS:-"
                    + testbed["mlRuntime"]["bindAddress"]
                    + "}"
                ),
                "protocol": "tcp",
            }],
            "volumes": [
                {
                    "type": "bind",
                    "source": "${CONFIG_DIR:-.}/" + name + ".yaml",
                    "target": "/etc/5g-nwdaf/config.yaml",
                    "read_only": True,
                },
                {
                    "type": "volume",
                    "source": definition["volume"]["name"],
                    "target": definition["volume"]["target"],
                },
            ],
            "healthcheck": {
                "test": [
                    "CMD",
                    "python",
                    "-c",
                    "import os, urllib.request; "
                    "urllib.request.urlopen('http://127.0.0.1:' + "
                    "os.environ['SERVICE_PORT'] + '/health/ready', timeout=2).read()",
                ],
                "interval": "10s",
                "timeout": "3s",
                "retries": 12,
                "start_period": "30s",
            },
            "labels": {
                "io.5g-nwdaf.service": name,
                "io.5g-nwdaf.config-set": "${CONFIG_SET_NAME:-default}",
                "io.5g-nwdaf.config-hash": "${CONFIG_HASH:-unresolved}",
            },
        }
        if name == runtime["coordinatorContainer"]:
            service["volumes"].insert(1, {
                "type": "bind",
                "source": (
                    "${CONFIG_DIR:-.}/topology/protocol-hierarchical.yaml"
                ),
                "target": (
                    "/etc/5g-nwdaf/topology/protocol-hierarchical.yaml"
                ),
                "read_only": True,
            })
        if str(definition["device"]).startswith("cuda"):
            service["runtime"] = "nvidia"
            environment.update({
                "NVIDIA_VISIBLE_DEVICES": "nvidia.com/gpu=all",
                "NVIDIA_DRIVER_CAPABILITIES": "compute,utility",
            })
        node = definitions[name]
        if node["role"] == "root":
            service["volumes"].append({
                "type": "bind",
                "source": str(dataset_root / "validation.npz"),
                "target": "/data/validation.npz",
                "read_only": True,
            })
            selected_seed = selected_seed_source(scenario)
            if selected_seed:
                service["volumes"].append({
                    "type": "bind",
                    "source": str(selected_seed[0]),
                    "target": selected_seed[1],
                    "read_only": True,
                })
        elif node["role"] == "leaf":
            service["volumes"].append({
                "type": "bind",
                "source": str(
                    dataset_root / "leaves" / (node["unit"] + ".npz")
                ),
                "target": "/data/train.npz",
                "read_only": True,
            })
        if name == runtime["coordinatorContainer"]:
            selected_seed = selected_seed_source(scenario)
            environment.update({
                "PYMTLF_SEED_SOURCE": (
                    selected_seed[1]
                    if selected_seed
                    else "/opt/app/seed_models/image_classification/"
                    + scenario["workload"]["dataset"]
                ),
                "PYMTLF_SEED_MODEL_ID": str(
                    scenario["workload"]["seedModelId"]
                ),
                "PYMTLF_SEED_INTEROPERABILITY": scenario["workload"][
                    "modelInteroperability"
                ],
                "PYMTLF_SEED_ARTIFACT_KEY": scenario["workload"][
                    "seedArtifactKey"
                ],
            })
        services[name] = service
    dump_yaml(output / "compose.yaml", {
        "name": "5g-nwdaf-infrastructure",
        "services": services,
        "networks": {"ml": {"driver": "bridge"}},
        "volumes": {item["name"]: None for item in runtime["mlVolumes"]},
    })


def prepare_seed(scenario, seed):
    series = scenario.get("experiment", {}).get("series")
    if series == "e0-e2b" and seed is None:
        raise SystemExit("selected experiment scenario requires --seed")
    if seed is None:
        return
    if seed < 0 or series != "e0-e2b":
        raise SystemExit("--seed requires an E0-E2b scenario and non-negative value")
    scenario["partition"]["seed"] = seed
    scenario["partition"]["datasetId"] = "{}-formal-s{}".format(
        scenario["workload"]["dataset"], seed
    )
    interpreter = ROOT / "ML" / "PyMTLF" / ".venv" / "bin" / "python"
    if not interpreter.is_file():
        raise SystemExit(
            "PyMTLF project interpreter is required for selected seed preparation"
        )
    result = subprocess.run(
        [
            str(interpreter),
            str(ROOT / "scripts" / "host" / "seed-model.py"),
            "--dataset",
            scenario["workload"]["dataset"],
            "--seed",
            str(seed),
            "--model-id",
            str(scenario["workload"]["seedModelId"]),
            "--interoperability",
            scenario["workload"]["modelInteroperability"],
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise SystemExit(result.stderr.strip() or result.stdout.strip())
    scenario["workload"]["seedArtifactKey"] = result.stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--testbed", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--output-root", default="config/local")
    parser.add_argument("--ml-device", choices=("cpu", "gpu"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    if not args.name.replace("-", "").replace("_", "").isalnum():
        raise SystemExit("name may contain only letters, digits, '-' and '_'")

    testbed_path = resolve_path(args.testbed)
    topology_definition = load_yaml(testbed_path)
    testbed = copy.deepcopy(topology_definition)
    deployment_kind(testbed)
    if args.ml_device:
        training_device = "cpu" if args.ml_device == "cpu" else "cuda:0"
        for service in testbed["mlRuntime"]["services"].values():
            if service["device"] != "cpu":
                service["device"] = training_device

    scenario_path, scenario = load_scenario_definition(args.scenario)
    image_scenario_contract(scenario)
    prepare_seed(scenario, args.seed)

    output = resolve_path(args.output_root) / args.name
    if output.exists():
        if not args.force:
            raise SystemExit(
                "output exists; pass --force to replace it: {}".format(output)
            )
        shutil.rmtree(str(output))
    render_protocol(testbed, output, scenario)

    try:
        scenario_definition = scenario_path.relative_to(ROOT).as_posix()
    except ValueError:
        scenario_definition = str(scenario_path)
    manifest = {
        "schemaVersion": 1,
        "name": args.name,
        "scenario": {
            "name": scenario["name"],
            "kind": scenario["kind"],
            "definition": scenario_definition,
            "schemaVersion": scenario["schemaVersion"],
            "workload": copy.deepcopy(scenario["workload"]),
            "partition": copy.deepcopy(scenario["partition"]),
            "training": copy.deepcopy(scenario["training"]),
            "topology": copy.deepcopy(scenario["topology"]),
        },
    }
    if "experiment" in scenario:
        manifest["scenario"]["experiment"] = copy.deepcopy(
            scenario["experiment"]
        )
    for section in ("fault", "observation"):
        if section in scenario:
            manifest["scenario"][section] = copy.deepcopy(scenario[section])

    try:
        topology_path = testbed_path.relative_to(ROOT).as_posix()
    except ValueError:
        topology_path = str(testbed_path)
    manifest["topology"] = {
        "name": topology_definition["name"],
        "kind": deployment_kind(topology_definition),
        "definition": topology_path,
    }
    manifest["renderOptions"] = {
        "mlDevicePolicy": (
            "gpu"
            if any(
                str(service["device"]).startswith("cuda")
                for service in testbed["mlRuntime"]["services"].values()
            )
            else "cpu"
        ),
    }
    manifest["runtime"] = expected_runtime_inventory(testbed, scenario)
    coordinator_config = load_yaml(
        output / (manifest["runtime"]["coordinatorContainer"] + ".yaml")
    )
    seed_descriptor = coordinator_config.get("model_provision", {}).get(
        "seed_models", [{}]
    )[0]
    manifest["seedRestoration"] = {
        "coordinatorContainer": manifest["runtime"]["coordinatorContainer"],
        "canonicalSource": (
            selected_seed_source(scenario)
            or (
                None,
                "/opt/app/seed_models/image_classification/"
                + scenario["workload"]["dataset"],
            )
        )[1],
        "modelId": seed_descriptor.get("model_id"),
        "modelInteroperability": seed_descriptor.get("model_interoperability"),
        "artifactKey": seed_descriptor.get("artifact_key"),
    }
    dataset_root = (
        ROOT / ".generated" / "image-datasets" / image_dataset_name(scenario)
    )
    manifest["datasets"] = {
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
    }
    render_compose(testbed, output, manifest["runtime"], scenario)
    manifest["generated"] = {
        "files": sorted(
            path.relative_to(output).as_posix()
            for path in output.rglob("*")
            if path.is_file() and path.name != "manifest.yaml"
        ),
    }
    dump_yaml(output / "manifest.yaml", manifest)
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
