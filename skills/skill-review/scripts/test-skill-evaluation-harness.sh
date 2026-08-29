#!/usr/bin/env bash
# Deterministic contract tests for the sealed M5.2 trial evidence harness.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
# shellcheck source=lib-test-work.sh
source "$SCRIPT_DIR/lib-test-work.sh"
prune_test_work "$TEST_ROOT" "skill-evaluation-harness" 2
TMP="$(mktemp -d "$TEST_ROOT/skill-evaluation-harness.XXXXXX")"
cleanup() {
  local rc=$?
  trap - EXIT
  finish_test_work "$rc" "$TMP" "harness"
  exit "$rc"
}
trap cleanup EXIT
HARNESS="$SCRIPT_DIR/skill-evaluation-harness.py"
ADAPTER="$SCRIPT_DIR/fake-skill-evaluation-adapter.py"
passes=0
pass() { echo "PASS  $*"; passes=$((passes + 1)); }
fail() { echo "FAIL  $*" >&2; exit 1; }

# CHK-A2 harness purity ratchet.
# docs/shadow-trial-authentication-boundary-design.md invariant I1 freezes this
# harness: the shadow trial authentication boundary is only sound if the
# harness that builds every trial environment is byte-unchanged from the
# reviewed integration base. This is a deliberate hard pin on a critical trust
# anchor. Changing the harness requires a separate reviewed work order that
# also updates HARNESS_PINNED_SHA256.
HARNESS_BASE_COMMIT="f63f55befa1e4a476ebf450444406ed1606cb750"
HARNESS_PINNED_SHA256="4b9b30524bf33be0d526130064ec426d61754c39f0eec24e8320637eb988ee57"
harness_working_sha256="$(shasum -a 256 "$HARNESS" | awk '{print $1}')"
if [ "$harness_working_sha256" != "$HARNESS_PINNED_SHA256" ]; then
  fail "unauthorized skill-evaluation-harness.py byte change: working tree is $harness_working_sha256 but the reviewed pin is $HARNESS_PINNED_SHA256 (CHK-A2, invariant I1)"
fi
pass "harness working-tree bytes match the reviewed CHK-A2 pin"

# The pin is compared against a second, independent source of the base bytes —
# the git object store — so this check cannot pass vacuously by deriving the
# expected and actual digests from the same file.
if ! git -C "$REPO_ROOT" rev-parse --verify --quiet "$HARNESS_BASE_COMMIT^{commit}" >/dev/null 2>&1; then
  fail "CHK-A2 ratchet source unavailable: integration base $HARNESS_BASE_COMMIT is not readable from $REPO_ROOT, so harness purity cannot be independently corroborated"
fi
harness_base_sha256="$(git -C "$REPO_ROOT" show "$HARNESS_BASE_COMMIT:skills/skill-review/scripts/skill-evaluation-harness.py" | shasum -a 256 | awk '{print $1}')"
if [ "$harness_base_sha256" != "$HARNESS_PINNED_SHA256" ]; then
  fail "CHK-A2 pin disagrees with integration base $HARNESS_BASE_COMMIT: base is $harness_base_sha256 but the pin is $HARNESS_PINNED_SHA256"
fi
pass "harness pin corroborated against the integration base object bytes"

scratch_dir() { local path="$TMP/scratch-$1"; rm -rf "$path"; mkdir -p "$path"; printf '%s\n' "$path"; }

harness_run() { "$HARNESS" run --input "$1" --output "$2" --routing "$TMP/routing.json" --scratch "$(scratch_dir "$(basename "$2")")"; }

harness_verify() { "$HARNESS" verify --result "$1" --scratch "$(scratch_dir "verify-$(basename "$1")")" --nonce fixture-nonce; }

state_of() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["state"])' "$1/manifest.json"; }

dump_result_diagnostics() {
  local label="$1" root="$2"
  python3 - "$label" "$root" <<'PY'
import json
import sys
from pathlib import Path

label, root_arg = sys.argv[1:]
root = Path(root_arg)
print(f"DIAGNOSTIC {label}: result={root}", file=sys.stderr)
manifest_path = root / "manifest.json"
if not manifest_path.is_file():
    print("DIAGNOSTIC manifest missing", file=sys.stderr)
    raise SystemExit
manifest = json.loads(manifest_path.read_text())
summary = {
    key: manifest.get(key)
    for key in ("state", "collection_state", "executor_states")
}
print(
    "DIAGNOSTIC manifest=" + json.dumps(summary, sort_keys=True, separators=(",", ":")),
    file=sys.stderr,
)
for result_path in sorted((root / "trials").glob("*/result.json")):
    result = json.loads(result_path.read_text())
    trial = {
        key: result.get(key)
        for key in (
            "executor",
            "case_id",
            "case_class",
            "treatment",
            "status",
            "errors",
            "infrastructure_error",
            "cleanup_failed",
            "shared_safety_failure",
        )
    }
    grader_path = result_path.with_name("grader-results.json")
    if grader_path.is_file():
        trial["grader_results"] = json.loads(grader_path.read_text()).get("results")
    print(
        "DIAGNOSTIC trial=" + json.dumps(trial, sort_keys=True, separators=(",", ":")),
        file=sys.stderr,
    )
PY
}

fail_result() {
  local label="$1" root="$2" message="$3"
  dump_result_diagnostics "$label" "$root" || true
  fail "$message"
}

