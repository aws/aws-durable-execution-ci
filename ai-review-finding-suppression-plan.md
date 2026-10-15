# Plan: suppress repeated AI review findings after an explicit maintainer verdict

Status: implemented locally. Consumer adoption and live smoke test remain pending.
Repository: `aws/aws-durable-execution-ci`, analysed at `main` = `d2a7344`.
Author: Harry (hln33) with Akisa. Original date: 2026-10-15.
All existing-code references below refer to that revision.

## 1. Problem

The AI PR review runs on every `synchronize` event. Each run can post the same
finding again. It does so even after the author rejected the finding with a
reason.

Example, `aws/aws-durable-execution-sdk-python#757`. One finding,
`arf_v1_qyavuzqaxgovssjakqmvosmm35`, was posted three times:

| Comment | Posted | Reviewed head | Human reply on thread |
| --- | --- | --- | --- |
| `r4226989239` | 2026-10-09 | `f2f73f6` | "Not changing this. It reverses a decision made in review." |
| `r4266100188` | 2026-10-14 | `bc89ab7` | "Will not fix - see r4265434039" |
| `r4277052448` | 2026-10-14 | `f401083` | — |

All three comments carry the same `finding_id`. So finding identity works for
this example. The pipeline recognised the repeat each time and posted it anyway.

The first version will enforce explicit maintainer verdicts. It will not infer
rejection from existing prose. A maintainer must record a verdict for those cases.

## 2. Root cause

The pipeline has a write path for maintainer decisions and no read path.

1. `scripts/record_ai_review_activity.py:428–545` authorizes and records
   `/ai verdict <outcome>` commands as `finding_verdict` events.
2. `scripts/load_ai_review_findings.py:33–88,100–109` builds the prior-findings
   catalog from `finding_observed` records only. It never reads verdicts.
3. `scripts/prepare_ai_review_comments.py:339–369` validates or computes finding
   identity. It appends every valid comment to the publish list at
   `scripts/prepare_ai_review_comments.py:480–509`.
4. `scripts/record_ai_review_activity.py:234–274` records prose replies as
   `human_inline_comment` metadata without the body. They carry no verdict.
5. `scripts/post_ai_review_summary.sh:232–243` minimizes snapshotted earlier
   inline comments. So the thread containing the author's reason can be collapsed
   while another copy of the finding appears.

The published finding body has no verdict instructions
(`scripts/prepare_ai_review_comments.py:470–478`). So the existing command is
also hard to discover from an inline review.

## 3. Design

### 3.1 Explicit verdicts only

Read `finding_verdict` events from `events/pr-{n}/finding_verdict/` on the
existing `ai-review-telemetry-v1` branch. For each finding, use the newest valid
command by `command_created_at`. Compare parsed timestamps. Break equal-time
ties by taking the lexicographically greatest `record_id`.

Keep existing command authorization. The writer requires current write,
maintain, or admin permission and validates the target finding
(`scripts/record_ai_review_activity.py:29–31,428–495`).

| Latest verdict | Inline publication if the model confirms the finding again |
| --- | --- |
| None (`open`) | Post |
| `accepted` | Post |
| `already-fixed` | Post |
| `deferred` | Suppress |
| `false-positive` | Suppress |
| `out-of-scope` | Suppress |

A fixed defect can regress. So `already-fixed` and thread resolution do not
suppress a later observation. A maintainer who wants continued suppression can
use `deferred`. A new `accepted` verdict reverses an earlier suppressing verdict.
Commands must be new comments; editing or deleting a command does not revoke its
recorded event. Keep that existing created-event contract
(`scripts/record_ai_review_activity.py:330–348`).

### 3.2 Separate model context from enforcement

Keep the 100-entry prior-findings catalog for model context. Add the latest
verdict as its `status` field. The current catalog limit is applied after sorting
observations (`scripts/load_ai_review_findings.py:81–88`).

Build a separate decision map from all verdict records within the supported
store limit. Do not filter this map through the 100-entry catalog. The publisher
looks up the computed or validated finding ID in this map. So reusing an old key
still enforces its verdict after its catalog entry expires.

Load the map again in the publication job. Generation-time status is context,
not publication authority. A verdict recorded before this reload applies to
that publication. A later command applies on the next review; v1 does not make
verdict recording and comment publication atomic.

