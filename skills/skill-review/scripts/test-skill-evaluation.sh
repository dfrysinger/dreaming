#!/usr/bin/env bash
# Deterministic M2 checks for source/sibling evaluation and stale gates.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/skill-evaluation.XXXXXX")"
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
passes=0

pass() { echo "PASS  $*"; passes=$((passes + 1)); }
fail() { echo "FAIL  $*" >&2; exit 1; }

export SKILLS_STATE_DIR="$TMP/state"
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
  mkdir -p "$root/$name"
  cat > "$root/$name/SKILL.md" <<EOF
---
name: $name
description: Use when a fixture asks for the exact source or sibling outcome.
---

# $name

Return RIGHT for the source task. Return SAFE for the sibling task.
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
  "created_at": "2026-01-01T00:00:00+00:00",
  "evidence": [{
    "task_key": "task:11111111-1111-1111-1111-111111111111",
    "session_id": "fixture-session",
    "observed_at": "2026-01-01T00:00:00+00:00",
    "independence": "verified",
    "evidence_kind": "successful-procedure",
    "summary": "Evaluation fixture"
  }],
  "routing": {"destination": "skill", "reason": "Reusable fixture"},
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
    "task_id": "source:fixture-0001",
    "prompt": "Produce the source outcome.",
    "required_regex": [{"id": "right", "pattern": "\\bRIGHT\\b"}],
    "forbidden_regex": [{"id": "harm", "pattern": "\\bHARM\\b"}],
    "friction_regex": [{"id": "redundant", "pattern": "\\bREDUNDANT\\b"}]
  },
  "sibling": {
    "task_id": "sibling:fixture-0002",
    "prompt": "Produce the sibling outcome.",
    "required_regex": [{"id": "safe", "pattern": "\\bSAFE\\b"}],
    "forbidden_regex": [{"id": "bad", "pattern": "\\bBAD\\b"}],
    "friction_regex": []
  }
}
JSON
}

write_log() {
  local path="$1" answer="$2" skill="${3:-}"
  : > "$path"
  if [[ -n "$skill" ]]; then
    printf '{"type":"tool.execution_start","data":{"toolName":"skill","arguments":{"skill":"%s"}}}\n' \
      "$skill" >> "$path"
  fi
  python3 - "$path" "$answer" <<'PY'
import json, sys
path, answer = sys.argv[1:3]
with open(path, "a") as handle:
    handle.write(json.dumps({"type": "assistant.message", "data": {"content": answer, "model": "gpt-5.4"}}) + "\n")
    handle.write(json.dumps({"type": "result", "exitCode": 0}) + "\n")
PY
}

make_run() {
  local skill_dir="$1" sibling_answer="${2:-SAFE}" load_source="${3:-yes}"
  local source_baseline="${4:-REDUNDANT}"
  local run_dir="$TMP/run-$(basename "$skill_dir")-$(date +%s%N)"
  local plugin_dir="$TMP/plugin-$(basename "$skill_dir")-$(date +%s%N)"
  mkdir -p "$run_dir"
  "$SCRIPT_DIR/skill-evaluation.py" prepare "$skill_dir" \
    --model gpt-5.4 --run-dir "$run_dir" --plugin-dir "$plugin_dir" >/dev/null
  write_log "$run_dir/source-baseline.jsonl" "$source_baseline"
  if [[ "$load_source" == "yes" ]]; then
    write_log "$run_dir/source-candidate.jsonl" "RIGHT" "$(basename "$skill_dir")"
  else
    write_log "$run_dir/source-candidate.jsonl" "RIGHT"
  fi
  write_log "$run_dir/sibling-baseline.jsonl" "SAFE"
  write_log "$run_dir/sibling-candidate.jsonl" "$sibling_answer" "$(basename "$skill_dir")"
  "$SCRIPT_DIR/skill-evaluation.py" finalize --run-dir "$run_dir"
}

ROOT="$TMP/skills"
make_skill "$ROOT" helpful-skill
result="$(make_run "$ROOT/helpful-skill")"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$result")" == "pass" ]] ||
  fail "helpful candidate did not pass"
