#!/usr/bin/env python3

import base64
import hashlib
import json
import re
import subprocess
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


TELEMETRY_BRANCH = "ai-review-telemetry-v1"
SCHEMA_VERSION = 1
MAX_TREE_ENTRIES = 100_000
MAX_WRITE_ATTEMPTS = 5
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*_v[0-9]+_[a-z2-7]{26}$")
FINDING_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:/-]{2,255}$")
SAFE_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")


class TelemetryError(ValueError):
    pass


class GitHubApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class TelemetryConflictError(TelemetryError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def stable_identifier(prefix: str, value: Any) -> tuple[str, str]:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", prefix):
        raise TelemetryError("identifier prefix is invalid")
    digest = hashlib.sha256(canonical_json_bytes(value)).digest()
    encoded = base64.b32encode(digest).decode("ascii").lower().rstrip("=")
    full_digest = digest.hex()
    return f"{prefix}_v{SCHEMA_VERSION}_{encoded[:26]}", full_digest


def git_blob_sha(content: bytes) -> str:
    prefix = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(prefix + content).hexdigest()


def require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA_PATTERN.fullmatch(value):
        raise TelemetryError(f"{label} must be a lowercase 40-character SHA")
    return value


def require_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise TelemetryError(f"{label} is invalid")
    return value


def require_finding_key(value: Any) -> str:
    if not isinstance(value, str) or not FINDING_KEY_PATTERN.fullmatch(value):
        raise TelemetryError(
            "finding_key must be 3-256 lowercase characters using "
            "letters, digits, '.', '_', ':', '/', or '-'"
        )
    return value


def require_repository_id(value: Any) -> int:
    if type(value) is int:
        repository_id = value
    elif isinstance(value, str) and value.isdigit():
        repository_id = int(value)
    else:
        raise TelemetryError("repository ID must be a positive integer")
    if repository_id < 1:
        raise TelemetryError("repository ID must be a positive integer")
    return repository_id


def require_pull_request_number(value: Any) -> int:
    if type(value) is int:
        number = value
    elif isinstance(value, str) and value.isdigit():
        number = int(value)
    else:
        raise TelemetryError("pull request number must be a positive integer")
    if number < 1:
        raise TelemetryError("pull request number must be a positive integer")
    return number


def require_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TelemetryError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TelemetryError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise TelemetryError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise TelemetryError("telemetry record must be an object")
    required = {
        "schema_version",
        "record_type",
        "record_id",
        "recorded_at",
        "repository",
        "pull_request",
        "data",
    }
    if set(record) != required:
        raise TelemetryError(
            f"telemetry record fields must be {sorted(required)}"
        )
    if record["schema_version"] != SCHEMA_VERSION:
        raise TelemetryError("unsupported telemetry schema version")
    record_type = record["record_type"]
    if (
        not isinstance(record_type, str)
        or not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", record_type)
    ):
        raise TelemetryError("telemetry record type is invalid")
    require_identifier(record["record_id"], "record_id")
    require_timestamp(record["recorded_at"], "recorded_at")

    repository = record["repository"]
    if not isinstance(repository, dict) or set(repository) != {
        "id",
        "full_name",
    }:
        raise TelemetryError("repository metadata is invalid")
    require_repository_id(repository["id"])
    if (
        not isinstance(repository["full_name"], str)
        or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
            repository["full_name"],
        )
    ):
        raise TelemetryError("repository full name is invalid")

    pull_request = record["pull_request"]
    if not isinstance(pull_request, dict) or set(pull_request) != {"number"}:
        raise TelemetryError("pull request metadata is invalid")
    require_pull_request_number(pull_request["number"])
    if not isinstance(record["data"], dict):
        raise TelemetryError("telemetry record data must be an object")
    return record


def record_path(record: Mapping[str, Any]) -> str:
    validated = validate_record(dict(record))
    number = validated["pull_request"]["number"]
    return (
        f"events/pr-{number}/{validated['record_type']}/"
        f"{validated['record_id']}.json"
    )


