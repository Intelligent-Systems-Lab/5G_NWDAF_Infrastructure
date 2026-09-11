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


DEFAULT_SERVICES = ("pyanlf-a", "pyanlf-b", "pymtlf-a", "pymtlf-b", "pymtlf-c")
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


def parse_fl_milestones(logs_by_service, coordinator_name="pymtlf-c"):
    if coordinator_name == "pymtlf-root":
        return parse_hierarchical_milestones(logs_by_service, coordinator_name)
    result = {
        "monitors": milestone(),
        "degradation": milestone(),
        "process": milestone(),
        "preparation": milestone(),
        "rounds": milestone(),
        "validation": milestone(),
        "publication": milestone(),
        "completion": milestone(),
        "cleanup": milestone(),
        "cleanup_failure": milestone(),
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
    created_resources = {}
    deleted_resources = {}
    cleanup_timestamps = {}
    client_services = tuple(
        sorted(
            service
            for service in logs_by_service
            if service != coordinator_name and service.startswith("pymtlf-")
        )
    )
    client_rounds = {service: set() for service in client_services}
    client_samples = {service: {} for service in client_services}
    client_validation = {service: False for service in client_services}
    client_timestamp = {service: "not-seen" for service in client_services}
    cutover_seen = False

    for service, raw in logs_by_service.items():
        for line in raw.splitlines():
            match = TIMESTAMP_PATTERN.match(line)
            timestamp, message = (match.group(1), match.group(2)) if match else ("unknown", line)
            if service in client_rounds:
                local = re.search(r"FL client local result ready .* round=(\d+) samples=(\d+)", message)
                if local:
                    round_indicator = int(local.group(1))
                    client_rounds[service].add(round_indicator)
                    client_samples[service][round_indicator] = int(local.group(2))
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
            completed = re.search(
                r"Federated final validation complete process_id=([^ ]+) state=([^ ]+) artifact=([^ ]+)",
                message,
            )
            if completed:
                result["completion"] = milestone(
                    timestamp,
                    "process={} state={} artifact={}".format(*completed.groups()),
                )
            cleanup_failed = re.search(
                r"FL participant cleanup failed process_id=([^ ]+) nf=([^ ]+) error=(.*)",
                message,
            )
            if cleanup_failed:
                result["cleanup_failure"] = latest_milestone(
                    result["cleanup_failure"],
                    timestamp,
                    "process={} nf={} error={}".format(*cleanup_failed.groups()),
                )
            created = re.search(
                r"FL participant resource created process_id=([^ ]+) nf=([^ ]+) location=([^ ]+)",
                message,
            )
            if created:
                resource_process = created.group(1)
                created_resources.setdefault(resource_process, {})[created.group(3)] = (
                    created.group(2)
                )
                cleanup_timestamps[resource_process] = timestamp
            deleted = re.search(
                r"FL participant resource deleted process_id=([^ ]+) nf=([^ ]+) location=([^ ]+) status=(\d+)",
                message,
            )
            if deleted:
                resource_process = deleted.group(1)
                deleted_resources.setdefault(resource_process, set()).add(deleted.group(3))
                cleanup_timestamps[resource_process] = timestamp
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

    cleanup_process = milestone_value(result["completion"]["detail"], "process")
    if cleanup_process == "unknown":
        cleanup_process = process_id
    process_created = created_resources.get(cleanup_process or "", {})
    process_deleted = deleted_resources.get(cleanup_process or "", set())
    if process_created or process_deleted:
        active_resources = set(process_created).difference(process_deleted)
        unknown_deletes = process_deleted.difference(process_created)
        result["cleanup"] = milestone(
            cleanup_timestamps[cleanup_process],
            "created={} deleted={} active={} unknown_deletes={}".format(
                len(process_created),
                len(process_deleted.intersection(process_created)),
                len(active_resources),
                len(unknown_deletes),
            ),
        )

    for service in client_services:
        key = {
            "pymtlf-a": "client_a",
            "pymtlf-b": "client_b",
        }.get(
            service,
            service[len("pymtlf-") :].replace("-", "_"),
        )
        result[key] = milestone()
        if client_rounds[service] or client_validation[service]:
            samples = ",".join(
                "{}:{}".format(round_indicator, client_samples[service][round_indicator])
                for round_indicator in sorted(client_samples[service])
            ) or "none"
            result[key] = milestone(
                client_timestamp[service],
                "rounds={} samples={} final_validation={}".format(
                    ",".join(map(str, sorted(client_rounds[service]))) or "none",
                    samples,
                    str(client_validation[service]).lower(),
                ),
            )
    return result


def _participant_count(value):
    return len(set(re.findall(r"['\"]([^'\"]+)['\"]", value)))


def _resource_milestone(created, deleted, timestamp, *, branches=None):
    active = set(created).difference(deleted)
    unknown_deletes = set(deleted).difference(created)
    detail = "created={} deleted={} active={} unknown_deletes={}".format(
        len(created),
        len(set(deleted).intersection(created)),
        len(active),
        len(unknown_deletes),
    )
    if branches is not None:
        detail += " branches={}".format(branches)
    return milestone(timestamp, detail)


def parse_hierarchical_milestones(logs_by_service, coordinator_name="pymtlf-root"):
    result = {
        "hierarchy_request": milestone(),
        "root_preparation": milestone(),
        "branch_preparation": milestone(),
        "validation": milestone(),
        "publication": milestone(),
        "upper_cleanup": milestone(),
        "lower_cleanup": milestone(),
        "cleanup_failure": milestone(),
        "failure": milestone(),
    }
    request_id = None
    plan_id = None
    request_timestamp = "not-seen"
    root_logs = logs_by_service.get(coordinator_name, "")
    for line in root_logs.splitlines():
        match = TIMESTAMP_PATTERN.match(line)
        timestamp, message = (
            (match.group(1), match.group(2)) if match else ("unknown", line)
        )
        accepted = re.search(
            r"Accepted hierarchy Root request request_id=([^ ]+) plan_id=([^ ]+) "
            r"source=([^ ]+) family=([^ ]+)",
            message,
        )
        if accepted:
            candidate = latest_milestone(
                result["hierarchy_request"],
                timestamp,
                "request={} plan={} source={} family={}".format(*accepted.groups()),
            )
            if candidate is not result["hierarchy_request"]:
                result["hierarchy_request"] = candidate
                request_id, plan_id = accepted.group(1), accepted.group(2)
                request_timestamp = timestamp

    cutoff = timestamp_order(request_timestamp)

    def current_run(timestamp):
        observed = timestamp_order(timestamp)
        return cutoff is None or observed is None or observed >= cutoff

    root_process = None
    branch_processes = {}
    branch_participants = {}
    branch_preparation_timestamps = {}
    created_resources = {}
    deleted_resources = {}
    resource_timestamps = {}

    for service, raw in logs_by_service.items():
        for line in raw.splitlines():
            match = TIMESTAMP_PATTERN.match(line)
            timestamp, message = (
                (match.group(1), match.group(2)) if match else ("unknown", line)
            )
            if not current_run(timestamp):
                continue

            dispatched = re.search(
                r"Hierarchy preparation dispatched plan_id=([^ ]+) "
                r"process_id=([^ ]+) participants=(.*)",
                message,
            )
            if dispatched and dispatched.group(1) == plan_id:
                process = dispatched.group(2)
                participants = _participant_count(dispatched.group(3))
                if service == coordinator_name:
                    if root_process is not None and root_process != process:
                        result["failure"] = latest_milestone(
                            result["failure"],
                            timestamp,
                            "plan={} contradictory_root_processes".format(plan_id),
                        )
                    root_process = process
                    result["root_preparation"] = milestone(
                        timestamp,
                        "plan={} process={} participants={}".format(
                            plan_id, process, participants
                        ),
                    )
                elif service.startswith("pymtlf-branch-"):
                    if (
                        service in branch_processes
                        and branch_processes[service] != process
                    ):
                        result["failure"] = latest_milestone(
                            result["failure"],
                            timestamp,
                            "plan={} service={} contradictory_branch_processes".format(
                                plan_id, service
                            ),
                        )
                    branch_processes[service] = process
                    branch_participants[service] = participants
                    branch_preparation_timestamps[service] = timestamp

            local = re.search(
                r"FL client local result ready .* round=(\d+) samples=(\d+)",
                message,
            )
            validation_ready = re.search(
                r"FL client final validation ready .* round=(\d+) samples=(\d+)",
                message,
            )
            if service.startswith("pymtlf-leaf-") and (local or validation_ready):
                key = "_leaf_events_{}".format(service)
                events = result.setdefault(
                    key,
                    {
                        "rounds": set(),
                        "samples": {},
                        "validation": False,
                        "timestamp": "not-seen",
                    },
                )
                if local:
                    round_indicator = int(local.group(1))
                    events["rounds"].add(round_indicator)
                    events["samples"][round_indicator] = int(local.group(2))
                if validation_ready:
                    events["validation"] = True
                events["timestamp"] = timestamp

            if (
                "FL client round failed" in message
                or "FL client final validation failed" in message
            ):
                result["failure"] = latest_milestone(
                    result["failure"], timestamp, service + ": " + message.strip()
                )

            failed = re.search(
                r"Hierarchy Root request failed request_id=([^ ]+) plan_id=([^ ]+)",
                message,
            )
            if failed and failed.group(1) == request_id and failed.group(2) == plan_id:
                result["failure"] = latest_milestone(
                    result["failure"],
                    timestamp,
                    "request={} plan={}".format(*failed.groups()),
                )

            evaluated = re.search(
                r"Hierarchy final validation evaluated process_id=([^ ]+) "
                r"base_wape=([^ ]+) candidate_wape=([^ ]+) "
                r"gate_would_accept=([^ ]+)",
                message,
            )
            if (
                evaluated
                and service == coordinator_name
                and evaluated.group(1) == root_process
            ):
                result["validation"] = milestone(
                    timestamp,
                    "process={} base_wape={} candidate_wape={} "
                    "gate_would_accept={}".format(*evaluated.groups()),
                )

            published = re.search(
                r"Federated model published .* model_id=([^ ]+) state=([^ ]+) "
                r"required_scopes=(\d+)",
                message,
            )
            if published and service == coordinator_name:
                result["publication"] = milestone(
                    timestamp,
                    "model={} state={} required_scopes={}".format(
                        *published.groups()
                    ),
                )

            created = re.search(
                r"FL participant resource created process_id=([^ ]+) "
                r"nf=([^ ]+) location=([^ ]+)",
                message,
            )
            if created:
                key = (service, created.group(1))
                created_resources.setdefault(key, {})[created.group(3)] = created.group(2)
                resource_timestamps[key] = timestamp
            deleted = re.search(
                r"FL participant resource deleted process_id=([^ ]+) "
                r"nf=([^ ]+) location=([^ ]+) status=(\d+)",
                message,
            )
            if deleted:
                key = (service, deleted.group(1))
                deleted_resources.setdefault(key, set()).add(deleted.group(3))
                resource_timestamps[key] = timestamp
            cleanup_failed = re.search(
                r"FL participant cleanup failed process_id=([^ ]+) "
                r"nf=([^ ]+) error=(.*)",
                message,
            )
            known_processes = {root_process, *branch_processes.values()}
            if cleanup_failed and cleanup_failed.group(1) in known_processes:
                result["cleanup_failure"] = latest_milestone(
                    result["cleanup_failure"],
                    timestamp,
                    "process={} nf={} error={}".format(*cleanup_failed.groups()),
                )

    prepared_exact = sum(
        participants == 2 for participants in branch_participants.values()
    )
    if branch_processes:
        latest = max(
            branch_preparation_timestamps.values(),
            key=lambda value: timestamp_order(value) or datetime.datetime.min.replace(
                tzinfo=datetime.timezone.utc
            ),
        )
        result["branch_preparation"] = milestone(
            latest,
            "plan={} prepared={} expected=2 participants_exact={}".format(
                plan_id, len(branch_processes), prepared_exact
            ),
        )

    if root_process is not None:
        key = (coordinator_name, root_process)
        if key in created_resources or key in deleted_resources:
            result["upper_cleanup"] = _resource_milestone(
                created_resources.get(key, {}),
                deleted_resources.get(key, set()),
                resource_timestamps.get(key, request_timestamp),
            )

    lower_created = {}
    lower_deleted = set()
    exact_branches = 0
    lower_timestamp = request_timestamp
    for service, process in branch_processes.items():
        key = (service, process)
        created = created_resources.get(key, {})
        deleted = deleted_resources.get(key, set())
        lower_created.update(
            {"{}:{}".format(service, location): nf for location, nf in created.items()}
        )
        lower_deleted.update("{}:{}".format(service, location) for location in deleted)
        if (
            len(created) == 2
            and len(deleted.intersection(created)) == 2
            and not set(created).difference(deleted)
            and not set(deleted).difference(created)
        ):
            exact_branches += 1
        lower_timestamp = resource_timestamps.get(key, lower_timestamp)
    if lower_created or lower_deleted:
        result["lower_cleanup"] = _resource_milestone(
            lower_created,
            lower_deleted,
            lower_timestamp,
            branches=exact_branches,
        )

    for position in range(1, 5):
        service = "pymtlf-leaf-{}".format(position)
        events = result.pop("_leaf_events_{}".format(service), None)
        result["leaf_{}".format(position)] = milestone()
        if events is None:
            continue
        samples = ",".join(
            "{}:{}".format(round_indicator, events["samples"][round_indicator])
            for round_indicator in sorted(events["samples"])
        ) or "none"
        result["leaf_{}".format(position)] = milestone(
            events["timestamp"],
            "rounds={} samples={} final_validation={}".format(
                ",".join(map(str, sorted(events["rounds"]))) or "none",
                samples,
                str(events["validation"]).lower(),
            ),
        )
    return result


def milestone_value(detail, key):
    match = re.search(r"(?:^| ){}=([^ ]+)".format(re.escape(key)), detail)
    return match.group(1) if match else "unknown"


def _exact_resource_cleanup(value, expected, *, branches=None):
    exact = (
        milestone_value(value["detail"], "created") == str(expected)
        and milestone_value(value["detail"], "deleted") == str(expected)
        and milestone_value(value["detail"], "active") == "0"
        and milestone_value(value["detail"], "unknown_deletes") == "0"
    )
    if branches is not None:
        exact = exact and milestone_value(value["detail"], "branches") == str(branches)
    return exact


def _positive_round_samples(detail):
    value = milestone_value(detail, "samples")
    try:
        pairs = [item.split(":", 1) for item in value.split(",")]
        return (
            [int(round_indicator) for round_indicator, _samples in pairs] == [0, 1]
            and all(int(samples) > 0 for _round, samples in pairs)
        )
    except (TypeError, ValueError):
        return False


def _hierarchical_result(summary, coordinator_state):
    failure = latest_milestone(
        summary["failure"],
        summary["cleanup_failure"]["timestamp"],
        summary["cleanup_failure"]["detail"],
    )
    if failure["timestamp"] != "not-seen":
        return "outcome=failed evidence={}".format(
            failure["detail"].replace(" ", "_")
        )
    if summary["hierarchy_request"]["timestamp"] == "not-seen":
        if coordinator_state == "absent":
            return "outcome=not-started reason=coordinator-absent"
        if coordinator_state == "running":
            return "outcome=in-progress phase=starting"
        return "outcome=incomplete phase=starting coordinator_state={}".format(
            coordinator_state
        )

    published = summary["publication"]["timestamp"] != "not-seen"
    leaf_evidence = all(
        milestone_value(summary["leaf_{}".format(position)]["detail"], "rounds")
        == "0,1"
        and milestone_value(
            summary["leaf_{}".format(position)]["detail"], "final_validation"
        )
        == "true"
        and _positive_round_samples(
            summary["leaf_{}".format(position)]["detail"]
        )
        for position in range(1, 5)
    )
    branch_evidence = (
        milestone_value(summary["root_preparation"]["detail"], "participants") == "2"
        and milestone_value(summary["branch_preparation"]["detail"], "prepared") == "2"
        and milestone_value(
            summary["branch_preparation"]["detail"], "participants_exact"
        )
        == "2"
    )
    publication_exact = (
        published
        and milestone_value(summary["publication"]["detail"], "state") == "COMPLETE"
        and milestone_value(summary["publication"]["detail"], "required_scopes") == "0"
    )
    cleanup_exact = _exact_resource_cleanup(summary["upper_cleanup"], 2) and (
        _exact_resource_cleanup(summary["lower_cleanup"], 4, branches=2)
    )
    validation_seen = summary["validation"]["timestamp"] != "not-seen"

    if published:
        if not leaf_evidence:
            return "outcome=verification-incomplete phase=leaf-evidence"
        if not branch_evidence:
            return "outcome=verification-incomplete phase=hierarchy-preparation"
        if not validation_seen:
            return "outcome=verification-incomplete phase=final-validation"
        if not publication_exact:
            return "outcome=verification-incomplete phase=publication"
        if not cleanup_exact:
            return "outcome=verification-incomplete phase=cleanup"
        return (
            "outcome=verification-incomplete phase=top-level-status model={} "
            "evidence=hierarchical-publication-and-cleanup"
        ).format(milestone_value(summary["publication"]["detail"], "model"))

    phases = (
        ("validation", "final-validation"),
        ("leaf_1", "leaf-training"),
        ("branch_preparation", "branch-preparation"),
        ("root_preparation", "root-preparation"),
        ("hierarchy_request", "root-request"),
    )
    phase = next(
        (
            name
            for milestone_name, name in phases
            if summary[milestone_name]["timestamp"] != "not-seen"
        ),
        "starting",
    )
    if coordinator_state == "running":
        return "outcome=in-progress phase={}".format(phase)
    return "outcome=incomplete phase={} coordinator_state={}".format(
        phase, coordinator_state
    )


def fl_result(summary, coordinator_state, *, static=False, hierarchical=False):
    if hierarchical:
        return _hierarchical_result(summary, coordinator_state)
    if static:
        completion = summary["completion"]
        failure = latest_milestone(
            summary["failure"],
            summary["cleanup_failure"]["timestamp"],
            summary["cleanup_failure"]["detail"],
        )
        complete_time = timestamp_order(completion["timestamp"])
        failure_time = timestamp_order(failure["timestamp"])
        if failure["timestamp"] != "not-seen" and (
            completion["timestamp"] == "not-seen"
            or complete_time is None
            or failure_time is None
            or failure_time >= complete_time
        ):
            return "outcome=failed evidence={}".format(failure["detail"].replace(" ", "_"))
        if (
            completion["timestamp"] != "not-seen"
            and milestone_value(completion["detail"], "state") == "COMPLETE"
            and summary["publication"]["timestamp"] != "not-seen"
            and milestone_value(summary["publication"]["detail"], "required_scopes") == "0"
            and milestone_value(summary["cleanup"]["detail"], "created") == "4"
            and milestone_value(summary["cleanup"]["detail"], "deleted") == "4"
            and milestone_value(summary["cleanup"]["detail"], "active") == "0"
            and milestone_value(summary["cleanup"]["detail"], "unknown_deletes") == "0"
        ):
            return "outcome=complete model={} evidence=static-publication".format(
                milestone_value(summary["publication"]["detail"], "model")
            )
        if (
            completion["timestamp"] != "not-seen"
            and milestone_value(completion["detail"], "state") == "COMPLETE"
            and summary["publication"]["timestamp"] != "not-seen"
        ):
            return "outcome=verification-incomplete phase=cleanup"
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
        ("completion", "static-completion"),
        ("cleanup", "participant-cleanup"),
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


def print_fl_summary(
    by_service,
    coordinator_name="pymtlf-c",
    cache_dir=None,
    deployment_kind=None,
):
    static_flat = coordinator_name == "pymtlf-server"
    static_hierarchical = coordinator_name == "pymtlf-root"
    supported = coordinator_name == "pymtlf-c" or static_flat or static_hierarchical
    coordinator = by_service.get(coordinator_name)
    if coordinator is None:
        print("FL CURRENT RUN coordinator={} container=absent milestones=not-seen".format(coordinator_name))
        if supported:
            print(
                "FL RESULT {}".format(
                    fl_result(
                        parse_fl_milestones({}, coordinator_name),
                        "absent",
                        static=static_flat,
                        hierarchical=static_hierarchical,
                    )
                )
            )
        else:
            print("FL RESULT outcome=not-started reason=coordinator-absent")
        return
    labels = coordinator.get("Config", {}).get("Labels", {})
    identity = "{}:{}".format(
        labels.get("io.5g-nwdaf.config-set", "unknown"),
        labels.get("io.5g-nwdaf.config-hash", "unknown")[:12],
    )
    started_at = coordinator.get("State", {}).get("StartedAt", "unknown")
    coordinator_state = (
        "running" if coordinator.get("State", {}).get("Running")
        else coordinator.get("State", {}).get("Status", "stopped")
    )
    if not supported:
        print(
            "FL CURRENT RUN config={} coordinator={} coordinator_started_at={}".format(
                identity, coordinator_name, started_at
            )
        )
        print(
            "FL RESULT outcome={} topology=static-hierarchical milestones=not-evaluated".format(
                "in-progress" if coordinator_state == "running" else "not-started"
            )
        )
        return
    for service in by_service:
        if not service.startswith("pymtlf-") or service == coordinator_name:
            continue
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
                "FL current-run config identity mismatch: {}={} {}={}".format(
                    coordinator_name, identity, service, service_identity
                )
            )
    if deployment_kind == "protocol-hierarchical":
        print(
            "FL CURRENT RUN config={} coordinator={} coordinator_started_at={}".format(
                identity, coordinator_name, started_at
            )
        )
        print(
            "FL RESULT outcome=not-evaluated topology=protocol-hierarchical "
            "status=use-fl-training-status"
        )
        return
    selected_fl_services = tuple(
        service for service in by_service if service.startswith("pymtlf-")
    )
    logs = {
        service: container_logs_since(
            by_service[service], service=service, cache_dir=cache_dir
        )
        for service in selected_fl_services
    }
    summary = parse_fl_milestones(logs, coordinator_name)
    print(
        "FL CURRENT RUN config={} coordinator={} coordinator_started_at={}".format(
            identity, coordinator_name, started_at
        )
    )
    print("{:<24} {:<30} {}".format("MILESTONE", "TIMESTAMP", "EVIDENCE"))
    for name, value in summary.items():
        print("{:<24} {:<30} {}".format(name, value["timestamp"], value["detail"]))
    print(
        "FL RESULT {}".format(
            fl_result(
                summary,
                coordinator_state,
                static=static_flat,
                hierarchical=static_hierarchical,
            )
        )
    )


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
    parser.add_argument("--services", default=",".join(DEFAULT_SERVICES))
    parser.add_argument("--coordinator", default="pymtlf-c")
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
    identity_mismatches = []
    for container in containers:
        labels = container.get("Config", {}).get("Labels", {})
        service = labels.get("com.docker.compose.service")
        if service not in services:
            unexpected.append(container)
            continue
        if service in by_service:
            duplicates.add(service)
        by_service[service] = container
        identity_mismatch = (
            labels.get("io.5g-nwdaf.config-set") != args.config_set
            or labels.get("io.5g-nwdaf.config-hash") != args.config_hash
        )
        if identity_mismatch and not (
            args.allow_stopped_selected_mismatch
            and not container.get("State", {}).get("Running")
        ):
            identity_mismatches.append(service)

    if duplicates:
        print("ERROR duplicate services: {}".format(", ".join(sorted(duplicates))), file=sys.stderr)
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
            status = container.get("State", {}).get("Status", "unknown")
            details.append("{}:{}".format(identity, status))
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
        "{:<20} {:<9} {:<9} {:<7} {:<8} {:<20} {:<6} {:<21} {:<12} {:<12} {}".format(
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
    for service in services:
        container = by_service.get(service)
        if container is None:
            print("{:<20} absent".format(service))
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
            "{:<20} {:<9} {:<9} {:<7} {:<8} {:<20} {:<6} {:<21} {:<12} {:<12} {}".format(
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

    for container in unexpected:
        labels = container.get("Config", {}).get("Labels", {})
        print(
            "UNEXPECTED service={} state={} config={}:{}".format(
                labels.get("com.docker.compose.service", "unknown"),
                container.get("State", {}).get("Status", "unknown"),
                labels.get("io.5g-nwdaf.config-set", "unknown"),
                labels.get("io.5g-nwdaf.config-hash", "unknown")[:12],
            )
        )

    print()
    print_fl_summary(
        by_service,
        args.coordinator,
        cache_dir,
        deployment_kind=args.deployment_kind,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
