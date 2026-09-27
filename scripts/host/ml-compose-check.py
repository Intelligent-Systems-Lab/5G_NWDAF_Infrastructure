#!/usr/bin/env python3
"""Validate resolved Compose service wiring against the testbed definition."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from configlib import (
    ROOT,
    deployment_kind,
    image_dataset_name,
    load_runtime_manifest,
    load_yaml,
    nwdaf_definitions,
    resolve_config_dir,
    resolve_config_scenario,
    resolve_ml_bind_address,
    resolve_ml_device_policy,
    resolve_path,
    selected_seed_source,
)


class Check:
    def __init__(self):
        self.errors = []

    def equal(self, label, actual, expected):
        if actual != expected:
            self.errors.append("{}: expected {!r}, got {!r}".format(label, expected, actual))

    def true(self, label, condition):
        if not condition:
            self.errors.append(label)


def compose_config(config_dir, bind_address):
    command = [
        "docker", "compose", "-f", str(config_dir / "compose.yaml"),
    ]
    command.extend(["config", "--format", "json"])
    environment = dict(os.environ)
    environment.update(
        {
            "CONFIG_DIR": str(config_dir),
            "CONFIG_SET_NAME": config_dir.name,
            "CONFIG_HASH": "static-check",
            "ML_BIND_ADDRESS": bind_address,
            "REPOSITORY_ROOT": str(ROOT),
        }
    )
    return json.loads(subprocess.check_output(command, text=True, env=environment))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--testbed", required=True)
    parser.add_argument("--config-dir")
    args = parser.parse_args()

    testbed = load_yaml(resolve_path(args.testbed))
    config_dir = resolve_config_dir(testbed, args.config_dir)
    device_policy = resolve_ml_device_policy(config_dir)
    bind_address = resolve_ml_bind_address(testbed)
    resolved = compose_config(config_dir, bind_address)
    all_services = resolved.get("services", {})
    manifest = load_runtime_manifest(config_dir)
    deployment_kind(testbed)
    _scenario_path, scenario = resolve_config_scenario(config_dir)
    selected_names = manifest["runtime"]["hostContainers"]
    services = {name: all_services.get(name, {}) for name in selected_names}
    expected_services = {
        name: testbed["mlRuntime"]["services"][name]
        for name in selected_names
    }
    component_locks = {
        item["path"]: item["commit"] for item in load_yaml(ROOT / "components.lock.yaml")["components"]
    }
    revision = component_locks["ML/PyMTLF"]
    check = Check()
    check.equal("Compose exact service set", sorted(all_services), sorted(selected_names))
    check.equal("Compose service set", sorted(services), sorted(expected_services))
    check.equal("Compose ML network driver", resolved.get("networks", {}).get("ml", {}).get("driver"), "bridge")

    for name, expected in expected_services.items():
        service = services.get(name, {})
        native = load_yaml(config_dir / (name + ".yaml"))
        if native.get("federated_learning", {}).get("client") is not None:
            configured_device = native["federated_learning"]["client"]["training"]["device"]
        else:
            configured_device = (
                native.get("federated_learning", {})
                .get("experiment_recording", {})
                .get("validation", {})
                .get("device", "cpu")
            )
        check.equal(name + " build target", service.get("build", {}).get("target"), "pymtlf")
        check.equal(name + " image", service.get("image"), "5g-nwdaf-infrastructure/pymtlf:local")
        check.equal(name + " source revision", service.get("build", {}).get("args", {}).get("COMPONENT_REVISION"), revision)
        check.true(name + " root filesystem must be read-only", service.get("read_only") is True)
        check.true(name + " must drop all capabilities", "ALL" in service.get("cap_drop", []))
        check.true(name + " must set no-new-privileges", "no-new-privileges:true" in service.get("security_opt", []))
        check.true(name + " healthcheck missing", bool(service.get("healthcheck", {}).get("test")))
        check.equal(name + " log driver", service.get("logging", {}).get("driver"), "local")
        check.true(name + " memory limit missing", int(service.get("mem_limit", 0)) > 0)
        labels = service.get("labels", {})
        check.equal(name + " service label", labels.get("io.5g-nwdaf.service"), name)
        check.equal(name + " config-set label", labels.get("io.5g-nwdaf.config-set"), config_dir.name)
        check.equal(name + " config-hash label", labels.get("io.5g-nwdaf.config-hash"), "static-check")

        ports = service.get("ports", [])
        check.equal(name + " published port count", len(ports), 1)
        if ports:
            check.equal(name + " container port", ports[0].get("target"), expected["containerPort"])
            check.equal(name + " published port", int(ports[0].get("published")), expected["publishedPort"])
            check.equal(name + " bind address", ports[0].get("host_ip"), bind_address)

        mounts = service.get("volumes", [])
        config_mounts = [item for item in mounts if item.get("target") == "/etc/5g-nwdaf/config.yaml"]
        check.equal(name + " config mount count", len(config_mounts), 1)
        if config_mounts:
            check.equal(name + " config mount type", config_mounts[0].get("type"), "bind")
            check.equal(name + " config source", Path(config_mounts[0]["source"]), (config_dir / (name + ".yaml")).resolve())
            check.true(name + " config must be read-only", config_mounts[0].get("read_only") is True)
        check.true(
            name + " writable data volume missing",
            any(
                item.get("type") == "volume"
                and item.get("target") == (
                    "/var/lib/5g-nwdaf-infrastructure/" + name
                )
                for item in mounts
            ),
        )

        expected_gpu = configured_device.startswith("cuda")
        expected_runtime = "nvidia" if expected_gpu else None
        check.equal(name + " OCI runtime", service.get("runtime"), expected_runtime)
        environment = service.get("environment", {})
        expected_visible_devices = (
            "nvidia.com/gpu=all" if expected_gpu else None
        )
        expected_driver_capabilities = (
            "compute,utility" if expected_gpu else None
        )
        check.equal(name + " CDI selector", environment.get("NVIDIA_VISIBLE_DEVICES"), expected_visible_devices)
        check.equal(name + " NVIDIA driver capabilities", environment.get("NVIDIA_DRIVER_CAPABILITIES"), expected_driver_capabilities)
        check.equal(name + " host device mapping", service.get("devices", []), [])
        check.true(name + " must not use a Compose GPU request", not service.get("gpus"))

        if name == manifest["runtime"]["coordinatorContainer"]:
            selected_seed = selected_seed_source(scenario)
            expected_seed_environment = {
                "PYMTLF_SEED_SOURCE": (
                    selected_seed[1] if selected_seed else
                    "/opt/app/seed_models/image_classification/"
                    + scenario["workload"]["dataset"]
                ),
                "PYMTLF_SEED_MODEL_ID": str(
                    scenario["workload"]["seedModelId"]
                ),
                "PYMTLF_SEED_INTEROPERABILITY": (
                    scenario["workload"]["modelInteroperability"]
                ),
                "PYMTLF_SEED_ARTIFACT_KEY": (
                    scenario["workload"]["seedArtifactKey"]
                ),
            }
        else:
            expected_seed_environment = {}
        for key, value in expected_seed_environment.items():
            check.equal(name + " " + key, environment.get(key), value)

        definition = next(
            item for item in nwdaf_definitions(testbed)
            if item["backends"]["mtlf"] == name
        )
        selected_seed = selected_seed_source(scenario)
        selected_mounts = [
            item for item in mounts
            if item.get("target") == "/opt/app/seed_models/selected"
        ]
        check.equal(
            name + " selected seed mount count", len(selected_mounts),
            1 if selected_seed and definition["role"] == "root" else 0,
        )
        if selected_seed and definition["role"] == "root" and selected_mounts:
            check.equal(name + " selected seed source", Path(selected_mounts[0]["source"]), selected_seed[0])
            check.true(name + " selected seed must be read-only", selected_mounts[0].get("read_only") is True)
        topology_mounts = [
            item for item in mounts
            if item.get("target") == "/etc/5g-nwdaf/topology/protocol-hierarchical.yaml"
        ]
        check.equal(
            name + " topology mount count", len(topology_mounts),
            1 if definition["role"] == "root" else 0,
        )
        dataset_root = ROOT / ".generated" / "image-datasets" / image_dataset_name(scenario)
        if definition["role"] == "root":
            expected_dataset_mount = (
                dataset_root / "validation.npz", "/data/validation.npz"
            )
        elif definition["role"] == "leaf":
            expected_dataset_mount = (
                dataset_root / "leaves" / (definition["unit"] + ".npz"),
                "/data/train.npz",
            )
        else:
            expected_dataset_mount = None
        data_mounts = [
            item for item in mounts if item.get("target", "").startswith("/data/")
        ]
        check.equal(
            name + " dataset mount count", len(data_mounts),
            1 if expected_dataset_mount else 0,
        )
        if expected_dataset_mount and data_mounts:
            source, target = expected_dataset_mount
            check.equal(name + " dataset source", Path(data_mounts[0]["source"]), source)
            check.equal(name + " dataset target", data_mounts[0]["target"], target)
            check.true(name + " dataset must be read-only", data_mounts[0].get("read_only") is True)

    if check.errors:
        for error in check.errors:
            print("ERROR: " + error, file=sys.stderr)
        return 1
    print("OK compose device_policy={} services={} config={}".format(
        device_policy, len(services), config_dir
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
