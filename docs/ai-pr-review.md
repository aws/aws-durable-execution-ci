# AI pull request review

The reusable AI review workflow runs independent Claude and Codex reviews
through Amazon Bedrock. It checks out only the pull request's trusted base
revision, builds a SHA-anchored review context through the GitHub API, and runs
both reviewers as unprivileged, read-only users.

Claude and Codex each run in one reusable workflow with separate generation and
comment publication jobs. The generation jobs have read-only GitHub access
(plus OIDC access for Amazon Bedrock) and pass their output through short-lived
workflow artifacts. Only publication jobs receive `pull-requests: write`; they
receive no AWS credentials and run neither model.

Both reviewers return structured findings. The publication jobs re-check the
pull request revision, validate every requested path and right-side line range
against GitHub's diff, and then publish inline comments. Small, unambiguous
fixes include GitHub `suggestion` blocks so a maintainer can apply them
directly. Each inline comment identifies whether it came from Claude or Codex;
the AI reviewers never edit the branch.

## Usage

Add this caller as `.github/workflows/ai-pr-review.yml` in a consuming
repository:

```yaml
name: AI PR Review

on:
  pull_request_target:
    types: [opened, synchronize, reopened, ready_for_review]
  issue_comment:
    types: [created]

permissions: {}

jobs:
  ai-pr-review:
    permissions:
      contents: read
      id-token: write
      pull-requests: write
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-pr-review.yml@<full-commit-sha>
    secrets: inherit
```

Replace `<full-commit-sha>` with the 40-character commit SHA to use. Pinning is
especially important because `secrets: inherit` grants the reusable workflow
access to secrets available to the caller.

## Request a review

A team member can post `/ai review` as a standalone comment on an open pull
request to review its current revision. Leading and trailing spaces or tabs are
ignored, and one or more spaces or tabs may separate `/ai` and `review`. The
author must currently have `write`, `maintain`, or `admin` repository
permission and must not be a bot.

Dependabot, draft pull requests, and pull requests from forks do not run an
automatic AI review. An authorized command starts the enabled generation and
publication jobs directly for those pull requests. The workflow resolves the
pull request's current base and head revisions before starting, and publication
still stops if either revision changes while the review is running.

Concurrency is applied only after resolution. Claude and Codex each use a
separate group keyed by repository ID and pull request number, so both
reviewers can run in parallel while a newer command or synchronization event
replaces only an older run of the same reviewer on the same pull request.
Ordinary comments that are not authorized review commands never enter those
groups and cannot cancel an active review.

The caller must declare the `issue_comment` event shown above. A reusable
workflow does not add its own event triggers to the caller.

## Select reviewers

Claude and Codex run by default. To run only one reviewer, disable the other
one:

```yaml
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-pr-review.yml@<full-commit-sha>
    with:
      run-claude: false
      run-codex: true
    secrets: inherit
```

## Models and reasoning

Each reviewer has independent model and reasoning settings. For example, this
keeps the default model IDs while lowering their reasoning efforts:

```yaml
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-pr-review.yml@<full-commit-sha>
    with:
      claude-model: us.anthropic.claude-sonnet-5
      claude-reasoning-effort: high
      codex-model: openai.gpt-5.6-sol
      codex-reasoning-effort: medium
    secrets: inherit
```

Claude reasoning can be `low`, `medium`, `high`, `xhigh`, or `max`. Codex
reasoning can be `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`.
Both reviewers default to `xhigh`.

Claude runs in bare mode with only the `Read`, `Grep`, and `Glob` built-in
tools available.

Model IDs must be available through the configured Amazon Bedrock provider.
Not every model supports every reasoning level, so the selected model still
validates the requested combination.

## Custom prompt

The shared prompt is used by default. To override it, add a prompt file to the
consuming repository and pass its repository-relative path:

```yaml
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-pr-review.yml@<full-commit-sha>
    with:
      prompt-path: .github/prompts/ai-pr-review.md
    secrets: inherit
```

The prompt is loaded from the trusted base revision. It must be a readable,
non-empty file inside the consuming repository. The shared workflow appends its
trusted structured-output contract to custom prompts. Inline comments are
limited to changed diff hunks and must include at least one added line.

## Repository setup

Create an `ai-pr-review-runtime` environment in each consuming repository. Add
the `BEDROCK_ROLE_ARN` secret containing the IAM role that GitHub's OIDC
provider can assume. Do not add required reviewers or a wait timer to this
environment; every model-generation job uses it, so either rule would require
manual approval for every review.

To use a different environment, pass its name to the reusable workflow:

```yaml
    with:
      environment-name: ai-runtime
```

Keep `BEDROCK_ROLE_ARN` in the selected environment. The caller must still
specify `secrets: inherit` for GitHub to resolve environment-scoped secrets
inside cross-repository reusable jobs. The model-generation jobs remain bound
to that environment, including its protection rules and branch policies.

Non-draft, non-Dependabot pull requests from branches in the same repository
are reviewed automatically. The runtime role is restricted to the model APIs
by inline session policies in the shared workflow.

The shared workflow loads its scripts and prompts from
`job.workflow_repository` at `job.workflow_sha`, so callers only need the
workflow above and always use support files from the same immutable revision.
It also listens for pull requests in this repository, so changes to the shared
CI implementation receive the same AI review directly.
