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
import shutil
import stat
import subprocess
import sys
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

    def __init__(self, argv: Iterable[str], role: str, timeout: int = 30):
        if role not in ROLES:
            raise RuntimeFailure("unknown-role", role)
        self.argv = [str(item) for item in argv]
        self.role = role
        self.timeout = timeout
        self.identity = self._verify_contract()

    def _invoke(self, *args: Any) -> dict[str, Any]:
        command = self.argv + [str(arg) for arg in args]
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeFailure("adapter-unavailable", str(error)) from error
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
        return self._invoke(*argv)


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
    def bundles(self) -> Path:
        return self.data / "bundles"


class DreamingRuntime:
    def __init__(
        self,
        paths: RuntimePaths,
        routes: Iterable[tuple[str, str]],
        policy_version: int = 1,
        overlap_seconds: int = 300,
        quiet_retry_seconds: int = 300,
        max_snapshot_bytes: int = 1_000_000,
        max_events: int = 2_000,
        max_field_bytes: int = 64_000,
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
        self.now = now or (lambda: int(datetime.now(timezone.utc).timestamp()))

    def _state(self, path: Path, default: Any) -> Any:
        return read_json(path, default)

    def _write(self, path: Path, value: Any) -> None:
        atomic_json(path, value)

    def _route_allowed(self, source: str, executor_id: str) -> bool:
        return (source, executor_id) in self.routes

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
        snapshot = {
            "contract_version": CONTRACT_VERSION,
            "identity": inspected,
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

        current = source.call("inspect", session=qualified_session_id)["session"]
        validate_identity(current, source_name)
        session_pending = self._pending_transaction_for_session(
            qualified_session_id
        )
        if session_pending is not None:
            self._mark_queue(
                qualified_session_id,
                current["source_revision"],
                "recovery-required",
            )
            raise RuntimeFailure(
                "mutation-recovery-required",
                f"{qualified_session_id} has an unresolved transaction",
            )
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
                        "started_at": self.now(),
                    },
                )
                try:
                    result = executor.call(
                        "run", snapshot=snapshot_path, result=result_path
                    )
                except RuntimeFailure:
                    partial = read_json(result_path, {}) if result_path.exists() else {}
                    if partial.get("mutation_started"):
                        mutation_started = True
                        self._write_transaction(
                            qualified_session_id,
                            reviewed_identity["source_revision"],
                            {
                                "status": "mutation-recovery-required",
                                "session_id": qualified_session_id,
                                "source_revision": reviewed_identity["source_revision"],
                                "executor": executor_id,
                                "result_path": str(result_path),
                                "mutation_started": True,
                            },
                        )
                        attempts.append(
                            {
                                "session_id": qualified_session_id,
                                "source": source_name,
                                "executor": executor_id,
                                "route": f"{source_name}>{executor_id}",
                                "policy_version": self.policy_version,
                                "status": "mutation-recovery-required",
                                "mutation_started": True,
                            }
                        )
                        self._write(self.paths.attempts, attempts)
                        raise RuntimeFailure(
                            "mutation-recovery-required", executor_id
                        )
                    self._clear_transaction(
                        qualified_session_id,
                        reviewed_identity["source_revision"],
                    )
                    raise
                if not result_path.exists():
                    mutation_started = bool(result.get("mutation_started"))
                    if mutation_started:
                        self._write_transaction(
                            qualified_session_id,
                            reviewed_identity["source_revision"],
                            {
                                "status": "mutation-recovery-required",
                                "session_id": qualified_session_id,
                                "source_revision": reviewed_identity["source_revision"],
                                "executor": executor_id,
                                "result_path": str(result_path),
                                "mutation_started": True,
                            },
                        )
                    else:
                        self._clear_transaction(
                            qualified_session_id,
                            reviewed_identity["source_revision"],
                        )
                    raise RuntimeFailure("missing-executor-result", executor_id)
                file_result = read_json(result_path, {})
                mutation_started = bool(
                    file_result.get("mutation_started")
                    or result.get("mutation_started")
                )
                self._write_transaction(
                    qualified_session_id,
                    reviewed_identity["source_revision"],
                    {
                        "status": (
                            "mutation-started"
                            if mutation_started
                            else "result-ready"
                        ),
                        "session_id": qualified_session_id,
                        "source_revision": reviewed_identity["source_revision"],
                        "executor": executor_id,
                        "result_path": str(result_path),
                        "mutation_started": mutation_started,
                    },
                )
                if file_result != {key: value for key, value in result.items() if key != "ok"}:
                    if not mutation_started:
                        self._clear_transaction(
                            qualified_session_id,
                            reviewed_identity["source_revision"],
                        )
                    raise RuntimeFailure("result-channel-mismatch", executor_id)
                attempt = {
                    "session_id": qualified_session_id,
                    "source": source_name,
                    "executor": executor_id,
                    "route": f"{source_name}>{executor_id}",
                    "policy_version": self.policy_version,
                    "status": result.get("status"),
                    "mutation_started": bool(result.get("mutation_started")),
                }
                attempts.append(attempt)
                self._write(self.paths.attempts, attempts)
                if (
                    result.get("status") != "ok"
                    or result.get("completion_sentinel") != "DREAMING_REVIEW_COMPLETE"
                ):
                    if result.get("mutation_started"):
                        raise RuntimeFailure(
                            "mutation-recovery-required", executor_id
                        )
                    self._clear_transaction(
                        qualified_session_id,
                        reviewed_identity["source_revision"],
                    )
                    last_failure = RuntimeFailure("executor-failed", executor_id)
                    continue

                latest = source.call("inspect", session=qualified_session_id)["session"]
                validate_identity(latest, source_name)
                if not same_revision(reviewed_identity, latest):
                    if mutation_started:
                        raise RuntimeFailure(
                            "mutation-recovery-required", executor_id
                        )
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
                        "terminal_route": result.get("terminal_route"),
                        "reviewed_at": self.now(),
                    }
                )
                mutation_started = True
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
                return {"status": "accepted", "executor": executor_id}
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
                        }
                    )
                    self._write(self.paths.attempts, attempts)
                    raise RuntimeFailure("mutation-recovery-required", executor_id)
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
                    }
                )
                self._write(self.paths.attempts, attempts)
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
        inventory: list[dict[str, str]] = []
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
                inventory.append(
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
        proof = {
            "contract_version": CONTRACT_VERSION,
            "files": inventory,
            "skills_revision": revision,
            "orchestration_skills_absent": True,
        }
        bundle_id = digest(proof)
        bundle = self.paths.bundles / bundle_id.removeprefix("sha256:")
        if not bundle.exists():
            staging = self.paths.bundles / f".staging-{uuid.uuid4().hex}"
            staging.mkdir(parents=True)
            for item in inventory:
                source = skills_root / item["path"]
                destination = staging / item["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
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
            adapter = ExecutableAdapter(argv, role)
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
                adapter = ExecutableAdapter(argv, role)
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


def configured_runtime_settings(config: dict[str, Any]) -> dict[str, int]:
    defaults = {
        "policy_version": 1,
        "overlap_seconds": 300,
        "quiet_retry_seconds": 300,
        "page_size": 100,
        "max_pages_per_run": 100,
        "max_snapshot_bytes": 1_000_000,
        "max_events": 2_000,
        "max_field_bytes": 64_000,
    }
    settings: dict[str, int] = {}
    for name, default in defaults.items():
        value = config.get(name, default)
        minimum = 0 if name == "overlap_seconds" else 1
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise RuntimeFailure(
                "invalid-adapter-config",
                f"{name} must be an integer greater than or equal to {minimum}",
            )
        settings[name] = value
    return settings


def selftest(require_config: bool) -> dict[str, Any]:
    paths = default_paths()
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
        "errors": adapter_errors,
        "legacy_records_imported": imported_legacy,
    }
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
    for item in queue:
        if item.get("status") != "queued":
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
    report["ok"] = not report["errors"]
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("selftest")
    subcommands.add_parser("doctor")
    subcommands.add_parser("run")
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
        report = (
            scheduled_run()
            if args.command == "run"
            else selftest(require_config=args.command == "doctor")
        )
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
