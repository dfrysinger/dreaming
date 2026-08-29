"""Deterministic routing from immutable catalog-audit dispositions to evaluation.

Every retained profile-audit disposition receives exactly one terminal
evaluation route.  Only a no-covering-skill disposition whose owner-derived
candidate lifecycle record has satisfied the three-current-independent-
occurrence gate may name an evaluation subject, and that subject is always the
exact immutable candidate package recorded by the lifecycle owner.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, NoReturn

EVALUATION_ROUTING_CONTRACT_VERSION = 1
EVALUATION_ROUTES = frozenset(
    {
        "legacy-not-routable",
        "no-change",
        "repair-recommendation",
        "recurrence-ineligible",
        "awaiting-recurrence",
        "candidate-evaluation",
    }
)
REPAIR_OUTCOMES = frozenset({"missed-skill", "wrong-or-incomplete-skill"})
UNRESOLVED_BOUNDARIES = frozenset({"boundary-conflict", "boundary-unresolved"})
AUTHORING_READY_STATES = frozenset({"ready_for_draft", "evaluating"})
REQUIRED_CURRENT_OCCURRENCES = 3
CURRENT_OCCURRENCE_WINDOW = timedelta(days=30)
# Prerequisites the funnel does not yet produce for the existing
# shadow-compile/shadow-execute/shadow-certify flow.  A routed candidate names
# them so the projection never implies an evaluation that cannot run, and no
# code path enters the evaluating state while they are unmet.  See
# docs/task-opportunity-shadow-evaluation-reframe.md.
SHADOW_EXECUTION_BLOCKERS = (
    "shadow-executor-authority-unconfigured",
    "shadow-suite-authority-unavailable",
)
UUID_RE = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}")
SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")


class EvaluationRoutingError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _reject(reason: str) -> NoReturn:
    raise EvaluationRoutingError(reason)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def candidate_record_digest(value: Any) -> str:
    """Reproduce the candidate-lifecycle record identity exactly."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def candidate_package_identity(files: Any) -> str:
    """Reproduce the candidate-lifecycle immutable package identity exactly."""
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _assert_unbound(lifecycle_record: Any, reason: str) -> None:
    if lifecycle_record is not None:
        _reject(reason)


def _current_occurrences(
    record: dict[str, Any], decision_at: datetime
) -> tuple[list[str], list[str]]:
    """Return the current distinct occurrence and evidence identities."""
    occurrences: dict[str, str] = {}
    evidence = record.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        _reject("candidate-evidence-unavailable")
    for item in evidence:
        if not isinstance(item, dict):
            _reject("candidate-evidence-unavailable")
        occurrence_id = item.get("canonical_occurrence_id")
        if occurrence_id is None:
            continue
        evidence_id = item.get("evidence_id")
        occurred_at = _parse_time(item.get("occurred_at"))
        if (
            not isinstance(occurrence_id, str)
            or not SHA256_RE.fullmatch(occurrence_id)
            or not isinstance(evidence_id, str)
            or not SHA256_RE.fullmatch(evidence_id)
            or occurred_at is None
        ):
            _reject("candidate-evidence-invalid")
        if occurred_at > decision_at:
            _reject("candidate-occurrence-not-yet-observed")
        if decision_at - occurred_at > CURRENT_OCCURRENCE_WINDOW:
            continue
        occurrences.setdefault(occurrence_id, evidence_id)
    return sorted(occurrences), sorted(occurrences.values())


