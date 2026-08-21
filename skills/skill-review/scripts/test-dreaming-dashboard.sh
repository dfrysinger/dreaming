#!/usr/bin/env bash
# Deterministic security, scale, integrity, and read-only checks for the dashboard.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." && pwd)"
TEST_ROOT="$REPO/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/dreaming-dashboard.XXXXXX")"
trap 'chmod -R u+w "$TMP" 2>/dev/null || true; rm -rf "$TMP"' EXIT

python3 - "$REPO" "$TMP" <<'PY'
import base64
import hashlib
import http.client
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

repo = Path(sys.argv[1])
root = Path(sys.argv[2])
script = repo / "skills/skill-review/scripts/dreaming-dashboard.py"
spec = importlib.util.spec_from_file_location("dreaming_dashboard", script)
dashboard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = dashboard
spec.loader.exec_module(dashboard)
action_spec = importlib.util.spec_from_file_location(
    "estate_action_dashboard_fixture",
    repo / "scripts/test-estate-action.py",
)
estate_action_test = importlib.util.module_from_spec(action_spec)
sys.modules[action_spec.name] = estate_action_test
action_spec.loader.exec_module(estate_action_test)

passes = 0
def check(value, message):
    global passes
    if not value:
        raise AssertionError(message)
    passes += 1
    print(f"PASS  {message}")

state = root / "state"
control = root / "control"
orchestrator = root / "orchestrator"
data = root / "data"
skills = root / "skills"
assets = repo / "skills/skill-review/assets/dashboard"
token_path = control / "dashboard/access-token"
index_text = (assets / "index.html").read_text(encoding="utf-8")
javascript = (assets / "dashboard.js").read_text(encoding="utf-8")
stylesheet = (assets / "dashboard.css").read_text(encoding="utf-8")
check('rel="icon"' in index_text and "data:image/svg+xml" in index_text, "static shell provides a self-contained favicon")
check(
    "location.hash = `transcript/${button.dataset.transcript}`" in javascript
    and 'name === "transcript" && firstPart' in javascript,
    "transcript navigation is represented in browser history",
)
check(
    'href="#candidates"' in index_text
    and 'api(`/api/v1/candidates?${params}`)' in javascript
    and 'api(`/api/v1/candidates/${encodeURIComponent(lifecycleId)}`)' in javascript
    and 'name === "candidate" && firstPart' in javascript,
    "browser routes expose candidate list and detail views",
)
check(
    "Unpublished drafts" in javascript
    and "Not published" in javascript
    and "Not active" in javascript
    and "Not discoverable" in javascript
    and "Evaluation gates" in javascript
    and "Exact candidate identity" in javascript,
    "candidate views conspicuously show unpublished authority, identity, recurrence, freshness, and gates",
)
check(
    'meta[name="dreaming-tailnet-host"]' in javascript
    and 'location.protocol === "https:" && location.host === tailnetHost' in javascript
    and "const headers = state.token ?" in javascript
    and "authNote.hidden = Boolean(state.token) || tailnetMode" in javascript,
    "browser uses tokenless API requests only on the exact injected HTTPS tailnet origin",
)
check(
    "Portfolio decisions" in javascript
    and "Decision queue" in javascript
    and "Who may change it" in javascript
    and "disabled title=" in javascript
    and "Recovery required:" in javascript
    and "receipts are verification-only" in javascript,
    "portfolio view separates decisions, disabled actions, receipts, and recovery state",
)
check(
    "Evaluation-input authoring must not start until the claims are recovered."
    in javascript
    and "Evaluation-input authoring must not start until the state is repaired."
    in javascript
    and "New evaluation work is paused." not in javascript,
    "evaluation recovery notices require action without claiming an unimplemented pause",
)
check(
    'class="portfolio-table"' in javascript
    and 'data-label="Skill"' in javascript
    and 'data-label="Next action"' in javascript
    and "0 · active tails excluded" in javascript
    and "+ · partial" in javascript
    and "Unknown · stable backlog" in javascript
    and "No settled use in 30 days" in javascript
    and "Evaluation queue" in javascript
    and "evaluation_queue_position" in javascript
    and "enablement is not required" in javascript
    and 'filesUsedBy.join(", ")' in javascript
    and ".portfolio-queue .portfolio-table td::before" in stylesheet
    and "@media (max-width: 700px)" in stylesheet,
    "portfolio decisions disclose settled-use exclusions and stack labeled fields on narrow screens",
)
readiness_labels = {
    state: dashboard.DashboardData._portfolio_evaluation(
        {
            "state": state,
            "status": status,
            "current": False,
            "evaluated_at": "2026-08-19T00:00:00+00:00",
            "receipt_sha256": None,
            "transition_id": "sha256:" + "1" * 64,
            "input_manifest_sha256": (
                None
                if state
                in {
                    "input_missing",
                    "drafting",
                    "insufficient_information",
                }
                else "sha256:" + "2" * 64
            ),
            "cases": [],
        },
        True,
    )["label"]
    for state, status in {
        "input_missing": "input_missing",
        "drafting": "authoring_claimed",
        "review_required": "validation_passed",
        "invalid": "independent_review_rejected",
        "insufficient_information": "objective_grader_unavailable",
        "ready": "ready",
        "executing": "executing",
        "stale": "pass",
    }.items()
}
check(
    readiness_labels
    == {
        "input_missing": "Needs test cases",
        "drafting": "Test design in progress",
        "review_required": "Test design in progress",
        "invalid": "Test design rejected",
        "insufficient_information": "Cannot test safely",
        "ready": "Ready to test",
        "executing": "Testing now",
        "stale": "Stale evaluation",
    },
    "portfolio evaluation labels expose every readiness lifecycle state",
)
legacy_dependencies = dashboard.DashboardData._portfolio_dependencies(
    ["/private/dependency/evidence"],
    True,
)
check(
    legacy_dependencies == {
        "state": "protected",
        "label": "Protected",
        "complete": False,
        "required_by": [],
        "files_used_by": [],
    },
    "legacy dependency lists remain protective without disclosing their values",
)
malformed_dependencies = dashboard.DashboardData._portfolio_dependencies(
    {
        "state": "protected",
        "complete": True,
        "blockers": ["/private/dependency/evidence"],
    },
    True,
)
check(
    malformed_dependencies["complete"] is False
    and malformed_dependencies["required_by"] == []
    and malformed_dependencies["files_used_by"] == [],
    "malformed dependency dictionaries cannot claim completeness or disclose values",
)
malformed_evaluation = dashboard.DashboardData._portfolio_evaluation(
    {
        "state": "pass",
        "status": "pass",
        "current": True,
        "evaluated_at": "2026-08-12T00:00:00+00:00",
        "receipt_sha256": "a" * 64,
        "transition_id": "sha256:" + "b" * 64,
        "cases": [{
            "executor": "copilot",
            "case_id": "fixture",
            "evaluation_class": "capability_uplift",
            "candidate_valid_trials": {"private": "do-not-disclose"},
            "candidate_successful_trials": 3,
            "control_valid_trials": 3,
            "control_successful_trials": 1,
            "comparable": True,
            "exclusion_reason": None,
        }],
    },
    True,
)
check(
    malformed_evaluation["state"] == "invalid"
    and malformed_evaluation["current"] is False
    and malformed_evaluation["cases"] == [],
    "malformed evaluation cases fail closed without disclosing their values",
)
missing_kind_dependencies = dashboard.DashboardData._portfolio_dependencies(
    {
        "state": "protected",
        "complete": True,
        "blockers": [{"source_skill": "consumer"}],
    },
    True,
)
check(
    missing_kind_dependencies["complete"] is False
    and missing_kind_dependencies["required_by"] == [],
    "dependency members missing their kind cannot claim completeness or labels",
)
check(
    not any(
        label in javascript
        for label in (
            "Governance decisions and actions",
            "Evidence recommendation",
            "Observed recommendation",
            "Current recommendation",
            "Historical action receipt",
            "Dream review",
            "Shadow candidates",
            "<th>Observed</th>",
            "Observed arrivals",
            "Observed completions",
            "Observed net",
        )
    ),
    "dashboard omits ambiguous governance labels",
)
check(
    all(
        label in javascript
        for label in (
            "Skill lives on",
            "Evaluation runs on",
            "Not copied yet",
            "Skill changed; refresh needed",
            "Snapshot ready",
            "Copy refused",
            "Controls remain report-only",
        )
    ),
    "dashboard explains remote skill location, copy state, and read-only control",
)
check(
    javascript.index('["failed", "error", "regression", "unhealthy", "invalid"]')
    < javascript.index('["healthy", "ok", "pass", "current", "completed", "inspected"]'),
    "negative compound statuses take precedence over positive badge words",
)
check(
    not any(
        token in javascript
        for token in (
            'fetch("/api/v1/candidates", {method:',
            'fetch(`/api/v1/candidates/${encodeURIComponent(lifecycleId)}`, {method:',
            "Activate candidate",
            "Publish candidate",
        )
    ),
    "candidate browser views expose no mutation controls",
)
check(
    not any(
        token in javascript
        for token in (
            'fetch("/api/v1/estate", {method:',
            "Disable plugin",
            "Archive skill",
            "Restore plugin",
            "Restore skill",
        )
    ),
    "estate browser view exposes no mutation controls",
)
for path in (state, control, orchestrator / "runs", data / "snapshots", skills):
    path.mkdir(parents=True, exist_ok=True)
token_path.parent.mkdir(parents=True, exist_ok=True)
token = "A" * 43
token_path.write_text(token + "\n", encoding="ascii")
token_path.chmod(0o600)
private_sentinels = {
    "settings": "CHK10-SETTINGS-PRIVATE-7d125afd",
    "credential": "CHK10-CREDENTIAL-PRIVATE-9367f9cb",
    "transcript": "CHK10-TRANSCRIPT-PRIVATE-451ea287",
    "case": "CHK10-CASE-PRIVATE-cfe25112",
    "authority": "CHK10-AUTHORITY-PRIVATE-0f89a614",
}
(state / "private-boundary-fixtures.json").write_text(
    json.dumps(private_sentinels),
    encoding="utf-8",
)

