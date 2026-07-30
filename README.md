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
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-pr-review.yml@<full-commit-sha>
```

Replace `<full-commit-sha>` with the 40-character commit SHA to use.

### Custom prompt

The shared prompt is used by default. To override it, add a prompt file to the
consuming repository and pass its repository-relative path:

```yaml
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-pr-review.yml@<full-commit-sha>
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

## Slack notifications

The reusable Slack workflow preserves the notification payloads used by the
Durable Execution SDK repositories. Each consuming repository needs only one
notification workflow for pull request, issue, discussion, and release events.

Add `.github/workflows/notify.yml` to the consuming repository:

```yaml
name: Notify Slack

on:
  pull_request_target:
    types: [opened, reopened, ready_for_review]
  issues:
    types: [opened, reopened]
  discussion:
    types: [created]
  release:
    types: [published]

permissions: {}

jobs:
  notify:
    uses: aws/aws-durable-execution-ci/.github/workflows/notify.yml@<full-commit-sha>
    secrets:
      SLACK_WEBHOOK_URL_PR: ${{ secrets.SLACK_WEBHOOK_URL_PR }}
      SLACK_WEBHOOK_URL_ISSUE: ${{ secrets.SLACK_WEBHOOK_URL_ISSUE }}
      SLACK_WEBHOOK_URL_DISCUSSION: ${{ secrets.SLACK_WEBHOOK_URL_DISCUSSION }}
      SLACK_WEBHOOK_URL_RELEASE: ${{ secrets.SLACK_WEBHOOK_URL_RELEASE }}
```

Draft pull requests are skipped. Using `pull_request_target` makes the webhook
available for pull requests from forks; the shared workflow does not check out
or execute pull request code. The shared workflow selects the event-specific
webhook and populates the `package_name` payload field from the caller's
`github.repository`.

The discussion webhook is optional. Repositories that do not notify on
discussions can omit both the `discussion` trigger and
`SLACK_WEBHOOK_URL_DISCUSSION` secret mapping.

The release notification is now independent of package publication. Remove
the old `notify-release` job from Python's `pypi-publish.yml` and JavaScript's
`npm-publish.yml`.

Once all references have been replaced, the consuming repository's old
`notify-pr.yml`, `notify-issues.yml`, `notify-discussions.yml`, and
`notify-release.yml` files can be removed where present.

Replace `<full-commit-sha>` with the 40-character commit SHA to use.

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more
information.

## License

This project is licensed under the Apache-2.0 License.
