#!/usr/bin/env bash
# Focused checks for names, evidence anchors, candidate identity, and evaluation history.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." && pwd)"
TEST_ROOT="$REPO/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/dreaming-dashboard-contracts.XXXXXX")"
trap 'chmod -R u+w "$TMP" 2>/dev/null || true; rm -rf "$TMP"' EXIT

SKILLS_STATE_DIR="$TMP/control" python3 - "$REPO" "$TMP" <<'PY'
import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import uuid
from pathlib import Path

repo = Path(sys.argv[1])
root = Path(sys.argv[2])

def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, repo / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

adapter = load("dreaming_vendor_adapter", "skills/skill-review/scripts/dreaming-vendor-adapter.py")
envelope = load("evidence_envelope", "skills/skill-review/scripts/evidence-envelope.py")
evaluation = load("skill_evaluation", "skills/skill-review/scripts/skill-evaluation.py")
lifecycle = load("candidate_lifecycle", "skills/skill-review/scripts/candidate-lifecycle.py")
dashboard = load("dreaming_dashboard", "skills/skill-review/scripts/dreaming-dashboard.py")

passes = 0
def check(value, message):
    global passes
    if not value:
        raise AssertionError(message)
    passes += 1
    print(f"PASS  {message}")

name = adapter.display_name("  Cafe\u0301\n\tcontrol\u0007 value  ")
check(name == "Café control value" and unicodedata.is_normalized("NFC", name), "display names normalize Unicode and controls")
bounded = adapter.display_name("😀" * 100)
check(len(bounded.encode("utf-8")) <= 160 and not bounded.endswith("\ufffd"), "display names clip on a UTF-8 boundary")
check(adapter.display_name("\n\u0000\t") is None, "empty sanitized display names remain absent")

base = {
    "schema_version": 2,
    "skill": "fixture-skill",
    "created_by": "skill-review",
    "source_session_id": "copilot:fixture",
    "source_mode": "review",
    "review_prompt_version": "skill-review-1",
    "created_at": "2026-01-01T00:00:00Z",
    "evidence": [{
        "task_key": "task:fixture-0001",
        "session_id": "copilot:fixture",
        "observed_at": "2026-01-01T00:00:00Z",
        "independence": "verified",
        "evidence_kind": "successful-procedure",
        "summary": "Observed success.",
    }],
    "routing": {"destination": "skill", "reason": "Reusable procedure."},
    "claims": [],
    "evaluation": {"status": "not_evaluated"},
}
check(envelope.validate_envelope(copy.deepcopy(base)) == base, "historical unanchored evidence remains valid")
anchored = copy.deepcopy(base)
anchored["evidence"][0]["transcript_context"] = {
    "schema_version": 1,
    "snapshot_sha256": "a" * 64,
    "source_revision": "revision-1",
    "event_ids": ["event-1", "event-2"],
}
check(envelope.validate_envelope(anchored) == anchored, "valid immutable transcript anchor is accepted")
for event_ids in ([], ["event-1", "event-1"], [f"event-{index}" for index in range(21)]):
    invalid = copy.deepcopy(anchored)
    invalid["evidence"][0]["transcript_context"]["event_ids"] = event_ids
    try:
        envelope.validate_envelope(invalid)
        raise AssertionError("invalid anchor accepted")
    except envelope.EnvelopeError:
        pass
check(True, "empty, duplicate, and excessive anchor IDs fail closed")

state = root / "state"
control = root / "control"
review = control / "skill-review"
orchestrator = root / "orchestrator"
data = root / "data"
skills = root / "skills"
assets = repo / "skills/skill-review/assets/dashboard"
token = state / "dashboard/access-token"
for path in (state, control, review, orchestrator, data, skills):
    path.mkdir(parents=True, exist_ok=True)
token.parent.mkdir(parents=True, exist_ok=True)
token.write_text("A" * 43 + "\n", encoding="ascii")
token.chmod(0o600)

skill = skills / "fixture-skill"
skill.mkdir()
(skill / ".agent-created").write_text("", encoding="utf-8")
(skill / ".agent-created.json").write_text(json.dumps(base), encoding="utf-8")
(skill / ".skill-evaluation-cases.json").write_text("{}", encoding="utf-8")
(skill / ".skill-evaluation-policy.json").write_text("{}", encoding="utf-8")
(skill / "SKILL.md").write_text("---\nname: fixture-skill\ndescription: Fixture.\n---\n", encoding="utf-8")
paths = dashboard.DashboardPaths(
    state, control, review, orchestrator, data, skills, repo, assets, token
)
service = dashboard.DashboardData(paths)
dashboard_candidate = service._skill_candidate(skill)
evaluation_candidate, _ = evaluation.candidate_id(skill)
check(dashboard_candidate == evaluation_candidate, "dashboard candidate identity exactly matches evaluator inventory")
subprocess.run(["git", "-C", str(skills), "init", "-q"], check=True)
subprocess.run(
    ["git", "-C", str(skills), "config", "user.name", "Dashboard Fixture"],
    check=True,
)
subprocess.run(
    ["git", "-C", str(skills), "config", "user.email", "fixture@example.invalid"],
    check=True,
)
subprocess.run(["git", "-C", str(skills), "add", "."], check=True)
subprocess.run(
    ["git", "-C", str(skills), "commit", "-qm", "initial fixture skill"],
    check=True,
)

