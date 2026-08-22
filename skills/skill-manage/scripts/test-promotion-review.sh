#!/usr/bin/env bash
# Deterministic checks for skill-manage promotion and retirement state.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/promotion-review.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
export TMPDIR="$TMP"
export DREAMING_REPO_ROOT="$REPO_ROOT"
export DREAMING_SHARED_SKILLS_ROOT="$TMP/shared"
export SKILLS_LAUNCH_AGENTS_DIR="$TMP/LaunchAgents"
mkdir -p "$SKILLS_LAUNCH_AGENTS_DIR"
for name in skill-create writing-great-skills dual-review authenticated-browse; do
  mkdir -p "$DREAMING_SHARED_SKILLS_ROOT/skills/$name"
  printf -- '---\nname: %s\ndescription: Shared test fixture dependency.\n---\n' "$name" \
    > "$DREAMING_SHARED_SKILLS_ROOT/skills/$name/SKILL.md"
done
passes=0

pass() { echo "PASS  $*"; passes=$((passes + 1)); }
fail() { echo "FAIL  $*" >&2; exit 1; }

FAKE_COPILOT="$TMP/copilot"
cat > "$FAKE_COPILOT" <<'SH'
#!/usr/bin/env bash
[[ "${1:-}" == "--version" ]] && { echo "copilot fixture 1.0"; exit 0; }
exit 1
SH
chmod +x "$FAKE_COPILOT"
export COPILOT_BIN="$FAKE_COPILOT"