"$SCRIPT_DIR/skill-evaluation.py" gate "$ROOT/helpful-skill" >/dev/null
grep -q '"status": "pass"' "$ROOT/helpful-skill/.agent-created.json" ||
  fail "evidence envelope did not mirror evaluation"
pass "helpful source improvement with preserved sibling passes"

echo "Changed runtime behavior." >> "$ROOT/helpful-skill/SKILL.md"
if "$SCRIPT_DIR/skill-evaluation.py" gate "$ROOT/helpful-skill" >/dev/null 2>&1; then
  fail "stale candidate receipt passed"
fi
pass "candidate edits invalidate the gate"

make_skill "$ROOT" overfit-skill
result="$(make_run "$ROOT/overfit-skill" "BAD")"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$result")" == "regression" ]] ||
  fail "overfitted candidate was not rejected"
if "$SCRIPT_DIR/skill-evaluation.py" gate "$ROOT/overfit-skill" >/dev/null 2>&1; then
  fail "regression receipt passed the gate"
fi
pass "sibling regression rejects an overfitted skill"

make_skill "$ROOT" unloaded-skill
result="$(make_run "$ROOT/unloaded-skill" "SAFE" "no")"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$result")" == "inconclusive" ]] ||
  fail "unloaded candidate did not become inconclusive"
pass "candidate run must actually load the skill"

make_skill "$ROOT" marginal-skill
result="$(make_run "$ROOT/marginal-skill" "SAFE" "yes" "RIGHT REDUNDANT")"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$result")" == "inconclusive" ]] ||
  fail "single-sample friction delta was treated as a pass"
pass "friction-only improvement remains inconclusive"

make_skill "$ROOT" symlink-skill
ln -s /tmp "$ROOT/symlink-skill/references"
if "$SCRIPT_DIR/skill-evaluation.py" prepare "$ROOT/symlink-skill" \
  --model gpt-5.4 --run-dir "$TMP/symlink-run" \
  --plugin-dir "$TMP/symlink-plugin" >/dev/null 2>&1; then
  fail "symlinked runtime input passed"
fi
pass "runtime inventory rejects symlinks"

make_skill "$ROOT" duplicate-case
python3 - "$ROOT/duplicate-case/.skill-evaluation-cases.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["sibling"]["task_id"] = d["source"]["task_id"]
json.dump(d, open(p, "w"))
PY
if "$SCRIPT_DIR/skill-evaluation.py" prepare "$ROOT/duplicate-case" \
  --model gpt-5.4 --run-dir "$TMP/duplicate-run" \
  --plugin-dir "$TMP/duplicate-plugin" >/dev/null 2>&1; then
  fail "duplicate source/sibling identity passed"
fi
pass "source and sibling tasks must be distinct"

make_skill "$ROOT" waiver-skill
mkdir -p "$ROOT/waiver-skill/scripts"
cat > "$ROOT/waiver-skill/scripts/helper.sh" <<'SH'
#!/usr/bin/env bash
exit 0
SH
cat > "$ROOT/waiver-skill/scripts/test-helper.sh" <<'SH'
#!/usr/bin/env bash
bash scripts/helper.sh
SH
chmod +x "$ROOT/waiver-skill/scripts/"*.sh
if "$SCRIPT_DIR/skill-evaluation.py" waive "$ROOT/waiver-skill" \
  --base-receipt "$TMP/missing-pass-receipt.json" --waiver-class deterministic-helper \
  --reason "No passing evaluation exists" --test-script scripts/test-helper.sh >/dev/null 2>&1; then
  fail "unevaluated skill received a waiver"
fi
pass "waiver requires an anchored passing evaluation"

git_root="$TMP/helper-root"
make_skill "$git_root" helper-skill
mkdir -p "$git_root/helper-skill/scripts"
cat > "$git_root/helper-skill/scripts/helper.sh" <<'SH'
#!/usr/bin/env bash
exit 1
SH
cat > "$git_root/helper-skill/scripts/test-helper.sh" <<'SH'
#!/usr/bin/env bash
set -e
bash scripts/helper.sh
sha="$(shasum -a 256 scripts/helper.sh | awk '{print $1}')"
printf '{"status":"pass","verified_files":{"scripts/helper.sh":"%s"}}\n' "$sha"
SH
chmod +x "$git_root/helper-skill/scripts/helper.sh" \
  "$git_root/helper-skill/scripts/test-helper.sh"
