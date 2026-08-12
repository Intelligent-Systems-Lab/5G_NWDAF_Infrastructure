#!/usr/bin/env bash
set -euo pipefail

machine=${1:?usage: runtime-tools-install.sh core|path-a|path-b source-root [source-hash]}
source_root=${2:?usage: runtime-tools-install.sh core|path-a|path-b source-root [source-hash]}
source_hash=${3:-provisioned-source}

case "$machine" in core|path-a|path-b) ;; *) echo "invalid machine: $machine" >&2; exit 2;; esac
test "$(id -u)" -eq 0 || { echo "runtime tool installation requires root" >&2; exit 1; }
test -d "$source_root/scripts/guest" || { echo "invalid runtime tool source: $source_root" >&2; exit 1; }

if [ -f /etc/5g-nwdaf-infrastructure/machine ]; then
  installed_machine=$(cat /etc/5g-nwdaf-infrastructure/machine)
  test "$installed_machine" = "$machine" || {
    echo "guest role mismatch: installed=$installed_machine requested=$machine" >&2
    exit 1
  }
fi

destination=/usr/local/libexec/5g-nwdaf-infrastructure
install -d "$destination" "$destination/bin" /etc/5g-nwdaf-infrastructure
install -m 0755 "$source_root/scripts/guest/service-run.sh" "$destination/service-run"
install -m 0755 "$source_root/scripts/guest/config-activate.sh" "$destination/config-activate"
install -m 0755 "$source_root/scripts/guest/network-config.py" "$destination/network-config"
install -m 0755 "$source_root/scripts/guest/network-setup.sh" "$destination/network-setup"
install -m 0755 "$source_root/scripts/guest/dataset-activate.sh" "$destination/dataset-activate"
install -m 0755 "$source_root/tools/nwdaf-consumer/consumer.py" "$destination/nwdaf-consumer"
install -m 0644 "$source_root/scripts/guest/subscriber-data.js" "$destination/subscriber-data.js"
install -m 0644 "$source_root/scripts/guest/systemd/5g-nwdaf@.service" /etc/systemd/system/5g-nwdaf@.service
install -m 0644 "$source_root/scripts/guest/systemd/5g-nwdaf-stack.target" /etc/systemd/system/5g-nwdaf-stack.target
install -m 0644 "$source_root/scripts/guest/systemd/5g-nwdaf-network.service" /etc/systemd/system/5g-nwdaf-network.service
install -m 0644 "$source_root/scripts/guest/systemd/5g-nwdaf-consumer.service" /etc/systemd/system/5g-nwdaf-consumer.service
printf '%s\n' "$source_hash" >/etc/5g-nwdaf-infrastructure/runtime-tools.sha256
systemctl daemon-reload
echo "RUNTIME TOOLS machine=$machine source=$source_hash"
