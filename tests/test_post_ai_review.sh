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
    if [[ "${GH_TELEMETRY_READ_ERROR:-false}" == "true" ]]; then
      echo "gh: Service Unavailable (HTTP 503)" >&2
      return 1
    fi
    if [[ -r "${GH_TREE_FIXTURE:-}" ]]; then
      cat "$GH_TREE_FIXTURE"
    else
      printf '%s\n' '{"truncated":false,"tree":[]}'
    fi
    return
  fi

  if [[ "$joined" == *"git/blobs/"* ]]; then
    for argument in "$@"; do
      if [[ "$argument" == repos/*/git/blobs/* ]]; then
        cat "${GH_BLOB_FIXTURE_DIR}/${argument##*/}.json"
        return
      fi
    done
    fail "telemetry blob request has no endpoint"
  fi

  if [[ "$joined" == *"git/commits/6666666666666666666666666666666666666666"* ]]; then
    printf '%s\n' \
      '{"tree":{"sha":"5555555555555555555555555555555555555555"}}'
    return
  fi

  if [[ "$joined" == *"--method POST"*"git/blobs"* ]]; then
    command python3 -c '
import base64, json, sys
value = json.load(sys.stdin)
content = base64.b64decode(value["content"]).decode()
with open(sys.argv[1], "a") as output:
    output.write(content)
' "$GH_BLOB_UPLOAD_LOG"
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
    printf '%s\n' inline >> "$GH_REQUEST_LOG"
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
    if [[ -n "${CHANGED_REVISION_AFTER:-}" ]]; then
      local checks=0
      if [[ -r "$GH_REVISION_COUNT_FILE" ]]; then
        checks="$(cat "$GH_REVISION_COUNT_FILE")"
      fi
      checks=$((checks + 1))
      printf '%s\n' "$checks" > "$GH_REVISION_COUNT_FILE"
      if [[ "$checks" -ge "$CHANGED_REVISION_AFTER" ]]; then
        printf '%s\t%s\n' "$EXPECTED_BASE_SHA" "9999999999999999999999999999999999999999"
        return
      fi
    fi
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
    printf '%s\n' summary >> "$GH_REQUEST_LOG"
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
export GH_TREE_FIXTURE="$test_dir/telemetry-tree.json"
export GH_BLOB_FIXTURE_DIR="$test_dir/telemetry-blobs"
export GH_BLOB_UPLOAD_LOG="$test_dir/telemetry-uploads.jsonl"
export GH_REQUEST_LOG="$test_dir/publication-requests"
export GH_REVISION_COUNT_FILE="$test_dir/revision-count"
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

write_telemetry_fixture() {
  command python3 - "$repo_root" "$test_dir" "$1" "$2" "${3:-}" <<'PY'
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))
from ai_review_telemetry import (
    build_record, canonical_json_bytes, git_blob_sha, record_path, stable_identifier,
)

directory = Path(sys.argv[2])
reviewer, outcome, newer_outcome = sys.argv[3:]
finding_key = "src/example.py::value::behavior-change"
finding_id, finding_digest = stable_identifier("arf", {
    "repository_id": 123, "pull_request_number": 42,
    "reviewer": reviewer, "finding_key": finding_key,
})
observation_id, _ = stable_identifier("aro", {"fixture": reviewer})
observation = build_record(
    record_type="finding_observed",
    identity={"observation_id": observation_id},
    repository_id=123, repository="example/repository", pull_request_number=42,
    recorded_at="2026-08-25T11:00:00Z",
    data={
        "finding_id": finding_id, "finding_digest": finding_digest,
        "reviewer": reviewer, "identity_key": finding_key,
        "finding_key": finding_key, "path": "src/example.py",
        "body": "The value changes behavior.", "published_at": "2026-08-25T11:00:00Z",
        "reviewed_head_sha": "2" * 40,
    },
)
records = [observation]
for position, status in enumerate(filter(None, (outcome, newer_outcome))):
    timestamp = f"2026-08-25T11:0{position}:00Z"
    records.append(build_record(
        record_type="finding_verdict",
        identity={"command": f"{reviewer}-{position}-{status}"},
        repository_id=123, repository="example/repository", pull_request_number=42,
        recorded_at=timestamp,
        data={
            "finding_id": finding_id, "reviewer": reviewer,
            "outcome": status, "command_created_at": timestamp,
        },
    ))
