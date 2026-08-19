#!/usr/bin/env bash
# Deterministic M5.4 Dreaming compilation, verification, policy, and authority checks.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
# shellcheck source=lib-test-work.sh
source "$SCRIPT_DIR/lib-test-work.sh"
prune_test_work "$TEST_ROOT" "dreaming-certification" 2
TMP="$(mktemp -d "$TEST_ROOT/dreaming-certification.XXXXXX")"
cleanup() {
  local rc=$?
  trap - EXIT
  finish_test_work "$rc" "$TMP" "certification"
  exit "$rc"
}
trap cleanup EXIT
EVAL="$SCRIPT_DIR/skill-evaluation.py"
HARNESS="$SCRIPT_DIR/skill-evaluation-harness.py"
ADAPTER="$SCRIPT_DIR/fake-skill-evaluation-adapter.py"
export SKILLS_STATE_DIR="$TMP/state"
export DREAMING_EVALUATION_EXECUTORS="copilot"
export DREAMING_ADVISORY_EVALUATION_EXECUTORS="claude,codex"
passes=0

pass() { echo "PASS  $*"; passes=$((passes + 1)); }
fail() { echo "FAIL  $*" >&2; exit 1; }

dump_certification_diagnostics() {
  local label="$1" root="$2" certification_path="$3"
  python3 - "$label" "$root" "$certification_path" <<'PY'
import json
import sys
from pathlib import Path

label, root_arg, certification_arg = sys.argv[1:]
root = Path(root_arg)
certification_path = Path(certification_arg)
print(f"DIAGNOSTIC {label}: fixture={root}", file=sys.stderr)
if certification_path.is_file():
    certification = json.loads(certification_path.read_text())
    summary = {
        key: certification.get(key)
        for key in ("status", "authoritative", "aggregate")
    }
    print(
        "DIAGNOSTIC certification="
        + json.dumps(summary, sort_keys=True, separators=(",", ":")),
        file=sys.stderr,
    )
manifest_path = root / "result" / "manifest.json"
if not manifest_path.is_file():
    print("DIAGNOSTIC result manifest missing", file=sys.stderr)
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
for result_path in sorted((root / "result" / "trials").glob("*/result.json")):
    result = json.loads(result_path.read_text())
    if result.get("case_class") not in {"activation_positive", "activation_negative"}:
        continue
    trial = {
        key: result.get(key)
        for key in (
            "executor",
            "case_id",
            "case_class",
            "treatment",
            "status",
            "skill_load_proved",
            "errors",
            "infrastructure_error",
            "cleanup_failed",
            "shared_safety_failure",
        )
    }
    print(
        "DIAGNOSTIC activation_trial="
        + json.dumps(trial, sort_keys=True, separators=(",", ":")),
        file=sys.stderr,
    )
PY
}

expect_refusal() {
  local name="$1" expected="$2"
  shift 2
  if "$@" >"$TMP/$name.out" 2>"$TMP/$name.err"; then
    fail "$name unexpectedly succeeded"
  fi
  grep -q "$expected" "$TMP/$name.err" ||
    fail "$name omitted expected refusal: $expected"
}

bash "$SCRIPT_DIR/test-evaluation-input-claims.sh"
pass "aggregate evaluation-input claim ledger foundation is fail closed"

make_fixture() {
  local root="$1" profile="${2:-gate}" kind="${3:-capability_uplift}" fixture="${4:-correct}"
  mkdir -p "$root/skill" "$root/config/fixtures" "$root/config/graders"
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
 "adapter_version":1,"adapter_executable_sha256":adapter_sha,"timeout_seconds":120,
 "token_budget":100,"rubric_id":sha(rubric)}
required_executors=[]; advisory_executors=[]; compiled_executors=[]; routes=[]
for number, name in enumerate(("copilot","claude","codex"), 1):
    base={"name":name,"model":f"{name}-model-1","adapter_id":adapter_id,"adapter_version":1,
          "adapter_executable_sha256":adapter_sha,"cli_executable_sha256":"sha256:"+str(number+2)*64}
    requirement="required" if name == "copilot" else "advisory"
    full={**base,"requirement":requirement,"cli_version":f"{name}-cli-1","tool_policy_id":tool,
          "limits":{"timeout_seconds":120,"token_budget":100,"output_bytes":100000},
          "sandbox_id":"sha256:"+str(number+5)*64}
    identity={key:value for key,value in full.items() if key not in {"name","requirement"}}
    identity_path=config_root/f"{name}-identity.json"
    identity_path.write_bytes(canonical(identity)+b"\n")
    (required_executors if requirement == "required" else advisory_executors).append(base)
    compiled_executors.append(full)
    routes.append({"name":name,"adapter_id":adapter_id,"adapter_executable_sha256":adapter_sha,
                   "argv":[adapter,"--identity",str(identity_path)]})
policy={"schema_version":2,"profile":profile,"policy_kind":kind,
        "required_executors":required_executors,"advisory_executors":advisory_executors,
        "comparator":comparator}
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
 "limits":{"timeout_seconds":120,"output_bytes":100000,"file_bytes":100000,
           "global_concurrency":1,"per_executor_concurrency":1},
 "identity_markers":["candidate-marker","fixture-skill"],"graders":graders,
 "case_runtime":runtime,"rubric":rubric,"executors":compiled_executors,
 "comparator":comparator}
config_root.joinpath("compilation.json").write_bytes(canonical(config)+b"\n")
fixture_entries=[]
for fixture_id in sorted({item["fixture"] for item in runtime}):
    fixture_path=config_root/f"fixtures/{fixture_id}.json"
    fixture_content=canonical(
        {"schema_version":1,"kind":"synthetic_fixture","fixture":fixture_id}
    )+b"\n"
    fixture_path.write_bytes(fixture_content)
    fixture_entries.append(
        {"id":fixture_id,"path":fixture_path.relative_to(config_root/"fixtures").as_posix(),
         "sha256":"sha256:"+hashlib.sha256(fixture_content).hexdigest(),
         "size":len(fixture_content),"source_kind":"synthetic",
         "description":f"Synthetic deterministic {fixture_id} trial fixture."}
    )
config_root.joinpath("graders/contracts.json").write_bytes(
 canonical({"schema_version":1,"kind":"deterministic_grader_contracts","graders":graders})+b"\n")
catalog={"schema_version":1,"kind":"safe_evaluation_source_catalog",
 "fixtures":fixture_entries,
 "graders":[{"id":item["id"],"objective":True,
             "description":f"Deterministic {item['type']} outcome check."} for item in graders],
 "rubric":{"identity":sha(rubric),
           "description":"Allowlisted paired quality comparison rubric."}}
config_root.joinpath("authoring-catalog.json").write_bytes(canonical(catalog))
PY
}

publish_fixture() {
  local root="$1"
  local registration manifest validation review_one review_two ready
  registration="$(
    "$EVAL" v2-input-register "$root/skill" \
      --suite "$root/skill/.skill-evaluation-cases.json" \
      --policy "$root/skill/.skill-evaluation-policy.json" \
      --config "$root/config/compilation.json" \
      --routing "$root/config/routing.json" \
      --harness "$HARNESS" \
      --authoring-method deterministic-fixture \
      --source-id synthetic:certification-fixture
  )"
  manifest="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["input_manifest_sha256"])' <<<"$registration")"
  validation="$(
    "$EVAL" v2-input-validate "$root/skill" --manifest "$manifest"
  )"
  review_one="$(
    "$EVAL" v2-input-review "$root/skill" --manifest "$manifest" \
      --reviewer fixture-reviewer-one --decision accept
  )"
  review_two="$(
    "$EVAL" v2-input-review "$root/skill" --manifest "$manifest" \
      --reviewer fixture-reviewer-two --decision accept
  )"
  ready="$(
    "$EVAL" v2-input-ready "$root/skill" --manifest "$manifest" \
      --validation "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' <<<"$validation")" \
      --review "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' <<<"$review_one")" \
      --review "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' <<<"$review_two")" \
      --created-at 2026-01-01T00:00:00Z
  )"
  python3 - "$root/input-registry.json" \
    "$registration" "$validation" "$review_one" "$review_two" "$ready" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
names = ("registration", "validation", "review_one", "review_two", "ready")
path.write_text(
    json.dumps(
        {name: json.loads(value) for name, value in zip(names, sys.argv[2:])},
        sort_keys=True,
        separators=(",", ":"),
    )
    + "\n"
)
PY
}

run_fixture() {
  local root="$1" nonce="$2"
  publish_fixture "$root"
  mkdir -p "$root/run" "$root/result" "$root/run-scratch" "$root/verify-scratch"
  mv "$root/config/compilation.json" "$root/compilation.authoring"
  mv "$root/config/routing.json" "$root/routing.authoring"
  "$EVAL" v2-run-compile "$root/skill" --run-dir "$root/run" \
    --nonce "$nonce" --harness "$HARNESS" >/dev/null
  "$EVAL" v2-run-execute --run-dir "$root/run" --result-dir "$root/result" \
    --scratch "$root/run-scratch" --harness "$HARNESS" >/dev/null
  local certification
  certification="$(
    "$EVAL" v2-result-certify "$root/skill" --run-dir "$root/run" \
      --result-dir "$root/result" --scratch "$root/verify-scratch" \
      --nonce "$nonce" --harness "$HARNESS"
  )"
  mv "$root/compilation.authoring" "$root/config/compilation.json"
  mv "$root/routing.authoring" "$root/config/routing.json"
  printf '%s\n' "$certification"
}

skill_tree_digest() {
  python3 - "$1" <<'PY'
import hashlib
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
digest = hashlib.sha256()
for path in sorted(root.rglob("*")):
    relative = path.relative_to(root).as_posix().encode()
    digest.update(relative + b"\0")
    if path.is_symlink():
        digest.update(b"link\0" + os.readlink(path).encode())
    elif path.is_file():
        digest.update(b"file\0" + path.read_bytes())
    elif path.is_dir():
        digest.update(b"dir\0")
print(digest.hexdigest())
PY
}

author_packet() {
  local root="$1" output="$2"
  "$EVAL" v2-input-author-packet "$root/skill" \
    --suite "$root/skill/.skill-evaluation-cases.json" \
    --policy "$root/skill/.skill-evaluation-policy.json" \
    --config "$root/config/compilation.json" \
    --routing "$root/config/routing.json" \
    --harness "$HARNESS" \
    --catalog "$root/config/authoring-catalog.json" \
    --output "$output"
}

AUTHORING="$TMP/authoring"
make_fixture "$AUTHORING"
authoring_skill_before="$(skill_tree_digest "$AUTHORING/skill")"
author_packet "$AUTHORING" "$AUTHORING/packet.json" >/dev/null
[[ "$(skill_tree_digest "$AUTHORING/skill")" == "$authoring_skill_before" ]] ||
  fail "authoring packet changed the candidate root"
python3 - "$AUTHORING/packet.json" <<'PY'
import hashlib
import json
import sys

packet = json.load(open(sys.argv[1]))
packet_id = packet.pop("packet_id")
canonical = json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()
assert packet_id == "sha256:" + hashlib.sha256(canonical).hexdigest()
assert packet["kind"] == "safe_evaluation_input_authoring_packet"
assert packet["skill_contract"]["logical_path"] == "SKILL.md"
assert packet["candidate_inventory"]
assert {item["class"] for item in packet["suite_template"]["cases"]} == {
    "intended", "related", "activation_positive", "activation_negative"
}
serialized = json.dumps(packet, sort_keys=True)
assert "/Users/" not in serialized
assert '"argv"' not in serialized
assert '"identity_markers"' not in serialized
assert '"instruction"' not in serialized
assert "fixture-session" not in serialized
PY

make_authoring_draft() {
  local packet="$1" output="$2" mode="${3:-valid}"
  python3 - "$packet" "$output" "$mode" <<'PY'
import json
import sys
from pathlib import Path

packet_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
mode = sys.argv[3]
packet = json.loads(packet_path.read_text())
runtime = {
    item["id"]: item
    for item in packet["compilation_contract"]["case_runtime"]
}
cases = []
for index, template in enumerate(packet["suite_template"]["cases"], 1):
    case_runtime = runtime[template["id"]]
    cases.append(
        {
            "id": template["id"],
            "class": template["class"],
            "task_id": f"authored:{template['class']}-{index:04d}",
            "prompt": f"Complete the synthetic {template['class']} task {index}.",
            "deterministic_graders": template["deterministic_graders"],
            "fixture": case_runtime["fixture"],
            "artifacts": case_runtime["artifacts"],
            "semantic": case_runtime["semantic"],
        }
    )
if mode == "fixture":
    cases[0]["fixture"] = "undeclared"
elif mode == "grader":
    cases[0]["deterministic_graders"] = ["undeclared"]
elif mode == "artifact":
    cases[0]["artifacts"] = ["/Users/alice/private.txt"]
elif mode == "semantic":
    cases[0]["semantic"] = not cases[0]["semantic"]
elif mode == "missing":
    cases.pop()
elif mode == "sensitive":
    cases[0]["prompt"] = "Use raw transcript copied from a private session."
elif mode == "duplicate-task":
    cases[1]["task_id"] = cases[0]["task_id"]
elif mode == "duplicate-prompt":
    cases[1]["prompt"] = cases[0]["prompt"]
draft = {
    "schema_version": 1,
    "kind": "safe_evaluation_input_draft",
    "packet_id": packet["packet_id"],
    "candidate_id": packet["candidate_id"],
    "cases": cases,
}
output_path.write_text(json.dumps(draft, sort_keys=True, separators=(",", ":")))
PY
}

materialize_authoring() {
  local root="$1" packet="$2" draft="$3" output="$4"
  "$EVAL" v2-input-author-materialize "$root/skill" \
    --suite "$root/skill/.skill-evaluation-cases.json" \
    --policy "$root/skill/.skill-evaluation-policy.json" \
    --config "$root/config/compilation.json" \
    --routing "$root/config/routing.json" \
    --harness "$HARNESS" \
    --catalog "$root/config/authoring-catalog.json" \
    --packet "$packet" --draft "$draft" --output-dir "$output"
}

make_authoring_draft "$AUTHORING/packet.json" "$AUTHORING/draft.json"
materialization="$(
  materialize_authoring "$AUTHORING" "$AUTHORING/packet.json" \
    "$AUTHORING/draft.json" "$AUTHORING/materialized"
)"
[[ "$(skill_tree_digest "$AUTHORING/skill")" == "$authoring_skill_before" ]] ||
  fail "trusted materialization changed the candidate root"
python3 - "$AUTHORING/packet.json" "$AUTHORING/materialized" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

