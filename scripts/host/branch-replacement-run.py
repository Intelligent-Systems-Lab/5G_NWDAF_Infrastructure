#!/usr/bin/env python3
"""Run one protocol-driven GPU Branch replacement acceptance flow."""

from __future__ import annotations

import argparse
import base64
import fcntl
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from branch_replacement import (
    BranchReplacementContract,
    BranchReplacementError,
    EvidenceWriter,
    IncrementalJsonlReader,
    PhaseTracker,
    check_evidence,
    parse_resource_log,
    utc_now,
    validate_run_name,
)
from configlib import (
    ROOT,
    image_scenario_contract,
    load_runtime_manifest,
    load_yaml,
    resolve_config_dir,
    resolve_config_scenario,
    resolve_path,
    selected_component_paths,
)


def _load_fl_control():
    path = ROOT / "scripts" / "host" / "fl-control.py"
    spec = importlib.util.spec_from_file_location("testbed_fl_control", path)
    if spec is None or spec.loader is None:
        raise BranchReplacementError("cannot load the existing FL controller")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FL_CONTROL = _load_fl_control()
PROJECT = "5g-nwdaf-infrastructure"
IMAGE = "5g-nwdaf-infrastructure/pymtlf:local"


def command_output(
    command: list[str],
    *,
    timeout: int = 30,
    environment: dict | None = None,
    combined: bool = False,
) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-4000:]
        raise BranchReplacementError(
            "command failed ({}): {}".format(command[0], detail or "no diagnostic output")
        )
    output = result.stdout
    if combined and result.stderr:
        output += result.stderr
    return output.strip()


def quiet_command(
    command: list[str],
    label: str,
    *,
    timeout: int,
    environment: dict | None = None,
) -> str:
    """Run a noisy lifecycle command with bounded capture and low-frequency progress."""
    started = time.monotonic()
    last_heartbeat = started
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as transcript:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=transcript,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                elapsed = time.monotonic() - started
                if elapsed >= timeout:
                    raise BranchReplacementError(
                        "{} exceeded {} seconds".format(label, timeout)
                    )
                if time.monotonic() - last_heartbeat >= 30:
                    print(
                        "HEARTBEAT step={} elapsed={}s".format(label, int(elapsed)),
                        flush=True,
                    )
                    last_heartbeat = time.monotonic()
                time.sleep(1)
        except BaseException:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
            raise
        transcript.seek(0)
        output = transcript.read()
    if process.returncode:
        raise BranchReplacementError(
            "{} failed: {}".format(label, output.strip()[-4000:] or "no diagnostic output")
        )
    return output


def repository_metadata(testbed: dict, runtime: dict) -> dict:
    paths = ["."] + selected_component_paths(testbed, runtime)
    values = {}
    for relative in paths:
        repository = ROOT if relative == "." else ROOT / relative
        values["5G_NWDAF_Infrastructure" if relative == "." else relative] = {
            "branch": command_output(
                ["git", "-C", str(repository), "branch", "--show-current"]
            ),
            "revision": command_output(
                ["git", "-C", str(repository), "rev-parse", "HEAD"]
            ),
            "dirty": bool(
                command_output(
                    ["git", "-C", str(repository), "status", "--porcelain"]
                )
            ),
        }
    return values


