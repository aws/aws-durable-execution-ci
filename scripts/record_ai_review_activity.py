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
    GitHubApi,
    GitHubApiError,
    TelemetryError,
    TelemetryStore,
    build_record,
    finding_path,
    require_identifier,
    require_pull_request_number,
    require_repository_id,
    require_sha,
    require_timestamp,
    stable_identifier,
)


WRITE_PERMISSIONS = frozenset(("admin", "maintain", "write"))
VERDICTS = frozenset(
    ("accepted", "deferred", "false-positive", "out-of-scope", "already-fixed")
)
REPLY_VERDICT_PATTERN = re.compile(
    r"^[ \t]*/ai[ \t]+verdict[ \t]+"
    r"(accepted|deferred|false-positive|out-of-scope|already-fixed)[ \t]*$"
)
EXPLICIT_VERDICT_PATTERN = re.compile(
    r"^[ \t]*/ai[ \t]+verdict[ \t]+"
    r"(arf_v1_[a-z2-7]{26})[ \t]+"
    r"(accepted|deferred|false-positive|out-of-scope|already-fixed)[ \t]*$"
)
FINDING_MARKER_PATTERN = re.compile(
    r"<!-- ai-pr-review:finding:"
    r"(claude|codex):(arf_v1_[a-z2-7]{26}) -->"
)
MAX_SUGGESTIONS_PER_PR = 1000


class VerdictRejected(TelemetryError):
    pass


def require_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise TelemetryError(f"{name} must be set")
    return value


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


def user_metadata(user: Any) -> dict[str, Any]:
    if not isinstance(user, dict):
        return {"login": "", "id": 0, "is_bot": True}
    login = user.get("login")
    user_id = user.get("id")
    return {
        "login": login if isinstance(login, str) else "",
        "id": user_id if type(user_id) is int else 0,
        "is_bot": is_bot_user(user),
    }


def collaborator_has_write_permission(
    api: GitHubApi,
    repository: str,
    login: str,
) -> bool:
    encoded = urllib.parse.quote(login, safe="")
    try:
        response = api.request(
            f"repos/{repository}/collaborators/{encoded}/permission"
        )
    except GitHubApiError as error:
        if error.status == 404:
            return False
        raise
    return (
        isinstance(response, dict)
        and response.get("permission") in WRITE_PERMISSIONS
    )


def pull_request_number(event: dict[str, Any]) -> int:
    pull_request = event.get("pull_request")
    if isinstance(pull_request, dict):
        return require_pull_request_number(pull_request.get("number"))
    issue = event.get("issue")
    if isinstance(issue, dict) and "pull_request" in issue:
        return require_pull_request_number(issue.get("number"))
    raise TelemetryError("GitHub event is not for a pull request")


def pull_request_event_record(
    event: dict[str, Any],
    *,
    repository_id: int,
    repository: str,
) -> dict[str, Any]:
    pull_request = event.get("pull_request")
    action = event.get("action")
    if not isinstance(pull_request, dict) or not isinstance(action, str):
        raise TelemetryError("pull request event is invalid")
    number = require_pull_request_number(pull_request.get("number"))
    base = pull_request.get("base")
    head = pull_request.get("head")
    if not isinstance(base, dict) or not isinstance(head, dict):
        raise TelemetryError("pull request event has incomplete revisions")
    base_sha = require_sha(base.get("sha"), "event base SHA")
    head_sha = require_sha(head.get("sha"), "event head SHA")
    timestamp = (
        pull_request.get("created_at")
        if action == "opened"
        else pull_request.get("updated_at")
    )
    event_timestamp = require_timestamp(timestamp, "pull request event timestamp")
    before_sha = ""
    after_sha = ""
    if event.get("before") is not None:
        before_sha = require_sha(event.get("before"), "before SHA")
    if event.get("after") is not None:
        after_sha = require_sha(event.get("after"), "after SHA")
    actor = user_metadata(event.get("sender"))
    data = {
        "event_name": "pull_request_target",
        "event_action": action,
        "event_timestamp": event_timestamp,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "before_sha": before_sha,
        "after_sha": after_sha,
        "actor": actor,
        "author": user_metadata(pull_request.get("user")),
        "draft": pull_request.get("draft") is True,
    }
    return build_record(
        record_type="pull_request_event",
        identity=data,
        repository_id=repository_id,
        repository=repository,
        pull_request_number=number,
        recorded_at=event_timestamp,
        data=data,
    )


