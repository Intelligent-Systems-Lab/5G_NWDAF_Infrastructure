#!/usr/bin/env python3
"""Behavioral checks for protocol hierarchical FL experiment semantics."""

import copy
import importlib.util
import json
import os
import signal
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "host"))

from fl_experiment import (  # noqa: E402
    FLExperimentContract,
    FLExperimentError,
    EvidenceWriter,
    IncrementalJsonlReader,
    PhaseTracker,
    check_evidence,
    parse_resource_log,
    validate_run_name,
)


def load_runner():
    path = ROOT / "scripts/host/fl-experiment-run.py"
    spec = importlib.util.spec_from_file_location("fl_experiment_runner", path)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load FL experiment runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PLAN_ID = "550e8400-e29b-41d4-a716-446655440000"
RUN_ID = "123e4567-e89b-42d3-a456-426614174000"
RUN_NAME = "mnist-test-1"
BASE = datetime(2026, 9, 12, tzinfo=timezone.utc)


def timestamp(index):
    return (BASE + timedelta(seconds=index)).isoformat().replace("+00:00", "Z")


def replacement_contract():
    testbed = yaml.safe_load(
        (ROOT / "testbed.protocol-hierarchical.yaml").read_text(encoding="utf-8")
    )
    scenario = yaml.safe_load(
        (
            ROOT
            / "experiments/protocol-hierarchical/branch-replacement/mnist/scenario.yaml"
        ).read_text(encoding="utf-8")
    )
    return FLExperimentContract.build(testbed, scenario)


def normal_contract():
    testbed = yaml.safe_load(
        (ROOT / "testbed.protocol-hierarchical.yaml").read_text(encoding="utf-8")
    )
    scenario = yaml.safe_load(
        (ROOT / "experiments/protocol-hierarchical/mnist/scenario.yaml").read_text(
            encoding="utf-8"
        )
    )
    return FLExperimentContract.build(testbed, scenario)


def evaluation(contract, round_indicator, at, *, initial=False):
    value = {
        "recordedAt": timestamp(at),
        "recordType": "MODEL_EVALUATION",
        "mlCorreId": PLAN_ID,
        "nfInstanceId": contract.root_nf_instance_id,
        "evaluationStage": "ROOT_INITIAL" if initial else "ROOT_GLOBAL",
        "dataset": "mnist",
        "sampleCount": 200,
        "validationLoss": 1.0,
        "validationAccuracy": 0.5,
    }
    if not initial:
        value["roundInd"] = round_indicator
    return value


def outcome(contract, round_indicator, at, successful, failed=(), *, accepted=True):
    selected = list(successful) + list(failed)
    return {
        "recordedAt": timestamp(at),
        "recordType": "ROOT_ROUND_OUTCOME",
        "mlCorreId": PLAN_ID,
        "nfInstanceId": contract.root_nf_instance_id,
        "roundInd": round_indicator,
        "accepted": accepted,
        "selectedNfInstanceIds": selected,
        "successfulNfInstanceIds": list(successful),
        "failedNfInstanceIds": list(failed),
    }


def failure(contract, round_indicator, at):
    return {
        "recordedAt": timestamp(at),
        "recordType": "BRANCH_FAILURE_DETECTED",
        "mlCorreId": PLAN_ID,
        "nfInstanceId": contract.root_nf_instance_id,
        "roundInd": round_indicator,
        "failedBranchNfInstanceId": contract.primary_nf_instance_id,
    }


def ready(contract, at):
    return {
        "recordedAt": timestamp(at),
        "recordType": "BRANCH_REPLACEMENT_READY",
        "mlCorreId": PLAN_ID,
        "nfInstanceId": contract.root_nf_instance_id,
        "failedBranchNfInstanceId": contract.primary_nf_instance_id,
        "replacementBranchNfInstanceId": contract.replacement_nf_instance_id,
    }


def final_model_saved(contract, round_indicator, at, *, digest="a" * 64, size=8):
    return {
        "recordedAt": timestamp(at),
        "recordType": "FINAL_MODEL_SAVED",
        "mlCorreId": PLAN_ID,
        "nfInstanceId": contract.root_nf_instance_id,
        "roundInd": round_indicator,
        "artifactFile": "final-model.tar.gz",
        "artifactDigest": digest,
        "sizeBytes": size,
    }


def successful_records(contract):
    primary_set = (contract.primary_nf_instance_id, *contract.surviving_nf_instance_ids)
    survivor_set = contract.surviving_nf_instance_ids
    restored_set = (contract.replacement_nf_instance_id, *survivor_set)
    records = [evaluation(contract, None, 0, initial=True)]
    for round_indicator in (0, 1):
        records.extend(
            (
                outcome(contract, round_indicator, 1 + round_indicator * 2, primary_set),
                evaluation(contract, round_indicator, 2 + round_indicator * 2),
            )
        )
    records.extend(
        (
            outcome(
                contract, 2, 6, survivor_set,
                failed=(contract.primary_nf_instance_id,),
            ),
            failure(contract, 2, 6),
            evaluation(contract, 2, 7),
            ready(contract, 8),
            outcome(contract, 3, 9, survivor_set),
            evaluation(contract, 3, 10),
        )
    )
    for round_indicator in range(4, 8):
        records.extend(
            (
                outcome(contract, round_indicator, 11 + (round_indicator - 4) * 2, restored_set),
                evaluation(contract, round_indicator, 12 + (round_indicator - 4) * 2),
            )
        )
    records.append(final_model_saved(contract, 7, 19))
    return records


def terminal(contract):
    return {
        "state": "COMPLETE",
        "completedRounds": contract.accepted_rounds,
        "currentRound": contract.accepted_rounds - 1,
        "candidateDigest": "a" * 64,
    }


def test_run_name_and_retry_checkpoint():
    for value in ("123", "mnist-test-1", "CIFAR_10.acceptance"):
        assert validate_run_name(value) == value
    for value in ("", ".", "..", "contains space", "../escape", "a" * 65):
        try:
            validate_run_name(value)
        except FLExperimentError:
            pass
        else:
            raise AssertionError("unsafe run name was accepted: {!r}".format(value))

    with tempfile.TemporaryDirectory(prefix="fl-experiment-checkpoint-") as temporary:
        run_directory = Path(temporary) / RUN_NAME
        writer = EvidenceWriter(
            run_directory,
            {
                "runName": RUN_NAME,
                "requestId": RUN_ID,
                "status": "collection-pending",
            },
        )
        payload = {"requestId": RUN_ID}
        writer.append_once(
            "controller",
            "TRAINING_REQUEST_SUBMITTED",
            payload,
            recorded_at=timestamp(0),
        )
        reopened = EvidenceWriter.open_existing(run_directory)
        assert reopened.run["runName"] == RUN_NAME
        assert reopened.run["requestId"] == RUN_ID
        reopened.append_once(
            "controller",
            "TRAINING_REQUEST_SUBMITTED",
            payload,
            recorded_at=timestamp(1),
        )
        assert len(reopened.events()) == 1
        try:
            reopened.append_once(
                "controller",
                "TRAINING_REQUEST_SUBMITTED",
                {"requestId": "different"},
                recorded_at=timestamp(1),
            )
        except FLExperimentError as error:
            assert "conflicts" in str(error)
        else:
            raise AssertionError("conflicting retry event was accepted")
        try:
            EvidenceWriter(run_directory, {"status": "initializing"})
        except FileExistsError:
            pass
        else:
            raise AssertionError("new training overwrote an existing run directory")