reseal() { python3 - "$1" <<'PY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
def inventory():
    out = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_dir() or rel == "manifest.json":
            continue
        data = path.read_bytes()
        out.append({"path": rel, "sha256": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)})
    return out
manifest = json.load(open(root / "manifest.json"))
manifest["file_inventory"] = inventory()
manifest.pop("result_id", None)
manifest["result_id"] = "sha256:" + hashlib.sha256(
    json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
(root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
PY
}

make_run() {
  local root="$1" fixture="${2:-correct}" profile="${3:-gate}" executors="${4:-1}"
  local timeout="${5:-300}" command_grader="${6:-none}" behavior_cases="${7:-1}"
  local required_count="${8:-$executors}"
  local output_bytes="${9:-100000}"
  local comparator_timeout="${10:-120}"
  local rubric_marker="${11:-none}"
  mkdir -p "$root/candidate" "$root/fixtures" "$root/graders"
  cat > "$root/candidate/SKILL.md" <<'EOF'
---
name: fixture-skill
---
Return the deterministic fixture result.
EOF
  printf 'fixture\n' > "$root/fixtures/input.txt"
  python3 - "$root" "$fixture" "$profile" "$executors" "$timeout" "$HARNESS" "$ADAPTER" \
      "$command_grader" "$behavior_cases" "$required_count" "$output_bytes" \
      "$comparator_timeout" "$rubric_marker" <<'PY'
import hashlib, json, os, sys
from pathlib import Path
root, fixture, profile, executors, timeout, harness, adapter, command_grader, behavior_cases, required_count, output_bytes, comparator_timeout, rubric_marker = map(str, sys.argv[1:])
root = Path(root)
def canonical(x): return json.dumps(x, sort_keys=True, separators=(",", ":")).encode()
def sha(x): return "sha256:" + hashlib.sha256(canonical(x)).hexdigest()
def file_sha(p): return "sha256:" + hashlib.sha256(Path(p).read_bytes()).hexdigest()
def inv(base, exclude={"manifest.json"}):
    out=[]
    for p in sorted(base.rglob("*")):
        if p.is_dir() or p.relative_to(base).as_posix() in exclude: continue
        b=p.read_bytes()
        out.append({"path":p.relative_to(base).as_posix(),"sha256":"sha256:"+hashlib.sha256(b).hexdigest(),"size":len(b)})
    return out
adapter_sha=file_sha(adapter)
tool="sha256:"+"1"*64
adapter_id="sha256:"+"2"*64
candidate=[{"path":x["path"].removeprefix("candidate/"),"sha256":x["sha256"],"size":x["size"]} for x in inv(root) if x["path"].startswith("candidate/")]
grader_set = [
    {"id":"answer","type":"regex","safety":True,"config":{"required":["SUCCESS"],"forbidden":[]}},
    {"id":"artifact","type":"file","safety":True,"config":{"path":"out.txt","contains":"actual"}},
]
grader_ids=["answer","artifact"]
if command_grader != "none":
    program = root/"graders"/"check-artifact.py"
    program.write_text('''#!/usr/bin/env python3
import json, sys
from pathlib import Path
token, artifacts = sys.argv[1], Path(sys.argv[2])
target = artifacts / "out.txt"
present = target.is_file()
content = target.read_text() if present else ""
print(json.dumps({"passed": present and token in content,
                  "detail": {"token": token, "present": present}}, sort_keys=True))
''')
    os.chmod(program, 0o755)
    token = "actual" if command_grader == "pass" else "impossible-token"
    grader_set.append({"id":"command","type":"command","safety":True,
                       "config":{"argv":["check-artifact.py",token],"timeout_seconds":10,
                                 "program_sha256":file_sha(program)}})
    grader_ids.append("command")
rubric=(
    {"FixtureSkill criterion":"Choose the better response only by task quality."}
    if rubric_marker == "key"
    else {
        "id":"quality",
        "instruction": (
            "Judge FixtureSkill by task quality."
            if rubric_marker == "marker"
            else "Choose the better response only by task quality."
        ),
    }
)
cases=[]
for number in range(int(behavior_cases)):
    cases.append({"id":"behavior" if number==0 else f"behavior-{number+1}","class":"intended",
                  "task_id":f"task:behavior-{number+1:04d}","prompt":"Perform the task.","fixture":fixture,
                  "artifacts":["out.txt"],"graders":list(grader_ids),"semantic":True})
cases.extend([
 {"id":"activation-positive","class":"activation_positive","task_id":"task:activation-positive-0002","prompt":"Use the skill.","fixture":"correct","artifacts":["out.txt"],"graders":list(grader_ids),"semantic":False},
 {"id":"activation-negative","class":"activation_negative","task_id":"task:activation-negative-0003","prompt":"Do unrelated work.","fixture":fixture if fixture=="false-trigger" else "activation-negative","artifacts":["out.txt"],"graders":list(grader_ids),"semantic":False},
])
suite={"schema_version":1,"kind":"skill_evaluation_suite","grader_set_id":sha(grader_set),
       "identity_markers":["candidate-marker","fixture-skill"],"graders":grader_set,"cases":cases,"rubric":rubric}
(root/"suite.json").write_bytes(canonical(suite)+b"\n")
executor_values=[]
routing_executors=[]
for number in range(int(executors)):
    name=f"fixture-{number+1}"
    executor={"name":name,"requirement":"required" if number < int(required_count) else "advisory",
      "model":f"model-{number+1}","adapter_id":adapter_id,"adapter_version":1,
      "adapter_executable_sha256":adapter_sha,"cli_executable_sha256":"sha256:"+str(number+3)*64,
      "cli_version":f"cli-{number+1}","tool_policy_id":tool,"limits":{"timeout_seconds":int(timeout),"token_budget":100,"output_bytes":int(output_bytes)},"sandbox_id":"sha256:"+str(number+4)*64}
    identity={key:executor[key] for key in ("adapter_id","adapter_version","adapter_executable_sha256","model","cli_executable_sha256","cli_version","tool_policy_id","limits","sandbox_id")}
    identity_path=root.parent/f"{name}-identity.json"
    identity_path.write_bytes(canonical(identity)+b"\n")
    executor_values.append(executor)
    argv=[adapter,"--identity",str(identity_path)]
    if fixture == "mutate-input":
        argv += ["--mutate", str(root/"fixtures"/"input.txt")]
    routing_executors.append({"name":name,"adapter_id":adapter_id,"adapter_executable_sha256":adapter_sha,"argv":argv})
comparator={"route":"fixture-route","model":"judge-1","adapter_id":adapter_id,"adapter_version":1,"adapter_executable_sha256":adapter_sha,"timeout_seconds":int(comparator_timeout),"token_budget":100,"rubric_id":sha(rubric)}
comparator_path=root.parent/"comparator-identity.json"
comparator_path.write_bytes(canonical(comparator)+b"\n")
routing={"schema_version":1,"kind":"skill_evaluation_routing",
         "executors":routing_executors,
         "comparator":{"route":"fixture-route","adapter_id":adapter_id,"adapter_executable_sha256":adapter_sha,"argv":[adapter,"--identity",str(comparator_path)]}}
(root.parent/"routing.json").write_bytes(canonical(routing)+b"\n")
subject={"schema_version":1,"kind":"legacy_local_evaluation_subject_binding",
         "subject_key":sha("/fixture/skill"),"content_path":"/fixture/skill"}
manifest={"schema_version":2,"kind":"skill_evaluation_run","subject":subject,
 "input_manifest_sha256":None,"invocation_nonce":"fixture-nonce",
 "candidate_id":sha(candidate),"suite_id":sha(suite),"profile":profile,"trials_per_arm":3 if profile=="gate" else 1,
 "executors":executor_values,"comparator":comparator,"harness_executable_sha256":file_sha(harness),
 "tool_policy_id":tool,"grader_set_id":sha(grader_set),"retention_policy_id":"sha256:"+"b"*64,
 "limits":{"timeout_seconds":int(timeout),"output_bytes":int(output_bytes),"file_bytes":100000,"global_concurrency":1,"per_executor_concurrency":1},
 "file_inventory":inv(root)}
fields=("schema_version","kind","subject","input_manifest_sha256","candidate_id","suite_id","profile","trials_per_arm","executors","comparator",
        "harness_executable_sha256","tool_policy_id","grader_set_id","retention_policy_id","limits","file_inventory")
manifest["run_id"]=sha({key:manifest[key] for key in fields})
(root/"manifest.json").write_bytes(canonical(manifest)+b"\n")
PY
}

run_case() {
  local name="$1" fixture="${2:-correct}" profile="${3:-gate}" executors="${4:-1}"
  local timeout="${5:-120}" command_grader="${6:-none}" behavior_cases="${7:-1}"
  local required_count="${8:-$executors}" output_bytes="${9:-100000}"
  local input="$TMP/$name-input" output="$TMP/$name-output"
  mkdir -p "$output"
  make_run "$input" "$fixture" "$profile" "$executors" "$timeout" "$command_grader" \
    "$behavior_cases" "$required_count" "$output_bytes"
  harness_run "$input" "$output" >/dev/null
  printf '%s\n' "$output"
}

expect_manifest_refusal() {
  local name="$1" mutation="$2"
  local input="$TMP/$name-input" output="$TMP/$name-output"
  mkdir -p "$output"
  make_run "$input"
  python3 - "$input/manifest.json" "$mutation" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
mutation = sys.argv[2]
manifest = json.loads(path.read_text())
if mutation == "invalid-subject":
    manifest["subject"]["kind"] = "invalid"
elif mutation == "invalid-input-manifest":
    manifest["input_manifest_sha256"] = 123
elif mutation == "incomplete-remote-subject":
    manifest["subject"] = {
        "schema_version": 1,
        "kind": "remote_evaluation_subject_binding",
        "subject_key": "sha256:" + "9" * 64,
    }
elif mutation == "remote-candidate-mismatch":
    manifest["subject"] = {
        "schema_version": 1,
        "kind": "remote_evaluation_subject_binding",
        "subject_key": "sha256:" + "9" * 64,
        "origin_host_id": "remote-host",
        "origin_root_id": "personal",
        "origin_relative_path": "fixture-skill",
        "origin_path": "/remote/fixture-skill",
        "canonical_capability_id": "sha256:" + "8" * 64,
        "origin_inventory_sha256": "sha256:" + "7" * 64,
        "candidate_id": "wrong",
        "transport_receipt_sha256": "sha256:" + "5" * 64,
        "content_path": "/transport/fixture-skill",
    }
else:
    raise AssertionError(mutation)
fields = (
    "schema_version", "kind", "subject", "input_manifest_sha256",
    "candidate_id", "suite_id", "profile", "trials_per_arm", "executors",
    "comparator", "harness_executable_sha256", "tool_policy_id",
    "grader_set_id", "retention_policy_id", "limits", "file_inventory",
)
payload = json.dumps(
    {key: manifest[key] for key in fields},
    sort_keys=True,
    separators=(",", ":"),
).encode()
manifest["run_id"] = "sha256:" + hashlib.sha256(payload).hexdigest()
path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
PY
  if harness_run "$input" "$output" >/dev/null 2>&1; then
    fail "$name manifest ran"
  fi
  [[ -z "$(find "$output" -mindepth 1 -print -quit)" ]] ||
    fail "$name manifest created evidence"
}

expect_manifest_refusal invalid-subject invalid-subject
pass "invalid run subjects refuse before execution"
expect_manifest_refusal invalid-input-manifest invalid-input-manifest
pass "non-string input-manifest identities refuse without a traceback"
expect_manifest_refusal incomplete-remote-subject incomplete-remote-subject
pass "incomplete remote subjects refuse before execution"
expect_manifest_refusal remote-candidate-mismatch remote-candidate-mismatch
pass "malformed remote subject candidate identities refuse before execution"

python3 - "$HARNESS" <<'PY'
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("evaluation_harness", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
assert module.leaks_identity_marker(["fixture-skill"], "Use FixtureSkill")
assert module.leaks_identity_marker(["fixture-skill"], "Use fixtureskill")
assert not module.leaks_identity_marker(
    ["gaw"], "Refactor the parser so that debugging a workflow is easier."
)
assert module.leaks_identity_marker(["scout"], "We used scouts to survey.")
assert module.leaks_identity_marker(["scout"], "Start scouting the repository.")
assert module.leaks_identity_marker(["gaw"], "Use a gawbased pipeline.")
assert module.leaks_identity_marker(["pptx"], "Read the pptxgen output.")
assert module.leaks_identity_marker(["--"], "Keep -- literal")
PY

chmod +x "$HARNESS" "$ADAPTER"
export DREAMING_LEAK_CANARY=leaked
base="$(run_case base)"
harness_verify "$base" >/dev/null
[[ "$(state_of "$base")" == "complete" ]] ||
  fail_result "base completion" "$base" "valid result was not complete"
[[ "$(find "$base/trials" -name result.json | wc -l | tr -d ' ')" == "12" ]] ||
  fail "gate did not create three pairs plus activation trials"
python3 - "$base" <<'PY'
import json, sys
root=__import__("pathlib").Path(sys.argv[1])
records=[json.load(open(p)) for p in (root/"trials").glob("*/result.json")]
assert sum(x["case_id"]=="behavior" and x["treatment"]=="candidate" and x["status"]=="pass" for x in records) == 3
PY
pass "gate creates three matched trials per arm and sealed result verifies"

python3 - "$base" <<'PY'
import json, sys
root=sys.argv[1]
specs=[json.load(open(p)) for p in __import__("pathlib").Path(root, "trials").glob("*/trial.json")]
control=next(x for x in specs if x["treatment"]=="control")
candidate=next(x for x in specs if x["treatment"]=="candidate")
assert control["candidate_inventory"] == [] and control["candidate_root"] is None
assert candidate["candidate_inventory"] and candidate["candidate_root"]
trace=next(__import__("pathlib").Path(root, "trials").glob("*/trace.json"))
assert json.load(open(trace))["events"]
PY
pass "treatment isolation and normalized trace retain only the candidate projection"

tamper_input="$TMP/tamper-input"; tamper_output="$TMP/tamper-output"
mkdir -p "$tamper_output"; make_run "$tamper_input"
echo tampered >> "$tamper_input/candidate/SKILL.md"
if harness_run "$tamper_input" "$tamper_output" >/dev/null 2>&1; then
  fail "tampered sealed input reached adapters"
fi
[[ -z "$(find "$tamper_output" -mindepth 1 -print -quit)" ]] || fail "tampered input created output evidence"
pass "input tampering refuses before a trial process"

runid_input="$TMP/runid-input"; runid_output="$TMP/runid-output"
mkdir -p "$runid_output"; make_run "$runid_input"
python3 - "$runid_input" <<'PY'
import json, sys
from pathlib import Path
path=Path(sys.argv[1])/"manifest.json"
manifest=json.loads(path.read_text()); manifest["run_id"]="sha256:"+"c"*64
path.write_bytes(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()+b"\n")
PY
if harness_run "$runid_input" "$runid_output" >/dev/null 2>&1; then
  fail "non-canonical run_id was accepted"
fi
[[ -z "$(find "$runid_output" -mindepth 1 -print -quit)" ]] || fail "non-canonical run_id created evidence"
pass "run_id must equal the canonical digest of every sealed run input"

attest_input="$TMP/attest-input"; attest_output="$TMP/attest-output"
mkdir -p "$attest_output"; make_run "$attest_input"
python3 - "$TMP/comparator-identity.json" <<'PY'
import json, sys
from pathlib import Path
path=Path(sys.argv[1]); value=json.loads(path.read_text()); value["model"]="unauthorized-judge"
path.write_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()+b"\n")
PY
if harness_run "$attest_input" "$attest_output" >/dev/null 2>&1; then
  fail "unattested comparator identity was used"
fi
[[ -z "$(find "$attest_output" -mindepth 1 -print -quit)" ]] || fail "unattested comparator produced evidence"
pass "comparator route, model, adapter, budgets, and rubric are attested before any transfer"

missing="$(run_case missing-load missing-load iterate)"
wrong="$(run_case wrong-load wrong-load iterate)"
python3 - "$missing" "$wrong" <<'PY'
import json, sys
for root in sys.argv[1:]:
    records=[json.load(open(p)) for p in __import__("pathlib").Path(root, "trials").glob("*/result.json")]
    assert any(r["case_id"]=="behavior" and r["treatment"]=="candidate" and r["status"]=="invalid" for r in records)
PY
pass "correct outcomes without the exact loaded skill are invalid"

python3 - "$missing" <<'PY'
import json, sys
from pathlib import Path
root=Path(sys.argv[1])
comparisons=[json.load(open(p)) for p in (root/"comparisons").glob("*.json") if not p.name.endswith((".packet.json",".response.json"))]
assert comparisons and all(x["status"]=="inconclusive" for x in comparisons)
assert all("candidate_arm_load_unproved" in x["errors"] for x in comparisons)
assert all("assignment" not in x for x in comparisons)
assert not list((root/"comparisons").glob("*.response.json"))
assert not list((root/"comparisons").glob("*.packet.json"))
PY
pass "an unproved or invalid arm is never handed to the comparator"

false_trigger="$(run_case false-trigger false-trigger iterate)"
python3 - "$false_trigger" <<'PY'
import json, sys
records=[json.load(open(p)) for p in __import__("pathlib").Path(sys.argv[1], "trials").glob("*/result.json")]
assert any(x["case_id"]=="activation-negative" and x["status"]=="regression" for x in records)
PY
pass "activation-negative trials regress when any candidate load occurs"

mismatch="$(run_case effective-mismatch effective-model-mismatch iterate)"
python3 - "$mismatch" <<'PY'
import json, sys
root=__import__("pathlib").Path(sys.argv[1])
comparisons=[json.load(open(p)) for p in (root/"comparisons").glob("*.json") if not p.name.endswith((".packet.json",".response.json"))]
assert comparisons and all(x["status"]=="inconclusive" for x in comparisons)
records=[json.load(open(p)) for p in (root/"trials").glob("*/result.json")]
assert any("execution identity mismatch: model" in " ".join(x["errors"]) for x in records)
PY
pass "prepared or effective model mismatch invalidates the matched pair"

budget_mismatch="$(run_case budget-mismatch prepared-budget-mismatch iterate)"
python3 - "$budget_mismatch" <<'PY'
import json, sys
records=[json.load(open(p)) for p in __import__("pathlib").Path(sys.argv[1], "trials").glob("*/result.json")]
assert any("execution identity mismatch: limits" in " ".join(x["errors"]) for x in records)
PY
pass "pair budget mismatch invalidates evidence before scoring"

artifact="$(run_case artifact-missing artifact-missing iterate)"
collect="$(run_case collect-fail collect-fail iterate)"
python3 - "$artifact" "$collect" <<'PY'
import json, sys
def records(root): return [json.load(open(p)) for p in __import__("pathlib").Path(root,"trials").glob("*/result.json")]
assert any(x["case_id"]=="behavior" and x["treatment"]=="candidate" and x["status"]=="fail" for x in records(sys.argv[1]))
assert any(x["case_id"]=="behavior" and x["treatment"]=="candidate" and x["status"]=="inconclusive" for x in records(sys.argv[2]))
PY
pass "genuine missing artifacts fail while collection failures stay inconclusive"

leak="$(run_case identity-leak identity-leak iterate)"
python3 - "$leak" <<'PY'
import json, sys
root=__import__("pathlib").Path(sys.argv[1])
comparisons=[json.load(open(p)) for p in (root/"comparisons").glob("*.json") if not p.name.endswith((".packet.json",".response.json"))]
assert comparisons and all(x["status"]=="inconclusive" for x in comparisons)
assert not list((root/"comparisons").glob("*.response.json"))
PY
pass "identity-leaking blind packets are inconclusive before comparator invocation"

rubric_leak_input="$TMP/rubric-leak-input"
rubric_leak_output="$TMP/rubric-leak-output"
mkdir -p "$rubric_leak_output"
make_run "$rubric_leak_input" correct iterate 1 120 none 1 1 100000 120 marker
harness_run "$rubric_leak_input" "$rubric_leak_output" >/dev/null
python3 - "$rubric_leak_output" <<'PY'
import json, sys
root=__import__("pathlib").Path(sys.argv[1])
comparisons=[json.load(open(p)) for p in (root/"comparisons").glob("*.json") if not p.name.endswith((".packet.json",".response.json"))]
assert comparisons and all(x["status"]=="inconclusive" for x in comparisons)
assert not list((root/"comparisons").glob("*.response.json"))
PY
pass "identity markers in the rubric are refused before comparator invocation"

rubric_key_leak_input="$TMP/rubric-key-leak-input"
rubric_key_leak_output="$TMP/rubric-key-leak-output"
mkdir -p "$rubric_key_leak_output"
make_run "$rubric_key_leak_input" correct iterate 1 120 none 1 1 100000 120 key
harness_run "$rubric_key_leak_input" "$rubric_key_leak_output" >/dev/null
python3 - "$rubric_key_leak_output" <<'PY'
import json, sys
root=__import__("pathlib").Path(sys.argv[1])
comparisons=[json.load(open(p)) for p in (root/"comparisons").glob("*.json") if not p.name.endswith((".packet.json",".response.json"))]
assert comparisons and all(x["status"]=="inconclusive" for x in comparisons)
assert not list((root/"comparisons").glob("*.response.json"))
PY
pass "identity markers in rubric keys are refused before comparator invocation"

python3 - "$base" <<'PY'
import json, sys
root=__import__("pathlib").Path(sys.argv[1])
items=[json.load(open(p)) for p in (root/"comparisons").glob("*.json") if p.name.endswith(".json") and not p.name.endswith((".packet.json",".response.json"))]
assert items and all(x["status"]=="complete" and set(x["assignment"])=={"A","B"} for x in items)
assert list((root/"comparisons").glob("*.response.json"))
manifest=json.load(open(root/"manifest.json"))
assert set(manifest["comparator_identity"]) == {"route","model","adapter_id","adapter_version",
    "adapter_executable_sha256","timeout_seconds","token_budget","rubric_id"}
assert all(x["comparator"] == manifest["comparator_identity"] for x in items)
assert set(manifest["producer_audit"]) == {"routing_config_sha256","executor_argv_sha256",
    "comparator_argv_sha256","environment_sha256"}
PY
pass "valid blind comparison records assignment, comparator identity, and unblinded response"

python3 - "$base" <<'PY'
import json, sys
from pathlib import Path
root=Path(sys.argv[1])
manifest=json.load(open(root/"manifest.json"))
for pair in manifest["pairs"]:
    stem=pair.removeprefix("sha256:")
    observed=json.loads(json.load(open(root/"comparisons"/f"{stem}.response.json"))["evidence"])
    surface=json.dumps(observed)
    assert stem not in surface, "comparator observed the pair identity"
    for leak in ("control","candidate","trials","treatment"):
        assert leak not in surface, f"comparator observed {leak}"
    assert str(root) not in observed["home"], "comparator home was inside the result tree"
    assert not any(str(root) in item for item in observed["argv"]), "comparator argv pointed into the result tree"
    assert str(root) not in observed["cwd"], "comparator cwd was inside the result tree"
    assert {"HOME","LANG","LC_ALL","PATH"} <= set(observed["environment"])
    assert "DREAMING_LEAK_CANARY" not in observed["environment"], "caller environment leaked to the comparator"
PY
pass "comparator argv, cwd, and home reveal no pair identity or treatment"

command_pass="$(run_case command-pass correct iterate 1 120 pass)"
harness_verify "$command_pass" >/dev/null
if ! python3 - "$command_pass" <<'PY'
import json, sys
from pathlib import Path
root=Path(sys.argv[1])
records=[json.load(open(p)) for p in (root/"trials").glob("*/result.json")]
assert any(x["case_id"]=="behavior" and x["treatment"]=="candidate" and x["status"]=="pass" for x in records)
grades=[json.load(open(p)) for p in (root/"trials").glob("*/grader-results.json")]
command=[item for g in grades for item in g["results"] if item["type"]=="command"]
assert command and all(item["passed"] for item in command)
assert (root/"graders"/"check-artifact.py").is_file()
PY
then
  fail_result "command grader pass" "$command_pass" \
    "passing sealed command grader did not produce passing trial evidence"
fi
pass "sealed command graders run, seal their program bytes, and replay under verification"

command_fail="$(run_case command-fail correct iterate 1 120 fail)"
if ! python3 - "$command_fail" <<'PY'
import json, sys
records=[json.load(open(p)) for p in __import__("pathlib").Path(sys.argv[1],"trials").glob("*/result.json")]
assert any(x["case_id"]=="behavior" and x["treatment"]=="candidate" and x["status"]=="fail" for x in records)
PY
then
  fail_result "command grader fail" "$command_fail" \
    "failing sealed command grader did not produce failing trial evidence"
fi
pass "a failing sealed command grader fails the trial outcome"

command_tamper="$TMP/command-tamper-output"; cp -R "$command_pass" "$command_tamper"
python3 - "$command_tamper" <<'PY'
import sys
from pathlib import Path
program=Path(sys.argv[1])/"graders"/"check-artifact.py"
program.chmod(0o700)
program.write_text(program.read_text() + "# tampered\n")
PY
reseal "$command_tamper"
if harness_verify "$command_tamper" >/dev/null 2>&1; then
  fail "tampered sealed command grader program verified"
fi
pass "a substituted command grader program fails verification after resealing"

command_replay="$TMP/command-replay-output"; cp -R "$command_pass" "$command_replay"
python3 - "$command_replay" <<'PY'
import json, sys
from pathlib import Path
for path in Path(sys.argv[1], "trials").glob("*/grader-results.json"):
    value=json.load(open(path))
    for item in value["results"]:
        if item["type"] == "command":
            item["passed"] = not item["passed"]
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":"))+"\n")
PY
reseal "$command_replay"
if harness_verify "$command_replay" >/dev/null 2>&1; then
  fail "falsified command grader result verified"
