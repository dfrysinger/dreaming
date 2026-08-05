#!/usr/bin/env bash
# Deterministic M5.1 policy, schema, state, and waiver checks.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/cross-cli-evaluation.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
export SKILLS_STATE_DIR="$TMP/state"
export DREAMING_EVALUATION_EXECUTORS="copilot,claude,codex"
EVAL="$SCRIPT_DIR/skill-evaluation.py"
passes=0

pass() { echo "PASS  $*"; passes=$((passes + 1)); }
fail() { echo "FAIL  $*" >&2; exit 1; }

make_skill() {
  local root="$1" name="$2"
  mkdir -p "$root/$name/scripts"
  cat > "$root/$name/SKILL.md" <<EOF
---
name: $name
description: Cross-CLI evaluation fixture.
---

# $name
EOF
  touch "$root/$name/.agent-created"
  cat > "$root/$name/.agent-created.json" <<EOF
{"schema_version":2,"skill":"$name","created_by":"skill-review","source_session_id":"fixture-session","source_mode":"dispatch","review_prompt_version":"skill-review-2","created_at":"2026-01-01T00:00:00+00:00","evidence":[{"task_key":"task:11111111-1111-1111-1111-111111111111","session_id":"fixture-session","observed_at":"2026-01-01T00:00:00+00:00","independence":"verified","evidence_kind":"successful-procedure","summary":"fixture"}],"routing":{"destination":"skill","reason":"fixture"},"claims":[],"evaluation":{"status":"not_evaluated","evaluated_at":null,"candidate_id":null,"model":null,"source_case":null,"sibling_case":null,"waiver_class":null,"waiver_reason":null}}
EOF
  cat > "$root/$name/scripts/helper.sh" <<'SH'
#!/usr/bin/env bash
exit 1
SH
  cat > "$root/$name/scripts/test-helper.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
sha="$(shasum -a 256 scripts/helper.sh | awk '{print $1}')"
printf '{"status":"pass","verified_files":{"scripts/helper.sh":"%s"}}\n' "$sha"
SH
  chmod +x "$root/$name/scripts/"*.sh
}

write_suite() {
  local skill="$1"
  cat > "$skill/.skill-evaluation-cases.json" <<'JSON'
{
  "schema_version": 2,
  "graders": [{"id":"safe-outcome","type":"regex","safety":true,"identity":"sha256:0000000000000000000000000000000000000000000000000000000000000000"}],
  "cases": [
    {"id":"intended-case","class":"intended","task_id":"intended:fixture-0001","prompt":"Improve this task.","deterministic_graders":["safe-outcome"]},
    {"id":"related-case","class":"related","task_id":"related:fixture-0002","prompt":"Preserve this task.","deterministic_graders":["safe-outcome"]},
    {"id":"activation-positive","class":"activation_positive","task_id":"activate:fixture-0003","prompt":"Use the fixture skill.","deterministic_graders":["safe-outcome"],"activation":{"expected_load":true}},
    {"id":"activation-negative","class":"activation_negative","task_id":"activate:fixture-0004","prompt":"Do unrelated work.","deterministic_graders":["safe-outcome"],"activation":{"expected_load":false}}
  ]
}
JSON
}

