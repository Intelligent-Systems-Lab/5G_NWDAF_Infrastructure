#!/usr/bin/env bash
set -euo pipefail

path_name=${1:?usage: path.sh machine setup|build|kernel [comma-separated-services comma-separated-component-revisions]}
action=${2:-setup}
inventory=${3:-}
component_revisions=${4:-}
root=/opt/5g-nwdaf-infrastructure
source_root=$root/source
work_root=$root/work
bin_root=/usr/local/libexec/5g-nwdaf-infrastructure/bin
provision_lock=$source_root/provisioning.lock.yaml
components_lock=$source_root/components.lock.yaml
provision_tool=$source_root/scripts/guest/provisioning-lock.py
test "$(id -u)" -eq 0 || { echo "path setup requires root" >&2; exit 1; }

stage() {
  local source=$1 name=$2
  install -d "$work_root/$name"
  rsync -a --delete --exclude .git "$source_root/$source/" "$work_root/$name/"
  chown -R 5g-nwdaf:5g-nwdaf "$work_root/$name"
}

build_gtp5g() {
  stage kernel/gtp5g gtp5g
  make -C "$work_root/gtp5g" clean
  make -C "$work_root/gtp5g"
  make -C "$work_root/gtp5g" install
  depmod -a
}

if [ "$action" = kernel ]; then
  build_gtp5g
  exit 0
fi
[[ "$path_name" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "invalid path machine: $path_name" >&2; exit 2; }
[ -n "$inventory" ] && [ -n "$component_revisions" ] || {
  echo "setup/build requires service and component revision inventories" >&2
  exit 2
}
IFS=, read -r -a selected_services <<<"$inventory"
[ "${#selected_services[@]}" -gt 0 ] || { echo "path service inventory is empty" >&2; exit 2; }

build_selected() {
  local service need_gtp=false need_upf=false need_nwdaf=false need_ueransim=false
  for service in "${selected_services[@]}"; do
    case "$service" in
      upf-*) need_gtp=true; need_upf=true ;;
      nwdaf-*) need_nwdaf=true ;;
      gnb-*|ue[0-9]*) need_ueransim=true ;;
      *) echo "unsupported path service: $service" >&2; exit 2 ;;
    esac
  done
  if $need_gtp; then
    build_gtp5g
  fi
  if $need_upf; then
    stage NFs/upf upf
    runuser -u 5g-nwdaf -- env PATH=/usr/local/go/bin:/usr/local/bin:/usr/bin \
      go -C "$work_root/upf" build -trimpath -o "$work_root/upf/upf" ./cmd
    install -m 0755 "$work_root/upf/upf" "$bin_root/upf"
  fi
  if $need_nwdaf; then
    stage NFs/nwdaf nwdaf
    runuser -u 5g-nwdaf -- env PATH=/usr/local/go/bin:/usr/local/bin:/usr/bin \
      go -C "$work_root/nwdaf" build -trimpath -o "$work_root/nwdaf/nwdaf" ./cmd
    install -m 0755 "$work_root/nwdaf/nwdaf" "$bin_root/nwdaf"
  fi
  if $need_ueransim; then
    stage RAN/UERANSIM ueransim
    cmake -S "$work_root/ueransim" -B "$work_root/ueransim/build" -G Ninja -DCMAKE_BUILD_TYPE=Release
    cmake --build "$work_root/ueransim/build"
  fi
}

write_provisioning_manifest() {
  local -a component_args=()
  local record
  IFS=, read -r -a revision_records <<<"$component_revisions"
  for record in "${revision_records[@]}"; do
    component_args+=(--component "$record")
  done
  python3 "$provision_tool" write-manifest "$provision_lock" \
    --machine "$path_name" --components-lock "$components_lock" \
    "${component_args[@]}" \
    --output /etc/5g-nwdaf-infrastructure/provisioning-manifest.yaml
}

case "$action" in
  setup) build_selected; write_provisioning_manifest ;;
  build) build_selected; write_provisioning_manifest ;;
  *) echo "usage: path.sh machine setup|build|kernel [comma-separated-services comma-separated-component-revisions]" >&2; exit 2;;
esac