def human_review_record(
    event: dict[str, Any],
    *,
    repository_id: int,
    repository: str,
) -> dict[str, Any]:
    number = pull_request_number(event)
    review = event.get("review")
    action = event.get("action")
    if not isinstance(review, dict) or not isinstance(action, str):
        raise TelemetryError("pull request review event is invalid")
    review_id = review.get("node_id", review.get("id"))
    if isinstance(review_id, int):
        review_id = str(review_id)
    if not isinstance(review_id, str) or not review_id:
        raise TelemetryError("pull request review has no ID")
    submitted_at = review.get("submitted_at") or review.get("updated_at")
    timestamp = require_timestamp(submitted_at, "review timestamp")
    commit_id = review.get("commit_id") or ""
    if commit_id:
        commit_id = require_sha(commit_id, "review commit SHA")
    data = {
        "event_action": action,
        "review_node_id": review_id,
        "state": review.get("state") if isinstance(review.get("state"), str) else "",
        "submitted_at": timestamp,
        "commit_id": commit_id,
        "actor": user_metadata(review.get("user")),
    }
    return build_record(
        record_type="human_review",
        identity={
            "review_node_id": review_id,
            "event_action": action,
            "submitted_at": timestamp,
        },
        repository_id=repository_id,
        repository=repository,
        pull_request_number=number,
        recorded_at=timestamp,
        data=data,
    )


def inline_comment_record(
    event: dict[str, Any],
    *,
    repository_id: int,
    repository: str,
) -> dict[str, Any]:
    number = pull_request_number(event)
    comment = event.get("comment")
    action = event.get("action")
    if not isinstance(comment, dict) or not isinstance(action, str):
        raise TelemetryError("pull request review comment event is invalid")
    comment_id = comment.get("node_id", comment.get("id"))
    if isinstance(comment_id, int):
        comment_id = str(comment_id)
    if not isinstance(comment_id, str) or not comment_id:
        raise TelemetryError("review comment has no ID")
    timestamp_value = (
        comment.get("created_at")
        if action == "created"
        else comment.get("updated_at", comment.get("created_at"))
    )
    timestamp = require_timestamp(timestamp_value, "review comment timestamp")
    in_reply_to_id = comment.get("in_reply_to_id")
    data = {
        "event_action": action,
        "comment_node_id": comment_id,
        "created_at": require_timestamp(
            comment.get("created_at"),
            "review comment created_at",
        ),
        "event_timestamp": timestamp,
        "actor": user_metadata(comment.get("user")),
        "is_reply": type(in_reply_to_id) is int,
        "root_comment_id": in_reply_to_id if type(in_reply_to_id) is int else 0,
        "path": comment.get("path") if isinstance(comment.get("path"), str) else "",
        "commit_id": (
            require_sha(comment["commit_id"], "comment commit SHA")
            if isinstance(comment.get("commit_id"), str)
            and comment.get("commit_id")
            else ""
        ),
        "original_commit_id": (
            require_sha(
                comment["original_commit_id"],
                "comment original commit SHA",
            )
            if isinstance(comment.get("original_commit_id"), str)
            and comment.get("original_commit_id")
            else ""
        ),
    }
    return build_record(
        record_type="human_inline_comment",
        identity={
            "comment_node_id": comment_id,
            "event_action": action,
            "event_timestamp": timestamp,
        },
        repository_id=repository_id,
        repository=repository,
        pull_request_number=number,
        recorded_at=timestamp,
        data=data,
    )


