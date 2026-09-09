#!/usr/bin/env python3
"""Create a disposable CPU-only ML config set from a validated full config set."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "host"))

from configlib import ROOT, dump_yaml, load_yaml, resolve_path  # noqa: E402


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

    manifest = load_yaml(output / "manifest.yaml")
    services = manifest["runtime"]["hostContainers"]
    device_overrides = {}
    persistence_overrides = {}
    smoke_services = {}
    for name in services:
        config_path = output / (name + ".yaml")
        config = load_yaml(config_path)
        if name.startswith("pymtlf-"):
            client = config.get("federated_learning", {}).get("client")
            if isinstance(client, dict):
                client["training"]["device"] = "cpu"
                device_overrides[name] = "cpu"
            smoke_services[name] = {
                "volumes": [
                    {
                        "type": "bind",
                        "source": "${REPOSITORY_ROOT:?REPOSITORY_ROOT must be set}"
                        "/tests/support/pymtlf-smoke-health.py",
                        "target": "/opt/app/pymtlf-smoke-health.py",
                        "read_only": True,
                    }
                ],
                "healthcheck": {
                    "test": ["CMD", "python", "/opt/app/pymtlf-smoke-health.py"]
                },
            }
        elif name.startswith("pyanlf-"):
            config["mongodb"]["enabled"] = False
            persistence_overrides[name + ".mongodb"] = False
        dump_yaml(config_path, config)

    # This is generated test output, not a second operator-maintained Compose source.
    dump_yaml(
        Path(str(output) + ".cpu-smoke.yaml"),
        {"services": smoke_services},
    )
    manifest["smoke"] = {
        "purpose": "cpu-container-health",
        "deviceOverrides": device_overrides,
        "persistenceOverrides": persistence_overrides,
    }
    dump_yaml(output / "manifest.yaml", manifest)
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
