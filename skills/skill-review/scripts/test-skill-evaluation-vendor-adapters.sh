#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
TEST_ROOT="$ROOT/.test-work"
mkdir -p "$TEST_ROOT"
# shellcheck source=lib-test-work.sh
source "$ROOT/skills/skill-review/scripts/lib-test-work.sh"
prune_test_work "$TEST_ROOT" "skill-evaluation-vendor-adapters" 2
WORK="$(mktemp -d "$TEST_ROOT/skill-evaluation-vendor-adapters.XXXXXX")"
cleanup() {
  local status=$?
  trap - EXIT
  finish_test_work "$status" "$WORK" "vendor-adapter" 1
  exit "$status"
}
trap cleanup EXIT

python3 - "$ROOT" "$WORK" <<'PY'
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

root = Path(sys.argv[1])
work = Path(sys.argv[2])
sys.argv = [sys.argv[0]]
adapter = root / "skills/skill-review/scripts/dreaming-vendor-adapter.py"
harness_path = root / "skills/skill-review/scripts/skill-evaluation-harness.py"
harness_spec = importlib.util.spec_from_file_location("skill_evaluation_harness", harness_path)
harness = importlib.util.module_from_spec(harness_spec)
harness_spec.loader.exec_module(harness)
adapter_spec = importlib.util.spec_from_file_location(
    "dreaming_vendor_adapter_native_schema_guard", adapter
)
adapter_module = importlib.util.module_from_spec(adapter_spec)
sys.modules[adapter_spec.name] = adapter_module
adapter_spec.loader.exec_module(adapter_module)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha_bytes(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha(value):
    return sha_bytes(canonical(value))


class SkillEvaluationVendorAdapterTest(unittest.TestCase):
    def setUp(self):
        self.root = work / self._testMethodName
        self.root.mkdir()
        self.bin = work / f"{self._testMethodName}-bin"
        self.bin.mkdir()
        self.credentials = work / f"{self._testMethodName}-credentials"
        self.credentials.mkdir()
        for relative in (
            ".config/gh/hosts.yml",
            ".claude/.credentials.json",
            ".codex/auth.json",
        ):
            path = self.credentials / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n")
        cli = self.root / "fixture-cli.py"
        cli.write_text(
            """import json, os, sys
from pathlib import Path
vendor = os.environ["FIXTURE_VENDOR"]
args = sys.argv[1:]
if "--version" in args:
    print(vendor + " 1.0")
    raise SystemExit()
if "--help" in args:
    available_tools = (
        "--available-tools.invalid"
        if os.environ.get("FIXTURE_INVALID_HELP_BOUNDARY")
        else "--available-tools[=tools...]"
    )
    print("--plugin-dir --output-format --model --json --ignore-user-config " +
          available_tools + " --disable-builtin-mcps --no-custom-instructions "
          "--no-ask-user --no-remote")
    raise SystemExit()
if "plugin" in args:
    home = Path(os.environ["CODEX_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    if "marketplace" in args and "add" in args:
        (home / "candidate-root").write_text(args[-1])
    if "add" in args and "marketplace" not in args:
        (home / "candidate-installed").write_text("yes")
    print(json.dumps({"ok": True}))
    raise SystemExit()
model = args[args.index("--model") + 1]
full_prompt = args[args.index("-p") + 1] if "-p" in args else ""
prompt = next((value for value in args if value in {
    "fixture prompt", "DRIFT", "NOLOAD", "WRONGLOAD", "FALSETRIGGER", "TIMEOUT",
    "TOKENOVER", "FLOOD", "SCHEMA", "NOPATH", "NATIVEFAIL", "CURRENTCOPILOT",
    "SHADOWCANDIDATE", "SHADOWCATALOG", "COMMANDLOAD", "MULTIREAD", "NATIVEV2",
}), "")
comparator = full_prompt.startswith("BLIND_SKILL_EVALUATION_COMPARISON")
answer = (
    json.dumps({
        "winner":"A",
        "criteria":[{"id":"quality","score":1,"reason":"A is clearer"}],
        "evidence":"A directly satisfies the supplied rubric",
    }, sort_keys=True)
    if comparator else "answer"
)
observed = "drifted-model" if prompt == "DRIFT" else model
workspace = Path(args[args.index("-C") + 1]) if "-C" in args else Path.cwd()
(workspace / "out.txt").write_text("artifact\\n")
(workspace / "undeclared.txt").write_text("must not be collected\\n")
if prompt == "TIMEOUT":
    (workspace / "native.pid").write_text(str(os.getpid()))
    import time
    time.sleep(30)
if prompt == "FLOOD":
    print("x" * 200000, flush=True)
    import time
    time.sleep(30)
tokens = 1000 if prompt == "TOKENOVER" else 15
if prompt == "SCHEMA":
    print(json.dumps({"type":"new_skill_activation_event"}))
if prompt == "NATIVEFAIL":
    if vendor == "codex":
        print(json.dumps({"type":"error","message":"fixture native failure"}))
    raise SystemExit(1)
candidate = False
loaded_path = None
if "--plugin-dir" in args:
    plugin = Path(args[args.index("--plugin-dir") + 1])
    matches = list((plugin / "skills").glob("*/SKILL.md"))
    desired = (
        "fixture-skill"
        if prompt == "SHADOWCANDIDATE"
        else "approved-skill" if prompt == "SHADOWCATALOG" else None
    )
    selected = next(
        (match for match in matches if match.parent.name == desired),
        matches[0] if matches else None,
    )
    candidate = selected is not None
    loaded_path = str(selected) if selected else None
elif vendor == "codex":
    native_matches = list(
        (Path(os.environ["CODEX_HOME"]) / "skills").glob("*/SKILL.md")
    )
    candidate = (
        (Path(os.environ["CODEX_HOME"]) / "candidate-installed").is_file()
        or bool(native_matches)
    )
    root_file = Path(os.environ["CODEX_HOME"]) / "candidate-root"
    if root_file.is_file():
        loaded_path = str(
            Path(root_file.read_text()) / "skills/fixture-skill/SKILL.md"
        )
    elif native_matches:
        loaded_path = str(native_matches[0])
candidate = candidate and prompt != "NOLOAD"
loaded_name = (
    "other-skill"
    if prompt == "WRONGLOAD"
    else "approved-skill" if prompt == "SHADOWCATALOG" else "fixture-skill"
)
if vendor == "copilot":
    if prompt == "NATIVEV2" or "NATIVEV2" in full_prompt:
        events = [
            {"type":"session.start","data":{"model":observed}},
            {"type":"model.call_start","data":{"model":observed,"turn":0}},
            {"type":"model.messages_snapshot","data":{
                "messages":[{
                    "type":"assistant.message",
                    "content":"nested snapshot answer",
                    "outputTokens":999999,
                }],
                "decoys":[
                    {"type":"model.call_start","data":{
                        "model":"nested-model",
                        "toolRequests":[{"toolCallId":"nested-tool"}],
                    }},
                    {"type":"assistant.message","data":{
                        "content":"nested assistant",
                        "toolRequests":[{"name":"skill","toolCallId":"nested-skill"}],
                    }},
                    {"type":"assistant.turn_end","data":{"content":"nested final"}},
                    {"type":"session.task_complete","data":{"summary":"nested summary"}},
                    {"type":"tool.execution_start","data":{"toolCallId":"nested-tool"}},
                    {"type":"tool.execution_complete","data":{
                        "toolCallId":"nested-tool","success":True,
                        "result":{"content":"nested tool result"},
                    }},
                    {"type":"skill.invoked","data":{"skillName":"nested-skill"}},
                    {"type":"session.error","data":{"message":"nested failure"}},
                ],
            }},
            {"id":"fixture-v2-call","type":"model.model_call_success","data":{
                "responseChunk":{"usage":{
                    "prompt_tokens":8349,
                    "completion_tokens":103,
                    "total_tokens":8452,
                }},
                "responseUsage":{
                    "prompt_tokens":8349,
                    "completion_tokens":103,
                    "total_tokens":8452,
                },
            }},
            {"type":"assistant.message","data":{"content":answer}},
            {"type":"assistant.turn_end","data":{"content":answer}},
            {"type":"session.task_complete","data":{"summary":answer}},
        ]
    elif prompt in {"CURRENTCOPILOT", "SHADOWCANDIDATE", "SHADOWCATALOG"}:
        events = [
            {"type":"session.skills_loaded","data":{"skills":[
                {"name":loaded_name,"path":loaded_path,"enabled":True}
            ] if candidate else []}},
            {"type":"session.tools_updated","data":{"model":observed}},
            {"type":"model.call_start","data":{"model":observed}},
            {"type":"assistant.message","data":{
                "content":"",
                "outputTokens":tokens,
                "usage":{"input_tokens":10,"output_tokens":tokens},
                "toolRequests":[{
                    "arguments":{"skill":loaded_name},
                    "name":"skill",
                    "toolCallId":"current-skill-call",
                    "type":"function",
                }] if candidate else [],
            }},
        ]
    else:
        events = [{"type":"session.start","data":{"model":observed}},
                  *([{"type":"model.call_start","data":{"model":observed}}]
                    if comparator and not os.environ.get(
                        "FIXTURE_COMPARATOR_ZERO_TURN"
                    ) else []),
                  {"type":"assistant.message","data":{
                      "content":answer,
                      **({"outputTokens":5} if comparator else {})}},
                  {"type":"session.usage_checkpoint",
                   "usage":{"total_tokens":tokens}}]
    if candidate and prompt not in {"CURRENTCOPILOT", "SHADOWCANDIDATE", "SHADOWCATALOG"}:
        events.append({"type":"skill.invoked","data":{
            "skillName":loaded_name,"resolvedPath":loaded_path}})
    if prompt in {"CURRENTCOPILOT", "SHADOWCANDIDATE", "SHADOWCATALOG"}:
        events.extend([
            {"type":"tool.execution_complete","data":{
                "toolCallId":"current-skill-call",
                "success":True,
                "result":{"content":f'Skill "{loaded_name}" loaded successfully.'},
            }} if candidate else
            {"type":"session.tools_updated","data":{"model":observed}},
            {"type":"assistant.message",
             "data":{"content":"answer","outputTokens":0,"toolRequests":[]}},
            {"type":"assistant.turn_end","data":{}},
            {"type":"result","exitCode":0},
        ])
    else:
        events.append({"type":"session.task_complete","data":{"summary":answer}})
    print(json.dumps({"events":events}))
elif vendor == "claude":
    system = {"type":"system","model":observed}
    if candidate:
        system.update({
            "subtype":"init",
            "plugins":[{"name":"fixture-plugin","path":str(plugin)}],
            "skills":["fixture-plugin:fixture-skill"],
        })
    print(json.dumps(system))
    content = [{"type":"text","text":"answer"}]
    if candidate:
        skill_input = (
            {"skill":"fixture-plugin:fixture-skill"}
            if prompt == "NOPATH"
            else {"skill":loaded_name,"resolved_path":loaded_path}
        )
        content.append({"type":"tool_use","name":"Skill",
                        "input":skill_input})
    print(json.dumps({"type":"assistant","message":{"content":content}}))
    print(json.dumps({"type":"result","result":"answer",
                      "usage":{"total_tokens":tokens,
                               "input_tokens":10,"output_tokens":tokens-10}}))
else:
    print(json.dumps({"type":"turn_context","payload":{"model":observed}}))
    print(json.dumps({"type":"turn.completed",
                      "usage":{"total_tokens":tokens,
                               "input_tokens":10,"output_tokens":tokens-10}}))
    if candidate:
        if prompt in {"COMMANDLOAD", "MULTIREAD"}:
            skill_files = [
                Path(os.environ["CODEX_HOME"])
                / "skills/fixture-skill/SKILL.md"
            ]
            if prompt == "MULTIREAD":
                skill_files.append(
                    Path(os.environ["CODEX_HOME"])
                    / "skills/approved-skill/SKILL.md"
                )
            print(json.dumps({"type":"item.completed","item":{
                "id":"command-load",
                "type":"command_execution",
                "status":"completed",
                "exit_code":0,
                "command":"/bin/zsh -lc '/bin/cat " + " ".join(
                    str(skill_file) for skill_file in skill_files
                ) + "'",
                "aggregated_output":"reader diagnostic\\n" + "".join(
                    skill_file.read_text() for skill_file in skill_files
                ),
            }}))
        else:
            print(json.dumps({"type":"response_item","payload":{"type":"function_call",
                  "name":"skill","arguments":{
                      "skill":loaded_name,"resolved_path":loaded_path}}}))
    print(json.dumps({"type":"response_item","payload":{"type":"message",
          "role":"assistant","content":[{"type":"text","text":"answer"}]}}))
"""
        )
        self.binaries = {}
        for vendor in ("copilot", "claude", "codex"):
            binary = self.bin / vendor
            source = (
                "#include <unistd.h>\n#include <stdlib.h>\n"
                "int main(int argc,char **argv){"
                f"setenv(\"FIXTURE_VENDOR\",\"{vendor}\",1);"
                f"setenv(\"DREAMING_EXECUTOR_TEST_ALLOW_ROOT\",\"{self.root}\",1);"
                f"char **a=calloc(argc+2,sizeof(char*));a[0]=\"{sys.executable}\";"
                f"a[1]=\"{cli}\";for(int i=1;i<argc;i++)a[i+1]=argv[i];"
                f"execv(\"{sys.executable}\",a);return 127;}}\n"
            )
            subprocess.run(
                ["/usr/bin/clang", "-x", "c", "-o", str(binary), "-"],
                input=source,
                text=True,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.binaries[vendor] = binary
        self.invalid_help_binary = self.bin / "copilot-invalid-help"
        invalid_help_source = (
            "#include <unistd.h>\n#include <stdlib.h>\n"
            "int main(int argc,char **argv){"
            "setenv(\"FIXTURE_VENDOR\",\"copilot\",1);"
            "setenv(\"FIXTURE_INVALID_HELP_BOUNDARY\",\"1\",1);"
            f"setenv(\"DREAMING_EXECUTOR_TEST_ALLOW_ROOT\",\"{self.root}\",1);"
            f"char **a=calloc(argc+2,sizeof(char*));a[0]=\"{sys.executable}\";"
            f"a[1]=\"{cli}\";for(int i=1;i<argc;i++)a[i+1]=argv[i];"
            f"execv(\"{sys.executable}\",a);return 127;}}\n"
        )
        subprocess.run(
            [
                "/usr/bin/clang",
                "-x",
                "c",
                "-o",
                str(self.invalid_help_binary),
                "-",
            ],
            input=invalid_help_source,
            text=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def base(self, vendor, timeout=10, token_budget=100):
        return [
            sys.executable,
            str(adapter),
            "--vendor",
            vendor,
            "--role",
            "skill-evaluation-executor",
            "--binary",
            str(self.binaries[vendor]),
            "--credential-root",
            str(self.credentials),
            "--model",
            "fixture-model",
            "--timeout",
            str(timeout),
            "--token-budget",
            str(token_budget),
            "--output-bytes",
            "100000",
        ]

    def call(
        self,
        vendor,
        *args,
        check=True,
        adapter_timeout=120,
        cwd=None,
        token_budget=100,
        extra_env=None,
    ):
        started = time.monotonic()
        result = subprocess.run(
            [*self.base(vendor, adapter_timeout, token_budget), *map(str, args)],
            env={
                **os.environ,
                "DREAMING_EXECUTOR_TEST_ALLOW_ROOT": str(self.root),
                "GH_TOKEN": "fixture-token",
                **(extra_env or {}),
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
        )
        self.last_call = {
            "argv": [*self.base(vendor, adapter_timeout, token_budget), *map(str, args)],
            "duration_seconds": time.monotonic() - started,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        if check and result.returncode:
            self.fail(result.stdout + result.stderr)
        return json.loads(result.stdout.splitlines()[-1])

    def comparator_call(
        self,
        rubric_id,
        *args,
        check=True,
        binary=None,
        extra_env=None,
        token_budget=100,
    ):
        harness_home = self.root / "harness-home"
        harness_home.mkdir(exist_ok=True)
        command = [
            sys.executable,
            str(adapter),
            "--vendor",
            "copilot",
            "--role",
            "skill-evaluation-comparator",
            "--binary",
            str(binary or self.binaries["copilot"]),
            "--credential-root",
            str(self.credentials),
            "--model",
            "fixture-model",
            "--route-name",
            "copilot-blind-comparator",
            "--rubric-id",
            rubric_id,
            "--timeout",
            "10",
            "--token-budget",
            str(token_budget),
            "--output-bytes",
            "100000",
            *map(str, args),
        ]
        result = subprocess.run(
            command,
            env={
                **os.environ,
                "HOME": str(harness_home),
                "DREAMING_EXECUTOR_TEST_ALLOW_ROOT": str(self.root),
                "GH_TOKEN": "fixture-token",
                **(extra_env or {}),
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if check and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def test_blind_comparator_is_identity_bound_and_structured(self):
        rubric = {
            "criteria": [
                {
                    "id": "quality",
                    "description": "Prefer the response that completes the task",
                }
            ]
        }
        rubric_id = sha(rubric)
        version_result = self.comparator_call(rubric_id, "version")
        version = json.loads(version_result.stdout)
        self.assertEqual(version["route"], "copilot-blind-comparator")
        self.assertEqual(version["model"], "fixture-model")
        self.assertEqual(version["rubric_id"], rubric_id)
        doctor = json.loads(
            self.comparator_call(rubric_id, "doctor").stdout
        )
        self.assertTrue(doctor["healthy"])
        self.assertTrue(doctor["boundary_ready"])
        invalid_help = self.comparator_call(
            rubric_id,
            "doctor",
            check=False,
            binary=self.invalid_help_binary,
        )
        self.assertNotEqual(invalid_help.returncode, 0)
        self.assertIn("comparator-boundary-unavailable", invalid_help.stdout)
        packet = self.root / "comparison-packet.json"
        packet.write_bytes(
            canonical(
                {
                    "schema_version": 1,
                    "task_id": "task-one",
                    "task": "Complete the supplied formatting task.",
                    "rubric": rubric,
                    "A": "complete response",
                    "B": "incomplete response",
                }
            )
        )
        output = self.root / "comparison-result.json"
        response = self.comparator_call(
            rubric_id,
            "compare",
            "--packet",
            packet,
            "--output",
            output,
        )
        receipt = json.loads(response.stdout)
        verdict = json.loads(output.read_text())
        self.assertEqual(verdict["winner"], "A")
        self.assertEqual(receipt["execution"], version)
        self.assertEqual(receipt["response_sha256"], sha_bytes(output.read_bytes()))
        zero_binary = self.bin / "copilot-zero-turn"
        zero_source = (
            "#include <unistd.h>\n#include <stdlib.h>\n"
            "int main(int argc,char **argv){"
            'setenv("FIXTURE_VENDOR","copilot",1);'
            'setenv("FIXTURE_COMPARATOR_ZERO_TURN","1",1);'
            f'setenv("DREAMING_EXECUTOR_TEST_ALLOW_ROOT","{self.root}",1);'
            f'char **a=calloc(argc+2,sizeof(char*));a[0]="{sys.executable}";'
            f'a[1]="{self.root / "fixture-cli.py"}";'
            "for(int i=1;i<argc;i++)a[i+1]=argv[i];"
            f'execv("{sys.executable}",a);return 127;}}\n'
        )
        subprocess.run(
            ["/usr/bin/clang", "-x", "c", "-o", str(zero_binary), "-"],
            input=zero_source,
            text=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        zero_turn = self.comparator_call(
            rubric_id,
            "compare",
            "--packet",
            packet,
            "--output",
            self.root / "zero-turn-result.json",
            check=False,
            binary=zero_binary,
        )
        self.assertNotEqual(zero_turn.returncode, 0)
        self.assertIn("comparator-no-tools-unproved", zero_turn.stdout)
        refused = self.comparator_call(
            "sha256:" + "0" * 64,
            "compare",
            "--packet",
            packet,
            "--output",
            self.root / "refused-result.json",
            check=False,
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("comparator-packet-invalid", refused.stdout)

    def write_timeout_diagnostic(self, vendor, trial, response, phase):
        workspace = Path(trial["workspace"])
        marker = workspace / "native.pid"
        diagnostic = {
            "vendor": vendor,
            "phase": phase,
            "response": response,
            "workspace_exists": workspace.is_dir(),
            "pid_file_exists": marker.is_file(),
            "pid": marker.read_text().strip() if marker.is_file() else None,
            "raw_output_exists": Path(trial["raw"]).exists(),
            "adapter_call": getattr(self, "last_call", None),
        }
        path = self.root / f"{vendor}-{phase}-timeout-diagnostic.json"
        path.write_text(json.dumps(diagnostic, sort_keys=True, indent=2) + "\n")
        return diagnostic

    def trial(self, vendor, treatment="candidate", prompt="fixture prompt"):
        trial_root = self.root / f"{vendor}-{treatment}-{prompt.lower()}"
        home = trial_root / "home"
        workspace = trial_root / "workspace"
        artifacts = trial_root / "artifacts"
        candidate = trial_root / "candidate"
        for path in (home, workspace, artifacts):
            path.mkdir(parents=True)
        inventory = []
        skill_digest = None
        candidate_root = None
        if treatment == "candidate":
            candidate.mkdir()
            skill = candidate / "SKILL.md"
            skill.write_text(
                "---\nname: fixture-skill\ndescription: Fixture skill.\n---\n"
            )
            skill.chmod(0o400)
            skill_digest = sha_bytes(skill.read_bytes())
            inventory = [
                {"path": "SKILL.md", "sha256": skill_digest, "size": skill.stat().st_size}
            ]
            candidate_root = str(candidate)
        spec = {
            "schema_version": 1,
            "trial_id": sha({"vendor": vendor, "treatment": treatment, "prompt": prompt}),
            "case": {
                "id": "fixture",
                "class": "intended",
                "task_id": "fixture",
                "prompt": prompt,
                "fixture": "native-fixture",
                "artifacts": ["out.txt"],
                "graders": ["fixture"],
                "semantic": False,
            },
            "treatment": treatment,
            "executor": {"name": vendor},
            "candidate_id": sha(inventory),
            "candidate_inventory": inventory,
            "skill_md_sha256": skill_digest,
            "home": str(home),
            "workspace": str(workspace),
            "candidate_root": candidate_root,
            "raw": str(trial_root / "raw.jsonl"),
            "trace": str(trial_root / "trace.json"),
            "artifacts": str(artifacts),
        }
        path = trial_root / "trial.json"
        path.write_bytes(canonical(spec) + b"\n")
        return spec, path

    def shadow_trial(self, treatment, prompt):
        trial_root = self.root / f"copilot-shadow-{treatment}-{prompt.lower()}"
        home = trial_root / "home"
        workspace = trial_root / "workspace"
        artifacts = trial_root / "artifacts"
        candidate = self.root / "shadow-run/candidate"
        catalog = self.root / "shadow-run/catalog"
        for path in (home, workspace, artifacts, candidate, catalog / "approved-skill"):
            path.mkdir(parents=True, exist_ok=True)
        candidate_skill = candidate / "SKILL.md"
        if not candidate_skill.exists():
            candidate_skill.write_text(
                "---\nname: fixture-skill\ndescription: Fixture skill.\n---\n"
            )
            candidate_skill.chmod(0o400)
        catalog_skill = catalog / "approved-skill/SKILL.md"
        if not catalog_skill.exists():
            catalog_skill.write_text(
                "---\nname: approved-skill\ndescription: Approved fixture skill.\n---\n"
            )
            catalog_skill.chmod(0o400)
        candidate_inventory = [{
            "path": "SKILL.md",
            "sha256": sha_bytes(candidate_skill.read_bytes()),
            "size": candidate_skill.stat().st_size,
        }]
        catalog_inventory = [{
            "path": "approved-skill/SKILL.md",
            "sha256": sha_bytes(catalog_skill.read_bytes()),
            "size": catalog_skill.stat().st_size,
        }]
        catalog_skill_inventory = [{
            **catalog_inventory[0],
            "path": "SKILL.md",
        }]
        identity = self.call(
            "copilot",
            "--shadow-contract",
            "--turn-budget",
            "7",
            "--tool-budget",
            "8",
            "version",
        )
        spec = {
            "schema_version": 2,
            "trial_id": sha({"treatment": treatment, "prompt": prompt}),
            "case": {
                "id": prompt.lower(),
                "class": "task_value",
                "task_id": f"task:{prompt.lower()}",
                "prompt": prompt,
                "critical": True,
                "routing": {
                    "candidate_load": treatment == "candidate",
                    "catalog_loads": ["approved-skill"] if prompt == "SHADOWCATALOG" else [],
                },
                "artifacts": ["out.txt"],
                "graders": ["fixture"],
                "fixture": "native-fixture",
            },
            "treatment": treatment,
            "executor": {"name": "copilot", **identity},
            "candidate_id": sha(candidate_inventory),
            "candidate_inventory": candidate_inventory,
            "skill_md_sha256": candidate_inventory[0]["sha256"],
            "candidate_root": str(candidate),
            "catalog_id": sha(catalog_inventory),
            "catalog_root": str(catalog),
            "catalog_skills": [{
                "name": "approved-skill",
                "catalog_skill_id": sha(catalog_skill_inventory),
                "skill_md_sha256": catalog_inventory[0]["sha256"],
                "path": "catalog/approved-skill/SKILL.md",
            }],
            "suite_id": sha({"suite": 2}),
            "environment_id": sha({"environment": 2}),
            "workspace": str(workspace),
            "raw": str(trial_root / "raw.jsonl"),
            "trace": str(trial_root / "trace.json"),
            "artifacts": str(artifacts),
        }
        path = trial_root / "trial.json"
        path.write_bytes(canonical(spec) + b"\n")
        return spec, path, identity

    def prepare_and_run(
        self,
        vendor,
        treatment="candidate",
        prompt="fixture prompt",
        adapter_timeout=120,
    ):
        trial, trial_path = self.trial(vendor, treatment, prompt)
        prepared = self.call(
            vendor,
            "prepare",
            "--trial",
            trial_path,
            adapter_timeout=adapter_timeout,
        )
        record = {
            "schema_version": 1,
            "trial_id": trial["trial_id"],
            "adapter_prepared": prepared["prepared"],
            "execution": prepared["execution"],
        }
        record["prepared_digest"] = sha(record)
        prepared_path = trial_path.parent / "prepared.json"
        prepared_path.write_bytes(canonical(record) + b"\n")
        run = self.call(
            vendor,
            "run",
            "--trial",
            trial_path,
            "--prepared",
            prepared_path,
            "--output",
            trial["raw"],
            check=prompt not in {
                "DRIFT", "TIMEOUT", "TOKENOVER", "FLOOD", "SCHEMA"
            },
            adapter_timeout=adapter_timeout,
        )
        return trial, trial_path, prepared, run

    def test_contract_doctor_version_and_command_construction(self):
        keychains = self.credentials / "Library/Keychains"
        keychains.mkdir(parents=True)
        for index in range(1200):
            (keychains.parent / f"Sibling-{index:04d}").mkdir()
        commands = {}
        identities = []
        tool_policy_ids = []
        for vendor in ("copilot", "claude", "codex"):
            contract = self.call(
                vendor, "contract", "--role", "skill-evaluation-executor"
            )
            self.assertEqual(
                contract["protocol"], "dreaming.skill-evaluation-executor"
            )
            doctor = self.call(vendor, "doctor", cwd="/")
            self.assertTrue(doctor["healthy"])
            version = self.call(vendor, "version")
            identities.append(set(version))
            tool_policy_ids.append(version["tool_policy_id"])
            trial, path = self.trial(vendor)
            prepared = self.call(vendor, "prepare", "--trial", path)
            self.assertEqual(prepared["execution"], version)
            shadow_version = self.call(
                vendor,
                "--shadow-contract",
                "--turn-budget",
                "7",
                "--tool-budget",
                "8",
                "version",
            )
            self.assertEqual(shadow_version["real_backend"], True)
            self.assertIn(f"native-{vendor}-cli", shadow_version["real_backend_source"])
            self.assertEqual(shadow_version["limits"]["turn_budget"], 7)
            self.assertEqual(shadow_version["limits"]["tool_budget"], 8)
            self.assertEqual(
                set(shadow_version),
                set(version) | {"real_backend", "real_backend_source"},
            )
            shadow_trial, shadow_path = self.trial(vendor, prompt="shadow fixture")
            shadow_prepared = self.call(
                vendor,
                "--shadow-contract",
                "--turn-budget",
                "7",
                "--tool-budget",
                "8",
                "prepare",
                "--trial",
                shadow_path,
            )
            self.assertEqual(shadow_prepared["execution"], shadow_version)
            if vendor == "codex":
                shadow_command = shadow_prepared["prepared"]["command"]
                self.assertNotIn("--ignore-user-config", shadow_command)
                self.assertIn(
                    "--dangerously-bypass-approvals-and-sandbox",
                    shadow_command,
                )
                self.assertIn('web_search="disabled"', shadow_command)
                shadow_projection = shadow_prepared["prepared"]["projection"]
                native_skill = (
                    Path(shadow_projection["native_skill_root"])
                    / shadow_projection["skill_name"]
                )
                self.assertTrue(native_skill.is_dir())
                self.assertFalse(native_skill.is_symlink())
                self.assertEqual(
                    native_skill.joinpath("SKILL.md").read_bytes(),
                    Path(shadow_trial["candidate_root"]).joinpath("SKILL.md").read_bytes(),
                )
                self.assertFalse(
                    Path(shadow_trial["home"])
                    .joinpath(".codex/candidate-installed")
                    .exists()
                )
            commands[vendor] = prepared["prepared"]["command"]
            profile = Path(trial["home"]) / "evaluation.sb"
            self.assertLess(profile.stat().st_size, 65535)
            self.assertIn("(deny network*)", profile.read_text())
            if vendor == "copilot":
                broad_home_read = (
                    "(allow file-read* "
                    f'(require-all (subpath "{self.credentials.resolve()}") '
                    f'(process-path "{self.binaries[vendor].resolve()}")))'
                )
                self.assertNotIn(broad_home_read, profile.read_text())
                self.assertIn(
                    "(allow file-read-metadata "
                    f'(require-all (subpath "{self.credentials.resolve()}") '
                    f'(process-path "{self.binaries[vendor].resolve()}")))',
                    profile.read_text(),
                )
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            denied = subprocess.run(
                [
                    "/usr/bin/sandbox-exec",
                    "-f",
                    str(profile),
                    "/usr/bin/nc",
                    "-z",
                    "-w",
                    "1",
                    "127.0.0.1",
                    str(listener.getsockname()[1]),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            listener.close()
            self.assertNotEqual(denied.returncode, 0)
            before = self.binaries[vendor].read_bytes()
            denied_write = subprocess.run(
                [
                    "/usr/bin/sandbox-exec",
                    "-f",
                    str(profile),
                    "/bin/sh",
                    "-c",
                    'printf pwned >> "$1"',
                    "fixture",
                    str(self.binaries[vendor]),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(denied_write.returncode, 0)
            self.assertEqual(self.binaries[vendor].read_bytes(), before)
            self.assertEqual(
                prepared["prepared"]["projection"]["inventory"],
                trial["candidate_inventory"],
            )
        self.assertEqual(identities[0], identities[1])
        self.assertEqual(len(set(tool_policy_ids)), 1)
        self.assertIn("--plugin-dir", commands["copilot"])
        self.assertIn("--plugin-dir", commands["claude"])
        self.assertNotIn("--bare", commands["claude"])
        self.assertTrue(
            next(self.root.glob("claude-candidate-*/home/.claude/.credentials.json")).is_file()
        )
        self.assertFalse(
            next(self.root.glob("claude-candidate-*/home")).joinpath(".claude.json").exists()
        )
        self.assertIn("--ignore-user-config", commands["codex"])
        self.assertIn("--json", commands["codex"])

    def test_shadow_trial_projects_candidate_and_catalog_with_complete_usage(self):
        for treatment, prompt, present, absent in (
            ("candidate", "SHADOWCANDIDATE", "candidate_id", "catalog_skill_id"),
            ("control", "SHADOWCATALOG", "catalog_skill_id", "candidate_id"),
        ):
            trial, path, identity = self.shadow_trial(treatment, prompt)
            prepared = self.call(
                "copilot",
                "--shadow-contract",
                "--turn-budget",
                "7",
                "--tool-budget",
                "8",
                "prepare",
                "--trial",
                path,
            )
            record = {
                "schema_version": 2,
                "trial_id": trial["trial_id"],
                "adapter_prepared": prepared["prepared"],
                "execution": prepared["execution"],
            }
            record["prepared_digest"] = sha(record)
            prepared_path = path.parent / "prepared.json"
            prepared_path.write_bytes(canonical(record) + b"\n")
            response = self.call(
                "copilot",
                "--shadow-contract",
                "--turn-budget",
                "7",
                "--tool-budget",
                "8",
                "run",
                "--trial",
                path,
                "--prepared",
                prepared_path,
                "--output",
                trial["raw"],
            )
            self.assertEqual(response["effective_execution"], identity)
            self.call(
                "copilot",
                "--shadow-contract",
                "--turn-budget",
                "7",
                "--tool-budget",
                "8",
                "normalize",
                "--raw",
                trial["raw"],
                "--trace",
                trial["trace"],
            )
            events = json.loads(Path(trial["trace"]).read_text())["events"]
            loads = [event["data"] for event in events if event["kind"] == "skill_load"]
            self.assertEqual(len(loads), 1)
            self.assertIsNotNone(loads[0][present])
            self.assertIsNone(loads[0][absent])
            usage = [event["data"] for event in events if event["kind"] == "usage"]
            self.assertEqual(
                set(usage[0]),
                {"turns", "input_tokens", "output_tokens", "total_tokens", "tool_calls"},
            )
            self.assertEqual(
                usage[0]["total_tokens"],
                usage[0]["input_tokens"] + usage[0]["output_tokens"],
            )

    def test_codex_shadow_command_read_proves_exact_skill_load(self):
        trial, path = self.trial("codex", prompt="COMMANDLOAD")
        flags = (
            "--shadow-contract",
            "--turn-budget",
            "7",
            "--tool-budget",
            "8",
        )
        prepared = self.call("codex", *flags, "prepare", "--trial", path)
        record = {
            "schema_version": 1,
            "trial_id": trial["trial_id"],
            "adapter_prepared": prepared["prepared"],
            "execution": prepared["execution"],
        }
        record["prepared_digest"] = sha(record)
        prepared_path = path.parent / "prepared.json"
        prepared_path.write_bytes(canonical(record) + b"\n")
        self.call(
            "codex",
            *flags,
            "run",
            "--trial",
            path,
            "--prepared",
            prepared_path,
            "--output",
            trial["raw"],
        )
        self.call(
            "codex",
            *flags,
            "normalize",
            "--raw",
            trial["raw"],
            "--trace",
            trial["trace"],
        )
        events = json.loads(Path(trial["trace"]).read_text())["events"]
        loads = [event["data"] for event in events if event["kind"] == "skill_load"]
        self.assertEqual(len(loads), 1)
        self.assertEqual(loads[0]["candidate_id"], trial["candidate_id"])
        self.assertEqual(loads[0]["path"], "candidate/SKILL.md")
        usage = next(event["data"] for event in events if event["kind"] == "usage")
        self.assertEqual(usage["tool_calls"], 1)

    def test_codex_shadow_command_read_attests_every_exact_skill(self):
        flags = (
            "--shadow-contract",
            "--turn-budget",
            "7",
            "--tool-budget",
            "8",
        )
        trial, path, _ = self.shadow_trial("candidate", "MULTIREAD")
        identity = self.call("codex", *flags, "version")
        trial["executor"] = {"name": "codex", **identity}
        path.write_bytes(canonical(trial) + b"\n")
        prepared = self.call("codex", *flags, "prepare", "--trial", path)
        record = {
            "schema_version": 1,
            "trial_id": trial["trial_id"],
            "adapter_prepared": prepared["prepared"],
            "execution": prepared["execution"],
        }
        record["prepared_digest"] = sha(record)
        prepared_path = path.parent / "prepared.json"
        prepared_path.write_bytes(canonical(record) + b"\n")
        self.call(
            "codex",
            *flags,
            "run",
            "--trial",
            path,
            "--prepared",
            prepared_path,
            "--output",
            trial["raw"],
        )
        self.call(
            "codex",
            *flags,
            "normalize",
            "--raw",
            trial["raw"],
            "--trace",
            trial["trace"],
        )
        events = json.loads(Path(trial["trace"]).read_text())["events"]
        loads = [event["data"] for event in events if event["kind"] == "skill_load"]
        self.assertEqual(len(loads), 2)
        self.assertEqual(
            {load["path"] for load in loads},
            {"candidate/SKILL.md", "catalog/approved-skill/SKILL.md"},
        )

    def test_candidate_control_isolation_normalization_and_collection_parity(self):
        candidate_kinds = []
        for vendor in ("copilot", "claude", "codex"):
            candidate, candidate_path, prepared, run = self.prepare_and_run(vendor)
            self.assertEqual(run["effective_execution"], prepared["execution"])
            normalized = self.call(
                vendor,
                "normalize",
                "--raw",
                candidate["raw"],
                "--trace",
                candidate["trace"],
            )
            self.assertEqual(
                normalized["raw_sha256"], sha_bytes(Path(candidate["raw"]).read_bytes())
            )
            trace = json.loads(Path(candidate["trace"]).read_text())
            loads = [event for event in trace["events"] if event["kind"] == "skill_load"]
            self.assertEqual(len(loads), 1)
            self.assertEqual(loads[0]["data"]["candidate_id"], candidate["candidate_id"])
            self.assertEqual(
                loads[0]["data"]["skill_md_sha256"], candidate["skill_md_sha256"]
            )
            candidate_kinds.append([event["kind"] for event in trace["events"]])
            collected = self.call(
                vendor,
                "collect",
                "--trial",
                candidate_path,
                "--artifacts",
                candidate["artifacts"],
            )
            self.assertEqual(
                collected["declared_artifacts"],
                [{"path": "out.txt", "source_exists": True}],
            )
            self.assertEqual(
                (Path(candidate["artifacts"]) / "out.txt").read_text(), "artifact\n"
            )
            self.assertFalse(
                (Path(candidate["artifacts"]) / "undeclared.txt").exists()
            )

            control, _, control_prepared, _ = self.prepare_and_run(
                vendor, treatment="control"
            )
            self.assertEqual(control_prepared["prepared"]["projection"]["inventory"], [])
            self.call(
                vendor,
                "normalize",
                "--raw",
                control["raw"],
                "--trace",
                control["trace"],
            )
            control_trace = json.loads(Path(control["trace"]).read_text())
            self.assertFalse(
                any(event["kind"] == "skill_load" for event in control_trace["events"])
            )
        self.assertTrue(
            all(kinds == candidate_kinds[0] for kinds in candidate_kinds),
            candidate_kinds,
        )

    def test_exact_model_drift_fails_closed(self):
        for vendor in ("copilot", "claude", "codex"):
            _, _, _, response = self.prepare_and_run(vendor, prompt="DRIFT")
            self.assertEqual(response["error"]["code"], "exact-model-unproved")

    def test_current_native_load_and_failure_shapes(self):
        copilot, _, _, _ = self.prepare_and_run(
            "copilot",
            treatment="candidate",
            prompt="CURRENTCOPILOT",
        )
        self.call(
            "copilot",
            "normalize",
            "--raw",
            copilot["raw"],
            "--trace",
            copilot["trace"],
        )
        copilot_events = json.loads(Path(copilot["trace"]).read_text())["events"]
        self.assertEqual(
            [event["text"] for event in copilot_events if event["kind"] == "final_answer"],
            ["answer"],
        )
        self.assertEqual(
            [event["data"]["total_tokens"] for event in copilot_events if event["kind"] == "usage"],
            [25],
        )
        copilot_proof = harness.proof(
            copilot_events,
            "candidate",
            copilot["candidate_id"],
            copilot["skill_md_sha256"],
            "intended",
        )
        self.assertTrue(
            copilot_proof[0],
            (
                copilot_proof,
                [event for event in copilot_events if event["kind"] == "skill_load"],
            ),
        )

        trial, _, _, _ = self.prepare_and_run("claude", prompt="NOPATH")
        self.call(
            "claude",
            "normalize",
            "--raw",
            trial["raw"],
            "--trace",
            trial["trace"],
        )
        events = json.loads(Path(trial["trace"]).read_text())["events"]
        self.assertTrue(
            harness.proof(
                events,
                "candidate",
                trial["candidate_id"],
                trial["skill_md_sha256"],
                "intended",
            )[0]
        )

        failed, failed_path = self.trial("codex", prompt="NATIVEFAIL")
        prepared = self.call("codex", "prepare", "--trial", failed_path)
        record = {
            "schema_version": 1,
            "trial_id": failed["trial_id"],
            "adapter_prepared": prepared["prepared"],
            "execution": prepared["execution"],
        }
        record["prepared_digest"] = sha(record)
        prepared_path = failed_path.parent / "prepared.json"
        prepared_path.write_bytes(canonical(record) + b"\n")
        response = self.call(
            "codex",
            "run",
            "--trial",
            failed_path,
            "--prepared",
            prepared_path,
            "--output",
            failed["raw"],
            check=False,
        )
        self.assertEqual(response["error"]["code"], "executor-failed")
        self.assertEqual(response["error"]["message"], "fixture native failure")

    def test_copilot_shape_b_run_and_comparator_share_usage_authority(self):
        accepted_budget = 10000
        refused_budget = 8451
        trial, trial_path = self.trial("copilot", prompt="NATIVEV2")
        prepared = self.call(
            "copilot", "prepare", "--trial", trial_path, token_budget=accepted_budget
        )
        record = {
            "schema_version": 1,
            "trial_id": trial["trial_id"],
            "adapter_prepared": prepared["prepared"],
            "execution": prepared["execution"],
        }
        record["prepared_digest"] = sha(record)
        prepared_path = trial_path.parent / "prepared.json"
        prepared_path.write_bytes(canonical(record) + b"\n")
        run = self.call(
            "copilot",
            "run",
            "--trial",
            trial_path,
            "--prepared",
            prepared_path,
            "--output",
            trial["raw"],
            check=False,
            token_budget=accepted_budget,
        )
        with self.subTest(boundary="run-accepts-complete-metered-stream"):
            self.assertNotIn("error", run)
            if "error" not in run:
                native_records = [
                    json.loads(line)
                    for line in Path(trial["raw"]).read_text().splitlines()
                ]
                usage = next(
                    record
                    for record in native_records
                    if record["type"] == "dreaming.usage"
                )
                self.assertEqual(usage["total_tokens"], 8452)

        rubric = {
            "criteria": [
                {"id": "quality", "description": "Prefer the complete answer."}
            ]
        }
        packet = self.root / "shape-b-comparison-packet.json"
        packet.write_bytes(
            canonical(
                {
                    "schema_version": 1,
                    "task_id": "shape-b",
                    "task": "NATIVEV2: Compare the supplied answers.",
                    "rubric": rubric,
                    "A": "complete response",
                    "B": "incomplete response",
                }
            )
        )
        comparison = self.comparator_call(
            sha(rubric),
            "compare",
            "--packet",
            packet,
            "--output",
            self.root / "shape-b-comparison-result.json",
            check=False,
            token_budget=accepted_budget,
        )
        with self.subTest(boundary="comparator-accepts-complete-metered-stream"):
            self.assertEqual(
                comparison.returncode, 0, comparison.stdout + comparison.stderr
            )

        refused, refused_path = self.trial(
            "copilot", treatment="control", prompt="NATIVEV2"
        )
        refused_prepared = self.call(
            "copilot",
            "prepare",
            "--trial",
            refused_path,
            token_budget=refused_budget,
        )
        refused_record = {
            "schema_version": 1,
            "trial_id": refused["trial_id"],
            "adapter_prepared": refused_prepared["prepared"],
            "execution": refused_prepared["execution"],
        }
        refused_record["prepared_digest"] = sha(refused_record)
        refused_prepared_path = refused_path.parent / "prepared.json"
        refused_prepared_path.write_bytes(canonical(refused_record) + b"\n")
        refused_run = self.call(
            "copilot",
            "run",
            "--trial",
            refused_path,
            "--prepared",
            refused_prepared_path,
            "--output",
            refused["raw"],
            check=False,
            token_budget=refused_budget,
        )
        expected_refusal = {
            "code": "token-limit-exceeded",
            "message": f"8452 > {refused_budget}",
        }
        with self.subTest(boundary="run-refuses-the-same-metered-total"):
            self.assertEqual(refused_run.get("error"), expected_refusal)
            self.assertFalse(Path(refused["raw"]).exists())

        refused_output = self.root / "shape-b-refused-comparison-result.json"
        refused_comparison = self.comparator_call(
            sha(rubric),
            "compare",
            "--packet",
            packet,
            "--output",
            refused_output,
            check=False,
            token_budget=refused_budget,
        )
        with self.subTest(boundary="comparator-refuses-the-same-metered-total"):
            self.assertNotEqual(
                refused_comparison.returncode,
                0,
                refused_comparison.stdout + refused_comparison.stderr,
            )
            refusal = json.loads(refused_comparison.stdout.splitlines()[-1])
            self.assertEqual(refusal.get("error"), expected_refusal)
            self.assertFalse(refused_output.exists())

    def test_load_proof_activation_and_projection_drift_fail_closed(self):
        for vendor in ("copilot", "claude", "codex"):
            missing, _, _, _ = self.prepare_and_run(vendor, prompt="NOLOAD")
            self.call(
                vendor,
                "normalize",
                "--raw",
                missing["raw"],
                "--trace",
                missing["trace"],
            )
            missing_events = json.loads(Path(missing["trace"]).read_text())["events"]
            self.assertFalse(
                harness.proof(
                    missing_events,
                    "candidate",
                    missing["candidate_id"],
                    missing["skill_md_sha256"],
                    "intended",
                )[0]
            )
            self.assertTrue(
                harness.proof(
                    missing_events,
                    "candidate",
                    missing["candidate_id"],
                    missing["skill_md_sha256"],
                    "related",
                )[0]
            )
            self.assertTrue(
                harness.proof(
                    missing_events,
                    "candidate",
                    missing["candidate_id"],
                    missing["skill_md_sha256"],
                    "activation_negative",
                )[0]
            )

            wrong, _, _, _ = self.prepare_and_run(vendor, prompt="WRONGLOAD")
            self.call(
                vendor,
                "normalize",
                "--raw",
                wrong["raw"],
                "--trace",
                wrong["trace"],
            )
            wrong_events = json.loads(Path(wrong["trace"]).read_text())["events"]
            self.assertFalse(
                harness.proof(
                    wrong_events,
                    "candidate",
                    wrong["candidate_id"],
                    wrong["skill_md_sha256"],
                    "intended",
                )[0]
            )

            triggered, _, _, _ = self.prepare_and_run(
                vendor, prompt="FALSETRIGGER"
            )
            self.call(
                vendor,
                "normalize",
                "--raw",
                triggered["raw"],
                "--trace",
                triggered["trace"],
            )
            triggered_events = json.loads(Path(triggered["trace"]).read_text())[
                "events"
            ]
            self.assertFalse(
                harness.proof(
                    triggered_events,
                    "candidate",
                    triggered["candidate_id"],
                    triggered["skill_md_sha256"],
                    "activation_negative",
                )[0]
            )

            trial, trial_path = self.trial(vendor)
            prepared = self.call(vendor, "prepare", "--trial", trial_path)
            record = {
                "schema_version": 1,
                "trial_id": trial["trial_id"],
                "adapter_prepared": prepared["prepared"],
                "execution": prepared["execution"],
            }
            record["prepared_digest"] = sha(record)
            prepared_path = trial_path.parent / "prepared.json"
            prepared_path.write_bytes(canonical(record) + b"\n")
            tampered = json.loads(json.dumps(record))
            tampered["adapter_prepared"]["projection"]["skill_name"] = "other-skill"
            tampered["prepared_digest"] = sha(
                {
                    key: value
                    for key, value in tampered.items()
                    if key != "prepared_digest"
                }
            )
            prepared_path.write_bytes(canonical(tampered) + b"\n")
            response = self.call(
                vendor,
                "run",
                "--trial",
                trial_path,
                "--prepared",
                prepared_path,
                "--output",
                trial["raw"],
                check=False,
            )
            self.assertEqual(response["error"]["code"], "prepared-drift")

            prepared_path.write_bytes(canonical(record) + b"\n")
            (Path(trial["candidate_root"]) / "extra.txt").write_text("drift\n")
            response = self.call(
                vendor,
                "run",
                "--trial",
                trial_path,
                "--prepared",
                prepared_path,
                "--output",
                trial["raw"],
                check=False,
            )
            self.assertEqual(response["error"]["code"], "candidate-drift")

    def test_timeout_cancels_native_process_without_raw_output(self):
        for vendor in ("copilot", "claude", "codex"):
            trial, _, _, response = self.prepare_and_run(
                vendor, prompt="TIMEOUT", adapter_timeout=30
            )
            diagnostic = self.write_timeout_diagnostic(
                vendor, trial, response, "adapter-cancelled"
            )
            self.assertEqual(response["error"]["code"], "executor-timeout")
            self.assertFalse(Path(trial["raw"]).exists())
            self.assertTrue(diagnostic["pid_file_exists"])
            pid = int(diagnostic["pid"])
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

        vendor = "copilot"
        shutil.rmtree(self.root / "copilot-candidate-timeout")
        trial, trial_path = self.trial(vendor, prompt="TIMEOUT")
        prepared = self.call(
            vendor,
            "prepare",
            "--trial",
            trial_path,
            adapter_timeout=180,
        )
        record = {
            "schema_version": 1,
            "trial_id": trial["trial_id"],
            "adapter_prepared": prepared["prepared"],
            "execution": prepared["execution"],
        }
        record["prepared_digest"] = sha(record)
        prepared_path = trial_path.parent / "prepared.json"
        prepared_path.write_bytes(canonical(record) + b"\n")
        process = subprocess.Popen(
            [
                *self.base(vendor, 180),
                "run",
                "--trial",
                str(trial_path),
                "--prepared",
                str(prepared_path),
                "--output",
                trial["raw"],
            ],
            env={
                **os.environ,
                "DREAMING_EXECUTOR_TEST_ALLOW_ROOT": str(self.root),
                "GH_TOKEN": "fixture-token",
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        marker = Path(trial["workspace"]) / "native.pid"
        launch_started = time.monotonic()
        deadline = time.monotonic() + 120
        while not marker.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.05)
        before_cancel = {
            "adapter_pid": process.pid,
            "elapsed_seconds": time.monotonic() - launch_started,
            "workspace_exists": Path(trial["workspace"]).is_dir(),
            "pid_file_exists": marker.is_file(),
            "raw_output_exists": Path(trial["raw"]).exists(),
        }
        self.assertTrue(
            marker.exists(),
            "native process did not publish its PID before explicit cancellation: "
            + json.dumps(before_cancel, sort_keys=True),
        )
        native_pid = int(marker.read_text())
        os.kill(process.pid, 15)
        stdout, stderr = process.communicate(timeout=10)
        after_cancel = {
            **before_cancel,
            "native_pid": native_pid,
            "cancel_elapsed_seconds": time.monotonic() - launch_started,
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "pid_file_exists_after_cancel": marker.is_file(),
            "raw_output_exists_after_cancel": Path(trial["raw"]).exists(),
        }
        (self.root / "copilot-explicit-cancel-timeout-diagnostic.json").write_text(
            json.dumps(after_cancel, sort_keys=True, indent=2) + "\n"
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(
            Path(trial["raw"]).exists(),
            "explicit cancellation published raw output: "
            + json.dumps(after_cancel, sort_keys=True),
        )
        with self.assertRaises(ProcessLookupError):
            os.kill(native_pid, 0)

    def test_output_and_token_limits_fail_closed(self):
        for vendor in ("copilot", "claude", "codex"):
            trial, _, _, response = self.prepare_and_run(
                vendor, prompt="TOKENOVER"
            )
            self.assertEqual(response["error"]["code"], "token-limit-exceeded")
            self.assertFalse(Path(trial["raw"]).exists())
            trial, _, _, response = self.prepare_and_run(vendor, prompt="FLOOD")
            self.assertEqual(response["error"]["code"], "executor-output-limit")
            self.assertFalse(Path(trial["raw"]).exists())

    def test_unknown_native_schema_cannot_hide_activation(self):
        for vendor in ("copilot", "claude", "codex"):
            trial, _, _, response = self.prepare_and_run(vendor, prompt="SCHEMA")
            self.assertEqual(response["error"]["code"], "unsupported-native-schema")
            self.assertFalse(Path(trial["raw"]).exists())

    def test_raw_and_artifact_destination_symlinks_fail_closed(self):
        vendor = "copilot"
        trial, trial_path = self.trial(vendor)
        prepared = self.call(vendor, "prepare", "--trial", trial_path)
        record = {
            "schema_version": 1,
            "trial_id": trial["trial_id"],
            "adapter_prepared": prepared["prepared"],
            "execution": prepared["execution"],
        }
        record["prepared_digest"] = sha(record)
        prepared_path = trial_path.parent / "prepared.json"
        prepared_path.write_bytes(canonical(record) + b"\n")
        target = self.root / "raw-target"
        target.write_text("safe\n")
        Path(trial["raw"]).symlink_to(target)
        response = self.call(
            vendor,
            "run",
            "--trial",
            trial_path,
            "--prepared",
            prepared_path,
            "--output",
            trial["raw"],
            check=False,
        )
        self.assertIn(
            response["error"]["code"],
            {"trial-path-escaped", "raw-output-exists"},
        )
        self.assertEqual(target.read_text(), "safe\n")

        Path(trial["raw"]).unlink()
        self.call(
            vendor,
            "run",
            "--trial",
            trial_path,
            "--prepared",
            prepared_path,
            "--output",
            trial["raw"],
        )
        artifact_target = self.root / "artifact-target"
        artifact_target.write_text("safe\n")
        destination = Path(trial["artifacts"]) / "out.txt"
        destination.symlink_to(artifact_target)
        response = self.call(
            vendor,
            "collect",
            "--trial",
            trial_path,
            "--artifacts",
            trial["artifacts"],
            check=False,
        )
        self.assertEqual(response["error"]["code"], "artifact-destination-exists")
        self.assertEqual(artifact_target.read_text(), "safe\n")


class CopilotNativeEventSchemaGuardTest(unittest.TestCase):
    observed_types = {
        "model.call_finished",
        "model.captured_assignment_context",
        "model.message",
        "model.messages_snapshot",
        "model.model_call_started",
        "model.model_call_success",
        "model.response",
        "model.tool_execution",
        "model.turn_ended",
        "model.turn_started",
        "session.mcp_server_removed",
        "session.mcp_server_status_changed",
        "session.mcp_servers_loaded",
    }

    def call_start(self, turn, requests=None):
        return {
            "type": "model.call_start",
            "data": {
                "model": "fixture-model",
                "turn": turn,
                **({"toolRequests": requests} if requests is not None else {}),
            },
        }

    def call_success(self, event_id, prompt, completion, response_usage=None):
        usage = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }
        data = {"responseChunk": {"usage": usage}}
        if response_usage is not None:
            data["responseUsage"] = response_usage
        return {
            "id": event_id,
            "type": "model.model_call_success",
            "data": data,
        }

    def nested_snapshot(self, skill):
        return {
            "type": "model.messages_snapshot",
            "data": {
                "messages": [
                    {
                        "type": "assistant.message",
                        "content": "nested snapshot answer",
                        "outputTokens": 999999,
                    }
                ],
                "decoys": [
                    {
                        "type": "model.call_start",
                        "data": {
                            "model": "nested-model",
                            "toolRequests": [{"toolCallId": "nested-tool"}],
                        },
                    },
                    {
                        "type": "session.skills_loaded",
                        "data": {
                            "skills": [
                                {
                                    "name": "nested-skill",
                                    "path": str(skill),
                                    "enabled": True,
                                }
                            ]
                        },
                    },
                    {
                        "type": "assistant.message",
                        "data": {
                            "content": "nested assistant",
                            "toolRequests": [
                                {
                                    "name": "skill",
                                    "toolCallId": "nested-skill-call",
                                    "arguments": {"skill": "nested-skill"},
                                }
                            ],
                        },
                    },
                    {
                        "type": "tool.execution_start",
                        "data": {"toolCallId": "nested-tool"},
                    },
                    {
                        "type": "tool.execution_complete",
                        "data": {
                            "toolCallId": "nested-skill-call",
                            "success": True,
                            "result": {
                                "content": 'Skill "nested-skill" loaded successfully.'
                            },
                        },
                    },
                    {
                        "type": "skill.invoked",
                        "data": {
                            "skillName": "nested-skill",
                            "resolvedPath": str(skill),
                        },
                    },
                    {
                        "type": "assistant.turn_end",
                        "data": {"content": "nested final"},
                    },
                    {
                        "type": "session.task_complete",
                        "data": {"summary": "nested summary"},
                    },
                    {
                        "type": "session.error",
                        "data": {"message": "nested failure"},
                    },
                ],
            },
        }

    def managed_settings(self, skill):
        return {
            "id": "managed-settings-0",
            "timestamp": "2026-01-01T00:00:00Z",
            "parentId": None,
            "type": "session.managed_settings_resolved",
            "data": {
                "bypassPermissionsDisabled": False,
                "clientManaged": True,
                "deviceManaged": False,
                "failClosed": True,
                "managedKeys": ["permissions", "sandbox"],
                "permissionsAllowIntersected": True,
                "sandboxEnabledByUndeterminedPolicy": False,
                "serverManaged": True,
                "source": "mixed",
                "settings": {
                    "usage": {
                        "prompt_tokens": 999999,
                        "completion_tokens": 999999,
                        "total_tokens": 1999998,
                    },
                    "outputTokens": 999999,
                    "toolRequests": [{"toolCallId": "nested-managed-tool"}],
                    "snapshot": self.nested_snapshot(skill),
                    "events": [
                        self.call_success("nested-managed-usage", 999999, 999999),
                    ],
                },
            },
        }

    def assert_usage_refusal(self, values, reason):
        with self.assertRaises(adapter_module.AdapterError) as raised:
            adapter_module.copilot_usage(values)
        self.assertEqual(raised.exception.code, "usage-unproved")
        self.assertEqual(raised.exception.message, reason)

    def test_guard_a_exact_vocabulary_and_unknown_refusal(self):
        expected = {
            "abort",
            "assistant.idle",
            "assistant.message",
            "assistant.message_delta",
            "assistant.message_start",
            "assistant.reasoning",
            "assistant.tool_call_delta",
            "assistant.turn_end",
            "assistant.turn_start",
            "external_tool.completed",
            "external_tool.requested",
            "hook.end",
            "hook.start",
            "model.call_start",
            "permission.completed",
            "permission.requested",
            "result",
            "session.background_tasks_changed",
            "session.binary_asset",
            "session.canvas.recorded",
            "session.compaction_complete",
            "session.compaction_start",
            "session.context_changed",
            "session.custom_agents_updated",
            "session.error",
            "session.info",
            "session.mode_changed",
            "session.model_change",
            "session.permissions_changed",
            "session.plan_changed",
            "session.remote_steerable_changed",
            "session.resume",
            "session.shutdown",
            "session.start",
            "session.task_complete",
            "session.truncation",
            "session.usage_checkpoint",
            "session.warning",
            "session.autopilot_objective_changed",
            "session.workspace_file_changed",
            "session.schedule_created",
            "session.schedule_cancelled",
            "session.skills_loaded",
            "session.tools_updated",
            "skill.invoked",
            "subagent.completed",
            "subagent.failed",
            "subagent.selected",
            "subagent.started",
            "system.message",
            "system.notification",
            "tool.execution_complete",
            "tool.execution_partial_result",
            "tool.execution_start",
            "tool.user_requested",
            "user.message",
        } | self.observed_types
        for event_type in sorted(self.observed_types):
            with self.subTest(event_type=event_type):
                adapter_module.validate_native_schema(
                    "copilot", [{"type": event_type}]
                )
        adapter_module.validate_native_schema(
            "copilot",
            [{"events": [{"type": event_type} for event_type in sorted(self.observed_types)]}],
        )
        self.assertEqual(adapter_module.COPILOT_EVENT_TYPES, expected)
        self.assertEqual(len(adapter_module.COPILOT_EVENT_TYPES), 69)
        self.assertEqual(
            adapter_module.SUPPORTED_SOURCE_VERSIONS,
            {"copilot": 1, "claude": 1, "codex": 1},
        )
        for unknown in (
            "unknown-native-schema-sentinel",
            "model.model_call_success.future",
        ):
            with self.subTest(unknown=unknown):
                with self.assertRaises(adapter_module.AdapterError) as raised:
                    adapter_module.validate_native_schema(
                        "copilot", [{"type": unknown}]
                    )
                self.assertEqual(raised.exception.code, "unsupported-native-schema")
                self.assertEqual(raised.exception.message, f"copilot:{unknown}")

    def test_guard_a_malformed_envelopes_still_refuse(self):
        for values, message in (
            ([{"events": 3}], "copilot:events"),
            ([{"events": [1]}], "copilot:events"),
            ([{"events": [{"type": "unknown-native-schema-sentinel"}]}],
             "copilot:unknown-native-schema-sentinel"),
        ):
            with self.subTest(values=values):
                with self.assertRaises(adapter_module.AdapterError) as raised:
                    adapter_module.validate_native_schema("copilot", values)
                self.assertEqual(raised.exception.code, "unsupported-native-schema")
                self.assertEqual(raised.exception.message, message)

    def test_guard_b_outer_iterator_is_direct_and_one_level_only(self):
        direct = {"type": "session.start", "data": {"model": "fixture-model"}}
        wrapped_first = {"type": "model.call_start", "data": {"model": "fixture-model"}}
        wrapped_second = {"type": "assistant.message", "data": {"content": "outer"}}
        nested = {
            "type": "assistant.message",
            "data": {
                "content": "outer answer",
                "snapshot": {
                    "type": "model.call_start",
                    "data": {
                        "model": "nested-model",
                        "toolRequests": [{"toolCallId": "nested-tool"}],
                    },
                    "usage": {"total_tokens": 999999},
                },
            },
        }
        values = [direct, {"events": [wrapped_first, wrapped_second]}, nested]
        self.assertEqual(
            list(adapter_module.copilot_outer_events(values)),
            [direct, wrapped_first, wrapped_second, nested],
        )

    def test_guard_b_non_usage_readers_preserve_declared_outer_paths(self):
        case = work / self._testMethodName
        case.mkdir()
        skill = case / "fixture-skill" / "SKILL.md"
        skill.parent.mkdir()
        skill.write_text("fixture\n")
        values = [
            {"type": "session.start", "data": {"model": "fixture-model"}},
            self.call_start(0),
            {
                "type": "session.skills_loaded",
                "data": {
                    "skills": [
                        {"name": "fixture-skill", "path": str(skill), "enabled": True}
                    ]
                },
            },
            {
                "type": "assistant.message",
                "data": {
                    "content": "outer answer",
                    "toolRequests": [
                        {
                            "name": "skill",
                            "toolCallId": "outer-call",
                            "arguments": {"skill": "fixture-skill"},
                        }
                    ],
                },
            },
            self.nested_snapshot(skill),
            {
                "type": "tool.execution_complete",
                "data": {
                    "toolCallId": "outer-call",
                    "success": True,
                    "result": {
                        "content": 'Skill "fixture-skill" loaded successfully.'
                    },
                },
            },
            {"type": "assistant.turn_end", "data": {"content": "outer final"}},
            {
                "type": "session.error",
                "data": {
                    "message": "outer failure",
                    "snapshot": {
                        "type": "session.error",
                        "data": {"message": "nested failure"},
                    },
                },
            },
            self.call_success("outer-usage", 10, 5),
        ]
        self.assertEqual(adapter_module.native_model("copilot", values), "fixture-model")
        self.assertEqual(
            adapter_module.native_detailed_usage("copilot", values),
            {
                "turns": 1,
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "tool_calls": 1,
            },
        )
        self.assertEqual(
            adapter_module.native_failure_message(
                "copilot", "\n".join(json.dumps(value) for value in values), ""
            ),
            "outer failure",
        )
        self.assertEqual(
            adapter_module.normalized_native_events("copilot", {"events": values}),
            [
                ("assistant_message", "outer answer", {"native_type": "assistant.message"}),
                ("tool_result", 'Skill "fixture-skill" loaded successfully.', {"native_type": "tool.execution_complete"}),
                ("final_answer", "outer final", {"native_type": "assistant.turn_end"}),
            ],
        )
        evidence = adapter_module.native_skill_evidence(
            "copilot", values, {"fixture-skill": skill}, case
        )
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["projected_name"], "fixture-skill")
        comparator_values = [
            {"type": "session.start", "data": {"model": "fixture-model"}},
            self.call_start(0),
            self.nested_snapshot(skill),
            self.call_success("comparator-usage", 10, 5),
        ]
        comparator_events = list(adapter_module.copilot_outer_events(comparator_values))
        comparator_event_types = [
            item["type"]
            for item in comparator_events
            if isinstance(item.get("type"), str)
        ]
        comparator_tool_event = any(
            item_type.startswith(
                (
                    "external_tool.",
                    "permission.",
                    "skill.",
                    "subagent.",
                    "tool.",
                )
            )
            or item_type == "assistant.tool_call_delta"
            for item_type in comparator_event_types
        )
        comparator_usage = adapter_module.native_detailed_usage(
            "copilot", comparator_values
        )
        self.assertEqual(comparator_event_types.count("model.call_start"), 1)
        self.assertFalse(comparator_tool_event)
        self.assertEqual(comparator_usage["tool_calls"], 0)
        self.assertTrue(
            comparator_event_types.count("model.call_start") == 1
            and comparator_usage["tool_calls"] == 0
            and not comparator_tool_event
        )

    def test_guard_h_managed_settings_is_admitted_and_semantically_inert(self):
        case = work / self._testMethodName
        case.mkdir()
        skill = case / "fixture-skill" / "SKILL.md"
        skill.parent.mkdir()
        skill.write_text("fixture\n")
        nested_skill = case / "nested-skill" / "SKILL.md"
        nested_skill.parent.mkdir()
        nested_skill.write_text("nested fixture\n")
        baseline = [
            {"type": "session.start", "data": {"model": "fixture-model"}},
            self.call_start(0, [{"toolCallId": "outer-call"}]),
            {
                "type": "session.skills_loaded",
                "data": {
                    "skills": [
                        {
                            "name": "fixture-skill",
                            "path": str(skill),
                            "enabled": True,
                        }
                    ]
                },
            },
            {
                "type": "assistant.message",
                "data": {
                    "content": "outer answer",
                    "toolRequests": [
                        {
                            "name": "skill",
                            "toolCallId": "outer-call",
                            "arguments": {"skill": "fixture-skill"},
                        }
                    ],
                },
            },
            {
                "type": "tool.execution_complete",
                "data": {
                    "toolCallId": "outer-call",
                    "success": True,
                    "result": {
                        "content": 'Skill "fixture-skill" loaded successfully.'
                    },
                },
            },
            {"type": "assistant.turn_end", "data": {"content": "outer final"}},
            {"type": "session.error", "data": {"message": "outer failure"}},
            self.call_success("outer-usage", 10, 5),
        ]
        managed = self.managed_settings(nested_skill)
        candidate = [*baseline, managed]
        skill_files = {
            "fixture-skill": skill,
            "nested-skill": nested_skill,
        }

        def comparator_gate(values):
            events = list(adapter_module.copilot_outer_events(values))
            event_types = [
                item["type"]
                for item in events
                if isinstance(item.get("type"), str)
            ]
            tool_event = any(
                item_type.startswith(
                    (
                        "external_tool.",
                        "permission.",
                        "skill.",
                        "subagent.",
                        "tool.",
                    )
                )
                or item_type == "assistant.tool_call_delta"
                for item_type in event_types
            )
            usage = adapter_module.native_detailed_usage("copilot", values)
            return (
                event_types.count("model.call_start"),
                usage["tool_calls"],
                tool_event,
            )

        self.assertEqual(
            adapter_module.native_model("copilot", candidate),
            adapter_module.native_model("copilot", baseline),
        )
        self.assertEqual(
            adapter_module.native_token_usage("copilot", candidate),
            adapter_module.native_token_usage("copilot", baseline),
        )
        self.assertEqual(
            adapter_module.native_detailed_usage("copilot", candidate),
            adapter_module.native_detailed_usage("copilot", baseline),
        )
        self.assertEqual(
            adapter_module.native_skill_evidence(
                "copilot", candidate, skill_files, case
            ),
            adapter_module.native_skill_evidence(
                "copilot", baseline, skill_files, case
            ),
        )
        self.assertEqual(
            adapter_module.normalized_native_events(
                "copilot", {"events": candidate}
            ),
            adapter_module.normalized_native_events(
                "copilot", {"events": baseline}
            ),
        )
        self.assertEqual(
            adapter_module.native_failure_message(
                "copilot",
                "\n".join(json.dumps(value) for value in candidate),
                "",
            ),
            adapter_module.native_failure_message(
                "copilot",
                "\n".join(json.dumps(value) for value in baseline),
                "",
            ),
        )
        self.assertEqual(comparator_gate(candidate), comparator_gate(baseline))

        for event_type in sorted(self.observed_types):
            adapter_module.validate_native_schema(
                "copilot", [{"type": event_type}]
            )
        with self.assertRaises(adapter_module.AdapterError) as raised:
            adapter_module.validate_native_schema(
                "copilot", [{"type": "unknown-native-schema-sentinel"}]
            )
        self.assertEqual(raised.exception.code, "unsupported-native-schema")
        adapter_module.validate_native_schema("copilot", [managed])

    def test_guard_c_shape_b_sums_deduplicates_and_ignores_order(self):
        calls = [
            (8390, 716),
            (9156, 192),
            (9376, 113),
            (9518, 6),
        ]
        starts = [
            self.call_start(
                turn,
                [{"toolCallId": f"tool-{turn}"}] if turn < 3 else [],
            )
            for turn in range(4)
        ]
        successes = [
            self.call_success(f"usage-{turn}", prompt, completion)
            for turn, (prompt, completion) in enumerate(calls)
        ]
        metadata = {
            "type": "model.messages_snapshot",
            "data": {
                "messages": [
                    {
                        "type": "assistant.message",
                        "outputTokens": 999999,
                        "usage": {"total_tokens": 999999},
                    }
                ]
            },
        }
        expected = {
            "turns": 4,
            "input_tokens": 36440,
            "output_tokens": 1027,
            "total_tokens": 37467,
            "tool_calls": 3,
        }
        values = [*starts, metadata, *successes]
        self.assertEqual(adapter_module.copilot_usage(values), expected)
        self.assertEqual(
            adapter_module.copilot_usage([*reversed(successes), metadata, *starts]),
            expected,
        )
        self.assertEqual(
            adapter_module.copilot_usage([*starts, metadata, *successes, *successes]),
            expected,
        )
        self.assertEqual(adapter_module.native_token_usage("copilot", values), 37467)
        self.assertEqual(adapter_module.native_detailed_usage("copilot", values), expected)

    def test_guard_d_shape_b_refuses_malformed_ambiguous_and_incomplete_usage(self):
        valid = self.call_success("usage-0", 10, 5)
        malformed_cases = []
        for label, mutation in (
            ("missing", lambda usage: usage.pop("completion_tokens")),
            ("bool", lambda usage: usage.__setitem__("prompt_tokens", True)),
            ("float", lambda usage: usage.__setitem__("prompt_tokens", 10.0)),
            ("negative", lambda usage: usage.__setitem__("prompt_tokens", -1)),
            ("broken-sum", lambda usage: usage.__setitem__("total_tokens", 16)),
        ):
            item = json.loads(json.dumps(valid))
            mutation(item["data"]["responseChunk"]["usage"])
            malformed_cases.append((label, [self.call_start(0), item], "copilot:usage-malformed"))
        missing_id = json.loads(json.dumps(valid))
        missing_id.pop("id")
        empty_id = json.loads(json.dumps(valid))
        empty_id["id"] = ""
        collision = json.loads(json.dumps(valid))
        collision["data"]["responseChunk"]["usage"]["completion_tokens"] = 6
        collision["data"]["responseChunk"]["usage"]["total_tokens"] = 16
        contradictory = json.loads(json.dumps(valid))
        contradictory["data"]["responseUsage"] = {
            "prompt_tokens": 10,
            "completion_tokens": 6,
            "total_tokens": 16,
        }
        cases = [
            *malformed_cases,
            ("missing-id", [self.call_start(0), missing_id], "copilot:usage-ambiguous"),
            ("empty-id", [self.call_start(0), empty_id], "copilot:usage-ambiguous"),
            ("colliding-id", [self.call_start(0), valid, collision], "copilot:usage-ambiguous"),
            ("incomplete", [self.call_start(0), self.call_start(1), valid], "copilot:usage-incomplete"),
            ("response-contradiction", [self.call_start(0), contradictory], "copilot:usage-contradiction"),
        ]
        for label, values, reason in cases:
            with self.subTest(label=label):
                self.assert_usage_refusal(values, reason)

    def test_guard_d_response_usage_cross_check_always_refuses_contradiction(self):
        valid = self.call_success("usage-0", 10, 5)
        expected = {
            "turns": 1,
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "tool_calls": 0,
        }
        self.assertEqual(
            adapter_module.copilot_usage([self.call_start(0), valid]), expected
        )
        cases = (
            ("missing-prompt", {"completion_tokens": 5, "total_tokens": 15}),
            ("missing-completion", {"prompt_tokens": 10, "total_tokens": 15}),
            ("missing-total", {"prompt_tokens": 10, "completion_tokens": 5}),
            ("bool-prompt", {"prompt_tokens": True, "completion_tokens": 5, "total_tokens": 6}),
            ("bool-completion", {"prompt_tokens": 10, "completion_tokens": True, "total_tokens": 11}),
            ("bool-total", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": True}),
            ("negative-prompt", {"prompt_tokens": -1, "completion_tokens": 5, "total_tokens": 4}),
            ("negative-completion", {"prompt_tokens": 10, "completion_tokens": -1, "total_tokens": 9}),
            ("negative-total", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": -1}),
            ("broken-sum", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 16}),
            ("different-prompt", {"prompt_tokens": 9, "completion_tokens": 6, "total_tokens": 15}),
            ("different-completion", {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}),
            ("different-total", {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16}),
            ("wrong-shape-none", None),
            ("wrong-shape-list", []),
        )
        for label, response_usage in cases:
            with self.subTest(label=label):
                item = json.loads(json.dumps(valid))
                item["data"]["responseUsage"] = response_usage
                self.assert_usage_refusal(
                    [self.call_start(0), item], "copilot:usage-contradiction"
                )

    def test_guard_d_real_result_usage_is_not_a_token_authority(self):
        result_usage = {
            "type": "result",
            "usage": {
                "codeChanges": 1,
                "premiumRequests": 2,
                "sessionDurationMs": 3,
                "totalApiDurationMs": 4,
            },
        }
        values = [
            self.call_start(0),
            self.call_success("usage-0", 10, 5),
        ]
        self.assertEqual(
            adapter_module.copilot_usage([*values, result_usage]),
            adapter_module.copilot_usage(values),
        )
        self.assertIsNone(adapter_module.copilot_usage([result_usage]))

    def test_guard_e_legacy_shape_a_migrates_without_losing_compatibility(self):
        candidate = [
            self.call_start(0, [{"toolCallId": "current-skill-call"}]),
            {
                "type": "assistant.message",
                "data": {
                    "outputTokens": 15,
                    "usage": {"input_tokens": 10, "output_tokens": 15},
                    "toolRequests": [{"toolCallId": "current-skill-call"}],
                },
            },
            {"type": "assistant.message", "data": {"outputTokens": 0, "toolRequests": []}},
        ]
        no_candidate = [
            self.call_start(0),
            {
                "type": "assistant.message",
                "data": {
                    "outputTokens": 15,
                    "usage": {"input_tokens": 10, "output_tokens": 15},
                    "toolRequests": [],
                },
            },
            {"type": "assistant.message", "data": {"outputTokens": 0, "toolRequests": []}},
        ]
        comparator = [
            self.call_start(0),
            {"type": "assistant.message", "data": {"outputTokens": 5}},
            {"type": "session.usage_checkpoint", "usage": {"total_tokens": 15}},
        ]
        run = [
            {"type": "session.start", "data": {"model": "fixture-model"}},
            {"type": "session.usage_checkpoint", "usage": {"total_tokens": 15}},
        ]
        token_over = [
            {"type": "session.start", "data": {"model": "fixture-model"}},
            {"type": "session.usage_checkpoint", "usage": {"total_tokens": 1000}},
        ]
        cases = (
            ("candidate", candidate, 25, {"turns": 1, "input_tokens": 10, "output_tokens": 15, "total_tokens": 25, "tool_calls": 1}),
            ("no-candidate", no_candidate, 25, {"turns": 1, "input_tokens": 10, "output_tokens": 15, "total_tokens": 25, "tool_calls": 0}),
            ("comparator", comparator, 15, {"turns": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "tool_calls": 0}),
            ("run", run, 15, None),
            ("token-over", token_over, 1000, None),
        )
        for label, values, expected_token, expected_detailed in cases:
            with self.subTest(label=label, reader="token"):
                self.assertEqual(
                    adapter_module.native_token_usage("copilot", values), expected_token
                )
            with self.subTest(label=label, reader="detailed"):
                self.assertEqual(
                    adapter_module.native_detailed_usage("copilot", values),
                    expected_detailed,
                )

    def test_guard_f_copilot_readers_share_one_usage_authority(self):
        values = [self.call_start(0), self.call_success("usage-0", 8349, 103)]
        authority = adapter_module.copilot_usage(values)
        detailed = adapter_module.native_detailed_usage("copilot", values)
        self.assertEqual(authority, detailed)
        self.assertEqual(adapter_module.native_token_usage("copilot", values), 8452)
        self.assertEqual(detailed["total_tokens"], 8452)

    def test_guard_g_completeness_and_shape_coexistence_are_fail_closed(self):
        shape_b = self.call_success("usage-0", 10, 15)
        equal_shape_a = {
            "type": "assistant.message",
            "data": {"usage": {"input_tokens": 10, "output_tokens": 15}},
        }
        unequal_components = {
            "type": "assistant.message",
            "data": {"usage": {"input_tokens": 15, "output_tokens": 10}},
        }
        total_only = {
            "type": "session.usage_checkpoint",
            "usage": {"total_tokens": 25},
        }
        pair_conflict = {
            "type": "assistant.message",
            "data": {"usage": {"input_tokens": 9, "output_tokens": 16}},
        }
        total_conflict = {
            "type": "session.usage_checkpoint",
            "usage": {"total_tokens": 26},
        }
        bare_output = {
            "type": "assistant.message",
            "data": {"outputTokens": 15},
        }
        expected = {
            "turns": 1,
            "input_tokens": 10,
            "output_tokens": 15,
            "total_tokens": 25,
            "tool_calls": 0,
        }
        self.assertEqual(
            adapter_module.copilot_usage([self.call_start(0), equal_shape_a, shape_b]),
            expected,
        )
        self.assertEqual(
            adapter_module.copilot_usage([self.call_start(0), total_only, shape_b]),
            expected,
        )
        for label, values, reason in (
            ("started-without-success", [self.call_start(0)], "copilot:usage-incomplete"),
            ("unequal-components", [self.call_start(0), unequal_components, shape_b], "copilot:usage-contradiction"),
            ("total-only-conflict", [self.call_start(0), total_conflict, shape_b], "copilot:usage-contradiction"),
            ("pair-conflict", [self.call_start(0), equal_shape_a, pair_conflict], "copilot:usage-ambiguous"),
            ("mixed-shape-conflict", [self.call_start(0), equal_shape_a, total_conflict], "copilot:usage-contradiction"),
            ("bare-output", [bare_output], "copilot:usage-incomplete"),
        ):
            with self.subTest(label=label):
                self.assert_usage_refusal(values, reason)


unittest.main(verbosity=2)
PY
