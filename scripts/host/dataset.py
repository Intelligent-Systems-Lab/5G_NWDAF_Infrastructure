#!/usr/bin/env python3
"""Stable CLI facade for protocol image-classification datasets."""

import argparse
import subprocess
import sys

from configlib import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--testbed", required=True)
    parser.add_argument("--config-dir")
    parser.add_argument("action", choices=("generate", "check", "show"))
    args = parser.parse_args()

    interpreter = ROOT / "ML" / "PyMTLF" / ".venv" / "bin" / "python"
    if not interpreter.is_file():
        print(
            "ERROR: PyMTLF project environment is missing: {}".format(interpreter),
            file=sys.stderr,
        )
        return 1
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


if __name__ == "__main__":
    sys.exit(main())
