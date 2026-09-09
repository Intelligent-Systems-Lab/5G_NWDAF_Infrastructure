#!/usr/bin/env bash
set -euo pipefail

expected_identity=${1:?usage: webconsole-build.sh expected-identity source-archive source-revision}
source_archive=${2:?usage: webconsole-build.sh expected-identity source-archive source-revision}
source_revision=${3:?usage: webconsole-build.sh expected-identity source-archive source-revision}
runtime_root=/var/lib/5g-nwdaf-infrastructure/webconsole
release_root=$runtime_root/releases
current=$runtime_root/current
node_major=20
yarn_version=4.1.0

test "$(id -u)" -eq 0 || { echo "WebConsole build requires root" >&2; exit 1; }
[[ "$expected_identity" =~ ^[0-9a-f]{64}$ ]] || { echo "invalid WebConsole artifact identity" >&2; exit 2; }
[[ "$source_revision" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid WebConsole source revision" >&2; exit 2; }

if [ -x "$current/webconsole" ] && [ -f "$current/public/index.html" ] && \
   [ "$(cat "$current/identity" 2>/dev/null || true)" = "$expected_identity" ]; then
  echo "WEBCONSOLE ARTIFACT state=reused identity=$expected_identity revision=$source_revision"
  exit 0
fi

test -f "$source_archive" || { echo "WebConsole source archive is missing" >&2; exit 1; }

if ! command -v node >/dev/null 2>&1 || [ "$(node --version | sed -E 's/^v([0-9]+).*/\1/')" != "$node_major" ]; then
  keyring=/usr/share/keyrings/nodesource.gpg
  temporary_key=$(mktemp)
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key -o "$temporary_key"
  gpg --dearmor --yes -o "$keyring" "$temporary_key"
  rm -f "$temporary_key"
  printf '%s\n' "deb [arch=amd64 signed-by=$keyring] https://deb.nodesource.com/node_${node_major}.x nodistro main" \
    >/etc/apt/sources.list.d/nodesource.list
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nodejs
fi
test "$(node --version | sed -E 's/^v([0-9]+).*/\1/')" = "$node_major" || {
  echo "Node.js ${node_major}.x is required" >&2
  exit 1
}
command -v corepack >/dev/null 2>&1 || { echo "Node.js installation did not provide Corepack" >&2; exit 1; }
corepack enable

install -d -o 5g-nwdaf -g 5g-nwdaf "$runtime_root" "$release_root" "$runtime_root/cache/corepack"
build_root=$(mktemp -d "$runtime_root/.build.XXXXXX")
release_stage=$release_root/."$expected_identity".$$
cleanup() {
  rm -rf "$build_root" "$release_stage" "$source_archive"
}
trap cleanup EXIT
tar -C "$build_root" -xzf "$source_archive"
chown -R 5g-nwdaf:5g-nwdaf "$build_root" "$runtime_root/cache"

runuser -u 5g-nwdaf -- env \
  HOME=/var/lib/5g-nwdaf-infrastructure \
  COREPACK_HOME="$runtime_root/cache/corepack" \
  corepack prepare "yarn@$yarn_version" --activate
runuser -u 5g-nwdaf -- env \
  HOME=/var/lib/5g-nwdaf-infrastructure \
  COREPACK_HOME="$runtime_root/cache/corepack" \
  sh -c 'cd "$1" && exec corepack yarn install --immutable' sh "$build_root/frontend"
runuser -u 5g-nwdaf -- env \
  HOME=/var/lib/5g-nwdaf-infrastructure \
  COREPACK_HOME="$runtime_root/cache/corepack" \
  sh -c 'cd "$1" && exec corepack yarn build' sh "$build_root/frontend"
runuser -u 5g-nwdaf -- env \
  PATH=/usr/local/go/bin:/usr/local/bin:/usr/bin \
  CGO_ENABLED=0 \
  go -C "$build_root" build -trimpath -o "$build_root/webconsole" ./server.go

test -x "$build_root/webconsole" || { echo "WebConsole server build did not produce a binary" >&2; exit 1; }
test -f "$build_root/frontend/build/index.html" || { echo "WebConsole frontend build is incomplete" >&2; exit 1; }
install -d "$release_stage/public"
install -m 0755 "$build_root/webconsole" "$release_stage/webconsole"
cp -a "$build_root/frontend/build/." "$release_stage/public/"
printf '%s\n' "$expected_identity" >"$release_stage/identity"
printf '%s\n' "$source_revision" >"$release_stage/source-revision"
printf '%s\n' "node=$(/usr/bin/node --version)" "yarn=$yarn_version" >"$release_stage/toolchain"
chmod -R a+rX "$release_stage"
mv "$release_stage" "$release_root/$expected_identity"
temporary_link=$runtime_root/.current.$$
ln -s "$release_root/$expected_identity" "$temporary_link"
mv -Tf "$temporary_link" "$current"

echo "WEBCONSOLE ARTIFACT state=built identity=$expected_identity revision=$source_revision"
