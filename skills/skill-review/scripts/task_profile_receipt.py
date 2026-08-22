"""Shared validation for task-profile receipts at consuming boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class TaskProfileReceiptError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _reject(reason: str) -> None:
    raise TaskProfileReceiptError(reason)


def validate_task_profile_receipt(
    receipt: Any,
    snapshot: Any,
    *,
    receipt_path: Path,
    expected_executor: str,
    expected_executor_identity: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        _reject("receipt-shape")
    expected_receipt_keys = {
        "schema_version",
        "kind",
        "profile_set_id",
        "snapshot_sha256",
        "source_revision",
        "qualified_session_id",
        "observed_at",
        "executor",
        "executor_identity",
        "model",
        "profiles",
        "receipt_sha256",
    }
    if set(receipt) != expected_receipt_keys:
        _reject("receipt-shape")
    if receipt.get("schema_version") != 1:
        _reject("schema-version")
    if receipt.get("kind") != "task_profile_receipt":
        _reject("receipt-kind")
    receipt_sha256 = receipt.get("receipt_sha256")
    if not isinstance(receipt_sha256, str):
        _reject("receipt-sha256")
    receipt_body = {
        key: value
        for key, value in receipt.items()
        if key != "receipt_sha256"
    }
    if _digest(receipt_body) != receipt_sha256:
        _reject("receipt-sha256")
    if receipt_path.name != f"{receipt_sha256.removeprefix('sha256:')}.json":
        _reject("receipt-filename")
    if not isinstance(snapshot, dict):
        _reject("snapshot-shape")
    identity = snapshot.get("identity")
    events = snapshot.get("events")
    if not isinstance(identity, dict) or not isinstance(events, list):
        _reject("snapshot-shape")
    if receipt.get("snapshot_sha256") != _digest(snapshot):
        _reject("snapshot-sha256")
    if (
        receipt.get("qualified_session_id")
        != identity.get("qualified_session_id")
    ):
        _reject("qualified-session-id")
    if receipt.get("source_revision") != identity.get("source_revision"):
        _reject("source-revision")
    if receipt.get("executor") != expected_executor:
        _reject("executor")
    if receipt.get("executor_identity") != expected_executor_identity:
        _reject("executor-identity")
    if (
        not isinstance(receipt.get("observed_at"), str)
        or not receipt["observed_at"]
        or not isinstance(receipt.get("model"), str)
        or not receipt["model"]
    ):
        _reject("receipt-metadata")

    available: dict[str, int] = {}
    for index, event in enumerate(events):
        event_id = event.get("source_event_id") if isinstance(event, dict) else None
        if not isinstance(event_id, str) or not event_id or event_id in available:
            _reject("snapshot-event-identity")
        available[event_id] = index

    profiles = receipt.get("profiles")
    if not isinstance(profiles, list) or len(profiles) > 8:
        _reject("profile-collection")
    expected_profile_keys = {
        "source_event_ids",
        "task_type",
        "abstract_summary",
        "reuse_value",
        "procedure",
        "confidence",
        "sensitive_source",
        "task_state",
        "task_key",
        "profile_id",
        "procedure_fingerprint",
    }
    seen_task_keys: set[str] = set()
    seen_profile_ids: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, dict) or set(profile) != expected_profile_keys:
            _reject("profile-shape")
        event_ids = profile.get("source_event_ids")
        if (
            not isinstance(event_ids, list)
            or not event_ids
            or len(event_ids) > 20
            or any(not isinstance(value, str) or not value for value in event_ids)
            or len(event_ids) != len(set(event_ids))
            or any(value not in available for value in event_ids)
            or event_ids != sorted(event_ids, key=available.__getitem__)
        ):
            _reject("profile-evidence")
        procedure = profile.get("procedure")
        reuse_value = profile.get("reuse_value")
        if (
            reuse_value
            not in {"reusable-procedure", "one-off", "no-durable-learning"}
            or profile.get("confidence") not in {"low", "medium", "high"}
            or type(profile.get("sensitive_source")) is not bool
            or profile.get("task_state")
            not in {"completed", "failed", "unresolved"}
            or any(
                not isinstance(profile.get(field), str)
                or not profile[field].strip()
                or len(profile[field].encode("utf-8")) > 4_000
                for field in ("task_type", "abstract_summary")
            )
            or (reuse_value == "reusable-procedure")
            is not isinstance(procedure, dict)
            or (reuse_value != "reusable-procedure" and procedure is not None)
        ):
            _reject("profile-semantics")
        if isinstance(procedure, dict):
            actions = procedure.get("actions")
            exclusions = procedure.get("exclusions")
            if (
                set(procedure)
                != {"trigger", "outcome", "actions", "exclusions"}
                or any(
                    not isinstance(procedure.get(field), str)
                    or not procedure[field].strip()
                    or len(procedure[field].encode("utf-8")) > 4_000
                    for field in ("trigger", "outcome")
                )
                or not isinstance(actions, list)
                or not 1 <= len(actions) <= 16
                or any(
                    not isinstance(value, str)
                    or not value.strip()
                    or len(value.encode("utf-8")) > 4_000
                    for value in actions
                )
                or not isinstance(exclusions, list)
                or not 1 <= len(exclusions) <= 16
                or any(
                    not isinstance(value, str)
                    or not value.strip()
                    or len(value.encode("utf-8")) > 4_000
                    for value in exclusions
                )
            ):
                _reject("profile-procedure")
        model_profile = {
            key: value
            for key, value in profile.items()
            if key not in {"task_key", "profile_id", "procedure_fingerprint"}
        }
        expected_task_key = _digest(
            {
                "qualified_session_id": identity["qualified_session_id"],
                "source_event_ids": event_ids,
            }
        )
        expected_profile_id = _digest(
            {
                "qualified_session_id": identity["qualified_session_id"],
                **model_profile,
            }
        )
        expected_procedure_fingerprint = (
            _digest(procedure) if isinstance(procedure, dict) else None
        )
        if profile.get("task_key") != expected_task_key:
            _reject("task-key")
        if profile.get("profile_id") != expected_profile_id:
            _reject("profile-id")
        if (
            profile.get("procedure_fingerprint")
            != expected_procedure_fingerprint
        ):
            _reject("procedure-fingerprint")
        if (
            expected_task_key in seen_task_keys
            or expected_profile_id in seen_profile_ids
        ):
            _reject("duplicate-profile-identity")
        seen_task_keys.add(expected_task_key)
        seen_profile_ids.add(expected_profile_id)

    expected_profile_set_id = _digest(
        {
            "snapshot_sha256": receipt["snapshot_sha256"],
            "qualified_session_id": identity["qualified_session_id"],
            "profiles": profiles,
        }
    )
    if receipt.get("profile_set_id") != expected_profile_set_id:
        _reject("profile-set-id")
    return {
        "receipt_sha256": receipt_sha256,
        "profile_set_id": expected_profile_set_id,
        "profiles": [
            profile
            for profile in profiles
            if profile["reuse_value"] == "reusable-procedure"
        ],
    }
