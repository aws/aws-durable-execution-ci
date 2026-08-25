#!/usr/bin/env python3

import copy
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from prepare_ai_review_comments import ReviewValidationError, prepare_review


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


def prepare(comments, reviewer="claude"):
    return prepare_review(
        {"summary": "One actionable finding.", "comments": comments},
        PR_FILES,
        reviewer,
        "123",
        "2",
        HEAD_SHA,
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

        self.assertTrue(prepared["comments"][0]["body"].endswith("```suggestion\n\n```"))

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

        for label, comment in invalid_comments.items():
            with self.subTest(label=label):
                with self.assertRaises(ReviewValidationError):
                    prepare([comment])

    def test_rejects_duplicate_and_extra_fields(self):
        duplicate = finding()
        with self.assertRaises(ReviewValidationError):
            prepare([duplicate, copy.deepcopy(duplicate)])

        extra = finding()
        extra["severity"] = "P1"
        with self.assertRaises(ReviewValidationError):
            prepare([extra])

    def test_rejects_invalid_top_level_shape(self):
        with self.assertRaises(ReviewValidationError):
            prepare_review(
                {"summary": "summary", "comments": [], "extra": True},
                PR_FILES,
                "codex",
                "123",
                "1",
                HEAD_SHA,
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
            prior_findings=[prior],
        )

        repeated = prepared["telemetry"]["findings"][0]
        self.assertEqual(repeated["finding_id"], prior["finding_id"])
        self.assertEqual(repeated["identity_key"], prior["finding_key"])
        self.assertNotEqual(repeated["finding_key"], repeated["identity_key"])

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
            )


if __name__ == "__main__":
    unittest.main()