packet = json.load(open(sys.argv[1]))
root = Path(sys.argv[2])
def inventory(tree):
    values = []
    for path in sorted(tree.rglob("*")):
        if path.is_file():
            content = path.read_bytes()
            values.append(
                {
                    "path": path.relative_to(tree).as_posix(),
                    "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                }
            )
    return values
expected_fixtures = sorted(
    [
        {key: item[key] for key in ("path", "sha256", "size")}
        for item in packet["source_catalog"]["fixtures"]
    ],
    key=lambda item: item["path"],
)
assert inventory(root / "fixtures") == expected_fixtures
assert (
    inventory(root / "graders")
    == packet["source_catalog"]["grader_tree_inventory"]
)
PY
source_catalog_id="$(
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_catalog_id"])' \
    "$AUTHORING/packet.json"
)"
python3 - "$AUTHORING/packet.json" "$AUTHORING/draft.json" \
  "$SCRIPT_DIR/dreaming-vendor-adapter.py" "$AUTHORING/author-operation.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

packet_path, draft_path, adapter_path, output_path = map(Path, sys.argv[1:])
packet = json.loads(packet_path.read_text())
draft = json.loads(draft_path.read_text())
governance = lambda value: json.dumps(
    value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
).encode()
adapter = lambda value: json.dumps(
    value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
).encode()
operation = {
    "schema_version": 1,
    "kind": "evaluation_input_model_operation",
    "operation": "author",
    "status": "completed",
    "vendor": "copilot",
    "model": "fixture-author-model",
    "observed_model": "fixture-author-model",
    "adapter_executable_sha256": "sha256:" + hashlib.sha256(
        adapter_path.read_bytes()
    ).hexdigest(),
    "packet_id": packet["packet_id"],
    "candidate_id": packet["candidate_id"],
    "outcome": "draft",
    "summary": "Safe synthetic fixture cases.",
    "reason": None,
    "draft_id": "sha256:" + hashlib.sha256(governance(draft)).hexdigest(),
    "usage": {
        "normalized_tokens": 140,
        "input_tokens": 100,
        "output_tokens": 40,
    },
    "billing": {
        "status": "unavailable",
        "cost_usd": None,
        "provider": "copilot",
        "unavailable_reason": "provider_telemetry_unavailable",
        "native_line_item_id": None,
        "native_event_sha256": None,
        "native_event_size": None,
    },
    "elapsed_ms": 100,
}
operation["operation_id"] = "sha256:" + hashlib.sha256(adapter(operation)).hexdigest()
output_path.write_text(json.dumps(operation, sort_keys=True, separators=(",", ":")))
PY
author_operation_id="$(
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["operation_id"])' \
    "$AUTHORING/author-operation.json"
)"
mkdir -p "$AUTHORING/author-bin"
cat >"$AUTHORING/author-bin/copilot" <<'PY'
#!/usr/bin/env python3
import json
import os
import sys

for key in os.environ:
    if (
        key.startswith("FAKE_")
        or key == "DREAMING_COPILOT_BIN"
        or key == "UNTRUSTED_PROVIDER_REDIRECT"
    ):
        raise SystemExit(f"forbidden inherited provider variable: {key}")
args = sys.argv[1:]
prompt = args[args.index("-p") + 1]
model = args[args.index("--model") + 1]
repair = "EVALUATION_INPUT_REPAIR_OPERATION" in prompt
packet = json.loads(
    prompt.split("repair_packet:\n" if repair else "authoring_packet:\n", 1)[1]
)
if model == "fixture-author-model-mismatch":
    observed_model = "different-provider-model"
else:
    observed_model = model
if model == "fixture-author-insufficient" or (
    repair and model == "fixture-repair-insufficient"
):
    payload = {
        "outcome": "insufficient_information",
        "summary": "The synthetic fixture cannot establish an objective outcome.",
        "reason": "objective_grader_unavailable",
    }
else:
    cases = packet["initial_suite"]["cases"] if repair else packet["suite_template"]["cases"]
    payload = {
        "outcome": "draft",
        "summary": "Safe synthetic fixture cases.",
        "cases": [
            {
                "id": case["id"],
                "task_id": (
                    f"repaired:{case['class']}-{index:04d}"
                    if repair
                    else f"authored:{case['class']}-{index:04d}"
                ),
                "prompt": (
                    f"Complete the repaired synthetic {case['class']} task {index}."
                    if repair
                    else f"Complete the synthetic {case['class']} task {index}."
                ),
            }
            for index, case in enumerate(cases, 1)
        ],
    }
print(json.dumps({"events": [
    {"type": "session.start", "data": {"model": observed_model}},
    {"type": "result", "data": payload},
    {"type": "session.usage_checkpoint", "usage": {
        "input_tokens": 100,
        "output_tokens": 40,
        "total_tokens": 140,
    }},
]}))
PY
chmod +x "$AUTHORING/author-bin/copilot"
author_claim="$(
  "$EVAL" v2-input-claim "$AUTHORING/skill" \
    --owner-run-id certification-author-run \
    --author-model fixture-author-model \
    --reviewer-a-model fixture-review-model-one \
    --reviewer-b-model fixture-review-model-two
)"
author_claim_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["claim_id"])' \
    <<<"$author_claim"
)"
registration="$(
  env \
    DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/author-bin/copilot" \
    UNTRUSTED_PROVIDER_REDIRECT="$HOME/should-not-cross" \
    FAKE_PROVIDER_ROUTE="$HOME/should-not-cross" \
    "$EVAL" v2-input-author "$AUTHORING/skill" \
    --claim-id "$author_claim_id" \
    --suite "$AUTHORING/skill/.skill-evaluation-cases.json" \
    --policy "$AUTHORING/skill/.skill-evaluation-policy.json" \
    --config "$AUTHORING/config/compilation.json" \
    --routing "$AUTHORING/config/routing.json" \
    --harness "$HARNESS" \
    --catalog "$AUTHORING/config/authoring-catalog.json" \
    --model fixture-author-model \
    --timeout 60 --token-budget 140 --output-bytes 100000 \
    --output-dir "$AUTHORING/trusted-materialized"
)"
manifest="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["input_manifest_sha256"])' \
    <<<"$registration"
)"
author_validation="$(
  "$EVAL" v2-input-validate "$AUTHORING/skill" --manifest "$manifest"
)"
author_validation_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' \
    <<<"$author_validation"
)"
"$EVAL" v2-input-review-packet "$AUTHORING/skill" \
  --manifest "$manifest" \
  --validation "$author_validation_id" \
  --output "$AUTHORING/review-packet.json" >/dev/null
python3 - "$AUTHORING/review-packet.json" "$manifest" \
  "$author_validation_id" "$HOME" <<'PY'
import hashlib
import json
import sys

path, manifest_id, validation_id, home = sys.argv[1:]
packet = json.load(open(path))
packet_id = packet.pop("packet_id")
canonical = json.dumps(
    packet, sort_keys=True, separators=(",", ":"), ensure_ascii=True
).encode()
assert packet_id == "sha256:" + hashlib.sha256(canonical).hexdigest()
assert packet["kind"] == "safe_evaluation_input_review_packet"
assert packet["input_manifest_sha256"] == manifest_id
assert packet["validation_contract"]["receipt_sha256"] == validation_id
assert packet["authoring_contract"]["operation"]["model"] == "fixture-author-model"
assert {
    case["class"] for case in packet["suite"]["cases"]
} == {
    "intended", "related", "activation_positive", "activation_negative"
}
encoded = json.dumps(packet, sort_keys=True)
for forbidden in (
    home,
    ".copilot/session-state",
    "transcript",
    "PRIVATE_AMBIENT_SECRET",
):
    assert forbidden not in encoded
PY
mkdir -p "$AUTHORING/bin"
cat >"$AUTHORING/bin/copilot" <<'PY'
#!/usr/bin/env python3
import json
import os
import sys

for key in os.environ:
    if key.startswith("FAKE_") or key in {
        "DREAMING_COPILOT_BIN",
        "UNTRUSTED_PROVIDER_REDIRECT",
    }:
        raise SystemExit(f"forbidden inherited provider variable: {key}")
args = sys.argv[1:]
prompt = args[args.index("-p") + 1]
packet = json.loads(prompt.split("review_packet:\n", 1)[1])
model = args[args.index("--model") + 1]
force_reject = os.path.basename(
    os.path.dirname(os.path.realpath(sys.argv[0]))
) == "reject-bin"
input_tokens = 90
output_tokens = 30
payload = {
    "decision": (
        "reject"
        if force_reject
        or (
            model == "fixture-repair-review-one"
            and "repair_lineage_contract" not in packet
        )
        else "accept"
    ),
    "summary": f"Exact safe manifest reviewed by {model}.",
    "reason": (
        "prompt_contract_mismatch"
        if force_reject
        or (
            model == "fixture-repair-review-one"
            and "repair_lineage_contract" not in packet
        )
        else None
    ),
}
print(json.dumps({"events": [
    {"type": "session.start", "data": {"model": model}},
    {"type": "result", "data": payload},
    {"type": "session.usage_checkpoint", "usage": {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }},
]}))
PY
chmod +x "$AUTHORING/bin/copilot"
python3 - "$EVAL" "$AUTHORING/bin/copilot" "$HOME" "$TEST_ROOT" <<'PY'
import importlib.util
import os
import sys
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("skill_evaluation", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with mock.patch.dict(
    os.environ,
    {
        "DREAMING_EXECUTOR_TEST_ALLOW_ROOT": sys.argv[4],
        "DREAMING_COPILOT_BIN": sys.argv[2],
        "SKILLS_STATE_DIR": str(Path(sys.argv[3]) / ".copilot/test-state"),
    },
    clear=False,
):
    try:
        module.trusted_input_test_binary(
            Path(sys.argv[3]).resolve(), Path(sys.argv[3])
        )
    except module.EvaluationError as error:
        assert "non-authoritative test roots" in str(error)
    else:
        raise AssertionError("live-root test override was accepted")
PY
run_trusted_review() {
  local slot="$1" model="$2"
  env \
    DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/bin/copilot" \
    UNTRUSTED_PROVIDER_REDIRECT="$HOME/should-not-cross" \
    "$EVAL" v2-input-review "$AUTHORING/skill" --manifest "$manifest" \
      --claim-id "$author_claim_id" --slot "$slot" \
      --validation "$author_validation_id" --model "$model"
}
review_one="$(
  run_trusted_review review_a "fixture-review-model-one"
)"
review_one_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' \
    <<<"$review_one"
)"
python3 -c 'import json,sys; assert json.load(sys.stdin)["decision"] == "accept"' \
  <<<"$review_one"
review_two="$(
  run_trusted_review review_b "fixture-review-model-two"
)"
review_two_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' \
    <<<"$review_two"
)"
"$EVAL" v2-input-state "$AUTHORING/skill" \
  --state drafting --reason authoring_claimed >/dev/null
"$EVAL" v2-input-state "$AUTHORING/skill" \
  --state review_required --reason validation_passed \
  --manifest "$manifest" --validation "$author_validation_id" >/dev/null
expect_refusal "distinct-reviewer-models" \
  "input review receipts must be distinct" \
  "$EVAL" v2-input-ready "$AUTHORING/skill" \
    --claim-id "$author_claim_id" --manifest "$manifest" \
    --validation "$author_validation_id" \
    --review "$review_one_id" --review "$review_one_id"
python3 - "$SCRIPT_DIR" "$author_claim_id" "$AUTHORING/skill" \
  "$manifest" "$author_validation_id" "$review_one_id" "$review_two_id" <<'PY'
import sys
from pathlib import Path

script_dir, claim_id, skill_arg, manifest, validation, *reviews = sys.argv[1:]
sys.path.insert(0, script_dir)
import evaluation_input_claims as claims

claim = claims.inspect_claim(claim_id)
claims.complete_claim_ready(
    claim_id,
    skill_path=str(Path(skill_arg).resolve()),
    skill_key=claim["skill_key"],
    candidate_id=claim["candidate_id"],
    manifest_sha256=manifest,
    validation_receipt_sha256=validation,
    review_receipt_sha256s=reviews,
)
PY
python3 - "$EVAL" <<'PY'
import importlib.util
import hashlib
import json
import sys
from pathlib import Path

