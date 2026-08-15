#!/usr/bin/env python3

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AI_WORKFLOW = (REPO_ROOT / ".github/workflows/ai-pr-review.yml").read_text(
    encoding="utf-8"
)
CLAUDE_WORKFLOW = (REPO_ROOT / ".github/workflows/claude-review.yml").read_text(
    encoding="utf-8"
)
CODEX_WORKFLOW = (REPO_ROOT / ".github/workflows/codex-review.yml").read_text(
    encoding="utf-8"
)


def job_block(workflow: str, job_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job_name)}:\n(.*?)(?=^  [a-z0-9_-]+:\n|\Z)",
        workflow,
    )
    if match is None:
        raise AssertionError(f"workflow job {job_name!r} was not found")
    return match.group(1)


def input_block(workflow: str, input_name: str) -> str:
    match = re.search(
        rf"(?ms)^      {re.escape(input_name)}:\n"
        r"(.*?)(?=^      [a-zA-Z0-9_-]+:\n|^    [a-z][a-z0-9_-]*:\n|\Z)",
        workflow,
    )
    if match is None:
        raise AssertionError(f"workflow input {input_name!r} was not found")
    return match.group(1)


def enabled_guard(input_name: str) -> str:
    return f"format('{{0}}', inputs['{input_name}']) != 'false'"


class AiPrReviewWorkflowTest(unittest.TestCase):
    def assert_input_default(
        self,
        workflow: str,
        input_name: str,
        expected_default: str,
    ):
        self.assertIn(
            f"default: {expected_default}",
            input_block(workflow, input_name),
        )

    def test_public_reviewer_configuration_defaults(self):
        expected_defaults = {
            "claude-model": "us.anthropic.claude-opus-4-8",
            "claude-reasoning-effort": "xhigh",
            "codex-model": "openai.gpt-5.6-sol",
            "codex-reasoning-effort": "xhigh",
        }

        for input_name, expected_default in expected_defaults.items():
            with self.subTest(input_name=input_name):
                self.assert_input_default(
                    AI_WORKFLOW,
                    input_name,
                    expected_default,
                )

    def test_reviewer_configuration_is_forwarded(self):
        claude = job_block(AI_WORKFLOW, "claude-review")
        self.assertIn(
            "model: ${{ inputs['claude-model'] || "
            "'us.anthropic.claude-opus-4-8' }}",
            claude,
        )
        self.assertIn(
            "reasoning-effort: ${{ "
            "inputs['claude-reasoning-effort'] || 'xhigh' }}",
            claude,
        )

        codex = job_block(AI_WORKFLOW, "codex-review")
        self.assertIn(
            "model: ${{ inputs['codex-model'] || 'openai.gpt-5.6-sol' }}",
            codex,
        )
        self.assertIn(
            "reasoning-effort: ${{ "
            "inputs['codex-reasoning-effort'] || 'xhigh' }}",
            codex,
        )

    def test_claude_workflow_uses_model_and_reasoning_inputs(self):
        self.assert_input_default(
            CLAUDE_WORKFLOW,
            "model",
            "us.anthropic.claude-opus-4-8",
        )
        self.assert_input_default(CLAUDE_WORKFLOW, "reasoning-effort", "xhigh")
        self.assertEqual(
            CLAUDE_WORKFLOW.count(
                "--model ${{ steps.review-config.outputs.model }}"
            ),
            1,
        )
        self.assertEqual(
            CLAUDE_WORKFLOW.count(
                "--effort ${{ steps.review-config.outputs.reasoning_effort }}"
            ),
            1,
        )

    def test_claude_workflow_runs_once_with_30_minute_timeout(self):
        generate = job_block(CLAUDE_WORKFLOW, "generate")

        self.assertIn("timeout-minutes: 30", generate)
        self.assertNotIn("timeout-minutes: 45", generate)
        self.assertNotIn("review-retry", generate)
        self.assertNotIn("continue-on-error: true", generate)
        self.assertIn(
            "CLAUDE_EXECUTION_FILE: ${{ steps.review.outputs.execution_file }}",
            generate,
        )

    def test_codex_workflow_uses_model_and_reasoning_inputs(self):
        self.assert_input_default(
            CODEX_WORKFLOW,
            "model",
            "openai.gpt-5.6-sol",
        )
        self.assert_input_default(CODEX_WORKFLOW, "reasoning-effort", "xhigh")
        self.assertIn('--model "$CODEX_MODEL"', CODEX_WORKFLOW)
        self.assertIn(
            '--config "model_reasoning_effort='
            '\\"${CODEX_REASONING_EFFORT}\\""',
            CODEX_WORKFLOW,
        )

    def test_approval_requires_at_least_one_enabled_reviewer(self):
        approval = job_block(AI_WORKFLOW, "approve_review")

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
            job_block(AI_WORKFLOW, "claude-review"),
        )
        self.assertIn(
            enabled_guard("run-codex"),
            job_block(AI_WORKFLOW, "codex-review"),
        )


if __name__ == "__main__":
    unittest.main()