estate_snapshot = {
    "schema_version": 1,
    "host_id": "macbook-fixture",
    "collected_at": "2026-08-13T12:00:00+00:00",
    "private_case": private_sentinels["case"],
    "private_authority_receipt": private_sentinels["authority"],
    "scope": {
        "label": "Fixture bounded scope",
        "complete": True,
        "registered_context_ids": ["user"],
        "outside_context_ids": ["unregistered"],
    },
    "totals": {
        "physical_instances": 3,
        "effective_instances": 2,
        "canonical_capabilities": 2,
        "physical_only_instances": 1,
        "unresolved_runtime_skills": 0,
        "plugin_packages": 1,
        "enabled_plugin_packages": 1,
    },
    "authority_counts": {
        "cli_builtin": 0,
        "dreaming_managed": 0,
        "legacy_machine": 0,
        "plugin_managed": 1,
        "unknown_provenance": 2,
        "user_protected": 0,
    },
    "root_class_counts": {
        "builtin": 0,
        "custom": 0,
        "dreaming_publisher": 0,
        "personal": 2,
        "plugin": 1,
        "project": 0,
    },
    "contexts": [{
        "id": "user",
        "kind": "user",
        "registered": True,
        "inside_completeness_claim": True,
        "complete": True,
        "runtime_skill_count": 2,
        "mapped_skill_count": 2,
        "unresolved_count": 0,
    }],
    "physical_instances": [{
        "skill_name": "fixture-skill",
        "root_class": "personal",
        "authority": "unknown_provenance",
        "physical_only": False,
        "owner": "/private/settings/path",
        "instance_id": "sha256:" + "1" * 64,
        "canonical_capability_id": "sha256:" + "2" * 64,
        "absolute_path": "/private/skill/path",
        "files": [
            {
                "path": "secret",
                "sha256": private_sentinels["credential"],
            }
        ],
        "provenance": {
            "status": "invalid",
            "basis": "private-provenance-sentinel",
            "private_evidence_path": "/private/provenance/path",
        },
        "evaluation_complete": True,
        "evaluation": {
            "state": "pass",
            "status": "pass",
            "current": True,
            "evaluated_at": "2026-08-12T00:00:00+00:00",
            "receipt_sha256": "a" * 64,
            "transition_id": "sha256:" + "b" * 64,
            "cases": [{
                "executor": "copilot",
                "case_id": "fixture-intended",
                "evaluation_class": "capability_uplift",
                "candidate_valid_trials": 3,
                "candidate_successful_trials": 3,
                "control_valid_trials": 3,
                "control_successful_trials": 1,
                "comparable": True,
                "exclusion_reason": None,
            }],
        },
        "dependencies_complete": True,
        "dependencies": {
            "state": "protected",
            "complete": True,
            "blockers": [{
                "kind": "runtime_capability",
                "source_skill": "plugin-skill",
                "source_capability_id": "sha256:" + "4" * 64,
                "source_file": "SKILL.md",
                "source_line": "12",
            }],
            "installed_content_consumers": [{
                "kind": "installed_content",
                "source_skill": "script-consumer",
                "source_capability_id": "sha256:" + "7" * 64,
                "source_file": "scripts/run.py",
                "source_line": "4",
            }],
        },
    }, {
        "skill_name": "plugin-skill",
        "root_class": "plugin",
        "authority": "plugin_managed",
        "physical_only": False,
        "owner": "fixture@market",
        "package": {
            "plugin_id": "fixture@market",
            "source_identity": "installed:market/fixture",
            "version": "1.0.0",
        },
        "provenance": {
            "status": "verified",
            "basis": "exact_plugin_identity",
        },
        "evaluation_complete": True,
        "evaluation": {
            "state": "regression",
            "status": "regression",
            "current": True,
            "evaluated_at": "2026-08-12T00:00:00+00:00",
            "receipt_sha256": "c" * 64,
            "transition_id": "sha256:" + "d" * 64,
            "cases": [],
        },
        "dependencies_complete": True,
        "dependencies": {
            "complete": True,
        },
        "instance_id": "sha256:" + "3" * 64,
        "canonical_capability_id": "sha256:" + "4" * 64,
    }, {
        "skill_name": "fixture-skill",
        "root_class": "personal",
        "authority": "unknown_provenance",
        "physical_only": True,
        "owner": "/private/stale/path",
        "instance_id": "sha256:" + "5" * 64,
        "canonical_capability_id": "sha256:" + "6" * 64,
        "provenance": {
            "status": "insufficient",
            "basis": "no_evidence",
        },
        "dependencies": ["/private/dependency/evidence"],
    }],
    "enabled_instances": [{
        "context_id": "user",
        "runtime_name": "fixture-skill",
        "runtime_source": "personal",
        "runtime_enabled": True,
        "instance_id": "sha256:" + "1" * 64,
        "canonical_capability_id": "sha256:" + "2" * 64,
        "authority": "unknown_provenance",
    }, {
        "context_id": "user",
        "runtime_name": "plugin-skill",
        "runtime_source": "plugin",
        "runtime_enabled": True,
        "instance_id": "sha256:" + "3" * 64,
        "canonical_capability_id": "sha256:" + "4" * 64,
        "authority": "plugin_managed",
    }],
    "unresolved_mappings": [],
    "plugins": [{
        "plugin_id": "fixture@market",
        "name": "fixture",
        "version": "1.0.0",
        "source_identity": "installed:market/fixture",
        "package_root": "/private/plugin/path",
        "enabled": True,
        "capabilities": {
            "complete": True,
            "skills": ["./skills/fixture"],
            "agents": [],
            "hooks": [],
            "mcp_servers": [],
            "lsp_servers": [],
        },
    }],
    "evidence": {
        "settings_path": "/private/settings.json",
        "settings_sha256": private_sentinels["settings"],
    },
}
for index, item in enumerate(estate_snapshot["physical_instances"], start=1):
    item.update({
        "host_id": estate_snapshot["host_id"],
        "root_id": f"fixture-root-{index}",
        "relative_path": item["skill_name"],
        "absolute_path": item.get(
            "absolute_path", f"/private/skill/{item['skill_name']}"
        ),
        "inventory_sha256": "sha256:" + str(index) * 64,
    })
estate_census = {
    **estate_snapshot,
    "snapshot_sha256": dashboard.sha(estate_snapshot),
}
estate_receipt = {
    "schema_version": 1,
    "snapshot_sha256": estate_census["snapshot_sha256"],
    "receiver": {
        "receiver_id": "macbook-fixture",
        "receiver_sha256": "a" * 64,
        "collector_sha256": "b" * 64,
    },
    "census": estate_census,
}
estate_receipt_sha = dashboard.sha(estate_receipt)
estate_receipts = state / "estate-census-receipts"
estate_receipts.mkdir()
(estate_receipts / f"{estate_receipt_sha.removeprefix('sha256:')}.json").write_text(
    json.dumps(estate_receipt), encoding="utf-8"
)
(state / "estate-census-current.json").write_text(
    json.dumps({
        "schema_version": 1,
        "receipt_sha256": estate_receipt_sha,
        "snapshot_sha256": estate_census["snapshot_sha256"],
        "census": estate_census,
    }),
    encoding="utf-8",
)
usage_snapshot = {
    "schema_version": 1,
    "host_id": estate_snapshot["host_id"],
    "collected_at": estate_snapshot["collected_at"],
    "census_snapshot_sha256": estate_census["snapshot_sha256"],
    "source": "copilot_local_session_state",
    "coverage": {
        "complete": True,
        "corpus_complete": True,
        "attribution_complete": True,
        "earliest_retained_event": "2026-07-01T00:00:00+00:00",
        "discovered_sessions": 12,
        "discovered_bytes": 4096,
        "indexed_sessions": 12,
        "indexed_bytes": 4096,
        "pending_sessions": 0,
        "pending_bytes": 0,
        "sessions_scanned": 12,
        "bytes_scanned": 4096,
        "sessions_parsed_this_run": 12,
        "bytes_parsed_this_run": 4096,
        "max_sessions": 100,
        "max_bytes": 100000,
        "quiet_seconds": 300,
        "collection_watermark": estate_snapshot["collected_at"],
        "bound_reached": None,
        "work_budget_stopped_run": False,
        "index_status": "loaded",
        "pending": [],
        "failures": [],
    },
    "canonical_usage": [{
        "canonical_capability_id": "sha256:" + "2" * 64,
        "uses_7d": 4,
        "uses_30d": 5,
        "uses_90d": 6,
        "uses_total": 7,
        "last_successful_invocation": "2026-08-13T11:00:00+00:00",
    }, {
        "canonical_capability_id": "sha256:" + "4" * 64,
        "uses_7d": 0,
        "uses_30d": 0,
        "uses_90d": 0,
        "uses_total": 0,
        "last_successful_invocation": None,
    }],
    "unattributed": [],
}
estate_usage = {
    **usage_snapshot,
    "snapshot_sha256": dashboard.sha(usage_snapshot),
}
usage_receipt = {
    "schema_version": 1,
    "snapshot_sha256": estate_usage["snapshot_sha256"],
    "census_snapshot_sha256": estate_census["snapshot_sha256"],
    "receiver": estate_receipt["receiver"],
    "usage": estate_usage,
}
usage_receipt_sha = dashboard.sha(usage_receipt)
usage_receipts = state / "estate-usage-receipts"
usage_receipts.mkdir()
(usage_receipts / f"{usage_receipt_sha.removeprefix('sha256:')}.json").write_text(
    json.dumps(usage_receipt), encoding="utf-8"
)
(state / "estate-usage-current.json").write_text(
    json.dumps({
        "schema_version": 1,
        "receipt_sha256": usage_receipt_sha,
        "snapshot_sha256": estate_usage["snapshot_sha256"],
        "census_snapshot_sha256": estate_census["snapshot_sha256"],
        "usage": estate_usage,
    }),
    encoding="utf-8",
)

action_fixture = estate_action_test.Fixture(
    root / "estate-action-fixture", "plugin_disable"
)
action_fixture.dispatch()
review_state = control / "skill-review"
action_config_path = review_state / "estate-action/config.json"
action_config_path.parent.mkdir(parents=True)
action_config_path.write_bytes(action_fixture.config_path.read_bytes())
decision_records = []
for payload in (
    {
        "action_id": "protected-fixture",
        "target": "human-skill",
        "authority": "user_protected",
        "decision": "keep",
        "status": "protected",
        "target_kind": "personal_skill",
        "at": "2026-08-13T12:00:00Z",
    },
    {
        "action_id": "unknown-fixture",
        "target": "mystery-skill",
        "authority": "unknown_provenance",
        "decision": "investigate",
        "status": "unknown",
        "target_kind": "personal_skill",
        "at": "2026-08-13T12:00:00Z",
    },
    {
        "action_id": "same-name-personal-fixture",
        "target": "fixture@market",
        "authority": "user_protected",
        "decision": "keep",
        "status": "protected",
        "target_kind": "personal_skill",
        "at": "2099-08-13T12:00:00Z",
    },
):
    decision_records.append(
        {**payload, "record_sha256": dashboard.sha(payload)}
    )
(state / "estate-action-ledger.json").write_text(
    json.dumps(decision_records),
    encoding="utf-8",
)

paths = dashboard.DashboardPaths(
    state, control, review_state, orchestrator, data, skills, repo, assets, token_path
)

check(dashboard.read_token(token_path) == token, "valid mode-0600 token is accepted")
try:
    dashboard.read_token(root / "missing-token")
    raise AssertionError("missing token accepted")
except dashboard.DashboardError:
    check(True, "missing token is rejected")
token_path.chmod(0o644)
try:
    dashboard.read_token(token_path)
    raise AssertionError("permissive token accepted")
except dashboard.DashboardError:
    check(True, "permissive token is rejected")
token_path.chmod(0o600)
bad_token = root / "bad-token"
bad_token.write_text("short\n", encoding="ascii")
bad_token.chmod(0o600)
try:
    dashboard.read_token(bad_token)
    raise AssertionError("short token accepted")
except dashboard.DashboardError:
    check(True, "malformed token is rejected")
