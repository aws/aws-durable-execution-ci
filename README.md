# AWS Durable Execution CI

Shared GitHub Actions workflows for AWS Durable Execution repositories.

## Shareable workflows

- [AI pull request review](docs/ai-pr-review.md): Runs independent Claude and Codex reviews through Amazon Bedrock.
- [AI issue implementation](docs/ai-issue-implementation.md): Uses Codex to implement issues requested with `/ai implement`.
- [AI PR review address](docs/ai-pr-review-address.md): Uses Codex to address pull request feedback requested with `/ai address`.
- [Slack notifications](docs/slack-notifications.md): Sends notifications for pull request, issue, discussion, and release events.
- [Issue triage](docs/issue-triage.md): Uses AI to classify new issues with existing repository labels.
- [Stale issue closer](docs/stale-issue-closer.md): Closes issues with a `needs-info` label after 14 days without a response. Clears the label if a response was posted within the 14 day window.

## Dependency updates

Dependabot checks the npm runtime dependencies and SHA-pinned GitHub Actions
each week. Codex CLI is pinned in `package.json` and `package-lock.json`; the
shared workflows install that locked version through
`scripts/install_codex_cli.sh`.

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more
information.

## License

This project is licensed under the Apache-2.0 License.
