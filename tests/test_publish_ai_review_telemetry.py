#!/usr/bin/env python3

import copy
import json
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
    prepared_telemetry,
)
from ai_review_telemetry import TelemetryConflictError, TelemetryStore


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
                "model": "openai.gpt-6-astra",
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
            "suppressed_findings": [],
        },
    }


def suppressed_review(status="deferred") -> dict:
    prepared = prepared_review()
    finding = prepared["telemetry"]["findings"].pop()
    finding["suppression_status"] = status
    prepared["telemetry"]["suppressed_findings"].append(finding)
    prepared["comments"] = []
    return prepared


def mixed_review() -> dict:
    prepared = prepared_review()
    suppressed = suppressed_review()["telemetry"]["suppressed_findings"][0]
    suppressed["finding_id"] = "arf_v1_" + "g" * 26
    suppressed["observation_id"] = "aro_v1_" + "h" * 26
    prepared["telemetry"]["suppressed_findings"].append(suppressed)
    return prepared


SUMMARY = {"comment_node_id": "IC_summary", "created_at": "2026-08-25T12:01:10Z"}


class ImmutableStore(TelemetryStore):
    def __init__(self):
        super().__init__("aws/example")
        self.files = {}

    def write_files(self, files, *, message):
        for path, content in files.items():
            if path in self.files and self.files[path] != content:
                raise TelemetryConflictError(f"conflicting bytes at {path}")
        self.files.update(files)

    def records(self, record_type):
        return [
            json.loads(content)
            for path, content in self.files.items()
            if f"/{record_type}/" in path
        ]


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

    def test_suppressed_lifecycle_is_immutable_and_idempotent(self):
        for status in ("deferred", "false-positive", "out-of-scope"):
            with self.subTest(status=status):
                store = ImmutableStore()
                prepared = suppressed_review(status)
                planned = plan_review(store, prepared, recorded_at="2026-08-25T12:00:00Z")
                manifest = planned["data"]["suppressed_findings"][0]
                self.assertEqual(manifest["suppression_status"], status)
                self.assertEqual(manifest["suggestion_id"], "")
                self.assertTrue(manifest["body_digest"].startswith("sha256:"))
                self.assertTrue(manifest["suggestion_digest"].startswith("sha256:"))
                self.assertNotIn("body", manifest)
                self.assertNotIn("suggestion", manifest)
                self.assertEqual(store.records("finding_observed"), [])
                identity_files = [
                    json.loads(content)
                    for path, content in store.files.items()
                    if path.startswith("findings/")
                ]
                self.assertEqual(len(identity_files), 1)
                self.assertNotIn("body", identity_files[0])

                records = publish_review(store, prepared, [], SUMMARY)
                self.assertEqual([record["record_type"] for record in records], [
                    "review_published", "finding_observed",
                ])
                self.assertEqual(records[0]["data"]["findings"], [])
                self.assertEqual(records[0]["data"]["suppressed_findings"], [manifest])
                observed = records[1]
                finding = prepared["telemetry"]["suppressed_findings"][0]
                self.assertEqual(observed["recorded_at"], SUMMARY["created_at"])
                self.assertEqual(observed["data"]["published_at"], SUMMARY["created_at"])
                self.assertEqual(observed["data"]["suppression_status"], status)
                self.assertTrue(observed["data"]["suppressed"])
                self.assertEqual(observed["data"]["comment_node_id"], "")
                self.assertEqual(observed["data"]["suggestion_id"], "")
                for field in ("finding_id", "observation_id", "path", "start_line", "line", "body"):
                    self.assertEqual(observed["data"][field], finding[field])
                self.assertEqual(observed["data"]["reviewed_head_sha"], "2" * 40)
                self.assertFalse(any(path.startswith("suggestions/") for path in store.files))
                before = dict(store.files)
                self.assertEqual(publish_review(store, prepared, [], SUMMARY), records)
                self.assertEqual(store.files, before)
                self.assertEqual(len(store.records("finding_observed")), 1)
                with self.assertRaises(TelemetryConflictError):
                    publish_review(
                        store, prepared, [],
                        {**SUMMARY, "created_at": "2026-08-25T12:01:11Z"},
                    )
                self.assertEqual(store.files, before)

    def test_failed_publication_writes_no_suppressed_observations(self):
        store = ImmutableStore()
        prepared = mixed_review()
        plan_review(store, prepared, recorded_at="2026-08-25T12:00:00Z")
        failure = fail_review(
            store, prepared,
            [{
                "observation_id": prepared["telemetry"]["findings"][0]["observation_id"],
                "comment_node_id": "PRRC_partial",
            }],
            recorded_at="2026-08-25T12:02:00Z",
        )
        self.assertEqual(len(failure["data"]["partial_comments"]), 1)
        self.assertEqual(store.records("finding_observed"), [])
        self.assertEqual(store.records("review_published"), [])
        self.assertEqual(len(store.records("review_planned")[0]["data"]["suppressed_findings"]), 1)

    def test_mixed_publication_preserves_posted_mapping_and_suggestions(self):
        prepared = mixed_review()
        store = ImmutableStore()
        plan_review(store, prepared, recorded_at="2026-08-25T12:00:00Z")
        records = publish_review(
            store, prepared,
            [{
                "observation_id": prepared["telemetry"]["findings"][0]["observation_id"],
                "comment_node_id": "PRRC_posted",
                "created_at": "2026-08-25T12:01:00Z",
            }],
            SUMMARY,
        )
        posted, suppressed = records[1:]
        self.assertEqual(posted["data"]["comment_node_id"], "PRRC_posted")
        self.assertEqual(posted["recorded_at"], "2026-08-25T12:01:00Z")
        self.assertNotIn("suppressed", posted["data"])
        self.assertTrue(suppressed["data"]["suppressed"])
        suggestions = [path for path in store.files if path.startswith("suggestions/")]
        self.assertEqual(len(suggestions), 1)
        self.assertIn(posted["data"]["suggestion_id"], suggestions[0])

    def test_suppressed_observation_is_not_a_posted_mapping_target(self):
        prepared = suppressed_review()
        with self.assertRaisesRegex(TelemetryError, "unknown observation"):
            publish_review(
                Mock(), prepared,
                [{
                    "observation_id": prepared["telemetry"]["suppressed_findings"][0]["observation_id"],
                    "comment_node_id": "PRRC_forged",
                    "created_at": SUMMARY["created_at"],
                }],
                SUMMARY,
            )
        store = ImmutableStore()
        with self.assertRaisesRegex(TelemetryError, "summary comment mapping"):
            publish_review(store, prepared, [], None)
        self.assertEqual(store.files, {})

    def test_suppressed_telemetry_is_required_and_validated(self):
        invalid = []
        missing = prepared_review()
        missing["telemetry"].pop("suppressed_findings")
        invalid.append(missing)
        wrong_type = prepared_review()
        wrong_type["telemetry"]["suppressed_findings"] = {}
        invalid.append(wrong_type)
        for status in ("open", "accepted", "already-fixed", [], None):
            invalid.append(suppressed_review(status))
        missing_status = suppressed_review()
        missing_status["telemetry"]["suppressed_findings"][0].pop("suppression_status")
        invalid.append(missing_status)
        extra_flag = suppressed_review()
        extra_flag["telemetry"]["suppressed_findings"][0]["suppressed"] = True
        invalid.append(extra_flag)
        duplicate = mixed_review()
        duplicate["telemetry"]["suppressed_findings"][0]["finding_id"] = (
            duplicate["telemetry"]["findings"][0]["finding_id"]
        )
        invalid.append(duplicate)
        bad_body = suppressed_review()
        bad_body["telemetry"]["suppressed_findings"][0]["body"] = None
        invalid.append(bad_body)
        for position, prepared in enumerate(invalid):
            with self.subTest(position=position):
                with self.assertRaises(TelemetryError):
                    prepared_telemetry(copy.deepcopy(prepared))


if __name__ == "__main__":
    unittest.main()