link_token = root / "link-token"
link_token.symlink_to(token_path)
try:
    dashboard.read_token(link_token)
    raise AssertionError("symlink token accepted")
except dashboard.DashboardError:
    check(True, "symlink token is rejected")

result = subprocess.run(
    [sys.executable, str(script), "--host", "0.0.0.0", "--port", "47673"],
    env={
        **os.environ,
        "DREAMING_STATE_DIR": str(state),
        "SKILLS_STATE_DIR": str(control),
        "SKILLS_REVIEW_STATE_DIR": str(review_state),
        "DREAMING_ORCHESTRATOR_STATE_DIR": str(orchestrator),
        "DREAMING_DATA_DIR": str(data),
        "DREAMING_SKILLS_ROOT": str(skills),
        "DREAMING_DASHBOARD_TOKEN_FILE": str(token_path),
    },
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    timeout=30,
)
check(result.returncode == 2 and "bind_denied" in result.stderr, "non-loopback bind fails before listen")
result = subprocess.run(
    [sys.executable, str(script), "--host", "::1", "--port", "47673"],
    env={
        **os.environ,
        "DREAMING_STATE_DIR": str(state),
        "SKILLS_STATE_DIR": str(control),
        "SKILLS_REVIEW_STATE_DIR": str(review_state),
        "DREAMING_ORCHESTRATOR_STATE_DIR": str(orchestrator),
        "DREAMING_DATA_DIR": str(data),
        "DREAMING_SKILLS_ROOT": str(skills),
        "DREAMING_DASHBOARD_TOKEN_FILE": str(token_path),
    },
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    timeout=30,
)
check(
    result.returncode == 2 and "bind_denied" in result.stderr,
    "unsupported IPv6 loopback configuration fails before listen",
)

queue = []
for index in range(1750):
    queue.append({
        "qualified_session_id": f"copilot:session-{index:04d}",
        "source_revision": f"revision-{index}",
        "source": "copilot",
        "status": "queued",
        "display_name": f"Dream {index:04d}",
        "queued_at": "2026-01-01T00:00:00Z",
        "updated_at": f"2026-01-{1 + index % 28:02d}T00:00:00Z",
        "features": {
            "user_turn_count": index % 20,
            "assistant_turn_count": index % 21,
            "tool_call_count": index % 10,
        },
    })
(state / "queue.json").write_text(json.dumps(queue), encoding="utf-8")
(state / "unsettled.json").write_text("{}", encoding="utf-8")
(state / "review-ledger.json").write_text("[]", encoding="utf-8")
queue_bytes = (state / "queue.json").read_bytes()
ledger_bytes = (state / "review-ledger.json").read_bytes()
now = time.time()
stamp = lambda seconds: time.strftime(
    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - seconds)
)
(state / "queue.json").write_text(json.dumps([
    {
        "qualified_session_id": "copilot:queued-oldest",
        "source_revision": "queued-oldest",
        "status": "queued",
        "queued_at": stamp(7200),
    },
    {
        "qualified_session_id": "copilot:queued-newest",
        "source_revision": "queued-newest",
        "status": "queued",
        "queued_at": stamp(1800),
    },
    {
        "qualified_session_id": "copilot:superseded",
        "source_revision": "superseded",
        "status": "superseded",
        "queued_at": stamp(1200),
    },
    {
        "qualified_session_id": "copilot:recovery",
        "source_revision": "recovery",
        "status": "recovery-required",
        "queued_at": stamp(900),
    },
]), encoding="utf-8")
(state / "review-ledger.json").write_text(json.dumps([
    {
        "session_id": f"copilot:completed-{index}",
        "source_revision": f"completed-{index}",
        "reviewed_at": stamp(600 + index),
    }
    for index in range(6)
]), encoding="utf-8")
capacity = dashboard.DashboardData(paths).overview()["dreams"]
check(
    capacity["queued"] == 2
    and capacity["arrivals_24h"] == 4
    and capacity["completed_24h"] == 6
    and capacity["recovery_required"] == 1
    and capacity["observed_net_24h"] == 2
    and capacity["estimated_burn_down_days"] == 1
    and capacity["capacity_status"] == "burning_down"
    and 7100 <= capacity["oldest_queued_age_seconds"] <= 7300,
    "capacity projection counts retained arrivals, completions, current queue, and recovery state",
)
(state / "review-ledger.json").write_text(json.dumps([
    {
        "session_id": "copilot:completed-one",
        "source_revision": "completed-one",
        "reviewed_at": stamp(60),
    }
]), encoding="utf-8")
capacity = dashboard.DashboardData(paths).overview()["dreams"]
check(
    capacity["observed_net_24h"] == -3
    and capacity["estimated_burn_down_days"] is None
    and capacity["capacity_status"] == "not_burning_down",
    "non-positive observed throughput has no burn-down estimate",
)
(state / "review-ledger.json").write_text(json.dumps([
    {
        "session_id": "copilot:malformed",
        "source_revision": "malformed",
        "reviewed_at": "not-a-time",
    }
]), encoding="utf-8")
capacity = dashboard.DashboardData(paths).overview()["dreams"]
check(
    capacity["completed_24h"] is None
    and capacity["observed_net_24h"] is None
    and capacity["estimated_burn_down_days"] is None
    and capacity["capacity_status"] == "unknown",
    "malformed capacity timestamps remain unknown",
)
(state / "queue.json").write_bytes(queue_bytes)
(state / "review-ledger.json").write_bytes(ledger_bytes)
(state / "review-attempts.json").write_text(json.dumps([
    {
        "session_id": "copilot:scheduled-session",
        "source": "copilot",
        "status": "ok",
        "started_at": "2026-01-02T19:30:15Z",
        "parent_run_id": "run-1",
    },
    {
        "session_id": "claude:legacy-session",
        "source": "claude",
        "status": "ok",
        "started_at": "2026-01-01T19:30:15Z",
    },
    {
        "session_id": "codex:in-flight-session",
        "source": "codex",
        "status": "ok",
        "started_at": "2026-01-03T19:30:15Z",
        "parent_run_id": "run-not-yet-recorded",
    },
]), encoding="utf-8")
(control / "dreaming").mkdir(parents=True)
(control / "dreaming/activation-generation").write_text(
    "20260101T000000Z-install-fixture\n",
    encoding="ascii",
)
(state / "publisher-ownership.json").write_text(json.dumps({
    "copilot": {"skills": ["learned-skill-000"]},
    "claude": {"skills": ["learned-skill-000"]},
    "codex": {"skills": []},
}), encoding="utf-8")
(state / "adapters.json").write_text(json.dumps({
    "max_snapshot_bytes": 100_000,
    "publishers": {
        "copilot": {
            "argv": [
                "/fixture/python",
                "/fixture/scripts/ssh-skill-publisher.py",
                "--expected-receiver-id",
                "macbook-fixture",
                "--expected-receiver-sha",
                "a" * 64,
                "--expected-adapter-sha",
                "b" * 64,
            ]
        }
    },
    "evaluation_input_owner": {
        "enabled": False,
    },
    "remote_evaluation_subjects": {
        "enabled": False,
        "protocol_version": 1,
        "origin_host_id": estate_snapshot["host_id"],
        "receiver": {
            **estate_receipt["receiver"],
            "content_policy_sha256": "c" * 64,
        },
    },
}), encoding="utf-8")
transport_receiver = {
    **estate_receipt["receiver"],
    "content_policy_sha256": "c" * 64,
}
overlay_rows = []
enabled_capabilities = {
    item["canonical_capability_id"]: next(
        physical
        for physical in estate_snapshot["physical_instances"]
        if physical["instance_id"] == item["instance_id"]
    )
    for item in estate_snapshot["enabled_instances"]
    if item["runtime_enabled"]
}
for index, (capability_id, physical) in enumerate(
    sorted(enabled_capabilities.items()), start=1
):
    evaluation = {
        **physical["evaluation"],
        "input_manifest_sha256": "sha256:" + str(index + 6) * 64,
    }
    subject_identity = {
        "origin_host_id": physical["host_id"],
        "origin_root_id": physical["root_id"],
        "origin_relative_path": physical["relative_path"],
    }
    overlay_rows.append({
        "capability_id": capability_id,
        "subject_key": dashboard.sha(subject_identity),
        **subject_identity,
        "origin_path": physical["absolute_path"],
        "canonical_capability_id": capability_id,
        "origin_inventory_sha256": physical["inventory_sha256"],
        "candidate_id": "sha256:" + str(index + 3) * 64,
        "superseded_candidate_ids": [],
        "snapshot_state": "remote_candidate_snapshot_ready",
        "content_path": f"/private/snapshots/{index}/candidate",
        "transport_receipt_sha256": "sha256:" + str(index + 5) * 64,
        "snapshot_refusal": None,
        "evaluation": evaluation,
    })
overlay_identity = {
    "schema_version": 1,
    "kind": "remote_evaluation_overlay",
    "census_snapshot_sha256": estate_census["snapshot_sha256"],
    "census_receipt_sha256": estate_receipt_sha,
    "usage_snapshot_sha256": estate_usage["snapshot_sha256"],
    "usage_receipt_sha256": usage_receipt_sha,
    "receiver": estate_receipt["receiver"],
    "transport_receiver": transport_receiver,
    "origin_host_id": estate_snapshot["host_id"],
    "evaluator_sha256": "sha256:" + "d" * 64,
    "registry_identity": dashboard.EVALUATION_OVERLAY_REGISTRY_IDENTITY,
    "rows": overlay_rows,
}
overlay = {
    **overlay_identity,
    "overlay_sha256": dashboard.sha(overlay_identity),
}
overlay_root = state / "evaluation-input-overlays"
overlay_root.mkdir()
(overlay_root / f"{overlay['overlay_sha256'].removeprefix('sha256:')}.json").write_text(
    json.dumps(overlay), encoding="utf-8"
)
pointer_identity = {
    "schema_version": 1,
    "overlay_sha256": overlay["overlay_sha256"],
    "census_snapshot_sha256": estate_census["snapshot_sha256"],
    "census_receipt_sha256": estate_receipt_sha,
    "usage_snapshot_sha256": estate_usage["snapshot_sha256"],
    "usage_receipt_sha256": usage_receipt_sha,
}
(state / "evaluation-input-overlay-current.json").write_text(
    json.dumps({
        **pointer_identity,
        "pointer_sha256": dashboard.sha(pointer_identity),
    }),
    encoding="utf-8",
)
(state / "remote-publication-summary.json").write_text(json.dumps({
    "schema_version": 1,
    "status": "committed",
    "receiver_id": "macbook-fixture",
    "receiver_sha256": "a" * 64,
    "adapter_sha256": "b" * 64,
    "descriptor": {"skills": ["learned-skill-001"]},
}), encoding="utf-8")
(orchestrator / "runs/run-1.json").write_text(json.dumps({
    "run_id": "run-1",
    "started_at": "2026-01-02T19:30:00Z",
    "ended_at": "2026-01-02T19:31:00Z",
    "status": "ok",
    "passes": [
        {"name": "consolidate", "status": "ok"},
        {"name": "roll", "status": "ok"},
        {"name": "prune", "status": "skipped", "reason": "Weekly maintenance not due"},
    ],
}), encoding="utf-8")

