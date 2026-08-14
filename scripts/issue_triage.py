#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


MAX_CONFIGURED_LABELS = 50
MAX_LABELS = MAX_CONFIGURED_LABELS
MAX_JSON_FILE_BYTES = 1_000_000
CONTEXT_DIRECTORY = ".ai-issue-triage-context"
ISSUE_CONTEXT_FIELDS = frozenset(
    (
        "repository",
        "node_id",
        "number",
        "title",
        "body",
        "author_association",
    )
)


class TriageError(ValueError):
    pass


def require_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise TriageError(f"{name} must be set")
    return value


def repository_and_issue_number() -> tuple[str, int]:
    repository = require_environment("GITHUB_REPOSITORY")
    if repository.count("/") != 1 or any(
        not component for component in repository.split("/")
    ):
        raise TriageError("GITHUB_REPOSITORY must be an owner/repository name")

    raw_issue_number = require_environment("ISSUE_NUMBER")
    try:
        issue_number = int(raw_issue_number)
    except ValueError as error:
        raise TriageError("ISSUE_NUMBER must be a positive integer") from error
    if issue_number < 1:
        raise TriageError("ISSUE_NUMBER must be a positive integer")

    return repository, issue_number


def run_gh_json(
    arguments: list[str], *, input_value: Any | None = None
) -> Any:
    command = ["gh", "api", *arguments]
    encoded_input = None
    if input_value is not None:
        encoded_input = json.dumps(input_value, separators=(",", ":"))

    try:
        result = subprocess.run(
            command,
            check=False,
            input=encoded_input,
            text=True,
            capture_output=True,
        )
    except OSError as error:
        raise TriageError("failed to run the GitHub CLI") from error
    if result.returncode != 0:
        message = result.stderr.strip() or "GitHub API request failed"
        raise TriageError(message)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise TriageError("GitHub API returned invalid JSON") from error


def load_repository_labels(repository: str) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    page = 1
    while True:
        response = run_gh_json(
            [f"repos/{repository}/labels?per_page=100&page={page}"]
        )
        if not isinstance(response, list):
            raise TriageError("GitHub returned an invalid label list")
        labels.extend(response)
        if len(response) < 100:
            return labels
        page += 1