suite = {"cases": [{"id": "source-case", "class": "intended"}]}
verified = {
    "candidate_id": evaluation_candidate,
    "input_manifest_sha256": "sha256:" + "5" * 64,
    "suite_id": "sha256:" + "1" * 64,
    "policy_id": "sha256:" + "2" * 64,
    "policy": {
        "policy_kind": "capability_uplift",
        "required_executors": [{"name": "copilot"}],
        "advisory_executors": [],
    },
    "records": [
        {
            "executor": "copilot",
            "case_id": "source-case",
            "treatment": treatment,
            "status": status,
            "infrastructure_error": False,
            "cleanup_failed": False,
        }
        for treatment, status in (
            [("candidate", "pass")] * 3 + [("control", "regression")] * 3
        )
    ],
}
service._evaluation_input_identities = lambda skill_path, digests: (
    (verified["suite_id"], verified["policy_id"])
    if json.loads((skill_path / ".skill-evaluation-cases.json").read_text()) == {}
    and json.loads((skill_path / ".skill-evaluation-policy.json").read_text()) == {}
    else None
)
aggregate = {
    "status": "pass",
    "aggregate_id": "sha256:" + "3" * 64,
}
aggregate_sha = hashlib.sha256(dashboard.canonical(aggregate)).hexdigest()
aggregate_path = (
    control / "skill-review/evaluations/v2/receipts" / f"{aggregate_sha}.json"
)
aggregate_path.parent.mkdir(parents=True)
aggregate_path.write_text(json.dumps(aggregate), encoding="utf-8")
receipt, portfolio_sha = evaluation.write_portfolio_receipt(
    skill, verified, suite, aggregate, aggregate_sha
)
skill_key = service._skill_key(skill)
authority = {
    "schema_version": 3,
    "kind": "cross_cli_authority",
    "skill_path": str(skill.resolve()),
    "candidate_id": evaluation_candidate,
    "suite_id": verified["suite_id"],
    "policy_id": verified["policy_id"],
    "aggregate_receipt_sha256": aggregate_sha,
}
authority_sha = hashlib.sha256(dashboard.canonical(authority)).hexdigest()
authority_path = (
    control
    / "skill-review/evaluations/v2/authority"
    / skill_key
    / f"{evaluation_candidate}.json"
)
authority_path.parent.mkdir(parents=True)
authority_path.write_text(json.dumps(authority), encoding="utf-8")
latest_path = (
    control / "skill-review/evaluations/v2/latest" / f"{skill_key}.json"
)
latest_path.parent.mkdir(parents=True)
latest_path.write_text(json.dumps({
    "schema_version": 2,
    "skill_path": str(skill.resolve()),
    "candidate_id": evaluation_candidate,
    "authority_path": str(authority_path.resolve()),
    "authority_sha256": authority_sha,
}), encoding="utf-8")
current_envelope = json.loads((skill / ".agent-created.json").read_text())
current_envelope["evaluation_v3_sha256"] = authority_sha
(skill / ".agent-created.json").write_text(json.dumps(current_envelope), encoding="utf-8")
legacy_transition = {
    "schema_version": 1,
    "kind": "dashboard_authority_transition",
    "effective_at": dashboard.now_iso(),
    "skill_key": skill_key,
    "candidate_id": evaluation_candidate,
    "status": "pass",
    "authority_sha256": authority_sha,
    "aggregate_receipt_sha256": aggregate_sha,
    "portfolio_receipt_sha256": portfolio_sha,
    "transition_id": "sha256:" + "7" * 64,
}
legacy_transition_path = (
    control
    / "skill-review/evaluations/v2/dashboard-v1/authority-transitions"
    / skill_key
    / "legacy-pass.json"
)
legacy_transition_path.parent.mkdir(parents=True)
legacy_transition_path.write_text(json.dumps(legacy_transition), encoding="utf-8")
metrics = service._evaluation_portfolio()
check(
    metrics["candidate_percent"] == 100.0
    and metrics["control_percent"] == 0.0
    and metrics["comparable_skills"] == 1,
    "matching pass authority contributes equal-skill capability metrics",
)
for input_name in (
    ".skill-evaluation-cases.json",
    ".skill-evaluation-policy.json",
):
    input_path = skill / input_name
    saved_input = input_path.read_bytes()
    saved_times = input_path.stat()
    input_path.unlink()
    metrics = service._evaluation_portfolio()
    check(
        metrics["candidate_percent"] is None,
        f"missing {input_name} invalidates prior dashboard authority",
    )
    input_path.write_bytes(saved_input)
    os.utime(
        input_path,
        ns=(saved_times.st_atime_ns, saved_times.st_mtime_ns),
    )
    input_path.chmod(0)
    metrics = service._evaluation_portfolio()
    check(
        metrics["candidate_percent"] is None,
        f"unreadable {input_name} invalidates prior dashboard authority",
    )
    input_path.chmod(0o644)
    metrics = service._evaluation_portfolio()
    check(
        metrics["candidate_percent"] == 100.0,
        f"restored {input_name} restores current dashboard authority",
    )
