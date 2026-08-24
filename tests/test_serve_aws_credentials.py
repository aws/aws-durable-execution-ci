#!/usr/bin/env python3

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.serve_aws_credentials import (
    CredentialAudit,
    RefreshingCredentialProvider,
    assume_role_with_web_identity,
    normalize_expiration,
    request_github_oidc_token,
)


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/serve_aws_credentials.py"
)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class CredentialServerTest(unittest.TestCase):
    def test_requires_authorization_and_returns_container_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            ready_path = Path(directory) / "port"
            audit_path = Path(directory) / "credentials.json"
            environment = os.environ.copy()
            environment.update(
                {
                    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
                    "ACTIONS_ID_TOKEN_REQUEST_URL": (
                        "https://example.invalid/oidc"
                    ),
                    "AWS_ACCESS_KEY_ID": "ASIATESTACCESS",
                    "AWS_REGION": "us-east-1",
                    "AWS_ROLE_ARN": (
                        "arn:aws:iam::123456789012:role/TestRole"
                    ),
                    "AWS_ROLE_SESSION_NAME": "codex-test-session",
                    "AWS_SECRET_ACCESS_KEY": "test-secret",
                    "AWS_SESSION_TOKEN": "test-session",
                    "AWS_CREDENTIAL_EXPIRATION": '"2099-08-22T06:30:00Z"',
                    "CODEX_BEDROCK_SESSION_POLICY": (
                        '{"Version":"2012-10-17","Statement":[]}'
                    ),
                    "CODEX_CREDENTIAL_PROXY_TOKEN": "proxy-token",
                }
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(ready_path),
                    str(audit_path),
                ],
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
                        "Expiration": "2099-08-22T06:30:00Z",
                    },
                )
                self.assertEqual(
                    set(json.loads(audit_path.read_text(encoding="utf-8"))),
                    {
                        "ASIATESTACCESS",
                        "test-secret",
                        "test-session",
                    },
                )
                self.assertEqual(
                    audit_path.stat().st_mode & 0o777,
                    0o600,
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

    def test_provider_refreshes_expiring_credentials_and_audits_them(self):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc)
            audit_path = Path(directory) / "credentials.json"
            refresh = Mock(
                return_value={
                    "AccessKeyId": "ASIAREFRESHED",
                    "SecretAccessKey": "refreshed-secret",
                    "Token": "refreshed-session",
                    "Expiration": (
                        now + timedelta(hours=1)
                    ).isoformat(),
                }
            )
            provider = RefreshingCredentialProvider(
                {
                    "AccessKeyId": "ASIAINITIAL",
                    "SecretAccessKey": "initial-secret",
                    "Token": "initial-session",
                    "Expiration": (
                        now + timedelta(minutes=10)
                    ).isoformat(),
                },
                refresh,
                CredentialAudit(audit_path),
                now=lambda: now,
            )

            self.assertEqual(
                provider.credentials()["AccessKeyId"],
                "ASIAREFRESHED",
            )
            self.assertEqual(
                provider.credentials()["AccessKeyId"],
                "ASIAREFRESHED",
            )
            refresh.assert_called_once_with()
            self.assertEqual(
                set(json.loads(audit_path.read_text(encoding="utf-8"))),
                {
                    "ASIAINITIAL",
                    "initial-secret",
                    "initial-session",
                    "ASIAREFRESHED",
                    "refreshed-secret",
                    "refreshed-session",
                },
            )

    def test_provider_retries_refresh_while_current_session_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc)
            refresh = Mock(
                side_effect=RuntimeError("temporary refresh failure")
            )
            provider = RefreshingCredentialProvider(
                {
                    "AccessKeyId": "ASIAINITIAL",
                    "SecretAccessKey": "initial-secret",
                    "Token": "initial-session",
                    "Expiration": (
                        now + timedelta(minutes=10)
                    ).isoformat(),
                },
                refresh,
                CredentialAudit(Path(directory) / "credentials.json"),
                now=lambda: now,
            )

            self.assertEqual(
                provider.credentials()["AccessKeyId"],
                "ASIAINITIAL",
            )
            self.assertEqual(
                provider.credentials()["AccessKeyId"],
                "ASIAINITIAL",
            )
            self.assertEqual(refresh.call_count, 2)

    def test_provider_fails_when_expired_session_cannot_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc)
            provider = RefreshingCredentialProvider(
                {
                    "AccessKeyId": "ASIAINITIAL",
                    "SecretAccessKey": "initial-secret",
                    "Token": "initial-session",
                    "Expiration": now.isoformat(),
                },
                Mock(side_effect=RuntimeError("refresh failed")),
                CredentialAudit(Path(directory) / "credentials.json"),
                now=lambda: now,
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "refresh failed",
            ):
                provider.credentials()

    def test_provider_does_not_activate_unaudited_refresh(self):
        now = datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc)
        audit = Mock()
        audit.record.side_effect = [
            None,
            RuntimeError("audit write failed"),
        ]
        provider = RefreshingCredentialProvider(
            {
                "AccessKeyId": "ASIAINITIAL",
                "SecretAccessKey": "initial-secret",
                "Token": "initial-session",
                "Expiration": (
                    now + timedelta(minutes=10)
                ).isoformat(),
            },
            Mock(
                return_value={
                    "AccessKeyId": "ASIAREFRESHED",
                    "SecretAccessKey": "refreshed-secret",
                    "Token": "refreshed-session",
                    "Expiration": (
                        now + timedelta(hours=1)
                    ).isoformat(),
                }
            ),
            audit,
            now=lambda: now,
        )

        with self.assertRaisesRegex(RuntimeError, "audit write failed"):
            provider.credentials()

        self.assertEqual(provider.current["AccessKeyId"], "ASIAINITIAL")

    def test_refresh_uses_fresh_oidc_token_and_sts_web_identity(self):
        expiration = "2026-08-24T22:00:00Z"
        sts_response = f"""\
<AssumeRoleWithWebIdentityResponse>
  <AssumeRoleWithWebIdentityResult>
    <Credentials>
      <AccessKeyId>ASIAREFRESHED</AccessKeyId>
      <SecretAccessKey>refreshed-secret</SecretAccessKey>
      <SessionToken>refreshed-session</SessionToken>
      <Expiration>{expiration}</Expiration>
    </Credentials>
  </AssumeRoleWithWebIdentityResult>
</AssumeRoleWithWebIdentityResponse>
""".encode("utf-8")
        responses = [
            FakeResponse(b'{"value":"fresh-oidc-token"}'),
            FakeResponse(sts_response),
        ]
        with patch(
            "scripts.serve_aws_credentials.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            oidc_token = request_github_oidc_token(
                (
                    "https://example.invalid/oidc?"
                    "api-version=2.0&audience=old"
                ),
                "request-token",
            )
            credentials = assume_role_with_web_identity(
                "us-east-1",
                "arn:aws:iam::123456789012:role/TestRole",
                "codex-test-session",
                '{"Version":"2012-10-17","Statement":[]}',
                oidc_token,
            )

        oidc_request = urlopen.call_args_list[0].args[0]
        self.assertEqual(
            urllib.parse.parse_qs(
                urllib.parse.urlsplit(oidc_request.full_url).query
            )["audience"],
            ["sts.amazonaws.com"],
        )
        self.assertEqual(
            oidc_request.get_header("Authorization"),
            "bearer request-token",
        )
        sts_request = urlopen.call_args_list[1].args[0]
        sts_payload = urllib.parse.parse_qs(
            sts_request.data.decode("utf-8")
        )
        self.assertEqual(
            sts_payload["Action"],
            ["AssumeRoleWithWebIdentity"],
        )
        self.assertEqual(sts_payload["DurationSeconds"], ["3600"])
        self.assertEqual(
            sts_payload["WebIdentityToken"],
            ["fresh-oidc-token"],
        )
        self.assertEqual(credentials["AccessKeyId"], "ASIAREFRESHED")
        self.assertEqual(credentials["Expiration"], expiration)

    def test_preserves_plain_expiration(self):
        expiration = "2026-08-22T06:30:00Z"

        self.assertEqual(
            normalize_expiration(expiration),
            expiration,
        )


if __name__ == "__main__":
    unittest.main()
