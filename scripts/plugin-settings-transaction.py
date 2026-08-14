#!/usr/bin/env python3
"""Apply one lossless, source-qualified Copilot plugin settings transaction."""

from __future__ import annotations

import argparse
import copy
import ctypes
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

SCHEMA_VERSION = 1
MAX_SETTINGS_BYTES = 4 * 1024 * 1024
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RENAME_SWAP = 0x00000002


class SettingsError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FileState:
    data: bytes
    sha256: str
    device: int
    inode: int
    mode: int


class Swapper(Protocol):
    def verify_supported(self, directory: Path) -> None: ...

    def exchange(self, left: Path, right: Path) -> None: ...


Barrier = Callable[[str, dict[str, Any]], None]
RuntimeVerifier = Callable[[], dict[str, Any]]


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def hash_json(value: Any) -> str:
    return hash_bytes(canonical_bytes(value))


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_regular(path: Path, code: str) -> FileState:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SettingsError(code) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_SETTINGS_BYTES:
            raise SettingsError(code)
        chunks: list[bytes] = []
        remaining = MAX_SETTINGS_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(data) > MAX_SETTINGS_BYTES
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
        ):
            raise SettingsError(code)
        return FileState(
            data=data,
            sha256=hash_bytes(data),
            device=after.st_dev,
            inode=after.st_ino,
            mode=stat.S_IMODE(after.st_mode),
        )
    finally:
        os.close(descriptor)


def stable_read(
    path: Path,
    code: str,
    *,
    quiet_interval: float,
    stable_samples: int = 3,
) -> FileState:
    prior: FileState | None = None
    matches = 0
    for _ in range(max(stable_samples * 4, 4)):
        current = read_regular(path, code)
        if current == prior:
            matches += 1
            if matches >= stable_samples - 1:
                return current
        else:
            prior = current
            matches = 0
        if quiet_interval:
            time.sleep(quiet_interval)
    raise SettingsError("settings-preimage-unstable")


def load_object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SettingsError(code) from error
    if not isinstance(value, dict):
        raise SettingsError(code)
    return value


def immutable_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError:
        try:
            if path.read_bytes() != data:
                raise SettingsError("transaction-record-collision")
        except OSError as error:
            raise SettingsError("transaction-record-invalid") from error
        return
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    fsync_directory(path.parent)


def immutable_json(path: Path, value: dict[str, Any]) -> None:
    immutable_bytes(
        path,
        json.dumps(value, indent=2, sort_keys=True).encode() + b"\n",
    )


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        fsync_directory(path.parent)
    except OSError as error:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise SettingsError("transaction-record-write-failed") from error


def remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    fsync_directory(path.parent)


class MacOSSwapper:
    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise SettingsError("settings-swap-unsupported")
        library = ctypes.CDLL(None, use_errno=True)
        function = library.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        self._renamex_np = function

    def exchange(self, left: Path, right: Path) -> None:
        result = self._renamex_np(
            os.fsencode(left), os.fsencode(right), RENAME_SWAP
        )
        if result:
            error = ctypes.get_errno()
            raise SettingsError("settings-swap-failed") from OSError(
                error, os.strerror(error)
            )

    def verify_supported(self, directory: Path) -> None:
        left = directory / f".dreaming-swap-left.{uuid.uuid4().hex}"
        right = directory / f".dreaming-swap-right.{uuid.uuid4().hex}"
        try:
            immutable_bytes(left, b"left")
            immutable_bytes(right, b"right")
            self.exchange(left, right)
            if left.read_bytes() != b"right" or right.read_bytes() != b"left":
                raise SettingsError("settings-swap-unsupported")
        except (OSError, SettingsError) as error:
            raise SettingsError("settings-swap-unsupported") from error
        finally:
            for path in (left, right):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            fsync_directory(directory)


def validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "operation_id",
        "action",
        "plugin",
        "copilot_version",
        "qualification_sha256",
        "expected",
        "evidence",
        "request_sha256",
    }:
        raise SettingsError("settings-request-malformed")
    payload = {
        key: item for key, item in value.items() if key != "request_sha256"
    }
    plugin = value.get("plugin")
    expected = value.get("expected")
    evidence = value.get("evidence")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("action") != "disable"
        or not isinstance(value.get("operation_id"), str)
        or not SAFE_ID.fullmatch(value["operation_id"])
        or not isinstance(value.get("copilot_version"), str)
        or not value["copilot_version"]
        or not SHA256_RE.fullmatch(str(value.get("qualification_sha256")))
        or value.get("request_sha256") != hash_json(payload)
        or not isinstance(plugin, dict)
        or set(plugin)
        != {
            "plugin_id",
            "source_identity",
            "version",
            "source_type",
            "settings_key",
        }
        or not all(isinstance(item, str) and item for item in plugin.values())
        or plugin["source_type"] not in {"marketplace", "direct"}
        or not isinstance(expected, dict)
        or set(expected)
        != {
            "settings_sha256",
            "settings_device",
            "settings_inode",
            "settings_mode",
            "prior_key_present",
            "prior_key_value",
            "runtime_before_sha256",
        }
        or not SHA256_RE.fullmatch(str(expected.get("settings_sha256")))
        or not SHA256_RE.fullmatch(str(expected.get("runtime_before_sha256")))
        or not all(
            isinstance(expected.get(field), int)
            for field in ("settings_device", "settings_inode", "settings_mode")
        )
        or not isinstance(expected.get("prior_key_present"), bool)
        or (
            expected["prior_key_present"]
            and not isinstance(expected.get("prior_key_value"), bool)
        )
        or not isinstance(evidence, dict)
        or set(evidence)
        != {"census_sha256", "capability_inventory_sha256"}
        or not all(
            SHA256_RE.fullmatch(str(evidence.get(field)))
            for field in evidence
        )
    ):
        raise SettingsError("settings-request-malformed")
    return value


def validate_qualification(
    qualification: dict[str, Any], request: dict[str, Any]
) -> None:
    if set(qualification) != {
        "schema_version",
        "status",
        "source_type",
        "copilot_version",
        "plugin_id",
        "settings_key",
        "disable_verified",
        "restore_verified",
        "qualification_sha256",
    }:
        raise SettingsError("plugin-source-unqualified")
    payload = {
        key: item
        for key, item in qualification.items()
        if key != "qualification_sha256"
    }
    plugin = request["plugin"]
    if (
        qualification.get("schema_version") != SCHEMA_VERSION
        or qualification.get("status") != "qualified"
        or qualification.get("source_type") != plugin["source_type"]
        or qualification.get("copilot_version") != request["copilot_version"]
        or qualification.get("plugin_id") != plugin["plugin_id"]
        or qualification.get("settings_key") != plugin["settings_key"]
        or qualification.get("disable_verified") is not True
        or qualification.get("restore_verified") is not True
        or qualification.get("qualification_sha256") != hash_json(payload)
        or qualification["qualification_sha256"]
        != request["qualification_sha256"]
    ):
        raise SettingsError("plugin-source-unqualified")


def validate_runtime(
    value: Any, request: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "copilot_version",
        "plugin_identity",
        "plugin_enabled",
        "owned_capability_ids",
        "estate_capability_ids",
    }:
        raise SettingsError("plugin-runtime-inventory-malformed")
    plugin = request["plugin"]
    identity = {
        field: plugin[field]
        for field in ("plugin_id", "source_identity", "version")
    }
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("copilot_version") != request["copilot_version"]
        or value.get("plugin_identity") != identity
        or not isinstance(value.get("plugin_enabled"), bool)
        or not all(
            isinstance(value.get(field), list)
            and all(isinstance(item, str) and item for item in value[field])
            and len(value[field]) == len(set(value[field]))
            for field in ("owned_capability_ids", "estate_capability_ids")
        )
        or not set(value.get("owned_capability_ids", [])).issubset(
            value.get("estate_capability_ids", [])
        )
    ):
        raise SettingsError("plugin-runtime-inventory-malformed")
    return value


def settings_document(state: FileState) -> dict[str, Any]:
    try:
        value = json.loads(state.data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SettingsError("settings-json-invalid") from error
    if not isinstance(value, dict):
        raise SettingsError("settings-json-invalid")
    if "enabledPlugins" in value and not isinstance(
        value["enabledPlugins"], dict
    ):
        raise SettingsError("settings-json-invalid")
    return value


def write_stage(path: Path, data: bytes, mode: int) -> FileState:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except OSError as error:
        raise SettingsError("settings-stage-write-failed") from error
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        raise SettingsError("settings-stage-write-failed") from error
    state = read_regular(path, "settings-stage-invalid")
    if state.mode != mode:
        raise SettingsError("settings-stage-invalid")
    return state


def retained_path(root: Path, operation_id: str, name: str) -> Path:
    return root / operation_id / name


def retain_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, destination)
        os.chmod(destination, 0o600)
        fsync_directory(destination.parent)
        fsync_directory(source.parent)
    except OSError as error:
        raise SettingsError("settings-version-retention-failed") from error


