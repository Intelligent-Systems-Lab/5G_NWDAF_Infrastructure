#!/usr/bin/env python3
"""Render an explicit topology into a complete generated native config set."""

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path

from configlib import (
    ROOT, dump_yaml, deployment_kind, expected_runtime_inventory,
    guest_network_configs, load_yaml, load_scenario_definition,
    image_scenario_contract, nwdaf_definitions, protocol_topology,
    resolve_path, repository_relative_paths,
    resolve_mobile_identities, resolve_scenario_profile_paths, set_path,
)


def endpoint_uri(endpoint):
    return "http://{}:{}".format(endpoint["address"], endpoint["port"])


def read(directory, name):
    return load_yaml(directory / name)


def write(directory, name, value):
    dump_yaml(directory / name, value)


def read_json(directory, name):
    with (directory / name).open(encoding="utf-8") as stream:
        return json.load(stream)


def write_json(directory, name, value):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def static_nwdafs(testbed):
    services = testbed["mlRuntime"]["services"]
    result = []
    for definition in nwdaf_definitions(testbed):
        item = dict(definition)
        item["address"] = item["sbi"]["address"]
        item["backendService"] = item["backends"]["mtlf"]
        backend = services[item["backendService"]]
        item["backendPort"] = backend["publishedPort"]
        item["device"] = backend["device"]
        result.append(item)
    return result


def group_id(testbed, local_id):
    mobile = testbed["mobileNetwork"]
    return "{}-{}-{}-{}".format(
        mobile["internalGroup"]["serviceId"], mobile["plmn"]["mcc"],
        mobile["plmn"]["mnc"], local_id,
    )


def static_owned_supis(testbed):
    identities = resolve_mobile_identities(testbed)
    by_number = dict(zip(identities["subscriberNumbers"], identities["supis"]))
    return {
        owner["position"]: [by_number[number] for number in owner["subscriberNumbers"]]
        for owner in testbed["analytics"]["dataOwners"]
    }


def static_owner_tracking_area(testbed, item):
    owner = next(
        owner
        for owner in testbed["analytics"]["dataOwners"]
        if owner["position"] == item["dataOwner"]
    )
    return {
        "plmn_id": dict(testbed["mobileNetwork"]["plmn"]),
        "tac": testbed["paths"][owner["path"]]["tai"]["tac"],
    }


def render_static_nwdaf(testbed, output, item):
    role = item["role"]
    template = "nwdafcfg-c.yaml" if role in ("server", "root") else "nwdafcfg-a.yaml"
    config = load_yaml(ROOT / "config" / "default" / template)
    native = config["configuration"]
    native["nwdafName"] = item["unit"].upper()
    native["nfInstanceId"] = item["nfInstanceId"]
    native["sbi"].update({
        "registerIPv4": item["address"], "bindingIPv4": item["address"],
        "port": item["sbi"]["port"],
    })
    native["nrfUri"] = endpoint_uri(testbed["coreServices"]["nrf"]["sbi"])
    capability = {
        "server": "FL_SERVER", "root": "FL_SERVER", "client": "FL_CLIENT",
        "leaf": "FL_CLIENT", "branch": "FL_SERVER_AND_CLIENT",
    }[role]
    native["serviceNameList"] = (
        ["nnwdaf-mlmodelprovision", "nnwdaf-mlmodelmonitor"]
        if role in ("server", "root") else ["nnwdaf-mlmodeltraining"]
    )
    analytics_info = {
        "mlAnalyticsIds": ["UE_COMMUNICATION"],
        "mlModelInterInfo": {"vendorList": ["001122"]},
        "flCapabilityType": capability,
    }
    if deployment_kind(testbed) == "static-flat" and role == "client":
        area = static_owner_tracking_area(testbed, item)
        analytics_info["trackingAreaList"] = [{
            "plmnId": area["plmn_id"],
            "tac": area["tac"],
        }]
    native["nwdafInfo"] = {"mlAnalyticsList": [analytics_info]}
    for service in ("anlf", "mtlf"):
        native[service]["server"].update({
            "registerIPv4": item["address"], "bindingIPv4": item["address"],
        })
    native["anlfBackend"] = {"enabled": False}
    native["mtlfBackend"] = {
        "enabled": True,
        "endpoint": "http://{}:{}".format(
            testbed["mlRuntime"]["advertisedAddress"], item["backendPort"]
        ),
        "requestTimeout": 35,
    }
    config["info"]["description"] = "{} independent NWDAF NF".format(role)
    write(output, "nwdafcfg-{}.yaml".format(item["unit"][len("nwdaf-"):]), config)


def static_private_training_data(testbed, item, owner, sampling):
    path = testbed["paths"][owner["path"]]
    backend_uri = "http://{}:{}".format(
        testbed["mlRuntime"]["advertisedAddress"], item["backendPort"]
    )
    service = item["backendService"]
    return {
        "collection_trigger": "private_api",
        "callback_base_uri": backend_uri,
        "state_directory": "/var/lib/5g-nwdaf-infrastructure/{}/private-collection".format(service),
        "consent": {"purpose": "model_training", "policy": "not_required_by_local_policy"},
        "collection_profiles": [{
            "profile_id": "data-owner-{}".format(owner["position"]),
            "ml_event": "UE_COMMUNICATION", "ml_event_filter": {},
            "target_ue": {"intGroupIds": [group_id(testbed, owner["groupLocalId"])]},
            "network_area": {"tais": [{
                "plmn_id": dict(testbed["mobileNetwork"]["plmn"]),
                "tac": path["tai"]["tac"],
            }]},
            "dnns": [testbed["mobileNetwork"]["dnn"]],
            "snssais": [dict(testbed["mobileNetwork"]["snssai"])],
            "sampling_interval_seconds": sampling,
        }],
    }


def set_static_data_paths(config, service):
    root = "/var/lib/5g-nwdaf-infrastructure/{}".format(service)
    config["storage"] = {"artifact_root": root + "/artifacts"}
    config["model_state"] = {"directory": root + "/model-state"}
    config["publication"] = {"directory": root + "/publications"}
    config["federated_learning"]["workspace_root"] = root + "/fl-workspaces"


