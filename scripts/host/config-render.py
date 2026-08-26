#!/usr/bin/env python3
"""Render an explicit topology into a complete generated native config set."""

import argparse
import copy
import json
import shutil
import subprocess
import sys

from configlib import (
    ROOT, canonical_sha256, config_generator_source_hash, dump_yaml,
    guest_network_configs, load_yaml, load_scenario_definition, resolve_path,
    repository_relative_paths, resolve_mobile_identities,
    resolve_scenario_profile_paths, set_path, sha256_tree,
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


def render(testbed, baseline, output, scenario):
    identities = resolve_mobile_identities(testbed)
    shutil.copytree(str(baseline), str(output))
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
        raise ValueError("baseline subscriber fixture count must match topology")
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

    for index, supi in enumerate(supis, 1):
        name = "a" if index <= 3 else "b"
        ue = read(output, "ueransim/ue{}.yaml".format(index))
        ue["supi"] = supi
        ue["mcc"], ue["mnc"] = plmn["mcc"], plmn["mnc"]
        ue["gnbSearchList"] = [paths[name]["gnb"]["n2"]["address"]]
        ue["sessions"][0]["apn"] = testbed["mobileNetwork"]["dnn"]
        ue["sessions"][0]["slice"] = dict(ueransim_snssai)
        ue["configured-nssai"] = [dict(ueransim_snssai)]
        ue["default-nssai"] = [dict(ueransim_snssai)]
        ue["useNamespace"] = False
        write(output, "ueransim/ue{}.yaml".format(index), ue)

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
    parser.add_argument("--testbed", default="testbed.yaml")
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
    resolve_mobile_identities(testbed)
    if args.ml_device:
        training_device = "cpu" if args.ml_device == "cpu" else "cuda:0"
        for service_name in ("pymtlf-a", "pymtlf-b"):
            testbed["mlRuntime"]["services"][service_name]["device"] = training_device
    scenario_path, scenario = load_scenario_definition(args.scenario)
    profile_sources = repository_relative_paths(
        resolve_scenario_profile_paths(scenario_path, scenario)
    )
    baseline = ROOT / "config" / "default"
    output = resolve_path(args.output_root) / args.name
    if output.exists():
        if not args.force:
            raise SystemExit("output exists; pass --force to replace it: {}".format(output))
        shutil.rmtree(str(output))
    render(testbed, baseline, output, scenario)
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unknown"
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
        "definitionHash": canonical_sha256(scenario),
    }
    try:
        manifest["topology"] = testbed_path.relative_to(ROOT).as_posix()
    except ValueError:
        manifest["topology"] = str(testbed_path)
    manifest["runtime"] = {
        "guestMachines": sorted(testbed["machines"]),
        "hostContainers": list(testbed["placement"]["host-containers"]),
        "mlDevicePolicy": (
            "gpu"
            if any(
                service["device"].startswith("cuda")
                for service in testbed["mlRuntime"]["services"].values()
            )
            else "cpu"
        ),
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
    manifest["generated"] = {
        "baselineHash": sha256_tree(baseline),
        "definitionHash": canonical_sha256(topology_definition),
        "generatorSourceHash": config_generator_source_hash(),
        "generatorRevision": revision,
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
