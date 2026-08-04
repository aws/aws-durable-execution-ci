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
        "path": "src/example.py",
        "start_line": 11,
        "line": 11,
        "body": "This changes the value incorrectly. Use the expected value.",
        "has_suggestion": True,
        "suggestion": "new_value = 1",
    }
    value.update(overrides)
    return value


def prepare(comments):
    return prepare_review(
        {"summary": "One actionable finding.", "comments": comments},
        PR_FILES,
        "claude",
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
                    start_line=10,
                    line=12,
                    body="Replace this block together.",
                    suggestion="context = False\n```\nextra_value = 4",
                ),
                finding(
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
        self.assertEqual(
            single,
            {
                "body": (
                    "[ai-pr-review-inline-claude-123-2-published]: #\n"
                    "This changes the value incorrectly. Use the expected value.\n\n"
                    "```suggestion\nnew_value = 1\n```"
                ),
                "commit_id": HEAD_SHA,
                "path": "src/example.py",
                "line": 11,
                "side": "RIGHT",
            },
        )
        self.assertEqual(multiline["start_line"], 10)
        self.assertEqual(multiline["line"], 12)
        self.assertEqual(multiline["start_side"], "RIGHT")
        self.assertIn(
            "````suggestion\ncontext = False\n```\nextra_value = 4\n````",
            multiline["body"],
        )
        self.assertNotIn("suggestion", plain["body"])

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
