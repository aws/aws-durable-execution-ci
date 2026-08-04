# Issue triage

The reusable issue-triage workflow automatically applies a `needs-triage` label
to every newly opened issue. If the label does not exist in the repository, it
is created automatically.

## Usage

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
