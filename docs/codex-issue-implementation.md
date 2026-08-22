# Codex issue implementation

The reusable Codex issue implementation workflow reconciles explicitly labeled
issues into draft pull requests. Independently of issue labels, it also updates
an existing linked pull request when an authorized maintainer marks a review
thread with:

```text
/codex address
```

The immediate event triggers and daily schedule belong in each consuming
repository. The reusable workflow owns discovery, issue-scoped serialization,
Codex execution through Amazon Bedrock, state revalidation, and publication.

## Caller workflow

Add `.github/workflows/codex-issue-implementation.yml` to the consuming
repository:

```yaml
name: Codex Issue Implementation

on:
  issues:
    types: [labeled]
  pull_request_review_comment:
    types: [created]
  schedule:
    - cron: "17 3 * * *"
  workflow_dispatch:
    inputs:
      issue-number:
        description: Optional eligible issue number to reconcile
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
does not add its own `issues`, review-comment, schedule, or manual triggers to
the caller.

## Repository setup

Create the `codex:implement` label before enabling the workflow. Applying that
label to an open issue starts reconciliation immediately. The daily schedule
recovers missed events, failed runs, and branches pushed before pull request
creation completed.

The implementation label is not required for `/codex address`. That command is
restricted to updating an existing linked pull request and never creates a new
implementation branch or pull request.

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
      implementation-label: automation:implement
      no-pr-label: automation:no-pr
      max-issues: 5
      model: openai.gpt-5.6-sol
      reasoning-effort: high
      allow-workflow-changes: false
```

- `implementation-label` defaults to `codex:implement`.
- `no-pr-label` defaults to `codex:no-pr`.
- `max-issues` defaults to 3 and is limited to 10 per discovery run.
- `model` defaults to `openai.gpt-5.6-sol`.
- `reasoning-effort` defaults to `high`.
- `allow-workflow-changes` defaults to `false`. When false, a model result that
  changes `.github/workflows/**` is rejected before publication.

Label matching follows GitHub's case-insensitive behavior. Existing labels can
therefore use different casing from the configured values. Automation label
names must not contain commas because scheduled discovery uses GitHub's
comma-separated label filter.

The schedule in the caller controls run frequency. Scheduled and manual
discovery scans the oldest open eligible issues until it finds up to
`max-issues` issues with pending reconciliation work. Issues whose linked pull
request has no unprocessed review marker do not consume the limit. Blocked or
ambiguous issues consume a slot until their current state is reported; later
discovery runs skip the same actor-authored notification marker and continue
scanning. A manual run can pass `issue-number` to reconcile one eligible issue
directly.

## Issue and pull request behavior

For every selected issue, the entry workflow starts one reusable worker that
owns the complete reconcile and publication pipeline. The worker re-fetches
the issue, labels, linked pull requests, branch SHA, and unprocessed review
commands after acquiring this workflow-level concurrency group:

```text
codex-issue-<repository-id>-<issue-number>
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

With exactly one linked open pull request, labeled reconciliation requires that
pull request to close exactly that one open, eligible issue. Review-command
runs instead require exactly one open linked issue, regardless of the
implementation or no-PR labels. The selected issue set is persisted and
revalidated before pushes and acknowledgements. The head branch must be in the
current repository; fork branches are never updated. The workflow checks out
the exact head SHA, applies all currently unprocessed marked feedback in one
reconciliation, and pushes back to the same branch.

When multiple open pull requests close the issue, the workflow does not choose
one. It posts a deduplicated issue comment asking a maintainer to remove the
ambiguity.

## Review marker

Post `/codex address` as a reply in the relevant pull request review thread.
The trimmed body must exactly match that command. General comments, unmarked
review comments, command variants, and reactions do not start work.

The linked issue must be open, but it does not need `codex:implement` or any
other label. If the linked pull request disappears before reconciliation, the
address-only run skips instead of starting new issue implementation.

The command author must currently have `write`, `maintain`, or `admin`
permission. Bot users and users without sufficient repository permission are
ignored. `author_association` is not used as authorization.

After a successful update, the workflow replies in the review thread with the
result and a machine-readable marker containing the command comment ID and
commit SHA. Later runs treat that marker as processed. A no-change result is
also acknowledged, using the unchanged pull request head SHA. Review threads
are not resolved automatically. Review comment bodies and diff hunks are
preserved in full; the run fails instead of acknowledging feedback when the
complete prepared state exceeds the overall context size limit.

## Revalidation and failure behavior

The worker treats every run as reconciliation. It aborts publication when any
of these change after model execution:

- issue identity, body, state, or labels;
- linked pull request count or identity;
- the exact mode-appropriate issue set closed by the pull request;
- default branch designation or SHA, or pull request head SHA;
- unprocessed authorized review markers;
- deterministic implementation branch state.

Pushes use an exact `--force-with-lease` expectation anchored by the validated
remote SHA, or by branch nonexistence for a new implementation. A concurrent
human or automation update is therefore rejected atomically. Failed validation
or publication leaves review markers unprocessed for a later retry.

When Codex determines that a new implementation requires no repository
change, the workflow applies the non-actionable label and posts a deduplicated
explanation instead of opening a pull request.

## Security model

Issue titles, bodies, labels, pull request data, diffs, and review threads are
untrusted model input.

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