def repository_labels(labels: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()

    for label in labels:
        if not isinstance(label, dict):
            raise TriageError("repository label entries must be objects")

        name = label.get("name")
        description = label.get("description")
        if not isinstance(name, str) or not name.strip():
            raise TriageError("repository labels must have non-empty names")
        if description is not None and not isinstance(description, str):
            raise TriageError("repository label descriptions must be strings")

        casefolded_name = name.casefold()
        if casefolded_name in seen:
            raise TriageError("repository labels must have unique names")

        seen.add(casefolded_name)
        normalized.append({"name": name, "description": description or ""})

    return normalized


def configured_label_names(value: str) -> list[str]:
    names = [line.strip() for line in value.splitlines() if line.strip()]
    if not 1 <= len(names) <= MAX_CONFIGURED_LABELS:
        raise TriageError(
            "label configuration must contain between 1 and "
            f"{MAX_CONFIGURED_LABELS} names"
        )
    if any(len(name) > 50 for name in names):
        raise TriageError("configured label names must not exceed 50 characters")
    if len({name.casefold() for name in names}) != len(names):
        raise TriageError("configured label names must be unique")
    return names


def label_configuration() -> list[str]:
    override = os.environ.get("TRIAGE_LABELS_OVERRIDE", "")
    value = override if override.strip() else require_environment(
        "DEFAULT_ISSUE_TRIAGE_LABELS"
    )
    return configured_label_names(value)


def configured_labels(
    labels: list[dict[str, Any]], configured_names: list[str]
) -> list[dict[str, str]]:
    available = {
        label["name"].casefold(): label for label in repository_labels(labels)
    }
    selected = [
        available[name.casefold()]
        for name in configured_names
        if name.casefold() in available
    ]
    if not selected:
        raise TriageError(
            "none of the configured labels exist in this repository"
        )
    return selected


def normalize_issue(
    value: Any, repository: str, issue_number: int
) -> dict[str, Any]:
    if not isinstance(value, dict) or "pull_request" in value:
        raise TriageError("GitHub did not return an issue")

    issue = {
        "repository": repository,
        "node_id": value.get("node_id"),
        "number": value.get("number"),
        "title": value.get("title"),
        "body": value.get("body"),
        "author_association": value.get("author_association"),
    }
    if issue["number"] != issue_number:
        raise TriageError("GitHub returned the wrong issue")
    if not isinstance(issue["node_id"], str) or not issue["node_id"]:
        raise TriageError("GitHub returned an issue without a node ID")
    if not isinstance(issue["title"], str):
        raise TriageError("GitHub returned an issue without a title")
    if issue["body"] is not None and not isinstance(issue["body"], str):
        raise TriageError("GitHub returned an issue with an invalid body")
    if not isinstance(issue["author_association"], str):
        raise TriageError(
            "GitHub returned an issue with an invalid author association"
        )
    return issue


def validate_model_context(
    value: Any,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if not isinstance(value, dict) or set(value) != {
        "issue",
        "allowed_labels",
    }:
        raise TriageError(
            "model context fields must be exactly ['allowed_labels', 'issue']"
        )

    raw_issue = value["issue"]
    if not isinstance(raw_issue, dict) or set(raw_issue) != ISSUE_CONTEXT_FIELDS:
        raise TriageError("model issue context has invalid fields")

    repository = raw_issue.get("repository")
    issue_number = raw_issue.get("number")
    if not isinstance(repository, str) or type(issue_number) is not int:
        raise TriageError("model issue context has an invalid identity")
    issue = normalize_issue(raw_issue, repository, issue_number)

    raw_labels = value["allowed_labels"]
    if not isinstance(raw_labels, list):
        raise TriageError("allowed labels must be an array")
    labels = repository_labels(raw_labels)
    if not 1 <= len(labels) <= MAX_CONFIGURED_LABELS:
        raise TriageError("model context has an invalid label count")
    return issue, labels


def issue_snapshot(issue: dict[str, Any]) -> dict[str, str]:
    encoded_issue = json.dumps(
        issue,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "node_id": issue["node_id"],
        "content_sha256": hashlib.sha256(encoded_issue).hexdigest(),
    }


def output_schema(labels: list[dict[str, str]]) -> dict[str, Any]:
    label_names = [label["name"] for label in labels]
    return {
        "type": "object",
        "properties": {
            "labels": {
                "type": "array",
                "minItems": 1,
                "maxItems": min(MAX_LABELS, len(label_names)),
                "uniqueItems": True,
                "description": (
                    "Existing repository labels that classify the issue"
                ),
                "items": {
                    "type": "string",
                    "enum": label_names,
                },
            }
        },
        "required": ["labels"],
        "additionalProperties": False,
    }


def load_json(path: Path, description: str) -> Any:
    try:
        if path.stat().st_size > MAX_JSON_FILE_BYTES:
            raise TriageError(f"{description} exceeds the size limit")
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise TriageError(f"{description} is not readable JSON") from error


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=True, indent=2)
        file.write("\n")
    temporary_path.replace(path)


def validate_result(
    result: Any, candidates: list[dict[str, str]]
) -> dict[str, list[str]]:
    if not isinstance(result, dict) or set(result) != {"labels"}:
        raise TriageError("result fields must be exactly ['labels']")

    labels = result["labels"]
    if not isinstance(labels, list):
        raise TriageError("labels must be an array")
    if not 1 <= len(labels) <= MAX_LABELS:
        raise TriageError(
            f"labels must contain between 1 and {MAX_LABELS} entries"
        )
    if any(not isinstance(label, str) for label in labels):
        raise TriageError("every label must be a string")
    if len({label.casefold() for label in labels}) != len(labels):
        raise TriageError("labels must be unique")

    allowed_names = {candidate["name"] for candidate in candidates}
    unknown_names = [label for label in labels if label not in allowed_names]
    if unknown_names:
        raise TriageError(
            f"labels are not eligible in this repository: {unknown_names}"
        )

    return {"labels": labels}


def validate_artifact(value: Any) -> tuple[dict[str, str], Any]:
    if not isinstance(value, dict) or set(value) != {
        "issue_snapshot",
        "labels",
    }:
        raise TriageError(
            "triage artifact fields must be exactly "
            "['issue_snapshot', 'labels']"
        )

    snapshot = value["issue_snapshot"]
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "node_id",
        "content_sha256",
    }:
        raise TriageError("triage artifact has an invalid issue snapshot")
    if not isinstance(snapshot["node_id"], str) or not snapshot["node_id"]:
        raise TriageError("triage artifact has an invalid issue node ID")
    digest = snapshot["content_sha256"]
    if (
        not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise TriageError("triage artifact has an invalid issue digest")

    return snapshot, value["labels"]


def require_matching_snapshot(
    expected: dict[str, str], current_issue: dict[str, Any]
) -> None:
    if issue_snapshot(current_issue) != expected:
        raise TriageError(
            "the issue changed after classification; triage its latest content"
        )


def prepare_context() -> None:
    repository, issue_number = repository_and_issue_number()
    workspace = Path(require_environment("GITHUB_WORKSPACE"))
    context_directory = workspace / CONTEXT_DIRECTORY
    if context_directory.exists():
        raise TriageError("reserved issue triage context path already exists")

    labels = configured_labels(
        load_repository_labels(repository),
        label_configuration(),
    )
    issue = normalize_issue(
        run_gh_json([f"repos/{repository}/issues/{issue_number}"]),
        repository,
        issue_number,
    )

    context_directory.mkdir()
    write_json(
        context_directory / "context.json",
        {"issue": issue, "allowed_labels": labels},
    )
    write_json(context_directory / "output-schema.json", output_schema(labels))


def validate_files(
    result_path: Path, context_path: Path, output_path: Path
) -> None:
    result = load_json(result_path, "AI triage result")
    issue, candidates = validate_model_context(
        load_json(context_path, "issue triage context")
    )
    validated = validate_result(result, candidates)
    write_json(
        output_path,
        {
            "issue_snapshot": issue_snapshot(issue),
            "labels": validated["labels"],
        },
    )


def apply_result(result_path: Path) -> None:
    repository, issue_number = repository_and_issue_number()
    snapshot, raw_labels = validate_artifact(
        load_json(result_path, "AI triage result")
    )
    current_issue = normalize_issue(
        run_gh_json([f"repos/{repository}/issues/{issue_number}"]),
        repository,
        issue_number,
    )
    require_matching_snapshot(snapshot, current_issue)

    candidates = configured_labels(
        load_repository_labels(repository),
        label_configuration(),
    )
    result = validate_result(
        {"labels": raw_labels},
        candidates,
    )
    current_issue = normalize_issue(
        run_gh_json([f"repos/{repository}/issues/{issue_number}"]),
        repository,
        issue_number,
    )
    require_matching_snapshot(snapshot, current_issue)

    run_gh_json(
        [
            "--method",
            "POST",
            f"repos/{repository}/issues/{issue_number}/labels",
            "--input",
            "-",
        ],
        input_value=result,
    )


def apply_fallback() -> None:
    repository, issue_number = repository_and_issue_number()
    label_name = "needs-triage"
    existing_names = {
        label.get("name", "").casefold(): label.get("name")
        for label in load_repository_labels(repository)
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    }
    applied_label_name = existing_names.get(label_name)
    if applied_label_name is None:
        try:
            run_gh_json(
                [
                    "--method",
                    "POST",
                    f"repos/{repository}/labels",
                    "--input",
                    "-",
                ],
                input_value={
                    "name": label_name,
                    "color": "e11d48",
                    "description": "Issue needs triage",
                },
            )
            applied_label_name = label_name
        except TriageError:
            # A concurrent workflow may have created the label first.
            current_names = {
                label.get("name", "").casefold(): label.get("name")
                for label in load_repository_labels(repository)
                if isinstance(label, dict)
                and isinstance(label.get("name"), str)
            }
            applied_label_name = current_names.get(label_name)
            if applied_label_name is None:
                raise

    run_gh_json(
        [
            "--method",
            "POST",
            f"repos/{repository}/issues/{issue_number}/labels",
            "--input",
            "-",
        ],
        input_value={"labels": [applied_label_name]},
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare")

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("result", type=Path)
    validate_parser.add_argument("labels", type=Path)
    validate_parser.add_argument("output", type=Path)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("result", type=Path)

    subparsers.add_parser("fallback")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        if arguments.command == "prepare":
            prepare_context()
        elif arguments.command == "validate":
            validate_files(
                arguments.result,
                arguments.labels,
                arguments.output,
            )
        elif arguments.command == "apply":
            apply_result(arguments.result)
        elif arguments.command == "fallback":
            apply_fallback()
        else:
            raise AssertionError(f"unknown command: {arguments.command}")
    except TriageError as error:
        print(f"issue triage failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
