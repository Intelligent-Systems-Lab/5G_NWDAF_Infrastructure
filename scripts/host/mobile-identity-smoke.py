#!/usr/bin/env python3
"""Prove that one PLMN source renders every dependent mobile identity."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from configlib import (
    ROOT, dump_yaml, load_yaml, resolve_mobile_identities,
)


def equal(label, actual, expected):
    if actual != expected:
        raise AssertionError(
            "{}: expected {!r}, got {!r}".format(label, expected, actual)
        )


def load_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def render_and_check(testbed_path, output_root, name):
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "host" / "config-render.py"),
            "--testbed",
            str(testbed_path),
            "--name",
            name,
            "--scenario",
            "fixtures/full-core/scenarios/fl-closure-smoke.yaml",
            "--output-root",
            str(output_root),
            "--ml-device",
            "cpu",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    config_dir = output_root / name
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "host" / "config-check.py"),
            "--testbed",
            str(testbed_path),
            "--config-dir",
            str(config_dir),
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return config_dir


def verify_case(testbed, config_dir, expected_gpsis):
    identities = resolve_mobile_identities(testbed)
    plmn = identities["plmn"]
    group_id = identities["internalGroupId"]
    supis = identities["supis"]
    paths = testbed["paths"]
    snssai = testbed["mobileNetwork"]["snssai"]
    expected_tais = [
        {"plmnId": dict(plmn), "tac": paths[name]["tai"]["tac"]}
        for name in ("a", "b")
    ]

    equal(
        "NRF PLMN",
        load_yaml(config_dir / "nrfcfg.yaml")["configuration"]["DefaultPlmnId"],
        plmn,
    )
    equal(
        "AUSF PLMN",
        load_yaml(config_dir / "ausfcfg.yaml")["configuration"][
            "plmnSupportList"
        ],
        [plmn],
    )
    nssf = load_yaml(config_dir / "nssfcfg.yaml")["configuration"]
    equal("NSSF PLMN", nssf["supportedPlmnList"], [plmn])
    equal("NSSF TAIs", [item["tai"] for item in nssf["taList"]], expected_tais)
    amf = load_yaml(config_dir / "amfcfg.yaml")["configuration"]
    equal("AMF GUAMI PLMN", amf["servedGuamiList"][0]["plmnId"], plmn)
    equal("AMF TAIs", amf["supportTaiList"], expected_tais)
    smf = load_yaml(config_dir / "smfcfg.yaml")["configuration"]
    equal("SMF PLMN", smf["plmnList"], [plmn])
    for name in ("a", "b"):
        equal(
            "SMF Path {} TAI".format(name),
            smf["userplaneInformation"]["upNodes"]["UPF-" + name.upper()][
                "tais"
            ],
            [expected_tais[0 if name == "a" else 1]],
        )
        gnb = load_yaml(config_dir / "ueransim" / "gnb-{}.yaml".format(name))
        equal("gNB {} PLMN".format(name), (gnb["mcc"], gnb["mnc"]), (plmn["mcc"], plmn["mnc"]))
        nwdaf = load_yaml(config_dir / "nwdafcfg-{}.yaml".format(name))[
            "configuration"
        ]
        equal(
            "NWDAF {} TAI".format(name),
            nwdaf["nwdafInfo"]["mlAnalyticsList"][0]["trackingAreaList"],
            [expected_tais[0 if name == "a" else 1]],
        )

    adrf = load_yaml(config_dir / "adrfcfg.yaml")["configuration"]
    equal(
        "ADRF PLMN",
        adrf["plmnSupportList"],
        [{"plmnId": plmn, "snssaiList": [snssai]}],
    )
    equal(
        "UDM group",
        load_yaml(config_dir / "udmcfg.yaml")["configuration"][
            "internalGroupIdentifiersRanges"
        ],
        [{"start": group_id, "end": group_id}],
    )
    equal(
        "Consumer identity target",
        {
            key: load_yaml(config_dir / "consumer.yaml")["target"][key]
            for key in ("plmn", "internalGroupId")
        },
        {"plmn": plmn, "internalGroupId": group_id},
    )

    uerouting = load_yaml(config_dir / "uerouting.yaml")["ueRoutingInfo"]
    for name in ("a", "b"):
        equal(
            "UE routing Path {}".format(name),
            uerouting["path-" + name]["members"],
            identities["pathSupis"][name],
        )
    for index, supi in enumerate(supis, 1):
        ue = load_yaml(config_dir / "ueransim" / "ue{}.yaml".format(index))
        equal("UE{} SUPI".format(index), ue["supi"], supi)
        equal("UE{} PLMN".format(index), (ue["mcc"], ue["mnc"]), (plmn["mcc"], plmn["mnc"]))

    subscribers = load_json(config_dir / "subscriber" / "ue-subscribers.json")
    equal("subscriber PLMN", subscribers["servingPlmnId"], identities["plmnDigits"])
    equal(
        "subscriber SUPIs",
        [record["supi"] for record in subscribers["subscribers"]],
        supis,
    )
    equal(
        "GPSIs remain independent of PLMN",
        [record["gpsi"] for record in subscribers["subscribers"]],
        expected_gpsis,
    )
    groups = load_json(config_dir / "subscriber" / "group-memberships.json")
    equal("group fixture ID", groups["groups"][0]["intGroupId"], group_id)
    equal(
        "group fixture SUPIs",
        [item["supi"] for item in groups["groups"][0]["ueIdList"]],
        supis,
    )


def main():
    baseline_subscribers = load_json(
        ROOT / "config" / "default" / "subscriber" / "ue-subscribers.json"
    )
    expected_gpsis = [
        record["gpsi"] for record in baseline_subscribers["subscribers"]
    ]
    cases = (("mnc-2", "001", "01"), ("mnc-3", "001", "001"))
    with tempfile.TemporaryDirectory(prefix="5g-mobile-identity-") as temporary:
        root = Path(temporary)
        for name, mcc, mnc in cases:
            testbed = load_yaml(ROOT / "testbed.yaml")
            testbed["mobileNetwork"]["plmn"] = {"mcc": mcc, "mnc": mnc}
            testbed_path = root / (name + ".yaml")
            dump_yaml(testbed_path, testbed)
            config_dir = render_and_check(testbed_path, root / "rendered", name)
            verify_case(testbed, config_dir, expected_gpsis)
            identities = resolve_mobile_identities(testbed)
            print(
                "OK case={} plmn={}/{} first-supi={} group={}".format(
                    name,
                    mcc,
                    mnc,
                    identities["supis"][0],
                    identities["internalGroupId"],
                )
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
