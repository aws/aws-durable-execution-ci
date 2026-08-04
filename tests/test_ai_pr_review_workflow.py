#!/usr/bin/env python3

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (REPO_ROOT / ".github/workflows/ai-pr-review.yml").read_text(
    encoding="utf-8"
)


def job_block(job_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job_name)}:\n(.*?)(?=^  [a-z0-9_-]+:\n|\Z)",
        WORKFLOW,
    )
    if match is None:
        raise AssertionError(f"workflow job {job_name!r} was not found")
    return match.group(1)


def enabled_guard(input_name: str) -> str:
    return f"format('{{0}}', inputs['{input_name}']) != 'false'"


class AiPrReviewWorkflowTest(unittest.TestCase):
    def test_approval_requires_at_least_one_enabled_reviewer(self):
        approval = job_block("approve_review")

        self.assertIn(enabled_guard("run-claude"), approval)
        self.assertIn(enabled_guard("run-codex"), approval)
        self.assertRegex(
            approval,
            re.compile(
                re.escape(enabled_guard("run-claude"))
                + r"\s+\|\|\s+"
                + re.escape(enabled_guard("run-codex"))
            ),
        )

    def test_each_reviewer_job_has_its_own_guard(self):
        self.assertIn(
            enabled_guard("run-claude"),
            job_block("claude-review"),
        )
        self.assertIn(
            enabled_guard("run-codex"),
            job_block("codex-review"),
        )


if __name__ == "__main__":
    unittest.main()
