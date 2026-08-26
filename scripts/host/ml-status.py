#!/usr/bin/env python3
"""Report project-scoped ML container, config, image, device, and memory state."""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml


SERVICES = ("pyanlf-a", "pyanlf-b", "pymtlf-a", "pymtlf-b", "pymtlf-c")
PROJECT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
CONFIG_TARGET = "/etc/5g-nwdaf/config.yaml"
TIMESTAMP_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}T\S+)\s+(.*)$")


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
    if config.get("federated_learning", {}).get("client") is not None:
        return (
            config.get("federated_learning", {})
            .get("client", {})
            .get("training", {})
            .get("device", "cpu")
        )
    return "cpu"


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


def milestone(timestamp="not-seen", detail="not-seen"):
    return {"timestamp": timestamp, "detail": detail}


def timestamp_order(value):
    if value in (None, "not-seen", "unknown"):
        return None
    normalized = re.sub(r"(\.\d{6})\d+(?=Z|[+-])", r"\1", value)
    try:
        return datetime.datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


def latest_milestone(current, timestamp, detail):
    current_time = timestamp_order(current["timestamp"])
    candidate_time = timestamp_order(timestamp)
    if current["timestamp"] == "not-seen":
        return milestone(timestamp, detail)
    if candidate_time is not None and (
        current_time is None or candidate_time > current_time
    ):
        return milestone(timestamp, detail)
    return current


def parse_fl_milestones(logs_by_service):
    result = {
        "monitors": milestone(),
        "degradation": milestone(),
        "process": milestone(),
        "preparation": milestone(),
        "client_a": milestone(),
        "client_b": milestone(),
        "rounds": milestone(),
        "validation": milestone(),
        "publication": milestone(),
        "adoption": milestone(),
        "cutover": milestone(),
        "post_cutover_accuracy": milestone(),
        "failure": milestone(),
    }
    monitor_ids = set()
    active_monitor_ids = set()
    process_id = None
    rounds = set()
    adopted_scopes = set()
    client_rounds = {"pymtlf-a": set(), "pymtlf-b": set()}
    client_validation = {"pymtlf-a": False, "pymtlf-b": False}
    client_timestamp = {"pymtlf-a": "not-seen", "pymtlf-b": "not-seen"}
    cutover_seen = False

    for service, raw in logs_by_service.items():
        for line in raw.splitlines():
            match = TIMESTAMP_PATTERN.match(line)
            timestamp, message = (match.group(1), match.group(2)) if match else ("unknown", line)
            if service in client_rounds:
                local = re.search(r"FL client local result ready .* round=(\d+) samples=(\d+)", message)
                if local:
                    client_rounds[service].add(int(local.group(1)))
                    client_timestamp[service] = timestamp
                validation = re.search(r"FL client final validation ready .* round=(\d+) samples=(\d+)", message)
                if validation:
                    client_validation[service] = True
                    client_timestamp[service] = timestamp
                if "FL client round failed" in message or "FL client final validation failed" in message:
                    result["failure"] = latest_milestone(
                        result["failure"], timestamp, service + ": " + message.strip()
                    )
                continue

            active = re.search(r"ML Model Monitor subscription active subscription_id=([^ ]+)", message)
            if active:
                monitor_ids.add(active.group(1))
                active_monitor_ids.add(active.group(1))
                result["monitors"] = milestone(
                    timestamp,
                    "created={} active={}".format(len(monitor_ids), len(active_monitor_ids)),
                )
            removed = re.search(r"ML Model Monitor subscription removed subscription_id=([^ ]+)", message)
            if removed:
                active_monitor_ids.discard(removed.group(1))
                result["monitors"] = milestone(
                    timestamp,
                    "created={} active={}".format(len(monitor_ids), len(active_monitor_ids)),
                )
            if "ML Model accuracy report processed" in message:
                evaluated_true = re.search(r"evaluated=\[[^]]*True", message) is not None
                triggered_true = re.search(r"triggered=\[[^]]*True", message) is not None
                if triggered_true and result["degradation"]["timestamp"] == "not-seen":
                    result["degradation"] = milestone(timestamp, "evaluated={} triggered=true".format(evaluated_true))
                if cutover_seen and evaluated_true and not triggered_true:
                    result["post_cutover_accuracy"] = milestone(timestamp, "evaluated=true triggered=false")
            started = re.search(r"Federated process started process_id=([^ ]+) scopes=(.*)", message)
            if started:
                process_id = started.group(1)
                scope_count = started.group(2).count('"consumerId"') or "observed"
                result["process"] = milestone(
                    timestamp, "id={} scopes={}".format(process_id, scope_count)
                )
            prepared = re.search(r"Federated preparation complete process_id=([^ ]+) participants=(.*)", message)
            if prepared:
                result["preparation"] = milestone(timestamp, "process={} participants={}".format(prepared.group(1), prepared.group(2)))
            aggregated = re.search(r"Federated round aggregated process_id=([^ ]+) round=(\d+)", message)
            if aggregated:
                rounds.add(int(aggregated.group(2)))
                result["rounds"] = milestone(timestamp, "process={} completed={}".format(aggregated.group(1), ",".join(map(str, sorted(rounds)))))
            validation = re.search(
                r"Federated final validation evaluated process_id=([^ ]+) base_wape=([^ ]+) candidate_wape=([^ ]+) gate_would_accept=([^ ]+)",
                message,
            )
            if validation:
                result["validation"] = milestone(
                    timestamp,
                    "process={} base_wape={} candidate_wape={} gate_would_accept={}".format(*validation.groups()),
                )
            published = re.search(r"Federated model published .* model_id=([^ ]+) state=([^ ]+) required_scopes=(\d+)", message)
            if published:
                result["publication"] = milestone(
                    timestamp,
                    "model={} state={} required_scopes={}".format(*published.groups()),
                )
            adopted = re.search(r"Federated model scope adopted model_id=([^ ]+) scope=([^ ]+) complete=([^ ]+)", message)
            if adopted:
                adopted_scopes.add(adopted.group(2))
                result["adoption"] = milestone(
                    timestamp,
                    "model={} scopes={} complete={}".format(
                        adopted.group(1), len(adopted_scopes), adopted.group(3)
                    ),
                )
            cutover = re.search(r"Federated model cutover complete model_id=([^ ]+) family=(.*)", message)
            if cutover:
                cutover_seen = True
                result["cutover"] = milestone(timestamp, "model={} family={}".format(*cutover.groups()))
            failed = re.search(r"Federated process failed process_id=([^ ]+)", message)
            if failed:
                result["failure"] = latest_milestone(
                    result["failure"], timestamp, "process={}".format(failed.group(1))
                )

    for service, key in (("pymtlf-a", "client_a"), ("pymtlf-b", "client_b")):
        if client_rounds[service] or client_validation[service]:
            result[key] = milestone(
                client_timestamp[service],
                "rounds={} final_validation={}".format(
                    ",".join(map(str, sorted(client_rounds[service]))) or "none",
                    str(client_validation[service]).lower(),
                ),
            )
    return result


