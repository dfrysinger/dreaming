#!/usr/bin/env python3
"""Shared content policy for transported remote evaluation subjects."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

REMOTE_SUBJECT_SIDECARS = {
    ".agent-created",
    ".agent-created.json",
    ".promotion-reviewed.json",
    ".skill-evaluation-cases.json",
    ".skill-evaluation-policy.json",
    ".pinned",
}


class RemoteSubjectPolicyError(RuntimeError):
    pass


def load_content_policy(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RemoteSubjectPolicyError(
            "remote subject content policy is unavailable"
        )
    try:
        raw = path.read_bytes()
        policy = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise RemoteSubjectPolicyError(
            "remote subject content policy is malformed"
        ) from error
    patterns = policy.get("denied_patterns") if isinstance(policy, dict) else None
    if (
        set(policy) != {"schema_version", "kind", "denied_patterns"}
        or policy.get("schema_version") != 1
        or policy.get("kind") != "remote_subject_content_policy"
        or not isinstance(patterns, list)
        or not patterns
    ):
        raise RemoteSubjectPolicyError(
            "remote subject content policy is malformed"
        )
    compiled = []
    for item in patterns:
        if (
            not isinstance(item, dict)
            or set(item) != {"label", "pattern"}
            or not isinstance(item.get("label"), str)
            or not item["label"]
            or not isinstance(item.get("pattern"), str)
            or not item["pattern"]
        ):
            raise RemoteSubjectPolicyError(
                "remote subject content policy entry is malformed"
            )
        try:
            compiled.append((re.compile(item["pattern"]), item["label"]))
        except re.error as error:
            raise RemoteSubjectPolicyError(
                "remote subject content policy regex is invalid"
            ) from error
    return {
        "schema_version": 1,
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "patterns": compiled,
    }


def validate_text(
    content: bytes, relative: str, policy: dict[str, Any]
) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RemoteSubjectPolicyError(
            f"{relative}: remote subject content is not UTF-8"
        ) from error
    if any(
        ord(character) < 32 and character not in "\n\r\t"
        for character in text
    ):
        raise RemoteSubjectPolicyError(
            f"{relative}: remote subject content has control bytes"
        )
    for pattern, label in policy["patterns"]:
        if pattern.search(text):
            raise RemoteSubjectPolicyError(
                f"{relative}: remote subject content contains {label}"
            )
