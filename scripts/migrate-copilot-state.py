#!/usr/bin/env python3
"""Reversibly adopt Copilot-scoped Dreaming state into neutral roots."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

STATE_FILES = (
    "review-ledger.json",
    "queue.json",
    "unsettled.json",
    "discovery.json",
    "review-attempts.json",
    "review-transactions.json",
)


class MigrationError(RuntimeError):
    pass


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}"
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def git_output(root: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise MigrationError(f"git verification failed for {root}: {detail}") from error


def is_empty_unborn_repository(root: Path) -> bool:
    if not root.is_dir() or {path.name for path in root.iterdir()} != {".git"}:
        return False
    if not (root / ".git").is_dir():
        return False
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    remotes = subprocess.run(
        ["git", "-C", str(root), "remote"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return (
        status.returncode == 0
        and not status.stdout.strip()
        and head.returncode != 0
        and remotes.returncode == 0
        and not remotes.stdout.strip()
    )


def source_manifest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise MigrationError(f"legacy skills root contains a symlink: {path}")
        if path.is_file() and ".git" not in path.relative_to(root).parts:
            result[path.relative_to(root).as_posix()] = hash_file(path)
    return result


def verify_clean_repository(root: Path) -> tuple[str, dict[str, str]]:
    if not (root / ".git").is_dir():
        raise MigrationError(f"legacy skills root is not a Git repository: {root}")
    if git_output(root, "status", "--porcelain"):
        raise MigrationError(f"legacy skills repository is not clean: {root}")
    revision = git_output(root, "rev-parse", "HEAD")
    git_output(root, "fsck", "--no-dangling")
    return revision, source_manifest(root)


def requested_paths(
    legacy_skills: Path,
    legacy_state: Path,
    target_skills: Path,
    target_state: Path,
) -> dict[str, str]:
    return {
        "legacy_skills": str(legacy_skills),
        "legacy_state": str(legacy_state),
        "target_skills": str(target_skills),
        "target_state": str(target_state),
    }


def verify_journal_paths(journal: dict[str, Any], requested: dict[str, str]) -> None:
    mismatches = [
        name
        for name, value in requested.items()
        if journal.get(name) != value
    ]
    if mismatches:
        raise MigrationError(
            "migration journal belongs to different requested paths: "
            + ", ".join(mismatches)
        )


def clean_staging(journal: dict[str, Any], journal_path: Path) -> None:
    raw = journal.get("staging_root")
    if not raw:
        return
    staging = Path(raw)
    if staging.is_symlink():
        raise MigrationError(f"unsafe migration staging path in journal: {staging}")
    canonical_staging = staging.resolve()
    canonical_parent = journal_path.parent.resolve()
    if (
        canonical_staging.parent != canonical_parent
        or not canonical_staging.name.startswith(".migration-stage-")
    ):
        raise MigrationError(f"unsafe migration staging path in journal: {staging}")
    shutil.rmtree(canonical_staging, ignore_errors=True)


def prepare(
    legacy_skills: Path,
    legacy_state: Path,
    target_skills: Path,
    target_state: Path,
    journal_path: Path,
) -> dict[str, Any]:
    existing = json.loads(journal_path.read_text()) if journal_path.exists() else None
    paths = requested_paths(
        legacy_skills, legacy_state, target_skills, target_state
    )
    if existing is not None and not isinstance(existing, dict):
        raise MigrationError("migration journal must be a JSON object")
    if isinstance(existing, dict):
        verify_journal_paths(existing, paths)
        status = existing.get("status")
        if status in {"active", "verified"}:
            return existing
        if status in {"preparing", "failed", "rolled-back"}:
            clean_staging(existing, journal_path)
        else:
            raise MigrationError(f"unsupported migration journal status: {status}")
    journal: dict[str, Any] = {
        "version": 1,
        "status": "preparing",
        **paths,
        "skills": None,
        "state_files": [],
    }
    staging_root = journal_path.parent / f".migration-stage-{uuid.uuid4().hex}"
    staging_root.mkdir(parents=True)
    journal["staging_root"] = str(staging_root)
    atomic_json(journal_path, journal)
    try:
        if legacy_skills.exists():
            revision, manifest = verify_clean_repository(legacy_skills)
            should_stage_skills = True
            if target_skills.exists() and any(target_skills.iterdir()):
                if is_empty_unborn_repository(target_skills):
                    pass
                elif not (target_skills / ".git").is_dir():
                    raise MigrationError(
                        f"neutral skills root already contains foreign data: {target_skills}"
                    )
                else:
                    target_revision, target_manifest = verify_clean_repository(
                        target_skills
                    )
                    if target_revision != revision or target_manifest != manifest:
                        raise MigrationError(
                            "neutral skills root differs from the legacy repository"
                        )
                    should_stage_skills = False
            if should_stage_skills:
                staged_skills = staging_root / "skills"
                shutil.copytree(legacy_skills, staged_skills)
                staged_revision, staged_manifest = verify_clean_repository(staged_skills)
                if staged_revision != revision or staged_manifest != manifest:
                    raise MigrationError("staged skills repository verification failed")
                journal["skills"] = {
                    "revision": revision,
                    "manifest": manifest,
                    "staged": str(staged_skills),
                    "created": True,
                }
        for name in STATE_FILES:
            source = legacy_state / name
            destination = target_state / name
            if not source.is_file():
                continue
            source_hash = hash_file(source)
            if destination.exists():
                if hash_file(destination) != source_hash:
                    raise MigrationError(
                        f"neutral state conflicts with legacy state: {destination}"
                    )
                continue
            staged = staging_root / "state" / name
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged)
            if hash_file(staged) != source_hash:
                raise MigrationError(f"staged state verification failed: {name}")
            journal["state_files"].append(
                {
                    "name": name,
                    "sha256": source_hash,
                    "staged": str(staged),
                    "created": True,
                }
            )
        journal["status"] = "verified"
        atomic_json(journal_path, journal)
        return journal
    except Exception:
        journal["status"] = "failed"
        atomic_json(journal_path, journal)
        raise


def activate(journal: dict[str, Any], journal_path: Path) -> dict[str, Any]:
    if journal.get("status") == "active":
        return journal
    if journal.get("status") != "verified":
        raise MigrationError("migration journal is not verified")
    target_skills = Path(journal["target_skills"])
    skills = journal.get("skills")
    if skills and skills.get("created"):
        staged_skills: Path | None = Path(skills["staged"])
        if staged_skills.is_dir():
            staged_revision, staged_manifest = verify_clean_repository(staged_skills)
            if (
                staged_revision != skills["revision"]
                or staged_manifest != skills["manifest"]
            ):
                raise MigrationError("staged skills repository verification failed")
        target_skills.parent.mkdir(parents=True, exist_ok=True)
        if target_skills.exists() and any(target_skills.iterdir()):
            if is_empty_unborn_repository(target_skills):
                if not staged_skills.is_dir():
                    raise MigrationError("staged skills missing before activation")
                shutil.rmtree(target_skills)
            else:
                revision, manifest = verify_clean_repository(target_skills)
                if revision != skills["revision"] or manifest != skills["manifest"]:
                    raise MigrationError("neutral skills root changed before activation")
                staged_skills = None
        elif target_skills.exists():
            target_skills.rmdir()
        if staged_skills is not None:
            if not staged_skills.is_dir():
                raise MigrationError("staged skills missing before activation")
            os.replace(staged_skills, target_skills)
    target_state = Path(journal["target_state"])
    target_state.mkdir(parents=True, exist_ok=True)
    for item in journal.get("state_files", []):
        destination = target_state / item["name"]
        if destination.exists():
            if hash_file(destination) != item["sha256"]:
                raise MigrationError(
                    f"neutral state changed before activation: {destination}"
                )
            continue
        staged = Path(item["staged"])
        if not staged.is_file():
            raise MigrationError(f"staged state missing before activation: {item['name']}")
        os.replace(staged, destination)
    journal["status"] = "active"
    journal["source_retained_for_rollback"] = True
    atomic_json(journal_path, journal)
    clean_staging(journal, journal_path)
    return journal


def rollback(
    legacy_skills: Path,
    legacy_state: Path,
    target_skills: Path,
    target_state: Path,
    journal_path: Path,
) -> dict[str, Any]:
    if not journal_path.exists():
        return {"status": "absent"}
    journal = json.loads(journal_path.read_text())
    if not isinstance(journal, dict):
        raise MigrationError("migration journal must be a JSON object")
    verify_journal_paths(
        journal,
        requested_paths(
            legacy_skills, legacy_state, target_skills, target_state
        ),
    )
    if journal.get("status") == "rolled-back":
        return journal
    if journal.get("status") == "active":
        skills = journal.get("skills")
        target_skills = Path(journal["target_skills"])
        remove_skills = bool(skills and skills.get("created"))
        if remove_skills:
            if not target_skills.exists():
                raise MigrationError(
                    "neutral skills missing after migration; rollback requires repair"
                )
            revision, manifest = verify_clean_repository(target_skills)
            if revision != skills["revision"] or manifest != skills["manifest"]:
                raise MigrationError(
                    "neutral skills changed after migration; rollback requires repair"
                )
        target_state = Path(journal["target_state"])
        state_to_remove: list[Path] = []
        for item in journal.get("state_files", []):
            destination = target_state / item["name"]
            if not destination.is_file():
                raise MigrationError(
                    f"neutral state missing after migration: {destination}"
                )
            if hash_file(destination) != item["sha256"]:
                raise MigrationError(
                    f"neutral state changed after migration: {destination}"
                )
            state_to_remove.append(destination)
        if remove_skills:
            shutil.rmtree(target_skills)
        for destination in state_to_remove:
            destination.unlink()
    clean_staging(journal, journal_path)
    journal["status"] = "rolled-back"
    atomic_json(journal_path, journal)
    return journal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("apply", "rollback", "status"))
    parser.add_argument("--legacy-skills", required=True)
    parser.add_argument("--legacy-state", required=True)
    parser.add_argument("--target-skills", required=True)
    parser.add_argument("--target-state", required=True)
    parser.add_argument("--journal", required=True)
    args = parser.parse_args()
    journal_path = Path(args.journal).expanduser().resolve()
    try:
        if args.command == "apply":
            journal = prepare(
                Path(args.legacy_skills).expanduser().resolve(),
                Path(args.legacy_state).expanduser().resolve(),
                Path(args.target_skills).expanduser().resolve(),
                Path(args.target_state).expanduser().resolve(),
                journal_path,
            )
            result = activate(journal, journal_path)
        elif args.command == "rollback":
            result = rollback(
                Path(args.legacy_skills).expanduser().resolve(),
                Path(args.legacy_state).expanduser().resolve(),
                Path(args.target_skills).expanduser().resolve(),
                Path(args.target_state).expanduser().resolve(),
                journal_path,
            )
        else:
            result = (
                json.loads(journal_path.read_text())
                if journal_path.exists()
                else {"status": "absent"}
            )
    except (MigrationError, OSError, json.JSONDecodeError) as error:
        print(f"migrate-copilot-state.py: {error}", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