def _evaluation_subject(
    record: dict[str, Any],
    occurrence_ids: list[str],
    evidence_ids: list[str],
) -> dict[str, Any]:
    lifecycle_id = record["lifecycle_id"]
    candidate_id = record.get("current_candidate_id")
    if not isinstance(candidate_id, str) or not SHA256_RE.fullmatch(candidate_id):
        _reject("candidate-revision-unavailable")
    revisions = record.get("candidate_revisions")
    if not isinstance(revisions, list):
        _reject("candidate-revision-unavailable")
    matched = [
        revision
        for revision in revisions
        if isinstance(revision, dict) and revision.get("candidate_id") == candidate_id
    ]
    if len(matched) != 1:
        _reject("candidate-revision-unavailable")
    revision = matched[0]
    files = revision.get("files")
    if not isinstance(files, list) or not files:
        _reject("candidate-package-unavailable")
    if candidate_package_identity(files) != candidate_id:
        _reject("candidate-package-tampered")
    if revision.get("package_path") != (
        f"candidates/v1/packages/{lifecycle_id}/{candidate_id}"
    ):
        _reject("candidate-package-unbound")
    return {
        "lifecycle_id": lifecycle_id,
        "candidate_id": candidate_id,
        "proposed_name": record["proposed_name"],
        "record_version": record["record_version"],
        "record_sha256": candidate_record_digest(record),
        "package_path": revision["package_path"],
        "package_file_count": len(files),
        "current_occurrence_count": len(occurrence_ids),
        "current_occurrence_ids": occurrence_ids,
        "current_evidence_ids": evidence_ids,
    }


