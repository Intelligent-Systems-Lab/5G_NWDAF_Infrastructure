#!/usr/bin/env bash
set -euo pipefail

machine=${1:?usage: common.sh core|path-a|path-b}
case "$machine" in core|path-a|path-b) ;; *) echo "invalid machine: $machine" >&2; exit 2;; esac
test "$(id -u)" -eq 0 || { echo "common setup requires root" >&2; exit 1; }

# The base box enables randomized unattended upgrades.  needrestart may restart
# systemd-networkd after those upgrades and flush the topology aliases while an
# experiment is running.  Package changes in these reproducible guests are
# owned by explicit provisioning instead.
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

install -m 0755 /opt/5g-nwdaf-infrastructure/source/scripts/guest/service-run.sh /usr/local/libexec/5g-nwdaf-infrastructure/service-run
install -m 0755 /opt/5g-nwdaf-infrastructure/source/scripts/guest/config-activate.sh /usr/local/libexec/5g-nwdaf-infrastructure/config-activate
install -m 0755 /opt/5g-nwdaf-infrastructure/source/scripts/guest/network-setup.sh /usr/local/libexec/5g-nwdaf-infrastructure/network-setup
install -m 0755 /opt/5g-nwdaf-infrastructure/source/scripts/guest/dataset-activate.sh /usr/local/libexec/5g-nwdaf-infrastructure/dataset-activate
install -m 0755 /opt/5g-nwdaf-infrastructure/source/tools/nwdaf-consumer/consumer.py /usr/local/libexec/5g-nwdaf-infrastructure/nwdaf-consumer
install -m 0644 /opt/5g-nwdaf-infrastructure/source/scripts/guest/systemd/5g-nwdaf@.service /etc/systemd/system/5g-nwdaf@.service
install -m 0644 /opt/5g-nwdaf-infrastructure/source/scripts/guest/systemd/5g-nwdaf-stack.target /etc/systemd/system/5g-nwdaf-stack.target
install -m 0644 /opt/5g-nwdaf-infrastructure/source/scripts/guest/systemd/5g-nwdaf-network.service /etc/systemd/system/5g-nwdaf-network.service
install -m 0644 /opt/5g-nwdaf-infrastructure/source/scripts/guest/systemd/5g-nwdaf-consumer.service /etc/systemd/system/5g-nwdaf-consumer.service
systemctl daemon-reload
systemctl disable 5g-nwdaf-stack.target >/dev/null 2>&1 || true
systemctl disable 5g-nwdaf-consumer.service >/dev/null 2>&1 || true
systemctl enable --now 5g-nwdaf-network.service