def render_static_pymtlf(testbed, output, item, nwdafs, owners, scenario):
    role = item["role"]
    config = load_yaml(
        ROOT / "config" / "default" /
        ("pymtlf-c.yaml" if role in ("server", "root") else "pymtlf-a.yaml")
    )
    service = item["backendService"]
    public_uri = "http://{}:{}".format(
        testbed["mlRuntime"]["advertisedAddress"], item["backendPort"]
    )
    config["server"] = {"binding_host": "0.0.0.0", "port": item["backendPort"]}
    config["containing_nwdaf"] = {
        "internal_api_root": "http://{}:8091".format(item["address"]),
        "request_timeout_seconds": 30,
    }
    set_static_data_paths(config, service)
    config["artifact"]["public_base_url"] = public_uri
    fl = config["federated_learning"]
    fl["public_base_url"] = public_uri
    fl["artifact_download"] = {
        "allowed_origins": [
            "http://{}:{}".format(testbed["mlRuntime"]["advertisedAddress"], peer["backendPort"])
            for peer in nwdafs
        ],
        "timeout_seconds": 300,
    }
    if role in ("client", "leaf"):
        owner = owners[item["dataOwner"]]
        fl["client"]["training_data"] = static_private_training_data(
            testbed, item, owner, scenario["samplingIntervalSeconds"]
        )
        fl["client"]["training"]["device"] = item["device"]
        config["dataset"]["mongodb"]["collection"] = (
            "nwdaf_raw_notifications_{}".format(service.replace("pymtlf-", ""))
        )
    elif role == "branch":
        coordinator = load_yaml(ROOT / "config" / "default" / "pymtlf-c.yaml")
        client = load_yaml(ROOT / "config" / "default" / "pymtlf-a.yaml")
        fl["server"] = copy.deepcopy(coordinator["federated_learning"]["server"])
        fl["server"]["callback_uri"] = public_uri + "/internal/v1/ml-model-training/notifications"
        fl["client"] = copy.deepcopy(client["federated_learning"]["client"])
        fl["client"]["training"]["device"] = "cpu"
        fl.pop("orchestration", None)
        fl.pop("training_trigger", None)
        config.pop("dataset", None)
    else:
        training = scenario["training"]
        monitoring = scenario["monitoring"]
        fl["orchestration"] = {
            "mode": "flat" if role == "server" else "hierarchical",
            "participant_source": "static",
        }
        fl["training_trigger"] = {
            "degradation": {"enabled": False}, "private_api": {"enabled": True},
        }
        fl["server"]["callback_uri"] = public_uri + "/internal/v1/ml-model-training/notifications"
        fl["server"]["round_count"] = training["fittingRounds"]
        fl["server"]["client_training"]["epochs"] = training["localEpochs"]
        fl["server"]["preparation_data_window_seconds"] = training["preparationDataWindowSeconds"]
        fl["server"]["final_validation"]["enforce_performance_gate"] = training["enforcePerformanceGate"]
        fl["topology"] = {
            "strategy": "static", "config_file": "topology/{}.yaml".format(deployment_kind(testbed)),
        }
        if role == "root":
            fl["strategy"] = {
                "algorithm": {"name": "fedprox", "proximal_mu": 0.01},
                "participant_selection": "all", "waiting_policy": "all",
                "aggregation": "sample_weighted",
            }
        else:
            fl.pop("strategy", None)
        config["model_monitor"]["callback_uri"] = public_uri + "/internal/v1/ml-model-monitor/notifications"
        config["model_monitor"]["report_period_seconds"] = monitoring["reportPeriodSeconds"]
        config["accuracy_policy"]["min_reference_samples"] = monitoring["minimumReferenceReports"]
        config["accuracy_policy"]["decision_window_size"] = monitoring["decisionWindowSize"]
        config["accuracy_policy"]["required_hits"] = monitoring["requiredHits"]
    write(output, service + ".yaml", config)


def static_topology(testbed, nwdafs):
    if deployment_kind(testbed) == "static-flat":
        return {"version": 1, "clients": [{
            "nf_instance_id": item["nfInstanceId"],
            "scope": {"tracking_areas": [static_owner_tracking_area(testbed, item)]},
        } for item in nwdafs if item["role"] == "client"]}
    leaves = {item["dataOwner"]: item for item in nwdafs if item["role"] == "leaf"}
    return {
        "version": 1, "admission": {"mode": "complete_required"},
        "branches": [{
            "nf_instance_id": item["nfInstanceId"],
            "leaves": [{"nf_instance_id": leaves[position]["nfInstanceId"]} for position in item["leaves"]],
        } for item in nwdafs if item["role"] == "branch"],
    }


def render_static_analytics(testbed, output, scenario):
    nwdafs = static_nwdafs(testbed)
    owner_supis = static_owned_supis(testbed)
    owners = {item["position"]: item for item in testbed["analytics"]["dataOwners"]}
    groups = read_json(output, "subscriber/group-memberships.json")
    groups["groups"] = [{
        "intGroupId": group_id(testbed, owner["groupLocalId"]),
        "ueIdList": [{"supi": supi} for supi in owner_supis[owner["position"]]],
    } for owner in testbed["analytics"]["dataOwners"]]
    write_json(output, "subscriber/group-memberships.json", groups)
    udm = read(output, "udmcfg.yaml")
    group_ids = [item["intGroupId"] for item in groups["groups"]]
    udm["configuration"]["internalGroupIdentifiersRanges"] = [{
        "start": min(group_ids), "end": max(group_ids),
    }]
    write(output, "udmcfg.yaml", udm)
    analytics = {}
    for item in nwdafs:
        render_static_nwdaf(testbed, output, item)
        render_static_pymtlf(testbed, output, item, nwdafs, owners, scenario)
        analytics[item["unit"]] = {
            "machine": item["machine"], "sbi": dict(item["sbi"]),
        }
    for machine, network in guest_network_configs(
        testbed, analytics=analytics, include_consumer=False
    ).items():
        write(output, "network/{}.yaml".format(machine), network)
    kind = deployment_kind(testbed)
    write(output, "topology/{}.yaml".format(kind), static_topology(testbed, nwdafs))


