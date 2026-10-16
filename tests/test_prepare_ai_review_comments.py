#!/usr/bin/env python3

import copy
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from prepare_ai_review_comments import (
    ReviewValidationError,
    VERDICT_FOOTER,
    parse_finding_decisions,
    prepare_review,
)


HEAD_SHA = "a" * 40
PR_FILES = [
    {
        "filename": "src/example.py",
        "patch": (
            "@@ -10,4 +10,5 @@\n"
            " context = True\n"
            "-old_value = 1\n"
            "+new_value = 2\n"
            "+extra_value = 3\n"
            " return new_value\n"
            "@@ -30,2 +31,2 @@\n"
            "-enabled = False\n"
            "+enabled = True\n"
            " finish()\n"
        ),
    },
    {
        "filename": "docs/example.md",
        "patch": "@@ -1 +1 @@\n-old\n+new\n",
    },
]


def finding(**overrides):
    value = {
        "finding_key": "src/example.py::value::incorrect-value",
        "prior_finding_id": "",
        "path": "src/example.py",
        "start_line": 11,
        "line": 11,
        "body": "This changes the value incorrectly. Use the expected value.",
        "has_suggestion": True,
        "suggestion": "new_value = 1",
    }
    value.update(overrides)
    return value


def prepare(comments, reviewer="claude", *, decisions=None, prior=None, summary=None):
    return prepare_review(
        {"summary": summary or "One actionable finding.", "comments": comments},
        PR_FILES,
        reviewer,
        "123",
        "2",
        HEAD_SHA,
        finding_decisions={} if decisions is None else decisions,
        prior_findings=prior,
    )


