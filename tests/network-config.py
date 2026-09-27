#!/usr/bin/env python3
"""Focused tests for the Guest persistent Netplan renderer."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "host"))
from configlib import guest_network_configs, load_yaml  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "network_config", ROOT / "scripts" / "guest" / "network-config.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def main():
    testbed = load_yaml(ROOT / "testbed.protocol-hierarchical.yaml")
    configs = guest_network_configs(testbed)
    with tempfile.TemporaryDirectory(prefix="5g-network-config-") as temporary:
        directory = Path(temporary)
        for machine, config in configs.items():
            config_path = directory / (machine + ".yaml")
            addresses_path = directory / (machine + "-addresses.json")
            config_path.write_text(
                yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
            )
            anchors = []
            for index, anchor in enumerate(
                dict.fromkeys(item["anchor"] for item in config["aliases"])
            ):
                anchors.append(
                    {
                        "ifname": "eth{}".format(index),
                        "addr_info": [{"family": "inet", "local": anchor}],
                    }
                )
            addresses_path.write_text(json.dumps(anchors), encoding="utf-8")
            fragment, plan = MODULE.render(
                config_path, machine, addresses_path, None
            )
            assert plan["machine"] == machine
            assert len(plan["aliases"]) == len(config["aliases"])
            rendered = {
                alias.split("/", 1)[0]
                for values in fragment["network"]["ethernets"].values()
                for alias in values["addresses"]
            }
            assert rendered == {item["address"] for item in config["aliases"]}

        core = configs["core"]
        duplicate = dict(core)
        duplicate["aliases"] = list(core["aliases"]) + [dict(core["aliases"][0])]
        config_path = directory / "duplicate.yaml"
        config_path.write_text(yaml.safe_dump(duplicate), encoding="utf-8")
        addresses_path = directory / "core-addresses.json"
        try:
            MODULE.render(config_path, "core", addresses_path, None)
        except MODULE.NetworkConfigError as exc:
            assert "duplicate alias" in str(exc)
        else:
            raise AssertionError("duplicate network alias was accepted")

    print("NETWORK_CONFIG_TEST machines={} status=passed".format(len(configs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