write_policy() {
  local skill="$1" comparator_model="${2:-judge-1}"
  cat > "$skill/.skill-evaluation-policy.json" <<JSON
{
  "schema_version": 2,
  "profile": "gate",
  "policy_kind": "capability_uplift",
  "required_executors": [
    {"name":"copilot","model":"copilot-model-1","adapter_id":"sha256:1111111111111111111111111111111111111111111111111111111111111111","adapter_version":1,"adapter_executable_sha256":"sha256:1212121212121212121212121212121212121212121212121212121212121212","cli_executable_sha256":"sha256:1313131313131313131313131313131313131313131313131313131313131313"},
    {"name":"claude","model":"claude-model-1","adapter_id":"sha256:2222222222222222222222222222222222222222222222222222222222222222","adapter_version":1,"adapter_executable_sha256":"sha256:2323232323232323232323232323232323232323232323232323232323232323","cli_executable_sha256":"sha256:2424242424242424242424242424242424242424242424242424242424242424"},
    {"name":"codex","model":"codex-model-1","adapter_id":"sha256:3333333333333333333333333333333333333333333333333333333333333333","adapter_version":1,"adapter_executable_sha256":"sha256:3434343434343434343434343434343434343434343434343434343434343434","cli_executable_sha256":"sha256:3535353535353535353535353535353535353535353535353535353535353535"}
  ],
  "comparator":{"route":"local-fixture","model":"$comparator_model","adapter_id":"sha256:4444444444444444444444444444444444444444444444444444444444444444","adapter_version":1,"adapter_executable_sha256":"sha256:4545454545454545454545454545454545454545454545454545454545454545","timeout_seconds":120,"token_budget":4096,"rubric_id":"sha256:4646464646464646464646464646464646464646464646464646464646464646"}
}
JSON
}

make_aggregate() {
  local skill="$1" output="$2"
  shift 2
  local prepared="$output.prepared"
  "$EVAL" v2-prepare "$skill" > "$prepared"
  python3 - "$output" "$prepared" "$@" <<'PY'
import hashlib, json, sys
output, prepared_path, *statuses = sys.argv[1:]
prepared = json.load(open(prepared_path))
def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
def identity(field, value):
    return "sha256:" + hashlib.sha256(canonical({k:v for k,v in value.items() if k != field})).hexdigest()
certificates = []
for index, (executor, status) in enumerate(zip(prepared["required_executors"], statuses)):
    cert = {
        "schema_version": 2, "kind": "executor_certificate", "status": status,
        "candidate_id": prepared["candidate_id"], "suite_id": prepared["suite_id"],
        "policy_id": prepared["policy_id"], "profile": prepared["profile"],
        "executor": executor,
        "result_bundle_sha256": "sha256:" + str(index + 7) * 64,
        "result_bundle_id": "sha256:" + str(index + 4) * 64,
        "run_id": "sha256:" + str(index + 1) * 64,
    }
    cert["certificate_id"] = identity("certificate_id", cert)
    certificates.append(cert)
overall = "regression" if "regression" in statuses else "inconclusive" if any(s != "pass" for s in statuses) else "pass"
aggregate = {
    "schema_version": 2, "kind": "aggregate_receipt", "status": overall,
    "skill_path": __import__("os").path.realpath(sys.stdin.name) if False else None,
    "candidate_id": prepared["candidate_id"], "candidate_inventory": prepared["candidate_inventory"],
    "suite_id": prepared["suite_id"], "policy_id": prepared["policy_id"],
    "profile": prepared["profile"], "certificates": certificates,
}
# The fixture's skill path is supplied by the shell because JSON stdin has no path identity.
aggregate["skill_path"] = __import__("os").path.realpath(output + "/..") if False else ""
aggregate["aggregate_id"] = identity("aggregate_id", aggregate)
json.dump(aggregate, open(output, "w"), sort_keys=True)
PY
  python3 - "$output" "$skill" <<'PY'
import json, os, sys
p, skill = sys.argv[1:]
d = json.load(open(p))
d["skill_path"] = os.path.realpath(skill)
import hashlib
d.pop("aggregate_id")
d["aggregate_id"] = "sha256:" + hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
json.dump(d, open(p, "w"), sort_keys=True)
PY
}

ROOT="$TMP/skills"
make_skill "$ROOT" valid-skill
write_suite "$ROOT/valid-skill"
write_policy "$ROOT/valid-skill"
"$EVAL" v2-suite-validate "$ROOT/valid-skill/.skill-evaluation-cases.json" >/dev/null
"$EVAL" v2-policy-validate "$ROOT/valid-skill/.skill-evaluation-policy.json" >/dev/null
pass "schema-v2 suite and ordered gate policy validate"

