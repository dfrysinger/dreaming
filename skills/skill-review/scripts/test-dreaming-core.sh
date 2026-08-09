#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
# shellcheck source=lib-test-work.sh
source "$SCRIPT_DIR/lib-test-work.sh"
prune_test_work "$TEST_ROOT" "multi-cli-core" 2
export PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/env python3 - "$SCRIPT_DIR" <<'PY'
"""Standalone deterministic tests for the vendor-neutral Dreaming core."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(sys.argv[1]).resolve()
REPO = SCRIPT_DIR.parents[2]
WORK_PARENT = REPO / ".test-work"
WORK_PARENT.mkdir(parents=True, exist_ok=True)
WORK_ROOT = Path(tempfile.mkdtemp(prefix="multi-cli-core.", dir=WORK_PARENT))
RUNTIME_PATH = SCRIPT_DIR / "dreaming-core.py"
FAKE = SCRIPT_DIR / "fake-dreaming-adapter.py"
TEST_ADAPTER_TIMEOUT = 120

spec = importlib.util.spec_from_file_location("dreaming_runtime", RUNTIME_PATH)
runtime_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = runtime_module
spec.loader.exec_module(runtime_module)

DreamingRuntime = runtime_module.DreamingRuntime
ExecutableAdapter = runtime_module.ExecutableAdapter
RuntimeFailure = runtime_module.RuntimeFailure
RuntimePaths = runtime_module.RuntimePaths


def event(source: str, native: str, sequence: int, kind: str) -> dict:
    return {
        "source": source,
        "qualified_session_id": f"{source}:{native}",
        "sequence": sequence,
        "timestamp": sequence,
        "kind": kind,
        "tool_name": None,
        "text": f"{kind}-{sequence}",
        "source_event_id": f"{native}-event-{sequence}",
    }


class RuntimeTest(unittest.TestCase):
    def clean_case(self) -> None:
        if not self.case.exists():
            return
        for path in sorted(self.case.rglob("*"), reverse=True):
            try:
                os.chmod(path, 0o755 if path.is_dir() else 0o644)
            except FileNotFoundError:
                pass
        shutil.rmtree(self.case)

    def setUp(self) -> None:
        self.case = WORK_ROOT / self.id().rsplit(".", 1)[-1]
        self.clean_case()
        self.case.mkdir(parents=True)
        self.clock = 1000
        self.paths = RuntimePaths(
            self.case / "state", self.case / "data", self.case / "skills"
        )

    def tearDown(self) -> None:
        result = self._outcome.result
        failed = any(
            test is self
            for test, _ in [*result.failures, *result.errors]
        )
        if failed:
            print(
                f"DIAGNOSTIC retained failed standalone-core case: {self.case}",
                file=sys.stderr,
            )
        else:
            self.clean_case()

    def write(self, name: str, value: object) -> Path:
        path = self.case / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        return path

    def adapter(self, role: str, adapter_id: str, fixture: Path) -> ExecutableAdapter:
        return ExecutableAdapter(
            [
                sys.executable,
                str(FAKE),
                "--fixture",
                str(fixture),
                "--adapter-id",
                adapter_id,
                "--role",
                role,
            ],
            role,
            timeout=TEST_ADAPTER_TIMEOUT,
            run_timeout=TEST_ADAPTER_TIMEOUT,
        )

    def source_fixture(self, sessions: list[dict], watermark: int = 100) -> Path:
        return self.write(
            "source.json",
            {"source": "fake", "watermark": watermark, "sessions": sessions},
        )

    def session(
        self,
        native: str,
        updated_at: int,
        completion_state: str = "terminal",
        event_count: int = 2,
    ) -> dict:
        events = [
            event(
                "fake",
                native,
                sequence,
                "user_message" if sequence == 1 else "session_end",
            )
            for sequence in range(1, event_count + 1)
        ]
        return {
            "native_session_id": native,
            "repository_scope": "opaque-scope",
            "updated_at": updated_at,
            "completion_state": completion_state,
            "events": events,
        }

    def core(self, routes: set[tuple[str, str]]) -> DreamingRuntime:
        return DreamingRuntime(
            self.paths,
            routes,
            overlap_seconds=10,
            quiet_retry_seconds=5,
            now=lambda: self.clock,
        )

    def test_contract_identity_and_structured_failure(self) -> None:
        fixture = self.source_fixture([self.session("same-id", 10)])
        source = self.adapter("session-source", "fake", fixture)
        response = source.call("inspect", session="fake:same-id")["session"]
        self.assertEqual(response["qualified_session_id"], "fake:same-id")
        other_fixture = self.write(
            "other.json",
            {
                "source": "other",
                "watermark": 10,
                "sessions": [
                    {
                        **self.session("same-id", 10),
                        "events": [
                            event("other", "same-id", 1, "user_message"),
                            event("other", "same-id", 2, "session_end"),
                        ],
                    }
                ],
            },
        )
        other = self.adapter("session-source", "other", other_fixture)
        self.assertNotEqual(
            response["qualified_session_id"],
            other.call("inspect", session="other:same-id")["session"][
                "qualified_session_id"
            ],
        )
        with self.assertRaisesRegex(RuntimeFailure, "session-missing"):
            source.call("inspect", session="fake:missing")

    def test_core_selftest_and_doctor_use_neutral_environment(self) -> None:
        source_fixture = self.source_fixture(
            [self.session("one", 10), self.session("two", 20)]
        )
        executor_fixture = self.write("executor.json", {"mode": "success"})
        publisher_fixture = self.write("publisher.json", {"owned_bundle_ids": []})
        config = self.write(
            "adapters.json",
            {
                "contract_version": 1,
                "max_reviews_per_run": 1,
                "routes": ["fake>fake-executor"],
                "executor_order": ["fake-executor"],
                "sources": {
                    "fake": {
                        "argv": [
                            sys.executable,
                            str(FAKE),
                            "--fixture",
                            str(source_fixture),
                            "--adapter-id",
                            "fake",
                            "--role",
                            "session-source",
                        ],
                        "timeout": TEST_ADAPTER_TIMEOUT,
                        "run_timeout": TEST_ADAPTER_TIMEOUT,
                    }
                },
                "executors": {
                    "fake-executor": {
                        "argv": [
                            sys.executable,
                            str(FAKE),
                            "--fixture",
                            str(executor_fixture),
                            "--adapter-id",
                            "fake-executor",
                            "--role",
                            "review-executor",
                        ],
                        "timeout": TEST_ADAPTER_TIMEOUT,
                        "run_timeout": TEST_ADAPTER_TIMEOUT,
                    }
                },
                "publishers": {
                    "fake-publisher": {
                        "argv": [
                            sys.executable,
                            str(FAKE),
                            "--fixture",
                            str(publisher_fixture),
                            "--adapter-id",
                            "fake-publisher",
                            "--role",
                            "skill-publisher",
                        ],
                        "timeout": TEST_ADAPTER_TIMEOUT,
                        "run_timeout": TEST_ADAPTER_TIMEOUT,
                    }
                },
            },
        )
        environment = {
            **os.environ,
            "DREAMING_ADAPTER_CONFIG": str(config),
            "DREAMING_DATA_DIR": str(self.case / "neutral-data"),
            "DREAMING_STATE_DIR": str(self.case / "neutral-state"),
            "DREAMING_PARENT_RUN_ID": "scheduled-run-123",
        }
        result = subprocess.run(
            [sys.executable, str(RUNTIME_PATH), "doctor"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        report = json.loads(result.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["data_dir"], environment["DREAMING_DATA_DIR"])
        self.assertEqual(report["state_dir"], environment["DREAMING_STATE_DIR"])
        self.assertEqual(len(report["adapters"]), 3)

        result = subprocess.run(
            [sys.executable, str(RUNTIME_PATH), "run"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        report = json.loads(result.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(len(report["reviews"]), 1)
        self.assertEqual(report["reviews"][0]["session_id"], "fake:one")
        self.assertEqual(report["reviews"][0]["executor"], "fake-executor")
        self.assertEqual(report["reviews"][0]["status"], "accepted")
        self.assertEqual(report["reviews"][0]["terminal_route"], "discard")
        self.assertEqual(report["deferred_reviews"], 1)
        ledger = Path(environment["DREAMING_STATE_DIR"]) / "review-ledger.json"
        self.assertEqual(json.loads(ledger.read_text())[0]["session_id"], "fake:one")
        attempts = Path(environment["DREAMING_STATE_DIR"]) / "review-attempts.json"
        self.assertEqual(
            json.loads(attempts.read_text())[0]["parent_run_id"],
            "scheduled-run-123",
        )
        queue = Path(environment["DREAMING_STATE_DIR"]) / "queue.json"
        queued = json.loads(queue.read_text())
        self.assertEqual(
            [item["qualified_session_id"] for item in queued if item["status"] == "queued"],
            ["fake:two"],
        )

        config.unlink()
        result = subprocess.run(
            [sys.executable, str(RUNTIME_PATH), "selftest"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout)["adapters"], [])

    def test_support_file_paths_are_canonical_nonconflicting_files(self) -> None:
        core = self.core(set())
        result = {
            "ok": True,
            "status": "ok",
            "mutation_started": False,
            "completion_sentinel": "DREAMING_REVIEW_COMPLETE",
            "terminal_route": "support_file",
            "summary": "Add reusable support material.",
            "routing_reason": "The procedure needs a durable support file.",
            "artifact": {
                "operation": "support_file",
                "skill_name": "fixture-skill",
                "skill_markdown": (
                    "---\nname: fixture-skill\n"
                    "description: Reusable fixture procedure\n---\n"
                    "# Fixture skill\n"
                ),
                "support_files": [{"path": ".", "content": "invalid"}],
            },
        }
        with self.assertRaisesRegex(RuntimeFailure, "support file is invalid"):
            core._validated_review_result(result)
        result["artifact"]["support_files"] = [
            {"path": "skill.md", "content": "reserved alias"}
        ]
        with self.assertRaisesRegex(RuntimeFailure, "support file is invalid"):
            core._validated_review_result(result)
        result["artifact"]["support_files"] = [
            {"path": "notes", "content": "file"},
            {"path": "notes/example.txt", "content": "nested"},
        ]
        with self.assertRaisesRegex(RuntimeFailure, "support file paths conflict"):
            core._validated_review_result(result)
        result["artifact"]["support_files"] = [
            {"path": "Notes", "content": "file"},
            {"path": "notes/example.txt", "content": "nested"},
        ]
        with self.assertRaisesRegex(RuntimeFailure, "support file paths conflict"):
            core._validated_review_result(result)
        self.assertFalse((self.paths.skills / "fixture-skill").exists())

    def test_support_file_ancestors_are_validated_before_mutation(self) -> None:
        self.paths.skills.mkdir(parents=True)
        subprocess.run(
            ["git", "-C", str(self.paths.skills), "init", "-q"],
            check=True,
        )
        target = self.paths.skills / "fixture-skill"
        target.mkdir()
        skill_markdown = (
            "---\nname: fixture-skill\n"
            "description: Reusable fixture procedure\n---\n"
            "# Original fixture skill\n"
        )
        (target / "SKILL.md").write_text(skill_markdown)
        (target / "notes").write_text("existing file\n")
        subprocess.run(
            ["git", "-C", str(self.paths.skills), "add", "--", "fixture-skill"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.paths.skills),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@localhost",
                "commit",
                "-q",
                "-m",
                "Fixture skill",
            ],
            check=True,
        )
        result = {
            "terminal_route": "support_file",
            "summary": "Add reusable support material.",
            "routing_reason": "The procedure needs a durable support file.",
            "artifact": {
                "operation": "support_file",
                "skill_name": "fixture-skill",
                "skill_markdown": skill_markdown.replace("Original", "Updated"),
                "support_files": [
                    {"path": "notes/example.txt", "content": "nested"}
                ],
            },
        }
        with self.assertRaisesRegex(RuntimeFailure, "artifact-path-invalid"):
            self.core(set())._apply_review_artifact(
                "fake",
                {
                    "qualified_session_id": "fake:fixture",
                    "source_revision": "sha256:fixture",
                },
                "fake-executor",
                result,
            )
        self.assertEqual((target / "SKILL.md").read_text(), skill_markdown)
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(self.paths.skills), "status", "--porcelain"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout,
            "",
        )

    def test_doctor_rejects_source_without_usable_route(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 10)])
        executor_fixture = self.write("executor.json", {"mode": "success"})
        config = self.write(
            "invalid-adapters.json",
            {
                "contract_version": 1,
                "routes": ["fake>missing-executor"],
                "executor_order": ["fake-executor"],
                "sources": {
                    "fake": {
                        "argv": [
                            sys.executable,
                            str(FAKE),
                            "--fixture",
                            str(source_fixture),
                            "--adapter-id",
                            "fake",
                            "--role",
                            "session-source",
                        ]
                    }
                },
                "executors": {
                    "fake-executor": {
                        "argv": [
                            sys.executable,
                            str(FAKE),
                            "--fixture",
                            str(executor_fixture),
                            "--adapter-id",
                            "fake-executor",
                            "--role",
                            "review-executor",
                        ]
                    }
                },
            },
        )
        result = subprocess.run(
            [sys.executable, str(RUNTIME_PATH), "doctor"],
            env={
                **os.environ,
                "DREAMING_ADAPTER_CONFIG": str(config),
                "DREAMING_DATA_DIR": str(self.paths.data),
                "DREAMING_STATE_DIR": str(self.paths.state),
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout)["error"]["code"], "invalid-adapter-config"
        )
        invalid = json.loads(config.read_text())
        invalid["routes"] = ["fake>fake-executor"]
        invalid["overlap_seconds"] = "five"
        config.write_text(json.dumps(invalid))
        result = subprocess.run(
            [sys.executable, str(RUNTIME_PATH), "doctor"],
            env={
                **os.environ,
                "DREAMING_ADAPTER_CONFIG": str(config),
                "DREAMING_DATA_DIR": str(self.paths.data),
                "DREAMING_STATE_DIR": str(self.paths.state),
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout)["error"]["code"], "invalid-adapter-config"
        )

    def test_unavailable_source_does_not_block_healthy_source(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 10)])
        executor_fixture = self.write("executor.json", {"mode": "success"})
        config = self.write(
            "partial-adapters.json",
            {
                "contract_version": 1,
                "max_reviews_per_run": 1,
                "routes": [
                    "fake>fake-executor",
                    "offline>fake-executor",
                ],
                "executor_order": ["fake-executor"],
                "sources": {
                    "fake": {
                        "argv": [
                            sys.executable,
                            str(FAKE),
                            "--fixture",
                            str(source_fixture),
                            "--adapter-id",
                            "fake",
                            "--role",
                            "session-source",
                        ]
                    },
                    "offline": {"argv": [str(self.case / "missing-source")]},
                },
                "executors": {
                    "fake-executor": {
                        "argv": [
                            sys.executable,
                            str(FAKE),
                            "--fixture",
                            str(executor_fixture),
                            "--adapter-id",
                            "fake-executor",
                            "--role",
                            "review-executor",
                        ]
                    }
                },
            },
        )
        self.paths.state.mkdir(parents=True)
        self.paths.queue.write_text(
            json.dumps(
                [
                    {
                        "source": "offline",
                        "qualified_session_id": "offline:old",
                        "source_revision": "sha256:offline",
                        "status": "queued",
                    }
                ]
            )
        )
        result = subprocess.run(
            [sys.executable, str(RUNTIME_PATH), "run"],
            env={
                **os.environ,
                "DREAMING_ADAPTER_CONFIG": str(config),
                "DREAMING_DATA_DIR": str(self.paths.data),
                "DREAMING_STATE_DIR": str(self.paths.state),
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        report = json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(report["reviews"]), 1)
        self.assertEqual(report["reviews"][0]["session_id"], "fake:one")
        self.assertEqual(report["reviews"][0]["executor"], "fake-executor")
        self.assertEqual(report["reviews"][0]["status"], "accepted")
        self.assertEqual(report["errors"][0]["adapter"], "offline")
        self.assertEqual(
            json.loads(self.paths.ledger.read_text())[0]["session_id"], "fake:one"
        )

    def test_fixed_generation_pagination_overlap_and_replay(self) -> None:
        fixture = self.source_fixture(
            [
                self.session("b", 10),
                self.session("a", 10),
                self.session("c", 11),
                self.session("d", 12),
            ],
            watermark=12,
        )
        source = self.adapter("session-source", "fake", fixture)
        core = self.core({("fake", "exec")})
        state = core.discover("fake", source, page_size=2, max_pages=1)
        self.assertEqual(state["generation"]["ceiling"], 12)
        self.assertEqual(state["generation"]["cursor"], "2")
        data = json.loads(fixture.read_text())
        data["watermark"] = 20
        data["sessions"].append(self.session("new", 20))
        fixture.write_text(json.dumps(data))
        state = core.discover("fake", source, page_size=2, max_pages=8)
        self.assertEqual(state["settled_watermark"], 12)
        queued = json.loads(self.paths.queue.read_text())
        self.assertEqual(
            [item["native_session_id"] for item in queued if item["status"] == "queued"],
            ["a", "b", "c", "d"],
        )
        state = core.discover("fake", source, page_size=10, max_pages=1)
        self.assertIsNone(state["generation"])
        queued = json.loads(self.paths.queue.read_text())
        self.assertEqual(
            [item["native_session_id"] for item in queued if item["status"] == "queued"],
            ["a", "b", "c", "d", "new"],
        )

        def crash(_: dict) -> None:
            raise RuntimeError("simulated crash before cursor commit")

        self.paths.discovery.unlink()
        self.paths.queue.unlink()
        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            core.discover(
                "fake", source, page_size=2, max_pages=1, before_cursor_commit=crash
            )
        self.assertEqual(
            json.loads(self.paths.discovery.read_text())["fake"]["generation"]["cursor"],
            "",
        )
        core.discover("fake", source, page_size=2, max_pages=1)
        self.assertEqual(len(json.loads(self.paths.queue.read_text())), 2)

    def test_completion_unsettled_and_quiet_without_new_event(self) -> None:
        fixture = self.source_fixture(
            [self.session("active", 10, completion_state="active")], watermark=10
        )
        source = self.adapter("session-source", "fake", fixture)
        core = self.core({("fake", "exec")})
        core.discover("fake", source, page_size=10)
        unsettled = json.loads(self.paths.unsettled.read_text())
        self.assertIn("fake:active", unsettled)
        self.assertFalse(self.paths.queue.exists())
        data = json.loads(fixture.read_text())
        data["sessions"][0]["completion_state"] = "quiet"
        fixture.write_text(json.dumps(data))
        self.clock += 5
        self.assertEqual(core.revisit_unsettled("fake", source), ["queued"])
        self.assertEqual(json.loads(self.paths.unsettled.read_text()), {})
        self.assertEqual(
            json.loads(self.paths.queue.read_text())[0]["completion_state"], "quiet"
        )

    def test_deleted_unsettled_session_is_retired(self) -> None:
        fixture = self.source_fixture(
            [self.session("active", 10, completion_state="active")], watermark=10
        )
        source = self.adapter("session-source", "fake", fixture)
        core = self.core({("fake", "exec")})
        core.discover("fake", source, page_size=10)
        data = json.loads(fixture.read_text())
        data["sessions"] = []
        fixture.write_text(json.dumps(data))
        self.clock += 5
        self.assertEqual(core.revisit_unsettled("fake", source), ["deleted"])
        self.assertEqual(json.loads(self.paths.unsettled.read_text()), {})
        self.assertFalse(self.paths.queue.exists())

    def test_unsettled_failure_does_not_resurrect_prior_admission(self) -> None:
        fixture = self.source_fixture(
            [
                self.session("a", 10, completion_state="active"),
                self.session("b", 11, completion_state="active"),
            ],
            watermark=11,
        )
        source = self.adapter("session-source", "fake", fixture)
        core = self.core({("fake", "exec")})
        core.discover("fake", source, page_size=10)
        data = json.loads(fixture.read_text())
        data["sessions"][0]["completion_state"] = "terminal"
        data["sessions"] = [data["sessions"][0]]
        fixture.write_text(json.dumps(data))
        self.clock += 5
        self.assertEqual(
            core.revisit_unsettled("fake", source), ["queued", "deleted"]
        )
        self.assertEqual(json.loads(self.paths.unsettled.read_text()), {})
        self.assertEqual(
            json.loads(self.paths.queue.read_text())[0]["qualified_session_id"],
            "fake:a",
        )

    def test_route_denial_happens_before_render(self) -> None:
        fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", fixture)
        core = self.core(set())
        with self.assertRaisesRegex(RuntimeFailure, "route-denied"):
            core.render_snapshot("fake", source, "exec", "fake:one")
        # If render had run, malformed events would have raised a different error.
        data = json.loads(fixture.read_text())
        data["sessions"][0]["events"] = [{"secret": "must-not-be-read"}]
        fixture.write_text(json.dumps(data))
        with self.assertRaisesRegex(RuntimeFailure, "route-denied"):
            core.render_snapshot("fake", source, "exec", "fake:one")

    def test_snapshot_is_deterministic_and_immutable(self) -> None:
        fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", fixture)
        core = self.core({("fake", "exec")})
        first, _ = core.render_snapshot("fake", source, "exec", "fake:one")
        second, _ = core.render_snapshot("fake", source, "exec", "fake:one")
        self.assertEqual(first, second)
        self.assertEqual(first.stat().st_mode & 0o222, 0)
        snapshot = json.loads(first.read_text())
        self.assertEqual(snapshot["route"]["policy_version"], 1)

    def test_source_render_that_breaks_bounds_fails_closed(self) -> None:
        fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", fixture)
        core = DreamingRuntime(
            self.paths,
            {("fake", "exec")},
            max_field_bytes=4,
            now=lambda: self.clock,
        )
        with self.assertRaisesRegex(RuntimeFailure, "source-boundary-violation"):
            core.render_snapshot("fake", source, "exec", "fake:one")
        self.assertFalse(self.paths.snapshots.exists())

    def test_executor_fallback_only_before_mutation(self) -> None:
        fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", fixture)
        fail_fixture = self.write("fail.json", {"mode": "fail-before-mutation"})
        success_fixture = self.write("success.json", {"mode": "success"})
        first = self.adapter("review-executor", "first", fail_fixture)
        second = self.adapter("review-executor", "second", success_fixture)
        core = self.core({("fake", "first"), ("fake", "second")})
        result = core.review(
            "fake", source, "fake:one", [("first", first), ("second", second)]
        )
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["executor"], "second")
        self.assertEqual(result["terminal_route"], "discard")
        self.assertFalse(result["artifact_mutated"])
        self.assertEqual(
            core.review(
                "fake", source, "fake:one", [("first", first), ("second", second)]
            ),
            {"status": "already-reviewed"},
        )

        self.paths.ledger.unlink()
        after_fixture = self.write(
            "after.json",
            {
                "mode": "success",
                "terminal_route": "skill",
                "summary": "Reusable fixture procedure",
                "routing_reason": "The procedure has ordered reusable steps",
                "artifact": {
                    "operation": "create",
                    "skill_name": "fixture-skill",
                    "skill_markdown": (
                        "---\nname: fixture-skill\n"
                        "description: Reusable fixture procedure\n---\n"
                        "# Fixture skill\n"
                    ),
                    "support_files": [],
                },
            },
        )
        after = self.adapter("review-executor", "after", after_fixture)
        core = self.core({("fake", "after"), ("fake", "second")})
        with self.assertRaisesRegex(RuntimeFailure, "mutation-recovery-required"):
            core.review(
                "fake", source, "fake:one", [("after", after), ("second", second)]
            )
        with self.assertRaisesRegex(RuntimeFailure, "mutation-recovery-required"):
            core.review(
                "fake", source, "fake:one", [("second", second)]
            )
        data = json.loads(fixture.read_text())
        data["sessions"][0]["events"].append(event("fake", "one", 3, "session_end"))
        data["sessions"][0]["updated_at"] = 11
        fixture.write_text(json.dumps(data))
        latest = source.call("inspect", session="fake:one")["session"]
        core._queue_session(latest)
        self.assertEqual(
            json.loads(self.paths.queue.read_text())[-1]["status"],
            "recovery-required",
        )
        with self.assertRaisesRegex(RuntimeFailure, "mutation-recovery-required"):
            core.review(
                "fake", source, "fake:one", [("second", second)]
            )

    def test_missing_pre_mutation_result_remains_retryable(self) -> None:
        fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", fixture)
        missing_script = self.case / "missing-result.py"
        missing_script.write_text(
            """#!/usr/bin/env python3
