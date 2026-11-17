#!/usr/bin/env python3

import importlib.util
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any, TypeAlias
from unittest.mock import patch

JsonObject: TypeAlias = dict[str, Any]
EnvOverrides: TypeAlias = dict[str, str]

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
WORKFLOW: str = (
    REPO_ROOT / ".github/workflows/closed-issue-comment.yml"
).read_text(encoding="utf-8")

SCRIPT_PATH: Path = REPO_ROOT / "scripts/closed_issue_comment.py"
SPEC: ModuleSpec | None = importlib.util.spec_from_file_location(
    "closed_issue_comment", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None

MONITOR: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MONITOR)

REPO: str = "aws/example"
DEFAULT_ENV: EnvOverrides = {
    "GITHUB_REPOSITORY": REPO,
    "CLOSED_ISSUE_REPLY_AFTER_DAYS": "7",
}


def iso(days_ago: float) -> str:
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def event(
    *,
    closed_days_ago: float = 30,
    state: str = "closed",
    association: str = "NONE",
    user_type: str = "User",
    pull_request: bool = False,
) -> JsonObject:
    issue: JsonObject = {
        "number": 5,
        "state": state,
        "closed_at": iso(closed_days_ago),
    }
    if pull_request:
        issue["pull_request"] = {}
    return {
        "issue": issue,
        "comment": {
            "author_association": association,
            "user": {"login": "reporter", "type": user_type},
        },
    }


def generated_reply(*, days_ago: float, author: str | None = None) -> JsonObject:
    return {
        "body": MONITOR.reply_comment(),
        "created_at": iso(days_ago),
        "user": {"login": author or MONITOR.GENERATED_REPLY_AUTHOR},
    }


class FakeGitHub:
    def __init__(self, comments: list[JsonObject] | None = None) -> None:
        self.comments: list[JsonObject] = comments or []
        self.posted: list[tuple[int, JsonObject]] = []

    def __call__(
        self, arguments: list[str], *, input_value: JsonObject | None = None
    ) -> Any:
        head = arguments[0]
        if head == "--method":
            method, endpoint = arguments[1], arguments[2]
            if method == "POST" and endpoint == f"repos/{REPO}/issues/5/comments":
                assert input_value is not None
                self.posted.append((5, input_value))
                return {}
            raise AssertionError(f"unexpected mutation: {arguments}")
        if head.startswith(f"repos/{REPO}/issues/5/comments?"):
            page = int(re.search(r"[?&]page=(\d+)", head).group(1))  # type: ignore[union-attr]
            return self.comments if page == 1 else []
        raise AssertionError(f"unexpected request: {arguments}")


def run_with(
    payload: JsonObject,
    fake: FakeGitHub | None = None,
    env_overrides: EnvOverrides | None = None,
) -> tuple[bool, FakeGitHub]:
    fake = fake or FakeGitHub()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "event.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        env = {
            **DEFAULT_ENV,
            "GITHUB_EVENT_PATH": str(path),
            **(env_overrides or {}),
        }
        with patch.dict(MONITOR.os.environ, env, clear=True), patch.object(
            MONITOR, "gh_run_json", fake
        ):
            notify = MONITOR.run()
    return notify, fake


