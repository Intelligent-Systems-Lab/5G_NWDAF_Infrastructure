#!/usr/bin/env python3
"""Render an explicit topology into a complete generated native config set."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from configlib import (
    ROOT, canonical_sha256, dump_yaml, guest_network_configs, load_yaml,
    resolve_path, set_path, sha256_tree,
)


def endpoint_uri(endpoint):
    return "http://{}:{}".format(endpoint["address"], endpoint["port"])


def read(directory, name):
    return load_yaml(directory / name)


def write(directory, name, value):
    dump_yaml(directory / name, value)


def render(testbed, baseline, output):
    shutil.copytree(str(baseline), str(output))
    core = testbed["coreServices"]
    nrf_uri = endpoint_uri(core["nrf"]["sbi"])
    mongo_uri = "mongodb://{}:{}".format(
        core["mongodb"]["endpoint"]["address"], core["mongodb"]["endpoint"]["port"]
    )
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

    udm = read(output, "udmcfg.yaml")
    group_id = testbed["mobileNetwork"]["internalGroupId"]
    udm["configuration"]["internalGroupIdentifiersRanges"] = [
        {"start": group_id, "end": group_id}
    ]
    write(output, "udmcfg.yaml", udm)

    plmn = testbed["mobileNetwork"]["plmn"]
    snssai = testbed["mobileNetwork"]["snssai"]
    paths = testbed["paths"]
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
        upf["dnnList"] = [{"dnn": testbed["mobileNetwork"]["dnn"], "cidr": path["upf"]["uePool"]}]
        upf["ees"]["enabled"] = bool(path["upf"]["pseudoDriver"]["enabled"])
        upf["ees"]["listenAddr"] = "{}:{}".format(path["upf"]["eventExposure"]["address"], path["upf"]["eventExposure"]["port"])
        upf["ees"]["parquetDir"] = path["upf"]["pseudoDriver"]["dataset"]["guestDirectory"]
        write(output, "upfcfg-{}.yaml".format(name), upf)

        gnb = read(output, "ueransim/gnb-{}.yaml".format(name))
        gnb.update({"mcc": plmn["mcc"], "mnc": plmn["mnc"], "tac": int(path["tai"]["tac"], 16),
                    "linkIp": path["gnb"]["n2"]["address"], "ngapIp": path["gnb"]["n2"]["address"],
                    "gtpIp": path["gnb"]["n3"]["address"], "amfConfigs": [{"address": core["amf"]["n2"]["address"], "port": core["amf"]["n2"]["port"]}],
                    "slices": [dict(snssai)]})
        write(output, "ueransim/gnb-{}.yaml".format(name), gnb)

    for index in range(1, 7):
        name = "a" if index <= 3 else "b"
        ue = read(output, "ueransim/ue{}.yaml".format(index))
        ue["supi"] = paths[name]["ues"][index - 1 if index <= 3 else index - 4]
        ue["mcc"], ue["mnc"] = plmn["mcc"], plmn["mnc"]
        ue["gnbSearchList"] = [paths[name]["gnb"]["n2"]["address"]]
        ue["sessions"][0]["apn"] = testbed["mobileNetwork"]["dnn"]
        ue["sessions"][0]["slice"] = dict(snssai)
        ue["configured-nssai"] = [dict(snssai)]
        ue["default-nssai"] = [dict(snssai)]
        write(output, "ueransim/ue{}.yaml".format(index), ue)

    for name in ("a", "b", "c"):
        cfg = read(output, "nwdafcfg-{}.yaml".format(name))
        native = cfg["configuration"]
        definition = testbed["analytics"]["nwdaf-" + name]
        native["nfInstanceId"] = definition["nfInstanceId"]
        native["sbi"]["registerIPv4"] = definition["sbi"]["address"]
        native["sbi"]["bindingIPv4"] = definition["sbi"]["address"]
        native["sbi"]["port"] = definition["sbi"]["port"]
        native["nrfUri"] = nrf_uri
        if name in ("a", "b"):
            native["nwdafInfo"]["mlAnalyticsList"][0]["trackingAreaList"] = [{"plmnId": dict(plmn), "tac": definition["tai"]}]
            native["anlfBackend"]["endpoint"] = endpoint_uri(
                testbed["analytics"]["backends"]["pyanlf-" + name]
            )
        native["mtlfBackend"]["endpoint"] = endpoint_uri(
            testbed["analytics"]["backends"]["pymtlf-" + name]
        )
        write(output, "nwdafcfg-{}.yaml".format(name), cfg)

    backends = testbed["analytics"]["backends"]
    runtime_services = testbed["mlRuntime"]["services"]
    for name in ("a", "b"):
        anlf_name = "pyanlf-" + name
        mtlf_name = "pymtlf-" + name
        anlf_endpoint = backends[anlf_name]
        mtlf_endpoint = backends[mtlf_name]
        anlf = read(output, anlf_name + ".yaml")
        anlf["server"]["binding_host"] = "0.0.0.0"
        anlf["server"]["port"] = runtime_services[anlf_name]["containerPort"]
        anlf["model"]["artifact_download"]["allowed_origins"] = [
            endpoint_uri(mtlf_endpoint), endpoint_uri(core["adrf"]["sbi"])
        ]
        anlf["model_provision"]["callback_uri"] = endpoint_uri(anlf_endpoint) + "/internal/v1/ml-model-provision/notifications"
        anlf["collection"]["callback_base_uri"] = endpoint_uri(anlf_endpoint)
        anlf["mongodb"]["url"] = mongo_uri
        write(output, anlf_name + ".yaml", anlf)

        mtlf = read(output, mtlf_name + ".yaml")
        mtlf["server"]["binding_host"] = "0.0.0.0"
        mtlf["server"]["port"] = runtime_services[mtlf_name]["containerPort"]
        mtlf["artifact"]["public_base_url"] = endpoint_uri(mtlf_endpoint)
        mtlf["federated_learning"]["public_base_url"] = endpoint_uri(mtlf_endpoint)
        mtlf["federated_learning"]["client"]["training"]["device"] = runtime_services[mtlf_name]["device"]
        mtlf["dataset"]["mongodb"]["url"] = mongo_uri
        write(output, mtlf_name + ".yaml", mtlf)

    mtlf_c_endpoint = backends["pymtlf-c"]
    mtlf_c = read(output, "pymtlf-c.yaml")
    mtlf_c["server"]["binding_host"] = "0.0.0.0"
    mtlf_c["server"]["port"] = runtime_services["pymtlf-c"]["containerPort"]
    mtlf_c["artifact"]["public_base_url"] = endpoint_uri(mtlf_c_endpoint)
    mtlf_c["federated_learning"]["public_base_url"] = endpoint_uri(mtlf_c_endpoint)
    mtlf_c["federated_learning"]["artifact_download"]["allowed_origins"] = [
        endpoint_uri(backends["pymtlf-a"]), endpoint_uri(backends["pymtlf-b"])
    ]
    mtlf_c["federated_learning"]["server"]["callback_uri"] = endpoint_uri(mtlf_c_endpoint) + "/internal/v1/ml-model-training/notifications"
    mtlf_c["model_monitor"]["callback_uri"] = endpoint_uri(mtlf_c_endpoint) + "/internal/v1/ml-model-monitor/notifications"
    write(output, "pymtlf-c.yaml", mtlf_c)

    consumer = read(output, "consumer.yaml")
    consumer["nrfUri"] = nrf_uri
    consumer["target"]["plmn"] = dict(plmn)
    consumer["target"]["internalGroupId"] = testbed["mobileNetwork"]["internalGroupId"]
    consumer["target"]["paths"] = [{"name": name, "tac": paths[name]["tai"]["tac"]} for name in ("a", "b")]
    callback = testbed["consumer"]["callback"]
    consumer["callback"]["bindAddress"] = callback["bindAddress"]
    consumer["callback"]["advertisedUri"] = "http://{}:{}{}".format(callback["advertisedAddress"], callback["port"], callback["path"])
    write(output, "consumer.yaml", consumer)

    for machine, network_config in guest_network_configs(testbed).items():
        write(output, "network/{}.yaml".format(machine), network_config)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--testbed", default="testbed.yaml")
    parser.add_argument("--name", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not args.name.replace("-", "").replace("_", "").isalnum():
        raise SystemExit("name may contain only letters, digits, '-' and '_'")
    testbed_path = resolve_path(args.testbed)
    testbed = load_yaml(testbed_path)
    baseline = ROOT / "config" / "default"
    output = ROOT / "config" / "generated" / args.name
    if output.exists():
        if not args.force:
            raise SystemExit("output exists; pass --force to replace it: {}".format(output))
        shutil.rmtree(str(output))
    render(testbed, baseline, output)
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unknown"
    manifest = load_yaml(output / "manifest.yaml")
    manifest["name"] = args.name
    try:
        manifest["topology"] = testbed_path.relative_to(ROOT).as_posix()
    except ValueError:
        manifest["topology"] = str(testbed_path)
    manifest["runtime"] = {
        "guestMachines": sorted(testbed["machines"]),
        "hostContainers": list(testbed["placement"]["host-containers"]),
    }
    manifest["datasets"] = {}
    for path_name in ("a", "b"):
        pseudo = testbed["paths"][path_name]["upf"]["pseudoDriver"]
        dataset = pseudo["dataset"]
        manifest["datasets"]["path-" + path_name] = {
            "profile": pseudo["profile"],
            "guestDirectory": dataset["guestDirectory"],
        }
    manifest["generated"] = {
        "baselineHash": sha256_tree(baseline),
        "definitionHash": canonical_sha256(testbed),
        "generatorRevision": revision,
        "files": sorted(path.relative_to(output).as_posix() for path in output.rglob("*.yaml") if path.name != "manifest.yaml"),
    }
    dump_yaml(output / "manifest.yaml", manifest)
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
