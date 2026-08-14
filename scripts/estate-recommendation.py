#!/usr/bin/env python3
"""Persist sanitized estate recommendations for the local dashboard."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RecommendationError(RuntimeError):
    pass


SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,199}")
AUTHORITIES = {
    "dreaming_managed",
    "legacy_machine",
    "user_protected",
    "unknown_provenance",
    "plugin_managed",
}
DECISIONS = {
    "keep",
    "consolidate",
    "archive",
    "disable",
    "restore",
    "manual_review",
}


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise RecommendationError("estate-recommendation-ledger-invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RecommendationError(
            "estate-recommendation-ledger-invalid"
        ) from error
    if not isinstance(value, list):
        raise RecommendationError("estate-recommendation-ledger-invalid")
    for record in value:
        validate_record(record)
    return value


def validate_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "action_id",
        "target",
        "authority",
        "decision",
        "status",
        "target_kind",
        "at",
        "record_sha256",
    }:
        raise RecommendationError("estate-recommendation-record-invalid")
    payload = {
        key: item for key, item in value.items() if key != "record_sha256"
    }
    try:
        timestamp = datetime.fromisoformat(str(value.get("at")))
    except ValueError as error:
        raise RecommendationError(
            "estate-recommendation-record-invalid"
        ) from error
    if (
        not all(
            isinstance(value.get(field), str) and value[field]
            for field in (
                "action_id",
                "target",
                "authority",
                "decision",
                "at",
            )
        )
        or not SAFE_ID_RE.fullmatch(value["action_id"])
        or not SAFE_ID_RE.fullmatch(value["target"])
        or value.get("authority") not in AUTHORITIES
        or value.get("decision") not in DECISIONS
        or timestamp.tzinfo is None
        or value.get("target_kind") not in {"personal_skill", "plugin"}
        or value.get("status")
        not in {"recommended", "kept", "protected", "unknown"}
        or value.get("record_sha256") != digest(payload)
    ):
        raise RecommendationError("estate-recommendation-record-invalid")
    return value


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
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
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise RecommendationError(
            "estate-recommendation-write-failed"
        ) from error


def load_census(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RecommendationError("estate-recommendation-census-invalid")
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RecommendationError(
            "estate-recommendation-census-invalid"
        ) from error
    if not isinstance(current, dict) or set(current) != {
        "schema_version",
        "receipt_sha256",
        "snapshot_sha256",
        "census",
    }:
        raise RecommendationError("estate-recommendation-census-invalid")
    census = current.get("census")
    if not isinstance(census, dict):
        raise RecommendationError("estate-recommendation-census-invalid")
    snapshot = {
        key: item for key, item in census.items() if key != "snapshot_sha256"
    }
    scope = census.get("scope")
    if (
        current.get("schema_version") != 1
        or current.get("snapshot_sha256") != census.get("snapshot_sha256")
        or census.get("snapshot_sha256") != digest(snapshot)
        or not isinstance(scope, dict)
        or scope.get("complete") is not True
        or census.get("unresolved_mappings") != []
    ):
        raise RecommendationError("estate-recommendation-census-invalid")
    receipt_sha256 = current.get("receipt_sha256")
    if not isinstance(receipt_sha256, str) or not receipt_sha256.startswith(
        "sha256:"
    ):
        raise RecommendationError("estate-recommendation-census-invalid")
    receipt_path = (
        path.parent
        / "estate-census-receipts"
        / f"{receipt_sha256.removeprefix('sha256:')}.json"
    )
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise RecommendationError("estate-recommendation-census-invalid")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RecommendationError(
            "estate-recommendation-census-invalid"
        ) from error
    if (
        not isinstance(receipt, dict)
        or digest(receipt) != receipt_sha256
        or receipt.get("census") != census
        or receipt.get("snapshot_sha256") != census["snapshot_sha256"]
    ):
        raise RecommendationError("estate-recommendation-census-invalid")
    return current


def target_authority(
    census: dict[str, Any], target_kind: str, target: str
) -> str:
    if target_kind == "plugin":
        matches = [
            plugin
            for plugin in census.get("plugins", [])
            if isinstance(plugin, dict) and plugin.get("plugin_id") == target
        ]
        if len(matches) != 1:
            raise RecommendationError(
                "estate-recommendation-target-invalid"
            )
        return "plugin_managed"
    personal_instance_ids = {
        instance.get("instance_id")
        for instance in census.get("physical_instances", [])
        if isinstance(instance, dict)
        and instance.get("root_class") == "personal"
        and isinstance(instance.get("instance_id"), str)
    }
    matches = [
        instance
        for instance in census.get("enabled_instances", [])
        if isinstance(instance, dict)
        and instance.get("runtime_name") == target
        and instance.get("instance_id") in personal_instance_ids
    ]
    authorities = {
        instance.get("authority")
        for instance in matches
        if instance.get("authority") in AUTHORITIES
    }
    if not matches or len(authorities) != 1:
        raise RecommendationError("estate-recommendation-target-invalid")
    authority = authorities.pop()
    if authority == "plugin_managed":
        raise RecommendationError("estate-recommendation-target-invalid")
    return str(authority)


def record(args: argparse.Namespace) -> dict[str, Any]:
    state_input = Path(args.state_root).expanduser()
    if state_input.is_symlink():
        raise RecommendationError("estate-recommendation-state-invalid")
    state_input.mkdir(parents=True, exist_ok=True)
    state_root = state_input.resolve()
    if not state_root.is_dir():
        raise RecommendationError("estate-recommendation-state-invalid")
    path = state_root / "estate-action-ledger.json"
    census_path = (
        Path(os.path.abspath(os.path.expanduser(args.census)))
        if args.census
        else state_root / "estate-census-current.json"
    )
    current = load_census(census_path)
    authority = target_authority(
        current["census"], args.target_kind, args.target
    )
    action_id = "review-{}".format(
        digest(
            {"target_kind": args.target_kind, "target": args.target}
        ).removeprefix("sha256:")[:32],
    )
    payload = {
        "action_id": action_id,
        "target": args.target,
        "authority": authority,
        "decision": args.decision,
        "status": args.status,
        "target_kind": args.target_kind,
        "at": args.at or datetime.now(timezone.utc).isoformat(),
    }
    value = validate_record(
        {**payload, "record_sha256": digest(payload)}
    )
    lock_path = state_root / "estate-action-ledger.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = load_records(path)
        records = [
            existing
            for existing in records
            if existing["action_id"] != value["action_id"]
        ]
        records.append(value)
        records.sort(key=lambda item: (item["at"], item["action_id"]))
        atomic_json(path, records)
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    xdg_state = Path(
        os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")
    )
    parser.add_argument(
        "--state-root",
        default=os.environ.get(
            "DREAMING_STATE_DIR", str(xdg_state / "dreaming")
        ),
    )
    parser.add_argument("--census")
    parser.add_argument(
        "--target-kind",
        choices=("personal_skill", "plugin"),
        required=True,
    )
    parser.add_argument("--target", required=True)
    parser.add_argument("--decision", required=True)
    parser.add_argument(
        "--status",
        choices=("recommended", "kept", "protected", "unknown"),
        required=True,
    )
    parser.add_argument("--at")
    args = parser.parse_args()
    try:
        print(json.dumps(record(args), sort_keys=True))
    except (RecommendationError, OSError, TypeError, ValueError) as error:
        code = (
            str(error)
            if isinstance(error, RecommendationError)
            else "estate-recommendation-failed"
        )
        print(
            json.dumps({"ok": False, "error": {"code": code}}),
            file=os.sys.stderr,
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