import json, sys
if sys.argv[1] == "contract":
    print(json.dumps({"ok": True, "protocol": "dreaming.review-executor",
        "version": 1, "adapter_id": "missing", "capabilities":
        ["source-blind", "mutation-fence", "completion-sentinel"]}))
elif sys.argv[1] == "doctor":
    print(json.dumps({"ok": True, "healthy": True, "boundary_ready": True}))
elif sys.argv[1] == "run":
    print(json.dumps({"ok": True, "status": "ok", "mutation_started": False,
        "completion_sentinel": "DREAMING_REVIEW_COMPLETE",
        "terminal_route": "discard", "summary": "nothing durable",
        "routing_reason": "fixture discard", "artifact": None}))
"""
        )
        os.chmod(missing_script, 0o755)
        missing = ExecutableAdapter(
            [sys.executable, str(missing_script)],
            "review-executor",
            timeout=TEST_ADAPTER_TIMEOUT,
            run_timeout=TEST_ADAPTER_TIMEOUT,
        )
        success_fixture = self.write("success.json", {"mode": "success"})
        success = self.adapter("review-executor", "success", success_fixture)
        core = self.core({("fake", "missing"), ("fake", "success")})
        accepted = core.review(
            "fake",
            source,
            "fake:one",
            [("missing", missing), ("success", success)],
        )
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["executor"], "success")
        self.assertEqual(json.loads(self.paths.transactions.read_text()), {})

    def test_skill_result_commits_artifact_and_source_routing_evidence(self) -> None:
        fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", fixture)
        self.paths.skills.mkdir(parents=True)
        subprocess.run(
            ["git", "-C", str(self.paths.skills), "init", "-q"],
            check=True,
        )
        executor_fixture = self.write(
            "skill-result.json",
            {
                "mode": "success",
                "terminal_route": "skill",
                "summary": "A reusable fixture procedure was demonstrated",
                "routing_reason": "The steps are ordered and likely to recur",
                "artifact": {
                    "operation": "create",
                    "skill_name": "fixture-procedure",
                    "skill_markdown": (
                        "---\nname: fixture-procedure\n"
                        "description: Run the reusable fixture procedure\n---\n"
                        "# Fixture procedure\n\n1. Run the fixture.\n"
                    ),
                    "support_files": [],
                },
            },
        )
        executor = self.adapter("review-executor", "exec", executor_fixture)
        result = self.core({("fake", "exec")}).review(
            "fake", source, "fake:one", [("exec", executor)]
        )
        self.assertTrue(result["artifact_mutated"])
        self.assertTrue(result["artifact_commit"])
        skill = self.paths.skills / "fixture-procedure"
        self.assertTrue((skill / "SKILL.md").is_file())
        self.assertTrue((skill / ".agent-created").is_file())
        envelope = json.loads((skill / ".agent-created.json").read_text())
        evidence = envelope["evidence"][0]
        self.assertEqual(evidence["source"], "fake")
        self.assertEqual(evidence["review_executor"], "exec")
        self.assertEqual(evidence["transfer_route"], "fake>exec")
        self.assertEqual(evidence["destination"], "skill")
        self.assertEqual(len(evidence["draft_reviews"]), 2)
        self.assertEqual(
            [review["decision"] for review in evidence["draft_reviews"]],
            ["approve", "approve"],
        )
        ledger = json.loads(self.paths.ledger.read_text())[0]
        self.assertEqual(ledger["artifact_commit"], result["artifact_commit"])
        self.assertEqual(ledger["transfer_route"], "fake>exec")
        self.assertEqual(len(ledger["draft_reviews"]), 2)
        durable = json.loads(self.paths.review_evidence.read_text())[0]
        self.assertEqual(durable["source_revision"], ledger["source_revision"])
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(self.paths.skills), "status", "--porcelain"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout,
            "",
        )

    def test_rejected_draft_never_mutates_or_records_review(self) -> None:
        fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", fixture)
        self.paths.skills.mkdir(parents=True)
        subprocess.run(["git", "-C", str(self.paths.skills), "init", "-q"], check=True)
        executor_fixture = self.write(
            "rejected-skill.json",
            {
                "mode": "success",
                "terminal_route": "skill",
                "draft_review_decision": "reject",
                "artifact": {
                    "operation": "create",
                    "skill_name": "rejected-procedure",
                    "skill_markdown": (
                        "---\nname: rejected-procedure\n"
                        "description: Rejected fixture procedure\n---\n"
                        "# Rejected\n"
                    ),
                    "support_files": [],
                },
            },
        )
        executor = self.adapter("review-executor", "exec", executor_fixture)
        with self.assertRaisesRegex(RuntimeFailure, "draft-review-rejected"):
            self.core({("fake", "exec")}).review(
                "fake", source, "fake:one", [("exec", executor)]
            )
        self.assertFalse((self.paths.skills / "rejected-procedure").exists())
        self.assertFalse(self.paths.ledger.exists())
        self.assertFalse(self.paths.review_evidence.exists())
        self.assertEqual(json.loads(self.paths.transactions.read_text()), {})

    def test_executor_run_uses_configured_long_timeout(self) -> None:
        script = self.case / "slow-executor.py"
        script.write_text(
            """#!/usr/bin/env python3
