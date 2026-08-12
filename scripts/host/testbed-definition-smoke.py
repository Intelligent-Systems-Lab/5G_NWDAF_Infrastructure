#!/usr/bin/env python3
"""Exercise the single selected-testbed resolution contract."""

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "host"))

from configlib import (  # noqa: E402
    load_yaml,
    resolve_config_dir,
    resolve_ml_bind_address,
)


def main():
    default = load_yaml(ROOT / "testbed.yaml")
    assert resolve_config_dir(default) == ROOT / "config" / "default"
    assert resolve_ml_bind_address(default) == "192.168.57.1"
    print("OK committed testbed owns config directory and ML bind address")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        selected_config = root / "selected-config"
        explicit_config = root / "explicit-config"
        alternate = {
            "config": {"directory": str(selected_config)},
            "mlRuntime": {"bindAddress": "192.0.2.10"},
        }
        assert resolve_config_dir(alternate) == selected_config
        assert resolve_config_dir(alternate, str(explicit_config)) == explicit_config
        assert resolve_ml_bind_address(alternate) == "192.0.2.10"
    print("OK selected TESTBED values apply and explicit CONFIG_DIR wins")

    try:
        resolve_config_dir({"config": {}}, str(explicit_config))
    except ValueError as exc:
        assert "config.directory is required" in str(exc)
    else:
        raise AssertionError("an incomplete selected testbed bypassed config validation")
    try:
        resolve_ml_bind_address({"mlRuntime": {}})
    except ValueError as exc:
        assert "mlRuntime.bindAddress is required" in str(exc)
    else:
        raise AssertionError("an incomplete selected testbed supplied an ML bind address")
    print("OK incomplete selected testbed definitions are rejected")

    source = (ROOT / "scripts" / "host" / "configlib.py").read_text(
        encoding="utf-8"
    )
    assert "testbed.local" not in source
    assert "load_local_settings" not in source
    print("OK config resolution has no hidden local layer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
