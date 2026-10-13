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

# The implementation user must traverse every directory above the workspace.
# Hosted runners keep it under a private /home/runner; CodeBuild runners keep
# it under a world-traversable /codebuild tree. Grant execute-only ACLs on
# exactly the ancestors that block traversal, checking top-down so each test
# only depends on ancestors already handled.
ancestor="/"
IFS=/ read -r -a ancestor_parts <<< "$(dirname "${GITHUB_WORKSPACE#/}")"
for part in "${ancestor_parts[@]}"; do
  ancestor="${ancestor%/}/${part}"
  if ! sudo -u "$implementation_user" test -x "$ancestor"; then
    sudo setfacl -m "u:${implementation_user}:--x" "$ancestor"
  fi
done

# Keep Git metadata read-only while allowing Codex to edit the worktree. The
# workspace root and .git stay owned by the account running the job (`runner`
# on hosted runners, `root` on CodeBuild).
job_user="$(id -un)"
sudo chown -R "${implementation_user}:${implementation_user}" "$GITHUB_WORKSPACE"
sudo chown "${job_user}:${implementation_user}" "$GITHUB_WORKSPACE"
sudo chown -R "${job_user}:${implementation_user}" "$GITHUB_WORKSPACE/.git"
sudo chmod -R u+rwX,go-rwx "$GITHUB_WORKSPACE"
sudo chmod -R g-w,o-rwx "$GITHUB_WORKSPACE/.git"
sudo chmod -R g+rX "$GITHUB_WORKSPACE/.git"
sudo chmod 1770 "$GITHUB_WORKSPACE"

sudo -u "$implementation_user" env HOME="$home_dir" \
  git config --global --add safe.directory "$GITHUB_WORKSPACE"

if ! sudo -u "$implementation_user" test -r "$GITHUB_WORKSPACE/.git/HEAD"; then
  echo "$implementation_user cannot read the repository metadata" >&2
  exit 1
fi

if ! sudo -u "$implementation_user" env \
  HOME="$home_dir" \
  GIT_OPTIONAL_LOCKS=0 \
  git -C "$GITHUB_WORKSPACE" rev-parse --verify HEAD >/dev/null; then
  echo "$implementation_user cannot inspect the Git repository" >&2
  exit 1
fi

if ! sudo -u "$implementation_user" test -w "$GITHUB_WORKSPACE"; then
  echo "$implementation_user cannot write the repository worktree" >&2
  exit 1
fi
