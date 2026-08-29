#!/usr/bin/env bash
# Focused read-only projection checks for profile-funnel accounting.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." && pwd)"
TEST_ROOT="$REPO/.test-work"
mkdir -p "$TEST_ROOT"
WORK="$(mktemp -d "$TEST_ROOT/dashboard-accounting.XXXXXX")"
trap 'chmod -R u+w "$WORK" 2>/dev/null || true; rm -rf "$WORK"' EXIT

python3 - "$REPO" "$WORK" <<'PY'
import hashlib
import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

repo, root = map(Path, sys.argv[1:])
scripts = repo / "skills/skill-review/scripts"
sys.path.insert(0, str(scripts))

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, scripts / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

dashboard = load("dreaming_dashboard", "dreaming-dashboard.py")
profiles = load("task_profile_receipt", "task_profile_receipt.py")
audits = load("profile_audit_disposition", "profile_audit_disposition.py")
occurrences = load("task_occurrence", "task_occurrence.py")
accounting = load("task_pass_accounting", "task_pass_accounting.py")

state, data, skills = root / "state", root / "data", root / "skills"
control, orchestrator = root / "control", root / "orchestrator"
for path in (state, data, skills, control, orchestrator):
    path.mkdir(parents=True)
(state / "adapters.json").write_text(
    json.dumps({"max_profiles_per_run": 100, "max_reviews_per_run": 25}),
    encoding="utf-8",
)
token = state / "dashboard/access-token"
token.parent.mkdir()
token.write_text("A" * 43 + "\n", encoding="ascii")
token.chmod(0o600)
paths = dashboard.DashboardPaths(
    state, control, control / "skill-review", orchestrator, data, skills, repo,
    repo / "skills/skill-review/assets/dashboard", token,
    state / "skill-review/candidates/v1/records", data / "candidates/v1/packages",
)
no_candidate_paths = dashboard.DashboardPaths(
    state, control, control / "skill-review", orchestrator, data, skills, repo,
    repo / "skills/skill-review/assets/dashboard", token,
    state / "no-candidate-records", data / "candidates/v1/packages",
)

session_id = "copilot:dashboard-accounting"
snapshot = {
    "identity": {
        "qualified_session_id": session_id,
        "source_revision": "source-revision",
    },
    "events": [{
        "source_event_id": "goal-event",
        "kind": "user_message",
        "timestamp": "2026-08-28T12:00:00Z",
    }],
}
snapshot_sha = profiles._digest(snapshot)
(data / "snapshots").mkdir()
(data / "snapshots" / f"{snapshot_sha.removeprefix('sha256:')}.json").write_text(
    json.dumps(snapshot), encoding="utf-8"
)
procedure = {
    "trigger": "A bounded repeated dashboard task.",
    "outcome": "A reconciled reporting result.",
    "actions": ["Inspect retained authority."],
    "exclusions": ["Do not mutate Dreaming state."],
}
model_profile = {
    "source_event_ids": ["goal-event"],
    "goal_event_id": "goal-event",
    "occurred_at": "2026-08-28T12:00:00Z",
    "task_type": "dashboard-accounting",
    "abstract_summary": "A reusable accounting projection.",
    "reuse_value": "reusable-procedure",
    "procedure": procedure,
    "confidence": "high",
    "sensitive_source": False,
    "task_state": "completed",
}
profile = {
    **model_profile,
    "task_key": profiles._digest(
        {"qualified_session_id": session_id, "source_event_ids": ["goal-event"]}
    ),
    "profile_id": profiles._digest(
        {
            "qualified_session_id": session_id,
            **{
                key: value
                for key, value in model_profile.items()
                if key != "occurred_at"
            },
        }
    ),
    "procedure_fingerprint": profiles._digest(procedure),
}
executor_identity = {"adapter_id": "fixture-profiler", "capabilities": ["task-profile-v2"]}
receipt_body = {
    "schema_version": 2,
    "kind": "task_profile_receipt",
    "snapshot_sha256": snapshot_sha,
    "source_revision": "source-revision",
    "qualified_session_id": session_id,
    "observed_at": "2026-08-28T12:01:00Z",
    "executor": "fixture-profiler",
    "executor_identity": executor_identity,
    "model": "fixture-model",
    "profiles": [profile],
}
receipt = {
    **receipt_body,
    "profile_set_id": profiles._digest({
        "snapshot_sha256": snapshot_sha,
        "qualified_session_id": session_id,
        "profiles": [profile],
    }),
}
receipt["receipt_sha256"] = profiles._digest(receipt)
receipt_path = data / "task-profiles/v1" / f"{receipt['receipt_sha256'].removeprefix('sha256:')}.json"
receipt_path.parent.mkdir(parents=True)
receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