fi
pass "verification reruns command graders and rejects a falsified command result"

missing_grades="$TMP/missing-grades-output"; cp -R "$command_pass" "$missing_grades"
python3 - "$missing_grades" <<'PY'
import sys
from pathlib import Path
next(Path(sys.argv[1], "trials").glob("*/grader-results.json")).unlink()
PY
reseal "$missing_grades"
if harness_verify "$missing_grades" >/dev/null 2>&1; then
  fail "scored trial without grader results verified"
fi
pass "a deleted grader result fails verification even after resealing"

missing_trial="$TMP/missing-trial-output"; cp -R "$command_pass" "$missing_trial"
python3 - "$missing_trial" <<'PY'
import json, shutil, sys
from pathlib import Path
root=Path(sys.argv[1])
manifest=json.load(open(root/"manifest.json"))
victim=manifest["trials"][0]
shutil.rmtree(root/"trials"/victim.removeprefix("sha256:"))
manifest["trials"]=[x for x in manifest["trials"] if x != victim]
(root/"manifest.json").write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":"))+"\n")
PY
reseal "$missing_trial"
if harness_verify "$missing_trial" >/dev/null 2>&1; then
  fail "deleted trial verified after resealing"
fi
pass "a deleted trial fails the regenerated trial matrix even after resealing"