saved_latest = latest_path.read_text()
latest_path.write_text("{}")
metrics = service._evaluation_portfolio()
check(
    metrics["candidate_percent"] is None,
    "stale latest-authority state invalidates dashboard capability metrics",
)
latest_path.write_text(saved_latest)
metrics = service._evaluation_portfolio()
check(
    metrics["candidate_percent"] == 100.0,
    "restored latest-authority state restores capability metrics",
)
subject = {
    "schema_version": 1,
    "kind": "legacy_local_evaluation_subject_binding",
    "subject_key": f"sha256:{skill_key}",
    "content_path": str(skill.resolve()),
}
subject_authority = {
    "schema_version": 4,
    "kind": "cross_cli_authority",
    "skill_path": str(skill.resolve()),
    "subject": subject,
    "candidate_id": evaluation_candidate,
    "input_manifest_sha256": verified["input_manifest_sha256"],
    "suite_id": verified["suite_id"],
    "policy_id": verified["policy_id"],
    "observation_plan_id": "sha256:" + "6" * 64,
    "required_certificate_set_id": "sha256:" + "7" * 64,
    "required_executors": ["copilot"],
    "advisory_executors": [],
    "aggregate_receipt_sha256": aggregate_sha,
    "aggregate_id": aggregate["aggregate_id"],
}
subject_authority["authority_id"] = evaluation.identity_with(
    "authority_id", subject_authority
)
subject_authority_sha = hashlib.sha256(
    dashboard.canonical(subject_authority)
).hexdigest()
saved_authority = authority_path.read_bytes()
authority_path.write_text(json.dumps(subject_authority), encoding="utf-8")
latest_path.write_text(json.dumps({
    "schema_version": 3,
    "skill_path": str(skill.resolve()),
    "subject": subject,
    "candidate_id": evaluation_candidate,
    "input_manifest_sha256": verified["input_manifest_sha256"],
    "authority_path": str(authority_path.resolve()),
    "authority_sha256": subject_authority_sha,
}), encoding="utf-8")
subject_transition = {
    "schema_version": 2,
    "kind": "dashboard_authority_transition",
    "effective_at": dashboard.now_iso(),
    "skill_key": skill_key,
    "subject": subject,
    "candidate_id": evaluation_candidate,
    "input_manifest_sha256": verified["input_manifest_sha256"],
    "status": "pass",
    "authority_sha256": subject_authority_sha,
    "aggregate_receipt_sha256": aggregate_sha,
    "portfolio_receipt_sha256": portfolio_sha,
}
subject_transition["transition_id"] = evaluation.identity_with(
    "transition_id", subject_transition
)
current_input = {
    "subject": subject,
    "candidate_id": evaluation_candidate,
    "input_manifest_sha256": verified["input_manifest_sha256"],
    "suite_id": verified["suite_id"],
    "policy_id": verified["policy_id"],
}
service._current_evaluation_input = lambda skill_path: current_input
check(
    service._subject_transition_matches_current(skill, subject_transition),
    "subject-bound dashboard authority validates against the active input",
)
swapped_transition = {
    **subject_transition,
    "subject": {**subject, "subject_key": "sha256:" + "9" * 64},
}
swapped_transition["transition_id"] = evaluation.identity_with(
    "transition_id", swapped_transition
)
check(
    not service._subject_transition_matches_current(skill, swapped_transition),
    "subject-bound dashboard authority rejects a swapped subject",
)
stale_input = {
    **current_input,
    "input_manifest_sha256": "sha256:" + "8" * 64,
}
check(
    not service._subject_transition_matches_current(
        skill, subject_transition, stale_input
    ),
    "subject-bound dashboard authority rejects a stale input manifest",
)
legacy_transition_path.unlink()
subject_transition_path = legacy_transition_path.parent / (
    subject_transition["transition_id"].removeprefix("sha256:") + ".json"
)
subject_transition_path.write_text(
    json.dumps(subject_transition), encoding="utf-8"
)
check(
    service._current_transition(skill, evaluation_candidate)
    == subject_transition,
    "subject-bound transition loads through the dashboard current-state path",
)
subject_transition_path.unlink()
legacy_transition_path.write_text(json.dumps(legacy_transition), encoding="utf-8")
authority_path.write_bytes(saved_authority)
latest_path.write_text(saved_latest)
cases_path = skill / ".skill-evaluation-cases.json"
cases_times = cases_path.stat()
cases_path.write_text('{"changed":true}')
os.utime(
    cases_path,
    ns=(cases_times.st_atime_ns, cases_times.st_mtime_ns),
)
metrics = service._evaluation_portfolio()
check(
    metrics["candidate_percent"] is None,
    "content-changed evaluation input with preserved mtime invalidates authority",
)
(skill / "SKILL.md").write_text(
    "---\nname: fixture-skill\ndescription: Changed fixture.\n---\n",
    encoding="utf-8",
)
subprocess.run(["git", "-C", str(skills), "add", "."], check=True)
subprocess.run(
    ["git", "-C", str(skills), "commit", "-qm", "change fixture candidate"],
    check=True,
)
changed_candidate = service._skill_candidate(skill)
service._current_evaluation_input = lambda skill_path: {
    **current_input,
    "candidate_id": changed_candidate,
    "input_manifest_sha256": "sha256:" + "6" * 64,
}
other_key = "f" * 64
other_transition = {
    "schema_version": 1,
    "kind": "dashboard_authority_transition",
    "effective_at": dashboard.now_iso(),
    "skill_key": other_key,
    "candidate_id": "sha256:" + "e" * 64,
    "status": "inconclusive",
    "authority_sha256": None,
    "aggregate_receipt_sha256": "d" * 64,
    "portfolio_receipt_sha256": "c" * 64,
    "transition_id": "sha256:" + "b" * 64,
}
other_path = (
    control
    / "skill-review/evaluations/v2/dashboard-v1/authority-transitions"
    / other_key
    / "other.json"
)
other_path.parent.mkdir(parents=True)
other_path.write_text(json.dumps(other_transition), encoding="utf-8")
metrics = service._evaluation_portfolio()
check(
    metrics["history"][-1]["candidate_percent"] is None,
    "historical charts do not carry an obsolete candidate across later transitions",
)
evaluation.write_authority_transition(
    skill,
    changed_candidate,
    "sha256:" + "6" * 64,
    "regression",
    None,
    aggregate_sha,
    portfolio_sha,
)
metrics = service._evaluation_portfolio()
check(
    metrics["candidate_percent"] is None
    and metrics["preference"]["regression"] == 1,
    "newer regression transition removes stale pass capability authority",
)
rows, _ = service.skill_rows()
check(rows[0]["evaluation_status"] != "pass", "candidate changes invalidate prior evaluation authority")
check(len(metrics["history"]) == 3, "evaluation transitions produce retained historical chart points")

