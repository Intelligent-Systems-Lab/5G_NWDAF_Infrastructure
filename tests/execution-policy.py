#!/usr/bin/env python3
"""Protect provider isolation and destructive/runtime safety entrypoints."""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "host"))

from configlib import expected_runtime_inventory  # noqa: E402
DIRECT_PROVIDER = re.compile(
    r"(?:^|[;&|()])\s*@?(?:[A-Za-z_][A-Za-z0-9_]*=[^;&|()\s]+\s+)*"
    r"(?:(?:command|exec|nohup|sudo)\s+)*(?P<quote>[\"']?)"
    r"(?:[^=\s;&|()<>\"']*/)?(?:vagrant|VBoxManage)"
    r"(?![A-Za-z0-9_.-])(?P=quote)",
    re.IGNORECASE,
)


def provider_calls_are_guarded():
    paths = [ROOT / "Makefile"]
    paths.extend(sorted((ROOT / "scripts").rglob("*.sh")))
    paths.extend(sorted((ROOT / "tests").rglob("*.sh")))
    allowed = {
        ("scripts/host/lib.sh", 'command vagrant "$@" 9>&-'),
        ("scripts/host/lib.sh", 'command VBoxManage "$@"'),
    }
    findings = []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if (relative, line.strip()) in allowed:
                continue
            if DIRECT_PROVIDER.search(line):
                findings.append("{}:{}:{}".format(relative, line_number, line.strip()))
    if findings:
        raise AssertionError("unguarded provider command paths:\n" + "\n".join(findings))


def lifecycle_keeps_safety_gates():
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert 'TESTBED="$(TESTBED)" provider_vagrant_up' in make
    assert 'TESTBED="$(TESTBED)" provider_vagrant_halt' in make

    ml_start = (ROOT / "scripts/host/ml-start.sh").read_text(encoding="utf-8")
    assert 'assert_ml_runtime_identity "$testbed" "$config_dir" start' in ml_start
    assert 'ml_host_resource_gate "$testbed" "$config_dir"' in ml_start

    reset = (ROOT / "scripts/host/experiment-reset.sh").read_text(encoding="utf-8")
    assert "config-check.py" in reset
    assert "check_reset_runtime_inventory" in reset


def capacity_shortage_is_rejected():
    testbed = yaml.safe_load(
        (ROOT / "testbed.protocol-hierarchical.yaml").read_text(encoding="utf-8")
    )
    scenario = yaml.safe_load(
        (ROOT / "experiments/protocol-hierarchical/mnist/smoke.yaml").read_text(
            encoding="utf-8"
        )
    )
    testbed["hostSafety"]["reserveMemoryMiB"] = 999_999_999
    with tempfile.TemporaryDirectory(prefix="5g-capacity-gate-") as temporary:
        temporary = Path(temporary)
        selected_testbed = temporary / "testbed.yaml"
        config_dir = temporary / "config"
        config_dir.mkdir()
        selected_testbed.write_text(
            yaml.safe_dump(testbed, sort_keys=False), encoding="utf-8"
        )
        (config_dir / "manifest.yaml").write_text(
            yaml.safe_dump(
                {"runtime": expected_runtime_inventory(testbed, scenario)},
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; docker() { printf "/tmp\\n"; }; '
                'ml_host_resource_gate "$2" "$3"',
                "capacity-gate",
                str(ROOT / "scripts" / "host" / "lib.sh"),
                str(selected_testbed),
                str(config_dir),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    evidence = result.stdout + result.stderr
    if result.returncode == 0 or "below selected Host requirement" not in evidence:
        raise AssertionError("selected runtime capacity shortage was not rejected")


def main():
    provider_calls_are_guarded()
    lifecycle_keeps_safety_gates()
    capacity_shortage_is_rejected()
    print(
        "EXECUTION_POLICY_TEST provider=guarded lifecycle=safety-gated "
        "capacity=rejected status=passed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
