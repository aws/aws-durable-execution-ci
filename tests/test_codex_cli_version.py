#!/usr/bin/env python3

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github/workflows"
EXPECTED_VERSION = "0.149.1"


class CodexCliVersionTest(unittest.TestCase):
    def test_all_workflows_use_the_current_codex_cli(self):
        pins = []
        for workflow_path in sorted(WORKFLOW_DIR.glob("*.yml")):
            workflow = workflow_path.read_text(encoding="utf-8")
            for version in re.findall(
                r'@openai/codex@([0-9]+\.[0-9]+\.[0-9]+)',
                workflow,
            ):
                pins.append((workflow_path.name, version))

        stale_pins = [
            (name, version)
            for name, version in pins
            if version != EXPECTED_VERSION
        ]
        self.assertTrue(pins)
        self.assertEqual(stale_pins, [])


if __name__ == "__main__":
    unittest.main()
