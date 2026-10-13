#!/usr/bin/env python3

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "scripts/run_claude_isolated.sh"


class RunClaudeIsolatedTest(unittest.TestCase):
    def test_cli_limit_preserves_arguments_input_results_and_exit_status(self):
        cases = (
            ("success", False, 51, 0),
            ("error_max_turns", True, 21, 0),
            ("error_during_execution", True, 1, 7),
        )
        for subtype, is_error, num_turns, exit_code in cases:
            with self.subTest(subtype=subtype), tempfile.TemporaryDirectory(
                dir=os.environ.get("TMPDIR")
            ) as temp:
                root = Path(temp)
                fake_bin = root / "bin"
                fake_bin.mkdir()
                sudo = fake_bin / "sudo"
                sudo.write_text(
                    """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "-H" ]]; then shift; fi
[[ "$1" == "-u" && "$2" == "claude-review" && "$3" == "--" ]]
shift 3
exec "$@"
""",
                    encoding="utf-8",
                )
                sudo.chmod(0o755)

                action = root / "action"
                bun = action / "bin/bun"
                bun.parent.mkdir(parents=True)
                bun.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                bun.chmod(0o755)
                cli = action / (
                    "node_modules/@anthropic-ai/claude-agent-sdk-linux-x64/claude"
                )
                cli.parent.mkdir(parents=True)
                capture = root / "cli-input.json"
                result = {
                    "type": "result",
                    "subtype": subtype,
                    "is_error": is_error,
                    "num_turns": num_turns,
                }
                if not is_error:
                    result["structured_output"] = {
                        "comments": [],
                        "summary": "Review completed.",
                    }
                output = json.dumps(result) + "\n"
                cli.write_text(
                    f"#!{sys.executable}\n"
                    "import json, sys\n"
                    f"with open({str(capture)!r}, 'w') as handle:\n"
                    "    json.dump({'args': sys.argv[1:], "
                    "'input': sys.stdin.read()}, handle)\n"
                    f"sys.stdout.write({output!r})\n"
                    f"sys.exit({exit_code})\n",
                    encoding="utf-8",
                )
                cli.chmod(0o755)
                args = [
                    "--output-format", "stream-json",
                    "--input-format", "stream-json",
                    "--model", "test-model",
                    "--json-schema", '{"type": "object"}',
                ]
                prompt = '{"type":"user","message":"Review this diff"}\n'
                completed = subprocess.run(
                    ["bash", str(WRAPPER), *args],
                    env={
                        **os.environ,
                        "GITHUB_ACTION_PATH": str(action),
                        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    },
                    input=prompt,
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertEqual(completed.returncode, exit_code, completed.stderr)
                self.assertEqual(completed.stdout, output)
                self.assertEqual(
                    json.loads(capture.read_text(encoding="utf-8")),
                    {"args": ["--max-turns", "20", *args], "input": prompt},
                )


if __name__ == "__main__":
    unittest.main()
