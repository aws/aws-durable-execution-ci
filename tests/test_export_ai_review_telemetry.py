#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from ai_review_telemetry import build_record
from export_ai_review_telemetry import export_records


class ExportAiReviewTelemetryTest(unittest.TestCase):
    def test_orders_records_by_event_then_record_timestamp(self):
        later = build_record(
            record_type="review_published",
            identity={"review": 2},
            repository_id=123,
            repository="aws/example",
            pull_request_number=42,
            recorded_at="2026-08-25T12:02:00Z",
            data={},
        )
        earlier_event = build_record(
            record_type="pull_request_event",
            identity={"event": 1},
            repository_id=123,
            repository="aws/example",
            pull_request_number=42,
            recorded_at="2026-08-25T12:03:00Z",
            data={"event_timestamp": "2026-08-25T12:00:00Z"},
        )

        self.assertEqual(
            export_records([later, earlier_event]),
            [earlier_event, later],
        )


if __name__ == "__main__":
    unittest.main()