resolution = occurrences.build_resolution(
    profile=profile,
    receipt=receipt,
    relation="new-occurrence",
    review_contract="fixture-review-contract",
    review_executor="fixture-reviewer",
    review_executor_identity={"adapter_id": "fixture-reviewer"},
    decision_at="2026-08-28T12:02:00Z",
)
occurrences.persist(
    data / "task-occurrences/v2", state / "task-occurrence-index.json", resolution,
    project=False,
)
catalog_audit = {
    "outcome": "no-covering-skill",
    "reviewer_contract": "profile-catalog-audit-v1",
    "skill_name": None,
    "catalog_sha256": "sha256:" + "a" * 64,
    "catalog_skill_names": [],
    "tombstones_sha256": "sha256:" + "b" * 64,
    "skill_load_trace": [],
    "skill_load_trace_sha256": audits._digest([]),
    "candidate_group_id": str(uuid.uuid4()),
}
disposition = audits.build_profile_audit_disposition(
    receipt=receipt,
    profile=profile,
    review_executor="fixture-reviewer",
    review_executor_identity={"adapter_id": "fixture-reviewer"},
    review_result={
        "terminal_route": "skill",
        "summary": "No covering skill was loaded.",
        "routing_reason": "fixture-routing",
        "catalog_audit": catalog_audit,
    },
    reviewed_at=1_777_680_120,
    occurrence_resolution=resolution,
)
disposition_path = state / "profile-audit-dispositions/v3" / f"{profile['profile_id'].removeprefix('sha256:')}.json"
disposition_path.parent.mkdir(parents=True)
disposition_path.write_text(json.dumps(disposition), encoding="utf-8")

accounting_receipt = accounting.build_task_pass_accounting_receipt(
    pass_id="fixture-pass",
    queue_rows=[
        {"queue_row_id": "queue-profiled", "outcome": "newly-attempted", "profile_operation_ids": ["profile-op"]},
        {"queue_row_id": "queue-cached", "outcome": "cached-current-receipt", "profile_operation_ids": []},
        {"queue_row_id": "queue-deferred", "outcome": "eligible-deferred", "profile_operation_ids": []},
    ],
    profile_operations=[{"operation_id": "profile-op", "queue_row_id": "queue-profiled", "terminal": "profiled"}],
    profiles=[{"profile_id": profile["profile_id"], "queue_row_id": "queue-profiled", "profile_receipt_sha256": receipt["receipt_sha256"], "terminal": "reusable-dispositioned"}],
    review_rows=[{"review_row_id": "review-one", "queue_row_id": "queue-profiled", "profile_id": profile["profile_id"], "profile_receipt_sha256": receipt["receipt_sha256"], "outcome": "newly-attempted", "operation_id": "review-op"}],
    review_operations=[{"operation_id": "review-op", "profile_id": profile["profile_id"], "profile_receipt_sha256": receipt["receipt_sha256"], "terminal": "recurrence-ready"}],
    review_terminals=[{"operation_id": "review-op", "profile_id": profile["profile_id"], "profile_receipt_sha256": receipt["receipt_sha256"], "terminal": "recurrence-ready"}],
    profile_stop_reason="session-limit",
    review_stop_reason="eligible-exhausted",
)
accounting_path = data / "task-opportunity-accounting/v1" / f"{accounting_receipt['receipt_sha256'].removeprefix('sha256:')}.json"
accounting_path.parent.mkdir(parents=True)
accounting_path.write_text(json.dumps(accounting_receipt), encoding="utf-8")