make_skill() {
  local root="$1" name="$2"
  mkdir -p "$root/$name/references"
  cat > "$root/$name/SKILL.md" <<EOF
---
name: $name
description: Promotion fixture. Use when testing reviewed inventories.
---

# $name

Safe procedure.
EOF
  echo "Safe reference." > "$root/$name/references/example.md"
  touch "$root/$name/.agent-created"
  cat > "$root/$name/.agent-created.json" <<EOF
{
  "schema_version": 2,
  "skill": "$name",
  "created_by": "skill-review",
  "source_session_id": "fixture-session",
  "source_mode": "dispatch",
  "review_prompt_version": "skill-review-2",
  "created_at": "2026-01-01T00:00:00+00:00",
  "evidence": [{
    "task_key": "task:11111111-1111-1111-1111-111111111111",
    "session_id": "fixture-session",
    "observed_at": "2026-01-01T00:00:00+00:00",
    "independence": "verified",
    "evidence_kind": "successful-procedure",
    "summary": "Promotion fixture"
  }],
  "routing": {"destination": "skill", "reason": "Promotion fixture"},
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
  cat > "$root/$name/.skill-evaluation-cases.json" <<'JSON'
{
  "schema_version": 1,
  "source": {
    "task_id": "source:promotion-0001",
    "prompt": "Produce the source result.",
    "required_regex": [{"id": "right", "pattern": "\\bRIGHT\\b"}],
    "forbidden_regex": [],
    "friction_regex": []
  },
  "sibling": {
    "task_id": "sibling:promotion-0002",
    "prompt": "Produce the sibling result.",
    "required_regex": [{"id": "safe", "pattern": "\\bSAFE\\b"}],
    "forbidden_regex": [],
    "friction_regex": []
  }
}
JSON
  printf '{"schema_version":1,"fixture":"local-only-policy"}\n' \
    > "$root/$name/.skill-evaluation-policy.json"
}

approve_evaluation() {
  local skill="$1"
  local run="$TMP/eval-$(basename "$skill")"
  local plugin="$TMP/eval-plugin-$(basename "$skill")"
  rm -rf "$run"
  mkdir -p "$run"
  SKILLS_STATE_DIR="$TMP/state" \
    "$SCRIPT_DIR/../../skill-review/scripts/skill-evaluation.py" prepare "$skill" \
      --model gpt-5.4 --run-dir "$run" --plugin-dir "$plugin" >/dev/null
  python3 - "$run" "$(basename "$skill")" <<'PY'
import json, pathlib, sys
run, skill = pathlib.Path(sys.argv[1]), sys.argv[2]
def write(name, answer, load=False):
    events = []
    if load:
        events.append({"type": "tool.execution_start", "data": {"toolName": "skill", "arguments": {"skill": skill}}})
    events += [
        {"type": "assistant.message", "data": {"content": answer, "model": "gpt-5.4"}},
        {"type": "result", "exitCode": 0},
    ]
    (run / name).write_text("".join(json.dumps(e) + "\n" for e in events))
write("source-baseline.jsonl", "not yet")
write("source-candidate.jsonl", "RIGHT", True)
write("sibling-baseline.jsonl", "SAFE")
write("sibling-candidate.jsonl", "SAFE", True)
PY
  SKILLS_STATE_DIR="$TMP/state" \
    "$SCRIPT_DIR/../../skill-review/scripts/skill-evaluation.py" finalize \
      --run-dir "$run" >/dev/null
}

LOCAL="$TMP/local"
PUBLIC="$TMP/public"
mkdir -p "$LOCAL" "$PUBLIC/skills" "$PUBLIC/.claude-plugin" "$PUBLIC/.codex-plugin"
git -C "$LOCAL" init -q
git -C "$PUBLIC" init -q
for root in "$LOCAL" "$PUBLIC"; do
  git -C "$root" config user.email test@example.com
  git -C "$root" config user.name Test
  mkdir -p "$root/.githooks-fixture"
  git -C "$root" config core.hooksPath "$root/.githooks-fixture"
done
echo '{"name":"fixture","version":"0.1.0","skills":[]}' > "$PUBLIC/.claude-plugin/plugin.json"
echo '{"name":"fixture","metadata":{"version":"0.1.0"},"plugins":[{"name":"fixture","version":"0.1.0"}]}' \
  > "$PUBLIC/.claude-plugin/marketplace.json"
echo '{"name":"fixture","version":"0.1.0"}' > "$PUBLIC/.codex-plugin/plugin.json"
git -C "$PUBLIC" add .claude-plugin/plugin.json .claude-plugin/marketplace.json .codex-plugin/plugin.json
git -C "$PUBLIC" commit -qm base

make_skill "$LOCAL" private-skill
echo "PRIVATE_SENTINEL" >> "$LOCAL/private-skill/references/example.md"
if "$SCRIPT_DIR/promotion-review.py" approve "$LOCAL/private-skill" \
  --reviewer claude --reviewer gpt >/dev/null 2>&1; then
  fail "private support-file sentinel was approved"
fi
pass "private support-file sentinel blocks approval"

make_skill "$LOCAL" nested-sidecar
echo "PRIVATE_SENTINEL" > "$LOCAL/nested-sidecar/references/.promotion-reviewed.json"
if "$SCRIPT_DIR/promotion-review.py" approve "$LOCAL/nested-sidecar" \
  --reviewer claude --reviewer gpt >/dev/null 2>&1; then
  fail "nested reserved promotion sidecar was approved"
fi
pass "nested reserved sidecars fail closed"

make_skill "$LOCAL" safe-skill
if "$SCRIPT_DIR/promotion-review.py" approve "$LOCAL/safe-skill" \
  --reviewer same --reviewer same >/dev/null 2>&1; then
  fail "single reviewer identity was approved"
fi
"$SCRIPT_DIR/promotion-review.py" approve "$LOCAL/safe-skill" \
  --reviewer claude --reviewer gpt >/dev/null
"$SCRIPT_DIR/promotion-review.py" verify "$LOCAL/safe-skill" >/dev/null
echo "changed after review" >> "$LOCAL/safe-skill/references/example.md"
if "$SCRIPT_DIR/promotion-review.py" verify "$LOCAL/safe-skill" >/dev/null 2>&1; then
  fail "stale inventory passed"
fi
pass "promotion inventory requires two reviewers and exact hashes"

git -C "$LOCAL" add safe-skill
git -C "$LOCAL" commit -qm "add safe skill"
"$SCRIPT_DIR/promotion-review.py" approve "$LOCAL/safe-skill" \
  --reviewer claude --reviewer gpt >/dev/null
approve_evaluation "$LOCAL/safe-skill"
git -C "$LOCAL" add safe-skill/.promotion-reviewed.json
git -C "$LOCAL" commit -qm "approve safe skill"
SKILLS_LOCAL_ROOT="$LOCAL" SKILLS_REPO_ROOT="$PUBLIC" SKILLS_STATE_DIR="$TMP/state" \
  SKILLS_COAUTHOR_TRAILER="Reviewed-by: fixture" \
  "$SCRIPT_DIR/promote-skill.sh" safe-skill >/dev/null
[[ -f "$PUBLIC/skills/safe-skill/SKILL.md" ]] || fail "promoted skill missing"
if find "$PUBLIC/skills/safe-skill" -name '.agent-created*' -print -quit | grep -q .; then
  fail "public skill retained agent provenance"
fi
[[ ! -f "$PUBLIC/skills/safe-skill/.promotion-reviewed.json" ]] ||
  fail "public skill retained private review manifest"
[[ ! -f "$PUBLIC/skills/safe-skill/.skill-evaluation-policy.json" ]] ||
  fail "public skill retained private evaluation policy"
grep -q '"./skills/safe-skill"' "$PUBLIC/.claude-plugin/plugin.json" ||
  fail "promoted skill was not registered"
versions="$(python3 - "$PUBLIC" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
plugin = json.load(open(root / ".claude-plugin/plugin.json"))["version"]
market = json.load(open(root / ".claude-plugin/marketplace.json"))["metadata"]["version"]
codex = json.load(open(root / ".codex-plugin/plugin.json"))["version"]
print(f"{plugin}:{market}:{codex}")
PY
)"
[[ "$versions" == "0.2.0:0.2.0:0.2.0" ]] ||
  fail "promotion did not keep manifest versions aligned: $versions"
