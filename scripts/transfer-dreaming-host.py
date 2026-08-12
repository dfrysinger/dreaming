#!/usr/bin/env python3
"""Capture, install, and compare bounded Dreaming authority state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

REPOSITORY_LABELS = ("dreaming", "public", "local")
CONTENT_ROOT_LABELS = ("shared",)
TRANSFER_ROOT_LABELS = (*REPOSITORY_LABELS, *CONTENT_ROOT_LABELS)
SKILL_STATE_ENTRIES = (
    "curator.json",
    "publisher-ownership.json",
    "reports",
    "skill-review/retired",
    "skill-review/retirement-history",
    "skill-review/tombstones",
    "skill-review/curator-runs",
    "skill-review/evaluations",
)
DREAMING_STATE_ENTRIES = (
    "review-ledger.json",
    "queue.json",
    "unsettled.json",
    "discovery.json",
    "review-attempts.json",
    "review-transactions.json",
    "publisher-ownership.json",
)
EXCLUDED_MACHINE_LOCAL = (
    "dreaming/activation-generation",
    "dreaming/selftest-passed-generation",
    "dreaming/active-selftest-label",
    "dreaming/latest-migration-backup",
    "dreaming/active-migration-backup",
    "dreaming/lifecycle.lock",
    "daemon.lock",
    "daemon.lock-wal",
    "daemon.lock-shm",
)


class TransferError(RuntimeError):
    pass


Identity = dict[str, str | int]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(command: list[str], *, text: bool = True) -> subprocess.CompletedProcess[Any]:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        command,
        capture_output=True,
        text=text,
        check=False,
        env=environment,
    )
    if result.returncode:
        stderr = result.stderr.strip() if text else ""
        raise TransferError(f"{' '.join(command)} failed: {stderr}")
    return result


def git(root: Path, *arguments: str, text: bool = True) -> Any:
    return run(
        [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(root),
            *arguments,
        ],
        text=text,
    ).stdout


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
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
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def path_identity(path: Path, *, reject_symlink: bool = False) -> Identity:
    if path.is_symlink():
        if reject_symlink:
            raise TransferError(f"symlink is not allowed: {path}")
        return {
            "type": "symlink",
            "sha256": sha256(os.readlink(path).encode()),
        }
    if path.is_file():
        return {
            "type": "file",
            "sha256": sha256(path.read_bytes()),
            "mode": stat.S_IMODE(path.stat().st_mode),
        }
    if path.exists():
        raise TransferError(f"authority state target is not a file: {path}")
    raise TransferError(f"filesystem entry does not exist: {path}")


def directory_identity(path: Path) -> Identity:
    if path.is_symlink() or not path.is_dir():
        raise TransferError(f"directory is not a real directory: {path}")
    return {
        "type": "directory",
        "mode": stat.S_IMODE(path.stat().st_mode),
    }


def directory_manifest(root: Path) -> dict[str, Identity]:
    if root.is_symlink() or not root.is_dir():
        raise TransferError(f"manifest root is not a real directory: {root}")
    result = {".": directory_identity(root)}
    for path in sorted(root.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            result[path.relative_to(root).as_posix()] = directory_identity(path)
    return result


def file_manifest(
    root: Path,
    *,
    exclude_git: bool = False,
    reject_symlinks: bool = False,
) -> dict[str, Identity]:
    result: dict[str, Identity] = {}
    if not root.exists():
        return result
    if root.is_symlink() or not root.is_dir():
        raise TransferError(f"manifest root is not a real directory: {root}")
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if exclude_git and relative.parts and relative.parts[0] == ".git":
            continue
        key = relative.as_posix()
        if path.is_symlink():
            result[key] = path_identity(path, reject_symlink=reject_symlinks)
        elif path.is_file():
            result[key] = path_identity(path)
        elif not path.is_dir():
            raise TransferError(f"unsupported filesystem entry: {path}")
    return result


def repository_identity(root: Path) -> dict[str, Any]:
    git_directory = root / ".git"
    if git_directory.is_symlink() or not git_directory.is_dir():
        raise TransferError(
            f"repository must be self-contained; linked worktrees are not supported: {root}"
        )
    alternates = git_directory / "objects" / "info" / "alternates"
    if alternates.exists() or alternates.is_symlink():
        raise TransferError(
            f"repository must be self-contained; object alternates are not supported: {root}"
        )
    top = Path(git(root, "rev-parse", "--show-toplevel").strip()).resolve()
    if top != root.resolve():
        raise TransferError(f"repository root mismatch: {root} -> {top}")
    status = git(root, "status", "--porcelain=v1", "-z", text=False)
    refs = {}
    for line in git(
        root,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)",
        "refs/heads",
        "refs/tags",
    ).splitlines():
        name, commit = line.split("\0", 1)
        refs[name] = commit
    return {
        "path": str(root.resolve()),
        "head": git(root, "rev-parse", "HEAD").strip(),
        "branch": git(root, "branch", "--show-current").strip() or None,
        "refs": refs,
        "status_sha256": sha256(status),
        "worktree": repository_worktree_manifest(root),
        "git_metadata": file_manifest(git_directory, reject_symlinks=True),
        "git_directories": directory_manifest(git_directory),
    }


def repository_worktree_manifest(root: Path) -> dict[str, Identity]:
    listed = git(
        root,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
        text=False,
    )
    result: dict[str, Identity] = {}
    for raw in listed.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8", "surrogateescape")
        path = root / relative
        if path.is_symlink() or path.is_file():
            result[relative] = path_identity(path)
    return result


def copy_repository(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    copy_entry(source / ".git", destination / ".git")
    for relative in repository_worktree_manifest(source):
        copy_entry(source / relative, destination / relative)


def reject_symlink_components(root: Path, relative: Path) -> None:
    if root.is_symlink():
        raise TransferError(f"authority state path is a symlink: {root}")
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise TransferError(f"authority state path is a symlink: {current}")


def selected_state(root: Path, entries: tuple[str, ...]) -> dict[str, Identity]:
    result: dict[str, Identity] = {}
    for entry in entries:
        reject_symlink_components(root, Path(entry))
        path = root / entry
        if path.is_file():
            result[entry] = path_identity(path, reject_symlink=True)
        elif path.is_dir():
            for relative, identity in file_manifest(
                path, reject_symlinks=True
            ).items():
                result[f"{entry}/{relative}"] = identity
    return result


def launch_agent_identity(root: Path, user: str) -> dict[str, Identity]:
    result: dict[str, Identity] = {}
    if root.is_symlink():
        raise TransferError(f"launch agents root is a symlink: {root}")
    for prefix in (f"com.{user}.dreaming.", f"com.{user}.skills."):
        for path in sorted(root.glob(f"{prefix}*.plist")):
            if path.is_symlink() or not path.is_file():
                raise TransferError(f"launch agent is not a regular file: {path}")
            result[path.name] = path_identity(path)
    return result


def absolute_path(value: str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(value)))


def configured_paths(args: argparse.Namespace) -> dict[str, Path]:
    return {
        "dreaming": absolute_path(args.dreaming_root),
        "shared": absolute_path(args.shared_root),
        "public": absolute_path(args.public_root),
        "local": absolute_path(args.local_root),
        "skill_state": absolute_path(args.skill_state_root),
        "dreaming_state": absolute_path(args.dreaming_state_root),
        "launch_agents": absolute_path(args.launch_agents_root),
    }


def capture_payload(args: argparse.Namespace) -> dict[str, Any]:
    paths = configured_paths(args)
    repositories = {
        label: repository_identity(paths[label]) for label in REPOSITORY_LABELS
    }
    return {
        "schema_version": 2,
        "repositories": repositories,
        "content_roots": {
            label: {
                "path": str(paths[label]),
                "files": file_manifest(paths[label]),
                "directories": directory_manifest(paths[label]),
            }
            for label in CONTENT_ROOT_LABELS
        },
        "state": {
            "skill_state": selected_state(paths["skill_state"], SKILL_STATE_ENTRIES),
            "dreaming_state": selected_state(
                paths["dreaming_state"], DREAMING_STATE_ENTRIES
            ),
        },
        "launch_agents": launch_agent_identity(
            paths["launch_agents"], args.launch_agent_user
        ),
        "excluded_machine_local": list(EXCLUDED_MACHINE_LOCAL),
    }


def copy_entry(source: Path, destination: Path) -> None:
    if source.is_symlink():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(os.readlink(source))
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        raise TransferError(f"cannot copy unsupported entry: {source}")


def remove_tree(path: Path, *, ignore_errors: bool = False) -> None:
    if not path.exists():
        return
    try:
        for root, directories, files in os.walk(path, topdown=False):
            for name in files:
                target = Path(root) / name
                if not target.is_symlink():
                    target.chmod(target.stat().st_mode | stat.S_IWUSR)
            for name in directories:
                target = Path(root) / name
                if not target.is_symlink():
                    target.chmod(
                        target.stat().st_mode | stat.S_IWUSR | stat.S_IXUSR
                    )
        path.chmod(path.stat().st_mode | stat.S_IWUSR | stat.S_IXUSR)
        shutil.rmtree(path)
    except OSError:
        if not ignore_errors:
            raise


def command_capture(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle).expanduser().resolve()
    manifest_path = bundle / "manifest.json"
    if bundle.exists():
        if bundle.is_symlink() or any(bundle.iterdir()):
            raise TransferError(f"bundle directory is not empty: {bundle}")
    else:
        bundle.mkdir(parents=True)
    payload = capture_payload(args)
    paths = configured_paths(args)
    try:
        for label in TRANSFER_ROOT_LABELS:
            target = bundle / "roots" / label
            if label in REPOSITORY_LABELS:
                copy_repository(paths[label], target)
            else:
                copy_entry(paths[label], target)
        for root_label, entries in (
            ("skill_state", SKILL_STATE_ENTRIES),
            ("dreaming_state", DREAMING_STATE_ENTRIES),
        ):
            for entry in entries:
                source = paths[root_label] / entry
                if source.exists() or source.is_symlink():
                    copy_entry(source, bundle / "state" / root_label / entry)
        launch_bundle = bundle / "launch-agents"
        launch_bundle.mkdir(parents=True)
        for name in payload["launch_agents"]:
            copy_entry(paths["launch_agents"] / name, launch_bundle / name)
        atomic_json(manifest_path, payload)
        verify_bundle(args)
    except Exception:
        remove_tree(bundle, ignore_errors=True)
        raise
    print(manifest_path)
    return 0


def load_manifest(bundle: Path) -> dict[str, Any]:
    path = bundle / "manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TransferError(f"cannot load transfer manifest: {error}") from error
    if payload.get("schema_version") != 2:
        raise TransferError("unsupported transfer manifest")
    return payload


def compare_payload(expected: dict[str, Any], actual: dict[str, Any]) -> None:
    comparable_actual = json.loads(json.dumps(actual))
    comparable_expected = json.loads(json.dumps(expected))
    for payload in (comparable_actual, comparable_expected):
        for repository in payload["repositories"].values():
            repository.pop("path", None)
        for content_root in payload["content_roots"].values():
            content_root.pop("path", None)
    if comparable_actual != comparable_expected:
        difference = first_difference(comparable_expected, comparable_actual)
        raise TransferError(f"authority manifest does not match at {difference}")


def first_difference(expected: Any, actual: Any, path: str = "$") -> str:
    if type(expected) is not type(actual):
        return path
    if isinstance(expected, dict):
        if expected.keys() != actual.keys():
            differing = sorted(set(expected) ^ set(actual))
            return f"{path}.{differing[0]}"
        for key in expected:
            difference = first_difference(
                expected[key], actual[key], f"{path}.{key}"
            )
            if difference:
                return difference
        return ""
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path}.length"
        for index, (expected_item, actual_item) in enumerate(
            zip(expected, actual)
        ):
            difference = first_difference(
                expected_item, actual_item, f"{path}[{index}]"
            )
            if difference:
                return difference
        return ""
    return "" if expected == actual else path


def bundle_args(args: argparse.Namespace, bundle: Path) -> argparse.Namespace:
    values = vars(args).copy()
    for label in TRANSFER_ROOT_LABELS:
        values[f"{label}_root"] = str(bundle / "roots" / label)
    values["skill_state_root"] = str(bundle / "state" / "skill_state")
    values["dreaming_state_root"] = str(bundle / "state" / "dreaming_state")
    values["launch_agents_root"] = str(bundle / "launch-agents")
    values["launch_agent_user"] = args.launch_agent_user
    return argparse.Namespace(**values)


def verify_bundle(args: argparse.Namespace) -> dict[str, Any]:
    bundle = Path(args.bundle).expanduser().resolve()
    expected = load_manifest(bundle)
    validate_bundle_files(bundle, expected)
    actual = capture_payload(bundle_args(args, bundle))
    compare_payload(expected, actual)
    return expected


def command_verify_bundle(args: argparse.Namespace) -> int:
    verify_bundle(args)
    print("bundle verified")
    return 0


def validate_bundle_files(bundle: Path, expected: dict[str, Any]) -> None:
    for label in REPOSITORY_LABELS:
        root = bundle / "roots" / label
        repository = expected["repositories"][label]
        if file_manifest(root / ".git", reject_symlinks=True) != repository[
            "git_metadata"
        ]:
            raise TransferError(
                f"bundle Git metadata does not match manifest: {label}"
            )
        if directory_manifest(root / ".git") != repository["git_directories"]:
            raise TransferError(
                f"bundle Git directory metadata does not match manifest: {label}"
            )
        if file_manifest(root, exclude_git=True) != repository["worktree"]:
            raise TransferError(
                f"bundle repository files do not match manifest: {label}"
            )
    for label in CONTENT_ROOT_LABELS:
        if file_manifest(bundle / "roots" / label) != expected["content_roots"][
            label
        ]["files"]:
            raise TransferError(
                f"bundle content root does not match manifest: {label}"
            )
        if directory_manifest(bundle / "roots" / label) != expected[
            "content_roots"
        ][label]["directories"]:
            raise TransferError(
                f"bundle content directories do not match manifest: {label}"
            )
    for root_label in ("skill_state", "dreaming_state"):
        if file_manifest(
            bundle / "state" / root_label, reject_symlinks=True
        ) != expected["state"][root_label]:
            raise TransferError(
                f"bundle authority state does not match manifest: {root_label}"
            )
    if file_manifest(
        bundle / "launch-agents", reject_symlinks=True
    ) != expected["launch_agents"]:
        raise TransferError("bundle LaunchAgents do not match manifest")


def ensure_install_target(path: Path) -> None:
    if path.is_symlink():
        raise TransferError(f"install target is a symlink: {path}")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise TransferError(f"install target is not empty: {path}")


def merge_state(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir() and not path.is_symlink():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if path.is_symlink():
            raise TransferError(f"authority state entry is a symlink: {path}")
        reject_symlink_components(destination, relative)
        if target.exists() or target.is_symlink():
            if path_identity(path) != path_identity(target, reject_symlink=True):
                raise TransferError(f"state destination conflicts: {target}")
            continue
        copy_entry(path, target)


def command_compare(args: argparse.Namespace) -> int:
    expected = verify_bundle(args)
    actual = capture_payload(args)
    compare_payload(expected, actual)
    print("authority manifest matches")
    return 0


def entry_identity(path: Path) -> Identity | None:
    if path.is_symlink() or path.is_file():
        return path_identity(path)
    if path.exists():
        raise TransferError(f"authority state target is not a file: {path}")
    return None


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        remove_tree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def restore_path(path: Path, backup: Path | None) -> None:
    if backup is not None:
        if not (backup.exists() or backup.is_symlink()):
            raise TransferError(f"rollback backup is missing: {backup}")
        remove_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        backup.rename(path)
    else:
        remove_path(path)


def test_failpoint(name: str) -> None:
    if os.environ.get("TRANSFER_DREAMING_HOST_TEST_FAIL") == name:
        raise OSError(f"injected transfer failure: {name}")


def sibling_path(path: Path, purpose: str) -> Path:
    return path.parent / f".{path.name}.{purpose}-{uuid.uuid4().hex}"


def ensure_state_parent(
    root: Path, target: Path, created_directories: list[Path]
) -> None:
    relative_parent = target.parent.relative_to(root)
    current = root
    if current.is_symlink():
        raise TransferError(f"authority state path is a symlink: {current}")
    if not current.exists():
        current.mkdir()
        created_directories.append(current)
    elif not current.is_dir():
        raise TransferError(f"authority state parent is not a directory: {current}")
    for part in relative_parent.parts:
        current /= part
        if current.is_symlink():
            raise TransferError(f"authority state path is a symlink: {current}")
        if not current.exists():
            current.mkdir()
            created_directories.append(current)
        elif not current.is_dir():
            raise TransferError(
                f"authority state parent is not a directory: {current}"
            )


def state_install_plan(
    source: Path,
    destination: Path,
    entries: dict[str, Identity],
) -> list[tuple[str, Path, Path]]:
    plan: list[tuple[str, Path, Path]] = []
    for relative_text in sorted(entries):
        relative = Path(relative_text)
        path = source / relative
        reject_symlink_components(destination, relative)
        target = destination / relative
        if target.exists() or target.is_symlink():
            if path_identity(path) != path_identity(target, reject_symlink=True):
                raise TransferError(f"state destination conflicts: {target}")
            continue
        plan.append((relative_text, path, target))
    return plan


def rollback_transaction(
    backups: list[tuple[Path, Path | None]],
    staged_paths: list[Path],
    created_directories: list[Path],
) -> list[str]:
    errors: list[str] = []
    for target, backup in reversed(backups):
        try:
            restore_path(target, backup)
        except (OSError, TransferError) as error:
            errors.append(str(error))
    for staged in reversed(staged_paths):
        try:
            remove_path(staged)
        except OSError as error:
            errors.append(str(error))
    for directory in reversed(created_directories):
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError as error:
            errors.append(str(error))
    return errors


def cleanup_committed(
    operation: str,
    backups: list[tuple[Path, Path | None]],
    staged_paths: list[Path],
) -> None:
    try:
        test_failpoint(f"{operation}-cleanup")
        for _, backup in backups:
            if backup is not None:
                remove_path(backup)
        for staged in staged_paths:
            remove_path(staged)
    except OSError as error:
        print(
            f"WARNING: {operation} committed but transaction cleanup failed: {error}",
            file=sys.stderr,
        )


def command_install(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle).expanduser().resolve()
    manifest = verify_bundle(args)
    destinations = configured_paths(args)
    for label in TRANSFER_ROOT_LABELS:
        ensure_install_target(destinations[label])
    selected_state(destinations["skill_state"], SKILL_STATE_ENTRIES)
    selected_state(destinations["dreaming_state"], DREAMING_STATE_ENTRIES)
    state_plans = {
        root_label: state_install_plan(
            bundle / "state" / root_label,
            destinations[root_label],
            manifest["state"][root_label],
        )
        for root_label in ("skill_state", "dreaming_state")
    }
    staged_paths: list[Path] = []
    backups: list[tuple[Path, Path | None]] = []
    created_directories: list[Path] = []
    try:
        staged_roots: dict[str, Path] = {}
        for label in TRANSFER_ROOT_LABELS:
            destination = destinations[label]
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged = sibling_path(destination, "install")
            staged_paths.append(staged)
            if label in REPOSITORY_LABELS:
                copy_repository(bundle / "roots" / label, staged)
            else:
                copy_entry(bundle / "roots" / label, staged)
            staged_roots[label] = staged
        for label in TRANSFER_ROOT_LABELS:
            destination = destinations[label]
            backup: Path | None = None
            if destination.exists():
                backup = sibling_path(destination, "backup")
                destination.rename(backup)
            backups.append((destination, backup))
            staged_roots[label].rename(destination)
            staged_paths.remove(staged_roots[label])
        for root_label, plan in state_plans.items():
            state_root = destinations[root_label]
            for relative, source, target in plan:
                ensure_state_parent(state_root, target, created_directories)
                staged = sibling_path(target, "install")
                staged_paths.append(staged)
                copy_entry(source, staged)
                backups.append((target, None))
                staged.rename(target)
                staged_paths.remove(staged)
                test_failpoint(f"install-after-state:{root_label}/{relative}")
    except Exception as error:
        rollback_errors = rollback_transaction(
            backups, staged_paths, created_directories
        )
        if rollback_errors:
            raise TransferError(
                f"{error}; rollback incomplete: {'; '.join(rollback_errors)}"
            ) from error
        raise
    cleanup_committed("install", backups, staged_paths)
    print("bundle installed")
    return 0


def command_synchronize(args: argparse.Namespace) -> int:
    if not args.prior_bundle:
        raise TransferError("synchronize requires --prior-bundle")
    bundle = Path(args.bundle).expanduser().resolve()
    prior_bundle = Path(args.prior_bundle).expanduser().resolve()
    prior_args = argparse.Namespace(**vars(args))
    prior_args.bundle = str(prior_bundle)
    prior = verify_bundle(prior_args)
    expected = verify_bundle(args)
    current = capture_payload(args)
    compare_payload(prior, current)
    destinations = configured_paths(args)
    backups: list[tuple[Path, Path | None]] = []
    state_backups: list[tuple[Path, Path | None]] = []
    staged_paths: list[Path] = []
    created_directories: list[Path] = []
    try:
        root_changes: list[tuple[str, Path, Path]] = []
        for label in TRANSFER_ROOT_LABELS:
            if label in REPOSITORY_LABELS:
                previous_identity = json.loads(
                    json.dumps(prior["repositories"][label])
                )
                next_identity = json.loads(
                    json.dumps(expected["repositories"][label])
                )
                previous_identity.pop("path", None)
                next_identity.pop("path", None)
            else:
                previous_identity = json.loads(
                    json.dumps(prior["content_roots"][label])
                )
                next_identity = json.loads(
                    json.dumps(expected["content_roots"][label])
                )
                previous_identity.pop("path", None)
                next_identity.pop("path", None)
            if previous_identity == next_identity:
                continue
            source = bundle / "roots" / label
            destination = destinations[label]
            staged = sibling_path(destination, "sync")
            staged_paths.append(staged)
            if label in REPOSITORY_LABELS:
                copy_repository(source, staged)
            else:
                copy_entry(source, staged)
            root_changes.append((label, destination, staged))
        for label, destination, staged in root_changes:
            backup = sibling_path(destination, "backup")
            destination.rename(backup)
            backups.append((destination, backup))
            test_failpoint(f"sync-replacement-rename:{label}")
            staged.rename(destination)
            staged_paths.remove(staged)
        for root_label in ("skill_state", "dreaming_state"):
            prior_entries = prior["state"][root_label]
            next_entries = expected["state"][root_label]
            for relative in sorted(set(prior_entries) | set(next_entries)):
                if prior_entries.get(relative) == next_entries.get(relative):
                    continue
                target = destinations[root_label] / relative
                reject_symlink_components(
                    destinations[root_label], Path(relative)
                )
                if entry_identity(target) != prior_entries.get(relative):
                    raise TransferError(
                        f"authority state changed since prior capture: {target}"
                    )
                source = bundle / "state" / root_label / relative
                staged: Path | None = None
                if relative in next_entries:
                    ensure_state_parent(
                        destinations[root_label], target, created_directories
                    )
                    staged = sibling_path(target, "sync")
                    staged_paths.append(staged)
                    copy_entry(source, staged)
                backup: Path | None = None
                if target.exists() or target.is_symlink():
                    backup = sibling_path(target, "backup")
                    target.rename(backup)
                state_backups.append((target, backup))
                if staged is not None:
                    staged.rename(target)
                    staged_paths.remove(staged)
                test_failpoint(f"sync-after-state:{root_label}/{relative}")
    except Exception as error:
        rollback_errors = rollback_transaction(
            [*backups, *state_backups],
            staged_paths,
            created_directories,
        )
        if rollback_errors:
            raise TransferError(
                f"{error}; rollback incomplete: {'; '.join(rollback_errors)}"
            ) from error
        raise
    cleanup_committed(
        "sync",
        [*backups, *state_backups],
        staged_paths,
    )
    print("authority roots and state synchronized")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("--bundle", required=True)
    root.add_argument("--prior-bundle")
    root.add_argument("--dreaming-root", required=True)
    root.add_argument("--shared-root", required=True)
    root.add_argument("--public-root", required=True)
    root.add_argument("--local-root", required=True)
    root.add_argument("--skill-state-root", required=True)
    root.add_argument("--dreaming-state-root", required=True)
    root.add_argument("--launch-agents-root", required=True)
    root.add_argument("--launch-agent-user", required=True)
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("capture").set_defaults(func=command_capture)
    sub.add_parser("verify-bundle").set_defaults(func=command_verify_bundle)
    sub.add_parser("install").set_defaults(func=command_install)
    sub.add_parser("compare").set_defaults(func=command_compare)
    sub.add_parser("synchronize").set_defaults(func=command_synchronize)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except (TransferError, OSError, ValueError, KeyError) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
