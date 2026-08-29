#!/usr/bin/env python3
"""Own shadow-only candidate lifecycle records and immutable draft packages.

Records use DREAMING_STATE_ROOT when set.  DREAMING_STATE_DIR is the existing
Dreaming fallback, followed by XDG_STATE_HOME/dreaming.  Packages similarly use
DREAMING_DATA_ROOT, DREAMING_DATA_DIR, then XDG_DATA_HOME/dreaming.  Neither
root is a skill discovery or publisher root.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
DAY = timedelta(days=1)
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
FILE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
REASON_RE = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")
STATES = {
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
SHADOW_STATES = {"collecting", "ready_for_draft", "evaluating", "expired", "rejected", "absorbed"}
MATCH_OUTCOMES = {"same", "different", "uncertain", "duplicate", "supersedes", "absorbs"}
SHADOW_TRANSITIONS = {
    "collecting": {"ready_for_draft", "expired", "rejected", "absorbed"},
    "ready_for_draft": {"collecting", "evaluating", "expired", "rejected", "absorbed"},
    "evaluating": {"collecting", "ready_for_draft", "expired", "rejected", "absorbed"},
    "expired": {"collecting", "rejected", "absorbed"},
    "rejected": {"collecting", "absorbed"},
    "absorbed": set(),
}
DECLARED_TRANSITIONS = {
    **SHADOW_TRANSITIONS,
    "legacy_probation": {"ready_for_draft", "evaluating", "quarantined", "archived"},
    "portfolio_pending": {"admitted", "rejected", "quarantined"},
    "admitted": {"quarantined", "archived"},
    "quarantined": {"archived"},
    "archived": set(),
}
RECORD_KEYS = {
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
PROCEDURE_KEYS = {"schema_version", "trigger", "outcome", "actions", "exclusions", "match_fingerprint"}
EVIDENCE_KEYS = {"evidence_id", "task_key", "source_session_id", "canonical_occurrence_id", "occurred_at", "decision_at", "resolution_sha256", "summary", "procedure_fingerprint"}
REVISION_KEYS = {"candidate_id", "package_path", "files", "staged_at"}
FILE_KEYS = {"path", "sha256", "size"}
LIFECYCLE_KEYS = {"created_at", "last_supported_at", "expires_at", "transition_history"}
TRANSITION_KEYS = {
    "transition_id",
    "from_state",
    "to_state",
    "at",
    "reason",
    "authorizing_evidence_ids",
    "receipt_ids",
}
EVALUATION_KEYS = {"status", "last_evaluated_at", "history"}
EVALUATION_HISTORY_KEYS = {
    "evaluation_id",
    "evaluated_at",
    "recommendation",
    "reasons",
    "candidate_id",
    "shadow_only",
}
DECISION_KEYS = {
    "decision_id",
    "at",
    "outcome",
    "reason",
    "related_lifecycle_id",
    "evidence_ids",
    "shadow_only",
}
BLOCKER_KEYS = {"covering_lifecycle_ids", "tombstone_ids", "uncertain"}


class LifecycleError(ValueError):
    """A deterministic, fail-closed lifecycle error."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def now_epoch() -> int:
    raw = os.environ.get("DREAMING_NOW_EPOCH", os.environ.get("SKILLS_NOW_EPOCH"))
    if raw is None:
        return int(time.time())
    try:
        return int(raw)
    except ValueError as error:
        raise LifecycleError("DREAMING_NOW_EPOCH must be an integer") from error


def now_time() -> datetime:
    return datetime.fromtimestamp(now_epoch(), timezone.utc)


def iso(value: datetime | None = None) -> str:
    point = value or now_time()
    return point.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any, field: str) -> datetime:
    require_text(value, field, 64)
    try:
        point = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise LifecycleError(f"{field} must be an RFC3339 timestamp") from error
    if point.tzinfo is None:
        raise LifecycleError(f"{field} must include a timezone")
    return point.astimezone(timezone.utc)


def state_root() -> Path:
    home = Path.home()
    fallback = Path(os.environ.get("XDG_STATE_HOME", home / ".local" / "state")) / "dreaming"
    return Path(
        os.environ.get(
            "DREAMING_STATE_ROOT",
            os.environ.get("DREAMING_STATE_DIR", fallback),
        )
    )


def data_root() -> Path:
    home = Path.home()
    fallback = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / "dreaming"
    return Path(
        os.environ.get(
            "DREAMING_DATA_ROOT",
            os.environ.get("DREAMING_DATA_DIR", fallback),
        )
    )


def records_root() -> Path:
    return state_root() / "skill-review" / "candidates" / "v1" / "records"


def packages_root() -> Path:
    return data_root() / "candidates" / "v1" / "packages"


