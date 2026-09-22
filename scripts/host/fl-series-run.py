#!/usr/bin/env python3
"""Call the existing config and single-run targets sequentially for selected formal runs."""

import argparse
import subprocess
from pathlib import Path

from fl_experiment import validate_run_name


ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = {
    "mnist": {
        "E0": "formal-baseline.yaml",
        "E1": "formal-replacement.yaml",
        "E2a": "formal-reparent.yaml",
        "E2b": "formal-partial-reparent.yaml",
    },
    "cifar10": {
        "E0": "all-class-skew-baseline.yaml",
        "E1": "all-class-skew-replacement.yaml",
        "E2a": "all-class-skew-reparent.yaml",
        "E2b": "all-class-skew-partial-reparent.yaml",
    },
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", choices=SCENARIOS, required=True)
    parser.add_argument("--seeds", required=True, help="comma-separated selected seeds")
    parser.add_argument("--conditions", required=True, help="comma-separated selected conditions")
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--testbed", default="testbed.protocol-hierarchical.yaml")
    args = parser.parse_args()
    try:
        seeds = [int(value) for value in args.seeds.split(",")]
    except ValueError:
        parser.error("seeds must be comma-separated integers")
    conditions = args.conditions.split(",")
    if any(seed not in range(1, 6) for seed in seeds):
        parser.error("this paper series selects seeds 1 through 5")
    if any(condition not in SCENARIOS[args.workload] for condition in conditions):
        parser.error("conditions must be E0, E1, E2a or E2b")
    if not args.run_prefix.replace("-", "").replace("_", "").isalnum():
        parser.error("run-prefix must contain only letters, digits, hyphens and underscores")
    for seed in seeds:
        for condition in conditions:
            scenario = "experiments/protocol-hierarchical/{}/{}".format(
                args.workload, SCENARIOS[args.workload][condition]
            )
            identity = "{}-{}-s{}-{}".format(args.run_prefix, args.workload, seed, condition.lower())
            validate_run_name(identity)
            print("Starting {} from {}".format(identity, scenario), flush=True)
            subprocess.run([
                "make", "config-create", "TESTBED=" + args.testbed,
                "FROM=" + scenario, "NAME=" + identity, "SEED=" + str(seed),
                "DEVICE=gpu",
            ], cwd=ROOT, check=True)
            subprocess.run([
                "make", "dataset-generate", "TESTBED=" + args.testbed,
                "CONFIG_DIR=config/local/" + identity,
            ], cwd=ROOT, check=True)
            subprocess.run([
                "make", "fl-experiment-run", "TESTBED=" + args.testbed,
                "CONFIG_DIR=config/local/" + identity, "RUN_NAME=" + identity,
            ], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