def record_recovery(
    recovery_state: Path,
    request: dict[str, Any],
    settings_path: Path,
    staged_path: Path,
) -> dict[str, Any]:
    minimal = {
        "schema_version": SCHEMA_VERSION,
        "status": "retention_pending",
        "operation_id": request["operation_id"],
        "request_sha256": request["request_sha256"],
        "active": {"path": str(settings_path)},
        "displaced": {"path": str(staged_path)},
    }
    atomic_json(recovery_state, minimal)
    try:
        active = read_regular(settings_path, "settings-recovery-invalid")
        displaced = read_regular(staged_path, "settings-recovery-invalid")
    except SettingsError as error:
        failed = {**minimal, "error": {"code": error.code}}
        try:
            atomic_json(recovery_state, failed)
        except (SettingsError, OSError):
            pass
        return failed
    recovery_root = recovery_state.parent / "versions" / request["operation_id"]
    retained = recovery_root / "displaced-settings"
    record = {
        "schema_version": SCHEMA_VERSION,
        "status": "required",
        "operation_id": request["operation_id"],
        "request_sha256": request["request_sha256"],
        "active": {
            "path": str(settings_path),
            "sha256": active.sha256,
            "device": active.device,
            "inode": active.inode,
        },
        "displaced": {
            "path": str(staged_path),
            "sha256": displaced.sha256,
            "device": displaced.device,
            "inode": displaced.inode,
        },
    }
    try:
        retain_file(staged_path, retained)
        record["displaced"]["path"] = str(retained)
        atomic_json(recovery_state, record)
    except SettingsError as error:
        record["status"] = "retention_pending"
        record["error"] = {"code": error.code}
        try:
            atomic_json(recovery_state, record)
        except (SettingsError, OSError):
            pass
    return record


def rollback_exchange(
    *,
    request: dict[str, Any],
    settings_path: Path,
    staged_path: Path,
    output_state: FileState,
    swapper: Swapper,
    barrier: Barrier,
    transaction_root: Path,
    recovery_state: Path,
) -> dict[str, Any]:
    barrier(
        "before_rollback",
        {
            "settings_path": str(settings_path),
            "staged_path": str(staged_path),
        },
    )
    active = read_regular(settings_path, "settings-rollback-invalid")
    if active != output_state:
        recovery = record_recovery(
            recovery_state, request, settings_path, staged_path
        )
        return {
            "ok": False,
            "status": "recovery_required",
            "error": {"code": "settings-concurrent-write"},
            "recovery": recovery,
        }
    swapper.exchange(settings_path, staged_path)
    fsync_directory(settings_path.parent)
    remove_file(recovery_state)
    return {
        "ok": False,
        "status": "rolled_back",
        "error": {"code": "settings-concurrent-write"},
        "active_settings_sha256": read_regular(
            settings_path, "settings-rollback-invalid"
        ).sha256,
        "retained_output": str(staged_path),
    }


