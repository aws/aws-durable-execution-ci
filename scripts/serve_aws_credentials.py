#!/usr/bin/env python3

import http.server
import json
import os
import secrets
import sys
from http import HTTPStatus
from pathlib import Path
from typing import Any


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

        body = json.dumps(
            self.server.credentials,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: serve_aws_credentials.py READY_PATH", file=sys.stderr)
        return 2

    credentials = {
        "AccessKeyId": require_environment("AWS_ACCESS_KEY_ID"),
        "SecretAccessKey": require_environment("AWS_SECRET_ACCESS_KEY"),
        "Token": require_environment("AWS_SESSION_TOKEN"),
        "Expiration": normalize_expiration(
            require_environment("AWS_CREDENTIAL_EXPIRATION")
        ),
    }
    authorization_token = require_environment(
        "CODEX_CREDENTIAL_PROXY_TOKEN"
    )
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_CREDENTIAL_EXPIRATION",
        "CODEX_CREDENTIAL_PROXY_TOKEN",
    ):
        os.environ.pop(name, None)

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        CredentialHandler,
    )
    server.credentials = credentials
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
