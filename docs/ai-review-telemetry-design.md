# Durable AI review telemetry design

Status: proposed. This document describes the design only; it does not change
the current workflows.

## Context

The AI pull request review workflow currently validates findings against an
exact base and head revision, posts each finding as an inline comment, and
includes the reviewed head SHA in its summary. Its comment markers identify a
workflow run and attempt, while its structured review artifacts are retained
for one day.

Those records are sufficient for publication and cleanup, but not for durable
measurement:

- A rerun creates a new run-scoped marker even when it reports the same
  underlying problem.
- A comment does not have a structured maintainer disposition.
- Historical GitHub objects do not form an immutable event log for pull
  request revisions.
- Computing review latency and workload requires reconstructing state from
  comments and review prose.

## Goals

The implementation of this design will:

1. Give each logical AI finding a stable identifier and each appearance of
   that finding a separate occurrence identifier.
2. Attach the exact reviewed base and head SHA to every structured review
   result and published finding.
3. Track a finding as repeated, resolved, or newly discovered across review
   runs and pull request revisions.
4. Let an authorized maintainer record `accepted`, `deferred`,
   `false-positive`, `out-of-scope`, or `already-fixed` on a finding.
5. Retain pull request revision, AI review, human review, finding, verdict, and
   suggestion events in a versioned machine-readable dataset.
6. Support the requested quality, latency, and workload metrics without
   interpreting comment or review prose.
7. Keep model credentials, telemetry credentials, and GitHub write
   credentials in separate jobs.

## Non-goals

- Telemetry will not contain prompts, diffs, file contents, suggestion text,
  comment bodies, review bodies, commit messages, credentials, or model
  transcripts.
- Finding identifiers are correlation keys, not proof that a finding is
  correct.
- The first version will not correlate equivalent findings produced by
  different AI reviewers. A Claude finding and a Codex finding remain
  distinct even if they describe the same defect.
- The telemetry pipeline will not edit a pull request branch, apply a
  suggestion, or decide a maintainer verdict.
- This repository will not host a multi-tenant telemetry service. Each caller
  owns its export destination and retention policy.

## Terms

- **Review run**: one reviewer evaluating one base/head pair.
- **Finding**: a logical defect reported by one AI reviewer. It keeps one
  `finding_id` across reruns and revisions.
- **Occurrence**: one publication of a finding in one review run. It has a
  unique `occurrence_id`.
- **Revision**: an `opened` or `synchronize` event and its base and head SHAs.
- **Resolution**: a later successful review explicitly determines that a
  previously open finding is no longer present.
- **Verdict**: a maintainer's disposition of a finding. Verdict and resolution
  are independent; for example, an accepted finding remains unresolved until
  a later revision fixes it.

## Design overview

The reusable workflow will have four telemetry-producing paths:

1. A revision recorder runs before review fan-out for every `opened` and
   `synchronize` event. It persists the event action, the timestamp captured in
   the event payload, pull request number, base SHA, previous head SHA when
   present, and current head SHA.
2. The trusted review preparation step builds a catalog of prior AI findings
   and gives the model their identifiers as data. After generation, a trusted
   validator assigns or reuses finding identifiers and adds the trusted review
   envelope containing the base and head SHA.
3. The GitHub publication job emits sanitized response receipts after GitHub
   accepts comments. A separate telemetry finalizer turns those receipts into
   structured review and finding events.
4. A lightweight event collector records maintainer verdicts and human review
   activity without starting an AI review.

All paths emit schema-validated events to a caller-owned Amazon S3 bucket.
Each event is one immutable JSON object. Event consumers can read the objects
directly as an export or compact them into JSON Lines, Parquet, or a query
table without reading GitHub comment bodies.

Telemetry is enabled only when both its destination and its dedicated IAM role
are configured. Once enabled for a repository, automatic AI publication fails
closed if the revision or publication event cannot be persisted. This avoids
silently creating new measurement gaps.

## Stable finding identity

### Identifier levels

Three identifiers separate a logical finding from its executions:

| Identifier | Scope | Stability |
| --- | --- | --- |
| `review_id` | Reviewer, PR, base/head pair, workflow run and attempt | One review execution |
| `finding_id` | Repository, PR, reviewer, and logical defect | Reused across reruns and revisions |
| `occurrence_id` | Finding and review execution | One published appearance |

