#!/usr/bin/env bash
# Deterministic M5.4 Dreaming compilation, verification, policy, and authority checks.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/dreaming-certification.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
EVAL="$SCRIPT_DIR/skill-evaluation.py"
HARNESS="$SCRIPT_DIR/skill-evaluation-harness.py"
ADAPTER="$SCRIPT_DIR/fake-skill-evaluation-adapter.py"
export SKILLS_STATE_DIR="$TMP/state"
export DREAMING_EVALUATION_EXECUTORS="copilot,claude,codex"
passes=0

pass() { echo "PASS  $*"; passes=$((passes + 1)); }
fail() { echo "FAIL  $*" >&2; exit 1; }

make_fixture() {
  local root="$1" profile="${2:-gate}" kind="${3:-capability_uplift}" fixture="${4:-correct}"
  mkdir -p "$root/skill" "$root/config"
  python3 - "$root" "$profile" "$kind" "$fixture" "$HARNESS" "$ADAPTER" <<'PY'
import hashlib, json, os, sys
from pathlib import Path
root, profile, kind, fixture, harness, adapter = sys.argv[1:]
root = Path(root); skill = root/"skill"; config_root = root/"config"
def canonical(value): return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
def sha(value): return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()
def file_sha(path): return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
skill.joinpath("SKILL.md").write_text("---\nname: fixture-skill\ndescription: Fixture.\n---\n\nReturn the deterministic result.\n")
skill.joinpath(".agent-created").touch()
envelope = {
 "schema_version":2,"skill":"fixture-skill","created_by":"skill-review",
 "source_session_id":"fixture-session","source_mode":"dispatch",
 "review_prompt_version":"skill-review-2","created_at":"2026-01-01T00:00:00+00:00",
 "evidence":[{"task_key":"task:11111111-1111-1111-1111-111111111111",
 "session_id":"fixture-session","observed_at":"2026-01-01T00:00:00+00:00",
 "independence":"verified","evidence_kind":"successful-procedure","summary":"fixture"}],
 "routing":{"destination":"skill","reason":"fixture"},"claims":[],
 "evaluation":{"status":"not_evaluated","evaluated_at":None,"candidate_id":None,
 "model":None,"source_case":None,"sibling_case":None,"waiver_class":None,"waiver_reason":None}}
skill.joinpath(".agent-created.json").write_bytes(canonical(envelope)+b"\n")
graders = [
 {"id":"answer","type":"regex","safety":True,"config":{"required":["SUCCESS"],"forbidden":[]}},
 {"id":"artifact","type":"file","safety":True,"config":{"path":"out.txt","contains":"actual"}},
]
suite_graders = [
 {"id":item["id"],"type":item["type"],"safety":item["safety"],"identity":sha(item)}
 for item in graders
]
cases = [
 {"id":"intended-case","class":"intended","task_id":"intended:fixture-0001",
  "prompt":"Perform the intended task.","deterministic_graders":["answer","artifact"]},
 {"id":"related-case","class":"related","task_id":"related:fixture-0002",
  "prompt":"Preserve the related task.","deterministic_graders":["answer","artifact"]},
 {"id":"activation-positive","class":"activation_positive","task_id":"activate:fixture-0003",
  "prompt":"Use the fixture skill.","deterministic_graders":["answer","artifact"],
  "activation":{"expected_load":True}},
 {"id":"activation-negative","class":"activation_negative","task_id":"activate:fixture-0004",
  "prompt":"Do unrelated work.","deterministic_graders":["answer","artifact"],
  "activation":{"expected_load":False}},
]
skill.joinpath(".skill-evaluation-cases.json").write_bytes(
 canonical({"schema_version":2,"graders":suite_graders,"cases":cases})+b"\n")
adapter_sha=file_sha(adapter); adapter_id="sha256:"+"2"*64
tool="sha256:"+"1"*64
rubric={"id":"quality","instruction":"Choose the better response only by task quality."}
comparator={"route":"fixture-route","model":"judge-1","adapter_id":adapter_id,
 "adapter_version":1,"adapter_executable_sha256":adapter_sha,"timeout_seconds":30,
 "token_budget":100,"rubric_id":sha(rubric)}
policy_executors=[]; compiled_executors=[]; routes=[]
for number, name in enumerate(("copilot","claude","codex"), 1):
    base={"name":name,"model":f"{name}-model-1","adapter_id":adapter_id,"adapter_version":1,
          "adapter_executable_sha256":adapter_sha,"cli_executable_sha256":"sha256:"+str(number+2)*64}
    full={**base,"cli_version":f"{name}-cli-1","tool_policy_id":tool,
          "limits":{"timeout_seconds":30,"token_budget":100,"output_bytes":100000},
          "sandbox_id":"sha256:"+str(number+5)*64}
    identity={key:value for key,value in full.items() if key != "name"}
    identity_path=config_root/f"{name}-identity.json"
    identity_path.write_bytes(canonical(identity)+b"\n")
    policy_executors.append(base); compiled_executors.append(full)
    routes.append({"name":name,"adapter_id":adapter_id,"adapter_executable_sha256":adapter_sha,
                   "argv":[adapter,"--identity",str(identity_path)]})
policy={"schema_version":2,"profile":profile,"policy_kind":kind,
        "required_executors":policy_executors,"comparator":comparator}
skill.joinpath(".skill-evaluation-policy.json").write_bytes(canonical(policy)+b"\n")
comparator_path=config_root/"comparator-identity.json"
comparator_path.write_bytes(canonical(comparator)+b"\n")
routing={"schema_version":1,"kind":"skill_evaluation_routing","executors":routes,
 "comparator":{"route":comparator["route"],"adapter_id":adapter_id,
 "adapter_executable_sha256":adapter_sha,"argv":[adapter,"--identity",str(comparator_path)]}}
config_root.joinpath("routing.json").write_bytes(canonical(routing)+b"\n")
runtime=[]
for case in cases:
    case_fixture = fixture if case["id"] == "intended-case" and fixture != "false-trigger" else "correct"
    if case["id"] == "activation-negative":
        case_fixture = fixture if fixture == "false-trigger" else "activation-negative"
    runtime.append({"id":case["id"],"fixture":case_fixture,"artifacts":["out.txt"],
                    "semantic":case["class"] in {"intended","related"}})
config={"schema_version":1,"kind":"dreaming_evaluation_compilation",
 "harness_executable_sha256":file_sha(harness),"tool_policy_id":tool,
 "retention_policy_id":"sha256:"+"b"*64,
 "limits":{"timeout_seconds":30,"output_bytes":100000,"file_bytes":100000,
           "global_concurrency":1,"per_executor_concurrency":1},
 "identity_markers":["candidate-marker","fixture-skill"],"graders":graders,
 "case_runtime":runtime,"rubric":rubric,"executors":compiled_executors,
 "comparator":comparator}
config_root.joinpath("compilation.json").write_bytes(canonical(config)+b"\n")
PY
}

