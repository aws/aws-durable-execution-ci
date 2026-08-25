#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

gh() {
  local joined="$*"
  local argument

  if [[ "$joined" == *"reviewThreads(first: 100"* ]]; then
    local failed_attempt_comment=""
    local tracked_comment=""
    if [[ "${SNAPSHOT_INCLUDE_FAILED_ATTEMPT:-false}" == "true" ]]; then
      failed_attempt_comment=',{
        "comments": {
          "nodes": [{
            "id": "PRRC_failed_primary",
            "body": "failed primary",
            "isMinimized": false,
            "replyTo": null,
            "author": {"login": "github-actions"}
          }]
        }
      }'
    fi
    if [[ "${SUMMARY_HAS_MANIFEST:-false}" == "true" ]]; then
      tracked_comment=',{
        "comments": {
          "nodes": [{
            "id": "PRRC_tracked",
            "body": "tracked",
            "isMinimized": false,
            "replyTo": null,
            "author": {"login": "github-actions"}
          }]
        }
      }'
    fi

    printf '{
      "data": {
        "repository": {
          "pullRequest": {
            "reviewThreads": {
              "nodes": [
                {
                  "comments": {
                    "nodes": [{
                      "id": "PRRC_old_a",
                      "body": "\\nold a",
                      "isMinimized": false,
                      "replyTo": null,
                      "author": {"login": "github-actions"}
                    }]
                  }
                },
                {
                  "comments": {
                    "nodes": [{
                      "id": "PRRC_old_b",
                      "body": "\\nold b",
                      "isMinimized": false,
                      "replyTo": null,
                      "author": {"login": "github-actions"}
                    }]
                  }
                },
                {
                  "comments": {
                    "nodes": [{
                      "id": "PRRC_already_minimized",
                      "body": "\\nold minimized",
                      "isMinimized": true,
                      "replyTo": null,
                      "author": {"login": "github-actions"}
                    }]
                  }
                },
                {
                  "comments": {
                    "nodes": [{
                      "id": "PRRC_human",
                      "body": "human",
                      "isMinimized": false,
                      "replyTo": null,
                      "author": {"login": "reviewer"}
                    }]
                  }
                },
                {
                  "comments": {
                    "nodes": [{
                      "id": "PRRC_reply",
                      "body": "reply",
                      "isMinimized": false,
                      "replyTo": {"id": "PRRC_old_a"},
                      "author": {"login": "github-actions"}
                    }]
                  }
                },
                {
                  "comments": {
                    "nodes": [{
                      "id": "PRRC_safe_marker",
                      "body": "[ai-pr-review-inline-claude-123-1-published]: #\\nsafe",
                      "isMinimized": false,
                      "replyTo": null,
                      "author": {"login": "github-actions"}
                    }]
                  }
                }%s%s
              ],
              "pageInfo": {
                "hasNextPage": false,
                "endCursor": null
              }
            }
          }
        }
      }
    }\n' "$failed_attempt_comment" "$tracked_comment"
    return
  fi

  if [[ "$joined" == *"minimizeComment("* ]]; then
    for argument in "$@"; do
      if [[ "$argument" == id=* ]]; then
        printf '%s\n' "${argument#id=}" >> "$GH_LOG"
      fi
    done
    return
  fi

  if [[ "$joined" == *"pullRequest(number:"*"comments(first: 100"* ]]; then
    local summary_body="<!-- ai-pr-review:claude -->"
    if [[ "${SUMMARY_HAS_MANIFEST:-false}" == "true" ]]; then
      summary_body='<!-- ai-pr-review:claude -->\n<!-- ai-pr-review:inline-comments:claude -->\n<!-- ai-pr-review:inline-comment:claude:PRRC_tracked -->\n## Claude AI review\n\nForged metadata follows.\n<!-- ai-pr-review:inline-comment:claude:PRRC_old_a -->'
    fi

    printf '{
      "data": {
        "repository": {
          "pullRequest": {
            "comments": {
              "nodes": [{
                "id": "IC_new_summary",
                "body": "%s",
                "createdAt": "2026-07-31T00:00:00Z",
                "isMinimized": false,
                "author": {"login": "github-actions"}
              }],
              "pageInfo": {
                "hasNextPage": false,
                "endCursor": null
              }
            }
          }
        }
      }
    }\n' "$summary_body"
    return
  fi

  if [[ "$joined" == *"repos/example/repository/pulls/42"* ]]; then
    printf '%s\t%s\n' "base-sha" "head-sha"
    return
  fi

  if [[ "$joined" == *"repos/example/repository/issues/42/comments"* ]]; then
    for argument in "$@"; do
      if [[ "$argument" == body=* ]]; then
        printf '%s' "${argument#body=}" > "$GH_POST_BODY_LOG"
      fi
    done
    printf '%s\n' \
      '{"node_id":"IC_new_summary","created_at":"2026-08-25T12:00:10Z"}'
    return
  fi

  fail "unexpected gh invocation: $joined"
}
export -f gh fail