base_result="$(make_run "$git_root/helper-skill")"
base_receipt="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt"])' <<<"$base_result")"
cat > "$git_root/helper-skill/scripts/helper.sh" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$git_root/helper-skill/scripts/helper.sh"
result="$("$SCRIPT_DIR/skill-evaluation.py" waive "$git_root/helper-skill" \
  --base-receipt "$base_receipt" --waiver-class deterministic-helper \
  --reason "Exact helper behavior is covered by its executable check" \
  --test-script scripts/test-helper.sh)"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$result")" == "waived" ]] ||
  fail "deterministic helper waiver failed"
"$SCRIPT_DIR/skill-evaluation.py" gate "$git_root/helper-skill" >/dev/null
pass "narrow tested helper change may be waived"

make_skill "$ROOT" skill-md-waiver
mkdir -p "$ROOT/skill-md-waiver/scripts"
cat > "$ROOT/skill-md-waiver/scripts/test-helper.sh" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$ROOT/skill-md-waiver/scripts/test-helper.sh"
base_result="$(make_run "$ROOT/skill-md-waiver")"
base_receipt="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt"])' <<<"$base_result")"
echo "Behavior rewrite." >> "$ROOT/skill-md-waiver/SKILL.md"
if "$SCRIPT_DIR/skill-evaluation.py" waive "$ROOT/skill-md-waiver" \
  --base-receipt "$base_receipt" --waiver-class deterministic-helper \
  --reason "Not actually a helper" --test-script scripts/test-helper.sh >/dev/null 2>&1; then
  fail "SKILL.md rewrite received a waiver"
fi
pass "SKILL.md behavior changes cannot be waived"

ARCHIVE_ROOT="$TMP/archive-root"
mkdir -p "$ARCHIVE_ROOT" "$TMP/no-public/skills"
git -C "$ARCHIVE_ROOT" init -q
git -C "$ARCHIVE_ROOT" config user.email test@example.com
git -C "$ARCHIVE_ROOT" config user.name Test
make_skill "$ARCHIVE_ROOT" source-skill
make_skill "$ARCHIVE_ROOT" umbrella-skill
git -C "$ARCHIVE_ROOT" add .
git -C "$ARCHIVE_ROOT" commit -qm base
if SKILLS_LOCAL_ROOT="$ARCHIVE_ROOT" SKILLS_REPO_ROOT="$TMP/no-public" \
  SKILLS_ALLOW_NO_SCHEDULED_JOBS=1 \
  "$SCRIPT_DIR/../../skill-manage/scripts/archive-skill.sh" source-skill \
    --absorbed-into umbrella-skill >/dev/null 2>&1; then
  fail "consolidation archive bypassed the evaluation gate"
fi
make_run "$ARCHIVE_ROOT/umbrella-skill" >/dev/null
SKILLS_LOCAL_ROOT="$ARCHIVE_ROOT" SKILLS_REPO_ROOT="$TMP/no-public" \
  SKILLS_ALLOW_NO_SCHEDULED_JOBS=1 \
  SKILLS_COAUTHOR_TRAILER="Reviewed-by: fixture" \
  "$SCRIPT_DIR/../../skill-manage/scripts/archive-skill.sh" source-skill \
    --absorbed-into umbrella-skill >/dev/null
[[ ! -e "$ARCHIVE_ROOT/source-skill" ]] || fail "evaluated consolidation did not archive source"
pass "consolidation archive enforces the destination evaluation gate"

PUBLIC_ROOT="$TMP/cross-public"
LOCAL_ROOT="$TMP/cross-local"
mkdir -p "$PUBLIC_ROOT/skills" "$PUBLIC_ROOT/.claude-plugin" "$PUBLIC_ROOT/.codex-plugin" "$LOCAL_ROOT"
git -C "$PUBLIC_ROOT" init -q
git -C "$LOCAL_ROOT" init -q
for root in "$PUBLIC_ROOT" "$LOCAL_ROOT"; do
  git -C "$root" config user.email test@example.com
  git -C "$root" config user.name Test