snapshot = {
    "schema_version": 1,
    "qualified_session_id": "copilot:session-0000",
    "source_revision": "revision-0",
    "events": [
        {
            "source_event_id": f"event-{index}",
            "kind": "message",
            "text": (
                private_sentinels["transcript"]
                if index == 3
                else f"Transcript text {index}"
            ),
        }
        for index in range(7)
    ],
}
snapshot_bytes = json.dumps(
    snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
).encode()
snapshot_digest = hashlib.sha256(snapshot_bytes).hexdigest()
(data / "snapshots" / f"{snapshot_digest}.json").write_bytes(snapshot_bytes + b"\n")

for index in range(150):
    skill = skills / f"learned-skill-{index:03d}"
    skill.mkdir()
    (skill / ".agent-created").write_text("", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        f"---\nname: learned-skill-{index:03d}\ndescription: Fixture skill {index}.\n---\n\n# Skill\n",
        encoding="utf-8",
    )
    evidence = []
    if index == 0:
        evidence = [{
            "summary": "<script>window.fixturePwned=true</script> Exact observed behavior.",
            "session_id": "copilot:session-0000",
            "source": "copilot",
            "observed_at": "2026-01-02T12:00:00Z",
            "evidence_kind": "positive",
            "independence": "verified",
            "task_key": "task-1",
            "transcript_context": {
                "schema_version": 1,
                "snapshot_sha256": snapshot_digest,
                "source_revision": "revision-0",
                "event_ids": ["event-3"],
            },
        }]
    (skill / ".agent-created.json").write_text(json.dumps({
        "schema_version": 1,
        "created_at": f"2026-01-{1 + index % 28:02d}T00:00:00Z",
        "evidence": evidence,
    }), encoding="utf-8")

candidate_records = state / "skill-review/candidates/v1/records"
candidate_packages = data / "candidates/v1/packages"
os.environ["DREAMING_STATE_DIR"] = str(state)
os.environ["DREAMING_DATA_DIR"] = str(data)
os.environ["DREAMING_NOW_EPOCH"] = str(int(time.time()))
lifecycle_spec = importlib.util.spec_from_file_location(
    "candidate_lifecycle", repo / "skills/skill-review/scripts/candidate-lifecycle.py"
)
lifecycle = importlib.util.module_from_spec(lifecycle_spec)
sys.modules[lifecycle_spec.name] = lifecycle
lifecycle_spec.loader.exec_module(lifecycle)

candidate_procedure = lifecycle.validate_procedure({
    "schema_version": 1,
    "trigger": "A bounded recurring trigger.",
    "outcome": "A user-observable stopping condition.",
    "actions": ["Inspect the bounded input", "Apply the deterministic procedure"],
    "exclusions": ["Do not cover neighbouring unrelated work."],
    "match_fingerprint": "sha256:" + "a" * 64,
})

