"""Immutable canonical task-occurrence resolutions and bounded corrections."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn


RESOLUTION_SCHEMA_VERSION = 2
INDEX_SCHEMA_VERSION = 1
CORRECTION_SCHEMA_VERSION = 1
BOUNDARY_RELATIONS = {
    "same-occurrence",
    "new-occurrence",
    "boundary-conflict",
    "boundary-unresolved",
}


class TaskOccurrenceError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def _reject(reason: str) -> NoReturn:
    raise TaskOccurrenceError(reason)


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != 71
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        _reject(field)
    return value


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z") or len(value) < 20:
        _reject(field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _reject(field)
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        _reject(field)
    return value


def _read(path: Path, reason: str) -> Any:
    if path.is_symlink() or not path.is_file():
        _reject(reason)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TaskOccurrenceError(reason) from error


def _write_immutable(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        _reject("immutable-path")
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}"
    try:
        with temporary.open("xb") as handle:
            handle.write(canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o400)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_resolution(value: Any) -> dict[str, Any]:
    expected_keys = {
        "schema_version",
        "kind",
        "profile_id",
        "profile_sha256",
        "task_key",
        "source_event_ids",
        "profile_receipt_sha256",
        "snapshot_sha256",
        "source_revision",
        "qualified_session_id",
        "goal_event_id",
        "occurred_at",
        "decision_at",
        "boundary_relation",
        "canonical_occurrence_id",
        "prior_canonical_occurrence_ids",
        "overlap_resolution_ids",
        "review_contract",
        "review_executor",
        "review_executor_identity",
        "correction_attempt_sha256",
        "supersedes_resolution_sha256",
        "resolution_sha256",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        _reject("resolution-shape")
    body = {
        key: item for key, item in value.items() if key != "resolution_sha256"
    }
    if (
        value["schema_version"] != RESOLUTION_SCHEMA_VERSION
        or value["kind"] != "task_occurrence_resolution"
        or value["resolution_sha256"] != digest(body)
    ):
        _reject("resolution-identity")
    for key in (
        "profile_id",
        "profile_sha256",
        "task_key",
        "profile_receipt_sha256",
        "snapshot_sha256",
    ):
        _sha(value[key], key)
    relation = value["boundary_relation"]
    if relation not in BOUNDARY_RELATIONS:
        _reject("boundary-relation")
    source_event_ids = value["source_event_ids"]
    if (
        not isinstance(source_event_ids, list)
        or not source_event_ids
        or len(source_event_ids) != len(set(source_event_ids))
        or any(not isinstance(item, str) or not item for item in source_event_ids)
    ):
        _reject("source-event-ids")
    prior_ids = value["prior_canonical_occurrence_ids"]
    overlap_ids = value["overlap_resolution_ids"]
    if (
        not isinstance(prior_ids, list)
        or len(prior_ids) != len(set(prior_ids))
        or any(_sha(item, "prior-occurrence") != item for item in prior_ids)
        or not isinstance(overlap_ids, list)
        or len(overlap_ids) != len(set(overlap_ids))
        or any(_sha(item, "overlap-resolution") != item for item in overlap_ids)
    ):
        _reject("overlap-identities")
    occurrence_id = value["canonical_occurrence_id"]
    if occurrence_id is not None:
        _sha(occurrence_id, "canonical-occurrence-id")
    if relation == "same-occurrence":
        if len(prior_ids) != 1 or occurrence_id != prior_ids[0]:
            _reject("same-occurrence-alias")
    elif relation == "new-occurrence":
        if prior_ids or occurrence_id is None:
            _reject("new-occurrence-identity")
    elif occurrence_id is not None:
        _reject("conflict-occurrence")
    if relation == "boundary-conflict" and not prior_ids:
        _reject("conflict-prior-occurrences")
    correction_attempt = value["correction_attempt_sha256"]
    supersedes = value["supersedes_resolution_sha256"]
    if correction_attempt is not None:
        _sha(correction_attempt, "correction-attempt")
    if supersedes is not None:
        _sha(supersedes, "superseded-resolution")
    if (correction_attempt is None) != (supersedes is None):
        _reject("correction-binding")
    for key in (
        "source_revision",
        "qualified_session_id",
        "goal_event_id",
        "review_contract",
        "review_executor",
    ):
        if not isinstance(value[key], str) or not value[key]:
            _reject("resolution-metadata")
    if (
        not isinstance(value["review_executor_identity"], dict)
        or not value["review_executor_identity"]
    ):
        _reject("review-executor-identity")
    occurred_at = _timestamp(value["occurred_at"], "occurred-at")
    decision_at = _timestamp(value["decision_at"], "decision-at")
    if datetime.fromisoformat(occurred_at.replace("Z", "+00:00")) > datetime.fromisoformat(
        decision_at.replace("Z", "+00:00")
    ):
        _reject("future-occurrence")
    return value


def build_resolution(
    *,
    profile: dict[str, Any],
    receipt: dict[str, Any],
    relation: str,
    review_contract: str,
    review_executor: str,
    review_executor_identity: dict[str, Any],
    decision_at: str,
    prior_occurrence_ids: list[str] | None = None,
    overlap_resolution_ids: list[str] | None = None,
    correction_attempt_sha256: str | None = None,
    supersedes_resolution_sha256: str | None = None,
) -> dict[str, Any]:
    if receipt.get("schema_version") != 2:
        _reject("legacy-receipt-no-authority")
    required = (
        "profile_id",
        "task_key",
        "source_event_ids",
        "goal_event_id",
        "occurred_at",
    )
    if any(profile.get(key) is None or profile.get(key) == "" for key in required):
        _reject("profile-anchor")
    prior_ids = sorted(set(prior_occurrence_ids or []))
    overlaps = sorted(set(overlap_resolution_ids or []))
    if relation == "same-occurrence":
        if len(prior_ids) != 1:
            _reject("same-occurrence-prior")
        occurrence_id = prior_ids[0]
    elif relation == "new-occurrence":
        occurrence_id = digest(
            {
                "qualified_session_id": receipt.get("qualified_session_id"),
                "goal_event_id": profile["goal_event_id"],
                "source_event_ids": profile["source_event_ids"],
            }
        )
        prior_ids = []
    elif relation in {"boundary-conflict", "boundary-unresolved"}:
        occurrence_id = None
    else:
        _reject("boundary-relation")
    body = {
        "schema_version": RESOLUTION_SCHEMA_VERSION,
        "kind": "task_occurrence_resolution",
        "profile_id": profile["profile_id"],
        "profile_sha256": digest(profile),
        "task_key": profile["task_key"],
        "source_event_ids": profile["source_event_ids"],
        "profile_receipt_sha256": receipt.get("receipt_sha256"),
        "snapshot_sha256": receipt.get("snapshot_sha256"),
        "source_revision": receipt.get("source_revision"),
        "qualified_session_id": receipt.get("qualified_session_id"),
        "goal_event_id": profile["goal_event_id"],
        "occurred_at": profile["occurred_at"],
        "decision_at": decision_at,
        "boundary_relation": relation,
        "canonical_occurrence_id": occurrence_id,
        "prior_canonical_occurrence_ids": prior_ids,
        "overlap_resolution_ids": overlaps,
        "review_contract": review_contract,
        "review_executor": review_executor,
        "review_executor_identity": review_executor_identity,
        "correction_attempt_sha256": correction_attempt_sha256,
        "supersedes_resolution_sha256": supersedes_resolution_sha256,
    }
    return validate_resolution({**body, "resolution_sha256": digest(body)})


def validate_correction_attempt(value: Any) -> dict[str, Any]:
    expected_keys = {
        "schema_version",
        "kind",
        "qualified_session_id",
        "source_revision",
        "profile_receipt_sha256",
        "conflict_resolution_sha256s",
        "correction_contract",
        "profile_executor",
        "profile_executor_identity",
        "started_at",
        "terminal_status",
        "replacement_profile_receipt_sha256",
        "attempt_sha256",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        _reject("correction-shape")
    body = {key: item for key, item in value.items() if key != "attempt_sha256"}
    if (
        value["schema_version"] != CORRECTION_SCHEMA_VERSION
        or value["kind"] != "task_occurrence_correction_attempt"
        or value["attempt_sha256"] != digest(body)
    ):
        _reject("correction-identity")
    _sha(value["profile_receipt_sha256"], "profile_receipt_sha256")
    conflict_ids = value["conflict_resolution_sha256s"]
    if (
        not isinstance(conflict_ids, list)
        or not conflict_ids
        or len(conflict_ids) != len(set(conflict_ids))
        or any(_sha(item, "conflict-resolution") != item for item in conflict_ids)
    ):
        _reject("conflict-resolutions")
    replacement = value["replacement_profile_receipt_sha256"]
    if replacement is not None:
        _sha(replacement, "replacement-receipt")
    if value["terminal_status"] not in {
        "replacement-profiled",
        "boundary-unresolved",
        "failed",
    }:
        _reject("correction-status")
    if (
        value["terminal_status"] == "replacement-profiled"
    ) != (replacement is not None):
        _reject("correction-result")
    for key in (
        "qualified_session_id",
        "source_revision",
        "correction_contract",
        "profile_executor",
    ):
        if not isinstance(value[key], str) or not value[key]:
            _reject("correction-metadata")
    if (
        not isinstance(value["profile_executor_identity"], dict)
        or not value["profile_executor_identity"]
    ):
        _reject("correction-executor-identity")
    _timestamp(value["started_at"], "correction-started-at")
    return value


def build_correction_attempt(
    *,
    qualified_session_id: str,
    source_revision: str,
    profile_receipt_sha256: str,
    conflict_resolution_sha256s: list[str],
    correction_contract: str,
    profile_executor: str,
    profile_executor_identity: dict[str, Any],
    started_at: str,
    terminal_status: str,
    replacement_profile_receipt_sha256: str | None,
) -> dict[str, Any]:
    body = {
        "schema_version": CORRECTION_SCHEMA_VERSION,
        "kind": "task_occurrence_correction_attempt",
        "qualified_session_id": qualified_session_id,
        "source_revision": source_revision,
        "profile_receipt_sha256": profile_receipt_sha256,
        "conflict_resolution_sha256s": sorted(
            set(conflict_resolution_sha256s)
        ),
        "correction_contract": correction_contract,
        "profile_executor": profile_executor,
        "profile_executor_identity": profile_executor_identity,
        "started_at": started_at,
        "terminal_status": terminal_status,
        "replacement_profile_receipt_sha256": replacement_profile_receipt_sha256,
    }
    return validate_correction_attempt({**body, "attempt_sha256": digest(body)})


def validate_index(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "kind",
        "entries",
    }:
        _reject("index-shape")
    if (
        value["schema_version"] != INDEX_SCHEMA_VERSION
        or value["kind"] != "task_occurrence_index"
        or not isinstance(value["entries"], dict)
    ):
        _reject("index-contract")
    for task_key, entry in value["entries"].items():
        _sha(task_key, "index-task-key")
        if not isinstance(entry, dict) or set(entry) != {
            "resolution_sha256",
            "canonical_occurrence_id",
        }:
            _reject("index-entry")
        _sha(entry["resolution_sha256"], "index-resolution")
        if entry["canonical_occurrence_id"] is not None:
            _sha(entry["canonical_occurrence_id"], "index-occurrence")
    return value


def resolution_path(root: Path, resolution_sha256: str) -> Path:
    return root / (
        f"{_sha(resolution_sha256, 'resolution-sha256').removeprefix('sha256:')}.json"
    )


def correction_attempt_path(root: Path, attempt_sha256: str) -> Path:
    return root / (
        f"{_sha(attempt_sha256, 'attempt-sha256').removeprefix('sha256:')}.json"
    )


def load_exact(
    root: Path, index_path: Path, task_key: str
) -> dict[str, Any] | None:
    _sha(task_key, "task-key")
    if not index_path.exists():
        return None
    if index_path.is_symlink():
        _reject("index-read")
    index = validate_index(_read(index_path, "index-read"))
    entry = index["entries"].get(task_key)
    if entry is None:
        return None
    resolution = validate_resolution(
        _read(
            resolution_path(root, entry["resolution_sha256"]),
            "resolution-read",
        )
    )
    if (
        resolution["resolution_sha256"] != entry["resolution_sha256"]
        or resolution["task_key"] != task_key
        or resolution["canonical_occurrence_id"]
        != entry["canonical_occurrence_id"]
    ):
        _reject("index-provenance")
    return resolution


def load_all(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        _reject("resolution-root")
    return [
        validate_resolution(_read(path, "resolution-read"))
        for path in sorted(root.glob("*.json"))
    ]


def persist(
    root: Path,
    index_path: Path,
    resolution: dict[str, Any],
    *,
    project: bool = True,
) -> dict[str, Any]:
    resolution = validate_resolution(resolution)
    path = resolution_path(root, resolution["resolution_sha256"])
    if path.exists():
        if _read(path, "resolution-read") != resolution:
            _reject("resolution-collision")
    else:
        _write_immutable(path, resolution)
    if not project:
        return resolution
    index_path.parent.mkdir(parents=True, exist_ok=True)
    if index_path.parent.is_symlink() or index_path.is_symlink():
        _reject("index-path")
    lock_path = index_path.with_suffix(index_path.suffix + ".lock")
    with lock_path.open("a+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        index = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "kind": "task_occurrence_index",
            "entries": {},
        }
        if index_path.exists():
            index = validate_index(_read(index_path, "index-read"))
        entries = dict(index["entries"])
        prior = entries.get(resolution["task_key"])
        entry = {
            "resolution_sha256": resolution["resolution_sha256"],
            "canonical_occurrence_id": resolution[
                "canonical_occurrence_id"
            ],
        }
        if prior is not None and prior != entry:
            if (
                resolution["supersedes_resolution_sha256"]
                != prior["resolution_sha256"]
                or prior["canonical_occurrence_id"] is not None
            ):
                _reject("index-task-key-conflict")
        entries[resolution["task_key"]] = entry
        temporary = (
            index_path.parent / f".{index_path.name}.{uuid.uuid4().hex}"
        )
        try:
            with temporary.open("xb") as handle:
                handle.write(
                    canonical({**index, "entries": entries}) + b"\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, index_path)
        finally:
            temporary.unlink(missing_ok=True)
    return resolution


def persist_correction_attempt(
    root: Path, attempt: dict[str, Any]
) -> dict[str, Any]:
    attempt = validate_correction_attempt(attempt)
    path = correction_attempt_path(root, attempt["attempt_sha256"])
    if path.exists():
        if _read(path, "correction-read") != attempt:
            _reject("correction-collision")
    else:
        _write_immutable(path, attempt)
    return attempt


def load_correction_attempts(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        _reject("correction-root")
    return [
        validate_correction_attempt(_read(path, "correction-read"))
        for path in sorted(root.glob("*.json"))
    ]
