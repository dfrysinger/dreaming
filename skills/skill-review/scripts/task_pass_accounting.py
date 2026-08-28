"""Immutable, identity-bound accounting for one Dreaming scheduler pass."""

from __future__ import annotations

import hashlib
import json
from typing import Any, NoReturn

ACCOUNTING_SCHEMA_VERSION = 1
QUEUE_OUTCOMES = frozenset(
    {
        "cached-current-receipt",
        "active-unsettled",
        "stale-superseded",
        "already-terminal",
        "deleted",
        "source-unavailable",
        "executor-unavailable",
        "malformed",
        "profile-failed",
        "newly-attempted",
        "eligible-deferred",
    }
)
PROFILE_OPERATION_TERMINALS = frozenset(
    {"profiled", "malformed", "failed", "deleted", "stale"}
)
PROFILE_TERMINALS = frozenset(
    {"no-learning", "reusable-awaiting-review", "reusable-dispositioned"}
)
REVIEW_OUTCOMES = frozenset(
    {
        "already-dispositioned",
        "no-learning",
        "raw-unprofiled",
        "stale-superseded",
        "deleted",
        "invalid-unbound",
        "known-superseded-contract",
        "eligible-deferred",
        "newly-attempted",
    }
)
REVIEW_OPERATION_TERMINALS = frozenset(
    {"dispositioned", "malformed", "failed", "stale"}
)


class TaskPassAccountingError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def _reject(reason: str) -> NoReturn:
    raise TaskPassAccountingError(reason)


def queue_row_identity(row: dict[str, Any]) -> str:
    return digest(
        {
            "qualified_session_id": row.get("qualified_session_id"),
            "source_revision": row.get("source_revision"),
            "source": row.get("source"),
            "queued_at": row.get("queued_at"),
        }
    )


def _ids(rows: Any, field: str, reason: str) -> set[str]:
    if not isinstance(rows, list):
        _reject(reason)
    values = [row.get(field) if isinstance(row, dict) else None for row in rows]
    if (
        any(not isinstance(value, str) or not value for value in values)
        or len(values) != len(set(values))
    ):
        _reject(reason)
    return set(values)


