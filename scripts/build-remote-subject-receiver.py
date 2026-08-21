#!/usr/bin/env python3
"""Publish one immutable remote-subject receiver bundle."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any


class BundleError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def publish_create_only(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameatx_np = getattr(libc, "renameatx_np", None)
    if renameatx_np is None:
        raise BundleError("create-only directory publication is unavailable")
    renameatx_np.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameatx_np.restype = ctypes.c_int
    if (
        renameatx_np(
            -2,
            os.fsencode(source),
            -2,
            os.fsencode(destination),
            0x00000004,
        )
        != 0
    ):
        error = ctypes.get_errno()
        if error == 17:
            raise FileExistsError(destination)
        raise OSError(error, os.strerror(error), str(destination))


def source_files(repo_root: Path) -> list[tuple[str, Path]]:
    return [
        (
            "scripts/ssh-estate-census.py",
            repo_root / "scripts/ssh-estate-census.py",
        ),
        (
            "skills/skill-review/scripts/dreaming-estate.py",
            repo_root
            / "skills/skill-review/scripts/dreaming-estate.py",
        ),
        (
            "skills/skill-review/scripts/remote_subject_policy.py",
            repo_root
            / "skills/skill-review/scripts/remote_subject_policy.py",
        ),
        (
            "skills/skill-review/references/remote-subject-content-policy-v1.json",
            repo_root
            / "skills/skill-review/references/remote-subject-content-policy-v1.json",
        ),
    ]


def inventory(repo_root: Path) -> list[dict[str, Any]]:
    values = []
    for relative, path in source_files(repo_root):
        if path.is_symlink() or not path.is_file():
            raise BundleError(f"{relative} is not a regular source file")
        content = path.read_bytes()
        values.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    return values


def verify_bundle(destination: Path, manifest: dict[str, Any]) -> None:
    manifest_path = destination / "manifest.json"
    if (
        destination.is_symlink()
        or not destination.is_dir()
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.read_bytes() != canonical(manifest)
    ):
        raise BundleError("existing receiver bundle differs")
    expected = {item["path"]: item for item in manifest["files"]}
    observed = {}
    for path in sorted(destination.rglob("*")):
        relative = path.relative_to(destination).as_posix()
        if path.is_symlink():
            raise BundleError("receiver bundle contains a symbolic link")
        if path.is_dir():
            continue
        if not path.is_file() or relative == "manifest.json":
            if relative != "manifest.json":
                raise BundleError("receiver bundle contains an unknown file")
            continue
        content = path.read_bytes()
        observed[relative] = {
            "path": relative,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }
    if observed != expected:
        raise BundleError("existing receiver bundle inventory differs")


def build(repo_root: Path, output_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    output_root = output_root.resolve()
    if not output_root.exists():
        output_root.mkdir(parents=True, mode=0o700)
    if (
        output_root.is_symlink()
        or not output_root.is_dir()
        or output_root.stat().st_uid != os.getuid()
        or stat.S_IMODE(output_root.stat().st_mode) & 0o077
    ):
        raise BundleError("receiver bundle output root is unsafe")
    files = inventory(repo_root)
    manifest = {
        "schema_version": 1,
        "kind": "remote_subject_receiver_bundle",
        "protocol_version": 1,
        "files": files,
    }
    manifest["bundle_id"] = digest(manifest)
    destination = output_root / manifest["bundle_id"].removeprefix("sha256:")
    if destination.exists():
        verify_bundle(destination, manifest)
        return {
            "status": "existing",
            "bundle_id": manifest["bundle_id"],
            "bundle_root": str(destination),
        }
    with tempfile.TemporaryDirectory(
        prefix=".remote-subject-receiver.", dir=output_root
    ) as temporary:
        staging = Path(temporary) / "bundle"
        staging.mkdir(mode=0o700)
        for item, (_, source) in zip(files, source_files(repo_root)):
            target = staging / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(source, target)
            os.chmod(target, 0o500 if source.suffix == ".py" else 0o400)
        manifest_path = staging / "manifest.json"
        manifest_path.write_bytes(canonical(manifest))
        os.chmod(manifest_path, 0o400)
        for directory in sorted(
            [path for path in staging.rglob("*") if path.is_dir()],
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            os.chmod(directory, 0o500)
        try:
            publish_create_only(staging, destination)
        except FileExistsError:
            verify_bundle(destination, manifest)
            status = "existing"
        else:
            os.chmod(destination, 0o500)
            status = "published"
    return {
        "status": status,
        "bundle_id": manifest["bundle_id"],
        "bundle_root": str(destination),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                build(args.repo_root, args.output_root),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (BundleError, OSError) as error:
        print(
            json.dumps(
                {"ok": False, "error": str(error)},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
