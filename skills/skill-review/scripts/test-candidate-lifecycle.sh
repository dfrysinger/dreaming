#!/usr/bin/env bash
# Focused deterministic checks for the shadow-only candidate lifecycle owner.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
# shellcheck source=lib-test-work.sh
source "$SCRIPT_DIR/lib-test-work.sh"
prune_test_work "$TEST_ROOT" "candidate-lifecycle" 2
TMP="$(mktemp -d "$TEST_ROOT/candidate-lifecycle.XXXXXX")"
cleanup() {
  local rc=$?
  trap - EXIT
  finish_test_work "$rc" "$TMP" "candidate lifecycle" 1
  exit "$rc"
}
trap cleanup EXIT

LIFECYCLE="$SCRIPT_DIR/candidate-lifecycle.py"
LOCK="$SCRIPT_DIR/daemon-lock.py"
export PYTHONDONTWRITEBYTECODE=1
export DREAMING_STATE_ROOT="$TMP/state"
export DREAMING_DATA_ROOT="$TMP/data"
export DREAMING_SKILLS_ROOT="$TMP/managed-skills"
export SKILLS_STATE_DIR="$TMP/lock-state"
export SKILLS_LOCK_DIR="$TMP/lock-state/daemon.lock"
export DREAMING_NOW_EPOCH=1770249600 # 2026-02-05T00:00:00Z
export SKILLS_NOW_EPOCH="$DREAMING_NOW_EPOCH"
mkdir -p "$DREAMING_SKILLS_ROOT" "$TMP/fixtures"
passes=0

