#!/usr/bin/env python3
"""Check that dataset summaries explain raw-window aggregation."""

import copy
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "host"))

from configlib import load_yaml
from dataset import render_summary
from datasetlib import resolve_dataset_spec


def manifest_for(spec):
    paths = {}
    for name, profile in spec["paths"].items():
        paths[name] = {
            "ueIps": profile["ueIps"],
            "rows": 100,
            "bytes": 2048,
            "sha256": "a" * 64,
            "minTimestamp": 0,
            "maxTimestamp": 99,
        }
    return {"paths": paths}


def main():
    testbed = load_yaml(ROOT / "testbed.yaml")
    baseline = resolve_dataset_spec(testbed, ROOT / "config" / "default")
    output = render_summary(baseline, manifest_for(baseline), Path("/datasets/baseline"))
    for evidence in (
        "raw_window=1s observation=30s raw_windows_per_observation=30",
        "period=90s observations_per_report=3",
        "warm_start_boundary=900s traffic_transition=1800s",
        "earliest_after_start=1170s bounded_after_start=1290s bounded_closure=3090s",
    ):
        if evidence not in output:
            raise SystemExit("baseline summary omitted {!r}".format(evidence))

    five_second = copy.deepcopy(baseline)
    for profile in five_second["paths"].values():
        profile["samplingIntervalSeconds"] = 5
        profile["monitorReportPeriodSeconds"] = 30
    output = render_summary(
        five_second, manifest_for(five_second), Path("/datasets/five-second")
    )
    if "raw_window=1s observation=5s raw_windows_per_observation=5" not in output:
        raise SystemExit("five-second aggregation is not explicit")
    if "period=30s observations_per_report=6" not in output:
        raise SystemExit("five-second report capacity is not explicit")

    print("DATASET_SUMMARY_TEST status=passed aggregation=1s-to-5s-and-30s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