def protocol_origins(testbed):
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
    for group in topology["branchGroups"]:
        branches = [item["node"] for item in group["branches"]]
        leaves = [item["node"] for item in group["leaves"]]
        for branch in branches:
            allowed[branch] = [origins[root]] + [origins[leaf] for leaf in leaves]
        for leaf in leaves:
            allowed[leaf] = [origins[branch] for branch in branches]
    adrf = endpoint_uri(testbed["coreServices"]["adrf"]["sbi"])
    return {
        unit: values + [adrf]
        for unit, values in allowed.items()
    }


def render_protocol_nwdaf(testbed, output, item, scenario):
    template = "nwdafcfg-c.yaml" if item["role"] == "root" else "nwdafcfg-a.yaml"
    config = load_yaml(ROOT / "NFs" / "nwdaf" / "config" / template)
    native = config["configuration"]
    address = item["sbi"]["address"]
    native["nwdafName"] = item["unit"].upper()
    native["nfInstanceId"] = item["nfInstanceId"]
    native["sbi"].update({
        "scheme": "http", "registerIPv4": address,
        "bindingIPv4": address, "port": item["sbi"]["port"],
    })
    native["nrfRegistrationEnabled"] = True
    native["nrfUri"] = endpoint_uri(testbed["coreServices"]["nrf"]["sbi"])
    for service, port in (("anlf", 8090), ("mtlf", 8091)):
        native[service]["server"].update({
            "registerIPv4": address, "bindingIPv4": address, "port": port,
        })
    capability = {
        "root": "FL_SERVER", "branch": "FL_SERVER_AND_CLIENT", "leaf": "FL_CLIENT",
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
        if item["role"] == "root" else ["nnwdaf-mlmodeltraining"]
    )
    native["anlfBackend"] = {"enabled": False}
    backend = testbed["mlRuntime"]["services"][item["backends"]["mtlf"]]
    native["mtlfBackend"] = {
        "enabled": True,
        "endpoint": uri(
            testbed["mlRuntime"]["advertisedAddress"], backend["publishedPort"]
        ),
        "requestTimeout": testbed["operations"]["serviceReadyTimeoutSeconds"],
    }
    config["info"]["description"] = "Protocol-driven {} NWDAF".format(item["role"])
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
    public_origin = uri(testbed["mlRuntime"]["advertisedAddress"], service["publishedPort"])
    config["server"] = {"binding_host": "0.0.0.0", "port": service["containerPort"]}
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
    fl["experiment_recording"] = {"directory": volume_root + "/experiment-records"}
    if "server" in fl:
        fl["server"].update({
            "callback_uri": public_origin + "/internal/v1/ml-model-training/notifications",
            "preparation_timeout_seconds": testbed["operations"]["preparationTimeoutSeconds"],
            "preparation_data_window_seconds": testbed["operations"]["preparationDataWindowSeconds"],
            "round_timeout_seconds": testbed["operations"]["roundTimeoutSeconds"],
            "round_count": scenario["training"]["acceptedRounds"],
            "max_active_processes": 1,
            "client_training": {"epochs": 1},
        })
    if item["role"] == "root":
        fl["topology"] = {
            "strategy": "static",
            "config_file": "topology/protocol-hierarchical.yaml",
        }
        fl["training_trigger"] = {
            "degradation": {"enabled": False}, "private_api": {"enabled": True},
        }
        fl["experiment_recording"]["validation"] = {
            "dataset": scenario["workload"]["dataset"],
            "path": "/data/validation.npz",
            "device": "cpu",
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
            "model_interoperability": scenario["workload"]["modelInteroperability"],
            "use_case_context": "",
        }]
        config["model_monitor"]["callback_uri"] = (
            public_origin + "/internal/v1/ml-model-monitor/notifications"
        )
    elif item["role"] == "branch":
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
            "device": "cpu",
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
    nrf = load_yaml(ROOT / "config" / "default" / "nrfcfg.yaml")
    nrf["configuration"]["MongoDBName"] = core["mongodb"]["database"]
    nrf["configuration"]["MongoDBUrl"] = mongo_uri
    nrf["configuration"]["DefaultPlmnId"] = dict(testbed["mobileNetwork"]["plmn"])
    nrf["configuration"]["sbi"].update({
        "registerIPv4": core["nrf"]["sbi"]["address"],
        "bindingIPv4": core["nrf"]["sbi"]["address"],
        "port": core["nrf"]["sbi"]["port"],
        "oauth": False,
    })
    write(output, "nrfcfg.yaml", nrf)

    adrf = load_yaml(ROOT / "config" / "default" / "adrfcfg.yaml")
    adrf_native = adrf["configuration"]
    adrf_native["nfInstanceId"] = core["adrf"]["nfInstanceId"]
    adrf_native["nrfUri"] = nrf_uri
    adrf_native["sbi"].update({
        "registerIPv4": core["adrf"]["sbi"]["address"],
        "bindingIPv4": core["adrf"]["sbi"]["address"],
        "port": core["adrf"]["sbi"]["port"],
        "oauth": False,
    })
    adrf_native["mongodb"] = {"name": core["adrf"]["mongodb"]["database"], "url": mongo_uri}
    adrf_native["mlModelStorage"] = {
        "localDirectory": core["adrf"]["modelStorage"]["localDirectory"]
    }
    adrf_native["plmnSupportList"] = [{
        "plmnId": dict(testbed["mobileNetwork"]["plmn"]),
        "snssaiList": [dict(testbed["mobileNetwork"]["snssai"])],
    }]
    adrf_native["locality"] = "protocol-hierarchical"
    write(output, "adrfcfg.yaml", adrf)

    write(output, "topology/protocol-hierarchical.yaml", protocol_topology(testbed))
    for machine, network in guest_network_configs(testbed, include_consumer=False).items():
        write(output, "network/{}.yaml".format(machine), network)
    origins = protocol_origins(testbed)
    for item in nwdaf_definitions(testbed):
        render_protocol_nwdaf(testbed, output, item, scenario)
        render_protocol_pymtlf(testbed, output, item, scenario, origins[item["unit"]])


def uri(host, port):
    return "http://{}:{}".format(host, port)