pass "promotion strips provenance and the local review manifest"

make_skill "$LOCAL" registry-failure-skill
git -C "$LOCAL" add registry-failure-skill
git -C "$LOCAL" commit -qm "add registry failure skill"
"$SCRIPT_DIR/promotion-review.py" approve "$LOCAL/registry-failure-skill" \
  --reviewer claude --reviewer gpt >/dev/null
approve_evaluation "$LOCAL/registry-failure-skill"
git -C "$LOCAL" add registry-failure-skill/.promotion-reviewed.json
git -C "$LOCAL" commit -qm "approve registry failure skill"
echo 'NOT JSON' > "$PUBLIC/.codex-plugin/plugin.json"
before_manifests="$(shasum -a 256 \
  "$PUBLIC/.claude-plugin/plugin.json" \
  "$PUBLIC/.claude-plugin/marketplace.json" \
  "$PUBLIC/.codex-plugin/plugin.json")"
if SKILLS_LOCAL_ROOT="$LOCAL" SKILLS_REPO_ROOT="$PUBLIC" SKILLS_STATE_DIR="$TMP/state" \
  SKILLS_COAUTHOR_TRAILER="Reviewed-by: fixture" \
  "$SCRIPT_DIR/promote-skill.sh" registry-failure-skill >/dev/null 2>&1; then
  fail "malformed registry manifest returned success"
fi
after_manifests="$(shasum -a 256 \
  "$PUBLIC/.claude-plugin/plugin.json" \
  "$PUBLIC/.claude-plugin/marketplace.json" \
  "$PUBLIC/.codex-plugin/plugin.json")"
[[ "$after_manifests" == "$before_manifests" ]] ||
  fail "registry failure changed public manifests"
[[ -f "$LOCAL/registry-failure-skill/.agent-created.json" ]] ||
  fail "registry failure did not restore local provenance"
[[ ! -e "$PUBLIC/skills/registry-failure-skill" ]] ||
  fail "registry failure left the skill in the public tree"
git -C "$PUBLIC" restore -- .codex-plugin/plugin.json
pass "registry failure restores every public manifest"

make_skill "$LOCAL" rollback-skill
git -C "$LOCAL" add rollback-skill
git -C "$LOCAL" commit -qm "add rollback skill"
"$SCRIPT_DIR/promotion-review.py" approve "$LOCAL/rollback-skill" \
  --reviewer claude --reviewer gpt >/dev/null
approve_evaluation "$LOCAL/rollback-skill"
git -C "$LOCAL" add rollback-skill/.promotion-reviewed.json
git -C "$LOCAL" commit -qm "approve rollback skill"
python3 - "$PUBLIC/.claude-plugin/plugin.json" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path))
data["staged_fixture"] = True
with open(path, "w") as handle:
    json.dump(data, handle, indent=2)
    handle.write("\n")