path = sys.argv[1]
spec = importlib.util.spec_from_file_location("skill_evaluation", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
pending = module.pending_terminal_publications()
assert len(pending) == 1
first = module.publish_pending_terminal(pending[0])
second = module.publish_pending_terminal(pending[0])
assert first["transition_id"] == second["transition_id"]
assert first["publication"] == "published"
assert second["publication"] == "replayed"
assert module.pending_terminal_publications() == []
original_state = Path(module.os.environ["SKILLS_STATE_DIR"])
module.os.environ["SKILLS_STATE_DIR"] = str(
    original_state.parent / "terminal-recovery-state"
)

def sha(label):
    payload = json.dumps(
        {"fixture": label}, sort_keys=True, separators=(",", ":")
    ).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()

def make_skill(label):
    skill = Path(module.os.environ["SKILLS_STATE_DIR"]).parent / label
    skill.mkdir(parents=True)
    skill.joinpath("SKILL.md").write_text(
        f"---\nname: {label}\ndescription: Fixture.\n---\n\nFixture.\n"
    )
    return skill

invalid_skill = make_skill("terminal-invalid")
invalid_candidate, _ = module.candidate_id(invalid_skill)
invalid_claim = module.reserve_claim(
    skill_path=str(invalid_skill),
    skill_key=module.latest_key(str(invalid_skill)),
    candidate_id=invalid_candidate,
    owner_run_id="terminal-invalid-run",
    author_model="terminal-invalid-author",
    reviewer_a_model="terminal-invalid-review-a",
    reviewer_b_model="terminal-invalid-review-b",
)
try:
    module.prepare_claim_dispatch(
        claim_id=invalid_claim["claim_id"],
        skill_path=str(invalid_skill),
        skill_key=invalid_claim["skill_key"],
        candidate_id=invalid_candidate,
        slot_name="author",
        model="wrong-author",
        packet_id=sha("invalid-packet"),
        manifest_sha256=None,
        validation_receipt_sha256=None,
        requested_token_budget=100,
        requested_timeout_seconds=60,
    )
except module.ClaimLedgerError:
    pass
else:
    raise AssertionError("invalid terminal fixture did not close")

insufficient_skill = make_skill("terminal-insufficient")
insufficient_candidate, _ = module.candidate_id(insufficient_skill)
insufficient_claim = module.reserve_claim(
    skill_path=str(insufficient_skill),
    skill_key=module.latest_key(str(insufficient_skill)),
    candidate_id=insufficient_candidate,
    owner_run_id="terminal-insufficient-run",
    author_model="terminal-insufficient-author",
    reviewer_a_model="terminal-insufficient-review-a",
    reviewer_b_model="terminal-insufficient-review-b",
)
packet_id = sha("insufficient-packet")
module.prepare_claim_dispatch(
    claim_id=insufficient_claim["claim_id"],
    skill_path=str(insufficient_skill),
    skill_key=insufficient_claim["skill_key"],
    candidate_id=insufficient_candidate,
    slot_name="author",
    model="terminal-insufficient-author",
    packet_id=packet_id,
    manifest_sha256=None,
    validation_receipt_sha256=None,
    requested_token_budget=100,
    requested_timeout_seconds=60,
)
module.complete_claim_slot(
    claim_id=insufficient_claim["claim_id"],
    slot_name="author",
    operation={
        "operation": "author",
        "model": "terminal-insufficient-author",
        "observed_model": "terminal-insufficient-author",
        "packet_id": packet_id,
        "operation_id": sha("insufficient-operation"),
        "usage": {
            "normalized_tokens": 10,
            "input_tokens": 9,
            "output_tokens": 1,
        },
        "billing": {
            "status": "unavailable",
            "cost_usd": None,
            "provider": "copilot",
            "unavailable_reason": "provider_telemetry_unavailable",
            "native_line_item_id": None,
            "native_event_sha256": None,
            "native_event_size": None,
        },
        "elapsed_ms": 100,
    },
    manifest_sha256=None,
    terminal_reason="insufficient_information",
)
pending_values = module.pending_terminal_publications()
terminal_results = {
    item["state"]: item
    for item in (
        module.publish_pending_terminal(publication)
        for publication in pending_values
    )
}
assert set(terminal_results) == {"invalid", "insufficient_information"}
assert module.pending_terminal_publications() == []
invalid_publication = next(
    item for item in pending_values if item["readiness_state"] == "invalid"
)
module.write_input_transition(
    invalid_skill,
    state="invalid",
    reason="owner_interrupted",
    input_manifest_sha256=None,
    validation_receipt_sha256=None,
    review_receipt_sha256s=[],
    created_at="2026-08-18T00:00:01+00:00",
    claim_id=invalid_publication["claim_id"],
)
pointer_before = module.load_input_current_pointer(invalid_skill)
try:
    module.publish_pending_terminal(invalid_publication)
except module.EvaluationError as error:
    assert "not the unique history tip" in str(error)
else:
    raise AssertionError("recovery repointed a non-tip terminal transition")
assert module.load_input_current_pointer(invalid_skill) == pointer_before

fenced_skill = make_skill("terminal-fenced")
fenced_candidate, _ = module.candidate_id(fenced_skill)
fenced_claim = module.reserve_claim(
    skill_path=str(fenced_skill),
    skill_key=module.latest_key(str(fenced_skill)),
    candidate_id=fenced_candidate,
    owner_run_id="terminal-fenced-run",
    author_model="terminal-fenced-author",
    reviewer_a_model="terminal-fenced-review-a",
    reviewer_b_model="terminal-fenced-review-b",
)
try:
    module.prepare_claim_dispatch(
        claim_id=fenced_claim["claim_id"],
        skill_path=str(fenced_skill),
        skill_key=fenced_claim["skill_key"],
        candidate_id=fenced_candidate,
        slot_name="author",
        model="wrong-fenced-author",
        packet_id=sha("fenced-packet"),
        manifest_sha256=None,
        validation_receipt_sha256=None,
        requested_token_budget=100,
        requested_timeout_seconds=60,
    )
except module.ClaimLedgerError:
    pass
else:
    raise AssertionError("fenced terminal fixture did not close")
fenced_publication = module.pending_terminal_publications()[0]

checks = 0
def lose_before_pointer():
    global checks
    checks += 1
    if checks == 2:
        raise module.EvaluationError("fixture lease lost")

try:
    module.publish_pending_terminal(
        fenced_publication, authority_check=lose_before_pointer
    )
except module.EvaluationError as error:
    assert "fixture lease lost" in str(error)
else:
    raise AssertionError("publication ignored lease loss before pointer write")
assert module.load_input_current_pointer(fenced_skill) is None

checks = 0
def lose_before_ack():
    global checks
    checks += 1
    if checks == 2:
        raise module.EvaluationError("fixture halt asserted")

try:
    module.publish_pending_terminal(
        fenced_publication, authority_check=lose_before_ack
    )
except module.EvaluationError as error:
    assert "fixture halt asserted" in str(error)
else:
    raise AssertionError("publication ignored halt before acknowledgement")
assert module.load_input_current_pointer(fenced_skill) is not None
assert len(module.pending_terminal_publications()) == 1
assert module.publish_pending_terminal(fenced_publication)["publication"] == "replayed"
assert module.pending_terminal_publications() == []
PY
ready="$(
  python3 - "$EVAL" "$author_claim_id" "$AUTHORING/skill" \
    "$manifest" "$author_validation_id" "$review_one_id" "$review_two_id" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

evaluator, claim_id, skill, manifest, validation, *reviews = sys.argv[1:]
command = [
    evaluator,
    "v2-input-ready",
    skill,
    "--claim-id",
    claim_id,
    "--manifest",
    manifest,
    "--validation",
    validation,
]
for review in reviews:
    command.extend(("--review", review))
processes = [
    subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(2)
]
results = []
for process in processes:
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        raise AssertionError(f"concurrent readiness failed: {stderr}")
    results.append(json.loads(stdout))
assert results[0]["transition_id"] == results[1]["transition_id"]
transition_path = Path(results[0]["transition"])
transitions = [
    json.loads(path.read_text())
    for path in sorted(transition_path.parent.glob("*.json"))
]
tips = {item["transition_id"] for item in transitions} - {
    item["prior_transition_id"]
    for item in transitions
    if item["prior_transition_id"] is not None
}
assert tips == {results[0]["transition_id"]}
assert sum(item["state"] == "ready" for item in transitions) == 1

ready_transition = json.loads(transition_path.read_text())
pointer_path = Path(results[0]["current"])
pointer = json.loads(pointer_path.read_text())
pointer["transition_id"] = ready_transition["prior_transition_id"]
pointer_path.write_text(json.dumps(pointer, indent=2, sort_keys=True) + "\n")
recovered = subprocess.run(
    command, text=True, capture_output=True, check=False
)
if recovered.returncode != 0:
    raise AssertionError(f"readiness recovery failed: {recovered.stderr}")
recovered_result = json.loads(recovered.stdout)
assert recovered_result["transition_id"] == results[0]["transition_id"]
assert json.loads(pointer_path.read_text())["transition_id"] == results[0]["transition_id"]
assert len(list(transition_path.parent.glob("*.json"))) == len(transitions)
print(json.dumps(results[0], sort_keys=True, separators=(",", ":")))
PY
)"
pass "pending recovery and concurrent readiness publish one tip and recover an interrupted pointer"
python3 - "$registration" "$source_catalog_id" <<'PY'
import json
import sys

registration = json.loads(sys.argv[1])
manifest = json.load(open(registration["input_manifest"]))
roles = {item["role"] for item in manifest["objects"]}
assert {
    "authoring_packet", "authoring_draft", "authoring_receipt",
    "authoring_operation", "authoring_adapter"
} <= roles
assert sys.argv[2] in manifest["source_identities"]
assert manifest["authoring_method"] == "bounded-safe-author"
PY
repair_claim="$(
  "$EVAL" v2-input-claim "$AUTHORING/skill" \
    --owner-run-id certification-repair-run \
    --author-model fixture-repair-author \
    --reviewer-a-model fixture-repair-review-one \
    --reviewer-b-model fixture-repair-review-two
)"
repair_claim_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["claim_id"])' \
    <<<"$repair_claim"
)"
repair_initial="$(
  env DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/author-bin/copilot" \
    "$EVAL" v2-input-author "$AUTHORING/skill" \
      --claim-id "$repair_claim_id" \
      --suite "$AUTHORING/skill/.skill-evaluation-cases.json" \
      --policy "$AUTHORING/skill/.skill-evaluation-policy.json" \
      --config "$AUTHORING/config/compilation.json" \
      --routing "$AUTHORING/config/routing.json" \
      --harness "$HARNESS" \
      --catalog "$AUTHORING/config/authoring-catalog.json" \
      --model fixture-repair-author \
      --timeout 60 --token-budget 140 --output-bytes 100000 \
      --output-dir "$AUTHORING/repair-initial-materialized"
)"
repair_initial_manifest="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["input_manifest_sha256"])' \
    <<<"$repair_initial"
)"
repair_initial_validation="$(
  "$EVAL" v2-input-validate "$AUTHORING/skill" \
    --manifest "$repair_initial_manifest"
)"
repair_initial_validation_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' \
    <<<"$repair_initial_validation"
)"
repair_review_a="$(
  env DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/bin/copilot" \
    "$EVAL" v2-input-review "$AUTHORING/skill" \
      --claim-id "$repair_claim_id" --slot review_a \
      --manifest "$repair_initial_manifest" \
      --validation "$repair_initial_validation_id" \
      --model fixture-repair-review-one
)"
repair_review_a_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' \
    <<<"$repair_review_a"
)"
python3 -c 'import json,sys; assert json.load(sys.stdin)["decision"] == "reject"' \
  <<<"$repair_review_a"
repair_review_b="$(
  env DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/bin/copilot" \
    "$EVAL" v2-input-review "$AUTHORING/skill" \
      --claim-id "$repair_claim_id" --slot review_b \
      --manifest "$repair_initial_manifest" \
      --validation "$repair_initial_validation_id" \
      --model fixture-repair-review-two
)"
repair_review_b_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' \
    <<<"$repair_review_b"
)"
"$EVAL" v2-input-repair-packet "$AUTHORING/skill" \
  --claim-id "$repair_claim_id" \
  --manifest "$repair_initial_manifest" \
  --validation "$repair_initial_validation_id" \
  --review "$repair_review_a_id" --review "$repair_review_b_id" \
  --original-author-model fixture-repair-author \
  --output "$AUTHORING/repair-packet.json" >/dev/null
python3 - "$AUTHORING/repair-packet.json" "$HOME" \
  "$repair_claim_id" "$repair_initial_manifest" \
  "$repair_initial_validation_id" "$repair_review_a_id" \
  "$repair_review_b_id" <<'PY'
import hashlib
import json
import sys

path, home, claim, manifest, validation, review_a, review_b = sys.argv[1:]
packet = json.load(open(path))
packet_id = packet.pop("packet_id")
canonical = json.dumps(
    packet, sort_keys=True, separators=(",", ":"), ensure_ascii=True
).encode()
assert packet_id == "sha256:" + hashlib.sha256(canonical).hexdigest()
assert packet["kind"] == "safe_evaluation_input_repair_packet"
assert packet["claim_id"] == claim
assert packet["initial_manifest_sha256"] == manifest
assert packet["initial_validation_contract"]["receipt_sha256"] == validation
assert packet["initial_review_receipt_sha256s"] == sorted([review_a, review_b])
assert {item["decision"] for item in packet["review_history"]} == {
    "accept", "reject"
}
encoded = json.dumps(packet, sort_keys=True).lower()
for forbidden in (
    home.lower(),
    ".copilot/session-state",
    "transcript",
    "private_ambient_secret",
    "dashboard snapshot",
    "user disposition",
):
    assert forbidden not in encoded
assert '"content"' in encoded
assert '"fixture"' in encoded
assert "synthetic_fixture" not in encoded
assert "deterministic_grader_contracts" not in encoded
PY
expect_refusal "wrong-repair-claim" "claim does not exist" \
  "$EVAL" v2-input-repair-packet "$AUTHORING/skill" \
    --claim-id "sha256:0000000000000000000000000000000000000000000000000000000000000000" \
    --manifest "$repair_initial_manifest" \
    --validation "$repair_initial_validation_id" \
    --review "$repair_review_a_id" --review "$repair_review_b_id" \
    --original-author-model fixture-repair-author \
    --output "$AUTHORING/wrong-claim-repair-packet.json"
expect_refusal "wrong-repair-author-model" \
  "repair original author model differs from retained author provenance" \
  env DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/author-bin/copilot" \
    "$EVAL" v2-input-repair "$AUTHORING/skill" \
      --claim-id "$repair_claim_id" \
      --manifest "$repair_initial_manifest" \
      --validation "$repair_initial_validation_id" \
      --review "$repair_review_a_id" --review "$repair_review_b_id" \
      --original-author-model wrong-repair-author \
      --timeout 60 --token-budget 140 --output-bytes 100000 \
      --output-dir "$AUTHORING/wrong-model-repair"
wrong_model_repair_claim="$(
  "$EVAL" v2-input-claim-inspect --claim-id "$repair_claim_id"
)"
python3 - "$wrong_model_repair_claim" <<'PY'
import json
import sys

claim = json.loads(sys.argv[1])
assert claim["status"] == "open"
assert claim["slots"][3]["status"] == "unstarted"
assert claim["aggregate_actual"]["started_operations"] == 3
PY
repair_result="$(
  env DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/author-bin/copilot" \
    "$EVAL" v2-input-repair "$AUTHORING/skill" \
      --claim-id "$repair_claim_id" \
      --manifest "$repair_initial_manifest" \
      --validation "$repair_initial_validation_id" \
      --review "$repair_review_a_id" --review "$repair_review_b_id" \
      --original-author-model fixture-repair-author \
      --timeout 60 --token-budget 140 --output-bytes 100000 \
      --output-dir "$AUTHORING/repaired-materialized"
)"
repaired_manifest="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["input_manifest_sha256"])' \
    <<<"$repair_result"
)"
[[ "$repaired_manifest" != "$repair_initial_manifest" ]] ||
  fail "repair reused the initial manifest"
repaired_validation="$(
  "$EVAL" v2-input-validate "$AUTHORING/skill" --manifest "$repaired_manifest"
)"
repaired_validation_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' \
    <<<"$repaired_validation"
)"
cp -R "$SKILLS_STATE_DIR" "$AUTHORING/reject-state"
mkdir -p "$AUTHORING/reject-bin"
cp "$AUTHORING/bin/copilot" "$AUTHORING/reject-bin/copilot"
reject_rereview_a="$(
  env SKILLS_STATE_DIR="$AUTHORING/reject-state" \
    DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/bin/copilot" \
    "$EVAL" v2-input-review "$AUTHORING/skill" \
      --claim-id "$repair_claim_id" --slot rereview_a \
      --manifest "$repaired_manifest" \
      --validation "$repaired_validation_id" \
      --model fixture-repair-review-one
)"
reject_rereview_b="$(
  env SKILLS_STATE_DIR="$AUTHORING/reject-state" \
    DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/reject-bin/copilot" \
    "$EVAL" v2-input-review "$AUTHORING/skill" \
      --claim-id "$repair_claim_id" --slot rereview_b \
      --manifest "$repaired_manifest" \
      --validation "$repaired_validation_id" \
      --model fixture-repair-review-two
)"
python3 -c 'import json,sys; assert json.load(sys.stdin)["decision"] == "reject"' \
  <<<"$reject_rereview_b"
