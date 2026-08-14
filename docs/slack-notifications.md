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
    permissions:
      contents: read
      id-token: write
    uses: aws/aws-durable-execution-ci/.github/workflows/notify.yml@<full-commit-sha>
    secrets:
      BEDROCK_ROLE_ARN: ${{ secrets.BEDROCK_ROLE_ARN }}
      SLACK_WEBHOOK_URL_PR: ${{ secrets.SLACK_WEBHOOK_URL_PR }}
      SLACK_WEBHOOK_URL_ISSUE: ${{ secrets.SLACK_WEBHOOK_URL_ISSUE }}
      SLACK_WEBHOOK_URL_DISCUSSION: ${{ secrets.SLACK_WEBHOOK_URL_DISCUSSION }}
      SLACK_WEBHOOK_URL_RELEASE: ${{ secrets.SLACK_WEBHOOK_URL_RELEASE }}
```

Replace `<full-commit-sha>` with the 40-character commit SHA to use.

## Behavior

Draft pull requests are skipped. Using `pull_request_target` makes the webhook
available for pull requests from forks; the shared workflow does not check out
or execute pull request code. The shared workflow generates a concise summary
from the event title and body, selects the event-specific webhook, and
populates the `package_name` payload field from the caller's
`github.repository`.

Every pull request, issue, discussion, and release payload includes a
`summary` field. Configure the corresponding Slack webhook workflows to accept
and display that field. If Amazon Bedrock credentials are unavailable or Codex
returns invalid output, the notification still sends with a deterministic
title-based summary.

The discussion webhook is optional. Repositories that do not notify on
discussions can omit both the `discussion` trigger and
`SLACK_WEBHOOK_URL_DISCUSSION` secret mapping.

## Model configuration

The default model is `openai.gpt-5.6-luna`. Callers can select another Codex
model available through Amazon Bedrock:

```yaml
    uses: aws/aws-durable-execution-ci/.github/workflows/notify.yml@<full-commit-sha>
    with:
      model: openai.gpt-5.6-luna
    secrets:
      BEDROCK_ROLE_ARN: ${{ secrets.BEDROCK_ROLE_ARN }}
      SLACK_WEBHOOK_URL_PR: ${{ secrets.SLACK_WEBHOOK_URL_PR }}
      SLACK_WEBHOOK_URL_ISSUE: ${{ secrets.SLACK_WEBHOOK_URL_ISSUE }}
      SLACK_WEBHOOK_URL_DISCUSSION: ${{ secrets.SLACK_WEBHOOK_URL_DISCUSSION }}
      SLACK_WEBHOOK_URL_RELEASE: ${{ secrets.SLACK_WEBHOOK_URL_RELEASE }}
```

The caller must grant `id-token: write` and pass `BEDROCK_ROLE_ARN`. The role
must allow the Bedrock Mantle inference actions used by the workflow. If the
secret is omitted, notification delivery remains enabled but uses the
deterministic fallback summary.

## Security model

Titles and bodies are untrusted model data, not instructions. The summarizer
uses a fixed system prompt, sends bounded JSON input to Codex on Amazon
Bedrock, accepts at most 64 KiB of response data, and limits the normalized
summary to 240 displayed characters. Codex runs as an unprivileged user with
tools disabled and no access to the trusted workflow checkout. The sanitizer
removes control characters and explicit URLs and email addresses, defangs bare
domains, escapes Slack control syntax, and neutralizes broadcast mentions
while preserving readable technical content.

Model inference and Slack delivery run in separate jobs. The model job has
only `contents: read` and `id-token: write`, receives no webhook secrets, and
loads only the trusted summary scripts from the reusable workflow's immutable
commit. The Slack jobs have no repository or model permissions, receive only
the event-specific webhook, and execute no repository code. They still send
with an event-specific generic summary if the model job itself fails before
producing output.

## Migration

The release notification is independent of package publication. Remove the old
`notify-release` job from Python's `pypi-publish.yml` and JavaScript's
`npm-publish.yml`.

Once all references have been replaced, the consuming repository's old
`notify-pr.yml`, `notify-issues.yml`, `notify-discussions.yml`, and
`notify-release.yml` files can be removed where present.
