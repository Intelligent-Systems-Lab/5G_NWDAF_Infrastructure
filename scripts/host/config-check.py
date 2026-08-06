#!/usr/bin/env python3
"""Validate one complete native config set against a testbed definition."""

import argparse
import ipaddress
import sys
from pathlib import Path
from urllib.parse import urlparse

from configlib import get_path, load_yaml, resolve_config_dir, resolve_path, sha256_tree


REQUIRED = {
    "nrfcfg.yaml", "amfcfg.yaml", "ausfcfg.yaml", "nssfcfg.yaml", "pcfcfg.yaml",
    "smfcfg.yaml", "udmcfg.yaml", "udrcfg.yaml", "uerouting.yaml", "upfcfg-a.yaml",
    "upfcfg-b.yaml", "nwdafcfg-a.yaml", "nwdafcfg-b.yaml", "nwdafcfg-c.yaml",
    "adrfcfg.yaml", "pyanlf-a.yaml", "pyanlf-b.yaml", "pymtlf-a.yaml",
    "pymtlf-b.yaml", "pymtlf-c.yaml", "consumer.yaml", "manifest.yaml",
    "ueransim/gnb-a.yaml", "ueransim/gnb-b.yaml", "ueransim/ue1.yaml",
    "ueransim/ue2.yaml", "ueransim/ue3.yaml", "ueransim/ue4.yaml",
    "ueransim/ue5.yaml", "ueransim/ue6.yaml",
}


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--testbed", default="testbed.yaml")
    parser.add_argument("--config-dir")
    args = parser.parse_args()

    testbed_path = resolve_path(args.testbed)
    testbed = load_yaml(testbed_path)
    config_dir = resolve_config_dir(testbed, args.config_dir)
    check = Check()

    check.equal("schemaVersion", testbed.get("schemaVersion"), 1)
    check.equal("machine set", sorted(testbed.get("machines", {})), ["core", "path-a", "path-b"])
    check.true("TLS/certificate support must remain disabled", not testbed.get("security", {}).get("tls"))
    host_safety = testbed.get("hostSafety", {})
    for key in ("reserveMemoryMiB", "minimumFreeSwapMiB", "minimumFreeStorageGiB"):
        check.true("hostSafety.{} must be a positive integer".format(key), isinstance(host_safety.get(key), int) and host_safety[key] > 0)

    missing = sorted(name for name in REQUIRED if not (config_dir / name).is_file())
    check.true("missing config files: {}".format(", ".join(missing)), not missing)
    if missing:
        return finish(check, testbed_path, config_dir)

    all_addresses = []
    networks = testbed["networks"]
    for machine_name, machine in testbed["machines"].items():
        for network_name, address in machine["interfaces"].items():
            check.true(
                "{}.{} outside {}".format(machine_name, address, networks[network_name]["cidr"]),
                ipaddress.ip_address(address) in ipaddress.ip_network(networks[network_name]["cidr"]),
            )
            all_addresses.append(("machine {} {}".format(machine_name, network_name), address))

    core_files = {
        "nrf": "nrfcfg.yaml", "nssf": "nssfcfg.yaml", "udr": "udrcfg.yaml",
        "udm": "udmcfg.yaml", "ausf": "ausfcfg.yaml", "pcf": "pcfcfg.yaml",
        "amf": "amfcfg.yaml", "smf": "smfcfg.yaml", "adrf": "adrfcfg.yaml",
    }
    nrf = testbed["coreServices"]["nrf"]["sbi"]
    nrf_uri = uri(nrf["address"], nrf["port"])
    for name, filename in core_files.items():
        cfg = load_yaml(config_dir / filename)
        endpoint = testbed["coreServices"][name]["sbi"]
        sbi = get_path(cfg, ["configuration", "sbi"])
        check.equal(filename + " bind", sbi["bindingIPv4"], endpoint["address"])
        check.equal(filename + " port", sbi["port"], endpoint["port"])
        all_addresses.append((name + " sbi", endpoint["address"]))
        if name != "nrf":
            check.equal(filename + " NRF", cfg["configuration"]["nrfUri"], nrf_uri)

    amf = load_yaml(config_dir / "amfcfg.yaml")["configuration"]
    check.equal("AMF N2", amf["ngapIpList"], [testbed["coreServices"]["amf"]["n2"]["address"]])
    expected_tacs = [testbed["paths"][name]["tai"]["tac"] for name in ("a", "b")]
    check.equal("AMF TAIs", [str(item["tac"]).zfill(6) for item in amf["supportTaiList"]], expected_tacs)

    smf = load_yaml(config_dir / "smfcfg.yaml")["configuration"]
    check.equal("SMF N4", smf["pfcp"]["listenAddr"], testbed["coreServices"]["smf"]["n4"]["address"])
    for name in ("a", "b"):
        path = testbed["paths"][name]
        upf = load_yaml(config_dir / ("upfcfg-{}.yaml".format(name)))
        node = smf["userplaneInformation"]["upNodes"]["UPF-{}".format(name.upper())]
        check.equal("UPF {} N4".format(name), upf["pfcp"]["addr"], path["upf"]["n4"]["address"])
        check.equal("SMF UPF {} N4".format(name), node["nodeID"], path["upf"]["n4"]["address"])
        check.equal("UPF {} N3".format(name), upf["gtpu"]["ifList"][0]["addr"], path["upf"]["n3"]["address"])
        check.equal("UPF {} pool".format(name), upf["dnnList"][0]["cidr"], path["upf"]["uePool"])
        check.equal("SMF UPF {} TAI".format(name), node["tais"][0]["tac"], path["tai"]["tac"])
        check.equal("SMF UPF {} EES".format(name), node["nupfEeApiRoot"], uri(path["upf"]["eventExposure"]["address"], path["upf"]["eventExposure"]["port"]))
        check.true("UPF {} pseudo driver disabled".format(name), upf["ees"]["enabled"])
        check.true("UPF {} pseudo profile mismatch".format(name), upf["ees"]["parquetDir"].endswith("/" + path["upf"]["pseudoDriver"]["datasetProfile"]))

        gnb = load_yaml(config_dir / "ueransim" / ("gnb-{}.yaml".format(name)))
        check.equal("gNB {} TAC".format(name), str(gnb["tac"]).zfill(6), path["tai"]["tac"])
        check.equal("gNB {} N2".format(name), gnb["ngapIp"], path["gnb"]["n2"]["address"])
        check.equal("gNB {} N3".format(name), gnb["gtpIp"], path["gnb"]["n3"]["address"])

    for index in range(1, 7):
        ue = load_yaml(config_dir / "ueransim" / ("ue{}.yaml".format(index)))
        path_name = "a" if index <= 3 else "b"
        check.equal("UE{} SUPI".format(index), ue["supi"], testbed["paths"][path_name]["ues"][index - 1 if index <= 3 else index - 4])
        check.equal("UE{} gNB".format(index), ue["gnbSearchList"], [testbed["paths"][path_name]["gnb"]["n2"]["address"]])

    for name in ("a", "b", "c"):
        native = load_yaml(config_dir / ("nwdafcfg-{}.yaml".format(name)))["configuration"]
        expected = testbed["analytics"]["nwdaf-{}".format(name)]
        check.equal("NWDAF {} ID".format(name), native["nfInstanceId"], expected["nfInstanceId"])
        check.equal("NWDAF {} bind".format(name), native["sbi"]["bindingIPv4"], expected["sbi"]["address"])
        check.equal("NWDAF {} NRF".format(name), native["nrfUri"], nrf_uri)

    consumer = load_yaml(config_dir / "consumer.yaml")
    check.equal("consumer NRF", consumer["nrfUri"], nrf_uri)
    check.equal("consumer PLMN", consumer["target"]["plmn"], testbed["mobileNetwork"]["plmn"])
    check.equal("consumer group", consumer["target"]["internalGroupId"], testbed["mobileNetwork"]["internalGroupId"])
    check.equal("consumer paths", [p["tac"] for p in consumer["target"]["paths"]], expected_tacs)
    advertised = urlparse(consumer["callback"]["advertisedUri"])
    check.equal("consumer callback host", advertised.hostname, testbed["consumer"]["callback"]["advertisedAddress"])

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
