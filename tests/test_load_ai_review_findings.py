#!/usr/bin/env python3

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from ai_review_telemetry import (
    GitHubApiError,
    GitTree,
    TelemetryError,
    TelemetryStore,
    build_record,
    stable_identifier,
)
from load_ai_review_findings import finding_decisions, main, prior_findings


def observation(
    finding_id: str,
    *,
    reviewer: str = "codex",
    finding_key: str = "scripts/review.py::publish::stale-head",
    path: str = "scripts/review.py",
    body: str = "The publisher can post against a stale head.",
    head_sha: str = "a" * 40,
    published_at: str = "2026-08-25T12:00:00Z",
) -> dict:
    return {
        "record_type": "finding_observed",
        "recorded_at": published_at,
        "data": {
            "finding_id": finding_id,
            "finding_key": finding_key,
            "reviewer": reviewer,
            "path": path,
            "body": body,
            "reviewed_head_sha": head_sha,
            "published_at": published_at,
        },
    }


def verdict(
    finding_id: str = "arf_v1_" + "a" * 26,
    *,
    outcome: str = "deferred",
    timestamp: str = "2026-08-25T12:00:00Z",
    reviewer: str = "codex",
) -> dict:
    return build_record(
        record_type="finding_verdict",
        identity={"finding_id": finding_id, "outcome": outcome, "timestamp": timestamp},
        repository_id=123,
        repository="aws/example",
        pull_request_number=42,
        recorded_at=timestamp,
        data={
            "finding_id": finding_id,
            "reviewer": reviewer,
            "outcome": outcome,
            "command_created_at": timestamp,
        },
    )


def decisions(records):
    return finding_decisions(
        records,
        repository="aws/example",
        pull_request_number=42,
        reviewer="codex",
    )


