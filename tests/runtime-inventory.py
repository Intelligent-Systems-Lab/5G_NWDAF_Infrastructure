#!/usr/bin/env python3
"""Exercise protocol config rendering and scenario-derived runtime selection."""

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "host"))

from configlib import (  # noqa: E402
    expected_runtime_inventory,
    image_scenario_contract,
    load_yaml,
    resolve_fault_targets,
)


TESTBED = ROOT / "testbed.protocol-hierarchical.yaml"
SCENARIO_ROOT = ROOT / "experiments" / "protocol-hierarchical"


def run(*command):
    subprocess.run(command, cwd=ROOT, check=True)


def main():
    testbed = load_yaml(TESTBED)
    formal = sorted(SCENARIO_ROOT.glob("*/*yaml"))
    formal = [path for path in formal if path.name != "smoke.yaml"]
    assert len(formal) == 8
    observed = set()
    for path in formal:
        scenario = load_yaml(path)
        image_scenario_contract(scenario)
        observed.add(
            (
                scenario["workload"]["dataset"],
                scenario["experiment"]["condition"],
            )
        )
        runtime = expected_runtime_inventory(testbed, scenario)
        assert runtime["deploymentKind"] == "protocol-hierarchical"
        assert runtime["coordinatorContainer"] in runtime["hostContainers"]
        if scenario.get("fault") is not None:
            targets = resolve_fault_targets(testbed, scenario)
            assert targets["targets"]
    assert observed == {
        (dataset, condition)
        for dataset in ("mnist", "cifar10")
        for condition in ("E0", "E1", "E2a", "E2b")
    }

    with tempfile.TemporaryDirectory(prefix="5g-runtime-inventory-") as temporary:
        for device in ("cpu", "gpu"):
            name = "smoke-{}".format(device)
            run(
                sys.executable,
                "scripts/host/config-render.py",
                "--testbed",
                str(TESTBED),
                "--name",
                name,
                "--scenario",
                str(SCENARIO_ROOT / "mnist" / "smoke.yaml"),
                "--output-root",
                temporary,
                "--ml-device",
                device,
            )
            config_dir = Path(temporary) / name
            run(
                sys.executable,
                "scripts/host/config-check.py",
                "--testbed",
                str(TESTBED),
                "--config-dir",
                str(config_dir),
            )
            run(
                sys.executable,
                "scripts/host/ml-compose-check.py",
                "--testbed",
                str(TESTBED),
                "--config-dir",
                str(config_dir),
            )

    print("RUNTIME_INVENTORY_TEST formal_scenarios=8 render=mnist-smoke status=passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
