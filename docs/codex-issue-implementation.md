# Codex issue implementation and PR review addressing

The Codex automation exposes two separate workflows:

- `Codex Issue Implementation` handles `/ai implement` on issues.
- `Codex PR Review Address` handles `/ai address` on pull requests.

Both workflows use the same read-only work-item resolver and the same Codex
worker. Their execution paths differ:

- Issue implementation resolves an issue and calls the worker directly.
- PR review addressing uploads a bounded, read-only intake artifact. A
  `workflow_run` continuation from the default branch validates that artifact
  before calling the worker.

The default-branch continuation is used only for PR review addressing. This
keeps issue implementation behavior unchanged while allowing a repository to
protect the model environment with a default-branch deployment rule for review
updates.

## Caller workflows

Add the following caller workflows to the consuming repository. Replace every
`<full-commit-sha>` with the same 40-character commit SHA from
`aws/aws-durable-execution-ci`.

### Issue implementation

`.github/workflows/codex-issue-implementation.yml`:

```yaml
name: Codex Issue Implementation

on:
  issue_comment:
    types: [created]
  schedule:
    - cron: "17 3 * * *"
  workflow_dispatch:
    inputs:
      issue-number:
        description: Optional authorized issue number to implement
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

This workflow directly enters the configured environment from the triggering
run. If the environment restricts deployments by branch, its rule must allow
the refs from which issue implementation is expected to run.

### PR review intake

The caller must keep the exact workflow name `Codex PR Review Address`, because
the reconciliation workflow selects completed intake runs by that name.

`.github/workflows/codex-pr-review-address.yml`:

```yaml
name: Codex PR Review Address

on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
  schedule:
    - cron: "29 3 * * *"
  workflow_dispatch:
    inputs:
      pull-request-number:
        description: Optional pull request number with authorized feedback
        required: false
        type: string
        default: ""

permissions: {}

jobs:
  intake:
    permissions:
      contents: read
      issues: read
      pull-requests: read
    uses: aws/aws-durable-execution-ci/.github/workflows/codex-pr-review-address.yml@<full-commit-sha>
    with:
      pull-request-number: ${{ inputs['pull-request-number'] || '' }}
```

The intake workflow does not enter the model environment and cannot write
repository content.

### PR review reconciliation

The reconciliation caller must exist on the consuming repository's default
branch before review commands can use the protected continuation.

`.github/workflows/codex-pr-review-reconciliation.yml`:

```yaml
name: Codex PR Review Reconciliation

on:
  workflow_run:
    workflows:
      - Codex PR Review Address
    types: [completed]

permissions: {}

jobs:
  address:
    if: github.event.workflow_run.conclusion == 'success'
    permissions:
      actions: read
      contents: write
      id-token: write
      issues: write
      pull-requests: write
    uses: aws/aws-durable-execution-ci/.github/workflows/codex-pr-review-reconciliation.yml@<full-commit-sha>
    secrets: inherit
