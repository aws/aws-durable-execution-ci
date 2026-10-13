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
user and drop into it with `sudo`. The selected runner must therefore:

- be ephemeral, with a fresh environment for every job: GitHub-hosted runners
  and CodeBuild-hosted runners both discard the container after one job. The
  workflows create the unprivileged user and its home directory
  unconditionally and never remove them, so a persistent self-hosted runner
  fails on its second job and would carry the model's writable state from one
  job to the next;
- run jobs as an account with passwordless `sudo` (the account's name does not
  matter: `runner` on GitHub-hosted runners and `root` on CodeBuild-hosted
  runners are both resolved at runtime with `id -un`);
- provide `adduser` and `setfacl`;
- allow the job to run for at least as long as the workflow's
  `timeout-minutes`.

A consuming repository that calls the same reusable workflow from more than one
caller must pass `runs-on` in each of them. `ai-pr-review-address.yml` is
called from both the intake/address caller and the `workflow_run`
continuation; see `docs/ai-pr-review-address.md`.

The model process is started with `sudo -u <user> -- env -i` and an explicit
allowlist of variables (its Bedrock credentials and the CLI's own settings), so
it never sees the job's environment, whatever the runner image's `sudo`
environment policy is. On CodeBuild that is what keeps the project's service
role credentials out of the model's reach.

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
