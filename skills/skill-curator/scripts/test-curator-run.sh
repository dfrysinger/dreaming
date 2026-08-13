#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
RUNNER="$SCRIPT_DIR/curator-run.py"
ARCHIVER="$REPO_ROOT/skills/skill-manage/scripts/archive-skill.sh"
RESTORER="$REPO_ROOT/skills/skill-manage/scripts/restore-skill.sh"
PINNER="$REPO_ROOT/skills/skill-manage/scripts/pin-skill.sh"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/curator-run.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
export TMPDIR="$TMP"
export DREAMING_REPO_ROOT="$REPO_ROOT"
export DREAMING_SHARED_SKILLS_ROOT="$TMP/shared"
export SKILLS_LAUNCH_AGENTS_DIR="$TMP/LaunchAgents"
mkdir -p "$SKILLS_LAUNCH_AGENTS_DIR"
for name in writing-great-skills dual-review authenticated-browse; do
  mkdir -p "$DREAMING_SHARED_SKILLS_ROOT/skills/$name"
  printf -- '---\nname: %s\ndescription: Shared test fixture dependency.\n---\n' "$name" \
    > "$DREAMING_SHARED_SKILLS_ROOT/skills/$name/SKILL.md"
done

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

make_skill() {
  local root="$1" name="$2"
  mkdir -p "$root/$name"
  cat > "$root/$name/SKILL.md" <<EOF
---
name: $name
description: Curator transaction fixture.
author: skill-review
---

# $name
EOF
  touch "$root/$name/.agent-created"
  cat > "$root/$name/.agent-created.json" <<EOF
{
  "schema_version": 2,
  "skill": "$name",
  "created_by": "skill-review",
  "source_session_id": "fixture-session",
  "source_mode": "dispatch",
  "review_prompt_version": "skill-review-2",
  "created_at": "2025-01-01T00:00:00+00:00",
  "evidence": [{
    "task_key": "task:11111111-1111-1111-1111-111111111111",
    "session_id": "fixture-session",
    "observed_at": "2025-01-01T00:00:00+00:00",
    "independence": "verified",
    "evidence_kind": "successful-procedure",
    "summary": "Curator transaction fixture provenance"
  }],
  "routing": {"destination": "skill", "reason": "Fixture skill"},
  "claims": [],
  "evaluation": {
    "status": "not_evaluated",
    "evaluated_at": null,
    "candidate_id": null,
    "model": null,
    "source_case": null,
    "sibling_case": null,
    "waiver_class": null,
    "waiver_reason": null
  }
}
EOF
}

init_fixture() {
  CASE="$1"
  PUBLIC="$CASE/public"
  LOCAL="$CASE/local"
  STATE="$CASE/state"
  PLISTS="$CASE/plists"
  RUNS="$STATE/curator-runs"
  export SKILLS_REVIEW_STATE_DIR="$STATE"
  mkdir -p "$PUBLIC/skills" "$PUBLIC/.claude-plugin" \
    "$PUBLIC/.codex-plugin" "$LOCAL" "$STATE" "$PLISTS"
  printf '{"paused":false}\n' > "$CASE/curator.json"
  git -C "$PUBLIC" init -q -b main
  git -C "$LOCAL" init -q -b main
  for root in "$PUBLIC" "$LOCAL"; do
    git -C "$root" config user.email test@example.com
    git -C "$root" config user.name Test
    git -C "$root" config core.hooksPath /dev/null
  done
  echo '{"name":"fixture","version":"0.1.0","skills":[]}' \
    > "$PUBLIC/.claude-plugin/plugin.json"
  echo '{"name":"fixture","metadata":{"version":"0.1.0"},"plugins":[{"name":"fixture","version":"0.1.0"}]}' \
    > "$PUBLIC/.claude-plugin/marketplace.json"
  echo '{"name":"fixture","version":"0.1.0"}' \
    > "$PUBLIC/.codex-plugin/plugin.json"
  echo "public base" > "$PUBLIC/README.md"
  echo "local base" > "$LOCAL/README.md"
  make_skill "$PUBLIC/skills" umbrella
  make_skill "$PUBLIC/skills" old-public
  make_skill "$LOCAL" old-local
  git -C "$PUBLIC" add .
  git -C "$PUBLIC" commit -qm base
  git -C "$LOCAL" add .
  git -C "$LOCAL" commit -qm base
  git init --bare -q "$CASE/public-remote.git"
  git -C "$PUBLIC" remote add origin "$CASE/public-remote.git"
  git -C "$PUBLIC" push -q -u origin main
}

run_curator() {
  SKILLS_REPO_ROOT="$PUBLIC" \
  SKILLS_LOCAL_ROOT="$LOCAL" \
  SKILLS_STATE_DIR="$STATE" \
  SKILLS_CURATOR_RUNS_DIR="$RUNS" \
  SKILLS_LAUNCH_AGENTS_DIR="$PLISTS" \
  SKILLS_ALLOW_NO_SCHEDULED_JOBS=1 \
  CURATOR_ALLOW_UNSTAMPED_REPORT=1 \
  SKILLS_CURATOR_STATE_FILE="$CASE/curator.json" \
  SKILLS_HALT_SWITCH="$STATE/disable-daemon" \
  CURATOR_DEPENDENCY_SCANNER="${CURATOR_DEPENDENCY_SCANNER:-$SCRIPT_DIR/scheduled-skill-deps.py}" \
  SKILLS_LOCK_DIR="$STATE/writer-lock.sqlite" \
    "$RUNNER" "$@"
}

archive_skill() {
  local run_id="$1" skill="$2"
  shift 2
  SKILLS_REPO_ROOT="$PUBLIC" \
  SKILLS_LOCAL_ROOT="$LOCAL" \
  SKILLS_STATE_DIR="$STATE" \
  SKILLS_CURATOR_RUNS_DIR="$RUNS" \
  SKILLS_LAUNCH_AGENTS_DIR="$PLISTS" \
  SKILLS_ALLOW_NO_SCHEDULED_JOBS=1 \
  CURATOR_ALLOW_UNSTAMPED_REPORT=1 \
  SKILLS_CURATOR_STATE_FILE="$CASE/curator.json" \
  SKILLS_HALT_SWITCH="$STATE/disable-daemon" \
  SKILLS_LOCK_DIR="$STATE/writer-lock.sqlite" \
  SKILLS_CURATOR_RUN_ID="$run_id" \
    "$ARCHIVER" "$skill" "$@" >/dev/null
}