unborn_skills = root / "unborn-skills"
unborn_skills.mkdir()
subprocess.run(["git", "-C", str(unborn_skills), "init", "-q"], check=True)
unborn_paths = dashboard.DashboardPaths(
    state,
    control,
    review,
    orchestrator,
    data,
    unborn_skills,
    repo,
    assets,
    token,
)
unborn_service = dashboard.DashboardData(unborn_paths)
unborn_service._refresh_candidate_history_cache()
check(
    unborn_service._candidate_history_head is None
    and unborn_service._candidate_history_retry_at > 0,
    "unborn learned-skills history is temporarily unavailable",
)
unborn_skill = unborn_skills / "later-skill"
unborn_skill.mkdir()
(unborn_skill / ".agent-created").write_text("")
(unborn_skill / "SKILL.md").write_text(
    "---\nname: later-skill\ndescription: Later fixture.\n---\n"
)
subprocess.run(
    ["git", "-C", str(unborn_skills), "config", "user.name", "Dashboard Fixture"],
    check=True,
)
subprocess.run(
    ["git", "-C", str(unborn_skills), "config", "user.email", "fixture@example.invalid"],
    check=True,
)
subprocess.run(["git", "-C", str(unborn_skills), "add", "."], check=True)
subprocess.run(
    ["git", "-C", str(unborn_skills), "commit", "-qm", "first learned skill"],
    check=True,
)
unborn_service._candidate_history_retry_at = 0
unborn_service._refresh_candidate_history_cache()
check(
    unborn_service._candidate_history_head is not None
    and len(unborn_service._candidate_history(unborn_skill)) == 1,
    "evaluation history recovers after the first learned-skill commit",
)

candidate_state = root / "candidate-state"
candidate_data = root / "candidate-data"
candidate_records = candidate_state / "skill-review/candidates/v1/records"
candidate_packages = candidate_data / "candidates/v1/packages"
os.environ["DREAMING_STATE_ROOT"] = str(candidate_state)
os.environ["DREAMING_DATA_ROOT"] = str(candidate_data)
now = int(time.time())
os.environ["DREAMING_NOW_EPOCH"] = str(now)
os.environ["SKILLS_NOW_EPOCH"] = str(now)