Retain explicit storage bounds. Read at most 2,000 verdict records per PR, as
well as the existing 2,000-observation bound. The store raises when a prefix
exceeds its limit; it does not return a truncated list
(`scripts/ai_review_telemetry.py:568–592`). API errors, invalid relevant verdict
records, and exceeded bounds must stop publication with a clear error. Do not
silently substitute an empty decision map. A missing telemetry branch or a
successful read with no verdicts produces an empty map.

### 3.3 Publisher-controlled suppression

The model still reports confirmed findings with stable identity. Do not tell it
to omit a finding solely because of its status. Trusted code applies the policy
after all existing validation.

For each returned finding, the publisher:

1. Validates the model's prior ID or computes the ID from its key.
2. Validates the path, range, body, suggestion, and duplicate constraints.
3. Looks up the latest verdict in the publication-time decision map.
4. Either posts the comment or places it in `suppressed_findings`.

A suppressed finding is listed under "Suppressed repeats" in the summary. After
successful publication, it is recorded as an observation with `suppressed: true`.
The summary and observation describe repeats returned by this run. They are not
an inventory of every historically suppressed finding. No returned repeat means
no suppression entry for that finding.

### 3.4 Trust boundary and limits

Trusted code controls the suppression policy and its verdict input. The model
cannot supply a verdict or a suppression flag. It still controls semantic
correlation through `finding_key` and `prior_finding_id`. Existing validation
checks catalog membership and identity consistency, not root-cause equivalence
(`scripts/prepare_ai_review_comments.py:339–369`).

A new key can bypass an old verdict. An incorrect prior-ID association can
suppress an unrelated finding. Keep stable-key instructions and strict prior-ID
validation. Do not claim semantic correlation is independent of the model.

Explicit suppressing verdicts remain effective across revisions until superseded.
So a maintainer must replace the verdict when its rationale no longer applies.
Model context remains bounded, and verdict enforcement remains subject to the
explicit storage limits in §3.2.

### 3.5 Out of scope

- No suppression based on resolving or reopening a GitHub thread. No new
  thread-state lookup, scratch file, or synthetic `resolved` status.
- No inference from prose replies or acknowledgment alone.
- No skipping re-review on `synchronize`.
- No changes to old-comment minimization. Earlier rationale remains reachable
  in its original thread, even if collapsed.
- No new persistent datastore, model output fields, or reviewer-specific policy.

Thread resolution has different authorization from a verdict. Command-triggered
reviews also support fork PRs (`docs/ai-pr-review.md:85–89`). So any future
resolution-based policy needs its own authorization and regression design.

## 4. Data shapes

These are implemented internal interfaces, not model output APIs.

### 4.1 Catalog entry in `prior-findings.json`

Keep existing fields and add one field:

```json
{
  "finding_id": "arf_v1_...",
  "finding_key": "path::symbol::invariant",
  "path": "packages/.../core.py",
  "body": "...",
  "reviewed_head_sha": "<40 hex>",
  "observed_at": "2026-10-14T22:40:16Z",
  "status": "deferred"
}
```

`status` is one of `open`, `accepted`, `deferred`, `false-positive`,
`out-of-scope`, or `already-fixed`.

### 4.2 Publisher-only `finding-decisions.json`

Write this scratch file in the publication job's temporary directory. It contains
one entry per finding with an explicit verdict, including non-suppressing
verdicts. It is not limited to findings in the prompt catalog.

```json
{
  "arf_v1_...": "deferred",
  "arf_v1_...another-id": "accepted"
}
```

IDs above are placeholders. Validate real finding IDs and the five explicit
outcomes. An absent ID means `open`. The trusted loader scopes events to the
repository, PR, and reviewer. The model never writes or supplies this file.

### 4.3 Prepared review

Always emit `telemetry.suppressed_findings`, including when empty. Each entry has
the existing prepared-finding fields plus `suppression_status`. That status must
be `deferred`, `false-positive`, or `out-of-scope`.

Keep `telemetry.findings[i]` aligned with `comments[i]`. The publisher currently
relies on this alignment (`scripts/post_ai_review.sh:181–191`). Keep observation
IDs based on original model positions, not positions after suppression
(`scripts/prepare_ai_review_comments.py:451–458`).

Schematic output:

```text
summary: model overview + "Suppressed repeats" section when needed
comments: inline payloads for postable findings only
telemetry:
  review: existing review metadata
  findings: prepared findings for posted comments only
  suppressed_findings: prepared findings with suppression_status
```

## 5. Implementation steps

Both reviewers call the shared context and publication scripts:
`.github/workflows/claude-review.yml:153,296` and
`.github/workflows/codex-review.yml:160,367`. Apply one implementation to both.

### Step 1: Load verdicts and produce both views

Extend the loader at `scripts/load_ai_review_findings.py:22–109`:

- Validate relevant verdict records, including scope, ID, outcome, command
  timestamp, and record ID. Ignore other reviewers' records.
- Reduce verdict history to the latest outcome per finding using §3.1.
- Annotate catalog entries with `status`; keep `MAX_PRIOR_FINDINGS = 100`.
- Add optional `--decisions-output PATH` to write the full decision map from
  the same verdict read. Never derive it from the truncated catalog.
- Make read, validation, and capacity failures explicit as specified in §3.2.

The generation call at `scripts/prepare_ai_review_context.sh:65–70` needs no
new argument. It receives the annotated catalog through the existing output.

### Step 2: Filter publication and build the summary

Extend `scripts/prepare_ai_review_comments.py:126–165,200–219,315–535`:

- Add required `--finding-decisions PATH` to the CLI and validate the file shape.
- Add `status` to catalog validation. Treat it as context only.
- Consult the decision map in both identity branches. This includes a fresh key
  that hashes to an ID absent from the catalog. Keep existing validation of an
  explicit `prior_finding_id`; the decision map does not authorize arbitrary IDs.
- Run all existing validation before separating posted and suppressed findings.
- Build each prepared-finding entry once, then append it to the appropriate list.
- Leave posted comments aligned with posted telemetry. Emit an empty suppressed
  list when there are no suppressed findings.

Summary rendering must preserve the 4,000-character limit currently checked at
`scripts/prepare_ai_review_comments.py:259–265`:

1. Build the complete suppression section in model order, with one bullet per
   suppressed finding: `` `arf_…` — deferred (maintainer verdict) ``.
2. Reserve space for that section when budgeting the model overview. At most
   20 findings are allowed (`scripts/prepare_ai_review_comments.py:23,267–273`),
   so the bounded ID/status bullets fit without dropping entries.
3. If necessary, shorten the overview and append a visible truncation notice.
   Append the suppression section after the overview. Include the notice and
   separators in the 4,000-character budget.
4. If nothing is suppressed, preserve the original summary behavior.

Append this footer to published inline comments:

> To suppress repeats, reply `/ai verdict deferred`, `/ai verdict false-positive`,
> or `/ai verdict out-of-scope` on the first nonblank line. Add rationale on later
> lines. A new `/ai verdict accepted` allows future repeats again.

### Step 3: Keep the model responsible for detection and correlation

Extend `.github/prompts/ai-pr-review-output.md:14–17` with:

> Each prior finding has a `status` from the latest maintainer verdict. Report
> confirmed findings even when their status suppresses inline publication. Reuse
> `prior_finding_id` only for the same root cause. The publisher applies the
> latest verdict and lists suppressed repeats in the summary. Do not invent a
> new identity to bypass a verdict. Do not repeat a historical finding unless
> the current code still supports it.

### Step 4: Refresh decisions at publication

Extend the loader and preparer calls at `scripts/post_ai_review.sh:150–174` to
write and pass `finding-decisions.json`. Keep it in the existing temporary
directory. Do not use model-provided or generation-time decisions for enforcement.

Keep revision checks, the zero-comment loop, summary publication, and cleanup.
No new GraphQL query is required.

### Step 5: Record suppressed observations only on successful publication

Extend prepared telemetry validation at
`scripts/publish_ai_review_telemetry.py:49–136` to require and validate the third
list. Keep published-comment matching restricted to posted findings
(`scripts/publish_ai_review_telemetry.py:250–291`).

Use the existing lifecycle, with these additions:

| Phase | Suppressed-finding handling |
| --- | --- |
| Plan | Add a separate suppressed manifest with status and content digests. Persist identities as needed. Do not write observation bodies or `finding_observed` records. |
| Publish | Add a separate suppressed manifest to the published review. Write one `finding_observed` per suppressed finding with `suppressed: true`, `suppression_status`, and an empty `comment_node_id`. Use the summary creation timestamp for `recorded_at` and `published_at`. |
| Fail | Preserve the failure record and partial posted-comment mapping. Do not write suppressed observations. The planned manifest already records the intended suppression. |

Existing lifecycle locations are
`scripts/publish_ai_review_telemetry.py:207–247,294–386,389–433`.
The summary timestamp exists only after publication
(`scripts/post_ai_review.sh:176–178,218–236`). The store rejects differing bytes
at an existing record path (`scripts/ai_review_telemetry.py:468–479`). So only
successful publication writes suppressed observations. Repeating that telemetry
write with the same inputs must be idempotent.

Retain observation identity, reviewed revisions, path, range, and body metadata.
Do not create suggestion-adoption artifacts for suppressed findings. Their
observation's published `suggestion_id` is empty because no suggestion was
published. Keep posted suggestion handling unchanged.

### Step 6: Accept verdicts with trailing rationale

Change command parsing at `scripts/record_ai_review_activity.py:326–349` to
match the first nonblank line. Require that line to contain only a valid command.
Ignore later lines for parsing and telemetry. Apply this to both inline replies
and conversation-level commands with an explicit finding ID.

Preserve created-event, non-bot, permission, and target checks. Rationale before
the command and same-line trailing prose remain invalid. Do not store rationale
bodies in telemetry.

### Step 7: Document the policy

Extend `docs/ai-pr-review.md:105–155` with the six-state policy table, reversal
instructions, command-with-rationale examples, and the catalog/enforcement split.
Explain that resolving a thread has no suppression effect. Explain why
`already-fixed` can be reported again. Document summary scope, publication-time
refresh, capacity failures, and the semantic-identity limitation.

## 6. Tests

The existing unit and shell suites cover the cases below.

- **Loader:** Newest verdict wins; equal timestamps have deterministic ordering;
  other reviewers are ignored; no verdict means `open`; every catalog entry has
  status; malformed relevant verdicts fail; API/capacity failures do not become an
  empty map. With 101 distinct findings, the oldest verdict remains in the map
  after its catalog entry expires. A missing branch produces valid empty outputs.
- **Preparation:** Cover all six statuses and both identity branches. Suppress a
  repeated key absent from the catalog but present in the decision map. A stale
  catalog status cannot override the map. A newer `accepted` verdict restores
  posting. Keep posted telemetry aligned when a suppressed finding is between
  two posted ones. Keep observation IDs stable under filtering.
- **Validation:** Reject invalid decision maps and missing/invalid catalog status.
  Suppressed findings with invalid paths, ranges, bodies, suggestions, or duplicate
  identities still fail. Do not relax untrusted prior-ID rejection.
- **Summary/footer:** Cover no findings, all suppressed, and mixed output. Include
  a 4,000-character model summary and the maximum 20 suppression bullets. Verify
  the final bound, complete bullets, truncation notice, and command footer.
- **Publisher shell integration:** For both reviewers, supply persisted verdict
  and observation fixtures through the `gh` stub. A suppressed repeat produces
  zero inline POSTs and one summary POST. An `accepted` or `already-fixed` repeat
  posts normally. Test a verdict recorded after generation but before publication,
  including a reversal. Test a missing or unreadable decision file and preserve
  revision-change and partial-publication failure checks.
- **Telemetry lifecycle:** Require `suppressed_findings`; verify empty comment IDs,
  suppression status, and summary timestamps. Plan → publish writes each suppressed
  observation once. Plan → failure writes none. Use a store fixture that enforces
  immutable paths, not only a plain mock. Repeat the publish telemetry write with
  identical inputs and verify idempotency. Keep posted mapping validation strict.
  Verify suppressed suggestions do not create adoption artifacts.
- **Prompt contract:** Require status context, stable correlation, and
  publisher-controlled suppression. Do not require the model to omit repeats.
- **Verdict parsing:** Cover both command forms, blank leading lines, later-line
  rationale, invalid first lines, unknown outcomes, bots, and unauthorized users.
  Verify a new verdict can supersede one after a suppressed observation.
- **Cleanup regression:** Run the existing inline cleanup suite unchanged.