def test_contract_and_fault_barrier():
    contract = replacement_contract()
    assert contract.primary_unit == "nwdaf-branch-a-primary"
    assert contract.primary_service == "pymtlf-branch-a-primary"
    assert contract.accepted_rounds == 8
    tracker = PhaseTracker(contract, PLAN_ID)
    tracker.ingest(evaluation(contract, None, 0, initial=True))
    primary_set = (contract.primary_nf_instance_id, *contract.surviving_nf_instance_ids)
    for round_indicator in (0, 1):
        tracker.ingest(outcome(contract, round_indicator, round_indicator + 1, primary_set))
        tracker.ingest(evaluation(contract, round_indicator, round_indicator + 1))
    assert not tracker.ready_for_fault(
        {"state": "AGGREGATING", "currentRound": 1, "completedRounds": 2}
    )
    assert tracker.ready_for_fault(
        {"state": "ROUND_WAITING", "currentRound": 2, "completedRounds": 2}
    )


def test_normal_rounds_keep_the_selected_cohort_and_evaluations():
    contract = normal_contract()
    assert not contract.fault_enabled
    assert len(contract.normal_nf_instance_ids) == 3
    tracker = PhaseTracker(contract, PLAN_ID)
    tracker.ingest(evaluation(contract, None, 0, initial=True))
    for index in range(contract.accepted_rounds):
        tracker.ingest(outcome(contract, index, 1 + index * 2, contract.normal_nf_instance_ids))
        tracker.ingest(evaluation(contract, index, 2 + index * 2))
    assert not tracker.ready_for_fault(
        {"state": "ROUND_WAITING", "currentRound": 3, "completedRounds": 2}
    )
    tracker.ingest(final_model_saved(contract, contract.accepted_rounds - 1, 5))
    assert tracker.finalize(terminal(contract))["phaseCounts"] == {
        "normal": contract.accepted_rounds,
        "degraded": 0,
        "restored": 0,
    }
    invalid = PhaseTracker(contract, PLAN_ID)
    invalid.ingest(evaluation(contract, None, 0, initial=True))
    try:
        invalid.ingest(failure(contract, 0, 1))
    except FLExperimentError as error:
        assert "normal run" in str(error)
    else:
        raise AssertionError("normal run accepted a Branch failure event")


def test_variable_degraded_phase_and_ready_is_not_contribution():
    contract = replacement_contract()
    tracker = PhaseTracker(contract, PLAN_ID)
    records = successful_records(contract)
    for record in records[:5]:
        tracker.ingest(record)
    tracker.mark_stopped(timestamp(5))
    for record in records[5:]:
        tracker.ingest(record)
    summary = tracker.finalize(terminal(contract))
    assert summary["phaseCounts"] == {"normal": 2, "degraded": 2, "restored": 4}
    assert summary["rounds"][3]["phase"] == "degraded"
    assert summary["rounds"][4]["phase"] == "restored"
    assert summary["latencies"] == {
        "failureDetectedSeconds": 1.0,
        "replacementReadySeconds": 3.0,
        "firstContributionSeconds": 6.0,
    }


def test_rejected_attempt_and_missing_evaluation_fail_closed():
    contract = replacement_contract()
    tracker = PhaseTracker(contract, PLAN_ID)
    records = successful_records(contract)
    for record in records[:5]:
        tracker.ingest(record)
    tracker.mark_stopped(timestamp(5))
    rejected = outcome(
        contract, 20, 5, contract.surviving_nf_instance_ids,
        failed=(contract.primary_nf_instance_id,), accepted=False,
    )
    assert tracker.ingest(rejected) == "round-rejected"
    for record in records[5:]:
        tracker.ingest(record)
    assert tracker.finalize(terminal(contract))["acceptedRoundCount"] == 8

    missing = PhaseTracker(contract, PLAN_ID)
    for record in records[:5]:
        missing.ingest(record)
    missing.mark_stopped(timestamp(5))
    for record in records[5:]:
        if not (record.get("evaluationStage") == "ROOT_GLOBAL" and record.get("roundInd") == 7):
            missing.ingest(record)
    try:
        missing.finalize(terminal(contract))
    except FLExperimentError as error:
        assert "one-to-one" in str(error)
    else:
        raise AssertionError("missing Root evaluation was accepted")


def test_invalid_replacement_and_incomplete_recovery_fail_closed():
    contract = replacement_contract()
    records = successful_records(contract)

    wrong = PhaseTracker(contract, PLAN_ID)
    for record in records[:5]:
        wrong.ingest(record)
    wrong.mark_stopped(timestamp(5))
    for record in records[5:8]:
        wrong.ingest(record)
    invalid_ready = ready(contract, 8)
    invalid_ready["replacementBranchNfInstanceId"] = contract.primary_nf_instance_id
    try:
        wrong.ingest(invalid_ready)
    except FLExperimentError as error:
        assert "wrong replacement priority" in str(error)
    else:
        raise AssertionError("wrong replacement candidate was accepted")

    incomplete = PhaseTracker(contract, PLAN_ID)
    for record in records[:5]:
        incomplete.ingest(record)
    incomplete.mark_stopped(timestamp(5))
    survivor_set = contract.surviving_nf_instance_ids
    incomplete.ingest(
        outcome(
            contract,
            2,
            6,
            survivor_set,
            failed=(contract.primary_nf_instance_id,),
        )
    )
    incomplete.ingest(failure(contract, 2, 6))
    incomplete.ingest(evaluation(contract, 2, 7))
    incomplete.ingest(ready(contract, 8))
    for round_indicator in range(3, 8):
        incomplete.ingest(outcome(contract, round_indicator, 9 + round_indicator, survivor_set))
        incomplete.ingest(evaluation(contract, round_indicator, 10 + round_indicator))
    try:
        incomplete.finalize(terminal(contract))
    except FLExperimentError as error:
        assert "restored" in str(error)
    else:
        raise AssertionError("run without restored contribution was accepted")