env SKILLS_STATE_DIR="$AUTHORING/reject-state" \
  "$EVAL" v2-input-validate "$AUTHORING/skill" \
    --manifest "$repaired_manifest" >/dev/null
reject_claim_state="$(
  env SKILLS_STATE_DIR="$AUTHORING/reject-state" \
    "$EVAL" v2-input-claim-inspect --claim-id "$repair_claim_id"
)"
python3 - "$reject_claim_state" "$repaired_manifest" \
  "$repaired_validation_id" "$reject_rereview_a" "$reject_rereview_b" <<'PY'
import json
import sys

claim = json.loads(sys.argv[1])
assert claim["status"] == "invalid"
assert claim["terminal_reason"] == "independent_rereview_rejected"
terminal = claim["terminal_publication"]
assert terminal["readiness_state"] == "invalid"
assert terminal["manifest_sha256"] == sys.argv[2]
assert terminal["validation_receipt_sha256"] == sys.argv[3]
assert terminal["review_receipt_sha256s"] == sorted(
    json.loads(value)["receipt_sha256"] for value in sys.argv[4:]
)
PY
rereview_a="$(
  env DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/bin/copilot" \
    "$EVAL" v2-input-review "$AUTHORING/skill" \
      --claim-id "$repair_claim_id" --slot rereview_a \
      --manifest "$repaired_manifest" \
      --validation "$repaired_validation_id" \
      --model fixture-repair-review-one
)"
rereview_a_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' \
    <<<"$rereview_a"
)"
rereview_b="$(
  env DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/bin/copilot" \
    "$EVAL" v2-input-review "$AUTHORING/skill" \
      --claim-id "$repair_claim_id" --slot rereview_b \
      --manifest "$repaired_manifest" \
      --validation "$repaired_validation_id" \
      --model fixture-repair-review-two
)"
rereview_b_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' \
    <<<"$rereview_b"
)"
expect_refusal "stale-initial-reviews-for-repair" \
  "does not bind the exact manifest" \
  "$EVAL" v2-input-ready "$AUTHORING/skill" \
    --claim-id "$repair_claim_id" --manifest "$repaired_manifest" \
    --validation "$repaired_validation_id" \
    --review "$repair_review_a_id" --review "$repair_review_b_id"
expect_refusal "repaired-reviews-for-initial" \
  "exact manifest" \
  "$EVAL" v2-input-ready "$AUTHORING/skill" \
    --claim-id "$repair_claim_id" --manifest "$repair_initial_manifest" \
    --validation "$repair_initial_validation_id" \
    --review "$rereview_a_id" --review "$rereview_b_id"
repair_ready="$(
  "$EVAL" v2-input-ready "$AUTHORING/skill" \
    --claim-id "$repair_claim_id" --manifest "$repaired_manifest" \
    --validation "$repaired_validation_id" \
    --review "$rereview_a_id" --review "$rereview_b_id"
)"
python3 - "$repair_ready" "$repair_claim_id" "$repair_review_a_id" \
  "$repair_review_b_id" "$rereview_a_id" "$rereview_b_id" <<'PY'
import json
import sys

ready = json.loads(sys.argv[1])
assert ready["claim_id"] == sys.argv[2]
transition = json.load(open(ready["transition"]))
assert transition["review_receipt_sha256s"] == sorted(sys.argv[5:7])
assert not set(sys.argv[3:5]) & set(transition["review_receipt_sha256s"])
PY
rm -f "$AUTHORING/repair-packet.json"
rm -rf "$AUTHORING/repaired-materialized"
"$EVAL" v2-input-validate "$AUTHORING/skill" \
  --manifest "$repaired_manifest" >/dev/null
python3 - "$EVAL" "$AUTHORING/skill" "$repair_result" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

evaluator, skill, result_json = sys.argv[1:]
result = json.loads(result_json)
manifest_path = Path(result["input_manifest"])
manifest = json.loads(manifest_path.read_text())
registry = manifest_path.parent.parent
operation_entry = next(
    item for item in manifest["objects"] if item["role"] == "repair_operation"
)
operation_path = registry / "objects" / operation_entry["sha256"].removeprefix("sha256:")

def assert_tamper_refused(path, mutate):
    original = path.read_bytes()
    try:
        value = json.loads(original)
        mutate(value)
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
        completed = subprocess.run(
            [
                evaluator,
                "v2-input-validate",
                skill,
                "--manifest",
                result["input_manifest_sha256"],
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode != 0
    finally:
        path.write_bytes(original)

assert_tamper_refused(
    operation_path,
    lambda value: value.update(summary="tampered repair operation"),
)
assert_tamper_refused(
    manifest_path,
    lambda value: value["repair_lineage"].update(
        initial_review_set_id="sha256:" + "0" * 64
    ),
)
PY
expect_refusal "repair-twice" "completed and cannot dispatch" \
  env DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/author-bin/copilot" \
    "$EVAL" v2-input-repair "$AUTHORING/skill" \
      --claim-id "$repair_claim_id" \
      --manifest "$repair_initial_manifest" \
      --validation "$repair_initial_validation_id" \
      --review "$repair_review_a_id" --review "$repair_review_b_id" \
      --original-author-model fixture-repair-author \
      --timeout 60 --token-budget 140 --output-bytes 100000 \
      --output-dir "$AUTHORING/repaired-materialized-again"
repair_claim_state="$(
  "$EVAL" v2-input-claim-inspect --claim-id "$repair_claim_id"
)"
python3 - "$repair_claim_state" "$repair_initial_manifest" "$repaired_manifest" <<'PY'
import json
import sys

claim = json.loads(sys.argv[1])
assert claim["status"] == "completed"
assert claim["terminal_reason"] == "ready"
assert claim["initial_manifest_sha256"] == sys.argv[2]
assert claim["repaired_manifest_sha256"] == sys.argv[3]
assert claim["aggregate_actual"]["started_operations"] == 6
assert claim["aggregate_actual"]["normalized_tokens"] == 760
assert [item["status"] for item in claim["slots"]] == ["completed"] * 6
PY
pass "one rejected input is repaired once, re-reviewed by the same identities, replayable, and ready in six slots"
model_mismatch_claim="$(
  "$EVAL" v2-input-claim "$AUTHORING/skill" \
    --owner-run-id certification-model-mismatch-run \
    --author-model fixture-author-model-mismatch \
    --reviewer-a-model mismatch-reviewer-one \
    --reviewer-b-model mismatch-reviewer-two
)"
model_mismatch_claim_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["claim_id"])' \
    <<<"$model_mismatch_claim"
)"
expect_refusal "author-model-mismatch" "exact-model-unproved" \
  env DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/author-bin/copilot" \
    "$EVAL" v2-input-author "$AUTHORING/skill" \
      --claim-id "$model_mismatch_claim_id" \
      --suite "$AUTHORING/skill/.skill-evaluation-cases.json" \
      --policy "$AUTHORING/skill/.skill-evaluation-policy.json" \
      --config "$AUTHORING/config/compilation.json" \
      --routing "$AUTHORING/config/routing.json" \
      --harness "$HARNESS" \
      --catalog "$AUTHORING/config/authoring-catalog.json" \
      --model fixture-author-model-mismatch \
      --timeout 60 --token-budget 140 --output-bytes 100000 \
      --output-dir "$AUTHORING/model-mismatch-materialized"
[[ ! -e "$AUTHORING/model-mismatch-materialized" ]] ||
  fail "author model mismatch left materialized output"
insufficient_claim="$(
  "$EVAL" v2-input-claim "$AUTHORING/skill" \
    --owner-run-id certification-insufficient-run \
    --author-model fixture-author-insufficient \
    --reviewer-a-model insufficient-reviewer-one \
    --reviewer-b-model insufficient-reviewer-two
)"
insufficient_claim_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["claim_id"])' \
    <<<"$insufficient_claim"
)"
insufficient="$(
  env DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/author-bin/copilot" \
    "$EVAL" v2-input-author "$AUTHORING/skill" \
      --claim-id "$insufficient_claim_id" \
      --suite "$AUTHORING/skill/.skill-evaluation-cases.json" \
      --policy "$AUTHORING/skill/.skill-evaluation-policy.json" \
      --config "$AUTHORING/config/compilation.json" \
      --routing "$AUTHORING/config/routing.json" \
      --harness "$HARNESS" \
      --catalog "$AUTHORING/config/authoring-catalog.json" \
      --model fixture-author-insufficient \
      --timeout 60 --token-budget 140 --output-bytes 100000 \
      --output-dir "$AUTHORING/insufficient-materialized"
)"
python3 - "$insufficient" <<'PY'
import json
import sys

value = json.loads(sys.argv[1])
assert value["status"] == "insufficient_information"
assert value["state"] == "insufficient_information"
assert value["reason"] == "objective_grader_unavailable"
assert value["input_manifest"] is None
assert value["input_manifest_sha256"] is None
PY
[[ ! -e "$AUTHORING/insufficient-materialized" ]] ||
  fail "insufficient authoring created materialized output"
export DREAMING_NOW_EPOCH=1787234400
repair_insufficient_claim="$(
  "$EVAL" v2-input-claim "$AUTHORING/skill" \
    --owner-run-id certification-repair-insufficient-run \
    --author-model fixture-repair-insufficient \
    --reviewer-a-model fixture-repair-review-one \
    --reviewer-b-model fixture-repair-review-two
)"
repair_insufficient_claim_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["claim_id"])' \
    <<<"$repair_insufficient_claim"
)"
repair_insufficient_initial="$(
  env DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/author-bin/copilot" \
    "$EVAL" v2-input-author "$AUTHORING/skill" \
      --claim-id "$repair_insufficient_claim_id" \
      --suite "$AUTHORING/skill/.skill-evaluation-cases.json" \
      --policy "$AUTHORING/skill/.skill-evaluation-policy.json" \
      --config "$AUTHORING/config/compilation.json" \
      --routing "$AUTHORING/config/routing.json" \
      --harness "$HARNESS" \
      --catalog "$AUTHORING/config/authoring-catalog.json" \
      --model fixture-repair-insufficient \
      --timeout 60 --token-budget 140 --output-bytes 100000 \
      --output-dir "$AUTHORING/repair-insufficient-initial"
)"
repair_insufficient_manifest="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["input_manifest_sha256"])' \
    <<<"$repair_insufficient_initial"
)"
repair_insufficient_validation="$(
  "$EVAL" v2-input-validate "$AUTHORING/skill" \
    --manifest "$repair_insufficient_manifest"
)"
repair_insufficient_validation_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' \
    <<<"$repair_insufficient_validation"
)"
repair_insufficient_review_a="$(
  env DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/bin/copilot" \
    "$EVAL" v2-input-review "$AUTHORING/skill" \
      --claim-id "$repair_insufficient_claim_id" --slot review_a \
      --manifest "$repair_insufficient_manifest" \
      --validation "$repair_insufficient_validation_id" \
      --model fixture-repair-review-one
)"
repair_insufficient_review_a_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' \
    <<<"$repair_insufficient_review_a"
)"
repair_insufficient_review_b="$(
  env DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/bin/copilot" \
    "$EVAL" v2-input-review "$AUTHORING/skill" \
      --claim-id "$repair_insufficient_claim_id" --slot review_b \
      --manifest "$repair_insufficient_manifest" \
      --validation "$repair_insufficient_validation_id" \
      --model fixture-repair-review-two
)"
repair_insufficient_review_b_id="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' \
    <<<"$repair_insufficient_review_b"
)"
repair_insufficient="$(
  env DREAMING_EXECUTOR_TEST_ALLOW_ROOT="$TEST_ROOT" \
    DREAMING_COPILOT_BIN="$AUTHORING/author-bin/copilot" \
    "$EVAL" v2-input-repair "$AUTHORING/skill" \
      --claim-id "$repair_insufficient_claim_id" \
      --manifest "$repair_insufficient_manifest" \
      --validation "$repair_insufficient_validation_id" \
      --review "$repair_insufficient_review_a_id" \
      --review "$repair_insufficient_review_b_id" \
      --original-author-model fixture-repair-insufficient \
      --timeout 60 --token-budget 140 --output-bytes 100000 \
      --output-dir "$AUTHORING/repair-insufficient-output"
)"
python3 - "$repair_insufficient" "$EVAL" \
  "$repair_insufficient_claim_id" <<'PY'
import json
import subprocess
import sys