import json, pathlib, sys, time
command = sys.argv[1]
if command == "contract":
    print(json.dumps({"ok": True, "protocol": "dreaming.review-executor",
        "version": 1, "adapter_id": "slow", "capabilities":
        ["source-blind", "mutation-fence", "completion-sentinel"]}))
elif command == "doctor":
    print(json.dumps({"ok": True, "healthy": True, "boundary_ready": True}))
elif command == "run":
    time.sleep(1.2)
    result = {"status":"ok","mutation_started":False,
        "completion_sentinel":"DREAMING_REVIEW_COMPLETE",
        "terminal_route":"discard","summary":"slow fixture",
        "routing_reason":"timeout regression","artifact":None}
    pathlib.Path(sys.argv[sys.argv.index("--result") + 1]).write_text(json.dumps(result))
    print(json.dumps({"ok": True, **result}))
"""
        )
        adapter = ExecutableAdapter(
            [sys.executable, str(script)],
            "review-executor",
            timeout=1,
            run_timeout=10,
        )
        result_path = self.case / "slow-result.json"
        response = adapter.call(
            "run",
            snapshot=self.write("slow-snapshot.json", {"events": []}),
            result=result_path,
        )
        self.assertEqual(response["terminal_route"], "discard")

    def test_completed_transaction_self_heals_before_session_fence(self) -> None:
        fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", fixture)
        executor_fixture = self.write("success.json", {"mode": "success"})
        executor = self.adapter("review-executor", "exec", executor_fixture)
        core = self.core({("fake", "exec")})
        accepted = core.review("fake", source, "fake:one", [("exec", executor)])
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["executor"], "exec")
        reviewed = json.loads(self.paths.ledger.read_text())[0]
        transaction = {
            "status": "mutation-started",
            "session_id": "fake:one",
            "source_revision": reviewed["source_revision"],
            "executor": "exec",
            "mutation_started": True,
        }
        core._write_transaction(
            "fake:one", reviewed["source_revision"], transaction
        )
        self.assertEqual(
            core.review("fake", source, "fake:one", [("exec", executor)]),
            {"status": "already-reviewed"},
        )
        self.assertEqual(json.loads(self.paths.transactions.read_text()), {})

        core._write_transaction(
            "fake:one", reviewed["source_revision"], transaction
        )
        data = json.loads(fixture.read_text())
        data["sessions"][0]["events"].append(event("fake", "one", 3, "session_end"))
        data["sessions"][0]["updated_at"] = 11
        fixture.write_text(json.dumps(data))
        core._queue_session(source.call("inspect", session="fake:one")["session"])
        self.assertEqual(json.loads(self.paths.queue.read_text())[-1]["status"], "queued")
        self.assertEqual(json.loads(self.paths.transactions.read_text()), {})

    def test_executor_cannot_claim_core_owned_mutation(self) -> None:
        fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", fixture)
        executor_script = self.case / "nonzero-executor.py"
        executor_script.write_text(
            """#!/usr/bin/env python3
