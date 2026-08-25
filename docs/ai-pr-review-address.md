# AI PR review address

The AI PR review address workflow applies authorized pull request feedback
when a maintainer posts `/ai address`. It is separate from
[AI issue implementation](ai-issue-implementation.md) and never creates an
implementation branch or a new pull request.

The public workflow names describe the capability rather than the current
provider so additional agents can be supported later. The implementation
currently runs Codex through Amazon Bedrock.

PR review addressing keeps two permission paths behind one reusable workflow
and two event-specific callers:

- Inline review-thread commands use the read-only intake path and upload a
  bounded work-item artifact.
- Top-level pull request comments and manual dispatches from the default branch
  use the direct reconciliation path.
- A separately named `workflow_run` caller continues successful inline intake
  through the reconciliation path from the default branch.

The default-branch continuation allows repositories to protect the model
environment with a default-branch deployment rule without trusting the pull
request branch that requested the update.

## Caller workflows

Replace every `<full-commit-sha>` below with the same 40-character commit SHA
from `aws/aws-durable-execution-ci`.

### Review-address intake and direct reconciliation

Add `.github/workflows/ai-pr-review-address.yml` to the consuming repository.
Keep the exact workflow name `AI PR Review Address`, because the continuation
workflow selects completed inline-intake runs by that name.

```yaml
name: AI PR Review Address

on:
  issue_comment:
    types: [created]

  pull_request_review_comment:
    types: [created]

  workflow_dispatch:
    inputs:
      pull-request-number:
        description: Pull request number
        required: true
        type: string

permissions: {}

jobs:
  intake:
    if: github.event_name == 'pull_request_review_comment'
    permissions:
      contents: read
      issues: read
      pull-requests: read
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-pr-review-address.yml@<full-commit-sha>

  address:
    if: >-
      github.event_name == 'issue_comment' ||
      github.event_name == 'workflow_dispatch'
    permissions:
      actions: read
      contents: write
      id-token: write
      issues: write
      pull-requests: write
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-pr-review-address.yml@<full-commit-sha>
    with:
      pull-request-number: ${{ inputs['pull-request-number'] || '' }}
    secrets: inherit
```

The intake job is available only to inline review-comment events and has
read-only repository permissions. Top-level comments and manual dispatches
from the default branch call reconciliation directly. When manually running
the workflow, select the default branch.

### Inline-intake continuation

Add `.github/workflows/ai-pr-review-address-continuation.yml` to the consuming
repository:

```yaml
name: AI PR Review Address Continuation

on:
  workflow_run:
    workflows:
      - AI PR Review Address
    types: [completed]

permissions: {}

jobs:
  address:
    if: >-
      github.event.workflow_run.event ==
        'pull_request_review_comment' &&
      github.event.workflow_run.conclusion == 'success'
    permissions:
      actions: read
      contents: write
      id-token: write
      issues: write
      pull-requests: write
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-pr-review-address.yml@<full-commit-sha>
    with:
      source-run-id: >-
        ${{ format('{0}', github.event.workflow_run.id || '') }}
      source-run-attempt: >-
        ${{ format('{0}', github.event.workflow_run.run_attempt || '') }}
    secrets: inherit
```

The continuation has a different workflow name, so its completion cannot
trigger itself. It does not filter completed runs by branch name; a fork pull
request whose head branch is named like the target repository's default branch
still reaches reconciliation. The job-level event check ignores completed
top-level-comment and manual runs.

GitHub starts the `workflow_run` continuation from the default branch. The
trusted worker still checks out and updates the exact pull request head SHA
selected by the validated work item.

A reusable workflow does not add event triggers to its caller. Each consuming
repository must declare the comment, review-comment, manual, and
`workflow_run` events shown above. Both callers must exist on the default
branch before review commands can use the protected continuation. There is no
scheduled recovery scan. Use GitHub's rerun action for missed or failed runs,
or manually dispatch the review-address workflow with a specific pull request
number.

## Request review addressing

Post this command on an open pull request:

```text
/ai address
```

Leading and trailing spaces or tabs are ignored, and one or more spaces or
tabs may separate `/ai` and `address`. Additional text after the command is
passed to Codex as task-specific maintainer guidance and may continue on later
lines.

The command author must currently have `write`, `maintain`, or `admin`
repository permission and must not be a bot. Other command names and emoji
reactions do not start work.

The review-address workflow emits only pull request work items. It ignores
`/ai implement` comments, does not require a linked issue, and never creates a
new implementation branch or pull request.

## Feedback selection

Reply with `/ai address` inside an inline review thread to select that complete
thread. All currently unprocessed explicitly marked threads are reconciled
together.

