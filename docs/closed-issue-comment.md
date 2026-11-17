# Closed issue comment

The reusable closed-issue-comment workflow catches comments that outside users
post on closed issues, where the team is unlikely to see them. It replies with a
pointer to open a new issue and sends a Slack notification.

## Behavior

On each new issue comment, the workflow:

1. Skips pull requests and open issues.
2. Skips comments from `OWNER`, `MEMBER`, and `COLLABORATOR` authors and from
   bots.
3. Posts a reply asking the user to open a new issue that links to the closed
   one. The reply is skipped when the issue was closed fewer than
   `reply-after-days` days ago, so a quick thank-you after a fix gets no reply.
4. Sends a Slack notification to `SLACK_WEBHOOK_URL_ISSUE`, if the secret is
   set, so the team sees the comment.

The reply carries a hidden marker. It is posted once per closure: a marked
comment from the GitHub Actions bot made after the issue was closed suppresses
further replies. Marker text copied into another user's comment is ignored.

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
    permissions:
      contents: read
      issues: write
    uses: aws/aws-durable-execution-ci/.github/workflows/closed-issue-comment.yml@<full-commit-sha>
    secrets:
      SLACK_WEBHOOK_URL_ISSUE: ${{ secrets.SLACK_WEBHOOK_URL_ISSUE }}
```

Replace `<full-commit-sha>` with the 40-character commit SHA to use.

## Configuration

| Input | Default | Description |
| --- | --- | --- |
| `runs-on` | `ubuntu-latest` | Runner label for every job. |
| `reply-after-days` | `7` | Days an issue must be closed before the bot replies. Slack is notified either way. |

| Secret | Required | Description |
| --- | --- | --- |
| `SLACK_WEBHOOK_URL_ISSUE` | No | Incoming webhook for issue notifications. Without it, only the reply is posted. |

## Security model

The workflow checks out only `scripts/closed_issue_comment.py` from the
workflow's immutable revision and never checks out the consuming repository.
Comment text is never read or executed: the script uses only the issue state,
close time, author association, and author type from the event payload. The
`GITHUB_TOKEN` is scoped to `contents: read` and `issues: write`. The Slack job
has no token permissions.
