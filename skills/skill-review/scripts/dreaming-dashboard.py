#!/usr/bin/env python3
"""Private read-only localhost dashboard for Dreaming."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
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
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any

SCHEMA_VERSION = 1
SKILL_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 1_100_000
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
    orchestrator_state: Path
    data: Path
    skills: Path
    repo: Path
    assets: Path
    token: Path
    candidate_records: Path | None = None
    candidate_packages: Path | None = None

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
            orchestrator_state,
            data,
            skills,
            repo,
            assets,
            token,
            candidate_records,
            candidate_packages,
        )


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
            descriptor = (
                summary.get("descriptor")
                if isinstance(summary, dict)
                and summary.get("schema_version") == 1
                and summary.get("status") == "committed"
                and isinstance(summary.get("receiver_id"), str)
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
        for path in root.glob("*.json"):
            if path.is_symlink():
                continue
            transition = self._json(path, {}, f"transition:{path.name}")
            if (
                not isinstance(transition, dict)
                or transition.get("kind") != "dashboard_authority_transition"
                or transition.get("skill_key") != self._skill_key(skill)
                or transition.get("candidate_id") != candidate
                or transition.get("status")
                not in {"pass", "regression", "inconclusive", "revoked"}
            ):
                continue
            if current is None or (parse_time(transition.get("effective_at")) or 0) > (
                parse_time(current.get("effective_at")) or 0
            ):
                current = transition
        if current is None or not self._transition_matches_current(skill, current):
            return None
        return current

    def _transition_matches_current(
        self, skill: Path, transition: dict[str, Any]
    ) -> bool:
        effective_at = parse_time(transition.get("effective_at"))
        if effective_at is None:
            return False
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
                        {
                            **snapshot["events"][position],
                            "highlighted": position in indexes,
                        }
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
        return self._snapshot(digest)

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
        return {
            "runtime": self.health(),
            "dreams": {
                "remaining": len(remaining),
                "completed": len(completed),
                "active": sum(item["status"] == "active" for item in dreams),
                "history": self._backlog_history(),
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
            "activity": activity,
        }

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
                else "publication_recovery_required"
                if publication_recovery.exists()
                else "healthy"
                if isinstance(latest, dict) and latest.get("status") in {"ok", "skipped"}
                else "unknown"
            ),
            "halted": halt.exists(),
            "publication_recovery_required": publication_recovery.exists(),
            "activation_generation": activation_generation,
            "process_id": os.getpid(),
            "latest_run": latest,
        }

    def system(self) -> dict[str, Any]:
        categories = []
        for name, path in (
            ("State", self.paths.state),
            ("Control state", self.paths.control_state),
            ("Orchestrator state", self.paths.orchestrator_state),
            ("Snapshots", self.paths.data / "snapshots"),
            ("Evaluations", self.paths.control_state / "skill-review/evaluations"),
            ("Bundles", self.paths.data / "bundles"),
            ("Learned skills", self.paths.skills),
            ("Daemon logs", self.paths.control_state / "daemon-logs"),
            ("Dashboard logs", Path.home() / "Library/Logs/Dreaming"),
        ):
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
                "snapshot_bytes": 1_000_000,
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
        if origin is not None and origin not in {f"http://{item}" for item in self.allowed_hosts}:
            raise DashboardError(403, "origin_denied", "Request origin is not allowed")
        if not api:
            return
        if self.headers.get("Cookie") or "access_token" in urllib.parse.urlsplit(self.path).query:
            raise DashboardError(401, "authentication_required", "Bearer authentication is required")
        authorization = self.headers.get("Authorization", "")
        prefix = "Bearer "
        supplied = authorization[len(prefix) :] if authorization.startswith(prefix) else ""
        if not hmac.compare_digest(supplied, self.token):
            raise DashboardError(401, "authentication_required", "Bearer authentication is required")

    def _dispatch(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        api = parsed.path.startswith("/api/")
        self._request_guard(api)
        if not api:
            return self._static(parsed.path)
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        path = parsed.path
        if path == "/api/v1/health":
            return self._json_response(self.data.health())
        if path == "/api/v1/overview":
            return self._json_response(self.data.overview())
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
        self._send(asset.read_bytes(), content_type)

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


def run_server(host: str, port: int, paths: DashboardPaths) -> None:
    if host != "127.0.0.1":
        raise DashboardError(
            2,
            "bind_denied",
            "Dashboard host must be 127.0.0.1",
        )
    if not 1 <= port <= 65535:
        raise DashboardError(2, "port_invalid", "Dashboard port is invalid")
    token = read_token(paths.token)
    if not paths.assets.is_dir() or paths.assets.is_symlink():
        raise DashboardError(2, "asset_missing", "Dashboard assets are unavailable")
    handler = type(
        "ConfiguredDashboardHandler",
        (DashboardHandler,),
        {
            "data": DashboardData(paths),
            "token": token,
            "allowed_hosts": {
                f"{host}:{port}",
                f"localhost:{port}",
            },
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
        default=int(os.environ.get("DREAMING_DASHBOARD_PORT", "47673")),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        run_server(args.host, args.port, DashboardPaths.defaults())
        return 0
    except DashboardError as error:
        print(f"ERROR: {error.code}: {error.message}", file=os.sys.stderr)
        return 2
    except OSError as error:
        print(f"ERROR: bind_failed: {error}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
