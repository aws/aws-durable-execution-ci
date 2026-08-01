#!/usr/bin/env bash

set -euo pipefail

if [[ "$#" -ne 4 ]]; then
  echo "usage: $0 <claude|codex> <expected-base-sha> <expected-head-sha> <summary-file>" >&2
  exit 2
fi

reviewer="$1"
expected_base_sha="$2"
expected_head_sha="$3"
summary_file="$4"

case "$reviewer" in
  claude)
    marker="<!-- ai-pr-review:claude -->"
    title="Claude AI review"
    ;;
  codex)
    marker="<!-- ai-pr-review:codex -->"
    title="Codex AI review"
    ;;
  *)
    echo "unsupported AI reviewer: $reviewer" >&2
    exit 2
    ;;
esac

: "${GH_TOKEN:?GH_TOKEN must be set}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must be set}"
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID must be set}"
: "${GITHUB_SERVER_URL:?GITHUB_SERVER_URL must be set}"
: "${PR_NUMBER:?PR_NUMBER must be set}"

previous_inline_comments_file="${PREVIOUS_INLINE_COMMENTS_FILE:-}"
new_inline_comments_file="${NEW_INLINE_COMMENTS_FILE:-}"

if [[ ! -r "$summary_file" ]]; then
  echo "AI review summary is not readable: $summary_file" >&2
  exit 2
fi

summary="$(cat "$summary_file")"
if [[ -z "${summary//[[:space:]]/}" ]]; then
  echo "::error::$title returned an empty review body."
  exit 1
fi

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
  exit 1
fi

run_url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
inline_comment_metadata=""
if [[
  -n "$previous_inline_comments_file" &&
  -r "$previous_inline_comments_file" &&
  -n "$new_inline_comments_file" &&
  -r "$new_inline_comments_file"
]]; then
  inline_comment_metadata="<!-- ai-pr-review:inline-comments:${reviewer} -->"$'\n'
  while IFS= read -r comment_id; do
    [[ -n "$comment_id" ]] || continue

    if [[ "$comment_id" =~ ^[A-Za-z0-9_=/+-]+$ ]]; then
      inline_comment_metadata+="<!-- ai-pr-review:inline-comment:${reviewer}:${comment_id} -->"$'\n'
    else
      echo "::warning::Ignored an invalid current $title inline comment ID."
    fi
  done < <(
    {
      sort -u "$previous_inline_comments_file"
      sort -u "$new_inline_comments_file"
    } | sort -u
  )
fi

# shellcheck disable=SC2016 # Markdown backticks are intentionally literal.
printf -v body '%s\n%s## %s\n\n%s\n\nReviewed commit `%s`. [Workflow run](%s)' \
  "$marker" \
  "$inline_comment_metadata" \
  "$title" \
  "$summary" \
  "$expected_head_sha" \
  "$run_url"

new_comment_id="$(
  gh api \
    --method POST \
    "repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments" \
    --raw-field body="$body" \
    --jq .node_id
)"
if [[ -z "$new_comment_id" ]]; then
  echo "::error::GitHub did not return the new AI review comment ID."
  exit 1
fi

owner="${GITHUB_REPOSITORY%%/*}"
repository="${GITHUB_REPOSITORY#*/}"
comments_file="$(mktemp "${RUNNER_TEMP:-/tmp}/ai-review-comments.XXXXXX")"
cleanup_temp_files() {
  rm -f "$comments_file"
}
trap cleanup_temp_files EXIT

minimize_comment() {
  local comment_id="$1"

  # shellcheck disable=SC2016 # GraphQL variables are intentionally literal.
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

# shellcheck disable=SC2016 # GraphQL variables are intentionally literal.
if ! gh api graphql \
  --paginate \
  -F owner="$owner" \
  -F repository="$repository" \
  -F number="$PR_NUMBER" \
  -f query='
    query(
      $owner: String!,
      $repository: String!,
      $number: Int!,
      $endCursor: String
    ) {
      repository(owner: $owner, name: $repository) {
        pullRequest(number: $number) {
          comments(first: 100, after: $endCursor) {
            nodes {
              id
              body
              isMinimized
              author {
                login
              }
            }
            pageInfo {
              hasNextPage
              endCursor
            }
          }
        }
      }
    }
  ' > "$comments_file"; then
  echo "::warning::Failed to list previous $title comments for cleanup."
else
  previous_comment_count=0
  while IFS= read -r comment_id; do
    [[ -n "$comment_id" ]] || continue

    if minimize_comment "$comment_id"; then
      previous_comment_count=$((previous_comment_count + 1))
    else
      echo "::warning::Failed to minimize previous $title comment ($comment_id)."
    fi
  done < <(
    jq -rs \
      --arg current_id "$new_comment_id" \
      --arg marker "$marker" \
      --arg legacy_header "## $title" \
      '
        .[]
        | .data.repository.pullRequest.comments.nodes[]
        | select(.id != $current_id)
        | select(.isMinimized == false)
        | select(.author.login == "github-actions")
        | (.body | split("\n")[0]) as $first_line
        | select(
            $first_line == $marker
            or $first_line == $legacy_header
          )
        | .id
      ' \
      "$comments_file"
  )

  echo "Minimized $previous_comment_count previous $title comment(s)."
fi

if [[ -z "$previous_inline_comments_file" ]]; then
  exit 0
fi

if [[ ! -r "$previous_inline_comments_file" ]]; then
  echo "::warning::Previous $title inline comment snapshot is not readable."
  exit 0
fi

previous_inline_comment_count=0
while IFS= read -r comment_id; do
  [[ -n "$comment_id" ]] || continue

  if minimize_comment "$comment_id"; then
    previous_inline_comment_count=$((previous_inline_comment_count + 1))
  else
    echo "::warning::Failed to minimize previous $title inline comment ($comment_id)."
  fi
done < "$previous_inline_comments_file"

echo "Minimized $previous_inline_comment_count previous $title inline comment(s)."
