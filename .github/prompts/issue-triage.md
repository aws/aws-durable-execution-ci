Classify the newly opened GitHub issue using only the allowed repository labels
in the JSON appended as a stdin block.

Choose between one and five labels. Use the smallest set that accurately
describes the issue:

- Use `bug` for behavior that is incorrect relative to the documented or
  reasonably expected behavior.
- Use `enhancement` for a new capability or a change to intended behavior.
- Use `documentation` when the requested work primarily changes explanatory
  content.
- Use `question` when the issue is primarily a user enquiry seeking guidance,
  clarification, or support.
- Use `parity` when the issue identifies a behavior or capability disparity
  among language SDKs.
- Use `BREAKING` when the requested change may break backward compatibility or
  existing users.
- Use `needs-triage` when classification or resolution requires human
  intervention or maintainer judgment.
- Add component or area labels only when the issue clearly concerns that area.

Do not select status, resolution, ownership, difficulty, merge, or
project-management labels unless they are allowed and genuinely classify the
issue.
