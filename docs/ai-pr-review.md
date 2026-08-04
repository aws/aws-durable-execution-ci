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

Create these environments in each consuming repository:

- `ai-pr-review`: Add required reviewers. This approval gates reviews for
  Dependabot, draft pull requests, and pull requests from forks.
- `ai-pr-review-runtime`: Add the `BEDROCK_ROLE_ARN` secret containing the IAM
  role that GitHub's OIDC provider can assume.

Keep `BEDROCK_ROLE_ARN` in `ai-pr-review-runtime`. The caller must still specify
`secrets: inherit` for GitHub to resolve environment-scoped secrets inside
cross-repository reusable jobs. The model-generation jobs remain bound to
`ai-pr-review-runtime`, including its protection rules and branch policies.

Trusted, non-draft branches in the same repository are reviewed automatically.
The runtime role is restricted to the model APIs by inline session policies in
the shared workflow.

The shared workflow loads its scripts and prompts from
`job.workflow_repository` at `job.workflow_sha`, so callers only need the
workflow above and always use support files from the same immutable revision.
It also listens for pull requests in this repository, so changes to the shared
CI implementation receive the same AI review directly.
