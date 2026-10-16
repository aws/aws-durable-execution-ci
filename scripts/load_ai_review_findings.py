#!/usr/bin/env python3

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_review_telemetry import (
    GitHubApiError,
    SCHEMA_VERSION,
    TelemetryError,
    TelemetryStore,
    VERDICT_OUTCOMES,
    require_finding_id,
    require_finding_key,
    require_identifier,
    require_pull_request_number,
    require_sha,
    require_timestamp,
    validate_record,
)


MAX_PRIOR_FINDINGS = 100
MAX_STORED_RECORDS = 2000


def finding_decisions(
    records: list[Any],
    *,
    repository: str,
    pull_request_number: int,
    reviewer: str,
) -> dict[str, str]:
    """Return the latest explicit verdict for each finding of this reviewer.

    Select by parsed command timestamp, then greatest record ID for ties.
    Ignore records from the other reviewer.
    Return an empty map when no verdicts apply.
    Raise TelemetryError for invalid remaining records or mismatched scope.

    The model catalog is bounded, so this map retains decisions independently
    of catalog membership.
    """
    if reviewer not in {"claude", "codex"}:
        raise TelemetryError("AI reviewer is invalid")
    other_reviewer = "claude" if reviewer == "codex" else "codex"
    latest: dict[str, tuple[datetime, str, str]] = {}
    for record in records:
        data = record.get("data") if isinstance(record, dict) else None
        if isinstance(data, dict) and data.get("reviewer") == other_reviewer:
            continue
        validated = validate_record(record)
        if (
            validated["record_type"] != "finding_verdict"
            or validated["repository"]["full_name"] != repository
            or validated["pull_request"]["number"] != pull_request_number
            or validated["data"].get("reviewer") != reviewer
        ):
            raise TelemetryError("finding verdict scope is invalid")
        data = validated["data"]
        finding_id = require_finding_id(data.get("finding_id"))
        outcome = data.get("outcome")
        if not isinstance(outcome, str) or outcome not in VERDICT_OUTCOMES:
            raise TelemetryError("finding verdict outcome is invalid")
        timestamp = require_timestamp(
            data.get("command_created_at"),
            "verdict command timestamp",
        )
        record_id = validated["record_id"]
        if not record_id.startswith(f"art_v{SCHEMA_VERSION}_"):
            raise TelemetryError("finding verdict record_id is invalid")
        candidate = (
            datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
            record_id,
            outcome,
        )
        previous = latest.get(finding_id)
        if previous is None or candidate[:2] > previous[:2]:
            latest[finding_id] = candidate
    return {finding_id: verdict[2] for finding_id, verdict in latest.items()}


def prior_findings(
    records: list[Any],
    *,
    reviewer: str,
    decisions: dict[str, str] | None = None,
    maximum: int = MAX_PRIOR_FINDINGS,
) -> list[dict[str, Any]]:
    if reviewer not in {"claude", "codex"}:
        raise TelemetryError("AI reviewer is invalid")
    if maximum < 1:
        raise TelemetryError("maximum prior findings must be positive")

    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        if (
            not isinstance(record, dict)
            or record.get("record_type") != "finding_observed"
            or not isinstance(record.get("data"), dict)
        ):
            continue
        data = record["data"]
        if data.get("reviewer") != reviewer:
            continue
        try:
            finding_id = require_identifier(
                data.get("finding_id"),
                "finding_id",
            )
            finding_key = require_finding_key(
                data.get("identity_key", data.get("finding_key"))
            )
            reviewed_head_sha = require_sha(
                data.get("reviewed_head_sha"),
                "reviewed head SHA",
            )
        except TelemetryError:
            continue
        path = data.get("path")
        body = data.get("body")
        observed_at = data.get("published_at") or record.get("recorded_at")
        if (
            not isinstance(path, str)
            or not path
            or not isinstance(body, str)
            or not body
            or not isinstance(observed_at, str)
        ):
            continue
        candidate = {
            "finding_id": finding_id,
            "finding_key": finding_key,
            "path": path,
            "body": body,
            "reviewed_head_sha": reviewed_head_sha,
            "observed_at": observed_at,
            "status": (decisions or {}).get(finding_id, "open"),
        }
        previous = latest.get(finding_id)
        if previous is None or candidate["observed_at"] > previous["observed_at"]:
            latest[finding_id] = candidate

    return sorted(
        latest.values(),
        key=lambda finding: (
            finding["observed_at"],
            finding["finding_id"],
        ),
        reverse=True,
    )[:maximum]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pull-request-number", required=True)
    parser.add_argument("--reviewer", required=True, choices=("claude", "codex"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--decisions-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        number = require_pull_request_number(args.pull_request_number)
        store = TelemetryStore(args.repository)
        records = store.list_json(
            f"events/pr-{number}/finding_observed/",
            maximum=MAX_STORED_RECORDS,
        )
        verdicts = store.list_json(
            f"events/pr-{number}/finding_verdict/",
            maximum=MAX_STORED_RECORDS,
        )
        decisions = finding_decisions(
            verdicts,
            repository=args.repository,
            pull_request_number=number,
            reviewer=args.reviewer,
        )
        findings = prior_findings(
            records,
            reviewer=args.reviewer,
            decisions=decisions,
        )
        args.output.write_text(
            json.dumps(
                findings,
                ensure_ascii=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        if args.decisions_output is not None:
            args.decisions_output.write_text(
                json.dumps(decisions, ensure_ascii=True, separators=(",", ":"))
                + "\n",
                encoding="utf-8",
            )
    except (OSError, TelemetryError, GitHubApiError) as error:
        print(f"failed to load prior AI findings: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
