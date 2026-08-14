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


def plugin_identity(plugin: dict[str, Any]) -> dict[str, str]:
    return {
        field: plugin[field]
        for field in ("plugin_id", "source_identity", "version")
    }


def valid_safe_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value not in {".", ".."}
        and SAFE_ID.fullmatch(value) is not None
    )


def empty_ledger() -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "sequence": 0,
        "entry_sha256": None,
        "active_disables": [],
    }
    return {**payload, "ledger_sha256": hash_json(payload)}


def ledger_anchor() -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "genesis_ledger_sha256": empty_ledger()["ledger_sha256"],
    }
    return {**payload, "anchor_sha256": hash_json(payload)}


def load_ledger_anchor(transaction_root: Path) -> dict[str, Any]:
    value = load_private_object(
        transaction_root / "ledger-anchor.json",
        transaction_root,
        "settings-ledger-invalid",
    )
    payload = {
        key: item for key, item in value.items() if key != "anchor_sha256"
    }
    if value != ledger_anchor() or value.get("anchor_sha256") != hash_json(
        payload
    ):
        raise SettingsError("settings-ledger-invalid")
    return value


def ensure_private_directory(
    path: Path, root: Path, code: str
) -> Path:
    root = root.absolute()
    path = path.absolute()
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise SettingsError(code) from error
    current = root
    for component in (Path("."), *relative.parts):
        if component != Path("."):
            current /= component
        if current.is_symlink():
            raise SettingsError(code)
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        try:
            if not current.is_dir() or current.is_symlink():
                raise SettingsError(code)
        except OSError as error:
            raise SettingsError(code) from error
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise SettingsError(code)
    return path


def initialize_ledger(transaction_root: Path) -> dict[str, Any]:
    anchor = transaction_root / "ledger-anchor.json"
    ledger_path = transaction_root / "ledger"
    if anchor.is_symlink() or ledger_path.is_symlink():
        raise SettingsError("settings-ledger-invalid")
    anchor_exists = anchor.exists()
    ledger_exists = ledger_path.exists()
    if anchor_exists and not ledger_exists:
        raise SettingsError("settings-ledger-invalid")
    if not anchor_exists and not ledger_exists:
        allowed = {"ledger", "ledger-anchor.json"}
        if any(
            item.name not in allowed for item in transaction_root.iterdir()
        ):
            raise SettingsError("settings-ledger-invalid")
    ledger_root = ensure_private_directory(
        ledger_path,
        transaction_root,
        "settings-ledger-invalid",
    )
    entries = ensure_private_directory(
        ledger_root / "entries",
        transaction_root,
        "settings-ledger-invalid",
    )
    receipts = ensure_private_directory(
        ledger_root / "receipts",
        transaction_root,
        "settings-ledger-invalid",
    )
    current = ledger_root / "current.json"
    if current.is_symlink():
        raise SettingsError("settings-ledger-invalid")
    if not anchor_exists:
        allowed = {"entries", "receipts"}
        if (
            any(item.name not in allowed for item in ledger_root.iterdir())
            or any(entries.iterdir())
            or any(receipts.iterdir())
        ):
            raise SettingsError("settings-ledger-invalid")
        immutable_json(anchor, ledger_anchor())
    else:
        load_ledger_anchor(transaction_root)
    if not current.exists():
        allowed = {"entries", "receipts"}
        if (
            any(item.name not in allowed for item in ledger_root.iterdir())
            or any(entries.iterdir())
            or any(receipts.iterdir())
        ):
            raise SettingsError("settings-ledger-invalid")
        atomic_json(current, empty_ledger())
    return load_ledger(transaction_root)