run_fixture() {
  local root="$1" nonce="$2"
  mkdir -p "$root/run" "$root/result" "$root/run-scratch" "$root/verify-scratch"
  "$EVAL" v2-run-compile "$root/skill" --run-dir "$root/run" \
    --config "$root/config/compilation.json" --routing "$root/config/routing.json" \
    --nonce "$nonce" --harness "$HARNESS" >/dev/null
  "$EVAL" v2-run-execute --run-dir "$root/run" --result-dir "$root/result" \
    --routing "$root/config/routing.json" --scratch "$root/run-scratch" \
    --harness "$HARNESS" >/dev/null
  "$EVAL" v2-result-certify "$root/skill" --run-dir "$root/run" \
    --result-dir "$root/result" --routing "$root/config/routing.json" \
    --scratch "$root/verify-scratch" --nonce "$nonce" --harness "$HARNESS"
}

BASE="$TMP/base"
make_fixture "$BASE"
certification="$(run_fixture "$BASE" fixture-nonce)"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$certification")" == "pass" ]] ||
  fail "valid gate result did not certify"
aggregate="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["aggregate"])' <<<"$certification")"
authority="$("$EVAL" v2-authority-write "$BASE/skill" --aggregate "$aggregate")"
"$EVAL" v2-authority-validate "$BASE/skill" >/dev/null
"$EVAL" current-gate "$BASE/skill" >/dev/null
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["authoritative"])' <<<"$certification")" == "True" ]] ||
  fail "passing gate result was not marked authoritative"
pass "compile, execute, independent verify, certificates, aggregate, and authority pass end to end"

trace_path="$(find "$BASE/result/trials" -name trace.json | head -1)"
cp "$trace_path" "$BASE/trace.saved"
echo altered >> "$trace_path"
if "$EVAL" current-gate "$BASE/skill" >/dev/null 2>&1; then
  fail "current authority survived modified bound result evidence"