def _validated_lifecycle_record(
    lifecycle_record: Any, candidate_group_id: str
) -> dict[str, Any]:
    if not isinstance(lifecycle_record, dict):
        _reject("candidate-group-unavailable")
    if lifecycle_record.get("lifecycle_id") != candidate_group_id:
        _reject("candidate-group-mismatch")
    if lifecycle_record.get("publication") != {"status": "shadow_only"}:
        _reject("candidate-not-shadow-only")
    if not isinstance(lifecycle_record.get("proposed_name"), str) or not lifecycle_record[
        "proposed_name"
    ]:
        _reject("candidate-group-unavailable")
    version = lifecycle_record.get("record_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        _reject("candidate-group-unavailable")
    evaluation = lifecycle_record.get("evaluation")
    if not isinstance(evaluation, dict) or not isinstance(
        evaluation.get("history"), list
    ):
        _reject("candidate-group-unavailable")
    if not isinstance(lifecycle_record.get("state"), str):
        _reject("candidate-group-unavailable")
    return lifecycle_record


def _recurrence_reasons(
    record: dict[str, Any], occurrence_ids: list[str]
) -> list[str]:
    reasons: list[str] = []
    if len(occurrence_ids) < REQUIRED_CURRENT_OCCURRENCES:
        reasons.append("fewer-than-three-current-distinct-occurrences")
    evaluation = record["evaluation"]
    if evaluation.get("status") != "shadow_ready":
        reasons.append("recurrence-not-shadow-ready")
    history = evaluation["history"]
    latest = history[-1] if history else None
    if (
        not isinstance(latest, dict)
        or latest.get("recommendation") != "ready_for_draft"
        or latest.get("candidate_id") != record.get("current_candidate_id")
        or latest.get("shadow_only") is not True
    ):
        reasons.append("recurrence-recommendation-not-current")
    if record["state"] not in AUTHORING_READY_STATES:
        reasons.append("candidate-state-not-authoring-ready")
    blockers = record.get("blockers")
    if not isinstance(blockers, dict):
        _reject("candidate-group-unavailable")
    if blockers.get("uncertain"):
        reasons.append("uncertain-match-blocker")
    if blockers.get("covering_lifecycle_ids"):
        reasons.append("covering-lifecycle-blocker")
    if blockers.get("tombstone_ids"):
        reasons.append("tombstone-blocker")
    return reasons


def build_evaluation_routing_row(
    *,
    disposition: dict[str, Any],
    lifecycle_record: dict[str, Any] | None = None,
    now: int,
) -> dict[str, Any]:
    """Route one immutable catalog-audit disposition to exactly one outcome."""
    if not isinstance(disposition, dict):
        _reject("disposition-shape")
    body = {
        key: value for key, value in disposition.items() if key != "disposition_sha256"
    }
    if _digest(body) != disposition.get("disposition_sha256"):
        _reject("disposition-sha256")
    if isinstance(now, bool) or not isinstance(now, int):
        _reject("decision-time-invalid")
    decision_at = datetime.fromtimestamp(now, timezone.utc)
    version = disposition.get("profile_audit_contract_version")
    outcome = disposition.get("outcome")
    candidate_group_id = disposition.get("candidate_group_id")
    boundary_relation = disposition.get("boundary_relation")
    subject: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    if version != 3:
        _assert_unbound(lifecycle_record, "legacy-disposition-unbound")
        route, reasons = "legacy-not-routable", ["legacy-audit-contract"]
    elif outcome == "correct-skill":
        _assert_unbound(lifecycle_record, "correct-skill-unbound")
        route, reasons = "no-change", ["correct-skill-terminal"]
    elif outcome in REPAIR_OUTCOMES:
        # A repair recommendation is decision-relevant only once it is bound to
        # owner-derived immutable candidate evidence.  This audit contract never
        # binds one, so the repair stays a report-only recommendation and any
        # supplied lifecycle record is refused rather than silently evaluated.
        _assert_unbound(lifecycle_record, "repair-recommendation-unbound")
        route, reasons = (
            "repair-recommendation",
            ["existing-skill-repair-report-only"],
        )
    elif outcome != "no-covering-skill":
        _reject("disposition-outcome")
    elif boundary_relation in UNRESOLVED_BOUNDARIES:
        _assert_unbound(lifecycle_record, "boundary-conflict-unbound")
        route, reasons = "recurrence-ineligible", ["boundary-not-one-to-one"]
    else:
        if not isinstance(candidate_group_id, str) or not UUID_RE.fullmatch(
            candidate_group_id
        ):
            _reject("candidate-group-unbound")
        record = _validated_lifecycle_record(lifecycle_record, candidate_group_id)
        occurrence_ids, evidence_ids = _current_occurrences(record, decision_at)
        reasons = _recurrence_reasons(record, occurrence_ids)
        if reasons:
            route = "awaiting-recurrence"
        else:
            route = "candidate-evaluation"
            subject = _evaluation_subject(record, occurrence_ids, evidence_ids)
            execution = {
                "available": not SHADOW_EXECUTION_BLOCKERS,
                "reasons": list(SHADOW_EXECUTION_BLOCKERS),
            }
    decision = {
        "schema_version": EVALUATION_ROUTING_CONTRACT_VERSION,
        "kind": "profile_evaluation_routing_row",
        "routing_contract_version": EVALUATION_ROUTING_CONTRACT_VERSION,
        "profile_id": disposition["profile_id"],
        "task_key": disposition["task_key"],
        "disposition_sha256": disposition["disposition_sha256"],
        "profile_audit_contract_version": version,
        "outcome": outcome,
        "boundary_relation": boundary_relation,
        "canonical_occurrence_id": disposition.get("canonical_occurrence_id"),
        "catalog_skill_name": disposition.get("catalog_skill_name"),
        "candidate_group_id": candidate_group_id,
        "route": route,
        "reasons": reasons,
        "requires_evaluation": route == "candidate-evaluation",
        "evaluation_subject": subject,
        "evaluation_execution": execution,
    }
    return {**decision, "decision_sha256": _digest(decision)}


def summarize_evaluation_routing(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Reconcile every routed disposition into one terminal accounting state."""
    routes = {route: 0 for route in sorted(EVALUATION_ROUTES)}
    for row in rows:
        route = row.get("route")
        if route not in routes:
            _reject("routing-summary-unknown-route")
        routes[route] += 1
    if sum(routes.values()) != len(rows):
        _reject("routing-summary-unreconciled")
    return {
        "total": len(rows),
        "routes": routes,
        "requires_evaluation": sum(
            1 for row in rows if row.get("requires_evaluation") is True
        ),
        "executable_evaluation": sum(
            1
            for row in rows
            if isinstance(row.get("evaluation_execution"), dict)
            and row["evaluation_execution"].get("available") is True
        ),
    }
