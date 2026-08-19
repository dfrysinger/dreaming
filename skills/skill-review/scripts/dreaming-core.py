#!/usr/bin/env python3
"""Vendor-neutral Dreaming Milestone 1 runtime core.

Adapters are executable JSON protocol clients.  This module owns durable
discovery state, immutable snapshots, routing, fallback, result admission,
legacy migration, and content-addressed publication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

CONTRACT_VERSION = 1
ROLES = {
    "session-source": {
        "protocol": "dreaming.session-source",
        "capabilities": {
            "stable-pagination",
            "qualified-identity",
            "bounded-render",
            "revision-inspect",
        },
    },
    "review-executor": {
        "protocol": "dreaming.review-executor",
        "capabilities": {"source-blind", "mutation-fence", "completion-sentinel"},
    },
    "skill-publisher": {
        "protocol": "dreaming.skill-publisher",
        "capabilities": {
            "content-addressed-bundle",
            "ownership-safe-remove",
            "exact-inventory",
        },
    },
}
EVENT_KINDS = {
    "user_message",
    "assistant_message",
    "tool_call",
    "tool_result",
    "checkpoint",
    "summary",
    "session_end",
}
COMPLETED = {"terminal", "quiet"}
ORCHESTRATION_SKILLS = {
    "skill-review",
    "skill-create",
    "skill-manage",
    "skill-curator",
    "memory-curator",
}
ROLE_CONFIG_KEYS = {
    "sources": "session-source",
    "executors": "review-executor",
    "publishers": "skill-publisher",
}
REVIEW_DESTINATIONS = {
    "instruction",
    "factual_memory",
    "skill",
    "support_file",
    "discard",
}
ARTIFACT_OPERATIONS = {"create", "patch", "support_file"}
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RESERVED_SKILL_FILES = {"skill.md", ".agent-created", ".agent-created.json"}
EVALUATION_INPUT_OWNER_KEYS = {
    "enabled",
    "author_model",
    "reviewer_a_model",
    "reviewer_b_model",
    "content_root",
}
EVALUATION_INPUT_CONTENT_ROLES = {
    "suite": "application/json",
    "policy": "application/json",
    "compilation": "application/json",
    "routing": "application/json",
    "catalog": "application/json",
    "harness": "text/x-python",
}
EVALUATION_INPUT_SUPPORT_ROLES = {
    "fixture": "application/octet-stream",
    "grader": "application/octet-stream",
}
EVALUATION_INPUT_INDEX_NAME = "root-index.json"
EVALUATION_INPUT_MANIFEST_NAME = "input-manifest.json"
EVALUATION_INPUT_MAX_CONTROL_BYTES = 64_000
EVALUATION_INPUT_MAX_FILE_BYTES = 1_048_576
EVALUATION_INPUT_OWNER_MAX_SECONDS = 25 * 60
EVALUATION_INPUT_OWNER_STOP_SECONDS = 10
EVALUATION_INPUT_QUEUE_STATES = {
    "pass",
    "regression",
    "inconclusive",
    "stale",
    "missing",
    "input_missing",
    "drafting",
    "review_required",
    "insufficient_information",
    "ready",
    "invalid",
}


class RuntimeFailure(RuntimeError):
    """A fail-closed runtime or adapter protocol error."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def candidate_record_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def path_collision_key(path: Path) -> tuple[str, ...]:
    return tuple(
        unicodedata.normalize("NFC", part).casefold() for part in path.parts
    )


