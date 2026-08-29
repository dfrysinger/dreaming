"""Deterministic preparation for bounded shadow evaluation of a routed candidate.

Everything here runs *before* any lifecycle claim, writes only into a pass-local
scratch root, and refuses rather than guessing.  It selects the one conflict
target from owner-recomputed skill-load evidence, materializes the candidate
package and the one-skill approved target catalog out of the content-addressed
estate census, and assembles the authority facts that
``profile_evaluation_routing.derive_execution_authority`` turns into one routing
row.  No function here transitions a lifecycle record, executes a trial, or
writes a durable evaluation record.

See docs/task-opportunity-shadow-evaluation-reframe.md, sections E2c and E3.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, NoReturn

SHADOW_PREPARATION_CONTRACT_VERSION = 1
SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
SKILL_NAME_RE = re.compile(r"[a-z][a-z0-9-]{2,63}")
# Every bound the reservation names.  No value has a default: an unconfigured
# term is an absent authority, not a number to guess.
REQUIRED_ALLOWANCE_TERMS = frozenset(
    {
        "max_evaluations_per_run",
        "stage_seconds",
        "author_call_bound",
        "author_doctor_bound",
        "executor_call_bound",
        "compile_bound",
        "certify_bound",
        "lifecycle_transition_bound",
        "record_write_bound",
        "lifecycle_read_bound",
        "packet_build_bound",
        "packet_validate_bound",
        "package_file_ceiling",
        "package_bytes_ceiling",
        "catalog_file_ceiling",
        "catalog_bytes_ceiling",
        "prepare_throughput",
        "hash_throughput",
        "termination_grace",
        "deadline_margin",
    }
)
ALLOWANCE_CONFIG_KEY = "shadow_evaluation"


class ShadowPreparationError(RuntimeError):
    """A pre-claim refusal carrying one stable reason code."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}{': ' + detail if detail else ''}")
        self.code = code
        self.detail = detail