pass() { echo "PASS  $*"; passes=$((passes + 1)); }
fail() { echo "FAIL  $*" >&2; exit 1; }
json_get() { python3 -c "import json; print($1)" < "$2"; }
version_of() { json_get 'json.load(open(0))["record_version"]' "$1"; }
state_of() { json_get 'json.load(open(0))["state"]' "$1"; }
candidate_of() { json_get 'json.load(open(0))["current_candidate_id"]' "$1"; }
record_path() { printf '%s/skill-review/candidates/v1/records/%s.json' "$DREAMING_STATE_ROOT" "$1"; }
run() { "$LIFECYCLE" "$@"; }
record() { record_path "$1"; }
snapshot_managed() {
  python3 - "$DREAMING_SKILLS_ROOT" <<'PY'
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
items = []
for path in sorted(root.rglob("*")):
    relative = path.relative_to(root).as_posix()
    if path.is_symlink():
        items.append(("symlink", relative, path.readlink().as_posix()))
    elif path.is_file():
        items.append(("file", relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    elif path.is_dir():
        items.append(("directory", relative, ""))
print(repr(items))
PY
}

expect_refusal() {
  local name="$1"
  shift
  if "$@" >"$TMP/$name.out" 2>"$TMP/$name.err"; then
    fail "$name unexpectedly succeeded"
  fi
}

make_procedure() {
  local path="$1" fingerprint="${2:-a}"
  python3 - "$path" "$fingerprint" <<'PY'
import json, sys
path, char = sys.argv[1:]
json.dump({
  "schema_version": 1,
  "trigger": "A bounded recurring trigger.",
  "outcome": "A user-observable stopping condition.",
  "actions": ["Inspect the bounded input", "Apply the deterministic procedure"],
  "exclusions": ["Do not cover neighboring unrelated work."],
  "match_fingerprint": "sha256:" + char * 64,
}, open(path, "w", encoding="utf-8"), sort_keys=True)
PY
}

make_observation() {
  local path="$1" task="$2" session="$3" observed="$4" independence="${5:-verified}" fingerprint="${6:-a}"
  python3 - "$path" "$task" "$session" "$observed" "$independence" "$fingerprint" "$SCRIPT_DIR" "$DREAMING_DATA_ROOT" <<'PY'
import json, sys
from pathlib import Path

path, task, session, observed, independence, char, script_dir, data_root = sys.argv[1:]
sys.path.insert(0, script_dir)
from task_occurrence import build_resolution, digest, persist

task_key = digest({"fixture_task": task})
profile = {
  "profile_id": digest({"fixture_profile": task}),
  "task_key": task_key,
  "source_event_ids": [f"{task}-goal"],
  "goal_event_id": f"{task}-goal",
  "occurred_at": observed,
}
receipt = {
  "schema_version": 2,
  "receipt_sha256": digest({"fixture_receipt": task}),
  "snapshot_sha256": digest({"fixture_snapshot": task}),
  "source_revision": digest({"fixture_revision": task}),
  "qualified_session_id": session,
}
resolution = build_resolution(
  profile=profile,
  receipt=receipt,
  relation="new-occurrence",
  review_contract="candidate-lifecycle-test-v1",
  review_executor="fixture",
  review_executor_identity={"kind": "fixture"},
  decision_at="2026-02-05T00:00:00Z",
)
persist(
  Path(data_root) / "task-occurrences" / "v2",
  Path(data_root) / "unused-task-occurrence-index.json",
  resolution,
  project=False,
)
json.dump({
  "task_key": task_key, "source_session_id": session,
  "canonical_occurrence_id": resolution["canonical_occurrence_id"],
  "occurred_at": observed, "decision_at": "2026-02-05T00:00:00Z",
  "resolution_sha256": resolution["resolution_sha256"],
  "summary": "A deterministic observation.",
  "procedure_fingerprint": "sha256:" + char * 64,
}, open(path, "w", encoding="utf-8"), sort_keys=True)
PY
}

make_legacy_observation() {
  local path="$1" task="$2" session="$3" observed="$4" fingerprint="${5:-a}"
  python3 - "$path" "$task" "$session" "$observed" "$fingerprint" <<'PY'
import json, sys
path, task, session, observed, char = sys.argv[1:]
json.dump({
  "task_key": task,
  "session_id": session,
  "observed_at": observed,
  "independence": "verified",
  "summary": "Historical observation without occurrence authority.",
  "procedure_fingerprint": "sha256:" + char * 64,
}, open(path, "w", encoding="utf-8"), sort_keys=True)
PY
}

make_package() {
  local root="$1" revision="$2" name="${3:-lifecycle-fixture}"
  mkdir -p "$root/references"
  cat > "$root/SKILL.md" <<EOF
---
name: $name
description: Deterministic candidate package.
---

# Fixture $revision
EOF
  printf 'revision=%s\n' "$revision" > "$root/references/proof.txt"
}

make_procedure "$TMP/fixtures/procedure.json"
make_package "$TMP/package-one" one
make_observation "$TMP/fixtures/one.json" task-one session-one 2026-02-01T00:00:00Z
MANAGED_BEFORE="$(snapshot_managed)"
export SKILLS_LOCK_TOKEN="$("$LOCK" acquire --mode session --owner candidate-lifecycle-test)"
initial="$(run collect --procedure "$TMP/fixtures/procedure.json" --observation "$TMP/fixtures/one.json" \
  --package "$TMP/package-one" --proposed-name lifecycle-fixture)"
printf '%s\n' "$initial" > "$TMP/initial.out"
LID="$(json_get 'json.load(open(0))["lifecycle_id"]' "$TMP/initial.out")"
REC="$(record "$LID")"
[[ -f "$REC" ]] || fail "initial candidate record missing"
[[ -d "$DREAMING_DATA_ROOT/candidates/v1/packages/$LID/$(candidate_of "$REC")" ]] ||
  fail "isolated immutable package missing"
[[ "$(snapshot_managed)" == "$MANAGED_BEFORE" ]] || fail "candidate collection wrote under managed skills"
[[ ! -e "$DREAMING_SKILLS_ROOT/lifecycle-fixture" ]] || fail "candidate entered native skill discovery"
pass "fresh collection creates only isolated shadow record and package"

cp "$REC" "$TMP/prior-record.json"
expect_refusal missing-lease env -u SKILLS_LOCK_TOKEN SKILLS_LOCK_HELD_BY_PARENT=1 \
  "$LIFECYCLE" evaluate "$LID" --expected-version "$(version_of "$REC")"
cmp -s "$REC" "$TMP/prior-record.json" || fail "missing lease changed record"
expect_refusal wrong-lease env SKILLS_LOCK_TOKEN=wrong-token "$LIFECYCLE" evaluate "$LID" --expected-version "$(version_of "$REC")"
cmp -s "$REC" "$TMP/prior-record.json" || fail "wrong lease changed record"
expect_refusal stale-write run evaluate "$LID" --expected-version 999
cmp -s "$REC" "$TMP/prior-record.json" || fail "stale write changed record"
pass "missing, wrong, and stale writer leases refuse byte-identically"

mkdir -p "$TMP/symlink-package"
ln -s "$TMP/package-one/SKILL.md" "$TMP/symlink-package/SKILL.md"
expect_refusal package-symlink run collect --procedure "$TMP/fixtures/procedure.json" \
  --observation "$TMP/fixtures/one.json" --package "$TMP/symlink-package" \
  --proposed-name symlink-fixture
pass "symlinked draft packages refuse before mutation"

make_package "$TMP/mismatched-package" mismatch
package_roots_before="$(find "$DREAMING_DATA_ROOT/candidates/v1/packages" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
expect_refusal package-name-mismatch run collect --procedure "$TMP/fixtures/procedure.json" \
  --observation "$TMP/fixtures/one.json" --package "$TMP/mismatched-package" \
  --proposed-name different-fixture
package_roots_after="$(find "$DREAMING_DATA_ROOT/candidates/v1/packages" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
[[ "$package_roots_before" == "$package_roots_after" ]] ||
  fail "mismatched package created an immutable candidate directory"
pass "package frontmatter name must match the proposed candidate name"

cp "$REC" "$TMP/malformed-record.json"
printf '{' > "$REC"
expect_refusal malformed-record run validate "$LID"
mv "$TMP/malformed-record.json" "$REC"
cp "$REC" "$TMP/unknown-state-record.json"
python3 - "$REC" <<'PY'
import json, sys
path=sys.argv[1]; value=json.load(open(path)); value["state"]="invented"; json.dump(value, open(path,"w"))
PY
expect_refusal unknown-state run validate "$LID"
mv "$TMP/unknown-state-record.json" "$REC"
cp "$REC" "$TMP/pre-illegal-transition.json"
expect_refusal production-transition run transition "$LID" --to admitted --reason test-production --expected-version "$(version_of "$REC")"
cmp -s "$REC" "$TMP/pre-illegal-transition.json" || fail "illegal transition changed record"
pass "malformed records, unknown states, and production transitions fail closed"

ONE_RESOLUTION="$(json_get 'json.load(open(0))["resolution_sha256"].removeprefix("sha256:")' "$TMP/fixtures/one.json")"
ONE_RESOLUTION_PATH="$DREAMING_DATA_ROOT/task-occurrences/v2/$ONE_RESOLUTION.json"
cp "$ONE_RESOLUTION_PATH" "$TMP/one-resolution.json"
rm "$ONE_RESOLUTION_PATH"
expect_refusal missing-occurrence-authority run validate "$LID"
mv "$TMP/one-resolution.json" "$ONE_RESOLUTION_PATH"
chmod 400 "$ONE_RESOLUTION_PATH"
run validate "$LID" >/dev/null
cp "$TMP/fixtures/one.json" "$TMP/fixtures/unbound-occurrence.json"
python3 - "$TMP/fixtures/unbound-occurrence.json" <<'PY'
import json, sys
path = sys.argv[1]
value = json.load(open(path))
value["resolution_sha256"] = "sha256:" + "f" * 64
json.dump(value, open(path, "w"), sort_keys=True)
PY
make_package "$TMP/unbound-package" unbound unbound-fixture
expect_refusal unbound-occurrence run collect \
  --procedure "$TMP/fixtures/procedure.json" \
  --observation "$TMP/fixtures/unbound-occurrence.json" \
  --package "$TMP/unbound-package" --proposed-name unbound-fixture
pass "current recurrence evidence requires matching immutable occurrence authority"

make_observation "$TMP/fixtures/two.json" task-two session-two 2026-02-04T00:00:00Z
make_package "$TMP/package-two" two
before_candidate="$(candidate_of "$REC")"
run collect --lifecycle-id "$LID" --expected-version "$(version_of "$REC")" \
  --procedure "$TMP/fixtures/procedure.json" --observation "$TMP/fixtures/two.json" \
  --package "$TMP/package-two" --proposed-name lifecycle-fixture >/dev/null
[[ "$LID" == "$(json_get 'json.load(open(0))["lifecycle_id"]' "$REC")" ]] || fail "lifecycle id changed across revision"
[[ "$before_candidate" != "$(candidate_of "$REC")" ]] || fail "content edit did not create exact candidate identity"
[[ ! -w "$DREAMING_DATA_ROOT/candidates/v1/packages/$LID/$before_candidate/SKILL.md" ]] ||
  fail "prior immutable package is writable"
OLD_PACKAGE="$DREAMING_DATA_ROOT/candidates/v1/packages/$LID/$before_candidate"
chmod -R u+w "$OLD_PACKAGE"
printf 'tampered\n' > "$OLD_PACKAGE/references/proof.txt"
chmod -R a-w "$OLD_PACKAGE"
make_observation "$TMP/fixtures/three.json" task-three session-three 2026-02-04T00:00:00Z
expect_refusal package-collision run collect --lifecycle-id "$LID" --expected-version "$(version_of "$REC")" \
  --procedure "$TMP/fixtures/procedure.json" --observation "$TMP/fixtures/three.json" \
  --package "$TMP/package-one" --proposed-name lifecycle-fixture
chmod -R u+w "$OLD_PACKAGE"
cp "$TMP/package-one/references/proof.txt" "$OLD_PACKAGE/references/proof.txt"
chmod -R a-w "$OLD_PACKAGE"
run validate "$LID" >/dev/null
python3 - "$TMP/package-two" "$(candidate_of "$REC")" <<'PY'
import hashlib, json, sys
from pathlib import Path

root, actual = map(Path, sys.argv[1:])
files = []
for path in sorted(root.rglob("*")):
    if path.is_file():
        content = path.read_bytes()
        files.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        })