def stamp(offset_days):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + int(offset_days * 86400)))

candidate_procedure = lifecycle.validate_procedure({
    "schema_version": 1,
    "trigger": "A bounded recurring trigger.",
    "outcome": "A user-observable stopping condition.",
    "actions": ["Inspect the bounded input", "Apply the deterministic procedure"],
    "exclusions": ["Do not cover neighbouring unrelated work."],
    "match_fingerprint": "sha256:" + "a" * 64,
})

def candidate_package(label):
    source = root / f"candidate-package-{label}"
    (source / "references").mkdir(parents=True, exist_ok=True)
    (source / "SKILL.md").write_text(
        f"---\nname: lifecycle-fixture\ndescription: Deterministic candidate package.\n---\n\n# Fixture {label}\n",
        encoding="utf-8",
    )
    (source / "references/proof.txt").write_text(f"revision={label}\n", encoding="utf-8")
    return source

def candidate_observation(task, session, offset_days, independence="verified"):
    return lifecycle.validate_observation(
        {
            "task_key": task,
            "session_id": session,
            "observed_at": stamp(offset_days),
            "independence": independence,
            "summary": "A deterministic observation.",
            "procedure_fingerprint": candidate_procedure["match_fingerprint"],
        },
        candidate_procedure,
    )

def candidate_record(label, observations):
    identity = str(uuid.uuid4())
    staged, files, _ = lifecycle.make_immutable_package(
        identity, candidate_package(label), "lifecycle-fixture"
    )
    record = lifecycle.new_record(
        identity,
        "lifecycle-fixture",
        candidate_procedure,
        observations[0],
        staged,
        files,
        "different",
        "new-procedure-observed",
        None,
        [],
        [],
    )
    for observation in observations[1:]:
        record["evidence"].append(observation)
        lifecycle.append_decision(
            record, "same", "same-procedure-observed", None, [observation["evidence_id"]]
        )
        record["lifecycle"]["last_supported_at"] = observation["observed_at"]
    return identity, record

collecting_id, collecting = candidate_record(
    "collecting", [candidate_observation("task:collect-1", "copilot:candidate-1", -2)]
)
lifecycle.append_evaluation(collecting, lifecycle.recurrence(collecting))
collecting = lifecycle.persist(collecting)

ready_id, ready = candidate_record(
    "ready",
    [
        candidate_observation("task:ready-1", "copilot:candidate-2", -3),
        candidate_observation("task:ready-2", "claude:candidate-3", -1),
    ],
)
ready_decision = lifecycle.recurrence(ready)
lifecycle.append_evaluation(ready, ready_decision)
lifecycle.append_transition(
    ready, "ready_for_draft", "recurrence-qualified", ready_decision["evidence_ids"], []
)
ready = lifecycle.persist(ready)

evaluating_id, evaluating = candidate_record(
    "evaluating",
    [
        candidate_observation("task:evaluating-1", "copilot:candidate-4", -4),
        candidate_observation("task:evaluating-2", "codex:candidate-5", -2),
    ],
)
evaluating_decision = lifecycle.recurrence(evaluating)
lifecycle.append_evaluation(evaluating, evaluating_decision)
lifecycle.append_transition(
    evaluating,
    "ready_for_draft",
    "recurrence-qualified",
    evaluating_decision["evidence_ids"],
    [],
)
lifecycle.append_transition(
    evaluating, "evaluating", "candidate-staged-for-evaluation", [], []
)
evaluating = lifecycle.persist(evaluating)

expired_id, expired = candidate_record(
    "expired", [candidate_observation("task:expired-1", "copilot:candidate-6", -60)]
)
lifecycle.append_evaluation(expired, lifecycle.recurrence(expired))
lifecycle.append_transition(expired, "expired", "recurrence-window-elapsed", [], [])
expired = lifecycle.persist(expired)

rejected_id, rejected = candidate_record(
    "rejected", [candidate_observation("task:rejected-1", "copilot:candidate-7", -5)]
)
lifecycle.append_transition(rejected, "rejected", "policy-rejected-procedure", [], [])
rejected = lifecycle.persist(rejected)

absorbed_id, absorbed = candidate_record(
    "absorbed", [candidate_observation("task:absorbed-1", "copilot:candidate-8", -6)]
)
lifecycle.append_decision(absorbed, "uncertain", "uncertain-procedure-match", None, [])
lifecycle.append_decision(
    absorbed, "duplicate", "duplicate-of-existing-lifecycle", ready_id, []
)
lifecycle.append_decision(
    absorbed, "supersedes", "supersedes-existing-lifecycle", ready_id, []
)
lifecycle.append_transition(
    absorbed,
    "absorbed",
    "absorbed-into-existing-lifecycle",
    [],
    [],
    related_lifecycle_id=ready_id,
)
absorbed = lifecycle.persist(absorbed)

