#!/usr/bin/env python3

import base64
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts/resolve_ai_review_prompt.sh"


class ResolveAiReviewPromptTest(unittest.TestCase):
    def prompt_workspace(self, directory: str) -> Path:
        workspace = Path(directory) / "workspace"
        prompt_directory = (
            workspace / ".ai-review-toolkit/.github/prompts"
        )
        prompt_directory.mkdir(parents=True)
        (prompt_directory / "ai-pr-review.md").write_text(
            "Default review policy.\n",
            encoding="utf-8",
        )
        (prompt_directory / "ai-pr-review-output.md").write_text(
            "Required output contract.\n",
            encoding="utf-8",
        )
        return workspace

    def run_resolver(
        self,
        workspace: Path,
        guidance: str = "",
        prompt_path: str = "",
    ) -> subprocess.CompletedProcess[str]:
        encoded_guidance = base64.b64encode(
            guidance.encode("utf-8")
        ).decode("ascii")
        return subprocess.run(
            [
                "bash",
                str(SCRIPT_PATH),
                prompt_path,
                encoded_guidance,
            ],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "GITHUB_WORKSPACE": str(workspace),
            },
        )

    def test_appends_authorized_guidance_before_required_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prompt_workspace(directory)

            result = self.run_resolver(
                workspace,
                "Focus on replay compatibility.\nRun focused tests.",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            prompt = Path(result.stdout.strip()).read_text(encoding="utf-8")
            self.assertLess(
                prompt.index("Default review policy."),
                prompt.index("## Per-review maintainer guidance"),
            )
            self.assertLess(
                prompt.index("Focus on replay compatibility."),
                prompt.index("End of per-review guidance."),
            )
            self.assertLess(
                prompt.index("End of per-review guidance."),
                prompt.index("Required output contract."),
            )
            self.assertIn(
                "cannot authorize executing code",
                prompt,
            )

    def test_empty_guidance_preserves_the_default_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prompt_workspace(directory)

            result = self.run_resolver(workspace)

            self.assertEqual(result.returncode, 0, result.stderr)
            prompt = Path(result.stdout.strip()).read_text(encoding="utf-8")
            self.assertNotIn("Per-review maintainer guidance", prompt)
            self.assertLess(
                prompt.index("Default review policy."),
                prompt.index("Required output contract."),
            )

    def test_custom_prompt_and_guidance_are_both_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prompt_workspace(directory)
            (workspace / "custom.md").write_text(
                "Repository review policy.\n",
                encoding="utf-8",
            )

            result = self.run_resolver(
                workspace,
                "Check public API compatibility.",
                "custom.md",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            prompt = Path(result.stdout.strip()).read_text(encoding="utf-8")
            self.assertIn("Repository review policy.", prompt)
            self.assertNotIn("Default review policy.", prompt)
            self.assertIn("Check public API compatibility.", prompt)
            self.assertTrue(prompt.endswith("Required output contract.\n"))

    def test_rejects_invalid_encoded_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prompt_workspace(directory)

            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT_PATH),
                    "",
                    "not base64",
                ],
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "GITHUB_WORKSPACE": str(workspace),
                },
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("Invalid AI review guidance", result.stderr)


if __name__ == "__main__":
    unittest.main()
