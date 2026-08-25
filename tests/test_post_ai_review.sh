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

  if [[ "$joined" == *"git/ref/heads/ai-review-telemetry-v1"* ]]; then
    if [[ -r "$TELEMETRY_REF_FILE" ]]; then
      printf '%s\n' \
        '{"object":{"sha":"6666666666666666666666666666666666666666"}}'
      return
    fi
    echo "gh: Not Found (HTTP 404)" >&2
    return 1
  fi

  if [[ "$joined" == *"git/trees/"*"?recursive=1"* ]]; then
    printf '%s\n' '{"truncated":false,"tree":[]}'
    return
  fi

  if [[ "$joined" == *"git/commits/6666666666666666666666666666666666666666"* ]]; then
    printf '%s\n' \
      '{"tree":{"sha":"5555555555555555555555555555555555555555"}}'
    return
  fi

  if [[ "$joined" == *"--method POST"*"git/blobs"* ]]; then
    cat > /dev/null
    printf '%s\n' '{"sha":"4444444444444444444444444444444444444444"}'
    return
  fi

  if [[ "$joined" == *"--method POST"*"git/trees"* ]]; then
    cat > /dev/null
    printf '%s\n' '{"sha":"5555555555555555555555555555555555555555"}'
    return
  fi

  if [[ "$joined" == *"--method POST"*"git/commits"* ]]; then
    cat > /dev/null
    printf '%s\n' '{"sha":"6666666666666666666666666666666666666666"}'
    return
  fi

  if [[ "$joined" == *"--method POST"*"git/refs"* ]]; then
    cat > /dev/null
    touch "$TELEMETRY_REF_FILE"
    printf '%s\n' '{}'
    return
  fi

  if [[ "$joined" == *"--method PATCH"*"git/refs/heads/ai-review-telemetry-v1"* ]]; then
    cat > /dev/null
    printf '%s\n' '{}'
    return
  fi

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
      printf '{"node_id":"PRRC_partial_%s","created_at":"2026-08-25T12:00:0%sZ"}\n' \
        "$count" "$count"
    else
      cat > "$GH_INLINE_PAYLOAD_LOG"
      printf '%s\n' \
        '{"node_id":"PRRC_new","created_at":"2026-08-25T12:00:01Z"}'
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
    printf '%s\n' \
      '{"node_id":"IC_new_summary","created_at":"2026-08-25T12:00:10Z"}'
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
export GITHUB_REPOSITORY_ID="123"
export GITHUB_RUN_ATTEMPT="2"
export GITHUB_RUN_ID="123"
export GITHUB_SERVER_URL="https://github.example"
export GITHUB_WORKFLOW_SHA="3333333333333333333333333333333333333333"
export PR_NUMBER="42"
export REVIEW_MODEL="test-model"
export REVIEW_REASONING_EFFORT="high"
export RUNNER_TEMP="$test_dir"
export TELEMETRY_REF_FILE="$test_dir/telemetry-ref"
export PYTHONDONTWRITEBYTECODE="1"

review_file="$test_dir/review.json"
cat > "$review_file" <<'JSON'
{
  "summary": "One actionable finding.",
  "comments": [{
    "finding_key": "src/example.py::value::behavior-change",
    "prior_finding_id": "",
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
    (.body | contains("<!-- ai-pr-review:finding:claude:arf_v1_")) and
    (.body | contains("\n**Claude AI review · Finding `arf_v1_")) and
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
    "finding_key": "src/example.py::first::incorrect-value",
    "prior_finding_id": "",
    "path": "src/example.py",
    "start_line": 1,
    "line": 1,
    "body": "The first value is incorrect.",
    "has_suggestion": false,
    "suggestion": ""
  }, {
    "finding_key": "src/example.py::second::incorrect-value",
    "prior_finding_id": "",
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