def milestone_value(detail, key):
    match = re.search(r"(?:^| ){}=([^ ]+)".format(re.escape(key)), detail)
    return match.group(1) if match else "unknown"


def fl_result(summary, coordinator_state):
    success = summary["post_cutover_accuracy"]
    failure = summary["failure"]
    success_time = timestamp_order(success["timestamp"])
    failure_time = timestamp_order(failure["timestamp"])
    success_seen = success["timestamp"] != "not-seen"
    failure_seen = failure["timestamp"] != "not-seen"

    if success_seen and (
        not failure_seen
        or (success_time is not None and failure_time is not None and success_time > failure_time)
    ):
        model = milestone_value(summary["cutover"]["detail"], "model")
        return "outcome=complete model={} evidence=post-cutover-accuracy".format(model)
    if failure_seen:
        process = milestone_value(failure["detail"], "process")
        return "outcome=failed process={} evidence={}".format(
            process, failure["detail"].replace(" ", "_")
        )
    if coordinator_state == "absent":
        return "outcome=not-started reason=coordinator-absent"

    phases = (
        ("cutover", "post-cutover-validation"),
        ("adoption", "model-adoption"),
        ("publication", "model-publication"),
        ("validation", "federated-validation"),
        ("rounds", "federated-training"),
        ("preparation", "federated-preparation"),
        ("process", "federated-process"),
        ("degradation", "degradation-detected"),
        ("monitors", "monitoring"),
    )
    phase = next(
        (name for milestone_name, name in phases if summary[milestone_name]["timestamp"] != "not-seen"),
        "starting",
    )
    if coordinator_state == "running":
        return "outcome=in-progress phase={}".format(phase)
    return "outcome=incomplete phase={} coordinator_state={}".format(
        phase, coordinator_state
    )


