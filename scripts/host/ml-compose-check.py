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
    load_runtime_manifest,
    load_yaml,
    nwdaf_definitions,
    resolve_config_dir,
    resolve_config_scenario,
    resolve_ml_bind_address,
    resolve_ml_device_policy,
    resolve_path,
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


def compose_config(mode, device_policy, config_dir, bind_address):
    command = [
        "docker", "compose", "-f", str(config_dir / "compose.yaml"),
    ]
    if mode == "cpu-smoke":
        command.extend(["-f", str(ROOT / "compose.cpu-smoke.yaml")])
    command.extend(["config", "--format", "json"])
    environment = dict(os.environ)
    environment.update(
        {
            "CONFIG_DIR": str(config_dir),
            "CONFIG_SET_NAME": mode,
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
    parser.add_argument("--mode", choices=("baseline", "cpu-smoke"), default="baseline")
    args = parser.parse_args()

    testbed = load_yaml(resolve_path(args.testbed))
    config_dir = resolve_config_dir(testbed, args.config_dir)
    device_policy = resolve_ml_device_policy(config_dir)
    bind_address = "127.0.0.1" if args.mode == "cpu-smoke" else resolve_ml_bind_address(testbed)
    resolved = compose_config(args.mode, device_policy, config_dir, bind_address)
    all_services = resolved.get("services", {})
    manifest = load_runtime_manifest(config_dir)
    kind = deployment_kind(testbed)
    _scenario_path, scenario = resolve_config_scenario(config_dir)
    selected_names = manifest["runtime"]["hostContainers"]
    services = {name: all_services.get(name, {}) for name in selected_names}
    expected_services = testbed["mlRuntime"]["services"]
    component_locks = {
        item["path"]: item["commit"] for item in load_yaml(ROOT / "components.lock.yaml")["components"]
    }
    revisions = {
        "pyanlf": component_locks["ML/PyAnLF"],
        "pymtlf": component_locks["ML/PyMTLF"],
    }
    check = Check()
    check.equal("Compose exact service set", sorted(all_services), sorted(selected_names))
    check.equal("Compose service set", sorted(services), sorted(expected_services))
    check.equal("Compose ML network driver", resolved.get("networks", {}).get("ml", {}).get("driver"), "bridge")

    for name, expected in expected_services.items():
        service = services.get(name, {})
        native = load_yaml(config_dir / (name + ".yaml"))
        if name.startswith("pyanlf-"):
            configured_device = native["model"]["device"]
        elif native.get("federated_learning", {}).get("client") is not None:
            configured_device = native["federated_learning"]["client"]["training"]["device"]
        else:
            configured_device = "cpu"
        image_type = expected["image"]
        check.equal(name + " build target", service.get("build", {}).get("target"), image_type)
        check.equal(name + " image", service.get("image"), "5g-nwdaf-infrastructure/{}:local".format(image_type))
        check.equal(name + " source revision", service.get("build", {}).get("args", {}).get("COMPONENT_REVISION"), revisions[image_type])
        check.true(name + " root filesystem must be read-only", service.get("read_only") is True)
        check.true(name + " must drop all capabilities", "ALL" in service.get("cap_drop", []))
        check.true(name + " must set no-new-privileges", "no-new-privileges:true" in service.get("security_opt", []))
        check.true(name + " healthcheck missing", bool(service.get("healthcheck", {}).get("test")))
        check.equal(name + " log driver", service.get("logging", {}).get("driver"), "local")
        check.true(name + " memory limit missing", int(service.get("mem_limit", 0)) > 0)
        labels = service.get("labels", {})
        check.equal(name + " service label", labels.get("io.5g-nwdaf.service"), name)
        check.equal(name + " config-set label", labels.get("io.5g-nwdaf.config-set"), args.mode)
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
                    "/opt/app/artifacts" if name.startswith("pyanlf-")
                    else "/var/lib/5g-nwdaf-infrastructure/" + name
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
        check.true(name + " must not use legacy GPU request", not service.get("gpus"))

        if name == manifest["runtime"]["coordinatorContainer"]:
            expected_seed_environment = {
                "PYMTLF_SEED_SOURCE": (
                    "/opt/app/seed_models/image_classification/"
                    + scenario["workload"]["dataset"]
                    if kind == "protocol-hierarchical"
                    else "/opt/app/seed_models/initial"
                ),
                "PYMTLF_SEED_MODEL_ID": str(
                    scenario["workload"]["seedModelId"]
                    if kind == "protocol-hierarchical" else 1
                ),
                "PYMTLF_SEED_INTEROPERABILITY": (
                    scenario["workload"]["modelInteroperability"]
                    if kind == "protocol-hierarchical" else "001122"
                ),
                "PYMTLF_SEED_ARTIFACT_KEY": (
                    scenario["workload"]["seedArtifactKey"]
                    if kind == "protocol-hierarchical"
                    else "a2c796a001e2da2461418f80b01d7d1e33f0e3349c2817d92286f09e67aa6bef"
                ),
            }
        else:
            expected_seed_environment = {}
        for key, value in expected_seed_environment.items():
            check.equal(name + " " + key, environment.get(key), value)

        if kind == "protocol-hierarchical":
            definition = next(
                item for item in nwdaf_definitions(testbed)
                if item["backends"]["mtlf"] == name
            )
            topology_mounts = [
                item for item in mounts
                if item.get("target") == "/etc/5g-nwdaf/topology/protocol-hierarchical.yaml"
            ]
            check.equal(
                name + " topology mount count", len(topology_mounts),
                1 if definition["role"] == "root" else 0,
            )
            dataset_root = ROOT / ".generated" / "image-datasets" / scenario["name"]
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

    if args.mode == "cpu-smoke":
        helper_source = (ROOT / "tests" / "support" / "pymtlf-smoke-health.py").resolve()
        for name in ("pymtlf-a", "pymtlf-b", "pymtlf-c"):
            service = services.get(name, {})
            helper_mounts = [
                item
                for item in service.get("volumes", [])
                if item.get("target") == "/opt/app/pymtlf-smoke-health.py"
            ]
            check.equal(name + " smoke helper mount count", len(helper_mounts), 1)
            if helper_mounts:
                check.equal(
                    name + " smoke helper source",
                    Path(helper_mounts[0]["source"]),
                    helper_source,
                )
                check.equal(
                    name + " smoke helper source type",
                    helper_mounts[0].get("type"),
                    "bind",
                )
                check.true(
                    name + " smoke helper must be read-only",
                    helper_mounts[0].get("read_only") is True,
                )

    if check.errors:
        for error in check.errors:
            print("ERROR: " + error, file=sys.stderr)
        return 1
    print("OK compose mode={} device_policy={} services={} config={}".format(
        args.mode, device_policy, len(services), config_dir
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
