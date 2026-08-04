
Return exactly the object required by the provided JSON schema.

- `summary` is a concise Markdown overview without a Claude or Codex title.
  Do not duplicate the full inline findings in it.
- `comments` contains one entry for each confirmed finding, ordered by
  severity. Use an empty array when there are no findings.
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