def _refuse(code: str, detail: str = "") -> NoReturn:
    raise ShadowPreparationError(code, detail)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def candidate_package_identity(files: list[dict[str, Any]]) -> str:
    """Reproduce the candidate-lifecycle immutable package identity exactly."""
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def bounded_call(
    argv: Iterable[str],
    *,
    timeout: int,
    termination_grace: int,
    code: str,
    environment: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one child in its own process group under an enforced deadline.

    Stateless by construction: it observes no halt, interrupts nothing
    mid-call, and keeps no state.  It exists only so that no term of the
    reservation can be exceeded by an unbounded child, including a tool that
    shells to a nested subprocess of its own.
    """
    if timeout < 1 or termination_grace < 1:
        _refuse("shadow-allowance-unconfigured", code)
    try:
        process = subprocess.Popen(
            [str(item) for item in argv],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=environment,
            cwd=str(cwd) if cwd else None,
        )
    except OSError as error:
        _refuse(code, str(error))
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=termination_grace)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        _refuse(code, str(error))
    return subprocess.CompletedProcess(
        list(argv), process.returncode, stdout, stderr
    )


def allowance_authority(config: Any) -> tuple[bool, dict[str, int]]:
    """Read every reservation term from explicit owner configuration.

    Returns ``(False, {})`` when any required term is missing or unusable.  No
    term is ever defaulted, because a silently defaulted number would turn the
    reservation into an estimate rather than a declared policy.
    """
    entry = config.get(ALLOWANCE_CONFIG_KEY) if isinstance(config, dict) else None
    if not isinstance(entry, dict) or set(entry) != REQUIRED_ALLOWANCE_TERMS:
        return False, {}
    values: dict[str, int] = {}
    for key in sorted(REQUIRED_ALLOWANCE_TERMS):
        value = entry[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return False, {}
        values[key] = value
    return True, values


def census_authority(census: Any) -> bool:
    """A current readable census whose content address verifies."""
    if not isinstance(census, dict):
        return False
    claimed = census.get("snapshot_sha256")
    if not isinstance(claimed, str) or not SHA256_RE.fullmatch(claimed):
        return False
    body = {key: value for key, value in census.items() if key != "snapshot_sha256"}
    if digest(body) != claimed:
        return False
    return isinstance(census.get("physical_instances"), list)


def _trace_entries(traces: Any) -> list[dict[str, Any]]:
    """Concatenate owner-recomputed traces in canonical occurrence order."""
    if not isinstance(traces, list) or not traces:
        _refuse("shadow-conflict-target-unavailable", "no authorizing trace")
    ordered: list[tuple[str, list[dict[str, Any]]]] = []
    for item in traces:
        if not isinstance(item, dict) or set(item) != {
            "canonical_occurrence_id",
            "skill_load_trace",
        }:
            _refuse("shadow-conflict-target-unavailable", "malformed trace authority")
        occurrence = item["canonical_occurrence_id"]
        trace = item["skill_load_trace"]
        if not isinstance(occurrence, str) or not SHA256_RE.fullmatch(occurrence):
            _refuse("shadow-conflict-target-unavailable", "occurrence identity")
        if not isinstance(trace, list):
            _refuse("shadow-conflict-target-unavailable", "trace shape")
        ordered.append((occurrence, trace))
    if len({occurrence for occurrence, _ in ordered}) != len(ordered):
        _refuse("shadow-conflict-target-unavailable", "duplicate occurrence")
    entries: list[dict[str, Any]] = []
    for _occurrence, trace in sorted(ordered, key=lambda pair: pair[0]):
        for entry in trace:
            if not isinstance(entry, dict) or "catalog_skill_name" not in entry:
                _refuse("shadow-conflict-target-unavailable", "trace entry shape")
            entries.append(entry)
    return entries


def select_conflict_target(traces: Any, census: Any) -> dict[str, Any]:
    """Choose the single approved-target-catalog skill, behaviorally.

    The first catalog skill that actually loaded while the observed task was
    performed and still failed to cover it.  This is a recomputed behavioral
    fact, not a similarity, keyword, or embedding judgement.
    """
    if not census_authority(census):
        _refuse("shadow-catalog-authority-unavailable", "census snapshot")
    instances = [
        item for item in census["physical_instances"] if isinstance(item, dict)
    ]
    named: str | None = None
    for entry in _trace_entries(traces):
        value = entry["catalog_skill_name"]
        if isinstance(value, str) and value:
            named = value
            break
    if named is None:
        _refuse("shadow-conflict-target-unavailable", "no catalog load in trace")
    matched = [item for item in instances if item.get("skill_name") == named]
    if len(matched) != 1:
        _refuse(
            "shadow-conflict-target-ambiguous",
            f"{named} resolves to {len(matched)} census instances",
        )
    instance = matched[0]
    for key in ("absolute_path", "inventory_sha256", "canonical_capability_id"):
        if not isinstance(instance.get(key), str) or not instance[key]:
            _refuse("shadow-conflict-target-ambiguous", f"census instance {key}")
    if not SKILL_NAME_RE.fullmatch(named):
        _refuse("shadow-conflict-target-ambiguous", "skill name is not canonical")
    if not isinstance(instance.get("files"), list) or not instance["files"]:
        _refuse("shadow-conflict-target-ambiguous", "census instance inventory")
    return {
        "skill_name": named,
        "absolute_path": instance["absolute_path"],
        "inventory_sha256": instance["inventory_sha256"],
        "canonical_capability_id": instance["canonical_capability_id"],
        "files": instance["files"],
        "snapshot_sha256": census["snapshot_sha256"],
    }


def _copy_declared_files(
    source: Path,
    destination: Path,
    files: list[dict[str, Any]],
    *,
    file_ceiling: int,
    bytes_ceiling: int,
    code: str,
) -> None:
    if len(files) > file_ceiling:
        _refuse("preparation-oversize", f"{code}: {len(files)} files")
    if source.is_symlink() or not source.is_dir():
        _refuse(code, "source must be a real directory")
    total = 0
    for item in files:
        relative = item.get("path")
        if (
            not isinstance(relative, str)
            or not relative
            or PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts
        ):
            _refuse(code, "declared inventory path is unsafe")
        origin = source / relative
        if origin.is_symlink() or not origin.is_file():
            _refuse(code, f"{relative} is not a regular file")
        content = origin.read_bytes()
        total += len(content)
        if total > bytes_ceiling:
            _refuse("preparation-oversize", f"{code}: {total} bytes")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def materialize_catalog(
    target: dict[str, Any],
    destination: Path,
    *,
    file_ceiling: int,
    bytes_ceiling: int,
) -> dict[str, Any]:
    """Copy the one census instance into scratch and re-hash it to the census."""
    root = destination / target["skill_name"]
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    _copy_declared_files(
        Path(target["absolute_path"]),
        root,
        target["files"],
        file_ceiling=file_ceiling,
        bytes_ceiling=bytes_ceiling,
        code="shadow-catalog-snapshot-stale",
    )
    if not (root / "SKILL.md").is_file():
        _refuse("shadow-catalog-snapshot-stale", "catalog skill has no SKILL.md")
    observed = [
        {"path": path.relative_to(root).as_posix(), "sha256": file_sha256(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    if digest(observed) != target["inventory_sha256"]:
        _refuse(
            "shadow-catalog-snapshot-stale",
            "copied catalog bytes do not re-hash to the census inventory",
        )
    return {
        "catalog_dir": str(destination),
        "skill_name": target["skill_name"],
        "inventory_sha256": target["inventory_sha256"],
        "canonical_capability_id": target["canonical_capability_id"],
        "snapshot_sha256": target["snapshot_sha256"],
        "file_count": len(observed),
    }


def materialize_candidate(
    package_root: Path,
    files: list[dict[str, Any]],
    candidate_id: str,
    destination: Path,
    *,
    file_ceiling: int,
    bytes_ceiling: int,
) -> dict[str, Any]:
    """Copy the immutable candidate package and re-hash it to candidate_id."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    _copy_declared_files(
        package_root,
        destination,
        files,
        file_ceiling=file_ceiling,
        bytes_ceiling=bytes_ceiling,
        code="shadow-candidate-package-unavailable",
    )
    observed = [
        {
            "path": path.relative_to(destination).as_posix(),
            "sha256": file_sha256(path),
            "size": path.stat().st_size,
        }
        for path in sorted(destination.rglob("*"))
        if path.is_file()
    ]
    if candidate_package_identity(observed) != candidate_id:
        _refuse(
            "shadow-candidate-package-tampered",
            "materialized package does not re-hash to the claimed candidate",
        )
    return {
        "package_dir": str(destination),
        "candidate_id": candidate_id,
        "file_count": len(observed),
    }