expected = "sha256:" + hashlib.sha256(
    json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
assert expected == str(actual)
PY
pass "stable lifecycle identity retains immutable content-derived revisions"

make_observation "$TMP/fixtures/three-current.json" task-three-current session-one 2026-02-04T00:00:00Z
run collect --lifecycle-id "$LID" --expected-version "$(version_of "$REC")" \
  --procedure "$TMP/fixtures/procedure.json" --observation "$TMP/fixtures/three-current.json" \
  --package "$TMP/package-two" --proposed-name lifecycle-fixture >/dev/null
run evaluate "$LID" --expected-version "$(version_of "$REC")" > "$TMP/evaluate.out"
[[ "$(json_get 'json.load(open(0))["recommendation"]' "$TMP/evaluate.out")" == ready_for_draft ]] ||
  fail "three independent current occurrences did not recommend ready"
[[ "$(state_of "$REC")" == ready_for_draft ]] || fail "ready recurrence did not transition state"
make_package "$TMP/package-three" three
evidence_count_before="$(json_get 'len(json.load(open(0))["evidence"])' "$REC")"
revision_count_before="$(json_get 'len(json.load(open(0))["candidate_revisions"])' "$REC")"
evaluated_candidate="$(candidate_of "$REC")"
evaluated_at_before="$(json_get 'json.load(open(0))["evaluation"]["last_evaluated_at"]' "$REC")"
run revise "$LID" --package "$TMP/package-three" \
  --expected-version "$(version_of "$REC")" >/dev/null
[[ "$evaluated_candidate" != "$(candidate_of "$REC")" ]] ||
  fail "candidate revision did not change the exact candidate identity"
[[ "$(json_get 'len(json.load(open(0))["evidence"])' "$REC")" == "$evidence_count_before" ]] ||
  fail "candidate revision invented or removed recurrence evidence"
[[ "$(json_get 'len(json.load(open(0))["candidate_revisions"])' "$REC")" == "$((revision_count_before + 1))" ]] ||
  fail "candidate revision did not append exactly one immutable package"
[[ "$(json_get 'json.load(open(0))["evaluation"]["status"]' "$REC")" == not_evaluated ]] ||
  fail "candidate revision retained a stale recommendation summary"
[[ "$(json_get 'json.load(open(0))["evaluation"]["last_evaluated_at"]' "$REC")" == None ]] ||
  fail "candidate revision retained a stale evaluation timestamp"
[[ "$(json_get 'json.load(open(0))["evaluation"]["history"][-1]["candidate_id"]' "$REC")" == "$evaluated_candidate" ]] ||
  fail "candidate revision rewrote prior evaluation history"
cp "$REC" "$TMP/pre-unevaluated-transition.json"
expect_refusal stale-revision-evaluating run transition "$LID" --to evaluating \
  --reason stale-revision-evaluation --candidate-id "$(candidate_of "$REC")" \
  --expected-version "$(version_of "$REC")"
cmp -s "$REC" "$TMP/pre-unevaluated-transition.json" ||
  fail "stale successor transition changed the lifecycle record"
cp "$REC" "$TMP/pre-mismatched-revision.json"
make_package "$TMP/mismatched-revision-package" mismatch different-fixture
expect_refusal revise-name-mismatch run revise "$LID" --package "$TMP/mismatched-revision-package" \
  --expected-version "$(version_of "$REC")"
cmp -s "$REC" "$TMP/pre-mismatched-revision.json" ||
  fail "rejected candidate revision changed the lifecycle record"
run evaluate "$LID" --expected-version "$(version_of "$REC")" > "$TMP/revised-evaluate.out"
[[ "$(json_get 'json.load(open(0))["evaluation"]["history"][-1]["candidate_id"]' "$REC")" == "$(candidate_of "$REC")" ]] ||
  fail "successor recommendation was not bound to the exact revised candidate"
pass "candidate revisions preserve recurrence evidence and require a fresh recommendation"
run transition "$LID" --to evaluating --reason exact-draft-evaluation \
  --candidate-id "$(candidate_of "$REC")" --expected-version "$(version_of "$REC")" >/dev/null
[[ "$(state_of "$REC")" == evaluating ]] || fail "legal evaluating transition failed"
make_package "$TMP/package-four" four
run revise "$LID" --package "$TMP/package-four" \
  --expected-version "$(version_of "$REC")" >/dev/null
[[ "$(state_of "$REC")" == ready_for_draft ]] ||
  fail "revision during evaluation did not return to ready_for_draft"
[[ "$(json_get 'json.load(open(0))["evaluation"]["status"]' "$REC")" == not_evaluated ]] ||
  fail "revision during evaluation retained a stale recommendation"
run evaluate "$LID" --expected-version "$(version_of "$REC")" >/dev/null
run transition "$LID" --to evaluating --reason revised-draft-evaluation \
  --candidate-id "$(candidate_of "$REC")" --expected-version "$(version_of "$REC")" >/dev/null
run transition "$LID" --to rejected --reason evaluation-rejected \
  --expected-version "$(version_of "$REC")" >/dev/null
[[ "$(state_of "$REC")" == rejected ]] || fail "legal rejected transition failed"
expect_refusal rejected-direct-reopen run transition "$LID" --to collecting --reason invalid-reopen \
  --expected-version "$(version_of "$REC")"
pass "declared collecting-ready-evaluating-rejected transitions retain shadow-only control"

collect_fixture() {
  local label="$1" first_task="$2" first_session="$3" first_at="$4" second_task="$5" second_session="$6" second_at="$7"
  local procedure="$TMP/fixtures/$label-procedure.json" one="$TMP/fixtures/$label-one.json" two="$TMP/fixtures/$label-two.json" three="$TMP/fixtures/$label-three.json" package="$TMP/$label-package"
  make_procedure "$procedure"; make_observation "$one" "$first_task" "$first_session" "$first_at"; make_observation "$two" "$second_task" "$second_session" "$second_at"; make_observation "$three" "$label-task-three" "$first_session" "$second_at"; make_package "$package" "$label" "$label"
  local created id rec
  created="$(run collect --procedure "$procedure" --observation "$one" --package "$package" --proposed-name "$label")"; id="$(printf '%s' "$created" | python3 -c 'import json,sys; print(json.load(sys.stdin)["lifecycle_id"])')"; rec="$(record "$id")"
  run collect --lifecycle-id "$id" --expected-version "$(version_of "$rec")" --procedure "$procedure" --observation "$two" --package "$package" --proposed-name "$label" --match-outcome same >/dev/null
  run collect --lifecycle-id "$id" --expected-version "$(version_of "$rec")" --procedure "$procedure" --observation "$three" --package "$package" --proposed-name "$label" --match-outcome same >/dev/null
  run evaluate "$id" --expected-version "$(version_of "$rec")" > "$TMP/$label-evaluate.out"; printf '%s\n' "$id"
}
WITHIN="$(collect_fixture within-30 task-within-a session-within-a 2026-02-01T00:00:00Z task-within-b session-within-a 2026-02-04T00:00:00Z)"
[[ "$(state_of "$(record "$WITHIN")")" == ready_for_draft ]] || fail "three occurrences in one session did not pass"
FAR="$(collect_fixture beyond-30 task-far-a session-far-a 2025-12-15T00:00:00Z task-far-b session-far-b 2026-02-04T00:00:00Z)"
[[ "$(state_of "$(record "$FAR")")" == collecting ]] || fail "old occurrence became current"
EXACT="$(collect_fixture exact-30 task-exact-a session-exact 2026-01-06T00:00:00Z task-exact-b session-exact 2026-02-04T00:00:00Z)"
[[ "$(state_of "$(record "$EXACT")")" == ready_for_draft ]] ||
  fail "an occurrence exactly 30 days old was excluded"
OVER="$(collect_fixture over-30 task-over-a session-over 2026-01-05T23:59:59Z task-over-b session-over 2026-02-04T00:00:00Z)"
[[ "$(state_of "$(record "$OVER")")" == collecting ]] ||
  fail "an occurrence 30 days plus one second old was accepted"
make_legacy_observation "$TMP/fixtures/exact-legacy.json" legacy-task session-exact 2026-02-04T00:00:00Z
run collect --lifecycle-id "$EXACT" \
  --expected-version "$(version_of "$(record "$EXACT")")" \
  --procedure "$TMP/fixtures/exact-30-procedure.json" \
  --observation "$TMP/fixtures/exact-legacy.json" \
  --package "$TMP/exact-30-package" --proposed-name exact-30 \
  --match-outcome same >/dev/null
run evaluate "$EXACT" --expected-version "$(version_of "$(record "$EXACT")")" >/dev/null
[[ "$(state_of "$(record "$EXACT")")" == ready_for_draft ]] ||
  fail "legacy evidence crashed or weakened a current candidate record"
pass "recurrence requires three current occurrences, allows one session, and enforces the exact 30-day edge"

EXPIRE_PROC="$TMP/fixtures/expire-procedure.json"
EXPIRE_ONE="$TMP/fixtures/expire-one.json"
EXPIRE_TWO="$TMP/fixtures/expire-two.json"
EXPIRE_PACKAGE="$TMP/expire-package"
make_procedure "$EXPIRE_PROC"
make_observation "$EXPIRE_ONE" task-expire-old session-expire-old 2025-12-01T00:00:00Z
make_observation "$EXPIRE_TWO" task-expire-new session-expire-new 2026-02-04T00:00:00Z
make_package "$EXPIRE_PACKAGE" expire expire-fixture
EXPIRE_OUT="$(run collect --procedure "$EXPIRE_PROC" --observation "$EXPIRE_ONE" --package "$EXPIRE_PACKAGE" --proposed-name expire-fixture)"
EXPIRE_ID="$(printf '%s' "$EXPIRE_OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["lifecycle_id"])')"
EXPIRE_REC="$(record "$EXPIRE_ID")"
run expire "$EXPIRE_ID" --expected-version "$(version_of "$EXPIRE_REC")" >/dev/null
[[ "$(state_of "$EXPIRE_REC")" == expired ]] || fail "unsupported candidate did not expire"
run collect --lifecycle-id "$EXPIRE_ID" --expected-version "$(version_of "$EXPIRE_REC")" \
  --procedure "$EXPIRE_PROC" --observation "$EXPIRE_TWO" --package "$EXPIRE_PACKAGE" \
  --proposed-name expire-fixture >/dev/null
