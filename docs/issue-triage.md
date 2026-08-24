# Issue triage

The reusable issue-triage workflow uses Codex through Amazon Bedrock to
classify every newly opened issue. By default, the model may apply:

| Section | What it captures | Labels |
| --- | --- | --- |
| Package | Where in the monorepo | `pkg:sdk`, `pkg:testing`, `pkg:otel` |
| Issue kind | Report or request type | `bug`, `enhancement`, `question` |
| Area / topic | Orthogonal work facet | `documentation`, `parity` |
| Compatibility | Potential backward compatibility impact | `BREAKING` |
| Priority | Work that jumps the queue | `urgent` |
| Lifecycle | Transient in-flight state | `needs-triage`, `needs-info` |
| Community | Contribution signals | `good first issue`, `help wanted` |

Consuming repositories can replace this list with their own repository labels.
The model receives read-only issue access. A separate job re-fetches the
repository's labels, validates the configured names and structured model
output, and applies only labels from the configured list.

The default issue policy excludes the PR review/merge labels `needs-review`,
`changes-requested`, `needs-rebase`, `do-not-merge`, and `ready-to-merge`, and
the PR automation labels `dependencies` and `github_actions`. A consuming
repository can explicitly opt into any of them through the `labels` input and
repository-specific prompt guidance.

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
        pkg:sdk
        pkg:testing
        pkg:otel
        bug
        enhancement
        question
        documentation
        parity
        BREAKING
        urgent
        needs-triage
        needs-info
        good first issue
        help wanted
    secrets: inherit
```

Names are matched case-insensitively and the repository's canonical spelling
is passed to the model. Configured names that do not exist in a consuming
repository are omitted. This allows a shared list to include
repository-specific labels. The job fails and applies the `needs-triage`
fallback if none of the configured labels exist. The default prompt permits
compatible combinations across distinct facets and multiple package labels for
cross-package work, while preserving mutually exclusive alternatives within a
facet. It treats `urgent` and community signals conservatively and keeps
resolution, project-management, and PR-only labels out of the default policy.

## Prompt configuration

Use the `prompt-path` input to replace the default classification guidance with
a UTF-8 Markdown file from the consuming repository:

```yaml
    uses: aws/aws-durable-execution-ci/.github/workflows/issue-triage.yml@<full-commit-sha>
    with:
      prompt-path: .github/prompts/issue-triage.md
    secrets: inherit
```

The path must be relative to the repository and name a non-empty regular file
no larger than 64 KiB. The workflow retrieves only that file through the GitHub
Contents API at the caller's exact commit SHA; it does not check out or execute
code from the consuming repository.

The custom file replaces only the repository's classification guidance. The
workflow always appends its own immutable prompt-injection defenses, allowed
label constraint, and structured-output requirements. Custom guidance cannot
enable tools, repository access, network access, approvals, or additional
output fields.

## Repository setup

Create the `ai-pr-review-runtime` environment in each consuming repository if
it does not already exist. Add the `BEDROCK_ROLE_ARN` secret containing the IAM
role that GitHub's OIDC provider can assume. Do not add required reviewers or a
wait timer to this environment because every new issue invokes the model.

The workflow uses the same runtime environment and role as the AI pull request
review workflow. To select a different environment, pass
`environment-name: ai-runtime` in the caller's `with` block. The caller must
specify `secrets: inherit` so GitHub resolves the environment-scoped secret
inside the reusable job.

The role is restricted to the Codex Amazon Bedrock inference APIs by an inline
session policy. The model job receives `contents: read`, `issues: read`, and
`id-token: write`; only the separate publication job receives `issues: write`.

## Security model

Issue titles, bodies, label names, and label descriptions are all treated as
untrusted data. They are serialized as JSON and appended to Codex over stdin;
the resolved classification policy remains the separate prompt argument.

The generation job:

- Checks out only support files from the workflow's immutable revision and
  never checks out the consuming repository. An optional custom prompt is
  fetched as one file at the caller's exact commit.
- Appends workflow-owned security and output instructions after either the
  default or custom classification guidance.
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
