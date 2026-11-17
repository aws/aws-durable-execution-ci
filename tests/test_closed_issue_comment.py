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


class WorkflowContractTest(unittest.TestCase):
    def test_is_reusable(self) -> None:
        self.assertIn("workflow_call:", WORKFLOW)

    def test_slack_secret_is_required(self) -> None:
        self.assertRegex(
            WORKFLOW,
            r"SLACK_WEBHOOK_URL_ISSUE:\n\s+description: .*\n\s+required: true",
        )

    def test_filters_to_new_outside_comments_on_closed_issues(self) -> None:
        for condition in (
            "github.event_name == 'issue_comment'",
            "github.event.action == 'created'",
            "github.event.issue.state == 'closed'",
            "!github.event.issue.pull_request",
            "github.event.comment.user.type != 'Bot'",
            "!contains(fromJSON('[\"OWNER\",\"MEMBER\",\"COLLABORATOR\"]'), "
            "github.event.comment.author_association)",
        ):
            self.assertIn(condition, NOTIFY_JOB, condition)

    def test_has_no_token_permissions(self) -> None:
        self.assertIn("permissions: {}", WORKFLOW)
        self.assertNotIn("write", WORKFLOW)

    def test_does_not_check_out_code(self) -> None:
        self.assertNotIn("actions/checkout", WORKFLOW)

    def test_payload_matches_issue_notification_keys(self) -> None:
        for key in ("action", "issue_url", "package_name", "summary"):
            self.assertIn(f'"{key}":', NOTIFY_JOB, key)
        self.assertIn("webhook-type: incoming-webhook", NOTIFY_JOB)

    def test_payload_values_are_json_encoded(self) -> None:
        for line in NOTIFY_JOB.splitlines():
            if "${{ github.event" in line and '": ' in line:
                self.assertIn("toJSON(", line, line)

    def test_summary_does_not_mention_users(self) -> None:
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
