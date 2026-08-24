#!/usr/bin/env python3

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO_ROOT / "scripts/prepare_codex_implementation_user.sh"
).read_text(encoding="utf-8")


class PrepareCodexImplementationUserTest(unittest.TestCase):
    def test_private_runner_home_gets_execute_only_traversal(self):
        fallback = """\
if ! sudo -u "$implementation_user" test -x /home/runner; then
  sudo setfacl -m "u:${implementation_user}:--x" /home/runner
fi
"""

        self.assertIn(fallback, SCRIPT)
        self.assertLess(
            SCRIPT.index(fallback),
            SCRIPT.index("git config --global --add safe.directory"),
        )


if __name__ == "__main__":
    unittest.main()
