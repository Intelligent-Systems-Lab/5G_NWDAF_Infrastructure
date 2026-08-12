#!/usr/bin/env python3
"""Validate one complete native config set against a testbed definition."""

import argparse
import ipaddress
import json
import subprocess
import sys
import uuid
from pathlib import Path

from configlib import (
    ROOT, canonical_sha256, config_generator_source_hash, get_path,
    guest_network_configs, load_yaml, resolve_config_dir,
    resolve_config_scenario, resolve_ml_bind_address, resolve_path, sha256_tree,
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


def check_subscriber_fixtures(check, testbed, config_dir):
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

    expected_supis = testbed["paths"]["a"]["ues"] + testbed["paths"]["b"]["ues"]
    records = subscribers.get("subscribers", [])
    fixture_supis = [record.get("supi") for record in records if isinstance(record, dict)]
    fixture_gpsis = [record.get("gpsi") for record in records if isinstance(record, dict)]
    defaults = subscribers.get("defaults", {})
    authentication = defaults.get("authentication", {})
    expected_plmn = testbed["mobileNetwork"]["plmn"]["mcc"] + testbed["mobileNetwork"]["plmn"]["mnc"]

    check.equal("subscriber fixture schema", subscribers.get("schemaVersion"), 1)
    check.equal("subscriber fixture PLMN", subscribers.get("servingPlmnId"), expected_plmn)
    check.equal("subscriber fixture SUPIs", fixture_supis, expected_supis)
    check.true("subscriber fixture GPSIs must be unique", len(fixture_gpsis) == len(set(fixture_gpsis)) == 6)
    check.equal("subscriber fixture S-NSSAI", defaults.get("snssai"), testbed["mobileNetwork"]["snssai"])
    check.equal("subscriber fixture DNN", defaults.get("dnn"), testbed["mobileNetwork"]["dnn"])

    group_records = groups.get("groups", [])
    check.equal("group fixture schema", groups.get("schemaVersion"), 1)
    check.equal("group fixture count", len(group_records), 1)
    if len(group_records) == 1:
        check.equal(
            "group fixture ID", group_records[0].get("intGroupId"),
            testbed["mobileNetwork"]["internalGroupId"],
        )
        check.equal(
            "group fixture SUPIs",
            [item.get("supi") for item in group_records[0].get("ueIdList", [])],
            expected_supis,
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

    expected_placement = {
        "core": ["nrf", "nssf", "udr", "udm", "ausf", "pcf", "amf", "smf", "mongodb", "adrf", "nwdaf-c", "nwdaf-consumer"],
        "path-a": ["upf-a", "gnb-a", "ue1", "ue2", "ue3", "nwdaf-a"],
        "path-b": ["upf-b", "gnb-b", "ue4", "ue5", "ue6", "nwdaf-b"],
        "host-containers": ["pyanlf-a", "pymtlf-a", "pyanlf-b", "pymtlf-b", "pymtlf-c"],
    }
    check.equal("placement groups", sorted(testbed.get("placement", {})), sorted(expected_placement))
    for group, expected in expected_placement.items():
        check.equal("placement.{}".format(group), testbed.get("placement", {}).get(group), expected)

    ml_runtime = testbed.get("mlRuntime", {})
    check.equal("ML runtime engine", ml_runtime.get("engine"), "docker-compose-v2")
    check.equal("ML runtime network", ml_runtime.get("networkMode"), "bridge")
    expected_ml_names = sorted(expected_placement["host-containers"])
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

    missing = sorted(name for name in REQUIRED if not (config_dir / name).is_file())
    check.true("missing config files: {}".format(", ".join(missing)), not missing)
    if missing:
        return finish(check, testbed_path, config_dir)

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
        _scenario_path, scenario = resolve_config_scenario(config_dir)
    except (KeyError, OSError, ValueError) as exc:
        check.true("invalid scenario contract: {}".format(exc), False)
        return finish(check, testbed_path, config_dir)
    check.equal("scenario schema", scenario.get("schemaVersion"), 1)
    check.true(
        "scenario kind must be business-acceptance or bounded-smoke",
        scenario.get("kind") in ("business-acceptance", "bounded-smoke"),
    )
    check.true(
        "scenario warmStartMode must identify its data responsibility",
        scenario.get("warmStartMode") in ("inference-only", "inference-and-training"),
    )
    check.equal("scenario traffic paths", sorted(scenario.get("trafficProfiles", {})), ["a", "b"])
    for path_name, profile_source in scenario.get("trafficProfiles", {}).items():
        check.true(
            "scenario Path {} traffic profile must be a repository-relative file".format(path_name),
            isinstance(profile_source, str)
            and bool(profile_source)
            and (ROOT / profile_source).is_file(),
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

    check_subscriber_fixtures(check, testbed, config_dir)
    try:
        dataset_spec = resolve_dataset_spec(testbed, config_dir)
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        check.true("invalid PseudoDriver dataset contract: {}".format(exc), False)
        dataset_spec = None

    all_addresses = []
    networks = testbed["networks"]
    for machine_name, machine in testbed["machines"].items():
        for network_name, address in machine["interfaces"].items():
            check.true(
                "{}.{} outside {}".format(machine_name, address, networks[network_name]["cidr"]),
                ipaddress.ip_address(address) in ipaddress.ip_network(networks[network_name]["cidr"]),
            )
            all_addresses.append(("machine {} {}".format(machine_name, network_name), address))

    expected_network_configs = guest_network_configs(testbed)
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
            "plmnId": dict(testbed["mobileNetwork"]["plmn"]),
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
    group_id = testbed["mobileNetwork"]["internalGroupId"]
    check.equal(
        "UDM Internal Group range",
        udm.get("internalGroupIdentifiersRanges"),
        [{"start": group_id, "end": group_id}],
    )

    amf = load_yaml(config_dir / "amfcfg.yaml")["configuration"]
    check.equal("AMF N2", amf["ngapIpList"], [testbed["coreServices"]["amf"]["n2"]["address"]])
    expected_tacs = [testbed["paths"][name]["tai"]["tac"] for name in ("a", "b")]
    check.equal("AMF TAIs", [str(item["tac"]).zfill(6) for item in amf["supportTaiList"]], expected_tacs)

    smf = load_yaml(config_dir / "smfcfg.yaml")["configuration"]
    check.equal("SMF N4", smf["pfcp"]["listenAddr"], testbed["coreServices"]["smf"]["n4"]["address"])
    check.equal("SMF NRF registration", smf.get("nrfRegistrationEnabled"), True)
    check.equal("SMF URR period", smf.get("urrPeriod"), sampling)
    ueransim_snssai = dict(testbed["mobileNetwork"]["snssai"])
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
        check.equal("SMF UPF {} TAI".format(name), node["tais"][0]["tac"], path["tai"]["tac"])
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
        check.equal("gNB {} TAC".format(name), str(gnb["tac"]).zfill(6), path["tai"]["tac"])
        check.equal("gNB {} N2".format(name), gnb["ngapIp"], path["gnb"]["n2"]["address"])
        check.equal("gNB {} N3".format(name), gnb["gtpIp"], path["gnb"]["n3"]["address"])
        check.equal("gNB {} S-NSSAI".format(name), gnb["slices"], [ueransim_snssai])
        check.equal("gNB {} cell access type".format(name), gnb.get("cellAccessType"), "nr")

    for index in range(1, 7):
        ue = load_yaml(config_dir / "ueransim" / ("ue{}.yaml".format(index)))
        path_name = "a" if index <= 3 else "b"
        check.equal("UE{} SUPI".format(index), ue["supi"], testbed["paths"][path_name]["ues"][index - 1 if index <= 3 else index - 4])
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
            mtlf_name + " FL interoperability",
            client["model_interoperability_ids"],
            [expected_interoperability],
        )
        expected_device = "cpu" if ml_device_policy == "cpu" else "cuda:0"
        check.equal(mtlf_name + " device", client["training"]["device"], expected_device)
        check.equal(mtlf_name + " local epochs", client["training"]["epochs"], training["localEpochs"])
        check.equal(mtlf_name + " retrieval window", mtlf["dataset"]["retrieval_window_seconds"], training["preparationDataWindowSeconds"])
        check.equal(mtlf_name + " dataset MongoDB URL", mtlf["dataset"]["mongodb"]["url"], mongo_uri)
        check.equal(mtlf_name + " dataset MongoDB database", mtlf["dataset"]["mongodb"]["database"], mongo["database"])
        check_pymtlf_data_paths(check, mtlf_name, mtlf)

    mtlf_c_endpoint = backends["pymtlf-c"]
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
        scenario["trafficProfiles"],
    )
    for path_name in ("a", "b"):
        pseudo = testbed["paths"][path_name]["upf"]["pseudoDriver"]
        dataset = pseudo["dataset"]
        expected_manifest_dataset = {
            "profile": scenario["trafficProfiles"][path_name],
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
            "manifest baseline hash",
            generated.get("baselineHash"),
            sha256_tree(ROOT / "config" / "default"),
        )
        check.equal(
            "manifest topology hash",
            generated.get("definitionHash"),
            canonical_sha256(testbed),
        )
        check.equal(
            "manifest config generator hash",
            generated.get("generatorSourceHash"),
            config_generator_source_hash(),
        )
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
    check.equal("consumer PLMN", consumer["target"]["plmn"], testbed["mobileNetwork"]["plmn"])
    check.equal("consumer group", consumer["target"]["internalGroupId"], testbed["mobileNetwork"]["internalGroupId"])
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
