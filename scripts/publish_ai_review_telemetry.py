#!/usr/bin/env python3

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from ai_review_telemetry import (
    SCHEMA_VERSION,
    TelemetryError,
    TelemetryStore,
    build_record,
    finding_path,
    require_identifier,
    require_pull_request_number,
    require_repository_id,
    require_sha,
    require_timestamp,
    suggestion_path,
    utc_now,
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_lines(path: Path | None) -> list[Any]:
    if path is None or not path.exists():
        return []
    values = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line:
            continue
        try:
            values.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise TelemetryError(
                f"{path} line {line_number} is not valid JSON"
            ) from error
    return values


def prepared_telemetry(prepared: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(prepared, dict) or set(prepared) != {
        "summary",
        "comments",
        "telemetry",
    }:
        raise TelemetryError("prepared review has invalid top-level fields")
    telemetry = prepared["telemetry"]
    if not isinstance(telemetry, dict) or set(telemetry) != {
        "review",
        "findings",
    }:
        raise TelemetryError("prepared review telemetry is invalid")
    review = telemetry["review"]
    findings = telemetry["findings"]
    if not isinstance(review, dict) or not isinstance(findings, list):
        raise TelemetryError("prepared review telemetry has invalid types")
    required_review = {
        "review_id",
        "review_digest",
        "reviewer",
        "repository_id",
        "repository",
        "pull_request_number",
        "base_sha",
        "head_sha",
        "workflow_sha",
        "workflow_run_id",
        "workflow_run_attempt",
        "model",
        "reasoning_effort",
        "scope_id",
        "scope_digest",
        "trigger",
    }
    if set(review) != required_review:
        raise TelemetryError("prepared review metadata fields are invalid")
    require_identifier(review["review_id"], "review_id")
    require_identifier(review["scope_id"], "scope_id")
    require_repository_id(review["repository_id"])
    require_pull_request_number(review["pull_request_number"])
    require_sha(review["base_sha"], "base SHA")
    require_sha(review["head_sha"], "head SHA")
    require_sha(review["workflow_sha"], "workflow SHA")
    if review["reviewer"] not in {"claude", "codex"}:
        raise TelemetryError("AI reviewer is invalid")
    if not isinstance(review["trigger"], dict):
        raise TelemetryError("review trigger metadata is invalid")

    normalized_findings = []
    for position, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise TelemetryError(f"prepared finding {position + 1} is invalid")
        required_finding = {
            "finding_id",
            "finding_digest",
            "finding_key",
            "identity_key",
            "prior_finding_id",
            "observation_id",
            "observation_digest",
            "path",
            "start_line",
            "line",
            "body",
            "has_suggestion",
            "suggestion",
            "suggestion_id",
            "suggestion_digest",
        }
        if set(finding) != required_finding:
            raise TelemetryError(
                f"prepared finding {position + 1} fields are invalid"
            )
        require_identifier(finding["finding_id"], "finding_id")
        require_identifier(finding["observation_id"], "observation_id")
        if finding["suggestion_id"]:
            require_identifier(finding["suggestion_id"], "suggestion_id")
        if (
            not isinstance(finding["body"], str)
            or not isinstance(finding["suggestion"], str)
            or not isinstance(finding["path"], str)
        ):
            raise TelemetryError(
                f"prepared finding {position + 1} text fields are invalid"
            )
        normalized_findings.append(finding)
    return review, normalized_findings


def content_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def finding_identity_file(
    review: dict[str, Any],
    finding: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "finding_id": finding["finding_id"],
        "finding_digest": finding["finding_digest"],
        "repository": {
            "id": review["repository_id"],
            "full_name": review["repository"],
        },
        "pull_request_number": review["pull_request_number"],
        "reviewer": review["reviewer"],
        "identity_key": finding["identity_key"],
    }


def suggestion_file(
    review: dict[str, Any],
    finding: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "suggestion_id": finding["suggestion_id"],
        "suggestion_digest": finding["suggestion_digest"],
        "review_id": review["review_id"],
        "observation_id": finding["observation_id"],
        "repository": {
            "id": review["repository_id"],
            "full_name": review["repository"],
        },
        "pull_request_number": review["pull_request_number"],
        "reviewer": review["reviewer"],
        "reviewed_head_sha": review["head_sha"],
        "path": finding["path"],
        "start_line": finding["start_line"],
        "line": finding["line"],
        "replacement": finding["suggestion"],
    }


def review_manifest(
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "finding_id": finding["finding_id"],
            "observation_id": finding["observation_id"],
            "path": finding["path"],
            "start_line": finding["start_line"],
            "line": finding["line"],
            "body_digest": content_digest(finding["body"]),
            "suggestion_id": finding["suggestion_id"],
            "suggestion_digest": (
                content_digest(finding["suggestion"])
                if finding["has_suggestion"]
                else ""
            ),
        }
        for finding in findings
    ]


def plan_review(
    store: TelemetryStore,
    prepared: Any,
    *,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    review, findings = prepared_telemetry(prepared)
    trigger_timestamp = review["trigger"].get("event_timestamp")
    planned_at = recorded_at or (
        require_timestamp(trigger_timestamp, "trigger event timestamp")
        if trigger_timestamp
        else utc_now()
    )
    record = build_record(
        record_type="review_planned",
        identity={"review_id": review["review_id"]},
        repository_id=review["repository_id"],
        repository=review["repository"],
        pull_request_number=review["pull_request_number"],
        recorded_at=planned_at,
        data={
            **review,
            "planned_at": planned_at,
            "findings": review_manifest(findings),
        },
    )
    extra_files: dict[str, Any] = {}
    for finding in findings:
        extra_files[
            finding_path(
                review["pull_request_number"],
                review["reviewer"],
                finding["finding_id"],
            )
        ] = finding_identity_file(review, finding)
    store.write_records(
        [record],
        extra_files=extra_files,
        message=f"Record planned AI review {review['review_id']}",
    )
    return record


def published_comments(
    values: list[Any],
    findings: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    expected = {finding["observation_id"] for finding in findings}
    comments: dict[str, dict[str, str]] = {}
    for position, value in enumerate(values):
        if not isinstance(value, dict) or set(value) != {
            "observation_id",
            "comment_node_id",
            "created_at",
        }:
            raise TelemetryError(
                f"published comment mapping {position + 1} is invalid"
            )
        observation_id = require_identifier(
            value["observation_id"],
            "observation_id",
        )
        if observation_id not in expected:
            raise TelemetryError(
                "published comment mapping references an unknown observation"
            )
        if observation_id in comments:
            raise TelemetryError(
                "published comment mappings repeat an observation"
            )
        comment_node_id = value["comment_node_id"]
        if (
            not isinstance(comment_node_id, str)
            or not comment_node_id
            or len(comment_node_id) > 256
        ):
            raise TelemetryError("published comment node ID is invalid")
        created_at = require_timestamp(value["created_at"], "comment created_at")
        comments[observation_id] = {
            "comment_node_id": comment_node_id,
            "created_at": created_at,
        }
    if set(comments) != expected:
        raise TelemetryError("not every prepared finding was published")
    return comments


def publish_review(
    store: TelemetryStore,
    prepared: Any,
    comment_values: list[Any],
    summary_value: Any,
) -> list[dict[str, Any]]:
    review, findings = prepared_telemetry(prepared)
    comments = published_comments(comment_values, findings)
    if not isinstance(summary_value, dict) or set(summary_value) != {
        "comment_node_id",
        "created_at",
    }:
        raise TelemetryError("summary comment mapping is invalid")
    summary_comment_id = summary_value["comment_node_id"]
    if (
        not isinstance(summary_comment_id, str)
        or not summary_comment_id
        or len(summary_comment_id) > 256
    ):
        raise TelemetryError("summary comment node ID is invalid")
    summary_created_at = require_timestamp(
        summary_value["created_at"],
        "summary created_at",
    )

    review_record = build_record(
        record_type="review_published",
        identity={"review_id": review["review_id"]},
        repository_id=review["repository_id"],
        repository=review["repository"],
        pull_request_number=review["pull_request_number"],
        recorded_at=summary_created_at,
        data={
            **review,
            "published_at": summary_created_at,
            "summary_comment_node_id": summary_comment_id,
            "findings": [
                {
                    **manifest,
                    **comments[manifest["observation_id"]],
                }
                for manifest in review_manifest(findings)
            ],
        },
    )
    records = [review_record]
    for finding in findings:
        comment = comments[finding["observation_id"]]
        records.append(
            build_record(
                record_type="finding_observed",
                identity={"observation_id": finding["observation_id"]},
                repository_id=review["repository_id"],
                repository=review["repository"],
                pull_request_number=review["pull_request_number"],
                recorded_at=comment["created_at"],
                data={
                    "review_id": review["review_id"],
                    "reviewer": review["reviewer"],
                    "finding_id": finding["finding_id"],
                    "finding_digest": finding["finding_digest"],
                    "finding_key": finding["finding_key"],
                    "identity_key": finding["identity_key"],
                    "prior_finding_id": finding["prior_finding_id"],
                    "observation_id": finding["observation_id"],
                    "observation_digest": finding["observation_digest"],
                    "reviewed_base_sha": review["base_sha"],
                    "reviewed_head_sha": review["head_sha"],
                    "path": finding["path"],
                    "start_line": finding["start_line"],
                    "line": finding["line"],
                    "body": finding["body"],
                    "suggestion_id": finding["suggestion_id"],
                    "comment_node_id": comment["comment_node_id"],
                    "published_at": comment["created_at"],
                },
            )
        )
    suggestion_files = {
        suggestion_path(
            review["pull_request_number"],
            review["reviewer"],
            finding["suggestion_id"],
        ): suggestion_file(review, finding)
        for finding in findings
        if finding["suggestion_id"]
    }
    store.write_records(
        records,
        extra_files=suggestion_files,
        message=f"Record published AI review {review['review_id']}",
    )
    return records


def fail_review(
    store: TelemetryStore,
    prepared: Any,
    comment_values: list[Any],
    *,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    review, findings = prepared_telemetry(prepared)
    failed_at = recorded_at or utc_now()
    partial_comments = []
    expected = {finding["observation_id"] for finding in findings}
    for value in comment_values:
        if not isinstance(value, dict):
            continue
        observation_id = value.get("observation_id")
        comment_node_id = value.get("comment_node_id")
        if (
            observation_id in expected
            and isinstance(comment_node_id, str)
            and comment_node_id
        ):
            partial_comments.append(
                {
                    "observation_id": observation_id,
                    "comment_node_id": comment_node_id,
                }
            )
    record = build_record(
        record_type="review_failed",
        identity={"review_id": review["review_id"]},
        repository_id=review["repository_id"],
        repository=review["repository"],
        pull_request_number=review["pull_request_number"],
        recorded_at=failed_at,
        data={
            **review,
            "failed_at": failed_at,
            "partial_comments": partial_comments,
        },
    )
    store.write_records(
        [record],
        message=f"Record failed AI review {review['review_id']}",
    )
    return record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("plan", "publish", "fail"))
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--comments", type=Path)
    parser.add_argument("--summary", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        prepared = read_json(args.prepared)
        review, _findings = prepared_telemetry(prepared)
        store = TelemetryStore(review["repository"])
        if args.operation == "plan":
            plan_review(store, prepared)
        elif args.operation == "publish":
            if args.comments is None or args.summary is None:
                raise TelemetryError(
                    "publish requires --comments and --summary"
                )
            publish_review(
                store,
                prepared,
                read_json_lines(args.comments),
                read_json(args.summary),
            )
        else:
            fail_review(
                store,
                prepared,
                read_json_lines(args.comments),
            )
    except (
        OSError,
        json.JSONDecodeError,
        TelemetryError,
    ) as error:
        print(f"failed to record AI review telemetry: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
