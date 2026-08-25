#!/usr/bin/env python3

import json
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
REVIEW_SCHEMA = json.loads(
    (REPO_ROOT / ".github/prompts/ai-pr-review-schema.json").read_text(
        encoding="utf-8"
    )
)
REVIEW_OUTPUT = (
    REPO_ROOT / ".github/prompts/ai-pr-review-output.md"
).read_text(encoding="utf-8")


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
            "environment-name": "ai-pr-review-runtime",
            "claude-model": "us.anthropic.claude-sonnet-5",
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
            "review-guidance-base64: >-\n"
            "        ${{ needs.resolve_review.outputs."
            "review-guidance-base64 }}",
            claude,
        )
        self.assertIn(
            "trigger-metadata-base64: >-\n"
            "        ${{ needs.resolve_review.outputs."
            "trigger-metadata-base64 }}",
            claude,
        )
        self.assertIn(
            "environment-name: >-\n"
            "        ${{ inputs['environment-name'] || "
            "'ai-pr-review-runtime' }}",
            claude,
        )
        self.assertIn(
            "model: ${{ inputs['claude-model'] || "
            "'us.anthropic.claude-sonnet-5' }}",
            claude,
        )
        self.assertIn(
            "reasoning-effort: ${{ "
            "inputs['claude-reasoning-effort'] || 'xhigh' }}",
            claude,
        )

        codex = job_block(AI_WORKFLOW, "codex-review")
        self.assertIn(
            "review-guidance-base64: >-\n"
            "        ${{ needs.resolve_review.outputs."
            "review-guidance-base64 }}",
            codex,
        )
        self.assertIn(
            "trigger-metadata-base64: >-\n"
            "        ${{ needs.resolve_review.outputs."
            "trigger-metadata-base64 }}",
            codex,
        )
        self.assertIn(
            "environment-name: >-\n"
            "        ${{ inputs['environment-name'] || "
            "'ai-pr-review-runtime' }}",
            codex,
        )
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
            "review-guidance-base64",
            '""',
        )
        self.assert_input_default(
            CLAUDE_WORKFLOW,
            "trigger-metadata-base64",
            '""',
        )
        self.assert_input_default(
            CLAUDE_WORKFLOW,
            "environment-name",
            "ai-pr-review-runtime",
        )
        self.assert_input_default(
            CLAUDE_WORKFLOW,
            "model",
            "us.anthropic.claude-sonnet-5",
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

    def test_claude_workflow_uses_minimal_read_only_tooling(self):
        generate = job_block(CLAUDE_WORKFLOW, "generate")

        self.assertEqual(generate.count("--bare"), 1)
        self.assertEqual(generate.count('--tools "Read,Grep,Glob"'), 1)
        self.assertEqual(generate.count('--allowedTools "Read,Grep,Glob"'), 1)

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
            "review-guidance-base64",
            '""',
        )
        self.assert_input_default(
            CODEX_WORKFLOW,
            "trigger-metadata-base64",
            '""',
        )
        self.assert_input_default(
            CODEX_WORKFLOW,
            "environment-name",
            "ai-pr-review-runtime",
        )
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

    def test_reviewer_jobs_use_configurable_runtime_environment(self):
        expected = (
            "environment: >-\n"
            "      ${{ inputs['environment-name'] || "
            "'ai-pr-review-runtime' }}"
        )
        self.assertIn(expected, job_block(CLAUDE_WORKFLOW, "generate"))
        self.assertIn(expected, job_block(CODEX_WORKFLOW, "generate"))

    def test_issue_comment_command_is_resolved_before_review(self):
        resolve = job_block(AI_WORKFLOW, "resolve_review")

        self.assertIn("issue_comment:", AI_WORKFLOW)
        self.assertIn("types: [created]", AI_WORKFLOW)
        self.assertIn(
            "contains(github.event.comment.body, '/ai')",
            resolve,
        )
        self.assertIn(
            "contains(github.event.comment.body, 'review')",
            resolve,
        )
        self.assertIn(
            "python3 .ai-review-toolkit/scripts/resolve_ai_review.py",
            resolve,
        )
        self.assertIn("contents: read", resolve)
        self.assertIn("pull-requests: read", resolve)
        self.assertIn(
            "review-guidance-base64: >-\n"
            "        ${{ steps.resolve.outputs.review-guidance-base64 }}",
            resolve,
        )
        self.assertIn(
            "trigger-metadata-base64: >-\n"
            "        ${{ steps.resolve.outputs.trigger-metadata-base64 }}",
            resolve,
        )

    def test_authorized_guidance_is_appended_to_both_review_prompts(self):
        for reviewer, workflow in (
            ("claude", CLAUDE_WORKFLOW),
            ("codex", CODEX_WORKFLOW),
        ):
            with self.subTest(reviewer=reviewer):
                generate = job_block(workflow, "generate")
                self.assertIn(
                    "REVIEW_GUIDANCE_BASE64: "
                    "${{ inputs['review-guidance-base64'] }}",
                    generate,
                )
                self.assertIn(
                    '"$REVIEW_GUIDANCE_BASE64"',
                    generate,
                )

    def test_claude_prompt_output_uses_an_unpredictable_delimiter(self):
        generate = job_block(CLAUDE_WORKFLOW, "generate")

        self.assertIn(
            'delimiter="AI_REVIEW_PROMPT_'
            '$(cat /proc/sys/kernel/random/uuid)"',
            generate,
        )
        self.assertIn('echo "prompt<<$delimiter"', generate)
        self.assertIn('echo "$delimiter"', generate)

    def test_only_resolved_reviews_enter_pr_scoped_concurrency(self):
        entry_header = AI_WORKFLOW.split("jobs:", 1)[0]
        self.assertNotIn("concurrency:", entry_header)

        expected_groups = {
            "claude": (
                "ai-pr-review-claude-${{ github.repository_id }}-${{\n"
                "      inputs['pull-request-number']\n"
                "    }}"
            ),
            "codex": (
                "ai-pr-review-codex-${{ github.repository_id }}-${{\n"
                "      inputs['pull-request-number']\n"
                "    }}"
            ),
        }
        for reviewer, workflow in (
            ("claude", CLAUDE_WORKFLOW),
            ("codex", CODEX_WORKFLOW),
        ):
            with self.subTest(reviewer=reviewer):
                header = workflow.split("jobs:", 1)[0]
                self.assertIn(expected_groups[reviewer], header)
                self.assertIn("cancel-in-progress: true", header)
                self.assertNotIn("github.actor", header)
                self.assertNotIn("github.event", header)

    def test_reviewers_use_resolved_pull_request_identity(self):
        for name, workflow in (
            ("claude-review", CLAUDE_WORKFLOW),
            ("codex-review", CODEX_WORKFLOW),
        ):
            with self.subTest(reviewer=name):
                reviewer = job_block(AI_WORKFLOW, name)
                self.assertIn(
                    "needs: resolve_review",
                    reviewer,
                )
                self.assertNotIn("approve_review", reviewer)
                self.assertIn(
                    "pull-request-number: >-",
                    reviewer,
                )
                self.assertIn(
                    "${{ needs.resolve_review.outputs.base-sha }}",
                    reviewer,
                )
                self.assertIn(
                    "${{ needs.resolve_review.outputs.head-sha }}",
                    reviewer,
                )
                for input_name in (
                    "pull-request-number",
                    "base-sha",
                    "head-sha",
                ):
                    self.assertIn(
                        "required: true",
                        input_block(workflow, input_name),
                    )
                self.assertNotIn("github.event.pull_request", workflow)

    def test_workflow_has_no_manual_approval_job_or_environment(self):
        self.assertNotIn("approve_review:", AI_WORKFLOW)
        self.assertNotIn("environment: ai-pr-review\n", AI_WORKFLOW)
        self.assertNotIn("approval-required", AI_WORKFLOW)

    def test_each_reviewer_job_has_its_own_guard(self):
        self.assertIn(
            enabled_guard("run-claude"),
            job_block(AI_WORKFLOW, "claude-review"),
        )
        self.assertIn(
            enabled_guard("run-codex"),
            job_block(AI_WORKFLOW, "codex-review"),
        )

    def test_activity_job_records_human_feedback_and_verdict_events(self):
        activity = job_block(AI_WORKFLOW, "record_activity")

        self.assertIn("pull_request_review:", AI_WORKFLOW)
        self.assertIn("pull_request_review_comment:", AI_WORKFLOW)
        self.assertIn("contents: write", activity)
        self.assertIn("pull-requests: write", activity)
        self.assertIn(
            "python3 .ai-review-toolkit/scripts/record_ai_review_activity.py",
            activity,
        )
        self.assertNotIn("id-token: write", activity)

    def test_only_trusted_non_model_jobs_write_telemetry(self):
        for workflow in (CLAUDE_WORKFLOW, CODEX_WORKFLOW):
            with self.subTest(workflow=workflow.splitlines()[0]):
                generate = job_block(workflow, "generate")
                post = job_block(workflow, "post")

                self.assertIn("contents: read", generate)
                self.assertNotIn("contents: write", generate)
                self.assertIn("id-token: write", generate)
                self.assertIn("contents: write", post)
                self.assertNotIn("id-token: write", post)
                self.assertIn(
                    "TRIGGER_METADATA_BASE64: "
                    "${{ inputs['trigger-metadata-base64'] }}",
                    post,
                )

    def test_review_contract_requires_stable_finding_correlation(self):
        finding_schema = REVIEW_SCHEMA["properties"]["comments"]["items"]

        self.assertIn("finding_key", finding_schema["required"])
        self.assertIn("prior_finding_id", finding_schema["required"])
        self.assertEqual(
            finding_schema["properties"]["prior_finding_id"]["pattern"],
            "^(|arf_v1_[a-z2-7]{26})$",
        )
        self.assertIn(
            ".ai-review-context/prior-findings.json",
            REVIEW_OUTPUT,
        )
        self.assertIn(
            "Do not include a",
            REVIEW_OUTPUT,
        )
        self.assertIn(
            "line number, commit SHA, run ID, severity",
            REVIEW_OUTPUT,
        )


if __name__ == "__main__":
    unittest.main()
