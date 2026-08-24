# AWS Durable Execution CI

Shared GitHub Actions workflows for AWS Durable Execution repositories.

## Shareable workflows

- [AI pull request review](docs/ai-pr-review.md): Runs independent Claude and Codex reviews through Amazon Bedrock.
- [Codex issue implementation](docs/codex-issue-implementation.md): Implements issues requested with `/ai implement` and explicitly marked pull request review feedback.
- [Slack notifications](docs/slack-notifications.md): Sends notifications for pull request, issue, discussion, and release events.
- [Issue triage](docs/issue-triage.md): Uses AI to classify new issues with existing repository labels.
- [Stale issue closer](docs/stale-issue-closer.md): Closes issues with a `needs-info` label after 14 days without a response. Clears the label if a response was posted within the 14 day window.

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more
information.

## License

This project is licensed under the Apache-2.0 License.
