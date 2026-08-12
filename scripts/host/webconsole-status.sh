#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

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
printf '%s %s %s\n' \"\$state\" \"\${identity:--}\" \"\${revision:--}\"" 2>/dev/null | tr -d '\r') || {
  echo "WEBCONSOLE guest=unavailable"
  exit 1
}
read -r enabled address port state identity revision <<<"$(printf '%s\n' "$payload" | tr '\n' ' ')"
http=unavailable
if [ "$state" = active ] && curl -fsS --max-time 2 "http://$address:$port/" >/dev/null 2>&1; then
  http=ready
fi
echo "WEBCONSOLE enabled=$enabled state=${state:-unknown} http=$http endpoint=http://$address:$port artifact=${identity:--} revision=${revision:--}"
