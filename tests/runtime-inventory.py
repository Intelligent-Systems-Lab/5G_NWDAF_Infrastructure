#!/usr/bin/env python3
"""Regression checks for exact TESTBED-derived runtime inventory."""

import copy
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SCENARIO = "experiments/examples/fl-closure-smoke/scenario.yaml"
sys.path.insert(0, str(ROOT / "scripts" / "host"))

from configlib import expected_runtime_inventory, selected_component_paths  # noqa: E402


def run(*command, check=True):
    return subprocess.run(
        [str(item) for item in command], cwd=ROOT, text=True,
        capture_output=True, check=check,
    )


def render(output_root, testbed, name, scenario=LEGACY_SCENARIO):
    run(
        sys.executable, ROOT / "scripts/host/config-render.py",
        "--testbed", testbed, "--name", name, "--scenario", scenario,
        "--output-root", output_root, "--ml-device", "cpu", "--webconsole", "false",
    )
    return output_root / name


def rejected(testbed, config_dir):
    result = run(
        sys.executable, ROOT / "scripts/host/config-check.py",
        "--testbed", testbed, "--config-dir", config_dir, check=False,
    )
    if result.returncode == 0:
        raise AssertionError("tampered runtime inventory was accepted")


def main():
    renderer = (ROOT / "scripts/host/config-render.py").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    dockerfile = (ROOT / "containers/ml/Dockerfile").read_text(encoding="utf-8")
    component_locks = {
        item["path"]: item["commit"]
        for item in yaml.safe_load(
            (ROOT / "components.lock.yaml").read_text(encoding="utf-8")
        )["components"]
    }
    protocol_definition = yaml.safe_load(
        (ROOT / "testbed.protocol-hierarchical.yaml").read_text(encoding="utf-8")
    )
    protocol_components = selected_component_paths(
        protocol_definition, expected_runtime_inventory(protocol_definition)
    )
    assert protocol_components == [
        "ML/PyMTLF", "NFs/adrf", "NFs/nrf", "NFs/nwdaf",
    ]
    for path in protocol_components:
        expected = component_locks[path]
        actual = run("git", "-C", ROOT / path, "rev-parse", "HEAD").stdout.strip()
        assert actual == expected, (
            "component lock does not match checked-out revision: {} expected={} actual={}".format(
                path, expected, actual
            )
        )
    for forbidden in ("static-config-render.py", "deployments/", "DEPLOYMENT"):
        assert forbidden not in renderer + makefile, forbidden
    for testbed_path in (
        "testbed.yaml", "testbed.static-flat.yaml", "testbed.static-hierarchical.yaml",
        "testbed.protocol-hierarchical.yaml",
    ):
        definition = yaml.safe_load((ROOT / testbed_path).read_text(encoding="utf-8"))
        for service in definition["mlRuntime"]["services"].values():
            target = service["volume"]["target"]
            assert target in dockerfile, "image does not pre-own volume target: " + target

    with tempfile.TemporaryDirectory(prefix="5g-runtime-inventory-") as temporary:
        output_root = Path(temporary)
        # Exercise the shared exact-inventory guard with one complete fixture;
        # compatibility across historical profiles is outside this test.
        cases = (
            ("testbed.yaml", "production", LEGACY_SCENARIO),
            (
                "testbed.protocol-hierarchical.yaml", "protocol-mnist",
                "experiments/protocol-hierarchical/mnist/scenario.yaml",
            ),
            (
                "testbed.protocol-hierarchical.yaml", "protocol-cifar10",
                "experiments/protocol-hierarchical/cifar10/scenario.yaml",
            ),
        )
        for testbed, name, scenario in cases:
            config_dir = render(output_root, testbed, name, scenario)
            manifest_path = config_dir / "manifest.yaml"
            original = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            units = [item["unit"] for item in original["runtime"]["guestServices"]]
            if original["runtime"]["deploymentKind"] == "protocol-hierarchical":
                assert units[:3] == ["mongodb", "nrf", "adrf"]
                assert all(unit.startswith("nwdaf-") for unit in units[3:])
                assert original["runtime"]["guestMachines"] == [
                    "core", "path-a", "path-b", "path-c"
                ]
            else:
                first_upf = min(units.index(unit) for unit in units if unit.startswith("upf-"))
                first_nwdaf = min(units.index(unit) for unit in units if unit.startswith("nwdaf-"))
                first_gnb = min(units.index(unit) for unit in units if unit.startswith("gnb-"))
                first_ue = min(units.index(unit) for unit in units if unit.startswith("ue"))
                assert units.index("mongodb") < units.index("nrf") < units.index("amf")
                assert units.index("amf") < first_upf < units.index("smf")
                assert units.index("smf") < units.index("adrf") < first_nwdaf
                assert first_nwdaf < first_gnb < first_ue

            tampered = copy.deepcopy(original)
            tampered["runtime"]["hostContainers"] = []
            manifest_path.write_text(yaml.safe_dump(tampered, sort_keys=False), encoding="utf-8")
            rejected(testbed, config_dir)

            tampered = copy.deepcopy(original)
            tampered["runtime"]["guestServices"].append(
                {"machine": "core", "unit": "nwdaf-unexpected", "kind": "nwdaf"}
            )
            manifest_path.write_text(yaml.safe_dump(tampered, sort_keys=False), encoding="utf-8")
            rejected(testbed, config_dir)

            tampered = copy.deepcopy(original)
            foreign_volume = {
                "name": "foreign-topology-state",
                "image": "5g-nwdaf-infrastructure/pymtlf:local",
            }
            tampered["runtime"]["mlVolumes"].append(foreign_volume)
            tampered["runtime"]["resetScope"]["mlVolumes"].append(foreign_volume)
            manifest_path.write_text(yaml.safe_dump(tampered, sort_keys=False), encoding="utf-8")
            rejected(testbed, config_dir)

            manifest_path.write_text(yaml.safe_dump(original, sort_keys=False), encoding="utf-8")
            run(
                sys.executable, ROOT / "scripts/host/config-check.py",
                "--testbed", testbed, "--config-dir", config_dir,
            )
            compose = yaml.safe_load((config_dir / "compose.yaml").read_text(encoding="utf-8"))
            assert list(compose["services"]) == original["runtime"]["hostContainers"]
            expected_builds = 2 if original["runtime"]["deploymentKind"] == "production-flat" else 1
            assert len({service["image"] for service in compose["services"].values()}) == expected_builds
            assert list(compose["volumes"]) == [
                item["name"] for item in original["runtime"]["mlVolumes"]
            ]
            for service in compose["services"].values():
                target = service["build"]["target"]
                expected_revision = component_locks[
                    "ML/PyAnLF" if target == "pyanlf" else "ML/PyMTLF"
                ]
                assert service["build"]["args"]["COMPONENT_REVISION"] == expected_revision
            if original["runtime"]["deploymentKind"] in ("static-flat", "static-hierarchical"):
                topology_name = original["runtime"]["deploymentKind"] + ".yaml"
                for service in compose["services"].values():
                    assert any(
                        volume.get("source") == "${CONFIG_DIR:-.}/topology/" + topology_name
                        and volume.get("target") == "/etc/5g-nwdaf/topology/" + topology_name
                        and volume.get("read_only") is True
                        for volume in service["volumes"]
                    ), "static PyMTLF service is missing its generated topology mount"
            if original["runtime"]["deploymentKind"] == "protocol-hierarchical":
                root_service = original["runtime"]["coordinatorContainer"]
                for service_name, service in compose["services"].items():
                    topology_mounts = [
                        volume for volume in service["volumes"]
                        if volume.get("target") == "/etc/5g-nwdaf/topology/protocol-hierarchical.yaml"
                    ]
                    assert len(topology_mounts) == (1 if service_name == root_service else 0)
            print("OK exact-runtime testbed={} services={}".format(
                testbed, len(compose["services"])
            ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
