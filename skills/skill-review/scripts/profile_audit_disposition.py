"""Immutable, profile-bound terminal dispositions for expensive reviews."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, NoReturn

CURRENT_PROFILE_AUDIT_CONTRACT_VERSION = 3
KNOWN_PROFILE_AUDIT_CONTRACT_VERSIONS = frozenset({1, 2, 3})
CATALOG_AUDIT_OUTCOMES = frozenset(
    {
        "correct-skill",
        "missed-skill",
        "wrong-or-incomplete-skill",
        "no-covering-skill",
    }
)


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
    occurrence_resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog_audit = review_result.get("catalog_audit")
    contract_version = (
        3
        if occurrence_resolution is not None and isinstance(catalog_audit, dict)
        else 2
        if occurrence_resolution is not None
        else 1
    )
    disposition = {
        "schema_version": contract_version,
        "kind": "task_profile_audit_disposition",
        "profile_audit_contract_version": contract_version,
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
        "outcome": (
            catalog_audit["outcome"]
            if contract_version == 3
            else f"reviewed-terminal-v{contract_version}"
        ),
        "terminal_route": review_result["terminal_route"],
        "summary": review_result["summary"],
        "routing_reason": review_result["routing_reason"],
        "review_result_sha256": _digest(review_result),
        "reviewed_at": reviewed_at,
        **(
            {
                "occurrence_resolution_sha256": occurrence_resolution[
                    "resolution_sha256"
                ],
                "canonical_occurrence_id": occurrence_resolution[
                    "canonical_occurrence_id"
                ],
                "boundary_relation": occurrence_resolution[
                    "boundary_relation"
                ],
            }
            if occurrence_resolution is not None
            else {}
        ),
        **(
            {
                "reviewer_contract": catalog_audit["reviewer_contract"],
                "catalog_skill_name": catalog_audit["skill_name"],
                "catalog_sha256": catalog_audit["catalog_sha256"],
                "catalog_skill_names": catalog_audit[
                    "catalog_skill_names"
                ],
                "tombstones_sha256": catalog_audit["tombstones_sha256"],
                "skill_load_trace": catalog_audit["skill_load_trace"],
                "skill_load_trace_sha256": catalog_audit[
                    "skill_load_trace_sha256"
                ],
                "candidate_group_id": catalog_audit["candidate_group_id"],
            }
            if contract_version == 3
            else {}
        ),
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
    common_keys = {
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
    }
    version = disposition.get("profile_audit_contract_version")
    occurrence_keys = (
        {
            "occurrence_resolution_sha256",
            "canonical_occurrence_id",
            "boundary_relation",
        }
        if version in {2, 3}
        else set()
    )
    catalog_keys = (
        {
            "reviewer_contract",
            "catalog_skill_name",
            "catalog_sha256",
            "catalog_skill_names",
            "tombstones_sha256",
            "skill_load_trace",
            "skill_load_trace_sha256",
            "candidate_group_id",
        }
        if version == 3
        else set()
    )
    expected_keys = common_keys | occurrence_keys | catalog_keys | {
        "disposition_sha256"
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
        disposition.get("schema_version") != version
        or disposition.get("kind") != "task_profile_audit_disposition"
        or disposition.get("profile_audit_contract_version")
        not in KNOWN_PROFILE_AUDIT_CONTRACT_VERSIONS
    ):
        _reject("disposition-contract")
    if version in {1, 2}:
        if disposition.get("outcome") != f"reviewed-terminal-v{version}":
            _reject("disposition-contract")
    elif disposition.get("outcome") not in CATALOG_AUDIT_OUTCOMES:
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
    if version in {2, 3}:
        if (
            disposition.get("boundary_relation")
            not in {
                "same-occurrence",
                "new-occurrence",
                "boundary-conflict",
                "boundary-unresolved",
            }
            or not isinstance(
                disposition.get("occurrence_resolution_sha256"), str
            )
            or not disposition["occurrence_resolution_sha256"].startswith(
                "sha256:"
            )
        ):
            _reject("disposition-occurrence")
        canonical_occurrence_id = disposition.get("canonical_occurrence_id")
        if disposition["boundary_relation"] in {
            "same-occurrence",
            "new-occurrence",
        }:
            if (
                not isinstance(canonical_occurrence_id, str)
                or not canonical_occurrence_id.startswith("sha256:")
            ):
                _reject("disposition-occurrence")
        elif canonical_occurrence_id is not None:
            _reject("disposition-occurrence")
    if version == 3:
        trace = disposition.get("skill_load_trace")
        skill_name = disposition.get("catalog_skill_name")
        catalog_names = disposition.get("catalog_skill_names")
        loaded_names = (
            {
                item.get("catalog_skill_name")
                for item in trace
                if isinstance(item, dict)
                and isinstance(item.get("catalog_skill_name"), str)
            }
            if isinstance(trace, list)
            else set()
        )
        if (
            disposition.get("reviewer_contract") != "profile-catalog-audit-v1"
            or not isinstance(trace, list)
            or not isinstance(catalog_names, list)
            or catalog_names != sorted(set(catalog_names))
            or any(
                not isinstance(name, str) or not name
                for name in catalog_names
            )
            or _digest(trace) != disposition.get("skill_load_trace_sha256")
            or any(
                not isinstance(item, dict)
                or set(item)
                != {
                    "source_event_id",
                    "invoked_name",
                    "catalog_skill_name",
                    "event_sha256",
                }
                or not isinstance(item.get("source_event_id"), str)
                or not item["source_event_id"]
                or not isinstance(item.get("invoked_name"), str)
                or not item["invoked_name"]
                or (
                    item.get("catalog_skill_name") is not None
                    and (
                        not isinstance(item["catalog_skill_name"], str)
                        or not item["catalog_skill_name"]
                    )
                )
                or not isinstance(item.get("event_sha256"), str)
                or not item["event_sha256"].startswith("sha256:")
                for item in trace
            )
            or any(
                not isinstance(disposition.get(field), str)
                or not disposition[field].startswith("sha256:")
                for field in ("catalog_sha256", "tombstones_sha256")
            )
            or (
                skill_name is not None
                and (not isinstance(skill_name, str) or not skill_name.strip())
            )
        ):
            _reject("disposition-catalog-audit")
        candidate_group_id = disposition.get("candidate_group_id")
        boundary_conflict = disposition.get("boundary_relation") in {
            "boundary-conflict",
            "boundary-unresolved",
        }
        if (
            disposition["outcome"] == "no-covering-skill"
            and not boundary_conflict
            and (
                not isinstance(candidate_group_id, str)
                or not re.fullmatch(
                    r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}",
                    candidate_group_id,
                )
            )
        ) or (
            (
                disposition["outcome"] != "no-covering-skill"
                or boundary_conflict
            )
            and candidate_group_id is not None
        ):
            _reject("disposition-candidate-group")
        if (
            disposition["outcome"] == "no-covering-skill"
            and skill_name is not None
        ) or (
            disposition["outcome"] != "no-covering-skill"
            and not isinstance(skill_name, str)
        ) or (
            disposition["outcome"] == "correct-skill"
            and (
                skill_name not in catalog_names
                or skill_name not in loaded_names
            )
        ) or (
            disposition["outcome"] == "missed-skill"
            and (
                skill_name not in catalog_names
                or skill_name in loaded_names
            )
        ) or (
            disposition["outcome"] == "wrong-or-incomplete-skill"
            and (
                skill_name not in catalog_names
                or skill_name not in loaded_names
            )
        ):
            _reject("disposition-catalog-audit")
    return disposition