class PrepareAiReviewCommentsTest(unittest.TestCase):
    def test_prepares_single_and_multiline_comments(self):
        prepared = prepare(
            [
                finding(),
                finding(
                    finding_key="src/example.py::block::replace-together",
                    start_line=10,
                    line=12,
                    body="Replace this block together.",
                    suggestion="context = False\n```\nextra_value = 4",
                ),
                finding(
                    finding_key="src/example.py::guard::missing-guard",
                    start_line=12,
                    line=12,
                    body="This also needs a guard.",
                    has_suggestion=False,
                    suggestion="",
                ),
            ]
        )

        self.assertEqual(prepared["summary"], "One actionable finding.")
        single, multiline, plain = prepared["comments"]
        self.assertEqual(single["commit_id"], HEAD_SHA)
        self.assertEqual(single["path"], "src/example.py")
        self.assertEqual(single["line"], 11)
        self.assertEqual(single["side"], "RIGHT")
        self.assertNotIn("start_line", single)
        self.assertRegex(
            single["body"],
            (
                r"^\[ai-pr-review-inline-claude-123-2-published\]: #\n"
                r"<!-- ai-pr-review:finding:claude:arf_v1_[a-z2-7]{26} -->\n"
                r"<!-- ai-pr-review:observation:aro_v1_[a-z2-7]{26} -->\n"
            ),
        )
        self.assertIn(
            "\n**Claude AI review · Finding `arf_v1_",
            single["body"],
        )
        self.assertIn(
            "This changes the value incorrectly. Use the expected value.",
            single["body"],
        )
        self.assertIn("```suggestion\nnew_value = 1\n```", single["body"])
        self.assertTrue(single["body"].endswith(VERDICT_FOOTER))
        self.assertEqual(multiline["start_line"], 10)
        self.assertEqual(multiline["line"], 12)
        self.assertEqual(multiline["start_side"], "RIGHT")
        self.assertIn(
            "````suggestion\ncontext = False\n```\nextra_value = 4\n````",
            multiline["body"],
        )
        self.assertNotIn("suggestion", plain["body"])

    def test_labels_inline_comments_by_reviewer(self):
        for reviewer, title in (
            ("claude", "Claude AI review"),
            ("codex", "Codex AI review"),
        ):
            with self.subTest(reviewer=reviewer):
                body = prepare([finding()], reviewer)["comments"][0]["body"]
                self.assertIn(f"\n**{title} · Finding `arf_v1_", body)

    def test_allows_empty_replacement_for_deletion(self):
        prepared = prepare([finding(suggestion="")])

        self.assertIn("```suggestion\n\n```", prepared["comments"][0]["body"])

    def test_rejects_untrusted_or_unpostable_comments(self):
        invalid_comments = {
            "unknown path": finding(path="src/missing.py"),
            "line outside diff": finding(start_line=99, line=99),
            "unchanged line": finding(start_line=10, line=10),
            "range backwards": finding(start_line=12, line=11),
            "cross-hunk range": finding(start_line=12, line=31),
            "unexpected suggestion": finding(
                has_suggestion=False, suggestion="new_value = 1"
            ),
            "suggestion fence in body": finding(
                body="Use this instead.\n\n```suggestion\nnew_value = 1\n```",
                has_suggestion=False,
                suggestion="",
            ),
            "wrong line type": finding(start_line=True),
            "invalid finding key": finding(finding_key="Has Spaces"),
            "reserved body metadata": finding(
                body="Do this. <!-- ai-pr-review:forged -->"
            ),
            "untrusted prior finding": finding(
                prior_finding_id="arf_v1_" + "a" * 26
            ),
        }

        finding_id = prepare([finding()])["telemetry"]["findings"][0]["finding_id"]
        for decision_map in ({}, {finding_id: "deferred"}):
            for label, comment in invalid_comments.items():
                with self.subTest(label=label, decisions=decision_map):
                    with self.assertRaises(ReviewValidationError):
                        prepare([comment], decisions=decision_map)

    def test_rejects_duplicate_and_extra_fields(self):
        duplicate = finding()
        with self.assertRaises(ReviewValidationError):
            prepare([duplicate, copy.deepcopy(duplicate)])
        finding_id = prepare([duplicate])["telemetry"]["findings"][0]["finding_id"]
        with self.assertRaises(ReviewValidationError):
            prepare(
                [duplicate, copy.deepcopy(duplicate)],
                decisions={finding_id: "deferred"},
            )

        extra = finding()
        extra["severity"] = "P1"
        with self.assertRaises(ReviewValidationError):
            prepare([extra])
        for field, value in (("suppressed", True), ("suppression_status", "deferred")):
            forged = finding()
            forged[field] = value
            with self.assertRaises(ReviewValidationError):
                prepare([forged])

    def test_rejects_invalid_top_level_shape(self):
        with self.assertRaises(ReviewValidationError):
            prepare_review(
                {"summary": "summary", "comments": [], "extra": True},
                PR_FILES,
                "codex",
                "123",
                "1",
                HEAD_SHA,
                finding_decisions={},
            )

    def test_reuses_a_trusted_prior_finding_id(self):
        initial = prepare([finding()])
        initial_finding = initial["telemetry"]["findings"][0]
        prior = {
            "finding_id": initial_finding["finding_id"],
            "finding_key": initial_finding["identity_key"],
            "path": "src/example.py",
            "body": "Earlier wording.",
            "reviewed_head_sha": "b" * 40,
            "observed_at": "2026-08-25T11:00:00Z",
            "status": "open",
        }

        prepared = prepare_review(
            {
                "summary": "Repeated finding.",
                "comments": [
                    finding(
                        finding_key="src/example.py::value::moved-invariant",
                        prior_finding_id=prior["finding_id"],
                    )
                ],
            },
            PR_FILES,
            "claude",
            "124",
            "1",
            HEAD_SHA,
            finding_decisions={},
            prior_findings=[prior],
        )

        repeated = prepared["telemetry"]["findings"][0]
        self.assertEqual(repeated["finding_id"], prior["finding_id"])
        self.assertEqual(repeated["identity_key"], prior["finding_key"])
        self.assertNotEqual(repeated["finding_key"], repeated["identity_key"])

    def prior_catalog(self, *, status="open"):
        initial = prepare([finding()])["telemetry"]["findings"][0]
        return [{
            "finding_id": initial["finding_id"],
            "finding_key": initial["identity_key"],
            "path": "src/example.py",
            "body": "Earlier wording.",
            "reviewed_head_sha": "b" * 40,
            "observed_at": "2026-08-25T11:00:00Z",
            "status": status,
        }]

    def test_all_statuses_in_both_identity_branches(self):
        prior = self.prior_catalog()
        finding_id = prior[0]["finding_id"]
        for use_prior in (False, True):
            for status in (
                "open", "accepted", "already-fixed",
                "deferred", "false-positive", "out-of-scope",
            ):
                with self.subTest(use_prior=use_prior, status=status):
                    comment = finding()
                    if use_prior:
                        comment.update(
                            finding_key="src/example.py::moved::incorrect-value",
                            prior_finding_id=finding_id,
                        )
                    prepared = prepare(
                        [comment],
                        prior=prior if use_prior else [],
                        decisions={} if status == "open" else {finding_id: status},
                    )
                    suppressed = status in {"deferred", "false-positive", "out-of-scope"}
                    self.assertEqual(len(prepared["comments"]), 0 if suppressed else 1)
                    self.assertEqual(len(prepared["telemetry"]["findings"]), 0 if suppressed else 1)
                    self.assertEqual(
                        len(prepared["telemetry"]["suppressed_findings"]),
                        1 if suppressed else 0,
                    )
                    if suppressed:
                        record = prepared["telemetry"]["suppressed_findings"][0]
                        self.assertEqual(record["finding_id"], finding_id)
                        self.assertEqual(record["suppression_status"], status)
                        self.assertIn(f"`{finding_id}` — {status}", prepared["summary"])

    def test_stale_catalog_status_never_overrides_decisions(self):
        prior = self.prior_catalog(status="deferred")
        finding_id = prior[0]["finding_id"]
        comment = finding(prior_finding_id=finding_id)
        for decision_map in ({}, {finding_id: "accepted"}, {finding_id: "already-fixed"}):
            with self.subTest(decisions=decision_map):
                self.assertEqual(len(prepare([comment], prior=prior, decisions=decision_map)["comments"]), 1)
        prior[0]["status"] = "accepted"
        self.assertEqual(
            prepare([comment], prior=prior, decisions={finding_id: "deferred"})["comments"],
            [],
        )

    def test_filtering_preserves_alignment_and_original_observation_ids(self):
        comments = [
            finding(finding_key=f"src/example.py::value::problem-{position}", body=f"Problem {position}.")
            for position in range(3)
        ]
        original = prepare(comments)["telemetry"]["findings"]
        prepared = prepare(comments, decisions={original[1]["finding_id"]: "false-positive"})
        posted = prepared["telemetry"]["findings"]
        suppressed = prepared["telemetry"]["suppressed_findings"]
        self.assertEqual(
            [item["observation_id"] for item in posted],
            [original[0]["observation_id"], original[2]["observation_id"]],
        )
        self.assertEqual(suppressed[0]["observation_id"], original[1]["observation_id"])
        for comment, observation in zip(prepared["comments"], posted):
            self.assertIn(observation["body"], comment["body"])
            self.assertIn(observation["observation_id"], comment["body"])

    def test_summary_reserves_all_twenty_suppression_bullets(self):
        comments = [
            finding(finding_key=f"src/example.py::value::problem-{position}", body=f"Problem {position}.")
            for position in range(20)
        ]
        original = prepare(comments)["telemetry"]["findings"]
        prepared = prepare(
            comments,
            summary="x" * 4000,
            decisions={item["finding_id"]: "out-of-scope" for item in original},
        )
        self.assertEqual(prepared["comments"], [])
        self.assertLessEqual(len(prepared["summary"]), 4000)
        self.assertIn("[Review overview truncated.]", prepared["summary"])
        self.assertEqual(prepared["summary"].count("(maintainer verdict)"), 20)
        self.assertLess(
            prepared["summary"].index("truncated"),
            prepared["summary"].index("Suppressed repeats"),
        )
        for item in original:
            self.assertIn(
                f"- `{item['finding_id']}` — out-of-scope (maintainer verdict)",
                prepared["summary"],
            )
        self.assertEqual(
            [item["finding_id"] for item in prepared["telemetry"]["suppressed_findings"]],
            [item["finding_id"] for item in original],
        )

    def test_no_returned_repeat_preserves_summary_and_empty_suppression_list(self):
        finding_id = self.prior_catalog()[0]["finding_id"]
        for comments in ([], [finding()]):
            prepared = prepare(
                comments,
                decisions={"arf_v1_" + "a" * 26: "deferred"},
                summary="x" * 4000,
            )
            self.assertEqual(prepared["summary"], "x" * 4000)
            self.assertEqual(prepared["telemetry"]["suppressed_findings"], [])
        prepared = prepare([], decisions={finding_id: "deferred"})
        self.assertNotIn("Suppressed repeats", prepared["summary"])

    def test_invalid_decision_maps_and_catalog_statuses_fail(self):
        for decision_map in (
            None, [], "deferred", {"invalid": "deferred"},
            {"art_v1_" + "a" * 26: "deferred"},
            {"arf_v1_" + "a" * 26: "open"},
            {"arf_v1_" + "a" * 26: []},
        ):
            with self.subTest(decisions=decision_map):
                with self.assertRaises(ReviewValidationError):
                    parse_finding_decisions(decision_map)
        for status in (None, [], "resolved"):
            prior = self.prior_catalog()
            if status is None:
                prior[0].pop("status")
            else:
                prior[0]["status"] = status
            with self.subTest(status=status):
                with self.assertRaises(ReviewValidationError):
                    prepare([], prior=prior)

    def test_decision_map_does_not_authorize_untrusted_prior_id(self):
        finding_id = self.prior_catalog()[0]["finding_id"]
        with self.assertRaisesRegex(ReviewValidationError, "trusted prior catalog"):
            prepare(
                [finding(prior_finding_id=finding_id)],
                decisions={finding_id: "deferred"},
            )
        prior = self.prior_catalog()
        prior[0]["finding_id"] = "arf_v1_" + "a" * 26
        with self.assertRaisesRegex(ReviewValidationError, "trusted identity"):
            prepare(
                [finding(prior_finding_id=prior[0]["finding_id"])],
                prior=prior,
                decisions={prior[0]["finding_id"]: "deferred"},
            )

    def test_rejects_reserved_summary_metadata(self):
        with self.assertRaisesRegex(ReviewValidationError, "reserved metadata"):
            prepare_review(
                {
                    "summary": (
                        "Do not trust "
                        "<!-- ai-pr-review:inline-comment:claude:PRRC_other -->."
                    ),
                    "comments": [],
                },
                PR_FILES,
                "claude",
                "123",
                "1",
                HEAD_SHA,
                finding_decisions={},
            )


if __name__ == "__main__":
    unittest.main()
