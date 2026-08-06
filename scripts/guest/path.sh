#!/usr/bin/env bash
set -euo pipefail

path_name=${1:?usage: path.sh A|B setup|build}
action=${2:-setup}
case "$path_name" in A|B) ;; *) echo "path must be A or B" >&2; exit 2;; esac
root=/opt/5g-nwdaf-infrastructure
source_root=$root/source
work_root=$root/work
bin_root=/usr/local/libexec/5g-nwdaf-infrastructure/bin
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

case "$action" in
  setup) "$0" "$path_name" build ;;
  build)
    build_gtp5g
    stage NFs/upf upf
    runuser -u 5g-nwdaf -- env PATH=/usr/local/go/bin:/usr/local/bin:/usr/bin go -C "$work_root/upf" build -trimpath -o "$work_root/upf/upf" ./cmd
    install -m 0755 "$work_root/upf/upf" "$bin_root/upf"
    stage NFs/nwdaf nwdaf
    runuser -u 5g-nwdaf -- env PATH=/usr/local/go/bin:/usr/local/bin:/usr/bin go -C "$work_root/nwdaf" build -trimpath -o "$work_root/nwdaf/nwdaf" ./cmd
    install -m 0755 "$work_root/nwdaf/nwdaf" "$bin_root/nwdaf"
    stage RAN/UERANSIM ueransim
    cmake -S "$work_root/ueransim" -B "$work_root/ueransim/build" -G Ninja -DCMAKE_BUILD_TYPE=Release
    cmake --build "$work_root/ueransim/build"
    for name in pyanlf pymtlf; do
      source_name=PyAnLF; [ "$name" = pymtlf ] && source_name=PyMTLF
      stage "ML/$source_name" "$name"
      runuser -u 5g-nwdaf -- uv sync --project "$work_root/$name" --frozen --no-cache
    done
    ;;
  *) echo "usage: path.sh A|B setup|build" >&2; exit 2;;
esac