def finding_path(
    pull_request_number: int,
    reviewer: str,
    finding_id: str,
) -> str:
    number = require_pull_request_number(pull_request_number)
    if reviewer not in {"claude", "codex"}:
        raise TelemetryError("AI reviewer is invalid")
    require_identifier(finding_id, "finding_id")
    return f"findings/pr-{number}/{reviewer}/{finding_id}.json"


def suggestion_path(
    pull_request_number: int,
    reviewer: str,
    suggestion_id: str,
) -> str:
    number = require_pull_request_number(pull_request_number)
    if reviewer not in {"claude", "codex"}:
        raise TelemetryError("AI reviewer is invalid")
    require_identifier(suggestion_id, "suggestion_id")
    return f"suggestions/pr-{number}/{reviewer}/{suggestion_id}.json"


def validate_storage_path(path: str) -> str:
    if (
        not path
        or path.startswith("/")
        or path.endswith("/")
        or "//" in path
        or not SAFE_PATH_PATTERN.fullmatch(path)
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise TelemetryError(f"unsafe telemetry path: {path!r}")
    return path


def _http_status(stderr: str) -> int | None:
    match = re.search(r"\(HTTP ([0-9]{3})\)", stderr)
    return int(match.group(1)) if match else None


class GitHubApi:
    def request(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        payload: Any | None = None,
    ) -> Any:
        gh_arguments = ["api"]
        if method != "GET":
            gh_arguments.extend(("--method", method))
        gh_arguments.append(endpoint)
        input_text = None
        if payload is not None:
            gh_arguments.extend(("--input", "-"))
            input_text = json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
            )
        try:
            result = subprocess.run(
                ["bash", "-c", 'gh "$@"', "gh", *gh_arguments],
                check=True,
                capture_output=True,
                text=True,
                input=input_text,
            )
        except subprocess.CalledProcessError as error:
            stderr = error.stderr or ""
            raise GitHubApiError(
                stderr.strip() or f"GitHub API request failed: {endpoint}",
                _http_status(stderr),
            ) from error
        if not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise GitHubApiError(
                f"GitHub returned invalid JSON for {endpoint}"
            ) from error


@dataclass(frozen=True)
class GitTree:
    head_sha: str
    tree_sha: str
    entries: dict[str, str]