def conversation_comment_record(
    event: dict[str, Any],
    *,
    repository_id: int,
    repository: str,
) -> dict[str, Any]:
    number = pull_request_number(event)
    comment = event.get("comment")
    action = event.get("action")
    if not isinstance(comment, dict) or not isinstance(action, str):
        raise TelemetryError("pull request conversation comment event is invalid")
    comment_id = comment.get("node_id", comment.get("id"))
    if isinstance(comment_id, int):
        comment_id = str(comment_id)
    if not isinstance(comment_id, str) or not comment_id:
        raise TelemetryError("conversation comment has no ID")
    timestamp = require_timestamp(
        comment.get("created_at"),
        "conversation comment timestamp",
    )
    data = {
        "event_action": action,
        "comment_node_id": comment_id,
        "created_at": timestamp,
        "actor": user_metadata(comment.get("user")),
    }
    return build_record(
        record_type="pull_request_conversation_comment",
        identity={
            "comment_node_id": comment_id,
            "event_action": action,
        },
        repository_id=repository_id,
        repository=repository,
        pull_request_number=number,
        recorded_at=timestamp,
        data=data,
    )


def parse_finding_marker(body: Any) -> tuple[str, str] | None:
    if not isinstance(body, str):
        return None
    match = FINDING_MARKER_PATTERN.search(body)
    if match is None:
        return None
    return match.group(1), match.group(2)


def parse_verdict_command(
    event_name: str,
    event: dict[str, Any],
) -> tuple[str | None, str] | None:
    comment = event.get("comment")
    if (
        event.get("action") != "created"
        or not isinstance(comment, dict)
        or not isinstance(comment.get("body"), str)
        or is_bot_user(comment.get("user"))
    ):
        return None
    body = comment["body"]
    if event_name == "pull_request_review_comment":
        match = REPLY_VERDICT_PATTERN.fullmatch(body)
        if match is None:
            return None
        return None, match.group(1)
    if event_name == "issue_comment":
        match = EXPLICIT_VERDICT_PATTERN.fullmatch(body)
        if match is None:
            return None
        return match.group(1), match.group(2)
    return None


def find_finding(
    store: TelemetryStore,
    pull_request_number_value: int,
    finding_id: str,
    reviewer: str | None = None,
) -> tuple[str, dict[str, Any]]:
    require_identifier(finding_id, "finding_id")
    reviewers = (reviewer,) if reviewer else ("claude", "codex")
    matches = []
    for candidate in reviewers:
        if candidate not in {"claude", "codex"}:
            continue
        value = store.read_json(
            finding_path(
                pull_request_number_value,
                candidate,
                finding_id,
            )
        )
        if value is not None:
            expected_fields = {
                "schema_version",
                "finding_id",
                "finding_digest",
                "repository",
                "pull_request_number",
                "reviewer",
                "identity_key",
            }
            if (
                not isinstance(value, dict)
                or set(value) != expected_fields
                or value.get("schema_version") != 1
                or value.get("finding_id") != finding_id
                or value.get("pull_request_number")
                != pull_request_number_value
                or value.get("reviewer") != candidate
                or value.get("repository", {}).get("full_name")
                != store.repository
            ):
                raise TelemetryError(
                    "stored finding identity is invalid"
                )
            matches.append((candidate, value))
    if len(matches) != 1:
        raise TelemetryError("verdict does not identify one trusted AI finding")
    return matches[0]


def latest_observation(
    store: TelemetryStore,
    pull_request_number_value: int,
    finding_id: str,
) -> dict[str, Any]:
    records = store.list_json(
        f"events/pr-{pull_request_number_value}/finding_observed/",
        maximum=5000,
    )
    observations = [
        record
        for record in records
        if isinstance(record, dict)
        and isinstance(record.get("data"), dict)
        and record["data"].get("finding_id") == finding_id
    ]
    if not observations:
        raise TelemetryError("trusted finding has no published observation")
    observations.sort(
        key=lambda record: (
            record["data"].get("published_at", ""),
            record.get("record_id", ""),
        )
    )
    return observations[-1]["data"]


