#!/usr/bin/env python3
"""Build batches of recent failed AI review runs for retry."""

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def parse_github_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z"))


def recent_failed_runs(
    runs: list[dict[str, Any]],
    cutoff: datetime,
) -> list[dict[str, Any]]:
    return [
        run
        for run in runs
        if run["status"] == "completed"
        and run["conclusion"] == "failure"
        and parse_github_timestamp(run["updated_at"]) >= cutoff
    ]


def build_rerun_batches(
    runs: list[dict[str, Any]],
    batch_size: int,
) -> list[list[int]]:
    run_ids = [int(run["id"]) for run in runs]
    return [
        run_ids[start : start + batch_size - 1]
        for start in range(0, len(run_ids), batch_size)
    ]


def build_plan(
    input_path: Path,
    max_age_days: int,
    batch_size: int,
) -> dict[str, Any]:
    runs = json.loads(input_path.read_text(encoding="utf-8"))
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    failed_runs = recent_failed_runs(runs, cutoff)

    return {
        "failed_run_count": len(failed_runs),
        "rerun_batches": build_rerun_batches(failed_runs, batch_size),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--max-age-days", type=int, default=7)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()

    print(
        json.dumps(
            build_plan(args.input, args.max_age_days, args.batch_size),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
