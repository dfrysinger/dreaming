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
import argparse
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
            "CODEX_HOME": str(self.case / "codex-home"),
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
        binary=None,
        max_events=None,
        max_snapshot_bytes=None,
        model=None,
        token_budget=None,
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
        ]
        if binary is not None:
            invocation.extend(["--binary", str(binary)])
        if max_events is not None:
            invocation.extend(["--max-events", str(max_events)])
        if max_snapshot_bytes is not None:
            invocation.extend(["--max-snapshot-bytes", str(max_snapshot_bytes)])
        if model is not None:
            invocation.extend(["--model", model])
        if token_budget is not None:
            invocation.extend(["--token-budget", str(token_budget)])
        invocation.extend([command, *map(str, arguments)])
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

    def test_task_profile_mode_is_candidate_blind(self):
        snapshot = self.case / "profile-snapshot.json"
        snapshot.write_text(
            json.dumps(
                {
                    "identity": {
                        "qualified_session_id": "copilot:profile-fixture",
                    },
                    "events": [
                        {
                            "source_event_id": "event-1",
                            "role": "user",
                            "content": "make this a reusable procedure",
                        },
                        {
                            "source_event_id": "event-2",
                            "role": "assistant",
                            "content": "done",
                        },
                    ]
                }
            )
        )
        result_path = self.case / "profile-result.json"
        result = self.run_adapter(
            "copilot",
            "review-executor",
            "run",
            "--snapshot",
            snapshot,
            "--result",
            result_path,
            "--mode",
            "profile",
        )
        self.assertEqual(result["kind"], "llm_task_opportunity_profile")
        self.assertEqual(result["completion_sentinel"], "DREAMING_TASK_PROFILE_COMPLETE")
        self.assertEqual(result["profiles"][0]["source_event_ids"], ["event-1", "event-2"])
        self.assertTrue(result["profiles"][0]["task_key"].startswith("sha256:"))
        self.assertTrue(result["profiles"][0]["profile_id"].startswith("sha256:"))
        self.assertTrue(result["profile_set_id"].startswith("sha256:"))
        invocations = [
            json.loads(line)
            for line in Path(self.env["FAKE_CLI_LOG"]).read_text().splitlines()
        ]
        prompt_args = invocations[-1]["args"]
        prompt = json.loads(prompt_args[prompt_args.index("-p") + 1])
        self.assertNotIn("context", prompt)
        self.assertNotIn("skills", json.dumps(prompt))
        self.assertEqual(
            prompt["result_schema"]["properties"]["kind"]["const"],
            "llm_task_opportunity_profile",
        )
        self.assertEqual(
            prompt["result_schema"]["properties"]["schema_version"]["type"],
            "integer",
        )
        profile_schema = prompt["result_schema"]["properties"]["profiles"][
            "items"
        ]["properties"]
        for field in ("reuse_value", "confidence", "task_state"):
            self.assertEqual(profile_schema[field]["type"], "string")
        self.assertEqual(
            profile_schema["source_event_ids"]["items"]["enum"],
            ["event-1", "event-2"],
        )

    def test_task_profile_mode_rejects_reordered_evidence(self):
        snapshot = self.case / "profile-snapshot.json"
        snapshot.write_text(
            json.dumps(
                {
                    "identity": {
                        "qualified_session_id": "copilot:profile-fixture",
                    },
                    "events": [
                        {"source_event_id": "event-1"},
                        {"source_event_id": "event-2"},
                    ],
                }
            )
        )
        result = self.run_adapter(
            "copilot",
            "review-executor",
            "run",
            "--snapshot",
            snapshot,
            "--result",
            self.case / "profile-result.json",
            "--mode",
            "profile",
            check=False,
            environment={**self.env, "FAKE_TASK_PROFILE_REVERSE": "1"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "malformed-executor-result",
        )

    def _task_profile_review_fixture(self):
        snapshot_value = {
            "identity": {
                "qualified_session_id": "copilot:profiled-review",
                "source_revision": "revision-1",
            },
            "events": [
                {"source_event_id": "event-1"},
                {"source_event_id": "event-2"},
            ],
        }
        snapshot = self.case / "profiled-review-snapshot.json"
        snapshot.write_text(json.dumps(snapshot_value))
        def profile(
            event_ids,
            task_type,
            summary,
            reuse_value,
            procedure,
        ):
            model_profile = {
                "source_event_ids": event_ids,
                "task_type": task_type,
                "abstract_summary": summary,
                "reuse_value": reuse_value,
                "procedure": procedure,
                "confidence": "high",
                "sensitive_source": False,
                "task_state": "completed",
            }
            return {
                **model_profile,
                "task_key": vendor_module.sha(
                    {
                        "qualified_session_id": "copilot:profiled-review",
                        "source_event_ids": event_ids,
                    }
                ),
                "profile_id": vendor_module.sha(
                    {
                        "qualified_session_id": "copilot:profiled-review",
                        **model_profile,
                    }
                ),
                "procedure_fingerprint": (
                    vendor_module.sha(procedure)
                    if procedure is not None
                    else None
                ),
            }
        reusable_profile = profile(
            ["event-1"],
            "document-reusable-procedure",
            "Turn completed work into a reusable procedure.",
            "reusable-procedure",
            {
                "trigger": "A completed task contains a reusable procedure.",
                "outcome": "The procedure is captured for reuse.",
                "actions": ["Identify the task.", "Capture the procedure."],
                "exclusions": ["Do not copy source-specific details."],
            },
        )
        one_off_profile = profile(
            ["event-2"],
            "answer-one-off-question",
            "Answer a question that has no reusable procedure.",
            "one-off",
            None,
        )
        profiles = [reusable_profile, one_off_profile]
        protocol, capabilities = vendor_module.PROTOCOLS["review-executor"]
        receipt_body = {
            "schema_version": 1,
            "kind": "task_profile_receipt",
            "snapshot_sha256": vendor_module.sha(snapshot_value),
            "source_revision": "revision-1",
            "qualified_session_id": "copilot:profiled-review",
            "observed_at": "2026-01-01T00:00:00+00:00",
            "executor": "copilot",
            "executor_identity": {
                "ok": True,
                "protocol": protocol,
                "version": 1,
                "adapter_id": "copilot",
                "capabilities": capabilities,
            },
            "model": "fixture-profile-model",
            "profiles": profiles,
        }
        receipt_body["profile_set_id"] = vendor_module.sha(
            {
                "snapshot_sha256": receipt_body["snapshot_sha256"],
                "qualified_session_id": receipt_body[
                    "qualified_session_id"
                ],
                "profiles": profiles,
            }
        )
        receipt = {
            **receipt_body,
            "receipt_sha256": vendor_module.sha(receipt_body),
        }
        receipt_path = (
            self.case
            / f"{receipt['receipt_sha256'].removeprefix('sha256:')}.json"
        )
        receipt_path.write_text(json.dumps(receipt))
        return (
            snapshot,
            snapshot_value,
            receipt_path,
            receipt,
            reusable_profile,
        )

    def test_review_receives_only_reusable_validated_task_profile_context(self):
        (
            snapshot,
            _snapshot_value,
            receipt_path,
            receipt,
            reusable_profile,
        ) = self._task_profile_review_fixture()
        self.run_adapter(
            "copilot",
            "review-executor",
            "run",
            "--snapshot",
            snapshot,
            "--result",
            self.case / "profiled-review-result.json",
            "--task-profile-receipt",
            receipt_path,
            "--task-profile-executor",
            "copilot",
        )
        invocations = [
            json.loads(line)
            for line in Path(self.env["FAKE_CLI_LOG"]).read_text().splitlines()
        ]
        prompt_args = invocations[-1]["args"]
        prompt = json.loads(prompt_args[prompt_args.index("-p") + 1])
        context = prompt["task_profile_context"]
        self.assertEqual(
            context["receipt_sha256"], receipt["receipt_sha256"]
        )
        self.assertEqual(context["profiles"], [reusable_profile])

    def test_review_omits_context_when_receipt_has_no_reusable_profiles(self):
        (
            snapshot,
            _snapshot_value,
            receipt_path,
            receipt,
            _reusable_profile,
        ) = self._task_profile_review_fixture()
        receipt["profiles"] = [receipt["profiles"][1]]
        receipt["profile_set_id"] = vendor_module.sha(
            {
                "snapshot_sha256": receipt["snapshot_sha256"],
                "qualified_session_id": receipt["qualified_session_id"],
                "profiles": receipt["profiles"],
            }
        )
        body = {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
        receipt["receipt_sha256"] = vendor_module.sha(body)
        receipt_path.unlink()
        receipt_path = (
            self.case
            / f"{receipt['receipt_sha256'].removeprefix('sha256:')}.json"
        )
        receipt_path.write_text(json.dumps(receipt))
        self.run_adapter(
            "copilot",
            "review-executor",
            "run",
            "--snapshot",
            snapshot,
            "--result",
            self.case / "one-off-profiled-review-result.json",
            "--task-profile-receipt",
            receipt_path,
            "--task-profile-executor",
            "copilot",
        )
        invocations = [
            json.loads(line)
            for line in Path(self.env["FAKE_CLI_LOG"]).read_text().splitlines()
        ]
        prompt_args = invocations[-1]["args"]
        prompt = json.loads(prompt_args[prompt_args.index("-p") + 1])
        self.assertNotIn("task_profile_context", prompt)

    def test_review_rejects_invalid_task_profile_receipts_with_reason(self):
        cases = {
            "schema-version": lambda receipt, snapshot: receipt.__setitem__(
                "schema_version", 2
            ),
            "source-revision": lambda receipt, snapshot: receipt.__setitem__(
                "source_revision", "revision-2"
            ),
            "executor": lambda receipt, snapshot: receipt.__setitem__(
                "executor", "claude"
            ),
            "executor-identity": lambda receipt, snapshot: receipt[
                "executor_identity"
            ].__setitem__("adapter_id", "claude"),
            "profile-id": lambda receipt, snapshot: receipt["profiles"][0].__setitem__(
                "profile_id", "sha256:" + "0" * 64
            ),
            "profile-set-id": lambda receipt, snapshot: receipt.__setitem__(
                "profile_set_id", "sha256:" + "0" * 64
            ),
            "snapshot-sha256": lambda receipt, snapshot: snapshot["events"].append(
                {"source_event_id": "event-3"}
            ),
        }
        for expected_reason, mutate in cases.items():
            with self.subTest(expected_reason=expected_reason):
                (
                    snapshot_path,
                    snapshot,
                    receipt_path,
                    receipt,
                    _reusable_profile,
                ) = self._task_profile_review_fixture()
                mutate(receipt, snapshot)
                snapshot_path.write_text(json.dumps(snapshot))
                body = {
                    key: value
                    for key, value in receipt.items()
                    if key != "receipt_sha256"
                }
                receipt["receipt_sha256"] = vendor_module.sha(body)
                receipt_path.unlink()
                receipt_path = (
                    self.case
                    / f"{receipt['receipt_sha256'].removeprefix('sha256:')}.json"
                )
                receipt_path.write_text(json.dumps(receipt))
                result = self.run_adapter(
                    "copilot",
                    "review-executor",
                    "run",
                    "--snapshot",
                    snapshot_path,
                    "--result",
                    self.case / f"invalid-{expected_reason}.json",
                    "--task-profile-receipt",
                    receipt_path,
                    "--task-profile-executor",
                    "copilot",
                    check=False,
                )
                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["error"]["code"],
                    "task-profile-receipt-invalid",
                )
                self.assertTrue(
                    result["error"]["message"].endswith(expected_reason),
                    result["error"]["message"],
                )

    def test_review_rejects_missing_executor_and_malformed_snapshot_identity(self):
        (
            snapshot_path,
            snapshot,
            receipt_path,
            receipt,
            _reusable_profile,
        ) = self._task_profile_review_fixture()
        missing_executor = self.run_adapter(
            "copilot",
            "review-executor",
            "run",
            "--snapshot",
            snapshot_path,
            "--result",
            self.case / "missing-profile-executor.json",
            "--task-profile-receipt",
            receipt_path,
            check=False,
        )
        self.assertFalse(missing_executor["ok"])
        self.assertTrue(
            missing_executor["error"]["message"].endswith("executor-required")
        )

        snapshot["identity"].pop("qualified_session_id")
        snapshot_path.write_text(json.dumps(snapshot))
        receipt["qualified_session_id"] = None
        receipt["snapshot_sha256"] = vendor_module.sha(snapshot)
        receipt["profile_set_id"] = vendor_module.sha(
            {
                "snapshot_sha256": receipt["snapshot_sha256"],
                "qualified_session_id": None,
                "profiles": receipt["profiles"],
            }
        )
        body = {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
        receipt["receipt_sha256"] = vendor_module.sha(body)
        receipt_path.unlink()
        receipt_path = (
            self.case
            / f"{receipt['receipt_sha256'].removeprefix('sha256:')}.json"
        )
        receipt_path.write_text(json.dumps(receipt))
        malformed = self.run_adapter(
            "copilot",
            "review-executor",
            "run",
            "--snapshot",
            snapshot_path,
            "--result",
            self.case / "malformed-profile-identity.json",
            "--task-profile-receipt",
            receipt_path,
            "--task-profile-executor",
            "copilot",
            check=False,
        )
        self.assertFalse(malformed["ok"])
        self.assertTrue(
            malformed["error"]["message"].endswith("qualified-session-id")
        )

    def test_review_rejects_anti_substitution_receipt_failures(self):
        def run_invalid(snapshot_path, receipt_path, reason):
            result = self.run_adapter(
                "copilot",
                "review-executor",
                "run",
                "--snapshot",
                snapshot_path,
                "--result",
                self.case / f"invalid-anti-substitution-{reason}.json",
                "--task-profile-receipt",
                receipt_path,
                "--task-profile-executor",
                "copilot",
                check=False,
            )
            self.assertFalse(result["ok"])
            self.assertTrue(
                result["error"]["message"].endswith(reason),
                result["error"]["message"],
            )

        snapshot_path, _, receipt_path, receipt, _ = (
            self._task_profile_review_fixture()
        )
        receipt["observed_at"] = "tampered"
        receipt_path.write_text(json.dumps(receipt))
        run_invalid(snapshot_path, receipt_path, "receipt-sha256")

        snapshot_path, _, receipt_path, receipt, _ = (
            self._task_profile_review_fixture()
        )
        receipt["observed_at"] = "2026-01-01T00:00:01Z"
        body = {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
        receipt["receipt_sha256"] = vendor_module.sha(body)
        receipt_path.write_text(json.dumps(receipt))
        run_invalid(snapshot_path, receipt_path, "receipt-filename")

        snapshot_path, _, receipt_path, receipt, _ = (
            self._task_profile_review_fixture()
        )
        receipt["profiles"][0]["source_event_ids"] = ["missing-event"]
        body = {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
        receipt["receipt_sha256"] = vendor_module.sha(body)
        receipt_path.unlink()
        receipt_path = (
            self.case
            / f"{receipt['receipt_sha256'].removeprefix('sha256:')}.json"
        )
        receipt_path.write_text(json.dumps(receipt))
        run_invalid(snapshot_path, receipt_path, "profile-evidence")

        snapshot_path, _, receipt_path, receipt, _ = (
            self._task_profile_review_fixture()
        )
        receipt["profiles"] = [
            receipt["profiles"][0],
            dict(receipt["profiles"][0]),
        ]
        receipt["profile_set_id"] = vendor_module.sha(
            {
                "snapshot_sha256": receipt["snapshot_sha256"],
                "qualified_session_id": receipt["qualified_session_id"],
                "profiles": receipt["profiles"],
            }
        )
        body = {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
        receipt["receipt_sha256"] = vendor_module.sha(body)
        receipt_path.unlink()
        receipt_path = (
            self.case
            / f"{receipt['receipt_sha256'].removeprefix('sha256:')}.json"
        )
        receipt_path.write_text(json.dumps(receipt))
        run_invalid(snapshot_path, receipt_path, "duplicate-profile-identity")

        snapshot_path, _, receipt_path, _, _ = (
            self._task_profile_review_fixture()
        )
        symlink_path = self.case / "receipt-link.json"
        symlink_path.symlink_to(receipt_path)
        run_invalid(snapshot_path, symlink_path, "receipt-path")

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
log_path = Path(
    os.environ.get(
        "FAKE_CLI_LOG",
        str(Path(sys.argv[0]).resolve().parent / "isolated-cli-invocations.jsonl"),
    )
)
with log_path.open("a") as log:
    log.write(json.dumps({"vendor": vendor, "args": args}) + "\\n")
state_value = os.environ.get("FAKE_CLI_STATE")
state_path = Path(state_value) if state_value else Path(sys.argv[0]).resolve().parent / "isolated-state.json"
state = json.loads(state_path.read_text()) if state_path.exists() else {}
def write_codex_marketplace_config():
    if vendor != "codex":
        return
    codex_home = Path(os.environ["CODEX_HOME"])
    codex_home.mkdir(parents=True, exist_ok=True)
    lines = []
    for value in state.get("codex_marketplaces", []):
        lines.extend([
          f'[marketplaces.{json.dumps(value["name"])}]',
          'source_type = "local"',
          f'source = {json.dumps(value["bundle"])}',
          "",
        ])
    (codex_home / "config.toml").write_text("\\n".join(lines))
def input_author_payload(prompt):
    repair = "EVALUATION_INPUT_REPAIR_OPERATION" in prompt
    packet = json.loads(
      prompt.split("repair_packet:\\n" if repair else "authoring_packet:\\n", 1)[1]
    )
    cases = packet["initial_suite"]["cases"] if repair else packet["suite_template"]["cases"]
    return {
      "outcome": "draft",
      "summary": "safe synthetic fixture cases",
      "cases": [
        {
          "id": case["id"],
          "task_id": f"{'repaired' if repair else 'authored'}:{case['class']}-{index:04d}",
          "prompt": f"Complete the {'repaired ' if repair else ''}standalone {case['class']} task {index} — safely.",
        }
        for index, case in enumerate(cases, 1)
      ],
    }
def input_review_payload(prompt):
    json.loads(prompt.split("review_packet:\\n", 1)[1])
    return {
      "decision": "accept",
      "summary": "exact manifest satisfies the safe review contract",
      "reason": None,
    }
def task_profile_payload(prompt):
    packet = json.loads(prompt)
    assert "context" not in packet
    assert "skills" not in prompt
    event_ids = [
      event["source_event_id"]
      for event in packet["snapshot"]["events"]
      if event.get("source_event_id")
    ]
    if os.environ.get("FAKE_TASK_PROFILE_REVERSE") == "1":
        event_ids.reverse()
    return {
      "schema_version": 1,
      "kind": "llm_task_opportunity_profile",
      "profiles": [{
        "source_event_ids": event_ids[:2],
        "task_type": "document-reusable-procedure",
        "abstract_summary": "Turn completed work into a reusable procedure.",
        "reuse_value": "reusable-procedure",
        "procedure": {
          "trigger": "A completed task contains a reusable procedure.",
          "outcome": "The procedure is captured for reuse.",
          "actions": ["Identify the task outcome.", "Capture the ordered procedure."],
          "exclusions": ["Do not copy source-specific details."],
        },
        "confidence": "high",
        "sensitive_source": False,
        "task_state": "completed",
      }],
    }
if "--version" in args:
    print(vendor + " 1.0")
    raise SystemExit()
if (
    vendor == "copilot"
    and "-p" in args
    and any("DREAMING_AUTH_OK" in arg for arg in args)
):
    if os.environ.get("FAKE_COPILOT_AUTH_FAILURE") == "1":
        print("authentication required", file=sys.stderr)
        raise SystemExit(1)
    if os.environ.get("FAKE_COPILOT_AUTH_ECHO_ONLY") == "1":
        print(json.dumps({"prompt": next(arg for arg in args if "DREAMING_AUTH_OK" in arg)}))
        raise SystemExit()
    print(json.dumps({"result":"DREAMING_AUTH_OK"}))
    raise SystemExit()
if vendor == "claude" and args[:3] == ["auth", "status", "--json"]:
    print(json.dumps({"loggedIn": True}))
    raise SystemExit()
if vendor == "codex" and args[:2] == ["login", "status"]:
    print("Logged in")
    raise SystemExit()
if vendor == "codex" and "--output-last-message" in args:
    target = Path(args[args.index("--output-last-message") + 1])
    author_prompt = next(
      (arg for arg in args if "EVALUATION_INPUT_AUTHOR_OPERATION" in arg
       or "EVALUATION_INPUT_REPAIR_OPERATION" in arg), None
    )
    if author_prompt is not None:
        requested_model = args[args.index("--model") + 1]
        target.write_text(json.dumps(input_author_payload(author_prompt)))
        print(json.dumps({
          "type": "turn_context", "payload": {"model": requested_model},
          "usage": {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
        }))
        raise SystemExit()
    review_prompt = next(
      (arg for arg in args if "EVALUATION_INPUT_REVIEW_OPERATION" in arg), None
    )
    if review_prompt is not None:
        requested_model = args[args.index("--model") + 1]
        target.write_text(json.dumps(input_review_payload(review_prompt)))
        print(json.dumps({
          "type": "turn_context", "payload": {"model": requested_model},
          "usage": {"input_tokens": 90, "output_tokens": 30, "total_tokens": 120},
        }))
        raise SystemExit()
    prompt = next((arg for arg in args if "result_schema" in arg), "")
    if "llm_task_opportunity_profile" in prompt:
        target.write_text(json.dumps(task_profile_payload(prompt)))
        raise SystemExit()
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
    author_prompt = next(
      (arg for arg in args if "EVALUATION_INPUT_AUTHOR_OPERATION" in arg
       or "EVALUATION_INPUT_REPAIR_OPERATION" in arg), None
    )
    if author_prompt is not None:
        requested_model = args[args.index("--model") + 1]
        payload = input_author_payload(author_prompt)
        if vendor == "copilot":
            print(json.dumps({"events": [
              {"type": "session.start", "data": {"model": requested_model}},
              {"type": "result", "data": payload},
              {"type": "session.usage_checkpoint",
               "usage": {"input_tokens": 100, "output_tokens": 40,
                         "total_tokens": 140}},
            ]}))
        else:
            print(json.dumps({
              "type": "system", "model": requested_model, "result": payload,
              "usage": {"input_tokens": 100, "output_tokens": 40,
                        "total_tokens": 140},
            }))
        raise SystemExit()
    review_prompt = next(
      (arg for arg in args if "EVALUATION_INPUT_REVIEW_OPERATION" in arg), None
    )
    if review_prompt is not None:
        requested_model = args[args.index("--model") + 1]
        payload = input_review_payload(review_prompt)
        if vendor == "copilot":
            print(json.dumps({"events": [
              {"type": "session.start", "data": {"model": requested_model}},
              {"type": "result", "data": payload},
              {"type": "session.usage_checkpoint",
               "usage": {"input_tokens": 90, "output_tokens": 30,
                         "total_tokens": 120}},
            ]}))
        else:
            print(json.dumps({
              "type": "system", "model": requested_model, "result": payload,
              "usage": {"input_tokens": 90, "output_tokens": 30,
                        "total_tokens": 120},
            }))
        raise SystemExit()
    prompt = next((arg for arg in args if "result_schema" in arg), "")
    if "llm_task_opportunity_profile" in prompt:
        print(json.dumps({"result":task_profile_payload(prompt)}))
        raise SystemExit()
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
    write_codex_marketplace_config()
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
    write_codex_marketplace_config()
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

    def test_source_render_keeps_bounded_latest_evidence(self):
        events = self.case / "copilot/session/events.jsonl"
        rows = [
            {
                "type": "user.message",
                "data": {"content": f"message-{index}-" + ("x" * 400)},
                "id": f"event-{index}",
                "timestamp": f"2026-01-01T00:00:{index:02d}Z",
            }
            for index in range(10)
        ]
        rows.append(
            {
                "type": "session.shutdown",
                "data": {"shutdownType": "complete"},
                "id": "session-end",
                "timestamp": "2026-01-01T00:01:00Z",
            }
        )
        events.write_text("".join(json.dumps(row) + "\n" for row in rows))
        rendered = self.run_adapter(
            "copilot",
            "session-source",
            "render",
            "--session",
            "copilot:session",
            max_events=4,
            max_snapshot_bytes=1_200,
        )
        inspected = self.run_adapter(
            "copilot",
            "session-source",
            "inspect",
            "--session",
            "copilot:session",
            max_events=4,
            max_snapshot_bytes=1_200,
        )
        self.assertTrue(rendered["truncated"])
        self.assertLessEqual(len(rendered["events"]), 4)
        self.assertLessEqual(len(canonical(rendered["events"])), 1_200)
        self.assertEqual(rendered["events"][-1]["source_event_id"], "session-end")
        self.assertEqual(
            inspected["session"]["snapshot_digest"],
            vendor_module.sha(rendered["events"]),
        )
        self.assertEqual(inspected["session"]["event_frontier"], "session-end")

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
        invocations = []
        for log_path in (
            Path(self.env["FAKE_CLI_LOG"]),
            self.case / "bin/isolated-cli-invocations.jsonl",
        ):
            if log_path.is_file():
                invocations.extend(
                    json.loads(line) for line in log_path.read_text().splitlines()
                )
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

    def test_evaluation_input_author_is_structured_bounded_and_toolless(self):
        packet = self.case / "packet.json"
        cases = [
            {
                "id": f"{case_class.replace('_', '-')}-case",
                "class": case_class,
                "deterministic_graders": ["objective"],
            }
            for case_class in (
                "intended",
                "related",
                "activation_positive",
                "activation_negative",
            )
        ]
        packet.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "safe_evaluation_input_authoring_packet",
                    "packet_id": "sha256:" + "1" * 64,
                    "candidate_id": "sha256:" + "2" * 64,
                    "suite_template": {"cases": cases},
                    "compilation_contract": {
                        "case_runtime": [
                            {
                                "id": case["id"],
                                "fixture": "synthetic",
                                "artifacts": [],
                                "semantic": case["class"] in {"intended", "related"},
                            }
                            for case in cases
                        ]
                    },
                }
            )
        )
        unvalidated_result = self.case / "unvalidated-result.json"
        unvalidated_draft = self.case / "unvalidated-draft.json"
        unvalidated = self.run_adapter(
            "copilot",
            "evaluation-input-author",
            "run",
            "--operation",
            "author",
            "--packet",
            packet,
            "--result",
            unvalidated_result,
            "--draft-output",
            unvalidated_draft,
            model="fixture-author-model",
            check=False,
        )
        self.assertEqual(unvalidated["error"]["code"], "missing-argument")
        self.assertFalse(unvalidated_result.exists())
        self.assertFalse(unvalidated_draft.exists())
        environment_root = self.case / "author-environment"
        environment_root.mkdir()
        with mock.patch.dict(
            os.environ,
            {**self.env, "PRIVATE_AMBIENT_SECRET": "must-not-cross"},
            clear=False,
        ):
            author_environment = vendor_module.evaluation_input_author_environment(
                environment_root, self.env["DREAMING_COPILOT_BIN"]
            )
        self.assertNotIn("PRIVATE_AMBIENT_SECRET", author_environment)
        self.assertEqual(
            author_environment["HOME"], str(environment_root / "home")
        )
        profile = vendor_module.sandbox_profile(
            environment_root,
            self.env["DREAMING_COPILOT_BIN"],
            [],
            "isolated",
        ).read_text()
        self.assertIn(
            f'(deny file-read* file-write* (subpath "{Path.home().resolve()}"))',
            profile,
        )
        self.assertNotIn("Library/Keychains/login.keychain-db", profile)
        def author_args(token_budget, result_path, draft_path):
            return argparse.Namespace(
                vendor="copilot",
                operation="author",
                packet=str(packet),
                result=str(result_path),
                draft_output=str(draft_path),
                model="fixture-author-model",
                binary=self.env["DREAMING_COPILOT_BIN"],
                timeout=60,
                output_bytes=100_000,
                token_budget=token_budget,
                deny_root=[],
                skill_dir="validated-by-test-double",
                suite="validated-by-test-double",
                policy="validated-by-test-double",
                config="validated-by-test-double",
                routing="validated-by-test-double",
                harness="validated-by-test-double",
                catalog="validated-by-test-double",
            )
        alias_root = self.case / "adapter-alias"
        alias_root.mkdir()
        adapter_alias = alias_root / "dreaming-vendor-adapter.py"
        adapter_alias.symlink_to(adapter)
        (alias_root / "skill-evaluation.py").write_text(
            "raise SystemExit('decoy evaluator must not run')\n"
        )
        validator_commands = []
        validator_work = self.case / "validator-work"
        validator_work.mkdir()

        def validate_packet(command, *args, **kwargs):
            validator_commands.append(command)
            output = Path(command[command.index("--output") + 1])
            output.write_bytes(packet.read_bytes())
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with mock.patch.object(
            vendor_module, "__file__", str(adapter_alias)
        ), mock.patch.object(
            vendor_module, "run_process_bounded", side_effect=validate_packet
        ):
            vendor_module.validate_evaluation_input_packet(
                author_args(
                    140,
                    self.case / "anchor-result.json",
                    self.case / "anchor-draft.json",
                ),
                json.loads(packet.read_text()),
                validator_work,
            )
        self.assertEqual(
            Path(validator_commands[0][1]),
            adapter.resolve().with_name("skill-evaluation.py"),
        )
        result_path = self.case / "copilot-author-result.json"
        draft_path = self.case / "copilot-draft.json"
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch.object(
            vendor_module, "validate_evaluation_input_packet"
        ), self.assertRaises(SystemExit):
            vendor_module.evaluation_input_author_run(
                author_args(140, result_path, draft_path)
            )
        response = json.loads(result_path.read_text())
        self.assertEqual(response["outcome"], "draft")
        self.assertEqual(response["usage"]["normalized_tokens"], 140)
        self.assertEqual(response["billing"]["status"], "unavailable")
        self.assertIsNone(response["billing"]["cost_usd"])
        draft = json.loads(draft_path.read_text())
        expected_draft_id = "sha256:" + hashlib.sha256(
            json.dumps(
                draft, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
        ).hexdigest()
        self.assertEqual(response["draft_id"], expected_draft_id)
        self.assertIn("—", draft["cases"][0]["prompt"])
        self.assertEqual(draft["packet_id"], "sha256:" + "1" * 64)
        self.assertEqual(
            [item["id"] for item in draft["cases"]],
            [item["id"] for item in cases],
        )
        self.assertEqual(
            [item["fixture"] for item in draft["cases"]],
            ["synthetic"] * 4,
        )
        for vendor in ("claude", "codex"):
            doctor = self.run_adapter(
                vendor, "evaluation-input-author", "doctor"
            )
            self.assertFalse(doctor["boundary_ready"])
            refusal = self.run_adapter(
                vendor,
                "evaluation-input-author",
                "run",
                "--operation",
                "author",
                "--packet",
                packet,
                "--result",
                self.case / f"{vendor}-author-result.json",
                "--draft-output",
                self.case / f"{vendor}-author-draft.json",
                model="fixture-author-model",
                check=False,
            )
            self.assertEqual(
                refusal["error"]["code"], "authoring-boundary-unavailable"
            )
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch.object(
            vendor_module, "validate_evaluation_input_packet"
        ), self.assertRaises(vendor_module.AdapterError) as refusal:
            vendor_module.evaluation_input_author_run(
                author_args(
                    139,
                    self.case / "over-budget-result.json",
                    self.case / "over-budget-draft.json",
                )
            )
        self.assertEqual(refusal.exception.code, "token-limit-exceeded")
        self.assertFalse((self.case / "over-budget-result.json").exists())
        self.assertFalse((self.case / "over-budget-draft.json").exists())
        invocations = []
        for log_path in (
            Path(self.env["FAKE_CLI_LOG"]),
            self.case / "bin/isolated-cli-invocations.jsonl",
        ):
            if log_path.is_file():
                invocations.extend(
                    json.loads(line) for line in log_path.read_text().splitlines()
                )
        author_invocations = [
            row["args"]
            for row in invocations
            if any(
                "EVALUATION_INPUT_AUTHOR_OPERATION" in arg
                for arg in row["args"]
            )
        ]
        self.assertTrue(author_invocations)
        for invocation in author_invocations:
            self.assertIn(
                "--available-tools=__dreaming_no_tools__", invocation
            )
            self.assertNotIn("--available-tools=", invocation)
            self.assertNotIn("--allowedTools", invocation)

        repair_packet = self.case / "repair-packet.json"
        repair_packet.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "safe_evaluation_input_repair_packet",
                    "packet_id": "sha256:" + "6" * 64,
                    "claim_id": "sha256:" + "7" * 64,
                    "candidate_id": "sha256:" + "2" * 64,
                    "initial_manifest_sha256": "sha256:" + "4" * 64,
                    "initial_validation_contract": {
                        "receipt_sha256": "sha256:" + "5" * 64,
                    },
                    "initial_review_receipt_sha256s": [
                        "sha256:" + "8" * 64,
                        "sha256:" + "9" * 64,
                    ],
                    "review_set_id": "sha256:" + "a" * 64,
                    "original_author_model": "fixture-author-model",
                    "initial_suite": {"cases": cases},
                    "compilation_contract": {
                        "case_runtime": [
                            {
                                "id": case["id"],
                                "fixture": "synthetic",
                                "artifacts": [],
                                "semantic": case["class"]
                                in {"intended", "related"},
                            }
                            for case in cases
                        ]
                    },
                }
            )
        )
        repair_result = self.case / "copilot-repair-result.json"
        repair_draft = self.case / "copilot-repair-draft.json"
        repair_args = argparse.Namespace(
            vendor="copilot",
            operation="repair",
            packet=str(repair_packet),
            result=str(repair_result),
            draft_output=str(repair_draft),
            model="fixture-author-model",
            binary=self.env["DREAMING_COPILOT_BIN"],
            timeout=60,
            output_bytes=100_000,
            token_budget=140,
            deny_root=[],
            skill_dir="validated-by-test-double",
            claim_id="sha256:" + "7" * 64,
            manifest="sha256:" + "4" * 64,
            validation="sha256:" + "5" * 64,
            review=["sha256:" + "8" * 64, "sha256:" + "9" * 64],
            original_author_model="fixture-author-model",
            suite=None,
            policy=None,
            config=None,
            routing=None,
            harness=None,
            catalog=None,
        )
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch.object(
            vendor_module, "validate_evaluation_input_packet"
        ), self.assertRaises(SystemExit):
            vendor_module.evaluation_input_author_run(repair_args)
        repair = json.loads(repair_result.read_text())
        self.assertEqual(repair["operation"], "repair")
        self.assertEqual(repair["model"], "fixture-author-model")
        self.assertEqual(
            repair["initial_manifest_sha256"], "sha256:" + "4" * 64
        )
        self.assertEqual(
            repair["original_review_receipt_sha256s"],
            ["sha256:" + "8" * 64, "sha256:" + "9" * 64],
        )
        self.assertEqual(
            json.loads(repair_draft.read_text())["kind"],
            "safe_evaluation_input_repair_draft",
        )

        review_packet = self.case / "review-packet.json"
        review_packet.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "safe_evaluation_input_review_packet",
                    "packet_id": "sha256:" + "3" * 64,
                    "candidate_id": "sha256:" + "2" * 64,
                    "input_manifest_sha256": "sha256:" + "4" * 64,
                    "validation_contract": {
                        "receipt_sha256": "sha256:" + "5" * 64,
                    },
                    "review_contract": {"accept_only_if": ["safe"]},
                }
            )
        )
        review_result = self.case / "copilot-review-result.json"
        review_args = argparse.Namespace(
            vendor="copilot",
            operation="review",
            packet=str(review_packet),
            result=str(review_result),
            draft_output=None,
            model="fixture-review-model",
            binary=self.env["DREAMING_COPILOT_BIN"],
            timeout=60,
            output_bytes=100_000,
            token_budget=120,
            deny_root=[],
            skill_dir="validated-by-test-double",
            manifest="sha256:" + "4" * 64,
            validation="sha256:" + "5" * 64,
            suite=None,
            policy=None,
            config=None,
            routing=None,
            harness=None,
            catalog=None,
        )
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch.object(
            vendor_module, "validate_evaluation_input_packet"
        ), self.assertRaises(SystemExit):
            vendor_module.evaluation_input_author_run(review_args)
        review = json.loads(review_result.read_text())
        self.assertEqual(review["operation"], "review")
        self.assertEqual(review["decision"], "accept")
        self.assertEqual(review["model"], "fixture-review-model")
        self.assertEqual(review["usage"]["normalized_tokens"], 120)
        self.assertEqual(
            review["input_manifest_sha256"], "sha256:" + "4" * 64
        )
        self.assertEqual(
            review["validation_receipt_sha256"], "sha256:" + "5" * 64
        )
        self.assertEqual(
            review["billing"]["unavailable_reason"],
            "provider_telemetry_unavailable",
        )
        with self.assertRaises(vendor_module.AdapterError) as model_conflict:
            vendor_module.native_model(
                "copilot",
                [
                    {
                        "events": [
                            {
                                "type": "session.start",
                                "data": {"model": "fixture-review-model"},
                            },
                            {
                                "type": "session.model_change",
                                "data": {"model": "different-review-model"},
                            },
                        ]
                    }
                ],
            )
        self.assertEqual(model_conflict.exception.code, "exact-model-unproved")

    def test_executor_doctor_does_not_require_tomllib(self):
        blocked_stdlib = self.case / "blocked-stdlib"
        blocked_stdlib.mkdir()
        (blocked_stdlib / "tomllib.py").write_text(
            "raise ModuleNotFoundError(\"tomllib is unavailable\")\n"
        )
        environment = {
            **self.env,
            "PYTHONPATH": str(blocked_stdlib),
        }
        doctor = self.run_adapter(
            "copilot",
            "review-executor",
            "doctor",
            environment=environment,
        )
        self.assertTrue(doctor["healthy"])
        self.assertTrue(doctor["boundary_ready"])

    def test_copilot_executor_doctor_rejects_missing_authentication(self):
        environment = {
            **self.env,
            "FAKE_COPILOT_AUTH_FAILURE": "1",
        }
        doctor = self.run_adapter(
            "copilot",
            "review-executor",
            "doctor",
            environment=environment,
            check=False,
        )
        self.assertFalse(doctor["ok"])
        self.assertEqual(doctor["error"]["code"], "authentication-required")

    def test_copilot_executor_doctor_rejects_echoed_auth_prompt(self):
        environment = {
            **self.env,
            "FAKE_COPILOT_AUTH_ECHO_ONLY": "1",
        }
        doctor = self.run_adapter(
            "copilot",
            "review-executor",
            "doctor",
            environment=environment,
            check=False,
        )
        self.assertFalse(doctor["ok"])
        self.assertEqual(doctor["error"]["code"], "authentication-required")

    def test_executor_uses_pinned_binary_under_restricted_path(self):
        binary = self.case / "bin/copilot"
        environment = {
            **self.env,
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "DREAMING_COPILOT_BIN": str(self.case / "missing-copilot"),
        }
        doctor = self.run_adapter(
            "copilot",
            "review-executor",
            "doctor",
            environment=environment,
            binary=binary,
        )
        self.assertTrue(doctor["healthy"])
        work = self.case / "executor-environment"
        work.mkdir()
        executor_environment = vendor_module.executor_environment(
            "copilot", work, str(binary)
        )
        self.assertEqual(
            executor_environment["PATH"].split(os.pathsep)[0],
            str(binary.parent),
        )

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
            before = len(
                Path(self.env["FAKE_CLI_LOG"]).read_text().splitlines()
            )
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
            repeated = [
                json.loads(line)
                for line in Path(self.env["FAKE_CLI_LOG"]).read_text().splitlines()[
                    before:
                ]
            ]
            native_mutations = [
                row
                for row in repeated
                if row["args"][:2] in (
                    ["skill", "add"],
                    ["plugin", "install"],
                    ["plugin", "add"],
                )
                or row["args"][:3] == ["plugin", "marketplace", "add"]
            ]
            self.assertEqual(native_mutations, [])
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

    def test_copilot_snapshot_reconcile_restores_or_adopts_exact_state(self):
        paths = module.RuntimePaths(
            state=self.case / "state",
            data=self.case / "data",
            skills=self.case / "skills",
        )
        skill = paths.skills / "learned"
        skill.mkdir(parents=True)
        runtime = module.DreamingRuntime(paths, set())
        (skill / "SKILL.md").write_text(
            "---\nname: learned\ndescription: original\n---\n"
        )
        original, original_id = runtime.materialize_bundle(paths.skills)
        (skill / "SKILL.md").write_text(
            "---\nname: learned\ndescription: replacement\n---\n"
        )
        replacement, replacement_id = runtime.materialize_bundle(paths.skills)
        journal = self.case / "copilot-reconcile-journal.json"
        operation = self.case / "copilot-operation.json"
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
            + ["install", "--bundle", str(original), "--bundle-id", original_id],
            env=self.env,
            check=True,
            stdout=subprocess.PIPE,
        )
        snapshot = json.loads(
            subprocess.run(
                common
                + [
                    "snapshot",
                    "--bundle",
                    str(replacement),
                    "--bundle-id",
                    replacement_id,
                ],
                env=self.env,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout
        )
        operation.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "vendor": "copilot",
                    "prior": snapshot["prior"],
                    "new": snapshot["new"],
                }
            )
        )
        subprocess.run(
            common
            + ["install", "--bundle", str(replacement), "--bundle-id", replacement_id],
            env=self.env,
            check=True,
            stdout=subprocess.PIPE,
        )
        rolled_back = json.loads(
            subprocess.run(
                common
                + [
                    "reconcile",
                    "--operation",
                    str(operation),
                    "--outcome",
                    "rollback",
                ],
                env=self.env,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout
        )
        self.assertEqual(rolled_back["status"], "rolled_back")
        self.assertEqual(json.loads(journal.read_text())["copilot"], snapshot["prior"])
        state_path = Path(self.env["FAKE_CLI_STATE"])
        state = json.loads(state_path.read_text())
        self.assertEqual(state["copilot_bundles"], [str(original)])

        state["copilot_bundles"] = [str(replacement)]
        state_path.write_text(json.dumps(state))
        adopted = json.loads(
            subprocess.run(
                common
                + [
                    "reconcile",
                    "--operation",
                    str(operation),
                    "--outcome",
                    "auto",
                ],
                env=self.env,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout
        )
        self.assertEqual(adopted["status"], "committed")
        self.assertEqual(json.loads(journal.read_text())["copilot"], snapshot["new"])
        state = json.loads(state_path.read_text())
        self.assertEqual(state["copilot_bundles"], [str(replacement)])

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
        invocations = [
            json.loads(line)
            for line in Path(self.env["FAKE_CLI_LOG"]).read_text().splitlines()
        ]
        codex_lists = [
            row["args"]
            for row in invocations
            if row["vendor"] == "codex"
            and row["args"][:2] == ["plugin", "list"]
        ]
        self.assertTrue(codex_lists)
        self.assertTrue(all("--available" not in argv for argv in codex_lists))
        codex_marketplace_lists = [
            row["args"]
            for row in invocations
            if row["vendor"] == "codex"
            and row["args"][:3] == ["plugin", "marketplace", "list"]
        ]
        self.assertEqual(codex_marketplace_lists, [])

    def test_codex_marketplace_identity_is_independent_without_native_listing(self):
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
        journal = self.case / "codex-independent-journal.json"
        common = [
            sys.executable,
            str(adapter),
            "--vendor",
            "codex",
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
        descriptor = json.loads(journal.read_text())["codex"]
        state_path = Path(self.env["FAKE_CLI_STATE"])
        native_before = json.loads(state_path.read_text())
        foreign = str((self.case / "foreign-codex-marketplace").resolve())
        config_path = Path(self.env["CODEX_HOME"]) / "config.toml"
        config_path.write_text(
            "\n".join(
                [
                    f'[marketplaces.{json.dumps(descriptor["name"])}]',
                    'source_type = "local"',
                    f"source = {json.dumps(foreign)}",
                    "",
                ]
            )
        )
        before = len(Path(self.env["FAKE_CLI_LOG"]).read_text().splitlines())
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
        self.assertEqual(json.loads(state_path.read_text()), native_before)
        self.assertIn(foreign, config_path.read_text())
        invocations = [
            json.loads(line)
            for line in Path(self.env["FAKE_CLI_LOG"]).read_text().splitlines()[
                before:
            ]
        ]
        self.assertTrue(
            any(row["args"][:2] == ["plugin", "list"] for row in invocations)
        )
        self.assertFalse(
            any(
                row["args"][:3] == ["plugin", "marketplace", "list"]
                for row in invocations
            )
        )
        self.assertFalse(
            any(
                row["args"][:2] in (["plugin", "remove"], ["plugin", "uninstall"])
                or row["args"][:3] == ["plugin", "marketplace", "remove"]
                for row in invocations
            )
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
        self.assertEqual(data["max_snapshot_bytes"], 100_000)
        self.assertEqual(data["max_events"], 2_000)
        self.assertEqual(data["max_field_bytes"], 64_000)
        for entry in data["sources"].values():
            argv = entry["argv"]
            self.assertEqual(argv[argv.index("--max-events") + 1], "2000")
            self.assertEqual(
                argv[argv.index("--max-snapshot-bytes") + 1],
                "100000",
            )
            self.assertEqual(argv[argv.index("--max-field-bytes") + 1], "64000")
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
                self.assertEqual(configured["policy_version"], 2)
                self.assertEqual(
                    configured["max_autonomous_session_age_days"], 30
                )
                self.assertFalse(configured["allow_autonomous_skill_creation"])
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
