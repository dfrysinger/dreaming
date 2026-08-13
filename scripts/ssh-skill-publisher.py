#!/usr/bin/env python3
"""Publish a content-addressed Dreaming skill bundle on another Mac over SSH."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024


class PublisherError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def emit(value: dict[str, Any], status: int = 0) -> None:
    print(json.dumps(value, sort_keys=True))
    raise SystemExit(status)


def fail(code: str, message: str) -> None:
    emit({"ok": False, "error": {"code": code, "message": message}}, 2)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}"
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PublisherError("publication-state-invalid", str(path)) from error


def validate_bundle(bundle: Path, bundle_id: str) -> dict[str, Any]:
    if not bundle.is_dir() or bundle.is_symlink():
        raise PublisherError("bundle-proof-invalid", str(bundle))
    manifest = load_json(bundle / "dreaming-bundle-manifest.json")
    if not isinstance(manifest, dict) or manifest.get("bundle_id") != bundle_id:
        raise PublisherError("bundle-proof-invalid", bundle_id)
    declared: dict[str, str] = {}
    for item in manifest.get("files", []):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("sha256"), str)
        ):
            raise PublisherError("bundle-proof-invalid", "manifest files")
        relative = PurePosixPath(item["path"])
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise PublisherError("bundle-proof-invalid", item["path"])
        if item["path"] in declared:
            raise PublisherError("bundle-proof-invalid", "duplicate manifest path")
        declared[item["path"]] = item["sha256"]
    actual = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    expected = {*declared, "dreaming-bundle-manifest.json"}
    if actual != expected or any(path.is_symlink() for path in bundle.rglob("*")):
        raise PublisherError("bundle-proof-invalid", "bundle inventory")
    for relative, expected_sha in declared.items():
        if sha256_file(bundle / relative) != expected_sha:
            raise PublisherError("bundle-proof-invalid", relative)
    return manifest


def archive_bundle(bundle: Path, bundle_id: str) -> bytes:
    validate_bundle(bundle, bundle_id)
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as handle:
        with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(bundle.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(bundle).as_posix())
        size = handle.tell()
        if size > MAX_ARCHIVE_BYTES:
            raise PublisherError("bundle-too-large", str(size))
        handle.seek(0)
        return handle.read()


def parse_output(process: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    try:
        for raw in process.stdout.decode("utf-8").splitlines():
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("JSON line is not an object")
            objects.append(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PublisherError("malformed-adapter-output", str(error)) from error
    if not objects:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise PublisherError("empty-adapter-output", detail or f"exit {process.returncode}")
    result = objects[-1]
    if process.returncode != 0 or result.get("ok") is not True:
        error = result.get("error", {})
        raise PublisherError(
            str(error.get("code", "adapter-failed")),
            str(error.get("message", "remote publisher failed")),
        )
    return result


def command_index(arguments: list[str]) -> int:
    commands = {
        "contract",
        "doctor",
        "inventory",
        "install",
        "snapshot",
        "reconcile",
        "verify",
        "remove",
    }
    for index, value in enumerate(arguments):
        if value in commands:
            return index
    raise PublisherError("unsupported-command", "publisher command is missing")


def replace_argument(arguments: list[str], name: str, value: str) -> list[str]:
    result = list(arguments)
    try:
        index = result.index(name)
    except ValueError as error:
        raise PublisherError("missing-argument", name) from error
    if index + 1 >= len(result):
        raise PublisherError("missing-argument", name)
    result[index + 1] = value
    return result


def receiver_identity(args: argparse.Namespace) -> dict[str, str]:
    identity_path = Path(args.receiver_id_file).expanduser().resolve()
    try:
        receiver_id = identity_path.read_text(encoding="ascii").strip()
    except OSError as error:
        raise PublisherError("receiver-identity-missing", str(identity_path)) from error
    receiver_sha = sha256_file(Path(__file__).resolve())
    adapter_sha = sha256_file(Path(args.adapter_script).expanduser().resolve())
    if receiver_id != args.expected_receiver_id:
        raise PublisherError("receiver-identity-mismatch", receiver_id)
    if receiver_sha != args.expected_receiver_sha:
        raise PublisherError("receiver-code-mismatch", receiver_sha)
    if adapter_sha != args.expected_adapter_sha:
        raise PublisherError("adapter-code-mismatch", adapter_sha)
    return {
        "receiver_id": receiver_id,
        "receiver_sha256": receiver_sha,
        "adapter_sha256": adapter_sha,
    }


def adapter_command(args: argparse.Namespace, arguments: list[str]) -> list[str]:
    index = command_index(arguments)
    return [
        args.adapter_python,
        args.adapter_script,
        *arguments[:index],
        "--ownership-journal",
        args.ownership_journal,
        *arguments[index:],
    ]


def run_adapter(args: argparse.Namespace, arguments: list[str]) -> dict[str, Any]:
    process = subprocess.run(
        adapter_command(args, arguments),
        check=False,
        capture_output=True,
        timeout=180,
    )
    return parse_output(process)


def safe_extract(payload: bytes, destination: Path, bundle_id: str) -> None:
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise PublisherError("bundle-too-large", str(len(payload)))
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        root = Path(temporary)
        archive_path = root / "bundle.zip"
        archive_path.write_bytes(payload)
        extract_root = root / "files"
        extract_root.mkdir()
        seen: set[str] = set()
        try:
            with zipfile.ZipFile(archive_path) as archive:
                extracted_bytes = 0
                for item in archive.infolist():
                    relative = PurePosixPath(item.filename)
                    mode = item.external_attr >> 16
                    if (
                        item.is_dir()
                        or relative.is_absolute()
                        or ".." in relative.parts
                        or not relative.parts
                        or item.filename in seen
                        or (mode & 0o170000) == 0o120000
                    ):
                        raise PublisherError("bundle-archive-invalid", item.filename)
                    if (
                        item.file_size < 0
                        or item.file_size > MAX_EXTRACTED_BYTES
                        or extracted_bytes + item.file_size > MAX_EXTRACTED_BYTES
                    ):
                        raise PublisherError("bundle-too-large", item.filename)
                    seen.add(item.filename)
                    target = extract_root.joinpath(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(item) as source, target.open("wb") as output:
                        while chunk := source.read(1024 * 1024):
                            extracted_bytes += len(chunk)
                            if extracted_bytes > MAX_EXTRACTED_BYTES:
                                raise PublisherError(
                                    "bundle-too-large", str(extracted_bytes)
                                )
                            output.write(chunk)
        except (OSError, zipfile.BadZipFile) as error:
            raise PublisherError("bundle-archive-invalid", str(error)) from error
        validate_bundle(extract_root, bundle_id)
        if destination.exists():
            validate_bundle(destination, bundle_id)
            return
        staged = destination.parent / f".{destination.name}.{uuid.uuid4().hex}"
        extract_root.rename(staged)
        for path in sorted(staged.rglob("*"), reverse=True):
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        os.chmod(staged, 0o555)
        os.replace(staged, destination)


def operation_path(args: argparse.Namespace) -> Path:
    return Path(args.operation_root).expanduser().resolve() / "copilot.json"


def reconcile_pending(args: argparse.Namespace) -> dict[str, Any] | None:
    path = operation_path(args)
    if not path.is_file():
        return None
    operation = load_json(path)
    if not isinstance(operation, dict) or operation.get("phase") != "installing":
        return operation if isinstance(operation, dict) else None
    result = run_adapter(
        args,
        [
            "--vendor",
            "copilot",
            "--role",
            "skill-publisher",
            "reconcile",
            "--operation",
            str(path),
            "--outcome",
            "auto",
        ],
    )
    operation["phase"] = result["status"]
    operation["result"] = result
    atomic_json(path, operation)
    return operation


def receive(args: argparse.Namespace, arguments: list[str]) -> None:
    identity = receiver_identity(args)
    pending = reconcile_pending(args)
    index = command_index(arguments)
    command = arguments[index]
    if command != "install":
        result = run_adapter(args, arguments)
        if command == "verify" and result.get("verified") is True:
            journal = load_json(Path(args.ownership_journal))
            result["descriptor"] = journal.get("copilot")
        result.update(identity)
        if pending and pending.get("phase") in {"committed", "rolled_back"}:
            result["recovered_operation"] = pending
        emit(result)

    bundle_id = arguments[arguments.index("--bundle-id") + 1]
    bundle_name = bundle_id.removeprefix("sha256:")
    if len(bundle_name) != 64 or any(character not in "0123456789abcdef" for character in bundle_name):
        raise PublisherError("bundle-proof-invalid", bundle_id)
    bundle_root = Path(args.bundle_root).expanduser().resolve()
    bundle_root.mkdir(parents=True, exist_ok=True)
    destination = bundle_root / bundle_name
    payload = sys.stdin.buffer.read(MAX_ARCHIVE_BYTES + 1)
    safe_extract(payload, destination, bundle_id)
    remote_arguments = replace_argument(arguments, "--bundle", str(destination))
    snapshot = run_adapter(
        args,
        [
            "--vendor",
            "copilot",
            "--role",
            "skill-publisher",
            "snapshot",
            "--bundle",
            str(destination),
            "--bundle-id",
            bundle_id,
        ],
    )
    operation = {
        "schema_version": 1,
        "vendor": "copilot",
        "phase": "installing",
        "receiver": identity,
        "prior": snapshot.get("prior"),
        "new": snapshot.get("new"),
    }
    path = operation_path(args)
    atomic_json(path, operation)
    try:
        run_adapter(args, remote_arguments)
        verified = run_adapter(
            args,
            [
                "--vendor",
                "copilot",
                "--role",
                "skill-publisher",
                "verify",
                "--bundle-id",
                bundle_id,
            ],
        )
        if verified.get("verified") is not True:
            raise PublisherError("publisher-verification-failed", bundle_id)
    except PublisherError:
        result = run_adapter(
            args,
            [
                "--vendor",
                "copilot",
                "--role",
                "skill-publisher",
                "reconcile",
                "--operation",
                str(path),
                "--outcome",
                "rollback",
            ],
        )
        operation["phase"] = result["status"]
        operation["result"] = result
        atomic_json(path, operation)
        raise
    journal = load_json(Path(args.ownership_journal))
    operation["phase"] = "committed"
    operation["result"] = {
        "bundle_id": bundle_id,
        "descriptor": journal.get("copilot"),
    }
    atomic_json(path, operation)
    emit(
        {
            "ok": True,
            "installed": True,
            "bundle_id": bundle_id,
            "descriptor": journal.get("copilot"),
            "receipt": operation,
            **identity,
        }
    )


def local_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-bin", default="/usr/bin/ssh")
    parser.add_argument("--host", required=True)
    parser.add_argument("--address-family", choices=("4", "6"))
    parser.add_argument("--remote-python", required=True)
    parser.add_argument("--remote-script", required=True)
    parser.add_argument("--remote-adapter-python", required=True)
    parser.add_argument("--remote-adapter-script", required=True)
    parser.add_argument("--remote-bundle-root", required=True)
    parser.add_argument("--remote-ownership-journal", required=True)
    parser.add_argument("--remote-operation-root", required=True)
    parser.add_argument("--remote-receiver-id-file", required=True)
    parser.add_argument("--expected-receiver-id", required=True)
    parser.add_argument("--expected-receiver-sha", required=True)
    parser.add_argument("--expected-adapter-sha", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--recovery-state", required=True)
    return parser


def receiver_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receive", action="store_true")
    parser.add_argument("--adapter-python", required=True)
    parser.add_argument("--adapter-script", required=True)
    parser.add_argument("--bundle-root", required=True)
    parser.add_argument("--ownership-journal", required=True)
    parser.add_argument("--operation-root", required=True)
    parser.add_argument("--receiver-id-file", required=True)
    parser.add_argument("--expected-receiver-id", required=True)
    parser.add_argument("--expected-receiver-sha", required=True)
    parser.add_argument("--expected-adapter-sha", required=True)
    return parser


def remote_command(args: argparse.Namespace, arguments: list[str]) -> list[str]:
    receiver = [
        args.remote_python,
        args.remote_script,
        "--receive",
        "--adapter-python",
        args.remote_adapter_python,
        "--adapter-script",
        args.remote_adapter_script,
        "--bundle-root",
        args.remote_bundle_root,
        "--ownership-journal",
        args.remote_ownership_journal,
        "--operation-root",
        args.remote_operation_root,
        "--receiver-id-file",
        args.remote_receiver_id_file,
        "--expected-receiver-id",
        args.expected_receiver_id,
        "--expected-receiver-sha",
        args.expected_receiver_sha,
        "--expected-adapter-sha",
        args.expected_adapter_sha,
        "--",
        *arguments,
    ]
    return [
        args.ssh_bin,
        *([f"-{args.address_family}"] if args.address_family else []),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "--",
        args.host,
        shlex.join(receiver),
    ]


def update_summary(args: argparse.Namespace, result: dict[str, Any]) -> None:
    descriptor = result.get("descriptor")
    if not isinstance(descriptor, dict):
        if result.get("verified") is False:
            Path(args.summary).unlink(missing_ok=True)
        return
    atomic_json(
        Path(args.summary),
        {
            "schema_version": 1,
            "status": "committed",
            "receiver_id": result.get("receiver_id"),
            "receiver_sha256": result.get("receiver_sha256"),
            "adapter_sha256": result.get("adapter_sha256"),
            "descriptor": descriptor,
        },
    )
    Path(args.recovery_state).unlink(missing_ok=True)


def local(args: argparse.Namespace, arguments: list[str]) -> None:
    if args.host.startswith("-"):
        raise PublisherError("receiver-host-invalid", args.host)
    index = command_index(arguments)
    command = arguments[index]
    payload = b""
    if command == "install":
        bundle = Path(arguments[arguments.index("--bundle") + 1]).resolve()
        bundle_id = arguments[arguments.index("--bundle-id") + 1]
        payload = archive_bundle(bundle, bundle_id)
    attempts = 3 if command in {"install", "verify"} else 1
    last_error: PublisherError | None = None
    for _ in range(attempts):
        process = subprocess.run(
            remote_command(args, arguments),
            input=payload,
            check=False,
            capture_output=True,
            timeout=240,
        )
        try:
            result = parse_output(process)
            if result.get("receiver_id") != args.expected_receiver_id:
                raise PublisherError(
                    "receiver-identity-mismatch", str(result.get("receiver_id"))
                )
            if command in {"install", "verify"}:
                update_summary(args, result)
            if command == "remove":
                Path(args.summary).unlink(missing_ok=True)
                Path(args.recovery_state).unlink(missing_ok=True)
            emit(result)
        except PublisherError as error:
            last_error = error
    atomic_json(
        Path(args.recovery_state),
        {
            "schema_version": 1,
            "status": "publication_recovery_required",
            "host": args.host,
            "error": {
                "code": last_error.code if last_error else "remote-publisher-failed",
                "message": last_error.message if last_error else "remote publisher failed",
            },
        },
    )
    raise last_error or PublisherError(
        "remote-publisher-failed", "remote publisher failed"
    )


def main() -> None:
    try:
        separator = sys.argv.index("--")
    except ValueError:
        fail("missing-argument", "adapter arguments must follow --")
    before = sys.argv[1:separator]
    arguments = sys.argv[separator + 1 :]
    try:
        if "--receive" in before:
            receive(receiver_parser().parse_args(before), arguments)
        local(local_parser().parse_args(before), arguments)
    except PublisherError as error:
        fail(error.code, error.message)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        fail("remote-publisher-failed", str(error))


if __name__ == "__main__":
    main()
