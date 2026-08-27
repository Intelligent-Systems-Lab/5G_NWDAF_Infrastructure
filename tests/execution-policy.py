#!/usr/bin/env python3
"""Prove diagnostics stay explicit while selected capacity gates ML start."""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "host"))

from configlib import dump_yaml, load_yaml
from datasetlib import resolve_dataset_spec


DIRECT_PROVIDER = re.compile(
    r"(?:^|[;&|()])\s*@?(?:[A-Za-z_][A-Za-z0-9_]*=[^;&|()\s]+\s+)*"
    r"(?:(?:command|exec|nohup|sudo)\s+)*(?P<quote>[\"']?)"
    r"(?:[^=\s;&|()<>\"']*/)?(?:vagrant|VBoxManage)"
    r"(?![A-Za-z0-9_.-])(?P=quote)",
    re.IGNORECASE,
)


def reject_startup_gate(relative, forbidden):
    source = (ROOT / relative).read_text(encoding="utf-8")
    for token in forbidden:
        if token in source:
            raise SystemExit("{} still invokes diagnostic gate {!r}".format(relative, token))


def reject_unguarded_provider_calls():
    for fixture in (
        "vagrant status",
        '@TESTBED="testbed.yaml" vagrant up',
        '(cd "$HOST_ROOT" && VBoxManage list vms)',
        "sudo /usr/bin/vagrant halt",
        '"/usr/bin/VBoxManage" list vms',
    ):
        if not DIRECT_PROVIDER.search(fixture):
            raise SystemExit("direct-provider detector missed: " + fixture)
    for fixture in (
        "provider_vagrant status",
        "provider_vboxmanage list vms",
        "command -v vagrant",
    ):
        if DIRECT_PROVIDER.search(fixture):
            raise SystemExit("direct-provider detector rejected guarded/reference path: " + fixture)

    paths = [ROOT / "Makefile"]
    paths.extend(sorted((ROOT / "scripts").rglob("*.sh")))
    paths.extend(sorted((ROOT / "tests").rglob("*.sh")))
    allowed = {
        ("scripts/host/lib.sh", 'command vagrant "$@" 9>&-'),
        ("scripts/host/lib.sh", 'command VBoxManage "$@"'),
    }
    findings = []
    for path in paths:
        relative = str(path.relative_to(ROOT))
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if (relative, stripped) in allowed:
                continue
            if DIRECT_PROVIDER.search(line):
                findings.append("{}:{}:{}".format(relative, line_number, stripped))
    if findings:
        raise SystemExit("unguarded provider command paths:\n" + "\n".join(findings))


def main():
    reject_unguarded_provider_calls()
    make_source = (ROOT / "Makefile").read_text(encoding="utf-8")
    if 'TESTBED="$(TESTBED)" provider_vagrant_up' not in make_source:
        raise SystemExit("vm-up must use the duplicate/orphan runtime preflight wrapper")
    reject_startup_gate("scripts/host/experiment-start.sh", ("experiment-validate.sh",))
    reject_startup_gate("scripts/host/services-start.sh", ("config-check.py",))
    reject_startup_gate(
        "scripts/host/ml-start.sh",
        ("config-check.py", "ml-compose-check.py"),
    )
    ml_start_source = (ROOT / "scripts" / "host" / "ml-start.sh").read_text(
        encoding="utf-8"
    )
    if (
        'ml_compose up --detach --no-build --wait --wait-timeout 240 "${ml_services[@]}" || rollback "$?"'
        not in ml_start_source
    ):
        raise SystemExit("ML startup must explicitly stop its project after Compose wait failure")
    if 'ml_host_resource_gate "$testbed" "$config_dir"' not in ml_start_source:
        raise SystemExit("ML startup lost the selected runtime capacity gate")
    if 'assert_ml_runtime_identity "$testbed" "$config_dir" start' not in ml_start_source:
        raise SystemExit("ML startup lost the start-safe selected/actual identity guard")
    logs_source = (ROOT / "scripts" / "host" / "logs.sh").read_text(
        encoding="utf-8"
    )
    if 'assert_ml_runtime_identity "$testbed" "$config_dir"' not in logs_source:
        raise SystemExit("ML logs lost the selected/actual identity guard")
    if "< <(vm_log_sources" in logs_source or "< <(\n    docker ps" in logs_source:
        raise SystemExit("log inventory readers can hide subprocess failure")
    reject_startup_gate("scripts/host/webconsole-start.sh", ("config-check.py",))
    reject_startup_gate("scripts/host/subscriber-data.sh", ("config-check.py",))

    reset_source = (ROOT / "scripts" / "host" / "experiment-reset.sh").read_text(
        encoding="utf-8"
    )
    if "config-check.py" not in reset_source:
        raise SystemExit("destructive reset lost its exact-scope config validation")
    if "< <(" in reset_source:
        raise SystemExit("reset inventory readers can hide subprocess failure")

    preflight_source = (ROOT / "scripts" / "host" / "preflight.sh").read_text(
        encoding="utf-8"
    )
    if "hard RAM gate" in preflight_source:
        raise SystemExit("preflight still describes advisory RAM guidance as a gate")

    testbed = load_yaml(ROOT / "testbed.yaml")
    with tempfile.TemporaryDirectory(prefix="5g-execution-policy-") as temporary:
        temporary_root = Path(temporary)
        config_dir = temporary_root / "config"
        output_root = temporary_root / "datasets"
        shutil.copytree(ROOT / "config" / "default", config_dir)
        anlf_path = config_dir / "pyanlf-a.yaml"
        anlf = load_yaml(anlf_path)
        anlf["accuracy_monitor"]["min_matched_predictions"] = 4
        dump_yaml(anlf_path, anlf)

        check = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "host" / "config-check.py"),
                "--testbed",
                str(ROOT / "testbed.yaml"),
                "--config-dir",
                str(config_dir),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        evidence = check.stdout + check.stderr
        expected = "monitor period cannot collect the required matched predictions"
        if check.returncode == 0 or expected not in evidence:
            raise SystemExit("config diagnostic did not report the intentional mismatch")

        diagnostics = []
        spec = resolve_dataset_spec(testbed, config_dir, diagnostics=diagnostics)
        if not any(expected in item for item in diagnostics):
            raise SystemExit("dataset contract did not expose the intentional mismatch")

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "host" / "dataset.py"),
                "--testbed",
                str(ROOT / "testbed.yaml"),
                "--config-dir",
                str(config_dir),
                "--output-root",
                str(output_root),
                "generate",
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        artifact = output_root / spec["datasetSetId"] / "path-a" / "traffic.parquet"
        if not artifact.is_file():
            raise SystemExit("diagnostic mismatch prevented dataset generation")

        capacity_testbed = temporary_root / "capacity-testbed.yaml"
        constrained = load_yaml(ROOT / "testbed.yaml")
        constrained["hostSafety"]["reserveMemoryMiB"] = 999999999
        dump_yaml(capacity_testbed, constrained)
        capacity = subprocess.run(
            [
                "bash", "-c",
                'source "$1"; docker() { [ "$1" = info ] && printf "/tmp\\n"; }; '
                'ml_host_resource_gate "$2" "$3"',
                "capacity-gate", str(ROOT / "scripts" / "host" / "lib.sh"),
                str(capacity_testbed), str(ROOT / "config" / "default"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        capacity_evidence = capacity.stdout + capacity.stderr
        if capacity.returncode == 0 or "below selected Host requirement" not in capacity_evidence:
            raise SystemExit("selected runtime capacity shortage was not rejected")

    print("EXECUTION_POLICY_TEST status=passed diagnostic=reported generation=allowed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
