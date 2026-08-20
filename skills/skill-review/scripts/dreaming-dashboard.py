#!/usr/bin/env python3
"""Private read-only localhost dashboard for Dreaming."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import threading
import time
import urllib.parse
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any

SCHEMA_VERSION = 1
SKILL_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
OBSERVED_SKILL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9:-]{0,198}[a-z0-9])?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 1_100_000
PREVIEW_MANIFEST_NAME = "dashboard-preview-manifest.json"
PREVIEW_ROOT_NAMES = (
    "state",
    "control_state",
    "review_state",
    "orchestrator_state",
    "data",
    "skills",
)
PREVIEW_DATA_SUBTREES = (
    "snapshots",
    "candidates/v1/packages",
    "bundles",
)
PREVIEW_LOCK_RUNTIME_NAMES = (
    "daemon.lock",
    "daemon.lock-wal",
    "daemon.lock-shm",
    "daemon.lock-journal",
)
PREVIEW_CAPTURE_SECONDS = 30
PREVIEW_ELIGIBILITY_MARGIN_SECONDS = 10 * 60
PREVIEW_RELEASE_RESERVE_SECONDS = 2
DEFAULT_LIMIT = 25
MAX_LIMIT = 100
EVALUATION_SIDECARS = {
    ".agent-created",
    ".agent-created.json",
    ".promotion-reviewed.json",
    ".skill-evaluation-cases.json",
    ".skill-evaluation-policy.json",
    ".pinned",
}
ESTATE_AUTHORITIES = {
    "cli_builtin",
    "dreaming_managed",
    "legacy_machine",
    "plugin_managed",
    "unknown_provenance",
    "user_protected",
}
ESTATE_ROOT_CLASSES = {
    "builtin",
    "custom",
    "dreaming_publisher",
    "personal",
    "plugin",
    "project",
}
CANDIDATE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CANDIDATE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
CANDIDATE_REASON_RE = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")
CANDIDATE_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
MAX_CANDIDATE_RECORD_BYTES = 1_000_000
MAX_CANDIDATE_PACKAGE_FILES = 512
MAX_CANDIDATE_PACKAGE_BYTES = 8 * 1024 * 1024
CANDIDATE_FRESH_SECONDS = 30 * 86400
CANDIDATE_AUTHORITY = "shadow-only"
EVALUATION_OVERLAY_REGISTRY_IDENTITY = {
    "claim_schema_version": 4,
    "input_registry_schema_version": 1,
    "runner_version": "skill-evaluation-runner-1",
}
CANDIDATE_LABEL = "Shadow-only candidate — not an active skill, not published"
CANDIDATE_NOTICE = (
    "Shadow-only candidate record. It is not an active skill, is not published to any "
    "target, and is not visible to any CLI. Nothing here can be activated from this "
    "read-only dashboard."
)
CANDIDATE_INVALID_LABEL = (
    "Shadow-only candidate record, unavailable or invalid — not an active skill, "
    "not published"
)
CANDIDATE_RECORD_KEYS = {
    "schema_version",
    "lifecycle_id",
    "state",
    "authority",
    "proposed_name",
    "procedure",
    "evidence",
    "candidate_revisions",
    "current_candidate_id",
    "evaluation",
    "publication",
    "lifecycle",
    "aliases",
    "absorbed_into",
    "match_decisions",
    "blockers",
    "record_version",
}
CANDIDATE_PROCEDURE_KEYS = {
    "schema_version",
    "trigger",
    "outcome",
    "actions",
    "exclusions",
    "match_fingerprint",
}
CANDIDATE_EVIDENCE_KEYS = {
    "evidence_id",
    "task_key",
    "session_id",
    "observed_at",
    "independence",
    "summary",
    "procedure_fingerprint",
}
CANDIDATE_REVISION_KEYS = {"candidate_id", "package_path", "files", "staged_at"}
CANDIDATE_FILE_KEYS = {"path", "sha256", "size"}
CANDIDATE_LIFECYCLE_KEYS = {
    "created_at",
    "last_supported_at",
    "expires_at",
    "transition_history",
}
CANDIDATE_TRANSITION_KEYS = {
    "transition_id",
    "from_state",
    "to_state",
    "at",
    "reason",
    "authorizing_evidence_ids",
    "receipt_ids",
}
CANDIDATE_EVALUATION_KEYS = {"status", "last_evaluated_at", "history"}
CANDIDATE_EVALUATION_HISTORY_KEYS = {
    "evaluation_id",
    "evaluated_at",
    "recommendation",
    "reasons",
    "candidate_id",
    "shadow_only",
}
CANDIDATE_DECISION_KEYS = {
    "decision_id",
    "at",
    "outcome",
    "reason",
    "related_lifecycle_id",
    "evidence_ids",
    "shadow_only",
}
CANDIDATE_BLOCKER_KEYS = {"covering_lifecycle_ids", "tombstone_ids", "uncertain"}
CANDIDATE_STATE_LABELS = {
    "collecting": "Collecting evidence (shadow-only, not active)",
    "ready_for_draft": "Ready for draft (shadow-only, not active)",
    "evaluating": "Evaluating (shadow-only, not active)",
    "expired": "Expired (shadow-only, not active)",
    "rejected": "Rejected (shadow-only, not active)",
    "absorbed": "Absorbed (shadow-only, not active)",
}
CANDIDATE_INITIAL_STATES = {"collecting"}
CANDIDATE_TRANSITIONS = {
    "collecting": {"ready_for_draft", "expired", "rejected", "absorbed"},
    "ready_for_draft": {"collecting", "evaluating", "expired", "rejected", "absorbed"},
    "evaluating": {"collecting", "ready_for_draft", "expired", "rejected", "absorbed"},
    "expired": {"collecting", "rejected", "absorbed"},
    "rejected": {"collecting", "absorbed"},
    "absorbed": set(),
}
CANDIDATE_MATCH_OUTCOMES = {
    "same",
    "different",
    "uncertain",
    "duplicate",
    "supersedes",
    "absorbs",
}
CANDIDATE_EVALUATION_LABELS = {
    "not_evaluated": "Recurrence gate not evaluated",
    "shadow_ready": "Recurrence gate passed in shadow (no publication authority)",
    "not_ready": "Recurrence gate not passed",
}
CANDIDATE_RECOMMENDATION_LABELS = {
    "ready_for_draft": (
        "Shadow-only recommendation: draft this candidate. It does not activate, "
        "publish, or admit anything."
    ),
    "collecting": (
        "Shadow-only recommendation: keep collecting evidence. It does not activate, "
        "publish, or admit anything."
    ),
    "none": "No shadow recommendation has been recorded for this candidate",
}
CANDIDATE_GATE_LABELS = {
    "recurrence": "Recurrence and independence",
    "routing": "Routing trials",
    "task_value": "Paired task-value trials",
}
CANDIDATE_UNEVALUATED_GATES = ("routing", "task_value")


class DashboardError(RuntimeError):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        sources: list[str] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.sources = sources or []


class CandidateInvalid(RuntimeError):
    """One shadow candidate record, package, or reference is untrustworthy."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def candidate_canonical(value: Any) -> bytes:
    """Canonical bytes exactly as the candidate lifecycle owner writes them."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def candidate_sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(candidate_canonical(value)).hexdigest()


def candidate_require(condition: Any, reason: str, detail: str = "") -> None:
    if not condition:
        raise CandidateInvalid(reason, detail)


def candidate_keys(value: Any, keys: set[str], reason: str) -> dict[str, Any]:
    candidate_require(isinstance(value, dict) and set(value) == keys, reason)
    return value


def candidate_text(value: Any, reason: str, limit: int = 4000) -> str:
    candidate_require(
        isinstance(value, str) and value.strip() and len(value) <= limit, reason
    )
    return value


def candidate_time(value: Any, reason: str) -> float:
    parsed = parse_time(value) if isinstance(value, str) else None
    candidate_require(parsed is not None, reason)
    return parsed


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def safe_text(value: Any, limit: int = 4000) -> str:
    if not isinstance(value, str):
        return ""
    encoded = value.encode("utf-8")
    return (
        value
        if len(encoded) <= limit
        else encoded[:limit].decode("utf-8", errors="ignore")
    )


def fallback_name(session_id: str) -> str:
    native = session_id.split(":", 1)[-1]
    short = "".join(char if char.isprintable() else "?" for char in native)[:8]
    return f"Untitled dream · {short or 'unknown'}"


@dataclass(frozen=True)
class DashboardPaths:
    state: Path
    control_state: Path
    review_state: Path
    orchestrator_state: Path
    data: Path
    skills: Path
    repo: Path
    assets: Path
    token: Path
    candidate_records: Path | None = None
    candidate_packages: Path | None = None
    preview_root: Path | None = None
    preview_manifest: Path | None = None

    @classmethod
    def defaults(cls) -> "DashboardPaths":
        repo = Path(
            os.environ.get("DREAMING_REPO_ROOT", Path(__file__).parents[3])
        ).resolve()
        data = Path(
            os.environ.get(
                "DREAMING_DATA_DIR",
                Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
                / "dreaming",
            )
        ).resolve()
        state = Path(
            os.environ.get(
                "DREAMING_STATE_DIR",
                Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
                / "dreaming",
            )
        ).resolve()
        control_state = Path(
            os.environ.get("SKILLS_STATE_DIR", state)
        ).resolve()
        review_state = Path(
            os.environ.get(
                "SKILLS_REVIEW_STATE_DIR", control_state / "skill-review"
            )
        ).resolve()
        orchestrator_state = Path(
            os.environ.get("DREAMING_ORCHESTRATOR_STATE_DIR", state / "orchestrator")
        ).resolve()
        skills = Path(os.environ.get("DREAMING_SKILLS_ROOT", data / "skills")).resolve()
        assets = Path(
            os.environ.get(
                "DREAMING_DASHBOARD_ASSETS",
                repo / "skills/skill-review/assets/dashboard",
            )
        ).resolve()
        token = Path(
            os.environ.get(
                "DREAMING_DASHBOARD_TOKEN_FILE",
                state / "dashboard/access-token",
            )
        )
        candidate_records = (
            Path(os.environ.get("DREAMING_STATE_ROOT", state)).resolve()
            / "skill-review/candidates/v1/records"
        )
        candidate_packages = (
            Path(os.environ.get("DREAMING_DATA_ROOT", data)).resolve()
            / "candidates/v1/packages"
        )
        return cls(
            state,
            control_state,
            review_state,
            orchestrator_state,
            data,
            skills,
            repo,
            assets,
            token,
            candidate_records,
            candidate_packages,
        )

    @classmethod
    def preview(cls, root: Path, assets: Path) -> "DashboardPaths":
        """Build paths exclusively from a verified private preview snapshot."""
        root = root.absolute()
        manifest = root / PREVIEW_MANIFEST_NAME
        paths = cls(
            state=root / "state",
            control_state=root / "control_state",
            review_state=root / "review_state",
            orchestrator_state=root / "orchestrator_state",
            data=root / "data",
            skills=root / "skills",
            repo=root,
            assets=_preview_assets_path(assets),
            token=root / "state" / "dashboard" / "access-token",
            candidate_records=root / "state" / "skill-review/candidates/v1/records",
            candidate_packages=root / "data" / "candidates/v1/packages",
            preview_root=root,
            preview_manifest=manifest,
        )
        verify_preview_manifest(paths)
        return paths


def _path_components(path: Path) -> list[Path]:
    absolute = path.absolute()
    parts = absolute.parts
    current = Path(parts[0])
    result = [current]
    for part in parts[1:]:
        current /= part
        result.append(current)
    return result


def _require_real_directory(path: Path, source: str) -> None:
    try:
        for component in _path_components(path):
            if component.is_symlink():
                raise DashboardError(
                    503, "preview_path_invalid", f"{source} contains a symlink", [source]
                )
        info = path.stat()
    except OSError as exc:
        raise DashboardError(
            503, "preview_path_invalid", f"{source} is unavailable", [source]
        ) from exc
    if not stat.S_ISDIR(info.st_mode):
        raise DashboardError(
            503, "preview_path_invalid", f"{source} is not a directory", [source]
        )


def _preview_assets_path(assets: Path) -> Path:
    expected = Path(__file__).resolve().parents[1] / "assets/dashboard"
    provided = assets.absolute()
    _require_real_directory(expected, "preview assets")
    _require_real_directory(provided, "preview assets")
    if provided.resolve() != expected.resolve():
        raise DashboardError(
            503,
            "preview_assets_invalid",
            "Preview assets must belong to this preview worktree",
        )
    return expected


def _validated_capture_destination(
    destination: Path, source_roots: dict[str, Path]
) -> Path:
    lexical = destination.absolute()
    if lexical.exists() or lexical.is_symlink():
        raise DashboardError(
            2, "preview_capture_exists", "Preview destination already exists"
        )
    _require_real_directory(lexical.parent, "preview destination parent")
    effective = lexical.resolve(strict=False)
    for root in source_roots.values():
        try:
            if effective.is_relative_to(root.resolve(strict=True)):
                raise DashboardError(
                    2,
                    "preview_capture_invalid",
                    "Preview destination must not be inside an input root",
                )
        except OSError as exc:
            raise DashboardError(
                2, "preview_capture_invalid", "Preview input root is unavailable"
            ) from exc
    return lexical


def _require_regular_path(path: Path, source: str) -> os.stat_result:
    try:
        for component in _path_components(path):
            if component.is_symlink():
                raise DashboardError(
                    503, "preview_path_invalid", f"{source} contains a symlink", [source]
                )
        info = path.stat()
    except OSError as exc:
        raise DashboardError(
            503, "preview_path_invalid", f"{source} is unavailable", [source]
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        raise DashboardError(
            503, "preview_path_invalid", f"{source} is not a regular file", [source]
        )
    return info


def _snapshot_files(
    root: Path, source: str, excluded: frozenset[str] = frozenset()
) -> dict[str, dict[str, Any]]:
    """Return a non-following, deterministic inventory rooted at ``root``."""
    try:
        if root.is_symlink() or not root.is_dir():
            raise DashboardError(
                503, "preview_path_invalid", f"{source} is not a directory", [source]
            )
        for component in _path_components(root):
            if component.is_symlink():
                raise DashboardError(
                    503, "preview_path_invalid", f"{source} contains a symlink", [source]
                )
    except OSError as exc:
        raise DashboardError(
            503, "preview_path_invalid", f"{source} is unavailable", [source]
        ) from exc

    files: dict[str, dict[str, Any]] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise DashboardError(
                503, "preview_path_invalid", f"{source} is unreadable", [source]
            ) from exc
        for entry in entries:
            relative = entry.relative_to(root).as_posix()
            try:
                info = entry.lstat()
            except OSError as exc:
                raise DashboardError(
                    503, "preview_path_invalid", f"{source} changed during inspection", [source]
                ) from exc
            if relative in excluded:
                continue
            if stat.S_ISLNK(info.st_mode):
                raise DashboardError(
                    503, "preview_path_invalid", f"{source} contains a symlink", [source]
                )
            if stat.S_ISDIR(info.st_mode):
                pending.append(entry)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise DashboardError(
                    503, "preview_path_invalid", f"{source} contains a non-file", [source]
                )
            try:
                content = entry.read_bytes()
                after = entry.stat()
            except OSError as exc:
                raise DashboardError(
                    503, "preview_path_invalid", f"{source} changed during inspection", [source]
                ) from exc
            if (
                after.st_dev != info.st_dev
                or after.st_ino != info.st_ino
                or after.st_size != info.st_size
            ):
                raise DashboardError(
                    503, "preview_path_invalid", f"{source} changed during inspection", [source]
                )
            files[relative] = {
                "device": info.st_dev,
                "inode": info.st_ino,
                "size": info.st_size,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
    return files


def _preview_capture_files(
    name: str, root: Path, excluded: frozenset[str] = frozenset()
) -> dict[str, dict[str, Any]]:
    """Inventory only the data subtrees that report-only dashboard routes read."""
    if name != "data":
        return _snapshot_files(root, f"source {name}", excluded)
    # Validate the root itself, but deliberately do not traverse unrelated
    # data such as the dependency-bundle cache under data/deps.
    try:
        if root.is_symlink() or not root.is_dir():
            raise DashboardError(
                503, "preview_path_invalid", "source data is not a directory", ["source data"]
            )
        for component in _path_components(root):
            if component.is_symlink():
                raise DashboardError(
                    503, "preview_path_invalid", "source data contains a symlink", ["source data"]
                )
    except OSError as exc:
        raise DashboardError(
            503, "preview_path_invalid", "source data is unavailable", ["source data"]
        ) from exc
    files: dict[str, dict[str, Any]] = {}
    for relative in PREVIEW_DATA_SUBTREES:
        subtree = root / relative
        if not subtree.exists():
            continue
        for path in _path_components(subtree):
            if path.is_symlink():
                raise DashboardError(
                    503,
                    "preview_path_invalid",
                    f"source data/{relative} contains a symlink",
                    [f"data/{relative}"],
                )
        for path, identity in _snapshot_files(
            subtree, f"source data/{relative}"
        ).items():
            files[f"{relative}/{path}"] = identity
    return files


def _capture_lock_exclusions(
    roots: dict[str, Path], lock_path: Path
) -> dict[str, frozenset[str]]:
    """Exclude only the concrete SQLite lease files owned by this capture."""
    runtime_paths = [
        lock_path.with_name(name) for name in PREVIEW_LOCK_RUNTIME_NAMES
    ]
    excluded: dict[str, frozenset[str]] = {}
    for name, root in roots.items():
        relative_paths = []
        for runtime in runtime_paths:
            try:
                relative_paths.append(runtime.relative_to(root).as_posix())
            except ValueError:
                continue
        excluded[name] = frozenset(relative_paths)
    return excluded


def verify_preview_manifest(paths: DashboardPaths) -> None:
    """Fail closed unless every snapshot input still has its captured identity."""
    root = paths.preview_root
    manifest_path = paths.preview_manifest
    if root is None or manifest_path is None:
        return
    manifest_info = _require_regular_path(manifest_path, "preview manifest")
    if manifest_info.st_size > MAX_JSON_BYTES:
        raise DashboardError(503, "preview_manifest_invalid", "Preview manifest is too large")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DashboardError(
            503, "preview_manifest_invalid", "Preview manifest is invalid"
        ) from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema_version", "captured_at", "roots", "files"}
        or manifest.get("schema_version") != 1
        or not isinstance(manifest.get("roots"), dict)
        or set(manifest["roots"]) != set(PREVIEW_ROOT_NAMES)
        or not isinstance(manifest.get("files"), list)
    ):
        raise DashboardError(503, "preview_manifest_invalid", "Preview manifest is invalid")
    expected_paths = {
        name: root / name for name in PREVIEW_ROOT_NAMES
    }
    if any(
        manifest["roots"].get(name) != name
        or getattr(paths, name) != expected
        for name, expected in expected_paths.items()
    ):
        raise DashboardError(
            503, "preview_manifest_invalid", "Preview paths do not match the manifest"
        )
    expected: dict[str, dict[str, Any]] = {}
    for item in manifest["files"]:
        if (
            not isinstance(item, dict)
            or set(item)
            != {
                "path",
                "device",
                "inode",
                "size",
                "sha256",
                "source_device",
                "source_inode",
                "source_size",
                "source_sha256",
            }
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("device"), int)
            or not isinstance(item.get("inode"), int)
            or not isinstance(item.get("size"), int)
            or item["size"] < 0
            or not SHA256_RE.fullmatch(str(item.get("sha256", "")))
            or not isinstance(item.get("source_device"), int)
            or not isinstance(item.get("source_inode"), int)
            or not isinstance(item.get("source_size"), int)
            or item["source_size"] < 0
            or not SHA256_RE.fullmatch(str(item.get("source_sha256", "")))
            or item["path"] in expected
        ):
            raise DashboardError(
                503, "preview_manifest_invalid", "Preview manifest is invalid"
            )
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 2:
            raise DashboardError(
                503, "preview_manifest_invalid", "Preview manifest escapes its root"
            )
        if relative.parts[0] not in PREVIEW_ROOT_NAMES:
            raise DashboardError(
                503, "preview_manifest_invalid", "Preview manifest is invalid"
            )
        expected[item["path"]] = item
    actual: dict[str, dict[str, Any]] = {}
    for name, directory in expected_paths.items():
        for relative, identity in _snapshot_files(directory, f"preview {name}").items():
            actual[f"{name}/{relative}"] = identity
    if set(actual) != set(expected):
        raise DashboardError(
            503, "preview_manifest_changed", "Preview snapshot files changed"
        )
    for path, identity in actual.items():
        captured = expected[path]
        if any(identity[key] != captured[key] for key in ("device", "inode", "size", "sha256")):
            raise DashboardError(
                503, "preview_manifest_changed", "Preview snapshot files changed", [path]
            )


def _copy_snapshot_file(source: Path, destination: Path, deadline: float) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_handle, destination.open("xb") as output_handle:
        while True:
            if time.monotonic() >= deadline:
                raise DashboardError(
                    2, "preview_capture_timeout", "Preview capture exceeded thirty seconds"
                )
            chunk = input_handle.read(1024 * 1024)
            if not chunk:
                break
            output_handle.write(chunk)
    destination.chmod(0o600)


def _snapshot_run_active(state: Path, orchestrator: Path) -> bool:
    for path in (
        state / "dreaming-run-active.json",
        state / "run-active.json",
        orchestrator / "active-run.json",
    ):
        if not path.exists():
            continue
        _require_regular_path(path, "run activity marker")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DashboardError(
                503, "preview_capture_invalid", "Run activity marker is invalid"
            ) from exc
        if isinstance(value, dict) and (
            value.get("active") is True or value.get("status") in {"running", "active"}
        ):
            return True
    return False


def _release_preview_lock(
    lock_script: Path,
    token: str,
    environment: dict[str, str],
    deadline: float,
) -> None:
    error: Exception | None = None
    for _ in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            result = subprocess.run(
                [
                    os.sys.executable,
                    str(lock_script),
                    "release",
                    token,
                    "--idempotent",
                ],
                capture_output=True,
                text=True,
                env=environment,
                timeout=min(1.0, remaining),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = exc
            continue
        if result.returncode == 0:
            return
        error = RuntimeError(f"release returned {result.returncode}")
    raise DashboardError(
        2, "preview_capture_release_failed", "Preview capture could not release its writer lock"
    ) from error


def _reconcile_preview_acquire(
    lock_script: Path,
    token: str,
    environment: dict[str, str],
    deadline: float,
) -> None:
    _release_preview_lock(lock_script, token, environment, deadline)


def capture_preview_snapshot(
    *,
    destination: Path,
    roots: dict[str, Path],
    lock_state: Path,
    next_eligible_at: float | None = None,
) -> None:
    """Capture a disposable preview snapshot while holding the existing writer lease."""
    deadline = time.monotonic() + PREVIEW_CAPTURE_SECONDS
    if set(roots) != set(PREVIEW_ROOT_NAMES):
        raise DashboardError(2, "preview_capture_invalid", "All preview roots are required")
    if next_eligible_at is not None and next_eligible_at - time.time() < PREVIEW_ELIGIBILITY_MARGIN_SECONDS:
        raise DashboardError(
            2, "preview_capture_too_close", "Next interval eligibility is less than ten minutes away"
        )
    source_roots = {name: path.absolute() for name, path in roots.items()}
    lock_path = lock_state.absolute() / "daemon.lock"
    lock_exclusions = _capture_lock_exclusions(source_roots, lock_path)
    destination = _validated_capture_destination(destination, source_roots)
    if _snapshot_run_active(source_roots["state"], source_roots["orchestrator_state"]):
        raise DashboardError(2, "preview_capture_active", "A Dreaming run is active")
    lock_script = Path(__file__).with_name("daemon-lock.py")
    environment = {
        **os.environ,
        "SKILLS_STATE_DIR": str(lock_state.absolute()),
        "SKILLS_LOCK_DIR": str(lock_path),
        "SKILLS_LOCK_NONBLOCKING": "1",
    }
    token = str(uuid.uuid4())
    work_deadline = deadline - PREVIEW_RELEASE_RESERVE_SECONDS
    acquire_timeout = work_deadline - time.monotonic()
    if acquire_timeout <= 0:
        raise DashboardError(
            2, "preview_capture_timeout", "Preview capture exceeded thirty seconds"
        )
    try:
        lock = subprocess.run(
            [
                os.sys.executable,
                str(lock_script),
                "acquire",
                "--mode",
                "session",
                "--owner",
                "dashboard-preview-capture",
                "--token",
                token,
            ],
            capture_output=True,
            text=True,
            env=environment,
            timeout=min(1.0, acquire_timeout),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _reconcile_preview_acquire(lock_script, token, environment, deadline)
        raise DashboardError(
            2,
            "preview_capture_acquire_ambiguous",
            "Preview capture could not confirm writer-lock acquisition",
        ) from exc
    if lock.returncode != 0:
        _reconcile_preview_acquire(lock_script, token, environment, deadline)
        raise DashboardError(2, "preview_capture_locked", "Writer lock is unavailable")
    if lock.stdout.strip() != token:
        _reconcile_preview_acquire(lock_script, token, environment, deadline)
        raise DashboardError(
            2,
            "preview_capture_acquire_ambiguous",
            "Preview capture could not confirm writer-lock acquisition",
        )
    created = False
    failure: Exception | None = None
    try:
        if _snapshot_run_active(source_roots["state"], source_roots["orchestrator_state"]):
            raise DashboardError(2, "preview_capture_active", "A Dreaming run is active")
        before = {
            name: _preview_capture_files(name, path, lock_exclusions[name])
            for name, path in source_roots.items()
        }
        if time.monotonic() >= work_deadline:
            raise DashboardError(2, "preview_capture_timeout", "Preview capture exceeded thirty seconds")
        destination.mkdir(mode=0o700, parents=False)
        created = True
        for name, files in before.items():
            (destination / name).mkdir(mode=0o700)
            for relative in files:
                _copy_snapshot_file(
                    source_roots[name] / relative,
                    destination / name / relative,
                    work_deadline,
                )
                if time.monotonic() >= work_deadline:
                    raise DashboardError(2, "preview_capture_timeout", "Preview capture exceeded thirty seconds")
        after = {
            name: _preview_capture_files(name, path, lock_exclusions[name])
            for name, path in source_roots.items()
        }
        if before != after:
            raise DashboardError(2, "preview_capture_changed", "Source inputs changed during capture")
        files = []
        for name in PREVIEW_ROOT_NAMES:
            for relative, identity in _snapshot_files(destination / name, f"preview {name}").items():
                source_identity = before[name][relative]
                files.append(
                    {
                        "path": f"{name}/{relative}",
                        **identity,
                        "source_device": source_identity["device"],
                        "source_inode": source_identity["inode"],
                        "source_size": source_identity["size"],
                        "source_sha256": source_identity["sha256"],
                    }
                )
        manifest = {
            "schema_version": 1,
            "captured_at": now_iso(),
            "roots": {name: name for name in PREVIEW_ROOT_NAMES},
            "files": sorted(files, key=lambda item: item["path"]),
        }
        (destination / PREVIEW_MANIFEST_NAME).write_bytes(canonical(manifest))
        preview_paths = DashboardPaths.preview(
            destination, Path(__file__).parents[1] / "assets/dashboard"
        )
        verify_preview_manifest(preview_paths)
        if time.monotonic() >= work_deadline:
            raise DashboardError(2, "preview_capture_timeout", "Preview capture exceeded thirty seconds")
    except Exception as exc:
        failure = exc
        if created and destination.exists():
            shutil.rmtree(destination)
    try:
        _release_preview_lock(lock_script, token, environment, deadline)
    except DashboardError as release_error:
        if created and destination.exists():
            shutil.rmtree(destination)
        if failure is not None:
            raise release_error from failure
        raise
    if failure is not None:
        raise failure


def read_token(path: Path) -> str:
    if path.is_symlink():
        raise DashboardError(503, "token_invalid", "Dashboard token is symlinked")
    try:
        info = path.stat()
    except OSError as exc:
        raise DashboardError(503, "token_missing", "Dashboard token is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise DashboardError(503, "token_permissions", "Dashboard token permissions are invalid")
    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise DashboardError(503, "token_invalid", "Dashboard token is unreadable") from exc
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise DashboardError(503, "token_invalid", "Dashboard token is malformed")
    return value


class DashboardData:
    def __init__(self, paths: DashboardPaths):
        self.paths = paths
        self._skills_git_root: Path | None | bool = False
        self._candidate_history_retry_at = 0.0
        self._candidate_history_head: str | None = None
        self._candidate_history_cache: dict[str, list[tuple[float, str]]] = {}
        self._evaluation_identity_cache: dict[
            tuple[str, str], tuple[str, str] | None
        ] = {}

    def _adapter_config_path(self) -> Path:
        return (
            self.paths.state / "adapters.json"
            if self.paths.preview_root is not None
            else Path(
                os.environ.get(
                    "DREAMING_ADAPTER_CONFIG",
                    self.paths.state / "adapters.json",
                )
            )
        )

    def _adapter_config(self) -> dict[str, Any]:
        path = self._adapter_config_path()
        value = self._json(path, {}, "adapter config") if path.exists() else {}
        if not isinstance(value, dict):
            raise DashboardError(
                503,
                "adapter_config_invalid",
                "Adapter configuration is malformed",
                ["adapter config"],
            )
        return value

    def _json(self, path: Path, default: Any, source: str) -> Any:
        if not path.exists():
            return default
        if path.is_symlink() or not path.is_file():
            raise DashboardError(503, "state_invalid", f"{source} is not a regular file", [source])
        try:
            if path.stat().st_size > MAX_JSON_BYTES:
                raise DashboardError(503, "state_oversized", f"{source} is too large", [source])
            return json.loads(path.read_text(encoding="utf-8"))
        except DashboardError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DashboardError(503, "state_invalid", f"{source} is invalid", [source]) from exc

    def _list(self, name: str, default: list[Any] | None = None) -> list[Any]:
        value = self._json(self.paths.state / name, default or [], name)
        if not isinstance(value, list):
            raise DashboardError(503, "state_invalid", f"{name} must be a list", [name])
        return value

    def _dict(self, name: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
        value = self._json(self.paths.state / name, default or {}, name)
        if not isinstance(value, dict):
            raise DashboardError(503, "state_invalid", f"{name} must be an object", [name])
        return value

    def _fingerprint(self, paths: list[Path]) -> str:
        records = []
        for path in sorted(paths):
            if not path.exists():
                records.append([str(path), None])
                continue
            if path.is_symlink():
                raise DashboardError(503, "state_invalid", "Fingerprint source is symlinked")
            if path.is_file():
                content = path.read_bytes()
                records.append([str(path), hashlib.sha256(content).hexdigest()])
            elif path.is_dir():
                children = []
                for child in sorted(path.rglob("*")):
                    if child.is_symlink():
                        continue
                    relative = child.relative_to(path).as_posix()
                    children.append(
                        [
                            relative,
                            "file" if child.is_file() else "directory",
                            child.stat().st_size,
                            child.stat().st_mtime_ns,
                        ]
                    )
                records.append([str(path), children])
        return sha(records)

    def _cursor(
        self,
        items: list[dict[str, Any]],
        query: dict[str, Any],
        fingerprint: str,
        raw_cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        start = 0
        if raw_cursor:
            try:
                padding = "=" * (-len(raw_cursor) % 4)
                cursor = json.loads(
                    base64.urlsafe_b64decode(raw_cursor + padding).decode("utf-8")
                )
            except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
                raise DashboardError(400, "invalid_cursor", "Pagination cursor is invalid") from exc
            if cursor.get("query") != query:
                raise DashboardError(400, "invalid_cursor", "Pagination query changed")
            if cursor.get("fingerprint") != fingerprint:
                raise DashboardError(409, "stale_snapshot", "List state changed")
            start = cursor.get("offset")
            if not isinstance(start, int) or start < 0:
                raise DashboardError(400, "invalid_cursor", "Pagination offset is invalid")
        page = items[start : start + limit]
        next_cursor = None
        if start + limit < len(items):
            value = {
                "query": query,
                "fingerprint": fingerprint,
                "offset": start + limit,
            }
            next_cursor = base64.urlsafe_b64encode(canonical(value)).decode().rstrip("=")
        return {
            "items": page,
            "total": len(items),
            "next_cursor": next_cursor,
            "fingerprint": fingerprint,
        }

    def dream_rows(self) -> tuple[list[dict[str, Any]], str]:
        queue_path = self.paths.state / "queue.json"
        unsettled_path = self.paths.state / "unsettled.json"
        ledger_path = self.paths.state / "review-ledger.json"
        queue = self._list("queue.json")
        unsettled = self._dict("unsettled.json")
        ledger = self._list("review-ledger.json")
        reviewed = {
            (item.get("session_id"), item.get("source_revision")): item
            for item in ledger
            if isinstance(item, dict)
        }
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in queue:
            if isinstance(item, dict) and isinstance(item.get("qualified_session_id"), str):
                grouped.setdefault(item["qualified_session_id"], []).append(item)
        for session_id, item in unsettled.items():
            if isinstance(item, dict):
                grouped.setdefault(session_id, []).append({**item, "status": "active"})
        rows = []
        for session_id, revisions in grouped.items():
            revisions.sort(
                key=lambda item: (
                    parse_time(item.get("updated_at")) or 0,
                    str(item.get("source_revision", "")),
                )
            )
            current = revisions[-1]
            accepted = reviewed.get((session_id, current.get("source_revision")))
            raw_status = "reviewed" if accepted else current.get("status", "unknown")
            status = (
                "completed"
                if accepted
                else "active"
                if raw_status == "active"
                else "remaining"
                if raw_status in {"queued", "recovery-required", "migration-hold"}
                else raw_status
            )
            features = current.get("features") if isinstance(current.get("features"), dict) else {}
            name = safe_text(current.get("display_name"), 160) or safe_text(
                accepted.get("display_name") if accepted else None, 160
            )
            rows.append(
                {
                    "id": session_id,
                    "name": name or fallback_name(session_id),
                    "source": current.get("source", session_id.split(":", 1)[0]),
                    "status": status,
                    "raw_status": raw_status,
                    "updated_at": current.get("updated_at"),
                    "queued_at": current.get("queued_at"),
                    "completed_at": accepted.get("reviewed_at") if accepted else None,
                    "activity": {
                        "user_turns": features.get("user_turn_count"),
                        "assistant_turns": features.get("assistant_turn_count"),
                        "tool_calls": features.get("tool_call_count"),
                    },
                    "learning_result": (
                        accepted.get("terminal_route") if accepted else None
                    ),
                    "summary": safe_text(
                        accepted.get("summary") if accepted else None, 500
                    ),
                    "source_revision": current.get("source_revision"),
                }
            )
        rows.sort(key=lambda item: parse_time(item["updated_at"]) or 0, reverse=True)
        return rows, self._fingerprint([queue_path, unsettled_path, ledger_path])

    def dreams(self, params: dict[str, list[str]]) -> dict[str, Any]:
        rows, fingerprint = self.dream_rows()
        status = first(params, "status")
        source = first(params, "source")
        result = first(params, "result")
        query_text = first(params, "query").casefold()
        if status:
            rows = [item for item in rows if item["status"] == status or item["raw_status"] == status]
        if source:
            rows = [item for item in rows if item["source"] == source]
        if result:
            rows = [item for item in rows if item["learning_result"] == result]
        if query_text:
            rows = [
                item
                for item in rows
                if query_text in item["name"].casefold()
                or query_text in item["summary"].casefold()
            ]
        sort = first(params, "sort") or "updated"
        if sort == "name":
            rows.sort(key=lambda item: item["name"].casefold())
        elif sort == "oldest":
            rows.sort(key=lambda item: parse_time(item["updated_at"]) or 0)
        query = {
            "status": status,
            "source": source,
            "result": result,
            "query": query_text,
            "sort": sort,
        }
        return self._cursor(
            rows,
            query,
            fingerprint,
            first(params, "cursor") or None,
            parse_limit(params),
        )

    def dream_detail(self, session_id: str) -> dict[str, Any]:
        rows, _ = self.dream_rows()
        row = next((item for item in rows if item["id"] == session_id), None)
        if row is None:
            raise DashboardError(404, "dream_not_found", "Dream was not found")
        queue = [
            item
            for item in self._list("queue.json")
            if isinstance(item, dict) and item.get("qualified_session_id") == session_id
        ]
        ledger = [
            item
            for item in self._list("review-ledger.json")
            if isinstance(item, dict) and item.get("session_id") == session_id
        ]
        return {**row, "revisions": queue, "reviews": ledger}

    def _skill_candidate(self, skill: Path) -> str:
        files = []
        for path in sorted(skill.rglob("*")):
            if path.is_symlink():
                raise DashboardError(503, "skill_invalid", f"{skill.name} contains a symlink")
            relative = path.relative_to(skill).as_posix()
            if not path.is_file() or relative in EVALUATION_SIDECARS:
                continue
            if path.name in EVALUATION_SIDECARS:
                raise DashboardError(
                    503,
                    "skill_invalid",
                    f"{skill.name} has a reserved evaluation sidecar below its root",
                )
            content = path.read_bytes()
            files.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                }
            )
        return "sha256:" + hashlib.sha256(canonical(files)).hexdigest()

    def skill_rows(self) -> tuple[list[dict[str, Any]], str]:
        if not self.paths.skills.is_dir():
            return [], self._fingerprint([self.paths.skills])
        rows = []
        evaluation_root = self.paths.control_state / "skill-review/evaluations"
        publication_path = self.paths.state / "publisher-ownership.json"
        remote_publication_path = self.paths.state / "remote-publication-summary.json"
        publication = self._publication_targets(
            publication_path, remote_publication_path
        )
        for skill in sorted(self.paths.skills.iterdir()):
            if (
                skill.is_symlink()
                or not skill.is_dir()
                or not SKILL_RE.fullmatch(skill.name)
                or not (skill / ".agent-created").is_file()
            ):
                continue
            envelope_path = skill / ".agent-created.json"
            try:
                envelope = self._json(
                    envelope_path, {}, f"{skill.name}/.agent-created.json"
                )
                if not isinstance(envelope, dict):
                    raise DashboardError(503, "skill_invalid", f"{skill.name} envelope is invalid")
                body = (skill / "SKILL.md").read_text(encoding="utf-8")
                candidate = self._skill_candidate(skill)
                evidence = envelope.get("evidence")
                if not isinstance(evidence, list):
                    raise DashboardError(503, "skill_invalid", f"{skill.name} evidence is invalid")
                evaluation = envelope.get("evaluation")
                envelope_status = (
                    evaluation.get("status")
                    if isinstance(evaluation, dict)
                    else "not_evaluated"
                )
                transition = self._current_transition(skill, candidate)
                evaluation_status = (
                    transition["status"]
                    if transition is not None
                    else envelope_status
                    if envelope_status in {"not_evaluated", "pending", "waived"}
                    else "unavailable"
                )
                rows.append(
                    {
                        "name": skill.name,
                        "status": "current",
                        "created_at": envelope.get("created_at"),
                        "bytes": len(body.encode("utf-8")),
                        "lines": len(body.splitlines()),
                        "words": len(body.split()),
                        "candidate_id": candidate,
                        "evidence_count": len(evidence),
                        "verified_task_count": len(
                            {
                                item.get("task_key")
                                for item in evidence
                                if isinstance(item, dict)
                                and item.get("independence") == "verified"
                            }
                        ),
                        "evaluation_status": evaluation_status,
                        "evaluation_v3_sha256": envelope.get("evaluation_v3_sha256"),
                        "evaluation_transition": transition,
                        "usage": {
                            "known": False,
                            "count": None,
                            "last_used_at": None,
                        },
                        "publication_targets": publication.get(skill.name, []),
                    }
                )
            except (OSError, UnicodeError, DashboardError) as exc:
                rows.append(
                    {
                        "name": skill.name,
                        "status": "unhealthy",
                        "error": str(exc),
                        "usage": {"known": False, "count": None, "last_used_at": None},
                    }
                )
        return rows, self._fingerprint(
            [
                self.paths.skills,
                evaluation_root,
                publication_path,
                remote_publication_path,
            ]
        )

    def _publication_targets(
        self, path: Path, remote_path: Path
    ) -> dict[str, list[str]]:
        journal = self._json(path, {}, "publisher ownership") if path.exists() else {}
        if not isinstance(journal, dict):
            raise DashboardError(
                503,
                "publication_invalid",
                "Publisher ownership journal is malformed",
                ["publisher ownership"],
            )
        targets: dict[str, list[str]] = {}
        for vendor, descriptor in journal.items():
            if not isinstance(vendor, str) or not isinstance(descriptor, dict):
                continue
            skills = descriptor.get("skills")
            if not isinstance(skills, list):
                continue
            for name in skills:
                if isinstance(name, str) and SKILL_RE.fullmatch(name):
                    targets.setdefault(name, []).append(vendor)
        if remote_path.exists():
            summary = self._json(
                remote_path, {}, "remote publication summary"
            )
            config = self._adapter_config()
            publisher = (
                config.get("publishers", {}).get("copilot")
                if isinstance(config, dict)
                and isinstance(config.get("publishers"), dict)
                else None
            )
            argv = publisher.get("argv") if isinstance(publisher, dict) else None

            def argument(name: str) -> str | None:
                if not isinstance(argv, list):
                    return None
                try:
                    index = argv.index(name)
                except ValueError:
                    return None
                if index + 1 >= len(argv) or not isinstance(argv[index + 1], str):
                    return None
                return argv[index + 1]

            expected_identity = (
                argument("--expected-receiver-id"),
                argument("--expected-receiver-sha"),
                argument("--expected-adapter-sha"),
            )
            descriptor = (
                summary.get("descriptor")
                if isinstance(summary, dict)
                and summary.get("schema_version") == 1
                and summary.get("status") == "committed"
                and expected_identity[0] is not None
                and summary.get("receiver_id") == expected_identity[0]
                and summary.get("receiver_sha256") == expected_identity[1]
                and summary.get("adapter_sha256") == expected_identity[2]
                else None
            )
            if isinstance(descriptor, dict):
                skills = descriptor.get("skills")
                if isinstance(skills, list):
                    for name in skills:
                        if isinstance(name, str) and SKILL_RE.fullmatch(name):
                            targets.setdefault(name, []).append("copilot@MacBook")
        return {
            name: sorted(set(vendors))
            for name, vendors in targets.items()
        }

    def _skill_key(self, skill: Path) -> str:
        return hashlib.sha256(str(skill.resolve()).encode()).hexdigest()

    def _current_evaluation_input(
        self, skill: Path
    ) -> dict[str, Any] | None:
        python = shutil.which("python3")
        evaluator = self.paths.repo / "skills/skill-review/scripts/skill-evaluation.py"
        if python is None or not evaluator.is_file() or evaluator.is_symlink():
            return None
        try:
            result = subprocess.run(
                [python, str(evaluator), "v2-prepare", str(skill)],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            payload = json.loads(result.stdout)
            subject = payload.get("subject")
            identities = [
                payload.get("candidate_id"),
                payload.get("input_manifest_sha256"),
                payload.get("suite_id"),
                payload.get("policy_id"),
            ]
            if (
                not isinstance(subject, dict)
                or any(
                    not isinstance(value, str)
                    or not value.startswith("sha256:")
                    or not SHA256_RE.fullmatch(value.removeprefix("sha256:"))
                    for value in identities
                )
            ):
                return None
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            subprocess.SubprocessError,
        ):
            return None
        return payload

    def _record_hash_matches(self, path: Path, expected: Any) -> bool:
        if (
            not isinstance(expected, str)
            or not SHA256_RE.fullmatch(expected)
            or path.is_symlink()
            or not path.is_file()
        ):
            return False
        try:
            value = self._json(path, {}, f"evaluation record:{path.name}")
        except DashboardError:
            return False
        return hashlib.sha256(canonical(value)).hexdigest() == expected

    def _current_transition(
        self, skill: Path, candidate: str
    ) -> dict[str, Any] | None:
        root = (
            self.paths.control_state
            / "skill-review/evaluations/v2/dashboard-v1/authority-transitions"
            / self._skill_key(skill)
        )
        current = None
        if not root.is_dir():
            return None
        current_input = self._current_evaluation_input(skill)
        for path in root.glob("*.json"):
            if path.is_symlink():
                continue
            transition = self._json(path, {}, f"transition:{path.name}")
            schema_version = (
                transition.get("schema_version")
                if isinstance(transition, dict)
                else None
            )
            if (
                not isinstance(transition, dict)
                or schema_version not in {1, 2}
                or transition.get("kind") != "dashboard_authority_transition"
                or transition.get("skill_key") != self._skill_key(skill)
                or transition.get("candidate_id") != candidate
                or (
                    schema_version == 2
                    and (
                        current_input is None
                        or transition.get("subject")
                        != current_input.get("subject")
                    )
                )
                or transition.get("status")
                not in {"pass", "regression", "inconclusive", "revoked"}
            ):
                continue
            if current is None or (parse_time(transition.get("effective_at")) or 0) > (
                parse_time(current.get("effective_at")) or 0
            ):
                current = transition
        if current is None or not self._transition_matches_current(
            skill, current, current_input
        ):
            return None
        return current

    def _transition_matches_current(
        self,
        skill: Path,
        transition: dict[str, Any],
        current_input: dict[str, Any] | None = None,
    ) -> bool:
        effective_at = parse_time(transition.get("effective_at"))
        if effective_at is None:
            return False
        if transition.get("schema_version") == 2:
            return self._subject_transition_matches_current(
                skill, transition, current_input
            )
        input_paths = [
            skill / name
            for name in (
                ".skill-evaluation-cases.json",
                ".skill-evaluation-policy.json",
            )
        ]
        input_digests = []
        for path in input_paths:
            try:
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or path.stat().st_mtime > effective_at
                ):
                    return False
                content = path.read_bytes()
                input_digests.append(hashlib.sha256(content).hexdigest())
            except OSError:
                return False
        if transition.get("status") != "pass":
            return True

        candidate = transition.get("candidate_id")
        authority_sha = transition.get("authority_sha256")
        aggregate_sha = transition.get("aggregate_receipt_sha256")
        if (
            not isinstance(candidate, str)
            or not SHA256_RE.fullmatch(candidate.removeprefix("sha256:"))
            or not isinstance(authority_sha, str)
            or not SHA256_RE.fullmatch(authority_sha)
            or not isinstance(aggregate_sha, str)
            or not SHA256_RE.fullmatch(aggregate_sha)
        ):
            return False
        key = self._skill_key(skill)
        evaluation_root = self.paths.control_state / "skill-review/evaluations/v2"
        authority_path = evaluation_root / "authority" / key / f"{candidate}.json"
        latest_path = evaluation_root / "latest" / f"{key}.json"
        try:
            authority = self._json(authority_path, {}, "evaluation authority")
            latest = self._json(latest_path, {}, "latest evaluation authority")
            envelope = self._json(
                skill / ".agent-created.json", {}, f"{skill.name} envelope"
            )
        except DashboardError:
            return False
        if (
            hashlib.sha256(canonical(authority)).hexdigest() != authority_sha
            or authority.get("kind") != "cross_cli_authority"
            or authority.get("skill_path") != str(skill.resolve())
            or authority.get("candidate_id") != candidate
            or authority.get("aggregate_receipt_sha256") != aggregate_sha
            or latest
            != {
                "schema_version": 2,
                "skill_path": str(skill.resolve()),
                "candidate_id": candidate,
                "authority_path": str(authority_path.resolve()),
                "authority_sha256": authority_sha,
            }
            or envelope.get("evaluation_v3_sha256") != authority_sha
        ):
            return False
        identities = self._evaluation_input_identities(
            skill,
            (input_digests[0], input_digests[1]),
        )
        if (
            identities is None
            or identities[0] != authority.get("suite_id")
            or identities[1] != authority.get("policy_id")
        ):
            return False
        return True

    def _subject_transition_matches_current(
        self,
        skill: Path,
        transition: dict[str, Any],
        current_input: dict[str, Any] | None = None,
    ) -> bool:
        key = self._skill_key(skill)
        if current_input is None:
            current_input = self._current_evaluation_input(skill)
        if current_input is None:
            return False
        subject = current_input["subject"]
        transition_id = transition.get("transition_id")
        candidate = transition.get("candidate_id")
        input_manifest_sha256 = transition.get("input_manifest_sha256")
        expected_transition_id = "sha256:" + hashlib.sha256(
            canonical(
                {
                    name: value
                    for name, value in transition.items()
                    if name != "transition_id"
                }
            )
        ).hexdigest()
        if (
            transition.get("subject") != subject
            or candidate != current_input.get("candidate_id")
            or input_manifest_sha256
            != current_input.get("input_manifest_sha256")
            or not isinstance(transition_id, str)
            or transition_id != expected_transition_id
            or not isinstance(candidate, str)
            or not SHA256_RE.fullmatch(candidate.removeprefix("sha256:"))
            or not isinstance(input_manifest_sha256, str)
            or not SHA256_RE.fullmatch(
                input_manifest_sha256.removeprefix("sha256:")
            )
        ):
            return False
        authority_sha = transition.get("authority_sha256")
        aggregate_sha = transition.get("aggregate_receipt_sha256")
        portfolio_sha = transition.get("portfolio_receipt_sha256")
        evaluation_root = self.paths.control_state / "skill-review/evaluations/v2"
        aggregate_path = evaluation_root / "receipts" / f"{aggregate_sha}.json"
        portfolio_path = (
            evaluation_root
            / "dashboard-v1/portfolio"
            / f"{portfolio_sha}.json"
        )
        status = transition.get("status")
        if status == "revoked":
            return all(
                value is None
                for value in (authority_sha, aggregate_sha, portfolio_sha)
            )
        if status in {"regression", "inconclusive"}:
            return (
                authority_sha is None
                and self._record_hash_matches(aggregate_path, aggregate_sha)
                and self._record_hash_matches(portfolio_path, portfolio_sha)
            )
        if status != "pass":
            return False
        if not all(
            isinstance(value, str) and SHA256_RE.fullmatch(value)
            for value in (authority_sha, aggregate_sha, portfolio_sha)
        ):
            return False
        if (
            not self._record_hash_matches(aggregate_path, aggregate_sha)
            or not self._record_hash_matches(portfolio_path, portfolio_sha)
        ):
            return False
        authority_path = (
            evaluation_root / "authority" / key / f"{candidate}.json"
        )
        latest_path = evaluation_root / "latest" / f"{key}.json"
        try:
            authority = self._json(
                authority_path, {}, "subject-bound evaluation authority"
            )
            latest = self._json(
                latest_path, {}, "latest subject-bound evaluation authority"
            )
        except DashboardError:
            return False
        authority_keys = {
            "schema_version", "kind", "skill_path", "subject",
            "candidate_id", "input_manifest_sha256", "suite_id", "policy_id",
            "observation_plan_id", "required_certificate_set_id",
            "required_executors", "advisory_executors",
            "aggregate_receipt_sha256", "aggregate_id", "authority_id",
        }
        authority_projection = {
            name: value
            for name, value in authority.items()
            if name != "authority_id"
        }
        expected_authority_id = "sha256:" + hashlib.sha256(
            canonical(authority_projection)
        ).hexdigest()
        if (
            set(authority) != authority_keys
            or hashlib.sha256(canonical(authority)).hexdigest() != authority_sha
            or authority.get("schema_version") != 4
            or authority.get("kind") != "cross_cli_authority"
            or authority.get("skill_path") != str(skill.resolve())
            or authority.get("subject") != subject
            or authority.get("candidate_id") != candidate
            or authority.get("input_manifest_sha256")
            != input_manifest_sha256
            or authority.get("suite_id") != current_input.get("suite_id")
            or authority.get("policy_id") != current_input.get("policy_id")
            or authority.get("aggregate_receipt_sha256") != aggregate_sha
            or authority.get("authority_id") != expected_authority_id
            or latest
            != {
                "schema_version": 3,
                "skill_path": str(skill.resolve()),
                "subject": subject,
                "candidate_id": candidate,
                "input_manifest_sha256": input_manifest_sha256,
                "authority_path": str(authority_path.resolve()),
                "authority_sha256": authority_sha,
            }
        ):
            return False
        return True

    def _evaluation_input_identities(
        self,
        skill: Path,
        digests: tuple[str, str],
    ) -> tuple[str, str] | None:
        if digests in self._evaluation_identity_cache:
            return self._evaluation_identity_cache[digests]
        python = shutil.which("python3")
        evaluator = self.paths.repo / "skills/skill-review/scripts/skill-evaluation.py"
        if python is None or not evaluator.is_file() or evaluator.is_symlink():
            return None
        try:
            result = subprocess.run(
                [python, str(evaluator), "v2-prepare", str(skill)],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            payload = json.loads(result.stdout)
            values = [payload.get("suite_id"), payload.get("policy_id")]
            if any(
                not isinstance(value, str)
                or not value.startswith("sha256:")
                or not SHA256_RE.fullmatch(value.removeprefix("sha256:"))
                for value in values
            ):
                raise ValueError("evaluation identity is malformed")
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            subprocess.SubprocessError,
        ):
            return None
        identities = (values[0], values[1])
        self._evaluation_identity_cache[digests] = identities
        return identities

    def skills(self, params: dict[str, list[str]]) -> dict[str, Any]:
        rows, fingerprint = self.skill_rows()
        status = first(params, "status")
        evaluation = first(params, "evaluation")
        query_text = first(params, "query").casefold()
        if status:
            rows = [item for item in rows if item.get("status") == status]
        if evaluation:
            rows = [item for item in rows if item.get("evaluation_status") == evaluation]
        if query_text:
            rows = [item for item in rows if query_text in item["name"].casefold()]
        sort = first(params, "sort") or "name"
        if sort == "created":
            rows.sort(key=lambda item: parse_time(item.get("created_at")) or 0, reverse=True)
        elif sort == "evidence":
            rows.sort(key=lambda item: item.get("evidence_count", -1), reverse=True)
        else:
            rows.sort(key=lambda item: item["name"])
        query = {
            "status": status,
            "evaluation": evaluation,
            "query": query_text,
            "sort": sort,
        }
        return self._cursor(
            rows,
            query,
            fingerprint,
            first(params, "cursor") or None,
            parse_limit(params),
        )

    def _skill_path(self, name: str) -> Path:
        if not SKILL_RE.fullmatch(name):
            raise DashboardError(404, "skill_not_found", "Skill was not found")
        path = self.paths.skills / name
        if path.is_symlink() or not path.is_dir() or path.parent.resolve() != self.paths.skills:
            raise DashboardError(404, "skill_not_found", "Skill was not found")
        return path

    def skill_detail(self, name: str) -> dict[str, Any]:
        path = self._skill_path(name)
        rows, _ = self.skill_rows()
        row = next((item for item in rows if item["name"] == name), None)
        if row is None:
            raise DashboardError(404, "skill_not_found", "Skill was not found")
        envelope = self._json(path / ".agent-created.json", {}, f"{name} envelope")
        text = (path / "SKILL.md").read_text(encoding="utf-8")
        dream_names = self._dream_names()
        previews = []
        for index, item in enumerate(envelope.get("evidence", [])):
            if not isinstance(item, dict):
                continue
            previews.append(
                {
                    "id": f"evidence-{index + 1}",
                    "summary": safe_text(item.get("summary"), 500),
                    "session_id": item.get("session_id"),
                    "dream_name": self._dream_name(
                        item.get("session_id"), dream_names
                    ),
                    "source": item.get("source"),
                    "observed_at": item.get("observed_at"),
                    "evidence_kind": item.get("evidence_kind"),
                    "independence": item.get("independence"),
                    "anchored": isinstance(item.get("transcript_context"), dict),
                }
            )
        return {**row, "text": text, "evidence": previews, "envelope": envelope}

    def _snapshot(self, digest: str) -> dict[str, Any]:
        if not SHA256_RE.fullmatch(digest):
            raise DashboardError(404, "snapshot_not_found", "Snapshot was not found")
        root = self.paths.data / "snapshots"
        path = root / f"{digest}.json"
        if path.is_symlink() or not path.is_file() or path.parent.resolve() != root.resolve():
            raise DashboardError(404, "snapshot_not_found", "Snapshot was not found")
        if path.stat().st_size > MAX_SNAPSHOT_BYTES:
            raise DashboardError(422, "snapshot_invalid", "Snapshot exceeds its retained limit")
        try:
            snapshot = self._json(path, {}, "snapshot")
        except DashboardError as error:
            raise DashboardError(
                422,
                "snapshot_invalid",
                "Snapshot is not valid retained JSON",
            ) from error
        if hashlib.sha256(canonical(snapshot)).hexdigest() != digest:
            raise DashboardError(422, "snapshot_invalid", "Snapshot digest does not match")
        if not isinstance(snapshot.get("events"), list):
            raise DashboardError(422, "snapshot_invalid", "Snapshot events are invalid")
        event_ids = []
        for event in snapshot["events"]:
            if (
                not isinstance(event, dict)
                or not isinstance(event.get("source_event_id"), str)
                or not event["source_event_id"]
                or not isinstance(event.get("kind"), str)
                or not event["kind"]
                or (
                    event.get("text") is not None
                    and not isinstance(event.get("text"), str)
                )
            ):
                raise DashboardError(
                    422,
                    "snapshot_invalid",
                    "Snapshot event is invalid",
                )
            event_ids.append(event["source_event_id"])
        if len(event_ids) != len(set(event_ids)):
            raise DashboardError(
                422,
                "snapshot_invalid",
                "Snapshot event identities are not unique",
            )
        return snapshot

    def evidence(self, name: str, params: dict[str, list[str]]) -> dict[str, Any]:
        path = self._skill_path(name)
        envelope = self._json(path / ".agent-created.json", {}, f"{name} envelope")
        dream_names = self._dream_names()
        cards = []
        for index, item in enumerate(envelope.get("evidence", [])):
            if not isinstance(item, dict):
                continue
            context = item.get("transcript_context")
            events = []
            anchor_status = "historical-unanchored"
            snapshot_sha = None
            if isinstance(context, dict):
                snapshot_sha = context.get("snapshot_sha256")
                try:
                    event_ids = context.get("event_ids")
                    if (
                        context.get("schema_version") != 1
                        or not isinstance(snapshot_sha, str)
                        or not SHA256_RE.fullmatch(snapshot_sha)
                        or not isinstance(context.get("source_revision"), str)
                        or not context["source_revision"]
                        or not isinstance(event_ids, list)
                        or not 1 <= len(event_ids) <= 20
                        or any(
                            not isinstance(event_id, str) or not event_id
                            for event_id in event_ids
                        )
                        or len(event_ids) != len(set(event_ids))
                    ):
                        raise DashboardError(
                            422,
                            "anchor_invalid",
                            "Evidence anchor is malformed",
                        )
                    snapshot = self._snapshot(snapshot_sha)
                    if snapshot.get("source_revision") != context["source_revision"]:
                        raise DashboardError(
                            422,
                            "anchor_invalid",
                            "Evidence anchor revision does not match snapshot",
                        )
                    positions = {
                        event.get("source_event_id"): position
                        for position, event in enumerate(snapshot["events"])
                        if isinstance(event, dict)
                        and isinstance(event.get("source_event_id"), str)
                    }
                    if any(event_id not in positions for event_id in event_ids):
                        raise DashboardError(
                            422,
                            "anchor_invalid",
                            "Evidence anchor event is absent from snapshot",
                        )
                    indexes = [positions[event_id] for event_id in event_ids]
                    if indexes != sorted(indexes):
                        raise DashboardError(
                            422,
                            "anchor_invalid",
                            "Evidence anchor events are out of order",
                        )
                    selected = set()
                    for position in indexes:
                        selected.update(
                            range(
                                max(0, position - 2),
                                min(len(snapshot["events"]), position + 3),
                            )
                        )
                    events = [
                        self._public_snapshot_event(
                            snapshot["events"][position],
                            highlighted=position in indexes,
                        )
                        for position in sorted(selected)
                    ]
                    anchor_status = "exact"
                except DashboardError:
                    anchor_status = "invalid"
            cards.append(
                {
                    "id": f"evidence-{index + 1}",
                    "summary": safe_text(item.get("summary"), 4000),
                    "session_id": item.get("session_id"),
                    "dream_name": self._dream_name(
                        item.get("session_id"), dream_names
                    ),
                    "source": item.get("source"),
                    "observed_at": item.get("observed_at"),
                    "evidence_kind": item.get("evidence_kind"),
                    "independence": item.get("independence"),
                    "anchor_status": anchor_status,
                    "snapshot_sha256": snapshot_sha,
                    "events": events,
                }
            )
        fingerprint = self._fingerprint([path / ".agent-created.json", self.paths.data / "snapshots"])
        return self._cursor(
            cards,
            {"skill": name},
            fingerprint,
            first(params, "cursor") or None,
            parse_limit(params),
        )

    def _dream_names(self) -> dict[str, str]:
        rows, _ = self.dream_rows()
        return {item["id"]: item["name"] for item in rows}

    def _dream_name(
        self, session_id: Any, names: dict[str, str] | None = None
    ) -> str:
        if not isinstance(session_id, str):
            return "Untitled dream"
        catalog = names if names is not None else self._dream_names()
        return catalog.get(session_id, fallback_name(session_id))

    def transcript(self, digest: str) -> dict[str, Any]:
        snapshot = self._snapshot(digest)
        return {
            "schema_version": 1,
            "snapshot_sha256": digest,
            "source_revision": safe_text(snapshot.get("source_revision"), 500),
            "event_count": len(snapshot["events"]),
            "events": [
                self._public_snapshot_event(event)
                for event in snapshot["events"]
                if isinstance(event, dict)
            ],
        }

    @staticmethod
    def _public_snapshot_event(
        event: dict[str, Any],
        *,
        highlighted: bool | None = None,
    ) -> dict[str, Any]:
        public = {
            "source_event_id": safe_text(event.get("source_event_id"), 500),
            "kind": safe_text(event.get("kind"), 100),
        }
        if highlighted is not None:
            public["highlighted"] = highlighted
        return public

    def _candidate_records_root(self) -> Path:
        return self.paths.candidate_records or (
            self.paths.state / "skill-review/candidates/v1/records"
        )

    def _candidate_packages_root(self) -> Path:
        return self.paths.candidate_packages or (
            self.paths.data / "candidates/v1/packages"
        )

    def _candidate_package_files(self, root: Path) -> list[dict[str, Any]]:
        candidate_require(not root.is_symlink(), "package_symlinked")
        candidate_require(root.is_dir(), "package_unavailable")
        files: list[dict[str, Any]] = []
        total = 0
        try:
            for path in sorted(root.rglob("*")):
                candidate_require(not path.is_symlink(), "package_symlinked")
                if path.is_dir():
                    continue
                candidate_require(path.is_file(), "package_irregular_entry")
                candidate_require(
                    len(files) < MAX_CANDIDATE_PACKAGE_FILES, "package_oversized"
                )
                content = path.read_bytes()
                total += len(content)
                candidate_require(
                    total <= MAX_CANDIDATE_PACKAGE_BYTES, "package_oversized"
                )
                files.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "size": len(content),
                    }
                )
        except OSError as exc:
            raise CandidateInvalid("package_unreadable", str(exc)) from exc
        candidate_require(files, "package_empty")
        return files

    def _candidate_declared_files(self, value: Any) -> list[dict[str, Any]]:
        candidate_require(isinstance(value, list) and value, "revision_files_invalid")
        seen: set[str] = set()
        for item in value:
            item = candidate_keys(item, CANDIDATE_FILE_KEYS, "revision_files_invalid")
            relative = item["path"]
            candidate_require(
                isinstance(relative, str) and relative, "revision_files_invalid"
            )
            candidate_require(
                not Path(relative).is_absolute()
                and ".." not in Path(relative).parts
                and relative not in seen,
                "revision_files_invalid",
            )
            seen.add(relative)
            candidate_require(
                isinstance(item["sha256"], str) and SHA256_RE.fullmatch(item["sha256"]),
                "revision_files_invalid",
            )
            candidate_require(
                isinstance(item["size"], int)
                and not isinstance(item["size"], bool)
                and item["size"] >= 0,
                "revision_files_invalid",
            )
        candidate_require(
            value == sorted(value, key=lambda item: item["path"]),
            "revision_files_invalid",
        )
        return value

    def _candidate_record(self, path: Path) -> dict[str, Any]:
        candidate_require(not path.is_symlink(), "record_symlinked")
        candidate_require(path.is_file(), "record_not_regular_file")
        try:
            candidate_require(
                path.stat().st_size <= MAX_CANDIDATE_RECORD_BYTES, "record_oversized"
            )
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CandidateInvalid("record_unreadable", str(exc)) from exc
        record = candidate_keys(record, CANDIDATE_RECORD_KEYS, "record_schema_unknown")
        candidate_require(
            record["schema_version"] == 1
            and not isinstance(record["schema_version"], bool),
            "record_schema_version_unsupported",
        )
        lifecycle_id = record["lifecycle_id"]
        candidate_require(
            isinstance(lifecycle_id, str) and CANDIDATE_UUID_RE.fullmatch(lifecycle_id),
            "record_lifecycle_id_invalid",
        )
        candidate_require(lifecycle_id == path.stem, "record_identity_mismatch")
        candidate_require(
            record["state"] in CANDIDATE_STATE_LABELS, "record_state_not_shadow"
        )
        candidate_require(
            record["publication"] == {"status": "shadow_only"},
            "record_publication_not_shadow",
        )
        candidate_require(
            record["authority"] in {"autonomous", "user_authorized"},
            "record_authority_invalid",
        )
        candidate_require(
            isinstance(record["proposed_name"], str)
            and CANDIDATE_NAME_RE.fullmatch(record["proposed_name"]),
            "record_name_invalid",
        )
        candidate_require(
            isinstance(record["record_version"], int)
            and not isinstance(record["record_version"], bool)
            and record["record_version"] >= 1,
            "record_version_invalid",
        )

        procedure = candidate_keys(
            record["procedure"], CANDIDATE_PROCEDURE_KEYS, "procedure_invalid"
        )
        candidate_require(
            procedure["schema_version"] == 1
            and not isinstance(procedure["schema_version"], bool),
            "procedure_invalid",
        )
        candidate_text(procedure["trigger"], "procedure_invalid")
        candidate_text(procedure["outcome"], "procedure_invalid")
        for field in ("actions", "exclusions"):
            values = procedure[field]
            candidate_require(
                isinstance(values, list) and 1 <= len(values) <= 16, "procedure_invalid"
            )
            for item in values:
                candidate_text(item, "procedure_invalid")
        candidate_require(
            isinstance(procedure["match_fingerprint"], str)
            and CANDIDATE_ID_RE.fullmatch(procedure["match_fingerprint"]),
            "procedure_invalid",
        )

        candidate_require(isinstance(record["evidence"], list), "evidence_invalid")
        evidence_ids: set[str] = set()
        for item in record["evidence"]:
            item = candidate_keys(item, CANDIDATE_EVIDENCE_KEYS, "evidence_invalid")
            observation = {
                key: item[key] for key in CANDIDATE_EVIDENCE_KEYS - {"evidence_id"}
            }
            candidate_require(
                item["evidence_id"] == candidate_sha(observation),
                "evidence_identity_mismatch",
            )
            candidate_require(
                item["evidence_id"] not in evidence_ids, "evidence_identity_repeated"
            )
            evidence_ids.add(item["evidence_id"])
            candidate_text(item["task_key"], "evidence_invalid", 512)
            candidate_text(item["session_id"], "evidence_invalid", 512)
            candidate_text(item["summary"], "evidence_invalid")
            candidate_time(item["observed_at"], "evidence_invalid")
            candidate_require(
                item["independence"] in {"verified", "unverified"}, "evidence_invalid"
            )
            candidate_require(
                item["procedure_fingerprint"] == procedure["match_fingerprint"],
                "evidence_procedure_mismatch",
            )

        revisions = record["candidate_revisions"]
        candidate_require(isinstance(revisions, list) and revisions, "revision_invalid")
        revision_ids: list[str] = []
        for revision in revisions:
            revision = candidate_keys(
                revision, CANDIDATE_REVISION_KEYS, "revision_invalid"
            )
            identity = revision["candidate_id"]
            candidate_require(
                isinstance(identity, str) and CANDIDATE_ID_RE.fullmatch(identity),
                "revision_invalid",
            )
            candidate_require(identity not in revision_ids, "revision_repeated")
            revision_ids.append(identity)
            candidate_require(
                revision["package_path"]
                == f"candidates/v1/packages/{lifecycle_id}/{identity}",
                "package_reference_invalid",
            )
            self._candidate_declared_files(revision["files"])
            candidate_time(revision["staged_at"], "revision_invalid")
        current = record["current_candidate_id"]
        candidate_require(current in revision_ids, "current_candidate_unknown")

        packages = self._candidate_packages_root() / lifecycle_id
        for revision in revisions:
            directory = packages / revision["candidate_id"]
            if revision["candidate_id"] != current:
                candidate_require(not directory.is_symlink(), "package_symlinked")
                candidate_require(directory.is_dir(), "package_unavailable")
                continue
            files = self._candidate_package_files(directory)
            candidate_require(files == revision["files"], "package_inventory_mismatch")
            candidate_require(
                candidate_sha(files) == current, "candidate_identity_mismatch"
            )

        evaluation = candidate_keys(
            record["evaluation"], CANDIDATE_EVALUATION_KEYS, "evaluation_invalid"
        )
        candidate_require(
            evaluation["status"] in CANDIDATE_EVALUATION_LABELS, "evaluation_invalid"
        )
        history = evaluation["history"]
        candidate_require(isinstance(history, list), "evaluation_invalid")
        for entry in history:
            entry = candidate_keys(
                entry, CANDIDATE_EVALUATION_HISTORY_KEYS, "evaluation_invalid"
            )
            payload = {
                key: entry[key]
                for key in CANDIDATE_EVALUATION_HISTORY_KEYS - {"evaluation_id"}
            }
            candidate_require(
                entry["evaluation_id"] == candidate_sha(payload),
                "evaluation_identity_mismatch",
            )
            candidate_time(entry["evaluated_at"], "evaluation_invalid")
            candidate_require(
                entry["recommendation"] in {"ready_for_draft", "collecting"},
                "evaluation_recommendation_invalid",
            )
            candidate_require(
                isinstance(entry["reasons"], list)
                and all(isinstance(reason, str) for reason in entry["reasons"]),
                "evaluation_invalid",
            )
            candidate_require(
                entry["candidate_id"] in revision_ids, "evaluation_candidate_unknown"
            )
            candidate_require(entry["shadow_only"] is True, "evaluation_not_shadow")
        if history:
            candidate_require(
                evaluation["last_evaluated_at"] == history[-1]["evaluated_at"],
                "evaluation_reference_stale",
            )
            candidate_require(
                (evaluation["status"] == "shadow_ready")
                == (history[-1]["recommendation"] == "ready_for_draft"),
                "evaluation_status_mismatch",
            )
        else:
            candidate_require(
                evaluation["status"] == "not_evaluated"
                and evaluation["last_evaluated_at"] is None,
                "evaluation_status_mismatch",
            )

        lifecycle = candidate_keys(
            record["lifecycle"], CANDIDATE_LIFECYCLE_KEYS, "lifecycle_invalid"
        )
        for field in ("created_at", "last_supported_at", "expires_at"):
            candidate_time(lifecycle[field], "lifecycle_invalid")
        transitions = lifecycle["transition_history"]
        candidate_require(
            isinstance(transitions, list) and transitions, "transition_invalid"
        )
        prior = None
        for entry in transitions:
            entry = candidate_keys(
                entry, CANDIDATE_TRANSITION_KEYS, "transition_invalid"
            )
            payload = {
                key: entry[key] for key in CANDIDATE_TRANSITION_KEYS - {"transition_id"}
            }
            candidate_require(
                entry["transition_id"] == candidate_sha(payload),
                "transition_identity_mismatch",
            )
            candidate_require(
                entry["from_state"] == prior, "transition_history_discontinuous"
            )
            candidate_require(
                entry["to_state"] in CANDIDATE_STATE_LABELS,
                "transition_state_not_shadow",
            )
            candidate_require(
                entry["to_state"] in CANDIDATE_INITIAL_STATES
                if prior is None
                else entry["to_state"] in CANDIDATE_TRANSITIONS[prior],
                "transition_illegal",
            )
            candidate_time(entry["at"], "transition_invalid")
            candidate_require(
                isinstance(entry["reason"], str)
                and CANDIDATE_REASON_RE.fullmatch(entry["reason"]),
                "transition_reason_invalid",
            )
            candidate_require(
                isinstance(entry["authorizing_evidence_ids"], list),
                "transition_evidence_invalid",
            )
            candidate_require(
                all(
                    item in evidence_ids for item in entry["authorizing_evidence_ids"]
                ),
                "transition_evidence_invalid",
            )
            candidate_require(
                isinstance(entry["receipt_ids"], list)
                and all(
                    isinstance(item, str) and CANDIDATE_ID_RE.fullmatch(item)
                    for item in entry["receipt_ids"]
                ),
                "transition_receipt_invalid",
            )
            prior = entry["to_state"]
        candidate_require(prior == record["state"], "state_history_mismatch")

        candidate_require(isinstance(record["aliases"], list), "alias_invalid")
        aliases: set[tuple[str, str]] = set()
        for alias in record["aliases"]:
            alias = candidate_keys(alias, {"namespace", "value"}, "alias_invalid")
            item = (
                candidate_text(alias["namespace"], "alias_invalid", 128),
                candidate_text(alias["value"], "alias_invalid", 512),
            )
            candidate_require(item not in aliases, "alias_repeated")
            aliases.add(item)

        absorbed = record["absorbed_into"]
        candidate_require(
            (
                isinstance(absorbed, str) and CANDIDATE_UUID_RE.fullmatch(absorbed)
                if record["state"] == "absorbed"
                else absorbed is None
            ),
            "absorption_target_invalid",
        )

        candidate_require(isinstance(record["match_decisions"], list), "decision_invalid")
        decision_ids: set[str] = set()
        for decision in record["match_decisions"]:
            decision = candidate_keys(
                decision, CANDIDATE_DECISION_KEYS, "decision_invalid"
            )
            payload = {
                key: decision[key]
                for key in CANDIDATE_DECISION_KEYS - {"decision_id"}
            }
            candidate_require(
                decision["decision_id"] == candidate_sha(payload),
                "decision_identity_mismatch",
            )
            candidate_require(
                decision["decision_id"] not in decision_ids,
                "decision_identity_repeated",
            )
            decision_ids.add(decision["decision_id"])
            candidate_time(decision["at"], "decision_invalid")
            candidate_require(
                decision["outcome"] in CANDIDATE_MATCH_OUTCOMES,
                "decision_outcome_invalid",
            )
            candidate_require(
                isinstance(decision["reason"], str)
                and CANDIDATE_REASON_RE.fullmatch(decision["reason"]),
                "decision_reason_invalid",
            )
            related = decision["related_lifecycle_id"]
            candidate_require(
                related is None
                or (isinstance(related, str) and CANDIDATE_UUID_RE.fullmatch(related)),
                "decision_relation_invalid",
            )
            candidate_require(
                isinstance(decision["evidence_ids"], list),
                "decision_evidence_invalid",
            )
            candidate_require(
                all(item in evidence_ids for item in decision["evidence_ids"]),
                "decision_evidence_invalid",
            )
            candidate_require(decision["shadow_only"] is True, "decision_not_shadow")

        blockers = candidate_keys(
            record["blockers"], CANDIDATE_BLOCKER_KEYS, "blocker_invalid"
        )
        covering = blockers["covering_lifecycle_ids"]
        candidate_require(isinstance(covering, list), "blocker_invalid")
        candidate_require(
            all(
                isinstance(item, str) and CANDIDATE_UUID_RE.fullmatch(item)
                for item in covering
            ),
            "blocker_invalid",
        )
        candidate_require(len(set(covering)) == len(covering), "blocker_invalid")
        tombstones = blockers["tombstone_ids"]
        candidate_require(isinstance(tombstones, list), "blocker_invalid")
        candidate_require(
            all(
                isinstance(item, str) and item and len(item) <= 512
                for item in tombstones
            ),
            "blocker_invalid",
        )
        candidate_require(len(set(tombstones)) == len(tombstones), "blocker_invalid")
        candidate_require(isinstance(blockers["uncertain"], bool), "blocker_invalid")
        return record

    def _candidate_gates(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        evaluation = record["evaluation"]
        history = evaluation["history"]
        gates = [
            {
                "name": "recurrence",
                "label": CANDIDATE_GATE_LABELS["recurrence"],
                "status": evaluation["status"],
                "status_label": CANDIDATE_EVALUATION_LABELS[evaluation["status"]],
                "reasons": list(history[-1]["reasons"]) if history else [],
                "evaluated_at": evaluation["last_evaluated_at"],
                "shadow_only": True,
            }
        ]
        for name in CANDIDATE_UNEVALUATED_GATES:
            gates.append(
                {
                    "name": name,
                    "label": CANDIDATE_GATE_LABELS[name],
                    "status": "unavailable",
                    "status_label": f"{CANDIDATE_GATE_LABELS[name]}: no evidence recorded",
                    "reasons": [f"no-{name.replace('_', '-')}-evidence-recorded"],
                    "evaluated_at": None,
                    "shadow_only": True,
                }
            )
        return gates

    def _candidate_view(
        self, record: dict[str, Any], detail: bool = False
    ) -> dict[str, Any]:
        now = time.time()
        lifecycle = record["lifecycle"]
        transitions = lifecycle["transition_history"]
        latest_transition = transitions[-1]
        evidence = record["evidence"]
        verified = [item for item in evidence if item["independence"] == "verified"]
        newest = (
            max(verified, key=lambda item: parse_time(item["observed_at"]))
            if verified
            else None
        )
        newest_at = parse_time(newest["observed_at"]) if newest else None
        expires_at = parse_time(lifecycle["expires_at"])
        evaluation = record["evaluation"]
        history = evaluation["history"]
        latest_evaluation = history[-1] if history else None
        recommendation = (
            latest_evaluation["recommendation"] if latest_evaluation else "none"
        )
        decisions = record["match_decisions"]
        counts = {outcome: 0 for outcome in sorted(CANDIDATE_MATCH_OUTCOMES)}
        for decision in decisions:
            counts[decision["outcome"]] += 1
        blockers = record["blockers"]
        blocker_reasons = []
        if blockers["uncertain"]:
            blocker_reasons.append("uncertain-match-blocker")
        if blockers["covering_lifecycle_ids"]:
            blocker_reasons.append("covering-lifecycle-blocker")
        if blockers["tombstone_ids"]:
            blocker_reasons.append("tombstone-blocker")
        current = next(
            item
            for item in record["candidate_revisions"]
            if item["candidate_id"] == record["current_candidate_id"]
        )
        updated_at = max(
            (
                value
                for value in (
                    latest_transition["at"],
                    evaluation["last_evaluated_at"],
                    decisions[-1]["at"] if decisions else None,
                )
                if value is not None
            ),
            key=lambda value: parse_time(value) or 0,
        )
        view = {
            "lifecycle_id": record["lifecycle_id"],
            "status": "shadow",
            "state": record["state"],
            "state_label": CANDIDATE_STATE_LABELS[record["state"]],
            "state_reason": latest_transition["reason"],
            "state_changed_at": latest_transition["at"],
            "proposed_name": safe_text(record["proposed_name"], 64),
            "authority": CANDIDATE_AUTHORITY,
            "record_authority": record["authority"],
            "label": CANDIDATE_LABEL,
            "notice": CANDIDATE_NOTICE,
            "shadow_only": True,
            "active": False,
            "published": False,
            "discoverable": False,
            "publication_status": record["publication"]["status"],
            "publication_targets": [],
            "current_candidate_id": record["current_candidate_id"],
            "candidate_revision": {
                "candidate_id": current["candidate_id"],
                "package_path": current["package_path"],
                "package_status": "verified",
                "file_count": len(current["files"]),
                "bytes": sum(item["size"] for item in current["files"]),
                "staged_at": current["staged_at"],
            },
            "candidate_revision_count": len(record["candidate_revisions"]),
            "evidence": {
                "total": len(evidence),
                "verified": len(verified),
                "unverified": len(evidence) - len(verified),
                "distinct_tasks": len({item["task_key"] for item in verified}),
                "distinct_sessions": len({item["session_id"] for item in verified}),
            },
            "freshness": {
                "created_at": lifecycle["created_at"],
                "last_supported_at": lifecycle["last_supported_at"],
                "expires_at": lifecycle["expires_at"],
                "newest_verified_evidence_at": (
                    newest["observed_at"] if newest else None
                ),
                "fresh_evidence": (
                    newest_at is not None
                    and 0 <= now - newest_at <= CANDIDATE_FRESH_SECONDS
                ),
                "past_expiry": expires_at is not None and expires_at <= now,
                "days_until_expiry": (
                    max(0, math.ceil((expires_at - now) / 86400))
                    if expires_at is not None
                    else None
                ),
            },
            "evaluation": {
                "status": evaluation["status"],
                "status_label": CANDIDATE_EVALUATION_LABELS[evaluation["status"]],
                "last_evaluated_at": evaluation["last_evaluated_at"],
                "history_count": len(history),
                "composite_score": None,
                "gates": self._candidate_gates(record),
                "shadow_only": True,
                "authorizes_publication": False,
            },
            "recommendation": {
                "value": recommendation,
                "label": CANDIDATE_RECOMMENDATION_LABELS[recommendation],
                "reasons": (
                    list(latest_evaluation["reasons"]) if latest_evaluation else []
                ),
                "candidate_id": (
                    latest_evaluation["candidate_id"] if latest_evaluation else None
                ),
                "evaluated_at": (
                    latest_evaluation["evaluated_at"] if latest_evaluation else None
                ),
                "stale": bool(
                    latest_evaluation
                    and latest_evaluation["candidate_id"]
                    != record["current_candidate_id"]
                ),
                "shadow_only": True,
                "authorizes_publication": False,
                "authorizes_activation": False,
            },
            "decisions": {
                "total": len(decisions),
                "counts": counts,
                "outcomes": sorted({item["outcome"] for item in decisions}),
                "shadow_only": True,
            },
            "blockers": {
                "present": bool(blocker_reasons),
                "reasons": blocker_reasons,
                "covering_lifecycle_ids": list(blockers["covering_lifecycle_ids"]),
                "tombstone_ids": [
                    safe_text(item, 512) for item in blockers["tombstone_ids"]
                ],
                "uncertain": blockers["uncertain"],
            },
            "absorbed_into": record["absorbed_into"],
            "aliases": [
                {
                    "namespace": safe_text(item["namespace"], 128),
                    "value": safe_text(item["value"], 512),
                }
                for item in record["aliases"]
            ],
            "created_at": lifecycle["created_at"],
            "updated_at": updated_at,
            "record_version": record["record_version"],
        }
        if not detail:
            return view
        procedure = record["procedure"]
        names = self._dream_names()
        return {
            **view,
            "procedure": {
                "trigger": safe_text(procedure["trigger"], 4000),
                "outcome": safe_text(procedure["outcome"], 4000),
                "actions": [safe_text(item, 4000) for item in procedure["actions"]],
                "exclusions": [
                    safe_text(item, 4000) for item in procedure["exclusions"]
                ],
                "match_fingerprint": procedure["match_fingerprint"],
            },
            "evidence_items": [
                {
                    "evidence_id": item["evidence_id"],
                    "task_key": safe_text(item["task_key"], 512),
                    "session_id": item["session_id"],
                    "dream_name": self._dream_name(item["session_id"], names),
                    "observed_at": item["observed_at"],
                    "independence": item["independence"],
                    "summary": safe_text(item["summary"], 4000),
                }
                for item in evidence
            ],
            "candidate_revisions": [
                {
                    "candidate_id": item["candidate_id"],
                    "package_path": item["package_path"],
                    "package_status": (
                        "verified"
                        if item["candidate_id"] == record["current_candidate_id"]
                        else "present"
                    ),
                    "current": item["candidate_id"] == record["current_candidate_id"],
                    "file_count": len(item["files"]),
                    "bytes": sum(entry["size"] for entry in item["files"]),
                    "staged_at": item["staged_at"],
                }
                for item in record["candidate_revisions"]
            ],
            "transition_history": [
                {
                    "transition_id": item["transition_id"],
                    "from_state": item["from_state"],
                    "from_state_label": (
                        CANDIDATE_STATE_LABELS[item["from_state"]]
                        if item["from_state"]
                        else None
                    ),
                    "to_state": item["to_state"],
                    "to_state_label": CANDIDATE_STATE_LABELS[item["to_state"]],
                    "at": item["at"],
                    "reason": item["reason"],
                    "authorizing_evidence_ids": list(
                        item["authorizing_evidence_ids"]
                    ),
                    "receipt_ids": list(item["receipt_ids"]),
                    "shadow_only": True,
                }
                for item in transitions
            ],
            "evaluation_history": [
                {
                    "evaluation_id": item["evaluation_id"],
                    "evaluated_at": item["evaluated_at"],
                    "recommendation": item["recommendation"],
                    "recommendation_label": CANDIDATE_RECOMMENDATION_LABELS[
                        item["recommendation"]
                    ],
                    "reasons": list(item["reasons"]),
                    "candidate_id": item["candidate_id"],
                    "current_candidate": (
                        item["candidate_id"] == record["current_candidate_id"]
                    ),
                    "shadow_only": True,
                    "authorizes_publication": False,
                }
                for item in history
            ],
            "match_decisions": [
                {
                    "decision_id": item["decision_id"],
                    "at": item["at"],
                    "outcome": item["outcome"],
                    "reason": item["reason"],
                    "related_lifecycle_id": item["related_lifecycle_id"],
                    "evidence_ids": list(item["evidence_ids"]),
                    "shadow_only": True,
                }
                for item in decisions
            ],
        }

    def _candidate_unavailable(self, path: Path, error: Exception) -> dict[str, Any]:
        reason = getattr(error, "reason", "record_unreadable")
        return {
            "lifecycle_id": (
                path.stem if CANDIDATE_UUID_RE.fullmatch(path.stem) else None
            ),
            "source": path.name,
            "status": "invalid",
            "state": None,
            "state_label": CANDIDATE_INVALID_LABEL,
            "state_reason": reason,
            "proposed_name": None,
            "authority": CANDIDATE_AUTHORITY,
            "label": CANDIDATE_INVALID_LABEL,
            "notice": CANDIDATE_NOTICE,
            "shadow_only": True,
            "active": False,
            "published": False,
            "discoverable": False,
            "current_candidate_id": None,
            "reasons": [reason],
            "error": safe_text(str(error), 500),
            "updated_at": None,
        }

    def candidate_rows(self) -> tuple[list[dict[str, Any]], str]:
        root = self._candidate_records_root()
        packages = self._candidate_packages_root()
        if root.is_symlink() or (root.exists() and not root.is_dir()):
            raise DashboardError(
                503,
                "candidate_state_invalid",
                "Candidate record root is not a directory",
                ["candidate records"],
            )
        fingerprint = self._fingerprint([root, packages])
        if not root.is_dir():
            return [], fingerprint
        rows = []
        try:
            entries = sorted(root.glob("*.json"))
        except OSError as exc:
            raise DashboardError(
                503,
                "candidate_state_invalid",
                "Candidate record root is unreadable",
                ["candidate records"],
            ) from exc
        for path in entries:
            try:
                rows.append(self._candidate_view(self._candidate_record(path)))
            except (CandidateInvalid, OSError, UnicodeError, DashboardError) as exc:
                rows.append(self._candidate_unavailable(path, exc))
        rows.sort(
            key=lambda item: (
                parse_time(item.get("updated_at")) or 0,
                item.get("lifecycle_id") or item.get("source") or "",
            ),
            reverse=True,
        )
        return rows, fingerprint

    def candidates(self, params: dict[str, list[str]]) -> dict[str, Any]:
        rows, fingerprint = self.candidate_rows()
        state = first(params, "state")
        status = first(params, "status")
        recommendation = first(params, "recommendation")
        query_text = first(params, "query").casefold()
        if state:
            rows = [item for item in rows if item.get("state") == state]
        if status:
            rows = [item for item in rows if item.get("status") == status]
        if recommendation:
            rows = [
                item
                for item in rows
                if (item.get("recommendation") or {}).get("value") == recommendation
            ]
        if query_text:
            rows = [
                item
                for item in rows
                if query_text in (item.get("proposed_name") or "").casefold()
                or query_text in (item.get("lifecycle_id") or "").casefold()
            ]
        sort = first(params, "sort") or "updated"
        if sort == "name":
            rows.sort(key=lambda item: (item.get("proposed_name") or "").casefold())
        elif sort == "state":
            rows.sort(key=lambda item: (item.get("state") or "", item.get("proposed_name") or ""))
        query = {
            "state": state,
            "status": status,
            "recommendation": recommendation,
            "query": query_text,
            "sort": sort,
        }
        return {
            **self._cursor(
                rows,
                query,
                fingerprint,
                first(params, "cursor") or None,
                parse_limit(params),
            ),
            "authority": CANDIDATE_AUTHORITY,
            "shadow_only": True,
            "active": False,
            "published": False,
            "label": CANDIDATE_LABEL,
            "notice": CANDIDATE_NOTICE,
        }

    def candidate_detail(self, lifecycle_id: str) -> dict[str, Any]:
        if not CANDIDATE_UUID_RE.fullmatch(lifecycle_id):
            raise DashboardError(
                404, "candidate_not_found", "Candidate record was not found"
            )
        root = self._candidate_records_root()
        path = root / f"{lifecycle_id}.json"
        if (
            not root.is_dir()
            or root.is_symlink()
            or path.is_symlink()
            or not path.is_file()
            or path.parent.resolve() != root.resolve()
        ):
            raise DashboardError(
                404, "candidate_not_found", "Candidate record was not found"
            )
        try:
            record = self._candidate_record(path)
        except (CandidateInvalid, OSError, UnicodeError) as exc:
            raise DashboardError(
                422,
                "candidate_invalid",
                "Candidate record is unavailable or invalid",
                [getattr(exc, "reason", "record_unreadable")],
            ) from exc
        return self._candidate_view(record, detail=True)

    def candidate_summary(self) -> dict[str, Any]:
        root = self._candidate_records_root()
        rows, _ = self.candidate_rows()
        valid = [item for item in rows if item["status"] == "shadow"]
        states = {name: 0 for name in sorted(CANDIDATE_STATE_LABELS)}
        recommendations = {"ready_for_draft": 0, "collecting": 0, "none": 0}
        for item in valid:
            states[item["state"]] += 1
            recommendations[item["recommendation"]["value"]] += 1
        return {
            "authority": CANDIDATE_AUTHORITY,
            "shadow_only": True,
            "active": False,
            "published": False,
            "discoverable": False,
            "label": CANDIDATE_LABEL,
            "notice": CANDIDATE_NOTICE,
            "records_root_present": root.is_dir(),
            "total": len(rows),
            "valid": len(valid),
            "invalid": len(rows) - len(valid),
            "states": states,
            "state_labels": dict(CANDIDATE_STATE_LABELS),
            "recommendations": recommendations,
            "stale_recommendations": sum(
                item["recommendation"]["stale"] for item in valid
            ),
            "blocked": sum(item["blockers"]["present"] for item in valid),
            "past_expiry": sum(item["freshness"]["past_expiry"] for item in valid),
        }

    def activity(self, params: dict[str, list[str]]) -> dict[str, Any]:
        runs_dir = self.paths.orchestrator_state / "runs"
        rows = []
        scheduled = {}
        if runs_dir.is_dir():
            for path in runs_dir.glob("*.json"):
                if path.is_symlink():
                    continue
                run = self._json(path, {}, f"run:{path.name}")
                if not isinstance(run, dict):
                    continue
                run_id = run.get("run_id", path.stem)
                row = {
                        "kind": "scheduled",
                        "id": run_id,
                        "started_at": run.get("started_at"),
                        "ended_at": run.get("ended_at"),
                        "status": run.get("status"),
                        "reason": run.get("reason"),
                        "passes": run.get("passes", []),
                        "last_success_at_before": run.get("last_success_at_before"),
                        "maintenance": maintenance_status(run),
                        "reviews": [],
                    }
                rows.append(row)
                scheduled[run_id] = row
        for item in self._list("review-attempts.json"):
            if not isinstance(item, dict):
                continue
            review = {
                "kind": "dream-review",
                "id": sha(item),
                "started_at": item.get("started_at"),
                "status": item.get("status"),
                "source": item.get("source"),
                "session_id": item.get("session_id"),
            }
            parent_run_id = item.get("parent_run_id")
            if parent_run_id in scheduled:
                scheduled[parent_run_id]["reviews"].append(review)
            else:
                if isinstance(parent_run_id, str) and parent_run_id:
                    review["parent_run_id"] = parent_run_id
                rows.append(review)
        transition_root = (
            self.paths.control_state
            / "skill-review/evaluations/v2/dashboard-v1/authority-transitions"
        )
        if transition_root.is_dir():
            for path in transition_root.glob("*/*.json"):
                if path.is_symlink():
                    continue
                transition = self._json(path, {}, f"transition:{path.name}")
                if isinstance(transition, dict):
                    rows.append(
                        {
                            "kind": "evaluation",
                            "id": transition.get("transition_id", path.stem),
                            "started_at": transition.get("effective_at"),
                            "status": transition.get("status"),
                            "candidate_id": transition.get("candidate_id"),
                            "skill_key": transition.get("skill_key"),
                        }
                    )
        rows.sort(key=lambda item: parse_time(item.get("started_at")) or 0, reverse=True)
        kind = first(params, "kind")
        if kind:
            rows = [item for item in rows if item["kind"] == kind]
        fingerprint = self._fingerprint(
            [runs_dir, self.paths.state / "review-attempts.json", transition_root]
        )
        return self._cursor(
            rows,
            {"kind": kind},
            fingerprint,
            first(params, "cursor") or None,
            parse_limit(params),
        )

    def _evaluation_portfolio(self) -> dict[str, Any]:
        root = self.paths.control_state / "skill-review/evaluations/v2/dashboard-v1"
        transition_root = root / "authority-transitions"
        current: dict[str, dict[str, Any]] = {}
        transitions = []
        if transition_root.is_dir():
            for path in transition_root.glob("*/*.json"):
                if path.is_symlink():
                    continue
                transition = self._json(path, {}, f"transition:{path.name}")
                if not isinstance(transition, dict):
                    continue
                transitions.append(transition)
                key = transition.get("skill_key")
                if not isinstance(key, str):
                    continue
                existing = current.get(key)
                if existing is None or (parse_time(transition.get("effective_at")) or 0) > (
                    parse_time(existing.get("effective_at")) or 0
                ):
                    current[key] = transition
        installed = {
            self._skill_key(self.paths.skills / item["name"]): {
                "candidate_id": item.get("candidate_id"),
                "path": self.paths.skills / item["name"],
                "validate_current": True,
            }
            for item in self.skill_rows()[0]
            if item.get("status") == "current"
        }
        metrics = self._portfolio_metrics(current.values(), installed)
        history = []
        effective = {}
        relevant_keys = {
            transition.get("skill_key")
            for transition in transitions
            if isinstance(transition.get("skill_key"), str)
        }
        self._refresh_candidate_history_cache()
        candidate_history = {
            key: self._candidate_history(value["path"])
            for key, value in installed.items()
            if key in relevant_keys
        }
        for transition in sorted(
            transitions, key=lambda item: parse_time(item.get("effective_at")) or 0
        ):
            key = transition.get("skill_key")
            at = parse_time(transition.get("effective_at"))
            if not isinstance(key, str) or at is None:
                continue
            effective[key] = transition
            historical = {}
            for installed_key, history_items in candidate_history.items():
                value = installed[installed_key]
                candidate = self._candidate_at(history_items, at)
                if candidate is not None:
                    historical[installed_key] = {
                        "candidate_id": candidate,
                        "path": value["path"],
                        "validate_current": False,
                    }
            point = self._portfolio_metrics(effective.values(), historical)
            history.append(
                {
                    "at": int(at),
                    "candidate_percent": point["candidate_percent"],
                    "control_percent": point["control_percent"],
                    "comparable_skills": point["comparable_skills"],
                }
            )
        return {**metrics, "history": history}

    def _refresh_candidate_history_cache(self) -> None:
        if time.monotonic() < self._candidate_history_retry_at:
            return
        try:
            if self._skills_git_root is False:
                self._skills_git_root = Path(
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(self.paths.skills),
                            "rev-parse",
                            "--show-toplevel",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    ).stdout.strip()
                ).resolve()
            head = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self._skills_git_root),
                    "rev-parse",
                    "HEAD",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            self._candidate_history_retry_at = time.monotonic() + 30
            self._candidate_history_cache.clear()
            return
        self._candidate_history_retry_at = 0
        if head != self._candidate_history_head:
            self._candidate_history_head = head
            self._candidate_history_cache.clear()

    def _candidate_history(self, skill: Path) -> list[tuple[float, str]]:
        cache_key = str(skill.resolve())
        if cache_key in self._candidate_history_cache:
            return self._candidate_history_cache[cache_key]
        try:
            if not isinstance(self._skills_git_root, Path):
                return []
            root = self._skills_git_root
            relative = skill.resolve().relative_to(root).as_posix()
            commits = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "log",
                    "--reverse",
                    "--format=%ct %H",
                    "--",
                    relative,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.splitlines()
        except (OSError, ValueError, subprocess.SubprocessError):
            if self._skills_git_root is False:
                self._skills_git_root = None
            return []
        history = []
        for line in commits:
            try:
                raw_time, commit = line.split(" ", 1)
                archived = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(root),
                        "archive",
                        "--format=tar",
                        commit,
                        "--",
                        relative,
                    ],
                    check=True,
                    capture_output=True,
                    timeout=30,
                ).stdout
                files = []
                with tarfile.open(fileobj=io.BytesIO(archived), mode="r:") as archive:
                    for member in sorted(archive.getmembers(), key=lambda item: item.name):
                        if not member.isfile():
                            continue
                        path = Path(member.name)
                        relative_file = path.relative_to(relative).as_posix()
                        if relative_file in EVALUATION_SIDECARS:
                            continue
                        if path.name in EVALUATION_SIDECARS:
                            raise ValueError("reserved nested evaluation sidecar")
                        extracted = archive.extractfile(member)
                        if extracted is None:
                            raise ValueError("archive member is unreadable")
                        content = extracted.read()
                        files.append(
                            {
                                "path": relative_file,
                                "sha256": hashlib.sha256(content).hexdigest(),
                                "size": len(content),
                            }
                        )
                candidate = "sha256:" + hashlib.sha256(canonical(files)).hexdigest()
                history.append((float(raw_time), candidate))
            except (OSError, ValueError, tarfile.TarError, subprocess.SubprocessError):
                return []
        self._candidate_history_cache[cache_key] = history
        return history

    def _candidate_at(
        self, history: list[tuple[float, str]], at: float
    ) -> str | None:
        candidates = [
            candidate
            for committed_at, candidate in history
            if committed_at <= at + 1
        ]
        return candidates[-1] if candidates else None

    def _portfolio_metrics(
        self,
        transitions: Any,
        installed: dict[str, Any],
    ) -> dict[str, Any]:
        portfolio_root = (
            self.paths.control_state
            / "skill-review/evaluations/v2/dashboard-v1/portfolio"
        )
        skill_values = []
        preference = {"pass": 0, "regression": 0, "inconclusive": 0}
        for transition in transitions:
            status = transition.get("status")
            skill_key = transition.get("skill_key")
            candidate = transition.get("candidate_id")
            installed_skill = installed.get(skill_key)
            if (
                not isinstance(installed_skill, dict)
                or installed_skill.get("candidate_id") != candidate
            ):
                continue
            if (
                installed_skill.get("validate_current") is True
                and not self._transition_matches_current(
                    installed_skill["path"], transition
                )
            ):
                continue
            if status != "pass":
                if status in preference:
                    preference[status] += 1
                continue
            receipt_sha = transition.get("portfolio_receipt_sha256")
            if not isinstance(receipt_sha, str) or not SHA256_RE.fullmatch(receipt_sha):
                continue
            receipt = self._json(portfolio_root / f"{receipt_sha}.json", {}, "portfolio receipt")
            if (
                hashlib.sha256(canonical(receipt)).hexdigest() != receipt_sha
                or receipt.get("kind") != "dashboard_portfolio_receipt"
                or receipt.get("skill_key") != skill_key
                or receipt.get("candidate_id") != candidate
            ):
                raise DashboardError(
                    503,
                    "evaluation_invalid",
                    "Portfolio receipt identity does not match current authority",
                    ["evaluation portfolio"],
                )
            cases = receipt.get("cases")
            if not isinstance(cases, list):
                continue
            comparable = [
                item
                for item in cases
                if isinstance(item, dict)
                and item.get("evaluation_class") == "capability_uplift"
                and item.get("comparable") is True
                and item.get("candidate_valid_trials")
                and item.get("control_valid_trials")
            ]
            if not comparable:
                if any(
                    isinstance(item, dict)
                    and item.get("evaluation_class") == "encoded_preference"
                    for item in cases
                ):
                    preference["pass"] += 1
                continue
            candidate = sum(
                item["candidate_successful_trials"] / item["candidate_valid_trials"]
                for item in comparable
            ) / len(comparable)
            control = sum(
                item["control_successful_trials"] / item["control_valid_trials"]
                for item in comparable
            ) / len(comparable)
            skill_values.append((candidate, control))
        return {
            "candidate_percent": (
                round(100 * sum(item[0] for item in skill_values) / len(skill_values), 1)
                if skill_values
                else None
            ),
            "control_percent": (
                round(100 * sum(item[1] for item in skill_values) / len(skill_values), 1)
                if skill_values
                else None
            ),
            "comparable_skills": len(skill_values),
            "preference": preference,
        }

    def overview(self) -> dict[str, Any]:
        dreams, _ = self.dream_rows()
        skills, _ = self.skill_rows()
        remaining = [item for item in dreams if item["status"] == "remaining"]
        completed = [item for item in dreams if item["status"] == "completed"]
        activity = self.activity({"limit": ["5"]})["items"]
        capacity = self._capacity_projection()
        return {
            "runtime": self.health(),
            "dreams": {
                "remaining": len(remaining),
                "completed": len(completed),
                "active": sum(item["status"] == "active" for item in dreams),
                "history": self._backlog_history(),
                **capacity,
            },
            "skills": {
                "count": len([item for item in skills if item.get("status") == "current"]),
                "latest": sorted(
                    skills,
                    key=lambda item: parse_time(item.get("created_at")) or 0,
                    reverse=True,
                )[:5],
                "history": self._skill_history(),
            },
            "evaluations": self._evaluation_portfolio(),
            "candidates": self.candidate_summary(),
            "estate": self.estate(summary_only=True),
            "activity": activity,
        }

    def _capacity_projection(self) -> dict[str, Any]:
        queue = [
            item for item in self._list("queue.json") if isinstance(item, dict)
        ]
        ledger = [
            item
            for item in self._list("review-ledger.json")
            if isinstance(item, dict)
        ]
        now = time.time()
        cutoff = now - 86400
        current_queued = {
            item.get("qualified_session_id"): item
            for item in queue
            if item.get("status") == "queued"
            and isinstance(item.get("qualified_session_id"), str)
        }
        recovery_required = {
            item.get("qualified_session_id")
            for item in queue
            if item.get("status") == "recovery-required"
            and isinstance(item.get("qualified_session_id"), str)
        }

        queued_times = [parse_time(item.get("queued_at")) for item in current_queued.values()]
        oldest_queued_at = None
        oldest_queued_age_seconds = None
        if queued_times and all(value is not None for value in queued_times):
            oldest = min(value for value in queued_times if value is not None)
            oldest_queued_at = datetime.fromtimestamp(oldest, timezone.utc).isoformat()
            oldest_queued_age_seconds = max(0, int(now - oldest))

        arrival_times = [parse_time(item.get("queued_at")) for item in queue]
        completion_times = [parse_time(item.get("reviewed_at")) for item in ledger]
        arrivals_24h = (
            sum(value >= cutoff for value in arrival_times if value is not None)
            if all(value is not None for value in arrival_times)
            else None
        )
        completed_24h = (
            sum(value >= cutoff for value in completion_times if value is not None)
            if all(value is not None for value in completion_times)
            else None
        )
        observed_net_24h = (
            completed_24h - arrivals_24h
            if arrivals_24h is not None and completed_24h is not None
            else None
        )
        if observed_net_24h is None:
            capacity_status = "unknown"
            estimated_burn_down_days = None
        elif observed_net_24h > 0:
            capacity_status = "burning_down"
            estimated_burn_down_days = math.ceil(
                len(current_queued) / observed_net_24h
            )
        else:
            capacity_status = "not_burning_down"
            estimated_burn_down_days = None
        return {
            "queued": len(current_queued),
            "oldest_queued_at": oldest_queued_at,
            "oldest_queued_age_seconds": oldest_queued_age_seconds,
            "arrivals_24h": arrivals_24h,
            "completed_24h": completed_24h,
            "recovery_required": len(recovery_required),
            "observed_net_24h": observed_net_24h,
            "estimated_burn_down_days": estimated_burn_down_days,
            "capacity_status": capacity_status,
        }

    def _estate_actions(self) -> dict[str, Any]:
        config_path = self.paths.review_state / "estate-action/config.json"
        if not config_path.exists():
            return {
                "status": "unavailable",
                "available": False,
                "stale": None,
                "recovery_required": False,
                "running": False,
                "halted": None,
                "paused": None,
                "writers_blocked": True,
                "total": 0,
                "items": [],
                "message": "Estate action authority is not configured.",
            }
        try:
            authority_path = self.paths.repo / "scripts" / "estate-action.py"
            if authority_path.is_symlink() or not authority_path.is_file():
                raise ValueError("authority implementation")
            specification = importlib.util.spec_from_file_location(
                "dreaming_dashboard_estate_action", authority_path
            )
            if specification is None or specification.loader is None:
                raise ValueError("authority implementation")
            authority_tool = importlib.util.module_from_spec(specification)
            try:
                specification.loader.exec_module(authority_tool)
            except Exception as error:
                raise ValueError("authority implementation") from error
            config = authority_tool.load_authority_config(config_path)
            current = authority_tool.load_current_evidence(config)
            state_root = Path(str(config["state_root"]))
            halt_switch = Path(str(config["halt_switch"]))
            recovery_state = Path(str(config["recovery_state"]))
            curator_state_path = Path(str(config["curator_state"]))
            if state_root.is_symlink() or not state_root.is_dir():
                raise ValueError("configured action state")
            curator_state = authority_tool.load_object(
                curator_state_path, "estate-action-curator-state-invalid"
            )
            if not isinstance(curator_state.get("paused"), bool):
                raise ValueError("curator state")

            items: list[dict[str, Any]] = []
            malformed = False
            for operation_root in sorted(state_root.iterdir()):
                if not operation_root.is_dir() or operation_root.is_symlink():
                    continue
                try:
                    authorization = authority_tool.load_object(
                        operation_root / "authorization.json",
                        "estate-action-state-invalid",
                    )
                    authorization = authority_tool.validate_authorization(
                        authorization
                    )
                    index = authority_tool.load_object(
                        operation_root / "index.json",
                        "estate-action-state-invalid",
                    )
                    action = authorization.get("action")
                    evidence = authorization.get("evidence")
                    executor = authorization.get("executor")
                    if (
                        authorization["action_id"] != operation_root.name
                        or set(index)
                        not in (
                            {
                                "schema_version",
                                "action_id",
                                "authorization_sha256",
                                "phase",
                            },
                            {
                                "schema_version",
                                "action_id",
                                "authorization_sha256",
                                "phase",
                                "result_sha256",
                            },
                        )
                        or index.get("schema_version") != 1
                        or index.get("action_id") != operation_root.name
                        or index.get("authorization_sha256")
                        != authorization["authorization_sha256"]
                    ):
                        raise ValueError("operation identity")
                    config_current = (
                        authorization["authority"]["config_sha256"]
                        == config["config_sha256"]
                        and authorization["authority"]["evidence_root"]
                        == config["evidence_root"]
                    )
                    if config_current:
                        adapter_path = authority_tool.configured_adapter(
                            config, action["kind"]
                        )
                        if (
                            action["adapter_sha256"]
                            != authority_tool.file_digest(adapter_path)
                            or executor["argv"][0] != str(adapter_path)
                            or evidence["receiver"]["value"]
                            != {
                                **config["receivers"][action["kind"]],
                                "adapter_sha256": action[
                                    "adapter_sha256"
                                ],
                            }
                        ):
                            raise ValueError("current action bindings")
                    stale_labels = sorted(
                        [
                            label
                            for label, wrapper in current.items()
                            if evidence[label]["sha256"]
                            != wrapper["sha256"]
                        ]
                        + ([] if config_current else ["configuration"])
                    )
                    phase = index.get("phase")
                    if phase not in {
                        "running",
                        "committed",
                        "rejected",
                        "rolled_back",
                        "recovery_required",
                    }:
                        raise ValueError("operation phase")
                    result_sha256 = None
                    if phase in {
                        "committed",
                        "rejected",
                        "rolled_back",
                        "recovery_required",
                    }:
                        if "result_sha256" not in index:
                            raise ValueError("result identity")
                        result = authority_tool.load_result(
                            operation_root, index
                        )
                        result = authority_tool.result_payload(
                            result, authorization
                        )
                        if result["status"] != phase:
                            raise ValueError("result seal")
                        result_sha256 = result["result_sha256"]
                    request_target = (
                        executor["request"].get("target")
                        if action["kind"].startswith("personal_")
                        else executor["request"].get("plugin")
                    )
                    target = evidence["target"]["value"]
                    display_name = (
                        request_target.get("skill")
                        if action["kind"].startswith("personal_")
                        else request_target.get("plugin_id")
                    )
                    items.append(
                        {
                            "action_id": safe_text(
                                operation_root.name, 100
                            ),
                            "kind": action["kind"],
                            "target": safe_text(display_name, 200),
                            "authority": safe_text(
                                target.get("authority")
                                if isinstance(target, dict)
                                else None,
                                80,
                            ),
                            "decision": safe_text(
                                target.get("decision")
                                if isinstance(target, dict)
                                else None,
                                80,
                            ),
                            "status": phase,
                            "stale": (
                                phase == "running" and bool(stale_labels)
                            ),
                            "evidence_state": (
                                "stale"
                                if phase == "running" and stale_labels
                                else "historical"
                                if stale_labels
                                else "current"
                            ),
                            "stale_evidence": stale_labels,
                            "receipt_sha256": (
                                result_sha256
                                if isinstance(result_sha256, str)
                                else None
                            ),
                            "at": datetime.fromtimestamp(
                                (operation_root / "index.json").stat().st_mtime,
                                timezone.utc,
                            ).isoformat(),
                        }
                    )
                except (
                    authority_tool.ActionError,
                    DashboardError,
                    OSError,
                    TypeError,
                    ValueError,
                    KeyError,
                ):
                    malformed = True

            known_ids = {item["action_id"] for item in items}
            for item in self._list("estate-action-ledger.json"):
                if not isinstance(item, dict):
                    malformed = True
                    continue
                record_payload = {
                    key: value
                    for key, value in item.items()
                    if key != "record_sha256"
                }
                action_id = safe_text(item.get("action_id"), 100)
                status = safe_text(item.get("status"), 80)
                target_kind = safe_text(item.get("target_kind"), 40)
                if (
                    set(item)
                    != {
                        "action_id",
                        "target",
                        "authority",
                        "decision",
                        "status",
                        "target_kind",
                        "at",
                        "record_sha256",
                    }
                    or item.get("record_sha256")
                    != sha(record_payload)
                    or not action_id
                    or action_id in known_ids
                    or target_kind not in {"personal_skill", "plugin"}
                    or status
                    not in {
                        "recommended",
                        "kept",
                        "protected",
                        "unknown",
                    }
                ):
                    malformed = True
                    continue
                items.append(
                    {
                        "action_id": action_id,
                        "kind": "recommendation",
                        "target_kind": target_kind,
                        "target": safe_text(item.get("target"), 200),
                        "authority": safe_text(item.get("authority"), 80),
                        "decision": safe_text(item.get("decision"), 80),
                        "status": status,
                        "stale": item.get("stale") is True,
                        "evidence_state": "recommendation",
                        "stale_evidence": [],
                        "receipt_sha256": None,
                        "at": item.get("at"),
                    }
                )
            items.sort(
                key=lambda item: (
                    parse_time(item.get("at")) or 0,
                    item["action_id"],
                ),
                reverse=True,
            )
            local_recovery = state_root / "recovery-required.json"
            paused = curator_state.get("paused") is True
            halted = halt_switch.exists()
            running = any(item["status"] == "running" for item in items)
            recovery_required = (
                recovery_state.exists()
                or local_recovery.exists()
                or any(
                    item["status"] == "recovery_required"
                    for item in items
                )
            )
            stale = any(item["stale"] for item in items)
            status = (
                "invalid"
                if malformed
                else "recovery required"
                if recovery_required
                else "stale"
                if stale
                else "running"
                if running
                else "paused"
                if paused
                else "halted"
                if halted
                else "current"
            )
            return {
                "status": status,
                "available": not malformed,
                "stale": stale,
                "recovery_required": recovery_required,
                "running": running,
                "halted": halted,
                "paused": paused,
                "writers_blocked": (
                    recovery_required or running or halted or paused
                ),
                "total": len(items),
                "items": items,
                "message": (
                    "Estate action state is malformed."
                    if malformed
                    else None
                ),
            }
        except (
            DashboardError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            KeyError,
        ):
            return {
                "status": "invalid",
                "available": False,
                "stale": None,
                "recovery_required": False,
                "running": False,
                "halted": None,
                "paused": None,
                "writers_blocked": True,
                "total": 0,
                "items": [],
                "message": "Estate action state is malformed.",
            }

    @staticmethod
    def _remote_overlay_evaluation_valid(value: Any) -> bool:
        return (
            isinstance(value, dict)
            and set(value)
            == {
                "state",
                "status",
                "current",
                "evaluated_at",
                "receipt_sha256",
                "transition_id",
                "input_manifest_sha256",
                "cases",
            }
            and isinstance(value.get("state"), str)
            and isinstance(value.get("status"), str)
            and isinstance(value.get("current"), bool)
            and (
                value.get("evaluated_at") is None
                or parse_time(value.get("evaluated_at")) is not None
            )
            and (
                value.get("receipt_sha256") is None
                or SHA256_RE.fullmatch(str(value.get("receipt_sha256")))
            )
            and (
                value.get("transition_id") is None
                or CANDIDATE_ID_RE.fullmatch(str(value.get("transition_id")))
            )
            and (
                value.get("input_manifest_sha256") is None
                or CANDIDATE_ID_RE.fullmatch(
                    str(value.get("input_manifest_sha256"))
                )
            )
            and isinstance(value.get("cases"), list)
        )

    def _estate_remote_evaluation(
        self,
        census: dict[str, Any],
        census_receiver: dict[str, Any],
        census_receipt_sha256: str,
        usage: dict[str, Any],
    ) -> dict[str, Any]:
        unavailable = {
            "configured": False,
            "enabled": False,
            "status": "not configured",
            "available": False,
            "report_only": True,
            "origin_host": None,
            "origin_host_id": None,
            "execution_host": "Mac mini",
            "message": "Remote skill evaluation is not configured.",
            "_rows": {},
            "_suppress_all": False,
        }
        try:
            config = self._adapter_config()
            remote = config.get("remote_evaluation_subjects")
            if not isinstance(remote, dict):
                return unavailable
            owner = config.get("evaluation_input_owner")
            configured = {
                **unavailable,
                "configured": True,
                "enabled": remote.get("enabled") is True,
                "status": "waiting for current evaluation view",
                "origin_host": "MacBook",
                "origin_host_id": remote.get("origin_host_id"),
                "message": (
                    "Remote copying is disabled. Existing evidence remains "
                    "read-only."
                    if remote.get("enabled") is not True
                    else "The Mac mini has not published a current evaluation "
                    "view for this census."
                ),
                "_suppress_all": True,
            }
            transport_receiver = remote.get("receiver")
            if (
                remote.get("protocol_version") != 1
                or not isinstance(remote.get("origin_host_id"), str)
                or remote.get("origin_host_id") != census.get("host_id")
                or not isinstance(transport_receiver, dict)
                or set(transport_receiver)
                != {
                    "receiver_id",
                    "receiver_sha256",
                    "collector_sha256",
                    "content_policy_sha256",
                }
                or any(
                    not isinstance(transport_receiver.get(field), str)
                    for field in transport_receiver
                )
                or not isinstance(owner, dict)
                or not isinstance(owner.get("enabled"), bool)
                or (
                    remote.get("enabled") is True
                    and owner.get("enabled") is not True
                )
            ):
                return {
                    **configured,
                    "status": "configuration invalid",
                    "origin_host": None,
                    "origin_host_id": None,
                    "execution_host": None,
                    "message": "Remote evaluation configuration is invalid.",
                }
            configured["_suppress_all"] = False
            pointer_path = (
                self.paths.state / "evaluation-input-overlay-current.json"
            )
            if not pointer_path.is_file() or pointer_path.is_symlink():
                return configured
            pointer = self._json(
                pointer_path, None, "current remote evaluation view"
            )
            pointer_fields = {
                "schema_version",
                "overlay_sha256",
                "census_snapshot_sha256",
                "census_receipt_sha256",
                "usage_snapshot_sha256",
                "usage_receipt_sha256",
                "pointer_sha256",
            }
            pointer_identity = (
                {
                    key: value
                    for key, value in pointer.items()
                    if key != "pointer_sha256"
                }
                if isinstance(pointer, dict)
                else {}
            )
            if (
                not isinstance(pointer, dict)
                or set(pointer) != pointer_fields
                or pointer.get("schema_version") != 1
                or not CANDIDATE_ID_RE.fullmatch(
                    str(pointer.get("overlay_sha256", ""))
                )
                or pointer.get("pointer_sha256") != sha(pointer_identity)
                or pointer.get("census_snapshot_sha256")
                != census.get("snapshot_sha256")
                or pointer.get("census_receipt_sha256")
                != census_receipt_sha256
                or pointer.get("usage_snapshot_sha256")
                != usage.get("_snapshot_sha256")
                or pointer.get("usage_receipt_sha256")
                != usage.get("_receipt_sha256")
            ):
                return {
                    **configured,
                    "status": "current view invalid",
                    "message": (
                        "The retained Mac mini evaluation view does not match "
                        "the current census and usage evidence."
                    ),
                }
            overlay_path = (
                self.paths.state
                / "evaluation-input-overlays"
                / f"{pointer['overlay_sha256'].removeprefix('sha256:')}.json"
            )
            if (
                not overlay_path.is_file()
                or overlay_path.is_symlink()
                or overlay_path.stat().st_size > MAX_JSON_BYTES
            ):
                return {
                    **configured,
                    "status": "current view invalid",
                    "message": "The retained Mac mini evaluation view is unavailable.",
                }
            overlay = self._json(
                overlay_path, None, "remote evaluation view"
            )
            overlay_fields = {
                "schema_version",
                "kind",
                "census_snapshot_sha256",
                "census_receipt_sha256",
                "usage_snapshot_sha256",
                "usage_receipt_sha256",
                "receiver",
                "transport_receiver",
                "origin_host_id",
                "evaluator_sha256",
                "registry_identity",
                "rows",
                "overlay_sha256",
            }
            overlay_identity = (
                {
                    key: value
                    for key, value in overlay.items()
                    if key != "overlay_sha256"
                }
                if isinstance(overlay, dict)
                else {}
            )
            if (
                not isinstance(overlay, dict)
                or set(overlay) != overlay_fields
                or overlay.get("schema_version") != 1
                or overlay.get("kind") != "remote_evaluation_overlay"
                or overlay.get("overlay_sha256") != pointer["overlay_sha256"]
                or overlay.get("overlay_sha256") != sha(overlay_identity)
                or overlay.get("census_snapshot_sha256")
                != pointer["census_snapshot_sha256"]
                or overlay.get("census_receipt_sha256")
                != pointer["census_receipt_sha256"]
                or overlay.get("usage_snapshot_sha256")
                != pointer["usage_snapshot_sha256"]
                or overlay.get("usage_receipt_sha256")
                != pointer["usage_receipt_sha256"]
                or overlay.get("receiver") != census_receiver
                or overlay.get("transport_receiver") != transport_receiver
                or overlay.get("origin_host_id") != remote["origin_host_id"]
                or not CANDIDATE_ID_RE.fullmatch(
                    str(overlay.get("evaluator_sha256", ""))
                )
                or overlay.get("registry_identity")
                != EVALUATION_OVERLAY_REGISTRY_IDENTITY
                or not isinstance(overlay.get("rows"), list)
            ):
                return {
                    **configured,
                    "status": "current view invalid",
                    "message": "The retained Mac mini evaluation view is invalid.",
                }
            physical = {
                item.get("instance_id"): item
                for item in census.get("physical_instances", [])
                if isinstance(item, dict)
                and isinstance(item.get("instance_id"), str)
            }
            enabled_by_capability: dict[str, list[dict[str, Any]]] = {}
            for enabled in census.get("enabled_instances", []):
                if (
                    not isinstance(enabled, dict)
                    or enabled.get("runtime_enabled") is not True
                    or enabled.get("instance_id") not in physical
                ):
                    continue
                enabled_by_capability.setdefault(
                    str(enabled.get("canonical_capability_id")), []
                ).append(physical[enabled["instance_id"]])
            rows: dict[str, dict[str, Any]] = {}
            row_fields = {
                "capability_id",
                "subject_key",
                "origin_host_id",
                "origin_root_id",
                "origin_relative_path",
                "origin_path",
                "canonical_capability_id",
                "origin_inventory_sha256",
                "candidate_id",
                "superseded_candidate_ids",
                "snapshot_state",
                "content_path",
                "transport_receipt_sha256",
                "snapshot_refusal",
                "evaluation",
            }
            for row in overlay["rows"]:
                capability_id = (
                    row.get("capability_id")
                    if isinstance(row, dict)
                    else None
                )
                instances = enabled_by_capability.get(str(capability_id), [])
                instance = instances[0] if len(instances) == 1 else {}
                subject_identity = {
                    "origin_host_id": instance.get("host_id"),
                    "origin_root_id": instance.get("root_id"),
                    "origin_relative_path": instance.get("relative_path"),
                }
                state = row.get("snapshot_state") if isinstance(row, dict) else None
                non_ready = state in {
                    "remote_candidate_not_fetched",
                    "remote_candidate_changed",
                    "remote_candidate_refused",
                }
                if (
                    not isinstance(row, dict)
                    or set(row) != row_fields
                    or not CANDIDATE_ID_RE.fullmatch(str(capability_id))
                    or capability_id in rows
                    or len(instances) != 1
                    or any(
                        not isinstance(value, str) or not value
                        for value in subject_identity.values()
                    )
                    or row.get("canonical_capability_id") != capability_id
                    or row.get("origin_host_id")
                    != subject_identity["origin_host_id"]
                    or row.get("origin_root_id")
                    != subject_identity["origin_root_id"]
                    or row.get("origin_relative_path")
                    != subject_identity["origin_relative_path"]
                    or row.get("origin_path") != instance.get("absolute_path")
                    or row.get("origin_inventory_sha256")
                    != instance.get("inventory_sha256")
                    or row.get("subject_key") != sha(subject_identity)
                    or not isinstance(row.get("superseded_candidate_ids"), list)
                    or not all(
                        CANDIDATE_ID_RE.fullmatch(str(candidate))
                        for candidate in row["superseded_candidate_ids"]
                    )
                    or state
                    not in {
                        "remote_candidate_not_fetched",
                        "remote_candidate_changed",
                        "remote_candidate_snapshot_ready",
                        "remote_candidate_refused",
                    }
                    or (
                        state == "remote_candidate_snapshot_ready"
                        and (
                            not CANDIDATE_ID_RE.fullmatch(
                                str(row.get("candidate_id", ""))
                            )
                            or not isinstance(row.get("content_path"), str)
                            or not CANDIDATE_ID_RE.fullmatch(
                                str(row.get("transport_receipt_sha256", ""))
                            )
                            or not self._remote_overlay_evaluation_valid(
                                row.get("evaluation")
                            )
                        )
                    )
                    or (
                        non_ready
                        and any(
                            row.get(field) is not None
                            for field in (
                                "candidate_id",
                                "content_path",
                                "transport_receipt_sha256",
                                "evaluation",
                            )
                        )
                    )
                    or (
                        state == "remote_candidate_changed"
                        and not row["superseded_candidate_ids"]
                    )
                    or (
                        state == "remote_candidate_not_fetched"
                        and row["superseded_candidate_ids"]
                    )
                    or (
                        state == "remote_candidate_refused"
                        and (
                            not isinstance(row.get("snapshot_refusal"), dict)
                            or set(row["snapshot_refusal"])
                            != {
                                "code", "message",
                                "receipt_sha256", "observed_at",
                            }
                            or not isinstance(
                                row["snapshot_refusal"].get("code"), str
                            )
                            or not isinstance(
                                row["snapshot_refusal"].get("message"), str
                            )
                        )
                    )
                    or (
                        state != "remote_candidate_refused"
                        and row.get("snapshot_refusal") is not None
                    )
                ):
                    return {
                        **configured,
                        "status": "current view invalid",
                        "message": (
                            "The retained Mac mini evaluation view has invalid "
                            "skill identity."
                        ),
                    }
                rows[capability_id] = row
            if set(rows) != set(enabled_by_capability):
                return {
                    **configured,
                    "status": "current view invalid",
                    "message": (
                        "The retained Mac mini evaluation view does not cover "
                        "every enabled skill."
                    ),
                }
            return {
                **configured,
                "status": "current",
                "available": True,
                "message": (
                    "Exact copies, when available, are evaluated on the Mac "
                    "mini. This dashboard cannot change skills or plugins."
                ),
                "_rows": rows,
            }
        except (DashboardError, OSError, TypeError, ValueError):
            return {
                **unavailable,
                "configured": True,
                "status": "current view invalid",
                "message": "Remote evaluation evidence is malformed.",
                "_suppress_all": True,
            }

    @staticmethod
    def _remote_refusal_reason(code: Any) -> str:
        reasons = {
            "remote-candidate-content-unsafe": (
                "The skill contains content that cannot be copied safely."
            ),
            "remote-candidate-fetch-timeout": "Copying the skill timed out.",
            "remote-candidate-fetch-oversized": (
                "The skill copy exceeded the configured size limit."
            ),
            "remote-candidate-store-full": (
                "The evaluation computer does not have enough protected "
                "storage for this copy."
            ),
            "remote-candidate-fetch-failed": (
                "The origin computer refused or could not provide a safe copy."
            ),
        }
        return reasons.get(
            code,
            "The skill could not be copied safely.",
        )

    @staticmethod
    def _apply_remote_evaluation(
        physical: list[dict[str, Any]], remote: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if not remote.get("configured"):
            return physical
        origin_host_id = remote.get("origin_host_id")
        rows = remote.get("_rows", {})
        sanitized = []
        for raw in physical:
            item = dict(raw)
            if (
                not remote.get("_suppress_all")
                and item.get("host_id") != origin_host_id
            ):
                sanitized.append(item)
                continue
            item.pop("evaluation", None)
            item["evaluation_complete"] = None
            capability_id = item.get("canonical_capability_id")
            row = rows.get(capability_id) if isinstance(rows, dict) else None
            state = (
                row.get("snapshot_state")
                if isinstance(row, dict)
                else "remote_candidate_state_unavailable"
            )
            item["remote_evaluation"] = {
                "origin_host": remote.get("origin_host"),
                "origin_host_id": origin_host_id,
                "execution_host": remote.get("execution_host"),
                "subject_key": (
                    row.get("subject_key") if isinstance(row, dict) else None
                ),
                "snapshot_state": state,
                "refusal_reason": (
                    DashboardData._remote_refusal_reason(
                        row.get("snapshot_refusal", {}).get("code")
                    )
                    if isinstance(row, dict)
                    and state == "remote_candidate_refused"
                    else None
                ),
            }
            if (
                isinstance(row, dict)
                and state == "remote_candidate_snapshot_ready"
            ):
                item["evaluation"] = row["evaluation"]
                item["evaluation_complete"] = True
            sanitized.append(item)
        return sanitized

    def estate(self, summary_only: bool = False) -> dict[str, Any]:
        current_path = self.paths.state / "estate-census-current.json"
        recovery_path = self.paths.state / "estate-recovery-required.json"
        actions = self._estate_actions()
        if not current_path.exists():
            return {
                "status": "unavailable",
                "available": False,
                "complete": None,
                "fresh": None,
                "collected_at": None,
                "totals": None,
                "recovery_required": recovery_path.exists(),
                "actions": actions,
                "message": "No estate census has been recorded.",
                "portfolio_decisions": [],
            }
        try:
            current = self._json(
                current_path, None, "current estate census"
            )
            if (
                not isinstance(current, dict)
                or current.get("schema_version") != 1
                or not CANDIDATE_ID_RE.fullmatch(
                    str(current.get("receipt_sha256", ""))
                )
                or not CANDIDATE_ID_RE.fullmatch(
                    str(current.get("snapshot_sha256", ""))
                )
                or not isinstance(current.get("census"), dict)
            ):
                raise DashboardError(
                    503,
                    "estate_invalid",
                    "Current estate census is malformed",
                    ["current estate census"],
                )
            census = current["census"]
            snapshot_sha256 = census.get("snapshot_sha256")
            snapshot = {
                key: value
                for key, value in census.items()
                if key != "snapshot_sha256"
            }
            if (
                census.get("schema_version") != 1
                or snapshot_sha256 != current["snapshot_sha256"]
                or sha(snapshot) != snapshot_sha256
            ):
                raise DashboardError(
                    503,
                    "estate_invalid",
                    "Estate census identity is invalid",
                    ["current estate census"],
                )
            receipt_path = (
                self.paths.state
                / "estate-census-receipts"
                / f"{current['receipt_sha256'].removeprefix('sha256:')}.json"
            )
            if (
                not receipt_path.is_file()
                or receipt_path.is_symlink()
                or receipt_path.stat().st_size > MAX_JSON_BYTES
            ):
                raise DashboardError(
                    503,
                    "estate_invalid",
                    "Estate census receipt is unavailable",
                    ["estate census receipt"],
                )
            receipt = self._json(
                receipt_path, None, "estate census receipt"
            )
            receiver = (
                receipt.get("receiver")
                if isinstance(receipt, dict)
                else None
            )
            if (
                not isinstance(receipt, dict)
                or set(receipt)
                != {"schema_version", "snapshot_sha256", "receiver", "census"}
                or receipt.get("schema_version") != 1
                or receipt.get("snapshot_sha256") != snapshot_sha256
                or receipt.get("census") != census
                or sha(receipt) != current["receipt_sha256"]
                or not isinstance(receiver, dict)
                or set(receiver)
                != {
                    "receiver_id",
                    "receiver_sha256",
                    "collector_sha256",
                }
                or not isinstance(receiver.get("receiver_id"), str)
                or not re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(receiver.get("receiver_sha256", "")),
                )
                or not re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(receiver.get("collector_sha256", "")),
                )
            ):
                raise DashboardError(
                    503,
                    "estate_invalid",
                    "Estate census receipt is invalid",
                    ["estate census receipt"],
                )
        except DashboardError as error:
            return {
                "status": "invalid",
                "available": False,
                "complete": None,
                "fresh": None,
                "collected_at": None,
                "totals": None,
                "recovery_required": recovery_path.exists(),
                "actions": actions,
                "message": error.message,
                "portfolio_decisions": [],
            }

        collected_at = census.get("collected_at")
        collected_epoch = parse_time(collected_at)
        fresh = (
            collected_epoch is not None
            and time.time() - collected_epoch <= 24 * 60 * 60
        )
        complete = census.get("scope", {}).get("complete") is True
        recovery_required = (
            recovery_path.exists() or actions["recovery_required"]
        )
        status = (
            "recovery required"
            if recovery_required
            else "invalid"
            if actions["status"] == "invalid"
            else "incomplete"
            if not complete
            else "stale"
            if not fresh or actions["status"] == "stale"
            else "current"
        )
        base = {
            "status": status,
            "available": True,
            "complete": complete,
            "fresh": fresh,
            "collected_at": collected_at,
            "snapshot_sha256": snapshot_sha256,
            "receipt_sha256": current["receipt_sha256"],
            "receiver": {
                "id": safe_text(receiver["receiver_id"], 200),
                "receiver_sha256": receiver["receiver_sha256"],
                "collector_sha256": receiver["collector_sha256"],
            },
            "settings_sha256": (
                census.get("evidence", {}).get("settings_sha256")
                if re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(
                        census.get("evidence", {}).get(
                            "settings_sha256", ""
                        )
                    ),
                )
                else None
            ),
            "totals": census.get("totals")
            if isinstance(census.get("totals"), dict)
            else None,
            "scope": {
                "label": safe_text(census.get("scope", {}).get("label"), 200),
                "registered_context_ids": [
                    safe_text(value, 200)
                    for value in census.get("scope", {}).get(
                        "registered_context_ids", []
                    )
                    if isinstance(value, str)
                ],
                "outside_context_ids": [
                    safe_text(value, 200)
                    for value in census.get("scope", {}).get(
                        "outside_context_ids", []
                    )
                    if isinstance(value, str)
                ],
            },
            "recovery_required": recovery_required,
            "actions": actions,
            "read_only": True,
            "authorizes_actions": False,
        }
        if summary_only:
            return base

        authority = {name: 0 for name in sorted(ESTATE_AUTHORITIES)}
        root_classes = {name: 0 for name in sorted(ESTATE_ROOT_CLASSES)}
        physical_instances: list[dict[str, Any]] = []

        def latest_action(
            target: str | None, prefix: str
        ) -> dict[str, Any] | None:
            if not target:
                return None
            return next(
                (
                    item
                    for item in actions["items"]
                    if item.get("target") == target
                    and (
                        str(item.get("kind", "")).startswith(prefix)
                        or (
                            item.get("kind") == "recommendation"
                            and item.get("target_kind")
                            == (
                                "personal_skill"
                                if prefix == "personal_"
                                else "plugin"
                            )
                        )
                    )
                ),
                None,
            )

        usage = self._estate_usage(census, receiver)
        remote_evaluation = self._estate_remote_evaluation(
            census,
            receiver,
            current["receipt_sha256"],
            usage,
        )
        source_physical = self._apply_remote_evaluation(
            [
                item
                for item in census.get("physical_instances", [])
                if isinstance(item, dict)
            ],
            remote_evaluation,
        )
        for item in source_physical:
            authority_name = safe_text(item.get("authority"), 80)
            root_class = safe_text(item.get("root_class"), 80)
            if authority_name in authority:
                authority[authority_name] += 1
            if root_class in root_classes:
                root_classes[root_class] += 1
            owner = item.get("owner")
            if isinstance(owner, str) and owner.startswith("/"):
                owner = "local configured root"
            package = item.get("package")
            source = item.get("source_identity")
            if not isinstance(source, str) and isinstance(package, dict):
                source = package.get("source_identity") or package.get(
                    "plugin_id"
                )
            if not isinstance(source, str):
                source = root_class
            skill_name = safe_text(item.get("skill_name"), 200)
            provenance = item.get("provenance")
            provenance_status = (
                provenance.get("status")
                if isinstance(provenance, dict)
                and provenance.get("status")
                in {"verified", "protected", "insufficient", "invalid"}
                else "unknown"
            )
            decision = latest_action(skill_name, "personal_")
            physical_instances.append(
                {
                    "skill_name": skill_name,
                    "root_class": root_class,
                    "authority": authority_name,
                    "physical_only": item.get("physical_only") is True,
                    "effective_state": (
                        "physical only"
                        if item.get("physical_only") is True
                        else "enabled"
                    ),
                    "owner": safe_text(owner, 200),
                    "source": safe_text(source, 200),
                    "provenance_status": provenance_status,
                    "evaluation_complete": (
                        item.get("evaluation_complete")
                        if isinstance(item.get("evaluation_complete"), bool)
                        else None
                    ),
                    "evaluation": self._portfolio_evaluation(
                        item.get("evaluation", item.get("evaluation_state")),
                        item.get("evaluation_complete"),
                    ),
                    "usage_complete": (
                        item.get("usage_complete")
                        if isinstance(item.get("usage_complete"), bool)
                        else None
                    ),
                    "dependencies_complete": (
                        item.get("dependencies_complete")
                        if isinstance(
                            item.get("dependencies_complete"), bool
                        )
                        else None
                    ),
                    "dependencies": self._portfolio_dependency_fact(
                        item.get("dependencies")
                    ),
                    "latest_decision": (
                        {
                            "decision": decision["decision"],
                            "status": decision["status"],
                            "receipt_sha256": decision[
                                "receipt_sha256"
                            ],
                        }
                        if decision
                        else None
                    ),
                    "instance_id": safe_text(item.get("instance_id"), 80),
                    "canonical_capability_id": safe_text(
                        item.get("canonical_capability_id"), 80
                    ),
                    "remote_evaluation": (
                        {
                            "origin_host": safe_text(
                                item["remote_evaluation"].get("origin_host"), 80
                            ),
                            "execution_host": safe_text(
                                item["remote_evaluation"].get("execution_host"), 80
                            ),
                            "snapshot_state": safe_text(
                                item["remote_evaluation"].get("snapshot_state"),
                                80,
                            ),
                        }
                        if isinstance(item.get("remote_evaluation"), dict)
                        else None
                    ),
                }
            )
        declared_authority = census.get("authority_counts")
        if (
            isinstance(declared_authority, dict)
            and set(declared_authority) == ESTATE_AUTHORITIES
            and all(
                isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
                for value in declared_authority.values()
            )
            and sum(declared_authority.values()) == len(physical_instances)
        ):
            authority = {
                name: declared_authority[name]
                for name in sorted(ESTATE_AUTHORITIES)
            }
        declared_root_classes = census.get("root_class_counts")
        if (
            isinstance(declared_root_classes, dict)
            and set(declared_root_classes) == ESTATE_ROOT_CLASSES
            and all(
                isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
                for value in declared_root_classes.values()
            )
            and sum(declared_root_classes.values()) == len(physical_instances)
        ):
            root_classes = {
                name: declared_root_classes[name]
                for name in sorted(ESTATE_ROOT_CLASSES)
            }
        contexts = [
            {
                "id": safe_text(item.get("id"), 200),
                "kind": safe_text(item.get("kind"), 40),
                "registered": item.get("registered") is True,
                "inside_completeness_claim": (
                    item.get("inside_completeness_claim") is True
                ),
                "complete": item.get("complete"),
                "runtime_skill_count": item.get("runtime_skill_count"),
                "mapped_skill_count": item.get("mapped_skill_count"),
                "unresolved_count": item.get("unresolved_count"),
            }
            for item in census.get("contexts", [])
            if isinstance(item, dict)
        ]
        unresolved = [
            {
                "context_id": safe_text(item.get("context_id"), 200),
                "runtime_name": safe_text(item.get("runtime_name"), 200),
                "runtime_source": safe_text(item.get("runtime_source"), 80),
                "reason": safe_text(item.get("reason"), 80),
            }
            for item in census.get("unresolved_mappings", [])
            if isinstance(item, dict)
        ]
        plugins = []
        for plugin in census.get("plugins", []):
            if not isinstance(plugin, dict):
                continue
            capabilities = plugin.get("capabilities")
            plugin_id = safe_text(plugin.get("plugin_id"), 200)
            decision = latest_action(plugin_id, "plugin_")
            capability_counts = None
            if isinstance(capabilities, dict):
                capability_counts = {
                    name: len(capabilities.get(name, []))
                    for name in (
                        "skills",
                        "agents",
                        "hooks",
                        "mcp_servers",
                        "lsp_servers",
                    )
                    if isinstance(capabilities.get(name, []), list)
                }
            plugins.append(
                {
                    "plugin_id": plugin_id,
                    "name": safe_text(plugin.get("name"), 200),
                    "version": safe_text(plugin.get("version"), 80),
                    "source_identity": safe_text(
                        plugin.get("source_identity"), 200
                    ),
                    "enabled": plugin.get("enabled") is True,
                    "capability_inventory_complete": (
                        isinstance(capabilities, dict)
                        and capabilities.get("complete") is True
                    ),
                    "capability_counts": capability_counts,
                    "latest_decision": (
                        {
                            "decision": decision["decision"],
                            "kind": decision["kind"],
                            "status": decision["status"],
                            "evidence_state": decision[
                                "evidence_state"
                            ],
                            "receipt_sha256": decision[
                                "receipt_sha256"
                            ],
                        }
                        if decision
                        else None
                    ),
                }
            )

        usage_by_capability = {
            item["canonical_capability_id"]: item
            for item in usage.get("canonical_usage", [])
            if isinstance(item, dict)
        }
        physical_by_instance = {
            item["instance_id"]: item
            for item in physical_instances
            if item["instance_id"]
        }
        enabled_by_capability: dict[str, list[dict[str, Any]]] = {}
        for item in census.get("enabled_instances", []):
            if not isinstance(item, dict) or item.get("runtime_enabled") is not True:
                continue
            capability_id = item.get("canonical_capability_id")
            if not isinstance(capability_id, str):
                continue
            enabled_by_capability.setdefault(capability_id, []).append(item)

        enabled_skills = []
        usage_state = (
            "unavailable"
            if not usage["available"]
            else "complete"
            if usage["complete"]
            else "incomplete"
        )
        represented_instance_ids: set[str] = set()
        for capability_id, mappings in sorted(
            enabled_by_capability.items(),
            key=lambda value: safe_text(value[1][0].get("runtime_name"), 200),
        ):
            representative = next(
                (
                    physical_by_instance.get(safe_text(mapping.get("instance_id"), 80))
                    for mapping in mappings
                    if physical_by_instance.get(
                        safe_text(mapping.get("instance_id"), 80)
                    )
                ),
                None,
            )
            if representative is None:
                continue
            represented_instance_ids.add(representative["instance_id"])
            usage_row = usage_by_capability.get(capability_id)
            row_usage_state = usage_state if usage_row else "unavailable"
            skill_name = safe_text(mappings[0].get("runtime_name"), 200)
            decision_prefix = (
                "plugin_"
                if representative["root_class"] == "plugin"
                else "personal_"
            )
            decision_target = (
                representative["owner"]
                if decision_prefix == "plugin_"
                else skill_name
            )
            decision = latest_action(decision_target, decision_prefix)
            enabled_skills.append(
                {
                    "skill_name": skill_name,
                    "canonical_capability_id": capability_id,
                    "source": representative["source"],
                    "root_class": representative["root_class"],
                    "authority": representative["authority"],
                    "provenance_status": representative["provenance_status"],
                    "state": "enabled",
                    "usage_state": row_usage_state,
                    "uses_7d": usage_row.get("uses_7d") if usage_row else None,
                    "uses_30d": usage_row.get("uses_30d") if usage_row else None,
                    "uses_90d": usage_row.get("uses_90d") if usage_row else None,
                    "uses_total": usage_row.get("uses_total") if usage_row else None,
                    "last_successful_invocation": (
                        usage_row.get("last_successful_invocation")
                        if usage_row
                        else None
                    ),
                    "latest_decision": (
                        {
                            "decision": decision["decision"],
                            "status": decision["status"],
                            "receipt_sha256": decision["receipt_sha256"],
                        }
                        if decision
                        else None
                    ),
                }
            )
        disabled_instance_ids = {
            safe_text(item.get("instance_id"), 80)
            for item in census.get("enabled_instances", [])
            if isinstance(item, dict) and item.get("runtime_enabled") is not True
        }
        other_physical_copies = []
        for item in physical_instances:
            if item["instance_id"] in represented_instance_ids:
                continue
            if item["instance_id"] in disabled_instance_ids:
                reason = "installed but disabled in the current runtime"
            elif item["physical_only"]:
                reason = "installed copy not selected by the current runtime"
            else:
                reason = "additional installed copy of an enabled capability"
            other_physical_copies.append({**item, "reason": reason})
        portfolio_decisions = self._portfolio_decisions(
            enabled_by_capability,
            physical_by_instance,
            usage_by_capability,
            usage,
            enabled_skills,
        )
        evaluation_queue = [
            item
            for item in portfolio_decisions
            if item.get("evaluation_queue_position") is not None
        ]
        return {
            **base,
            "usage": {
                key: value
                for key, value in usage.items()
                if key
                not in {
                    "canonical_usage",
                    "_receipt_sha256",
                    "_snapshot_sha256",
                    "_pending",
                    "_failures",
                    "_unattributed",
                }
            },
            "remote_evaluation": {
                key: value
                for key, value in remote_evaluation.items()
                if not key.startswith("_") and key != "origin_host_id"
            },
            "authority_counts": dict(sorted(authority.items())),
            "root_class_counts": dict(sorted(root_classes.items())),
            "contexts": contexts,
            "unresolved_mappings": unresolved,
            "plugins": plugins,
            "enabled_skills": enabled_skills,
            "portfolio_decisions": portfolio_decisions,
            "evaluation_queue": {
                "queued": len(evaluation_queue),
                "current": sum(
                    item["evaluation"].get("current") is True
                    for item in portfolio_decisions
                ),
                "missing": sum(
                    item["evaluation"].get("state")
                    in {"missing", "input_missing"}
                    for item in portfolio_decisions
                ),
                "drafting": sum(
                    item["evaluation"].get("state") == "drafting"
                    for item in portfolio_decisions
                ),
                "review_required": sum(
                    item["evaluation"].get("state") == "review_required"
                    for item in portfolio_decisions
                ),
                "insufficient_information": sum(
                    item["evaluation"].get("state")
                    == "insufficient_information"
                    for item in portfolio_decisions
                ),
                "ready": sum(
                    item["evaluation"].get("state") == "ready"
                    for item in portfolio_decisions
                ),
                "stale": sum(
                    item["evaluation"].get("state") == "stale"
                    for item in portfolio_decisions
                ),
                "invalid": sum(
                    item["evaluation"].get("state") == "invalid"
                    for item in portfolio_decisions
                ),
            },
            "other_physical_copies": other_physical_copies,
            "decisions": actions["items"],
        }

    def _portfolio_decisions(
        self,
        enabled_by_capability: dict[str, list[dict[str, Any]]],
        physical_by_instance: dict[str, dict[str, Any]],
        usage_by_capability: dict[str, dict[str, Any]],
        usage: dict[str, Any],
        enabled_skills: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Project retained estate facts into one non-authorizing value judgment."""
        skills_by_capability = {
            item["canonical_capability_id"]: item
            for item in enabled_skills
            if isinstance(item.get("canonical_capability_id"), str)
        }
        rows = []
        for capability_id, mappings in enabled_by_capability.items():
            representative = next(
                (
                    physical_by_instance.get(safe_text(mapping.get("instance_id"), 80))
                    for mapping in mappings
                    if physical_by_instance.get(
                        safe_text(mapping.get("instance_id"), 80)
                    )
                ),
                None,
            )
            enabled = skills_by_capability.get(capability_id, {})
            raw_evaluation = self._portfolio_fact(
                "evaluation", "evaluation_state", mappings, representative
            )
            evaluation_complete = self._portfolio_fact(
                "evaluation_complete", None, mappings, representative
            )
            evaluation = self._portfolio_evaluation(
                raw_evaluation, evaluation_complete
            )
            raw_dependencies = self._portfolio_fact(
                "dependencies", "dependency_state", mappings, representative
            )
            dependency_complete = self._portfolio_fact(
                "dependencies_complete", None, mappings, representative
            )
            dependencies = self._portfolio_dependencies(
                raw_dependencies, dependency_complete
            )
            usage_row = usage_by_capability.get(capability_id)
            usage_available = usage.get("available") is True and usage_row is not None
            decision_coverage = self._portfolio_usage_coverage(
                capability_id,
                usage,
                usage_row,
            )
            usage_state = decision_coverage["state"]
            uses_7d = usage_row.get("uses_7d") if usage_available else None
            uses_30d = usage_row.get("uses_30d") if usage_available else None
            uses_90d = usage_row.get("uses_90d") if usage_available else None
            last_used = (
                usage_row.get("last_successful_invocation")
                if usage_available
                else None
            )
            recommendation, why = self._portfolio_recommendation(
                evaluation["state"], usage_state, uses_30d
            )
            authority = (
                representative.get("authority")
                if representative is not None
                else mappings[0].get("authority")
            )
            who_may_change = self._portfolio_authority(authority)
            next_action = {
                "disable_candidate": "Disable",
                "proven_useful": "Keep",
                "used_evaluation_missing": "Run evaluation",
                "evaluate_now": "Run evaluation",
                "insufficient_information": "Gather information",
            }[recommendation]
            skill_name = safe_text(
                enabled.get("skill_name") or mappings[0].get("runtime_name"), 200
            )
            source = safe_text(
                enabled.get("source")
                or (representative or {}).get("source")
                or "Unknown installation",
                200,
            )
            rows.append(
                {
                    "canonical_capability_id": capability_id,
                    "skill_name": skill_name,
                    "installed_from": source,
                    "recommendation": recommendation,
                    "recommendation_label": {
                        "disable_candidate": "Disable candidate",
                        "proven_useful": "Proven useful",
                        "used_evaluation_missing": "Used; evaluation needed",
                        "evaluate_now": "Evaluate now",
                        "insufficient_information": "Insufficient information",
                    }[recommendation],
                    "why": why,
                    "evaluation": evaluation,
                    "uses_7d": uses_7d,
                    "uses_30d": uses_30d,
                    "uses_90d": uses_90d,
                    "last_successful_invocation": last_used,
                    "usage_state": usage_state,
                    "decision_coverage": decision_coverage,
                    "dependencies": dependencies,
                    "who_may_change": who_may_change,
                    "remote_evaluation": (
                        representative.get("remote_evaluation")
                        if representative is not None
                        else None
                    ),
                    "next_action": {
                        "label": next_action,
                        "enabled": False,
                        "reason": "Unavailable: this preview is read-only.",
                    },
                    "preview_read_only": self.paths.preview_root is not None,
                    "_evaluation_priority": self._portfolio_evaluation_priority(
                        evaluation,
                        usage_state,
                        enabled.get("root_class")
                        or (representative or {}).get("root_class"),
                    ),
                }
            )
        priority = {
            "disable_candidate": 0,
            "evaluate_now": 1,
            "used_evaluation_missing": 2,
            "insufficient_information": 3,
            "proven_useful": 4,
        }
        ordered = sorted(
            rows,
            key=lambda item: (
                (
                    item["_evaluation_priority"][0]
                    if item["_evaluation_priority"] is not None
                    else 99
                ),
                priority[item["recommendation"]],
                item["skill_name"].casefold(),
                item["canonical_capability_id"],
            ),
        )
        queue_position = 0
        for item in ordered:
            evaluation_priority = item.pop("_evaluation_priority")
            if evaluation_priority is None:
                item["evaluation_queue_position"] = None
                item["evaluation_queue_reason"] = None
                continue
            queue_position += 1
            item["evaluation_queue_position"] = queue_position
            item["evaluation_queue_reason"] = evaluation_priority[1]
        return ordered

    @staticmethod
    def _portfolio_fact(
        primary: str,
        alternate: str | None,
        mappings: list[dict[str, Any]],
        representative: dict[str, Any] | None,
    ) -> Any:
        for item in ([representative] if representative is not None else []) + mappings:
            if not isinstance(item, dict):
                continue
            if primary in item:
                return item[primary]
            if alternate is not None and alternate in item:
                return item[alternate]
        return None

    @staticmethod
    def _portfolio_evaluation_case(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or set(value) != {
            "executor",
            "case_id",
            "evaluation_class",
            "candidate_valid_trials",
            "candidate_successful_trials",
            "control_valid_trials",
            "control_successful_trials",
            "comparable",
            "exclusion_reason",
        }:
            return None
        for field in ("executor", "case_id", "evaluation_class"):
            if not isinstance(value[field], str) or not value[field]:
                return None
        for field in (
            "candidate_valid_trials",
            "candidate_successful_trials",
            "control_valid_trials",
            "control_successful_trials",
        ):
            count = value[field]
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or not 0 <= count <= 3
            ):
                return None
        if not isinstance(value["comparable"], bool) or not (
            value["exclusion_reason"] is None
            or isinstance(value["exclusion_reason"], str)
        ):
            return None
        return {
            "executor": safe_text(value["executor"], 80),
            "case_id": safe_text(value["case_id"], 200),
            "evaluation_class": safe_text(value["evaluation_class"], 80),
            "candidate_valid_trials": value["candidate_valid_trials"],
            "candidate_successful_trials": value[
                "candidate_successful_trials"
            ],
            "control_valid_trials": value["control_valid_trials"],
            "control_successful_trials": value[
                "control_successful_trials"
            ],
            "comparable": value["comparable"],
            "exclusion_reason": safe_text(value["exclusion_reason"], 80),
        }

    @staticmethod
    def _portfolio_evaluation(
        value: Any, complete: Any = None
    ) -> dict[str, Any]:
        receipt_sha256 = None
        transition_id = None
        input_manifest_sha256 = None
        evaluated_at = None
        cases: list[dict[str, Any]] = []
        current = not isinstance(value, dict)
        historical_status = None
        if isinstance(value, dict):
            current = value.get("current") is True
            historical_status = safe_text(value.get("status"), 80).casefold()
            evaluated_at = safe_text(value.get("evaluated_at"), 80) or None
            receipt = value.get("receipt_sha256")
            if isinstance(receipt, str) and SHA256_RE.fullmatch(receipt):
                receipt_sha256 = receipt
            transition = value.get("transition_id")
            if (
                isinstance(transition, str)
                and transition.startswith("sha256:")
                and SHA256_RE.fullmatch(transition.removeprefix("sha256:"))
            ):
                transition_id = transition
            manifest = value.get("input_manifest_sha256")
            if (
                isinstance(manifest, str)
                and manifest.startswith("sha256:")
                and SHA256_RE.fullmatch(manifest.removeprefix("sha256:"))
            ):
                input_manifest_sha256 = manifest
            raw_cases = value.get("cases")
            if not isinstance(raw_cases, list) or len(raw_cases) > 100:
                complete = False
            else:
                for item in raw_cases[:100]:
                    projected = DashboardData._portfolio_evaluation_case(item)
                    if projected is None:
                        complete = False
                        cases = []
                        break
                    cases.append(projected)
            value = value.get("state") or value.get("status")
        normalized = safe_text(value, 80).casefold().replace("-", "_").replace(" ", "_")
        invalid_readiness_reasons = {
            "deterministic_validation_failed",
            "independent_review_rejected",
            "authoring_budget_exhausted",
        }
        if complete is False or normalized == "incomplete":
            state, label, current = "invalid", "Evaluation data invalid", False
        elif normalized in {"input_missing", "missing", "not_evaluated", ""}:
            state, label, current = "input_missing", "Needs test cases", False
        elif normalized == "drafting":
            state, label, current = "drafting", "Test design in progress", False
        elif normalized == "review_required":
            state, label, current = (
                "review_required",
                "Test design in progress",
                False,
            )
        elif normalized == "insufficient_information":
            state, label, current = (
                "insufficient_information",
                "Cannot test safely",
                False,
            )
        elif normalized == "ready":
            state, label, current = "ready", "Ready to test", False
        elif normalized in {"executing", "running"}:
            state, label, current = "executing", "Testing now", False
        elif normalized == "invalid":
            state, current = "invalid", False
            label = (
                "Test design rejected"
                if historical_status in invalid_readiness_reasons
                else "Evaluation data invalid"
            )
        elif normalized in {"stale", "expired", "revoked"} or not current:
            state, label, current = "stale", "Stale evaluation", False
        elif normalized in {
            "regression",
            "fail",
            "failed",
            "critical_regression",
        }:
            state, label = "regression", "Current regression"
        elif normalized in {"pass", "passed", "current_pass", "waived"}:
            state, label = "pass", "Current pass"
        elif normalized in {"inconclusive", "queued"}:
            state = normalized
            label = normalized.replace("_", " ").title()
        else:
            state, label, current = "invalid", "Evaluation data invalid", False
        return {
            "state": state,
            "status": historical_status or normalized or "missing",
            "label": label,
            "current": current,
            "evaluated_at": evaluated_at,
            "receipt_sha256": receipt_sha256,
            "transition_id": transition_id,
            "input_manifest_sha256": input_manifest_sha256,
            "cases": cases,
        }

    @staticmethod
    def _portfolio_evaluation_priority(
        evaluation: dict[str, Any],
        usage_state: str,
        root_class: Any,
    ) -> tuple[int, str] | None:
        if usage_state in {"complete_zero_30d", "settled_zero_30d"}:
            return (1, "No successful use in 30 days")
        state = evaluation.get("state")
        if state in {
            "missing",
            "input_missing",
            "drafting",
            "review_required",
            "insufficient_information",
            "ready",
            "executing",
            "inconclusive",
            "invalid",
        }:
            return (2, "No current conclusive evaluation")
        if state == "regression":
            return (4, "Current evaluation found a regression")
        if root_class == "plugin":
            return (5, "Plugin capability needs package-level judgment")
        if state == "stale":
            return (
                6 if evaluation.get("status") == "pass" else 2,
                "Passing evaluation is stale"
                if evaluation.get("status") == "pass"
                else "No current conclusive evaluation",
            )
        return None

    @staticmethod
    def _portfolio_dependency_fact(value: Any) -> Any:
        if not isinstance(value, dict):
            return {
                "state": "incomplete",
                "complete": False,
                "blockers": [],
                "installed_content_consumers": [],
            }
        blockers = value.get("blockers")
        installed_consumers = value.get("installed_content_consumers")
        blockers_valid = isinstance(blockers, list) and all(
            isinstance(item, dict)
            and isinstance(item.get("kind"), str)
            and item.get("kind")
            and isinstance(item.get("source_skill"), str)
            and item.get("source_skill")
            for item in blockers
        )
        consumers_valid = isinstance(installed_consumers, list) and all(
            isinstance(item, dict)
            and isinstance(item.get("kind"), str)
            and item.get("kind")
            and isinstance(item.get("source_skill"), str)
            and item.get("source_skill")
            for item in installed_consumers
        )
        state = safe_text(value.get("state"), 80).casefold()
        valid_state = state in {"protected", "clear", "incomplete"}
        complete = (
            value.get("complete") is True
            and blockers_valid
            and consumers_valid
            and valid_state
        )
        return {
            "state": state if valid_state else "incomplete",
            "complete": complete,
            "blockers": [
                {
                    "kind": safe_text(item.get("kind"), 80),
                    "source_skill": safe_text(item.get("source_skill"), 200),
                }
                for item in blockers[:100]
                if isinstance(item, dict) and item.get("source_skill")
            ] if blockers_valid else [],
            "installed_content_consumers": [
                {
                    "kind": safe_text(item.get("kind"), 80),
                    "source_skill": safe_text(item.get("source_skill"), 200),
                }
                for item in installed_consumers[:100]
                if isinstance(item, dict) and item.get("source_skill")
            ] if consumers_valid else [],
        }

    @staticmethod
    def _portfolio_dependencies(value: Any, complete: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            state = safe_text(value.get("state") or value.get("status"), 80).casefold()
            blockers = value.get("blockers") or value.get("dependencies")
            installed_consumers = value.get("installed_content_consumers")
            blockers_valid = blockers is None or (
                isinstance(blockers, list)
                and all(
                    isinstance(item, dict)
                    and isinstance(item.get("kind"), str)
                    and item.get("kind")
                    and isinstance(item.get("source_skill"), str)
                    and item.get("source_skill")
                    for item in blockers
                )
            )
            consumers_valid = installed_consumers is None or (
                isinstance(installed_consumers, list)
                and all(
                    isinstance(item, dict)
                    and isinstance(item.get("kind"), str)
                    and item.get("kind")
                    and isinstance(item.get("source_skill"), str)
                    and item.get("source_skill")
                    for item in installed_consumers
                )
            )
            evidence_complete = (
                value.get("complete") is True
                and blockers_valid
                and consumers_valid
            )
            required_by = sorted(
                {
                    safe_text(item.get("source_skill"), 200)
                    for item in blockers
                    if isinstance(item, dict)
                    and isinstance(item.get("kind"), str)
                    and item.get("kind")
                    and isinstance(item.get("source_skill"), str)
                    and item.get("source_skill")
                }
            ) if isinstance(blockers, list) else []
            files_used_by = sorted(
                {
                    safe_text(item.get("source_skill"), 200)
                    for item in installed_consumers
                    if isinstance(item, dict)
                    and isinstance(item.get("kind"), str)
                    and item.get("kind")
                    and isinstance(item.get("source_skill"), str)
                    and item.get("source_skill")
                }
            ) if isinstance(installed_consumers, list) else []
            if isinstance(blockers, list) and blockers:
                return {
                    "state": "protected",
                    "label": "Protected",
                    "complete": evidence_complete,
                    "required_by": required_by,
                    "files_used_by": files_used_by,
                }
            if state in {"protected", "required", "blocked"}:
                return {
                    "state": "protected",
                    "label": "Protected",
                    "complete": evidence_complete,
                    "required_by": required_by,
                    "files_used_by": files_used_by,
                }
            if state in {"clear", "none"} and evidence_complete:
                return {
                    "state": "clear",
                    "label": "Clear",
                    "complete": True,
                    "required_by": [],
                    "files_used_by": files_used_by,
                }
            if state == "incomplete" or value.get("complete") is False:
                return {
                    "state": "incomplete",
                    "label": "Incomplete",
                    "complete": False,
                    "required_by": required_by,
                    "files_used_by": files_used_by,
                }
            return {
                "state": "incomplete",
                "label": "Incomplete",
                "complete": False,
                "required_by": [],
                "files_used_by": files_used_by,
            }
        if isinstance(value, list):
            return {
                "state": "protected" if value else "incomplete",
                "label": "Protected" if value else "Incomplete",
                "complete": False,
                "required_by": [],
                "files_used_by": [],
            }
        if isinstance(value, str) and value in {"protected", "required", "blocked"}:
            return {
                "state": "protected",
                "label": "Protected",
                "complete": complete is True,
                "required_by": [],
                "files_used_by": [],
            }
        if isinstance(value, str) and value in {"clear", "none"} and complete is True:
            return {
                "state": "clear",
                "label": "Clear",
                "complete": True,
                "required_by": [],
                "files_used_by": [],
            }
        return {
            "state": "incomplete" if complete is False else "unknown",
            "label": "Incomplete" if complete is False else "Unknown",
            "complete": False,
            "required_by": [],
            "files_used_by": [],
        }

    @staticmethod
    def _portfolio_recommendation(
        evaluation: str, usage_state: str, uses_30d: Any
    ) -> tuple[str, str]:
        if evaluation == "regression":
            return "disable_candidate", "A current evaluation found a regression."
        verified_recent_use = isinstance(uses_30d, int) and uses_30d > 0
        if evaluation == "pass" and verified_recent_use:
            return "proven_useful", "Current evaluation passed and verified recent use exists."
        if verified_recent_use:
            return "used_evaluation_missing", "Verified recent use exists, but no current passing evaluation is retained."
        if (
            usage_state in {"complete_zero_30d", "settled_zero_30d"}
            and uses_30d == 0
            and evaluation != "pass"
        ):
            if usage_state == "settled_zero_30d":
                return (
                    "evaluate_now",
                    "Settled transcripts show no successful load in 30 days; recent active tails are excluded.",
                )
            return "evaluate_now", "Complete retained usage shows no successful load in 30 days."
        return "insufficient_information", "Primary evaluation or verified usage evidence is missing or incomplete."

    @staticmethod
    def _portfolio_usage_coverage(
        capability_id: str,
        usage: dict[str, Any],
        usage_row: dict[str, Any] | None,
        *,
        window_days: int = 30,
    ) -> dict[str, Any]:
        unavailable = {
            "window_days": window_days,
            "window_start": None,
            "window_end": None,
            "state": "unknown",
            "is_lower_bound": False,
            "usage_receipt_sha256": None,
            "collection_watermark": None,
            "excluded_recent": {"count": 0, "bytes": 0},
            "relevant_stable_backlog": {
                "count": 0,
                "bytes": 0,
                "oldest_modified_at": None,
            },
            "pending_failure_ids": [],
            "identity_blockers": [],
            "candidate_capability_ids": [],
        }
        if usage.get("available") is not True or usage_row is None:
            return unavailable
        window_end = parse_time(usage.get("collected_at"))
        if window_end is None:
            return unavailable
        window_start = window_end - window_days * 24 * 60 * 60
        uses = usage_row.get(f"uses_{window_days}d")
        if isinstance(uses, int) and not isinstance(uses, bool) and uses > 0:
            state = f"used_{window_days}d"
        else:
            state = ""

        def intersects(item: dict[str, Any]) -> bool:
            modified_at = parse_time(item.get("modified_at"))
            return modified_at is None or modified_at >= window_start

        pending = [
            item
            for item in usage.get("_pending", [])
            if isinstance(item, dict) and intersects(item)
        ]
        excluded = [
            item
            for item in pending
            if item.get("reason") == "events_recently_modified"
        ]
        stable = [
            item
            for item in pending
            if item.get("reason") != "events_recently_modified"
        ]
        relevant_failures = [
            item
            for item in usage.get("_failures", [])
            if isinstance(item, dict)
            and intersects(item)
            and (
                not item.get("candidate_capability_ids")
                or capability_id in item["candidate_capability_ids"]
            )
        ]
        identity_blockers = sorted(
            {
                item["name"]
                for item in usage.get("_unattributed", [])
                if isinstance(item, dict)
                and capability_id in item.get("candidate_capability_ids", [])
            }
        )
        identity_blockers.extend(
            sorted(
                {
                    item["reason"]
                    for item in relevant_failures
                    if item.get("candidate_capability_ids")
                }
            )
        )
        identity_blockers = sorted(set(identity_blockers))
        failure_ids = sorted(
            {
                item["failure_id"]
                for item in relevant_failures
                if isinstance(item.get("failure_id"), str)
            }
        )
        stable_session_ids = {
            item.get("session_id")
            for item in stable
            if isinstance(item.get("session_id"), str)
        }
        for failure in relevant_failures:
            if failure.get("candidate_capability_ids"):
                continue
            if failure.get("session_id") not in stable_session_ids:
                stable.append(failure)
                stable_session_ids.add(failure.get("session_id"))

        if not state:
            if usage.get("complete") is True:
                state = f"complete_zero_{window_days}d"
            elif identity_blockers:
                state = "blocked_identity"
            elif stable:
                state = "blocked_stable_backlog"
            else:
                state = f"settled_zero_{window_days}d"
        stable_times = [
            parse_time(item.get("modified_at"))
            for item in stable
            if parse_time(item.get("modified_at")) is not None
        ]
        return {
            "window_days": window_days,
            "window_start": datetime.fromtimestamp(
                window_start, timezone.utc
            ).isoformat(),
            "window_end": datetime.fromtimestamp(
                window_end, timezone.utc
            ).isoformat(),
            "state": state,
            "is_lower_bound": (
                state == f"used_{window_days}d"
                and usage.get("complete") is not True
            ),
            "usage_receipt_sha256": usage.get("_receipt_sha256"),
            "collection_watermark": usage.get("collection_watermark"),
            "excluded_recent": {
                "count": len(excluded),
                "bytes": sum(item.get("bytes", 0) for item in excluded),
            },
            "relevant_stable_backlog": {
                "count": len(stable),
                "bytes": sum(
                    item.get("bytes") or 0
                    for item in stable
                    if isinstance(item.get("bytes"), int)
                ),
                "oldest_modified_at": (
                    datetime.fromtimestamp(
                        min(stable_times), timezone.utc
                    ).isoformat()
                    if stable_times
                    else None
                ),
            },
            "pending_failure_ids": failure_ids,
            "identity_blockers": identity_blockers,
            "candidate_capability_ids": (
                [capability_id] if identity_blockers else []
            ),
        }

    @staticmethod
    def _portfolio_authority(authority: Any) -> str:
        return {
            "dreaming_managed": "Automatic",
            "legacy_machine": "Automatic",
            "plugin_managed": "Plugin package only",
            "cli_builtin": "Immutable",
        }.get(authority, "Your decision")

    def _estate_usage(
        self, census: dict[str, Any], census_receiver: dict[str, Any]
    ) -> dict[str, Any]:
        unavailable = {
            "status": "unavailable",
            "available": False,
            "complete": None,
            "corpus_complete": None,
            "attribution_complete": None,
            "source": None,
            "collected_at": None,
            "earliest_retained_event": None,
            "discovered_sessions": None,
            "discovered_bytes": None,
            "indexed_sessions": None,
            "indexed_bytes": None,
            "pending_sessions": None,
            "pending_bytes": None,
            "sessions_scanned": None,
            "bytes_scanned": None,
            "sessions_parsed_this_run": None,
            "bytes_parsed_this_run": None,
            "bound_reached": None,
            "collection_watermark": None,
            "work_budget_stopped_run": None,
            "index_status": None,
            "failure_count": None,
            "unattributed_count": None,
            "canonical_usage": [],
            "_receipt_sha256": None,
            "_snapshot_sha256": None,
            "_pending": [],
            "_failures": [],
            "_unattributed": [],
        }
        current_path = self.paths.state / "estate-usage-current.json"
        if not current_path.is_file() or current_path.is_symlink():
            return unavailable
        try:
            current = self._json(current_path, None, "current estate usage")
            required_current = {
                "schema_version",
                "receipt_sha256",
                "snapshot_sha256",
                "census_snapshot_sha256",
                "usage",
            }
            if (
                not isinstance(current, dict)
                or set(current) != required_current
                or current.get("schema_version") != 1
                or not CANDIDATE_ID_RE.fullmatch(
                    str(current.get("receipt_sha256", ""))
                )
                or not CANDIDATE_ID_RE.fullmatch(
                    str(current.get("snapshot_sha256", ""))
                )
                or current.get("census_snapshot_sha256")
                != census.get("snapshot_sha256")
                or not isinstance(current.get("usage"), dict)
            ):
                return unavailable
            usage = current["usage"]
            required_usage = {
                "schema_version",
                "host_id",
                "collected_at",
                "census_snapshot_sha256",
                "source",
                "coverage",
                "canonical_usage",
                "unattributed",
                "snapshot_sha256",
            }
            usage_snapshot = {
                key: value for key, value in usage.items() if key != "snapshot_sha256"
            }
            if (
                set(usage) != required_usage
                or usage.get("schema_version") != 1
                or usage.get("snapshot_sha256") != current["snapshot_sha256"]
                or sha(usage_snapshot) != usage.get("snapshot_sha256")
                or usage.get("host_id") != census.get("host_id")
                or usage.get("collected_at") != census.get("collected_at")
                or usage.get("census_snapshot_sha256")
                != census.get("snapshot_sha256")
                or usage.get("source") != "copilot_local_session_state"
            ):
                return unavailable
            receipt_path = (
                self.paths.state
                / "estate-usage-receipts"
                / f"{current['receipt_sha256'].removeprefix('sha256:')}.json"
            )
            if (
                not receipt_path.is_file()
                or receipt_path.is_symlink()
                or receipt_path.stat().st_size > MAX_JSON_BYTES
            ):
                return unavailable
            receipt = self._json(receipt_path, None, "estate usage receipt")
            if (
                not isinstance(receipt, dict)
                or set(receipt)
                != {
                    "schema_version",
                    "snapshot_sha256",
                    "census_snapshot_sha256",
                    "receiver",
                    "usage",
                }
                or receipt.get("schema_version") != 1
                or receipt.get("snapshot_sha256") != usage["snapshot_sha256"]
                or receipt.get("census_snapshot_sha256")
                != census.get("snapshot_sha256")
                or receipt.get("receiver") != census_receiver
                or receipt.get("usage") != usage
                or sha(receipt) != current["receipt_sha256"]
            ):
                return unavailable
            coverage = usage.get("coverage")
            if (
                not isinstance(coverage, dict)
                or set(coverage)
                != {
                    "complete",
                    "corpus_complete",
                    "attribution_complete",
                    "earliest_retained_event",
                    "discovered_sessions",
                    "discovered_bytes",
                    "indexed_sessions",
                    "indexed_bytes",
                    "pending_sessions",
                    "pending_bytes",
                    "sessions_scanned",
                    "bytes_scanned",
                    "sessions_parsed_this_run",
                    "bytes_parsed_this_run",
                    "max_sessions",
                    "max_bytes",
                    "quiet_seconds",
                    "collection_watermark",
                    "bound_reached",
                    "work_budget_stopped_run",
                    "index_status",
                    "pending",
                    "failures",
                }
                or not isinstance(coverage.get("complete"), bool)
                or not isinstance(coverage.get("corpus_complete"), bool)
                or not isinstance(coverage.get("attribution_complete"), bool)
                or not isinstance(coverage.get("work_budget_stopped_run"), bool)
                or not all(
                    isinstance(coverage.get(name), int)
                    and not isinstance(coverage.get(name), bool)
                    and coverage[name] >= 0
                    for name in (
                        "discovered_sessions",
                        "discovered_bytes",
                        "indexed_sessions",
                        "indexed_bytes",
                        "pending_sessions",
                        "pending_bytes",
                        "sessions_scanned",
                        "bytes_scanned",
                        "sessions_parsed_this_run",
                        "bytes_parsed_this_run",
                        "max_sessions",
                        "max_bytes",
                        "quiet_seconds",
                    )
                )
                or coverage["indexed_sessions"] + coverage["pending_sessions"]
                != coverage["discovered_sessions"]
                or coverage["indexed_bytes"] + coverage["pending_bytes"]
                != coverage["discovered_bytes"]
                or coverage["sessions_scanned"]
                != coverage["sessions_parsed_this_run"]
                or coverage["bytes_scanned"] != coverage["bytes_parsed_this_run"]
                or coverage.get("bound_reached")
                not in {None, "max_sessions", "max_bytes"}
                or coverage["work_budget_stopped_run"]
                != (coverage["bound_reached"] is not None)
                or coverage.get("index_status")
                not in {"absent", "loaded", "migrated", "rebuilt"}
                or parse_time(coverage.get("collection_watermark")) is None
                or coverage.get("collection_watermark") != usage.get("collected_at")
                or not isinstance(coverage.get("pending"), list)
                or not isinstance(coverage.get("failures"), list)
            ):
                return unavailable
            if coverage["earliest_retained_event"] is not None and parse_time(
                coverage["earliest_retained_event"]
            ) is None:
                return unavailable
            for pending in coverage["pending"]:
                if (
                    not isinstance(pending, dict)
                    or set(pending)
                    != {
                        "session_id",
                        "reason",
                        "modified_at",
                        "bytes",
                        "failure_id",
                    }
                    or not CANDIDATE_ID_RE.fullmatch(
                        str(pending.get("session_id", ""))
                    )
                    or pending.get("reason")
                    not in {
                        "events_recently_modified",
                        "stable_budget_deferred",
                        "events_changed_or_unreadable",
                        "usage_session_invalid_utf8",
                        "usage_session_malformed_json",
                        "usage_session_event_not_object",
                        "usage_session_invalid_timestamp",
                        "usage_session_future_timestamp",
                        "usage_session_invalid_skill_start",
                        "usage_session_duplicate_skill_start",
                        "usage_session_duplicate_completion",
                    }
                    or parse_time(pending.get("modified_at")) is None
                    or not isinstance(pending.get("bytes"), int)
                    or isinstance(pending.get("bytes"), bool)
                    or pending["bytes"] < 0
                    or (
                        pending.get("failure_id") is not None
                        and not CANDIDATE_ID_RE.fullmatch(
                            str(pending.get("failure_id", ""))
                        )
                    )
                ):
                    return unavailable
            for failure in coverage["failures"]:
                if (
                    not isinstance(failure, dict)
                    or set(failure)
                    != {
                        "failure_id",
                        "session_id",
                        "reason",
                        "modified_at",
                        "bytes",
                        "candidate_capability_ids",
                    }
                    or not CANDIDATE_ID_RE.fullmatch(
                        str(failure.get("failure_id", ""))
                    )
                    or not CANDIDATE_ID_RE.fullmatch(
                        str(failure.get("session_id", ""))
                    )
                    or not re.fullmatch(
                        r"[a-z0-9_]{3,100}", str(failure.get("reason", ""))
                    )
                    or (
                        failure.get("modified_at") is not None
                        and parse_time(failure.get("modified_at")) is None
                    )
                    or (
                        failure.get("bytes") is not None
                        and (
                            not isinstance(failure.get("bytes"), int)
                            or isinstance(failure.get("bytes"), bool)
                            or failure["bytes"] < 0
                        )
                    )
                    or not isinstance(
                        failure.get("candidate_capability_ids"), list
                    )
                    or not all(
                        CANDIDATE_ID_RE.fullmatch(str(value))
                        for value in failure["candidate_capability_ids"]
                    )
                ):
                    return unavailable
            pending_ids = [item["session_id"] for item in coverage["pending"]]
            failures_by_id = {
                item["failure_id"]: item for item in coverage["failures"]
            }
            if (
                len(pending_ids) != len(set(pending_ids))
                or len(pending_ids) != coverage["pending_sessions"]
                or len(failures_by_id) != len(coverage["failures"])
                or sum(item["bytes"] for item in coverage["pending"])
                != coverage["pending_bytes"]
                or any(
                    item["failure_id"] is not None
                    and (
                        item["failure_id"] not in failures_by_id
                        or any(
                            failures_by_id[item["failure_id"]][field]
                            != item[field]
                            for field in (
                                "session_id",
                                "reason",
                                "modified_at",
                                "bytes",
                            )
                        )
                    )
                    for item in coverage["pending"]
                )
                or (
                    coverage["corpus_complete"]
                    and coverage["pending_sessions"] != 0
                )
            ):
                return unavailable
            enabled_ids = {
                item.get("canonical_capability_id")
                for item in census.get("enabled_instances", [])
                if isinstance(item, dict) and item.get("runtime_enabled") is True
            }
            canonical_usage = usage.get("canonical_usage")
            if not isinstance(canonical_usage, list):
                return unavailable
            seen_ids: set[str] = set()
            for item in canonical_usage:
                if (
                    not isinstance(item, dict)
                    or set(item)
                    != {
                        "canonical_capability_id",
                        "uses_7d",
                        "uses_30d",
                        "uses_90d",
                        "uses_total",
                        "last_successful_invocation",
                    }
                    or item.get("canonical_capability_id") not in enabled_ids
                    or item.get("canonical_capability_id") in seen_ids
                    or not all(
                        isinstance(item.get(name), int)
                        and not isinstance(item.get(name), bool)
                        and item[name] >= 0
                        for name in (
                            "uses_7d",
                            "uses_30d",
                            "uses_90d",
                            "uses_total",
                        )
                    )
                    or not (
                        item["uses_7d"]
                        <= item["uses_30d"]
                        <= item["uses_90d"]
                        <= item["uses_total"]
                    )
                ):
                    return unavailable
                last_used = item.get("last_successful_invocation")
                if (
                    last_used is not None
                    and (
                        parse_time(last_used) is None
                        or parse_time(last_used) > parse_time(usage["collected_at"])
                    )
                ):
                    return unavailable
                seen_ids.add(item["canonical_capability_id"])
            unattributed = usage.get("unattributed")
            if not isinstance(unattributed, list):
                return unavailable
            for item in unattributed:
                if (
                    not isinstance(item, dict)
                    or set(item)
                    != {
                        "name",
                        "reason",
                        "uses_7d",
                        "uses_30d",
                        "uses_90d",
                        "uses_total",
                        "candidate_capability_ids",
                    }
                    or not OBSERVED_SKILL_RE.fullmatch(str(item.get("name", "")))
                    or item.get("reason")
                    not in {
                        "unmapped",
                        "conflicting_mapping",
                        "alias_target_missing",
                        "alias_target_conflicting",
                    }
                    or not isinstance(
                        item.get("candidate_capability_ids"), list
                    )
                    or not all(
                        CANDIDATE_ID_RE.fullmatch(str(value))
                        for value in item["candidate_capability_ids"]
                    )
                ):
                    return unavailable
                if not set(item["candidate_capability_ids"]).issubset(enabled_ids):
                    return unavailable
            if any(
                not set(item["candidate_capability_ids"]).issubset(enabled_ids)
                for item in coverage["failures"]
            ):
                return unavailable
            complete = coverage["complete"]
            if complete and (
                seen_ids != enabled_ids
                or not coverage["corpus_complete"]
                or not coverage["attribution_complete"]
                or coverage["failures"]
                or coverage["bound_reached"] is not None
                or unattributed
            ):
                return unavailable
            return {
                "status": "complete" if complete else "incomplete",
                "available": True,
                "complete": complete,
                "corpus_complete": coverage["corpus_complete"],
                "attribution_complete": coverage["attribution_complete"],
                "source": "MacBook Copilot local transcripts",
                "collected_at": usage["collected_at"],
                "earliest_retained_event": coverage["earliest_retained_event"],
                "discovered_sessions": coverage["discovered_sessions"],
                "discovered_bytes": coverage["discovered_bytes"],
                "indexed_sessions": coverage["indexed_sessions"],
                "indexed_bytes": coverage["indexed_bytes"],
                "pending_sessions": coverage["pending_sessions"],
                "pending_bytes": coverage["pending_bytes"],
                "sessions_scanned": coverage["sessions_scanned"],
                "bytes_scanned": coverage["bytes_scanned"],
                "sessions_parsed_this_run": coverage["sessions_parsed_this_run"],
                "bytes_parsed_this_run": coverage["bytes_parsed_this_run"],
                "bound_reached": coverage["bound_reached"],
                "collection_watermark": coverage["collection_watermark"],
                "work_budget_stopped_run": coverage["work_budget_stopped_run"],
                "index_status": coverage["index_status"],
                "failure_count": len(coverage["failures"]),
                "unattributed_count": len(unattributed),
                "canonical_usage": canonical_usage,
                "_receipt_sha256": current["receipt_sha256"],
                "_snapshot_sha256": current["snapshot_sha256"],
                "_pending": coverage["pending"],
                "_failures": coverage["failures"],
                "_unattributed": unattributed,
            }
        except (DashboardError, OSError, TypeError, ValueError):
            return unavailable

    def _backlog_history(self) -> list[dict[str, Any]]:
        queue = [
            item for item in self._list("queue.json") if isinstance(item, dict)
        ]
        ledger = [
            item
            for item in self._list("review-ledger.json")
            if isinstance(item, dict)
        ]
        completions = {
            (item.get("session_id"), item.get("source_revision")): parse_time(
                item.get("reviewed_at")
            )
            for item in ledger
        }
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in queue:
            session_id = item.get("qualified_session_id")
            if isinstance(session_id, str):
                grouped.setdefault(session_id, []).append(item)
        intervals = []
        for session_id, revisions in grouped.items():
            revisions.sort(
                key=lambda item: (
                    parse_time(item.get("queued_at"))
                    or parse_time(item.get("updated_at"))
                    or 0,
                    str(item.get("source_revision", "")),
                )
            )
            for index, item in enumerate(revisions):
                started = parse_time(item.get("queued_at")) or parse_time(
                    item.get("updated_at")
                )
                if started is None:
                    continue
                completed = completions.get(
                    (session_id, item.get("source_revision"))
                )
                replaced = None
                if index + 1 < len(revisions):
                    replaced = parse_time(revisions[index + 1].get("queued_at")) or parse_time(
                        revisions[index + 1].get("updated_at")
                    )
                ended_candidates = [
                    value for value in (completed, replaced) if value is not None
                ]
                intervals.append(
                    (started, min(ended_candidates) if ended_candidates else None)
                )
        if not intervals:
            return []
        end = time.time()
        start = max(min(item[0] for item in intervals), end - 30 * 86400)
        return [
            {
                "at": int(at),
                "remaining": sum(
                    opened <= at and (closed is None or closed > at)
                    for opened, closed in intervals
                ),
            }
            for at in (
                start + (end - start) * index / 30
                for index in range(31)
            )
        ]

    def _skill_history(self) -> list[dict[str, Any]]:
        if not (self.paths.skills / ".git").is_dir():
            return []
        result = subprocess.run(
            [
                "git",
                "-C",
                str(self.paths.skills),
                "log",
                "--format=%H|%ct",
                "--since=180 days ago",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            return []
        commits = result.stdout.splitlines()
        selected = commits[:: max(1, len(commits) // 12)] if commits else []
        points = []
        for entry in reversed(selected[:12]):
            commit, epoch = entry.split("|", 1)
            tree = subprocess.run(
                ["git", "-C", str(self.paths.skills), "ls-tree", "-r", "--name-only", commit],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            if tree.returncode != 0:
                continue
            count = sum(path.endswith("/.agent-created") for path in tree.stdout.splitlines())
            points.append({"at": int(epoch), "count": count})
        return points

    def health(self) -> dict[str, Any]:
        halt = self.paths.control_state / "skill-review/disable-daemon"
        publication_recovery = (
            self.paths.state / "publication-recovery-required.json"
        )
        evaluation_recovery_path = (
            self.paths.control_state
            / "dreaming/evaluation-input-recovery-required.json"
        )
        evaluation_recovery = None
        evaluation_recovery_invalid = False
        if evaluation_recovery_path.exists() or evaluation_recovery_path.is_symlink():
            try:
                if (
                    evaluation_recovery_path.is_symlink()
                    or not evaluation_recovery_path.is_file()
                    or evaluation_recovery_path.stat().st_size > 64_000
                ):
                    raise ValueError("evaluation recovery marker")
                evaluation_recovery = self._json(
                    evaluation_recovery_path,
                    None,
                    "evaluation-input recovery marker",
                )
                if (
                    not isinstance(evaluation_recovery, dict)
                    or set(evaluation_recovery)
                    != {
                        "schema_version",
                        "kind",
                        "claims",
                        "record_sha256",
                    }
                    or evaluation_recovery.get("schema_version") != 1
                    or evaluation_recovery.get("kind")
                    != "evaluation_input_recovery_required"
                    or not isinstance(evaluation_recovery.get("claims"), list)
                    or not evaluation_recovery["claims"]
                    or any(
                        not isinstance(row, dict)
                        or set(row) != {"claim_id", "reason"}
                        or CANDIDATE_ID_RE.fullmatch(
                            str(row.get("claim_id", ""))
                        )
                        is None
                        or re.fullmatch(
                            r"[a-z][a-z0-9_]{0,127}",
                            str(row.get("reason", "")),
                        )
                        is None
                        for row in evaluation_recovery["claims"]
                    )
                    or len(
                        {
                            row["claim_id"]
                            for row in evaluation_recovery["claims"]
                        }
                    )
                    != len(evaluation_recovery["claims"])
                ):
                    raise ValueError("evaluation recovery marker")
                retained = dict(evaluation_recovery)
                record_sha256 = retained.pop("record_sha256")
                if record_sha256 != sha(retained):
                    raise ValueError("evaluation recovery marker identity")
            except (DashboardError, OSError, TypeError, ValueError):
                evaluation_recovery = None
                evaluation_recovery_invalid = True
        generation_path = self.paths.control_state / "dreaming/activation-generation"
        activation_generation = None
        if (
            generation_path.is_file()
            and not generation_path.is_symlink()
            and generation_path.stat().st_size <= 256
        ):
            try:
                value = generation_path.read_text(encoding="ascii").strip()
                if re.fullmatch(
                    r"[0-9]{8}T[0-9]{6}Z-(?:install|rollback)-[A-Za-z0-9._-]+",
                    value,
                ):
                    activation_generation = value
            except (OSError, UnicodeError):
                activation_generation = None
        runs_dir = self.paths.orchestrator_state / "runs"
        latest = None
        if runs_dir.is_dir():
            files = [path for path in runs_dir.glob("*.json") if path.is_file() and not path.is_symlink()]
            if files:
                latest = self._json(max(files, key=lambda path: path.stat().st_mtime_ns), {}, "latest run")
        return {
            "status": (
                "halted"
                if halt.exists()
                else "Evaluation recovery state invalid"
                if evaluation_recovery_invalid
                else "Evaluation recovery required"
                if evaluation_recovery is not None
                else "publication_recovery_required"
                if publication_recovery.exists()
                else "healthy"
                if isinstance(latest, dict) and latest.get("status") in {"ok", "skipped"}
                else "unknown"
            ),
            "halted": halt.exists(),
            "publication_recovery_required": publication_recovery.exists(),
            "evaluation_input_recovery_required": (
                evaluation_recovery is not None
            ),
            "evaluation_input_recovery_invalid": evaluation_recovery_invalid,
            "evaluation_input_recovery_claims": (
                len(evaluation_recovery["claims"])
                if evaluation_recovery is not None
                else 0
            ),
            "activation_generation": activation_generation,
            "process_id": os.getpid(),
            "latest_run": latest,
        }

    def system(self) -> dict[str, Any]:
        config = self._adapter_config()
        snapshot_bytes = config.get("max_snapshot_bytes", 100_000)
        if (
            not isinstance(snapshot_bytes, int)
            or isinstance(snapshot_bytes, bool)
            or snapshot_bytes < 2
        ):
            raise DashboardError(
                503,
                "adapter_config_invalid",
                "Adapter snapshot limit is malformed",
                ["adapter config"],
            )
        categories = []
        category_paths = [
            ("State", self.paths.state),
            ("Control state", self.paths.control_state),
            ("Orchestrator state", self.paths.orchestrator_state),
            ("Snapshots", self.paths.data / "snapshots"),
            ("Evaluations", self.paths.control_state / "skill-review/evaluations"),
            ("Bundles", self.paths.data / "bundles"),
            ("Learned skills", self.paths.skills),
            ("Daemon logs", self.paths.control_state / "daemon-logs"),
        ]
        if self.paths.preview_root is None:
            category_paths.append(("Dashboard logs", Path.home() / "Library/Logs/Dreaming"))
        for name, path in category_paths:
            size, count = tree_size(path)
            categories.append({"name": name, "bytes": size, "items": count})
        devices = {}
        for path in {
            self.paths.state,
            self.paths.control_state,
            self.paths.orchestrator_state,
            self.paths.data,
            self.paths.skills,
        }:
            existing = path
            while not existing.exists() and existing != existing.parent:
                existing = existing.parent
            info = existing.stat()
            usage = shutil.disk_usage(existing)
            devices[str(info.st_dev)] = {
                "path": str(existing),
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
            }
        return {
            "health": self.health(),
            "roots": {
                "state": str(self.paths.state),
                "control_state": str(self.paths.control_state),
                "orchestrator_state": str(self.paths.orchestrator_state),
                "data": str(self.paths.data),
                "skills": str(self.paths.skills),
            },
            "categories": categories,
            "filesystems": list(devices.values()),
            "limits": {
                "snapshot_bytes": snapshot_bytes,
                "aggregate_retention_bytes": None,
                "automatic_cleanup": False,
            },
        }


def first(params: dict[str, list[str]], name: str) -> str:
    values = params.get(name)
    return values[0] if values else ""


def parse_limit(params: dict[str, list[str]]) -> int:
    raw = first(params, "limit")
    if not raw:
        return DEFAULT_LIMIT
    try:
        value = int(raw)
    except ValueError as exc:
        raise DashboardError(400, "invalid_limit", "Page limit is invalid") from exc
    if not 1 <= value <= MAX_LIMIT:
        raise DashboardError(400, "invalid_limit", "Page limit is outside the supported range")
    return value


def maintenance_status(run: dict[str, Any]) -> dict[str, Any] | None:
    passes = run.get("passes")
    if not isinstance(passes, list) or not any(
        isinstance(item, dict)
        and item.get("name") in {"roll", "prune"}
        and item.get("status") == "not_scheduled"
        and item.get("reason") == "weekly-not-due"
        for item in passes
    ):
        return None
    last = parse_time(run.get("last_success_at_before"))
    started = parse_time(run.get("started_at")) or time.time()
    if last is None:
        return {
            "status": "not_due",
            "last_run_at": None,
            "days_until_due": None,
        }
    return {
        "status": "not_due",
        "last_run_at": run.get("last_success_at_before"),
        "days_until_due": max(0, math.ceil((last + 604800 - started) / 86400)),
    }


def tree_size(root: Path) -> tuple[int, int]:
    if not root.exists() or root.is_symlink():
        return 0, 0
    size = 0
    count = 0
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            size += path.stat().st_size
            count += 1
        except OSError:
            continue
    return size, count


class BoundedThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

    def __init__(self, *args: Any, max_workers: int = 8, **kwargs: Any):
        self._workers = threading.BoundedSemaphore(max_workers)
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._workers.acquire(blocking=False):
            request.close()
            return
        super().process_request(request, client_address)

    def get_request(self) -> tuple[Any, Any]:
        request, address = super().get_request()
        request.settimeout(10)
        return request, address

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._workers.release()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "DreamingDashboard/1"
    protocol_version = "HTTP/1.1"
    data: DashboardData
    token: str
    allowed_hosts: set[str]
    tailnet_host: str | None

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def _headers(self, content_type: str, length: int, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self._headers(content_type, len(body), status)
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json_response(self, data: Any, fingerprint: str | None = None) -> None:
        body = canonical(
            {
                "schema_version": SCHEMA_VERSION,
                "generated_at": now_iso(),
                "source_fingerprint": fingerprint or sha(data),
                "data": data,
            }
        )
        if len(body) > MAX_JSON_BYTES:
            raise DashboardError(
                413,
                "response_too_large",
                "Dashboard response exceeds its bounded limit",
            )
        self._send(body, "application/json; charset=utf-8")

    def _error(self, error: DashboardError) -> None:
        body = canonical(
            {
                "schema_version": SCHEMA_VERSION,
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "sources": error.sources,
                },
            }
        )
        self._send(body, "application/json; charset=utf-8", error.status)

    def _request_guard(self, api: bool) -> None:
        if len(self.headers) > 64:
            raise DashboardError(431, "headers_too_large", "Too many request headers")
        host = self.headers.get("Host", "")
        if host not in self.allowed_hosts:
            raise DashboardError(403, "host_denied", "Request host is not allowed")
        origin = self.headers.get("Origin")
        tailnet_request = self.tailnet_host is not None and host == self.tailnet_host
        if tailnet_request:
            if origin is not None and origin != f"https://{self.tailnet_host}":
                raise DashboardError(403, "origin_denied", "Request origin is not allowed")
        elif origin is not None and origin not in {
            f"http://{item}" for item in self.allowed_hosts if item != self.tailnet_host
        }:
            raise DashboardError(403, "origin_denied", "Request origin is not allowed")
        if not api:
            return
        if self.headers.get("Cookie") or "access_token" in urllib.parse.urlsplit(self.path).query:
            raise DashboardError(401, "authentication_required", "Bearer authentication is required")
        if tailnet_request:
            return
        authorization = self.headers.get("Authorization", "")
        prefix = "Bearer "
        supplied = authorization[len(prefix) :] if authorization.startswith(prefix) else ""
        if not hmac.compare_digest(supplied, self.token):
            raise DashboardError(401, "authentication_required", "Bearer authentication is required")

    def _dispatch(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        api = parsed.path.startswith("/api/")
        self._request_guard(api)
        if api:
            verify_preview_manifest(self.data.paths)
        if not api:
            return self._static(parsed.path)
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        path = parsed.path
        if path == "/api/v1/health":
            return self._json_response(self.data.health())
        if path == "/api/v1/overview":
            return self._json_response(self.data.overview())
        if path == "/api/v1/estate":
            return self._json_response(self.data.estate())
        if path == "/api/v1/activity":
            result = self.data.activity(params)
            return self._json_response(result, result["fingerprint"])
        if path == "/api/v1/dreams":
            result = self.data.dreams(params)
            return self._json_response(result, result["fingerprint"])
        if path.startswith("/api/v1/dreams/"):
            session_id = urllib.parse.unquote(path.removeprefix("/api/v1/dreams/"))
            return self._json_response(self.data.dream_detail(session_id))
        if path == "/api/v1/skills":
            result = self.data.skills(params)
            return self._json_response(result, result["fingerprint"])
        if path.startswith("/api/v1/skills/"):
            tail = path.removeprefix("/api/v1/skills/")
            if tail.endswith("/evidence"):
                name = urllib.parse.unquote(tail.removesuffix("/evidence"))
                result = self.data.evidence(name, params)
                return self._json_response(result, result["fingerprint"])
            return self._json_response(self.data.skill_detail(urllib.parse.unquote(tail)))
        if path == "/api/v1/candidates":
            result = self.data.candidates(params)
            return self._json_response(result, result["fingerprint"])
        if path.startswith("/api/v1/candidates/"):
            lifecycle_id = urllib.parse.unquote(
                path.removeprefix("/api/v1/candidates/")
            )
            return self._json_response(self.data.candidate_detail(lifecycle_id))
        if path.startswith("/api/v1/transcripts/"):
            digest = path.removeprefix("/api/v1/transcripts/")
            return self._json_response(self.data.transcript(digest))
        if path == "/api/v1/system":
            return self._json_response(self.data.system())
        raise DashboardError(404, "route_not_found", "Route was not found")

    def _static(self, path: str) -> None:
        names = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
            "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
        }
        if path not in names:
            raise DashboardError(404, "route_not_found", "Route was not found")
        name, content_type = names[path]
        asset = self.data.paths.assets / name
        if asset.is_symlink() or not asset.is_file() or asset.parent.resolve() != self.data.paths.assets:
            raise DashboardError(503, "asset_missing", "Dashboard asset is unavailable")
        body = asset.read_bytes()
        if (
            name == "index.html"
            and self.tailnet_host is not None
            and self.headers.get("Host") == self.tailnet_host
        ):
            body = body.replace(
                b'<meta name="dreaming-tailnet-host" content="">',
                (
                    f'<meta name="dreaming-tailnet-host" '
                    f'content="{self.tailnet_host}">'
                ).encode("ascii"),
            )
        self._send(body, content_type)

    def do_GET(self) -> None:
        try:
            self._dispatch()
        except DashboardError as error:
            self._error(error)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self._error(DashboardError(500, "internal_error", "Dashboard request failed"))

    def do_HEAD(self) -> None:
        self.do_GET()

    def _method_not_allowed(self) -> None:
        try:
            self._request_guard(self.path.startswith("/api/"))
        except DashboardError as error:
            self._error(error)
            return
        self._error(DashboardError(405, "method_not_allowed", "Method is not supported"))

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_OPTIONS = _method_not_allowed


def configured_tailnet_host(port: int) -> str | None:
    value = os.environ.get("DREAMING_DASHBOARD_TAILNET_HOST")
    if value is None:
        return None
    try:
        parsed = urllib.parse.urlsplit(f"//{value}")
        parsed_port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
        or parsed.hostname != parsed.hostname.lower()
        or not parsed.hostname.endswith(".ts.net")
        or parsed_port != port
        or value != f"{parsed.hostname}:{parsed_port}"
    ):
        return None
    return value


def run_server(host: str, port: int, paths: DashboardPaths) -> None:
    if host != "127.0.0.1":
        raise DashboardError(
            2,
            "bind_denied",
            "Dashboard host must be 127.0.0.1",
        )
    if not 1 <= port <= 65535:
        raise DashboardError(2, "port_invalid", "Dashboard port is invalid")
    verify_preview_manifest(paths)
    token = read_token(paths.token)
    if not paths.assets.is_dir() or paths.assets.is_symlink():
        raise DashboardError(2, "asset_missing", "Dashboard assets are unavailable")
    tailnet_host = configured_tailnet_host(port)
    allowed_hosts = {
        f"{host}:{port}",
        f"localhost:{port}",
    }
    if tailnet_host is not None:
        allowed_hosts.add(tailnet_host)
    handler = type(
        "ConfiguredDashboardHandler",
        (DashboardHandler,),
        {
            "data": DashboardData(paths),
            "token": token,
            "allowed_hosts": allowed_hosts,
            "tailnet_host": tailnet_host,
        },
    )
    server = BoundedThreadingHTTPServer((host, port), handler)
    server.serve_forever(poll_interval=0.5)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--host", default=os.environ.get("DREAMING_DASHBOARD_HOST", "127.0.0.1")
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--preview-snapshot",
        type=Path,
        help="Private manifested snapshot root for report-only preview mode",
    )
    parser.add_argument(
        "--preview-assets",
        type=Path,
        help="Read-only dashboard assets for preview mode",
    )
    capture = parser.add_subparsers(dest="command")
    snapshot = capture.add_parser(
        "capture-preview-snapshot",
        help="Capture one private dashboard preview snapshot",
    )
    snapshot.add_argument("--destination", type=Path, required=True)
    for name in PREVIEW_ROOT_NAMES:
        snapshot.add_argument(
            f"--source-{name.replace('_', '-')}",
            dest=f"source_{name}",
            type=Path,
            required=True,
        )
    snapshot.add_argument("--lock-state", type=Path, required=True)
    snapshot.add_argument(
        "--next-eligible-at",
        help="Known next installed interval eligibility as ISO-8601 or epoch seconds",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "capture-preview-snapshot":
            next_eligible = (
                parse_time(args.next_eligible_at)
                if args.next_eligible_at is not None
                else None
            )
            if args.next_eligible_at is not None and next_eligible is None:
                raise DashboardError(
                    2, "preview_capture_invalid", "Next interval eligibility is invalid"
                )
            capture_preview_snapshot(
                destination=args.destination,
                roots={
                    name: getattr(args, f"source_{name}")
                    for name in PREVIEW_ROOT_NAMES
                },
                lock_state=args.lock_state,
                next_eligible_at=next_eligible,
            )
            return 0
        if args.preview_snapshot is not None:
            if args.port is None or args.port == 47673:
                raise DashboardError(
                    2,
                    "preview_port_required",
                    "Preview mode requires a caller-supplied non-default port",
                )
            assets = args.preview_assets or (
                Path(__file__).parents[1] / "assets/dashboard"
            )
            run_server(
                args.host,
                args.port,
                DashboardPaths.preview(args.preview_snapshot, assets),
            )
            return 0
        port = (
            args.port
            if args.port is not None
            else int(os.environ.get("DREAMING_DASHBOARD_PORT", "47673"))
        )
        run_server(args.host, port, DashboardPaths.defaults())
        return 0
    except DashboardError as error:
        print(f"ERROR: {error.code}: {error.message}", file=os.sys.stderr)
        return 2
    except OSError as error:
        print(f"ERROR: bind_failed: {error}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