```

GitHub starts this `workflow_run` workflow from the default branch. The trusted
worker still checks out and updates the exact pull request head SHA selected by
the validated work item. Repository checkout state does not determine
environment eligibility.

A reusable workflow does not add event triggers to its caller. Each consuming
repository must declare the comment, review-comment, schedule, manual, and
`workflow_run` events shown above.

## Commands

Post this command as a standalone comment on an open issue:

```text
/ai implement
```

Post this command on an open pull request:

```text
/ai address
```

Leading and trailing spaces or tabs are ignored, and one or more spaces or
tabs may separate `/ai` from the command. Additional text after the command is
passed to Codex as maintainer guidance and may continue on later lines.

The command author must currently have `write`, `maintain`, or `admin`
repository permission and must not be a bot. Other command names and emoji
reactions do not start work.

To explicitly authorize changes under `.github/workflows/**` for one issue
implementation request, place the option immediately after the command:

```text
/ai implement --allow-workflow-changes

Keep the workflow change narrowly scoped.
```

The option is removed from the guidance before it is sent to Codex. Mentioning
`--allow-workflow-changes` later in guidance does not grant permission.

The workflows enforce command scope:

- Issue implementation emits only issue work items. It ignores `/ai address`
  comments and does not convert linked-PR review feedback into address work.
- PR review intake emits only PR address work items. It ignores `/ai implement`
  comments and never creates an implementation branch or pull request.

No linked issue is required for `/ai address`.

## Review selection

Reply with `/ai address` inside an inline review thread to select that complete
thread. All currently unprocessed explicitly marked threads are reconciled
together.

Post `/ai address` as a top-level pull request conversation comment to address
all eligible feedback created at or after GitHub's server-recorded push time
for the current head:

- conversation comments
- submitted review summaries
- inline review comments

Human and bot-authored feedback are both included, so automated review
findings can be addressed. If the activity record is unavailable, the workflow
includes all otherwise eligible feedback rather than silently omit findings.
Multiple pending top-level commands and their appended guidance are processed
and acknowledged together.

Edits to comments from an older head do not carry those findings into the
current batch. Publisher-authored acknowledgements and reactions do not start
or contribute feedback.

## Configuration

The reusable workflows support these common inputs:

- `environment-name`, default `ai-pr-review-runtime`
- `no-pr-label`, default `codex:no-pr`
- `model`, default `openai.gpt-5.6-sol`
- `reasoning-effort`, default `xhigh`
- `allow-workflow-changes`, default `false`

Issue implementation also accepts:

- `issue-number`, an optional explicit issue for a manual run
- `max-issues`, default 3 and limited to 10

PR review intake also accepts:

- `pull-request-number`, an optional explicit PR for a manual run
- `max-pull-requests`, default 3 and limited to 10

When customizing PR review configuration, pass the same values to both the
intake and reconciliation callers:

```yaml
    with:
      no-pr-label: automation:no-pr
      environment-name: ai-runtime
      model: openai.gpt-5.6-sol
      reasoning-effort: xhigh
      allow-workflow-changes: false
```

For non-manual events, reconciliation rejects an artifact whose configuration
does not match the trusted reconciliation inputs. A manual
`workflow_dispatch` may preserve its validated intake configuration because
starting that event already requires Actions write access.

Issue and PR discovery use separate scoped cursors. Scheduled and manual issue
discovery scans only implementation work. Scheduled and manual PR discovery
scans only pending address commands. Each run evaluates at most 25 new
candidates and persists the last evaluated number in the Actions cache so a
later run can continue through older inactive items.

## Repository setup

Create the configured environment with this secret:

- `BEDROCK_ROLE_ARN`: the IAM role that GitHub's OIDC provider can assume.

For PR review addressing, the environment may restrict deployments to the
default branch. The intake run never enters it; the successful intake triggers
the default-branch reconciliation workflow, and only that continuation enters
the environment.

Create this repository or organization Actions secret when workflow changes
may be authorized:

- `CODEX_WORKFLOW_PUSH_TOKEN`: a token scoped to the target repository with
  `Contents: write` and `Workflows: write`.

The token is used only by the publication checkout when a validated patch
changes `.github/workflows/**`; it is never available to the Codex process.
Ordinary publication uses `GITHUB_TOKEN`. If a workflow change is authorized
but the secret is unavailable, publication fails with a specific configuration
error.

Allow GitHub Actions to create pull requests in the repository settings. A
called workflow cannot elevate permissions that its caller withheld, so use
the caller permissions shown above. The workflow does not merge or approve
pull requests.

The issue implementation workflow creates the configured no-PR label when it
needs to report that an issue requires no repository change. Creating the label
in advance is recommended so its color and description follow repository
conventions. Issues carrying that label are excluded from scheduled issue
discovery.

## Execution and publication

Both paths call the same `codex-issue-worker.yml`. The worker re-fetches the
current command authorization, issue or pull request state, and target SHA
after acquiring one workflow-level concurrency group:

```text
codex-<repository-id>-issue-<issue-number>
codex-<repository-id>-pr-<pull-request-number>
```

Different work items can run in parallel. A running pipeline is not canceled
between model execution and publication by a newer event for the same work
item.

The PR intake artifact is closed and bounded. It contains only normalized PR
work items, source repository and run identity, and validated configuration.
Reconciliation downloads it from that exact run, checks its review-only scope,
and validates it with the trusted workflow revision before starting the worker.

Codex model execution is limited to two hours. The reconcile job has a
140-minute timeout so setup and post-model validation retain a 20-minute
allowance.

For new issue implementation, Codex checks out the exact current default branch
revision, commits to `implement-issue-<number>`, and opens a draft pull request.
If the default branch advances while the model runs, the validated change may
still be published and opened against the current default branch. A later run
can recover a workflow-owned branch when the push succeeded but pull request
creation did not.

For review addressing, the pull request head must be in the current repository;
fork branches are never updated. The worker checks out the exact validated head
SHA, applies the selected feedback, revalidates the PR state, pushes to the same
branch, updates the PR description when appropriate, and posts an
acknowledgement for the addressed commands.
