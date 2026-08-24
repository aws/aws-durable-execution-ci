#!/usr/bin/env bash

set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "usage: $0 <trusted-package-root>" >&2
  exit 2
fi

package_root="$1"
package_json="${package_root}/package.json"
package_lock="${package_root}/package-lock.json"
if [[ ! -f "$package_json" || ! -f "$package_lock" ]]; then
  echo "trusted package.json and package-lock.json are required" >&2
  exit 2
fi

install_root="${CODEX_INSTALL_ROOT:-/opt/aws-durable-execution-codex-cli}"
if [[ -n "${CODEX_INSTALL_ROOT:-}" ]]; then
  install -d -m 755 "$install_root"
else
  sudo install \
    -d \
    -m 755 \
    -o "$(id -un)" \
    -g "$(id -gn)" \
    "$install_root"
fi

install -m 644 "$package_json" "$package_lock" "$install_root/"
npm ci \
  --prefix "$install_root" \
  --omit=dev \
  --ignore-scripts \
  --no-audit \
  --no-fund
chmod -R a+rX "$install_root"

codex_bin_dir="${install_root}/node_modules/.bin"
if [[ ! -x "${codex_bin_dir}/codex" ]]; then
  echo "Codex CLI was not installed" >&2
  exit 1
fi
if [[ -z "${GITHUB_PATH:-}" ]]; then
  echo "GITHUB_PATH is required" >&2
  exit 2
fi
echo "$codex_bin_dir" >> "$GITHUB_PATH"
