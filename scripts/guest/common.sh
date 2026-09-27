#!/usr/bin/env bash
set -euo pipefail

machine=${1:?usage: common.sh machine}
[[ "$machine" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "invalid machine: $machine" >&2; exit 2; }
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
  build-essential ca-certificates curl git gnupg iproute2 jq libssl-dev python3 chrony \
  python3-yaml rsync socat util-linux

systemctl enable --now chrony

source_root=/opt/5g-nwdaf-infrastructure/source
provision_lock=$source_root/provisioning.lock.yaml
provision_tool=$source_root/scripts/guest/provisioning-lock.py
python3 "$provision_tool" validate "$provision_lock"

. /etc/os-release
expected_os=$(python3 "$provision_tool" get "$provision_lock" platform.os)
expected_release=$(python3 "$provision_tool" get "$provision_lock" platform.release)
expected_arch=$(python3 "$provision_tool" get "$provision_lock" platform.architecture)
test "$ID" = "$expected_os" -a "$VERSION_ID" = "$expected_release" || {
  echo "unsupported Guest platform: expected $expected_os $expected_release, got $ID $VERSION_ID" >&2
  exit 1
}
test "$(dpkg --print-architecture)" = "$expected_arch" || {
  echo "unsupported Guest architecture: expected $expected_arch" >&2
  exit 1
}

go_version=$(python3 "$provision_tool" get "$provision_lock" go.version)
go_archive=$(python3 "$provision_tool" get "$provision_lock" go.archive)
go_url=$(python3 "$provision_tool" get "$provision_lock" go.url)
go_sha256=$(python3 "$provision_tool" get "$provision_lock" go.sha256)
if ! test -x /usr/local/go/bin/go || \
   [ "$(/usr/local/go/bin/go version | awk '{print $3, $4}')" != "go${go_version} linux/amd64" ]; then
  archive=$(mktemp "/tmp/${go_archive}.XXXXXX")
  stage=$(mktemp -d /usr/local/.go-stage.XXXXXX)
  cleanup_go_stage() { rm -rf "$stage" "$archive"; }
  trap cleanup_go_stage EXIT
  curl -fsSL "$go_url" -o "$archive"
  printf '%s  %s\n' "$go_sha256" "$archive" | sha256sum --check --status || {
    echo "Go archive SHA-256 mismatch: $go_archive" >&2
    exit 1
  }
  tar -C "$stage" -xzf "$archive"
  test "$($stage/go/bin/go version | awk '{print $3, $4}')" = "go${go_version} linux/amd64" || {
    echo "Go archive binary identity mismatch" >&2
    exit 1
  }
  target="/usr/local/go-${go_version}"
  rm -rf "$target"
  mv "$stage/go" "$target"
  if [ -e /usr/local/go ] && [ ! -L /usr/local/go ]; then
    mv /usr/local/go "/usr/local/go.previous-$(date -u +%Y%m%dT%H%M%SZ)"
  fi
  ln -sfn "$target" /usr/local/go
  trap - EXIT
  cleanup_go_stage
fi
ln -sfn /usr/local/go/bin/go /usr/local/bin/go

id 5g-nwdaf >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/5g-nwdaf-infrastructure --shell /usr/sbin/nologin 5g-nwdaf
install -d -o 5g-nwdaf -g 5g-nwdaf /var/lib/5g-nwdaf-infrastructure
install -d /etc/5g-nwdaf-infrastructure/config-sets /opt/5g-nwdaf-infrastructure/work /usr/local/libexec/5g-nwdaf-infrastructure/bin
printf '%s\n' "$machine" >/etc/5g-nwdaf-infrastructure/machine

/opt/5g-nwdaf-infrastructure/source/scripts/guest/runtime-tools-install.sh \
  "$machine" /opt/5g-nwdaf-infrastructure/source
systemctl disable 5g-nwdaf-stack.target >/dev/null 2>&1 || true
systemctl disable 5g-nwdaf-network.service >/dev/null 2>&1 || true
if [ -f "/etc/5g-nwdaf-infrastructure/active/network/$machine.yaml" ]; then
  systemctl restart 5g-nwdaf-network.service
else
  systemctl stop 5g-nwdaf-network.service >/dev/null 2>&1 || true
fi