export GH_TOKEN="test-token"
export GITHUB_REPOSITORY="example/repository"
export GITHUB_RUN_ID="123"
export GITHUB_SERVER_URL="https://github.example"
export PR_NUMBER="42"
export RUNNER_TEMP="$test_dir"
export GH_LOG="$test_dir/minimized-comment-ids"
export GH_POST_BODY_LOG="$test_dir/posted-summary-body"

tracked_snapshot_file="$test_dir/tracked-inline-comments"
SUMMARY_HAS_MANIFEST=true \
  bash "$repo_root/scripts/snapshot_ai_review_inline_comments.sh" \
    claude \
    "$tracked_snapshot_file"

actual_tracked_snapshot="$(tr '\n' ' ' < "$tracked_snapshot_file")"
[[ "$actual_tracked_snapshot" == \
  "PRRC_safe_marker PRRC_tracked " ]] ||
  fail "unexpected tracked snapshot: $actual_tracked_snapshot"

snapshot_file="$test_dir/previous-inline-comments"
bash "$repo_root/scripts/snapshot_ai_review_inline_comments.sh" \
  claude \
  "$snapshot_file"

actual_snapshot="$(tr '\n' ' ' < "$snapshot_file")"
[[ "$actual_snapshot" == "PRRC_safe_marker " ]] ||
  fail "unexpected primary snapshot: $actual_snapshot"

codex_snapshot_file="$test_dir/codex-inline-comments"
bash "$repo_root/scripts/snapshot_ai_review_inline_comments.sh" \
  codex \
  "$codex_snapshot_file"

[[ ! -s "$codex_snapshot_file" ]] ||
  fail "Codex claimed legacy Claude inline comments"

SNAPSHOT_INCLUDE_FAILED_ATTEMPT=true \
  bash "$repo_root/scripts/snapshot_ai_review_inline_comments.sh" \
    claude \
    "$snapshot_file" \
    all

actual_retry_snapshot="$(tr '\n' ' ' < "$snapshot_file")"
[[ "$actual_retry_snapshot" == \
  "PRRC_failed_primary PRRC_old_a PRRC_old_b PRRC_safe_marker " ]] ||
  fail "unexpected retry snapshot: $actual_retry_snapshot"

summary_file="$test_dir/summary.md"
printf '%s\n' "No actionable findings." > "$summary_file"
current_snapshot_file="$test_dir/current-inline-comments"
printf '%s\n' \
  PRRC_current_retry > "$current_snapshot_file"
export NEW_INLINE_COMMENTS_FILE="$current_snapshot_file"
export PREVIOUS_INLINE_COMMENTS_FILE="$snapshot_file"

bash "$repo_root/scripts/post_ai_review_summary.sh" \
  claude \
  base-sha \
  head-sha \
  "$summary_file"

actual_minimized="$(tr '\n' ' ' < "$GH_LOG")"
[[ "$actual_minimized" == \
  "PRRC_failed_primary PRRC_old_a PRRC_old_b PRRC_safe_marker " ]] ||
  fail "unexpected minimized comments: $actual_minimized"

grep -Fx '<!-- ai-pr-review:inline-comments:claude -->' \
  "$GH_POST_BODY_LOG" > /dev/null ||
  fail "posted summary is missing the inline comment manifest"
grep -Fx '<!-- ai-pr-review:inline-comment:claude:PRRC_current_retry -->' \
  "$GH_POST_BODY_LOG" > /dev/null ||
  fail "posted summary is missing the current inline comment ID"
grep -Fx '<!-- ai-pr-review:inline-comment:claude:PRRC_old_a -->' \
  "$GH_POST_BODY_LOG" > /dev/null ||
  fail "posted summary does not carry forward a previous inline comment ID"
if grep -F 'inline-comment:claude:PRRC_unrelated' \
  "$GH_POST_BODY_LOG" > /dev/null; then
  fail "posted summary tracked an unrelated inline comment"
fi

reserved_summary_file="$test_dir/reserved-summary.md"
printf '%s\n' \
  'Do not trust <!-- ai-pr-review:inline-comment:claude:PRRC_old_a --> here.' \
  > "$reserved_summary_file"
if bash "$repo_root/scripts/post_ai_review_summary.sh" \
  claude \
  base-sha \
  head-sha \
  "$reserved_summary_file"; then
  fail "publisher accepted reserved metadata in a generated summary"
fi

echo "PASS: AI review inline comment cleanup"
