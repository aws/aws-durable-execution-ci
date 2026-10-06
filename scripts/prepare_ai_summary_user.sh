#!/usr/bin/env bash

set -euo pipefail

summary_user="codex-summary"
home_dir="/home/${summary_user}"

if [[ -z "${GITHUB_WORKSPACE:-}" || ! -d "$GITHUB_WORKSPACE" ]]; then
  echo "GITHUB_WORKSPACE must name an existing directory" >&2
  exit 2
fi

sudo adduser \
  --system \
  --home "$home_dir" \
  --shell /bin/bash \
  --group "$summary_user"

sudo install \
  -d \
  -m 700 \
  -o "$summary_user" \
  -g "$summary_user" \
  "${home_dir}/.codex"

# Codex runs through sudo, so preserve its AWS SDK credential chain for the
# account running the job (`runner` on hosted runners, `root` on CodeBuild).
# Never add AWS_CONTAINER_CREDENTIALS_RELATIVE_URI here: on CodeBuild that
# path is what keeps the project role out of the model's reach.
printf '%s\n' \
  "Defaults:$(id -un) env_keep += \"AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_REGION AWS_DEFAULT_REGION\"" \
  | sudo tee /etc/sudoers.d/codex-summary-env >/dev/null
sudo chmod 440 /etc/sudoers.d/codex-summary-env
sudo visudo -cf /etc/sudoers.d/codex-summary-env

current_userns="$(
  sysctl -n kernel.unprivileged_userns_clone 2>/dev/null || true
)"
if [[ -n "$current_userns" && "$current_userns" != "1" ]]; then
  sudo sysctl -w kernel.unprivileged_userns_clone=1
fi

current_apparmor="$(
  sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || true
)"
if [[ -n "$current_apparmor" && "$current_apparmor" != "0" ]]; then
  sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
fi

# The model receives its complete context over stdin. Deny direct access to the
# trusted workflow checkout and its sanitization code.
chmod o-rwx "$GITHUB_WORKSPACE"
cd /tmp
if sudo -u "$summary_user" test -r \
  "$GITHUB_WORKSPACE/.notification-toolkit/scripts/summarize_notification.py"
then
  echo "$summary_user can read the trusted workspace" >&2
  exit 1
fi