result = json.loads(sys.argv[1])
assert result["status"] == "insufficient_information"
assert result["input_manifest_sha256"] is None
claim = json.loads(
    subprocess.run(
        [sys.argv[2], "v2-input-claim-inspect", "--claim-id", sys.argv[3]],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
)
assert claim["status"] == "completed"
assert claim["terminal_reason"] == "repair_insufficient_information"
assert claim["slots"][3]["status"] == "completed"
assert claim["slots"][3]["usage_status"] == "available"
assert claim["aggregate_actual"]["started_operations"] == 4
PY
[[ ! -e "$AUTHORING/repair-insufficient-output" ]] ||
  fail "insufficient repair created materialized output"
unset DREAMING_NOW_EPOCH
pass "repair insufficient information is terminal, non-ready, and retains actual usage"
mkdir -p "$AUTHORING/evaluator-alias"
ln -s "$EVAL" "$AUTHORING/evaluator-alias/skill-evaluation.py"
python3 - "$EVAL" "$AUTHORING/evaluator-alias/skill-evaluation.py" \
  "$AUTHORING/adapter-boundary" "$SCRIPT_DIR/dreaming-vendor-adapter.py" <<'PY'
import importlib.util
import sys
from pathlib import Path
from unittest import mock

evaluator, alias, boundary_arg, adapter = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("skill_evaluation", evaluator)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with mock.patch.object(module, "__file__", str(alias)):
    assert module.trusted_authoring_adapter_path() == adapter.resolve()
boundary_arg.mkdir()
fake_evaluator = boundary_arg / "skill-evaluation.py"
fake_evaluator.write_text("# trust-anchor fixture\n")
fake_adapter = boundary_arg / "dreaming-vendor-adapter.py"
fake_adapter.symlink_to(adapter)
with mock.patch.object(module, "__file__", str(fake_evaluator)), mock.patch.object(
    module.subprocess, "run"
) as run:
    try:
        module.trusted_authoring_adapter_path()
    except module.EvaluationError as error:
        assert "adapter is unavailable" in str(error)
    else:
        raise AssertionError("symlinked adapter was accepted")
    assert not run.called
fake_adapter.unlink()
fake_adapter.write_text("# byte-substituted adapter\n")
with mock.patch.object(module, "__file__", str(fake_evaluator)), mock.patch.object(
    module.subprocess, "run"
) as run:
    try:
        module.trusted_authoring_adapter_path()
    except module.EvaluationError as error:
        assert "reviewed identity" in str(error)
    else:
        raise AssertionError("byte-substituted adapter was accepted")
    assert not run.called
PY
expect_refusal "caller-forged-authoring-ingress" \
  "caller-supplied authoring provenance is not accepted" \
  "$AUTHORING/evaluator-alias/skill-evaluation.py" \
    v2-input-register "$AUTHORING/skill" \
    --suite "$AUTHORING/materialized/suite.json" \
    --policy "$AUTHORING/materialized/policy.json" \
    --config "$AUTHORING/materialized/compilation.json" \
    --routing "$AUTHORING/materialized/routing.json" \
    --harness "$HARNESS" \
    --authoring-method bounded-safe-author \
    --authoring-packet "$AUTHORING/packet.json" \
    --authoring-draft "$AUTHORING/draft.json" \
    --authoring-receipt "$AUTHORING/materialized/authoring.json" \
    --authoring-operation "$AUTHORING/author-operation.json" \
    --source-id "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["packet_id"])' <<<"$materialization")" \
    --source-id "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["draft_id"])' <<<"$materialization")" \
    --source-id "$source_catalog_id" \
    --source-id "$author_operation_id"
expect_refusal "authoring-provenance-required" "registration is evaluator-owned" \
  "$EVAL" v2-input-register "$AUTHORING/skill" \
    --suite "$AUTHORING/materialized/suite.json" \
    --policy "$AUTHORING/materialized/policy.json" \
    --config "$AUTHORING/materialized/compilation.json" \
    --routing "$AUTHORING/materialized/routing.json" \
    --harness "$HARNESS" --authoring-method bounded-safe-author \
    --source-id "$source_catalog_id"
python3 - "$EVAL" "$AUTHORING/packet.json" "$AUTHORING/draft.json" \
  "$AUTHORING/author-operation.json" "$SCRIPT_DIR/dreaming-vendor-adapter.py" <<'PY'
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("skill_evaluation", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
packet = json.loads(Path(sys.argv[2]).read_text())
draft = json.loads(Path(sys.argv[3]).read_text())
operation = json.loads(Path(sys.argv[4]).read_text())
adapter_sha = module.sha256_file(Path(sys.argv[5]))
draft_id = "sha256:" + hashlib.sha256(module.canonical(draft)).hexdigest()
mutations = {
    "normalized-token budget": (
        lambda value: value["usage"].update(
            normalized_tokens=112001, input_tokens=112000, output_tokens=1
        ),
        "normalized-token budget",
    ),
    "elapsed-time budget": (
        lambda value: value.__setitem__("elapsed_ms", 1500001),
        "elapsed-time budget",
    ),
    "billing provenance": (
        lambda value: value["billing"].update(status="available"),
        "billing telemetry",
    ),
}
for label, (mutate, expected) in mutations.items():
    value = copy.deepcopy(operation)
    value.pop("operation_id")
    mutate(value)
    value["operation_id"] = "sha256:" + hashlib.sha256(
        module.shadow_canonical(value)
    ).hexdigest()
    try:
        module.validate_authoring_operation(value, packet, draft_id, adapter_sha)
    except module.EvaluationError as error:
        assert expected in str(error), (label, error)
    else:
        raise AssertionError(f"{label} forgery was accepted")
forged = copy.deepcopy(operation)
forged["operation_id"] = "sha256:" + "3" * 64
try:
    module.validate_authoring_operation(forged, packet, draft_id, adapter_sha)
except module.EvaluationError as error:
    assert "content identity" in str(error)
else:
    raise AssertionError("operation ID forgery was accepted")
PY
python3 - "$AUTHORING/author-operation.json" \
  "$AUTHORING/overspent-author-operation.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

source, output = map(Path, sys.argv[1:])
value = json.loads(source.read_text())
value["usage"] = {
    "normalized_tokens": 112001,
    "input_tokens": 112000,
    "output_tokens": 1,
}
value.pop("operation_id")
canonical = json.dumps(
    value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
).encode()
value["operation_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
output.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
PY
overspent_operation_id="$(
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["operation_id"])' \
    "$AUTHORING/overspent-author-operation.json"
)"
expect_refusal "authoring-operation-budget" "caller-supplied authoring provenance is not accepted" \
  "$EVAL" v2-input-register "$AUTHORING/skill" \
    --suite "$AUTHORING/materialized/suite.json" \
    --policy "$AUTHORING/materialized/policy.json" \
    --config "$AUTHORING/materialized/compilation.json" \
    --routing "$AUTHORING/materialized/routing.json" \
    --harness "$HARNESS" --authoring-method bounded-safe-author \
    --authoring-packet "$AUTHORING/packet.json" \
    --authoring-draft "$AUTHORING/draft.json" \
    --authoring-receipt "$AUTHORING/materialized/authoring.json" \
    --authoring-operation "$AUTHORING/overspent-author-operation.json" \
    --source-id "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["packet_id"])' <<<"$materialization")" \
    --source-id "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["draft_id"])' <<<"$materialization")" \
    --source-id "$source_catalog_id" \
    --source-id "$overspent_operation_id"
python3 - "$AUTHORING/author-operation.json" "$AUTHORING" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

source = json.loads(Path(sys.argv[1]).read_text())
output = Path(sys.argv[2])
mutations = {
    "default-model": lambda value: value.__setitem__("model", "default"),
    "wrong-adapter": lambda value: value.__setitem__(
        "adapter_executable_sha256", "sha256:" + "0" * 64
    ),
    "wrong-packet": lambda value: value.__setitem__(
        "packet_id", "sha256:" + "1" * 64
    ),
    "wrong-draft": lambda value: value.__setitem__(
        "draft_id", "sha256:" + "2" * 64
    ),
    "elapsed-overrun": lambda value: value.__setitem__("elapsed_ms", 1500001),
    "false-billing": lambda value: value.__setitem__(
        "billing",
        {
            "status": "available",
            "cost_usd": None,
            "provider": "copilot",
            "unavailable_reason": None,
            "native_line_item_id": None,
            "native_event_sha256": None,
            "native_event_size": None,
        },
    ),
}
for name, mutate in mutations.items():
    value = json.loads(json.dumps(source))
    value.pop("operation_id")
    mutate(value)
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    value["operation_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    (output / f"{name}-author-operation.json").write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    )
forged = json.loads(json.dumps(source))
forged["operation_id"] = "sha256:" + "3" * 64
(output / "forged-id-author-operation.json").write_text(
    json.dumps(forged, sort_keys=True, separators=(",", ":"))
)
PY
expect_author_operation_refusal() {
  local label="$1"
  local expected="caller-supplied authoring provenance is not accepted"
  local operation_path="$3"
  local operation_id
  operation_id="$(
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["operation_id"])' \
      "$operation_path"
  )"
  expect_refusal "$label" "$expected" \
    "$EVAL" v2-input-register "$AUTHORING/skill" \
      --suite "$AUTHORING/materialized/suite.json" \
      --policy "$AUTHORING/materialized/policy.json" \
      --config "$AUTHORING/materialized/compilation.json" \
      --routing "$AUTHORING/materialized/routing.json" \
      --harness "$HARNESS" --authoring-method bounded-safe-author \
      --authoring-packet "$AUTHORING/packet.json" \
      --authoring-draft "$AUTHORING/draft.json" \
      --authoring-receipt "$AUTHORING/materialized/authoring.json" \
      --authoring-operation "$operation_path" \
      --source-id "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["packet_id"])' <<<"$materialization")" \
      --source-id "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["draft_id"])' <<<"$materialization")" \
      --source-id "$source_catalog_id" \
      --source-id "$operation_id"
}
expect_author_operation_refusal \
  "authoring-operation-default-model" \
  "authoring operation identity, outcome, or draft binding is invalid" \
  "$AUTHORING/default-model-author-operation.json"
expect_author_operation_refusal \
  "authoring-operation-wrong-adapter" \
  "authoring operation identity, outcome, or draft binding is invalid" \
  "$AUTHORING/wrong-adapter-author-operation.json"
expect_author_operation_refusal \
  "authoring-operation-wrong-packet" \
  "authoring operation identity, outcome, or draft binding is invalid" \
  "$AUTHORING/wrong-packet-author-operation.json"
expect_author_operation_refusal \
  "authoring-operation-wrong-draft" \
  "authoring operation identity, outcome, or draft binding is invalid" \
  "$AUTHORING/wrong-draft-author-operation.json"
expect_author_operation_refusal \
  "authoring-operation-elapsed-overrun" \
  "authoring operation exceeds the elapsed-time budget" \
  "$AUTHORING/elapsed-overrun-author-operation.json"
expect_author_operation_refusal \
  "authoring-operation-false-billing" \
  "authoring operation billing telemetry is invalid" \
  "$AUTHORING/false-billing-author-operation.json"
expect_author_operation_refusal \
  "authoring-operation-forged-id" \
  "authoring operation content identity is invalid" \
  "$AUTHORING/forged-id-author-operation.json"
