#!/usr/bin/env python3
"""Parse exact VBoxHeadless PID/UUID records from pgrep output."""

import os
import re
import sys
import uuid


LINE = re.compile(r"^(?P<pid>[1-9][0-9]*)[ \t]+(?P<command>.+)$")
START_VM = re.compile(r"(?:^|[ \t])--startvm(?:[ \t]+|=)(?P<uuid>[^ \t]+)")


def canonical_uuid(value):
    if value.startswith("{") or value.endswith("}"):
        if not (value.startswith("{") and value.endswith("}")):
            raise ValueError("invalid --startvm UUID braces: {}".format(value))
        candidate = value[1:-1]
    else:
        candidate = value
    try:
        parsed = uuid.UUID(candidate)
    except ValueError as exc:
        raise ValueError("invalid --startvm UUID: {}".format(value)) from exc
    if candidate.lower() != str(parsed):
        raise ValueError("non-canonical --startvm UUID: {}".format(value))
    return str(parsed)


def parse_line(raw):
    match = LINE.fullmatch(raw)
    if not match:
        raise ValueError("invalid pgrep record: {}".format(raw))
    command = match.group("command")
    executable = command.split(None, 1)[0]
    if os.path.basename(executable) != "VBoxHeadless":
        raise ValueError("unexpected process in VBoxHeadless inventory: {}".format(raw))
    matches = list(START_VM.finditer(command))
    if len(matches) != 1:
        raise ValueError(
            "VBoxHeadless record must contain exactly one --startvm UUID: {}".format(raw)
        )
    return int(match.group("pid")), canonical_uuid(matches[0].group("uuid"))


def main():
    records = []
    seen_pids = set()
    for line_number, raw in enumerate(sys.stdin, 1):
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        try:
            pid, machine_uuid = parse_line(line)
        except ValueError as exc:
            raise SystemExit("line {}: {}".format(line_number, exc)) from exc
        if pid in seen_pids:
            raise SystemExit("duplicate VBoxHeadless PID: {}".format(pid))
        seen_pids.add(pid)
        records.append((pid, machine_uuid))
    for pid, machine_uuid in sorted(records):
        print("{}|{}".format(pid, machine_uuid))


if __name__ == "__main__":
    main()
