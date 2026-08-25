#!/usr/bin/env python3

import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from record_ai_review_activity import (
    GitHubApiError,
    VerdictRejected,
    activity_records,
    parse_finding_marker,
    parse_verdict_command,
    suggestion_applied,
    verdict_record,
)


BASE_SHA = "1" * 40
HEAD_SHA = "2" * 40


def pull_request_event() -> dict:
    return {
        "action": "synchronize",
        "before": "3" * 40,
        "after": HEAD_SHA,
        "sender": {"login": "author", "id": 7, "type": "User"},
        "pull_request": {
            "number": 42,
            "updated_at": "2026-08-25T12:00:00Z",
            "draft": False,
            "user": {"login": "author", "id": 7, "type": "User"},
            "base": {"sha": BASE_SHA},
            "head": {"sha": HEAD_SHA},
        },
    }


class RecordAiReviewActivityTest(unittest.TestCase):
    def test_pull_request_event_retains_exact_revision_linkage(self):
        records = activity_records(
            "pull_request_target",
            pull_request_event(),
            repository_id=123,
            repository="aws/example",
        )

        self.assertEqual(len(records), 1)
        data = records[0]["data"]
        self.assertEqual(data["event_timestamp"], "2026-08-25T12:00:00Z")
        self.assertEqual(data["base_sha"], BASE_SHA)
        self.assertEqual(data["head_sha"], HEAD_SHA)
        self.assertEqual(data["before_sha"], "3" * 40)
        self.assertEqual(data["after_sha"], HEAD_SHA)

    def test_human_records_do_not_store_comment_bodies(self):
        event = {
            "action": "created",
            "pull_request": {"number": 42},
            "comment": {
                "node_id": "PRRC_human",
                "body": "Sensitive review text",
                "created_at": "2026-08-25T12:01:00Z",
                "path": "src/example.py",
                "commit_id": HEAD_SHA,
                "original_commit_id": HEAD_SHA,
                "user": {"login": "reviewer", "id": 8, "type": "User"},
            },
        }

        record = activity_records(
            "pull_request_review_comment",
            event,
            repository_id=123,
            repository="aws/example",
        )[0]

        self.assertNotIn("body", record["data"])
        self.assertEqual(record["data"]["path"], "src/example.py")
        self.assertFalse(record["data"]["actor"]["is_bot"])

    def test_verdict_parsing_supports_replies_and_explicit_ids(self):
        reply = {
            "action": "created",
            "comment": {
                "body": "/ai verdict false-positive",
                "user": {"login": "maintainer", "type": "User"},
            },
        }
        explicit = {
            "action": "created",
            "comment": {
                "body": (
                    "/ai verdict arf_v1_"
                    + "a" * 26
                    + " already-fixed"
                ),
                "user": {"login": "maintainer", "type": "User"},
            },
        }

        self.assertEqual(
            parse_verdict_command("pull_request_review_comment", reply),
            (None, "false-positive"),
        )
        self.assertEqual(
            parse_verdict_command("issue_comment", explicit),
            ("arf_v1_" + "a" * 26, "already-fixed"),
        )

    def test_finding_marker_is_strict(self):
        self.assertEqual(
            parse_finding_marker(
                "<!-- ai-pr-review:finding:codex:arf_v1_"
                + "a" * 26
                + " -->"
            ),
            ("codex", "arf_v1_" + "a" * 26),
        )
        self.assertIsNone(parse_finding_marker("Finding arf_v1_" + "a" * 26))

    def test_exact_suggestion_adoption_requires_unique_context(self):
        old = [
            "before_a",
            "before_b",
            "old_value",
            "after_a",
            "after_b",
        ]
        new = [
            "before_a",
            "before_b",
            "new_value",
            "after_a",
            "after_b",
        ]
        self.assertTrue(
            suggestion_applied(
                old,
                new,
                start_line=3,
                end_line=3,
                replacement="new_value",
            )
        )
        self.assertFalse(
            suggestion_applied(
                old,
                new + new,
                start_line=3,
                end_line=3,
                replacement="new_value",
            )
        )

    def test_verdict_record_binds_maintainer_and_reviewed_head(self):
        finding_id = "arf_v1_" + "a" * 26
        event = {
            "action": "created",
            "issue": {"number": 42, "pull_request": {}},
            "comment": {
                "node_id": "IC_verdict",
                "body": f"/ai verdict {finding_id} accepted",
                "created_at": "2026-08-25T12:02:00Z",
                "user": {
                    "login": "maintainer",
                    "id": 9,
                    "type": "User",
                },
            },
        }
        api = Mock()
        api.request.side_effect = [
            {"permission": "write"},
            {"head": {"sha": HEAD_SHA}},
        ]
        store = Mock()
        store.repository = "aws/example"
        store.read_json.side_effect = [
            None,
            {
                "schema_version": 1,
                "finding_id": finding_id,
                "finding_digest": "4" * 64,
                "repository": {
                    "id": 123,
                    "full_name": "aws/example",
                },
                "pull_request_number": 42,
                "reviewer": "codex",
                "identity_key": "scripts/review.py::publish::stale-head",
            },
        ]
        store.list_json.return_value = [
            {
                "record_id": "art_v1_" + "b" * 26,
                "data": {
                    "finding_id": finding_id,
                    "reviewed_base_sha": BASE_SHA,
                    "reviewed_head_sha": "3" * 40,
                    "published_at": "2026-08-25T12:01:00Z",
                },
            }
        ]

        record, reply = verdict_record(
            api,
            store,
            "issue_comment",
            event,
            repository_id=123,
            repository="aws/example",
        )

        self.assertEqual(reply, ("issue_comment", 0))
        self.assertEqual(record["data"]["outcome"], "accepted")
        self.assertEqual(record["data"]["reviewer"], "codex")
        self.assertEqual(record["data"]["reviewed_head_sha"], "3" * 40)
        self.assertEqual(record["data"]["current_head_sha"], HEAD_SHA)
        self.assertEqual(record["data"]["maintainer"]["login"], "maintainer")

    def test_verdict_rejects_a_non_collaborator_without_failing_activity(self):
        finding_id = "arf_v1_" + "a" * 26
        event = {
            "action": "created",
            "issue": {"number": 42, "pull_request": {}},
            "comment": {
                "node_id": "IC_verdict",
                "body": f"/ai verdict {finding_id} accepted",
                "created_at": "2026-08-25T12:02:00Z",
                "user": {
                    "login": "outside-user",
                    "id": 9,
                    "type": "User",
                },
            },
        }
        api = Mock()
        api.request.side_effect = GitHubApiError("not found", 404)

        with self.assertRaisesRegex(
            VerdictRejected,
            "lacks write permission",
        ):
            verdict_record(
                api,
                Mock(),
                "issue_comment",
                event,
                repository_id=123,
                repository="aws/example",
            )


if __name__ == "__main__":
    unittest.main()