mutate="$(run_case mutate-input mutate-input iterate 1 120 pass)"
python3 - "$mutate" <<'PY'
import json, sys
from pathlib import Path
root=Path(sys.argv[1])
records=[json.load(open(p)) for p in (root/"trials").glob("*/result.json")]
assert any("sealed input changed before command graders" in " ".join(x["errors"]) for x in records)
aggregate=json.load(open(root/"aggregate.json"))
assert "sealed input changed before result sealing" in aggregate["infrastructure"]["input_recheck"]
PY
[[ "$(state_of "$mutate")" == "incomplete" ]] || fail "mutated sealed input produced a complete result"
pass "input mutation is rechecked before command graders and before sealing"

flood="$(run_case flood output-flood iterate 1 120 none 1 1 4096)"
python3 - "$flood" <<'PY'
import json, sys
records=[json.load(open(p)) for p in __import__("pathlib").Path(sys.argv[1],"trials").glob("*/result.json")]
errors=" ".join(e for x in records for e in x["errors"])
assert "adapter output exceeds bound" in errors, errors
assert "adapter timeout" not in errors, errors
PY
[[ "$(state_of "$flood")" == "incomplete" ]] || fail "flooding adapter produced a complete result"
pass "streaming capture stops an adapter at its output byte cap before its timeout"

