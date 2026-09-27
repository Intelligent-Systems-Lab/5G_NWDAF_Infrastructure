#!/usr/bin/env python3
"""Report project-scoped protocol PyMTLF container and identity state."""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml


DEFAULT_SERVICES = ("pymtlf-root",)
PROJECT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
CONFIG_TARGET = "/etc/5g-nwdaf/config.yaml"


def output(command, timeout=30):
    return subprocess.check_output(command, text=True, timeout=timeout).strip()


def configured_device(container):
    source = next(
        (
            mount.get("Source")
            for mount in container.get("Mounts", [])
            if mount.get("Destination") == CONFIG_TARGET
            and mount.get("Type") == "bind"
        ),
        None,
    )
    if not source or not Path(source).is_file():
        return "unknown"
    with Path(source).open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    client = config.get("federated_learning", {}).get("client")
    if client is not None:
        return client.get("training", {}).get("device", "cpu")
    return (
        config.get("federated_learning", {})
        .get("experiment_recording", {})
        .get("validation", {})
        .get("device", "cpu")
    )


def atomic_cache_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".{}.{}.tmp".format(path.name, os.getpid()))
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def cache_key(value):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def cuda_visible(container_id, cache_dir=None):
    cache_file = None
    if cache_dir is not None:
        cache_file = cache_dir / "cuda" / cache_key(container_id)
        try:
            cached = cache_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            cached = ""
        if cached in ("true", "false"):
            return cached
    try:
        visible = output(
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
    if cache_file is not None and visible in ("true", "false"):
        atomic_cache_write(cache_file, visible + "\n")
    return visible


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
                value["Id"][len("sha256:"):]
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
    return {
        item["Name"]: item.get("MemUsage", "unknown")
        for item in map(json.loads, lines)
    }


def print_fl_summary(by_service, coordinator_name="pymtlf-root", **_unused):
    coordinator = by_service.get(coordinator_name)
    if coordinator is None:
        print(
            "FL CURRENT RUN coordinator={} container=absent milestones=not-seen".format(
                coordinator_name
            )
        )
        print("FL RESULT outcome=not-started reason=coordinator-absent")
        return
    labels = coordinator.get("Config", {}).get("Labels", {})
    identity = "{}:{}".format(
        labels.get("io.5g-nwdaf.config-set", "unknown"),
        labels.get("io.5g-nwdaf.config-hash", "unknown")[:12],
    )
    started_at = coordinator.get("State", {}).get("StartedAt", "unknown")
    for service, container in by_service.items():
        service_labels = container.get("Config", {}).get("Labels", {})
        service_identity = "{}:{}".format(
            service_labels.get("io.5g-nwdaf.config-set", "unknown"),
            service_labels.get("io.5g-nwdaf.config-hash", "unknown")[:12],
        )
        if service_identity != identity:
            raise RuntimeError(
                "FL current-run config identity mismatch: {}={} {}={}".format(
                    coordinator_name, identity, service, service_identity
                )
            )
    print(
        "FL CURRENT RUN config={} coordinator={} coordinator_started_at={}".format(
            identity, coordinator_name, started_at
        )
    )
    print(
        "FL RESULT outcome=not-evaluated topology=protocol-hierarchical "
        "status=use-fl-training-status"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="5g-nwdaf-infrastructure")
    parser.add_argument("--cache-dir")
    parser.add_argument("--services", default=",".join(DEFAULT_SERVICES))
    parser.add_argument("--retained-services")
    parser.add_argument("--coordinator", default="pymtlf-root")
    parser.add_argument("--deployment-kind")
    parser.add_argument("--config-set", required=True)
    parser.add_argument("--config-hash", required=True)
    parser.add_argument("--identity-only", action="store_true")
    parser.add_argument("--allow-stopped-selected-mismatch", action="store_true")
    parser.add_argument("--require-running-selected", action="store_true")
    args = parser.parse_args()
    if args.allow_stopped_selected_mismatch and not args.identity_only:
        parser.error("--allow-stopped-selected-mismatch requires --identity-only")
    if args.require_running_selected and not args.identity_only:
        parser.error("--require-running-selected requires --identity-only")
    if args.require_running_selected and args.allow_stopped_selected_mismatch:
        parser.error(
            "--require-running-selected cannot be combined with "
            "--allow-stopped-selected-mismatch"
        )
    if not PROJECT_PATTERN.fullmatch(args.project):
        raise SystemExit("invalid project name")
    cache_dir = Path(args.cache_dir).resolve() if args.cache_dir else None
    services = tuple(item for item in args.services.split(",") if item)
    if not services or len(services) != len(set(services)):
        raise SystemExit("services must be a non-empty unique comma-separated list")
    retained = tuple(
        item for item in (args.retained_services or args.services).split(",") if item
    )
    if len(retained) != len(set(retained)) or not set(services) <= set(retained):
        raise SystemExit("retained services must uniquely include selected services")
    if args.coordinator not in services:
        raise SystemExit("coordinator must be selected by services")

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
        if args.require_running_selected:
            print("ERROR selected ML containers are absent", file=sys.stderr)
            return 1
        if args.identity_only:
            return 0
        print("ML project={} containers=absent".format(args.project))
        print()
        print_fl_summary({}, args.coordinator)
        return 0

    containers = json.loads(output(["docker", "inspect", *ids]))
    by_service = {}
    duplicates = set()
    unexpected = []
    retained_stopped = []
    seen_retained = set()
    identity_mismatches = []
    for container in containers:
        labels = container.get("Config", {}).get("Labels", {})
        service = labels.get("com.docker.compose.service")
        if service not in services:
            if service in retained and not container.get("State", {}).get("Running"):
                if service in seen_retained:
                    duplicates.add(service)
                seen_retained.add(service)
                retained_stopped.append(container)
                continue
            unexpected.append(container)
            continue
        if service in by_service:
            duplicates.add(service)
        by_service[service] = container
        mismatch = (
            labels.get("io.5g-nwdaf.config-set") != args.config_set
            or labels.get("io.5g-nwdaf.config-hash") != args.config_hash
        )
        if mismatch and not (
            args.allow_stopped_selected_mismatch
            and not container.get("State", {}).get("Running")
        ):
            identity_mismatches.append(service)

    if duplicates:
        print(
            "ERROR duplicate services: {}".format(", ".join(sorted(duplicates))),
            file=sys.stderr,
        )
        return 1
    if identity_mismatches:
        print(
            "ERROR selected container config identity mismatch: {}".format(
                ", ".join(sorted(identity_mismatches))
            ),
            file=sys.stderr,
        )
        return 1
    if unexpected:
        details = []
        for container in unexpected:
            labels = container.get("Config", {}).get("Labels", {})
            identity = (
                labels.get("com.docker.compose.service")
                or container.get("Name", "").lstrip("/")
                or container.get("Id", "unknown")[:12]
            )
            details.append(
                "{}:{}".format(
                    identity, container.get("State", {}).get("Status", "unknown")
                )
            )
        print(
            "ERROR unexpected ML containers exist: {}".format(
                ", ".join(sorted(details))
            ),
            file=sys.stderr,
        )
        return 1
    if args.require_running_selected:
        unavailable = [
            service
            for service in services
            if service not in by_service
            or not by_service[service].get("State", {}).get("Running")
        ]
        if unavailable:
            print(
                "ERROR selected ML containers are not all running: {}".format(
                    ", ".join(unavailable)
                ),
                file=sys.stderr,
            )
            return 1
    if args.identity_only:
        return 0

    images = image_metadata({value["Image"] for value in by_service.values()})
    memory = memory_usage(list(by_service.values()))
    print("ML project={}".format(args.project))
    print(
        "{:<28} {:<9} {:<9} {:<7} {:<8} {:<20} {:<6} {:<21} {:<12} {:<12} {}".format(
            "SERVICE", "STATE", "HEALTH", "DEVICE", "RUNTIME", "CDI", "CUDA",
            "MEMORY", "IMAGE", "REVISION", "CONFIG",
        )
    )
    for service in services:
        container = by_service.get(service)
        if container is None:
            print("{:<28} absent".format(service))
            continue
        state = container["State"]
        health = (
            state.get("Health", {}).get("Status", "none")
            if state["Running"]
            else "n/a"
        )
        cuda = cuda_visible(container["Id"], cache_dir) if state["Running"] else "n/a"
        labels = container["Config"].get("Labels", {})
        image = images.get(container["Image"], {"id": "unknown", "revision": "unknown"})
        config = "{}:{}".format(
            labels.get("io.5g-nwdaf.config-set", "unknown"),
            labels.get("io.5g-nwdaf.config-hash", "unknown")[:12],
        )
        print(
            "{:<28} {:<9} {:<9} {:<7} {:<8} {:<20} {:<6} {:<21} {:<12} {:<12} {}".format(
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

    for container in retained_stopped:
        labels = container.get("Config", {}).get("Labels", {})
        print(
            "RETAINED service={} state={} config={}:{}".format(
                labels.get("com.docker.compose.service", "unknown"),
                container.get("State", {}).get("Status", "unknown"),
                labels.get("io.5g-nwdaf.config-set", "unknown"),
                labels.get("io.5g-nwdaf.config-hash", "unknown")[:12],
            )
        )

    print()
    print_fl_summary(by_service, args.coordinator)
    return 0


if __name__ == "__main__":
    sys.exit(main())