candidate_paths = dashboard.DashboardPaths(
    state,
    control,
    review,
    orchestrator,
    data,
    skills,
    repo,
    assets,
    token,
    candidate_records,
    candidate_packages,
)
candidate_service = dashboard.DashboardData(candidate_paths)
rows, _ = candidate_service.candidate_rows()
by_id = {item["lifecycle_id"]: item for item in rows}
check(
    len(rows) == 6
    and all(item["status"] == "shadow" for item in rows)
    and {item["state"] for item in rows}
    == {"collecting", "ready_for_draft", "evaluating", "expired", "rejected", "absorbed"},
    "every declared shadow candidate state renders as its own truthful state",
)
check(
    all(
        item["shadow_only"] is True
        and item["active"] is False
        and item["published"] is False
        and item["discoverable"] is False
        and item["authority"] == "shadow-only"
        and item["publication_status"] == "shadow_only"
        and "shadow-only, not active" in item["state_label"]
        for item in rows
    ),
    "every candidate row is conspicuously labeled shadow-only, not active, not published",
)
ready_row = by_id[ready_id]
check(
    ready_row["evidence"]
    == {
        "total": 2,
        "verified": 2,
        "unverified": 0,
        "distinct_tasks": 2,
        "distinct_sessions": 2,
    }
    and ready_row["freshness"]["fresh_evidence"] is True
    and ready_row["freshness"]["past_expiry"] is False
    and ready_row["freshness"]["newest_verified_evidence_at"] == stamp(-1)
    and ready_row["state_reason"] == "recurrence-qualified",
    "ready candidates report exact recurrence evidence counts, freshness, and reason",
)
expired_row = by_id[expired_id]
check(
    expired_row["freshness"]["fresh_evidence"] is False
    and expired_row["freshness"]["past_expiry"] is True
    and expired_row["freshness"]["days_until_expiry"] == 0
    and expired_row["state_reason"] == "recurrence-window-elapsed"
    and by_id[rejected_id]["state_reason"] == "policy-rejected-procedure"
    and by_id[evaluating_id]["state_reason"] == "candidate-staged-for-evaluation",
    "expired, rejected, and evaluating candidates report truthful reasons and freshness",
)
collecting_stored = json.loads(lifecycle.record_path(collecting_id).read_text())
collecting_row = by_id[collecting_id]
check(
    collecting_row["recommendation"]["value"] == "collecting"
    and collecting_row["recommendation"]["reasons"]
    == collecting_stored["evaluation"]["history"][-1]["reasons"]
    and "fewer-than-two-verified-evidence" in collecting_row["recommendation"]["reasons"]
    and collecting_row["recommendation"]["authorizes_publication"] is False
    and collecting_row["recommendation"]["authorizes_activation"] is False
    and collecting_row["evaluation"]["status"] == "not_ready",
    "collecting candidates preserve the exact shadow recommendation and its reasons",
)
gates = {item["name"]: item for item in ready_row["evaluation"]["gates"]}
check(
    ready_row["recommendation"]["value"] == "ready_for_draft"
    and ready_row["recommendation"]["reasons"] == []
    and ready_row["evaluation"]["composite_score"] is None
    and gates["recurrence"]["status"] == "shadow_ready"
    and gates["routing"]["status"] == "unavailable"
    and gates["task_value"]["status"] == "unavailable"
    and gates["task_value"]["reasons"] == ["no-task-value-evidence-recorded"],
    "candidate evaluation keeps separate gate results and never reports a composite pass",
)
ready_package = candidate_packages / ready_id / ready["current_candidate_id"]
owner_files = lifecycle.package_inventory(ready_package, "immutable package")
check(
    ready_row["current_candidate_id"] == lifecycle.candidate_identity(owner_files)
    and candidate_service._candidate_package_files(ready_package) == owner_files
    and ready_row["candidate_revision"]["package_path"]
    == f"candidates/v1/packages/{ready_id}/{ready_row['current_candidate_id']}"
    and ready_row["candidate_revision"]["file_count"] == len(owner_files),
    "dashboard candidate identity exactly matches the lifecycle owner package inventory",
)
absorbed_row = by_id[absorbed_id]
check(
    absorbed_row["decisions"]["counts"]
    == {
        "absorbs": 1,
        "different": 1,
        "duplicate": 1,
        "same": 0,
        "supersedes": 1,
        "uncertain": 1,
    }
    and absorbed_row["absorbed_into"] == ready_id
    and absorbed_row["blockers"]["present"] is True
    and absorbed_row["blockers"]["reasons"]
    == ["uncertain-match-blocker", "covering-lifecycle-blocker"]
    and absorbed_row["blockers"]["covering_lifecycle_ids"] == [ready_id],
    "same, duplicate, uncertain, supersession, and absorption decisions stay separate from state",
)
absorbed_detail = candidate_service.candidate_detail(absorbed_id)
check(
    [item["outcome"] for item in absorbed_detail["match_decisions"]]
    == ["different", "uncertain", "duplicate", "supersedes", "absorbs"]
    and [item["reason"] for item in absorbed_detail["match_decisions"]][1]
    == "uncertain-procedure-match"
    and absorbed_detail["transition_history"][-1]["to_state"] == "absorbed"
    and absorbed_detail["transition_history"][-1]["reason"]
    == "absorbed-into-existing-lifecycle"
    and all(item["shadow_only"] is True for item in absorbed_detail["match_decisions"]),
    "candidate detail preserves append-only decision and transition history with reasons",
)
drifted = copy.deepcopy(ready)
staged_two, files_two, _ = lifecycle.make_immutable_package(
    ready_id, candidate_package("ready-revision-two"), "lifecycle-fixture"
)
drifted["candidate_revisions"].append(
    {
        "candidate_id": staged_two,
        "package_path": lifecycle.package_reference(ready_id, staged_two),
        "files": files_two,
        "staged_at": stamp(0),
    }
)
drifted["current_candidate_id"] = staged_two
drifted = lifecycle.persist(drifted)
drift_row = {
    item["lifecycle_id"]: item for item in candidate_service.candidate_rows()[0]
}[ready_id]
check(
    drift_row["current_candidate_id"] == staged_two
    and drift_row["candidate_revision_count"] == 2
    and drift_row["recommendation"]["stale"] is True
    and drift_row["recommendation"]["candidate_id"] == ready["current_candidate_id"],
    "a new exact candidate revision preserves identity and marks the recommendation stale",
)