def load_ledger(transaction_root: Path) -> dict[str, Any]:
    ledger_root = transaction_root / "ledger"
    anchor = transaction_root / "ledger-anchor.json"
    path = ledger_root / "current.json"
    if anchor.is_symlink() or ledger_root.is_symlink() or path.is_symlink():
        raise SettingsError("settings-ledger-invalid")
    if anchor.exists() != ledger_root.exists():
        raise SettingsError("settings-ledger-invalid")
    if not path.exists():
        if ledger_root.exists():
            try:
                if not ledger_root.is_dir() or any(ledger_root.iterdir()):
                    raise SettingsError("settings-ledger-invalid")
            except OSError as error:
                raise SettingsError("settings-ledger-invalid") from error
        if anchor.exists():
            raise SettingsError("settings-ledger-invalid")
        return empty_ledger()
    load_ledger_anchor(transaction_root)
    value = load_private_object(
        path, transaction_root, "settings-ledger-invalid"
    )
    if set(value) != {
        "schema_version",
        "sequence",
        "entry_sha256",
        "active_disables",
        "ledger_sha256",
    }:
        raise SettingsError("settings-ledger-invalid")
    payload = {
        key: item for key, item in value.items() if key != "ledger_sha256"
    }
    active = value.get("active_disables")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or not isinstance(value.get("sequence"), int)
        or value["sequence"] < 0
        or (
            value["entry_sha256"] is not None
            and not SHA256_RE.fullmatch(str(value["entry_sha256"]))
        )
        or not isinstance(active, list)
        or not all(
            isinstance(item, dict)
            and set(item)
            == {
                "receipt_sha256",
                "settings_key",
                "plugin_identity_sha256",
            }
            and SHA256_RE.fullmatch(str(item["receipt_sha256"]))
            and isinstance(item["settings_key"], str)
            and item["settings_key"]
            and SHA256_RE.fullmatch(str(item["plugin_identity_sha256"]))
            for item in active
        )
        or value.get("ledger_sha256") != hash_json(payload)
    ):
        raise SettingsError("settings-ledger-invalid")
    expected_sequence = value["sequence"]
    expected_entry_sha256 = value["entry_sha256"]
    expected_active = value["active_disables"]
    while expected_sequence:
        if not isinstance(expected_entry_sha256, str):
            raise SettingsError("settings-ledger-invalid")
        entry = load_private_object(
            transaction_root
            / "ledger/entries"
            / f"{expected_entry_sha256}.json",
            transaction_root,
            "settings-ledger-invalid",
        )
        if set(entry) != {
            "schema_version",
            "sequence",
            "previous_entry_sha256",
            "action",
            "operation_id",
            "receipt_sha256",
            "previous_active_disables",
            "active_disables",
            "entry_sha256",
        }:
            raise SettingsError("settings-ledger-invalid")
        entry_payload = {
            key: item for key, item in entry.items() if key != "entry_sha256"
        }
        if (
            entry.get("schema_version") != SCHEMA_VERSION
            or entry.get("sequence") != expected_sequence
            or entry.get("entry_sha256") != expected_entry_sha256
            or hash_json(entry_payload) != expected_entry_sha256
            or entry.get("active_disables") != expected_active
            or entry.get("action") not in {"disable", "restore"}
            or not valid_safe_id(entry.get("operation_id"))
            or not SHA256_RE.fullmatch(str(entry.get("receipt_sha256")))
            or not isinstance(entry.get("previous_active_disables"), list)
        ):
            raise SettingsError("settings-ledger-invalid")
        expected_active = entry["previous_active_disables"]
        expected_entry_sha256 = entry["previous_entry_sha256"]
        expected_sequence -= 1
    if expected_entry_sha256 is not None or expected_active:
        raise SettingsError("settings-ledger-invalid")
    return value


