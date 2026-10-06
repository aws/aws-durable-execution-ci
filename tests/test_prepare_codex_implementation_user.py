#!/usr/bin/env python3

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO_ROOT / "scripts/prepare_codex_implementation_user.sh"
).read_text(encoding="utf-8")


class PrepareCodexImplementationUserTest(unittest.TestCase):
    def test_blocking_workspace_ancestors_get_execute_only_traversal(self):
        fallback = """\
for part in "${ancestor_parts[@]}"; do
  ancestor="${ancestor%/}/${part}"
  if ! sudo -u "$implementation_user" test -x "$ancestor"; then
    sudo setfacl -m "u:${implementation_user}:--x" "$ancestor"
  fi
done
"""

        self.assertIn(fallback, SCRIPT)
        self.assertIn(
            'IFS=/ read -r -a ancestor_parts <<< '
            '"$(dirname "${GITHUB_WORKSPACE#/}")"',
            SCRIPT,
        )
        code_lines = [
            line for line in SCRIPT.splitlines()
            if not line.lstrip().startswith("#")
        ]
        self.assertFalse(any("/home/runner" in line for line in code_lines))
        self.assertLess(
            SCRIPT.index(fallback),
            SCRIPT.index("git config --global --add safe.directory"),
        )

    def test_workspace_ownership_uses_the_invoking_account(self):
        self.assertIn('job_user="$(id -un)"', SCRIPT)
        self.assertIn(
            'sudo chown "${job_user}:${implementation_user}" '
            '"$GITHUB_WORKSPACE"',
            SCRIPT,
        )
        self.assertNotIn('"runner:', SCRIPT)


if __name__ == "__main__":
    unittest.main()