Existing test locations to extend:
`tests/test_load_ai_review_findings.py:39–94`,
`tests/test_prepare_ai_review_comments.py:65–236`,
`tests/test_publish_ai_review_telemetry.py:69–178`,
`tests/test_post_ai_review.sh:14–249`, and
`tests/test_ai_pr_review_workflow.py:430–450`.
Extend the verdict parser tests at
`tests/test_record_ai_review_activity.py:88–115` and add the authorization and
supersession cases described above.

## 7. Verification

Run these commands from the repository root after implementation:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
bash tests/test_post_ai_review.sh
bash tests/test_ai_review_inline_cleanup.sh
shellcheck scripts/post_ai_review.sh scripts/prepare_ai_review_context.sh
```

At the analysed revision, no GitHub Actions workflow runs this test suite.
Local results are required before rollout. A passing current baseline does not
validate the proposed behavior.

Smoke-test one consuming repository before updating all pins:

1. Review a test PR with a known finding. Record `/ai verdict deferred` and wait
   for the workflow's acknowledgment before pushing another revision.
2. Keep the defect present and review again. If the model returns the repeat,
   verify zero inline copies, one summary entry, and one suppressed observation.
3. If the model omits the finding, that run does not exercise suppression. Use the
   deterministic publisher fixtures for coverage; do not count omission as proof.
4. Post a new `/ai verdict accepted`, wait for acknowledgment, and review again.
   A returned repeat must be posted. Test `already-fixed` as postable too.
5. Confirm thread resolution alone does not change the publication decision.

## 8. Rollout

1. Land the verdict-only implementation, parser improvement, docs, and tests in
   one PR. Leave thread-state lookup and minimization changes out.
2. Consuming repositories pin reusable workflows by SHA
   (`docs/ai-pr-review.md:48–60`). Update one consumer and complete the smoke test.
3. Then update remaining consumers: the Python, JS, Java, Go, and Rust SDKs and
   the conformance-test repository. Confirm this repository's direct workflow run
   uses the merged revision; it also runs reviews directly
   (`docs/ai-pr-review.md:282–286`).
4. Existing explicit verdicts become effective after adoption. No verdict backfill
   is needed. Earlier prose-only rejections still require a command.

## 9. Preparing #757 for adoption

Before this ships, reply with exactly `/ai verdict deferred` on `r4277052448`.
Wait for acknowledgment. This records the decision now; it does not stop today's
publisher from repeating the finding. Suppression starts after the consuming
repository adopts this implementation. Resolving the thread is not a substitute.

## 10. Changes from the initial proposal

- **Removed:** Thread-resolution suppression, its GraphQL reader and scratch file,
  its synthetic status, and its tests. `already-fixed` is now postable.
- **Deferred:** Changes to old-comment minimization.
- **Added:** A publisher-only decision map independent of the prompt catalog,
  publication-time refresh tests, reversal/capacity tests, and immutable-store
  lifecycle tests.
- **Corrected:** The model reports confirmed repeats; the publisher suppresses
  them. Suppressed observations are written only during successful publication.
  Summary rendering reserves space before appending suppression entries.
- **Unchanged:** Stable IDs, latest explicit verdict precedence, shared trusted
  publication, validation, separate posted/suppressed lists, summary visibility,
  command discoverability, later-line rationale, and SHA-pinned consumer rollout.

## 11. Local implementation verification

The repository changes are implemented. The scope is this repository.
Consumer pin updates, the #757 verdict command, and live smoke testing remain
follow-up work.

- The 87 review-related Python tests pass.
- `bash tests/test_post_ai_review.sh` passes for both reviewers, including
  suppression, posting, publication-time verdict changes, reversal, invalid
  decision files, API failure, revision changes, and partial-publication cleanup.
- `bash tests/test_ai_review_inline_cleanup.sh` passes unchanged.
- Full Python discovery runs 330 tests. Outside the sandbox, it reports the
  three pre-existing Claude isolation failure cases under Bash 3.2.
  Each fails because `model_env[@]` is treated as unbound in
  `scripts/run_claude_isolated.sh`. That script is unchanged.
- The sandbox also prevents the existing credential-server test from binding.
  All seven credential-server tests pass outside the sandbox.
- Shell syntax checks and `git diff --check` pass.
- ShellCheck is unavailable on this machine, so its check remains pending.