PY
git -C "$PUBLIC" add .claude-plugin/plugin.json
cached_before="$(git -C "$PUBLIC" diff --cached --binary -- \
  skills/rollback-skill \
  .claude-plugin/plugin.json \
  .claude-plugin/marketplace.json \
  .codex-plugin/plugin.json)"
cat > "$PUBLIC/.githooks-fixture/pre-commit" <<'SH'
#!/usr/bin/env bash
exit 1
SH
chmod +x "$PUBLIC/.githooks-fixture/pre-commit"
if SKILLS_LOCAL_ROOT="$LOCAL" SKILLS_REPO_ROOT="$PUBLIC" SKILLS_STATE_DIR="$TMP/state" \
  SKILLS_COAUTHOR_TRAILER="Reviewed-by: fixture" \
  "$SCRIPT_DIR/promote-skill.sh" rollback-skill >/dev/null 2>&1; then
  fail "forced public commit failure returned success"
fi
[[ -f "$LOCAL/rollback-skill/.agent-created" ]] ||
  fail "failed promotion lost authority marker"
[[ -f "$LOCAL/rollback-skill/.agent-created.json" ]] ||
  fail "failed promotion lost evidence envelope"
[[ -f "$LOCAL/rollback-skill/.promotion-reviewed.json" ]] ||
  fail "failed promotion lost review manifest"
[[ -f "$LOCAL/rollback-skill/.skill-evaluation-cases.json" ]] ||
  fail "failed promotion lost evaluation cases"
[[ -f "$LOCAL/rollback-skill/.skill-evaluation-policy.json" ]] ||
  fail "failed promotion lost evaluation policy"
[[ ! -e "$PUBLIC/skills/rollback-skill" ]] ||
  fail "failed promotion left the skill in the public tree"
cached_after="$(git -C "$PUBLIC" diff --cached --binary -- \
  skills/rollback-skill \
  .claude-plugin/plugin.json \
  .claude-plugin/marketplace.json \
  .codex-plugin/plugin.json)"
[[ "$cached_after" == "$cached_before" ]] ||
  fail "failed promotion did not restore the scoped staged diff"
pass "failed promotion restores local provenance and staged state"

CHECK_TOMBSTONE="$SCRIPT_DIR/../../skill-review/scripts/check-tombstone.sh"
TOMB_STATE="$TMP/tombstone-state"
mkdir -p "$TOMB_STATE/review/tombstones" "$TOMB_STATE/legacy/tombstones"
printf '{"skill":"canonical-skill","reason":"pruned"}\n' \
  > "$TOMB_STATE/review/tombstones/canonical-skill.json"
SKILLS_REVIEW_STATE_DIR="$TOMB_STATE/review" \
  SKILLS_STATE_DIR="$TOMB_STATE/legacy" \
  "$CHECK_TOMBSTONE" canonical-skill >/dev/null ||
  fail "canonical review-state tombstone was not enforced"
rm "$TOMB_STATE/review/tombstones/canonical-skill.json"
printf '{"skill":"legacy-skill","reason":"pruned"}\n' \
  > "$TOMB_STATE/legacy/tombstones/legacy-skill.json"
SKILLS_REVIEW_STATE_DIR="$TOMB_STATE/review" \
  SKILLS_STATE_DIR="$TOMB_STATE/legacy" \
  "$CHECK_TOMBSTONE" legacy-skill >/dev/null ||
  fail "pre-migration tombstone was not enforced"
mkdir -p "$TOMB_STATE/custom-review/tombstones"
printf '{"skill":"custom-skill","reason":"pruned"}\n' \
  > "$TOMB_STATE/custom-review/tombstones/custom-skill.json"
SKILLS_REVIEW_STATE_DIR= SKILLS_STATE_DIR="$TOMB_STATE/custom-review" \
  "$CHECK_TOMBSTONE" custom-skill >/dev/null ||
  fail "arbitrary legacy SKILLS_STATE_DIR override was reinterpreted"
printf '{malformed\n' > "$TOMB_STATE/review/tombstones/ambiguous-skill.json"
set +e
SKILLS_REVIEW_STATE_DIR="$TOMB_STATE/review" \
  SKILLS_STATE_DIR="$TOMB_STATE/legacy" \
  "$CHECK_TOMBSTONE" ambiguous-skill >/dev/null 2>&1
