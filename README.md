# AWS Durable Execution CI

Shared GitHub Actions workflows for AWS Durable Execution repositories.

## Developer experience

- [AI-assisted pull request lifecycle](docs/ai-developer-experience.md):
  Shows how developers create pull requests, request and interpret reviews,
  address feedback, and finish the review loop after all AI workflows are
  integrated.

## Shareable workflows

- [AI pull request review](docs/ai-pr-review.md): Runs independent Claude and Codex reviews through Amazon Bedrock.
- [AI issue implementation](docs/ai-issue-implementation.md): Uses Codex to implement issues requested with `/ai implement`.
- [AI PR review address](docs/ai-pr-review-address.md): Uses Codex to address pull request feedback requested with `/ai address`.
- [Slack notifications](docs/slack-notifications.md): Sends notifications for pull request, issue, discussion, and release events.
- [Issue triage](docs/issue-triage.md): Uses AI to classify new issues with existing repository labels.
- [Stale issue closer](docs/stale-issue-closer.md): Closes issues with a `needs-info` label after 14 days without a response. Clears the label if a response was posted within the 14 day window.

### Runner selection

Every shareable workflow accepts an optional `runs-on` input that sets the
runner label for all of its jobs. It defaults to `ubuntu-latest`. A caller that
hosts its own runners, for example through CodeBuild-hosted GitHub Actions
runners, passes the label in the `with` block:

```yaml
    uses: aws/aws-durable-execution-ci/.github/workflows/ai-pr-review.yml@<full-commit-sha>
    with:
      runs-on: codebuild-github-actions-runner-${{ github.run_id }}-${{ github.run_attempt }}
    secrets: inherit
```

Workflows that run model jobs (`ai-pr-review`, `ai-pr-review-address`,
`ai-issue-implementation`, `notify`, `issue-triage`) create an unprivileged
user with `sudo`, so the selected runner must provide root access and allow
the job to run for at least as long as the workflow's `timeout-minutes`.

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
