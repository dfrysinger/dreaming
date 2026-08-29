#!/usr/bin/env python3
"""Vendor-neutral Dreaming Milestone 1 runtime core.

Adapters are executable JSON protocol clients.  This module owns durable
discovery state, immutable snapshots, routing, fallback, result admission,
legacy migration, and content-addressed publication.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import errno
import fcntl
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

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from remote_subject_policy import (
    REMOTE_SUBJECT_SIDECARS,
    RemoteSubjectPolicyError,
    load_content_policy,
    validate_text,
)
from task_profile_receipt import (
    TaskProfileReceiptError,
    compatible_task_profile_executor_identities,
    validate_task_profile_receipt,
)
from profile_audit_disposition import (
    CURRENT_PROFILE_AUDIT_CONTRACT_VERSION,
    ProfileAuditDispositionError,
    build_profile_audit_disposition,
    validate_profile_audit_disposition,
)
from profile_evaluation_routing import (
    EvaluationRoutingError,
    build_evaluation_routing_row,
    summarize_evaluation_routing,
)
from task_pass_accounting import (
    TaskPassAccountingError,
    build_task_pass_accounting_receipt,
    queue_row_identity,
    validate_task_pass_accounting_receipt,
)
from task_occurrence import (
    TaskOccurrenceError,
    build_correction_attempt as build_task_occurrence_correction_attempt,
    build_resolution as build_task_occurrence_resolution,
    load_all as load_task_occurrence_resolutions,
    load_correction_attempts as load_task_occurrence_correction_attempts,
    load_exact as load_exact_task_occurrence,
    persist as persist_task_occurrence_resolution,
    persist_correction_attempt as persist_task_occurrence_correction_attempt,
    resolution_path as task_occurrence_resolution_path,
    validate_resolution as validate_task_occurrence_resolution,
)

CONTRACT_VERSION = 1
TASK_PROFILE_CAPABILITY = "task-profile-v2"
TASK_PROFILE_SNAPSHOT_CONTRACT_VERSION = 1
TASK_OCCURRENCE_REVIEW_CONTRACT = "profile-catalog-review-occurrence-v1"
TASK_OCCURRENCE_CORRECTION_CONTRACT = "profile-boundary-correction-v1"
MAX_CANDIDATE_GROUPS_PER_REVIEW = 20
MAX_CANDIDATE_GROUP_CONTEXT_BYTES = 48_000
CANDIDATE_LIFECYCLE_STATES = {
    "collecting",
    "legacy_probation",
    "ready_for_draft",
    "evaluating",
    "portfolio_pending",
    "admitted",
    "expired",
    "rejected",
    "quarantined",
    "absorbed",
    "archived",
}
ELIGIBLE_CANDIDATE_GROUP_STATES = {
    "collecting",
    "ready_for_draft",
    "expired",
    "rejected",
}
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
REMOTE_EVALUATION_SUBJECT_KEYS = {
    "enabled",
    "protocol_version",
    "origin_host_id",
    "command",
    "receiver",
    "max_files",
    "max_file_bytes",
    "max_decoded_bytes",
    "max_encoded_bytes",
    "snapshot_root",
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
EVALUATION_OVERLAY_REGISTRY_IDENTITY = {
    "claim_schema_version": 4,
    "input_registry_schema_version": 1,
    "runner_version": "skill-evaluation-runner-1",
}
REMOTE_SUBJECT_STORE_MAX_BYTES = 1024 * 1024 * 1024
REMOTE_SUBJECT_FREE_SPACE_RESERVE = 256 * 1024 * 1024
REMOTE_SUBJECT_MAX_FILES = 512
REMOTE_SUBJECT_MAX_FILE_BYTES = 8 * 1024 * 1024
REMOTE_SUBJECT_MAX_DECODED_BYTES = 32 * 1024 * 1024
REMOTE_SUBJECT_MAX_ENCODED_BYTES = 48 * 1024 * 1024
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


def publish_directory_create_only(source: Path, destination: Path) -> None:
    if sys.platform != "darwin":
        raise RuntimeFailure(
            "remote-candidate-publication-refused",
            "atomic create-only directory publication requires macOS",
        )
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameatx_np
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    if (
        rename(
            -2,
            os.fsencode(source),
            -2,
            os.fsencode(destination),
            0x00000004,
        )
        != 0
    ):
        failure = ctypes.get_errno()
        if failure in {errno.EEXIST, errno.ENOTEMPTY}:
            raise RuntimeFailure(
                "remote-candidate-publication-collision",
                str(destination),
            )
        raise RuntimeFailure(
            "remote-candidate-publication-refused",
            f"{destination}: {os.strerror(failure)}",
        )


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
        self.capabilities = frozenset(self.identity["capabilities"])

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

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


@dataclass(frozen=True)
class TaskProfileReceipt:
    path: Path
    payload: dict[str, Any]


@dataclass(frozen=True)
class TaskProfileBinding:
    status: str
    receipt: TaskProfileReceipt | None = None
    context: dict[str, Any] | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ProfileAuditTarget:
    receipt: TaskProfileReceipt
    profile: dict[str, Any]


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
    def task_profile_receipts(self) -> Path:
        return self.data / "task-profiles" / "v1"

    @property
    def task_profile_index(self) -> Path:
        return self.state / "task-profile-index.json"

    @property
    def profile_audit_dispositions(self) -> Path:
        return self.state / "profile-audit-dispositions" / "v3"

    @property
    def prior_profile_audit_dispositions(self) -> Path:
        return self.state / "profile-audit-dispositions" / "v2"

    @property
    def legacy_profile_audit_dispositions(self) -> Path:
        return self.state / "profile-audit-dispositions" / "v1"

    @property
    def task_occurrence_resolutions(self) -> Path:
        return self.data / "task-occurrences" / "v2"

    @property
    def task_occurrence_index(self) -> Path:
        return self.state / "task-occurrence-index.json"

    @property
    def task_occurrence_contexts(self) -> Path:
        return self.state / "task-occurrence-contexts"

    @property
    def task_occurrence_corrections(self) -> Path:
        return self.data / "task-occurrence-corrections" / "v1"

    @property
    def task_pass_accounting_receipts(self) -> Path:
        return self.data / "task-opportunity-accounting" / "v1"

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

    @staticmethod
    def _profile_skill_load_trace(
        snapshot: dict[str, Any],
        profile: dict[str, Any],
        catalog_names: list[str],
    ) -> list[dict[str, Any]]:
        events = snapshot.get("events")
        source_event_ids = profile.get("source_event_ids")
        if not isinstance(events, list) or not isinstance(
            source_event_ids, list
        ) or not source_event_ids:
            raise RuntimeFailure(
                "malformed-executor-result", "catalog_audit source evidence"
            )
        positions = {
            event.get("source_event_id"): index
            for index, event in enumerate(events)
            if isinstance(event, dict)
            and isinstance(event.get("source_event_id"), str)
        }
        if any(event_id not in positions for event_id in source_event_ids):
            raise RuntimeFailure(
                "malformed-executor-result", "catalog_audit source evidence"
            )
        trace: list[dict[str, Any]] = []
        first = positions[source_event_ids[0]]
        last = positions[source_event_ids[-1]]
        for event in events[first : last + 1]:
            if (
                not isinstance(event, dict)
                or event.get("kind") != "tool_call"
                or str(event.get("tool_name", "")).casefold() != "skill"
            ):
                continue
            raw_input = event.get("text")
            try:
                parsed_input = (
                    json.loads(raw_input)
                    if isinstance(raw_input, str)
                    else raw_input
                )
            except json.JSONDecodeError as error:
                raise RuntimeFailure(
                    "malformed-executor-result",
                    "catalog_audit skill invocation",
                ) from error
            invoked_name = None
            if isinstance(parsed_input, dict):
                invoked_name = parsed_input.get(
                    "skill",
                    parsed_input.get(
                        "skillName", parsed_input.get("name")
                    ),
                )
            if not isinstance(invoked_name, str) or not invoked_name.strip():
                raise RuntimeFailure(
                    "malformed-executor-result",
                    "catalog_audit skill invocation",
                )
            invoked_name = invoked_name.strip().lstrip("/")
            projected_name = invoked_name
            if (
                projected_name not in catalog_names
                and ":" in projected_name
            ):
                suffix = projected_name.rsplit(":", 1)[1]
                projected_name = (
                    suffix if suffix in catalog_names else projected_name
                )
            trace.append(
                {
                    "source_event_id": event["source_event_id"],
                    "invoked_name": invoked_name,
                    "catalog_skill_name": (
                        projected_name
                        if projected_name in catalog_names
                        else None
                    ),
                    "event_sha256": digest(event),
                }
            )
        return trace

    def _validated_review_result(
        self,
        result: dict[str, Any],
        *,
        require_catalog_audit: bool = False,
        catalog_snapshot: dict[str, Any] | None = None,
        catalog_profile: dict[str, Any] | None = None,
        candidate_groups: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
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
        catalog_audit = result.get("catalog_audit")
        if require_catalog_audit:
            if not isinstance(catalog_audit, dict):
                raise RuntimeFailure(
                    "malformed-executor-result", "catalog_audit is required"
                )
            expected_audit_keys = {
                "outcome",
                "skill_name",
                "reviewer_contract",
                "catalog_sha256",
                "catalog_skill_names",
                "tombstones_sha256",
                "skill_load_trace",
                "skill_load_trace_sha256",
                "candidate_group_id",
                "candidate_groups",
            }
            trace = catalog_audit.get("skill_load_trace")
            catalog_names = catalog_audit.get("catalog_skill_names")
            if (
                set(catalog_audit) != expected_audit_keys
                or catalog_audit.get("outcome")
                not in {
                    "correct-skill",
                    "missed-skill",
                    "wrong-or-incomplete-skill",
                    "no-covering-skill",
                }
                or catalog_audit.get("reviewer_contract")
                != "profile-catalog-audit-v1"
                or not isinstance(trace, list)
                or digest(trace) != catalog_audit.get("skill_load_trace_sha256")
                or any(
                    not isinstance(item, dict)
                    or set(item)
                    != {
                        "source_event_id",
                        "invoked_name",
                        "catalog_skill_name",
                        "event_sha256",
                    }
                    or not isinstance(item.get("source_event_id"), str)
                    or not item["source_event_id"]
                    or not isinstance(item.get("invoked_name"), str)
                    or not item["invoked_name"]
                    or (
                        item.get("catalog_skill_name") is not None
                        and (
                            not isinstance(item["catalog_skill_name"], str)
                            or not item["catalog_skill_name"]
                        )
                    )
                    or not isinstance(item.get("event_sha256"), str)
                    or not item["event_sha256"].startswith("sha256:")
                    for item in trace
                )
                or not isinstance(catalog_names, list)
                or catalog_names != sorted(set(catalog_names))
                or any(
                    not isinstance(name, str) or not name for name in catalog_names
                )
                or not isinstance(catalog_snapshot, dict)
                or not isinstance(catalog_profile, dict)
                or catalog_audit.get("candidate_groups") != candidate_groups
                or trace
                != self._profile_skill_load_trace(
                    catalog_snapshot,
                    catalog_profile,
                    catalog_names,
                )
                or any(
                    not isinstance(catalog_audit.get(field), str)
                    or not catalog_audit[field].startswith("sha256:")
                    for field in ("catalog_sha256", "tombstones_sha256")
                )
            ):
                raise RuntimeFailure(
                    "malformed-executor-result", "catalog_audit is invalid"
                )
            candidate_group_id = catalog_audit.get("candidate_group_id")
            boundary_conflict = (
                isinstance(result.get("occurrence_boundary"), dict)
                and result["occurrence_boundary"].get("relation")
                == "boundary-conflict"
            )
            if (
                (catalog_audit["outcome"] != "no-covering-skill" or boundary_conflict)
                and candidate_group_id is not None
            ) or (
                catalog_audit["outcome"] == "no-covering-skill"
                and not boundary_conflict
                and candidate_group_id is not None
                and candidate_group_id
                not in {
                    item.get("lifecycle_id")
                    for item in candidate_groups or []
                    if isinstance(item, dict)
                }
            ):
                raise RuntimeFailure(
                    "malformed-executor-result", "catalog_audit candidate group is invalid"
                )
        elif catalog_audit is not None:
            raise RuntimeFailure(
                "malformed-executor-result",
                "catalog_audit is only valid for profile review",
            )
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
            if require_catalog_audit:
                skill_name = catalog_audit["skill_name"]
                loaded_names = {
                    item.get("catalog_skill_name")
                    for item in catalog_audit["skill_load_trace"]
                    if isinstance(item, dict)
                }
                outcome = catalog_audit["outcome"]
                occurrence_boundary = result.get("occurrence_boundary")
                boundary_conflict = (
                    isinstance(occurrence_boundary, dict)
                    and occurrence_boundary.get("relation")
                    == "boundary-conflict"
                )
                valid_semantic_outcome = (
                    outcome == "correct-skill"
                    and isinstance(skill_name, str)
                    and skill_name in catalog_audit["catalog_skill_names"]
                    and skill_name in loaded_names
                ) or (
                    outcome == "missed-skill"
                    and isinstance(skill_name, str)
                    and skill_name in catalog_audit["catalog_skill_names"]
                    and skill_name not in loaded_names
                ) or (
                    outcome == "wrong-or-incomplete-skill"
                    and isinstance(skill_name, str)
                    and skill_name in catalog_audit["catalog_skill_names"]
                    and skill_name in loaded_names
                ) or (
                    outcome == "no-covering-skill"
                    and skill_name is None
                )
                if not valid_semantic_outcome or (
                    not boundary_conflict and outcome != "correct-skill"
                ) or destination != "discard":
                    raise RuntimeFailure(
                        "malformed-executor-result",
                        "catalog_audit route is invalid",
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
        if require_catalog_audit:
            outcome = catalog_audit["outcome"]
            skill_name = catalog_audit["skill_name"]
            loaded_trace = catalog_audit["skill_load_trace"]
            catalog_names = catalog_audit["catalog_skill_names"]
            valid_catalog_route = (
                outcome == "missed-skill"
                and isinstance(skill_name, str)
                and skill_name in catalog_names
                and skill_name
                not in {
                    item.get("catalog_skill_name")
                    for item in loaded_trace
                    if isinstance(item, dict)
                }
                and destination == "skill"
                and operation == "patch"
                and artifact["skill_name"] == skill_name
            ) or (
                outcome == "wrong-or-incomplete-skill"
                and isinstance(skill_name, str)
                and skill_name in catalog_names
                and skill_name
                in {
                    item.get("catalog_skill_name")
                    for item in loaded_trace
                    if isinstance(item, dict)
                }
                and destination in {"skill", "support_file"}
                and operation in {"patch", "support_file"}
                and artifact["skill_name"] == skill_name
            ) or (
                outcome == "no-covering-skill"
                and skill_name is None
                and destination == "skill"
                and operation == "create"
                and artifact["skill_name"] not in catalog_names
            )
            selected_group = next(
                (
                    group
                    for group in candidate_groups or []
                    if isinstance(group, dict)
                    and group.get("lifecycle_id")
                    == catalog_audit["candidate_group_id"]
                ),
                None,
            )
            if (
                catalog_audit["candidate_group_id"] is not None
                and (
                    not isinstance(selected_group, dict)
                    or artifact["skill_name"]
                    != selected_group.get("proposed_name")
                )
            ):
                raise RuntimeFailure(
                    "malformed-executor-result",
                    "catalog_audit candidate group artifact is invalid",
                )
            if not valid_catalog_route:
                raise RuntimeFailure(
                    "malformed-executor-result",
                    "catalog_audit route is invalid",
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

    def _validated_task_profile_receipt(
        self,
        result: dict[str, Any],
        snapshot_path: Path,
        reviewed_identity: dict[str, Any],
        executor_id: str,
        executor_identity: dict[str, Any],
    ) -> dict[str, Any]:
        expected_result_keys = {
            "status",
            "mutation_started",
            "completion_sentinel",
            "schema_version",
            "kind",
            "snapshot_sha256",
            "qualified_session_id",
            "profile_set_id",
            "profiles",
            "model",
        }
        if (
            set(result) != expected_result_keys
            or result.get("status") != "ok"
            or result.get("mutation_started") is not False
            or result.get("completion_sentinel")
            != "DREAMING_TASK_PROFILE_COMPLETE"
            or result.get("schema_version") != 1
            or result.get("kind") != "llm_task_opportunity_profile"
            or result.get("qualified_session_id")
            != reviewed_identity["qualified_session_id"]
            or not isinstance(result.get("model"), str)
            or not result["model"]
        ):
            raise RuntimeFailure(
                "task-profile-invalid", "executor result contract"
            )
        snapshot = read_json(snapshot_path, {})
        snapshot_sha256 = digest(snapshot)
        if (
            result.get("snapshot_sha256") != snapshot_sha256
            or snapshot_path.stem != snapshot_sha256.removeprefix("sha256:")
        ):
            raise RuntimeFailure(
                "task-profile-invalid", "snapshot identity"
            )
        model_profiles = result.get("profiles")
        observed = parse_time(reviewed_identity.get("updated_at"))
        if observed is None:
            raise RuntimeFailure("task-profile-invalid", "source observation time")
        if not isinstance(model_profiles, list):
            raise RuntimeFailure("task-profile-invalid", "profiles")
        events = snapshot.get("events")
        if not isinstance(events, list):
            raise RuntimeFailure("task-profile-invalid", "snapshot events")
        event_by_id = {
            event.get("source_event_id"): event for event in events
            if isinstance(event, dict) and isinstance(event.get("source_event_id"), str)
        }
        profiles: list[dict[str, Any]] = []
        legacy_profiles = bool(model_profiles) and all(
            isinstance(profile, dict) and "goal_event_id" not in profile
            for profile in model_profiles
        )
        expected_model_keys = {
            "source_event_ids", "goal_event_id", "task_type", "abstract_summary",
            "reuse_value", "procedure", "confidence", "sensitive_source", "task_state",
            "task_key", "profile_id", "procedure_fingerprint",
        }
        for profile in model_profiles:
            expected = expected_model_keys - {"goal_event_id"} if legacy_profiles else expected_model_keys
            if not isinstance(profile, dict) or set(profile) != expected:
                raise RuntimeFailure("task-profile-invalid", "profile contract")
            event_ids = profile.get("source_event_ids")
            if legacy_profiles:
                semantic = {key: profile[key] for key in expected - {"task_key", "profile_id", "procedure_fingerprint"}}
                owned = {**semantic,
                    "task_key": digest({"qualified_session_id": reviewed_identity["qualified_session_id"], "source_event_ids": event_ids}),
                    "profile_id": digest({"qualified_session_id": reviewed_identity["qualified_session_id"], **semantic}),
                    "procedure_fingerprint": digest(semantic["procedure"]) if isinstance(semantic["procedure"], dict) else None}
                profiles.append(owned)
                continue
            goal_event_id = profile.get("goal_event_id")
            goal_event = event_by_id.get(goal_event_id)
            if (not isinstance(event_ids, list) or goal_event_id not in event_ids
                    or not isinstance(goal_event, dict) or goal_event.get("kind") != "user_message"):
                raise RuntimeFailure("task-profile-invalid", "goal event")
            goal_time = parse_time(goal_event.get("timestamp"))
            if goal_time is None or goal_time > datetime.fromtimestamp(self.now(), timezone.utc):
                raise RuntimeFailure("task-profile-invalid", "goal timestamp")
            # Never accept model-owned identities or occurrence times: retain only its semantic selection.
            semantic = {key: profile[key] for key in expected_model_keys - {"task_key", "profile_id", "procedure_fingerprint"}}
            owned = {
                **semantic,
                "occurred_at": goal_time.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "task_key": digest({"qualified_session_id": reviewed_identity["qualified_session_id"], "source_event_ids": event_ids}),
                "profile_id": digest({"qualified_session_id": reviewed_identity["qualified_session_id"], **semantic}),
                "procedure_fingerprint": digest(semantic["procedure"]) if isinstance(semantic["procedure"], dict) else None,
            }
            profiles.append(owned)
        receipt = {
            "schema_version": 1 if legacy_profiles else 2,
            "kind": "task_profile_receipt",
            "profile_set_id": digest({"snapshot_sha256": snapshot_sha256, "qualified_session_id": reviewed_identity["qualified_session_id"], "profiles": profiles}),
            "snapshot_sha256": snapshot_sha256,
            "source_revision": reviewed_identity["source_revision"],
            "qualified_session_id": reviewed_identity["qualified_session_id"],
            "observed_at": observed.astimezone(timezone.utc).isoformat(),
            "executor": executor_id,
            "executor_identity": executor_identity,
            "model": result["model"],
            "profiles": profiles,
        }
        complete_receipt = {
            **receipt,
            "receipt_sha256": digest(receipt),
        }
        try:
            validate_task_profile_receipt(
                complete_receipt,
                snapshot,
                receipt_path=Path(
                    complete_receipt["receipt_sha256"].removeprefix("sha256:")
                    + ".json"
                ),
                expected_executor=executor_id,
                expected_executor_identity=executor_identity,
            )
        except TaskProfileReceiptError as error:
            raise RuntimeFailure(
                "task-profile-invalid", error.reason
            ) from error
        return complete_receipt

    def profile(
        self,
        source_name: str,
        source: ExecutableAdapter,
        qualified_session_id: str,
        executor_id: str,
        executor: ExecutableAdapter,
        expected_revision: str | None = None,
        on_profile_operation_start: Callable[[], None] | None = None,
        correction_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._route_allowed(source_name, executor_id):
            raise RuntimeFailure("route-denied", f"{source_name}>{executor_id}")
        if not executor.supports(TASK_PROFILE_CAPABILITY):
            raise RuntimeFailure(
                "task-profile-unsupported", executor_id
            )
        doctor = executor.call("doctor")
        if not doctor.get("healthy") or not doctor.get("boundary_ready"):
            raise RuntimeFailure("executor-boundary-unavailable", executor_id)
        try:
            current = source.call(
                "inspect", session=qualified_session_id
            )["session"]
        except RuntimeFailure as error:
            if (
                expected_revision is None
                or error.code != "session-missing"
                or error.message != qualified_session_id
            ):
                raise
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
            self._admit(current)
            return {
                "status": "stale-before-profile",
                "queued_revision": current["source_revision"],
            }
        if current["completion_state"] not in {"terminal", "quiet"}:
            raise RuntimeFailure(
                "task-profile-unsettled", qualified_session_id
            )
        snapshot_path, reviewed_identity = self.render_snapshot(
            source_name,
            source,
            executor_id,
            qualified_session_id,
        )
        result_name = hashlib.sha256(
            qualified_session_id.encode("utf-8")
        ).hexdigest()
        executor_name = hashlib.sha256(executor_id.encode("utf-8")).hexdigest()
        result_path = (
            self.paths.state
            / "profile-results"
            / (
                f"{result_name}-"
                f"{hashlib.sha256(current['source_revision'].encode()).hexdigest()}-"
                f"{executor_name}"
                f"{'-correction' if correction_context is not None else ''}.json"
            )
        )
        result_path.parent.mkdir(parents=True, exist_ok=True)
        profile_key = self._task_profile_key(
            reviewed_identity["qualified_session_id"],
            reviewed_identity["source_revision"],
            executor_id,
        )
        prior_index = self._state(self.paths.task_profile_index, {})
        if not isinstance(prior_index, dict):
            raise RuntimeFailure(
                "task-profile-index-invalid",
                str(self.paths.task_profile_index),
            )
        prior_entry = prior_index.get(profile_key)
        if on_profile_operation_start is not None:
            on_profile_operation_start()
        run_arguments: dict[str, Any] = {
            "snapshot": snapshot_path,
            "result": result_path,
            "mode": "profile",
        }
        if correction_context is not None:
            correction_path = (
                self.paths.task_occurrence_contexts
                / (
                    hashlib.sha256(
                        canonical(correction_context)
                    ).hexdigest()
                    + "-correction.json"
                )
            )
            atomic_json(correction_path, correction_context)
            run_arguments["task_profile_correction"] = correction_path
        response = executor.call("run", **run_arguments)
        if not result_path.exists():
            raise RuntimeFailure("missing-task-profile-result", executor_id)
        file_result = read_json(result_path, {})
        if file_result != {
            key: value for key, value in response.items() if key != "ok"
        }:
            raise RuntimeFailure("result-channel-mismatch", executor_id)
        receipt = self._validated_task_profile_receipt(
            file_result,
            snapshot_path,
            reviewed_identity,
            executor_id,
            executor.identity,
        )
        try:
            latest = source.call(
                "inspect", session=qualified_session_id
            )["session"]
        except RuntimeFailure as error:
            if (
                expected_revision is None
                or error.code != "session-missing"
                or error.message != qualified_session_id
            ):
                raise
            self._mark_queue(
                qualified_session_id, expected_revision, "deleted"
            )
            return {"status": "deleted"}
        validate_identity(latest, source_name)
        if not same_revision(reviewed_identity, latest):
            raise RuntimeFailure(
                "task-profile-stale", qualified_session_id
            )
        receipt_path = (
            self.paths.task_profile_receipts
            / f"{receipt['receipt_sha256'].removeprefix('sha256:')}.json"
        )
        if receipt_path.exists():
            if read_json(receipt_path, {}) != receipt:
                raise RuntimeFailure(
                    "task-profile-collision", str(receipt_path)
                )
        else:
            atomic_json(receipt_path, receipt)
        entry = {
            "qualified_session_id": reviewed_identity[
                "qualified_session_id"
            ],
            "source_revision": reviewed_identity["source_revision"],
            "executor": executor_id,
            "receipt_sha256": receipt["receipt_sha256"],
            "profile_set_id": receipt["profile_set_id"],
        }
        index_lock = self.paths.state / "task-profile-index.lock"
        index_lock.parent.mkdir(parents=True, exist_ok=True)
        with index_lock.open("a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            index = self._state(self.paths.task_profile_index, {})
            if not isinstance(index, dict):
                raise RuntimeFailure(
                    "task-profile-index-invalid",
                    str(self.paths.task_profile_index),
                )
            if index.get(profile_key) != prior_entry:
                raise RuntimeFailure(
                    "task-profile-concurrent-refresh", profile_key
                )
            index[profile_key] = entry
            self._write(self.paths.task_profile_index, index)
        return {
            "status": "profiled",
            "receipt": str(receipt_path),
            "receipt_sha256": receipt["receipt_sha256"],
            "profile_set_id": receipt["profile_set_id"],
            "profile_count": len(receipt["profiles"]),
        }

    def indexed_task_profile_receipt_for(
        self,
        qualified_session_id: str,
        source_revision: str,
        executor_id: str,
        *,
        current_contract: bool = True,
        receipt_sha256: str | None = None,
    ) -> TaskProfileReceipt | None:
        index = self._state(self.paths.task_profile_index, {})
        if not isinstance(index, dict):
            raise RuntimeFailure(
                "task-profile-index-invalid",
                str(self.paths.task_profile_index),
            )
        profile_key = self._task_profile_key(
            qualified_session_id,
            source_revision,
            executor_id,
        )
        entry = index.get(profile_key) if current_contract else None
        if not current_contract:
            if not isinstance(receipt_sha256, str):
                raise RuntimeFailure(
                    "task-profile-index-invalid",
                    "historical receipt lookup requires receipt_sha256",
                )
            matches = [
                (key, value)
                for key, value in index.items()
                if isinstance(value, dict)
                and value.get("qualified_session_id")
                == qualified_session_id
                and value.get("source_revision") == source_revision
                and value.get("executor") == executor_id
                and value.get("receipt_sha256") == receipt_sha256
            ]
            if len(matches) > 1:
                raise RuntimeFailure(
                    "task-profile-index-invalid",
                    f"multiple managed receipts for {qualified_session_id}",
                )
            if matches:
                profile_key, entry = matches[0]
        if entry is None:
            return None
        entry_keys = {
            "qualified_session_id",
            "source_revision",
            "executor",
            "receipt_sha256",
            "profile_set_id",
        }
        if (
            not isinstance(entry, dict)
            or frozenset(entry)
            not in {
                frozenset(entry_keys),
                frozenset(entry_keys | {"receipt"}),
            }
            or entry.get("qualified_session_id") != qualified_session_id
            or entry.get("source_revision") != source_revision
            or entry.get("executor") != executor_id
            or not isinstance(entry.get("receipt_sha256"), str)
        ):
            raise RuntimeFailure(
                "task-profile-index-invalid", profile_key
            )
        receipt_path = (
            self.paths.task_profile_receipts
            / f"{entry['receipt_sha256'].removeprefix('sha256:')}.json"
        )
        legacy_receipt = entry.get("receipt")
        if (
            legacy_receipt is not None
            and (
                not isinstance(legacy_receipt, str)
                or Path(legacy_receipt).name != receipt_path.name
            )
        ):
            raise RuntimeFailure(
                "task-profile-index-invalid", profile_key
            )
        receipt = read_json(receipt_path, {})
        if not isinstance(receipt, dict):
            raise RuntimeFailure(
                "task-profile-index-invalid", profile_key
            )
        receipt_sha256 = receipt.get("receipt_sha256")
        receipt_body = {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
        valid = (
            receipt_sha256 == entry.get("receipt_sha256")
            and digest(receipt_body) == receipt_sha256
            and receipt.get("profile_set_id") == entry.get("profile_set_id")
            and receipt.get("qualified_session_id") == qualified_session_id
            and receipt.get("source_revision") == source_revision
            and receipt.get("executor") == executor_id
            and receipt.get("profile_set_id")
            == digest(
                {
                    "snapshot_sha256": receipt.get("snapshot_sha256"),
                    "qualified_session_id": receipt.get(
                        "qualified_session_id"
                    ),
                    "profiles": receipt.get("profiles"),
                }
            )
            and receipt_path.stem
            == receipt_sha256.removeprefix("sha256:")
        )
        if not valid:
            raise RuntimeFailure(
                "task-profile-index-invalid", profile_key
            )
        return TaskProfileReceipt(receipt_path, receipt)

    def _task_profile_key(
        self,
        qualified_session_id: str,
        source_revision: str,
        executor_id: str,
    ) -> str:
        return digest(
            {
                "qualified_session_id": qualified_session_id,
                "source_revision": source_revision,
                "executor": executor_id,
                "policy_version": self.policy_version,
                "snapshot_contract_version": (
                    TASK_PROFILE_SNAPSHOT_CONTRACT_VERSION
                ),
            }
        )

    def task_profile_binding_for(
        self,
        qualified_session_id: str,
        source_revision: str,
        executor_id: str,
        snapshot_path: Path,
        executor_identity: dict[str, Any],
    ) -> TaskProfileBinding:
        indexed = self.indexed_task_profile_receipt_for(
            qualified_session_id,
            source_revision,
            executor_id,
        )
        if indexed is None:
            return TaskProfileBinding(status="absent")
        # V1 receipts lack the goal-derived occurrence authority and catalog
        # audit contract required by the current profile-review lifecycle.
        if indexed.payload.get("schema_version") != 2:
            return TaskProfileBinding(
                status="unbound",
                receipt=indexed,
                reason="schema-version",
            )
        snapshot = read_json(snapshot_path, {})
        receipt_identity = indexed.payload.get("executor_identity")
        if not compatible_task_profile_executor_identities(
            receipt_identity, executor_identity
        ):
            return TaskProfileBinding(
                status="unbound",
                receipt=indexed,
                reason="executor-identity",
            )
        try:
            context = validate_task_profile_receipt(
                indexed.payload,
                snapshot,
                receipt_path=indexed.path,
                expected_executor=executor_id,
                expected_executor_identity=receipt_identity,
            )
        except TaskProfileReceiptError as error:
            if error.reason == "snapshot-sha256":
                return TaskProfileBinding(
                    status="unbound",
                    receipt=indexed,
                    reason=error.reason,
                )
            raise RuntimeFailure(
                "task-profile-index-invalid",
                f"{indexed.path}: {error.reason}",
            ) from error
        return TaskProfileBinding(
            status="bound",
            receipt=indexed,
            context=context,
        )

    def profile_audit_targets_for(
        self,
        source_name: str,
        source: ExecutableAdapter,
        qualified_session_id: str,
        source_revision: str,
        executor_id: str,
        executor: ExecutableAdapter,
    ) -> list[ProfileAuditTarget]:
        """Resolve every reusable profile without spending a model call."""
        if not executor.supports(TASK_PROFILE_CAPABILITY):
            raise RuntimeFailure("profile-audit-executor-unsupported", executor_id)
        current = source.call("inspect", session=qualified_session_id)["session"]
        validate_identity(current, source_name)
        if current["source_revision"] != source_revision:
            self._mark_queue(
                qualified_session_id, source_revision, "superseded"
            )
            self._admit(current)
            raise RuntimeFailure("profile-audit-stale", qualified_session_id)
        snapshot_path, _identity = self.render_snapshot(
            source_name, source, executor_id, qualified_session_id
        )
        binding = self.task_profile_binding_for(
            qualified_session_id,
            source_revision,
            executor_id,
            snapshot_path,
            executor.identity,
        )
        if binding.status == "absent":
            raise RuntimeFailure("profile-audit-receipt-unavailable", qualified_session_id)
        if binding.status != "bound" or binding.receipt is None or binding.context is None:
            raise RuntimeFailure(
                "profile-audit-receipt-unbound",
                binding.reason or binding.status,
            )
        profiles = binding.context.get("profiles")
        if not isinstance(profiles, list):
            raise RuntimeFailure("profile-audit-receipt-invalid", "profiles")
        # A receipt can produce multiple review units.  Callers intentionally
        # enumerate this list rather than treating the receipt as one unit.
        return [
            ProfileAuditTarget(binding.receipt, profile)
            for profile in profiles
            if isinstance(profile, dict)
        ]

    def _profile_audit_disposition_path(
        self, profile_id: str, *, contract_version: int = 3
    ) -> Path:
        if not isinstance(profile_id, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", profile_id
        ):
            raise RuntimeFailure("profile-audit-disposition-invalid", "profile-id")
        root = {
            3: self.paths.profile_audit_dispositions,
            2: self.paths.prior_profile_audit_dispositions,
            1: self.paths.legacy_profile_audit_dispositions,
        }.get(contract_version)
        if root is None:
            raise RuntimeFailure(
                "profile-audit-disposition-invalid", "contract-version"
            )
        return (
            root
            / f"{profile_id.removeprefix('sha256:')}.json"
        )

    def _validate_task_occurrence_provenance(
        self, resolution: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            resolution = validate_task_occurrence_resolution(resolution)
            receipt_path = (
                self.paths.task_profile_receipts
                / (
                    resolution["profile_receipt_sha256"]
                    .removeprefix("sha256:")
                    + ".json"
                )
            )
            snapshot_path = (
                self.paths.snapshots
                / (
                    resolution["snapshot_sha256"].removeprefix("sha256:")
                    + ".json"
                )
            )
            receipt = read_json(receipt_path, {})
            snapshot = read_json(snapshot_path, {})
            context = validate_task_profile_receipt(
                receipt,
                snapshot,
                receipt_path=receipt_path,
                expected_executor=receipt.get("executor"),
                expected_executor_identity=receipt.get("executor_identity"),
            )
        except (TaskOccurrenceError, TaskProfileReceiptError) as error:
            reason = getattr(error, "reason", str(error))
            raise RuntimeFailure(
                "task-occurrence-resolution-invalid", reason
            ) from error
        profile = next(
            (
                item
                for item in context["profiles"]
                if item.get("profile_id") == resolution["profile_id"]
            ),
            None,
        )
        expected = {
            "profile_sha256": digest(profile) if profile is not None else None,
            "task_key": profile.get("task_key") if profile is not None else None,
            "source_event_ids": (
                profile.get("source_event_ids") if profile is not None else None
            ),
            "goal_event_id": (
                profile.get("goal_event_id") if profile is not None else None
            ),
            "occurred_at": (
                profile.get("occurred_at") if profile is not None else None
            ),
            "snapshot_sha256": receipt.get("snapshot_sha256"),
            "source_revision": receipt.get("source_revision"),
            "qualified_session_id": receipt.get("qualified_session_id"),
        }
        if profile is None or any(
            resolution.get(key) != value for key, value in expected.items()
        ):
            raise RuntimeFailure(
                "task-occurrence-resolution-invalid", "origin-provenance"
            )
        return resolution

    def task_occurrence_resolution_for(
        self, target: ProfileAuditTarget
    ) -> dict[str, Any] | None:
        try:
            bound = [
                self._validate_task_occurrence_provenance(resolution)
                for resolution in load_task_occurrence_resolutions(
                    self.paths.task_occurrence_resolutions
                )
                if (
                    resolution.get("profile_id")
                    == target.profile["profile_id"]
                    and resolution.get("profile_receipt_sha256")
                    == target.receipt.payload["receipt_sha256"]
                    and resolution.get("task_key")
                    == target.profile["task_key"]
                )
            ]
        except TaskOccurrenceError as error:
            raise RuntimeFailure(
                "task-occurrence-resolution-invalid", error.reason
            ) from error
        resolution_ids = {
            resolution["resolution_sha256"] for resolution in bound
        }
        superseded_ids = {
            resolution["supersedes_resolution_sha256"]
            for resolution in bound
            if resolution["supersedes_resolution_sha256"] is not None
        }
        if not superseded_ids.issubset(resolution_ids):
            raise RuntimeFailure(
                "task-occurrence-resolution-invalid",
                "supersession-target",
            )
        bound = [
            resolution
            for resolution in bound
            if resolution["resolution_sha256"] not in superseded_ids
        ]
        if len(bound) > 1:
            raise RuntimeFailure(
                "task-occurrence-resolution-invalid",
                "duplicate-profile-resolution",
            )
        if bound:
            return bound[0]
        try:
            resolution = load_exact_task_occurrence(
                self.paths.task_occurrence_resolutions,
                self.paths.task_occurrence_index,
                target.profile["task_key"],
            )
        except TaskOccurrenceError as error:
            raise RuntimeFailure(
                "task-occurrence-index-invalid", error.reason
            ) from error
        if resolution is None:
            return None
        resolution = self._validate_task_occurrence_provenance(resolution)
        if (
            resolution["profile_id"] != target.profile["profile_id"]
            or resolution["profile_receipt_sha256"]
            != target.receipt.payload["receipt_sha256"]
        ):
            if resolution["boundary_relation"] == "boundary-conflict":
                correction = self.task_occurrence_correction_attempt_for(
                    resolution
                )
                if (
                    correction is not None
                    and correction["terminal_status"] == "replacement-profiled"
                    and correction["replacement_profile_receipt_sha256"]
                    == target.receipt.payload["receipt_sha256"]
                ):
                    return None
            raise RuntimeFailure(
                "task-occurrence-task-key-reused",
                target.profile["task_key"],
            )
        return resolution

    def _reuse_exact_task_occurrence(
        self,
        target: ProfileAuditTarget,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        try:
            prior = load_exact_task_occurrence(
                self.paths.task_occurrence_resolutions,
                self.paths.task_occurrence_index,
                target.profile["task_key"],
            )
        except TaskOccurrenceError as error:
            raise RuntimeFailure(
                "task-occurrence-index-invalid", error.reason
            ) from error
        if prior is None:
            return None
        prior = self._validate_task_occurrence_provenance(prior)
        if prior["boundary_relation"] not in {
            "same-occurrence",
            "new-occurrence",
        }:
            return None
        if (
            prior["profile_id"] == target.profile["profile_id"]
            and prior["profile_receipt_sha256"]
            == target.receipt.payload["receipt_sha256"]
        ):
            return None
        origin_receipt_path = (
            self.paths.task_profile_receipts
            / (
                prior["profile_receipt_sha256"].removeprefix("sha256:")
                + ".json"
            )
        )
        origin_receipt = read_json(origin_receipt_path, {})
        origin_snapshot_path = (
            self.paths.snapshots
            / (
                origin_receipt.get("snapshot_sha256", "")
                .removeprefix("sha256:")
                + ".json"
            )
        )
        try:
            origin_context = validate_task_profile_receipt(
                origin_receipt,
                read_json(origin_snapshot_path, {}),
                receipt_path=origin_receipt_path,
                expected_executor=origin_receipt.get("executor"),
                expected_executor_identity=origin_receipt.get(
                    "executor_identity"
                ),
            )
        except TaskProfileReceiptError as error:
            raise RuntimeFailure(
                "task-occurrence-resolution-invalid",
                f"reuse-origin-{error.reason}",
            ) from error
        origin_profile = next(
            (
                profile
                for profile in origin_context["profiles"]
                if profile["profile_id"] == prior["profile_id"]
            ),
            None,
        )
        if origin_profile is None:
            raise RuntimeFailure(
                "task-occurrence-resolution-invalid",
                "reuse-origin-profile",
            )
        prior_target = ProfileAuditTarget(
            TaskProfileReceipt(origin_receipt_path, origin_receipt),
            origin_profile,
        )
        prior_disposition = self.profile_audit_disposition_for(prior_target)
        if (
            prior_disposition is None
            or prior_disposition["profile_audit_contract_version"]
            != CURRENT_PROFILE_AUDIT_CONTRACT_VERSION
            or prior_disposition["boundary_relation"]
            not in {"same-occurrence", "new-occurrence"}
        ):
            raise RuntimeFailure(
                "task-occurrence-resolution-invalid",
                "reuse-origin-disposition",
            )
        target_snapshot_path = (
            self.paths.snapshots
            / (
                target.receipt.payload["snapshot_sha256"].removeprefix(
                    "sha256:"
                )
                + ".json"
            )
        )
        target_snapshot = read_json(target_snapshot_path, {})
        if (
            digest(target_snapshot)
            != target.receipt.payload["snapshot_sha256"]
            or self._profile_skill_load_trace(
                target_snapshot,
                target.profile,
                prior_disposition["catalog_skill_names"],
            )
            != prior_disposition["skill_load_trace"]
        ):
            raise RuntimeFailure(
                "task-occurrence-resolution-invalid",
                "reuse-catalog-load-trace",
            )
        decision_at = (
            datetime.fromtimestamp(self.now(), timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        try:
            alias = build_task_occurrence_resolution(
                profile=target.profile,
                receipt=target.receipt.payload,
                relation="same-occurrence",
                review_contract="exact-task-key-reuse-v1",
                review_executor=prior["review_executor"],
                review_executor_identity=prior[
                    "review_executor_identity"
                ],
                decision_at=decision_at,
                prior_occurrence_ids=[prior["canonical_occurrence_id"]],
                overlap_resolution_ids=[prior["resolution_sha256"]],
            )
            persist_task_occurrence_resolution(
                self.paths.task_occurrence_resolutions,
                self.paths.task_occurrence_index,
                alias,
                project=False,
            )
        except TaskOccurrenceError as error:
            raise RuntimeFailure(
                "task-occurrence-resolution-invalid", error.reason
            ) from error
        copied_result = {
            "terminal_route": prior_disposition["terminal_route"],
            "summary": prior_disposition["summary"],
            "routing_reason": (
                "Reused the exact task occurrence's terminal catalog audit: "
                + prior_disposition["routing_reason"]
            ),
            "catalog_audit": {
                "outcome": prior_disposition["outcome"],
                "skill_name": prior_disposition["catalog_skill_name"],
                "reviewer_contract": prior_disposition[
                    "reviewer_contract"
                ],
                "catalog_sha256": prior_disposition["catalog_sha256"],
                "catalog_skill_names": prior_disposition[
                    "catalog_skill_names"
                ],
                "tombstones_sha256": prior_disposition[
                    "tombstones_sha256"
                ],
                "skill_load_trace": prior_disposition[
                    "skill_load_trace"
                ],
                "skill_load_trace_sha256": prior_disposition[
                    "skill_load_trace_sha256"
                ],
                "candidate_group_id": prior_disposition[
                    "candidate_group_id"
                ],
            },
        }
        disposition = self._record_profile_audit_disposition(
            target,
            prior_disposition["review_executor"],
            prior_disposition["review_executor_identity"],
            copied_result,
            alias,
        )
        return alias, disposition

    def _task_occurrence_context_for(
        self, target: ProfileAuditTarget
    ) -> dict[str, Any]:
        current_events = set(target.profile["source_event_ids"])
        try:
            resolutions = load_task_occurrence_resolutions(
                self.paths.task_occurrence_resolutions
            )
        except TaskOccurrenceError as error:
            raise RuntimeFailure(
                "task-occurrence-resolution-invalid", error.reason
            ) from error
        overlaps = []
        for resolution in resolutions:
            resolution = self._validate_task_occurrence_provenance(resolution)
            if (
                resolution["task_key"] == target.profile["task_key"]
                or resolution["qualified_session_id"]
                != target.receipt.payload["qualified_session_id"]
                or resolution["boundary_relation"]
                not in {"same-occurrence", "new-occurrence"}
                or not current_events.intersection(resolution["source_event_ids"])
            ):
                continue
            overlaps.append(
                {
                    "resolution_sha256": resolution["resolution_sha256"],
                    "profile_id": resolution["profile_id"],
                    "task_key": resolution["task_key"],
                    "source_event_ids": resolution["source_event_ids"],
                    "goal_event_id": resolution["goal_event_id"],
                    "occurred_at": resolution["occurred_at"],
                    "boundary_relation": resolution["boundary_relation"],
                    "canonical_occurrence_id": resolution[
                        "canonical_occurrence_id"
                    ],
                }
            )
        if len(overlaps) > 20:
            raise RuntimeFailure(
                "task-occurrence-overlap-limit", target.profile["profile_id"]
            )
        candidate_groups = self._candidate_group_context_for()
        return {
            "review_contract": TASK_OCCURRENCE_REVIEW_CONTRACT,
            "selected_profile_id": target.profile["profile_id"],
            "selected_task_key": target.profile["task_key"],
            "prior_overlaps": sorted(
                overlaps, key=lambda item: item["resolution_sha256"]
            ),
            "candidate_groups": candidate_groups,
        }

    def _candidate_group_context_for(self) -> list[dict[str, Any]]:
        """Return every eligible semantic group, or refuse an incomplete view."""
        listing = self._candidate_lifecycle_call("list")
        records = listing.get("records")
        if not isinstance(records, list):
            raise RuntimeFailure("candidate-lifecycle-failed", "candidate listing is malformed")
        groups: list[dict[str, Any]] = []
        expected_listing_keys = {
            "candidate_id",
            "lifecycle_id",
            "record_sha256",
            "record_version",
            "shadow_only",
            "state",
        }
        for item in records:
            if (
                not isinstance(item, dict)
                or set(item) != expected_listing_keys
                or not isinstance(item.get("lifecycle_id"), str)
                or not isinstance(item.get("candidate_id"), str)
                or not isinstance(item.get("record_sha256"), str)
                or not isinstance(item.get("record_version"), int)
                or item["record_version"] < 1
                or item.get("shadow_only") is not True
                or item.get("state") not in CANDIDATE_LIFECYCLE_STATES
            ):
                raise RuntimeFailure(
                    "candidate-lifecycle-failed", "candidate listing is malformed"
                )
            if item["state"] not in ELIGIBLE_CANDIDATE_GROUP_STATES:
                continue
            record = self._candidate_lifecycle_call("read", item["lifecycle_id"])
            record_sha256 = candidate_record_digest(record)
            if (
                record.get("lifecycle_id") != item["lifecycle_id"]
                or record.get("record_version") != item.get("record_version")
                or record_sha256 != item.get("record_sha256")
            ):
                raise RuntimeFailure(
                    "candidate-lifecycle-failed", "candidate listing changed"
                )
            useful_occurrences = {
                evidence["canonical_occurrence_id"]
                for evidence in record.get("evidence", [])
                if isinstance(evidence, dict)
                and isinstance(evidence.get("canonical_occurrence_id"), str)
                and (occurred_at := parse_time(evidence.get("occurred_at"))) is not None
                and occurred_at <= datetime.fromtimestamp(self.now(), timezone.utc)
                and datetime.fromtimestamp(self.now(), timezone.utc) - occurred_at
                <= timedelta(days=30)
            }
            groups.append(
                {
                    "lifecycle_id": record["lifecycle_id"],
                    "proposed_name": record["proposed_name"],
                    "procedure": record["procedure"],
                    "state": record["state"],
                    "record_version": record["record_version"],
                    "record_sha256": record_sha256,
                    "useful_current_count": len(useful_occurrences),
                }
            )
        groups.sort(key=lambda group: group["lifecycle_id"])
        if len(groups) > MAX_CANDIDATE_GROUPS_PER_REVIEW:
            raise RuntimeFailure("candidate-group-context-limit", "group-count")
        if len(canonical(groups)) > MAX_CANDIDATE_GROUP_CONTEXT_BYTES:
            raise RuntimeFailure("candidate-group-context-limit", "context-bytes")
        return groups

    def _record_task_occurrence_resolution(
        self,
        target: ProfileAuditTarget,
        executor_id: str,
        executor_identity: dict[str, Any],
        result: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        if target.receipt.payload.get("schema_version") != 2:
            return None
        boundary = result.get("occurrence_boundary")
        if not isinstance(boundary, dict):
            raise RuntimeFailure(
                "task-occurrence-boundary-invalid", "missing-boundary"
            )
        relation = boundary.get("relation")
        prior_ids = boundary.get("prior_canonical_occurrence_ids")
        if (
            relation
            not in {
                "same-occurrence",
                "new-occurrence",
                "boundary-conflict",
            }
            or not isinstance(prior_ids, list)
            or len(prior_ids) != len(set(prior_ids))
            or any(
                not isinstance(item, str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", item)
                for item in prior_ids
            )
        ):
            raise RuntimeFailure(
                "task-occurrence-boundary-invalid", "boundary-shape"
            )
        overlaps = context["prior_overlaps"]
        allowed_prior_ids = {
            item["canonical_occurrence_id"]
            for item in overlaps
            if item["canonical_occurrence_id"] is not None
        }
        if any(item not in allowed_prior_ids for item in prior_ids):
            raise RuntimeFailure(
                "task-occurrence-boundary-invalid", "unknown-prior-occurrence"
            )
        if relation == "same-occurrence" and len(prior_ids) != 1:
            raise RuntimeFailure(
                "task-occurrence-boundary-invalid", "same-requires-one-prior"
            )
        if relation == "new-occurrence" and prior_ids:
            raise RuntimeFailure(
                "task-occurrence-boundary-invalid", "new-forbids-prior"
            )
        if relation == "boundary-conflict" and not prior_ids:
            raise RuntimeFailure(
                "task-occurrence-boundary-invalid", "conflict-requires-prior"
            )
        overlap_ids = [
            item["resolution_sha256"] for item in context["prior_overlaps"]
        ]
        correction_attempt_sha256 = None
        supersedes_resolution_sha256 = None
        try:
            prior_exact = load_exact_task_occurrence(
                self.paths.task_occurrence_resolutions,
                self.paths.task_occurrence_index,
                target.profile["task_key"],
            )
        except TaskOccurrenceError as error:
            raise RuntimeFailure(
                "task-occurrence-index-invalid", error.reason
            ) from error
        if prior_exact is not None:
            prior_exact = self._validate_task_occurrence_provenance(prior_exact)
            if prior_exact["boundary_relation"] != "boundary-conflict":
                raise RuntimeFailure(
                    "task-occurrence-task-key-reused",
                    target.profile["task_key"],
                )
            correction = self.task_occurrence_correction_attempt_for(prior_exact)
            if (
                correction is None
                or correction["terminal_status"] != "replacement-profiled"
                or correction["replacement_profile_receipt_sha256"]
                != target.receipt.payload["receipt_sha256"]
            ):
                raise RuntimeFailure(
                    "task-occurrence-correction-invalid",
                    "replacement-authority",
                )
            correction_attempt_sha256 = correction["attempt_sha256"]
            supersedes_resolution_sha256 = prior_exact["resolution_sha256"]
        decision_at = (
            datetime.fromtimestamp(self.now(), timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        try:
            resolution = build_task_occurrence_resolution(
                profile=target.profile,
                receipt=target.receipt.payload,
                relation=relation,
                review_contract=TASK_OCCURRENCE_REVIEW_CONTRACT,
                review_executor=executor_id,
                review_executor_identity=executor_identity,
                decision_at=decision_at,
                prior_occurrence_ids=prior_ids,
                overlap_resolution_ids=overlap_ids,
                correction_attempt_sha256=correction_attempt_sha256,
                supersedes_resolution_sha256=supersedes_resolution_sha256,
            )
            return persist_task_occurrence_resolution(
                self.paths.task_occurrence_resolutions,
                self.paths.task_occurrence_index,
                resolution,
            )
        except TaskOccurrenceError as error:
            raise RuntimeFailure(
                "task-occurrence-resolution-invalid", error.reason
            ) from error

    def task_occurrence_conflicts_for(
        self, receipt: TaskProfileReceipt
    ) -> list[dict[str, Any]]:
        task_keys = {
            profile["task_key"] for profile in receipt.payload["profiles"]
        }
        try:
            resolutions = [
                self._validate_task_occurrence_provenance(resolution)
                for resolution in load_task_occurrence_resolutions(
                    self.paths.task_occurrence_resolutions
                )
                if (
                    resolution.get("task_key") in task_keys
                    and resolution.get("profile_receipt_sha256")
                    == receipt.payload["receipt_sha256"]
                )
            ]
        except TaskOccurrenceError as error:
            raise RuntimeFailure(
                "task-occurrence-resolution-invalid", error.reason
            ) from error
        resolution_ids = {
            resolution["resolution_sha256"] for resolution in resolutions
        }
        superseded_ids = {
            resolution["supersedes_resolution_sha256"]
            for resolution in resolutions
            if resolution["supersedes_resolution_sha256"] is not None
        }
        if not superseded_ids.issubset(resolution_ids):
            raise RuntimeFailure(
                "task-occurrence-resolution-invalid",
                "supersession-target",
            )
        return [
            resolution
            for resolution in resolutions
            if (
                resolution["resolution_sha256"] not in superseded_ids
                and resolution["boundary_relation"]
                in {"boundary-conflict", "boundary-unresolved"}
            )
        ]

    def task_occurrence_correction_attempt_for(
        self, conflict: dict[str, Any]
    ) -> dict[str, Any] | None:
        try:
            attempts = load_task_occurrence_correction_attempts(
                self.paths.task_occurrence_corrections
            )
        except TaskOccurrenceError as error:
            raise RuntimeFailure(
                "task-occurrence-correction-invalid", error.reason
            ) from error
        matches = [
            attempt
            for attempt in attempts
            if (
                attempt["source_revision"] == conflict["source_revision"]
                and attempt["qualified_session_id"]
                == conflict["qualified_session_id"]
                and attempt["correction_contract"]
                == TASK_OCCURRENCE_CORRECTION_CONTRACT
            )
        ]
        if len(matches) > 1:
            raise RuntimeFailure(
                "task-occurrence-correction-invalid", "duplicate-attempt"
            )
        return matches[0] if matches else None

    def correct_task_occurrence_conflict(
        self,
        source_name: str,
        source: ExecutableAdapter,
        receipt: TaskProfileReceipt,
        conflict: dict[str, Any],
        executor_id: str,
        executor: ExecutableAdapter,
        on_profile_operation_start: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        existing = self.task_occurrence_correction_attempt_for(conflict)
        if existing is not None:
            return {
                "status": existing["terminal_status"],
                "attempt_sha256": existing["attempt_sha256"],
                "cached": True,
            }
        conflicts = self.task_occurrence_conflicts_for(receipt)
        if (
            not conflicts
            or conflict["resolution_sha256"]
            not in {
                item["resolution_sha256"] for item in conflicts
            }
        ):
            raise RuntimeFailure(
                "task-occurrence-correction-invalid",
                "conflict-set",
            )
        correction_context = {
            "correction_contract": TASK_OCCURRENCE_CORRECTION_CONTRACT,
            "conflicts": [
                {
                    "resolution_sha256": item["resolution_sha256"],
                    "profile_id": item["profile_id"],
                    "task_key": item["task_key"],
                    "source_event_ids": item["source_event_ids"],
                    "prior_canonical_occurrence_ids": item[
                        "prior_canonical_occurrence_ids"
                    ],
                }
                for item in conflicts
            ],
            "profile_receipt_sha256": receipt.payload["receipt_sha256"],
            "qualified_session_id": conflict["qualified_session_id"],
            "source_revision": conflict["source_revision"],
        }
        started_at = (
            datetime.fromtimestamp(self.now(), timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        operation_started = False

        def record_start() -> None:
            nonlocal operation_started
            if on_profile_operation_start is not None:
                on_profile_operation_start()
            operation_started = True

        try:
            result = self.profile(
                source_name,
                source,
                conflict["qualified_session_id"],
                executor_id,
                executor,
                expected_revision=conflict["source_revision"],
                on_profile_operation_start=record_start,
                correction_context=correction_context,
            )
            if not operation_started:
                raise RuntimeFailure(
                    "task-occurrence-correction-not-started",
                    result.get("status", "unknown"),
                )
            replacement = self.indexed_task_profile_receipt_for(
                conflict["qualified_session_id"],
                conflict["source_revision"],
                executor_id,
            )
            if replacement is None:
                raise RuntimeFailure(
                    "task-occurrence-correction-invalid",
                    "replacement-receipt-missing",
                )
            replacement_sha256 = replacement.payload["receipt_sha256"]
            conflict_task_keys = {
                item["task_key"] for item in conflicts
            }
            conflict_still_present = any(
                profile["task_key"] in conflict_task_keys
                for profile in replacement.payload["profiles"]
            )
            terminal_status = (
                "boundary-unresolved"
                if (
                    replacement_sha256 == receipt.payload["receipt_sha256"]
                    or conflict_still_present
                )
                else "replacement-profiled"
            )
            if (
                terminal_status == "boundary-unresolved"
                and replacement_sha256 != receipt.payload["receipt_sha256"]
            ):
                profile_key = self._task_profile_key(
                    conflict["qualified_session_id"],
                    conflict["source_revision"],
                    executor_id,
                )
                index_lock = self.paths.state / "task-profile-index.lock"
                with index_lock.open("a+") as lock_file:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                    index = self._state(self.paths.task_profile_index, {})
                    current_entry = index.get(profile_key)
                    if (
                        not isinstance(current_entry, dict)
                        or current_entry.get("receipt_sha256")
                        != replacement_sha256
                    ):
                        raise RuntimeFailure(
                            "task-profile-concurrent-refresh", profile_key
                        )
                    index[profile_key] = {
                        "qualified_session_id": conflict[
                            "qualified_session_id"
                        ],
                        "source_revision": conflict["source_revision"],
                        "executor": executor_id,
                        "receipt_sha256": receipt.payload["receipt_sha256"],
                        "profile_set_id": receipt.payload["profile_set_id"],
                    }
                    self._write(self.paths.task_profile_index, index)
                replacement_sha256 = receipt.payload["receipt_sha256"]
        except RuntimeFailure:
            if not operation_started:
                raise
            terminal_status = "failed"
            replacement_sha256 = None
            result = {"status": "failed"}
        try:
            attempt = build_task_occurrence_correction_attempt(
                qualified_session_id=conflict["qualified_session_id"],
                source_revision=conflict["source_revision"],
                profile_receipt_sha256=receipt.payload["receipt_sha256"],
                conflict_resolution_sha256s=[
                    item["resolution_sha256"] for item in conflicts
                ],
                correction_contract=TASK_OCCURRENCE_CORRECTION_CONTRACT,
                profile_executor=executor_id,
                profile_executor_identity=executor.identity,
                started_at=started_at,
                terminal_status=terminal_status,
                replacement_profile_receipt_sha256=(
                    replacement_sha256
                    if terminal_status == "replacement-profiled"
                    else None
                ),
            )
            persist_task_occurrence_correction_attempt(
                self.paths.task_occurrence_corrections, attempt
            )
            unresolved_resolutions = []
            if terminal_status == "boundary-unresolved":
                profiles_by_id = {
                    profile["profile_id"]: profile
                    for profile in receipt.payload["profiles"]
                }
                for conflict_item in conflicts:
                    unresolved_resolution = (
                        build_task_occurrence_resolution(
                            profile=profiles_by_id[
                                conflict_item["profile_id"]
                            ],
                            receipt=receipt.payload,
                            relation="boundary-unresolved",
                            review_contract=conflict_item[
                                "review_contract"
                            ],
                            review_executor=conflict_item[
                                "review_executor"
                            ],
                            review_executor_identity=conflict_item[
                                "review_executor_identity"
                            ],
                            decision_at=started_at,
                            prior_occurrence_ids=conflict_item[
                                "prior_canonical_occurrence_ids"
                            ],
                            overlap_resolution_ids=conflict_item[
                                "overlap_resolution_ids"
                            ],
                            correction_attempt_sha256=attempt[
                                "attempt_sha256"
                            ],
                            supersedes_resolution_sha256=conflict_item[
                                "resolution_sha256"
                            ],
                        )
                    )
                    persist_task_occurrence_resolution(
                        self.paths.task_occurrence_resolutions,
                        self.paths.task_occurrence_index,
                        unresolved_resolution,
                    )
                    unresolved_resolutions.append(unresolved_resolution)
        except TaskOccurrenceError as error:
            raise RuntimeFailure(
                "task-occurrence-correction-invalid", error.reason
            ) from error
        return {
            **result,
            "status": terminal_status,
            "attempt_sha256": attempt["attempt_sha256"],
            "cached": False,
            "occurrence_resolution_sha256s": [
                resolution["resolution_sha256"]
                for resolution in unresolved_resolutions
            ],
        }

    def profile_audit_disposition_for(
        self, target: ProfileAuditTarget
    ) -> dict[str, Any] | None:
        current_path = self._profile_audit_disposition_path(
            target.profile["profile_id"]
        )
        legacy_path = self._profile_audit_disposition_path(
            target.profile["profile_id"], contract_version=1
        )
        prior_path = self._profile_audit_disposition_path(
            target.profile["profile_id"], contract_version=2
        )
        paths = [
            path
            for path in (current_path, prior_path, legacy_path)
            if path.exists()
        ]
        if not paths:
            return None
        if len(paths) > 1:
            raise RuntimeFailure(
                "profile-audit-disposition-invalid",
                f"{target.profile['profile_id']}: duplicate-versions",
            )
        path = paths[0]
        try:
            disposition = validate_profile_audit_disposition(
                read_json(path, {}),
                receipt=target.receipt.payload,
                profile=target.profile,
            )
        except ProfileAuditDispositionError as error:
            raise RuntimeFailure(
                "profile-audit-disposition-invalid",
                f"{path}: {error.reason}",
            ) from error
        origin_path = (
            self.paths.task_profile_receipts
            / f"{disposition['profile_receipt_sha256'].removeprefix('sha256:')}.json"
        )
        origin_snapshot_path = (
            self.paths.snapshots
            / f"{disposition['snapshot_sha256'].removeprefix('sha256:')}.json"
        )
        try:
            origin_receipt = read_json(origin_path, {})
            origin_snapshot = read_json(origin_snapshot_path, {})
            origin_context = validate_task_profile_receipt(
                origin_receipt,
                origin_snapshot,
                receipt_path=origin_path,
                expected_executor=disposition["profile_executor"],
                expected_executor_identity=disposition["profile_executor_identity"],
            )
        except (RuntimeFailure, TaskProfileReceiptError) as error:
            reason = (
                error.reason
                if isinstance(error, TaskProfileReceiptError)
                else error.message
            )
            raise RuntimeFailure(
                "profile-audit-disposition-invalid",
                f"{origin_path}: origin-{reason}",
            ) from error
        provenance = {
            "profile_receipt_sha256": origin_receipt.get("receipt_sha256"),
            "profile_set_id": origin_receipt.get("profile_set_id"),
            "snapshot_sha256": origin_receipt.get("snapshot_sha256"),
            "source_revision": origin_receipt.get("source_revision"),
            "profile_executor": origin_receipt.get("executor"),
            "profile_executor_identity": origin_receipt.get("executor_identity"),
        }
        if any(disposition.get(key) != value for key, value in provenance.items()):
            raise RuntimeFailure(
                "profile-audit-disposition-invalid",
                f"{origin_path}: origin-provenance",
            )
        origin_profile = next(
            (
                profile
                for profile in origin_context["profiles"]
                if (
                    profile.get("profile_id") == disposition["profile_id"]
                    and profile.get("task_key") == disposition["task_key"]
                    and digest(profile) == disposition["profile_sha256"]
                )
            ),
            None,
        )
        if origin_profile != target.profile:
            raise RuntimeFailure(
                "profile-audit-disposition-invalid",
                f"{origin_path}: origin-profile",
            )
        if (
            disposition["profile_audit_contract_version"] == 3
            and disposition["skill_load_trace"]
            != self._profile_skill_load_trace(
                origin_snapshot,
                origin_profile,
                disposition["catalog_skill_names"],
            )
        ):
            raise RuntimeFailure(
                "profile-audit-disposition-invalid",
                f"{origin_path}: catalog-load-trace",
            )
        if disposition["profile_audit_contract_version"] in {2, 3}:
            try:
                occurrence_path = task_occurrence_resolution_path(
                    self.paths.task_occurrence_resolutions,
                    disposition["occurrence_resolution_sha256"],
                )
                occurrence = validate_task_occurrence_resolution(
                    read_json(occurrence_path, {})
                )
            except TaskOccurrenceError as error:
                raise RuntimeFailure(
                    "profile-audit-disposition-invalid",
                    f"occurrence-{error.reason}",
                ) from error
            if occurrence is None:
                raise RuntimeFailure(
                    "profile-audit-disposition-invalid",
                    "occurrence-missing",
                )
            occurrence = self._validate_task_occurrence_provenance(occurrence)
            expected_occurrence = {
                "occurrence_resolution_sha256": occurrence[
                    "resolution_sha256"
                ],
                "canonical_occurrence_id": occurrence[
                    "canonical_occurrence_id"
                ],
                "boundary_relation": occurrence["boundary_relation"],
                "review_executor": occurrence["review_executor"],
                "review_executor_identity": occurrence[
                    "review_executor_identity"
                ],
            }
            disposition_occurrence = {
                "occurrence_resolution_sha256": disposition.get(
                    "occurrence_resolution_sha256"
                ),
                "canonical_occurrence_id": disposition.get(
                    "canonical_occurrence_id"
                ),
                "boundary_relation": disposition.get("boundary_relation"),
                "review_executor": disposition.get("review_executor"),
                "review_executor_identity": disposition.get(
                    "review_executor_identity"
                ),
            }
            if disposition_occurrence != expected_occurrence:
                raise RuntimeFailure(
                    "profile-audit-disposition-invalid",
                    "occurrence-provenance",
                )
        return disposition

    def profile_audit_disposition_admission_for(
        self, target: ProfileAuditTarget
    ) -> tuple[str, dict[str, Any] | None]:
        disposition = self.profile_audit_disposition_for(target)
        if disposition is None:
            return "undispositioned", None
        version = disposition["profile_audit_contract_version"]
        required_version = (
            CURRENT_PROFILE_AUDIT_CONTRACT_VERSION
            if target.receipt.payload.get("schema_version") == 2
            else 1
        )
        if version != required_version:
            return "superseded-requires-repair-backfill", disposition
        if disposition.get("boundary_relation") in {
            "boundary-conflict",
            "boundary-unresolved",
        }:
            return disposition["boundary_relation"], disposition
        return "terminal", disposition

    def mark_profile_audit_queue_terminal(
        self,
        qualified_session_id: str,
        source_revision: str,
        targets: list[ProfileAuditTarget],
    ) -> bool:
        if any(
            self.profile_audit_disposition_admission_for(target)[0] != "terminal"
            for target in targets
        ):
            return False
        self._mark_queue(
            qualified_session_id, source_revision, "profile-audited"
        )
        return True

    def _record_profile_audit_disposition(
        self,
        target: ProfileAuditTarget,
        executor_id: str,
        executor_identity: dict[str, Any],
        result: dict[str, Any],
        occurrence_resolution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        review_result = {
            key: result.get(key)
            for key in (
                "terminal_route",
                "summary",
                "routing_reason",
                "draft_reviews",
                "artifact_commit",
                "policy_deferred",
                "catalog_audit",
            )
            if key in result
        }
        disposition = build_profile_audit_disposition(
            receipt=target.receipt.payload,
            profile=target.profile,
            review_executor=executor_id,
            review_executor_identity=executor_identity,
            review_result=review_result,
            reviewed_at=self.now(),
            occurrence_resolution=occurrence_resolution,
        )
        contract_version = disposition["profile_audit_contract_version"]
        path = self._profile_audit_disposition_path(
            target.profile["profile_id"],
            contract_version=contract_version,
        )
        if path.exists():
            existing = self.profile_audit_disposition_for(target)
            if existing is None:
                raise RuntimeFailure(
                    "profile-audit-disposition-invalid", target.profile["profile_id"]
                )
            return existing
        atomic_json(path, disposition, mode=0o400)
        return disposition

    def _retained_profile_audit_dispositions(self) -> list[dict[str, Any]]:
        """Load every retained disposition, refusing misplaced or duplicate rows."""
        loaded: dict[str, dict[str, Any]] = {}
        roots = (
            (3, self.paths.profile_audit_dispositions),
            (2, self.paths.prior_profile_audit_dispositions),
            (1, self.paths.legacy_profile_audit_dispositions),
        )
        for contract_version, root in roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*.json")):
                try:
                    disposition = validate_profile_audit_disposition(
                        read_json(path, {})
                    )
                except ProfileAuditDispositionError as error:
                    raise RuntimeFailure(
                        "profile-audit-disposition-invalid",
                        f"{path}: {error.reason}",
                    ) from error
                if (
                    disposition["profile_audit_contract_version"] != contract_version
                    or path.stem != disposition["profile_id"].removeprefix("sha256:")
                ):
                    raise RuntimeFailure(
                        "profile-audit-disposition-invalid", f"{path}: misplaced"
                    )
                if disposition["profile_id"] in loaded:
                    raise RuntimeFailure(
                        "profile-audit-disposition-invalid",
                        f"{disposition['profile_id']}: duplicate-versions",
                    )
                loaded[disposition["profile_id"]] = disposition
        return [loaded[profile_id] for profile_id in sorted(loaded)]

    def derive_evaluation_routing(self) -> dict[str, Any]:
        """Give every retained catalog audit one terminal evaluation route."""
        rows: list[dict[str, Any]] = []
        for disposition in self._retained_profile_audit_dispositions():
            group = disposition.get("candidate_group_id")
            record = (
                self._candidate_lifecycle_call("read", group)
                if isinstance(group, str)
                and disposition.get("boundary_relation")
                not in {"boundary-conflict", "boundary-unresolved"}
                else None
            )
            try:
                rows.append(
                    build_evaluation_routing_row(
                        disposition=disposition,
                        lifecycle_record=record,
                        now=self.now(),
                    )
                )
            except EvaluationRoutingError as error:
                raise RuntimeFailure(
                    "evaluation-routing-invalid",
                    f"{disposition['profile_id']}: {error.reason}",
                ) from error
        try:
            summary = summarize_evaluation_routing(rows)
        except EvaluationRoutingError as error:
            raise RuntimeFailure(
                "evaluation-routing-invalid", error.reason
            ) from error
        return {
            "schema_version": 1,
            "status": "derived",
            "rows": rows,
            "summary": summary,
        }

    def handoff_candidate_for_evaluation(self, lifecycle_id: str) -> dict[str, Any]:
        """Enter the existing shadow evaluation state for one routed candidate."""
        routing = self.derive_evaluation_routing()
        matched = [
            row
            for row in routing["rows"]
            if isinstance(row["evaluation_subject"], dict)
            and row["evaluation_subject"]["lifecycle_id"] == lifecycle_id
        ]
        if not matched:
            raise RuntimeFailure("evaluation-handoff-not-routed", lifecycle_id)
        if len({digest(row["evaluation_subject"]) for row in matched}) != 1:
            raise RuntimeFailure("evaluation-handoff-ambiguous", lifecycle_id)
        subject = matched[0]["evaluation_subject"]
        record = self._candidate_lifecycle_call("read", lifecycle_id)
        if (
            candidate_record_digest(record) != subject["record_sha256"]
            or record.get("record_version") != subject["record_version"]
        ):
            raise RuntimeFailure("evaluation-handoff-stale", lifecycle_id)
        handoff = {
            "lifecycle_id": lifecycle_id,
            "candidate_id": subject["candidate_id"],
            "proposed_name": subject["proposed_name"],
            "package_path": subject["package_path"],
            "current_occurrence_count": subject["current_occurrence_count"],
            "profile_ids": sorted(row["profile_id"] for row in matched),
            "shadow_only": True,
        }
        if record.get("state") == "evaluating":
            return {
                **handoff,
                "status": "already-evaluating",
                "changed": False,
                "record_version": subject["record_version"],
                "record_sha256": subject["record_sha256"],
            }
        transitioned = self._candidate_lifecycle_call(
            "transition",
            lifecycle_id,
            "--to",
            "evaluating",
            "--reason",
            "profile-audit-evaluation-handoff",
            "--candidate-id",
            subject["candidate_id"],
            "--expected-version",
            str(subject["record_version"]),
            "--expected-record-sha256",
            subject["record_sha256"],
        )
        if (
            transitioned.get("state") != "evaluating"
            or transitioned.get("candidate_id") != subject["candidate_id"]
        ):
            raise RuntimeFailure("evaluation-handoff-refused", lifecycle_id)
        return {
            **handoff,
            "status": "evaluating",
            "changed": True,
            "record_version": transitioned["record_version"],
            "record_sha256": transitioned["record_sha256"],
        }

    def review_profile(
        self,
        source_name: str,
        source: ExecutableAdapter,
        qualified_session_id: str,
        source_revision: str,
        executor_id: str,
        executor: ExecutableAdapter,
        profile_id: str,
        on_review_operation_start: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        targets = self.profile_audit_targets_for(
            source_name,
            source,
            qualified_session_id,
            source_revision,
            executor_id,
            executor,
        )
        target = next(
            (item for item in targets if item.profile.get("profile_id") == profile_id),
            None,
        )
        if target is None:
            raise RuntimeFailure("profile-audit-profile-unavailable", profile_id)
        admission, existing = self.profile_audit_disposition_admission_for(target)
        if admission == "terminal" and existing is not None:
            return {
                "status": "already-dispositioned",
                "profile_id": profile_id,
                "disposition_sha256": existing["disposition_sha256"],
            }
        if admission == "superseded-requires-repair-backfill":
            return {
                "status": "profile-audit-disposition-superseded-requires-repair-backfill",
                "profile_id": profile_id,
                "disposition_sha256": existing["disposition_sha256"],
            }
        reused = self._reuse_exact_task_occurrence(target)
        if reused is not None:
            resolution, disposition = reused
            return {
                "status": "exact-task-reused",
                "profile_id": profile_id,
                "canonical_occurrence_id": resolution[
                    "canonical_occurrence_id"
                ],
                "occurrence_resolution_sha256": resolution[
                    "resolution_sha256"
                ],
                "disposition_sha256": disposition[
                    "disposition_sha256"
                ],
            }
        existing_resolution = self.task_occurrence_resolution_for(target)
        if (
            existing_resolution is not None
            and existing_resolution["boundary_relation"]
            in {"boundary-conflict", "boundary-unresolved"}
        ):
            status = existing_resolution["boundary_relation"]
            if (
                status == "boundary-unresolved"
                and self.task_occurrence_correction_attempt_for(
                    existing_resolution
                )
                is None
            ):
                status = "boundary-conflict"
            return {
                "status": status,
                "profile_id": profile_id,
                "occurrence_resolution_sha256": existing_resolution[
                    "resolution_sha256"
                ],
            }
        return self.review(
            source_name,
            source,
            qualified_session_id,
            [(executor_id, executor)],
            expected_revision=source_revision,
            profile_audit_target=target,
            on_review_operation_start=on_review_operation_start,
        )

    def task_profile_evidence_present_for(
        self,
        qualified_session_id: str,
        source_revision: str,
    ) -> bool:
        index = self._state(self.paths.task_profile_index, {})
        if not isinstance(index, dict):
            raise RuntimeFailure(
                "task-profile-index-invalid",
                str(self.paths.task_profile_index),
            )
        for profile_key, entry in index.items():
            if not isinstance(profile_key, str) or not isinstance(entry, dict):
                raise RuntimeFailure(
                    "task-profile-index-invalid", str(profile_key)
                )
            if (
                not isinstance(entry.get("qualified_session_id"), str)
                or not isinstance(entry.get("source_revision"), str)
                or not isinstance(entry.get("executor"), str)
                or not isinstance(entry.get("receipt_sha256"), str)
            ):
                raise RuntimeFailure(
                    "task-profile-index-invalid", profile_key
                )
            if (
                entry["qualified_session_id"] == qualified_session_id
                and entry["source_revision"] == source_revision
            ):
                return True
        return False

    def _matching_task_profile(
        self,
        result: dict[str, Any],
        receipt_path: Path | None,
        reviewed_identity: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        if receipt_path is None:
            return None, "receipt-unavailable"
        receipt = read_json(receipt_path, {})
        if not isinstance(receipt, dict):
            raise RuntimeFailure(
                "task-profile-receipt-invalid", str(receipt_path)
            )
        receipt_sha256 = receipt.get("receipt_sha256")
        receipt_body = {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
        context = result.get("transcript_context")
        if (
            not isinstance(receipt_sha256, str)
            or digest(receipt_body) != receipt_sha256
            or receipt_path.stem != receipt_sha256.removeprefix("sha256:")
            or receipt.get("qualified_session_id")
            != reviewed_identity["qualified_session_id"]
            or receipt.get("source_revision")
            != reviewed_identity["source_revision"]
            or not isinstance(context, dict)
        ):
            raise RuntimeFailure(
                "task-profile-receipt-invalid", str(receipt_path)
            )
        evidence = result.get("evidence_event_ids")
        receipt_snapshot = receipt.get("snapshot_sha256")
        if (
            context.get("snapshot_sha256")
            != (
                receipt_snapshot.removeprefix("sha256:")
                if isinstance(receipt_snapshot, str)
                else None
            )
        ):
            return None, "snapshot-mismatch"
        if not isinstance(evidence, list) or not evidence:
            return None, "review-evidence-unavailable"
        matches = [
            profile
            for profile in receipt.get("profiles", [])
            if isinstance(profile, dict)
            and profile.get("reuse_value") == "reusable-procedure"
            and isinstance(profile.get("source_event_ids"), list)
            and evidence == profile["source_event_ids"]
        ]
        if len(matches) == 1:
            return matches[0], "matched"
        return None, "ambiguous" if matches else "no-exact-match"

    def _apply_autonomous_admission_policy(
        self,
        result: dict[str, Any],
        reviewed_identity: dict[str, Any],
        task_profile_receipt: Path | None = None,
        task_profile_evidence_present: bool = False,
        occurrence_resolution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact = result.get("artifact")
        if not isinstance(artifact, dict):
            return result
        task_profile_evidence_present = (
            task_profile_evidence_present
            or task_profile_receipt is not None
        )
        catalog_outcome = (
            result.get("catalog_audit", {}).get("outcome")
            if isinstance(result.get("catalog_audit"), dict)
            else None
        )
        source_updated_at = parse_time(reviewed_identity.get("updated_at"))
        if source_updated_at is None:
            raise RuntimeFailure(
                "source-time-invalid",
                "reviewed source updated_at is not comparable",
            )
        age_seconds = max(0, self.now() - int(source_updated_at.timestamp()))
        reason: str | None = None
        if (
            task_profile_evidence_present
            and catalog_outcome
            in {"missed-skill", "wrong-or-incomplete-skill"}
        ):
            reason = "task-profile-repair-requires-evaluation"
        elif task_profile_evidence_present:
            reason = "task-profile-artifact-requires-evaluation"
        elif age_seconds > self.max_autonomous_session_age_seconds:
            reason = "historical-source-outside-mutation-window"
        elif (
            artifact.get("operation") == "create"
            and not self.allow_autonomous_skill_creation
        ):
            reason = "autonomous-create-requires-recurrence"
        if reason is None:
            return result
        shadow_candidate = None
        if reason in {
            "autonomous-create-requires-recurrence",
            "task-profile-artifact-requires-evaluation",
        }:
            matched_profile, profile_match = self._matching_task_profile(
                result,
                task_profile_receipt,
                reviewed_identity,
            )
            shadow_candidate = self._collect_shadow_candidate(
                result,
                reviewed_identity,
                matched_profile,
                profile_match,
                occurrence_resolution,
            )
            if (
                shadow_candidate is not None
                and isinstance(result.get("catalog_audit"), dict)
            ):
                result["catalog_audit"]["candidate_group_id"] = (
                    shadow_candidate["lifecycle_id"]
                )
        repair_recommendation = (
            {
                "catalog_outcome": catalog_outcome,
                "operation": artifact["operation"],
                "skill_name": artifact["skill_name"],
                "artifact_sha256": digest(artifact),
                "report_only": True,
            }
            if reason == "task-profile-repair-requires-evaluation"
            else None
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
            **(
                {"repair_recommendation": repair_recommendation}
                if repair_recommendation is not None
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
        task_profile: dict[str, Any] | None = None,
        profile_match: str = "receipt-unavailable",
        occurrence_resolution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact = result["artifact"]
        self._assert_candidate_root_isolated()
        observed = parse_time(reviewed_identity["updated_at"])
        if observed is None:
            raise RuntimeFailure(
                "candidate-lifecycle-failed",
                "reviewed source updated_at is invalid",
            )
        procedure = self._candidate_procedure(artifact)
        if task_profile is None:
            task_key = (
                "task:"
                + hashlib.sha256(
                    reviewed_identity["qualified_session_id"].encode("utf-8")
                ).hexdigest()
            )
            independence = "unverified"
            summary = result["summary"]
        else:
            profile_procedure = task_profile.get("procedure")
            if (
                not isinstance(profile_procedure, dict)
                or not isinstance(
                    task_profile.get("procedure_fingerprint"), str
                )
                or not isinstance(task_profile.get("task_key"), str)
                or not isinstance(
                    task_profile.get("abstract_summary"), str
                )
            ):
                raise RuntimeFailure(
                    "task-profile-receipt-invalid", "matched profile"
                )
            task_key = task_profile["task_key"]
            independence = "verified"
            summary = task_profile["abstract_summary"]
        if task_profile is None or occurrence_resolution is None:
            # Preserve historical shadow observations in a v1 record.  V1 is
            # explicitly non-authoritative in candidate-lifecycle recurrence().
            observation = {"task_key": task_key, "session_id": reviewed_identity["qualified_session_id"], "observed_at": observed.astimezone(timezone.utc).isoformat(), "independence": independence, "summary": summary, "procedure_fingerprint": procedure["match_fingerprint"]}
        else:
            occurrence_resolution = self._validate_task_occurrence_provenance(
                occurrence_resolution
            )
            if (
                occurrence_resolution["profile_id"] != task_profile["profile_id"]
                or occurrence_resolution["boundary_relation"]
                not in {"same-occurrence", "new-occurrence"}
                or occurrence_resolution["canonical_occurrence_id"] is None
            ):
                raise RuntimeFailure(
                    "candidate-lifecycle-failed",
                    "task occurrence resolution is not authoritative",
                )
            occurred_at = parse_time(occurrence_resolution["occurred_at"])
            decision_at = parse_time(occurrence_resolution["decision_at"])
            if occurred_at is None or occurred_at > decision_at:
                raise RuntimeFailure("candidate-lifecycle-failed", "profile occurrence time is invalid")
            observation = {
                "task_key": task_key,
                "source_session_id": reviewed_identity["qualified_session_id"],
                "canonical_occurrence_id": occurrence_resolution[
                    "canonical_occurrence_id"
                ],
                "occurred_at": occurrence_resolution["occurred_at"],
                "decision_at": occurrence_resolution["decision_at"],
                "resolution_sha256": occurrence_resolution["resolution_sha256"],
                "summary": summary,
                "procedure_fingerprint": procedure["match_fingerprint"],
            }
        # Current evidence is source anchored; legacy branch remains v1 history.

        audit = result.get("catalog_audit")
        candidate_groups = (
            audit.get("candidate_groups") if isinstance(audit, dict) else None
        )
        selected_group_id = (
            audit.get("candidate_group_id") if isinstance(audit, dict) else None
        )
        if candidate_groups is None and audit is None:
            candidate_groups = []
        if not isinstance(candidate_groups, list):
            raise RuntimeFailure(
                "candidate-lifecycle-failed", "candidate group context is absent"
            )
        selected_group = next(
            (
                group
                for group in candidate_groups
                if isinstance(group, dict)
                and group.get("lifecycle_id") == selected_group_id
            ),
            None,
        )
        lifecycle_id = None
        expected_version = None
        expected_identity = None
        if audit is None:
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
        if selected_group_id is not None:
            if (
                selected_group is None
                or artifact["skill_name"] != selected_group.get("proposed_name")
                or not isinstance(selected_group.get("record_version"), int)
                or not isinstance(selected_group.get("record_sha256"), str)
            ):
                raise RuntimeFailure(
                    "candidate-lifecycle-failed", "candidate group selection is invalid"
                )
            lifecycle_id = selected_group_id
            expected_version = selected_group["record_version"]
            expected_identity = selected_group["record_sha256"]
            procedure = selected_group["procedure"]
            observation["procedure_fingerprint"] = procedure["match_fingerprint"]

        staging_parent = self.paths.data / "candidates" / "v1" / "incoming"
        staging_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".candidate-", dir=staging_parent
        ) as temporary:
            package = Path(temporary)
            if artifact["operation"] in {"patch", "support_file"}:
                existing = self.paths.skills / artifact["skill_name"]
                for source_path in sorted(existing.rglob("*")):
                    if source_path.is_symlink():
                        raise RuntimeFailure(
                            "candidate-lifecycle-failed",
                            f"candidate source contains symlink: {source_path}",
                        )
                    if not source_path.is_file():
                        continue
                    relative = source_path.relative_to(existing)
                    if relative.as_posix().casefold() in RESERVED_SKILL_FILES:
                        continue
                    destination = package / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_path, destination)
            atomic_text(package / "SKILL.md", artifact["skill_markdown"])
            for support_file in artifact.get("support_files", []):
                destination = package / support_file["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                atomic_text(destination, support_file["content"])
            collected = self._collect_candidate_observation(
                artifact["skill_name"],
                procedure,
                observation,
                package,
                lifecycle_id=lifecycle_id,
                expected_version=expected_version,
                expected_identity=expected_identity,
            )
            return {
                **collected,
                "profile_match": profile_match,
                "independence": independence,
                **({"canonical_occurrence_id": observation["canonical_occurrence_id"]} if "canonical_occurrence_id" in observation else {}),
                **(
                    {"profile_id": task_profile["profile_id"]}
                    if task_profile is not None
                    else {}
                ),
            }

    def _assert_candidate_root_isolated(self) -> None:
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

    def _collect_candidate_observation(
        self,
        proposed_name: str,
        procedure: dict[str, Any],
        observation: dict[str, Any],
        package: Path,
        *,
        lifecycle_id: str | None = None,
        expected_version: int | None = None,
        expected_identity: str | None = None,
    ) -> dict[str, Any]:
        self._assert_candidate_root_isolated()
        with tempfile.TemporaryDirectory(
            prefix=".candidate-inputs-", dir=package.parent
        ) as temporary:
            procedure_path = Path(temporary) / "procedure.json"
            observation_path = Path(temporary) / "observation.json"
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
                proposed_name,
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
                        str(expected_identity),
                    ]
                )
            collected = self._candidate_lifecycle_call(*command)
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

    def collect_profile_candidate(
        self,
        receipt_path: Path,
        profile_id: str,
        package: Path,
        proposed_name: str,
    ) -> dict[str, Any]:
        receipt = read_json(receipt_path, {})
        if not isinstance(receipt, dict):
            raise RuntimeFailure(
                "task-profile-receipt-invalid", str(receipt_path)
            )
        receipt_sha256 = receipt.get("receipt_sha256")
        receipt_body = {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
        expected_receipt_keys = {
            "schema_version",
            "kind",
            "profile_set_id",
            "snapshot_sha256",
            "source_revision",
            "qualified_session_id",
            "observed_at",
            "executor",
            "executor_identity",
            "model",
            "profiles",
            "receipt_sha256",
        }
        if (
            set(receipt) != expected_receipt_keys
            or receipt.get("schema_version") != 1
            or receipt.get("kind") != "task_profile_receipt"
            or not isinstance(receipt_sha256, str)
            or digest(receipt_body) != receipt_sha256
            or receipt_path.stem != receipt_sha256.removeprefix("sha256:")
            or not isinstance(receipt.get("profiles"), list)
        ):
            raise RuntimeFailure(
                "task-profile-receipt-invalid", str(receipt_path)
            )
        managed_receipt = self.indexed_task_profile_receipt_for(
            receipt["qualified_session_id"],
            receipt["source_revision"],
            receipt["executor"],
            current_contract=False,
            receipt_sha256=receipt["receipt_sha256"],
        )
        if managed_receipt is None or managed_receipt.path != receipt_path:
            raise RuntimeFailure(
                "task-profile-receipt-invalid", "receipt is not managed"
            )
        for item in receipt["profiles"]:
            if not isinstance(item, dict):
                raise RuntimeFailure(
                    "task-profile-receipt-invalid", "profile shape"
                )
            model_profile = {
                key: value
                for key, value in item.items()
                if key
                not in {"task_key", "profile_id", "procedure_fingerprint"}
            }
            procedure = item.get("procedure")
            if (
                item.get("task_key")
                != digest(
                    {
                        "qualified_session_id": receipt[
                            "qualified_session_id"
                        ],
                        "source_event_ids": item.get("source_event_ids"),
                    }
                )
                or item.get("profile_id")
                != digest(
                    {
                        "qualified_session_id": receipt[
                            "qualified_session_id"
                        ],
                        **model_profile,
                    }
                )
                or item.get("procedure_fingerprint")
                != (digest(procedure) if isinstance(procedure, dict) else None)
            ):
                raise RuntimeFailure(
                    "task-profile-receipt-invalid", "profile identity"
                )
        if receipt.get("profile_set_id") != digest(
            {
                "snapshot_sha256": receipt.get("snapshot_sha256"),
                "qualified_session_id": receipt.get(
                    "qualified_session_id"
                ),
                "profiles": receipt["profiles"],
            }
        ):
            raise RuntimeFailure(
                "task-profile-receipt-invalid", "profile set identity"
            )
        profile = next(
            (
                item
                for item in receipt["profiles"]
                if isinstance(item, dict)
                and item.get("profile_id") == profile_id
            ),
            None,
        )
        if (
            profile is None
            or profile.get("reuse_value") != "reusable-procedure"
            or not isinstance(profile.get("procedure"), dict)
            or not isinstance(profile.get("procedure_fingerprint"), str)
            or not isinstance(profile.get("task_key"), str)
            or not isinstance(profile.get("abstract_summary"), str)
        ):
            raise RuntimeFailure(
                "task-profile-receipt-invalid", profile_id
            )
        procedure = {
            "schema_version": 1,
            **profile["procedure"],
            "match_fingerprint": profile["procedure_fingerprint"],
        }
        observation = {
            "task_key": profile["task_key"],
            "session_id": receipt["qualified_session_id"],
            "observed_at": receipt["observed_at"],
            "independence": "verified",
            "summary": profile["abstract_summary"],
            "procedure_fingerprint": profile["procedure_fingerprint"],
        }
        listing = self._candidate_lifecycle_call("list")
        lifecycle_id = None
        expected_version = None
        expected_identity = None
        for item in listing.get("records", []):
            record = self._candidate_lifecycle_call(
                "read", item["lifecycle_id"]
            )
            if (
                record.get("proposed_name") == proposed_name
                and record.get("procedure") == procedure
                and record.get("state")
                in {"collecting", "ready_for_draft", "expired", "rejected"}
            ):
                lifecycle_id = record["lifecycle_id"]
                expected_version = record["record_version"]
                expected_identity = candidate_record_digest(record)
                break
        return self._collect_candidate_observation(
            proposed_name,
            procedure,
            observation,
            package,
            lifecycle_id=lifecycle_id,
            expected_version=expected_version,
            expected_identity=expected_identity,
        )

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
        artifact = result["artifact"]
        existing_artifact = None
        if artifact["operation"] in {"patch", "support_file"}:
            skill_dir = self.paths.skills / artifact["skill_name"]
            skill_path = skill_dir / "SKILL.md"
            if (
                skill_dir.is_symlink()
                or skill_path.is_symlink()
                or not skill_path.is_file()
            ):
                raise RuntimeFailure("skill-missing", artifact["skill_name"])
            existing_markdown = skill_path.read_text(encoding="utf-8")
            existing_frontmatter = re.match(
                r"^---\n(.*?)\n---\n", existing_markdown, re.S
            )
            proposed_frontmatter = re.match(
                r"^---\n(.*?)\n---\n", artifact["skill_markdown"], re.S
            )
            if existing_frontmatter is None or proposed_frontmatter is None:
                raise RuntimeFailure("patch-content-loss", artifact["skill_name"])
            existing_keys = set(
                re.findall(r"(?m)^([A-Za-z0-9_-]+):", existing_frontmatter.group(1))
            )
            proposed_keys = set(
                re.findall(r"(?m)^([A-Za-z0-9_-]+):", proposed_frontmatter.group(1))
            )
            existing_headings = set(
                re.findall(r"(?m)^#{1,6}\s+\S.*$", existing_markdown)
            )
            proposed_headings = set(
                re.findall(r"(?m)^#{1,6}\s+\S.*$", artifact["skill_markdown"])
            )
            if (
                not existing_keys.issubset(proposed_keys)
                or not existing_headings.issubset(proposed_headings)
            ):
                raise RuntimeFailure("patch-content-loss", artifact["skill_name"])
            existing_artifact = {
                "skill_name": artifact["skill_name"],
                "skill_markdown": existing_markdown,
            }
        packet = {
            "contract_version": CONTRACT_VERSION,
            "packet_kind": "draft_review",
            "source_revision": reviewed_identity["source_revision"],
            "snapshot_digest": reviewed_identity["snapshot_digest"],
            "proposing_executor": proposing_executor,
            "existing_artifact": existing_artifact,
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
        profile_audit_target: ProfileAuditTarget | None = None,
        on_review_operation_start: Callable[[], None] | None = None,
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
        if profile_audit_target is None and source_name == "copilot":
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
        if profile_audit_target is None:
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
        task_profile_evidence_present = self.task_profile_evidence_present_for(
            qualified_session_id,
            current["source_revision"],
        )
        for executor_id, executor in allowed:
            mutation_started = False
            attempt_started_at = self.now()
            task_profile_delivery = "unknown"
            receipt_fields: dict[str, str] = {}
            profile_audit_fields: dict[str, Any] = (
                {
                    "profile_audit": True,
                    "profile_id": profile_audit_target.profile["profile_id"],
                    "task_key": profile_audit_target.profile["task_key"],
                }
                if profile_audit_target is not None
                else {}
            )
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
                    / (
                        f"{result_name}-{hashlib.sha256(current['source_revision'].encode()).hexdigest()}"
                        f"-{executor_name}"
                        f"{('-' + profile_audit_target.profile['profile_id'].removeprefix('sha256:')[:16]) if profile_audit_target is not None else ''}.json"
                    )
                )
                result_path.parent.mkdir(parents=True, exist_ok=True)
                if profile_audit_target is not None:
                    if (
                        executor_id != profile_audit_target.receipt.payload["executor"]
                        or not compatible_task_profile_executor_identities(
                            profile_audit_target.receipt.payload[
                                "executor_identity"
                            ],
                            executor.identity,
                        )
                    ):
                        raise RuntimeFailure(
                            "profile-audit-executor-mismatch", executor_id
                        )
                    task_profile_binding = TaskProfileBinding(
                        status="bound",
                        receipt=profile_audit_target.receipt,
                        context={"profiles": [profile_audit_target.profile]},
                    )
                else:
                    profile_capable = executor.supports(TASK_PROFILE_CAPABILITY)
                    task_profile_binding = (
                        self.task_profile_binding_for(
                            qualified_session_id,
                            reviewed_identity["source_revision"],
                            executor_id,
                            snapshot_path,
                            executor.identity,
                        )
                        if profile_capable
                        else TaskProfileBinding(status="unsupported")
                    )
                task_profile_receipt = (
                    task_profile_binding.receipt.path
                    if task_profile_binding.status == "bound"
                    and task_profile_binding.receipt is not None
                    else None
                )
                occurrence_context: dict[str, Any] | None = None
                occurrence_context_path: Path | None = None
                existing_occurrence_resolution: dict[str, Any] | None = None
                if profile_audit_target is not None:
                    existing_occurrence_resolution = (
                        self.task_occurrence_resolution_for(
                            profile_audit_target
                        )
                    )
                    occurrence_context = self._task_occurrence_context_for(
                        profile_audit_target
                    )
                    occurrence_context_path = (
                        self.paths.task_occurrence_contexts
                        / (
                            profile_audit_target.profile["profile_id"]
                            .removeprefix("sha256:")
                            + ".json"
                        )
                    )
                    atomic_json(occurrence_context_path, occurrence_context)
                task_profile_delivery = {
                    "bound": "delivered",
                    "absent": "unavailable",
                    "unsupported": "unsupported",
                    "unbound": "unbound",
                }[task_profile_binding.status]
                if task_profile_binding.reason is not None:
                    task_profile_delivery += f":{task_profile_binding.reason}"
                elif (
                    task_profile_binding.status in {"absent", "unsupported"}
                    and task_profile_evidence_present
                ):
                    task_profile_delivery += ":profiled-by-other-executor"
                receipt_fields = (
                    {
                        "task_profile_receipt_sha256": (
                            task_profile_binding.receipt.payload[
                                "receipt_sha256"
                            ]
                        )
                    }
                    if task_profile_binding.receipt is not None
                    else {}
                )
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
                        "task_profile_delivery": task_profile_delivery,
                        **receipt_fields,
                        **profile_audit_fields,
                    },
                )
                try:
                    run_arguments: dict[str, Any] = {
                        "snapshot": snapshot_path,
                        "result": result_path,
                    }
                    if task_profile_receipt is not None:
                        run_arguments.update(
                            {
                                "task_profile_receipt": task_profile_receipt,
                                "task_profile_executor": (
                                    profile_audit_target.receipt.payload["executor"]
                                    if profile_audit_target is not None
                                    else executor_id
                                ),
                            }
                        )
                    if profile_audit_target is not None:
                        run_arguments["task_profile_id"] = (
                            profile_audit_target.profile["profile_id"]
                        )
                        run_arguments["task_occurrence_context"] = (
                            occurrence_context_path
                        )
                    if on_review_operation_start is not None:
                        on_review_operation_start()
                    result = executor.call("run", **run_arguments)
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
                result = self._validated_review_result(
                    result,
                    require_catalog_audit=profile_audit_target is not None,
                    catalog_snapshot=(
                        read_json(snapshot_path, {})
                        if profile_audit_target is not None
                        else None
                    ),
                    catalog_profile=(
                        profile_audit_target.profile
                        if profile_audit_target is not None
                        else None
                    ),
                    candidate_groups=(
                        occurrence_context.get("candidate_groups")
                        if occurrence_context is not None
                        else None
                    ),
                )
                occurrence_resolution = existing_occurrence_resolution
                if (
                    profile_audit_target is not None
                    and occurrence_resolution is None
                ):
                    occurrence_resolution = (
                        self._record_task_occurrence_resolution(
                            profile_audit_target,
                            executor_id,
                            executor.identity,
                            result,
                            occurrence_context or {},
                        )
                    )
                result["transcript_context"] = self._validated_evidence_context(
                    result,
                    snapshot_path,
                    reviewed_identity,
                )
                if (
                    occurrence_resolution is not None
                    and occurrence_resolution["boundary_relation"]
                    == "boundary-conflict"
                ):
                    if (
                        result["terminal_route"] != "discard"
                        or result.get("artifact") is not None
                    ):
                        raise RuntimeFailure(
                            "task-occurrence-boundary-invalid",
                            "conflict-must-discard",
                        )
                    attempts.append(
                        {
                            "session_id": qualified_session_id,
                            "source": source_name,
                            "executor": executor_id,
                            "route": f"{source_name}>{executor_id}",
                            "policy_version": self.policy_version,
                            "status": "boundary-conflict",
                            "terminal_route": "discard",
                            "mutation_started": False,
                            "started_at": attempt_started_at,
                            "task_profile_delivery": task_profile_delivery,
                            **receipt_fields,
                            **profile_audit_fields,
                            "occurrence_resolution_sha256": (
                                occurrence_resolution["resolution_sha256"]
                            ),
                        }
                    )
                    self._write(self.paths.attempts, attempts)
                    disposition = self._record_profile_audit_disposition(
                        profile_audit_target,
                        executor_id,
                        executor.identity,
                        result,
                        occurrence_resolution,
                    )
                    self._clear_transaction(
                        qualified_session_id,
                        reviewed_identity["source_revision"],
                    )
                    return {
                        "status": "boundary-conflict",
                        "executor": executor_id,
                        "profile_id": profile_audit_target.profile["profile_id"],
                        "occurrence_resolution_sha256": (
                            occurrence_resolution["resolution_sha256"]
                        ),
                        "disposition_sha256": disposition[
                            "disposition_sha256"
                        ],
                    }
                if profile_audit_target is not None and result["terminal_route"] in {
                    "skill",
                    "support_file",
                }:
                    matched_profile, _profile_match = self._matching_task_profile(
                        result,
                        task_profile_receipt,
                        reviewed_identity,
                    )
                    if matched_profile != profile_audit_target.profile:
                        raise RuntimeFailure(
                            "profile-audit-evidence-mismatch",
                            profile_audit_target.profile["profile_id"],
                        )
                result = self._apply_autonomous_admission_policy(
                    result,
                    reviewed_identity,
                    task_profile_receipt,
                    task_profile_evidence_present,
                    occurrence_resolution,
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
                    "task_profile_delivery": task_profile_delivery,
                    **receipt_fields,
                    **profile_audit_fields,
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
                if profile_audit_target is not None:
                    disposition = self._record_profile_audit_disposition(
                        profile_audit_target,
                        executor_id,
                        executor.identity,
                        {
                            **result,
                            "artifact_commit": artifact_commit,
                        },
                        occurrence_resolution,
                    )
                else:
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
                        {
                            "profile_id": profile_audit_target.profile["profile_id"],
                            "disposition_sha256": disposition["disposition_sha256"],
                            **(
                                {
                                    "occurrence_resolution_sha256": (
                                        occurrence_resolution[
                                            "resolution_sha256"
                                        ]
                                    ),
                                    "canonical_occurrence_id": (
                                        occurrence_resolution[
                                            "canonical_occurrence_id"
                                        ]
                                    ),
                                }
                                if occurrence_resolution is not None
                                else {}
                            ),
                        }
                        if profile_audit_target is not None
                        else {}
                    ),
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
                            "task_profile_delivery": task_profile_delivery,
                            **receipt_fields,
                            **profile_audit_fields,
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
                        "task_profile_delivery": task_profile_delivery,
                        **receipt_fields,
                        **profile_audit_fields,
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
    if (
        evaluator.is_symlink()
        or not evaluator.is_file()
        or not os.access(evaluator, os.X_OK)
    ):
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


def configured_remote_evaluation_subjects(
    config: dict[str, Any],
    owner: dict[str, Any] | None,
    paths: RuntimePaths,
) -> dict[str, Any] | None:
    entry = config.get("remote_evaluation_subjects")
    if entry is None:
        return None
    if (
        owner is None
        or not isinstance(entry, dict)
        or set(entry) != REMOTE_EVALUATION_SUBJECT_KEYS
        or not isinstance(entry.get("enabled"), bool)
        or not isinstance(entry.get("command"), list)
        or not entry["command"]
        or not all(
            isinstance(value, str) and value and value == value.strip()
            for value in entry["command"]
        )
    ):
        raise RuntimeFailure(
            "invalid-adapter-config",
            "remote_evaluation_subjects is malformed",
        )
    if entry["enabled"] and not owner.get("enabled"):
        raise RuntimeFailure(
            "invalid-adapter-config",
            "remote evaluation subjects require the enabled evaluation owner",
        )
    expected_snapshot_root = (
        paths.state / "remote-evaluation-subjects"
    ).resolve()
    if (
        entry.get("protocol_version") != 1
        or not isinstance(entry.get("origin_host_id"), str)
        or not entry["origin_host_id"]
        or entry.get("max_files") != REMOTE_SUBJECT_MAX_FILES
        or entry.get("max_file_bytes") != REMOTE_SUBJECT_MAX_FILE_BYTES
        or entry.get("max_decoded_bytes")
        != REMOTE_SUBJECT_MAX_DECODED_BYTES
        or entry.get("max_encoded_bytes")
        != REMOTE_SUBJECT_MAX_ENCODED_BYTES
        or Path(str(entry.get("snapshot_root"))).resolve()
        != expected_snapshot_root
    ):
        raise RuntimeFailure(
            "invalid-adapter-config",
            "remote evaluation subject protocol bounds are malformed",
        )
    command = list(entry["command"])
    executable = Path(command[0])
    if (
        not executable.is_absolute()
        or executable.is_symlink()
        or not executable.is_file()
        or not os.access(executable, os.X_OK)
        or command.count("--fetch-subject") != 1
    ):
        raise RuntimeFailure(
            "invalid-adapter-config",
            "remote evaluation subject command is unsafe",
        )
    request_options = {
        "--census-snapshot-sha256",
        "--origin-root-id",
        "--origin-relative-path",
        "--origin-path",
        "--canonical-capability-id",
        "--origin-inventory-sha256",
    }
    if request_options.intersection(command):
        raise RuntimeFailure(
            "invalid-adapter-config",
            "remote evaluation subject command contains a mutable request",
        )
    receiver = entry.get("receiver")
    receiver_fields = {
        "receiver_id",
        "receiver_sha256",
        "collector_sha256",
        "content_policy_sha256",
    }
    if (
        not isinstance(receiver, dict)
        or set(receiver) != receiver_fields
        or not isinstance(receiver.get("receiver_id"), str)
        or not receiver["receiver_id"]
        or not all(
            re.fullmatch(r"[0-9a-f]{64}", str(receiver.get(field)))
            is not None
            for field in receiver_fields - {"receiver_id"}
        )
    ):
        raise RuntimeFailure(
            "invalid-adapter-config",
            "remote evaluation subject receiver is malformed",
        )

    def command_option(name: str) -> str | None:
        positions = [
            index for index, value in enumerate(command) if value == name
        ]
        if (
            len(positions) != 1
            or positions[0] + 1 >= len(command)
            or command[positions[0] + 1].startswith("--")
        ):
            return None
        return command[positions[0] + 1]

    expected_options = {
        "--expected-receiver-id": receiver["receiver_id"],
        "--expected-receiver-sha": receiver["receiver_sha256"],
        "--expected-collector-sha": receiver["collector_sha256"],
        "--expected-content-policy-sha": receiver[
            "content_policy_sha256"
        ],
    }
    if any(
        command_option(option) != expected
        for option, expected in expected_options.items()
    ) or any(
        command_option(option) is None
        for option in ("--known-hosts-file", "--expected-known-hosts-sha")
    ):
        raise RuntimeFailure(
            "invalid-adapter-config",
            "remote evaluation subject command pins do not match its receiver",
        )
    policy_path = (
        Path(__file__).resolve().parent.parent
        / "references"
        / "remote-subject-content-policy-v1.json"
    )
    try:
        local_policy = load_content_policy(policy_path)
    except RemoteSubjectPolicyError as error:
        raise RuntimeFailure("invalid-adapter-config", str(error)) from error
    if (
        local_policy["sha256"].removeprefix("sha256:")
        != receiver["content_policy_sha256"]
    ):
        raise RuntimeFailure(
            "invalid-adapter-config",
            "remote evaluation subject policy pin differs from local policy",
        )
    return {
        **entry,
        "command": command,
        "receiver": dict(receiver),
        "content_policy": str(policy_path),
        "snapshot_store": str(
            expected_snapshot_root
        ),
        "overlay_store": str(
            (paths.state / "evaluation-input-overlays").resolve()
        ),
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
        try:
            result = subprocess.run(
                [
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
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeFailure(
                "evaluation-input-seal-invalid",
                f"evaluation-input evaluator is unavailable: {error}",
            ) from error
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
    if (
        evaluator.is_symlink()
        or not evaluator.is_file()
        or not os.access(evaluator, os.X_OK)
    ):
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


def remote_subject_key(subject: dict[str, Any]) -> str:
    fields = {
        "origin_host_id": subject.get("origin_host_id"),
        "origin_root_id": subject.get("origin_root_id"),
        "origin_relative_path": subject.get("origin_relative_path"),
    }
    if not all(isinstance(value, str) and value for value in fields.values()):
        raise RuntimeFailure(
            "remote-candidate-invalid", "remote subject identity is malformed"
        )
    return digest(fields)


def remote_subject_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeFailure(
            "remote-candidate-invalid", "remote subject path is malformed"
        )
    path = Path(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or ".." in path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeFailure(
            "remote-candidate-invalid", "remote subject path is unsafe"
        )
    return value


def validate_remote_subject_inventory(
    value: Any, field: str, *, allow_empty: bool = False
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise RuntimeFailure("remote-candidate-invalid", f"{field} is malformed")
    result = []
    seen = set()
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256", "size"}
            or re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256"))) is None
            or not isinstance(item.get("size"), int)
            or isinstance(item.get("size"), bool)
            or item["size"] < 0
        ):
            raise RuntimeFailure(
                "remote-candidate-invalid", f"{field} entry is malformed"
            )
        relative = remote_subject_relative_path(item.get("path"))
        if relative in seen:
            raise RuntimeFailure(
                "remote-candidate-invalid", f"{field} paths collide"
            )
        seen.add(relative)
        result.append(dict(item))
    if result != sorted(result, key=lambda item: item["path"]):
        raise RuntimeFailure(
            "remote-candidate-invalid", f"{field} is not canonical"
        )
    return result


def validate_remote_subject_response(
    response: dict[str, Any],
    request: dict[str, str],
    expected_receiver: dict[str, str],
    local_policy_path: Path,
) -> dict[str, Any]:
    if (
        not isinstance(response, dict)
        or set(response) != {"ok", "receiver", "subject"}
        or response.get("ok") is not True
        or response.get("receiver") != expected_receiver
    ):
        raise RuntimeFailure(
            "remote-candidate-invalid", "remote subject response identity differs"
        )
    subject = response.get("subject")
    subject_fields = {
        "schema_version",
        "kind",
        "census_snapshot_sha256",
        "origin_host_id",
        "origin_root_id",
        "origin_relative_path",
        "origin_path",
        "canonical_capability_id",
        "origin_inventory_sha256",
        "candidate_id",
        "content_policy",
        "origin_inventory",
        "candidate_inventory",
        "excluded_sidecars",
        "files",
        "receipt_sha256",
    }
    if (
        not isinstance(subject, dict)
        or set(subject) != subject_fields
        or subject.get("schema_version") != 1
        or subject.get("kind") != "remote_evaluation_subject"
        or any(subject.get(key) != value for key, value in request.items())
        or re.fullmatch(r"sha256:[0-9a-f]{64}", str(subject.get("candidate_id")))
        is None
    ):
        raise RuntimeFailure(
            "remote-candidate-invalid", "remote subject response is malformed"
        )
    without_receipt = {
        key: value for key, value in subject.items() if key != "receipt_sha256"
    }
    if subject["receipt_sha256"] != digest(without_receipt):
        raise RuntimeFailure(
            "remote-candidate-invalid", "remote subject receipt identity differs"
        )
    remote_policy = subject.get("content_policy")
    if (
        not isinstance(remote_policy, dict)
        or set(remote_policy) != {"schema_version", "sha256"}
        or remote_policy.get("schema_version") != 1
        or remote_policy.get("sha256")
        != "sha256:" + expected_receiver["content_policy_sha256"]
    ):
        raise RuntimeFailure(
            "remote-candidate-invalid", "remote subject content policy differs"
        )
    try:
        local_policy = load_content_policy(local_policy_path)
    except RemoteSubjectPolicyError as error:
        raise RuntimeFailure("remote-candidate-invalid", str(error)) from error
    origin = validate_remote_subject_inventory(
        subject.get("origin_inventory"), "remote subject origin inventory"
    )
    candidate = validate_remote_subject_inventory(
        subject.get("candidate_inventory"),
        "remote subject candidate inventory",
    )
    excluded = validate_remote_subject_inventory(
        subject.get("excluded_sidecars"),
        "remote subject excluded sidecars",
        allow_empty=True,
    )
    if (
        sorted([*candidate, *excluded], key=lambda item: item["path"]) != origin
        or any(
            Path(item["path"]).name not in REMOTE_SUBJECT_SIDECARS
            for item in excluded
        )
        or any(
            Path(item["path"]).name in REMOTE_SUBJECT_SIDECARS
            for item in candidate
        )
        or not any(item["path"] == "SKILL.md" for item in candidate)
    ):
        raise RuntimeFailure(
            "remote-candidate-invalid", "remote subject inventory partition differs"
        )
    census_inventory = [
        {"path": item["path"], "sha256": item["sha256"]} for item in origin
    ]
    if digest(census_inventory) != request["origin_inventory_sha256"]:
        raise RuntimeFailure(
            "remote-candidate-invalid", "remote subject origin inventory differs"
        )
    if candidate_record_digest(candidate) != subject["candidate_id"]:
        raise RuntimeFailure(
            "remote-candidate-invalid", "remote subject candidate identity differs"
        )
    files = subject.get("files")
    if not isinstance(files, list) or len(files) != len(candidate):
        raise RuntimeFailure(
            "remote-candidate-invalid", "remote subject files are malformed"
        )
    decoded = {}
    total = 0
    for inventory_item, file_item in zip(candidate, files):
        if (
            not isinstance(file_item, dict)
            or set(file_item) != {
                "path",
                "sha256",
                "size",
                "content_base64",
            }
            or {
                key: file_item.get(key) for key in ("path", "sha256", "size")
            }
            != inventory_item
            or not isinstance(file_item.get("content_base64"), str)
        ):
            raise RuntimeFailure(
                "remote-candidate-invalid", "remote subject file entry differs"
            )
        try:
            content = base64.b64decode(
                file_item["content_base64"], validate=True
            )
        except (ValueError, TypeError) as error:
            raise RuntimeFailure(
                "remote-candidate-invalid", "remote subject file encoding is invalid"
            ) from error
        total += len(content)
        if (
            total > REMOTE_SUBJECT_MAX_DECODED_BYTES
            or len(content) != inventory_item["size"]
            or hashlib.sha256(content).hexdigest()
            != inventory_item["sha256"]
        ):
            raise RuntimeFailure(
                "remote-candidate-invalid", "remote subject file identity differs"
            )
        try:
            validate_text(content, inventory_item["path"], local_policy)
        except RemoteSubjectPolicyError as error:
            raise RuntimeFailure("remote-candidate-content-unsafe", str(error)) from error
        decoded[inventory_item["path"]] = content
    return {
        "subject": subject,
        "subject_key": remote_subject_key(subject),
        "local_content_policy_sha256": local_policy["sha256"],
        "decoded": decoded,
        "decoded_bytes": total,
    }


def remote_subject_store_usage(root: Path) -> int:
    total = 0
    if root.is_symlink() or not root.is_dir():
        raise RuntimeFailure(
            "remote-candidate-store-invalid", "remote subject store is unavailable"
        )
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeFailure(
                "remote-candidate-store-invalid",
                "remote subject store contains a symbolic link",
            )
        if path.is_file():
            total += path.stat().st_size
            if total > REMOTE_SUBJECT_STORE_MAX_BYTES:
                break
        elif not path.is_dir():
            raise RuntimeFailure(
                "remote-candidate-store-invalid",
                "remote subject store contains a special file",
            )
    return total


def publish_remote_subject_snapshot(
    response: dict[str, Any],
    request: dict[str, str],
    expected_receiver: dict[str, str],
    local_policy_path: Path,
    store_root: Path,
    *,
    installed_skill_roots: Iterable[Path],
) -> dict[str, Any]:
    store_root = store_root.resolve()
    if (
        store_root.is_symlink()
        or not store_root.is_dir()
        or store_root.stat().st_uid != os.getuid()
        or stat.S_IMODE(store_root.stat().st_mode) & 0o077
    ):
        raise RuntimeFailure(
            "remote-candidate-store-invalid",
            "remote subject store permissions are unsafe",
        )
    for installed in installed_skill_roots:
        absolute = installed.resolve()
        if store_root.is_relative_to(absolute) or absolute.is_relative_to(store_root):
            raise RuntimeFailure(
                "remote-candidate-store-invalid",
                "remote subject store overlaps an installed skill root",
            )
    validated = validate_remote_subject_response(
        response, request, expected_receiver, local_policy_path
    )
    if (
        remote_subject_store_usage(store_root) + validated["decoded_bytes"]
        > REMOTE_SUBJECT_STORE_MAX_BYTES
        or shutil.disk_usage(store_root).free
        < 2 * REMOTE_SUBJECT_MAX_DECODED_BYTES
        + REMOTE_SUBJECT_FREE_SPACE_RESERVE
    ):
        raise RuntimeFailure(
            "remote-candidate-store-full",
            "remote subject store has insufficient capacity",
        )
    subject = validated["subject"]
    subject_parent = store_root / validated["subject_key"].removeprefix("sha256:")
    subject_parent.mkdir(mode=0o700, exist_ok=True)
    if subject_parent.is_symlink() or not subject_parent.is_dir():
        raise RuntimeFailure(
            "remote-candidate-store-invalid",
            "remote subject directory is unsafe",
        )
    destination = subject_parent / subject["candidate_id"].removeprefix("sha256:")
    retained_receipt = {
        "schema_version": 1,
        "kind": "remote_evaluation_subject_transport_receipt",
        "receiver": expected_receiver,
        "remote_receipt_sha256": subject["receipt_sha256"],
        "local_content_policy_sha256": validated[
            "local_content_policy_sha256"
        ],
        "subject": {
            key: value
            for key, value in subject.items()
            if key not in {"files", "receipt_sha256"}
        },
    }
    retained_receipt["receipt_sha256"] = digest(retained_receipt)
    if destination.exists():
        existing = read_json(destination / "transport-receipt.json", None)
        if existing != retained_receipt:
            raise RuntimeFailure(
                "remote-candidate-publication-collision", str(destination)
            )
        return {
            "status": "existing",
            "subject_key": validated["subject_key"],
            "candidate_id": subject["candidate_id"],
            "candidate_root": str(destination / "candidate"),
            "receipt": str(destination / "transport-receipt.json"),
        }
    with tempfile.TemporaryDirectory(
        prefix=".remote-subject.", dir=store_root
    ) as temporary:
        staging = Path(temporary) / "snapshot"
        candidate_root = staging / "candidate"
        candidate_root.mkdir(parents=True, mode=0o700)
        for relative, content in validated["decoded"].items():
            target = candidate_root / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(content)
            os.chmod(target, 0o400)
        receipt_path = staging / "transport-receipt.json"
        receipt_path.write_bytes(canonical(retained_receipt))
        os.chmod(receipt_path, 0o400)
        for directory in sorted(
            [path for path in candidate_root.rglob("*") if path.is_dir()],
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            os.chmod(directory, 0o500)
        os.chmod(candidate_root, 0o500)
        publish_directory_create_only(staging, destination)
        os.chmod(destination, 0o500)
    return {
        "status": "published",
        "subject_key": validated["subject_key"],
        "candidate_id": subject["candidate_id"],
        "candidate_root": str(destination / "candidate"),
        "receipt": str(destination / "transport-receipt.json"),
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
        and (
            capability_id in item.get("candidate_capability_ids", [])
            or (
                "candidate_capability_ids" not in item
                and item.get("reason")
                not in {"unmapped", "alias_target_missing"}
            )
        )
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


def valid_overlay_evaluation(value: Any) -> bool:
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
        and value.get("state") in EVALUATION_INPUT_QUEUE_STATES
        and isinstance(value.get("status"), str)
        and isinstance(value.get("current"), bool)
        and (
            value.get("evaluated_at") is None
            or isinstance(value.get("evaluated_at"), str)
        )
        and (
            value.get("receipt_sha256") is None
            or re.fullmatch(
                r"[0-9a-f]{64}", str(value.get("receipt_sha256"))
            )
            is not None
        )
        and (
            value.get("transition_id") is None
            or re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(value.get("transition_id"))
            )
            is not None
        )
        and (
            value.get("input_manifest_sha256") is None
            or re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(value.get("input_manifest_sha256")),
            )
            is not None
        )
        and isinstance(value.get("cases"), list)
    )


def validate_remote_evaluation_overlay(
    overlay: Any,
    census: dict[str, Any],
    usage: dict[str, Any],
    receiver: dict[str, Any],
    *,
    census_receipt_sha256: str,
    usage_receipt_sha256: str,
    enabled_capability_ids: set[str],
    transport_receiver: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(overlay, dict):
        raise RuntimeFailure(
            "evaluation-overlay-invalid",
            "remote evaluation overlay is unavailable",
        )
    fields = {
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
    receiver_identity = {
        key: receiver.get(key)
        for key in ("collector_sha256", "receiver_id", "receiver_sha256")
    }
    expected_transport_receiver = (
        transport_receiver
        if transport_receiver is not None
        else {
            **receiver_identity,
            "content_policy_sha256": receiver.get(
                "content_policy_sha256"
            ),
        }
    )
    without_identity = {
        key: value for key, value in overlay.items() if key != "overlay_sha256"
    }
    if (
        set(overlay) != fields
        or overlay.get("schema_version") != 1
        or overlay.get("kind") != "remote_evaluation_overlay"
        or overlay.get("census_snapshot_sha256")
        != census.get("snapshot_sha256")
        or overlay.get("census_receipt_sha256") != census_receipt_sha256
        or overlay.get("usage_snapshot_sha256")
        != usage.get("snapshot_sha256")
        or overlay.get("usage_receipt_sha256") != usage_receipt_sha256
        or overlay.get("receiver") != receiver_identity
        or overlay.get("transport_receiver")
        != expected_transport_receiver
        or overlay.get("origin_host_id") != census.get("host_id")
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(overlay.get("evaluator_sha256"))
        )
        is None
        or overlay.get("registry_identity")
        != EVALUATION_OVERLAY_REGISTRY_IDENTITY
        or overlay.get("overlay_sha256") != digest(without_identity)
        or not isinstance(overlay.get("rows"), list)
    ):
        raise RuntimeFailure(
            "evaluation-overlay-invalid",
            "remote evaluation overlay identity is malformed or cross-run",
        )
    by_capability: dict[str, dict[str, Any]] = {}
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
        if not isinstance(row, dict) or set(row) != row_fields:
            raise RuntimeFailure(
                "evaluation-overlay-invalid",
                "remote evaluation overlay row is malformed",
            )
        capability_id = row.get("capability_id")
        identity_fields = {
            "origin_host_id": row.get("origin_host_id"),
            "origin_root_id": row.get("origin_root_id"),
            "origin_relative_path": row.get("origin_relative_path"),
        }
        relative = Path(str(row.get("origin_relative_path")))
        expected_subject_key = (
            digest(identity_fields)
            if all(isinstance(value, str) and value for value in identity_fields.values())
            else None
        )
        state = row.get("snapshot_state")
        superseded = row.get("superseded_candidate_ids")
        if (
            not isinstance(capability_id, str)
            or capability_id in by_capability
            or row.get("canonical_capability_id") != capability_id
            or row.get("origin_host_id") != census.get("host_id")
            or expected_subject_key is None
            or row.get("subject_key") != expected_subject_key
            or relative.is_absolute()
            or relative.as_posix() != row.get("origin_relative_path")
            or any(part in {"", ".", ".."} for part in relative.parts)
            or not isinstance(row.get("origin_path"), str)
            or not row["origin_path"]
            or not isinstance(row.get("origin_inventory_sha256"), str)
            or not row["origin_inventory_sha256"]
            or not isinstance(superseded, list)
            or len(superseded) != len(set(superseded))
            or not all(
                re.fullmatch(r"sha256:[0-9a-f]{64}", str(item)) is not None
                for item in superseded
            )
            or state
            not in {
                "remote_candidate_not_fetched",
                "remote_candidate_changed",
                "remote_candidate_snapshot_ready",
                "remote_candidate_refused",
            }
        ):
            raise RuntimeFailure(
                "evaluation-overlay-invalid",
                "remote evaluation overlay subject identity is malformed",
            )
        if state == "remote_candidate_snapshot_ready":
            content_path = Path(str(row.get("content_path")))
            if (
                re.fullmatch(
                    r"sha256:[0-9a-f]{64}", str(row.get("candidate_id"))
                )
                is None
                or not isinstance(row.get("content_path"), str)
                or not row["content_path"]
                or content_path.name != "candidate"
                or content_path.parent.name
                != str(row["candidate_id"]).removeprefix("sha256:")
                or content_path.parent.parent.name
                != str(row["subject_key"]).removeprefix("sha256:")
                or re.fullmatch(
                    r"sha256:[0-9a-f]{64}",
                    str(row.get("transport_receipt_sha256")),
                )
                is None
                or not valid_overlay_evaluation(row.get("evaluation"))
            ):
                raise RuntimeFailure(
                    "evaluation-overlay-invalid",
                    "ready remote evaluation overlay row is incomplete",
                )
        elif state == "remote_candidate_refused":
            refusal = row.get("snapshot_refusal")
            if (
                not isinstance(refusal, dict)
                or set(refusal)
                != {"code", "message", "receipt_sha256", "observed_at"}
                or not isinstance(refusal.get("code"), str)
                or not refusal["code"]
                or not isinstance(refusal.get("message"), str)
                or len(refusal["message"]) > 500
                or re.fullmatch(
                    r"sha256:[0-9a-f]{64}",
                    str(refusal.get("receipt_sha256")),
                )
                is None
                or not isinstance(refusal.get("observed_at"), str)
            ):
                raise RuntimeFailure(
                    "evaluation-overlay-invalid",
                    "refused remote evaluation overlay row is incomplete",
                )
        elif row.get("snapshot_refusal") is not None:
            raise RuntimeFailure(
                "evaluation-overlay-invalid",
                "non-refused remote evaluation row retains refusal evidence",
            )
        elif (
            row.get("candidate_id") is not None
            or row.get("content_path") is not None
            or row.get("transport_receipt_sha256") is not None
            or row.get("evaluation") is not None
            or (
                state == "remote_candidate_not_fetched" and superseded
            )
            or (
                state == "remote_candidate_changed" and not superseded
            )
        ):
            raise RuntimeFailure(
                "evaluation-overlay-invalid",
                "non-ready remote evaluation overlay grants local authority",
            )
        by_capability[capability_id] = row
    if set(by_capability) != enabled_capability_ids:
        raise RuntimeFailure(
            "evaluation-overlay-invalid",
            "remote evaluation overlay does not cover the enabled estate",
        )
    return by_capability


def retained_remote_subject_snapshot(
    destination: Path,
    *,
    subject_key: str,
    origin_host_id: str,
    origin_root_id: str,
    origin_relative_path: str,
) -> dict[str, Any]:
    if (
        destination.is_symlink()
        or not destination.is_dir()
        or re.fullmatch(r"[0-9a-f]{64}", destination.name) is None
    ):
        raise RuntimeFailure(
            "evaluation-overlay-invalid",
            "remote subject snapshot directory is unsafe",
        )
    receipt_path = destination / "transport-receipt.json"
    candidate_root = destination / "candidate"
    if (
        receipt_path.is_symlink()
        or not receipt_path.is_file()
        or candidate_root.is_symlink()
        or not candidate_root.is_dir()
    ):
        raise RuntimeFailure(
            "evaluation-overlay-invalid",
            "remote subject snapshot is incomplete",
        )
    try:
        raw = receipt_path.read_bytes()
        receipt = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeFailure(
            "evaluation-overlay-invalid",
            "remote subject snapshot receipt is unreadable",
        ) from error
    receipt_fields = {
        "schema_version",
        "kind",
        "receiver",
        "remote_receipt_sha256",
        "local_content_policy_sha256",
        "subject",
        "receipt_sha256",
    }
    receipt_identity = {
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    } if isinstance(receipt, dict) else {}
    if (
        not isinstance(receipt, dict)
        or set(receipt) != receipt_fields
        or raw != canonical(receipt)
        or receipt.get("schema_version") != 1
        or receipt.get("kind")
        != "remote_evaluation_subject_transport_receipt"
        or receipt.get("receipt_sha256") != digest(receipt_identity)
    ):
        raise RuntimeFailure(
            "evaluation-overlay-invalid",
            "remote subject snapshot receipt identity is invalid",
        )
    subject = receipt.get("subject")
    if (
        not isinstance(subject, dict)
        or subject.get("kind") != "remote_evaluation_subject"
        or subject.get("origin_host_id") != origin_host_id
        or subject.get("origin_root_id") != origin_root_id
        or subject.get("origin_relative_path") != origin_relative_path
        or remote_subject_key(subject) != subject_key
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(subject.get("candidate_id"))
        )
        is None
        or destination.name
        != str(subject["candidate_id"]).removeprefix("sha256:")
    ):
        raise RuntimeFailure(
            "evaluation-overlay-invalid",
            "remote subject snapshot differs from its stable subject",
        )
    return {
        "candidate_id": subject["candidate_id"],
        "canonical_capability_id": subject.get("canonical_capability_id"),
        "origin_inventory_sha256": subject.get("origin_inventory_sha256"),
        "origin_path": subject.get("origin_path"),
        "transport_receipt_sha256": receipt["receipt_sha256"],
        "candidate_root": str(candidate_root.resolve()),
    }


def retain_remote_subject_refusal(
    store_root: Path,
    request: dict[str, str],
    error: RuntimeFailure,
) -> dict[str, Any]:
    identity_fields = {
        "origin_host_id": request.get("origin_host_id"),
        "origin_root_id": request.get("origin_root_id"),
        "origin_relative_path": request.get("origin_relative_path"),
    }
    subject_key = digest(identity_fields)
    message = " ".join(error.message.split())[:500]
    receipt = {
        "schema_version": 1,
        "kind": "remote_evaluation_subject_refusal",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "census_snapshot_sha256": request.get("census_snapshot_sha256"),
        "subject_key": subject_key,
        **identity_fields,
        "origin_path": request.get("origin_path"),
        "canonical_capability_id": request.get("canonical_capability_id"),
        "origin_inventory_sha256": request.get("origin_inventory_sha256"),
        "code": error.code,
        "message": message,
    }
    receipt["receipt_sha256"] = digest(receipt)
    path = (
        store_root
        / "refusals"
        / subject_key.removeprefix("sha256:")
        / f"{receipt['receipt_sha256'].removeprefix('sha256:')}.json"
    )
    if path.exists():
        if read_json(path, None) != receipt:
            raise RuntimeFailure(
                "remote-candidate-refusal-collision", str(path)
            )
    else:
        atomic_json(path, receipt)
    return {**receipt, "path": str(path)}


def retained_remote_subject_refusal(
    store_root: Path,
    request: dict[str, str],
) -> dict[str, Any] | None:
    identity_fields = {
        "origin_host_id": request.get("origin_host_id"),
        "origin_root_id": request.get("origin_root_id"),
        "origin_relative_path": request.get("origin_relative_path"),
    }
    subject_key = digest(identity_fields)
    root = store_root / "refusals" / subject_key.removeprefix("sha256:")
    if not root.exists():
        return None
    if root.is_symlink() or not root.is_dir():
        raise RuntimeFailure(
            "evaluation-overlay-invalid",
            "remote subject refusal store is unsafe",
        )
    fields = {
        "schema_version", "kind", "observed_at", "census_snapshot_sha256",
        "subject_key", "origin_host_id", "origin_root_id",
        "origin_relative_path", "origin_path", "canonical_capability_id",
        "origin_inventory_sha256", "code", "message", "receipt_sha256",
    }
    matching = []
    for path in sorted(root.iterdir()):
        value = read_json(path, None)
        identity = {
            key: item for key, item in value.items() if key != "receipt_sha256"
        } if isinstance(value, dict) else {}
        if (
            path.is_symlink()
            or not path.is_file()
            or not isinstance(value, dict)
            or set(value) != fields
            or path.name
            != f"{str(value.get('receipt_sha256')).removeprefix('sha256:')}.json"
            or value.get("schema_version") != 1
            or value.get("kind") != "remote_evaluation_subject_refusal"
            or value.get("receipt_sha256") != digest(identity)
            or value.get("subject_key") != subject_key
            or any(value.get(key) != expected for key, expected in request.items())
            or not isinstance(value.get("code"), str)
            or not value["code"]
            or not isinstance(value.get("message"), str)
            or len(value["message"]) > 500
        ):
            raise RuntimeFailure(
                "evaluation-overlay-invalid",
                "remote subject refusal receipt is malformed",
            )
        try:
            observed_at = datetime.fromisoformat(value["observed_at"])
        except (TypeError, ValueError) as error:
            raise RuntimeFailure(
                "evaluation-overlay-invalid",
                "remote subject refusal time is malformed",
            ) from error
        matching.append((observed_at, value))
    if not matching:
        return None
    _, value = max(matching, key=lambda item: item[0])
    return {
        "code": value["code"],
        "message": value["message"],
        "receipt_sha256": value["receipt_sha256"],
        "observed_at": value["observed_at"],
    }


def remote_snapshot_evaluation(
    evaluator: Path,
    candidate_root: str,
    *,
    observed_at: str | None = None,
) -> dict[str, Any]:
    command = [
        str(evaluator),
        "portfolio-current",
        candidate_root,
        "--max-age-days",
        "90",
    ]
    if observed_at is not None:
        command.extend(["--now", observed_at])
    try:
        process = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeFailure(
            "evaluation-overlay-evaluator-failed", str(error)
        ) from error
    try:
        output = [
            json.loads(line)
            for line in process.stdout.splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as error:
        raise RuntimeFailure(
            "evaluation-overlay-evaluator-malformed", str(error)
        ) from error
    if (
        process.returncode != 0
        or len(output) != 1
        or not valid_overlay_evaluation(output[0])
    ):
        detail = (process.stderr or process.stdout).strip()[-1000:]
        raise RuntimeFailure(
            "evaluation-overlay-evaluator-failed",
            detail or "remote subject evaluator refused the snapshot",
        )
    return output[0]


def build_remote_evaluation_overlay(
    owner: dict[str, Any],
    census: dict[str, Any],
    usage: dict[str, Any],
    receiver: dict[str, Any],
    *,
    census_receipt_sha256: str,
    usage_receipt_sha256: str,
    snapshot_store: Path,
    observed_at: str | None = None,
    transport_receiver: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot_store = snapshot_store.resolve()
    if (
        snapshot_store.is_symlink()
        or not snapshot_store.is_dir()
        or snapshot_store.stat().st_uid != os.getuid()
        or stat.S_IMODE(snapshot_store.stat().st_mode) & 0o077
    ):
        raise RuntimeFailure(
            "evaluation-overlay-invalid",
            "remote subject snapshot store is unsafe",
        )
    evaluator = Path(str(owner.get("evaluator")))
    if evaluator.is_symlink() or not evaluator.is_file():
        raise RuntimeFailure(
            "evaluation-overlay-invalid",
            "remote evaluation overlay evaluator is unavailable",
        )
    physical = census.get("physical_instances")
    enabled = census.get("enabled_instances")
    if not isinstance(physical, list) or not isinstance(enabled, list):
        raise RuntimeFailure(
            "evaluation-overlay-invalid",
            "remote evaluation overlay census is malformed",
        )
    physical_by_instance = {
        item.get("instance_id"): item
        for item in physical
        if isinstance(item, dict) and isinstance(item.get("instance_id"), str)
    }
    if len(physical_by_instance) != len(physical):
        raise RuntimeFailure(
            "evaluation-overlay-invalid",
            "remote evaluation overlay physical inventory is ambiguous",
        )
    instances_by_capability: dict[str, set[str]] = {}
    for item in enabled:
        if not isinstance(item, dict) or item.get("runtime_enabled") is not True:
            continue
        capability_id = item.get("canonical_capability_id")
        instance_id = item.get("instance_id")
        if (
            not isinstance(capability_id, str)
            or not isinstance(instance_id, str)
            or instance_id not in physical_by_instance
        ):
            raise RuntimeFailure(
                "evaluation-overlay-invalid",
                "remote evaluation overlay enabled inventory is malformed",
            )
        instances_by_capability.setdefault(capability_id, set()).add(
            instance_id
        )
    rows = []
    for capability_id in sorted(instances_by_capability):
        instance_ids = instances_by_capability[capability_id]
        if len(instance_ids) != 1:
            raise RuntimeFailure(
                "evaluation-overlay-invalid",
                "remote evaluation overlay subject mapping is ambiguous",
            )
        item = physical_by_instance[next(iter(instance_ids))]
        identity_fields = {
            "origin_host_id": item.get("host_id"),
            "origin_root_id": item.get("root_id"),
            "origin_relative_path": item.get("relative_path"),
        }
        if (
            item.get("canonical_capability_id") != capability_id
            or any(
                not isinstance(value, str) or not value
                for value in identity_fields.values()
            )
            or not isinstance(item.get("absolute_path"), str)
            or not isinstance(item.get("inventory_sha256"), str)
        ):
            raise RuntimeFailure(
                "evaluation-overlay-invalid",
                "remote evaluation overlay subject is malformed",
            )
        subject_key = digest(identity_fields)
        subject_parent = snapshot_store / subject_key.removeprefix("sha256:")
        snapshots = []
        if subject_parent.exists():
            if subject_parent.is_symlink() or not subject_parent.is_dir():
                raise RuntimeFailure(
                    "evaluation-overlay-invalid",
                    "remote evaluation subject store is malformed",
                )
            snapshots = [
                retained_remote_subject_snapshot(
                    path,
                    subject_key=subject_key,
                    **identity_fields,
                )
                for path in sorted(subject_parent.iterdir())
            ]
        exact = [
            snapshot
            for snapshot in snapshots
            if snapshot["canonical_capability_id"] == capability_id
            and snapshot["origin_inventory_sha256"]
            == item["inventory_sha256"]
            and snapshot["origin_path"] == item["absolute_path"]
        ]
        if len(exact) > 1:
            raise RuntimeFailure(
                "evaluation-overlay-invalid",
                "remote evaluation subject has multiple current snapshots",
            )
        superseded = sorted(
            {
                snapshot["candidate_id"]
                for snapshot in snapshots
                if not exact or snapshot["candidate_id"] != exact[0]["candidate_id"]
            }
        )
        if exact:
            selected = exact[0]
            row = {
                "candidate_id": selected["candidate_id"],
                "superseded_candidate_ids": superseded,
                "snapshot_state": "remote_candidate_snapshot_ready",
                "content_path": selected["candidate_root"],
                "transport_receipt_sha256": selected[
                    "transport_receipt_sha256"
                ],
                "snapshot_refusal": None,
                "evaluation": remote_snapshot_evaluation(
                    evaluator,
                    selected["candidate_root"],
                    observed_at=observed_at,
                ),
            }
        else:
            request = {
                "census_snapshot_sha256": census.get("snapshot_sha256"),
                **identity_fields,
                "origin_path": item["absolute_path"],
                "canonical_capability_id": capability_id,
                "origin_inventory_sha256": item["inventory_sha256"],
            }
            refusal = retained_remote_subject_refusal(snapshot_store, request)
            row = {
                "candidate_id": None,
                "superseded_candidate_ids": superseded,
                "snapshot_state": (
                    "remote_candidate_refused"
                    if refusal is not None
                    else "remote_candidate_changed"
                    if superseded
                    else "remote_candidate_not_fetched"
                ),
                "content_path": None,
                "transport_receipt_sha256": None,
                "snapshot_refusal": refusal,
                "evaluation": None,
            }
        rows.append(
            {
                "capability_id": capability_id,
                "subject_key": subject_key,
                **identity_fields,
                "origin_path": item["absolute_path"],
                "canonical_capability_id": capability_id,
                "origin_inventory_sha256": item["inventory_sha256"],
                **row,
            }
        )
    overlay = {
        "schema_version": 1,
        "kind": "remote_evaluation_overlay",
        "census_snapshot_sha256": census.get("snapshot_sha256"),
        "census_receipt_sha256": census_receipt_sha256,
        "usage_snapshot_sha256": usage.get("snapshot_sha256"),
        "usage_receipt_sha256": usage_receipt_sha256,
        "receiver": {
            key: receiver.get(key)
            for key in ("collector_sha256", "receiver_id", "receiver_sha256")
        },
        "transport_receiver": (
            transport_receiver
            if transport_receiver is not None
            else {
                key: receiver.get(key)
                for key in (
                    "collector_sha256",
                    "content_policy_sha256",
                    "receiver_id",
                    "receiver_sha256",
                )
            }
        ),
        "origin_host_id": census.get("host_id"),
        "evaluator_sha256": "sha256:"
        + hashlib.sha256(evaluator.read_bytes()).hexdigest(),
        "registry_identity": EVALUATION_OVERLAY_REGISTRY_IDENTITY,
        "rows": rows,
    }
    overlay["overlay_sha256"] = digest(overlay)
    validate_remote_evaluation_overlay(
        overlay,
        census,
        usage,
        receiver,
        census_receipt_sha256=census_receipt_sha256,
        usage_receipt_sha256=usage_receipt_sha256,
        enabled_capability_ids=set(instances_by_capability),
        transport_receiver=overlay["transport_receiver"],
    )
    return overlay


def publish_remote_evaluation_overlay(
    overlay: dict[str, Any], overlay_root: Path
) -> Path:
    overlay_root = overlay_root.resolve()
    if not overlay_root.exists():
        overlay_root.mkdir(parents=True, mode=0o700)
    if (
        overlay_root.is_symlink()
        or not overlay_root.is_dir()
        or overlay_root.stat().st_uid != os.getuid()
        or stat.S_IMODE(overlay_root.stat().st_mode) & 0o077
    ):
        raise RuntimeFailure(
            "evaluation-overlay-store-invalid",
            "remote evaluation overlay store is unsafe",
        )
    identity = overlay.get("overlay_sha256")
    if (
        re.fullmatch(r"sha256:[0-9a-f]{64}", str(identity)) is None
        or identity
        != digest(
            {
                key: value
                for key, value in overlay.items()
                if key != "overlay_sha256"
            }
        )
    ):
        raise RuntimeFailure(
            "evaluation-overlay-invalid",
            "remote evaluation overlay identity is invalid",
        )
    content = canonical(overlay)
    destination = overlay_root / f"{identity.removeprefix('sha256:')}.json"
    if destination.exists():
        if (
            destination.is_symlink()
            or not destination.is_file()
            or destination.read_bytes() != content
        ):
            raise RuntimeFailure(
                "evaluation-overlay-publication-collision",
                str(destination),
            )
    else:
        with tempfile.NamedTemporaryFile(
            prefix=".overlay.", dir=overlay_root, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.chmod(temporary_path, 0o400)
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                if (
                    destination.is_symlink()
                    or not destination.is_file()
                    or destination.read_bytes() != content
                ):
                    raise RuntimeFailure(
                        "evaluation-overlay-publication-collision",
                        str(destination),
                    )
            directory_fd = os.open(overlay_root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary_path.unlink(missing_ok=True)
    return destination


def promote_remote_evaluation_overlay(
    overlay: dict[str, Any],
    overlay_root: Path,
    census: dict[str, Any],
    usage: dict[str, Any],
    receiver: dict[str, Any],
    *,
    census_receipt_sha256: str,
    usage_receipt_sha256: str,
    enabled_capability_ids: set[str],
    transport_receiver: dict[str, Any] | None = None,
) -> Path:
    validate_remote_evaluation_overlay(
        overlay,
        census,
        usage,
        receiver,
        census_receipt_sha256=census_receipt_sha256,
        usage_receipt_sha256=usage_receipt_sha256,
        enabled_capability_ids=enabled_capability_ids,
        transport_receiver=transport_receiver,
    )
    identity = overlay["overlay_sha256"]
    destination = (
        overlay_root.resolve() / f"{identity.removeprefix('sha256:')}.json"
    )
    if (
        destination.is_symlink()
        or not destination.is_file()
        or destination.read_bytes() != canonical(overlay)
    ):
        raise RuntimeFailure(
            "evaluation-overlay-invalid",
            "remote evaluation overlay is not immutably retained",
        )
    pointer_identity = {
        "schema_version": 1,
        "overlay_sha256": identity,
        "census_snapshot_sha256": overlay["census_snapshot_sha256"],
        "census_receipt_sha256": overlay["census_receipt_sha256"],
        "usage_snapshot_sha256": overlay["usage_snapshot_sha256"],
        "usage_receipt_sha256": overlay["usage_receipt_sha256"],
    }
    pointer_path = overlay_root.parent / "evaluation-input-overlay-current.json"
    atomic_json(
        pointer_path,
        {
            **pointer_identity,
            "pointer_sha256": digest(pointer_identity),
        },
        mode=0o600,
    )
    return pointer_path


def derive_evaluation_input_queue(
    owner: dict[str, Any],
    census: dict[str, Any],
    usage: dict[str, Any],
    receiver: dict[str, Any],
    *,
    census_receipt_sha256: str,
    usage_receipt_sha256: str,
    evaluation_overlay: dict[str, Any] | None = None,
    transport_receiver: dict[str, Any] | None = None,
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
        or (
            evaluation_overlay is None
            and census.get("evidence", {})
            .get("evaluation_inventory", {})
            .get("complete")
            is not True
        )
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
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("reason"), str)
            or (
                candidate_ids is not None
                and (
                    not isinstance(candidate_ids, list)
                    or not all(isinstance(value, str) for value in candidate_ids)
                )
            )
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
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("reason"), str)
            or (
                candidate_ids is not None
                and (
                    not isinstance(candidate_ids, list)
                    or not all(isinstance(value, str) for value in candidate_ids)
                )
            )
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
    overlay_by_capability = (
        validate_remote_evaluation_overlay(
            evaluation_overlay,
            census,
            usage,
            receiver,
            census_receipt_sha256=census_receipt_sha256,
            usage_receipt_sha256=usage_receipt_sha256,
            enabled_capability_ids=set(enabled_by_capability),
            transport_receiver=transport_receiver,
        )
        if evaluation_overlay is not None
        else None
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
        if representative is not None and (
            representative.get("canonical_capability_id") != capability_id
            or (
                overlay_by_capability is None
                and (
                    representative.get("evaluation_complete") is not True
                    or not isinstance(evaluation, dict)
                    or evaluation.get("state")
                    not in EVALUATION_INPUT_QUEUE_STATES
                    or not isinstance(evaluation.get("status"), str)
                    or not isinstance(evaluation.get("current"), bool)
                    or not isinstance(evaluation.get("cases"), list)
                )
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
        overlay_row = (
            overlay_by_capability.get(capability_id)
            if overlay_by_capability is not None
            else None
        )
        if overlay_by_capability is not None:
            if representative is None or overlay_row is None:
                raise RuntimeFailure(
                    "evaluation-overlay-invalid",
                    "remote evaluation overlay cannot resolve one physical subject",
                )
            if (
                overlay_row["origin_host_id"] != representative.get("host_id")
                or overlay_row["origin_root_id"] != representative.get("root_id")
                or overlay_row["origin_relative_path"]
                != representative.get("relative_path")
                or overlay_row["origin_path"]
                != representative.get("absolute_path")
                or overlay_row["canonical_capability_id"]
                != representative.get("canonical_capability_id")
                or overlay_row["origin_inventory_sha256"]
                != representative.get("inventory_sha256")
            ):
                raise RuntimeFailure(
                    "evaluation-overlay-invalid",
                    "remote evaluation overlay differs from the census subject",
                )
            evaluation = (
                overlay_row["evaluation"]
                if overlay_row["evaluation"] is not None
                else {
                    "state": "missing",
                    "status": overlay_row["snapshot_state"],
                    "current": False,
                    "evaluated_at": None,
                    "receipt_sha256": None,
                    "transition_id": None,
                    "input_manifest_sha256": None,
                    "cases": [],
                }
            )
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
            skill_path = Path(
                overlay_row["content_path"]
                if overlay_row is not None
                and overlay_row["content_path"] is not None
                else representative["absolute_path"]
            )
            needs_transport = (
                overlay_row is not None
                and overlay_row["snapshot_state"]
                in {
                    "remote_candidate_not_fetched",
                    "remote_candidate_changed",
                }
            )
            try:
                resolved_skill = (
                    None if needs_transport else skill_path.resolve(strict=True)
                )
            except OSError:
                resolved_skill = None
            if needs_transport:
                if capability_id not in indexed_content:
                    deferral_reason = "input_not_ready"
            elif (
                skill_path.is_symlink()
                or resolved_skill is None
                or resolved_skill != skill_path
                or not skill_path.is_dir()
                or not (skill_path / "SKILL.md").is_file()
            ):
                deferral_reason = "capability_path_unavailable"
            elif (
                (
                    overlay_row is None
                    or overlay_row.get("content_path") is None
                )
                and len(physical_by_path.get(str(skill_path), set())) != 1
            ):
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
            "transport"
            if overlay_row is not None
            and overlay_row["snapshot_state"]
            in {
                "remote_candidate_not_fetched",
                "remote_candidate_changed",
            }
            else "authoring"
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
                "subject_key": (
                    overlay_row["subject_key"]
                    if overlay_row is not None
                    else None
                ),
                "origin_host_id": (
                    overlay_row["origin_host_id"]
                    if overlay_row is not None
                    else census.get("host_id")
                ),
                "snapshot_state": (
                    overlay_row["snapshot_state"]
                    if overlay_row is not None
                    else "local_candidate"
                ),
                "required_phase": required_phase,
                "runnable_phase": (
                    required_phase
                    if deferral_reason is None
                    and required_phase in {"transport", "authoring"}
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
        "evaluation_overlay_sha256": (
            evaluation_overlay["overlay_sha256"]
            if evaluation_overlay is not None
            else None
        ),
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


def remote_subject_request_for_row(
    census: dict[str, Any], row: dict[str, Any]
) -> dict[str, str]:
    physical = census.get("physical_instances")
    if not isinstance(physical, list):
        raise RuntimeFailure(
            "remote-candidate-invalid",
            "remote subject census inventory is unavailable",
        )
    matching = [
        item
        for item in physical
        if isinstance(item, dict)
        and item.get("canonical_capability_id") == row.get("capability_id")
        and item.get("host_id") == row.get("origin_host_id")
        and item.get("root_id") == row.get("origin_root_id")
        and item.get("relative_path") == row.get("origin_relative_path")
        and item.get("absolute_path") == row.get("skill_path")
    ]
    if len(matching) != 1:
        raise RuntimeFailure(
            "remote-candidate-invalid",
            "remote subject queue row no longer resolves uniquely",
        )
    item = matching[0]
    request = {
        "census_snapshot_sha256": census.get("snapshot_sha256"),
        "origin_host_id": item.get("host_id"),
        "origin_root_id": item.get("root_id"),
        "origin_relative_path": item.get("relative_path"),
        "origin_path": item.get("absolute_path"),
        "canonical_capability_id": item.get("canonical_capability_id"),
        "origin_inventory_sha256": item.get("inventory_sha256"),
    }
    if not all(isinstance(value, str) and value for value in request.values()):
        raise RuntimeFailure(
            "remote-candidate-invalid",
            "remote subject request is malformed",
        )
    return request


def stop_remote_subject_process(process: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def run_remote_subject_fetch(
    remote: dict[str, Any],
    request: dict[str, str],
    paths: RuntimePaths,
    *,
    halt_check: Callable[[], bool],
    lease_check: Callable[[], bool],
    timeout_seconds: int = 240,
    max_output_bytes: int = 64 * 1024 * 1024,
) -> dict[str, Any]:
    command = list(remote["command"])
    for option, key in (
        ("--census-snapshot-sha256", "census_snapshot_sha256"),
        ("--origin-root-id", "origin_root_id"),
        ("--origin-relative-path", "origin_relative_path"),
        ("--origin-path", "origin_path"),
        ("--canonical-capability-id", "canonical_capability_id"),
        ("--origin-inventory-sha256", "origin_inventory_sha256"),
    ):
        command.extend([option, request[key]])
    paths.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(
        prefix=".remote-subject-fetch.", dir=paths.state
    ) as temporary:
        stdout_path = Path(temporary) / "stdout"
        stderr_path = Path(temporary) / "stderr"
        with stdout_path.open("w+b") as stdout_file, stderr_path.open(
            "w+b"
        ) as stderr_file:
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                )
            except OSError as error:
                raise RuntimeFailure(
                    "remote-candidate-fetch-failed", str(error)
                ) from error
            started = time.monotonic()
            while process.poll() is None:
                if halt_check():
                    stop_remote_subject_process(process)
                    return {"status": "halted", "response": None}
                if not lease_check():
                    stop_remote_subject_process(process)
                    return {"status": "lock_lost", "response": None}
                if (
                    stdout_path.stat().st_size + stderr_path.stat().st_size
                    > max_output_bytes
                ):
                    stop_remote_subject_process(process)
                    raise RuntimeFailure(
                        "remote-candidate-fetch-oversized",
                        "remote subject transport output exceeded its bound",
                    )
                if time.monotonic() - started >= timeout_seconds:
                    stop_remote_subject_process(process)
                    raise RuntimeFailure(
                        "remote-candidate-fetch-timeout",
                        "remote subject transport exceeded its timeout",
                    )
                time.sleep(0.05)
            stdout_file.flush()
            stderr_file.flush()
        stdout = stdout_path.read_bytes()
        stderr = stderr_path.read_bytes()
    if len(stdout) + len(stderr) > max_output_bytes:
        raise RuntimeFailure(
            "remote-candidate-fetch-oversized",
            "remote subject transport output exceeded its bound",
        )
    try:
        values = [
            json.loads(line)
            for line in stdout.decode("utf-8").splitlines()
            if line.strip()
        ]
        error_text = stderr.decode("utf-8", errors="replace")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeFailure(
            "remote-candidate-fetch-malformed", str(error)
        ) from error
    if (
        process.returncode != 0
        or len(values) != 1
        or not isinstance(values[0], dict)
    ):
        detail = (error_text or stdout.decode("utf-8", errors="replace"))
        raise RuntimeFailure(
            "remote-candidate-fetch-failed",
            detail.strip()[-1000:]
            or f"remote subject transport exited {process.returncode}",
        )
    return {"status": "fetched", "response": values[0]}


def execute_remote_subject_transport(
    remote: dict[str, Any],
    census: dict[str, Any],
    row: dict[str, Any],
    paths: RuntimePaths,
    *,
    halt_check: Callable[[], bool] | None = None,
    lease_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    if not remote.get("enabled") or row.get("runnable_phase") != "transport":
        return {"status": "idle", "selected_capability_id": None}
    halt_check = halt_check or (
        lambda: evaluation_input_owner_halt_path(paths).exists()
    )
    lease_check = lease_check or evaluation_input_owner_lease_valid
    if halt_check():
        return {"status": "halted", "selected_capability_id": row["capability_id"]}
    if not lease_check():
        return {
            "status": "lock_lost",
            "selected_capability_id": row["capability_id"],
        }
    request = remote_subject_request_for_row(census, row)
    try:
        fetched = run_remote_subject_fetch(
            remote,
            request,
            paths,
            halt_check=halt_check,
            lease_check=lease_check,
        )
    except RuntimeFailure as error:
        refusal = retain_remote_subject_refusal(
            Path(remote["snapshot_store"]), request, error
        )
        return {
            "status": "refused",
            "selected_capability_id": row["capability_id"],
            "refusal_code": refusal["code"],
            "refusal_receipt_sha256": refusal["receipt_sha256"],
        }
    if fetched["status"] != "fetched":
        return {
            "status": fetched["status"],
            "selected_capability_id": row["capability_id"],
        }
    if halt_check():
        return {"status": "halted", "selected_capability_id": row["capability_id"]}
    if not lease_check():
        return {
            "status": "lock_lost",
            "selected_capability_id": row["capability_id"],
        }
    store = Path(remote["snapshot_store"])
    if not store.exists():
        store.mkdir(parents=True, mode=0o700)
    try:
        published = publish_remote_subject_snapshot(
            fetched["response"],
            request,
            remote["receiver"],
            Path(remote["content_policy"]),
            store,
            installed_skill_roots=[
                Path(item["absolute_path"])
                for item in census.get("physical_instances", [])
                if isinstance(item, dict)
                and isinstance(item.get("absolute_path"), str)
            ],
        )
    except RuntimeFailure as error:
        refusal = retain_remote_subject_refusal(store, request, error)
        return {
            "status": "refused",
            "selected_capability_id": row["capability_id"],
            "refusal_code": refusal["code"],
            "refusal_receipt_sha256": refusal["receipt_sha256"],
        }
    if halt_check():
        status = "halted"
    elif not lease_check():
        status = "lock_lost"
    else:
        status = "published"
    return {
        "status": status,
        "selected_capability_id": row["capability_id"],
        "subject_key": published["subject_key"],
        "candidate_id": published["candidate_id"],
        "candidate_root": published["candidate_root"],
        "publication_status": published["status"],
    }


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


def configured_runtime_settings(
    config: dict[str, Any],
    *,
    include_scheduler: bool = True,
) -> dict[str, Any]:
    runtime_defaults = {
        "policy_version": 2,
        "overlap_seconds": 300,
        "quiet_retry_seconds": 300,
        "max_snapshot_bytes": 100_000,
        "max_events": 2_000,
        "max_field_bytes": 64_000,
        "max_autonomous_session_age_days": 30,
        "allow_autonomous_skill_creation": False,
    }
    scheduler_defaults = {
        "page_size": 100,
        "max_pages_per_run": 100,
        "max_reviews_per_run": 25,
        "max_profiles_per_run": 100,
        "max_profile_elapsed_seconds": 600,
    }
    defaults = {
        **runtime_defaults,
        **(scheduler_defaults if include_scheduler else {}),
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
        if name == "max_profiles_per_run" and value > 500:
            raise RuntimeFailure(
                "invalid-adapter-config",
                f"{name} must not exceed 500",
            )
        if name == "max_profile_elapsed_seconds" and value > 1_800:
            raise RuntimeFailure(
                "invalid-adapter-config",
                f"{name} must not exceed 1800",
            )
        settings[name] = value
    return settings


def profile_budget_reason(
    attempts: int,
    started_at: float,
    settings: dict[str, Any],
    *,
    now: float | None = None,
) -> str | None:
    if attempts >= settings["max_profiles_per_run"]:
        return "session-limit"
    observed_at = time.monotonic() if now is None else now
    if observed_at - started_at >= settings["max_profile_elapsed_seconds"]:
        return "elapsed-time-limit"
    return None


def configured_runtime(
    paths: RuntimePaths,
    routes: Iterable[tuple[str, str]],
    config: dict[str, Any],
    *,
    parent_run_id: str | None = None,
) -> DreamingRuntime:
    settings = configured_runtime_settings(config, include_scheduler=False)
    return DreamingRuntime(
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
        parent_run_id=parent_run_id,
    )


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
    core = configured_runtime(
        paths,
        routes,
        parent_run_id=os.environ.get("DREAMING_PARENT_RUN_ID") or None,
        config=config,
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
        "profiles": [],
        "profile_skips": [],
        "profile_failures": [],
        "deferred_profiles": 0,
        "profile_budget": {
            "max_sessions": settings["max_profiles_per_run"],
            "max_elapsed_seconds": settings["max_profile_elapsed_seconds"],
            "attempts": 0,
            "elapsed_seconds": 0,
            "exhausted_reason": None,
        },
        "reviews": [],
        "deferred_reviews": 0,
        "review_budget": {
            "max_operations": settings["max_reviews_per_run"],
            "started_operations": 0,
        },
        "profile_review_skips": [],
        "accounting": {
            "schema_version": 2,
            "status": "pending",
            "receipt": None,
            "receipt_sha256": None,
            "queue_terminal_accounting": [],
            "profile_operation_accounting": [],
            "profile_terminal_accounting": [],
            "review_operation_accounting": [],
            "review_terminal_accounting": [],
            "eligible_backlog": {"profiles": 0, "reviews": 0},
            "unused_capacity": {"profiles": 0, "reviews": 0},
            "stop_reason": {"profiles": None, "reviews": None},
        },
        "publication": [],
        "evaluation_routing": {
            "schema_version": 1,
            "status": "pending",
            "summary": None,
            "rows": [],
        },
        "errors": adapter_errors,
        "legacy_records_imported": imported_legacy,
    }
    try:
        evaluation_owner = configured_evaluation_input_owner(
            config, config_path, paths
        )
        remote_subjects = configured_remote_evaluation_subjects(
            config, evaluation_owner, paths
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
            "remote_subjects": (
                {
                    "configured": True,
                    "enabled": remote_subjects["enabled"],
                }
                if remote_subjects is not None
                else {"configured": False, "enabled": False}
            ),
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
            evaluation_overlay = None
            if remote_subjects is not None and remote_subjects["enabled"]:
                if (
                    queue_evidence["census"].get("host_id")
                    != remote_subjects["origin_host_id"]
                ):
                    raise RuntimeFailure(
                        "evaluation-overlay-invalid",
                        "remote evaluation origin host differs from the census",
                    )
                if evaluation_input_owner_halt_path(paths).exists():
                    report["evaluation_input"]["run"] = {
                        "status": "halted",
                        "selected_capability_id": None,
                    }
                    return report
                if not evaluation_input_owner_lease_valid():
                    report["evaluation_input"]["run"] = {
                        "status": "lock_lost",
                        "selected_capability_id": None,
                    }
                    report["errors"].append(
                        {
                            "phase": "evaluation-input-run",
                            "code": "writer-lock-lost",
                        }
                    )
                    report["ok"] = False
                    return report
                snapshot_store = Path(remote_subjects["snapshot_store"])
                if not snapshot_store.exists():
                    snapshot_store.mkdir(parents=True, mode=0o700)
                evaluation_overlay = build_remote_evaluation_overlay(
                    evaluation_owner,
                    queue_evidence["census"],
                    usage,
                    queue_evidence["receiver"],
                    census_receipt_sha256=queue_evidence["summary"][
                        "receipt_sha256"
                    ],
                    usage_receipt_sha256=usage_summary["receipt_sha256"],
                    snapshot_store=snapshot_store,
                    transport_receiver=remote_subjects["receiver"],
                )
                overlay_path = publish_remote_evaluation_overlay(
                    evaluation_overlay,
                    Path(remote_subjects["overlay_store"]),
                )
                report["evaluation_input"]["remote_subjects"].update(
                    {
                        "overlay_sha256": evaluation_overlay[
                            "overlay_sha256"
                        ],
                        "overlay_path": str(overlay_path),
                    }
                )
                if evaluation_input_owner_halt_path(paths).exists():
                    report["evaluation_input"]["run"] = {
                        "status": "halted",
                        "selected_capability_id": None,
                    }
                    return report
                if not evaluation_input_owner_lease_valid():
                    report["evaluation_input"]["run"] = {
                        "status": "lock_lost",
                        "selected_capability_id": None,
                    }
                    report["errors"].append(
                        {
                            "phase": "evaluation-input-run",
                            "code": "writer-lock-lost",
                        }
                    )
                    report["ok"] = False
                    return report
                promote_remote_evaluation_overlay(
                    evaluation_overlay,
                    Path(remote_subjects["overlay_store"]),
                    queue_evidence["census"],
                    usage,
                    queue_evidence["receiver"],
                    census_receipt_sha256=queue_evidence["summary"][
                        "receipt_sha256"
                    ],
                    usage_receipt_sha256=usage_summary["receipt_sha256"],
                    enabled_capability_ids={
                        row["capability_id"]
                        for row in evaluation_overlay["rows"]
                    },
                    transport_receiver=remote_subjects["receiver"],
                )
            derived_queue = derive_evaluation_input_queue(
                evaluation_owner,
                queue_evidence["census"],
                usage,
                queue_evidence["receiver"],
                census_receipt_sha256=queue_evidence["summary"]["receipt_sha256"],
                usage_receipt_sha256=usage_summary["receipt_sha256"],
                evaluation_overlay=evaluation_overlay,
                transport_receiver=(
                    remote_subjects["receiver"]
                    if remote_subjects is not None
                    and remote_subjects["enabled"]
                    else None
                ),
            )
            report["evaluation_input"]["queue"] = derived_queue
            if remote_subjects is not None and remote_subjects["enabled"]:
                if evaluation_input_owner_halt_path(paths).exists():
                    report["evaluation_input"]["run"] = {
                        "status": "halted",
                        "selected_capability_id": None,
                    }
                    return report
                if not evaluation_input_owner_lease_valid():
                    report["evaluation_input"]["run"] = {
                        "status": "lock_lost",
                        "selected_capability_id": None,
                    }
                    report["errors"].append(
                        {
                            "phase": "evaluation-input-run",
                            "code": "writer-lock-lost",
                        }
                    )
                    report["ok"] = False
                    return report
                transport_row = next(
                    (
                        row
                        for row in derived_queue["rows"]
                        if row.get("runnable_phase") == "transport"
                    ),
                    None,
                )
                if transport_row is not None:
                    transport = execute_remote_subject_transport(
                        remote_subjects,
                        queue_evidence["census"],
                        transport_row,
                        paths,
                    )
                    report["evaluation_input"]["remote_subjects"][
                        "transport"
                    ] = transport
                    if transport["status"] in {"halted", "lock_lost"}:
                        report["evaluation_input"]["run"] = transport
                        if transport["status"] == "lock_lost":
                            report["errors"].append(
                                {
                                    "phase": "evaluation-input-run",
                                    "code": "writer-lock-lost",
                                }
                            )
                            report["ok"] = False
                        return report
                    if transport["status"] in {"published", "refused"}:
                        evaluation_overlay = build_remote_evaluation_overlay(
                            evaluation_owner,
                            queue_evidence["census"],
                            usage,
                            queue_evidence["receiver"],
                            census_receipt_sha256=queue_evidence["summary"][
                                "receipt_sha256"
                            ],
                            usage_receipt_sha256=usage_summary[
                                "receipt_sha256"
                            ],
                            snapshot_store=Path(
                                remote_subjects["snapshot_store"]
                            ),
                            transport_receiver=remote_subjects["receiver"],
                        )
                        overlay_path = publish_remote_evaluation_overlay(
                            evaluation_overlay,
                            Path(remote_subjects["overlay_store"]),
                        )
                        derived_queue = derive_evaluation_input_queue(
                            evaluation_owner,
                            queue_evidence["census"],
                            usage,
                            queue_evidence["receiver"],
                            census_receipt_sha256=queue_evidence["summary"][
                                "receipt_sha256"
                            ],
                            usage_receipt_sha256=usage_summary[
                                "receipt_sha256"
                            ],
                            evaluation_overlay=evaluation_overlay,
                            transport_receiver=remote_subjects["receiver"],
                        )
                        report["evaluation_input"]["queue"] = derived_queue
                        if evaluation_input_owner_halt_path(paths).exists():
                            report["evaluation_input"]["run"] = {
                                "status": "halted",
                                "selected_capability_id": transport_row[
                                    "capability_id"
                                ],
                            }
                            return report
                        if not evaluation_input_owner_lease_valid():
                            report["evaluation_input"]["run"] = {
                                "status": "lock_lost",
                                "selected_capability_id": transport_row[
                                    "capability_id"
                                ],
                            }
                            report["errors"].append(
                                {
                                    "phase": "evaluation-input-run",
                                    "code": "writer-lock-lost",
                                }
                            )
                            report["ok"] = False
                            return report
                        promote_remote_evaluation_overlay(
                            evaluation_overlay,
                            Path(remote_subjects["overlay_store"]),
                            queue_evidence["census"],
                            usage,
                            queue_evidence["receiver"],
                            census_receipt_sha256=queue_evidence["summary"][
                                "receipt_sha256"
                            ],
                            usage_receipt_sha256=usage_summary[
                                "receipt_sha256"
                            ],
                            enabled_capability_ids={
                                row["capability_id"]
                                for row in derived_queue["rows"]
                            },
                            transport_receiver=remote_subjects["receiver"],
                        )
                        report["evaluation_input"]["remote_subjects"].update(
                            {
                                "overlay_sha256": evaluation_overlay[
                                    "overlay_sha256"
                                ],
                                "overlay_path": str(overlay_path),
                            }
                        )
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
    if not isinstance(queue, list) or any(not isinstance(item, dict) for item in queue):
        raise RuntimeFailure("queue-invalid", str(paths.queue))
    accounting_pass_id = (
        f"{core.parent_run_id}:{uuid.uuid4().hex}"
        if core.parent_run_id
        else f"manual:{uuid.uuid4().hex}"
    )
    queue_accounting: list[dict[str, Any]] = []
    profile_operations: list[dict[str, Any]] = []
    profile_accounting: list[dict[str, Any]] = []
    review_accounting: list[dict[str, Any]] = []
    review_operations: list[dict[str, Any]] = []
    queue_ids = {id(item): queue_row_identity(item) for item in queue}

    def account_queue(
        item: dict[str, Any], outcome: str, operation_id: str | None = None
    ) -> str:
        row_id = queue_ids[id(item)]
        row = {
            "queue_row_id": row_id,
            "outcome": outcome,
            "profile_operation_ids": (
                [operation_id] if operation_id is not None else []
            ),
        }
        queue_accounting.append(row)
        return row_id

    def link_profile_operation(
        item: dict[str, Any], operation_id: str, *, outcome: str
    ) -> None:
        row_id = queue_ids[id(item)]
        rows = [
            row for row in queue_accounting if row["queue_row_id"] == row_id
        ]
        if len(rows) != 1:
            raise RuntimeFailure(
                "task-pass-accounting-invalid", "queue-row-link"
            )
        row = rows[0]
        if operation_id in row["profile_operation_ids"]:
            raise RuntimeFailure(
                "task-pass-accounting-invalid", "duplicate-profile-operation"
            )
        row["profile_operation_ids"].append(operation_id)
        row["outcome"] = outcome

    def mark_queue_outcome(item: dict[str, Any], outcome: str) -> None:
        row_id = queue_ids[id(item)]
        rows = [
            row for row in queue_accounting if row["queue_row_id"] == row_id
        ]
        if len(rows) != 1:
            raise RuntimeFailure(
                "task-pass-accounting-invalid", "queue-row-outcome"
            )
        rows[0]["outcome"] = outcome

    def account_profiles(
        item: dict[str, Any],
        receipt: TaskProfileReceipt,
        *,
        forced_terminals: dict[str, str] | None = None,
    ) -> None:
        row_id = queue_ids[id(item)]
        existing_profile_ids = {
            row["profile_id"] for row in profile_accounting
        }
        for profile in receipt.payload["profiles"]:
            if profile["profile_id"] in existing_profile_ids:
                continue
            profile_accounting.append(
                {
                    "profile_id": profile["profile_id"],
                    "queue_row_id": row_id,
                    "profile_receipt_sha256": receipt.payload["receipt_sha256"],
                    "terminal": (forced_terminals or {}).get(
                        profile["profile_id"]
                    ) or (
                        "reusable-awaiting-review"
                        if profile["reuse_value"] == "reusable-procedure"
                        else "no-learning"
                    ),
                }
            )
            existing_profile_ids.add(profile["profile_id"])

    profile_attempts = 0
    profile_failed_sessions: set[str] = set()
    profile_boundary_terminal_profiles: dict[str, str] = {}
    profile_started_at = time.monotonic()
    for item in queue:
        status = item.get("status")
        if status != "queued":
            account_queue(
                item,
                (
                    "active-unsettled"
                    if status == "active"
                    else "stale-superseded"
                    if status == "superseded"
                    else "deleted"
                    if status == "deleted"
                    else "already-terminal"
                ),
            )
            continue
        source_name = item.get("source")
        source = sources.get(source_name)
        executor_name = next(
            (
                name
                for name in executor_order
                if (
                    name in executors
                    and (source_name, name) in routes
                    and executors[name].supports(TASK_PROFILE_CAPABILITY)
                )
            ),
            None,
        )
        if source is None:
            account_queue(item, "source-unavailable")
            report["errors"].append(
                {
                    "phase": "profile",
                    "session_id": item.get("qualified_session_id"),
                    "code": "source-not-configured",
                }
            )
            continue
        if executor_name is None:
            account_queue(item, "executor-unavailable")
            report["profile_skips"].append(
                {
                    "session_id": item.get("qualified_session_id"),
                    "code": "no-profile-capable-executor",
                }
            )
            continue
        try:
            current = source.call("inspect", session=item["qualified_session_id"])["session"]
            validate_identity(current, source_name)
        except RuntimeFailure as error:
            if (
                error.code == "session-missing"
                and error.message == item.get("qualified_session_id")
            ):
                core._mark_queue(
                    item["qualified_session_id"], item["source_revision"], "deleted"
                )
                account_queue(item, "deleted")
                report["profiles"].append(
                    {"session_id": item["qualified_session_id"], "status": "deleted"}
                )
            else:
                account_queue(item, "source-unavailable")
                report["errors"].append(
                    {
                        "phase": "profile",
                        "session_id": item.get("qualified_session_id"),
                        "code": error.code,
                    }
                )
            continue
        if current["source_revision"] != item["source_revision"]:
            core._mark_queue(
                item["qualified_session_id"], item["source_revision"], "superseded"
            )
            core._admit(current)
            account_queue(item, "stale-superseded")
            report["profiles"].append(
                {
                    "session_id": item["qualified_session_id"],
                    "status": "stale-before-profile",
                    "queued_revision": current["source_revision"],
                }
            )
            continue
        if current["completion_state"] not in COMPLETED:
            core._admit(current)
            account_queue(item, "active-unsettled")
            report["profile_skips"].append(
                {
                    "session_id": item["qualified_session_id"],
                    "code": "completion-not-admitted",
                }
            )
            continue
        try:
            existing_receipt = core.indexed_task_profile_receipt_for(
                item["qualified_session_id"], item["source_revision"], executor_name
            )
        except RuntimeFailure as error:
            account_queue(item, "malformed")
            report["profile_failures"].append(
                {
                    "session_id": item["qualified_session_id"],
                    "code": error.code,
                    "message": error.message,
                }
            )
            continue
        if (
            existing_receipt is not None
            and existing_receipt.payload.get("schema_version") == 2
            and compatible_task_profile_executor_identities(
            existing_receipt.payload.get("executor_identity"),
            executors[executor_name].identity,
            )
        ):
            conflicts = core.task_occurrence_conflicts_for(existing_receipt)
            if conflicts:
                prior_correction = core.task_occurrence_correction_attempt_for(
                    conflicts[0]
                )
                if prior_correction is not None:
                    prior_terminal = (
                        "boundary-correction-failed"
                        if prior_correction["terminal_status"] == "failed"
                        else "boundary-unresolved"
                    )
                    account_queue(item, "cached-current-receipt")
                    account_profiles(
                        item,
                        existing_receipt,
                        forced_terminals={
                            conflict["profile_id"]: prior_terminal
                            for conflict in conflicts
                        },
                    )
                    for conflict in conflicts:
                        profile_boundary_terminal_profiles[
                            conflict["profile_id"]
                        ] = prior_terminal
                    report["profile_skips"].append(
                        {
                            "session_id": item["qualified_session_id"],
                            "code": "boundary-correction-already-terminal",
                        }
                    )
                    continue
                budget_reason = profile_budget_reason(
                    profile_attempts, profile_started_at, settings
                )
                if budget_reason is not None:
                    account_queue(item, "eligible-deferred")
                    report["profile_budget"]["exhausted_reason"] = budget_reason
                    continue
                row_id = queue_ids[id(item)]
                operation_id = f"profile:{accounting_pass_id}:{row_id}"
                correction_started = False

                def record_correction_start() -> None:
                    nonlocal profile_attempts, correction_started
                    bound = profile_budget_reason(
                        profile_attempts, profile_started_at, settings
                    )
                    if bound is not None:
                        raise RuntimeFailure("profile-operation-bound", bound)
                    profile_attempts += 1
                    correction_started = True

                try:
                    correction = core.correct_task_occurrence_conflict(
                        source_name,
                        source,
                        existing_receipt,
                        conflicts[0],
                        executor_name,
                        executors[executor_name],
                        on_profile_operation_start=record_correction_start,
                    )
                    terminal = (
                        "profiled"
                        if correction["status"] == "replacement-profiled"
                        else "correction-unresolved"
                        if correction["status"] == "boundary-unresolved"
                        else "failed"
                    )
                    if correction_started:
                        profile_operations.append(
                            {
                                "operation_id": operation_id,
                                "queue_row_id": row_id,
                                "terminal": terminal,
                            }
                        )
                    account_queue(
                        item,
                        (
                            "newly-attempted"
                            if correction_started
                            else "profile-failed"
                        ),
                        operation_id if correction_started else None,
                    )
                    replacement = core.indexed_task_profile_receipt_for(
                        item["qualified_session_id"],
                        item["source_revision"],
                        executor_name,
                    )
                    if terminal == "profiled" and replacement is not None:
                        account_profiles(item, replacement)
                    elif terminal == "correction-unresolved":
                        account_profiles(
                            item,
                            existing_receipt,
                            forced_terminals={
                                conflict["profile_id"]:
                                "boundary-unresolved"
                                for conflict in conflicts
                            },
                        )
                        for conflict in conflicts:
                            profile_boundary_terminal_profiles[
                                conflict["profile_id"]
                            ] = "boundary-unresolved"
                    else:
                        profile_failed_sessions.add(
                            item["qualified_session_id"]
                        )
                    report["profiles"].append(
                        {
                            "session_id": item["qualified_session_id"],
                            "correction": True,
                            **correction,
                        }
                    )
                except RuntimeFailure as error:
                    if error.code == "profile-operation-bound":
                        account_queue(item, "eligible-deferred")
                        report["profile_budget"]["exhausted_reason"] = error.message
                        continue
                    if correction_started:
                        profile_operations.append(
                            {
                                "operation_id": operation_id,
                                "queue_row_id": row_id,
                                "terminal": "failed",
                            }
                        )
                    account_queue(
                        item,
                        "profile-failed",
                        operation_id if correction_started else None,
                    )
                    profile_failed_sessions.add(item["qualified_session_id"])
                    report["profile_failures"].append(
                        {
                            "session_id": item["qualified_session_id"],
                            "code": error.code,
                            "message": error.message,
                        }
                    )
                continue
            account_queue(item, "cached-current-receipt")
            account_profiles(item, existing_receipt)
            report["profile_skips"].append(
                {
                    "session_id": item["qualified_session_id"],
                    "code": "cached-current-receipt",
                }
            )
            continue
        budget_reason = profile_budget_reason(
            profile_attempts, profile_started_at, settings
        )
        if budget_reason is not None:
            account_queue(item, "eligible-deferred")
            report["profile_budget"]["exhausted_reason"] = budget_reason
            continue
        row_id = queue_ids[id(item)]
        operation_id = f"profile:{accounting_pass_id}:{row_id}"
        profile_started = False

        def record_profile_start() -> None:
            nonlocal profile_attempts, profile_started
            bound = profile_budget_reason(
                profile_attempts, profile_started_at, settings
            )
            if bound is not None:
                raise RuntimeFailure("profile-operation-bound", bound)
            profile_attempts += 1
            profile_started = True

        try:
            result = core.profile(
                source_name,
                source,
                item["qualified_session_id"],
                executor_name,
                executors[executor_name],
                expected_revision=item["source_revision"],
                on_profile_operation_start=record_profile_start,
            )
            terminal = (
                "deleted"
                if result["status"] == "deleted"
                else "stale"
                if result["status"] == "stale-before-profile"
                else "profiled"
            )
            receipt = None
            receipt_error = None
            if terminal == "profiled":
                try:
                    receipt = core.indexed_task_profile_receipt_for(
                        item["qualified_session_id"],
                        item["source_revision"],
                        executor_name,
                    )
                    if receipt is None:
                        raise RuntimeFailure(
                            "task-profile-index-invalid", "new receipt is absent"
                        )
                except RuntimeFailure as error:
                    receipt_error = error
            if profile_started:
                profile_operations.append(
                    {
                        "operation_id": operation_id,
                        "queue_row_id": row_id,
                        "terminal": terminal,
                    }
                )
            account_queue(
                item,
                "deleted" if terminal == "deleted" else
                "stale-superseded" if terminal == "stale" else "newly-attempted",
                operation_id if profile_started else None,
            )
            report["profiles"].append({"session_id": item["qualified_session_id"], **result})
            if receipt_error is not None:
                profile_failed_sessions.add(item["qualified_session_id"])
                report["profile_failures"].append(
                    {
                        "session_id": item["qualified_session_id"],
                        "code": receipt_error.code,
                        "message": receipt_error.message,
                    }
                )
                continue
            if receipt is not None:
                account_profiles(item, receipt)
        except RuntimeFailure as error:
            if error.code == "profile-operation-bound":
                account_queue(item, "eligible-deferred")
                report["profile_budget"]["exhausted_reason"] = error.message
                continue
            terminal = (
                "malformed"
                if error.code in {"malformed-executor-result", "task-profile-invalid"}
                else "failed"
            )
            if profile_started:
                profile_operations.append(
                    {
                        "operation_id": operation_id,
                        "queue_row_id": row_id,
                        "terminal": terminal,
                    }
                )
            account_queue(
                item,
                "malformed" if terminal == "malformed" else "profile-failed",
                operation_id if profile_started else None,
            )
            profile_failed_sessions.add(item["qualified_session_id"])
            report["profile_failures"].append(
                {
                    "session_id": item["qualified_session_id"],
                    "code": error.code,
                    "message": error.message,
                }
            )
    report["deferred_profiles"] = sum(
        row["outcome"] == "eligible-deferred" for row in queue_accounting
    )
    report["profile_budget"]["attempts"] = profile_attempts
    report["profile_budget"]["elapsed_seconds"] = int(time.monotonic() - profile_started_at)
    profile_queue_outcome = {
        row["queue_row_id"]: row["outcome"] for row in queue_accounting
    }
    profile_review_candidates: list[
        tuple[
            str,
            ExecutableAdapter,
            str,
            str,
            ExecutableAdapter,
            ProfileAuditTarget,
            dict[str, Any],
        ]
    ] = []
    profile_audit_revisions: dict[
        tuple[str, str], list[ProfileAuditTarget]
    ] = {}

    def account_review(
        item: dict[str, Any],
        profile_id: str | None,
        outcome: str,
        profile_receipt_sha256: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "review_row_id": digest(
                {
                    "queue_row_id": queue_ids[id(item)],
                    "profile_id": profile_id,
                    "profile_receipt_sha256": profile_receipt_sha256,
                    "position": len(review_accounting),
                }
            ),
            "queue_row_id": queue_ids[id(item)],
            "profile_id": profile_id,
            "profile_receipt_sha256": profile_receipt_sha256,
            "outcome": outcome,
            "operation_id": None,
        }
        review_accounting.append(row)
        return row

    for item in queue:
        if item.get("status") != "queued":
            account_review(
                item,
                None,
                (
                    "already-dispositioned"
                    if item.get("status") == "profile-audited"
                    else "stale-superseded"
                ),
            )
            continue
        if profile_queue_outcome[queue_ids[id(item)]] in {
            "eligible-deferred",
            "malformed",
            "profile-failed",
            "source-unavailable",
            "executor-unavailable",
            "active-unsettled",
            "deleted",
        }:
            account_review(item, None, "raw-unprofiled")
            report["profile_review_skips"].append(
                {
                    "session_id": item["qualified_session_id"],
                    "code": "profile-unavailable",
                }
            )
            continue
        if item.get("qualified_session_id") in profile_failed_sessions:
            account_review(item, None, "raw-unprofiled")
            report["profile_review_skips"].append(
                {
                    "session_id": item["qualified_session_id"],
                    "code": "profile-unavailable",
                }
            )
            continue
        source_name = item.get("source")
        source = sources.get(source_name)
        if source is None:
            account_review(item, None, "invalid-unbound")
            report["profile_review_skips"].append(
                {
                    "session_id": item.get("qualified_session_id"),
                    "code": "source-not-configured",
                }
            )
            continue
        executor_name = next(
            (
                name
                for name in executor_order
                if (
                    name in executors
                    and (source_name, name) in routes
                    and executors[name].supports(TASK_PROFILE_CAPABILITY)
                )
            ),
            None,
        )
        if executor_name is None:
            account_review(item, None, "invalid-unbound")
            report["profile_review_skips"].append(
                {
                    "session_id": item["qualified_session_id"],
                    "code": "no-profile-capable-executor",
                }
            )
            continue
        try:
            targets = core.profile_audit_targets_for(
                source_name,
                source,
                item["qualified_session_id"],
                item["source_revision"],
                executor_name,
                executors[executor_name],
            )
        except RuntimeFailure as error:
            account_review(
                item,
                None,
                "stale-superseded"
                if error.code == "profile-audit-stale"
                else "invalid-unbound",
            )
            report["profile_review_skips"].append(
                {
                    "session_id": item.get("qualified_session_id"),
                    "code": error.code,
                }
            )
            continue
        if not targets:
            account_review(item, None, "no-learning")
            report["profile_review_skips"].append(
                {
                    "session_id": item["qualified_session_id"],
                    "code": "no-reusable-profile",
                }
            )
            profile_audit_revisions[
                (item["qualified_session_id"], item["source_revision"])
            ] = targets
            continue
        profile_audit_revisions[
            (item["qualified_session_id"], item["source_revision"])
        ] = targets
        for target in targets:
            boundary_terminal = profile_boundary_terminal_profiles.get(
                target.profile["profile_id"]
            )
            if boundary_terminal is not None:
                account_review(
                    item,
                    target.profile["profile_id"],
                    boundary_terminal,
                    target.receipt.payload["receipt_sha256"],
                )
                report["profile_review_skips"].append(
                    {
                        "session_id": item["qualified_session_id"],
                        "profile_id": target.profile["profile_id"],
                        "code": "boundary-correction-already-terminal",
                    }
                )
                continue
            try:
                disposition_admission, disposition = (
                    core.profile_audit_disposition_admission_for(target)
                )
            except RuntimeFailure as error:
                account_review(
                    item,
                    target.profile["profile_id"],
                    "invalid-unbound",
                    target.receipt.payload["receipt_sha256"],
                )
                report["profile_review_skips"].append(
                    {
                        "session_id": item["qualified_session_id"],
                        "profile_id": target.profile["profile_id"],
                        "code": error.code,
                    }
                )
                continue
            if disposition_admission == "terminal" and disposition is not None:
                account_review(
                    item,
                    target.profile["profile_id"],
                    "already-dispositioned",
                    target.receipt.payload["receipt_sha256"],
                )
                for profile_row in profile_accounting:
                    if profile_row["profile_id"] == target.profile["profile_id"]:
                        profile_row["terminal"] = "reusable-dispositioned"
                report["profile_review_skips"].append(
                    {
                        "session_id": item["qualified_session_id"],
                        "profile_id": target.profile["profile_id"],
                        "code": "already-dispositioned",
                    }
                )
                continue
            if (
                disposition_admission
                == "superseded-requires-repair-backfill"
                and disposition is not None
            ):
                account_review(
                    item,
                    target.profile["profile_id"],
                    "known-superseded-contract",
                    target.receipt.payload["receipt_sha256"],
                )
                report["profile_review_skips"].append(
                    {
                        "session_id": item["qualified_session_id"],
                        "profile_id": target.profile["profile_id"],
                        "code": (
                            "profile-audit-disposition-superseded"
                            "-requires-repair-backfill"
                        ),
                    }
                )
                continue
            if disposition_admission in {
                "boundary-conflict",
                "boundary-unresolved",
            }:
                account_review(
                    item,
                    target.profile["profile_id"],
                    disposition_admission,
                    target.receipt.payload["receipt_sha256"],
                )
                report["profile_review_skips"].append(
                    {
                        "session_id": item["qualified_session_id"],
                        "profile_id": target.profile["profile_id"],
                        "code": disposition_admission,
                    }
                )
                continue
            profile_review_candidates.append(
                (
                    source_name,
                    source,
                    item["qualified_session_id"],
                    item["source_revision"],
                    executors[executor_name],
                    target,
                    account_review(
                        item,
                        target.profile["profile_id"],
                        "newly-attempted",
                        target.receipt.payload["receipt_sha256"],
                    ),
                )
            )
    queue_item_by_row_id = {
        queue_ids[id(item)]: item for item in queue
    }
    superseded_profile_receipts: set[str] = set()
    for (
        source_name,
        source,
        qualified_session_id,
        source_revision,
        executor,
        target,
        review_row,
    ) in profile_review_candidates:
        if (
            target.receipt.payload["receipt_sha256"]
            in superseded_profile_receipts
        ):
            review_row["outcome"] = "stale-superseded"
            report["profile_review_skips"].append(
                {
                    "session_id": qualified_session_id,
                    "profile_id": target.profile["profile_id"],
                    "code": "profile-receipt-superseded-by-correction",
                }
            )
            continue
        if (
            report["review_budget"]["started_operations"]
            >= settings["max_reviews_per_run"]
        ):
            review_row["outcome"] = "eligible-deferred"
            continue
        executor_id = target.receipt.payload["executor"]
        started_before = report["review_budget"]["started_operations"]

        def record_review_start() -> None:
            report["review_budget"]["started_operations"] += 1

        try:
            result = core.review_profile(
                source_name,
                source,
                qualified_session_id,
                source_revision,
                executor_id,
                executor,
                target.profile["profile_id"],
                on_review_operation_start=record_review_start,
            )
            if (
                result["status"]
                == "profile-audit-disposition-superseded-requires-repair-backfill"
            ):
                review_row["outcome"] = "known-superseded-contract"
                report["profile_review_skips"].append(
                    {
                        "session_id": qualified_session_id,
                        "profile_id": target.profile["profile_id"],
                        "code": result["status"],
                    }
                )
            elif result["status"] == "already-dispositioned":
                review_row["outcome"] = "already-dispositioned"
                report["profile_review_skips"].append(
                    {
                        "session_id": qualified_session_id,
                        "profile_id": target.profile["profile_id"],
                        "code": "already-dispositioned",
                    }
                )
            elif result["status"] == "exact-task-reused":
                review_row["outcome"] = "already-dispositioned"
                for profile_row in profile_accounting:
                    if profile_row["profile_id"] == target.profile["profile_id"]:
                        profile_row["terminal"] = "reusable-dispositioned"
                report["profile_review_skips"].append(
                    {
                        "session_id": qualified_session_id,
                        "profile_id": target.profile["profile_id"],
                        "code": "exact-task-reused",
                    }
                )
            elif result["status"] == "stale-before-review":
                review_row["outcome"] = "stale-superseded"
                report["profile_review_skips"].append(
                    {
                        "session_id": qualified_session_id,
                        "profile_id": target.profile["profile_id"],
                        "code": result["status"],
                    }
                )
            elif result["status"] == "deleted":
                review_row["outcome"] = "deleted"
                report["profile_review_skips"].append(
                    {
                        "session_id": qualified_session_id,
                        "profile_id": target.profile["profile_id"],
                        "code": result["status"],
                    }
                )
            elif (
                report["review_budget"]["started_operations"] == started_before
                and result["status"]
                not in {"boundary-conflict", "boundary-unresolved"}
            ):
                review_row["outcome"] = "invalid-unbound"
                report["profile_review_skips"].append(
                    {
                        "session_id": qualified_session_id,
                        "profile_id": target.profile["profile_id"],
                        "code": result["status"],
                    }
                )
            else:
                review_row["outcome"] = (
                    result["status"]
                    if result["status"]
                    in {"boundary-conflict", "boundary-unresolved"}
                    else "newly-attempted"
                )
                if report["review_budget"]["started_operations"] > started_before:
                    operation_id = (
                        f"review:{accounting_pass_id}:{review_row['review_row_id']}"
                    )
                    review_row["operation_id"] = operation_id
                    review_operations.append(
                        {
                            "operation_id": operation_id,
                            "profile_id": target.profile["profile_id"],
                            "profile_receipt_sha256": target.receipt.payload[
                                "receipt_sha256"
                            ],
                            "terminal": (
                                "boundary-conflict"
                                if result["status"] == "boundary-conflict"
                                else "recurrence-ready"
                                if (
                                    isinstance(
                                        result.get("policy_deferred"), dict
                                    )
                                    and isinstance(
                                        result["policy_deferred"].get(
                                            "shadow_candidate"
                                        ),
                                        dict,
                                    )
                                    and result["policy_deferred"][
                                        "shadow_candidate"
                                    ].get("recommendation")
                                    == "ready_for_draft"
                                )
                                else "recurrence-waiting"
                                if (
                                    isinstance(
                                        result.get("policy_deferred"), dict
                                    )
                                    and isinstance(
                                        result["policy_deferred"].get(
                                            "shadow_candidate"
                                        ),
                                        dict,
                                    )
                                )
                                else "dispositioned"
                            ),
                        }
                    )
                    for profile_row in profile_accounting:
                        if profile_row["profile_id"] == target.profile["profile_id"]:
                            profile_row["terminal"] = (
                                "boundary-conflict"
                                if result["status"] == "boundary-conflict"
                                else "reusable-dispositioned"
                            )
                report["reviews"].append(
                    {
                        "session_id": qualified_session_id,
                        "profile_id": target.profile["profile_id"],
                        **result,
                    }
                )
                if result["status"] == "boundary-conflict":
                    conflict = core.task_occurrence_resolution_for(target)
                    if conflict is None:
                        raise RuntimeFailure(
                            "task-occurrence-resolution-invalid",
                            "conflict-resolution-missing",
                        )
                    item = queue_item_by_row_id[review_row["queue_row_id"]]
                    profile_bound = profile_budget_reason(
                        profile_attempts, profile_started_at, settings
                    )
                    if profile_bound is not None:
                        mark_queue_outcome(item, "eligible-deferred")
                        report["profile_budget"]["exhausted_reason"] = (
                            profile_bound
                        )
                        continue
                    correction_operation_id = (
                        f"profile-correction:{accounting_pass_id}:"
                        f"{review_row['queue_row_id']}:"
                        f"{conflict['resolution_sha256'][7:23]}"
                    )
                    correction_started = False

                    def record_correction_start() -> None:
                        nonlocal profile_attempts, correction_started
                        bound = profile_budget_reason(
                            profile_attempts, profile_started_at, settings
                        )
                        if bound is not None:
                            raise RuntimeFailure(
                                "profile-operation-bound", bound
                            )
                        profile_attempts += 1
                        correction_started = True

                    try:
                        correction = core.correct_task_occurrence_conflict(
                            source_name,
                            source,
                            target.receipt,
                            conflict,
                            executor_id,
                            executor,
                            on_profile_operation_start=record_correction_start,
                        )
                        if correction_started:
                            correction_terminal = (
                                "profiled"
                                if correction["status"]
                                == "replacement-profiled"
                                else "correction-unresolved"
                                if correction["status"]
                                == "boundary-unresolved"
                                else "failed"
                            )
                            profile_operations.append(
                                {
                                    "operation_id": correction_operation_id,
                                    "queue_row_id": review_row["queue_row_id"],
                                    "terminal": correction_terminal,
                                }
                            )
                            link_profile_operation(
                                item,
                                correction_operation_id,
                                outcome="newly-attempted",
                            )
                        report["profiles"].append(
                            {
                                "session_id": qualified_session_id,
                                "correction": True,
                                **correction,
                            }
                        )
                        if correction["status"] == "replacement-profiled":
                            superseded_profile_receipts.add(
                                target.receipt.payload["receipt_sha256"]
                            )
                            replacement = core.indexed_task_profile_receipt_for(
                                qualified_session_id,
                                source_revision,
                                executor_id,
                            )
                            if replacement is None:
                                raise RuntimeFailure(
                                    "task-occurrence-correction-invalid",
                                    "replacement-receipt-missing",
                                )
                            account_profiles(item, replacement)
                            replacement_targets = (
                                core.profile_audit_targets_for(
                                    source_name,
                                    source,
                                    qualified_session_id,
                                    source_revision,
                                    executor_id,
                                    executor,
                                )
                            )
                            profile_audit_revisions[
                                (qualified_session_id, source_revision)
                            ] = replacement_targets
                            existing_candidate_keys = {
                                (
                                    candidate[5].profile["profile_id"],
                                    candidate[5].receipt.payload[
                                        "receipt_sha256"
                                    ],
                                )
                                for candidate in profile_review_candidates
                            }
                            for replacement_target in replacement_targets:
                                if (
                                    (
                                        replacement_target.profile[
                                            "profile_id"
                                        ],
                                        replacement_target.receipt.payload[
                                            "receipt_sha256"
                                        ],
                                    )
                                    in existing_candidate_keys
                                ):
                                    continue
                                profile_review_candidates.append(
                                    (
                                        source_name,
                                        source,
                                        qualified_session_id,
                                        source_revision,
                                        executor,
                                        replacement_target,
                                        account_review(
                                            item,
                                            replacement_target.profile[
                                                "profile_id"
                                            ],
                                            "newly-attempted",
                                            replacement.payload[
                                                "receipt_sha256"
                                            ],
                                        ),
                                    )
                                )
                        elif correction["status"] == "boundary-unresolved":
                            for profile_row in profile_accounting:
                                if (
                                    profile_row["profile_id"]
                                    == target.profile["profile_id"]
                                ):
                                    profile_row["terminal"] = (
                                        "boundary-unresolved"
                                    )
                    except RuntimeFailure as error:
                        if error.code == "profile-operation-bound":
                            mark_queue_outcome(item, "eligible-deferred")
                            report["profile_budget"]["exhausted_reason"] = (
                                error.message
                            )
                            continue
                        if correction_started:
                            profile_operations.append(
                                {
                                    "operation_id": correction_operation_id,
                                    "queue_row_id": review_row["queue_row_id"],
                                    "terminal": "failed",
                                }
                            )
                            link_profile_operation(
                                item,
                                correction_operation_id,
                                outcome="newly-attempted",
                            )
                        report["errors"].append(
                            {
                                "phase": "profile-correction",
                                "session_id": qualified_session_id,
                                "profile_id": target.profile["profile_id"],
                                "code": error.code,
                            }
                        )
        except RuntimeFailure as error:
            started = report["review_budget"]["started_operations"] > started_before
            if started:
                operation_id = (
                    f"review:{accounting_pass_id}:{review_row['review_row_id']}"
                )
                review_row["operation_id"] = operation_id
                review_operations.append(
                    {
                        "operation_id": operation_id,
                        "profile_id": target.profile["profile_id"],
                        "profile_receipt_sha256": target.receipt.payload[
                            "receipt_sha256"
                        ],
                        "terminal": (
                            "malformed"
                            if error.code
                            in {"malformed-executor-result", "task-profile-invalid"}
                            else "stale"
                            if error.code == "profile-audit-stale"
                            else "failed"
                        ),
                    }
                )
            elif error.code == "profile-audit-stale":
                review_row["outcome"] = "stale-superseded"
            elif error.code == "session-missing":
                review_row["outcome"] = "deleted"
            else:
                review_row["outcome"] = "invalid-unbound"
            destination = report["errors"] if started else report["profile_review_skips"]
            record = {
                "session_id": qualified_session_id,
                "profile_id": target.profile["profile_id"],
                "code": error.code,
            }
            if destination is report["errors"]:
                destination.append({"phase": "review", **record})
            else:
                destination.append(record)
    for (qualified_session_id, source_revision), targets in (
        profile_audit_revisions.items()
    ):
        try:
            core.mark_profile_audit_queue_terminal(
                qualified_session_id, source_revision, targets
            )
        except RuntimeFailure as error:
            report["profile_review_skips"].append(
                {
                    "session_id": qualified_session_id,
                    "code": error.code,
                }
            )
    report["deferred_reviews"] = sum(
        row["outcome"] == "eligible-deferred" for row in review_accounting
    )
    report["deferred_profiles"] = sum(
        row["outcome"] == "eligible-deferred" for row in queue_accounting
    )
    report["profile_budget"]["attempts"] = profile_attempts
    report["profile_budget"]["elapsed_seconds"] = int(
        time.monotonic() - profile_started_at
    )
    profile_stop_reason = (
        report["profile_budget"]["exhausted_reason"] or "eligible-exhausted"
    )
    review_stop_reason = (
        "review-operation-limit"
        if report["deferred_reviews"]
        else "eligible-exhausted"
    )
    accounting_receipt = build_task_pass_accounting_receipt(
        pass_id=accounting_pass_id,
        queue_rows=queue_accounting,
        profile_operations=profile_operations,
        profiles=profile_accounting,
        review_rows=review_accounting,
        review_operations=review_operations,
        review_terminals=[dict(row) for row in review_operations],
        profile_stop_reason=profile_stop_reason,
        review_stop_reason=review_stop_reason,
    )
    try:
        validate_task_pass_accounting_receipt(accounting_receipt)
        receipt_path = (
            paths.task_pass_accounting_receipts
            / f"{accounting_receipt['receipt_sha256'].removeprefix('sha256:')}.json"
        )
        if receipt_path.exists():
            if read_json(receipt_path, {}) != accounting_receipt:
                raise RuntimeFailure("task-pass-accounting-collision", str(receipt_path))
        else:
            atomic_json(receipt_path, accounting_receipt, mode=0o400)
        report["accounting"] = {
            "schema_version": 2,
            "status": "reconciled",
            "receipt": str(receipt_path),
            "receipt_sha256": accounting_receipt["receipt_sha256"],
            "queue_terminal_accounting": queue_accounting,
            "profile_operation_accounting": profile_operations,
            "profile_terminal_accounting": profile_accounting,
            "review_eligibility_accounting": review_accounting,
            "review_operation_accounting": review_operations,
            "review_terminal_accounting": [dict(row) for row in review_operations],
            "eligible_backlog": {
                "profiles": report["deferred_profiles"],
                "reviews": report["deferred_reviews"],
            },
            "unused_capacity": {
                "profiles": (
                    settings["max_profiles_per_run"] - profile_attempts
                    if profile_stop_reason == "eligible-exhausted"
                    else 0
                ),
                "reviews": (
                    settings["max_reviews_per_run"]
                    - report["review_budget"]["started_operations"]
                    if review_stop_reason == "eligible-exhausted"
                    else 0
                ),
            },
            "stop_reason": {
                "profiles": profile_stop_reason,
                "reviews": review_stop_reason,
            },
        }
    except (RuntimeFailure, TaskPassAccountingError) as error:
        code = (
            error.code
            if isinstance(error, RuntimeFailure)
            else f"task-pass-accounting-invalid:{error.reason}"
        )
        report["accounting"]["status"] = "unhealthy"
        report["accounting"]["stop_reason"] = {
            "profiles": profile_stop_reason,
            "reviews": review_stop_reason,
        }
        report["errors"].append({"phase": "accounting", "code": code})
    try:
        routing = core.derive_evaluation_routing()
        report["evaluation_routing"] = {
            "schema_version": routing["schema_version"],
            "status": routing["status"],
            "summary": routing["summary"],
            "rows": routing["rows"],
        }
    except RuntimeFailure as error:
        report["evaluation_routing"] = {
            "schema_version": 1,
            "status": "refused",
            "code": error.code,
            "summary": None,
            "rows": [],
        }
        report["errors"].append(
            {"phase": "evaluation-routing", "code": error.code}
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
    report["ok"] = not report["errors"] and not report["profile_failures"]
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
    core = configured_runtime(paths, routes, config)
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


def profile_session(
    source_name: str,
    qualified_session_id: str,
    executor_name: str | None,
) -> dict[str, Any]:
    paths = default_paths()
    config = load_adapter_config(default_adapter_config(paths))
    adapters, _reports, errors = configured_adapters_tolerant(config)
    source = adapters["session-source"].get(source_name)
    if source is None:
        relevant = [
            error
            for error in errors
            if error.get("role") == "session-source"
            and error.get("adapter") == source_name
        ]
        code = relevant[0]["code"] if relevant else "source-not-configured"
        raise RuntimeFailure(str(code), source_name)
    executors = adapters["review-executor"]
    routes = configured_routes(config)
    if executor_name is None:
        candidates = sorted(
            name
            for name in executors
            if (
                (source_name, name) in routes
                and executors[name].supports(TASK_PROFILE_CAPABILITY)
            )
        )
        if not candidates:
            raise RuntimeFailure(
                "no-profile-capable-executor", source_name
            )
        executor_name = candidates[0]
    executor = executors.get(executor_name)
    if executor is None:
        relevant = [
            error
            for error in errors
            if error.get("role") == "review-executor"
            and error.get("adapter") == executor_name
        ]
        code = relevant[0]["code"] if relevant else "executor-not-configured"
        raise RuntimeFailure(str(code), executor_name)
    core = configured_runtime(paths, routes, config)
    result = core.profile(
        source_name,
        source,
        qualified_session_id,
        executor_name,
        executor,
    )
    return {
        "ok": True,
        "runtime": "dreaming-core",
        "command": "profile",
        "source": source_name,
        "session_id": qualified_session_id,
        "executor": executor_name,
        **result,
    }


def collect_profile_candidate(
    receipt_path: Path,
    profile_id: str,
    package: Path,
    proposed_name: str,
) -> dict[str, Any]:
    paths = default_paths()
    core = DreamingRuntime(paths, [])
    result = core.collect_profile_candidate(
        receipt_path,
        profile_id,
        package,
        proposed_name,
    )
    return {
        "ok": True,
        "runtime": "dreaming-core",
        "command": "collect-profile-candidate",
        "receipt": str(receipt_path),
        "profile_id": profile_id,
        "proposed_name": proposed_name,
        **result,
    }


def evaluation_routing() -> dict[str, Any]:
    paths = default_paths()
    core = DreamingRuntime(paths, [])
    return {
        "ok": True,
        "runtime": "dreaming-core",
        "command": "evaluation-routing",
        **core.derive_evaluation_routing(),
    }


def handoff_evaluation(lifecycle_id: str) -> dict[str, Any]:
    paths = default_paths()
    core = DreamingRuntime(paths, [])
    return {
        "ok": True,
        "runtime": "dreaming-core",
        "command": "handoff-evaluation",
        **core.handoff_candidate_for_evaluation(lifecycle_id),
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
    profile = subcommands.add_parser("profile")
    profile.add_argument("--source", required=True)
    profile.add_argument("--session", required=True)
    profile.add_argument("--executor")
    collect_profile = subcommands.add_parser("collect-profile-candidate")
    collect_profile.add_argument("--receipt", required=True)
    collect_profile.add_argument("--profile-id", required=True)
    collect_profile.add_argument("--package", required=True)
    collect_profile.add_argument("--proposed-name", required=True)
    subcommands.add_parser("evaluation-routing")
    handoff = subcommands.add_parser("handoff-evaluation")
    handoff.add_argument("--lifecycle-id", required=True)
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
        elif args.command == "profile":
            report = profile_session(
                args.source,
                args.session,
                args.executor,
            )
        elif args.command == "collect-profile-candidate":
            report = collect_profile_candidate(
                Path(args.receipt),
                args.profile_id,
                Path(args.package),
                args.proposed_name,
            )
        elif args.command == "evaluation-routing":
            report = evaluation_routing()
        elif args.command == "handoff-evaluation":
            report = handoff_evaluation(args.lifecycle_id)
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
