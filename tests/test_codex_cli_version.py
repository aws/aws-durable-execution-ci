#!/usr/bin/env python3

import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github/workflows"
DEPENDABOT = (
    REPO_ROOT / ".github/dependabot.yml"
).read_text(encoding="utf-8")
PACKAGE = json.loads(
    (REPO_ROOT / "package.json").read_text(encoding="utf-8")
)
PACKAGE_LOCK = json.loads(
    (REPO_ROOT / "package-lock.json").read_text(encoding="utf-8")
)
CODEX_WORKFLOWS = (
    "codex-issue-worker.yml",
    "codex-review.yml",
    "issue-triage.yml",
    "notify.yml",
)


class CodexCliVersionTest(unittest.TestCase):
    def test_dependabot_updates_npm_and_github_actions(self):
        for ecosystem in ("github-actions", "npm"):
            with self.subTest(ecosystem=ecosystem):
                match = re.search(
                    rf"(?ms)^  - package-ecosystem: {ecosystem}\n"
                    r"(.*?)(?=^  - package-ecosystem:|\Z)",
                    DEPENDABOT,
                )
                self.assertIsNotNone(match)
                block = match.group(1)
                self.assertIn("directory: /", block)
                self.assertIn("interval: weekly", block)

    def test_codex_cli_is_exactly_pinned_in_the_npm_manifest(self):
        version = PACKAGE["dependencies"]["@openai/codex"]

        self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+$")
        self.assertEqual(
            PACKAGE_LOCK["packages"][""]["dependencies"]["@openai/codex"],
            version,
        )
        self.assertEqual(
            PACKAGE_LOCK["packages"]["node_modules/@openai/codex"][
                "version"
            ],
            version,
        )

    def test_codex_workflows_install_from_the_trusted_lockfile(self):
        for workflow_name in CODEX_WORKFLOWS:
            with self.subTest(workflow=workflow_name):
                workflow = (
                    WORKFLOW_DIR / workflow_name
                ).read_text(encoding="utf-8")
                self.assertNotRegex(
                    workflow,
                    r"@openai/codex@[0-9]+\.[0-9]+\.[0-9]+",
                )
                self.assertIn("package.json", workflow)
                self.assertIn("package-lock.json", workflow)
                self.assertIn("install_codex_cli.sh", workflow)


if __name__ == "__main__":
    unittest.main()
