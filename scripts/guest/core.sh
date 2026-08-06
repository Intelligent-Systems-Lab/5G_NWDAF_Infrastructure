#!/usr/bin/env bash
set -euo pipefail

action=${1:-setup}
root=/opt/5g-nwdaf-infrastructure
source_root=$root/source
work_root=$root/work
bin_root=/usr/local/libexec/5g-nwdaf-infrastructure/bin
test "$(id -u)" -eq 0 || { echo "core setup requires root" >&2; exit 1; }

stage() {
  local source=$1 name=$2
  install -d "$work_root/$name"
  rsync -a --delete --exclude .git "$source_root/$source/" "$work_root/$name/"
  chown -R 5g-nwdaf:5g-nwdaf "$work_root/$name"
}

build_go() {
  local name=$1
  runuser -u 5g-nwdaf -- env PATH=/usr/local/go/bin:/usr/local/bin:/usr/bin \
    go -C "$work_root/$name" build -trimpath -o "$work_root/$name/$name" ./cmd
  install -m 0755 "$work_root/$name/$name" "$bin_root/$name"
}

setup_mongodb() {
  if ! command -v mongod >/dev/null; then
    curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc | gpg --dearmor -o /usr/share/keyrings/mongodb-server-8.0.gpg
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg] https://repo.mongodb.org/apt/ubuntu noble/mongodb-org/8.0 multiverse" >/etc/apt/sources.list.d/mongodb-org-8.0.list
    apt-get update
    apt-get install -y mongodb-org
  fi
  systemctl disable --now mongod >/dev/null 2>&1 || true
  install -d -o 5g-nwdaf -g 5g-nwdaf /var/lib/5g-nwdaf-infrastructure/mongodb
}

case "$action" in
  setup) setup_mongodb; "$0" build ;;
  build)
    for mapping in \
      NFs/nrf:nrf NFs/nssf:nssf NFs/udr:udr NFs/udm:udm NFs/ausf:ausf \
      NFs/pcf:pcf NFs/amf:amf NFs/smf:smf NFs/adrf:adrf NFs/nwdaf:nwdaf; do
      stage "${mapping%%:*}" "${mapping##*:}"
      build_go "${mapping##*:}"
    done
    stage ML/PyMTLF pymtlf
    runuser -u 5g-nwdaf -- env UV_CACHE_DIR=/var/lib/5g-nwdaf-infrastructure/.cache/uv \
      uv sync --project "$work_root/pymtlf" --frozen
    ;;
  *) echo "usage: core.sh setup|build" >&2; exit 2;;
esac