def authoring_authority(
    *,
    owner_block: Any,
    adapter_resolves: bool,
    doctor_healthy: bool,
    packet_subcommand_available: bool,
) -> bool:
    """Every E2b authoring condition, all explicitly true or nothing."""
    if not isinstance(owner_block, dict):
        return False
    if owner_block.get("enabled") is not True:
        return False
    model = owner_block.get("author_model")
    if not isinstance(model, str) or not model.strip() or model == "default":
        return False
    return bool(adapter_resolves and doctor_healthy and packet_subcommand_available)


def execution_authority_facts(
    *,
    evaluator_configured: bool,
    evaluator_healthy: bool,
    evaluator_attested: bool,
    suite_authority: bool,
    authoring_authority_available: bool,
    catalog_authority_available: bool,
    candidate_package_available: bool,
    allowances_configured: bool,
) -> dict[str, bool]:
    """Assemble exactly the fact set the routing derivation consumes."""
    return {
        "evaluator_configured": bool(evaluator_configured),
        "evaluator_healthy": bool(evaluator_healthy),
        "evaluator_attested": bool(evaluator_attested),
        "suite_authority": bool(suite_authority),
        "authoring_authority": bool(authoring_authority_available),
        "catalog_authority": bool(catalog_authority_available),
        "candidate_package": bool(candidate_package_available),
        "allowances_configured": bool(allowances_configured),
    }