Post `/ai address` as a top-level pull request conversation comment to address
all eligible feedback created at or after GitHub's server-recorded push time
for the current head:

- conversation comments;
- submitted review summaries; and
- inline review comments.

Human and bot-authored feedback are both included, so automated review
findings can be addressed. If the activity record is unavailable, the workflow
includes all otherwise eligible feedback rather than silently omit findings.
Explicitly marked inline threads are included once as complete threads.

Multiple pending top-level commands and their appended guidance are processed
and acknowledged together. Edits to comments from an older head do not carry
those findings into the current batch. Publisher-authored acknowledgements and
reactions do not start or contribute feedback.

After a successful update, the workflow replies in each marked review thread
and posts a pull request conversation acknowledgement for top-level commands.
A no-change result is also acknowledged. Review threads are not resolved
automatically.

## Configuration

The reusable workflow supports these inputs:

- `environment-name`: GitHub environment for the model job; defaults to
  `ai-pr-review-runtime`.
- `no-pr-label`: shared worker label configuration; defaults to
  `codex:no-pr`.
- `model`: Codex model ID; defaults to `openai.gpt-5.6-sol`.
- `reasoning-effort`: defaults to `xhigh`.
- `allow-workflow-changes`: defaults to `false`.

```yaml
  address:
    # ...
    with:
      # Keep the event-specific inputs from the caller examples above.
      environment-name: ai-runtime
      no-pr-label: automation:no-pr
      model: openai.gpt-5.6-sol
      reasoning-effort: xhigh
      allow-workflow-changes: false
```

To override defaults, add the same runtime values to the `with` block of both
privileged `address` jobs. Do not add them to the read-only `intake` job.
The read-only intake artifact does not carry runtime configuration.
Reconciliation always applies the model, environment, label, and workflow
change policy from the trusted default-branch caller.

## Repository setup

Create the configured GitHub environment with this secret:

- `BEDROCK_ROLE_ARN`: the IAM role that GitHub's OIDC provider can assume.

The environment may restrict deployments to the default branch. The intake
run never enters it. A successful intake triggers the default-branch
reconciliation workflow, and only that continuation enters the environment.

When review addressing may change `.github/workflows/**`, create this
repository or organization Actions secret:

- `CODEX_WORKFLOW_PUSH_TOKEN`: a token scoped to the target repository with
  `Contents: write` and `Workflows: write`.

The token is available only to the publication checkout for a validated
workflow change; it is never available to the Codex process. Ordinary
publication uses `GITHUB_TOKEN`.

The `address` job must use `secrets: inherit` and grant the actions, contents,
identity-token, issue, and pull request permissions shown above. A called
workflow cannot elevate permissions that its caller withheld.

## Execution and publication

The intake artifact is closed and bounded. It contains only normalized PR work
items and source repository and run identity. Reconciliation downloads it from
the exact successful run, verifies its review-only scope, and validates it with
the trusted workflow revision before starting `codex-issue-worker.yml`.

The worker re-fetches the current command authorization, pull request state,
and target SHA after acquiring a PR-scoped concurrency group:

```text
codex-<repository-id>-pr-<pull-request-number>
```

A running pipeline is not canceled between model execution and publication by
a newer event for the same pull request. Codex model execution is limited to
two hours, with additional time reserved for setup and post-model validation.

The pull request head must be in the current repository; fork branches are
never updated. The worker checks out the exact validated head SHA, applies the
selected feedback, revalidates the pull request state, pushes to the same
branch, updates the pull request description when appropriate, and posts
acknowledgements for the addressed commands.

If publication pushes an automation commit but new feedback prevents
acknowledgement, a retry follows consecutive automation commits back to the
preceding non-automation commit. The original server-recorded push time remains
the feedback baseline.

## Security model

Pull request metadata, comments, review summaries, inline review threads,
diffs, and repository content are untrusted model input.

The workflow:

- performs event intake with read-only repository permissions;
- transfers only a bounded, normalized work-item artifact to the
  default-branch continuation;
- validates the source repository, run identity, and review-only scope before
  model execution;
- checks out the exact pull request head SHA without persisted GitHub
  credentials;
- runs Codex as an unprivileged user with read-only Git metadata;
- provides short-lived Bedrock credentials through a runner-owned loopback
  endpoint;
- disables outbound model-tool networking, web search, approval prompts,
  plugins, apps, hooks, browser, computer, image, and multi-agent tools;
- never exposes a write-enabled GitHub token to the model job;
- transfers only validated state, result data, and a bounded patch to the
  separate publication job; and
- revalidates the pull request, selected commands, changed paths, and
  publication preconditions before using write credentials.
