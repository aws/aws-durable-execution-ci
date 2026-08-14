Classify the newly opened GitHub issue using only the allowed repository labels
in the JSON appended as a stdin block.

Choose one or more labels. Combine labels only when they describe distinct,
compatible facets. Preserve mutually exclusive alternatives within a facet;
for example, do not select multiple priority or lifecycle states unless
repository-specific guidance explicitly says they may coexist. Multiple
package labels may be combined for cross-package work. Use the smallest
accurate set.

Package:

- Use `pkg:sdk` for the core durable-execution SDK or runtime package.
- Use `pkg:testing` for the testing package, test utilities, or local testing
  experience.
- Use `pkg:otel` for the OpenTelemetry package, instrumentation, or telemetry
  integration.

Issue kind:

- Use `bug` for behavior that is incorrect relative to the documented or
  reasonably expected behavior.
- Use `enhancement` for a new capability or a change to intended behavior.
- Use `question` when the issue is primarily a user enquiry seeking guidance,
  clarification, or support.

Area or topic:

- Use `documentation` when the requested work primarily changes explanatory
  content.
- Use `parity` when the issue identifies a behavior or capability disparity
  among language SDKs.

Compatibility:

- Use `BREAKING` when the requested change may break backward compatibility or
  existing users.

Priority:

- Use `urgent` only for a substantiated, time-sensitive issue with severe user,
  release, security, or production impact. Do not infer urgency from emphatic
  wording alone.

Lifecycle:

- Use `needs-triage` when routing, classification, or resolution requires human
  intervention or maintainer judgment.
- Use `needs-info` when specific missing information from the reporter blocks
  diagnosis or a useful next step.

Community:

- Use `good first issue` only for well-scoped, low-risk work suitable for a
  first contribution.
- Use `help wanted` only for well-scoped work suitable for external
  contribution.

Do not select status, resolution, ownership, difficulty, merge, or
project-management labels unless they are allowed and genuinely classify the
issue.