iterate="$(run_case iterate correct iterate)"
python3 - "$iterate" <<'PY'
import json, sys
root=__import__("pathlib").Path(sys.argv[1])
records=[json.load(open(p)) for p in (root/"trials").glob("*/result.json")]
assert len(records)==4 and all(not x["authoritative"] for x in records)
assert json.load(open(root/"aggregate.json"))["authoritative"] is False
PY
pass "iterate runs exactly one visibly non-authoritative trial"

two="$(run_case two-executors correct iterate 2)"
python3 - "$two" <<'PY'
import json, sys
aggregate=json.load(open(__import__("pathlib").Path(sys.argv[1])/"aggregate.json"))
assert sorted(aggregate["executors"]) == ["fixture-1","fixture-2"]
assert not {"counts","comparison_counts","deltas","separate_executor_dimensions"} & set(aggregate)
assert set(aggregate["infrastructure"]) == {
    "input_recheck","infrastructure_errors","cleanup_failures",
    "shared_safety_failures","required_state","collection_state","executor_states",
}
for name, block in aggregate["executors"].items():
    assert block["requirement"] == "required"
    assert block["state"] == "complete"
    classes=block["case_classes"]
    assert set(classes) == {"intended","activation_positive","activation_negative"}
    assert classes["intended"]["trial_counts"] == {"pass":1,"fail":1,"invalid":0,"regression":0,"inconclusive":0}
    assert classes["intended"]["treatment_counts"]["candidate"]["pass"] == 1
    assert classes["intended"]["treatment_counts"]["control"]["fail"] == 1
    assert classes["intended"]["skill_load_proved"] == 2
    assert classes["intended"]["comparison_counts"]["complete"] == 1
    assert "activation_negative" in classes and classes["activation_negative"]["comparison_counts"]["complete"] == 0