done
echo '{"name":"fixture","version":"0.1.0","skills":["./skills/public-source"]}' > "$PUBLIC_ROOT/.claude-plugin/plugin.json"
echo '{"name":"fixture","metadata":{"version":"0.1.0"},"plugins":[{"name":"fixture","version":"0.1.0"}]}' > "$PUBLIC_ROOT/.claude-plugin/marketplace.json"
echo '{"name":"fixture","version":"0.1.0"}' > "$PUBLIC_ROOT/.codex-plugin/plugin.json"
make_skill "$PUBLIC_ROOT/skills" public-source
make_skill "$LOCAL_ROOT" local-umbrella
git -C "$PUBLIC_ROOT" add . && git -C "$PUBLIC_ROOT" commit -qm base
git -C "$LOCAL_ROOT" add . && git -C "$LOCAL_ROOT" commit -qm base
make_run "$LOCAL_ROOT/local-umbrella" >/dev/null
if SKILLS_REPO_ROOT="$PUBLIC_ROOT" SKILLS_LOCAL_ROOT="$LOCAL_ROOT" \
  SKILLS_ALLOW_NO_SCHEDULED_JOBS=1 \
  "$SCRIPT_DIR/../../skill-manage/scripts/archive-skill.sh" public-source \
    --absorbed-into local-umbrella >/dev/null 2>&1; then
  fail "public source accepted a local-only replacement"
fi
pass "consolidation replacement must remain in the source root"

SHADOW_HARNESS="$SCRIPT_DIR/skill-evaluation-harness.py"
SHADOW_ADAPTER="$SCRIPT_DIR/fake-skill-evaluation-adapter.py"

