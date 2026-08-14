#!/usr/bin/env python3
"""Fixture regression for current-container FL milestone summaries."""

import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ml_status", ROOT / "scripts" / "host" / "ml-status.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def main():
    logs = {
        "pymtlf-a": "\n".join(
            (
                "2026-08-13T00:00:05Z FL client local result ready subscription_id=a round=0 samples=6 artifact=x",
                "2026-08-13T00:00:06Z FL client local result ready subscription_id=a round=1 samples=6 artifact=y",
                "2026-08-13T00:00:07Z FL client final validation ready subscription_id=a round=2 samples=1",
            )
        ),
        "pymtlf-b": "\n".join(
            (
                "2026-08-13T00:00:05Z FL client local result ready subscription_id=b round=0 samples=6 artifact=x",
                "2026-08-13T00:00:06Z FL client local result ready subscription_id=b round=1 samples=6 artifact=y",
                "2026-08-13T00:00:07Z FL client final validation ready subscription_id=b round=2 samples=1",
            )
        ),
        "pymtlf-c": "\n".join(
            (
                "2026-08-13T00:00:00Z ML Model Monitor subscription active subscription_id=mon-a registration_id=ra correlation_id=ca",
                "2026-08-13T00:00:01Z ML Model Monitor subscription active subscription_id=mon-b registration_id=rb correlation_id=cb",
                "2026-08-13T00:00:02Z ML Model accuracy report processed correlation_id=ca model_ids=[1] evaluated=[True] triggered=[True]",
                "2026-08-13T00:00:03Z Federated process started process_id=process-1 scopes=('a', 'b')",
                "2026-08-13T00:00:04Z Federated preparation complete process_id=process-1 participants=['a', 'b']",
                "2026-08-13T00:00:08Z Federated round aggregated process_id=process-1 round=0 artifact=x",
                "2026-08-13T00:00:09Z Federated round aggregated process_id=process-1 round=1 artifact=y",
                "2026-08-13T00:00:10Z Federated final validation evaluated process_id=process-1 base_wape=1.8 candidate_wape=0.4 gate_would_accept=True enforced=False",
                "2026-08-13T00:00:11Z Federated model published publication_id=pub model_id=2 state=CUTOVER_PENDING required_scopes=2",
                "2026-08-13T00:00:12Z Federated model scope adopted model_id=2 scope=a complete=False",
                "2026-08-13T00:00:13Z Federated model scope adopted model_id=2 scope=b complete=True",
                "2026-08-13T00:00:14Z ML Model accuracy report processed correlation_id=ca model_ids=[2] evaluated=[False] triggered=[False]",
                "2026-08-13T00:00:15Z Federated model cutover complete model_id=2 family=f",
                "2026-08-13T00:00:16Z ML Model accuracy report processed correlation_id=ca model_ids=[2] evaluated=[True] triggered=[False]",
            )
        ),
    }
    summary = MODULE.parse_fl_milestones(logs)
    assert summary["monitors"]["detail"] == "created=2 active=2", summary
    assert summary["degradation"]["timestamp"] == "2026-08-13T00:00:02Z", summary
    assert summary["process"]["detail"] == "id=process-1 scopes=observed", summary
    assert summary["rounds"]["detail"] == "process=process-1 completed=0,1", summary
    assert "candidate_wape=0.4" in summary["validation"]["detail"], summary
    assert summary["publication"]["detail"] == "model=2 state=CUTOVER_PENDING required_scopes=2", summary
    assert summary["adoption"]["detail"] == "model=2 scopes=2 complete=True", summary
    assert summary["cutover"]["timestamp"] == "2026-08-13T00:00:15Z", summary
    assert summary["post_cutover_accuracy"]["timestamp"] == "2026-08-13T00:00:16Z", summary
    assert summary["client_a"]["detail"] == "rounds=0,1 final_validation=true", summary
    assert summary["client_b"]["detail"] == "rounds=0,1 final_validation=true", summary
    assert summary["failure"] == {"timestamp": "not-seen", "detail": "not-seen"}, summary
    assert MODULE.fl_result(summary, "running") == (
        "outcome=complete model=2 evidence=post-cutover-accuracy"
    ), summary
    assert MODULE.fl_result(summary, "exited") == (
        "outcome=complete model=2 evidence=post-cutover-accuracy"
    ), summary

    empty = MODULE.parse_fl_milestones({"pymtlf-c": "unrelated old output"})
    assert all(value["timestamp"] == "not-seen" for value in empty.values()), empty
    assert MODULE.fl_result(empty, "absent") == (
        "outcome=not-started reason=coordinator-absent"
    ), empty
    assert MODULE.fl_result(empty, "running") == (
        "outcome=in-progress phase=starting"
    ), empty
    assert MODULE.fl_result(empty, "exited") == (
        "outcome=incomplete phase=starting coordinator_state=exited"
    ), empty

    failed = MODULE.parse_fl_milestones({
        "pymtlf-c": "2026-08-13T00:00:10Z Federated process failed process_id=failed-1"
    })
    assert MODULE.fl_result(failed, "running") == (
        "outcome=failed process=failed-1 evidence=process=failed-1"
    ), failed

    recovered = MODULE.parse_fl_milestones({
        "pymtlf-c": "\n".join((
            "2026-08-13T00:00:10.123456789Z Federated process failed process_id=failed-1",
            "2026-08-13T00:00:11Z Federated model cutover complete model_id=3 family=f",
            "2026-08-13T00:00:12Z ML Model accuracy report processed correlation_id=ca model_ids=[3] evaluated=[True] triggered=[False]",
        ))
    })
    assert MODULE.fl_result(recovered, "running") == (
        "outcome=complete model=3 evidence=post-cutover-accuracy"
    ), recovered

    later_failure = MODULE.parse_fl_milestones({
        "pymtlf-c": "\n".join((
            "2026-08-13T00:00:11Z Federated model cutover complete model_id=3 family=f",
            "2026-08-13T00:00:12Z ML Model accuracy report processed correlation_id=ca model_ids=[3] evaluated=[True] triggered=[False]",
            "2026-08-13T00:00:13Z Federated process failed process_id=failed-2",
        ))
    })
    assert MODULE.fl_result(later_failure, "running") == (
        "outcome=failed process=failed-2 evidence=process=failed-2"
    ), later_failure

    cross_service_failure = MODULE.parse_fl_milestones({
        "pymtlf-a": "2026-08-13T00:00:13Z FL client round failed subscription_id=a round=1 error=newer",
        "pymtlf-c": "2026-08-13T00:00:10Z Federated process failed process_id=older",
    })
    assert cross_service_failure["failure"]["timestamp"] == "2026-08-13T00:00:13Z", cross_service_failure
    assert "pymtlf-a" in cross_service_failure["failure"]["detail"], cross_service_failure

    original_run = MODULE.subprocess.run
    captured = {}
    try:
        class Completed:
            returncode = 0
            stdout = "2026-08-13T00:00:00Z line\n"
            stderr = ""

        def fake_run(command, **_kwargs):
            captured["command"] = command
            return Completed()

        MODULE.subprocess.run = fake_run
        output = MODULE.container_logs_since({"Id": "container-c", "State": {"StartedAt": "2026-08-13T00:00:00.123Z"}})
    finally:
        MODULE.subprocess.run = original_run
    assert output.startswith("2026-08-13T00:00:00Z"), output
    assert captured["command"] == [
        "docker", "logs", "--timestamps", "--since", "2026-08-13T00:00:00.123Z", "container-c"
    ], captured

    def container(identity):
        name, digest = identity.split(":")
        return {
            "Id": identity,
            "Name": identity,
            "State": {"StartedAt": "0001-01-01T00:00:00Z"},
            "Config": {"Labels": {"io.5g-nwdaf.config-set": name, "io.5g-nwdaf.config-hash": digest}},
        }

    try:
        with redirect_stdout(io.StringIO()):
            MODULE.print_fl_summary({
                "pymtlf-a": container("set-a:aaaaaaaaaaaa"),
                "pymtlf-b": container("set-a:aaaaaaaaaaaa"),
                "pymtlf-c": container("set-b:bbbbbbbbbbbb"),
            })
    except RuntimeError as error:
        assert "config identity mismatch" in str(error), error
    else:
        raise AssertionError("FL summary accepted mixed config identities")
    print("ML_STATUS_TEST status=passed milestones={}".format(len(summary)))


if __name__ == "__main__":
    main()
