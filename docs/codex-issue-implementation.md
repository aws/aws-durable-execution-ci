# Codex issue implementation

The reusable Codex issue implementation workflow reconciles open issues into
draft pull requests when an authorized maintainer comments:

```text
/ai implement
```

Independently of issue linkage, it also updates an existing pull request when
an authorized maintainer posts:

```text
/ai address
```

The immediate event triggers and daily schedule belong in each consuming
repository. The reusable workflow owns discovery, issue-scoped serialization,
PR-scoped review serialization, Codex execution through Amazon Bedrock, state
revalidation, and publication.

## Caller workflow

Add `.github/workflows/codex-issue-implementation.yml` to the consuming
repository:

```yaml
name: Codex Issue Implementation

on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
  schedule:
    - cron: "17 3 * * *"
  workflow_dispatch:
    inputs:
      issue-number:
        description: Optional authorized issue number to reconcile
        required: false
        type: string
        default: ""

permissions: {}

jobs:
  implement:
    permissions:
      contents: write
      id-token: write
      issues: write
      pull-requests: write
    uses: aws/aws-durable-execution-ci/.github/workflows/codex-issue-implementation.yml@<full-commit-sha>
    with:
      issue-number: ${{ inputs['issue-number'] || '' }}
    secrets: inherit
```

Replace `<full-commit-sha>` with the 40-character commit SHA to use. Pinning
ensures that the workflow, trusted prompt, schema, and publication policy come
from one immutable revision.

The caller must declare every event it wants to support. A reusable workflow
does not add its own issue-comment, review-comment, schedule, or manual
triggers to the caller.

## Repository setup

Post `/ai implement` as a standalone comment on an open issue. The trimmed
body must exactly match the command. The author must currently have `write`,
`maintain`, or `admin` repository permission and must not be a bot. The daily
schedule recovers missed events, failed runs, and branches pushed before pull
request creation completed by finding open issues with an authorized command.
Implementation labels are not inspected; applying `codex:implement` alone does
not start work. Command variants and emoji reactions do not start work.

`/ai address` is scoped directly to the pull request containing the command.
It does not require that pull request to close or reference an issue and never
creates a new implementation branch or pull request.

Create an `ai-pr-review-runtime` environment with this secret:

- `BEDROCK_ROLE_ARN`: the IAM role that GitHub's OIDC provider can assume.

Allow GitHub Actions to create pull requests in the repository settings. The
caller must grant the reusable workflow contents, issues, and pull request
write permissions as shown above; a called workflow cannot elevate permissions
that the caller withheld.

Publication intentionally uses the automatic `GITHUB_TOKEN`. Pushes, pull
requests, comments, and labels created by that token do not trigger ordinary
`push` or `pull_request` workflows, so model-authored code is not executed
automatically with repository secrets. Repositories can provide a separately
reviewed, secretless manual validation workflow for generated drafts. Any
validation with privileged credentials should require human approval.

The workflow creates the default `codex:no-pr` label if it needs to mark an
issue that requires no repository change. Creating it in advance is
recommended so its color and description follow repository conventions.
Issues carrying `codex:no-pr` are excluded from scheduled discovery. Remove
that label before asking Codex to reconsider the issue.

The repository settings must allow the automatic token to create draft pull
requests. The workflow does not merge or approve pull requests.

## Configuration

Pass optional inputs from the caller job:

```yaml
    with:
      no-pr-label: automation:no-pr
      max-issues: 5
      model: openai.gpt-5.6-sol
      reasoning-effort: high
      allow-workflow-changes: false
```

- `no-pr-label` defaults to `codex:no-pr`.
- `max-issues` defaults to 3 and is limited to 10 work items per discovery
  run.
- `model` defaults to `openai.gpt-5.6-sol`.
- `reasoning-effort` defaults to `high`.
- `allow-workflow-changes` defaults to `false`. When false, a model result that
  changes `.github/workflows/**` is rejected before publication.

