#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

from ai_review_telemetry import TelemetryError, TelemetryStore, validate_record


def export_records(records: list[object]) -> list[dict]:
    validated = [validate_record(record) for record in records]
    return sorted(
        validated,
        key=lambda record: (
            record["data"].get("event_timestamp", record["recorded_at"]),
            record["recorded_at"],
            record["record_id"],
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        records = export_records(
            TelemetryStore(args.repository).list_json(
                "events/",
                maximum=100_000,
            )
        )
        output = sys.stdout
        if args.output is not None:
            output = args.output.open("w", encoding="utf-8")
        try:
            for record in records:
                print(
                    json.dumps(
                        record,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    file=output,
                )
        finally:
            if output is not sys.stdout:
                output.close()
    except (OSError, TelemetryError) as error:
        print(f"failed to export AI review telemetry: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
