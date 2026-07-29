#!/usr/bin/env bash

set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "usage: $0 <caller-prompt-path>" >&2
  exit 2
fi

custom_prompt_path="$1"
default_prompt_path="${GITHUB_WORKSPACE}/.ai-review-toolkit/.github/prompts/ai-pr-review.md"

if [[ -z "$custom_prompt_path" ]]; then
  prompt_path="$default_prompt_path"
else
  if [[ "$custom_prompt_path" == /* ]]; then
    echo "::error::The AI review prompt path must be relative to the caller repository." >&2
    exit 1
  fi
  if [[ "$custom_prompt_path" == *$'\n'* || "$custom_prompt_path" == *$'\r'* ]]; then
    echo "::error::The AI review prompt path contains a line break." >&2
    exit 1
  fi

  workspace_path="$(realpath "$GITHUB_WORKSPACE")"
  if ! prompt_path="$(
    realpath "${GITHUB_WORKSPACE}/${custom_prompt_path}" 2>/dev/null
  )"; then
    echo "::error::The AI review prompt does not exist: $custom_prompt_path" >&2
    exit 1
  fi

  case "$prompt_path" in
    "$workspace_path"/*)
      ;;
    *)
      echo "::error::The AI review prompt resolves outside the caller repository." >&2
      exit 1
      ;;
  esac
fi

if [[ ! -f "$prompt_path" || ! -r "$prompt_path" || ! -s "$prompt_path" ]]; then
  echo "::error::The AI review prompt must be a readable, non-empty file." >&2
  exit 1
fi

printf '%s\n' "$prompt_path"
