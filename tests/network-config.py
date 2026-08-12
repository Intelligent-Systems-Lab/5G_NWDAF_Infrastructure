#!/usr/bin/env python3
"""Focused tests for the Guest persistent Netplan renderer."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts/guest/network-config.py"
CORE_CONFIG = ROOT / "config/default/network/core.yaml"
PATH_A_CONFIG = ROOT / "config/default/network/path-a.yaml"
PATH_B_CONFIG = ROOT / "config/default/network/path-b.yaml"


def write_yaml(path: Path, value: object) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def current_addresses(*extra: tuple[str, str, int]) -> list[dict[str, object]]:
    values = [
        ("enp0s8", "192.168.56.10", 24),
        ("enp0s9", "192.168.57.2", 24),
        ("enp0s10", "192.168.58.2", 24),
        ("enp0s16", "192.168.61.2", 24),
    ]
    values.extend(extra)
    grouped: dict[str, list[dict[str, object]]] = {}
    for device, address, prefix in values:
        grouped.setdefault(device, []).append(
            {"family": "inet", "local": address, "prefixlen": prefix}
        )
    return [
        {"ifname": device, "addr_info": addresses}
        for device, addresses in grouped.items()
    ]


def invoke(
    directory: Path,
    *,
    config: Path = CORE_CONFIG,
    machine: str = "core",
    addresses: list[dict[str, object]] | None = None,
    previous: Path | None = None,
    expect_success: bool = True,
) -> tuple[dict[str, object], dict[str, object], subprocess.CompletedProcess[str]]:
    address_path = directory / "addresses.json"
    output_path = directory / "fragment.yaml"
    plan_path = directory / "plan.json"
    address_path.write_text(
        json.dumps(addresses or current_addresses()), encoding="utf-8"
    )
    command = [
        sys.executable,
        str(RENDERER),
        "--config",
        str(config),
        "--machine",
        machine,
        "--addresses",
        str(address_path),
        "--output",
        str(output_path),
        "--plan-output",
        str(plan_path),
    ]
    if previous is not None:
        command.extend(("--previous", str(previous)))
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if (result.returncode == 0) != expect_success:
        raise AssertionError(
            f"unexpected renderer status {result.returncode}: {result.stderr}"
        )
    if not expect_success:
        return {}, {}, result
    return (
        yaml.safe_load(output_path.read_text(encoding="utf-8")),
        json.loads(plan_path.read_text(encoding="utf-8")),
        result,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="5g-network-config-test-") as raw:
        directory = Path(raw)
        fragment, plan, _ = invoke(directory)
        ethernets = fragment["network"]["ethernets"]
        assert ethernets["enp0s9"]["addresses"] == [
            "192.168.57.10/24",
            "192.168.57.11/24",
            "192.168.57.12/24",
            "192.168.57.13/24",
            "192.168.57.14/24",
            "192.168.57.15/24",
            "192.168.57.16/24",
            "192.168.57.17/24",
            "192.168.57.18/24",
            "192.168.57.19/24",
            "192.168.57.30/24",
            "192.168.57.32/24",
        ]
        assert ethernets["enp0s10"]["addresses"] == ["192.168.58.10/24"]
        assert ethernets["enp0s16"]["addresses"] == ["192.168.61.10/24"]
        assert len(plan["aliases"]) == 14
        assert plan["affectedDevices"] == ["enp0s10", "enp0s16", "enp0s9"]

        _, path_a_plan, _ = invoke(
            directory,
            config=PATH_A_CONFIG,
            machine="path-a",
            addresses=current_addresses(
                ("enp0s9", "192.168.57.3", 24),
                ("enp0s10", "192.168.58.3", 24),
                ("enp0s16", "192.168.59.2", 24),
                ("enp0s17", "192.168.61.3", 24),
                ("enp0s18", "192.168.62.2", 24),
            ),
        )
        assert len(path_a_plan["aliases"]) == 7
        assert path_a_plan["affectedDevices"] == [
            "enp0s10",
            "enp0s16",
            "enp0s17",
            "enp0s18",
            "enp0s9",
        ]

        _, path_b_plan, _ = invoke(
            directory,
            config=PATH_B_CONFIG,
            machine="path-b",
            addresses=current_addresses(
                ("enp0s9", "192.168.57.4", 24),
                ("enp0s10", "192.168.58.4", 24),
                ("enp0s16", "192.168.60.2", 24),
                ("enp0s17", "192.168.61.4", 24),
                ("enp0s18", "192.168.63.2", 24),
            ),
        )
        assert len(path_b_plan["aliases"]) == 7
        assert path_b_plan["affectedDevices"] == path_a_plan["affectedDevices"]

        _, _, wrong_role = invoke(directory, machine="path-a", expect_success=False)
        assert "does not match" in wrong_role.stderr

        duplicate_config = directory / "duplicate.yaml"
        duplicate = yaml.safe_load(CORE_CONFIG.read_text(encoding="utf-8"))
        duplicate["aliases"].append(dict(duplicate["aliases"][0]))
        write_yaml(duplicate_config, duplicate)
        _, _, duplicate_result = invoke(
            directory, config=duplicate_config, expect_success=False
        )
        assert "duplicate alias address" in duplicate_result.stderr

        collision_addresses = current_addresses(("enp0s8", "192.168.57.10", 24))
        _, _, collision = invoke(
            directory, addresses=collision_addresses, expect_success=False
        )
        assert "already exists on enp0s8" in collision.stderr

        previous = directory / "previous.yaml"
        write_yaml(
            previous,
            {
                "network": {
                    "version": 2,
                    "ethernets": {
                        "enp0s9": {"addresses": ["192.168.57.99/24"]},
                        "enp0s8": {"addresses": ["192.168.57.10/24"]},
                    },
                }
            },
        )
        _, migration_plan, _ = invoke(
            directory,
            addresses=collision_addresses,
            previous=previous,
        )
        stale = {
            (item["cidr"], item["device"])
            for item in migration_plan["staleAliases"]
        }
        assert stale == {
            ("192.168.57.10/24", "enp0s8"),
            ("192.168.57.99/24", "enp0s9"),
        }
        assert migration_plan["affectedDevices"] == [
            "enp0s10",
            "enp0s16",
            "enp0s8",
            "enp0s9",
        ]

    print("NETWORK_CONFIG_TEST cases=7 status=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
