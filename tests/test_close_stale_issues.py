#!/usr/bin/env python3

import importlib.util
from importlib.machinery import ModuleSpec
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeAlias
from unittest.mock import patch

JsonObject: TypeAlias = dict[str, Any]
IssueList: TypeAlias = list[JsonObject]
TimelineMap: TypeAlias = dict[int, list[JsonObject]]
EnvOverrides: TypeAlias = dict[str, str]
GitHubResponse: TypeAlias = JsonObject | list[JsonObject]

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
WORKFLOW: str = (
    REPO_ROOT / ".github/workflows/stale-issue-closer.yml"
).read_text(encoding="utf-8")

SCRIPT_PATH: Path = REPO_ROOT / "scripts/close_stale_issues.py"
SPEC: ModuleSpec | None = importlib.util.spec_from_file_location(
    "close_stale_issues", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None

CLOSER: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLOSER)

REPO: str = "aws/example"
DEFAULT_ENV: EnvOverrides = {
    "GITHUB_REPOSITORY": REPO,
    "STALE_DAYS_UNTIL_CLOSE": "14",
    "STALE_CLOSE_REASON": "not_planned",
}


def iso(days_ago: float) -> str:
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def labeled(*, days_ago: float, name: str = "needs-info") -> JsonObject:
    return {"event": "labeled", "label": {"name": name}, "created_at": iso(days_ago)}


def commented(*, days_ago: float) -> JsonObject:
    return {"event": "commented", "created_at": iso(days_ago)}


class FakeGitHub:
    def __init__(self, issues: IssueList, timelines: TimelineMap) -> None:
        self.issues: IssueList = issues
        self.timelines: TimelineMap = timelines
        self.comments: list[tuple[int, JsonObject]] = []
        self.closed_issues: list[tuple[int, JsonObject]] = []

    def __call__(
        self, arguments: list[str], *, input_value: JsonObject | None = None
    ) -> GitHubResponse:
        head: str = arguments[0]
        if head == "--method":
            method, endpoint = arguments[1], arguments[2]
            number = int(endpoint.split("/issues/")[1].split("/")[0])
            if method == "POST" and endpoint.endswith("/comments"):
                assert input_value is not None
                self.comments.append((number, input_value))
                return {}
            if method == "PATCH":
                assert input_value is not None
                self.closed_issues.append((number, input_value))
                return {}
            raise AssertionError(f"unexpected mutation: {arguments}")
        if "/timeline" in head:
            number = int(head.split("/issues/")[1].split("/")[0])
            return self.timelines.get(number, [])
        if head.startswith(f"repos/{REPO}/issues?"):
            page_match = re.search(r"[?&]page=(\d+)", head)
            assert page_match is not None
            page = int(page_match.group(1))
            return self.issues if page == 1 else []
        raise AssertionError(f"unexpected request: {arguments}")


def run_with(
    issues: IssueList,
    timelines: TimelineMap,
    env_overrides: EnvOverrides | None = None,
) -> FakeGitHub:
    env: EnvOverrides = {**DEFAULT_ENV, **(env_overrides or {})}
    fake: FakeGitHub = FakeGitHub(issues, timelines)
    with patch.dict(CLOSER.os.environ, env, clear=True), patch.object(
        CLOSER, "run_gh_json", fake
    ):
        CLOSER.run()
    return fake