tombstone_status=$?
set -e
[[ "$tombstone_status" -eq 2 ]] ||
  fail "ambiguous tombstone state failed open with status $tombstone_status"
mkdir -p "$TOMB_STATE/dangling"
ln -s "$TOMB_STATE/missing-tombstones" "$TOMB_STATE/dangling/tombstones"
set +e
SKILLS_REVIEW_STATE_DIR="$TOMB_STATE/dangling" SKILLS_STATE_DIR= \
  "$CHECK_TOMBSTONE" absent-skill >/dev/null 2>&1
tombstone_status=$?
set -e
[[ "$tombstone_status" -eq 2 ]] ||
  fail "dangling tombstone state failed open with status $tombstone_status"
pass "tombstone lookup shares canonical state and fails closed"

init_retirement_roots() {
  local fixture="$1"
  RETIRE_LOCAL="$fixture/local"
  RETIRE_PUBLIC="$fixture/public"
  mkdir -p "$RETIRE_LOCAL" "$RETIRE_PUBLIC/skills"
  git -C "$RETIRE_LOCAL" init -q
  git -C "$RETIRE_PUBLIC" init -q
  for root in "$RETIRE_LOCAL" "$RETIRE_PUBLIC"; do
    git -C "$root" config user.email test@example.com
    git -C "$root" config user.name Test
    git -C "$root" commit --allow-empty -qm base
  done
}

commit_then_delete_skill() {
  local root="$1" name="$2"
  mkdir -p "$root/$name"
  printf -- '---\nname: %s\ndescription: Retirement fixture.\n---\n' "$name" \
    > "$root/$name/SKILL.md"
  git -C "$root" add "$name"
  git -C "$root" commit -qm "add $name"
  RETIRE_RESTORE_SHA="$(git -C "$root" rev-parse HEAD)"
  git -C "$root" rm -qr "$name"
  git -C "$root" commit -qm "delete $name"
  RETIRE_DELETE_SHA="$(git -C "$root" rev-parse HEAD)"
}

ARCHIVE_FIXTURE="$TMP/archive-retirement"
init_retirement_roots "$ARCHIVE_FIXTURE"
make_skill "$RETIRE_LOCAL" archived-skill
git -C "$RETIRE_LOCAL" add archived-skill
git -C "$RETIRE_LOCAL" commit -qm "add archived skill"
ARCHIVE_RESTORE_SHA="$(git -C "$RETIRE_LOCAL" rev-parse HEAD)"
ARCHIVE_STATE="$ARCHIVE_FIXTURE/custom-review"
SKILLS_REVIEW_STATE_DIR= \
  SKILLS_STATE_DIR="$ARCHIVE_STATE" \
  SKILLS_LOCAL_ROOT="$RETIRE_LOCAL" \
  SKILLS_REPO_ROOT="$RETIRE_PUBLIC" \
  SKILLS_ALLOW_NO_SCHEDULED_JOBS=1 \
  SKILLS_COAUTHOR_TRAILER="Reviewed-by: fixture" \
  "$SCRIPT_DIR/archive-skill.sh" archived-skill >/dev/null
[[ -f "$ARCHIVE_STATE/retired/archived-skill.json" ]] ||
  fail "archive did not preserve arbitrary SKILLS_STATE_DIR semantics"
[[ -f "$ARCHIVE_STATE/tombstones/archived-skill.json" ]] ||
  fail "archive did not write the matching tombstone path"
[[ ! -e "$ARCHIVE_STATE/skill-review" ]] ||
  fail "archive appended skill-review to a legacy override"
SKILLS_REVIEW_STATE_DIR= \
  SKILLS_STATE_DIR="$ARCHIVE_STATE" \
  SKILLS_LOCAL_ROOT="$RETIRE_LOCAL" \
  SKILLS_REPO_ROOT="$RETIRE_PUBLIC" \
  SKILLS_COAUTHOR_TRAILER="Reviewed-by: fixture" \
  "$SCRIPT_DIR/restore-skill.sh" archived-skill >/dev/null
