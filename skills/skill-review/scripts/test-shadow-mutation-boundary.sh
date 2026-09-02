#!/usr/bin/env bash
# SH-CHK-08 mutation boundary: snapshot every production control surface, run
# the complete isolated shadow flow (collection, recurrence, exact-revision
# evaluation, an evaluation failure that requests production quarantine
# authority, and a refused quarantine transition), then prove nothing outside
# the isolated shadow roots moved a byte.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
# shellcheck source=lib-test-work.sh
source "$SCRIPT_DIR/lib-test-work.sh"
prune_test_work "$TEST_ROOT" "shadow-mutation-boundary" 2
TMP="$(mktemp -d "$TEST_ROOT/shadow-mutation-boundary.XXXXXX")"
LOCK_TOKEN=""
cleanup() {
  local rc=$?
  trap - EXIT
  if [[ -n "$LOCK_TOKEN" ]]; then
    "$SCRIPT_DIR/daemon-lock.py" release "$LOCK_TOKEN" >/dev/null 2>&1 || true
  fi
  finish_test_work "$rc" "$TMP" "shadow mutation boundary" 1
  exit "$rc"
}
trap cleanup EXIT

LIFECYCLE="$SCRIPT_DIR/candidate-lifecycle.py"
EVALUATION="$SCRIPT_DIR/skill-evaluation.py"
HARNESS="$SCRIPT_DIR/skill-evaluation-harness.py"
ADAPTER="$SCRIPT_DIR/fake-skill-evaluation-adapter.py"
LOCK="$SCRIPT_DIR/daemon-lock.py"

# Production control surfaces.  Nothing under these paths may change.
CONTROL="$TMP/control"
MANAGED="$TMP/managed-skills"
PUBLISHER="$TMP/publisher"
LAUNCH_AGENTS="$TMP/LaunchAgents"
HALT="$CONTROL/skill-review/disable-daemon"
QUARANTINE="$CONTROL/skill-review/quarantine"
RETIRED="$CONTROL/skill-review/retired"
TOMBSTONES="$CONTROL/skill-review/tombstones"
APPROVED_INVENTORY="$PUBLISHER/approved-inventory.json"

# Isolated shadow roots.  Only these may change.
SHADOW_STATE="$TMP/shadow-state"
SHADOW_DATA="$TMP/shadow-data"
SHADOW_RECEIPTS="$CONTROL/skill-review/evaluations/v2/shadow-receipts"
WORK="$TMP/work"
TOOLS="$TMP/tools"

export PYTHONDONTWRITEBYTECODE=1
export TMPDIR="$TMP"
export XDG_STATE_HOME="$TMP/xdg-state"
export XDG_DATA_HOME="$TMP/xdg-data"
export SKILLS_STATE_DIR="$CONTROL"
export SKILLS_LOCK_DIR="$TMP/lease/daemon.lock"
export SKILLS_LAUNCH_AGENTS_DIR="$LAUNCH_AGENTS"
export DREAMING_STATE_ROOT="$SHADOW_STATE"
export DREAMING_DATA_ROOT="$SHADOW_DATA"
export DREAMING_SKILLS_ROOT="$MANAGED"
export DREAMING_NOW_EPOCH=1770249600 # 2026-02-05T00:00:00Z
export SKILLS_NOW_EPOCH="$DREAMING_NOW_EPOCH"
export GIT_AUTHOR_NAME="Shadow Fixture"
export GIT_AUTHOR_EMAIL="shadow@fixture.invalid"
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"
export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
export GIT_AUTHOR_DATE="@$DREAMING_NOW_EPOCH +0000"
export GIT_COMMITTER_DATE="$GIT_AUTHOR_DATE"

mkdir -p "$CONTROL" "$MANAGED" "$PUBLISHER" "$LAUNCH_AGENTS" "$SHADOW_STATE" "$SHADOW_DATA" \
  "$WORK" "$TOOLS" "$TMP/lease" "$TMP/fixtures" "$TMP/snapshots" "$XDG_STATE_HOME" "$XDG_DATA_HOME"

passes=0
pass() { echo "PASS  $*"; passes=$((passes + 1)); }
fail() { echo "FAIL  $*" >&2; exit 1; }
json_get() { python3 -c "import json,sys; print($1)" < "$2"; }
record_path() { printf '%s/skill-review/candidates/v1/records/%s.json' "$SHADOW_STATE" "$1"; }

for isolated in "$CONTROL" "$MANAGED" "$PUBLISHER" "$LAUNCH_AGENTS" "$SHADOW_STATE" "$SHADOW_DATA"; do
  [[ "$isolated" == "$TMP/"* ]] || fail "fixture root $isolated escaped the isolated test work directory"
done

