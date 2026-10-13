#!/usr/bin/env bash

set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "usage: $0 <claude-review|codex-review>" >&2
  exit 2
fi

review_user="$1"
home_dir="/home/${review_user}"

if [[ -z "${GITHUB_WORKSPACE:-}" || ! -d "$GITHUB_WORKSPACE" ]]; then
  echo "GITHUB_WORKSPACE must name an existing directory" >&2
  exit 2
fi

case "$review_user" in
  claude-review)
    private_dirs=("${home_dir}/.claude" "${home_dir}/tmp")
    ;;
  codex-review)
    private_dirs=("${home_dir}/.codex")
    ;;
  *)
    echo "unsupported AI review user: $review_user" >&2
    exit 2
    ;;
esac

sudo adduser \
  --system \
  --home "$home_dir" \
  --shell /bin/bash \
  --group "$review_user"

sudo install \
  -d \
  -m 700 \
  -o "$review_user" \
  -g "$review_user" \
  "${private_dirs[@]}"

# The review user must traverse every directory above the workspace. Hosted
# runners keep it under a private /home/runner; CodeBuild runners keep it
# under a world-traversable /codebuild tree. Grant execute-only ACLs on exactly
# the ancestors that block traversal, checking top-down so each test only
# depends on ancestors already handled.
ancestor="/"
IFS=/ read -r -a ancestor_parts <<< "$(dirname "${GITHUB_WORKSPACE#/}")"
for part in "${ancestor_parts[@]}"; do
  ancestor="${ancestor%/}/${part}"
  if ! sudo -u "$review_user" test -x "$ancestor"; then
    sudo setfacl -m "u:${review_user}:--x" "$ancestor"
  fi
done

# The workspace stays owned by the account running the job (`runner` on
# hosted runners, `root` on CodeBuild); only group access is opened.
sudo chown -R "$(id -un):${review_user}" "$GITHUB_WORKSPACE"
sudo chmod -R g-w,o-rwx "$GITHUB_WORKSPACE"
sudo chmod -R g+rX "$GITHUB_WORKSPACE"

if ! sudo -u "$review_user" test -r "$GITHUB_WORKSPACE/README.md"; then
  echo "$review_user cannot read the trusted workspace" >&2
  exit 1
fi