PY
pass "aggregate diagnostics stay partitioned per executor and case class"

timeout="$(run_case timeout timeout iterate 1 3)"
[[ "$(state_of "$timeout")" == "incomplete" ]] || fail "timeout result looked complete"
[[ ! -d "$(find "$timeout/trials" -type d -name workspace -print -quit)" ]] || fail "timeout workspace was not cleaned"
while IFS= read -r pid; do
  ! kill -0 "$pid" 2>/dev/null || fail "timeout left child process $pid running"
done < <(find "$timeout/trials" -name child.pid -exec cat {} \;)
pass "timeout cancels owned process group and leaves incomplete evidence"

oversized_input="$TMP/oversized-input"; oversized_output="$TMP/oversized-output"
mkdir -p "$oversized_output"; make_run "$oversized_input" correct gate 3 120 none 60
if harness_run "$oversized_input" "$oversized_output" >/dev/null 2>&1; then
  fail "oversized projected matrix ran"
fi
[[ -z "$(find "$oversized_output" -mindepth 1 -print -quit)" ]] || fail "oversized matrix created evidence"
pass "an oversized projected matrix is refused before any trial process"

advisory_input="$TMP/advisory-input"; advisory_output="$TMP/advisory-output"
mkdir -p "$advisory_output"
make_run "$advisory_input" correct iterate 2 120 none 1 1
python3 - "$TMP/routing.json" <<'PY'
import json, sys
path=sys.argv[1]
value=json.load(open(path))
value["executors"][1]["argv"] += ["--fixture", "collect-fail"]
open(path,"w").write(json.dumps(value,sort_keys=True,separators=(",",":"))+"\n")
PY
harness_run "$advisory_input" "$advisory_output" >/dev/null
harness_verify "$advisory_output" >/dev/null
python3 - "$advisory_output" <<'PY'
import json, sys
root=sys.argv[1]
manifest=json.load(open(root+"/manifest.json"))
aggregate=json.load(open(root+"/aggregate.json"))
assert manifest["state"]=="complete", manifest
assert manifest["collection_state"]=="incomplete", manifest
assert manifest["executor_states"]["fixture-1"]["state"]=="complete", manifest["executor_states"]
assert manifest["executor_states"]["fixture-2"]["state"]=="incomplete", manifest["executor_states"]
assert aggregate["infrastructure"]["required_state"]=="complete", aggregate["infrastructure"]
assert aggregate["executors"]["fixture-1"]["requirement"]=="required", aggregate["executors"]
assert aggregate["executors"]["fixture-2"]["requirement"]=="advisory", aggregate["executors"]
PY
pass "advisory infrastructure failure leaves required evidence complete"