The configured no-PR label follows GitHub's case-insensitive label behavior.

The schedule in the caller controls run frequency. Scheduled and manual
discovery scans open issues and pull requests in creation order until they find
up to `max-issues` pending work items. Each run evaluates at most 25 new
candidates and persists the last evaluated issue number in the Actions cache,
so the next run resumes later in the list instead of repeatedly spending its
budget on the oldest inactive issues. Reaching the end resets the cursor for a
new cycle. This recovers authorized `/ai implement` commands and unprocessed
`/ai address` commands even when the pull request does not close an eligible
issue. Across webhook and recovery resolution, address work is always emitted
as a pull request work item, including when it was found through a linked issue,
so duplicate issue-scoped and pull-request-scoped workers cannot target the
same review command. Blocked or ambiguous issues consume a slot until their
current state is reported; later discovery runs skip the same actor-authored
notification marker and continue scanning. A manual run can pass `issue-number`
to reconcile one issue directly, but the worker still requires an authorized
implementation command.

## Issue and pull request behavior

For every selected issue or pull request review command, the entry workflow
starts one reusable worker that owns the complete reconcile and publication
pipeline. The worker re-fetches the applicable issue or pull request state,
branch SHA, and unprocessed review commands after acquiring one of these
workflow-level concurrency groups:

```text
codex-<repository-id>-issue-<issue-number>
codex-<repository-id>-pr-<pull-request-number>
```

A running pipeline cannot be canceled between model execution and publication
by a newer event for the same issue. GitHub may replace an older pending worker
before it starts, since the newer worker will reconcile fresher state. Different
issue numbers remain independent matrix jobs and can run in parallel.

With no linked open pull request, Codex checks out the exact current default
branch revision. A change is committed to the deterministic
`implement-issue-<number>` branch and published with an exact
`--force-with-lease` comparison that requires the remote branch not to exist.
The workflow checks again for a linked pull request before it pushes and before
it opens one draft pull request whose body closes the issue.

If a workflow-owned branch was pushed but pull request creation failed, a
later run recognizes commit trailers on that branch and retries only pull
request creation. The trailers include a semantic digest of the issue title,
body, state, and labels; recovery is blocked when the current issue no longer
matches the work that produced the branch. Recovery is also refused when any
open or closed pull request has already used that branch, so closing a
generated draft and deleting its branch does not cause a replacement or orphan
branch. An unrelated branch with the deterministic name is not overwritten.

With exactly one linked open pull request, issue-scoped reconciliation requires
that pull request to close exactly that one open issue.
Review-command runs are scoped directly to the pull request containing the
command and do not inspect closing issue references. The pull request snapshot
is persisted and revalidated before pushes and
acknowledgements. The head branch must be in the current repository; fork
branches are never updated. The workflow checks out the exact head SHA, applies
all currently unprocessed marked feedback in one reconciliation, and pushes
back to the same branch.

When multiple open pull requests close the issue, the workflow does not choose
one. It posts a deduplicated issue comment asking a maintainer to remove the
ambiguity.

## Review marker

Post `/ai address` as a reply in a pull request review thread to address that
complete thread. The trimmed body must exactly match the command. All currently
unprocessed marked threads are reconciled together.

Post `/ai address` as a top-level pull request conversation comment to address
all human conversation comments, submitted review summaries, and inline review
comments created or edited at or after GitHub's server-recorded push time for
the current head. If that activity record is unavailable, the workflow includes
all otherwise eligible feedback rather than risk silently omitting feedback.
Explicitly marked inline threads are included once as complete threads rather
than duplicated in the batch feedback. Multiple pending top-level commands are
acknowledged together. Command variants, bot comments, previous
acknowledgements, and reactions do not start or contribute feedback.

No linked issue is required. If the pull request closes, moves to an
unwritable head, or otherwise changes before reconciliation, the address-only
run skips or aborts instead of starting issue implementation.

The command author must currently have `write`, `maintain`, or `admin`
permission. Bot users and users without sufficient repository permission are
ignored. `author_association` is not used as authorization.