class ClosedIssueCommentTest(unittest.TestCase):
    def test_replies_and_notifies_for_outside_comment(self) -> None:
        notify, fake = run_with(event())
        self.assertTrue(notify)
        self.assertEqual(len(fake.posted), 1)
        self.assertIn(MONITOR.REPLY_MARKER, fake.posted[0][1]["body"])

    def test_open_issue_is_skipped(self) -> None:
        notify, fake = run_with(event(state="open"))
        self.assertFalse(notify)
        self.assertEqual(fake.posted, [])

    def test_pull_request_is_skipped(self) -> None:
        notify, fake = run_with(event(pull_request=True))
        self.assertFalse(notify)
        self.assertEqual(fake.posted, [])

    def test_team_comments_are_skipped(self) -> None:
        for association in ("OWNER", "MEMBER", "COLLABORATOR"):
            notify, fake = run_with(event(association=association))
            self.assertFalse(notify, association)
            self.assertEqual(fake.posted, [], association)

    def test_contributor_comment_is_flagged(self) -> None:
        notify, fake = run_with(event(association="CONTRIBUTOR"))
        self.assertTrue(notify)
        self.assertEqual(len(fake.posted), 1)

    def test_bot_comment_is_skipped(self) -> None:
        notify, fake = run_with(event(user_type="Bot"))
        self.assertFalse(notify)
        self.assertEqual(fake.posted, [])

    def test_recently_closed_notifies_without_reply(self) -> None:
        notify, fake = run_with(event(closed_days_ago=2))
        self.assertTrue(notify)
        self.assertEqual(fake.posted, [])

    def test_zero_days_replies_right_away(self) -> None:
        notify, fake = run_with(
            event(closed_days_ago=0.01),
            env_overrides={"CLOSED_ISSUE_REPLY_AFTER_DAYS": "0"},
        )
        self.assertTrue(notify)
        self.assertEqual(len(fake.posted), 1)

    def test_existing_reply_since_close_is_not_repeated(self) -> None:
        fake = FakeGitHub([generated_reply(days_ago=10)])
        notify, fake = run_with(event(closed_days_ago=30), fake)
        self.assertTrue(notify)
        self.assertEqual(fake.posted, [])

    def test_reply_from_before_reopen_does_not_suppress(self) -> None:
        fake = FakeGitHub([generated_reply(days_ago=40)])
        notify, fake = run_with(event(closed_days_ago=30), fake)
        self.assertTrue(notify)
        self.assertEqual(len(fake.posted), 1)

    def test_marker_from_non_bot_does_not_suppress(self) -> None:
        fake = FakeGitHub([generated_reply(days_ago=10, author="someone")])
        notify, fake = run_with(event(closed_days_ago=30), fake)
        self.assertTrue(notify)
        self.assertEqual(len(fake.posted), 1)

    def test_rejects_negative_days(self) -> None:
        with self.assertRaises(MONITOR.ClosedIssueCommentError):
            run_with(event(), env_overrides={"CLOSED_ISSUE_REPLY_AFTER_DAYS": "-1"})

    def test_rejects_non_integer_days(self) -> None:
        with self.assertRaises(MONITOR.ClosedIssueCommentError):
            run_with(event(), env_overrides={"CLOSED_ISSUE_REPLY_AFTER_DAYS": "x"})

    def test_rejects_bad_repository(self) -> None:
        with self.assertRaises(MONITOR.ClosedIssueCommentError):
            run_with(event(), env_overrides={"GITHUB_REPOSITORY": "not-a-repo"})


class WorkflowContractTest(unittest.TestCase):
    def test_is_reusable(self) -> None:
        self.assertIn("workflow_call:", WORKFLOW)
        self.assertIn("default: 7", WORKFLOW)

    def test_slack_secret_is_optional(self) -> None:
        self.assertRegex(
            WORKFLOW,
            r"SLACK_WEBHOOK_URL_ISSUE:\n\s+description: .*\n\s+required: false",
        )

    def test_skips_pull_requests_and_open_issues(self) -> None:
        self.assertIn("github.event.issue.state == 'closed'", WORKFLOW)
        self.assertIn("!github.event.issue.pull_request", WORKFLOW)

    def test_grants_minimal_permissions(self) -> None:
        self.assertIn("permissions: {}", WORKFLOW)
        self.assertIn("issues: write", WORKFLOW)
        self.assertIn("contents: read", WORKFLOW)

    def test_checks_out_only_trusted_toolkit(self) -> None:
        self.assertIn("repository: ${{ job.workflow_repository }}", WORKFLOW)
        self.assertIn("ref: ${{ job.workflow_sha }}", WORKFLOW)
        self.assertIn(
            "sparse-checkout: scripts/closed_issue_comment.py", WORKFLOW
        )
        self.assertIn("persist-credentials: false", WORKFLOW)

    def test_actions_are_pinned_to_commit_sha(self) -> None:
        for reference in re.findall(r"uses: (\S+)", WORKFLOW):
            self.assertRegex(
                reference,
                r"@[0-9a-f]{40}$",
                f"{reference} must be pinned to a full commit SHA",
            )


if __name__ == "__main__":
    unittest.main()
