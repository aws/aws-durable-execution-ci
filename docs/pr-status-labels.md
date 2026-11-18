# PR status labels

The PR status labels workflow keeps two pull request review labels current,
so a label search shows which pull requests wait on maintainers and which
wait on their authors. It changes only `needs-review` and
`changes-requested`. Maintainers still set `changes-requested`,
`needs-rebase`, `do-not-merge`, and `ready-to-merge` by hand.

## Behavior

The workflow skips draft pull requests and pull requests opened by bots.

| Event | Condition | Change |
| --- | --- | --- |
| Opened, reopened, or marked ready for review | The author has no write access, and the pull request has no status label | Adds `needs-review` |
| Pushed to by its author, or marked ready for review | The pull request has `changes-requested` | Adds `needs-review` and removes `changes-requested` |

The status labels are `needs-review`, `changes-requested`, `needs-rebase`,
`do-not-merge`, and `ready-to-merge`. Write access means the repository
collaborator permission API reports `admin` or `write` for the author. That
API reports the maintain role as `write`, so maintainers count. The workflow
does not use `author_association`, because GitHub reports organization
members with read-only access as `MEMBER`.

Only a push by the pull request author counts as an update. A maintainer who
pushes a fix or clicks **Update branch** leaves `changes-requested` in place.
The job does not start for those pushes, or for pushes to a pull request
without `changes-requested`.

The script reads the pull request's current labels before it changes them.
It replaces `changes-requested` only when the pull request's events show
that the label was last applied before the push or the ready-for-review, so
a job that starts late, or a re-run of an old run, keeps a request that a
maintainer made after the event. It adds
`needs-review` before it removes `changes-requested`, so a failure leaves
at least one of them in place. A missing `needs-review` label is created.

This repository runs the workflow on its own pull requests. Other
repositories call it as a reusable workflow.

## Usage

Add `.github/workflows/pr-status-labels.yml` to the consuming repository:

```yaml
name: PR Status Labels

on:
  pull_request_target:
    types: [opened, reopened, ready_for_review, synchronize]

permissions: {}

jobs:
  pr-status-labels:
    permissions:
      contents: read
      pull-requests: write
    uses: aws/aws-durable-execution-ci/.github/workflows/pr-status-labels.yml@<full-commit-sha>
```

Replace `<full-commit-sha>` with the 40-character commit SHA to use.

## Configuration

| Input | Default | Description |
| --- | --- | --- |
| `runs-on` | `ubuntu-latest` | Runner label for every job. |

## Finding pull requests that need attention

These searches list the review queue. Add one `repo:` qualifier per
repository to search several repositories at once.

| Waiting on | Search |
| --- | --- |
| Maintainers | `is:pr is:open -is:draft label:needs-review -review:approved` |
| Authors | `is:pr is:open label:changes-requested,needs-rebase` |

## Limitations

- The workflow never adds `changes-requested`. A review that requests
  changes on a fork pull request runs with a read-only token, so applying
  the label from that review would need a separate trusted workflow.
- A push by the author that only merges the base branch still counts as an
  update.
- Commits that a workflow pushes, such as `/ai address` fixes, are not pushes
  by the author, so they leave `changes-requested` in place.

## Security model

`pull_request_target` runs the workflow from the default branch of the base
repository, so the token can label pull requests from forks, and the run
needs no approval for first-time contributors. The job checks out only
`scripts/pr_status_labels.py` from the workflow's immutable revision and
never checks out or runs pull request code. Event values reach the script as
environment variables, and the script reads the pull request through the
API. The token is limited to `contents: read` and `pull-requests: write`,
and the job receives no secrets.
