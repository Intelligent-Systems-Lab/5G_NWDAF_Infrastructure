#!/usr/bin/env python3
"""Build, audit, and locate topology-derived PseudoDriver datasets."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from configlib import (
    ROOT, load_yaml, resolve_config_dir, resolve_config_scenario, resolve_path,
    scenario_profile,
)
from datasetlib import canonical_bytes, resolve_dataset_spec


DEFAULT_OUTPUT = ROOT / ".generated" / "datasets"
TOOL_DIR = ROOT / "tools" / "datasetgen"
TOOL_CACHE = ROOT / ".generated" / "tools"


def command_spec(args):
    testbed = load_yaml(resolve_path(args.testbed))
    config_dir = resolve_config_dir(testbed, args.config_dir)
    return testbed, config_dir, resolve_dataset_spec(testbed, config_dir)


def binary_path(spec):
    return TOOL_CACHE / ("datasetgen-" + spec["generatorSourceHash"][:16])


def build_tool(spec):
    binary = binary_path(spec)
    if binary.is_file():
        for stale in TOOL_CACHE.glob("datasetgen-*"):
            if stale != binary and stale.is_file():
                stale.unlink()
        return binary
    TOOL_CACHE.mkdir(parents=True, exist_ok=True)
    temporary = binary.with_suffix(".tmp-{}".format(os.getpid()))
    try:
        subprocess.run(
            ["go", "build", "-trimpath", "-ldflags=-s -w", "-o", str(temporary), "."],
            cwd=TOOL_DIR,
            check=True,
        )
        os.replace(temporary, binary)
        for stale in TOOL_CACHE.glob("datasetgen-*"):
            if stale != binary and stale.is_file():
                stale.unlink()
    finally:
        temporary.unlink(missing_ok=True)
    return binary


def require_tool(spec):
    binary = binary_path(spec)
    if not binary.is_file():
        raise SystemExit("dataset generator is not built; run make dataset-generate")
    return binary


def run_tool(binary, action, spec, root, quiet=False):
    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as stream:
        stream.write(canonical_bytes(spec))
        spec_path = Path(stream.name)
    try:
        subprocess.run(
            [str(binary), action, str(spec_path), str(root)],
            check=True,
            stdout=subprocess.DEVNULL if quiet else None,
        )
    finally:
        spec_path.unlink(missing_ok=True)


def dataset_root(output_root, spec):
    return Path(output_root).resolve() / spec["datasetSetId"]


def generate(args, spec):
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    destination = dataset_root(output_root, spec)
    binary = build_tool(spec)
    if destination.exists():
        run_tool(binary, "check", spec, destination)
        print("REUSE dataset={} root={}".format(spec["datasetSetId"], destination))
        return destination
    temporary = Path(tempfile.mkdtemp(prefix=".dataset-", dir=output_root))
    try:
        run_tool(binary, "generate", spec, temporary)
        (temporary / "resolved-spec.json").write_bytes(canonical_bytes(spec) + b"\n")
        run_tool(binary, "check", spec, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    print("GENERATED dataset={} root={}".format(spec["datasetSetId"], destination))
    return destination


def check(args, spec, quiet=False):
    explicit_root = getattr(args, "root", None)
    root = Path(explicit_root).resolve() if explicit_root else dataset_root(args.output_root, spec)
    if not root.is_dir():
        raise SystemExit("generated dataset is missing: {}; run make dataset-generate".format(root))
    run_tool(require_tool(spec), "check", spec, root, quiet=quiet)
    return root


def _byte_size(value):
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return "{:.0f}{}".format(size, unit) if unit == "B" else "{:.1f}{}".format(size, unit)
        size /= 1024


def _ratio(numerator, denominator):
    if denominator and numerator % denominator == 0:
        return str(numerator // denominator)
    return "non-integral({}/{})".format(numerator, denominator)


def render_summary(spec, manifest, root):
    scenario = spec["scenario"]
    lines = [
        "DATASET id={} scenario={} kind={} warm_start={}".format(
            spec["datasetSetId"],
            scenario["name"],
            scenario["kind"],
            scenario["warmStartMode"],
        ),
        "SOURCE definition={} root={}".format(scenario["definition"], root),
    ]
    for path_name in ("path-a", "path-b"):
        profile = spec["paths"][path_name]
        artifact = manifest["paths"][path_name]
        raw_window = profile["windowSeconds"]
        sampling = profile["samplingIntervalSeconds"]
        report_period = profile["monitorReportPeriodSeconds"]
        warm_start = profile["breakingTimeSeconds"]
        transition = profile["stableWindows"] * raw_window
        total_duration = (
            profile["stableWindows"] + profile["degradedWindows"]
        ) * raw_window
        lines.extend(
            [
                "",
                "PATH {} mode={} ues={} rows={} size={} sha256={}".format(
                    path_name,
                    profile["postBoundaryMode"],
                    len(artifact["ueIps"]),
                    artifact["rows"],
                    _byte_size(artifact["bytes"]),
                    artifact["sha256"],
                ),
                "  artifact={} timestamps={}..{}s duration={}s".format(
                    profile["artifactFile"],
                    artifact["minTimestamp"],
                    artifact["maxTimestamp"],
                    total_duration,
                ),
                "  aggregation raw_window={}s observation={}s raw_windows_per_observation={}".format(
                    raw_window,
                    sampling,
                    _ratio(sampling, raw_window),
                ),
                "  reporting period={}s observations_per_report={} minimum_matched={}".format(
                    report_period,
                    _ratio(report_period, sampling),
                    profile["minimumMatchedPredictions"],
                ),
                "  timeline warm_start_boundary={}s traffic_transition={}s live_stable_lead={}s post_duration={}s".format(
                    warm_start,
                    transition,
                    profile["stableLeadInSeconds"],
                    profile["degradedTailSeconds"],
                ),
                "  model input_observations={} output_observations={} warm_start_observations={}".format(
                    profile["modelInputWindow"],
                    profile["modelOutputWindow"],
                    profile["historicalObservations"],
                ),
                "  trigger earliest_after_start={}s bounded_after_start={}s bounded_closure={}s".format(
                    profile["earliestTriggerSeconds"],
                    profile["boundedTriggerSeconds"],
                    profile["boundedClosureSeconds"],
                ),
                "  trigger_samples observations={} training={} validation={} admission_minimum={}".format(
                    profile["triggerObservations"],
                    profile["triggerTrainingSamples"],
                    profile["triggerValidationSamples"],
                    profile["minimumAdmissionTrainingSamples"],
                ),
            ]
        )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--testbed", required=True)
    parser.add_argument("--config-dir")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("generate")
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--root")
    subparsers.add_parser("show")
    locate_parser = subparsers.add_parser("locate")
    locate_parser.add_argument("--path", choices=("a", "b"))
    args = parser.parse_args()

    try:
        testbed = load_yaml(resolve_path(args.testbed))
        config_dir = resolve_config_dir(testbed, args.config_dir)
        _scenario_path, scenario = resolve_config_scenario(config_dir)
        if scenario_profile(scenario) == "image-classification":
            if args.action == "locate":
                raise ValueError("dataset locate is only available for UE communication datasets")
            interpreter = ROOT / "ML" / "PyMTLF" / ".venv" / "bin" / "python"
            if not interpreter.is_file():
                raise ValueError("PyMTLF project environment is missing: {}".format(interpreter))
            command = [
                str(interpreter),
                str(ROOT / "scripts" / "host" / "image_dataset.py"),
                "--testbed",
                args.testbed,
            ]
            if args.config_dir:
                command.extend(["--config-dir", args.config_dir])
            command.append(args.action)
            return subprocess.run(command, check=False).returncode
        _testbed, _config_dir, spec = command_spec(args)
        if args.action == "generate":
            generate(args, spec)
        elif args.action == "check":
            check(args, spec)
        elif args.action == "show":
            root = check(args, spec, quiet=True)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            print(render_summary(spec, manifest, root), end="")
        else:
            root = check(args, spec, quiet=True)
            print(spec["datasetSetId"])
            print(root / ("path-" + args.path) if args.path else root)
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
