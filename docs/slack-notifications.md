# Slack notifications

The reusable Slack workflow preserves the notification payloads used by the
Durable Execution SDK repositories. Each consuming repository needs only one
notification workflow for pull request, issue, discussion, and release events.

## Usage

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

Replace `<full-commit-sha>` with the 40-character commit SHA to use.

## Behavior

Draft pull requests are skipped. Using `pull_request_target` makes the webhook
available for pull requests from forks; the shared workflow does not check out
or execute pull request code. The shared workflow selects the event-specific
webhook and populates the `package_name` payload field from the caller's
`github.repository`.

The discussion webhook is optional. Repositories that do not notify on
discussions can omit both the `discussion` trigger and
`SLACK_WEBHOOK_URL_DISCUSSION` secret mapping.

## Migration

The release notification is independent of package publication. Remove the old
`notify-release` job from Python's `pypi-publish.yml` and JavaScript's
`npm-publish.yml`.

Once all references have been replaced, the consuming repository's old
`notify-pr.yml`, `notify-issues.yml`, `notify-discussions.yml`, and
`notify-release.yml` files can be removed where present.