[[ "$(state_of "$EXPIRE_REC")" == collecting ]] || fail "fresh verified observation did not reopen expired record"
python3 - "$EXPIRE_REC" <<'PY'
import json, sys
record=json.load(open(sys.argv[1]))
assert len(record["evidence"]) == 2
assert [x["to_state"] for x in record["lifecycle"]["transition_history"]] == ["collecting", "expired", "collecting"]
PY
pass "expiration and reopen preserve append-only evidence and transition history"

LEGACY_PROC="$TMP/fixtures/legacy-procedure.json"
LEGACY_OBS="$TMP/fixtures/legacy-observation.json"
LEGACY_PACKAGE="$TMP/legacy-package"
make_procedure "$LEGACY_PROC"
make_legacy_observation "$LEGACY_OBS" legacy-task session-legacy 2026-02-04T00:00:00Z
make_package "$LEGACY_PACKAGE" legacy legacy-fixture
LEGACY_OUT="$(run collect --procedure "$LEGACY_PROC" --observation "$LEGACY_OBS" \
  --package "$LEGACY_PACKAGE" --proposed-name legacy-fixture)"
LEGACY_ID="$(printf '%s' "$LEGACY_OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["lifecycle_id"])')"
LEGACY_REC="$(record "$LEGACY_ID")"
run transition "$LEGACY_ID" --to rejected --reason legacy-rejected \
  --expected-version "$(version_of "$LEGACY_REC")" >/dev/null
