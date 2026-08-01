#!/usr/bin/env bash

set -euo pipefail

if [[ "$#" -ne 4 ]]; then
  echo "usage: $0 <claude|codex> <expected-base-sha> <expected-head-sha> <review-file>" >&2
  exit 2
fi

reviewer="$1"
expected_base_sha="$2"
expected_head_sha="$3"
review_file="$4"

case "$reviewer" in
  claude | codex)
    ;;
  *)
    echo "unsupported AI reviewer: $reviewer" >&2
    exit 2
    ;;
esac

: "${GH_TOKEN:?GH_TOKEN must be set}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must be set}"
: "${GITHUB_RUN_ATTEMPT:?GITHUB_RUN_ATTEMPT must be set}"
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID must be set}"
: "${GITHUB_SERVER_URL:?GITHUB_SERVER_URL must be set}"
: "${PR_NUMBER:?PR_NUMBER must be set}"

if [[ ! -r "$review_file" ]]; then
  echo "AI review output is not readable: $review_file" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
temp_dir="$(mktemp -d "${RUNNER_TEMP:-/tmp}/post-ai-review.XXXXXX")"
files_pages="${temp_dir}/pr-files-pages.json"
files_file="${temp_dir}/pr-files.json"
prepared_review_file="${temp_dir}/prepared-review.json"
previous_inline_comments_file="${temp_dir}/previous-inline-comments"
new_inline_comments_file="${temp_dir}/new-inline-comments"
summary_file="${temp_dir}/summary.md"
publication_complete=false
touch "$new_inline_comments_file"

minimize_comment() {
  local comment_id="$1"

  gh api graphql \
    -F id="$comment_id" \
    -f query='
      mutation($id: ID!) {
        minimizeComment(
          input: {
            subjectId: $id,
            classifier: OUTDATED
          }
        ) {
          minimizedComment {
            isMinimized
          }
        }
      }
    ' > /dev/null
}

cleanup() {
  local status="$?"
  trap - EXIT

  if [[ "$publication_complete" != "true" && -s "$new_inline_comments_file" ]]; then
    while IFS= read -r comment_id; do
      [[ -n "$comment_id" ]] || continue
      minimize_comment "$comment_id" ||
        echo "::warning::Failed to minimize a partially published AI review comment."
    done < "$new_inline_comments_file"
  fi

  rm -rf "$temp_dir"
  exit "$status"
}
trap cleanup EXIT

verify_current_revision() {
  local current_revision
  local current_base_sha
  local current_head_sha

  current_revision="$(
    gh api \
      "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" \
      --jq '.base.sha + "\t" + .head.sha'
  )"
  IFS=$'\t' read -r current_base_sha current_head_sha <<< "$current_revision"
  if [[
    "$current_base_sha" != "$expected_base_sha" ||
    "$current_head_sha" != "$expected_head_sha"
  ]]; then
    echo "::error::The PR changed while it was being reviewed."
    return 1
  fi
}

verify_current_revision

bash "${script_dir}/snapshot_ai_review_inline_comments.sh" \
  "$reviewer" \
  "$previous_inline_comments_file"

gh api \
  --paginate \
  -H "Accept: application/vnd.github+json" \
  "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}/files?per_page=100" \
  > "$files_pages"
jq -se '
  if all(.[]; type == "array") then
    add
  else
    error("GitHub returned an invalid PR files payload")
  end
' "$files_pages" > "$files_file"

verify_current_revision

python3 "${script_dir}/prepare_ai_review_comments.py" \
  --review "$review_file" \
  --files "$files_file" \
  --output "$prepared_review_file" \
  --reviewer "$reviewer" \
  --run-id "$GITHUB_RUN_ID" \
  --run-attempt "$GITHUB_RUN_ATTEMPT" \
  --expected-head-sha "$expected_head_sha"

while IFS= read -r comment_payload; do
  verify_current_revision
  comment_id="$(
    gh api \
      --method POST \
      "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}/comments" \
      --input - \
      --jq .node_id \
      <<< "$comment_payload"
  )"
  if [[ -z "$comment_id" ]]; then
    echo "::error::GitHub did not return the new inline comment ID."
    exit 1
  fi
  printf '%s\n' "$comment_id" >> "$new_inline_comments_file"
done < <(jq -c '.comments[]' "$prepared_review_file")

verify_current_revision
jq -r .summary "$prepared_review_file" > "$summary_file"

PREVIOUS_INLINE_COMMENTS_FILE="$previous_inline_comments_file" \
NEW_INLINE_COMMENTS_FILE="$new_inline_comments_file" \
  bash "${script_dir}/post_ai_review_summary.sh" \
    "$reviewer" \
    "$expected_base_sha" \
    "$expected_head_sha" \
    "$summary_file"

publication_complete=true
