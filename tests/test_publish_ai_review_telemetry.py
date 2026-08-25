#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from publish_ai_review_telemetry import (
    TelemetryError,
    fail_review,
    plan_review,
    publish_review,
)


def prepared_review() -> dict:
    return {
        "summary": "One finding.",
        "comments": [{"body": "Published comment payload."}],
        "telemetry": {
            "review": {
                "review_id": "arr_v1_" + "a" * 26,
                "review_digest": "1" * 64,
                "reviewer": "codex",
                "repository_id": 123,
                "repository": "aws/example",
                "pull_request_number": 42,
                "base_sha": "1" * 40,
                "head_sha": "2" * 40,
                "workflow_sha": "3" * 40,
                "workflow_run_id": 100,
                "workflow_run_attempt": 1,
                "model": "openai.gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "scope_id": "ars_v1_" + "b" * 26,
                "scope_digest": "2" * 64,
                "trigger": {
                    "trigger_id": "art_v1_" + "c" * 26,
                    "event_name": "pull_request_target",
                },
            },
            "findings": [
                {
                    "finding_id": "arf_v1_" + "d" * 26,
                    "finding_digest": "3" * 64,
                    "finding_key": "scripts/review.py::publish::stale-head",
                    "identity_key": "scripts/review.py::publish::stale-head",
                    "prior_finding_id": "",
                    "observation_id": "aro_v1_" + "e" * 26,
                    "observation_digest": "4" * 64,
                    "path": "scripts/review.py",
                    "start_line": 10,
                    "line": 12,
                    "body": "The publisher can target a stale revision.",
                    "has_suggestion": True,
                    "suggestion": "verify_head()",
                    "suggestion_id": "arsg_v1_" + "f" * 26,
                    "suggestion_digest": "5" * 64,
                }
            ],
        },
    }


class PublishAiReviewTelemetryTest(unittest.TestCase):
    def test_plan_persists_ids_before_publication_without_finding_body(self):
        store = Mock()
        prepared = prepared_review()

        record = plan_review(
            store,
            prepared,
            recorded_at="2026-08-25T12:00:00Z",
        )

        self.assertEqual(record["record_type"], "review_planned")
        manifest = record["data"]["findings"][0]
        self.assertEqual(
            manifest["finding_id"],
            prepared["telemetry"]["findings"][0]["finding_id"],
        )
        self.assertNotIn("body", manifest)
        extra_files = store.write_records.call_args.kwargs["extra_files"]
        finding_file = next(
            value
            for path, value in extra_files.items()
            if path.startswith("findings/")
        )
        self.assertNotIn("body", finding_file)
        self.assertFalse(
            any(path.startswith("suggestions/") for path in extra_files)
        )

    def test_publish_records_review_and_each_observation(self):
        store = Mock()
        prepared = prepared_review()
        observation_id = prepared["telemetry"]["findings"][0]["observation_id"]

        records = publish_review(
            store,
            prepared,
            [
                {
                    "observation_id": observation_id,
                    "comment_node_id": "PRRC_finding",
                    "created_at": "2026-08-25T12:01:00Z",
                }
            ],
            {
                "comment_node_id": "IC_summary",
                "created_at": "2026-08-25T12:01:10Z",
            },
        )

        self.assertEqual(
            [record["record_type"] for record in records],
            ["review_published", "finding_observed"],
        )
        observed = records[1]["data"]
        self.assertEqual(observed["comment_node_id"], "PRRC_finding")
        self.assertEqual(
            observed["body"],
            "The publisher can target a stale revision.",
        )
        extra_files = store.write_records.call_args.kwargs["extra_files"]
        suggestion_file = next(iter(extra_files.values()))
        self.assertEqual(suggestion_file["replacement"], "verify_head()")
        store.write_records.assert_called_once()

    def test_publish_requires_exactly_one_comment_per_observation(self):
        store = Mock()
        with self.assertRaisesRegex(TelemetryError, "not every"):
            publish_review(
                store,
                prepared_review(),
                [],
                {
                    "comment_node_id": "IC_summary",
                    "created_at": "2026-08-25T12:01:10Z",
                },
            )

    def test_failure_record_uses_only_trusted_partial_comment_ids(self):
        store = Mock()
        prepared = prepared_review()
        observation_id = prepared["telemetry"]["findings"][0]["observation_id"]

        record = fail_review(
            store,
            prepared,
            [
                {
                    "observation_id": observation_id,
                    "comment_node_id": "PRRC_partial",
                    "created_at": "not needed for failure",
                },
                {
                    "observation_id": "aro_v1_" + "z" * 26,
                    "comment_node_id": "PRRC_untrusted",
                    "created_at": "ignored",
                },
            ],
            recorded_at="2026-08-25T12:02:00Z",
        )

        self.assertEqual(
            record["data"]["partial_comments"],
            [
                {
                    "observation_id": observation_id,
                    "comment_node_id": "PRRC_partial",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
