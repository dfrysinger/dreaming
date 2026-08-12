#!/usr/bin/env python3
"""Transactional run manifest and whole-run rollback for live curation."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
LOCK_TOOL = Path(
    os.environ.get(
        "CURATOR_LOCK_TOOL",
        SCRIPT_DIR.parent.parent / "skill-review/scripts/daemon-lock.py",
    )
)
SCANNER = Path(
    os.environ.get("CURATOR_DEPENDENCY_SCANNER", SCRIPT_DIR / "scheduled-skill-deps.py")
)
EVIDENCE_TOOL = Path(
    os.environ.get(
        "CURATOR_EVIDENCE_TOOL",
        SCRIPT_DIR.parent.parent / "skill-review/scripts/evidence-envelope.py",
    )
)
RESTORE_TOOL = Path(
    os.environ.get(
        "CURATOR_RESTORE_TOOL",
        SCRIPT_DIR.parent.parent / "skill-manage/scripts/restore-skill.sh",
    )
)
TRAILER = os.environ.get(
    "SKILLS_COAUTHOR_TRAILER",
    "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
)
PUBLIC_MANIFESTS = (
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    ".codex-plugin/plugin.json",
)
MAX_REPORT_AGE = timedelta(days=7)
AGE_ONLY_PRUNING_DAYS = 90


class RunError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=text, check=False)
    if check and result.returncode:
        stderr = result.stderr.strip() if text else ""
        raise RunError(f"{' '.join(command)} failed: {stderr}")
    return result


def git(root: Path, *args: str, check: bool = True) -> str:
    return run(["git", "-C", str(root), *args], check=check).stdout.strip()


def roots() -> dict[str, Path]:
    return {
        "public": Path(
            os.environ.get("SKILLS_REPO_ROOT", Path.home() / "code/skills")
        ).resolve(),
        "local": Path(
            os.environ.get("SKILLS_LOCAL_ROOT", Path.home() / ".copilot/skills")
        ).resolve(),
    }


def archive_state_dir() -> Path:
    explicit = os.environ.get("SKILLS_REVIEW_STATE_DIR")
    if explicit:
        return Path(explicit).resolve()
    state = os.environ.get("SKILLS_STATE_DIR")
    if state:
        root = Path(state)
        return (root if root.name == "skill-review" else root / "skill-review").resolve()
    return (Path.home() / ".copilot/skill-state/skill-review").resolve()


def runs_dir() -> Path:
    return Path(
        os.environ.get(
            "SKILLS_CURATOR_RUNS_DIR", archive_state_dir() / "curator-runs"
        )
    ).resolve()


def scratch_dir() -> Path:
    path = Path(
        os.environ.get(
            "DREAMING_SCRATCH_DIR",
            archive_state_dir().parent / "dreaming/scratch",
        )
    ).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def ledger_path() -> Path:
    return archive_state_dir() / "ledger.jsonl"


def manifest_path(run_id: str) -> Path:
    if not re_safe_id(run_id):
        raise RunError(f"invalid run id: {run_id}")
    return runs_dir() / f"{run_id}.json"


def authorization_path(run_id: str) -> Path:
    if not re_safe_id(run_id):
        raise RunError(f"invalid run id: {run_id}")
    return runs_dir() / f"{run_id}.authorization.json"


def re_safe_id(value: str) -> bool:
    return bool(value) and all(ch.isalnum() or ch in "._-" for ch in value)


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def immutable_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    except FileExistsError as error:
        raise RunError(f"authorization receipt already exists: {path}") from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def load_manifest(run_id: str) -> tuple[Path, dict[str, Any]]:
    path = manifest_path(run_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RunError(f"cannot load run manifest {path}: {error}") from error
    if payload.get("run_id") != run_id or payload.get("schema_version") != 1:
        raise RunError(f"invalid run manifest identity: {path}")
    return path, payload


def root_identity(path: Path) -> dict[str, str]:
    if not path.is_dir():
        raise RunError(f"managed root does not exist: {path}")
    top = Path(git(path, "rev-parse", "--show-toplevel")).resolve()
    if top != path:
        raise RunError(f"configured root {path} resolves to git root {top}")
    git_dir_raw = git(path, "rev-parse", "--git-dir")
    git_dir = (path / git_dir_raw).resolve() if not Path(git_dir_raw).is_absolute() else Path(git_dir_raw).resolve()
    return {
        "path": str(path),
        "git_dir": str(git_dir),
        "initial_head": git(path, "rev-parse", "HEAD"),
    }


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_json(payload: Any) -> str:
    return hash_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )


def dirty_paths(root: Path) -> set[str]:
    paths: set[str] = set()
    for args in (
        ("diff", "--name-only", "-z"),
        ("diff", "--cached", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ):
        output = run(["git", "-C", str(root), *args], text=False).stdout
        paths.update(
            item.decode("utf-8", "surrogateescape")
            for item in output.split(b"\0")
            if item
        )
    return paths


def path_fingerprint(root: Path, relative: str) -> dict[str, Any]:
    target = root / relative
    if target.is_symlink():
        worktree = {"type": "symlink", "sha256": hash_bytes(os.readlink(target).encode())}
    elif target.is_file():
        worktree = {"type": "file", "sha256": hash_bytes(target.read_bytes())}
    elif target.is_dir():
        worktree = {"type": "directory"}
    else:
        worktree = {"type": "absent"}
    return {
        "path": relative,
        "worktree": worktree,
        "index": git(root, "ls-files", "--stage", "--", relative, check=False),
        "status": git(root, "status", "--porcelain=v1", "--", relative, check=False),
    }


def dirty_snapshot(root: Path) -> list[dict[str, Any]]:
    return [path_fingerprint(root, path) for path in sorted(dirty_paths(root))]


def overlaps(left: str, right: str) -> bool:
    left_path = Path(left)
    right_path = Path(right)
    return left_path == right_path or left_path in right_path.parents or right_path in left_path.parents


def validate_relative_path(root_name: str, value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or value in {"", "."}:
        raise RunError(f"unsafe operation path: {value}")
    if root_name == "public":
        if len(path.parts) < 2 or path.parts[0] not in {
            "skills",
            ".claude-plugin",
            ".codex-plugin",
        }:
            raise RunError(f"public operation path is outside allowed scopes: {value}")
    elif len(path.parts) < 1:
        raise RunError(f"local operation path is invalid: {value}")
    return path.as_posix()


def scanner_inventory() -> dict[str, Any]:
    result = run([str(SCANNER), "--inventory"], check=False)
    if result.returncode:
        raise RunError(f"scheduled dependency enumeration failed: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RunError(f"scheduled dependency inventory is malformed: {error}") from error
    if payload.get("complete") is not True or not isinstance(payload.get("skills"), list):
        raise RunError("scheduled dependency inventory is incomplete")
    return payload


def curator_state_path() -> Path:
    return Path(
        os.environ.get(
            "SKILLS_CURATOR_STATE_FILE",
            archive_state_dir().parent / "curator.json",
        )
    ).resolve()


def halt_switch_path() -> Path:
    return Path(
        os.environ.get(
            "SKILLS_HALT_SWITCH",
            archive_state_dir() / "disable-daemon",
        )
    ).resolve()


def require_autonomous_switches_open() -> dict[str, Any]:
    halt = halt_switch_path()
    if halt.exists():
        raise RunError(f"autonomous retirement is halted: {halt}")
    state_path = curator_state_path()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RunError(f"cannot verify curator pause state: {error}") from error
    paused = state.get("paused")
    if not isinstance(paused, bool):
        raise RunError("curator pause state is malformed")
    if paused:
        raise RunError("autonomous retirement is paused")
    return {
        "halt_switch": str(halt),
        "curator_state": str(state_path),
        "curator_state_sha256": hash_bytes(state_path.read_bytes()),
        "paused": paused,
    }


def report_scalar(raw: str, field: str) -> str:
    value = raw.strip()
    if not value:
        raise RunError(f"curator report has empty {field}")
    if value[0] in {"'", '"'}:
        if len(value) < 2 or value[-1] != value[0]:
            raise RunError(f"curator report has malformed quoted {field}")
        value = value[1:-1]
    return value


def parse_report_actions(report: Path) -> dict[str, Any]:
    try:
        text = report.read_text(encoding="utf-8")
    except OSError as error:
        raise RunError(f"cannot read curator report: {error}") from error
    blocks: list[str] = []
    in_yaml = False
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == "```yaml":
            in_yaml = True
            current = []
        elif in_yaml and line.strip() == "```":
            blocks.append("\n".join(current))
            in_yaml = False
        elif in_yaml:
            current.append(line)
    if not blocks:
        raise RunError("curator report has no structured YAML block")
    block = blocks[-1]
    sections: dict[str, list[dict[str, Any]]] = {
        "consolidations": [],
        "prunings": [],
        "manual_review": [],
    }
    section: str | None = None
    item: dict[str, Any] | None = None
    nested: dict[str, str] | None = None
    seen_sections: set[str] = set()
    for raw_line in block.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent == 0:
            section = None
            item = None
            nested = None
            if ":" not in stripped:
                raise RunError("curator report has malformed top-level YAML")
            name, raw_value = stripped.split(":", 1)
            if name not in sections:
                continue
            if name in seen_sections:
                raise RunError(f"curator report repeats top-level section {name}")
            seen_sections.add(name)
            value = raw_value.strip()
            if value == "[]":
                continue
            if value:
                raise RunError(f"curator report section {name} must be a list")
            section = name
            continue
        if section is None:
            continue
        if indent == 2 and stripped.startswith("- "):
            item = {}
            sections[section].append(item)
            nested = None
            remainder = stripped[2:].strip()
            if not remainder:
                continue
            if ":" not in remainder:
                raise RunError(f"curator report has malformed {section} item")
            key, value = remainder.split(":", 1)
            item[key.strip()] = report_scalar(value, f"{section}.{key.strip()}")
            continue
        if item is None:
            raise RunError(f"curator report has malformed {section} indentation")
        if indent == 4:
            if ":" not in stripped:
                raise RunError(f"curator report has malformed {section} field")
            key, value = stripped.split(":", 1)
            key = key.strip()
            if key in item:
                raise RunError(f"curator report repeats {section}.{key}")
            if not value.strip():
                if section != "prunings" or key != "evidence":
                    raise RunError(
                        f"curator report has unsupported nested field {section}.{key}"
                    )
                nested = {}
                item[key] = nested
            else:
                nested = None
                item[key] = report_scalar(value, f"{section}.{key}")
            continue
        if indent == 6 and nested is not None:
            if stripped.startswith("- ") or ":" not in stripped:
                raise RunError("curator pruning evidence must be a scalar mapping")
            key, value = stripped.split(":", 1)
            key = key.strip()
            if key in nested:
                raise RunError(f"curator report repeats pruning evidence.{key}")
            nested[key] = report_scalar(value, f"pruning evidence.{key}")
            continue
        raise RunError(f"curator report has malformed nested {section} content")
    consolidations: list[dict[str, str]] = []
    for item in sections["consolidations"]:
        if set(item) != {"from", "into", "reason"}:
            raise RunError("curator report consolidation fields are malformed")
        source = item.get("from", "")
        destination = item.get("into", "")
        if not re_safe_id(source) or not re_safe_id(destination):
            raise RunError("curator report has malformed consolidation identity")
        consolidations.append(
            {"from": source, "into": destination, "reason": item["reason"]}
        )
    prunings: list[dict[str, Any]] = []
    for item in sections["prunings"]:
        if set(item) != {"name", "reason", "evidence"}:
            raise RunError("curator report pruning evidence is incomplete")
        name = item.get("name", "")
        if not re_safe_id(name):
            raise RunError("curator report has malformed pruning identity")
        if not isinstance(item["evidence"], dict):
            raise RunError("curator report pruning evidence is malformed")
        prunings.append(item)
    if len({item["from"] for item in consolidations}) != len(consolidations):
        raise RunError("curator report repeats a consolidation source")
    pruning_names = [item["name"] for item in prunings]
    if len(set(pruning_names)) != len(pruning_names):
        raise RunError("curator report repeats a pruning source")
    if set(pruning_names) & {item["from"] for item in consolidations}:
        raise RunError("curator report assigns a source to multiple actions")
    return {
        "consolidations": consolidations,
        "prunings": prunings,
    }


def report_identity(path: Path) -> dict[str, Any]:
    report = path.resolve()
    try:
        stat = report.stat()
        data = report.read_bytes()
    except OSError as error:
        raise RunError(f"cannot identify curator report: {error}") from error
    if not report.is_file() or report.is_symlink():
        raise RunError(f"curator report is not a regular file: {report}")
    modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
    match = re.fullmatch(
        r"(\d{8})-(\d{6})-curator-report\.md",
        report.name,
    )
    if match:
        created_at = datetime.strptime(
            "".join(match.groups()), "%Y%m%d%H%M%S"
        ).replace(tzinfo=timezone.utc)
    elif os.environ.get("CURATOR_ALLOW_UNSTAMPED_REPORT") == "1":
        created_at = modified_at
    else:
        raise RunError("curator report filename has no trusted timestamp")
    age = datetime.now(timezone.utc) - created_at
    if age < timedelta(0) or age > MAX_REPORT_AGE:
        raise RunError("curator report is stale or has an invalid timestamp")
    return {
        "path": str(report),
        "sha256": hash_bytes(data),
        "created_at": created_at.isoformat(),
        "modified_at": modified_at.isoformat(),
        "max_age_seconds": int(MAX_REPORT_AGE.total_seconds()),
        "actions": parse_report_actions(report),
    }


def inventory_rows(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in inventory["skills"]:
        name = row.get("name")
        if not isinstance(name, str) or name in rows:
            raise RunError("scheduled dependency inventory has duplicate skill identity")
        rows[name] = row
    return rows


def skill_directory(row: dict[str, Any], configured_roots: dict[str, Path]) -> Path:
    root_name = row.get("root")
    if root_name not in configured_roots:
        raise RunError(f"skill is outside curator-managed roots: {row.get('name')}")
    path = Path(str(row.get("path", ""))).resolve()
    expected_root = (
        configured_roots[root_name] / "skills"
        if root_name == "public"
        else configured_roots[root_name]
    )
    try:
        relative = path.relative_to(expected_root.resolve())
    except ValueError as error:
        raise RunError(f"skill path escaped its managed root: {path}") from error
    if len(relative.parts) != 1 or relative.name != row.get("name"):
        raise RunError(f"skill path does not match inventory identity: {path}")
    return path


def frontmatter_author(skill_md: Path) -> str:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as error:
        raise RunError(f"cannot read skill provenance frontmatter: {error}") from error
    match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", text, re.DOTALL)
    if not match:
        raise RunError(f"agent-created skill has malformed frontmatter: {skill_md}")
    authors = re.findall(r"(?m)^author:\s*([^\s#]+)\s*$", match.group(1))
    if len(authors) != 1:
        raise RunError(f"agent-created skill has invalid author provenance: {skill_md}")
    return authors[0]


def parse_iso_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise RunError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RunError(f"{field} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise RunError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def require_agent_created(
    name: str,
    rows: dict[str, dict[str, Any]],
    configured_roots: dict[str, Path],
) -> dict[str, Any]:
    row = rows.get(name)
    if row is None:
        raise RunError(f"authorized skill is not live: {name}")
    if row.get("pinned") or row.get("implicit_pin"):
        raise RunError(f"authorized skill is pinned: {name}")
    directory = skill_directory(row, configured_roots)
    marker = directory / ".agent-created"
    if not marker.is_file() or marker.is_symlink():
        raise RunError(f"authorized skill is not agent-created: {name}")
    envelope = directory / ".agent-created.json"
    if not envelope.is_file() or envelope.is_symlink():
        raise RunError(f"authorized skill has no valid provenance envelope: {name}")
    validation = run([str(EVIDENCE_TOOL), "validate", str(envelope)], check=False)
    if validation.returncode:
        raise RunError(
            f"authorized skill provenance envelope is invalid: {name}: "
            f"{validation.stderr.strip()}"
        )
    try:
        provenance = json.loads(envelope.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RunError(f"cannot load provenance envelope for {name}: {error}") from error
    if provenance.get("skill") != name:
        raise RunError(f"authorized skill provenance belongs to another skill: {name}")
    created_by = provenance.get("created_by", "skill-review")
    author = frontmatter_author(directory / "SKILL.md")
    if author != created_by:
        raise RunError(f"authorized skill author provenance does not match: {name}")
    created_at = provenance.get("created_at")
    parse_iso_timestamp(created_at, f"{name} provenance created_at")
    source_session_id = provenance.get("source_session_id")
    if not isinstance(source_session_id, str) or not source_session_id:
        raise RunError(f"authorized skill source provenance is missing: {name}")
    return {
        "name": name,
        "root": row["root"],
        "path": str(directory),
        "marker": str(marker),
        "marker_sha256": hash_bytes(marker.read_bytes()),
        "envelope": str(envelope),
        "envelope_sha256": hash_bytes(envelope.read_bytes()),
        "envelope_schema_version": provenance.get("schema_version", 1),
        "created_at": created_at,
        "source_session_id": source_session_id,
        "created_by": created_by,
        "skill_md": str(directory / "SKILL.md"),
        "skill_md_sha256": hash_bytes((directory / "SKILL.md").read_bytes()),
        "author": author,
    }


def completed_project_cooldown_days() -> int:
    state_path = curator_state_path()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RunError(f"cannot read curator pruning policy: {error}") from error
    value = state.get("config_overrides", {}).get(
        "completed_project_cooldown_days", 14
    )
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 365:
        raise RunError("completed-project pruning cooldown is malformed")
    return value


def validate_pruning_evidence(
    pruning: dict[str, Any], provenance: dict[str, Any]
) -> None:
    evidence = pruning["evidence"]
    expected_fields = {
        "basis",
        "created_at",
        "last_used_at",
        "completion_evidence",
        "reuse_assessment",
        "evaluation",
        "tombstone_effect",
    }
    if set(evidence) != expected_fields:
        raise RunError(
            f"autonomous pruning evidence is incomplete: {pruning['name']}"
        )
    if evidence["reuse_assessment"] != "no-reusable-content":
        raise RunError(f"autonomous pruning has reusable content: {pruning['name']}")
    if evidence["evaluation"] != "not-required-no-merge-target":
        raise RunError(
            f"autonomous pruning evaluation evidence is unsupported: {pruning['name']}"
        )
    if (
        evidence["tombstone_effect"]
        != "permanent-name-family-block-acknowledged"
    ):
        raise RunError(
            f"autonomous pruning omits permanent tombstone acknowledgement: "
            f"{pruning['name']}"
        )
    created = parse_iso_timestamp(
        evidence["created_at"], f"{pruning['name']} pruning created_at"
    )
    provenance_created = parse_iso_timestamp(
        provenance["created_at"], f"{pruning['name']} provenance created_at"
    )
    if created != provenance_created:
        raise RunError(
            f"autonomous pruning creation evidence does not match provenance: "
            f"{pruning['name']}"
        )
    last_used_raw = evidence["last_used_at"]
    last_used = (
        created
        if last_used_raw == "never"
        else parse_iso_timestamp(
            last_used_raw, f"{pruning['name']} pruning last_used_at"
        )
    )
    if last_used < created:
        raise RunError(
            f"autonomous pruning last-use evidence predates creation: {pruning['name']}"
        )
    age = datetime.now(timezone.utc) - max(created, last_used)
    basis = evidence["basis"]
    if basis == "age-only":
        if age < timedelta(days=AGE_ONLY_PRUNING_DAYS):
            raise RunError(
                f"autonomous age-only pruning is too recent: {pruning['name']}"
            )
        if evidence["completion_evidence"] != "not-required-age-threshold":
            raise RunError(
                f"autonomous age-only pruning has unsupported completion evidence: "
                f"{pruning['name']}"
            )
    elif basis == "completed-project":
        if age < timedelta(days=completed_project_cooldown_days()):
            raise RunError(
                f"autonomous completed-project pruning is inside cooldown: "
                f"{pruning['name']}"
            )
        completion = evidence["completion_evidence"].strip()
        if completion in {"", "unknown", "none", "unsupported"}:
            raise RunError(
                f"autonomous completed-project pruning lacks direct completion "
                f"evidence: {pruning['name']}"
            )
    else:
        raise RunError(f"autonomous pruning basis is unsupported: {pruning['name']}")


def destination_path_prefix(root_name: str, destination: str) -> str:
    return f"skills/{destination}/" if root_name == "public" else f"{destination}/"


def validate_destination_paths(operation: dict[str, Any], destination: str) -> None:
    prefix = destination_path_prefix(operation["root"], destination)
    permitted = {
        path
        for path in operation["paths"]
        if path.startswith(prefix)
        or (
            operation["root"] == "public"
            and operation["action"] == "create"
            and path in PUBLIC_MANIFESTS
        )
    }
    if permitted != set(operation["paths"]):
        raise RunError(
            f"commit paths escape authorized destination skill: {destination}"
        )


def linked_destination_commit(
    operations: list[dict[str, Any]], archive_index: int
) -> dict[str, Any] | None:
    archive = operations[archive_index]
    destination = archive.get("absorbed_into")
    if not destination:
        return None
    for operation in reversed(operations[:archive_index]):
        if (
            operation["kind"] == "commit"
            and operation["root"] == archive["root"]
            and operation.get("skill") == destination
            and archive["skill"] in operation.get("sources", [])
        ):
            return operation
    raise RunError(
        f"consolidation archive lacks a preceding destination commit: "
        f"{archive['skill']}"
    )


def authorize_operations(
    operations: list[dict[str, Any]],
    report: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    rows = inventory_rows(inventory)
    configured_roots = roots()
    consolidations = {
        item["from"]: item["into"] for item in report["actions"]["consolidations"]
    }
    prunings = {
        item["name"]: item for item in report["actions"]["prunings"]
    }
    evidence: dict[str, dict[str, Any]] = {}
    for index, operation in enumerate(operations):
        if operation["kind"] == "archive" and operation.get("absorbed_into"):
            linked_destination_commit(operations, index)
        if operation["status"] == "complete":
            continue
        if operation["kind"] == "archive":
            name = operation["skill"]
            destination = operation.get("absorbed_into")
            if destination:
                if consolidations.get(name) != destination:
                    raise RunError(
                        f"archive is not authorized by report consolidation: {name}"
                    )
            elif name not in prunings:
                raise RunError(f"archive is not authorized by report pruning: {name}")
            source_evidence = require_agent_created(name, rows, configured_roots)
            if not destination:
                validate_pruning_evidence(prunings[name], source_evidence)
            evidence[name] = source_evidence
            continue
        destination = operation.get("skill")
        sources = operation.get("sources", [])
        if not isinstance(destination, str) or not re_safe_id(destination):
            raise RunError("autonomous commit requires a destination skill")
        if not sources or any(not isinstance(item, str) for item in sources):
            raise RunError(f"autonomous commit requires declared sources: {destination}")
        expected_sources = {
            source for source, target in consolidations.items() if target == destination
        }
        if set(sources) != expected_sources:
            raise RunError(
                f"commit sources do not match report consolidations: {destination}"
            )
        validate_destination_paths(operation, destination)
        for source in sources:
            source_evidence = require_agent_created(source, rows, configured_roots)
            if source_evidence["root"] != operation["root"]:
                raise RunError(
                    f"consolidation crosses managed roots: {source} -> {destination}"
                )
            evidence[source] = source_evidence
        if operation["action"] == "patch":
            destination_evidence = require_agent_created(
                destination, rows, configured_roots
            )
            if destination_evidence["root"] != operation["root"]:
                raise RunError(f"destination root mismatch: {destination}")
            evidence[destination] = destination_evidence
        else:
            if operation["status"] == "planned" and destination in rows:
                raise RunError(f"create destination already exists: {destination}")
            if operation["status"] == "intent":
                destination_evidence = require_agent_created(
                    destination, rows, configured_roots
                )
                if destination_evidence["root"] != operation["root"]:
                    raise RunError(f"destination root mismatch: {destination}")
                evidence[destination] = destination_evidence
            prefix = "skills/" if operation["root"] == "public" else ""
            required = {
                f"{prefix}{destination}/SKILL.md",
                f"{prefix}{destination}/.agent-created",
                f"{prefix}{destination}/.agent-created.json",
            }
            if not required.issubset(operation["paths"]):
                raise RunError(
                    f"create destination lacks package/provenance paths: {destination}"
                )
    return {
        "dependency_inventory_sha256": hash_json(inventory),
        "skills": sorted(evidence.values(), key=lambda item: item["name"]),
    }


def normalize_plan(plan: dict[str, Any], inventory: dict[str, Any]) -> list[dict[str, Any]]:
    raw_operations = plan.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise RunError("plan.operations must be a non-empty array")
    skills = {item["name"]: item for item in inventory["skills"]}
    operations: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_operations, 1):
        if not isinstance(raw, dict):
            raise RunError(f"plan operation {index} must be an object")
        kind = raw.get("kind")
        if kind == "archive":
            name = raw.get("skill")
            if not isinstance(name, str) or name not in skills:
                raise RunError(f"archive operation {index} names no live skill")
            absorbed_into = raw.get("absorbed_into")
            if absorbed_into is not None and (
                not isinstance(absorbed_into, str) or not re_safe_id(absorbed_into)
            ):
                raise RunError(
                    f"archive operation {index} has invalid replacement identity"
                )
            row = skills[name]
            if row["pinned"] or row["implicit_pin"]:
                raise RunError(f"archive operation {index} targets pinned skill: {name}")
            root_name = row["root"]
            root = roots()[root_name]
            relative = Path(row["path"]).relative_to(
                root / "skills" if root_name == "public" else root
            )
            path = (
                Path("skills") / relative
                if root_name == "public"
                else relative
            ).as_posix()
            paths = [path]
            if root_name == "public":
                paths.extend(PUBLIC_MANIFESTS)
            operation = {
                "op_id": f"op-{index:03d}",
                "kind": "archive",
                "root": root_name,
                "skill": name,
                "absorbed_into": absorbed_into,
                "paths": paths,
                "status": "planned",
            }
        elif kind == "commit":
            root_name = raw.get("root")
            action = raw.get("action")
            if root_name not in {"public", "local"} or action not in {"patch", "create"}:
                raise RunError(f"commit operation {index} has invalid root/action")
            raw_paths = raw.get("paths")
            if not isinstance(raw_paths, list) or not raw_paths:
                raise RunError(f"commit operation {index} requires paths")
            raw_sources = raw.get("sources", [])
            if not isinstance(raw_sources, list) or any(
                not isinstance(source, str) or not re_safe_id(source)
                for source in raw_sources
            ):
                raise RunError(f"commit operation {index} has invalid sources")
            operation = {
                "op_id": f"op-{index:03d}",
                "kind": "commit",
                "root": root_name,
                "action": action,
                "skill": raw.get("skill"),
                "sources": sorted(set(raw_sources)),
                "paths": sorted(
                    {validate_relative_path(root_name, str(path)) for path in raw_paths}
                ),
                "status": "planned",
            }
        else:
            raise RunError(f"plan operation {index} has invalid kind: {kind}")
        for left_index, left in enumerate(operation["paths"]):
            for right in operation["paths"][left_index + 1 :]:
                if overlaps(left, right):
                    raise RunError(
                        f"operation {index} has overlapping paths: {left}, {right}"
                    )
        operations.append(operation)
    for index, operation in enumerate(operations):
        if operation["kind"] == "archive" and operation.get("absorbed_into"):
            linked_destination_commit(operations, index)
    return operations


def verify_dirty_disjoint(
    root_records: dict[str, dict[str, Any]], operations: list[dict[str, Any]]
) -> None:
    for root_name, record in root_records.items():
        operation_paths = [
            path
            for operation in operations
            if operation["root"] == root_name
            for path in operation["paths"]
        ]
        for dirty in record["initial_dirty"]:
            for planned in operation_paths:
                if overlaps(dirty["path"], planned):
                    raise RunError(
                        f"planned path {planned} overlaps pre-existing dirty path "
                        f"{dirty['path']} in {root_name}"
                    )


def lock_command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run([str(LOCK_TOOL), *args], check=check)


def acquire_lock(owner: str) -> str:
    result = lock_command("acquire", "--mode", "session", "--owner", owner, check=False)
    if result.returncode:
        raise RunError(f"writer lease unavailable: {result.stderr.strip()}")
    return result.stdout.strip()


def renew_lock(manifest: dict[str, Any]) -> None:
    result = lock_command("renew", manifest["lock_token"], check=False)
    if result.returncode:
        raise RunError("writer lease is no longer owned by this run")
    manifest["lock_renewed_at"] = now_iso()


def release_lock(manifest: dict[str, Any]) -> None:
    if lock_command("release", manifest["lock_token"], check=False).returncode:
        raise RunError("could not release writer lease")


def verify_root_records(manifest: dict[str, Any]) -> dict[str, Path]:
    current_roots = roots()
    seen_git_dirs: set[str] = set()
    for name, record in manifest["roots"].items():
        path = current_roots[name]
        identity = root_identity(path)
        if identity["path"] != record["path"] or identity["git_dir"] != record["git_dir"]:
            raise RunError(f"{name} root identity changed")
        if identity["git_dir"] in seen_git_dirs:
            raise RunError("managed roots resolve to the same git repository")
        seen_git_dirs.add(identity["git_dir"])
        if run(
            [
                "git",
                "-C",
                str(path),
                "cat-file",
                "-e",
                f"{record['initial_head']}^{{commit}}",
            ],
            check=False,
        ).returncode:
            raise RunError(f"{name} starting commit is missing or rewritten")
    return current_roots


def verify_expected_heads(
    manifest: dict[str, Any], current_roots: dict[str, Path]
) -> None:
    for root_name, root in current_roots.items():
        if git(root, "rev-parse", "HEAD") != expected_head(manifest, root_name):
            raise RunError(f"unexpected commit appeared in {root_name} root")


def authorized_changed_destination_skills(manifest: dict[str, Any]) -> set[str]:
    return {
        operation["skill"]
        for operation in manifest["operations"]
        if operation["kind"] == "commit"
        and operation["status"] in {"intent", "complete"}
        and isinstance(operation.get("skill"), str)
    }


def validate_inventory_revalidation(
    manifest: dict[str, Any],
    receipt: dict[str, Any],
    current: dict[str, Any],
) -> None:
    frozen = manifest["dependency_freeze"]
    if hash_json(frozen) != receipt["dependency_inventory_sha256"]:
        raise RunError("authorization receipt dependency inventory does not match run")
    if {
        key: value for key, value in current.items() if key != "skills"
    } != {
        key: value for key, value in frozen.items() if key != "skills"
    }:
        raise RunError("scheduled dependency inventory metadata changed")
    expected = inventory_rows(frozen)
    changed: set[str] = set()
    created: dict[str, dict[str, Any]] = {}
    for operation in manifest["operations"]:
        if operation["status"] not in {"intent", "complete"}:
            continue
        if operation["kind"] == "archive":
            if operation["status"] != "complete":
                continue
            expected.pop(operation["skill"], None)
            created.pop(operation["skill"], None)
            changed.discard(operation["skill"])
        elif operation["action"] == "create":
            created[operation["skill"]] = operation
            changed.add(operation["skill"])
        else:
            changed.add(operation["skill"])
    current_rows = inventory_rows(current)
    expected_names = (set(expected) | set(created))
    if set(current_rows) != expected_names:
        missing = sorted(expected_names - set(current_rows))
        unexpected = sorted(set(current_rows) - expected_names)
        raise RunError(
            "scheduled dependency inventory changed outside authorized operations "
            f"(missing={missing}, unexpected={unexpected})"
        )
    configured_roots = roots()
    for name, frozen_row in expected.items():
        current_row = current_rows[name]
        if name not in changed:
            if current_row != frozen_row:
                raise RunError(
                    f"scheduled dependency inventory drifted for unrelated skill: {name}"
                )
            continue
        if (
            current_row.get("root") != frozen_row.get("root")
            or Path(str(current_row.get("path", ""))).resolve()
            != Path(str(frozen_row.get("path", ""))).resolve()
            or current_row.get("pinned")
            or current_row.get("implicit_pin")
        ):
            raise RunError(
                f"authorized destination inventory identity changed: {name}"
            )
        skill_directory(current_row, configured_roots)
    for name, operation in created.items():
        row = current_rows[name]
        if (
            row.get("root") != operation["root"]
            or row.get("pinned")
            or row.get("implicit_pin")
        ):
            raise RunError(f"created destination inventory is unsafe: {name}")
        skill_directory(row, configured_roots)


def validate_bound_provenance(
    manifest: dict[str, Any],
    receipt: dict[str, Any],
    current_authorization: dict[str, Any],
) -> None:
    bound = {item["name"]: item for item in receipt["skills"]}
    changed = authorized_changed_destination_skills(manifest)
    for current in current_authorization["skills"]:
        previous = bound.get(current["name"])
        if previous is None or current["name"] in changed:
            continue
        if current != previous:
            raise RunError(
                f"marker-backed provenance changed after authorization: "
                f"{current['name']}"
            )


def reverify_authorization(
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    receipt_path = Path(manifest["authorization_receipt"])
    if (
        not receipt_path.is_file()
        or receipt_path.is_symlink()
        or hash_bytes(receipt_path.read_bytes())
        != manifest["authorization_receipt_sha256"]
    ):
        raise RunError("autonomous authorization receipt changed")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RunError(f"cannot load autonomous authorization receipt: {error}") from error
    if (
        receipt.get("schema_version") != 1
        or receipt.get("run_id") != manifest["run_id"]
    ):
        raise RunError("autonomous authorization receipt identity is invalid")
    report = report_identity(Path(manifest["report"]))
    if (
        report["sha256"] != manifest["report_sha256"]
        or report["sha256"] != receipt["report"]["sha256"]
        or report["modified_at"] != receipt["report"]["modified_at"]
        or report["created_at"] != receipt["report"]["created_at"]
    ):
        raise RunError("curator report changed after authorization")
    require_autonomous_switches_open()
    inventory = scanner_inventory()
    validate_inventory_revalidation(manifest, receipt, inventory)
    current = authorize_operations(manifest["operations"], report, inventory)
    validate_bound_provenance(manifest, receipt, current)
    return report, receipt, current


def snapshot_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    if not path.is_file() or path.is_symlink():
        raise RunError(f"state effect is not a regular file: {path}")
    data = path.read_bytes()
    return {
        "path": str(path),
        "exists": True,
        "sha256": hash_bytes(data),
        "bytes_b64": base64.b64encode(data).decode(),
    }


def snapshot_effects(operation: dict[str, Any]) -> dict[str, Any]:
    effects: dict[str, Any] = {"ledger": snapshot_file(ledger_path())}
    if operation["kind"] == "archive":
        name = operation["skill"]
        state = archive_state_dir()
        effects["retirement"] = snapshot_file(state / "retired" / f"{name}.json")
        effects["tombstone"] = snapshot_file(state / "tombstones" / f"{name}.json")
        if operation["root"] == "public":
            public_root = roots()["public"]
            for relative in PUBLIC_MANIFESTS:
                effects[f"manifest:{relative}"] = snapshot_file(public_root / relative)
    return effects


def verify_initial_dirty(manifest: dict[str, Any], current_roots: dict[str, Path]) -> None:
    for name, record in manifest["roots"].items():
        for expected in record["initial_dirty"]:
            actual = path_fingerprint(current_roots[name], expected["path"])
            if actual != expected:
                raise RunError(
                    f"pre-existing dirty path changed during run: {name}:{expected['path']}"
                )


def verify_dirty_state(
    manifest: dict[str, Any],
    current_roots: dict[str, Path],
    allowed: dict[str, list[str]] | None = None,
) -> None:
    verify_initial_dirty(manifest, current_roots)
    allowed = allowed or {}
    for name, root in current_roots.items():
        initial = {
            item["path"] for item in manifest["roots"][name]["initial_dirty"]
        }
        permitted = allowed.get(name, [])
        for current in dirty_paths(root):
            if current in initial:
                continue
            if current in permitted:
                continue
            raise RunError(f"undeclared dirty path: {name}:{current}")


def find_planned_operation(
    manifest: dict[str, Any], args: argparse.Namespace
) -> dict[str, Any]:
    if any(operation["status"] == "intent" for operation in manifest["operations"]):
        raise RunError("the preceding mutation intent is still incomplete")
    operation = next(
        (
            item
            for item in manifest["operations"]
            if item["status"] == "planned"
        ),
        None,
    )
    if operation is None:
        raise RunError("no planned operation remains")
    if operation["kind"] != args.kind or operation["root"] != args.root:
        raise RunError(f"next planned operation is {operation['op_id']}")
    if args.skill and operation.get("skill") != args.skill:
        raise RunError(f"next planned operation is {operation['op_id']}")
    if args.action and operation.get("action") != args.action:
        raise RunError(f"next planned operation is {operation['op_id']}")
    if args.paths:
        supplied = sorted(
            {validate_relative_path(args.root, path) for path in args.paths}
        )
        if operation["paths"] != supplied:
            raise RunError(f"next planned operation is {operation['op_id']}")
    return operation


def expected_head(manifest: dict[str, Any], root_name: str) -> str:
    head = manifest["roots"][root_name]["initial_head"]
    for operation in manifest["operations"]:
        if operation["root"] == root_name and operation["status"] == "complete":
            head = operation["commit"]
    return head


def command_begin(args: argparse.Namespace) -> int:
    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RunError(f"cannot load plan: {error}") from error
    inventory = scanner_inventory()
    operations = normalize_plan(plan, inventory)
    authorization: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    if args.autonomous:
        report = report_identity(Path(args.report))
        switches = require_autonomous_switches_open()
        authorization = {
            "schema_version": 1,
            "authorized_at": now_iso(),
            "report": report,
            "switches": switches,
            **authorize_operations(operations, report, inventory),
        }
    configured_roots = roots()
    root_records = {
        name: {
            **root_identity(path),
            "initial_dirty": dirty_snapshot(path),
        }
        for name, path in configured_roots.items()
    }
    if root_records["public"]["git_dir"] == root_records["local"]["git_dir"]:
        raise RunError("managed roots resolve to the same git repository")
    verify_dirty_disjoint(root_records, operations)
    run_id = args.run_id or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    path = manifest_path(run_id)
    if path.exists():
        raise RunError(f"run already exists: {run_id}")
    token = acquire_lock(f"skill-curator:{run_id}")
    receipt_path: Path | None = None
    try:
        if authorization is not None:
            receipt_path = authorization_path(run_id)
            authorization["run_id"] = run_id
            immutable_write(receipt_path, authorization)
    except Exception:
        lock_command("release", token, check=False)
        raise
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "active",
        "started_at": now_iso(),
        "report": str(Path(args.report).resolve()),
        "plan": str(Path(args.plan).resolve()),
        "authority_mode": "autonomous" if args.autonomous else "manual",
        "lock_token": token,
        "lock_renewed_at": now_iso(),
        "roots": root_records,
        "dependency_freeze": inventory,
        "operations": operations,
    }
    if receipt_path is not None:
        payload["report_sha256"] = report["sha256"]
        payload["authorization_receipt"] = str(receipt_path)
        payload["authorization_receipt_sha256"] = hash_bytes(receipt_path.read_bytes())
    try:
        atomic_write(path, payload)
    except Exception:
        if receipt_path is not None:
            receipt_path.unlink(missing_ok=True)
        lock_command("release", token, check=False)
        raise
    print(run_id)
    return 0


def command_renew(args: argparse.Namespace) -> int:
    path, manifest = load_manifest(args.run)
    if manifest["status"] not in {"active", "publish_failed"}:
        raise RunError(f"cannot renew run in status {manifest['status']}")
    verify_root_records(manifest)
    renew_lock(manifest)
    atomic_write(path, manifest)
    return 0


def provenance_refs(source: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "kind": "agent-created-marker",
            "path": source["marker"],
            "sha256": source["marker_sha256"],
        },
        {
            "kind": "agent-created-envelope",
            "path": source["envelope"],
            "sha256": source["envelope_sha256"],
            "schema_version": source["envelope_schema_version"],
            "created_by": source["created_by"],
            "source_session_id": source["source_session_id"],
            "created_at": source["created_at"],
        },
        {
            "kind": "skill-author-frontmatter",
            "path": source["skill_md"],
            "sha256": source["skill_md_sha256"],
            "author": source["author"],
        },
    ]


def command_archive_context(args: argparse.Namespace) -> int:
    path, manifest = load_manifest(args.run)
    if manifest["status"] != "active":
        raise RunError(f"cannot authorize archive in status {manifest['status']}")
    current_roots = verify_root_records(manifest)
    renew_lock(manifest)
    verify_expected_heads(manifest, current_roots)
    verify_dirty_state(manifest, current_roots)
    operation = next(
        (
            item
            for item in manifest["operations"]
            if item["status"] == "planned"
        ),
        None,
    )
    if (
        operation is None
        or operation["kind"] != "archive"
        or operation.get("skill") != args.skill
    ):
        raise RunError(f"archive is not the next planned operation: {args.skill}")
    archive_index = manifest["operations"].index(operation)
    destination_commit = linked_destination_commit(
        manifest["operations"], archive_index
    )
    if destination_commit is not None and destination_commit["status"] != "complete":
        raise RunError(
            f"consolidation destination commit is incomplete: {args.skill}"
        )
    if manifest.get("authority_mode") != "autonomous":
        context = {
            "authority_mode": "manual",
            "absorbed_into": operation.get("absorbed_into"),
        }
        operation["archive_context"] = context
        atomic_write(path, manifest)
        print(json.dumps(context, sort_keys=True))
        return 0
    report, _, current = reverify_authorization(manifest)
    source = next(
        item for item in current["skills"] if item["name"] == args.skill
    )
    context = {
        "authority_mode": "autonomous",
        "report": report["path"],
        "report_sha256": report["sha256"],
        "absorbed_into": operation.get("absorbed_into"),
        "evidence_refs": provenance_refs(source),
    }
    operation["archive_context"] = context
    atomic_write(path, manifest)
    print(json.dumps(context, sort_keys=True))
    return 0


def command_intent(args: argparse.Namespace) -> int:
    path, manifest = load_manifest(args.run)
    if manifest["status"] != "active":
        raise RunError(f"cannot add intent to run in status {manifest['status']}")
    current_roots = verify_root_records(manifest)
    renew_lock(manifest)
    if manifest.get("authority_mode") == "autonomous":
        reverify_authorization(manifest)
    verify_dirty_state(manifest, current_roots)
    operation = find_planned_operation(manifest, args)
    if operation["kind"] == "archive" and "archive_context" not in operation:
        raise RunError("archive intent lacks bound archive context")
    current_head = git(current_roots[operation["root"]], "rev-parse", "HEAD")
    if current_head != expected_head(manifest, operation["root"]):
        raise RunError(f"unexpected commit appeared in {operation['root']} root")
    operation["status"] = "intent"
    operation["intent_at"] = now_iso()
    operation["before_head"] = git(current_roots[operation["root"]], "rev-parse", "HEAD")
    operation["effects_before"] = snapshot_effects(operation)
    atomic_write(path, manifest)
    print(operation["op_id"])
    return 0


def operation_by_id(manifest: dict[str, Any], op_id: str) -> dict[str, Any]:
    for operation in manifest["operations"]:
        if operation["op_id"] == op_id:
            return operation
    raise RunError(f"unknown operation: {op_id}")


def commit_paths(root: Path, commit: str) -> list[str]:
    output = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit)
    return [line for line in output.splitlines() if line]


def record_ledger_effect(operation: dict[str, Any]) -> dict[str, Any]:
    before = operation["effects_before"]["ledger"]
    path = Path(before["path"])
    current = path.read_bytes() if path.exists() else b""
    previous = base64.b64decode(before.get("bytes_b64", "")) if before["exists"] else b""
    if not current.startswith(previous):
        raise RunError("ledger changed non-append-only during operation")
    appended = current[len(previous) :]
    return {
        "path": str(path),
        "offset": len(previous),
        "bytes_b64": base64.b64encode(appended).decode(),
        "sha256": hash_bytes(appended),
    }


def snapshot_json(snapshot: dict[str, Any], label: str) -> dict[str, Any]:
    if not snapshot.get("exists"):
        raise RunError(f"archive did not write {label}")
    try:
        payload = json.loads(base64.b64decode(snapshot["bytes_b64"]))
    except (ValueError, json.JSONDecodeError) as error:
        raise RunError(f"archive wrote malformed {label}") from error
    if not isinstance(payload, dict):
        raise RunError(f"archive wrote malformed {label}")
    return payload


def validate_archive_completion(operation: dict[str, Any]) -> None:
    expected_replacement = operation.get("absorbed_into")
    retirement = snapshot_json(
        operation["effects_after"]["retirement"], "retirement record"
    )
    context = operation.get("archive_context")
    if not isinstance(context, dict):
        raise RunError("archive completion lacks bound archive context")
    tombstone_snapshot = operation["effects_after"]["tombstone"]
    tombstone = (
        snapshot_json(tombstone_snapshot, "tombstone")
        if tombstone_snapshot.get("exists")
        else None
    )
    if context.get("authority_mode") == "autonomous" and tombstone is None:
        raise RunError("autonomous archive did not write a tombstone")
    records = [("retirement record", retirement)]
    if tombstone is not None:
        records.append(("tombstone", tombstone))
    for label, payload in records:
        if payload.get("skill") != operation["skill"]:
            raise RunError(f"archive {label} belongs to another skill")
        if payload.get("replacement") != expected_replacement:
            raise RunError(
                f"archive {label} replacement differs from authorized destination"
            )
        expected_reason = "consolidated" if expected_replacement else "pruned"
        if payload.get("reason") != expected_reason:
            raise RunError(f"archive {label} reason differs from authorization")
    if context.get("absorbed_into") != expected_replacement:
        raise RunError("archive context replacement differs from plan")
    if context.get("authority_mode") == "autonomous":
        if (
            retirement.get("curator_authority") != "autonomous"
            or retirement.get("curator_report") != context.get("report")
            or retirement.get("curator_report_sha256")
            != context.get("report_sha256")
            or retirement.get("evidence_refs") != context.get("evidence_refs")
        ):
            raise RunError(
                "archive retirement record does not bind authorization evidence"
            )


def complete_operation(
    path: Path, manifest: dict[str, Any], operation: dict[str, Any]
) -> None:
    current_roots = verify_root_records(manifest)
    renew_lock(manifest)
    if operation["status"] != "intent":
        raise RunError(f"operation is not awaiting completion: {operation['op_id']}")
    root = current_roots[operation["root"]]
    head = git(root, "rev-parse", "HEAD")
    if head == operation["before_head"]:
        raise RunError("operation produced no commit")
    count = int(git(root, "rev-list", "--count", f"{operation['before_head']}..{head}"))
    if count != 1:
        raise RunError("operation must produce exactly one commit")
    changed = commit_paths(root, head)
    for changed_path in changed:
        declared = (
            any(overlaps(changed_path, allowed) for allowed in operation["paths"])
            if operation["kind"] == "archive"
            else changed_path in operation["paths"]
        )
        if not declared:
            raise RunError(
                f"operation commit touched undeclared path: {operation['root']}:{changed_path}"
            )
    operation["commit"] = head
    operation["changed_paths"] = changed
    operation["effects_after"] = snapshot_effects(operation)
    operation["ledger_effect"] = record_ledger_effect(operation)
    if operation["kind"] == "archive":
        validate_archive_completion(operation)
    operation["status"] = "complete"
    operation["completed_at"] = now_iso()
    verify_dirty_state(manifest, current_roots)
    atomic_write(path, manifest)


def command_complete(args: argparse.Namespace) -> int:
    path, manifest = load_manifest(args.run)
    if manifest["status"] != "active":
        raise RunError(f"cannot complete operation in status {manifest['status']}")
    operation = operation_by_id(manifest, args.op)
    complete_operation(path, manifest, operation)
    print(operation["commit"])
    return 0


def command_commit(args: argparse.Namespace) -> int:
    path, manifest = load_manifest(args.run)
    if manifest["status"] != "active":
        raise RunError(f"cannot commit operation in status {manifest['status']}")
    operation = operation_by_id(manifest, args.op)
    if operation["kind"] != "commit" or operation["status"] != "intent":
        raise RunError("scoped commit requires a commit intent")
    current_roots = verify_root_records(manifest)
    renew_lock(manifest)
    if manifest.get("authority_mode") == "autonomous":
        reverify_authorization(manifest)
    root = current_roots[operation["root"]]
    verify_dirty_state(
        manifest,
        current_roots,
        {operation["root"]: operation["paths"]},
    )
    message = Path(args.message_file).read_text(encoding="utf-8").rstrip()
    if TRAILER not in message:
        message = f"{message}\n\n{TRAILER}"
    message_path = Path(
        tempfile.mkstemp(
            prefix="curator-commit.", suffix=".txt", dir=scratch_dir()
        )[1]
    )
    try:
        message_path.write_text(f"{message}\n", encoding="utf-8")
        git(root, "add", "--", *operation["paths"])
        result = run(
            [
                "git",
                "-C",
                str(root),
                "commit",
                "--only",
                "-F",
                str(message_path),
                "--",
                *operation["paths"],
            ],
            check=False,
        )
        if result.returncode:
            git(root, "reset", "-q", "--", *operation["paths"], check=False)
            raise RunError(f"scoped curator commit failed: {result.stderr.strip()}")
    finally:
        message_path.unlink(missing_ok=True)
    complete_operation(path, manifest, operation)
    print(operation["commit"])
    return 0


def remote_head(root: Path, remote: str, branch: str) -> str | None:
    result = run(
        ["git", "-C", str(root), "ls-remote", "--heads", remote, f"refs/heads/{branch}"],
        check=False,
    )
    if result.returncode:
        raise RunError(f"cannot read public remote identity: {result.stderr.strip()}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) != 1:
        raise RunError("public remote returned ambiguous branch identity")
    fields = lines[0].split()
    if len(fields) != 2 or fields[1] != f"refs/heads/{branch}":
        raise RunError("public remote returned malformed branch identity")
    return fields[0]


def public_changed(manifest: dict[str, Any]) -> bool:
    return any(
        operation["root"] == "public" and operation["status"] == "complete"
        for operation in manifest["operations"]
    )


def recorded_remote_url(root: Path, remote: str) -> str:
    value = git(root, "remote", "get-url", remote)
    if not value:
        raise RunError(f"public remote has no URL: {remote}")
    return value


def verify_recorded_remote_url(root: Path, publication: dict[str, Any]) -> None:
    if recorded_remote_url(root, publication["remote"]) != publication["remote_url"]:
        raise RunError("public remote URL changed after publication authorization")


def mark_publication_failed(
    path: Path,
    manifest: dict[str, Any],
    publication: dict[str, Any],
    message: str,
) -> None:
    publication["status"] = "failed"
    publication["failed_at"] = now_iso()
    publication["error"] = message
    manifest["status"] = "publish_failed"
    atomic_write(path, manifest)


def reconcile_publication(
    path: Path,
    manifest: dict[str, Any],
    publication: dict[str, Any],
    public: Path,
    *,
    recovered_field: str,
) -> str:
    verify_recorded_remote_url(public, publication)
    try:
        served = remote_head(
            public, publication["remote"], publication["branch"]
        )
    except RunError as error:
        mark_publication_failed(path, manifest, publication, str(error))
        raise
    publication["remote_after"] = served
    if served == publication["new_head"]:
        publication["status"] = "published"
        publication["published_at"] = now_iso()
        publication[recovered_field] = True
        manifest["status"] = "active"
        atomic_write(path, manifest)
        return "published"
    if served == publication["prior_head"]:
        mark_publication_failed(
            path,
            manifest,
            publication,
            "publication did not update the remote",
        )
        return "not_published"
    mark_publication_failed(
        path,
        manifest,
        publication,
        "publication remote identity is neither the prior nor transaction head",
    )
    raise RunError("publication remote identity is unresolved")


def command_publish(args: argparse.Namespace) -> int:
    path, manifest = load_manifest(args.run)
    if manifest["status"] not in {"active", "publish_failed"}:
        raise RunError(f"cannot publish run in status {manifest['status']}")
    incomplete = [
        operation["op_id"]
        for operation in manifest["operations"]
        if operation["status"] != "complete"
    ]
    if incomplete:
        raise RunError(f"run has incomplete operations: {', '.join(incomplete)}")
    current_roots = verify_root_records(manifest)
    renew_lock(manifest)
    if manifest.get("authority_mode") == "autonomous":
        reverify_authorization(manifest)
    verify_dirty_state(manifest, current_roots)
    if not public_changed(manifest):
        raise RunError("run has no public-root changes to publish")
    public = current_roots["public"]
    prior = manifest["roots"]["public"]["initial_head"]
    new = expected_head(manifest, "public")
    existing_publication = manifest.get("publication", {})
    if existing_publication.get("status") in {"publishing", "failed"}:
        if (
            existing_publication.get("remote") != args.remote
            or existing_publication.get("branch") != args.branch
            or existing_publication.get("prior_head") != prior
            or existing_publication.get("new_head") != new
        ):
            raise RunError("publication identity does not match retry")
        outcome = reconcile_publication(
            path,
            manifest,
            existing_publication,
            public,
            recovered_field="recovered_before_retry",
        )
        if outcome == "published":
            return 0
    if git(public, "rev-parse", "HEAD") != new:
        raise RunError("public root is not at the finished transaction head")
    if run(
        ["git", "-C", str(public), "merge-base", "--is-ancestor", prior, new],
        check=False,
    ).returncode:
        raise RunError("public transaction is not a fast-forward from its prior head")
    remote_url = recorded_remote_url(public, args.remote)
    before = remote_head(public, args.remote, args.branch)
    if before != prior:
        raise RunError("public remote no longer matches the transaction prior head")
    publication = {
        "status": "publishing",
        "remote": args.remote,
        "remote_url": remote_url,
        "branch": args.branch,
        "prior_head": prior,
        "new_head": new,
        "remote_before": before,
        "started_at": now_iso(),
    }
    manifest["status"] = "active"
    manifest["publication"] = publication
    atomic_write(path, manifest)
    result = run(
        [
            "git",
            "-C",
            str(public),
            "push",
            args.remote,
            f"{new}:refs/heads/{args.branch}",
        ],
        check=False,
    )
    if result.returncode:
        outcome = reconcile_publication(
            path,
            manifest,
            publication,
            public,
            recovered_field="recovered_after_failed_push",
        )
        if outcome == "published":
            publication["push_error"] = result.stderr.strip()
            atomic_write(path, manifest)
            return 0
        publication["error"] = result.stderr.strip()
        atomic_write(path, manifest)
        raise RunError(f"public publication failed: {result.stderr.strip()}")
    after = remote_head(public, args.remote, args.branch)
    if after != new:
        mark_publication_failed(
            path,
            manifest,
            publication,
            "remote identity differs from pushed transaction head",
        )
        raise RunError("public remote does not serve the pushed transaction head")
    publication["status"] = "published"
    publication["remote_after"] = after
    publication["published_at"] = now_iso()
    atomic_write(path, manifest)
    return 0


def command_finish(args: argparse.Namespace) -> int:
    path, manifest = load_manifest(args.run)
    if manifest["status"] != "active":
        raise RunError(f"cannot finish run in status {manifest['status']}")
    incomplete = [
        operation["op_id"]
        for operation in manifest["operations"]
        if operation["status"] != "complete"
    ]
    if incomplete:
        raise RunError(f"run has incomplete operations: {', '.join(incomplete)}")
    current_roots = verify_root_records(manifest)
    if manifest.get("authority_mode") == "autonomous":
        reverify_authorization(manifest)
    for root_name, root in current_roots.items():
        if git(root, "rev-parse", "HEAD") != expected_head(manifest, root_name):
            raise RunError(f"unexpected commit appeared in {root_name} root")
    verify_dirty_state(manifest, current_roots)
    if public_changed(manifest):
        publication = manifest.get("publication", {})
        if publication.get("status") != "published":
            raise RunError("public-root transaction has not been published")
        verify_recorded_remote_url(current_roots["public"], publication)
        if (
            remote_head(
                current_roots["public"],
                publication["remote"],
                publication["branch"],
            )
            != publication["new_head"]
        ):
            raise RunError("published public identity changed before completion")
    renew_lock(manifest)
    manifest["status"] = "complete"
    manifest["finished_at"] = now_iso()
    atomic_write(path, manifest)
    release_lock(manifest)
    return 0


def ensure_rollback_lock(manifest: dict[str, Any]) -> None:
    if lock_command("renew", manifest["lock_token"], check=False).returncode == 0:
        manifest["lock_renewed_at"] = now_iso()
        return
    manifest["lock_token"] = acquire_lock(f"skill-curator-rollback:{manifest['run_id']}")
    manifest["lock_renewed_at"] = now_iso()


def validate_rollback_dirty(
    manifest: dict[str, Any], current_roots: dict[str, Path]
) -> None:
    verify_initial_dirty(manifest, current_roots)
    active_paths = {
        name: {
            path
            for operation in manifest["operations"]
            if operation["root"] == name and operation["status"] == "intent"
            for path in operation["paths"]
        }
        for name in current_roots
    }
    for name, root in current_roots.items():
        initial = {
            item["path"] for item in manifest["roots"][name]["initial_dirty"]
        }
        for current in dirty_paths(root):
            if current in initial:
                continue
            if any(overlaps(current, allowed) for allowed in active_paths[name]):
                continue
            raise RunError(f"unexpected dirty path blocks rollback: {name}:{current}")


def infer_interrupted_commit(root: Path, operation: dict[str, Any]) -> str | None:
    head = git(root, "rev-parse", "HEAD")
    if head == operation["before_head"]:
        return None
    count = int(git(root, "rev-list", "--count", f"{operation['before_head']}..{head}"))
    if count != 1:
        raise RunError(
            f"cannot infer interrupted operation {operation['op_id']}: history advanced by {count} commits"
        )
    for changed in commit_paths(root, head):
        if not any(overlaps(changed, allowed) for allowed in operation["paths"]):
            raise RunError(
                f"interrupted operation commit touched undeclared path: {changed}"
            )
    return head


def remove_empty_new_parents(root: Path, target: Path, before_head: str) -> None:
    parent = target.parent
    while parent != root and parent.is_dir() and not any(parent.iterdir()):
        parent_relative = parent.relative_to(root).as_posix()
        if run(
            [
                "git",
                "-C",
                str(root),
                "cat-file",
                "-e",
                f"{before_head}:{parent_relative}",
            ],
            check=False,
        ).returncode == 0:
            break
        parent.rmdir()
        parent = parent.parent


def remove_uncommitted_operation(root: Path, operation: dict[str, Any]) -> None:
    for relative in operation["paths"]:
        existed = (
            run(
                [
                    "git",
                    "-C",
                    str(root),
                    "cat-file",
                    "-e",
                    f"{operation['before_head']}:{relative}",
                ],
                check=False,
            ).returncode
            == 0
        )
        if existed:
            git(root, "reset", "-q", "--", relative, check=False)
            target = (root / relative).resolve()
            try:
                target.relative_to(root)
            except ValueError as error:
                raise RunError(f"unsafe cleanup path: {target}") from error
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink()
            remove_empty_new_parents(root, target, operation["before_head"])
            git(root, "checkout", operation["before_head"], "--", relative)
        else:
            git(root, "reset", "-q", "--", relative, check=False)
            target = (root / relative).resolve()
            try:
                target.relative_to(root)
            except ValueError as error:
                raise RunError(f"unsafe cleanup path: {target}") from error
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink()
            remove_empty_new_parents(root, target, operation["before_head"])


def restore_snapshot(snapshot: dict[str, Any], expected_after: dict[str, Any] | None) -> None:
    path = Path(snapshot["path"])
    if expected_after and expected_after.get("exists") and path.exists():
        if hash_bytes(path.read_bytes()) != expected_after["sha256"]:
            raise RunError(f"state effect changed after operation: {path}")
    if snapshot["exists"]:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = base64.b64decode(snapshot["bytes_b64"])
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    elif path.exists():
        path.unlink()


def validate_effects_after(operation: dict[str, Any]) -> None:
    for name, expected in operation.get("effects_after", {}).items():
        if name == "ledger":
            continue
        path = Path(expected["path"])
        if expected["exists"]:
            if not path.is_file() or hash_bytes(path.read_bytes()) != expected["sha256"]:
                raise RunError(f"recorded state effect is missing or changed: {path}")
        elif path.exists():
            raise RunError(f"unexpected state effect appeared after operation: {path}")


def scoped_revert(root: Path, operation: dict[str, Any], commit: str) -> str:
    with tempfile.TemporaryDirectory(
        prefix="curator-revert.", dir=scratch_dir()
    ) as temporary:
        index = Path(temporary) / "index"
        environment = os.environ.copy()
        environment["GIT_INDEX_FILE"] = str(index)
        seeded = subprocess.run(
            ["git", "-C", str(root), "read-tree", "HEAD"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if seeded.returncode:
            raise RunError(f"cannot seed isolated revert index: {seeded.stderr.strip()}")
        result = subprocess.run(
            ["git", "-C", str(root), "revert", "--no-edit", commit],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            subprocess.run(
                ["git", "-C", str(root), "revert", "--abort"],
                env=environment,
                capture_output=True,
                check=False,
            )
            git(root, "checkout", "HEAD", "--", *operation["paths"], check=False)
            raise RunError(f"git revert failed for {commit}: {result.stderr.strip()}")
    # The isolated index protects unrelated staged work. Bring only this
    # operation's paths in the real index forward to the new HEAD.
    git(root, "reset", "-q", "HEAD", "--", *operation["paths"])
    return git(root, "rev-parse", "HEAD")


def reverse_ledger(operation: dict[str, Any]) -> None:
    effect = operation.get("ledger_effect")
    if not effect:
        return
    added = base64.b64decode(effect["bytes_b64"])
    if not added:
        return
    path = Path(effect["path"])
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        current = handle.read()
        offset = effect["offset"]
        if current[offset : offset + len(added)] != added:
            raise RunError(f"recorded ledger effect is missing or changed: {path}")
        updated = current[:offset] + current[offset + len(added) :]
        handle.seek(0)
        handle.write(updated)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())


def reverse_operation(
    manifest_path_value: Path,
    manifest: dict[str, Any],
    operation: dict[str, Any],
    current_roots: dict[str, Path],
) -> None:
    root = current_roots[operation["root"]]
    commit = operation.get("commit")
    if operation["status"] == "intent":
        commit = infer_interrupted_commit(root, operation)
        if commit is None:
            remove_uncommitted_operation(root, operation)
            operation["status"] = "rolled_back"
            operation["rolled_back_at"] = now_iso()
            atomic_write(manifest_path_value, manifest)
            return
        operation["commit"] = commit
        operation["changed_paths"] = commit_paths(root, commit)
        operation["effects_after"] = snapshot_effects(operation)
        operation["ledger_effect"] = record_ledger_effect(operation)
    if operation["status"] not in {"complete", "intent"} or not commit:
        return
    if run(
        ["git", "-C", str(root), "cat-file", "-e", f"{commit}^{{commit}}"],
        check=False,
    ).returncode:
        raise RunError(f"operation commit is missing: {commit}")
    if run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"],
        check=False,
    ).returncode:
        raise RunError(f"operation commit is no longer in current history: {commit}")

    if operation["kind"] == "archive":
        validate_effects_after(operation)
        environment = os.environ.copy()
        environment.pop("SKILLS_CURATOR_RUN_ID", None)
        environment["SKILLS_CURATOR_ROLLBACK"] = manifest["run_id"]
        environment["SKILLS_RESTORE_GIT_ROOT"] = str(root)
        environment["SKILLS_RESTORE_SRC_REL"] = operation["paths"][0]
        environment["SKILLS_RESTORE_SHA"] = operation["before_head"]
        with tempfile.TemporaryDirectory(
            prefix="curator-manifests.", dir=scratch_dir()
        ) as temporary:
            if operation["root"] == "public":
                snapshot_root = Path(temporary)
                for relative in PUBLIC_MANIFESTS:
                    snapshot = operation["effects_before"][f"manifest:{relative}"]
                    if not snapshot["exists"]:
                        raise RunError(f"pre-archive manifest was missing: {relative}")
                    target = snapshot_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(base64.b64decode(snapshot["bytes_b64"]))
                environment["SKILLS_RESTORE_MANIFEST_SNAPSHOT"] = temporary
            result = subprocess.run(
                [str(RESTORE_TOOL), operation["skill"]],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        if result.returncode:
            raise RunError(
                f"archive rollback failed for {operation['skill']}: {result.stderr.strip()}"
            )
    else:
        scoped_revert(root, operation, commit)

    reverse_ledger(operation)
    after = operation.get("effects_after", {})
    for name, snapshot in operation["effects_before"].items():
        if name == "ledger":
            continue
        restore_snapshot(snapshot, None)
    operation["status"] = "rolled_back"
    operation["rollback_commit"] = git(root, "rev-parse", "HEAD")
    operation["rolled_back_at"] = now_iso()
    atomic_write(manifest_path_value, manifest)
    verify_dirty_state(manifest, current_roots)


def command_rollback(args: argparse.Namespace) -> int:
    path, manifest = load_manifest(args.run)
    if manifest["status"] == "rolled_back":
        return 0
    if manifest["status"] not in {
        "active",
        "complete",
        "publish_failed",
        "rollback_failed",
        "rolling_back",
    }:
        raise RunError(f"cannot rollback run in status {manifest['status']}")
    current_roots = verify_root_records(manifest)
    ensure_rollback_lock(manifest)
    atomic_write(path, manifest)
    validate_rollback_dirty(manifest, current_roots)
    publication = manifest.get("publication", {})
    if publication.get("status") in {"publishing", "failed", "published"}:
        public = current_roots["public"]
        verify_recorded_remote_url(public, publication)
        served = remote_head(
            public, publication["remote"], publication["branch"]
        )
        if served == publication["new_head"]:
            previous_status = publication["status"]
            publication["status"] = "published"
            publication["remote_after"] = served
            publication["published_at"] = now_iso()
            if previous_status != "published":
                publication["recovered_during_rollback"] = True
            if previous_status == "publishing":
                publication["recovered_after_interruption"] = True
        elif (
            served == publication["prior_head"]
            and publication["status"] in {"publishing", "failed"}
        ):
            publication["status"] = "failed"
            publication["failed_at"] = now_iso()
            publication["error"] = (
                "publication did not update the remote"
            )
        else:
            raise RunError(
                "publication remote identity changed before rollback"
            )
        atomic_write(path, manifest)
    manifest["status"] = "rolling_back"
    manifest.setdefault("rollback_started_at", now_iso())
    atomic_write(path, manifest)
    try:
        for operation in reversed(manifest["operations"]):
            if operation["status"] in {"complete", "intent"}:
                renew_lock(manifest)
                reverse_operation(path, manifest, operation, current_roots)
        if publication.get("status") == "published":
            public = current_roots["public"]
            verify_recorded_remote_url(public, publication)
            if (
                remote_head(public, publication["remote"], publication["branch"])
                != publication["new_head"]
            ):
                raise RunError("published public identity changed before rollback")
            rollback_head = git(public, "rev-parse", "HEAD")
            result = run(
                [
                    "git",
                    "-C",
                    str(public),
                    "push",
                    publication["remote"],
                    f"{rollback_head}:refs/heads/{publication['branch']}",
                ],
                check=False,
            )
            if result.returncode:
                raise RunError(
                    f"public rollback publication failed: {result.stderr.strip()}"
                )
            if (
                remote_head(public, publication["remote"], publication["branch"])
                != rollback_head
            ):
                raise RunError("public remote does not serve the rollback head")
            publication["rollback_head"] = rollback_head
            publication["rolled_back_at"] = now_iso()
            publication["status"] = "reverted"
        manifest["status"] = "rolled_back"
        manifest["rolled_back_at"] = now_iso()
        atomic_write(path, manifest)
        release_lock(manifest)
        return 0
    except Exception:
        manifest["status"] = "rollback_failed"
        manifest["rollback_failed_at"] = now_iso()
        atomic_write(path, manifest)
        raise


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)

    begin = sub.add_parser("begin")
    begin.add_argument("--plan", required=True)
    begin.add_argument("--report", required=True)
    begin.add_argument("--run-id")
    begin.add_argument("--autonomous", action="store_true")
    begin.set_defaults(func=command_begin)

    renew = sub.add_parser("renew")
    renew.add_argument("--run", required=True)
    renew.set_defaults(func=command_renew)

    archive_context = sub.add_parser("archive-context")
    archive_context.add_argument("--run", required=True)
    archive_context.add_argument("--skill", required=True)
    archive_context.set_defaults(func=command_archive_context)

    intent = sub.add_parser("intent")
    intent.add_argument("--run", required=True)
    intent.add_argument("--kind", choices=("archive", "commit"), required=True)
    intent.add_argument("--root", choices=("public", "local"), required=True)
    intent.add_argument("--skill")
    intent.add_argument("--action", choices=("patch", "create"))
    intent.add_argument("--paths", nargs="*")
    intent.set_defaults(func=command_intent)

    complete = sub.add_parser("complete")
    complete.add_argument("--run", required=True)
    complete.add_argument("--op", required=True)
    complete.set_defaults(func=command_complete)

    commit = sub.add_parser("commit")
    commit.add_argument("--run", required=True)
    commit.add_argument("--op", required=True)
    commit.add_argument("--message-file", required=True)
    commit.set_defaults(func=command_commit)

    publish = sub.add_parser("publish")
    publish.add_argument("--run", required=True)
    publish.add_argument("--remote", default="origin")
    publish.add_argument("--branch", default="main")
    publish.set_defaults(func=command_publish)

    finish = sub.add_parser("finish")
    finish.add_argument("--run", required=True)
    finish.set_defaults(func=command_finish)

    rollback = sub.add_parser("rollback")
    rollback.add_argument("--run", required=True)
    rollback.set_defaults(func=command_rollback)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except (RunError, OSError, ValueError, KeyError) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
