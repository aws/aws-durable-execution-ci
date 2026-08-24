#!/usr/bin/env python3

import http.server
import json
import os
import re
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable


OIDC_AUDIENCE = "sts.amazonaws.com"
REFRESH_WINDOW = timedelta(minutes=15)
REQUEST_TIMEOUT_SECONDS = 15
ROLE_SESSION_DURATION_SECONDS = 3600
ROLE_SESSION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9+=,.@_-]{2,64}$")


def require_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def normalize_expiration(value: str) -> str:
    """Accept both plain timestamps and JSON-serialized action outputs."""
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    return decoded if isinstance(decoded, str) else value


def parse_expiration(value: str) -> datetime:
    normalized = normalize_expiration(value)
    try:
        parsed = datetime.fromisoformat(
            normalized[:-1] + "+00:00"
            if normalized.endswith("Z")
            else normalized
        )
    except ValueError as error:
        raise RuntimeError("AWS credential expiration is invalid") from error
    if parsed.tzinfo is None:
        raise RuntimeError("AWS credential expiration must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_credentials(credentials: dict[str, Any]) -> dict[str, str]:
    required = ("AccessKeyId", "SecretAccessKey", "Token", "Expiration")
    if any(
        not isinstance(credentials.get(name), str)
        or not credentials[name]
        for name in required
    ):
        raise RuntimeError("AWS returned invalid temporary credentials")
    parse_expiration(credentials["Expiration"])
    return {name: credentials[name] for name in required}


def request_github_oidc_token(
    request_url: str,
    request_token: str,
) -> str:
    parsed = urllib.parse.urlsplit(request_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("GitHub OIDC request URL is invalid")
    query = urllib.parse.parse_qsl(
        parsed.query,
        keep_blank_values=True,
    )
    query = [
        (name, value)
        for name, value in query
        if name != "audience"
    ]
    query.append(("audience", OIDC_AUDIENCE))
    url = urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(query),
            parsed.fragment,
        )
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"bearer {request_token}",
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            body = response.read(1_000_001)
    except (OSError, urllib.error.URLError) as error:
        raise RuntimeError(
            "failed to request a fresh GitHub OIDC token"
        ) from error
    if len(body) > 1_000_000:
        raise RuntimeError("GitHub OIDC response is too large")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("GitHub returned an invalid OIDC response") from error
    token = payload.get("value") if isinstance(payload, dict) else None
    if (
        not isinstance(token, str)
        or not 4 <= len(token) <= 20_000
    ):
        raise RuntimeError("GitHub returned an invalid OIDC token")
    return token


def xml_text(root: ElementTree.Element, name: str) -> str:
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == name:
            value = element.text
            if isinstance(value, str) and value:
                return value
    raise RuntimeError("AWS returned an invalid credential response")


def assume_role_with_web_identity(
    region: str,
    role_arn: str,
    role_session_name: str,
    inline_session_policy: str,
    web_identity_token: str,
) -> dict[str, str]:
    if re.fullmatch(r"[a-z0-9-]+", region) is None:
        raise RuntimeError("AWS region is invalid")
    if not role_arn.startswith("arn:") or len(role_arn) > 2_048:
        raise RuntimeError("AWS role ARN is invalid")
    if ROLE_SESSION_NAME_PATTERN.fullmatch(role_session_name) is None:
        raise RuntimeError("AWS role session name is invalid")
    try:
        policy = json.loads(inline_session_policy)
    except json.JSONDecodeError as error:
        raise RuntimeError("AWS inline session policy is invalid") from error
    if not isinstance(policy, dict):
        raise RuntimeError("AWS inline session policy is invalid")
    normalized_policy = json.dumps(
        policy,
        separators=(",", ":"),
        sort_keys=True,
    )
    if not 1 <= len(normalized_policy) <= 2_048:
        raise RuntimeError("AWS inline session policy is invalid")

    request = urllib.request.Request(
        f"https://sts.{region}.amazonaws.com/",
        data=urllib.parse.urlencode(
            {
                "Action": "AssumeRoleWithWebIdentity",
                "DurationSeconds": str(ROLE_SESSION_DURATION_SECONDS),
                "Policy": normalized_policy,
                "RoleArn": role_arn,
                "RoleSessionName": role_session_name,
                "Version": "2011-06-15",
                "WebIdentityToken": web_identity_token,
            }
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            body = response.read(1_000_001)
    except (OSError, urllib.error.URLError) as error:
        raise RuntimeError(
            "failed to refresh AWS role credentials"
        ) from error
    if len(body) > 1_000_000:
        raise RuntimeError("AWS credential response is too large")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise RuntimeError(
            "AWS returned an invalid credential response"
        ) from error
    return validate_credentials(
        {
            "AccessKeyId": xml_text(root, "AccessKeyId"),
            "SecretAccessKey": xml_text(root, "SecretAccessKey"),
            "Token": xml_text(root, "SessionToken"),
            "Expiration": xml_text(root, "Expiration"),
        }
    )


class CredentialAudit:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.values: set[str] = set()
        self.lock = threading.Lock()

    def record(self, credentials: dict[str, str]) -> None:
        try:
            with self.lock:
                self.values.update(
                    credentials[name]
                    for name in ("AccessKeyId", "SecretAccessKey", "Token")
                )
                temporary = self.path.with_name(
                    f".{self.path.name}.{os.getpid()}.tmp"
                )
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                    0o600,
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                    json.dump(
                        sorted(self.values),
                        file,
                        ensure_ascii=True,
                        separators=(",", ":"),
                    )
                    file.write("\n")
                temporary.replace(self.path)
        except OSError as error:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)
            raise RuntimeError(
                "failed to update the runtime credential audit"
            ) from error


class RefreshingCredentialProvider:
    def __init__(
        self,
        credentials: dict[str, Any],
        refresh: Callable[[], dict[str, str]],
        audit: CredentialAudit,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.current = validate_credentials(credentials)
        self.refresh = refresh
        self.audit = audit
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.lock = threading.Lock()
        self.audit.record(self.current)

    def credentials(self) -> dict[str, str]:
        with self.lock:
            current_time = self.now()
            expiration = parse_expiration(self.current["Expiration"])
            if expiration <= current_time + REFRESH_WINDOW:
                try:
                    refreshed = validate_credentials(self.refresh())
                except RuntimeError:
                    if expiration <= current_time:
                        raise
                else:
                    if parse_expiration(
                        refreshed["Expiration"]
                    ) <= current_time:
                        raise RuntimeError(
                            "refreshed AWS credentials are already expired"
                        )
                    self.audit.record(refreshed)
                    self.current = refreshed
            return dict(self.current)


class CredentialHandler(http.server.BaseHTTPRequestHandler):
    server: Any

    def do_GET(self) -> None:
        expected = self.server.authorization_token
        provided = self.headers.get("Authorization", "")
        if (
            self.path != "/credentials"
            or not secrets.compare_digest(provided, expected)
        ):
            self.send_error(HTTPStatus.UNAUTHORIZED)
            return

        try:
            credentials = self.server.provider.credentials()
        except RuntimeError as error:
            print(
                f"AWS credential refresh failed: {error}",
                file=sys.stderr,
                flush=True,
            )
            self.send_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "AWS credential refresh failed",
            )
            return
        body = json.dumps(credentials, separators=(",", ":")).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "usage: serve_aws_credentials.py READY_PATH AUDIT_PATH",
            file=sys.stderr,
        )
        return 2

    initial_credentials = {
        "AccessKeyId": require_environment("AWS_ACCESS_KEY_ID"),
        "SecretAccessKey": require_environment("AWS_SECRET_ACCESS_KEY"),
        "Token": require_environment("AWS_SESSION_TOKEN"),
        "Expiration": normalize_expiration(
            require_environment("AWS_CREDENTIAL_EXPIRATION")
        ),
    }
    oidc_request_token = require_environment(
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN"
    )
    oidc_request_url = require_environment("ACTIONS_ID_TOKEN_REQUEST_URL")
    region = require_environment("AWS_REGION")
    role_arn = require_environment("AWS_ROLE_ARN")
    role_session_name = require_environment("AWS_ROLE_SESSION_NAME")
    inline_session_policy = require_environment(
        "CODEX_BEDROCK_SESSION_POLICY"
    )
    authorization_token = require_environment(
        "CODEX_CREDENTIAL_PROXY_TOKEN"
    )
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_CREDENTIAL_EXPIRATION",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "AWS_REGION",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
        "CODEX_BEDROCK_SESSION_POLICY",
        "CODEX_CREDENTIAL_PROXY_TOKEN",
    ):
        os.environ.pop(name, None)

    audit = CredentialAudit(Path(sys.argv[2]))
    provider = RefreshingCredentialProvider(
        initial_credentials,
        lambda: assume_role_with_web_identity(
            region,
            role_arn,
            role_session_name,
            inline_session_policy,
            request_github_oidc_token(
                oidc_request_url,
                oidc_request_token,
            ),
        ),
        audit,
    )
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        CredentialHandler,
    )
    server.provider = provider
    server.authorization_token = authorization_token

    ready_path = Path(sys.argv[1])
    ready_path.write_text(
        str(server.server_address[1]),
        encoding="ascii",
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
