#!/usr/bin/env python3

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/serve_aws_credentials.py"
)


class CredentialServerTest(unittest.TestCase):
    def test_requires_authorization_and_returns_container_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            ready_path = Path(directory) / "port"
            environment = os.environ.copy()
            environment.update(
                {
                    "AWS_ACCESS_KEY_ID": "ASIATESTACCESS",
                    "AWS_SECRET_ACCESS_KEY": "test-secret",
                    "AWS_SESSION_TOKEN": "test-session",
                    "AWS_CREDENTIAL_EXPIRATION": (
                        "2026-08-22T06:30:00Z"
                    ),
                    "CODEX_CREDENTIAL_PROXY_TOKEN": "proxy-token",
                }
            )
            process = subprocess.Popen(
                [sys.executable, str(SCRIPT), str(ready_path)],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                for _ in range(100):
                    if ready_path.exists():
                        break
                    if process.poll() is not None:
                        stdout, stderr = process.communicate()
                        self.fail(
                            f"server exited: {stdout!r} {stderr!r}"
                        )
                    time.sleep(0.01)
                self.assertTrue(ready_path.exists())
                url = (
                    f"http://127.0.0.1:{ready_path.read_text()}"
                    "/credentials"
                )

                request = urllib.request.Request(
                    url,
                    headers={"Authorization": "proxy-token"},
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    credentials = json.load(response)

                self.assertEqual(
                    credentials,
                    {
                        "AccessKeyId": "ASIATESTACCESS",
                        "SecretAccessKey": "test-secret",
                        "Token": "test-session",
                        "Expiration": "2026-08-22T06:30:00Z",
                    },
                )

                unauthorized = urllib.request.Request(
                    url,
                    headers={"Authorization": "wrong-token"},
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(unauthorized, timeout=2)
                self.assertEqual(error.exception.code, 401)
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=2)


if __name__ == "__main__":
    unittest.main()