class TelemetryStore:
    def __init__(
        self,
        repository: str,
        *,
        api: GitHubApi | None = None,
        branch: str = TELEMETRY_BRANCH,
    ):
        if not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
            repository,
        ):
            raise TelemetryError("repository full name is invalid")
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
            raise TelemetryError("telemetry branch is invalid")
        self.repository = repository
        self.api = api or GitHubApi()
        self.branch = branch

    def _endpoint(self, suffix: str) -> str:
        return f"repos/{self.repository}/{suffix}"

    def _request_optional(self, endpoint: str) -> Any | None:
        try:
            return self.api.request(endpoint)
        except GitHubApiError as error:
            if error.status == 404:
                return None
            raise

    def _load_tree(self) -> GitTree | None:
        encoded_branch = urllib.parse.quote(self.branch, safe="")
        reference = self._request_optional(
            self._endpoint(f"git/ref/heads/{encoded_branch}")
        )
        if reference is None:
            return None
        try:
            head_sha = reference["object"]["sha"]
        except (KeyError, TypeError) as error:
            raise TelemetryError("GitHub returned an invalid telemetry ref") from error
        require_sha(head_sha, "telemetry branch head")

        commit = self.api.request(self._endpoint(f"git/commits/{head_sha}"))
        try:
            tree_sha = commit["tree"]["sha"]
        except (KeyError, TypeError) as error:
            raise TelemetryError(
                "GitHub returned an invalid telemetry commit"
            ) from error
        require_sha(tree_sha, "telemetry tree SHA")

        tree = self.api.request(
            self._endpoint(f"git/trees/{tree_sha}?recursive=1")
        )
        if (
            not isinstance(tree, dict)
            or tree.get("truncated") is True
            or not isinstance(tree.get("tree"), list)
        ):
            raise TelemetryError("GitHub returned an incomplete telemetry tree")
        if len(tree["tree"]) > MAX_TREE_ENTRIES:
            raise TelemetryError("telemetry tree exceeds the supported size")
        entries: dict[str, str] = {}
        for entry in tree["tree"]:
            if (
                isinstance(entry, dict)
                and entry.get("type") == "blob"
                and isinstance(entry.get("path"), str)
                and isinstance(entry.get("sha"), str)
            ):
                entries[entry["path"]] = require_sha(
                    entry["sha"],
                    "telemetry blob SHA",
                )
        return GitTree(head_sha, tree_sha, entries)

    def _create_blob(self, content: bytes) -> str:
        response = self.api.request(
            self._endpoint("git/blobs"),
            method="POST",
            payload={
                "content": base64.b64encode(content).decode("ascii"),
                "encoding": "base64",
            },
        )
        try:
            return require_sha(response["sha"], "created blob SHA")
        except (KeyError, TypeError) as error:
            raise TelemetryError("GitHub returned no created blob SHA") from error

    def _create_tree(
        self,
        files: Mapping[str, bytes],
        *,
        base_tree: str | None,
    ) -> str:
        entries = []
        for path, content in sorted(files.items()):
            entries.append(
                {
                    "path": validate_storage_path(path),
                    "mode": "100644",
                    "type": "blob",
                    "sha": self._create_blob(content),
                }
            )
        payload: dict[str, Any] = {"tree": entries}
        if base_tree is not None:
            payload["base_tree"] = require_sha(base_tree, "base tree SHA")
        response = self.api.request(
            self._endpoint("git/trees"),
            method="POST",
            payload=payload,
        )
        try:
            return require_sha(response["sha"], "created tree SHA")
        except (KeyError, TypeError) as error:
            raise TelemetryError("GitHub returned no created tree SHA") from error

    def _create_commit(
        self,
        tree_sha: str,
        message: str,
        *,
        parent: str | None,
    ) -> str:
        if not message or "\0" in message:
            raise TelemetryError("telemetry commit message is invalid")
        payload: dict[str, Any] = {
            "message": message,
            "tree": require_sha(tree_sha, "tree SHA"),
        }
        if parent is not None:
            payload["parents"] = [require_sha(parent, "parent commit SHA")]
        else:
            payload["parents"] = []
        response = self.api.request(
            self._endpoint("git/commits"),
            method="POST",
            payload=payload,
        )
        try:
            return require_sha(response["sha"], "created commit SHA")
        except (KeyError, TypeError) as error:
            raise TelemetryError("GitHub returned no created commit SHA") from error

    def _create_ref(self, commit_sha: str) -> None:
        self.api.request(
            self._endpoint("git/refs"),
            method="POST",
            payload={
                "ref": f"refs/heads/{self.branch}",
                "sha": require_sha(commit_sha, "commit SHA"),
            },
        )

    def _update_ref(self, commit_sha: str) -> None:
        encoded_branch = urllib.parse.quote(self.branch, safe="")
        self.api.request(
            self._endpoint(f"git/refs/heads/{encoded_branch}"),
            method="PATCH",
            payload={
                "sha": require_sha(commit_sha, "commit SHA"),
                "force": False,
            },
        )

    @staticmethod
    def _is_retryable(error: GitHubApiError) -> bool:
        return error.status in {409, 422}

    def write_files(
        self,
        files: Mapping[str, bytes],
        *,
        message: str,
    ) -> None:
        if not files:
            return
        normalized: dict[str, bytes] = {}
        for path, content in files.items():
            validate_storage_path(path)
            if not isinstance(content, bytes) or not content:
                raise TelemetryError(
                    f"telemetry content for {path!r} must be non-empty bytes"
                )
            normalized[path] = content

        for _attempt in range(MAX_WRITE_ATTEMPTS):
            current = self._load_tree()
            pending: dict[str, bytes] = {}
            if current is not None:
                for path, content in normalized.items():
                    existing_sha = current.entries.get(path)
                    if existing_sha is None:
                        pending[path] = content
                    elif existing_sha != git_blob_sha(content):
                        raise TelemetryConflictError(
                            f"telemetry path already has different content: {path}"
                        )
                if not pending:
                    return
            else:
                pending = normalized

            tree_sha = self._create_tree(
                pending,
                base_tree=current.tree_sha if current else None,
            )
            commit_sha = self._create_commit(
                tree_sha,
                message,
                parent=current.head_sha if current else None,
            )
            try:
                if current is None:
                    self._create_ref(commit_sha)
                else:
                    self._update_ref(commit_sha)
                return
            except GitHubApiError as error:
                if not self._is_retryable(error):
                    raise
        raise TelemetryConflictError(
            "telemetry branch changed too often; retry the workflow"
        )

    def write_json_files(
        self,
        files: Mapping[str, Any],
        *,
        message: str,
    ) -> None:
        self.write_files(
            {path: canonical_json_bytes(value) for path, value in files.items()},
            message=message,
        )

    def write_records(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        extra_files: Mapping[str, Any] | None = None,
        message: str,
    ) -> None:
        files: dict[str, Any] = {}
        for record in records:
            path = record_path(record)
            if path in files:
                raise TelemetryError(f"duplicate telemetry record path: {path}")
            files[path] = validate_record(dict(record))
        if extra_files:
            for path, value in extra_files.items():
                validate_storage_path(path)
                if path in files:
                    raise TelemetryError(f"duplicate telemetry path: {path}")
                files[path] = value
        self.write_json_files(files, message=message)

    def read_json(self, path: str) -> Any | None:
        validate_storage_path(path)
        current = self._load_tree()
        if current is None:
            return None
        blob_sha = current.entries.get(path)
        if blob_sha is None:
            return None
        return self._read_blob_json(blob_sha)

    def _read_blob_json(self, blob_sha: str) -> Any:
        require_sha(blob_sha, "telemetry blob SHA")
        blob = self.api.request(self._endpoint(f"git/blobs/{blob_sha}"))
        try:
            encoding = blob["encoding"]
            content = blob["content"]
        except (KeyError, TypeError) as error:
            raise TelemetryError("GitHub returned an invalid telemetry blob") from error
        if encoding != "base64" or not isinstance(content, str):
            raise TelemetryError("GitHub returned an unsupported telemetry blob")
        try:
            decoded = base64.b64decode(
                "".join(content.split()),
                validate=True,
            )
            return json.loads(decoded)
        except (ValueError, json.JSONDecodeError) as error:
            raise TelemetryError("telemetry blob is not valid JSON") from error

    def list_json(
        self,
        prefix: str,
        *,
        maximum: int = 1000,
    ) -> list[Any]:
        validate_storage_path(prefix.rstrip("/"))
        if maximum < 1:
            raise TelemetryError("maximum must be positive")
        current = self._load_tree()
        if current is None:
            return []
        paths = sorted(
            path
            for path in current.entries
            if path.startswith(prefix) and path.endswith(".json")
        )
        if len(paths) > maximum:
            raise TelemetryError(
                f"telemetry prefix contains more than {maximum} records"
            )
        values = []
        for path in paths:
            values.append(self._read_blob_json(current.entries[path]))
        return values


def build_record(
    *,
    record_type: str,
    identity: Any,
    repository_id: int | str,
    repository: str,
    pull_request_number: int | str,
    data: Mapping[str, Any],
    recorded_at: str | None = None,
) -> dict[str, Any]:
    record_id, _digest = stable_identifier(
        "art",
        {
            "record_type": record_type,
            "identity": identity,
        },
    )
    record = {
        "schema_version": SCHEMA_VERSION,
        "record_type": record_type,
        "record_id": record_id,
        "recorded_at": recorded_at or utc_now(),
        "repository": {
            "id": require_repository_id(repository_id),
            "full_name": repository,
        },
        "pull_request": {
            "number": require_pull_request_number(pull_request_number),
        },
        "data": dict(data),
    }
    return validate_record(record)