[[ -f "$RETIRE_LOCAL/archived-skill/SKILL.md" ]] ||
  fail "archived skill was not restored"
[[ ! -e "$ARCHIVE_STATE/retired/archived-skill.json" ]] ||
  fail "active retirement record survived restore"
ARCHIVE_HISTORY="$(find "$ARCHIVE_STATE/retirement-history" \
  -name 'archived-skill-*.json' -type f)"
[[ -n "$ARCHIVE_HISTORY" ]] ||
  fail "retirement record was not moved into history"
python3 - "$ARCHIVE_HISTORY" "$ARCHIVE_RESTORE_SHA" <<'PY'
import json, sys
record = json.load(open(sys.argv[1]))
assert record["restore_sha"] == sys.argv[2]
assert record["record_source"] == "record"
assert record["restore_commit"]
PY
pass "archive and atomic restore preserve legacy override evidence"

LEGACY_FIXTURE="$TMP/legacy-retirement"
init_retirement_roots "$LEGACY_FIXTURE"
commit_then_delete_skill "$RETIRE_LOCAL" legacy-record-skill
LEGACY_STATE="$LEGACY_FIXTURE/state"
CANONICAL_STATE="$LEGACY_STATE/skill-review"
mkdir -p "$LEGACY_STATE/retired" "$LEGACY_STATE/tombstones" \
  "$CANONICAL_STATE/tombstones"
python3 - "$LEGACY_STATE/retired/legacy-record-skill.json" \
  "$RETIRE_LOCAL" "$RETIRE_RESTORE_SHA" <<'PY'
import json, sys
path, root, sha = sys.argv[1:]
json.dump({
    "skill": "legacy-record-skill",
    "path": "legacy-record-skill",
    "dest": "legacy-record-skill",
    "git_root": root,
    "restore_sha": sha,
    "retired_at": "2026-01-01T00:00:00+00:00",
    "reason": "pruned",
    "replacement": None,
    "curator_report_sha256": "legacy-evidence",
}, open(path, "w"), indent=2)
open(path, "a").write("\n")
PY
printf '{"skill":"legacy-record-skill"}\n' \
  > "$LEGACY_STATE/tombstones/legacy-record-skill.json"
printf '{"skill":"legacy-record-skill"}\n' \
  > "$CANONICAL_STATE/tombstones/legacy-record-skill.json"
SKILLS_REVIEW_STATE_DIR="$CANONICAL_STATE" \
  SKILLS_STATE_DIR="$LEGACY_STATE" \
  SKILLS_LOCAL_ROOT="$RETIRE_LOCAL" \
  SKILLS_REPO_ROOT="$RETIRE_PUBLIC" \
  SKILLS_COAUTHOR_TRAILER="Reviewed-by: fixture" \
  "$SCRIPT_DIR/restore-skill.sh" legacy-record-skill \
  >"$LEGACY_FIXTURE/restore.out" 2>"$LEGACY_FIXTURE/restore.err"
[[ -f "$RETIRE_LOCAL/legacy-record-skill/SKILL.md" ]] ||
  fail "legacy retirement record was not restored"
[[ ! -e "$LEGACY_STATE/retired/legacy-record-skill.json" ]] ||
  fail "legacy retirement record survived restore"
[[ ! -e "$LEGACY_STATE/tombstones/legacy-record-skill.json" &&
   ! -e "$CANONICAL_STATE/tombstones/legacy-record-skill.json" ]] ||
  fail "restore did not clear canonical and legacy tombstones"
LEGACY_HISTORY="$(find "$LEGACY_STATE/retirement-history" \
  -name 'legacy-record-skill-*.json' -type f)"
[[ -n "$LEGACY_HISTORY" ]] ||
  fail "legacy record evidence was not retained in history"
python3 - "$LEGACY_HISTORY" <<'PY'
import json, sys
record = json.load(open(sys.argv[1]))
assert record["curator_report_sha256"] == "legacy-evidence"
assert record["record_source"] == "legacy-record"
PY
grep -q "legacy retirement record:" "$LEGACY_FIXTURE/restore.err" ||
  fail "legacy record migration was not visible"
pass "legacy records remain visible and retain provenance"

