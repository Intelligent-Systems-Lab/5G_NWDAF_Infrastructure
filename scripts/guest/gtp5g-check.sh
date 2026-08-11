#!/usr/bin/env bash
set -euo pipefail

path_name=${1:?usage: gtp5g-check.sh A|B}
case "$path_name" in A|B) ;; *) echo "path must be A or B" >&2; exit 2;; esac
test "$(id -u)" -eq 0 || { echo "gtp5g check requires root" >&2; exit 1; }

kernel=$(uname -r)
if ! vermagic=$(modinfo -F vermagic gtp5g 2>/dev/null); then
  echo "Path $path_name has no gtp5g module for running kernel $kernel" >&2
  echo "repair: sudo /opt/5g-nwdaf-infrastructure/source/scripts/guest/path.sh $path_name kernel" >&2
  exit 1
fi

module_kernel=${vermagic%% *}
if [ "$module_kernel" != "$kernel" ]; then
  echo "Path $path_name gtp5g vermagic $module_kernel does not match running kernel $kernel" >&2
  echo "repair: sudo /opt/5g-nwdaf-infrastructure/source/scripts/guest/path.sh $path_name kernel" >&2
  exit 1
fi

if ! modprobe gtp5g; then
  echo "Path $path_name could not load gtp5g for running kernel $kernel" >&2
  echo "repair: sudo /opt/5g-nwdaf-infrastructure/source/scripts/guest/path.sh $path_name kernel" >&2
  exit 1
fi
lsmod | awk '$1 == "gtp5g" {found=1} END {exit !found}' || {
  echo "Path $path_name gtp5g module is not loaded" >&2
  exit 1
}
echo "GTP5G path=$path_name kernel=$kernel vermagic=$module_kernel loaded=yes"
