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
    print("--plugin-dir --output-format --model --json --ignore-user-config")
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
prompt = next((value for value in args if value in {
    "fixture prompt", "DRIFT", "NOLOAD", "WRONGLOAD", "FALSETRIGGER", "TIMEOUT",
    "TOKENOVER", "FLOOD", "SCHEMA", "NOPATH", "NATIVEFAIL", "CURRENTCOPILOT"
}), "")
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
    candidate = bool(matches)
    loaded_path = str(matches[0]) if matches else None
elif vendor == "codex":
    candidate = (Path(os.environ["CODEX_HOME"]) / "candidate-installed").is_file()
    root_file = Path(os.environ["CODEX_HOME"]) / "candidate-root"
    if root_file.is_file():
        loaded_path = str(
            Path(root_file.read_text()) / "skills/fixture-skill/SKILL.md"
        )
candidate = candidate and prompt != "NOLOAD"
loaded_name = "other-skill" if prompt == "WRONGLOAD" else "fixture-skill"
if vendor == "copilot":
    if prompt == "CURRENTCOPILOT":
        events = [
            {"type":"session.skills_loaded","data":{"skills":[
                {"name":loaded_name,"path":loaded_path,"enabled":True}
            ] if candidate else []}},
            {"type":"session.tools_updated","data":{"model":observed}},
            {"type":"model.call_start","data":{"model":observed}},
            {"type":"assistant.message","data":{
                "content":"",
                "outputTokens":tokens,
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
                  {"type":"assistant.message","data":{"content":"answer"}},
                  {"type":"session.usage_checkpoint",
                   "usage":{"total_tokens":tokens}}]
    if candidate and prompt != "CURRENTCOPILOT":
        events.append({"type":"skill.invoked","data":{
            "skillName":loaded_name,"resolvedPath":loaded_path}})
    if prompt == "CURRENTCOPILOT":
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
        events.append({"type":"session.task_complete","data":{"summary":"answer"}})
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
                      "usage":{"total_tokens":tokens}}))
else:
    print(json.dumps({"type":"turn_context","payload":{"model":observed}}))
    print(json.dumps({"type":"turn.completed",
                      "usage":{"total_tokens":tokens}}))
    if candidate:
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

    def base(self, vendor, timeout=10):
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
            "100",
            "--output-bytes",
            "100000",
        ]

    def call(self, vendor, *args, check=True, adapter_timeout=120, cwd=None):
        started = time.monotonic()
        result = subprocess.run(
            [*self.base(vendor, adapter_timeout), *map(str, args)],
            env={
                **os.environ,
                "DREAMING_EXECUTOR_TEST_ALLOW_ROOT": str(self.root),
                "GH_TOKEN": "fixture-token",
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
        )
        self.last_call = {
            "argv": [*self.base(vendor, adapter_timeout), *map(str, args)],
            "duration_seconds": time.monotonic() - started,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        if check and result.returncode:
            self.fail(result.stdout + result.stderr)
        return json.loads(result.stdout.splitlines()[-1])

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
            [15],
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


unittest.main(verbosity=2)
PY