tree = []
blob_directory = directory / "telemetry-blobs"
blob_directory.mkdir(exist_ok=True)
for record in records:
    content = canonical_json_bytes(record)
    sha = git_blob_sha(content)
    tree.append({"path": record_path(record), "type": "blob", "sha": sha})
    (blob_directory / f"{sha}.json").write_text(json.dumps({
        "encoding": "base64", "content": base64.b64encode(content).decode(),
    }))
(directory / "telemetry-tree.json").write_text(json.dumps({
    "truncated": False, "tree": tree,
}))
(directory / "fixture-finding-id").write_text(finding_id)
PY
}

reset_publication_logs() {
  rm -f "$GH_REQUEST_LOG" "$GH_INLINE_PAYLOAD_LOG" "$GH_SUMMARY_BODY_LOG" \
    "$GH_BLOB_UPLOAD_LOG" "$GH_REVISION_COUNT_FILE" "$GH_MINIMIZED_LOG"
}

fixture_review_file="$test_dir/fixture-review.json"
for reviewer in claude codex; do
  for outcome in deferred false-positive out-of-scope accepted already-fixed; do
    write_telemetry_fixture "$reviewer" "$outcome"
    finding_id="$(cat "$test_dir/fixture-finding-id")"
    jq --arg finding_id "$finding_id" \
      '.comments[0].prior_finding_id = $finding_id' \
      "$review_file" > "$fixture_review_file"
    reset_publication_logs
    bash "$repo_root/scripts/post_ai_review.sh" \
      "$reviewer" "$EXPECTED_BASE_SHA" "$EXPECTED_HEAD_SHA" "$fixture_review_file"

    case "$outcome" in
      deferred | false-positive | out-of-scope)
        [[ "$(cat "$GH_REQUEST_LOG")" == "summary" ]] ||
          fail "$reviewer posted a suppressed finding for $outcome"
        grep -F -- "- \`$finding_id\` — $outcome (maintainer verdict)" \
          "$GH_SUMMARY_BODY_LOG" > /dev/null ||
          fail "$reviewer summary omitted its suppressed repeat"
        jq -se --arg status "$outcome" '
          [.[] | select(.record_type == "finding_observed")] as $observed |
          ($observed | length) == 1 and
          $observed[0].data.suppressed == true and
          $observed[0].data.suppression_status == $status and
          $observed[0].data.comment_node_id == "" and
          $observed[0].data.suggestion_id == "" and
          $observed[0].recorded_at == "2026-08-25T12:00:10Z" and
          ([.[] | select(has("replacement"))] | length) == 0
        ' "$GH_BLOB_UPLOAD_LOG" > /dev/null ||
          fail "$reviewer recorded invalid suppressed telemetry"
        ;;
      accepted | already-fixed)
        [[ "$(tr '\n' ' ' < "$GH_REQUEST_LOG")" == "inline summary " ]] ||
          fail "$reviewer failed to post $outcome"
        ;;
    esac
  done

  for transition in suppress reverse; do
    if [[ "$transition" == "suppress" ]]; then
      initial_outcome=accepted
      final_outcome=deferred
    else
      initial_outcome=deferred
      final_outcome=accepted
    fi
    write_telemetry_fixture "$reviewer" "$initial_outcome"
    generation_catalog="$test_dir/generation-catalog.json"
    command python3 "$repo_root/scripts/load_ai_review_findings.py" \
      --repository "$GITHUB_REPOSITORY" --pull-request-number "$PR_NUMBER" \
      --reviewer "$reviewer" --output "$generation_catalog"
    jq -e --arg status "$initial_outcome" '.[0].status == $status' \
      "$generation_catalog" > /dev/null ||
      fail "generation did not load its initial verdict"
    write_telemetry_fixture "$reviewer" "$initial_outcome" "$final_outcome"
    reset_publication_logs
    bash "$repo_root/scripts/post_ai_review.sh" \
      "$reviewer" "$EXPECTED_BASE_SHA" "$EXPECTED_HEAD_SHA" "$fixture_review_file"
    if [[ "$transition" == "suppress" ]]; then
      [[ "$(cat "$GH_REQUEST_LOG")" == "summary" ]] ||
        fail "publication ignored a newer suppressing verdict"
    else
      [[ "$(tr '\n' ' ' < "$GH_REQUEST_LOG")" == "inline summary " ]] ||
        fail "publication ignored a newer verdict reversal"
    fi
  done