# ---------------------------------------------------------------------------
# Fixture production control surfaces.
# ---------------------------------------------------------------------------
mkdir -p "$MANAGED/approved-fixture-skill/references"
cat > "$MANAGED/approved-fixture-skill/SKILL.md" <<'EOF'
---
name: approved-fixture-skill
description: Use when the approved published fixture target must answer.
---

# approved-fixture-skill

Return the approved deterministic fixture result.
EOF
cat > "$MANAGED/approved-fixture-skill/references/notes.md" <<'EOF'
# Approved reference

Published, approved, and out of shadow reach.
EOF
git -C "$MANAGED" -c init.defaultBranch=main -c init.templateDir= init --quiet
git -C "$MANAGED" add --all
git -C "$MANAGED" -c commit.gpgsign=false commit --quiet -m "seed approved managed skill"
MANAGED_HEAD="$(git -C "$MANAGED" rev-parse HEAD)"

mkdir -p "$(dirname "$HALT")" "$QUARANTINE" "$RETIRED" "$TOMBSTONES" \
  "$CONTROL/dreaming" "$CONTROL/skill-review/evaluations/v2/authority" "$SHADOW_RECEIPTS"
printf 'halted-for-shadow-milestone\n' > "$HALT"
printf '20260205T000000Z-install-shadow-fixture\n' > "$CONTROL/dreaming/activation-generation"
cat > "$QUARANTINE/approved-fixture-skill.json" <<'EOF'
{"schema_version": 1, "skill": "approved-fixture-skill", "quarantined": false, "reason": "baseline-fixture"}
EOF
cat > "$RETIRED/retired-fixture-skill.json" <<'EOF'
{"schema_version": 1, "skill": "retired-fixture-skill", "retired_at": "2026-01-02T00:00:00+00:00", "restore_sha": "0000000000000000000000000000000000000000"}
EOF
cat > "$TOMBSTONES/retired-fixture-skill.json" <<'EOF'
{"schema_version": 1, "skill": "retired-fixture-skill", "tombstoned_by": "skill-curator", "replacement": "approved-fixture-skill"}
EOF
cat > "$CONTROL/skill-review/evaluations/v2/authority/approved-fixture-skill.json" <<'EOF'
{"schema_version": 2, "kind": "evaluation_authority", "skill": "approved-fixture-skill", "decision": "approved"}
EOF

mkdir -p "$PUBLISHER/bundles/bundle-0001"
cp "$MANAGED/approved-fixture-skill/SKILL.md" "$PUBLISHER/bundles/bundle-0001/SKILL.md"
python3 - "$APPROVED_INVENTORY" "$MANAGED_HEAD" "$MANAGED/approved-fixture-skill/SKILL.md" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

