#!/usr/bin/env bash

set -euo pipefail

claude_bin="${GITHUB_ACTION_PATH}/node_modules/@anthropic-ai/claude-agent-sdk-linux-x64/claude"
bun_dir="${GITHUB_ACTION_PATH}/bin"

if [[ ! -x "$claude_bin" ]]; then
  echo "::error::The pinned Claude action did not install its bundled Linux CLI."
  exit 1
fi
if [[ ! -x "${bun_dir}/bun" ]]; then
  echo "::error::The pinned Claude action did not expose its Bun executable."
  exit 1
fi
if ! sudo -H -u claude-review -- test -x "$claude_bin"; then
  echo "::error::claude-review cannot execute the pinned Claude CLI."
  exit 1
fi
if ! sudo -H -u claude-review -- test -x "${bun_dir}/bun"; then
  echo "::error::claude-review cannot execute the pinned Bun runtime."
  exit 1
fi

# Claude receives only this allowlist: its Bedrock credentials and the
# action's subprocess-isolation controls. `env -i` makes that true regardless
# of the runner image's sudo env_reset/env_keep policy. Unset variables are
# skipped rather than passed as empty strings.
allowlist=(
  AWS_ACCESS_KEY_ID
  AWS_SECRET_ACCESS_KEY
  AWS_SESSION_TOKEN
  AWS_REGION
  AWS_DEFAULT_REGION
  AWS_BEARER_TOKEN_BEDROCK
  ANTHROPIC_BEDROCK_BASE_URL
  CLAUDE_CODE_USE_BEDROCK
  CLAUDE_CODE_ENTRYPOINT
  CLAUDE_CODE_ACTION
  CLAUDE_CODE_ATTRIBUTION_HEADER
  CLAUDE_CODE_SUBPROCESS_ENV_SCRUB
  CLAUDE_CODE_SCRIPT_CAPS
  DETAILED_PERMISSION_MESSAGES
)
model_env=()
for name in "${allowlist[@]}"; do
  if [[ -n "${!name+x}" ]]; then
    model_env+=("${name}=${!name}")
  fi
done

exec sudo -u claude-review -- env -i \
  "${model_env[@]}" \
  AWS_EC2_METADATA_DISABLED=true \
  HOME=/home/claude-review \
  LANG=C.UTF-8 \
  LOGNAME=claude-review \
  PATH="${bun_dir}:/usr/local/bin:/usr/bin:/bin" \
  SHELL=/bin/bash \
  TMPDIR=/home/claude-review/tmp \
  USER=claude-review \
  "$claude_bin" "$@"