def append_ledger(
    *,
    transaction_root: Path,
    current: dict[str, Any],
    receipt: dict[str, Any],
    action: str,
) -> dict[str, Any]:
    ensure_private_directory(
        transaction_root / "ledger/entries",
        transaction_root,
        "settings-ledger-invalid",
    )
    receipt_sha256 = receipt["receipt_sha256"]
    plugin = receipt["plugin"]
    active = list(current["active_disables"])
    identity_sha256 = hash_json(plugin_identity(plugin))
    active_item = {
        "receipt_sha256": receipt_sha256,
        "settings_key": plugin["settings_key"],
        "plugin_identity_sha256": identity_sha256,
    }
    if action == "disable":
        if any(
            item["settings_key"] == plugin["settings_key"] for item in active
        ):
            raise SettingsError("settings-ledger-order-conflict")
        active.append(active_item)
    elif action == "restore":
        if not active or active[-1]["receipt_sha256"] != receipt[
            "restores_receipt_sha256"
        ]:
            raise SettingsError("settings-ledger-order-conflict")
        active.pop()
    else:
        raise SettingsError("settings-ledger-invalid")
    entry_payload = {
        "schema_version": SCHEMA_VERSION,
        "sequence": current["sequence"] + 1,
        "previous_entry_sha256": current["entry_sha256"],
        "action": action,
        "operation_id": receipt["operation_id"],
        "receipt_sha256": receipt_sha256,
        "previous_active_disables": current["active_disables"],
        "active_disables": active,
    }
    entry = {**entry_payload, "entry_sha256": hash_json(entry_payload)}
    immutable_json(
        transaction_root
        / "ledger/entries"
        / f"{entry['entry_sha256']}.json",
        entry,
    )
    next_payload = {
        "schema_version": SCHEMA_VERSION,
        "sequence": entry["sequence"],
        "entry_sha256": entry["entry_sha256"],
        "active_disables": active,
    }
    next_ledger = {
        **next_payload,
        "ledger_sha256": hash_json(next_payload),
    }
    atomic_json(transaction_root / "ledger/current.json", next_ledger)
    return next_ledger


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
        "protocol",
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
        or value.get("protocol") != "dreaming.plugin-settings"
        or value.get("action") != "disable"
        or not valid_safe_id(value.get("operation_id"))
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
            "ledger_sha256",
        }
        or not SHA256_RE.fullmatch(str(expected.get("settings_sha256")))
        or not SHA256_RE.fullmatch(str(expected.get("runtime_before_sha256")))
        or not SHA256_RE.fullmatch(str(expected.get("ledger_sha256")))
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


