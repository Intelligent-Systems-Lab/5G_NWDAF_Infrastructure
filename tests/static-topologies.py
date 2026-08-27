#!/usr/bin/env python3
"""Exercise both generated static topology contracts without changing runtime state."""

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "host"))
from configlib import dump_yaml, load_yaml
from datasetlib import resolve_dataset_spec


ROOT = Path(__file__).resolve().parents[1]


def run(*command, check=True):
    return subprocess.run(
        [str(item) for item in command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


def main():
    scenario = "experiments/examples/fl-closure-smoke/scenario.yaml"
    with tempfile.TemporaryDirectory(prefix="5g-static-topologies-") as temporary:
        output_root = Path(temporary)
        definitions = []
        dataset_ids = []
        for deployment, testbed_file, expected_nwdafs in (
            ("static-flat", "testbed.static-flat.yaml", 5),
            ("static-hierarchical", "testbed.static-hierarchical.yaml", 7),
        ):
            definition = yaml.safe_load((ROOT / testbed_file).read_text(encoding="utf-8"))
            definitions.append(definition)
            run(
                sys.executable,
                ROOT / "scripts/host/config-render.py",
                "--testbed", testbed_file,
                "--name", deployment,
                "--scenario", scenario,
                "--output-root", output_root,
                "--ml-device", "cpu",
            )
            config_dir = output_root / deployment
            validated = run(
                sys.executable,
                ROOT / "scripts/host/config-check.py",
                "--testbed", testbed_file,
                "--config-dir", config_dir,
            )
            if "OK testbed=" not in validated.stdout:
                raise AssertionError("static config did not use the strict validation path")
            run(
                sys.executable,
                ROOT / "scripts/host/ml-compose-check.py",
                "--testbed", testbed_file,
                "--config-dir", config_dir,
            )
            with (config_dir / "manifest.yaml").open(encoding="utf-8") as stream:
                manifest = yaml.safe_load(stream)
            subscribers = json.loads(
                (config_dir / "subscriber/ue-subscribers.json").read_text(encoding="utf-8")
            )
            groups = json.loads(
                (config_dir / "subscriber/group-memberships.json").read_text(encoding="utf-8")
            )
            assert len(manifest["runtime"]["hostContainers"]) == expected_nwdafs
            assert manifest["runtime"]["deploymentKind"] == deployment
            assert manifest["runtime"]["capacity"]["hostContainerMemoryMiB"] in (6144, 8192)
            assert len(subscribers["subscribers"]) == 8
            assert [len(group["ueIdList"]) for group in groups["groups"]] == [2, 2, 2, 2]
            dataset_ids.append(resolve_dataset_spec(definition, config_dir)["datasetSetId"])

            owners = sorted(
                (
                    item["dataOwner"], item["backends"]["mtlf"]
                )
                for unit, item in definition["analytics"].items()
                if unit.startswith("nwdaf-")
                and item.get("role") in ("client", "leaf")
                and item.get("machine") == "path-a"
            )
            first_config = load_yaml(config_dir / (owners[0][1] + ".yaml"))
            second_path = config_dir / (owners[1][1] + ".yaml")
            second_original = load_yaml(second_path)
            second_config = copy.deepcopy(second_original)
            first_group = (
                first_config["federated_learning"]["client"]["training_data"]
                ["collection_profiles"][0]["target_ue"]["intGroupIds"]
            )
            second_config["federated_learning"]["client"]["training_data"] \
                ["collection_profiles"][0]["target_ue"]["intGroupIds"] = list(first_group)
            dump_yaml(second_path, second_config)
            try:
                resolve_dataset_spec(definition, config_dir)
            except ValueError as error:
                assert "exactly cover declared data-owner groups" in str(error)
            else:
                raise AssertionError("duplicate data-owner group was accepted")
            dump_yaml(second_path, second_original)

            topology_path = config_dir / "topology" / (deployment + ".yaml")
            topology = yaml.safe_load(topology_path.read_text(encoding="utf-8"))
            if deployment == "static-flat":
                topology["clients"] = topology["clients"][:-1]
            else:
                topology["branches"][0]["leaves"] = []
            topology_path.write_text(yaml.safe_dump(topology, sort_keys=False), encoding="utf-8")
            rejected = run(
                sys.executable,
                ROOT / "scripts/host/config-check.py",
                "--testbed", testbed_file,
                "--config-dir", config_dir,
                check=False,
            )
            if rejected.returncode == 0:
                raise AssertionError("tampered {} topology was accepted".format(deployment))
            print("OK deployment={} nwdafs={} ues=8 groups=4x2 tamper=rejected".format(
                deployment, expected_nwdafs
            ))
        assert len(set(dataset_ids)) == 1, "Flat/HFL dataset stimulus identity drifted"
        for field in ("machines", "networks", "mobileNetwork", "coreServices", "paths"):
            left = definitions[0][field]
            right = definitions[1][field]
            if field == "paths":
                left = {name: {key: value for key, value in path.items() if key != "subscriberNumbers"} for name, path in left.items()}
                right = {name: {key: value for key, value in path.items() if key != "subscriberNumbers"} for name, path in right.items()}
            assert left == right, "unexpected common TESTBED drift in {}".format(field)
    return 0


if __name__ == "__main__":
    sys.exit(main())
