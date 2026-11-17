# Closed issue comment

The reusable closed-issue-comment workflow sends a Slack notification when an
outside user comments on a closed issue, where the team is unlikely to see it.
It posts nothing on GitHub.

## Behavior

The workflow sends a notification to `SLACK_WEBHOOK_URL_ISSUE` when all of
these are true:

- the event is a new issue comment (`issue_comment` with action `created`);
- the issue is closed;
- the issue is not a pull request;
- the author is not a bot and is not an `OWNER`, `MEMBER`, or `COLLABORATOR`.

Otherwise the job is skipped. The notification uses the same payload keys as
the issue notification in [Slack notifications](slack-notifications.md), with
`action` set to `commented_on_closed` and `issue_url` set to the comment's URL.

## Usage

Add `.github/workflows/closed-issue-comment.yml` to the consuming repository:

```yaml
name: Closed Issue Comment

on:
  issue_comment:
    types: [created]

permissions: {}

jobs:
  closed-issue-comment:
    uses: aws/aws-durable-execution-ci/.github/workflows/closed-issue-comment.yml@<full-commit-sha>
    secrets:
      SLACK_WEBHOOK_URL_ISSUE: ${{ secrets.SLACK_WEBHOOK_URL_ISSUE }}
```

Replace `<full-commit-sha>` with the 40-character commit SHA to use.

## Configuration

| Input | Default | Description |
| --- | --- | --- |
| `runs-on` | `ubuntu-latest` | Runner label for every job. |

| Secret | Required | Description |
| --- | --- | --- |
| `SLACK_WEBHOOK_URL_ISSUE` | Yes | Incoming webhook for issue notifications. |

## Security model

The workflow checks out no code and its job has no `GITHUB_TOKEN`
permissions. Comment text is never read. The Slack payload includes only the
repository name, issue number, comment URL, and comment author login, each
JSON-encoded with `toJSON`.
