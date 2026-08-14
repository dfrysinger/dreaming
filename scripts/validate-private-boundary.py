#!/usr/bin/env python3
"""Reject private sentinels in tracked repository blobs or dashboard payloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


class BoundaryError(RuntimeError):
    pass


SENTINEL_NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}")


def load_sentinels(path: Path) -> dict[str, bytes]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BoundaryError("sentinel manifest is unavailable or invalid") from error
    if not isinstance(value, dict) or not value:
        raise BoundaryError("sentinel manifest must be a non-empty object")
    sentinels: dict[str, bytes] = {}
    for name, sentinel in value.items():
        if (
            not isinstance(name, str)
            or not SENTINEL_NAME_RE.fullmatch(name)
            or not isinstance(sentinel, str)
            or len(sentinel) < 16
        ):
            raise BoundaryError(
                "sentinel names must be bounded identifiers and values at least 16 characters"
            )
        encoded = sentinel.encode("utf-8")
        if encoded in name.encode("utf-8"):
            raise BoundaryError("sentinel names must not contain sentinel values")
        if encoded in sentinels.values():
            raise BoundaryError("sentinel values must be unique")
        sentinels[name] = encoded
    return sentinels


def git(repository: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    process = subprocess.run(
        ["/usr/bin/git", "-C", str(repository), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0:
        raise BoundaryError("Git repository inspection failed")
    return process.stdout


def tracked_blobs(repository: Path) -> list[tuple[str, str]]:
    output = git(repository, "ls-files", "-s", "-z")
    blobs: list[tuple[str, str]] = []
    for record in output.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, digest, stage = metadata.decode("ascii").split()
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise BoundaryError("tracked-file inventory is malformed") from error
        if stage != "0":
            raise BoundaryError("repository index has unresolved stages")
        if mode not in {"100644", "100755", "120000"}:
            continue
        blobs.append((path, digest))
    return blobs


def violations(
    repository: Path,
    responses: list[Path],
    sentinels: dict[str, bytes],
) -> list[str]:
    found: list[str] = []
    for path, digest in tracked_blobs(repository):
        content = git(repository, "cat-file", "blob", digest)
        for name, sentinel in sentinels.items():
            if sentinel in content:
                category = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
                surface = hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
                found.append(f"category={category}:repository={surface}")
    for index, response in enumerate(responses):
        try:
            content = response.read_bytes()
        except OSError as error:
            raise BoundaryError("dashboard response is unavailable") from error
        for name, sentinel in sentinels.items():
            if sentinel in content:
                category = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
                found.append(f"category={category}:dashboard={index}")
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--sentinels", type=Path, required=True)
    parser.add_argument("--response", type=Path, action="append", default=[])
    args = parser.parse_args()
    try:
        repository = args.repository.expanduser().resolve()
        if not repository.is_dir():
            raise BoundaryError("repository path is not a directory")
        top_level = Path(
            git(repository, "rev-parse", "--show-toplevel")
            .decode("utf-8")
            .strip()
        ).resolve()
        if top_level != repository:
            raise BoundaryError("repository path must be the Git top level")
        sentinels = load_sentinels(args.sentinels.expanduser().resolve())
        found = violations(
            repository,
            [path.expanduser().resolve() for path in args.response],
            sentinels,
        )
        if found:
            for item in found:
                print(f"private-boundary violation: {item}", file=sys.stderr)
            return 1
    except BoundaryError as error:
        print(f"private-boundary refused: {error}", file=sys.stderr)
        return 2
    print("private boundary validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
