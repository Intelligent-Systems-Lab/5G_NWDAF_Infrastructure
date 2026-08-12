#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

name=5g-nwdaf-provisioning-install-smoke
if docker container inspect "$name" >/dev/null 2>&1; then
  echo "refusing to reuse existing container: $name" >&2
  exit 1
fi

docker run --rm --name "$name" --mount "type=bind,src=$HOST_ROOT,dst=/source,readonly" \
  ubuntu:22.04 bash -euo pipefail -c '
export DEBIAN_FRONTEND=noninteractive
apt-get update >/dev/null
apt-get install -y --no-install-recommends ca-certificates curl gnupg python3 python3-yaml >/dev/null

lock=/source/provisioning.lock.yaml
tool=/source/scripts/guest/provisioning-lock.py
python3 "$tool" validate "$lock"

go_version=$(python3 "$tool" get "$lock" go.version)
go_archive=$(python3 "$tool" get "$lock" go.archive)
go_url=$(python3 "$tool" get "$lock" go.url)
go_sha=$(python3 "$tool" get "$lock" go.sha256)
curl -fsSL "$go_url" -o "/tmp/$go_archive"
printf "%s  %s\n" "$go_sha" "/tmp/$go_archive" | sha256sum --check --status
mkdir /tmp/go-stage
tar -C /tmp/go-stage -xzf "/tmp/$go_archive"
test "$(/tmp/go-stage/go/bin/go version | awk "{print \$3, \$4}")" = "go${go_version} linux/amd64"
echo "OK Go artifact version=$go_version sha256=$go_sha"

repository=$(python3 "$tool" get "$lock" mongodb.repository.url)
distribution=$(python3 "$tool" get "$lock" mongodb.repository.distribution)
series=$(python3 "$tool" get "$lock" mongodb.repository.series)
key_url=$(python3 "$tool" get "$lock" mongodb.repository.signingKeyUrl)
expected_fingerprint=$(python3 "$tool" get "$lock" mongodb.repository.signingKeyFingerprint)
curl -fsSL "$key_url" -o /tmp/mongodb.asc
actual_fingerprint=$(gpg --show-keys --with-colons /tmp/mongodb.asc 2>/dev/null | awk -F: "\$1 == \"fpr\" {print \$10; exit}")
test "$actual_fingerprint" = "$expected_fingerprint"
gpg --dearmor --batch --yes -o "/usr/share/keyrings/mongodb-server-${series}.gpg" /tmp/mongodb.asc
printf "deb [arch=amd64 signed-by=/usr/share/keyrings/mongodb-server-%s.gpg] %s %s/mongodb-org/%s multiverse\n" \
  "$series" "$repository" "$distribution" "$series" >"/etc/apt/sources.list.d/mongodb-org-${series}.list"
apt-get update >/dev/null

plan=$(python3 "$tool" resolve-mongodb "$lock")
test "$(python3 -c "import json,sys; print(json.load(sys.stdin)[\"source\"])" <<<"$plan")" = repository
test "$(python3 -c "import json,sys; print(str(json.load(sys.stdin)[\"drift\"]).lower())" <<<"$plan")" = false
mapfile -t specs < <(python3 -c "import json,sys; data=json.load(sys.stdin); print(*(name + \"=\" + version for name,version in data[\"packages\"].items()), sep=\"\\n\")" <<<"$plan")
apt-get install -y --no-install-recommends "${specs[@]}" >/dev/null
resolved=$(python3 "$tool" resolve-mongodb "$lock")
test "$(python3 -c "import json,sys; print(json.load(sys.stdin)[\"source\"])" <<<"$resolved")" = installed
test "$(python3 -c "import json,sys; print(str(json.load(sys.stdin)[\"drift\"]).lower())" <<<"$resolved")" = false
echo "OK MongoDB preferred package set installed"
python3 -c "import json,sys; [print(name + \"=\" + version) for name,version in json.load(sys.stdin)[\"packages\"].items()]" <<<"$resolved"
'

echo "Provisioning install smoke passed; the disposable container was removed."