done

reset_publication_logs
if GH_TELEMETRY_READ_ERROR=true bash "$repo_root/scripts/post_ai_review.sh" \
  codex "$EXPECTED_BASE_SHA" "$EXPECTED_HEAD_SHA" "$fixture_review_file" \
  > "$test_dir/read-error.log" 2>&1; then
  fail "publisher continued after a telemetry API failure"
fi
[[ ! -e "$GH_REQUEST_LOG" ]] || fail "publisher posted after a telemetry read error"
grep -F "failed to load prior AI findings" "$test_dir/read-error.log" > /dev/null ||
  fail "loader API failure did not produce a clear error"

# The publisher subprocess calls this exported stub; nothing in this file
# invokes it directly.
# shellcheck disable=SC2329
python3() {
  if [[ "$1" == *"/prepare_ai_review_comments.py" ]]; then
    local previous=""
    local argument
    for argument in "$@"; do
      if [[ "$previous" == "--finding-decisions" ]]; then
        rm "$argument"
        if [[ "$DECISION_FILE_FAILURE" == "unreadable" ]]; then
          mkdir "$argument"
        fi
        break
      fi
      previous="$argument"
    done
  fi
  command python3 "$@"
}
export -f python3
for failure in missing unreadable; do
  reset_publication_logs
  if DECISION_FILE_FAILURE="$failure" bash "$repo_root/scripts/post_ai_review.sh" \
    codex "$EXPECTED_BASE_SHA" "$EXPECTED_HEAD_SHA" "$fixture_review_file" \
    > "$test_dir/decision-error.log" 2>&1; then
    fail "publisher continued with a $failure decision file"
  fi
  [[ ! -e "$GH_REQUEST_LOG" ]] || fail "publisher posted with a $failure decision file"
  grep -F "invalid AI review output" "$test_dir/decision-error.log" > /dev/null ||
    fail "invalid decision file did not produce a clear error"
done
unset -f python3

reset_publication_logs
if CHANGED_REVISION_AFTER=4 EXPECT_PARTIAL_CLEANUP=true \
  bash "$repo_root/scripts/post_ai_review.sh" \
    codex "$EXPECTED_BASE_SHA" "$EXPECTED_HEAD_SHA" "$fixture_review_file" \
    > "$test_dir/changed-revision.log" 2>&1; then
  fail "publisher continued after the PR revision changed"
fi
[[ "$(cat "$GH_REQUEST_LOG")" == "inline" ]] ||
  fail "publisher posted a summary after the PR revision changed"
[[ "$(cat "$GH_MINIMIZED_LOG")" == "PRRC_new" ]] ||
  fail "publisher did not clean up after the PR revision changed"
jq -se '
  [.[] | select(.record_type == "review_failed")] | length == 1
' "$GH_BLOB_UPLOAD_LOG" > /dev/null ||
  fail "publisher did not record the revision-change failure"

echo "PASS: trusted AI review publisher"
