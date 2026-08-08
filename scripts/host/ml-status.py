#!/usr/bin/env python3
"""Report project-scoped ML container, config, image, device, and memory state."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml


SERVICES = ("pyanlf-a", "pyanlf-b", "pymtlf-a", "pymtlf-b", "pymtlf-c")
PROJECT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
CONFIG_TARGET = "/etc/5g-nwdaf/config.yaml"


def output(command, timeout=30):
    return subprocess.check_output(command, text=True, timeout=timeout).strip()


def configured_device(container):
    source = next(
        (
            mount.get("Source")
            for mount in container.get("Mounts", [])
            if mount.get("Destination") == CONFIG_TARGET and mount.get("Type") == "bind"
        ),
        None,
    )
    if not source or not Path(source).is_file():
        return "unknown"
    with Path(source).open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    service = container["Config"]["Labels"].get("com.docker.compose.service", "")
    if service.startswith("pyanlf-"):
        return config.get("model", {}).get("device", "cpu")
    if config.get("runtime", {}).get("mode") == "fl_client":
        return (
            config.get("federated_learning", {})
            .get("client", {})
            .get("training", {})
            .get("device", "cpu")
        )
    return "cpu"


def cuda_visible(container_id):
    try:
        return output(
            [
                "docker",
                "exec",
                container_id,
                "python",
                "-c",
                "import torch; print(str(torch.cuda.is_available()).lower())",
            ],
            timeout=20,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "error"


def environment(container):
    return dict(
        entry.split("=", 1)
        for entry in container.get("Config", {}).get("Env") or []
        if "=" in entry
    )


def cdi_selector(container):
    return environment(container).get("NVIDIA_VISIBLE_DEVICES", "none")


def image_metadata(image_ids):
    if not image_ids:
        return {}
    values = json.loads(output(["docker", "image", "inspect", *sorted(image_ids)]))
    return {
        value["Id"]: {
            "id": (
                value["Id"][len("sha256:") :]
                if value["Id"].startswith("sha256:")
                else value["Id"]
            )[:12],
            "revision": value.get("Config", {})
            .get("Labels", {})
            .get("org.opencontainers.image.revision", "unknown")[:12],
        }
        for value in values
    }


def memory_usage(containers):
    running = [value["Id"] for value in containers if value["State"]["Running"]]
    if not running:
        return {}
    lines = output(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", *running],
        timeout=30,
    ).splitlines()
    return {item["Name"]: item.get("MemUsage", "unknown") for item in map(json.loads, lines)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="5g-nwdaf-infrastructure")
    args = parser.parse_args()
    if not PROJECT_PATTERN.fullmatch(args.project):
        raise SystemExit("invalid project name")

    ids = output(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project={}".format(args.project),
        ]
    ).splitlines()
    if not ids:
        print("ML project={} containers=absent".format(args.project))
        return 0

    containers = json.loads(output(["docker", "inspect", *ids]))
    by_service = {}
    duplicates = set()
    for container in containers:
        service = container.get("Config", {}).get("Labels", {}).get(
            "com.docker.compose.service"
        )
        if service not in SERVICES:
            continue
        if service in by_service:
            duplicates.add(service)
        by_service[service] = container

    images = image_metadata({value["Image"] for value in by_service.values()})
    memory = memory_usage(list(by_service.values()))
    print("ML project={}".format(args.project))
    print(
        "{:<10} {:<9} {:<9} {:<7} {:<8} {:<20} {:<6} {:<21} {:<12} {:<12} {}".format(
            "SERVICE",
            "STATE",
            "HEALTH",
            "DEVICE",
            "RUNTIME",
            "CDI",
            "CUDA",
            "MEMORY",
            "IMAGE",
            "REVISION",
            "CONFIG",
        )
    )
    for service in SERVICES:
        container = by_service.get(service)
        if container is None:
            print("{:<10} absent".format(service))
            continue
        state = container["State"]
        health = (
            state.get("Health", {}).get("Status", "none") if state["Running"] else "n/a"
        )
        cuda = cuda_visible(container["Id"]) if state["Running"] else "n/a"
        labels = container["Config"].get("Labels", {})
        image = images.get(container["Image"], {"id": "unknown", "revision": "unknown"})
        config = "{}:{}".format(
            labels.get("io.5g-nwdaf.config-set", "unknown"),
            labels.get("io.5g-nwdaf.config-hash", "unknown")[:12],
        )
        print(
            "{:<10} {:<9} {:<9} {:<7} {:<8} {:<20} {:<6} {:<21} {:<12} {:<12} {}".format(
                service,
                state["Status"],
                health,
                configured_device(container),
                container.get("HostConfig", {}).get("Runtime", "default"),
                cdi_selector(container),
                cuda,
                memory.get(container["Name"].lstrip("/"), "n/a"),
                image["id"],
                image["revision"],
                config,
            )
        )

    if duplicates:
        print("ERROR duplicate services: {}".format(", ".join(sorted(duplicates))), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
