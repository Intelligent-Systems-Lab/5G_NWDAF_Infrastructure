#!/usr/bin/env python3
"""Shared, host-only configuration helpers."""

import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def load_yaml(path):
    with Path(path).open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    return {} if value is None else value


def dump_yaml(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(value, stream, sort_keys=False, default_flow_style=False)


def resolve_path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def resolve_config_dir(testbed, explicit=None):
    if explicit:
        return resolve_path(explicit)
    local_path = ROOT / "testbed.local.yaml"
    if local_path.exists():
        configured = load_yaml(local_path).get("config", {}).get("directory")
        if configured:
            return resolve_path(configured)
    return resolve_path(testbed.get("config", {}).get("directory", "config/default"))


def load_local_settings():
    local_path = ROOT / "testbed.local.yaml"
    return load_yaml(local_path) if local_path.exists() else {}


def resolve_ml_bind_address(testbed):
    local = load_local_settings()
    return local.get("host", {}).get(
        "mlBindAddress", testbed.get("mlRuntime", {}).get("bindAddress")
    )


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(directory):
    digest = hashlib.sha256()
    for path in sorted(Path(directory).rglob("*.yaml")):
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def canonical_sha256(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def get_path(value, keys):
    current = value
    for key in keys:
        current = current[key]
    return current


def set_path(value, keys, replacement):
    current = value
    for key in keys[:-1]:
        current = current[key]
    current[keys[-1]] = replacement
