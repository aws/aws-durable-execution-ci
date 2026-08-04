# AWS Durable Execution CI

Shared GitHub Actions workflows for AWS Durable Execution repositories.

## AI pull request review

The reusable AI review workflow runs independent Claude and Codex reviews
through Amazon Bedrock. It checks out only the pull request's trusted base
revision, builds a SHA-anchored review context through the GitHub API, and runs
both reviewers as unprivileged, read-only users.

Codex generation and comment publication run in separate reusable workflows.
The generation workflow has read-only GitHub access (plus OIDC access for
Amazon Bedrock) and passes its output through a short-lived workflow artifact.
Claude uses the same read-only generation and trusted-publication boundary in
the parent workflow. Only publication jobs receive `pull-requests: write`; they
receive no AWS credentials and run neither model.

Both reviewers return structured findings. The publication jobs re-check the
pull request revision, validate every requested path and right-side line range
against GitHub's diff, and then publish inline comments. Small, unambiguous
fixes include GitHub `suggestion` blocks so a maintainer can apply them
directly; the AI reviewers never edit the branch.

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

### Custom prompt

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

### Repository setup

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

## Issue triage

The reusable issue-triage workflow automatically applies a `needs-triage` label
to every newly opened issue. If the label does not exist in the repository it is
created automatically.

Add `.github/workflows/issue-triage.yml` to the consuming repository:

```yaml
name: Issue Triage

on:
  issues:
    types: [opened]

permissions: {}

jobs:
  triage:
    permissions:
      issues: write
    uses: aws/aws-durable-execution-ci/.github/workflows/issue-triage.yml@<full-commit-sha>
```

Replace `<full-commit-sha>` with the 40-character commit SHA to use.

No secrets or additional repository setup is required. The workflow only needs
`issues: write` permission, which is used to create the label (if missing) and
apply it to the new issue.

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more
information.

## License

This project is licensed under the Apache-2.0 License.
