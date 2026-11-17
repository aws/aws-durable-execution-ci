#!/usr/bin/env python3

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

# Comments from these associations come from the team and are not flagged.
TEAM_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
GITHUB_PAGE_SIZE = 100
REPLY_MARKER = "<!-- aws-durable-execution-ci:closed-issue-comment -->"
GENERATED_REPLY_AUTHOR = "github-actions[bot]"


class ClosedIssueCommentError(ValueError):
    pass


def require_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value.strip():
        raise ClosedIssueCommentError(f"{name} must be set")
    return value


def repository() -> str:
    value = require_environment("GITHUB_REPOSITORY")
    if value.count("/") != 1 or any(not part for part in value.split("/")):
        raise ClosedIssueCommentError(
            "GITHUB_REPOSITORY must be an owner/repository name"
        )
    return value


def reply_after_days() -> int:
    raw = require_environment("CLOSED_ISSUE_REPLY_AFTER_DAYS")
    try:
        days = int(raw)
    except ValueError as error:
        raise ClosedIssueCommentError(
            "CLOSED_ISSUE_REPLY_AFTER_DAYS must be a non-negative integer"
        ) from error
    if days < 0:
        raise ClosedIssueCommentError(
            "CLOSED_ISSUE_REPLY_AFTER_DAYS must be a non-negative integer"
        )
    return days


def load_event() -> dict[str, Any]:
    path = require_environment("GITHUB_EVENT_PATH")
    try:
        with open(path, encoding="utf-8") as handle:
            event = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise ClosedIssueCommentError("failed to read the event payload") from error
    if not isinstance(event, dict):
        raise ClosedIssueCommentError("event payload must be a JSON object")
    return event


def reply_comment() -> str:
    return (
        f"{REPLY_MARKER}\n\n"
        "This issue is closed, so comments here are hard for our team to "
        "see. If you still need help, please open a new issue and link to "
        "this one."
    )


def gh_run_json(
    arguments: list[str], *, input_value: Any | None = None
) -> Any:
    encoded_input = None
    if input_value is not None:
        encoded_input = json.dumps(input_value, separators=(",", ":"))

    try:
        result = subprocess.run(
            ["gh", "api", *arguments],
            check=False,
            input=encoded_input,
            text=True,
            capture_output=True,
        )
    except OSError as error:
        raise ClosedIssueCommentError("failed to run the GitHub CLI") from error

    if result.returncode != 0:
        message = result.stderr.strip() or "GitHub API request failed"
        raise ClosedIssueCommentError(message)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ClosedIssueCommentError("GitHub API returned invalid JSON") from error


def gh_get_comments_since(
    repo: str, issue_number: int, since: str
) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    page = 1
    while True:
        response = gh_run_json(
            [
                f"repos/{repo}/issues/{issue_number}/comments"
                f"?since={quote(since)}"
                f"&per_page={GITHUB_PAGE_SIZE}&page={page}"
            ]
        )
        if not isinstance(response, list):
            raise ClosedIssueCommentError("GitHub returned an invalid comment list")

        comments.extend(c for c in response if isinstance(c, dict))

        if len(response) < GITHUB_PAGE_SIZE:
            return comments
        page += 1


def gh_post_comment(repo: str, issue_number: int, body: str) -> None:
    gh_run_json(
        [
            "--method",
            "POST",
            f"repos/{repo}/issues/{issue_number}/comments",
            "--input",
            "-",
        ],
        input_value={"body": body},
    )


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_generated_reply(comment: dict[str, Any]) -> bool:
    body = comment.get("body")
    if not isinstance(body, str) or REPLY_MARKER not in body:
        return False
    user = comment.get("user")
    return isinstance(user, dict) and user.get("login") == GENERATED_REPLY_AUTHOR


def has_generated_reply_since(
    comments: list[dict[str, Any]], since: datetime
) -> bool:
    for comment in comments:
        if not is_generated_reply(comment):
            continue
        created = parse_timestamp(comment.get("created_at"))
        if created is not None and created >= since:
            return True
    return False


def is_outside_comment(comment: dict[str, Any]) -> bool:
    if comment.get("author_association") in TEAM_ASSOCIATIONS:
        return False
    user = comment.get("user")
    if not isinstance(user, dict):
        return False
    return user.get("type") != "Bot"


def write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def run() -> bool:
    """Handle one issue_comment event. Returns True when Slack should be pinged."""
    repo = repository()
    days = reply_after_days()
    event = load_event()

    issue = event.get("issue")
    comment = event.get("comment")
    if not isinstance(issue, dict) or not isinstance(comment, dict):
        raise ClosedIssueCommentError("event payload has no issue or comment")

    number = issue.get("number")
    if not isinstance(number, int):
        raise ClosedIssueCommentError("event payload has no issue number")

    if "pull_request" in issue:
        print(f"#{number}: pull request comment, skipping.")
        return False
    if issue.get("state") != "closed":
        print(f"#{number}: issue is open, skipping.")
        return False
    if not is_outside_comment(comment):
        print(f"#{number}: comment is from the team or a bot, skipping.")
        return False

    closed_at = parse_timestamp(issue.get("closed_at"))
    if closed_at is None:
        raise ClosedIssueCommentError("closed issue has no valid closed_at")

    age = datetime.now(timezone.utc) - closed_at
    if age < timedelta(days=days):
        print(
            f"#{number}: closed {age.days}d ago, under {days}d; "
            "no reply posted."
        )
        return True

    comments = gh_get_comments_since(repo, number, issue["closed_at"])
    if has_generated_reply_since(comments, closed_at):
        print(f"#{number}: reply already posted since close.")
        return True

    gh_post_comment(repo, number, reply_comment())
    print(f"#{number}: reply posted.")
    return True


def main() -> int:
    try:
        notify = run()
    except ClosedIssueCommentError as error:
        print(f"closed issue comment check failed: {error}", file=sys.stderr)
        return 1
    write_output("notify", "true" if notify else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
