# Codex issue implementation

The reusable Codex issue implementation workflow reconciles explicitly labeled
issues into draft pull requests. It also updates an existing linked pull
request when an authorized maintainer marks a review thread with:

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
      contents: read
      id-token: write
      issues: read
      pull-requests: read
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

Create an `ai-pr-review-runtime` environment with these secrets:

- `BEDROCK_ROLE_ARN`: the IAM role that GitHub's OIDC provider can assume.
- `CODEX_PUBLISH_TOKEN`: a fine-grained personal access token or valid GitHub
  App installation token with contents, issues, and pull request write access
  to the consuming repository.

The publication token is required because pushes and pull requests created by
the automatic `GITHUB_TOKEN` do not trigger most subsequent workflow runs.
Keep the token in the protected environment so it is released only to the
trusted actor-resolution and publication steps. Do not add required reviewers
or a wait timer unless every implementation run should require manual approval.

The workflow creates the default `codex:no-pr` label if it needs to mark an
issue that requires no repository change. Creating it in advance is
recommended so its color and description follow repository conventions.
Issues carrying `codex:no-pr` are excluded from scheduled discovery. Remove
that label before asking Codex to reconsider the issue.

The publication credential and repository settings must allow draft pull
request creation. The workflow does not merge or approve pull requests.

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

The schedule in the caller controls run frequency. Scheduled and manual
discovery scans the oldest open eligible issues until it finds up to
`max-issues` issues with pending reconciliation work. Issues whose linked pull
request has no unprocessed review marker do not consume the limit. A manual run
can pass `issue-number` to reconcile one eligible issue directly.

## Issue and pull request behavior

For every selected issue, the worker re-fetches the issue, labels, linked pull
requests, branch SHA, and unprocessed review commands after acquiring this
concurrency group:

```text
codex-issue-<repository-id>-<issue-number>
```

Runs for one issue are serialized without cancellation. Different issue
numbers remain independent matrix jobs and can run in parallel.

With no linked open pull request, Codex checks out the exact current default
branch revision. A change is committed to the deterministic
`implement-issue-<number>` branch and published with a non-force push. The
workflow checks again for a linked pull request before it pushes and before it
opens one draft pull request whose body closes the issue.

If a workflow-owned branch was pushed but pull request creation failed, a
later run recognizes commit trailers on that branch and retries only pull
request creation. The trailers include a semantic digest of the issue title,
body, state, and labels; recovery is blocked when the current issue no longer
matches the work that produced the branch. Recovery is also refused when any
open or closed pull request has already used that branch, so closing a
generated draft does not cause a replacement. An unrelated branch with the
deterministic name is not overwritten.

With exactly one linked open pull request, Codex runs only when that pull
request has an unprocessed review marker. The head branch must be in the
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
- default branch designation or SHA, or pull request head SHA;
- unprocessed authorized review markers;
- deterministic implementation branch state.

Pushes are non-force and anchored by the validated parent SHA, so a concurrent
human or automation update is rejected. Failed validation or publication
leaves review markers unprocessed for a later retry.

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
- runs Codex as an unprivileged user with a writable worktree and read-only
  Git metadata;
- uses `workspace-write` with approval prompts disabled, outbound network and
  web search disabled, temporary directories excluded, and apps, plugins,
  hooks, image, browser, computer, and multi-agent tools disabled;
- exposes only the Bedrock credential chain to the Codex process and removes
  AWS, GitHub, key, secret, and token variables from spawned shell commands;
- never places the publication token or automatic `GITHUB_TOKEN` in the Codex
  step;
- accepts only a closed JSON result contract and a size-limited Git patch;
- rejects gitlinks, leaked runtime credentials, unexpected workflow changes,
  stale state, and changes outside the checked-out repository;
- re-checks all mutation preconditions in the publication step before the
  publication token is used.

The automatic `GITHUB_TOKEN` is read-only in the worker. The separate
publication token is passed only to trusted actor-resolution, publication
checkout, and mutation steps, and is not available to Codex.
