#!/usr/bin/env python3

import base64
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from ai_review_telemetry import (
    GitHubApiError,
    GitTree,
    TelemetryConflictError,
    TelemetryError,
    TelemetryStore,
    build_record,
    canonical_json_bytes,
    finding_path,
    git_blob_sha,
    record_path,
    require_finding_key,
    stable_identifier,
)


SHA_1 = "1" * 40
SHA_2 = "2" * 40
SHA_3 = "3" * 40


class AiReviewTelemetryTest(unittest.TestCase):
    def test_canonical_identifiers_ignore_object_key_order(self):
        first, first_digest = stable_identifier(
            "arf",
            {"reviewer": "codex", "key": "path::symbol::invariant"},
        )
        second, second_digest = stable_identifier(
            "arf",
            {"key": "path::symbol::invariant", "reviewer": "codex"},
        )

        self.assertEqual(first, second)
        self.assertEqual(first_digest, second_digest)
        self.assertRegex(first, r"^arf_v1_[a-z2-7]{26}$")
        self.assertEqual(len(first_digest), 64)

    def test_finding_keys_are_bounded_and_location_independent(self):
        self.assertEqual(
            require_finding_key("scripts/review.py::publish::stale-head"),
            "scripts/review.py::publish::stale-head",
        )
        for invalid in (
            "",
            "UPPERCASE",
            "has spaces",
            "line-42#wrong",
            "x" * 257,
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(TelemetryError):
                    require_finding_key(invalid)

    def test_records_have_deterministic_paths_and_closed_envelopes(self):
        record = build_record(
            record_type="finding_observed",
            identity={"observation_id": "aro_v1_" + "a" * 26},
            repository_id=123,
            repository="aws/example",
            pull_request_number=42,
            recorded_at="2026-08-25T12:00:00Z",
            data={"finding_id": "arf_v1_" + "b" * 26},
        )

        self.assertEqual(
            record_path(record),
            f"events/pr-42/finding_observed/{record['record_id']}.json",
        )
        self.assertEqual(
            canonical_json_bytes(record),
            canonical_json_bytes(json.loads(canonical_json_bytes(record))),
        )
        self.assertEqual(
            finding_path(42, "codex", "arf_v1_" + "c" * 26),
            "findings/pr-42/codex/arf_v1_" + "c" * 26 + ".json",
        )

    def test_store_creates_an_orphan_branch_for_the_first_write(self):
        store = TelemetryStore("aws/example", api=Mock())
        content = b'{"schema_version":1}\n'

        with (
            patch.object(store, "_load_tree", return_value=None),
            patch.object(store, "_create_tree", return_value=SHA_1) as tree,
            patch.object(store, "_create_commit", return_value=SHA_2) as commit,
            patch.object(store, "_create_ref") as create_ref,
        ):
            store.write_files(
                {"events/pr-42/test/record.json": content},
                message="Record telemetry",
            )

        tree.assert_called_once_with(
            {"events/pr-42/test/record.json": content},
            base_tree=None,
        )
        commit.assert_called_once_with(
            SHA_1,
            "Record telemetry",
            parent=None,
        )
        create_ref.assert_called_once_with(SHA_2)

    def test_store_is_idempotent_and_rejects_conflicting_rewrites(self):
        path = "events/pr-42/test/record.json"
        content = b'{"record":"same"}\n'
        current = GitTree(SHA_1, SHA_2, {path: git_blob_sha(content)})
        store = TelemetryStore("aws/example", api=Mock())

        with patch.object(store, "_load_tree", return_value=current), patch.object(
            store,
            "_create_tree",
        ) as create_tree:
            store.write_files({path: content}, message="Retry telemetry")

        create_tree.assert_not_called()

        with patch.object(store, "_load_tree", return_value=current):
            with self.assertRaises(TelemetryConflictError):
                store.write_files(
                    {path: b'{"record":"different"}\n'},
                    message="Conflicting telemetry",
                )

    def test_store_retries_a_concurrent_branch_creation(self):
        path = "events/pr-42/test/record.json"
        content = b'{"record":"value"}\n'
        current = GitTree(SHA_1, SHA_2, {})
        store = TelemetryStore("aws/example", api=Mock())

        with (
            patch.object(store, "_load_tree", side_effect=[None, current]),
            patch.object(
                store,
                "_create_tree",
                side_effect=[SHA_1, SHA_2],
            ),
            patch.object(
                store,
                "_create_commit",
                side_effect=[SHA_2, SHA_3],
            ),
            patch.object(
                store,
                "_create_ref",
                side_effect=GitHubApiError("conflict", 422),
            ),
            patch.object(store, "_update_ref") as update_ref,
        ):
            store.write_files({path: content}, message="Record telemetry")

        update_ref.assert_called_once_with(SHA_3)

    def test_read_json_rejects_non_json_blob_content(self):
        path = "events/pr-42/test/record.json"
        api = Mock()
        api.request.return_value = {
            "encoding": "base64",
            "content": base64.b64encode(b"not json").decode("ascii"),
        }
        store = TelemetryStore("aws/example", api=api)
        with patch.object(
            store,
            "_load_tree",
            return_value=GitTree(SHA_1, SHA_2, {path: SHA_3}),
        ):
            with self.assertRaisesRegex(TelemetryError, "valid JSON"):
                store.read_json(path)

    def test_read_json_accepts_wrapped_base64_blob_content(self):
        path = "events/pr-42/test/record.json"
        encoded = base64.b64encode(b'{"record":"value"}').decode("ascii")
        api = Mock()
        api.request.return_value = {
            "encoding": "base64",
            "content": f"{encoded[:8]}\n{encoded[8:]}\n",
        }
        store = TelemetryStore("aws/example", api=api)
        with patch.object(
            store,
            "_load_tree",
            return_value=GitTree(SHA_1, SHA_2, {path: SHA_3}),
        ):
            self.assertEqual(store.read_json(path), {"record": "value"})


if __name__ == "__main__":
    unittest.main()
