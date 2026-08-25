# AI issue implementation

The reusable AI issue implementation workflow turns authorized `/ai implement`
commands on open issues into draft pull requests. The entry workflow is named
for the capability rather than its current provider so additional agents can
be supported later. It currently runs Codex through Amazon Bedrock.

Pull request review addressing is a separate reusable workflow. See
[AI PR review address](ai-pr-review-address.md).

## Caller workflow

Add `.github/workflows/ai-issue-implementation.yml` to the consuming
repository:

```yaml
name: AI Issue Implementation

on:
  issue_comment:
    types: [created]
  workflow_dispatch:
    inputs:
      issue-number:
        description: Authorized issue number to implement
        required: true
        type: string

permissions: {}

jobs:
  implement:
    permissions:
      contents: write
      id-token: write
      issues: write
      pull-requests: write
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-issue-implementation.yml@<full-commit-sha>
    with:
      issue-number: ${{ inputs['issue-number'] || '' }}
    secrets: inherit
```

Replace `<full-commit-sha>` with the 40-character commit SHA to use. Pinning
keeps the workflow, trusted prompt, output schema, and publication policy on
one immutable revision.

A reusable workflow does not add event triggers to its caller. Each consuming
repository must declare the issue-comment and manual triggers shown above.
There is no scheduled recovery scan. Use GitHub's rerun action for a missed or
failed event, or manually dispatch the workflow with a specific issue number.

## Request implementation

Post this command on an open issue:

```text
/ai implement
```

Leading and trailing spaces or tabs are ignored, and one or more spaces or
tabs may separate `/ai` and `implement`. Additional text after the command is
passed to Codex as task-specific maintainer guidance and may continue on later
lines.

The command author must currently have `write`, `maintain`, or `admin`
repository permission and must not be a bot. Implementation labels are not
used as authorization. Other commands and emoji reactions do not start work.

The workflow resolves only issue implementation work. It ignores `/ai address`
commands and does not convert linked pull request feedback into review-address
work.

## Workflow changes

To authorize changes under `.github/workflows/**` for one request, place the
option immediately after the command:

```text
/ai implement --allow-workflow-changes

Keep the workflow change narrowly scoped.
```

The option is removed before the remaining guidance is sent to Codex.
Mentioning `--allow-workflow-changes` later in the guidance does not grant
permission.

Authorized workflow changes require this repository or organization Actions
secret:

- `CODEX_WORKFLOW_PUSH_TOKEN`: a token scoped to the target repository with
  `Contents: write` and `Workflows: write`.

The token is available only to the publication checkout when a validated patch
changes `.github/workflows/**`; it is never available to the Codex process.
Ordinary publication uses `GITHUB_TOKEN`. If a workflow change is authorized
but the secret is unavailable, publication fails before checkout.

## Configuration

The reusable workflow accepts these optional inputs:

- `issue-number`: an explicit issue number for a manual run.
- `environment-name`: GitHub environment for the model job; defaults to
  `ai-pr-review-runtime`.
- `no-pr-label`: label used when no repository change is required; defaults to
  `codex:no-pr`.
- `model`: Codex model ID; defaults to `openai.gpt-5.6-sol`.
- `reasoning-effort`: defaults to `xhigh`.
- `allow-workflow-changes`: workflow-wide administrative override; defaults
  to `false`.

Example:

```yaml
    with:
      issue-number: ${{ inputs['issue-number'] || '' }}
      environment-name: ai-runtime
      no-pr-label: automation:no-pr
      model: openai.gpt-5.6-sol
      reasoning-effort: xhigh
      allow-workflow-changes: false
```

## Repository setup

Create the configured GitHub environment with this secret:

- `BEDROCK_ROLE_ARN`: the IAM role that GitHub's OIDC provider can assume.

The caller must use `secrets: inherit` so GitHub can resolve the
environment-scoped secret inside the reusable workflow. The environment may
apply protection rules, but its branch policy must allow the refs from which
issue implementation is expected to run.

Allow GitHub Actions to create pull requests in the repository settings. The
caller must grant the contents, identity-token, issue, and pull request
permissions shown above; a called workflow cannot elevate permissions that
the caller withheld.

The workflow creates the configured no-PR label when it needs to report that
an issue requires no repository change. Creating the label in advance is
recommended so its color and description follow repository conventions.
Remove the label before explicitly asking Codex to reconsider the issue.

The workflow does not merge or approve pull requests.

## Execution and publication

The workflow re-fetches the current command authorization and issue state
after acquiring an issue-scoped concurrency group:

```text
codex-<repository-id>-issue-<issue-number>
```

A running pipeline is not canceled between model execution and publication by
a newer event for the same issue. Different issues can run in parallel. Codex
model execution is limited to two hours, with additional time reserved for
setup and post-model validation.

With no linked open pull request, the worker checks out the exact current
default branch revision. A change is committed to the deterministic
`implement-issue-<number>` branch and published with an exact
`--force-with-lease` comparison. The workflow checks again for a linked pull
request before pushing and before opening one draft pull request. Its title
identifies the issue and requested work. Its body closes the issue and records
the issue title, command guidance, model summary, changed paths, and validation
performed.

If the default branch advances while the model runs, the validated change may
still be published from its original revision and opened against the current
default branch.

A later run can recover a workflow-owned branch when the push succeeded but
pull request creation did not. Commit trailers bind recovery to the original
issue and implementation command and preserve bounded description metadata.
Older automation commits that contain only the issue snapshot remain
recoverable with the summary available from their commit message.

When an open pull request already closes the issue, this workflow does not
update it or create another pull request. Use the PR review-address workflow
for explicit review feedback on that pull request. Fork branches are never
updated. When multiple open pull requests close the issue, the workflow posts
a deduplicated issue comment rather than choosing one.

When Codex determines that no repository change is required, the workflow
applies the configured no-PR label and posts a deduplicated explanation.

## Security model

Issue titles, bodies, labels, comments, pull request data, and repository
content are untrusted model input.

The workflow:

- loads its helper, prompt, and schema from the immutable reusable-workflow
  revision;
- checks out an exact target SHA without persisted GitHub credentials;
- runs Codex as an unprivileged user with read-only Git metadata;
- provides short-lived Bedrock credentials through a runner-owned loopback
  endpoint;
- disables outbound model-tool networking, web search, approval prompts,
  plugins, apps, hooks, browser, computer, image, and multi-agent tools;
- never exposes a write-enabled GitHub token to the model job;
- transfers only validated state, result data, and a bounded patch to the
  separate publication job; and
- revalidates command authorization, repository state, changed paths, and
  publication preconditions before using write credentials.

The model job has a read-only automatic token. Only the publication job has
contents, issue, and pull request write permissions.
