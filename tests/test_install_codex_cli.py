#!/usr/bin/env python3

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts/install_codex_cli.sh"


class InstallCodexCliTest(unittest.TestCase):
    def test_installs_from_lockfile_and_publishes_binary_path(self):
        with tempfile.TemporaryDirectory(
            dir=os.environ.get("TMPDIR")
        ) as temp:
            temp_path = Path(temp)
            fake_bin = temp_path / "bin"
            fake_bin.mkdir()
            npm_args = temp_path / "npm-args"
            install_root = temp_path / "codex"
            github_path = temp_path / "github-path"
            fake_npm = fake_bin / "npm"
            fake_npm.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$@" > "$NPM_ARGS_FILE"
install_root=""
while [[ "$#" -gt 0 ]]; do
  if [[ "$1" == "--prefix" ]]; then
    install_root="$2"
    shift 2
  else
    shift
  fi
done
install -d -m 755 "$install_root/node_modules/.bin"
printf '#!/usr/bin/env bash\\n' > "$install_root/node_modules/.bin/codex"
chmod 755 "$install_root/node_modules/.bin/codex"
""",
                encoding="utf-8",
            )
            fake_npm.chmod(0o755)
            env = {
                **os.environ,
                "CODEX_INSTALL_ROOT": str(install_root),
                "GITHUB_PATH": str(github_path),
                "NPM_ARGS_FILE": str(npm_args),
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            }

            subprocess.run(
                ["bash", str(INSTALLER), str(REPO_ROOT)],
                check=True,
                env=env,
            )

            self.assertEqual(
                npm_args.read_text(encoding="utf-8").splitlines(),
                [
                    "ci",
                    "--prefix",
                    str(install_root),
                    "--omit=dev",
                    "--ignore-scripts",
                    "--no-audit",
                    "--no-fund",
                ],
            )
            self.assertEqual(
                github_path.read_text(encoding="utf-8").strip(),
                str(install_root / "node_modules/.bin"),
            )


if __name__ == "__main__":
    unittest.main()
