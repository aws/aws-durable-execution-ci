#!/usr/bin/env python3

import base64
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from ai_review_telemetry import (
    TelemetryError,
    require_repository_id,
    require_timestamp,
    stable_identifier,
)


REVIEW_COMMAND = "/ai review"
REVIEW_COMMAND_PATTERN = re.compile(
    r"^[ \t]*/ai[ \t]+review(?=\Z|[ \t\r\n])"
)
MAX_REVIEW_GUIDANCE_BYTES = 10_000
WRITE_PERMISSIONS = frozenset(("admin", "maintain", "write"))
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class ReviewResolutionError(ValueError):
    pass


class ReviewNotRequested(ValueError):
    pass


def run_gh_json(endpoint: str) -> Any:
    result = subprocess.run(
        ["gh", "api", endpoint],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def is_bot_user(user: Any) -> bool:
    if not isinstance(user, dict):
        return True
    login = user.get("login")
    return (
        not isinstance(login, str)
        or not login
        or user.get("type") == "Bot"
        or login.casefold().endswith("[bot]")
    )


def collaborator_has_write_permission(
    repository: str,
    login: str,
) -> bool:
    encoded_login = urllib.parse.quote(login, safe="")
    permission = run_gh_json(
        f"repos/{repository}/collaborators/{encoded_login}/permission"
    )
    return (
        isinstance(permission, dict)
        and permission.get("permission") in WRITE_PERMISSIONS
    )


def parse_review_command(body: str) -> str | None:
    match = REVIEW_COMMAND_PATTERN.match(body)
    if match is None:
        return None
    return body[match.end():].strip()


def validate_review_guidance(guidance: str) -> str:
    if "\0" in guidance:
        raise ReviewResolutionError(
            "AI review guidance must not contain a null character"
        )
    if len(guidance.encode("utf-8")) > MAX_REVIEW_GUIDANCE_BYTES:
        raise ReviewResolutionError(
            "AI review guidance exceeds the 10000-byte limit"
        )
    return guidance


def review_request(
    event_name: str,
    event: dict[str, Any],
) -> tuple[int, bool, str]:
    if event_name == "pull_request_target":
        pull_request = event.get("pull_request")
        if (
            not isinstance(pull_request, dict)
            or type(pull_request.get("number")) is not int
        ):
            raise ReviewResolutionError(
                "pull_request_target event has no pull request number"
            )
        return pull_request["number"], False, ""

    if event_name != "issue_comment":
        raise ReviewResolutionError(f"unsupported event: {event_name}")

    issue = event.get("issue")
    comment = event.get("comment")
    guidance = (
        parse_review_command(comment.get("body"))
        if isinstance(comment, dict)
        and isinstance(comment.get("body"), str)
        else None
    )
    if (
        event.get("action") != "created"
        or not isinstance(issue, dict)
        or "pull_request" not in issue
        or issue.get("state") != "open"
        or type(issue.get("number")) is not int
        or not isinstance(comment, dict)
        or guidance is None
        or is_bot_user(comment.get("user"))
    ):
        raise ReviewNotRequested("event is not an AI review command")

    user = comment["user"]["login"]
    repository = require_environment("GITHUB_REPOSITORY")
    if not collaborator_has_write_permission(repository, user):
        print(
            f"::warning::Ignoring {REVIEW_COMMAND} from unauthorized user "
            f"{user}."
        )
        raise ReviewNotRequested("AI review command is not authorized")

    return issue["number"], True, validate_review_guidance(guidance)


def require_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ReviewResolutionError(f"{name} must be set")
    return value


def require_string(value: Any, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReviewResolutionError(f"GitHub returned no {description}")
    return value


def require_sha(value: Any, description: str) -> str:
    sha = require_string(value, description)
    if not SHA_PATTERN.fullmatch(sha):
        raise ReviewResolutionError(f"GitHub returned an invalid {description}")
    return sha


def trigger_metadata(
    event_name: str,
    event: dict[str, Any],
    pull_request: dict[str, Any],
    *,
    number: int,
    base_sha: str,
    head_sha: str,
) -> dict[str, str]:
    action = event.get("action")
    if not isinstance(action, str) or not action:
        raise ReviewResolutionError("GitHub event has no action")

    metadata = {
        "event_name": event_name,
        "event_action": action,
        "event_timestamp": "",
        "event_base_sha": base_sha,
        "event_head_sha": head_sha,
        "before_sha": "",
        "after_sha": "",
        "actor": require_environment("GITHUB_ACTOR"),
        "command_comment_id": "",
    }
    if event_name == "pull_request_target":
        event_pull_request = event.get("pull_request")
        if not isinstance(event_pull_request, dict):
            raise ReviewResolutionError(
                "pull_request_target event has no pull request"
            )
        event_base = event_pull_request.get("base")
        event_head = event_pull_request.get("head")
        if isinstance(event_base, dict) and event_base.get("sha") is not None:
            metadata["event_base_sha"] = require_sha(
                event_base.get("sha"),
                "event base SHA",
            )
        if isinstance(event_head, dict) and event_head.get("sha") is not None:
            metadata["event_head_sha"] = require_sha(
                event_head.get("sha"),
                "event head SHA",
            )
        timestamp = (
            event_pull_request.get("created_at")
            if action == "opened"
            else event_pull_request.get("updated_at")
        )
        if timestamp is None:
            timestamp = (
                pull_request.get("created_at")
                if action == "opened"
                else pull_request.get("updated_at")
            )
        before = event.get("before")
        after = event.get("after")
        if before is not None:
            metadata["before_sha"] = require_sha(before, "before SHA")
        if after is not None:
            metadata["after_sha"] = require_sha(after, "after SHA")
    else:
        comment = event.get("comment")
        if not isinstance(comment, dict):
            raise ReviewResolutionError("review command has no comment")
        timestamp = comment.get("created_at")
        comment_id = comment.get("node_id", comment.get("id"))
        if isinstance(comment_id, int):
            comment_id = str(comment_id)
        if not isinstance(comment_id, str) or not comment_id:
            raise ReviewResolutionError("review command has no comment ID")
        metadata["command_comment_id"] = comment_id

    try:
        metadata["event_timestamp"] = require_timestamp(
            timestamp,
            "event timestamp",
        )
        repository_id = require_repository_id(
            require_environment("GITHUB_REPOSITORY_ID")
        )
    except TelemetryError as error:
        raise ReviewResolutionError(str(error)) from error
    trigger_id, _digest = stable_identifier(
        "art",
        {
            "repository_id": repository_id,
            "pull_request_number": number,
            **metadata,
        },
    )
    return {"trigger_id": trigger_id, **metadata}


def resolve_review(
    event_name: str,
    event: dict[str, Any],
) -> dict[str, str] | None:
    try:
        number, command_requested, review_guidance = review_request(
            event_name,
            event,
        )
    except ReviewNotRequested:
        return None

    repository = require_environment("GITHUB_REPOSITORY")
    pull_request = run_gh_json(f"repos/{repository}/pulls/{number}")
    if not isinstance(pull_request, dict):
        raise ReviewResolutionError("GitHub returned an invalid pull request")
    if pull_request.get("state") != "open":
        return None

    base = pull_request.get("base")
    head = pull_request.get("head")
    if (
        not isinstance(base, dict)
        or not isinstance(head, dict)
    ):
        raise ReviewResolutionError(
            "GitHub returned incomplete pull request metadata"
        )

    base_sha = require_sha(base.get("sha"), "base SHA")
    head_sha = require_sha(head.get("sha"), "head SHA")
    head_repository = head.get("repo")
    head_repository_name = (
        head_repository.get("full_name")
        if isinstance(head_repository, dict)
        else None
    )
    draft = pull_request.get("draft")
    if not isinstance(draft, bool):
        raise ReviewResolutionError("GitHub returned no pull request draft state")

    author = None
    if not command_requested:
        user = pull_request.get("user")
        if user is not None:
            if not isinstance(user, dict):
                raise ReviewResolutionError(
                    "GitHub returned invalid pull request author metadata"
                )
            author = require_string(user.get("login"), "pull request author")

    if (
        not command_requested
        and (
            author == "dependabot[bot]"
            or draft
            or head_repository_name != repository
        )
    ):
        return None

    return {
        "review-requested": "true",
        "pull-request-number": str(number),
        "base-sha": base_sha,
        "head-sha": head_sha,
        "review-guidance-base64": base64.b64encode(
            review_guidance.encode("utf-8")
        ).decode("ascii"),
        "trigger-metadata-base64": base64.b64encode(
            json.dumps(
                trigger_metadata(
                    event_name,
                    event,
                    pull_request,
                    number=number,
                    base_sha=base_sha,
                    head_sha=head_sha,
                ),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).decode("ascii"),
    }


def write_outputs(outputs: dict[str, str] | None) -> None:
    output_path = Path(require_environment("GITHUB_OUTPUT"))
    resolved = outputs or {
        "review-requested": "false",
        "pull-request-number": "",
        "base-sha": "",
        "head-sha": "",
        "review-guidance-base64": "",
        "trigger-metadata-base64": "",
    }
    with output_path.open("a", encoding="utf-8") as output_file:
        for name, value in resolved.items():
            print(f"{name}={value}", file=output_file)


def main() -> int:
    try:
        event_path = Path(require_environment("GITHUB_EVENT_PATH"))
        event = json.loads(event_path.read_text(encoding="utf-8"))
        if not isinstance(event, dict):
            raise ReviewResolutionError("GitHub event payload is not an object")
        write_outputs(
            resolve_review(
                require_environment("GITHUB_EVENT_NAME"),
                event,
            )
        )
    except (
        json.JSONDecodeError,
        OSError,
        ReviewResolutionError,
        subprocess.CalledProcessError,
    ) as error:
        print(
            f"::error::Failed to resolve AI review request: {error}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
