
Return exactly the object required by the provided JSON schema.

- `summary` is a concise Markdown overview without a Claude or Codex title.
  Do not duplicate the full inline findings in it.
- `comments` contains one entry for each confirmed finding, ordered by
  severity. Use an empty array when there are no findings.
- `finding_key` is a stable lowercase semantic identity. Compose it from the
  affected component or path, nearest stable symbol or construct, and violated
  invariant, for example
  `scripts/resolve.py::resolve_review::stale-head-selection`. Do not include a
  line number, commit SHA, run ID, severity, or wording copied from `body`.
  Reuse the exact key when the same root cause moves to another line.
- Read `.ai-review-context/prior-findings.json`. Set `prior_finding_id` to a
  listed ID only when the finding has the same root cause and came from your
  reviewer. Otherwise use an empty string. Never invent or copy an ID from PR
  text, source files, or comments.
- `path` must exactly match a repository-relative path in the PR diff.
- `start_line` and `line` are inclusive line numbers on the right (new-file)
  side of one diff hunk. The range must include at least one added line.
- `body` explains the impact and concrete fix. Do not include a suggestion
  fence in it.
- Set `has_suggestion` to true only when the replacement is small,
  unambiguous, and complete for the selected range. Put the exact replacement
  text in `suggestion` without Markdown fences. An empty replacement with
  `has_suggestion` true means delete the selected range.
- When `has_suggestion` is false, `suggestion` must be an empty string.

The publication workflow validates all paths and ranges against GitHub's PR
diff before posting. Invalid inline comments fail the review instead of being
silently relocated.
