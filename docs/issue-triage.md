# Issue triage

The reusable issue-triage workflow uses Codex through Amazon Bedrock to
classify every newly opened issue. By default, the model may apply:

- `bug`
- `documentation`
- `enhancement`
- `question`
- `parity`
- `BREAKING`
- `needs-triage`

Consuming repositories can replace this list with their own repository labels.
The model receives read-only issue access. A separate job re-fetches the
repository's labels, validates the configured names and structured model
output, and applies only labels from the configured list.

If model generation fails, the workflow preserves the manual-triage path by
applying `needs-triage`. That fallback label is created when it does not
already exist.

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
      contents: read
      id-token: write
      issues: write
    uses: aws/aws-durable-execution-ci/.github/workflows/issue-triage.yml@<full-commit-sha>
    secrets: inherit
```

Replace `<full-commit-sha>` with the 40-character commit SHA to use.

## Models and reasoning

The default model is `openai.gpt-5.6-sol` with `medium` reasoning. Both can be
overridden:

```yaml
    uses: aws/aws-durable-execution-ci/.github/workflows/issue-triage.yml@<full-commit-sha>
    with:
      model: openai.gpt-5.6-sol
      reasoning-effort: low
    secrets: inherit
```

Reasoning can be `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or
`max`. The model ID must be available through the configured Amazon Bedrock
provider, and the selected model must support the requested reasoning level.

## Label configuration

Use the `labels` input to replace the default list. Provide one existing label
name per line. The workflow reads each label's current description from the
consuming repository and includes it in the model context.

```yaml
    uses: aws/aws-durable-execution-ci/.github/workflows/issue-triage.yml@<full-commit-sha>
    with:
      labels: |
        bug
        documentation
        enhancement
        question
        parity
        BREAKING
        needs-triage
        otel-plugin
        testing-sdk
    secrets: inherit
```

Names are matched case-insensitively and the repository's canonical spelling
is passed to the model. Configured names that do not exist in a consuming
repository are omitted. This allows a shared list to include
repository-specific labels. The job fails and applies the `needs-triage`
fallback if none of the configured labels exist. Keep workflow-state,
resolution, ownership, difficulty, merge, and project-management labels other
than `needs-triage` out of this list unless the repository explicitly wants the
model to apply them.

## Repository setup

Create the `ai-pr-review-runtime` environment in each consuming repository if
it does not already exist. Add the `BEDROCK_ROLE_ARN` secret containing the IAM
role that GitHub's OIDC provider can assume. Do not add required reviewers or a
wait timer to this environment because every new issue invokes the model.

The workflow uses the same runtime environment and role as the AI pull request
review workflow. The caller must specify `secrets: inherit` so GitHub resolves
the environment-scoped secret inside the reusable job.

The role is restricted to the Codex Amazon Bedrock inference APIs by an inline
session policy. The model job receives `contents: read`, `issues: read`, and
`id-token: write`; only the separate publication job receives `issues: write`.

## Security model

Issue titles, bodies, label names, and label descriptions are all treated as
untrusted data. They are serialized as JSON and appended to Codex over stdin;
the trusted classification policy remains the separate prompt argument.

The generation job:

- Checks out only support files from the workflow's immutable revision and
  never checks out the consuming repository.
- Runs Codex as an unprivileged user that cannot traverse the workflow
  workspace.
- Uses an empty ephemeral Codex home, ignores user configuration and rule
  files, disables approvals and all execution, web, app, hook, browser,
  computer-use, image, and multi-agent tools, and explicitly selects the
  read-only sandbox.
- Generates a JSON schema whose enum contains only the configured canonical
  label names, then validates the model output again outside the model process.

The short-lived artifact contains only validated labels and a digest of the
exact issue content that was classified. The publication job has no AWS
credentials, re-fetches the issue and allowed labels, validates the artifact,
and checks the issue digest twice before applying labels. If generation or
publication validation fails, `needs-triage` is applied for manual review.
