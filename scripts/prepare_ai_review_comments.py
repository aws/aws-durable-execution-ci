#!/usr/bin/env python3

import argparse
import base64
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_review_telemetry import (
    TelemetryError,
    require_finding_key,
    require_identifier,
    require_pull_request_number,
    require_repository_id,
    require_sha,
    stable_identifier,
)


MAX_COMMENTS = 20
MAX_RANGE_LINES = 100
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
RESERVED_METADATA_PREFIX = "<!-- ai-pr-review:"
REVIEWER_TITLES = {
    "claude": "Claude AI review",
    "codex": "Codex AI review",
}
DEFAULT_BASE_SHA = "0" * 40
DEFAULT_WORKFLOW_SHA = "0" * 40


class ReviewValidationError(ValueError):
    pass


@dataclass(frozen=True)
class DiffLine:
    kind: str
    hunk: int


def require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ReviewValidationError(
            f"{label} fields must be {sorted(expected)}; got {sorted(actual)}"
        )


def parse_patch(patch: str) -> dict[int, DiffLine]:
    lines: dict[int, DiffLine] = {}
    new_line = 0
    hunk = 0
    in_hunk = False

    for patch_line in patch.splitlines():
        header = HUNK_HEADER.match(patch_line)
        if header:
            hunk += 1
            new_line = int(header.group(1))
            in_hunk = True
            continue

        if not in_hunk or patch_line.startswith("\\"):
            continue

        prefix = patch_line[:1]
        if prefix == "+":
            lines[new_line] = DiffLine("addition", hunk)
            new_line += 1
        elif prefix == " ":
            lines[new_line] = DiffLine("context", hunk)
            new_line += 1
        elif prefix == "-":
            continue
        else:
            in_hunk = False

    return lines


def build_diff_index(files: Any) -> dict[str, dict[int, DiffLine]]:
    if not isinstance(files, list):
        raise ReviewValidationError("PR files payload must be an array")

    index: dict[str, dict[int, DiffLine]] = {}
    for position, file_entry in enumerate(files):
        if not isinstance(file_entry, dict):
            raise ReviewValidationError(f"PR file {position} must be an object")

        path = file_entry.get("filename")
        patch = file_entry.get("patch")
        if not isinstance(path, str) or not path:
            raise ReviewValidationError(f"PR file {position} has no valid filename")
        if path in index:
            raise ReviewValidationError(f"PR files payload repeats path {path!r}")
        index[path] = parse_patch(patch) if isinstance(patch, str) else {}

    return index


def suggestion_fence(suggestion: str) -> str:
    longest_run = max(
        (len(match.group(0)) for match in re.finditer(r"`+", suggestion)),
        default=0,
    )
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}suggestion\n{suggestion}\n{fence}"


def require_string(
    value: Any, label: str, *, minimum: int = 0, maximum: int
) -> str:
    if not isinstance(value, str):
        raise ReviewValidationError(f"{label} must be a string")
    if len(value) < minimum or len(value) > maximum:
        raise ReviewValidationError(
            f"{label} length must be between {minimum} and {maximum}"
        )
    return value