def validate_restore_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "protocol",
        "operation_id",
        "action",
        "plugin",
        "copilot_version",
        "qualification_sha256",
        "restores_receipt_sha256",
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
        or value.get("protocol") != "dreaming.plugin-settings"
        or value.get("action") != "restore"
        or not valid_safe_id(value.get("operation_id"))
        or not isinstance(value.get("copilot_version"), str)
        or not value["copilot_version"]
        or not SHA256_RE.fullmatch(str(value.get("qualification_sha256")))
        or not SHA256_RE.fullmatch(
            str(value.get("restores_receipt_sha256"))
        )
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
            "runtime_before_sha256",
            "ledger_sha256",
        }
        or not all(
            SHA256_RE.fullmatch(str(expected.get(field)))
            for field in (
                "settings_sha256",
                "runtime_before_sha256",
                "ledger_sha256",
            )
        )
        or not all(
            isinstance(expected.get(field), int)
            for field in ("settings_device", "settings_inode", "settings_mode")
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


def load_private_object(
    path: Path, root: Path, code: str
) -> dict[str, Any]:
    try:
        resolved_root = root.resolve()
        if path.is_symlink():
            raise SettingsError(code)
        resolved = path.resolve()
        if resolved_root not in resolved.parents:
            raise SettingsError(code)
        state = read_regular(resolved, code)
        value = json.loads(state.data.decode("utf-8"))
    except SettingsError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SettingsError(code) from error
    if not isinstance(value, dict):
        raise SettingsError(code)
    return value


def load_disable_receipt(
    transaction_root: Path, request: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes]:
    receipt_sha256 = request["restores_receipt_sha256"]
    receipt = load_private_object(
        transaction_root
        / "ledger/receipts"
        / f"{receipt_sha256}.json",
        transaction_root,
        "settings-ledger-receipt-invalid",
    )
    payload = {
        key: item for key, item in receipt.items() if key != "receipt_sha256"
    }
    if (
        receipt.get("receipt_sha256") != receipt_sha256
        or hash_json(payload) != receipt_sha256
        or receipt.get("status") != "committed"
        or receipt.get("action") != "disable"
        or receipt.get("plugin") != request["plugin"]
        or receipt.get("qualification_sha256")
        != request["qualification_sha256"]
        or not isinstance(receipt.get("before"), dict)
        or not isinstance(receipt.get("after"), dict)
        or receipt["after"].get("key_present") is not True
        or receipt["after"].get("key_value") is not False
    ):
        raise SettingsError("settings-ledger-receipt-invalid")
    runtime_before = load_private_object(
        Path(str(receipt.get("runtime_before_path", ""))),
        transaction_root,
        "settings-ledger-receipt-invalid",
    )
    runtime_after = load_private_object(
        Path(str(receipt.get("runtime_after_path", ""))),
        transaction_root,
        "settings-ledger-receipt-invalid",
    )
    if (
        hash_json(runtime_before) != receipt.get("runtime_before_sha256")
        or hash_json(runtime_after) != receipt.get("runtime_after_sha256")
    ):
        raise SettingsError("settings-ledger-receipt-invalid")
    preimage_path = Path(str(receipt["before"].get("preimage_path", "")))
    try:
        resolved_root = transaction_root.resolve()
        if preimage_path.is_symlink():
            raise SettingsError("settings-ledger-receipt-invalid")
        resolved_preimage = preimage_path.resolve()
        if resolved_root not in resolved_preimage.parents:
            raise SettingsError("settings-ledger-receipt-invalid")
        preimage = read_regular(
            resolved_preimage, "settings-ledger-receipt-invalid"
        ).data
    except OSError as error:
        raise SettingsError("settings-ledger-receipt-invalid") from error
    if hash_bytes(preimage) != receipt["before"].get("sha256"):
        raise SettingsError("settings-ledger-receipt-invalid")
    return receipt, runtime_before, runtime_after, preimage


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
    if transaction_root.is_symlink():
        raise SettingsError("settings-transaction-root-invalid")
    transaction_root.mkdir(parents=True, exist_ok=True)
    ensure_private_directory(
        transaction_root,
        transaction_root,
        "settings-transaction-root-invalid",
    )
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
        ledger = initialize_ledger(transaction_root)
        if ledger["ledger_sha256"] != request["expected"]["ledger_sha256"]:
            raise SettingsError("settings-ledger-mismatch")
        if any(
            item["settings_key"] == request["plugin"]["settings_key"]
            for item in ledger["active_disables"]
        ):
            raise SettingsError("settings-ledger-order-conflict")
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
        operation_root = ensure_private_directory(
            transaction_root / request["operation_id"],
            transaction_root,
            "settings-operation-root-invalid",
        )
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
            immutable_json(
                operation_root / "runtime-before.json", runtime_before
            )
            immutable_json(
                operation_root / "runtime-after.json", runtime_after
            )
            receipt_payload = {
                "schema_version": SCHEMA_VERSION,
                "status": "committed",
                "action": "disable",
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
                "runtime_before_path": str(
                    operation_root / "runtime-before.json"
                ),
                "runtime_after_path": str(
                    operation_root / "runtime-after.json"
                ),
                "ledger": {
                    "sequence": ledger["sequence"] + 1,
                    "previous_entry_sha256": ledger["entry_sha256"],
                    "previous_ledger_sha256": ledger["ledger_sha256"],
                },
            }
            receipt = {
                **receipt_payload,
                "receipt_sha256": hash_json(receipt_payload),
            }
            immutable_json(operation_root / "receipt.json", receipt)
            immutable_json(
                transaction_root
                / "ledger/receipts"
                / f"{receipt['receipt_sha256']}.json",
                receipt,
            )
            next_ledger = append_ledger(
                transaction_root=transaction_root,
                current=ledger,
                receipt=receipt,
                action="disable",
            )
            preserve_staged = True
            exchanged = False
            remove_file(recovery_state)
            fence_active = False
            return {"ok": True, **receipt, "ledger_after": next_ledger}
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


def execute_restore(
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
    request = validate_restore_request(request)
    if recovery_state.exists():
        raise SettingsError("estate-recovery-required")
    qualification = load_object(
        qualification_root / f"{request['qualification_sha256']}.json",
        "plugin-source-unqualified",
    )
    validate_qualification(qualification, request)
    if transaction_root.is_symlink():
        raise SettingsError("settings-transaction-root-invalid")
    transaction_root.mkdir(parents=True, exist_ok=True)
    ensure_private_directory(
        transaction_root,
        transaction_root,
        "settings-transaction-root-invalid",
    )
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
        ledger = initialize_ledger(transaction_root)
        expected = request["expected"]
        if ledger["ledger_sha256"] != expected["ledger_sha256"]:
            raise SettingsError("settings-ledger-mismatch")
        if (
            not ledger["active_disables"]
            or ledger["active_disables"][-1]["receipt_sha256"]
            != request["restores_receipt_sha256"]
        ):
            raise SettingsError("settings-ledger-order-conflict")
        (
            disable_receipt,
            disable_runtime_before,
            _disable_runtime_after,
            disable_preimage,
        ) = load_disable_receipt(transaction_root, request)
        current = read_regular(settings_path, "settings-preimage-invalid")
        if (
            current.sha256 != expected["settings_sha256"]
            or current.device != expected["settings_device"]
            or current.inode != expected["settings_inode"]
            or current.mode != expected["settings_mode"]
        ):
            raise SettingsError("settings-preimage-mismatch")
        current_document = settings_document(current)
        key = request["plugin"]["settings_key"]
        if current_document.get("enabledPlugins", {}).get(key) is not False:
            raise SettingsError("settings-target-key-conflict")
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
            or runtime_before["plugin_enabled"] is not False
            or runtime_before["owned_capability_ids"]
        ):
            raise SettingsError("plugin-runtime-before-mismatch")

        exact_round_trip = (
            current.sha256 == disable_receipt["after"]["sha256"]
        )
        if exact_round_trip:
            output = disable_preimage
            output_mode = int(disable_receipt["before"]["mode"])
            restored_document = json.loads(output.decode("utf-8"))
        else:
            restored_document = copy.deepcopy(current_document)
            restored_enabled = restored_document["enabledPlugins"]
            if disable_receipt["before"]["key_present"]:
                restored_enabled[key] = disable_receipt["before"]["key_value"]
            else:
                restored_enabled.pop(key)
            output = (
                json.dumps(restored_document, indent=2, sort_keys=True).encode(
                    "utf-8"
                )
                + b"\n"
            )
            output_mode = current.mode
        if (
            not isinstance(restored_document, dict)
            or (
                disable_receipt["before"]["key_present"]
                and restored_document.get("enabledPlugins", {}).get(key)
                != disable_receipt["before"]["key_value"]
            )
            or (
                not disable_receipt["before"]["key_present"]
                and key in restored_document.get("enabledPlugins", {})
            )
        ):
            raise SettingsError("settings-semantic-verification-failed")

        operation_root = ensure_private_directory(
            transaction_root / request["operation_id"],
            transaction_root,
            "settings-operation-root-invalid",
        )
        if operation_root.stat().st_dev != current.device:
            raise SettingsError("settings-transaction-volume-unsupported")
        immutable_bytes(operation_root / "before-settings", current.data)
        staged_path = settings_path.parent / (
            f".{settings_path.name}.dreaming-stage.{request['operation_id']}"
        )
        output_state: FileState | None = None
        exchanged = False
        preserve_staged = False
        fence_active = False
        try:
            swapper.verify_supported(settings_path.parent)
            output_state = write_stage(staged_path, output, output_mode)
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
            if displaced != current:
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
            if (
                disable_receipt["before"]["key_present"]
                and active_document.get("enabledPlugins", {}).get(key)
                != disable_receipt["before"]["key_value"]
            ) or (
                not disable_receipt["before"]["key_present"]
                and key in active_document.get("enabledPlugins", {})
            ):
                raise SettingsError("settings-semantic-verification-failed")
            runtime_after = validate_runtime(runtime_verifier(), request)
            restored_owned = sorted(
                disable_runtime_before["owned_capability_ids"]
            )
            expected_estate = sorted(
                set(runtime_before["estate_capability_ids"])
                | set(restored_owned)
            )
            if (
                runtime_after["plugin_enabled"] is not True
                or sorted(runtime_after["owned_capability_ids"])
                != restored_owned
                or sorted(runtime_after["estate_capability_ids"])
                != expected_estate
            ):
                raise SettingsError("plugin-runtime-after-mismatch")
            immutable_json(
                operation_root / "runtime-before.json", runtime_before
            )
            immutable_json(
                operation_root / "runtime-after.json", runtime_after
            )
            receipt_payload = {
                "schema_version": SCHEMA_VERSION,
                "status": "committed",
                "action": "restore",
                "operation_id": request["operation_id"],
                "request_sha256": request["request_sha256"],
                "restores_receipt_sha256": request[
                    "restores_receipt_sha256"
                ],
                "plugin": request["plugin"],
                "qualification_sha256": request["qualification_sha256"],
                "evidence": request["evidence"],
                "before": {
                    "sha256": current.sha256,
                    "device": current.device,
                    "inode": current.inode,
                    "mode": current.mode,
                    "key_present": True,
                    "key_value": False,
                    "preimage_path": str(operation_root / "before-settings"),
                    "retained_inode_path": str(staged_path),
                },
                "after": {
                    "sha256": active.sha256,
                    "device": active.device,
                    "inode": active.inode,
                    "mode": active.mode,
                    "key_present": disable_receipt["before"]["key_present"],
                    "key_value": disable_receipt["before"]["key_value"],
                },
                "runtime_before_sha256": hash_json(runtime_before),
                "runtime_after_sha256": hash_json(runtime_after),
                "runtime_before_path": str(
                    operation_root / "runtime-before.json"
                ),
                "runtime_after_path": str(
                    operation_root / "runtime-after.json"
                ),
                "ledger": {
                    "sequence": ledger["sequence"] + 1,
                    "previous_entry_sha256": ledger["entry_sha256"],
                    "previous_ledger_sha256": ledger["ledger_sha256"],
                },
                "exact_round_trip": exact_round_trip,
            }
            receipt = {
                **receipt_payload,
                "receipt_sha256": hash_json(receipt_payload),
            }
            immutable_json(operation_root / "receipt.json", receipt)
            immutable_json(
                transaction_root
                / "ledger/receipts"
                / f"{receipt['receipt_sha256']}.json",
                receipt,
            )
            next_ledger = append_ledger(
                transaction_root=transaction_root,
                current=ledger,
                receipt=receipt,
                action="restore",
            )
            preserve_staged = True
            exchanged = False
            remove_file(recovery_state)
            fence_active = False
            return {"ok": True, **receipt, "ledger_after": next_ledger}
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
    parser.add_argument("action", choices=("disable", "restore"))
    parser.add_argument("--request", required=True)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--transaction-root", required=True)
    parser.add_argument("--qualification-root", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--recovery-state", required=True)
    parser.add_argument(
        "--runtime-verifier", required=True, nargs=argparse.REMAINDER
    )
    args = parser.parse_args()
    if not args.runtime_verifier:
        parser.error("--runtime-verifier must be the final non-empty command")
    try:
        settings_path = Path(
            os.path.abspath(os.path.expanduser(args.settings))
        )
        raw_request = load_object(
            Path(args.request), "settings-request-malformed"
        )
        request = (
            validate_request(raw_request)
            if args.action == "disable"
            else validate_restore_request(raw_request)
        )
        executor = (
            execute_disable if args.action == "disable" else execute_restore
        )
        result = executor(
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
