#!/usr/bin/env python3
"""Verify and materialize Dreaming's immutable shared-skill bundle."""

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

PROTOCOL_VERSION = 1
PLUGIN_NAME = "dfrysinger-skills"
SHARED_SKILLS = (
    "writing-great-skills",
    "dual-review",
    "authenticated-browse",
)
PLACEHOLDER_REVISION = "__DREAMING_PINNED_SKILLS_REVISION__"
PLACEHOLDER_FILES = "__DREAMING_SHARED_SKILL_FILE_HASHES__"


class DependencyError(RuntimeError):
    pass


def canonical(path: Path) -> Path:
    return path.expanduser().resolve()


def repo_root() -> Path:
    return canonical(
        Path(os.environ.get("DREAMING_REPO_ROOT", Path(__file__).resolve().parent.parent))
    )


def receipt_path() -> Path:
    return canonical(
        Path(
            os.environ.get(
                "DREAMING_RECEIPT_FILE", repo_root() / "scripts/shared-deps-receipt.json"
            )
        )
    )


def load_receipt(path: Path | None = None) -> dict[str, Any]:
    target = path or receipt_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DependencyError(f"cannot read compatibility receipt {target}: {exc}") from exc
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise DependencyError(
            f"shared-skill protocol mismatch: expected {PROTOCOL_VERSION}, "
            f"got {payload.get('protocol_version')!r}"
        )
    revision = payload.get("pinned_revision")
    files = payload.get("files")
    if revision == PLACEHOLDER_REVISION or files == PLACEHOLDER_FILES:
        raise DependencyError(
            "shared dependency receipt still contains the release placeholder"
        )
    if (
        not isinstance(revision, str)
        or len(revision) != 40
        or any(char not in "0123456789abcdef" for char in revision)
    ):
        raise DependencyError("compatibility receipt pinned revision must be a full commit SHA")
    if not isinstance(files, dict) or not files:
        raise DependencyError("compatibility receipt files must be a non-empty object")
    normalized: dict[str, str] = {}
    for relative, digest in files.items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise DependencyError("compatibility receipt paths and hashes must be strings")
        path_value = Path(relative)
        if path_value.is_absolute() or ".." in path_value.parts:
            raise DependencyError(f"invalid receipt path: {relative}")
        if len(path_value.parts) < 3 or path_value.parts[:1] != ("skills",):
            raise DependencyError(f"receipt path is outside skills/: {relative}")
        if path_value.parts[1] not in SHARED_SKILLS:
            raise DependencyError(f"receipt includes an unowned skill: {relative}")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise DependencyError(f"invalid SHA-256 for {relative}")
        normalized[path_value.as_posix()] = digest
    payload["files"] = dict(sorted(normalized.items()))
    return payload


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for skill in SHARED_SKILLS:
        directory = root / "skills" / skill
        if not (directory / "SKILL.md").is_file():
            raise DependencyError(f"incomplete shared source: missing {directory / 'SKILL.md'}")
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise DependencyError(f"shared dependency source contains symlink: {path}")
            if path.is_file():
                result[path.relative_to(root).as_posix()] = hash_file(path)
    return result


def verify_root(root: Path, receipt: dict[str, Any], label: str) -> None:
    actual = source_files(root)
    expected = receipt["files"]
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    skew = sorted(path for path in set(actual) & set(expected) if actual[path] != expected[path])
    if missing or extra or skew:
        details = []
        if missing:
            details.append(f"missing={missing}")
        if extra:
            details.append(f"unexpected={extra}")
        if skew:
            details.append(f"hash_skew={skew}")
        raise DependencyError(f"{label} does not match compatibility receipt: {'; '.join(details)}")


def verify_bundle_layout(bundle: Path, receipt: dict[str, Any]) -> None:
    symlinks = [str(path) for path in bundle.rglob("*") if path.is_symlink()]
    if symlinks:
        raise DependencyError(f"immutable bundle contains symlinks: {symlinks}")
    allowed = set(receipt["files"]) | {".claude-plugin/plugin.json"}
    actual = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != allowed:
        raise DependencyError(
            "immutable bundle layout mismatch: "
            f"missing={sorted(allowed - actual)} unexpected={sorted(actual - allowed)}"
        )


def make_immutable(bundle: Path) -> None:
    for path in sorted(bundle.rglob("*"), reverse=True):
        mode = path.stat().st_mode & 0o777
        os.chmod(path, mode & ~0o222)
    os.chmod(bundle, bundle.stat().st_mode & 0o555)


def complete(root: Path) -> bool:
    return all((root / "skills" / name / "SKILL.md").is_file() for name in SHARED_SKILLS)


def installed_sources(installed_root: Path) -> list[Path]:
    found: list[Path] = []
    if not installed_root.is_dir():
        return found
    for manifest in sorted(installed_root.glob("**/.claude-plugin/plugin.json")):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("name") != PLUGIN_NAME:
            continue
        root = canonical(manifest.parent.parent)
        if complete(root):
            found.append(root)
    unique = sorted(set(found))
    if len(unique) > 1:
        raise DependencyError(
            "multiple complete installed dfrysinger-skills plugins found: "
            + ", ".join(map(str, unique))
        )
    return unique