os.environ["DREAMING_STATE_DIR"] = str(state)
os.environ["DREAMING_DATA_DIR"] = str(data)
os.environ["DREAMING_NOW_EPOCH"] = "1777680180"
lifecycle = load("candidate_lifecycle", "candidate-lifecycle.py")
candidate_source = root / "candidate-source"
candidate_source.mkdir()
(candidate_source / "SKILL.md").write_text(
    "---\nname: accounting-skill\ndescription: A test-only immutable package.\n---\n",
    encoding="utf-8",
)
candidate_procedure = lifecycle.validate_procedure({
    "schema_version": 1,
    "trigger": "A bounded dashboard accounting task.",
    "outcome": "A reconciled dashboard result.",
    "actions": ["Read immutable authority."],
    "exclusions": ["Do not mutate state."],
    "match_fingerprint": "sha256:" + "c" * 64,
})
observation = lifecycle.validate_observation({
    "task_key": profile["task_key"],
    "source_session_id": session_id,
    "canonical_occurrence_id": resolution["canonical_occurrence_id"],
    "occurred_at": resolution["occurred_at"],
    "decision_at": resolution["decision_at"],
    "resolution_sha256": resolution["resolution_sha256"],
    "summary": "A current canonical occurrence.",
    "procedure_fingerprint": candidate_procedure["match_fingerprint"],
}, candidate_procedure)
lifecycle_id = str(uuid.uuid4())
candidate_id, candidate_files, _ = lifecycle.make_immutable_package(
    lifecycle_id, candidate_source, "accounting-skill"
)
lifecycle.persist(lifecycle.new_record(
    lifecycle_id, "accounting-skill", candidate_procedure, observation,
    candidate_id, candidate_files, "different", "new-procedure-observed",
    None, [], [],
))
candidate = dashboard.DashboardData(paths).candidate_detail(lifecycle_id)
assert (
    candidate["status"] == "shadow"
    and candidate["evidence"]["current_canonical_occurrences"] == 1
    and candidate["evidence_items"][0]["session_id"] == session_id
), candidate
print("PASS  current lifecycle source_session_id evidence remains dashboard-readable")

candidate_rows, _ = dashboard.DashboardData(paths).candidate_rows()
assert (
    candidate_rows[0]["evidence_items"][0]["canonical_occurrence_id"]
    == resolution["canonical_occurrence_id"]
), candidate_rows
data_with_rows = dashboard.DashboardData(paths)
data_with_rows.candidate_rows = lambda: (candidate_rows, "fixture")
data_with_rows._candidate_record = lambda _path: (_ for _ in ()).throw(
    AssertionError("task opportunities reread a candidate record")
)
view = data_with_rows.task_opportunities()
assert view["status"] == "reconciled", view
assert view["profiles"] == {
    "receipts": 1, "profiles": 1, "reusable": 1, "no_learning": 0,
    "awaiting_catalog_audit": 0, "audited": 1,
}, view
assert view["catalog_audit_outcomes"]["no-covering-skill"] == 1, view
assert view["occurrences"]["canonical_occurrences"] == 1, view
assert view["candidates"]["current_canonical_occurrences"] == 1, view
assert view["accounting"]["terminal_totals"]["queue"] == {
    "cached-current-receipt": 1, "eligible-deferred": 1, "newly-attempted": 1,
}, view
assert view["accounting"]["capacity_limits"] == {"profiles": 100, "reviews": 25}, view
print("PASS  valid owner receipts reconcile profiles, audit, occurrence, capacity, backlog, and terminals without rereading candidates")

(state / "adapters.json").write_text("{}", encoding="utf-8")
view = dashboard.DashboardData(no_candidate_paths).task_opportunities()
assert (
    view["status"] == "reconciled"
    and view["candidates"]["records"] == 0
    and view["accounting"]["capacity_limits"] == {"profiles": 100, "reviews": 25}
), view
print("PASS  missing capacity settings project scheduler-effective defaults")
(state / "adapters.json").write_text(
    json.dumps({"max_profiles_per_run": 100, "max_reviews_per_run": 25}),
    encoding="utf-8",
)

tampered = dict(accounting_receipt)
tampered["totals"] = {**tampered["totals"], "queue": {"newly-attempted": 99}}
accounting_path.write_text(json.dumps(tampered), encoding="utf-8")
view = dashboard.DashboardData(paths).task_opportunities()
assert view["status"] == "unhealthy" and any(
    error.startswith("accounting-invalid:") for error in view["errors"]
), view
print("PASS  tampered terminal totals fail closed instead of being projected")