python3 - "$ROOT/valid-skill/.skill-evaluation-cases.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["cases"][1]["id"] = d["cases"][0]["id"]
json.dump(d, open(p, "w"))
PY
if "$EVAL" v2-suite-validate "$ROOT/valid-skill/.skill-evaluation-cases.json" >/dev/null 2>&1; then
  fail "duplicate case ID passed"
fi
write_suite "$ROOT/valid-skill"
python3 - "$ROOT/valid-skill/.skill-evaluation-cases.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["cases"][1]["task_id"] = d["cases"][0]["task_id"]
json.dump(d, open(p, "w"))
PY
if "$EVAL" v2-suite-validate "$ROOT/valid-skill/.skill-evaluation-cases.json" >/dev/null 2>&1; then
  fail "shared task ID passed"
fi
write_suite "$ROOT/valid-skill"
python3 - "$ROOT/valid-skill/.skill-evaluation-cases.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["cases"][0]["deterministic_graders"] = ["missing-grader"]
json.dump(d, open(p, "w"))
PY
if "$EVAL" v2-suite-validate "$ROOT/valid-skill/.skill-evaluation-cases.json" >/dev/null 2>&1; then
  fail "unknown deterministic grader reference passed"
fi
write_suite "$ROOT/valid-skill"
pass "v2 suite rejects duplicate IDs, shared tasks, and invalid grader references"

policy_one="$("$EVAL" v2-policy-validate "$ROOT/valid-skill/.skill-evaluation-policy.json")"
write_policy "$ROOT/valid-skill" different-judge
policy_two="$("$EVAL" v2-policy-validate "$ROOT/valid-skill/.skill-evaluation-policy.json")"
policy_one_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["policy_id"])' <<<"$policy_one")"
policy_two_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["policy_id"])' <<<"$policy_two")"
[[ "$policy_one_id" != "$policy_two_id" ]] || fail "comparator input did not change policy identity"
write_policy "$ROOT/valid-skill"
python3 - "$ROOT/valid-skill/.skill-evaluation-policy.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["required_executors"][0], d["required_executors"][1] = d["required_executors"][1], d["required_executors"][0]
json.dump(d, open(p, "w"))
PY
if "$EVAL" v2-policy-validate "$ROOT/valid-skill/.skill-evaluation-policy.json" >/dev/null 2>&1; then
  fail "misordered gate executors passed"
fi
write_policy "$ROOT/valid-skill"
pass "ordered exact executor and comparator inputs bind policy identity"

python3 - "$ROOT/valid-skill/.skill-evaluation-policy.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["required_executors"] = [
    executor
    for executor in d["required_executors"]
    if executor["name"] in {"copilot", "codex"}
]
json.dump(d, open(p, "w"))
PY
"$EVAL" v2-policy-validate "$ROOT/valid-skill/.skill-evaluation-policy.json" >/dev/null
write_policy "$ROOT/valid-skill"
pass "gate policy accepts an explicit ordered executor subset"

cat > "$TMP/legacy-cases.json" <<'JSON'
{"schema_version":1,"source":{"task_id":"source:fixture-0001","prompt":"source","required_regex":[{"id":"right","pattern":"RIGHT"}]},"sibling":{"task_id":"sibling:fixture-0002","prompt":"sibling","required_regex":[{"id":"safe","pattern":"SAFE"}]}}
JSON
legacy="$("$EVAL" v2-suite-validate "$TMP/legacy-cases.json")"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["cross_executor_authority"])' <<<"$legacy")" == "False" ]] ||
  fail "legacy compile gained cross-executor authority"
[[ "$(python3 -c 'import json,sys; print(",".join(json.load(sys.stdin)["case_classes"]))' <<<"$legacy")" == "intended,related" ]] ||
  fail "legacy compile invented activation cases"
pass "legacy suite compiles without activation or cross-executor authority"

make_aggregate "$ROOT/valid-skill" "$TMP/regressing.json" regression pass pass
if "$EVAL" v2-authority-write "$ROOT/valid-skill" --aggregate "$TMP/regressing.json" >/dev/null 2>&1; then
  fail "regressing executor aggregate issued authority"
