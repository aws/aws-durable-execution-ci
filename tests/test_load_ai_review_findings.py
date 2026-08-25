#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from load_ai_review_findings import prior_findings


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


if __name__ == "__main__":
    unittest.main()