def render_compose(testbed, output, runtime, scenario=None):
    kind = deployment_kind(testbed)
    common_environment = {
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
        "NNPACK_DISABLE": "1", "MALLOC_ARENA_MAX": "2", "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1",
    }
    component_locks = {
        item["path"]: item["commit"]
        for item in load_yaml(ROOT / "components.lock.yaml")["components"]
    }
    revisions = {
        "pyanlf": component_locks["ML/PyAnLF"],
        "pymtlf": component_locks["ML/PyMTLF"],
    }
    services = {}
    for name in runtime["hostContainers"]:
        definition = testbed["mlRuntime"]["services"][name]
        image = definition["image"]
        environment = dict(common_environment)
        environment["SERVICE_PORT"] = str(definition["containerPort"])
        service = {
            "init": True, "restart": "no", "read_only": True,
            "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
            "pids_limit": 256, "tmpfs": ["/tmp:rw,noexec,nosuid,size=64m"],
            "networks": ["ml"],
            "logging": {"driver": "local", "options": {"max-size": "10m", "max-file": "3"}},
            "image": "5g-nwdaf-infrastructure/{}:local".format(image),
            "build": {
                "context": "${REPOSITORY_ROOT:?REPOSITORY_ROOT must be set}",
                "dockerfile": "containers/ml/Dockerfile", "target": image,
                "args": {"COMPONENT_REVISION": revisions[image]},
            },
            "environment": environment,
            "cpus": float(definition["cpus"]),
            "mem_limit": "{}m".format(definition["memoryMiB"]),
            "ports": [{
                "target": definition["containerPort"],
                "published": str(definition["publishedPort"]),
                "host_ip": "${ML_BIND_ADDRESS:-" + testbed["mlRuntime"]["bindAddress"] + "}",
                "protocol": "tcp",
            }],
            "volumes": [
                {"type": "bind", "source": "${CONFIG_DIR:-.}/" + name + ".yaml", "target": "/etc/5g-nwdaf/config.yaml", "read_only": True},
                {"type": "volume", "source": definition["volume"]["name"], "target": definition["volume"]["target"]},
            ],
            "healthcheck": {
                "test": ["CMD", "python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['SERVICE_PORT'] + '/health/ready', timeout=2).read()"],
                "interval": "10s", "timeout": "3s", "retries": 12, "start_period": "30s",
            },
            "labels": {
                "io.5g-nwdaf.service": name,
                "io.5g-nwdaf.config-set": "${CONFIG_SET_NAME:-default}",
                "io.5g-nwdaf.config-hash": "${CONFIG_HASH:-unresolved}",
            },
        }
        if kind != "production-flat" and image == "pymtlf" and (
            kind != "protocol-hierarchical" or name == runtime["coordinatorContainer"]
        ):
            topology_name = kind + ".yaml"
            service["volumes"].insert(1, {
                "type": "bind",
                "source": "${CONFIG_DIR:-.}/topology/" + topology_name,
                "target": "/etc/5g-nwdaf/topology/" + topology_name,
                "read_only": True,
            })
        if str(definition["device"]).startswith("cuda"):
            service["runtime"] = "nvidia"
            environment.update({
                "NVIDIA_VISIBLE_DEVICES": "nvidia.com/gpu=all",
                "NVIDIA_DRIVER_CAPABILITIES": "compute,utility",
            })
        if kind == "protocol-hierarchical":
            definition_by_backend = {
                item["backends"]["mtlf"]: item for item in nwdaf_definitions(testbed)
            }
            node = definition_by_backend[name]
            dataset_root = ROOT / ".generated" / "image-datasets" / scenario["name"]
            if node["role"] == "root":
                service["volumes"].append({
                    "type": "bind", "source": str(dataset_root / "validation.npz"),
                    "target": "/data/validation.npz", "read_only": True,
                })
            elif node["role"] == "leaf":
                service["volumes"].append({
                    "type": "bind",
                    "source": str(dataset_root / "leaves" / (node["unit"] + ".npz")),
                    "target": "/data/train.npz", "read_only": True,
                })
        if name == runtime["coordinatorContainer"]:
            if kind == "protocol-hierarchical":
                environment.update({
                    "PYMTLF_SEED_SOURCE": "/opt/app/seed_models/image_classification/"
                    + scenario["workload"]["dataset"],
                    "PYMTLF_SEED_MODEL_ID": str(scenario["workload"]["seedModelId"]),
                    "PYMTLF_SEED_INTEROPERABILITY": scenario["workload"]["modelInteroperability"],
                    "PYMTLF_SEED_ARTIFACT_KEY": scenario["workload"]["seedArtifactKey"],
                })
            else:
                environment.update({
                    "PYMTLF_SEED_SOURCE": "/opt/app/seed_models/initial",
                    "PYMTLF_SEED_MODEL_ID": "1", "PYMTLF_SEED_INTEROPERABILITY": "001122",
                    "PYMTLF_SEED_ARTIFACT_KEY": "a2c796a001e2da2461418f80b01d7d1e33f0e3349c2817d92286f09e67aa6bef",
                })
        services[name] = service
    dump_yaml(output / "compose.yaml", {
        "name": "5g-nwdaf-infrastructure", "services": services,
        "networks": {"ml": {"driver": "bridge"}},
        "volumes": {item["name"]: None for item in runtime["mlVolumes"]},
    })