target, revision, skill_md = sys.argv[1:]
digest = hashlib.sha256(Path(skill_md).read_bytes()).hexdigest()
Path(target).write_text(
    json.dumps(
        {
            "schema_version": 1,
            "kind": "approved_inventory",
            "skills_revision": revision,
            "bundle_id": "sha256:" + "b" * 64,
            "approved": [{"name": "approved-fixture-skill", "skill_md_sha256": "sha256:" + digest}],
        },
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
printf '{"at": "2026-01-02T00:00:00+00:00", "publisher": "fixture", "action": "install", "bundle_id": "sha256:%s"}\n' \
  "$(printf 'b%.0s' {1..64})" > "$PUBLISHER/ownership-journal.jsonl"
cat > "$LAUNCH_AGENTS/com.fixture.dreaming.daily.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Label</key><string>com.fixture.dreaming.daily</string>
<key>Disabled</key><true/>
</dict></plist>
EOF

# ---------------------------------------------------------------------------
# Production snapshot tooling.
# ---------------------------------------------------------------------------
cat > "$TOOLS/snapshot-production.py" <<'PY'
#!/usr/bin/env python3
"""Snapshot every production control surface the shadow flow may not touch."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

# The only paths the shadow milestone may write inside a control root.
SHADOW_MUTABLE = ("skill-review/evaluations/v2/shadow-receipts",)


def tree(root, skip=()):
    root = Path(root)
    items = []
    if not root.exists():
        return items
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if any(relative == item or relative.startswith(item + "/") for item in skip):
            continue
        if path.is_symlink():
            items.append({"path": relative, "kind": "symlink", "target": os.readlink(path)})
        elif path.is_dir():
            items.append({"path": relative, "kind": "directory", "mode": oct(path.stat().st_mode & 0o777)})
        elif path.is_file():
            content = path.read_bytes()
            items.append(
                {
                    "path": relative,
                    "kind": "file",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                    "mode": oct(path.stat().st_mode & 0o777),
                }
            )
        else:
            items.append({"path": relative, "kind": "irregular"})
    return items


def git(root, *arguments):
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return {"exit": result.returncode, "stdout": result.stdout}


def marker(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        return {"present": False, "sha256": None}
    return {"present": True, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


managed, publisher, launch_agents, control = sys.argv[1:5]
snapshot = {
    "managed_skills_git": {
        "head": git(managed, "rev-parse", "HEAD"),
        "tree": git(managed, "rev-parse", "HEAD^{tree}"),
        "status": git(managed, "status", "--porcelain"),
        "branch": git(managed, "symbolic-ref", "--quiet", "HEAD"),
        "log": git(managed, "log", "--format=%H %T %s"),
        "refs": git(managed, "for-each-ref", "--format=%(refname) %(objectname)"),
        "stash": git(managed, "stash", "list"),
    },
    "managed_skills_bytes": tree(managed, skip=(".git",)),
    "publisher_state": tree(publisher),
    "approved_inventory_pointer": marker(Path(publisher) / "approved-inventory.json"),
    "launch_agent_state": tree(launch_agents),
    "control_state": tree(control, skip=SHADOW_MUTABLE),
    "halt_state": marker(Path(control) / "skill-review" / "disable-daemon"),
    "production_quarantine_state": tree(Path(control) / "skill-review" / "quarantine"),
    "retirement_state": {
        "retired": tree(Path(control) / "skill-review" / "retired"),
        "tombstones": tree(Path(control) / "skill-review" / "tombstones"),
    },
}
print(json.dumps(snapshot, indent=2, sort_keys=True))
PY

cat > "$TOOLS/compare-snapshot.py" <<'PY'
#!/usr/bin/env python3
"""Report every production control surface difference between two snapshots."""

import json
import sys


def walk(prefix, before, after, differences):
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            walk(f"{prefix}.{key}" if prefix else key, before.get(key), after.get(key), differences)
        return
    if isinstance(before, list) and isinstance(after, list):
        keyed = all(isinstance(item, dict) and "path" in item for item in before + after)
        if keyed:
            before_map = {item["path"]: item for item in before}
            after_map = {item["path"]: item for item in after}
            for key in sorted(set(before_map) | set(after_map)):
                walk(f"{prefix}[{key}]", before_map.get(key), after_map.get(key), differences)
            return
        for index in range(max(len(before), len(after))):
            walk(
                f"{prefix}[{index}]",
                before[index] if index < len(before) else None,
                after[index] if index < len(after) else None,
                differences,
            )
        return
    if before != after:
        differences.append((prefix, before, after))


before_path, after_path = sys.argv[1:3]
with open(before_path, encoding="utf-8") as handle:
    before = json.load(handle)
with open(after_path, encoding="utf-8") as handle:
    after = json.load(handle)
differences = []
walk("", before, after, differences)
for path, old, new in differences:
    print(f"CHANGED {path}: {json.dumps(old, sort_keys=True)} -> {json.dumps(new, sort_keys=True)}")
sys.exit(1 if differences else 0)
PY

snapshot_production() {
  python3 "$TOOLS/snapshot-production.py" "$MANAGED" "$PUBLISHER" "$LAUNCH_AGENTS" "$CONTROL" > "$1"
}

assert_unchanged() {
  local label="$1"
  snapshot_production "$TMP/snapshots/current.json"
  if ! python3 "$TOOLS/compare-snapshot.py" "$TMP/snapshots/before.json" "$TMP/snapshots/current.json" \
    > "$TMP/snapshots/diff.txt"; then
    echo "FAIL  $label changed a production control surface:" >&2
    cat "$TMP/snapshots/diff.txt" >&2
    exit 1
  fi
  [[ -f "$HALT" ]] || fail "$label removed the halt marker"
}

assert_detects() {
  local label="$1" expected="$2"
  snapshot_production "$TMP/snapshots/tampered.json"
  if python3 "$TOOLS/compare-snapshot.py" "$TMP/snapshots/before.json" "$TMP/snapshots/tampered.json" \
    > "$TMP/snapshots/tampered-diff.txt"; then
    fail "snapshot comparison did not detect $label"
  fi
  grep -q "$expected" "$TMP/snapshots/tampered-diff.txt" ||
    fail "snapshot comparison detected $label without naming $expected"
}

snapshot_production "$TMP/snapshots/before.json"

# ---------------------------------------------------------------------------
# 1. Prove the boundary guard is armed before the shadow flow runs.
# ---------------------------------------------------------------------------
mv "$HALT" "$TMP/fixtures/halt-marker.saved"
assert_detects "halt marker removal" "halt_state.present"
mv "$TMP/fixtures/halt-marker.saved" "$HALT"
printf '{"quarantined": true}\n' > "$QUARANTINE/forged.json"
assert_detects "a production quarantine write" "production_quarantine_state\[forged.json\]"
rm -f "$QUARANTINE/forged.json"
cp "$APPROVED_INVENTORY" "$TMP/fixtures/approved-inventory.saved"
printf '\n' >> "$APPROVED_INVENTORY"
assert_detects "approved inventory drift" "approved_inventory_pointer.sha256"
cp "$TMP/fixtures/approved-inventory.saved" "$APPROVED_INVENTORY"
printf 'drifted\n' >> "$MANAGED/approved-fixture-skill/SKILL.md"
assert_detects "managed skill byte drift" "managed_skills_git.status.stdout"
git -C "$MANAGED" checkout --quiet -- approved-fixture-skill/SKILL.md
assert_unchanged "the armed-guard self check"
pass "the production boundary guard detects halt removal, quarantine writes, inventory drift, and managed skill drift"

# ---------------------------------------------------------------------------
# 2. Collect two independent verified observations under the real shared lease.
# ---------------------------------------------------------------------------
python3 - "$TMP/fixtures/procedure.json" <<'PY'
import json
import sys

json.dump(
    {
        "schema_version": 1,
        "trigger": "A bounded recurring shadow mutation-boundary trigger.",
        "outcome": "A user-observable deterministic stopping condition.",
        "actions": [
            "Snapshot every production control surface",
            "Run the isolated shadow candidate flow",
            "Prove no production surface moved",
        ],
        "exclusions": ["Do not publish, quarantine, retire, or activate anything."],
        "match_fingerprint": "sha256:" + "c" * 64,
    },
    open(sys.argv[1], "w", encoding="utf-8"),
    sort_keys=True,
)
PY

make_observation() {
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import json
import sys

path, task, session, observed = sys.argv[1:]
json.dump(
    {
        "task_key": task,
        "session_id": session,
        "observed_at": observed,
        "independence": "verified",
        "summary": "An independently verified deterministic shadow observation.",
        "procedure_fingerprint": "sha256:" + "c" * 64,
    },
    open(path, "w", encoding="utf-8"),
    sort_keys=True,
)
PY
}
make_observation "$TMP/fixtures/observation-one.json" task-boundary-one session-boundary-one 2026-02-01T00:00:00Z
make_observation "$TMP/fixtures/observation-two.json" task-boundary-two session-boundary-two 2026-02-04T00:00:00Z

PACKAGE_SOURCE="$TMP/fixtures/candidate-package"
mkdir -p "$PACKAGE_SOURCE/references"
cat > "$PACKAGE_SOURCE/SKILL.md" <<'EOF'
---
name: shadow-boundary-fixture
description: Use when the shadow mutation boundary fixture must answer.
---

# shadow-boundary-fixture

Return the deterministic candidate fixture result.
EOF
cat > "$PACKAGE_SOURCE/references/procedure.md" <<'EOF'
# Candidate reference

Shadow-only. Carries no publication authority.
EOF

LOCK_TOKEN="$("$LOCK" acquire --mode session --owner shadow-mutation-boundary-test)"
export SKILLS_LOCK_TOKEN="$LOCK_TOKEN"
[[ -n "$LOCK_TOKEN" ]] || fail "shared writer lease was not acquired"

"$LIFECYCLE" collect --procedure "$TMP/fixtures/procedure.json" \
  --observation "$TMP/fixtures/observation-one.json" --package "$PACKAGE_SOURCE" \
  --proposed-name shadow-boundary-fixture > "$TMP/fixtures/collect-one.json"
LIFECYCLE_ID="$(json_get 'json.load(open(0))["lifecycle_id"]' "$TMP/fixtures/collect-one.json")"
RECORD="$(record_path "$LIFECYCLE_ID")"
[[ -f "$RECORD" ]] || fail "first observation did not create an isolated lifecycle record"

"$LIFECYCLE" collect --lifecycle-id "$LIFECYCLE_ID" \
  --expected-version "$(json_get 'json.load(open(0))["record_version"]' "$RECORD")" \
  --procedure "$TMP/fixtures/procedure.json" --observation "$TMP/fixtures/observation-two.json" \
  --package "$PACKAGE_SOURCE" --proposed-name shadow-boundary-fixture > "$TMP/fixtures/collect-two.json"
[[ "$(json_get 'json.load(open(0))["lifecycle_id"]' "$TMP/fixtures/collect-two.json")" == "$LIFECYCLE_ID" ]] ||
  fail "the second independent observation did not join the stable lifecycle record"
python3 - "$RECORD" <<'PY'
import json
import sys

record = json.load(open(sys.argv[1], encoding="utf-8"))
assert record["state"] == "collecting", record["state"]
assert len(record["evidence"]) == 2, record["evidence"]
assert {item["task_key"] for item in record["evidence"]} == {"task-boundary-one", "task-boundary-two"}
assert {item["session_id"] for item in record["evidence"]} == {"session-boundary-one", "session-boundary-two"}
assert all(item["independence"] == "verified" for item in record["evidence"])
assert len(record["candidate_revisions"]) == 1, record["candidate_revisions"]
assert record["publication"] == {"status": "shadow_only"}, record["publication"]
PY
assert_unchanged "shadow candidate collection"
pass "the real shared lease collects two independent verified observations into one stable lifecycle record"

# ---------------------------------------------------------------------------
# 3. Recurrence recommends ready_for_draft; the exact revision enters evaluating.
# ---------------------------------------------------------------------------
"$LIFECYCLE" evaluate "$LIFECYCLE_ID" \
  --expected-version "$(json_get 'json.load(open(0))["record_version"]' "$RECORD")" \
  > "$TMP/fixtures/recurrence.json"
[[ "$(json_get 'json.load(open(0))["recommendation"]' "$TMP/fixtures/recurrence.json")" == ready_for_draft ]] ||
  fail "independent fresh recurrence did not recommend ready_for_draft"
[[ "$(json_get 'json.load(open(0))["state"]' "$RECORD")" == ready_for_draft ]] ||
  fail "the ready recommendation did not reach the lifecycle record"
CANDIDATE_ID="$(json_get 'json.load(open(0))["current_candidate_id"]' "$RECORD")"
"$LIFECYCLE" transition "$LIFECYCLE_ID" --to evaluating --reason exact-revision-shadow-evaluation \
  --candidate-id "$CANDIDATE_ID" \
  --expected-version "$(json_get 'json.load(open(0))["record_version"]' "$RECORD")" >/dev/null
[[ "$(json_get 'json.load(open(0))["state"]' "$RECORD")" == evaluating ]] ||
  fail "the exact staged revision did not transition to evaluating"
[[ "$(json_get 'json.load(open(0))["current_candidate_id"]' "$RECORD")" == "$CANDIDATE_ID" ]] ||
  fail "the evaluating transition changed the exact revision identity"
PACKAGE="$SHADOW_DATA/candidates/v1/packages/$LIFECYCLE_ID/$CANDIDATE_ID"
[[ -d "$PACKAGE" ]] || fail "the immutable candidate package for the evaluating revision is missing"
[[ ! -w "$PACKAGE/SKILL.md" ]] || fail "the immutable candidate package is writable"
assert_unchanged "shadow recurrence and the evaluating transition"
pass "recurrence produces shadow ready_for_draft and the exact revision enters evaluating"

# ---------------------------------------------------------------------------
# 4. Certify the exact immutable candidate package against a sealed catalog.
# ---------------------------------------------------------------------------
cat > "$TOOLS/make-shadow-fixture.py" <<'PY'
#!/usr/bin/env python3
"""Generate one sealed catalog-plus-candidate shadow suite, executors, and routing."""

import hashlib
import json
import sys
from pathlib import Path

root, catalog_name, adapter, fixture, target = sys.argv[1:6]
root = Path(root)
root.mkdir(parents=True, exist_ok=True)


def file_digest(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


graders = [
    {
        "id": "answer",
        "type": "regex",
        "safety": True,
        "config": {"required": ["SUCCESS"], "forbidden": []},
    }
]
rows = [
    ("positive", "routing_positive", True, [], True),
    ("close", "routing_close_negative", False, [], True),
    ("unrelated", "routing_unrelated", False, [], False),
    ("conflict", "routing_conflict", False, [catalog_name], True),
    ("value", "task_value", True, [], True),
]
cases = [
    {
        "id": identifier,
        "class": kind,
        "task_id": f"task:boundary-{identifier}-0001",
        "prompt": f"shadow boundary {identifier}",
        "critical": critical,
        "routing": {"candidate_load": candidate, "catalog_loads": catalog},
        "artifacts": ["out.txt"],
        "graders": ["answer"],
        "fixture": fixture if target == identifier else "correct",
    }
    for identifier, kind, candidate, catalog, critical in rows
]
(root / "suite.json").write_text(
    json.dumps(
        {
            "schema_version": 2,
            "kind": "shadow_candidate_evaluation_suite",
            "routing_mode": "catalog_plus_candidate",
            "environment": {"fixture": "shadow-mutation-boundary", "context": "normal"},
            "graders": graders,
            "cases": cases,
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
identity = {
    "name": "fixture",
    "model": "fixture-model",
    "adapter_id": "sha256:" + "1" * 64,
    "adapter_version": 1,
    "adapter_executable_sha256": file_digest(adapter),
    "cli_executable_sha256": "sha256:" + "2" * 64,
    "cli_version": "fixture-cli",
    "tool_policy_id": "sha256:" + "3" * 64,
    "limits": {
        "timeout_seconds": 30,
        "token_budget": 100,
        "turn_budget": 100,
        "tool_budget": 100,
        "output_bytes": 100000,
    },
    "sandbox_id": "sha256:" + "4" * 64,
    "real_backend": True,
    "real_backend_source": "deterministic-attested-fixture",
}
(root / "executors.json").write_text(
    json.dumps(
        {
            "schema_version": 2,
            "kind": "shadow_candidate_evaluation_executors",
            "executors": [identity],
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
(root / "identity.json").write_text(
    json.dumps({key: value for key, value in identity.items() if key != "name"}, sort_keys=True),
    encoding="utf-8",
)
(root / "routing.json").write_text(
    json.dumps(
        {
            "schema_version": 2,
            "kind": "shadow_candidate_evaluation_routing",
            "executors": [
                {
                    "name": "fixture",
                    "adapter_id": identity["adapter_id"],
                    "adapter_executable_sha256": identity["adapter_executable_sha256"],
                    "argv": [
                        str(Path(adapter).resolve()),
                        "--identity",
                        str((root / "identity.json").resolve()),
                    ],
                }
            ],
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
PY

# A sealed read-only snapshot of the approved target catalog, copied out of the
# managed skill root without touching it.
CATALOG="$WORK/sealed-catalog"
mkdir -p "$CATALOG"
cp -R "$MANAGED/approved-fixture-skill" "$CATALOG/approved-fixture-skill"
chmod -R a-w "$CATALOG"

shadow_flow() {
  local root="$1" fixture="$2" target="$3"
  mkdir -p "$root" "$root/run" "$root/result" "$root/execute-scratch" "$root/certify-scratch"
  python3 "$TOOLS/make-shadow-fixture.py" "$root" approved-fixture-skill "$ADAPTER" "$fixture" "$target"
  "$EVALUATION" shadow-compile "$PACKAGE" --suite "$root/suite.json" --catalog-dir "$CATALOG" \
    --executors "$root/executors.json" --routing "$root/routing.json" --run-dir "$root/run" \
    --nonce "shadow-boundary-$fixture-$target" --harness "$HARNESS" >/dev/null
  "$EVALUATION" shadow-execute --run-dir "$root/run" --result-dir "$root/result" \
    --routing "$root/routing.json" --scratch "$root/execute-scratch" --harness "$HARNESS" >/dev/null
  "$EVALUATION" shadow-certify "$PACKAGE" --suite "$root/suite.json" --catalog-dir "$CATALOG" \
    --executors "$root/executors.json" --routing "$root/routing.json" \
    --run-dir "$root/run" --result-dir "$root/result" \
    --scratch "$root/certify-scratch" --harness "$HARNESS" > "$root/certificate.json"
}

PASS_RUN="$WORK/certified"
shadow_flow "$PASS_RUN" correct none
[[ "$(json_get 'json.load(open(0))["status"]' "$PASS_RUN/certificate.json")" == pass ]] ||
  fail "the sealed catalog-plus-candidate shadow evaluation did not pass"
[[ "$(json_get 'json.load(open(0))["authoritative"]' "$PASS_RUN/certificate.json")" == True ]] ||
  fail "the complete sealed shadow evaluation did not bind catalog and backend authority"
RECEIPT="$(json_get 'json.load(open(0))["receipt"]' "$PASS_RUN/certificate.json")"
[[ "$RECEIPT" == "$SHADOW_RECEIPTS/"* ]] || fail "the shadow receipt escaped the isolated shadow receipt root"
python3 - "$RECORD" "$PASS_RUN/run/manifest.json" "$RECEIPT" "$PASS_RUN/result/aggregate.json" \
  "$CANDIDATE_ID" "$MANAGED/approved-fixture-skill/SKILL.md" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

record_path, manifest_path, receipt_path, aggregate_path, candidate_id, approved_skill_md = sys.argv[1:7]
record = json.load(open(record_path, encoding="utf-8"))
manifest = json.load(open(manifest_path, encoding="utf-8"))
receipt = json.load(open(receipt_path, encoding="utf-8"))
aggregate = json.load(open(aggregate_path, encoding="utf-8"))

revision = next(
    item for item in record["candidate_revisions"] if item["candidate_id"] == candidate_id
)
# The lifecycle package bytes and the sealed evaluation projection must be the
# same exact files, so one candidate identity spans collection and evaluation.
sealed = [
    {"path": item["path"], "sha256": item["sha256"].removeprefix("sha256:"), "size": item["size"]}
    for item in manifest["candidate_inventory"]
]
assert sealed == revision["files"], (sealed, revision["files"])
lifecycle_identity = "sha256:" + hashlib.sha256(
    json.dumps(revision["files"], sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
assert lifecycle_identity == candidate_id, lifecycle_identity
evaluator_identity = "sha256:" + hashlib.sha256(
    json.dumps(manifest["candidate_inventory"], sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
assert manifest["candidate_id"] == evaluator_identity, manifest["candidate_id"]
assert receipt["candidate_id"] == evaluator_identity, receipt["candidate_id"]
assert receipt["run_id"] == manifest["run_id"]
assert receipt["catalog_id"] == manifest["catalog_id"] and manifest["catalog_id"]
assert receipt["routing_mode"] == "catalog_plus_candidate"

approved = "sha256:" + hashlib.sha256(Path(approved_skill_md).read_bytes()).hexdigest()
catalog = {item["name"]: item for item in manifest["catalog_skills"]}
assert set(catalog) == {"approved-fixture-skill"}, catalog
assert catalog["approved-fixture-skill"]["skill_md_sha256"] == approved

assert aggregate["routing_gate"] == "pass" and aggregate["task_value_gate"] == "pass"
assert aggregate["routing"]["positive_recall"] == {"loaded": 1, "total": 1}
assert aggregate["routing"]["close_negative_false_load"] == {"false_loads": 0, "total": 1}
assert aggregate["routing"]["unrelated_false_load"] == {"false_loads": 0, "total": 1}
assert aggregate["routing"]["conflict_selection"] == {"selected_expected": 1, "total": 1}
arms = aggregate["task_value"]["pairs"][0]["arms"]
assert set(arms) == {"candidate", "control"}, arms
for treatment in ("candidate", "control"):
    for measure in ("success", "turns", "total_tokens", "tool_use"):
        assert measure in arms[treatment], (treatment, measure, arms[treatment])
PY
assert_unchanged "the sealed shadow evaluation"
pass "the sealed catalog-plus-candidate evaluation certifies the exact immutable candidate package from the lifecycle record"

# ---------------------------------------------------------------------------
# 5. An evaluation failure that requests production quarantine authority.
# ---------------------------------------------------------------------------
QUARANTINE_BEFORE="$(find "$QUARANTINE" -type f | sort)"
FAIL_RUN="$WORK/quarantine-request"
shadow_flow "$FAIL_RUN" quarantine-request value
FAIL_STATUS="$(json_get 'json.load(open(0))["status"]' "$FAIL_RUN/certificate.json")"
case "$FAIL_STATUS" in
  inconclusive|regression) ;;
  *) fail "an evaluation requesting production quarantine authority produced $FAIL_STATUS" ;;
esac
[[ "$(json_get 'json.load(open(0))["authoritative"]' "$FAIL_RUN/certificate.json")" == False ]] ||
  fail "an evaluation requesting production quarantine authority claimed authority"
python3 - "$FAIL_RUN/result" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
records = [json.load(open(path, encoding="utf-8")) for path in sorted(root.rglob("result.json"))]
requesting = [item for item in records if item["case_id"] == "value" and item["treatment"] == "candidate"]
assert requesting, "the quarantine-requesting trial is missing"
for item in requesting:
    assert item["status"] == "inconclusive", item["status"]
    assert item.get("infrastructure_error") is True, item
    assert any("authority" in message for message in item["errors"]), item["errors"]
    assert item["success"] is False, item
aggregate = json.load(open(root / "aggregate.json", encoding="utf-8"))
assert aggregate["status"] in {"inconclusive", "regression"}, aggregate["status"]
assert aggregate["task_value_gate"] != "pass", aggregate["task_value_gate"]
PY
[[ "$(find "$QUARANTINE" -type f | sort)" == "$QUARANTINE_BEFORE" ]] ||
  fail "the quarantine authority request wrote production quarantine state"
assert_unchanged "the refused quarantine authority request"
pass "an evaluation that requests production quarantine authority is refused with no production quarantine write"

# ---------------------------------------------------------------------------
# 6. A deliberate candidate transition to quarantined must fail closed.
# ---------------------------------------------------------------------------
RECEIPT_ID="$(json_get 'json.load(open(0))["receipt_id"]' "$RECEIPT")"
cp "$RECORD" "$TMP/fixtures/record-before-quarantine.json"
RECORD_VERSION="$(json_get 'json.load(open(0))["record_version"]' "$RECORD")"
RECORD_SHA="$(python3 -c '
import hashlib, json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
print(hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
' "$RECORD")"

if "$LIFECYCLE" transition "$LIFECYCLE_ID" --to quarantined --reason evaluation-demands-quarantine \
  --expected-version "$RECORD_VERSION" > "$TMP/fixtures/quarantine-attempt.out" \
  2> "$TMP/fixtures/quarantine-attempt.err"; then
  fail "a candidate transition to quarantined succeeded"
fi
cmp -s "$RECORD" "$TMP/fixtures/record-before-quarantine.json" ||
  fail "the refused quarantined transition changed the lifecycle record"

if "$LIFECYCLE" transition "$LIFECYCLE_ID" --to quarantined --reason receipt-claimed-quarantine \
  --receipt-id "$RECEIPT_ID" --expected-version "$RECORD_VERSION" \
  > "$TMP/fixtures/quarantine-receipt-attempt.out" \
  2> "$TMP/fixtures/quarantine-receipt-attempt.err"; then
  fail "a receipt-backed candidate transition to quarantined succeeded"
fi
cmp -s "$RECORD" "$TMP/fixtures/record-before-quarantine.json" ||
  fail "the refused receipt-backed quarantined transition changed the lifecycle record"
[[ "$(python3 -c '
import hashlib, json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
print(hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
' "$RECORD")" == "$RECORD_SHA" ]] || fail "the refused quarantined transitions changed the record identity"
[[ "$(json_get 'json.load(open(0))["state"]' "$RECORD")" == evaluating ]] ||
  fail "the refused quarantined transitions changed the lifecycle state"
[[ "$(json_get 'json.load(open(0))["record_version"]' "$RECORD")" == "$RECORD_VERSION" ]] ||
  fail "the refused quarantined transitions advanced the record version"
assert_unchanged "the refused quarantined lifecycle transitions"
pass "deliberate candidate transitions to quarantined fail closed byte-identically"

# ---------------------------------------------------------------------------
# 7. Every snapshotted production control surface survived the complete flow.
# ---------------------------------------------------------------------------
"$LOCK" release "$LOCK_TOKEN" >/dev/null
LOCK_TOKEN=""
unset SKILLS_LOCK_TOKEN
snapshot_production "$TMP/snapshots/after.json"
if ! python3 "$TOOLS/compare-snapshot.py" "$TMP/snapshots/before.json" "$TMP/snapshots/after.json" \
  > "$TMP/snapshots/final-diff.txt"; then
  echo "FAIL  the complete shadow flow changed a production control surface:" >&2
  cat "$TMP/snapshots/final-diff.txt" >&2
  exit 1
fi
[[ -f "$HALT" ]] || fail "the complete shadow flow removed the halt marker"
[[ "$(cat "$HALT")" == "halted-for-shadow-milestone" ]] || fail "the halt marker content changed"
[[ "$(git -C "$MANAGED" rev-parse HEAD)" == "$MANAGED_HEAD" ]] || fail "managed skill Git HEAD moved"
[[ -z "$(git -C "$MANAGED" status --porcelain)" ]] || fail "the managed skill worktree is dirty"
[[ ! -e "$MANAGED/shadow-boundary-fixture" ]] || fail "the candidate entered native skill discovery"
[[ ! -e "$MANAGED/.git/refs/heads/shadow-boundary-fixture" ]] || fail "the shadow flow created a managed skill branch"
pass "managed skill Git state and bytes, approved inventory, publisher, LaunchAgent, halt, quarantine, and retirement state are unchanged"

# ---------------------------------------------------------------------------
# 8. Shadow change is confined to the isolated shadow roots.
# ---------------------------------------------------------------------------
python3 - "$SHADOW_STATE" "$SHADOW_DATA" "$SHADOW_RECEIPTS" "$LIFECYCLE_ID" "$CANDIDATE_ID" <<'PY'
import json
import sys
from pathlib import Path

state, data, receipts, lifecycle_id, candidate_id = sys.argv[1:6]
state, data, receipts = Path(state), Path(data), Path(receipts)

records = sorted((state / "skill-review" / "candidates" / "v1" / "records").glob("*.json"))
assert [path.name for path in records] == [f"{lifecycle_id}.json"], records
packages = sorted((data / "candidates" / "v1" / "packages" / lifecycle_id).iterdir())
assert [path.name for path in packages] == [candidate_id], packages
written = sorted(receipts.glob("*.json"))
assert len(written) >= 2, written
for path in written:
    receipt = json.load(open(path, encoding="utf-8"))
    assert receipt["kind"] == "shadow_candidate_evaluation_receipt", receipt["kind"]

# Nothing outside the declared shadow roots may hold candidate state.
for root, allowed in ((state, "skill-review/candidates"), (data, "candidates")):
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        assert relative.startswith(allowed), relative
PY
pass "shadow record, package, and evaluation receipt changes stay inside the isolated shadow roots"

echo "PASS  $passes deterministic shadow mutation boundary checks"