fi
mv "$BASE/trace.saved" "$trace_path"
"$EVAL" current-gate "$BASE/skill" >/dev/null
pass "current authority re-verifies bound result evidence instead of trusting stored hashes"

cp "$BASE/skill/.skill-evaluation-policy.json" "$BASE/policy.saved"
printf '{"schema_version":1,"fixture":"downgrade"}\n' > "$BASE/skill/.skill-evaluation-policy.json"
if "$EVAL" current-gate "$BASE/skill" >/dev/null 2>&1; then
  fail "current gate downgraded to legacy after v2 authority existed"
fi
mv "$BASE/policy.saved" "$BASE/skill/.skill-evaluation-policy.json"
pass "current authority cannot be downgraded to a legacy gate by mutable sidecars"

python3 - "$aggregate" <<'PY'
import json, sys
value=json.load(open(sys.argv[1]))
assert [x["executor"]["name"] for x in value["certificates"]] == ["copilot","claude","codex"]
assert len({x["certificate_id"] for x in value["certificates"]}) == 3
assert all(x["status"] == "pass" and x["result_bundle_id"].startswith("sha256:")
           and x["result_bundle_sha256"].startswith("sha256:") for x in value["certificates"])
PY
pass "executor certificates remain independent and bind the exact result identity"

python3 - "$EVAL" <<'PY'
import importlib.util, sys
spec=importlib.util.spec_from_file_location("evaluation", sys.argv[1])
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
def records(related_candidate="pass", positive_loads=3):
    out=[]
    for case_id, case_class in (
        ("intended","intended"),("related","related"),
        ("positive","activation_positive"),("negative","activation_negative")):
        for repetition in range(3):
            status="pass"; proved=True
            if case_class=="related": status=related_candidate
            if case_class=="activation_positive" and repetition >= positive_loads:
                status="invalid"; proved=False
            out.append({"executor":"copilot","case_id":case_id,"case_class":case_class,
                        "treatment":"candidate","status":status,"skill_load_proved":proved,
                        "deterministic_pass":status=="pass"})
            if case_class in {"intended","related"}:
                out.append({"executor":"copilot","case_id":case_id,"case_class":case_class,
                            "treatment":"control","status":"fail","skill_load_proved":True,
                            "deterministic_pass":False})
    return out
comparisons=[
 {"executor":"copilot","case_id":"intended","status":"complete","winner":"A",
  "assignment":{"A":"candidate","B":"control"}} for _ in range(3)
]
encoded={"profile":"gate","policy_kind":"encoded_preference"}
capability={"profile":"gate","policy_kind":"capability_uplift"}
assert module.executor_policy_status("copilot", encoded, "complete", records(), comparisons)=="pass"
losing=[{**item,"assignment":{"A":"control","B":"candidate"}} for item in comparisons]
assert module.executor_policy_status("copilot", encoded, "complete", records(), losing)=="regression"
assert module.executor_policy_status("copilot", capability, "complete", records("fail"), comparisons)=="regression"
assert module.executor_policy_status("copilot", capability, "complete", records(positive_loads=1), comparisons)=="regression"
PY
pass "capability, encoded-preference, related-task, and activation policies are independent and fail closed"

ITERATE="$TMP/iterate"
make_fixture "$ITERATE" iterate
iterate="$(run_fixture "$ITERATE" iterate-nonce)"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$iterate")" == "inconclusive" ]] ||
  fail "iterate result became authoritative"
if "$EVAL" v2-authority-write "$ITERATE/skill" \
  --aggregate "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["aggregate"])' <<<"$iterate")" >/dev/null 2>&1; then
  fail "iterate aggregate issued authority"
fi
pass "one-trial iterate evidence remains visibly non-authoritative"

REGRESSION="$TMP/regression"
make_fixture "$REGRESSION" gate capability_uplift false-trigger
regression="$(run_fixture "$REGRESSION" regression-nonce)"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$regression")" == "regression" ]] ||
  fail "activation regression did not block certification"
pass "activation and related regression policy fails closed"

INFRA="$TMP/infrastructure"
make_fixture "$INFRA" gate capability_uplift collect-fail
infrastructure="$(run_fixture "$INFRA" infrastructure-nonce)"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$infrastructure")" == "inconclusive" ]] ||
  fail "infrastructure failure was scored as candidate behavior"
pass "inconclusive infrastructure cannot authorize"