make_shadow_fixture() {
  local root="$1" fixture="${2:-correct}" target="${3:-none}" mode="${4:-catalog_plus_candidate}" backend="${5:-true}"
  mkdir -p "$root/candidate" "$root/catalog/approved-skill"
  cat > "$root/candidate/SKILL.md" <<'EOF'
---
name: fixture-candidate
---
Return the deterministic fixture result.
EOF
  cat > "$root/catalog/approved-skill/SKILL.md" <<'EOF'
---
name: approved-skill
---
Return the approved deterministic fixture result.
EOF
  python3 - "$root" "$fixture" "$target" "$mode" "$backend" "$SHADOW_ADAPTER" <<'PY'
import hashlib, json, sys
from pathlib import Path

root, fixture, target, mode, backend, adapter = sys.argv[1:]
root = Path(root)
def digest(value):
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
def file_digest(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
graders = [{"id":"answer","type":"regex","safety":True,
            "config":{"required":["SUCCESS"],"forbidden":[]}}]
rows = [
    ("positive", "routing_positive", True, [], True),
    ("close", "routing_close_negative", False, [], True),
    ("unrelated", "routing_unrelated", False, [], False),
    ("conflict", "routing_conflict", False, [] if mode == "candidate_only" else ["approved-skill"], True),
    ("value", "task_value", True, [], True),
]
cases = [
    {"id":ident, "class":kind, "task_id":f"task:{ident}-0001", "prompt":ident,
     "critical":critical, "routing":{"candidate_load":candidate,"catalog_loads":catalog},
     "artifacts":["out.txt"], "graders":["answer"],
     "fixture":fixture if target == ident else "correct"}
    for ident, kind, candidate, catalog, critical in rows
]
suite = {"schema_version":2, "kind":"shadow_candidate_evaluation_suite",
         "routing_mode":mode, "environment":{"fixture":"shadow","context":"normal"},
         "graders":graders, "cases":cases}
(root/"suite.json").write_text(json.dumps(suite, sort_keys=True))
identity = {
    "name":"fixture", "model":"fixture-model", "adapter_id":"sha256:"+"1"*64,
    "adapter_version":1, "adapter_executable_sha256":file_digest(adapter),
    "cli_executable_sha256":"sha256:"+"2"*64, "cli_version":"fixture-cli",
    "tool_policy_id":"sha256:"+"3"*64,
    "limits":{"timeout_seconds":30,"token_budget":100,"turn_budget":100,"tool_budget":100,"output_bytes":100000},
    "sandbox_id":"sha256:"+"4"*64, "real_backend":backend == "true",
    "real_backend_source":"deterministic-attested-fixture" if backend == "true" else "missing-real-backend-fixture",
}
(root/"executors.json").write_text(json.dumps(
    {"schema_version":2,"kind":"shadow_candidate_evaluation_executors","executors":[identity]},
    sort_keys=True,
))
(root/"identity.json").write_text(json.dumps(
    {key:value for key,value in identity.items() if key != "name"}, sort_keys=True
))
(root/"routing.json").write_text(json.dumps(
    {"schema_version":2,"kind":"shadow_candidate_evaluation_routing","executors":[{
        "name":"fixture","adapter_id":identity["adapter_id"],
        "adapter_executable_sha256":identity["adapter_executable_sha256"],
        "argv":[str(Path(adapter).resolve()),"--identity",str((root/"identity.json").resolve())],
    }]}, sort_keys=True,
))
PY
}

shadow_compile() {
  local root="$1" mode="$2"
  mkdir -p "$root/run"
  if [[ "$mode" == "catalog_plus_candidate" ]]; then
    "$SCRIPT_DIR/skill-evaluation.py" shadow-compile "$root/candidate" \
      --suite "$root/suite.json" --catalog-dir "$root/catalog" --executors "$root/executors.json" \
      --routing "$root/routing.json" \
      --run-dir "$root/run" --nonce shadow-fixture-nonce --harness "$SHADOW_HARNESS" >/dev/null
    return
  fi
  "$SCRIPT_DIR/skill-evaluation.py" shadow-compile "$root/candidate" \
    --suite "$root/suite.json" --executors "$root/executors.json" \
    --routing "$root/routing.json" \
    --run-dir "$root/run" --nonce shadow-fixture-nonce --harness "$SHADOW_HARNESS" >/dev/null
}

shadow_execute() {
  local root="$1"
  mkdir -p "$root/result" "$root/execute-scratch"
  "$SCRIPT_DIR/skill-evaluation.py" shadow-execute --run-dir "$root/run" \
    --result-dir "$root/result" --routing "$root/routing.json" \
    --scratch "$root/execute-scratch" --harness "$SHADOW_HARNESS" >/dev/null
}

shadow_certify() {
  local root="$1" mode="$2" scratch="$3"
  mkdir -p "$root/$scratch"
  if [[ "$mode" == "catalog_plus_candidate" ]]; then
    "$SCRIPT_DIR/skill-evaluation.py" shadow-certify "$root/candidate" \
      --suite "$root/suite.json" --catalog-dir "$root/catalog" --executors "$root/executors.json" \
      --routing "$root/routing.json" \
      --run-dir "$root/run" --result-dir "$root/result" \
      --scratch "$root/$scratch" --harness "$SHADOW_HARNESS"
    return
  fi
  "$SCRIPT_DIR/skill-evaluation.py" shadow-certify "$root/candidate" \
    --suite "$root/suite.json" --executors "$root/executors.json" \
    --routing "$root/routing.json" \
    --run-dir "$root/run" --result-dir "$root/result" \
    --scratch "$root/$scratch" --harness "$SHADOW_HARNESS"
}

shadow_status() { python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])'; }

shadow_root="$TMP/shadow-complete"
make_shadow_fixture "$shadow_root"
shadow_compile "$shadow_root" catalog_plus_candidate
shadow_execute "$shadow_root"
shadow_result="$(shadow_certify "$shadow_root" catalog_plus_candidate certify-scratch)"
[[ "$(printf '%s' "$shadow_result" | shadow_status)" == "pass" ]] ||
  fail "complete catalog-bound shadow evaluation did not pass"
