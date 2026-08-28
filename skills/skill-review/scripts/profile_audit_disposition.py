"""Immutable, profile-bound terminal dispositions for expensive reviews."""

from __future__ import annotations

import hashlib
import json
from typing import Any, NoReturn

CURRENT_PROFILE_AUDIT_CONTRACT_VERSION = 1
KNOWN_PROFILE_AUDIT_CONTRACT_VERSIONS = frozenset({1})


class ProfileAuditDispositionError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _reject(reason: str) -> NoReturn:
    raise ProfileAuditDispositionError(reason)


def build_profile_audit_disposition(
    *,
    receipt: dict[str, Any],
    profile: dict[str, Any],
    review_executor: str,
    review_executor_identity: dict[str, Any],
    review_result: dict[str, Any],
    reviewed_at: int,
) -> dict[str, Any]:
    disposition = {
        "schema_version": 1,
        "kind": "task_profile_audit_disposition",
        "profile_audit_contract_version": CURRENT_PROFILE_AUDIT_CONTRACT_VERSION,
        "profile_id": profile["profile_id"],
        "task_key": profile["task_key"],
        "profile_sha256": _digest(profile),
        "profile_receipt_sha256": receipt["receipt_sha256"],
        "profile_set_id": receipt["profile_set_id"],
        "snapshot_sha256": receipt["snapshot_sha256"],
        "qualified_session_id": receipt["qualified_session_id"],
        "source_revision": receipt["source_revision"],
        "profile_executor": receipt["executor"],
        "profile_executor_identity": receipt["executor_identity"],
        "review_executor": review_executor,
        "review_executor_identity": review_executor_identity,
        # The four catalog-audit outcomes are deliberately not represented until
        # their reviewer contract exists.  This only claims successful terminal
        # execution of the current full-review contract.
        "outcome": "reviewed-terminal-v1",
        "terminal_route": review_result["terminal_route"],
        "summary": review_result["summary"],
        "routing_reason": review_result["routing_reason"],
        "review_result_sha256": _digest(review_result),
        "reviewed_at": reviewed_at,
    }
    return {**disposition, "disposition_sha256": _digest(disposition)}


def validate_profile_audit_disposition(
    disposition: Any,
    *,
    receipt: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(disposition, dict):
        _reject("disposition-shape")
    expected_keys = {
        "schema_version",
        "kind",
        "profile_audit_contract_version",
        "profile_id",
        "task_key",
        "profile_sha256",
        "profile_receipt_sha256",
        "profile_set_id",
        "snapshot_sha256",
        "qualified_session_id",
        "source_revision",
        "profile_executor",
        "profile_executor_identity",
        "review_executor",
        "review_executor_identity",
        "outcome",
        "terminal_route",
        "summary",
        "routing_reason",
        "review_result_sha256",
        "reviewed_at",
        "disposition_sha256",
    }
    if set(disposition) != expected_keys:
        _reject("disposition-shape")
    body = {
        key: value for key, value in disposition.items() if key != "disposition_sha256"
    }
    if (
        not isinstance(disposition.get("disposition_sha256"), str)
        or _digest(body) != disposition["disposition_sha256"]
    ):
        _reject("disposition-sha256")
    if (
        disposition.get("schema_version") != 1
        or disposition.get("kind") != "task_profile_audit_disposition"
        or disposition.get("profile_audit_contract_version")
        not in KNOWN_PROFILE_AUDIT_CONTRACT_VERSIONS
        or disposition.get("outcome") != "reviewed-terminal-v1"
    ):
        _reject("disposition-contract")
    expected = {
        "profile_id": profile.get("profile_id"),
        "task_key": profile.get("task_key"),
        "profile_sha256": _digest(profile),
        "qualified_session_id": receipt.get("qualified_session_id"),
    }
    if any(disposition.get(key) != value for key, value in expected.items()):
        _reject("disposition-binding")
    if (
        disposition.get("terminal_route")
        not in {"instruction", "factual_memory", "skill", "support_file", "discard"}
        or any(
            not isinstance(disposition.get(field), str)
            or not disposition[field].strip()
            for field in (
                "review_executor",
                "summary",
                "routing_reason",
                "review_result_sha256",
            )
        )
        or not isinstance(disposition.get("review_executor_identity"), dict)
        or not disposition["review_executor_identity"]
        or any(
            not isinstance(disposition.get(field), str)
            or not disposition[field].startswith("sha256:")
            for field in (
                "profile_receipt_sha256",
                "profile_set_id",
                "snapshot_sha256",
            )
        )
        or not isinstance(disposition.get("source_revision"), str)
        or not disposition["source_revision"]
        or not isinstance(disposition.get("profile_executor"), str)
        or not disposition["profile_executor"]
        or not isinstance(disposition.get("profile_executor_identity"), dict)
        or not disposition["profile_executor_identity"]
        or isinstance(disposition.get("reviewed_at"), bool)
        or not isinstance(disposition.get("reviewed_at"), int)
    ):
        _reject("disposition-metadata")
    return disposition
