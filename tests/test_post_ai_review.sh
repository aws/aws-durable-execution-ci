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

  if [[ "$joined" == *"repos/example/repository/pulls/42/files?per_page=100"* ]]; then
    printf '%s\n' \
      '[{"filename":"src/example.py","patch":"@@ -1 +1,2 @@\n-old_value = 1\n+new_value = 2\n+extra_value = 3\n"}]'
    return
  fi

  if [[ "$joined" == *"repos/example/repository/pulls/42/comments"* ]]; then
    if [[ "${FAIL_SECOND_INLINE_COMMENT:-false}" == "true" ]]; then
      local count=0
      if [[ -r "$GH_INLINE_COUNT_FILE" ]]; then
        count="$(cat "$GH_INLINE_COUNT_FILE")"
      fi
      count=$((count + 1))
      printf '%s\n' "$count" > "$GH_INLINE_COUNT_FILE"
      cat > "${GH_INLINE_PAYLOAD_LOG}.${count}"
      if [[ "$count" -eq 2 ]]; then
        return 1
      fi
      printf 'PRRC_partial_%s\n' "$count"
    else
      cat > "$GH_INLINE_PAYLOAD_LOG"
      printf '%s\n' "PRRC_new"
    fi
    return
  fi

  if [[ "$joined" == *"repos/example/repository/pulls/42"* ]]; then
    printf '%s\t%s\n' "$EXPECTED_BASE_SHA" "$EXPECTED_HEAD_SHA"
    return
  fi

  if [[ "$joined" == *"reviewThreads(first: 100"* ]]; then
    printf '%s\n' \
      '{"data":{"repository":{"pullRequest":{"reviewThreads":{"nodes":[],"pageInfo":{"hasNextPage":false,"endCursor":null}}}}}}'
    return
  fi

  if [[ "$joined" == *"pullRequest(number:"*"comments(first: 100"* ]]; then
    printf '%s\n' \
      '{"data":{"repository":{"pullRequest":{"comments":{"nodes":[],"pageInfo":{"hasNextPage":false,"endCursor":null}}}}}}'
    return
  fi

  if [[ "$joined" == *"repos/example/repository/issues/42/comments"* ]]; then
    for argument in "$@"; do
      if [[ "$argument" == body=* ]]; then
        printf '%s' "${argument#body=}" > "$GH_SUMMARY_BODY_LOG"
      fi
    done
    printf '%s\n' "IC_new_summary"
    return
  fi

  if [[ "$joined" == *"minimizeComment("* ]]; then
    if [[ "${EXPECT_PARTIAL_CLEANUP:-false}" != "true" ]]; then
      fail "publisher unexpectedly minimized a comment"
    fi
    for argument in "$@"; do
      if [[ "$argument" == id=* ]]; then
        printf '%s\n' "${argument#id=}" >> "$GH_MINIMIZED_LOG"
      fi
    done
    return
  fi

  fail "unexpected gh invocation: $joined"
}
export -f gh fail

export EXPECTED_BASE_SHA="1111111111111111111111111111111111111111"
export EXPECTED_HEAD_SHA="2222222222222222222222222222222222222222"
export GH_INLINE_COUNT_FILE="$test_dir/inline-count"
export GH_INLINE_PAYLOAD_LOG="$test_dir/inline-payload.json"
export GH_MINIMIZED_LOG="$test_dir/minimized-comments"
export GH_SUMMARY_BODY_LOG="$test_dir/summary-body.md"
export GH_TOKEN="test-token"
export GITHUB_REPOSITORY="example/repository"
export GITHUB_RUN_ATTEMPT="2"
export GITHUB_RUN_ID="123"
export GITHUB_SERVER_URL="https://github.example"
export PR_NUMBER="42"
export RUNNER_TEMP="$test_dir"
export PYTHONDONTWRITEBYTECODE="1"

review_file="$test_dir/review.json"
cat > "$review_file" <<'JSON'
{
  "summary": "One actionable finding.",
  "comments": [{
    "path": "src/example.py",
    "start_line": 1,
    "line": 1,
    "body": "The new value changes the behavior. Preserve the old value.",
    "has_suggestion": true,
    "suggestion": "new_value = 1"
  }]
}
JSON

bash "$repo_root/scripts/post_ai_review.sh" \
  claude \
  "$EXPECTED_BASE_SHA" \
  "$EXPECTED_HEAD_SHA" \
  "$review_file"

jq -e \
  --arg head_sha "$EXPECTED_HEAD_SHA" \
  '
    .commit_id == $head_sha and
    .path == "src/example.py" and
    .line == 1 and
    .side == "RIGHT" and
    (has("start_line") | not) and
    (.body | startswith("[ai-pr-review-inline-claude-123-2-published]: #\n")) and
    (.body | contains("\n**Claude AI review**\n\n")) and
    (.body | contains("```suggestion\nnew_value = 1\n```"))
  ' \
  "$GH_INLINE_PAYLOAD_LOG" > /dev/null ||
  fail "publisher sent an unexpected inline comment payload"

grep -Fx '<!-- ai-pr-review:inline-comments:claude -->' \
  "$GH_SUMMARY_BODY_LOG" > /dev/null ||
  fail "summary is missing the inline comment manifest"
grep -Fx '<!-- ai-pr-review:inline-comment:claude:PRRC_new -->' \
  "$GH_SUMMARY_BODY_LOG" > /dev/null ||
  fail "summary is missing the published inline comment ID"
grep -F 'One actionable finding.' "$GH_SUMMARY_BODY_LOG" > /dev/null ||
  fail "summary is missing the review overview"

failed_review_file="$test_dir/failed-review.json"
cat > "$failed_review_file" <<'JSON'
{
  "summary": "Two actionable findings.",
  "comments": [{
    "path": "src/example.py",
    "start_line": 1,
    "line": 1,
    "body": "The first value is incorrect.",
    "has_suggestion": false,
    "suggestion": ""
  }, {
    "path": "src/example.py",
    "start_line": 2,
    "line": 2,
    "body": "The second value is incorrect.",
    "has_suggestion": false,
    "suggestion": ""
  }]
}
JSON

if FAIL_SECOND_INLINE_COMMENT=true EXPECT_PARTIAL_CLEANUP=true \
  bash "$repo_root/scripts/post_ai_review.sh" \
    codex \
    "$EXPECTED_BASE_SHA" \
    "$EXPECTED_HEAD_SHA" \
    "$failed_review_file"; then
  fail "publisher unexpectedly succeeded after an inline comment API failure"
fi

actual_minimized="$(tr '\n' ' ' < "$GH_MINIMIZED_LOG")"
[[ "$actual_minimized" == "PRRC_partial_1 " ]] ||
  fail "publisher did not clean up its partial inline comments: $actual_minimized"

echo "PASS: trusted AI review publisher"
