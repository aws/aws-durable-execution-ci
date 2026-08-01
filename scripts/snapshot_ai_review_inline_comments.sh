#!/usr/bin/env bash

set -euo pipefail

if [[ "$#" -lt 2 || "$#" -gt 3 ]]; then
  echo "usage: $0 <claude|codex> <output-file> [tracked|all]" >&2
  exit 2
fi

reviewer="$1"
output_file="$2"
mode="${3:-tracked}"

case "$reviewer" in
  claude)
    title="Claude AI review"
    ;;
  codex)
    title="Codex AI review"
    ;;
  *)
    echo "unsupported AI reviewer: $reviewer" >&2
    exit 2
    ;;
esac

case "$mode" in
  tracked | all)
    ;;
  *)
    echo "unsupported snapshot mode: $mode" >&2
    exit 2
    ;;
esac

: "${GH_TOKEN:?GH_TOKEN must be set}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must be set}"
: "${PR_NUMBER:?PR_NUMBER must be set}"

owner="${GITHUB_REPOSITORY%%/*}"
repository="${GITHUB_REPOSITORY#*/}"
inline_marker_pattern="^\\[ai-pr-review-inline-${reviewer}-[0-9]+-[0-9]+-(primary|retry|published)\\]: #$"
comments_file="$(mktemp "${RUNNER_TEMP:-/tmp}/ai-review-inline-comments.XXXXXX")"
legacy_ids_file="$(mktemp "${RUNNER_TEMP:-/tmp}/ai-review-legacy-inline-ids.XXXXXX")"
marked_ids_file="$(mktemp "${RUNNER_TEMP:-/tmp}/ai-review-marked-inline-ids.XXXXXX")"
open_ids_file="$(mktemp "${RUNNER_TEMP:-/tmp}/ai-review-open-inline-ids.XXXXXX")"
summaries_file="$(mktemp "${RUNNER_TEMP:-/tmp}/ai-review-summaries.XXXXXX")"
summary_state_file="$(mktemp "${RUNNER_TEMP:-/tmp}/ai-review-summary-state.XXXXXX")"
tracked_ids_file="$(mktemp "${RUNNER_TEMP:-/tmp}/ai-review-tracked-inline-ids.XXXXXX")"
snapshot_file="$(mktemp "${output_file}.XXXXXX")"
cleanup_temp_files() {
  rm -f \
    "$comments_file" \
    "$legacy_ids_file" \
    "$marked_ids_file" \
    "$open_ids_file" \
    "$summaries_file" \
    "$summary_state_file" \
    "$tracked_ids_file" \
    "$snapshot_file"
}
trap cleanup_temp_files EXIT

has_manifest=false
if [[ "$mode" == "tracked" ]]; then
  summary_marker="<!-- ai-pr-review:${reviewer} -->"
  inline_manifest="<!-- ai-pr-review:inline-comments:${reviewer} -->"
  inline_id_prefix="<!-- ai-pr-review:inline-comment:${reviewer}:"

  # New summaries record the exact inline comments owned by that review. This
  # avoids touching unrelated comments posted by other GitHub Actions.
  # shellcheck disable=SC2016 # GraphQL variables are intentionally literal.
  if gh api graphql \
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
                body
                createdAt
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
    ' > "$summaries_file"; then
    if jq -rs \
      --arg marker "$summary_marker" \
      --arg manifest "$inline_manifest" \
      --arg id_prefix "$inline_id_prefix" \
      '
        [
          .[]
          | .data.repository.pullRequest.comments.nodes[]
          | select(.isMinimized == false)
          | select(.author.login == "github-actions")
          | select((.body | split("\n")[0]) == $marker)
          | select(.body | split("\n") | index($manifest))
        ] as $summaries
        | if ($summaries | length) == 0 then
            {found: false, ids: []}
          else
            ($summaries | sort_by(.createdAt) | last | .body) as $body
            | {
                found: true,
                ids: [
                  $body
                  | split("\n")[]
                  | select(startswith($id_prefix))
                  | select(endswith(" -->"))
                  | ltrimstr($id_prefix)
                  | rtrimstr(" -->")
                  | select(test("^[A-Za-z0-9_=/+-]+$"))
                ]
              }
          end
      ' \
      "$summaries_file" > "$summary_state_file"; then
      if [[ "$(jq -r .found "$summary_state_file")" == "true" ]]; then
        jq -r '.ids[]' "$summary_state_file" > "$tracked_ids_file"
        has_manifest=true
      fi
    else
      echo "::warning::Failed to parse previous $title summary metadata."
    fi
  else
    echo "::warning::Failed to list previous $title summaries."
  fi
