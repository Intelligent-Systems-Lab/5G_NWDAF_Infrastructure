#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

webconsole_status_main() {
  local core_state payload enabled address port state identity revision http
  core_state=$(vm_state_for core) || return
  if [ "$core_state" != running ]; then
    echo "WEBCONSOLE enabled=unknown state=not-running http=not-running endpoint=unknown artifact=- revision=-"
    return 0
  fi

  payload=$(vssh core "python3 - <<'PY'
import pathlib, yaml
root = pathlib.Path('/etc/5g-nwdaf-infrastructure/active')
manifest = yaml.safe_load((root / 'manifest.yaml').read_text()) if root.exists() else {}
webui = yaml.safe_load((root / 'webuicfg.yaml').read_text()) if root.exists() else {}
enabled = manifest.get('optionalServices', {}).get('webconsole', {}).get('enabled', False)
server = webui.get('configuration', {}).get('webServer', {})
print(str(enabled).lower(), server.get('ipv4Address', '-'), server.get('port', '-'))
PY
state=\$(systemctl is-active 5g-nwdaf@webconsole.service 2>/dev/null || true)
identity=\$(cat /var/lib/5g-nwdaf-infrastructure/webconsole/current/identity 2>/dev/null || true)
revision=\$(cat /var/lib/5g-nwdaf-infrastructure/webconsole/current/source-revision 2>/dev/null || true)
printf '%s %s %s\n' \"\$state\" \"\${identity:--}\" \"\${revision:--}\"" | tr -d '\r') || {
    echo "WEBCONSOLE guest=unavailable" >&2
    return 1
  }
  read -r enabled address port state identity revision <<<"$(printf '%s\n' "$payload" | tr '\n' ' ')"
  http=unavailable
  if [ "$state" = active ] && curl -fsS --max-time 2 "http://$address:$port/" >/dev/null 2>&1; then
    http=ready
  fi
  echo "WEBCONSOLE enabled=$enabled state=${state:-unknown} http=$http endpoint=http://$address:$port artifact=${identity:--} revision=${revision:--}"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  webconsole_status_main "$@"
fi
