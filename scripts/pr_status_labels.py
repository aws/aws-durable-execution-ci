#!/usr/bin/env python3
"""Keep a pull request's review status labels current.

Adds needs-review to a new pull request whose author has no write access,
and replaces changes-requested with needs-review when the author pushes an
update or the pull request is marked ready for review.
"""

import json
import os
import subprocess
import sys
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any


REVIEW_LABEL = "needs-review"
REVIEW_LABEL_COLOR = "d4c5f9"
REVIEW_LABEL_DESCRIPTION = "Awaiting maintainer review"
CHANGES_REQUESTED_LABEL = "changes-requested"
STATUS_LABELS = frozenset(
    (
        "needs-review",
        "changes-requested",
        "needs-rebase",
        "do-not-merge",
        "ready-to-merge",
    )
)
OPENING_ACTIONS = frozenset(("opened", "reopened", "ready_for_review"))
# The permission field reports the maintain role as write.
WRITE_PERMISSIONS = frozenset(("admin", "write"))
GITHUB_PAGE_SIZE = 100


class StatusLabelError(ValueError):
    pass


@dataclass(frozen=True)
class LabelChange:
    add: tuple[str, ...] = ()
    remove: tuple[str, ...] = ()


def require_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value.strip():
        raise StatusLabelError(f"{name} must be set")
    return value


def repository_name() -> str:
    repository = require_environment("GITHUB_REPOSITORY")
    if repository.count("/") != 1 or any(
        not component for component in repository.split("/")
    ):
        raise StatusLabelError(
            "GITHUB_REPOSITORY must be an owner/repository name"
        )
    return repository


def pull_request_number() -> int:
    raw_number = require_environment("PULL_REQUEST_NUMBER")
    try:
        number = int(raw_number)
    except ValueError as error:
        raise StatusLabelError(
            "PULL_REQUEST_NUMBER must be a positive integer"
        ) from error
    if number < 1:
        raise StatusLabelError(
            "PULL_REQUEST_NUMBER must be a positive integer"
        )
    return number