forge_authoring_provenance() {
  local mode="$1" prefix="$AUTHORING/forged-$1"
  python3 - "$AUTHORING/packet.json" "$AUTHORING/draft.json" \
    "$AUTHORING/materialized/authoring.json" "$prefix" "$mode" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

packet_path, draft_path, receipt_path, prefix_arg, mode = sys.argv[1:]
prefix = Path(prefix_arg)
def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
def identity(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()
packet = json.load(open(packet_path))
if mode == "rubric":
    packet["source_catalog"]["rubric"]["identity"] = "sha256:" + "9" * 64
    packet["compilation_contract"]["rubric"] = packet["source_catalog"]["rubric"]
elif mode == "source-kind":
    packet["source_catalog"]["fixtures"][0]["source_kind"] = "private"
packet["source_catalog_id"] = identity(packet["source_catalog"])
packet_without_id = {key: value for key, value in packet.items() if key != "packet_id"}
packet["packet_id"] = identity(packet_without_id)
draft = json.load(open(draft_path))
draft["packet_id"] = packet["packet_id"]
draft_id = identity(draft)
receipt = json.load(open(receipt_path))
receipt["packet_id"] = packet["packet_id"]
receipt["draft_id"] = draft_id
receipt["source_catalog_id"] = packet["source_catalog_id"]
for suffix, value in (
    ("packet.json", packet), ("draft.json", draft), ("receipt.json", receipt)
):
    Path(f"{prefix}.{suffix}").write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    )
Path(f"{prefix}.meta.json").write_text(
    json.dumps(
        {
            "packet_id": packet["packet_id"],
            "draft_id": draft_id,
            "source_catalog_id": packet["source_catalog_id"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
)
PY
}
for forged in rubric source-kind; do
  forge_authoring_provenance "$forged"
  forged_prefix="$AUTHORING/forged-$forged"
  expect_refusal "authoring-forged-$forged" "caller-supplied authoring provenance is not accepted" \
    "$EVAL" v2-input-register "$AUTHORING/skill" \
      --suite "$AUTHORING/materialized/suite.json" \
      --policy "$AUTHORING/materialized/policy.json" \
      --config "$AUTHORING/materialized/compilation.json" \
      --routing "$AUTHORING/materialized/routing.json" \
      --harness "$HARNESS" --authoring-method bounded-safe-author \
      --authoring-packet "$forged_prefix.packet.json" \
      --authoring-draft "$forged_prefix.draft.json" \
      --authoring-receipt "$forged_prefix.receipt.json" \
      --authoring-operation "$AUTHORING/author-operation.json" \
      --source-id "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["packet_id"])' "$forged_prefix.meta.json")" \
      --source-id "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["draft_id"])' "$forged_prefix.meta.json")" \
      --source-id "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_catalog_id"])' "$forged_prefix.meta.json")" \
      --source-id "$author_operation_id"
  python3 - "$EVAL" "$AUTHORING" "$HARNESS" "$forged_prefix" "$forged" <<'PY'
import importlib.util
import json
import sys
from pathlib import Path

evaluator, root_arg, harness_arg, prefix_arg, mode = sys.argv[1:]
root = Path(root_arg)
prefix = Path(prefix_arg)
spec = importlib.util.spec_from_file_location("skill_evaluation", evaluator)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
skill = root / "skill"
candidate, files = module.candidate_id(skill)
suite, suite_id = module.load_suite(root / "materialized/suite.json")
policy, policy_id = module.load_policy(root / "materialized/policy.json")
harness = Path(harness_arg)
harness_sha = module.sha256_file(harness)
config_path = root / "materialized/compilation.json"
config, _ = module.validate_compilation_config(
    config_path, suite, policy, harness_sha
)
routing = module.validate_routing(
    root / "materialized/routing.json",
    config["executors"],
    config["comparator"],
)
try:
    module.validate_authoring_provenance(
        skill,
        candidate,
        files,
        suite,
        suite_id,
        policy,
        policy_id,
        config,
        routing,
        harness_sha,
        json.loads(Path(f"{prefix}.packet.json").read_text()),
        json.loads(Path(f"{prefix}.draft.json").read_text()),
        json.loads(Path(f"{prefix}.receipt.json").read_text()),
        json.loads((root / "author-operation.json").read_text()),
        module.sha256_file(Path(evaluator).resolve().with_name(
            "dreaming-vendor-adapter.py"
        )),
        module.canonical_file_inventory(config_path.parent / "fixtures"),
        module.canonical_file_inventory(config_path.parent / "graders"),
    )
except module.EvaluationError as error:
    expected = (
        "retained authoring catalog rubric"
        if mode == "rubric"
        else "not a safe declared fixture"
    )
    assert expected in str(error), (mode, error)
else:
    raise AssertionError(f"{mode} retained provenance forgery was accepted")
PY
done
for invalid_draft in fixture grader artifact semantic missing sensitive duplicate-task duplicate-prompt; do
  make_authoring_draft "$AUTHORING/packet.json" \
    "$AUTHORING/draft-$invalid_draft.json" "$invalid_draft"
  expect_refusal "authoring-draft-$invalid_draft" "REFUSED:" \
    materialize_authoring "$AUTHORING" "$AUTHORING/packet.json" \
      "$AUTHORING/draft-$invalid_draft.json" \
      "$AUTHORING/materialized-$invalid_draft"
done
expect_refusal "authoring-materialize-inside-skill" "cannot be written inside the skill root" \
  materialize_authoring "$AUTHORING" "$AUTHORING/packet.json" \
    "$AUTHORING/draft.json" "$AUTHORING/skill/materialized"
AUTHORING_DRIFT="$TMP/authoring-drift"
make_fixture "$AUTHORING_DRIFT"
author_packet "$AUTHORING_DRIFT" "$AUTHORING_DRIFT/packet.json" >/dev/null
make_authoring_draft "$AUTHORING_DRIFT/packet.json" "$AUTHORING_DRIFT/draft.json"
python3 - "$AUTHORING_DRIFT/config/compilation.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
value = json.loads(path.read_text())
value["identity_markers"].append("changed-safe-marker")
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
expect_refusal "authoring-source-drift" "differs from the current candidate and trusted sources" \
  materialize_authoring "$AUTHORING_DRIFT" "$AUTHORING_DRIFT/packet.json" \
    "$AUTHORING_DRIFT/draft.json" "$AUTHORING_DRIFT/materialized"
AUTHORING_CANDIDATE_DRIFT="$TMP/authoring-candidate-drift"
make_fixture "$AUTHORING_CANDIDATE_DRIFT"
author_packet "$AUTHORING_CANDIDATE_DRIFT" \
  "$AUTHORING_CANDIDATE_DRIFT/packet.json" >/dev/null
make_authoring_draft "$AUTHORING_CANDIDATE_DRIFT/packet.json" \
  "$AUTHORING_CANDIDATE_DRIFT/draft.json"
printf 'candidate changed\n' >"$AUTHORING_CANDIDATE_DRIFT/skill/reference.txt"
expect_refusal "authoring-candidate-drift" "differs from the current candidate and trusted sources" \
  materialize_authoring "$AUTHORING_CANDIDATE_DRIFT" \
    "$AUTHORING_CANDIDATE_DRIFT/packet.json" \
    "$AUTHORING_CANDIDATE_DRIFT/draft.json" \
    "$AUTHORING_CANDIDATE_DRIFT/materialized"
AUTHORING_LEGACY="$TMP/authoring-legacy"
make_fixture "$AUTHORING_LEGACY"
python3 - "$AUTHORING_LEGACY/skill/.skill-evaluation-cases.json" <<'PY'
import json
import sys
from pathlib import Path

case = lambda task, prompt: {
    "task_id": task,
    "prompt": prompt,
    "required_regex": [{"id": "success", "pattern": "SUCCESS"}],
    "forbidden_regex": [],
    "friction_regex": [],
}
Path(sys.argv[1]).write_text(
    json.dumps(
        {
            "schema_version": 1,
            "source": case("legacy:source-0001", "Complete the source task."),
            "sibling": case("legacy:sibling-0002", "Complete the sibling task."),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
)
PY
expect_refusal "authoring-legacy-suite" "requires a cross-executor schema-2 suite template" \
  author_packet "$AUTHORING_LEGACY" "$AUTHORING_LEGACY/packet.json"
pass "trusted materialization changes only prompts and task identities, rebinds current sources, and registers an exact external manifest"

make_unsafe_authoring_fixture() {
  local root="$1" mode="$2"
  make_fixture "$root"
  python3 - "$root" "$mode" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
mode = sys.argv[2]
catalog_path = root / "config/authoring-catalog.json"
catalog = json.loads(catalog_path.read_text())
if mode == "subjective":
    catalog["graders"][0]["objective"] = False
elif mode == "missing":
    catalog["fixtures"].pop()
elif mode == "description-token":
    catalog["fixtures"][0]["description"] = "Copied sk-proj-9fA2bQ7xLm4TzR8vNc1D"
elif mode == "compilation":
    config_path = root / "config/compilation.json"
    config = json.loads(config_path.read_text())
    config["identity_markers"].append("raw transcript from session")
    config_path.write_text(json.dumps(config, sort_keys=True, separators=(",", ":")) + "\n")
elif mode == "grader-tree":
    (root / "config/graders/contracts.json").write_text(
        "api_key=grader-tree-secret\n"
    )
else:
    fixture = catalog["fixtures"][0]
    path = root / "config/fixtures" / fixture["path"]
    content = {
        "transcript": b"raw transcript copied from a private session\n",
        "home": b"/Users/alice/private-state.json\n",
        "credential": b"api_key=super-secret-value\n",
        "bare-token": b"sk-proj-9fA2bQ7xLm4TzR8vNc1D\n",
    }[mode]
    path.write_bytes(content)
    fixture["sha256"] = "sha256:" + hashlib.sha256(content).hexdigest()
    fixture["size"] = len(content)
catalog_path.write_text(json.dumps(catalog, sort_keys=True, separators=(",", ":")))
PY
}

for unsafe in transcript home credential bare-token description-token compilation grader-tree subjective missing; do
  root="$TMP/authoring-$unsafe"
  make_unsafe_authoring_fixture "$root" "$unsafe"
  expect_refusal "authoring-$unsafe" "REFUSED:" \
    author_packet "$root" "$root/packet.json"
done
expect_refusal "authoring-inside-skill" "cannot be written inside the skill root" \
  author_packet "$AUTHORING" "$AUTHORING/skill/packet.json"
pass "model-facing authoring packets are candidate-bound, transcript-blind, path-sanitized, and limited to declared safe objective sources"

LOCAL_DEV="$TMP/local-development"
make_fixture "$LOCAL_DEV"
local_prepare="$("$EVAL" v2-prepare "$LOCAL_DEV/skill")"
python3 - "$local_prepare" <<'PY'
import json
import sys
value = json.loads(sys.argv[1])
assert value["input_manifest_sha256"] is None
assert value["cross_executor_authority"] is True
PY
mkdir "$LOCAL_DEV/run"
"$EVAL" v2-run-compile "$LOCAL_DEV/skill" --run-dir "$LOCAL_DEV/run" \
  --config "$LOCAL_DEV/config/compilation.json" \
  --routing "$LOCAL_DEV/config/routing.json" \
  --nonce local-development --harness "$HARNESS" >/dev/null
python3 - "$LOCAL_DEV/run/dreaming-input.json" <<'PY'
import json
import sys
assert json.load(open(sys.argv[1]))["input_manifest_sha256"] is None
PY
pass "root-local prepare and compile remain explicitly non-certifiable development inputs"

BASE="$TMP/base"
make_fixture "$BASE"
base_skill_before="$(skill_tree_digest "$BASE/skill")"
certification="$(run_fixture "$BASE" fixture-nonce)"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$certification")" == "pass" ]] ||
  fail "valid gate result did not certify"
mkdir "$BASE/replay-scratch"
replayed="$("$EVAL" v2-result-certify "$BASE/skill" --run-dir "$BASE/run" \
  --result-dir "$BASE/result" --routing "$BASE/config/routing.json" \
  --scratch "$BASE/replay-scratch" --nonce fixture-nonce --harness "$HARNESS")"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$replayed")" == "pass" ]] ||
  fail "retained normalized suite did not replay through certification"
pass "retained run evidence replays through the ready external input manifest"
python3 - "$BASE/run/source-suite.json" "$BASE/run/source-policy.json" "$BASE" <<'PY'
import json, sys
from pathlib import Path
suite_path, policy_path, root = map(Path, sys.argv[1:])
suite = json.loads(suite_path.read_text())
policy = json.loads(policy_path.read_text())
variants = {
    "suite-source-schema.json": {**suite, "compiled_from_schema_version": 1},
    "suite-no-authority.json": {**suite, "cross_executor_authority": False},
    "suite-half-marked.json": {
        key: value for key, value in suite.items() if key != "cross_executor_authority"
    },
    "suite-float-version.json": {**suite, "schema_version": 2.0},
    "suite-legacy-markers.json": {**suite, "schema_version": 1},
    "suite-bool-version.json": {**suite, "schema_version": True},
    "policy-wrong-trials.json": {**policy, "trials_per_arm": 1},
    "policy-float-trials.json": {**policy, "trials_per_arm": 3.0},
    "policy-bool-trials.json": {**policy, "profile": "iterate", "trials_per_arm": True},
}
for name, value in variants.items():
    root.joinpath(name).write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
expect_refusal suite-source-schema "invalid source schema" \
  "$EVAL" v2-suite-validate "$BASE/suite-source-schema.json"
expect_refusal suite-no-authority "must retain cross-executor authority" \
  "$EVAL" v2-suite-validate "$BASE/suite-no-authority.json"
expect_refusal suite-half-marked "suite has missing keys.*cross_executor_authority" \
  "$EVAL" v2-suite-validate "$BASE/suite-half-marked.json"
expect_refusal suite-float-version "normalized suite schema_version must be integer 2" \
  "$EVAL" v2-suite-validate "$BASE/suite-float-version.json"
expect_refusal suite-legacy-markers "normalized suite schema_version must be integer 2" \
  "$EVAL" v2-suite-validate "$BASE/suite-legacy-markers.json"
expect_refusal suite-bool-version "normalized suite schema_version must be integer 2" \
  "$EVAL" v2-suite-validate "$BASE/suite-bool-version.json"
expect_refusal policy-wrong-trials "profile-derived integer" \
  "$EVAL" v2-policy-validate "$BASE/policy-wrong-trials.json"
expect_refusal policy-float-trials "profile-derived integer" \
  "$EVAL" v2-policy-validate "$BASE/policy-float-trials.json"
expect_refusal policy-bool-trials "profile-derived integer" \
  "$EVAL" v2-policy-validate "$BASE/policy-bool-trials.json"
pass "retained suite and policy authority guards fail closed"
mkdir "$BASE/wrong-nonce-scratch"
if "$EVAL" v2-result-certify "$BASE/skill" --run-dir "$BASE/run" \
  --result-dir "$BASE/result" --routing "$BASE/config/routing.json" \
  --scratch "$BASE/wrong-nonce-scratch" --nonce wrong-nonce --harness "$HARNESS" \
  >"$BASE/wrong-nonce.out" 2>"$BASE/wrong-nonce.err"; then
  fail "wrong nonce certified"
fi
[[ "$(head -n 1 "$BASE/wrong-nonce.err")" == REFUSED:* ]] ||
  fail "wrong nonce refusal did not begin with REFUSED:"
grep -q "caller nonce mismatch" "$BASE/wrong-nonce.err" ||
  fail "wrong nonce refusal omitted verifier detail"
[[ "$(grep -c '^REFUSED:' "$BASE/wrong-nonce.err")" == "1" ]] ||
  fail "wrong nonce emitted multiple public REFUSED lines"
pass "nested verifier failures emit one public REFUSED line first"
aggregate="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["aggregate"])' <<<"$certification")"
authority="$("$EVAL" v2-authority-write "$BASE/skill" --aggregate "$aggregate")"
"$EVAL" v2-authority-validate "$BASE/skill" >/dev/null
authority_path="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["authority"])' <<<"$authority")"
ln -s "$SKILLS_STATE_DIR" "$TMP/state-alias"
SKILLS_STATE_DIR="$TMP/state-alias" \
  "$EVAL" v2-authority-validate "$BASE/skill" --authority "$authority_path" >/dev/null
aliased_authority="$(
  SKILLS_STATE_DIR="$TMP/state-alias" \
    "$EVAL" v2-authority-write "$BASE/skill" --aggregate "$aggregate"
)"
aliased_authority_path="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["authority"])' <<<"$aliased_authority"
)"
canonical_authority_path="$(
  python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' \
    "$aliased_authority_path"
)"
"$EVAL" v2-authority-validate "$BASE/skill" \
  --authority "$canonical_authority_path" >/dev/null
ln -s authority-loop "$TMP/authority-loop"
expect_refusal authority-symlink-loop "cannot" \
  "$EVAL" v2-authority-validate "$BASE/skill" \
  --authority "$TMP/authority-loop/authority.json"
[[ "$(grep -c '^REFUSED:' "$TMP/authority-symlink-loop.err")" == "1" ]] ||
  fail "authority symlink loop did not emit one public REFUSED line"
"$EVAL" current-gate "$BASE/skill" >/dev/null
transition_path="$(find "$SKILLS_STATE_DIR/skill-review/evaluations/v2/dashboard-v1/authority-transitions" -type f -name '*.json' | head -1)"
read -r current_at stale_at < <(
  python3 - "$transition_path" <<'PY'
import datetime, json, sys
value = json.load(open(sys.argv[1]))
at = datetime.datetime.fromisoformat(value["effective_at"])
print(
    (at + datetime.timedelta(days=1)).isoformat(),
    (at + datetime.timedelta(days=91)).isoformat(),
)
PY
)
portfolio_current="$("$EVAL" portfolio-current "$BASE/skill" --now "$current_at")"
python3 - "$portfolio_current" <<'PY'
import json, sys
value = json.loads(sys.argv[1])
assert value["state"] == "pass"
assert value["status"] == "pass"
assert value["current"] is True
assert value["receipt_sha256"]
assert value["transition_id"].startswith("sha256:")
assert value["input_manifest_sha256"].startswith("sha256:")
assert value["cases"]
PY
pass "portfolio inventory validates a current passing evaluation"
aggregate_sha="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["aggregate_receipt_sha256"])' <<<"$certification")"
portfolio_sha="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["portfolio_receipt_sha256"])' "$transition_path")"
python3 - \
  "$BASE/input-registry.json" \
  "$BASE/run/dreaming-input.json" \
  "$aggregate" \
  "$SKILLS_STATE_DIR/skill-review/evaluations/v2/certifications/$aggregate_sha.json" \
  "$authority_path" \
  "$SKILLS_STATE_DIR/skill-review/evaluations/v2/dashboard-v1/portfolio/$portfolio_sha.json" \
  "$transition_path" \
  "$portfolio_current" <<'PY'
import json
import sys
from pathlib import Path

registry = json.load(open(sys.argv[1]))
manifest = registry["registration"]["input_manifest_sha256"]
documents = [json.load(open(path)) for path in sys.argv[2:8]]
portfolio_current = json.loads(sys.argv[8])
assert all(document["input_manifest_sha256"] == manifest for document in documents)
assert portfolio_current["input_manifest_sha256"] == manifest
PY
[[ "$(skill_tree_digest "$BASE/skill")" == "$base_skill_before" ]] ||
  fail "external evaluation authority changed the installed skill root"