def container_logs_since(container, service=None, cache_dir=None, until=None):
    started_at = container.get("State", {}).get("StartedAt", "")
    if not started_at or started_at.startswith("0001-"):
        return ""
    cached_logs = ""
    cursor = started_at
    metadata_file = None
    log_file = None
    if cache_dir is not None:
        key = cache_key(service or container["Id"])
        metadata_file = cache_dir / "fl-logs" / (key + ".json")
        log_file = cache_dir / "fl-logs" / (key + ".log")
        try:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            if (
                metadata.get("containerId") == container["Id"]
                and metadata.get("startedAt") == started_at
                and log_file.is_file()
            ):
                cursor = metadata.get("until", started_at)
                cached_logs = log_file.read_text(encoding="utf-8")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    command = ["docker", "logs", "--timestamps", "--since", cursor]
    if cache_dir is not None:
        until = until or datetime.datetime.now(datetime.timezone.utc).isoformat()
        command.extend(("--until", until))
    command.append(container["Id"])
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "docker logs failed for {}: {}".format(
                container.get("Name", container["Id"]), completed.stderr.strip()
            )
        )
    new_logs = "\n".join(value for value in (completed.stdout, completed.stderr) if value)
    combined = cached_logs
    if combined and new_logs:
        combined = combined.rstrip("\n") + "\n"
    combined += new_logs
    if metadata_file is not None and log_file is not None:
        atomic_cache_write(log_file, combined)
        atomic_cache_write(
            metadata_file,
            json.dumps(
                {
                    "schemaVersion": 1,
                    "containerId": container["Id"],
                    "startedAt": started_at,
                    "until": until,
                },
                sort_keys=True,
            )
            + "\n",
        )
    return combined


def print_fl_summary(by_service, cache_dir=None):
    coordinator = by_service.get("pymtlf-c")
    if coordinator is None:
        print("FL CURRENT RUN container=absent milestones=not-seen")
        print("FL RESULT {}".format(fl_result(parse_fl_milestones({}), "absent")))
        return
    labels = coordinator.get("Config", {}).get("Labels", {})
    identity = "{}:{}".format(
        labels.get("io.5g-nwdaf.config-set", "unknown"),
        labels.get("io.5g-nwdaf.config-hash", "unknown")[:12],
    )
    for service in ("pymtlf-a", "pymtlf-b"):
        container = by_service.get(service)
        if container is None:
            continue
        service_labels = container.get("Config", {}).get("Labels", {})
        service_identity = "{}:{}".format(
            service_labels.get("io.5g-nwdaf.config-set", "unknown"),
            service_labels.get("io.5g-nwdaf.config-hash", "unknown")[:12],
        )
        if service_identity != identity:
            raise RuntimeError(
                "FL current-run config identity mismatch: pymtlf-c={} {}={}".format(
                    identity, service, service_identity
                )
            )
    started_at = coordinator.get("State", {}).get("StartedAt", "unknown")
    logs = {
        service: container_logs_since(
            by_service[service], service=service, cache_dir=cache_dir
        )
        for service in ("pymtlf-a", "pymtlf-b", "pymtlf-c")
        if service in by_service
    }
    summary = parse_fl_milestones(logs)
    print("FL CURRENT RUN config={} coordinator_started_at={}".format(identity, started_at))
    print("{:<24} {:<30} {}".format("MILESTONE", "TIMESTAMP", "EVIDENCE"))
    for name, value in summary.items():
        print("{:<24} {:<30} {}".format(name, value["timestamp"], value["detail"]))
    coordinator_state = (
        "running" if coordinator.get("State", {}).get("Running")
        else coordinator.get("State", {}).get("Status", "stopped")
    )
    print("FL RESULT {}".format(fl_result(summary, coordinator_state)))


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
    parser.add_argument("--cache-dir")
    args = parser.parse_args()
    if not PROJECT_PATTERN.fullmatch(args.project):
        raise SystemExit("invalid project name")
    cache_dir = Path(args.cache_dir).resolve() if args.cache_dir else None

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
        print()
        print_fl_summary({})
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
        cuda = cuda_visible(container["Id"], cache_dir) if state["Running"] else "n/a"
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
    print()
    print_fl_summary(by_service, cache_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
