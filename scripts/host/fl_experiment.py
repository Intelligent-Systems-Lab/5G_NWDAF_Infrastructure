#!/usr/bin/env python3
"""Protocol hierarchical FL experiment contracts, events, and evidence checks."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from configlib import (
    expected_runtime_inventory, nwdaf_definitions, protocol_topology, resolve_fault_targets,
)


class FLExperimentError(RuntimeError):
    """A fail-closed experiment contract or evidence violation."""


def parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise FLExperimentError("recordedAt must be a non-empty timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise FLExperimentError("recordedAt is not a valid timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FLExperimentError("recordedAt must include a timezone")
    return parsed.astimezone(timezone.utc)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


RUN_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def validate_run_name(value: object) -> str:
    """Return one safe operator-facing run directory name."""
    if not isinstance(value, str) or RUN_NAME_PATTERN.fullmatch(value) is None:
        raise FLExperimentError(
            "RUN_NAME must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}"
        )
    if value in {".", ".."}:
        raise FLExperimentError("RUN_NAME cannot be . or ..")
    return value


@dataclass(frozen=True)
class FLExperimentContract:
    dataset: str
    root_nf_instance_id: str
    selected_registration_ids: tuple[str, ...]
    fault_targets: tuple[dict, ...]
    samples_per_leaf: int
    local_epochs: int
    accepted_rounds: int
    normal_accepted_rounds: int
    poll_seconds: float
    heartbeat_seconds: int
    fault_enabled: bool

    @classmethod
    def build(cls, testbed: dict, scenario: dict) -> "FLExperimentContract":
        protocol_topology(
            testbed,
            scenario["training"]["localEpochs"],
            scenario["training"].get("proximalMu"),
            scenario["topology"]["onBranchFailure"],
        )
        definitions = {item["unit"]: item for item in nwdaf_definitions(testbed)}
        root = [item for item in definitions.values() if item["role"] == "root"]
        if len(root) != 1:
            raise FLExperimentError("protocol topology must have exactly one Root")
        runtime = expected_runtime_inventory(testbed, scenario)
        active_units = {item["unit"] for item in runtime["guestServices"]}
        common = {
            "dataset": scenario["workload"]["dataset"],
            "root_nf_instance_id": root[0]["nfInstanceId"],
            "selected_registration_ids": tuple(
                item["nfInstanceId"] for item in runtime["nwdafs"]
                if item["unit"] in active_units
            ) + (runtime["resetScope"]["adrf"]["nfInstanceId"],),
            "samples_per_leaf": scenario["partition"]["samplesPerLeaf"],
            "local_epochs": scenario["training"]["localEpochs"],
            "accepted_rounds": scenario["training"]["acceptedRounds"],
        }
        if scenario.get("fault") is None:
            return cls(
                **common,
                fault_targets=(),
                normal_accepted_rounds=scenario["training"]["acceptedRounds"],
                poll_seconds=0.25,
                heartbeat_seconds=30,
                fault_enabled=False,
            )
        resolved = resolve_fault_targets(testbed, scenario)
        fault = scenario["fault"]
        observation = scenario["observation"]
        return cls(
            **common,
            fault_targets=tuple(resolved["targets"]),
            normal_accepted_rounds=fault["normalAcceptedRounds"],
            poll_seconds=observation["pollIntervalMilliseconds"] / 1000,
            heartbeat_seconds=observation["heartbeatSeconds"],
            fault_enabled=True,
        )


class IncrementalJsonlReader:
    """Decode complete JSONL records while retaining one incomplete trailing line."""

    def __init__(self, read_chunk: Callable[[int], tuple[int, bytes]]) -> None:
        self._read_chunk = read_chunk
        self.offset = 0
        self._buffer = b""

    def poll(self) -> list[dict]:
        next_offset, chunk = self._read_chunk(self.offset)
        if next_offset < self.offset or next_offset != self.offset + len(chunk):
            raise FLExperimentError("observation source offset changed unexpectedly")
        self.offset = next_offset
        self._buffer += chunk
        lines = self._buffer.split(b"\n")
        self._buffer = lines.pop()
        values = []
        for raw in lines:
            if not raw:
                raise FLExperimentError("observation stream contains an empty record")
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise FLExperimentError("observation stream contains invalid JSON") from error
            if not isinstance(value, dict):
                raise FLExperimentError("observation record must be an object")
            values.append(value)
        return values

    def finish(self) -> None:
        if self._buffer:
            raise FLExperimentError("observation stream ends with a partial record")


class PhaseTracker:
    """Track the fault barrier and completed Root rounds without judging repair."""

    def __init__(self, contract: FLExperimentContract, plan_id: str) -> None:
        self.contract = contract
        self.plan_id = plan_id
        self.stop_at: datetime | None = None
        self.initial_evaluation: dict | None = None
        self.global_evaluations: dict[int, dict] = {}
        self.outcomes: dict[int, dict] = {}
        self.accepted: list[dict] = []
        self.final_model_saved: dict | None = None
        self._last_recorded_at: datetime | None = None

    def mark_stopped(self, recorded_at: str) -> None:
        if not self.contract.fault_enabled:
            raise FLExperimentError("normal run must not stop a node")
        if self.stop_at is not None:
            raise FLExperimentError("first fault stop was recorded more than once")
        if len(self.accepted) < self.contract.normal_accepted_rounds:
            raise FLExperimentError("fault stop preceded the selected accepted-round barrier")
        self.stop_at = parse_timestamp(recorded_at)

    def ready_for_fault(self, status: dict) -> bool:
        if not self.contract.fault_enabled:
            return False
        if self.stop_at is not None or len(self.accepted) != self.contract.normal_accepted_rounds:
            return False
        if status.get("completedRounds") != self.contract.normal_accepted_rounds:
            return False
        if status.get("state") not in {"ROUND_DISPATCH", "ROUND_WAITING"}:
            return False
        current = status.get("currentRound")
        if not isinstance(current, int) or isinstance(current, bool):
            return False
        return current > self.accepted[-1]["roundInd"]

    def ingest(self, record: dict) -> str | None:
        timestamp = parse_timestamp(record.get("recordedAt"))
        if self._last_recorded_at is not None and timestamp < self._last_recorded_at:
            raise FLExperimentError("Root observation timestamps are out of order")
        self._last_recorded_at = timestamp
        if record.get("mlCorreId") != self.plan_id:
            raise FLExperimentError("Root observation mlCorreId differs from planId")
        if record.get("nfInstanceId") != self.contract.root_nf_instance_id:
            raise FLExperimentError("Root observation has an unexpected NF instance ID")
        record_type = record.get("recordType")
        if record_type == "MODEL_EVALUATION":
            self._ingest_evaluation(record)
            return None
        if record_type == "ROUND_AGGREGATION":
            return self._ingest_outcome(record)
        if record_type == "MODEL_ARTIFACT_SAVED":
            self._ingest_final_model(record)
            return "final-model-saved"
        return None

    def _round(self, record: dict) -> int:
        value = record.get("roundInd")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise FLExperimentError("roundInd must be a non-negative integer")
        return value

    def _ingest_evaluation(self, record: dict) -> None:
        stage = record.get("evaluationStage")
        for field in ("loss", "accuracy"):
            value = record.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise FLExperimentError("model evaluation contains a non-finite metric")
        if stage == "ROOT_INITIAL":
            if "roundInd" in record or self.initial_evaluation is not None:
                raise FLExperimentError("Root initial evaluation is duplicated or has a round")
            self.initial_evaluation = record
            return
        if stage != "ROOT_GLOBAL":
            raise FLExperimentError("Root stream contains a non-Root evaluation stage")
        round_indicator = self._round(record)
        if round_indicator in self.global_evaluations:
            raise FLExperimentError("Root global evaluation is duplicated")
        self.global_evaluations[round_indicator] = record

    def _identities(self, record: dict, field: str) -> tuple[str, ...]:
        values = record.get(field)
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise FLExperimentError("{} must be an identity list".format(field))
        if len(values) != len(set(values)):
            raise FLExperimentError("{} contains duplicate identities".format(field))
        return tuple(values)

    def _ingest_outcome(self, record: dict) -> str | None:
        round_indicator = self._round(record)
        if round_indicator in self.outcomes:
            raise FLExperimentError("Root round outcome is duplicated")
        selected = self._identities(record, "selectedNfInstanceIds")
        successful = self._identities(record, "successfulNfInstanceIds")
        failed = self._identities(record, "failedNfInstanceIds")
        if set(successful) & set(failed) or not (set(successful) | set(failed)) <= set(selected):
            raise FLExperimentError("Root outcome participant sets are inconsistent")
        accepted = record.get("accepted")
        if not isinstance(accepted, bool):
            raise FLExperimentError("Root outcome accepted must be boolean")
        self.outcomes[round_indicator] = record
        if not accepted:
            return "round-rejected"
        if len(self.accepted) >= self.contract.accepted_rounds:
            raise FLExperimentError("Root produced more accepted rounds than configured")
        interpreted = {
            "roundInd": round_indicator,
            "phase": "afterFault" if self.stop_at is not None else "beforeFault",
            "recordedAt": record["recordedAt"],
            "selectedNfInstanceIds": list(selected),
            "successfulNfInstanceIds": list(successful),
            "failedNfInstanceIds": list(failed),
        }
        self.accepted.append(interpreted)
        return "round-accepted"

    def _ingest_final_model(self, record: dict) -> None:
        if self.final_model_saved is not None:
            raise FLExperimentError("final model record is duplicated")
        if len(self.accepted) != self.contract.accepted_rounds:
            raise FLExperimentError("final model record precedes the final accepted round")
        round_indicator = self._round(record)
        if round_indicator != self.accepted[-1]["roundInd"]:
            raise FLExperimentError("final model round differs from the final accepted round")
        if record.get("artifactFile") != "final-model.tar.gz":
            raise FLExperimentError("final model filename is not canonical")
        self.final_model_saved = {
            "roundInd": round_indicator,
            "artifactFile": record["artifactFile"],
        }

    def finalize(self, terminal_status: dict) -> dict:
        if terminal_status.get("state") != "COMPLETE":
            raise FLExperimentError("training resource did not reach COMPLETE")
        if terminal_status.get("completedRounds") != self.contract.accepted_rounds:
            raise FLExperimentError("training status completedRounds differs from scenario")
        if len(self.accepted) != self.contract.accepted_rounds:
            raise FLExperimentError("Root evidence lacks the selected accepted rounds")
        if self.contract.fault_enabled and self.stop_at is None:
            raise FLExperimentError("selected fault was not injected")
        if not self.contract.fault_enabled and self.stop_at is not None:
            raise FLExperimentError("normal run contains a fault stop")
        accepted_indices = {item["roundInd"] for item in self.accepted}
        rejected_indices = {
            index for index, record in self.outcomes.items() if record.get("accepted") is False
        }
        if set(self.global_evaluations) != accepted_indices:
            raise FLExperimentError(
                "accepted outcomes and Root global evaluations are not one-to-one"
            )
        if set(self.global_evaluations) & rejected_indices:
            raise FLExperimentError("rejected round has a Root global evaluation")
        if self.initial_evaluation is None:
            raise FLExperimentError("Root initial evaluation is missing")
        candidate = terminal_status.get("candidateDigest")
        if not isinstance(candidate, str) or re.fullmatch(r"[0-9a-f]{64}", candidate) is None:
            raise FLExperimentError("terminal candidate artifact identity is missing")
        if self.final_model_saved is None:
            raise FLExperimentError("final model record is missing")
        if terminal_status.get("currentRound") != self.final_model_saved["roundInd"]:
            raise FLExperimentError("final model round differs from terminal status")
        self.final_model_saved["artifactDigest"] = candidate
        return self.summary()

    def summary(self) -> dict:
        phase_counts = {
            phase: sum(item["phase"] == phase for item in self.accepted)
            for phase in ("beforeFault", "afterFault")
        }
        return {
            "acceptedRoundCount": len(self.accepted),
            "phaseCounts": phase_counts,
            "rounds": list(self.accepted),
        }


class EvidenceWriter:
    """Write the two-file experiment evidence contract."""

    def __init__(self, run_directory: Path, initial_run: dict) -> None:
        self.run_directory = run_directory
        self.events_path = run_directory / "events.jsonl"
        self.run_path = run_directory / "run.json"
        run_directory.mkdir(parents=True, exist_ok=False)
        self.run = dict(initial_run)
        self._last_event_at: datetime | None = None
        self.checkpoint()

    @classmethod
    def open_existing(cls, run_directory: Path) -> "EvidenceWriter":
        run_path = run_directory / "run.json"
        events_path = run_directory / "events.jsonl"
        if not run_directory.is_dir() or not run_path.is_file():
            raise FLExperimentError("collection checkpoint does not exist")
        try:
            run = json.loads(run_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise FLExperimentError("collection checkpoint run.json is invalid") from error
        if not isinstance(run, dict):
            raise FLExperimentError("collection checkpoint run.json must be an object")
        records = load_wrapped_events(events_path) if events_path.is_file() else []
        writer = cls.__new__(cls)
        writer.run_directory = run_directory
        writer.events_path = events_path
        writer.run_path = run_path
        writer.run = run
        writer._last_event_at = (
            parse_timestamp(records[-1]["recordedAt"]) if records else None
        )
        return writer

    def events(self) -> list[dict]:
        if not self.events_path.is_file():
            return []
        return load_wrapped_events(self.events_path)

    def append(self, source: str, event_type: str, payload: dict, *, recorded_at: str | None = None, nf_instance_id: str | None = None) -> dict:
        timestamp = recorded_at or utc_now()
        parsed = parse_timestamp(timestamp)
        if self._last_event_at is not None and parsed < self._last_event_at:
            raise FLExperimentError("events.jsonl would not be chronological")
        self._last_event_at = parsed
        record = {
            "recordedAt": timestamp,
            "source": source,
            "eventType": event_type,
            "payload": payload,
        }
        if nf_instance_id is not None:
            record["nfInstanceId"] = nf_instance_id
        serialized = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(serialized + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return record

    def append_root(self, payload: dict) -> dict:
        return self.append(
            "pymtlf-root",
            str(payload.get("recordType", "UNKNOWN")),
            payload,
            recorded_at=str(payload.get("recordedAt", "")),
            nf_instance_id=payload.get("nfInstanceId"),
        )

    def append_once(
        self,
        source: str,
        event_type: str,
        payload: dict,
        *,
        recorded_at: str | None = None,
        nf_instance_id: str | None = None,
    ) -> dict:
        matches = [
            record
            for record in self.events()
            if record["source"] == source and record["eventType"] == event_type
        ]
        if len(matches) > 1:
            raise FLExperimentError(
                "events contain duplicate {} {} records".format(source, event_type)
            )
        if matches:
            existing = matches[0]
            if (
                existing.get("payload") != payload
                or existing.get("nfInstanceId") != nf_instance_id
            ):
                raise FLExperimentError(
                    "existing {} {} event conflicts with retry output".format(
                        source, event_type
                    )
                )
            return existing
        return self.append(
            source,
            event_type,
            payload,
            recorded_at=recorded_at,
            nf_instance_id=nf_instance_id,
        )

    def update(self, **values: object) -> None:
        self.run.update(values)
        self.checkpoint()

    def checkpoint(self) -> None:
        self.run_directory.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=".run.", suffix=".json", dir=self.run_directory
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.run, stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.run_path)
        finally:
            temporary.unlink(missing_ok=True)


def load_wrapped_events(path: Path) -> list[dict]:
    records = []
    last_timestamp = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise FLExperimentError("events.jsonl line {} is invalid".format(number)) from error
        if not isinstance(record, dict) or not {
            "recordedAt", "source", "eventType", "payload",
        } <= set(record):
            raise FLExperimentError("events.jsonl line {} is incomplete".format(number))
        timestamp = parse_timestamp(record["recordedAt"])
        if last_timestamp is not None and timestamp < last_timestamp:
            raise FLExperimentError("events.jsonl timestamps are out of order")
        last_timestamp = timestamp
        records.append(record)
    return records


def check_evidence(
    run_directory: Path,
    contract: FLExperimentContract,
    *,
    require_cleanup: bool = True,
) -> dict:
    run_path = run_directory / "run.json"
    events_path = run_directory / "events.jsonl"
    artifact_path = run_directory / "final-root-model.tar.gz"
    if not run_path.is_file() or not events_path.is_file() or not artifact_path.is_file():
        raise FLExperimentError("successful run evidence is missing a required file")
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise FLExperimentError("run.json is invalid") from error
    if require_cleanup:
        if run.get("status") != "successful" or run.get("finalized") is not True:
            raise FLExperimentError("run.json is not finalized as successful")
    elif (
        run.get("status") not in {"collection-pending", "collection-failed"}
        or run.get("finalized") is not False
    ):
        raise FLExperimentError("run.json is not a retryable collection checkpoint")
    run_name = validate_run_name(run.get("runName"))
    if run_directory.name != run_name:
        raise FLExperimentError("run.json runName differs from its directory")
    request_id = run.get("requestId")
    try:
        parsed_request_id = uuid.UUID(request_id)
    except (AttributeError, TypeError, ValueError) as error:
        raise FLExperimentError("run.json requestId is not a UUIDv4") from error
    if parsed_request_id.version != 4 or str(parsed_request_id) != request_id:
        raise FLExperimentError("run.json requestId is not a canonical UUIDv4")
    workload = run.get("workload", {})
    if (
        run.get("dataset") != contract.dataset
        or not isinstance(workload, dict)
        or workload.get("samplesPerLeaf") != contract.samples_per_leaf
        or workload.get("localEpochs") != contract.local_epochs
        or workload.get("acceptedRounds") != contract.accepted_rounds
    ):
        raise FLExperimentError("run.json workload differs from the selected contract")
    if not contract.fault_enabled and ("fault" in run or "faultStops" in run):
        raise FLExperimentError("normal run contains a fault checkpoint")
    if contract.fault_enabled and (
        run.get("fault") != {
            **run.get("scenario", {}).get("fault", {}),
            "targets": list(contract.fault_targets),
        }
    ):
        raise FLExperimentError("run.json fault targets differ from the selected scenario")
    plan_id = run.get("planId")
    if not isinstance(plan_id, str) or run.get("mlCorreId") != plan_id:
        raise FLExperimentError("run.json planId is missing")
    events = load_wrapped_events(events_path)
    required_events = {
        ("controller", "GPU_ADMISSION"),
        ("controller", "RUNTIME_READY"),
        ("controller", "TRAINING_REQUEST_SUBMITTED"),
        ("controller", "FINAL_ARTIFACT_COLLECTED"),
        ("held-out-evaluator", "HELD_OUT_EVALUATION"),
    }
    if require_cleanup:
        required_events.add(("controller", "CLEANUP_COMPLETE"))
    indexed = {}
    for key in required_events:
        matches = [
            record for record in events
            if (record["source"], record["eventType"]) == key
        ]
        if len(matches) != 1:
            raise FLExperimentError(
                "events must contain exactly one {} {} record".format(*key)
            )
        indexed[key] = matches[0]

    admission = indexed[("controller", "GPU_ADMISSION")]["payload"]
    if (
        not isinstance(admission.get("participantCount"), int)
        or admission["participantCount"] <= 0
        or not isinstance(admission.get("minimumFreeMiB"), int)
        or admission["minimumFreeMiB"] <= 0
        or admission.get("memoryFreeMiB", -1) < admission["minimumFreeMiB"]
    ):
        raise FLExperimentError("GPU_ADMISSION does not meet the selected floor")
    runtime_ready = indexed[("controller", "RUNTIME_READY")]["payload"]
    virtual_machines = runtime_ready.get("virtualMachines", {})
    guest_services = runtime_ready.get("guestServices", {})
    registrations = runtime_ready.get("nrfRegistrations", {})
    selected_runtime = run.get("runtimeInventory", {})
    selected_guest_units = {
        item["unit"] for item in selected_runtime.get("guestServices", [])
    }
    if (
        set(virtual_machines) != set(selected_runtime.get("guestMachines", []))
        or set(virtual_machines.values()) != {"running"}
        or set(guest_services) != selected_guest_units
        or any(value.get("state") != "active" for value in guest_services.values())
    ):
        raise FLExperimentError("RUNTIME_READY VM or Guest inventory is incomplete")
    registered = registrations.get("nfInstanceIds", [])
    if (
        registrations.get("state") != "ready"
        or not isinstance(registered, list)
        or len(set(registered)) != len(registered)
        or set(registered) != set(contract.selected_registration_ids)
    ):
        raise FLExperimentError("RUNTIME_READY NRF registration inventory is incomplete")
    containers = runtime_ready.get("containers", {})
    devices = {
        name: value.get("device") for name, value in containers.items()
        if isinstance(value, dict)
    }
    if (
        set(devices) != set(selected_runtime.get("hostContainers", []))
        or sum(device == "cuda:0" for device in devices.values()) != admission["participantCount"]
        or any(device not in ("cuda:0", "cpu") for device in devices.values())
        or run.get("resolvedDevices") != devices
    ):
        raise FLExperimentError("RUNTIME_READY mixed-device inventory is inconsistent")
    for name, value in containers.items():
        expected_gpu = value.get("device") == "cuda:0"
        if value.get("state") != "running" or value.get("health") != "healthy":
            raise FLExperimentError("RUNTIME_READY contains an unhealthy container")
        if expected_gpu and (
            value.get("runtime") != "nvidia"
            or value.get("cdiSelector") != "nvidia.com/gpu=all"
            or value.get("cuda", {}).get("available") is not True
        ):
            raise FLExperimentError("RUNTIME_READY lacks actual CUDA evidence for " + name)
        if not expected_gpu and (
            value.get("runtime") == "nvidia" or value.get("cdiSelector") is not None
        ):
            raise FLExperimentError("RUNTIME_READY exposes a GPU to CPU-owned " + name)
    if run.get("image") != runtime_ready.get("image") or run.get("gpu") != runtime_ready.get("gpu"):
        raise FLExperimentError("run runtime identity differs from RUNTIME_READY")
    active_identity = run.get("activeIdentity", {})
    if (
        active_identity.get("config") != runtime_ready.get("activeConfig")
        or active_identity.get("guestServices") != guest_services
        or active_identity.get("containers") != containers
    ):
        raise FLExperimentError("run active identity differs from RUNTIME_READY")
    submitted = indexed[("controller", "TRAINING_REQUEST_SUBMITTED")]["payload"]
    if submitted.get("requestId") != request_id:
        raise FLExperimentError("training request run identity is mismatched")
    observations = run.get("rawObservations")
    if not isinstance(observations, dict) or set(observations) != set(
        selected_runtime.get("hostContainers", [])
    ):
        raise FLExperimentError("raw observation inventory differs from active containers")
    if observations.get("pymtlf-root", {}).get("state") != "collected":
        raise FLExperimentError("Root raw observations are missing")
    for service, entry in observations.items():
        relative = "observations/{}.jsonl".format(service)
        path = run_directory / relative
        if (
            entry.get("state") != "collected"
            or entry.get("path") != relative
            or not path.is_file()
            or entry.get("bytes") != path.stat().st_size
            or path.stat().st_size <= 0
        ):
            raise FLExperimentError("raw observations are incomplete for " + service)

    tracker = PhaseTracker(contract, plan_id)
    stop_records = []
    for record in events:
        if record["source"] == "controller" and record["eventType"] == "NODE_PROCESS_STOPPED":
            if not stop_records:
                tracker.mark_stopped(record["recordedAt"])
            stop_records.append(record)
        elif record["source"] == "pymtlf-root":
            tracker.ingest(record["payload"])
    if len(stop_records) != len(contract.fault_targets):
        raise FLExperimentError("confirmed stop events differ from selected fault targets")
    if contract.fault_enabled and run.get("faultStops") != [
        record["payload"] for record in stop_records
    ]:
        raise FLExperimentError("run.json fault stops differ from controller events")
    for stop_record, target in zip(stop_records, contract.fault_targets):
        stop_payload = stop_record["payload"]
        guest_stop = stop_payload.get("guest", {})
        container_stop = stop_payload.get("container", {})
        hard_stopped_at = stop_payload.get("hardStoppedAt")
        if (
            stop_payload.get("effectiveAt") != stop_record["recordedAt"]
            or not isinstance(hard_stopped_at, str)
            or parse_timestamp(hard_stopped_at) < parse_timestamp(stop_record["recordedAt"])
        ):
            raise FLExperimentError("confirmed stop timing is inconsistent")
        if (
            stop_record.get("nfInstanceId") != target["nfInstanceId"]
            or stop_payload.get("nfInstanceId") != target["nfInstanceId"]
            or stop_payload.get("guestStopped") is not True
            or stop_payload.get("containerStopped") is not True
            or not isinstance(guest_stop, dict)
            or guest_stop.get("machine") != target["machine"]
            or guest_stop.get("unit") != target["unit"]
            or not isinstance(guest_stop.get("originalPid"), int)
            or isinstance(guest_stop.get("originalPid"), bool)
            or guest_stop["originalPid"] <= 0
            or guest_stop.get("freezeSignal") != "SIGSTOP"
            or guest_stop.get("signal") != "SIGKILL"
            or guest_stop.get("activeState") not in {"inactive", "failed"}
            or not isinstance(guest_stop.get("subState"), str)
            or not guest_stop["subState"]
            or guest_stop.get("restartSuppressed") is not True
            or not isinstance(container_stop, dict)
            or container_stop.get("service") != target["service"]
            or not isinstance(container_stop.get("containerId"), str)
            or not container_stop["containerId"]
            or not isinstance(container_stop.get("originalPid"), int)
            or isinstance(container_stop.get("originalPid"), bool)
            or container_stop["originalPid"] <= 0
            or container_stop.get("freezeSignal") != "SIGSTOP"
            or container_stop.get("signal") != "SIGKILL"
            or container_stop.get("exitCode") != 137
            or container_stop.get("restartPolicy") != "no"
            or not isinstance(container_stop.get("restartCount"), int)
            or isinstance(container_stop.get("restartCount"), bool)
            or container_stop["restartCount"] < 0
        ):
            raise FLExperimentError("confirmed stop target or postcondition is invalid")
    summary = tracker.finalize(run.get("terminalStatus", {}))
    tracker.final_model_saved["sizeBytes"] = artifact_path.stat().st_size
    if run.get("phases") != summary:
        raise FLExperimentError("run.json phase summary differs from events.jsonl")
    if run.get("finalModel") != tracker.final_model_saved:
        raise FLExperimentError("run.json final model checkpoint differs from Root evidence")
    held_out = run.get("heldOutEvaluation")
    if not isinstance(held_out, dict) or held_out.get("run_id") != request_id:
        raise FLExperimentError("run.json held-out evaluation is missing or mismatched")
    if indexed[("held-out-evaluator", "HELD_OUT_EVALUATION")]["payload"] != held_out:
        raise FLExperimentError("held-out event differs from run.json")
    if run.get("finalArtifact") != "final-root-model.tar.gz":
        raise FLExperimentError("run.json final artifact path is not canonical")
    collected = indexed[("controller", "FINAL_ARTIFACT_COLLECTED")]["payload"]
    if (
        collected.get("path") != run.get("finalArtifact")
        or collected.get("artifactKey") != run.get("finalArtifactIdentity")
        or collected.get("sizeBytes") != artifact_path.stat().st_size
        or run.get("finalArtifactSizeBytes") != artifact_path.stat().st_size
        or tracker.final_model_saved.get("sizeBytes") != artifact_path.stat().st_size
        or run.get("finalArtifactIdentity") != run.get("terminalStatus", {}).get(
            "candidateDigest"
        )
    ):
        raise FLExperimentError("final artifact event and terminal identity differ")
    if require_cleanup:
        cleanup = run.get("cleanup", {})
        if (
            cleanup.get("processesStopped") is not True
            or cleanup.get("guestRestartPolicyRestored") is not True
            or cleanup.get("applied") is not True
            or cleanup.get("resetVerified") is not True
        ):
            raise FLExperimentError("run cleanup/reset evidence is incomplete")
        if indexed[("controller", "CLEANUP_COMPLETE")]["payload"] != cleanup:
            raise FLExperimentError("cleanup event differs from run.json")
    return run
