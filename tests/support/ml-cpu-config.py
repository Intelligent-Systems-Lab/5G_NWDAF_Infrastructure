#!/usr/bin/env python3
"""Create a disposable CPU-only ML config set from a validated full config set."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "host"))

from configlib import ROOT, dump_yaml, load_yaml, resolve_path, sha256_tree


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="config/default")
    parser.add_argument(
        "--output", default=".generated/tests/config/ml-container-test"
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    source = resolve_path(args.source)
    output = resolve_path(args.output)
    generated_root = (ROOT / ".generated" / "tests" / "config").resolve()
    if output.resolve().parent != generated_root:
        raise SystemExit(
            "output must be a direct child of .generated/tests/config"
        )
    if output.exists():
        if not args.force:
            raise SystemExit("output exists; pass --force to replace it: {}".format(output))
        shutil.rmtree(str(output))
    source_manifest = load_yaml(source / "manifest.yaml")
    topology = source_manifest["topology"]
    topology_definition = topology["definition"] if isinstance(topology, dict) else topology
    subprocess.run(
        [
            sys.executable, str(ROOT / "scripts/host/config-render.py"),
            "--testbed", topology_definition,
            "--name", output.name,
            "--scenario", source_manifest["scenario"]["definition"],
            "--output-root", str(output.parent),
            "--ml-device", "cpu", "--webconsole", "false",
        ],
        cwd=ROOT, check=True, stdout=subprocess.DEVNULL,
    )

    for name in ("pymtlf-a.yaml", "pymtlf-b.yaml"):
        config = load_yaml(output / name)
        config["federated_learning"]["client"]["training"]["device"] = "cpu"
        dump_yaml(output / name, config)

    for name in ("pyanlf-a.yaml", "pyanlf-b.yaml"):
        config = load_yaml(output / name)
        config["mongodb"]["enabled"] = False
        dump_yaml(output / name, config)

    manifest = load_yaml(output / "manifest.yaml")
    manifest["smoke"] = {
        "purpose": "cpu-container-health",
        "sourceConfigHash": sha256_tree(source),
        "deviceOverrides": {"pymtlf-a": "cpu", "pymtlf-b": "cpu"},
        "persistenceOverrides": {"pyanlf-a.mongodb": False, "pyanlf-b.mongodb": False},
    }
    dump_yaml(output / "manifest.yaml", manifest)
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