def require_text(value: Any, field: str, maximum: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise LifecycleError(f"{field} must be non-empty text no longer than {maximum} characters")
    return value


def require_exact_keys(value: Any, keys: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise LifecycleError(f"{field} must have exactly: {', '.join(sorted(keys))}")
    return value


def require_uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise LifecycleError(f"{field} must be a UUID")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise LifecycleError(f"{field} must be a UUID") from error
    if str(parsed) != value.lower():
        raise LifecycleError(f"{field} must be a canonical UUID")
    return value


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise LifecycleError(f"{field} must be a sha256 identity")
    return value


def require_reason(value: Any, field: str = "reason") -> str:
    if not isinstance(value, str) or not REASON_RE.fullmatch(value):
        raise LifecycleError(f"{field} must be a lowercase reason code")
    return value


def reject_symlink(path: Path, field: str) -> None:
    if path.is_symlink():
        raise LifecycleError(f"{field} may not be a symlink: {path}")


def read_json(path: Path, field: str) -> Any:
    reject_symlink(path, field)
    if not path.is_file():
        raise LifecycleError(f"{field} must be a regular file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LifecycleError(f"cannot read valid JSON from {field}: {error}") from error


def read_json_object(path: str, field: str) -> dict[str, Any]:
    value = read_json(Path(path), field)
    if not isinstance(value, dict):
        raise LifecycleError(f"{field} must be a JSON object")
    return value


def atomic_json(path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink(path.parent, "record directory")
    reject_symlink(path, "record")
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}"
    try:
        with temporary.open("xb") as handle:
            handle.write(canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def validate_procedure(value: Any) -> dict[str, Any]:
    procedure = require_exact_keys(value, PROCEDURE_KEYS, "procedure")
    if procedure["schema_version"] != 1:
        raise LifecycleError("procedure.schema_version must be 1")
    require_text(procedure["trigger"], "procedure.trigger")
    require_text(procedure["outcome"], "procedure.outcome")
    actions = procedure["actions"]
    exclusions = procedure["exclusions"]
    if not isinstance(actions, list) or not 1 <= len(actions) <= 16:
        raise LifecycleError("procedure.actions must be an ordered list of 1 to 16 entries")
    if not isinstance(exclusions, list) or not 1 <= len(exclusions) <= 16:
        raise LifecycleError("procedure.exclusions must be a list of 1 to 16 entries")
    for index, item in enumerate(actions):
        require_text(item, f"procedure.actions[{index}]")
    for index, item in enumerate(exclusions):
        require_text(item, f"procedure.exclusions[{index}]")
    require_sha256(procedure["match_fingerprint"], "procedure.match_fingerprint")
    return procedure


def validate_observation(value: Any, procedure: dict[str, Any]) -> dict[str, Any]:
    # V1 observations stay readable and may be retained in explicit legacy
    # records, but recurrence() never grants them current authority.
    legacy_keys = {"task_key", "session_id", "observed_at", "independence", "summary", "procedure_fingerprint"}
    if isinstance(value, dict) and set(value) == legacy_keys:
        require_text(value["task_key"], "observation.task_key", 512); require_text(value["session_id"], "observation.session_id", 512)
        parse_time(value["observed_at"], "observation.observed_at")
        if value["independence"] not in {"verified", "unverified"}: raise LifecycleError("observation.independence must be verified or unverified")
        require_text(value["summary"], "observation.summary"); require_sha256(value["procedure_fingerprint"], "observation.procedure_fingerprint")
        if value["procedure_fingerprint"] != procedure["match_fingerprint"]: raise LifecycleError("observation.procedure_fingerprint must match procedure.match_fingerprint")
        evidence=dict(value); evidence["evidence_id"]=sha256(canonical(value)); return {key:evidence[key] for key in sorted(legacy_keys | {"evidence_id"})}
    observation = require_exact_keys(value, EVIDENCE_KEYS - {"evidence_id"}, "observation")
    require_text(observation["task_key"], "observation.task_key", 512); require_text(observation["source_session_id"], "observation.source_session_id", 512)
    require_sha256(observation["canonical_occurrence_id"], "observation.canonical_occurrence_id"); require_sha256(observation["resolution_sha256"], "observation.resolution_sha256")
    occurred = parse_time(observation["occurred_at"], "observation.occurred_at"); decision = parse_time(observation["decision_at"], "observation.decision_at")
    if occurred > decision: raise LifecycleError("observation.occurred_at may not be after decision_at")
    require_text(observation["summary"], "observation.summary"); require_sha256(observation["procedure_fingerprint"], "observation.procedure_fingerprint")
    if observation["procedure_fingerprint"] != procedure["match_fingerprint"]: raise LifecycleError("observation.procedure_fingerprint must match procedure.match_fingerprint")
    evidence=dict(observation); evidence["evidence_id"]=sha256(canonical(observation)); return {key:evidence[key] for key in sorted(EVIDENCE_KEYS)}


def validate_files(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise LifecycleError(f"{field} must be a non-empty file inventory")
    paths: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        item = require_exact_keys(item, FILE_KEYS, f"{field}[{index}]")
        relative = item["path"]
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or relative in paths
        ):
            raise LifecycleError(f"{field}[{index}].path must be a unique relative path")
        paths.add(relative)
        if not isinstance(item["sha256"], str) or not FILE_SHA256_RE.fullmatch(item["sha256"]):
            raise LifecycleError(f"{field}[{index}].sha256 must be a sha256 digest")
        if not isinstance(item["size"], int) or item["size"] < 0:
            raise LifecycleError(f"{field}[{index}].size must be a non-negative integer")
        result.append(item)
    if result != sorted(result, key=lambda item: item["path"]):
        raise LifecycleError(f"{field} must be sorted by path")
    return result


def package_inventory(root: Path, field: str) -> list[dict[str, Any]]:
    reject_symlink(root, field)
    if not root.is_dir():
        raise LifecycleError(f"{field} must be a directory: {root}")
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        reject_symlink(path, field)
        if path.is_dir():
            continue
        if not path.is_file():
            raise LifecycleError(f"{field} contains a non-regular path: {relative}")
        content = path.read_bytes()
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    return validate_files(files, f"{field}.files")


def candidate_identity(files: list[dict[str, Any]]) -> str:
    return sha256(canonical(files))


def package_skill_name(root: Path, field: str) -> str:
    skill_path = root / "SKILL.md"
    reject_symlink(skill_path, field)
    if not skill_path.is_file():
        raise LifecycleError(f"{field} must contain SKILL.md")
    try:
        content = skill_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise LifecycleError(f"{field} SKILL.md must be UTF-8") from exc
    if not content.startswith("---\n"):
        raise LifecycleError(f"{field} SKILL.md must start with YAML frontmatter")
    closing = content.find("\n---\n", 4)
    if closing < 0:
        raise LifecycleError(f"{field} SKILL.md frontmatter is not closed")
    frontmatter = content[4:closing]
    names = re.findall(r"^name:[ \t]*([a-z][a-z0-9-]{2,63})[ \t]*$", frontmatter, re.MULTILINE)
    if len(names) != 1:
        raise LifecycleError(f"{field} SKILL.md must declare exactly one canonical name")
    return names[0]


def package_path(lifecycle_id: str, candidate_id: str) -> Path:
    return packages_root() / lifecycle_id / candidate_id


def package_reference(lifecycle_id: str, candidate_id: str) -> str:
    return f"candidates/v1/packages/{lifecycle_id}/{candidate_id}"


def verify_package(lifecycle_id: str, revision: dict[str, Any], proposed_name: str) -> None:
    candidate = require_sha256(revision["candidate_id"], "candidate revision.candidate_id")
    expected = package_path(lifecycle_id, candidate)
    if revision["package_path"] != package_reference(lifecycle_id, candidate):
        raise LifecycleError("candidate revision.package_path does not name its exact immutable package")
    actual_files = package_inventory(expected, "immutable package")
    if package_skill_name(expected, "immutable package") != proposed_name:
        raise LifecycleError("immutable package SKILL.md name does not match candidate proposed_name")
    if actual_files != revision["files"] or candidate_identity(actual_files) != candidate:
        raise LifecycleError(f"immutable package content does not match {candidate}")
    if os.access(expected, os.W_OK):
        raise LifecycleError(f"immutable package is writable: {expected}")


def make_immutable_package(
    lifecycle_id: str, source: Path, proposed_name: str
) -> tuple[str, list[dict[str, Any]], bool]:
    source_files = package_inventory(source, "package source")
    if package_skill_name(source, "package source") != proposed_name:
        raise LifecycleError("package source SKILL.md name does not match --proposed-name")
    candidate = candidate_identity(source_files)
    target = package_path(lifecycle_id, candidate)
    if target.exists() or target.is_symlink():
        reject_symlink(target, "immutable package")
        actual = package_inventory(target, "immutable package")
        if actual != source_files or candidate_identity(actual) != candidate:
            raise LifecycleError(f"immutable package collision for {candidate}")
        if os.access(target, os.W_OK):
            raise LifecycleError(f"immutable package is writable: {target}")
        return candidate, source_files, False

    target.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink(target.parent, "package directory")
    staging = target.parent / f".{candidate}.creating-{uuid.uuid4().hex}"
    try:
        staging.mkdir(mode=0o700)
        for item in source_files:
            source_path = source / item["path"]
            reject_symlink(source_path, "package source")
            destination = staging / item["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            content = source_path.read_bytes()
            if hashlib.sha256(content).hexdigest() != item["sha256"] or len(content) != item["size"]:
                raise LifecycleError("package source changed while being staged")
            with destination.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(destination, 0o444)
        actual = package_inventory(staging, "staged immutable package")
        if actual != source_files or candidate_identity(actual) != candidate:
            raise LifecycleError("staged immutable package identity mismatch")
        for directory in sorted((path for path in staging.rglob("*") if path.is_dir()), reverse=True):
            os.chmod(directory, 0o555)
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        os.chmod(staging, 0o555)
        descriptor = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(staging, target)
        descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return candidate, source_files, True
    finally:
        if staging.exists():
            for path in sorted(staging.rglob("*"), reverse=True):
                if not path.is_symlink():
                    os.chmod(path, 0o700)
            shutil.rmtree(staging, ignore_errors=True)


def remove_created_package(lifecycle_id: str, candidate: str) -> None:
    target = package_path(lifecycle_id, candidate)
    if not target.exists() or target.is_symlink():
        return
    for path in sorted(target.rglob("*"), reverse=True):
        if not path.is_symlink():
            os.chmod(path, 0o700)
    os.chmod(target, 0o700)
    shutil.rmtree(target)


def validate_record(value: Any, verify_packages: bool = True) -> dict[str, Any]:
    record = require_exact_keys(value, RECORD_KEYS, "candidate record")
    if record["schema_version"] == LEGACY_SCHEMA_VERSION:
        # Legacy v1 evidence remains inspectable but cannot grant current recurrence.
        return record
    if record["schema_version"] != SCHEMA_VERSION:
        raise LifecycleError("candidate record.schema_version must be 2")
    lifecycle_id = require_uuid(record["lifecycle_id"], "candidate record.lifecycle_id")
    if record["state"] not in STATES:
        raise LifecycleError("candidate record.state is unknown")
    if record["authority"] not in {"autonomous", "user_authorized"}:
        raise LifecycleError("candidate record.authority is invalid")
    if not isinstance(record["proposed_name"], str) or not NAME_RE.fullmatch(record["proposed_name"]):
        raise LifecycleError("candidate record.proposed_name is invalid")
    procedure = validate_procedure(record["procedure"])

    evidence = record["evidence"]
    if not isinstance(evidence, list):
        raise LifecycleError("candidate record.evidence must be a list")
    evidence_ids: set[str] = set()
    for index, item in enumerate(evidence):
        item_keys = set(item) if isinstance(item, dict) else set()
        legacy_evidence_keys = {
                "task_key",
                "session_id",
                "observed_at",
                "independence",
                "summary",
                "procedure_fingerprint",
                "evidence_id",
            }
        if (
            not isinstance(item, dict)
            or (
                item_keys != EVIDENCE_KEYS
                and item_keys != legacy_evidence_keys
            )
        ):
            raise LifecycleError(
                f"candidate record.evidence[{index}] has an unknown shape"
            )
        payload = {
            key: item[key] for key in item if key != "evidence_id"
        }
        validated = validate_observation(payload, procedure)
        if (
            item != validated
            or item["evidence_id"] in evidence_ids
        ):
            raise LifecycleError(f"candidate record.evidence[{index}] has an invalid identity")
        evidence_ids.add(item["evidence_id"])

    revisions = record["candidate_revisions"]
    if not isinstance(revisions, list) or not revisions:
        raise LifecycleError("candidate record.candidate_revisions must be a non-empty list")
    candidate_ids: set[str] = set()
    for index, revision in enumerate(revisions):
        revision = require_exact_keys(revision, REVISION_KEYS, f"candidate record.candidate_revisions[{index}]")
        candidate = require_sha256(revision["candidate_id"], f"candidate record.candidate_revisions[{index}].candidate_id")
        if candidate in candidate_ids:
            raise LifecycleError("candidate record.candidate_revisions cannot repeat a candidate_id")
        candidate_ids.add(candidate)
        validate_files(revision["files"], f"candidate record.candidate_revisions[{index}].files")
        parse_time(revision["staged_at"], f"candidate record.candidate_revisions[{index}].staged_at")
        if verify_packages:
            verify_package(lifecycle_id, revision, record["proposed_name"])
    if record["current_candidate_id"] not in candidate_ids:
        raise LifecycleError("candidate record.current_candidate_id must name a staged revision")

    evaluation = require_exact_keys(record["evaluation"], EVALUATION_KEYS, "candidate record.evaluation")
    if evaluation["status"] not in {"not_evaluated", "shadow_ready", "not_ready"}:
        raise LifecycleError("candidate record.evaluation.status is invalid")
    if evaluation["last_evaluated_at"] is not None:
        parse_time(evaluation["last_evaluated_at"], "candidate record.evaluation.last_evaluated_at")
    if not isinstance(evaluation["history"], list):
        raise LifecycleError("candidate record.evaluation.history must be a list")
    for index, entry in enumerate(evaluation["history"]):
        entry = require_exact_keys(entry, EVALUATION_HISTORY_KEYS, f"candidate record.evaluation.history[{index}]")
        require_sha256(entry["evaluation_id"], f"candidate record.evaluation.history[{index}].evaluation_id")
        parse_time(entry["evaluated_at"], f"candidate record.evaluation.history[{index}].evaluated_at")
        if entry["recommendation"] not in {"ready_for_draft", "collecting"}:
            raise LifecycleError("candidate record evaluation recommendation is invalid")
        if not isinstance(entry["reasons"], list) or not all(isinstance(reason, str) for reason in entry["reasons"]):
            raise LifecycleError("candidate record evaluation reasons are invalid")
        if entry["candidate_id"] not in candidate_ids or entry["shadow_only"] is not True:
            raise LifecycleError("candidate record evaluation is not bound to a shadow candidate")

    publication = record["publication"]
    if publication != {"status": "shadow_only"}:
        raise LifecycleError("candidate record.publication must remain shadow_only")

    lifecycle = require_exact_keys(record["lifecycle"], LIFECYCLE_KEYS, "candidate record.lifecycle")
    parse_time(lifecycle["created_at"], "candidate record.lifecycle.created_at")
    parse_time(lifecycle["last_supported_at"], "candidate record.lifecycle.last_supported_at")
    parse_time(lifecycle["expires_at"], "candidate record.lifecycle.expires_at")
    if not isinstance(lifecycle["transition_history"], list) or not lifecycle["transition_history"]:
        raise LifecycleError("candidate record.lifecycle.transition_history must be non-empty")
    prior_state: str | None = None
    for index, transition in enumerate(lifecycle["transition_history"]):
        transition = require_exact_keys(transition, TRANSITION_KEYS, f"candidate record.lifecycle.transition_history[{index}]")
        require_sha256(transition["transition_id"], f"candidate record.lifecycle.transition_history[{index}].transition_id")
        if transition["from_state"] is not None and transition["from_state"] not in STATES:
            raise LifecycleError("candidate record transition has an unknown prior state")
        if transition["to_state"] not in STATES:
            raise LifecycleError("candidate record transition has an unknown next state")
        if transition["from_state"] != prior_state:
            raise LifecycleError("candidate record transition history is discontinuous")
        if prior_state is None:
            if transition["to_state"] not in {"collecting", "legacy_probation"}:
                raise LifecycleError("candidate record has an illegal initial state")
        elif transition["to_state"] not in DECLARED_TRANSITIONS[prior_state]:
            raise LifecycleError("candidate record transition history contains an illegal transition")
        parse_time(transition["at"], f"candidate record.lifecycle.transition_history[{index}].at")
        require_reason(transition["reason"], f"candidate record.lifecycle.transition_history[{index}].reason")
        if not isinstance(transition["authorizing_evidence_ids"], list) or not all(
            item in evidence_ids for item in transition["authorizing_evidence_ids"]
        ):
            raise LifecycleError("candidate record transition evidence is invalid")
        if not isinstance(transition["receipt_ids"], list) or not all(
            SHA256_RE.fullmatch(item or "") for item in transition["receipt_ids"]
        ):
            raise LifecycleError("candidate record transition receipts are invalid")
        prior_state = transition["to_state"]
    if prior_state != record["state"]:
        raise LifecycleError("candidate record.state does not match its transition history")

    if not isinstance(record["aliases"], list):
        raise LifecycleError("candidate record.aliases must be a list")
    aliases: set[tuple[str, str]] = set()
    for index, alias in enumerate(record["aliases"]):
        alias = require_exact_keys(alias, {"namespace", "value"}, f"candidate record.aliases[{index}]")
        item = (require_text(alias["namespace"], "alias.namespace", 128), require_text(alias["value"], "alias.value", 512))
        if item in aliases:
            raise LifecycleError("candidate record.aliases cannot repeat an alias")
        aliases.add(item)
    if record["absorbed_into"] is not None:
        require_uuid(record["absorbed_into"], "candidate record.absorbed_into")
    if record["state"] == "absorbed" and record["absorbed_into"] is None:
        raise LifecycleError("absorbed candidate record requires absorbed_into")
    if record["state"] != "absorbed" and record["absorbed_into"] is not None:
        raise LifecycleError("only an absorbed candidate may have absorbed_into")

    decisions = record["match_decisions"]
    if not isinstance(decisions, list):
        raise LifecycleError("candidate record.match_decisions must be a list")
    decision_ids: set[str] = set()
    for index, decision in enumerate(decisions):
        decision = require_exact_keys(decision, DECISION_KEYS, f"candidate record.match_decisions[{index}]")
        require_sha256(decision["decision_id"], f"candidate record.match_decisions[{index}].decision_id")
        if decision["decision_id"] in decision_ids:
            raise LifecycleError("candidate record.match_decisions cannot repeat an identity")
        decision_ids.add(decision["decision_id"])
        parse_time(decision["at"], f"candidate record.match_decisions[{index}].at")
        if decision["outcome"] not in MATCH_OUTCOMES:
            raise LifecycleError("candidate record match decision outcome is invalid")
        require_reason(decision["reason"], f"candidate record.match_decisions[{index}].reason")
        if decision["related_lifecycle_id"] is not None:
            require_uuid(decision["related_lifecycle_id"], "candidate record match decision related_lifecycle_id")
        if not isinstance(decision["evidence_ids"], list) or not all(item in evidence_ids for item in decision["evidence_ids"]):
            raise LifecycleError("candidate record match decision evidence is invalid")
        if decision["shadow_only"] is not True:
            raise LifecycleError("candidate record match decisions must be shadow_only")

    blockers = require_exact_keys(record["blockers"], BLOCKER_KEYS, "candidate record.blockers")
    if not isinstance(blockers["covering_lifecycle_ids"], list):
        raise LifecycleError("candidate record blocker covering_lifecycle_ids are invalid")
    for item in blockers["covering_lifecycle_ids"]:
        require_uuid(item, "candidate record blocker covering_lifecycle_id")
    if len(set(blockers["covering_lifecycle_ids"])) != len(blockers["covering_lifecycle_ids"]):
        raise LifecycleError("candidate record blocker covering_lifecycle_ids cannot repeat")
    if not isinstance(blockers["tombstone_ids"], list) or not all(
        isinstance(item, str) and item for item in blockers["tombstone_ids"]
    ):
        raise LifecycleError("candidate record blocker tombstone_ids are invalid")
    if len(set(blockers["tombstone_ids"])) != len(blockers["tombstone_ids"]):
        raise LifecycleError("candidate record blocker tombstone_ids cannot repeat")
    if not isinstance(blockers["uncertain"], bool):
        raise LifecycleError("candidate record blocker uncertain is invalid")

    if not isinstance(record["record_version"], int) or record["record_version"] < 1:
        raise LifecycleError("candidate record.record_version must be a positive integer")
    return record


def record_path(lifecycle_id: str) -> Path:
    return records_root() / f"{require_uuid(lifecycle_id, 'lifecycle_id')}.json"


def load_record(lifecycle_id: str) -> dict[str, Any]:
    path = record_path(lifecycle_id)
    return validate_record(read_json(path, "candidate record"))


def record_identity(record: dict[str, Any]) -> str:
    return sha256(canonical(record))


def assert_expected(record: dict[str, Any], args: argparse.Namespace) -> None:
    expected = getattr(args, "expected_version", None)
    if expected is None:
        raise LifecycleError("--expected-version is required for an existing lifecycle record")
    if expected != record["record_version"]:
        raise LifecycleError("stale record version")
    expected_hash = getattr(args, "expected_record_sha256", None)
    if expected_hash is not None:
        require_sha256(expected_hash, "--expected-record-sha256")
        if expected_hash != record_identity(record):
            raise LifecycleError("stale record identity")


def assert_writer_lease() -> None:
    token = os.environ.get("SKILLS_LOCK_TOKEN")
    if not token:
        raise LifecycleError("SKILLS_LOCK_TOKEN is required for candidate lifecycle mutation")
    lock = Path(__file__).with_name("daemon-lock.py")
    command = [sys.executable, str(lock), "assert", token]
    owner_pid = os.environ.get("SKILLS_LOCK_OWNER_PID")
    owner_identity = os.environ.get("SKILLS_LOCK_OWNER_IDENTITY")
    if owner_pid or owner_identity:
        if not owner_pid or not owner_identity or not owner_pid.isdigit():
            raise LifecycleError("shared writer lease owner identity is incomplete")
        command.extend(
            [
                "--pid",
                owner_pid,
                "--process-identity",
                owner_identity,
            ]
        )
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise LifecycleError("shared writer lease assertion failed")


def transition_entry(
    prior: str | None,
    target: str,
    reason: str,
    evidence_ids: list[str],
    receipt_ids: list[str],
) -> dict[str, Any]:
    payload = {
        "from_state": prior,
        "to_state": target,
        "at": iso(),
        "reason": require_reason(reason),
        "authorizing_evidence_ids": sorted(set(evidence_ids)),
        "receipt_ids": sorted(set(receipt_ids)),
    }
    return {"transition_id": sha256(canonical(payload)), **payload}


def decision_entry(
    outcome: str,
    reason: str,
    related_lifecycle_id: str | None,
    evidence_ids: list[str],
) -> dict[str, Any]:
    if outcome not in MATCH_OUTCOMES:
        raise LifecycleError("match outcome is invalid")
    if related_lifecycle_id is not None:
        require_uuid(related_lifecycle_id, "related lifecycle id")
    payload = {
        "at": iso(),
        "outcome": outcome,
        "reason": require_reason(reason),
        "related_lifecycle_id": related_lifecycle_id,
        "evidence_ids": sorted(set(evidence_ids)),
        "shadow_only": True,
    }
    return {"decision_id": sha256(canonical(payload)), **payload}


def append_transition(
    record: dict[str, Any],
    target: str,
    reason: str,
    evidence_ids: list[str],
    receipt_ids: list[str],
    *,
    internal: bool = False,
    related_lifecycle_id: str | None = None,
) -> None:
    current = record["state"]
    if current not in SHADOW_STATES or target not in SHADOW_STATES:
        raise LifecycleError("production lifecycle states are read-only in the shadow milestone")
    if target not in SHADOW_TRANSITIONS[current]:
        raise LifecycleError(f"illegal lifecycle transition: {current} -> {target}")
    if target == "evaluating":
        if record["current_candidate_id"] is None:
            raise LifecycleError("evaluating requires an exact staged candidate")
    if target == "collecting" and current in {"expired", "rejected"} and not internal:
        raise LifecycleError("reopen requires a fresh verified observation")
    if target == "absorbed":
        if related_lifecycle_id is None:
            raise LifecycleError("absorbed transition requires --related-lifecycle-id")
        record["absorbed_into"] = require_uuid(related_lifecycle_id, "related lifecycle id")
        record["match_decisions"].append(
            decision_entry("absorbs", "absorption-recorded", related_lifecycle_id, evidence_ids)
        )
    record["lifecycle"]["transition_history"].append(
        transition_entry(current, target, reason, evidence_ids, receipt_ids)
    )
    record["state"] = target


def append_decision(
    record: dict[str, Any],
    outcome: str,
    reason: str,
    related_lifecycle_id: str | None,
    evidence_ids: list[str],
) -> None:
    if record["state"] not in SHADOW_STATES:
        raise LifecycleError("production lifecycle states are read-only in the shadow milestone")
    if outcome in {"duplicate", "supersedes", "absorbs"} and related_lifecycle_id is None:
        raise LifecycleError(f"{outcome} requires --related-lifecycle-id")
    decision = decision_entry(outcome, reason, related_lifecycle_id, evidence_ids)
    record["match_decisions"].append(decision)
    if outcome == "uncertain":
        record["blockers"]["uncertain"] = True
    if outcome in {"duplicate", "supersedes"}:
        if related_lifecycle_id not in record["blockers"]["covering_lifecycle_ids"]:
            record["blockers"]["covering_lifecycle_ids"].append(related_lifecycle_id)


def fresh(point: datetime, now: datetime | None = None) -> bool:
    current = now or now_time()
    return point <= current and current - point <= 30 * DAY


def recurrence(record: dict[str, Any]) -> dict[str, Any]:
    if record["schema_version"] != SCHEMA_VERSION:
        return {"ready": False, "reasons": ["legacy-no-current-occurrence-authority"], "evidence_ids": [], "verified_evidence_count": 0, "distinct_occurrence_count": 0}
    decision_at = now_time()
    qualified = [
        item
        for item in record["evidence"]
        if "canonical_occurrence_id" in item
        and parse_time(item["occurred_at"], "evidence.occurred_at")
        <= decision_at
        and decision_at
        - parse_time(item["occurred_at"], "evidence.occurred_at")
        <= 30 * DAY
    ]
    occurrences = {item["canonical_occurrence_id"] for item in qualified}
    reasons: list[str] = []
    if len(occurrences) < 3:
        reasons.append("fewer-than-three-current-distinct-occurrences")
    blockers = record["blockers"]
    if blockers["uncertain"]:
        reasons.append("uncertain-match-blocker")
    if blockers["covering_lifecycle_ids"]:
        reasons.append("covering-lifecycle-blocker")
    if blockers["tombstone_ids"]:
        reasons.append("tombstone-blocker")
    return {"ready": not reasons, "reasons": reasons, "evidence_ids": [item["evidence_id"] for item in qualified], "verified_evidence_count": len(qualified), "distinct_occurrence_count": len(occurrences)}


def append_evaluation(record: dict[str, Any], decision: dict[str, Any]) -> None:
    recommendation = "ready_for_draft" if decision["ready"] else "collecting"
    payload = {
        "evaluated_at": iso(),
        "recommendation": recommendation,
        "reasons": decision["reasons"],
        "candidate_id": record["current_candidate_id"],
        "shadow_only": True,
    }
    item = {"evaluation_id": sha256(canonical(payload)), **payload}
    record["evaluation"]["history"].append(item)
    record["evaluation"]["last_evaluated_at"] = payload["evaluated_at"]
    record["evaluation"]["status"] = "shadow_ready" if decision["ready"] else "not_ready"


def persist(record: dict[str, Any]) -> dict[str, Any]:
    record["record_version"] += 1
    validate_record(record)
    atomic_json(record_path(record["lifecycle_id"]), record)
    return record


def result(record: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "candidate_id": record["current_candidate_id"],
        "lifecycle_id": record["lifecycle_id"],
        "record_sha256": record_identity(record),
        "record_version": record["record_version"],
        "shadow_only": True,
        "state": record["state"],
        **extra,
    }


def new_record(
    lifecycle_id: str,
    proposed_name: str,
    procedure: dict[str, Any],
    evidence: dict[str, Any],
    candidate: str,
    files: list[dict[str, Any]],
    match_outcome: str,
    reason: str,
    related_lifecycle_id: str | None,
    covering: list[str],
    tombstones: list[str],
) -> dict[str, Any]:
    if not NAME_RE.fullmatch(proposed_name):
        raise LifecycleError("--proposed-name is invalid")
    require_reason(reason)
    lifecycle_id = require_uuid(lifecycle_id, "lifecycle_id")
    observed = parse_time(evidence.get("occurred_at", evidence.get("observed_at")), "observation.observed_at")
    record = {
        "schema_version": LEGACY_SCHEMA_VERSION if "observed_at" in evidence else SCHEMA_VERSION,
        "lifecycle_id": lifecycle_id,
        "state": "collecting",
        "authority": "autonomous",
        "proposed_name": proposed_name,
        "procedure": procedure,
        "evidence": [evidence],
        "candidate_revisions": [
            {
                "candidate_id": candidate,
                "package_path": package_reference(lifecycle_id, candidate),
                "files": files,
                "staged_at": iso(),
            }
        ],
        "current_candidate_id": candidate,
        "evaluation": {"status": "not_evaluated", "last_evaluated_at": None, "history": []},
        "publication": {"status": "shadow_only"},
        "lifecycle": {
            "created_at": iso(),
            "last_supported_at": evidence.get("occurred_at", evidence.get("observed_at")),
            "expires_at": iso(observed + 30 * DAY),
            "transition_history": [
                transition_entry(None, "collecting", "candidate-collected", [evidence["evidence_id"]], [])
            ],
        },
        "aliases": [],
        "absorbed_into": None,
        "match_decisions": [],
        "blockers": {
            "covering_lifecycle_ids": sorted(set(covering)),
            "tombstone_ids": sorted(set(tombstones)),
            "uncertain": match_outcome == "uncertain",
        },
        "record_version": 0,
    }
    for item in record["blockers"]["covering_lifecycle_ids"]:
        require_uuid(item, "covering lifecycle id")
    for item in record["blockers"]["tombstone_ids"]:
        require_text(item, "tombstone id", 512)
    append_decision(record, match_outcome, reason, related_lifecycle_id, [evidence["evidence_id"]])
    return record


def collect(args: argparse.Namespace) -> dict[str, Any]:
    assert_writer_lease()
    procedure = validate_procedure(read_json_object(args.procedure, "procedure"))
    evidence = validate_observation(read_json_object(args.observation, "observation"), procedure)
    source = Path(args.package)
    existing: dict[str, Any] | None = None
    lifecycle_id = args.lifecycle_id
    if lifecycle_id is not None:
        existing = load_record(lifecycle_id)
        assert_expected(existing, args)
        if existing["procedure"] != procedure:
            raise LifecycleError("same-procedure evidence must use the exact stored procedure descriptor")
        if existing["state"] not in {"collecting", "ready_for_draft", "expired", "rejected"}:
            raise LifecycleError("evidence cannot be collected for this lifecycle state")
        if evidence["evidence_id"] in {item["evidence_id"] for item in existing["evidence"]}:
            return result(existing, changed=False, match_outcome=args.match_outcome or "same")
    else:
        lifecycle_id = str(uuid.uuid4())

    outcome = args.match_outcome or ("same" if existing else "different")
    if outcome not in MATCH_OUTCOMES:
        raise LifecycleError("--match-outcome is invalid")
    related = args.related_lifecycle_id
    if related is not None:
        require_uuid(related, "--related-lifecycle-id")
    reason = args.reason or ("same-procedure-observed" if existing else "new-procedure-observed")
    require_reason(reason)
    covering = args.covering_lifecycle_id or []
    tombstones = args.tombstone_id or []
    for item in covering:
        require_uuid(item, "--covering-lifecycle-id")
    for item in tombstones:
        require_text(item, "--tombstone-id", 512)
    if existing and (covering or tombstones):
        raise LifecycleError("matching blockers can only be declared while creating a lifecycle")
    candidate, files, created = make_immutable_package(lifecycle_id, source, args.proposed_name)
    try:
        if existing is None:
            record = new_record(
                lifecycle_id,
                args.proposed_name,
                procedure,
                evidence,
                candidate,
                files,
                outcome,
                reason,
                related,
                covering,
                tombstones,
            )
        else:
            record = copy.deepcopy(existing)
            record["evidence"].append(evidence)
            if candidate not in {item["candidate_id"] for item in record["candidate_revisions"]}:
                record["candidate_revisions"].append(
                    {
                        "candidate_id": candidate,
                        "package_path": package_reference(lifecycle_id, candidate),
                        "files": files,
                        "staged_at": iso(),
                    }
                )
            record["current_candidate_id"] = candidate
            supported = parse_time(record["lifecycle"]["last_supported_at"], "last supported")
            observed_value = evidence.get("occurred_at", evidence.get("observed_at"))
            observed = parse_time(observed_value, "observation.observed_at")
            if observed > supported:
                record["lifecycle"]["last_supported_at"] = observed_value
                record["lifecycle"]["expires_at"] = iso(observed + 30 * DAY)
            append_decision(record, outcome, reason, related, [evidence["evidence_id"]])
            if existing["state"] == "expired" and fresh(observed):
                append_transition(
                    record,
                    "collecting",
                    "fresh-evidence-reopened",
                    [evidence["evidence_id"]],
                    [],
                    internal=True,
                )
        record = persist(record)
    except Exception:
        if created:
            remove_created_package(lifecycle_id, candidate)
        raise
    return result(record, changed=True, match_outcome=outcome)


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    assert_writer_lease()
    record = copy.deepcopy(load_record(args.lifecycle_id))
    assert_expected(record, args)
    if record["state"] not in {"collecting", "ready_for_draft"}:
        raise LifecycleError("recurrence can only be evaluated while collecting or ready_for_draft")
    decision = recurrence(record)
    append_evaluation(record, decision)
    if decision["ready"] and record["state"] == "collecting":
        append_transition(
            record,
            "ready_for_draft",
            "recurrence-qualified",
            decision["evidence_ids"],
            [],
        )
    record = persist(record)
    return result(record, recommendation="ready_for_draft" if decision["ready"] else "collecting", recurrence=decision)


def revise(args: argparse.Namespace) -> dict[str, Any]:
    assert_writer_lease()
    record = copy.deepcopy(load_record(args.lifecycle_id))
    assert_expected(record, args)
    if record["state"] not in {"collecting", "ready_for_draft", "evaluating"}:
        raise LifecycleError("candidate revision requires a collecting, ready, or evaluating lifecycle")
    candidate, files, created = make_immutable_package(
        args.lifecycle_id,
        Path(args.package),
        record["proposed_name"],
    )
    if candidate == record["current_candidate_id"]:
        return result(record, changed=False)
    try:
        if candidate not in {item["candidate_id"] for item in record["candidate_revisions"]}:
            record["candidate_revisions"].append(
                {
                    "candidate_id": candidate,
                    "package_path": package_reference(args.lifecycle_id, candidate),
                    "files": files,
                    "staged_at": iso(),
                }
            )
        record["current_candidate_id"] = candidate
        record["evaluation"]["status"] = "not_evaluated"
        record["evaluation"]["last_evaluated_at"] = None
        if record["state"] == "evaluating":
            append_transition(
                record,
                "ready_for_draft",
                "candidate-revised",
                [
                    item["evidence_id"]
                    for item in record["evidence"]
                    if True
                ],
                [],
                internal=True,
            )
        record = persist(record)
    except Exception:
        if created:
            remove_created_package(args.lifecycle_id, candidate)
        raise
    return result(record, changed=True)


def transition(args: argparse.Namespace) -> dict[str, Any]:
    assert_writer_lease()
    record = copy.deepcopy(load_record(args.lifecycle_id))
    assert_expected(record, args)
    target = args.to
    if target not in STATES:
        raise LifecycleError("--to names an unknown lifecycle state")
    evidence_ids = args.evidence_id or []
    current_evidence = {item["evidence_id"] for item in record["evidence"]}
    if not all(item in current_evidence for item in evidence_ids):
        raise LifecycleError("--evidence-id must name retained evidence")
    receipt_ids = args.receipt_id or []
    for item in receipt_ids:
        require_sha256(item, "--receipt-id")
    if target == "collecting" and record["state"] in {"expired", "rejected"}:
        raise LifecycleError("use reopen with a fresh verified evidence identity")
    if target == "evaluating":
        if args.candidate_id != record["current_candidate_id"]:
            raise LifecycleError("evaluating must name the current exact candidate_id")
        history = record["evaluation"]["history"]
        if (
            record["evaluation"]["status"] != "shadow_ready"
            or not history
            or history[-1]["candidate_id"] != record["current_candidate_id"]
            or history[-1]["recommendation"] != "ready_for_draft"
        ):
            raise LifecycleError(
                "evaluating requires a shadow-ready recommendation for the current candidate"
            )
    append_transition(
        record,
        target,
        args.reason,
        evidence_ids,
        receipt_ids,
        related_lifecycle_id=args.related_lifecycle_id,
    )
    record = persist(record)
    return result(record)


def reopen(args: argparse.Namespace) -> dict[str, Any]:
    assert_writer_lease()
    record = copy.deepcopy(load_record(args.lifecycle_id))
    assert_expected(record, args)
    if record["state"] not in {"expired", "rejected"}:
        raise LifecycleError("only expired or rejected candidates can reopen")
    evidence = next((item for item in record["evidence"] if item["evidence_id"] == args.evidence_id), None)
    if evidence is None or not fresh(
        parse_time(evidence["occurred_at"], "evidence.observed_at")
    ):
        raise LifecycleError("reopen requires a retained fresh verified evidence identity")
    append_transition(
        record,
        "collecting",
        args.reason,
        [args.evidence_id],
        [],
        internal=True,
    )
    record = persist(record)
    return result(record)


def expire(args: argparse.Namespace) -> dict[str, Any]:
    assert_writer_lease()
    record = copy.deepcopy(load_record(args.lifecycle_id))
    assert_expected(record, args)
    if record["state"] != "collecting":
        raise LifecycleError("only collecting candidates can expire")
    last_supported = parse_time(record["lifecycle"]["last_supported_at"], "last supported")
    if now_time() - last_supported <= 30 * DAY:
        return result(record, changed=False, expiration="not_due")
    supporting = [
        item["evidence_id"]
        for item in record["evidence"]
        if parse_time(item["occurred_at"], "evidence.occurred_at") == last_supported
    ]
    append_transition(record, "expired", "recurrence-expired", supporting, [])
    record = persist(record)
    return result(record, changed=True, expiration="expired")


def decide(args: argparse.Namespace) -> dict[str, Any]:
    assert_writer_lease()
    record = copy.deepcopy(load_record(args.lifecycle_id))
    assert_expected(record, args)
    evidence_ids = args.evidence_id or []
    known = {item["evidence_id"] for item in record["evidence"]}
    if not all(item in known for item in evidence_ids):
        raise LifecycleError("--evidence-id must name retained evidence")
    append_decision(record, args.outcome, args.reason, args.related_lifecycle_id, evidence_ids)
    record = persist(record)
    return result(record, match_outcome=args.outcome)


def read(args: argparse.Namespace) -> dict[str, Any]:
    return load_record(args.lifecycle_id)


def list_records(args: argparse.Namespace) -> dict[str, Any]:
    root = records_root()
    if not root.exists():
        return {"records": [], "shadow_only": True}
    reject_symlink(root, "records directory")
    records = []
    for path in sorted(root.glob("*.json")):
        record = validate_record(read_json(path, "candidate record"))
        records.append(
            {
                "candidate_id": record["current_candidate_id"],
                "lifecycle_id": record["lifecycle_id"],
                "record_sha256": record_identity(record),
                "record_version": record["record_version"],
                "shadow_only": True,
                "state": record["state"],
            }
        )
    return {"records": records, "shadow_only": True}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    if args.lifecycle_id:
        record = load_record(args.lifecycle_id)
        return result(record, valid=True)
    records = list_records(args)["records"]
    return {"record_count": len(records), "shadow_only": True, "valid": True}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    collect_parser = sub.add_parser("collect", help="collect one observation and an immutable draft package")
    collect_parser.add_argument("--procedure", required=True, help="JSON procedure descriptor")
    collect_parser.add_argument("--observation", required=True, help="JSON observation")
    collect_parser.add_argument("--package", required=True, help="draft package directory")
    collect_parser.add_argument("--proposed-name", required=True)
    collect_parser.add_argument("--lifecycle-id")
    collect_parser.add_argument("--match-outcome", choices=sorted(MATCH_OUTCOMES))
    collect_parser.add_argument("--reason")
    collect_parser.add_argument("--related-lifecycle-id")
    collect_parser.add_argument("--covering-lifecycle-id", action="append")
    collect_parser.add_argument("--tombstone-id", action="append")
    collect_parser.add_argument("--expected-version", type=int)
    collect_parser.add_argument("--expected-record-sha256")
    collect_parser.set_defaults(func=collect)

    evaluate_parser = sub.add_parser("evaluate", help="record a shadow recurrence recommendation")
    evaluate_parser.add_argument("lifecycle_id")
    evaluate_parser.add_argument("--expected-version", type=int, required=True)
    evaluate_parser.add_argument("--expected-record-sha256")
    evaluate_parser.set_defaults(func=evaluate)

    revise_parser = sub.add_parser(
        "revise",
        help="stage an immutable successor package without inventing new evidence",
    )
    revise_parser.add_argument("lifecycle_id")
    revise_parser.add_argument("--package", required=True, help="successor draft package directory")
    revise_parser.add_argument("--expected-version", type=int, required=True)
    revise_parser.add_argument("--expected-record-sha256")
    revise_parser.set_defaults(func=revise)

    transition_parser = sub.add_parser("transition", help="perform a declared shadow lifecycle transition")
    transition_parser.add_argument("lifecycle_id")
    transition_parser.add_argument("--to", required=True)
    transition_parser.add_argument("--reason", required=True)
    transition_parser.add_argument("--candidate-id")
    transition_parser.add_argument("--related-lifecycle-id")
    transition_parser.add_argument("--evidence-id", action="append")
    transition_parser.add_argument("--receipt-id", action="append")
    transition_parser.add_argument("--expected-version", type=int, required=True)
    transition_parser.add_argument("--expected-record-sha256")
    transition_parser.set_defaults(func=transition)

    reopen_parser = sub.add_parser("reopen", help="reopen with retained fresh verified evidence")
    reopen_parser.add_argument("lifecycle_id")
    reopen_parser.add_argument("--evidence-id", required=True)
    reopen_parser.add_argument("--reason", default="fresh-evidence-reopened")
    reopen_parser.add_argument("--expected-version", type=int, required=True)
    reopen_parser.add_argument("--expected-record-sha256")
    reopen_parser.set_defaults(func=reopen)

    expire_parser = sub.add_parser("expire", help="expire an unsupported collecting candidate")
    expire_parser.add_argument("lifecycle_id")
    expire_parser.add_argument("--expected-version", type=int, required=True)
    expire_parser.add_argument("--expected-record-sha256")
    expire_parser.set_defaults(func=expire)

    decide_parser = sub.add_parser("decide", help="append a shadow matching decision")
    decide_parser.add_argument("lifecycle_id")
    decide_parser.add_argument("--outcome", choices=sorted(MATCH_OUTCOMES), required=True)
    decide_parser.add_argument("--reason", required=True)
    decide_parser.add_argument("--related-lifecycle-id")
    decide_parser.add_argument("--evidence-id", action="append")
    decide_parser.add_argument("--expected-version", type=int, required=True)
    decide_parser.add_argument("--expected-record-sha256")
    decide_parser.set_defaults(func=decide)

    read_parser = sub.add_parser("read", help="read and validate a lifecycle record")
    read_parser.add_argument("lifecycle_id")
    read_parser.set_defaults(func=read)
    list_parser = sub.add_parser("list", help="list validated lifecycle records")
    list_parser.set_defaults(func=list_records)
    validate_parser = sub.add_parser("validate", help="validate one record or all records")
    validate_parser.add_argument("lifecycle_id", nargs="?")
    validate_parser.set_defaults(func=validate)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        response = args.func(args)
        print(json.dumps(response, sort_keys=True))
        return 0
    except (LifecycleError, OSError, subprocess.SubprocessError) as error:
        print(f"candidate-lifecycle: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
