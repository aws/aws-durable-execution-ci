# AI pull request review

The reusable AI review workflow runs independent Claude and Codex reviews
through Amazon Bedrock. It checks out only the pull request's trusted base
revision, builds a SHA-anchored review context through the GitHub API, and runs
both reviewers as unprivileged, read-only users.

Claude and Codex each run in one reusable workflow with separate generation and
comment publication jobs. The generation jobs have read-only GitHub access
(plus OIDC access for Amazon Bedrock) and pass their output through short-lived
workflow artifacts. Only publication jobs receive `pull-requests: write`; they
receive no AWS credentials and run neither model.

Both reviewers return structured findings. The publication jobs re-check the
pull request revision, validate every requested path and right-side line range
against GitHub's diff, and then publish inline comments. Small, unambiguous
fixes include GitHub `suggestion` blocks so a maintainer can apply them
directly. Each inline comment identifies whether it came from Claude or Codex;
the AI reviewers never edit the branch. Every finding also has a stable ID
that can be reused across reruns and later pull request revisions.

Trusted jobs retain review, finding, verdict, revision, and human-review
metadata on the repository's orphan `ai-review-telemetry-v1` branch. Model
jobs cannot write this branch. The telemetry contains versioned JSON rather
than credentials, prompts, model transcripts, unpublished source, or
human-authored comment bodies.

## Usage

Add this caller as `.github/workflows/ai-pr-review.yml` in a consuming
repository:

```yaml
name: AI PR Review

on:
  pull_request_target:
    types: [opened, synchronize, reopened, ready_for_review]
  pull_request_review:
    types: [submitted, edited, dismissed]
  pull_request_review_comment:
    types: [created, edited, deleted]
  issue_comment:
    types: [created]

permissions: {}

jobs:
  ai-pr-review:
    permissions:
      contents: write
      id-token: write
      pull-requests: write
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-pr-review.yml@<full-commit-sha>
    secrets: inherit
```

Replace `<full-commit-sha>` with the 40-character commit SHA to use. Pinning is
especially important because `secrets: inherit` grants the reusable workflow
access to secrets available to the caller.

## Request a review

A team member can post `/ai review` on an open pull request to review its
current revision. Leading and trailing spaces or tabs are ignored, and one or
more spaces or tabs may separate `/ai` and `review`. The author must currently
have `write`, `maintain`, or `admin` repository permission and must not be a
bot.

Append optional guidance after the command to tailor that review run:

```text
/ai review

Focus on public API compatibility and verify the retry and replay tests.
```

Guidance can start on the command line or a later line, is limited to 10,000
UTF-8 bytes, and is sent to both enabled reviewers. It can narrow or prioritize
the review, request additional checks, or provide PR-specific context. It
cannot override the workflow-owned read-only security policy, diff scope, or
structured-output requirements. Automatic reviews run without per-review
guidance.

Dependabot, draft pull requests, and pull requests from forks do not run an
automatic AI review. An authorized command starts the enabled generation and
publication jobs directly for those pull requests. The workflow resolves the
pull request's current base and head revisions before starting, and publication
still stops if either revision changes while the review is running.

Concurrency is applied only after resolution. Claude and Codex each use a
separate group keyed by repository ID and pull request number, so both
reviewers can run in parallel while a newer command or synchronization event
replaces only an older run of the same reviewer on the same pull request.
Ordinary comments that are not authorized review commands never enter those
groups and cannot cancel an active review.

The caller must declare the `issue_comment` event shown above. A reusable
workflow does not add its own event triggers to the caller. The
`pull_request_review` and `pull_request_review_comment` events retain human
review workload and allow inline verdict replies. `contents: write` is the
caller-level ceiling needed by trusted telemetry jobs; model jobs explicitly
retain only `contents: read`.

## Finding identity

Each model finding includes a stable semantic key based on the component,
nearest symbol or construct, and violated invariant. It excludes line numbers,
commit IDs, severity, and rendered prose. The trusted publisher hashes that
key with the repository, pull request, and reviewer identity to create an
`arf_v1_...` finding ID.

The model also receives a bounded catalog of durable prior findings from the
same reviewer. When a later review identifies the same root cause, it can
return the trusted prior ID. The publisher validates that reference before
reusing it. Path and line remain evidence for an observation rather than the
finding's identity.

Published comments contain hidden machine metadata and a visible finding ID:

```text
Codex AI review · Finding `arf_v1_...`
```

Each reviewer execution and each finding observation also receive separate
stable IDs. This distinguishes repeated observations from genuinely new
findings.

## Record a finding verdict

A maintainer with current `write`, `maintain`, or `admin` permission can reply
to an AI inline comment:

```text
/ai verdict accepted
```

The conversation-level form includes the finding ID:

```text
/ai verdict arf_v1_... false-positive
```

The allowed outcomes are:

- `accepted`
- `deferred`
- `false-positive`
- `out-of-scope`
- `already-fixed`

The workflow verifies the root AI comment or durable finding record, captures
the maintainer identity, reviewed base and head SHA, current head SHA, command
timestamp, and comment ID, then acknowledges the verdict. It stores
superseding commands as new immutable events rather than editing history.

## Measurement telemetry

The workflow records:

- `opened`, `synchronize`, `reopened`, and `ready_for_review` event timestamps
  and event revisions.
- The resolved base and head SHA reviewed by each model.
- Planned, published, and failed review executions.
- Stable findings, repeated observations, and published GitHub comment IDs.
- Structured maintainer verdicts.
- Human reviews, approvals, inline comments, replies, and conversation
  response timestamps without their bodies.
- Exact suggestion adoption when a later revision uniquely matches the
  published replacement and surrounding context.

Data is stored as immutable JSON files under `events/` on the orphan
`ai-review-telemetry-v1` branch. Stable finding and suggestion metadata live
under `findings/` and `suggestions/`. The writer uses optimistic,
non-force branch updates and treats an attempt to rewrite an existing record
with different bytes as an error.

Export the durable event stream as canonical newline-delimited JSON:

```bash
GH_TOKEN=... python3 scripts/export_ai_review_telemetry.py \
  --repository owner/repository \
  --output ai-review-telemetry.jsonl
```

The export is ordered by event and record timestamp and does not parse
natural-language comment bodies. It supports review latency, revision response
time, finding-to-corrective-revision time, suggestion adoption, applicable
finding precision, repeated findings, human feedback rounds, author response,
and time from first human review to approval.

## Select reviewers

Claude and Codex run by default. To run only one reviewer, disable the other
one:

```yaml
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-pr-review.yml@<full-commit-sha>
    with:
      run-claude: false
      run-codex: true
    secrets: inherit
```

## Models and reasoning

Each reviewer has independent model and reasoning settings. For example, this
keeps the default model IDs while lowering their reasoning efforts:

```yaml
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-pr-review.yml@<full-commit-sha>
    with:
      claude-model: us.anthropic.claude-sonnet-5
      claude-reasoning-effort: high
      codex-model: openai.gpt-5.6-sol
      codex-reasoning-effort: medium
    secrets: inherit
```

Claude reasoning can be `low`, `medium`, `high`, `xhigh`, or `max`. Codex
reasoning can be `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`.
Both reviewers default to `xhigh`.

Claude runs in bare mode with only the `Read`, `Grep`, and `Glob` built-in
tools available.

Model IDs must be available through the configured Amazon Bedrock provider.
Not every model supports every reasoning level, so the selected model still
validates the requested combination.

## Custom prompt

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

## Repository setup

Create an `ai-pr-review-runtime` environment in each consuming repository. Add
the `BEDROCK_ROLE_ARN` secret containing the IAM role that GitHub's OIDC
provider can assume. Do not add required reviewers or a wait timer to this
environment; every model-generation job uses it, so either rule would require
manual approval for every review.

To use a different environment, pass its name to the reusable workflow:

```yaml
    with:
      environment-name: ai-runtime
```

Keep `BEDROCK_ROLE_ARN` in the selected environment. The caller must still
specify `secrets: inherit` for GitHub to resolve environment-scoped secrets
inside cross-repository reusable jobs. The model-generation jobs remain bound
to that environment, including its protection rules and branch policies.

Non-draft, non-Dependabot pull requests from branches in the same repository
are reviewed automatically. The runtime role is restricted to the model APIs
by inline session policies in the shared workflow.

The shared workflow loads its scripts and prompts from
`job.workflow_repository` at `job.workflow_sha`, so callers only need the
workflow above and always use support files from the same immutable revision.
It also listens for pull requests in this repository, so changes to the shared
CI implementation receive the same AI review directly.
