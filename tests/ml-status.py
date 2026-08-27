#!/usr/bin/env python3
"""Fixture regression for current-container FL milestone summaries."""

import importlib.util
import io
import tempfile
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
    assert summary["client_a"]["detail"] == "rounds=0,1 samples=0:6,1:6 final_validation=true", summary
    assert summary["client_b"]["detail"] == "rounds=0,1 samples=0:6,1:6 final_validation=true", summary
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

    static_logs = {
        "pymtlf-server": "\n".join((
            "2026-08-27T00:00:10Z Federated process started process_id=static-1 scopes=['1','2','3','4']",
            "2026-08-27T00:00:20Z Federated preparation complete process_id=static-1 participants=['1','2','3','4']",
            "2026-08-27T00:00:30Z Federated round aggregated process_id=static-1 round=0 artifact=a",
            "2026-08-27T00:00:40Z Federated round aggregated process_id=static-1 round=1 artifact=b",
            "2026-08-27T00:00:50Z Federated final validation evaluated process_id=static-1 base_wape=1.0 candidate_wape=0.5 gate_would_accept=True enforced=False",
            "2026-08-27T00:01:00Z Federated model published publication_id=pub model_id=2 state=COMPLETE required_scopes=0",
            "2026-08-27T00:01:01Z Federated final validation complete process_id=static-1 state=COMPLETE artifact=http://candidate",
        )),
    }
    for position in range(1, 5):
        static_logs["pymtlf-client-{}".format(position)] = "\n".join((
            "2026-08-27T00:00:25Z FL client local result ready subscription_id={} round=0 samples={} artifact=a".format(position, position),
            "2026-08-27T00:00:35Z FL client local result ready subscription_id={} round=1 samples={} artifact=b".format(position, position + 4),
            "2026-08-27T00:00:45Z FL client final validation ready subscription_id={} round=2 samples=1".format(position),
        ))
    static = MODULE.parse_fl_milestones(static_logs, "pymtlf-server")
    assert all(
        static["client_{}".format(position)]["timestamp"] != "not-seen"
        for position in range(1, 5)
    ), static
    assert static["client_4"]["detail"] == (
        "rounds=0,1 samples=0:4,1:8 final_validation=true"
    ), static
    assert MODULE.fl_result(static, "running", static=True) == (
        "outcome=verification-incomplete phase=cleanup"
    ), static
    cleanup_lines = []
    for position in range(1, 5):
        cleanup_lines.extend((
            "2026-08-27T00:00:{:02d}Z FL participant resource created process_id=static-1 nf=client-{} location=/training/{}".format(
                20 + position, position, position
            ),
            "2026-08-27T00:01:{:02d}Z FL participant resource deleted process_id=static-1 nf=client-{} location=/training/{} status=204".format(
                1 + position, position, position
            ),
        ))
    static_success = MODULE.parse_fl_milestones(
        {
            **static_logs,
            "pymtlf-server": static_logs["pymtlf-server"] + "\n" + "\n".join(cleanup_lines),
        },
        "pymtlf-server",
    )
    assert static_success["cleanup"]["detail"] == (
        "created=4 deleted=4 active=0 unknown_deletes=0"
    ), static_success
    assert MODULE.fl_result(static_success, "running", static=True) == (
        "outcome=complete model=2 evidence=static-publication"
    ), static_success
    mixed_run = MODULE.parse_fl_milestones(
        {
            **static_logs,
            "pymtlf-server": static_logs["pymtlf-server"]
            + "\n"
            + "\n".join(cleanup_lines[:6])
            + "\n2026-08-27T00:01:10Z FL participant resource created process_id=other-run nf=client-4 location=/training/other"
            + "\n2026-08-27T00:01:11Z FL participant resource deleted process_id=other-run nf=client-4 location=/training/other status=204",
        },
        "pymtlf-server",
    )
    assert mixed_run["cleanup"]["detail"] == (
        "created=3 deleted=3 active=0 unknown_deletes=0"
    ), mixed_run
    assert MODULE.fl_result(mixed_run, "running", static=True) == (
        "outcome=verification-incomplete phase=cleanup"
    ), mixed_run
    static_failed = MODULE.parse_fl_milestones(
        {
            **static_logs,
            "pymtlf-server": static_logs["pymtlf-server"]
            + "\n2026-08-27T00:01:02Z FL participant cleanup failed process_id=static-1 nf=client-4 error=timeout",
        },
        "pymtlf-server",
    )
    assert MODULE.fl_result(static_failed, "running", static=True).startswith(
        "outcome=failed"
    ), static_failed

    hfl_container = {
        "Config": {
            "Labels": {
                "io.5g-nwdaf.config-set": "static-hfl",
                "io.5g-nwdaf.config-hash": "c" * 64,
            }
        },
        "State": {
            "Running": True,
            "Status": "running",
            "StartedAt": "2026-08-27T00:00:00Z",
        },
    }
    rendered = io.StringIO()
    with redirect_stdout(rendered):
        MODULE.print_fl_summary({"pymtlf-root": hfl_container}, "pymtlf-root")
    assert "topology=static-hierarchical milestones=not-evaluated" in rendered.getvalue()
    assert "created=4" not in rendered.getvalue()

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

    original_output = MODULE.output
    cuda_calls = []
    try:
        def fake_output(command, timeout=30):
            cuda_calls.append((command, timeout))
            return "true"

        MODULE.output = fake_output
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            assert MODULE.cuda_visible("container-a", cache) == "true"
            assert MODULE.cuda_visible("container-a", cache) == "true"
            assert MODULE.cuda_visible("container-b", cache) == "true"
    finally:
        MODULE.output = original_output
    assert len(cuda_calls) == 2, cuda_calls

    incremental_calls = []
    try:
        class IncrementalCompleted:
            returncode = 0
            stderr = ""

            def __init__(self, stdout):
                self.stdout = stdout

        def fake_incremental_run(command, **_kwargs):
            incremental_calls.append(command)
            return IncrementalCompleted(
                "2026-08-13T00:00:0{}Z line-{}\n".format(
                    len(incremental_calls), len(incremental_calls)
                )
            )

        MODULE.subprocess.run = fake_incremental_run
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            container_value = {
                "Id": "container-c",
                "State": {"StartedAt": "2026-08-13T00:00:00.123Z"},
            }
            first = MODULE.container_logs_since(
                container_value,
                service="pymtlf-c",
                cache_dir=cache,
                until="2026-08-13T00:00:10Z",
            )
            second = MODULE.container_logs_since(
                container_value,
                service="pymtlf-c",
                cache_dir=cache,
                until="2026-08-13T00:00:20Z",
            )
    finally:
        MODULE.subprocess.run = original_run
    assert "line-1" in first, first
    assert "line-1" in second and "line-2" in second, second
    assert incremental_calls[0] == [
        "docker", "logs", "--timestamps", "--since",
        "2026-08-13T00:00:00.123Z", "--until", "2026-08-13T00:00:10Z",
        "container-c",
    ], incremental_calls
    assert incremental_calls[1] == [
        "docker", "logs", "--timestamps", "--since",
        "2026-08-13T00:00:10Z", "--until", "2026-08-13T00:00:20Z",
        "container-c",
    ], incremental_calls

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

    original_output = MODULE.output
    original_argv = MODULE.sys.argv
    identity_commands = []
    try:
        def identity_output(command, timeout=30):
            identity_commands.append(command)
            if command[1:3] == ["ps", "-aq"]:
                return "container-client"
            if command[1] == "inspect":
                return MODULE.json.dumps([{
                    "Id": "container-client",
                    "Config": {"Labels": {
                        "com.docker.compose.service": "pymtlf-client-1",
                        "io.5g-nwdaf.config-set": "static-flat",
                        "io.5g-nwdaf.config-hash": "selected-hash",
                    }},
                    "State": {"Running": True, "Status": "running"},
                }])
            raise AssertionError("identity-only mode performed extra Docker work: {}".format(command))

        MODULE.output = identity_output
        MODULE.sys.argv = [
            "ml-status.py", "--services", "pymtlf-client-1",
            "--coordinator", "pymtlf-client-1", "--config-set", "static-flat",
            "--config-hash", "selected-hash", "--identity-only",
            "--require-running-selected",
        ]
        assert MODULE.main() == 0
        assert [command[1] for command in identity_commands] == ["ps", "inspect"]

        MODULE.sys.argv[2] = "pymtlf-client-1,pymtlf-client-2"
        assert MODULE.main() == 1
        MODULE.sys.argv[2] = "pymtlf-client-1"

        MODULE.sys.argv[8] = "wrong-hash"
        assert MODULE.main() == 1

        MODULE.sys.argv.pop()
        MODULE.sys.argv.append("--allow-stopped-selected-mismatch")
        assert MODULE.main() == 1

        def stopped_mismatch_output(command, timeout=30):
            if command[1:3] == ["ps", "-aq"]:
                return "container-client"
            if command[1] == "inspect":
                return MODULE.json.dumps([{
                    "Id": "container-client",
                    "Config": {"Labels": {
                        "com.docker.compose.service": "pymtlf-client-1",
                        "io.5g-nwdaf.config-set": "previous-static-flat",
                        "io.5g-nwdaf.config-hash": "previous-hash",
                    }},
                    "State": {"Running": False, "Status": "exited"},
                }])
            raise AssertionError("stopped-mismatch check performed extra Docker work")

        MODULE.output = stopped_mismatch_output
        assert MODULE.main() == 0
        MODULE.sys.argv.pop()
        assert MODULE.main() == 1

        def unexpected_output(command, timeout=30):
            if command[1:3] == ["ps", "-aq"]:
                return "container-foreign"
            if command[1] == "inspect":
                return MODULE.json.dumps([{
                    "Id": "container-foreign",
                    "Config": {"Labels": {
                        "com.docker.compose.service": "pymtlf-foreign",
                        "io.5g-nwdaf.config-set": "other-topology",
                        "io.5g-nwdaf.config-hash": "other-hash",
                    }},
                    "State": {"Running": True, "Status": "running"},
                }])
            raise AssertionError("unexpected-container check performed extra work")

        MODULE.output = unexpected_output
        MODULE.sys.argv[-2] = "selected-hash"
        assert MODULE.main() == 1
    finally:
        MODULE.output = original_output
        MODULE.sys.argv = original_argv
    print("ML_STATUS_TEST status=passed milestones={}".format(len(summary)))


if __name__ == "__main__":
    main()