def verdict_record(
    api: GitHubApi,
    store: TelemetryStore,
    event_name: str,
    event: dict[str, Any],
    *,
    repository_id: int,
    repository: str,
) -> tuple[dict[str, Any], tuple[str, int]]:
    parsed = parse_verdict_command(event_name, event)
    if parsed is None:
        raise TelemetryError("event is not a verdict command")
    explicit_finding_id, outcome = parsed
    if outcome not in VERDICTS:
        raise TelemetryError("verdict outcome is invalid")
    number = pull_request_number(event)
    comment = event["comment"]
    actor = user_metadata(comment.get("user"))
    if not actor["login"] or not collaborator_has_write_permission(
        api,
        repository,
        actor["login"],
    ):
        raise VerdictRejected("verdict author lacks write permission")

    reply_root_id = 0
    reviewer = None
    finding_id = explicit_finding_id
    if event_name == "pull_request_review_comment":
        reply_root_id = comment.get("in_reply_to_id")
        if type(reply_root_id) is not int:
            raise VerdictRejected(
                "inline verdict must reply to an AI finding"
            )
        root = api.request(
            f"repos/{repository}/pulls/comments/{reply_root_id}"
        )
        if not isinstance(root, dict):
            raise TelemetryError("GitHub returned no root review comment")
        root_author = root.get("user")
        root_login = (
            root_author.get("login")
            if isinstance(root_author, dict)
            else None
        )
        if root_login not in {"github-actions", "github-actions[bot]"}:
            raise VerdictRejected(
                "inline verdict root was not published by GitHub Actions"
            )
        marker = parse_finding_marker(root.get("body"))
        if marker is None:
            raise VerdictRejected(
                "inline verdict root is not a trusted finding"
            )
        reviewer, finding_id = marker

    assert finding_id is not None
    try:
        reviewer, _identity = find_finding(
            store,
            number,
            finding_id,
            reviewer,
        )
    except TelemetryError as error:
        if str(error) == "verdict does not identify one trusted AI finding":
            raise VerdictRejected(str(error)) from error
        raise
    observation = latest_observation(store, number, finding_id)
    current_pull_request = api.request(f"repos/{repository}/pulls/{number}")
    if not isinstance(current_pull_request, dict):
        raise TelemetryError("GitHub returned no pull request")
    current_head = current_pull_request.get("head")
    if not isinstance(current_head, dict):
        raise TelemetryError("GitHub returned no current pull request head")
    current_head_sha = require_sha(
        current_head.get("sha"),
        "current head SHA",
    )
    comment_id = comment.get("node_id", comment.get("id"))
    if isinstance(comment_id, int):
        comment_id = str(comment_id)
    if not isinstance(comment_id, str) or not comment_id:
        raise TelemetryError("verdict comment has no ID")
    created_at = require_timestamp(
        comment.get("created_at"),
        "verdict timestamp",
    )
    verdict_id, verdict_digest = stable_identifier(
        "arv",
        {
            "finding_id": finding_id,
            "outcome": outcome,
            "command_comment_id": comment_id,
        },
    )
    record = build_record(
        record_type="finding_verdict",
        identity={"verdict_id": verdict_id},
        repository_id=repository_id,
        repository=repository,
        pull_request_number=number,
        recorded_at=created_at,
        data={
            "verdict_id": verdict_id,
            "verdict_digest": verdict_digest,
            "finding_id": finding_id,
            "outcome": outcome,
            "maintainer": actor,
            "command_comment_node_id": comment_id,
            "command_created_at": created_at,
            "reviewer": reviewer,
            "reviewed_base_sha": observation["reviewed_base_sha"],
            "reviewed_head_sha": observation["reviewed_head_sha"],
            "current_head_sha": current_head_sha,
        },
    )
    return record, (event_name, reply_root_id)


