#!/usr/bin/env bash
set -euo pipefail

machine=${1:?usage: common.sh core|path-a|path-b}
case "$machine" in core|path-a|path-b) ;; *) echo "invalid machine: $machine" >&2; exit 2;; esac
test "$(id -u)" -eq 0 || { echo "common setup requires root" >&2; exit 1; }

# The base box enables randomized unattended upgrades.  Package changes and a
# needrestart-driven networkd restart can still interrupt a bounded experiment,
# so changes in these reproducible guests are owned by explicit provisioning.
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true
while systemctl is-active --quiet apt-daily.service || \
      systemctl is-active --quiet apt-daily-upgrade.service; do
  sleep 2
done
systemctl mask apt-daily.service apt-daily-upgrade.service \
  apt-daily.timer apt-daily-upgrade.timer >/dev/null

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  build-essential ca-certificates cmake curl git gnupg iproute2 jq libssl-dev \
  libsctp-dev linux-headers-"$(uname -r)" ninja-build python3 \
  python3-yaml rsync socat util-linux

go_version=1.26.2
if ! command -v go >/dev/null || [ "$(go version | awk '{print $3}')" != "go${go_version}" ]; then
  archive="/tmp/go${go_version}.linux-amd64.tar.gz"
  curl -fsSL "https://go.dev/dl/go${go_version}.linux-amd64.tar.gz" -o "$archive"
  rm -rf /usr/local/go
  tar -C /usr/local -xzf "$archive"
  rm -f "$archive"
fi
ln -sfn /usr/local/go/bin/go /usr/local/bin/go

id 5g-nwdaf >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/5g-nwdaf-infrastructure --shell /usr/sbin/nologin 5g-nwdaf
install -d -o 5g-nwdaf -g 5g-nwdaf /var/lib/5g-nwdaf-infrastructure
install -d -o 5g-nwdaf -g 5g-nwdaf /var/lib/5g-nwdaf-infrastructure/datasets /var/lib/5g-nwdaf-infrastructure/datasets/sets
install -d /etc/5g-nwdaf-infrastructure/config-sets /opt/5g-nwdaf-infrastructure/work /usr/local/libexec/5g-nwdaf-infrastructure/bin
printf '%s\n' "$machine" >/etc/5g-nwdaf-infrastructure/machine

/opt/5g-nwdaf-infrastructure/source/scripts/guest/runtime-tools-install.sh \
  "$machine" /opt/5g-nwdaf-infrastructure/source
systemctl disable 5g-nwdaf-stack.target >/dev/null 2>&1 || true
systemctl disable 5g-nwdaf-consumer.service >/dev/null 2>&1 || true
systemctl disable 5g-nwdaf-network.service >/dev/null 2>&1 || true
if [ -f "/etc/5g-nwdaf-infrastructure/active/network/$machine.yaml" ]; then
  systemctl restart 5g-nwdaf-network.service
else
  systemctl stop 5g-nwdaf-network.service >/dev/null 2>&1 || true
fi
