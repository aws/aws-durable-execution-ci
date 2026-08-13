#!/usr/bin/env python3

import base64
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts/resolve_issue_triage_prompt.py"
SPEC = importlib.util.spec_from_file_location(
    "resolve_issue_triage_prompt",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
RESOLVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RESOLVER)


class ResolveIssueTriagePromptTest(unittest.TestCase):
    def prompt_files(self, directory: str):
        root = Path(directory)
        default_prompt = root / "default.md"
        required_prompt = root / "required.md"
        output_prompt = root / "context" / "prompt.md"
        output_prompt.parent.mkdir()
        default_prompt.write_text("Default classification.\n", encoding="utf-8")
        required_prompt.write_text(
            "Mandatory security contract.\n",
            encoding="utf-8",
        )
        return default_prompt, required_prompt, output_prompt

    def test_default_prompt_is_followed_by_required_instructions(self):
        with tempfile.TemporaryDirectory() as directory:
            default_prompt, required_prompt, output_prompt = self.prompt_files(
                directory
            )

            RESOLVER.resolve_prompt(
                default_prompt,
                required_prompt,
                output_prompt,
                "",
                "",
                "",
            )

            self.assertEqual(
                output_prompt.read_text(encoding="utf-8"),
                "Default classification.\n\n"
                "Mandatory security contract.\n",
            )
            self.assertEqual(output_prompt.stat().st_mode & 0o777, 0o600)

    def test_custom_prompt_replaces_default_but_not_required_instructions(self):
        with tempfile.TemporaryDirectory() as directory:
            default_prompt, required_prompt, output_prompt = self.prompt_files(
                directory
            )

            with patch.object(
                RESOLVER,
                "fetch_custom_prompt",
                return_value="Custom classification.\n",
            ) as fetch:
                RESOLVER.resolve_prompt(
                    default_prompt,
                    required_prompt,
                    output_prompt,
                    ".github/prompts/triage.md",
                    "aws/example",
                    "a" * 40,
                )

            fetch.assert_called_once_with(
                "aws/example",
                "a" * 40,
                ".github/prompts/triage.md",
            )
            self.assertEqual(
                output_prompt.read_text(encoding="utf-8"),
                "Custom classification.\n\n"
                "Mandatory security contract.\n",
            )

    def test_fetches_only_requested_file_at_exact_commit(self):
        prompt = "Repository-specific classification.\n"
        response = {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(prompt.encode()).decode(),
        }

        with patch.object(
            RESOLVER,
            "run_gh_json",
            return_value=response,
        ) as run_gh:
            self.assertEqual(
                RESOLVER.fetch_custom_prompt(
                    "aws/example",
                    "b" * 40,
                    ".github/prompts/custom triage.md",
                ),
                prompt,
            )

        run_gh.assert_called_once_with(
            "repos/aws/example/contents/"
            ".github/prompts/custom%20triage.md?ref="
            + "b" * 40
        )

    def test_rejects_unsafe_custom_prompt_paths(self):
        invalid_paths = (
            "/tmp/prompt.md",
            "../prompt.md",
            ".github/../prompt.md",
            ".github//prompt.md",
            ".github\\prompt.md",
            ".github/prompt.md\nother",
            ".github/prompt.md\0",
        )

        for prompt_path in invalid_paths:
            with self.subTest(prompt_path=prompt_path):
                with self.assertRaises(RESOLVER.PromptError):
                    RESOLVER.validate_custom_prompt_path(prompt_path)

    def test_rejects_invalid_repository_or_nonimmutable_revision(self):
        response = {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(b"prompt").decode(),
        }
        with patch.object(RESOLVER, "run_gh_json", return_value=response):
            with self.assertRaisesRegex(
                RESOLVER.PromptError,
                "repository",
            ):
                RESOLVER.fetch_custom_prompt(
                    "aws/example/extra",
                    "c" * 40,
                    "prompt.md",
                )
            with self.assertRaisesRegex(RESOLVER.PromptError, "40-character"):
                RESOLVER.fetch_custom_prompt(
                    "aws/example",
                    "main",
                    "prompt.md",
                )

    def test_rejects_invalid_remote_file_metadata_and_content(self):
        invalid_responses = (
            [],
            {"type": "dir", "encoding": "base64", "content": ""},
            {"type": "file", "encoding": "utf-8", "content": "prompt"},
            {"type": "file", "encoding": "base64"},
            {"type": "file", "encoding": "base64", "content": "%%%"},
        )

        for response in invalid_responses:
            with self.subTest(response=response), patch.object(
                RESOLVER,
                "run_gh_json",
                return_value=response,
            ):
                with self.assertRaises(RESOLVER.PromptError):
                    RESOLVER.fetch_custom_prompt(
                        "aws/example",
                        "d" * 40,
                        "prompt.md",
                    )

    def test_rejects_unsafe_prompt_content(self):
        invalid_contents = (
            b"",
            b" \n\t",
            b"contains\0nul",
            b"\xff",
            b"x" * (RESOLVER.MAX_PROMPT_BYTES + 1),
        )

        for content in invalid_contents:
            with self.subTest(content_length=len(content)):
                with self.assertRaises(RESOLVER.PromptError):
                    RESOLVER.validate_prompt_bytes(content, "custom prompt")


if __name__ == "__main__":
    unittest.main()