def acknowledge_verdict(
    api: GitHubApi,
    repository: str,
    pull_request_number_value: int,
    record: dict[str, Any],
    reply: tuple[str, int],
) -> None:
    data = record["data"]
    body = (
        f"Recorded `{data['outcome']}` for AI finding "
        f"`{data['finding_id']}`."
    )
    event_name, root_id = reply
    if event_name == "pull_request_review_comment":
        api.request(
            (
                f"repos/{repository}/pulls/{pull_request_number_value}/"
                f"comments/{root_id}/replies"
            ),
            method="POST",
            payload={"body": body},
        )
    else:
        api.request(
            f"repos/{repository}/issues/{pull_request_number_value}/comments",
            method="POST",
            payload={"body": body},
        )


def github_file_lines(
    api: GitHubApi,
    repository: str,
    path: str,
    revision: str,
) -> list[str] | None:
    encoded_path = urllib.parse.quote(path, safe="/")
    encoded_revision = urllib.parse.quote(revision, safe="")
    try:
        response = api.request(
            f"repos/{repository}/contents/{encoded_path}?ref={encoded_revision}"
        )
    except GitHubApiError as error:
        if error.status == 404:
            return None
        raise
    if (
        not isinstance(response, dict)
        or response.get("type") != "file"
        or response.get("encoding") != "base64"
        or not isinstance(response.get("content"), str)
    ):
        return None
    try:
        content = base64.b64decode(
            response["content"].replace("\n", ""),
            validate=True,
        ).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return content.splitlines()


def suggestion_applied(
    old_lines: list[str],
    new_lines: list[str],
    *,
    start_line: int,
    end_line: int,
    replacement: str,
) -> bool:
    if start_line < 1 or end_line < start_line or end_line > len(old_lines):
        return False
    replacement_lines = replacement.splitlines()
    before = old_lines[max(0, start_line - 4): start_line - 1]
    after = old_lines[end_line: end_line + 3]
    needle = before + replacement_lines + after
    if not needle:
        return False
    width = len(needle)
    matches = sum(
        1
        for position in range(0, len(new_lines) - width + 1)
        if new_lines[position: position + width] == needle
    )
    return matches == 1


def suggestion_records(
    api: GitHubApi,
    store: TelemetryStore,
    event: dict[str, Any],
    *,
    repository_id: int,
    repository: str,
) -> list[dict[str, Any]]:
    if event.get("action") != "synchronize":
        return []
    number = pull_request_number(event)
    after_sha = require_sha(event.get("after"), "synchronize after SHA")
    event_timestamp = require_timestamp(
        event["pull_request"].get("updated_at"),
        "synchronize timestamp",
    )
    suggestions = store.list_json(
        f"suggestions/pr-{number}/",
        maximum=MAX_SUGGESTIONS_PER_PR,
    )
    published = store.list_json(
        f"events/pr-{number}/review_published/",
        maximum=5000,
    )
    published_review_ids = {
        record["data"].get("review_id")
        for record in published
        if isinstance(record, dict) and isinstance(record.get("data"), dict)
    }
    applied = store.list_json(
        f"events/pr-{number}/suggestion_applied/",
        maximum=MAX_SUGGESTIONS_PER_PR,
    )
    applied_ids = {
        record["data"].get("suggestion_id")
        for record in applied
        if isinstance(record, dict) and isinstance(record.get("data"), dict)
    }

    records = []
    for suggestion in suggestions:
        if (
            not isinstance(suggestion, dict)
            or suggestion.get("review_id") not in published_review_ids
            or suggestion.get("suggestion_id") in applied_ids
        ):
            continue
        try:
            reviewed_head_sha = require_sha(
                suggestion.get("reviewed_head_sha"),
                "suggestion reviewed head SHA",
            )
            path = suggestion["path"]
            start_line = suggestion["start_line"]
            end_line = suggestion["line"]
            replacement = suggestion["replacement"]
            suggestion_id = require_identifier(
                suggestion["suggestion_id"],
                "suggestion_id",
            )
        except (KeyError, TelemetryError):
            continue
        if (
            not isinstance(path, str)
            or type(start_line) is not int
            or type(end_line) is not int
            or not isinstance(replacement, str)
        ):
            continue
        old_lines = github_file_lines(
            api,
            repository,
            path,
            reviewed_head_sha,
        )
        new_lines = github_file_lines(api, repository, path, after_sha)
        if (
            old_lines is None
            or new_lines is None
            or not suggestion_applied(
                old_lines,
                new_lines,
                start_line=start_line,
                end_line=end_line,
                replacement=replacement,
            )
        ):
            continue
        records.append(
            build_record(
                record_type="suggestion_applied",
                identity={
                    "suggestion_id": suggestion_id,
                    "applied_head_sha": after_sha,
                },
                repository_id=repository_id,
                repository=repository,
                pull_request_number=number,
                recorded_at=event_timestamp,
                data={
                    "suggestion_id": suggestion_id,
                    "review_id": suggestion["review_id"],
                    "observation_id": suggestion["observation_id"],
                    "reviewer": suggestion["reviewer"],
                    "reviewed_head_sha": reviewed_head_sha,
                    "applied_head_sha": after_sha,
                    "path": path,
                    "detected_at": event_timestamp,
                    "detection": "exact-replacement-with-context",
                },
            )
        )
    return records


