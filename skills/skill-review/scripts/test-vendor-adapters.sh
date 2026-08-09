#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
TEST_ROOT="$ROOT/.test-work"
mkdir -p "$TEST_ROOT"
# shellcheck source=lib-test-work.sh
source "$ROOT/skills/skill-review/scripts/lib-test-work.sh"
prune_test_work "$TEST_ROOT" "vendor-adapters" 2
TMP="$(mktemp -d "$TEST_ROOT/vendor-adapters.XXXXXX")"
cleanup() {
  local status=$?
  trap - EXIT
  finish_test_work "$status" "$TMP" "native-adapter" 1
  exit "$status"
}
trap cleanup EXIT

python3 - "$ROOT" "$TMP" <<'PY'
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import unittest
from itertools import combinations
from pathlib import Path
from unittest import mock

root = Path(sys.argv[1])
temp = Path(sys.argv[2])
sys.argv = [sys.argv[0]]
adapter = root / "skills/skill-review/scripts/dreaming-vendor-adapter.py"
configure = root / "scripts/configure-adapters.py"
vendor_spec = importlib.util.spec_from_file_location("dreaming_vendor_adapter", adapter)
vendor_module = importlib.util.module_from_spec(vendor_spec)
sys.modules[vendor_spec.name] = vendor_module
vendor_spec.loader.exec_module(vendor_module)
core_path = root / "skills/skill-review/scripts/dreaming-core.py"
spec = importlib.util.spec_from_file_location("dreaming_core", core_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


class VendorAdapterTest(unittest.TestCase):
    def setUp(self):
        self.case = temp / self._testMethodName
        self.case.mkdir()
        self.env = {
            **os.environ,
            "DREAMING_ADAPTER_ALLOWED_ROOT": str(self.case),
            "DREAMING_CODEX_ROLLOUT_ROOT": str(self.case / "codex"),
            "DREAMING_EXECUTOR_TEST_ALLOW_ROOT": str(self.case),
            "DREAMING_REPO_ROOT": str(root),
            "DREAMING_SKILLS_ROOT": str(self.case / "skills"),
            "DREAMING_STATE_DIR": str(self.case / "state"),
            "FAKE_CLI_LOG": str(self.case / "cli-invocations.jsonl"),
        }
        (self.case / "skills").mkdir()
        self._write_sources()
        self._write_fake_clis()

    def run_adapter(
        self,
        vendor,
        role,
        command,
        *arguments,
        check=True,
        environment=None,
    ):
        source_root = {
            "copilot": self.case / "copilot",
            "claude": self.case / "claude",
            "codex": self.case / "codex",
        }[vendor]
        invocation = [
            sys.executable,
            str(adapter),
            "--vendor",
            vendor,
            "--role",
            role,
            "--source-root",
            str(source_root),
            "--quiet-seconds",
            "0",
            command,
            *map(str, arguments),
        ]
        result = subprocess.run(
            invocation,
            env=environment or self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
        )
        return json.loads(result.stdout.splitlines()[-1])

    def test_copilot_executor_ignores_ambient_copilot_home(self):
        work = self.case / "executor"
        work.mkdir()
        ambient_home = self.case / "ambient-copilot-home"
        with mock.patch.dict(
            os.environ,
            {"COPILOT_HOME": str(ambient_home)},
            clear=False,
        ):
            environment = vendor_module.executor_environment("copilot", work)
        self.assertNotIn("COPILOT_HOME", environment)
        self.assertEqual(environment["HOME"], str(work / "home"))

    def _write_sources(self):
        copilot = self.case / "copilot/session"
        copilot.mkdir(parents=True)
        copilot_events = [
            {
                "type": "session.start",
                "data": {"cwd": "/work/project"},
                "id": "c0",
                "timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "type": "user.message",
                "data": {"content": "make this a reusable procedure"},
                "id": "c1",
                "timestamp": "2026-01-01T00:00:01Z",
            },
            {
                "type": "assistant.message",
                "data": {"content": "done"},
                "id": "c2",
                "timestamp": "2026-01-01T00:00:02Z",
            },
            {
                "type": "tool.execution_start",
                "data": {"toolName": "bash", "arguments": {"command": "true"}},
                "id": "c3",
                "timestamp": "2026-01-01T00:00:03Z",
            },
            {
                "type": "session.shutdown",
                "data": {"shutdownType": "complete"},
                "id": "c4",
                "timestamp": "2026-01-01T00:00:04Z",
            },
        ]
        (copilot / "events.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in copilot_events)
        )

        claude = self.case / "claude/project"
        claude.mkdir(parents=True)
        claude_events = [
            {
                "type": "user",
                "sessionId": "session",
                "uuid": "h1",
                "timestamp": "2026-01-01T00:00:01Z",
                "cwd": "/work/project",
                "message": {
                    "role": "user",
                    "content": "make this a reusable procedure",
                },
            },
            {
                "type": "assistant",
                "sessionId": "session",
                "uuid": "h2",
                "timestamp": "2026-01-01T00:00:02Z",
                "cwd": "/work/project",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "done"},
                        {
                            "type": "tool_use",
                            "id": "tool",
                            "name": "bash",
                            "input": {"command": "true"},
                        },
                    ],
                },
            },
        ]
        (claude / "session.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in claude_events)
        )

        codex = self.case / "codex"
        codex.mkdir()
        rollout = codex / "rollout.jsonl"
        codex_events = [
            {
                "type": "response_item",
                "id": "x1",
                "timestamp": "2026-01-01T00:00:01Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "make this a reusable procedure"}
                    ],
                },
            },
            {
                "type": "response_item",
                "id": "x2",
                "timestamp": "2026-01-01T00:00:02Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                },
            },
            {
                "type": "response_item",
                "id": "x3",
                "timestamp": "2026-01-01T00:00:03Z",
                "payload": {
                    "type": "function_call",
                    "name": "bash",
                    "arguments": {"command": "true"},
                },
            },
            {
                "type": "event_msg",
                "id": "x4",
                "timestamp": "2026-01-01T00:00:04Z",
                "payload": {"type": "task_complete", "last_agent_message": "done"},
            },
        ]
        rollout.write_text("".join(json.dumps(item) + "\n" for item in codex_events))
        database = sqlite3.connect(codex / "state_5.sqlite")
        database.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, "
            "created_at INTEGER, updated_at INTEGER, cwd TEXT, has_user_event INTEGER)"
        )
        database.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, 1)",
            ("session", str(rollout), 1, 4, "/work/project"),
        )
        database.commit()
        database.close()

    def _write_fake_clis(self):
        bin_dir = self.case / "bin"
        bin_dir.mkdir()
        script = bin_dir / "vendor-cli"
        script.write_text(
            """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
vendor = Path(sys.argv[0]).name
args = sys.argv[1:]
with Path(os.environ["FAKE_CLI_LOG"]).open("a") as log:
    log.write(json.dumps({"vendor": vendor, "args": args}) + "\\n")
state_path = Path(os.environ["FAKE_CLI_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {}
if "--version" in args:
    print(vendor + " 1.0")
    raise SystemExit()
if vendor == "claude" and args[:3] == ["auth", "status", "--json"]:
    print(json.dumps({"loggedIn": True}))
    raise SystemExit()
if vendor == "codex" and args[:2] == ["login", "status"]:
    print("Logged in")
    raise SystemExit()
if vendor == "codex" and "--output-last-message" in args:
    target = Path(args[args.index("--output-last-message") + 1])
    prompt = next((arg for arg in args if "result_schema" in arg), "")
    payload = (
        {"decision":"approve","summary":"independent fixture approval"}
        if "draft_review" in prompt
        else {"terminal_route":"discard","summary":"fixture",
            "routing_reason":"no durable procedure","artifact":None,
            "evidence_event_ids":[]}
    )
    target.write_text(json.dumps(payload))
    raise SystemExit()
if ("-p" in args or "--print" in args) and "plugin" not in args:
    prompt = next((arg for arg in args if "result_schema" in arg), "")
    payload = (
        {"decision":"approve","summary":"independent fixture approval"}
        if "draft_review" in prompt
        else {"terminal_route":"discard","summary":"fixture",
            "routing_reason":"no durable procedure","artifact":None,
            "evidence_event_ids":[]}
    )
    print(json.dumps({"result":payload}))
    raise SystemExit()
if "plugin" in args and ("marketplace" in args and "add" in args):
    bundle = args[-1]
    manifest = json.load(open(Path(bundle)/".claude-plugin/marketplace.json"))
    values = state.setdefault(vendor + "_marketplaces", [])
    values[:] = [value for value in values if value["name"] != manifest["name"]]
    values.append({"name": manifest["name"], "bundle": str(Path(bundle).resolve())})
elif "skill" in args and "add" in args:
    fail_bundle = os.environ.get("FAKE_COPILOT_FAIL_ADD_BUNDLE")
    if args[-1] == fail_bundle and not state.get("copilot_fail_add_once"):
        state["copilot_fail_add_once"] = True
        state_path.write_text(json.dumps(state))
        print("fixture Copilot add failure", file=sys.stderr)
        raise SystemExit(1)
    values = state.setdefault("copilot_bundles", [])
    if args[-1] not in values:
        values.append(args[-1])
elif "skill" in args and "list" in args:
    rows = []
    names = set()
    for raw in state.get("copilot_bundles", []):
        for path in Path(raw).iterdir():
            if (path / "SKILL.md").is_file() and path.name not in names:
                rows.append({"name": path.name, "path": str(path)})
                names.add(path.name)
    print(json.dumps(rows))
    raise SystemExit()
elif "plugin" in args and ("install" in args or ("add" in args and "marketplace" not in args)):
    name = next(a.split("@")[0] for a in args if a.startswith("dreaming-learned-"))
    values = state.setdefault(vendor + "_plugins", [])
    marketplace = next(
      value for value in state.get(vendor + "_marketplaces", [])
      if value["name"] == name
    )
    values[:] = [value for value in values if value["name"] != name]
    values.append({"name": name, "bundle": marketplace["bundle"]})
elif "plugin" in args and "marketplace" in args and "list" in args:
    values = state.get(vendor + "_marketplaces", [])
    if vendor == "claude":
        print(json.dumps([
          {
            "name": value["name"],
            "source": "directory",
            "repo": value["bundle"],
            "installLocation": value["bundle"],
          }
          for value in values
        ]))
    else:
        print(json.dumps({"marketplaces": [
          {
            "name": value["name"],
            "marketplaceSource": {
              "sourceType": "local",
              "source": value["bundle"],
            },
            "root": value["bundle"],
          }
          for value in values
        ]}))
    raise SystemExit()
elif "plugin" in args and "list" in args:
    values = state.get(vendor + "_plugins", [])
    if vendor == "claude":
        print(json.dumps([
          {
            "id": value["name"] + "@" + value["name"],
            "installPath": value["bundle"],
            "scope": "user",
            "version": "0.1.0",
            "enabled": True,
          }
          for value in values
        ]))
    else:
        rows = [
          {
            "pluginId": value["name"] + "@" + value["name"],
            "name": value["name"],
            "marketplaceName": value["name"],
            "marketplaceSource": {
              "sourceType": "local",
              "source": value["bundle"],
            },
            "source": {"path": value["bundle"], "source": value["bundle"]},
            "version": "0.1.0",
            "installed": True,
            "enabled": True,
          }
          for value in values
        ]
        print(json.dumps({"installed": rows, "available": rows}))
    raise SystemExit()
elif "skill" in args and "remove" in args:
    state["copilot_bundles"] = [
      value for value in state.get("copilot_bundles", []) if value != args[-1]
    ]
elif "plugin" in args and "marketplace" in args and "remove" in args:
    name = args[-1]
    state[vendor + "_marketplaces"] = [
      value
      for value in state.get(vendor + "_marketplaces", [])
      if value["name"] != name
    ]
elif "plugin" in args and ("uninstall" in args or "remove" in args):
    name = next(a.split("@")[0] for a in args if a.startswith("dreaming-learned-"))
    state[vendor + "_plugins"] = [
      value
      for value in state.get(vendor + "_plugins", [])
      if value["name"] != name
    ]
state_path.write_text(json.dumps(state))
print(json.dumps({"ok": True}))
"""
        )
        script.chmod(0o755)
        for vendor in ("copilot", "claude", "codex"):
            target = bin_dir / vendor
            target.symlink_to(script)
            self.env[f"DREAMING_{vendor.upper()}_BIN"] = str(target)
        self.env["FAKE_CLI_STATE"] = str(self.case / "cli-state.json")

    def test_sources_normalize_equivalent_sessions(self):
        kinds = []
        for vendor in ("copilot", "claude", "codex"):
            doctor = self.run_adapter(vendor, "session-source", "doctor")
            self.assertTrue(doctor["healthy"])
            page = self.run_adapter(
                vendor,
                "session-source",
                "list",
                "--floor",
                "null",
                "--ceiling",
                "9999999999",
                "--cursor",
                "",
                "--page-size",
                "10",
            )
            self.assertEqual(len(page["items"]), 1)
            identity = page["items"][0]
            self.assertEqual(identity["qualified_session_id"], f"{vendor}:session")
            rendered = self.run_adapter(
                vendor,
                "session-source",
                "render",
                "--session",
                f"{vendor}:session",
            )
            kinds.append([event["kind"] for event in rendered["events"]])
        for row in kinds:
            self.assertEqual(
                [kind for kind in row if kind != "session_end"],
                ["user_message", "assistant_message", "tool_call"],
            )

    def test_claude_title_scan_is_bounded_and_malformed_lines_fall_back(self):
        source = self.case / "claude/project"
        transcript = source / "session.jsonl"
        original = transcript.read_text()
        transcript.write_text(
            "".join(
                json.dumps({
                    "type": "system",
                    "subtype": "fixture",
                    "sessionId": "session",
                    "uuid": f"padding-{index}",
                    "text": "x" * 2048,
                }) + "\n"
                for index in range(256)
            )
            + json.dumps({"type": "ai-title", "title": "Too late"}) + "\n"
            + original
        )
        page = self.run_adapter(
            "claude",
            "session-source",
            "list",
            "--floor",
            "null",
            "--ceiling",
            "9999999999",
            "--cursor",
            "",
            "--page-size",
            "10",
        )
        self.assertEqual(len(page["items"]), 1)
        self.assertNotIn("display_name", page["items"][0])
        (source / "zzzz-malformed.jsonl").write_text("{malformed\n")
        page = self.run_adapter(
            "claude",
            "session-source",
            "list",
            "--floor",
            "null",
            "--ceiling",
            "9999999999",
            "--cursor",
            "",
            "--page-size",
            "10",
        )
        self.assertEqual(
            [item["qualified_session_id"] for item in page["items"]],
            ["claude:session"],
        )
        doctor = self.run_adapter("claude", "session-source", "doctor")
        self.assertTrue(doctor["healthy"])

    def test_source_root_symlink_fails_closed(self):
        target = self.case / "real"
        target.mkdir()
        link = self.case / "link"
        link.symlink_to(target)
        result = subprocess.run(
            [
                sys.executable,
                str(adapter),
                "--vendor",
                "copilot",
                "--role",
                "session-source",
                "--source-root",
                str(link),
                "doctor",
            ],
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "source-root-symlink")

    def test_copilot_shipping_events_are_accepted_and_ignored(self):
        events = self.case / "copilot/session/events.jsonl"
        rows = [json.loads(line) for line in events.read_text().splitlines()]
        for index, kind in enumerate(
            (
                "session.autopilot_objective_changed",
                "session.workspace_file_changed",
                "session.schedule_created",
                "session.schedule_cancelled",
                "session.canvas.recorded",
                "session.context_changed",
                "session.remote_steerable_changed",
                "session.truncation",
                "subagent.failed",
            ),
            10,
        ):
            rows.insert(
                -1,
                {
                    "type": kind,
                    "data": {"value": "fixture"},
                    "id": f"shipping-{index}",
                    "timestamp": f"2026-01-01T00:00:{index:02d}Z",
                },
            )
        events.write_text("".join(json.dumps(row) + "\n" for row in rows))
        rendered = self.run_adapter(
            "copilot", "session-source", "render", "--session", "copilot:session"
        )
        self.assertEqual(
            [event["kind"] for event in rendered["events"]],
            ["user_message", "assistant_message", "tool_call", "session_end"],
        )

    def test_copilot_candidate_symlinks_fail_closed(self):
        source = self.case / "copilot"
        real = self.case / "copilot-real"
        real.mkdir()
        (real / "events.jsonl").write_text("")
        candidate = source / "linked-session"
        candidate.symlink_to(real)
        response = self.run_adapter(
            "copilot",
            "session-source",
            "list",
            "--floor",
            "null",
            "--ceiling",
            "9999999999",
            "--cursor",
            "",
            "--page-size",
            "10",
            check=False,
        )
        self.assertEqual(response["error"]["code"], "source-path-symlink")
        candidate.unlink()
        linked_events = source / "session/events.jsonl"
        original = linked_events.read_text()
        linked_events.unlink()
        target = self.case / "external-events.jsonl"
        target.write_text(original)
        linked_events.symlink_to(target)
        response = self.run_adapter(
            "copilot", "session-source", "doctor", check=False
        )
        self.assertEqual(response["error"]["code"], "source-path-symlink")

    def test_headless_executors_emit_structured_result(self):
        skill_schema = vendor_module.review_result_schema()["properties"][
            "artifact"
        ]["properties"]["skill_markdown"]
        self.assertIn("pattern", skill_schema)
        self.assertIn("frontmatter", skill_schema["description"])
        snapshot = self.case / "snapshot.json"
        snapshot.write_text(json.dumps({"events": []}))
        for vendor in ("copilot", "claude", "codex"):
            result_path = self.case / f"{vendor}-result.json"
            response = self.run_adapter(
                vendor,
                "review-executor",
                "run",
                "--snapshot",
                snapshot,
                "--result",
                result_path,
            )
            self.assertEqual(response["completion_sentinel"], "DREAMING_REVIEW_COMPLETE")
            self.assertEqual(json.loads(result_path.read_text())["terminal_route"], "discard")
        invocations = [
            json.loads(line)
            for line in Path(self.env["FAKE_CLI_LOG"]).read_text().splitlines()
        ]
        claude_run = next(
            row["args"]
            for row in invocations
            if row["vendor"] == "claude" and "--print" in row["args"]
        )
        copilot_run = next(
            row["args"]
            for row in invocations
            if row["vendor"] == "copilot" and "-p" in row["args"]
        )
        self.assertIn("--allow-all-tools", copilot_run)
        self.assertIn("--available-tools=__dreaming_no_tools__", copilot_run)
        self.assertNotIn("--available-tools=", copilot_run)
        self.assertIn("--safe-mode", claude_run)
        self.assertNotIn("--bare", claude_run)
        self.assertIn("--setting-sources", claude_run)
        self.assertEqual(claude_run[claude_run.index("--setting-sources") + 1], "")
        self.assertIn("--settings", claude_run)
        self.assertEqual(claude_run[claude_run.index("--settings") + 1], "{}")

    def test_executor_canonicalizes_tmp_alias(self):
        environment = {**self.env, "TMPDIR": "/tmp"}
        doctor = self.run_adapter(
            "codex",
            "review-executor",
            "doctor",
            environment=environment,
        )
        self.assertTrue(doctor["boundary_ready"])
        snapshot = self.case / "tmp-alias-snapshot.json"
        result_path = self.case / "tmp-alias-result.json"
        snapshot.write_text(json.dumps({"events": []}))
        response = self.run_adapter(
            "codex",
            "review-executor",
            "run",
            "--snapshot",
            snapshot,
            "--result",
            result_path,
            environment=environment,
        )
        self.assertEqual(response["completion_sentinel"], "DREAMING_REVIEW_COMPLETE")
        self.assertEqual(json.loads(result_path.read_text())["terminal_route"], "discard")

    def test_executable_canonicalizes_parent_alias_without_resolving_cli_symlink(self):
        alias = Path("/tmp") / f"dreaming-cli-alias-{os.getpid()}-{self._testMethodName}"
        alias.symlink_to(self.case / "bin", target_is_directory=True)
        previous = os.environ.get("DREAMING_CODEX_BIN")
        os.environ["DREAMING_CODEX_BIN"] = str(alias / "codex")
        try:
            resolved = Path(vendor_module.executable("codex"))
        finally:
            if previous is None:
                os.environ.pop("DREAMING_CODEX_BIN", None)
            else:
                os.environ["DREAMING_CODEX_BIN"] = previous
            alias.unlink()
        self.assertEqual(resolved, self.case / "bin/codex")
        self.assertEqual(resolved.name, "codex")
        self.assertEqual(resolved.resolve().name, "vendor-cli")

    def test_executor_sandbox_denies_source_and_unrelated_home(self):
        work = self.case / "boundary-work"
        work.mkdir()
        source_file = self.case / "copilot/session/events.jsonl"
        profile = vendor_module.sandbox_profile(
            work,
            "/bin/cat",
            [str(self.case / "copilot")],
            "copilot",
        )
        environment = {**os.environ, "HOME": str(Path.home())}
        denied_source = subprocess.run(
            ["/usr/bin/sandbox-exec", "-f", str(profile), "/bin/cat", str(source_file)],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(denied_source.returncode, 0)
        home_canary = Path.home() / ".claude.json"
        if home_canary.is_file():
            denied_home = subprocess.run(
                [
                    "/usr/bin/sandbox-exec",
                    "-f",
                    str(profile),
                    "/bin/cat",
                    str(home_canary),
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(denied_home.returncode, 0)

    def test_publishers_reconcile_one_immutable_bundle(self):
        paths = module.RuntimePaths(
            state=self.case / "state",
            data=self.case / "data",
            skills=self.case / "skills",
        )
        skill = paths.skills / "learned"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: learned\ndescription: fixture\n---\n")
        runtime = module.DreamingRuntime(paths, set())
        bundle, bundle_id = runtime.materialize_bundle(paths.skills)
        (skill / "SKILL.md").write_text(
            "---\nname: learned\ndescription: updated fixture\n---\n"
        )
        replacement, replacement_id = runtime.materialize_bundle(paths.skills)
        for vendor in ("copilot", "claude", "codex"):
            journal = self.case / f"{vendor}-journal.json"
            common = [
                sys.executable,
                str(adapter),
                "--vendor",
                vendor,
                "--role",
                "skill-publisher",
                "--ownership-journal",
                str(journal),
            ]
            subprocess.run(
                common
                + [
                    "install",
                    "--bundle",
                    str(bundle),
                    "--bundle-id",
                    bundle_id,
                ],
                env=self.env,
                check=True,
                stdout=subprocess.PIPE,
            )
            verified = subprocess.run(
                common + ["verify", "--bundle-id", bundle_id],
                env=self.env,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            )
            self.assertTrue(json.loads(verified.stdout)["verified"])
            subprocess.run(
                common
                + [
                    "install",
                    "--bundle",
                    str(replacement),
                    "--bundle-id",
                    replacement_id,
                ],
                env=self.env,
                check=True,
                stdout=subprocess.PIPE,
            )
            replacement_verified = subprocess.run(
                common + ["verify", "--bundle-id", replacement_id],
                env=self.env,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            )
            self.assertTrue(json.loads(replacement_verified.stdout)["verified"])
            owned = json.loads(journal.read_text())[vendor]
            self.assertEqual(owned["bundle_id"], replacement_id)
            self.assertNotIn("previous", owned)
            subprocess.run(
                common + ["remove"],
                env=self.env,
                check=True,
                stdout=subprocess.PIPE,
            )

    def test_publishers_retain_all_superseded_ownership_and_repair_inventory(self):
        paths = module.RuntimePaths(
            state=self.case / "state",
            data=self.case / "data",
            skills=self.case / "skills",
        )
        skill = paths.skills / "learned"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: learned\ndescription: one\n---\n")
        runtime = module.DreamingRuntime(paths, set())
        bundles = []
        for description in ("one", "two", "three"):
            (skill / "SKILL.md").write_text(
                f"---\nname: learned\ndescription: {description}\n---\n"
            )
            bundles.append(runtime.materialize_bundle(paths.skills))
        vendor = "copilot"
        journal = self.case / "superseded-journal.json"
        common = [
            sys.executable,
            str(adapter),
            "--vendor",
            vendor,
            "--role",
            "skill-publisher",
            "--ownership-journal",
            str(journal),
        ]
        for bundle, bundle_id in bundles:
            subprocess.run(
                common + ["install", "--bundle", str(bundle), "--bundle-id", bundle_id],
                env=self.env,
                check=True,
                stdout=subprocess.PIPE,
            )
        owned = json.loads(journal.read_text())[vendor]
        self.assertEqual(len(owned["superseded"]), 2)
        final_id = bundles[-1][1]
        subprocess.run(
            common + ["verify", "--bundle-id", final_id],
            env=self.env,
            check=True,
            stdout=subprocess.PIPE,
        )
        self.assertNotIn("superseded", json.loads(journal.read_text())[vendor])

        state_path = Path(self.env["FAKE_CLI_STATE"])
        state = json.loads(state_path.read_text())
        state["copilot_bundles"] = []
        state_path.write_text(json.dumps(state))
        final_bundle = bundles[-1][0]
        subprocess.run(
            common
            + ["install", "--bundle", str(final_bundle), "--bundle-id", final_id],
            env=self.env,
            check=True,
            stdout=subprocess.PIPE,
        )
        repaired = json.loads(state_path.read_text())
        self.assertIn(str(final_bundle), repaired["copilot_bundles"])

    def test_copilot_publication_restores_prior_bundle_when_replacement_fails(self):
        paths = module.RuntimePaths(
            state=self.case / "state",
            data=self.case / "data",
            skills=self.case / "skills",
        )
        skill = paths.skills / "learned"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: learned\ndescription: original\n---\n"
        )
        runtime = module.DreamingRuntime(paths, set())
        original, original_id = runtime.materialize_bundle(paths.skills)
        (skill / "SKILL.md").write_text(
            "---\nname: learned\ndescription: replacement\n---\n"
        )
        replacement, replacement_id = runtime.materialize_bundle(paths.skills)
        journal = self.case / "copilot-rollback-journal.json"
        common = [
            sys.executable,
            str(adapter),
            "--vendor",
            "copilot",
            "--role",
            "skill-publisher",
            "--ownership-journal",
            str(journal),
        ]
        subprocess.run(
            common
            + [
                "install",
                "--bundle",
                str(original),
                "--bundle-id",
                original_id,
            ],
            env=self.env,
            check=True,
            stdout=subprocess.PIPE,
        )
        failed_env = dict(self.env)
        failed_env["FAKE_COPILOT_FAIL_ADD_BUNDLE"] = str(replacement)
        result = subprocess.run(
            common
            + [
                "install",
                "--bundle",
                str(replacement),
                "--bundle-id",
                replacement_id,
            ],
            env=failed_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        owned = json.loads(journal.read_text())["copilot"]
        self.assertEqual(owned["bundle_id"], original_id)
        state = json.loads(Path(self.env["FAKE_CLI_STATE"]).read_text())
        self.assertEqual(state["copilot_bundles"], [str(original)])

    def test_copilot_failed_replacement_does_not_restore_absent_superseded_bundles(self):
        paths = module.RuntimePaths(
            state=self.case / "state",
            data=self.case / "data",
            skills=self.case / "skills",
        )
        skill = paths.skills / "learned"
        skill.mkdir(parents=True)
        runtime = module.DreamingRuntime(paths, set())
        bundles = []
        for description in ("one", "two", "three", "four"):
            (skill / "SKILL.md").write_text(
                f"---\nname: learned\ndescription: {description}\n---\n"
            )
            bundles.append(runtime.materialize_bundle(paths.skills))
        journal = self.case / "copilot-multigeneration-rollback.json"
        common = [
            sys.executable,
            str(adapter),
            "--vendor",
            "copilot",
            "--role",
            "skill-publisher",
            "--ownership-journal",
            str(journal),
        ]
        for bundle, bundle_id in bundles[:3]:
            subprocess.run(
                common
                + ["install", "--bundle", str(bundle), "--bundle-id", bundle_id],
                env=self.env,
                check=True,
                stdout=subprocess.PIPE,
            )
        before = json.loads(Path(self.env["FAKE_CLI_STATE"]).read_text())
        self.assertEqual(before["copilot_bundles"], [str(bundles[2][0])])
        failed_env = dict(self.env)
        failed_env["FAKE_COPILOT_FAIL_ADD_BUNDLE"] = str(bundles[3][0])
        result = subprocess.run(
            common
            + [
                "install",
                "--bundle",
                str(bundles[3][0]),
                "--bundle-id",
                bundles[3][1],
            ],
            env=failed_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        after = json.loads(Path(self.env["FAKE_CLI_STATE"]).read_text())
        self.assertEqual(after["copilot_bundles"], [str(bundles[2][0])])

    def test_non_copilot_publishers_require_exact_native_identity(self):
        paths = module.RuntimePaths(
            state=self.case / "state",
            data=self.case / "data",
            skills=self.case / "skills",
        )
        skill = paths.skills / "learned"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: learned\ndescription: fixture\n---\n"
        )
        bundle, bundle_id = module.DreamingRuntime(paths, set()).materialize_bundle(
            paths.skills
        )
        for vendor in ("claude", "codex"):
            journal = self.case / f"{vendor}-exact-journal.json"
            common = [
                sys.executable,
                str(adapter),
                "--vendor",
                vendor,
                "--role",
                "skill-publisher",
                "--ownership-journal",
                str(journal),
            ]
            subprocess.run(
                common
                + [
                    "install",
                    "--bundle",
                    str(bundle),
                    "--bundle-id",
                    bundle_id,
                ],
                env=self.env,
                check=True,
                stdout=subprocess.PIPE,
            )
            descriptor = json.loads(journal.read_text())[vendor]
            state_path = Path(self.env["FAKE_CLI_STATE"])
            state = json.loads(state_path.read_text())
            foreign = str((self.case / f"foreign-{vendor}").resolve())
            for key in (f"{vendor}_marketplaces", f"{vendor}_plugins"):
                state[key] = [{"name": descriptor["name"], "bundle": foreign}]
            state_path.write_text(json.dumps(state))
            verified = subprocess.run(
                common + ["verify", "--bundle-id", bundle_id],
                env=self.env,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            )
            self.assertFalse(json.loads(verified.stdout)["verified"])
            subprocess.run(
                common + ["remove"],
                env=self.env,
                check=True,
                stdout=subprocess.PIPE,
            )
            preserved = json.loads(state_path.read_text())
            self.assertEqual(
                preserved[f"{vendor}_plugins"],
                [{"name": descriptor["name"], "bundle": foreign}],
            )

    def test_configuration_is_complete_desired_state(self):
        output = self.case / "adapters.json"
        environment = {
            **self.env,
            "DREAMING_SESSION_SOURCES": "claude codex",
            "DREAMING_REVIEW_EXECUTORS": "codex claude",
            "DREAMING_SOURCE_EXECUTOR_ALLOW": "claude>codex codex>codex",
            "DREAMING_SKILL_TARGETS": "claude codex",
            "DREAMING_CLAUDE_SESSION_ROOT": str(self.case / "claude"),
            "DREAMING_CODEX_SESSION_ROOT": str(self.case / "codex"),
        }
        subprocess.run(
            [
                sys.executable,
                str(configure),
                "--output",
                str(output),
                "--repo-root",
                str(root),
                "--state-dir",
                str(self.case / "state"),
            ],
            env=environment,
            check=True,
            stdout=subprocess.PIPE,
        )
        data = json.loads(output.read_text())
        self.assertEqual(list(data["sources"]), ["claude", "codex"])
        self.assertEqual(data["executor_order"], ["codex", "claude"])
        self.assertEqual(data["routes"], ["claude>codex", "codex>codex"])
        self.assertEqual(list(data["publishers"]), ["claude", "codex"])
        expected_roots = {
            str((self.case / "claude").resolve()),
            str((self.case / "codex").resolve()),
        }
        for entry in data["executors"].values():
            argv = entry["argv"]
            denied = {
                argv[index + 1]
                for index, value in enumerate(argv)
                if value == "--deny-root"
            }
            self.assertEqual(denied, expected_roots)
            self.assertEqual(entry["timeout"], 120)
            self.assertGreater(entry["run_timeout"], 30)

        existing_copilot = {
            "argv": ["fixture", "--vendor", "copilot", "--role", "skill-publisher"]
        }
        data["publishers"]["copilot"] = existing_copilot
        output.write_text(json.dumps(data))
        subprocess.run(
            [
                sys.executable,
                str(configure),
                "--output",
                str(output),
                "--repo-root",
                str(root),
                "--state-dir",
                str(self.case / "state"),
            ],
            env=environment,
            check=True,
            stdout=subprocess.PIPE,
        )
        regenerated = json.loads(output.read_text())
        self.assertEqual(regenerated["retired_publishers"]["copilot"], existing_copilot)

    def test_single_pair_and_three_cli_learning_matrix(self):
        for size in (1, 2, 3):
            for vendors in combinations(("copilot", "claude", "codex"), size):
                name = "-".join(vendors)
                case = self.case / name
                state = case / "state"
                data = case / "data"
                skills = data / "skills"
                skill = skills / "learned"
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(
                    "---\nname: learned\ndescription: matrix fixture\n---\n"
                )
                config = case / "adapters.json"
                selected = " ".join(vendors)
                environment = {
                    **self.env,
                    "FAKE_CLI_STATE": str(case / "cli-state.json"),
                    "DREAMING_SESSION_SOURCES": selected,
                    "DREAMING_REVIEW_EXECUTORS": selected,
                    "DREAMING_SKILL_TARGETS": selected,
                    "DREAMING_COPILOT_SESSION_ROOT": str(self.case / "copilot"),
                    "DREAMING_CLAUDE_SESSION_ROOT": str(self.case / "claude"),
                    "DREAMING_CODEX_SESSION_ROOT": str(self.case / "codex"),
                    "DREAMING_QUIET_SECONDS": "0",
                }
                environment.pop("DREAMING_SOURCE_EXECUTOR_ALLOW", None)
                subprocess.run(
                    [
                        sys.executable,
                        str(configure),
                        "--output",
                        str(config),
                        "--repo-root",
                        str(root),
                        "--state-dir",
                        str(state),
                    ],
                    env=environment,
                    check=True,
                    stdout=subprocess.PIPE,
                )
                configured = json.loads(config.read_text())
                self.assertEqual(set(configured["sources"]), set(vendors))
                self.assertEqual(set(configured["executors"]), set(vendors))
                self.assertEqual(set(configured["publishers"]), set(vendors))
                self.assertEqual(
                    set(configured["routes"]),
                    {f"{vendor}>{vendor}" for vendor in vendors},
                )
                for group in ("sources", "publishers"):
                    for entry in configured[group].values():
                        entry["timeout"] = 120
                        entry["run_timeout"] = 120
                config.write_text(json.dumps(configured))
                run_environment = {
                    **environment,
                    "DREAMING_ADAPTER_CONFIG": str(config),
                    "DREAMING_DATA_DIR": str(data),
                    "DREAMING_STATE_DIR": str(state),
                    "DREAMING_SKILLS_ROOT": str(skills),
                }
                result = subprocess.run(
                    [sys.executable, str(core_path), "run"],
                    env=run_environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    {
                        "matrix": name,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    },
                )
                report = json.loads(result.stdout)
                self.assertTrue(report["ok"], (name, report))
                self.assertEqual(
                    {row["session_id"].split(":", 1)[0] for row in report["reviews"]},
                    set(vendors),
                )
                self.assertEqual(
                    {row["publisher"] for row in report["publication"]},
                    set(vendors),
                )

    def test_unreadable_source_does_not_block_healthy_sources(self):
        malformed = self.case / "claude/project/session.jsonl"
        malformed.write_text("{not-json}\n")
        case = self.case / "partial"
        state = case / "state"
        data = case / "data"
        skills = data / "skills"
        skill = skills / "learned"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: learned\ndescription: partial fixture\n---\n"
        )
        config = case / "adapters.json"
        environment = {
            **self.env,
            "DREAMING_SESSION_SOURCES": "copilot claude codex",
            "DREAMING_REVIEW_EXECUTORS": "copilot claude codex",
            "DREAMING_SKILL_TARGETS": "copilot codex",
            "DREAMING_SOURCE_EXECUTOR_ALLOW": (
                "copilot>copilot claude>claude codex>codex"
            ),
            "DREAMING_COPILOT_SESSION_ROOT": str(self.case / "copilot"),
            "DREAMING_CLAUDE_SESSION_ROOT": str(self.case / "claude"),
            "DREAMING_CODEX_SESSION_ROOT": str(self.case / "codex"),
            "DREAMING_QUIET_SECONDS": "0",
        }
        subprocess.run(
            [
                sys.executable,
                str(configure),
                "--output",
                str(config),
                "--repo-root",
                str(root),
                "--state-dir",
                str(state),
            ],
            env=environment,
            check=True,
            stdout=subprocess.PIPE,
        )
        result = subprocess.run(
            [sys.executable, str(core_path), "run"],
            env={
                **environment,
                "DREAMING_ADAPTER_CONFIG": str(config),
                "DREAMING_DATA_DIR": str(data),
                "DREAMING_STATE_DIR": str(state),
                "DREAMING_SKILLS_ROOT": str(skills),
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        report = json.loads(result.stdout)
        self.assertEqual(
            {row["session_id"].split(":", 1)[0] for row in report["reviews"]},
            {"copilot", "codex"},
        )
        self.assertTrue(
            any(
                error.get("adapter") == "claude"
                and error["phase"] == "adapter-health"
                for error in report["errors"]
            )
        )


unittest.main(verbosity=2)
PY