python3 - "$shadow_root/result/aggregate.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1]))
assert value["routing_gate"] == "pass" and value["task_value_gate"] == "pass"
assert value["routing"]["positive_recall"] == {"loaded": 1, "total": 1}
assert value["routing"]["close_negative_false_load"] == {"false_loads": 0, "total": 1}
assert value["routing"]["unrelated_false_load"] == {"false_loads": 0, "total": 1}
assert value["routing"]["conflict_selection"] == {"selected_expected": 1, "total": 1}
pair = value["task_value"]["pairs"][0]
assert pair["arms"]["candidate"]["total_tokens"] == 15
assert pair["arms"]["control"]["tool_use"] == 1
assert value["catalog_id"] and value["candidate_id"] and value["environment_id"]
PY
python3 - "$shadow_result" <<'PY'
import json, sys
value = json.loads(sys.argv[1])
receipt = json.load(open(value["receipt"]))
assert receipt["result_id"].startswith("sha256:")
assert receipt["routing_id"].startswith("sha256:")
PY
pass "catalog-bound routing matrix and separate task-value metrics certify exact identities"

task_value_catalog="$TMP/shadow-task-value-control-catalog"
make_shadow_fixture "$task_value_catalog" control-catalog-load value
shadow_compile "$task_value_catalog" catalog_plus_candidate
shadow_execute "$task_value_catalog"
[[ "$(shadow_certify "$task_value_catalog" catalog_plus_candidate certify-scratch | shadow_status)" == "pass" ]] ||
  fail "task-value control could not use the sealed current catalog"
pass "task-value controls may use sealed catalog skills without candidate contamination"

unicode_root="$TMP/shadow-unicode"
make_shadow_fixture "$unicode_root"
python3 - "$unicode_root/suite.json" <<'PY'
import json, sys
path = sys.argv[1]
value = json.load(open(path))
value["cases"][0]["prompt"] = "Review the pull request — carefully"
open(path, "w").write(json.dumps(value, sort_keys=True))
PY
shadow_compile "$unicode_root" catalog_plus_candidate
shadow_execute "$unicode_root"
[[ "$(shadow_certify "$unicode_root" catalog_plus_candidate certify-scratch | shadow_status)" == "pass" ]] ||
  fail "non-ASCII shadow suite did not retain exact identity"
pass "non-ASCII shadow inputs retain identical compiler and harness identities"

routing_tamper="$TMP/shadow-routing-tamper"
make_shadow_fixture "$routing_tamper"
shadow_compile "$routing_tamper" catalog_plus_candidate
python3 - "$routing_tamper/routing.json" <<'PY'
import json, sys
path = sys.argv[1]
value = json.load(open(path))
value["executors"][0]["argv"].append("--changed-route")
open(path, "w").write(json.dumps(value, sort_keys=True))
PY
mkdir -p "$routing_tamper/result" "$routing_tamper/execute-scratch"
if "$SCRIPT_DIR/skill-evaluation.py" shadow-execute --run-dir "$routing_tamper/run" \
  --result-dir "$routing_tamper/result" --routing "$routing_tamper/routing.json" \
  --scratch "$routing_tamper/execute-scratch" --harness "$SHADOW_HARNESS" >/dev/null 2>&1; then
  fail "changed executor route ran under a sealed shadow identity"
fi
pass "behavior-affecting executor route arguments are sealed"

same_model="$TMP/shadow-same-model"
make_shadow_fixture "$same_model"
python3 - "$same_model/executors.json" "$same_model/routing.json" <<'PY'
import copy, json, sys
executors_path, routing_path = sys.argv[1:]
executors = json.load(open(executors_path))
second = copy.deepcopy(executors["executors"][0])
second["name"] = "fixture-two"
executors["executors"].append(second)
open(executors_path, "w").write(json.dumps(executors, sort_keys=True))
routing = json.load(open(routing_path))
second_route = copy.deepcopy(routing["executors"][0])
second_route["name"] = "fixture-two"
routing["executors"].append(second_route)
open(routing_path, "w").write(json.dumps(routing, sort_keys=True))
PY
shadow_compile "$same_model" catalog_plus_candidate
shadow_execute "$same_model"
[[ "$(shadow_certify "$same_model" catalog_plus_candidate certify-scratch | shadow_status)" == "pass" ]] ||
  fail "same-model executors were not verified by exact executor name"
