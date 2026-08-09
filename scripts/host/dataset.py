#!/usr/bin/env python3
"""Build, audit, and locate topology-derived PseudoDriver datasets."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from configlib import ROOT, load_yaml, resolve_config_dir, resolve_path
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--testbed", default="testbed.yaml")
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
        _testbed, _config_dir, spec = command_spec(args)
        if args.action == "generate":
            generate(args, spec)
        elif args.action == "check":
            check(args, spec)
        elif args.action == "show":
            root = check(args, spec, quiet=True)
            print((root / "manifest.json").read_text(encoding="utf-8"), end="")
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