def run_gh_json(
    arguments: list[str],
    *,
    input_value: Any | None = None,
    allow_not_found: bool = False,
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
        raise StatusLabelError("failed to run the GitHub CLI") from error
    if result.returncode != 0:
        message = result.stderr.strip() or "GitHub API request failed"
        if allow_not_found and "HTTP 404" in message:
            return None
        raise StatusLabelError(message)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise StatusLabelError("GitHub API returned invalid JSON") from error


def parse_timestamp(value: Any, description: str) -> datetime:
    error_message = f"{description} must be an ISO 8601 timestamp"
    if not isinstance(value, str):
        raise StatusLabelError(error_message)
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise StatusLabelError(error_message) from error
    if timestamp.tzinfo is None:
        raise StatusLabelError(error_message)
    return timestamp


def event_time() -> datetime:
    # The payload's updated_at is when the push or the ready-for-review
    # happened. A re-run reuses the original payload.
    return parse_timestamp(
        require_environment("EVENT_UPDATED_AT"),
        "EVENT_UPDATED_AT",
    )


def is_bot_user(user: dict[str, Any]) -> bool:
    return (
        user.get("type") == "Bot"
        or user["login"].casefold().endswith("[bot]")
    )


def normalize_pull_request(value: Any, number: int) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("number") != number:
        raise StatusLabelError("GitHub returned the wrong pull request")

    state = value.get("state")
    draft = value.get("draft")
    user = value.get("user")
    labels = value.get("labels")
    if not isinstance(state, str) or not isinstance(draft, bool):
        raise StatusLabelError("GitHub returned an invalid pull request state")
    if not isinstance(user, dict) or not isinstance(user.get("login"), str):
        raise StatusLabelError(
            "GitHub returned a pull request without an author"
        )
    if not isinstance(labels, list) or any(
        not isinstance(label, dict) or not isinstance(label.get("name"), str)
        for label in labels
    ):
        raise StatusLabelError("GitHub returned invalid pull request labels")

    return {
        "state": state,
        "draft": draft,
        "user": user,
        # Casefolded name -> the name as spelled on the pull request.
        "labels": {
            label["name"].casefold(): label["name"] for label in labels
        },
    }


def plan_label_change(
    action: str,
    pull_request: dict[str, Any],
    sender: str,
    requested_before_event: Callable[[], bool],
    author_can_write: Callable[[], bool],
) -> LabelChange:
    if (
        pull_request["state"] != "open"
        or pull_request["draft"]
        or is_bot_user(pull_request["user"])
    ):
        return LabelChange()

    labels = pull_request["labels"]
    pushed_by_author = (
        action == "synchronize"
        and sender.casefold() == pull_request["user"]["login"].casefold()
    )
    if CHANGES_REQUESTED_LABEL in labels and (
        pushed_by_author or action == "ready_for_review"
    ):
        # A job that starts late, or a re-run of an old run, must keep a
        # request that a maintainer made after the event.
        if not requested_before_event():
            return LabelChange()
        return LabelChange(
            add=() if REVIEW_LABEL in labels else (REVIEW_LABEL,),
            remove=(labels[CHANGES_REQUESTED_LABEL],),
        )

    # The checks are API calls, so they run only when needed.
    if (
        action in OPENING_ACTIONS
        and STATUS_LABELS.isdisjoint(labels)
        and not author_can_write()
    ):
        return LabelChange(add=(REVIEW_LABEL,))
    return LabelChange()


def has_write_permission(repository: str, login: str) -> bool:
    encoded_login = urllib.parse.quote(login, safe="")
    permission = run_gh_json(
        [f"repos/{repository}/collaborators/{encoded_login}/permission"]
    )
    return (
        isinstance(permission, dict)
        and permission.get("permission") in WRITE_PERMISSIONS
    )


def latest_label_application(
    repository: str, number: int, label: str
) -> datetime | None:
    latest = None
    page = 1
    while True:
        events = run_gh_json(
            [
                f"repos/{repository}/issues/{number}/events"
                f"?per_page={GITHUB_PAGE_SIZE}&page={page}"
            ]
        )
        if not isinstance(events, list):
            raise StatusLabelError("GitHub returned invalid issue events")
        for event in events:
            if not isinstance(event, dict) or event.get("event") != "labeled":
                continue
            event_label = event.get("label")
            if (
                not isinstance(event_label, dict)
                or not isinstance(event_label.get("name"), str)
                or event_label["name"].casefold() != label.casefold()
            ):
                continue
            applied_at = parse_timestamp(
                event.get("created_at"),
                "GitHub label event time",
            )
            if latest is None or applied_at > latest:
                latest = applied_at
        if len(events) < GITHUB_PAGE_SIZE:
            return latest
        page += 1


def applied_before(
    repository: str, number: int, label: str, moment: datetime
) -> bool:
    applied_at = latest_label_application(repository, number, label)
    if applied_at is None:
        print(f"Keeping {label}: GitHub has no record of applying it.")
        return False
    # Timestamps have one-second precision, so a tie keeps the request.
    if applied_at >= moment:
        print(f"Keeping {label}: it was not applied before this event.")
        return False
    return True


def ensure_review_label(repository: str) -> None:
    encoded_label = urllib.parse.quote(REVIEW_LABEL, safe="")
    label_path = f"repos/{repository}/labels/{encoded_label}"
    if run_gh_json([label_path], allow_not_found=True) is not None:
        return
    try:
        run_gh_json(
            ["--method", "POST", f"repos/{repository}/labels", "--input", "-"],
            input_value={
                "name": REVIEW_LABEL,
                "color": REVIEW_LABEL_COLOR,
                "description": REVIEW_LABEL_DESCRIPTION,
            },
        )
    except StatusLabelError:
        # A concurrent run may have created the label first.
        if run_gh_json([label_path], allow_not_found=True) is None:
            raise


def apply_label_change(
    repository: str, number: int, change: LabelChange
) -> None:
    if not change.add and not change.remove:
        print("No status label change.")
        return

    # Add before removing, so a failure leaves a status label in place.
    if change.add:
        ensure_review_label(repository)
        run_gh_json(
            [
                "--method",
                "POST",
                f"repos/{repository}/issues/{number}/labels",
                "--input",
                "-",
            ],
            input_value={"labels": list(change.add)},
        )
        print(f"Added {', '.join(change.add)}.")

    for label in change.remove:
        encoded_label = urllib.parse.quote(label, safe="")
        # A label that is already gone needs no removal.
        run_gh_json(
            [
                "--method",
                "DELETE",
                f"repos/{repository}/issues/{number}/labels/{encoded_label}",
            ],
            allow_not_found=True,
        )
        print(f"Removed {label}.")


def main() -> int:
    try:
        repository = repository_name()
        number = pull_request_number()
        action = require_environment("EVENT_ACTION")
        sender = require_environment("EVENT_SENDER")
        moment = event_time()
        pull_request = normalize_pull_request(
            run_gh_json([f"repos/{repository}/pulls/{number}"]),
            number,
        )
        change = plan_label_change(
            action,
            pull_request,
            sender,
            lambda: applied_before(
                repository,
                number,
                CHANGES_REQUESTED_LABEL,
                moment,
            ),
            lambda: has_write_permission(
                repository,
                pull_request["user"]["login"],
            ),
        )
        apply_label_change(repository, number, change)
    except StatusLabelError as error:
        print(f"PR status labels failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
