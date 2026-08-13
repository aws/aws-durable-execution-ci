#!/usr/bin/env python3

import argparse
import base64
import binascii
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote


MAX_PROMPT_BYTES = 64 * 1024
MAX_PROMPT_PATH_LENGTH = 1024
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}")


class PromptError(ValueError):
    pass


def validate_prompt_bytes(data: bytes, description: str) -> str:
    if len(data) > MAX_PROMPT_BYTES:
        raise PromptError(
            f"{description} exceeds the {MAX_PROMPT_BYTES}-byte limit"
        )

    try:
        prompt = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PromptError(f"{description} must be valid UTF-8") from error

    if "\0" in prompt:
        raise PromptError(f"{description} must not contain NUL bytes")
    if not prompt.strip():
        raise PromptError(f"{description} must not be empty")
    return prompt


def read_prompt(path: Path, description: str) -> str:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise PromptError(f"{description} is unavailable") from error
    return validate_prompt_bytes(data, description)


def validate_custom_prompt_path(value: str) -> str:
    if not value:
        raise PromptError("custom prompt path must not be empty")
    if len(value) > MAX_PROMPT_PATH_LENGTH:
        raise PromptError("custom prompt path is too long")
    if value.startswith("/"):
        raise PromptError("custom prompt path must be repository-relative")
    if any(character in value for character in ("\0", "\n", "\r", "\\")):
        raise PromptError("custom prompt path contains an invalid character")

    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise PromptError("custom prompt path must be normalized")
    return value


def validate_repository(value: str) -> str:
    if not REPOSITORY_PATTERN.fullmatch(value):
        raise PromptError("caller repository is invalid")
    return value


def validate_sha(value: str) -> str:
    if not SHA_PATTERN.fullmatch(value):
        raise PromptError("caller SHA must be a full 40-character commit SHA")
    return value


def run_gh_json(endpoint: str) -> object:
    result = subprocess.run(
        ["gh", "api", endpoint],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise PromptError("GitHub could not retrieve the custom prompt")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PromptError("GitHub returned invalid prompt metadata") from error


def fetch_custom_prompt(
    repository: str,
    caller_sha: str,
    prompt_path: str,
) -> str:
    repository = validate_repository(repository)
    caller_sha = validate_sha(caller_sha)
    prompt_path = validate_custom_prompt_path(prompt_path)
    endpoint = (
        f"repos/{quote(repository, safe='/')}/contents/"
        f"{quote(prompt_path, safe='/')}?ref={quote(caller_sha, safe='')}"
    )
    response = run_gh_json(endpoint)

    if not isinstance(response, dict):
        raise PromptError("custom prompt metadata must describe one file")
    if response.get("type") != "file" or response.get("encoding") != "base64":
        raise PromptError("custom prompt must be a regular repository file")

    encoded = response.get("content")
    if not isinstance(encoded, str):
        raise PromptError("custom prompt content is unavailable")

    try:
        data = base64.b64decode("".join(encoded.splitlines()), validate=True)
    except (binascii.Error, ValueError) as error:
        raise PromptError("custom prompt content is not valid base64") from error
    return validate_prompt_bytes(data, "custom prompt")


def write_prompt(path: Path, prompt: str) -> None:
    parent = path.parent
    if not parent.is_dir():
        raise PromptError("prompt output directory is unavailable")

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            output.write(prompt)
            output.flush()
            os.fsync(output.fileno())
        temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
    except OSError as error:
        raise PromptError("could not write the resolved prompt") from error
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def resolve_prompt(
    default_prompt_path: Path,
    required_instructions_path: Path,
    output_path: Path,
    custom_prompt_path: str,
    repository: str,
    caller_sha: str,
) -> None:
    if custom_prompt_path:
        classification_prompt = fetch_custom_prompt(
            repository,
            caller_sha,
            custom_prompt_path,
        )
    else:
        classification_prompt = read_prompt(
            default_prompt_path,
            "default classification prompt",
        )

    required_instructions = read_prompt(
        required_instructions_path,
        "required security and output instructions",
    )
    combined_prompt = (
        classification_prompt.rstrip()
        + "\n\n"
        + required_instructions.rstrip()
        + "\n"
    )
    write_prompt(output_path, combined_prompt)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("default_prompt", type=Path)
    parser.add_argument("required_instructions", type=Path)
    parser.add_argument("output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        resolve_prompt(
            args.default_prompt,
            args.required_instructions,
            args.output,
            os.environ.get("CUSTOM_PROMPT_PATH", ""),
            os.environ.get("CALLER_REPOSITORY", ""),
            os.environ.get("CALLER_SHA", ""),
        )
    except PromptError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