pass "external compile, certification, authority, currentness, and portfolio bind one manifest without changing the skill root"

materialized_run="$TMP/materialized-substitution-run"
cp -R "$BASE/run" "$materialized_run"
python3 - "$materialized_run" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
grader = root / "graders/contracts.json"
grader.write_text('{"forged":true}\n')
manifest = json.load(open(root / "manifest.json"))
inventory = []
for path in sorted(root.rglob("*")):
    relative = path.relative_to(root).as_posix()
    if path.is_dir() or relative == "manifest.json":
        continue
    content = path.read_bytes()
    inventory.append(
        {
            "path": relative,
            "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }
    )
manifest["file_inventory"] = inventory
fields = (
    "schema_version", "kind", "candidate_id", "suite_id", "profile",
    "trials_per_arm", "executors", "comparator",
    "harness_executable_sha256", "tool_policy_id", "grader_set_id",
    "retention_policy_id", "limits", "file_inventory",
)
raw = json.dumps(
    {key: manifest[key] for key in fields},
    sort_keys=True,
    separators=(",", ":"),
).encode()
manifest["run_id"] = "sha256:" + hashlib.sha256(raw).hexdigest()
(root / "manifest.json").write_text(
    json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
)
PY
mkdir "$TMP/materialized-substitution-result" "$TMP/materialized-substitution-scratch"
expect_refusal materialized-grader-substitution "fixture or grader objects differ" \
  "$EVAL" v2-run-execute --run-dir "$materialized_run" \
    --result-dir "$TMP/materialized-substitution-result" \
    --scratch "$TMP/materialized-substitution-scratch" --harness "$HARNESS"
pass "self-consistent run rewrites cannot substitute manifest fixtures or graders"

read -r input_manifest_path registry_object_path validation_receipt_path \
  review_receipt_path readiness_path input_current_path < <(
  python3 - "$BASE/input-registry.json" "$SKILLS_STATE_DIR" <<'PY'
import json
import sys
from pathlib import Path

registry = json.load(open(sys.argv[1]))
state = Path(sys.argv[2]) / "skill-review/evaluations/v2/input-registry"
manifest_sha = registry["registration"]["input_manifest_sha256"]
manifest_path = state / "manifests" / f"{manifest_sha.removeprefix('sha256:')}.json"
manifest = json.load(open(manifest_path))
fixture = next(item for item in manifest["objects"] if item["role"] == "fixture")
validation_sha = registry["validation"]["receipt_sha256"]
review_sha = registry["review_one"]["receipt_sha256"]
print(
    manifest_path,
    state / "objects" / fixture["sha256"].removeprefix("sha256:"),
    state / "reviews" / f"{validation_sha.removeprefix('sha256:')}.json",
    state / "reviews" / f"{review_sha.removeprefix('sha256:')}.json",
    registry["ready"]["transition"],
    registry["ready"]["current"],
)
PY
)

cp "$registry_object_path" "$BASE/registry-object.saved"
rm "$registry_object_path"
expect_refusal registry-object-missing "regular non-symlink file" \
  "$EVAL" v2-prepare "$BASE/skill"
mv "$BASE/registry-object.saved" "$registry_object_path"

cp "$registry_object_path" "$BASE/registry-object.saved"
printf 'tampered\n' >"$registry_object_path"
expect_refusal registry-object-tampered "digest, size, or path is invalid" \
  "$EVAL" v2-prepare "$BASE/skill"
expect_refusal registry-object-collision "input registry object collision" \
  "$EVAL" v2-input-register "$BASE/skill" \
    --suite "$BASE/skill/.skill-evaluation-cases.json" \
    --policy "$BASE/skill/.skill-evaluation-policy.json" \
    --config "$BASE/config/compilation.json" \
    --routing "$BASE/config/routing.json" --harness "$HARNESS" \
    --authoring-method deterministic-fixture \
    --source-id synthetic:certification-fixture
mv "$BASE/registry-object.saved" "$registry_object_path"

for registry_pair in \
  "object:$registry_object_path" \
  "manifest:$input_manifest_path" \
  "validation:$validation_receipt_path" \
  "review:$review_receipt_path" \
  "readiness:$readiness_path" \
  "current:$input_current_path"; do
  registry_name="${registry_pair%%:*}"
  registry_path="${registry_pair#*:}"
  saved="$BASE/registry-$registry_name.saved"
  cp "$registry_path" "$saved"
  rm "$registry_path"
  ln -s "$saved" "$registry_path"
  expect_refusal "registry-$registry_name-symlink" "non-symlink\\|cannot be a symlink" \
    "$EVAL" v2-prepare "$BASE/skill"
  rm "$registry_path"
  mv "$saved" "$registry_path"
done
readiness_skill_root="$(dirname "$(dirname "$readiness_path")")"
mv "$readiness_skill_root" "$BASE/readiness-root.saved"
ln -s "$BASE/readiness-root.saved" "$readiness_skill_root"
expect_refusal registry-readiness-parent-symlink "cannot traverse a symlink" \
  "$EVAL" v2-prepare "$BASE/skill"
rm "$readiness_skill_root"
mv "$BASE/readiness-root.saved" "$readiness_skill_root"
registry_root="$SKILLS_STATE_DIR/skill-review/evaluations/v2/input-registry"
mv "$registry_root" "$BASE/input-registry-root.saved"
ln -s "$BASE/input-registry-root.saved" "$registry_root"
expect_refusal registry-root-symlink "input registry root must be a real directory" \
  "$EVAL" v2-prepare "$BASE/skill"
rm "$registry_root"
mv "$BASE/input-registry-root.saved" "$registry_root"
pass "registry objects, manifests, receipts, transitions, and current pointers reject missing, tampered, colliding, or symlinked state"

cp "$input_current_path" "$BASE/input-current.saved"
python3 - "$input_current_path" <<'PY'
import json
import sys
path = sys.argv[1]
value = json.load(open(path))
value["transition_id"] = "sha256:" + "9" * 64
open(path, "w").write(json.dumps(value, sort_keys=True, separators=(",", ":")))
PY
expect_refusal input-pointer-mismatch "does not name the unique chain tip" \
  "$EVAL" v2-prepare "$BASE/skill"
mv "$BASE/input-current.saved" "$input_current_path"

cp "$input_current_path" "$BASE/input-current.saved"
python3 - "$input_current_path" "$BASE/input-registry.json" <<'PY'
import json
import sys
path = sys.argv[1]
value = json.load(open(path))
registry = json.load(open(sys.argv[2]))
value["input_manifest_sha256"] = registry["registration"]["input_manifest_sha256"]
open(path, "w").write(json.dumps(value, sort_keys=True, separators=(",", ":")))
PY
expect_refusal input-pointer-manifest-supply "unknown keys.*input_manifest_sha256" \
  "$EVAL" v2-prepare "$BASE/skill"
mv "$BASE/input-current.saved" "$input_current_path"

mv "$input_current_path" "$BASE/input-current.saved"
missing_registry_inventory="$(
  "$EVAL" portfolio-inventory "$BASE/skill" --now "$current_at"
)"
python3 - "$missing_registry_inventory" <<'PY'
import json
import sys
value = json.loads(sys.argv[1])
assert value["evaluations"][0]["evaluation"]["state"] == "invalid"
PY
mkdir "$BASE/no-fallback-scratch"
expect_refusal no-sidecar-fallback "current pointer is missing" \
  "$EVAL" v2-result-certify "$BASE/skill" --run-dir "$BASE/run" \
    --result-dir "$BASE/result" --routing "$BASE/config/routing.json" \
    --scratch "$BASE/no-fallback-scratch" --nonce fixture-nonce \
    --harness "$HARNESS"
mv "$BASE/input-current.saved" "$input_current_path"
pass "missing established readiness is invalid and never falls back to root sidecars"

POINTER_DOWNGRADE="$TMP/pointer-downgrade"
make_fixture "$POINTER_DOWNGRADE"
publish_fixture "$POINTER_DOWNGRADE"
pointer_downgrade_current="$(
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["ready"]["current"])' \
    "$POINTER_DOWNGRADE/input-registry.json"
)"
mv "$pointer_downgrade_current" "$POINTER_DOWNGRADE/current.saved"
mv "$POINTER_DOWNGRADE/skill/.skill-evaluation-policy.json" \
  "$POINTER_DOWNGRADE/policy.saved"
expect_refusal current-gate-pointer-downgrade "current pointer is missing" \
  "$EVAL" current-gate "$POINTER_DOWNGRADE/skill"
expect_refusal portfolio-pointer-downgrade "current pointer is missing" \
  "$EVAL" portfolio-current "$POINTER_DOWNGRADE/skill"
mv "$POINTER_DOWNGRADE/policy.saved" \
  "$POINTER_DOWNGRADE/skill/.skill-evaluation-policy.json"
mv "$POINTER_DOWNGRADE/current.saved" "$pointer_downgrade_current"
pass "current gate cannot downgrade established readiness to a legacy gate"

portfolio_stale="$("$EVAL" portfolio-current "$BASE/skill" --now "$stale_at")"
python3 - "$portfolio_stale" <<'PY'
import json, sys
value = json.loads(sys.argv[1])
assert value["state"] == "stale"
assert value["status"] == "pass"
assert value["current"] is False
PY
pass "portfolio inventory expires evaluation authority after 90 days"
transition_tie="$(
  python3 - "$transition_path" <<'PY'
import hashlib, json, pathlib, sys
source = pathlib.Path(sys.argv[1])
value = json.loads(source.read_text())
value.update({
    "candidate_id": "sha256:" + "f" * 64,
    "status": "revoked",
    "authority_sha256": None,
    "aggregate_receipt_sha256": None,
    "portfolio_receipt_sha256": None,
})
value.pop("transition_id")
raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
value["transition_id"] = "sha256:" + hashlib.sha256(raw).hexdigest()
target = source.with_name(value["transition_id"].removeprefix("sha256:") + ".json")
target.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
print(target)
PY
)"
expect_refusal portfolio-transition-time-collision "share an effective time" \
  "$EVAL" portfolio-current "$BASE/skill" --now "$current_at"
rm "$transition_tie"
pass "portfolio inventory rejects ambiguous transition ordering"
mkdir -p "$TMP/missing-evaluation"
printf '%s\n' '# Missing evaluation fixture' > "$TMP/missing-evaluation/SKILL.md"
portfolio_inventory="$(
  "$EVAL" portfolio-inventory "$BASE/skill" "$BASE/skill/../skill" \
    "$TMP/missing-evaluation" \
    --now "$current_at"
)"
python3 - "$portfolio_inventory" "$BASE/skill" "$TMP/missing-evaluation" <<'PY'
import json, sys
value = json.loads(sys.argv[1])
rows = {item["skill_path"]: item["evaluation"] for item in value["evaluations"]}
assert len(value["evaluations"]) == 2
assert rows[sys.argv[2]]["state"] == "pass"
assert rows[sys.argv[3]]["state"] == "input_missing"
PY
pass "bounded portfolio inventory deduplicates paths and retains missing evaluations"

READINESS="$TMP/readiness-lifecycle"
make_fixture "$READINESS"
readiness_root_before="$(
  python3 - "$READINESS/skill" <<'PY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
value = hashlib.sha256()
for path in sorted(root.rglob("*")):
    if path.is_file():
        value.update(path.relative_to(root).as_posix().encode())
        value.update(path.read_bytes())
print(value.hexdigest())
PY
)"
drafting="$(
  "$EVAL" v2-input-state "$READINESS/skill" \
    --state drafting --reason authoring_claimed \
    --created-at 2026-01-01T00:00:00Z
)"
drafting_projection="$(
  "$EVAL" portfolio-current "$READINESS/skill" --now "$current_at"
)"
registration="$(
  "$EVAL" v2-input-register "$READINESS/skill" \
    --suite "$READINESS/skill/.skill-evaluation-cases.json" \
    --policy "$READINESS/skill/.skill-evaluation-policy.json" \
    --config "$READINESS/config/compilation.json" \
    --routing "$READINESS/config/routing.json" \
    --harness "$HARNESS" \
    --authoring-method deterministic-fixture \
    --source-id synthetic:readiness-lifecycle
)"
manifest="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["input_manifest_sha256"])' <<<"$registration")"
validation="$("$EVAL" v2-input-validate "$READINESS/skill" --manifest "$manifest")"
validation_sha="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' <<<"$validation")"
review_one="$(
  "$EVAL" v2-input-review "$READINESS/skill" --manifest "$manifest" \
    --reviewer readiness-reviewer-one --decision accept
)"
review_one_sha="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' <<<"$review_one")"
"$EVAL" v2-input-state "$READINESS/skill" \
  --state review_required --reason validation_passed \
  --manifest "$manifest" --validation "$validation_sha" \
  --review "$review_one_sha" --created-at 2026-01-01T00:01:00Z >/dev/null
review_projection="$(
  "$EVAL" portfolio-current "$READINESS/skill" --now "$current_at"
)"
review_reject="$(
  "$EVAL" v2-input-review "$READINESS/skill" --manifest "$manifest" \
    --reviewer readiness-reviewer-two --decision reject
)"
review_reject_sha="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' <<<"$review_reject")"
"$EVAL" v2-input-state "$READINESS/skill" \
  --state invalid --reason independent_review_rejected \
  --manifest "$manifest" --validation "$validation_sha" \
  --review "$review_one_sha" --review "$review_reject_sha" \
  --created-at 2026-01-01T00:02:00Z >/dev/null
invalid_projection="$(
  "$EVAL" portfolio-current "$READINESS/skill" --now "$current_at"
)"
"$EVAL" v2-input-state "$READINESS/skill" \
  --state drafting --reason authoring_claimed \
  --created-at 2026-01-01T00:03:00Z >/dev/null
"$EVAL" v2-input-state "$READINESS/skill" \
  --state review_required --reason validation_passed \
  --manifest "$manifest" --validation "$validation_sha" \
  --review "$review_one_sha" --created-at 2026-01-01T00:04:00Z >/dev/null

INSUFFICIENT="$TMP/readiness-insufficient"
make_fixture "$INSUFFICIENT"
"$EVAL" v2-input-state "$READINESS/skill" \
  --state ready --reason validated_and_reviewed 2>/dev/null && \
  fail "generic input-state command accepted ready"
"$EVAL" v2-input-state "$INSUFFICIENT/skill" \
  --state insufficient_information --reason objective_grader_unavailable \
  --created-at 2026-01-01T00:00:00Z >/dev/null