If publication pushes a Codex review commit but new feedback prevents
acknowledgement, the retry follows consecutive Codex review commits back to
the preceding non-automation commit. The original server-recorded push time
remains the feedback baseline, so the intervening feedback is not hidden by
the automation push.

After a successful update, the workflow replies in each marked review thread
and posts a pull request conversation acknowledgement for top-level commands.
Machine-readable markers contain the command kind, command comment IDs, and
commit SHA, so later runs treat those commands as processed without conflating
the separate review-comment and issue-comment ID spaces. A no-change result is
also acknowledged, using the unchanged pull request head SHA. Batch
acknowledgements also record a feedback high-water mark, so a later top-level
command on the same head includes only newer or subsequently edited feedback.
Review threads are not resolved automatically. Conversation bodies, review
comment bodies, and diff hunks are preserved in full; the run fails instead of
acknowledging feedback when the complete prepared state exceeds the overall
context size limit.

## Revalidation and failure behavior

The worker treats every run as reconciliation. It aborts publication when any
of these change after model execution:

- issue identity, body, state, or labels for issue-scoped work;
- the selected `/ai implement` comment or its author's current permission;
- linked pull request count, identity, and issue ownership for issue-scoped
  work;
- the exact pull request identity, refs, and head SHA for review commands;
- default branch designation or SHA, including immediately before a pull
  request head update;
- unprocessed authorized review markers, batch commands, and feedback since
  the prepared head commit;
- deterministic implementation branch state.

Pushes use an exact `--force-with-lease` expectation anchored by the validated
remote SHA, or by branch nonexistence for a new implementation. A concurrent
human or automation update is therefore rejected atomically. Failed validation
or publication leaves review markers unprocessed for a later retry.

When Codex determines that a new implementation requires no repository
change, the workflow applies the non-actionable label and posts a deduplicated
explanation instead of opening a pull request. It rechecks that the
deterministic implementation branch is still absent and has no pull request
history immediately before both the explanation and the label.

## Security model

Issue titles, bodies, labels, comments, pull request data, diffs, and review
threads are untrusted model input.

The workflow:

- loads its helper, prompt, and output schema from the immutable reusable
  workflow revision;
- checks out an exact default-branch or pull-request-head SHA without
  persisted GitHub credentials;
- disables writable-checkout project instructions and exec-policy rules, then
  supplies only `AGENTS.md`, `AGENTS.override.md`, and `CONTRIBUTING.md` files
  read from the exact default-branch or pull-request-base commit;
- runs Codex as an unprivileged user with a writable worktree, read-only Git
  metadata, an exact safe-directory registration, and optional Git locks
  disabled;
- serves Bedrock credentials through a runner-owned loopback endpoint and
  verifies that network-disabled model tools cannot reach it; Codex receives
  only the endpoint URI and a short-lived authorization token;
- uses `workspace-write` with approval prompts disabled, outbound network and
  web search disabled, temporary directories excluded, and apps, plugins,
  hooks, image, browser, computer, and multi-agent tools disabled;
- removes AWS, GitHub, key, secret, and token variables from spawned shell
  commands;
- never places a write-enabled automatic `GITHUB_TOKEN` in the Codex step;
- transfers only the validated state, artifact, and bounded patch to a separate
  write-enabled publication job;
- accepts only a closed JSON result contract, bounds individual and cumulative
  staged blob content before diff generation, and streams the Git patch under
  a separate hard size limit;
- rejects gitlinks, runtime credentials in model-authored result text, raw
  staged blobs, and patch metadata, protected workflow renames or edits, stale
  state, prior pull request history, and changes outside the checked-out
  repository;
- re-checks all mutation preconditions in the publication job before the
  event-suppressing automatic token is used.

The model job has a read-only automatic token. The separate publication job is
the only job with contents, issues, and pull request write permissions, and it
does not receive the Bedrock environment or its credentials.