class LoadAiReviewFindingsTest(unittest.TestCase):
    def test_returns_latest_observation_for_each_reviewer_finding(self):
        finding_id = "arf_v1_" + "a" * 26
        records = [
            observation(
                finding_id,
                body="Old body",
                published_at="2026-08-25T10:00:00Z",
            ),
            observation(
                finding_id,
                body="Current body",
                published_at="2026-08-25T12:00:00Z",
            ),
            observation(
                "arf_v1_" + "b" * 26,
                reviewer="claude",
            ),
            {"record_type": "review_published", "data": {}},
        ]

        self.assertEqual(
            prior_findings(records, reviewer="codex"),
            [
                {
                    "finding_id": finding_id,
                    "finding_key": "scripts/review.py::publish::stale-head",
                    "path": "scripts/review.py",
                    "body": "Current body",
                    "reviewed_head_sha": "a" * 40,
                    "observed_at": "2026-08-25T12:00:00Z",
                    "status": "open",
                }
            ],
        )

    def test_ignores_malformed_observations_and_applies_bound(self):
        records = [
            observation(
                f"arf_v1_{character * 26}",
                published_at=f"2026-08-25T12:00:0{position}Z",
            )
            for position, character in enumerate(("a", "b", "c"))
        ]
        records.append(
            observation(
                "invalid",
                published_at="2026-08-25T13:00:00Z",
            )
        )

        findings = prior_findings(records, reviewer="codex", maximum=2)

        self.assertEqual(
            [finding["finding_id"] for finding in findings],
            ["arf_v1_" + "c" * 26, "arf_v1_" + "b" * 26],
        )

    def test_verdict_order_uses_instants_and_deterministic_ties(self):
        finding_id = "arf_v1_" + "a" * 26
        records = [
            verdict(timestamp="2026-08-25T13:00:00+01:00"),
            verdict(outcome="accepted", timestamp="2026-08-25T12:00:00.1Z"),
        ]
        self.assertEqual(decisions(records), {finding_id: "accepted"})
        self.assertEqual(decisions(list(reversed(records))), decisions(records))

        records[0]["data"]["command_created_at"] = "2026-08-25T12:00:00.100Z"
        records[0]["record_id"] = "art_v1_" + "z" * 26
        records[1]["record_id"] = "art_v1_" + "a" * 26
        self.assertEqual(decisions(records), {finding_id: "deferred"})
        self.assertEqual(decisions(list(reversed(records))), decisions(records))

    def test_all_explicit_outcomes_are_preserved(self):
        for outcome in (
            "accepted", "already-fixed", "deferred", "false-positive", "out-of-scope",
        ):
            with self.subTest(outcome=outcome):
                record = verdict(outcome=outcome)
                self.assertEqual(decisions([record]), {record["data"]["finding_id"]: outcome})

    def test_other_reviewer_records_are_ignored(self):
        other = verdict(reviewer="claude")
        other["data"]["outcome"] = "invalid"
        self.assertEqual(decisions([other]), {})
        self.assertEqual(decisions([other, verdict()]), decisions([verdict()]))

    def test_malformed_relevant_verdicts_fail(self):
        mutations = [
            lambda r: r.update(record_type="finding_observed"),
            lambda r: r.update(record_id="invalid"),
            lambda r: r.update(record_id="arf_v1_" + "a" * 26),
            lambda r: r["repository"].update(full_name="aws/other"),
            lambda r: r["pull_request"].update(number=43),
            lambda r: r["data"].update(reviewer="unknown"),
            lambda r: r["data"].update(reviewer=[]),
            lambda r: r["data"].pop("reviewer"),
            lambda r: r["data"].update(finding_id="art_v1_" + "a" * 26),
            lambda r: r["data"].update(outcome="open"),
            lambda r: r["data"].update(outcome=[]),
            lambda r: r["data"].update(command_created_at="2026-08-25"),
            lambda r: r["data"].pop("command_created_at"),
            lambda r: r.update(data=None),
        ]
        for position, mutate in enumerate(mutations):
            record = copy.deepcopy(verdict())
            mutate(record)
            with self.subTest(position=position):
                with self.assertRaises(TelemetryError):
                    decisions([record])
        with self.assertRaises(TelemetryError):
            decisions([None])

    def test_decisions_survive_catalog_expiry_and_annotate_entries(self):
        start = datetime(2026, 8, 25, tzinfo=timezone.utc)
        records = []
        for position in range(101):
            finding_id, _ = stable_identifier("arf", {"position": position})
            records.append(
                observation(
                    finding_id,
                    published_at=(start + timedelta(seconds=position)).isoformat(),
                )
            )
        oldest_id = records[0]["data"]["finding_id"]
        newest_id = records[-1]["data"]["finding_id"]
        decision_map = decisions([verdict(oldest_id), verdict(newest_id)])
        catalog = prior_findings(records, reviewer="codex", decisions=decision_map)
        self.assertEqual(len(catalog), 100)
        self.assertNotIn(oldest_id, {item["finding_id"] for item in catalog})
        self.assertEqual(decision_map[oldest_id], "deferred")
        self.assertEqual(catalog[0]["status"], "deferred")
        self.assertTrue(all(item["status"] == "open" for item in catalog[1:]))

    def run_loader(self, store):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.json"
            decision_file = Path(directory) / "decisions.json"
            argv = [
                "load_ai_review_findings.py",
                "--repository", "aws/example",
                "--pull-request-number", "42",
                "--reviewer", "codex",
                "--output", str(catalog),
                "--decisions-output", str(decision_file),
            ]
            stderr = io.StringIO()
            with (
                patch.object(sys, "argv", argv),
                patch("load_ai_review_findings.TelemetryStore", return_value=store),
                redirect_stderr(stderr),
            ):
                status = main()
            return (
                status,
                json.loads(catalog.read_text()) if catalog.exists() else None,
                json.loads(decision_file.read_text()) if decision_file.exists() else None,
                stderr.getvalue(),
            )

    def test_cli_writes_both_views_from_one_verdict_read(self):
        record = verdict()
        store = Mock()
        store.list_json.side_effect = [
            [observation(record["data"]["finding_id"])], [record],
        ]
        status, catalog, decision_map, error = self.run_loader(store)
        self.assertEqual(status, 0, error)
        self.assertEqual(catalog[0]["status"], "deferred")
        self.assertEqual(decision_map, decisions([record]))
        self.assertEqual(store.list_json.call_count, 2)
        self.assertTrue(all(call.kwargs["maximum"] == 2000 for call in store.list_json.call_args_list))

    def test_missing_branch_writes_empty_outputs(self):
        api = Mock()
        api.request.side_effect = GitHubApiError("missing branch", 404)
        status, catalog, decision_map, error = self.run_loader(
            TelemetryStore("aws/example", api=api)
        )
        self.assertEqual((status, catalog, decision_map), (0, [], {}), error)

    def test_read_and_validation_errors_never_write_empty_outputs(self):
        for failure in (GitHubApiError("unavailable", 503), TelemetryError("bad blob")):
            store = Mock()
            store.list_json.side_effect = [[], failure]
            result = self.run_loader(store)
            self.assertEqual(result[:3], (1, None, None))
            self.assertIn(str(failure), result[3])
        store = Mock()
        store.list_json.side_effect = [[], [verdict(outcome="invalid")]]
        self.assertEqual(self.run_loader(store)[:3], (1, None, None))

    def test_capacity_errors_for_either_prefix_stop_loading(self):
        for record_type in ("finding_observed", "finding_verdict"):
            entries = {
                f"events/pr-42/{record_type}/{position}.json": "3" * 40
                for position in range(2001)
            }
            store = TelemetryStore("aws/example", api=Mock())
            with patch.object(
                store, "_load_tree", return_value=GitTree("1" * 40, "2" * 40, entries),
            ):
                result = self.run_loader(store)
            self.assertEqual(result[:3], (1, None, None))
            self.assertIn("more than 2000", result[3])


if __name__ == "__main__":
    unittest.main()