Identifiers use a versioned, URL-safe form such as `aif_v1_<digest>`. The
trusted validator derives digests with SHA-256 from length-delimited fields,
then truncates them to 128 bits. Repository database ID and pull request node
ID are used instead of mutable repository names. The full hash inputs and
algorithm will be covered by test vectors so implementations cannot produce
different identifiers through ambiguous concatenation.

The initial `finding_id` is derived from:

- the identifier scheme version;
- repository database ID;
- pull request node ID;
- AI reviewer (`claude` or `codex`); and
- a normalized semantic key from the structured model result.

The semantic key describes the affected component and violated invariant, not
the current line number or the wording of the comment. It is a bounded,
lowercase token validated by the trusted wrapper. Path and line remain finding
attributes but are not the identity by themselves.

An `occurrence_id` is derived from the `finding_id` and `review_id`. Repeating
the publication step within one workflow attempt is therefore idempotent,
while a later workflow attempt or review run produces a new occurrence tied
to the same finding.

### Matching later reviews

Before invoking a reviewer, the trusted context preparation step lists prior
AI comments for the same pull request, including minimized comments, and
strictly parses only metadata produced by this workflow. The model receives a
bounded prior-finding catalog with:

- `finding_id` and semantic key;
- reviewer;
- last reviewed head SHA;
- last path and line range;
- lifecycle state; and
- the already-published finding text as untrusted matching context.

The prior text is not copied into telemetry. It is supplied to the model only
because the text is already published on the pull request and helps distinguish
semantic matches.

For every new result, the model must either reference an existing
`finding_id` or provide a new semantic key. The trusted validator verifies that
a referenced identifier belongs to the same repository, pull request, and
reviewer. It also rejects duplicate identifiers within one result.

This produces the required distinctions:

- The same defect reported again references the old `finding_id` and creates a
  new `occurrence_id`.
- A prior finding assessed as absent on a later head produces a resolution
  event without a new occurrence.
- A different defect at the same path and line uses a different semantic key
  and therefore a new `finding_id`.

Model-based semantic matching can be uncertain. The result schema therefore
requires an assessment of every previously open finding as `repeated`,
`resolved`, or `unverified`. `unverified` leaves the finding open and avoids
claiming a resolution from mere absence. A resolution is accepted only from a
successful review of a different head SHA.

### Review result and comment metadata

The model does not set trusted revision metadata. After model output
validation, the wrapper creates a review artifact with this shape:

```json
{
  "schema_version": 1,
  "review": {
    "review_id": "air_v1_...",
    "reviewer": "codex",
    "base_sha": "40 hexadecimal characters",
    "head_sha": "40 hexadecimal characters"
  },
  "summary": "model-produced summary",
  "findings": [
    {
      "finding_id": "aif_v1_...",
      "occurrence_id": "afo_v1_...",
      "path": "scripts/example.py",
      "start_line": 10,
      "line": 12,
      "severity": "high",
      "category": "correctness",
      "has_suggestion": true,
      "body": "model-produced finding",
      "suggestion": "model-produced replacement"
    }
  ],
  "prior_finding_assessments": []
}
```

The short-lived artifact still contains the body and suggestion needed for
publication. The durable export omits both fields.

Each published inline comment starts with a hidden, strictly formatted marker
containing the schema version, `review_id`, `finding_id`, `occurrence_id`, and
reviewed head SHA. The visible comment includes a short finding ID so a
maintainer can quote it outside the review thread. The summary comment carries
the `review_id`, base SHA, head SHA, and the occurrence IDs it owns. Existing
markers remain readable during migration, but only versioned finding markers
participate in identity matching.

## Structured maintainer verdicts

An authorized maintainer records a verdict by replying to an AI inline
comment with exactly:

```text
/ai verdict accepted
```

The final token can instead be `deferred`, `false-positive`, `out-of-scope`,
or `already-fixed`. Requiring a reply means the maintainer does not have to
copy an identifier, and the root comment provides the finding and reviewed
revision.

The caller adds `pull_request_review_comment: [created]`. A resolver:

1. Requires an exact command and rejects bots.
2. Checks the actor's current `write`, `maintain`, or `admin` permission.
3. Resolves the root of the reply thread.
4. Requires a valid finding marker on a root comment owned by the AI review
   workflow and verifies its repository and pull request.
5. Emits a `finding_verdict_recorded` event with the finding ID, verdict,
   actor login and database ID, pull request number, AI reviewer, reviewed
   head SHA, command comment ID, and command timestamp.

The command body is only an input mechanism. Metric consumers read the
structured verdict event and never parse that body.

Verdicts form an append-only history. If a maintainer records another verdict
for the same finding, both events remain in the export and the latest event by
GitHub timestamp and comment database ID is current. Deleting or editing the
command does not rewrite history. A future explicit `superseded` event can be
added if verdict retraction is required.

Verdict processing is independent from AI review concurrency and never starts
a model job.

## Revision and human-review capture

### Revision events

The revision recorder is outside reviewer-specific concurrency so a newer run
cannot cancel the capture of an older `synchronize` event. It records:

- `opened` or `synchronize`;
- the timestamp from the original event payload and the UTC ingestion
  timestamp;
- repository ID and pull request number/node ID;
- base SHA;
- prior head SHA (`before`) for `synchronize`;
- reviewed-candidate head SHA (`after`);
- GitHub workflow run ID and attempt; and
- a deterministic event ID.

For `opened`, the source timestamp is the pull request `created_at`. For
`synchronize`, it is the pull request `updated_at` value captured in that
event's immutable Actions payload. The record includes `timestamp_source` so
analyses do not accidentally substitute a later API observation. The workflow
also records the event immediately, before fetching current PR state; if the
head has already advanced again, the original revision remains in the export
even though its review is skipped.

The collector fetches commit metadata for the `before..after` range and emits
`pull_request_commit_observed` events containing SHA, parent SHAs, and
committed timestamp. It does not export commit messages, author email
addresses, or patches.

### Human review events

The caller also forwards:

- `pull_request_review: [submitted, dismissed]`; and
- `pull_request_review_comment: [created, edited, deleted]`.

For non-bot actors, the collector emits `human_review_submitted`,
`human_review_dismissed`, and `human_inline_comment_*` events. These records
contain object IDs, actor login and database ID, timestamps, review state,
thread/reply relationships, path and line metadata already exposed by GitHub,
and the PR head SHA associated with the event. They contain no comment or
review body.

A verdict command is recorded as a verdict rather than human feedback, so it
does not inflate inline-comment workload. AI comments are recognized by their
validated markers rather than by the generic `github-actions` login alone.

## Durable event export

### Storage

The canonical store is a caller-owned, private S3 bucket with versioning,
default encryption, public access blocking, and a retention policy selected by
the caller. Object Lock can be enabled when immutable audit retention is
required. Actions artifacts remain a short-lived job handoff and are not the
telemetry system of record.

The reusable workflow accepts a bucket and prefix and a dedicated
`AI_REVIEW_TELEMETRY_ROLE_ARN`. The role trust policy restricts assumption to
the caller repository and workflow. Its session policy permits only
`s3:PutObject` beneath that repository's prefix, plus the encryption action
required by the bucket when applicable. It cannot read repository contents,
invoke a model, list unrelated objects, or mutate GitHub.

Objects use partition-friendly keys:

```text
<prefix>/schema=v1/repository_id=<id>/pr=<number>/date=<yyyy-mm-dd>/<event_id>.json
```

Writers use a conditional create. A duplicate event ID is success only when it
represents a GitHub rerun/redelivery of the same source event. Event IDs are
derived from immutable source coordinates, such as a GitHub object database
ID or the repository, pull request, action, `before`, and `after` tuple.

Each object is a complete event, not a mutable per-PR summary. A consumer can
list the prefix as the durable JSON export. An optional separately authorized
compaction workflow may create JSON Lines or Parquet snapshots, but compacted
files are derived data and never replace source events.

### Common event envelope

Every event has these fields:

```json
{
  "schema_version": 1,
  "event_id": "aie_v1_...",
  "event_type": "finding_published",
  "occurred_at": "2026-08-24T12:34:56.000Z",
  "recorded_at": "2026-08-24T12:35:02.000Z",
  "repository": {
    "id": 123,
    "full_name": "owner/repository"
  },
  "pull_request": {
    "number": 42,
    "node_id": "PR_..."
  },
  "base_sha": "40 hexadecimal characters",
  "head_sha": "40 hexadecimal characters",
  "source": {
    "workflow_run_id": 1234,
    "workflow_run_attempt": 1
  },
  "payload": {}
}
```

Event-specific JSON Schemas use `additionalProperties: false`, bounded
strings, enumerated states, RFC 3339 UTC timestamps, and 40-character SHA
validation. The implementation repository will version and test those schemas.
New optional event fields require a schema version that old consumers can
reject or explicitly support.

### Event types

| Event | Important payload fields |
| --- | --- |
| `pull_request_revision` | action, source timestamp, timestamp source, before/base/head SHA |
| `pull_request_commit_observed` | commit SHA, parent SHAs, committed timestamp |
| `ai_review_published` | review ID, reviewer/model, reasoning effort, base/head SHA, first-feedback/completion timestamps, finding count |
| `finding_published` | finding/occurrence/review IDs, reviewer, severity, category, path/lines, comment ID, suggestion offered |
| `finding_repeated` | finding ID, previous and current occurrence IDs and head SHAs |
| `finding_resolved` | finding ID, last occurrence, resolved head SHA, corrective commit SHA, detection method |
| `finding_verdict_recorded` | finding ID, verdict, actor, reviewed head SHA, command comment ID |
| `suggestion_applied` | finding/occurrence IDs, suggestion digest, applying commit SHA and timestamp, detection method |
| `human_review_submitted` | review ID, actor, state, submitted timestamp, associated head SHA |
| `human_review_dismissed` | review ID, actor, dismissed timestamp |
| `human_inline_comment_created` | comment/thread/reply IDs, actor, timestamp, associated head SHA, path/line |
| `human_inline_comment_edited` | comment ID, actor, timestamp |
| `human_inline_comment_deleted` | comment ID, actor, timestamp |

The model name and reasoning effort are useful experimental dimensions and are
safe configuration metadata. The export does not contain the model input or
output prose.

## Finding resolution and applied suggestions

A successful later review explicitly assesses prior open findings. When it
marks one resolved, the trusted wrapper emits `finding_resolved` and uses the
reviewed head SHA as the corrective commit SHA. This defines the general
metric as the first successfully reviewed commit where the defect is confirmed
absent, rather than guessing from an intervening push.

For exact GitHub suggestions, the synchronize collector can provide a more
precise result. At publication it stores a digest of the selected old range
and replacement, but not their text. On a later synchronize run, trusted code
reads the already-published suggestion and relevant Git blobs, treats both as
data, and checks each introduced commit tree for that exact replacement. It
emits `suggestion_applied` for the earliest unambiguous match. Renames are
followed through GitHub's compare metadata. Ambiguous or partially rewritten
changes produce no applied event; they may still receive a later
reviewer-assessed resolution.

The `detection_method` field distinguishes `exact_suggestion` from
`reviewer_assessment`, allowing analyses to choose the required confidence.

## Metric derivation

No metric below requires natural-language parsing:

| Metric | Structured derivation |
| --- | --- |
| Time to first AI feedback | First `pull_request_revision(opened)` to `first_feedback_at` in the first `ai_review_published` |
| Time to first review from AI or human | Opened timestamp to the earliest AI publication, human review submission, or human root inline comment |
| Time after each synchronize | Each revision event to the next AI publication or human review event associated with that or a later head |
| Time from finding to corrective commit | First `finding_published` to the committed timestamp referenced by the first `finding_resolved` |
| Applied suggestion count | Count distinct `suggestion_applied` occurrence IDs |
| Applicable-finding precision | Join the latest verdict per finding; report accepted, deferred, and already-fixed findings separately from false-positive and out-of-scope findings so the analysis can state its denominator |
| Findings repeated across reruns | Finding IDs with occurrences in more than one review ID; group further by equal or different head SHA |
| Human inline comments | Count human root/reply comment creation events, excluding verdict commands |
| Human feedback rounds | Group human feedback events into revision windows bounded by `opened`/`synchronize` events |
| First human review to approval | First human review or root inline comment to the first later `human_review_submitted` with state `approved` |

