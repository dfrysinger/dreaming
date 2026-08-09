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
import subprocess
import sys
import unicodedata
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
orchestrator = root / "orchestrator"
data = root / "data"
skills = root / "skills"
assets = repo / "skills/skill-review/assets/dashboard"
token = state / "dashboard/access-token"
for path in (state, control, orchestrator, data, skills):
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
paths = dashboard.DashboardPaths(state, control, orchestrator, data, skills, repo, assets, token)
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
aggregate_sha = "4" * 64
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
evaluation.write_authority_transition(
    skill,
    evaluation_candidate,
    "pass",
    authority_sha,
    aggregate_sha,
    portfolio_sha,
)
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

print(f"== result: {passes} checks passed ==")
PY
