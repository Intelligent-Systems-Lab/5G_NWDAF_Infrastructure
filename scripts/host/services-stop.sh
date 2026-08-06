#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

for unit in ue6 ue5 ue4 gnb-b nwdaf-b pymtlf-b pyanlf-b upf-b; do stop_unit path-b "$unit"; done
for unit in ue3 ue2 ue1 gnb-a nwdaf-a pymtlf-a pyanlf-a upf-a; do stop_unit path-a "$unit"; done
for unit in nwdaf-c pymtlf-c adrf smf amf pcf ausf udm udr nssf nrf mongodb; do stop_unit core "$unit"; done
echo "Experiment services stopped; VMs remain powered on."