class LiveEnvironment:
    def __init__(self, testbed_path: Path, config_dir: Path, manifest: dict) -> None:
        self.testbed_path = testbed_path
        self.config_dir = config_dir
        self.manifest = manifest
        self.runtime = manifest["runtime"]
        self._root_container_id: str | None = None
        self._faulted_guest: tuple[str, str] | None = None

    def validate_inputs(self) -> dict[str, str]:
        quiet_command(
            [
                str(ROOT / "scripts/host/experiment-validate.sh"),
                str(self.testbed_path),
                str(self.config_dir),
            ],
            "input-validation",
            timeout=600,
        )
        states = self.vm_states()
        unavailable = [name for name, state in states.items() if state != "running"]
        if unavailable:
            raise BranchReplacementError(
                "selected VMs are not running: {}".format(", ".join(unavailable))
            )
        return states

    def _provider_shell(self, body: str, arguments: list[str], *, timeout: int = 180) -> str:
        return command_output(
            ["bash", "-c", body, "branch-replacement", *arguments], timeout=timeout
        )

    def vm_states(self) -> dict[str, str]:
        output = self._provider_shell(
            'source "$1"; select_testbed_machines "$2"; provider_runtime_state_records',
            [str(ROOT / "scripts/host/lib.sh"), str(self.testbed_path)],
        )
        values = {}
        for line in output.splitlines():
            fields = line.split("|")
            if len(fields) == 2:
                values[fields[0]] = fields[1]
        expected = self.runtime["guestMachines"]
        if sorted(values) != sorted(expected):
            raise BranchReplacementError("provider state omitted or added a selected VM")
        return values

    def start(self) -> None:
        print("MILESTONE runtime-starting", flush=True)
        quiet_command(
            [
                str(ROOT / "scripts/host/experiment-start.sh"),
                str(self.testbed_path),
                str(self.config_dir),
            ],
            "runtime-start",
            timeout=1800,
        )
        print("MILESTONE runtime-ready", flush=True)

    def active_guest_identity(self) -> dict[str, dict[str, str]]:
        output = self._provider_shell(
            r'''source "$1"
select_testbed_machines "$2"
assert_selected_provider_running
assert_guest_runtime_identity "$3"
for machine in "${MACHINES[@]}"; do
  identity=$(vssh "$machine" 'printf "%s|%s\n" "$(readlink /etc/5g-nwdaf-infrastructure/active 2>/dev/null || true)" "$(cat /etc/5g-nwdaf-infrastructure/active.sha256 2>/dev/null || true)"' | tr -d '\r' | tail -n 1)
  printf 'ACTIVE|%s|%s\n' "$machine" "$identity"
done''',
            [
                str(ROOT / "scripts/host/lib.sh"),
                str(self.testbed_path),
                str(self.config_dir),
            ],
        )
        lines = [line.split("|", 3) for line in output.splitlines() if line.startswith("ACTIVE|")]
        if len(lines) != len(self.runtime["guestMachines"]):
            raise BranchReplacementError("active Guest config identity is incomplete")
        values = {
            fields[1]: {"activeTarget": fields[2], "activeIdentity": fields[3]}
            for fields in lines if len(fields) == 4
        }
        if sorted(values) != sorted(self.runtime["guestMachines"]):
            raise BranchReplacementError("active Guest identity added or omitted a machine")
        return values

    def guest_runtime_snapshot(self) -> dict[str, dict[str, str]]:
        output = self._provider_shell(
            r'''source "$1"
select_testbed_machines "$2"
assert_selected_provider_running
assert_guest_runtime_identity "$3"
while IFS='|' read -r machine unit kind; do
  state=$(vssh "$machine" "systemctl is-active 5g-nwdaf@$unit.service 2>/dev/null || true" | tr -d '\r' | tail -n 1)
  printf 'GUEST|%s|%s|%s|%s\n' "$machine" "$unit" "$kind" "$state"
done < <(config_guest_service_records "$3")''',
            [
                str(ROOT / "scripts/host/lib.sh"),
                str(self.testbed_path),
                str(self.config_dir),
            ],
        )
        records = [
            line.split("|", 4)
            for line in output.splitlines()
            if line.startswith("GUEST|")
        ]
        expected = {
            (item["machine"], item["unit"], item["kind"])
            for item in self.runtime["guestServices"]
        }
        actual = {(fields[1], fields[2], fields[3]) for fields in records if len(fields) == 5}
        if actual != expected or len(records) != len(expected):
            raise BranchReplacementError("actual Guest service inventory is not exact")
        values = {
            fields[2]: {"machine": fields[1], "kind": fields[3], "state": fields[4]}
            for fields in records
        }
        if any(value["state"] != "active" for value in values.values()):
            raise BranchReplacementError("selected Guest services are not all active")
        return values

    def registration_snapshot(self) -> dict:
        output = command_output(
            [
                str(ROOT / "scripts/host/registration-check.sh"),
                "--once",
                str(self.testbed_path),
                str(self.config_dir),
            ],
            timeout=60,
        )
        identities = self.runtime["resetScope"]["nrf"]["nfInstanceIds"]
        marker = "NRF REGISTRATIONS selected={} state=ready".format(len(identities))
        if marker not in output.splitlines():
            raise BranchReplacementError("exact NRF registration evidence is incomplete")
        return {"nfInstanceIds": identities, "state": "ready"}

    def _container_id(self, service: str, *, running: bool = True) -> str:
        command = ["docker", "ps"]
        if not running:
            command.append("-a")
        command.extend(
            [
                "--filter", "label=com.docker.compose.project=" + PROJECT,
                "--filter", "label=com.docker.compose.service=" + service,
                "--format", "{{.ID}}",
            ]
        )
        values = command_output(command).splitlines()
        if len(values) != 1:
            raise BranchReplacementError(
                "expected exactly one {} container for {}".format(
                    "running" if running else "selected", service
                )
            )
        return values[0]

    def runtime_snapshot(self) -> dict:
        selected = {}
        image_ids = set()
        for service in self.runtime["hostContainers"]:
            container_id = self._container_id(service)
            inspected = json.loads(
                command_output(["docker", "inspect", container_id])
            )[0]
            image_ids.add(inspected["Image"])
            environment = dict(
                item.split("=", 1)
                for item in inspected["Config"].get("Env", [])
                if "=" in item
            )
            native = load_yaml(self.config_dir / (service + ".yaml"))
            client = native.get("federated_learning", {}).get("client")
            if client is not None:
                device = client["training"]["device"]
            else:
                device = native["federated_learning"]["experiment_recording"][
                    "validation"
                ]["device"]
            selected[service] = {
                "containerId": container_id,
                "device": device,
                "runtime": inspected["HostConfig"].get("Runtime", "default"),
                "cdiSelector": environment.get("NVIDIA_VISIBLE_DEVICES"),
                "state": inspected["State"]["Status"],
                "health": inspected["State"].get("Health", {}).get("Status"),
            }
            if device == "cuda:0":
                if (
                    inspected["HostConfig"].get("Runtime") != "nvidia"
                    or environment.get("NVIDIA_VISIBLE_DEVICES") != "nvidia.com/gpu=all"
                ):
                    raise BranchReplacementError(
                        service + " does not use the selected NVIDIA runtime/CDI mapping"
                    )
                probe = json.loads(
                    command_output(
                        [
                            "docker", "exec", container_id, "python", "-c",
                            "import json,torch; print(json.dumps({'available':torch.cuda.is_available(),'device':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,'torch':torch.__version__,'cuda':torch.version.cuda}))",
                        ],
                        timeout=30,
                    )
                )
                if probe.get("available") is not True:
                    raise BranchReplacementError(service + " cannot see CUDA")
                selected[service]["cuda"] = probe
            elif (
                inspected["HostConfig"].get("Runtime") == "nvidia"
                or environment.get("NVIDIA_VISIBLE_DEVICES") is not None
            ):
                raise BranchReplacementError(service + " is CPU-owned but exposes a GPU")
            if (
                selected[service]["state"] != "running"
                or selected[service]["health"] != "healthy"
            ):
                raise BranchReplacementError(service + " is not running and healthy")
        if len([value for value in selected.values() if value["device"] == "cuda:0"]) != 7:
            raise BranchReplacementError("actual runtime does not contain seven CUDA participants")
        if len(image_ids) != 1:
            raise BranchReplacementError("selected PyMTLF containers do not share one image")
        image = json.loads(
            command_output(["docker", "image", "inspect", next(iter(image_ids))])
        )[0]
        gpu = self.gpu_snapshot()
        return {
            "containers": selected,
            "image": {
                "id": image["Id"],
                "revision": image.get("Config", {}).get("Labels", {}).get(
                    "org.opencontainers.image.revision"
                ),
            },
            "gpu": {**gpu, "participantCount": 7},
        }

    def gpu_snapshot(self) -> dict:
        gpu = command_output(
            [
                "nvidia-smi",
                "--query-gpu=uuid,name,driver_version,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ]
        ).splitlines()
        if len(gpu) != 1:
            raise BranchReplacementError("acceptance requires exactly one selected Host GPU")
        uuid_value, name, driver, total, free = [item.strip() for item in gpu[0].split(",")]
        return {
            "uuid": uuid_value,
            "name": name,
            "driver": driver,
            "memoryTotalMiB": int(total),
            "memoryFreeMiB": int(free),
        }

    def selected_image_snapshot(self) -> dict:
        inspected = json.loads(command_output(["docker", "image", "inspect", IMAGE]))
        if len(inspected) != 1:
            raise BranchReplacementError("selected PyMTLF image is ambiguous")
        image = inspected[0]
        return {
            "id": image["Id"],
            "revision": image.get("Config", {}).get("Labels", {}).get(
                "org.opencontainers.image.revision"
            ),
        }

    def root_observation_reader(self, plan_id: str) -> IncrementalJsonlReader:
        root_service = self.runtime["coordinatorContainer"]
        self._root_container_id = self._container_id(root_service)
        volume_target = load_yaml(self.config_dir / (root_service + ".yaml"))[
            "federated_learning"
        ]["experiment_recording"]["directory"]
        path = "{}/{}/observations.jsonl".format(volume_target, plan_id)
        program = r'''import base64,json,sys
from pathlib import Path
path=Path(sys.argv[1]); offset=int(sys.argv[2])
if not path.exists():
    print(json.dumps({"offset":offset,"data":""})); raise SystemExit(0)
size=path.stat().st_size
if size < offset:
    raise SystemExit("observation file was truncated")
with path.open("rb") as stream:
    stream.seek(offset); data=stream.read(1048576)
print(json.dumps({"offset":offset+len(data),"data":base64.b64encode(data).decode("ascii")}))'''

        def read_chunk(offset: int) -> tuple[int, bytes]:
            value = json.loads(
                command_output(
                    [
                        "docker", "exec", self._root_container_id, "python", "-c",
                        program, path, str(offset),
                    ],
                    timeout=15,
                )
            )
            return int(value["offset"]), base64.b64decode(value["data"])

        return IncrementalJsonlReader(read_chunk)

    @staticmethod
    def _single_record(output: str, prefix: str) -> list[str]:
        records = [line.split("|") for line in output.splitlines() if line.startswith(prefix)]
        if len(records) != 1:
            raise BranchReplacementError("missing or duplicate {} evidence".format(prefix[:-1]))
        return records[0]

    def fail_stop_primary(self, contract: BranchReplacementContract) -> dict:
        print("MILESTONE primary-fail-stop-starting", flush=True)

        def freeze_guest() -> dict:
            frozen = self._provider_shell(
                r'''source "$1"
select_testbed_machines "$2"
assert_selected_provider_running
assert_guest_runtime_identity "$3"
actual=$(config_guest_service_machine "$3" "$5")
[ "$actual" = "$4" ] || { echo "selected Guest target mismatch" >&2; exit 1; }
service="5g-nwdaf@$5.service"
state=$(vssh "$4" "systemctl is-active '$service' 2>/dev/null || true" | tr -d '\r' | tail -n 1)
[ "$state" = active ] || { echo "Guest Branch is not active: $state" >&2; exit 1; }
pid=$(vssh "$4" "systemctl show '$service' --property=MainPID --value" | tr -d '\r' | tail -n 1)
[[ "$pid" =~ ^[1-9][0-9]*$ ]] || { echo "Guest Branch MainPID is invalid: $pid" >&2; exit 1; }
link=$(vssh "$4" "readlink '/run/systemd/system/$service' 2>/dev/null || true" | tr -d '\r' | tail -n 1)
[ "$link" != /dev/null ] || { echo "Guest Branch already has a runtime mask" >&2; exit 1; }
# Freeze the Guest owner before the Host backend can disappear. Otherwise the
# still-running Go process can observe that loss and clean up its Leaf edges.
vssh "$4" "sudo systemctl kill --kill-who=all --signal=SIGSTOP '$service'"
current_pid=$(vssh "$4" "systemctl show '$service' --property=MainPID --value" | tr -d '\r' | tail -n 1)
[ "$current_pid" = "$pid" ] || { echo "Guest Branch MainPID changed while freezing: $current_pid" >&2; exit 1; }
printf 'GUEST_FROZEN|%s\n' "$pid"''',
                [
                    str(ROOT / "scripts/host/lib.sh"), str(self.testbed_path),
                    str(self.config_dir), contract.primary_machine, contract.primary_unit,
                ],
                timeout=180,
            )
            frozen_record = self._single_record(frozen, "GUEST_FROZEN|")
            return {
                "machine": contract.primary_machine,
                "unit": contract.primary_unit,
                "originalPid": int(frozen_record[1]),
            }

        def kill_frozen_guest(target: dict) -> dict:
            original_pid = target["originalPid"]
            # The preflight established that this exact target had no prior runtime
            # mask, so cleanup owns removing a mask even if the remote call returns
            # an ambiguous failure after applying it.
            self._faulted_guest = (contract.primary_machine, contract.primary_unit)
            killed = self._provider_shell(
                r'''source "$1"
select_testbed_machines "$2"
assert_selected_provider_running
assert_guest_runtime_identity "$3"
actual=$(config_guest_service_machine "$3" "$5")
[ "$actual" = "$4" ] || { echo "selected Guest target mismatch" >&2; exit 1; }
service="5g-nwdaf@$5.service"
current_pid=$(vssh "$4" "systemctl show '$service' --property=MainPID --value" | tr -d '\r' | tail -n 1)
[ "$current_pid" = "$6" ] || { echo "Guest Branch MainPID changed before fail-stop: $current_pid" >&2; exit 1; }
# The runtime mask prevents Restart=on-failure from undoing the injected crash.
vssh "$4" "sudo systemctl mask --runtime '$service' >/dev/null && sudo systemctl daemon-reload"
link=$(vssh "$4" "readlink '/run/systemd/system/$service' 2>/dev/null || true" | tr -d '\r' | tail -n 1)
[ "$link" = /dev/null ] || { echo "Guest Branch runtime mask was not installed" >&2; exit 1; }
vssh "$4" "sudo systemctl kill --kill-who=all --signal=SIGKILL '$service'"
for attempt in $(seq 1 50); do
  status=$(vssh "$4" "systemctl show '$service' --property=MainPID --property=ActiveState --property=SubState" | tr -d '\r')
  current_pid=$(sed -n 's/^MainPID=//p' <<<"$status" | tail -n 1)
  active_state=$(sed -n 's/^ActiveState=//p' <<<"$status" | tail -n 1)
  sub_state=$(sed -n 's/^SubState=//p' <<<"$status" | tail -n 1)
  if [ "$current_pid" = 0 ] && { [ "$active_state" = inactive ] || [ "$active_state" = failed ]; }; then
    printf 'GUEST_KILLED|%s|%s|%s\n' "$6" "$active_state" "$sub_state"
    exit 0
  fi
  if [[ "$current_pid" =~ ^[1-9][0-9]*$ ]] && [ "$current_pid" != "$6" ]; then
    echo "Guest Branch restarted with MainPID $current_pid" >&2
    exit 1
  fi
  sleep 0.2
done
echo "Guest Branch did not converge to a stopped state" >&2
exit 1''',
                [
                    str(ROOT / "scripts/host/lib.sh"), str(self.testbed_path),
                    str(self.config_dir), contract.primary_machine, contract.primary_unit,
                    str(original_pid),
                ],
                timeout=180,
            )
            killed_record = self._single_record(killed, "GUEST_KILLED|")
            if int(killed_record[1]) != original_pid:
                raise BranchReplacementError("Guest fail-stop PID evidence changed")
            return {
                **target,
                "freezeSignal": "SIGSTOP",
                "signal": "SIGKILL",
                "activeState": killed_record[2],
                "subState": killed_record[3],
                "restartSuppressed": True,
            }

        def inspect_container() -> dict:
            container_id = self._container_id(contract.primary_service)
            inspected = json.loads(command_output(["docker", "inspect", container_id]))[0]
            labels = inspected["Config"].get("Labels", {})
            if (
                labels.get("com.docker.compose.project") != PROJECT
                or labels.get("com.docker.compose.service") != contract.primary_service
            ):
                raise BranchReplacementError("primary PyMTLF container identity mismatch")
            state = inspected["State"]
            original_pid = state.get("Pid")
            restart_policy = inspected.get("HostConfig", {}).get(
                "RestartPolicy", {}
            ).get("Name", "")
            if state.get("Running") is not True or not isinstance(original_pid, int) or original_pid <= 0:
                raise BranchReplacementError("primary PyMTLF container is not running")
            if restart_policy not in ("", "no"):
                raise BranchReplacementError(
                    "primary PyMTLF container has an automatic restart policy"
                )
            return {
                "service": contract.primary_service,
                "containerId": container_id,
                "originalPid": original_pid,
                "restartPolicy": restart_policy or "no",
                "restartCount": inspected.get("RestartCount", 0),
            }

        def freeze_container(target: dict) -> None:
            container_id = target["containerId"]
            command_output(["docker", "kill", "--signal", "STOP", container_id], timeout=30)
            selected_id = self._container_id(contract.primary_service)
            inspected = json.loads(command_output(["docker", "inspect", container_id]))[0]
            if (
                selected_id != container_id
                or inspected["State"].get("Running") is not True
                or inspected["State"].get("Pid") != target["originalPid"]
            ):
                raise BranchReplacementError("primary PyMTLF container changed while freezing")
            process_state = command_output(
                ["ps", "-o", "stat=", "-p", str(target["originalPid"])], timeout=10,
            ).strip()
            if not process_state.startswith("T"):
                raise BranchReplacementError(
                    "primary PyMTLF container did not enter a stopped process state"
                )

        def kill_frozen_container(target: dict) -> dict:
            container_id = target["containerId"]
            command_output(["docker", "kill", "--signal", "KILL", container_id], timeout=30)
            selected_id = self._container_id(contract.primary_service, running=False)
            if selected_id != container_id:
                raise BranchReplacementError("primary PyMTLF container identity changed")
            stopped = json.loads(command_output(["docker", "inspect", container_id]))[0]
            stopped_state = stopped["State"]
            if (
                stopped_state.get("Running") is True
                or stopped_state.get("Pid") != 0
                or stopped_state.get("ExitCode") != 137
                or stopped.get("RestartCount", 0) != target["restartCount"]
            ):
                raise BranchReplacementError(
                    "primary PyMTLF container did not remain hard-stopped"
                )
            return {
                **target,
                "freezeSignal": "SIGSTOP",
                "signal": "SIGKILL",
                "exitCode": stopped_state["ExitCode"],
            }

        container_target = inspect_container()
        guest_target = None
        container_frozen = False
        try:
            # Both application owners must be unable to perform protocol cleanup
            # before either dependency is killed.
            guest_target = freeze_guest()
            container_frozen = True
            freeze_container(container_target)
            guest_result = kill_frozen_guest(guest_target)
            container_result = kill_frozen_container(container_target)
            container_frozen = False
        except Exception as error:
            recovery_errors = []
            if container_frozen:
                try:
                    command_output(
                        ["docker", "kill", "--signal", "CONT", container_target["containerId"]],
                        timeout=30,
                    )
                except Exception as recovery_error:
                    recovery_errors.append("container resume failed: {}".format(recovery_error))
            if guest_target is not None and self._faulted_guest is None:
                try:
                    self._provider_shell(
                        r'''source "$1"
select_testbed_machines "$2"
assert_selected_provider_running
assert_guest_runtime_identity "$3"
actual=$(config_guest_service_machine "$3" "$5")
[ "$actual" = "$4" ] || { echo "selected Guest target mismatch" >&2; exit 1; }
service="5g-nwdaf@$5.service"
current_pid=$(vssh "$4" "systemctl show '$service' --property=MainPID --value" | tr -d '\r' | tail -n 1)
if [ "$current_pid" = "$6" ]; then
  vssh "$4" "sudo systemctl kill --kill-who=all --signal=SIGCONT '$service'"
elif [ "$current_pid" != 0 ]; then
  echo "Guest Branch MainPID changed during fault recovery: $current_pid" >&2
  exit 1
fi
printf 'GUEST_RESUMED|%s\n' "$current_pid"''',
                        [
                            str(ROOT / "scripts/host/lib.sh"), str(self.testbed_path),
                            str(self.config_dir), contract.primary_machine,
                            contract.primary_unit, str(guest_target["originalPid"]),
                        ],
                        timeout=180,
                    )
                except Exception as recovery_error:
                    recovery_errors.append("Guest resume failed: {}".format(recovery_error))
            detail = str(error)
            if recovery_errors:
                detail += "; " + "; ".join(recovery_errors)
            raise BranchReplacementError("partial primary stop: " + detail) from error
        print("MILESTONE primary-fail-stopped", flush=True)
        return {"guest": guest_result, "container": container_result}

    def _restore_faulted_guest(self) -> None:
        if self._faulted_guest is None:
            return
        machine, unit = self._faulted_guest
        self._provider_shell(
            r'''source "$1"
select_testbed_machines "$2"
assert_selected_provider_running
assert_guest_runtime_identity "$3"
actual=$(config_guest_service_machine "$3" "$5")
[ "$actual" = "$4" ] || { echo "selected Guest target mismatch" >&2; exit 1; }
service="5g-nwdaf@$5.service"
vssh "$4" "sudo systemctl unmask --runtime '$service' >/dev/null && sudo systemctl daemon-reload"
link=$(vssh "$4" "readlink '/run/systemd/system/$service' 2>/dev/null || true" | tr -d '\r' | tail -n 1)
[ "$link" != /dev/null ] || { echo "Guest Branch runtime mask remains installed" >&2; exit 1; }
state=$(vssh "$4" "systemctl is-active '$service' 2>/dev/null || true" | tr -d '\r' | tail -n 1)
[ "$state" = inactive ] || [ "$state" = failed ] || { echo "Guest Branch restarted during cleanup: $state" >&2; exit 1; }
printf 'GUEST_RESTART_RESTORED\n' ''',
            [
                str(ROOT / "scripts/host/lib.sh"), str(self.testbed_path),
                str(self.config_dir), machine, unit,
            ],
            timeout=180,
        )
        self._faulted_guest = None

    def final_model_source(self, plan_id: str) -> dict:
        try:
            parsed_plan_id = uuid.UUID(plan_id)
        except (AttributeError, TypeError, ValueError) as error:
            raise BranchReplacementError("final model planId is not a UUIDv4") from error
        if parsed_plan_id.version != 4 or str(parsed_plan_id) != plan_id:
            raise BranchReplacementError("final model planId is not a canonical UUIDv4")

        root_service = self.runtime["coordinatorContainer"]
        root_config = load_yaml(self.config_dir / (root_service + ".yaml"))
        record_directory = root_config["federated_learning"]["experiment_recording"][
            "directory"
        ]
        compose = load_yaml(self.config_dir / "compose.yaml")
        volume_mounts = [
            item
            for item in compose["services"][root_service].get("volumes", [])
            if isinstance(item, dict)
            and item.get("type") == "volume"
            and isinstance(item.get("target"), str)
            and (
                record_directory == item["target"]
                or record_directory.startswith(item["target"].rstrip("/") + "/")
            )
        ]
        if len(volume_mounts) != 1:
            raise BranchReplacementError(
                "Root experiment record directory lacks one exact volume owner"
            )
        mount = volume_mounts[0]
        logical_volume = mount.get("source")
        selected_volumes = {
            item["name"]: item["image"] for item in self.runtime["mlVolumes"]
        }
        if logical_volume not in selected_volumes:
            raise BranchReplacementError("Root experiment record volume is not selected")
        physical_volume = PROJECT + "_" + logical_volume
        return {
            "rootService": root_service,
            "logicalVolume": logical_volume,
            "physicalVolume": physical_volume,
            "image": selected_volumes[logical_volume],
            "mountTarget": mount["target"],
            "artifactPath": "{}/{}/final-model.tar.gz".format(
                record_directory.rstrip("/"), plan_id
            ),
        }

    def copy_final_artifact(
        self,
        plan_id: str,
        expected_size: int,
        destination: Path,
    ) -> None:
        if (
            not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size <= 0
        ):
            raise BranchReplacementError("final model checkpoint size is invalid")
        source = self.final_model_source(plan_id)
        inspected = json.loads(
            command_output(["docker", "volume", "inspect", source["physicalVolume"]])
        )
        if len(inspected) != 1:
            raise BranchReplacementError("Root experiment record volume is ambiguous")
        labels = inspected[0].get("Labels") or {}
        if (
            inspected[0].get("Name") != source["physicalVolume"]
            or labels.get("com.docker.compose.project") != PROJECT
            or labels.get("com.docker.compose.volume") != source["logicalVolume"]
        ):
            raise BranchReplacementError("Root experiment record volume identity differs")

        temporary = destination.with_name("." + destination.name + ".collecting")
        temporary.unlink(missing_ok=True)
        collector_id = None
        try:
            collector_id = command_output(
                [
                    "docker", "create", "--network", "none", "--read-only",
                    "--label", "io.5g-nwdaf.experiment-plan=" + plan_id,
                    "--mount",
                    "type=volume,source={},target={},readonly".format(
                        source["physicalVolume"], source["mountTarget"]
                    ),
                    source["image"],
                ],
                timeout=30,
            )
            if not collector_id:
                raise BranchReplacementError("artifact collector container was not created")
            command_output(
                [
                    "docker", "cp",
                    collector_id + ":" + source["artifactPath"],
                    str(temporary),
                ],
                timeout=60,
            )
        finally:
            if collector_id:
                command_output(
                    ["docker", "rm", "--force", collector_id], timeout=30
                )
        if not temporary.is_file() or temporary.stat().st_size != expected_size:
            temporary.unlink(missing_ok=True)
            raise BranchReplacementError("final Root artifact size differs from checkpoint")
        temporary.chmod(0o644)
        temporary.replace(destination)

    def collect_protocol_evidence(
        self,
        directory: Path,
        contract: BranchReplacementContract,
        plan_id: str,
        since: str,
    ) -> dict:
        services = {
            "rootEdges": self.runtime["coordinatorContainer"],
            "primaryLeafEdges": contract.primary_service,
            "replacementLeafEdges": next(
                item["backends"]["mtlf"]
                for item in self.runtime["nwdafs"]
                if item["nfInstanceId"] == contract.replacement_nf_instance_id
            ),
        }
        directory.mkdir(parents=True, exist_ok=True)
        evidence = {"planId": plan_id}
        for field, service in services.items():
            container_id = self._container_id(service, running=False)
            output = command_output(
                [
                    "docker", "logs", "--since", since, container_id,
                ],
                timeout=30,
                combined=True,
            )
            selected_lines = [line for line in output.splitlines() if plan_id in line]
            (directory / (service + ".log")).write_text(
                "\n".join(selected_lines) + ("\n" if selected_lines else ""),
                encoding="utf-8",
            )
            records = parse_resource_log(output, plan_id)
            if field == "rootEdges":
                wanted = {
                    contract.primary_nf_instance_id,
                    contract.replacement_nf_instance_id,
                }
            else:
                wanted = set(contract.fault_group_leaf_nf_instance_ids)
            evidence[field] = [
                item for item in records if item["nfInstanceId"] in wanted
            ]
        if {item["nfInstanceId"] for item in evidence["rootEdges"]} != {
            contract.primary_nf_instance_id,
            contract.replacement_nf_instance_id,
        }:
            raise BranchReplacementError(
                "Root logs do not contain exact primary and replacement resources"
            )
        expected_leaves = set(contract.fault_group_leaf_nf_instance_ids)
        for field in ("primaryLeafEdges", "replacementLeafEdges"):
            if {item["nfInstanceId"] for item in evidence[field]} != expected_leaves:
                raise BranchReplacementError(field + " does not contain the exact Area leaves")
        if (
            {item["notifCorreId"] for item in evidence["primaryLeafEdges"]}
            & {item["notifCorreId"] for item in evidence["replacementLeafEdges"]}
            or {item["resourceLocation"] for item in evidence["primaryLeafEdges"]}
            & {item["resourceLocation"] for item in evidence["replacementLeafEdges"]}
        ):
            raise BranchReplacementError("replacement reused an old Branch-to-Leaf resource")
        return evidence

    def stop_all(self) -> dict:
        print("MILESTONE runtime-stopping", flush=True)
        stop_error = None
        try:
            quiet_command(
                [
                    str(ROOT / "scripts/host/experiment-stop.sh"),
                    str(self.testbed_path),
                    str(self.config_dir),
                ],
                "runtime-stop",
                timeout=600,
            )
        except Exception as error:
            stop_error = error
        try:
            self._restore_faulted_guest()
        except Exception as error:
            if stop_error is not None:
                raise BranchReplacementError(
                    "runtime stop failed: {}; Guest restart restoration failed: {}".format(
                        stop_error, error
                    )
                ) from error
            raise
        if stop_error is not None:
            raise stop_error
        print("MILESTONE runtime-stopped", flush=True)
        return {
            "processesStopped": True,
            "guestRestartPolicyRestored": self._faulted_guest is None,
        }

    def evaluate(self, run_id: str, artifact_key: str, artifact: Path) -> dict:
        leaf = next(
            item for item in self.runtime["nwdafs"] if item.get("role") == "leaf"
        )
        config = (self.config_dir / (leaf["backends"]["mtlf"] + ".yaml")).resolve()
        held_out = Path(self.manifest["datasets"]["heldOut"]).resolve()
        container_name = "fl-heldout-" + run_id.split("-")[0]
        output = command_output(
            [
                "docker", "run", "--rm", "--name", container_name,
                "--network", "none", "--read-only", "--runtime", "nvidia",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "--env", "NVIDIA_VISIBLE_DEVICES=nvidia.com/gpu=all",
                "--env", "NVIDIA_DRIVER_CAPABILITIES=compute,utility",
                "--label", "io.5g-nwdaf.experiment-run=" + run_id,
                "--mount", "type=bind,source={},target=/eval/config.yaml,readonly".format(config),
                "--mount", "type=bind,source={},target=/eval/{}.tar.gz,readonly".format(
                    artifact.resolve(), artifact_key
                ),
                "--mount", "type=bind,source={},target=/eval/held-out.npz,readonly".format(held_out),
                "--entrypoint", "python", IMAGE,
                "/opt/app/tools/evaluate_image_model.py",
                "--config", "/eval/config.yaml",
                "--artifact-path", "/eval/{}.tar.gz".format(artifact_key),
                "--test-data", "/eval/held-out.npz",
                "--run-id", run_id,
            ],
            timeout=600,
        )
        try:
            value = json.loads(output.splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as error:
            raise BranchReplacementError("held-out evaluator returned invalid JSON") from error
        if value.get("run_id") != run_id or value.get("model_artifact_key") != artifact_key:
            raise BranchReplacementError("held-out evaluation identity mismatch")
        return value

    def reset(self) -> dict:
        scenario = self.manifest["scenario"]["name"]
        environment = dict(os.environ)
        environment["RESET_CONFIRM"] = scenario
        applied = quiet_command(
            [
                str(ROOT / "scripts/host/experiment-reset.sh"), "apply",
                str(self.testbed_path), str(self.config_dir),
            ],
            "experiment-reset",
            timeout=600,
            environment=environment,
        )
        verified = quiet_command(
            [
                str(ROOT / "scripts/host/experiment-reset.sh"), "verify",
                str(self.testbed_path), str(self.config_dir),
            ],
            "experiment-reset-verify",
            timeout=600,
        )
        return {"applied": True, "resetVerified": True, "details": verified.strip()[-1000:]}

    def diagnostics(self, directory: Path, contract: BranchReplacementContract) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        commands = {
            "root-container.log": [
                "docker", "logs", "--tail", "120",
                self._container_id(self.runtime["coordinatorContainer"], running=False),
            ],
            "primary-container.log": [
                "docker", "logs", "--tail", "120",
                self._container_id(contract.primary_service, running=False),
            ],
        }
        for filename, command in commands.items():
            try:
                value = command_output(command, timeout=30, combined=True)
            except BranchReplacementError as error:
                value = str(error)
            (directory / filename).write_text(value + "\n", encoding="utf-8")
        try:
            journal = self._provider_shell(
                r'''source "$1"; select_testbed_machines "$2"; vssh "$3" "sudo journalctl -u 5g-nwdaf@$4.service -n 120 --no-pager"''',
                [
                    str(ROOT / "scripts/host/lib.sh"), str(self.testbed_path),
                    contract.primary_machine, contract.primary_unit,
                ],
                timeout=60,
            )
        except BranchReplacementError as error:
            journal = str(error)
        (directory / "primary-guest.log").write_text(journal + "\n", encoding="utf-8")


def initial_run_record(
    run_name: str,
    request_id: str,
    testbed_path: Path,
    config_dir: Path,
    testbed: dict,
    manifest: dict,
    scenario: dict,
    contract: BranchReplacementContract,
) -> dict:
    operations = testbed["operations"]
    root_service = manifest["runtime"]["coordinatorContainer"]
    root_native = load_yaml(config_dir / (root_service + ".yaml"))
    root_server = root_native["federated_learning"]["server"]
    delay_policy = root_server.get("delay_policy", {})
    root_unit = next(
        item["unit"]
        for item in manifest["runtime"]["nwdafs"]
        if item["role"] == "root"
    )
    root_go = load_yaml(config_dir / ("nwdafcfg-" + root_unit[6:] + ".yaml"))[
        "configuration"
    ]
    return {
        "runName": run_name,
        "requestId": request_id,
        "dataset": scenario["workload"]["dataset"],
        "scenario": manifest["scenario"],
        "startedAt": utc_now(),
        "finishedAt": None,
        "status": "initializing",
        "finalized": False,
        "repositories": repository_metadata(testbed, manifest["runtime"]),
        "selection": {
            "testbed": str(testbed_path.relative_to(ROOT)),
            "configDirectory": str(config_dir.relative_to(ROOT)),
            "configSet": config_dir.name,
        },
        "runtimeInventory": {
            "guestMachines": manifest["runtime"]["guestMachines"],
            "guestServices": manifest["runtime"]["guestServices"],
            "hostContainers": manifest["runtime"]["hostContainers"],
            "ports": manifest["runtime"]["capacity"]["publishedPorts"],
            "volumes": manifest["runtime"]["mlVolumes"],
        },
        "workload": {
            **scenario["partition"],
            **scenario["training"],
            "localEpochs": root_server["client_training"]["epochs"],
        },
        "deadlines": {
            "preparationTimeoutSeconds": operations["preparationTimeoutSeconds"],
            "roundTimeoutSeconds": operations["roundTimeoutSeconds"],
            "mtlfBackendRequestTimeoutSeconds": root_go["mtlfBackend"]["requestTimeout"],
            "delayExtensionPolicy": {
                "maximumExtensions": delay_policy.get("max_extensions", 1),
                "maximumTotalSeconds": delay_policy.get("max_extension_seconds", 300),
            },
        },
        "fault": {
            **scenario["fault"],
            "primary": {
                "nfInstanceId": contract.primary_nf_instance_id,
                "unit": contract.primary_unit,
                "machine": contract.primary_machine,
                "service": contract.primary_service,
            },
            "replacementNfInstanceId": contract.replacement_nf_instance_id,
        },
        "failures": [],
    }


def canonical_uuid4(value: object, field: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as error:
        raise BranchReplacementError(field + " must be a canonical UUIDv4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise BranchReplacementError(field + " must be a canonical UUIDv4")
    return value


def event_payload(writer: EvidenceWriter, source: str, event_type: str) -> dict | None:
    matches = [
        record
        for record in writer.events()
        if record["source"] == source and record["eventType"] == event_type
    ]
    if len(matches) > 1:
        raise BranchReplacementError(
            "events contain duplicate {} {} records".format(source, event_type)
        )
    return matches[0]["payload"] if matches else None


def validate_collection_checkpoint(
    writer: EvidenceWriter,
    run_name: str,
    testbed_path: Path,
    config_dir: Path,
    manifest: dict,
    contract: BranchReplacementContract,
    environment: LiveEnvironment,
) -> tuple[str, str, dict]:
    run = writer.run
    if run.get("status") not in {"collection-pending", "collection-failed"}:
        raise BranchReplacementError(
            "collection-only requires a collection-pending or collection-failed checkpoint"
        )
    if run.get("runName") != run_name or writer.run_directory.name != run_name:
        raise BranchReplacementError("collection checkpoint runName is mismatched")
    request_id = canonical_uuid4(run.get("requestId"), "checkpoint requestId")
    plan_id = canonical_uuid4(run.get("planId"), "checkpoint planId")
    if run.get("mlCorreId") != plan_id:
        raise BranchReplacementError("checkpoint mlCorreId differs from planId")
    if run.get("dataset") != contract.dataset or run.get("scenario") != manifest["scenario"]:
        raise BranchReplacementError("collection checkpoint scenario is not selected")
    selection = run.get("selection", {})
    if selection.get("testbed") != str(testbed_path.relative_to(ROOT)) or selection.get(
        "configDirectory"
    ) != str(config_dir.relative_to(ROOT)):
        raise BranchReplacementError("collection checkpoint config selection is not active")
    terminal = run.get("terminalStatus")
    final_model = run.get("finalModel")
    if (
        not isinstance(terminal, dict)
        or terminal.get("state") != "COMPLETE"
        or not isinstance(final_model, dict)
        or final_model.get("artifactFile") != "final-model.tar.gz"
        or final_model.get("artifactDigest") != terminal.get("candidateDigest")
        or final_model.get("roundInd") != terminal.get("currentRound")
        or not isinstance(final_model.get("sizeBytes"), int)
        or isinstance(final_model.get("sizeBytes"), bool)
        or final_model["sizeBytes"] <= 0
    ):
        raise BranchReplacementError("collection checkpoint final model is incomplete")
    selected_source = environment.final_model_source(plan_id)
    if run.get("finalModelSource") != selected_source:
        raise BranchReplacementError("collection checkpoint Root volume source is mismatched")
    if environment.selected_image_snapshot() != run.get("image"):
        raise BranchReplacementError("collection requires the training PyMTLF image")
    return request_id, plan_id, final_model


def complete_collection(
    writer: EvidenceWriter,
    environment: LiveEnvironment,
    contract: BranchReplacementContract,
) -> int:
    request_id = canonical_uuid4(writer.run.get("requestId"), "checkpoint requestId")
    plan_id = canonical_uuid4(writer.run.get("planId"), "checkpoint planId")
    final_model = writer.run["finalModel"]
    artifact_key = final_model["artifactDigest"]
    expected_size = final_model["sizeBytes"]
    artifact = writer.run_directory / "final-root-model.tar.gz"

    collected = event_payload(writer, "controller", "FINAL_ARTIFACT_COLLECTED")
    if collected is None:
        environment.copy_final_artifact(plan_id, expected_size, artifact)
        collected = {
            "artifactKey": artifact_key,
            "path": artifact.name,
            "sizeBytes": artifact.stat().st_size,
        }
        writer.append_once(
            "controller",
            "FINAL_ARTIFACT_COLLECTED",
            collected,
            nf_instance_id=contract.root_nf_instance_id,
        )
    if (
        collected.get("artifactKey") != artifact_key
        or collected.get("path") != artifact.name
        or collected.get("sizeBytes") != expected_size
        or not artifact.is_file()
        or artifact.stat().st_size != expected_size
    ):
        raise BranchReplacementError("collected final artifact differs from checkpoint")
    artifact.chmod(0o644)

    protocol_resources = event_payload(
        writer, "controller", "PROTOCOL_RESOURCE_EVIDENCE"
    )
    if protocol_resources is None:
        protocol_resources = environment.collect_protocol_evidence(
            writer.run_directory / "diagnostics" / "protocol-resources",
            contract,
            plan_id,
            writer.run["startedAt"],
        )
        writer.append_once(
            "controller",
            "PROTOCOL_RESOURCE_EVIDENCE",
            protocol_resources,
            nf_instance_id=contract.root_nf_instance_id,
        )
    if protocol_resources.get("planId") != plan_id:
        raise BranchReplacementError("protocol resource evidence has the wrong planId")
    writer.update(
        protocolResources=protocol_resources,
        finalArtifact=artifact.name,
        finalArtifactIdentity=artifact_key,
        finalArtifactSizeBytes=expected_size,
    )

    process_cleanup = writer.run.get("processCleanup")
    if not isinstance(process_cleanup, dict) or process_cleanup.get(
        "processesStopped"
    ) is not True:
        process_cleanup = environment.stop_all()
        writer.update(processCleanup=process_cleanup)

    held_out = event_payload(writer, "held-out-evaluator", "HELD_OUT_EVALUATION")
    if held_out is None:
        held_out = environment.evaluate(request_id, artifact_key, artifact)
        writer.append_once("held-out-evaluator", "HELD_OUT_EVALUATION", held_out)
    if held_out.get("run_id") != request_id:
        raise BranchReplacementError("held-out evaluation request identity is mismatched")
    writer.update(heldOutEvaluation=held_out)

    check_evidence(writer.run_directory, contract, require_cleanup=False)

    cleanup = event_payload(writer, "controller", "CLEANUP_COMPLETE")
    if cleanup is None:
        cleanup = {**process_cleanup, **environment.reset()}
        writer.append_once("controller", "CLEANUP_COMPLETE", cleanup)
    writer.update(
        status="successful",
        finalized=True,
        finishedAt=utc_now(),
        cleanup=cleanup,
    )
    try:
        check_evidence(writer.run_directory, contract)
    except BaseException:
        writer.update(status="collection-failed", finalized=False, finishedAt=None)
        raise
    print(
        "MILESTONE run-complete dataset={} accepted={} degraded={} restored={}".format(
            contract.dataset,
            contract.accepted_rounds,
            writer.run["phases"]["phaseCounts"]["degraded"],
            writer.run["phases"]["phaseCounts"]["restored"],
        ),
        flush=True,
    )
    return 0


def run_collection_only(
    writer: EvidenceWriter,
    run_name: str,
    testbed_path: Path,
    config_dir: Path,
    manifest: dict,
    contract: BranchReplacementContract,
    environment: LiveEnvironment,
) -> int:
    validate_collection_checkpoint(
        writer,
        run_name,
        testbed_path,
        config_dir,
        manifest,
        contract,
        environment,
    )
    return complete_collection(writer, environment, contract)


def record_run_failure(writer: EvidenceWriter, error: BaseException) -> None:
    failures = list(writer.run.get("failures", []))
    failures.append(
        {
            "recordedAt": utc_now(),
            "detail": str(error) or error.__class__.__name__,
        }
    )
    terminal_checkpoint = writer.run.get("terminalStatus")
    collection_retryable = (
        writer.run.get("status") in {"collection-pending", "collection-failed"}
        and isinstance(writer.run.get("finalModel"), dict)
        and isinstance(terminal_checkpoint, dict)
        and terminal_checkpoint.get("state") == "COMPLETE"
    )
    writer.update(
        status="collection-failed" if collection_retryable else "failed",
        finalized=False if collection_retryable else True,
        finishedAt=None if collection_retryable else utc_now(),
        failures=failures,
    )


def ensure_runtime_stopped_after_failure(
    writer: EvidenceWriter,
    environment: LiveEnvironment,
    runtime_started: bool,
) -> None:
    if not runtime_started:
        return
    process_cleanup = writer.run.get("processCleanup")
    if isinstance(process_cleanup, dict) and process_cleanup.get(
        "processesStopped"
    ) is True:
        return
    writer.update(processCleanup=environment.stop_all())


def validate_source_image_revision(image: dict, expected_revision: str) -> None:
    if image.get("revision") != expected_revision:
        raise BranchReplacementError(
            "PyMTLF image revision differs from the selected source revision"
        )


def validate_runtime_image(
    runtime_image: dict,
    selected_image: dict,
    expected_revision: str,
) -> None:
    validate_source_image_revision(selected_image, expected_revision)
    if runtime_image != selected_image:
        raise BranchReplacementError(
            "running PyMTLF containers do not use the post-build selected image"
        )


def run(args: argparse.Namespace) -> int:
    run_name = validate_run_name(args.run_name)
    testbed_path = resolve_path(args.testbed).resolve()
    testbed = load_yaml(testbed_path)
    config_dir = resolve_config_dir(testbed, args.config_dir or None).resolve()
    manifest = load_runtime_manifest(config_dir)
    _scenario_path, scenario = resolve_config_scenario(config_dir)
    image_scenario_contract(scenario)
    if manifest["scenario"].get("fault") != scenario.get("fault"):
        raise BranchReplacementError("generated fault contract differs from scenario")
    if manifest["runtime"].get("deploymentKind") != "protocol-hierarchical":
        raise BranchReplacementError("runner requires protocol-hierarchical deployment")
    if manifest["runtime"].get("mlDevicePolicy") != "gpu":
        raise BranchReplacementError("Branch replacement acceptance requires DEVICE=gpu")
    if manifest["runtime"]["capacity"].get("gpuParticipants") != 7:
        raise BranchReplacementError("generated runtime must select seven GPU participants")
    contract = BranchReplacementContract.build(testbed, scenario)
    run_directory = (
        ROOT
        / "runs"
        / "protocol-hierarchical"
        / scenario["workload"]["dataset"]
        / run_name
    )
    lock_path = ROOT / ".generated" / "run-locks" / "protocol-hierarchical.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    environment = LiveEnvironment(testbed_path, config_dir, manifest)
    writer = None
    runtime_started = False
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BranchReplacementError("another protocol experiment runner is active") from error
        if args.collect_only:
            writer = EvidenceWriter.open_existing(run_directory)
            if writer.run.get("status") not in {
                "collection-pending",
                "collection-failed",
            }:
                raise BranchReplacementError(
                    "collection-only requires a collection-pending or collection-failed checkpoint"
                )
        else:
            request_id = str(uuid.uuid4())
            writer = EvidenceWriter(
                run_directory,
                initial_run_record(
                    run_name,
                    request_id,
                    testbed_path,
                    config_dir,
                    testbed,
                    manifest,
                    scenario,
                    contract,
                ),
            )
        try:
            if args.collect_only:
                return run_collection_only(
                    writer,
                    run_name,
                    testbed_path,
                    config_dir,
                    manifest,
                    contract,
                    environment,
                )

            virtual_machines = environment.validate_inputs()
            prestart_gpu = environment.gpu_snapshot()
            minimum_gpu = manifest["runtime"]["capacity"]["minimumGpuMemoryMiB"]
            if prestart_gpu["memoryFreeMiB"] < minimum_gpu:
                raise BranchReplacementError("GPU free memory is below the pre-start floor")
            source_image = environment.selected_image_snapshot()
            expected_image_revision = writer.run["repositories"]["ML/PyMTLF"][
                "revision"
            ]
            validate_source_image_revision(source_image, expected_image_revision)
            writer.append(
                "controller", "GPU_ADMISSION",
                {**prestart_gpu, "minimumFreeMiB": minimum_gpu, "participantCount": 7},
            )
            writer.update(gpuAdmission={**prestart_gpu, "minimumFreeMiB": minimum_gpu})
            writer.update(status="starting")
            environment.start()
            runtime_started = True
            active_identity = environment.active_guest_identity()
            guest_services = environment.guest_runtime_snapshot()
            registrations = environment.registration_snapshot()
            snapshot = environment.runtime_snapshot()
            selected_image = environment.selected_image_snapshot()
            validate_runtime_image(
                snapshot["image"], selected_image, expected_image_revision
            )
            runtime_ready = {
                **snapshot,
                "virtualMachines": virtual_machines,
                "guestServices": guest_services,
                "activeConfig": active_identity,
                "nrfRegistrations": registrations,
            }
            writer.append("controller", "RUNTIME_READY", runtime_ready)
            writer.update(
                status="running",
                activeIdentity={
                    "config": active_identity,
                    "guestServices": guest_services,
                    "containers": snapshot["containers"],
                },
                image=snapshot["image"],
                gpu=snapshot["gpu"],
                resolvedDevices={
                    name: value["device"] for name, value in snapshot["containers"].items()
                },
            )

            fl_contract = FL_CONTROL.load_contract(str(testbed_path), str(config_dir))
            FL_CONTROL.verify_runtime_identity(fl_contract)
            controller = FL_CONTROL.Controller(
                fl_contract,
                FL_CONTROL.HttpClient(timeout=30),
                poll_seconds=contract.poll_seconds,
            )
            request_started_at = utc_now()
            status = controller.training_start(request_id, None)
            writer.append(
                "controller",
                "TRAINING_REQUEST_SUBMITTED",
                {"requestId": request_id},
                recorded_at=request_started_at,
                nf_instance_id=contract.root_nf_instance_id,
            )
            plan_id = status.get("planId")
            deadline = time.monotonic() + fl_contract.closure_budget_seconds
            while not plan_id and time.monotonic() < deadline:
                time.sleep(contract.poll_seconds)
                status = controller.training_status(request_id)
                plan_id = status.get("planId")
            if not isinstance(plan_id, str):
                raise BranchReplacementError("training resource did not expose planId")
            writer.update(planId=plan_id, mlCorreId=plan_id, trainingRequest=status)
            tracker = PhaseTracker(contract, plan_id)
            reader = environment.root_observation_reader(plan_id)
            last_heartbeat = time.monotonic()
            fault_injected = False
            terminal = None
            while time.monotonic() < deadline:
                for record in reader.poll():
                    writer.append_root(record)
                    transition = tracker.ingest(record)
                    if transition == "round-accepted":
                        latest = tracker.accepted[-1]
                        print(
                            "MILESTONE accepted={} attempt={} phase={} degraded={}".format(
                                len(tracker.accepted), latest["roundInd"], latest["phase"],
                                tracker.summary()["phaseCounts"]["degraded"],
                            ),
                            flush=True,
                        )
                        writer.update(phases=tracker.summary())
                    elif transition:
                        print("MILESTONE " + transition, flush=True)
                status = controller.training_status(request_id)
                if status.get("state") == "FAILED":
                    raise BranchReplacementError(
                        "training failed: {} {}".format(
                            status.get("failureCause"), status.get("failureDetail")
                        )
                    )
                if not fault_injected and tracker.ready_for_fault(status):
                    fail_stop = environment.fail_stop_primary(contract)
                    stopped_at = utc_now()
                    tracker.mark_stopped(stopped_at)
                    stop_payload = {
                        "nfInstanceId": contract.primary_nf_instance_id,
                        "guestStopped": True,
                        "containerStopped": True,
                        **fail_stop,
                    }
                    writer.append(
                        "controller", "BRANCH_PROCESS_STOPPED", stop_payload,
                        recorded_at=stopped_at, nf_instance_id=contract.primary_nf_instance_id,
                    )
                    writer.update(primaryStoppedAt=stopped_at)
                    fault_injected = True
                if status.get("state") == "COMPLETE":
                    evidence_deadline = min(deadline, time.monotonic() + 30)
                    while (
                        tracker.final_model_saved is None
                        and time.monotonic() < evidence_deadline
                    ):
                        records = reader.poll()
                        for record in records:
                            writer.append_root(record)
                            tracker.ingest(record)
                        if tracker.final_model_saved is None:
                            time.sleep(contract.poll_seconds)
                    reader.finish()
                    terminal = status
                    phases = tracker.finalize(status)
                    writer.update(
                        status="collection-pending",
                        finalized=False,
                        phases=phases,
                        terminalStatus=status,
                        finalModel=tracker.final_model_saved,
                        finalModelSource=environment.final_model_source(plan_id),
                    )
                    break
                if time.monotonic() - last_heartbeat >= contract.heartbeat_seconds:
                    phase_counts = tracker.summary()["phaseCounts"]
                    print(
                        "HEARTBEAT accepted={} state={} current_round={} degraded={} elapsed={}s".format(
                            len(tracker.accepted), status.get("state"),
                            status.get("currentRound"), phase_counts["degraded"],
                            int(fl_contract.closure_budget_seconds - (deadline - time.monotonic())),
                        ),
                        flush=True,
                    )
                    last_heartbeat = time.monotonic()
                time.sleep(contract.poll_seconds)
            if terminal is None:
                raise BranchReplacementError("training did not complete within the configured budget")

            return complete_collection(writer, environment, contract)
        except BaseException as error:
            if writer is not None:
                record_run_failure(writer, error)
                failures = list(writer.run.get("failures", []))
                try:
                    environment.diagnostics(run_directory / "diagnostics", contract)
                except Exception as diagnostic_error:
                    failures.append(
                        {"recordedAt": utc_now(), "detail": "diagnostics: " + str(diagnostic_error)}
                    )
                    writer.update(failures=failures)
            if writer is not None:
                try:
                    ensure_runtime_stopped_after_failure(
                        writer, environment, runtime_started
                    )
                except Exception as cleanup_error:
                    failures = list(writer.run.get("failures", []))
                    failures.append(
                        {"recordedAt": utc_now(), "detail": "cleanup: " + str(cleanup_error)}
                    )
                    writer.update(failures=failures)
            raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--testbed", required=True)
    parser.add_argument("--config-dir", default="")
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--collect-only", action="store_true")
    args = parser.parse_args()
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def terminate(_signum, _frame):
        raise BranchReplacementError("runner interrupted by SIGTERM")

    signal.signal(signal.SIGTERM, terminate)
    try:
        return run(args)
    except (BranchReplacementError, ValueError, OSError, subprocess.SubprocessError) as error:
        print("ERROR {}".format(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ERROR runner interrupted by keyboard", file=sys.stderr)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    sys.exit(main())
