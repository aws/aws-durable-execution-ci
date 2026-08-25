#!/usr/bin/env python3

import base64
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts/resolve_ai_review.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
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
    author: str | None = "contributor",
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
        "created_at": "2026-08-25T10:00:00Z",
        "updated_at": "2026-08-25T12:00:00Z",
        "user": {"login": author} if author is not None else None,
        "base": {"sha": BASE_SHA},
        "head": {"sha": HEAD_SHA, "repo": head_repo},
    }


def pull_request_event() -> dict:
    return {
        "action": "synchronize",
        "before": "3" * 40,
        "after": HEAD_SHA,
        "pull_request": {
            "number": 42,
            "updated_at": "2026-08-25T12:00:00Z",
            "base": {"sha": BASE_SHA},
            "head": {"sha": HEAD_SHA},
        },
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
            "node_id": "IC_review_command",
            "created_at": "2026-08-25T12:01:00Z",
            "user": {"login": login, "type": user_type},
        },
    }


class ResolveAiReviewTest(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "GITHUB_REPOSITORY": REPOSITORY,
                "GITHUB_REPOSITORY_ID": "123",
                "GITHUB_ACTOR": "maintainer",
            },
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
                "review-guidance-base64": "",
                "trigger-metadata-base64": result[
                    "trigger-metadata-base64"
                ],
            },
        )
        trigger = json.loads(
            base64.b64decode(
                result["trigger-metadata-base64"],
                validate=True,
            )
        )
        self.assertEqual(
            trigger,
            {
                "actor": "maintainer",
                "after_sha": HEAD_SHA,
                "before_sha": "3" * 40,
                "command_comment_id": "",
                "event_action": "synchronize",
                "event_base_sha": BASE_SHA,
                "event_head_sha": HEAD_SHA,
                "event_name": "pull_request_target",
                "event_timestamp": "2026-08-25T12:00:00Z",
                "trigger_id": trigger["trigger_id"],
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

    def test_authorized_review_command_allows_deleted_pull_request_author(self):
        with patch.object(
            RESOLVER,
            "run_gh_json",
            side_effect=[
                {"permission": "write"},
                pull_request(
                    author=None,
                    draft=True,
                    head_repository="contributor/example",
                ),
            ],
        ):
            result = RESOLVER.resolve_review(
                "issue_comment",
                review_command_event(),
            )

        self.assertIsNotNone(result)

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

    def test_review_command_accepts_surrounding_and_inner_blanks(self):
        commands = (
            " /ai review ",
            "\t/ai\treview\t",
            "  /ai    review\t",
        )
        for command in commands:
            with self.subTest(command=command), patch.object(
                RESOLVER,
                "run_gh_json",
                side_effect=[
                    {"permission": "write"},
                    pull_request(),
                ],
            ):
                result = RESOLVER.resolve_review(
                    "issue_comment",
                    review_command_event(body=command),
                )

            self.assertIsNotNone(result)

    def test_review_command_accepts_appended_guidance(self):
        commands = {
            "/ai review Focus on serialization.": "Focus on serialization.",
            "/ai review\n\nAdd replay coverage.": "Add replay coverage.",
            (
                "\t/ai  review\tPreserve the public API.\nRun unit tests."
            ): "Preserve the public API.\nRun unit tests.",
        }
        for command, expected_guidance in commands.items():
            with self.subTest(command=command), patch.object(
                RESOLVER,
                "run_gh_json",
                side_effect=[
                    {"permission": "write"},
                    pull_request(),
                ],
            ):
                result = RESOLVER.resolve_review(
                    "issue_comment",
                    review_command_event(body=command),
                )

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(
                base64.b64decode(
                    result["review-guidance-base64"],
                    validate=True,
                ).decode("utf-8"),
                expected_guidance,
            )

    def test_review_guidance_is_size_bounded(self):
        guidance = "x" * (RESOLVER.MAX_REVIEW_GUIDANCE_BYTES + 1)
        with patch.object(
            RESOLVER,
            "run_gh_json",
            return_value={"permission": "write"},
        ):
            with self.assertRaisesRegex(
                RESOLVER.ReviewResolutionError,
                "10000-byte limit",
            ):
                RESOLVER.resolve_review(
                    "issue_comment",
                    review_command_event(
                        body=f"/ai review {guidance}",
                    ),
                )

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
            review_command_event(body="/ai\nreview"),
            review_command_event(body="/ai reviewer"),
            review_command_event(body="/aireview"),
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