pass "same-model executors retain distinct exact identities"

candidate_only="$TMP/shadow-candidate-only"
make_shadow_fixture "$candidate_only" correct none candidate_only
shadow_compile "$candidate_only" candidate_only
shadow_execute "$candidate_only"
[[ "$(shadow_certify "$candidate_only" candidate_only certify-scratch | shadow_status)" == "inconclusive" ]] ||
  fail "candidate-only routing certified catalog authority"
python3 - "$candidate_only/result/aggregate.json" <<'PY'
import json, sys
value=json.load(open(sys.argv[1]))
assert value["routing_diagnostic"] is True and value["routing_gate"] == "inconclusive"
PY
pass "candidate-only routing remains explicitly diagnostic and inconclusive"

missing_catalog="$TMP/shadow-missing-catalog"
make_shadow_fixture "$missing_catalog"
mkdir -p "$missing_catalog/run"
if "$SCRIPT_DIR/skill-evaluation.py" shadow-compile "$missing_catalog/candidate" \
  --suite "$missing_catalog/suite.json" --executors "$missing_catalog/executors.json" \
  --routing "$missing_catalog/routing.json" \
  --run-dir "$missing_catalog/run" --nonce shadow-fixture-nonce --harness "$SHADOW_HARNESS" >/dev/null 2>&1; then
  fail "catalog routing compiled without a sealed catalog"
fi
pass "missing approved catalog fails closed"

run_shadow_failure() {
  local name="$1" fixture="$2" target="$3" expected="$4" backend="${5:-true}"
  local root="$TMP/shadow-$name"
  make_shadow_fixture "$root" "$fixture" "$target" catalog_plus_candidate "$backend"
  shadow_compile "$root" catalog_plus_candidate
  shadow_execute "$root"
  [[ "$(shadow_certify "$root" catalog_plus_candidate certify-scratch | shadow_status)" == "$expected" ]] ||
    fail "shadow fixture $name did not produce $expected"
}

run_shadow_failure positive-missing positive-missing-load positive regression
run_shadow_failure positive-wrong wrong-load positive regression
run_shadow_failure positive-ambiguous ambiguous-load positive regression
run_shadow_failure close-false close-negative-false-load close regression
run_shadow_failure unrelated-false unrelated-false-load unrelated regression
run_shadow_failure conflict-wrong conflict-wrong-selection conflict regression
run_shadow_failure effective-mismatch effective-identity-mismatch value inconclusive
run_shadow_failure token-budget over-token value regression
run_shadow_failure turn-budget over-turn value regression
run_shadow_failure tool-budget over-tool value regression
run_shadow_failure usage-missing usage-missing value inconclusive
run_shadow_failure usage-invalid usage-invalid value inconclusive
run_shadow_failure usage-duplicate usage-duplicate value inconclusive
run_shadow_failure missing-real-backend correct none inconclusive false
run_shadow_failure quarantine-authority quarantine-request value inconclusive
pass "routing errors, execution identity, budgets, usage, backend, and authority requests fail closed"

for drift in candidate catalog environment scenario executor; do
  root="$TMP/shadow-drift-$drift"
  make_shadow_fixture "$root"
  shadow_compile "$root" catalog_plus_candidate
  shadow_execute "$root"
  case "$drift" in
    candidate) echo drift >> "$root/candidate/SKILL.md" ;;
    catalog) echo drift >> "$root/catalog/approved-skill/SKILL.md" ;;
    environment) python3 - "$root/suite.json" <<'PY'
import json, sys
p=sys.argv[1]; d=json.load(open(p)); d["environment"]["context"]="drift"; open(p,"w").write(json.dumps(d))
PY
      ;;
    scenario) python3 - "$root/suite.json" <<'PY'
import json, sys
p=sys.argv[1]; d=json.load(open(p)); d["cases"][0]["prompt"]="changed"; open(p,"w").write(json.dumps(d))
PY
      ;;
    executor) python3 - "$root/executors.json" <<'PY'