fi
make_aggregate "$ROOT/valid-skill" "$TMP/unavailable.json" unavailable pass pass
python3 - "$TMP/unavailable.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["status"] = "pass"
d.pop("aggregate_id")
import hashlib
d["aggregate_id"] = "sha256:" + hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
json.dump(d, open(p, "w"), sort_keys=True)
PY
if "$EVAL" v2-authority-write "$ROOT/valid-skill" --aggregate "$TMP/unavailable.json" >/dev/null 2>&1; then
  fail "unavailable executor was pooled into a pass"
fi
make_aggregate "$ROOT/valid-skill" "$TMP/inconclusive.json" inconclusive pass pass
if "$EVAL" v2-authority-write "$ROOT/valid-skill" --aggregate "$TMP/inconclusive.json" >/dev/null 2>&1; then
  fail "inconclusive executor issued authority"
fi
pass "executor certificates remain isolated and unavailable or inconclusive cannot pass"

make_aggregate "$ROOT/valid-skill" "$TMP/passing.json" pass pass pass
if "$EVAL" v2-authority-write "$ROOT/valid-skill" --aggregate "$TMP/passing.json" >/dev/null 2>&1; then
  fail "fabricated passing aggregate issued authority"
fi
if "$EVAL" gate "$ROOT/valid-skill" >/dev/null 2>&1; then
  fail "legacy M2 gate accepted v2 authority"
fi
pass "authority requires production certification and remains inert to the old gate"

make_skill "$ROOT" waiver-skill
write_suite "$ROOT/waiver-skill"
write_policy "$ROOT/waiver-skill"
make_aggregate "$ROOT/waiver-skill" "$TMP/waiver-base.json" pass pass pass
mkdir -p "$SKILLS_STATE_DIR/skill-review/evaluations/v2/receipts"
base_sha="$(python3 - "$TMP/waiver-base.json" <<'PY'
import hashlib, json, sys
value=json.load(open(sys.argv[1]))
print(hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
PY
)"
base_receipt="$SKILLS_STATE_DIR/skill-review/evaluations/v2/receipts/$base_sha.json"
cp "$TMP/waiver-base.json" "$base_receipt"
legacy_receipt="$SKILLS_STATE_DIR/skill-review/evaluations/v2/receipts/$(python3 - <<'PY'
import hashlib, json
d = {"schema_version": 1, "kind": "evaluation", "status": "pass"}
print(hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
PY
).json"
printf '{"schema_version":1,"kind":"evaluation","status":"pass"}\n' > "$legacy_receipt"
if "$EVAL" v2-waive "$ROOT/waiver-skill" --base-aggregate "$legacy_receipt" \
  --reason "fixture" --test-script scripts/test-helper.sh >/dev/null 2>&1; then
  fail "legacy receipt anchored an M5 waiver"
fi
cat > "$ROOT/waiver-skill/scripts/helper.sh" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$ROOT/waiver-skill/scripts/helper.sh"
waiver="$("$EVAL" v2-waive "$ROOT/waiver-skill" --base-aggregate "$base_receipt" \
  --reason "Bound helper behavior is covered by its unchanged test" --test-script scripts/test-helper.sh)"
waiver_receipt="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt"])' <<<"$waiver")"
"$EVAL" v2-waiver-validate "$ROOT/waiver-skill" --waiver "$waiver_receipt" >/dev/null
"$EVAL" current-gate "$ROOT/waiver-skill" >/dev/null
echo "# stale" >> "$ROOT/waiver-skill/scripts/test-helper.sh"
if "$EVAL" v2-waiver-validate "$ROOT/waiver-skill" --waiver "$waiver_receipt" >/dev/null 2>&1; then
  fail "changed waiver test identity remained valid"
fi
pass "M5 waiver rejects legacy anchors and binds restricted current inputs"

echo "PASS  $passes deterministic M5.1 cross-CLI evaluation checks"
