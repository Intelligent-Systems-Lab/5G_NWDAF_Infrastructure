#!/usr/bin/env python3
"""Exercise the supported protocol testbed definition contract."""

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "host"))

from configlib import (  # noqa: E402
    deployment_kind,
    expected_runtime_inventory,
    load_yaml,
    resolve_config_dir,
    resolve_ml_bind_address,
    selected_component_paths,
    selected_machine_names,
)


def main():
    testbed = load_yaml(ROOT / "testbed.protocol-hierarchical.yaml")
    assert deployment_kind(testbed) == "protocol-hierarchical"
    machines = selected_machine_names(testbed)
    assert set(testbed["placement"]) == set(machines) | {"host-containers"}
    assert resolve_config_dir(testbed) == ROOT / testbed["config"]["directory"]
    assert resolve_ml_bind_address(testbed) == testbed["mlRuntime"]["bindAddress"]

    runtime = expected_runtime_inventory(testbed)
    assert runtime["guestMachines"] == machines
    assert selected_component_paths(testbed, runtime) == [
        "ML/PyMTLF",
        "NFs/adrf",
        "NFs/nrf",
        "NFs/nwdaf",
    ]

    with tempfile.TemporaryDirectory() as temporary:
        explicit = Path(temporary) / "config"
        assert resolve_config_dir(testbed, str(explicit)) == explicit

    print(
        "TESTBED_DEFINITION_TEST machines={} guest_services={} host_containers={} status=passed".format(
            len(machines),
            len(runtime["guestServices"]),
            len(runtime["hostContainers"]),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