insufficient_projection="$(
  "$EVAL" portfolio-current "$INSUFFICIENT/skill" --now "$current_at"
)"
expect_refusal readiness-private-authority \
  "insufficient_information readiness cannot bind input receipts" \
  "$EVAL" v2-input-state "$INSUFFICIENT/skill" \
    --state insufficient_information --reason objective_grader_unavailable \
    --manifest "$manifest"
expect_refusal readiness-terminal "cannot transition from insufficient_information" \
  "$EVAL" v2-input-state "$INSUFFICIENT/skill" \
    --state drafting --reason authoring_claimed
review_two="$(
  "$EVAL" v2-input-review "$READINESS/skill" --manifest "$manifest" \
    --reviewer readiness-reviewer-two --decision accept
)"
review_two_sha="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' <<<"$review_two")"
ready="$(
  "$EVAL" v2-input-ready "$READINESS/skill" --manifest "$manifest" \
    --validation "$validation_sha" --review "$review_one_sha" \
    --review "$review_two_sha" --created-at 2026-01-01T00:05:00Z
)"
python3 - "$EVAL" "$READINESS/skill" "$current_at" \
  "$drafting" "$manifest" "$ready" "$drafting_projection" \
  "$review_projection" "$invalid_projection" "$insufficient_projection" <<'PY'
import json, subprocess, sys
(
    evaluator, skill, observed_at, drafting_raw, manifest, ready_raw,
    drafting_projection, review_projection, invalid_projection,
    insufficient_projection,
) = sys.argv[1:]
drafting = json.loads(drafting_raw)
ready = json.loads(ready_raw)
assert drafting["state"] == "drafting"
assert ready["state"] == "ready"
assert ready["input_manifest_sha256"] == manifest
assert json.loads(drafting_projection)["state"] == "drafting"
assert json.loads(review_projection)["state"] == "review_required"
assert json.loads(invalid_projection)["state"] == "invalid"
assert json.loads(insufficient_projection)["state"] == "insufficient_information"
assert all(
    json.loads(item)["evaluated_at"] is None
    for item in (
        drafting_projection,
        review_projection,
        invalid_projection,
        insufficient_projection,
    )
)
value = json.loads(subprocess.check_output(
    [evaluator, "portfolio-current", skill, "--now", observed_at],
    text=True,
))
assert value["state"] == "ready"
assert value["status"] == "ready"
assert value["current"] is False
assert value["evaluated_at"] is None
assert value["input_manifest_sha256"] == manifest
PY
review_three="$(
  "$EVAL" v2-input-review "$READINESS/skill" --manifest "$manifest" \
    --reviewer readiness-reviewer-three --decision accept
)"
review_three_sha="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_sha256"])' <<<"$review_three")"
ready_republished="$(
  "$EVAL" v2-input-ready "$READINESS/skill" --manifest "$manifest" \
    --validation "$validation_sha" --review "$review_one_sha" \
    --review "$review_three_sha" --created-at 2026-01-01T00:06:00Z
)"
python3 - "$ready" "$ready_republished" <<'PY'
import json
import sys

first, second = map(json.loads, sys.argv[1:])
assert first["transition_id"] != second["transition_id"]
second_transition = json.load(open(second["transition"]))
assert second_transition["prior_transition_id"] == first["transition_id"]
assert second["state"] == "ready"
PY
readiness_current="$(
  python3 - "$ready_republished" <<'PY'
import json, sys
print(json.loads(sys.argv[1])["current"])
PY
)"
cp "$readiness_current" "$READINESS/current.saved"
python3 - "$readiness_current" "$drafting" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
pointer = json.loads(path.read_text())
pointer["transition_id"] = json.loads(sys.argv[2])["transition_id"]
path.write_text(json.dumps(pointer, sort_keys=True) + "\n")
PY
expect_refusal readiness-pointer-rollback "does not name the unique chain tip" \
  "$EVAL" portfolio-current "$READINESS/skill" --now "$current_at"
mv "$READINESS/current.saved" "$readiness_current"
readiness_root_after="$(
  python3 - "$READINESS/skill" <<'PY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
value = hashlib.sha256()
for path in sorted(root.rglob("*")):
    if path.is_file():
        value.update(path.relative_to(root).as_posix().encode())
        value.update(path.read_bytes())
print(value.hexdigest())
PY
)"
[[ "$readiness_root_before" == "$readiness_root_after" ]] ||
  fail "readiness lifecycle changed the installed skill root"
pass "readiness lifecycle is append-only, fail-closed, projected, and external to the skill root"

CROSS_CANDIDATE="$TMP/readiness-cross-candidate"
make_fixture "$CROSS_CANDIDATE"
publish_fixture "$CROSS_CANDIDATE"
cross_current="$(
  python3 - "$CROSS_CANDIDATE/input-registry.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["ready"]["current"])
PY
)"
cp "$cross_current" "$CROSS_CANDIDATE/old-current.json"
printf '%s\n' 'Changed candidate contract.' >> "$CROSS_CANDIDATE/skill/SKILL.md"
publish_fixture "$CROSS_CANDIDATE"
cp "$CROSS_CANDIDATE/old-current.json" "$cross_current"
expect_refusal readiness-cross-candidate-rollback \
  "readiness pointer candidate is stale" \
  "$EVAL" portfolio-current "$CROSS_CANDIDATE/skill" --now "$current_at"
pass "an older candidate pointer cannot hide established current-candidate readiness"

ln -s "$TMP/unresolvable-loop" "$TMP/unresolvable-loop"
unresolvable_inventory="$(
  "$EVAL" portfolio-inventory "$BASE/skill" "$TMP/unresolvable-loop" \
    --now "$current_at"
)"
python3 - "$unresolvable_inventory" "$BASE/skill" "$TMP/unresolvable-loop" <<'PY'
import json, os, sys
value = json.loads(sys.argv[1])
rows = {item["skill_path"]: item["evaluation"] for item in value["evaluations"]}
assert rows[sys.argv[2]]["state"] == "pass"
assert rows[os.path.abspath(sys.argv[3])]["state"] == "invalid"
PY
pass "unresolvable paths cannot suppress valid portfolio inventory rows"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["authoritative"])' <<<"$certification")" == "True" ]] ||
  fail "passing gate result was not marked authoritative"
pass "compile, execute, independent verify, certificates, aggregate, and canonical authority paths pass end to end"

policy_before="$("$EVAL" v2-policy-validate "$BASE/skill/.skill-evaluation-policy.json")"
cp "$BASE/skill/.skill-evaluation-policy.json" "$BASE/policy.advisory-saved"
cp "$BASE/config/compilation.json" "$BASE/compilation.advisory-saved"
python3 - "$BASE/skill/.skill-evaluation-policy.json" "$BASE/config/compilation.json" <<'PY'
import json, sys
policy_path, compilation_path = sys.argv[1:]
policy = json.load(open(policy_path))
policy["advisory_executors"][0]["model"] = "claude-observation-model-2"
open(policy_path, "w").write(json.dumps(policy, sort_keys=True, separators=(",", ":")) + "\n")
compilation = json.load(open(compilation_path))
compilation["executors"][1]["model"] = "claude-observation-model-2"
open(compilation_path, "w").write(
    json.dumps(compilation, sort_keys=True, separators=(",", ":")) + "\n"
)
PY
policy_after="$("$EVAL" v2-policy-validate "$BASE/skill/.skill-evaluation-policy.json")"
unreviewed_registration="$(
  "$EVAL" v2-input-register "$BASE/skill" \
    --suite "$BASE/skill/.skill-evaluation-cases.json" \
    --policy "$BASE/skill/.skill-evaluation-policy.json" \
    --config "$BASE/config/compilation.json" \
    --routing "$BASE/config/routing.json" \
    --harness "$HARNESS" \
    --authoring-method deterministic-fixture \
    --source-id synthetic:advisory-variant
)"
unreviewed_manifest="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["input_manifest_sha256"])' <<<"$unreviewed_registration")"
unreviewed_validation="$(
  "$EVAL" v2-input-validate "$BASE/skill" --manifest "$unreviewed_manifest"
)"
python3 - "$policy_before" "$policy_after" "$BASE/input-registry.json" \
  "$unreviewed_registration" "$SKILLS_STATE_DIR" <<'PY'
import json, sys
before, after = (json.loads(item) for item in sys.argv[1:3])
registry = json.load(open(sys.argv[3]))
unreviewed = json.loads(sys.argv[4])
state = sys.argv[5]
reviewed_sha = registry["registration"]["input_manifest_sha256"]
unreviewed_sha = unreviewed["input_manifest_sha256"]
reviewed = json.load(open(
    f"{state}/skill-review/evaluations/v2/input-registry/manifests/"
    f"{reviewed_sha.removeprefix('sha256:')}.json"
))
variant = json.load(open(
    f"{state}/skill-review/evaluations/v2/input-registry/manifests/"
    f"{unreviewed_sha.removeprefix('sha256:')}.json"
))
assert before["policy_id"] == after["policy_id"]
assert before["observation_plan_id"] != after["observation_plan_id"]
assert reviewed_sha != unreviewed_sha
assert reviewed["complete_policy_sha256"] != variant["complete_policy_sha256"]
PY
mv "$BASE/policy.advisory-saved" "$BASE/skill/.skill-evaluation-policy.json"
mv "$BASE/compilation.advisory-saved" "$BASE/config/compilation.json"
first_validation="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["validation"]["receipt_sha256"])' "$BASE/input-registry.json")"
first_review_one="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["review_one"]["receipt_sha256"])' "$BASE/input-registry.json")"
first_review_two="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["review_two"]["receipt_sha256"])' "$BASE/input-registry.json")"
expect_refusal unreviewed-manifest-swap "does not bind the exact manifest" \
  "$EVAL" v2-input-ready "$BASE/skill" --manifest "$unreviewed_manifest" \
    --validation "$first_validation" --review "$first_review_one" \
    --review "$first_review_two" --created-at 2026-01-02T00:00:00Z
"$EVAL" current-gate "$BASE/skill" >/dev/null
pass "complete advisory policy identity is distinct and cannot borrow readiness from a reviewed manifest"

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
cp "$BASE/skill/.agent-created.json" "$BASE/envelope.saved"
printf '{"schema_version":1,"fixture":"downgrade"}\n' > "$BASE/skill/.skill-evaluation-policy.json"
python3 - "$BASE/skill/.agent-created.json" <<'PY'
import json
import sys
path = sys.argv[1]
value = json.load(open(path))
value["evaluation_v3_sha256"] = "0" * 64
open(path, "w").write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
"$EVAL" v2-authority-validate "$BASE/skill" >/dev/null
"$EVAL" current-gate "$BASE/skill" >/dev/null
"$EVAL" portfolio-current "$BASE/skill" --now "$current_at" >/dev/null
mv "$BASE/policy.saved" "$BASE/skill/.skill-evaluation-policy.json"
mv "$BASE/envelope.saved" "$BASE/skill/.agent-created.json"
pass "external authority ignores root-local policy and stale evaluation_v3 pointers"

mv "$BASE/config" "$BASE/config.removed"
"$EVAL" v2-authority-validate "$BASE/skill" >/dev/null
"$EVAL" current-gate "$BASE/skill" >/dev/null
mv "$BASE/config.removed" "$BASE/config"
pass "external authority replay uses retained registry routing instead of authoring paths"

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
printf '%s\n' "$regression" >"$REGRESSION/certification.json"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$regression")" == "regression" ]] ||
  {
    aggregate="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("aggregate", ""))' <<<"$regression")"
    if [[ -n "$aggregate" ]]; then
      "$EVAL" v2-authority-write "$REGRESSION/skill" --aggregate "$aggregate" \
        >"$REGRESSION/authority-attempt.out" 2>"$REGRESSION/authority-attempt.err" || true
    fi
    dump_certification_diagnostics \
      "activation regression" "$REGRESSION" "$REGRESSION/certification.json"
    fail "activation regression did not block certification"
  }
pass "activation and related regression policy fails closed"

INFRA="$TMP/infrastructure"
make_fixture "$INFRA" gate capability_uplift collect-fail
infrastructure="$(run_fixture "$INFRA" infrastructure-nonce)"
[[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$infrastructure")" == "inconclusive" ]] ||
  fail "infrastructure failure was scored as candidate behavior"
pass "inconclusive infrastructure cannot authorize"

ADVISORY_INFRA="$TMP/advisory-infrastructure"
make_fixture "$ADVISORY_INFRA"
python3 - "$ADVISORY_INFRA/config/routing.json" <<'PY'
import json, sys
p=sys.argv[1]; d=json.load(open(p))
d["executors"][2]["argv"] += ["--fixture", "collect-fail"]
open(p,"w").write(json.dumps(d,sort_keys=True,separators=(",",":"))+"\n")
PY
advisory_infrastructure="$(run_fixture "$ADVISORY_INFRA" advisory-infrastructure-nonce)"
python3 - "$advisory_infrastructure" <<'PY'
import json, sys
value=json.loads(sys.argv[1])
statuses={item["executor"]["name"]:item["status"] for item in value["certificates"]}
assert value["status"] == "pass", value
assert statuses == {"copilot":"pass","claude":"pass","codex":"inconclusive"}, statuses
PY
advisory_aggregate="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["aggregate"])' <<<"$advisory_infrastructure")"
"$EVAL" v2-authority-write "$ADVISORY_INFRA/skill" --aggregate "$advisory_aggregate" >/dev/null
"$EVAL" current-gate "$ADVISORY_INFRA/skill" >/dev/null
pass "advisory infrastructure failure remains visible without blocking required authority"

ADVISORY_REGRESSION="$TMP/advisory-regression"
make_fixture "$ADVISORY_REGRESSION"
python3 - "$ADVISORY_REGRESSION/config/routing.json" <<'PY'
import json, sys
p=sys.argv[1]; d=json.load(open(p))
d["executors"][1]["argv"] += ["--fixture", "artifact-missing"]
open(p,"w").write(json.dumps(d,sort_keys=True,separators=(",",":"))+"\n")
PY
advisory_regression="$(run_fixture "$ADVISORY_REGRESSION" advisory-regression-nonce)"
python3 - "$advisory_regression" <<'PY'
import json, sys
value=json.loads(sys.argv[1])
statuses={item["executor"]["name"]:item["status"] for item in value["certificates"]}
assert value["status"] == "pass", value
assert statuses["copilot"] == "pass" and statuses["claude"] == "regression", statuses
PY
pass "advisory behavioral regression remains visible without changing the required decision"

UNAVAILABLE="$TMP/unavailable"
make_fixture "$UNAVAILABLE"
publish_fixture "$UNAVAILABLE"
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