HISTORY_FIXTURE="$TMP/history-only-retirement"
init_retirement_roots "$HISTORY_FIXTURE"
commit_then_delete_skill "$RETIRE_LOCAL" history-only-skill
HISTORY_STATE="$HISTORY_FIXTURE/state"
SKILLS_REVIEW_STATE_DIR="$HISTORY_STATE/skill-review" \
  SKILLS_STATE_DIR="$HISTORY_STATE" \
  SKILLS_LOCAL_ROOT="$RETIRE_LOCAL" \
  SKILLS_REPO_ROOT="$RETIRE_PUBLIC" \
  SKILLS_COAUTHOR_TRAILER="Reviewed-by: fixture" \
  "$SCRIPT_DIR/restore-skill.sh" history-only-skill >/dev/null
HISTORY_ONLY_RECORD="$(find "$HISTORY_STATE/skill-review/retirement-history" \
  -name 'history-only-skill-*.json' -type f)"
[[ -n "$HISTORY_ONLY_RECORD" ]] ||
  fail "history-only restore did not create lifecycle history"
python3 - "$HISTORY_ONLY_RECORD" "$RETIRE_DELETE_SHA" <<'PY'
import json, sys
record = json.load(open(sys.argv[1]))
assert record["record_source"] == "git-history"
assert record["delete_commit"] == sys.argv[2]
assert record["restore_commit"]
PY
pass "history-only restores create durable lifecycle records"

ROLLBACK_FIXTURE="$TMP/rollback-retirement"
init_retirement_roots "$ROLLBACK_FIXTURE"
make_skill "$RETIRE_LOCAL" rollback-retired-skill
git -C "$RETIRE_LOCAL" add rollback-retired-skill
git -C "$RETIRE_LOCAL" commit -qm "add rollback retired skill"
ROLLBACK_SHA="$(git -C "$RETIRE_LOCAL" rev-parse HEAD)"
ROLLBACK_STATE="$ROLLBACK_FIXTURE/state"
SKILLS_REVIEW_STATE_DIR="$ROLLBACK_STATE/skill-review" \
  SKILLS_STATE_DIR="$ROLLBACK_STATE" \
  SKILLS_LOCAL_ROOT="$RETIRE_LOCAL" \
  SKILLS_REPO_ROOT="$RETIRE_PUBLIC" \
  SKILLS_ALLOW_NO_SCHEDULED_JOBS=1 \
  SKILLS_COAUTHOR_TRAILER="Reviewed-by: fixture" \
  "$SCRIPT_DIR/archive-skill.sh" rollback-retired-skill >/dev/null
SKILLS_REVIEW_STATE_DIR="$ROLLBACK_STATE/skill-review" \
  SKILLS_STATE_DIR="$ROLLBACK_STATE" \
  "$CHECK_TOMBSTONE" rollback-retired-skill >/dev/null ||
  fail "archive writer and tombstone guard disagreed on canonical state"
SKILLS_REVIEW_STATE_DIR="$ROLLBACK_STATE/skill-review" \
  SKILLS_STATE_DIR="$ROLLBACK_STATE" \
  SKILLS_LOCAL_ROOT="$RETIRE_LOCAL" \
  SKILLS_REPO_ROOT="$RETIRE_PUBLIC" \
  SKILLS_CURATOR_ROLLBACK=fixture \
  SKILLS_RESTORE_GIT_ROOT="$RETIRE_LOCAL" \
  SKILLS_RESTORE_SRC_REL="rollback-retired-skill" \
  SKILLS_RESTORE_SHA="$ROLLBACK_SHA" \
  SKILLS_COAUTHOR_TRAILER="Reviewed-by: fixture" \
  "$SCRIPT_DIR/restore-skill.sh" rollback-retired-skill >/dev/null
[[ -f "$RETIRE_LOCAL/rollback-retired-skill/SKILL.md" ]] ||
  fail "transaction rollback did not restore the skill"
[[ ! -e "$ROLLBACK_STATE/skill-review/retired/rollback-retired-skill.json" ]] ||
  fail "transaction rollback retained an active retirement record"
[[ ! -d "$ROLLBACK_STATE/skill-review/retirement-history" ]] ||
  fail "transaction rollback created user-facing retirement history"
pass "transaction rollback semantics remain unchanged"

echo "PASS  $passes deterministic skill-manage checks"
