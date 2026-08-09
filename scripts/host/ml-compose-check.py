#!/usr/bin/env python3
"""Validate resolved Compose service wiring against the testbed definition."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from configlib import ROOT, load_yaml, resolve_config_dir, resolve_ml_bind_address, resolve_path


class Check:
    def __init__(self):
        self.errors = []

    def equal(self, label, actual, expected):
        if actual != expected:
            self.errors.append("{}: expected {!r}, got {!r}".format(label, expected, actual))

    def true(self, label, condition):
        if not condition:
            self.errors.append(label)


def compose_config(mode, config_dir, bind_address):
    command = ["docker", "compose", "-f", str(ROOT / "compose.yaml")]
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
        }
    )
    return json.loads(subprocess.check_output(command, text=True, env=environment))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--testbed", default="testbed.yaml")
    parser.add_argument("--config-dir")
    parser.add_argument("--mode", choices=("baseline", "cpu-smoke"), default="baseline")
    args = parser.parse_args()

    testbed = load_yaml(resolve_path(args.testbed))
    config_dir = resolve_config_dir(testbed, args.config_dir)
    bind_address = "127.0.0.1" if args.mode == "cpu-smoke" else resolve_ml_bind_address(testbed)
    resolved = compose_config(args.mode, config_dir, bind_address)
    services = resolved.get("services", {})
    expected_services = testbed["mlRuntime"]["services"]
    component_locks = {
        item["path"]: item["commit"] for item in load_yaml(ROOT / "components.lock.yaml")["components"]
    }
    revisions = {
        "pyanlf": component_locks["ML/PyAnLF"],
        "pymtlf": component_locks["ML/PyMTLF"],
    }
    data_targets = {
        "pyanlf-a": "/opt/app/artifacts",
        "pyanlf-b": "/opt/app/artifacts",
        "pymtlf-a": "/var/lib/5g-nwdaf-infrastructure/pymtlf-a",
        "pymtlf-b": "/var/lib/5g-nwdaf-infrastructure/pymtlf-b",
        "pymtlf-c": "/var/lib/5g-nwdaf-infrastructure/pymtlf-c",
    }
    check = Check()
    check.equal("Compose service set", sorted(services), sorted(expected_services))
    check.equal("Compose ML network driver", resolved.get("networks", {}).get("ml", {}).get("driver"), "bridge")

    for name, expected in expected_services.items():
        service = services.get(name, {})
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
            any(item.get("type") == "volume" and item.get("target") == data_targets[name] for item in mounts),
        )

        expected_gpu = expected["device"].startswith("cuda") and args.mode == "baseline"
        cpu_override = args.mode == "cpu-smoke" and expected["device"].startswith("cuda")
        expected_runtime = "nvidia" if expected_gpu else ("runc" if cpu_override else None)
        check.equal(name + " OCI runtime", service.get("runtime"), expected_runtime)
        environment = service.get("environment", {})
        expected_visible_devices = (
            "nvidia.com/gpu=all" if expected_gpu else ("void" if cpu_override else None)
        )
        expected_driver_capabilities = (
            "compute,utility" if expected_gpu else ("void" if cpu_override else None)
        )
        check.equal(name + " CDI selector", environment.get("NVIDIA_VISIBLE_DEVICES"), expected_visible_devices)
        check.equal(name + " NVIDIA driver capabilities", environment.get("NVIDIA_DRIVER_CAPABILITIES"), expected_driver_capabilities)
        check.equal(name + " host device mapping", service.get("devices", []), [])
        check.true(name + " must not use legacy GPU request", not service.get("gpus"))

        expected_seed_environment = {
            "PYMTLF_SEED_SOURCE": "/opt/app/seed_models/initial",
            "PYMTLF_SEED_MODEL_ID": "1",
            "PYMTLF_SEED_INTEROPERABILITY": "001122",
            "PYMTLF_SEED_ARTIFACT_KEY": "a2c796a001e2da2461418f80b01d7d1e33f0e3349c2817d92286f09e67aa6bef",
        } if name == "pymtlf-c" else {}
        for key, value in expected_seed_environment.items():
            check.equal(name + " " + key, environment.get(key), value)

    if check.errors:
        for error in check.errors:
            print("ERROR: " + error, file=sys.stderr)
        return 1
    print("OK compose mode={} services={} config={}".format(args.mode, len(services), config_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
