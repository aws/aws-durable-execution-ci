# AWS Durable Execution CI

Shared GitHub Actions workflows for AWS Durable Execution repositories.

## AI pull request review

The reusable AI review workflow runs independent Claude and Codex reviews
through Amazon Bedrock. It checks out only the pull request's trusted base
revision, builds a SHA-anchored review context through the GitHub API, and runs
both reviewers as unprivileged, read-only users.

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
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-pr-review.yml@main
```

Pin the `uses` reference to a release tag or full commit SHA when one is
available.

### Custom prompt

The shared prompt is used by default. To override it, add a prompt file to the
consuming repository and pass its repository-relative path:

```yaml
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-pr-review.yml@main
    with:
      prompt-path: .github/prompts/ai-pr-review.md
```

The prompt is loaded from the trusted base revision. It must be a readable,
non-empty file inside the consuming repository.

### Repository setup

Create these environments in each consuming repository:

- `ai-pr-review`: Add required reviewers. This approval gates reviews for
  Dependabot, draft pull requests, and pull requests from forks.
- `ai-pr-review-runtime`: Add the `BEDROCK_ROLE_ARN` secret containing the IAM
  role that GitHub's OIDC provider can assume.

Trusted, non-draft branches in the same repository are reviewed automatically.
The runtime role is restricted to the model APIs by inline session policies in
the shared workflow.

Alternatively, store `BEDROCK_ROLE_ARN` as a repository or organization secret
and pass it explicitly from the caller:

```yaml
    secrets:
      BEDROCK_ROLE_ARN: ${{ secrets.BEDROCK_ROLE_ARN }}
```

The shared workflow loads its scripts and prompts from
`job.workflow_repository` at `job.workflow_sha`, so callers only need the
workflow above and always use support files from the same immutable revision.

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more
information.

## License

This project is licensed under the Apache-2.0 License.
