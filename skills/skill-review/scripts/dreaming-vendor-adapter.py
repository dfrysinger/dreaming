#!/usr/bin/env python3
"""Native Copilot, Claude, and Codex adapters for Dreaming protocol v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

PROTOCOLS = {
    "session-source": (
        "dreaming.session-source",
        [
            "stable-pagination",
            "qualified-identity",
            "bounded-render",
            "revision-inspect",
        ],
    ),
    "review-executor": (
        "dreaming.review-executor",
        ["source-blind", "mutation-fence", "completion-sentinel"],
    ),
    "skill-publisher": (
        "dreaming.skill-publisher",
        ["content-addressed-bundle", "ownership-safe-remove", "exact-inventory"],
    ),
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
SUPPORTED_SOURCE_VERSIONS = {
    "copilot": 1,
    "claude": 1,
    "codex": 1,
}
COPILOT_EVENT_TYPES = {
    "abort",
    "assistant.message",
    "assistant.turn_end",
    "assistant.turn_start",
    "external_tool.completed",
    "external_tool.requested",
    "hook.end",
    "hook.start",
    "permission.completed",
    "permission.requested",
    "session.binary_asset",
    "session.compaction_complete",
    "session.compaction_start",
    "session.error",
    "session.info",
    "session.mode_changed",
    "session.model_change",
    "session.permissions_changed",
    "session.plan_changed",
    "session.resume",
    "session.shutdown",
    "session.start",
    "session.task_complete",
    "session.usage_checkpoint",
    "session.warning",
    "session.autopilot_objective_changed",
    "session.workspace_file_changed",
    "session.schedule_created",
    "session.schedule_cancelled",
    "skill.invoked",
    "subagent.completed",
    "subagent.selected",
    "subagent.started",
    "system.message",
    "system.notification",
    "tool.execution_complete",
    "tool.execution_start",
    "tool.user_requested",
    "user.message",
}
CLAUDE_EVENT_TYPES = {
    "ai-title",
    "assistant",
    "attachment",
    "file-history-snapshot",
    "last-prompt",
    "mode",
    "permission-mode",
    "queue-operation",
    "system",
    "user",
}
CODEX_EVENT_TYPES = {
    "compacted",
    "event_msg",
    "ghost_snapshot",
    "response_item",
    "session_meta",
    "turn_context",
}


class AdapterError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def emit(value: dict[str, Any], status: int = 0) -> None:
    print(json.dumps(value, sort_keys=True))
    raise SystemExit(status)


def fail(code: str, message: str) -> None:
    emit({"ok": False, "error": {"code": code, "message": message}}, 2)


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AdapterError("invalid-json", f"{path}: {error}") from error


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}"
    temporary.write_bytes(canonical(value) + b"\n")
    os.replace(temporary, path)


def opaque_scope(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def strict_root(path: Path) -> Path:
    if not path.exists():
        raise AdapterError("source-unavailable", str(path))
    if path.is_symlink():
        raise AdapterError("source-root-symlink", str(path))
    resolved = path.resolve()
    allowed = Path(os.environ.get("DREAMING_ADAPTER_ALLOWED_ROOT", Path.home())).resolve()
    if not within(resolved, allowed):
        raise AdapterError("source-root-escaped", str(path))
    return resolved


def strict_file(path: Path, root: Path) -> Path:
    if path.is_symlink():
        raise AdapterError("source-path-symlink", str(path))
    resolved = path.resolve()
    if not within(resolved, root):
        raise AdapterError("source-path-escaped", str(path))
    if not resolved.is_file():
        raise AdapterError("session-missing", str(path))
    return resolved


def bounded_text(value: Any, limit: int) -> tuple[str, bool]:
    if value is None:
        return "", False
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False)
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def normalized_event(
    source: str,
    native_id: str,
    sequence: int,
    timestamp: Any,
    kind: str,
    event_id: str,
    text: Any = "",
    tool_name: Any = None,
    field_limit: int = 64_000,
) -> tuple[dict[str, Any], bool]:
    if kind not in EVENT_KINDS:
        raise AdapterError("unknown-event-kind", kind)
    safe_text, truncated = bounded_text(text, field_limit)
    safe_tool, tool_truncated = bounded_text(tool_name, 512)
    return (
        {
            "source": source,
            "qualified_session_id": f"{source}:{native_id}",
            "sequence": sequence,
            "timestamp": timestamp,
            "kind": kind,
            "tool_name": safe_tool or None,
            "text": safe_text,
            "source_event_id": str(event_id),
        },
        truncated or tool_truncated,
    )


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {
            "text",
            "thinking",
            "input_text",
            "output_text",
        } and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def source_defaults(vendor: str) -> Path:
    home = Path.home()
    return {
        "copilot": Path(os.environ.get("COPILOT_HOME", home / ".copilot"))
        / "session-state",
        "claude": Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude"))
        / "projects",
        "codex": Path(os.environ.get("CODEX_HOME", home / ".codex")),
    }[vendor]


class NativeSource:
    def __init__(self, vendor: str, root: Path, quiet_seconds: int, field_limit: int):
        self.vendor = vendor
        self.root = strict_root(root)
        self.quiet_seconds = quiet_seconds
        self.field_limit = field_limit

    def records(self) -> list[dict[str, Any]]:
        if self.vendor == "copilot":
            return self._copilot_records()
        if self.vendor == "claude":
            return self._claude_records()
        return self._codex_records()

    def events(self, record: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        if self.vendor == "copilot":
            return self._copilot_events(record)
        if self.vendor == "claude":
            return self._claude_events(record)
        return self._codex_events(record)

    def identity(self, record: dict[str, Any]) -> dict[str, Any]:
        events, truncated = self.events(record)
        snapshot_digest = sha(events)
        frontier = events[-1]["source_event_id"] if events else "empty"
        completion = record["completion_state"]
        if (
            events
            and events[-1]["kind"] == "session_end"
            and not events[-1]["source_event_id"].endswith(":quiet")
        ):
            completion = "terminal"
        revision = sha(
            {
                "frontier": frontier,
                "snapshot_digest": snapshot_digest,
                "completion_state": completion,
                "adapter_version": SUPPORTED_SOURCE_VERSIONS[self.vendor],
                "truncated": truncated,
            }
        )
        native_id = record["native_session_id"]
        return {
            "source": self.vendor,
            "native_session_id": native_id,
            "qualified_session_id": f"{self.vendor}:{native_id}",
            "repository_scope": opaque_scope(record.get("cwd", "")),
            "started_at": record["started_at"],
            "updated_at": record["updated_at"],
            "source_revision": revision,
            "event_frontier": frontier,
            "snapshot_digest": snapshot_digest,
            "completion_state": completion,
            "adapter_version": SUPPORTED_SOURCE_VERSIONS[self.vendor],
            "features": self._features(events),
        }

    def find(self, qualified_id: str) -> dict[str, Any]:
        prefix = f"{self.vendor}:"
        if not qualified_id.startswith(prefix):
            raise AdapterError("source-mismatch", qualified_id)
        native_id = qualified_id[len(prefix) :]
        for record in self.records():
            if record["native_session_id"] == native_id:
                return record
        raise AdapterError("session-missing", qualified_id)

    def _completion(self, path: Path, terminal: bool) -> str:
        if terminal:
            return "terminal"
        age = max(0, time.time() - path.stat().st_mtime)
        return "quiet" if age >= self.quiet_seconds else "active"

    def _features(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        user = sum(item["kind"] == "user_message" for item in events)
        assistant = sum(item["kind"] == "assistant_message" for item in events)
        tools = [item["tool_name"] for item in events if item["kind"] == "tool_call"]
        lower = "\n".join(
            item["text"].lower() for item in events if item["kind"] == "user_message"
        )
        correction_terms = (
            "actually",
            "from now on",
            "remember",
            "stop doing",
            "too verbose",
            "just give me",
        )
        intent_terms = ("skill", "reusable", "procedure", "make this a")
        return {
            "user_turn_count": user,
            "assistant_turn_count": assistant,
            "tool_call_count": len(tools),
            "distinct_tool_count": len(set(filter(None, tools))),
            "correction_signals": sum(lower.count(term) for term in correction_terms),
            "skill_intent_signals": sum(lower.count(term) for term in intent_terms),
            "daemon_origin": "[autoreview-daemon-session:" in lower,
        }

    def _copilot_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for directory in sorted(self.root.iterdir()):
            if directory.is_symlink():
                raise AdapterError("source-path-symlink", str(directory))
            if not directory.is_dir():
                continue
            events_path = directory / "events.jsonl"
            if events_path.is_symlink():
                raise AdapterError("source-path-symlink", str(events_path))
            if not events_path.is_file():
                continue
            cwd = ""
            workspace = directory / "workspace.yaml"
            if workspace.is_symlink():
                raise AdapterError("source-path-symlink", str(workspace))
            if workspace.is_file():
                try:
                    for line in workspace.read_text(encoding="utf-8").splitlines():
                        if line.startswith("cwd:"):
                            cwd = line.split(":", 1)[1].strip()
                            break
                except OSError as error:
                    raise AdapterError("source-unavailable", str(workspace)) from error
            stat = events_path.stat()
            records.append(
                {
                    "native_session_id": directory.name,
                    "path": str(events_path),
                    "cwd": cwd,
                    "started_at": stat.st_ctime,
                    "updated_at": stat.st_mtime,
                    "order_time": stat.st_mtime,
                    "completion_state": self._completion(events_path, False),
                }
            )
        return records

    def _copilot_events(
        self, record: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], bool]:
        path = strict_file(Path(record["path"]), self.root)
        events: list[dict[str, Any]] = []
        truncated = False
        mapping = {
            "user.message": "user_message",
            "assistant.message": "assistant_message",
            "tool.execution_start": "tool_call",
            "tool.execution_complete": "tool_result",
            "session.task_complete": "summary",
            "session.shutdown": "session_end",
        }
        with path.open(encoding="utf-8") as handle:
            for raw in handle:
                item = json.loads(raw)
                if (
                    not isinstance(item, dict)
                    or item.get("type") not in COPILOT_EVENT_TYPES
                ):
                    raise AdapterError("unsupported-source-schema", str(path))
                kind = mapping.get(item.get("type"))
                if not kind:
                    continue
                data = item.get("data", {})
                if not isinstance(data, dict):
                    raise AdapterError("unsupported-source-schema", str(path))
                if kind in {"user_message", "assistant_message"}:
                    text = data.get("content", "")
                elif kind == "tool_call":
                    text = data.get("arguments", {})
                elif kind == "tool_result":
                    text = data.get("result", {})
                else:
                    text = data.get("summary", data.get("shutdownType", ""))
                event, clipped = normalized_event(
                    self.vendor,
                    record["native_session_id"],
                    len(events) + 1,
                    item.get("timestamp"),
                    kind,
                    item.get("id", len(events) + 1),
                    text,
                    data.get("toolName"),
                    self.field_limit,
                )
                events.append(event)
                truncated = truncated or clipped
        return events, truncated

    def _claude_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*/*.jsonl")):
            strict_file(path, self.root)
            native_id = path.stem
            stat = path.stat()
            records.append(
                {
                    "native_session_id": native_id,
                    "path": str(path),
                    "cwd": path.parent.name,
                    "started_at": stat.st_ctime,
                    "updated_at": stat.st_mtime,
                    "order_time": stat.st_mtime,
                    "completion_state": self._completion(path, False),
                }
            )
        return records

    def _claude_events(
        self, record: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], bool]:
        path = strict_file(Path(record["path"]), self.root)
        events: list[dict[str, Any]] = []
        truncated = False
        with path.open(encoding="utf-8") as handle:
            for raw in handle:
                item = json.loads(raw)
                if not isinstance(item, dict):
                    raise AdapterError("unsupported-source-schema", str(path))
                item_type = item.get("type")
                if (
                    item_type not in CLAUDE_EVENT_TYPES
                    or item.get("sessionId")
                    not in (None, record["native_session_id"])
                ):
                    raise AdapterError("unsupported-source-schema", str(path))
                message = item.get("message", {})
                if item_type in {"user", "assistant"}:
                    if not isinstance(message, dict):
                        raise AdapterError("unsupported-source-schema", str(path))
                    content = message.get("content", "")
                    if item_type == "user":
                        event, clipped = normalized_event(
                            self.vendor,
                            record["native_session_id"],
                            len(events) + 1,
                            item.get("timestamp"),
                            "user_message",
                            item.get("uuid", len(events) + 1),
                            content_text(content),
                            field_limit=self.field_limit,
                        )
                        events.append(event)
                        truncated = truncated or clipped
                        blocks = content if isinstance(content, list) else []
                        for index, block in enumerate(blocks):
                            if (
                                not isinstance(block, dict)
                                or block.get("type") != "tool_result"
                            ):
                                continue
                            event, clipped = normalized_event(
                                self.vendor,
                                record["native_session_id"],
                                len(events) + 1,
                                item.get("timestamp"),
                                "tool_result",
                                f"{item.get('uuid', len(events) + 1)}:{index}",
                                block.get("content", ""),
                                field_limit=self.field_limit,
                            )
                            events.append(event)
                            truncated = truncated or clipped
                        continue
                    blocks = content if isinstance(content, list) else []
                    text = content_text(blocks)
                    if text:
                        event, clipped = normalized_event(
                            self.vendor,
                            record["native_session_id"],
                            len(events) + 1,
                            item.get("timestamp"),
                            "assistant_message",
                            item.get("uuid", len(events) + 1),
                            text,
                            field_limit=self.field_limit,
                        )
                        events.append(event)
                        truncated = truncated or clipped
                    for index, block in enumerate(blocks):
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        event, clipped = normalized_event(
                            self.vendor,
                            record["native_session_id"],
                            len(events) + 1,
                            item.get("timestamp"),
                            "tool_call",
                            f"{item.get('uuid', len(events) + 1)}:{index}",
                            block.get("input", {}),
                            block.get("name"),
                            self.field_limit,
                        )
                        events.append(event)
                        truncated = truncated or clipped
                elif item_type == "system" and item.get("subtype") == "turn_duration":
                    event, clipped = normalized_event(
                        self.vendor,
                        record["native_session_id"],
                        len(events) + 1,
                        item.get("timestamp"),
                        "checkpoint",
                        item.get("uuid", len(events) + 1),
                        {"duration_ms": item.get("durationMs")},
                        field_limit=self.field_limit,
                    )
                    events.append(event)
                    truncated = truncated or clipped
        if record["completion_state"] in {"quiet", "terminal"}:
            event, clipped = normalized_event(
                self.vendor,
                record["native_session_id"],
                len(events) + 1,
                record["updated_at"],
                "session_end",
                f"{record['native_session_id']}:quiet",
                "quiet completion",
                field_limit=self.field_limit,
            )
            events.append(event)
            truncated = truncated or clipped
        return events, truncated

    def _codex_connection(self) -> sqlite3.Connection:
        database = strict_file(self.root / "state_5.sqlite", self.root)
        uri = f"file:{database}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True)
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(threads)")
            }
        except sqlite3.Error as error:
            raise AdapterError("source-unavailable", str(database)) from error
        required = {"id", "rollout_path", "created_at", "updated_at", "cwd"}
        if not required.issubset(columns):
            connection.close()
            raise AdapterError("unsupported-source-schema", str(database))
        return connection

    def _codex_records(self) -> list[dict[str, Any]]:
        connection = self._codex_connection()
        try:
            rows = connection.execute(
                "SELECT id, rollout_path, created_at, updated_at, cwd FROM threads "
                "WHERE has_user_event = 1 ORDER BY updated_at, id"
            ).fetchall()
        finally:
            connection.close()
        records: list[dict[str, Any]] = []
        for native_id, rollout, created, updated, cwd in rows:
            path = Path(rollout).expanduser()
            if path.is_symlink() or not path.is_file():
                raise AdapterError("session-missing", str(path))
            allowed = Path(
                os.environ.get("DREAMING_CODEX_ROLLOUT_ROOT", self.root)
            ).resolve()
            resolved = path.resolve()
            if not within(resolved, allowed):
                raise AdapterError("source-path-escaped", str(path))
            records.append(
                {
                    "native_session_id": str(native_id),
                    "path": str(resolved),
                    "cwd": str(cwd),
                    "started_at": int(created),
                    "updated_at": int(updated),
                    "order_time": int(updated),
                    "completion_state": self._completion(resolved, False),
                }
            )
        return records

    def _codex_events(
        self, record: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], bool]:
        allowed = Path(
            os.environ.get("DREAMING_CODEX_ROLLOUT_ROOT", self.root)
        ).resolve()
        path = strict_file(Path(record["path"]), allowed)
        events: list[dict[str, Any]] = []
        truncated = False
        with path.open(encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, 1):
                item = json.loads(raw)
                if (
                    not isinstance(item, dict)
                    or item.get("type") not in CODEX_EVENT_TYPES
                ):
                    raise AdapterError("unsupported-source-schema", str(path))
                payload = item.get("payload", {})
                if not isinstance(payload, dict):
                    continue
                item_type = item["type"]
                kind = None
                text: Any = ""
                tool_name = None
                if item_type == "response_item" and payload.get("type") == "message":
                    role = payload.get("role")
                    kind = (
                        "user_message"
                        if role == "user"
                        else "assistant_message"
                        if role == "assistant"
                        else None
                    )
                    text = content_text(payload.get("content"))
                elif item_type == "response_item" and payload.get("type") in {
                    "function_call",
                    "custom_tool_call",
                }:
                    kind = "tool_call"
                    text = payload.get("arguments", payload.get("input", ""))
                    tool_name = payload.get("name")
                elif item_type == "response_item" and payload.get("type") in {
                    "function_call_output",
                    "custom_tool_call_output",
                }:
                    kind = "tool_result"
                    text = payload.get("output", "")
                elif item_type == "event_msg" and payload.get("type") == "task_complete":
                    kind = "session_end"
                    text = payload.get("last_agent_message", "")
                if kind is None:
                    continue
                event, clipped = normalized_event(
                    self.vendor,
                    record["native_session_id"],
                    len(events) + 1,
                    item.get("timestamp"),
                    kind,
                    str(item.get("id", line_number)),
                    text,
                    tool_name,
                    self.field_limit,
                )
                events.append(event)
                truncated = truncated or clipped
        if (
            record["completion_state"] in {"quiet", "terminal"}
            and (not events or events[-1]["kind"] != "session_end")
        ):
            event, clipped = normalized_event(
                self.vendor,
                record["native_session_id"],
                len(events) + 1,
                record["updated_at"],
                "session_end",
                f"{record['native_session_id']}:quiet",
                "quiet completion",
                field_limit=self.field_limit,
            )
            events.append(event)
            truncated = truncated or clipped
        return events, truncated


def source_command(args: argparse.Namespace) -> None:
    source = NativeSource(
        args.vendor,
        Path(args.source_root or source_defaults(args.vendor)),
        args.quiet_seconds,
        args.max_field_bytes,
    )
    if args.command == "doctor":
        records = source.records()
        if records:
            source.identity(records[-1])
        emit({"ok": True, "healthy": True, "source": args.vendor})
    records = source.records()
    if args.command == "watermark":
        watermark = max((record["order_time"] for record in records), default=0)
        emit({"ok": True, "watermark": watermark})
    if args.command == "list":
        floor = json.loads(args.floor)
        ceiling = json.loads(args.ceiling)
        ordered = sorted(
            records,
            key=lambda record: (
                record["order_time"],
                record["native_session_id"],
            ),
        )
        eligible = [
            record
            for record in ordered
            if (floor is None or record["order_time"] >= floor)
            and record["order_time"] <= ceiling
        ]
        start = int(args.cursor or 0)
        page: list[dict[str, Any]] = []
        next_index = start
        while next_index < len(eligible) and len(page) < args.page_size:
            identity = source.identity(eligible[next_index])
            next_index += 1
            if not identity["features"]["daemon_origin"]:
                page.append(identity)
        emit(
            {
                "ok": True,
                "items": page,
                "next_cursor": str(next_index),
                "exhausted": next_index >= len(eligible),
            }
        )
    record = source.find(args.session)
    if args.command == "inspect":
        emit({"ok": True, "session": source.identity(record)})
    events, truncated = source.events(record)
    emit({"ok": True, "events": events, "truncated": truncated})


def executable(vendor: str) -> str:
    override = os.environ.get(f"DREAMING_{vendor.upper()}_BIN")
    candidate = override or shutil.which({"copilot": "copilot", "claude": "claude", "codex": "codex"}[vendor])
    if not candidate:
        raise AdapterError("executor-unavailable", vendor)
    path = Path(candidate).expanduser()
    canonical = path.parent.resolve() / path.name
    if not canonical.is_file():
        raise AdapterError("executor-unavailable", vendor)
    return str(canonical)


def run_process(
    command: list[str],
    environment: dict[str, str],
    timeout: int,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            cwd=cwd,
            start_new_session=True,
        )
    except OSError as error:
        raise AdapterError("executor-unavailable", str(error)) from error
    try:
        stdout, stderr = process.communicate(timeout=timeout)
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
        raise AdapterError("executor-timeout", str(error)) from error
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def copy_auth_file(source: Path, destination: Path) -> None:
    if not source.is_file() or source.is_symlink():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    os.chmod(destination, 0o600)


def executor_environment(vendor: str, work: Path) -> dict[str, str]:
    environment = os.environ.copy()
    real_home = Path.home()
    synthetic_home = work / "home"
    synthetic_home.mkdir()
    (work / "tmp").mkdir()
    (synthetic_home / ".cache").mkdir()
    (synthetic_home / ".config").mkdir()
    (synthetic_home / ".local/share").mkdir(parents=True)
    (synthetic_home / ".local/state").mkdir(parents=True)
    environment.update(
        {
            "HOME": str(synthetic_home),
            "TMPDIR": str(work / "tmp"),
            "XDG_CACHE_HOME": str(synthetic_home / ".cache"),
            "XDG_CONFIG_HOME": str(synthetic_home / ".config"),
            "XDG_DATA_HOME": str(synthetic_home / ".local/share"),
            "XDG_STATE_HOME": str(synthetic_home / ".local/state"),
            "CLAUDE_CODE_TMPDIR": str(work / "tmp"),
        }
    )
    if vendor == "copilot":
        copy_auth_file(
            real_home / ".config/gh/hosts.yml",
            synthetic_home / ".config/gh/hosts.yml",
        )
        copy_auth_file(
            real_home / ".config/gh/config.yml",
            synthetic_home / ".config/gh/config.yml",
        )
        copy_auth_file(
            real_home / ".copilot/config.json",
            synthetic_home / ".copilot/config.json",
        )
    elif vendor == "claude":
        environment["HOME"] = str(real_home)
    else:
        codex_home = synthetic_home / ".codex"
        codex_home.mkdir()
        copy_auth_file(real_home / ".codex/auth.json", codex_home / "auth.json")
        environment["CODEX_HOME"] = str(codex_home)
    return environment


def sandbox_quote(path: Path | str) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def sandbox_profile(
    work: Path,
    binary: str,
    denied_roots: Iterable[str],
    vendor: str,
) -> Path:
    profile = work / "executor.sb"
    real_home = Path.home().resolve()
    rules = [
        "(version 1)",
        "(allow default)",
        f'(deny file-read* file-write* (subpath "{sandbox_quote(real_home)}"))',
    ]
    for root in {
        Path("/tmp"),
        Path("/private/tmp"),
        Path(tempfile.gettempdir()).resolve(),
        *(Path(value).expanduser().resolve() for value in denied_roots),
    }:
        rules.append(
            f'(deny file-read* file-write* (subpath "{sandbox_quote(root)}"))'
        )
        rules.append(
            f'(deny file-read* file-write* (literal "{sandbox_quote(root)}"))'
        )
    binary_path = Path(binary).expanduser()
    for allowed in {
        work.resolve(),
        binary_path.resolve(),
        binary_path,
    }:
        operation = "subpath" if allowed.is_dir() else "literal"
        rules.append(
            f'(allow file-read* file-write* ({operation} "{sandbox_quote(allowed)}"))'
        )
    for parent in [work.resolve(), *work.resolve().parents]:
        rules.append(
            f'(allow file-read-metadata (literal "{sandbox_quote(parent)}"))'
        )
    if vendor == "claude":
        for path in (
            real_home / ".claude.json",
            real_home / ".claude/settings.json",
            real_home / "Library/Keychains/login.keychain-db",
        ):
            rules.append(
                f'(allow file-read* (literal "{sandbox_quote(path)}"))'
            )
    if vendor == "copilot":
        for path in (
            real_home,
            real_home / ".local",
            real_home / ".local/bin",
        ):
            rules.append(
                f'(allow file-read* (literal "{sandbox_quote(path)}"))'
            )
    test_roots = [
        value
        for value in (
            os.environ.get("DREAMING_EXECUTOR_TEST_ALLOW_ROOTS", "").split(
                os.pathsep
            )
            + [os.environ.get("DREAMING_EXECUTOR_TEST_ALLOW_ROOT", "")]
        )
        if value
    ]
    for test_root in test_roots:
        rules.append(
            '(allow file-read* file-write* (subpath "'
            + sandbox_quote(Path(test_root).expanduser().resolve())
            + '"))'
        )
    profile.write_text("\n".join(rules) + "\n", encoding="utf-8")
    return profile


def sandboxed_command(
    command: list[str],
    work: Path,
    binary: str,
    denied_roots: Iterable[str],
    vendor: str,
) -> list[str]:
    sandbox = Path("/usr/bin/sandbox-exec")
    if not sandbox.is_file():
        raise AdapterError(
            "executor-boundary-unavailable",
            "macOS sandbox-exec is required",
        )
    profile = sandbox_profile(work, binary, denied_roots, vendor)
    return [str(sandbox), "-f", str(profile), *command]


def prove_boundary(
    work: Path,
    environment: dict[str, str],
    binary: str,
    denied_roots: Iterable[str],
    vendor: str,
) -> None:
    allowed = work / "boundary-allowed"
    allowed.write_text("allowed\n", encoding="utf-8")
    denied_dir = Path(tempfile.mkdtemp(prefix="dreaming-boundary-denied-"))
    try:
        denied = denied_dir / "canary"
        denied.write_text("denied\n", encoding="utf-8")
        roots = [*denied_roots, str(denied_dir)]
        allowed_result = run_process(
            sandboxed_command(
                ["/bin/cat", str(allowed)],
                work,
                binary,
                roots,
                vendor,
            ),
            environment,
            10,
            cwd=work,
        )
        denied_result = run_process(
            sandboxed_command(
                ["/bin/cat", str(denied)],
                work,
                binary,
                roots,
                vendor,
            ),
            environment,
            10,
            cwd=work,
        )
        if allowed_result.returncode != 0 or denied_result.returncode == 0:
            raise AdapterError(
                "executor-boundary-unavailable",
                "filesystem boundary canary failed",
            )
    finally:
        shutil.rmtree(denied_dir, ignore_errors=True)


def executor_doctor(args: argparse.Namespace) -> dict[str, Any]:
    binary = executable(args.vendor)
    probe_timeout = min(args.timeout, 60)
    with tempfile.TemporaryDirectory(prefix=f"dreaming-{args.vendor}-doctor-") as raw:
        work = Path(raw).resolve()
        environment = executor_environment(args.vendor, work)
        prove_boundary(work, environment, binary, args.deny_root, args.vendor)
        result = run_process(
            sandboxed_command(
                [binary, "--version"],
                work,
                binary,
                args.deny_root,
                args.vendor,
            ),
            environment,
            probe_timeout,
            cwd=work,
        )
        if result.returncode != 0:
            raise AdapterError("executor-unavailable", args.vendor)
        if args.vendor == "claude":
            authentication = run_process(
                sandboxed_command(
                    [binary, "auth", "status", "--json"],
                    work,
                    binary,
                    args.deny_root,
                    args.vendor,
                ),
                environment,
                probe_timeout,
                cwd=work,
            )
            try:
                logged_in = json.loads(authentication.stdout).get("loggedIn") is True
            except (json.JSONDecodeError, AttributeError):
                logged_in = False
            if authentication.returncode != 0 or not logged_in:
                raise AdapterError("authentication-required", args.vendor)
        if args.vendor == "codex":
            authentication = run_process(
                sandboxed_command(
                    [binary, "login", "status"],
                    work,
                    binary,
                    args.deny_root,
                    args.vendor,
                ),
                environment,
                probe_timeout,
                cwd=work,
            )
            if authentication.returncode != 0:
                raise AdapterError("authentication-required", args.vendor)
    return {
        "ok": True,
        "healthy": True,
        "boundary_ready": True,
        "executor_version": (result.stdout or result.stderr).strip(),
        "boundary": (
            "sandboxed-auth-no-tools-native-session-and-unrelated-home-denial"
        ),
    }


def bounded_file_text(path: Path, limit: int) -> str:
    value = path.read_text(encoding="utf-8")
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore") + "\n[TRUNCATED]\n"


def review_context() -> dict[str, Any]:
    repo_root = Path(
        os.environ.get("DREAMING_REPO_ROOT", Path(__file__).parents[3])
    ).resolve()
    skills_root = Path(
        os.environ.get(
            "DREAMING_SKILLS_ROOT",
            Path.home() / ".local/share/dreaming/skills",
        )
    ).resolve()
    context: dict[str, Any] = {
        "artifact_routing": bounded_file_text(
            repo_root / "skills/skill-review/references/artifact-routing.md",
            32_000,
        ),
        "skills": [],
        "tombstones": [],
    }
    total = 0
    if skills_root.is_dir():
        for directory in sorted(skills_root.iterdir()):
            if directory.is_symlink():
                raise AdapterError("skills-context-symlink", str(directory))
            skill_file = directory / "SKILL.md"
            if not directory.is_dir() or not skill_file.is_file():
                continue
            if skill_file.is_symlink():
                raise AdapterError("skills-context-symlink", str(skill_file))
            content = bounded_file_text(skill_file, 64_000)
            size = len(content.encode("utf-8"))
            if total + size > 512_000:
                context["skills_truncated"] = True
                break
            context["skills"].append(
                {
                    "name": directory.name,
                    "agent_created": (directory / ".agent-created").is_file(),
                    "skill_markdown": content,
                }
            )
            total += size
    state_dir = Path(
        os.environ.get("DREAMING_STATE_DIR", Path.home() / ".local/state/dreaming")
    )
    tombstones = state_dir / "skill-review/tombstones"
    if tombstones.is_dir() and not tombstones.is_symlink():
        for path in sorted(tombstones.glob("*.json")):
            if path.is_symlink():
                raise AdapterError("skills-context-symlink", str(path))
            value = load_json(path, {})
            if isinstance(value, dict):
                context["tombstones"].append(
                    {
                        "name": path.stem,
                        "replacement": value.get("replacement"),
                        "reason": value.get("reason"),
                    }
                )
    shared_root = os.environ.get("DREAMING_SHARED_SKILLS_ROOT")
    if shared_root:
        for key, relative in (
            ("writing_rubric", "skills/writing-great-skills/SKILL.md"),
            ("dual_review_protocol", "skills/dual-review/SKILL.md"),
        ):
            rubric = Path(shared_root) / relative
            if rubric.is_file() and not rubric.is_symlink():
                context[key] = bounded_file_text(rubric, 64_000)
    return context


def review_result_schema() -> dict[str, Any]:
    artifact = {
        "type": ["object", "null"],
        "properties": {
            "operation": {"enum": ["create", "patch", "support_file"]},
            "skill_name": {"type": "string"},
            "skill_markdown": {
                "type": "string",
                "pattern": "^---\\nname: [a-z0-9]+(?:-[a-z0-9]+)*\\ndescription: \\S",
                "description": (
                    "Complete SKILL.md content. It must start with YAML "
                    "frontmatter whose name exactly matches skill_name and "
                    "whose description states when to use the skill."
                ),
            },
            "support_files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "operation",
            "skill_name",
            "skill_markdown",
            "support_files",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "terminal_route": {
                "enum": [
                    "discard",
                    "instruction",
                    "factual_memory",
                    "skill",
                    "support_file",
                ]
            },
            "summary": {"type": "string"},
            "routing_reason": {"type": "string"},
            "artifact": artifact,
        },
        "required": [
            "terminal_route",
            "summary",
            "routing_reason",
            "artifact",
        ],
        "additionalProperties": False,
    }


def review_prompt(snapshot: dict[str, Any]) -> str:
    if snapshot.get("packet_kind") == "draft_review":
        return json.dumps(
            {
                "task": (
                    "Independently review this proposed durable artifact. Apply the "
                    "supplied dual-review and writing protocols. Reject private data, "
                    "unsupported claims, unsafe instructions, weak reuse value, or an "
                    "artifact that does not match its route. Use no tools or external "
                    "knowledge. Return JSON matching result_schema."
                ),
                "result_schema": {
                    "type": "object",
                    "properties": {
                        "decision": {"enum": ["approve", "reject"]},
                        "summary": {"type": "string"},
                    },
                    "required": ["decision", "summary"],
                    "additionalProperties": False,
                },
                "context": review_context(),
                "draft": snapshot,
            },
            sort_keys=True,
        )
    packet = {
        "task": (
            "Review this bounded normalized session for a durable reusable "
            "artifact. Use only the supplied snapshot and context. Do not use "
            "tools or external knowledge. Return JSON matching result_schema. "
            "Never include private names, repositories, URLs, credentials, or "
            "source-specific paths in durable content. Scope guarantees to what "
            "the snapshot actually proves; do not turn a precondition check "
            "into an unsupported atomicity or recovery claim."
        ),
        "policy": {
            "reuse_order": [
                "patch an existing matching skill",
                "add a support file to an existing skill",
                "create a new skill",
                "record a recommendation",
                "discard",
            ],
            "instruction_and_factual_memory_are_recommendation_only": True,
            "artifact_required_for": ["skill", "support_file"],
            "artifact_forbidden_for": [
                "discard",
                "instruction",
                "factual_memory",
            ],
        },
        "result_schema": review_result_schema(),
        "context": review_context(),
        "snapshot": snapshot,
    }
    return json.dumps(packet, sort_keys=True)


def parse_model_result(text: str) -> dict[str, Any]:
    def find(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            if isinstance(value.get("terminal_route"), str) or isinstance(
                value.get("decision"), str
            ):
                return value
            for nested in value.values():
                found = find(nested)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = find(nested)
                if found is not None:
                    return found
        elif isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return None
            return find(parsed)
        return None

    candidates = [line.strip() for line in text.splitlines() if line.strip()]
    candidates.append(text.strip())
    for candidate in reversed(candidates):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        found = find(value)
        if found is not None:
            return found
    raise AdapterError("malformed-executor-result", "model returned no route JSON")


def executor_run(args: argparse.Namespace) -> None:
    snapshot = load_json(Path(args.snapshot))
    draft_review = (
        isinstance(snapshot, dict)
        and snapshot.get("packet_kind") == "draft_review"
    )
    if not isinstance(snapshot, dict) or (
        not draft_review and not isinstance(snapshot.get("events"), list)
    ):
        raise AdapterError("snapshot-invalid", args.snapshot)
    binary = executable(args.vendor)
    prompt = review_prompt(snapshot)
    with tempfile.TemporaryDirectory(prefix=f"dreaming-{args.vendor}-") as work:
        work_path = Path(work).resolve()
        environment = executor_environment(args.vendor, work_path)
        schema = work_path / "result-schema.json"
        active_schema = (
            {
                "type": "object",
                "properties": {
                    "decision": {"enum": ["approve", "reject"]},
                    "summary": {"type": "string"},
                },
                "required": ["decision", "summary"],
                "additionalProperties": False,
            }
            if draft_review
            else review_result_schema()
        )
        schema.write_text(json.dumps(active_schema), encoding="utf-8")
        output = work_path / "last-message.json"
        if args.vendor == "copilot":
            command = [
                binary,
                "-p",
                prompt,
                "--allow-all-tools",
                "--available-tools=__dreaming_no_tools__",
                "--disable-builtin-mcps",
                "--no-custom-instructions",
                "--no-ask-user",
                "--no-remote",
                "--no-color",
                "--output-format",
                "json",
                "-C",
                str(work_path),
            ]
        elif args.vendor == "claude":
            command = [
                binary,
                "--print",
                prompt,
                "--safe-mode",
                "--disable-slash-commands",
                "--setting-sources",
                "",
                "--settings",
                "{}",
                "--allowedTools",
                "",
                "--permission-mode",
                "dontAsk",
                "--max-budget-usd",
                os.environ.get("DREAMING_CLAUDE_MAX_BUDGET_USD", "1.00"),
                "--no-session-persistence",
                "--output-format",
                "json",
                "--json-schema",
                schema.read_text(encoding="utf-8"),
            ]
        else:
            command = [
                binary,
                "--ask-for-approval",
                "never",
                "exec",
                prompt,
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--output-schema",
                str(schema),
                "--output-last-message",
                str(output),
                "-C",
                str(work_path),
            ]
        command = sandboxed_command(
            command,
            work_path,
            binary,
            args.deny_root,
            args.vendor,
        )
        result = run_process(
            command,
            environment,
            args.timeout,
            cwd=work_path,
        )
        if result.returncode != 0:
            raise AdapterError(
                "executor-failed",
                (result.stderr or result.stdout).strip()[-1000:] or args.vendor,
            )
        text = output.read_text(encoding="utf-8") if output.exists() else result.stdout
        model_result = parse_model_result(text)
    if draft_review:
        if model_result.get("decision") not in {"approve", "reject"}:
            raise AdapterError("malformed-executor-result", "decision")
        if not isinstance(model_result.get("summary"), str) or not model_result[
            "summary"
        ].strip():
            raise AdapterError("malformed-executor-result", "summary")
        final = {
            "status": "ok",
            "mutation_started": False,
            "completion_sentinel": "DREAMING_DRAFT_REVIEW_COMPLETE",
            "decision": model_result["decision"],
            "summary": model_result["summary"],
            "model": os.environ.get(
                f"DREAMING_{args.vendor.upper()}_REVIEW_MODEL",
                f"{args.vendor}-default",
            ),
        }
        atomic_json(Path(args.result), final)
        emit({"ok": True, **final})
    terminal_route = model_result["terminal_route"]
    if terminal_route not in {
        "discard",
        "instruction",
        "factual_memory",
        "skill",
        "support_file",
    }:
        raise AdapterError("malformed-executor-result", terminal_route)
    for field in ("summary", "routing_reason"):
        if not isinstance(model_result.get(field), str) or not model_result[field].strip():
            raise AdapterError("malformed-executor-result", field)
    if "artifact" not in model_result:
        raise AdapterError("malformed-executor-result", "artifact")
    final = {
        "status": "ok",
        "mutation_started": False,
        "completion_sentinel": "DREAMING_REVIEW_COMPLETE",
        "terminal_route": terminal_route,
        "summary": model_result["summary"],
        "routing_reason": model_result["routing_reason"],
        "artifact": model_result["artifact"],
    }
    atomic_json(Path(args.result), final)
    emit({"ok": True, **final})


def publisher_state(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    path = Path(
        args.ownership_journal
        or os.environ.get(
            "DREAMING_PUBLISHER_JOURNAL",
            Path(os.environ.get("DREAMING_STATE_DIR", Path.home() / ".local/state/dreaming"))
            / "publisher-ownership.json",
        )
    )
    state = load_json(path, {})
    if not isinstance(state, dict):
        raise AdapterError("ownership-journal-invalid", str(path))
    return path, state


def skill_names(bundle: Path) -> list[str]:
    manifest = load_json(bundle / "dreaming-bundle-manifest.json", {})
    files = manifest.get("files", []) if isinstance(manifest, dict) else []
    names = {
        Path(item["path"]).parts[0]
        for item in files
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and len(Path(item["path"]).parts) > 1
        and Path(item["path"]).parts[1] == "SKILL.md"
    }
    return sorted(names)


def run_native(command: list[str], timeout: int = 60) -> str:
    result = run_process(command, os.environ.copy(), timeout)
    if result.returncode != 0:
        raise AdapterError(
            "publisher-native-failed",
            (result.stderr or result.stdout).strip()[-1000:] or "native command failed",
        )
    return result.stdout


def publication_descriptor(vendor: str, bundle: Path, bundle_id: str) -> dict[str, Any]:
    native_manifest = load_json(bundle / ".claude-plugin/plugin.json", {})
    name = native_manifest.get("name")
    if not isinstance(name, str) or not name:
        raise AdapterError("bundle-proof-invalid", "publication manifest has no name")
    return {
        "vendor": vendor,
        "bundle": str(bundle),
        "bundle_id": bundle_id,
        "skills": skill_names(bundle),
        "name": name,
    }


def remove_native_registration(vendor: str, descriptor: dict[str, Any]) -> None:
    binary = executable(vendor)
    if vendor == "copilot":
        run_native([binary, "skill", "remove", descriptor["bundle"]])
    elif vendor == "claude":
        run_native(
            [
                binary,
                "plugin",
                "uninstall",
                f"{descriptor['name']}@{descriptor['name']}",
            ]
        )
        run_native(
            [binary, "plugin", "marketplace", "remove", descriptor["name"]]
        )
    else:
        run_native(
            [
                binary,
                "plugin",
                "remove",
                descriptor["name"],
                "--marketplace",
                descriptor["name"],
                "--json",
            ]
        )
        run_native(
            [binary, "plugin", "marketplace", "remove", descriptor["name"]]
        )


def superseded_descriptors(row: dict[str, Any]) -> list[dict[str, Any]]:
    result = [
        item
        for item in row.get("superseded", [])
        if isinstance(item, dict)
    ]
    previous = row.get("previous")
    while isinstance(previous, dict):
        nested = dict(previous)
        previous = nested.pop("previous", None)
        nested.pop("superseded", None)
        result.append(nested)
    return result


def remove_if_present(vendor: str, descriptor: dict[str, Any]) -> None:
    if inventory_contains(vendor, inventory_json(vendor), descriptor):
        remove_native_registration(vendor, descriptor)


def publisher_install(args: argparse.Namespace) -> None:
    bundle = Path(args.bundle).resolve()
    manifest = load_json(bundle / "dreaming-bundle-manifest.json", {})
    if manifest.get("bundle_id") != args.bundle_id:
        raise AdapterError("bundle-proof-invalid", args.bundle_id)
    path, state = publisher_state(args)
    existing = state.get(args.vendor)
    if existing and existing.get("bundle_id") == args.bundle_id:
        if inventory_contains(args.vendor, inventory_json(args.vendor), existing):
            emit({"ok": True, "installed": True, "bundle_id": args.bundle_id})
    descriptor = publication_descriptor(args.vendor, bundle, args.bundle_id)
    binary = executable(args.vendor)
    if args.vendor == "copilot":
        run_native([binary, "skill", "add", str(bundle)])
    elif args.vendor == "claude":
        run_native([binary, "plugin", "marketplace", "add", str(bundle)])
        run_native(
            [
                binary,
                "plugin",
                "install",
                f"{descriptor['name']}@{descriptor['name']}",
            ]
        )
    else:
        run_native([binary, "plugin", "marketplace", "add", str(bundle)])
        run_native(
            [
                binary,
                "plugin",
                "add",
                descriptor["name"],
                "--marketplace",
                descriptor["name"],
                "--json",
            ]
        )
    if args.vendor != "copilot":
        identity = native_identity(args.vendor, inventory_json(args.vendor), descriptor)
        if identity is None:
            raise AdapterError(
                "publisher-verification-failed",
                f"native registration identity missing for {descriptor['name']}",
            )
        descriptor["native_identity"] = identity
    if existing and existing.get("bundle_id") != args.bundle_id:
        current = dict(existing)
        inherited = superseded_descriptors(current)
        current.pop("previous", None)
        current.pop("superseded", None)
        descriptor["superseded"] = [*inherited, current]
    elif existing:
        inherited = superseded_descriptors(existing)
        if inherited:
            descriptor["superseded"] = inherited
    state[args.vendor] = descriptor
    atomic_json(path, state)
    emit({"ok": True, "installed": True, "bundle_id": args.bundle_id})


def inventory_json(vendor: str) -> Any:
    binary = executable(vendor)
    if vendor == "copilot":
        return json.loads(run_native([binary, "skill", "list", "--json"]))
    if vendor == "claude":
        return {
            "plugins": json.loads(
                run_native([binary, "plugin", "list", "--json"])
            ),
            "marketplaces": json.loads(
                run_native([binary, "plugin", "marketplace", "list", "--json"])
            ),
        }
    return {
        "plugins": json.loads(
            run_native([binary, "plugin", "list", "--available", "--json"])
        ),
        "marketplaces": json.loads(
            run_native([binary, "plugin", "marketplace", "list", "--json"])
        ),
    }


def canonical_inventory_path(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    path = Path(value).expanduser()
    return str(path.resolve()) if path.is_absolute() else value


def native_identity(
    vendor: str, inventory: Any, descriptor: dict[str, Any]
) -> dict[str, Any] | None:
    if not isinstance(inventory, dict):
        return None
    name = descriptor["name"]
    if vendor == "claude":
        plugins = inventory.get("plugins")
        marketplaces = inventory.get("marketplaces")
        if not isinstance(plugins, list) or not isinstance(marketplaces, list):
            return None
        plugin_matches = [
            item
            for item in plugins
            if isinstance(item, dict) and item.get("id") == f"{name}@{name}"
        ]
        marketplace_matches = [
            item
            for item in marketplaces
            if isinstance(item, dict) and item.get("name") == name
        ]
        if len(plugin_matches) != 1 or len(marketplace_matches) != 1:
            return None
        plugin = plugin_matches[0]
        marketplace = marketplace_matches[0]
        return {
            "plugin": {
                "id": plugin.get("id"),
                "installPath": canonical_inventory_path(plugin.get("installPath")),
                "scope": plugin.get("scope"),
                "version": plugin.get("version"),
            },
            "marketplace": {
                "name": marketplace.get("name"),
                "source": marketplace.get("source"),
                "repo": marketplace.get("repo"),
                "installLocation": canonical_inventory_path(
                    marketplace.get("installLocation")
                ),
            },
        }
    plugins = inventory.get("plugins")
    marketplaces = inventory.get("marketplaces")
    if not isinstance(plugins, dict) or not isinstance(marketplaces, dict):
        return None
    installed = plugins.get("installed")
    marketplace_rows = marketplaces.get("marketplaces")
    if not isinstance(installed, list) or not isinstance(marketplace_rows, list):
        return None
    plugin_matches = [
        item
        for item in installed
        if isinstance(item, dict)
        and item.get("name") == name
        and item.get("marketplaceName") == name
        and item.get("installed") is True
    ]
    marketplace_matches = [
        item
        for item in marketplace_rows
        if isinstance(item, dict) and item.get("name") == name
    ]
    if len(plugin_matches) != 1 or len(marketplace_matches) != 1:
        return None
    plugin = plugin_matches[0]
    marketplace = marketplace_matches[0]
    plugin_source = dict(plugin.get("source", {}))
    plugin_source["path"] = canonical_inventory_path(plugin_source.get("path"))
    plugin_marketplace = dict(plugin.get("marketplaceSource", {}))
    if plugin_marketplace.get("sourceType") == "local":
        plugin_marketplace["source"] = canonical_inventory_path(
            plugin_marketplace.get("source")
        )
    marketplace_source = dict(marketplace.get("marketplaceSource", {}))
    if marketplace_source.get("sourceType") == "local":
        marketplace_source["source"] = canonical_inventory_path(
            marketplace_source.get("source")
        )
    return {
        "plugin": {
            "pluginId": plugin.get("pluginId"),
            "name": plugin.get("name"),
            "marketplaceName": plugin.get("marketplaceName"),
            "marketplaceSource": plugin_marketplace,
            "source": plugin_source,
            "version": plugin.get("version"),
            "installed": plugin.get("installed"),
        },
        "marketplace": {
            "name": marketplace.get("name"),
            "marketplaceSource": marketplace_source,
            "root": canonical_inventory_path(marketplace.get("root")),
        },
    }


def inventory_contains(vendor: str, inventory: Any, descriptor: dict[str, Any]) -> bool:
    if vendor == "copilot":
        bundle = str(Path(descriptor["bundle"]).resolve())
        names = set(descriptor.get("skills", []))
        found = {
            item.get("name")
            for item in inventory
            if isinstance(item, dict)
            and str(Path(item.get("path", "")).resolve()).startswith(bundle + os.sep)
        }
        return names.issubset(found)
    expected = descriptor.get("native_identity")
    return isinstance(expected, dict) and native_identity(
        vendor, inventory, descriptor
    ) == expected


def publisher_command(args: argparse.Namespace) -> None:
    path, state = publisher_state(args)
    if args.command == "doctor":
        executable(args.vendor)
        emit({"ok": True, "healthy": True})
    if args.command == "inventory":
        row = state.get(args.vendor)
        owned = []
        if row:
            owned = [
                row["bundle_id"],
                *[
                    item["bundle_id"]
                    for item in superseded_descriptors(row)
                    if isinstance(item.get("bundle_id"), str)
                ],
            ]
        emit(
            {
                "ok": True,
                "owned_bundle_ids": owned,
            }
        )
    if args.command == "install":
        publisher_install(args)
    row = state.get(args.vendor)
    if args.command == "verify":
        verified = bool(
            row
            and row.get("bundle_id") == args.bundle_id
            and inventory_contains(args.vendor, inventory_json(args.vendor), row)
        )
        if verified and superseded_descriptors(row):
            for descriptor in superseded_descriptors(row):
                remove_if_present(args.vendor, descriptor)
            row.pop("previous", None)
            row.pop("superseded", None)
            state[args.vendor] = row
            atomic_json(path, state)
        emit(
            {
                "ok": True,
                "verified": verified,
                "bundle_id": args.bundle_id if verified else None,
            }
        )
    if args.command == "remove":
        if row:
            remove_if_present(args.vendor, row)
            for descriptor in superseded_descriptors(row):
                remove_if_present(args.vendor, descriptor)
            del state[args.vendor]
            atomic_json(path, state)
        emit({"ok": True, "removed": True})
    raise AdapterError("unsupported-command", args.command)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--vendor", required=True, choices=("copilot", "claude", "codex"))
    result.add_argument("--role", required=True, choices=PROTOCOLS)
    result.add_argument("--source-root")
    result.add_argument("--quiet-seconds", type=int, default=300)
    result.add_argument("--max-field-bytes", type=int, default=64_000)
    result.add_argument("--timeout", type=int, default=600)
    result.add_argument("--ownership-journal")
    result.add_argument("--deny-root", action="append", default=[])
    sub = result.add_subparsers(dest="command", required=True)
    contract = sub.add_parser("contract")
    contract.add_argument("--role", dest="contract_role", required=True)
    for name in ("doctor", "watermark", "version", "inventory", "remove"):
        sub.add_parser(name)
    listing = sub.add_parser("list")
    listing.add_argument("--floor", required=True)
    listing.add_argument("--ceiling", required=True)
    listing.add_argument("--cursor", required=True)
    listing.add_argument("--page-size", required=True, type=int)
    for name in ("inspect", "render"):
        command = sub.add_parser(name)
        command.add_argument("--session", required=True)
    run = sub.add_parser("run")
    run.add_argument("--snapshot", required=True)
    run.add_argument("--result", required=True)
    install = sub.add_parser("install")
    install.add_argument("--bundle", required=True)
    install.add_argument("--bundle-id", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--bundle-id", required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "contract":
            if args.contract_role != args.role:
                raise AdapterError("role-mismatch", args.contract_role)
            protocol, capabilities = PROTOCOLS[args.role]
            emit(
                {
                    "ok": True,
                    "protocol": protocol,
                    "version": 1,
                    "adapter_id": args.vendor,
                    "capabilities": capabilities,
                }
            )
        if args.role == "session-source":
            source_command(args)
        if args.role == "review-executor":
            if args.command == "doctor":
                emit(executor_doctor(args))
            if args.command == "version":
                emit(executor_doctor(args))
            if args.command == "run":
                executor_run(args)
            raise AdapterError("unsupported-command", args.command)
        publisher_command(args)
    except AdapterError as error:
        fail(error.code, error.message)
    except (OSError, json.JSONDecodeError, sqlite3.Error, ValueError) as error:
        fail("adapter-failed", str(error))


if __name__ == "__main__":
    main()