Every latency calculation uses `occurred_at`; `recorded_at` measures ingestion
lag and supports pipeline monitoring.

## Publication consistency and recovery

GitHub and S3 cannot participate in one transaction. Publication therefore
uses an idempotent sequence:

1. A trusted validator assigns IDs and produces a sanitized publication intent.
2. A telemetry-only job persists that intent.
3. A GitHub-only job posts inline comments and uploads receipts containing
   their IDs and timestamps, but no bodies.
4. A telemetry-only finalizer persists `finding_published` from the receipts.
5. A GitHub-only job posts the summary after every finding event is durable
   and emits its receipt.
6. A final telemetry-only job persists `ai_review_published`, including the
   earliest inline or summary timestamp as `first_feedback_at`.

If a finalizer fails after GitHub publication, a compensation job with only
GitHub write permission minimizes the comments listed in the receipts. A
scheduled reconciliation path compares strictly validated comment markers
with event IDs and backfills a missing publication event or minimizes an
orphan when compensation was interrupted. Publication intents alone are not
counted as findings.

Revision and human-event records are independently idempotent. Rerunning the
original Actions run retains its event payload and retries the same object key.
A scheduled audit reports gaps it cannot reconstruct exactly instead of
inventing timestamps.

## Security and privacy

- Event payloads, pull request text, prior finding text, and model output
  remain untrusted data. They never control an S3 key, IAM role, command, or
  schema selection without validation.
- Repository and pull request numeric IDs form storage partitions; names and
  paths are validated data fields, not path fragments.
- The model-generation job retains only GitHub read access and the Bedrock
  role. It never receives the telemetry role.
- The telemetry writer receives an OIDC session restricted to one S3 prefix
  and has no model or GitHub write credentials.
- The comment-publication job retains GitHub write access and receives no AWS
  credentials.
- Verdict authorization is checked against current collaborator permission,
  not `author_association` alone.
- Actor login and database ID are retained because verdict attribution and
  human workload require them. Comment bodies, commit authors, email
  addresses, and prose are excluded.
- The writer logs event IDs and object keys only. It does not print event
  payloads, prompts, credentials, or model results.
- Bucket encryption, access logging, lifecycle, retention, and deletion remain
  under the caller's administrative control.

## Compatibility and rollout

Implementation should be delivered in independently reviewable stages:

1. Add versioned event schemas, deterministic ID test vectors, and an
   offline-tested S3 writer.
2. Enrich review artifacts and comments with review, finding, occurrence, base,
   and head identifiers while continuing to recognize legacy markers.
3. Capture and export revision, review, finding, and resolution events.
4. Add verdict command handling and human-review event capture.
5. Add exact suggestion-application detection and reconciliation.

Telemetry configuration is initially opt-in so existing callers do not fail
after upgrading the reusable workflow. The workflow validates that the role
and destination are either both present or both absent. Stable identifiers can
be published before export is enabled, but a repository is not considered
measurement-complete until durable telemetry is configured.

Tests must cover identifier stability, a new issue at a reused line, reruns on
the same head, resolution on a later head, duplicate event delivery, invalid or
unauthorized verdicts, edited/deleted human comments, publication rollback,
legacy marker coexistence, schema rejection, and confirmation that exported
fixtures contain none of the prohibited content fields.

## Acceptance mapping

| Requirement | Design element |
| --- | --- |
| Stable machine-readable finding identifier | Versioned `finding_id` in validated artifacts, inline markers, and events |
| Exact reviewed revisions | Trusted review envelope and summary/finding metadata carry base and head SHA |
| Correlate repeats and resolutions | Finding catalog, occurrence IDs, and explicit prior-finding assessments |
| Structured maintainer verdicts | Authorized reply command and enumerated verdict event |
| Durable machine-readable metadata | Immutable, schema-versioned JSON objects in caller-owned S3 |
| Retain opened/synchronize timestamps | Uncancelled revision recorder preserves the original event payload timestamp |
| Support requested metrics without prose | Event types and derivations defined above |
| Do not expose sensitive or excess content | Allowlisted metadata schemas, separate credentials, and prohibited-field tests |