def test_final_model_record_must_match_terminal_artifact():
    contract = replacement_contract()
    records = successful_records(contract)

    for field, value, message in (
        ("roundInd", 6, "round"),
        ("artifactDigest", "b" * 64, "identity"),
        ("artifactFile", "other.tar.gz", "filename"),
        ("sizeBytes", 0, "size"),
    ):
        tracker = PhaseTracker(contract, PLAN_ID)
        for record in records[:5]:
            tracker.ingest(record)
        tracker.mark_stopped(timestamp(5))
        for record in records[5:-1]:
            tracker.ingest(record)
        invalid = {**records[-1], field: value}
        try:
            tracker.ingest(invalid)
            tracker.finalize(terminal(contract))
        except FLExperimentError as error:
            assert message in str(error)
        else:
            raise AssertionError("invalid final-model record was accepted")

    missing = PhaseTracker(contract, PLAN_ID)
    for record in records[:5]:
        missing.ingest(record)
    missing.mark_stopped(timestamp(5))
    for record in records[5:-1]:
        missing.ingest(record)
    try:
        missing.finalize(terminal(contract))
    except FLExperimentError as error:
        assert "final model" in str(error)
    else:
        raise AssertionError("missing final-model record was accepted")

    duplicate = PhaseTracker(contract, PLAN_ID)
    for record in records[:5]:
        duplicate.ingest(record)
    duplicate.mark_stopped(timestamp(5))
    for record in records[5:]:
        duplicate.ingest(record)
    try:
        duplicate.ingest(records[-1])
    except FLExperimentError as error:
        assert "duplicated" in str(error)
    else:
        raise AssertionError("duplicate final-model record was accepted")


def test_exact_pair_stop_and_partial_failure():
    runner = load_runner()
    contract = replacement_contract()
    manifest = {
        "runtime": {
            "coordinatorContainer": "pymtlf-root",
            "hostContainers": [contract.primary_service],
        }
    }
    calls = []

    with tempfile.TemporaryDirectory(prefix="fl-experiment-stop-") as temporary:
        config_dir = Path(temporary)
        environment = runner.LiveEnvironment(
            ROOT / "testbed.protocol-hierarchical.yaml", config_dir, manifest
        )

        def provider(body, arguments, timeout=180):
            calls.append(("provider", arguments, body))
            if "GUEST_FROZEN|" in body:
                return "GUEST_FROZEN|1234"
            if "GUEST_KILLED|" in body:
                return "GUEST_KILLED|1234|failed|failed"
            if "GUEST_RESTART_RESTORED" in body:
                return "GUEST_RESTART_RESTORED"
            raise AssertionError("unexpected provider command")

        inspect_running = {
            "Config": {
                "Labels": {
                    "com.docker.compose.project": runner.PROJECT,
                    "com.docker.compose.service": contract.primary_service,
                }
            },
            "HostConfig": {"RestartPolicy": {"Name": "no"}},
            "RestartCount": 0,
            "State": {"Running": True, "Pid": 4321, "ExitCode": 0},
        }
        inspect_stopped = copy.deepcopy(inspect_running)
        inspect_stopped["State"].update({"Running": False, "Pid": 0, "ExitCode": 137})
        stopped = False
        frozen = False

        def command(command, **_kwargs):
            nonlocal stopped, frozen
            calls.append(("command", list(command)))
            if command[:2] == ["docker", "ps"]:
                return "container-id"
            if command[:2] == ["docker", "inspect"]:
                return json.dumps([inspect_stopped if stopped else inspect_running])
            if command[:4] == ["docker", "kill", "--signal", "STOP"]:
                frozen = True
                return "container-id"
            if command[:4] == ["docker", "kill", "--signal", "KILL"]:
                stopped = True
                frozen = False
                return "container-id"
            if command[:4] == ["docker", "kill", "--signal", "CONT"]:
                frozen = False
                return "container-id"
            if command[:3] == ["ps", "-o", "stat="]:
                return "Tsl" if frozen else "Ssl"
            raise AssertionError("unexpected command: {!r}".format(command))

        original_command = runner.command_output
        original_quiet = runner.quiet_command
        try:
            environment._provider_shell = provider
            runner.command_output = command
            result = environment.fail_stop_primary(contract)
            assert result["guest"]["originalPid"] == 1234
            assert result["guest"]["freezeSignal"] == "SIGSTOP"
            assert result["guest"]["signal"] == "SIGKILL"
            assert result["guest"]["restartSuppressed"] is True
            assert result["container"]["originalPid"] == 4321
            assert result["container"]["exitCode"] == 137
            runner.quiet_command = lambda *_args, **_kwargs: ""
            environment.stop_all()
        finally:
            runner.command_output = original_command
            runner.quiet_command = original_quiet

        provider_calls = [item for item in calls if item[0] == "provider"]
        assert all(
            item[1][3:5] == [contract.primary_machine, contract.primary_unit]
            for item in provider_calls
        )
        guest_freeze_call = next(
            index
            for index, item in enumerate(calls)
            if item[0] == "provider" and "GUEST_FROZEN|" in item[2]
        )
        container_freeze_call = next(
            index
            for index, item in enumerate(calls)
            if item[0] == "command"
            and item[1][:4] == ["docker", "kill", "--signal", "STOP"]
        )
        guest_kill_call = next(
            index
            for index, item in enumerate(calls)
            if item[0] == "provider" and "GUEST_KILLED|" in item[2]
        )
        container_kill_call = next(
            index
            for index, item in enumerate(calls)
            if item[0] == "command"
            and item[1][:4] == ["docker", "kill", "--signal", "KILL"]
        )
        assert guest_freeze_call < container_freeze_call < guest_kill_call < container_kill_call
        fail_stop_body = calls[guest_kill_call][2]
        assert "mask --runtime" in fail_stop_body
        assert "--signal=SIGKILL" in fail_stop_body
        assert "--signal=SIGSTOP" not in fail_stop_body
        assert "unmask --runtime" in provider_calls[-1][2]
        assert environment._faulted_guest is None
        kill_call = next(
            item for item in calls
            if item[0] == "command"
            and item[1][:4] == ["docker", "kill", "--signal", "KILL"]
        )
        assert kill_call[1] == ["docker", "kill", "--signal", "KILL", "container-id"]
        assert not any(
            item[0] == "command" and item[1][:2] == ["docker", "compose"]
            for item in calls
        )

        calls.clear()

        def failed_provider(*_args, **_kwargs):
            calls.append(("provider-failed",))
            raise FLExperimentError("guest stop failed")

        stopped = False
        try:
            environment._provider_shell = failed_provider
            runner.command_output = command
            environment.fail_stop_primary(contract)
        except FLExperimentError as error:
            assert "partial primary stop" in str(error)
        else:
            raise AssertionError("partial primary stop was accepted")
        finally:
            runner.command_output = original_command
        assert not any(
            item[0] == "command" and item[1][:2] == ["docker", "kill"]
            for item in calls
        ), "container was mutated after the Guest freeze failed"


