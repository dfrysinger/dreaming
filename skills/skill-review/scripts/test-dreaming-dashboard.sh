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
    "Shadow candidates" in javascript
    and "Not published" in javascript
    and "Not active" in javascript
    and "Not discoverable" in javascript
    and "Evaluation gates" in javascript
    and "Exact candidate identity" in javascript,
    "candidate views conspicuously show shadow authority, identity, recurrence, freshness, and gates",
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
for path in (state, control, orchestrator / "runs", data / "snapshots", skills):
    path.mkdir(parents=True, exist_ok=True)
token_path.parent.mkdir(parents=True, exist_ok=True)
token = "A" * 43
token_path.write_text(token + "\n", encoding="ascii")
token_path.chmod(0o600)

paths = dashboard.DashboardPaths(
    state, control, orchestrator, data, skills, repo, assets, token_path
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
        {"source_event_id": f"event-{index}", "kind": "message", "text": f"Transcript text {index}"}
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
    "DREAMING_ORCHESTRATOR_STATE_DIR": str(orchestrator),
    "DREAMING_DATA_DIR": str(data),
    "DREAMING_SKILLS_ROOT": str(skills),
    "DREAMING_DASHBOARD_TOKEN_FILE": str(token_path),
    "DREAMING_DASHBOARD_ASSETS": str(assets),
}
server = subprocess.Popen(
    [sys.executable, str(script), "--host", "127.0.0.1", "--port", str(port)],
    env=env,
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

try:
    deadline = time.time() + 120
    while True:
        try:
            status, _, _ = request("/", auth=False)
            if status == 200:
                break
        except OSError:
            pass
        if time.time() > deadline:
            raise AssertionError("server did not become ready")
        time.sleep(0.05)

    status, headers, body = request("/", auth=False)
    check(status == 200 and b"Dreaming Dashboard" in body, "static shell loads without private-state authority")
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
    evidence = json.loads(body)["data"]["items"][0]
    check(
        evidence["anchor_status"] == "exact"
        and [item["source_event_id"] for item in evidence["events"] if item["highlighted"]] == ["event-3"],
        "evidence returns exact highlighted retained context",
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
    check(status == 200 and b"Transcript text 3" in body, "valid canonical snapshot digest opens transcript")
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
    oversized_digest = "e" * 64
    (data / "snapshots" / f"{oversized_digest}.json").write_bytes(b"x" * (dashboard.MAX_SNAPSHOT_BYTES + 1))
    check(request(f"/api/v1/transcripts/{oversized_digest}")[0] == 422, "oversized snapshot is rejected")

    for route in ("/api/v1/overview", "/api/v1/activity", "/api/v1/system", "/api/v1/health"):
        check(request(route)[0] == 200, f"{route} returns schema-v1 data")
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
check(manifest(state, control, orchestrator, data, skills) == before, "complete dashboard browsing is read-only")
print(f"== result: {passes} checks passed ==")
PY
