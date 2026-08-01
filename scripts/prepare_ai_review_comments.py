#!/usr/bin/env python3

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_COMMENTS = 20
MAX_RANGE_LINES = 100
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


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


def prepare_review(
    review: Any,
    files: Any,
    reviewer: str,
    run_id: str,
    run_attempt: str,
    expected_head_sha: str,
) -> dict[str, Any]:
    if not isinstance(review, dict):
        raise ReviewValidationError("review must be an object")
    require_exact_keys(review, {"summary", "comments"}, "review")

    summary = require_string(
        review["summary"], "summary", minimum=1, maximum=4000
    ).strip()
    if not summary:
        raise ReviewValidationError("summary must contain non-whitespace text")

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
    prepared_comments: list[dict[str, Any]] = []
    seen_comments: set[tuple[Any, ...]] = set()
    expected_keys = {
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

        published_body = f"{marker}\n{body}"
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

    return {"summary": summary, "comments": prepared_comments}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--files", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reviewer", required=True, choices=("claude", "codex"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--expected-head-sha", required=True)
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
        prepared = prepare_review(
            review,
            files,
            args.reviewer,
            args.run_id,
            args.run_attempt,
            args.expected_head_sha,
        )
    except (OSError, json.JSONDecodeError, ReviewValidationError) as error:
        print(f"invalid AI review output: {error}", file=sys.stderr)
        return 1

    args.output.write_text(
        json.dumps(prepared, ensure_ascii=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
