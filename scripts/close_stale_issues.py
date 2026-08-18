#!/usr/bin/env python3

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

# Only issues with this label are considered by this workflow
TARGET_LABEL: str = "needs-info"

CLOSE_REASONS = frozenset({"completed", "not_planned", "duplicate"})
GITHUB_PAGE_SIZE = 100



class StaleIssueError(ValueError):
    pass


def require_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value.strip():
        raise StaleIssueError(f"{name} must be set")
    return value


def repository() -> str:
    value = require_environment("GITHUB_REPOSITORY")
    if value.count("/") != 1 or any(not part for part in value.split("/")):
        raise StaleIssueError(
            "GITHUB_REPOSITORY must be an owner/repository name"
        )
    return value


def days_until_close() -> int:
    raw = require_environment("STALE_DAYS_UNTIL_CLOSE")
    try:
        days = int(raw)
    except ValueError as error:
        raise StaleIssueError(
            "STALE_DAYS_UNTIL_CLOSE must be a positive integer"
        ) from error
    if days < 1:
        raise StaleIssueError(
            "STALE_DAYS_UNTIL_CLOSE must be a positive integer"
        )
    return days


def close_reason() -> str:
    reason = require_environment("STALE_CLOSE_REASON")
    if reason not in CLOSE_REASONS:
        raise StaleIssueError(
            "STALE_CLOSE_REASON must be one of "
            f"{sorted(CLOSE_REASONS)}"
        )
    return reason


def close_comment(days: int) -> str:
    day_label = "day" if days == 1 else "days"
    return (
        "This issue has been automatically closed because it was labeled "
        f"`{TARGET_LABEL}` and has not received a response in "
        f"{days} {day_label}. If you still need help, please comment with "
        "the requested details and we will be happy to reopen it."
    )


def run_gh_json(
    arguments: list[str], *, input_value: Any | None = None
) -> Any:
    """Run `gh api` with optional JSON input and return parsed JSON output.

    Converts CLI launch failures, non-zero exits, and invalid JSON responses
    into StaleIssueError so callers get consistent workflow-facing errors.
    """
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
        raise StaleIssueError("failed to run the GitHub CLI") from error

    if result.returncode != 0:
        message = result.stderr.strip() or "GitHub API request failed"
        raise StaleIssueError(message)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise StaleIssueError("GitHub API returned invalid JSON") from error


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


def get_gh_open_issues(repo: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    page = 1
    while True:
        response = run_gh_json(
            [
                f"repos/{repo}/issues"
                f"?state=open&labels={TARGET_LABEL}"
                f"&per_page={GITHUB_PAGE_SIZE}&page={page}"
            ]
        )
        if not isinstance(response, list):
            raise StaleIssueError("GitHub returned an invalid issue list")
        
        for issue in response:
            if isinstance(issue, dict) and "pull_request" not in issue:
                issues.append(issue)

        if len(response) < GITHUB_PAGE_SIZE:
            return issues
        page += 1


def get_gh_issue_events(repo: str, issue_number: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    page = 1
    while True:
        response = run_gh_json(
            [
                f"repos/{repo}/issues/{issue_number}/timeline"
                f"?per_page={GITHUB_PAGE_SIZE}&page={page}"
            ]
        )
        if not isinstance(response, list):
            raise StaleIssueError("GitHub returned an invalid issue timeline")

        events.extend(event for event in response if isinstance(event, dict))

        if len(response) < GITHUB_PAGE_SIZE:
            return events
        page += 1


def latest_label_applied_at(events: list[dict[str, Any]]) -> datetime | None:
    """Return the most recent time the given label was applied to an issue.

    Ignores non-label timeline events, malformed label payloads, labels with a
    different name, and events whose timestamps cannot be parsed.
    """
    latest: datetime | None = None

    for event in events:
        if event.get("event") != "labeled":
            continue

        event_label = event.get("label")
        if not isinstance(event_label, dict):
            continue

        name = event_label.get("name")
        if not isinstance(name, str) or name.casefold() != TARGET_LABEL:
            continue

        applied_time = parse_timestamp(event.get("created_at"))
        if applied_time is not None and (latest is None or applied_time > latest):
            latest = applied_time

    return latest


def latest_comment_at(events: list[dict[str, Any]]) -> datetime | None:
    latest: datetime | None = None
    for event in events:
        if event.get("event") != "commented":
            continue
        commented = parse_timestamp(event.get("created_at"))
        if commented is not None and (latest is None or commented > latest):
            latest = commented
    return latest


def close_gh_issue(repo: str, issue_number: int, reason: str) -> None:
    run_gh_json(
        [
            "--method",
            "PATCH",
            f"repos/{repo}/issues/{issue_number}",
            "--input",
            "-",
        ],
        input_value={"state": "closed", "state_reason": reason},
    )


def post_gh_comment(repo: str, issue_number: int, body: str) -> None:
    run_gh_json(
        [
            "--method",
            "POST",
            f"repos/{repo}/issues/{issue_number}/comments",
            "--input",
            "-",
        ],
        input_value={"body": body},
    )


def run() -> None:
    repo = repository()
    days = days_until_close()
    reason = close_reason()
    comment = close_comment(days)

    now = datetime.now(timezone.utc)
    cutoff = timedelta(days=days)

    issues = get_gh_open_issues(repo)
    print(f"Found {len(issues)} open issue(s) labeled '{TARGET_LABEL}' in {repo}.")

    closed = 0
    for issue in issues:
        number = issue.get("number")
        if not isinstance(number, int):
            continue

        events = get_gh_issue_events(repo, number)
        label_applied_time = latest_label_applied_at(events)
        if label_applied_time is None:
            print(f"#{number}: no '{TARGET_LABEL}' labeled event found.")
            continue

        commented_time = latest_comment_at(events)
        last_activity_time = label_applied_time
        if commented_time is not None:
            last_activity_time = max(label_applied_time, commented_time)
        age = now - last_activity_time

        if age < cutoff:
            remaining = cutoff - age
            print(
                f"#{number}: last activity {age.days}d ago; "
                f"{remaining.days}d until close — skipping."
            )
            continue

        post_gh_comment(repo, number, comment)
        close_gh_issue(repo, number, reason)
        print(f"#{number}: closed ({age.days}d without response).")
        closed += 1

    print(f"{closed} issue(s) closed.")


def main() -> int:
    try:
        run()
    except StaleIssueError as error:
        print(f"stale issue closer failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