fi

# List open root comments authored by the GITHUB_TOKEN identity. Tracked mode
# intersects this list with the latest summary manifest and recovery markers.
# The broken HTML marker was stripped to a leading newline; new Markdown
# reference markers survive the action's sanitizer but remain hidden by GitHub.
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
          reviewThreads(first: 100, after: $endCursor) {
            nodes {
              comments(first: 1) {
                nodes {
                  id
                  body
                  isMinimized
                  replyTo {
                    id
                  }
                  author {
                    login
                  }
                }
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
  echo "::warning::Failed to snapshot previous $title inline comments."
  exit 0
fi

if ! jq -rs \
  '
    .[]
    | .data.repository.pullRequest.reviewThreads.nodes[]
    | .comments.nodes[]
    | select(.replyTo == null)
    | select(.isMinimized == false)
    | select(.author.login == "github-actions")
    | .id
  ' \
  "$comments_file" > "$open_ids_file"; then
  echo "::warning::Failed to parse previous $title inline comments."
  exit 0
fi

if [[ "$reviewer" == "claude" ]]; then
  if ! jq -rs \
    '
      .[]
      | .data.repository.pullRequest.reviewThreads.nodes[]
      | .comments.nodes[]
      | select(.replyTo == null)
      | select(.isMinimized == false)
      | select(.author.login == "github-actions")
      | ((.body // "") | split("\n")[0]) as $first_line
      | select($first_line == "")
      | .id
    ' \
    "$comments_file" > "$legacy_ids_file"; then
    echo "::warning::Failed to parse legacy $title inline comments."
    exit 0
  fi
else
  : > "$legacy_ids_file"
fi

if ! jq -rs \
  --arg marker_pattern "$inline_marker_pattern" \
  '
    .[]
    | .data.repository.pullRequest.reviewThreads.nodes[]
    | .comments.nodes[]
    | select(.replyTo == null)
    | select(.isMinimized == false)
    | select(.author.login == "github-actions")
    | ((.body // "") | split("\n")[0]) as $first_line
    | select($first_line | test($marker_pattern))
    | .id
  ' \
  "$comments_file" > "$marked_ids_file"; then
  echo "::warning::Failed to parse marked $title inline comments."
  exit 0
fi

snapshot_kind="open"
if [[ "$mode" == "tracked" && "$has_manifest" == "true" ]]; then
  {
    comm -12 \
      <(sort -u "$open_ids_file") \
      <(sort -u "$tracked_ids_file")
    sort -u "$marked_ids_file"
  } | sort -u > "$snapshot_file"
  snapshot_kind="tracked"
elif [[ "$mode" == "tracked" ]]; then
  {
    sort -u "$legacy_ids_file"
    sort -u "$marked_ids_file"
  } | sort -u > "$snapshot_file"
  snapshot_kind="recoverable"
else
  sort -u "$open_ids_file" > "$snapshot_file"
fi

mv "$snapshot_file" "$output_file"
previous_inline_comment_count="$(wc -l < "$output_file" | tr -d '[:space:]')"
echo "Snapshotted $previous_inline_comment_count $snapshot_kind $title inline comment(s)."