import json, pathlib, sys
if sys.argv[1] == "contract":
    print(json.dumps({"ok": True, "protocol": "dreaming.review-executor",
        "version": 1, "adapter_id": "nonzero", "capabilities":
        ["source-blind", "mutation-fence", "completion-sentinel"]}))
elif sys.argv[1] == "doctor":
    print(json.dumps({"ok": True, "healthy": True, "boundary_ready": True}))
elif sys.argv[1] == "run":
    result = pathlib.Path(sys.argv[sys.argv.index("--result") + 1])
    result.write_text(json.dumps({"status": "failed", "mutation_started": True,
        "completion_sentinel": None, "terminal_route": None}))
    print(json.dumps({"ok": False, "error": {"code": "crashed", "message": "after mutation"}}))
    raise SystemExit(2)
"""
        )
        os.chmod(executor_script, 0o755)
        nonzero = ExecutableAdapter(
            [sys.executable, str(executor_script)],
            "review-executor",
            timeout=TEST_ADAPTER_TIMEOUT,
            run_timeout=TEST_ADAPTER_TIMEOUT,
        )
        success_fixture = self.write("success.json", {"mode": "success"})
        success = self.adapter("review-executor", "success", success_fixture)
        core = self.core({("fake", "nonzero"), ("fake", "success")})
        result = core.review(
            "fake",
            source,
            "fake:one",
            [("nonzero", nonzero), ("success", success)],
        )
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["executor"], "success")

    def test_stale_result_rejected_and_latest_revision_requeued(self) -> None:
        fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", fixture)
        executor_fixture = self.write(
            "stale.json", {"mode": "success", "mutate_source_fixture": str(fixture)}
        )
        executor = self.adapter("review-executor", "exec", executor_fixture)
        core = self.core({("fake", "exec")})
        before = source.call("inspect", session="fake:one")["session"]["source_revision"]
        result = core.review("fake", source, "fake:one", [("exec", executor)])
        self.assertEqual(result["status"], "stale")
        self.assertNotEqual(result["queued_revision"], before)
        self.assertFalse(self.paths.ledger.exists())
        queue = json.loads(self.paths.queue.read_text())
        self.assertEqual(queue[-1]["source_revision"], result["queued_revision"])

        shutil.rmtree(self.paths.state)
        mutating_fixture = self.write(
            "stale-mutation.json",
            {
                "mode": "success",
                "mutation_started": True,
                "mutate_source_fixture": str(fixture),
            },
        )
        mutating = self.adapter("review-executor", "mutating", mutating_fixture)
        core = self.core({("fake", "mutating")})
        stale = core.review("fake", source, "fake:one", [("mutating", mutating)])
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(json.loads(self.paths.transactions.read_text()), {})

    def test_queued_revision_is_revalidated_before_executor_start(self) -> None:
        fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", fixture)
        executor_fixture = self.write("executor.json", {"mode": "success"})
        executor = self.adapter("review-executor", "exec", executor_fixture)
        core = self.core({("fake", "exec")})
        queued = source.call("inspect", session="fake:one")["session"]
        core._queue_session(queued)
        data = json.loads(fixture.read_text())
        data["sessions"][0]["events"].append(event("fake", "one", 3, "session_end"))
        data["sessions"][0]["updated_at"] = 11
        data["watermark"] = 11
        fixture.write_text(json.dumps(data))
        result = core.review(
            "fake",
            source,
            "fake:one",
            [("exec", executor)],
            expected_revision=queued["source_revision"],
        )
        self.assertEqual(result["status"], "stale-before-review")
        queue = json.loads(self.paths.queue.read_text())
        self.assertEqual(queue[0]["status"], "superseded")
        self.assertEqual(queue[-1]["status"], "queued")
        self.assertFalse(self.paths.ledger.exists())

    def test_halt_after_discovery_prevents_review(self) -> None:
        halt = self.paths.state / "skill-review" / "disable-daemon"
        source_fixture = self.source_fixture([self.session("one", 10)])
        source_data = json.loads(source_fixture.read_text())
        source_data["touch_on_list"] = str(halt)
        source_fixture.write_text(json.dumps(source_data))
        executor_fixture = self.write("executor.json", {"mode": "success"})
        config = self.write(
            "adapters.json",
            {
                "contract_version": 1,
                "routes": ["fake>exec"],
                "executor_order": ["exec"],
                "sources": {
                    "fake": {
                        "argv": [
                            sys.executable,
                            str(FAKE),
                            "--fixture",
                            str(source_fixture),
                            "--adapter-id",
                            "fake",
                            "--role",
                            "session-source",
                        ]
                    }
                },
                "executors": {
                    "exec": {
                        "argv": [
                            sys.executable,
                            str(FAKE),
                            "--fixture",
                            str(executor_fixture),
                            "--adapter-id",
                            "exec",
                            "--role",
                            "review-executor",
                        ]
                    }
                },
            },
        )
        result = subprocess.run(
            [sys.executable, str(RUNTIME_PATH), "run"],
            env={
                **os.environ,
                "DREAMING_ADAPTER_CONFIG": str(config),
                "DREAMING_DATA_DIR": str(self.paths.data),
                "DREAMING_STATE_DIR": str(self.paths.state),
                "DREAMING_SKILLS_ROOT": str(self.paths.skills),
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        report = json.loads(result.stdout)
        self.assertTrue(report["halted"])
        self.assertFalse(self.paths.ledger.exists())
        self.assertEqual(json.loads(self.paths.queue.read_text())[0]["status"], "queued")

    def test_lazy_legacy_migration_baseline_changed_and_ambiguous(self) -> None:
        record = self.session("legacy", 10)
        record["events"] = [
            event("copilot", "legacy", 1, "user_message"),
            event("copilot", "legacy", 2, "session_end"),
        ]
        fixture = self.write(
            "source.json",
            {"source": "copilot", "watermark": 10, "sessions": [record]},
        )
        source = self.adapter("session-source", "copilot", fixture)
        current = source.call("inspect", session="copilot:legacy")["session"]

        core = self.core(set())
        self.write(
            "state/review-ledger.json",
            [{"session_id": "legacy", "reviewed_at": 20, "kept": "yes"}],
        )
        self.assertEqual(core.migrate_legacy("legacy", current), "baseline-seeded")
        migrated = json.loads(self.paths.ledger.read_text())[0]
        self.assertEqual(migrated["session_id"], "copilot:legacy")
        self.assertEqual(migrated["kept"], "yes")
        self.assertEqual(migrated["source_revision"], "legacy-reviewed")
        self.assertFalse(self.paths.queue.exists())

        shutil.rmtree(self.paths.state)
        self.write(
            "state/review-ledger.json",
            [{"session_id": "legacy", "reviewed_at": 5, "kept": "yes"}],
        )
        self.assertEqual(
            core.migrate_legacy("legacy", current), "queued-current-revision"
        )
        self.assertEqual(len(json.loads(self.paths.queue.read_text())), 1)
        self.assertEqual(core.migrate_legacy("legacy", current), "absent")
        self.assertEqual(len(json.loads(self.paths.queue.read_text())), 1)

        shutil.rmtree(self.paths.state)
        self.write(
            "state/review-ledger.json",
            [{"session_id": "legacy", "reviewed_at": "not-comparable"}],
        )
        self.assertEqual(core.migrate_legacy("legacy", current), "ambiguous-hold")
        held = json.loads(self.paths.ledger.read_text())[0]
        self.assertEqual(held["migration"]["status"], "ambiguous-hold")
        self.assertFalse(self.paths.queue.exists())

    def test_legacy_jsonl_import_is_idempotent_and_malformed_fails_closed(self) -> None:
        legacy = self.case / "legacy.jsonl"
        legacy.write_text(
            json.dumps(
                {
                    "session_id": "legacy",
                    "mode": "dispatch",
                    "reviewed_at": 20,
                }
            )
            + "\n"
        )
        core = self.core(set())
        self.assertEqual(core.import_legacy_ledger(legacy), 1)
        self.assertEqual(core.import_legacy_ledger(legacy), 0)
        imported = json.loads(self.paths.ledger.read_text())
        self.assertEqual(imported[0]["legacy_import"]["source"], "copilot")
        legacy.write_text("{not-json}\n")
        with self.assertRaisesRegex(RuntimeFailure, "legacy-ledger-malformed"):
            core.import_legacy_ledger(legacy)

    def test_publisher_contract_and_content_addressed_bundle_proof(self) -> None:
        skill = self.paths.skills / "learned-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: learned-skill\n---\n")
        subprocess.run(["git", "-C", str(self.paths.skills), "init", "-q"], check=True)
        (self.paths.skills / ".private-state").write_text("not publishable")
        publisher_fixture = self.write("publisher.json", {"owned_bundle_ids": []})
        publisher = self.adapter(
            "skill-publisher", "fake-publisher", publisher_fixture
        )
        core = self.core(set())
        first = core.publish(publisher, self.paths.skills)
        second = core.publish(publisher, self.paths.skills)
        self.assertEqual(first["bundle_id"], second["bundle_id"])
        bundle = Path(first["bundle"])
        self.assertEqual(bundle.stat().st_mode & 0o222, 0)
        manifest = json.loads(
            (bundle / "dreaming-bundle-manifest.json").read_text()
        )
        self.assertEqual(
            [item["path"] for item in manifest["files"]],
            [
                "learned-skill/SKILL.md",
                ".agents/plugins/marketplace.json",
                ".claude-plugin/marketplace.json",
                ".claude-plugin/plugin.json",
                ".codex-plugin/plugin.json",
            ],
        )
        state = json.loads(publisher_fixture.read_text())
        self.assertEqual(state["owned_bundle_ids"], [first["bundle_id"]])

        os.chmod(bundle / "learned-skill" / "SKILL.md", 0o644)
        (bundle / "learned-skill" / "SKILL.md").write_text("tampered")
        os.chmod(bundle / "learned-skill" / "SKILL.md", 0o444)
        with self.assertRaisesRegex(RuntimeFailure, "bundle-inventory-mismatch"):
            core.verify_bundle(bundle, first["bundle_id"])

    def test_publisher_rejects_orchestration_skill_leak(self) -> None:
        leaked = self.paths.skills / "skill-review"
        leaked.mkdir(parents=True)
        (leaked / "SKILL.md").write_text("forbidden")
        publisher_fixture = self.write("publisher.json", {"owned_bundle_ids": []})
        publisher = self.adapter(
            "skill-publisher", "fake-publisher", publisher_fixture
        )
        with self.assertRaisesRegex(RuntimeFailure, "orchestration-skill-leak"):
            self.core(set()).publish(publisher, self.paths.skills)


if __name__ == "__main__":
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        program = unittest.main(argv=[sys.argv[0]], verbosity=2, exit=False)
    except BaseException:
        print(
            f"DIAGNOSTIC retained interrupted standalone-core evidence: {WORK_ROOT}",
            file=sys.stderr,
        )
        raise
    if program.result.wasSuccessful():
        shutil.rmtree(WORK_ROOT, ignore_errors=True)
    else:
        print(f"DIAGNOSTIC retained failed standalone-core evidence: {WORK_ROOT}", file=sys.stderr)
        raise SystemExit(1)
PY