def parse_prior_findings(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise ReviewValidationError("prior findings must be an array")
    findings: dict[str, dict[str, Any]] = {}
    for position, finding in enumerate(value):
        label = f"prior finding {position + 1}"
        if not isinstance(finding, dict):
            raise ReviewValidationError(f"{label} must be an object")
        expected = {
            "finding_id",
            "finding_key",
            "path",
            "body",
            "reviewed_head_sha",
            "observed_at",
        }
        require_exact_keys(finding, expected, label)
        try:
            finding_id = require_identifier(
                finding["finding_id"],
                f"{label}.finding_id",
            )
            finding_key = require_finding_key(finding["finding_key"])
            require_sha(finding["reviewed_head_sha"], f"{label}.reviewed_head_sha")
        except TelemetryError as error:
            raise ReviewValidationError(str(error)) from error
        require_string(finding["path"], f"{label}.path", minimum=1, maximum=1024)
        require_string(finding["body"], f"{label}.body", minimum=1, maximum=2000)
        require_string(
            finding["observed_at"],
            f"{label}.observed_at",
            minimum=1,
            maximum=64,
        )
        if finding_id in findings:
            raise ReviewValidationError(
                f"prior findings repeat finding ID {finding_id}"
            )
        findings[finding_id] = finding
    return findings


def decode_trigger_metadata(encoded: str) -> dict[str, Any]:
    if not encoded:
        return {}
    try:
        decoded = base64.b64decode(encoded, validate=True)
        value = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as error:
        raise ReviewValidationError("trigger metadata is invalid") from error
    if not isinstance(value, dict):
        raise ReviewValidationError("trigger metadata must be an object")
    allowed = {
        "trigger_id",
        "event_name",
        "event_action",
        "event_timestamp",
        "event_base_sha",
        "event_head_sha",
        "before_sha",
        "after_sha",
        "actor",
        "command_comment_id",
    }
    if not set(value).issubset(allowed):
        raise ReviewValidationError("trigger metadata has unexpected fields")
    for name, item in value.items():
        if not isinstance(item, str) or len(item) > 256 or "\0" in item:
            raise ReviewValidationError(
                f"trigger metadata field {name} is invalid"
            )
    return value


def prepare_review(
    review: Any,
    files: Any,
    reviewer: str,
    run_id: str,
    run_attempt: str,
    expected_head_sha: str,
    *,
    repository_id: int | str = 1,
    repository: str = "local/example",
    pull_request_number: int | str = 1,
    expected_base_sha: str = DEFAULT_BASE_SHA,
    workflow_sha: str = DEFAULT_WORKFLOW_SHA,
    model: str = "test-model",
    reasoning_effort: str = "test",
    prompt_path: str = "",
    review_guidance_base64: str = "",
    trigger_metadata: dict[str, Any] | None = None,
    prior_findings: Any = None,
) -> dict[str, Any]:
    reviewer_title = REVIEWER_TITLES.get(reviewer)
    if reviewer_title is None:
        raise ReviewValidationError(f"unsupported AI reviewer: {reviewer}")

    try:
        normalized_repository_id = require_repository_id(repository_id)
        normalized_pr_number = require_pull_request_number(pull_request_number)
        normalized_base_sha = require_sha(expected_base_sha, "expected base SHA")
        normalized_head_sha = require_sha(expected_head_sha, "expected head SHA")
        normalized_workflow_sha = require_sha(workflow_sha, "workflow SHA")
    except TelemetryError as error:
        raise ReviewValidationError(str(error)) from error
    if not isinstance(repository, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
        repository,
    ):
        raise ReviewValidationError("repository full name is invalid")
    model = require_string(model, "model", minimum=1, maximum=256)
    reasoning_effort = require_string(
        reasoning_effort,
        "reasoning effort",
        minimum=1,
        maximum=32,
    )
    prompt_path = require_string(prompt_path, "prompt path", maximum=1024)
    review_guidance_base64 = require_string(
        review_guidance_base64,
        "review guidance",
        maximum=20_000,
    )
    normalized_trigger = trigger_metadata or {}
    if not isinstance(normalized_trigger, dict):
        raise ReviewValidationError("trigger metadata must be an object")
    normalized_prior = parse_prior_findings(prior_findings or [])

    if not isinstance(review, dict):
        raise ReviewValidationError("review must be an object")
    require_exact_keys(review, {"summary", "comments"}, "review")

    summary = require_string(
        review["summary"], "summary", minimum=1, maximum=4000
    ).strip()
    if not summary:
        raise ReviewValidationError("summary must contain non-whitespace text")
    if RESERVED_METADATA_PREFIX in summary:
        raise ReviewValidationError("summary must not contain reserved metadata")

    comments = review["comments"]
    if not isinstance(comments, list):
        raise ReviewValidationError("comments must be an array")
    if len(comments) > MAX_COMMENTS:
        raise ReviewValidationError(
            f"comments must contain at most {MAX_COMMENTS} items"
        )

    diff_index = build_diff_index(files)
    marker = (
        f"[ai-pr-review-inline-{reviewer}-{run_id}-{run_attempt}-published]: #"
    )
    if not run_id.isdigit() or not run_attempt.isdigit():
        raise ReviewValidationError("run ID and attempt must be numeric")
    review_id, review_digest = stable_identifier(
        "arr",
        {
            "repository_id": normalized_repository_id,
            "pull_request_number": normalized_pr_number,
            "reviewer": reviewer,
            "workflow_run_id": int(run_id),
            "workflow_run_attempt": int(run_attempt),
        },
    )
    scope_id, scope_digest = stable_identifier(
        "ars",
        {
            "workflow_sha": normalized_workflow_sha,
            "base_sha": normalized_base_sha,
            "prompt_path": prompt_path,
            "review_guidance_base64": review_guidance_base64,
        },
    )
    prepared_comments: list[dict[str, Any]] = []
    telemetry_findings: list[dict[str, Any]] = []
    seen_comments: set[tuple[Any, ...]] = set()
    seen_finding_ids: set[str] = set()
    expected_keys = {
        "finding_key",
        "prior_finding_id",
        "path",
        "start_line",
        "line",
        "body",
        "has_suggestion",
        "suggestion",
    }

    for position, comment in enumerate(comments):
        label = f"comment {position + 1}"
        if not isinstance(comment, dict):
            raise ReviewValidationError(f"{label} must be an object")
        require_exact_keys(comment, expected_keys, label)

        try:
            finding_key = require_finding_key(comment["finding_key"])
        except TelemetryError as error:
            raise ReviewValidationError(f"{label}.{error}") from error
        prior_finding_id = require_string(
            comment["prior_finding_id"],
            f"{label}.prior_finding_id",
            maximum=64,
        )
        prior_finding: dict[str, Any] | None = None
        if prior_finding_id:
            try:
                require_identifier(
                    prior_finding_id,
                    f"{label}.prior_finding_id",
                )
            except TelemetryError as error:
                raise ReviewValidationError(str(error)) from error
            prior_finding = normalized_prior.get(prior_finding_id)
            if prior_finding is None:
                raise ReviewValidationError(
                    f"{label}.prior_finding_id is not in the trusted prior catalog"
                )
            finding_id = prior_finding_id
            identity_key = prior_finding["finding_key"]
            _expected_id, finding_digest = stable_identifier(
                "arf",
                {
                    "repository_id": normalized_repository_id,
                    "pull_request_number": normalized_pr_number,
                    "reviewer": reviewer,
                    "finding_key": identity_key,
                },
            )
            if _expected_id != finding_id:
                raise ReviewValidationError(
                    f"{label}.prior_finding_id does not match its trusted identity"
                )
        else:
            identity_key = finding_key
            finding_id, finding_digest = stable_identifier(
                "arf",
                {
                    "repository_id": normalized_repository_id,
                    "pull_request_number": normalized_pr_number,
                    "reviewer": reviewer,
                    "finding_key": identity_key,
                },
            )
        if finding_id in seen_finding_ids:
            raise ReviewValidationError(
                f"{label} repeats finding identity {finding_id}"
            )
        seen_finding_ids.add(finding_id)

        path = require_string(
            comment["path"], f"{label}.path", minimum=1, maximum=1024
        )
        body = require_string(
            comment["body"], f"{label}.body", minimum=1, maximum=2000
        ).strip()
        if not body:
            raise ReviewValidationError(
                f"{label}.body must contain non-whitespace text"
            )
        if re.search(r"(?im)^[ \t]*(?:`{3,}|~{3,})suggestion(?:[ \t]|$)", body):
            raise ReviewValidationError(
                f"{label}.body must not contain a suggestion fence"
            )
        if RESERVED_METADATA_PREFIX in body:
            raise ReviewValidationError(
                f"{label}.body must not contain reserved metadata"
            )

        start_line = comment["start_line"]
        end_line = comment["line"]
        if type(start_line) is not int or start_line < 1:
            raise ReviewValidationError(
                f"{label}.start_line must be a positive integer"
            )
        if type(end_line) is not int or end_line < 1:
            raise ReviewValidationError(f"{label}.line must be a positive integer")
        if start_line > end_line:
            raise ReviewValidationError(f"{label} starts after it ends")
        if end_line - start_line + 1 > MAX_RANGE_LINES:
            raise ReviewValidationError(
                f"{label} spans more than {MAX_RANGE_LINES} lines"
            )

        has_suggestion = comment["has_suggestion"]
        if type(has_suggestion) is not bool:
            raise ReviewValidationError(f"{label}.has_suggestion must be a boolean")
        suggestion = require_string(
            comment["suggestion"], f"{label}.suggestion", maximum=12000
        )
        if not has_suggestion and suggestion:
            raise ReviewValidationError(
                f"{label}.suggestion must be empty when has_suggestion is false"
            )

        path_lines = diff_index.get(path)
        if path_lines is None:
            raise ReviewValidationError(f"{label}.path is not present in the PR diff")

        selected_lines: list[DiffLine] = []
        for line_number in range(start_line, end_line + 1):
            diff_line = path_lines.get(line_number)
            if diff_line is None:
                raise ReviewValidationError(
                    f"{label} line {line_number} is not on the right side of a diff hunk"
                )
            selected_lines.append(diff_line)

        if len({line.hunk for line in selected_lines}) != 1:
            raise ReviewValidationError(f"{label} spans more than one diff hunk")
        if not any(line.kind == "addition" for line in selected_lines):
            raise ReviewValidationError(f"{label} does not include an added line")

        duplicate_key = (
            path,
            start_line,
            end_line,
            body,
            has_suggestion,
            suggestion,
        )
        if duplicate_key in seen_comments:
            raise ReviewValidationError(f"{label} duplicates an earlier comment")
        seen_comments.add(duplicate_key)

        observation_id, observation_digest = stable_identifier(
            "aro",
            {
                "review_id": review_id,
                "finding_id": finding_id,
                "position": position,
            },
        )
        suggestion_id = ""
        suggestion_digest = ""
        if has_suggestion:
            suggestion_id, suggestion_digest = stable_identifier(
                "arsg",
                {
                    "observation_id": observation_id,
                    "suggestion": suggestion,
                },
            )

        published_body = (
            f"{marker}\n"
            f"<!-- ai-pr-review:finding:{reviewer}:{finding_id} -->\n"
            f"<!-- ai-pr-review:observation:{observation_id} -->\n"
            f"**{reviewer_title} · Finding `{finding_id}`**\n\n"
            f"{body}"
        )
        if has_suggestion:
            published_body += f"\n\n{suggestion_fence(suggestion)}"

        payload: dict[str, Any] = {
            "body": published_body,
            "commit_id": expected_head_sha,
            "path": path,
            "line": end_line,
            "side": "RIGHT",
        }
        if start_line < end_line:
            payload["start_line"] = start_line
            payload["start_side"] = "RIGHT"
        prepared_comments.append(payload)
        telemetry_findings.append(
            {
                "finding_id": finding_id,
                "finding_digest": finding_digest,
                "finding_key": finding_key,
                "identity_key": identity_key,
                "prior_finding_id": prior_finding_id,
                "observation_id": observation_id,
                "observation_digest": observation_digest,
                "path": path,
                "start_line": start_line,
                "line": end_line,
                "body": body,
                "has_suggestion": has_suggestion,
                "suggestion": suggestion,
                "suggestion_id": suggestion_id,
                "suggestion_digest": suggestion_digest,
            }
        )

    return {
        "summary": summary,
        "comments": prepared_comments,
        "telemetry": {
            "review": {
                "review_id": review_id,
                "review_digest": review_digest,
                "reviewer": reviewer,
                "repository_id": normalized_repository_id,
                "repository": repository,
                "pull_request_number": normalized_pr_number,
                "base_sha": normalized_base_sha,
                "head_sha": normalized_head_sha,
                "workflow_sha": normalized_workflow_sha,
                "workflow_run_id": int(run_id),
                "workflow_run_attempt": int(run_attempt),
                "model": model,
                "reasoning_effort": reasoning_effort,
                "scope_id": scope_id,
                "scope_digest": scope_digest,
                "trigger": normalized_trigger,
            },
            "findings": telemetry_findings,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--files", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reviewer", required=True, choices=("claude", "codex"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--expected-head-sha", required=True)
    parser.add_argument("--expected-base-sha", required=True)
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pull-request-number", required=True)
    parser.add_argument("--workflow-sha", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", required=True)
    parser.add_argument("--prompt-path", default="")
    parser.add_argument("--review-guidance-base64", default="")
    parser.add_argument("--trigger-metadata-base64", default="")
    parser.add_argument("--prior-findings", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.run_id.isdigit() or not args.run_attempt.isdigit():
        print("run ID and attempt must be numeric", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.expected_head_sha):
        print(
            "expected head SHA must be a 40-character hexadecimal value",
            file=sys.stderr,
        )
        return 2

    try:
        review = json.loads(args.review.read_text(encoding="utf-8"))
        files = json.loads(args.files.read_text(encoding="utf-8"))
        prior_findings_value = json.loads(
            args.prior_findings.read_text(encoding="utf-8")
        )
        prepared = prepare_review(
            review,
            files,
            args.reviewer,
            args.run_id,
            args.run_attempt,
            args.expected_head_sha,
            repository_id=args.repository_id,
            repository=args.repository,
            pull_request_number=args.pull_request_number,
            expected_base_sha=args.expected_base_sha,
            workflow_sha=args.workflow_sha,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            prompt_path=args.prompt_path,
            review_guidance_base64=args.review_guidance_base64,
            trigger_metadata=decode_trigger_metadata(
                args.trigger_metadata_base64
            ),
            prior_findings=prior_findings_value,
        )
    except (
        OSError,
        json.JSONDecodeError,
        ReviewValidationError,
        TelemetryError,
    ) as error:
        print(f"invalid AI review output: {error}", file=sys.stderr)
        return 1

    args.output.write_text(
        json.dumps(prepared, ensure_ascii=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