LEGACY_EVIDENCE_ID="$(json_get 'json.load(open(0))["evidence"][0]["evidence_id"]' "$LEGACY_REC")"
run reopen "$LEGACY_ID" --evidence-id "$LEGACY_EVIDENCE_ID" \
  --expected-version "$(version_of "$LEGACY_REC")" >/dev/null
[[ "$(state_of "$LEGACY_REC")" == collecting ]] ||
  fail "legacy evidence could not reopen through its observed_at timestamp"
LEGACY_CANDIDATE="$(candidate_of "$LEGACY_REC")"
LEGACY_PROOF="$DREAMING_DATA_ROOT/candidates/v1/packages/$LEGACY_ID/$LEGACY_CANDIDATE/references/proof.txt"
cp "$LEGACY_PROOF" "$TMP/legacy-proof.txt"
chmod u+w "$LEGACY_PROOF"
printf 'tampered\n' > "$LEGACY_PROOF"
chmod a-w "$LEGACY_PROOF"
expect_refusal legacy-package-tamper run validate "$LEGACY_ID"
chmod u+w "$LEGACY_PROOF"
cp "$TMP/legacy-proof.txt" "$LEGACY_PROOF"
chmod a-w "$LEGACY_PROOF"
run validate "$LEGACY_ID" >/dev/null
pass "legacy records retain full structural and immutable-package validation"

