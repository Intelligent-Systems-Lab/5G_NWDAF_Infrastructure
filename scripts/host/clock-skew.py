#!/usr/bin/env python3
"""Validate exact Guest clock samples against a millisecond tolerance."""

from __future__ import annotations

import argparse
import sys


def validate(records: list[str], machines: list[str], tolerance_ms: int) -> int:
    samples = {}
    for record in records:
        fields = record.rstrip("\n").split("|")
        if len(fields) != 2 or fields[0] not in machines or not fields[1].isdigit():
            raise ValueError("invalid Guest clock sample: {}".format(record.rstrip()))
        if fields[0] in samples:
            raise ValueError("duplicate Guest clock sample: {}".format(fields[0]))
        samples[fields[0]] = int(fields[1])
    missing = sorted(set(machines) - set(samples))
    unexpected = sorted(set(samples) - set(machines))
    if missing or unexpected:
        raise ValueError(
            "Guest clock sample inventory differs: missing={} unexpected={}".format(
                missing, unexpected
            )
        )
    skew = max(samples.values()) - min(samples.values())
    if skew > tolerance_ms:
        raise ValueError(
            "Guest clock skew {}ms exceeds {}ms".format(skew, tolerance_ms)
        )
    return skew


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machines", required=True)
    parser.add_argument("--tolerance-ms", required=True, type=int)
    args = parser.parse_args()
    machines = args.machines.split(",")
    if not machines or any(not machine for machine in machines):
        print("ERROR: machine inventory is empty or invalid", file=sys.stderr)
        return 1
    if args.tolerance_ms <= 0:
        print("ERROR: tolerance must be positive", file=sys.stderr)
        return 1
    try:
        skew = validate(list(sys.stdin), machines, args.tolerance_ms)
    except ValueError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    print("CLOCK synchronized=true skew_ms={} tolerance_ms={}".format(skew, args.tolerance_ms))
    return 0


if __name__ == "__main__":
    sys.exit(main())
