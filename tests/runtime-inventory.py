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

from configlib import (  # noqa: E402
    expected_runtime_inventory,
    image_scenario_contract,
    protocol_topology,
    resolve_branch_replacement,
    selected_component_paths,
)


def run(*command, check=True):
    return subprocess.run(
        [str(item) for item in command], cwd=ROOT, text=True,
        capture_output=True, check=check,
    )


def render(output_root, testbed, name, scenario=LEGACY_SCENARIO, device="cpu"):
    run(
        sys.executable, ROOT / "scripts/host/config-render.py",
        "--testbed", testbed, "--name", name, "--scenario", scenario,
        "--output-root", output_root, "--ml-device", device, "--webconsole", "false",
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
    protocol_runtime = expected_runtime_inventory(protocol_definition)
    assert protocol_runtime["capacity"]["gpuParticipants"] == 7
    assert protocol_runtime["capacity"]["minimumGpuMemoryMiB"] == 8192
    accelerator_services = {
        name
        for name, service in protocol_definition["mlRuntime"]["services"].items()
        if service["device"] == "cuda:0"
    }
    assert accelerator_services == {
        "pymtlf-root",
        "pymtlf-leaf-a1", "pymtlf-leaf-a2",
        "pymtlf-leaf-b1", "pymtlf-leaf-b2",
        "pymtlf-leaf-c1", "pymtlf-leaf-c2",
    }
    assert protocol_runtime["capacity"]["hostContainerMemoryMiB"] == 16384
    for name in accelerator_services - {"pymtlf-root"}:
        assert protocol_definition["mlRuntime"]["services"][name]["memoryMiB"] == 2048
    normal_scenario = yaml.safe_load(
        (ROOT / "experiments/protocol-hierarchical/mnist/scenario.yaml").read_text(
            encoding="utf-8"
        )
    )
    replacement_scenario = yaml.safe_load(
        (
            ROOT
            / "experiments/protocol-hierarchical/branch-replacement/mnist/scenario.yaml"
        ).read_text(encoding="utf-8")
    )
    image_scenario_contract(normal_scenario)
    image_scenario_contract(replacement_scenario)
    assert normal_scenario["training"]["localEpochs"] == 1
    assert replacement_scenario["partition"]["samplesPerLeaf"] == 8000
    assert replacement_scenario["training"]["localEpochs"] == 32
    for group in protocol_definition["analytics"]["protocolTopology"]["branchGroups"]:
        assert all(
            candidate["reportAfter"]["unit"] == "round"
            for candidate in group["branches"]
        )
        assert all("reportAfter" not in candidate for candidate in group["leaves"])
    normal_topology = protocol_topology(
        protocol_definition, normal_scenario["training"]["localEpochs"]
    )
    replacement_topology = protocol_topology(
        protocol_definition, replacement_scenario["training"]["localEpochs"]
    )
    for topology, expected_epochs in ((normal_topology, 1), (replacement_topology, 32)):
        source_groups = protocol_definition["analytics"]["protocolTopology"][
            "branchGroups"
        ]
        for source_group, group in zip(source_groups, topology["branch_groups"]):
            assert [candidate["report_after"] for candidate in group["branches"]] == [
                {
                    "count": candidate["reportAfter"]["count"],
                    "unit": "round",
                }
                for candidate in source_group["branches"]
            ]
            assert all(
                candidate["report_after"] == {
                    "count": expected_epochs,
                    "unit": "epoch",
                }
                for candidate in group["leaves"]
            )
    resolved_fault = resolve_branch_replacement(protocol_definition, replacement_scenario)
    assert resolved_fault["primary"]["unit"] == "nwdaf-branch-a-primary"
    assert resolved_fault["replacement"]["unit"] == "nwdaf-branch-a-replacement"
    invalid_scenario = copy.deepcopy(replacement_scenario)
    invalid_scenario["training"]["device"] = "cuda:0"
    try:
        image_scenario_contract(invalid_scenario)
    except ValueError:
        pass
    else:
        raise AssertionError("scenario duplicated TESTBED-owned device selection")
    invalid_scenario = copy.deepcopy(normal_scenario)
    del invalid_scenario["training"]["localEpochs"]
    try:
        image_scenario_contract(invalid_scenario)
    except ValueError:
        pass
    else:
        raise AssertionError("image scenario accepted a missing local epoch source")
    invalid_scenario = copy.deepcopy(normal_scenario)
    invalid_scenario["training"]["localEpochs"] = 2
    try:
        image_scenario_contract(invalid_scenario)
    except ValueError:
        pass
    else:
        raise AssertionError("normal scenario accepted a non-smoke local epoch count")
    invalid_definition = copy.deepcopy(protocol_definition)
    invalid_definition["analytics"]["protocolTopology"]["branchGroups"][0][
        "leaves"
    ][0]["reportAfter"] = {"count": 1, "unit": "epoch"}
    try:
        protocol_topology(invalid_definition, 1)
    except ValueError:
        pass
    else:
        raise AssertionError("protocol topology accepted TESTBED-owned Leaf epochs")
    invalid_definition = copy.deepcopy(protocol_definition)
    del invalid_definition["analytics"]["protocolTopology"]["branchGroups"][0][
        "branches"
    ][0]["reportAfter"]
    try:
        protocol_topology(invalid_definition, 1)
    except ValueError:
        pass
    else:
        raise AssertionError("protocol topology accepted a Branch without reportAfter")
    for field, value in (("samplesPerLeaf", 100), ("localEpochs", 1)):
        invalid_scenario = copy.deepcopy(replacement_scenario)
        owner = (
            invalid_scenario["partition"]
            if field == "samplesPerLeaf"
            else invalid_scenario["training"]
        )
        owner[field] = value
        try:
            image_scenario_contract(invalid_scenario)
        except ValueError:
            pass
        else:
            raise AssertionError("replacement scenario accepted an undersized workload")
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
            ("testbed.yaml", "production", LEGACY_SCENARIO, "cpu"),
            (
                "testbed.protocol-hierarchical.yaml", "protocol-mnist",
                "experiments/protocol-hierarchical/mnist/scenario.yaml", "cpu",
            ),
            (
                "testbed.protocol-hierarchical.yaml", "protocol-cifar10",
                "experiments/protocol-hierarchical/cifar10/scenario.yaml", "cpu",
            ),
            (
                "testbed.protocol-hierarchical.yaml", "replacement-mnist",
                "experiments/protocol-hierarchical/branch-replacement/mnist/scenario.yaml",
                "gpu",
            ),
            (
                "testbed.protocol-hierarchical.yaml", "replacement-cifar10",
                "experiments/protocol-hierarchical/branch-replacement/cifar10/scenario.yaml",
                "gpu",
            ),
        )
        for testbed, name, scenario, device in cases:
            config_dir = render(output_root, testbed, name, scenario, device)
            manifest_path = config_dir / "manifest.yaml"
            original = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            units = [item["unit"] for item in original["runtime"]["guestServices"]]
            if original["runtime"]["deploymentKind"] == "protocol-hierarchical":
                assert units[:3] == ["mongodb", "nrf", "adrf"]
                assert all(unit.startswith("nwdaf-") for unit in units[3:])
                assert original["runtime"]["guestMachines"] == [
                    "core", "path-a", "path-b", "path-c"
                ]
                expected_gpu_participants = 7 if device == "gpu" else 0
                assert (
                    original["runtime"]["capacity"]["gpuParticipants"]
                    == expected_gpu_participants
                )
                assert original["scenario"]["training"]["acceptedRounds"] == (
                    8 if original["scenario"].get("fault") else 2
                )
                expected_local_epochs = 32 if original["scenario"].get("fault") else 1
                assert original["scenario"]["training"]["localEpochs"] == (
                    expected_local_epochs
                )
                coordinator = original["runtime"]["coordinatorContainer"]
                root_config = yaml.safe_load(
                    (config_dir / (coordinator + ".yaml")).read_text(encoding="utf-8")
                )
                assert root_config["federated_learning"]["server"]["client_training"][
                    "epochs"
                ] == expected_local_epochs
                rendered_topology = yaml.safe_load(
                    (config_dir / "topology/protocol-hierarchical.yaml").read_text(
                        encoding="utf-8"
                    )
                )
                for group in rendered_topology["branch_groups"]:
                    assert all(
                        candidate["report_after"] == {
                            "count": expected_local_epochs,
                            "unit": "epoch",
                        }
                        for candidate in group["leaves"]
                    )
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
            run(
                sys.executable, ROOT / "scripts/host/ml-compose-check.py",
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
