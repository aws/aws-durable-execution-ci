#!/usr/bin/env python3

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts/resolve_ai_review.py"
SPEC = importlib.util.spec_from_file_location("resolve_ai_review", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load AI review resolver")
RESOLVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RESOLVER)


BASE_SHA = "1" * 40
HEAD_SHA = "2" * 40
REPOSITORY = "aws/example"


def pull_request(
    *,
    author: str = "contributor",
    draft: bool = False,
    head_repository: str | None = REPOSITORY,
    state: str = "open",
) -> dict:
    head_repo = (
        {"full_name": head_repository}
        if head_repository is not None
        else None
    )
    return {
        "number": 42,
        "state": state,
        "draft": draft,
        "user": {"login": author},
        "base": {"sha": BASE_SHA},
        "head": {"sha": HEAD_SHA, "repo": head_repo},
    }


def pull_request_event() -> dict:
    return {
        "action": "synchronize",
        "pull_request": {"number": 42},
    }


def review_command_event(
    *,
    body: str = "/ai review",
    login: str = "maintainer",
    user_type: str = "User",
) -> dict:
    return {
        "action": "created",
        "issue": {
            "number": 42,
            "state": "open",
            "pull_request": {"url": "https://api.github.test/pulls/42"},
        },
        "comment": {
            "body": body,
            "user": {"login": login, "type": user_type},
        },
    }


class ResolveAiReviewTest(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"GITHUB_REPOSITORY": REPOSITORY},
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_trusted_pull_request_runs_automatically(self):
        with patch.object(
            RESOLVER,
            "run_gh_json",
            return_value=pull_request(),
        ) as run_gh:
            result = RESOLVER.resolve_review(
                "pull_request_target",
                pull_request_event(),
            )

        self.assertEqual(
            result,
            {
                "review-requested": "true",
                "pull-request-number": "42",
                "base-sha": BASE_SHA,
                "head-sha": HEAD_SHA,
            },
        )
        run_gh.assert_called_once_with(f"repos/{REPOSITORY}/pulls/42")

    def test_gated_pull_requests_wait_for_review_command(self):
        cases = (
            pull_request(author="dependabot[bot]"),
            pull_request(draft=True),
            pull_request(head_repository="contributor/example"),
            pull_request(head_repository=None),
        )
        for pull_request_data in cases:
            with self.subTest(pull_request=pull_request_data), patch.object(
                RESOLVER,
                "run_gh_json",
                return_value=pull_request_data,
            ):
                result = RESOLVER.resolve_review(
                    "pull_request_target",
                    pull_request_event(),
                )

            self.assertIsNone(result)

    def test_authorized_review_command_runs_gated_pull_request(self):
        with patch.object(
            RESOLVER,
            "run_gh_json",
            side_effect=[
                {"permission": "write"},
                pull_request(
                    draft=True,
                    head_repository="contributor/example",
                ),
            ],
        ) as run_gh:
            result = RESOLVER.resolve_review(
                "issue_comment",
                review_command_event(),
            )

        self.assertIsNotNone(result)
        self.assertEqual(
            run_gh.call_args_list[0].args[0],
            f"repos/{REPOSITORY}/collaborators/maintainer/permission",
        )
        self.assertEqual(
            run_gh.call_args_list[1].args[0],
            f"repos/{REPOSITORY}/pulls/42",
        )

    def test_each_write_level_can_request_review(self):
        for permission in ("write", "maintain", "admin"):
            with self.subTest(permission=permission), patch.object(
                RESOLVER,
                "run_gh_json",
                side_effect=[
                    {"permission": permission},
                    pull_request(),
                ],
            ):
                result = RESOLVER.resolve_review(
                    "issue_comment",
                    review_command_event(),
                )

            self.assertIsNotNone(result)

    def test_unauthorized_review_command_is_ignored(self):
        with patch.object(
            RESOLVER,
            "run_gh_json",
            return_value={"permission": "read"},
        ) as run_gh:
            result = RESOLVER.resolve_review(
                "issue_comment",
                review_command_event(),
            )

        self.assertIsNone(result)
        run_gh.assert_called_once()

    def test_non_commands_and_bots_are_ignored_without_api_calls(self):
        events = (
            review_command_event(body="/ai review please"),
            review_command_event(login="dependabot[bot]", user_type="Bot"),
            review_command_event(login="automation[bot]", user_type="User"),
        )
        for event in events:
            with self.subTest(event=event), patch.object(
                RESOLVER,
                "run_gh_json",
            ) as run_gh:
                result = RESOLVER.resolve_review(
                    "issue_comment",
                    event,
                )

            self.assertIsNone(result)
            run_gh.assert_not_called()

    def test_closed_pull_request_is_ignored(self):
        with patch.object(
            RESOLVER,
            "run_gh_json",
            side_effect=[
                {"permission": "admin"},
                pull_request(state="closed"),
            ],
        ):
            result = RESOLVER.resolve_review(
                "issue_comment",
                review_command_event(),
            )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