UNAVAILABLE="$TMP/unavailable"
make_fixture "$UNAVAILABLE"
unavailable="$("$EVAL" v2-unavailable-aggregate "$UNAVAILABLE/skill" \
  --unavailable copilot=missing --unavailable claude=missing --unavailable codex=missing)"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$unavailable")" == "inconclusive" ]] ||
  fail "unavailable required executors did not remain inconclusive"
pass "absent required executors are explicit unavailable certificates"

expect_compile_refusal() {
  local name="$1" python="$2"
  local root="$TMP/mutate-$name"
  make_fixture "$root"
  python3 - "$root" <<<"$python"
  mkdir -p "$root/run"
  if "$EVAL" v2-run-compile "$root/skill" --run-dir "$root/run" \
    --config "$root/config/compilation.json" --routing "$root/config/routing.json" \
    --nonce mutation --harness "$HARNESS" >/dev/null 2>&1; then
    fail "$name mutation compiled"
  fi
}

expect_compile_refusal model '
import json,sys; from pathlib import Path
p=Path(sys.argv[1])/"config/compilation.json"; d=json.load(open(p)); d["executors"][0]["model"]="other"; p.write_text(json.dumps(d))
'
expect_compile_refusal cli '
import json,sys; from pathlib import Path
p=Path(sys.argv[1])/"config/compilation.json"; d=json.load(open(p)); d["executors"][0]["cli_executable_sha256"]="sha256:"+"9"*64; p.write_text(json.dumps(d))
'
expect_compile_refusal adapter '
import json,sys; from pathlib import Path
p=Path(sys.argv[1])/"config/compilation.json"; d=json.load(open(p)); d["executors"][0]["adapter_id"]="sha256:"+"9"*64; p.write_text(json.dumps(d))
'
expect_compile_refusal harness '
import json,sys; from pathlib import Path
p=Path(sys.argv[1])/"config/compilation.json"; d=json.load(open(p)); d["harness_executable_sha256"]="sha256:"+"9"*64; p.write_text(json.dumps(d))
'
expect_compile_refusal grader '
import json,sys; from pathlib import Path
p=Path(sys.argv[1])/"config/compilation.json"; d=json.load(open(p)); d["graders"][0]["config"]["required"]=["OTHER"]; p.write_text(json.dumps(d))
'
expect_compile_refusal comparator '
import json,sys; from pathlib import Path
p=Path(sys.argv[1])/"config/compilation.json"; d=json.load(open(p)); d["comparator"]["model"]="other"; p.write_text(json.dumps(d))
'
expect_compile_refusal tool '
import json,sys; from pathlib import Path
p=Path(sys.argv[1])/"config/compilation.json"; d=json.load(open(p)); d["tool_policy_id"]="sha256:"+"9"*64; p.write_text(json.dumps(d))
'
expect_compile_refusal required-set '
import json,sys; from pathlib import Path
p=Path(sys.argv[1])/"skill/.skill-evaluation-policy.json"; d=json.load(open(p)); d["required_executors"].pop(); p.write_text(json.dumps(d))
'
pass "executor, model, CLI, adapter, harness, grader, comparator, tool, and required-set drift refuses"

for mutation in candidate case nonce budget profile; do
  root="$TMP/stale-$mutation"
  cp -R "$BASE" "$root"
  rm -rf "$root/verify-scratch"; mkdir "$root/verify-scratch"
  case "$mutation" in
    candidate) echo changed >> "$root/skill/SKILL.md" ;;
    case) python3 - "$root/skill/.skill-evaluation-cases.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["cases"][0]["prompt"]="changed"; open(p,"w").write(json.dumps(d))
PY
      ;;
    budget) python3 - "$root/run/manifest.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["executors"][0]["limits"]["token_budget"]=99; open(p,"w").write(json.dumps(d))
PY
      ;;
    profile) python3 - "$root/skill/.skill-evaluation-policy.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["profile"]="iterate"; open(p,"w").write(json.dumps(d))
PY
      ;;
  esac
  used_nonce="fixture-nonce"; [[ "$mutation" == nonce ]] && used_nonce="wrong-nonce"
  if "$EVAL" v2-result-certify "$root/skill" --run-dir "$root/run" \
    --result-dir "$root/result" --routing "$root/config/routing.json" \
    --scratch "$root/verify-scratch" --nonce "$used_nonce" --harness "$HARNESS" >/dev/null 2>&1; then
    fail "$mutation stale input certified"
  fi