def atomic_json(path: Path, value: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}"
    try:
        with temporary.open("wb") as handle:
            handle.write(canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_text(path: Path, value: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(value)
            if not value.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeFailure("invalid-state", f"{path}: {error}") from error


def parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def time_order_key(value: Any) -> float:
    parsed = parse_time(value)
    if parsed is None:
        raise RuntimeFailure("invalid-updated-at", str(value))
    return parsed.timestamp()


def overlap_floor(watermark: Any, seconds: int) -> Any:
    if watermark is None:
        return None
    if isinstance(watermark, (int, float)):
        return max(0, watermark - seconds)
    parsed = parse_time(watermark)
    if parsed is None:
        raise RuntimeFailure(
            "unsupported-watermark",
            "overlap requires an epoch or RFC3339 settled watermark",
        )
    return (parsed - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


class ExecutableAdapter:
    """Strict JSON-lines client for an external adapter executable."""

    def __init__(
        self,
        argv: Iterable[str],
        role: str,
        timeout: int = 30,
        run_timeout: int | None = None,
    ):
        if role not in ROLES:
            raise RuntimeFailure("unknown-role", role)
        self.argv = [str(item) for item in argv]
        self.role = role
        self.timeout = timeout
        self.run_timeout = run_timeout or timeout
        self.identity = self._verify_contract()

    def _invoke(self, *args: Any, timeout: int | None = None) -> dict[str, Any]:
        command = self.argv + [str(arg) for arg in args]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as error:
            raise RuntimeFailure("adapter-unavailable", str(error)) from error
        try:
            stdout, stderr = process.communicate(timeout=timeout or self.timeout)
        except subprocess.TimeoutExpired as error:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            raise RuntimeFailure("adapter-timeout", str(error)) from error
        result = subprocess.CompletedProcess(
            command, process.returncode, stdout, stderr
        )
        objects: list[dict[str, Any]] = []
        try:
            for raw in result.stdout.splitlines():
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("JSON line is not an object")
                objects.append(value)
        except (json.JSONDecodeError, ValueError) as error:
            raise RuntimeFailure("malformed-adapter-output", str(error)) from error
        if not objects:
            raise RuntimeFailure(
                "empty-adapter-output",
                result.stderr.strip() or f"exit {result.returncode}",
            )
        response = objects[-1]
        if result.returncode != 0 or response.get("ok") is not True:
            detail = response.get("error", {})
            raise RuntimeFailure(
                str(detail.get("code", "adapter-failed")),
                str(detail.get("message", result.stderr.strip() or "adapter failed")),
            )
        return response

    def _verify_contract(self) -> dict[str, Any]:
        response = self._invoke("contract", "--role", self.role)
        expected = ROLES[self.role]
        if (
            response.get("protocol") != expected["protocol"]
            or response.get("version") != CONTRACT_VERSION
            or not isinstance(response.get("adapter_id"), str)
            or not response["adapter_id"]
        ):
            raise RuntimeFailure("contract-mismatch", f"{self.role} handshake rejected")
        capabilities = set(response.get("capabilities", []))
        missing = expected["capabilities"] - capabilities
        if missing:
            raise RuntimeFailure(
                "contract-mismatch", f"missing capabilities: {sorted(missing)}"
            )
        return response

    def call(self, command: str, **arguments: Any) -> dict[str, Any]:
        argv: list[str] = [command]
        for name, value in arguments.items():
            if value is None:
                continue
            argv.extend([f"--{name.replace('_', '-')}", str(value)])
        return self._invoke(
            *argv,
            timeout=self.run_timeout if command == "run" else self.timeout,
        )


def validate_identity(session: dict[str, Any], expected_source: str | None = None) -> None:
    required = {
        "source",
        "native_session_id",
        "qualified_session_id",
        "repository_scope",
        "updated_at",
        "source_revision",
        "event_frontier",
        "snapshot_digest",
        "completion_state",
        "adapter_version",
    }
    missing = required - session.keys()
    if missing:
        raise RuntimeFailure("invalid-identity", f"missing {sorted(missing)}")
    source = session["source"]
    native = session["native_session_id"]
    if expected_source is not None and source != expected_source:
        raise RuntimeFailure("source-mismatch", f"{source} != {expected_source}")
    if not isinstance(source, str) or not isinstance(native, str) or ":" in source:
        raise RuntimeFailure("invalid-identity", "source and native id must be opaque strings")
    if session["qualified_session_id"] != f"{source}:{native}":
        raise RuntimeFailure("invalid-identity", "qualified session id is not canonical")
    if session["completion_state"] not in {"active", "terminal", "quiet"}:
        raise RuntimeFailure("invalid-completion-state", session["completion_state"])
    if not isinstance(session["adapter_version"], int):
        raise RuntimeFailure("invalid-identity", "adapter_version must be an integer")


def same_revision(left: dict[str, Any], right: dict[str, Any]) -> bool:
    fields = (
        "qualified_session_id",
        "source_revision",
        "event_frontier",
        "snapshot_digest",
        "completion_state",
        "adapter_version",
    )
    return all(left.get(field) == right.get(field) for field in fields)


@dataclass
class RuntimePaths:
    state: Path
    data: Path
    skills: Path

    @property
    def discovery(self) -> Path:
        return self.state / "discovery.json"

    @property
    def queue(self) -> Path:
        return self.state / "queue.json"

    @property
    def unsettled(self) -> Path:
        return self.state / "unsettled.json"

    @property
    def ledger(self) -> Path:
        return self.state / "review-ledger.json"

    @property
    def attempts(self) -> Path:
        return self.state / "review-attempts.json"

    @property
    def transactions(self) -> Path:
        return self.state / "review-transactions.json"

    @property
    def snapshots(self) -> Path:
        return self.data / "snapshots"

    @property
    def review_evidence(self) -> Path:
        return self.state / "review-evidence.json"

    @property
    def bundles(self) -> Path:
        return self.data / "bundles"

    @property
    def estate_current(self) -> Path:
        return self.state / "estate-census-current.json"

    @property
    def estate_receipts(self) -> Path:
        return self.state / "estate-census-receipts"

    @property
    def estate_usage_current(self) -> Path:
        return self.state / "estate-usage-current.json"

    @property
    def estate_usage_receipts(self) -> Path:
        return self.state / "estate-usage-receipts"


class DreamingRuntime:
    def __init__(
        self,
        paths: RuntimePaths,
        routes: Iterable[tuple[str, str]],
        policy_version: int = 1,
        overlap_seconds: int = 300,
        quiet_retry_seconds: int = 300,
        max_snapshot_bytes: int = 100_000,
        max_events: int = 2_000,
        max_field_bytes: int = 64_000,
        max_autonomous_session_age_days: int = 30,
        allow_autonomous_skill_creation: bool = False,
        parent_run_id: str | None = None,
        now: Callable[[], int] | None = None,
    ):
        self.paths = paths
        self.routes = set(routes)
        self.policy_version = policy_version
        self.overlap_seconds = overlap_seconds
        self.quiet_retry_seconds = quiet_retry_seconds
        self.max_snapshot_bytes = max_snapshot_bytes
        self.max_events = max_events
        self.max_field_bytes = max_field_bytes
        self.max_autonomous_session_age_seconds = (
            max_autonomous_session_age_days * 24 * 60 * 60
        )
        self.allow_autonomous_skill_creation = allow_autonomous_skill_creation
        self.parent_run_id = parent_run_id
        self.now = now or (lambda: int(datetime.now(timezone.utc).timestamp()))

    def _state(self, path: Path, default: Any) -> Any:
        return read_json(path, default)

    def _write(self, path: Path, value: Any) -> None:
        atomic_json(path, value)

    def _route_allowed(self, source: str, executor_id: str) -> bool:
        return (source, executor_id) in self.routes

    def record_estate_census(
        self, census: dict[str, Any], receiver: dict[str, Any]
    ) -> dict[str, Any]:
        if census.get("schema_version") != 1:
            raise RuntimeFailure("estate-census-invalid", "schema version")
        host_id = census.get("host_id")
        collected_at = parse_time(census.get("collected_at"))
        if not isinstance(host_id, str) or not host_id:
            raise RuntimeFailure("estate-census-invalid", "host binding")
        if collected_at is None or collected_at.tzinfo is None:
            raise RuntimeFailure("estate-census-invalid", "collection time")
        snapshot_sha256 = census.get("snapshot_sha256")
        snapshot = {
            key: value for key, value in census.items() if key != "snapshot_sha256"
        }
        if not isinstance(snapshot_sha256, str) or digest(snapshot) != snapshot_sha256:
            raise RuntimeFailure("estate-census-invalid", "snapshot digest")
        required_receiver = {
            "receiver_id",
            "receiver_sha256",
            "collector_sha256",
        }
        if (
            not isinstance(receiver, dict)
            or not required_receiver.issubset(receiver)
            or not all(
                isinstance(receiver[key], str) and receiver[key]
                for key in required_receiver
            )
        ):
            raise RuntimeFailure("estate-census-invalid", "receiver identity")
        receipt = {
            "schema_version": 1,
            "snapshot_sha256": snapshot_sha256,
            "receiver": {
                key: receiver[key] for key in sorted(required_receiver)
            },
            "census": census,
        }
        receipt_sha256 = digest(receipt)
        receipt_path = (
            self.paths.estate_receipts
            / f"{receipt_sha256.removeprefix('sha256:')}.json"
        )
        if receipt_path.exists():
            if read_json(receipt_path, {}) != receipt:
                raise RuntimeFailure(
                    "estate-census-receipt-collision", receipt_sha256
                )
        else:
            atomic_json(receipt_path, receipt, mode=0o600)
        current = {
            "schema_version": 1,
            "receipt_sha256": receipt_sha256,
            "snapshot_sha256": snapshot_sha256,
            "census": census,
        }
        existing = read_json(self.paths.estate_current, {})
        existing_census = existing.get("census") if isinstance(existing, dict) else None
        existing_collected_at = (
            parse_time(existing_census.get("collected_at"))
            if isinstance(existing_census, dict)
            else None
        )
        is_current = (
            existing_collected_at is None
            or collected_at >= existing_collected_at
        )
        if is_current:
            atomic_json(self.paths.estate_current, current, mode=0o600)
        return {
            "status": "recorded" if is_current else "superseded",
            "receipt_sha256": receipt_sha256,
            "snapshot_sha256": snapshot_sha256,
            "complete": census.get("scope", {}).get("complete") is True,
            "totals": census.get("totals", {}),
        }

    def record_estate_usage(
        self,
        usage: dict[str, Any],
        receiver: dict[str, Any],
        census: dict[str, Any],
    ) -> dict[str, Any]:
        if usage.get("schema_version") != 1:
            raise RuntimeFailure("estate-usage-invalid", "schema version")
        snapshot_sha256 = usage.get("snapshot_sha256")
        snapshot = {
            key: value for key, value in usage.items() if key != "snapshot_sha256"
        }
        if not isinstance(snapshot_sha256, str) or digest(snapshot) != snapshot_sha256:
            raise RuntimeFailure("estate-usage-invalid", "snapshot digest")
        if (
            usage.get("census_snapshot_sha256") != census.get("snapshot_sha256")
            or usage.get("host_id") != census.get("host_id")
            or usage.get("collected_at") != census.get("collected_at")
        ):
            raise RuntimeFailure("estate-usage-invalid", "census binding")
        host_id = usage.get("host_id")
        collected_at = parse_time(usage.get("collected_at"))
        coverage = usage.get("coverage")
        if not isinstance(host_id, str) or not host_id:
            raise RuntimeFailure("estate-usage-invalid", "host binding")
        if collected_at is None or collected_at.tzinfo is None:
            raise RuntimeFailure("estate-usage-invalid", "collection time")
        if not isinstance(coverage, dict) or not isinstance(
            coverage.get("complete"), bool
        ):
            raise RuntimeFailure("estate-usage-invalid", "coverage")
        required_receiver = {
            "receiver_id",
            "receiver_sha256",
            "collector_sha256",
        }
        if (
            not isinstance(receiver, dict)
            or not required_receiver.issubset(receiver)
            or not all(
                isinstance(receiver[key], str) and receiver[key]
                for key in required_receiver
            )
        ):
            raise RuntimeFailure("estate-usage-invalid", "receiver identity")
        receipt = {
            "schema_version": 1,
            "snapshot_sha256": snapshot_sha256,
            "census_snapshot_sha256": census["snapshot_sha256"],
            "receiver": {
                key: receiver[key] for key in sorted(required_receiver)
            },
            "usage": usage,
        }
        receipt_sha256 = digest(receipt)
        receipt_path = (
            self.paths.estate_usage_receipts
            / f"{receipt_sha256.removeprefix('sha256:')}.json"
        )
        if receipt_path.exists():
            if read_json(receipt_path, {}) != receipt:
                raise RuntimeFailure(
                    "estate-usage-receipt-collision", receipt_sha256
                )
        else:
            atomic_json(receipt_path, receipt, mode=0o600)
        current = {
            "schema_version": 1,
            "receipt_sha256": receipt_sha256,
            "snapshot_sha256": snapshot_sha256,
            "census_snapshot_sha256": census["snapshot_sha256"],
            "usage": usage,
        }
        existing = read_json(self.paths.estate_usage_current, {})
        existing_usage = existing.get("usage") if isinstance(existing, dict) else None
        existing_collected_at = (
            parse_time(existing_usage.get("collected_at"))
            if isinstance(existing_usage, dict)
            else None
        )
        is_current = (
            existing_collected_at is None or collected_at >= existing_collected_at
        )
        if is_current:
            atomic_json(self.paths.estate_usage_current, current, mode=0o600)
        return {
            "status": "recorded" if is_current else "superseded",
            "receipt_sha256": receipt_sha256,
            "snapshot_sha256": snapshot_sha256,
            "complete": coverage.get("complete") is True,
        }

    def _mark_queue(self, qualified_session_id: str, revision: str, status: str) -> None:
        queue = self._state(self.paths.queue, [])
        changed = False
        for item in queue:
            if (
                item["qualified_session_id"] == qualified_session_id
                and item["source_revision"] == revision
            ):
                item["status"] = status
                changed = True
        if changed:
            self._write(self.paths.queue, queue)

    def _transaction_key(
        self, qualified_session_id: str, revision: str
    ) -> str:
        return digest(
            {
                "qualified_session_id": qualified_session_id,
                "source_revision": revision,
            }
        )

    def _transaction(
        self, qualified_session_id: str, revision: str
    ) -> dict[str, Any] | None:
        transactions = self._state(self.paths.transactions, {})
        return transactions.get(self._transaction_key(qualified_session_id, revision))

    def _pending_transaction_for_session(
        self, qualified_session_id: str
    ) -> dict[str, Any] | None:
        transactions = self._state(self.paths.transactions, {})
        session_entries = [
            (key, value)
            for key, value in transactions.items()
            if value.get("session_id") == qualified_session_id
        ]
        if not session_entries:
            return None
        completed_revisions = {
            row.get("source_revision")
            for row in self._state(self.paths.ledger, [])
            if row.get("session_id") == qualified_session_id
        }
        completed_keys = {
            key
            for key, value in session_entries
            if value.get("source_revision") in completed_revisions
        }
        if completed_keys:
            for key in completed_keys:
                del transactions[key]
            self._write(self.paths.transactions, transactions)
        return next(
            (
                value
                for key, value in session_entries
                if key not in completed_keys
            ),
            None,
        )

    def _write_transaction(
        self,
        qualified_session_id: str,
        revision: str,
        value: dict[str, Any],
    ) -> None:
        transactions = self._state(self.paths.transactions, {})
        transactions[self._transaction_key(qualified_session_id, revision)] = value
        self._write(self.paths.transactions, transactions)

    def _clear_transaction(
        self, qualified_session_id: str, revision: str
    ) -> None:
        transactions = self._state(self.paths.transactions, {})
        key = self._transaction_key(qualified_session_id, revision)
        if key in transactions:
            del transactions[key]
            self._write(self.paths.transactions, transactions)

    def _queue_session(self, session: dict[str, Any]) -> None:
        validate_identity(session)
        queue = self._state(self.paths.queue, [])
        qid = session["qualified_session_id"]
        revision = session["source_revision"]
        for item in queue:
            if item["qualified_session_id"] == qid and item["source_revision"] == revision:
                return
        for item in queue:
            if item["qualified_session_id"] == qid and item["status"] == "queued":
                item["status"] = "superseded"
                item["superseded_by"] = revision
        blocked = self._pending_transaction_for_session(qid) is not None
        queue.append(
            {
                **session,
                "status": "recovery-required" if blocked else "queued",
                "queued_at": self.now(),
            }
        )
        self._write(self.paths.queue, queue)

    def _record_unsettled(self, session: dict[str, Any]) -> None:
        unsettled = self._state(self.paths.unsettled, {})
        qid = session["qualified_session_id"]
        unsettled[qid] = {
            **session,
            "next_check_at": self.now() + self.quiet_retry_seconds,
        }
        self._write(self.paths.unsettled, unsettled)

    def _admit(self, session: dict[str, Any]) -> str:
        validate_identity(session)
        qid = session["qualified_session_id"]
        if session["completion_state"] == "active":
            self._record_unsettled(session)
            return "unsettled"
        if session["completion_state"] not in COMPLETED:
            raise RuntimeFailure("completion-not-admitted", session["completion_state"])
        unsettled = self._state(self.paths.unsettled, {})
        if qid in unsettled:
            del unsettled[qid]
            self._write(self.paths.unsettled, unsettled)
        self._queue_session(session)
        return "queued"

    def discover(
        self,
        source_name: str,
        source: ExecutableAdapter,
        page_size: int,
        max_pages: int = 1,
        before_cursor_commit: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if source.role != "session-source":
            raise RuntimeFailure("wrong-adapter-role", source.role)
        state = self._state(self.paths.discovery, {})
        current = state.get(source_name, {"settled_watermark": None, "generation": None})
        generation = current.get("generation")
        if generation is None:
            ceiling = source.call("watermark").get("watermark")
            generation = {
                "floor": overlap_floor(
                    current.get("settled_watermark"), self.overlap_seconds
                ),
                "ceiling": ceiling,
                "cursor": "",
                "last_key": None,
            }
            current["generation"] = generation
            state[source_name] = current
            self._write(self.paths.discovery, state)

        pages = 0
        while pages < max_pages:
            response = source.call(
                "list",
                floor=json.dumps(generation["floor"]),
                ceiling=json.dumps(generation["ceiling"]),
                cursor=generation["cursor"],
                page_size=page_size,
            )
            items = response.get("items")
            if not isinstance(items, list):
                raise RuntimeFailure("malformed-source-page", "items must be a list")
            keys: list[tuple[float, str]] = []
            for item in items:
                validate_identity(item, source_name)
                keys.append(
                    (time_order_key(item["updated_at"]), item["native_session_id"])
                )
            if keys != sorted(keys) or len(keys) != len(set(keys)):
                raise RuntimeFailure(
                    "unstable-source-page", "items are not in unique stable order"
                )
            prior_key = generation.get("last_key")
            if keys and prior_key is not None and keys[0] <= tuple(prior_key):
                raise RuntimeFailure(
                    "unstable-source-page", "page does not advance stable key order"
                )
            for item in items:
                self._admit(item)
            next_cursor = response.get("next_cursor")
            exhausted = response.get("exhausted")
            if not isinstance(next_cursor, str) or not isinstance(exhausted, bool):
                raise RuntimeFailure("malformed-source-page", "invalid page trailer")
            if not exhausted and next_cursor == generation["cursor"]:
                raise RuntimeFailure("stalled-source-page", "cursor did not advance")
            if before_cursor_commit is not None:
                before_cursor_commit(response)
            generation["cursor"] = next_cursor
            if keys:
                generation["last_key"] = list(keys[-1])
            current["generation"] = generation
            state[source_name] = current
            self._write(self.paths.discovery, state)
            pages += 1
            if exhausted:
                current["settled_watermark"] = generation["ceiling"]
                current["generation"] = None
                state[source_name] = current
                self._write(self.paths.discovery, state)
                break
        return current

    def revisit_unsettled(
        self, source_name: str, source: ExecutableAdapter
    ) -> list[str]:
        unsettled = self._state(self.paths.unsettled, {})
        outcomes: list[str] = []
        for qid, item in sorted(list(unsettled.items())):
            if item["source"] != source_name or item["next_check_at"] > self.now():
                continue
            try:
                latest = source.call("inspect", session=qid)["session"]
                validate_identity(latest, source_name)
                outcomes.append(self._admit(latest))
                unsettled = self._state(self.paths.unsettled, {})
            except RuntimeFailure as error:
                unsettled = self._state(self.paths.unsettled, {})
                if error.code == "session-missing":
                    unsettled.pop(qid, None)
                    self._write(self.paths.unsettled, unsettled)
                    outcomes.append("deleted")
                    continue
                item["next_check_at"] = self.now() + self.quiet_retry_seconds
                unsettled[qid] = item
                self._write(self.paths.unsettled, unsettled)
                outcomes.append("retry")
        return outcomes

    def render_snapshot(
        self,
        source_name: str,
        source: ExecutableAdapter,
        executor_id: str,
        qualified_session_id: str,
    ) -> tuple[Path, dict[str, Any]]:
        if not self._route_allowed(source_name, executor_id):
            raise RuntimeFailure(
                "route-denied", f"{source_name}>{executor_id} is not allowed"
            )
        inspected = source.call("inspect", session=qualified_session_id)["session"]
        validate_identity(inspected, source_name)
        if inspected["completion_state"] not in COMPLETED:
            self._admit(inspected)
            raise RuntimeFailure("completion-not-admitted", inspected["completion_state"])
        rendered = source.call("render", session=qualified_session_id)
        events = rendered.get("events")
        if not isinstance(events, list):
            raise RuntimeFailure("malformed-render", "events must be a list")
        if len(events) > self.max_events:
            raise RuntimeFailure("source-boundary-violation", "event limit exceeded")
        previous = -1
        for event in events:
            if not isinstance(event, dict):
                raise RuntimeFailure("malformed-render", "event must be an object")
            if event.get("source") != source_name:
                raise RuntimeFailure("source-mismatch", "event source differs")
            if event.get("qualified_session_id") != qualified_session_id:
                raise RuntimeFailure("identity-mismatch", "event session differs")
            if event.get("kind") not in EVENT_KINDS:
                raise RuntimeFailure("unknown-event-kind", str(event.get("kind")))
            sequence = event.get("sequence")
            if not isinstance(sequence, int) or sequence <= previous:
                raise RuntimeFailure("event-order", "sequence is not strictly increasing")
            if not isinstance(event.get("source_event_id"), str):
                raise RuntimeFailure("event-identity", "source_event_id is required")
            for field in ("text", "tool_name", "source_event_id"):
                value = event.get(field)
                if value is not None and not isinstance(value, str):
                    raise RuntimeFailure(
                        "malformed-render", f"{field} must be a string or null"
                    )
                if (
                    isinstance(value, str)
                    and len(value.encode("utf-8")) > self.max_field_bytes
                ):
                    raise RuntimeFailure(
                        "source-boundary-violation", f"{field} limit exceeded"
                    )
            previous = sequence
        if len(canonical(events)) > self.max_snapshot_bytes:
            raise RuntimeFailure("source-boundary-violation", "snapshot limit exceeded")
        if not isinstance(rendered.get("truncated", False), bool):
            raise RuntimeFailure("malformed-render", "truncated must be a boolean")
        calculated = digest(events)
        if calculated != inspected["snapshot_digest"]:
            raise RuntimeFailure("snapshot-digest-mismatch", qualified_session_id)
        snapshot_identity = {
            key: value for key, value in inspected.items() if key != "display_name"
        }
        snapshot = {
            "contract_version": CONTRACT_VERSION,
            "identity": snapshot_identity,
            "events": events,
            "truncated": bool(rendered.get("truncated", False)),
            "route": {
                "source": source_name,
                "executor": executor_id,
                "policy_version": self.policy_version,
            },
        }
        snapshot_digest = digest(snapshot)
        path = self.paths.snapshots / f"{snapshot_digest.removeprefix('sha256:')}.json"
        if path.exists():
            if path.read_bytes() != canonical(snapshot) + b"\n":
                raise RuntimeFailure("immutable-snapshot-collision", str(path))
        else:
            atomic_json(path, snapshot, mode=0o400)
        return path, inspected

    def _validated_review_result(self, result: dict[str, Any]) -> dict[str, Any]:
        if result.get("status") != "ok":
            raise RuntimeFailure("executor-failed", "review result status is not ok")
        if result.get("completion_sentinel") != "DREAMING_REVIEW_COMPLETE":
            raise RuntimeFailure("executor-failed", "completion sentinel missing")
        destination = result.get("terminal_route")
        if destination not in REVIEW_DESTINATIONS:
            raise RuntimeFailure(
                "malformed-executor-result", "terminal route is invalid"
            )
        for field in ("summary", "routing_reason"):
            value = result.get(field)
            if not isinstance(value, str) or not value.strip():
                raise RuntimeFailure(
                    "malformed-executor-result", f"{field} is required"
                )
            if len(value.encode("utf-8")) > 4_000:
                raise RuntimeFailure(
                    "malformed-executor-result", f"{field} is too large"
                )
        artifact = result.get("artifact")
        evidence_event_ids = result.get("evidence_event_ids")
        if destination in {"discard", "instruction", "factual_memory"}:
            if artifact is not None:
                raise RuntimeFailure(
                    "malformed-executor-result",
                    f"{destination} must not include an artifact",
                )
            if evidence_event_ids not in (None, []):
                raise RuntimeFailure(
                    "malformed-executor-result",
                    f"{destination} must not cite evidence events",
                )
            return result
        if not isinstance(artifact, dict):
            raise RuntimeFailure(
                "malformed-executor-result", "artifact is required"
            )
        operation = artifact.get("operation")
        if operation not in ARTIFACT_OPERATIONS:
            raise RuntimeFailure(
                "malformed-executor-result", "artifact operation is invalid"
            )
        if destination == "skill" and operation not in {"create", "patch"}:
            raise RuntimeFailure(
                "malformed-executor-result", "skill route operation is invalid"
            )
        if destination == "support_file" and operation != "support_file":
            raise RuntimeFailure(
                "malformed-executor-result",
                "support_file route requires support_file operation",
            )
        skill_name = artifact.get("skill_name")
        if (
            not isinstance(skill_name, str)
            or not SKILL_NAME_RE.fullmatch(skill_name)
            or skill_name in ORCHESTRATION_SKILLS
        ):
            raise RuntimeFailure(
                "malformed-executor-result", "artifact skill name is invalid"
            )
        skill_markdown = artifact.get("skill_markdown")
        if not isinstance(skill_markdown, str) or not skill_markdown.strip():
            raise RuntimeFailure(
                "malformed-executor-result", "skill_markdown is required"
            )
        if len(skill_markdown.encode("utf-8")) > 256_000:
            raise RuntimeFailure(
                "malformed-executor-result", "skill_markdown is too large"
            )
        frontmatter = re.match(r"^---\n(.*?)\n---\n", skill_markdown, re.S)
        if frontmatter is None:
            raise RuntimeFailure(
                "malformed-executor-result", "SKILL.md frontmatter is required"
            )
        name_match = re.search(
            r"(?m)^name:\s*([a-z0-9]+(?:-[a-z0-9]+)*)\s*$",
            frontmatter.group(1),
        )
        description_match = re.search(
            r"(?m)^description:\s*(\S.*)$", frontmatter.group(1)
        )
        if (
            name_match is None
            or name_match.group(1) != skill_name
            or description_match is None
        ):
            raise RuntimeFailure(
                "malformed-executor-result",
                "SKILL.md name and description must match the artifact",
            )
        support_files = artifact.get("support_files", [])
        if not isinstance(support_files, list):
            raise RuntimeFailure(
                "malformed-executor-result", "support_files must be a list"
            )
        seen: set[tuple[str, ...]] = set()
        support_paths: list[tuple[str, ...]] = []
        for item in support_files:
            if not isinstance(item, dict):
                raise RuntimeFailure(
                    "malformed-executor-result", "support file must be an object"
                )
            relative = item.get("path")
            content = item.get("content")
            relative_path = Path(relative) if isinstance(relative, str) else None
            collision_key = (
                path_collision_key(relative_path)
                if relative_path is not None
                else ()
            )
            if (
                not isinstance(relative, str)
                or not relative
                or relative_path is None
                or not relative_path.parts
                or relative_path.is_absolute()
                or relative_path.as_posix() != relative
                or ".." in relative_path.parts
                or collision_key in seen
                or collision_key[0] in RESERVED_SKILL_FILES
                or not isinstance(content, str)
            ):
                raise RuntimeFailure(
                    "malformed-executor-result", "support file is invalid"
                )
            if len(content.encode("utf-8")) > 256_000:
                raise RuntimeFailure(
                    "malformed-executor-result", "support file is too large"
                )
            seen.add(collision_key)
            support_paths.append(collision_key)
        for index, path in enumerate(support_paths):
            for other in support_paths[index + 1 :]:
                if (
                    len(path) < len(other)
                    and other[: len(path)] == path
                    or len(other) < len(path)
                    and path[: len(other)] == other
                ):
                    raise RuntimeFailure(
                        "malformed-executor-result",
                        "support file paths conflict",
                    )
        if operation != "support_file" and support_files:
            raise RuntimeFailure(
                "malformed-executor-result",
                "only support_file operations may include support files",
            )
        if operation == "support_file" and not support_files:
            raise RuntimeFailure(
                "malformed-executor-result",
                "support_file operation requires at least one support file",
            )
        if not isinstance(evidence_event_ids, list):
            raise RuntimeFailure(
                "evidence-anchor-invalid", "evidence_event_ids must be a list"
            )
        if any(not isinstance(item, str) or not item for item in evidence_event_ids):
            raise RuntimeFailure(
                "evidence-anchor-invalid",
                "evidence_event_ids must contain strings",
            )
        if (
            not evidence_event_ids
            or len(evidence_event_ids) > 20
            or len(evidence_event_ids) != len(set(evidence_event_ids))
        ):
            raise RuntimeFailure(
                "evidence-anchor-invalid",
                "artifact outcomes require 1 to 20 unique evidence event IDs",
            )
        return result

    def _validated_evidence_context(
        self,
        result: dict[str, Any],
        snapshot_path: Path,
        reviewed_identity: dict[str, Any],
    ) -> dict[str, Any] | None:
        if result["terminal_route"] not in {"skill", "support_file"}:
            return None
        snapshot = read_json(snapshot_path, {})
        events = snapshot.get("events")
        if not isinstance(events, list):
            raise RuntimeFailure("evidence-anchor-invalid", "snapshot events missing")
        available = {
            item.get("source_event_id"): index
            for index, item in enumerate(events)
            if isinstance(item, dict)
            and isinstance(item.get("source_event_id"), str)
        }
        requested = result["evidence_event_ids"]
        if any(item not in available for item in requested):
            raise RuntimeFailure(
                "evidence-anchor-invalid", "cited event is absent from snapshot"
            )
        ordered = sorted(requested, key=lambda item: available[item])
        if ordered != requested:
            raise RuntimeFailure(
                "evidence-anchor-invalid", "cited events are not in snapshot order"
            )
        snapshot_sha256 = snapshot_path.stem
        if not re.fullmatch(r"[0-9a-f]{64}", snapshot_sha256):
            raise RuntimeFailure(
                "evidence-anchor-invalid", "snapshot filename is not content addressed"
            )
        if digest(snapshot).removeprefix("sha256:") != snapshot_sha256:
            raise RuntimeFailure(
                "evidence-anchor-invalid", "snapshot object digest does not match path"
            )
        return {
            "schema_version": 1,
            "snapshot_sha256": snapshot_sha256,
            "source_revision": reviewed_identity["source_revision"],
            "event_ids": requested,
        }

    def _apply_autonomous_admission_policy(
        self,
        result: dict[str, Any],
        reviewed_identity: dict[str, Any],
    ) -> dict[str, Any]:
        artifact = result.get("artifact")
        if not isinstance(artifact, dict):
            return result
        source_updated_at = parse_time(reviewed_identity.get("updated_at"))
        if source_updated_at is None:
            raise RuntimeFailure(
                "source-time-invalid",
                "reviewed source updated_at is not comparable",
            )
        age_seconds = max(0, self.now() - int(source_updated_at.timestamp()))
        reason: str | None = None
        if age_seconds > self.max_autonomous_session_age_seconds:
            reason = "historical-source-outside-mutation-window"
        elif (
            artifact.get("operation") == "create"
            and not self.allow_autonomous_skill_creation
        ):
            reason = "autonomous-create-requires-recurrence"
        if reason is None:
            return result
        shadow_candidate = None
        if reason == "autonomous-create-requires-recurrence":
            shadow_candidate = self._collect_shadow_candidate(
                result,
                reviewed_identity,
            )
        deferred = dict(result)
        deferred_context = deferred.get("transcript_context")
        deferred["terminal_route"] = "discard"
        deferred["artifact"] = None
        deferred["evidence_event_ids"] = None
        deferred["transcript_context"] = None
        deferred["routing_reason"] = (
            f"Deferred by conservative autonomous admission policy: {reason}."
        )
        deferred["policy_deferred"] = {
            "reason": reason,
            "original_terminal_route": result["terminal_route"],
            "original_operation": artifact["operation"],
            "skill_name": artifact["skill_name"],
            "source_updated_at": reviewed_identity["updated_at"],
            **(
                {"transcript_context": deferred_context}
                if isinstance(deferred_context, dict)
                else {}
            ),
            **(
                {"shadow_candidate": shadow_candidate}
                if shadow_candidate is not None
                else {}
            ),
        }
        return deferred

    def _candidate_lifecycle_call(self, *arguments: str) -> dict[str, Any]:
        helper = Path(__file__).with_name("candidate-lifecycle.py")
        environment = os.environ.copy()
        environment["DREAMING_STATE_ROOT"] = str(self.paths.state)
        environment["DREAMING_DATA_ROOT"] = str(self.paths.data)
        environment["DREAMING_NOW_EPOCH"] = str(self.now())
        environment["SKILLS_NOW_EPOCH"] = str(self.now())
        environment.setdefault("SKILLS_STATE_DIR", str(self.paths.state))
        try:
            completed = subprocess.run(
                [str(helper), *arguments],
                check=False,
                text=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
        except OSError as error:
            raise RuntimeFailure("candidate-lifecycle-failed", str(error)) from error
        if completed.returncode != 0:
            raise RuntimeFailure(
                "candidate-lifecycle-failed",
                completed.stderr.strip() or f"exit {completed.returncode}",
            )
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeFailure(
                "candidate-lifecycle-failed",
                "helper returned malformed JSON",
            ) from error
        if not isinstance(response, dict) or (
            response.get("shadow_only") is not True
            and response.get("publication") != {"status": "shadow_only"}
        ):
            raise RuntimeFailure(
                "candidate-lifecycle-failed",
                "helper response is not shadow-only",
            )
        return response

    def _candidate_procedure(self, artifact: dict[str, Any]) -> dict[str, Any]:
        skill_markdown = artifact["skill_markdown"]
        frontmatter = re.match(r"^---\n(.*?)\n---\n", skill_markdown, re.S)
        description = re.search(
            r"(?m)^description:\s*(\S.*)$",
            frontmatter.group(1) if frontmatter else "",
        )
        if description is None:
            raise RuntimeFailure(
                "candidate-lifecycle-failed",
                "validated draft description is missing",
            )
        trigger = description.group(1).strip()
        descriptor = {
            "proposed_name": artifact["skill_name"],
            "trigger": trigger.casefold(),
        }
        return {
            "schema_version": 1,
            "trigger": trigger,
            "outcome": f"Complete the {artifact['skill_name']} procedure.",
            "actions": [
                "Follow the ordered instructions in the staged draft package."
            ],
            "exclusions": [f"Tasks not covered by this trigger: {trigger}"],
            "match_fingerprint": digest(descriptor),
        }

    def _collect_shadow_candidate(
        self,
        result: dict[str, Any],
        reviewed_identity: dict[str, Any],
    ) -> dict[str, Any]:
        artifact = result["artifact"]
        candidate_root = (self.paths.data / "candidates" / "v1").resolve()
        skills_root = self.paths.skills.resolve()
        if (
            candidate_root == skills_root
            or candidate_root in skills_root.parents
            or skills_root in candidate_root.parents
        ):
            raise RuntimeFailure(
                "candidate-lifecycle-failed",
                "candidate storage must be isolated from the skill discovery root",
            )
        procedure = self._candidate_procedure(artifact)
        observed = parse_time(reviewed_identity["updated_at"])
        if observed is None:
            raise RuntimeFailure(
                "candidate-lifecycle-failed",
                "reviewed source updated_at is invalid",
            )
        task_key = (
            "task:"
            + hashlib.sha256(
                reviewed_identity["qualified_session_id"].encode("utf-8")
            ).hexdigest()
        )
        observation = {
            "task_key": task_key,
            "session_id": reviewed_identity["qualified_session_id"],
            "observed_at": observed.astimezone(timezone.utc).isoformat(),
            "independence": "unverified",
            "summary": result["summary"],
            "procedure_fingerprint": procedure["match_fingerprint"],
        }

        lifecycle_id = None
        expected_version = None
        expected_identity = None
        listing = self._candidate_lifecycle_call("list")
        for item in listing.get("records", []):
            if not isinstance(item, dict) or not isinstance(
                item.get("lifecycle_id"), str
            ):
                raise RuntimeFailure(
                    "candidate-lifecycle-failed",
                    "candidate listing is malformed",
                )
            record = self._candidate_lifecycle_call(
                "read", item["lifecycle_id"]
            )
            if (
                record.get("proposed_name") == artifact["skill_name"]
                and record.get("procedure") == procedure
                and record.get("state")
                in {"collecting", "ready_for_draft", "expired", "rejected"}
            ):
                lifecycle_id = record["lifecycle_id"]
                expected_version = record["record_version"]
                expected_identity = candidate_record_digest(record)
                break

        staging_parent = self.paths.data / "candidates" / "v1" / "incoming"
        staging_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".candidate-", dir=staging_parent
        ) as temporary:
            package = Path(temporary)
            atomic_text(package / "SKILL.md", artifact["skill_markdown"])
            procedure_path = package.parent / f".{package.name}-procedure.json"
            observation_path = package.parent / f".{package.name}-observation.json"
            try:
                atomic_json(procedure_path, procedure)
                atomic_json(observation_path, observation)
                command = [
                    "collect",
                    "--procedure",
                    str(procedure_path),
                    "--observation",
                    str(observation_path),
                    "--package",
                    str(package),
                    "--proposed-name",
                    artifact["skill_name"],
                ]
                if lifecycle_id is not None:
                    command.extend(
                        [
                            "--lifecycle-id",
                            lifecycle_id,
                            "--match-outcome",
                            "same",
                            "--expected-version",
                            str(expected_version),
                            "--expected-record-sha256",
                            expected_identity,
                        ]
                    )
                collected = self._candidate_lifecycle_call(*command)
            finally:
                procedure_path.unlink(missing_ok=True)
                observation_path.unlink(missing_ok=True)

        evaluated = self._candidate_lifecycle_call(
            "evaluate",
            collected["lifecycle_id"],
            "--expected-version",
            str(collected["record_version"]),
            "--expected-record-sha256",
            collected["record_sha256"],
        )
        return {
            "candidate_id": evaluated["candidate_id"],
            "lifecycle_id": evaluated["lifecycle_id"],
            "recommendation": evaluated["recommendation"],
            "record_sha256": evaluated["record_sha256"],
            "record_version": evaluated["record_version"],
            "shadow_only": True,
            "state": evaluated["state"],
        }

    def _validated_draft_review(self, result: dict[str, Any]) -> dict[str, Any]:
        if result.get("status") != "ok":
            raise RuntimeFailure("draft-review-failed", "review status is not ok")
        if result.get("completion_sentinel") != "DREAMING_DRAFT_REVIEW_COMPLETE":
            raise RuntimeFailure("draft-review-failed", "completion sentinel missing")
        if result.get("decision") not in {"approve", "reject"}:
            raise RuntimeFailure("draft-review-malformed", "decision is invalid")
        for field in ("summary", "model"):
            value = result.get(field)
            if not isinstance(value, str) or not value.strip():
                raise RuntimeFailure(
                    "draft-review-malformed", f"{field} is required"
                )
            if len(value.encode("utf-8")) > 4_000:
                raise RuntimeFailure(
                    "draft-review-malformed", f"{field} is too large"
                )
        return result

    def _review_draft(
        self,
        reviewed_identity: dict[str, Any],
        proposing_executor: str,
        result: dict[str, Any],
        reviewers: list[tuple[str, ExecutableAdapter]],
    ) -> list[dict[str, Any]]:
        selected = reviewers[:2] if len(reviewers) > 1 else reviewers * 2
        packet = {
            "contract_version": CONTRACT_VERSION,
            "packet_kind": "draft_review",
            "source_revision": reviewed_identity["source_revision"],
            "snapshot_digest": reviewed_identity["snapshot_digest"],
            "proposing_executor": proposing_executor,
            "proposal": {
                "terminal_route": result["terminal_route"],
                "summary": result["summary"],
                "routing_reason": result["routing_reason"],
                "artifact": result["artifact"],
                "evidence_event_ids": result["evidence_event_ids"],
            },
        }
        packet_digest = digest(packet)
        packet_path = (
            self.paths.state
            / "draft-reviews"
            / f"{packet_digest.removeprefix('sha256:')}.json"
        )
        if packet_path.exists():
            if packet_path.read_bytes() != canonical(packet) + b"\n":
                raise RuntimeFailure(
                    "immutable-draft-review-collision", str(packet_path)
                )
        else:
            atomic_json(packet_path, packet, mode=0o400)

        reviews: list[dict[str, Any]] = []
        for slot, (reviewer_id, reviewer) in enumerate(selected, 1):
            doctor = reviewer.call("doctor")
            if not doctor.get("healthy") or not doctor.get("boundary_ready"):
                raise RuntimeFailure(
                    "executor-boundary-unavailable", reviewer_id
                )
            result_path = (
                self.paths.state
                / "draft-reviews"
                / (
                    f"{packet_digest.removeprefix('sha256:')}-"
                    f"{slot}-{hashlib.sha256(reviewer_id.encode()).hexdigest()}.result.json"
                )
            )
            response = reviewer.call(
                "run", snapshot=packet_path, result=result_path
            )
            if not result_path.exists():
                raise RuntimeFailure("missing-draft-review-result", reviewer_id)
            file_result = read_json(result_path, {})
            if file_result != {
                key: value for key, value in response.items() if key != "ok"
            }:
                raise RuntimeFailure("draft-review-channel-mismatch", reviewer_id)
            validated = self._validated_draft_review(response)
            review = {
                "slot": slot,
                "executor": reviewer_id,
                "model": validated["model"],
                "decision": validated["decision"],
                "summary": validated["summary"],
            }
            reviews.append(review)
            if validated["decision"] != "approve":
                raise RuntimeFailure("draft-review-rejected", reviewer_id)
        return reviews

    def _append_review_evidence(
        self,
        source_name: str,
        reviewed_identity: dict[str, Any],
        executor_id: str,
        result: dict[str, Any],
        artifact_commit: str | None,
    ) -> None:
        records = self._state(self.paths.review_evidence, [])
        if not isinstance(records, list):
            raise RuntimeFailure(
                "review-evidence-invalid", str(self.paths.review_evidence)
            )
        record = {
            "session_id": reviewed_identity["qualified_session_id"],
            "source": source_name,
            "source_revision": reviewed_identity["source_revision"],
            "review_executor": executor_id,
            "transfer_route": f"{source_name}>{executor_id}",
            "policy_version": self.policy_version,
            "destination": result["terminal_route"],
            "summary": result["summary"],
            "routing_reason": result["routing_reason"],
            "draft_reviews": result.get("draft_reviews", []),
            "artifact_commit": artifact_commit,
            "observed_at": self.now(),
        }
        if isinstance(reviewed_identity.get("display_name"), str):
            record["display_name"] = reviewed_identity["display_name"]
        if isinstance(result.get("transcript_context"), dict):
            record["transcript_context"] = result["transcript_context"]
        if isinstance(result.get("policy_deferred"), dict):
            record["policy_deferred"] = result["policy_deferred"]
        records.append(record)
        self._write(self.paths.review_evidence, records)

    def _git(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(self.paths.skills), *arguments],
                check=check,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            detail = getattr(error, "stderr", "") or str(error)
            raise RuntimeFailure("skill-git-failed", detail.strip()) from error

    def _apply_review_artifact(
        self,
        source_name: str,
        reviewed_identity: dict[str, Any],
        executor_id: str,
        result: dict[str, Any],
    ) -> str:
        artifact = result["artifact"]
        operation = artifact["operation"]
        skill_name = artifact["skill_name"]
        skills_root = self.paths.skills.resolve()
        target = skills_root / skill_name
        if target.parent != skills_root:
            raise RuntimeFailure("artifact-path-escaped", skill_name)
        if target.is_symlink():
            raise RuntimeFailure("artifact-path-symlink", str(target))
        if target.exists() and not target.is_dir():
            raise RuntimeFailure("artifact-path-invalid", str(target))
        exists = target.is_dir()
        if operation == "create" and exists:
            raise RuntimeFailure("skill-collision", skill_name)
        if operation in {"patch", "support_file"} and not exists:
            raise RuntimeFailure("skill-missing", skill_name)
        if exists:
            dirty = self._git("status", "--porcelain", "--", skill_name).stdout
            if dirty.strip():
                raise RuntimeFailure("skill-target-dirty", skill_name)
        else:
            tombstone = (
                Path(os.environ.get("DREAMING_REPO_ROOT", Path(__file__).parents[3]))
                / "skills/skill-review/scripts/check-tombstone.sh"
            )
            checked = subprocess.run(
                [str(tombstone), skill_name],
                env={
                    **os.environ,
                    "SKILLS_STATE_DIR": str(self.paths.state),
                    "SKILLS_LOCAL_ROOT": str(self.paths.skills),
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if checked.returncode == 0:
                raise RuntimeFailure("skill-tombstoned", skill_name)
            if checked.returncode != 1:
                raise RuntimeFailure(
                    "skill-tombstone-check-failed",
                    (checked.stderr or checked.stdout).strip(),
                )
        destinations: list[tuple[Path, str]] = []
        for item in artifact.get("support_files", []):
            destination = target / item["path"]
            resolved_parent = destination.parent.resolve()
            if resolved_parent != target and target not in resolved_parent.parents:
                raise RuntimeFailure("artifact-path-escaped", item["path"])
            current = destination.parent
            while current != target:
                if current.is_symlink():
                    raise RuntimeFailure("artifact-path-symlink", str(current))
                if current.exists() and not current.is_dir():
                    raise RuntimeFailure("artifact-path-invalid", str(current))
                current = current.parent
            if destination.is_symlink():
                raise RuntimeFailure("artifact-path-symlink", str(destination))
            if destination.exists() and not destination.is_file():
                raise RuntimeFailure("artifact-path-invalid", str(destination))
            destinations.append((destination, item["content"]))
        try:
            target.mkdir(parents=True, exist_ok=True)
            atomic_text(target / "SKILL.md", artifact["skill_markdown"])
            for destination, content in destinations:
                destination.parent.mkdir(parents=True, exist_ok=True)
                atomic_text(destination, content)
        except OSError as error:
            raise RuntimeFailure("artifact-mutation-failed", str(error)) from error
        marker = target / ".agent-created"
        envelope = target / ".agent-created.json"
        if operation == "create" or marker.exists():
            evidence = (
                Path(os.environ.get("DREAMING_REPO_ROOT", Path(__file__).parents[3]))
                / "skills/skill-review/scripts/evidence-envelope.py"
            )
            task_key = (
                "task:"
                + hashlib.sha256(
                    (
                        reviewed_identity["qualified_session_id"]
                        + "\0"
                        + reviewed_identity["source_revision"]
                    ).encode("utf-8")
                ).hexdigest()
            )
            command = [
                str(evidence),
                "upsert",
                str(envelope),
                "--skill",
                skill_name,
                "--session-id",
                reviewed_identity["qualified_session_id"],
                "--source-mode",
                "sweep",
                "--task-key",
                task_key,
                "--independence",
                "unverified",
                "--evidence-kind",
                "successful-procedure",
                "--summary",
                result["summary"],
                "--destination",
                result["terminal_route"],
                "--reason",
                result["routing_reason"],
                "--source",
                source_name,
                "--source-revision",
                reviewed_identity["source_revision"],
                "--review-executor",
                executor_id,
                "--transfer-route",
                f"{source_name}>{executor_id}",
                "--policy-version",
                str(self.policy_version),
            ]
            context = result.get("transcript_context")
            if isinstance(context, dict):
                command.extend(
                    [
                        "--snapshot-sha256",
                        context["snapshot_sha256"],
                        "--anchor-source-revision",
                        context["source_revision"],
                    ]
                )
                for event_id in context["event_ids"]:
                    command.extend(["--event-id", event_id])
            try:
                subprocess.run(
                    command,
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except (OSError, subprocess.CalledProcessError) as error:
                detail = getattr(error, "stderr", "") or str(error)
                raise RuntimeFailure(
                    "skill-evidence-failed", detail.strip()
                ) from error
            envelope_data = read_json(envelope, {})
            matching = next(
                (
                    item
                    for item in envelope_data.get("evidence", [])
                    if item.get("task_key") == task_key
                ),
                None,
            )
            if matching is None:
                raise RuntimeFailure("skill-evidence-failed", "evidence item missing")
            matching["draft_reviews"] = result.get("draft_reviews", [])
            try:
                atomic_json(envelope, envelope_data)
                marker.touch()
            except OSError as error:
                raise RuntimeFailure("artifact-mutation-failed", str(error)) from error
        self._git("add", "--", skill_name)
        message = (
            f"Learn {skill_name} from {source_name} session\n\n"
            f"Reviewed-by: {executor_id}\n"
            f"Source-revision: {reviewed_identity['source_revision']}"
        )
        self._git(
            "-c",
            "user.name=Dreaming",
            "-c",
            "user.email=dreaming@localhost",
            "commit",
            "-m",
            message,
            "--",
            skill_name,
        )
        return self._git("rev-parse", "HEAD").stdout.strip()

    def review(
        self,
        source_name: str,
        source: ExecutableAdapter,
        qualified_session_id: str,
        executors: list[tuple[str, ExecutableAdapter]],
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        attempts = self._state(self.paths.attempts, [])
        allowed = [
            (name, adapter)
            for name, adapter in executors
            if self._route_allowed(source_name, name)
        ]
        if not allowed:
            raise RuntimeFailure("no-allowed-executor", source_name)

        session_pending = self._pending_transaction_for_session(
            qualified_session_id
        )
        if session_pending is not None:
            pending_revision = session_pending.get("source_revision")
            if isinstance(pending_revision, str):
                self._mark_queue(
                    qualified_session_id,
                    pending_revision,
                    "recovery-required",
                )
            raise RuntimeFailure(
                "mutation-recovery-required",
                f"{qualified_session_id} has an unresolved transaction",
            )
        try:
            current = source.call("inspect", session=qualified_session_id)["session"]
        except RuntimeFailure as error:
            if (
                error.code != "session-missing"
                or error.message != qualified_session_id
            ):
                raise
            if expected_revision is not None:
                self._mark_queue(
                    qualified_session_id, expected_revision, "deleted"
                )
            return {"status": "deleted"}
        validate_identity(current, source_name)
        if (
            expected_revision is not None
            and current["source_revision"] != expected_revision
        ):
            self._mark_queue(
                qualified_session_id, expected_revision, "superseded"
            )
            admission = self._admit(current)
            return {
                "status": "stale-before-review",
                "latest_admission": admission,
                "queued_revision": current["source_revision"],
            }
        if source_name == "copilot":
            migration = self.migrate_legacy(
                current["native_session_id"], current
            )
            if migration == "ambiguous-hold":
                self._mark_queue(
                    qualified_session_id,
                    current["source_revision"],
                    "migration-hold",
                )
                return {"status": "migration-hold"}
        for row in self._state(self.paths.ledger, []):
            if row.get("session_id") != qualified_session_id:
                continue
            if row.get("source_revision") == current["source_revision"]:
                self._clear_transaction(
                    qualified_session_id, current["source_revision"]
                )
                self._mark_queue(
                    qualified_session_id, current["source_revision"], "reviewed"
                )
                return {"status": "already-reviewed"}
            migration = row.get("migration", {})
            if (
                row.get("source_revision") == "legacy-reviewed"
                and migration.get("status") == "baseline-seeded"
                and migration.get("event_frontier") == current["event_frontier"]
                and migration.get("snapshot_digest") == current["snapshot_digest"]
                and migration.get("adapter_version") == current["adapter_version"]
            ):
                self._mark_queue(
                    qualified_session_id, current["source_revision"], "reviewed"
                )
                return {"status": "already-reviewed-legacy-baseline"}

        last_failure: RuntimeFailure | None = None
        for executor_id, executor in allowed:
            mutation_started = False
            attempt_started_at = self.now()
            try:
                doctor = executor.call("doctor")
                if not doctor.get("healthy") or not doctor.get("boundary_ready"):
                    raise RuntimeFailure("executor-boundary-unavailable", executor_id)
                if not self._route_allowed(source_name, executor_id):
                    raise RuntimeFailure("route-denied", f"{source_name}>{executor_id}")
                snapshot_path, reviewed_identity = self.render_snapshot(
                    source_name,
                    source,
                    executor_id,
                    qualified_session_id,
                )
                if not self._route_allowed(source_name, executor_id):
                    raise RuntimeFailure("route-denied", f"{source_name}>{executor_id}")
                result_name = hashlib.sha256(
                    qualified_session_id.encode("utf-8")
                ).hexdigest()
                executor_name = hashlib.sha256(
                    executor_id.encode("utf-8")
                ).hexdigest()
                result_path = (
                    self.paths.state
                    / "results"
                    / f"{result_name}-{hashlib.sha256(current['source_revision'].encode()).hexdigest()}-{executor_name}.json"
                )
                result_path.parent.mkdir(parents=True, exist_ok=True)
                self._write_transaction(
                    qualified_session_id,
                    reviewed_identity["source_revision"],
                    {
                        "status": "prepared",
                        "session_id": qualified_session_id,
                        "source_revision": reviewed_identity["source_revision"],
                        "executor": executor_id,
                        "result_path": str(result_path),
                        "started_at": attempt_started_at,
                    },
                )
                try:
                    result = executor.call(
                        "run", snapshot=snapshot_path, result=result_path
                    )
                except RuntimeFailure:
                    self._clear_transaction(
                        qualified_session_id,
                        reviewed_identity["source_revision"],
                    )
                    raise
                if not result_path.exists():
                    self._clear_transaction(
                        qualified_session_id,
                        reviewed_identity["source_revision"],
                    )
                    raise RuntimeFailure("missing-executor-result", executor_id)
                file_result = read_json(result_path, {})
                if file_result != {key: value for key, value in result.items() if key != "ok"}:
                    self._clear_transaction(
                        qualified_session_id,
                        reviewed_identity["source_revision"],
                    )
                    raise RuntimeFailure("result-channel-mismatch", executor_id)
                result = self._validated_review_result(result)
                result["transcript_context"] = self._validated_evidence_context(
                    result,
                    snapshot_path,
                    reviewed_identity,
                )
                result = self._apply_autonomous_admission_policy(
                    result,
                    reviewed_identity,
                )
                attempt = {
                    "session_id": qualified_session_id,
                    "source": source_name,
                    "executor": executor_id,
                    "route": f"{source_name}>{executor_id}",
                    "policy_version": self.policy_version,
                    "status": result.get("status"),
                    "terminal_route": result.get("terminal_route"),
                    "mutation_started": False,
                    "started_at": attempt_started_at,
                    **(
                        {"parent_run_id": self.parent_run_id}
                        if self.parent_run_id
                        else {}
                    ),
                    **(
                        {"policy_deferred": result["policy_deferred"]}
                        if isinstance(result.get("policy_deferred"), dict)
                        else {}
                    ),
                }
                attempts.append(attempt)
                self._write(self.paths.attempts, attempts)

                if result["terminal_route"] in {"skill", "support_file"}:
                    result["draft_reviews"] = self._review_draft(
                        reviewed_identity,
                        executor_id,
                        result,
                        allowed,
                    )
                latest = source.call("inspect", session=qualified_session_id)["session"]
                validate_identity(latest, source_name)
                if not same_revision(reviewed_identity, latest):
                    attempt["status"] = "stale"
                    self._write(self.paths.attempts, attempts)
                    self._clear_transaction(
                        qualified_session_id,
                        reviewed_identity["source_revision"],
                    )
                    admission = self._admit(latest)
                    return {
                        "status": "stale",
                        "latest_admission": admission,
                        "queued_revision": latest["source_revision"],
                    }

                mutation_started = True
                self._write_transaction(
                    qualified_session_id,
                    reviewed_identity["source_revision"],
                    {
                        "status": "mutation-started",
                        "session_id": qualified_session_id,
                        "source_revision": reviewed_identity["source_revision"],
                        "executor": executor_id,
                        "result_path": str(result_path),
                        "terminal_route": result["terminal_route"],
                        "mutation_started": True,
                    },
                )
                artifact_commit = None
                if result["terminal_route"] in {"skill", "support_file"}:
                    artifact_commit = self._apply_review_artifact(
                        source_name,
                        reviewed_identity,
                        executor_id,
                        result,
                    )
                self._append_review_evidence(
                    source_name,
                    reviewed_identity,
                    executor_id,
                    result,
                    artifact_commit,
                )
                ledger = self._state(self.paths.ledger, [])
                ledger.append(
                    {
                        "session_id": qualified_session_id,
                        "source": source_name,
                        "source_revision": reviewed_identity["source_revision"],
                        "event_frontier": reviewed_identity["event_frontier"],
                        "snapshot_digest": reviewed_identity["snapshot_digest"],
                        "adapter_version": reviewed_identity["adapter_version"],
                        "review_executor": executor_id,
                        "transfer_route": f"{source_name}>{executor_id}",
                        "policy_version": self.policy_version,
                        "terminal_route": result["terminal_route"],
                        "summary": result["summary"],
                        "routing_reason": result["routing_reason"],
                        "draft_reviews": result.get("draft_reviews", []),
                        "artifact_commit": artifact_commit,
                        "reviewed_at": self.now(),
                        **(
                            {"policy_deferred": result["policy_deferred"]}
                            if isinstance(result.get("policy_deferred"), dict)
                            else {}
                        ),
                        **(
                            {"display_name": reviewed_identity["display_name"]}
                            if isinstance(reviewed_identity.get("display_name"), str)
                            else {}
                        ),
                    }
                )
                self._write(self.paths.ledger, ledger)
                self._mark_queue(
                    qualified_session_id,
                    reviewed_identity["source_revision"],
                    "reviewed",
                )
                self._clear_transaction(
                    qualified_session_id,
                    reviewed_identity["source_revision"],
                )
                return {
                    "status": (
                        "deferred"
                        if isinstance(result.get("policy_deferred"), dict)
                        else "accepted"
                    ),
                    "executor": executor_id,
                    "terminal_route": result["terminal_route"],
                    "artifact_mutated": artifact_commit is not None,
                    "artifact_commit": artifact_commit,
                    **(
                        {"policy_deferred": result["policy_deferred"]}
                        if isinstance(result.get("policy_deferred"), dict)
                        else {}
                    ),
                }
            except RuntimeFailure as error:
                last_failure = error
                if error.code == "mutation-recovery-required":
                    self._mark_queue(
                        qualified_session_id,
                        current["source_revision"],
                        "recovery-required",
                    )
                    raise
                if mutation_started:
                    attempts.append(
                        {
                            "session_id": qualified_session_id,
                            "source": source_name,
                            "executor": executor_id,
                            "route": f"{source_name}>{executor_id}",
                            "policy_version": self.policy_version,
                            "status": "mutation-recovery-required",
                            "error": error.code,
                            "mutation_started": True,
                            "started_at": attempt_started_at,
                            **(
                                {"parent_run_id": self.parent_run_id}
                                if self.parent_run_id
                                else {}
                            ),
                        }
                    )
                    self._write(self.paths.attempts, attempts)
                    raise RuntimeFailure("mutation-recovery-required", executor_id)
                self._clear_transaction(
                    qualified_session_id,
                    current["source_revision"],
                )
                attempts.append(
                    {
                        "session_id": qualified_session_id,
                        "source": source_name,
                        "executor": executor_id,
                        "route": f"{source_name}>{executor_id}",
                        "policy_version": self.policy_version,
                        "status": "failed-before-mutation",
                        "error": error.code,
                        "mutation_started": False,
                        "source_revision": current["source_revision"],
                        "started_at": attempt_started_at,
                        **(
                            {"parent_run_id": self.parent_run_id}
                            if self.parent_run_id
                            else {}
                        ),
                    }
                )
                self._write(self.paths.attempts, attempts)
                if error.code == "evidence-anchor-invalid":
                    failures = sum(
                        item.get("session_id") == qualified_session_id
                        and item.get("source_revision") == current["source_revision"]
                        and item.get("error") == "evidence-anchor-invalid"
                        for item in attempts
                    )
                    if failures >= 3:
                        self._mark_queue(
                            qualified_session_id,
                            current["source_revision"],
                            "recovery-required",
                        )
                        raise RuntimeFailure(
                            "evidence-anchor-recovery-required",
                            qualified_session_id,
                        )
        raise last_failure or RuntimeFailure("executor-failed", "all executors failed")

    def migrate_legacy(
        self,
        native_session_id: str,
        current: dict[str, Any],
    ) -> str:
        validate_identity(current, "copilot")
        ledger = self._state(self.paths.ledger, [])
        legacy = next(
            (
                row
                for row in ledger
                if row.get("session_id") == native_session_id
                and ":" not in str(row.get("session_id"))
            ),
            None,
        )
        if legacy is None:
            return "absent"
        reviewed = parse_time(legacy.get("reviewed_at"))
        updated = parse_time(current.get("updated_at"))
        if reviewed is None or updated is None:
            legacy["migration"] = {
                "status": "ambiguous-hold",
                "qualified_session_id": current["qualified_session_id"],
                "reason": "source and ledger timestamps are not comparable",
            }
            self._write(self.paths.ledger, ledger)
            return "ambiguous-hold"

        migrated = dict(legacy)
        migrated.update(
            {
                "session_id": current["qualified_session_id"],
                "source": "copilot",
                "source_revision": "legacy-reviewed",
                "legacy_session_id": native_session_id,
            }
        )
        if updated <= reviewed:
            migrated["migration"] = {
                "status": "baseline-seeded",
                "event_frontier": current["event_frontier"],
                "snapshot_digest": current["snapshot_digest"],
                "adapter_version": current["adapter_version"],
            }
            ledger[ledger.index(legacy)] = migrated
            self._write(self.paths.ledger, ledger)
            return "baseline-seeded"

        migrated["migration"] = {"status": "changed-since-legacy-review"}
        ledger[ledger.index(legacy)] = migrated
        self._write(self.paths.ledger, ledger)
        self._queue_session(current)
        return "queued-current-revision"

    def import_legacy_ledger(self, path: Path) -> int:
        if not path.exists():
            return 0
        ledger = self._state(self.paths.ledger, [])
        existing = {
            (str(row.get("session_id")), str(row.get("mode", "")))
            for row in ledger
        }
        imported = 0
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise RuntimeFailure("legacy-ledger-unreadable", str(error)) from error
        for line_number, raw in enumerate(lines, 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as error:
                raise RuntimeFailure(
                    "legacy-ledger-malformed",
                    f"{path}:{line_number}: {error.msg}",
                ) from error
            if not isinstance(row, dict) or not row.get("session_id"):
                raise RuntimeFailure(
                    "legacy-ledger-malformed",
                    f"{path}:{line_number}: session_id required",
                )
            session_id = str(row["session_id"])
            if ":" in session_id:
                continue
            key = (session_id, str(row.get("mode", "")))
            if key in existing:
                continue
            imported_row = dict(row)
            imported_row["legacy_import"] = {
                "source": "copilot",
                "path": str(path),
                "line": line_number,
            }
            ledger.append(imported_row)
            existing.add(key)
            imported += 1
        if imported:
            self._write(self.paths.ledger, ledger)
        return imported

    def materialize_bundle(self, skills_root: Path) -> tuple[Path, str]:
        skills_root.mkdir(parents=True, exist_ok=True)
        skill_inventory: list[dict[str, str]] = []
        for skill_root in sorted(skills_root.iterdir()):
            if skill_root.name.startswith("."):
                continue
            if skill_root.is_symlink():
                raise RuntimeFailure("bundle-symlink", str(skill_root))
            if not skill_root.is_dir() or not (skill_root / "SKILL.md").is_file():
                continue
            if skill_root.name in ORCHESTRATION_SKILLS:
                raise RuntimeFailure(
                    "orchestration-skill-leak", skill_root.name
                )
            for path in sorted(skill_root.rglob("*")):
                if path.is_symlink():
                    raise RuntimeFailure("bundle-symlink", str(path))
                if not path.is_file():
                    continue
                relative = path.relative_to(skills_root)
                skill_inventory.append(
                    {
                        "path": relative.as_posix(),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
        revision = "unversioned"
        try:
            revision = subprocess.run(
                ["git", "-C", str(skills_root), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            pass
        content_id = digest(
            {
                "files": skill_inventory,
                "skills_revision": revision,
                "orchestration_skills_absent": True,
            }
        )
        publication_name = (
            "dreaming-learned-" + content_id.removeprefix("sha256:")[:16]
        )
        skill_paths = sorted(
            {
                f"./{Path(item['path']).parts[0]}"
                for item in skill_inventory
                if len(Path(item["path"]).parts) > 1
                and Path(item["path"]).parts[1] == "SKILL.md"
            }
        )
        metadata = {
            ".claude-plugin/plugin.json": {
                "name": publication_name,
                "version": "1.0.0",
                "description": "Immutable learned skills published by Dreaming.",
                "skills": skill_paths,
            },
            ".claude-plugin/marketplace.json": {
                "name": publication_name,
                "metadata": {
                    "description": "Immutable learned skills published by Dreaming.",
                    "version": "1.0.0",
                },
                "owner": {"name": "Dreaming"},
                "plugins": [
                    {
                        "name": publication_name,
                        "description": "Immutable learned skills published by Dreaming.",
                        "version": "1.0.0",
                        "source": "./",
                    }
                ],
            },
            ".codex-plugin/plugin.json": {
                "name": publication_name,
                "version": "1.0.0",
                "description": "Immutable learned skills published by Dreaming.",
                "skills": "./",
            },
            ".agents/plugins/marketplace.json": {
                "name": publication_name,
                "interface": {"displayName": "Dreaming learned skills"},
                "plugins": [
                    {
                        "name": publication_name,
                        "source": {"source": "local", "path": "./"},
                        "policy": {
                            "installation": "AVAILABLE",
                            "authentication": "ON_INSTALL",
                        },
                        "category": "Productivity",
                    }
                ],
            },
        }
        metadata_bytes = {
            path: canonical(value) + b"\n" for path, value in metadata.items()
        }
        inventory = [
            *skill_inventory,
            *[
                {
                    "path": path,
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
                for path, content in sorted(metadata_bytes.items())
            ],
        ]
        proof = {
            "contract_version": CONTRACT_VERSION,
            "files": inventory,
            "skills_revision": revision,
            "orchestration_skills_absent": True,
            "publication_name": publication_name,
        }
        bundle_id = digest(proof)
        bundle = self.paths.bundles / bundle_id.removeprefix("sha256:")
        if not bundle.exists():
            staging = self.paths.bundles / f".staging-{uuid.uuid4().hex}"
            staging.mkdir(parents=True)
            for item in inventory:
                destination = staging / item["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                if item["path"] in metadata_bytes:
                    destination.write_bytes(metadata_bytes[item["path"]])
                else:
                    shutil.copyfile(skills_root / item["path"], destination)
            atomic_json(
                staging / "dreaming-bundle-manifest.json",
                {**proof, "bundle_id": bundle_id},
                mode=0o444,
            )
            for path in sorted(staging.rglob("*"), reverse=True):
                os.chmod(path, 0o555 if path.is_dir() else 0o444)
            os.chmod(staging, 0o555)
            self.paths.bundles.mkdir(parents=True, exist_ok=True)
            os.replace(staging, bundle)
        self.verify_bundle(bundle, bundle_id)
        return bundle, bundle_id

    def verify_bundle(self, bundle: Path, bundle_id: str) -> None:
        manifest_path = bundle / "dreaming-bundle-manifest.json"
        manifest = read_json(manifest_path, {})
        if manifest.get("bundle_id") != bundle_id:
            raise RuntimeFailure("bundle-id-mismatch", str(bundle))
        proof = {key: value for key, value in manifest.items() if key != "bundle_id"}
        if digest(proof) != bundle_id:
            raise RuntimeFailure("bundle-proof-invalid", str(bundle))
        expected = {
            item["path"]: item["sha256"] for item in manifest.get("files", [])
        }
        actual: dict[str, str] = {}
        for path in sorted(bundle.rglob("*")):
            if path.is_symlink():
                raise RuntimeFailure("bundle-symlink", str(path))
            if path.is_file() and path != manifest_path:
                relative = path.relative_to(bundle).as_posix()
                actual[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeFailure("bundle-inventory-mismatch", str(bundle))
        if any(Path(name).parts[0] in ORCHESTRATION_SKILLS for name in actual):
            raise RuntimeFailure("orchestration-skill-leak", str(bundle))
        for path in bundle.rglob("*"):
            if path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
                raise RuntimeFailure("mutable-bundle", str(path))

    def publish(
        self, publisher: ExecutableAdapter, skills_root: Path
    ) -> dict[str, Any]:
        if publisher.role != "skill-publisher":
            raise RuntimeFailure("wrong-adapter-role", publisher.role)
        doctor = publisher.call("doctor")
        if not doctor.get("healthy"):
            raise RuntimeFailure("publisher-unhealthy", publisher.identity["adapter_id"])
        bundle, bundle_id = self.materialize_bundle(skills_root)
        publisher.call("install", bundle=bundle, bundle_id=bundle_id)
        verified = publisher.call("verify", bundle_id=bundle_id)
        if (
            verified.get("verified") is not True
            or verified.get("bundle_id") != bundle_id
        ):
            raise RuntimeFailure("publisher-verification-failed", bundle_id)
        self.verify_bundle(bundle, bundle_id)
        return {"status": "published", "bundle": str(bundle), "bundle_id": bundle_id}


def default_paths() -> RuntimePaths:
    home = Path.home()
    data_base = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    state_base = Path(os.environ.get("XDG_STATE_HOME", home / ".local" / "state"))
    data = Path(os.environ.get("DREAMING_DATA_DIR", data_base / "dreaming"))
    state = Path(os.environ.get("DREAMING_STATE_DIR", state_base / "dreaming"))
    skills = Path(os.environ.get("DREAMING_SKILLS_ROOT", data / "skills"))
    return RuntimePaths(state=state, data=data, skills=skills)


def default_adapter_config(paths: RuntimePaths) -> Path:
    return Path(
        os.environ.get("DREAMING_ADAPTER_CONFIG", paths.state / "adapters.json")
    )


def load_adapter_config(path: Path) -> dict[str, Any]:
    config = read_json(path, None)
    if not isinstance(config, dict):
        raise RuntimeFailure("invalid-adapter-config", f"{path} must be an object")
    if config.get("contract_version") != CONTRACT_VERSION:
        raise RuntimeFailure(
            "invalid-adapter-config", "contract_version must be 1"
        )
    return config


def configured_evaluation_input_owner(
    config: dict[str, Any], config_path: Path, paths: RuntimePaths
) -> dict[str, Any] | None:
    entry = config.get("evaluation_input_owner")
    if entry is None:
        return None
    if (
        not isinstance(entry, dict)
        or set(entry) != EVALUATION_INPUT_OWNER_KEYS
        or not isinstance(entry.get("enabled"), bool)
    ):
        raise RuntimeFailure(
            "invalid-adapter-config", "evaluation_input_owner is malformed"
        )
    models = [
        entry.get("author_model"),
        entry.get("reviewer_a_model"),
        entry.get("reviewer_b_model"),
    ]
    if (
        not all(
            isinstance(model, str) and model and model == model.strip()
            for model in models
        )
        or len(set(models)) != 3
    ):
        raise RuntimeFailure(
            "invalid-adapter-config",
            "evaluation_input_owner models must be three distinct identities",
        )
    if os.environ.get("DREAMING_ADAPTER_CONFIG_MANAGED") != "1":
        raise RuntimeFailure(
            "evaluation-input-owner-unsealed",
            "automatic evaluation requires installation-managed configuration",
        )
    expected_digest = os.environ.get("DREAMING_ADAPTER_CONFIG_SHA256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise RuntimeFailure(
            "evaluation-input-owner-unsealed",
            "automatic evaluation requires an installation-sealed config digest",
        )
    expected_config = (paths.state / "adapters.json").resolve()
    if (
        config_path.is_symlink()
        or not config_path.is_file()
        or config_path.resolve() != expected_config
    ):
        raise RuntimeFailure(
            "evaluation-input-owner-unsealed",
            "automatic evaluation requires the canonical managed configuration",
        )
    expected_root = (paths.state / "evaluation-input-owner").resolve()
    configured_root = Path(str(entry.get("content_root")))
    if configured_root.is_symlink() or configured_root.resolve() != expected_root:
        raise RuntimeFailure(
            "invalid-adapter-config",
            "evaluation_input_owner content_root is not the fixed owner root",
        )
    evaluator = Path(__file__).with_name("skill-evaluation.py")
    if evaluator.is_symlink() or not evaluator.is_file():
        raise RuntimeFailure(
            "evaluation-input-owner-unsealed",
            "fixed evaluation-input owner executable is unavailable",
        )
    try:
        config_bytes = config_path.read_bytes()
        persisted_config = json.loads(config_bytes)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeFailure(
            "evaluation-input-owner-unsealed",
            f"managed adapter config cannot be verified: {error}",
        ) from error
    if persisted_config != config:
        raise RuntimeFailure(
            "evaluation-input-owner-unsealed",
            "managed adapter config changed while it was being loaded",
        )
    config_digest = hashlib.sha256(config_bytes).hexdigest()
    if config_digest != expected_digest:
        raise RuntimeFailure(
            "evaluation-input-owner-unsealed",
            "managed adapter config does not match its installed digest",
        )
    return {
        **entry,
        "content_root": str(expected_root),
        "config_sha256": "sha256:" + config_digest,
        "evaluator": str(evaluator),
    }


def read_canonical_control_json(
    path: Path, field: str
) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeFailure("evaluation-input-content-invalid", f"{field} is not a regular file")
    try:
        metadata = path.stat()
        if (
            metadata.st_uid != os.getuid()
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not metadata.st_mode & stat.S_IRUSR
        ):
            raise RuntimeFailure(
                "evaluation-input-content-invalid",
                f"{field} ownership or permissions are unsafe",
            )
        if metadata.st_size > EVALUATION_INPUT_MAX_CONTROL_BYTES:
            raise RuntimeFailure("evaluation-input-content-invalid", f"{field} is oversized")
        content = path.read_bytes()
        value = json.loads(content)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeFailure(
            "evaluation-input-content-invalid", f"{field} is unreadable: {error}"
        ) from error
    if not isinstance(value, dict) or content != canonical(value):
        raise RuntimeFailure(
            "evaluation-input-content-invalid", f"{field} is not canonical JSON"
        )
    return value, content


def require_owned_content_path(
    path: Path, field: str, *, executable: bool = False
) -> os.stat_result:
    if path.is_symlink() or not path.is_file():
        raise RuntimeFailure("evaluation-input-not-ready", f"{field} is not a regular file")
    try:
        metadata = path.stat()
    except OSError as error:
        raise RuntimeFailure(
            "evaluation-input-not-ready", f"{field} is unreadable: {error}"
        ) from error
    if metadata.st_uid != os.getuid() or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeFailure(
            "evaluation-input-not-ready", f"{field} ownership or permissions are unsafe"
        )
    if not metadata.st_mode & stat.S_IRUSR:
        raise RuntimeFailure("evaluation-input-not-ready", f"{field} is not readable")
    if executable and not metadata.st_mode & stat.S_IXUSR:
        raise RuntimeFailure("evaluation-input-not-ready", f"{field} is not executable")
    return metadata


def load_evaluation_input_root(owner: dict[str, Any]) -> dict[str, dict[str, Any]]:
    root = Path(owner["content_root"])
    if root.is_symlink() or not root.is_dir():
        raise RuntimeFailure(
            "evaluation-input-root-invalid", "evaluation-input content root is unavailable"
        )
    try:
        metadata = root.stat()
    except OSError as error:
        raise RuntimeFailure(
            "evaluation-input-root-invalid",
            f"evaluation-input content root is unreadable: {error}",
        ) from error
    if (
        metadata.st_uid != os.getuid()
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or not metadata.st_mode & stat.S_IRUSR
        or not metadata.st_mode & stat.S_IXUSR
    ):
        raise RuntimeFailure(
            "evaluation-input-root-invalid", "evaluation-input content root permissions are unsafe"
        )
    index_path = root / EVALUATION_INPUT_INDEX_NAME
    try:
        index, _ = read_canonical_control_json(
            index_path, "evaluation-input root index"
        )
    except RuntimeFailure as error:
        raise RuntimeFailure("evaluation-input-root-invalid", error.message) from error
    if (
        set(index) != {
            "schema_version",
            "kind",
            "capabilities",
            "record_sha256",
        }
        or index.get("schema_version") != 1
        or index.get("kind") != "evaluation_input_content_root_index"
        or not isinstance(index.get("capabilities"), list)
        or index.get("record_sha256")
        != digest({key: value for key, value in index.items() if key != "record_sha256"})
    ):
        raise RuntimeFailure(
            "evaluation-input-root-invalid", "evaluation-input root index identity is invalid"
        )
    entries: dict[str, dict[str, Any]] = {}
    directories: set[str] = set()
    for position, entry in enumerate(index["capabilities"]):
        if (
            not isinstance(entry, dict)
            or set(entry) != {"capability_id", "directory", "manifest_sha256"}
            or not isinstance(entry.get("capability_id"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", entry["capability_id"]) is None
            or not isinstance(entry.get("directory"), str)
            or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", entry["directory"]) is None
            or entry["directory"] in {".", ".."}
            or not isinstance(entry.get("manifest_sha256"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", entry["manifest_sha256"]) is None
        ):
            raise RuntimeFailure(
                "evaluation-input-root-invalid",
                f"evaluation-input root index entry {position} is malformed",
            )
        if entry["capability_id"] in entries or entry["directory"] in directories:
            raise RuntimeFailure(
                "evaluation-input-root-invalid", "evaluation-input root index entries collide"
            )
        capability_dir = root / entry["directory"]
        manifest_path = capability_dir / EVALUATION_INPUT_MANIFEST_NAME
        if (
            capability_dir.is_symlink()
            or not capability_dir.is_dir()
            or manifest_path.is_symlink()
            or not manifest_path.is_file()
        ):
            raise RuntimeFailure(
                "evaluation-input-root-invalid",
                "evaluation-input root index names an unavailable capability manifest",
            )
        try:
            if manifest_path.stat().st_size > EVALUATION_INPUT_MAX_CONTROL_BYTES:
                raise RuntimeFailure(
                    "evaluation-input-root-invalid",
                    "evaluation-input capability manifest is oversized",
                )
            manifest_bytes = manifest_path.read_bytes()
        except OSError as error:
            raise RuntimeFailure(
                "evaluation-input-root-invalid",
                f"evaluation-input capability manifest is unreadable: {error}",
            ) from error
        manifest_digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
        if manifest_digest != entry["manifest_sha256"]:
            raise RuntimeFailure(
                "evaluation-input-root-invalid",
                "evaluation-input root index manifest identity is stale",
            )
        entries[entry["capability_id"]] = dict(entry)
        directories.add(entry["directory"])
    try:
        root_entries = list(root.iterdir())
    except OSError as error:
        raise RuntimeFailure(
            "evaluation-input-root-invalid",
            f"evaluation-input content root is unreadable: {error}",
        ) from error
    actual = {
        path.name
        for path in root_entries
        if path.name != EVALUATION_INPUT_INDEX_NAME
    }
    if actual != directories or any(path.is_symlink() for path in root_entries):
        raise RuntimeFailure(
            "evaluation-input-root-invalid", "evaluation-input content root inventory differs from its index"
        )
    return entries


def validate_evaluation_input_capability(
    owner: dict[str, Any],
    entry: dict[str, Any],
    *,
    installed_skill_roots: Iterable[Path],
) -> dict[str, Any]:
    root = Path(owner["content_root"]).resolve()
    capability_dir = root / entry["directory"]
    installed_roots = tuple(path.resolve() for path in installed_skill_roots)
    if capability_dir.is_symlink() or not capability_dir.is_dir():
        raise RuntimeFailure(
            "evaluation-input-not-ready", "evaluation-input capability directory is unavailable"
        )
    try:
        directory_metadata = capability_dir.stat()
    except OSError as error:
        raise RuntimeFailure(
            "evaluation-input-not-ready",
            f"evaluation-input capability directory is unreadable: {error}",
        ) from error
    if (
        directory_metadata.st_uid != os.getuid()
        or directory_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or not directory_metadata.st_mode & stat.S_IRUSR
        or not directory_metadata.st_mode & stat.S_IXUSR
    ):
        raise RuntimeFailure(
            "evaluation-input-not-ready",
            "evaluation-input capability directory permissions are unsafe",
        )
    manifest_path = capability_dir / EVALUATION_INPUT_MANIFEST_NAME
    try:
        manifest, manifest_bytes = read_canonical_control_json(
            manifest_path, "evaluation-input capability manifest"
        )
    except RuntimeFailure as error:
        raise RuntimeFailure("evaluation-input-not-ready", error.message) from error
    if (
        "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
        != entry["manifest_sha256"]
    ):
        raise RuntimeFailure(
            "evaluation-input-not-ready",
            "evaluation-input capability manifest identity is stale",
        )
    if (
        set(manifest) != {"schema_version", "kind", "capability_id", "files"}
        or manifest.get("schema_version") != 1
        or manifest.get("kind") != "evaluation_input_capability_manifest"
        or manifest.get("capability_id") != entry["capability_id"]
        or not isinstance(manifest.get("files"), list)
    ):
        raise RuntimeFailure(
            "evaluation-input-not-ready", "evaluation-input capability manifest is malformed"
        )
    files: dict[str, Path] = {}
    declared_paths: set[str] = set()
    allowed_roles = {
        **EVALUATION_INPUT_CONTENT_ROLES,
        **EVALUATION_INPUT_SUPPORT_ROLES,
    }
    for position, item in enumerate(manifest["files"]):
        if (
            not isinstance(item, dict)
            or set(item) != {"role", "path", "size", "media_type", "sha256"}
            or item.get("role") not in allowed_roles
            or item.get("media_type")
            != allowed_roles.get(item.get("role"))
            or not isinstance(item.get("path"), str)
            or Path(item["path"]).is_absolute()
            or Path(item["path"]).as_posix() != item["path"]
            or not Path(item["path"]).parts
            or ".." in Path(item["path"]).parts
            or not isinstance(item.get("size"), int)
            or isinstance(item["size"], bool)
            or item["size"] < 0
            or item["size"] > EVALUATION_INPUT_MAX_FILE_BYTES
            or not isinstance(item.get("sha256"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", item["sha256"]) is None
        ):
            raise RuntimeFailure(
                "evaluation-input-not-ready",
                f"evaluation-input capability file {position} is malformed",
            )
        role = item["role"]
        logical_path = Path(item["path"])
        if (
            (
                role in EVALUATION_INPUT_CONTENT_ROLES
                and role in files
            )
            or item["path"] in declared_paths
            or (
                role == "fixture"
                and (
                    len(logical_path.parts) < 2
                    or logical_path.parts[0] != "fixtures"
                )
            )
            or (
                role == "grader"
                and (
                    len(logical_path.parts) < 2
                    or logical_path.parts[0] != "graders"
                )
            )
        ):
            raise RuntimeFailure(
                "evaluation-input-not-ready", "evaluation-input capability files collide"
            )
        path = capability_dir / item["path"]
        resolved = path.resolve()
        if not resolved.is_relative_to(capability_dir.resolve()):
            raise RuntimeFailure(
                "evaluation-input-not-ready", "evaluation-input capability file escapes its directory"
            )
        if any(
            resolved == installed.resolve()
            or resolved.is_relative_to(installed)
            for installed in installed_roots
        ):
            raise RuntimeFailure(
                "evaluation-input-not-ready", "evaluation-input capability file overlaps an installed skill root"
            )
        metadata = require_owned_content_path(
            path, f"evaluation-input {role}", executable=role == "harness"
        )
        if (
            metadata.st_size > EVALUATION_INPUT_MAX_FILE_BYTES
            or metadata.st_size != item["size"]
        ):
            raise RuntimeFailure(
                "evaluation-input-not-ready", f"evaluation-input {role} size is stale"
            )
        try:
            content = path.read_bytes()
        except OSError as error:
            raise RuntimeFailure(
                "evaluation-input-not-ready",
                f"evaluation-input {role} is unreadable: {error}",
            ) from error
        if (
            len(content) != item["size"]
            or "sha256:" + hashlib.sha256(content).hexdigest() != item["sha256"]
        ):
            raise RuntimeFailure(
                "evaluation-input-not-ready", f"evaluation-input {role} identity is stale"
            )
        if role == "harness":
            trusted_harness = Path(__file__).with_name("skill-evaluation-harness.py")
            trusted_metadata = require_owned_content_path(
                trusted_harness, "installed evaluation-input harness", executable=True
            )
            if trusted_metadata.st_size > EVALUATION_INPUT_MAX_FILE_BYTES:
                raise RuntimeFailure(
                    "evaluation-input-not-ready",
                    "installed evaluation-input harness is oversized",
                )
            try:
                trusted_harness_content = trusted_harness.read_bytes()
            except OSError as error:
                raise RuntimeFailure(
                    "evaluation-input-not-ready",
                    f"installed evaluation-input harness is unreadable: {error}",
                ) from error
            if content != trusted_harness_content:
                raise RuntimeFailure(
                    "evaluation-input-not-ready", "evaluation-input harness is not installation-authorized"
                )
        elif role in EVALUATION_INPUT_CONTENT_ROLES:
            try:
                if not isinstance(json.loads(content), dict):
                    raise ValueError("not an object")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                raise RuntimeFailure(
                    "evaluation-input-not-ready", f"evaluation-input {role} is not a JSON object"
                ) from error
        if role in EVALUATION_INPUT_CONTENT_ROLES:
            files[role] = (
                trusted_harness.resolve()
                if role == "harness"
                else resolved
            )
        declared_paths.add(item["path"])
    if set(files) != set(EVALUATION_INPUT_CONTENT_ROLES):
        raise RuntimeFailure(
            "evaluation-input-not-ready", "evaluation-input capability files are incomplete"
        )
    try:
        inventory = list(capability_dir.rglob("*"))
    except OSError as error:
        raise RuntimeFailure(
            "evaluation-input-not-ready",
            f"evaluation-input capability inventory is unreadable: {error}",
        ) from error
    actual = {
        path.relative_to(capability_dir).as_posix()
        for path in inventory
        if path.is_file() and path != manifest_path
    }
    expected_directories = {
        parent.as_posix()
        for declared in declared_paths
        for parent in Path(declared).parents
        if parent.as_posix() != "."
    }
    actual_directories = {
        path.relative_to(capability_dir).as_posix()
        for path in inventory
        if path.is_dir()
    }
    for directory in (
        path for path in inventory if path.is_dir()
    ):
        try:
            metadata = directory.stat()
        except OSError as error:
            raise RuntimeFailure(
                "evaluation-input-not-ready",
                f"evaluation-input support directory is unreadable: {error}",
            ) from error
        if (
            metadata.st_uid != os.getuid()
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not metadata.st_mode & stat.S_IRUSR
            or not metadata.st_mode & stat.S_IXUSR
        ):
            raise RuntimeFailure(
                "evaluation-input-not-ready",
                "evaluation-input support directory permissions are unsafe",
            )
    if (
        actual != declared_paths
        or actual_directories != expected_directories
        or any(path.is_symlink() for path in inventory)
    ):
        raise RuntimeFailure(
            "evaluation-input-not-ready", "evaluation-input capability inventory differs from its manifest"
        )
    return {
        "capability_id": entry["capability_id"],
        "manifest_sha256": entry["manifest_sha256"],
        "directory": str(capability_dir.resolve()),
        "files": {role: str(path) for role, path in sorted(files.items())},
    }


def validate_evaluation_input_seal_plan(
    source_root: Path, plan_path: Path
) -> list[dict[str, str]]:
    try:
        plan, raw = read_canonical_control_json(
            plan_path, "evaluation-input seal plan"
        )
    except RuntimeFailure as error:
        raise RuntimeFailure("evaluation-input-seal-invalid", error.message) from error
    capabilities = plan.get("capabilities") if isinstance(plan, dict) else None
    if (
        len(raw) > EVALUATION_INPUT_MAX_CONTROL_BYTES
        or set(plan) != {"schema_version", "kind", "capabilities"}
        or plan.get("schema_version") != 1
        or plan.get("kind") != "evaluation_input_seal_plan"
        or not isinstance(capabilities, list)
        or not capabilities
    ):
        raise RuntimeFailure(
            "evaluation-input-seal-invalid",
            "evaluation-input seal plan is malformed",
        )
    normalized: list[dict[str, str]] = []
    seen_capabilities: set[str] = set()
    seen_directories: set[str] = set()
    for position, item in enumerate(capabilities):
        if (
            not isinstance(item, dict)
            or set(item)
            != {
                "capability_id",
                "directory",
                "skill_path",
                "source_directory",
            }
            or re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(item.get("capability_id"))
            )
            is None
            or re.fullmatch(
                r"[a-z0-9][a-z0-9._-]{0,127}", str(item.get("directory"))
            )
            is None
            or item.get("directory") == EVALUATION_INPUT_INDEX_NAME
            or not isinstance(item.get("source_directory"), str)
            or Path(item["source_directory"]).is_absolute()
            or Path(item["source_directory"]).as_posix()
            != item["source_directory"]
            or ".." in Path(item["source_directory"]).parts
            or not isinstance(item.get("skill_path"), str)
            or not Path(item["skill_path"]).is_absolute()
        ):
            raise RuntimeFailure(
                "evaluation-input-seal-invalid",
                f"evaluation-input seal plan capability {position} is malformed",
            )
        capability_id = item["capability_id"]
        directory = item["directory"]
        if (
            capability_id in seen_capabilities
            or directory in seen_directories
        ):
            raise RuntimeFailure(
                "evaluation-input-seal-invalid",
                "evaluation-input seal plan identities collide",
            )
        source_directory = source_root / item["source_directory"]
        skill_path = Path(item["skill_path"])
        if (
            source_directory.is_symlink()
            or not source_directory.is_dir()
            or not source_directory.resolve().is_relative_to(
                source_root.resolve()
            )
            or skill_path.is_symlink()
            or not skill_path.is_dir()
        ):
            raise RuntimeFailure(
                "evaluation-input-seal-invalid",
                "evaluation-input seal plan path is unavailable",
            )
        seen_capabilities.add(capability_id)
        seen_directories.add(directory)
        normalized.append(
            {
                "capability_id": capability_id,
                "directory": directory,
                "skill_path": str(skill_path.resolve()),
                "source_directory": item["source_directory"],
            }
        )
    if normalized != sorted(
        normalized, key=lambda item: item["capability_id"]
    ):
        raise RuntimeFailure(
            "evaluation-input-seal-invalid",
            "evaluation-input seal plan must use canonical capability order",
        )
    return normalized


def read_evaluation_input_seal_source_tree(
    source_root: Path,
    source_directory: str,
) -> tuple[dict[str, bytes], set[str]]:
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    file_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        file_flags |= os.O_NOFOLLOW

    def safe_metadata(metadata: os.stat_result, *, directory: bool) -> bool:
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        return (
            expected(metadata.st_mode)
            and metadata.st_uid == os.getuid()
            and metadata.st_mode & stat.S_IRUSR
            and (not directory or metadata.st_mode & stat.S_IXUSR)
            and not metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        )

    def read_file(
        parent_descriptor: int,
        name: str,
        listed: os.stat_result,
        logical_path: str,
    ) -> bytes:
        descriptor = os.open(
            name, file_flags, dir_fd=parent_descriptor
        )
        try:
            before = os.fstat(descriptor)
            if (
                not safe_metadata(before, directory=False)
                or (before.st_dev, before.st_ino)
                != (listed.st_dev, listed.st_ino)
                or before.st_size > EVALUATION_INPUT_MAX_FILE_BYTES
            ):
                raise RuntimeFailure(
                    "evaluation-input-seal-invalid",
                    f"evaluation-input seal source file is unsafe: {logical_path}",
                )
            chunks = []
            remaining = EVALUATION_INPUT_MAX_FILE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            after = os.fstat(descriptor)
            stable = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) == (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            if (
                not stable
                or len(content) != before.st_size
                or len(content) > EVALUATION_INPUT_MAX_FILE_BYTES
            ):
                raise RuntimeFailure(
                    "evaluation-input-seal-invalid",
                    f"evaluation-input seal source changed while being read: {logical_path}",
                )
            return content
        finally:
            os.close(descriptor)

    def scan(
        descriptor: int,
        prefix: Path,
        files: dict[str, bytes],
        directories: set[str],
    ) -> None:
        try:
            names = sorted(os.listdir(descriptor))
        except OSError as error:
            raise RuntimeFailure(
                "evaluation-input-seal-invalid",
                f"evaluation-input seal source is unreadable: {error}",
            ) from error
        for name in names:
            if name in {"", ".", ".."} or "/" in name:
                raise RuntimeFailure(
                    "evaluation-input-seal-invalid",
                    "evaluation-input seal source has an invalid entry",
                )
            logical = prefix / name
            logical_text = logical.as_posix()
            try:
                metadata = os.stat(
                    name, dir_fd=descriptor, follow_symlinks=False
                )
            except OSError as error:
                raise RuntimeFailure(
                    "evaluation-input-seal-invalid",
                    f"evaluation-input seal source is unreadable: {error}",
                ) from error
            if stat.S_ISREG(metadata.st_mode):
                files[logical_text] = read_file(
                    descriptor, name, metadata, logical_text
                )
            elif stat.S_ISDIR(metadata.st_mode):
                child = os.open(name, directory_flags, dir_fd=descriptor)
                try:
                    opened = os.fstat(child)
                    if (
                        not safe_metadata(opened, directory=True)
                        or (opened.st_dev, opened.st_ino)
                        != (metadata.st_dev, metadata.st_ino)
                    ):
                        raise RuntimeFailure(
                            "evaluation-input-seal-invalid",
                            f"evaluation-input seal source directory is unsafe: {logical_text}",
                        )
                    directories.add(logical_text)
                    scan(child, logical, files, directories)
                finally:
                    os.close(child)
            else:
                raise RuntimeFailure(
                    "evaluation-input-seal-invalid",
                    f"evaluation-input seal source entry is unsafe: {logical_text}",
                )

    descriptor = os.open(source_root, directory_flags)
    try:
        if not safe_metadata(os.fstat(descriptor), directory=True):
            raise RuntimeFailure(
                "evaluation-input-seal-invalid",
                "evaluation-input seal source root is unsafe",
            )
        current = descriptor
        owned: list[int] = []
        try:
            for part in Path(source_directory).parts:
                child = os.open(part, directory_flags, dir_fd=current)
                metadata = os.fstat(child)
                if not safe_metadata(metadata, directory=True):
                    os.close(child)
                    raise RuntimeFailure(
                        "evaluation-input-seal-invalid",
                        "evaluation-input seal source directory is unsafe",
                    )
                owned.append(child)
                current = child
            files: dict[str, bytes] = {}
            directories: set[str] = set()
            scan(current, Path(), files, directories)
            return files, directories
        finally:
            for child in reversed(owned):
                os.close(child)
    except OSError as error:
        raise RuntimeFailure(
            "evaluation-input-seal-invalid",
            f"evaluation-input seal source is unavailable: {error}",
        ) from error
    finally:
        os.close(descriptor)


def make_evaluation_input_seal_directory(path: Path, root: Path) -> None:
    missing = []
    current = path
    while current != root and not current.exists():
        missing.append(current)
        current = current.parent
    if (
        current.is_symlink()
        or not current.is_dir()
        or not current.resolve().is_relative_to(root.resolve())
    ):
        raise RuntimeFailure(
            "evaluation-input-seal-invalid",
            "evaluation-input seal destination escapes its staging root",
        )
    os.chmod(current, 0o700)
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)


def copy_evaluation_input_seal_file(
    content: bytes,
    destination: Path,
    staging_root: Path,
    *,
    executable: bool = False,
) -> dict[str, Any]:
    if len(content) > EVALUATION_INPUT_MAX_FILE_BYTES:
        raise RuntimeFailure(
            "evaluation-input-seal-invalid",
            "evaluation-input seal source exceeds its size bound",
        )
    make_evaluation_input_seal_directory(
        destination.parent, staging_root
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(destination, flags, 0o700 if executable else 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    return {
        "size": len(content),
        "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
    }


def make_evaluation_input_seal_read_only(root: Path) -> None:
    inventory = sorted(
        root.rglob("*"), key=lambda path: len(path.parts), reverse=True
    )
    for path in inventory:
        if path.is_symlink():
            raise RuntimeFailure(
                "evaluation-input-seal-invalid",
                "evaluation-input seal staging content became a symlink",
            )
        executable = bool(path.stat().st_mode & stat.S_IXUSR)
        os.chmod(
            path,
            0o500 if path.is_dir() or executable else 0o400,
        )
    os.chmod(root, 0o700)


def read_evaluation_input_seal_trusted_file(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_size > EVALUATION_INPUT_MAX_FILE_BYTES
            ):
                raise RuntimeFailure(
                    "evaluation-input-seal-invalid",
                    "evaluation-input trusted source is unsafe",
                )
            content = b""
            while len(content) <= EVALUATION_INPUT_MAX_FILE_BYTES:
                chunk = os.read(descriptor, 65_536)
                if not chunk:
                    break
                content += chunk
            after = os.fstat(descriptor)
            if (
                len(content) != before.st_size
                or len(content) > EVALUATION_INPUT_MAX_FILE_BYTES
                or (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                )
            ):
                raise RuntimeFailure(
                    "evaluation-input-seal-invalid",
                    "evaluation-input trusted source changed while being read",
                )
            return content
        finally:
            os.close(descriptor)
    except OSError as error:
        raise RuntimeFailure(
            "evaluation-input-seal-invalid",
            f"evaluation-input trusted source is unavailable: {error}",
        ) from error


def validate_sealed_evaluation_input_packet(
    evaluator: Path,
    skill_path: str,
    primary_files: dict[str, Path],
) -> None:
    with tempfile.TemporaryDirectory(
        prefix="evaluation-input-packet."
    ) as temporary:
        output = Path(temporary) / "packet.json"
        result = subprocess.run(
            [
                sys.executable,
                str(evaluator),
                "v2-input-author-packet",
                skill_path,
                "--suite",
                str(primary_files["suite"]),
                "--policy",
                str(primary_files["policy"]),
                "--config",
                str(primary_files["compilation"]),
                "--routing",
                str(primary_files["routing"]),
                "--harness",
                str(primary_files["harness"]),
                "--catalog",
                str(primary_files["catalog"]),
                "--output",
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        raise RuntimeFailure(
            "evaluation-input-seal-invalid",
            (result.stderr or result.stdout).strip()[-1000:]
            or "evaluation-input author packet validation refused",
        )
    try:
        values = [
            json.loads(line)
            for line in result.stdout.splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as error:
        raise RuntimeFailure(
            "evaluation-input-seal-invalid",
            f"evaluation-input author packet result is malformed: {error}",
        ) from error
    if len(values) != 1 or not isinstance(values[0], dict):
        raise RuntimeFailure(
            "evaluation-input-seal-invalid",
            "evaluation-input author packet result is not singular",
        )


def seal_evaluation_input_root(
    source_root: Path,
    plan_path: Path,
    output_root: Path,
    *,
    installed_skill_roots: Iterable[Path],
) -> dict[str, Any]:
    source_root = source_root.resolve()
    output_parent = output_root.parent.resolve()
    if (
        source_root.is_symlink()
        or not source_root.is_dir()
        or output_root.exists()
        or output_root.is_symlink()
        or not output_parent.is_dir()
        or output_root.resolve().is_relative_to(source_root)
        or source_root.is_relative_to(output_root.resolve())
    ):
        raise RuntimeFailure(
            "evaluation-input-seal-invalid",
            "evaluation-input seal roots are unsafe or overlapping",
        )
    capabilities = validate_evaluation_input_seal_plan(
        source_root, plan_path
    )
    installed_roots = tuple(installed_skill_roots)
    evaluator = Path(__file__).with_name("skill-evaluation.py")
    harness = Path(__file__).with_name("skill-evaluation-harness.py")
    if evaluator.is_symlink() or not evaluator.is_file():
        raise RuntimeFailure(
            "evaluation-input-seal-invalid",
            "evaluation-input evaluator is unavailable",
        )
    harness_content = read_evaluation_input_seal_trusted_file(harness)
    entries = []
    with tempfile.TemporaryDirectory(
        prefix=".evaluation-input-seal.", dir=output_parent
    ) as temporary:
        staging = Path(temporary) / "root"
        staging.mkdir(mode=0o700)
        for capability in capabilities:
            source_files, source_directories = (
                read_evaluation_input_seal_source_tree(
                    source_root, capability["source_directory"]
                )
            )
            primary_names = {
                "suite": "suite.json",
                "policy": "policy.json",
                "compilation": "compilation.json",
                "routing": "routing.json",
                "catalog": "authoring-catalog.json",
            }
            required_primary = set(primary_names.values())
            support_files = {
                role: sorted(
                    path
                    for path in source_files
                    if path.startswith(f"{directory_name}/")
                )
                for role, directory_name in (
                    ("fixture", "fixtures"),
                    ("grader", "graders"),
                )
            }
            allowed_directories = {
                path
                for path in source_directories
                if path == "fixtures"
                or path.startswith("fixtures/")
                or path == "graders"
                or path.startswith("graders/")
            }
            if (
                not required_primary <= set(source_files)
                or any(not paths for paths in support_files.values())
                or set(source_directories) != allowed_directories
                or set(source_files)
                != required_primary
                | set(support_files["fixture"])
                | set(support_files["grader"])
            ):
                raise RuntimeFailure(
                    "evaluation-input-seal-invalid",
                    "evaluation-input seal source pack inventory is malformed",
                )
            destination = staging / capability["directory"]
            destination.mkdir(mode=0o700)
            primary_sources = {
                role: source_files[name]
                for role, name in primary_names.items()
            }
            primary_sources["harness"] = harness_content
            primary_files: dict[str, Path] = {}
            records = []
            for role, content in primary_sources.items():
                relative = (
                    "skill-evaluation-harness.py"
                    if role == "harness"
                    else primary_names[role]
                )
                target = destination / relative
                facts = copy_evaluation_input_seal_file(
                    content,
                    target,
                    staging,
                    executable=role == "harness",
                )
                primary_files[role] = harness if role == "harness" else target
                records.append(
                    {
                        "role": role,
                        "path": relative,
                        "size": facts["size"],
                        "media_type": EVALUATION_INPUT_CONTENT_ROLES[role],
                        "sha256": facts["sha256"],
                    }
                )
            for role, directory_name in (
                ("fixture", "fixtures"),
                ("grader", "graders"),
            ):
                for relative in support_files[role]:
                    facts = copy_evaluation_input_seal_file(
                        source_files[relative],
                        destination / relative,
                        staging,
                    )
                    records.append(
                        {
                            "role": role,
                            "path": relative,
                            "size": facts["size"],
                            "media_type": EVALUATION_INPUT_SUPPORT_ROLES[role],
                            "sha256": facts["sha256"],
                        }
                    )
            records.sort(key=lambda item: (item["role"], item["path"]))
            manifest = {
                "schema_version": 1,
                "kind": "evaluation_input_capability_manifest",
                "capability_id": capability["capability_id"],
                "files": records,
            }
            manifest_path = destination / EVALUATION_INPUT_MANIFEST_NAME
            manifest_path.write_bytes(canonical(manifest))
            os.chmod(manifest_path, 0o600)
            validate_sealed_evaluation_input_packet(
                evaluator, capability["skill_path"], primary_files
            )
            entries.append(
                {
                    "capability_id": capability["capability_id"],
                    "directory": capability["directory"],
                    "manifest_sha256": "sha256:"
                    + hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                }
            )
        index = {
            "schema_version": 1,
            "kind": "evaluation_input_content_root_index",
            "capabilities": entries,
        }
        index["record_sha256"] = digest(index)
        index_path = staging / EVALUATION_INPUT_INDEX_NAME
        index_path.write_bytes(canonical(index))
        os.chmod(index_path, 0o600)
        owner = {"content_root": str(staging)}
        indexed = load_evaluation_input_root(owner)
        for capability in capabilities:
            validate_evaluation_input_capability(
                owner,
                indexed[capability["capability_id"]],
                installed_skill_roots=installed_roots,
            )
        make_evaluation_input_seal_read_only(staging)
        os.replace(staging, output_root)
        os.chmod(output_root, 0o500)
    return {
        "status": "sealed",
        "output_root": str(output_root.resolve()),
        "capability_count": len(entries),
        "capability_ids": [
            entry["capability_id"] for entry in entries
        ],
        "root_index_sha256": "sha256:"
        + hashlib.sha256(
            (output_root / EVALUATION_INPUT_INDEX_NAME).read_bytes()
        ).hexdigest(),
    }


def evaluation_input_usage_state(
    capability_id: str, usage: dict[str, Any], usage_row: dict[str, Any]
) -> str:
    uses_30d = usage_row.get("uses_30d")
    if isinstance(uses_30d, bool) or not isinstance(uses_30d, int) or uses_30d < 0:
        raise RuntimeFailure(
            "evaluation-input-evidence-invalid",
            "evaluation-input usage count is malformed",
        )
    if uses_30d > 0:
        return "used_30d"
    collected_at = parse_time(usage.get("collected_at"))
    if collected_at is None or collected_at.tzinfo is None:
        raise RuntimeFailure(
            "evaluation-input-evidence-invalid",
            "evaluation-input usage collection time is malformed",
        )
    coverage = usage.get("coverage")
    pending = coverage.get("pending") if isinstance(coverage, dict) else None
    failures = coverage.get("failures") if isinstance(coverage, dict) else None
    unattributed = usage.get("unattributed")
    if (
        not isinstance(coverage, dict)
        or not isinstance(coverage.get("complete"), bool)
        or not isinstance(pending, list)
        or not isinstance(failures, list)
        or not isinstance(unattributed, list)
    ):
        raise RuntimeFailure(
            "evaluation-input-evidence-invalid",
            "evaluation-input usage coverage is malformed",
        )
    window_start = collected_at - timedelta(days=30)

    def intersects_window(item: dict[str, Any]) -> bool:
        modified_at = parse_time(item.get("modified_at"))
        return modified_at is None or modified_at >= window_start

    stable_backlog = [
        item
        for item in pending
        if isinstance(item, dict)
        and intersects_window(item)
        and item.get("reason") != "events_recently_modified"
    ]
    relevant_failures = [
        item
        for item in failures
        if isinstance(item, dict)
        and intersects_window(item)
        and (
            not item.get("candidate_capability_ids")
            or capability_id in item.get("candidate_capability_ids", [])
        )
    ]
    identity_blocked = any(
        isinstance(item, dict)
        and capability_id in item.get("candidate_capability_ids", [])
        for item in unattributed
    ) or any(item.get("candidate_capability_ids") for item in relevant_failures)
    stable_session_ids = {
        item.get("session_id")
        for item in stable_backlog
        if isinstance(item.get("session_id"), str)
    }
    stable_failure = any(
        not item.get("candidate_capability_ids")
        and item.get("session_id") not in stable_session_ids
        for item in relevant_failures
    )
    if coverage["complete"]:
        return "complete_zero_30d"
    if identity_blocked:
        return "blocked_identity"
    if stable_backlog or stable_failure:
        return "blocked_stable_backlog"
    return "settled_zero_30d"


def evaluation_input_queue_priority(
    evaluation: dict[str, Any],
    usage_state: str,
    *,
    routing_conflict: bool,
    root_class: str,
    plugin_complete: bool,
) -> tuple[int, str] | None:
    if usage_state in {"complete_zero_30d", "settled_zero_30d"}:
        return 1, "unused_30d"
    state = evaluation["state"]
    if state in {
        "missing",
        "input_missing",
        "drafting",
        "review_required",
        "insufficient_information",
        "inconclusive",
        "invalid",
        "ready",
    }:
        return 2, "evaluation_missing_or_invalid"
    if state == "regression" or routing_conflict:
        return 3, "regression_or_routing_conflict"
    if any(
        isinstance(item, dict)
        and item.get("evaluation_class") == "overlap"
        and item.get("comparable") is True
        for item in evaluation.get("cases", [])
    ):
        return 4, "overlapping_capability"
    if root_class == "plugin" and plugin_complete:
        return 5, "complete_plugin_package"
    if state == "stale" and evaluation.get("status") == "pass":
        return 6, "stale_passing_evaluation"
    return None


def derive_evaluation_input_queue(
    owner: dict[str, Any],
    census: dict[str, Any],
    usage: dict[str, Any],
    receiver: dict[str, Any],
    *,
    census_receipt_sha256: str,
    usage_receipt_sha256: str,
) -> dict[str, Any]:
    census_snapshot = {
        key: value for key, value in census.items() if key != "snapshot_sha256"
    }
    usage_snapshot = {
        key: value for key, value in usage.items() if key != "snapshot_sha256"
    }
    receiver_keys = {"receiver_id", "receiver_sha256", "collector_sha256"}
    if (
        not isinstance(receiver, dict)
        or not receiver_keys.issubset(receiver)
        or not all(
            isinstance(receiver[key], str) and receiver[key]
            for key in receiver_keys
        )
    ):
        raise RuntimeFailure(
            "evaluation-input-evidence-invalid",
            "evaluation-input receiver identity is malformed",
        )
    receipt_receiver = {
        key: receiver[key] for key in sorted(receiver_keys)
    }
    expected_census_receipt = digest(
        {
            "schema_version": 1,
            "snapshot_sha256": census.get("snapshot_sha256"),
            "receiver": receipt_receiver,
            "census": census,
        }
    )
    expected_usage_receipt = digest(
        {
            "schema_version": 1,
            "snapshot_sha256": usage.get("snapshot_sha256"),
            "census_snapshot_sha256": census.get("snapshot_sha256"),
            "receiver": receipt_receiver,
            "usage": usage,
        }
    )
    if (
        digest(census_snapshot) != census.get("snapshot_sha256")
        or digest(usage_snapshot) != usage.get("snapshot_sha256")
        or census.get("scope", {}).get("complete") is not True
        or census.get("evidence", {}).get("evaluation_inventory", {}).get("complete")
        is not True
        or usage.get("census_snapshot_sha256") != census.get("snapshot_sha256")
        or usage.get("host_id") != census.get("host_id")
        or usage.get("collected_at") != census.get("collected_at")
        or census_receipt_sha256 != expected_census_receipt
        or usage_receipt_sha256 != expected_usage_receipt
    ):
        raise RuntimeFailure(
            "evaluation-input-evidence-invalid",
            "evaluation-input queue evidence is incomplete or cross-run",
        )
    physical = census.get("physical_instances")
    enabled = census.get("enabled_instances")
    usage_rows = usage.get("canonical_usage")
    unresolved = census.get("unresolved_mappings")
    plugins = census.get("plugins")
    coverage = usage.get("coverage")
    unattributed = usage.get("unattributed")
    if (
        not isinstance(physical, list)
        or not isinstance(enabled, list)
        or not isinstance(usage_rows, list)
        or not isinstance(unresolved, list)
        or not isinstance(plugins, list)
        or not isinstance(coverage, dict)
        or not isinstance(coverage.get("failures"), list)
        or not isinstance(unattributed, list)
    ):
        raise RuntimeFailure(
            "evaluation-input-evidence-invalid",
            "evaluation-input queue inventory is malformed",
        )
    for item in unattributed:
        candidate_ids = (
            item.get("candidate_capability_ids")
            if isinstance(item, dict)
            else None
        )
        if not isinstance(candidate_ids, list) or not all(
            isinstance(value, str) for value in candidate_ids
        ):
            raise RuntimeFailure(
                "evaluation-input-evidence-invalid",
                "evaluation-input usage attribution is malformed",
            )
    for item in coverage["failures"]:
        candidate_ids = (
            item.get("candidate_capability_ids")
            if isinstance(item, dict)
            else None
        )
        if not isinstance(candidate_ids, list) or not all(
            isinstance(value, str) for value in candidate_ids
        ):
            raise RuntimeFailure(
                "evaluation-input-evidence-invalid",
                "evaluation-input usage failure attribution is malformed",
            )
    for item in unresolved:
        if not isinstance(item, dict):
            raise RuntimeFailure(
                "evaluation-input-evidence-invalid",
                "evaluation-input unresolved mapping is malformed",
            )
        if item.get("reason") == "multiply_mapped":
            candidate_ids = item.get("candidate_instance_ids")
            if not isinstance(candidate_ids, list) or not all(
                isinstance(value, str) for value in candidate_ids
            ):
                raise RuntimeFailure(
                    "evaluation-input-evidence-invalid",
                    "evaluation-input routing conflict is malformed",
                )
    indexed_content = load_evaluation_input_root(owner)
    physical_by_instance: dict[str, dict[str, Any]] = {}
    physical_by_path: dict[str, set[str]] = {}
    physical_capability_ids: set[str] = set()
    all_skill_paths: list[Path] = []
    for item in physical:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("instance_id"), str)
            or item["instance_id"] in physical_by_instance
            or not isinstance(item.get("canonical_capability_id"), str)
            or not isinstance(item.get("absolute_path"), str)
        ):
            raise RuntimeFailure(
                "evaluation-input-evidence-invalid",
                "evaluation-input physical inventory is ambiguous",
            )
        physical_by_instance[item["instance_id"]] = item
        physical_capability_ids.add(item["canonical_capability_id"])
        physical_by_path.setdefault(item["absolute_path"], set()).add(
            item["canonical_capability_id"]
        )
        all_skill_paths.append(Path(item["absolute_path"]))
    if not set(indexed_content).issubset(physical_capability_ids):
        raise RuntimeFailure(
            "evaluation-input-root-invalid",
            "evaluation-input root index contains an unknown capability",
        )
    enabled_by_capability: dict[str, set[str]] = {}
    runtime_names: dict[str, set[str]] = {}
    for item in enabled:
        if not isinstance(item, dict) or item.get("runtime_enabled") is not True:
            continue
        capability_id = item.get("canonical_capability_id")
        instance_id = item.get("instance_id")
        runtime_name = item.get("runtime_name")
        if (
            not isinstance(capability_id, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", capability_id) is None
            or not isinstance(instance_id, str)
            or instance_id not in physical_by_instance
        ):
            raise RuntimeFailure(
                "evaluation-input-evidence-invalid",
                "evaluation-input enabled inventory is malformed",
            )
        enabled_by_capability.setdefault(capability_id, set()).add(instance_id)
        if isinstance(runtime_name, str) and runtime_name:
            runtime_names.setdefault(runtime_name.casefold(), set()).add(capability_id)
    usage_by_capability: dict[str, dict[str, Any]] = {}
    for item in usage_rows:
        capability_id = (
            item.get("canonical_capability_id") if isinstance(item, dict) else None
        )
        if (
            not isinstance(capability_id, str)
            or capability_id in usage_by_capability
        ):
            raise RuntimeFailure(
                "evaluation-input-evidence-invalid",
                "evaluation-input usage inventory is ambiguous",
            )
        usage_by_capability[capability_id] = item
    if set(usage_by_capability) != set(enabled_by_capability):
        raise RuntimeFailure(
            "evaluation-input-evidence-invalid",
            "evaluation-input usage inventory does not cover the enabled estate",
        )
    routing_conflict_instances = {
        instance_id
        for item in unresolved
        if isinstance(item, dict) and item.get("reason") == "multiply_mapped"
        for instance_id in item.get("candidate_instance_ids", [])
        if isinstance(instance_id, str)
    }
    routing_conflict_capabilities = {
        item["canonical_capability_id"]
        for instance_id in routing_conflict_instances
        if (item := physical_by_instance.get(instance_id)) is not None
    }
    routing_conflict_capabilities.update(
        capability_id
        for capability_ids in runtime_names.values()
        if len(capability_ids) > 1
        for capability_id in capability_ids
    )
    complete_plugins = set()
    for item in plugins:
        if (
            isinstance(item, dict)
            and item.get("enabled") is True
            and isinstance(item.get("capabilities"), dict)
            and item["capabilities"].get("complete") is True
        ):
            plugin_id = item.get("plugin_id")
            if not isinstance(plugin_id, str) or not plugin_id:
                raise RuntimeFailure(
                    "evaluation-input-evidence-invalid",
                    "evaluation-input complete plugin identity is malformed",
                )
            complete_plugins.add(plugin_id)
    rows = []
    for capability_id in sorted(enabled_by_capability):
        instance_ids = enabled_by_capability[capability_id]
        representative = (
            physical_by_instance[next(iter(instance_ids))]
            if len(instance_ids) == 1
            else None
        )
        evaluation = (
            representative.get("evaluation")
            if isinstance(representative, dict)
            else None
        )
        if (
            representative is not None
            and (
                representative.get("canonical_capability_id") != capability_id
                or representative.get("evaluation_complete") is not True
                or not isinstance(evaluation, dict)
                or evaluation.get("state") not in EVALUATION_INPUT_QUEUE_STATES
                or not isinstance(evaluation.get("status"), str)
                or not isinstance(evaluation.get("current"), bool)
                or not isinstance(evaluation.get("cases"), list)
            )
        ):
            raise RuntimeFailure(
                "evaluation-input-evidence-invalid",
                "evaluation-input evaluation inventory is malformed",
            )
        if representative is None:
            evaluation = {
                "state": "missing",
                "status": "missing",
                "current": False,
                "cases": [],
            }
        usage_state = evaluation_input_usage_state(
            capability_id, usage, usage_by_capability[capability_id]
        )
        root_class = (
            representative.get("root_class")
            if isinstance(representative, dict)
            and isinstance(representative.get("root_class"), str)
            else "unknown"
        )
        owner_id = (
            representative.get("owner")
            if isinstance(representative, dict)
            else None
        )
        priority = (
            (2, "evaluation_missing_or_invalid")
            if representative is None
            else evaluation_input_queue_priority(
                evaluation,
                usage_state,
                routing_conflict=capability_id in routing_conflict_capabilities,
                root_class=root_class,
                plugin_complete=(
                    isinstance(owner_id, str) and owner_id in complete_plugins
                ),
            )
        )
        if priority is None:
            continue
        deferral_reason = None
        skill_path: Path | None = None
        content = None
        if representative is None:
            deferral_reason = "capability_path_ambiguous"
        else:
            skill_path = Path(representative["absolute_path"])
            try:
                resolved_skill = skill_path.resolve(strict=True)
            except OSError:
                resolved_skill = None
            if (
                skill_path.is_symlink()
                or resolved_skill is None
                or resolved_skill != skill_path
                or not skill_path.is_dir()
                or not (skill_path / "SKILL.md").is_file()
            ):
                deferral_reason = "capability_path_unavailable"
            elif len(physical_by_path.get(str(skill_path), set())) != 1:
                deferral_reason = "capability_path_ambiguous"
            elif representative.get("dependencies_complete") is not True:
                deferral_reason = "dependency_evidence_incomplete"
            elif capability_id not in indexed_content:
                deferral_reason = "input_not_ready"
            else:
                try:
                    content = validate_evaluation_input_capability(
                        owner,
                        indexed_content[capability_id],
                        installed_skill_roots=all_skill_paths,
                    )
                except RuntimeFailure as error:
                    if error.code != "evaluation-input-not-ready":
                        raise
                    deferral_reason = "input_not_ready"
        required_phase = (
            "authoring"
            if evaluation["state"]
            in {
                "missing",
                "input_missing",
                "drafting",
                "review_required",
                "insufficient_information",
                "invalid",
            }
            else "execution"
        )
        rows.append(
            {
                "capability_id": capability_id,
                "skill_path": str(skill_path) if skill_path is not None else None,
                "priority": priority[0],
                "queue_reason": priority[1],
                "usage_state": usage_state,
                "evaluation_state": evaluation["state"],
                "required_phase": required_phase,
                "runnable_phase": (
                    "authoring"
                    if deferral_reason is None and required_phase == "authoring"
                    else None
                ),
                "deferral_reason": (
                    "ready_for_execution"
                    if deferral_reason is None and evaluation["state"] == "ready"
                    else deferral_reason
                ),
                "input_manifest_sha256": (
                    content["manifest_sha256"] if content is not None else None
                ),
            }
        )
    rows.sort(key=lambda item: (item["priority"], item["capability_id"]))
    return {
        "schema_version": 1,
        "census_snapshot_sha256": census["snapshot_sha256"],
        "census_receipt_sha256": census_receipt_sha256,
        "usage_snapshot_sha256": usage["snapshot_sha256"],
        "usage_receipt_sha256": usage_receipt_sha256,
        "rows": rows,
    }


def evaluation_input_process_identity(pid: int) -> str | None:
    result = subprocess.run(
        ["/bin/ps", "-o", "lstart=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
    )
    identity = " ".join(result.stdout.split())
    return identity if result.returncode == 0 and identity else None


def evaluation_input_process_group_status(
    pgid: int, expected_leader_identity: str
) -> str:
    observed_identity = evaluation_input_process_identity(pgid)
    if (
        observed_identity is not None
        and observed_identity != expected_leader_identity
    ):
        return "reused"
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return "absent"
    except (PermissionError, OSError) as error:
        raise RuntimeFailure(
            "evaluation-input-owner-group-unreadable", str(error)
        ) from error
    return "present" if observed_identity is not None else "leaderless"


def stop_evaluation_input_process_group(
    process: subprocess.Popen[str],
    *,
    leader_identity: str,
    timeout_seconds: int,
) -> None:
    status = evaluation_input_process_group_status(
        process.pid, leader_identity
    )
    if status == "present":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif status not in {"absent", "reused"}:
        raise RuntimeFailure(
            "evaluation-input-owner-group-unreadable",
            "evaluation-input owner group leader identity is unavailable",
        )
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        raise RuntimeFailure(
            "evaluation-input-owner-group-live",
            "evaluation-input owner did not exit after exact group termination",
        )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = evaluation_input_process_group_status(
            process.pid, leader_identity
        )
        if status in {"absent", "reused"}:
            break
        time.sleep(0.05)
    if status not in {"absent", "reused"}:
        raise RuntimeFailure(
            "evaluation-input-owner-group-live",
            "evaluation-input owner process group survived termination",
        )
    try:
        process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        raise RuntimeFailure(
            "evaluation-input-owner-pipe-live",
            "evaluation-input owner pipes did not close after termination",
        ) from error


def read_evaluation_input_claim_fence(
    path: Path, expected_owner_run_id: str
) -> dict[str, str] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise RuntimeFailure(
            "evaluation-input-owner-claim-invalid",
            "evaluation-input owner claim fence is not a regular file",
        )
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeFailure(
            "evaluation-input-owner-claim-invalid",
            f"evaluation-input owner claim fence is unreadable: {error}",
        ) from error
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "claim_id", "owner_run_id"}
        or value.get("schema_version") != 1
        or re.fullmatch(r"sha256:[0-9a-f]{64}", str(value.get("claim_id"))) is None
        or value.get("owner_run_id") != expected_owner_run_id
    ):
        raise RuntimeFailure(
            "evaluation-input-owner-claim-invalid",
            "evaluation-input owner claim fence is malformed",
        )
    return {
        "claim_id": value["claim_id"],
        "owner_run_id": value["owner_run_id"],
    }


def run_evaluation_input_owner_process(
    command: list[str],
    *,
    owner_run_id: str,
    claim_fence_path: Path,
    halt_check: Callable[[], bool],
    lease_check: Callable[[], bool],
    terminalize: Callable[
        [dict[str, str] | None, str], dict[str, Any]
    ],
    timeout_seconds: int = EVALUATION_INPUT_OWNER_MAX_SECONDS,
    stop_seconds: int = EVALUATION_INPUT_OWNER_STOP_SECONDS,
    poll_seconds: float = 0.1,
) -> dict[str, Any]:
    if not lease_check():
        return {"status": "lock_lost", "claim": None}
    if halt_check():
        return {"status": "halted", "claim": None}
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as error:
        raise RuntimeFailure(
            "evaluation-input-owner-start-failed", str(error)
        ) from error
    leader_identity = None
    identity_deadline = time.monotonic() + 1
    while time.monotonic() < identity_deadline:
        leader_identity = evaluation_input_process_identity(process.pid)
        if leader_identity is not None:
            break
        if process.poll() is not None:
            break
        time.sleep(0.01)
    if leader_identity is None:
        try:
            process.communicate(timeout=stop_seconds)
        except subprocess.TimeoutExpired:
            pass
        raise RuntimeFailure(
            "evaluation-input-owner-identity-unavailable",
            "evaluation-input owner process identity could not be captured",
        )
    started = time.monotonic()
    stop_reason = None
    while process.poll() is None:
        if halt_check():
            stop_reason = "halted"
            break
        if not lease_check():
            stop_reason = "lock_lost"
            break
        if time.monotonic() - started >= timeout_seconds:
            stop_reason = "skill_elapsed_budget_exhausted"
            break
        time.sleep(poll_seconds)
    if stop_reason is not None:
        stop_evaluation_input_process_group(
            process,
            leader_identity=leader_identity,
            timeout_seconds=stop_seconds,
        )
        claim = read_evaluation_input_claim_fence(
            claim_fence_path, owner_run_id
        )
        if stop_reason == "lock_lost" or not lease_check():
            return {
                "status": "lock_lost",
                "claim": claim,
                "process_group_id": process.pid,
            }
        terminal = terminalize(claim, stop_reason)
        return {
            "status": stop_reason,
            "claim": claim,
            "terminal": terminal,
            "process_group_id": process.pid,
        }
    group_status = evaluation_input_process_group_status(
        process.pid, leader_identity
    )
    if group_status not in {"absent", "reused"}:
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        raise RuntimeFailure(
            "evaluation-input-owner-group-leaked",
            "evaluation-input owner left a live process group",
        )
    try:
        stdout, stderr = process.communicate(timeout=stop_seconds)
    except subprocess.TimeoutExpired as error:
        raise RuntimeFailure(
            "evaluation-input-owner-pipe-live",
            "evaluation-input owner pipes remained open after group exit",
        ) from error
    try:
        values = [
            json.loads(line)
            for line in stdout.splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as error:
        raise RuntimeFailure(
            "evaluation-input-owner-malformed", str(error)
        ) from error
    if (
        process.returncode != 0
        or len(values) != 1
        or not isinstance(values[0], dict)
    ):
        detail = (stderr or stdout).strip()[-1000:]
        raise RuntimeFailure(
            "evaluation-input-owner-failed",
            detail or f"evaluation-input owner exited {process.returncode}",
        )
    return values[0]


def evaluation_input_owner_lease_valid() -> bool:
    required = (
        "SKILLS_LOCK_TOKEN",
        "SKILLS_LOCK_OWNER_PID",
        "SKILLS_LOCK_OWNER_IDENTITY",
    )
    if (
        os.environ.get("DREAMING_ORCHESTRATED") != "1"
        or os.environ.get("SKILLS_LOCK_HELD_BY_PARENT") != "1"
        or any(not os.environ.get(key) for key in required)
    ):
        return False
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("daemon-lock.py")),
            "assert",
            os.environ["SKILLS_LOCK_TOKEN"],
            "--pid",
            os.environ["SKILLS_LOCK_OWNER_PID"],
            "--process-identity",
            os.environ["SKILLS_LOCK_OWNER_IDENTITY"],
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def evaluation_input_owner_halt_path(paths: RuntimePaths) -> Path:
    return Path(
        os.environ.get(
            "DREAMING_HALT_FILE",
            paths.state / "skill-review" / "disable-daemon",
        )
    )


def evaluation_input_owner_content(
    owner: dict[str, Any],
    census: dict[str, Any],
    capability_id: str,
) -> dict[str, Any]:
    entries = load_evaluation_input_root(owner)
    entry = entries.get(capability_id)
    physical = census.get("physical_instances")
    if entry is None or not isinstance(physical, list):
        raise RuntimeFailure(
            "evaluation-input-not-ready",
            "selected evaluation-input content is unavailable",
        )
    installed_roots = [
        Path(item["absolute_path"])
        for item in physical
        if isinstance(item, dict)
        and isinstance(item.get("absolute_path"), str)
    ]
    return validate_evaluation_input_capability(
        owner, entry, installed_skill_roots=installed_roots
    )


def evaluation_input_owner_terminal_command(
    owner: dict[str, Any],
    claim: dict[str, str] | None,
    reason: str,
    owner_run_id: str,
) -> dict[str, Any]:
    command = [
        sys.executable,
        owner["evaluator"],
        "v2-input-owner-terminal",
        "--expected-owner-run-id",
        owner_run_id,
        "--reason",
        reason,
    ]
    if claim is not None:
        command.extend(["--claim-id", claim["claim_id"]])
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeFailure(
            "evaluation-input-owner-terminal-failed", str(error)
        ) from error
    try:
        values = [
            json.loads(line)
            for line in result.stdout.splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as error:
        raise RuntimeFailure(
            "evaluation-input-owner-terminal-malformed", str(error)
        ) from error
    if (
        result.returncode != 0
        or len(values) != 1
        or not isinstance(values[0], dict)
    ):
        detail = (result.stderr or result.stdout).strip()[-1000:]
        raise RuntimeFailure(
            "evaluation-input-owner-terminal-failed",
            detail or "evaluation-input owner terminalization refused",
        )
    return values[0]


def execute_evaluation_input_owner(
    owner: dict[str, Any],
    census: dict[str, Any],
    queue: dict[str, Any],
    paths: RuntimePaths,
) -> dict[str, Any]:
    row = next(
        (
            item
            for item in queue["rows"]
            if item.get("runnable_phase") == "authoring"
        ),
        None,
    )
    if row is None:
        return {"status": "idle", "selected_capability_id": None}
    owner_run_id = os.environ.get("DREAMING_PARENT_RUN_ID")
    if not owner_run_id:
        raise RuntimeFailure(
            "evaluation-input-owner-unorchestrated",
            "evaluation-input owner requires a parent run identity",
        )
    content = evaluation_input_owner_content(
        owner, census, row["capability_id"]
    )
    files = content["files"]
    paths.state.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".evaluation-input-owner.", dir=paths.state
    ) as temporary:
        claim_fence = Path(temporary) / "claim.json"
        command = [
            sys.executable,
            owner["evaluator"],
            "v2-input-owner-run",
            row["skill_path"],
            "--owner-run-id",
            owner_run_id,
            "--claim-fence",
            str(claim_fence),
            "--owner-config-sha256",
            owner["config_sha256"],
            "--suite",
            files["suite"],
            "--policy",
            files["policy"],
            "--config",
            files["compilation"],
            "--routing",
            files["routing"],
            "--harness",
            files["harness"],
            "--catalog",
            files["catalog"],
            "--author-model",
            owner["author_model"],
            "--reviewer-a-model",
            owner["reviewer_a_model"],
            "--reviewer-b-model",
            owner["reviewer_b_model"],
        ]
        result = run_evaluation_input_owner_process(
            command,
            owner_run_id=owner_run_id,
            claim_fence_path=claim_fence,
            halt_check=lambda: evaluation_input_owner_halt_path(paths).exists(),
            lease_check=evaluation_input_owner_lease_valid,
            terminalize=lambda claim, reason: (
                evaluation_input_owner_terminal_command(
                    owner, claim, reason, owner_run_id
                )
            ),
        )
    return {
        "selected_capability_id": row["capability_id"],
        "selected_priority": row["priority"],
        **result,
    }


def reconcile_evaluation_input_owner(owner: dict[str, Any]) -> dict[str, Any]:
    try:
        process = subprocess.run(
            [sys.executable, owner["evaluator"], "v2-input-owner-reconcile"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeFailure(
            "evaluation-input-recovery-failed", str(error)
        ) from error
    try:
        values = [
            json.loads(line)
            for line in process.stdout.splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as error:
        raise RuntimeFailure(
            "evaluation-input-recovery-malformed", str(error)
        ) from error
    if (
        process.returncode != 0
        or len(values) != 1
        or not isinstance(values[0], dict)
        or values[0].get("status") != "reconciled"
    ):
        raise RuntimeFailure(
            "evaluation-input-recovery-failed",
            process.stderr.strip() or "owner recovery refused",
        )
    return values[0]


def configured_adapters(
    config: dict[str, Any],
) -> tuple[dict[str, dict[str, ExecutableAdapter]], list[dict[str, Any]]]:
    adapters: dict[str, dict[str, ExecutableAdapter]] = {
        role: {} for role in ROLES
    }
    reports: list[dict[str, Any]] = []
    for key, role in ROLE_CONFIG_KEYS.items():
        entries = config.get(key, {})
        if not isinstance(entries, dict):
            raise RuntimeFailure("invalid-adapter-config", f"{key} must be an object")
        for name, entry in sorted(entries.items()):
            if not isinstance(name, str) or not isinstance(entry, dict):
                raise RuntimeFailure("invalid-adapter-config", f"invalid {key} entry")
            argv = entry.get("argv")
            if (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(item, str) and item for item in argv)
            ):
                raise RuntimeFailure(
                    "invalid-adapter-config", f"{key}.{name}.argv is invalid"
                )
            timeout = entry.get("timeout", 30)
            run_timeout = entry.get("run_timeout", timeout)
            if (
                not isinstance(timeout, int)
                or timeout < 1
                or not isinstance(run_timeout, int)
                or run_timeout < timeout
            ):
                raise RuntimeFailure(
                    "invalid-adapter-config",
                    f"{key}.{name} timeout is invalid",
                )
            adapter = ExecutableAdapter(
                argv,
                role,
                timeout=timeout,
                run_timeout=run_timeout,
            )
            adapters[role][name] = adapter
            doctor = adapter.call("doctor")
            if doctor.get("healthy") is not True:
                raise RuntimeFailure("adapter-unhealthy", f"{role}:{name}")
            if role == "review-executor" and doctor.get("boundary_ready") is not True:
                raise RuntimeFailure(
                    "executor-boundary-unavailable", f"{role}:{name}"
                )
            reports.append(
                {
                    "name": name,
                    "role": role,
                    "adapter_id": adapter.identity["adapter_id"],
                    "healthy": True,
                }
            )
    if not reports:
        raise RuntimeFailure("invalid-adapter-config", "no adapters configured")
    return adapters, reports


def configured_adapters_tolerant(
    config: dict[str, Any],
) -> tuple[
    dict[str, dict[str, ExecutableAdapter]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    adapters: dict[str, dict[str, ExecutableAdapter]] = {
        role: {} for role in ROLES
    }
    reports: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for key, role in ROLE_CONFIG_KEYS.items():
        entries = config.get(key, {})
        if not isinstance(entries, dict):
            raise RuntimeFailure("invalid-adapter-config", f"{key} must be an object")
        for name, entry in sorted(entries.items()):
            if not isinstance(name, str) or not isinstance(entry, dict):
                raise RuntimeFailure("invalid-adapter-config", f"invalid {key} entry")
            argv = entry.get("argv")
            if (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(item, str) and item for item in argv)
            ):
                raise RuntimeFailure(
                    "invalid-adapter-config", f"{key}.{name}.argv is invalid"
                )
            try:
                timeout = entry.get("timeout", 30)
                run_timeout = entry.get("run_timeout", timeout)
                if (
                    not isinstance(timeout, int)
                    or timeout < 1
                    or not isinstance(run_timeout, int)
                    or run_timeout < timeout
                ):
                    raise RuntimeFailure(
                        "invalid-adapter-config",
                        f"{key}.{name} timeout is invalid",
                    )
                adapter = ExecutableAdapter(
                    argv,
                    role,
                    timeout=timeout,
                    run_timeout=run_timeout,
                )
                doctor = adapter.call("doctor")
                if doctor.get("healthy") is not True:
                    raise RuntimeFailure("adapter-unhealthy", f"{role}:{name}")
                if (
                    role == "review-executor"
                    and doctor.get("boundary_ready") is not True
                ):
                    raise RuntimeFailure(
                        "executor-boundary-unavailable", f"{role}:{name}"
                    )
            except RuntimeFailure as error:
                errors.append(
                    {
                        "phase": "adapter-health",
                        "role": role,
                        "adapter": name,
                        "code": error.code,
                    }
                )
                continue
            adapters[role][name] = adapter
            reports.append(
                {
                    "name": name,
                    "role": role,
                    "adapter_id": adapter.identity["adapter_id"],
                    "healthy": True,
                }
            )
    return adapters, reports, errors


def configured_role_tolerant(
    config: dict[str, Any],
    key: str,
    role: str,
) -> tuple[dict[str, ExecutableAdapter], list[dict[str, Any]], list[dict[str, Any]]]:
    entries = config.get(key, {})
    if not isinstance(entries, dict):
        raise RuntimeFailure("invalid-adapter-config", f"{key} must be an object")
    adapters: dict[str, ExecutableAdapter] = {}
    reports: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for name, entry in sorted(entries.items()):
        if not isinstance(name, str) or not isinstance(entry, dict):
            raise RuntimeFailure("invalid-adapter-config", f"invalid {key} entry")
        argv = entry.get("argv")
        timeout = entry.get("timeout", 30)
        run_timeout = entry.get("run_timeout", timeout)
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(item, str) and item for item in argv)
            or not isinstance(timeout, int)
            or timeout < 1
            or not isinstance(run_timeout, int)
            or run_timeout < timeout
        ):
            raise RuntimeFailure(
                "invalid-adapter-config", f"{key}.{name} is invalid"
            )
        try:
            adapter = ExecutableAdapter(
                argv,
                role,
                timeout=timeout,
                run_timeout=run_timeout,
            )
            doctor = adapter.call("doctor")
            if doctor.get("healthy") is not True:
                raise RuntimeFailure("adapter-unhealthy", f"{role}:{name}")
        except RuntimeFailure as error:
            errors.append(
                {
                    "phase": "adapter-health",
                    "role": role,
                    "adapter": name,
                    "code": error.code,
                }
            )
            continue
        adapters[name] = adapter
        reports.append(
            {
                "name": name,
                "role": role,
                "adapter_id": adapter.identity["adapter_id"],
                "healthy": True,
            }
        )
    return adapters, reports, errors


def validated_routing(
    config: dict[str, Any],
    source_names: set[str],
    executor_names: set[str],
) -> tuple[set[tuple[str, str]], list[str]]:
    if not source_names or not executor_names:
        raise RuntimeFailure(
            "invalid-adapter-config", "at least one source and executor are required"
        )
    routes = configured_routes(config)
    for source, executor in routes:
        if source not in source_names or executor not in executor_names:
            raise RuntimeFailure(
                "invalid-adapter-config",
                f"route references unknown adapter: {source}>{executor}",
            )
    executor_order = config.get("executor_order", sorted(executor_names))
    if (
        not isinstance(executor_order, list)
        or not executor_order
        or not all(name in executor_names for name in executor_order)
    ):
        raise RuntimeFailure(
            "invalid-adapter-config", "executor_order names unknown executors"
        )
    for source in source_names:
        if not any(
            route_source == source and executor in executor_order
            for route_source, executor in routes
        ):
            raise RuntimeFailure(
                "invalid-adapter-config",
                f"source has no selectable executor route: {source}",
            )
    return routes, executor_order


def validate_adapter_config(path: Path) -> list[dict[str, Any]]:
    config = load_adapter_config(path)
    adapters, reports = configured_adapters(config)
    validated_routing(
        config,
        set(adapters["session-source"]),
        set(adapters["review-executor"]),
    )
    configured_runtime_settings(config)
    return reports


def configured_routes(config: dict[str, Any]) -> set[tuple[str, str]]:
    values = config.get("routes")
    if not isinstance(values, list) or not values:
        raise RuntimeFailure("invalid-adapter-config", "routes must be a nonempty list")
    routes: set[tuple[str, str]] = set()
    for value in values:
        if not isinstance(value, str) or value.count(">") != 1:
            raise RuntimeFailure("invalid-adapter-config", f"invalid route {value!r}")
        source, executor = value.split(">", 1)
        if not source or not executor:
            raise RuntimeFailure("invalid-adapter-config", f"invalid route {value!r}")
        routes.add((source, executor))
    return routes


def configured_runtime_settings(config: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "policy_version": 2,
        "overlap_seconds": 300,
        "quiet_retry_seconds": 300,
        "page_size": 100,
        "max_pages_per_run": 100,
        "max_reviews_per_run": 25,
        "max_snapshot_bytes": 100_000,
        "max_events": 2_000,
        "max_field_bytes": 64_000,
        "max_autonomous_session_age_days": 30,
        "allow_autonomous_skill_creation": False,
    }
    settings: dict[str, Any] = {}
    for name, default in defaults.items():
        value = config.get(name, default)
        if name == "allow_autonomous_skill_creation":
            if not isinstance(value, bool):
                raise RuntimeFailure(
                    "invalid-adapter-config",
                    f"{name} must be a boolean",
                )
            if value:
                raise RuntimeFailure(
                    "invalid-adapter-config",
                    f"{name} must remain false until recurrence admission exists",
                )
            settings[name] = value
            continue
        minimum = 0 if name == "overlap_seconds" else 1
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise RuntimeFailure(
                "invalid-adapter-config",
                f"{name} must be an integer greater than or equal to {minimum}",
            )
        if name == "max_autonomous_session_age_days" and value != 30:
            raise RuntimeFailure(
                "invalid-adapter-config",
                f"{name} must remain 30 until recurrence admission exists",
            )
        if name == "max_reviews_per_run" and value > 25:
            raise RuntimeFailure(
                "invalid-adapter-config",
                f"{name} must not exceed 25",
            )
        settings[name] = value
    return settings


def configured_estate_census(config: dict[str, Any]) -> dict[str, Any] | None:
    entry = config.get("estate_census")
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise RuntimeFailure(
            "invalid-adapter-config", "estate_census must be an object"
        )
    argv = entry.get("argv")
    timeout = entry.get("timeout", 180)
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item for item in argv)
        or not isinstance(timeout, int)
        or timeout < 1
    ):
        raise RuntimeFailure(
            "invalid-adapter-config", "estate_census command is invalid"
        )
    return {"argv": argv, "timeout": timeout}


def collect_estate_census(
    core: DreamingRuntime,
    config: dict[str, Any],
    *,
    include_evidence: bool = False,
) -> dict[str, Any] | None:
    entry = configured_estate_census(config)
    if entry is None:
        return None
    try:
        process = subprocess.run(
            entry["argv"],
            check=False,
            capture_output=True,
            text=True,
            timeout=entry["timeout"],
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeFailure("estate-census-failed", str(error)) from error
    try:
        values = [
            json.loads(line)
            for line in process.stdout.splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as error:
        raise RuntimeFailure("estate-census-malformed", str(error)) from error
    if len(values) != 1 or not isinstance(values[0], dict):
        raise RuntimeFailure("estate-census-malformed", "ambiguous output")
    result = values[0]
    if process.returncode != 0 or result.get("ok") is not True:
        raise RuntimeFailure(
            "estate-census-failed", str(result.get("error", process.stderr.strip()))
        )
    census = result.get("census")
    usage = result.get("usage")
    receiver = result.get("receiver")
    if not isinstance(census, dict):
        raise RuntimeFailure("estate-census-malformed", "census missing")
    recorded = core.record_estate_census(census, receiver)
    if usage is not None:
        if not isinstance(usage, dict):
            raise RuntimeFailure("estate-census-malformed", "usage malformed")
        recorded["usage"] = core.record_estate_usage(usage, receiver, census)
    else:
        recorded["usage"] = {
            "status": "unavailable",
            "reason": "collector_generation_has_no_usage",
        }
    if include_evidence:
        return {
            "summary": recorded,
            "census": census,
            "usage": usage,
            "receiver": receiver,
        }
    return recorded


def selftest(require_config: bool) -> dict[str, Any]:
    paths = default_paths()
    recovery_state = paths.state / "publication-recovery-required.json"
    if recovery_state.exists():
        raise RuntimeFailure("publication-recovery-required", str(recovery_state))
    contract_path = Path(__file__).with_name("dreaming-adapter-contract-v1.json")
    contract = read_json(contract_path, None)
    if (
        not isinstance(contract, dict)
        or contract.get("contract_version") != CONTRACT_VERSION
    ):
        raise RuntimeFailure("contract-file-invalid", str(contract_path))

    probes = [
        paths.state / ".dreaming-core-selftest.json",
        paths.data / ".dreaming-core-selftest.json",
    ]
    try:
        for probe in probes:
            atomic_json(
                probe,
                {"contract_version": CONTRACT_VERSION, "probe": probe.parent.name},
            )
            if read_json(probe, {})["contract_version"] != CONTRACT_VERSION:
                raise RuntimeFailure("selftest-probe-failed", str(probe))
    finally:
        for probe in probes:
            probe.unlink(missing_ok=True)

    config_path = default_adapter_config(paths)
    adapters: list[dict[str, Any]] = []
    if config_path.exists():
        adapters = validate_adapter_config(config_path)
        config = load_adapter_config(config_path)
        configured_estate_census(config)
        configured_evaluation_input_owner(config, config_path, paths)
    elif require_config:
        raise RuntimeFailure("adapter-config-missing", str(config_path))
    return {
        "ok": True,
        "runtime": "dreaming-core",
        "contract_version": CONTRACT_VERSION,
        "adapter_config": str(config_path),
        "adapters": adapters,
        "data_dir": str(paths.data),
        "state_dir": str(paths.state),
    }


def scheduled_run() -> dict[str, Any]:
    paths = default_paths()
    config_path = default_adapter_config(paths)
    config = load_adapter_config(config_path)
    source_config = config.get("sources", {})
    executor_config = config.get("executors", {})
    if not isinstance(source_config, dict) or not isinstance(executor_config, dict):
        raise RuntimeFailure(
            "invalid-adapter-config", "sources and executors must be objects"
        )
    declared_sources = set(source_config)
    declared_executors = set(executor_config)
    routes, executor_order = validated_routing(
        config, declared_sources, declared_executors
    )
    adapters, adapter_reports, adapter_errors = configured_adapters_tolerant(config)
    retired_publishers, retired_reports, retired_errors = configured_role_tolerant(
        config, "retired_publishers", "skill-publisher"
    )
    adapter_reports.extend(retired_reports)
    adapter_errors.extend(retired_errors)
    sources = adapters["session-source"]
    executors = adapters["review-executor"]
    settings = configured_runtime_settings(config)
    core = DreamingRuntime(
        paths,
        routes,
        policy_version=settings["policy_version"],
        overlap_seconds=settings["overlap_seconds"],
        quiet_retry_seconds=settings["quiet_retry_seconds"],
        max_snapshot_bytes=settings["max_snapshot_bytes"],
        max_events=settings["max_events"],
        max_field_bytes=settings["max_field_bytes"],
        max_autonomous_session_age_days=settings[
            "max_autonomous_session_age_days"
        ],
        allow_autonomous_skill_creation=settings[
            "allow_autonomous_skill_creation"
        ],
        parent_run_id=os.environ.get("DREAMING_PARENT_RUN_ID") or None,
    )
    legacy_ledger = config.get("legacy_ledger_path") or os.environ.get(
        "DREAMING_LEGACY_LEDGER"
    )
    imported_legacy = (
        core.import_legacy_ledger(Path(legacy_ledger))
        if isinstance(legacy_ledger, str) and legacy_ledger
        else 0
    )
    report: dict[str, Any] = {
        "ok": True,
        "runtime": "dreaming-core",
        "command": "run",
        "adapter_config": str(config_path),
        "adapters": adapter_reports,
        "discovery": {},
        "reviews": [],
        "deferred_reviews": 0,
        "publication": [],
        "errors": adapter_errors,
        "legacy_records_imported": imported_legacy,
    }
    try:
        evaluation_owner = configured_evaluation_input_owner(
            config, config_path, paths
        )
    except RuntimeFailure as error:
        report["evaluation_input"] = {
            "configured": True,
            "recovery": "refused",
            "code": error.code,
        }
        report["errors"].append(
            {"phase": "evaluation-input-config", "code": error.code}
        )
        report["ok"] = False
        return report
    if evaluation_owner is None:
        report["evaluation_input"] = {
            "configured": False,
            "enabled": False,
            "recovery": "not_configured",
        }
    else:
        try:
            recovery = reconcile_evaluation_input_owner(evaluation_owner)
        except RuntimeFailure as error:
            report["evaluation_input"] = {
                "configured": True,
                "enabled": evaluation_owner["enabled"],
                "mode": (
                    "authoring" if evaluation_owner["enabled"] else "reconcile_only"
                ),
                "recovery": "failed",
                "code": error.code,
            }
            report["errors"].append(
                {"phase": "evaluation-input-recovery", "code": error.code}
            )
            report["ok"] = False
            return report
        report["evaluation_input"] = {
            "configured": True,
            "enabled": evaluation_owner["enabled"],
            "mode": (
                "authoring" if evaluation_owner["enabled"] else "reconcile_only"
            ),
            "config_sha256": evaluation_owner["config_sha256"],
            "recovery": recovery,
        }
    queue_evidence = None
    try:
        estate_result = collect_estate_census(
            core,
            config,
            include_evidence=bool(
                evaluation_owner is not None and evaluation_owner["enabled"]
            ),
        )
        if (
            evaluation_owner is not None
            and evaluation_owner["enabled"]
            and isinstance(estate_result, dict)
            and isinstance(estate_result.get("summary"), dict)
        ):
            report["estate_census"] = estate_result["summary"]
            queue_evidence = estate_result
        else:
            report["estate_census"] = estate_result
            if evaluation_owner is not None and evaluation_owner["enabled"]:
                report["evaluation_input"]["queue"] = {
                    "status": "refused",
                    "code": "evaluation-input-evidence-invalid",
                }
                report["errors"].append(
                    {
                        "phase": "evaluation-input-queue",
                        "code": "evaluation-input-evidence-invalid",
                    }
                )
    except RuntimeFailure as error:
        report["errors"].append(
            {"phase": "estate-census", "code": error.code}
        )
        queue_evidence = None
    if queue_evidence is not None and evaluation_owner is not None:
        try:
            usage_summary = queue_evidence["summary"].get("usage")
            usage = queue_evidence.get("usage")
            if (
                not isinstance(usage, dict)
                or not isinstance(usage_summary, dict)
                or not isinstance(usage_summary.get("receipt_sha256"), str)
            ):
                raise RuntimeFailure(
                    "evaluation-input-evidence-invalid",
                    "evaluation-input usage evidence is unavailable",
                )
            derived_queue = derive_evaluation_input_queue(
                evaluation_owner,
                queue_evidence["census"],
                usage,
                queue_evidence["receiver"],
                census_receipt_sha256=queue_evidence["summary"]["receipt_sha256"],
                usage_receipt_sha256=usage_summary["receipt_sha256"],
            )
            report["evaluation_input"]["queue"] = derived_queue
            report["evaluation_input"]["run"] = execute_evaluation_input_owner(
                evaluation_owner,
                queue_evidence["census"],
                derived_queue,
                paths,
            )
            if report["evaluation_input"]["run"]["status"] == "lock_lost":
                report["errors"].append(
                    {
                        "phase": "evaluation-input-run",
                        "code": "writer-lock-lost",
                    }
                )
                report["ok"] = False
                return report
        except RuntimeFailure as error:
            if "queue" not in report["evaluation_input"]:
                report["evaluation_input"]["queue"] = {
                    "status": "refused",
                    "code": error.code,
                }
            else:
                report["evaluation_input"]["run"] = {
                    "status": "refused",
                    "code": error.code,
                }
            report["errors"].append(
                {
                    "phase": (
                        "evaluation-input-run"
                        if "run" in report["evaluation_input"]
                        else "evaluation-input-queue"
                    ),
                    "code": error.code,
                }
            )
            if not evaluation_input_owner_lease_valid():
                report["ok"] = False
                return report
    recovery_state = paths.state / "publication-recovery-required.json"
    if recovery_state.exists():
        report["publication_recovery_required"] = True
        report["errors"].append(
            {"phase": "publication-recovery", "code": "recovery-required"}
        )
        report["ok"] = False
        return report
    for source_name, source in sources.items():
        try:
            state = core.discover(
                source_name,
                source,
                page_size=settings["page_size"],
                max_pages=settings["max_pages_per_run"],
            )
            unsettled = core.revisit_unsettled(source_name, source)
            report["discovery"][source_name] = {
                "settled_watermark": state.get("settled_watermark"),
                "generation_active": state.get("generation") is not None,
                "unsettled_outcomes": unsettled,
            }
        except RuntimeFailure as error:
            report["errors"].append(
                {"phase": "discovery", "source": source_name, "code": error.code}
            )

    halt_file = Path(
        os.environ.get(
            "DREAMING_HALT_FILE",
            paths.state / "skill-review" / "disable-daemon",
        )
    )
    if halt_file.exists():
        report["halted"] = True
        report["ok"] = not report["errors"]
        return report

    queue = read_json(paths.queue, [])
    review_attempts = 0
    for item in queue:
        if item.get("status") != "queued":
            continue
        if review_attempts >= settings["max_reviews_per_run"]:
            report["deferred_reviews"] += 1
            continue
        source_name = item.get("source")
        source = sources.get(source_name)
        if source is None:
            report["errors"].append(
                {
                    "phase": "review",
                    "session_id": item.get("qualified_session_id"),
                    "code": "source-not-configured",
                }
            )
            continue
        review_attempts += 1
        try:
            result = core.review(
                source_name,
                source,
                item["qualified_session_id"],
                [
                    (name, executors[name])
                    for name in executor_order
                    if name in executors
                ],
                expected_revision=item["source_revision"],
            )
            report["reviews"].append(
                {"session_id": item["qualified_session_id"], **result}
            )
        except RuntimeFailure as error:
            report["errors"].append(
                {
                    "phase": "review",
                    "session_id": item.get("qualified_session_id"),
                    "code": error.code,
                }
            )
    for publisher_name, publisher in retired_publishers.items():
        try:
            publisher.call("remove")
            report["publication"].append(
                {"publisher": publisher_name, "status": "retired"}
            )
        except RuntimeFailure as error:
            report["errors"].append(
                {
                    "phase": "publication-retire",
                    "publisher": publisher_name,
                    "code": error.code,
                }
            )
    for publisher_name, publisher in adapters["skill-publisher"].items():
        try:
            result = core.publish(publisher, paths.skills)
            report["publication"].append(
                {"publisher": publisher_name, **result}
            )
        except RuntimeFailure as error:
            report["errors"].append(
                {
                    "phase": "publication",
                    "publisher": publisher_name,
                    "code": error.code,
                }
            )
    report["ok"] = not report["errors"]
    return report


def remove_publications() -> dict[str, Any]:
    paths = default_paths()
    config_path = default_adapter_config(paths)
    config = load_adapter_config(config_path)
    publishers, adapter_reports, adapter_errors = configured_role_tolerant(
        config, "publishers", "skill-publisher"
    )
    retired, retired_reports, retired_errors = configured_role_tolerant(
        config, "retired_publishers", "skill-publisher"
    )
    adapter_reports.extend(retired_reports)
    adapter_errors.extend(retired_errors)
    report: dict[str, Any] = {
        "ok": True,
        "runtime": "dreaming-core",
        "command": "unpublish",
        "adapters": adapter_reports,
        "removed": [],
        "errors": adapter_errors,
    }
    for publisher_name, publisher in {**retired, **publishers}.items():
        try:
            publisher.call("remove")
            report["removed"].append(publisher_name)
        except RuntimeFailure as error:
            report["errors"].append(
                {
                    "phase": "publication-remove",
                    "publisher": publisher_name,
                    "code": error.code,
                }
            )
    report["ok"] = not report["errors"]
    return report


def reconcile_publications() -> dict[str, Any]:
    paths = default_paths()
    config_path = default_adapter_config(paths)
    config = load_adapter_config(config_path)
    publishers, adapter_reports, adapter_errors = configured_role_tolerant(
        config, "publishers", "skill-publisher"
    )
    retired, retired_reports, retired_errors = configured_role_tolerant(
        config, "retired_publishers", "skill-publisher"
    )
    adapter_reports.extend(retired_reports)
    adapter_errors.extend(retired_errors)
    core = DreamingRuntime(paths, configured_routes(config))
    report: dict[str, Any] = {
        "ok": True,
        "runtime": "dreaming-core",
        "command": "publish",
        "adapters": adapter_reports,
        "publication": [],
        "errors": adapter_errors,
    }
    for publisher_name, publisher in retired.items():
        try:
            publisher.call("remove")
            report["publication"].append(
                {"publisher": publisher_name, "status": "retired"}
            )
        except RuntimeFailure as error:
            report["errors"].append(
                {
                    "phase": "publication-retire",
                    "publisher": publisher_name,
                    "code": error.code,
                }
            )
    for publisher_name, publisher in publishers.items():
        try:
            result = core.publish(publisher, paths.skills)
            report["publication"].append(
                {"publisher": publisher_name, **result}
            )
        except RuntimeFailure as error:
            report["errors"].append(
                {
                    "phase": "publication",
                    "publisher": publisher_name,
                    "code": error.code,
                }
            )
    report["ok"] = not report["errors"]
    return report


def census_only() -> dict[str, Any]:
    paths = default_paths()
    config = load_adapter_config(default_adapter_config(paths))
    core = DreamingRuntime(paths, configured_routes(config))
    result = collect_estate_census(core, config)
    if result is None:
        raise RuntimeFailure("estate-census-not-configured", "estate_census")
    return {
        "ok": True,
        "runtime": "dreaming-core",
        "command": "census",
        "estate_census": result,
    }


def enqueue_session(source_name: str, qualified_session_id: str) -> dict[str, Any]:
    paths = default_paths()
    config_path = default_adapter_config(paths)
    config = load_adapter_config(config_path)
    adapters, _reports, errors = configured_adapters_tolerant(config)
    if errors:
        relevant = [
            error
            for error in errors
            if error.get("role") == "session-source"
            and error.get("adapter") == source_name
        ]
        if relevant:
            raise RuntimeFailure(str(relevant[0]["code"]), source_name)
    source = adapters["session-source"].get(source_name)
    if source is None:
        raise RuntimeFailure("source-not-configured", source_name)
    routes = configured_routes(config)
    core = DreamingRuntime(paths, routes)
    session = source.call("inspect", session=qualified_session_id)["session"]
    validate_identity(session, source_name)
    admission = core._admit(session)
    return {
        "ok": True,
        "runtime": "dreaming-core",
        "command": "enqueue",
        "source": source_name,
        "session_id": qualified_session_id,
        "admission": admission,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("selftest")
    subcommands.add_parser("doctor")
    subcommands.add_parser("run")
    subcommands.add_parser("publish")
    subcommands.add_parser("unpublish")
    subcommands.add_parser("census")
    seal_inputs = subcommands.add_parser("seal-inputs")
    seal_inputs.add_argument("--source-root", required=True)
    seal_inputs.add_argument("--plan", required=True)
    seal_inputs.add_argument("--output-root", required=True)
    seal_inputs.add_argument(
        "--installed-root", action="append", required=True
    )
    enqueue = subcommands.add_parser("enqueue")
    enqueue.add_argument("--source", required=True)
    enqueue.add_argument("--session", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command is None:
        print(
            json.dumps(
                {
                    "runtime": "dreaming-core",
                    "contract_version": CONTRACT_VERSION,
                    "roles": sorted(ROLES),
                },
                sort_keys=True,
            )
        )
        return
    try:
        if args.command == "run":
            report = scheduled_run()
        elif args.command == "publish":
            report = reconcile_publications()
        elif args.command == "unpublish":
            report = remove_publications()
        elif args.command == "census":
            report = census_only()
        elif args.command == "seal-inputs":
            report = {
                "ok": True,
                "runtime": "dreaming-core",
                "command": "seal-inputs",
                **seal_evaluation_input_root(
                    Path(args.source_root),
                    Path(args.plan),
                    Path(args.output_root),
                    installed_skill_roots=[
                        Path(path) for path in args.installed_root
                    ],
                ),
            }
        elif args.command == "enqueue":
            report = enqueue_session(args.source, args.session)
        else:
            report = selftest(require_config=args.command == "doctor")
        print(json.dumps(report, sort_keys=True))
        if report.get("ok") is not True:
            raise SystemExit(2)
    except RuntimeFailure as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {"code": error.code, "message": error.message},
                },
                sort_keys=True,
            )
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
