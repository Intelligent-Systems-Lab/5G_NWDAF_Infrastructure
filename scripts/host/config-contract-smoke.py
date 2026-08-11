#!/usr/bin/env python3
"""Prove that config-check rejects representative cross-config drift."""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

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
    parser.add_argument("--config-dir", default="config/generated/fl-closure-smoke")
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
            "pyanlf-device",
            "pyanlf-a.yaml",
            lambda value: value["model"].update({"device": "cuda:0"}),
            "pyanlf-a model device",
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
            "consumer-callback",
            "consumer.yaml",
            lambda value: value["callback"].update(
                {"advertisedUri": "http://192.168.57.32:9090/wrong"}
            ),
            "consumer callback URI",
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
            "manifest-baseline",
            "manifest.yaml",
            lambda value: value["generated"].update(
                {"baselineHash": "0" * 64}
            ),
            "manifest baseline hash",
        ),
        (
            "manifest-generator",
            "manifest.yaml",
            lambda value: value["generated"].update(
                {"generatorSourceHash": "0" * 64}
            ),
            "manifest config generator hash",
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