restore_skill() {
  SKILLS_REPO_ROOT="$PUBLIC" \
  SKILLS_LOCAL_ROOT="$LOCAL" \
  SKILLS_STATE_DIR="$STATE" \
  SKILLS_LOCK_DIR="$STATE/writer-lock.sqlite" \
    "$RESTORER" "$1" >/dev/null
}

write_pruning_report() {
  local path="$1" skill="$2"
  cat > "$path" <<EOF
# Curator fixture report

\`\`\`yaml
consolidations: []
prunings:
  - name: $skill
    reason: Test-authorized pruning.
    evidence:
      basis: age-only
      created_at: 2025-01-01T00:00:00+00:00
      last_used_at: never
      completion_evidence: not-required-age-threshold
      reuse_assessment: no-reusable-content
      evaluation: not-required-no-merge-target
      tombstone_effect: permanent-name-family-block-acknowledged
manual_review: []
\`\`\`
EOF
}

CASE="$TMP/two-root"
init_fixture "$CASE"
echo "public unrelated" > "$PUBLIC/notes.txt"
echo "local unrelated" > "$LOCAL/local-notes.txt"
echo "public staged" >> "$PUBLIC/README.md"
echo "local staged" >> "$LOCAL/README.md"
git -C "$PUBLIC" add README.md
git -C "$LOCAL" add README.md
PUBLIC_DIRTY="$(shasum -a 256 "$PUBLIC/notes.txt")"
LOCAL_DIRTY="$(shasum -a 256 "$LOCAL/local-notes.txt")"
PUBLIC_INDEX="$(git -C "$PUBLIC" diff --cached --binary)"
LOCAL_INDEX="$(git -C "$LOCAL" diff --cached --binary)"
MANIFESTS_BEFORE="$(shasum -a 256 \
  "$PUBLIC/.claude-plugin/plugin.json" \
  "$PUBLIC/.claude-plugin/marketplace.json" \
  "$PUBLIC/.codex-plugin/plugin.json")"
cat > "$CASE/plan.json" <<'JSON'
{
  "operations": [
    {
      "kind": "commit",
      "action": "patch",
      "root": "public",
      "skill": "umbrella",
      "paths": ["skills/umbrella/SKILL.md"]
    },
    {"kind": "archive", "skill": "old-public"},
    {
      "kind": "commit",
      "action": "create",
      "root": "local",
      "skill": "new-local",
      "paths": ["new-local/SKILL.md", "new-local/.agent-created", "new-local/.agent-created.json"]
    },
    {"kind": "archive", "skill": "old-local"}
  ]
}
JSON
RUN_ID="$(run_curator begin --plan "$CASE/plan.json" --report "$CASE/report.md")"

if archive_skill "$RUN_ID" old-public 2>/dev/null; then
  fail "out-of-order archive was accepted"
fi
[[ -e "$PUBLIC/skills/old-public" ]] ||
  fail "out-of-order refusal mutated the archive target"
if SKILLS_REPO_ROOT="$PUBLIC" \
   SKILLS_LOCAL_ROOT="$LOCAL" \
   SKILLS_STATE_DIR="$STATE" \
   SKILLS_CURATOR_RUNS_DIR="$RUNS" \
   SKILLS_LAUNCH_AGENTS_DIR="$PLISTS" \
   SKILLS_ALLOW_NO_SCHEDULED_JOBS=1 \
   SKILLS_LOCK_DIR="$STATE/writer-lock.sqlite" \
     "$ARCHIVER" old-local >/dev/null 2>&1; then
  fail "standalone archive bypassed the active curator writer lease"
fi
[[ -e "$LOCAL/old-local" ]] || fail "writer-lease refusal mutated the target"
if SKILLS_REPO_ROOT="$PUBLIC" \
   SKILLS_LOCAL_ROOT="$LOCAL" \
   SKILLS_STATE_DIR="$STATE" \
   SKILLS_LOCK_DIR="$STATE/writer-lock.sqlite" \
     "$PINNER" old-local >/dev/null 2>&1; then
  fail "pin mutation bypassed the active curator writer lease"
fi
[[ ! -e "$LOCAL/old-local/.pinned" ]] ||
  fail "writer-lease refusal created a pin"

OP="$(run_curator intent --run "$RUN_ID" --kind commit --root public \
  --action patch --skill umbrella --paths skills/umbrella/SKILL.md)"
echo "transaction patch" >> "$PUBLIC/skills/umbrella/SKILL.md"
printf '{"session_id":"curator-effect","mode":"dispatch"}\n' >> "$STATE/ledger.jsonl"
cat > "$CASE/message-1.txt" <<'EOF'
skill-curator: patch umbrella
EOF
run_curator commit --run "$RUN_ID" --op "$OP" --message-file "$CASE/message-1.txt" \
  >/dev/null

archive_skill "$RUN_ID" old-public

OP="$(run_curator intent --run "$RUN_ID" --kind commit --root local \
  --action create --skill new-local \
  --paths new-local/SKILL.md new-local/.agent-created new-local/.agent-created.json)"
make_skill "$LOCAL" new-local
cat > "$CASE/message-2.txt" <<'EOF'
skill-curator: create new-local
EOF
run_curator commit --run "$RUN_ID" --op "$OP" --message-file "$CASE/message-2.txt" \
  >/dev/null

archive_skill "$RUN_ID" old-local
run_curator publish --run "$RUN_ID"
run_curator finish --run "$RUN_ID"
[[ ! -e "$PUBLIC/skills/old-public" ]] || fail "public archive did not apply"
[[ ! -e "$LOCAL/old-local" ]] || fail "local archive did not apply"
[[ -e "$LOCAL/new-local" ]] || fail "local create did not apply"
grep -q "transaction patch" "$PUBLIC/skills/umbrella/SKILL.md" ||
  fail "public patch did not apply"

printf '{"session_id":"unrelated-later","mode":"dispatch"}\n' >> "$STATE/ledger.jsonl"
run_curator rollback --run "$RUN_ID"
[[ -e "$PUBLIC/skills/old-public/SKILL.md" ]] ||
  fail "public archive was not restored"
[[ -e "$LOCAL/old-local/SKILL.md" ]] || fail "local archive was not restored"
[[ ! -e "$LOCAL/new-local" ]] || fail "local create was not reverted"
if grep -q "transaction patch" "$PUBLIC/skills/umbrella/SKILL.md"; then
  fail "public patch was not reverted"
fi
[[ "$(shasum -a 256 "$PUBLIC/notes.txt")" == "$PUBLIC_DIRTY" ]] ||
  fail "public unrelated dirty file changed"
[[ "$(shasum -a 256 "$LOCAL/local-notes.txt")" == "$LOCAL_DIRTY" ]] ||
  fail "local unrelated dirty file changed"
[[ "$(git -C "$PUBLIC" diff --cached --binary)" == "$PUBLIC_INDEX" ]] ||
  fail "public unrelated staged state changed"
[[ "$(git -C "$LOCAL" diff --cached --binary)" == "$LOCAL_INDEX" ]] ||
  fail "local unrelated staged state changed"
MANIFESTS_AFTER="$(shasum -a 256 \
  "$PUBLIC/.claude-plugin/plugin.json" \
  "$PUBLIC/.claude-plugin/marketplace.json" \
  "$PUBLIC/.codex-plugin/plugin.json")"
[[ "$MANIFESTS_AFTER" == "$MANIFESTS_BEFORE" ]] ||
  fail "public manifest bytes changed across rollback"
git -C "$PUBLIC" diff --quiet HEAD -- \
  .claude-plugin/plugin.json \
  .claude-plugin/marketplace.json \
  .codex-plugin/plugin.json ||
  fail "restored public manifests were not committed"
grep -q '"session_id":"unrelated-later"' "$STATE/ledger.jsonl" ||
  fail "later unrelated ledger append was removed"
if grep -q '"session_id":"curator-effect"' "$STATE/ledger.jsonl"; then
  fail "recorded curator ledger effect survived rollback"
fi
[[ "$(git --git-dir="$CASE/public-remote.git" show main:skills/old-public/SKILL.md)" == *"# old-public"* ]] ||
  fail "public pushed rollback did not restore the prior tree"
[[ ! -e "$STATE/retired/old-public.json" ]] ||
  fail "public retirement record survived rollback"
[[ ! -e "$STATE/tombstones/old-public.json" ]] ||
  fail "public tombstone survived rollback"
echo "PASS: two-root rollback preserves unrelated dirty state and ledger appends"

CASE="$TMP/interrupted"
init_fixture "$CASE"
cat > "$CASE/plan.json" <<'JSON'
{"operations":[{"kind":"commit","action":"create","root":"local","skill":"partial","paths":["partial/SKILL.md","partial/.agent-created","partial/.agent-created.json"]}]}
JSON
RUN_ID="$(run_curator begin --plan "$CASE/plan.json" --report "$CASE/report.md")"
run_curator intent --run "$RUN_ID" --kind commit --root local \
  --action create --skill partial \
  --paths partial/SKILL.md partial/.agent-created partial/.agent-created.json >/dev/null
make_skill "$LOCAL" partial
run_curator rollback --run "$RUN_ID"
if [[ -e "$LOCAL/partial" ]]; then
  find "$LOCAL/partial" -maxdepth 2 -print >&2
  fail "interrupted uncommitted create survived rollback"
fi
echo "PASS: interrupted uncommitted operation is removed"

CASE="$TMP/interrupted-existing"
init_fixture "$CASE"
UMBRELLA_BEFORE="$(shasum -a 256 "$PUBLIC/skills/umbrella/SKILL.md")"
cat > "$CASE/plan.json" <<'JSON'
{"operations":[{"kind":"commit","action":"patch","root":"public","skill":"umbrella","paths":["skills/umbrella/SKILL.md","skills/umbrella/references/new.md"]}]}
JSON
RUN_ID="$(run_curator begin --plan "$CASE/plan.json" --report "$CASE/report.md")"
run_curator intent --run "$RUN_ID" --kind commit --root public \
  --action patch --skill umbrella \
  --paths skills/umbrella/SKILL.md skills/umbrella/references/new.md >/dev/null
echo "interrupted edit" >> "$PUBLIC/skills/umbrella/SKILL.md"
mkdir -p "$PUBLIC/skills/umbrella/references"
echo "new residue" > "$PUBLIC/skills/umbrella/references/new.md"
run_curator rollback --run "$RUN_ID"
[[ "$(shasum -a 256 "$PUBLIC/skills/umbrella/SKILL.md")" == "$UMBRELLA_BEFORE" ]] ||
  fail "existing declared tree was not restored"
[[ ! -e "$PUBLIC/skills/umbrella/references/new.md" ]] ||
  fail "untracked residue survived existing-tree rollback"
echo "PASS: interrupted existing-tree operation removes untracked residue"

CASE="$TMP/interrupted-archive"
init_fixture "$CASE"
make_skill "$PUBLIC/skills" old-local
git -C "$PUBLIC" add skills/old-local
git -C "$PUBLIC" commit -qm "historical same-name skill"
git -C "$PUBLIC" rm -rq skills/old-local
git -C "$PUBLIC" commit -qm "historical same-name deletion"
cat > "$CASE/plan.json" <<'JSON'
{"operations":[{"kind":"archive","skill":"old-local"}]}
JSON
RUN_ID="$(run_curator begin --plan "$CASE/plan.json" --report "$CASE/report.md")"
FAKE_RUNNER="$CASE/fail-complete.sh"
cat > "$FAKE_RUNNER" <<EOF
#!/usr/bin/env bash
if [[ "\$1" == "complete" ]]; then
  exit 9
fi
exec "$RUNNER" "\$@"
EOF
chmod +x "$FAKE_RUNNER"
if SKILLS_REPO_ROOT="$PUBLIC" \
   SKILLS_LOCAL_ROOT="$LOCAL" \
   SKILLS_STATE_DIR="$STATE" \
   SKILLS_CURATOR_RUNS_DIR="$RUNS" \
   SKILLS_LAUNCH_AGENTS_DIR="$PLISTS" \
   SKILLS_ALLOW_NO_SCHEDULED_JOBS=1 \
   SKILLS_LOCK_DIR="$STATE/writer-lock.sqlite" \
   SKILLS_CURATOR_RUN_ID="$RUN_ID" \
   SKILLS_CURATOR_RUNNER="$FAKE_RUNNER" \
     "$ARCHIVER" old-local >/dev/null 2>&1; then
  fail "archive completed despite injected completion failure"
fi
[[ ! -e "$LOCAL/old-local" ]] || fail "interrupted archive did not commit"
run_curator rollback --run "$RUN_ID"
[[ -e "$LOCAL/old-local/SKILL.md" ]] ||
  fail "interrupted committed archive was not recovered"
echo "PASS: interrupted committed archive is inferred and restored"

CASE="$TMP/rollback-guards"
init_fixture "$CASE"
cat > "$CASE/plan.json" <<'JSON'
{"operations":[{"kind":"commit","action":"create","root":"local","skill":"guarded","paths":["guarded/SKILL.md","guarded/.agent-created","guarded/.agent-created.json"]}]}
JSON
RUN_ID="$(run_curator begin --plan "$CASE/plan.json" --report "$CASE/report.md")"
OP="$(run_curator intent --run "$RUN_ID" --kind commit --root local \
  --action create --skill guarded \
  --paths guarded/SKILL.md guarded/.agent-created guarded/.agent-created.json)"
make_skill "$LOCAL" guarded
echo "skill-curator: create guarded" > "$CASE/message.txt"
echo "undeclared" > "$LOCAL/during-operation.txt"
echo "undeclared child" > "$LOCAL/guarded/unrelated.md"
if run_curator commit --run "$RUN_ID" --op "$OP" \
  --message-file "$CASE/message.txt" >/dev/null 2>&1; then
  fail "scoped commit accepted undeclared post-begin dirt"
fi
rm "$LOCAL/during-operation.txt"
rm "$LOCAL/guarded/unrelated.md"
run_curator commit --run "$RUN_ID" --op "$OP" --message-file "$CASE/message.txt" \
  >/dev/null
run_curator finish --run "$RUN_ID"
MANIFEST="$RUNS/$RUN_ID.json"
COMMIT="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["operations"][0]["commit"])' "$MANIFEST")"
INITIAL="$(git -C "$LOCAL" rev-parse "$COMMIT^")"

echo "unexpected" > "$LOCAL/intruder.txt"
if run_curator rollback --run "$RUN_ID" >/dev/null 2>&1; then
  fail "rollback accepted unexpected dirty state"
fi
rm "$LOCAL/intruder.txt"

python3 - "$MANIFEST" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path))
data["operations"][0]["commit"] = "0" * 40
json.dump(data, open(path, "w"), indent=2)
open(path, "a").write("\n")
PY
if run_curator rollback --run "$RUN_ID" >/dev/null 2>&1; then
  fail "rollback accepted a missing recorded commit"
fi
python3 - "$MANIFEST" "$COMMIT" <<'PY'
import json, sys
path, commit = sys.argv[1:]
data = json.load(open(path))
data["operations"][0]["commit"] = commit
json.dump(data, open(path, "w"), indent=2)
open(path, "a").write("\n")
PY

git -C "$LOCAL" reset --hard -q "$INITIAL"
if run_curator rollback --run "$RUN_ID" >/dev/null 2>&1; then
  fail "rollback accepted rewritten history"
fi
git -C "$LOCAL" reset --hard -q "$COMMIT"
run_curator rollback --run "$RUN_ID"
[[ ! -e "$LOCAL/guarded" ]] || fail "guarded create survived repaired rollback"
echo "PASS: dirty, missing-commit, and rewritten-history guards fail closed"

CASE="$TMP/state-guard"
init_fixture "$CASE"
cat > "$CASE/plan.json" <<'JSON'
{"operations":[{"kind":"archive","skill":"old-local"}]}
JSON
RUN_ID="$(run_curator begin --plan "$CASE/plan.json" --report "$CASE/report.md")"
archive_skill "$RUN_ID" old-local
run_curator finish --run "$RUN_ID"
echo '{"tampered":true}' > "$STATE/tombstones/old-local.json"
if run_curator rollback --run "$RUN_ID" >/dev/null 2>&1; then
  fail "rollback accepted a changed tombstone effect"
fi
python3 - "$RUNS/$RUN_ID.json" "$STATE/tombstones/old-local.json" <<'PY'
import base64, json, sys
manifest, target = sys.argv[1:]
data = json.load(open(manifest))
encoded = data["operations"][0]["effects_after"]["tombstone"]["bytes_b64"]
open(target, "wb").write(base64.b64decode(encoded))
PY
run_curator rollback --run "$RUN_ID"
[[ -e "$LOCAL/old-local/SKILL.md" ]] ||
  fail "state-guard archive was not restored after repair"
echo "PASS: changed retirement state blocks rollback"

CASE="$TMP/publication-failure"
init_fixture "$CASE"
cat > "$CASE/plan.json" <<'JSON'
{"operations":[{"kind":"archive","skill":"old-public"}]}
JSON
RUN_ID="$(run_curator begin --plan "$CASE/plan.json" --report "$CASE/report.md")"
archive_skill "$RUN_ID" old-public
cat > "$CASE/public-remote.git/hooks/pre-receive" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$CASE/public-remote.git/hooks/pre-receive"
if run_curator publish --run "$RUN_ID" >/dev/null 2>&1; then
  fail "rejected public push was reported as published"
fi
run_curator renew --run "$RUN_ID"
python3 - "$RUNS/$RUN_ID.json" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1]))
assert manifest["status"] == "publish_failed"
assert manifest["publication"]["status"] == "failed"
PY
rm "$CASE/public-remote.git/hooks/pre-receive"
run_curator rollback --run "$RUN_ID"
[[ -e "$PUBLIC/skills/old-public/SKILL.md" ]] ||
  fail "publish-failed rollback did not restore public source"
echo "PASS: rejected publication stays non-complete and rolls back"

CASE="$TMP/accepted-with-error"
init_fixture "$CASE"
cat > "$CASE/plan.json" <<'JSON'
{"operations":[{"kind":"archive","skill":"old-public"}]}
JSON
RUN_ID="$(run_curator begin --plan "$CASE/plan.json" --report "$CASE/report.md")"
archive_skill "$RUN_ID" old-public
REAL_GIT="$(command -v git)"
FAKEBIN="$CASE/fakebin"
mkdir -p "$FAKEBIN"
cat > "$FAKEBIN/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
is_push=0
for arg in "$@"; do
  [[ "$arg" == "push" ]] && is_push=1
done
if [[ "$is_push" == "1" && "${CURATOR_FAIL_AFTER_PUSH:-0}" == "1" ]]; then
  "$CURATOR_REAL_GIT" "$@"
  echo "simulated disconnect after accepted push" >&2
  exit 9
fi
exec "$CURATOR_REAL_GIT" "$@"
EOF
chmod +x "$FAKEBIN/git"
PATH="$FAKEBIN:$PATH" CURATOR_REAL_GIT="$REAL_GIT" CURATOR_FAIL_AFTER_PUSH=1 \
  run_curator publish --run "$RUN_ID"
python3 - "$RUNS/$RUN_ID.json" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1]))
assert manifest["status"] == "active"
assert manifest["publication"]["status"] == "published"
assert manifest["publication"]["recovered_after_failed_push"] is True
assert "simulated disconnect" in manifest["publication"]["push_error"]
PY
run_curator rollback --run "$RUN_ID"
[[ -e "$PUBLIC/skills/old-public/SKILL.md" ]] ||
  fail "accepted-with-error publication did not roll back"
echo "PASS: failed push reconciles the exact accepted remote identity"

CASE="$TMP/interrupted-publication"
init_fixture "$CASE"
cat > "$CASE/plan.json" <<'JSON'
{"operations":[{"kind":"archive","skill":"old-public"}]}
JSON
RUN_ID="$(run_curator begin --plan "$CASE/plan.json" --report "$CASE/report.md")"
archive_skill "$RUN_ID" old-public
run_curator publish --run "$RUN_ID"
python3 - "$RUNS/$RUN_ID.json" <<'PY'
import json, sys
path = sys.argv[1]
manifest = json.load(open(path))
manifest["publication"]["status"] = "publishing"
manifest["publication"].pop("remote_after", None)
manifest["publication"].pop("published_at", None)
json.dump(manifest, open(path, "w"), indent=2)
open(path, "a").write("\n")
PY
run_curator rollback --run "$RUN_ID"
[[ -e "$PUBLIC/skills/old-public/SKILL.md" ]] ||
  fail "interrupted-publication rollback did not restore public source"
[[ "$(git --git-dir="$CASE/public-remote.git" show main:skills/old-public/SKILL.md)" == *"# old-public"* ]] ||
  fail "interrupted-publication rollback left the remote ahead"
python3 - "$RUNS/$RUN_ID.json" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1]))
assert manifest["status"] == "rolled_back"
assert manifest["publication"]["status"] == "reverted"
assert manifest["publication"]["recovered_after_interruption"] is True
PY
echo "PASS: interrupted accepted publication is reconciled before rollback"

CASE="$TMP/rollback-remote-identity"
init_fixture "$CASE"
cat > "$CASE/plan.json" <<'JSON'
{"operations":[{"kind":"archive","skill":"old-public"}]}
JSON
RUN_ID="$(run_curator begin --plan "$CASE/plan.json" --report "$CASE/report.md")"
archive_skill "$RUN_ID" old-public
run_curator publish --run "$RUN_ID"
TRANSACTION_HEAD="$(git -C "$PUBLIC" rev-parse HEAD)"
git clone --bare -q "$CASE/public-remote.git" "$CASE/other-remote.git"
git -C "$PUBLIC" remote set-url origin "$CASE/other-remote.git"
if run_curator rollback --run "$RUN_ID" >/dev/null 2>&1; then
  fail "rollback accepted a changed recorded remote URL"
fi
[[ "$(git -C "$PUBLIC" rev-parse HEAD)" == "$TRANSACTION_HEAD" ]] ||
  fail "remote URL refusal happened after local reversal"
[[ ! -e "$PUBLIC/skills/old-public" ]] ||
  fail "remote URL refusal restored the source locally"
git -C "$PUBLIC" remote set-url origin "$CASE/public-remote.git"
git clone -q "$CASE/public-remote.git" "$CASE/remote-writer"
git -C "$CASE/remote-writer" config user.email test@example.com
git -C "$CASE/remote-writer" config user.name Test
echo "external advance" >> "$CASE/remote-writer/README.md"
git -C "$CASE/remote-writer" add README.md
git -C "$CASE/remote-writer" commit -qm "external advance"
git -C "$CASE/remote-writer" push -q origin main
if run_curator rollback --run "$RUN_ID" >/dev/null 2>&1; then
  fail "rollback accepted an advanced public remote"
fi
[[ "$(git -C "$PUBLIC" rev-parse HEAD)" == "$TRANSACTION_HEAD" ]] ||
  fail "remote-head refusal happened after local reversal"
git --git-dir="$CASE/public-remote.git" update-ref refs/heads/main "$TRANSACTION_HEAD"
run_curator rollback --run "$RUN_ID"
[[ -e "$PUBLIC/skills/old-public/SKILL.md" ]] ||
  fail "remote identity recovery did not restore the source"
echo "PASS: rollback verifies remote URL and head before local reversal"

CASE="$TMP/report-parser"
init_fixture "$CASE"
cat > "$CASE/report.md" <<'EOF'
# Curator fixture report

```yaml
consolidations: []
prunings:
  - name: old-local
    reason: Supported fixture pruning.
    evidence:
      basis: age-only
      created_at: 2025-01-01T00:00:00+00:00
      last_used_at: never
      completion_evidence: not-required-age-threshold
      reuse_assessment: no-reusable-content
      evaluation: not-required-no-merge-target
      tombstone_effect: permanent-name-family-block-acknowledged
keep:
  - name: old-public
manual_review: []
```
EOF
cat > "$CASE/plan.json" <<'JSON'
{"operations":[{"kind":"archive","skill":"old-public"}]}
JSON
if run_curator begin --autonomous --plan "$CASE/plan.json" \
  --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "unknown top-level report keys widened pruning authority"
fi
cat > "$CASE/report.md" <<'EOF'
# Curator fixture report

```yaml
consolidations: []
prunings:
  - name: old-local
    reason: Nested evidence fixture.
    evidence:
      basis: age-only
      created_at: 2025-01-01T00:00:00+00:00
      last_used_at: never
      completion_evidence: not-required-age-threshold
      reuse_assessment: no-reusable-content
      evaluation: not-required-no-merge-target
      tombstone_effect: permanent-name-family-block-acknowledged
      notes:
        - name: old-public
manual_review: []
```
EOF
if run_curator begin --autonomous --plan "$CASE/plan.json" \
  --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "nested report evidence widened pruning authority"
fi
cat > "$CASE/report.md" <<'EOF'
# Curator fixture report

```yaml
consolidations: []
prunings:
  - name: old-local
    reason: Missing evidence fixture.
manual_review: []
```
EOF
cat > "$CASE/plan.json" <<'JSON'
{"operations":[{"kind":"archive","skill":"old-local"}]}
JSON
if run_curator begin --autonomous --plan "$CASE/plan.json" \
  --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "autonomous pruning accepted unsupported report evidence"
fi
echo "PASS: report parsing and pruning evidence fail closed"

CASE="$TMP/autonomous-pruning"
init_fixture "$CASE"
write_pruning_report "$CASE/report.md" old-local
cat > "$CASE/plan.json" <<'JSON'
{"operations":[{"kind":"archive","skill":"old-local"}]}
JSON
RUN_ID="$(run_curator begin --autonomous --plan "$CASE/plan.json" --report "$CASE/report.md")"
RECEIPT="$RUNS/$RUN_ID.authorization.json"
[[ -f "$RECEIPT" ]] || fail "autonomous authorization receipt was not written"
[[ "$(stat -f '%Lp' "$RECEIPT")" == "400" ]] ||
  fail "autonomous authorization receipt is mutable"
python3 - "$RECEIPT" <<'PY'
import json, sys
receipt = json.load(open(sys.argv[1]))
skill = receipt["skills"][0]
assert skill["authority_class"] == "legacy_machine"
assert skill["provenance_basis"] == "current_envelope"
PY
REPORT_SHA="$(shasum -a 256 "$CASE/report.md" | awk '{print $1}')"
archive_skill "$RUN_ID" old-local
run_curator finish --run "$RUN_ID"
python3 - "$STATE/retired/old-local.json" "$REPORT_SHA" <<'PY'
import json, sys
record = json.load(open(sys.argv[1]))
assert record["curator_authority"] == "autonomous"
assert record["curator_report_sha256"] == sys.argv[2]
assert [item["kind"] for item in record["evidence_refs"]] == [
    "agent-created-marker",
    "agent-created-envelope",
    "skill-author-frontmatter",
]
assert all(len(item["sha256"]) == 64 for item in record["evidence_refs"])
assert record["evidence_refs"][1]["source_session_id"] == "fixture-session"
PY
restore_skill old-local
[[ -e "$LOCAL/old-local/SKILL.md" ]] ||
  fail "autonomously retired skill did not restore"
HISTORY="$(find "$STATE/retirement-history" -name 'old-local-*.json' -type f)"
[[ -n "$HISTORY" ]] || fail "restore discarded retirement evidence"
python3 - "$HISTORY" "$REPORT_SHA" <<'PY'
import json, sys
record = json.load(open(sys.argv[1]))
assert record["curator_report_sha256"] == sys.argv[2]
assert record["evidence_refs"][1]["kind"] == "agent-created-envelope"
assert len(record["restore_commit"]) == 40
PY
echo "PASS: autonomous pruning and restore retain report and provenance evidence"

CASE="$TMP/autonomous-protected"
init_fixture "$CASE"
write_pruning_report "$CASE/report.md" old-local
cat > "$CASE/plan.json" <<'JSON'
{"operations":[{"kind":"archive","skill":"old-local"}]}
JSON
rm "$LOCAL/old-local/.agent-created"
git -C "$LOCAL" add old-local/.agent-created
git -C "$LOCAL" commit -qm "remove marker without authorship proof"
if run_curator begin --autonomous --plan "$CASE/plan.json" \
  --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "autonomous authorization accepted missing-marker provenance"
fi
touch "$LOCAL/old-local/.agent-created"
git -C "$LOCAL" add old-local/.agent-created
git -C "$LOCAL" commit -qm "restore agent provenance"
cp "$LOCAL/old-local/.agent-created.json" "$CASE/provenance.json"
git -C "$LOCAL" rm -q old-local/.agent-created.json
git -C "$LOCAL" commit -qm "remove provenance envelope"
if run_curator begin --autonomous --plan "$CASE/plan.json" \
  --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "autonomous authorization accepted marker-only provenance"
fi
cp "$CASE/provenance.json" "$LOCAL/old-local/.agent-created.json"
git -C "$LOCAL" add old-local/.agent-created.json
git -C "$LOCAL" commit -qm "restore provenance envelope"
python3 - "$LOCAL/old-local/.agent-created.json" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path))
data["schema_version"] = 99
json.dump(data, open(path, "w"), indent=2)
open(path, "a").write("\n")
PY
git -C "$LOCAL" add old-local/.agent-created.json
git -C "$LOCAL" commit -qm "corrupt provenance envelope"
if run_curator begin --autonomous --plan "$CASE/plan.json" \
  --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "autonomous authorization accepted malformed provenance envelope"
fi
cp "$CASE/provenance.json" "$LOCAL/old-local/.agent-created.json"
git -C "$LOCAL" add old-local/.agent-created.json
git -C "$LOCAL" commit -qm "repair provenance envelope"
sed -i '' 's/author: skill-review/author: skill-create/' \
  "$LOCAL/old-local/SKILL.md"
git -C "$LOCAL" add old-local/SKILL.md
git -C "$LOCAL" commit -qm "mismatch author provenance"
if run_curator begin --autonomous --plan "$CASE/plan.json" \
  --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "autonomous authorization accepted mismatched author provenance"
fi
sed -i '' 's/author: skill-create/author: skill-review/' \
  "$LOCAL/old-local/SKILL.md"
git -C "$LOCAL" add old-local/SKILL.md
git -C "$LOCAL" commit -qm "repair author provenance"
printf '{"paused":true}\n' > "$CASE/curator.json"
if run_curator begin --autonomous --plan "$CASE/plan.json" \
  --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "autonomous authorization ignored curator pause"
fi
printf '{"paused":false}\n' > "$CASE/curator.json"
touch "$STATE/disable-daemon"
if run_curator begin --autonomous --plan "$CASE/plan.json" \
  --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "autonomous authorization ignored the halt switch"
fi
rm "$STATE/disable-daemon"
touch "$LOCAL/old-local/.pinned"
git -C "$LOCAL" add old-local/.pinned
git -C "$LOCAL" commit -qm "pin fixture"
if run_curator begin --autonomous --plan "$CASE/plan.json" \
  --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "autonomous authorization accepted an explicit pin"
fi
git -C "$LOCAL" rm -q old-local/.pinned
git -C "$LOCAL" commit -qm "unpin fixture"
PIN_SCANNER="$CASE/implicit-pin-scanner"
cat > "$PIN_SCANNER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
"$SCRIPT_DIR/scheduled-skill-deps.py" "\$@" |
  python3 -c 'import json,sys; data=json.load(sys.stdin); row=next(item for item in data["skills"] if item["name"]=="old-local"); row["implicit_pin"]=True; row["implicit_pin_sources"]=["fixture"]; print(json.dumps(data))'
EOF
chmod +x "$PIN_SCANNER"
if CURATOR_DEPENDENCY_SCANNER="$PIN_SCANNER" run_curator begin --autonomous \
  --plan "$CASE/plan.json" --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "autonomous authorization accepted an implicit pin"
fi
FAILED_SCANNER="$CASE/failed-scanner"
cat > "$FAILED_SCANNER" <<'EOF'
#!/usr/bin/env bash
echo "incomplete fixture inventory" >&2
exit 1
EOF
chmod +x "$FAILED_SCANNER"
if CURATOR_DEPENDENCY_SCANNER="$FAILED_SCANNER" run_curator begin --autonomous \
  --plan "$CASE/plan.json" --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "autonomous authorization accepted incomplete dependencies"
fi
touch -t 202001010000 "$CASE/report.md"
if run_curator begin --autonomous --plan "$CASE/plan.json" \
  --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "autonomous authorization accepted a stale report"
fi
touch "$CASE/report.md"
RUN_ID="$(run_curator begin --autonomous --plan "$CASE/plan.json" --report "$CASE/report.md")"
touch "$CASE/report.md"
if archive_skill "$RUN_ID" old-local 2>/dev/null; then
  fail "archive accepted a report timestamp changed after authorization"
fi
[[ -e "$LOCAL/old-local/SKILL.md" ]] ||
  fail "changed-timestamp refusal mutated the source"
run_curator rollback --run "$RUN_ID"
write_pruning_report "$CASE/report.md" old-local
RUN_ID="$(run_curator begin --autonomous --plan "$CASE/plan.json" --report "$CASE/report.md")"
echo "tampered after authorization" >> "$CASE/report.md"
if archive_skill "$RUN_ID" old-local 2>/dev/null; then
  fail "archive accepted a report digest changed after authorization"
fi
run_curator rollback --run "$RUN_ID"
write_pruning_report "$CASE/report.md" old-local
RUN_ID="$(run_curator begin --autonomous --plan "$CASE/plan.json" --report "$CASE/report.md")"
printf '{"paused":true}\n' > "$CASE/curator.json"
if archive_skill "$RUN_ID" old-local 2>/dev/null; then
  fail "archive ignored a mid-run curator pause"
fi
printf '{"paused":false}\n' > "$CASE/curator.json"
touch "$STATE/disable-daemon"
if archive_skill "$RUN_ID" old-local 2>/dev/null; then
  fail "archive ignored a mid-run halt switch"
fi
rm "$STATE/disable-daemon"
run_curator rollback --run "$RUN_ID"
echo "PASS: protected provenance, pause, halt, freshness, timestamp, and digest fail closed"

CASE="$TMP/receipt-write-failure"
init_fixture "$CASE"
write_pruning_report "$CASE/report.md" old-local
cat > "$CASE/plan.json" <<'JSON'
{"operations":[{"kind":"archive","skill":"old-local"}]}
JSON
mkdir -p "$RUNS"
printf '{}\n' > "$RUNS/receipt-collision.authorization.json"
chmod 400 "$RUNS/receipt-collision.authorization.json"
if run_curator begin --autonomous --run-id receipt-collision \
  --plan "$CASE/plan.json" --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "authorization receipt collision unexpectedly succeeded"
fi
RUN_ID="$(run_curator begin --autonomous --run-id after-receipt-failure \
  --plan "$CASE/plan.json" --report "$CASE/report.md")"
run_curator rollback --run "$RUN_ID"
echo "PASS: authorization receipt failure releases the writer lease"

CASE="$TMP/autonomous-consolidation"
init_fixture "$CASE"
cat > "$CASE/report.md" <<'EOF'
# Curator fixture report

```yaml
consolidations:
  - from: old-local
    into: new-local
    reason: Test-authorized consolidation.
prunings: []
manual_review: []
```
EOF
cat > "$CASE/plan.json" <<'JSON'
{
  "operations": [
    {
      "kind": "commit",
      "action": "create",
      "root": "local",
      "skill": "new-local",
      "sources": ["old-local"],
      "paths": ["new-local/SKILL.md", "new-local/.agent-created", "new-local/.agent-created.json"]
    },
    {
      "kind": "archive",
      "skill": "old-local",
      "absorbed_into": "new-local"
    }
  ]
}
JSON
RUN_ID="$(run_curator begin --autonomous --plan "$CASE/plan.json" --report "$CASE/report.md")"
if archive_skill "$RUN_ID" old-local --absorbed-into new-local 2>/dev/null; then
  fail "autonomous archive ran before its destination commit"
fi
OP="$(run_curator intent --run "$RUN_ID" --kind commit --root local \
  --action create --skill new-local \
  --paths new-local/SKILL.md new-local/.agent-created new-local/.agent-created.json)"
make_skill "$LOCAL" new-local
echo "skill-curator: create new-local" > "$CASE/message.txt"
run_curator commit --run "$RUN_ID" --op "$OP" \
  --message-file "$CASE/message.txt" >/dev/null
run_curator archive-context --run "$RUN_ID" --skill old-local >/dev/null
if archive_skill "$RUN_ID" old-local 2>/dev/null; then
  fail "autonomous archive accepted a missing declared replacement"
fi
run_curator rollback --run "$RUN_ID"
[[ -e "$LOCAL/old-local/SKILL.md" ]] ||
  fail "replacement-binding rollback did not restore source"
[[ ! -e "$LOCAL/new-local" ]] ||
  fail "replacement-binding rollback did not remove destination"
sed 's/"new-local"/"wrong-destination"/' "$CASE/plan.json" > "$CASE/bad-plan.json"
if run_curator begin --autonomous --plan "$CASE/bad-plan.json" \
  --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "autonomous authorization accepted a report/plan destination mismatch"
fi
make_skill "$LOCAL" handmade-destination
rm "$LOCAL/handmade-destination/.agent-created"
git -C "$LOCAL" add handmade-destination
git -C "$LOCAL" commit -qm "add hand-made destination"
cat > "$CASE/handmade-report.md" <<'EOF'
# Curator fixture report

```yaml
consolidations:
  - from: old-local
    into: handmade-destination
    reason: Protected destination fixture.
prunings: []
manual_review: []
```
EOF
cat > "$CASE/handmade-plan.json" <<'JSON'
{
  "operations": [
    {
      "kind": "commit",
      "action": "patch",
      "root": "local",
      "skill": "handmade-destination",
      "sources": ["old-local"],
      "paths": ["handmade-destination/SKILL.md"]
    },
    {
      "kind": "archive",
      "skill": "old-local",
      "absorbed_into": "handmade-destination"
    }
  ]
}
JSON
if run_curator begin --autonomous --plan "$CASE/handmade-plan.json" \
  --report "$CASE/handmade-report.md" >/dev/null 2>&1; then
  fail "autonomous authorization accepted a hand-made destination"
fi
cat > "$CASE/escaped-plan.json" <<'JSON'
{
  "operations": [
    {
      "kind": "commit",
      "action": "create",
      "root": "local",
      "skill": "new-local",
      "sources": ["old-local"],
      "paths": [
        "old-local/SKILL.md",
        "new-local/SKILL.md",
        "new-local/.agent-created",
        "new-local/.agent-created.json"
      ]
    },
    {
      "kind": "archive",
      "skill": "old-local",
      "absorbed_into": "new-local"
    }
  ]
}
JSON
if run_curator begin --autonomous --plan "$CASE/escaped-plan.json" \
  --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "autonomous destination commit accepted an escaped skill path"
fi
python3 - "$CASE/plan.json" "$CASE/archive-first-plan.json" <<'PY'
import json, sys
source, target = sys.argv[1:]
data = json.load(open(source))
data["operations"].reverse()
json.dump(data, open(target, "w"), indent=2)
open(target, "a").write("\n")
PY
if run_curator begin --autonomous --plan "$CASE/archive-first-plan.json" \
  --report "$CASE/report.md" >/dev/null 2>&1; then
  fail "autonomous authorization accepted archive-before-destination order"
fi
echo "PASS: autonomous consolidation binds paths, order, source, and destination"

CASE="$TMP/autonomous-multi-operation"
init_fixture "$CASE"
make_skill "$LOCAL" old-local-two
git -C "$LOCAL" add old-local-two
git -C "$LOCAL" commit -qm "add second pruning fixture"
cat > "$CASE/report.md" <<'EOF'
# Curator fixture report

```yaml
consolidations: []
prunings:
  - name: old-local
    reason: First supported pruning.
    evidence:
      basis: age-only
      created_at: 2025-01-01T00:00:00+00:00
      last_used_at: never
      completion_evidence: not-required-age-threshold
      reuse_assessment: no-reusable-content
      evaluation: not-required-no-merge-target
      tombstone_effect: permanent-name-family-block-acknowledged
  - name: old-local-two
    reason: Second supported pruning.
    evidence:
      basis: age-only
      created_at: 2025-01-01T00:00:00+00:00
      last_used_at: never
      completion_evidence: not-required-age-threshold
      reuse_assessment: no-reusable-content
      evaluation: not-required-no-merge-target
      tombstone_effect: permanent-name-family-block-acknowledged
manual_review: []
```
EOF
cat > "$CASE/plan.json" <<'JSON'
{
  "operations": [
    {"kind": "archive", "skill": "old-local"},
    {"kind": "archive", "skill": "old-local-two"}
  ]
}
JSON
RUN_ID="$(run_curator begin --autonomous --plan "$CASE/plan.json" --report "$CASE/report.md")"
archive_skill "$RUN_ID" old-local
archive_skill "$RUN_ID" old-local-two
run_curator finish --run "$RUN_ID"
run_curator rollback --run "$RUN_ID"
[[ -e "$LOCAL/old-local/SKILL.md" && -e "$LOCAL/old-local-two/SKILL.md" ]] ||
  fail "multi-operation autonomous rollback did not restore both sources"

RUN_ID="$(run_curator begin --autonomous --plan "$CASE/plan.json" --report "$CASE/report.md")"
archive_skill "$RUN_ID" old-local
make_skill "$LOCAL" external-drift
if archive_skill "$RUN_ID" old-local-two 2>/dev/null; then
  fail "autonomous revalidation accepted external inventory drift"
fi
rm -rf "$LOCAL/external-drift"
run_curator rollback --run "$RUN_ID"
echo "PASS: own inventory mutations revalidate while external drift is refused"

CASE="$TMP/ambiguous"
init_fixture "$CASE"
cat > "$CASE/plan.json" <<'JSON'
{"operations":[{"kind":"archive","skill":"old-local"}]}
JSON
if SKILLS_REPO_ROOT="$PUBLIC" \
   SKILLS_LOCAL_ROOT="$PUBLIC" \
   SKILLS_STATE_DIR="$STATE" \
   SKILLS_CURATOR_RUNS_DIR="$RUNS" \
   SKILLS_LAUNCH_AGENTS_DIR="$PLISTS" \
   SKILLS_ALLOW_NO_SCHEDULED_JOBS=1 \
   SKILLS_LOCK_DIR="$STATE/writer-lock.sqlite" \
     "$RUNNER" begin --plan "$CASE/plan.json" --report "$CASE/report.md" \
       >/dev/null 2>&1; then
  fail "ambiguous root identity was accepted"
fi
echo "PASS: ambiguous root identity fails closed"

echo "curator transaction tests: PASS"