done
pass "candidate, case, nonce, budget, and profile changes stale bound evidence"

root="$TMP/compiled-identity-drift"
cp -R "$BASE" "$root"
rm -rf "$root/verify-scratch"; mkdir "$root/verify-scratch"
python3 - "$root/run" <<'PY'
import hashlib, json, sys
from pathlib import Path
root=Path(sys.argv[1])
compilation=json.load(open(root/"compilation.json"))
manifest=json.load(open(root/"manifest.json"))
compilation["executors"][0]["model"]="unauthorized-model"
(root/"compilation.json").write_text(json.dumps(compilation, sort_keys=True, separators=(",", ":"))+"\n")
manifest["executors"][0]["model"]="unauthorized-model"
def inventory():
    out=[]
    for p in sorted(root.rglob("*")):
        rel=p.relative_to(root).as_posix()
        if p.is_dir() or rel=="manifest.json": continue
        data=p.read_bytes()
        out.append({"path":rel,"sha256":"sha256:"+hashlib.sha256(data).hexdigest(),"size":len(data)})
    return out
manifest["file_inventory"]=inventory()
fields=("schema_version","kind","candidate_id","suite_id","profile","trials_per_arm",
        "executors","comparator","harness_executable_sha256","tool_policy_id",
        "grader_set_id","retention_policy_id","limits","file_inventory")
manifest["run_id"]="sha256:"+hashlib.sha256(json.dumps(
    {key:manifest[key] for key in fields},sort_keys=True,separators=(",",":")).encode()).hexdigest()
(root/"manifest.json").write_text(json.dumps(manifest,sort_keys=True,separators=(",",":"))+"\n")
PY
if "$EVAL" v2-result-certify "$root/skill" --run-dir "$root/run" \
  --result-dir "$root/result" --routing "$root/config/routing.json" \
  --scratch "$root/verify-scratch" --nonce fixture-nonce --harness "$HARNESS" >/dev/null 2>&1; then
  fail "self-consistent compiled executor substitution certified"
fi
pass "certification rebinds compiled executor, comparator, tool, and budget identities to policy"

for missing in trial trace artifact comparison; do
  root="$TMP/missing-$missing"
  cp -R "$BASE" "$root"
  rm -rf "$root/verify-scratch"; mkdir "$root/verify-scratch"
  case "$missing" in
    trial) rm "$(find "$root/result/trials" -name trial.json | head -1)" ;;
    trace) rm "$(find "$root/result/trials" -name trace.json | head -1)" ;;
    artifact) rm "$(find "$root/result/trials" -path '*/artifacts/out.txt' | head -1)" ;;
    comparison) rm "$(find "$root/result/comparisons" -name '*.json' ! -name '*.packet.json' ! -name '*.response.json' | head -1)" ;;
  esac
  if "$EVAL" v2-result-certify "$root/skill" --run-dir "$root/run" \
    --result-dir "$root/result" --routing "$root/config/routing.json" \
    --scratch "$root/verify-scratch" --nonce fixture-nonce --harness "$HARNESS" >/dev/null 2>&1; then
    fail "missing $missing evidence certified"
  fi
done
pass "missing trial, trace, artifact, or comparison evidence refuses"

for forged in producer result; do
  root="$TMP/forged-$forged"
  cp -R "$BASE" "$root"
  rm -rf "$root/verify-scratch"; mkdir "$root/verify-scratch"
  python3 - "$root/result/manifest.json" "$forged" <<'PY'
import json,sys
p,kind=sys.argv[1:]; d=json.load(open(p))
if kind=="producer": d["harness_executable_sha256"]="sha256:"+"9"*64
else: d["result_id"]="sha256:"+"9"*64
open(p,"w").write(json.dumps(d))
PY
  if "$EVAL" v2-result-certify "$root/skill" --run-dir "$root/run" \
    --result-dir "$root/result" --routing "$root/config/routing.json" \
    --scratch "$root/verify-scratch" --nonce fixture-nonce --harness "$HARNESS" >/dev/null 2>&1; then
    fail "forged $forged identity certified"
  fi
done
pass "forged producer and result identities refuse"

if "$EVAL" gate "$BASE/skill" >/dev/null 2>&1; then
  fail "legacy v1 gate authorized schema-v3 authority"
fi
pass "rollback leaves v2 receipts inert to the legacy gate"

echo "PASS  $passes deterministic M5.4 Dreaming certification checks"
