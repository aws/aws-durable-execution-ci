#!/usr/bin/env bash

set -euo pipefail

implementation_user="codex-implement"
home_dir="/home/${implementation_user}"

if [[ -z "${GITHUB_WORKSPACE:-}" || ! -d "$GITHUB_WORKSPACE" ]]; then
  echo "GITHUB_WORKSPACE must name an existing directory" >&2
  exit 2
fi

sudo adduser \
  --system \
  --home "$home_dir" \
  --shell /bin/bash \
  --group "$implementation_user"

sudo install \
  -d \
  -m 700 \
  -o "$implementation_user" \
  -g "$implementation_user" \
  "${home_dir}/.codex"

# Keep Git metadata read-only while allowing Codex to edit the worktree.
sudo chown -R "${implementation_user}:${implementation_user}" "$GITHUB_WORKSPACE"
sudo chown "runner:${implementation_user}" "$GITHUB_WORKSPACE"
sudo chown -R "runner:${implementation_user}" "$GITHUB_WORKSPACE/.git"
sudo chmod -R u+rwX,go-rwx "$GITHUB_WORKSPACE"
sudo chmod -R g-w,o-rwx "$GITHUB_WORKSPACE/.git"
sudo chmod -R g+rX "$GITHUB_WORKSPACE/.git"
sudo chmod 1770 "$GITHUB_WORKSPACE"

if ! sudo -u "$implementation_user" test -r "$GITHUB_WORKSPACE/.git/HEAD"; then
  echo "$implementation_user cannot read the repository metadata" >&2
  exit 1
fi

if ! sudo -u "$implementation_user" test -w "$GITHUB_WORKSPACE"; then
  echo "$implementation_user cannot write the repository worktree" >&2
  exit 1
fi
