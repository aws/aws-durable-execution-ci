#!/usr/bin/env bash

set -euo pipefail

triage_user="codex-triage"
home_dir="/home/${triage_user}"

if [[ -z "${GITHUB_WORKSPACE:-}" || ! -d "$GITHUB_WORKSPACE" ]]; then
  echo "GITHUB_WORKSPACE must name an existing directory" >&2
  exit 2
fi

sudo adduser \
  --system \
  --home "$home_dir" \
  --shell /bin/bash \
  --group "$triage_user"

sudo install \
  -d \
  -m 700 \
  -o "$triage_user" \
  -g "$triage_user" \
  "${home_dir}/.codex"

# The model receives its complete context over stdin. Deny it direct access to
# the checked-out workflow, context snapshot, validation code, and prompt.
chmod o-rwx "$GITHUB_WORKSPACE"
cd /tmp
for protected_path in \
  "$GITHUB_WORKSPACE/README.md" \
  "$GITHUB_WORKSPACE/.github/prompts/issue-triage.md" \
  "$GITHUB_WORKSPACE/.ai-issue-triage-context/context.json"
do
  if sudo -u "$triage_user" test -r "$protected_path"; then
    echo "$triage_user can read the trusted workspace" >&2
    exit 1
  fi
done