def tamper(label, mutate):
    identity, record = candidate_record(
        label, [candidate_observation(f"task:{label}", f"copilot:{label}", -1)]
    )
    lifecycle.append_evaluation(record, lifecycle.recurrence(record))
    record = lifecycle.persist(record)
    path = lifecycle.record_path(identity)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if mutate(identity, payload) is not False:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return identity, payload

def set_state(_identity, payload):
    payload["state"] = "invented_state"

def set_admitted(_identity, payload):
    payload["state"] = "admitted"

def set_published(_identity, payload):
    payload["publication"] = {"status": "published"}

def rewrite_evidence(_identity, payload):
    payload["evidence"][0]["summary"] = "A fabricated observation."

def rewrite_recommendation(_identity, payload):
    payload["evaluation"]["history"][-1]["recommendation"] = "ready_for_draft"

def drop_package(identity, payload):
    lifecycle.remove_created_package(identity, payload["current_candidate_id"])
    return False

def extend_package(identity, payload):
    directory = candidate_packages / identity / payload["current_candidate_id"]
    os.chmod(directory, 0o755)
    (directory / "smuggled.txt").write_text("smuggled\n", encoding="utf-8")
    os.chmod(directory, 0o555)
    return False

unknown_state_id, unknown_state_payload = tamper("unknown-state", set_state)
admitted_id, admitted_payload = tamper("admitted-state", set_admitted)
published_id, published_payload = tamper("published-state", set_published)
evidence_id, evidence_payload = tamper("evidence-tamper", rewrite_evidence)
recommendation_id, recommendation_payload = tamper(
    "recommendation-tamper", rewrite_recommendation
)
missing_package_id, _ = tamper("missing-package", drop_package)
extra_package_id, _ = tamper("extra-package", extend_package)
misfiled_id = str(uuid.uuid4())
(candidate_records / f"{misfiled_id}.json").write_text(
    lifecycle.record_path(collecting_id).read_text(encoding="utf-8"), encoding="utf-8"
)
malformed_id = str(uuid.uuid4())
(candidate_records / f"{malformed_id}.json").write_text("{malformed", encoding="utf-8")

owner_rejections = 0
for tampered in (
    unknown_state_payload,
    admitted_payload,
    published_payload,
    evidence_payload,
):
    try:
        lifecycle.validate_record(copy.deepcopy(tampered), verify_packages=False)
    except lifecycle.LifecycleError:
        owner_rejections += 1
check(
    owner_rejections == 4,
    "the candidate lifecycle owner independently rejects every tampered fixture it owns",
)
check(
    lifecycle.validate_record(copy.deepcopy(recommendation_payload), verify_packages=False)
    == recommendation_payload,
    "a forged recommendation survives the owner schema check alone",
)

rows, _ = candidate_service.candidate_rows()
invalid_rows = {
    item.get("source"): item for item in rows if item["status"] == "invalid"
}
expectations = (
    (f"{unknown_state_id}.json", "record_state_not_shadow"),
    (f"{admitted_id}.json", "record_state_not_shadow"),
    (f"{published_id}.json", "record_publication_not_shadow"),
    (f"{evidence_id}.json", "evidence_identity_mismatch"),
    (f"{recommendation_id}.json", "evaluation_identity_mismatch"),
    (f"{missing_package_id}.json", "package_unavailable"),
    (f"{extra_package_id}.json", "package_inventory_mismatch"),
    (f"{misfiled_id}.json", "record_identity_mismatch"),
    (f"{malformed_id}.json", "record_unreadable"),
)
for source, reason in expectations:
    row = invalid_rows.get(source, {})
    check(
        row.get("status") == "invalid"
        and row.get("reasons") == [reason]
        and row.get("state") is None
        and row.get("active") is False
        and row.get("published") is False
        and row.get("current_candidate_id") is None
        and row.get("label") == dashboard.CANDIDATE_INVALID_LABEL,
        f"untrustworthy candidate data is reported invalid, never active: {reason}",
    )