def candidate_fixture(label, target_state):
    source = root / f"candidate-source-{label}"
    source.mkdir(parents=True, exist_ok=True)
    (source / "SKILL.md").write_text(
        f"---\nname: dashboard-fixture\ndescription: Deterministic shadow candidate.\n---\n\n# {label}\n",
        encoding="utf-8",
    )
    identity = str(uuid.uuid4())
    staged, files, _ = lifecycle.make_immutable_package(identity, source)
    observations = [
        lifecycle.validate_observation(
            {
                "task_key": f"task:{label}-{index}",
                "session_id": f"copilot:{label}-{index}",
                "observed_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - (index + 1) * 86400)
                ),
                "independence": "verified",
                "summary": "A deterministic observation.",
                "procedure_fingerprint": candidate_procedure["match_fingerprint"],
            },
            candidate_procedure,
        )
        for index in range(3 if target_state != "collecting" else 1)
    ]
    record = lifecycle.new_record(
        identity,
        "dashboard-fixture",
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
    lifecycle.append_evaluation(record, lifecycle.recurrence(record))
    if target_state != "collecting":
        lifecycle.append_transition(
            record,
            "ready_for_draft",
            "recurrence-threshold-met",
            [item["evidence_id"] for item in observations],
            [record["evaluation"]["history"][-1]["evaluation_id"]],
        )
    if target_state == "evaluating":
        lifecycle.append_transition(
            record, "evaluating", "shadow-evaluation-started", [], []
        )
    return identity, lifecycle.persist(record)

collecting_candidate, _ = candidate_fixture("collecting", "collecting")
ready_candidate, _ = candidate_fixture("ready", "ready_for_draft")
evaluating_candidate, _ = candidate_fixture("evaluating", "evaluating")

admitted_candidate, admitted_record = candidate_fixture("admitted", "ready_for_draft")
admitted_payload = json.loads(
    lifecycle.record_path(admitted_candidate).read_text(encoding="utf-8")
)
admitted_payload["state"] = "admitted"
admitted_payload["publication"] = {"status": "published", "published_at": "2026-01-01T00:00:00Z"}
lifecycle.record_path(admitted_candidate).write_text(
    json.dumps(admitted_payload), encoding="utf-8"
)
malformed_candidate = str(uuid.uuid4())
(candidate_records / f"{malformed_candidate}.json").write_text("{", encoding="utf-8")

def manifest(*roots):
    rows = []
    for base in roots:
        for path in sorted(base.rglob("*")):
            info = path.lstat()
            relative = f"{base.name}/{path.relative_to(base)}"
            if path.is_symlink():
                rows.append((relative, "link", os.readlink(path), info.st_mode))
            elif path.is_file():
                rows.append((relative, "file", hashlib.sha256(path.read_bytes()).hexdigest(), info.st_mode))
            elif path.is_dir():
                rows.append((relative, "dir", info.st_mode))
    return rows

before = manifest(state, control, orchestrator, data, skills)
probe = socket.socket()
probe.bind(("127.0.0.1", 0))
port = probe.getsockname()[1]
probe.close()
env = {
    **os.environ,
    "DREAMING_REPO_ROOT": str(repo),
    "DREAMING_STATE_DIR": str(state),
    "SKILLS_STATE_DIR": str(control),
    "SKILLS_REVIEW_STATE_DIR": str(review_state),
    "DREAMING_ORCHESTRATOR_STATE_DIR": str(orchestrator),
    "DREAMING_DATA_DIR": str(data),
    "DREAMING_SKILLS_ROOT": str(skills),
    "DREAMING_DASHBOARD_TOKEN_FILE": str(token_path),
    "DREAMING_DASHBOARD_ASSETS": str(assets),
}
tailnet_host = f"mac-mini.example.ts.net:{port}"

def start_server(extra_env=None):
    return subprocess.Popen(
        [sys.executable, str(script), "--host", "127.0.0.1", "--port", str(port)],
        env={**env, **(extra_env or {})},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

def request(path, *, method="GET", host=None, origin=None, auth=True, cookie=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {}
    if auth:
        headers["Authorization"] = f"Bearer {token}"
    if origin is not None:
        headers["Origin"] = origin
    if cookie is not None:
        headers["Cookie"] = cookie
    connection.putrequest(method, path, skip_host=True)
    connection.putheader("Host", host or f"127.0.0.1:{port}")
    for name, value in headers.items():
        connection.putheader(name, value)
    connection.endheaders()
    response = connection.getresponse()
    body = response.read()
    result = response.status, dict(response.getheaders()), body
    connection.close()
    return result

def wait_for_server():
    deadline = time.time() + 120
    while True:
        try:
            status, _, _ = request("/", auth=False)
            if status == 200:
                return
        except OSError:
            pass
        if time.time() > deadline:
            raise AssertionError("server did not become ready")
        time.sleep(0.05)

server = start_server()
try:
    wait_for_server()
    check(
        request(
            "/api/v1/dreams",
            host=tailnet_host,
            origin=f"https://{tailnet_host}",
            auth=False,
        )[0]
        == 403,
        "unset tailnet Host configuration retains localhost-only behavior",
    )
finally:
    server.terminate()
    server.wait(timeout=30)

server = start_server(
    {"DREAMING_DASHBOARD_TAILNET_HOST": f"https://{tailnet_host}"}
)
try:
    wait_for_server()
    check(
        request(
            "/api/v1/dreams",
            host=tailnet_host,
            origin=f"https://{tailnet_host}",
            auth=False,
        )[0]
        == 403,
        "malformed tailnet Host configuration retains localhost-only behavior",
    )
finally:
    server.terminate()
    server.wait(timeout=30)

server = start_server({"DREAMING_DASHBOARD_TAILNET_HOST": tailnet_host})
try:
    wait_for_server()

    status, headers, body = request("/", auth=False)
    check(
        status == 200
        and b"Dreaming Dashboard" in body
        and b'<meta name="dreaming-tailnet-host" content="">' in body,
        "localhost static shell loads without remote-origin configuration",
    )
    check(
        "default-src 'self'" in headers.get("Content-Security-Policy", "")
        and headers.get("Cache-Control") == "no-store"
        and "Access-Control-Allow-Origin" not in headers,
        "responses carry strict browser isolation headers and no CORS",
    )

    malformed = (state / "queue.json").read_bytes()
    (state / "queue.json").write_text("{malformed", encoding="utf-8")
    check(request("/api/v1/dreams", auth=False)[0] == 401, "missing bearer is rejected before state reads")
    check(request("/api/v1/dreams", host="evil.example", auth=False)[0] == 403, "foreign Host is rejected before state reads")
    check(request("/api/v1/dreams", origin="http://evil.example", auth=False)[0] == 403, "foreign Origin is rejected before state reads")
    (state / "queue.json").write_bytes(malformed)
    check(
        request("/api/v1/dreams", host=tailnet_host, auth=False)[0] == 200,
        "exact configured tailnet Host reads without a bearer when Origin is absent",
    )
    check(
        request(
            "/api/v1/dreams",
            host=tailnet_host,
            origin=f"https://{tailnet_host}",
            auth=False,
        )[0]
        == 200,
        "exact configured tailnet HTTPS origin reads without a bearer",
    )
    tailnet_shell = request("/", host=tailnet_host, auth=False)
    check(
        tailnet_shell[0] == 200
        and (
            f'<meta name="dreaming-tailnet-host" content="{tailnet_host}">'
        ).encode("ascii")
        in tailnet_shell[2],
        "tailnet shell receives only its exact tokenless origin configuration",
    )
    (state / "queue.json").write_text("{malformed", encoding="utf-8")
    check(
        request(
            "/api/v1/dreams",
            host=tailnet_host,
            origin=f"https://evil.example",
            auth=False,
        )[0]
        == 403,
        "configured tailnet Host rejects a foreign Origin before state reads",
    )
    check(
        request(
            "/api/v1/dreams",
            host=tailnet_host,
            origin=f"http://{tailnet_host}",
            auth=False,
        )[0]
        == 403,
        "configured tailnet Host rejects a non-HTTPS Origin before state reads",
    )
    check(
        request(
            "/api/v1/dreams",
            host=tailnet_host,
            origin=f"https://{tailnet_host}",
            auth=False,
            cookie="dashboard=secret",
        )[0]
        == 401,
        "configured tailnet path rejects cookie authentication before state reads",
    )
    check(
        request(
            "/api/v1/dreams?access_token=secret",
            host=tailnet_host,
            origin=f"https://{tailnet_host}",
            auth=False,
        )[0]
        == 401,
        "configured tailnet path rejects query-token authentication before state reads",
    )
    check(
        request(
            "/api/v1/dreams",
            method="POST",
            host=tailnet_host,
            origin=f"https://{tailnet_host}",
            auth=False,
        )[0]
        == 405,
        "configured tailnet path rejects write methods",
    )
    (state / "queue.json").write_bytes(malformed)
    check(request("/api/v1/dreams", cookie="dashboard=secret")[0] == 401, "cookie authentication is rejected")
    check(request("/api/v1/dreams?access_token=secret", auth=False)[0] == 401, "query-token authentication is rejected")
    check(request("/api/v1/dreams", method="POST")[0] == 405, "unsupported authenticated method is rejected")

    all_dreams = []
    cursor = ""
    while True:
        suffix = f"&cursor={cursor}" if cursor else ""
        status, _, body = request(f"/api/v1/dreams?limit=100&sort=name{suffix}")
        check(status == 200, "dream page request succeeds")
        payload = json.loads(body)["data"]
        all_dreams.extend(item["id"] for item in payload["items"])
        check("Transcript text" not in body.decode(), "dream catalog page excludes transcript text")
        cursor = payload["next_cursor"]
        if not cursor:
            break
    check(len(all_dreams) == 1750 and len(set(all_dreams)) == 1750, "1,750 dreams paginate exactly once")

    all_skills = []
    cursor = ""
    while True:
        suffix = f"&cursor={cursor}" if cursor else ""
        status, _, body = request(f"/api/v1/skills?limit=100&sort=name{suffix}")
        check(status == 200, "skill page request succeeds")
        payload = json.loads(body)["data"]
        all_skills.extend(item["name"] for item in payload["items"])
        cursor = payload["next_cursor"]
        if not cursor:
            break
    check(len(all_skills) == 150 and len(set(all_skills)) == 150, "150 skills paginate exactly once")
    status, _, body = request("/api/v1/skills/learned-skill-000")
    skill_detail = json.loads(body)["data"]
    check(
        status == 200
        and skill_detail["publication_targets"] == ["claude", "copilot"],
        "skill detail reports publisher ownership targets",
    )
    status, _, body = request("/api/v1/skills/learned-skill-001")
    remote_skill = json.loads(body)["data"]
    check(
        status == 200
        and remote_skill["publication_targets"] == ["copilot@MacBook"],
        "skill detail reports verified remote publication target",
    )
    adapters_path = state / "adapters.json"
    adapters = json.loads(adapters_path.read_text(encoding="utf-8"))
    receiver_index = adapters["publishers"]["copilot"]["argv"].index(
        "--expected-receiver-id"
    )
    adapters["publishers"]["copilot"]["argv"][receiver_index + 1] = "replacement"
    adapters_path.write_text(json.dumps(adapters), encoding="utf-8")
    status, _, body = request("/api/v1/skills/learned-skill-001")
    stale_remote_skill = json.loads(body)["data"]
    check(
        status == 200 and stale_remote_skill["publication_targets"] == [],
        "skill detail rejects a summary bound to stale receiver configuration",
    )
    adapters["publishers"]["copilot"]["argv"][receiver_index + 1] = (
        "macbook-fixture"
    )
    adapters_path.write_text(json.dumps(adapters), encoding="utf-8")
    publication_path = state / "publisher-ownership.json"
    publication_bytes = publication_path.read_bytes()
    publication_path.write_text("{malformed", encoding="utf-8")
    status, _, body = request("/api/v1/skills?limit=10")
    check(
        status == 503 and b"publisher ownership" in body.lower(),
        "malformed publication state produces an explicit scoped error",
    )
    publication_path.write_bytes(publication_bytes)

    status, _, body = request("/api/v1/dreams?limit=10")
    stale_cursor = json.loads(body)["data"]["next_cursor"]
    queue.append({
        "qualified_session_id": "copilot:new-session",
        "source_revision": "new-revision",
        "source": "copilot",
        "status": "queued",
        "updated_at": "2026-02-01T00:00:00Z",
    })
    (state / "queue.json").write_text(json.dumps(queue), encoding="utf-8")
    check(request(f"/api/v1/dreams?limit=10&cursor={stale_cursor}")[0] == 409, "changed dream state invalidates a cursor")

    status, _, body = request("/api/v1/skills?limit=10")
    isolated_cursor = json.loads(body)["data"]["next_cursor"]
    (orchestrator / "runs/unrelated.json").write_text("{}", encoding="utf-8")
    check(request(f"/api/v1/skills?limit=10&cursor={isolated_cursor}")[0] == 200, "unrelated activity does not invalidate a skill cursor")

    status, _, body = request("/api/v1/skills/learned-skill-000/evidence?limit=100")
    evidence_body = body
    evidence = json.loads(body)["data"]["items"][0]
    check(
        evidence["anchor_status"] == "exact"
        and [item["source_event_id"] for item in evidence["events"] if item["highlighted"]] == ["event-3"]
        and private_sentinels["transcript"] not in body.decode()
        and "Transcript text" not in body.decode(),
        "evidence returns exact highlighted metadata without transcript text",
    )
    envelope_path = skills / "learned-skill-000/.agent-created.json"
    original_envelope = envelope_path.read_text(encoding="utf-8")
    invalid_cases = (
        [],
        ["event-3", "event-3"],
        ["event-4", "event-3"],
        ["missing-event"],
    )
    for event_ids in invalid_cases:
        invalid_envelope = json.loads(original_envelope)
        invalid_envelope["evidence"][0]["transcript_context"]["event_ids"] = event_ids
        envelope_path.write_text(json.dumps(invalid_envelope), encoding="utf-8")
        status, _, body = request(
            "/api/v1/skills/learned-skill-000/evidence?limit=100"
        )
        invalid_evidence = json.loads(body)["data"]["items"][0]
        check(
            status == 200 and invalid_evidence["anchor_status"] == "invalid",
            f"invalid evidence event IDs are never labeled exact: {event_ids}",
        )
    invalid_envelope = json.loads(original_envelope)
    invalid_envelope["evidence"][0]["transcript_context"]["source_revision"] = "wrong"
    envelope_path.write_text(json.dumps(invalid_envelope), encoding="utf-8")
    status, _, body = request(
        "/api/v1/skills/learned-skill-000/evidence?limit=100"
    )
    check(
        status == 200
        and json.loads(body)["data"]["items"][0]["anchor_status"] == "invalid",
        "evidence revision mismatch is never labeled exact",
    )
    envelope_path.write_text(original_envelope, encoding="utf-8")
    status, _, body = request(f"/api/v1/transcripts/{snapshot_digest}")
    transcript_body = body
    transcript_payload = json.loads(body)["data"]
    check(
        status == 200
        and transcript_payload["event_count"] == 7
        and transcript_payload["events"][3]["source_event_id"] == "event-3"
        and private_sentinels["transcript"] not in body.decode()
        and "Transcript text" not in body.decode(),
        "valid canonical snapshot exposes transcript metadata without transcript text",
    )
    for invalid in (
        "../queue",
        snapshot_digest.upper(),
        snapshot_digest[:16],
        "f" * 64,
    ):
        check(request(f"/api/v1/transcripts/{invalid}")[0] == 404, f"invalid snapshot reference is rejected: {invalid[:12]}")
    mismatch = "b" * 64
    (data / "snapshots" / f"{mismatch}.json").write_text("{}", encoding="utf-8")
    check(request(f"/api/v1/transcripts/{mismatch}")[0] == 422, "snapshot digest mismatch is rejected")
    symlink_digest = "c" * 64
    (data / "snapshots" / f"{symlink_digest}.json").symlink_to(data / "snapshots" / f"{snapshot_digest}.json")
    check(request(f"/api/v1/transcripts/{symlink_digest}")[0] == 404, "snapshot symlink is rejected")
    malformed_digest = "d" * 64
    (data / "snapshots" / f"{malformed_digest}.json").write_text("{", encoding="utf-8")
    check(request(f"/api/v1/transcripts/{malformed_digest}")[0] == 422, "malformed snapshot JSON is rejected")
    for invalid_events in (
        [None],
        [{"source_event_id": "event-only", "kind": "message", "text": 123}],
        [
            {"source_event_id": "duplicate", "kind": "message", "text": "one"},
            {"source_event_id": "duplicate", "kind": "message", "text": "two"},
        ],
    ):
        invalid_snapshot = {
            **snapshot,
            "events": invalid_events,
        }
        invalid_bytes = json.dumps(
            invalid_snapshot,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        invalid_digest = hashlib.sha256(invalid_bytes).hexdigest()
        invalid_path = data / "snapshots" / f"{invalid_digest}.json"
        invalid_path.write_bytes(invalid_bytes + b"\n")
        check(
            request(f"/api/v1/transcripts/{invalid_digest}")[0] == 422,
            "malformed snapshot event metadata is rejected",
        )
        invalid_path.unlink()
    nullable_snapshot = {
        **snapshot,
        "events": [
            {
                "source_event_id": "nullable-text",
                "kind": "message",
                "text": None,
            }
        ],
    }
    nullable_bytes = json.dumps(
        nullable_snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    nullable_digest = hashlib.sha256(nullable_bytes).hexdigest()
    nullable_path = data / "snapshots" / f"{nullable_digest}.json"
    nullable_path.write_bytes(nullable_bytes + b"\n")
    check(
        request(f"/api/v1/transcripts/{nullable_digest}")[0] == 200,
        "nullable transcript text allowed by the producer remains valid metadata",
    )
    nullable_path.unlink()
    oversized_digest = "e" * 64
    (data / "snapshots" / f"{oversized_digest}.json").write_bytes(b"x" * (dashboard.MAX_SNAPSHOT_BYTES + 1))
    check(request(f"/api/v1/transcripts/{oversized_digest}")[0] == 422, "oversized snapshot is rejected")

    private_boundary_bodies = [evidence_body, transcript_body]
    for route in ("/api/v1/overview", "/api/v1/estate", "/api/v1/activity", "/api/v1/system", "/api/v1/health"):
        route_status, _, route_body = request(route)
        check(route_status == 200, f"{route} returns schema-v1 data")
        private_boundary_bodies.append(route_body)
        if route == "/api/v1/system":
            check(
                json.loads(route_body)["data"]["limits"]["snapshot_bytes"]
                == 100_000,
                "system reports the configured snapshot boundary",
            )
    check(
        all(
            sentinel not in payload.decode()
            for payload in private_boundary_bodies
            for sentinel in private_sentinels.values()
        ),
        "dashboard serializers redact settings, credentials, transcripts, cases, and authority records",
    )
    _, _, estate_body = request("/api/v1/estate")
    estate_view = json.loads(estate_body)["data"]
    check(
        estate_view["totals"]["physical_instances"] == 3
        and estate_view["authority_counts"] == {
            "cli_builtin": 0,
            "dreaming_managed": 0,
            "legacy_machine": 0,
            "plugin_managed": 1,
            "unknown_provenance": 2,
            "user_protected": 0,
        }
        and estate_view["plugins"][0]["capability_counts"]["skills"] == 1
        and estate_view["plugins"][0]["latest_decision"]["status"]
        == "committed"
        and len(estate_view["enabled_skills"]) == 2
        and len(estate_view["other_physical_copies"]) == 1
        and estate_view["enabled_skills"][0]["skill_name"] == "fixture-skill"
        and estate_view["enabled_skills"][0]["uses_7d"] == 4
        and estate_view["enabled_skills"][0]["uses_30d"] == 5
        and estate_view["enabled_skills"][0]["uses_90d"] == 6
        and estate_view["enabled_skills"][0]["usage_state"] == "complete"
        and estate_view["enabled_skills"][1]["source"]
        == "installed:market/fixture"
        and estate_view["enabled_skills"][1]["uses_total"] == 0
        and estate_view["usage"]["status"] == "complete"
        and "uses_total" not in estate_view["other_physical_copies"][0]
        and estate_view["receiver"]["id"] == "macbook-fixture"
        and estate_view["receipt_sha256"] == estate_receipt_sha
        and estate_view["actions"]["status"] == "current"
        and estate_view["actions"]["total"] == 4
        and estate_view["remote_evaluation"]["status"] == "current"
        and estate_view["remote_evaluation"]["origin_host"] == "MacBook"
        and estate_view["remote_evaluation"]["execution_host"] == "Mac mini"
        and estate_view["remote_evaluation"]["report_only"] is True
        and {
            item["status"] for item in estate_view["actions"]["items"]
        } == {"committed", "protected", "unknown"}
        and estate_view["read_only"] is True
        and estate_view["authorizes_actions"] is False,
        "estate API reports bounded totals, authority, and plugin capability inventory",
    )
    portfolio = {
        item["skill_name"]: item
        for item in estate_view["portfolio_decisions"]
    }
    check(
        len(portfolio) == estate_view["totals"]["canonical_capabilities"]
        and portfolio["fixture-skill"]["recommendation"] == "proven_useful"
        and portfolio["plugin-skill"]["recommendation"] == "disable_candidate"
        and portfolio["plugin-skill"]["who_may_change"] == "Plugin package only"
        and portfolio["plugin-skill"]["dependencies"]["state"] == "incomplete"
        and portfolio["plugin-skill"]["dependencies"]["complete"] is False
        and estate_view["evaluation_queue"] == {
            "queued": 1,
            "current": 2,
            "missing": 0,
            "drafting": 0,
            "review_required": 0,
            "insufficient_information": 0,
            "ready": 0,
            "stale": 0,
            "invalid": 0,
        }
        and portfolio["plugin-skill"]["evaluation_queue_position"] == 1
        and portfolio["plugin-skill"]["evaluation_queue_reason"]
        == "No successful use in 30 days"
        and portfolio["fixture-skill"]["evaluation_queue_position"] is None
        and portfolio["fixture-skill"]["evaluation"]["cases"][0]["case_id"]
        == "fixture-intended"
        and portfolio["fixture-skill"]["remote_evaluation"]["snapshot_state"]
        == "remote_candidate_snapshot_ready"
        and portfolio["fixture-skill"]["remote_evaluation"]["origin_host"]
        == "MacBook"
        and portfolio["fixture-skill"]["dependencies"]["required_by"]
        == ["plugin-skill"]
        and portfolio["fixture-skill"]["dependencies"]["files_used_by"]
        == ["script-consumer"]
        and all(item["next_action"]["enabled"] is False for item in portfolio.values()),
        "portfolio decisions cover each enabled capability and separate value from authority",
    )
    check(
        all(
            sentinel not in estate_body.decode()
            for sentinel in (
                private_sentinels["settings"],
                private_sentinels["credential"],
                private_sentinels["case"],
                private_sentinels["authority"],
                "/private/settings",
                "/private/plugin/path",
                "/private/skill/path",
                "/private/provenance/path",
                "/private/dependency/evidence",
                "private-provenance-sentinel",
            )
        ),
        "estate API excludes private settings, file inventories, and local roots",
    )
    original_overlay_pointer = (
        state / "evaluation-input-overlay-current.json"
    ).read_bytes()
    stale_pointer = json.loads(original_overlay_pointer)
    stale_pointer["usage_receipt_sha256"] = "sha256:" + "f" * 64
    stale_identity = {
        key: value
        for key, value in stale_pointer.items()
        if key != "pointer_sha256"
    }
    stale_pointer["pointer_sha256"] = dashboard.sha(stale_identity)
    (state / "evaluation-input-overlay-current.json").write_text(
        json.dumps(stale_pointer), encoding="utf-8"
    )
    _, _, stale_overlay_body = request("/api/v1/estate")
    stale_overlay = json.loads(stale_overlay_body)["data"]
    stale_portfolio = {
        item["skill_name"]: item
        for item in stale_overlay["portfolio_decisions"]
    }
    check(
        stale_overlay["remote_evaluation"]["status"] == "current view invalid"
        and all(
            item["evaluation"]["state"] == "input_missing"
            for item in stale_portfolio.values()
        )
        and all(
            item["remote_evaluation"]["snapshot_state"]
            == "remote_candidate_state_unavailable"
            for item in stale_portfolio.values()
        ),
        "stale remote evaluation pointers fail closed without trusting path-local evaluation",
    )
    (state / "evaluation-input-overlay-current.json").write_bytes(
        original_overlay_pointer
    )
    original_adapters = (state / "adapters.json").read_bytes()
    local_adapters = json.loads(original_adapters)
    mismatched_adapters = json.loads(original_adapters)
    mismatched_adapters["remote_evaluation_subjects"][
        "origin_host_id"
    ] = "different-host"
    (state / "adapters.json").write_text(
        json.dumps(mismatched_adapters), encoding="utf-8"
    )
    _, _, mismatched_remote_body = request("/api/v1/estate")
    mismatched_remote = json.loads(mismatched_remote_body)["data"]
    check(
        mismatched_remote["remote_evaluation"]["status"]
        == "configuration invalid"
        and all(
            item["evaluation"]["state"] == "input_missing"
            for item in mismatched_remote["portfolio_decisions"]
        ),
        "mismatched remote origin suppresses all path-local evaluation authority",
    )
    disabled_owner_adapters = json.loads(original_adapters)
    disabled_owner_adapters["remote_evaluation_subjects"]["enabled"] = True
    (state / "adapters.json").write_text(
        json.dumps(disabled_owner_adapters), encoding="utf-8"
    )
    _, _, disabled_owner_body = request("/api/v1/estate")
    disabled_owner = json.loads(disabled_owner_body)["data"]
    check(
        disabled_owner["remote_evaluation"]["status"]
        == "configuration invalid"
        and all(
            item["evaluation"]["state"] == "input_missing"
            for item in disabled_owner["portfolio_decisions"]
        ),
        "enabled remote transport with a disabled owner cannot restore raw evaluation",
    )
    local_adapters.pop("evaluation_input_owner")
    local_adapters.pop("remote_evaluation_subjects")
    (state / "adapters.json").write_text(
        json.dumps(local_adapters), encoding="utf-8"
    )
    original_usage_current = (state / "estate-usage-current.json").read_bytes()
    (state / "estate-usage-current.json").unlink()
    _, _, missing_usage_body = request("/api/v1/estate")
    missing_usage = json.loads(missing_usage_body)["data"]
    check(
        missing_usage["available"] is True
        and missing_usage["usage"]["status"] == "unavailable"
        and all(
            item["usage_state"] == "unavailable"
            and item["uses_7d"] is None
            for item in missing_usage["enabled_skills"]
        ),
        "missing usage keeps inventory visible without synthesizing zero",
    )
    (state / "estate-usage-current.json").write_bytes(original_usage_current)
    tampered_usage_current = json.loads(original_usage_current)
    tampered_usage_current["usage"]["canonical_usage"][0]["uses_7d"] = 99
    (state / "estate-usage-current.json").write_text(
        json.dumps(tampered_usage_current), encoding="utf-8"
    )
    _, _, tampered_usage_body = request("/api/v1/estate")
    check(
        json.loads(tampered_usage_body)["data"]["usage"]["status"]
        == "unavailable",
        "tampered usage receipt is unavailable rather than trusted",
    )
    (state / "estate-usage-current.json").write_bytes(original_usage_current)

    usage_variant_paths = []

    def record_usage_variant(snapshot):
        variant_usage = {
            **snapshot,
            "snapshot_sha256": dashboard.sha(snapshot),
        }
        variant_receipt = {
            "schema_version": 1,
            "snapshot_sha256": variant_usage["snapshot_sha256"],
            "census_snapshot_sha256": estate_census["snapshot_sha256"],
            "receiver": estate_receipt["receiver"],
            "usage": variant_usage,
        }
        variant_receipt_sha = dashboard.sha(variant_receipt)
        variant_path = (
            usage_receipts
            / f"{variant_receipt_sha.removeprefix('sha256:')}.json"
        )
        variant_path.write_text(json.dumps(variant_receipt), encoding="utf-8")
        usage_variant_paths.append(variant_path)
        (state / "estate-usage-current.json").write_text(
            json.dumps({
                "schema_version": 1,
                "receipt_sha256": variant_receipt_sha,
                "snapshot_sha256": variant_usage["snapshot_sha256"],
                "census_snapshot_sha256": estate_census["snapshot_sha256"],
                "usage": variant_usage,
            }),
            encoding="utf-8",
        )

    migrated_usage_snapshot = json.loads(json.dumps(usage_snapshot))
    migrated_usage_snapshot["coverage"]["index_status"] = "migrated"
    record_usage_variant(migrated_usage_snapshot)
    _, _, migrated_usage_body = request("/api/v1/estate")
    check(
        json.loads(migrated_usage_body)["data"]["usage"]["status"] == "complete",
        "a parser-revision migration remains a valid usage receipt",
    )

    sealed_legacy_usage = json.loads(json.dumps(usage_snapshot))
    sealed_legacy_coverage = sealed_legacy_usage["coverage"]
    sealed_legacy_coverage.pop("quiet_seconds")
    sealed_legacy_coverage.pop("collection_watermark")
    sealed_legacy_coverage["complete"] = False
    sealed_legacy_coverage["corpus_complete"] = False
    sealed_legacy_coverage["attribution_complete"] = False
    sealed_legacy_coverage["discovered_sessions"] += 1
    sealed_legacy_coverage["discovered_bytes"] += 128
    sealed_legacy_coverage["pending_sessions"] = 1
    sealed_legacy_coverage["pending_bytes"] = 128
    sealed_legacy_coverage["pending"] = []
    sealed_legacy_coverage["failures"] = [{
        "session_id": "copilot:legacy-session",
        "reason": "usage_session_invalid_skill_name",
    }]
    sealed_legacy_usage["unattributed"] = [{
        "name": "retired-skill",
        "reason": "unmapped",
        "uses_7d": 0,
        "uses_30d": 0,
        "uses_90d": 1,
        "uses_total": 1,
    }]
    record_usage_variant(sealed_legacy_usage)
    _, _, sealed_legacy_body = request("/api/v1/estate")
    sealed_legacy_view = json.loads(sealed_legacy_body)["data"]
    sealed_legacy_portfolio = {
        item["skill_name"]: item
        for item in sealed_legacy_view["portfolio_decisions"]
    }
    check(
        sealed_legacy_view["usage"]["status"] == "incomplete"
        and sealed_legacy_view["usage"]["pending_sessions"] == 1
        and sealed_legacy_portfolio["plugin-skill"]["usage_state"]
        == "blocked_stable_backlog"
        and sealed_legacy_portfolio["plugin-skill"]["decision_coverage"][
            "relevant_stable_backlog"
        ] == {
            "count": 2,
            "bytes": 128,
            "oldest_modified_at": None,
        },
        "sealed legacy usage without pending detail remains visible and blocks zero-use",
    )

    hybrid_usage_snapshot = json.loads(json.dumps(usage_snapshot))
    hybrid_usage_snapshot["coverage"]["complete"] = False
    hybrid_usage_snapshot["coverage"]["failures"] = [{
        "session_id": "copilot:hybrid-session",
        "reason": "usage_session_invalid_skill_name",
    }]
    record_usage_variant(hybrid_usage_snapshot)
    _, _, hybrid_usage_body = request("/api/v1/estate")
    check(
        json.loads(hybrid_usage_body)["data"]["usage"]["status"]
        == "unavailable",
        "modern coverage rejects legacy two-field failure records",
    )

    impossible_complete_snapshot = json.loads(json.dumps(usage_snapshot))
    impossible_coverage = impossible_complete_snapshot["coverage"]
    impossible_coverage["discovered_sessions"] += 1
    impossible_coverage["discovered_bytes"] += 128
    impossible_coverage["pending_sessions"] = 1
    impossible_coverage["pending_bytes"] = 128
    impossible_coverage["pending"] = [{
        "session_id": "sha256:" + "6" * 64,
        "reason": "events_recently_modified",
        "modified_at": "2026-08-13T11:59:00+00:00",
        "bytes": 128,
        "failure_id": None,
    }]
    record_usage_variant(impossible_complete_snapshot)
    _, _, impossible_complete_body = request("/api/v1/estate")
    check(
        json.loads(impossible_complete_body)["data"]["usage"]["status"]
        == "unavailable",
        "complete corpus claims with pending sessions fail closed",
    )

    mismatched_failure_snapshot = json.loads(json.dumps(usage_snapshot))
    mismatched_coverage = mismatched_failure_snapshot["coverage"]
    mismatched_coverage["complete"] = False
    mismatched_coverage["corpus_complete"] = False
    mismatched_coverage["discovered_sessions"] += 1
    mismatched_coverage["discovered_bytes"] += 128
    mismatched_coverage["pending_sessions"] = 1
    mismatched_coverage["pending_bytes"] = 128
    mismatched_coverage["pending"] = [{
        "session_id": "sha256:" + "6" * 64,
        "reason": "usage_session_malformed_json",
        "modified_at": "2026-08-13T11:59:00+00:00",
        "bytes": 128,
        "failure_id": "sha256:" + "8" * 64,
    }]
    mismatched_coverage["failures"] = [{
        "failure_id": "sha256:" + "8" * 64,
        "session_id": "sha256:" + "7" * 64,
        "reason": "usage_session_malformed_json",
        "modified_at": "2026-08-13T11:59:00+00:00",
        "bytes": 128,
        "candidate_capability_ids": [],
    }]
    record_usage_variant(mismatched_failure_snapshot)
    _, _, mismatched_failure_body = request("/api/v1/estate")
    check(
        json.loads(mismatched_failure_body)["data"]["usage"]["status"]
        == "unavailable",
        "pending failures must match their exact referenced session record",
    )

    incomplete_usage_snapshot = json.loads(json.dumps(usage_snapshot))
    incomplete_usage_snapshot["coverage"]["complete"] = False
    incomplete_usage_snapshot["coverage"]["failures"] = [{
        "failure_id": "sha256:" + "8" * 64,
        "session_id": "sha256:" + "7" * 64,
        "reason": "usage_session_malformed_json",
        "modified_at": "2026-08-13T10:00:00+00:00",
        "bytes": 128,
        "candidate_capability_ids": [],
    }]
    incomplete_usage = {
        **incomplete_usage_snapshot,
        "snapshot_sha256": dashboard.sha(incomplete_usage_snapshot),
    }
    incomplete_usage_receipt = {
        "schema_version": 1,
        "snapshot_sha256": incomplete_usage["snapshot_sha256"],
        "census_snapshot_sha256": estate_census["snapshot_sha256"],
        "receiver": estate_receipt["receiver"],
        "usage": incomplete_usage,
    }
    incomplete_usage_receipt_sha = dashboard.sha(incomplete_usage_receipt)
    incomplete_usage_receipt_path = (
        usage_receipts
        / f"{incomplete_usage_receipt_sha.removeprefix('sha256:')}.json"
    )
    incomplete_usage_receipt_path.write_text(
        json.dumps(incomplete_usage_receipt), encoding="utf-8"
    )
    (state / "estate-usage-current.json").write_text(
        json.dumps({
            "schema_version": 1,
            "receipt_sha256": incomplete_usage_receipt_sha,
            "snapshot_sha256": incomplete_usage["snapshot_sha256"],
            "census_snapshot_sha256": estate_census["snapshot_sha256"],
            "usage": incomplete_usage,
        }),
        encoding="utf-8",
    )
    _, _, incomplete_usage_body = request("/api/v1/estate")
    incomplete_usage_view = json.loads(incomplete_usage_body)["data"]
    check(
        incomplete_usage_view["usage"]["status"] == "incomplete"
        and all(
            item["usage_state"] == "incomplete"
            for item in incomplete_usage_view["enabled_skills"]
        ),
        "incomplete coverage remains distinct from complete zero usage",
    )
    incomplete_portfolio = {
        item["skill_name"]: item
        for item in incomplete_usage_view["portfolio_decisions"]
    }
    check(
        incomplete_portfolio["fixture-skill"]["usage_state"] == "used_30d"
        and incomplete_portfolio["fixture-skill"]["decision_coverage"][
            "is_lower_bound"
        ] is True
        and incomplete_portfolio["fixture-skill"]["uses_30d"] == 5
        and incomplete_portfolio["fixture-skill"]["last_successful_invocation"]
        == "2026-08-13T11:00:00+00:00"
        and incomplete_portfolio["fixture-skill"]["recommendation"]
        == "proven_useful",
        "incomplete coverage preserves verified positive usage as decisive evidence",
    )
    check(
        incomplete_portfolio["plugin-skill"]["usage_state"]
        == "blocked_stable_backlog"
        and incomplete_portfolio["plugin-skill"]["recommendation"]
        == "disable_candidate",
        "current regression remains visible while in-window transcript failure blocks non-use authority",
    )
    legacy_usage_snapshot = json.loads(json.dumps(incomplete_usage_snapshot))
    legacy_usage_snapshot["coverage"]["failures"][0].pop(
        "candidate_capability_ids"
    )
    legacy_usage_snapshot["unattributed"] = [{
        "name": "retired-skill",
        "reason": "unmapped",
        "uses_7d": 0,
        "uses_30d": 0,
        "uses_90d": 1,
        "uses_total": 1,
    }]
    record_usage_variant(legacy_usage_snapshot)
    _, _, legacy_usage_body = request("/api/v1/estate")
    legacy_usage_view = json.loads(legacy_usage_body)["data"]
    check(
        legacy_usage_view["usage"]["status"] == "incomplete"
        and legacy_usage_view["usage"]["failure_count"] == 1
        and legacy_usage_view["usage"]["unattributed_count"] == 1,
        "sealed legacy usage attribution remains visible without inventing candidate identities",
    )
    decision_usage = {
        "available": True,
        "complete": False,
        "collected_at": estate_snapshot["collected_at"],
        "collection_watermark": estate_snapshot["collected_at"],
        "_receipt_sha256": usage_receipt_sha,
        "_failures": [],
        "_unattributed": [],
        "_pending": [{
            "session_id": "sha256:" + "9" * 64,
            "reason": "events_recently_modified",
            "modified_at": "2026-08-13T11:59:00+00:00",
            "bytes": 256,
            "failure_id": None,
        }],
    }
    zero_row = {
        "uses_7d": 0,
        "uses_30d": 0,
        "uses_90d": 0,
        "uses_total": 0,
        "last_successful_invocation": None,
    }
    settled = dashboard.DashboardData._portfolio_usage_coverage(
        "sha256:" + "4" * 64,
        decision_usage,
        zero_row,
    )
    check(
        settled["state"] == "settled_zero_30d"
        and settled["excluded_recent"] == {"count": 1, "bytes": 256}
        and settled["relevant_stable_backlog"]["count"] == 0,
        "recent active tails produce explicit settled 30-day zero coverage",
    )
    stable_usage = json.loads(json.dumps(decision_usage))
    stable_usage["_pending"][0]["reason"] = "stable_budget_deferred"
    blocked = dashboard.DashboardData._portfolio_usage_coverage(
        "sha256:" + "4" * 64,
        stable_usage,
        zero_row,
    )
    check(
        blocked["state"] == "blocked_stable_backlog"
        and blocked["relevant_stable_backlog"]["count"] == 1,
        "stable unread transcripts inside the decision window block zero-use",
    )
    stable_usage["_pending"][0]["modified_at"] = "2026-07-01T00:00:00+00:00"
    aged_out = dashboard.DashboardData._portfolio_usage_coverage(
        "sha256:" + "4" * 64,
        stable_usage,
        zero_row,
    )
    check(
        aged_out["state"] == "settled_zero_30d"
        and aged_out["relevant_stable_backlog"]["count"] == 0,
        "stable unread transcripts older than the decision window do not block it",
    )
    identity_usage = json.loads(json.dumps(decision_usage))
    identity_usage["_pending"] = []
    identity_usage["_unattributed"] = [{
        "name": "ambiguous-name",
        "candidate_capability_ids": ["sha256:" + "4" * 64],
    }]
    identity_blocked = dashboard.DashboardData._portfolio_usage_coverage(
        "sha256:" + "4" * 64,
        identity_usage,
        zero_row,
    )
    unrelated = dashboard.DashboardData._portfolio_usage_coverage(
        "sha256:" + "2" * 64,
        identity_usage,
        zero_row,
    )
    check(
        identity_blocked["state"] == "blocked_identity"
        and unrelated["state"] == "settled_zero_30d",
        "identity ambiguity blocks only candidate capabilities",
    )
    legacy_identity_usage = json.loads(json.dumps(decision_usage))
    legacy_identity_usage["_pending"] = []
    legacy_identity_usage["_unattributed"] = [{
        "name": "ambiguous-legacy-name",
        "reason": "conflicting_mapping",
    }]
    legacy_identity_blocked = dashboard.DashboardData._portfolio_usage_coverage(
        "sha256:" + "4" * 64,
        legacy_identity_usage,
        zero_row,
    )
    legacy_unmapped_usage = json.loads(json.dumps(legacy_identity_usage))
    legacy_unmapped_usage["_unattributed"][0]["reason"] = "unmapped"
    legacy_unmapped = dashboard.DashboardData._portfolio_usage_coverage(
        "sha256:" + "4" * 64,
        legacy_unmapped_usage,
        zero_row,
    )
    check(
        legacy_identity_blocked["state"] == "blocked_identity"
        and legacy_unmapped["state"] == "settled_zero_30d",
        "ambiguous legacy attribution blocks zero-use while candidate-free legacy names do not",
    )
    (state / "estate-usage-current.json").write_bytes(original_usage_current)
    (state / "adapters.json").write_bytes(original_adapters)
    incomplete_usage_receipt_path.unlink()
    for path in usage_variant_paths:
        path.unlink()

    original_estate_current = (
        state / "estate-census-current.json"
    ).read_bytes()
    variant_receipts = []

    def record_estate_variant(snapshot):
        census = {
            **snapshot,
            "snapshot_sha256": dashboard.sha(snapshot),
        }
        receipt = {
            "schema_version": 1,
            "snapshot_sha256": census["snapshot_sha256"],
            "receiver": estate_receipt["receiver"],
            "census": census,
        }
        receipt_sha256 = dashboard.sha(receipt)
        receipt_path = (
            estate_receipts
            / f"{receipt_sha256.removeprefix('sha256:')}.json"
        )
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        variant_receipts.append(receipt_path)
        (state / "estate-census-current.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "receipt_sha256": receipt_sha256,
                    "snapshot_sha256": census["snapshot_sha256"],
                    "census": census,
                }
            ),
            encoding="utf-8",
        )

    stale_snapshot = dict(estate_snapshot)
    stale_snapshot["collected_at"] = "2000-01-01T00:00:00Z"
    record_estate_variant(stale_snapshot)
    _, _, stale_body = request("/api/v1/estate")
    check(
        json.loads(stale_body)["data"]["status"] == "stale",
        "estate API labels a valid old census stale",
    )
    incomplete_snapshot = json.loads(json.dumps(estate_snapshot))
    incomplete_snapshot["scope"]["complete"] = False
    record_estate_variant(incomplete_snapshot)
    _, _, incomplete_body = request("/api/v1/estate")
    check(
        json.loads(incomplete_body)["data"]["status"] == "incomplete",
        "estate API labels an incomplete census without synthesizing totals",
    )
    (state / "estate-census-current.json").write_text(
        "{", encoding="utf-8"
    )
    _, _, invalid_estate_body = request("/api/v1/estate")
    check(
        json.loads(invalid_estate_body)["data"]["status"] == "invalid",
        "estate API labels malformed census state invalid",
    )
    (state / "estate-census-current.json").write_bytes(
        original_estate_current
    )
    estate_recovery = state / "estate-recovery-required.json"
    estate_recovery.write_text("{}", encoding="utf-8")
    _, _, recovery_estate_body = request("/api/v1/estate")
    check(
        json.loads(recovery_estate_body)["data"]["status"]
        == "recovery required",
        "estate API makes recovery-required state conspicuous",
    )
    estate_recovery.unlink()
    _, _, activity_body = request("/api/v1/activity")
    activity = json.loads(activity_body)["data"]["items"]
    scheduled = next(item for item in activity if item["id"] == "run-1")
    check(
        scheduled["reviews"][0]["session_id"] == "copilot:scheduled-session",
        "scheduled reviews are nested by exact parent run ID",
    )
    check(
        any(
            item["kind"] == "dream-review"
            and item["session_id"] == "claude:legacy-session"
            and "parent_run_id" not in item
            for item in activity
        ),
        "unparented historical reviews remain explicit",
    )
    check(
        any(
            item["kind"] == "dream-review"
            and item["session_id"] == "codex:in-flight-session"
            and item["parent_run_id"] == "run-not-yet-recorded"
            for item in activity
        ),
        "scheduled reviews retain unresolved parent run IDs",
    )
    status, _, body = request("/api/v1/candidates?limit=100")
    candidate_page = json.loads(body)["data"]
    candidate_rows = {item.get("lifecycle_id"): item for item in candidate_page["items"]}
    check(
        status == 200
        and candidate_page["shadow_only"] is True
        and candidate_page["active"] is False
        and candidate_page["published"] is False
        and candidate_page["authority"] == "shadow-only"
        and "shadow-only" in candidate_page["notice"].casefold()
        and "not published" in candidate_page["notice"].casefold(),
        "the candidate route is conspicuously labeled shadow-only, not active, not published",
    )
    check(
        all(
            row["shadow_only"] is True
            and row["active"] is False
            and row["published"] is False
            and row["discoverable"] is False
            and "shadow-only" in row["label"].casefold()
            for row in candidate_page["items"]
        ),
        "every served candidate row carries its own shadow-only labeling",
    )
    check(
        candidate_rows[collecting_candidate]["state"] == "collecting"
        and candidate_rows[ready_candidate]["state"] == "ready_for_draft"
        and candidate_rows[evaluating_candidate]["state"] == "evaluating"
        and all(
            candidate_rows[identity]["status"] == "shadow"
            for identity in (collecting_candidate, ready_candidate, evaluating_candidate)
        ),
        "trustworthy shadow candidates render their exact declared state",
    )
    check(
        candidate_rows[admitted_candidate]["status"] == "invalid"
        and candidate_rows[admitted_candidate].get("state") is None
        and candidate_rows[admitted_candidate]["reasons"] == ["record_state_not_shadow"]
        and candidate_rows[malformed_candidate]["status"] == "invalid"
        and candidate_rows[malformed_candidate]["reasons"] == ["record_unreadable"]
        and candidate_rows[admitted_candidate]["active"] is False
        and candidate_rows[malformed_candidate]["active"] is False,
        "production-misrepresented and malformed candidate records serve as invalid, never active",
    )
    check(request("/api/v1/candidates", auth=False)[0] == 401, "candidate reads require a bearer token")
    status, _, body = request(f"/api/v1/candidates/{ready_candidate}")
    detail = json.loads(body)["data"]
    check(
        status == 200
        and detail["shadow_only"] is True
        and detail["active"] is False
        and detail["published"] is False
        and detail["current_candidate_id"].startswith("sha256:")
        and detail["recommendation"]["value"] == "ready_for_draft"
        and detail["evaluation"]["composite_score"] is None
        and {gate["name"] for gate in detail["evaluation"]["gates"]}
        == {"recurrence", "routing", "task_value"},
        "candidate detail preserves exact identity, recommendation, and separate gates",
    )
    check(
        request(f"/api/v1/candidates/{admitted_candidate}")[0] == 422
        and request(f"/api/v1/candidates/{malformed_candidate}")[0] == 422,
        "untrustworthy candidate detail fails closed",
    )
    check(
        request(f"/api/v1/candidates/{uuid.uuid4()}")[0] == 404
        and request("/api/v1/candidates/../queue")[0] == 404,
        "unknown candidate references are not found",
    )
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        check(
            request("/api/v1/candidates", method=method)[0] == 405
            and request(f"/api/v1/candidates/{ready_candidate}", method=method)[0] == 405,
            f"candidate routes expose no {method} transition control",
        )
    _, _, overview_body = request("/api/v1/overview")
    overview_candidates = json.loads(overview_body)["data"]["candidates"]
    check(
        overview_candidates["valid"] == 3
        and overview_candidates["invalid"] == 2
        and overview_candidates["states"]
        == {
            "absorbed": 0,
            "collecting": 1,
            "evaluating": 1,
            "expired": 0,
            "ready_for_draft": 1,
            "rejected": 0,
        }
        and overview_candidates["active"] is False
        and overview_candidates["published"] is False,
        "the overview counts shadow candidates without ever claiming they are active",
    )

    status, _, body = request("/api/v1/health")
    health = json.loads(body)["data"]
    check(
        status == 200
        and health["activation_generation"]
        == "20260101T000000Z-install-fixture"
        and isinstance(health["process_id"], int),
        "authenticated health identifies the active generation and process",
    )
    recovery = state / "publication-recovery-required.json"
    recovery.write_text('{"status":"publication_recovery_required"}', encoding="utf-8")
    status, _, body = request("/api/v1/health")
    recovery_health = json.loads(body)["data"]
    check(
        status == 200
        and recovery_health["status"] == "publication_recovery_required"
        and recovery_health["publication_recovery_required"] is True,
        "authenticated health exposes remote publication recovery",
    )
    recovery.unlink()
    evaluation_recovery = {
        "schema_version": 1,
        "kind": "evaluation_input_recovery_required",
        "claims": [
            {
                "claim_id": "sha256:" + "1" * 64,
                "reason": "prior_owner_live",
            }
        ],
    }
    evaluation_recovery["record_sha256"] = dashboard.sha(
        evaluation_recovery
    )
    evaluation_recovery_path = (
        control / "dreaming/evaluation-input-recovery-required.json"
    )
    evaluation_recovery_path.parent.mkdir(parents=True, exist_ok=True)
    evaluation_recovery_path.write_text(
        json.dumps(evaluation_recovery), encoding="utf-8"
    )
    status, _, body = request("/api/v1/health")
    evaluation_health = json.loads(body)["data"]
    check(
        status == 200
        and evaluation_health["status"] == "Evaluation recovery required"
        and evaluation_health["evaluation_input_recovery_required"] is True
        and evaluation_health["evaluation_input_recovery_claims"] == 1,
        "authenticated health exposes lane-scoped evaluation recovery",
    )
    evaluation_recovery_path.write_text("{}", encoding="utf-8")
    status, _, body = request("/api/v1/health")
    invalid_evaluation_health = json.loads(body)["data"]
    check(
        status == 200
        and invalid_evaluation_health["status"]
        == "Evaluation recovery state invalid"
        and invalid_evaluation_health[
            "evaluation_input_recovery_invalid"
        ]
        is True,
        "authenticated health fails closed on malformed evaluation recovery",
    )
    evaluation_recovery_path.unlink()
finally:
    server.terminate()
    server.wait(timeout=30)

after = manifest(state, control, orchestrator, data, skills)
check(before != after, "test fixtures exercised state-generation changes")
# Remove mutations intentionally introduced by stale, mismatch, symlink, and isolation fixtures,
# then compare against the equivalent expected fixture tree.
queue.pop()
(state / "queue.json").write_text(json.dumps(queue), encoding="utf-8")
(orchestrator / "runs/unrelated.json").unlink()
(data / "snapshots" / f"{mismatch}.json").unlink()
(data / "snapshots" / f"{symlink_digest}.json").unlink()
(data / "snapshots" / f"{malformed_digest}.json").unlink()
(data / "snapshots" / f"{oversized_digest}.json").unlink()
for receipt_path in variant_receipts:
    receipt_path.unlink()
check(manifest(state, control, orchestrator, data, skills) == before, "complete dashboard browsing is read-only")
print(f"== result: {passes} checks passed ==")
PY