def _count(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        value = row[field]
        result[value] = result.get(value, 0) + 1
    return result


def build_task_pass_accounting_receipt(
    *,
    pass_id: str,
    queue_rows: list[dict[str, Any]],
    profile_operations: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    review_rows: list[dict[str, Any]],
    review_operations: list[dict[str, Any]],
    review_terminals: list[dict[str, Any]],
    profile_stop_reason: str,
    review_stop_reason: str,
) -> dict[str, Any]:
    receipt = {
        "schema_version": ACCOUNTING_SCHEMA_VERSION,
        "kind": "task_opportunity_pass_accounting",
        "pass_id": pass_id,
        "queue_rows": queue_rows,
        "profile_operations": profile_operations,
        "profiles": profiles,
        "review_rows": review_rows,
        "review_operations": review_operations,
        "review_terminals": review_terminals,
        "profile_stop_reason": profile_stop_reason,
        "review_stop_reason": review_stop_reason,
        "totals": {
            "queue": _count(queue_rows, "outcome"),
            "profile_operations": _count(profile_operations, "terminal"),
            "profiles": _count(profiles, "terminal"),
            "review_rows": _count(review_rows, "outcome"),
            "review_operations": _count(review_operations, "terminal"),
            "review_terminals": _count(review_terminals, "terminal"),
        },
    }
    return {**receipt, "receipt_sha256": digest(receipt)}


def validate_task_pass_accounting_receipt(receipt: Any) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        _reject("accounting-shape")
    required = {
        "schema_version",
        "kind",
        "pass_id",
        "queue_rows",
        "profile_operations",
        "profiles",
        "review_rows",
        "review_operations",
        "review_terminals",
        "profile_stop_reason",
        "review_stop_reason",
        "totals",
        "receipt_sha256",
    }
    if set(receipt) != required:
        _reject("accounting-shape")
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if (
        receipt["schema_version"] != ACCOUNTING_SCHEMA_VERSION
        or receipt["kind"] != "task_opportunity_pass_accounting"
        or not isinstance(receipt["pass_id"], str)
        or not receipt["pass_id"]
        or not isinstance(receipt["receipt_sha256"], str)
        or digest(body) != receipt["receipt_sha256"]
    ):
        _reject("accounting-identity")
    queue_ids = _ids(receipt["queue_rows"], "queue_row_id", "queue-identity")
    profile_operation_ids = _ids(
        receipt["profile_operations"], "operation_id", "profile-operation-identity"
    )
    profile_ids = _ids(receipt["profiles"], "profile_id", "profile-identity")
    review_row_ids = _ids(receipt["review_rows"], "review_row_id", "review-row-identity")
    review_operation_ids = _ids(
        receipt["review_operations"], "operation_id", "review-operation-identity"
    )
    review_terminal_ids = _ids(
        receipt["review_terminals"], "operation_id", "review-terminal-identity"
    )
    if review_operation_ids != review_terminal_ids:
        _reject("review-operation-terminal-unmatched")
    if any(
        row.get("outcome") not in QUEUE_OUTCOMES
        or set(row) != {"queue_row_id", "outcome", "profile_operation_id"}
        or (
            row["profile_operation_id"] is not None
            and row["profile_operation_id"] not in profile_operation_ids
        )
        for row in receipt["queue_rows"]
    ):
        _reject("queue-terminal-invalid")
    if any(
        row.get("terminal") not in PROFILE_OPERATION_TERMINALS
        or set(row) != {"operation_id", "queue_row_id", "terminal"}
        or row["queue_row_id"] not in queue_ids
        for row in receipt["profile_operations"]
    ):
        _reject("profile-operation-terminal-invalid")
    linked_profile_operations = {
        row["profile_operation_id"]
        for row in receipt["queue_rows"]
        if row["profile_operation_id"] is not None
    }
    if linked_profile_operations != profile_operation_ids:
        _reject("profile-operation-unmatched")
    if any(
        row.get("terminal") not in PROFILE_TERMINALS
        or set(row)
        != {"profile_id", "queue_row_id", "profile_receipt_sha256", "terminal"}
        or row["queue_row_id"] not in queue_ids
        or not isinstance(row["profile_receipt_sha256"], str)
        or not row["profile_receipt_sha256"].startswith("sha256:")
        for row in receipt["profiles"]
    ):
        _reject("profile-terminal-invalid")
    if any(
        row.get("outcome") not in REVIEW_OUTCOMES
        or set(row)
        != {
            "review_row_id",
            "queue_row_id",
            "profile_id",
            "profile_receipt_sha256",
            "outcome",
            "operation_id",
        }
        or row["queue_row_id"] not in queue_ids
        or (
            row["profile_id"] is not None
            and row["profile_id"] not in profile_ids
        )
        or (
            row["profile_id"] is not None
            and (
                not isinstance(row["profile_receipt_sha256"], str)
                or not row["profile_receipt_sha256"].startswith("sha256:")
            )
        )
        or (
            row["profile_id"] is None
            and row["profile_receipt_sha256"] is not None
        )
        or (
            row["operation_id"] is not None
            and row["operation_id"] not in review_operation_ids
        )
        or (
            row["outcome"] == "newly-attempted"
            and (
                not isinstance(row["profile_id"], str)
                or not isinstance(row["operation_id"], str)
            )
        )
        or (
            row["outcome"] != "newly-attempted"
            and row["operation_id"] is not None
        )
        or (
            row["outcome"] == "eligible-deferred"
            and not isinstance(row["profile_id"], str)
        )
        for row in receipt["review_rows"]
    ):
        _reject("review-row-terminal-invalid")
    linked_review_operations = {
        row["operation_id"]
        for row in receipt["review_rows"]
        if row["operation_id"] is not None
    }
    if linked_review_operations != review_operation_ids:
        _reject("review-operation-unmatched")
    if any(
        row.get("terminal") not in REVIEW_OPERATION_TERMINALS
        or set(row)
        != {"operation_id", "profile_id", "profile_receipt_sha256", "terminal"}
        or row["operation_id"] not in review_operation_ids
        or row["profile_id"] not in profile_ids
        or not isinstance(row["profile_receipt_sha256"], str)
        or not row["profile_receipt_sha256"].startswith("sha256:")
        for row in receipt["review_operations"]
    ):
        _reject("review-operation-terminal-invalid")
    if any(
        row.get("terminal") not in REVIEW_OPERATION_TERMINALS
        or set(row)
        != {"operation_id", "profile_id", "profile_receipt_sha256", "terminal"}
        for row in receipt["review_terminals"]
    ):
        _reject("review-terminal-invalid")
    if receipt["review_operations"] != receipt["review_terminals"]:
        _reject("review-terminal-mismatch")
    expected_totals = {
        "queue": _count(receipt["queue_rows"], "outcome"),
        "profile_operations": _count(receipt["profile_operations"], "terminal"),
        "profiles": _count(receipt["profiles"], "terminal"),
        "review_rows": _count(receipt["review_rows"], "outcome"),
        "review_operations": _count(receipt["review_operations"], "terminal"),
        "review_terminals": _count(receipt["review_terminals"], "terminal"),
    }
    if receipt["totals"] != expected_totals:
        _reject("accounting-totals")
    if (
        not isinstance(receipt["profile_stop_reason"], str)
        or not receipt["profile_stop_reason"]
        or not isinstance(receipt["review_stop_reason"], str)
        or not receipt["review_stop_reason"]
    ):
        _reject("accounting-stop-reason")
    return receipt
