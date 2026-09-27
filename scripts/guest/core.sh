#!/usr/bin/env bash
set -euo pipefail

action=${1:-setup}
inventory=${2:?usage: core.sh setup|build comma-separated-services comma-separated-component-revisions}
component_revisions=${3:?usage: core.sh setup|build comma-separated-services comma-separated-component-revisions}
root=/opt/5g-nwdaf-infrastructure
source_root=$root/source
work_root=$root/work
bin_root=/usr/local/libexec/5g-nwdaf-infrastructure/bin
provision_lock=$source_root/provisioning.lock.yaml
components_lock=$source_root/components.lock.yaml
provision_tool=$source_root/scripts/guest/provisioning-lock.py
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
  local repository distribution series key_url expected_fingerprint key_file
  local key_stage actual_fingerprint installed_fingerprint repo_file plan source drift
  local -a packages install_specs
  python3 "$provision_tool" validate "$provision_lock"
  repository=$(python3 "$provision_tool" get "$provision_lock" mongodb.repository.url)
  distribution=$(python3 "$provision_tool" get "$provision_lock" mongodb.repository.distribution)
  series=$(python3 "$provision_tool" get "$provision_lock" mongodb.repository.series)
  key_url=$(python3 "$provision_tool" get "$provision_lock" mongodb.repository.signingKeyUrl)
  expected_fingerprint=$(python3 "$provision_tool" get "$provision_lock" mongodb.repository.signingKeyFingerprint)
  key_file="/usr/share/keyrings/mongodb-server-${series}.gpg"
  key_stage=$(mktemp)
  curl -fsSL "$key_url" -o "$key_stage"
  actual_fingerprint=$(gpg --show-keys --with-colons "$key_stage" 2>/dev/null | awk -F: '$1 == "fpr" {print $10; exit}')
  if [ "$actual_fingerprint" != "$expected_fingerprint" ]; then
    rm -f "$key_stage"
    echo "MongoDB signing-key fingerprint mismatch" >&2
    exit 1
  fi
  if [ -f "$key_file" ]; then
    installed_fingerprint=$(gpg --show-keys --with-colons "$key_file" 2>/dev/null | awk -F: '$1 == "fpr" {print $10; exit}')
    if [ "$installed_fingerprint" != "$expected_fingerprint" ]; then
      rm -f "$key_stage"
      echo "installed MongoDB signing key differs from provisioning lock" >&2
      exit 1
    fi
  else
    gpg --dearmor --batch --yes -o "$key_file" "$key_stage"
  fi
  rm -f "$key_stage"

  repo_file="/etc/apt/sources.list.d/mongodb-org-${series}.list"
  printf 'deb [arch=amd64 signed-by=%s] %s %s/mongodb-org/%s multiverse\n' \
    "$key_file" "$repository" "$distribution" "$series" >"$repo_file"
  apt-get update

  plan=$(python3 "$provision_tool" resolve-mongodb "$provision_lock")
  source=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["source"])' <<<"$plan")
  drift=$(python3 -c 'import json,sys; print(str(json.load(sys.stdin)["drift"]).lower())' <<<"$plan")
  mapfile -t packages < <(python3 -c 'import json,sys; print(*json.load(sys.stdin)["packages"], sep="\n")' <<<"$plan")
  if [ "$drift" = true ]; then
    python3 -c 'import json,sys; [print("WARN MongoDB version drift: " + reason, file=sys.stderr) for reason in json.load(sys.stdin)["reasons"]]' <<<"$plan"
  fi
  if [ "$source" = repository ]; then
    mapfile -t install_specs < <(python3 -c 'import json,sys; data=json.load(sys.stdin); print(*(name + "=" + version for name,version in data["packages"].items()), sep="\n")' <<<"$plan")
    apt-get install -y --no-install-recommends "${install_specs[@]}"
    plan=$(python3 "$provision_tool" resolve-mongodb "$provision_lock")
  fi
  apt-mark hold "${packages[@]}" >/dev/null
  systemctl disable --now mongod >/dev/null 2>&1 || true
  install -d -o 5g-nwdaf -g 5g-nwdaf /var/lib/5g-nwdaf-infrastructure/mongodb
}

setup_runtime_storage() {
  install -d -o 5g-nwdaf -g 5g-nwdaf \
    /var/lib/5g-nwdaf-infrastructure/adrf \
    /var/lib/5g-nwdaf-infrastructure/adrf/models
}

IFS=, read -r -a selected_services <<<"$inventory"
[ "${#selected_services[@]}" -gt 0 ] || { echo "core service inventory is empty" >&2; exit 2; }

has_service() {
  local wanted=$1 service
  for service in "${selected_services[@]}"; do
    [ "$service" = "$wanted" ] && return 0
  done
  return 1
}

build_selected() {
  local service mapping source name
  declare -A built=()
  for service in "${selected_services[@]}"; do
    case "$service" in
      mongodb) continue ;;
      nrf|adrf) mapping="NFs/$service:$service" ;;
      nwdaf-*) mapping="NFs/nwdaf:nwdaf" ;;
      *) echo "unsupported core service: $service" >&2; exit 2 ;;
    esac
    source=${mapping%%:*}
    name=${mapping##*:}
    [ -z "${built[$name]+set}" ] || continue
    stage "$source" "$name"
    build_go "$name"
    built[$name]=true
  done
}

write_provisioning_manifest() {
  local -a component_args=()
  local record
  IFS=, read -r -a revision_records <<<"$component_revisions"
  for record in "${revision_records[@]}"; do
    component_args+=(--component "$record")
  done
  if has_service mongodb; then
    component_args+=(--include-mongodb)
  fi
  python3 "$provision_tool" write-manifest "$provision_lock" \
    --machine core --components-lock "$components_lock" \
    "${component_args[@]}" \
    --output /etc/5g-nwdaf-infrastructure/provisioning-manifest.yaml
}

case "$action" in
  setup)
    has_service mongodb && setup_mongodb
    has_service adrf && setup_runtime_storage
    build_selected
    write_provisioning_manifest
    ;;
  build) build_selected; write_provisioning_manifest ;;
  *) echo "usage: core.sh setup|build comma-separated-services comma-separated-component-revisions" >&2; exit 2;;
esac