def activity_records(
    event_name: str,
    event: dict[str, Any],
    *,
    repository_id: int,
    repository: str,
) -> list[dict[str, Any]]:
    if event_name == "pull_request_target":
        return [
            pull_request_event_record(
                event,
                repository_id=repository_id,
                repository=repository,
            )
        ]
    if event_name == "pull_request_review":
        return [
            human_review_record(
                event,
                repository_id=repository_id,
                repository=repository,
            )
        ]
    if event_name == "pull_request_review_comment":
        return [
            inline_comment_record(
                event,
                repository_id=repository_id,
                repository=repository,
            )
        ]
    if event_name == "issue_comment":
        return [
            conversation_comment_record(
                event,
                repository_id=repository_id,
                repository=repository,
            )
        ]
    raise TelemetryError(f"unsupported telemetry event: {event_name}")


def main() -> int:
    try:
        event_name = require_environment("GITHUB_EVENT_NAME")
        repository = require_environment("GITHUB_REPOSITORY")
        repository_id = require_repository_id(
            require_environment("GITHUB_REPOSITORY_ID")
        )
        event = json.loads(
            Path(require_environment("GITHUB_EVENT_PATH")).read_text(
                encoding="utf-8"
            )
        )
        if not isinstance(event, dict):
            raise TelemetryError("GitHub event payload must be an object")
        api = GitHubApi()
        store = TelemetryStore(repository, api=api)
        records = activity_records(
            event_name,
            event,
            repository_id=repository_id,
            repository=repository,
        )
        records.extend(
            suggestion_records(
                api,
                store,
                event,
                repository_id=repository_id,
                repository=repository,
            )
            if event_name == "pull_request_target"
            else []
        )
        verdict = parse_verdict_command(event_name, event)
        verdict_reply = None
        if verdict is not None:
            try:
                verdict_value, verdict_reply = verdict_record(
                    api,
                    store,
                    event_name,
                    event,
                    repository_id=repository_id,
                    repository=repository,
                )
                records.append(verdict_value)
            except VerdictRejected as error:
                print(f"::warning::Ignoring AI verdict: {error}")
        store.write_records(
            records,
            message=(
                f"Record {event_name} telemetry for pull request "
                f"{pull_request_number(event)}"
            ),
        )
        if verdict_reply is not None:
            acknowledge_verdict(
                api,
                repository,
                pull_request_number(event),
                records[-1],
                verdict_reply,
            )
    except (
        GitHubApiError,
        json.JSONDecodeError,
        OSError,
        subprocess.CalledProcessError,
        TelemetryError,
    ) as error:
        print(f"failed to record AI review activity: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