def ensure_distinct(left: Path, right: Path, description: str) -> None:
    if canonical(left) == canonical(right):
        raise DependencyError(f"canonical root alias rejected: {description}: {left}")


def sparse_source(receipt: dict[str, Any], deps_dir: Path) -> Path:
    revision = receipt["pinned_revision"]
    url = os.environ.get(
        "DREAMING_SPARSE_REPO_URL", "https://github.com/dfrysinger/skills.git"
    )
    git = os.environ.get("GIT_BIN", "git")
    work = deps_dir / f".sparse-source-{os.getpid()}-{uuid.uuid4().hex}"
    try:
        subprocess.run(
            [git, "clone", "--filter=blob:none", "--no-checkout", url, str(work)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        subprocess.run(
            [git, "-C", str(work), "sparse-checkout", "set"]
            + [f"skills/{name}" for name in SHARED_SKILLS],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        subprocess.run(
            [git, "-C", str(work), "checkout", "--detach", revision],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        actual_revision = subprocess.check_output(
            [git, "-C", str(work), "rev-parse", "HEAD"], text=True
        ).strip()
        if actual_revision != revision:
            raise DependencyError(
                f"sparse checkout revision mismatch: expected {revision}, got {actual_revision}"
            )
        skill_dirs = {
            path.name for path in (work / "skills").iterdir() if path.is_dir()
        }
        if skill_dirs != set(SHARED_SKILLS):
            raise DependencyError(
                f"sparse checkout materialized unexpected skill paths: {sorted(skill_dirs)}"
            )
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(work, ignore_errors=True)
        detail = (exc.stderr or "").strip()
        raise DependencyError(f"sparse dependency checkout failed: {detail}") from exc
    except (DependencyError, OSError) as exc:
        shutil.rmtree(work, ignore_errors=True)
        if isinstance(exc, DependencyError):
            raise
        raise DependencyError(f"sparse dependency checkout inspection failed: {exc}") from exc
    return work


def candidate_rejection(
    root: Path, receipt: dict[str, Any], label: str
) -> str | None:
    try:
        verify_root(root, receipt, label)
        return None
    except DependencyError as exc:
        return str(exc)


def resolve_source(
    receipt: dict[str, Any], deps_dir: Path
) -> tuple[str, Path, bool, list[str]]:
    explicit = os.environ.get("DREAMING_DEPS_SOURCE")
    if explicit:
        root = canonical(Path(explicit))
        if not complete(root):
            raise DependencyError(f"explicit shared dependency source is incomplete: {root}")
        verify_root(root, receipt, "explicit source")
        return "explicit", root, False, []

    rejected: list[str] = []
    canonical_value = os.environ.get(
        "DREAMING_CANONICAL_SKILLS_ROOT", str(Path.home() / "code/skills")
    )
    if canonical_value:
        root = canonical(Path(canonical_value))
        if complete(root):
            rejection = candidate_rejection(root, receipt, "canonical source")
            if rejection is None:
                return "canonical", root, False, rejected
            rejected.append(rejection)

    installed_value = os.environ.get(
        "DREAMING_INSTALLED_PLUGINS_ROOT", str(Path.home() / ".copilot/installed-plugins")
    )
    candidates = installed_sources(canonical(Path(installed_value)))
    if candidates:
        rejection = candidate_rejection(candidates[0], receipt, "installed source")
        if rejection is None:
            return "installed", candidates[0], False, rejected
        rejected.append(rejection)

    try:
        source = sparse_source(receipt, deps_dir)
    except DependencyError as exc:
        details = "; ".join([*rejected, str(exc)])
        raise DependencyError(f"no compatible shared dependency source: {details}") from exc
    selected = False
    try:
        rejection = candidate_rejection(source, receipt, "sparse source")
        if rejection is not None:
            details = "; ".join([*rejected, rejection])
            raise DependencyError(f"no compatible shared dependency source: {details}")
        selected = True
        return "sparse", source, True, rejected
    finally:
        if not selected:
            shutil.rmtree(source, ignore_errors=True)


def bundle_identity(receipt: dict[str, Any]) -> str:
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}"
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def discover_catalog(source_kind: str, source: Path) -> tuple[Path | None, str]:
    explicit = os.environ.get("SKILLS_REPO_ROOT")
    if explicit:
        root = canonical(Path(explicit))
        if not (root / "skills").is_dir():
            raise DependencyError(f"SKILLS_REPO_ROOT has no skills directory: {root}")
        return root, "managed" if (root / ".git").is_dir() and os.access(root, os.W_OK) else "read-only"
    canonical_root = canonical(Path.home() / "code/skills")
    if (canonical_root / "skills").is_dir():
        return canonical_root, (
            "managed"
            if (canonical_root / ".git").is_dir() and os.access(canonical_root, os.W_OK)
            else "read-only"
        )
    if source_kind == "installed":
        return source, "read-only"
    return None, "unavailable"


def materialize() -> dict[str, Any]:
    receipt = load_receipt()
    root = repo_root()
    deps_dir = canonical(
        Path(os.environ.get("DREAMING_DEPS_DIR", Path.home() / ".copilot/dreaming/deps"))
    )
    config = canonical(
        Path(
            os.environ.get(
                "DREAMING_CONFIG_FILE", Path.home() / ".copilot/dreaming/config.env"
            )
        )
    )
    deps_dir.mkdir(parents=True, exist_ok=True)
    source_kind, source, temporary_source, rejected_sources = resolve_source(
        receipt, deps_dir
    )
    try:
        ensure_distinct(source, root, "shared source and dreaming repository")
        verify_root(source, receipt, f"{source_kind} source")
        identity = bundle_identity(receipt)
        bundles = deps_dir / "bundles"
        bundle = bundles / identity
        ensure_distinct(source, bundle, "shared source and immutable bundle")
        public, catalog_mode = discover_catalog(source_kind, source)
        if public is not None:
            ensure_distinct(bundle, public, "shared bundle and public catalog")
            ensure_distinct(root, public, "dreaming repository and public catalog")

        if not bundle.exists():
            bundles.mkdir(parents=True, exist_ok=True)
            stage = bundles / f".stage-{identity}-{os.getpid()}-{uuid.uuid4().hex}"
            try:
                for skill in SHARED_SKILLS:
                    destination = stage / "skills" / skill
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(source / "skills" / skill, destination)
                manifest = {
                    "name": "dfrysinger-dreaming-shared",
                    "version": "1.0.0",
                    "description": "Verified immutable shared skills for dfrysinger-dreaming.",
                    "skills": [f"./skills/{name}" for name in SHARED_SKILLS],
                }
                manifest_path = stage / ".claude-plugin/plugin.json"
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                write_json(manifest_path, manifest)
                verify_root(stage, receipt, "copied bundle")
                verify_bundle_layout(stage, receipt)
                os.replace(stage, bundle)
                make_immutable(bundle)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        verify_root(bundle, receipt, "selected bundle")
        verify_bundle_layout(bundle, receipt)
        make_immutable(bundle)

        current = deps_dir / "current"
        link = deps_dir / f".current-{os.getpid()}-{uuid.uuid4().hex}"
        link.symlink_to(bundle)
        os.replace(link, current)
        selected = canonical(current)
        if selected != canonical(bundle):
            raise DependencyError("atomic shared bundle selection did not resolve to bundle")

        lines = [
            f"DREAMING_REPO_ROOT={shell_quote(str(root))}",
            f"DREAMING_SHARED_SKILLS_ROOT={shell_quote(str(selected))}",
            f"DREAMING_SHARED_BUNDLE_ID={shell_quote(identity)}",
            f"DREAMING_SHARED_SOURCE_KIND={shell_quote(source_kind)}",
            f"DREAMING_SHARED_PROTOCOL={shell_quote(str(PROTOCOL_VERSION))}",
            f"DREAMING_SHARED_REVISION={shell_quote(receipt['pinned_revision'])}",
            f"SKILLS_CATALOG_MODE={shell_quote(catalog_mode)}",
        ]
        if public is not None:
            lines.append(f"SKILLS_REPO_ROOT={shell_quote(str(public))}")
        atomic_write(config, "\n".join(lines) + "\n")
        return {
            "source_kind": source_kind,
            "source": str(source),
            "rejected_sources": rejected_sources,
            "bundle": str(selected),
            "bundle_id": identity,
            "config": str(config),
            "catalog": str(public) if public else None,
            "catalog_mode": catalog_mode,
        }
    finally:
        if temporary_source:
            shutil.rmtree(source, ignore_errors=True)


def generate_receipt(source: Path, revision: str) -> dict[str, Any]:
    if (
        not revision
        or revision == PLACEHOLDER_REVISION
        or len(revision) != 40
        or any(char not in "0123456789abcdef" for char in revision)
    ):
        raise DependencyError("receipt generation requires a full pinned commit SHA")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "pinned_revision": revision,
        "files": source_files(canonical(source)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("materialize")
    verify = subparsers.add_parser("verify")
    verify.add_argument("root")
    generate = subparsers.add_parser("generate-receipt")
    generate.add_argument("source")
    generate.add_argument("--revision", required=True)
    generate.add_argument("--output")
    args = parser.parse_args()
    try:
        if args.command == "materialize":
            print(json.dumps(materialize(), indent=2, sort_keys=True))
        elif args.command == "verify":
            verify_root(canonical(Path(args.root)), load_receipt(), "shared root")
            print(f"verified {canonical(Path(args.root))}")
        else:
            payload = generate_receipt(Path(args.source), args.revision)
            encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
            if args.output:
                Path(args.output).write_text(encoded, encoding="utf-8")
            else:
                print(encoded, end="")
        return 0
    except DependencyError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