no_required_input="$TMP/no-required-input"; no_required_output="$TMP/no-required-output"
mkdir -p "$no_required_output"
make_run "$no_required_input" correct iterate 1 120 none 1 0
if harness_run "$no_required_input" "$no_required_output" >/dev/null 2>&1; then
  fail "empty required executor set ran"
fi
[[ -z "$(find "$no_required_output" -mindepth 1 -print -quit)" ]] ||
  fail "empty required executor set created evidence"
pass "an empty required executor set refuses before execution"

realistic="$(run_case realistic correct gate 3 120 none 2)"
harness_verify "$realistic" >/dev/null
[[ "$(state_of "$realistic")" == "complete" ]] || fail "realistic three-executor gate suite did not complete"
python3 - "$realistic" <<'PY'
import json, sys
from pathlib import Path
root=Path(sys.argv[1])
manifest=json.load(open(root/"manifest.json"))
assert len(manifest["trials"]) == 54, len(manifest["trials"])
assert len(manifest["pairs"]) == 18, len(manifest["pairs"])
assert len(manifest["file_inventory"]) > 256, len(manifest["file_inventory"])
assert sorted(json.load(open(root/"aggregate.json"))["executors"]) == ["fixture-1","fixture-2","fixture-3"]
PY
pass "a realistic three-executor gate suite seals within the split result bound"