class StaleClosingTest(unittest.TestCase):
    def test_closes_when_no_response_past_window(self) -> None:
        fake = run_with(
            [{"number": 7}],
            {7: [labeled(days_ago=20)]},
        )
        self.assertEqual([n for n, _ in fake.closed_issues], [7])
        self.assertEqual([n for n, _ in fake.comments], [7])
        self.assertEqual(
            fake.closed_issues[0][1],
            {"state": "closed", "state_reason": "not_planned"},
        )

    def test_comment_after_label_resets_clock(self) -> None:
        fake = run_with(
            [{"number": 7}],
            {7: [labeled(days_ago=20), commented(days_ago=2)]},
        )
        self.assertEqual(fake.closed_issues, [])
        self.assertEqual(fake.comments, [])


    def test_old_comment_before_label_does_not_reset(self) -> None:
        fake = run_with(
            [{"number": 7}],
            {7: [commented(days_ago=40), labeled(days_ago=20)]},
        )
        self.assertEqual([n for n, _ in fake.closed_issues], [7])

    def test_within_window_is_skipped(self) -> None:
        fake = run_with(
            [{"number": 7}],
            {7: [labeled(days_ago=3)]},
        )
        self.assertEqual(fake.closed_issues, [])
        self.assertEqual(fake.comments, [])

    def test_missing_label_event_is_skipped(self) -> None:
        fake = run_with(
            [{"number": 7}],
            {7: [commented(days_ago=30)]},
        )
        self.assertEqual(fake.closed_issues, [])
        self.assertEqual(fake.comments, [])

    def test_pull_requests_are_ignored(self) -> None:
        fake = run_with(
            [{"number": 7, "pull_request": {"url": "x"}}, {"number": 8}],
            {7: [labeled(days_ago=20)], 8: [labeled(days_ago=20)]},
        )
        self.assertEqual([n for n, _ in fake.closed_issues], [8])

    def test_uses_most_recent_label_event(self) -> None:
        # Removed then re-applied: the latest application starts the clock.
        fake = run_with(
            [{"number": 7}],
            {7: [labeled(days_ago=30), labeled(days_ago=3)]},
        )
        self.assertEqual(fake.closed_issues, [])

    def test_custom_close_reason(self) -> None:
        fake = run_with(
            [{"number": 7}],
            {7: [labeled(days_ago=20)]},
            {"STALE_CLOSE_REASON": "completed"},
        )
        self.assertEqual(fake.closed_issues[0][1]["state_reason"], "completed")

    def test_comment_body_uses_configured_window(self) -> None:
        fake = run_with(
            [{"number": 7}],
            {7: [labeled(days_ago=40)]},
            {"STALE_DAYS_UNTIL_CLOSE": "30"},
        )
        body = fake.comments[0][1]["body"]
        self.assertIn("not received a response in 30 days", body)
        self.assertEqual(
            body,
            "This issue has been automatically closed because it was labeled "
            "`needs-info` and has not received a response in 30 days. If you "
            "still need help, please comment with the requested details and "
            "we will be happy to reopen it.",
        )

    def test_comment_body_handles_one_day_window(self) -> None:
        fake = run_with(
            [{"number": 7}],
            {7: [labeled(days_ago=2)]},
            {"STALE_DAYS_UNTIL_CLOSE": "1"},
        )
        self.assertIn(
            "not received a response in 1 day", fake.comments[0][1]["body"]
        )


class ConfigurationTest(unittest.TestCase):
    def _run_expecting_error(self, env_overrides: EnvOverrides) -> None:
        env = {**DEFAULT_ENV, **env_overrides}
        with patch.dict(CLOSER.os.environ, env, clear=True):
            with self.assertRaises(CLOSER.StaleIssueError):
                CLOSER.run()

    def test_rejects_non_positive_days(self) -> None:
        self._run_expecting_error({"STALE_DAYS_UNTIL_CLOSE": "0"})

    def test_rejects_non_integer_days(self) -> None:
        self._run_expecting_error({"STALE_DAYS_UNTIL_CLOSE": "seven"})

    def test_rejects_unknown_close_reason(self) -> None:
        self._run_expecting_error({"STALE_CLOSE_REASON": "wontfix"})

    def test_rejects_bad_repository(self) -> None:
        self._run_expecting_error({"GITHUB_REPOSITORY": "not-a-repo"})


class WorkflowContractTest(unittest.TestCase):
    def test_is_reusable_and_scheduled_by_caller(self) -> None:
        self.assertIn("workflow_call:", WORKFLOW)
        self.assertIn("default: not_planned", WORKFLOW)

    def test_grants_minimal_permissions(self) -> None:
        self.assertIn("permissions: {}", WORKFLOW)
        self.assertIn("issues: write", WORKFLOW)
        self.assertIn("contents: read", WORKFLOW)

    def test_checks_out_only_trusted_toolkit(self) -> None:
        self.assertIn("repository: ${{ job.workflow_repository }}", WORKFLOW)
        self.assertIn("ref: ${{ job.workflow_sha }}", WORKFLOW)
        self.assertIn("sparse-checkout: scripts/close_stale_issues.py", WORKFLOW)
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
