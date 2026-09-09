#!/usr/bin/env python3
"""Render one role's topology aliases as a persistent Netplan fragment."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

import yaml


DEVICE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")


class NetworkConfigError(ValueError):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = yaml.safe_load(stream) or {}
    if not isinstance(value, dict):
        raise NetworkConfigError(f"YAML root must be an object: {path}")
    return value


def load_current_addresses(path: Path) -> dict[str, list[str]]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, list):
        raise NetworkConfigError("current address inventory must be a list")

    by_address: dict[str, list[str]] = {}
    for interface in value:
        if not isinstance(interface, dict):
            raise NetworkConfigError("invalid interface address inventory")
        device = interface.get("ifname")
        if not isinstance(device, str) or not DEVICE_PATTERN.fullmatch(device):
            raise NetworkConfigError(f"invalid interface name: {device!r}")
        for address in interface.get("addr_info", []):
            if not isinstance(address, dict) or address.get("family") != "inet":
                continue
            local = str(ipaddress.ip_address(address.get("local")))
            by_address.setdefault(local, []).append(device)
    return by_address


def load_previous_fragment(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    config = load_yaml(path)
    network = config.get("network")
    if not isinstance(network, dict) or network.get("version") != 2:
        raise NetworkConfigError("managed Netplan fragment has an invalid network root")
    ethernets = network.get("ethernets", {})
    if not isinstance(ethernets, dict):
        raise NetworkConfigError("managed Netplan fragment ethernets must be an object")

    aliases: list[dict[str, str]] = []
    for device, settings in ethernets.items():
        if not isinstance(device, str) or not DEVICE_PATTERN.fullmatch(device):
            raise NetworkConfigError(f"invalid managed interface name: {device!r}")
        if not isinstance(settings, dict) or set(settings) != {"addresses"}:
            raise NetworkConfigError(
                f"managed fragment contains unsupported settings for {device}"
            )
        addresses = settings["addresses"]
        if not isinstance(addresses, list):
            raise NetworkConfigError(f"managed addresses for {device} must be a list")
        for cidr in addresses:
            if not isinstance(cidr, str):
                raise NetworkConfigError("managed address must use CIDR string form")
            interface = ipaddress.ip_interface(cidr)
            if interface.version != 4:
                raise NetworkConfigError("only IPv4 managed aliases are supported")
            aliases.append(
                {
                    "address": str(interface.ip),
                    "cidr": str(interface),
                    "device": device,
                }
            )
    return aliases


def render(
    config_path: Path,
    machine: str,
    addresses_path: Path,
    previous_path: Path | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = load_yaml(config_path)
    if config.get("schemaVersion") != 1:
        raise NetworkConfigError("unsupported network config schema")
    if config.get("machine") != machine:
        raise NetworkConfigError("network config machine does not match this guest")
    aliases = config.get("aliases", [])
    if not isinstance(aliases, list):
        raise NetworkConfigError("network aliases must be a list")

    current = load_current_addresses(addresses_path)
    previous = load_previous_fragment(previous_path)
    previous_locations = {
        (item["address"], item["device"]) for item in previous
    }
    groups: OrderedDict[str, list[str]] = OrderedDict()
    desired: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in aliases:
        if not isinstance(item, dict):
            raise NetworkConfigError("network alias must be an object")
        required = ("owner", "endpoint", "network", "address", "prefixLength", "anchor")
        if any(key not in item for key in required):
            raise NetworkConfigError("network alias is missing a required field")
        for key in ("owner", "endpoint", "network"):
            value = item[key]
            if not isinstance(value, str) or not value or any(
                separator in value for separator in ("\t", "\r", "\n")
            ):
                raise NetworkConfigError(f"invalid network alias {key}")

        address = ipaddress.ip_address(item["address"])
        anchor = ipaddress.ip_address(item["anchor"])
        prefix = item["prefixLength"]
        if address.version != 4 or anchor.version != 4:
            raise NetworkConfigError("only IPv4 aliases are supported")
        if isinstance(prefix, bool) or not isinstance(prefix, int) or not 0 <= prefix <= 32:
            raise NetworkConfigError("invalid alias prefix length")
        subnet = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
        if anchor not in subnet:
            raise NetworkConfigError("alias and anchor are not in the same subnet")
        if address == anchor:
            raise NetworkConfigError("alias duplicates its anchor address")

        address_text = str(address)
        anchor_text = str(anchor)
        if address_text in seen:
            raise NetworkConfigError(f"duplicate alias address: {address_text}")
        seen.add(address_text)

        anchor_devices = current.get(anchor_text, [])
        if len(anchor_devices) != 1:
            raise NetworkConfigError(
                f"cannot resolve exactly one interface via anchor {anchor_text}"
            )
        device = anchor_devices[0]
        for existing_device in current.get(address_text, []):
            if existing_device != device and (
                address_text,
                existing_device,
            ) not in previous_locations:
                raise NetworkConfigError(
                    f"{address_text} already exists on {existing_device} instead of {device}"
                )

        cidr = str(ipaddress.ip_interface(f"{address}/{prefix}"))
        groups.setdefault(device, []).append(cidr)
        desired.append(
            {
                "address": address_text,
                "cidr": cidr,
                "device": device,
                "owner": item["owner"],
                "endpoint": item["endpoint"],
            }
        )

    fragment: dict[str, Any] = {
        "network": {
            "version": 2,
            "ethernets": {
                device: {"addresses": cidrs} for device, cidrs in groups.items()
            },
        }
    }
    desired_locations = {
        (item["cidr"], item["device"]) for item in desired
    }
    stale = [
        item
        for item in previous
        if (item["cidr"], item["device"]) not in desired_locations
    ]
    affected_devices = sorted(
        {item["device"] for item in desired} | {item["device"] for item in previous}
    )
    plan = {
        "schemaVersion": 1,
        "machine": machine,
        "aliases": desired,
        "previousAliases": previous,
        "staleAliases": stale,
        "affectedDevices": affected_devices,
    }
    return fragment, plan


def clear_plan(previous_path: Path | None) -> dict[str, Any]:
    previous = load_previous_fragment(previous_path)
    return {
        "schemaVersion": 1,
        "aliases": [],
        "previousAliases": previous,
        "staleAliases": previous,
        "affectedDevices": sorted({item["device"] for item in previous}),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--machine")
    parser.add_argument("--addresses", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan-output", type=Path, required=True)
    parser.add_argument("--clear", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.clear:
            plan = clear_plan(args.previous)
        else:
            if not all((args.config, args.machine, args.addresses, args.output)):
                raise NetworkConfigError(
                    "render requires --config, --machine, --addresses, and --output"
                )
            fragment, plan = render(
                args.config, args.machine, args.addresses, args.previous
            )
            args.output.write_text(
                yaml.safe_dump(fragment, sort_keys=False), encoding="utf-8"
            )
        args.plan_output.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (NetworkConfigError, OSError, ValueError, yaml.YAMLError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