ABSORB_PROC="$TMP/fixtures/absorbed-procedure.json"
ABSORB_OBS="$TMP/fixtures/absorbed-observation.json"
ABSORB_PACKAGE="$TMP/absorbed-package"
make_procedure "$ABSORB_PROC"
make_observation "$ABSORB_OBS" task-absorbed session-absorbed 2026-02-04T00:00:00Z
make_package "$ABSORB_PACKAGE" absorbed absorbed-fixture
ABSORB_OUT="$(run collect --procedure "$ABSORB_PROC" --observation "$ABSORB_OBS" --package "$ABSORB_PACKAGE" --proposed-name absorbed-fixture)"
ABSORB_ID="$(printf '%s' "$ABSORB_OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["lifecycle_id"])')"
ABSORB_REC="$(record "$ABSORB_ID")"
run decide "$ABSORB_ID" --outcome supersedes --reason replacement-pending \
  --related-lifecycle-id "$LID" --expected-version "$(version_of "$ABSORB_REC")" >/dev/null
run decide "$ABSORB_ID" --outcome duplicate --reason duplicate-recorded \
  --related-lifecycle-id "$LID" --expected-version "$(version_of "$ABSORB_REC")" >/dev/null
run transition "$ABSORB_ID" --to absorbed --reason absorbed-by-umbrella \
  --related-lifecycle-id "$LID" --expected-version "$(version_of "$ABSORB_REC")" >/dev/null
[[ "$(state_of "$ABSORB_REC")" == absorbed ]] || fail "legal absorbed transition failed"
python3 - "$ABSORB_REC" "$LID" <<'PY'
import json, sys
record=json.load(open(sys.argv[1]))
assert record["absorbed_into"] == sys.argv[2]
assert {item["outcome"] for item in record["match_decisions"]} >= {"supersedes", "absorbs"}
assert record["publication"] == {"status": "shadow_only"}
PY
pass "supersession and absorption remain recorded decisions, not publisher mutations"

run validate >/dev/null
[[ "$(snapshot_managed)" == "$MANAGED_BEFORE" ]] || fail "shadow flow changed managed skill root"
[[ ! -e "$DREAMING_DATA_ROOT/skills" ]] || fail "shadow flow created a managed data skill root"
pass "validation remains isolated from managed skills and publisher roots"

"$LOCK" release "$SKILLS_LOCK_TOKEN" >/dev/null
echo "PASS  $passes deterministic candidate lifecycle checks"
