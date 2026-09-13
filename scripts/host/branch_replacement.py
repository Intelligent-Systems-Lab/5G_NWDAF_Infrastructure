#!/usr/bin/env python3
"""Branch-replacement experiment contracts, event parsing, and evidence checks."""

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

from configlib import nwdaf_definitions, resolve_branch_replacement


class BranchReplacementError(RuntimeError):
    """A fail-closed branch-replacement contract or evidence violation."""


def parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise BranchReplacementError("recordedAt must be a non-empty timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise BranchReplacementError("recordedAt is not a valid timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BranchReplacementError("recordedAt must include a timezone")
    return parsed.astimezone(timezone.utc)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


RUN_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def validate_run_name(value: object) -> str:
    """Return one safe operator-facing run directory name."""
    if not isinstance(value, str) or RUN_NAME_PATTERN.fullmatch(value) is None:
        raise BranchReplacementError(
            "RUN_NAME must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}"
        )
    if value in {".", ".."}:
        raise BranchReplacementError("RUN_NAME cannot be . or ..")
    return value


@dataclass(frozen=True)
class BranchReplacementContract:
    dataset: str
    root_nf_instance_id: str
    primary_nf_instance_id: str
    replacement_nf_instance_id: str
    primary_unit: str
    primary_machine: str
    primary_service: str
    fault_group_leaf_nf_instance_ids: tuple[str, ...]
    surviving_nf_instance_ids: tuple[str, ...]
    samples_per_leaf: int
    local_epochs: int
    accepted_rounds: int
    normal_accepted_rounds: int
    restored_accepted_rounds: int
    poll_seconds: float
    heartbeat_seconds: int

    @classmethod
    def build(cls, testbed: dict, scenario: dict) -> "BranchReplacementContract":
        target = resolve_branch_replacement(testbed, scenario)
        definitions = {item["unit"]: item for item in nwdaf_definitions(testbed)}
        root = [item for item in definitions.values() if item["role"] == "root"]
        if len(root) != 1:
            raise BranchReplacementError("protocol topology must have exactly one Root")
        groups = testbed["analytics"]["protocolTopology"]["branchGroups"]
        fault_group = next(group for group in groups if group["name"] == target["group"])
        fault_leaves = tuple(
            definitions[item["node"]]["nfInstanceId"]
            for item in fault_group["leaves"]
            if item.get("enabled") is True
        )
        if len(fault_leaves) != 2:
            raise BranchReplacementError("replacement acceptance requires two Area leaves")
        survivors = []
        for group in groups:
            if group["name"] == target["group"]:
                continue
            enabled = [item for item in group["branches"] if item.get("enabled") is True]
            if not enabled:
                raise BranchReplacementError("surviving Branch group has no enabled candidate")
            priorities = [item["priority"] for item in enabled]
            if len(priorities) != len(set(priorities)):
                raise BranchReplacementError("surviving Branch priorities are ambiguous")
            selected = max(enabled, key=lambda item: item["priority"])
            survivors.append(definitions[selected["node"]]["nfInstanceId"])
        if len(survivors) != 2:
            raise BranchReplacementError("replacement acceptance requires two surviving regions")
        fault = scenario["fault"]
        observation = scenario["observation"]
        primary = target["primary"]
        return cls(
            dataset=scenario["workload"]["dataset"],
            root_nf_instance_id=root[0]["nfInstanceId"],
            primary_nf_instance_id=primary["nfInstanceId"],
            replacement_nf_instance_id=target["replacement"]["nfInstanceId"],
            primary_unit=primary["unit"],
            primary_machine=primary["machine"],
            primary_service=primary["backends"]["mtlf"],
            fault_group_leaf_nf_instance_ids=fault_leaves,
            surviving_nf_instance_ids=tuple(survivors),
            samples_per_leaf=scenario["partition"]["samplesPerLeaf"],
            local_epochs=scenario["training"]["localEpochs"],
            accepted_rounds=scenario["training"]["acceptedRounds"],
            normal_accepted_rounds=fault["normalAcceptedRounds"],
            restored_accepted_rounds=fault["restoredAcceptedRounds"],
            poll_seconds=observation["pollIntervalMilliseconds"] / 1000,
            heartbeat_seconds=observation["heartbeatSeconds"],
        )


RESOURCE_RECORD_PATTERN = re.compile(
    r"FL participant resource created process_id=(?P<plan>\S+) "
    r"nf=(?P<nf>\S+) notif_corre_id=(?P<notification>\S+) "
    r"location=(?P<location>\S+)"
)


def parse_resource_log(value: str, plan_id: str) -> list[dict[str, str]]:
    """Extract component-owned participant resource identities for one plan."""
    records = []
    for line in value.splitlines():
        match = RESOURCE_RECORD_PATTERN.search(line)
        if match is None or match.group("plan") != plan_id:
            continue
        records.append(
            {
                "planId": match.group("plan"),
                "nfInstanceId": match.group("nf"),
                "notifCorreId": match.group("notification"),
                "resourceLocation": match.group("location"),
            }
        )
    return records


class IncrementalJsonlReader:
    """Decode complete JSONL records while retaining one incomplete trailing line."""

    def __init__(self, read_chunk: Callable[[int], tuple[int, bytes]]) -> None:
        self._read_chunk = read_chunk
        self.offset = 0
        self._buffer = b""

    def poll(self) -> list[dict]:
        next_offset, chunk = self._read_chunk(self.offset)
        if next_offset < self.offset or next_offset != self.offset + len(chunk):
            raise BranchReplacementError("observation source offset changed unexpectedly")
        self.offset = next_offset
        self._buffer += chunk
        lines = self._buffer.split(b"\n")
        self._buffer = lines.pop()
        values = []
        for raw in lines:
            if not raw:
                raise BranchReplacementError("observation stream contains an empty record")
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise BranchReplacementError("observation stream contains invalid JSON") from error
            if not isinstance(value, dict):
                raise BranchReplacementError("observation record must be an object")
            values.append(value)
        return values

    def finish(self) -> None:
        if self._buffer:
            raise BranchReplacementError("observation stream ends with a partial record")


class PhaseTracker:
    """Validate Root observations and derive natural replacement phases."""

    def __init__(self, contract: BranchReplacementContract, plan_id: str) -> None:
        self.contract = contract
        self.plan_id = plan_id
        self.stop_at: datetime | None = None
        self.failure_detected_at: datetime | None = None
        self.replacement_ready_at: datetime | None = None
        self.first_contribution_at: datetime | None = None
        self.initial_evaluation: dict | None = None
        self.global_evaluations: dict[int, dict] = {}
        self.outcomes: dict[int, dict] = {}
        self.accepted: list[dict] = []
        self.final_model_saved: dict | None = None
        self._last_recorded_at: datetime | None = None

    def mark_stopped(self, recorded_at: str) -> None:
        if self.stop_at is not None:
            raise BranchReplacementError("primary stop was recorded more than once")
        if len(self.accepted) != self.contract.normal_accepted_rounds:
            raise BranchReplacementError("primary stop did not follow the selected normal rounds")
        self.stop_at = parse_timestamp(recorded_at)

    def ready_for_fault(self, status: dict) -> bool:
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
            raise BranchReplacementError("Root observation timestamps are out of order")
        self._last_recorded_at = timestamp
        if record.get("mlCorreId") != self.plan_id:
            raise BranchReplacementError("Root observation mlCorreId differs from planId")
        if record.get("nfInstanceId") != self.contract.root_nf_instance_id:
            raise BranchReplacementError("Root observation has an unexpected NF instance ID")
        record_type = record.get("recordType")
        if record_type == "MODEL_EVALUATION":
            self._ingest_evaluation(record)
            return None
        if record_type == "ROOT_ROUND_OUTCOME":
            return self._ingest_outcome(record, timestamp)
        if record_type == "BRANCH_FAILURE_DETECTED":
            self._ingest_failure(record, timestamp)
            return "failure-detected"
        if record_type == "BRANCH_REPLACEMENT_READY":
            self._ingest_ready(record, timestamp)
            return "replacement-ready"
        if record_type == "FINAL_MODEL_SAVED":
            self._ingest_final_model(record)
            return "final-model-saved"
        raise BranchReplacementError("Root observation has an unsupported recordType")

    def _round(self, record: dict) -> int:
        value = record.get("roundInd")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise BranchReplacementError("roundInd must be a non-negative integer")
        return value

    def _ingest_evaluation(self, record: dict) -> None:
        stage = record.get("evaluationStage")
        for field in ("validationLoss", "validationAccuracy"):
            value = record.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise BranchReplacementError("model evaluation contains a non-finite metric")
        if stage == "ROOT_INITIAL":
            if "roundInd" in record or self.initial_evaluation is not None:
                raise BranchReplacementError("Root initial evaluation is duplicated or has a round")
            self.initial_evaluation = record
            return
        if stage != "ROOT_GLOBAL":
            raise BranchReplacementError("Root stream contains a non-Root evaluation stage")
        round_indicator = self._round(record)
        if round_indicator in self.global_evaluations:
            raise BranchReplacementError("Root global evaluation is duplicated")
        self.global_evaluations[round_indicator] = record

    def _identities(self, record: dict, field: str) -> tuple[str, ...]:
        values = record.get(field)
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise BranchReplacementError("{} must be an identity list".format(field))
        if len(values) != len(set(values)):
            raise BranchReplacementError("{} contains duplicate identities".format(field))
        return tuple(values)

    def _ingest_outcome(self, record: dict, timestamp: datetime) -> str | None:
        round_indicator = self._round(record)
        if round_indicator in self.outcomes:
            raise BranchReplacementError("Root round outcome is duplicated")
        selected = self._identities(record, "selectedNfInstanceIds")
        successful = self._identities(record, "successfulNfInstanceIds")
        failed = self._identities(record, "failedNfInstanceIds")
        if set(successful) & set(failed) or not (set(successful) | set(failed)) <= set(selected):
            raise BranchReplacementError("Root outcome participant sets are inconsistent")
        allowed = {
            self.contract.primary_nf_instance_id,
            self.contract.replacement_nf_instance_id,
            *self.contract.surviving_nf_instance_ids,
        }
        if not set(selected) <= allowed:
            raise BranchReplacementError("Root outcome contains an unknown Branch identity")
        accepted = record.get("accepted")
        if not isinstance(accepted, bool):
            raise BranchReplacementError("Root outcome accepted must be boolean")
        self.outcomes[round_indicator] = record
        if not accepted:
            return "round-rejected"
        if len(self.accepted) >= self.contract.accepted_rounds:
            raise BranchReplacementError("Root produced more accepted rounds than configured")
        phase = "normal"
        if self.stop_at is None:
            if len(self.accepted) >= self.contract.normal_accepted_rounds:
                raise BranchReplacementError("controller missed the post-normal in-flight barrier")
            if self.contract.replacement_nf_instance_id in selected:
                raise BranchReplacementError("replacement appeared before primary stop")
            expected = {
                self.contract.primary_nf_instance_id,
                *self.contract.surviving_nf_instance_ids,
            }
            if set(selected) != expected or set(successful) != expected or failed:
                raise BranchReplacementError("normal round did not contain the exact three regions")
        else:
            if timestamp < self.stop_at:
                raise BranchReplacementError("post-stop outcome predates the confirmed stop")
            if self.contract.primary_nf_instance_id in successful:
                raise BranchReplacementError("stopped primary contributed after fault injection")
            missing_survivors = set(self.contract.surviving_nf_instance_ids) - set(successful)
            if missing_survivors:
                raise BranchReplacementError("accepted post-stop round omitted a surviving region")
            if self.contract.replacement_nf_instance_id in successful:
                if self.replacement_ready_at is None:
                    raise BranchReplacementError("replacement contributed before its ready event")
                if self.contract.primary_nf_instance_id in selected:
                    raise BranchReplacementError("restored cohort retained the failed primary")
                expected_successful = set(self.contract.surviving_nf_instance_ids) | {
                    self.contract.replacement_nf_instance_id
                }
                if set(successful) != expected_successful:
                    raise BranchReplacementError(
                        "restored round successful set differs from the three regions"
                    )
                phase = "restored"
                if set(selected) != expected_successful or failed:
                    raise BranchReplacementError("restored round cohort is not exact")
                if self.first_contribution_at is None:
                    self.first_contribution_at = timestamp
            else:
                phase = "degraded"
                if self.contract.replacement_nf_instance_id in selected:
                    raise BranchReplacementError("ready replacement failed its selected cohort")
                if set(successful) != set(self.contract.surviving_nf_instance_ids):
                    raise BranchReplacementError(
                        "degraded round successful set differs from the surviving regions"
                    )
                if self.failure_detected_at is None:
                    expected_selected = set(self.contract.surviving_nf_instance_ids) | {
                        self.contract.primary_nf_instance_id
                    }
                    if set(selected) != expected_selected or set(failed) != {
                        self.contract.primary_nf_instance_id
                    }:
                        raise BranchReplacementError(
                            "first degraded outcome did not detect the stopped primary"
                        )
                elif set(selected) != set(self.contract.surviving_nf_instance_ids) or failed:
                    raise BranchReplacementError("later degraded round cohort is not exact")
        interpreted = {
            "roundInd": round_indicator,
            "phase": phase,
            "recordedAt": record["recordedAt"],
            "selectedNfInstanceIds": list(selected),
            "successfulNfInstanceIds": list(successful),
            "failedNfInstanceIds": list(failed),
        }
        self.accepted.append(interpreted)
        return "round-accepted"

    def _ingest_failure(self, record: dict, timestamp: datetime) -> None:
        if self.stop_at is None or timestamp < self.stop_at:
            raise BranchReplacementError("failure detection does not follow confirmed stop")
        if self.failure_detected_at is not None:
            raise BranchReplacementError("Branch failure detection is duplicated")
        if record.get("failedBranchNfInstanceId") != self.contract.primary_nf_instance_id:
            raise BranchReplacementError("failure detection identifies the wrong Branch")
        round_indicator = self._round(record)
        outcome = self.outcomes.get(round_indicator)
        if (
            outcome is None
            or outcome.get("accepted") is not True
            or self.contract.primary_nf_instance_id
            not in outcome.get("failedNfInstanceIds", [])
        ):
            raise BranchReplacementError("failure detection lacks its accepted degraded outcome")
        self.failure_detected_at = timestamp

    def _ingest_ready(self, record: dict, timestamp: datetime) -> None:
        if self.failure_detected_at is None or timestamp < self.failure_detected_at:
            raise BranchReplacementError("replacement ready does not follow failure detection")
        if self.replacement_ready_at is not None:
            raise BranchReplacementError("replacement ready is duplicated")
        if record.get("failedBranchNfInstanceId") != self.contract.primary_nf_instance_id:
            raise BranchReplacementError("replacement ready identifies the wrong failed Branch")
        if record.get("replacementBranchNfInstanceId") != self.contract.replacement_nf_instance_id:
            raise BranchReplacementError("Root selected the wrong replacement priority candidate")
        self.replacement_ready_at = timestamp

    def _ingest_final_model(self, record: dict) -> None:
        if self.final_model_saved is not None:
            raise BranchReplacementError("final model record is duplicated")
        if len(self.accepted) != self.contract.accepted_rounds:
            raise BranchReplacementError("final model record precedes the final accepted round")
        round_indicator = self._round(record)
        digest = record.get("artifactDigest")
        size = record.get("sizeBytes")
        if round_indicator != self.accepted[-1]["roundInd"]:
            raise BranchReplacementError("final model round differs from the final accepted round")
        if record.get("artifactFile") != "final-model.tar.gz":
            raise BranchReplacementError("final model filename is not canonical")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise BranchReplacementError("final model identity is invalid")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise BranchReplacementError("final model size must be positive")
        self.final_model_saved = {
            "roundInd": round_indicator,
            "artifactFile": record["artifactFile"],
            "artifactDigest": digest,
            "sizeBytes": size,
        }

    def finalize(self, terminal_status: dict) -> dict:
        if terminal_status.get("state") != "COMPLETE":
            raise BranchReplacementError("training resource did not reach COMPLETE")
        if terminal_status.get("completedRounds") != self.contract.accepted_rounds:
            raise BranchReplacementError("training status completedRounds differs from scenario")
        if len(self.accepted) != self.contract.accepted_rounds:
            raise BranchReplacementError("Root evidence lacks the selected accepted rounds")
        phases = [item["phase"] for item in self.accepted]
        if phases.count("normal") != self.contract.normal_accepted_rounds:
            raise BranchReplacementError("Root evidence lacks the selected normal rounds")
        if phases.count("degraded") < 1:
            raise BranchReplacementError("Root evidence has no natural degraded round")
        if phases.count("restored") < self.contract.restored_accepted_rounds:
            raise BranchReplacementError("Root evidence has no restored accepted round")
        if self.failure_detected_at is None or self.replacement_ready_at is None:
            raise BranchReplacementError("Root replacement lifecycle evidence is incomplete")
        if self.first_contribution_at is None:
            raise BranchReplacementError("replacement never contributed to an accepted round")
        accepted_indices = {item["roundInd"] for item in self.accepted}
        rejected_indices = {
            index for index, record in self.outcomes.items() if record.get("accepted") is False
        }
        if set(self.global_evaluations) != accepted_indices:
            raise BranchReplacementError(
                "accepted outcomes and Root global evaluations are not one-to-one"
            )
        if set(self.global_evaluations) & rejected_indices:
            raise BranchReplacementError("rejected round has a Root global evaluation")
        if self.initial_evaluation is None:
            raise BranchReplacementError("Root initial evaluation is missing")
        candidate = terminal_status.get("candidateDigest")
        if not isinstance(candidate, str) or re.fullmatch(r"[0-9a-f]{64}", candidate) is None:
            raise BranchReplacementError("terminal candidate artifact identity is missing")
        if self.final_model_saved is None:
            raise BranchReplacementError("final model record is missing")
        if terminal_status.get("currentRound") != self.final_model_saved["roundInd"]:
            raise BranchReplacementError("final model round differs from terminal status")
        if candidate != self.final_model_saved["artifactDigest"]:
            raise BranchReplacementError("final model identity differs from terminal status")
        return self.summary()

    def summary(self) -> dict:
        phase_counts = {
            phase: sum(item["phase"] == phase for item in self.accepted)
            for phase in ("normal", "degraded", "restored")
        }
        latencies = {}
        if self.stop_at is not None:
            for name, value in (
                ("failureDetectedSeconds", self.failure_detected_at),
                ("replacementReadySeconds", self.replacement_ready_at),
                ("firstContributionSeconds", self.first_contribution_at),
            ):
                if value is not None:
                    latencies[name] = (value - self.stop_at).total_seconds()
        return {
            "acceptedRoundCount": len(self.accepted),
            "phaseCounts": phase_counts,
            "rounds": list(self.accepted),
            "latencies": latencies,
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
            raise BranchReplacementError("collection checkpoint does not exist")
        try:
            run = json.loads(run_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise BranchReplacementError("collection checkpoint run.json is invalid") from error
        if not isinstance(run, dict):
            raise BranchReplacementError("collection checkpoint run.json must be an object")
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
            raise BranchReplacementError("events.jsonl would not be chronological")
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
            raise BranchReplacementError(
                "events contain duplicate {} {} records".format(source, event_type)
            )
        if matches:
            existing = matches[0]
            if (
                existing.get("payload") != payload
                or existing.get("nfInstanceId") != nf_instance_id
            ):
                raise BranchReplacementError(
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
            raise BranchReplacementError("events.jsonl line {} is invalid".format(number)) from error
        if not isinstance(record, dict) or not {
            "recordedAt", "source", "eventType", "payload",
        } <= set(record):
            raise BranchReplacementError("events.jsonl line {} is incomplete".format(number))
        timestamp = parse_timestamp(record["recordedAt"])
        if last_timestamp is not None and timestamp < last_timestamp:
            raise BranchReplacementError("events.jsonl timestamps are out of order")
        last_timestamp = timestamp
        records.append(record)
    return records


def check_evidence(
    run_directory: Path,
    contract: BranchReplacementContract,
    *,
    require_cleanup: bool = True,
) -> dict:
    run_path = run_directory / "run.json"
    events_path = run_directory / "events.jsonl"
    artifact_path = run_directory / "final-root-model.tar.gz"
    if not run_path.is_file() or not events_path.is_file() or not artifact_path.is_file():
        raise BranchReplacementError("successful run evidence is missing a required file")
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise BranchReplacementError("run.json is invalid") from error
    if require_cleanup:
        if run.get("status") != "successful" or run.get("finalized") is not True:
            raise BranchReplacementError("run.json is not finalized as successful")
    elif (
        run.get("status") not in {"collection-pending", "collection-failed"}
        or run.get("finalized") is not False
    ):
        raise BranchReplacementError("run.json is not a retryable collection checkpoint")
    run_name = validate_run_name(run.get("runName"))
    if run_directory.name != run_name:
        raise BranchReplacementError("run.json runName differs from its directory")
    request_id = run.get("requestId")
    try:
        parsed_request_id = uuid.UUID(request_id)
    except (AttributeError, TypeError, ValueError) as error:
        raise BranchReplacementError("run.json requestId is not a UUIDv4") from error
    if parsed_request_id.version != 4 or str(parsed_request_id) != request_id:
        raise BranchReplacementError("run.json requestId is not a canonical UUIDv4")
    workload = run.get("workload", {})
    if (
        run.get("dataset") != contract.dataset
        or not isinstance(workload, dict)
        or workload.get("samplesPerLeaf") != contract.samples_per_leaf
        or workload.get("localEpochs") != contract.local_epochs
        or workload.get("acceptedRounds") != contract.accepted_rounds
    ):
        raise BranchReplacementError("run.json workload differs from the selected contract")
    plan_id = run.get("planId")
    if not isinstance(plan_id, str) or run.get("mlCorreId") != plan_id:
        raise BranchReplacementError("run.json planId is missing")
    events = load_wrapped_events(events_path)
    required_events = {
        ("controller", "GPU_ADMISSION"),
        ("controller", "RUNTIME_READY"),
        ("controller", "TRAINING_REQUEST_SUBMITTED"),
        ("controller", "BRANCH_PROCESS_STOPPED"),
        ("controller", "FINAL_ARTIFACT_COLLECTED"),
        ("controller", "PROTOCOL_RESOURCE_EVIDENCE"),
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
            raise BranchReplacementError(
                "events must contain exactly one {} {} record".format(*key)
            )
        indexed[key] = matches[0]

    admission = indexed[("controller", "GPU_ADMISSION")]["payload"]
    if (
        admission.get("participantCount") != 7
        or admission.get("minimumFreeMiB") != 8192
        or admission.get("memoryFreeMiB", -1) < admission.get("minimumFreeMiB", 8192)
    ):
        raise BranchReplacementError("GPU_ADMISSION does not prove the seven-participant floor")
    runtime_ready = indexed[("controller", "RUNTIME_READY")]["payload"]
    virtual_machines = runtime_ready.get("virtualMachines", {})
    guest_services = runtime_ready.get("guestServices", {})
    registrations = runtime_ready.get("nrfRegistrations", {})
    if (
        len(virtual_machines) != 4
        or set(virtual_machines.values()) != {"running"}
        or len(guest_services) != 14
        or any(value.get("state") != "active" for value in guest_services.values())
    ):
        raise BranchReplacementError("RUNTIME_READY VM or Guest inventory is incomplete")
    required_registrations = {
        contract.root_nf_instance_id,
        contract.primary_nf_instance_id,
        contract.replacement_nf_instance_id,
        *contract.surviving_nf_instance_ids,
    }
    registered = registrations.get("nfInstanceIds", [])
    if (
        registrations.get("state") != "ready"
        or not isinstance(registered, list)
        or len(registered) != 12
        or len(set(registered)) != 12
        or not required_registrations <= set(registered)
    ):
        raise BranchReplacementError("RUNTIME_READY NRF registration inventory is incomplete")
    containers = runtime_ready.get("containers", {})
    devices = {
        name: value.get("device") for name, value in containers.items()
        if isinstance(value, dict)
    }
    if (
        len(devices) != 11
        or sum(device == "cuda:0" for device in devices.values()) != 7
        or sum(device == "cpu" for device in devices.values()) != 4
        or run.get("resolvedDevices") != devices
    ):
        raise BranchReplacementError("RUNTIME_READY mixed-device inventory is inconsistent")
    for name, value in containers.items():
        expected_gpu = value.get("device") == "cuda:0"
        if value.get("state") != "running" or value.get("health") != "healthy":
            raise BranchReplacementError("RUNTIME_READY contains an unhealthy container")
        if expected_gpu and (
            value.get("runtime") != "nvidia"
            or value.get("cdiSelector") != "nvidia.com/gpu=all"
            or value.get("cuda", {}).get("available") is not True
        ):
            raise BranchReplacementError("RUNTIME_READY lacks actual CUDA evidence for " + name)
        if not expected_gpu and (
            value.get("runtime") == "nvidia" or value.get("cdiSelector") is not None
        ):
            raise BranchReplacementError("RUNTIME_READY exposes a GPU to CPU-owned " + name)
    if run.get("image") != runtime_ready.get("image") or run.get("gpu") != runtime_ready.get("gpu"):
        raise BranchReplacementError("run runtime identity differs from RUNTIME_READY")
    active_identity = run.get("activeIdentity", {})
    if (
        active_identity.get("config") != runtime_ready.get("activeConfig")
        or active_identity.get("guestServices") != guest_services
        or active_identity.get("containers") != containers
    ):
        raise BranchReplacementError("run active identity differs from RUNTIME_READY")
    submitted = indexed[("controller", "TRAINING_REQUEST_SUBMITTED")]["payload"]
    if submitted.get("requestId") != request_id:
        raise BranchReplacementError("training request run identity is mismatched")
    protocol_resources = indexed[("controller", "PROTOCOL_RESOURCE_EVIDENCE")][
        "payload"
    ]
    if run.get("protocolResources") != protocol_resources:
        raise BranchReplacementError("protocol resource evidence differs from run.json")
    if protocol_resources.get("planId") != plan_id:
        raise BranchReplacementError("protocol resource evidence has the wrong plan identity")
    root_edges = protocol_resources.get("rootEdges", [])
    primary_edges = protocol_resources.get("primaryLeafEdges", [])
    replacement_edges = protocol_resources.get("replacementLeafEdges", [])
    if {item.get("nfInstanceId") for item in root_edges} != {
        contract.primary_nf_instance_id,
        contract.replacement_nf_instance_id,
    }:
        raise BranchReplacementError("Root resource evidence lacks primary or replacement")
    expected_leaves = set(contract.fault_group_leaf_nf_instance_ids)
    if (
        {item.get("nfInstanceId") for item in primary_edges} != expected_leaves
        or {item.get("nfInstanceId") for item in replacement_edges} != expected_leaves
    ):
        raise BranchReplacementError("replacement did not preserve the exact Area leaf subtree")
    all_edges = [*root_edges, *primary_edges, *replacement_edges]
    if any(
        item.get("planId") != plan_id
        or not item.get("notifCorreId")
        or not item.get("resourceLocation")
        for item in all_edges
    ):
        raise BranchReplacementError("protocol resource identity is incomplete")
    if (
        {item["notifCorreId"] for item in primary_edges}
        & {item["notifCorreId"] for item in replacement_edges}
        or {item["resourceLocation"] for item in primary_edges}
        & {item["resourceLocation"] for item in replacement_edges}
    ):
        raise BranchReplacementError("replacement reused an old Branch-to-Leaf resource")

    tracker = PhaseTracker(contract, plan_id)
    stop_record = None
    for record in events:
        if record["source"] == "controller" and record["eventType"] == "BRANCH_PROCESS_STOPPED":
            if stop_record is not None:
                raise BranchReplacementError("events contain duplicate confirmed stop records")
            stop_record = record
            tracker.mark_stopped(record["recordedAt"])
        elif record["source"] == "pymtlf-root":
            tracker.ingest(record["payload"])
    if stop_record is None:
        raise BranchReplacementError("events are missing the confirmed primary stop")
    stop_payload = stop_record["payload"]
    guest_stop = stop_payload.get("guest", {})
    container_stop = stop_payload.get("container", {})
    if (
        stop_record.get("nfInstanceId") != contract.primary_nf_instance_id
        or stop_payload.get("nfInstanceId") != contract.primary_nf_instance_id
        or stop_payload.get("guestStopped") is not True
        or stop_payload.get("containerStopped") is not True
        or not isinstance(guest_stop, dict)
        or guest_stop.get("machine") != contract.primary_machine
        or guest_stop.get("unit") != contract.primary_unit
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
        or container_stop.get("service") != contract.primary_service
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
        raise BranchReplacementError("confirmed primary stop target or postcondition is invalid")
    summary = tracker.finalize(run.get("terminalStatus", {}))
    if run.get("phases") != summary:
        raise BranchReplacementError("run.json phase summary differs from events.jsonl")
    if run.get("finalModel") != tracker.final_model_saved:
        raise BranchReplacementError("run.json final model checkpoint differs from Root evidence")
    held_out = run.get("heldOutEvaluation")
    if not isinstance(held_out, dict) or held_out.get("run_id") != request_id:
        raise BranchReplacementError("run.json held-out evaluation is missing or mismatched")
    if indexed[("held-out-evaluator", "HELD_OUT_EVALUATION")]["payload"] != held_out:
        raise BranchReplacementError("held-out event differs from run.json")
    if run.get("finalArtifact") != "final-root-model.tar.gz":
        raise BranchReplacementError("run.json final artifact path is not canonical")
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
        raise BranchReplacementError("final artifact event and terminal identity differ")
    if require_cleanup:
        cleanup = run.get("cleanup", {})
        if (
            cleanup.get("processesStopped") is not True
            or cleanup.get("guestRestartPolicyRestored") is not True
            or cleanup.get("applied") is not True
            or cleanup.get("resetVerified") is not True
        ):
            raise BranchReplacementError("run cleanup/reset evidence is incomplete")
        if indexed[("controller", "CLEANUP_COMPLETE")]["payload"] != cleanup:
            raise BranchReplacementError("cleanup event differs from run.json")
    return run