summary = candidate_service.candidate_summary()
check(
    summary["valid"] == 6
    and summary["invalid"] == len(expectations)
    and summary["total"] == 6 + len(expectations)
    and summary["states"]
    == {
        "absorbed": 1,
        "collecting": 1,
        "evaluating": 1,
        "expired": 1,
        "ready_for_draft": 1,
        "rejected": 1,
    }
    and summary["recommendations"] == {"ready_for_draft": 2, "collecting": 2, "none": 2}
    and summary["blocked"] == 1
    and summary["past_expiry"] == 1
    and summary["active"] is False
    and summary["published"] is False,
    "the candidate summary separates valid shadow states from invalid records",
)
try:
    candidate_service.candidate_detail(unknown_state_id)
    raise AssertionError("invalid candidate detail rendered")
except dashboard.DashboardError as error:
    check(
        error.status == 422
        and error.code == "candidate_invalid"
        and error.sources == ["record_state_not_shadow"],
        "candidate detail fails closed on an unknown or production-misrepresented state",
    )
for absent in ("not-a-uuid", "../queue", str(uuid.uuid4()), collecting_id.upper()):
    try:
        candidate_service.candidate_detail(absent)
        raise AssertionError("absent candidate detail rendered")
    except dashboard.DashboardError as error:
        check(
            error.status == 404 and error.code == "candidate_not_found",
            f"unknown candidate reference is not found: {absent[:12]}",
        )

empty_service = dashboard.DashboardData(
    dashboard.DashboardPaths(
        state,
        control,
        review,
        orchestrator,
        data,
        skills,
        repo,
        assets,
        token,
        root / "absent-candidate-records",
        root / "absent-candidate-packages",
    )
)
empty_summary = empty_service.candidate_summary()
check(
    empty_service.candidate_rows()[0] == []
    and empty_service.candidates({})["items"] == []
    and empty_summary["records_root_present"] is False
    and empty_summary["total"] == 0
    and empty_summary["invalid"] == 0
    and empty_summary["shadow_only"] is True,
    "a missing candidate root is a healthy empty state rather than an inferred failure",
)
file_root = root / "candidate-records-file"
file_root.write_text("{}", encoding="utf-8")
file_service = dashboard.DashboardData(
    dashboard.DashboardPaths(
        state,
        control,
        review,
        orchestrator,
        data,
        skills,
        repo,
        assets,
        token,
        file_root,
        candidate_packages,
    )
)
try:
    file_service.candidate_rows()
    raise AssertionError("non-directory candidate root accepted")
except dashboard.DashboardError as error:
    check(
        error.status == 503 and error.code == "candidate_state_invalid",
        "a candidate record root that is not a directory fails closed",
    )

exposed_keys = set()
exposed_labels = []
label_fields = {
    "label",
    "notice",
    "state_label",
    "status_label",
    "recommendation_label",
    "from_state_label",
    "to_state_label",
    "authority",
}

def walk_candidate(value):
    if isinstance(value, dict):
        for key, item in value.items():
            exposed_keys.add(key)
            if key == "state_labels" and isinstance(item, dict):
                exposed_labels.extend(item.values())
            elif key in label_fields and isinstance(item, str):
                exposed_labels.append(item)
            walk_candidate(item)
    elif isinstance(value, list):
        for item in value:
            walk_candidate(item)

walk_candidate(
    {
        "rows": rows,
        "detail": candidate_service.candidate_detail(ready_id),
        "summary": summary,
        "page": candidate_service.candidates({}),
    }
)
activation = re.compile(
    r"\b(activate|activated|activation|active|publish|publishes|published|promote|"
    r"promoted|admit|admitted|install|installed|enable|enabled)\b",
    re.IGNORECASE,
)
check(
    not exposed_keys
    & {
        "activate",
        "approve",
        "buttons",
        "commands",
        "controls",
        "endpoints",
        "form",
        "mutations",
        "promote",
        "publish",
        "transition_to",
        "write",
    },
    "candidate surfaces expose no production mutation control",
)
check(
    len(exposed_labels) >= 20
    and all(
        not activation.search(item)
        or "shadow" in item.casefold()
        or "not " in item.casefold()
        for item in exposed_labels
    ),
    "candidate labels never claim active, published, or admitted status",
)

print(f"== result: {passes} checks passed ==")
PY
