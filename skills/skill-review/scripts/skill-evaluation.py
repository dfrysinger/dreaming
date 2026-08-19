#!/usr/bin/env python3
"""Prepare, score, and verify source/sibling skill evaluations."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pwd
import re
import signal
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from evaluation_input_claims import (  # noqa: E402
    SLOT_DEFINITIONS as CLAIM_SLOT_DEFINITIONS,
    ClaimLedgerError,
    acknowledge_terminal_publication,
    assert_ready as assert_claim_ready,
    complete_claim_ready,
    complete_slot as complete_claim_slot,
    fail_dispatched_slot,
    inspect_claim,
    ledger_path as claim_ledger_path,
    open_scheduled_claims,
    pending_terminal_publications,
    prepare_dispatch as prepare_claim_dispatch,
    recover_open_scheduled_claim,
    reserve_claim,
    review_set_identity,
)

SCHEMA_VERSION = 1
SUITE_SCHEMA_VERSION = 2
POLICY_SCHEMA_VERSION = 2
AUTHORITY_SCHEMA_VERSION = 3
RUNNER_VERSION = "skill-evaluation-runner-1"
PROMPT_VERSION = "skill-evaluation-prompt-1"
COMPARATOR_VERSION = "skill-evaluation-comparator-1"
CASE_FILE = ".skill-evaluation-cases.json"
POLICY_FILE = ".skill-evaluation-policy.json"
LOCAL_SIDECARS = {
    ".agent-created",
    ".agent-created.json",
    ".promotion-reviewed.json",
    CASE_FILE,
    POLICY_FILE,
    ".pinned",
}
STATUS_ALLOWLIST = {"pass", "waived"}
WAIVER_CLASSES = {"documentation-only", "reference-only", "deterministic-helper"}
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CASE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
GRADER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
EXECUTOR_NAMES = ("copilot", "claude", "codex")
CASE_CLASSES = {
    "intended",
    "related",
    "activation_positive",
    "activation_negative",
}
POLICY_KINDS = {"capability_uplift", "encoded_preference"}
PROFILES = {"gate", "iterate"}
CERTIFICATE_STATUSES = {"pass", "regression", "inconclusive", "unavailable"}
HARNESS_CONTRACT_VERSION = 1
COMPILATION_SCHEMA_VERSION = 1
INPUT_REGISTRY_SCHEMA_VERSION = 1
AUTHORING_CATALOG_SCHEMA_VERSION = 1
AUTHORING_PACKET_SCHEMA_VERSION = 1
TRUSTED_MODEL_ENVIRONMENT_VERSION = 1
TRUSTED_AUTHORING_ADAPTER_SHA256 = (
    "sha256:8679d5fb613194abe57164d2fdfdb19d23057d2d92289c8f6d6515ac41857648"
)
AUTHORING_FIXTURE_SOURCE_KINDS = {"public", "synthetic"}
AUTHORING_MAX_SKILL_CONTRACT_BYTES = 131_072
AUTHORING_MAX_FIXTURE_BYTES = 1_048_576
AUTHORING_MAX_DESCRIPTION_BYTES = 4_096
AUTHORING_DENIED_TEXT_PATTERNS = (
    (re.compile(r"(?i)(?:^|[/\\])(?:users|home)[/\\][^/\\\s]+"), "home path"),
    (re.compile(r"(?i)(?:^|[/\\])\.copilot[/\\]session-state(?:[/\\]|$)"), "session state"),
    (re.compile(r"(?i)\b(?:raw[-_ ]?)?transcript(?:s)?\b"), "transcript"),
    (re.compile(r"(?i)\bdashboard (?:snapshot|export)\b"), "dashboard snapshot"),
    (re.compile(r"(?i)\buser disposition(?:s)?\b"), "user disposition"),
    (
        re.compile(
            r"(?i)\b(?:password|passwd|api[-_ ]?key|access[-_ ]?token|"
            r"refresh[-_ ]?token|client[-_ ]?secret|private[-_ ]?key)"
            r"\s*[:=]\s*[^\s,;]{4,}"
        ),
        "credential value",
    ),
    (
        re.compile(
            r"\b(?:(?:ghp|github_pat)_[A-Za-z0-9_-]{12,}|"
            r"sk-[A-Za-z0-9][A-Za-z0-9_-]{15,})\b"
        ),
        "token",
    ),
    (re.compile(r"(?i)\bauthorization:\s*bearer\s+\S+"), "bearer credential"),
)
INPUT_REGISTRY_REQUIRED_ROLES = {
    "suite",
    "policy",
    "compilation",
    "routing",
    "harness",
}
INPUT_AUTHORING_OBJECT_ROLES = {
    "authoring_packet",
    "authoring_draft",
    "authoring_receipt",
    "authoring_operation",
    "authoring_adapter",
}
INPUT_REPAIR_OBJECT_ROLES = {
    "repair_packet",
    "repair_draft",
    "repair_operation",
    "repair_adapter",
}
INPUT_REGISTRY_OPTIONAL_ROLES = (
    INPUT_AUTHORING_OBJECT_ROLES | INPUT_REPAIR_OBJECT_ROLES
)
INPUT_REVIEW_OBJECT_ROLES = {
    "input_review_packet",
    "input_review_adapter",
}
INPUT_READINESS_STATES = {
    "input_missing",
    "drafting",
    "review_required",
    "invalid",
    "insufficient_information",
    "ready",
}
INPUT_READINESS_REASONS = {
    "input_missing": {"no_external_manifest"},
    "drafting": {"authoring_claimed"},
    "review_required": {"validation_passed"},
    "invalid": {
        "deterministic_validation_failed",
        "independent_review_rejected",
        "independent_rereview_rejected",
        "authoring_budget_exhausted",
    },
    "insufficient_information": {
        "evaluation_case_unavailable",
        "safe_fixture_unavailable",
        "objective_grader_unavailable",
    },
    "ready": {"validated_and_reviewed"},
}
INPUT_READINESS_TRANSITIONS = {
    "input_missing": {"drafting"},
    "drafting": {
        "review_required",
        "invalid",
        "insufficient_information",
    },
    "review_required": {
        "review_required",
        "invalid",
        "insufficient_information",
        "ready",
    },
    "invalid": {"drafting", "review_required", "invalid", "ready"},
    "insufficient_information": set(),
    "ready": {"ready"},
}
RESULT_MANIFEST_KEYS = {
    "schema_version",
    "kind",
    "state",
    "collection_state",
    "executor_states",
    "input_run_id",
    "invocation_nonce",
    "harness_version",
    "harness_executable_sha256",
    "profile",
    "candidate_id",
    "suite_id",
    "grader_set_id",
    "trials",
    "pairs",
    "executor_identities",
    "comparator_identity",
    "producer_audit",
    "file_inventory",
    "result_id",
}


class EvaluationError(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def shadow_canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{field} must be non-empty text")
    return value


def resolve_path(path: Path, field: str) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError) as exc:
        raise EvaluationError(f"cannot resolve {field} {path}: {exc}") from exc


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvaluationError(f"{path} must contain a JSON object")
    return value


def atomic_write(path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
            os.fsync(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    finally:
        os.close(directory_fd)


def inventory(skill_dir: Path, destination: Path | None = None) -> list[dict[str, Any]]:
    if not (skill_dir / "SKILL.md").is_file():
        raise EvaluationError(f"missing SKILL.md in {skill_dir}")
    files: list[dict[str, Any]] = []
    for path in sorted(skill_dir.rglob("*")):
        relative = path.relative_to(skill_dir).as_posix()
        if path.is_symlink():
            raise EvaluationError(f"{relative}: symlinks are not valid evaluation inputs")
        if path.is_dir():
            continue
        if not path.is_file():
            raise EvaluationError(f"{relative}: runtime input must be a regular file")
        if relative in LOCAL_SIDECARS:
            continue
        if path.name in LOCAL_SIDECARS:
            raise EvaluationError(f"{relative}: reserved evaluation sidecar must be at skill root")
        content = path.read_bytes()
        files.append({"path": relative, "sha256": digest(content), "size": len(content)})
        if destination is not None:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            os.chmod(target, path.stat().st_mode & 0o777)
    return files


def candidate_id(skill_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    files = inventory(skill_dir)
    return f"sha256:{digest(canonical(files))}", files


def validate_patterns(value: Any, field: str, required: bool = False) -> list[dict[str, str]]:
    if not isinstance(value, list) or (required and not value):
        raise EvaluationError(f"{field} must be {'a non-empty' if required else 'an'} list")
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise EvaluationError(f"{field}[{index}] must be an object")
        item_id = require_text(item.get("id"), f"{field}[{index}].id")
        pattern = require_text(item.get("pattern"), f"{field}[{index}].pattern")
        if item_id in seen:
            raise EvaluationError(f"{field} has duplicate id {item_id}")
        seen.add(item_id)
        try:
            re.compile(pattern, re.MULTILINE)
        except re.error as exc:
            raise EvaluationError(f"{field}[{index}].pattern is invalid: {exc}") from exc
        result.append({"id": item_id, "pattern": pattern})
    return result


def validate_case(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError(f"{field} must be an object")
    task_id = require_text(value.get("task_id"), f"{field}.task_id")
    if not TASK_ID_RE.fullmatch(task_id):
        raise EvaluationError(f"{field}.task_id is invalid")
    return {
        "task_id": task_id,
        "prompt": require_text(value.get("prompt"), f"{field}.prompt"),
        "required_regex": validate_patterns(
            value.get("required_regex"), f"{field}.required_regex", required=True
        ),
        "forbidden_regex": validate_patterns(
            value.get("forbidden_regex", []), f"{field}.forbidden_regex"
        ),
        "friction_regex": validate_patterns(
            value.get("friction_regex", []), f"{field}.friction_regex"
        ),
    }


def load_cases(path: Path) -> tuple[dict[str, Any], str]:
    raw = load_json(path)
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise EvaluationError(f"case manifest schema_version must be {SCHEMA_VERSION}")
    value = {
        "schema_version": SCHEMA_VERSION,
        "source": validate_case(raw.get("source"), "source"),
        "sibling": validate_case(raw.get("sibling"), "sibling"),
    }
    if value["source"]["task_id"] == value["sibling"]["task_id"]:
        raise EvaluationError("source and sibling task_id values must differ")
    return value, digest(canonical(value))


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise EvaluationError(f"{field} must be a sha256 identity")
    return value


def require_nonnegative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EvaluationError(f"{field} must be a non-negative integer")
    return value


def require_positive_int(value: Any, field: str) -> int:
    value = require_nonnegative_int(value, field)
    if value == 0:
        raise EvaluationError(f"{field} must be a positive integer")
    return value


def sha256_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise EvaluationError(f"{path} must be a regular executable file")
    return f"sha256:{digest(path.read_bytes())}"


def trusted_harness_path() -> Path:
    path = Path(__file__).resolve().with_name("skill-evaluation-harness.py")
    if not path.is_file() or path.is_symlink():
        raise EvaluationError("reviewed Dreaming harness executable is unavailable")
    return path


def trusted_authoring_adapter_path() -> Path:
    evaluator = Path(__file__).resolve()
    if not evaluator.is_file() or evaluator.is_symlink():
        raise EvaluationError("trusted evaluator executable is unavailable")
    path = evaluator.with_name("dreaming-vendor-adapter.py")
    if not path.is_file() or path.is_symlink():
        raise EvaluationError("trusted authoring adapter is unavailable")
    if sha256_file(path) != TRUSTED_AUTHORING_ADAPTER_SHA256:
        raise EvaluationError(
            "trusted authoring adapter bytes differ from the reviewed identity"
        )
    return path


def require_trusted_harness(path: Path) -> Path:
    resolved = path.resolve()
    trusted = trusted_harness_path()
    if resolved != trusted:
        raise EvaluationError("selected harness is not the reviewed Dreaming harness executable")
    return trusted


def canonical_file_inventory(root: Path, exclude: set[str] | None = None) -> list[dict[str, Any]]:
    excluded = exclude or set()
    result: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        if path.is_symlink():
            raise EvaluationError(f"{relative}: symlinks are forbidden in sealed bundles")
        if path.is_dir():
            continue
        if not path.is_file():
            raise EvaluationError(f"{relative}: sealed input must be a regular file")
        content = path.read_bytes()
        result.append(
            {
                "path": relative,
                "sha256": f"sha256:{digest(content)}",
                "size": len(content),
            }
        )
    return result


def require_exact_keys(value: dict[str, Any], field: str, keys: set[str]) -> None:
    unknown = sorted(set(value) - keys)
    missing = sorted(keys - set(value))
    if unknown or missing:
        details = []
        if unknown:
            details.append(f"unknown keys {unknown}")
        if missing:
            details.append(f"missing keys {missing}")
        raise EvaluationError(f"{field} has {'; '.join(details)}")


def require_authoring_safe_text(value: Any, field: str, *, maximum: int) -> str:
    text = require_text(value, field)
    encoded = text.encode("utf-8")
    if len(encoded) > maximum:
        raise EvaluationError(f"{field} exceeds the authoring size limit")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in text):
        raise EvaluationError(f"{field} contains unsupported control characters")
    for pattern, label in AUTHORING_DENIED_TEXT_PATTERNS:
        if pattern.search(text):
            raise EvaluationError(f"{field} contains forbidden {label} content")
    return text


def reject_authoring_sensitive_bytes(
    content: bytes, field: str, *, allow_contract_paths: bool = False
) -> None:
    sample = content.decode("utf-8", errors="ignore")
    for pattern, label in AUTHORING_DENIED_TEXT_PATTERNS:
        if allow_contract_paths and label in {"home path", "session state"}:
            continue
        if pattern.search(sample):
            raise EvaluationError(f"{field} contains forbidden {label} content")


def reject_authoring_sensitive_value(value: Any, field: str) -> None:
    encoded = canonical(value)
    if len(encoded) > AUTHORING_MAX_FIXTURE_BYTES:
        raise EvaluationError(f"{field} exceeds the authoring contract size limit")

    def visit(item: Any, item_field: str) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                visit(child, f"{item_field}.{key}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{item_field}[{index}]")
        elif isinstance(item, str):
            if len(item.encode("utf-8")) > AUTHORING_MAX_SKILL_CONTRACT_BYTES:
                raise EvaluationError(f"{item_field} exceeds the authoring text size limit")
            if any(
                ord(character) < 32 and character not in "\n\r\t"
                for character in item
            ):
                raise EvaluationError(
                    f"{item_field} contains unsupported control characters"
                )
            reject_authoring_sensitive_bytes(item.encode("utf-8"), item_field)

    visit(value, field)


def validate_suite_grader(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError(f"{field} must be an object")
    require_exact_keys(value, field, {"id", "type", "safety", "identity"})
    grader_id = require_text(value.get("id"), f"{field}.id")
    if not GRADER_ID_RE.fullmatch(grader_id):
        raise EvaluationError(f"{field}.id is invalid")
    grader_type = require_text(value.get("type"), f"{field}.type")
    if grader_type not in {"regex", "json_schema", "file", "command", "trace", "numeric"}:
        raise EvaluationError(f"{field}.type is unsupported")
    safety = value.get("safety")
    if not isinstance(safety, bool):
        raise EvaluationError(f"{field}.safety must be a boolean")
    identity = require_sha256(value.get("identity"), f"{field}.identity")
    return {"id": grader_id, "type": grader_type, "safety": safety, "identity": identity}


def validate_suite_case(value: Any, field: str, graders: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError(f"{field} must be an object")
    case_id = require_text(value.get("id"), f"{field}.id")
    if not CASE_ID_RE.fullmatch(case_id):
        raise EvaluationError(f"{field}.id is invalid")
    case_class = value.get("class")
    if case_class not in CASE_CLASSES:
        raise EvaluationError(f"{field}.class is invalid")
    task_id = require_text(value.get("task_id"), f"{field}.task_id")
    if not TASK_ID_RE.fullmatch(task_id):
        raise EvaluationError(f"{field}.task_id is invalid")
    references = value.get("deterministic_graders")
    if not isinstance(references, list) or not references:
        raise EvaluationError(f"{field}.deterministic_graders must be a non-empty list")
    if not all(isinstance(reference, str) for reference in references):
        raise EvaluationError(f"{field}.deterministic_graders must contain grader IDs")
    if len(set(references)) != len(references):
        raise EvaluationError(f"{field}.deterministic_graders has duplicate grader IDs")
    missing = sorted(set(references) - set(graders))
    if missing:
        raise EvaluationError(f"{field}.deterministic_graders references unknown graders {missing}")
    if not any(graders[reference]["safety"] for reference in references):
        raise EvaluationError(f"{field} must reference a deterministic safety grader")
    allowed = {"id", "class", "task_id", "prompt", "deterministic_graders"}
    if case_class.startswith("activation_"):
        allowed.add("activation")
    require_exact_keys(value, field, allowed)
    result = {
        "id": case_id,
        "class": case_class,
        "task_id": task_id,
        "prompt": require_text(value.get("prompt"), f"{field}.prompt"),
        "deterministic_graders": references,
    }
    if case_class.startswith("activation_"):
        trigger = value.get("activation")
        if not isinstance(trigger, dict):
            raise EvaluationError(f"{field}.activation must be an object")
        require_exact_keys(trigger, f"{field}.activation", {"expected_load"})
        result["activation"] = {
            "expected_load": trigger.get("expected_load"),
        }
        if result["activation"]["expected_load"] != (case_class == "activation_positive"):
            raise EvaluationError(f"{field}.activation.expected_load does not match case class")
    elif "activation" in value:
        raise EvaluationError(f"{field}.activation is valid only for activation cases")
    return result


def compile_legacy_cases(raw: dict[str, Any]) -> dict[str, Any]:
    """Compile M2 source/sibling input without inventing M5 activation authority."""
    source = validate_case(raw.get("source"), "source")
    sibling = validate_case(raw.get("sibling"), "sibling")
    if source["task_id"] == sibling["task_id"]:
        raise EvaluationError("source and sibling task_id values must differ")
    grader_id = "legacy-regex"
    legacy_grader = {
        "id": grader_id,
        "type": "regex",
        "safety": True,
        "identity": f"sha256:{digest(canonical({'version': 1, 'kind': 'legacy-regex'}))}",
    }
    cases = []
    for case_id, case_class, case in (
        ("legacy-intended", "intended", source),
        ("legacy-related", "related", sibling),
    ):
        cases.append(
            {
                "id": case_id,
                "class": case_class,
                "task_id": case["task_id"],
                "prompt": case["prompt"],
                "deterministic_graders": [grader_id],
            }
        )
    suite = {
        "schema_version": SUITE_SCHEMA_VERSION,
        "compiled_from_schema_version": 1,
        "cross_executor_authority": False,
        "graders": [legacy_grader],
        "cases": cases,
    }
    return suite


def load_suite(path: Path) -> tuple[dict[str, Any], str]:
    raw = load_json(path)
    schema_version = raw.get("schema_version")
    normalized = "compiled_from_schema_version" in raw or "cross_executor_authority" in raw
    if normalized and (
        not isinstance(schema_version, int)
        or schema_version != SUITE_SCHEMA_VERSION
    ):
        raise EvaluationError("normalized suite schema_version must be integer 2")
    if schema_version == 1:
        suite = compile_legacy_cases(raw)
        return suite, f"sha256:{digest(canonical(suite))}"
    if schema_version != SUITE_SCHEMA_VERSION:
        raise EvaluationError("suite schema_version must be 1 or 2")
    keys = {"schema_version", "graders", "cases"}
    if normalized:
        keys.update({"compiled_from_schema_version", "cross_executor_authority"})
    require_exact_keys(raw, "suite", keys)
    if normalized:
        if raw.get("compiled_from_schema_version") is not None:
            raise EvaluationError("normalized version-2 suite has invalid source schema")
        if raw.get("cross_executor_authority") is not True:
            raise EvaluationError("normalized version-2 suite must retain cross-executor authority")
    graders_value = raw.get("graders")
    if not isinstance(graders_value, list) or not graders_value:
        raise EvaluationError("suite.graders must be a non-empty list")
    graders: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(graders_value):
        validated = validate_suite_grader(item, f"suite.graders[{index}]")
        if validated["id"] in graders:
            raise EvaluationError(f"suite.graders has duplicate id {validated['id']}")
        graders[validated["id"]] = validated
    cases_value = raw.get("cases")
    if not isinstance(cases_value, list) or not cases_value:
        raise EvaluationError("suite.cases must be a non-empty list")
    cases: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    task_ids: set[str] = set()
    classes: set[str] = set()
    for index, item in enumerate(cases_value):
        validated = validate_suite_case(item, f"suite.cases[{index}]", graders)
        if validated["id"] in case_ids:
            raise EvaluationError(f"suite.cases has duplicate id {validated['id']}")
        if validated["task_id"] in task_ids:
            raise EvaluationError(
                f"suite.cases task_id {validated['task_id']} is shared; task identities must be independent"
            )
        case_ids.add(validated["id"])
        task_ids.add(validated["task_id"])
        classes.add(validated["class"])
        cases.append(validated)
    missing_classes = sorted(CASE_CLASSES - classes)
    if missing_classes:
        raise EvaluationError(f"suite is missing required case classes {missing_classes}")
    suite = {
        "schema_version": SUITE_SCHEMA_VERSION,
        "compiled_from_schema_version": None,
        "cross_executor_authority": True,
        "graders": [graders[item["id"]] for item in graders_value],
        "cases": cases,
    }
    return suite, f"sha256:{digest(canonical(suite))}"


def validate_executor(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError(f"{field} must be an object")
    require_exact_keys(
        value,
        field,
        {
            "name",
            "model",
            "adapter_id",
            "adapter_version",
            "adapter_executable_sha256",
            "cli_executable_sha256",
        },
    )
    name = value.get("name")
    if name not in EXECUTOR_NAMES:
        raise EvaluationError(f"{field}.name must be one of {', '.join(EXECUTOR_NAMES)}")
    adapter_version = value.get("adapter_version")
    if not isinstance(adapter_version, int) or isinstance(adapter_version, bool) or adapter_version < 1:
        raise EvaluationError(f"{field}.adapter_version must be a positive integer")
    return {
        "name": name,
        "model": require_text(value.get("model"), f"{field}.model"),
        "adapter_id": require_sha256(value.get("adapter_id"), f"{field}.adapter_id"),
        "adapter_version": adapter_version,
        "adapter_executable_sha256": require_sha256(
            value.get("adapter_executable_sha256"), f"{field}.adapter_executable_sha256"
        ),
        "cli_executable_sha256": require_sha256(
            value.get("cli_executable_sha256"), f"{field}.cli_executable_sha256"
        ),
    }


def load_policy(path: Path) -> tuple[dict[str, Any], str]:
    raw = load_json(path)
    if raw.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise EvaluationError(f"policy schema_version must be {POLICY_SCHEMA_VERSION}")
    normalized = "trials_per_arm" in raw
    keys = {
        "schema_version",
        "profile",
        "policy_kind",
        "required_executors",
        "advisory_executors",
        "comparator",
    }
    if normalized:
        keys.add("trials_per_arm")
    require_exact_keys(raw, "policy", keys)
    profile = raw.get("profile")
    if profile not in PROFILES:
        raise EvaluationError("policy.profile must be gate or iterate")
    trials_per_arm = 3 if profile == "gate" else 1
    retained_trials = raw.get("trials_per_arm")
    if normalized and (
        not isinstance(retained_trials, int)
        or isinstance(retained_trials, bool)
        or retained_trials != trials_per_arm
    ):
        raise EvaluationError(
            "normalized policy trials_per_arm must be the profile-derived integer"
        )
    policy_kind = raw.get("policy_kind")
    if policy_kind not in POLICY_KINDS:
        raise EvaluationError("policy.policy_kind must be capability_uplift or encoded_preference")
    required_value = raw.get("required_executors")
    if not isinstance(required_value, list) or not required_value:
        raise EvaluationError("policy.required_executors must be a non-empty ordered list")
    required_executors = [
        validate_executor(item, f"policy.required_executors[{index}]")
        for index, item in enumerate(required_value)
    ]
    advisory_value = raw.get("advisory_executors")
    if not isinstance(advisory_value, list):
        raise EvaluationError("policy.advisory_executors must be an ordered list")
    advisory_executors = [
        validate_executor(item, f"policy.advisory_executors[{index}]")
        for index, item in enumerate(advisory_value)
    ]
    for field, executors in (
        ("required_executors", required_executors),
        ("advisory_executors", advisory_executors),
    ):
        names = [executor["name"] for executor in executors]
        if len(set(names)) != len(names):
            raise EvaluationError(f"policy.{field} cannot repeat an executor")
        canonical_order = [name for name in EXECUTOR_NAMES if name in names]
        if names != canonical_order:
            raise EvaluationError(f"policy.{field} must follow copilot, claude, codex order")
    required_names = {executor["name"] for executor in required_executors}
    advisory_names = {executor["name"] for executor in advisory_executors}
    if required_names & advisory_names:
        raise EvaluationError("policy required and advisory executors must be disjoint")
    comparator_value = raw.get("comparator")
    if not isinstance(comparator_value, dict):
        raise EvaluationError("policy.comparator must be an object")
    require_exact_keys(
        comparator_value,
        "policy.comparator",
        {
            "route",
            "model",
            "adapter_id",
            "adapter_version",
            "adapter_executable_sha256",
            "timeout_seconds",
            "token_budget",
            "rubric_id",
        },
    )
    comparator = {
        "route": require_text(comparator_value.get("route"), "policy.comparator.route"),
        "model": require_text(comparator_value.get("model"), "policy.comparator.model"),
        "adapter_id": require_sha256(comparator_value.get("adapter_id"), "policy.comparator.adapter_id"),
        "adapter_version": comparator_value.get("adapter_version"),
        "adapter_executable_sha256": require_sha256(
            comparator_value.get("adapter_executable_sha256"),
            "policy.comparator.adapter_executable_sha256",
        ),
        "timeout_seconds": require_nonnegative_int(
            comparator_value.get("timeout_seconds"), "policy.comparator.timeout_seconds"
        ),
        "token_budget": require_nonnegative_int(
            comparator_value.get("token_budget"), "policy.comparator.token_budget"
        ),
        "rubric_id": require_sha256(comparator_value.get("rubric_id"), "policy.comparator.rubric_id"),
    }
    if not isinstance(comparator["adapter_version"], int) or isinstance(comparator["adapter_version"], bool) or comparator["adapter_version"] < 1:
        raise EvaluationError("policy.comparator.adapter_version must be a positive integer")
    policy = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "profile": profile,
        "trials_per_arm": trials_per_arm,
        "policy_kind": policy_kind,
        "required_executors": required_executors,
        "advisory_executors": advisory_executors,
        "comparator": comparator,
    }
    return policy, policy_identity(policy)


def policy_identity(policy: dict[str, Any]) -> str:
    authority_policy = {
        key: policy[key]
        for key in (
            "schema_version",
            "profile",
            "trials_per_arm",
            "policy_kind",
            "required_executors",
            "comparator",
        )
    }
    return f"sha256:{digest(canonical(authority_policy))}"


def observation_plan_identity(policy: dict[str, Any], policy_id: str) -> str:
    return f"sha256:{digest(canonical({
        'schema_version': POLICY_SCHEMA_VERSION,
        'policy_id': policy_id,
        'advisory_executors': policy['advisory_executors'],
    }))}"


def cli_version(copilot: str) -> str:
    result = subprocess.run(
        [copilot, "--version"], check=True, capture_output=True, text=True, timeout=30
    )
    return require_text(result.stdout.strip(), "Copilot CLI version")


def runtime_contract(candidate: str, cases_sha: str, model: str, cli: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate,
        "case_manifest_sha256": cases_sha,
        "model": model,
        "cli_version": cli,
        "runner_version": RUNNER_VERSION,
        "prompt_version": PROMPT_VERSION,
        "comparator_version": COMPARATOR_VERSION,
        "flags": [
            "--effort=low",
            "--available-tools=skill,view",
            "--allow-tool=skill,view",
            "--no-custom-instructions",
            "--disable-builtin-mcps",
            "--disallow-temp-dir",
            "--no-remote",
            "--no-color",
            "--output-format=json",
            "--log-level=error",
        ],
        "working_directory": "fresh-empty-directory",
        "baseline_plugin": None,
        "candidate_plugin": "immutable-candidate-snapshot",
    }


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    run_dir = Path(args.run_dir).resolve()
    plugin = Path(args.plugin_dir).resolve()
    cases_path = Path(args.cases or skill_dir / CASE_FILE).resolve()
    if args.model == "auto":
        raise EvaluationError("evaluation requires an explicit non-auto model")
    cases, cases_sha = load_cases(cases_path)
    name_match = re.search(
        r"(?m)^name:\s*([a-z0-9][a-z0-9-]*)\s*$",
        (skill_dir / "SKILL.md").read_text(encoding="utf-8"),
    )
    if not name_match:
        raise EvaluationError("SKILL.md must contain a simple kebab-case name")
    name = name_match.group(1)
    destination = plugin / "skills" / name
    destination.mkdir(parents=True, exist_ok=True)
    files = inventory(skill_dir, destination)
    current_id = f"sha256:{digest(canonical(files))}"
    copilot = os.environ.get("COPILOT_BIN", str(Path.home() / ".local/bin/copilot"))
    contract = runtime_contract(current_id, cases_sha, args.model, cli_version(copilot))
    run_id = f"sha256:{digest(canonical(contract))}"
    atomic_write(
        plugin / ".claude-plugin" / "plugin.json",
        {
            "name": "skill-evaluation-candidate",
            "version": "0.0.0",
            "skills": [f"./skills/{name}"],
        },
        mode=0o644,
    )
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "skill": name,
        "skill_path": str(skill_dir),
        "cases_path": str(cases_path),
        "candidate_inventory": files,
        "candidate_id": current_id,
        "case_manifest_sha256": cases_sha,
        "source_case_id": f"sha256:{digest(canonical(cases['source']))}",
        "sibling_case_id": f"sha256:{digest(canonical(cases['sibling']))}",
        "run_id": run_id,
        "runtime": contract,
        "cases": cases,
    }
    atomic_write(run_dir / "metadata.json", metadata)
    for case_name in ("source", "sibling"):
        (run_dir / f"{case_name}.prompt").write_text(
            cases[case_name]["prompt"].rstrip()
            + "\n\nAnswer the task directly. Do not discuss this evaluation.\n",
            encoding="utf-8",
        )
    return metadata


def parse_run(path: Path, skill: str, require_skill: bool, expected_model: str) -> dict[str, Any]:
    messages: list[str] = []
    result_event: dict[str, Any] | None = None
    loaded = False
    models: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return {"valid": False, "error": str(exc)}
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return {"valid": False, "error": "non-JSON output in structured run log"}
        event_type = event.get("type")
        data = event.get("data", {})
        if not isinstance(data, dict):
            return {"valid": False, "error": f"{event_type} data must be an object"}
        if event_type == "assistant.message" and isinstance(data.get("content"), str):
            if isinstance(data.get("model"), str):
                models.add(data["model"])
            if data["content"].strip():
                messages.append(data["content"])
        elif event_type == "tool.execution_start":
            if isinstance(data.get("model"), str):
                models.add(data["model"])
            arguments = data.get("arguments")
            loaded = (
                loaded
                or data.get("toolName") == "skill"
                and isinstance(arguments, dict)
                and arguments.get("skill") == skill
            )
        elif event_type == "result":
            result_event = event
    if result_event is None or result_event.get("exitCode") != 0:
        return {"valid": False, "error": "missing successful result event"}
    if not messages:
        return {"valid": False, "error": "missing final assistant message"}
    if models != {expected_model}:
        return {
            "valid": False,
            "error": f"run used model identities {sorted(models)!r}, expected {expected_model!r}",
        }
    if loaded != require_skill:
        expected = "load" if require_skill else "not load"
        return {"valid": False, "error": f"candidate skill must {expected} in this run"}
    return {"valid": True, "answer": messages[-1], "skill_loaded": loaded}


def score(run: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    if not run.get("valid"):
        return {**run, "passed": False, "friction_count": None}
    answer = run["answer"]
    required = {
        item["id"]: bool(re.search(item["pattern"], answer, re.MULTILINE))
        for item in case["required_regex"]
    }
    forbidden = {
        item["id"]: len(re.findall(item["pattern"], answer, re.MULTILINE))
        for item in case["forbidden_regex"]
    }
    friction = {
        item["id"]: len(re.findall(item["pattern"], answer, re.MULTILINE))
        for item in case["friction_regex"]
    }
    return {
        **run,
        "required": required,
        "forbidden": forbidden,
        "friction": friction,
        "passed": all(required.values()) and not any(forbidden.values()),
        "friction_count": sum(friction.values()),
    }


def evaluation_dir() -> Path:
    root = Path(os.environ.get("SKILLS_STATE_DIR", str(Path.home() / ".copilot/skill-state")))
    return root / "skill-review" / "evaluations"


def latest_key(skill_path: str) -> str:
    return digest(str(Path(skill_path).resolve()).encode())


def write_receipt(receipt: dict[str, Any]) -> tuple[Path, str]:
    receipt_bytes = canonical(receipt)
    receipt_sha = digest(receipt_bytes)
    path = evaluation_dir() / "receipts" / f"{receipt_sha}.json"
    if path.exists():
        existing = load_json(path)
        if canonical(existing) != receipt_bytes:
            raise EvaluationError("content-addressed receipt collision")
    else:
        atomic_write(path, receipt)
    atomic_write(
        evaluation_dir() / "latest" / f"{latest_key(receipt['skill_path'])}.json",
        {
            "schema_version": SCHEMA_VERSION,
            "skill_path": receipt["skill_path"],
            "receipt_sha256": receipt_sha,
            "receipt_path": str(path),
        },
    )
    return path, receipt_sha


def update_envelope(skill_dir: Path, receipt_path: Path) -> None:
    envelope = skill_dir / ".agent-created.json"
    if not envelope.exists():
        return
    helper = Path(__file__).with_name("evidence-envelope.py")
    subprocess.run(
        [str(helper), "set-evaluation", str(envelope), str(receipt_path)],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def finalize(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir).resolve()
    metadata = load_json(run_dir / "metadata.json")
    cases = metadata["cases"]
    runs = {
        "source_baseline": score(
            parse_run(
                run_dir / "source-baseline.jsonl",
                metadata["skill"],
                False,
                metadata["runtime"]["model"],
            ),
            cases["source"],
        ),
        "source_candidate": score(
            parse_run(
                run_dir / "source-candidate.jsonl",
                metadata["skill"],
                True,
                metadata["runtime"]["model"],
            ),
            cases["source"],
        ),
        "sibling_baseline": score(
            parse_run(
                run_dir / "sibling-baseline.jsonl",
                metadata["skill"],
                False,
                metadata["runtime"]["model"],
            ),
            cases["sibling"],
        ),
        "sibling_candidate": score(
            parse_run(
                run_dir / "sibling-candidate.jsonl",
                metadata["skill"],
                True,
                metadata["runtime"]["model"],
            ),
            cases["sibling"],
        ),
    }
    valid = all(run["valid"] for run in runs.values())
    source_before = runs["source_baseline"]
    source_after = runs["source_candidate"]
    sibling_before = runs["sibling_baseline"]
    sibling_after = runs["sibling_candidate"]
    source_improved = bool(valid and not source_before["passed"] and source_after["passed"])
    sibling_regressed = bool(
        valid
        and sibling_before["passed"]
        and (
            not sibling_after["passed"]
            or sibling_after["friction_count"] > sibling_before["friction_count"]
        )
    )
    if valid and (not source_after["passed"] or sibling_regressed):
        status = "regression"
    elif valid and sibling_before["passed"] and source_improved and not sibling_regressed:
        status = "pass"
    else:
        status = "inconclusive"
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": "evaluation",
        "status": status,
        "evaluated_at": now_iso(),
        "skill": metadata["skill"],
        "skill_path": metadata["skill_path"],
        "candidate_id": metadata["candidate_id"],
        "candidate_inventory": metadata["candidate_inventory"],
        "run_id": metadata["run_id"],
        "case_manifest_sha256": metadata["case_manifest_sha256"],
        "cases_path": metadata["cases_path"],
        "source_case_id": metadata["source_case_id"],
        "sibling_case_id": metadata["sibling_case_id"],
        "runtime": metadata["runtime"],
        "source_improved": source_improved,
        "sibling_regressed": sibling_regressed,
        "runs": runs,
    }
    receipt_path, receipt_sha = write_receipt(receipt)
    update_envelope(Path(metadata["skill_path"]), receipt_path)
    return {"status": status, "receipt": str(receipt_path), "receipt_sha256": receipt_sha}


def verify_receipt_bytes(pointer: dict[str, Any]) -> tuple[dict[str, Any], str]:
    path = Path(require_text(pointer.get("receipt_path"), "receipt_path"))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EvaluationError(f"cannot read receipt: {exc}") from exc
    receipt_sha = digest(canonical(json.loads(raw)))
    if receipt_sha != pointer.get("receipt_sha256") or path.name != f"{receipt_sha}.json":
        raise EvaluationError("receipt hash or content-addressed path does not match")
    receipt = json.loads(raw)
    if not isinstance(receipt, dict):
        raise EvaluationError("receipt must be a JSON object")
    return receipt, receipt_sha


def gate(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    pointer_path = evaluation_dir() / "latest" / f"{latest_key(str(skill_dir))}.json"
    pointer = load_json(pointer_path)
    if pointer.get("skill_path") != str(skill_dir):
        raise EvaluationError("latest evaluation pointer belongs to another skill path")
    receipt, receipt_sha = verify_receipt_bytes(pointer)
    if receipt.get("status") not in STATUS_ALLOWLIST:
        raise EvaluationError(f"evaluation status {receipt.get('status')!r} is not allowed")
    current_id, _ = candidate_id(skill_dir)
    if receipt.get("candidate_id") != current_id:
        raise EvaluationError("evaluation is stale for the current candidate inventory")
    if receipt.get("kind") == "evaluation":
        cases, cases_sha = load_cases(Path(receipt["cases_path"]))
        if cases_sha != receipt.get("case_manifest_sha256"):
            raise EvaluationError("evaluation case manifest changed after the run")
        expected = runtime_contract(
            current_id,
            cases_sha,
            receipt["runtime"]["model"],
            receipt["runtime"]["cli_version"],
        )
        if receipt.get("runtime") != expected:
            raise EvaluationError("evaluation runtime contract is malformed")
        if receipt.get("run_id") != f"sha256:{digest(canonical(expected))}":
            raise EvaluationError("evaluation run_id does not match runtime inputs")
        if receipt.get("source_case_id") != f"sha256:{digest(canonical(cases['source']))}":
            raise EvaluationError("source case identity changed")
        if receipt.get("sibling_case_id") != f"sha256:{digest(canonical(cases['sibling']))}":
            raise EvaluationError("sibling case identity changed")
    elif receipt.get("kind") == "waiver":
        anchor_sha = require_text(
            receipt.get("waived_from_receipt_sha256"),
            "waived_from_receipt_sha256",
        )
        anchor_pointer = {
            "receipt_path": str(evaluation_dir() / "receipts" / f"{anchor_sha}.json"),
            "receipt_sha256": anchor_sha,
        }
        anchor, _ = verify_receipt_bytes(anchor_pointer)
        verify_evaluation_anchor(anchor, skill_dir)
        if (
            receipt.get("base_candidate_id") != anchor.get("candidate_id")
            or receipt.get("base_run_id") != anchor.get("run_id")
        ):
            raise EvaluationError("waiver does not bind its passing evaluation")
    else:
        raise EvaluationError("receipt kind is invalid")
    envelope_path = skill_dir / ".agent-created.json"
    if envelope_path.exists():
        envelope = load_json(envelope_path)
        evaluation = envelope.get("evaluation", {})
        if (
            evaluation.get("status") != receipt.get("status")
            or evaluation.get("candidate_id") != current_id
            or evaluation.get("receipt_sha256") != receipt_sha
        ):
            raise EvaluationError("evidence envelope does not mirror the current receipt")
    return {"status": receipt["status"], "candidate_id": current_id, "receipt_sha256": receipt_sha}


def verify_evaluation_anchor(receipt: dict[str, Any], skill_dir: Path) -> None:
    if (
        receipt.get("kind") != "evaluation"
        or receipt.get("status") != "pass"
        or receipt.get("skill_path") != str(skill_dir)
    ):
        raise EvaluationError("waiver anchor must be a passing evaluation for this skill")
    files = receipt.get("candidate_inventory")
    if not isinstance(files, list):
        raise EvaluationError("waiver anchor is missing candidate inventory")
    if receipt.get("candidate_id") != f"sha256:{digest(canonical(files))}":
        raise EvaluationError("waiver anchor candidate inventory is malformed")
    cases, cases_sha = load_cases(Path(receipt["cases_path"]))
    expected = runtime_contract(
        receipt["candidate_id"],
        cases_sha,
        receipt["runtime"]["model"],
        receipt["runtime"]["cli_version"],
    )
    if (
        receipt.get("case_manifest_sha256") != cases_sha
        or receipt.get("runtime") != expected
        or receipt.get("run_id") != f"sha256:{digest(canonical(expected))}"
        or receipt.get("source_case_id")
        != f"sha256:{digest(canonical(cases['source']))}"
        or receipt.get("sibling_case_id")
        != f"sha256:{digest(canonical(cases['sibling']))}"
    ):
        raise EvaluationError("waiver anchor evaluation contract is stale or malformed")


def waive(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    base_path = Path(args.base_receipt).resolve()
    if base_path.parent != (evaluation_dir() / "receipts").resolve():
        raise EvaluationError("base receipt must come from the evaluation receipt store")
    base_sha = base_path.stem
    base, verified_base_sha = verify_receipt_bytes(
        {"receipt_path": str(base_path), "receipt_sha256": base_sha}
    )
    verify_evaluation_anchor(base, skill_dir)
    current_id, current_files = candidate_id(skill_dir)
    before = {item["path"]: item for item in base["candidate_inventory"]}
    after = {item["path"]: item for item in current_files}
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    if not changed:
        raise EvaluationError("waiver requires an actual candidate change")
    if "SKILL.md" in changed:
        raise EvaluationError("SKILL.md changes cannot be waived")
    waiver_class = args.waiver_class
    if waiver_class in {"documentation-only", "reference-only"}:
        raise EvaluationError(f"{waiver_class} cannot waive runtime-visible skill files")
    if not all(path.startswith("scripts/") for path in changed):
        raise EvaluationError("deterministic-helper waivers may change only scripts/")
    if not args.test_script:
        raise EvaluationError("deterministic-helper waiver requires --test-script")
    test_path = (skill_dir / args.test_script).resolve()
    try:
        test_relative = test_path.relative_to(skill_dir).as_posix()
    except ValueError as exc:
        raise EvaluationError("test script must remain inside the skill") from exc
    if not test_relative.startswith("scripts/") or test_relative in changed:
        raise EvaluationError("test script must be an unchanged scripts/ file")
    if before.get(test_relative) != after.get(test_relative):
        raise EvaluationError("test script must match the base snapshot exactly")
    result = subprocess.run(
        [str(test_path)],
        cwd=skill_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise EvaluationError("deterministic helper test command failed")
    try:
        attestation = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise EvaluationError("helper test must emit one JSON attestation") from exc
    expected_files = {path: after[path]["sha256"] for path in changed}
    if not isinstance(attestation, dict) or attestation != {
        "status": "pass",
        "verified_files": expected_files,
    }:
        raise EvaluationError("helper test attestation does not bind every changed file")
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": "waiver",
        "status": "waived",
        "evaluated_at": now_iso(),
        "skill": skill_dir.name,
        "skill_path": str(skill_dir),
        "candidate_id": current_id,
        "base_candidate_id": base["candidate_id"],
        "base_run_id": base["run_id"],
        "waived_from_receipt_sha256": verified_base_sha,
        "changed_paths": changed,
        "waiver_class": waiver_class,
        "waiver_reason": require_text(args.reason, "waiver reason"),
        "test_script": test_relative,
        "test_attestation": attestation,
    }
    receipt_path, receipt_sha = write_receipt(receipt)
    update_envelope(skill_dir, receipt_path)
    return {"status": "waived", "receipt": str(receipt_path), "receipt_sha256": receipt_sha}


def v2_evaluation_dir() -> Path:
    return evaluation_dir() / "v2"


def v2_receipt_path(receipt_sha256: str) -> Path:
    return v2_evaluation_dir() / "receipts" / f"{receipt_sha256}.json"


def v2_authority_path(skill_dir: Path, current_candidate_id: str) -> Path:
    return (
        v2_evaluation_dir()
        / "authority"
        / latest_key(str(skill_dir))
        / f"{current_candidate_id}.json"
    )


def v2_certification_path(aggregate_sha256: str) -> Path:
    return v2_evaluation_dir() / "certifications" / f"{aggregate_sha256}.json"


def v2_latest_waiver_path(skill_dir: Path) -> Path:
    return v2_evaluation_dir() / "latest-waiver" / f"{latest_key(str(skill_dir))}.json"


def v2_portfolio_receipt_path(receipt_sha256: str) -> Path:
    return v2_evaluation_dir() / "dashboard-v1" / "portfolio" / f"{receipt_sha256}.json"


def v2_portfolio_pointer_path(aggregate_sha256: str) -> Path:
    return (
        v2_evaluation_dir()
        / "dashboard-v1"
        / "portfolio-by-aggregate"
        / f"{aggregate_sha256}.json"
    )


def v2_transition_dir(skill_dir: Path) -> Path:
    return (
        v2_evaluation_dir()
        / "dashboard-v1"
        / "authority-transitions"
        / latest_key(str(skill_dir))
    )


def input_registry_root() -> Path:
    evaluation_root = resolve_path(v2_evaluation_dir(), "version-2 evaluation root")
    root = evaluation_root / "input-registry"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise EvaluationError("input registry root must be a real directory")
    return root


def input_registry_component(name: str, *, create: bool = False) -> Path:
    root = input_registry_root()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise EvaluationError("input registry root must be a real directory")
    path = root / name
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if path.exists() and (path.is_symlink() or not path.is_dir()):
        raise EvaluationError(f"input registry {name} root must be a real directory")
    return path


def require_registry_file(path: Path, root: Path, field: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise EvaluationError(f"{field} must be a regular non-symlink file")
    registry_root = input_registry_root()
    try:
        lexical_relative = path.relative_to(registry_root)
    except ValueError as exc:
        raise EvaluationError(f"{field} escapes the input registry") from exc
    current = registry_root
    for part in lexical_relative.parts:
        current = current / part
        if current.is_symlink():
            raise EvaluationError(f"{field} cannot traverse a symlink")
    resolved_root = resolve_path(root, f"{field} root")
    resolved = resolve_path(path, field)
    try:
        resolved.relative_to(registry_root)
        relative = resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise EvaluationError(f"{field} escapes its canonical registry root") from exc
    return resolved.read_bytes()


def create_only_bytes(path: Path, content: bytes, field: str) -> None:
    registry_root = input_registry_root()
    try:
        relative = path.relative_to(registry_root)
    except ValueError as exc:
        raise EvaluationError(f"{field} escapes the input registry") from exc
    current = registry_root
    for part in relative.parent.parts:
        current = current / part
        if current.is_symlink():
            raise EvaluationError(f"{field} cannot traverse a symlink")
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink() or not parent.is_dir():
        raise EvaluationError(f"{field} parent must be a real directory")
    try:
        resolve_path(parent, f"{field} parent").relative_to(registry_root)
    except ValueError as exc:
        raise EvaluationError(f"{field} parent escapes the input registry") from exc
    directory_fd = os.open(parent, os.O_RDONLY)
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        if path.exists() or path.is_symlink():
            existing = require_registry_file(path, parent, field)
            if existing != content:
                raise EvaluationError(f"{field} collision")
            return
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.fsync(directory_fd)
        except OSError:
            if path.exists() and not path.is_symlink():
                path.unlink()
            raise
    finally:
        os.close(directory_fd)


def registry_object_path(object_sha256: str) -> Path:
    identity = require_sha256(object_sha256, "registry object digest")
    return input_registry_component("objects") / identity.removeprefix("sha256:")


def registry_manifest_path(manifest_sha256: str) -> Path:
    identity = require_sha256(manifest_sha256, "input manifest digest")
    return (
        input_registry_component("manifests")
        / f"{identity.removeprefix('sha256:')}.json"
    )


def registry_review_path(receipt_sha256: str) -> Path:
    identity = require_sha256(receipt_sha256, "input receipt digest")
    return (
        input_registry_component("reviews")
        / f"{identity.removeprefix('sha256:')}.json"
    )


def input_readiness_dir(skill_dir: Path, current_candidate_id: str) -> Path:
    return (
        input_registry_component("readiness")
        / latest_key(str(skill_dir))
        / current_candidate_id
    )


def input_current_path(skill_dir: Path) -> Path:
    return input_registry_component("current") / f"{latest_key(str(skill_dir))}.json"


@contextmanager
def input_readiness_state_lock():
    root = input_registry_component("readiness", create=True)
    directory_fd = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(directory_fd)


def safe_registry_logical_path(value: Any, field: str) -> str:
    text = require_text(value, field)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != text:
        raise EvaluationError(f"{field} must be a normalized relative path")
    return text


def publish_registry_object(
    content: bytes,
    role: str,
    logical_path: str,
    media_type: str,
) -> dict[str, Any]:
    object_sha256 = f"sha256:{digest(content)}"
    path = (
        input_registry_component("objects", create=True)
        / object_sha256.removeprefix("sha256:")
    )
    create_only_bytes(path, content, "input registry object")
    return {
        "role": role,
        "logical_path": safe_registry_logical_path(
            logical_path, "registry object logical_path"
        ),
        "media_type": require_text(media_type, "registry object media_type"),
        "sha256": object_sha256,
        "size": len(content),
    }


def append_registry_tree_objects(
    values: list[dict[str, Any]],
    source: Path,
    role: str,
) -> None:
    if not source.exists():
        return
    if source.is_symlink() or not source.is_dir():
        raise EvaluationError(f"{source} must be a real directory")
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source).as_posix()
        if path.is_symlink():
            raise EvaluationError(f"{source}/{relative} cannot be a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise EvaluationError(f"{source}/{relative} must be a regular file")
        values.append(
            publish_registry_object(
                path.read_bytes(),
                role,
                f"{role}s/{relative}",
                "application/octet-stream",
            )
        )


def registry_object_bytes(entry: Any, index: int) -> tuple[dict[str, Any], bytes]:
    field = f"input manifest.objects[{index}]"
    if not isinstance(entry, dict):
        raise EvaluationError(f"{field} must be an object")
    require_exact_keys(
        entry,
        field,
        {"role", "logical_path", "media_type", "sha256", "size"},
    )
    role = require_text(entry.get("role"), f"{field}.role")
    if role not in (
        INPUT_REGISTRY_REQUIRED_ROLES
        | INPUT_REGISTRY_OPTIONAL_ROLES
        | INPUT_REVIEW_OBJECT_ROLES
        | {"fixture", "grader"}
    ):
        raise EvaluationError(f"{field}.role is unsupported")
    normalized = {
        "role": role,
        "logical_path": safe_registry_logical_path(
            entry.get("logical_path"), f"{field}.logical_path"
        ),
        "media_type": require_text(entry.get("media_type"), f"{field}.media_type"),
        "sha256": require_sha256(entry.get("sha256"), f"{field}.sha256"),
        "size": require_nonnegative_int(entry.get("size"), f"{field}.size"),
    }
    path = registry_object_path(normalized["sha256"])
    content = require_registry_file(
        path, input_registry_component("objects"), f"{field} object"
    )
    if (
        len(content) != normalized["size"]
        or f"sha256:{digest(content)}" != normalized["sha256"]
        or path.name != normalized["sha256"].removeprefix("sha256:")
    ):
        raise EvaluationError(f"{field} object digest, size, or path is invalid")
    return normalized, content


def load_registry_json_object(entry: dict[str, Any], field: str) -> dict[str, Any]:
    path = registry_object_path(entry["sha256"])
    content = require_registry_file(path, input_registry_component("objects"), field)
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"{field} must contain canonical JSON") from exc
    if not isinstance(value, dict) or content != canonical(value):
        raise EvaluationError(f"{field} must contain one canonical JSON object")
    return value


def manifest_role(
    objects: list[dict[str, Any]], role: str
) -> dict[str, Any]:
    matching = [item for item in objects if item["role"] == role]
    if len(matching) != 1:
        raise EvaluationError(f"input manifest must contain exactly one {role} object")
    return matching[0]


def is_bounded_input_manifest(manifest: dict[str, Any]) -> bool:
    return manifest.get("authoring_method") in {
        "bounded-safe-author",
        "bounded-safe-repair",
    }


def validate_input_manifest(
    skill_dir: Path, manifest_sha256: str
) -> dict[str, Any]:
    skill_dir = resolve_path(skill_dir, "skill directory")
    manifest_path = registry_manifest_path(manifest_sha256)
    raw = require_registry_file(
        manifest_path, input_registry_component("manifests"), "input manifest"
    )
    manifest = load_json(manifest_path)
    if raw != canonical(manifest):
        raise EvaluationError("input manifest must be canonical JSON")
    if (
        f"sha256:{digest(raw)}" != manifest_sha256
        or manifest_path.name
        != f"{manifest_sha256.removeprefix('sha256:')}.json"
    ):
        raise EvaluationError("input manifest content-addressed path does not match")
    manifest_keys = {
            "schema_version",
            "kind",
            "skill_path",
            "skill_key",
            "candidate_id",
            "candidate_inventory",
            "suite_id",
            "policy_id",
            "observation_plan_id",
            "complete_policy_sha256",
            "compilation_sha256",
            "routing_sha256",
            "fixture_set_sha256",
            "grader_set_sha256",
            "rubric_sha256",
            "tool_policy_id",
            "harness_executable_sha256",
            "authoring_method",
            "source_identities",
            "tool_version",
            "objects",
    }
    if set(manifest) not in (manifest_keys, manifest_keys | {"repair_lineage"}):
        raise EvaluationError("input manifest has unexpected or missing fields")
    if (
        manifest.get("schema_version") != INPUT_REGISTRY_SCHEMA_VERSION
        or manifest.get("kind") != "evaluation_input_manifest"
        or manifest.get("skill_path") != str(skill_dir)
        or manifest.get("skill_key") != latest_key(str(skill_dir))
        or manifest.get("tool_version") != RUNNER_VERSION
    ):
        raise EvaluationError("input manifest schema, skill, or tool identity is invalid")
    candidate, files = candidate_id(skill_dir)
    if (
        manifest.get("candidate_id") != candidate
        or manifest.get("candidate_inventory") != files
    ):
        raise EvaluationError("input manifest candidate identity is stale")
    require_text(manifest.get("authoring_method"), "input manifest.authoring_method")
    source_identities = manifest.get("source_identities")
    if (
        not isinstance(source_identities, list)
        or not source_identities
        or not all(isinstance(item, str) and item.strip() for item in source_identities)
        or len(set(source_identities)) != len(source_identities)
    ):
        raise EvaluationError(
            "input manifest.source_identities must be a unique non-empty text list"
        )
    object_values = manifest.get("objects")
    if not isinstance(object_values, list):
        raise EvaluationError("input manifest.objects must be a list")
    objects: list[dict[str, Any]] = []
    logical_paths: set[str] = set()
    for index, value in enumerate(object_values):
        entry, _ = registry_object_bytes(value, index)
        if entry["logical_path"] in logical_paths:
            raise EvaluationError("input manifest object logical paths must be unique")
        logical_paths.add(entry["logical_path"])
        objects.append(entry)
    if objects != sorted(
        objects, key=lambda item: (item["role"], item["logical_path"])
    ):
        raise EvaluationError("input manifest objects must use canonical role/path order")
    roles = {item["role"] for item in objects}
    if not INPUT_REGISTRY_REQUIRED_ROLES <= roles:
        raise EvaluationError("input manifest is missing a required object role")
    suite_entry = manifest_role(objects, "suite")
    policy_entry = manifest_role(objects, "policy")
    compilation_entry = manifest_role(objects, "compilation")
    routing_entry = manifest_role(objects, "routing")
    harness_entry = manifest_role(objects, "harness")
    suite, suite_id = load_suite(registry_object_path(suite_entry["sha256"]))
    policy, policy_id = load_policy(registry_object_path(policy_entry["sha256"]))
    harness_sha = sha256_file(trusted_harness_path())
    if harness_entry["sha256"] != harness_sha:
        raise EvaluationError("input manifest harness differs from the reviewed harness")
    config, harness_suite = validate_compilation_config(
        registry_object_path(compilation_entry["sha256"]),
        suite,
        policy,
        harness_sha,
    )
    routing = validate_routing(
        registry_object_path(routing_entry["sha256"]),
        config["executors"],
        config["comparator"],
    )
    if (
        manifest.get("suite_id") != suite_id
        or manifest.get("policy_id") != policy_id
        or manifest.get("observation_plan_id")
        != observation_plan_identity(policy, policy_id)
        or manifest.get("complete_policy_sha256") != policy_entry["sha256"]
        or manifest.get("compilation_sha256") != compilation_entry["sha256"]
        or manifest.get("routing_sha256") != routing_entry["sha256"]
        or manifest.get("fixture_set_sha256")
        != f"sha256:{digest(canonical(config['case_runtime']))}"
        or manifest.get("grader_set_sha256")
        != f"sha256:{digest(canonical(config['graders']))}"
        or manifest.get("rubric_sha256")
        != f"sha256:{digest(canonical(config['rubric']))}"
        or manifest.get("tool_policy_id") != config["tool_policy_id"]
        or manifest.get("harness_executable_sha256") != harness_sha
    ):
        raise EvaluationError("input manifest normalized input identities do not match")
    authoring_roles = roles & INPUT_AUTHORING_OBJECT_ROLES
    repair_roles = roles & INPUT_REPAIR_OBJECT_ROLES
    if manifest["authoring_method"] == "bounded-safe-author":
        if (
            "repair_lineage" in manifest
            or authoring_roles != INPUT_AUTHORING_OBJECT_ROLES
            or repair_roles
        ):
            raise EvaluationError(
                "bounded-safe-author manifest is missing exact authoring provenance"
            )
        packet = load_registry_json_object(
            manifest_role(objects, "authoring_packet"), "authoring packet"
        )
        draft_value = load_registry_json_object(
            manifest_role(objects, "authoring_draft"), "authoring draft"
        )
        receipt = load_registry_json_object(
            manifest_role(objects, "authoring_receipt"), "authoring receipt"
        )
        operation = load_registry_json_object(
            manifest_role(objects, "authoring_operation"), "authoring operation"
        )
        authoring_adapter_entry = manifest_role(objects, "authoring_adapter")
        fixture_inventory = sorted(
            [
                {
                    "path": item["logical_path"].removeprefix("fixtures/"),
                    "sha256": item["sha256"],
                    "size": item["size"],
                }
                for item in objects
                if item["role"] == "fixture"
            ],
            key=lambda item: item["path"],
        )
        grader_inventory = sorted(
            [
                {
                    "path": item["logical_path"].removeprefix("graders/"),
                    "sha256": item["sha256"],
                    "size": item["size"],
                }
                for item in objects
                if item["role"] == "grader"
            ],
            key=lambda item: item["path"],
        )
        packet, draft_value, _, operation = validate_authoring_provenance(
            skill_dir,
            candidate,
            files,
            suite,
            suite_id,
            policy,
            policy_id,
            config,
            routing,
            harness_sha,
            packet,
            draft_value,
            receipt,
            operation,
            authoring_adapter_entry["sha256"],
            fixture_inventory,
            grader_inventory,
        )
        if set(manifest["source_identities"]) != {
            packet["packet_id"],
            f"sha256:{digest(canonical(draft_value))}",
            packet["source_catalog_id"],
            operation["operation_id"],
        }:
            raise EvaluationError(
                "bounded-safe-author manifest source identities are incomplete"
            )
    elif manifest["authoring_method"] == "bounded-safe-repair":
        validate_repaired_input_manifest(
            skill_dir,
            manifest_sha256,
            manifest,
            objects,
            resolved_suite=suite,
            resolved_suite_id=suite_id,
            resolved_policy=policy,
            resolved_policy_id=policy_id,
            resolved_config=config,
            resolved_routing=routing,
            harness_sha=harness_sha,
        )
    elif authoring_roles or repair_roles or "repair_lineage" in manifest:
        raise EvaluationError(
            "authoring provenance is valid only for bounded-safe-author manifests"
        )
    return {
        "input_manifest_sha256": manifest_sha256,
        "manifest": {**manifest, "objects": objects},
        "candidate_id": candidate,
        "candidate_inventory": files,
        "suite": suite,
        "suite_id": suite_id,
        "policy": policy,
        "policy_id": policy_id,
        "config": config,
        "harness_suite": harness_suite,
        "routing": routing,
    }


def write_input_registry_json(
    component: str, value: dict[str, Any], field: str
) -> tuple[Path, str]:
    content = canonical(value)
    value_sha256 = f"sha256:{digest(content)}"
    path = (
        input_registry_component(component, create=True)
        / f"{value_sha256.removeprefix('sha256:')}.json"
    )
    create_only_bytes(path, content, field)
    return path, value_sha256


def validate_authoring_catalog(
    path: Path,
    config_path: Path,
    config: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    catalog = load_json(path)
    if raw != canonical(catalog):
        raise EvaluationError("authoring catalog must be canonical JSON")
    require_exact_keys(
        catalog,
        "authoring catalog",
        {"schema_version", "kind", "fixtures", "graders", "rubric"},
    )
    if (
        catalog.get("schema_version") != AUTHORING_CATALOG_SCHEMA_VERSION
        or catalog.get("kind") != "safe_evaluation_source_catalog"
    ):
        raise EvaluationError("unsupported evaluation-input authoring catalog")

    fixture_values = catalog.get("fixtures")
    if not isinstance(fixture_values, list) or not fixture_values:
        raise EvaluationError("authoring catalog.fixtures must be a non-empty list")
    fixture_root = config_path.parent / "fixtures"
    actual_fixture_inventory = canonical_file_inventory(fixture_root)
    fixtures: list[dict[str, Any]] = []
    fixture_ids: set[str] = set()
    fixture_inventory: list[dict[str, Any]] = []
    for index, value in enumerate(fixture_values):
        field = f"authoring catalog.fixtures[{index}]"
        if not isinstance(value, dict):
            raise EvaluationError(f"{field} must be an object")
        require_exact_keys(
            value,
            field,
            {"id", "path", "sha256", "size", "source_kind", "description"},
        )
        fixture_id = require_text(value.get("id"), f"{field}.id")
        if (
            fixture_id in fixture_ids
            or not GRADER_ID_RE.fullmatch(fixture_id)
        ):
            raise EvaluationError(f"{field}.id is invalid or duplicated")
        fixture_ids.add(fixture_id)
        logical_path = safe_registry_logical_path(value.get("path"), f"{field}.path")
        source_kind = value.get("source_kind")
        if source_kind not in AUTHORING_FIXTURE_SOURCE_KINDS:
            raise EvaluationError(
                f"{field}.source_kind must be public or synthetic"
            )
        item = {
            "id": fixture_id,
            "path": logical_path,
            "sha256": require_sha256(value.get("sha256"), f"{field}.sha256"),
            "size": require_nonnegative_int(value.get("size"), f"{field}.size"),
            "source_kind": source_kind,
            "description": require_authoring_safe_text(
                value.get("description"),
                f"{field}.description",
                maximum=AUTHORING_MAX_DESCRIPTION_BYTES,
            ),
        }
        if item["size"] > AUTHORING_MAX_FIXTURE_BYTES:
            raise EvaluationError(f"{field} exceeds the authoring fixture size limit")
        fixture_path = fixture_root / logical_path
        if fixture_path.is_symlink() or not fixture_path.is_file():
            raise EvaluationError(f"{field}.path does not name a regular fixture")
        content = fixture_path.read_bytes()
        reject_authoring_sensitive_bytes(content, field)
        if (
            len(content) != item["size"]
            or f"sha256:{digest(content)}" != item["sha256"]
        ):
            raise EvaluationError(f"{field} digest or size does not match its fixture")
        fixture_inventory.append(
            {"path": logical_path, "sha256": item["sha256"], "size": item["size"]}
        )
        fixtures.append(item)
    if fixtures != sorted(fixtures, key=lambda item: item["id"]):
        raise EvaluationError("authoring catalog.fixtures must use canonical id order")
    if sorted(fixture_inventory, key=lambda item: item["path"]) != actual_fixture_inventory:
        raise EvaluationError(
            "authoring catalog must declare the complete fixture tree exactly"
        )
    runtime_fixture_ids = {item["fixture"] for item in config["case_runtime"]}
    if not runtime_fixture_ids <= fixture_ids:
        raise EvaluationError(
            "compilation case runtime uses a fixture absent from the safe catalog"
        )

    grader_values = catalog.get("graders")
    if not isinstance(grader_values, list) or not grader_values:
        raise EvaluationError("authoring catalog.graders must be a non-empty list")
    graders: list[dict[str, Any]] = []
    for index, value in enumerate(grader_values):
        field = f"authoring catalog.graders[{index}]"
        if not isinstance(value, dict):
            raise EvaluationError(f"{field} must be an object")
        require_exact_keys(value, field, {"id", "objective", "description"})
        if value.get("objective") is not True:
            raise EvaluationError(f"{field} must declare an objective grader")
        graders.append(
            {
                "id": require_text(value.get("id"), f"{field}.id"),
                "objective": True,
                "description": require_authoring_safe_text(
                    value.get("description"),
                    f"{field}.description",
                    maximum=AUTHORING_MAX_DESCRIPTION_BYTES,
                ),
            }
        )
    expected_grader_ids = [item["id"] for item in config["graders"]]
    if [item["id"] for item in graders] != expected_grader_ids:
        raise EvaluationError(
            "authoring catalog graders differ from the compilation grader order"
        )
    if len(set(expected_grader_ids)) != len(expected_grader_ids):
        raise EvaluationError("compilation graders contain duplicate ids")

    rubric = catalog.get("rubric")
    if not isinstance(rubric, dict):
        raise EvaluationError("authoring catalog.rubric must be an object")
    require_exact_keys(rubric, "authoring catalog.rubric", {"identity", "description"})
    normalized_rubric = {
        "identity": require_sha256(
            rubric.get("identity"), "authoring catalog.rubric.identity"
        ),
        "description": require_authoring_safe_text(
            rubric.get("description"),
            "authoring catalog.rubric.description",
            maximum=AUTHORING_MAX_DESCRIPTION_BYTES,
        ),
    }
    expected_rubric = f"sha256:{digest(canonical(config['rubric']))}"
    if normalized_rubric["identity"] != expected_rubric:
        raise EvaluationError(
            "authoring catalog rubric differs from the compilation rubric"
        )
    grader_root = config_path.parent / "graders"
    grader_inventory = canonical_file_inventory(grader_root)
    for entry in grader_inventory:
        grader_path = grader_root / entry["path"]
        if entry["size"] > AUTHORING_MAX_FIXTURE_BYTES:
            raise EvaluationError(
                f"grader template {entry['path']} exceeds the authoring size limit"
            )
        reject_authoring_sensitive_bytes(
            grader_path.read_bytes(), f"grader template {entry['path']}"
        )
    normalized = {
        "schema_version": AUTHORING_CATALOG_SCHEMA_VERSION,
        "kind": "safe_evaluation_source_catalog",
        "fixtures": fixtures,
        "graders": graders,
        "rubric": normalized_rubric,
        "grader_tree_inventory": grader_inventory,
        "grader_tree_id": f"sha256:{digest(canonical(grader_inventory))}",
    }
    reject_authoring_sensitive_value(normalized, "authoring catalog")
    return normalized, f"sha256:{digest(canonical(normalized))}"


def build_input_author_packet(
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any]]:
    skill_dir = resolve_path(Path(args.skill_dir), "skill directory")
    candidate, files = candidate_id(skill_dir)
    contract_path = skill_dir / "SKILL.md"
    if contract_path.is_symlink() or not contract_path.is_file():
        raise EvaluationError("skill contract must be a regular non-symlink file")
    contract = contract_path.read_bytes()
    if len(contract) > AUTHORING_MAX_SKILL_CONTRACT_BYTES:
        raise EvaluationError("skill contract exceeds the authoring size limit")
    try:
        contract_text = contract.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvaluationError("skill contract must be UTF-8 text") from exc
    reject_authoring_sensitive_bytes(
        contract, "skill contract", allow_contract_paths=True
    )

    suite, suite_id = load_suite(resolve_path(Path(args.suite), "suite template"))
    if (
        suite["compiled_from_schema_version"] is not None
        or suite["cross_executor_authority"] is not True
    ):
        raise EvaluationError(
            "safe authoring requires a cross-executor schema-2 suite template"
        )
    for index, case in enumerate(suite["cases"]):
        require_authoring_safe_text(
            case["prompt"],
            f"suite template.cases[{index}].prompt",
            maximum=AUTHORING_MAX_DESCRIPTION_BYTES,
        )
    policy, policy_id = load_policy(resolve_path(Path(args.policy), "policy"))
    harness = require_trusted_harness(Path(args.harness))
    harness_sha = sha256_file(harness)
    config_path = resolve_path(Path(args.config), "compilation config")
    config, _ = validate_compilation_config(config_path, suite, policy, harness_sha)
    routing = validate_routing(
        resolve_path(Path(args.routing), "routing config"),
        config["executors"],
        config["comparator"],
    )
    catalog, catalog_id = validate_authoring_catalog(
        resolve_path(Path(args.catalog), "authoring catalog"),
        config_path,
        config,
    )
    reject_authoring_sensitive_value(suite, "suite template")
    reject_authoring_sensitive_value(policy, "policy contract")
    reject_authoring_sensitive_value(config, "compilation source contract")
    routing_contract = {
        "executors": [
            {
                key: route[key]
                for key in ("name", "adapter_id", "adapter_executable_sha256")
            }
            for route in routing["executors"]
        ],
        "comparator": {
            key: routing["comparator"][key]
            for key in ("route", "adapter_id", "adapter_executable_sha256")
        },
    }
    compilation_contract = {
        "tool_policy_id": config["tool_policy_id"],
        "retention_policy_id": config["retention_policy_id"],
        "limits": config["limits"],
        "graders": [
            {
                "id": source["id"],
                "type": source["type"],
                "safety": source["safety"],
                "identity": source["identity"],
            }
            for source in suite["graders"]
        ],
        "case_runtime": config["case_runtime"],
        "rubric": catalog["rubric"],
        "executors": config["executors"],
        "comparator": config["comparator"],
    }
    reject_authoring_sensitive_value(
        compilation_contract, "model-facing compilation contract"
    )
    reject_authoring_sensitive_value(routing_contract, "routing contract")
    packet = {
        "schema_version": AUTHORING_PACKET_SCHEMA_VERSION,
        "kind": "safe_evaluation_input_authoring_packet",
        "candidate_id": candidate,
        "candidate_inventory": files,
        "skill_contract": {
            "logical_path": "SKILL.md",
            "sha256": f"sha256:{digest(contract)}",
            "content": contract_text,
        },
        "suite_template": suite,
        "suite_template_id": suite_id,
        "source_catalog": catalog,
        "source_catalog_id": catalog_id,
        "policy_contract": policy,
        "policy_id": policy_id,
        "compilation_source_id": f"sha256:{digest(canonical(config))}",
        "compilation_contract": compilation_contract,
        "routing_source_id": f"sha256:{digest(canonical(routing))}",
        "routing_contract": routing_contract,
        "harness_executable_sha256": harness_sha,
    }
    packet["packet_id"] = f"sha256:{digest(canonical(packet))}"
    return packet, {
        "skill_dir": skill_dir,
        "suite": suite,
        "policy": policy,
        "config": config,
        "config_path": config_path,
        "routing": routing,
        "harness_sha": harness_sha,
        "catalog_id": catalog_id,
    }


def authoring_output_path(value: str, skill_dir: Path, field: str) -> Path:
    supplied = Path(value)
    output = resolve_path(supplied.parent, f"{field} parent") / supplied.name
    if output.is_symlink() or output.exists():
        raise EvaluationError(f"{field} must not already exist")
    try:
        output.relative_to(skill_dir)
    except ValueError:
        pass
    else:
        raise EvaluationError(f"{field} cannot be written inside the skill root")
    return output


def v2_input_author_packet(args: argparse.Namespace) -> dict[str, Any]:
    packet, context = build_input_author_packet(args)
    output = authoring_output_path(
        args.output, context["skill_dir"], "authoring packet output"
    )
    atomic_write(output, packet)
    return {
        "candidate_id": packet["candidate_id"],
        "packet": str(output),
        "packet_id": packet["packet_id"],
        "source_catalog_id": context["catalog_id"],
    }


def validate_input_author_draft_value(
    draft: dict[str, Any],
    packet: dict[str, Any],
    context: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    if len(canonical(draft)) > AUTHORING_MAX_SKILL_CONTRACT_BYTES:
        raise EvaluationError("authoring draft exceeds the authoring size limit")
    require_exact_keys(
        draft,
        "authoring draft",
        {"schema_version", "kind", "packet_id", "candidate_id", "cases"},
    )
    repair = packet.get("kind") == "safe_evaluation_input_repair_packet"
    expected_kind = (
        "safe_evaluation_input_repair_draft"
        if repair
        else "safe_evaluation_input_draft"
    )
    if (
        draft.get("schema_version") != AUTHORING_PACKET_SCHEMA_VERSION
        or draft.get("kind") != expected_kind
        or draft.get("packet_id") != packet["packet_id"]
        or draft.get("candidate_id") != packet["candidate_id"]
    ):
        raise EvaluationError(
            "authoring draft schema, packet, or candidate identity is invalid"
        )
    values = draft.get("cases")
    template_cases = context["suite"]["cases"]
    runtime = {
        item["id"]: item for item in context["config"]["case_runtime"]
    }
    if not isinstance(values, list) or len(values) != len(template_cases):
        raise EvaluationError(
            "authoring draft must define every template case exactly once"
        )
    cases: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    prompts: set[str] = set()
    changed = False
    for index, (value, template) in enumerate(zip(values, template_cases)):
        field = f"authoring draft.cases[{index}]"
        if not isinstance(value, dict):
            raise EvaluationError(f"{field} must be an object")
        require_exact_keys(
            value,
            field,
            {
                "id",
                "class",
                "task_id",
                "prompt",
                "deterministic_graders",
                "fixture",
                "artifacts",
                "semantic",
            },
        )
        source_runtime = runtime[template["id"]]
        fixed = {
            "id": template["id"],
            "class": template["class"],
            "deterministic_graders": template["deterministic_graders"],
            "fixture": source_runtime["fixture"],
            "artifacts": source_runtime["artifacts"],
            "semantic": source_runtime["semantic"],
        }
        if any(value.get(key) != expected for key, expected in fixed.items()):
            raise EvaluationError(
                f"{field} changes a trusted case, fixture, grader, or runtime field"
            )
        task_id = require_text(value.get("task_id"), f"{field}.task_id")
        if not TASK_ID_RE.fullmatch(task_id) or task_id in task_ids:
            raise EvaluationError(f"{field}.task_id is invalid or duplicated")
        prompt = require_authoring_safe_text(
            value.get("prompt"),
            f"{field}.prompt",
            maximum=AUTHORING_MAX_DESCRIPTION_BYTES,
        )
        if prompt in prompts:
            raise EvaluationError(f"{field}.prompt duplicates another case prompt")
        task_ids.add(task_id)
        prompts.add(prompt)
        if task_id != template["task_id"] or prompt != template["prompt"]:
            changed = True
        cases.append({**fixed, "task_id": task_id, "prompt": prompt})
    if repair and not changed:
        raise EvaluationError("repair draft must change at least one task or prompt")
    normalized = {
        "schema_version": AUTHORING_PACKET_SCHEMA_VERSION,
        "kind": expected_kind,
        "packet_id": packet["packet_id"],
        "candidate_id": packet["candidate_id"],
        "cases": cases,
    }
    reject_authoring_sensitive_value(normalized, "authoring draft")
    return normalized, f"sha256:{digest(canonical(normalized))}"


def validate_input_author_draft(
    path: Path,
    packet: dict[str, Any],
    context: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    if path.stat().st_size > AUTHORING_MAX_SKILL_CONTRACT_BYTES:
        raise EvaluationError("authoring draft exceeds the authoring size limit")
    return validate_input_author_draft_value(load_json(path), packet, context)


def materialized_authoring_suite(
    draft: dict[str, Any],
    template_suite: dict[str, Any],
) -> dict[str, Any]:
    materialized_cases: list[dict[str, Any]] = []
    for draft_case, template in zip(draft["cases"], template_suite["cases"]):
        case = {
            "id": template["id"],
            "class": template["class"],
            "task_id": draft_case["task_id"],
            "prompt": draft_case["prompt"],
            "deterministic_graders": template["deterministic_graders"],
        }
        if "activation" in template:
            case["activation"] = template["activation"]
        materialized_cases.append(case)
    return {
        **template_suite,
        "cases": materialized_cases,
    }


def validate_materialized_authoring_trees(
    staging: Path,
    packet: dict[str, Any],
) -> None:
    catalog = packet["source_catalog"]
    expected_fixtures = sorted(
        [
            {
                "path": item["path"],
                "sha256": item["sha256"],
                "size": item["size"],
            }
            for item in catalog["fixtures"]
        ],
        key=lambda item: item["path"],
    )
    actual_fixtures = canonical_file_inventory(staging / "fixtures")
    if actual_fixtures != expected_fixtures:
        raise EvaluationError(
            "materialized fixture tree differs from the packet-bound inventory"
        )
    actual_graders = canonical_file_inventory(staging / "graders")
    if (
        actual_graders != catalog["grader_tree_inventory"]
        or f"sha256:{digest(canonical(actual_graders))}"
        != catalog["grader_tree_id"]
    ):
        raise EvaluationError(
            "materialized grader tree differs from the packet-bound inventory"
        )
    for tree_name, inventory_values in (
        ("fixtures", actual_fixtures),
        ("graders", actual_graders),
    ):
        for entry in inventory_values:
            reject_authoring_sensitive_bytes(
                (staging / tree_name / entry["path"]).read_bytes(),
                f"materialized {tree_name} object {entry['path']}",
            )


def materialize_input_author(
    expected_packet: dict[str, Any],
    context: dict[str, Any],
    draft_value: dict[str, Any],
    output_dir: str,
) -> dict[str, Any]:
    draft, draft_id = validate_input_author_draft_value(
        draft_value, expected_packet, context
    )
    output = authoring_output_path(
        output_dir, context["skill_dir"], "authoring materialization output"
    )
    parent = output.parent
    if parent.is_symlink() or not parent.is_dir():
        raise EvaluationError("authoring materialization parent must be a real directory")

    runtime = {
        item["id"]: item for item in context["config"]["case_runtime"]
    }
    suite = materialized_authoring_suite(draft, context["suite"])
    materialized_cases = suite["cases"]
    suite_id = f"sha256:{digest(canonical(suite))}"
    config = {
        **context["config"],
        "case_runtime": [
            {
                "id": case["id"],
                "fixture": runtime[case["id"]]["fixture"],
                "artifacts": runtime[case["id"]]["artifacts"],
                "semantic": runtime[case["id"]]["semantic"],
            }
            for case in materialized_cases
        ],
    }
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", dir=parent)
    )
    try:
        for tree_name in ("fixtures", "graders"):
            source = context["config_path"].parent / tree_name
            if source.exists():
                shutil.copytree(source, staging / tree_name)
        atomic_write(staging / "suite.json", suite)
        atomic_write(staging / "policy.json", context["policy"])
        atomic_write(staging / "compilation.json", config)
        atomic_write(staging / "routing.json", context["routing"])
        atomic_write(
            staging / "authoring.json",
            {
                "schema_version": AUTHORING_PACKET_SCHEMA_VERSION,
                "kind": "safe_evaluation_input_materialization",
                "candidate_id": expected_packet["candidate_id"],
                "packet_id": expected_packet["packet_id"],
                "draft_id": draft_id,
                "suite_id": suite_id,
                "source_catalog_id": expected_packet["source_catalog_id"],
            },
        )
        staged_suite, staged_suite_id = load_suite(staging / "suite.json")
        if staged_suite != suite or staged_suite_id != suite_id:
            raise EvaluationError(
                "materialized suite differs from its trusted in-memory form"
            )
        validate_compilation_config(
            staging / "compilation.json",
            staged_suite,
            context["policy"],
            context["harness_sha"],
        )
        validate_routing(
            staging / "routing.json",
            config["executors"],
            config["comparator"],
        )
        validate_materialized_authoring_trees(staging, expected_packet)
        os.replace(staging, output)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return {
        "candidate_id": expected_packet["candidate_id"],
        "packet_id": expected_packet["packet_id"],
        "draft_id": draft_id,
        "suite_id": suite_id,
        "output_dir": str(output),
        "suite": str(output / "suite.json"),
        "policy": str(output / "policy.json"),
        "config": str(output / "compilation.json"),
        "routing": str(output / "routing.json"),
    }


def v2_input_author_materialize(args: argparse.Namespace) -> dict[str, Any]:
    expected_packet, context = build_input_author_packet(args)
    supplied_packet = load_json(resolve_path(Path(args.packet), "authoring packet"))
    if supplied_packet != expected_packet:
        raise EvaluationError(
            "authoring packet differs from the current candidate and trusted sources"
        )
    draft = load_json(resolve_path(Path(args.draft), "authoring draft"))
    return materialize_input_author(
        expected_packet, context, draft, args.output_dir
    )


def validate_authoring_operation(
    operation: dict[str, Any],
    packet: dict[str, Any],
    draft_id: str | None,
    adapter_sha256: str,
    *,
    expected_outcome: str = "draft",
) -> dict[str, Any]:
    if expected_outcome not in {"draft", "insufficient_information"}:
        raise EvaluationError("unsupported authoring operation outcome")
    require_exact_keys(
        operation,
        "authoring operation",
        {
            "schema_version",
            "kind",
            "operation",
            "status",
            "vendor",
            "model",
            "observed_model",
            "adapter_executable_sha256",
            "packet_id",
            "candidate_id",
            "outcome",
            "summary",
            "reason",
            "draft_id",
            "usage",
            "billing",
            "elapsed_ms",
            "operation_id",
        },
    )
    model = require_text(operation.get("model"), "authoring operation.model")
    outcome_valid = (
        operation.get("outcome") == "draft"
        and expected_outcome == "draft"
        and operation.get("reason") is None
        and operation.get("draft_id") == draft_id
    ) or (
        operation.get("outcome") == "insufficient_information"
        and expected_outcome == "insufficient_information"
        and operation.get("reason")
        in {
            "evaluation_case_unavailable",
            "safe_fixture_unavailable",
            "objective_grader_unavailable",
        }
        and operation.get("draft_id") is None
        and draft_id is None
    )
    if (
        operation.get("schema_version") != AUTHORING_PACKET_SCHEMA_VERSION
        or operation.get("kind") != "evaluation_input_model_operation"
        or operation.get("operation") != "author"
        or operation.get("status") != "completed"
        or operation.get("vendor") != "copilot"
        or model == "default"
        or operation.get("observed_model") != model
        or operation.get("adapter_executable_sha256") != adapter_sha256
        or operation.get("packet_id") != packet["packet_id"]
        or operation.get("candidate_id") != packet["candidate_id"]
        or not outcome_valid
    ):
        raise EvaluationError(
            "authoring operation identity, outcome, or draft binding is invalid"
        )
    require_authoring_safe_text(
        operation.get("summary"),
        "authoring operation.summary",
        maximum=AUTHORING_MAX_DESCRIPTION_BYTES,
    )
    usage = operation.get("usage")
    if not isinstance(usage, dict):
        raise EvaluationError("authoring operation.usage must be an object")
    require_exact_keys(
        usage,
        "authoring operation.usage",
        {"normalized_tokens", "input_tokens", "output_tokens"},
    )
    normalized_tokens = require_positive_int(
        usage.get("normalized_tokens"),
        "authoring operation.usage.normalized_tokens",
    )
    if normalized_tokens > 112_000:
        raise EvaluationError("authoring operation exceeds the normalized-token budget")
    detailed: list[int] = []
    for field in ("input_tokens", "output_tokens"):
        value = usage.get(field)
        if value is not None:
            detailed.append(
                require_nonnegative_int(value, f"authoring operation.usage.{field}")
            )
    if detailed and (
        len(detailed) != 2 or sum(detailed) != normalized_tokens
    ):
        raise EvaluationError(
            "authoring operation detailed usage does not match normalized tokens"
        )
    billing = operation.get("billing")
    if not isinstance(billing, dict):
        raise EvaluationError("authoring operation.billing must be an object")
    require_exact_keys(
        billing,
        "authoring operation.billing",
        {
            "status",
            "cost_usd",
            "provider",
            "unavailable_reason",
            "native_line_item_id",
            "native_event_sha256",
            "native_event_size",
        },
    )
    billing_status = billing.get("status")
    billing_cost = billing.get("cost_usd")
    if billing_status != "unavailable" or billing != {
        "status": "unavailable",
        "cost_usd": None,
        "provider": "copilot",
        "unavailable_reason": "provider_telemetry_unavailable",
        "native_line_item_id": None,
        "native_event_sha256": None,
        "native_event_size": None,
    }:
        raise EvaluationError("authoring operation billing telemetry is invalid")
    elapsed_ms = require_nonnegative_int(
        operation.get("elapsed_ms"), "authoring operation.elapsed_ms"
    )
    if elapsed_ms > 25 * 60 * 1000:
        raise EvaluationError("authoring operation exceeds the elapsed-time budget")
    operation_without_id = {
        key: value for key, value in operation.items() if key != "operation_id"
    }
    expected_id = f"sha256:{digest(shadow_canonical(operation_without_id))}"
    if operation.get("operation_id") != expected_id:
        raise EvaluationError("authoring operation content identity is invalid")
    return operation


def validate_authoring_provenance(
    skill_dir: Path,
    candidate: str,
    files: list[dict[str, Any]],
    suite: dict[str, Any],
    suite_id: str,
    policy: dict[str, Any],
    policy_id: str,
    config: dict[str, Any],
    routing: dict[str, Any],
    harness_sha: str,
    packet: dict[str, Any],
    draft_value: dict[str, Any],
    receipt: dict[str, Any],
    operation: dict[str, Any],
    adapter_sha256: str,
    fixture_inventory: list[dict[str, Any]],
    grader_inventory: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    require_exact_keys(
        packet,
        "retained authoring packet",
        {
            "schema_version",
            "kind",
            "candidate_id",
            "candidate_inventory",
            "skill_contract",
            "suite_template",
            "suite_template_id",
            "source_catalog",
            "source_catalog_id",
            "policy_contract",
            "policy_id",
            "compilation_source_id",
            "compilation_contract",
            "routing_source_id",
            "routing_contract",
            "harness_executable_sha256",
            "packet_id",
        },
    )
    packet_without_id = {key: value for key, value in packet.items() if key != "packet_id"}
    packet_id = f"sha256:{digest(canonical(packet_without_id))}"
    if (
        packet.get("schema_version") != AUTHORING_PACKET_SCHEMA_VERSION
        or packet.get("kind") != "safe_evaluation_input_authoring_packet"
        or packet.get("packet_id") != packet_id
        or packet.get("candidate_id") != candidate
        or packet.get("candidate_inventory") != files
        or packet.get("harness_executable_sha256") != harness_sha
    ):
        raise EvaluationError(
            "retained authoring packet schema or current candidate binding is invalid"
        )
    contract = packet.get("skill_contract")
    contract_content = (skill_dir / "SKILL.md").read_bytes()
    if contract != {
        "logical_path": "SKILL.md",
        "sha256": f"sha256:{digest(contract_content)}",
        "content": contract_content.decode("utf-8"),
    }:
        raise EvaluationError(
            "retained authoring packet skill contract is not current"
        )
    template_suite = packet.get("suite_template")
    if (
        not isinstance(template_suite, dict)
        or packet.get("suite_template_id")
        != f"sha256:{digest(canonical(template_suite))}"
        or template_suite.get("compiled_from_schema_version") is not None
        or template_suite.get("cross_executor_authority") is not True
        or packet.get("policy_contract") != policy
        or packet.get("policy_id") != policy_id
        or packet.get("compilation_source_id")
        != f"sha256:{digest(canonical(config))}"
        or packet.get("routing_source_id")
        != f"sha256:{digest(canonical(routing))}"
    ):
        raise EvaluationError(
            "retained authoring packet trusted source identities are invalid"
        )
    catalog = packet.get("source_catalog")
    if not isinstance(catalog, dict):
        raise EvaluationError("retained authoring packet source catalog is invalid")
    require_exact_keys(
        catalog,
        "retained authoring catalog",
        {
            "schema_version",
            "kind",
            "fixtures",
            "graders",
            "rubric",
            "grader_tree_inventory",
            "grader_tree_id",
        },
    )
    if (
        catalog.get("schema_version") != AUTHORING_CATALOG_SCHEMA_VERSION
        or catalog.get("kind") != "safe_evaluation_source_catalog"
    ):
        raise EvaluationError("retained authoring catalog schema is invalid")
    if (
        packet.get("source_catalog_id")
        != f"sha256:{digest(canonical(catalog))}"
        or catalog.get("grader_tree_inventory") != grader_inventory
        or catalog.get("grader_tree_id")
        != f"sha256:{digest(canonical(grader_inventory))}"
    ):
        raise EvaluationError(
            "retained authoring packet grader provenance is invalid"
        )
    expected_compilation_contract = {
        "tool_policy_id": config["tool_policy_id"],
        "retention_policy_id": config["retention_policy_id"],
        "limits": config["limits"],
        "graders": template_suite["graders"],
        "case_runtime": config["case_runtime"],
        "rubric": catalog.get("rubric"),
        "executors": config["executors"],
        "comparator": config["comparator"],
    }
    expected_routing_contract = {
        "executors": [
            {
                key: route[key]
                for key in ("name", "adapter_id", "adapter_executable_sha256")
            }
            for route in routing["executors"]
        ],
        "comparator": {
            key: routing["comparator"][key]
            for key in ("route", "adapter_id", "adapter_executable_sha256")
        },
    }
    if (
        packet.get("compilation_contract") != expected_compilation_contract
        or packet.get("routing_contract") != expected_routing_contract
    ):
        raise EvaluationError(
            "retained authoring packet projected contracts are invalid"
        )
    fixture_values = catalog.get("fixtures")
    if not isinstance(fixture_values, list):
        raise EvaluationError(
            "retained authoring packet fixture provenance is invalid"
        )
    expected_fixtures = sorted(
        [
            {
                "path": item["path"],
                "sha256": item["sha256"],
                "size": item["size"],
            }
            for item in fixture_values
            if isinstance(item, dict)
            and {"path", "sha256", "size"} <= set(item)
        ],
        key=lambda item: item["path"],
    )
    if len(expected_fixtures) != len(fixture_values) or expected_fixtures != fixture_inventory:
        raise EvaluationError(
            "retained authoring packet fixture provenance is invalid"
        )
    fixture_ids: set[str] = set()
    for index, item in enumerate(fixture_values):
        field = f"retained authoring catalog.fixtures[{index}]"
        if not isinstance(item, dict):
            raise EvaluationError(f"{field} must be an object")
        require_exact_keys(
            item,
            field,
            {"id", "path", "sha256", "size", "source_kind", "description"},
        )
        fixture_id = require_text(item.get("id"), f"{field}.id")
        if (
            not GRADER_ID_RE.fullmatch(fixture_id)
            or fixture_id in fixture_ids
            or item.get("source_kind") not in AUTHORING_FIXTURE_SOURCE_KINDS
            or safe_registry_logical_path(item.get("path"), f"{field}.path")
            != item["path"]
            or require_sha256(item.get("sha256"), f"{field}.sha256")
            != item["sha256"]
            or require_nonnegative_int(item.get("size"), f"{field}.size")
            != item["size"]
            or item["size"] > AUTHORING_MAX_FIXTURE_BYTES
        ):
            raise EvaluationError(f"{field} is not a safe declared fixture")
        require_authoring_safe_text(
            item.get("description"),
            f"{field}.description",
            maximum=AUTHORING_MAX_DESCRIPTION_BYTES,
        )
        fixture_ids.add(fixture_id)
    if fixture_values != sorted(fixture_values, key=lambda item: item["id"]):
        raise EvaluationError(
            "retained authoring catalog fixtures are not in canonical order"
        )
    if not {
        item["fixture"] for item in config["case_runtime"]
    } <= fixture_ids:
        raise EvaluationError(
            "retained authoring catalog omits a runtime fixture"
        )
    grader_values = catalog.get("graders")
    if not isinstance(grader_values, list) or not grader_values:
        raise EvaluationError("retained authoring catalog graders are invalid")
    for index, item in enumerate(grader_values):
        field = f"retained authoring catalog.graders[{index}]"
        if not isinstance(item, dict):
            raise EvaluationError(f"{field} must be an object")
        require_exact_keys(item, field, {"id", "objective", "description"})
        if item.get("objective") is not True:
            raise EvaluationError(f"{field} must declare an objective grader")
        require_authoring_safe_text(
            item.get("description"),
            f"{field}.description",
            maximum=AUTHORING_MAX_DESCRIPTION_BYTES,
        )
    if [item.get("id") for item in grader_values] != [
        item["id"] for item in config["graders"]
    ]:
        raise EvaluationError(
            "retained authoring catalog graders differ from compilation"
        )
    rubric = catalog.get("rubric")
    if not isinstance(rubric, dict):
        raise EvaluationError("retained authoring catalog rubric is invalid")
    require_exact_keys(
        rubric, "retained authoring catalog.rubric", {"identity", "description"}
    )
    if rubric.get("identity") != f"sha256:{digest(canonical(config['rubric']))}":
        raise EvaluationError(
            "retained authoring catalog rubric differs from compilation"
        )
    require_authoring_safe_text(
        rubric.get("description"),
        "retained authoring catalog.rubric.description",
        maximum=AUTHORING_MAX_DESCRIPTION_BYTES,
    )
    reject_authoring_sensitive_value(catalog, "retained authoring catalog")
    reject_authoring_sensitive_value(template_suite, "retained suite template")
    reject_authoring_sensitive_value(policy, "retained policy contract")
    reject_authoring_sensitive_value(config, "retained compilation source")
    reject_authoring_sensitive_value(
        packet.get("compilation_contract"),
        "retained projected compilation contract",
    )
    reject_authoring_sensitive_value(
        packet.get("routing_contract"), "retained routing contract"
    )
    draft, draft_id = validate_input_author_draft_value(
        draft_value,
        packet,
        {
            "suite": template_suite,
            "config": {
                "case_runtime": packet["compilation_contract"]["case_runtime"]
            },
        },
    )
    expected_suite = materialized_authoring_suite(draft, template_suite)
    if suite != expected_suite or suite_id != f"sha256:{digest(canonical(expected_suite))}":
        raise EvaluationError(
            "retained authoring draft does not produce the registered suite"
        )
    require_exact_keys(
        receipt,
        "authoring materialization receipt",
        {
            "schema_version",
            "kind",
            "candidate_id",
            "packet_id",
            "draft_id",
            "suite_id",
            "source_catalog_id",
        },
    )
    expected_receipt = {
        "schema_version": AUTHORING_PACKET_SCHEMA_VERSION,
        "kind": "safe_evaluation_input_materialization",
        "candidate_id": candidate,
        "packet_id": packet_id,
        "draft_id": draft_id,
        "suite_id": suite_id,
        "source_catalog_id": packet["source_catalog_id"],
    }
    if receipt != expected_receipt:
        raise EvaluationError(
            "authoring materialization receipt does not bind the registered inputs"
        )
    operation = validate_authoring_operation(
        operation, packet, draft_id, adapter_sha256
    )
    return packet, draft, receipt, operation


def register_input_manifest(
    args: argparse.Namespace,
    trusted_authoring: tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        Path,
    ]
    | None = None,
) -> dict[str, Any]:
    skill_dir = resolve_path(Path(args.skill_dir), "skill directory")
    candidate, files = candidate_id(skill_dir)
    suite, suite_id = load_suite(resolve_path(Path(args.suite), "suite"))
    policy, policy_id = load_policy(resolve_path(Path(args.policy), "policy"))
    harness = require_trusted_harness(Path(args.harness))
    harness_sha = sha256_file(harness)
    config_path = resolve_path(Path(args.config), "compilation config")
    config, _ = validate_compilation_config(
        config_path, suite, policy, harness_sha
    )
    routing_path = resolve_path(Path(args.routing), "routing config")
    routing = validate_routing(
        routing_path, config["executors"], config["comparator"]
    )
    authoring_method = require_text(args.authoring_method, "authoring method")
    provenance_objects: list[dict[str, Any]] = []
    if trusted_authoring is None:
        if authoring_method == "bounded-safe-author":
            raise EvaluationError(
                "bounded-safe-author registration is evaluator-owned; use v2-input-author"
            )
        source_identities = list(args.source_id)
    else:
        if authoring_method != "bounded-safe-author":
            raise EvaluationError(
                "trusted authoring provenance requires bounded-safe-author"
            )
        packet, draft_value, receipt, operation, authoring_adapter = (
            trusted_authoring
        )
        authoring_adapter_sha = sha256_file(authoring_adapter)
        packet, draft_value, receipt, operation = validate_authoring_provenance(
            skill_dir,
            candidate,
            files,
            suite,
            suite_id,
            policy,
            policy_id,
            config,
            routing,
            harness_sha,
            packet,
            draft_value,
            receipt,
            operation,
            authoring_adapter_sha,
            canonical_file_inventory(config_path.parent / "fixtures"),
            canonical_file_inventory(config_path.parent / "graders"),
        )
        provenance_objects = [
            publish_registry_object(
                canonical(value), role, f"authoring/{name}.json", "application/json"
            )
            for value, role, name in (
                (packet, "authoring_packet", "packet"),
                (draft_value, "authoring_draft", "draft"),
                (receipt, "authoring_receipt", "materialization"),
                (operation, "authoring_operation", "operation"),
            )
        ]
        provenance_objects.append(
            publish_registry_object(
                authoring_adapter.read_bytes(),
                "authoring_adapter",
                "authoring/dreaming-vendor-adapter.py",
                "text/x-python",
            )
        )
        source_identities = [
            packet["packet_id"],
            f"sha256:{digest(canonical(draft_value))}",
            packet["source_catalog_id"],
            operation["operation_id"],
        ]
    objects = [
        publish_registry_object(
            canonical(suite), "suite", "suite.json", "application/json"
        ),
        publish_registry_object(
            canonical(policy), "policy", "policy.json", "application/json"
        ),
        publish_registry_object(
            canonical(config),
            "compilation",
            "compilation.json",
            "application/json",
        ),
        publish_registry_object(
            canonical(routing), "routing", "routing.json", "application/json"
        ),
        publish_registry_object(
            harness.read_bytes(),
            "harness",
            "harness/skill-evaluation-harness.py",
            "text/x-python",
        ),
        *provenance_objects,
    ]
    append_registry_tree_objects(
        objects, config_path.parent / "fixtures", "fixture"
    )
    append_registry_tree_objects(
        objects, config_path.parent / "graders", "grader"
    )
    objects.sort(key=lambda item: (item["role"], item["logical_path"]))
    by_role = {item["role"]: item for item in objects}
    manifest = {
        "schema_version": INPUT_REGISTRY_SCHEMA_VERSION,
        "kind": "evaluation_input_manifest",
        "skill_path": str(skill_dir),
        "skill_key": latest_key(str(skill_dir)),
        "candidate_id": candidate,
        "candidate_inventory": files,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "observation_plan_id": observation_plan_identity(policy, policy_id),
        "complete_policy_sha256": by_role["policy"]["sha256"],
        "compilation_sha256": by_role["compilation"]["sha256"],
        "routing_sha256": by_role["routing"]["sha256"],
        "fixture_set_sha256": f"sha256:{digest(canonical(config['case_runtime']))}",
        "grader_set_sha256": f"sha256:{digest(canonical(config['graders']))}",
        "rubric_sha256": f"sha256:{digest(canonical(config['rubric']))}",
        "tool_policy_id": config["tool_policy_id"],
        "harness_executable_sha256": harness_sha,
        "authoring_method": authoring_method,
        "source_identities": source_identities,
        "tool_version": RUNNER_VERSION,
        "objects": objects,
    }
    path, manifest_sha256 = write_input_registry_json(
        "manifests", manifest, "input manifest"
    )
    validate_input_manifest(skill_dir, manifest_sha256)
    return {
        "candidate_id": candidate,
        "input_manifest": str(path),
        "input_manifest_sha256": manifest_sha256,
    }


def v2_input_register(args: argparse.Namespace) -> dict[str, Any]:
    if any(
        getattr(args, name, None)
        for name in (
            "authoring_packet",
            "authoring_draft",
            "authoring_receipt",
            "authoring_operation",
        )
    ):
        raise EvaluationError(
            "caller-supplied authoring provenance is not accepted; use v2-input-author"
        )
    return register_input_manifest(args)


def input_receipt_binding(
    resolved: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    manifest = resolved["manifest"]
    return {
        "schema_version": INPUT_REGISTRY_SCHEMA_VERSION,
        "kind": kind,
        "skill_path": manifest["skill_path"],
        "skill_key": manifest["skill_key"],
        "candidate_id": manifest["candidate_id"],
        "input_manifest_sha256": resolved["input_manifest_sha256"],
        "object_inventory": manifest["objects"],
    }


def v2_input_validate(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = resolve_path(Path(args.skill_dir), "skill directory")
    resolved = validate_input_manifest(
        skill_dir, require_sha256(args.manifest, "input manifest digest")
    )
    receipt = {
        **input_receipt_binding(resolved, "evaluation_input_validation"),
        "status": "pass",
        "suite_id": resolved["suite_id"],
        "policy_id": resolved["policy_id"],
        "observation_plan_id": resolved["manifest"]["observation_plan_id"],
        "validator": RUNNER_VERSION,
    }
    path, receipt_sha256 = write_input_registry_json(
        "reviews", receipt, "input validation receipt"
    )
    return {
        "status": "pass",
        "receipt": str(path),
        "receipt_sha256": receipt_sha256,
        "input_manifest_sha256": resolved["input_manifest_sha256"],
    }


def v2_input_review(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = resolve_path(Path(args.skill_dir), "skill directory")
    resolved = validate_input_manifest(
        skill_dir, require_sha256(args.manifest, "input manifest digest")
    )
    bounded = is_bounded_input_manifest(resolved["manifest"])
    if bounded:
        expected_slots = (
            {"rereview_a", "rereview_b"}
            if resolved["manifest"]["authoring_method"] == "bounded-safe-repair"
            else {"review_a", "review_b"}
        )
        if args.slot not in expected_slots:
            raise EvaluationError(
                f"input review slot {args.slot} does not match this manifest generation"
            )
        if (
            not args.claim_id
            or not args.validation
            or not args.model
            or args.model == "default"
            or args.reviewer
            or args.decision
        ):
            raise EvaluationError(
                "bounded-safe-author review requires claim, slot, validation, and exact model identity"
            )
        validation_sha256 = require_sha256(
            args.validation, "input review validation receipt"
        )
        require_positive_int(args.timeout, "input review timeout")
        require_positive_int(args.token_budget, "input review token budget")
        require_positive_int(args.output_bytes, "input review output-byte budget")
        if (
            args.timeout > 25 * 60
            or args.token_budget > 112_000
            or args.output_bytes > 1_000_000
        ):
            raise EvaluationError("input review process budget exceeds its hard bound")
        packet = build_input_review_packet(
            skill_dir,
            resolved["input_manifest_sha256"],
            validation_sha256,
        )
        adapter = trusted_authoring_adapter_path()
        adapter_sha256 = sha256_file(adapter)
        dispatch = prepare_claim_dispatch(
            claim_id=require_sha256(args.claim_id, "claim ID"),
            skill_path=str(skill_dir),
            skill_key=latest_key(str(skill_dir)),
            candidate_id=resolved["candidate_id"],
            slot_name=args.slot,
            model=require_text(args.model, "input review model"),
            packet_id=packet["packet_id"],
            manifest_sha256=resolved["input_manifest_sha256"],
            validation_receipt_sha256=validation_sha256,
            requested_token_budget=args.token_budget,
            requested_timeout_seconds=args.timeout,
        )
        trusted_args = argparse.Namespace(**vars(args))
        trusted_args.token_budget = dispatch["token_budget"]
        trusted_args.timeout = dispatch["timeout_seconds"]
        phase = "model_execution"
        try:
            operation = run_trusted_input_review(
                trusted_args,
                skill_dir,
                resolved["input_manifest_sha256"],
                packet,
                adapter,
            )
            phase = "result_validation"
            operation = validate_input_review_operation(
                operation, packet, adapter_sha256
            )
            author_operation = packet["authoring_contract"]["operation"]
            if operation["observed_model"] == author_operation["observed_model"]:
                raise EvaluationError(
                    "input reviewer model must differ from the author model"
                )
            phase = "materialization"
            packet_entry = publish_registry_object(
                canonical(packet),
                "input_review_packet",
                "reviews/packet.json",
                "application/json",
            )
            adapter_entry = publish_registry_object(
                adapter.read_bytes(),
                "input_review_adapter",
                "reviews/dreaming-vendor-adapter.py",
                "text/x-python",
            )
            receipt = {
                **input_receipt_binding(resolved, "evaluation_input_review"),
                "reviewer": f"copilot:{operation['observed_model']}",
                "decision": operation["decision"],
                "review_packet": packet_entry,
                "review_operation": operation,
                "review_adapter": adapter_entry,
            }
            path, receipt_sha256 = write_input_registry_json(
                "reviews", receipt, "input review receipt"
            )
            complete_claim_slot(
                claim_id=args.claim_id,
                slot_name=args.slot,
                operation=operation,
                manifest_sha256=resolved["input_manifest_sha256"],
                review_receipt_sha256=receipt_sha256,
                decision=operation["decision"],
            )
        except (
            EvaluationError,
            OSError,
            subprocess.SubprocessError,
            json.JSONDecodeError,
        ) as error:
            fail_dispatched_slot(
                args.claim_id,
                args.slot,
                claim_failure_reason(phase, error),
            )
            raise
        return {
            "claim_id": args.claim_id,
            "slot": args.slot,
            "decision": receipt["decision"],
            "receipt": str(path),
            "receipt_sha256": receipt_sha256,
            "input_manifest_sha256": resolved["input_manifest_sha256"],
        }
    if (
        args.claim_id
        or args.slot
        or args.validation
        or args.model
        or not args.reviewer
        or not args.decision
    ):
        raise EvaluationError(
            "manual input review requires reviewer and decision only"
        )
    receipt = {
        **input_receipt_binding(resolved, "evaluation_input_review"),
        "reviewer": require_text(args.reviewer, "reviewer"),
        "decision": args.decision,
    }
    path, receipt_sha256 = write_input_registry_json(
        "reviews", receipt, "input review receipt"
    )
    return {
        "decision": receipt["decision"],
        "receipt": str(path),
        "receipt_sha256": receipt_sha256,
        "input_manifest_sha256": resolved["input_manifest_sha256"],
    }


def run_trusted_input_review(
    args: argparse.Namespace,
    skill_dir: Path,
    manifest_sha256: str,
    packet: dict[str, Any],
    adapter: Path,
) -> dict[str, Any]:
    operation, _ = run_trusted_input_adapter(
        args,
        skill_dir,
        Path(args.skill_dir),
        packet,
        adapter,
        "review",
        [
            "--manifest",
            manifest_sha256,
            "--validation",
            packet["validation_contract"]["receipt_sha256"],
        ],
    )
    return operation


def path_has_symlink(path: Path) -> bool:
    current = Path(os.path.abspath(path))
    while True:
        if current.is_symlink():
            return True
        if current.parent == current:
            return False
        current = current.parent


def trusted_model_path() -> str:
    return os.pathsep.join(
        dict.fromkeys(
            (
                str(Path(sys.executable).resolve().parent),
                "/opt/homebrew/bin",
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
                "/usr/sbin",
                "/sbin",
            )
        )
    )


def trusted_input_test_binary(
    skill_dir: Path, supplied_skill_dir: Path
) -> Path | None:
    allow_value = os.environ.get("DREAMING_EXECUTOR_TEST_ALLOW_ROOT")
    if not allow_value:
        return None
    test_root = Path(__file__).resolve().parents[3] / ".test-work"
    allow_path = Path(allow_value)
    state_value = os.environ.get("SKILLS_STATE_DIR")
    binary_value = os.environ.get("DREAMING_COPILOT_BIN")
    if not state_value or not binary_value:
        raise EvaluationError(
            "trusted input test override requires isolated state and binary"
        )
    state_path = Path(state_value)
    binary_path = Path(binary_value)
    allow_root = allow_path.resolve()
    state = state_path.resolve()
    binary = binary_path.resolve()
    if (
        path_has_symlink(allow_path)
        or path_has_symlink(state_path)
        or path_has_symlink(binary_path)
        or path_has_symlink(supplied_skill_dir)
        or allow_root != test_root
        or state_path.is_symlink()
        or binary_path.is_symlink()
        or not state.is_relative_to(test_root)
        or not skill_dir.is_relative_to(test_root)
        or not binary.is_relative_to(test_root)
        or not binary.is_file()
        or not os.access(binary, os.X_OK)
    ):
        raise EvaluationError(
            "trusted input test override is limited to non-authoritative test roots"
        )
    return binary


def trusted_model_state_root() -> Path:
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    supplied = Path(
        os.environ.get(
            "SKILLS_STATE_DIR", str(real_home / ".copilot/skill-state")
        )
    )
    if path_has_symlink(supplied):
        raise EvaluationError("trusted model registry state must not use symlinks")
    return supplied.resolve()


def trusted_model_launch_environment(
    work_path: Path, test_binary: Path | None
) -> dict[str, str]:
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    temporary = work_path / "tmp"
    temporary.mkdir()
    required, advisory = desired_executor_roles()
    environment = {
        "HOME": str(real_home),
        "PATH": trusted_model_path(),
        "TMPDIR": str(temporary),
        "LANG": "C",
        "LC_ALL": "C",
        "LC_CTYPE": "C",
        "SKILLS_STATE_DIR": str(trusted_model_state_root()),
        "DREAMING_EVALUATION_EXECUTORS": ",".join(required),
        "DREAMING_ADVISORY_EVALUATION_EXECUTORS": ",".join(advisory),
        "DREAMING_TRUSTED_MODEL_ENVIRONMENT_VERSION": str(
            TRUSTED_MODEL_ENVIRONMENT_VERSION
        ),
    }
    if test_binary is not None:
        environment["DREAMING_EXECUTOR_TEST_ALLOW_ROOT"] = str(
            Path(__file__).resolve().parents[3] / ".test-work"
        )
    return environment


def trusted_copilot_binary(test_binary: Path | None) -> Path:
    if test_binary is not None:
        return test_binary
    selected = shutil.which("copilot", path=trusted_model_path())
    if not selected:
        raise EvaluationError("trusted Copilot executable is unavailable")
    binary = Path(selected).resolve()
    if not binary.is_file():
        raise EvaluationError("trusted Copilot executable is unavailable")
    return binary


def trusted_model_work_parent() -> Path:
    state_root = trusted_model_state_root()
    expected_evaluation_root = (
        state_root / "skill-review" / "evaluations" / "v2"
    )
    if v2_evaluation_dir().resolve() != expected_evaluation_root:
        raise EvaluationError(
            "trusted model registry state differs from evaluator state"
        )
    parent = expected_evaluation_root / "trusted-model-work"
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink() or not parent.is_dir():
        raise EvaluationError("trusted model work root must be a real directory")
    return parent


def run_trusted_input_adapter(
    args: argparse.Namespace,
    skill_dir: Path,
    supplied_skill_dir: Path,
    packet: dict[str, Any],
    adapter: Path,
    operation_name: str,
    source_arguments: list[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    test_binary = trusted_input_test_binary(skill_dir, supplied_skill_dir)
    binary = trusted_copilot_binary(test_binary)
    with tempfile.TemporaryDirectory(
        prefix=f"dreaming-input-{operation_name}-owner-",
        dir=trusted_model_work_parent(),
    ) as work:
        work_path = Path(work).resolve()
        packet_path = work_path / "packet.json"
        result_path = work_path / "operation.json"
        draft_path = work_path / "draft.json"
        packet_path.write_bytes(canonical(packet))
        command = [
            sys.executable,
            str(adapter),
            "--vendor",
            "copilot",
            "--role",
            "evaluation-input-author",
            "--model",
            require_text(args.model, f"input {operation_name} model"),
            "--timeout",
            str(args.timeout),
            "--token-budget",
            str(args.token_budget),
            "--output-bytes",
            str(args.output_bytes),
            "--binary",
            str(binary),
            "run",
            "--operation",
            operation_name,
            "--packet",
            str(packet_path),
            "--skill-dir",
            str(skill_dir),
            "--result",
            str(result_path),
        ]
        if operation_name in {"author", "repair"}:
            command.extend(["--draft-output", str(draft_path)])
        command.extend(source_arguments)
        try:
            completed = subprocess.run(
                command,
                cwd=work_path,
                env=trusted_model_launch_environment(work_path, test_binary),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=args.timeout + 120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EvaluationError(
                f"trusted input {operation_name} execution failed: {exc}"
            ) from exc
        if (
            completed.returncode != 0
            or not result_path.is_file()
            or result_path.is_symlink()
        ):
            detail = (completed.stderr or completed.stdout).strip()[-1000:]
            raise EvaluationError(
                f"trusted input {operation_name} refused"
                + (f": {detail}" if detail else "")
            )
        operation = load_json(result_path)
        draft = None
        if draft_path.exists():
            if draft_path.is_symlink() or not draft_path.is_file():
                raise EvaluationError(
                    "trusted input author draft must be a regular file"
                )
            draft = load_json(draft_path)
        return operation, draft


def validate_input_model_budget(args: argparse.Namespace, field: str) -> None:
    require_positive_int(args.timeout, f"{field} timeout")
    require_positive_int(args.token_budget, f"{field} token budget")
    require_positive_int(args.output_bytes, f"{field} output-byte budget")
    if (
        args.timeout > 25 * 60
        or args.token_budget > 112_000
        or args.output_bytes > 1_000_000
    ):
        raise EvaluationError(f"{field} process budget exceeds its hard bound")


def v2_input_claim(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = resolve_path(Path(args.skill_dir), "skill directory")
    candidate, _ = candidate_id(skill_dir)
    return reserve_claim(
        skill_path=str(skill_dir),
        skill_key=latest_key(str(skill_dir)),
        candidate_id=candidate,
        owner_run_id=require_text(args.owner_run_id, "claim owner run ID"),
        author_model=require_text(args.author_model, "claim author model"),
        reviewer_a_model=require_text(
            args.reviewer_a_model, "claim reviewer A model"
        ),
        reviewer_b_model=require_text(
            args.reviewer_b_model, "claim reviewer B model"
        ),
    )


def v2_input_claim_inspect(args: argparse.Namespace) -> dict[str, Any]:
    return inspect_claim(require_sha256(args.claim_id, "claim ID"))


def claim_failure_reason(phase: str, error: BaseException) -> str:
    detail = str(error).lower()
    if phase == "model_execution":
        if "timed out" in detail or "timeout" in detail:
            return "trusted_operation_timeout"
        if "refused" in detail:
            return "trusted_operation_refused"
        return "trusted_operation_crash"
    if phase == "result_validation":
        return "trusted_operation_malformed"
    return "trusted_materialization_failed"


def v2_input_author(args: argparse.Namespace) -> dict[str, Any]:
    if args.slot != "author":
        raise EvaluationError(
            f"input author slot {args.slot} is reserved for a later slice"
        )
    model = require_text(args.model, "input author model")
    if model == "default":
        raise EvaluationError("input author requires an explicit non-default model")
    validate_input_model_budget(args, "input author")
    packet, context = build_input_author_packet(args)
    adapter = trusted_authoring_adapter_path()
    adapter_sha256 = sha256_file(adapter)
    output = authoring_output_path(
        args.output_dir,
        context["skill_dir"],
        "authoring materialization output",
    )
    test_binary = trusted_input_test_binary(
        context["skill_dir"], Path(args.skill_dir)
    )
    if test_binary is not None and (
        path_has_symlink(Path(args.output_dir))
        or not output.is_relative_to(
            Path(__file__).resolve().parents[3] / ".test-work"
        )
    ):
        raise EvaluationError(
            "trusted input test materialization is limited to non-authoritative test roots"
        )
    source_arguments: list[str] = []
    for option in (
        "suite",
        "policy",
        "config",
        "routing",
        "harness",
        "catalog",
    ):
        source_arguments.extend(
            [f"--{option}", str(resolve_path(Path(getattr(args, option)), option))]
        )
    dispatch = prepare_claim_dispatch(
        claim_id=require_sha256(args.claim_id, "claim ID"),
        skill_path=str(context["skill_dir"]),
        skill_key=latest_key(str(context["skill_dir"])),
        candidate_id=packet["candidate_id"],
        slot_name="author",
        model=model,
        packet_id=packet["packet_id"],
        manifest_sha256=None,
        validation_receipt_sha256=None,
        requested_token_budget=args.token_budget,
        requested_timeout_seconds=args.timeout,
    )
    trusted_args = argparse.Namespace(**vars(args))
    trusted_args.token_budget = dispatch["token_budget"]
    trusted_args.timeout = dispatch["timeout_seconds"]
    phase = "model_execution"
    try:
        operation, draft_value = run_trusted_input_adapter(
            trusted_args,
            context["skill_dir"],
            Path(args.skill_dir),
            packet,
            adapter,
            "author",
            source_arguments,
        )
        phase = "result_validation"
        if operation.get("model") != model:
            raise EvaluationError(
                "trusted input author returned the wrong requested model"
            )
        if operation.get("outcome") == "insufficient_information":
            if draft_value is not None:
                raise EvaluationError(
                    "insufficient-information authoring returned an unexpected draft"
                )
            operation = validate_authoring_operation(
                operation,
                packet,
                None,
                adapter_sha256,
                expected_outcome="insufficient_information",
            )
            complete_claim_slot(
                claim_id=args.claim_id,
                slot_name="author",
                operation=operation,
                manifest_sha256=None,
                terminal_reason="insufficient_information",
            )
            return {
                "status": "insufficient_information",
                "state": "insufficient_information",
                "reason": operation["reason"],
                "summary": operation["summary"],
                "candidate_id": packet["candidate_id"],
                "packet_id": packet["packet_id"],
                "operation_id": operation["operation_id"],
                "claim_id": args.claim_id,
                "input_manifest": None,
                "input_manifest_sha256": None,
            }
        if draft_value is None:
            raise EvaluationError("trusted input author returned no draft")
        draft_value, draft_id = validate_input_author_draft_value(
            draft_value, packet, context
        )
        operation = validate_authoring_operation(
            operation, packet, draft_id, adapter_sha256
        )
        phase = "materialization"
        materialization = materialize_input_author(
            packet, context, draft_value, str(output)
        )
        receipt = load_json(
            Path(materialization["output_dir"]) / "authoring.json"
        )
        registration_args = argparse.Namespace(
            skill_dir=str(context["skill_dir"]),
            suite=materialization["suite"],
            policy=materialization["policy"],
            config=materialization["config"],
            routing=materialization["routing"],
            harness=str(trusted_harness_path()),
            authoring_method="bounded-safe-author",
            source_id=[],
        )
        registration = register_input_manifest(
            registration_args,
            (packet, draft_value, receipt, operation, adapter),
        )
        complete_claim_slot(
            claim_id=args.claim_id,
            slot_name="author",
            operation=operation,
            manifest_sha256=registration["input_manifest_sha256"],
        )
    except (
        EvaluationError,
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as error:
        fail_dispatched_slot(
            args.claim_id, "author", claim_failure_reason(phase, error)
        )
        raise
    return {
        "status": "review_required",
        "state": "review_required",
        "outcome": "draft",
        "claim_id": args.claim_id,
        **materialization,
        **registration,
        "author_operation_id": operation["operation_id"],
        "author_model": operation["observed_model"],
    }


def initial_authoring_contract(
    resolved: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    objects = resolved["manifest"]["objects"]
    return (
        load_registry_json_object(
            manifest_role(objects, "authoring_packet"), "authoring packet"
        ),
        load_registry_json_object(
            manifest_role(objects, "authoring_draft"), "authoring draft"
        ),
        load_registry_json_object(
            manifest_role(objects, "authoring_receipt"), "authoring receipt"
        ),
        load_registry_json_object(
            manifest_role(objects, "authoring_operation"), "authoring operation"
        ),
    )


def build_input_repair_packet(
    skill_dir: Path,
    claim_id: str,
    initial_manifest_sha256: str,
    validation_sha256: str,
    review_sha256s: list[str],
    original_author_model: str,
    *,
    require_active_claim: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved = validate_input_manifest(skill_dir, initial_manifest_sha256)
    if resolved["manifest"]["authoring_method"] != "bounded-safe-author":
        raise EvaluationError("repair requires an initial bounded-safe-author manifest")
    validate_input_validation_receipt(resolved, validation_sha256)
    validation = load_input_registry_receipt(validation_sha256)
    review_sha256s = sorted(
        require_sha256(value, "initial review receipt")
        for value in review_sha256s
    )
    decisions = validate_input_review_receipts(resolved, review_sha256s)
    if len(decisions) != 2 or "reject" not in decisions:
        raise EvaluationError(
            "repair requires exactly two initial reviews with at least one rejection"
        )
    authoring_packet, authoring_draft, materialization, author_operation = (
        initial_authoring_contract(resolved)
    )
    retained_author_model = require_text(
        author_operation.get("observed_model"), "original author model"
    )
    if original_author_model != retained_author_model:
        raise EvaluationError(
            "repair original author model differs from retained author provenance"
        )
    review_history: list[dict[str, Any]] = []
    reviewer_models: list[str] = []
    for receipt_sha256 in review_sha256s:
        receipt = load_input_registry_receipt(receipt_sha256)
        operation = receipt["review_operation"]
        model = require_text(
            operation.get("observed_model"), "original reviewer model"
        )
        reviewer_models.append(model)
        review_history.append(
            {
                "receipt_sha256": receipt_sha256,
                "reviewer_model": model,
                "decision": operation["decision"],
                "reason": operation["reason"],
                "summary": require_authoring_safe_text(
                    operation["summary"],
                    "original review summary",
                    maximum=AUTHORING_MAX_DESCRIPTION_BYTES,
                ),
            }
        )
    reviewer_models.sort()
    try:
        review_set_id = review_set_identity(
            require_sha256(claim_id, "claim ID"),
            resolved["candidate_id"],
            resolved["input_manifest_sha256"],
            retained_author_model,
            reviewer_models,
        )
    except ClaimLedgerError as error:
        raise EvaluationError(str(error)) from error
    if require_active_claim:
        try:
            claim = inspect_claim(require_sha256(claim_id, "claim ID"))
        except ClaimLedgerError as error:
            raise EvaluationError(str(error)) from error
        initial_slots = claim["slots"][:3]
        claim_review_slots = claim["slots"][1:3]
        if (
            claim["status"] not in {"open", "completed"}
            or (
                claim["status"] == "completed"
                and claim["terminal_reason"] != "ready"
            )
            or claim["skill_path"] != resolved["manifest"]["skill_path"]
            or claim["skill_key"] != resolved["manifest"]["skill_key"]
            or claim["candidate_id"] != resolved["candidate_id"]
            or claim["initial_manifest_sha256"]
            != resolved["input_manifest_sha256"]
            or claim["review_set_id"] != review_set_id
            or claim["models"]["author"] != retained_author_model
            or sorted(
                [claim["models"]["reviewer_a"], claim["models"]["reviewer_b"]]
            )
            != reviewer_models
            or len(initial_slots) != 3
            or any(
                slot["status"] != "completed"
                or slot["usage_status"] != "available"
                for slot in initial_slots
            )
            or sorted(
                slot["review_receipt_sha256"] for slot in claim_review_slots
            )
            != review_sha256s
            or {
                slot["validation_receipt_sha256"] for slot in claim_review_slots
            }
            != {validation_sha256}
            or "reject"
            not in {slot["decision"] for slot in claim_review_slots}
        ):
            raise EvaluationError(
                "repair claim, manifest, review set, validation, or receipt lineage is invalid"
            )
    packet = {
        "schema_version": AUTHORING_PACKET_SCHEMA_VERSION,
        "kind": "safe_evaluation_input_repair_packet",
        "claim_id": require_sha256(claim_id, "claim ID"),
        "candidate_id": resolved["candidate_id"],
        "candidate_inventory": resolved["candidate_inventory"],
        "initial_manifest_sha256": resolved["input_manifest_sha256"],
        "initial_validation_contract": {
            "receipt_sha256": validation_sha256,
            "status": validation["status"],
            "validator": validation["validator"],
        },
        "initial_review_receipt_sha256s": review_sha256s,
        "review_set_id": review_set_id,
        "original_author_model": retained_author_model,
        "original_reviewer_models": reviewer_models,
        "skill_contract": authoring_packet["skill_contract"],
        "initial_suite": resolved["suite"],
        "source_catalog": authoring_packet["source_catalog"],
        "policy_contract": authoring_packet["policy_contract"],
        "compilation_contract": authoring_packet["compilation_contract"],
        "routing_contract": authoring_packet["routing_contract"],
        "authoring_contract": {
            "packet_id": authoring_packet["packet_id"],
            "draft_id": f"sha256:{digest(canonical(authoring_draft))}",
            "materialization_id": f"sha256:{digest(canonical(materialization))}",
            "operation_id": author_operation["operation_id"],
            "observed_model": retained_author_model,
        },
        "review_history": review_history,
        "repair_contract": {
            "allowed_case_changes": ["prompt", "task_id"],
            "fixed_fields": [
                "id",
                "class",
                "deterministic_graders",
                "fixture",
                "artifacts",
                "semantic",
            ],
            "insufficient_information_allowed": True,
        },
    }
    reject_authoring_sensitive_value(packet, "model-facing repair packet")
    packet["packet_id"] = f"sha256:{digest(canonical(packet))}"
    return packet, {
        "skill_dir": resolve_path(skill_dir, "skill directory"),
        "suite": resolved["suite"],
        "policy": resolved["policy"],
        "config": resolved["config"],
        "routing": resolved["routing"],
        "harness_sha": resolved["manifest"]["harness_executable_sha256"],
        "initial": resolved,
    }


def v2_input_repair_packet(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = resolve_path(Path(args.skill_dir), "skill directory")
    packet, _ = build_input_repair_packet(
        skill_dir,
        require_sha256(args.claim_id, "claim ID"),
        require_sha256(args.manifest, "initial manifest"),
        require_sha256(args.validation, "initial validation receipt"),
        list(args.review),
        require_text(args.original_author_model, "original author model"),
    )
    output = authoring_output_path(
        args.output, skill_dir, "input repair packet output"
    )
    atomic_write(output, packet)
    return {
        "candidate_id": packet["candidate_id"],
        "initial_manifest_sha256": packet["initial_manifest_sha256"],
        "review_set_id": packet["review_set_id"],
        "packet_id": packet["packet_id"],
        "output": str(output),
    }


def validate_repair_operation(
    operation: dict[str, Any],
    packet: dict[str, Any],
    draft_id: str | None,
    adapter_sha256: str,
    *,
    expected_outcome: str = "draft",
) -> dict[str, Any]:
    require_exact_keys(
        operation,
        "repair operation",
        {
            "schema_version",
            "kind",
            "operation",
            "status",
            "vendor",
            "model",
            "observed_model",
            "adapter_executable_sha256",
            "packet_id",
            "candidate_id",
            "initial_manifest_sha256",
            "validation_receipt_sha256",
            "review_set_id",
            "original_review_receipt_sha256s",
            "outcome",
            "summary",
            "reason",
            "draft_id",
            "usage",
            "billing",
            "elapsed_ms",
            "operation_id",
        },
    )
    model = require_text(operation.get("model"), "repair operation.model")
    outcome_valid = (
        expected_outcome == "draft"
        and operation.get("outcome") == "draft"
        and operation.get("reason") is None
        and operation.get("draft_id") == draft_id
    ) or (
        expected_outcome == "insufficient_information"
        and operation.get("outcome") == "insufficient_information"
        and operation.get("reason")
        in {
            "evaluation_case_unavailable",
            "safe_fixture_unavailable",
            "objective_grader_unavailable",
        }
        and operation.get("draft_id") is None
        and draft_id is None
    )
    if (
        operation.get("schema_version") != AUTHORING_PACKET_SCHEMA_VERSION
        or operation.get("kind") != "evaluation_input_model_operation"
        or operation.get("operation") != "repair"
        or operation.get("status") != "completed"
        or operation.get("vendor") != "copilot"
        or model != packet["original_author_model"]
        or operation.get("observed_model") != model
        or operation.get("adapter_executable_sha256") != adapter_sha256
        or operation.get("packet_id") != packet["packet_id"]
        or operation.get("candidate_id") != packet["candidate_id"]
        or operation.get("initial_manifest_sha256")
        != packet["initial_manifest_sha256"]
        or operation.get("validation_receipt_sha256")
        != packet["initial_validation_contract"]["receipt_sha256"]
        or operation.get("review_set_id") != packet["review_set_id"]
        or operation.get("original_review_receipt_sha256s")
        != packet["initial_review_receipt_sha256s"]
        or not outcome_valid
    ):
        raise EvaluationError(
            "repair operation identity, lineage, outcome, or draft binding is invalid"
        )
    require_authoring_safe_text(
        operation.get("summary"),
        "repair operation.summary",
        maximum=AUTHORING_MAX_DESCRIPTION_BYTES,
    )
    usage = operation.get("usage")
    if not isinstance(usage, dict):
        raise EvaluationError("repair operation.usage must be an object")
    require_exact_keys(
        usage,
        "repair operation.usage",
        {"normalized_tokens", "input_tokens", "output_tokens"},
    )
    normalized_tokens = require_positive_int(
        usage.get("normalized_tokens"),
        "repair operation.usage.normalized_tokens",
    )
    if normalized_tokens > 112_000:
        raise EvaluationError("repair operation exceeds the normalized-token budget")
    detailed = [
        require_nonnegative_int(
            usage.get(field), f"repair operation.usage.{field}"
        )
        for field in ("input_tokens", "output_tokens")
        if usage.get(field) is not None
    ]
    if detailed and (len(detailed) != 2 or sum(detailed) != normalized_tokens):
        raise EvaluationError(
            "repair operation detailed usage does not match normalized tokens"
        )
    expected_billing = {
        "status": "unavailable",
        "cost_usd": None,
        "provider": "copilot",
        "unavailable_reason": "provider_telemetry_unavailable",
        "native_line_item_id": None,
        "native_event_sha256": None,
        "native_event_size": None,
    }
    if operation.get("billing") != expected_billing:
        raise EvaluationError("repair operation billing telemetry is invalid")
    elapsed_ms = require_nonnegative_int(
        operation.get("elapsed_ms"), "repair operation.elapsed_ms"
    )
    if elapsed_ms > 25 * 60 * 1000:
        raise EvaluationError("repair operation exceeds the elapsed-time budget")
    operation_without_id = {
        key: value for key, value in operation.items() if key != "operation_id"
    }
    if operation.get("operation_id") != (
        f"sha256:{digest(shadow_canonical(operation_without_id))}"
    ):
        raise EvaluationError("repair operation content identity is invalid")
    return operation


def materialize_input_repair(
    packet: dict[str, Any],
    context: dict[str, Any],
    draft_value: dict[str, Any],
    output_dir: str,
) -> dict[str, Any]:
    draft, draft_id = validate_input_author_draft_value(
        draft_value, packet, context
    )
    output = authoring_output_path(
        output_dir, context["skill_dir"], "repair materialization output"
    )
    parent = output.parent
    if parent.is_symlink() or not parent.is_dir():
        raise EvaluationError("repair materialization parent must be a real directory")
    suite = materialized_authoring_suite(draft, context["suite"])
    suite_id = f"sha256:{digest(canonical(suite))}"
    initial_objects = context["initial"]["manifest"]["objects"]
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=parent))
    try:
        atomic_write(staging / "suite.json", suite)
        for role, name in (
            ("policy", "policy.json"),
            ("compilation", "compilation.json"),
            ("routing", "routing.json"),
        ):
            entry = manifest_role(initial_objects, role)
            (staging / name).write_bytes(
                require_registry_file(
                    registry_object_path(entry["sha256"]),
                    input_registry_component("objects"),
                    f"retained {role}",
                )
            )
        for entry in initial_objects:
            if entry["role"] not in {"fixture", "grader"}:
                continue
            prefix = f"{entry['role']}s/"
            if not entry["logical_path"].startswith(prefix):
                raise EvaluationError("retained repair tree path is malformed")
            relative = safe_registry_logical_path(
                entry["logical_path"].removeprefix(prefix),
                f"retained {entry['role']} path",
            )
            destination = staging / f"{entry['role']}s" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(
                require_registry_file(
                    registry_object_path(entry["sha256"]),
                    input_registry_component("objects"),
                    f"retained {entry['role']} object",
                )
            )
        atomic_write(
            staging / "repair.json",
            {
                "schema_version": AUTHORING_PACKET_SCHEMA_VERSION,
                "kind": "safe_evaluation_input_repair_materialization",
                "candidate_id": packet["candidate_id"],
                "initial_manifest_sha256": packet["initial_manifest_sha256"],
                "claim_id": packet["claim_id"],
                "review_set_id": packet["review_set_id"],
                "original_review_receipt_sha256s": packet[
                    "initial_review_receipt_sha256s"
                ],
                "packet_id": packet["packet_id"],
                "draft_id": draft_id,
                "suite_id": suite_id,
            },
        )
        staged_suite, staged_suite_id = load_suite(staging / "suite.json")
        if staged_suite != suite or staged_suite_id != suite_id:
            raise EvaluationError("repaired suite differs from its trusted form")
        validate_compilation_config(
            staging / "compilation.json",
            staged_suite,
            context["policy"],
            context["harness_sha"],
        )
        validate_routing(
            staging / "routing.json",
            context["config"]["executors"],
            context["config"]["comparator"],
        )
        os.replace(staging, output)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return {
        "candidate_id": packet["candidate_id"],
        "packet_id": packet["packet_id"],
        "draft_id": draft_id,
        "suite_id": suite_id,
        "output_dir": str(output),
        "suite": str(output / "suite.json"),
        "policy": str(output / "policy.json"),
        "config": str(output / "compilation.json"),
        "routing": str(output / "routing.json"),
    }


def register_repaired_input_manifest(
    context: dict[str, Any],
    packet: dict[str, Any],
    draft: dict[str, Any],
    operation: dict[str, Any],
    adapter: Path,
) -> dict[str, Any]:
    initial = context["initial"]
    repaired_suite = materialized_authoring_suite(draft, context["suite"])
    objects = [
        item for item in initial["manifest"]["objects"] if item["role"] != "suite"
    ]
    objects.append(
        publish_registry_object(
            canonical(repaired_suite), "suite", "suite.json", "application/json"
        )
    )
    for value, role, name in (
        (packet, "repair_packet", "packet"),
        (draft, "repair_draft", "draft"),
        (operation, "repair_operation", "operation"),
    ):
        objects.append(
            publish_registry_object(
                canonical(value), role, f"repair/{name}.json", "application/json"
            )
        )
    objects.append(
        publish_registry_object(
            adapter.read_bytes(),
            "repair_adapter",
            "repair/dreaming-vendor-adapter.py",
            "text/x-python",
        )
    )
    objects.sort(key=lambda item: (item["role"], item["logical_path"]))
    by_role = {item["role"]: item for item in objects}
    manifest = {
        **{
            key: value
            for key, value in initial["manifest"].items()
            if key
            not in {
                "suite_id",
                "authoring_method",
                "source_identities",
                "objects",
            }
        },
        "suite_id": f"sha256:{digest(canonical(repaired_suite))}",
        "authoring_method": "bounded-safe-repair",
        "source_identities": [
            packet["claim_id"],
            packet["initial_manifest_sha256"],
            packet["review_set_id"],
            *packet["initial_review_receipt_sha256s"],
            packet["packet_id"],
            f"sha256:{digest(canonical(draft))}",
            operation["operation_id"],
        ],
        "repair_lineage": {
            "initial_manifest_sha256": packet["initial_manifest_sha256"],
            "claim_id": packet["claim_id"],
            "initial_review_set_id": packet["review_set_id"],
            "original_review_receipt_sha256s": packet[
                "initial_review_receipt_sha256s"
            ],
            "repair_packet_sha256": by_role["repair_packet"]["sha256"],
            "repair_draft_sha256": by_role["repair_draft"]["sha256"],
            "repair_operation_sha256": by_role["repair_operation"]["sha256"],
            "repair_adapter_sha256": by_role["repair_adapter"]["sha256"],
        },
        "objects": objects,
    }
    path, manifest_sha256 = write_input_registry_json(
        "manifests", manifest, "repaired input manifest"
    )
    validate_input_manifest(context["skill_dir"], manifest_sha256)
    return {
        "candidate_id": initial["candidate_id"],
        "input_manifest": str(path),
        "input_manifest_sha256": manifest_sha256,
    }


def v2_input_repair(args: argparse.Namespace) -> dict[str, Any]:
    model = require_text(args.original_author_model, "original author model")
    if model == "default":
        raise EvaluationError("input repair requires an explicit original model")
    validate_input_model_budget(args, "input repair")
    skill_dir = resolve_path(Path(args.skill_dir), "skill directory")
    initial = validate_input_manifest(
        skill_dir, require_sha256(args.manifest, "initial manifest")
    )
    _, _, _, author_operation = initial_authoring_contract(initial)
    retained_model = require_text(
        author_operation["observed_model"], "retained author model"
    )
    if model != retained_model:
        raise EvaluationError(
            "repair original author model differs from retained author provenance"
        )
    packet, context = build_input_repair_packet(
        skill_dir,
        require_sha256(args.claim_id, "claim ID"),
        initial["input_manifest_sha256"],
        require_sha256(args.validation, "initial validation receipt"),
        list(args.review),
        retained_model,
    )
    adapter = trusted_authoring_adapter_path()
    adapter_sha256 = sha256_file(adapter)
    output = authoring_output_path(
        args.output_dir, skill_dir, "repair materialization output"
    )
    test_binary = trusted_input_test_binary(skill_dir, Path(args.skill_dir))
    if test_binary is not None and (
        path_has_symlink(Path(args.output_dir))
        or not output.is_relative_to(
            Path(__file__).resolve().parents[3] / ".test-work"
        )
    ):
        raise EvaluationError(
            "trusted input test materialization is limited to non-authoritative test roots"
        )
    dispatch = prepare_claim_dispatch(
        claim_id=args.claim_id,
        skill_path=str(skill_dir),
        skill_key=latest_key(str(skill_dir)),
        candidate_id=initial["candidate_id"],
        slot_name="repair",
        model=model,
        packet_id=packet["packet_id"],
        manifest_sha256=initial["input_manifest_sha256"],
        validation_receipt_sha256=packet["initial_validation_contract"][
            "receipt_sha256"
        ],
        requested_token_budget=args.token_budget,
        requested_timeout_seconds=args.timeout,
        lineage_receipt_sha256s=packet["initial_review_receipt_sha256s"],
    )
    trusted_args = argparse.Namespace(**vars(args))
    trusted_args.model = model
    trusted_args.token_budget = dispatch["token_budget"]
    trusted_args.timeout = dispatch["timeout_seconds"]
    source_arguments = [
        "--claim-id",
        packet["claim_id"],
        "--manifest",
        packet["initial_manifest_sha256"],
        "--validation",
        packet["initial_validation_contract"]["receipt_sha256"],
        "--original-author-model",
        retained_model,
    ]
    for receipt_sha256 in packet["initial_review_receipt_sha256s"]:
        source_arguments.extend(["--review", receipt_sha256])
    phase = "model_execution"
    try:
        operation, draft_value = run_trusted_input_adapter(
            trusted_args,
            skill_dir,
            Path(args.skill_dir),
            packet,
            adapter,
            "repair",
            source_arguments,
        )
        phase = "result_validation"
        if operation.get("outcome") == "insufficient_information":
            if draft_value is not None:
                raise EvaluationError(
                    "insufficient-information repair returned an unexpected draft"
                )
            operation = validate_repair_operation(
                operation,
                packet,
                None,
                adapter_sha256,
                expected_outcome="insufficient_information",
            )
            complete_claim_slot(
                claim_id=args.claim_id,
                slot_name="repair",
                operation=operation,
                manifest_sha256=None,
                terminal_reason="repair_insufficient_information",
            )
            return {
                "status": "insufficient_information",
                "state": "insufficient_information",
                "reason": operation["reason"],
                "summary": operation["summary"],
                "candidate_id": packet["candidate_id"],
                "packet_id": packet["packet_id"],
                "operation_id": operation["operation_id"],
                "claim_id": args.claim_id,
                "input_manifest": None,
                "input_manifest_sha256": None,
            }
        if draft_value is None:
            raise EvaluationError("trusted input repair returned no draft")
        draft_value, draft_id = validate_input_author_draft_value(
            draft_value, packet, context
        )
        operation = validate_repair_operation(
            operation, packet, draft_id, adapter_sha256
        )
        phase = "materialization"
        materialization = materialize_input_repair(
            packet, context, draft_value, str(output)
        )
        registration = register_repaired_input_manifest(
            context, packet, draft_value, operation, adapter
        )
        complete_claim_slot(
            claim_id=args.claim_id,
            slot_name="repair",
            operation=operation,
            manifest_sha256=registration["input_manifest_sha256"],
        )
    except (
        EvaluationError,
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as error:
        fail_dispatched_slot(
            args.claim_id, "repair", claim_failure_reason(phase, error)
        )
        raise
    return {
        "status": "review_required",
        "state": "review_required",
        "outcome": "draft",
        "claim_id": args.claim_id,
        "initial_manifest_sha256": packet["initial_manifest_sha256"],
        "review_set_id": packet["review_set_id"],
        "original_review_receipt_sha256s": packet[
            "initial_review_receipt_sha256s"
        ],
        **materialization,
        **registration,
        "repair_operation_id": operation["operation_id"],
        "repair_model": operation["observed_model"],
    }


def build_input_review_packet(
    skill_dir: Path,
    manifest_sha256: str,
    validation_sha256: str,
) -> dict[str, Any]:
    resolved = validate_input_manifest(skill_dir, manifest_sha256)
    manifest = resolved["manifest"]
    if not is_bounded_input_manifest(manifest):
        raise EvaluationError(
            "model review packets require bounded-safe-author provenance"
        )
    validate_input_validation_receipt(resolved, validation_sha256)
    validation = load_input_registry_receipt(validation_sha256)
    objects = manifest["objects"]
    authoring_packet = load_registry_json_object(
        manifest_role(objects, "authoring_packet"), "authoring packet"
    )
    authoring_draft = load_registry_json_object(
        manifest_role(objects, "authoring_draft"), "authoring draft"
    )
    materialization = load_registry_json_object(
        manifest_role(objects, "authoring_receipt"), "authoring receipt"
    )
    author_operation = load_registry_json_object(
        manifest_role(objects, "authoring_operation"), "authoring operation"
    )
    packet = {
        "schema_version": AUTHORING_PACKET_SCHEMA_VERSION,
        "kind": "safe_evaluation_input_review_packet",
        "candidate_id": resolved["candidate_id"],
        "input_manifest_sha256": resolved["input_manifest_sha256"],
        "manifest_contract": {
            "suite_id": resolved["suite_id"],
            "policy_id": resolved["policy_id"],
            "observation_plan_id": manifest["observation_plan_id"],
            "harness_executable_sha256": manifest[
                "harness_executable_sha256"
            ],
            "object_inventory": objects,
        },
        "validation_contract": {
            "receipt_sha256": validation_sha256,
            "status": validation["status"],
            "validator": validation["validator"],
        },
        "skill_contract": authoring_packet["skill_contract"],
        "candidate_inventory": authoring_packet["candidate_inventory"],
        "suite": resolved["suite"],
        "source_catalog": authoring_packet["source_catalog"],
        "policy_contract": authoring_packet["policy_contract"],
        "compilation_contract": authoring_packet["compilation_contract"],
        "routing_contract": authoring_packet["routing_contract"],
        "authoring_contract": {
            "packet_id": authoring_packet["packet_id"],
            "draft_id": f"sha256:{digest(canonical(authoring_draft))}",
            "materialization": materialization,
            "operation": author_operation,
        },
        "review_contract": {
            "decision_values": ["accept", "reject"],
            "accept_only_if": [
                "every prompt is a realistic standalone task for its declared case class",
                "prompts do not disclose expected answers, grader mechanics, or evaluation metadata",
                "the skill contract and every required case class are covered",
                "declared public or synthetic fixtures and objective graders can observe the outcome",
                "task identities and prompts are distinct",
                "no private or undeclared source is required",
            ],
        },
    }
    if manifest["authoring_method"] == "bounded-safe-repair":
        lineage = manifest["repair_lineage"]
        repair_packet = load_registry_json_object(
            manifest_role(objects, "repair_packet"), "repair packet"
        )
        repair_draft = load_registry_json_object(
            manifest_role(objects, "repair_draft"), "repair draft"
        )
        repair_operation = load_registry_json_object(
            manifest_role(objects, "repair_operation"), "repair operation"
        )
        packet["repair_lineage_contract"] = {
            **lineage,
            "repair_packet_id": repair_packet["packet_id"],
            "repair_draft_id": f"sha256:{digest(canonical(repair_draft))}",
            "repair_operation": repair_operation,
            "original_review_history": repair_packet["review_history"],
        }
    reject_authoring_sensitive_value(packet, "model-facing review packet")
    packet["packet_id"] = f"sha256:{digest(canonical(packet))}"
    return packet


def v2_input_review_packet(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = resolve_path(Path(args.skill_dir), "skill directory")
    packet = build_input_review_packet(
        skill_dir,
        require_sha256(args.manifest, "input manifest digest"),
        require_sha256(args.validation, "input validation receipt"),
    )
    output = authoring_output_path(
        args.output, skill_dir, "input review packet output"
    )
    atomic_write(output, packet)
    return {
        "candidate_id": packet["candidate_id"],
        "input_manifest_sha256": packet["input_manifest_sha256"],
        "validation_receipt_sha256": packet["validation_contract"][
            "receipt_sha256"
        ],
        "packet_id": packet["packet_id"],
        "output": str(output),
    }


def validate_input_review_operation(
    operation: dict[str, Any],
    packet: dict[str, Any],
    adapter_sha256: str,
) -> dict[str, Any]:
    require_exact_keys(
        operation,
        "input review operation",
        {
            "schema_version",
            "kind",
            "operation",
            "status",
            "vendor",
            "model",
            "observed_model",
            "adapter_executable_sha256",
            "packet_id",
            "candidate_id",
            "input_manifest_sha256",
            "validation_receipt_sha256",
            "decision",
            "summary",
            "reason",
            "usage",
            "billing",
            "elapsed_ms",
            "operation_id",
        },
    )
    model = require_text(operation.get("model"), "input review operation.model")
    if (
        operation.get("schema_version") != AUTHORING_PACKET_SCHEMA_VERSION
        or operation.get("kind") != "evaluation_input_model_operation"
        or operation.get("operation") != "review"
        or operation.get("status") != "completed"
        or operation.get("vendor") != "copilot"
        or model == "default"
        or operation.get("observed_model") != model
        or operation.get("adapter_executable_sha256") != adapter_sha256
        or operation.get("packet_id") != packet["packet_id"]
        or operation.get("candidate_id") != packet["candidate_id"]
        or operation.get("input_manifest_sha256")
        != packet["input_manifest_sha256"]
        or operation.get("validation_receipt_sha256")
        != packet["validation_contract"]["receipt_sha256"]
        or operation.get("decision") not in {"accept", "reject"}
        or (
            operation.get("decision") == "accept"
            and operation.get("reason") is not None
        )
        or (
            operation.get("decision") == "reject"
            and operation.get("reason")
            not in {
                "case_coverage_invalid",
                "objective_outcome_unproved",
                "privacy_boundary_violation",
                "prompt_contract_mismatch",
                "task_independence_invalid",
            }
        )
    ):
        raise EvaluationError(
            "input review operation identity, decision, or manifest binding is invalid"
        )
    require_authoring_safe_text(
        operation.get("summary"),
        "input review operation.summary",
        maximum=AUTHORING_MAX_DESCRIPTION_BYTES,
    )
    usage = operation.get("usage")
    if not isinstance(usage, dict):
        raise EvaluationError("input review operation.usage must be an object")
    require_exact_keys(
        usage,
        "input review operation.usage",
        {"normalized_tokens", "input_tokens", "output_tokens"},
    )
    normalized_tokens = require_positive_int(
        usage.get("normalized_tokens"),
        "input review operation.usage.normalized_tokens",
    )
    if normalized_tokens > 112_000:
        raise EvaluationError(
            "input review operation exceeds the normalized-token budget"
        )
    detailed = [
        require_nonnegative_int(usage.get(field), f"input review operation.usage.{field}")
        for field in ("input_tokens", "output_tokens")
        if usage.get(field) is not None
    ]
    if detailed and (
        len(detailed) != 2 or sum(detailed) != normalized_tokens
    ):
        raise EvaluationError(
            "input review operation detailed usage does not match normalized tokens"
        )
    expected_billing = {
        "status": "unavailable",
        "cost_usd": None,
        "provider": "copilot",
        "unavailable_reason": "provider_telemetry_unavailable",
        "native_line_item_id": None,
        "native_event_sha256": None,
        "native_event_size": None,
    }
    if operation.get("billing") != expected_billing:
        raise EvaluationError("input review operation billing telemetry is invalid")
    elapsed_ms = require_nonnegative_int(
        operation.get("elapsed_ms"), "input review operation.elapsed_ms"
    )
    if elapsed_ms > 25 * 60 * 1000:
        raise EvaluationError(
            "input review operation exceeds the elapsed-time budget"
        )
    operation_without_id = {
        key: value for key, value in operation.items() if key != "operation_id"
    }
    if operation.get("operation_id") != (
        f"sha256:{digest(shadow_canonical(operation_without_id))}"
    ):
        raise EvaluationError("input review operation content identity is invalid")
    return operation


def load_input_registry_receipt(receipt_sha256: str) -> dict[str, Any]:
    path = registry_review_path(receipt_sha256)
    raw = require_registry_file(
        path, input_registry_component("reviews"), "input registry receipt"
    )
    receipt = load_json(path)
    if (
        raw != canonical(receipt)
        or f"sha256:{digest(raw)}" != receipt_sha256
        or path.name != f"{receipt_sha256.removeprefix('sha256:')}.json"
    ):
        raise EvaluationError("input registry receipt content address is invalid")
    return receipt


def validate_input_receipts(
    resolved: dict[str, Any],
    validation_sha256: str,
    review_sha256s: list[str],
) -> None:
    validate_input_validation_receipt(resolved, validation_sha256)
    decisions = validate_input_review_receipts(resolved, review_sha256s)
    if len(decisions) != 2 or any(decision != "accept" for decision in decisions):
        raise EvaluationError(
            "ready input requires exactly two independent accepting reviews"
        )


def bounded_review_set_id(
    resolved: dict[str, Any],
    claim_id: str,
    review_sha256s: list[str],
) -> str:
    author_operation = load_registry_json_object(
        manifest_role(
            resolved["manifest"]["objects"], "authoring_operation"
        ),
        "authoring operation",
    )
    reviewer_models: list[str] = []
    for receipt_sha256 in review_sha256s:
        receipt = load_input_registry_receipt(receipt_sha256)
        operation = receipt.get("review_operation")
        if not isinstance(operation, dict):
            raise EvaluationError(
                "bounded input review receipt operation is unavailable"
            )
        reviewer_models.append(
            require_text(
                operation.get("observed_model"),
                "bounded input review observed model",
            )
        )
    if resolved["manifest"]["authoring_method"] == "bounded-safe-repair":
        lineage = resolved["manifest"]["repair_lineage"]
        initial = validate_input_manifest(
            Path(resolved["manifest"]["skill_path"]),
            lineage["initial_manifest_sha256"],
        )
        original_receipts = lineage["original_review_receipt_sha256s"]
        validate_input_review_receipts(initial, original_receipts)
        original_models = []
        for receipt_sha256 in original_receipts:
            receipt = load_input_registry_receipt(receipt_sha256)
            original_models.append(
                require_text(
                    receipt["review_operation"].get("observed_model"),
                    "original input reviewer model",
                )
            )
        if sorted(reviewer_models) != sorted(original_models):
            raise EvaluationError(
                "repaired input reviews must use the original reviewer identities"
            )
        expected = bounded_review_set_id(
            initial, claim_id, original_receipts
        )
        if lineage["initial_review_set_id"] != expected:
            raise EvaluationError("repaired input review-set lineage is invalid")
        return expected
    try:
        return review_set_identity(
            require_sha256(claim_id, "claim ID"),
            resolved["candidate_id"],
            resolved["input_manifest_sha256"],
            require_text(
                author_operation.get("observed_model"),
                "bounded input author observed model",
            ),
            reviewer_models,
        )
    except ClaimLedgerError as error:
        raise EvaluationError(str(error)) from error


def validate_input_validation_receipt(
    resolved: dict[str, Any], validation_sha256: str
) -> None:
    expected = input_receipt_binding(resolved, "evaluation_input_validation")
    validation = load_input_registry_receipt(validation_sha256)
    require_exact_keys(
        validation,
        "input validation receipt",
        set(expected)
        | {"status", "suite_id", "policy_id", "observation_plan_id", "validator"},
    )
    if validation != {
        **expected,
        "status": "pass",
        "suite_id": resolved["suite_id"],
        "policy_id": resolved["policy_id"],
        "observation_plan_id": resolved["manifest"]["observation_plan_id"],
        "validator": RUNNER_VERSION,
    }:
        raise EvaluationError(
            "input validation receipt does not bind the exact manifest"
        )


def validate_input_review_receipts(
    resolved: dict[str, Any], review_sha256s: list[str]
) -> list[str]:
    if len(review_sha256s) != len(set(review_sha256s)):
        raise EvaluationError("input review receipts must be distinct")
    reviewers: set[str] = set()
    decisions: list[str] = []
    review_expected = input_receipt_binding(
        resolved, "evaluation_input_review"
    )
    bounded = is_bounded_input_manifest(resolved["manifest"])
    author_model = None
    if bounded:
        author_operation = load_registry_json_object(
            manifest_role(
                resolved["manifest"]["objects"], "authoring_operation"
            ),
            "authoring operation",
        )
        author_model = author_operation["observed_model"]
    for index, receipt_sha256 in enumerate(review_sha256s):
        receipt = load_input_registry_receipt(receipt_sha256)
        extra_keys = (
            {"review_packet", "review_operation", "review_adapter"}
            if bounded
            else set()
        )
        require_exact_keys(
            receipt,
            f"input review receipt {index}",
            set(review_expected) | {"reviewer", "decision"} | extra_keys,
        )
        reviewer = require_text(
            receipt.get("reviewer"), f"input review receipt {index}.reviewer"
        )
        decision = receipt.get("decision")
        if bounded:
            packet_entry, _ = registry_object_bytes(
                receipt.get("review_packet"), index
            )
            adapter_entry, _ = registry_object_bytes(
                receipt.get("review_adapter"), index
            )
            if (
                packet_entry["role"] != "input_review_packet"
                or packet_entry["logical_path"] != "reviews/packet.json"
                or packet_entry["media_type"] != "application/json"
                or adapter_entry["role"] != "input_review_adapter"
                or adapter_entry["logical_path"]
                != "reviews/dreaming-vendor-adapter.py"
                or adapter_entry["media_type"] != "text/x-python"
                or adapter_entry["sha256"] != TRUSTED_AUTHORING_ADAPTER_SHA256
            ):
                raise EvaluationError(
                    "input review receipt retained object roles are invalid"
                )
            packet = load_registry_json_object(
                packet_entry, "input review packet"
            )
            operation = receipt.get("review_operation")
            if not isinstance(operation, dict):
                raise EvaluationError(
                    "input review receipt operation must be an object"
                )
            operation = validate_input_review_operation(
                operation, packet, adapter_entry["sha256"]
            )
            expected_packet = build_input_review_packet(
                Path(resolved["manifest"]["skill_path"]),
                resolved["input_manifest_sha256"],
                operation["validation_receipt_sha256"],
            )
            if (
                packet != expected_packet
                or reviewer != f"copilot:{operation['observed_model']}"
                or decision != operation["decision"]
                or operation["observed_model"] == author_model
            ):
                raise EvaluationError(
                    "input review receipt model or packet provenance is invalid"
                )
        if (
            any(receipt.get(key) != value for key, value in review_expected.items())
            or decision not in {"accept", "reject"}
            or reviewer in reviewers
        ):
            raise EvaluationError(
                "input reviews must independently review the exact manifest"
            )
        reviewers.add(reviewer)
        decisions.append(decision)
    return decisions


def validate_repaired_input_manifest(
    skill_dir: Path,
    manifest_sha256: str,
    manifest: dict[str, Any],
    objects: list[dict[str, Any]],
    *,
    resolved_suite: dict[str, Any],
    resolved_suite_id: str,
    resolved_policy: dict[str, Any],
    resolved_policy_id: str,
    resolved_config: dict[str, Any],
    resolved_routing: dict[str, Any],
    harness_sha: str,
) -> None:
    lineage = manifest.get("repair_lineage")
    if not isinstance(lineage, dict):
        raise EvaluationError("repaired manifest lineage must be an object")
    require_exact_keys(
        lineage,
        "repaired manifest lineage",
        {
            "initial_manifest_sha256",
            "claim_id",
            "initial_review_set_id",
            "original_review_receipt_sha256s",
            "repair_packet_sha256",
            "repair_draft_sha256",
            "repair_operation_sha256",
            "repair_adapter_sha256",
        },
    )
    initial_sha256 = require_sha256(
        lineage.get("initial_manifest_sha256"),
        "repair lineage initial manifest",
    )
    if initial_sha256 == manifest_sha256:
        raise EvaluationError("repaired manifest cannot name itself as its initial input")
    claim_id = require_sha256(lineage.get("claim_id"), "repair lineage claim ID")
    review_set_id = require_sha256(
        lineage.get("initial_review_set_id"),
        "repair lineage review set ID",
    )
    review_sha256s = lineage.get("original_review_receipt_sha256s")
    if not isinstance(review_sha256s, list):
        raise EvaluationError("repair lineage reviews must be a list")
    review_sha256s = [
        require_sha256(value, "repair lineage review receipt")
        for value in review_sha256s
    ]
    if review_sha256s != sorted(review_sha256s) or len(review_sha256s) != 2:
        raise EvaluationError(
            "repair lineage requires two sorted original review receipts"
        )
    roles = {item["role"] for item in objects}
    if (
        roles & INPUT_AUTHORING_OBJECT_ROLES != INPUT_AUTHORING_OBJECT_ROLES
        or roles & INPUT_REPAIR_OBJECT_ROLES != INPUT_REPAIR_OBJECT_ROLES
    ):
        raise EvaluationError(
            "repaired manifest is missing exact authoring or repair provenance"
        )
    initial = validate_input_manifest(skill_dir, initial_sha256)
    if initial["manifest"]["authoring_method"] != "bounded-safe-author":
        raise EvaluationError("repair lineage must name an initial author manifest")
    if (
        initial["candidate_id"] != manifest["candidate_id"]
        or initial["candidate_inventory"] != manifest["candidate_inventory"]
    ):
        raise EvaluationError("repaired manifest candidate differs from its initial input")
    initial_retained = sorted(
        [
            item
            for item in initial["manifest"]["objects"]
            if item["role"] != "suite"
        ],
        key=lambda item: (item["role"], item["logical_path"]),
    )
    repaired_retained = sorted(
        [
            item
            for item in objects
            if item["role"] != "suite"
            and item["role"] not in INPUT_REPAIR_OBJECT_ROLES
        ],
        key=lambda item: (item["role"], item["logical_path"]),
    )
    if repaired_retained != initial_retained:
        raise EvaluationError(
            "repaired manifest changes a retained policy, config, routing, harness, fixture, grader, or authoring object"
        )
    repair_entries = {
        role: manifest_role(objects, role) for role in INPUT_REPAIR_OBJECT_ROLES
    }
    expected_repair_entries = {
        "repair_packet": ("repair/packet.json", "application/json"),
        "repair_draft": ("repair/draft.json", "application/json"),
        "repair_operation": ("repair/operation.json", "application/json"),
        "repair_adapter": (
            "repair/dreaming-vendor-adapter.py",
            "text/x-python",
        ),
    }
    if any(
        (
            repair_entries[role]["logical_path"],
            repair_entries[role]["media_type"],
        )
        != expected
        for role, expected in expected_repair_entries.items()
    ):
        raise EvaluationError("repair provenance object roles or paths are invalid")
    for role, lineage_key in (
        ("repair_packet", "repair_packet_sha256"),
        ("repair_draft", "repair_draft_sha256"),
        ("repair_operation", "repair_operation_sha256"),
        ("repair_adapter", "repair_adapter_sha256"),
    ):
        if repair_entries[role]["sha256"] != require_sha256(
            lineage.get(lineage_key), f"repair lineage {lineage_key}"
        ):
            raise EvaluationError("repair lineage object identity is invalid")
    if repair_entries["repair_adapter"]["sha256"] != TRUSTED_AUTHORING_ADAPTER_SHA256:
        raise EvaluationError("repair adapter differs from the trusted adapter")
    packet = load_registry_json_object(
        repair_entries["repair_packet"], "repair packet"
    )
    draft = load_registry_json_object(
        repair_entries["repair_draft"], "repair draft"
    )
    operation = load_registry_json_object(
        repair_entries["repair_operation"], "repair operation"
    )
    expected_packet, context = build_input_repair_packet(
        skill_dir,
        claim_id,
        initial_sha256,
        packet.get("initial_validation_contract", {}).get("receipt_sha256"),
        review_sha256s,
        packet.get("original_author_model"),
        require_active_claim=False,
    )
    if packet != expected_packet or packet["review_set_id"] != review_set_id:
        raise EvaluationError("repair packet or review-set lineage is not reproducible")
    draft, draft_id = validate_input_author_draft_value(
        draft, packet, context
    )
    operation = validate_repair_operation(
        operation,
        packet,
        draft_id,
        repair_entries["repair_adapter"]["sha256"],
    )
    expected_suite = materialized_authoring_suite(draft, initial["suite"])
    if (
        resolved_suite != expected_suite
        or resolved_suite_id != f"sha256:{digest(canonical(expected_suite))}"
        or resolved_policy != initial["policy"]
        or resolved_policy_id != initial["policy_id"]
        or resolved_config != initial["config"]
        or resolved_routing != initial["routing"]
        or harness_sha != initial["manifest"]["harness_executable_sha256"]
    ):
        raise EvaluationError(
            "repaired manifest changes more than the allowed task and prompt fields"
        )
    expected_sources = {
        claim_id,
        initial_sha256,
        review_set_id,
        *review_sha256s,
        packet["packet_id"],
        draft_id,
        operation["operation_id"],
    }
    if set(manifest["source_identities"]) != expected_sources:
        raise EvaluationError("repaired manifest source identities are incomplete")


def parse_registry_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvaluationError("readiness creation time must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise EvaluationError("readiness creation time must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def load_input_current_pointer(skill_dir: Path) -> dict[str, Any] | None:
    path = input_current_path(skill_dir)
    if not path.exists() and not path.is_symlink():
        return None
    require_registry_file(
        path, input_registry_component("current"), "input current pointer"
    )
    pointer = load_json(path)
    require_exact_keys(
        pointer,
        "input current pointer",
        {
            "schema_version",
            "kind",
            "skill_path",
            "skill_key",
            "candidate_id",
            "transition_id",
        },
    )
    if (
        pointer.get("schema_version") != INPUT_REGISTRY_SCHEMA_VERSION
        or pointer.get("kind") != "evaluation_input_current"
        or pointer.get("skill_path") != str(skill_dir)
        or pointer.get("skill_key") != latest_key(str(skill_dir))
    ):
        raise EvaluationError("input current pointer identity is malformed")
    require_sha256(pointer.get("candidate_id"), "input current candidate_id")
    require_sha256(pointer.get("transition_id"), "input current transition_id")
    return pointer


def input_transition_path(
    skill_dir: Path, current_candidate_id: str, transition_id: str
) -> Path:
    identity = require_sha256(transition_id, "input readiness transition_id")
    return (
        input_readiness_dir(skill_dir, current_candidate_id)
        / f"{identity.removeprefix('sha256:')}.json"
    )


def load_input_transition(
    skill_dir: Path, current_candidate_id: str, transition_id: str
) -> dict[str, Any]:
    path = input_transition_path(skill_dir, current_candidate_id, transition_id)
    root = input_readiness_dir(skill_dir, current_candidate_id)
    raw = require_registry_file(path, root, "input readiness transition")
    transition = load_json(path)
    if raw != canonical(transition):
        raise EvaluationError("input readiness transition must be canonical JSON")
    transition_keys = {
        "schema_version",
        "kind",
        "skill_path",
        "skill_key",
        "candidate_id",
        "input_manifest_sha256",
        "prior_transition_id",
        "state",
        "reason",
        "created_at",
        "validation_receipt_sha256",
        "review_receipt_sha256s",
        "transition_id",
    }
    if set(transition) not in (
        transition_keys,
        transition_keys | {"claim_id"},
        transition_keys | {"claim_id", "review_set_id"},
    ):
        raise EvaluationError(
            "input readiness transition has unexpected or missing fields"
        )
    state = transition.get("state")
    reason = transition.get("reason")
    claim_id = transition.get("claim_id")
    claim_terminal_reason = (
        isinstance(claim_id, str)
        and state in {"invalid", "insufficient_information"}
        and isinstance(reason, str)
        and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", reason) is not None
    )
    if (
        transition.get("schema_version") != INPUT_REGISTRY_SCHEMA_VERSION
        or transition.get("kind") != "evaluation_input_readiness_transition"
        or transition.get("skill_path") != str(skill_dir)
        or transition.get("skill_key") != latest_key(str(skill_dir))
        or transition.get("candidate_id") != current_candidate_id
        or state not in INPUT_READINESS_STATES
        or (
            reason not in INPUT_READINESS_REASONS.get(state, set())
            and not claim_terminal_reason
        )
        or transition.get("transition_id")
        != identity_with("transition_id", transition)
        or transition.get("transition_id") != transition_id
        or path.name != f"{transition_id.removeprefix('sha256:')}.json"
    ):
        raise EvaluationError("input readiness transition identity is malformed")
    prior = transition.get("prior_transition_id")
    if prior is not None:
        require_sha256(prior, "input readiness prior transition")
    require_text(transition.get("created_at"), "input readiness created_at")
    parse_registry_timestamp(transition["created_at"])
    manifest_sha256 = transition.get("input_manifest_sha256")
    validation_sha256 = transition.get("validation_receipt_sha256")
    reviews = transition.get("review_receipt_sha256s")
    review_set_id = transition.get("review_set_id")
    if review_set_id is not None and claim_id is None:
        raise EvaluationError(
            "input readiness review set identity requires a claim"
        )
    if claim_id is not None:
        require_sha256(claim_id, "input readiness claim ID")
    if review_set_id is not None:
        require_sha256(review_set_id, "input readiness review set ID")
    if not isinstance(reviews, list):
        raise EvaluationError("input readiness reviews must be a list")
    for index, receipt_sha256 in enumerate(reviews):
        require_sha256(
            receipt_sha256, f"input readiness review receipt {index}"
        )
    if state in {"input_missing", "drafting"} or (
        state == "insufficient_information" and claim_id is None
    ):
        if (
            manifest_sha256 is not None
            or validation_sha256 is not None
            or reviews
            or claim_id is not None
        ):
            raise EvaluationError(
                f"{state} readiness cannot retain manifest or review authority"
            )
        return transition
    if state == "insufficient_information":
        if (
            manifest_sha256 is not None
            or validation_sha256 is not None
            or reviews
            or review_set_id is not None
        ):
            raise EvaluationError(
                "claim-bound insufficient information cannot retain input authority"
            )
        return transition
    if state == "invalid" and manifest_sha256 is None:
        if validation_sha256 is not None or reviews or claim_id is None:
            raise EvaluationError(
                "manifest-free invalid readiness requires a claim without receipts"
            )
        return transition
    require_sha256(manifest_sha256, "input readiness manifest digest")
    resolved = validate_input_manifest(skill_dir, manifest_sha256)
    if validation_sha256 is not None:
        require_sha256(
            validation_sha256, "input readiness validation receipt"
        )
        validate_input_validation_receipt(resolved, validation_sha256)
    decisions = validate_input_review_receipts(resolved, reviews)
    if state == "review_required":
        if validation_sha256 is None or len(reviews) >= 2 or any(
            decision != "accept" for decision in decisions
        ):
            raise EvaluationError(
                "review-required input needs validation and fewer than two accepting reviews"
            )
    elif state == "invalid":
        if reason in {
            "independent_review_rejected",
            "independent_rereview_rejected",
        } and "reject" not in decisions:
            raise EvaluationError("rejected input must bind a rejecting review")
        if reason == "deterministic_validation_failed" and (
            validation_sha256 is not None or reviews
        ):
            raise EvaluationError(
                "deterministic validation failure cannot retain passing receipts"
            )
    elif state == "ready":
        if validation_sha256 is None:
            raise EvaluationError("ready input requires validation")
        validate_input_receipts(resolved, validation_sha256, reviews)
        bounded = is_bounded_input_manifest(resolved["manifest"])
        if bounded and claim_id is not None:
            if review_set_id is None:
                raise EvaluationError(
                    "bounded ready input requires a review set identity"
                )
            expected_review_set = bounded_review_set_id(
                resolved, claim_id, reviews
            )
            if review_set_id != expected_review_set:
                raise EvaluationError(
                    "ready input review set identity is invalid"
                )
        elif not bounded and claim_id is not None:
            raise EvaluationError(
                "manual ready input cannot bind an authoring claim"
            )
    return transition


def input_transition_result(
    skill_dir: Path, transition: dict[str, Any]
) -> dict[str, Any]:
    path = input_transition_path(
        skill_dir, transition["candidate_id"], transition["transition_id"]
    )
    return {
        "state": transition["state"],
        "reason": transition["reason"],
        "input_manifest_sha256": transition["input_manifest_sha256"],
        "transition": str(path),
        "transition_id": transition["transition_id"],
        "current": str(input_current_path(skill_dir)),
    }


def write_input_current_pointer(
    skill_dir: Path, candidate: str, transition_id: str
) -> None:
    pointer = {
        "schema_version": INPUT_REGISTRY_SCHEMA_VERSION,
        "kind": "evaluation_input_current",
        "skill_path": str(skill_dir),
        "skill_key": latest_key(str(skill_dir)),
        "candidate_id": candidate,
        "transition_id": transition_id,
    }
    pointer_path = input_current_path(skill_dir)
    if pointer_path.is_symlink():
        raise EvaluationError("input current pointer cannot be a symlink")
    atomic_write(pointer_path, pointer)


def _write_input_transition_locked(
    skill_dir: Path,
    *,
    state: str,
    reason: str,
    input_manifest_sha256: str | None,
    validation_receipt_sha256: str | None,
    review_receipt_sha256s: list[str],
    created_at: str,
    claim_id: str | None = None,
    review_set_id: str | None = None,
    before_persist: Callable[[], None] | None = None,
) -> dict[str, Any]:
    candidate, _ = candidate_id(skill_dir)
    current = load_input_current_pointer(skill_dir)
    prior_state = None
    if current is not None and current["candidate_id"] == candidate:
        prior = resolve_input_readiness(skill_dir)
        prior_state = prior["state"]
        if state not in INPUT_READINESS_TRANSITIONS[prior_state]:
            raise EvaluationError(
                f"readiness cannot transition from {prior_state} to {state}"
            )
    prior_transition_id = (
        current["transition_id"]
        if current is not None and current["candidate_id"] == candidate
        else None
    )
    transition = {
        "schema_version": INPUT_REGISTRY_SCHEMA_VERSION,
        "kind": "evaluation_input_readiness_transition",
        "skill_path": str(skill_dir),
        "skill_key": latest_key(str(skill_dir)),
        "candidate_id": candidate,
        "input_manifest_sha256": input_manifest_sha256,
        "prior_transition_id": prior_transition_id,
        "state": state,
        "reason": reason,
        "created_at": parse_registry_timestamp(created_at),
        "validation_receipt_sha256": validation_receipt_sha256,
        "review_receipt_sha256s": sorted(review_receipt_sha256s),
    }
    if claim_id is not None:
        transition["claim_id"] = require_sha256(
            claim_id, "input readiness claim ID"
        )
    if review_set_id is not None:
        transition["review_set_id"] = require_sha256(
            review_set_id, "input readiness review set ID"
        )
    transition["transition_id"] = identity_with("transition_id", transition)
    path = input_transition_path(skill_dir, candidate, transition["transition_id"])
    if before_persist is not None:
        before_persist()
    create_only_bytes(path, canonical(transition), "input readiness transition")
    if before_persist is not None:
        before_persist()
    write_input_current_pointer(
        skill_dir, candidate, transition["transition_id"]
    )
    resolve_input_readiness(skill_dir)
    return input_transition_result(skill_dir, transition)


def write_input_transition(
    skill_dir: Path,
    *,
    state: str,
    reason: str,
    input_manifest_sha256: str | None,
    validation_receipt_sha256: str | None,
    review_receipt_sha256s: list[str],
    created_at: str,
    claim_id: str | None = None,
    review_set_id: str | None = None,
) -> dict[str, Any]:
    with input_readiness_state_lock():
        return _write_input_transition_locked(
            skill_dir,
            state=state,
            reason=reason,
            input_manifest_sha256=input_manifest_sha256,
            validation_receipt_sha256=validation_receipt_sha256,
            review_receipt_sha256s=review_receipt_sha256s,
            created_at=created_at,
            claim_id=claim_id,
            review_set_id=review_set_id,
        )


def v2_input_state(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = resolve_path(Path(args.skill_dir), "skill directory")
    state = args.state
    reason = args.reason
    if reason not in INPUT_READINESS_REASONS[state]:
        raise EvaluationError(f"{reason} is not valid for readiness state {state}")
    manifest_sha256 = (
        require_sha256(args.manifest, "input manifest digest")
        if args.manifest is not None
        else None
    )
    validation_sha256 = (
        require_sha256(args.validation, "input validation receipt")
        if args.validation is not None
        else None
    )
    reviews = [
        require_sha256(item, "input review receipt") for item in args.review
    ]
    if state in {"review_required", "invalid"} and manifest_sha256 is None:
        raise EvaluationError(f"{state} readiness requires an input manifest")
    if state not in {"review_required", "invalid"} and (
        manifest_sha256 is not None or validation_sha256 is not None or reviews
    ):
        raise EvaluationError(f"{state} readiness cannot bind input receipts")
    if manifest_sha256 is not None:
        resolved = validate_input_manifest(skill_dir, manifest_sha256)
        if validation_sha256 is not None:
            validate_input_validation_receipt(resolved, validation_sha256)
        decisions = validate_input_review_receipts(resolved, reviews)
        if state == "review_required" and (
            validation_sha256 is None
            or len(reviews) >= 2
            or any(decision != "accept" for decision in decisions)
        ):
            raise EvaluationError(
                "review-required input needs validation and fewer than two accepting reviews"
            )
        if state == "invalid":
            if (
                reason
                in {
                    "independent_review_rejected",
                    "independent_rereview_rejected",
                }
                and "reject" not in decisions
            ):
                raise EvaluationError("rejected input must bind a rejecting review")
            if reason == "deterministic_validation_failed" and (
                validation_sha256 is not None or reviews
            ):
                raise EvaluationError(
                    "deterministic validation failure cannot retain passing receipts"
                )
    return write_input_transition(
        skill_dir,
        state=state,
        reason=reason,
        input_manifest_sha256=manifest_sha256,
        validation_receipt_sha256=validation_sha256,
        review_receipt_sha256s=reviews,
        created_at=args.created_at or now_iso(),
    )


def ready_transition_matches(
    transition: dict[str, Any],
    *,
    manifest_sha256: str,
    validation_sha256: str,
    review_sha256s: list[str],
    claim_id: str | None,
    review_set_id: str | None,
) -> bool:
    return (
        transition["state"] == "ready"
        and transition["reason"] == "validated_and_reviewed"
        and transition["input_manifest_sha256"] == manifest_sha256
        and transition["validation_receipt_sha256"] == validation_sha256
        and transition["review_receipt_sha256s"] == review_sha256s
        and transition.get("claim_id") == claim_id
        and transition.get("review_set_id") == review_set_id
    )


def ready_result(
    skill_dir: Path,
    transition: dict[str, Any],
    claim_facts: dict[str, Any] | None,
) -> dict[str, Any]:
    result = input_transition_result(skill_dir, transition)
    if claim_facts is not None:
        result.update(
            {
                "claim_id": claim_facts["claim_id"],
                "review_set_id": claim_facts["review_set_id"],
            }
        )
    return result


def pending_terminal_matches(
    transition: dict[str, Any], publication: dict[str, Any]
) -> bool:
    return (
        transition.get("claim_id") == publication["claim_id"]
        and transition.get("state") == publication["readiness_state"]
        and transition.get("reason") == publication["readiness_reason"]
        and transition.get("input_manifest_sha256")
        == publication["manifest_sha256"]
        and transition.get("validation_receipt_sha256")
        == publication["validation_receipt_sha256"]
        and transition.get("review_receipt_sha256s")
        == publication["review_receipt_sha256s"]
        and transition.get("review_set_id") == publication["review_set_id"]
    )


def validate_pending_terminal_facts(
    skill_dir: Path, publication: dict[str, Any]
) -> None:
    require_sha256(publication.get("claim_id"), "pending terminal claim")
    state = publication.get("readiness_state")
    reason = publication.get("readiness_reason")
    if state not in {"ready", "invalid", "insufficient_information"} or (
        not isinstance(reason, str)
        or re.fullmatch(r"[a-z][a-z0-9_]{0,127}", reason) is None
    ):
        raise EvaluationError("pending terminal state or reason is invalid")
    manifest = publication.get("manifest_sha256")
    validation = publication.get("validation_receipt_sha256")
    reviews = publication.get("review_receipt_sha256s")
    if not isinstance(reviews, list):
        raise EvaluationError("pending terminal reviews must be a list")
    if state == "insufficient_information":
        if (
            manifest is not None
            or validation is not None
            or reviews
            or publication.get("review_set_id") is not None
        ):
            raise EvaluationError(
                "pending insufficient information cannot retain input authority"
            )
        return
    if manifest is None:
        if state != "invalid" or validation is not None or reviews:
            raise EvaluationError("pending terminal manifest facts are invalid")
        return
    resolved = validate_input_manifest(
        skill_dir, require_sha256(manifest, "pending terminal manifest")
    )
    validated_reviews = sorted(
        require_sha256(value, "pending terminal review") for value in reviews
    )
    if validation is not None:
        validate_input_validation_receipt(
            resolved,
            require_sha256(validation, "pending terminal validation receipt"),
        )
    decisions = validate_input_review_receipts(resolved, validated_reviews)
    if state == "invalid":
        if reason in {
            "independent_review_rejected",
            "independent_rereview_rejected",
        } and "reject" not in decisions:
            raise EvaluationError(
                "pending rejected input lacks a rejecting review"
            )
        if reason == "deterministic_validation_failed" and (
            validation is not None or validated_reviews
        ):
            raise EvaluationError(
                "pending validation failure retains passing receipts"
            )
    if state == "ready":
        if validation is None or publication.get("review_set_id") is None:
            raise EvaluationError(
                "pending ready transition lacks validation or review set"
            )
        validate_input_receipts(resolved, validation, validated_reviews)
        expected_review_set = bounded_review_set_id(
            resolved, publication["claim_id"], validated_reviews
        )
        if publication["review_set_id"] != expected_review_set:
            raise EvaluationError("pending ready review set identity is invalid")


def publish_pending_terminal(
    publication: dict[str, Any],
    *,
    authority_check: Callable[[], None] | None = None,
    readiness_lock_held: bool = False,
) -> dict[str, Any]:
    skill_dir = resolve_path(
        Path(publication["skill_path"]), "pending terminal skill directory"
    )
    candidate, _ = candidate_id(skill_dir)
    if (
        candidate != publication["candidate_id"]
        or latest_key(str(skill_dir)) != publication["skill_key"]
    ):
        raise EvaluationError(
            "pending terminal candidate identity differs from the current skill"
        )
    validate_pending_terminal_facts(skill_dir, publication)
    lock = nullcontext() if readiness_lock_held else input_readiness_state_lock()
    with lock:
        readiness_root = input_readiness_dir(skill_dir, candidate)
        if readiness_root.exists() or readiness_root.is_symlink():
            transitions, tips = load_input_transition_history(
                skill_dir, candidate
            )
        else:
            transitions = {}
            tips = set()
        pointer = load_input_current_pointer(skill_dir)
        matching = [
            transition
            for transition in transitions.values()
            if pending_terminal_matches(transition, publication)
        ]
        if len(matching) > 1:
            raise EvaluationError(
                "pending terminal publication has multiple exact transitions"
            )
        if matching:
            transition = matching[0]
            if tips != {transition["transition_id"]}:
                raise EvaluationError(
                    "pending terminal transition is not the unique history tip"
                )
            validate_input_transition_chain(
                transitions, transition["transition_id"]
            )
            if pointer is None:
                if transition["prior_transition_id"] is not None:
                    raise EvaluationError(
                        "pending terminal history lacks its prior pointer"
                    )
            elif (
                pointer["transition_id"] != transition["transition_id"]
                and transition["prior_transition_id"]
                != pointer["transition_id"]
            ):
                raise EvaluationError(
                    "pending terminal transition is not the current child"
                )
            if (
                pointer is None
                or pointer["transition_id"] != transition["transition_id"]
            ):
                if authority_check is not None:
                    authority_check()
                write_input_current_pointer(
                    skill_dir, candidate, transition["transition_id"]
                )
            resolve_input_readiness(skill_dir)
        else:
            if transitions:
                if pointer is None or tips != {pointer["transition_id"]}:
                    raise EvaluationError(
                        "pending terminal history lacks one current tip"
                    )
                validate_input_transition_chain(
                    transitions, pointer["transition_id"]
                )
            elif pointer is not None:
                raise EvaluationError(
                    "pending terminal pointer has no transition history"
                )
            terminal_epoch = publication.get("terminal_epoch")
            if (
                not isinstance(terminal_epoch, int)
                or isinstance(terminal_epoch, bool)
                or terminal_epoch < 0
            ):
                raise EvaluationError(
                    "pending terminal publication lacks terminal time"
                )
            result = _write_input_transition_locked(
                skill_dir,
                state=publication["readiness_state"],
                reason=publication["readiness_reason"],
                input_manifest_sha256=publication["manifest_sha256"],
                validation_receipt_sha256=publication[
                    "validation_receipt_sha256"
                ],
                review_receipt_sha256s=publication[
                    "review_receipt_sha256s"
                ],
                created_at=datetime.fromtimestamp(
                    terminal_epoch, tz=timezone.utc
                ).isoformat(),
                claim_id=publication["claim_id"],
                review_set_id=publication["review_set_id"],
                before_persist=authority_check,
            )
            transition = load_input_transition(
                skill_dir, candidate, result["transition_id"]
            )
        if authority_check is not None:
            authority_check()
        acknowledge_terminal_publication(
            publication["claim_id"], transition
        )
        return {
            **input_transition_result(skill_dir, transition),
            "claim_id": publication["claim_id"],
            "publication": "replayed" if matching else "published",
        }


def input_owner_halt_file() -> Path:
    return Path(
        os.environ.get(
            "DREAMING_HALT_FILE",
            Path(
                os.environ.get(
                    "SKILLS_STATE_DIR", Path.home() / ".copilot/skill-state"
                )
            )
            / "skill-review"
            / "disable-daemon",
        )
    )


def assert_input_owner_lease() -> None:
    if (
        os.environ.get("DREAMING_ORCHESTRATED") != "1"
        or os.environ.get("SKILLS_LOCK_HELD_BY_PARENT") != "1"
        or not os.environ.get("DREAMING_PARENT_RUN_ID")
        or not os.environ.get("SKILLS_LOCK_TOKEN")
        or not os.environ.get("SKILLS_LOCK_OWNER_PID")
        or not os.environ.get("SKILLS_LOCK_OWNER_IDENTITY")
    ):
        raise EvaluationError(
            "evaluation-input owner recovery requires inherited orchestration"
        )
    lock_assert = subprocess.run(
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
    if lock_assert.returncode != 0:
        raise EvaluationError("evaluation-input owner writer lease is invalid")


def assert_input_owner_authority() -> None:
    assert_input_owner_lease()
    if input_owner_halt_file().exists():
        raise EvaluationError("evaluation-input owner recovery is halted")


def assert_input_owner_operator_authority() -> None:
    assert_input_owner_lease()
    if not input_owner_halt_file().exists():
        raise EvaluationError(
            "operator owner recovery requires the Dreaming halt file"
        )


def boot_identity_from_sysctl(value: str) -> str:
    matches = re.findall(
        r"\{\s*sec\s*=\s*([0-9]+),\s*usec\s*=\s*([0-9]+)\s*\}",
        value,
    )
    if len(matches) != 1:
        raise EvaluationError("host boot identity is unavailable")
    seconds, microseconds = matches[0]
    stable = f"{int(seconds)}:{int(microseconds)}"
    return "sha256:" + hashlib.sha256(stable.encode()).hexdigest()


def host_boot_identity() -> str:
    process = subprocess.run(
        ["/usr/sbin/sysctl", "-n", "kern.boottime"],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise EvaluationError("host boot identity is unavailable")
    return boot_identity_from_sysctl(process.stdout)


def inspect_process_identity(pid: int) -> dict[str, Any]:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return {"status": "unreadable", "detail": "invalid recorded PID"}
    process = subprocess.run(
        ["/bin/ps", "-o", "lstart=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
    )
    identity = " ".join(process.stdout.split())
    if process.returncode == 0 and identity:
        return {"status": "present", "identity": identity}
    if process.returncode != 0 and not identity and not process.stderr.strip():
        return {"status": "absent"}
    return {
        "status": "unreadable",
        "detail": process.stderr.strip() or "process identity is unavailable",
    }


def parse_process_group_identity(value: str) -> tuple[int, str]:
    match = re.fullmatch(r"pgid:([1-9][0-9]*):leader:(.+)", value)
    if match is None:
        raise EvaluationError("recorded process-group identity is malformed")
    return int(match.group(1)), match.group(2)


def process_group_identity(pgid: int) -> str:
    observed = inspect_process_identity(pgid)
    if observed["status"] != "present":
        raise EvaluationError("process-group leader identity is unavailable")
    return f"pgid:{pgid}:leader:{observed['identity']}"


def process_group_alive(pgid: int) -> str:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return "absent"
    except PermissionError:
        return "unreadable"
    except OSError:
        return "unreadable"
    return "present"


def inspect_recorded_process(
    pid: int, expected_identity: str
) -> dict[str, Any]:
    observed = inspect_process_identity(pid)
    if observed["status"] != "present":
        return observed
    if observed["identity"] != expected_identity:
        return {
            "status": "reused",
            "identity": observed["identity"],
        }
    return observed


def inspect_recorded_process_group(value: str) -> dict[str, Any]:
    try:
        pgid, leader_identity = parse_process_group_identity(value)
    except EvaluationError as error:
        return {"status": "unreadable", "detail": str(error)}
    leader = inspect_recorded_process(pgid, leader_identity)
    if leader["status"] == "reused":
        return {"status": "reused", "pgid": pgid}
    if leader["status"] == "unreadable":
        return {
            "status": "unreadable",
            "pgid": pgid,
            "detail": leader.get("detail", "group leader identity is unreadable"),
        }
    group_status = process_group_alive(pgid)
    if group_status == "unreadable":
        return {
            "status": "unreadable",
            "pgid": pgid,
            "detail": "process-group liveness is unreadable",
        }
    if group_status == "absent":
        return {"status": "absent", "pgid": pgid}
    if leader["status"] == "absent":
        return {
            "status": "unreadable",
            "pgid": pgid,
            "detail": "process-group leader identity is no longer provable",
        }
    return {
        "status": "present",
        "pgid": pgid,
        "leader_status": leader["status"],
    }


def wait_for_recorded_process_exit(
    pid: int,
    identity: str,
    *,
    timeout_seconds: float,
    authority_check: Any,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        observed = inspect_recorded_process(pid, identity)
        if observed["status"] != "present":
            return observed
        if time.monotonic() >= deadline:
            return observed
        authority_check()
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def terminate_recorded_process_group(
    recorded_identity: str,
    *,
    timeout_seconds: float,
    authority_check: Any,
) -> bool:
    group = inspect_recorded_process_group(recorded_identity)
    if group["status"] in {"absent", "reused"}:
        return True
    if group["status"] != "present":
        return False
    pgid = group["pgid"]
    if pgid <= 1 or pgid == os.getpgrp():
        return False
    deadline = time.monotonic() + timeout_seconds
    authority_check()
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    kill_sent = False
    while True:
        observed = inspect_recorded_process_group(recorded_identity)
        if observed["status"] in {"absent", "reused"}:
            return True
        if observed["status"] != "present":
            return False
        current = time.monotonic()
        if current >= deadline:
            return False
        authority_check()
        if not kill_sent and current >= deadline - (timeout_seconds / 2):
            observed = inspect_recorded_process_group(recorded_identity)
            if observed["status"] in {"absent", "reused"}:
                return True
            if observed["status"] != "present":
                return False
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                return True
            except (PermissionError, OSError):
                return False
            kill_sent = True
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def reconcile_open_owner_claims(
    *,
    authority_check: Any,
    owner_wait_seconds: float = 10.0,
    group_wait_seconds: float = 10.0,
) -> dict[str, Any]:
    recovered = []
    recovery_required = []
    open_claims = open_scheduled_claims()
    if not open_claims:
        return {
            "recovered_claims": recovered,
            "recovery_required": recovery_required,
        }
    authority_check()
    try:
        current_boot = host_boot_identity()
    except EvaluationError:
        for claim in open_claims:
            authority_check()
            recovery_required.append(
                {
                    "claim_id": claim["claim_id"],
                    "reason": "host_boot_identity_unreadable",
                }
            )
        return {
            "recovered_claims": recovered,
            "recovery_required": recovery_required,
        }
    for claim in open_claims:
        authority_check()
        if claim["owner_boot_identity"] != current_boot:
            owner = {"status": "prior_boot"}
        else:
            owner = wait_for_recorded_process_exit(
                claim["owner_pid"],
                claim["owner_process_identity"],
                timeout_seconds=owner_wait_seconds,
                authority_check=authority_check,
            )
        if owner["status"] == "present":
            recovery_required.append(
                {
                    "claim_id": claim["claim_id"],
                    "reason": "prior_owner_live",
                }
            )
            continue
        if owner["status"] == "unreadable":
            recovery_required.append(
                {
                    "claim_id": claim["claim_id"],
                    "reason": "prior_owner_identity_unreadable",
                }
            )
            continue
        if owner["status"] == "prior_boot":
            group = {"status": "absent"}
        else:
            group = inspect_recorded_process_group(
                claim["owner_process_group_identity"]
            )
        if group["status"] == "unreadable":
            recovery_required.append(
                {
                    "claim_id": claim["claim_id"],
                    "reason": "prior_process_group_unreadable",
                }
            )
            continue
        if group["status"] == "present" and not terminate_recorded_process_group(
            claim["owner_process_group_identity"],
            timeout_seconds=group_wait_seconds,
            authority_check=authority_check,
        ):
            recovery_required.append(
                {
                    "claim_id": claim["claim_id"],
                    "reason": "prior_process_group_live",
                }
            )
            continue
        try:
            readiness = resolve_input_readiness(Path(claim["skill_path"]))
        except (EvaluationError, OSError) as error:
            recovery_required.append(
                {
                    "claim_id": claim["claim_id"],
                    "reason": "claim_readiness_unreadable",
                    "detail": str(error),
                }
            )
            continue
        if (
            readiness["state"] not in {"drafting", "review_required"}
            or readiness["candidate_id"] != claim["candidate_id"]
            or readiness["skill_key"] != claim["skill_key"]
        ):
            recovery_required.append(
                {
                    "claim_id": claim["claim_id"],
                    "reason": "claim_readiness_not_recoverable",
                }
            )
            continue
        authority_check()
        recovered.append(
            recover_open_scheduled_claim(
                claim["claim_id"],
                expected_owner_run_id=claim["owner_run_id"],
                expected_owner_pid=claim["owner_pid"],
                expected_owner_process_identity=claim[
                    "owner_process_identity"
                ],
                expected_owner_process_group_identity=claim[
                    "owner_process_group_identity"
                ],
                expected_owner_boot_identity=claim["owner_boot_identity"],
            )
        )
    return {
        "recovered_claims": recovered,
        "recovery_required": recovery_required,
    }


def recoverable_claim_readiness(claim: dict[str, Any]) -> dict[str, Any]:
    readiness = resolve_input_readiness(Path(claim["skill_path"]))
    if (
        readiness["state"] not in {"drafting", "review_required"}
        or readiness["candidate_id"] != claim["candidate_id"]
        or readiness["skill_key"] != claim["skill_key"]
    ):
        raise EvaluationError(
            "open claim readiness is not recoverable for the recorded candidate"
        )
    return readiness


def inspect_operator_owner_death(
    claim: dict[str, Any], *, authority_check: Any
) -> dict[str, Any]:
    authority_check()
    current_boot = host_boot_identity()
    if claim["owner_boot_identity"] != current_boot:
        return {
            "owner_status": "prior_boot",
            "process_group_status": "prior_boot",
        }

    def inspect_same_boot() -> dict[str, Any]:
        authority_check()
        owner = inspect_recorded_process(
            claim["owner_pid"], claim["owner_process_identity"]
        )
        if owner["status"] == "present":
            raise EvaluationError("recorded evaluation-input owner is still live")
        if owner["status"] == "unreadable":
            raise EvaluationError(
                "same-boot evaluation-input owner identity is unreadable"
            )
        group = inspect_recorded_process_group(
            claim["owner_process_group_identity"]
        )
        if group["status"] == "present":
            raise EvaluationError(
                "recorded evaluation-input process group is still live"
            )
        if group["status"] == "unreadable":
            raise EvaluationError(
                "same-boot evaluation-input process group is unreadable"
            )
        return {
            "owner_status": owner["status"],
            "process_group_status": group["status"],
        }

    inspect_same_boot()
    return inspect_same_boot()


def evaluation_input_recovery_path() -> Path:
    return claim_ledger_path().with_name(
        "evaluation-input-recovery-required.json"
    )


def persist_evaluation_input_recovery_required(
    recovery_required: list[dict[str, Any]],
    *,
    authority_check: Any,
) -> dict[str, Any] | None:
    path = evaluation_input_recovery_path()
    if recovery_required:
        rows = sorted(
            [
                {
                    "claim_id": require_sha256(
                        row.get("claim_id"), "recovery-required claim"
                    ),
                    "reason": require_text(
                        row.get("reason"), "recovery-required reason"
                    ),
                }
                for row in recovery_required
            ],
            key=lambda row: row["claim_id"],
        )
        if any(
            re.fullmatch(r"[a-z][a-z0-9_]{0,127}", row["reason"]) is None
            for row in rows
        ):
            raise EvaluationError("recovery-required reason is malformed")
        if len({row["claim_id"] for row in rows}) != len(rows):
            raise EvaluationError("recovery-required claims are duplicated")
        record = {
            "schema_version": 1,
            "kind": "evaluation_input_recovery_required",
            "claims": rows,
        }
        record["record_sha256"] = identity_with("record_sha256", record)
        authority_check()
        if path.is_symlink():
            raise EvaluationError(
                "evaluation-input recovery marker must not be a symlink"
            )
        atomic_write(path, record)
        return record
    if path.exists() or path.is_symlink():
        authority_check()
        if path.is_symlink() or not path.is_file():
            raise EvaluationError(
                "evaluation-input recovery marker is not a regular file"
            )
        path.unlink()
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return None


def v2_input_owner_reconcile(_args: argparse.Namespace) -> dict[str, Any]:
    assert_input_owner_authority()
    publications = []
    failures = []
    for publication in pending_terminal_publications():
        try:
            assert_input_owner_authority()
            publications.append(
                publish_pending_terminal(
                    publication, authority_check=assert_input_owner_authority
                )
            )
        except (ClaimLedgerError, EvaluationError, OSError) as error:
            failures.append(
                {
                    "claim_id": publication["claim_id"],
                    "error": str(error),
                }
            )
    if failures:
        raise EvaluationError(
            "terminal publication recovery incomplete: "
            + json.dumps(failures, sort_keys=True, separators=(",", ":"))
        )
    claims = reconcile_open_owner_claims(
        authority_check=assert_input_owner_authority
    )
    recovery_marker = persist_evaluation_input_recovery_required(
        claims["recovery_required"],
        authority_check=assert_input_owner_authority,
    )
    for publication in pending_terminal_publications():
        try:
            assert_input_owner_authority()
            publications.append(
                publish_pending_terminal(
                    publication, authority_check=assert_input_owner_authority
                )
            )
        except (ClaimLedgerError, EvaluationError, OSError) as error:
            failures.append(
                {
                    "claim_id": publication["claim_id"],
                    "error": str(error),
                }
            )
    if failures:
        raise EvaluationError(
            "open-claim terminal recovery incomplete: "
            + json.dumps(failures, sort_keys=True, separators=(",", ":"))
        )
    return {
        "status": "reconciled",
        "terminal_publications": publications,
        "recovery_marker": recovery_marker,
        **claims,
    }


def v2_input_owner_recover(args: argparse.Namespace) -> dict[str, Any]:
    if args.confirm_owner_dead is not True:
        raise EvaluationError(
            "operator owner recovery requires --confirm-owner-dead"
        )
    assert_input_owner_operator_authority()
    claim_id = require_sha256(args.claim_id, "claim ID")
    expected_owner_run_id = require_text(
        args.expected_owner_run_id, "expected owner run ID"
    )
    open_matches = [
        claim
        for claim in open_scheduled_claims()
        if claim["claim_id"] == claim_id
    ]
    if len(open_matches) > 1:
        raise EvaluationError(
            "operator owner recovery requires one exact open scheduled claim"
        )
    if not open_matches:
        pending_matches = [
            publication
            for publication in pending_terminal_publications()
            if publication["claim_id"] == claim_id
            and publication["owner_run_id"] == expected_owner_run_id
            and publication["owner_mode"] == "scheduled"
            and publication["readiness_state"] == "invalid"
            and publication["readiness_reason"] == "owner_interrupted"
        ]
        if len(pending_matches) != 1:
            raise EvaluationError(
                "operator owner recovery requires one exact open or pending "
                "owner-interrupted scheduled claim"
            )
        with input_readiness_state_lock():
            published = publish_pending_terminal(
                pending_matches[0],
                authority_check=assert_input_owner_operator_authority,
                readiness_lock_held=True,
            )
        remaining = [
            {
                "claim_id": row["claim_id"],
                "reason": "operator_inspection_required",
            }
            for row in open_scheduled_claims()
        ]
        marker = persist_evaluation_input_recovery_required(
            remaining,
            authority_check=assert_input_owner_operator_authority,
        )
        return {
            "status": "replayed",
            "claim_id": claim_id,
            "owner_run_id": expected_owner_run_id,
            "death_proof": {
                "owner_status": "retained_owner_recovery",
                "process_group_status": "retained_owner_recovery",
            },
            "claim": None,
            "terminal_publication": published,
            "recovery_marker": marker,
        }
    claim = open_matches[0]
    if claim["owner_run_id"] != expected_owner_run_id:
        raise EvaluationError(
            "open claim owner run differs from --expected-owner-run-id"
        )
    proof = inspect_operator_owner_death(
        claim, authority_check=assert_input_owner_operator_authority
    )
    with input_readiness_state_lock():
        recoverable_claim_readiness(claim)
        proof = inspect_operator_owner_death(
            claim, authority_check=assert_input_owner_operator_authority
        )
        assert_input_owner_operator_authority()
        recovered = recover_open_scheduled_claim(
            claim["claim_id"],
            expected_owner_run_id=claim["owner_run_id"],
            expected_owner_pid=claim["owner_pid"],
            expected_owner_process_identity=claim[
                "owner_process_identity"
            ],
            expected_owner_process_group_identity=claim[
                "owner_process_group_identity"
            ],
            expected_owner_boot_identity=claim["owner_boot_identity"],
        )
        pending = [
            publication
            for publication in pending_terminal_publications()
            if publication["claim_id"] == claim["claim_id"]
        ]
        if len(pending) != 1:
            raise EvaluationError(
                "recovered claim lacks one exact pending terminal publication"
            )
        published = publish_pending_terminal(
            pending[0],
            authority_check=assert_input_owner_operator_authority,
            readiness_lock_held=True,
        )
    remaining = [
        {
            "claim_id": row["claim_id"],
            "reason": "operator_inspection_required",
        }
        for row in open_scheduled_claims()
    ]
    marker = persist_evaluation_input_recovery_required(
        remaining,
        authority_check=assert_input_owner_operator_authority,
    )
    return {
        "status": "recovered",
        "claim_id": claim["claim_id"],
        "owner_run_id": claim["owner_run_id"],
        "death_proof": proof,
        "claim": recovered,
        "terminal_publication": published,
        "recovery_marker": marker,
    }


def v2_input_ready(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = resolve_path(Path(args.skill_dir), "skill directory")
    resolved = validate_input_manifest(
        skill_dir, require_sha256(args.manifest, "input manifest digest")
    )
    validation_sha256 = require_sha256(
        args.validation, "input validation receipt"
    )
    review_sha256s = sorted(
        require_sha256(item, "input review receipt") for item in args.review
    )
    validate_input_receipts(
        resolved, validation_sha256, review_sha256s
    )
    bounded = is_bounded_input_manifest(resolved["manifest"])
    claim_id = None
    expected_review_set = None
    if bounded:
        if not args.claim_id:
            raise EvaluationError(
                "bounded-safe-author readiness requires an authoring claim"
            )
        claim_id = require_sha256(args.claim_id, "claim ID")
        expected_review_set = bounded_review_set_id(
            resolved, claim_id, review_sha256s
        )
    elif args.claim_id:
        raise EvaluationError(
            "manual readiness cannot bind an authoring claim"
        )
    with input_readiness_state_lock():
        pointer = load_input_current_pointer(skill_dir)
        if pointer is not None and pointer["candidate_id"] == resolved["candidate_id"]:
            transitions, tips = load_input_transition_history(
                skill_dir, resolved["candidate_id"]
            )
            if tips == {pointer["transition_id"]}:
                validate_input_transition_chain(
                    transitions, pointer["transition_id"]
                )
                current = transitions[pointer["transition_id"]]
                if current["state"] == "ready":
                    if ready_transition_matches(
                        current,
                        manifest_sha256=resolved["input_manifest_sha256"],
                        validation_sha256=validation_sha256,
                        review_sha256s=review_sha256s,
                        claim_id=claim_id,
                        review_set_id=expected_review_set,
                    ):
                        claim_facts = (
                            complete_claim_ready(
                                claim_id,
                                skill_path=str(skill_dir),
                                skill_key=latest_key(str(skill_dir)),
                                candidate_id=resolved["candidate_id"],
                                manifest_sha256=resolved["input_manifest_sha256"],
                                validation_receipt_sha256=validation_sha256,
                                review_receipt_sha256s=review_sha256s,
                            )
                            if bounded
                            else None
                        )
                        resolve_ready_input(skill_dir)
                        if bounded:
                            acknowledge_terminal_publication(
                                claim_id, current
                            )
                        return ready_result(skill_dir, current, claim_facts)
            else:
                recoverable = [
                    transition
                    for transition in transitions.values()
                    if transition["prior_transition_id"]
                    == pointer["transition_id"]
                    and ready_transition_matches(
                        transition,
                        manifest_sha256=resolved["input_manifest_sha256"],
                        validation_sha256=validation_sha256,
                        review_sha256s=review_sha256s,
                        claim_id=claim_id,
                        review_set_id=expected_review_set,
                    )
                ]
                if len(recoverable) != 1 or tips != {
                    recoverable[0]["transition_id"]
                }:
                    raise EvaluationError(
                        "input readiness current pointer does not name the unique chain tip"
                    )
                recovered = recoverable[0]
                validate_input_transition_chain(
                    transitions, recovered["transition_id"]
                )
                claim_facts = (
                    complete_claim_ready(
                        claim_id,
                        skill_path=str(skill_dir),
                        skill_key=latest_key(str(skill_dir)),
                        candidate_id=resolved["candidate_id"],
                        manifest_sha256=resolved["input_manifest_sha256"],
                        validation_receipt_sha256=validation_sha256,
                        review_receipt_sha256s=review_sha256s,
                    )
                    if bounded
                    else None
                )
                write_input_current_pointer(
                    skill_dir,
                    resolved["candidate_id"],
                    recovered["transition_id"],
                )
                resolve_ready_input(skill_dir)
                if bounded:
                    acknowledge_terminal_publication(
                        claim_id, recovered
                    )
                return ready_result(skill_dir, recovered, claim_facts)
        claim_facts = (
            complete_claim_ready(
                claim_id,
                skill_path=str(skill_dir),
                skill_key=latest_key(str(skill_dir)),
                candidate_id=resolved["candidate_id"],
                manifest_sha256=resolved["input_manifest_sha256"],
                validation_receipt_sha256=validation_sha256,
                review_receipt_sha256s=review_sha256s,
            )
            if bounded
            else None
        )
        result = _write_input_transition_locked(
            skill_dir,
            state="ready",
            reason="validated_and_reviewed",
            input_manifest_sha256=resolved["input_manifest_sha256"],
            validation_receipt_sha256=validation_sha256,
            review_receipt_sha256s=review_sha256s,
            created_at=args.created_at or now_iso(),
            claim_id=claim_facts["claim_id"] if claim_facts else None,
            review_set_id=(
                claim_facts["review_set_id"] if claim_facts else None
            ),
        )
        resolve_ready_input(skill_dir)
        if bounded:
            published = load_input_transition(
                skill_dir, resolved["candidate_id"], result["transition_id"]
            )
            acknowledge_terminal_publication(
                claim_id, published
            )
        return {
            **result,
            **(
                {
                    "claim_id": claim_facts["claim_id"],
                    "review_set_id": claim_facts["review_set_id"],
                }
                if claim_facts
                else {}
            ),
        }


def v2_input_claim_assert_ready(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = resolve_path(Path(args.skill_dir), "skill directory")
    resolved = validate_input_manifest(
        skill_dir, require_sha256(args.manifest, "input manifest digest")
    )
    if not is_bounded_input_manifest(resolved["manifest"]):
        raise EvaluationError(
            "claim readiness assertion requires bounded-safe-author input"
        )
    validation_sha256 = require_sha256(
        args.validation, "input validation receipt"
    )
    review_sha256s = sorted(
        require_sha256(item, "input review receipt") for item in args.review
    )
    validate_input_receipts(
        resolved, validation_sha256, review_sha256s
    )
    return assert_claim_ready(
        require_sha256(args.claim_id, "claim ID"),
        skill_path=str(skill_dir),
        skill_key=latest_key(str(skill_dir)),
        candidate_id=resolved["candidate_id"],
        manifest_sha256=resolved["input_manifest_sha256"],
        validation_receipt_sha256=validation_sha256,
        review_receipt_sha256s=review_sha256s,
    )


def load_input_transition_history(
    skill_dir: Path, candidate: str
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    readiness = input_readiness_dir(skill_dir, candidate)
    if readiness.is_symlink() or not readiness.is_dir():
        raise EvaluationError("input readiness transition root must be a real directory")
    transitions: dict[str, dict[str, Any]] = {}
    for path in sorted(readiness.iterdir()):
        if path.suffix != ".json":
            raise EvaluationError("input readiness transition root has an unknown entry")
        transition_id = f"sha256:{path.stem}"
        transitions[transition_id] = load_input_transition(
            skill_dir, candidate, transition_id
        )
    if not transitions:
        raise EvaluationError("input readiness transition chain is empty")
    referenced: set[str] = set()
    for transition in transitions.values():
        prior_id = transition["prior_transition_id"]
        if prior_id is None:
            continue
        if prior_id not in transitions:
            raise EvaluationError(
                "input readiness transition chain has a missing predecessor"
            )
        prior_state = transitions[prior_id]["state"]
        if transition["state"] not in INPUT_READINESS_TRANSITIONS[prior_state]:
            raise EvaluationError(
                f"readiness cannot transition from {prior_state} "
                f"to {transition['state']}"
            )
        referenced.add(prior_id)
    return transitions, set(transitions) - referenced


def validate_input_transition_chain(
    transitions: dict[str, dict[str, Any]], tip_id: str
) -> None:
    seen: set[str] = set()
    transition_id: str | None = tip_id
    while transition_id is not None:
        if transition_id in seen:
            raise EvaluationError("input readiness transition chain contains a cycle")
        if transition_id not in transitions:
            raise EvaluationError(
                "input readiness transition chain has a missing predecessor"
            )
        seen.add(transition_id)
        transition_id = transitions[transition_id]["prior_transition_id"]
    if seen != set(transitions):
        raise EvaluationError("input readiness transition history is disconnected")


def resolve_input_readiness(
    skill_dir: Path, *, missing_ok: bool = False
) -> dict[str, Any] | None:
    skill_dir = resolve_path(skill_dir, "skill directory")
    candidate, _ = candidate_id(skill_dir)
    pointer = load_input_current_pointer(skill_dir)
    if pointer is None:
        readiness = input_readiness_dir(skill_dir, candidate)
        if readiness.is_symlink() or (
            readiness.exists() and any(readiness.iterdir())
        ) or established_evaluation_state_exists(skill_dir, candidate):
            raise EvaluationError(
                "external evaluation input readiness current pointer is missing"
            )
        if missing_ok:
            return None
        raise EvaluationError("external evaluation input readiness is missing")
    if pointer["candidate_id"] != candidate:
        readiness = input_readiness_dir(skill_dir, candidate)
        if (
            readiness.is_symlink()
            or (readiness.exists() and any(readiness.iterdir()))
            or established_evaluation_state_exists(skill_dir, candidate)
        ):
            raise EvaluationError(
                "external evaluation input readiness pointer candidate is stale"
            )
        if missing_ok:
            return None
        raise EvaluationError("external evaluation input readiness is missing")
    transitions, tips = load_input_transition_history(skill_dir, candidate)
    if tips != {pointer["transition_id"]}:
        raise EvaluationError(
            "input readiness current pointer does not name the unique chain tip"
        )
    validate_input_transition_chain(transitions, pointer["transition_id"])
    return transitions[pointer["transition_id"]]


def established_evaluation_state_exists(
    skill_dir: Path, candidate: str
) -> bool:
    skill_key = latest_key(str(skill_dir))
    authority_path = v2_authority_path(skill_dir, candidate)
    if authority_path.exists() or authority_path.is_symlink():
        return True
    latest_path = (
        v2_evaluation_dir()
        / "latest"
        / f"{skill_key}.json"
    )
    if latest_path.exists() or latest_path.is_symlink():
        try:
            latest = load_json(latest_path)
        except EvaluationError:
            return True
        if latest.get("candidate_id") == candidate:
            return True
    transition_root = v2_transition_dir(skill_dir)
    if transition_root.is_symlink():
        return True
    if not transition_root.exists():
        return False
    if not transition_root.is_dir():
        return True
    for path in transition_root.iterdir():
        try:
            transition, _ = portfolio_transition(path, skill_key)
        except (EvaluationError, OSError):
            return True
        if transition["candidate_id"] == candidate:
            return True
    return False


def resolve_ready_input(
    skill_dir: Path, *, missing_ok: bool = False
) -> dict[str, Any] | None:
    skill_dir = resolve_path(skill_dir, "skill directory")
    candidate, _ = candidate_id(skill_dir)
    pointer = load_input_current_pointer(skill_dir)
    if pointer is None:
        readiness = input_readiness_dir(skill_dir, candidate)
        if (
            readiness.is_symlink()
            or (readiness.exists() and any(readiness.iterdir()))
            or established_evaluation_state_exists(skill_dir, candidate)
        ):
            raise EvaluationError(
                "ready external evaluation input current pointer is missing"
            )
        if missing_ok:
            return None
        raise EvaluationError("ready external evaluation input is missing")
    if pointer["candidate_id"] != candidate:
        readiness = input_readiness_dir(skill_dir, candidate)
        if (
            readiness.is_symlink()
            or (readiness.exists() and any(readiness.iterdir()))
            or established_evaluation_state_exists(skill_dir, candidate)
        ):
            raise EvaluationError(
                "ready external evaluation input pointer candidate is stale"
            )
        if missing_ok:
            return None
        raise EvaluationError("ready external evaluation input is missing")
    transition = resolve_input_readiness(skill_dir)
    if transition["state"] != "ready":
        if missing_ok:
            return None
        raise EvaluationError(
            f"external evaluation input is {transition['state']}, not ready"
        )
    resolved = validate_input_manifest(
        skill_dir, transition["input_manifest_sha256"]
    )
    validate_input_receipts(
        resolved,
        transition["validation_receipt_sha256"],
        transition["review_receipt_sha256s"],
    )
    return {**resolved, "transition_id": transition["transition_id"]}


def identity_with(field: str, value: dict[str, Any]) -> str:
    return f"sha256:{digest(canonical({key: item for key, item in value.items() if key != field}))}"


def validate_certificate(
    value: Any,
    candidate: str,
    input_manifest_sha256: str,
    suite_id: str,
    policy_id: str,
    observation_plan_id: str,
    profile: str,
    requirement: str,
    expected_executor: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    field = f"aggregate.certificates[{index}]"
    if not isinstance(value, dict):
        raise EvaluationError(f"{field} must be an object")
    require_exact_keys(
        value,
        field,
        {
            "schema_version",
            "kind",
            "status",
            "candidate_id",
            "input_manifest_sha256",
            "suite_id",
            "policy_id",
            "observation_plan_id",
            "profile",
            "requirement",
            "executor",
            "result_bundle_sha256",
            "result_bundle_id",
            "run_id",
            "certificate_id",
        },
    )
    if value.get("schema_version") != 2 or value.get("kind") != "executor_certificate":
        raise EvaluationError(f"{field} must be a schema-v2 executor_certificate")
    if value.get("status") not in CERTIFICATE_STATUSES:
        raise EvaluationError(f"{field}.status is invalid")
    if (
        value.get("candidate_id") != candidate
        or value.get("input_manifest_sha256") != input_manifest_sha256
        or value.get("suite_id") != suite_id
        or value.get("policy_id") != policy_id
    ):
        raise EvaluationError(f"{field} does not bind the aggregate inputs")
    if value.get("profile") != profile:
        raise EvaluationError(f"{field}.profile does not match policy")
    if value.get("requirement") != requirement:
        raise EvaluationError(f"{field}.requirement does not match its aggregate partition")
    expected_observation = observation_plan_id if requirement == "advisory" else None
    if value.get("observation_plan_id") != expected_observation:
        raise EvaluationError(f"{field}.observation_plan_id does not match its partition")
    require_sha256(value.get("result_bundle_sha256"), f"{field}.result_bundle_sha256")
    require_sha256(value.get("result_bundle_id"), f"{field}.result_bundle_id")
    require_sha256(value.get("run_id"), f"{field}.run_id")
    executor = validate_executor(value.get("executor"), f"{field}.executor")
    if executor != expected_executor:
        raise EvaluationError(f"{field}.executor does not match its aggregate partition")
    expected_id = identity_with("certificate_id", value)
    if value.get("certificate_id") != expected_id:
        raise EvaluationError(f"{field}.certificate_id does not match certificate content")
    return {**value, "executor": executor}


def validate_aggregate(
    value: Any, skill_dir: Path, candidate: str, input_manifest_sha256: str,
    suite: dict[str, Any], suite_id: str,
    policy: dict[str, Any], policy_id: str, allow_advisory_drift: bool = False
) -> dict[str, Any]:
    if not suite["cross_executor_authority"]:
        raise EvaluationError("legacy-compiled suites cannot anchor cross-executor authority")
    if not isinstance(value, dict):
        raise EvaluationError("aggregate receipt must be an object")
    require_exact_keys(
        value,
        "aggregate",
        {
            "schema_version",
            "kind",
            "status",
            "skill_path",
            "candidate_id",
            "candidate_inventory",
            "input_manifest_sha256",
            "suite_id",
            "policy_id",
            "observation_plan_id",
            "profile",
            "required_executors",
            "advisory_executors",
            "certificates",
            "required_certificate_set_id",
            "aggregate_id",
        },
    )
    if value.get("schema_version") != 2 or value.get("kind") != "aggregate_receipt":
        raise EvaluationError("aggregate receipt must be schema-v2 aggregate_receipt")
    if value.get("skill_path") != str(skill_dir):
        raise EvaluationError("aggregate receipt belongs to another skill path")
    if (
        value.get("candidate_id") != candidate
        or value.get("input_manifest_sha256") != input_manifest_sha256
        or value.get("suite_id") != suite_id
        or value.get("policy_id") != policy_id
        or value.get("profile") != policy["profile"]
    ):
        raise EvaluationError("aggregate receipt input identity is stale or malformed")
    candidate_inventory = value.get("candidate_inventory")
    if not isinstance(candidate_inventory, list) or f"sha256:{digest(canonical(candidate_inventory))}" != candidate:
        raise EvaluationError("aggregate receipt candidate inventory is stale or malformed")
    required_executors = value.get("required_executors")
    advisory_executors = value.get("advisory_executors")
    if required_executors != policy["required_executors"]:
        raise EvaluationError("aggregate required executors differ from the current authority policy")
    if not isinstance(advisory_executors, list):
        raise EvaluationError("aggregate advisory_executors must be a list")
    advisory_executors = [
        validate_executor(item, f"aggregate.advisory_executors[{index}]")
        for index, item in enumerate(advisory_executors)
    ]
    advisory_names = [item["name"] for item in advisory_executors]
    if (
        len(set(advisory_names)) != len(advisory_names)
        or advisory_names != [name for name in EXECUTOR_NAMES if name in advisory_names]
        or set(advisory_names) & {item["name"] for item in required_executors}
    ):
        raise EvaluationError("aggregate advisory executor partition is invalid")
    aggregate_observation_plan_id = observation_plan_identity(
        {**policy, "advisory_executors": advisory_executors}, policy_id
    )
    if value.get("observation_plan_id") != aggregate_observation_plan_id:
        raise EvaluationError("aggregate observation_plan_id does not match advisory inputs")
    if not allow_advisory_drift and advisory_executors != policy["advisory_executors"]:
        raise EvaluationError("aggregate advisory executors differ from the current observation plan")
    certificates_value = value.get("certificates")
    if not isinstance(certificates_value, list):
        raise EvaluationError("aggregate.certificates must be a list")
    expected_partitions = [
        (requirement, executor)
        for requirement, executors in (
            ("required", required_executors),
            ("advisory", advisory_executors),
        )
        for executor in executors
    ]
    if len(certificates_value) != len(expected_partitions):
        raise EvaluationError("aggregate certificates must cover every selected executor")
    certificates = [
        validate_certificate(
            item,
            candidate,
            input_manifest_sha256,
            suite_id,
            policy_id,
            aggregate_observation_plan_id,
            policy["profile"],
            requirement,
            executor,
            index,
        )
        for index, (item, (requirement, executor)) in enumerate(
            zip(certificates_value, expected_partitions)
        )
    ]
    required_certificates = [
        certificate for certificate in certificates if certificate["requirement"] == "required"
    ]
    statuses = [certificate["status"] for certificate in required_certificates]
    expected_status = (
        "regression" if "regression" in statuses
        else "inconclusive" if any(status != "pass" for status in statuses)
        else "pass"
    )
    if value.get("status") != expected_status:
        raise EvaluationError(
            f"aggregate.status must be {expected_status!r} for the independent executor certificates"
        )
    expected_set_id = required_certificate_set_identity(
        candidate,
        input_manifest_sha256,
        suite_id,
        policy_id,
        policy["profile"],
        required_certificates,
    )
    if value.get("required_certificate_set_id") != expected_set_id:
        raise EvaluationError("aggregate required_certificate_set_id does not match required evidence")
    expected_id = identity_with("aggregate_id", value)
    if value.get("aggregate_id") != expected_id:
        raise EvaluationError("aggregate.aggregate_id does not match aggregate content")
    return {**value, "certificates": certificates}


def required_certificate_set_identity(
    candidate: str,
    input_manifest_sha256: str,
    suite_id: str,
    policy_id: str,
    profile: str,
    certificates: list[dict[str, Any]],
) -> str:
    return f"sha256:{digest(canonical({
        'candidate_id': candidate,
        'input_manifest_sha256': input_manifest_sha256,
        'suite_id': suite_id,
        'policy_id': policy_id,
        'profile': profile,
        'certificate_ids': [item['certificate_id'] for item in certificates],
    }))}"


def load_v2_inputs(skill_dir: Path, suite_path: str | None, policy_path: str | None) -> tuple[
    str, list[dict[str, Any]], dict[str, Any], str, dict[str, Any], str
]:
    current_candidate, files = candidate_id(skill_dir)
    suite, suite_id = load_suite(Path(suite_path).resolve() if suite_path else skill_dir / CASE_FILE)
    policy, policy_id = load_policy(
        Path(policy_path).resolve() if policy_path else skill_dir / POLICY_FILE
    )
    return current_candidate, files, suite, suite_id, policy, policy_id


def v2_prepare(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    resolved = resolve_ready_input(skill_dir, missing_ok=True)
    if resolved is None:
        current_candidate, files, suite, suite_id, policy, policy_id = load_v2_inputs(
            skill_dir, args.suite, args.policy
        )
        input_manifest_sha256 = None
    else:
        if args.suite or args.policy:
            raise EvaluationError(
                "ready external inputs cannot be overridden by suite or policy paths"
            )
        current_candidate = resolved["candidate_id"]
        files = resolved["candidate_inventory"]
        suite = resolved["suite"]
        suite_id = resolved["suite_id"]
        policy = resolved["policy"]
        policy_id = resolved["policy_id"]
        input_manifest_sha256 = resolved["input_manifest_sha256"]
    return {
        "candidate_id": current_candidate,
        "candidate_inventory": files,
        "input_manifest_sha256": input_manifest_sha256,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "observation_plan_id": observation_plan_identity(policy, policy_id),
        "profile": policy["profile"],
        "trials_per_arm": policy["trials_per_arm"],
        "required_executors": policy["required_executors"],
        "advisory_executors": policy["advisory_executors"],
        "cross_executor_authority": suite["cross_executor_authority"],
    }


def v2_suite_validate(args: argparse.Namespace) -> dict[str, Any]:
    suite, suite_id = load_suite(Path(args.suite).resolve())
    return {
        "suite_id": suite_id,
        "schema_version": suite["schema_version"],
        "compiled_from_schema_version": suite["compiled_from_schema_version"],
        "cross_executor_authority": suite["cross_executor_authority"],
        "case_classes": [case["class"] for case in suite["cases"]],
    }


def v2_policy_validate(args: argparse.Namespace) -> dict[str, Any]:
    policy, policy_id = load_policy(Path(args.policy).resolve())
    return {
        "policy_id": policy_id,
        "observation_plan_id": observation_plan_identity(policy, policy_id),
        "profile": policy["profile"],
        "trials_per_arm": policy["trials_per_arm"],
        "required_executors": policy["required_executors"],
        "advisory_executors": policy["advisory_executors"],
        "comparator": policy["comparator"],
    }


def configured_executor_names(variable: str, default: str, allow_empty: bool) -> list[str]:
    configured = os.environ.get(variable, default)
    if configured == "" and allow_empty:
        return []
    names = [item.strip() for item in configured.split(",")]
    if not names or any(not item for item in names):
        qualifier = "an ordered comma-separated set" if allow_empty else "a non-empty ordered comma-separated set"
        raise EvaluationError(f"{variable} must be {qualifier}")
    if len(set(names)) != len(names) or any(item not in EXECUTOR_NAMES for item in names):
        raise EvaluationError(f"{variable} contains an unknown or duplicate executor")
    expected = [name for name in EXECUTOR_NAMES if name in names]
    if names != expected:
        raise EvaluationError(f"{variable} must follow copilot, claude, codex order")
    return names


def desired_executor_names() -> list[str]:
    return configured_executor_names("DREAMING_EVALUATION_EXECUTORS", "copilot", False)


def desired_advisory_executor_names() -> list[str]:
    return configured_executor_names("DREAMING_ADVISORY_EVALUATION_EXECUTORS", "", True)


def desired_executor_roles() -> tuple[list[str], list[str]]:
    required = desired_executor_names()
    advisory = desired_advisory_executor_names()
    overlap = set(required) & set(advisory)
    if overlap:
        raise EvaluationError(
            "DREAMING_EVALUATION_EXECUTORS and DREAMING_ADVISORY_EVALUATION_EXECUTORS must be disjoint"
        )
    return required, advisory


def validate_harness_executor(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError(f"{field} must be an object")
    require_exact_keys(
        value,
        field,
        {
            "name",
            "requirement",
            "model",
            "adapter_id",
            "adapter_version",
            "adapter_executable_sha256",
            "cli_executable_sha256",
            "cli_version",
            "tool_policy_id",
            "limits",
            "sandbox_id",
        },
    )
    requirement = value.get("requirement")
    if requirement not in {"required", "advisory"}:
        raise EvaluationError(f"{field}.requirement must be required or advisory")
    base = validate_executor(
        {key: value[key] for key in (
            "name", "model", "adapter_id", "adapter_version",
            "adapter_executable_sha256", "cli_executable_sha256",
        )},
        field,
    )
    limits = value.get("limits")
    if not isinstance(limits, dict):
        raise EvaluationError(f"{field}.limits must be an object")
    require_exact_keys(limits, f"{field}.limits", {"timeout_seconds", "token_budget", "output_bytes"})
    return {
        **base,
        "requirement": requirement,
        "cli_version": require_text(value.get("cli_version"), f"{field}.cli_version"),
        "tool_policy_id": require_sha256(value.get("tool_policy_id"), f"{field}.tool_policy_id"),
        "limits": {
            "timeout_seconds": require_positive_int(limits.get("timeout_seconds"), f"{field}.limits.timeout_seconds"),
            "token_budget": require_positive_int(limits.get("token_budget"), f"{field}.limits.token_budget"),
            "output_bytes": require_positive_int(limits.get("output_bytes"), f"{field}.limits.output_bytes"),
        },
        "sandbox_id": require_sha256(value.get("sandbox_id"), f"{field}.sandbox_id"),
    }


def validate_compilation_config(
    path: Path, suite: dict[str, Any], policy: dict[str, Any], harness_sha: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = load_json(path)
    require_exact_keys(
        raw,
        "compilation",
        {
            "schema_version",
            "kind",
            "harness_executable_sha256",
            "tool_policy_id",
            "retention_policy_id",
            "limits",
            "identity_markers",
            "graders",
            "case_runtime",
            "rubric",
            "executors",
            "comparator",
        },
    )
    if raw.get("schema_version") != COMPILATION_SCHEMA_VERSION or raw.get("kind") != "dreaming_evaluation_compilation":
        raise EvaluationError("unsupported Dreaming evaluation compilation config")
    if raw.get("harness_executable_sha256") != harness_sha:
        raise EvaluationError("compilation harness digest does not match the selected executable")
    tool_policy_id = require_sha256(raw.get("tool_policy_id"), "compilation.tool_policy_id")
    retention_policy_id = require_sha256(
        raw.get("retention_policy_id"), "compilation.retention_policy_id"
    )
    limits = raw.get("limits")
    if not isinstance(limits, dict):
        raise EvaluationError("compilation.limits must be an object")
    require_exact_keys(
        limits,
        "compilation.limits",
        {"timeout_seconds", "output_bytes", "file_bytes", "global_concurrency", "per_executor_concurrency"},
    )
    normalized_limits = {
        key: require_positive_int(limits.get(key), f"compilation.limits.{key}")
        for key in limits
    }
    markers = raw.get("identity_markers")
    if not isinstance(markers, list) or not markers or not all(isinstance(item, str) and item for item in markers):
        raise EvaluationError("compilation.identity_markers must be a non-empty text list")
    if len(set(markers)) != len(markers):
        raise EvaluationError("compilation.identity_markers cannot contain duplicates")
    grader_values = raw.get("graders")
    if not isinstance(grader_values, list) or not grader_values:
        raise EvaluationError("compilation.graders must be a non-empty list")
    source_graders = {item["id"]: item for item in suite["graders"]}
    graders: list[dict[str, Any]] = []
    for index, grader in enumerate(grader_values):
        field = f"compilation.graders[{index}]"
        if not isinstance(grader, dict):
            raise EvaluationError(f"{field} must be an object")
        require_exact_keys(grader, field, {"id", "type", "safety", "config"})
        grader_id = require_text(grader.get("id"), f"{field}.id")
        source = source_graders.get(grader_id)
        if source is None:
            raise EvaluationError(f"{field}.id is not declared by the schema-v2 suite")
        if grader.get("type") != source["type"] or grader.get("safety") != source["safety"]:
            raise EvaluationError(f"{field} changes the schema-v2 grader contract")
        if not isinstance(grader.get("config"), dict):
            raise EvaluationError(f"{field}.config must be an object")
        if source["identity"] != f"sha256:{digest(canonical(grader))}":
            raise EvaluationError(f"{field} does not match the suite grader identity")
        graders.append(grader)
    if {item["id"] for item in graders} != set(source_graders):
        raise EvaluationError("compilation.graders must define every suite grader exactly once")
    runtime_values = raw.get("case_runtime")
    if not isinstance(runtime_values, list):
        raise EvaluationError("compilation.case_runtime must be a list")
    runtime: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(runtime_values):
        field = f"compilation.case_runtime[{index}]"
        if not isinstance(item, dict):
            raise EvaluationError(f"{field} must be an object")
        require_exact_keys(item, field, {"id", "fixture", "artifacts", "semantic"})
        case_id = require_text(item.get("id"), f"{field}.id")
        artifacts = item.get("artifacts")
        if (
            case_id in runtime
            or not isinstance(artifacts, list)
            or len(set(artifacts)) != len(artifacts)
            or not all(isinstance(value, str) and value and not Path(value).is_absolute() and ".." not in Path(value).parts for value in artifacts)
        ):
            raise EvaluationError(f"{field} has invalid case runtime data")
        if not isinstance(item.get("semantic"), bool):
            raise EvaluationError(f"{field}.semantic must be a boolean")
        runtime[case_id] = {
            "fixture": require_text(item.get("fixture"), f"{field}.fixture"),
            "artifacts": artifacts,
            "semantic": item["semantic"],
        }
    if set(runtime) != {case["id"] for case in suite["cases"]}:
        raise EvaluationError("compilation.case_runtime must define every suite case exactly once")
    rubric = raw.get("rubric")
    if not isinstance(rubric, dict) or f"sha256:{digest(canonical(rubric))}" != policy["comparator"]["rubric_id"]:
        raise EvaluationError("compilation.rubric does not match policy comparator rubric identity")
    executors_value = raw.get("executors")
    if not isinstance(executors_value, list):
        raise EvaluationError("compilation.executors must be a list")
    executors = [
        validate_harness_executor(item, f"compilation.executors[{index}]")
        for index, item in enumerate(executors_value)
    ]
    required_names, advisory_names = desired_executor_roles()
    expected_names = required_names + advisory_names
    if [item["name"] for item in executors] != expected_names:
        raise EvaluationError("compilation executors differ from the configured required and advisory sets")
    expected_executors = [
        {**item, "requirement": requirement}
        for requirement, values in (
            ("required", policy["required_executors"]),
            ("advisory", policy["advisory_executors"]),
        )
        for item in values
    ]
    compiled_policy_executors = [
        {
            key: item[key]
            for key in (
                "name",
                "model",
                "adapter_id",
                "adapter_version",
                "adapter_executable_sha256",
                "cli_executable_sha256",
                "requirement",
            )
        }
        for item in executors
    ]
    if compiled_policy_executors != expected_executors:
        raise EvaluationError("compilation executors differ from the exact policy executor identities and roles")
    if any(item["tool_policy_id"] != tool_policy_id for item in executors):
        raise EvaluationError("executor tool policy differs from the compilation tool policy")
    comparator = raw.get("comparator")
    if comparator != policy["comparator"]:
        raise EvaluationError("compilation comparator differs from the exact policy comparator")
    harness_suite = {
        "schema_version": HARNESS_CONTRACT_VERSION,
        "kind": "skill_evaluation_suite",
        "grader_set_id": f"sha256:{digest(canonical(graders))}",
        "identity_markers": markers,
        "graders": graders,
        "cases": [
            {
                "id": case["id"],
                "class": case["class"],
                "task_id": case["task_id"],
                "prompt": case["prompt"],
                "fixture": runtime[case["id"]]["fixture"],
                "artifacts": runtime[case["id"]]["artifacts"],
                "graders": case["deterministic_graders"],
                "semantic": runtime[case["id"]]["semantic"],
            }
            for case in suite["cases"]
        ],
        "rubric": rubric,
    }
    normalized = {
        "schema_version": COMPILATION_SCHEMA_VERSION,
        "kind": "dreaming_evaluation_compilation",
        "harness_executable_sha256": harness_sha,
        "tool_policy_id": tool_policy_id,
        "retention_policy_id": retention_policy_id,
        "limits": normalized_limits,
        "identity_markers": markers,
        "graders": graders,
        "case_runtime": runtime_values,
        "rubric": rubric,
        "executors": executors,
        "comparator": comparator,
    }
    return normalized, harness_suite


def validate_routing(path: Path, executors: list[dict[str, Any]], comparator: dict[str, Any]) -> dict[str, Any]:
    routing = load_json(path)
    require_exact_keys(routing, "routing", {"schema_version", "kind", "executors", "comparator"})
    if routing.get("schema_version") != 1 or routing.get("kind") != "skill_evaluation_routing":
        raise EvaluationError("unsupported harness routing config")
    values = routing.get("executors")
    if not isinstance(values, list):
        raise EvaluationError("routing.executors must be a list")
    expected = {item["name"]: item for item in executors}
    actual: dict[str, dict[str, Any]] = {}
    for index, route in enumerate(values):
        field = f"routing.executors[{index}]"
        if not isinstance(route, dict):
            raise EvaluationError(f"{field} must be an object")
        require_exact_keys(route, field, {"name", "adapter_id", "adapter_executable_sha256", "argv"})
        name = require_text(route.get("name"), f"{field}.name")
        argv = route.get("argv")
        if name in actual or not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise EvaluationError(f"{field} has invalid argv or duplicate name")
        executable = Path(argv[0]).resolve()
        expected_executor = expected.get(name)
        if (
            expected_executor is None
            or route.get("adapter_id") != expected_executor["adapter_id"]
            or route.get("adapter_executable_sha256") != expected_executor["adapter_executable_sha256"]
            or sha256_file(executable) != expected_executor["adapter_executable_sha256"]
        ):
            raise EvaluationError(f"{field} is not an authorized exact executor route")
        actual[name] = route
    if list(actual) != [item["name"] for item in executors]:
        raise EvaluationError("routing executor set or order differs from the selected executor set")
    comparator_route = routing.get("comparator")
    if not isinstance(comparator_route, dict):
        raise EvaluationError("routing.comparator must be an object")
    require_exact_keys(
        comparator_route,
        "routing.comparator",
        {"route", "adapter_id", "adapter_executable_sha256", "argv"},
    )
    argv = comparator_route.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise EvaluationError("routing.comparator.argv must be a non-empty text list")
    if (
        any(comparator_route.get(key) != comparator[key] for key in ("route", "adapter_id", "adapter_executable_sha256"))
        or sha256_file(Path(argv[0]).resolve()) != comparator["adapter_executable_sha256"]
    ):
        raise EvaluationError("routing.comparator is not the authorized exact comparator route")
    return routing


def copy_sealed_tree(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    if not source.is_dir() or source.is_symlink():
        raise EvaluationError(f"{source} must be a real directory")
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_symlink():
            raise EvaluationError(f"{path}: symlinks are forbidden in sealed inputs")
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
            os.chmod(target, path.stat().st_mode & 0o777)
        else:
            raise EvaluationError(f"{path}: sealed input must be a regular file")


def materialize_registry_run_objects(
    resolved: dict[str, Any], run_dir: Path
) -> None:
    for entry in resolved["manifest"]["objects"]:
        if entry["role"] not in {"fixture", "grader"}:
            continue
        target = run_dir / entry["logical_path"]
        try:
            target.relative_to(run_dir)
        except ValueError as exc:
            raise EvaluationError("registry run object escapes the run directory") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            require_registry_file(
                registry_object_path(entry["sha256"]),
                input_registry_component("objects"),
                "registry run object",
            )
        )
        os.chmod(target, 0o600)


def validate_materialized_registry_run(
    resolved: dict[str, Any], run_dir: Path, run_manifest: dict[str, Any]
) -> None:
    expected = [
        {
            "path": entry["logical_path"],
            "sha256": entry["sha256"],
            "size": entry["size"],
        }
        for entry in resolved["manifest"]["objects"]
        if entry["role"] in {"fixture", "grader"}
    ]
    actual = [
        {
            "path": f"{root_name}/{entry['path']}",
            "sha256": entry["sha256"],
            "size": entry["size"],
        }
        for root_name in ("fixtures", "graders")
        for entry in canonical_file_inventory(run_dir / root_name)
    ]
    if actual != expected:
        raise EvaluationError(
            "compiled run fixture or grader objects differ from the ready input manifest"
        )
    if load_json(run_dir / "suite.json") != resolved["harness_suite"]:
        raise EvaluationError(
            "compiled harness suite differs from the ready input manifest"
        )
    if load_json(run_dir / "source-routing.json") != resolved["routing"]:
        raise EvaluationError(
            "compiled source routing differs from the ready input manifest"
        )
    if (
        run_manifest.get("suite_id")
        != f"sha256:{digest(canonical(resolved['harness_suite']))}"
        or run_manifest.get("grader_set_id")
        != resolved["harness_suite"]["grader_set_id"]
    ):
        raise EvaluationError(
            "compiled harness suite identities differ from the ready input manifest"
        )


def v2_run_compile(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    run_dir = Path(args.run_dir).resolve()
    if not run_dir.is_dir() or run_dir.is_symlink() or any(run_dir.iterdir()):
        raise EvaluationError("run directory must exist, be real, and be empty")
    harness = require_trusted_harness(Path(args.harness))
    harness_sha = sha256_file(harness)
    resolved = resolve_ready_input(skill_dir, missing_ok=True)
    if resolved is None:
        candidate, files, suite, suite_id, policy, policy_id = load_v2_inputs(
            skill_dir, args.suite, args.policy
        )
        input_manifest_sha256 = None
        if not args.config or not args.routing:
            raise EvaluationError(
                "root-local development compile requires config and routing paths"
            )
        config_path = Path(args.config).resolve()
        config, harness_suite = validate_compilation_config(
            config_path, suite, policy, harness_sha
        )
        routing_path = Path(args.routing).resolve()
        routing = validate_routing(
            routing_path, config["executors"], config["comparator"]
        )
    else:
        if args.suite or args.policy:
            raise EvaluationError(
                "ready external inputs cannot be overridden by suite or policy paths"
            )
        candidate = resolved["candidate_id"]
        files = resolved["candidate_inventory"]
        suite = resolved["suite"]
        suite_id = resolved["suite_id"]
        policy = resolved["policy"]
        policy_id = resolved["policy_id"]
        input_manifest_sha256 = resolved["input_manifest_sha256"]
        config = resolved["config"]
        harness_suite = resolved["harness_suite"]
        routing = resolved["routing"]
        if args.config:
            requested_config, _ = validate_compilation_config(
                Path(args.config).resolve(), suite, policy, harness_sha
            )
            if requested_config != config:
                raise EvaluationError(
                    "compile config differs from the ready external input manifest"
                )
        if args.routing:
            requested_routing = validate_routing(
                Path(args.routing).resolve(),
                config["executors"],
                config["comparator"],
            )
            if requested_routing != routing:
                raise EvaluationError(
                    "compile routing differs from the ready external input manifest"
                )
    required_names, advisory_names = desired_executor_roles()
    if required_names != [item["name"] for item in policy["required_executors"]]:
        raise EvaluationError("policy required executors differ from DREAMING_EVALUATION_EXECUTORS")
    if advisory_names != [item["name"] for item in policy["advisory_executors"]]:
        raise EvaluationError(
            "policy advisory executors differ from DREAMING_ADVISORY_EVALUATION_EXECUTORS"
        )
    if resolved is not None and (
        harness_sha != resolved["manifest"]["harness_executable_sha256"]
    ):
        raise EvaluationError(
            "compile arguments differ from the ready external input manifest"
        )
    inventory(skill_dir, run_dir / "candidate")
    if resolved is None:
        copy_sealed_tree(config_path.parent / "fixtures", run_dir / "fixtures")
        copy_sealed_tree(config_path.parent / "graders", run_dir / "graders")
    else:
        materialize_registry_run_objects(resolved, run_dir)
    atomic_write(run_dir / "source-suite.json", suite)
    atomic_write(run_dir / "source-policy.json", policy)
    atomic_write(run_dir / "source-routing.json", routing)
    atomic_write(run_dir / "compilation.json", config)
    atomic_write(
        run_dir / "dreaming-input.json",
        {
            "schema_version": 1,
            "skill_path": str(skill_dir),
            "candidate_id": candidate,
            "candidate_inventory": files,
            "input_manifest_sha256": input_manifest_sha256,
            "suite_id": suite_id,
            "policy_id": policy_id,
            "observation_plan_id": observation_plan_identity(policy, policy_id),
        },
    )
    atomic_write(run_dir / "suite.json", harness_suite)
    file_inventory = canonical_file_inventory(run_dir)
    harness_projection = [
        {
            "path": item["path"].removeprefix("candidate/"),
            "sha256": item["sha256"],
            "size": item["size"],
        }
        for item in file_inventory
        if item["path"].startswith("candidate/")
    ]
    manifest = {
        "schema_version": HARNESS_CONTRACT_VERSION,
        "kind": "skill_evaluation_run",
        "invocation_nonce": require_text(args.nonce, "invocation nonce"),
        "candidate_id": f"sha256:{digest(canonical(harness_projection))}",
        "suite_id": f"sha256:{digest(canonical(harness_suite))}",
        "profile": policy["profile"],
        "trials_per_arm": policy["trials_per_arm"],
        "executors": config["executors"],
        "comparator": config["comparator"],
        "harness_executable_sha256": harness_sha,
        "tool_policy_id": config["tool_policy_id"],
        "grader_set_id": harness_suite["grader_set_id"],
        "retention_policy_id": config["retention_policy_id"],
        "limits": config["limits"],
        "file_inventory": file_inventory,
    }
    manifest["run_id"] = f"sha256:{digest(canonical({
        key: manifest[key]
        for key in (
            "schema_version", "kind", "candidate_id", "suite_id", "profile",
            "trials_per_arm", "executors", "comparator",
            "harness_executable_sha256", "tool_policy_id", "grader_set_id",
            "retention_policy_id", "limits", "file_inventory",
        )
    }))}"
    atomic_write(run_dir / "manifest.json", manifest)
    return {
        "run_dir": str(run_dir),
        "run_id": manifest["run_id"],
        "candidate_id": candidate,
        "candidate_inventory": files,
        "input_manifest_sha256": input_manifest_sha256,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "observation_plan_id": observation_plan_identity(policy, policy_id),
        "required_executors": required_names,
        "advisory_executors": advisory_names,
    }


def load_compiled_run(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    manifest = load_json(run_dir / "manifest.json")
    source_suite = load_json(run_dir / "source-suite.json")
    source_policy = load_json(run_dir / "source-policy.json")
    compilation = load_json(run_dir / "compilation.json")
    actual_inventory = canonical_file_inventory(run_dir, {"manifest.json"})
    if manifest.get("file_inventory") != actual_inventory:
        raise EvaluationError("compiled run file inventory was modified")
    expected_run_id = f"sha256:{digest(canonical({
        key: manifest.get(key)
        for key in (
            "schema_version", "kind", "candidate_id", "suite_id", "profile",
            "trials_per_arm", "executors", "comparator",
            "harness_executable_sha256", "tool_policy_id", "grader_set_id",
            "retention_policy_id", "limits", "file_inventory",
        )
    }))}"
    if manifest.get("run_id") != expected_run_id:
        raise EvaluationError("compiled run_id is not canonical")
    return manifest, source_suite, source_policy, compilation


def v2_run_execute(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir).resolve()
    result_dir = Path(args.result_dir).resolve()
    scratch_dir = Path(args.scratch).resolve()
    harness = require_trusted_harness(Path(args.harness))
    manifest, _, _, compilation = load_compiled_run(run_dir)
    dreaming_input = load_json(run_dir / "dreaming-input.json")
    input_manifest_sha256 = dreaming_input.get("input_manifest_sha256")
    requested_routing_path = (
        Path(args.routing).resolve() if args.routing else None
    )
    if input_manifest_sha256 is not None:
        resolved = resolve_ready_input(
            Path(require_text(dreaming_input.get("skill_path"), "dreaming input skill_path"))
        )
        if (
            resolved is None
            or resolved["input_manifest_sha256"] != input_manifest_sha256
        ):
            raise EvaluationError("compiled run external input manifest is no longer ready")
    if sha256_file(harness) != manifest.get("harness_executable_sha256"):
        raise EvaluationError("selected harness executable differs from the compiled run")
    routing_path = requested_routing_path
    if input_manifest_sha256 is not None:
        validate_materialized_registry_run(resolved, run_dir, manifest)
        if args.routing:
            requested_routing = validate_routing(
                requested_routing_path,
                compilation["executors"],
                compilation["comparator"],
            )
            if requested_routing != resolved["routing"]:
                raise EvaluationError(
                    "execution routing differs from the ready input manifest"
                )
        routing_path = registry_object_path(
            resolved["manifest"]["routing_sha256"]
        )
    else:
        if not args.routing:
            raise EvaluationError(
                "root-local development execution requires a routing path"
            )
        validate_routing(
            requested_routing_path,
            compilation["executors"],
            compilation["comparator"],
        )
    if not result_dir.is_dir() or result_dir.is_symlink() or any(result_dir.iterdir()):
        raise EvaluationError("result directory must exist, be real, and be empty")
    if not scratch_dir.is_dir() or scratch_dir.is_symlink() or any(scratch_dir.iterdir()):
        raise EvaluationError("scratch directory must exist, be real, and be empty")
    subprocess.run(
        [
            str(harness),
            "run",
            "--input",
            str(run_dir),
            "--output",
            str(result_dir),
            "--routing",
            str(routing_path),
            "--scratch",
            str(scratch_dir),
        ],
        check=True,
    )
    return {
        "result_dir": str(result_dir),
        "run_id": manifest["run_id"],
        "input_manifest_sha256": input_manifest_sha256,
    }


def result_bundle_identity(result_dir: Path, manifest: dict[str, Any]) -> tuple[str, str]:
    inventory_value = manifest.get("file_inventory")
    if inventory_value != canonical_file_inventory(result_dir, {"manifest.json"}):
        raise EvaluationError("result file inventory differs from the sealed manifest")
    result_id = require_sha256(manifest.get("result_id"), "result.result_id")
    bundle_sha = f"sha256:{digest(canonical({'manifest': manifest, 'files': inventory_value}))}"
    return bundle_sha, result_id


def verify_native_raw(path: Path, field: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise EvaluationError(f"{field} is missing")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise EvaluationError(f"{field} must contain native events")
    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"{field}[{index}] is not valid JSON") from exc
        if not isinstance(event, dict) or not event:
            raise EvaluationError(f"{field}[{index}] must be a non-empty native event object")


def verify_result_independently(
    skill_dir: Path,
    run_dir: Path,
    result_dir: Path,
    routing_path: Path,
    harness: Path,
    nonce: str,
    scratch: Path,
    suite_path: str | None,
    policy_path: str | None,
    resolved_input: dict[str, Any] | None = None,
    allow_advisory_drift: bool = False,
) -> dict[str, Any]:
    if resolved_input is None:
        candidate, files, suite, suite_id, policy, policy_id = load_v2_inputs(
            skill_dir, suite_path, policy_path
        )
        input_manifest_sha256 = None
    else:
        if suite_path or policy_path:
            raise EvaluationError(
                "authoritative verification cannot use suite or policy path overrides"
            )
        candidate = resolved_input["candidate_id"]
        files = resolved_input["candidate_inventory"]
        suite = resolved_input["suite"]
        suite_id = resolved_input["suite_id"]
        policy = resolved_input["policy"]
        policy_id = resolved_input["policy_id"]
        input_manifest_sha256 = resolved_input["input_manifest_sha256"]
    required_names, advisory_names = desired_executor_roles()
    if required_names != [item["name"] for item in policy["required_executors"]]:
        raise EvaluationError("current policy differs from DREAMING_EVALUATION_EXECUTORS")
    if (
        not allow_advisory_drift
        and advisory_names != [item["name"] for item in policy["advisory_executors"]]
    ):
        raise EvaluationError(
            "current policy differs from DREAMING_ADVISORY_EVALUATION_EXECUTORS"
        )
    run_manifest, source_suite, source_policy, compilation = load_compiled_run(run_dir)
    if resolved_input is not None:
        validate_materialized_registry_run(
            resolved_input, run_dir, run_manifest
        )
    dreaming_input = load_json(run_dir / "dreaming-input.json")
    require_exact_keys(
        dreaming_input,
        "dreaming input",
        {
            "schema_version",
            "skill_path",
            "candidate_id",
            "candidate_inventory",
            "input_manifest_sha256",
            "suite_id",
            "policy_id",
            "observation_plan_id",
        },
    )
    if source_suite != suite:
        raise EvaluationError("compiled run binds stale suite input")
    if policy_identity(source_policy) != policy_id:
        raise EvaluationError("compiled run binds stale required policy input")
    if source_policy != policy and (
        resolved_input is not None or not allow_advisory_drift
    ):
        raise EvaluationError("compiled run binds stale advisory policy input")
    source_observation_plan_id = observation_plan_identity(source_policy, policy_id)
    expected_policy_executors = [
        {**item, "requirement": requirement}
        for requirement, values in (
            ("required", source_policy["required_executors"]),
            ("advisory", source_policy["advisory_executors"]),
        )
        for item in values
    ]
    compiled_policy_executors = [
        {
            key: item[key]
            for key in (
                "name",
                "model",
                "adapter_id",
                "adapter_version",
                "adapter_executable_sha256",
                "cli_executable_sha256",
                "requirement",
            )
        }
        for item in compilation.get("executors", [])
    ]
    if compiled_policy_executors != expected_policy_executors:
        raise EvaluationError("compiled executor identities differ from the current policy")
    if compilation.get("comparator") != policy["comparator"]:
        raise EvaluationError("compiled comparator identity differs from the current policy")
    if (
        run_manifest.get("executors") != compilation.get("executors")
        or run_manifest.get("comparator") != compilation.get("comparator")
        or run_manifest.get("tool_policy_id") != compilation.get("tool_policy_id")
        or run_manifest.get("retention_policy_id") != compilation.get("retention_policy_id")
        or run_manifest.get("limits") != compilation.get("limits")
    ):
        raise EvaluationError("compiled run manifest differs from its reviewed compilation")
    if any(
        item.get("tool_policy_id") != compilation.get("tool_policy_id")
        for item in compilation.get("executors", [])
    ):
        raise EvaluationError("compiled executor tool policy differs from the reviewed compilation")
    if dreaming_input != {
        "schema_version": 1,
        "skill_path": str(skill_dir),
        "candidate_id": candidate,
        "candidate_inventory": files,
        "input_manifest_sha256": input_manifest_sha256,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "observation_plan_id": source_observation_plan_id,
    }:
        raise EvaluationError("compiled run binds a stale candidate")
    run_projection = [
        {
            "path": item["path"].removeprefix("candidate/"),
            "sha256": item["sha256"],
            "size": item["size"],
        }
        for item in run_manifest["file_inventory"]
        if item["path"].startswith("candidate/")
    ]
    if run_manifest.get("candidate_id") != f"sha256:{digest(canonical(run_projection))}":
        raise EvaluationError("compiled harness candidate identity is stale")
    harness = require_trusted_harness(harness)
    harness_sha = sha256_file(harness)
    if (
        harness_sha != run_manifest.get("harness_executable_sha256")
        or harness_sha != compilation.get("harness_executable_sha256")
    ):
        raise EvaluationError("unknown or changed harness producer")
    routing = validate_routing(routing_path, compilation["executors"], compilation["comparator"])
    if resolved_input is not None and (
        f"sha256:{digest(canonical(compilation))}"
        != resolved_input["manifest"]["compilation_sha256"]
        or f"sha256:{digest(canonical(routing))}"
        != resolved_input["manifest"]["routing_sha256"]
    ):
        raise EvaluationError(
            "compiled run configuration differs from the ready input manifest"
        )
    if not scratch.is_dir() or scratch.is_symlink() or any(scratch.iterdir()):
        raise EvaluationError("verification scratch directory must exist, be real, and be empty")
    try:
        subprocess.run(
            [
                str(harness),
                "verify",
                "--result",
                str(result_dir),
                "--scratch",
                str(scratch),
                "--nonce",
                nonce,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = " ".join((exc.stderr or "").splitlines()).strip()
        suffix = f": {detail}" if detail else ""
        raise EvaluationError(
            f"independent result verification failed with exit status {exc.returncode}{suffix}"
        ) from exc
    result_manifest = load_json(result_dir / "manifest.json")
    require_exact_keys(result_manifest, "result manifest", RESULT_MANIFEST_KEYS)
    if (
        result_manifest.get("schema_version") != HARNESS_CONTRACT_VERSION
        or result_manifest.get("kind") != "skill_evaluation_result"
        or result_manifest.get("invocation_nonce") != nonce
        or result_manifest.get("input_run_id") != run_manifest["run_id"]
        or result_manifest.get("candidate_id") != run_manifest["candidate_id"]
        or result_manifest.get("profile") != source_policy["profile"]
        or result_manifest.get("harness_executable_sha256") != harness_sha
    ):
        raise EvaluationError("result manifest differs from the current sealed run")
    expected_result_id = f"sha256:{digest(canonical({
        key: value for key, value in result_manifest.items() if key != 'result_id'
    }))}"
    if result_manifest.get("result_id") != expected_result_id:
        raise EvaluationError("result identity is forged or malformed")
    bundle_sha, result_id = result_bundle_identity(result_dir, result_manifest)
    expected_executor_identities = {
        item["name"]: {
            key: value for key, value in item.items() if key not in {"name", "requirement"}
        }
        for item in compilation["executors"]
    }
    if result_manifest.get("executor_identities") != expected_executor_identities:
        raise EvaluationError("result executor identities differ from the required exact set")
    if result_manifest.get("comparator_identity") != compilation["comparator"]:
        raise EvaluationError("result comparator identity differs from the authorized comparator")
    producer_audit = result_manifest.get("producer_audit")
    if not isinstance(producer_audit, dict):
        raise EvaluationError("result producer audit is missing")
    require_exact_keys(
        producer_audit,
        "result.producer_audit",
        {"routing_config_sha256", "executor_argv_sha256", "comparator_argv_sha256", "environment_sha256"},
    )
    if producer_audit["routing_config_sha256"] != f"sha256:{digest(routing_path.read_bytes())}":
        raise EvaluationError("result producer routing digest differs from the authorized route")
    expected_executor_argv = {
        item["name"]: f"sha256:{digest(canonical(item['argv']))}"
        for item in routing["executors"]
    }
    if producer_audit["executor_argv_sha256"] != expected_executor_argv:
        raise EvaluationError("result executor argv identities differ from authorized routing")
    if producer_audit["comparator_argv_sha256"] != f"sha256:{digest(canonical(routing['comparator']['argv']))}":
        raise EvaluationError("result comparator argv identity differs from authorized routing")
    records: list[dict[str, Any]] = []
    for trial_id in result_manifest.get("trials", []):
        require_sha256(trial_id, "result.trials[]")
        root = result_dir / "trials" / trial_id.removeprefix("sha256:")
        record = load_json(root / "result.json")
        if record.get("trial_id") != trial_id:
            raise EvaluationError(f"trial {trial_id} has a forged result identity")
        executor = next(
            (item for item in compilation["executors"] if item["name"] == record.get("executor")),
            None,
        )
        if executor is None or record.get("model") != executor["model"]:
            raise EvaluationError(f"trial {trial_id} uses an unauthorized executor or model")
        trial = load_json(root / "trial.json")
        if (
            trial.get("trial_id") != trial_id
            or trial.get("candidate_id") != run_manifest["candidate_id"]
            or trial.get("executor") != executor
        ):
            raise EvaluationError(f"trial {trial_id} differs from its sealed input identity")
        if record.get("status") != "inconclusive":
            prepared = load_json(root / "prepared.json")
            prepared_digest = prepared.get("prepared_digest")
            if prepared_digest != f"sha256:{digest(canonical({
                key: value for key, value in prepared.items() if key != 'prepared_digest'
            }))}":
                raise EvaluationError(f"trial {trial_id} prepared execution digest is invalid")
            if record.get("prepared_digest") != prepared_digest:
                raise EvaluationError(f"trial {trial_id} did not bind the prepared execution")
            effective = {
                key: value
                for key, value in executor.items()
                if key not in {"name", "requirement"}
            }
            if record.get("effective_execution") != effective or prepared.get("execution") != effective:
                raise EvaluationError(f"trial {trial_id} prepared or effective execution drifted")
            raw = root / "raw.jsonl"
            trace = root / "trace.json"
            verify_native_raw(raw, f"trial {trial_id} raw log")
            if (
                record.get("raw_sha256") != f"sha256:{digest(raw.read_bytes())}"
                or record.get("trace_sha256") != f"sha256:{digest(trace.read_bytes())}"
            ):
                raise EvaluationError(f"trial {trial_id} raw or trace link is stale")
            artifact_inventory = canonical_file_inventory(root / "artifacts")
            if record.get("artifact_inventory") != artifact_inventory:
                raise EvaluationError(f"trial {trial_id} artifact inventory is stale")
            if not (root / "grader-results.json").is_file():
                raise EvaluationError(f"trial {trial_id} is missing deterministic grader evidence")
        records.append(record)
    if set(result_manifest.get("trials", [])) != {item.get("trial_id") for item in records}:
        raise EvaluationError("result trial matrix is partial or duplicated")
    comparisons = []
    for pair_id in result_manifest.get("pairs", []):
        require_sha256(pair_id, "result.pairs[]")
        comparison = load_json(
            result_dir / "comparisons" / f"{pair_id.removeprefix('sha256:')}.json"
        )
        if comparison.get("pair_id") != pair_id:
            raise EvaluationError(f"comparison {pair_id} has a forged identity")
        if comparison.get("comparator") != compilation["comparator"]:
            raise EvaluationError(f"comparison {pair_id} uses an unauthorized comparator")
        comparisons.append(comparison)
    if set(result_manifest.get("pairs", [])) != {item.get("pair_id") for item in comparisons}:
        raise EvaluationError("result comparison matrix is partial or duplicated")
    return {
        "candidate_id": candidate,
        "candidate_inventory": files,
        "input_manifest_sha256": input_manifest_sha256,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "observation_plan_id": source_observation_plan_id,
        "policy": source_policy,
        "run_id": run_manifest["run_id"],
        "result_bundle_sha256": bundle_sha,
        "result_bundle_id": result_id,
        "state": result_manifest.get("state"),
        "collection_state": result_manifest.get("collection_state"),
        "executor_states": result_manifest.get("executor_states"),
        "records": records,
        "comparisons": comparisons,
    }


def reverify_certification_record(
    certification: dict[str, Any], skill_dir: Path
) -> dict[str, Any]:
    resolved = resolve_ready_input(skill_dir)
    if resolved is None:
        raise EvaluationError("ready external evaluation input is missing")
    if (
        certification.get("input_manifest_sha256")
        != resolved["input_manifest_sha256"]
    ):
        raise EvaluationError("certification input manifest is no longer ready")
    scratch_parent = v2_evaluation_dir() / "verification-scratch"
    scratch_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="authority-", dir=scratch_parent) as temporary:
        scratch = Path(temporary)
        return verify_result_independently(
            skill_dir,
            Path(require_text(certification.get("run_dir"), "certification.run_dir")).resolve(),
            Path(require_text(certification.get("result_dir"), "certification.result_dir")).resolve(),
            Path(require_text(certification.get("routing_path"), "certification.routing_path")).resolve(),
            require_trusted_harness(
                Path(require_text(certification.get("harness_path"), "certification.harness_path"))
            ),
            require_text(certification.get("invocation_nonce"), "certification.invocation_nonce"),
            scratch,
            None,
            None,
            resolved,
            True,
        )


def candidate_comparison_wins(comparisons: list[dict[str, Any]]) -> int:
    wins = 0
    for comparison in comparisons:
        winner = comparison.get("winner")
        assignment = comparison.get("assignment")
        if (
            comparison.get("status") == "complete"
            and winner in {"A", "B"}
            and isinstance(assignment, dict)
            and assignment.get(winner) == "candidate"
        ):
            wins += 1
    return wins


def executor_policy_status(
    executor_name: str,
    policy: dict[str, Any],
    state: str,
    records: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
) -> str:
    if policy["profile"] != "gate":
        return "inconclusive"
    selected = [item for item in records if item.get("executor") == executor_name]
    if state != "complete" or not selected:
        return "inconclusive"
    if any(item.get("status") == "inconclusive" or item.get("infrastructure_error") or item.get("cleanup_failed") for item in selected):
        return "inconclusive"
    by_case: dict[str, list[dict[str, Any]]] = {}
    for item in selected:
        by_case.setdefault(require_text(item.get("case_id"), "trial.case_id"), []).append(item)
    for case_records in by_case.values():
        case_class = case_records[0].get("case_class")
        candidate_records = [item for item in case_records if item.get("treatment") == "candidate"]
        control_records = [item for item in case_records if item.get("treatment") == "control"]
        if len(candidate_records) != 3 or (case_class in {"intended", "related"} and len(control_records) != 3):
            return "inconclusive"
        if case_class == "intended":
            if any(item.get("status") == "invalid" for item in case_records):
                return "inconclusive"
            candidate_passes = sum(item.get("status") == "pass" for item in candidate_records)
            control_passes = sum(item.get("status") == "pass" for item in control_records)
            if candidate_passes < 2:
                return "regression"
            if policy["policy_kind"] == "capability_uplift":
                if candidate_passes - control_passes < 1 or control_passes == 3:
                    return "regression"
            else:
                case_comparisons = [
                    item
                    for item in comparisons
                    if item.get("executor") == executor_name
                    and item.get("case_id") == case_records[0].get("case_id")
                ]
                if len(case_comparisons) != 3 or any(item.get("status") != "complete" for item in case_comparisons):
                    return "inconclusive"
                if candidate_comparison_wins(case_comparisons) < 2:
                    return "regression"
        elif case_class == "related":
            if any(item.get("status") == "invalid" for item in case_records):
                return "inconclusive"
            if (
                any(item.get("status") == "regression" for item in candidate_records)
                or any(item.get("deterministic_pass") is not True for item in candidate_records)
                or sum(item.get("status") == "pass" for item in candidate_records)
                < sum(item.get("status") == "pass" for item in control_records)
            ):
                return "regression"
        elif case_class == "activation_positive":
            if sum(item.get("skill_load_proved") is True for item in candidate_records) < 2:
                return "regression"
        elif case_class == "activation_negative":
            if any(item.get("skill_load_proved") is not True or item.get("status") == "regression" for item in candidate_records):
                return "regression"
        else:
            return "inconclusive"
    return "pass"


def make_executor_certificate(
    status: str,
    candidate: str,
    input_manifest_sha256: str,
    suite_id: str,
    policy_id: str,
    observation_plan_id: str,
    profile: str,
    requirement: str,
    executor: dict[str, Any],
    result_bundle_sha256: str,
    result_bundle_id: str,
    run_id: str,
) -> dict[str, Any]:
    certificate = {
        "schema_version": 2,
        "kind": "executor_certificate",
        "status": status,
        "candidate_id": candidate,
        "input_manifest_sha256": input_manifest_sha256,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "observation_plan_id": observation_plan_id if requirement == "advisory" else None,
        "profile": profile,
        "requirement": requirement,
        "executor": executor,
        "result_bundle_sha256": result_bundle_sha256,
        "result_bundle_id": result_bundle_id,
        "run_id": run_id,
    }
    certificate["certificate_id"] = identity_with("certificate_id", certificate)
    return certificate


def write_certification_aggregate(
    skill_dir: Path,
    candidate: str,
    files: list[dict[str, Any]],
    input_manifest_sha256: str,
    suite: dict[str, Any],
    suite_id: str,
    policy_id: str,
    policy: dict[str, Any],
    certificates: list[dict[str, Any]],
) -> tuple[dict[str, Any], Path, str]:
    required_certificates = [
        item for item in certificates if item["requirement"] == "required"
    ]
    statuses = [item["status"] for item in required_certificates]
    aggregate_status = (
        "regression"
        if "regression" in statuses
        else "inconclusive"
        if any(item != "pass" for item in statuses)
        else "pass"
    )
    aggregate = {
        "schema_version": 2,
        "kind": "aggregate_receipt",
        "status": aggregate_status,
        "skill_path": str(skill_dir),
        "candidate_id": candidate,
        "candidate_inventory": files,
        "input_manifest_sha256": input_manifest_sha256,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "observation_plan_id": observation_plan_identity(policy, policy_id),
        "profile": policy["profile"],
        "required_executors": policy["required_executors"],
        "advisory_executors": policy["advisory_executors"],
        "certificates": certificates,
        "required_certificate_set_id": required_certificate_set_identity(
            candidate,
            input_manifest_sha256,
            suite_id,
            policy_id,
            policy["profile"],
            required_certificates,
        ),
    }
    aggregate["aggregate_id"] = identity_with("aggregate_id", aggregate)
    validated = validate_aggregate(
        aggregate,
        skill_dir,
        candidate,
        input_manifest_sha256,
        suite,
        suite_id,
        policy,
        policy_id,
    )
    path, receipt_sha = write_v2_receipt(validated)
    return validated, path, receipt_sha


def write_portfolio_receipt(
    skill_dir: Path,
    verified: dict[str, Any],
    suite: dict[str, Any],
    aggregate: dict[str, Any],
    aggregate_sha: str,
) -> tuple[dict[str, Any], str]:
    cases: list[dict[str, Any]] = []
    intended = {
        item["id"]
        for item in suite["cases"]
        if item.get("class") == "intended"
    }
    executor_names = [
        item["name"]
        for item in (
            verified["policy"]["required_executors"]
            + verified["policy"]["advisory_executors"]
        )
    ]
    for executor in executor_names:
        for case_id in sorted(intended):
            rows = [
                item
                for item in verified["records"]
                if item.get("executor") == executor
                and item.get("case_id") == case_id
            ]
            candidate = [
                item for item in rows if item.get("treatment") == "candidate"
            ]
            control = [item for item in rows if item.get("treatment") == "control"]
            invalid = any(
                item.get("status") in {"invalid", "inconclusive"}
                or item.get("infrastructure_error")
                or item.get("cleanup_failed")
                for item in rows
            )
            comparable = len(candidate) == 3 and len(control) == 3 and not invalid
            cases.append(
                {
                    "executor": executor,
                    "case_id": case_id,
                    "evaluation_class": verified["policy"]["policy_kind"],
                    "candidate_valid_trials": sum(
                        item.get("status") in {"pass", "regression"}
                        for item in candidate
                    ),
                    "candidate_successful_trials": sum(
                        item.get("status") == "pass" for item in candidate
                    ),
                    "control_valid_trials": sum(
                        item.get("status") in {"pass", "regression"}
                        for item in control
                    ),
                    "control_successful_trials": sum(
                        item.get("status") == "pass" for item in control
                    ),
                    "comparable": comparable,
                    "exclusion_reason": None if comparable else "incomplete_pair",
                }
            )
    receipt = {
        "schema_version": 1,
        "kind": "dashboard_portfolio_receipt",
        "skill_key": latest_key(str(skill_dir)),
        "candidate_id": verified["candidate_id"],
        "input_manifest_sha256": verified["input_manifest_sha256"],
        "suite_id": verified["suite_id"],
        "policy_id": verified["policy_id"],
        "status": aggregate["status"],
        "aggregate_receipt_sha256": aggregate_sha,
        "aggregate_id": aggregate["aggregate_id"],
        "cases": cases,
    }
    receipt["portfolio_id"] = identity_with("portfolio_id", receipt)
    receipt_sha = digest(canonical(receipt))
    path = v2_portfolio_receipt_path(receipt_sha)
    if path.exists() and canonical(load_json(path)) != canonical(receipt):
        raise EvaluationError("dashboard portfolio receipt collision")
    if not path.exists():
        atomic_write(path, receipt)
    pointer = {
        "schema_version": 1,
        "aggregate_receipt_sha256": aggregate_sha,
        "portfolio_receipt_sha256": receipt_sha,
        "portfolio_id": receipt["portfolio_id"],
    }
    pointer_path = v2_portfolio_pointer_path(aggregate_sha)
    if pointer_path.exists() and load_json(pointer_path) != pointer:
        raise EvaluationError("dashboard portfolio pointer collision")
    if not pointer_path.exists():
        atomic_write(pointer_path, pointer)
    return receipt, receipt_sha


def load_portfolio_for_aggregate(aggregate_sha: str) -> tuple[dict[str, Any], str]:
    aggregate, verified_aggregate_sha = load_v2_receipt(
        v2_receipt_path(aggregate_sha)
    )
    if verified_aggregate_sha != aggregate_sha:
        raise EvaluationError("portfolio aggregate receipt digest does not match")
    pointer = load_json(v2_portfolio_pointer_path(aggregate_sha))
    if pointer.get("aggregate_receipt_sha256") != aggregate_sha:
        raise EvaluationError("portfolio pointer aggregate does not match")
    receipt_sha = require_text(
        pointer.get("portfolio_receipt_sha256"),
        "portfolio pointer receipt digest",
    )
    receipt = load_json(v2_portfolio_receipt_path(receipt_sha))
    if digest(canonical(receipt)) != receipt_sha:
        raise EvaluationError("portfolio receipt digest does not match")
    if (
        receipt.get("aggregate_receipt_sha256") != aggregate_sha
        or receipt.get("input_manifest_sha256")
        != aggregate.get("input_manifest_sha256")
        or receipt.get("portfolio_id") != identity_with("portfolio_id", receipt)
        or pointer.get("portfolio_id") != receipt.get("portfolio_id")
    ):
        raise EvaluationError("portfolio receipt identity does not match")
    return receipt, receipt_sha


def write_authority_transition(
    skill_dir: Path,
    candidate_id: str,
    input_manifest_sha256: str,
    status: str,
    authority_sha: str | None,
    aggregate_sha: str | None,
    portfolio_sha: str | None,
) -> dict[str, Any]:
    if status not in {"pass", "regression", "inconclusive", "revoked"}:
        raise EvaluationError("authority transition status is invalid")
    if status == "pass" and not all((authority_sha, aggregate_sha, portfolio_sha)):
        raise EvaluationError("pass transition requires complete authority evidence")
    if status in {"regression", "inconclusive"} and (
        authority_sha is not None or not aggregate_sha or not portfolio_sha
    ):
        raise EvaluationError("non-passing transition evidence is invalid")
    if status == "revoked" and authority_sha is not None:
        raise EvaluationError("revoked transition cannot retain authority")
    transition = {
        "schema_version": 1,
        "kind": "dashboard_authority_transition",
        "effective_at": now_iso(),
        "skill_key": latest_key(str(skill_dir)),
        "candidate_id": candidate_id,
        "input_manifest_sha256": input_manifest_sha256,
        "status": status,
        "authority_sha256": authority_sha,
        "aggregate_receipt_sha256": aggregate_sha,
        "portfolio_receipt_sha256": portfolio_sha,
    }
    transition["transition_id"] = identity_with("transition_id", transition)
    path = v2_transition_dir(skill_dir) / f"{transition['transition_id'].removeprefix('sha256:')}.json"
    if path.exists() and canonical(load_json(path)) != canonical(transition):
        raise EvaluationError("authority transition collision")
    if not path.exists():
        atomic_write(path, transition)
    return transition


def v2_result_certify(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    if args.suite or args.policy:
        raise EvaluationError(
            "certification cannot accept suite or policy path overrides"
        )
    resolved = resolve_ready_input(skill_dir)
    if resolved is None:
        raise EvaluationError("ready external evaluation input is missing")
    if args.routing:
        requested_routing = validate_routing(
            Path(args.routing).resolve(),
            resolved["config"]["executors"],
            resolved["config"]["comparator"],
        )
        if requested_routing != resolved["routing"]:
            raise EvaluationError(
                "certification routing differs from the ready input manifest"
            )
    registry_routing_path = registry_object_path(
        resolved["manifest"]["routing_sha256"]
    )
    verified = verify_result_independently(
        skill_dir,
        Path(args.run_dir).resolve(),
        Path(args.result_dir).resolve(),
        registry_routing_path,
        Path(args.harness).resolve(),
        require_text(args.nonce, "invocation nonce"),
        Path(args.scratch).resolve(),
        args.suite,
        args.policy,
        resolved,
    )
    policy = verified["policy"]
    certificates = []
    for requirement, executors in (
        ("required", policy["required_executors"]),
        ("advisory", policy["advisory_executors"]),
    ):
        for executor in executors:
            executor_state = verified["executor_states"].get(executor["name"], {}).get(
                "state", "incomplete"
            )
            certificates.append(
                make_executor_certificate(
                    executor_policy_status(
                        executor["name"],
                        policy,
                        executor_state,
                        verified["records"],
                        verified["comparisons"],
                    ),
                    verified["candidate_id"],
                    verified["input_manifest_sha256"],
                    verified["suite_id"],
                    verified["policy_id"],
                    verified["observation_plan_id"],
                    policy["profile"],
                    requirement,
                    executor,
                    verified["result_bundle_sha256"],
                    verified["result_bundle_id"],
                    verified["run_id"],
                )
            )
    suite = resolved["suite"]
    aggregate, path, receipt_sha = write_certification_aggregate(
        skill_dir,
        verified["candidate_id"],
        verified["candidate_inventory"],
        verified["input_manifest_sha256"],
        suite,
        verified["suite_id"],
        verified["policy_id"],
        policy,
        certificates,
    )
    _, portfolio_sha = write_portfolio_receipt(
        skill_dir,
        verified,
        suite,
        aggregate,
        receipt_sha,
    )
    if policy["profile"] == "gate" and aggregate["status"] in {
        "regression",
        "inconclusive",
    }:
        write_authority_transition(
            skill_dir,
            verified["candidate_id"],
            verified["input_manifest_sha256"],
            aggregate["status"],
            None,
            receipt_sha,
            portfolio_sha,
        )
    if policy["profile"] == "gate" and aggregate["status"] == "pass":
        certification = {
            "schema_version": 1,
            "kind": "dreaming_certification",
            "skill_path": str(skill_dir),
            "candidate_id": verified["candidate_id"],
            "input_manifest_sha256": verified["input_manifest_sha256"],
            "suite_id": verified["suite_id"],
            "policy_id": verified["policy_id"],
            "required_certificate_set_id": aggregate["required_certificate_set_id"],
            "profile": policy["profile"],
            "aggregate_receipt_sha256": receipt_sha,
            "aggregate_id": aggregate["aggregate_id"],
            "result_bundle_sha256": verified["result_bundle_sha256"],
            "result_bundle_id": verified["result_bundle_id"],
            "run_id": verified["run_id"],
            "run_dir": str(Path(args.run_dir).resolve()),
            "result_dir": str(Path(args.result_dir).resolve()),
            "routing_path": str(registry_routing_path),
            "harness_path": str(Path(args.harness).resolve()),
            "invocation_nonce": require_text(args.nonce, "invocation nonce"),
        }
        certification["certification_id"] = identity_with("certification_id", certification)
        atomic_write(v2_certification_path(receipt_sha), certification)
    return {
        "status": aggregate["status"],
        "aggregate": str(path),
        "aggregate_receipt_sha256": receipt_sha,
        "aggregate_id": aggregate["aggregate_id"],
        "input_manifest_sha256": verified["input_manifest_sha256"],
        "certificates": certificates,
        "authoritative": policy["profile"] == "gate" and aggregate["status"] == "pass",
    }


def parse_unavailable(value: str) -> tuple[str, str]:
    name, separator, reason = value.partition("=")
    if not separator or name not in EXECUTOR_NAMES:
        raise EvaluationError("--unavailable must be executor=reason")
    return name, require_text(reason, f"unavailable reason for {name}")


def v2_unavailable_aggregate(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    if args.suite or args.policy:
        raise EvaluationError(
            "unavailable evidence cannot accept suite or policy path overrides"
        )
    resolved = resolve_ready_input(skill_dir)
    if resolved is None:
        raise EvaluationError("ready external evaluation input is missing")
    candidate = resolved["candidate_id"]
    files = resolved["candidate_inventory"]
    suite = resolved["suite"]
    suite_id = resolved["suite_id"]
    policy = resolved["policy"]
    policy_id = resolved["policy_id"]
    input_manifest_sha256 = resolved["input_manifest_sha256"]
    required_names, advisory_names = desired_executor_roles()
    if required_names != [item["name"] for item in policy["required_executors"]]:
        raise EvaluationError("policy required executors differ from DREAMING_EVALUATION_EXECUTORS")
    if advisory_names != [item["name"] for item in policy["advisory_executors"]]:
        raise EvaluationError(
            "policy advisory executors differ from DREAMING_ADVISORY_EVALUATION_EXECUTORS"
        )
    reasons = dict(parse_unavailable(item) for item in args.unavailable)
    selected = required_names + advisory_names
    if set(reasons) != set(selected):
        raise EvaluationError("unavailability evidence must name every selected executor exactly once")
    certificates = []
    observation_plan_id = observation_plan_identity(policy, policy_id)
    for requirement, executors in (
        ("required", policy["required_executors"]),
        ("advisory", policy["advisory_executors"]),
    ):
        for executor in executors:
            evidence = {
                "schema_version": 1,
                "kind": "skill_evaluation_unavailable",
                "candidate_id": candidate,
                "input_manifest_sha256": input_manifest_sha256,
                "suite_id": suite_id,
                "policy_id": policy_id,
                "observation_plan_id": (
                    observation_plan_id if requirement == "advisory" else None
                ),
                "profile": policy["profile"],
                "requirement": requirement,
                "executor": executor,
                "reason": reasons[executor["name"]],
            }
            evidence["result_id"] = identity_with("result_id", evidence)
            evidence_path, evidence_sha = write_v2_receipt(evidence)
            certificates.append(
                make_executor_certificate(
                    "unavailable",
                    candidate,
                    input_manifest_sha256,
                    suite_id,
                    policy_id,
                    observation_plan_id,
                    policy["profile"],
                    requirement,
                    executor,
                    f"sha256:{evidence_sha}",
                    evidence["result_id"],
                    f"sha256:{digest(canonical({'unavailable': str(evidence_path)}))}",
                )
            )
    aggregate, path, receipt_sha = write_certification_aggregate(
        skill_dir,
        candidate,
        files,
        input_manifest_sha256,
        suite,
        suite_id,
        policy_id,
        policy,
        certificates,
    )
    return {
        "status": aggregate["status"],
        "aggregate": str(path),
        "aggregate_receipt_sha256": receipt_sha,
        "input_manifest_sha256": input_manifest_sha256,
        "authoritative": False,
    }


def load_v2_receipt(path: Path) -> tuple[dict[str, Any], str]:
    resolved = path.resolve()
    if resolved.parent != (v2_evaluation_dir() / "receipts").resolve():
        raise EvaluationError("v2 receipt must come from the version-2 receipt store")
    receipt = load_json(resolved)
    receipt_sha = digest(canonical(receipt))
    if resolved.name != f"{receipt_sha}.json":
        raise EvaluationError("v2 receipt hash or content-addressed path does not match")
    return receipt, receipt_sha


def write_v2_receipt(receipt: dict[str, Any]) -> tuple[Path, str]:
    receipt_sha = digest(canonical(receipt))
    path = v2_receipt_path(receipt_sha)
    if path.exists() and canonical(load_json(path)) != canonical(receipt):
        raise EvaluationError("version-2 receipt collision")
    if not path.exists():
        atomic_write(path, receipt)
    return path, receipt_sha


def validate_authority(
    authority: Any, skill_dir: Path, candidate: str, input_manifest_sha256: str,
    suite: dict[str, Any], suite_id: str,
    policy: dict[str, Any], policy_id: str
) -> dict[str, Any]:
    if not isinstance(authority, dict):
        raise EvaluationError("authority document must be an object")
    require_exact_keys(
        authority,
        "authority",
        {
            "schema_version",
            "kind",
            "skill_path",
            "candidate_id",
            "input_manifest_sha256",
            "suite_id",
            "policy_id",
            "observation_plan_id",
            "required_certificate_set_id",
            "required_executors",
            "advisory_executors",
            "aggregate_receipt_sha256",
            "aggregate_id",
            "authority_id",
        },
    )
    if authority.get("schema_version") != AUTHORITY_SCHEMA_VERSION or authority.get("kind") != "cross_cli_authority":
        raise EvaluationError("authority document must be schema-v3 cross_cli_authority")
    if authority.get("skill_path") != str(skill_dir):
        raise EvaluationError("authority document belongs to another skill path")
    if (
        authority.get("candidate_id") != candidate
        or authority.get("input_manifest_sha256") != input_manifest_sha256
        or authority.get("suite_id") != suite_id
        or authority.get("policy_id") != policy_id
    ):
        raise EvaluationError("authority document input identity is stale")
    aggregate_sha = require_text(authority.get("aggregate_receipt_sha256"), "authority.aggregate_receipt_sha256")
    aggregate, verified_sha = load_v2_receipt(v2_receipt_path(aggregate_sha))
    if verified_sha != aggregate_sha:
        raise EvaluationError("authority aggregate receipt digest does not match")
    aggregate = validate_aggregate(
        aggregate,
        skill_dir,
        candidate,
        input_manifest_sha256,
        suite,
        suite_id,
        policy,
        policy_id,
        allow_advisory_drift=True,
    )
    if aggregate["status"] != "pass":
        raise EvaluationError("cross-CLI authority requires a passing aggregate receipt")
    if policy["profile"] != "gate":
        raise EvaluationError("cross-CLI authority requires a gate-profile policy")
    certification = load_json(v2_certification_path(aggregate_sha))
    require_exact_keys(
        certification,
        "certification record",
        {
            "schema_version",
            "kind",
            "skill_path",
            "candidate_id",
            "input_manifest_sha256",
            "suite_id",
            "policy_id",
            "required_certificate_set_id",
            "profile",
            "aggregate_receipt_sha256",
            "aggregate_id",
            "result_bundle_sha256",
            "result_bundle_id",
            "run_id",
            "run_dir",
            "result_dir",
            "routing_path",
            "harness_path",
            "invocation_nonce",
            "certification_id",
        },
    )
    if (
        certification.get("schema_version") != 1
        or certification.get("kind") != "dreaming_certification"
        or certification.get("skill_path") != str(skill_dir)
        or certification.get("candidate_id") != candidate
        or certification.get("input_manifest_sha256") != input_manifest_sha256
        or certification.get("suite_id") != suite_id
        or certification.get("policy_id") != policy_id
        or certification.get("required_certificate_set_id")
        != aggregate["required_certificate_set_id"]
        or certification.get("profile") != "gate"
        or certification.get("aggregate_receipt_sha256") != aggregate_sha
        or certification.get("aggregate_id") != aggregate["aggregate_id"]
        or certification.get("certification_id") != identity_with("certification_id", certification)
    ):
        raise EvaluationError("authority certification record is stale or malformed")
    certificates = aggregate["certificates"]
    for field in ("result_bundle_sha256", "result_bundle_id", "run_id"):
        values = {item[field] for item in certificates}
        if values != {certification[field]}:
            raise EvaluationError(f"authority certification record does not bind aggregate {field}")
    verified = reverify_certification_record(certification, skill_dir)
    for field in (
        "candidate_id",
        "input_manifest_sha256",
        "suite_id",
        "policy_id",
        "run_id",
        "result_bundle_sha256",
        "result_bundle_id",
    ):
        if certification[field] != verified[field]:
            raise EvaluationError(f"authority certification evidence differs at {field}")
    if authority.get("aggregate_id") != aggregate["aggregate_id"]:
        raise EvaluationError("authority document aggregate identity does not match")
    if (
        authority.get("observation_plan_id") != aggregate["observation_plan_id"]
        or authority.get("required_certificate_set_id")
        != aggregate["required_certificate_set_id"]
        or authority.get("required_executors")
        != [item["name"] for item in aggregate["required_executors"]]
        or authority.get("advisory_executors")
        != [item["name"] for item in aggregate["advisory_executors"]]
    ):
        raise EvaluationError("authority document partition identities do not match the aggregate")
    expected_id = identity_with("authority_id", authority)
    if authority.get("authority_id") != expected_id:
        raise EvaluationError("authority.authority_id does not match authority content")
    return authority


def v2_authority_write(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    if args.suite or args.policy:
        raise EvaluationError(
            "authority write cannot accept suite or policy path overrides"
        )
    resolved = resolve_ready_input(skill_dir)
    if resolved is None:
        raise EvaluationError("ready external evaluation input is missing")
    candidate = resolved["candidate_id"]
    suite = resolved["suite"]
    suite_id = resolved["suite_id"]
    policy = resolved["policy"]
    policy_id = resolved["policy_id"]
    input_manifest_sha256 = resolved["input_manifest_sha256"]
    aggregate, aggregate_sha = load_v2_receipt(Path(args.aggregate).resolve())
    aggregate = validate_aggregate(
        aggregate,
        skill_dir,
        candidate,
        input_manifest_sha256,
        suite,
        suite_id,
        policy,
        policy_id,
    )
    if aggregate["status"] != "pass" or policy["profile"] != "gate":
        raise EvaluationError("only a passing aggregate receipt can issue cross-CLI authority")
    certification = load_json(v2_certification_path(aggregate_sha))
    if (
        certification.get("kind") != "dreaming_certification"
        or certification.get("aggregate_receipt_sha256") != aggregate_sha
        or certification.get("certification_id") != identity_with("certification_id", certification)
    ):
        raise EvaluationError("aggregate lacks a valid Dreaming certification record")
    reverify_certification_record(certification, skill_dir)
    aggregate_path = v2_receipt_path(aggregate_sha)
    authority = {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "kind": "cross_cli_authority",
        "skill_path": str(skill_dir),
        "candidate_id": candidate,
        "input_manifest_sha256": input_manifest_sha256,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "observation_plan_id": aggregate["observation_plan_id"],
        "required_certificate_set_id": aggregate["required_certificate_set_id"],
        "required_executors": [item["name"] for item in aggregate["required_executors"]],
        "advisory_executors": [item["name"] for item in aggregate["advisory_executors"]],
        "aggregate_receipt_sha256": aggregate_sha,
        "aggregate_id": aggregate["aggregate_id"],
    }
    authority["authority_id"] = identity_with("authority_id", authority)
    authority_path = v2_authority_path(skill_dir, candidate)
    atomic_write(authority_path, authority)
    authority_sha = digest(canonical(authority))
    _, portfolio_sha = load_portfolio_for_aggregate(aggregate_sha)
    write_authority_transition(
        skill_dir,
        candidate,
        input_manifest_sha256,
        "pass",
        authority_sha,
        aggregate_sha,
        portfolio_sha,
    )
    atomic_write(
        v2_evaluation_dir() / "latest" / f"{latest_key(str(skill_dir))}.json",
        {
            "schema_version": 2,
            "skill_path": str(skill_dir),
            "candidate_id": candidate,
            "input_manifest_sha256": input_manifest_sha256,
            "authority_path": str(authority_path),
            "authority_sha256": authority_sha,
        },
    )
    return {
        "authority": str(authority_path),
        "authority_sha256": authority_sha,
        "aggregate_receipt": str(aggregate_path),
        "aggregate_receipt_sha256": aggregate_sha,
        "portfolio_receipt_sha256": portfolio_sha,
        "input_manifest_sha256": input_manifest_sha256,
    }


def v2_authority_validate(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = resolve_path(Path(args.skill_dir), "skill directory")
    if args.suite or args.policy:
        raise EvaluationError(
            "authority validation cannot accept suite or policy path overrides"
        )
    resolved = resolve_ready_input(skill_dir)
    if resolved is None:
        raise EvaluationError("ready external evaluation input is missing")
    candidate = resolved["candidate_id"]
    suite = resolved["suite"]
    suite_id = resolved["suite_id"]
    policy = resolved["policy"]
    policy_id = resolved["policy_id"]
    input_manifest_sha256 = resolved["input_manifest_sha256"]
    expected_path = resolve_path(
        v2_authority_path(skill_dir, candidate), "expected authority path"
    )
    path = (
        resolve_path(Path(args.authority), "authority path")
        if args.authority
        else expected_path
    )
    authority = load_json(path)
    if path != expected_path:
        raise EvaluationError("authority document path does not match skill and candidate identity")
    validate_authority(
        authority,
        skill_dir,
        candidate,
        input_manifest_sha256,
        suite,
        suite_id,
        policy,
        policy_id,
    )
    authority_sha = digest(canonical(authority))
    latest = load_json(v2_evaluation_dir() / "latest" / f"{latest_key(str(skill_dir))}.json")
    latest_authority_path = resolve_path(
        Path(require_text(latest.get("authority_path"), "latest authority path")),
        "latest authority path",
    )
    normalized_latest = {**latest, "authority_path": str(latest_authority_path)}
    if normalized_latest != {
        "schema_version": 2,
        "skill_path": str(skill_dir),
        "candidate_id": candidate,
        "input_manifest_sha256": input_manifest_sha256,
        "authority_path": str(path),
        "authority_sha256": authority_sha,
    }:
        raise EvaluationError("version-2 latest authority pointer is stale or malformed")
    return {
        "status": "pass",
        "candidate_id": candidate,
        "input_manifest_sha256": input_manifest_sha256,
        "authority_sha256": authority_sha,
    }


def changed_inventory_paths(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[str]:
    base = {item["path"]: item for item in before}
    current = {item["path"]: item for item in after}
    return sorted(path for path in base.keys() | current.keys() if base.get(path) != current.get(path))


def validate_v2_waiver(
    waiver: Any, skill_dir: Path, candidate: str, files: list[dict[str, Any]], suite: dict[str, Any],
    suite_id: str, policy: dict[str, Any], policy_id: str,
    input_manifest_sha256: str,
) -> dict[str, Any]:
    if not isinstance(waiver, dict) or waiver.get("schema_version") != 2 or waiver.get("kind") != "cross_cli_waiver":
        raise EvaluationError("waiver must be a schema-v2 cross_cli_waiver")
    require_exact_keys(
        waiver,
        "waiver",
        {
            "schema_version",
            "kind",
            "status",
            "skill_path",
            "candidate_id",
            "input_manifest_sha256",
            "base_candidate_id",
            "base_aggregate_receipt_sha256",
            "suite_id",
            "policy_id",
            "required_executors",
            "changed_paths",
            "waiver_reason",
            "test_command",
            "test_script",
            "test_script_sha256",
            "test_attestation",
            "test_result_sha256",
            "waiver_id",
        },
    )
    if waiver.get("status") != "waived" or waiver.get("skill_path") != str(skill_dir):
        raise EvaluationError("waiver status or skill path is invalid")
    if (
        waiver.get("candidate_id") != candidate
        or waiver.get("input_manifest_sha256") != input_manifest_sha256
        or waiver.get("suite_id") != suite_id
        or waiver.get("policy_id") != policy_id
    ):
        raise EvaluationError("cross-CLI waiver input identity is stale")
    if waiver.get("required_executors") != policy["required_executors"]:
        raise EvaluationError("cross-CLI waiver required executor set is stale")
    base_sha = require_text(waiver.get("base_aggregate_receipt_sha256"), "waiver.base_aggregate_receipt_sha256")
    base, verified_base_sha = load_v2_receipt(v2_receipt_path(base_sha))
    if verified_base_sha != base_sha:
        raise EvaluationError("cross-CLI waiver base aggregate digest does not match")
    base_candidate = require_sha256(waiver.get("base_candidate_id"), "waiver.base_candidate_id")
    if base.get("candidate_id") != base_candidate:
        raise EvaluationError("cross-CLI waiver does not bind its base aggregate")
    validate_aggregate(
        base,
        skill_dir,
        base_candidate,
        require_sha256(
            base.get("input_manifest_sha256"),
            "base aggregate input_manifest_sha256",
        ),
        suite,
        suite_id,
        policy,
        policy_id,
        allow_advisory_drift=True,
    )
    if base.get("status") != "pass":
        raise EvaluationError("cross-CLI waiver base aggregate must pass")
    changed = changed_inventory_paths(base.get("candidate_inventory", []), files)
    if not changed or waiver.get("changed_paths") != changed:
        raise EvaluationError("cross-CLI waiver changed paths are stale or malformed")
    if "SKILL.md" in changed or not all(path.startswith("scripts/") for path in changed):
        raise EvaluationError("cross-CLI waivers may change only restricted scripts/ paths")
    test_script = require_text(waiver.get("test_script"), "waiver.test_script")
    if waiver.get("test_command") != [test_script]:
        raise EvaluationError("waiver test command is stale or malformed")
    test_path = (skill_dir / test_script).resolve()
    try:
        test_relative = test_path.relative_to(skill_dir).as_posix()
    except ValueError as exc:
        raise EvaluationError("waiver test script must remain inside the skill") from exc
    if test_relative != test_script or not test_relative.startswith("scripts/") or test_relative in changed:
        raise EvaluationError("waiver test script must be unchanged under scripts/")
    current = {item["path"]: item for item in files}
    base_files = {item["path"]: item for item in base["candidate_inventory"]}
    if current.get(test_relative) != base_files.get(test_relative):
        raise EvaluationError("waiver test script identity changed")
    if waiver.get("test_script_sha256") != current[test_relative]["sha256"]:
        raise EvaluationError("waiver test script digest is stale")
    attestation = waiver.get("test_attestation")
    expected_files = {path: current[path]["sha256"] for path in changed}
    if attestation != {"status": "pass", "verified_files": expected_files}:
        raise EvaluationError("waiver test attestation does not bind every changed path")
    if waiver.get("test_result_sha256") != digest(canonical(attestation)):
        raise EvaluationError("waiver test result digest is stale")
    expected_id = identity_with("waiver_id", waiver)
    if waiver.get("waiver_id") != expected_id:
        raise EvaluationError("waiver.waiver_id does not match waiver content")
    return waiver


def v2_waive(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    if args.suite or args.policy:
        raise EvaluationError(
            "waiver authority cannot accept suite or policy path overrides"
        )
    resolved = resolve_ready_input(skill_dir)
    if resolved is None:
        raise EvaluationError("ready external evaluation input is missing")
    candidate = resolved["candidate_id"]
    files = resolved["candidate_inventory"]
    suite = resolved["suite"]
    suite_id = resolved["suite_id"]
    policy = resolved["policy"]
    policy_id = resolved["policy_id"]
    input_manifest_sha256 = resolved["input_manifest_sha256"]
    base, base_sha = load_v2_receipt(Path(args.base_aggregate).resolve())
    base_candidate = require_sha256(base.get("candidate_id"), "base aggregate candidate_id")
    validate_aggregate(
        base,
        skill_dir,
        base_candidate,
        require_sha256(
            base.get("input_manifest_sha256"),
            "base aggregate input_manifest_sha256",
        ),
        suite,
        suite_id,
        policy,
        policy_id,
        allow_advisory_drift=True,
    )
    if base["status"] != "pass":
        raise EvaluationError("cross-CLI waiver requires a passing version-2 aggregate receipt")
    changed = changed_inventory_paths(base.get("candidate_inventory", []), files)
    if not changed:
        raise EvaluationError("cross-CLI waiver requires a candidate change")
    if "SKILL.md" in changed or not all(path.startswith("scripts/") for path in changed):
        raise EvaluationError("cross-CLI waivers may change only restricted scripts/ paths")
    test_path = (skill_dir / args.test_script).resolve()
    try:
        test_script = test_path.relative_to(skill_dir).as_posix()
    except ValueError as exc:
        raise EvaluationError("waiver test script must remain inside the skill") from exc
    before = {item["path"]: item for item in base["candidate_inventory"]}
    after = {item["path"]: item for item in files}
    if not test_script.startswith("scripts/") or test_script in changed or before.get(test_script) != after.get(test_script):
        raise EvaluationError("waiver test script must be an unchanged scripts/ file")
    result = subprocess.run([str(test_path)], cwd=skill_dir, capture_output=True, text=True)
    if result.returncode != 0:
        raise EvaluationError("cross-CLI waiver test command failed")
    try:
        attestation = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise EvaluationError("cross-CLI waiver test command must emit one JSON attestation") from exc
    expected_files = {path: after[path]["sha256"] for path in changed}
    if attestation != {"status": "pass", "verified_files": expected_files}:
        raise EvaluationError("cross-CLI waiver test attestation does not bind every changed file")
    waiver = {
        "schema_version": 2,
        "kind": "cross_cli_waiver",
        "status": "waived",
        "skill_path": str(skill_dir),
        "candidate_id": candidate,
        "input_manifest_sha256": input_manifest_sha256,
        "base_candidate_id": base_candidate,
        "base_aggregate_receipt_sha256": base_sha,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "required_executors": policy["required_executors"],
        "changed_paths": changed,
        "waiver_reason": require_text(args.reason, "waiver reason"),
        "test_command": [test_script],
        "test_script": test_script,
        "test_script_sha256": after[test_script]["sha256"],
        "test_attestation": attestation,
        "test_result_sha256": digest(canonical(attestation)),
    }
    waiver["waiver_id"] = identity_with("waiver_id", waiver)
    path, waiver_sha = write_v2_receipt(waiver)
    atomic_write(
        v2_latest_waiver_path(skill_dir),
        {
            "schema_version": 2,
            "skill_path": str(skill_dir),
            "candidate_id": candidate,
            "input_manifest_sha256": input_manifest_sha256,
            "waiver_path": str(path),
            "waiver_sha256": waiver_sha,
        },
    )
    return {"status": "waived", "receipt": str(path), "receipt_sha256": waiver_sha}


def v2_waiver_validate(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    if args.suite or args.policy:
        raise EvaluationError(
            "waiver validation cannot accept suite or policy path overrides"
        )
    resolved = resolve_ready_input(skill_dir)
    if resolved is None:
        raise EvaluationError("ready external evaluation input is missing")
    candidate = resolved["candidate_id"]
    files = resolved["candidate_inventory"]
    suite = resolved["suite"]
    suite_id = resolved["suite_id"]
    policy = resolved["policy"]
    policy_id = resolved["policy_id"]
    waiver, waiver_sha = load_v2_receipt(Path(args.waiver).resolve())
    validate_v2_waiver(
        waiver,
        skill_dir,
        candidate,
        files,
        suite,
        suite_id,
        policy,
        policy_id,
        resolved["input_manifest_sha256"],
    )
    return {
        "status": "waived",
        "candidate_id": candidate,
        "input_manifest_sha256": resolved["input_manifest_sha256"],
        "receipt_sha256": waiver_sha,
    }


def current_gate(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    skill_key = latest_key(str(skill_dir))
    current_candidate, _ = candidate_id(skill_dir)
    readiness = input_readiness_dir(skill_dir, current_candidate)
    readiness_state_exists = (
        readiness.is_symlink()
        or (readiness.exists() and any(readiness.iterdir()))
    )
    v2_state_exists = (
        (v2_evaluation_dir() / "latest" / f"{skill_key}.json").exists()
        or v2_latest_waiver_path(skill_dir).exists()
        or (v2_evaluation_dir() / "authority" / skill_key).exists()
        or readiness_state_exists
        or v2_transition_dir(skill_dir).exists()
        or v2_transition_dir(skill_dir).is_symlink()
        or input_current_path(skill_dir).exists()
        or input_current_path(skill_dir).is_symlink()
    )
    if not v2_state_exists:
        if not (skill_dir / POLICY_FILE).is_file():
            legacy = argparse.Namespace(skill_dir=str(skill_dir))
            return gate(legacy)
        if (
            load_json(skill_dir / POLICY_FILE).get("schema_version")
            != POLICY_SCHEMA_VERSION
        ):
            legacy = argparse.Namespace(skill_dir=str(skill_dir))
            return gate(legacy)
    resolved = resolve_ready_input(skill_dir)
    if resolved is None:
        raise EvaluationError("ready external evaluation input is missing")
    candidate = resolved["candidate_id"]
    files = resolved["candidate_inventory"]
    suite = resolved["suite"]
    suite_id = resolved["suite_id"]
    policy = resolved["policy"]
    policy_id = resolved["policy_id"]
    if desired_executor_names() != [item["name"] for item in policy["required_executors"]]:
        raise EvaluationError("active required executor set differs from DREAMING_EVALUATION_EXECUTORS")
    authority_path = v2_authority_path(skill_dir, candidate)
    if authority_path.is_file():
        validate_args = argparse.Namespace(
            skill_dir=str(skill_dir), authority=str(authority_path), suite=None, policy=None
        )
        return v2_authority_validate(validate_args)
    latest_path = v2_latest_waiver_path(skill_dir)
    latest = load_json(latest_path)
    require_exact_keys(
        latest,
        "latest waiver",
        {
            "schema_version",
            "skill_path",
            "candidate_id",
            "input_manifest_sha256",
            "waiver_path",
            "waiver_sha256",
        },
    )
    waiver_path = Path(require_text(latest.get("waiver_path"), "latest waiver path")).resolve()
    waiver, waiver_sha = load_v2_receipt(waiver_path)
    if latest != {
        "schema_version": 2,
        "skill_path": str(skill_dir),
        "candidate_id": candidate,
        "input_manifest_sha256": resolved["input_manifest_sha256"],
        "waiver_path": str(waiver_path),
        "waiver_sha256": waiver_sha,
    }:
        raise EvaluationError("latest cross-CLI waiver pointer is stale or malformed")
    validate_v2_waiver(
        waiver,
        skill_dir,
        candidate,
        files,
        suite,
        suite_id,
        policy,
        policy_id,
        resolved["input_manifest_sha256"],
    )
    return {
        "status": "waived",
        "candidate_id": candidate,
        "input_manifest_sha256": resolved["input_manifest_sha256"],
        "receipt_sha256": waiver_sha,
    }


def portfolio_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvaluationError("portfolio now must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise EvaluationError("portfolio now must include a timezone")
    return parsed.astimezone(timezone.utc)


def portfolio_transition_time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            require_text(value, "transition.effective_at").replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise EvaluationError("transition.effective_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise EvaluationError("transition.effective_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def require_hex_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{64}", value) is None
    ):
        raise EvaluationError(f"{field} must be a hexadecimal sha256 identity")
    return value


def portfolio_transition(
    path: Path,
    skill_key: str,
) -> tuple[dict[str, Any], datetime]:
    if path.is_symlink() or not path.is_file():
        raise EvaluationError("portfolio transition must be a regular file")
    value = load_json(path)
    require_exact_keys(
        value,
        "portfolio transition",
        {
            "schema_version",
            "kind",
            "effective_at",
            "skill_key",
            "candidate_id",
            "input_manifest_sha256",
            "status",
            "authority_sha256",
            "aggregate_receipt_sha256",
            "portfolio_receipt_sha256",
            "transition_id",
        },
    )
    if (
        value.get("schema_version") != 1
        or value.get("kind") != "dashboard_authority_transition"
        or value.get("skill_key") != skill_key
        or value.get("status")
        not in {"pass", "regression", "inconclusive", "revoked"}
        or value.get("transition_id") != identity_with("transition_id", value)
        or path.name
        != f"{str(value.get('transition_id')).removeprefix('sha256:')}.json"
    ):
        raise EvaluationError("portfolio transition identity is malformed")
    require_sha256(value.get("candidate_id"), "transition.candidate_id")
    require_sha256(
        value.get("input_manifest_sha256"),
        "transition.input_manifest_sha256",
    )
    status = value["status"]
    authority_sha = value.get("authority_sha256")
    aggregate_sha = value.get("aggregate_receipt_sha256")
    portfolio_sha = value.get("portfolio_receipt_sha256")
    if status == "pass":
        for field, item in (
            ("authority_sha256", authority_sha),
            ("aggregate_receipt_sha256", aggregate_sha),
            ("portfolio_receipt_sha256", portfolio_sha),
        ):
            require_hex_sha256(item, f"transition.{field}")
    elif status in {"regression", "inconclusive"}:
        if authority_sha is not None:
            raise EvaluationError("non-passing transition cannot retain authority")
        require_hex_sha256(
            aggregate_sha, "transition.aggregate_receipt_sha256"
        )
        require_hex_sha256(
            portfolio_sha, "transition.portfolio_receipt_sha256"
        )
    elif any(item is not None for item in (authority_sha, aggregate_sha, portfolio_sha)):
        raise EvaluationError("revoked transition cannot retain evaluation authority")
    return value, portfolio_transition_time(value["effective_at"])


def portfolio_current_value(
    skill_dir: Path,
    *,
    observed_at: datetime,
    max_age_days: int,
) -> dict[str, Any]:
    skill_dir = resolve_path(skill_dir, "skill directory")
    candidate, _ = candidate_id(skill_dir)
    readiness = resolve_input_readiness(skill_dir, missing_ok=True)
    if readiness is None:
        return {
            "state": "input_missing",
            "status": "input_missing",
            "current": False,
            "evaluated_at": None,
            "receipt_sha256": None,
            "transition_id": None,
            "input_manifest_sha256": None,
            "cases": [],
        }
    if readiness["state"] != "ready":
        return {
            "state": readiness["state"],
            "status": readiness["reason"],
            "current": False,
            "evaluated_at": None,
            "receipt_sha256": None,
            "transition_id": readiness["transition_id"],
            "input_manifest_sha256": readiness["input_manifest_sha256"],
            "cases": [],
        }
    resolved_input = resolve_ready_input(skill_dir)
    skill_key = latest_key(str(skill_dir))
    transition_root = v2_transition_dir(skill_dir)
    if not transition_root.exists():
        return {
            "state": "ready",
            "status": "ready",
            "current": False,
            "evaluated_at": None,
            "receipt_sha256": None,
            "transition_id": readiness["transition_id"],
            "input_manifest_sha256": resolved_input["input_manifest_sha256"],
            "cases": [],
        }
    if transition_root.is_symlink() or not transition_root.is_dir():
        raise EvaluationError("portfolio transition root must be a real directory")
    matching: list[tuple[datetime, dict[str, Any]]] = []
    historical: list[tuple[datetime, dict[str, Any]]] = []
    transition_times: set[datetime] = set()
    for path in sorted(transition_root.iterdir()):
        transition, effective_at = portfolio_transition(path, skill_key)
        if effective_at > observed_at:
            raise EvaluationError("portfolio transition is from the future")
        if effective_at in transition_times:
            raise EvaluationError("portfolio transitions share an effective time")
        transition_times.add(effective_at)
        historical.append((effective_at, transition))
        if transition["candidate_id"] == candidate:
            matching.append((effective_at, transition))
    if not historical:
        return {
            "state": "ready",
            "status": "ready",
            "current": False,
            "evaluated_at": None,
            "receipt_sha256": None,
            "transition_id": readiness["transition_id"],
            "input_manifest_sha256": resolved_input["input_manifest_sha256"],
            "cases": [],
        }
    if not matching:
        effective_at, transition = max(historical, key=lambda item: item[0])
        return {
            "state": "stale",
            "status": transition["status"],
            "current": False,
            "evaluated_at": effective_at.isoformat(),
            "receipt_sha256": transition.get("aggregate_receipt_sha256"),
            "transition_id": transition["transition_id"],
            "input_manifest_sha256": resolved_input["input_manifest_sha256"],
            "cases": [],
        }
    effective_at, transition = max(matching, key=lambda item: item[0])
    status = transition["status"]
    if status == "revoked":
        return {
            "state": "stale",
            "status": "revoked",
            "current": False,
            "evaluated_at": effective_at.isoformat(),
            "receipt_sha256": None,
            "transition_id": transition["transition_id"],
            "input_manifest_sha256": resolved_input["input_manifest_sha256"],
            "cases": [],
        }
    if (
        transition["input_manifest_sha256"]
        != resolved_input["input_manifest_sha256"]
    ):
        raise EvaluationError(
            "portfolio transition input manifest is no longer ready"
        )
    candidate_id_value = resolved_input["candidate_id"]
    suite = resolved_input["suite"]
    suite_id = resolved_input["suite_id"]
    policy = resolved_input["policy"]
    policy_id = resolved_input["policy_id"]
    aggregate_sha = transition["aggregate_receipt_sha256"]
    aggregate, verified_sha = load_v2_receipt(v2_receipt_path(aggregate_sha))
    if verified_sha != aggregate_sha:
        raise EvaluationError("portfolio aggregate receipt identity is malformed")
    aggregate = validate_aggregate(
        aggregate,
        skill_dir,
        candidate_id_value,
        resolved_input["input_manifest_sha256"],
        suite,
        suite_id,
        policy,
        policy_id,
    )
    if aggregate["status"] != status:
        raise EvaluationError("portfolio transition status differs from its aggregate")
    portfolio, portfolio_sha = load_portfolio_for_aggregate(aggregate_sha)
    if (
        transition["portfolio_receipt_sha256"] != portfolio_sha
        or portfolio.get("status") != status
        or portfolio.get("candidate_id") != candidate
        or portfolio.get("input_manifest_sha256")
        != resolved_input["input_manifest_sha256"]
    ):
        raise EvaluationError("portfolio transition differs from its portfolio receipt")
    if status == "pass":
        authority_args = argparse.Namespace(
            skill_dir=str(skill_dir),
            authority=None,
            suite=None,
            policy=None,
        )
        authority_result = v2_authority_validate(authority_args)
        if authority_result.get("authority_sha256") != transition["authority_sha256"]:
            raise EvaluationError("portfolio transition authority is stale")
        if (
            authority_result.get("input_manifest_sha256")
            != resolved_input["input_manifest_sha256"]
        ):
            raise EvaluationError("portfolio authority input manifest is stale")
    age_seconds = (observed_at - effective_at).total_seconds()
    current = age_seconds <= max_age_days * 24 * 60 * 60
    return {
        "state": status if current else "stale",
        "status": status,
        "current": current,
        "evaluated_at": effective_at.isoformat(),
        "receipt_sha256": aggregate_sha,
        "transition_id": transition["transition_id"],
        "input_manifest_sha256": resolved_input["input_manifest_sha256"],
        "cases": portfolio["cases"][:100],
    }


def portfolio_current(args: argparse.Namespace) -> dict[str, Any]:
    if args.max_age_days <= 0:
        raise EvaluationError("max-age-days must be positive")
    return portfolio_current_value(
        Path(args.skill_dir),
        observed_at=portfolio_now(args.now),
        max_age_days=args.max_age_days,
    )


def portfolio_inventory(args: argparse.Namespace) -> dict[str, Any]:
    if args.max_age_days <= 0:
        raise EvaluationError("max-age-days must be positive")
    observed_at = portfolio_now(args.now)
    rows = []
    seen: set[str] = set()
    for value in args.skill_dir:
        skill_path = os.path.abspath(value)
        try:
            skill_dir = resolve_path(Path(value), "skill directory")
            skill_path = str(skill_dir)
            evaluation = portfolio_current_value(
                skill_dir,
                observed_at=observed_at,
                max_age_days=args.max_age_days,
            )
        except (EvaluationError, KeyError, OSError, subprocess.SubprocessError):
            evaluation = {
                "state": "invalid",
                "status": "invalid",
                "current": False,
                "evaluated_at": None,
                "receipt_sha256": None,
                "transition_id": None,
                "input_manifest_sha256": None,
                "cases": [],
            }
        if skill_path in seen:
            continue
        seen.add(skill_path)
        rows.append({"skill_path": skill_path, "evaluation": evaluation})
    return {
        "schema_version": 1,
        "observed_at": observed_at.isoformat(),
        "max_age_days": args.max_age_days,
        "evaluations": rows,
    }


SHADOW_SUITE_VERSION = 2
SHADOW_HARNESS_VERSION = 2


def shadow_sha(value: Any) -> str:
    return f"sha256:{digest(shadow_canonical(value))}"


def shadow_inventory(root: Path) -> list[dict[str, Any]]:
    if not root.is_dir() or root.is_symlink():
        raise EvaluationError(f"{root} must be a real directory")
    values: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise EvaluationError(f"{relative}: shadow inputs cannot contain symlinks")
        if path.is_dir():
            continue
        if not path.is_file():
            raise EvaluationError(f"{relative}: shadow inputs must be regular files")
        if relative in LOCAL_SIDECARS:
            continue
        content = path.read_bytes()
        values.append({"path": relative, "sha256": f"sha256:{digest(content)}", "size": len(content)})
    if not values:
        raise EvaluationError(f"{root} has no shadow input files")
    return values


def shadow_catalog(source: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not source.is_dir() or source.is_symlink():
        raise EvaluationError("approved target catalog must be a real directory")
    skills: list[dict[str, Any]] = []
    inventory_value: list[dict[str, Any]] = []
    for path in sorted(source.iterdir()):
        if path.is_symlink() or not path.is_dir() or not (path / "SKILL.md").is_file():
            raise EvaluationError("approved target catalog must contain only skill directories with SKILL.md")
        files = shadow_inventory(path)
        skill_id = shadow_sha(files)
        skills.append(
            {
                "name": path.name,
                "catalog_skill_id": skill_id,
                "skill_md_sha256": next(item["sha256"] for item in files if item["path"] == "SKILL.md"),
                "path": f"catalog/{path.name}/SKILL.md",
            }
        )
        inventory_value.extend(
            [{**item, "path": f"{path.name}/{item['path']}"} for item in files]
        )
    return inventory_value, skills


def shadow_copy_tree(source: Path, destination: Path) -> None:
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_symlink():
            raise EvaluationError(f"{relative}: shadow inputs cannot contain symlinks")
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
            os.chmod(target, path.stat().st_mode & 0o777)
        else:
            raise EvaluationError(f"{relative}: shadow inputs must be regular files")


def shadow_suite(path: Path, catalog: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    raw = load_json(path)
    require_exact_keys(
        raw,
        "shadow suite",
        {"schema_version", "kind", "routing_mode", "environment", "graders", "cases"},
    )
    if (
        raw.get("schema_version") != SHADOW_SUITE_VERSION
        or raw.get("kind") != "shadow_candidate_evaluation_suite"
        or raw.get("routing_mode") not in {"catalog_plus_candidate", "candidate_only"}
        or not isinstance(raw.get("environment"), dict)
        or not raw["environment"]
        or not isinstance(raw.get("graders"), list)
        or not raw["graders"]
        or not isinstance(raw.get("cases"), list)
    ):
        raise EvaluationError("unsupported shadow candidate suite")
    known_catalog = {item["name"] for item in catalog}
    graders: list[dict[str, Any]] = []
    grader_ids: set[str] = set()
    for index, grader in enumerate(raw["graders"]):
        if not isinstance(grader, dict):
            raise EvaluationError(f"shadow graders[{index}] must be an object")
        require_exact_keys(grader, f"shadow graders[{index}]", {"id", "type", "safety", "config"})
        grader_id = require_text(grader.get("id"), f"shadow graders[{index}].id")
        if grader_id in grader_ids or not isinstance(grader.get("safety"), bool) or not isinstance(grader.get("config"), dict):
            raise EvaluationError("shadow grader definition is invalid")
        grader_ids.add(grader_id)
        graders.append(grader)
    classes: set[str] = set()
    case_ids: set[str] = set()
    task_ids: set[str] = set()
    cases: list[dict[str, Any]] = []
    for index, case in enumerate(raw["cases"]):
        field = f"shadow cases[{index}]"
        if not isinstance(case, dict):
            raise EvaluationError(f"{field} must be an object")
        require_exact_keys(
            case,
            field,
            {"id", "class", "task_id", "prompt", "critical", "routing", "artifacts", "graders", "fixture"},
        )
        case_id = require_text(case.get("id"), f"{field}.id")
        task_id = require_text(case.get("task_id"), f"{field}.task_id")
        case_class = require_text(case.get("class"), f"{field}.class")
        if (
            case_id in case_ids
            or task_id in task_ids
            or case_class not in {
                "routing_positive", "routing_close_negative", "routing_unrelated",
                "routing_conflict", "task_value",
            }
            or not isinstance(case.get("critical"), bool)
        ):
            raise EvaluationError("shadow case identifiers, class, or critical flag are invalid")
        case_ids.add(case_id)
        task_ids.add(task_id)
        classes.add(case_class)
        route = case.get("routing")
        if not isinstance(route, dict):
            raise EvaluationError(f"{field}.routing must be an object")
        require_exact_keys(route, f"{field}.routing", {"candidate_load", "catalog_loads"})
        names = route.get("catalog_loads")
        if (
            not isinstance(route.get("candidate_load"), bool)
            or not isinstance(names, list)
            or not all(isinstance(name, str) and name in known_catalog for name in names)
            or len(set(names)) != len(names)
            or (raw["routing_mode"] == "candidate_only" and names)
        ):
            raise EvaluationError("shadow case routing declaration is invalid")
        if (
            (case_class == "routing_positive" and not route["candidate_load"])
            or (case_class in {"routing_close_negative", "routing_unrelated", "routing_conflict"} and route["candidate_load"])
            or (
                case_class == "routing_conflict"
                and raw["routing_mode"] == "catalog_plus_candidate"
                and len(names) != 1
            )
            or (
                case_class == "routing_conflict"
                and raw["routing_mode"] == "candidate_only"
                and names
            )
            or (case_class == "task_value" and not route["candidate_load"])
        ):
            raise EvaluationError("shadow case routing does not match its class")
        artifacts = case.get("artifacts")
        graders_for_case = case.get("graders")
        if (
            not isinstance(artifacts, list)
            or len(set(artifacts)) != len(artifacts)
            or not all(isinstance(item, str) and item and not Path(item).is_absolute() and ".." not in Path(item).parts for item in artifacts)
            or not isinstance(graders_for_case, list)
            or not graders_for_case
            or not set(graders_for_case) <= grader_ids
        ):
            raise EvaluationError("shadow case artifacts or graders are invalid")
        if not any(item["id"] in graders_for_case and item["safety"] for item in graders):
            raise EvaluationError("shadow case requires a safety grader")
        cases.append(
            {
                "id": case_id,
                "class": case_class,
                "task_id": task_id,
                "prompt": require_text(case.get("prompt"), f"{field}.prompt"),
                "critical": case["critical"],
                "routing": {"candidate_load": route["candidate_load"], "catalog_loads": names},
                "artifacts": artifacts,
                "graders": graders_for_case,
                "fixture": require_text(case.get("fixture"), f"{field}.fixture"),
            }
        )
    required = {
        "routing_positive", "routing_close_negative", "routing_unrelated",
        "routing_conflict", "task_value",
    }
    if classes != required:
        raise EvaluationError("shadow suite must cover every routing case and task value")
    suite = {
        "schema_version": SHADOW_HARNESS_VERSION,
        "kind": "shadow_candidate_evaluation_suite",
        "routing_mode": raw["routing_mode"],
        "environment": raw["environment"],
        "environment_id": shadow_sha(raw["environment"]),
        "grader_set_id": shadow_sha(graders),
        "graders": graders,
        "cases": cases,
    }
    return suite, shadow_sha(suite)


def shadow_executors(path: Path) -> list[dict[str, Any]]:
    value = load_json(path)
    require_exact_keys(value, "shadow executors", {"schema_version", "kind", "executors"})
    if value.get("schema_version") != SHADOW_HARNESS_VERSION or value.get("kind") != "shadow_candidate_evaluation_executors":
        raise EvaluationError("unsupported shadow executor identity file")
    if not isinstance(value.get("executors"), list) or not value["executors"]:
        raise EvaluationError("shadow executor list is required")
    names: set[str] = set()
    executors: list[dict[str, Any]] = []
    keys = {
        "name", "model", "adapter_id", "adapter_version", "adapter_executable_sha256",
        "cli_executable_sha256", "cli_version", "tool_policy_id", "limits", "sandbox_id",
        "real_backend", "real_backend_source",
    }
    for index, executor in enumerate(value["executors"]):
        if not isinstance(executor, dict):
            raise EvaluationError(f"shadow executors[{index}] must be an object")
        require_exact_keys(executor, f"shadow executors[{index}]", keys)
        name = require_text(executor.get("name"), f"shadow executors[{index}].name")
        if name in names:
            raise EvaluationError("shadow executor names must be unique")
        names.add(name)
        limits = executor.get("limits")
        if not isinstance(limits, dict):
            raise EvaluationError("shadow executor limits must be an object")
        require_exact_keys(limits, f"shadow executors[{index}].limits", {
            "timeout_seconds", "token_budget", "turn_budget", "tool_budget", "output_bytes",
        })
        normalized = {
            "name": name,
            "model": require_text(executor.get("model"), f"shadow executors[{index}].model"),
            "adapter_id": require_sha256(executor.get("adapter_id"), f"shadow executors[{index}].adapter_id"),
            "adapter_version": require_positive_int(executor.get("adapter_version"), f"shadow executors[{index}].adapter_version"),
            "adapter_executable_sha256": require_sha256(executor.get("adapter_executable_sha256"), f"shadow executors[{index}].adapter_executable_sha256"),
            "cli_executable_sha256": require_sha256(executor.get("cli_executable_sha256"), f"shadow executors[{index}].cli_executable_sha256"),
            "cli_version": require_text(executor.get("cli_version"), f"shadow executors[{index}].cli_version"),
            "tool_policy_id": require_sha256(executor.get("tool_policy_id"), f"shadow executors[{index}].tool_policy_id"),
            "limits": {
                key: require_positive_int(limits.get(key), f"shadow executors[{index}].limits.{key}")
                for key in limits
            },
            "sandbox_id": require_sha256(executor.get("sandbox_id"), f"shadow executors[{index}].sandbox_id"),
            "real_backend": executor.get("real_backend"),
            "real_backend_source": require_text(executor.get("real_backend_source"), f"shadow executors[{index}].real_backend_source"),
        }
        if normalized["adapter_version"] != 1 or not isinstance(normalized["real_backend"], bool):
            raise EvaluationError("shadow executor adapter version or real backend attestation is invalid")
        executors.append(normalized)
    return executors


def shadow_routing(path: Path, executors: list[dict[str, Any]]) -> dict[str, Any]:
    value = load_json(path)
    require_exact_keys(
        value,
        "shadow routing",
        {"schema_version", "kind", "executors"},
    )
    if (
        value.get("schema_version") != SHADOW_HARNESS_VERSION
        or value.get("kind") != "shadow_candidate_evaluation_routing"
        or not isinstance(value.get("executors"), list)
    ):
        raise EvaluationError("unsupported shadow routing")
    expected = {item["name"]: item for item in executors}
    actual: dict[str, dict[str, Any]] = {}
    for index, route in enumerate(value["executors"]):
        field = f"shadow routing.executors[{index}]"
        if not isinstance(route, dict):
            raise EvaluationError(f"{field} must be an object")
        require_exact_keys(
            route,
            field,
            {"name", "adapter_id", "adapter_executable_sha256", "argv"},
        )
        name = require_text(route.get("name"), f"{field}.name")
        argv = route.get("argv")
        executable = (
            Path(argv[0]).resolve()
            if isinstance(argv, list) and argv
            else None
        )
        executor = expected.get(name)
        if (
            name in actual
            or executor is None
            or executable is None
            or not all(isinstance(item, str) and item for item in argv)
            or route.get("adapter_id") != executor["adapter_id"]
            or route.get("adapter_executable_sha256")
            != executor["adapter_executable_sha256"]
            or sha256_file(executable) != executor["adapter_executable_sha256"]
        ):
            raise EvaluationError(f"{field} is not an authorized exact executor route")
        actual[name] = route
    if list(actual) != [item["name"] for item in executors]:
        raise EvaluationError(
            "shadow routing executor set or order differs from the sealed executors"
        )
    return value


def shadow_write_receipt(receipt: dict[str, Any]) -> tuple[Path, str]:
    receipt_sha = digest(shadow_canonical(receipt))
    path = v2_evaluation_dir() / "shadow-receipts" / f"{receipt_sha}.json"
    if path.exists() and shadow_canonical(load_json(path)) != shadow_canonical(receipt):
        raise EvaluationError("shadow receipt collision")
    if not path.exists():
        atomic_write(path, receipt)
    return path, receipt_sha


def shadow_compile(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    run_dir = Path(args.run_dir).resolve()
    catalog_dir = Path(args.catalog_dir).resolve() if args.catalog_dir else None
    if not run_dir.is_dir() or run_dir.is_symlink() or any(run_dir.iterdir()):
        raise EvaluationError("shadow run directory must be real and empty")
    harness = require_trusted_harness(Path(args.harness))
    harness_sha = sha256_file(harness)
    candidate_source = shadow_inventory(skill_dir)
    candidate = shadow_sha(candidate_source)
    provisional_catalog, provisional_skills = (
        shadow_catalog(catalog_dir) if catalog_dir else ([], [])
    )
    suite, suite_id = shadow_suite(Path(args.suite).resolve(), provisional_skills)
    if suite["routing_mode"] == "catalog_plus_candidate" and catalog_dir is None:
        raise EvaluationError("catalog-plus-candidate routing requires an approved target catalog snapshot")
    if suite["routing_mode"] == "candidate_only" and catalog_dir is not None:
        raise EvaluationError("candidate-only routing must not claim a catalog authority")
    executors = shadow_executors(Path(args.executors).resolve())
    routing = shadow_routing(Path(args.routing).resolve(), executors)
    inventory(skill_dir, run_dir / "candidate")
    if catalog_dir:
        shadow_copy_tree(catalog_dir, run_dir / "catalog")
    atomic_write(run_dir / "suite.json", suite)
    file_inventory = canonical_file_inventory(run_dir)
    candidate_inventory = [
        {**item, "path": item["path"].removeprefix("candidate/")}
        for item in file_inventory if item["path"].startswith("candidate/")
    ]
    catalog_inventory = [
        {**item, "path": item["path"].removeprefix("catalog/")}
        for item in file_inventory if item["path"].startswith("catalog/")
    ]
    if candidate_inventory != candidate_source or shadow_sha(candidate_inventory) != candidate:
        raise EvaluationError("shadow candidate projection changed while compiling")
    if catalog_inventory != provisional_catalog:
        raise EvaluationError("shadow catalog projection changed while compiling")
    catalog_id = shadow_sha(catalog_inventory) if catalog_inventory else None
    manifest = {
        "schema_version": SHADOW_HARNESS_VERSION,
        "kind": "shadow_candidate_evaluation_run",
        "invocation_nonce": require_text(args.nonce, "shadow invocation nonce"),
        "candidate_id": candidate,
        "candidate_inventory": candidate_inventory,
        "catalog_id": catalog_id,
        "catalog_inventory": catalog_inventory,
        "catalog_skills": provisional_skills,
        "suite_id": suite_id,
        "environment_id": suite["environment_id"],
        "routing_mode": suite["routing_mode"],
        "executors": executors,
        "routing_id": shadow_sha(routing),
        "harness_executable_sha256": harness_sha,
        "file_inventory": file_inventory,
    }
    manifest["run_id"] = shadow_sha(
        {
            key: manifest[key]
            for key in (
                "schema_version", "kind", "candidate_id", "candidate_inventory", "catalog_id",
                "catalog_inventory", "catalog_skills", "suite_id", "environment_id", "routing_mode",
                "executors", "routing_id", "harness_executable_sha256", "file_inventory",
            )
        }
    )
    atomic_write(run_dir / "manifest.json", manifest)
    return {
        "run_dir": str(run_dir),
        "run_id": manifest["run_id"],
        "candidate_id": candidate,
        "catalog_id": catalog_id,
        "suite_id": suite_id,
        "environment_id": suite["environment_id"],
        "routing_mode": suite["routing_mode"],
    }


def shadow_execute(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir).resolve()
    result_dir = Path(args.result_dir).resolve()
    scratch = Path(args.scratch).resolve()
    harness = require_trusted_harness(Path(args.harness))
    manifest = load_json(run_dir / "manifest.json")
    if manifest.get("harness_executable_sha256") != sha256_file(harness):
        raise EvaluationError("shadow harness executable differs from the sealed run")
    if not result_dir.is_dir() or result_dir.is_symlink() or any(result_dir.iterdir()):
        raise EvaluationError("shadow result directory must be real and empty")
    if not scratch.is_dir() or scratch.is_symlink() or any(scratch.iterdir()):
        raise EvaluationError("shadow scratch directory must be real and empty")
    subprocess.run(
        [
            str(harness), "shadow-run", "--input", str(run_dir), "--output", str(result_dir),
            "--routing", str(Path(args.routing).resolve()), "--scratch", str(scratch),
        ],
        check=True,
    )
    return {"result_dir": str(result_dir), "run_id": manifest["run_id"]}


def shadow_current_identities(
    skill_dir: Path,
    suite_path: Path,
    catalog_dir: Path | None,
    executors_path: Path,
    routing_path: Path,
) -> tuple[str, str | None, str, str, list[dict[str, Any]], str]:
    catalog_inventory, catalog_skills = shadow_catalog(catalog_dir) if catalog_dir else ([], [])
    suite, suite_id = shadow_suite(suite_path, catalog_skills)
    executors = shadow_executors(executors_path)
    return (
        shadow_sha(shadow_inventory(skill_dir)),
        shadow_sha(catalog_inventory) if catalog_inventory else None,
        suite_id,
        suite["environment_id"],
        executors,
        shadow_sha(shadow_routing(routing_path, executors)),
    )


def shadow_certify(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    run_dir = Path(args.run_dir).resolve()
    result_dir = Path(args.result_dir).resolve()
    suite_path = Path(args.suite).resolve()
    catalog_dir = Path(args.catalog_dir).resolve() if args.catalog_dir else None
    executors_path = Path(args.executors).resolve()
    routing_path = Path(args.routing).resolve()
    sealed = load_json(run_dir / "manifest.json")
    result_id = None
    try:
        current = shadow_current_identities(
            skill_dir, suite_path, catalog_dir, executors_path, routing_path
        )
        expected = (
            sealed.get("candidate_id"), sealed.get("catalog_id"), sealed.get("suite_id"),
            sealed.get("environment_id"), sealed.get("executors"), sealed.get("routing_id"),
        )
        if current != expected:
            status = "stale"
            aggregate = None
            reason = "candidate, catalog, suite, environment, or executor identity drift"
        else:
            harness = require_trusted_harness(Path(args.harness))
            scratch = Path(args.scratch).resolve()
            if not scratch.is_dir() or scratch.is_symlink() or any(scratch.iterdir()):
                raise EvaluationError("shadow certification scratch directory must be real and empty")
            subprocess.run(
                [str(harness), "shadow-verify", "--result", str(result_dir), "--scratch", str(scratch)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            result = load_json(result_dir / "manifest.json")
            for field in (
                "run_id",
                "candidate_id",
                "catalog_id",
                "suite_id",
                "environment_id",
                "harness_executable_sha256",
                "routing_mode",
                "routing_id",
            ):
                if result.get(field) != sealed.get(field):
                    raise EvaluationError("shadow result does not bind the exact compiled candidate, catalog, suite, and environment")
            aggregate = result.get("aggregate")
            result_id = require_sha256(result.get("result_id"), "shadow result.result_id")
            if not isinstance(aggregate, dict):
                raise EvaluationError("shadow result has no aggregate")
            status = aggregate.get("status")
            if status not in {"pass", "regression", "inconclusive"}:
                raise EvaluationError("shadow aggregate status is unsupported")
            reason = None
    except (EvaluationError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        status = "inconclusive"
        aggregate = None
        reason = f"independent evidence verification failed: {error}"
    receipt = {
        "schema_version": SHADOW_HARNESS_VERSION,
        "kind": "shadow_candidate_evaluation_receipt",
        "status": status,
        "candidate_id": sealed.get("candidate_id"),
        "catalog_id": sealed.get("catalog_id"),
        "suite_id": sealed.get("suite_id"),
        "environment_id": sealed.get("environment_id"),
        "harness_executable_sha256": sealed.get("harness_executable_sha256"),
        "executors": sealed.get("executors"),
        "routing_mode": sealed.get("routing_mode"),
        "routing_id": sealed.get("routing_id"),
        "run_id": sealed.get("run_id"),
        "result_dir": str(result_dir),
        "result_id": result_id,
        "aggregate": aggregate,
        "reason": reason,
        "authoritative": bool(
            status == "pass"
            and sealed.get("routing_mode") == "catalog_plus_candidate"
            and all(item.get("real_backend") is True for item in sealed.get("executors", []))
        ),
    }
    receipt["receipt_id"] = shadow_sha(receipt)
    path, receipt_sha = shadow_write_receipt(receipt)
    return {
        "status": status,
        "authoritative": receipt["authoritative"],
        "receipt": str(path),
        "receipt_sha256": receipt_sha,
        "candidate_id": receipt["candidate_id"],
        "catalog_id": receipt["catalog_id"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("skill_dir")
    prepare_parser.add_argument("--cases")
    prepare_parser.add_argument("--model", required=True)
    prepare_parser.add_argument("--run-dir", required=True)
    prepare_parser.add_argument("--plugin-dir", required=True)
    finalize_parser = commands.add_parser("finalize")
    finalize_parser.add_argument("--run-dir", required=True)
    gate_parser = commands.add_parser("gate")
    gate_parser.add_argument("skill_dir")
    current_gate_parser = commands.add_parser("current-gate")
    current_gate_parser.add_argument("skill_dir")
    portfolio_current_parser = commands.add_parser("portfolio-current")
    portfolio_current_parser.add_argument("skill_dir")
    portfolio_current_parser.add_argument("--now")
    portfolio_current_parser.add_argument("--max-age-days", type=int, default=90)
    portfolio_inventory_parser = commands.add_parser("portfolio-inventory")
    portfolio_inventory_parser.add_argument("skill_dir", nargs="+")
    portfolio_inventory_parser.add_argument("--now")
    portfolio_inventory_parser.add_argument("--max-age-days", type=int, default=90)
    waive_parser = commands.add_parser("waive")
    waive_parser.add_argument("skill_dir")
    waive_parser.add_argument("--base-receipt", required=True)
    waive_parser.add_argument("--waiver-class", choices=sorted(WAIVER_CLASSES), required=True)
    waive_parser.add_argument("--reason", required=True)
    waive_parser.add_argument("--test-script")
    suite_validate_parser = commands.add_parser("v2-suite-validate")
    suite_validate_parser.add_argument("suite")
    policy_validate_parser = commands.add_parser("v2-policy-validate")
    policy_validate_parser.add_argument("policy")
    input_author_packet_parser = commands.add_parser("v2-input-author-packet")
    input_author_packet_parser.add_argument("skill_dir")
    input_author_packet_parser.add_argument("--suite", required=True)
    input_author_packet_parser.add_argument("--policy", required=True)
    input_author_packet_parser.add_argument("--config", required=True)
    input_author_packet_parser.add_argument("--routing", required=True)
    input_author_packet_parser.add_argument("--harness", required=True)
    input_author_packet_parser.add_argument("--catalog", required=True)
    input_author_packet_parser.add_argument("--output", required=True)
    input_claim_parser = commands.add_parser("v2-input-claim")
    input_claim_parser.add_argument("skill_dir")
    input_claim_parser.add_argument("--owner-run-id", required=True)
    input_claim_parser.add_argument("--author-model", required=True)
    input_claim_parser.add_argument("--reviewer-a-model", required=True)
    input_claim_parser.add_argument("--reviewer-b-model", required=True)
    input_claim_inspect_parser = commands.add_parser(
        "v2-input-claim-inspect"
    )
    input_claim_inspect_parser.add_argument("--claim-id", required=True)
    input_author_parser = commands.add_parser("v2-input-author")
    input_author_parser.add_argument("skill_dir")
    input_author_parser.add_argument("--claim-id", required=True)
    input_author_parser.add_argument(
        "--slot",
        choices=[item[0] for item in CLAIM_SLOT_DEFINITIONS],
        default="author",
    )
    input_author_parser.add_argument("--suite", required=True)
    input_author_parser.add_argument("--policy", required=True)
    input_author_parser.add_argument("--config", required=True)
    input_author_parser.add_argument("--routing", required=True)
    input_author_parser.add_argument("--harness", required=True)
    input_author_parser.add_argument("--catalog", required=True)
    input_author_parser.add_argument("--model", required=True)
    input_author_parser.add_argument("--timeout", type=int, default=600)
    input_author_parser.add_argument("--token-budget", type=int, default=18_000)
    input_author_parser.add_argument("--output-bytes", type=int, default=100_000)
    input_author_parser.add_argument("--output-dir", required=True)
    input_repair_packet_parser = commands.add_parser("v2-input-repair-packet")
    input_repair_packet_parser.add_argument("skill_dir")
    input_repair_packet_parser.add_argument("--claim-id", required=True)
    input_repair_packet_parser.add_argument("--manifest", required=True)
    input_repair_packet_parser.add_argument("--validation", required=True)
    input_repair_packet_parser.add_argument(
        "--review", action="append", required=True
    )
    input_repair_packet_parser.add_argument(
        "--original-author-model", required=True
    )
    input_repair_packet_parser.add_argument("--output", required=True)
    input_repair_parser = commands.add_parser("v2-input-repair")
    input_repair_parser.add_argument("skill_dir")
    input_repair_parser.add_argument("--claim-id", required=True)
    input_repair_parser.add_argument("--manifest", required=True)
    input_repair_parser.add_argument("--validation", required=True)
    input_repair_parser.add_argument(
        "--review", action="append", required=True
    )
    input_repair_parser.add_argument(
        "--original-author-model", required=True
    )
    input_repair_parser.add_argument("--timeout", type=int, default=600)
    input_repair_parser.add_argument("--token-budget", type=int, default=18_000)
    input_repair_parser.add_argument("--output-bytes", type=int, default=100_000)
    input_repair_parser.add_argument("--output-dir", required=True)
    input_author_materialize_parser = commands.add_parser(
        "v2-input-author-materialize"
    )
    input_author_materialize_parser.add_argument("skill_dir")
    input_author_materialize_parser.add_argument("--suite", required=True)
    input_author_materialize_parser.add_argument("--policy", required=True)
    input_author_materialize_parser.add_argument("--config", required=True)
    input_author_materialize_parser.add_argument("--routing", required=True)
    input_author_materialize_parser.add_argument("--harness", required=True)
    input_author_materialize_parser.add_argument("--catalog", required=True)
    input_author_materialize_parser.add_argument("--packet", required=True)
    input_author_materialize_parser.add_argument("--draft", required=True)
    input_author_materialize_parser.add_argument("--output-dir", required=True)
    input_register_parser = commands.add_parser("v2-input-register")
    input_register_parser.add_argument("skill_dir")
    input_register_parser.add_argument("--suite", required=True)
    input_register_parser.add_argument("--policy", required=True)
    input_register_parser.add_argument("--config", required=True)
    input_register_parser.add_argument("--routing", required=True)
    input_register_parser.add_argument("--harness", required=True)
    input_register_parser.add_argument("--authoring-method", required=True)
    input_register_parser.add_argument(
        "--authoring-packet", help=argparse.SUPPRESS
    )
    input_register_parser.add_argument(
        "--authoring-draft", help=argparse.SUPPRESS
    )
    input_register_parser.add_argument(
        "--authoring-receipt", help=argparse.SUPPRESS
    )
    input_register_parser.add_argument(
        "--authoring-operation", help=argparse.SUPPRESS
    )
    input_register_parser.add_argument(
        "--source-id", action="append", required=True
    )
    input_validate_parser = commands.add_parser("v2-input-validate")
    input_validate_parser.add_argument("skill_dir")
    input_validate_parser.add_argument("--manifest", required=True)
    input_review_parser = commands.add_parser("v2-input-review")
    input_review_parser.add_argument("skill_dir")
    input_review_parser.add_argument("--manifest", required=True)
    input_review_parser.add_argument("--claim-id")
    input_review_parser.add_argument(
        "--slot",
        choices=[item[0] for item in CLAIM_SLOT_DEFINITIONS],
    )
    input_review_parser.add_argument("--reviewer")
    input_review_parser.add_argument(
        "--decision", choices=("accept", "reject")
    )
    input_review_parser.add_argument("--validation")
    input_review_parser.add_argument("--model")
    input_review_parser.add_argument("--timeout", type=int, default=600)
    input_review_parser.add_argument("--token-budget", type=int, default=18_000)
    input_review_parser.add_argument("--output-bytes", type=int, default=100_000)
    input_review_packet_parser = commands.add_parser(
        "v2-input-review-packet"
    )
    input_review_packet_parser.add_argument("skill_dir")
    input_review_packet_parser.add_argument("--manifest", required=True)
    input_review_packet_parser.add_argument("--validation", required=True)
    input_review_packet_parser.add_argument("--output", required=True)
    input_state_parser = commands.add_parser("v2-input-state")
    input_state_parser.add_argument("skill_dir")
    input_state_parser.add_argument(
        "--state",
        choices=sorted(INPUT_READINESS_STATES - {"ready"}),
        required=True,
    )
    input_state_parser.add_argument("--reason", required=True)
    input_state_parser.add_argument("--manifest")
    input_state_parser.add_argument("--validation")
    input_state_parser.add_argument("--review", action="append", default=[])
    input_state_parser.add_argument("--created-at")
    input_ready_parser = commands.add_parser("v2-input-ready")
    input_ready_parser.add_argument("skill_dir")
    input_ready_parser.add_argument("--claim-id")
    input_ready_parser.add_argument("--manifest", required=True)
    input_ready_parser.add_argument("--validation", required=True)
    input_ready_parser.add_argument(
        "--review", action="append", required=True
    )
    input_ready_parser.add_argument("--created-at")
    commands.add_parser("v2-input-owner-reconcile")
    input_owner_recover_parser = commands.add_parser(
        "v2-input-owner-recover"
    )
    input_owner_recover_parser.add_argument("--claim-id", required=True)
    input_owner_recover_parser.add_argument(
        "--expected-owner-run-id", required=True
    )
    input_owner_recover_parser.add_argument(
        "--confirm-owner-dead", action="store_true", required=True
    )
    input_claim_assert_parser = commands.add_parser(
        "v2-input-claim-assert-ready"
    )
    input_claim_assert_parser.add_argument("skill_dir")
    input_claim_assert_parser.add_argument("--claim-id", required=True)
    input_claim_assert_parser.add_argument("--manifest", required=True)
    input_claim_assert_parser.add_argument("--validation", required=True)
    input_claim_assert_parser.add_argument(
        "--review", action="append", required=True
    )
    v2_prepare_parser = commands.add_parser("v2-prepare")
    v2_prepare_parser.add_argument("skill_dir")
    v2_prepare_parser.add_argument("--suite")
    v2_prepare_parser.add_argument("--policy")
    run_compile_parser = commands.add_parser("v2-run-compile")
    run_compile_parser.add_argument("skill_dir")
    run_compile_parser.add_argument("--run-dir", required=True)
    run_compile_parser.add_argument("--config")
    run_compile_parser.add_argument("--routing")
    run_compile_parser.add_argument("--nonce", required=True)
    run_compile_parser.add_argument("--harness", required=True)
    run_compile_parser.add_argument("--suite")
    run_compile_parser.add_argument("--policy")
    run_execute_parser = commands.add_parser("v2-run-execute")
    run_execute_parser.add_argument("--run-dir", required=True)
    run_execute_parser.add_argument("--result-dir", required=True)
    run_execute_parser.add_argument("--routing")
    run_execute_parser.add_argument("--scratch", required=True)
    run_execute_parser.add_argument("--harness", required=True)
    result_certify_parser = commands.add_parser("v2-result-certify")
    result_certify_parser.add_argument("skill_dir")
    result_certify_parser.add_argument("--run-dir", required=True)
    result_certify_parser.add_argument("--result-dir", required=True)
    result_certify_parser.add_argument("--routing")
    result_certify_parser.add_argument("--scratch", required=True)
    result_certify_parser.add_argument("--nonce", required=True)
    result_certify_parser.add_argument("--harness", required=True)
    result_certify_parser.add_argument("--suite")
    result_certify_parser.add_argument("--policy")
    unavailable_parser = commands.add_parser("v2-unavailable-aggregate")
    unavailable_parser.add_argument("skill_dir")
    unavailable_parser.add_argument("--unavailable", action="append", required=True)
    unavailable_parser.add_argument("--suite")
    unavailable_parser.add_argument("--policy")
    authority_write_parser = commands.add_parser("v2-authority-write")
    authority_write_parser.add_argument("skill_dir")
    authority_write_parser.add_argument("--aggregate", required=True)
    authority_write_parser.add_argument("--suite")
    authority_write_parser.add_argument("--policy")
    authority_validate_parser = commands.add_parser("v2-authority-validate")
    authority_validate_parser.add_argument("skill_dir")
    authority_validate_parser.add_argument("--authority")
    authority_validate_parser.add_argument("--suite")
    authority_validate_parser.add_argument("--policy")
    v2_waive_parser = commands.add_parser("v2-waive")
    v2_waive_parser.add_argument("skill_dir")
    v2_waive_parser.add_argument("--base-aggregate", required=True)
    v2_waive_parser.add_argument("--reason", required=True)
    v2_waive_parser.add_argument("--test-script", required=True)
    v2_waive_parser.add_argument("--suite")
    v2_waive_parser.add_argument("--policy")
    v2_waiver_validate_parser = commands.add_parser("v2-waiver-validate")
    v2_waiver_validate_parser.add_argument("skill_dir")
    v2_waiver_validate_parser.add_argument("--waiver", required=True)
    v2_waiver_validate_parser.add_argument("--suite")
    v2_waiver_validate_parser.add_argument("--policy")
    shadow_compile_parser = commands.add_parser("shadow-compile")
    shadow_compile_parser.add_argument("skill_dir")
    shadow_compile_parser.add_argument("--suite", required=True)
    shadow_compile_parser.add_argument("--catalog-dir")
    shadow_compile_parser.add_argument("--executors", required=True)
    shadow_compile_parser.add_argument("--routing", required=True)
    shadow_compile_parser.add_argument("--run-dir", required=True)
    shadow_compile_parser.add_argument("--nonce", required=True)
    shadow_compile_parser.add_argument("--harness", required=True)
    shadow_execute_parser = commands.add_parser("shadow-execute")
    shadow_execute_parser.add_argument("--run-dir", required=True)
    shadow_execute_parser.add_argument("--result-dir", required=True)
    shadow_execute_parser.add_argument("--routing", required=True)
    shadow_execute_parser.add_argument("--scratch", required=True)
    shadow_execute_parser.add_argument("--harness", required=True)
    shadow_certify_parser = commands.add_parser("shadow-certify")
    shadow_certify_parser.add_argument("skill_dir")
    shadow_certify_parser.add_argument("--suite", required=True)
    shadow_certify_parser.add_argument("--catalog-dir")
    shadow_certify_parser.add_argument("--executors", required=True)
    shadow_certify_parser.add_argument("--routing", required=True)
    shadow_certify_parser.add_argument("--run-dir", required=True)
    shadow_certify_parser.add_argument("--result-dir", required=True)
    shadow_certify_parser.add_argument("--scratch", required=True)
    shadow_certify_parser.add_argument("--harness", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = {
            "prepare": prepare,
            "finalize": finalize,
            "gate": gate,
            "current-gate": current_gate,
            "portfolio-current": portfolio_current,
            "portfolio-inventory": portfolio_inventory,
            "waive": waive,
            "v2-suite-validate": v2_suite_validate,
            "v2-policy-validate": v2_policy_validate,
            "v2-input-author-packet": v2_input_author_packet,
            "v2-input-claim": v2_input_claim,
            "v2-input-claim-inspect": v2_input_claim_inspect,
            "v2-input-author": v2_input_author,
            "v2-input-repair-packet": v2_input_repair_packet,
            "v2-input-repair": v2_input_repair,
            "v2-input-author-materialize": v2_input_author_materialize,
            "v2-input-register": v2_input_register,
            "v2-input-validate": v2_input_validate,
            "v2-input-review": v2_input_review,
            "v2-input-review-packet": v2_input_review_packet,
            "v2-input-state": v2_input_state,
            "v2-input-ready": v2_input_ready,
            "v2-input-owner-reconcile": v2_input_owner_reconcile,
            "v2-input-owner-recover": v2_input_owner_recover,
            "v2-input-claim-assert-ready": v2_input_claim_assert_ready,
            "v2-prepare": v2_prepare,
            "v2-run-compile": v2_run_compile,
            "v2-run-execute": v2_run_execute,
            "v2-result-certify": v2_result_certify,
            "v2-unavailable-aggregate": v2_unavailable_aggregate,
            "v2-authority-write": v2_authority_write,
            "v2-authority-validate": v2_authority_validate,
            "v2-waive": v2_waive,
            "v2-waiver-validate": v2_waiver_validate,
            "shadow-compile": shadow_compile,
            "shadow-execute": shadow_execute,
            "shadow-certify": shadow_certify,
        }[args.command](args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (
        ClaimLedgerError,
        EvaluationError,
        KeyError,
        OSError,
        sqlite3.Error,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as exc:
        print(f"REFUSED: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