def test_timed_command_terminates_its_child():
    runner = load_runner()
    with tempfile.TemporaryDirectory(prefix="fl-experiment-timeout-") as temporary:
        stopped = Path(temporary) / "stopped"
        ready = Path(temporary) / "ready"
        program = (
            "import pathlib,signal,sys,time; "
            "signal.signal(signal.SIGTERM, lambda *_: "
            "(pathlib.Path(sys.argv[1]).write_text('stopped'), sys.exit(0))); "
            "pathlib.Path(sys.argv[2]).write_text('ready'); "
            "time.sleep(30)"
        )
        try:
            runner.quiet_command(
                [sys.executable, "-c", program, str(stopped), str(ready)],
                "bounded-test-command",
                timeout=3,
            )
        except FLExperimentError as error:
            assert "exceeded 3 seconds" in str(error)
        else:
            raise AssertionError("command exceeded its deadline without failing")
        assert ready.read_text(encoding="utf-8") == "ready"
        assert stopped.read_text(encoding="utf-8") == "stopped"


def test_final_artifact_uses_persistent_procedure_record():
    runner = load_runner()
    contract = replacement_contract()
    digest = "b" * 64
    with tempfile.TemporaryDirectory(prefix="fl-experiment-artifact-") as temporary:
        config_dir = Path(temporary)
        (config_dir / "pymtlf-root.yaml").write_text(
            yaml.safe_dump(
                {
                    "federated_learning": {
                        "experiment_recording": {
                            "directory": "/runtime/root/experiment-records",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        environment = runner.LiveEnvironment(
            ROOT / "testbed.protocol-hierarchical.yaml",
            config_dir,
            {
                "runtime": {
                    "coordinatorContainer": "pymtlf-root",
                    "mlVolumes": [
                        {
                            "name": "pymtlf-root-data",
                            "image": runner.IMAGE,
                        }
                    ],
                }
            },
        )
        (config_dir / "compose.yaml").write_text(
            yaml.safe_dump(
                {
                    "services": {
                        "pymtlf-root": {
                            "volumes": [
                                {
                                    "type": "volume",
                                    "source": "pymtlf-root-data",
                                    "target": "/runtime/root",
                                }
                            ]
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        copied = []

        def command(command, **_kwargs):
            copied.append(list(command))
            if command[:3] == ["docker", "volume", "inspect"]:
                return json.dumps(
                    [
                        {
                            "Name": runner.PROJECT + "_pymtlf-root-data",
                            "Labels": {
                                "com.docker.compose.project": runner.PROJECT,
                                "com.docker.compose.volume": "pymtlf-root-data",
                            },
                        }
                    ]
                )
            if command[:2] == ["docker", "create"]:
                return "collector-container-id"
            if command[:2] == ["docker", "cp"]:
                Path(command[-1]).write_bytes(b"final-model")
                return ""
            if command[:3] == ["docker", "rm", "--force"]:
                return "collector-container-id"
            raise AssertionError("unexpected collector command: {}".format(command))

        destination = config_dir / "final-root-model.tar.gz"
        original_command = runner.command_output
        try:
            runner.command_output = command
            environment.copy_final_artifact(
                PLAN_ID,
                len(b"final-model"),
                destination,
            )
        finally:
            runner.command_output = original_command
        assert copied[0][:3] == ["docker", "volume", "inspect"]
        create = copied[1]
        assert create[:2] == ["docker", "create"]
        assert "type=volume,source={}_pymtlf-root-data,target=/runtime/root,readonly".format(
            runner.PROJECT
        ) in create
        assert copied[2][0:2] == ["docker", "cp"]
        assert copied[2][2] == (
            "collector-container-id:/runtime/root/experiment-records/"
            + PLAN_ID
            + "/final-model.tar.gz"
        )
        assert copied[3] == ["docker", "rm", "--force", "collector-container-id"]
        assert destination.read_bytes() == b"final-model"
        assert destination.stat().st_mode & 0o777 == 0o644


def test_held_out_evaluator_has_bounded_temporary_storage():
    runner = load_runner()
    digest = "c" * 64
    with tempfile.TemporaryDirectory(prefix="fl-experiment-evaluator-") as temporary:
        root = Path(temporary)
        config_dir = root / "config"
        config_dir.mkdir()
        artifact = root / "final-root-model.tar.gz"
        artifact.write_bytes(b"model")
        held_out = root / "held-out.npz"
        held_out.write_bytes(b"dataset")
        environment = runner.LiveEnvironment(
            ROOT / "testbed.protocol-hierarchical.yaml",
            config_dir,
            {
                "datasets": {"heldOut": str(held_out)},
                "runtime": {
                    "coordinatorContainer": "pymtlf-root",
                    "nwdafs": [
                        {
                            "role": "leaf",
                            "backends": {"mtlf": "pymtlf-leaf-a1"},
                        }
                    ],
                },
            },
        )
        commands = []

        def command(value, **_kwargs):
            commands.append(list(value))
            return json.dumps({"run_id": RUN_ID, "model_artifact_key": digest})

        original_command = runner.command_output
        try:
            runner.command_output = command
            result = environment.evaluate(RUN_ID, digest, artifact)
        finally:
            runner.command_output = original_command
        assert result["model_artifact_key"] == digest
        assert "--read-only" in commands[0]
        tmpfs_index = commands[0].index("--tmpfs")
        assert commands[0][tmpfs_index + 1] == "/tmp:rw,noexec,nosuid,size=64m"


def test_collection_retry_reuses_checkpoint_without_training_or_early_reset():
    runner = load_runner()
    contract = normal_contract()
    testbed_path = ROOT / "testbed.protocol-hierarchical.yaml"
    config_dir = ROOT / "config/local/protocol-hierarchical"
    manifest = {
        "scenario": yaml.safe_load(
            (ROOT / "experiments/protocol-hierarchical/mnist/scenario.yaml").read_text(
                encoding="utf-8"
            )
        )
    }
    image = {"id": "sha256:current-image", "revision": "current-revision"}
    source = {
        "rootService": "pymtlf-root",
        "logicalVolume": "pymtlf-root-data",
        "physicalVolume": runner.PROJECT + "_pymtlf-root-data",
        "image": runner.IMAGE,
        "mountTarget": "/runtime/root",
        "artifactPath": "/runtime/root/experiment-records/{}/final-model.tar.gz".format(
            PLAN_ID
        ),
    }
    calls = []

    class FakeEnvironment:
        fail_evaluation = True

        def final_model_source(self, plan_id):
            calls.append(("source", plan_id))
            return source

        def selected_image_snapshot(self):
            calls.append(("image",))
            return image

        def copy_final_artifact(self, plan_id, expected_size, destination):
            calls.append(("copy", plan_id, expected_size))
            destination.write_bytes(b"artifact")
            destination.chmod(0o600)

        def collect_protocol_evidence(self, _directory, _contract, plan_id, _since):
            calls.append(("protocol", plan_id))
            return {"planId": plan_id}

        def stop_all(self):
            calls.append(("stop",))
            return {"processesStopped": True, "guestRestartPolicyRestored": True}

        def evaluate(self, request_id, artifact_key, artifact):
            calls.append(("evaluate", request_id, artifact_key))
            assert artifact.stat().st_mode & 0o004
            if self.fail_evaluation:
                raise FLExperimentError("held-out evaluator failed")
            return {
                "run_id": request_id,
                "model_artifact_key": artifact_key,
                "accuracy": 0.5,
            }

        def reset(self):
            calls.append(("reset",))
            return {"applied": True, "resetVerified": True}

    environment = FakeEnvironment()
    with tempfile.TemporaryDirectory(prefix="fl-experiment-collection-") as temporary:
        writer = EvidenceWriter(
            Path(temporary) / RUN_NAME,
            {
                "runName": RUN_NAME,
                "requestId": RUN_ID,
                "planId": PLAN_ID,
                "mlCorreId": PLAN_ID,
                "dataset": contract.dataset,
                "scenario": manifest["scenario"],
                "selection": {
                    "testbed": str(testbed_path.relative_to(ROOT)),
                    "configDirectory": str(config_dir.relative_to(ROOT)),
                },
                "startedAt": timestamp(0),
                "status": "collection-pending",
                "finalized": False,
                "terminalStatus": terminal(contract),
                "finalModel": {
                    "roundInd": contract.accepted_rounds - 1,
                    "artifactFile": "final-model.tar.gz",
                    "artifactDigest": terminal(contract)["candidateDigest"],
                    "sizeBytes": len(b"artifact"),
                },
                "finalModelSource": source,
                "image": image,
                "phases": {
                    "phaseCounts": {
                        "normal": contract.accepted_rounds,
                        "degraded": 0,
                        "restored": 0,
                    }
                },
                "failures": [],
            },
        )
        original_check = runner.check_evidence
        try:
            runner.check_evidence = lambda *_args, **_kwargs: writer.run
            try:
                runner.run_collection_only(
                    writer,
                    RUN_NAME,
                    testbed_path,
                    config_dir,
                    manifest,
                    contract,
                    environment,
                )
            except FLExperimentError as error:
                assert "evaluator failed" in str(error)
                runner.record_run_failure(writer, error)
            else:
                raise AssertionError("collection failure was accepted")
            assert ("reset",) not in calls
            assert writer.run["status"] == "collection-failed"
            assert writer.run["finalized"] is False
            environment.fail_evaluation = False
            assert runner.run_collection_only(
                writer,
                RUN_NAME,
                testbed_path,
                config_dir,
                manifest,
                contract,
                environment,
            ) == 0
        finally:
            runner.check_evidence = original_check
        assert writer.run["status"] == "successful"
        assert calls.count(("copy", PLAN_ID, len(b"artifact"))) == 1
        assert calls.count(("protocol", PLAN_ID)) == 1
        assert calls.count(("stop",)) == 1
        assert calls.count(("reset",)) == 1
        assert len(
            [
                record
                for record in writer.events()
                if record["eventType"] == "FINAL_ARTIFACT_COLLECTED"
            ]
        ) == 1


def test_failure_cleanup_does_not_stop_an_already_stopped_runtime():
    runner = load_runner()
    calls = []

    class FakeEnvironment:
        def stop_all(self):
            calls.append("stop")
            return {"processesStopped": True, "guestRestartPolicyRestored": True}

    with tempfile.TemporaryDirectory(prefix="fl-experiment-cleanup-") as temporary:
        writer = EvidenceWriter(
            Path(temporary) / RUN_NAME,
            {
                "runName": RUN_NAME,
                "status": "collection-failed",
                "processCleanup": {
                    "processesStopped": True,
                    "guestRestartPolicyRestored": True,
                },
            },
        )
        runner.ensure_runtime_stopped_after_failure(
            writer, FakeEnvironment(), runtime_started=True
        )
        assert calls == []

        writer.update(processCleanup=None)
        runner.ensure_runtime_stopped_after_failure(
            writer, FakeEnvironment(), runtime_started=True
        )
        assert calls == ["stop"]
        assert writer.run["processCleanup"]["processesStopped"] is True


def test_runtime_image_admission_uses_the_post_build_identity():
    runner = load_runner()
    expected_revision = "component-revision"
    prebuild = {"id": "sha256:before", "revision": expected_revision}
    postbuild = {"id": "sha256:after", "revision": expected_revision}

    runner.validate_source_image_revision(prebuild, expected_revision)
    runner.validate_runtime_image(postbuild, postbuild, expected_revision)

    try:
        runner.validate_runtime_image(prebuild, postbuild, expected_revision)
    except FLExperimentError as error:
        assert "running" in str(error)
    else:
        raise AssertionError("a running container on a stale image was accepted")

    try:
        runner.validate_runtime_image(
            {"id": "sha256:after", "revision": "old-revision"},
            {"id": "sha256:after", "revision": "old-revision"},
            expected_revision,
        )
    except FLExperimentError as error:
        assert "source revision" in str(error)
    else:
        raise AssertionError("a rebuilt image from the wrong source was accepted")


def test_interrupted_command_terminates_its_child():
    runner = load_runner()
    with tempfile.TemporaryDirectory(prefix="fl-experiment-interrupt-") as temporary:
        ready = Path(temporary) / "ready"
        stopped = Path(temporary) / "stopped"
        pid_file = Path(temporary) / "pid"
        program = (
            "import os,pathlib,signal,sys,time; "
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
            "signal.signal(signal.SIGTERM, lambda *_: "
            "(pathlib.Path(sys.argv[2]).write_text('stopped'), sys.exit(0))); "
            "pathlib.Path(sys.argv[3]).write_text('ready'); "
            "time.sleep(30)"
        )
        original_sleep = runner.time.sleep

        def interrupt_when_ready(_seconds):
            for _ in range(50):
                if ready.exists():
                    raise KeyboardInterrupt()
                original_sleep(0.02)
            raise AssertionError("child did not become ready")

        try:
            runner.time.sleep = interrupt_when_ready
            try:
                runner.quiet_command(
                    [sys.executable, "-c", program, str(pid_file), str(stopped), str(ready)],
                    "interrupted-test-command",
                    timeout=30,
                )
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("interrupted command did not propagate the interruption")
        finally:
            runner.time.sleep = original_sleep
        for _ in range(50):
            if stopped.exists():
                break
            original_sleep(0.02)
        if not stopped.exists() and pid_file.exists():
            try:
                os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGKILL)
            except ProcessLookupError:
                pass
        assert stopped.read_text(encoding="utf-8") == "stopped"


def test_component_resource_log_parsing():
    contract = replacement_contract()
    value = (
        "prefix FL participant resource created process_id={} nf={} "
        "notif_corre_id=notification-a location=http://leaf/resources/a\n"
        "FL participant resource created process_id=other nf={} "
        "notif_corre_id=ignored location=http://leaf/resources/ignored\n"
    ).format(PLAN_ID, contract.fault_group_leaf_nf_instance_ids[0], contract.primary_nf_instance_id)
    assert parse_resource_log(value, PLAN_ID) == [
        {
            "planId": PLAN_ID,
            "nfInstanceId": contract.fault_group_leaf_nf_instance_ids[0],
            "notifCorreId": "notification-a",
            "resourceLocation": "http://leaf/resources/a",
        }
    ]


def test_protocol_resource_collection_keeps_the_complete_run_window():
    runner = load_runner()
    contract = replacement_contract()
    root_service = "pymtlf-root"
    replacement_service = "pymtlf-branch-a-replacement"
    manifest = {
        "runtime": {
            "coordinatorContainer": root_service,
            "nwdafs": [
                {
                    "nfInstanceId": contract.replacement_nf_instance_id,
                    "backends": {"mtlf": replacement_service},
                }
            ],
        }
    }
    environment = runner.LiveEnvironment(
        ROOT / "testbed.protocol-hierarchical.yaml",
        ROOT / "config/local/protocol-hierarchical",
        manifest,
    )
    environment._container_id = lambda service, running=False: service + "-id"

    def resource_line(nf_instance_id, notification, location):
        return (
            "FL participant resource created process_id={} nf={} "
            "notif_corre_id={} location={}"
        ).format(PLAN_ID, nf_instance_id, notification, location)

    root_lines = [
        resource_line(
            contract.primary_nf_instance_id,
            "root-primary",
            "http://primary/resources/old",
        ),
        *["unrelated health line {}".format(index) for index in range(600)],
        resource_line(
            contract.replacement_nf_instance_id,
            "root-replacement",
            "http://replacement/resources/new",
        ),
    ]
    leaf_lines = {
        contract.primary_service: [
            resource_line(nf_id, "primary-" + str(index), "http://leaf/old/" + str(index))
            for index, nf_id in enumerate(contract.fault_group_leaf_nf_instance_ids)
        ],
        replacement_service: [
            resource_line(nf_id, "replacement-" + str(index), "http://leaf/new/" + str(index))
            for index, nf_id in enumerate(contract.fault_group_leaf_nf_instance_ids)
        ],
    }

    def command(command, **_kwargs):
        service = command[-1][:-3]
        lines = root_lines if service == root_service else leaf_lines[service]
        if "--tail" in command:
            lines = lines[-int(command[command.index("--tail") + 1]):]
        return "\n".join(lines)

    original_command = runner.command_output
    try:
        runner.command_output = command
        with tempfile.TemporaryDirectory(prefix="fl-experiment-resources-") as temporary:
            evidence = environment.collect_protocol_evidence(
                Path(temporary),
                contract,
                PLAN_ID,
                timestamp(0),
            )
    finally:
        runner.command_output = original_command
    assert {item["nfInstanceId"] for item in evidence["rootEdges"]} == {
        contract.primary_nf_instance_id,
        contract.replacement_nf_instance_id,
    }


def test_incremental_reader_and_two_file_consistency():
    contract = replacement_contract()
    payload = b'{"a":1}\n{"b":2}\n'
    chunks = [payload[:5], payload[5:13], payload[13:]]

    def read_chunk(offset):
        chunk = chunks.pop(0) if chunks else b""
        return offset + len(chunk), chunk

    reader = IncrementalJsonlReader(read_chunk)
    values = reader.poll() + reader.poll() + reader.poll()
    reader.finish()
    assert values == [{"a": 1}, {"b": 2}]

    partial = IncrementalJsonlReader(lambda offset: (offset + 5, b'{"a":'))
    partial.poll()
    try:
        partial.finish()
    except FLExperimentError as error:
        assert "partial" in str(error)
    else:
        raise AssertionError("partial JSONL record was accepted")

    with tempfile.TemporaryDirectory(prefix="fl-experiment-evidence-") as temporary:
        run_directory = Path(temporary) / RUN_NAME
        writer = EvidenceWriter(
            run_directory,
            {"runName": RUN_NAME, "requestId": RUN_ID, "status": "running"},
        )
        devices = {
            **{"pymtlf-root": "cuda:0"},
            **{"pymtlf-leaf-{}".format(index): "cuda:0" for index in range(6)},
            **{"pymtlf-branch-{}".format(index): "cpu" for index in range(4)},
        }
        snapshot = {
            "virtualMachines": {
                "core": "running",
                "path-a": "running",
                "path-b": "running",
                "path-c": "running",
            },
            "guestServices": {
                "guest-{}".format(index): {"state": "active"}
                for index in range(14)
            },
            "activeConfig": {
                machine: {"activeTarget": "config", "activeIdentity": "identity"}
                for machine in ("core", "path-a", "path-b", "path-c")
            },
            "nrfRegistrations": {
                "nfInstanceIds": [
                    contract.root_nf_instance_id,
                    contract.primary_nf_instance_id,
                    contract.replacement_nf_instance_id,
                    *contract.surviving_nf_instance_ids,
                    *["registered-{}".format(index) for index in range(7)],
                ],
                "state": "ready",
            },
            "containers": {
                name: {
                    "device": device,
                    "state": "running",
                    "health": "healthy",
                    "runtime": "nvidia" if device == "cuda:0" else "runc",
                    "cdiSelector": "nvidia.com/gpu=all" if device == "cuda:0" else None,
                    **({"cuda": {"available": True}} if device == "cuda:0" else {}),
                }
                for name, device in devices.items()
            },
            "image": {"id": "sha256:image", "revision": "component-revision"},
            "gpu": {"participantCount": 7, "memoryFreeMiB": 8200},
        }
        writer.append(
            "controller",
            "GPU_ADMISSION",
            {"minimumFreeMiB": 8192, "memoryFreeMiB": 9000, "participantCount": 7},
            recorded_at=timestamp(-3),
        )
        writer.append("controller", "RUNTIME_READY", snapshot, recorded_at=timestamp(-2))
        writer.append(
            "controller",
            "TRAINING_REQUEST_SUBMITTED",
            {"requestId": RUN_ID},
            recorded_at=timestamp(-1),
        )
        records = successful_records(contract)
        for record in records[:5]:
            writer.append_root(record)
        writer.append(
            "controller",
            "BRANCH_PROCESS_STOPPED",
            {
                "nfInstanceId": contract.primary_nf_instance_id,
                "guestStopped": True,
                "containerStopped": True,
                "guest": {
                    "machine": contract.primary_machine,
                    "unit": contract.primary_unit,
                    "originalPid": 1234,
                    "freezeSignal": "SIGSTOP",
                    "signal": "SIGKILL",
                    "activeState": "failed",
                    "subState": "failed",
                    "restartSuppressed": True,
                },
                "container": {
                    "service": contract.primary_service,
                    "containerId": "container-id",
                    "originalPid": 4321,
                    "freezeSignal": "SIGSTOP",
                    "signal": "SIGKILL",
                    "exitCode": 137,
                    "restartPolicy": "no",
                    "restartCount": 0,
                },
            },
            recorded_at=timestamp(5),
            nf_instance_id=contract.primary_nf_instance_id,
        )
        for record in records[5:]:
            writer.append_root(record)
        (run_directory / "final-root-model.tar.gz").write_bytes(b"artifact")
        writer.append(
            "controller",
            "FINAL_ARTIFACT_COLLECTED",
            {
                "artifactKey": terminal(contract)["candidateDigest"],
                "path": "final-root-model.tar.gz",
                "sizeBytes": len(b"artifact"),
            },
            recorded_at=timestamp(20),
        )
        protocol_resources = {
            "planId": PLAN_ID,
            "rootEdges": [
                {
                    "planId": PLAN_ID,
                    "nfInstanceId": contract.primary_nf_instance_id,
                    "notifCorreId": "root-primary",
                    "resourceLocation": "http://primary/resources/old",
                },
                {
                    "planId": PLAN_ID,
                    "nfInstanceId": contract.replacement_nf_instance_id,
                    "notifCorreId": "root-replacement",
                    "resourceLocation": "http://replacement/resources/new",
                },
            ],
            "primaryLeafEdges": [
                {
                    "planId": PLAN_ID,
                    "nfInstanceId": leaf,
                    "notifCorreId": "primary-leaf-{}".format(index),
                    "resourceLocation": "http://leaf/resources/primary-{}".format(index),
                }
                for index, leaf in enumerate(contract.fault_group_leaf_nf_instance_ids)
            ],
            "replacementLeafEdges": [
                {
                    "planId": PLAN_ID,
                    "nfInstanceId": leaf,
                    "notifCorreId": "replacement-leaf-{}".format(index),
                    "resourceLocation": "http://leaf/resources/replacement-{}".format(index),
                }
                for index, leaf in enumerate(contract.fault_group_leaf_nf_instance_ids)
            ],
        }
        writer.append(
            "controller",
            "PROTOCOL_RESOURCE_EVIDENCE",
            protocol_resources,
            recorded_at=timestamp(20),
        )
        held_out = {"run_id": RUN_ID, "accuracy": 0.5}
        writer.append(
            "held-out-evaluator", "HELD_OUT_EVALUATION", held_out,
            recorded_at=timestamp(21),
        )
        cleanup = {
            "processesStopped": True,
            "guestRestartPolicyRestored": True,
            "applied": True,
            "resetVerified": True,
        }
        writer.append(
            "controller", "CLEANUP_COMPLETE", cleanup, recorded_at=timestamp(22)
        )
        tracker = PhaseTracker(contract, PLAN_ID)
        for record in records[:5]:
            tracker.ingest(record)
        tracker.mark_stopped(timestamp(5))
        for record in records[5:]:
            tracker.ingest(record)
        phases = tracker.finalize(terminal(contract))
        writer.update(
            status="successful",
            finalized=True,
            runName=RUN_NAME,
            requestId=RUN_ID,
            dataset="mnist",
            workload={
                "seed": 42,
                "samplesPerLeaf": 8000,
                "validationSamples": 200,
                "heldOutSamples": 200,
                "acceptedRounds": 8,
                "batchSize": 16,
                "learningRate": 0.001,
                "localEpochs": 32,
            },
            planId=PLAN_ID,
            mlCorreId=PLAN_ID,
            phases=phases,
            terminalStatus=terminal(contract),
            gpuAdmission={"minimumFreeMiB": 8192, "memoryFreeMiB": 9000},
            gpu=snapshot["gpu"],
            image=snapshot["image"],
            activeIdentity={
                "config": snapshot["activeConfig"],
                "guestServices": snapshot["guestServices"],
                "containers": snapshot["containers"],
            },
            resolvedDevices=devices,
            protocolResources=protocol_resources,
            heldOutEvaluation=held_out,
            finalArtifact="final-root-model.tar.gz",
            finalArtifactIdentity=terminal(contract)["candidateDigest"],
            finalArtifactSizeBytes=len(b"artifact"),
            finalModel={
                "roundInd": 7,
                "artifactFile": "final-model.tar.gz",
                "artifactDigest": terminal(contract)["candidateDigest"],
                "sizeBytes": len(b"artifact"),
            },
            cleanup=cleanup,
        )
        assert check_evidence(run_directory, contract)["status"] == "successful"
        original_run = writer.run_path.read_text(encoding="utf-8")
        missing_workload = json.loads(original_run)
        missing_workload["workload"].pop("localEpochs")
        writer.run_path.write_text(json.dumps(missing_workload), encoding="utf-8")
        try:
            check_evidence(run_directory, contract)
        except FLExperimentError as error:
            assert "workload" in str(error)
        else:
            raise AssertionError("evidence without the effective local epoch was accepted")
        writer.run_path.write_text(original_run, encoding="utf-8")
        incomplete_cleanup = json.loads(original_run)
        incomplete_cleanup["cleanup"]["guestRestartPolicyRestored"] = False
        writer.run_path.write_text(json.dumps(incomplete_cleanup), encoding="utf-8")
        try:
            check_evidence(run_directory, contract)
        except FLExperimentError as error:
            assert "cleanup" in str(error)
        else:
            raise AssertionError("evidence without Guest restart restoration was accepted")
        writer.run_path.write_text(original_run, encoding="utf-8")
        original_events = writer.events_path.read_text(encoding="utf-8")
        without_gpu = "\n".join(
            line for line in original_events.splitlines()
            if json.loads(line)["eventType"] != "GPU_ADMISSION"
        ) + "\n"
        writer.events_path.write_text(without_gpu, encoding="utf-8")
        try:
            check_evidence(run_directory, contract)
        except FLExperimentError as error:
            assert "GPU_ADMISSION" in str(error)
        else:
            raise AssertionError("evidence without GPU admission was accepted")
        writer.events_path.write_text(original_events, encoding="utf-8")
        incomplete_stop = "\n".join(
            json.dumps(
                {
                    **json.loads(line),
                    "payload": {
                        **json.loads(line)["payload"],
                        "guest": {
                            **json.loads(line)["payload"].get("guest", {}),
                            "restartSuppressed": False,
                        },
                    },
                },
                separators=(",", ":"),
            )
            if json.loads(line)["eventType"] == "BRANCH_PROCESS_STOPPED"
            else line
            for line in original_events.splitlines()
        ) + "\n"
        writer.events_path.write_text(incomplete_stop, encoding="utf-8")
        try:
            check_evidence(run_directory, contract)
        except FLExperimentError as error:
            assert "stop" in str(error)
        else:
            raise AssertionError("evidence without restart suppression was accepted")
        writer.events_path.write_text(original_events, encoding="utf-8")
        tampered = json.loads(writer.run_path.read_text(encoding="utf-8"))
        tampered["phases"] = copy.deepcopy(phases)
        tampered["phases"]["phaseCounts"]["degraded"] = 99
        writer.run_path.write_text(json.dumps(tampered), encoding="utf-8")
        try:
            check_evidence(run_directory, contract)
        except FLExperimentError as error:
            assert "differs" in str(error)
        else:
            raise AssertionError("cross-file phase mismatch was accepted")

        normal = normal_contract()
        normal_records = [evaluation(normal, None, 0, initial=True)]
        for index in range(normal.accepted_rounds):
            normal_records.extend(
                (
                    outcome(normal, index, 1 + index * 2, normal.normal_nf_instance_ids),
                    evaluation(normal, index, 2 + index * 2),
                )
            )
        normal_records.append(final_model_saved(normal, normal.accepted_rounds - 1, 5))
        normal_tracker = PhaseTracker(normal, PLAN_ID)
        for record in normal_records:
            normal_tracker.ingest(record)
        normal_resources = {
            "planId": PLAN_ID,
            "rootEdges": [
                {
                    "planId": PLAN_ID,
                    "nfInstanceId": branch_id,
                    "notifCorreId": "root-{}".format(index),
                    "resourceLocation": "http://root/{}".format(index),
                }
                for index, branch_id in enumerate(normal.normal_nf_instance_ids)
            ],
            "branchLeafEdges": {
                branch_id: [
                    {
                        "planId": PLAN_ID,
                        "nfInstanceId": leaf_id,
                        "notifCorreId": "leaf-{}-{}".format(branch_index, leaf_index),
                        "resourceLocation": "http://leaf/{}/{}".format(
                            branch_index, leaf_index
                        ),
                    }
                    for leaf_index, leaf_id in enumerate(leaves)
                ]
                for branch_index, (branch_id, _service, leaves) in enumerate(
                    normal.active_branch_leaf_edges
                )
            },
        }
        normal_run = copy.deepcopy(writer.run)
        normal_run.update(
            runName="normal-run",
            workload={
                **normal_run["workload"],
                "samplesPerLeaf": normal.samples_per_leaf,
                "localEpochs": normal.local_epochs,
                "acceptedRounds": normal.accepted_rounds,
            },
            phases=normal_tracker.finalize(terminal(normal)),
            terminalStatus=terminal(normal),
            finalModel={
                **normal_run["finalModel"],
                "roundInd": normal.accepted_rounds - 1,
            },
            protocolResources=normal_resources,
        )
        normal_writer = EvidenceWriter(Path(temporary) / "normal-run", normal_run)
        (normal_writer.run_directory / "final-root-model.tar.gz").write_bytes(b"artifact")
        previous_events = [json.loads(line) for line in original_events.splitlines()]
        for item in previous_events[:3]:
            normal_writer.append(
                item["source"], item["eventType"], item["payload"],
                recorded_at=item["recordedAt"],
                nf_instance_id=item.get("nfInstanceId"),
            )
        for record in normal_records:
            normal_writer.append_root(record)
        for event_type in (
            "FINAL_ARTIFACT_COLLECTED",
            "PROTOCOL_RESOURCE_EVIDENCE",
            "HELD_OUT_EVALUATION",
            "CLEANUP_COMPLETE",
        ):
            item = next(value for value in previous_events if value["eventType"] == event_type)
            normal_writer.append(
                item["source"], event_type,
                normal_resources if event_type == "PROTOCOL_RESOURCE_EVIDENCE" else item["payload"],
                recorded_at=item["recordedAt"],
                nf_instance_id=item.get("nfInstanceId"),
            )
        assert check_evidence(normal_writer.run_directory, normal)["status"] == "successful"


def main():
    test_run_name_and_retry_checkpoint()
    test_contract_and_fault_barrier()
    test_normal_rounds_keep_the_selected_cohort_and_evaluations()
    test_variable_degraded_phase_and_ready_is_not_contribution()
    test_rejected_attempt_and_missing_evaluation_fail_closed()
    test_invalid_replacement_and_incomplete_recovery_fail_closed()
    test_final_model_record_must_match_terminal_artifact()
    test_exact_pair_stop_and_partial_failure()
    test_final_artifact_uses_persistent_procedure_record()
    test_held_out_evaluator_has_bounded_temporary_storage()
    test_collection_retry_reuses_checkpoint_without_training_or_early_reset()
    test_failure_cleanup_does_not_stop_an_already_stopped_runtime()
    test_runtime_image_admission_uses_the_post_build_identity()
    test_timed_command_terminates_its_child()
    test_interrupted_command_terminates_its_child()
    test_component_resource_log_parsing()
    test_protocol_resource_collection_keeps_the_complete_run_window()
    test_incremental_reader_and_two_file_consistency()
    print("PASS protocol hierarchical FL experiment behavior")


if __name__ == "__main__":
    main()