import json, sys
p=sys.argv[1]; d=json.load(open(p)); d["executors"][0]["model"]="changed-model"; open(p,"w").write(json.dumps(d))
PY
      ;;
  esac
  [[ "$(shadow_certify "$root" catalog_plus_candidate certify-scratch | shadow_status)" == "stale" ]] ||
    fail "$drift drift did not become stale"
done
pass "candidate, catalog, suite, environment, and executor drift become stale"

missing_arm="$TMP/shadow-missing-arm"
cp -R "$shadow_root" "$missing_arm"
python3 - "$missing_arm/result" <<'PY'
import hashlib, json, shutil, sys
from pathlib import Path
root=Path(sys.argv[1])
manifest=json.load(open(root/"manifest.json"))
victim=next(item for item in manifest["trials"] if json.load(open(root/"trials"/item.removeprefix("sha256:")/"result.json"))["treatment"]=="control")
shutil.rmtree(root/"trials"/victim.removeprefix("sha256:"))
def inventory():
    values=[]
    for path in sorted(root.rglob("*")):
        rel=path.relative_to(root).as_posix()
        if path.is_dir() or rel=="manifest.json": continue
        data=path.read_bytes()
        values.append({"path":rel,"sha256":"sha256:"+hashlib.sha256(data).hexdigest(),"size":len(data)})
    return values
manifest["file_inventory"]=inventory()
manifest["result_id"]="sha256:"+hashlib.sha256(json.dumps(
    {key:value for key,value in manifest.items() if key!="result_id"},
    sort_keys=True,separators=(",",":")).encode()).hexdigest()
(root/"manifest.json").write_text(json.dumps(manifest,sort_keys=True,separators=(",",":")))
PY
[[ "$(shadow_certify "$missing_arm" catalog_plus_candidate reseal-scratch | shadow_status)" == "inconclusive" ]] ||
  fail "resealed missing arm produced authority"
pass "missing arms and resealed result tampering cannot forge a pass"

forged_metric="$TMP/shadow-forged-routing-metric"
cp -R "$shadow_root" "$forged_metric"
python3 - "$forged_metric/result" <<'PY'
import hashlib, json, sys
from pathlib import Path
root=Path(sys.argv[1])
manifest=json.load(open(root/"manifest.json"))
aggregate=json.load(open(root/"aggregate.json"))
for trial_id in manifest["trials"]:
    path=root/"trials"/trial_id.removeprefix("sha256:")/"result.json"
    record=json.load(open(path))
    if record["case_class"] != "routing_close_negative" or record["status"] != "pass":
        continue
    record["candidate_loaded"]=True
    path.write_text(json.dumps(record,sort_keys=True,separators=(",",":")))
    per_case=next(
        item for item in aggregate["routing"]["per_case"]
        if item["case_id"] == record["case_id"]
        and item["executor_model"] == record["executor_identity"]["model"]
    )
    per_case["candidate_loaded"]=True
    aggregate["routing"]["close_negative_false_load"]["false_loads"] += 1
    break
else:
    raise SystemExit("no passing close-negative trial to forge")
(root/"aggregate.json").write_text(json.dumps(aggregate,sort_keys=True,separators=(",",":")))
manifest["aggregate"]=aggregate
values=[]
for path in sorted(root.rglob("*")):
    rel=path.relative_to(root).as_posix()
    if path.is_dir() or rel=="manifest.json": continue
    data=path.read_bytes()
    values.append({"path":rel,"sha256":"sha256:"+hashlib.sha256(data).hexdigest(),"size":len(data)})
manifest["file_inventory"]=values
manifest["result_id"]="sha256:"+hashlib.sha256(json.dumps(
    {key:value for key,value in manifest.items() if key!="result_id"},
    sort_keys=True,separators=(",",":")).encode()).hexdigest()
(root/"manifest.json").write_text(json.dumps(manifest,sort_keys=True,separators=(",",":")))
PY
[[ "$(shadow_certify "$forged_metric" catalog_plus_candidate forged-metric-scratch | shadow_status)" == "inconclusive" ]] ||
  fail "resealed candidate-loaded metric produced authority"
pass "candidate-loaded metrics are re-derived from sealed load evidence"

echo "PASS  $passes deterministic skill-evaluation checks"