def attempt_rollback(
    *,
    request: dict[str, Any],
    settings_path: Path,
    staged_path: Path,
    output_state: FileState,
    swapper: Swapper,
    barrier: Barrier,
    transaction_root: Path,
    recovery_state: Path,
) -> dict[str, Any]:
    try:
        return rollback_exchange(
            request=request,
            settings_path=settings_path,
            staged_path=staged_path,
            output_state=output_state,
            swapper=swapper,
            barrier=barrier,
            transaction_root=transaction_root,
            recovery_state=recovery_state,
        )
    except (
        SettingsError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        recovery = record_recovery(
            recovery_state, request, settings_path, staged_path
        )
        return {
            "ok": False,
            "status": "recovery_required",
            "error": {"code": "settings-rollback-failed"},
            "cause": (
                error.code
                if isinstance(error, SettingsError)
                else "settings-transaction-step-failed"
            ),
            "recovery": recovery,
        }


def execute_disable(
    *,
    request: dict[str, Any],
    settings_path: Path,
    transaction_root: Path,
    qualification_root: Path,
    lock_path: Path,
    recovery_state: Path,
    runtime_verifier: RuntimeVerifier,
    swapper: Swapper,
    barrier: Barrier = lambda _name, _context: None,
    quiet_interval: float = 0.05,
) -> dict[str, Any]:
    request = validate_request(request)
    if recovery_state.exists():
        raise SettingsError("estate-recovery-required")
    qualification = load_object(
        qualification_root / f"{request['qualification_sha256']}.json",
        "plugin-source-unqualified",
    )
    validate_qualification(qualification, request)
    transaction_root.mkdir(parents=True, exist_ok=True)
    recovery_state.parent.mkdir(parents=True, exist_ok=True)
    if (
        settings_path.parent.stat().st_dev != transaction_root.stat().st_dev
        or settings_path.parent.stat().st_dev
        != recovery_state.parent.stat().st_dev
    ):
        raise SettingsError("settings-transaction-volume-unsupported")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if recovery_state.exists():
            raise SettingsError("estate-recovery-required")
        before = read_regular(settings_path, "settings-preimage-invalid")
        expected = request["expected"]
        if (
            before.sha256 != expected["settings_sha256"]
            or before.device != expected["settings_device"]
            or before.inode != expected["settings_inode"]
            or before.mode != expected["settings_mode"]
        ):
            raise SettingsError("settings-preimage-mismatch")
        document = settings_document(before)
        enabled = document.get("enabledPlugins") or {}
        key = request["plugin"]["settings_key"]
        present = key in enabled
        prior_value = enabled.get(key)
        if (
            present != expected["prior_key_present"]
            or prior_value != expected["prior_key_value"]
            or (present and prior_value is not True)
        ):
            raise SettingsError("plugin-effective-state-mismatch")
        try:
            runtime_before = validate_runtime(runtime_verifier(), request)
        except SettingsError:
            raise
        except (
            OSError,
            subprocess.SubprocessError,
            ValueError,
        ) as error:
            raise SettingsError("plugin-runtime-inventory-failed") from error
        if (
            hash_json(runtime_before) != expected["runtime_before_sha256"]
            or runtime_before["plugin_enabled"] is not True
        ):
            raise SettingsError("plugin-runtime-before-mismatch")

        updated = copy.deepcopy(document)
        updated_enabled = updated.setdefault("enabledPlugins", {})
        updated_enabled[key] = False
        output = (
            json.dumps(updated, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        )
        operation_root = transaction_root / request["operation_id"]
        operation_root.mkdir(parents=True, exist_ok=True)
        if operation_root.stat().st_dev != before.device:
            raise SettingsError("settings-transaction-volume-unsupported")
        immutable_bytes(operation_root / "before-settings", before.data)
        staged_path = settings_path.parent / (
            f".{settings_path.name}.dreaming-stage.{request['operation_id']}"
        )
        output_state: FileState | None = None
        exchanged = False
        preserve_staged = False
        fence_active = False
        try:
            swapper.verify_supported(settings_path.parent)
            output_state = write_stage(staged_path, output, before.mode)
            atomic_json(
                recovery_state,
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "transaction_active",
                    "operation_id": request["operation_id"],
                    "request_sha256": request["request_sha256"],
                    "settings_path": str(settings_path),
                    "staged_path": str(staged_path),
                },
            )
            fence_active = True
            barrier(
                "before_exchange",
                {
                    "settings_path": str(settings_path),
                    "staged_path": str(staged_path),
                },
            )
            swapper.exchange(settings_path, staged_path)
            exchanged = True
            barrier(
                "after_exchange",
                {
                    "settings_path": str(settings_path),
                    "staged_path": str(staged_path),
                },
            )
            fsync_directory(settings_path.parent)
            displaced = stable_read(
                staged_path,
                "settings-displaced-preimage-invalid",
                quiet_interval=quiet_interval,
            )
            if displaced != before:
                return attempt_rollback(
                    request=request,
                    settings_path=settings_path,
                    staged_path=staged_path,
                    output_state=output_state,
                    swapper=swapper,
                    barrier=barrier,
                    transaction_root=transaction_root,
                    recovery_state=recovery_state,
                )
            active = read_regular(settings_path, "settings-active-invalid")
            if active != output_state:
                return attempt_rollback(
                    request=request,
                    settings_path=settings_path,
                    staged_path=staged_path,
                    output_state=output_state,
                    swapper=swapper,
                    barrier=barrier,
                    transaction_root=transaction_root,
                    recovery_state=recovery_state,
                )
            active_document = settings_document(active)
            if active_document.get("enabledPlugins", {}).get(key) is not False:
                raise SettingsError("settings-semantic-verification-failed")
            runtime_after = validate_runtime(runtime_verifier(), request)
            expected_estate = sorted(
                set(runtime_before["estate_capability_ids"])
                - set(runtime_before["owned_capability_ids"])
            )
            if (
                runtime_after["plugin_enabled"] is not False
                or runtime_after["owned_capability_ids"]
                or sorted(runtime_after["estate_capability_ids"])
                != expected_estate
            ):
                raise SettingsError("plugin-runtime-after-mismatch")
            receipt_payload = {
                "schema_version": SCHEMA_VERSION,
                "status": "committed",
                "operation_id": request["operation_id"],
                "request_sha256": request["request_sha256"],
                "plugin": request["plugin"],
                "qualification_sha256": request["qualification_sha256"],
                "evidence": request["evidence"],
                "before": {
                    "sha256": before.sha256,
                    "device": before.device,
                    "inode": before.inode,
                    "mode": before.mode,
                    "key_present": present,
                    "key_value": prior_value,
                    "preimage_path": str(operation_root / "before-settings"),
                    "retained_inode_path": str(staged_path),
                },
                "after": {
                    "sha256": active.sha256,
                    "device": active.device,
                    "inode": active.inode,
                    "mode": active.mode,
                    "key_present": True,
                    "key_value": False,
                },
                "runtime_before_sha256": hash_json(runtime_before),
                "runtime_after_sha256": hash_json(runtime_after),
            }
            receipt = {
                **receipt_payload,
                "receipt_sha256": hash_json(receipt_payload),
            }
            immutable_json(operation_root / "receipt.json", receipt)
            preserve_staged = True
            exchanged = False
            remove_file(recovery_state)
            fence_active = False
            return {"ok": True, **receipt}
        except (
            SettingsError,
            OSError,
            subprocess.SubprocessError,
            ValueError,
        ) as error:
            cause = (
                error.code
                if isinstance(error, SettingsError)
                else "settings-transaction-step-failed"
            )
            if exchanged and output_state is not None:
                result = attempt_rollback(
                    request=request,
                    settings_path=settings_path,
                    staged_path=staged_path,
                    output_state=output_state,
                    swapper=swapper,
                    barrier=barrier,
                    transaction_root=transaction_root,
                    recovery_state=recovery_state,
                )
                result["cause"] = cause
                fence_active = recovery_state.exists()
                return result
            if fence_active:
                remove_file(recovery_state)
                fence_active = False
            if isinstance(error, SettingsError):
                raise
            raise SettingsError(cause) from error
        finally:
            if not exchanged and not preserve_staged:
                try:
                    staged_path.unlink()
                except FileNotFoundError:
                    pass


def command_runtime_verifier(
    command: list[str], settings_path: Path, request: dict[str, Any]
) -> RuntimeVerifier:
    def verify() -> dict[str, Any]:
        try:
            process = subprocess.run(
                [
                    *command,
                    "--settings",
                    str(settings_path),
                    "--plugin-id",
                    request["plugin"]["plugin_id"],
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise SettingsError("plugin-runtime-inventory-failed") from error
        if process.returncode:
            raise SettingsError("plugin-runtime-inventory-failed")
        try:
            value = json.loads(process.stdout)
        except json.JSONDecodeError as error:
            raise SettingsError("plugin-runtime-inventory-malformed") from error
        return value

    return verify


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("disable",))
    parser.add_argument("--request", required=True)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--transaction-root", required=True)
    parser.add_argument("--qualification-root", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--recovery-state", required=True)
    parser.add_argument("--runtime-verifier", required=True, nargs="+")
    args = parser.parse_args()
    try:
        settings_path = Path(
            os.path.abspath(os.path.expanduser(args.settings))
        )
        request = validate_request(
            load_object(Path(args.request), "settings-request-malformed")
        )
        result = execute_disable(
            request=request,
            settings_path=settings_path,
            transaction_root=Path(args.transaction_root).expanduser().resolve(),
            qualification_root=Path(
                args.qualification_root
            ).expanduser().resolve(),
            lock_path=Path(args.lock).expanduser().resolve(),
            recovery_state=Path(args.recovery_state).expanduser().resolve(),
            runtime_verifier=command_runtime_verifier(
                args.runtime_verifier,
                settings_path,
                request,
            ),
            swapper=MacOSSwapper(),
        )
        print(json.dumps({"ok": result.get("ok") is True, "result": result}))
        raise SystemExit(0 if result.get("ok") is True else 2)
    except (
        SettingsError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
        TypeError,
    ) as error:
        code = (
            error.code
            if isinstance(error, SettingsError)
            else "settings-transaction-step-failed"
        )
        print(json.dumps({"ok": False, "error": {"code": code}}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