accounting_path.write_text(json.dumps(accounting_receipt), encoding="utf-8")
(data / "task-profiles/v1" / "malformed.json").write_text("{", encoding="utf-8")
view = dashboard.DashboardData(paths).task_opportunities()
assert view["status"] == "unhealthy" and any(
    error.startswith("profile-receipt-invalid:malformed.json") for error in view["errors"]
), view
print("PASS  malformed profile authority remains explicitly unhealthy")
(data / "task-profiles/v1" / "malformed.json").unlink()

resolution_path = (
    data / "task-occurrences/v2"
    / f"{resolution['resolution_sha256'].removeprefix('sha256:')}.json"
)
resolution_text = resolution_path.read_text(encoding="utf-8")
resolution_path.unlink()
view = dashboard.DashboardData(no_candidate_paths).task_opportunities()
assert (
    view["status"] == "unhealthy"
    and view["profiles"]["audited"] == 0
    and view["catalog_audit_outcomes"]["no-covering-skill"] == 0
    and any("disposition-occurrence-unbound" in error for error in view["errors"])
), view
print("PASS  deleted disposition occurrence authority is unhealthy without candidates")
resolution_path.write_text(resolution_text, encoding="utf-8")

mismatched_resolution = occurrences.build_resolution(
    profile=profile,
    receipt=receipt,
    relation="boundary-unresolved",
    review_contract="fixture-review-contract",
    review_executor="fixture-reviewer",
    review_executor_identity={"adapter_id": "fixture-reviewer"},
    decision_at="2026-08-28T12:03:00Z",
)
mismatched_resolution_path = (
    data / "task-occurrences/v2"
    / f"{mismatched_resolution['resolution_sha256'].removeprefix('sha256:')}.json"
)
mismatched_resolution_path.write_text(json.dumps(mismatched_resolution), encoding="utf-8")
mismatched_disposition = {
    **disposition,
    "occurrence_resolution_sha256": mismatched_resolution["resolution_sha256"],
}
mismatched_disposition["disposition_sha256"] = audits._digest(
    {
        key: value
        for key, value in mismatched_disposition.items()
        if key != "disposition_sha256"
    }
)
disposition_path.write_text(json.dumps(mismatched_disposition), encoding="utf-8")
view = dashboard.DashboardData(no_candidate_paths).task_opportunities()
assert (
    view["status"] == "unhealthy"
    and view["profiles"]["audited"] == 0
    and view["catalog_audit_outcomes"]["no-covering-skill"] == 0
    and any("disposition-occurrence-mismatch" in error for error in view["errors"])
), view
print("PASS  mismatched disposition occurrence authority is unhealthy without candidates")
mismatched_resolution_path.unlink()
disposition_path.write_text(json.dumps(disposition), encoding="utf-8")

superseding_resolution = occurrences.build_resolution(
    profile=profile,
    receipt=receipt,
    relation="same-occurrence",
    review_contract="fixture-review-contract",
    review_executor="fixture-reviewer",
    review_executor_identity={"adapter_id": "fixture-reviewer"},
    decision_at="2026-08-28T12:03:00Z",
    prior_occurrence_ids=[resolution["canonical_occurrence_id"]],
    correction_attempt_sha256="sha256:" + "d" * 64,
    supersedes_resolution_sha256=resolution["resolution_sha256"],
)
superseding_resolution_path = (
    data / "task-occurrences/v2"
    / f"{superseding_resolution['resolution_sha256'].removeprefix('sha256:')}.json"
)
superseding_resolution_path.write_text(json.dumps(superseding_resolution), encoding="utf-8")
view = dashboard.DashboardData(no_candidate_paths).task_opportunities()
assert (
    view["status"] == "unhealthy"
    and view["profiles"]["audited"] == 0
    and view["catalog_audit_outcomes"]["no-covering-skill"] == 0
    and any("disposition-occurrence-superseded" in error for error in view["errors"])
), view
print("PASS  superseded disposition occurrence authority is unhealthy without candidates")
PY
