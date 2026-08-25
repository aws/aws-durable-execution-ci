#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ai_review_telemetry import (
    TelemetryError,
    TelemetryStore,
    require_finding_key,
    require_identifier,
    require_pull_request_number,
    require_sha,
)


MAX_PRIOR_FINDINGS = 100


def prior_findings(
    records: list[Any],
    *,
    reviewer: str,
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        number = require_pull_request_number(args.pull_request_number)
        store = TelemetryStore(args.repository)
        records = store.list_json(
            f"events/pr-{number}/finding_observed/",
            maximum=2000,
        )
        findings = prior_findings(records, reviewer=args.reviewer)
        args.output.write_text(
            json.dumps(
                findings,
                ensure_ascii=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
    except (OSError, TelemetryError) as error:
        print(f"failed to load prior AI findings: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