# Re-seal an altered grader result to prove verifier recomputation, not just inventory checks.
python3 - "$base" <<'PY'
import json, sys
from pathlib import Path
for path in Path(sys.argv[1], "trials").glob("*/grader-results.json"):
    value=json.load(open(path))
    passed=next((item for item in value["results"] if item["passed"]), None)
    if passed is not None:
        passed["passed"]=False
        break
else:
    raise SystemExit("fixture produced no passing grader result to falsify")
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":"))+"\n")
PY
reseal "$base"
if harness_verify "$base" >/dev/null 2>&1; then
  fail "falsified deterministic grader result verified"
fi
pass "verifier recomputes deterministic grades after a resealed falsification"

# Re-sealing cannot alter the derived blind assignment.
assignment_tamper="$(run_case assignment-tamper correct iterate)"
python3 - "$assignment_tamper" <<'PY'
import hashlib, json, sys
from pathlib import Path
root=Path(sys.argv[1])
comparison=next(
    p for p in (root/"comparisons").glob("*.json")
    if not p.name.endswith((".packet.json", ".response.json"))
)
d=json.load(open(comparison))
d["assignment"]={"A":d["assignment"]["B"],"B":d["assignment"]["A"]}
comparison.write_text(json.dumps(d,sort_keys=True,separators=(",",":"))+"\n")
def inv():
    out=[]
    for p in sorted(root.rglob("*")):
        rel=p.relative_to(root).as_posix()
        if p.is_dir() or rel=="manifest.json":
            continue
        b=p.read_bytes()
        out.append({"path":rel,"sha256":"sha256:"+hashlib.sha256(b).hexdigest(),"size":len(b)})
    return out
m=json.load(open(root/"manifest.json"))
m["file_inventory"]=inv()
m.pop("result_id")
m["result_id"]="sha256:"+hashlib.sha256(
    json.dumps(m,sort_keys=True,separators=(",",":")).encode()
).hexdigest()
(root/"manifest.json").write_text(json.dumps(m,sort_keys=True,separators=(",",":"))+"\n")
PY
if harness_verify "$assignment_tamper" >/dev/null 2>&1; then
  fail "resealed blind assignment tamper verified"
fi
pass "verifier re-derives blind assignment and packet from sealed trial evidence"

sealed_subject_tamper="$(run_case sealed-subject-tamper correct iterate)"
python3 - "$sealed_subject_tamper/manifest.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
manifest = json.loads(path.read_text())
manifest["subject"]["content_path"] = "/other/skill"
path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
PY
reseal "$sealed_subject_tamper"
if harness_verify "$sealed_subject_tamper" >/dev/null 2>&1; then
  fail "resealed result subject mismatch verified"
fi
pass "verifier rejects a resealed result subject differing from sealed input"

echo "PASS  $passes deterministic skill-evaluation-harness checks"
