#!/usr/bin/env python3
"""Prove that config-check rejects representative cross-config drift."""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "host"))

from configlib import ROOT, dump_yaml, load_yaml, resolve_path


def rewrite(path, update):
    value = load_yaml(path)
    update(value)
    dump_yaml(path, value)


def run_check(testbed, config_dir):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "host" / "config-check.py"),
            "--testbed",
            str(testbed),
            "--config-dir",
            str(config_dir),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--testbed", default="testbed.yaml")
    parser.add_argument("--config-dir", default="config/default")
    args = parser.parse_args()

    testbed = resolve_path(args.testbed).resolve()
    source = resolve_path(args.config_dir).resolve()
    baseline = run_check(testbed, source)
    if baseline.returncode:
        sys.stderr.write(baseline.stderr)
        raise SystemExit("source config must pass before negative contract checks")

    cases = (
        (
            "report-capacity",
            "pyanlf-a.yaml",
            lambda value: value["accuracy_monitor"].update(
                {"min_matched_predictions": 4}
            ),
            "monitor period cannot collect the required matched predictions",
        ),
        (
            "coordinator-self-origin",
            "pymtlf-c.yaml",
            lambda value: value["federated_learning"]["artifact_download"].update(
                {
                    "allowed_origins": value["federated_learning"][
                        "artifact_download"
                    ]["allowed_origins"][:-1]
                }
            ),
            "pymtlf-c client origins",
        ),
        (
            "gtp-interface",
            "upfcfg-a.yaml",
            lambda value: value["gtpu"]["ifList"][0].update(
                {"ifname": "wrong-interface"}
            ),
            "UPF a GTP interface",
        ),
        (
            "adrf-instance-id",
            "adrfcfg.yaml",
            lambda value: value["configuration"].update(
                {"nfInstanceId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}
            ),
            "ADRF NF instance ID",
        ),
        (
            "adrf-model-storage",
            "adrfcfg.yaml",
            lambda value: value["configuration"]["mlModelStorage"].update(
                {"localDirectory": "./storage/models"}
            ),
            "ADRF model storage",
        ),
        (
            "adrf-database",
            "adrfcfg.yaml",
            lambda value: value["configuration"]["mongodb"].update(
                {"name": "free5gc"}
            ),
            "ADRF MongoDB database",
        ),
        (
            "smf-nrf-registration",
            "smfcfg.yaml",
            lambda value: value["configuration"].update(
                {"nrfRegistrationEnabled": False}
            ),
            "SMF NRF registration",
        ),
        (
            "smf-urr-period",
            "smfcfg.yaml",
            lambda value: value["configuration"].update({"urrPeriod": 31}),
            "SMF URR period",
        ),
        (
            "gnb-cell-access-type",
            "ueransim/gnb-a.yaml",
            lambda value: value.update({"cellAccessType": "nr-leo"}),
            "gNB a cell access type",
        ),
        (
            "ue-network-namespace",
            "ueransim/ue1.yaml",
            lambda value: value.update({"useNamespace": True}),
            "UE1 network namespace",
        ),
        (
            "ue-derived-supi",
            "ueransim/ue1.yaml",
            lambda value: value.update({"supi": "imsi-001010000000001"}),
            "UE1 SUPI",
        ),
        (
            "ue-routing-derived-members",
            "uerouting.yaml",
            lambda value: value["ueRoutingInfo"]["path-a"].update(
                {"members": ["imsi-001010000000001"]}
            ),
            "UE routing Path a members",
        ),
        (
            "nssf-derived-plmn",
            "nssfcfg.yaml",
            lambda value: value["configuration"].update(
                {"supportedPlmnList": [{"mcc": "001", "mnc": "01"}]}
            ),
            "NSSF supported PLMN",
        ),
        (
            "smf-derived-tai",
            "smfcfg.yaml",
            lambda value: value["configuration"]["userplaneInformation"][
                "upNodes"
            ]["UPF-A"]["tais"][0].update(
                {"plmnId": {"mcc": "001", "mnc": "01"}}
            ),
            "SMF UPF a TAI",
        ),
        (
            "pyanlf-device",
            "pyanlf-a.yaml",
            lambda value: value["model"].update({"device": "cuda:0"}),
            "pyanlf-a model device",
        ),
        (
            "ml-device-policy",
            "manifest.yaml",
            lambda value: value["runtime"].update(
                {
                    "mlDevicePolicy": "gpu"
                    if value["runtime"]["mlDevicePolicy"] == "cpu"
                    else "cpu"
                }
            ),
            "pymtlf-a device",
        ),
        (
            "manifest-scenario-kind",
            "manifest.yaml",
            lambda value: value["scenario"].update({"kind": "wrong-kind"}),
            "manifest scenario kind",
        ),
        (
            "subscriber-fixture-path",
            "manifest.yaml",
            lambda value: value["subscriberData"].update(
                {"subscribers": "../fixtures/full-core/ue-subscribers.json"}
            ),
            "subscriberData.subscribers must select a file inside the config set",
        ),
        (
            "pyanlf-accuracy-timeout",
            "pyanlf-a.yaml",
            lambda value: value["accuracy_monitor"]["report_delivery"].update(
                {"request_timeout_seconds": 5}
            ),
            "pyanlf-a accuracy report delivery",
        ),
        (
            "pyanlf-analytics-timeout",
            "pyanlf-a.yaml",
            lambda value: value["analytics"]["report_delivery"].update(
                {"request_timeout_seconds": 5}
            ),
            "pyanlf-a analytics report delivery",
        ),
        (
            "pyanlf-completion-timeout",
            "pyanlf-a.yaml",
            lambda value: value["runtime_completion_delivery"].update(
                {"request_timeout_seconds": 5}
            ),
            "pyanlf-a runtime completion delivery",
        ),
        (
            "fl-public-url",
            "pymtlf-a.yaml",
            lambda value: value["federated_learning"].update(
                {"public_base_url": "http://192.168.57.1:9999"}
            ),
            "pymtlf-a FL public URL",
        ),
        (
            "pymtlf-runtime-mode",
            "pymtlf-a.yaml",
            lambda value: value["runtime"].update({"mode": "fl_client"}),
            "pymtlf-a runtime mode",
        ),
        (
            "pymtlf-collection-trigger",
            "pymtlf-a.yaml",
            lambda value: value["federated_learning"]["client"].pop(
                "training_data"
            ),
            "pymtlf-a collection trigger",
        ),
        (
            "pymtlf-coordinator-orchestration",
            "pymtlf-c.yaml",
            lambda value: value["federated_learning"].pop("orchestration"),
            "pymtlf-c orchestration mode",
        ),
        (
            "pymtlf-private-training-trigger",
            "pymtlf-c.yaml",
            lambda value: value["federated_learning"]["training_trigger"][
                "private_api"
            ].update({"enabled": True}),
            "pymtlf-c private training trigger",
        ),
        (
            "consumer-callback",
            "consumer.yaml",
            lambda value: value["callback"].update(
                {"advertisedUri": "http://192.168.57.32:9090/wrong"}
            ),
            "consumer callback URI",
        ),
        (
            "consumer-derived-group",
            "consumer.yaml",
            lambda value: value["target"].update(
                {"internalGroupId": "00000001-001-01-01"}
            ),
            "consumer group",
        ),
        (
            "consumer-unknown-field",
            "consumer.yaml",
            lambda value: value.update({"callbackPath": "/wrong"}),
            "consumer native validation failed",
        ),
        (
            "consumer-missing-field",
            "consumer.yaml",
            lambda value: value.pop("stateFile"),
            "consumer native validation failed",
        ),
        (
            "webconsole-endpoint",
            "webuicfg.yaml",
            lambda value: value["configuration"]["webServer"].update(
                {"ipv4Address": "0.0.0.0"}
            ),
            "WebConsole HTTP endpoint",
        ),
        (
            "webconsole-billing",
            "webuicfg.yaml",
            lambda value: value["configuration"]["billingServer"].update(
                {"enable": False}
            ),
            "WebConsole billing compatibility settings",
        ),
    )

    with tempfile.TemporaryDirectory(prefix="5g-config-contract-") as temporary:
        temporary_root = Path(temporary)
        for name, relative, update, expected in cases:
            candidate = temporary_root / name
            shutil.copytree(source, candidate)
            rewrite(candidate / relative, update)
            result = run_check(testbed, candidate)
            output = result.stdout + result.stderr
            if result.returncode == 0 or expected not in output:
                sys.stderr.write(output)
                raise SystemExit(
                    "negative check did not reject {} with {!r}".format(name, expected)
                )
            print("OK rejected={} evidence={!r}".format(name, expected))
    return 0


if __name__ == "__main__":
    sys.exit(main())
