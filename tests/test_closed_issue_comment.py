#!/usr/bin/env python3

import re
import unittest
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
WORKFLOW: str = (
    REPO_ROOT / ".github/workflows/closed-issue-comment.yml"
).read_text(encoding="utf-8")


def job_block(workflow: str, job_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job_name)}:\n(.*?)(?=^  [a-z0-9_-]+:\n|\Z)",
        workflow,
    )
    if match is None:
        raise AssertionError(f"workflow job {job_name!r} was not found")
    return match.group(1)


NOTIFY_JOB: str = job_block(WORKFLOW, "notify")

EXPECTED_CONDITIONS: tuple[str, ...] = (
    "github.event_name == 'issue_comment'",
    "github.event.action == 'created'",
    "github.event.issue.state == 'closed'",
    "!github.event.issue.pull_request",
    "github.event.comment.user.type != 'Bot'",
    "!contains(fromJSON('[\"OWNER\",\"MEMBER\",\"COLLABORATOR\"]'), "
    "github.event.comment.author_association)",
)


def job_condition(job: str) -> list[str]:
    match = re.search(r"(?ms)^    if: >-\n(.*?)(?=^    [a-z-]+:)", job)
    if match is None:
        raise AssertionError("job has no folded if: >- block")
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


class WorkflowContractTest(unittest.TestCase):
    def test_is_reusable(self) -> None:
        self.assertIn("workflow_call:", WORKFLOW)

    def test_slack_secret_is_required(self) -> None:
        self.assertRegex(
            WORKFLOW,
            r"SLACK_WEBHOOK_URL_ISSUE:\n\s+description: .*\n\s+required: true",
        )

    def test_filters_to_new_outside_comments_on_closed_issues(self) -> None:
        lines = job_condition(NOTIFY_JOB)
        expected = [f"{c} &&" for c in EXPECTED_CONDITIONS[:-1]]
        expected.append(EXPECTED_CONDITIONS[-1])
        self.assertEqual(lines, expected)

    def test_has_no_token_permissions(self) -> None:
        self.assertRegex(WORKFLOW, r"(?m)^permissions: \{\}$")
        self.assertRegex(NOTIFY_JOB, r"(?m)^    permissions: \{\}$")
        self.assertNotRegex(WORKFLOW, r":\s*write\b")

    def test_does_not_check_out_code(self) -> None:
        self.assertNotIn("actions/checkout", WORKFLOW)

    def test_payload_matches_issue_notification_keys(self) -> None:
        for key in ("action", "issue_url", "package_name", "summary"):
            self.assertIn(f'"{key}":', NOTIFY_JOB, key)
        self.assertIn("webhook-type: incoming-webhook", NOTIFY_JOB)

    def test_payload_values_are_json_encoded(self) -> None:
        expressions = re.findall(r'"\w+": \$\{\{ (.+?) \}\}', NOTIFY_JOB)
        self.assertEqual(len(expressions), 3)
        for expression in expressions:
            self.assertTrue(expression.startswith("toJSON("), expression)

    def test_summary_does_not_at_mention_author(self) -> None:
        self.assertNotIn("by @", NOTIFY_JOB)

    def test_actions_are_pinned_to_commit_sha(self) -> None:
        for reference in re.findall(r"uses: (\S+)", WORKFLOW):
            self.assertRegex(
                reference,
                r"@[0-9a-f]{40}$",
                f"{reference} must be pinned to a full commit SHA",
            )


if __name__ == "__main__":
    unittest.main()