def render(testbed, baseline, output, scenario):
    identities = resolve_mobile_identities(testbed)
    kind = deployment_kind(testbed)
    ignore = None
    if kind != "production-flat":
        ignore = shutil.ignore_patterns(
            "consumer.yaml", "nwdafcfg-*.yaml", "pyanlf-*.yaml", "pymtlf-*.yaml"
        )
    shutil.copytree(str(baseline), str(output), ignore=ignore)
    plmn = identities["plmn"]
    group_id = identities["internalGroupId"]
    path_supis = identities["pathSupis"]
    supis = identities["supis"]
    paths = testbed["paths"]
    core = testbed["coreServices"]
    sampling = scenario["samplingIntervalSeconds"]
    monitoring = scenario["monitoring"]
    training = scenario["training"]
    nrf_uri = endpoint_uri(core["nrf"]["sbi"])
    mongo_uri = "mongodb://{}:{}".format(
        core["mongodb"]["endpoint"]["address"], core["mongodb"]["endpoint"]["port"]
    )
    webconsole_definition = testbed["optionalServices"]["webconsole"]
    webconsole_endpoint = webconsole_definition["endpoint"]
    webconsole = read(output, "webuicfg.yaml")
    webconsole_config = webconsole["configuration"]
    webconsole_config["mongodb"] = {
        "name": core["mongodb"]["database"],
        "url": mongo_uri,
    }
    webconsole_config["nrfUri"] = nrf_uri
    webconsole_config["webServer"] = {
        "scheme": "http",
        "ipv4Address": webconsole_endpoint["address"],
        "port": webconsole_endpoint["port"],
    }
    webconsole_config["billingServer"]["enable"] = True
    write(output, "webuicfg.yaml", webconsole)

    subscribers = read_json(output, "subscriber/ue-subscribers.json")
    subscriber_records = subscribers.get("subscribers", [])
    if len(subscriber_records) != len(supis):
        if not subscriber_records:
            raise ValueError("baseline subscriber fixture must contain a template")
        gpsi = subscriber_records[0].get("gpsi", "")
        if len(gpsi) < 5 or not gpsi[-5:].isdigit():
            raise ValueError("baseline subscriber GPSI must end in five digits")
        gpsi_prefix = gpsi[:-5]
        subscriber_records = [
            copy.deepcopy(
                subscribers["subscribers"][min(index, len(subscribers["subscribers"]) - 1)]
            )
            for index in range(len(supis))
        ]
        for record, number in zip(subscriber_records, identities["subscriberNumbers"]):
            record["gpsi"] = "{}{:05d}".format(gpsi_prefix, number)
        subscribers["subscribers"] = subscriber_records
    subscribers["servingPlmnId"] = identities["plmnDigits"]
    subscribers["defaults"]["snssai"] = dict(testbed["mobileNetwork"]["snssai"])
    subscribers["defaults"]["dnn"] = testbed["mobileNetwork"]["dnn"]
    for record, supi in zip(subscriber_records, supis):
        record["supi"] = supi
    write_json(output, "subscriber/ue-subscribers.json", subscribers)

    groups = read_json(output, "subscriber/group-memberships.json")
    group_records = groups.get("groups", [])
    if len(group_records) != 1:
        raise ValueError("baseline group fixture must contain exactly one group")
    group_records[0]["intGroupId"] = group_id
    group_records[0]["ueIdList"] = [{"supi": supi} for supi in supis]
    write_json(output, "subscriber/group-memberships.json", groups)

    files = {
        "nrf": "nrfcfg.yaml", "nssf": "nssfcfg.yaml", "udr": "udrcfg.yaml",
        "udm": "udmcfg.yaml", "ausf": "ausfcfg.yaml", "pcf": "pcfcfg.yaml",
        "amf": "amfcfg.yaml", "smf": "smfcfg.yaml", "adrf": "adrfcfg.yaml",
    }
    for name, filename in files.items():
        cfg = read(output, filename)
        sbi = core[name]["sbi"]
        set_path(cfg, ["configuration", "sbi", "registerIPv4"], sbi["address"])
        set_path(cfg, ["configuration", "sbi", "bindingIPv4"], sbi["address"])
        set_path(cfg, ["configuration", "sbi", "port"], sbi["port"])
        if name != "nrf":
            set_path(cfg, ["configuration", "nrfUri"], nrf_uri)
        write(output, filename, cfg)

    nrf = read(output, "nrfcfg.yaml")
    set_path(nrf, ["configuration", "MongoDBUrl"], mongo_uri)
    set_path(nrf, ["configuration", "DefaultPlmnId"], testbed["mobileNetwork"]["plmn"])
    write(output, "nrfcfg.yaml", nrf)
    for filename in ("udrcfg.yaml", "pcfcfg.yaml", "adrfcfg.yaml"):
        cfg = read(output, filename)
        set_path(cfg, ["configuration", "mongodb", "url"], mongo_uri)
        write(output, filename, cfg)

    adrf = read(output, "adrfcfg.yaml")
    adrf_definition = core["adrf"]
    set_path(
        adrf,
        ["configuration", "nfInstanceId"],
        adrf_definition["nfInstanceId"],
    )
    set_path(
        adrf,
        ["configuration", "mongodb", "name"],
        adrf_definition["mongodb"]["database"],
    )
    set_path(
        adrf,
        ["configuration", "mlModelStorage", "localDirectory"],
        adrf_definition["modelStorage"]["localDirectory"],
    )
    adrf["configuration"]["plmnSupportList"] = [{
        "plmnId": dict(plmn),
        "snssaiList": [dict(testbed["mobileNetwork"]["snssai"])],
    }]
    write(output, "adrfcfg.yaml", adrf)

    udm = read(output, "udmcfg.yaml")
    udm["configuration"]["internalGroupIdentifiersRanges"] = [
        {"start": group_id, "end": group_id}
    ]
    write(output, "udmcfg.yaml", udm)

    snssai = testbed["mobileNetwork"]["snssai"]
    ueransim_snssai = dict(snssai)
    sd = str(snssai["sd"])
    ueransim_snssai["sd"] = sd if sd.startswith("0x") else "0x" + sd
    ausf = read(output, "ausfcfg.yaml")
    ausf["configuration"]["plmnSupportList"] = [dict(plmn)]
    write(output, "ausfcfg.yaml", ausf)

    nssf = read(output, "nssfcfg.yaml")
    nssf_config = nssf["configuration"]
    nssf_config["supportedPlmnList"] = [dict(plmn)]
    nssf_config["supportedNssaiInPlmnList"] = [{
        "plmnId": dict(plmn),
        "supportedSnssaiList": [dict(snssai)],
    }]
    nssf_config["nsiList"][0]["snssai"] = dict(snssai)
    nssf_config["taList"] = [{
        "tai": {"plmnId": dict(plmn), "tac": paths[name]["tai"]["tac"]},
        "accessType": "3GPP_ACCESS",
        "supportedSnssaiList": [dict(snssai)],
    } for name in ("a", "b")]
    write(output, "nssfcfg.yaml", nssf)

    amf = read(output, "amfcfg.yaml")
    amf_cfg = amf["configuration"]
    amf_cfg["ngapIpList"] = [core["amf"]["n2"]["address"]]
    amf_cfg["supportTaiList"] = [
        {"plmnId": dict(plmn), "tac": paths[name]["tai"]["tac"]} for name in ("a", "b")
    ]
    amf_cfg["servedGuamiList"][0]["plmnId"] = dict(plmn)
    amf_cfg["plmnSupportList"] = [{"plmnId": dict(plmn), "snssaiList": [dict(snssai)]}]
    amf_cfg["supportDnnList"] = [testbed["mobileNetwork"]["dnn"]]
    write(output, "amfcfg.yaml", amf)

    smf = read(output, "smfcfg.yaml")
    smf_cfg = smf["configuration"]
    smf_cfg["nrfRegistrationEnabled"] = True
    smf_cfg["urrPeriod"] = sampling
    smf_cfg["plmnList"] = [dict(plmn)]
    smf_cfg["snssaiInfos"][0]["sNssai"] = dict(snssai)
    smf_cfg["snssaiInfos"][0]["dnnInfos"][0]["dnn"] = testbed["mobileNetwork"]["dnn"]
    for key in ("nodeID", "listenAddr", "externalAddr"):
        smf_cfg["pfcp"][key] = core["smf"]["n4"]["address"]
    nodes = smf_cfg["userplaneInformation"]["upNodes"]
    links = []
    for name in ("a", "b"):
        path = paths[name]
        suffix = name.upper()
        nodes["gNB-" + suffix] = {"type": "AN", "anIP": path["gnb"]["n2"]["address"]}
        nodes["UPF-" + suffix] = {
            "type": "UPF", "nodeID": path["upf"]["n4"]["address"],
            "addr": path["upf"]["n4"]["address"],
            "tais": [{"plmnId": dict(plmn), "tac": path["tai"]["tac"]}],
            "nupfEeApiRoot": endpoint_uri(path["upf"]["eventExposure"]),
            "sNssaiUpfInfos": [{"sNssai": dict(snssai), "dnnUpfInfoList": [{
                "dnn": testbed["mobileNetwork"]["dnn"], "pools": [{"cidr": path["upf"]["uePool"]}]
            }]}],
            "interfaces": [{"interfaceType": "N3", "endpoints": [path["upf"]["n3"]["address"]], "networkInstances": [testbed["mobileNetwork"]["dnn"]]}],
        }
        links.append({"A": "gNB-" + suffix, "B": "UPF-" + suffix})
    smf_cfg["userplaneInformation"]["links"] = links
    write(output, "smfcfg.yaml", smf)

    for name in ("a", "b"):
        path = paths[name]
        upf = read(output, "upfcfg-{}.yaml".format(name))
        upf["pfcp"]["addr"] = path["upf"]["n4"]["address"]
        upf["pfcp"]["nodeID"] = path["upf"]["n4"]["address"]
        upf["gtpu"]["ifList"][0]["addr"] = path["upf"]["n3"]["address"]
        upf["gtpu"]["ifList"][0]["ifname"] = path["upf"]["gtpInterface"]
        upf["dnnList"] = [{"dnn": testbed["mobileNetwork"]["dnn"], "cidr": path["upf"]["uePool"]}]
        upf["ees"]["enabled"] = bool(path["upf"]["pseudoDriver"]["enabled"])
        upf["ees"]["listenAddr"] = "{}:{}".format(path["upf"]["eventExposure"]["address"], path["upf"]["eventExposure"]["port"])
        upf["ees"]["periodSec"] = sampling
        upf["ees"]["parquetDir"] = path["upf"]["pseudoDriver"]["dataset"]["guestDirectory"]
        write(output, "upfcfg-{}.yaml".format(name), upf)

        gnb = read(output, "ueransim/gnb-{}.yaml".format(name))
        gnb.update({"mcc": plmn["mcc"], "mnc": plmn["mnc"], "tac": int(path["tai"]["tac"], 16),
                    "linkIp": path["gnb"]["n2"]["address"], "ngapIp": path["gnb"]["n2"]["address"],
                    "gtpIp": path["gnb"]["n3"]["address"], "amfConfigs": [{"address": core["amf"]["n2"]["address"], "port": core["amf"]["n2"]["port"]}],
                    "slices": [dict(ueransim_snssai)], "cellAccessType": "nr"})
        write(output, "ueransim/gnb-{}.yaml".format(name), gnb)

    uerouting = read(output, "uerouting.yaml")
    for name in ("a", "b"):
        uerouting["ueRoutingInfo"]["path-" + name]["members"] = path_supis[name]
    write(output, "uerouting.yaml", uerouting)

    index = 0
    for name in ("a", "b"):
        for supi in path_supis[name]:
            index += 1
            filename = "ueransim/ue{}.yaml".format(index)
            if not (output / filename).is_file():
                template_index = 3 if name == "a" else 6
                shutil.copyfile(
                    output / "ueransim/ue{}.yaml".format(template_index),
                    output / filename,
                )
            ue = read(output, filename)
            ue["supi"] = supi
            ue["mcc"], ue["mnc"] = plmn["mcc"], plmn["mnc"]
            ue["gnbSearchList"] = [paths[name]["gnb"]["n2"]["address"]]
            ue["sessions"][0]["apn"] = testbed["mobileNetwork"]["dnn"]
            ue["sessions"][0]["slice"] = dict(ueransim_snssai)
            ue["configured-nssai"] = [dict(ueransim_snssai)]
            ue["default-nssai"] = [dict(ueransim_snssai)]
            ue["useNamespace"] = False
            write(output, filename, ue)

    if kind != "production-flat":
        render_static_analytics(testbed, output, scenario)
        return

    nwdaf_internal_roots = {}
    for name in ("a", "b", "c"):
        cfg = read(output, "nwdafcfg-{}.yaml".format(name))
        native = cfg["configuration"]
        definition = testbed["analytics"]["nwdaf-" + name]
        native["nfInstanceId"] = definition["nfInstanceId"]
        native["sbi"]["registerIPv4"] = definition["sbi"]["address"]
        native["sbi"]["bindingIPv4"] = definition["sbi"]["address"]
        native["sbi"]["port"] = definition["sbi"]["port"]
        native["nrfUri"] = nrf_uri
        for service in ("anlf", "mtlf"):
            native[service]["server"]["registerIPv4"] = definition["sbi"]["address"]
            native[service]["server"]["bindingIPv4"] = definition["sbi"]["address"]
        if name in ("a", "b"):
            native["nwdafInfo"]["mlAnalyticsList"][0]["trackingAreaList"] = [{"plmnId": dict(plmn), "tac": definition["tai"]}]
            native["anlfBackend"]["endpoint"] = endpoint_uri(
                testbed["analytics"]["backends"]["pyanlf-" + name]
            )
        native["mtlfBackend"]["endpoint"] = endpoint_uri(
            testbed["analytics"]["backends"]["pymtlf-" + name]
        )
        nwdaf_internal_roots[name] = {
            service: "http://{}:{}".format(
                native[service]["server"]["registerIPv4"],
                native[service]["server"]["port"],
            )
            for service in ("anlf", "mtlf")
        }
        write(output, "nwdafcfg-{}.yaml".format(name), cfg)

    backends = testbed["analytics"]["backends"]
    runtime_services = testbed["mlRuntime"]["services"]
    delivery = testbed["operations"]["pyanlfDelivery"]
    for name in ("a", "b"):
        anlf_name = "pyanlf-" + name
        mtlf_name = "pymtlf-" + name
        anlf_endpoint = backends[anlf_name]
        mtlf_endpoint = backends[mtlf_name]
        anlf = read(output, anlf_name + ".yaml")
        anlf["server"]["binding_host"] = "0.0.0.0"
        anlf["server"]["port"] = runtime_services[anlf_name]["containerPort"]
        anlf["containing_nwdaf"]["internal_api_root"] = nwdaf_internal_roots[name]["anlf"]
        anlf["model"]["device"] = runtime_services[anlf_name]["device"]
        anlf["model"]["artifact_download"]["allowed_origins"] = [
            endpoint_uri(mtlf_endpoint), endpoint_uri(backends["pymtlf-c"]),
            endpoint_uri(core["adrf"]["sbi"])
        ]
        anlf["model_provision"]["callback_uri"] = endpoint_uri(anlf_endpoint) + "/internal/v1/ml-model-provision/notifications"
        anlf["collection"]["callback_base_uri"] = endpoint_uri(anlf_endpoint)
        anlf["mongodb"]["url"] = mongo_uri
        anlf["analytics"]["ue_communication"]["sampling_interval_seconds"] = sampling
        anlf["analytics"]["report_delivery"] = {
            "request_timeout_seconds": delivery["analyticsReportTimeoutSeconds"],
            "max_attempts": 4,
            "retry_interval_seconds": 1,
            "worker_stop_timeout_seconds": delivery[
                "analyticsWorkerStopTimeoutSeconds"
            ],
        }
        anlf["accuracy_monitor"]["ground_truth_check_interval_seconds"] = sampling
        anlf["accuracy_monitor"]["report_period_seconds"] = monitoring["reportPeriodSeconds"]
        anlf["accuracy_monitor"]["report_delivery"] = {
            "request_timeout_seconds": delivery["accuracyReportTimeoutSeconds"],
            "max_attempts": 3,
            "retry_interval_seconds": 1,
        }
        anlf["runtime_completion_delivery"] = {
            "request_timeout_seconds": delivery[
                "runtimeCompletionTimeoutSeconds"
            ],
            "retry_interval_seconds": 1,
        }
        write(output, anlf_name + ".yaml", anlf)

        mtlf = read(output, mtlf_name + ".yaml")
        mtlf["server"]["binding_host"] = "0.0.0.0"
        mtlf["server"]["port"] = runtime_services[mtlf_name]["containerPort"]
        mtlf["containing_nwdaf"]["internal_api_root"] = nwdaf_internal_roots[name]["mtlf"]
        mtlf["artifact"]["public_base_url"] = endpoint_uri(mtlf_endpoint)
        mtlf["federated_learning"]["public_base_url"] = endpoint_uri(mtlf_endpoint)
        mtlf["federated_learning"]["client"]["training"]["device"] = runtime_services[mtlf_name]["device"]
        mtlf["dataset"]["retrieval_window_seconds"] = training["preparationDataWindowSeconds"]
        mtlf["dataset"]["mongodb"]["url"] = mongo_uri
        write(output, mtlf_name + ".yaml", mtlf)

    mtlf_c_endpoint = backends["pymtlf-c"]
    mtlf_c = read(output, "pymtlf-c.yaml")
    mtlf_c["server"]["binding_host"] = "0.0.0.0"
    mtlf_c["server"]["port"] = runtime_services["pymtlf-c"]["containerPort"]
    mtlf_c["containing_nwdaf"]["internal_api_root"] = nwdaf_internal_roots["c"]["mtlf"]
    mtlf_c["artifact"]["public_base_url"] = endpoint_uri(mtlf_c_endpoint)
    mtlf_c["federated_learning"]["public_base_url"] = endpoint_uri(mtlf_c_endpoint)
    mtlf_c["federated_learning"]["artifact_download"]["allowed_origins"] = [
        endpoint_uri(backends["pymtlf-a"]),
        endpoint_uri(backends["pymtlf-b"]),
        endpoint_uri(mtlf_c_endpoint),
    ]
    mtlf_c["federated_learning"]["server"]["callback_uri"] = endpoint_uri(mtlf_c_endpoint) + "/internal/v1/ml-model-training/notifications"
    mtlf_c["federated_learning"]["server"]["round_count"] = training["fittingRounds"]
    mtlf_c["federated_learning"]["server"]["client_training"]["epochs"] = training["localEpochs"]
    mtlf_c["federated_learning"]["server"]["preparation_data_window_seconds"] = training["preparationDataWindowSeconds"]
    mtlf_c["federated_learning"]["server"]["final_validation"]["enforce_performance_gate"] = training["enforcePerformanceGate"]
    mtlf_c["model_monitor"]["callback_uri"] = endpoint_uri(mtlf_c_endpoint) + "/internal/v1/ml-model-monitor/notifications"
    mtlf_c["model_monitor"]["report_period_seconds"] = monitoring["reportPeriodSeconds"]
    mtlf_c["accuracy_policy"]["min_reference_samples"] = monitoring["minimumReferenceReports"]
    mtlf_c["accuracy_policy"]["decision_window_size"] = monitoring["decisionWindowSize"]
    mtlf_c["accuracy_policy"]["required_hits"] = monitoring["requiredHits"]
    write(output, "pymtlf-c.yaml", mtlf_c)

    consumer = read(output, "consumer.yaml")
    consumer["nrfUri"] = nrf_uri
    consumer["requesterNfType"] = testbed["consumer"]["requesterNfType"]
    consumer["discovery"]["serviceName"] = testbed["consumer"]["discovery"]["serviceName"]
    consumer["discovery"]["event"] = testbed["consumer"]["discovery"]["event"]
    consumer["target"]["plmn"] = dict(plmn)
    consumer["target"]["internalGroupId"] = group_id
    consumer["target"]["paths"] = [{"name": name, "tac": paths[name]["tai"]["tac"]} for name in ("a", "b")]
    callback = testbed["consumer"]["callback"]
    consumer["callback"]["bindAddress"] = callback["bindAddress"]
    consumer["callback"]["advertisedUri"] = "http://{}:{}{}".format(callback["advertisedAddress"], callback["port"], callback["path"])
    consumer["reporting"]["method"] = testbed["consumer"]["reporting"]["method"]
    consumer["reporting"]["periodSeconds"] = sampling
    write(output, "consumer.yaml", consumer)

    for machine, network_config in guest_network_configs(testbed).items():
        write(output, "network/{}.yaml".format(machine), network_config)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--testbed", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--output-root", default="config/local")
    parser.add_argument("--ml-device", choices=("cpu", "gpu"))
    parser.add_argument("--webconsole", choices=("false", "true"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not args.name.replace("-", "").replace("_", "").isalnum():
        raise SystemExit("name may contain only letters, digits, '-' and '_'")
    testbed_path = resolve_path(args.testbed)
    topology_definition = load_yaml(testbed_path)
    testbed = copy.deepcopy(topology_definition)
    kind = deployment_kind(testbed)
    if kind != "protocol-hierarchical":
        resolve_mobile_identities(testbed)
    if kind != "production-flat" and args.webconsole == "true":
        raise SystemExit("static TESTBED definitions do not enable WebConsole")
    if args.ml_device:
        training_device = "cpu" if args.ml_device == "cpu" else "cuda:0"
        for service_name, service in testbed["mlRuntime"]["services"].items():
            if service_name.startswith("pymtlf-") and service["device"] != "cpu":
                service["device"] = training_device
    scenario_path, scenario = load_scenario_definition(args.scenario)
    if kind == "protocol-hierarchical":
        image_scenario_contract(scenario)
        profile_sources = {}
    else:
        profile_sources = repository_relative_paths(
            resolve_scenario_profile_paths(scenario_path, scenario)
        )
    baseline = ROOT / "config" / "default"
    output = resolve_path(args.output_root) / args.name
    if output.exists():
        if not args.force:
            raise SystemExit("output exists; pass --force to replace it: {}".format(output))
        shutil.rmtree(str(output))
    if kind == "protocol-hierarchical":
        render_protocol(testbed, output, scenario)
        manifest = {"schemaVersion": 1}
    else:
        render(testbed, baseline, output, scenario)
        manifest = load_yaml(output / "manifest.yaml")
    manifest["name"] = args.name
    try:
        scenario_definition = scenario_path.relative_to(ROOT).as_posix()
    except ValueError:
        scenario_definition = str(scenario_path)
    manifest["scenario"] = {
        "name": scenario["name"],
        "kind": scenario["kind"],
        "definition": scenario_definition,
    }
    if kind == "protocol-hierarchical":
        manifest["scenario"]["workload"] = copy.deepcopy(scenario["workload"])
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
        "mlDevicePolicy": "gpu" if any(
            str(service["device"]).startswith("cuda")
            for service in testbed["mlRuntime"]["services"].values()
        ) else "cpu",
    }
    manifest["runtime"] = expected_runtime_inventory(testbed)
    coordinator_config = load_yaml(
        output / (manifest["runtime"]["coordinatorContainer"] + ".yaml")
    )
    seed_descriptor = coordinator_config.get("model_provision", {}).get(
        "seed_models", [{}]
    )[0]
    manifest["seedRestoration"] = {
        "coordinatorContainer": manifest["runtime"]["coordinatorContainer"],
        "canonicalSource": (
            "/opt/app/seed_models/image_classification/" + scenario["workload"]["dataset"]
            if kind == "protocol-hierarchical" else "/opt/app/seed_models/initial"
        ),
        "modelId": seed_descriptor.get("model_id"),
        "modelInteroperability": seed_descriptor.get("model_interoperability"),
        "artifactKey": seed_descriptor.get("artifact_key"),
    }
    manifest["optionalServices"] = {
        "webconsole": {
            "enabled": args.webconsole == "true"
            if args.webconsole is not None
            else bool(
                manifest.get("optionalServices", {})
                .get("webconsole", {})
                .get("enabled", False)
            ),
        },
    }
    if kind == "protocol-hierarchical":
        dataset_root = ROOT / ".generated" / "image-datasets" / scenario["name"]
        manifest["datasets"] = {
            "root": str(dataset_root),
            "validation": str(dataset_root / "validation.npz"),
            "heldOut": str(dataset_root / "held-out.npz"),
            "leaves": {
                item["unit"]: str(
                    dataset_root / "leaves" / (item["unit"] + ".npz")
                )
                for item in nwdaf_definitions(testbed) if item["role"] == "leaf"
            },
        }
        manifest["constraints"] = {"pseudoDriverProfiles": {}}
    else:
        manifest["datasets"] = {}
        manifest.setdefault("constraints", {})["pseudoDriverProfiles"] = dict(
            profile_sources
        )
        for path_name in ("a", "b"):
            pseudo = testbed["paths"][path_name]["upf"]["pseudoDriver"]
            dataset = pseudo["dataset"]
            manifest["datasets"]["path-" + path_name] = {
                "profile": profile_sources[path_name],
                "guestDirectory": dataset["guestDirectory"],
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
