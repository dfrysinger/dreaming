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

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone
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
            if path.is_symlink():
                continue
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

    def evaluation_input_content(
        self,
    ) -> tuple[dict[str, object], str, dict[str, object], dict[str, Path]]:
        capability_id = runtime_module.digest({"capability": "fixture"})
        owner, entries, file_sets = self.evaluation_input_content_set(
            [capability_id]
        )
        return owner, capability_id, entries[capability_id], file_sets[capability_id]

    def evaluation_input_content_set(
        self, capability_ids: list[str]
    ) -> tuple[
        dict[str, object],
        dict[str, dict[str, object]],
        dict[str, dict[str, Path]],
    ]:
        root = self.paths.state / "evaluation-input-owner"
        entries: dict[str, dict[str, object]] = {}
        file_sets: dict[str, dict[str, Path]] = {}
        for position, capability_id in enumerate(capability_ids):
            capability_dir = root / f"capability-{position:03d}"
            capability_dir.mkdir(parents=True)
            files: dict[str, Path] = {}
            for role in ("suite", "policy", "compilation", "routing", "catalog"):
                path = capability_dir / f"{role}.json"
                path.write_bytes(runtime_module.canonical({"role": role}))
                os.chmod(path, 0o600)
                files[role] = path
            harness = capability_dir / "skill-evaluation-harness.py"
            harness.write_bytes(
                RUNTIME_PATH.with_name("skill-evaluation-harness.py").read_bytes()
            )
            os.chmod(harness, 0o700)
            files["harness"] = harness
            support_files = []
            for role, relative in (
                ("fixture", "fixtures/synthetic.json"),
                ("grader", "graders/contracts.json"),
            ):
                path = capability_dir / relative
                path.parent.mkdir(mode=0o700)
                path.write_bytes(runtime_module.canonical({"role": role}))
                os.chmod(path, 0o600)
                support_files.append((role, path))
            manifest = {
                "schema_version": 1,
                "kind": "evaluation_input_capability_manifest",
                "capability_id": capability_id,
                "files": [
                    {
                        "role": role,
                        "path": path.name,
                        "size": path.stat().st_size,
                        "media_type": runtime_module.EVALUATION_INPUT_CONTENT_ROLES[
                            role
                        ],
                        "sha256": "sha256:"
                        + hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                    for role, path in sorted(files.items())
                ]
                + [
                    {
                        "role": role,
                        "path": path.relative_to(capability_dir).as_posix(),
                        "size": path.stat().st_size,
                        "media_type": runtime_module.EVALUATION_INPUT_SUPPORT_ROLES[
                            role
                        ],
                        "sha256": "sha256:"
                        + hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                    for role, path in support_files
                ],
            }
            manifest_path = (
                capability_dir / runtime_module.EVALUATION_INPUT_MANIFEST_NAME
            )
            manifest_path.write_bytes(runtime_module.canonical(manifest))
            os.chmod(manifest_path, 0o600)
            entries[capability_id] = {
                "capability_id": capability_id,
                "directory": capability_dir.name,
                "manifest_sha256": "sha256:"
                + hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            }
            file_sets[capability_id] = files
            os.chmod(capability_dir, 0o700)
        index = {
            "schema_version": 1,
            "kind": "evaluation_input_content_root_index",
            "capabilities": [
                entries[capability_id] for capability_id in capability_ids
            ],
        }
        index["record_sha256"] = runtime_module.digest(index)
        index_path = root / runtime_module.EVALUATION_INPUT_INDEX_NAME
        index_path.write_bytes(runtime_module.canonical(index))
        os.chmod(index_path, 0o600)
        os.chmod(root, 0o700)
        return {"content_root": str(root)}, entries, file_sets

    def adapter(self, role: str, adapter_id: str, fixture: Path) -> ExecutableAdapter:
        return ExecutableAdapter(
            [
                "/usr/bin/python3",
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
            allow_autonomous_skill_creation=True,
            now=lambda: self.clock,
        )

    def test_estate_census_receipt_is_content_addressed(self) -> None:
        core = self.core({("fake", "executor")})
        snapshot = {
            "schema_version": 1,
            "host_id": "macbook",
            "collected_at": "2026-08-17T18:00:00+00:00",
            "scope": {"complete": True},
            "totals": {"physical_instances": 2, "effective_instances": 1},
        }
        census = {
            **snapshot,
            "snapshot_sha256": runtime_module.digest(snapshot),
        }
        receiver = {
            "receiver_id": "fixture",
            "receiver_sha256": "a" * 64,
            "collector_sha256": "b" * 64,
        }
        first = core.record_estate_census(census, receiver)
        second = core.record_estate_census(census, receiver)
        self.assertEqual(first, second)
        current = json.loads(self.paths.estate_current.read_text())
        self.assertEqual(current["receipt_sha256"], first["receipt_sha256"])
        receipt = (
            self.paths.estate_receipts
            / f"{first['receipt_sha256'].removeprefix('sha256:')}.json"
        )
        self.assertTrue(receipt.is_file())

    def test_estate_usage_receipt_is_separate_and_census_bound(self) -> None:
        core = self.core({("fake", "executor")})
        census_snapshot = {
            "schema_version": 1,
            "host_id": "macbook",
            "collected_at": "2026-08-17T18:00:00+00:00",
            "scope": {"complete": True},
        }
        census = {
            **census_snapshot,
            "snapshot_sha256": runtime_module.digest(census_snapshot),
        }
        usage_snapshot = {
            "schema_version": 1,
            "host_id": census["host_id"],
            "collected_at": census["collected_at"],
            "census_snapshot_sha256": census["snapshot_sha256"],
            "coverage": {"complete": True},
            "canonical_usage": [],
            "unattributed": [],
        }
        usage = {
            **usage_snapshot,
            "snapshot_sha256": runtime_module.digest(usage_snapshot),
        }
        receiver = {
            "receiver_id": "fixture",
            "receiver_sha256": "a" * 64,
            "collector_sha256": "b" * 64,
        }
        core.record_estate_census(census, receiver)
        first = core.record_estate_usage(usage, receiver, census)
        second = core.record_estate_usage(usage, receiver, census)
        self.assertEqual(first, second)
        current = json.loads(self.paths.estate_usage_current.read_text())
        self.assertEqual(
            current["census_snapshot_sha256"], census["snapshot_sha256"]
        )
        self.assertTrue(self.paths.estate_current.is_file())
        altered = dict(usage)
        altered["host_id"] = "other-host"
        altered_snapshot = {
            key: value for key, value in altered.items() if key != "snapshot_sha256"
        }
        altered["snapshot_sha256"] = runtime_module.digest(altered_snapshot)
        with self.assertRaisesRegex(RuntimeFailure, "census binding"):
            core.record_estate_usage(altered, receiver, census)

        invalid_census = dict(census)
        invalid_census["collected_at"] = "not-a-time"
        invalid_census_snapshot = {
            key: value
            for key, value in invalid_census.items()
            if key != "snapshot_sha256"
        }
        invalid_census["snapshot_sha256"] = runtime_module.digest(
            invalid_census_snapshot
        )
        with self.assertRaisesRegex(RuntimeFailure, "collection time"):
            core.record_estate_census(invalid_census, receiver)
        self.assertEqual(
            json.loads(self.paths.estate_current.read_text())["snapshot_sha256"],
            census["snapshot_sha256"],
        )

        invalid_coverage = dict(usage)
        invalid_coverage["coverage"] = None
        invalid_coverage_snapshot = {
            key: value
            for key, value in invalid_coverage.items()
            if key != "snapshot_sha256"
        }
        invalid_coverage["snapshot_sha256"] = runtime_module.digest(
            invalid_coverage_snapshot
        )
        with self.assertRaisesRegex(RuntimeFailure, "coverage"):
            core.record_estate_usage(invalid_coverage, receiver, census)

        newer_census_snapshot = {
            **census_snapshot,
            "collected_at": "2026-08-17T19:00:00+00:00",
        }
        newer_census = {
            **newer_census_snapshot,
            "snapshot_sha256": runtime_module.digest(newer_census_snapshot),
        }
        newer_usage_snapshot = {
            **usage_snapshot,
            "collected_at": newer_census["collected_at"],
            "census_snapshot_sha256": newer_census["snapshot_sha256"],
        }
        newer_usage = {
            **newer_usage_snapshot,
            "snapshot_sha256": runtime_module.digest(newer_usage_snapshot),
        }
        self.assertEqual(
            core.record_estate_census(newer_census, receiver)["status"],
            "recorded",
        )
        self.assertEqual(
            core.record_estate_usage(newer_usage, receiver, newer_census)["status"],
            "recorded",
        )
        self.assertEqual(
            core.record_estate_census(census, receiver)["status"],
            "superseded",
        )
        self.assertEqual(
            core.record_estate_usage(usage, receiver, census)["status"],
            "superseded",
        )
        self.assertEqual(
            json.loads(self.paths.estate_current.read_text())["snapshot_sha256"],
            newer_census["snapshot_sha256"],
        )
        self.assertEqual(
            json.loads(self.paths.estate_usage_current.read_text())[
                "snapshot_sha256"
            ],
            newer_usage["snapshot_sha256"],
        )

    def test_evaluation_owner_configuration_is_installation_sealed(self) -> None:
        config_path = self.paths.state / "adapters.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        content_root = self.paths.state / "evaluation-input-owner"
        policy_path = (
            Path(runtime_module.__file__).resolve().parent.parent
            / "references"
            / "remote-subject-content-policy-v1.json"
        )
        policy_sha = runtime_module.load_content_policy(policy_path)[
            "sha256"
        ].removeprefix("sha256:")
        transport_receiver = {
            "receiver_id": "transport-receiver",
            "receiver_sha256": "a" * 64,
            "collector_sha256": "b" * 64,
            "content_policy_sha256": policy_sha,
        }
        config = {
            "contract_version": 1,
            "evaluation_input_owner": {
                "enabled": False,
                "author_model": "author-model",
                "reviewer_a_model": "reviewer-a-model",
                "reviewer_b_model": "reviewer-b-model",
                "content_root": str(content_root),
            },
            "remote_evaluation_subjects": {
                "enabled": False,
                "protocol_version": 1,
                "origin_host_id": "fixture-host",
                "command": [
                    "/usr/bin/python3",
                    str(
                        Path(runtime_module.__file__).resolve().parents[3]
                        / "scripts"
                        / "ssh-estate-census.py"
                    ),
                    "--fetch-subject",
                    "--known-hosts-file",
                    str(self.case / "known-hosts"),
                    "--expected-known-hosts-sha",
                    "c" * 64,
                    "--expected-receiver-id",
                    transport_receiver["receiver_id"],
                    "--expected-receiver-sha",
                    transport_receiver["receiver_sha256"],
                    "--expected-collector-sha",
                    transport_receiver["collector_sha256"],
                    "--expected-content-policy-sha",
                    transport_receiver["content_policy_sha256"],
                ],
                "receiver": transport_receiver,
                "max_files": 512,
                "max_file_bytes": 8 * 1024 * 1024,
                "max_decoded_bytes": 32 * 1024 * 1024,
                "max_encoded_bytes": 48 * 1024 * 1024,
                "snapshot_root": str(
                    self.paths.state / "remote-evaluation-subjects"
                ),
            },
        }
        config_path.write_text(json.dumps(config))
        config_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeFailure, "installation-managed"):
                runtime_module.configured_evaluation_input_owner(
                    config, config_path, self.paths
                )
        with mock.patch.dict(
            os.environ,
            {
                "DREAMING_ADAPTER_CONFIG_MANAGED": "1",
                "DREAMING_ADAPTER_CONFIG_SHA256": config_digest,
            },
            clear=True,
        ):
            owner = runtime_module.configured_evaluation_input_owner(
                config, config_path, self.paths
            )
            remote = (
                runtime_module.configured_remote_evaluation_subjects(
                    config, owner, self.paths
                )
            )
        self.assertFalse(owner["enabled"])
        self.assertFalse(remote["enabled"])
        self.assertEqual(remote["receiver"], transport_receiver)
        self.assertEqual(
            remote["snapshot_store"],
            str(
                (
                    self.paths.state / "remote-evaluation-subjects"
                ).resolve()
            ),
        )
        self.assertEqual(owner["content_root"], str(content_root.resolve()))
        self.assertEqual(
            owner["config_sha256"],
            "sha256:" + hashlib.sha256(config_path.read_bytes()).hexdigest(),
        )
        external = self.case / "external-adapters.json"
        external.write_text(json.dumps(config))
        with mock.patch.dict(
            os.environ,
            {
                "DREAMING_ADAPTER_CONFIG_MANAGED": "1",
                "DREAMING_ADAPTER_CONFIG_SHA256": config_digest,
            },
            clear=True,
        ), self.assertRaisesRegex(RuntimeFailure, "canonical managed"):
            runtime_module.configured_evaluation_input_owner(
                config, external, self.paths
            )
        with mock.patch.dict(
            os.environ,
            {
                "DREAMING_ADAPTER_CONFIG_MANAGED": "1",
                "DREAMING_ADAPTER_CONFIG_SHA256": "0" * 64,
            },
            clear=True,
        ), self.assertRaisesRegex(RuntimeFailure, "installed digest"):
            runtime_module.configured_evaluation_input_owner(
                config, config_path, self.paths
            )

    def test_evaluation_input_root_index_is_exact_and_fail_closed(self) -> None:
        owner, capability_id, entry, _ = self.evaluation_input_content()
        self.assertEqual(
            runtime_module.load_evaluation_input_root(owner),
            {capability_id: entry},
        )
        root = Path(owner["content_root"])
        os.chmod(root, 0o200)
        with self.assertRaisesRegex(RuntimeFailure, "permissions are unsafe"):
            runtime_module.load_evaluation_input_root(owner)
        os.chmod(root, 0o700)
        unknown = root / "unknown"
        unknown.mkdir()
        with self.assertRaisesRegex(RuntimeFailure, "inventory differs"):
            runtime_module.load_evaluation_input_root(owner)
        unknown.rmdir()
        manifest = root / entry["directory"] / runtime_module.EVALUATION_INPUT_MANIFEST_NAME
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        with self.assertRaisesRegex(RuntimeFailure, "manifest identity is stale"):
            runtime_module.load_evaluation_input_root(owner)

    def test_evaluation_input_capability_manifest_enforces_every_file(self) -> None:
        owner, capability_id, entry, files = self.evaluation_input_content()
        indexed = runtime_module.load_evaluation_input_root(owner)
        validated = runtime_module.validate_evaluation_input_capability(
            owner,
            indexed[capability_id],
            installed_skill_roots=[self.paths.skills],
        )
        self.assertEqual(validated["capability_id"], capability_id)
        self.assertEqual(set(validated["files"]), set(files))
        manifest_path = (
            Path(owner["content_root"])
            / entry["directory"]
            / runtime_module.EVALUATION_INPUT_MANIFEST_NAME
        )
        original_manifest = manifest_path.read_bytes()
        manifest = json.loads(original_manifest)
        manifest["files"] = list(reversed(manifest["files"]))
        manifest_path.write_bytes(runtime_module.canonical(manifest))
        with self.assertRaisesRegex(RuntimeFailure, "manifest identity is stale"):
            runtime_module.validate_evaluation_input_capability(
                owner,
                entry,
                installed_skill_roots=[self.paths.skills],
            )
        manifest_path.write_bytes(original_manifest)
        suite_size = files["suite"].stat().st_size
        files["suite"].write_bytes(b"x" * suite_size)
        with self.assertRaisesRegex(RuntimeFailure, "suite identity is stale"):
            runtime_module.validate_evaluation_input_capability(
                owner,
                entry,
                installed_skill_roots=[self.paths.skills],
            )
        files["suite"].write_bytes(runtime_module.canonical({"role": "suite"}))
        files["suite"].write_bytes(
            b"x" * (runtime_module.EVALUATION_INPUT_MAX_FILE_BYTES + 1)
        )
        with self.assertRaisesRegex(RuntimeFailure, "suite size is stale"):
            runtime_module.validate_evaluation_input_capability(
                owner,
                entry,
                installed_skill_roots=[self.paths.skills],
            )
        files["suite"].write_bytes(runtime_module.canonical({"role": "suite"}))
        os.chmod(files["suite"], 0o200)
        with self.assertRaisesRegex(RuntimeFailure, "suite is not readable"):
            runtime_module.validate_evaluation_input_capability(
                owner,
                entry,
                installed_skill_roots=[self.paths.skills],
            )
        os.chmod(files["suite"], 0o600)
        os.chmod(files["policy"], 0o620)
        with self.assertRaisesRegex(RuntimeFailure, "permissions are unsafe"):
            runtime_module.validate_evaluation_input_capability(
                owner,
                entry,
                installed_skill_roots=[self.paths.skills],
            )
        os.chmod(files["policy"], 0o600)
        extra = Path(owner["content_root"]) / entry["directory"] / "extra.json"
        extra.write_text("{}")
        with self.assertRaisesRegex(RuntimeFailure, "inventory differs"):
            runtime_module.validate_evaluation_input_capability(
                owner,
                entry,
                installed_skill_roots=[self.paths.skills],
            )
        extra.unlink()
        malformed = json.loads(original_manifest)
        malformed["files"].append(
            {
                "role": "fixture",
                "path": ".",
                "size": 0,
                "media_type": "application/octet-stream",
                "sha256": "sha256:" + "0" * 64,
            }
        )
        malformed["files"].sort(
            key=lambda item: (item["role"], item["path"])
        )
        malformed_bytes = runtime_module.canonical(malformed)
        manifest_path.write_bytes(malformed_bytes)
        malformed_entry = {
            **entry,
            "manifest_sha256": "sha256:"
            + hashlib.sha256(malformed_bytes).hexdigest(),
        }
        with self.assertRaisesRegex(RuntimeFailure, "is malformed"):
            runtime_module.validate_evaluation_input_capability(
                owner,
                malformed_entry,
                installed_skill_roots=[self.paths.skills],
            )
        manifest_path.write_bytes(original_manifest)
        fake_runtime = self.case / "installed" / "dreaming-core.py"
        fake_runtime.parent.mkdir()
        fake_runtime.write_text("")
        trusted_harness = fake_runtime.with_name("skill-evaluation-harness.py")
        trusted_harness.symlink_to(
            RUNTIME_PATH.with_name("skill-evaluation-harness.py")
        )
        original_runtime_file = runtime_module.__file__
        original_read_bytes = Path.read_bytes

        def refuse_trusted_harness_read(path: Path) -> bytes:
            if path == trusted_harness:
                raise AssertionError("trusted symlink was opened before validation")
            return original_read_bytes(path)

        runtime_module.__file__ = str(fake_runtime)
        try:
            with mock.patch.object(
                Path, "read_bytes", autospec=True, side_effect=refuse_trusted_harness_read
            ), self.assertRaisesRegex(RuntimeFailure, "not a regular file"):
                runtime_module.validate_evaluation_input_capability(
                    owner,
                    entry,
                    installed_skill_roots=[self.paths.skills],
                )
        finally:
            runtime_module.__file__ = original_runtime_file
        with self.assertRaisesRegex(RuntimeFailure, "overlaps an installed"):
            runtime_module.validate_evaluation_input_capability(
                owner,
                entry,
                installed_skill_roots=[Path(owner["content_root"])],
            )

    def test_evaluation_input_sealer_includes_support_trees(self) -> None:
        source_root = self.case / "input-source"
        source = source_root / "pack"
        (source / "fixtures").mkdir(parents=True)
        (source / "graders").mkdir()
        for name in (
            "suite.json",
            "policy.json",
            "compilation.json",
            "routing.json",
            "authoring-catalog.json",
        ):
            (source / name).write_bytes(
                runtime_module.canonical({"name": name})
            )
        (source / "fixtures" / "synthetic.json").write_bytes(
            runtime_module.canonical({"fixture": "synthetic"})
        )
        (source / "fixtures" / "nested").mkdir()
        (source / "fixtures" / "nested" / "second.json").write_bytes(
            runtime_module.canonical({"fixture": "nested"})
        )
        (source / "graders" / "contracts.json").write_bytes(
            runtime_module.canonical({"grader": "contracts"})
        )
        skill = self.case / "installed" / "skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# Seal fixture\n")
        capability_id = "sha256:" + "d" * 64
        plan = self.case / "seal-plan.json"
        plan.write_bytes(
            runtime_module.canonical(
                {
                    "schema_version": 1,
                    "kind": "evaluation_input_seal_plan",
                    "capabilities": [
                        {
                            "capability_id": capability_id,
                            "directory": "capability-000",
                            "skill_path": str(skill.resolve()),
                            "source_directory": "pack",
                        }
                    ],
                }
            )
        )
        output = self.case / "sealed-inputs"
        prior_umask = os.umask(0o002)
        try:
            with mock.patch.object(
                runtime_module,
                "validate_sealed_evaluation_input_packet",
            ) as semantic:
                result = runtime_module.seal_evaluation_input_root(
                    source_root,
                    plan,
                    output,
                    installed_skill_roots=[skill],
                )
        finally:
            os.umask(prior_umask)
        self.assertEqual(result["status"], "sealed")
        self.assertEqual(result["capability_ids"], [capability_id])
        semantic.assert_called_once()
        owner = {"content_root": str(output)}
        entry = runtime_module.load_evaluation_input_root(owner)[capability_id]
        validated = runtime_module.validate_evaluation_input_capability(
            owner, entry, installed_skill_roots=[skill]
        )
        self.assertEqual(set(validated["files"]), set(
            runtime_module.EVALUATION_INPUT_CONTENT_ROLES
        ))
        manifest = json.loads(
            (
                output
                / entry["directory"]
                / runtime_module.EVALUATION_INPUT_MANIFEST_NAME
            ).read_bytes()
        )
        self.assertEqual(
            sorted(
                item["role"]
                for item in manifest["files"]
                if item["role"] in {"fixture", "grader"}
            ),
            ["fixture", "fixture", "grader"],
        )
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o500)
        self.assertEqual(
            stat.S_IMODE(
                (output / entry["directory"] / "fixtures").stat().st_mode
            ),
            0o500,
        )
        self.assertEqual(
            stat.S_IMODE(
                (
                    output
                    / entry["directory"]
                    / "fixtures"
                    / "nested"
                ).stat().st_mode
            ),
            0o500,
        )
        with self.assertRaisesRegex(
            RuntimeFailure, "roots are unsafe or overlapping"
        ):
            runtime_module.seal_evaluation_input_root(
                source_root,
                plan,
                output,
                installed_skill_roots=[skill],
            )
        fixture = output / entry["directory"] / "fixtures" / "synthetic.json"
        os.chmod(fixture, 0o600)
        fixture.write_bytes(b"x" * fixture.stat().st_size)
        with self.assertRaisesRegex(RuntimeFailure, "fixture identity is stale"):
            runtime_module.validate_evaluation_input_capability(
                owner, entry, installed_skill_roots=[skill]
            )

        reserved_plan = self.case / "reserved-plan.json"
        reserved = json.loads(plan.read_text())
        reserved["capabilities"][0]["directory"] = (
            runtime_module.EVALUATION_INPUT_INDEX_NAME
        )
        reserved_plan.write_bytes(runtime_module.canonical(reserved))
        with self.assertRaisesRegex(RuntimeFailure, "capability 0 is malformed"):
            runtime_module.validate_evaluation_input_seal_plan(
                source_root, reserved_plan
            )

        external = self.case / "external-pack"
        shutil.copytree(source, external)
        shutil.rmtree(source)
        source.symlink_to(external, target_is_directory=True)
        replacement_output = self.case / "replacement-output"
        with mock.patch.object(
            runtime_module,
            "validate_evaluation_input_seal_plan",
            return_value=[
                {
                    "capability_id": capability_id,
                    "directory": "capability-000",
                    "skill_path": str(skill.resolve()),
                    "source_directory": "pack",
                }
            ],
        ), self.assertRaisesRegex(
            RuntimeFailure, "source is unavailable"
        ):
            runtime_module.seal_evaluation_input_root(
                source_root,
                plan,
                replacement_output,
                installed_skill_roots=[skill],
            )

    def test_evaluation_input_queue_is_same_run_ordered_and_nonpersistent(self) -> None:
        capability_ids = [f"sha256:{value * 64}" for value in "1234567"]
        content_ids = [
            capability_id
            for capability_id in capability_ids
            if capability_id != capability_ids[2]
        ]
        owner, _, content_files = self.evaluation_input_content_set(content_ids)
        physical = []
        enabled = []
        states = [
            ("ready", "ready", False, []),
            ("missing", "missing", False, []),
            ("missing", "missing", False, []),
            ("regression", "regression", True, []),
            (
                "pass",
                "pass",
                True,
                [{"evaluation_class": "overlap", "comparable": True}],
            ),
            ("pass", "pass", True, []),
            ("stale", "pass", False, []),
        ]
        for position, (capability_id, state) in enumerate(
            zip(capability_ids, states)
        ):
            skill = (self.paths.skills / f"skill-{position}").resolve()
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(f"# Skill {position}\n")
            instance_id = f"instance-{position}"
            root_class = "plugin" if position == 5 else "user"
            physical.append(
                {
                    "instance_id": instance_id,
                    "canonical_capability_id": capability_id,
                    "absolute_path": str(skill),
                    "host_id": "fixture-host",
                    "root_id": "fixture-root",
                    "relative_path": f"skill-{position}",
                    "inventory_sha256": f"inventory-{position}",
                    "root_class": root_class,
                    "owner": "plugin-1" if position == 5 else None,
                    "evaluation": {
                        "state": state[0],
                        "status": state[1],
                        "current": state[2],
                        "cases": state[3],
                    },
                    "evaluation_complete": True,
                    "dependencies_complete": True,
                }
            )
            enabled.append(
                {
                    "instance_id": instance_id,
                    "canonical_capability_id": capability_id,
                    "runtime_enabled": True,
                    "runtime_name": f"skill-{position}",
                }
            )
        collected_at = "2026-08-18T16:00:00+00:00"
        census = {
            "schema_version": 1,
            "host_id": "fixture-host",
            "collected_at": collected_at,
            "scope": {"complete": True},
            "physical_instances": physical,
            "enabled_instances": enabled,
            "unresolved_mappings": [],
            "plugins": [
                {
                    "plugin_id": "plugin-1",
                    "enabled": True,
                    "capabilities": {"complete": True},
                }
            ],
            "evidence": {"evaluation_inventory": {"complete": True}},
        }
        census["snapshot_sha256"] = runtime_module.digest(census)
        usage = {
            "schema_version": 1,
            "host_id": census["host_id"],
            "collected_at": collected_at,
            "census_snapshot_sha256": census["snapshot_sha256"],
            "coverage": {
                "complete": False,
                "pending": [
                    {
                        "session_id": "active",
                        "modified_at": collected_at,
                        "reason": "events_recently_modified",
                    }
                ],
                "failures": [
                    {
                        "session_id": "legacy-malformed-name",
                        "modified_at": "2026-01-01T00:00:00+00:00",
                        "reason": "usage_session_invalid_skill_name",
                    }
                ],
            },
            "canonical_usage": [
                {
                    "canonical_capability_id": capability_id,
                    "uses_30d": 0 if position == 0 else 1,
                }
                for position, capability_id in enumerate(capability_ids)
            ],
            "unattributed": [
                {
                    "name": "retired-skill",
                    "reason": "unmapped",
                    "uses_30d": 0,
                    "uses_7d": 0,
                    "uses_90d": 1,
                    "uses_total": 1,
                }
            ],
        }
        usage["snapshot_sha256"] = runtime_module.digest(usage)
        legacy_conflicting_usage = json.loads(json.dumps(usage))
        legacy_conflicting_usage["unattributed"] = [
            {
                "name": "ambiguous-legacy-skill",
                "reason": "conflicting_mapping",
                "uses_30d": 0,
                "uses_7d": 0,
                "uses_90d": 1,
                "uses_total": 1,
            }
        ]
        self.assertEqual(
            runtime_module.evaluation_input_usage_state(
                capability_ids[0],
                legacy_conflicting_usage,
                legacy_conflicting_usage["canonical_usage"][0],
            ),
            "blocked_identity",
        )
        receiver = {
            "receiver_id": "fixture",
            "receiver_sha256": "a" * 64,
            "collector_sha256": "b" * 64,
        }
        receipt_receiver = {
            key: receiver[key] for key in sorted(receiver)
        }
        census_receipt_sha256 = runtime_module.digest(
            {
                "schema_version": 1,
                "snapshot_sha256": census["snapshot_sha256"],
                "receiver": receipt_receiver,
                "census": census,
            }
        )
        usage_receipt_sha256 = runtime_module.digest(
            {
                "schema_version": 1,
                "snapshot_sha256": usage["snapshot_sha256"],
                "census_snapshot_sha256": census["snapshot_sha256"],
                "receiver": receipt_receiver,
                "usage": usage,
            }
        )
        before = sorted(
            path.relative_to(self.case).as_posix()
            for path in self.case.rglob("*")
        )
        queue = runtime_module.derive_evaluation_input_queue(
            owner,
            census,
            usage,
            receiver,
            census_receipt_sha256=census_receipt_sha256,
            usage_receipt_sha256=usage_receipt_sha256,
        )
        after = sorted(
            path.relative_to(self.case).as_posix()
            for path in self.case.rglob("*")
        )
        self.assertEqual(before, after)
        self.assertEqual(
            [(row["priority"], row["capability_id"]) for row in queue["rows"]],
            [
                (1, capability_ids[0]),
                (2, capability_ids[1]),
                (2, capability_ids[2]),
                (3, capability_ids[3]),
                (4, capability_ids[4]),
                (5, capability_ids[5]),
                (6, capability_ids[6]),
            ],
        )
        by_id = {row["capability_id"]: row for row in queue["rows"]}
        self.assertEqual(
            by_id[capability_ids[0]]["deferral_reason"],
            "ready_for_execution",
        )
        self.assertIsNone(by_id[capability_ids[0]]["runnable_phase"])
        self.assertEqual(
            by_id[capability_ids[1]]["runnable_phase"], "authoring"
        )
        self.assertIsNotNone(
            by_id[capability_ids[1]]["input_manifest_sha256"]
        )
        self.assertEqual(
            by_id[capability_ids[2]]["deferral_reason"], "input_not_ready"
        )
        self.assertEqual(
            by_id[capability_ids[3]]["queue_reason"],
            "regression_or_routing_conflict",
        )
        overlay_rows = []
        for item in physical:
            identity_fields = {
                "origin_host_id": item["host_id"],
                "origin_root_id": item["root_id"],
                "origin_relative_path": item["relative_path"],
            }
            overlay_rows.append(
                {
                    "capability_id": item["canonical_capability_id"],
                    "subject_key": runtime_module.digest(identity_fields),
                    **identity_fields,
                    "origin_path": item["absolute_path"],
                    "canonical_capability_id": item[
                        "canonical_capability_id"
                    ],
                    "origin_inventory_sha256": item["inventory_sha256"],
                    "candidate_id": None,
                    "superseded_candidate_ids": [],
                    "snapshot_state": "remote_candidate_not_fetched",
                    "content_path": None,
                    "transport_receipt_sha256": None,
                    "snapshot_refusal": None,
                    "evaluation": None,
                }
            )
        overlay = {
            "schema_version": 1,
            "kind": "remote_evaluation_overlay",
            "census_snapshot_sha256": census["snapshot_sha256"],
            "census_receipt_sha256": census_receipt_sha256,
            "usage_snapshot_sha256": usage["snapshot_sha256"],
            "usage_receipt_sha256": usage_receipt_sha256,
            "receiver": receipt_receiver,
            "transport_receiver": {
                **receipt_receiver,
                "content_policy_sha256": "c" * 64,
            },
            "origin_host_id": census["host_id"],
            "evaluator_sha256": "sha256:" + "e" * 64,
            "registry_identity": (
                runtime_module.EVALUATION_OVERLAY_REGISTRY_IDENTITY
            ),
            "rows": overlay_rows,
        }
        overlay["overlay_sha256"] = runtime_module.digest(overlay)
        remote_queue = runtime_module.derive_evaluation_input_queue(
            owner,
            census,
            usage,
            receiver,
            census_receipt_sha256=census_receipt_sha256,
            usage_receipt_sha256=usage_receipt_sha256,
            evaluation_overlay=overlay,
            transport_receiver=overlay["transport_receiver"],
        )
        remote_by_id = {
            row["capability_id"]: row for row in remote_queue["rows"]
        }
        self.assertEqual(
            remote_by_id[capability_ids[0]]["required_phase"], "transport"
        )
        self.assertEqual(
            remote_by_id[capability_ids[0]]["runnable_phase"], "transport"
        )
        self.assertEqual(
            remote_by_id[capability_ids[0]]["evaluation_state"], "missing"
        )
        self.assertEqual(
            remote_by_id[capability_ids[0]]["snapshot_state"],
            "remote_candidate_not_fetched",
        )
        self.assertEqual(
            remote_by_id[capability_ids[2]]["deferral_reason"],
            "input_not_ready",
        )
        self.assertIsNone(
            remote_by_id[capability_ids[2]]["runnable_phase"]
        )

        snapshot_candidate_id = "sha256:" + "8" * 64
        snapshot_overlay = json.loads(json.dumps(overlay))
        snapshot_row = snapshot_overlay["rows"][1]
        remote_snapshot = (
            self.case
            / snapshot_row["subject_key"].removeprefix("sha256:")
            / snapshot_candidate_id.removeprefix("sha256:")
            / "candidate"
        )
        shutil.copytree(
            Path(physical[1]["absolute_path"]),
            remote_snapshot,
        )
        snapshot_row.update(
            {
                "candidate_id": snapshot_candidate_id,
                "snapshot_state": "remote_candidate_snapshot_ready",
                "content_path": str(remote_snapshot),
                "transport_receipt_sha256": "sha256:" + "9" * 64,
                "evaluation": {
                    "state": "missing",
                    "status": "",
                    "current": False,
                    "evaluated_at": None,
                    "receipt_sha256": None,
                    "transition_id": None,
                    "input_manifest_sha256": None,
                    "cases": [],
                },
            }
        )
        snapshot_overlay["overlay_sha256"] = runtime_module.digest(
            {
                key: value
                for key, value in snapshot_overlay.items()
                if key != "overlay_sha256"
            }
        )
        snapshot_queue = runtime_module.derive_evaluation_input_queue(
            owner,
            census,
            usage,
            receiver,
            census_receipt_sha256=census_receipt_sha256,
            usage_receipt_sha256=usage_receipt_sha256,
            evaluation_overlay=snapshot_overlay,
            transport_receiver=overlay["transport_receiver"],
        )
        snapshot_by_id = {
            row["capability_id"]: row for row in snapshot_queue["rows"]
        }
        self.assertEqual(
            snapshot_by_id[capability_ids[1]]["runnable_phase"],
            "authoring",
        )
        self.assertIsNone(
            snapshot_by_id[capability_ids[1]]["deferral_reason"]
        )
        self.assertIsNotNone(
            snapshot_by_id[capability_ids[1]]["input_manifest_sha256"]
        )

        refused_census = json.loads(json.dumps(census))
        ambiguous_instance = json.loads(json.dumps(physical[0]))
        ambiguous_instance["instance_id"] = "ambiguous-instance"
        ambiguous_instance["absolute_path"] = physical[1]["absolute_path"]
        refused_census["physical_instances"].append(ambiguous_instance)
        refused_census.pop("snapshot_sha256")
        refused_census["snapshot_sha256"] = runtime_module.digest(
            refused_census
        )
        refused_usage = json.loads(json.dumps(usage))
        refused_usage["census_snapshot_sha256"] = refused_census[
            "snapshot_sha256"
        ]
        refused_usage.pop("snapshot_sha256")
        refused_usage["snapshot_sha256"] = runtime_module.digest(refused_usage)
        refused_census_receipt_sha256 = runtime_module.digest(
            {
                "schema_version": 1,
                "snapshot_sha256": refused_census["snapshot_sha256"],
                "receiver": receipt_receiver,
                "census": refused_census,
            }
        )
        refused_usage_receipt_sha256 = runtime_module.digest(
            {
                "schema_version": 1,
                "snapshot_sha256": refused_usage["snapshot_sha256"],
                "census_snapshot_sha256": refused_census["snapshot_sha256"],
                "receiver": receipt_receiver,
                "usage": refused_usage,
            }
        )
        refused_overlay = json.loads(json.dumps(overlay))
        refused_overlay.update(
            {
                "census_snapshot_sha256": refused_census["snapshot_sha256"],
                "census_receipt_sha256": refused_census_receipt_sha256,
                "usage_snapshot_sha256": refused_usage["snapshot_sha256"],
                "usage_receipt_sha256": refused_usage_receipt_sha256,
            }
        )
        refused_row = refused_overlay["rows"][1]
        refused_row.update(
            {
                "snapshot_state": "remote_candidate_refused",
                "snapshot_refusal": {
                    "code": "remote_candidate_refused",
                    "message": "synthetic refusal",
                    "receipt_sha256": "sha256:" + "7" * 64,
                    "observed_at": "2026-08-21T22:11:20Z",
                },
            }
        )
        refused_overlay["overlay_sha256"] = runtime_module.digest(
            {
                key: value
                for key, value in refused_overlay.items()
                if key != "overlay_sha256"
            }
        )
        refused_queue = runtime_module.derive_evaluation_input_queue(
            owner,
            refused_census,
            refused_usage,
            receiver,
            census_receipt_sha256=refused_census_receipt_sha256,
            usage_receipt_sha256=refused_usage_receipt_sha256,
            evaluation_overlay=refused_overlay,
            transport_receiver=overlay["transport_receiver"],
        )
        refused_by_id = {
            row["capability_id"]: row for row in refused_queue["rows"]
        }
        self.assertEqual(
            refused_by_id[capability_ids[1]]["deferral_reason"],
            "capability_path_ambiguous",
        )
        self.assertIsNone(
            refused_by_id[capability_ids[1]]["runnable_phase"]
        )
        census_without_local_evaluations = json.loads(json.dumps(census))
        census_without_local_evaluations["evidence"] = {}
        for item in census_without_local_evaluations["physical_instances"]:
            item.pop("evaluation", None)
            item.pop("evaluation_complete", None)
        census_without_local_evaluations["snapshot_sha256"] = (
            runtime_module.digest(
                {
                    key: value
                    for key, value in census_without_local_evaluations.items()
                    if key != "snapshot_sha256"
                }
            )
        )
        usage_without_local_evaluations = json.loads(json.dumps(usage))
        usage_without_local_evaluations["census_snapshot_sha256"] = (
            census_without_local_evaluations["snapshot_sha256"]
        )
        usage_without_local_evaluations["snapshot_sha256"] = (
            runtime_module.digest(
                {
                    key: value
                    for key, value in usage_without_local_evaluations.items()
                    if key != "snapshot_sha256"
                }
            )
        )
        census_receipt_without_local_evaluations = runtime_module.digest(
            {
                "schema_version": 1,
                "snapshot_sha256": census_without_local_evaluations[
                    "snapshot_sha256"
                ],
                "receiver": receipt_receiver,
                "census": census_without_local_evaluations,
            }
        )
        usage_receipt_without_local_evaluations = runtime_module.digest(
            {
                "schema_version": 1,
                "snapshot_sha256": usage_without_local_evaluations[
                    "snapshot_sha256"
                ],
                "census_snapshot_sha256": census_without_local_evaluations[
                    "snapshot_sha256"
                ],
                "receiver": receipt_receiver,
                "usage": usage_without_local_evaluations,
            }
        )
        overlay_without_local_evaluations = json.loads(json.dumps(overlay))
        overlay_without_local_evaluations["census_snapshot_sha256"] = (
            census_without_local_evaluations["snapshot_sha256"]
        )
        overlay_without_local_evaluations["census_receipt_sha256"] = (
            census_receipt_without_local_evaluations
        )
        overlay_without_local_evaluations["usage_snapshot_sha256"] = (
            usage_without_local_evaluations["snapshot_sha256"]
        )
        overlay_without_local_evaluations["usage_receipt_sha256"] = (
            usage_receipt_without_local_evaluations
        )
        overlay_without_local_evaluations["overlay_sha256"] = (
            runtime_module.digest(
                {
                    key: value
                    for key, value in overlay_without_local_evaluations.items()
                    if key != "overlay_sha256"
                }
            )
        )
        remote_queue_without_local_evaluations = (
            runtime_module.derive_evaluation_input_queue(
                owner,
                census_without_local_evaluations,
                usage_without_local_evaluations,
                receiver,
                census_receipt_sha256=(
                    census_receipt_without_local_evaluations
                ),
                usage_receipt_sha256=usage_receipt_without_local_evaluations,
                evaluation_overlay=overlay_without_local_evaluations,
                transport_receiver=overlay["transport_receiver"],
            )
        )
        self.assertEqual(
            {
                row["capability_id"]: row["runnable_phase"]
                for row in remote_queue_without_local_evaluations["rows"]
            },
            {
                row["capability_id"]: row["runnable_phase"]
                for row in remote_queue["rows"]
            },
        )
        forged_overlay = json.loads(json.dumps(overlay))
        forged_overlay["rows"].pop()
        forged_overlay["overlay_sha256"] = runtime_module.digest(
            {
                key: value
                for key, value in forged_overlay.items()
                if key != "overlay_sha256"
            }
        )
        with self.assertRaisesRegex(
            RuntimeFailure, "does not cover the enabled estate"
        ):
            runtime_module.derive_evaluation_input_queue(
                owner,
                census,
                usage,
                receiver,
                census_receipt_sha256=census_receipt_sha256,
                usage_receipt_sha256=usage_receipt_sha256,
                evaluation_overlay=forged_overlay,
                transport_receiver=overlay["transport_receiver"],
            )
        content_files[capability_ids[1]]["suite"].write_text("{}")
        rescanned = runtime_module.derive_evaluation_input_queue(
            owner,
            census,
            usage,
            receiver,
            census_receipt_sha256=census_receipt_sha256,
            usage_receipt_sha256=usage_receipt_sha256,
        )
        rescanned_by_id = {
            row["capability_id"]: row for row in rescanned["rows"]
        }
        self.assertEqual(
            rescanned_by_id[capability_ids[1]]["deferral_reason"],
            "input_not_ready",
        )
        self.assertEqual(
            rescanned_by_id[capability_ids[3]]["priority"], 3
        )
        cross_run = dict(usage)
        cross_run["census_snapshot_sha256"] = "sha256:" + "f" * 64
        cross_run["snapshot_sha256"] = runtime_module.digest(
            {
                key: value
                for key, value in cross_run.items()
                if key != "snapshot_sha256"
            }
        )
        with self.assertRaisesRegex(RuntimeFailure, "cross-run"):
            runtime_module.derive_evaluation_input_queue(
                owner,
                census,
                cross_run,
                receiver,
                census_receipt_sha256=census_receipt_sha256,
                usage_receipt_sha256=usage_receipt_sha256,
            )
        with self.assertRaisesRegex(RuntimeFailure, "cross-run"):
            runtime_module.derive_evaluation_input_queue(
                owner,
                census,
                usage,
                receiver,
                census_receipt_sha256="sha256:" + "d" * 64,
                usage_receipt_sha256=usage_receipt_sha256,
            )
        malformed_usage = json.loads(json.dumps(usage))
        malformed_usage["unattributed"] = [
            {"name": "bad", "candidate_capability_ids": None}
        ]
        malformed_usage["snapshot_sha256"] = runtime_module.digest(
            {
                key: value
                for key, value in malformed_usage.items()
                if key != "snapshot_sha256"
            }
        )
        malformed_usage_receipt = runtime_module.digest(
            {
                "schema_version": 1,
                "snapshot_sha256": malformed_usage["snapshot_sha256"],
                "census_snapshot_sha256": census["snapshot_sha256"],
                "receiver": receipt_receiver,
                "usage": malformed_usage,
            }
        )
        with self.assertRaisesRegex(RuntimeFailure, "attribution is malformed"):
            runtime_module.derive_evaluation_input_queue(
                owner,
                census,
                malformed_usage,
                receiver,
                census_receipt_sha256=census_receipt_sha256,
                usage_receipt_sha256=malformed_usage_receipt,
            )
        index_path = (
            Path(owner["content_root"])
            / runtime_module.EVALUATION_INPUT_INDEX_NAME
        )
        index = json.loads(index_path.read_bytes())
        index["capabilities"][0]["capability_id"] = "sha256:" + "f" * 64
        index["record_sha256"] = runtime_module.digest(
            {
                key: value
                for key, value in index.items()
                if key != "record_sha256"
            }
        )
        index_path.write_bytes(runtime_module.canonical(index))
        with self.assertRaisesRegex(RuntimeFailure, "unknown capability"):
            runtime_module.derive_evaluation_input_queue(
                owner,
                census,
                usage,
                receiver,
                census_receipt_sha256=census_receipt_sha256,
                usage_receipt_sha256=usage_receipt_sha256,
            )

    def test_evaluation_input_owner_process_group_is_reaped_on_stop(self) -> None:
        owner_run_id = "owner-run-fixture"
        claim_id = "sha256:" + "a" * 64
        claim_fence = self.case / "claim.json"
        child = (
            "import json,subprocess,sys,time;"
            "from pathlib import Path;"
            f"Path({str(claim_fence)!r}).write_text(json.dumps("
            f"{{'schema_version':1,'claim_id':{claim_id!r},"
            f"'owner_run_id':{owner_run_id!r}}}));"
            "subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
            "time.sleep(60)"
        )
        terminals = []
        halted = runtime_module.run_evaluation_input_owner_process(
            [sys.executable, "-c", child],
            owner_run_id=owner_run_id,
            claim_fence_path=claim_fence,
            halt_check=claim_fence.exists,
            lease_check=lambda: True,
            terminalize=lambda claim, reason: terminals.append(
                (claim, reason)
            ) or {"status": "published"},
            timeout_seconds=5,
            stop_seconds=2,
            poll_seconds=0.05,
        )
        self.assertEqual(halted["status"], "halted")
        self.assertEqual(
            terminals,
            [
                (
                    {"claim_id": claim_id, "owner_run_id": owner_run_id},
                    "halted",
                )
            ],
        )
        with self.assertRaises(ProcessLookupError):
            os.killpg(halted["process_group_id"], 0)

        claim_fence.unlink()
        terminals.clear()
        lock_lost = runtime_module.run_evaluation_input_owner_process(
            [sys.executable, "-c", child],
            owner_run_id=owner_run_id,
            claim_fence_path=claim_fence,
            halt_check=lambda: False,
            lease_check=lambda: not claim_fence.exists(),
            terminalize=lambda claim, reason: terminals.append(
                (claim, reason)
            ) or {},
            timeout_seconds=5,
            stop_seconds=2,
            poll_seconds=0.05,
        )
        self.assertEqual(lock_lost["status"], "lock_lost")
        self.assertEqual(lock_lost["claim"]["claim_id"], claim_id)
        self.assertEqual(terminals, [])

        missing_fence = self.case / "missing-claim.json"
        halt_checks = iter([False, False, True])
        missing_terminals = []
        missing = runtime_module.run_evaluation_input_owner_process(
            [
                sys.executable,
                "-c",
                "import time;time.sleep(60)",
            ],
            owner_run_id=owner_run_id,
            claim_fence_path=missing_fence,
            halt_check=lambda: next(halt_checks, True),
            lease_check=lambda: True,
            terminalize=lambda claim, reason: missing_terminals.append(
                (claim, reason)
            ) or {"status": "no_claim"},
            timeout_seconds=5,
            stop_seconds=2,
            poll_seconds=0.05,
        )
        self.assertEqual(missing["status"], "halted")
        self.assertEqual(missing["claim"], None)
        self.assertEqual(missing_terminals, [(None, "halted")])

        claim_fence.unlink()
        completed = runtime_module.run_evaluation_input_owner_process(
            [
                sys.executable,
                "-c",
                "import json,time;print(json.dumps({'status':'complete'}));"
                "time.sleep(0.1)",
            ],
            owner_run_id=owner_run_id,
            claim_fence_path=claim_fence,
            halt_check=lambda: False,
            lease_check=lambda: True,
            terminalize=lambda claim, reason: {},
            timeout_seconds=5,
            stop_seconds=2,
            poll_seconds=0.05,
        )
        self.assertEqual(completed, {"status": "complete"})

    def test_evaluation_input_owner_selects_first_authorable_row(self) -> None:
        owner = {
            "evaluator": str(self.case / "skill-evaluation.py"),
            "config_sha256": "sha256:" + "1" * 64,
            "author_model": "author-model",
            "reviewer_a_model": "reviewer-a-model",
            "reviewer_b_model": "reviewer-b-model",
        }
        queue = {
            "rows": [
                {
                    "capability_id": "sha256:" + "a" * 64,
                    "skill_path": str(self.case / "blocked"),
                    "priority": 1,
                    "runnable_phase": None,
                },
                {
                    "capability_id": "sha256:" + "b" * 64,
                    "skill_path": str(self.case / "selected"),
                    "priority": 2,
                    "runnable_phase": "authoring",
                },
            ]
        }
        files = {
            "suite": str(self.case / "suite.json"),
            "policy": str(self.case / "policy.json"),
            "compilation": str(self.case / "compilation.json"),
            "routing": str(self.case / "routing.json"),
            "harness": str(self.case / "harness.py"),
            "catalog": str(self.case / "catalog.json"),
        }
        captured = {}
        with (
            mock.patch.dict(
                os.environ,
                {"DREAMING_PARENT_RUN_ID": "scheduled-parent-run"},
            ),
            mock.patch.object(
                runtime_module,
                "evaluation_input_owner_content",
                return_value={"files": files},
            ) as content,
            mock.patch.object(
                runtime_module,
                "run_evaluation_input_owner_process",
                side_effect=lambda command, **facts: captured.update(
                    {"command": command, **facts}
                )
                or {"status": "ready", "claim_id": "sha256:" + "c" * 64},
            ),
        ):
            result = runtime_module.execute_evaluation_input_owner(
                owner, {}, queue, self.paths
            )
        self.assertEqual(
            result["selected_capability_id"], "sha256:" + "b" * 64
        )
        self.assertEqual(result["selected_priority"], 2)
        self.assertEqual(result["status"], "ready")
        content.assert_called_once_with(
            owner, {}, "sha256:" + "b" * 64
        )
        self.assertEqual(
            captured["owner_run_id"], "scheduled-parent-run"
        )
        command = captured["command"]
        self.assertEqual(
            command[command.index("--config") + 1], files["compilation"]
        )
        self.assertEqual(
            command[command.index("--reviewer-b-model") + 1],
            "reviewer-b-model",
        )

    def test_disabled_evaluation_owner_recovers_before_census(self) -> None:
        adapters = {
            "session-source": {},
            "review-executor": {},
            "skill-publisher": {},
        }
        owner = {
            "enabled": False,
            "config_sha256": "sha256:" + "1" * 64,
            "evaluator": str(RUNTIME_PATH.with_name("skill-evaluation.py")),
        }
        recovery = {
            "status": "reconciled",
            "terminal_publications": [],
        }
        with (
            mock.patch.object(runtime_module, "default_paths", return_value=self.paths),
            mock.patch.object(
                runtime_module,
                "load_adapter_config",
                return_value={"sources": {}, "executors": {}},
            ),
            mock.patch.object(
                runtime_module, "validated_routing", return_value=(set(), [])
            ),
            mock.patch.object(
                runtime_module,
                "configured_adapters_tolerant",
                return_value=(adapters, [], []),
            ),
            mock.patch.object(
                runtime_module,
                "configured_role_tolerant",
                return_value=({}, [], []),
            ),
            mock.patch.object(
                runtime_module,
                "configured_evaluation_input_owner",
                return_value=owner,
            ),
            mock.patch.object(
                runtime_module,
                "reconcile_evaluation_input_owner",
                return_value=recovery,
            ) as reconcile,
            mock.patch.object(
                runtime_module,
                "collect_estate_census",
                side_effect=RuntimeFailure("census-stop", "fixture"),
            ) as census,
        ):
            report = runtime_module.scheduled_run()
        self.assertEqual(report["evaluation_input"]["mode"], "reconcile_only")
        self.assertEqual(report["evaluation_input"]["recovery"], recovery)
        self.assertEqual(reconcile.call_count, 1)
        self.assertEqual(census.call_count, 1)
        self.assertEqual(
            report["errors"][0],
            {"phase": "estate-census", "code": "census-stop"},
        )
        self.assertEqual(
            census.call_args.kwargs, {"include_evidence": False}
        )
        enabled_owner = {**owner, "enabled": True}
        with (
            mock.patch.object(runtime_module, "default_paths", return_value=self.paths),
            mock.patch.object(
                runtime_module,
                "load_adapter_config",
                return_value={"sources": {}, "executors": {}},
            ),
            mock.patch.object(
                runtime_module, "validated_routing", return_value=(set(), [])
            ),
            mock.patch.object(
                runtime_module,
                "configured_adapters_tolerant",
                return_value=(adapters, [], []),
            ),
            mock.patch.object(
                runtime_module,
                "configured_role_tolerant",
                return_value=({}, [], []),
            ),
            mock.patch.object(
                runtime_module,
                "configured_evaluation_input_owner",
                return_value=enabled_owner,
            ),
            mock.patch.object(
                runtime_module,
                "reconcile_evaluation_input_owner",
                return_value=recovery,
            ),
            mock.patch.object(
                runtime_module,
                "collect_estate_census",
                return_value=None,
            ) as enabled_census,
        ):
            enabled_report = runtime_module.scheduled_run()
        self.assertEqual(
            enabled_census.call_args.kwargs, {"include_evidence": True}
        )
        self.assertEqual(
            enabled_report["evaluation_input"]["queue"],
            {
                "status": "refused",
                "code": "evaluation-input-evidence-invalid",
            },
        )
        self.assertIn(
            {
                "phase": "evaluation-input-queue",
                "code": "evaluation-input-evidence-invalid",
            },
            enabled_report["errors"],
        )
        with (
            mock.patch.object(runtime_module, "default_paths", return_value=self.paths),
            mock.patch.object(
                runtime_module,
                "load_adapter_config",
                return_value={"sources": {}, "executors": {}},
            ),
            mock.patch.object(
                runtime_module, "validated_routing", return_value=(set(), [])
            ),
            mock.patch.object(
                runtime_module,
                "configured_adapters_tolerant",
                return_value=(adapters, [], []),
            ),
            mock.patch.object(
                runtime_module,
                "configured_role_tolerant",
                return_value=({}, [], []),
            ),
            mock.patch.object(
                runtime_module,
                "configured_evaluation_input_owner",
                return_value=owner,
            ),
            mock.patch.object(
                runtime_module,
                "reconcile_evaluation_input_owner",
                side_effect=RuntimeFailure(
                    "evaluation-input-recovery-failed", "fixture"
                ),
            ),
            mock.patch.object(
                runtime_module, "collect_estate_census"
            ) as blocked_census,
        ):
            blocked = runtime_module.scheduled_run()
        self.assertFalse(blocked["ok"])
        self.assertEqual(
            blocked["evaluation_input"]["mode"], "reconcile_only"
        )
        self.assertEqual(blocked["evaluation_input"]["recovery"], "failed")
        self.assertEqual(blocked_census.call_count, 0)

    def initialize_git_repo(self) -> None:
        subprocess.run(
            ["git", "-C", str(self.paths.skills), "init", "-q"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.paths.skills), "config", "core.hooksPath", "/dev/null"],
            check=True,
        )

    def init_skills_repo(self) -> str:
        self.paths.skills.mkdir(parents=True, exist_ok=True)
        self.initialize_git_repo()
        (self.paths.skills / ".gitignore").write_text(".DS_Store\n")
        subprocess.run(
            ["git", "-C", str(self.paths.skills), "add", ".gitignore"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.paths.skills),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "initial",
            ],
            check=True,
        )
        return subprocess.run(
            ["git", "-C", str(self.paths.skills), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()

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
        self.assertEqual(len(report["profiles"]), 2)
        self.assertEqual(report["deferred_profiles"], 0)
        self.assertEqual(report["reviews"], [])
        self.assertEqual(report["deferred_reviews"], 0)
        self.assertEqual(
            report["profile_review_skips"],
            [
                {"session_id": "fake:one", "code": "no-reusable-profile"},
                {"session_id": "fake:two", "code": "no-reusable-profile"},
            ],
        )
        ledger = Path(environment["DREAMING_STATE_DIR"]) / "review-ledger.json"
        self.assertFalse(ledger.exists())
        attempts = Path(environment["DREAMING_STATE_DIR"]) / "review-attempts.json"
        self.assertFalse(attempts.exists())
        queue = Path(environment["DREAMING_STATE_DIR"]) / "queue.json"
        queued = json.loads(queue.read_text())
        self.assertEqual(
            [item["qualified_session_id"] for item in queued if item["status"] == "queued"],
            [],
        )
        self.assertEqual(
            [item["status"] for item in queued],
            ["profile-audited", "profile-audited"],
        )

        recovery = (
            Path(environment["DREAMING_STATE_DIR"])
            / "publication-recovery-required.json"
        )
        recovery.write_text('{"status":"publication_recovery_required"}')
        blocked = subprocess.run(
            [sys.executable, str(RUNTIME_PATH), "run"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(blocked.returncode, 0)
        blocked_report = json.loads(blocked.stdout)
        self.assertFalse(blocked_report["ok"])
        self.assertTrue(blocked_report["publication_recovery_required"])
        self.assertEqual(
            blocked_report["errors"],
            [{"phase": "publication-recovery", "code": "recovery-required"}],
        )
        failed_selftest = subprocess.run(
            [sys.executable, str(RUNTIME_PATH), "selftest"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(failed_selftest.returncode, 0)
        self.assertIn("publication-recovery-required", failed_selftest.stdout)
        recovery.unlink()

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

    def test_repeated_runs_remain_bounded_at_twenty_five(self) -> None:
        sessions = [self.session(f"session-{index:02d}", index + 1) for index in range(52)]
        source_fixture = self.source_fixture(sessions)
        executor_fixture = self.write(
            "bounded-executor.json",
            {
                "mode": "success",
                "task_profiles": [
                    {
                        "task_type": "bounded-reusable-procedure",
                        "abstract_summary": "A bounded reusable procedure.",
                        "reuse_value": "reusable-procedure",
                        "procedure": {
                            "trigger": "A bounded task is complete.",
                            "outcome": "Its procedure is retained.",
                            "actions": ["Retain the procedure."],
                            "exclusions": ["Do not retain source details."],
                        },
                    }
                ],
            },
        )
        config = self.write(
            "bounded-adapters.json",
            {
                "contract_version": 1,
                "max_reviews_per_run": 25,
                "max_profiles_per_run": 20,
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
                "publishers": {},
            },
        )
        environment = {
            **os.environ,
            "DREAMING_ADAPTER_CONFIG": str(config),
            "DREAMING_DATA_DIR": str(self.case / "bounded-data"),
            "DREAMING_STATE_DIR": str(self.case / "bounded-state"),
        }
        observed = []
        for _ in range(3):
            result = subprocess.run(
                [sys.executable, str(RUNTIME_PATH), "run"],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            report = json.loads(result.stdout)
            observed.append(
                (
                    len(report["profiles"]),
                    report["deferred_profiles"],
                    len(report["reviews"]),
                    report["deferred_reviews"],
                )
            )
        self.assertEqual(
            observed,
            [(20, 32, 20, 0), (20, 12, 20, 0), (12, 0, 12, 0)],
        )
        dispositions = list(
            (
                Path(environment["DREAMING_STATE_DIR"])
                / "profile-audit-dispositions"
                / "v1"
            ).glob("*.json")
        )
        self.assertEqual(len(dispositions), 52)
        self.assertEqual(
            len({json.loads(row.read_text())["profile_id"] for row in dispositions}),
            52,
        )

    def test_review_limit_above_twenty_five_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeFailure, "max_reviews_per_run"):
            runtime_module.configured_runtime_settings(
                {"max_reviews_per_run": 26}
            )

    def test_profile_limit_is_independent_and_bounded(self) -> None:
        settings = runtime_module.configured_runtime_settings(
            {
                "max_reviews_per_run": 25,
                "max_profiles_per_run": 100,
                "max_profile_elapsed_seconds": 600,
            }
        )
        self.assertEqual(settings["max_reviews_per_run"], 25)
        self.assertEqual(settings["max_profiles_per_run"], 100)
        self.assertEqual(settings["max_profile_elapsed_seconds"], 600)
        self.assertEqual(
            runtime_module.profile_budget_reason(
                100, 100.0, settings, now=100.0
            ),
            "session-limit",
        )
        self.assertEqual(
            runtime_module.profile_budget_reason(
                99, 100.0, settings, now=700.0
            ),
            "elapsed-time-limit",
        )
        self.assertIsNone(
            runtime_module.profile_budget_reason(
                99, 100.0, settings, now=699.0
            )
        )
        with self.assertRaisesRegex(RuntimeFailure, "max_profiles_per_run"):
            runtime_module.configured_runtime_settings(
                {"max_profiles_per_run": 501}
            )
        with self.assertRaisesRegex(
            RuntimeFailure, "max_profile_elapsed_seconds"
        ):
            runtime_module.configured_runtime_settings(
                {"max_profile_elapsed_seconds": 1801}
            )

    def test_task_pass_accounting_reconciles_and_fails_closed(self) -> None:
        accounting = sys.modules["task_pass_accounting"]
        queue_rows = [
            {
                "queue_row_id": f"queue-{index}",
                "outcome": outcome,
                "profile_operation_id": operation,
            }
            for index, (outcome, operation) in enumerate(
                [
                    ("newly-attempted", "profile-one"),
                    ("malformed", "profile-two"),
                    ("profile-failed", "profile-three"),
                    ("cached-current-receipt", None),
                    ("stale-superseded", None),
                    ("eligible-deferred", None),
                ],
                1,
            )
        ]
        receipt = accounting.build_task_pass_accounting_receipt(
            pass_id="fixture-pass",
            queue_rows=queue_rows,
            profile_operations=[
                {
                    "operation_id": "profile-one",
                    "queue_row_id": "queue-1",
                    "terminal": "profiled",
                },
                {
                    "operation_id": "profile-two",
                    "queue_row_id": "queue-2",
                    "terminal": "malformed",
                },
                {
                    "operation_id": "profile-three",
                    "queue_row_id": "queue-3",
                    "terminal": "failed",
                },
            ],
            profiles=[
                {
                    "profile_id": "profile-reusable",
                    "queue_row_id": "queue-1",
                    "profile_receipt_sha256": "sha256:" + "1" * 64,
                    "terminal": "reusable-dispositioned",
                },
                {
                    "profile_id": "profile-no-learning",
                    "queue_row_id": "queue-4",
                    "profile_receipt_sha256": "sha256:" + "2" * 64,
                    "terminal": "no-learning",
                },
            ],
            review_rows=[
                {
                    "review_row_id": "review-reusable",
                    "queue_row_id": "queue-1",
                    "profile_id": "profile-reusable",
                    "profile_receipt_sha256": "sha256:" + "1" * 64,
                    "outcome": "newly-attempted",
                    "operation_id": "review-one",
                },
                {
                    "review_row_id": "review-no-learning",
                    "queue_row_id": "queue-4",
                    "profile_id": "profile-no-learning",
                    "profile_receipt_sha256": "sha256:" + "2" * 64,
                    "outcome": "no-learning",
                    "operation_id": None,
                },
                {
                    "review_row_id": "review-deferred",
                    "queue_row_id": "queue-6",
                    "profile_id": "profile-reusable",
                    "profile_receipt_sha256": "sha256:" + "1" * 64,
                    "outcome": "eligible-deferred",
                    "operation_id": None,
                },
            ],
            review_operations=[
                {
                    "operation_id": "review-one",
                    "profile_id": "profile-reusable",
                    "profile_receipt_sha256": "sha256:" + "1" * 64,
                    "terminal": "dispositioned",
                }
            ],
            review_terminals=[
                {
                    "operation_id": "review-one",
                    "profile_id": "profile-reusable",
                    "profile_receipt_sha256": "sha256:" + "1" * 64,
                    "terminal": "dispositioned",
                }
            ],
            profile_stop_reason="session-limit",
            review_stop_reason="review-operation-limit",
        )
        self.assertEqual(
            accounting.validate_task_pass_accounting_receipt(receipt), receipt
        )
        unstarted_attempt = {
            **receipt,
            "review_rows": [
                {
                    **row,
                    "operation_id": None,
                }
                if row["outcome"] == "newly-attempted"
                else row
                for row in receipt["review_rows"]
            ],
        }
        unstarted_attempt["receipt_sha256"] = accounting.digest(
            {
                key: value
                for key, value in unstarted_attempt.items()
                if key != "receipt_sha256"
            }
        )
        with self.assertRaisesRegex(
            accounting.TaskPassAccountingError, "review-row-terminal-invalid"
        ):
            accounting.validate_task_pass_accounting_receipt(unstarted_attempt)
        missing_terminal = accounting.build_task_pass_accounting_receipt(
            **{
                key: value
                for key, value in receipt.items()
                if key
                not in {
                    "schema_version",
                    "kind",
                    "totals",
                    "receipt_sha256",
                    "review_terminals",
                }
            },
            review_terminals=[],
        )
        with self.assertRaisesRegex(
            accounting.TaskPassAccountingError, "review-operation-terminal-unmatched"
        ):
            accounting.validate_task_pass_accounting_receipt(missing_terminal)
        duplicate_cached = accounting.build_task_pass_accounting_receipt(
            **{
                key: value
                for key, value in receipt.items()
                if key
                not in {
                    "schema_version",
                    "kind",
                    "totals",
                    "receipt_sha256",
                    "queue_rows",
                }
            },
            queue_rows=[
                *queue_rows,
                {
                    "queue_row_id": "queue-4",
                    "outcome": "cached-current-receipt",
                    "profile_operation_id": None,
                },
            ],
        )
        with self.assertRaisesRegex(
            accounting.TaskPassAccountingError, "queue-identity"
        ):
            accounting.validate_task_pass_accounting_receipt(duplicate_cached)

    def test_malformed_profile_is_retained_without_failing_run(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 1)])
        executor_fixture = self.write(
            "malformed-profile-executor.json",
            {
                "mode": "success",
                "task_profile_error": {
                    "code": "malformed-executor-result",
                    "message": "duplicate task profile evidence",
                },
            },
        )
        config = self.write(
            "malformed-profile-adapters.json",
            {
                "contract_version": 1,
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
                "publishers": {},
            },
        )
        environment = {
            **os.environ,
            "DREAMING_ADAPTER_CONFIG": str(config),
            "DREAMING_DATA_DIR": str(self.case / "malformed-profile-data"),
            "DREAMING_STATE_DIR": str(self.case / "malformed-profile-state"),
        }
        result = subprocess.run(
            [sys.executable, str(RUNTIME_PATH), "run"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        report = json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(report["ok"])
        self.assertEqual(report["profiles"], [])
        self.assertEqual(
            report["profile_failures"],
            [
                {
                    "session_id": "fake:one",
                    "code": "malformed-executor-result",
                    "message": "duplicate task profile evidence",
                }
            ],
        )
        self.assertEqual(report["reviews"], [])

    def test_manual_runtime_ignores_scheduler_only_limits(self) -> None:
        runtime = runtime_module.configured_runtime(
            self.paths,
            {("fake", "executor")},
            {
                "policy_version": 7,
                "max_reviews_per_run": 26,
                "max_profiles_per_run": 501,
            },
        )
        self.assertEqual(runtime.policy_version, 7)
        with self.assertRaisesRegex(RuntimeFailure, "max_reviews_per_run"):
            runtime_module.configured_runtime_settings(
                {"max_reviews_per_run": 26}
            )

    def test_stale_task_profile_receipt_is_replaced(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 1)])
        executor_fixture = self.write("profile-refresh-executor.json", {})
        source = self.adapter("session-source", "fake", source_fixture)
        executor = self.adapter(
            "review-executor", "profile-refresh-executor", executor_fixture
        )
        first_core = DreamingRuntime(
            self.paths,
            {("fake", "profile-refresh-executor")},
            policy_version=2,
            now=lambda: self.clock,
        )
        first = first_core.profile(
            "fake",
            source,
            "fake:one",
            "profile-refresh-executor",
            executor,
        )
        refreshed_core = DreamingRuntime(
            self.paths,
            {("fake", "profile-refresh-executor")},
            policy_version=7,
            now=lambda: self.clock,
        )
        snapshot_path, identity = refreshed_core.render_snapshot(
            "fake",
            source,
            "profile-refresh-executor",
            "fake:one",
        )
        binding = refreshed_core.task_profile_binding_for(
            "fake:one",
            identity["source_revision"],
            "profile-refresh-executor",
            snapshot_path,
            executor.identity,
        )
        self.assertEqual(binding.status, "absent")
        refreshed = refreshed_core.profile(
            "fake",
            source,
            "fake:one",
            "profile-refresh-executor",
            executor,
        )
        self.assertNotEqual(
            refreshed["receipt_sha256"], first["receipt_sha256"]
        )
        self.assertEqual(
            refreshed_core.indexed_task_profile_receipt_for(
                "fake:one",
                identity["source_revision"],
                "profile-refresh-executor",
                current_contract=False,
                receipt_sha256=first["receipt_sha256"],
            ).path,
            Path(first["receipt"]),
        )
        self.assertEqual(
            refreshed_core.indexed_task_profile_receipt_for(
                "fake:one",
                identity["source_revision"],
                "profile-refresh-executor",
                current_contract=False,
                receipt_sha256=refreshed["receipt_sha256"],
            ).path,
            Path(refreshed["receipt"]),
        )
        binding = refreshed_core.task_profile_binding_for(
            "fake:one",
            identity["source_revision"],
            "profile-refresh-executor",
            snapshot_path,
            executor.identity,
        )
        self.assertEqual(binding.status, "bound")
        self.assertEqual(
            binding.receipt.path if binding.receipt is not None else None,
            Path(refreshed["receipt"]),
        )

    def test_executor_identity_drift_refreshes_task_profile_receipt(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 1)])
        executor_fixture = self.write("profile-identity-refresh.json", {})
        source = self.adapter("session-source", "fake", source_fixture)
        executor = self.adapter(
            "review-executor", "profile-identity-refresh", executor_fixture
        )
        core = self.core({("fake", "profile-identity-refresh")})
        first = core.profile(
            "fake",
            source,
            "fake:one",
            "profile-identity-refresh",
            executor,
        )
        fixture = json.loads(executor_fixture.read_text())
        fixture["capabilities"] = [
            *executor.identity["capabilities"],
            "new-compatible-capability",
        ]
        executor_fixture.write_text(json.dumps(fixture))
        upgraded = self.adapter(
            "review-executor", "profile-identity-refresh", executor_fixture
        )
        snapshot_path, identity = core.render_snapshot(
            "fake",
            source,
            "profile-identity-refresh",
            "fake:one",
        )
        binding = core.task_profile_binding_for(
            "fake:one",
            identity["source_revision"],
            "profile-identity-refresh",
            snapshot_path,
            upgraded.identity,
        )
        self.assertEqual(binding.status, "unbound")
        self.assertEqual(binding.reason, "executor-identity")
        refreshed = core.profile(
            "fake",
            source,
            "fake:one",
            "profile-identity-refresh",
            upgraded,
        )
        self.assertNotEqual(
            refreshed["receipt_sha256"], first["receipt_sha256"]
        )

    def test_legacy_executor_is_not_given_indexed_profile_receipt(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 1)])
        executor_fixture = self.write("capability-change-executor.json", {})
        source = self.adapter("session-source", "fake", source_fixture)
        capable = self.adapter(
            "review-executor", "capability-change-executor", executor_fixture
        )
        core = self.core({("fake", "capability-change-executor")})
        core.profile(
            "fake",
            source,
            "fake:one",
            "capability-change-executor",
            capable,
        )
        fixture = json.loads(executor_fixture.read_text())
        fixture["capabilities"] = [
            "source-blind",
            "mutation-fence",
            "completion-sentinel",
        ]
        fixture["reject_task_profile_receipt"] = True
        executor_fixture.write_text(json.dumps(fixture))
        legacy = self.adapter(
            "review-executor", "capability-change-executor", executor_fixture
        )
        result = core.review(
            "fake",
            source,
            "fake:one",
            [("capability-change-executor", legacy)],
        )
        self.assertEqual(result["status"], "accepted")
        attempt = json.loads(self.paths.attempts.read_text())[-1]
        self.assertEqual(
            attempt["task_profile_delivery"],
            "unsupported:profiled-by-other-executor",
        )
        self.assertNotIn("task_profile_receipt_sha256", attempt)

    def test_legacy_review_executor_is_not_used_for_profiling(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 1)])
        executor_fixture = self.write(
            "legacy-executor.json",
            {
                "mode": "success",
                "capabilities": [
                    "source-blind",
                    "mutation-fence",
                    "completion-sentinel",
                ],
            },
        )
        config = self.write(
            "legacy-adapters.json",
            {
                "contract_version": 1,
                "routes": ["fake>legacy-executor"],
                "executor_order": ["legacy-executor"],
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
                    "legacy-executor": {
                        "argv": [
                            sys.executable,
                            str(FAKE),
                            "--fixture",
                            str(executor_fixture),
                            "--adapter-id",
                            "legacy-executor",
                            "--role",
                            "review-executor",
                        ]
                    }
                },
                "publishers": {},
            },
        )
        result = subprocess.run(
            [sys.executable, str(RUNTIME_PATH), "run"],
            env={
                **os.environ,
                "DREAMING_ADAPTER_CONFIG": str(config),
                "DREAMING_DATA_DIR": str(self.case / "legacy-data"),
                "DREAMING_STATE_DIR": str(self.case / "legacy-state"),
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        report = json.loads(result.stdout)
        self.assertEqual(report["profiles"], [])
        self.assertEqual(
            report["profile_skips"],
            [
                {
                    "session_id": "fake:one",
                    "code": "no-profile-capable-executor",
                }
            ],
        )
        self.assertEqual(report["reviews"], [])

    def test_profile_command_selects_profile_capable_executor(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 1)])
        legacy_fixture = self.write(
            "alpha-legacy.json",
            {
                "capabilities": [
                    "source-blind",
                    "mutation-fence",
                    "completion-sentinel",
                ],
            },
        )
        profiler_fixture = self.write("zeta-profiler.json", {})
        config = self.write(
            "mixed-profile-adapters.json",
            {
                "contract_version": 1,
                "policy_version": 7,
                "routes": [
                    "fake>alpha-legacy",
                    "fake>zeta-profiler",
                ],
                "executor_order": ["alpha-legacy", "zeta-profiler"],
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
                    name: {
                        "argv": [
                            sys.executable,
                            str(FAKE),
                            "--fixture",
                            str(fixture),
                            "--adapter-id",
                            name,
                            "--role",
                            "review-executor",
                        ]
                    }
                    for name, fixture in (
                        ("alpha-legacy", legacy_fixture),
                        ("zeta-profiler", profiler_fixture),
                    )
                },
                "publishers": {},
            },
        )
        result = subprocess.run(
            [
                sys.executable,
                str(RUNTIME_PATH),
                "profile",
                "--source",
                "fake",
                "--session",
                "fake:one",
            ],
            env={
                **os.environ,
                "DREAMING_ADAPTER_CONFIG": str(config),
                "DREAMING_DATA_DIR": str(self.case / "mixed-data"),
                "DREAMING_STATE_DIR": str(self.case / "mixed-state"),
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        receipt = json.loads(
            Path(json.loads(result.stdout)["receipt"]).read_text()
        )
        self.assertEqual(receipt["executor"], "zeta-profiler")
        snapshot = json.loads(
            (
                self.case
                / "mixed-data"
                / "snapshots"
                / (
                    receipt["snapshot_sha256"].removeprefix("sha256:")
                    + ".json"
                )
            ).read_text()
        )
        self.assertEqual(snapshot["route"]["policy_version"], 7)

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
        self.initialize_git_repo()
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
        invalid["overlap_seconds"] = 5
        invalid["allow_autonomous_skill_creation"] = True
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
        invalid["allow_autonomous_skill_creation"] = False
        invalid["max_autonomous_session_age_days"] = 31
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
        invalid["max_autonomous_session_age_days"] = 30
        invalid["allow_autonomous_skill_creation"] = "false"
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
        self.assertEqual(report["reviews"], [])
        self.assertEqual(len(report["profiles"]), 1)
        self.assertEqual(report["errors"][0]["adapter"], "offline")
        self.assertFalse(self.paths.ledger.exists())

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
        attempts = json.loads(self.paths.attempts.read_text())
        self.assertEqual(attempts[0]["status"], "failed-before-mutation")
        self.assertEqual(attempts[0]["task_profile_delivery"], "unavailable")
        self.assertEqual(attempts[1]["task_profile_delivery"], "unavailable")
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

    def test_missing_queued_session_is_retired(self) -> None:
        source = self.adapter(
            "session-source", "fake", self.source_fixture([])
        )
        executor = self.adapter(
            "review-executor",
            "exec",
            self.write("missing-executor.json", {"mode": "success"}),
        )
        core = self.core({("fake", "exec")})
        core._write(
            self.paths.queue,
            [
                {
                    "qualified_session_id": "fake:missing",
                    "source_revision": "missing-revision",
                    "status": "queued",
                }
            ],
        )
        result = core.review(
            "fake",
            source,
            "fake:missing",
            [("exec", executor)],
            expected_revision="missing-revision",
        )
        self.assertEqual(result, {"status": "deleted"})
        self.assertEqual(
            core._state(self.paths.queue, [])[0]["status"], "deleted"
        )

    def test_unrelated_missing_record_does_not_retire_queued_session(self) -> None:
        class UnrelatedMissingSource:
            def call(self, command: str, **arguments: object) -> dict:
                self.assert_inspect(command, arguments)
                raise RuntimeFailure(
                    "session-missing", "/missing/unrelated-rollout.jsonl"
                )

            @staticmethod
            def assert_inspect(command: str, arguments: dict[str, object]) -> None:
                if command != "inspect" or arguments != {"session": "fake:one"}:
                    raise AssertionError((command, arguments))

        executor = self.adapter(
            "review-executor",
            "exec",
            self.write("unrelated-missing-executor.json", {"mode": "success"}),
        )
        core = self.core({("fake", "exec")})
        core._write(
            self.paths.queue,
            [
                {
                    "qualified_session_id": "fake:one",
                    "source_revision": "queued-revision",
                    "status": "queued",
                }
            ],
        )
        with self.assertRaisesRegex(
            RuntimeFailure, "/missing/unrelated-rollout.jsonl"
        ):
            core.review(
                "fake",
                UnrelatedMissingSource(),
                "fake:one",
                [("exec", executor)],
                expected_revision="queued-revision",
            )
        self.assertEqual(
            core._state(self.paths.queue, [])[0]["status"], "queued"
        )

    def test_missing_queued_session_with_transaction_requires_recovery(self) -> None:
        source = self.adapter(
            "session-source", "fake", self.source_fixture([])
        )
        executor = self.adapter(
            "review-executor",
            "exec",
            self.write("missing-transaction-executor.json", {"mode": "success"}),
        )
        core = self.core({("fake", "exec")})
        core._write(
            self.paths.queue,
            [
                {
                    "qualified_session_id": "fake:done",
                    "source_revision": "done-revision",
                    "status": "reviewed",
                },
                {
                    "qualified_session_id": "fake:missing",
                    "source_revision": "missing-revision",
                    "status": "queued",
                },
                {
                    "qualified_session_id": "fake:later",
                    "source_revision": "later-revision",
                    "status": "queued",
                },
            ],
        )
        core._write_transaction(
            "fake:missing",
            "missing-revision",
            {
                "session_id": "fake:missing",
                "source_revision": "missing-revision",
                "phase": "mutation-started",
            },
        )
        with self.assertRaisesRegex(
            RuntimeFailure, "mutation-recovery-required"
        ):
            core.review(
                "fake",
                source,
                "fake:missing",
                [("exec", executor)],
                expected_revision="missing-revision",
            )
        statuses = {
            row["qualified_session_id"]: row["status"]
            for row in core._state(self.paths.queue, [])
        }
        self.assertEqual(
            statuses,
            {
                "fake:done": "reviewed",
                "fake:missing": "recovery-required",
                "fake:later": "queued",
            },
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

    def test_autonomous_create_is_deferred_without_mutation(self) -> None:
        fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", fixture)
        starting_head = self.init_skills_repo()
        executor_fixture = self.write(
            "deferred-create.json",
            {
                "mode": "success",
                "terminal_route": "skill",
                "summary": "A reusable fixture procedure was demonstrated",
                "routing_reason": "The procedure has ordered reusable steps",
                "artifact": {
                    "operation": "create",
                    "skill_name": "deferred-procedure",
                    "skill_markdown": (
                        "---\nname: deferred-procedure\n"
                        "description: Run the deferred fixture procedure\n---\n"
                        "# Deferred procedure\n"
                    ),
                    "support_files": [],
                },
            },
        )
        executor = self.adapter("review-executor", "exec", executor_fixture)
        core = DreamingRuntime(
            self.paths,
            {("fake", "exec")},
            allow_autonomous_skill_creation=False,
            now=lambda: self.clock,
        )
        lock_environment = {
            **os.environ,
            "SKILLS_STATE_DIR": str(self.paths.state),
            "SKILLS_NOW_EPOCH": str(self.clock),
        }
        acquired = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "daemon-lock.py"),
                "acquire",
                "--mode",
                "session",
                "--owner",
                "test-shadow-candidate",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            env=lock_environment,
        ).stdout.strip()
        prior_token = os.environ.get("SKILLS_LOCK_TOKEN")
        prior_state = os.environ.get("SKILLS_STATE_DIR")
        os.environ["SKILLS_LOCK_TOKEN"] = acquired
        os.environ["SKILLS_STATE_DIR"] = str(self.paths.state)
        try:
            result = core.review(
                "fake", source, "fake:one", [("exec", executor)]
            )
        finally:
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "daemon-lock.py"),
                    "release",
                    acquired,
                ],
                check=True,
                env=lock_environment,
            )
            if prior_token is None:
                os.environ.pop("SKILLS_LOCK_TOKEN", None)
            else:
                os.environ["SKILLS_LOCK_TOKEN"] = prior_token
            if prior_state is None:
                os.environ.pop("SKILLS_STATE_DIR", None)
            else:
                os.environ["SKILLS_STATE_DIR"] = prior_state
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(result["terminal_route"], "discard")
        self.assertFalse(result["artifact_mutated"])
        self.assertEqual(
            result["policy_deferred"]["reason"],
            "autonomous-create-requires-recurrence",
        )
        self.assertFalse((self.paths.skills / "deferred-procedure").exists())
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(self.paths.skills), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
            starting_head,
        )
        retained = list((self.paths.state / "results").glob("*.json"))
        self.assertEqual(len(retained), 1)
        original = json.loads(retained[0].read_text())
        self.assertEqual(original["terminal_route"], "skill")
        self.assertEqual(original["artifact"]["operation"], "create")
        self.assertFalse((self.paths.state / "draft-reviews").exists())
        ledger = json.loads(self.paths.ledger.read_text())[0]
        self.assertEqual(ledger["terminal_route"], "discard")
        self.assertEqual(
            ledger["policy_deferred"]["skill_name"], "deferred-procedure"
        )
        shadow = ledger["policy_deferred"]["shadow_candidate"]
        self.assertTrue(shadow["shadow_only"])
        self.assertEqual(shadow["state"], "collecting")
        self.assertEqual(shadow["recommendation"], "collecting")
        lifecycle_id = shadow["lifecycle_id"]
        record_path = (
            self.paths.state
            / "skill-review"
            / "candidates"
            / "v1"
            / "records"
            / f"{lifecycle_id}.json"
        )
        record = json.loads(record_path.read_text())
        self.assertEqual(record["state"], "collecting")
        self.assertEqual(record["evidence"][0]["independence"], "unverified")
        package = (
            self.paths.data
            / "candidates"
            / "v1"
            / "packages"
            / lifecycle_id
            / shadow["candidate_id"]
        )
        self.assertEqual(
            (package / "SKILL.md").read_text(),
            (
                "---\nname: deferred-procedure\n"
                "description: Run the deferred fixture procedure\n---\n"
                "# Deferred procedure\n"
            ),
        )

    def test_profiled_create_uses_verified_task_observation(self) -> None:
        fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", fixture)
        self.init_skills_repo()
        procedure = {
            "trigger": "A reusable fixture procedure is demonstrated.",
            "outcome": "The fixture procedure is retained.",
            "actions": ["Identify the procedure.", "Retain its ordered steps."],
            "exclusions": ["Do not copy source-specific details."],
        }
        executor_fixture = self.write(
            "profiled-create.json",
            {
                "mode": "success",
                "terminal_route": "skill",
                "summary": "A reusable fixture procedure was demonstrated",
                "routing_reason": "The procedure has ordered reusable steps",
                "require_task_profile_context": True,
                "task_profiles": [
                    {
                        "source_event_ids": ["one-event-1"],
                        "task_type": "retain-fixture-procedure",
                        "abstract_summary": (
                            "Retain a reusable procedure from completed work."
                        ),
                        "reuse_value": "reusable-procedure",
                        "procedure": procedure,
                    }
                ],
                "artifact": {
                    "operation": "create",
                    "skill_name": "profiled-procedure",
                    "skill_markdown": (
                        "---\nname: profiled-procedure\n"
                        "description: Run the profiled fixture procedure\n---\n"
                        "# Profiled procedure\n"
                    ),
                    "support_files": [],
                },
            },
        )
        executor = self.adapter("review-executor", "exec", executor_fixture)
        core = DreamingRuntime(
            self.paths,
            {("fake", "exec")},
            allow_autonomous_skill_creation=False,
            now=lambda: self.clock,
        )
        profiled = core.profile(
            "fake", source, "fake:one", "exec", executor
        )
        self.assertEqual(profiled["profile_count"], 1)
        lock_environment = {
            **os.environ,
            "SKILLS_STATE_DIR": str(self.paths.state),
            "SKILLS_NOW_EPOCH": str(self.clock),
        }
        token = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "daemon-lock.py"),
                "acquire",
                "--mode",
                "session",
                "--owner",
                "test-profiled-create",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            env=lock_environment,
        ).stdout.strip()
        prior_token = os.environ.get("SKILLS_LOCK_TOKEN")
        prior_state = os.environ.get("SKILLS_STATE_DIR")
        os.environ["SKILLS_LOCK_TOKEN"] = token
        os.environ["SKILLS_STATE_DIR"] = str(self.paths.state)
        try:
            result = core.review(
                "fake", source, "fake:one", [("exec", executor)]
            )
        finally:
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "daemon-lock.py"),
                    "release",
                    token,
                ],
                check=True,
                env=lock_environment,
            )
            if prior_token is None:
                os.environ.pop("SKILLS_LOCK_TOKEN", None)
            else:
                os.environ["SKILLS_LOCK_TOKEN"] = prior_token
            if prior_state is None:
                os.environ.pop("SKILLS_STATE_DIR", None)
            else:
                os.environ["SKILLS_STATE_DIR"] = prior_state
        shadow = result["policy_deferred"]["shadow_candidate"]
        self.assertEqual(
            result["policy_deferred"]["reason"],
            "task-profile-artifact-requires-evaluation",
        )
        attempt = json.loads(self.paths.attempts.read_text())[-1]
        self.assertEqual(attempt["task_profile_delivery"], "delivered")
        self.assertEqual(
            attempt["task_profile_receipt_sha256"],
            profiled["receipt_sha256"],
        )
        self.assertEqual(shadow["profile_match"], "matched")
        self.assertEqual(shadow["independence"], "verified")
        self.assertEqual(
            shadow["profile_id"],
            json.loads(Path(profiled["receipt"]).read_text())["profiles"][0][
                "profile_id"
            ],
        )
        record = core._candidate_lifecycle_call(
            "read", shadow["lifecycle_id"]
        )
        self.assertEqual(record["evidence"][0]["independence"], "verified")
        self.assertEqual(
            record["procedure"],
            core._candidate_procedure(
                {
                    "skill_name": "profiled-procedure",
                    "skill_markdown": (
                        "---\nname: profiled-procedure\n"
                        "description: Run the profiled fixture procedure\n---\n"
                        "# Profiled procedure\n"
                    ),
                }
            ),
        )

        second_profile = {
            **json.loads(Path(profiled["receipt"]).read_text())["profiles"][0],
            "task_key": "sha256:" + "b" * 64,
            "abstract_summary": "Apply the same skill to independently worded work.",
            "procedure": {
                "trigger": "A differently worded but equivalent task appears.",
                "outcome": "The equivalent task reaches the same result.",
                "actions": ["Recognize the equivalent task.", "Apply the shared procedure."],
                "exclusions": ["Do not merge unrelated work."],
            },
        }
        second_profile["procedure_fingerprint"] = runtime_module.digest(
            second_profile["procedure"]
        )
        lock_environment = {
            **os.environ,
            "SKILLS_STATE_DIR": str(self.paths.state),
            "SKILLS_NOW_EPOCH": str(self.clock),
        }
        token = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "daemon-lock.py"),
                "acquire",
                "--mode",
                "session",
                "--owner",
                "test-profiled-recurrence",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            env=lock_environment,
        ).stdout.strip()
        prior_token = os.environ.get("SKILLS_LOCK_TOKEN")
        prior_state = os.environ.get("SKILLS_STATE_DIR")
        os.environ["SKILLS_LOCK_TOKEN"] = token
        os.environ["SKILLS_STATE_DIR"] = str(self.paths.state)
        try:
            second = core._collect_shadow_candidate(
                {
                    "summary": "The same reusable skill applies",
                    "artifact": {
                        "operation": "create",
                        "skill_name": "profiled-procedure",
                        "skill_markdown": (
                            "---\nname: profiled-procedure\n"
                            "description: Run the profiled fixture procedure\n---\n"
                            "# Profiled procedure\n"
                        ),
                        "support_files": [],
                    },
                },
                {
                    "qualified_session_id": "fake:two",
                    "updated_at": self.clock + 1,
                },
                second_profile,
                "matched",
            )
        finally:
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "daemon-lock.py"),
                    "release",
                    token,
                ],
                check=True,
                env=lock_environment,
            )
            if prior_token is None:
                os.environ.pop("SKILLS_LOCK_TOKEN", None)
            else:
                os.environ["SKILLS_LOCK_TOKEN"] = prior_token
            if prior_state is None:
                os.environ.pop("SKILLS_STATE_DIR", None)
            else:
                os.environ["SKILLS_STATE_DIR"] = prior_state
        self.assertEqual(second["lifecycle_id"], shadow["lifecycle_id"])
        self.assertEqual(second["recommendation"], "ready_for_draft")
        record = core._candidate_lifecycle_call(
            "read", shadow["lifecycle_id"]
        )
        self.assertEqual(
            [item["independence"] for item in record["evidence"]],
            ["verified", "verified"],
        )
        self.assertEqual(
            len({item["procedure_fingerprint"] for item in record["evidence"]}),
            1,
        )

    def test_profiled_patch_is_shadowed_before_mutation(self) -> None:
        core = DreamingRuntime(
            self.paths,
            {("fake", "exec")},
            allow_autonomous_skill_creation=False,
            now=lambda: self.clock,
        )
        result = {
            "terminal_route": "skill",
            "summary": "Repair a reusable fixture procedure",
            "routing_reason": "The existing skill needs the observed procedure",
            "artifact": {
                "operation": "patch",
                "skill_name": "existing-procedure",
                "skill_markdown": (
                    "---\nname: existing-procedure\n"
                    "description: Run the existing fixture procedure\n---\n"
                    "# Existing procedure\n"
                ),
                "support_files": [],
            },
            "evidence_event_ids": ["one-event-1"],
            "transcript_context": {
                "snapshot_sha256": "snapshot",
                "source_revision": "revision",
                "event_ids": ["one-event-1"],
            },
        }
        reviewed_identity = {
            "qualified_session_id": "fake:one",
            "source_revision": "sha256:" + "1" * 64,
            "updated_at": datetime.fromtimestamp(
                self.clock, timezone.utc
            ).isoformat(),
        }
        with (
            mock.patch.object(
                core,
                "_matching_task_profile",
                return_value=(None, "no-exact-match"),
            ),
            mock.patch.object(
                core,
                "_collect_shadow_candidate",
                return_value={"shadow_only": True, "state": "collecting"},
            ) as collect,
        ):
            deferred = core._apply_autonomous_admission_policy(
                result,
                reviewed_identity,
                self.case / "receipt.json",
            )
        self.assertEqual(deferred["terminal_route"], "discard")
        self.assertIsNone(deferred["artifact"])
        self.assertEqual(
            deferred["policy_deferred"]["reason"],
            "task-profile-artifact-requires-evaluation",
        )
        self.assertEqual(
            deferred["policy_deferred"]["original_operation"], "patch"
        )
        collect.assert_called_once()

    def test_profile_gate_remains_active_without_current_executor_receipt(self) -> None:
        core = DreamingRuntime(
            self.paths,
            {("fake", "legacy")},
            allow_autonomous_skill_creation=False,
            now=lambda: self.clock,
        )
        result = {
            "terminal_route": "skill",
            "summary": "Repair a reusable fixture procedure",
            "routing_reason": "The existing skill needs the observed procedure",
            "artifact": {
                "operation": "patch",
                "skill_name": "existing-procedure",
                "skill_markdown": (
                    "---\nname: existing-procedure\n"
                    "description: Run the existing fixture procedure\n---\n"
                    "# Existing procedure\n"
                ),
                "support_files": [],
            },
            "evidence_event_ids": ["one-event-1"],
            "transcript_context": {
                "snapshot_sha256": "snapshot",
                "source_revision": "revision",
                "event_ids": ["one-event-1"],
            },
        }
        reviewed_identity = {
            "qualified_session_id": "fake:one",
            "source_revision": "sha256:" + "1" * 64,
            "updated_at": datetime.fromtimestamp(
                self.clock, timezone.utc
            ).isoformat(),
        }
        with mock.patch.object(
            core,
            "_collect_shadow_candidate",
            return_value={"shadow_only": True, "state": "collecting"},
        ) as collect:
            deferred = core._apply_autonomous_admission_policy(
                result,
                reviewed_identity,
                task_profile_evidence_present=True,
            )
        self.assertEqual(deferred["terminal_route"], "discard")
        self.assertEqual(
            deferred["policy_deferred"]["reason"],
            "task-profile-artifact-requires-evaluation",
        )
        collect.assert_called_once()

    def test_shadow_support_file_candidate_contains_complete_package(self) -> None:
        core = DreamingRuntime(
            self.paths,
            {("fake", "exec")},
            allow_autonomous_skill_creation=False,
            now=lambda: self.clock,
        )
        skill = self.paths.skills / "existing-procedure"
        (skill / "references").mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: existing-procedure\n"
            "description: Run the existing fixture procedure\n---\n"
            "# Existing procedure\n"
        )
        (skill / "references" / "existing.md").write_text("existing\n")
        (skill / ".agent-created.json").write_text("{}\n")
        result = {
            "terminal_route": "support_file",
            "summary": "Add a reusable fixture reference",
            "routing_reason": "The existing skill needs supporting detail",
            "artifact": {
                "operation": "support_file",
                "skill_name": "existing-procedure",
                "skill_markdown": (skill / "SKILL.md").read_text(),
                "support_files": [
                    {
                        "path": "references/new.md",
                        "content": "new\n",
                    }
                ],
            },
            "evidence_event_ids": ["one-event-1"],
        }
        reviewed_identity = {
            "qualified_session_id": "fake:one",
            "source_revision": "sha256:" + "1" * 64,
            "updated_at": datetime.fromtimestamp(
                self.clock, timezone.utc
            ).isoformat(),
        }
        captured = {}

        def capture(
            proposed_name,
            procedure,
            observation,
            package,
            **_kwargs,
        ):
            captured.update(
                {
                    path.relative_to(package).as_posix(): path.read_text()
                    for path in package.rglob("*")
                    if path.is_file()
                }
            )
            return {"state": "collecting"}

        with (
            mock.patch.object(
                core,
                "_candidate_lifecycle_call",
                return_value={"records": []},
            ),
            mock.patch.object(
                core,
                "_collect_candidate_observation",
                side_effect=capture,
            ),
        ):
            core._collect_shadow_candidate(result, reviewed_identity)
        self.assertEqual(
            captured,
            {
                "SKILL.md": (skill / "SKILL.md").read_text(),
                "references/existing.md": "existing\n",
                "references/new.md": "new\n",
            },
        )

    def test_profile_matching_reports_no_match_and_ambiguity(self) -> None:
        fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", fixture)
        procedure = {
            "trigger": "A reusable fixture procedure is demonstrated.",
            "outcome": "The fixture procedure is retained.",
            "actions": ["Identify the procedure."],
            "exclusions": ["Do not copy source-specific details."],
        }
        executor_fixture = self.write(
            "profile-match.json",
            {
                "task_profiles": [
                    {
                        "source_event_ids": ["one-event-1"],
                        "task_type": "retain-fixture-procedure",
                        "abstract_summary": "Retain a reusable procedure.",
                        "reuse_value": "reusable-procedure",
                        "procedure": procedure,
                    }
                ]
            },
        )
        executor = self.adapter(
            "review-executor", "exec", executor_fixture
        )
        core = self.core({("fake", "exec")})
        profiled = core.profile(
            "fake", source, "fake:one", "exec", executor
        )
        receipt_path = Path(profiled["receipt"])
        receipt = json.loads(receipt_path.read_text())
        reviewed_identity = source.call(
            "inspect", session="fake:one"
        )["session"]
        no_match, no_match_status = core._matching_task_profile(
            {
                "transcript_context": {
                    "snapshot_sha256": receipt[
                        "snapshot_sha256"
                    ].removeprefix("sha256:")
                },
                "evidence_event_ids": ["one-event-2"],
            },
            receipt_path,
            reviewed_identity,
        )
        self.assertIsNone(no_match)
        self.assertEqual(no_match_status, "no-exact-match")
        snapshot_mismatch, snapshot_mismatch_status = (
            core._matching_task_profile(
                {
                    "transcript_context": {
                        "snapshot_sha256": "0" * 64
                    },
                    "evidence_event_ids": ["one-event-1"],
                },
                receipt_path,
                reviewed_identity,
            )
        )
        self.assertIsNone(snapshot_mismatch)
        self.assertEqual(
            snapshot_mismatch_status, "snapshot-mismatch"
        )
        invalid_snapshot_receipt = json.loads(json.dumps(receipt))
        invalid_snapshot_receipt["snapshot_sha256"] = None
        invalid_snapshot_body = {
            key: value
            for key, value in invalid_snapshot_receipt.items()
            if key != "receipt_sha256"
        }
        invalid_snapshot_receipt["receipt_sha256"] = runtime_module.digest(
            invalid_snapshot_body
        )
        invalid_snapshot_path = (
            self.paths.task_profile_receipts
            / (
                invalid_snapshot_receipt["receipt_sha256"]
                .removeprefix("sha256:")
                + ".json"
            )
        )
        invalid_snapshot_path.write_text(json.dumps(invalid_snapshot_receipt))
        invalid_snapshot, invalid_snapshot_status = (
            core._matching_task_profile(
                {
                    "transcript_context": {
                        "snapshot_sha256": receipt[
                            "snapshot_sha256"
                        ].removeprefix("sha256:")
                    },
                    "evidence_event_ids": ["one-event-1"],
                },
                invalid_snapshot_path,
                reviewed_identity,
            )
        )
        self.assertIsNone(invalid_snapshot)
        self.assertEqual(
            invalid_snapshot_status, "snapshot-mismatch"
        )
        receipt["profiles"].append(json.loads(json.dumps(receipt["profiles"][0])))
        receipt_body = {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
        receipt["receipt_sha256"] = runtime_module.digest(receipt_body)
        ambiguous_path = (
            self.paths.task_profile_receipts
            / f"{receipt['receipt_sha256'].removeprefix('sha256:')}.json"
        )
        ambiguous_path.write_text(json.dumps(receipt))
        ambiguous, ambiguous_status = core._matching_task_profile(
            {
                "transcript_context": {
                    "snapshot_sha256": receipt["snapshot_sha256"].removeprefix(
                        "sha256:"
                    )
                },
                "evidence_event_ids": ["one-event-1"],
            },
            ambiguous_path,
            reviewed_identity,
        )
        self.assertIsNone(ambiguous)
        self.assertEqual(ambiguous_status, "ambiguous")

    def test_shadow_candidate_recurrence_accepts_non_ascii_text(self) -> None:
        core = DreamingRuntime(
            self.paths,
            set(),
            allow_autonomous_skill_creation=False,
            now=lambda: self.clock,
        )
        result = {
            "summary": "A reusable procedure — with punctuation — was demonstrated",
            "artifact": {
                "operation": "create",
                "skill_name": "unicode-procedure",
                "skill_markdown": (
                    "---\nname: unicode-procedure\n"
                    "description: Run the reusable procedure — carefully\n---\n"
                    "# Unicode procedure\n"
                ),
                "support_files": [],
            },
        }
        lock_environment = {
            **os.environ,
            "SKILLS_STATE_DIR": str(self.paths.state),
            "SKILLS_NOW_EPOCH": str(self.clock),
        }
        token = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "daemon-lock.py"),
                "acquire",
                "--mode",
                "session",
                "--owner",
                "test-unicode-shadow-candidate",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            env=lock_environment,
        ).stdout.strip()
        prior_token = os.environ.get("SKILLS_LOCK_TOKEN")
        prior_state = os.environ.get("SKILLS_STATE_DIR")
        os.environ["SKILLS_LOCK_TOKEN"] = token
        os.environ["SKILLS_STATE_DIR"] = str(self.paths.state)
        try:
            first = core._collect_shadow_candidate(
                result,
                {
                    "qualified_session_id": "session-one",
                    "updated_at": datetime.fromtimestamp(
                        self.clock - 10, timezone.utc
                    ).isoformat(),
                },
            )
            second = core._collect_shadow_candidate(
                result,
                {
                    "qualified_session_id": "session-two",
                    "updated_at": datetime.fromtimestamp(
                        self.clock - 5, timezone.utc
                    ).isoformat(),
                },
            )
        finally:
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "daemon-lock.py"),
                    "release",
                    token,
                ],
                check=True,
                env=lock_environment,
            )
            if prior_token is None:
                os.environ.pop("SKILLS_LOCK_TOKEN", None)
            else:
                os.environ["SKILLS_LOCK_TOKEN"] = prior_token
            if prior_state is None:
                os.environ.pop("SKILLS_STATE_DIR", None)
            else:
                os.environ["SKILLS_STATE_DIR"] = prior_state
        self.assertEqual(first["lifecycle_id"], second["lifecycle_id"])
        record = json.loads(
            (
                self.paths.state
                / "skill-review"
                / "candidates"
                / "v1"
                / "records"
                / f"{first['lifecycle_id']}.json"
            ).read_text()
        )
        self.assertEqual(len(record["evidence"]), 2)

    def test_shadow_candidate_root_cannot_overlap_skill_discovery(self) -> None:
        paths = RuntimePaths(
            state=self.paths.state,
            data=self.paths.data,
            skills=self.paths.data / "candidates",
        )
        core = DreamingRuntime(
            paths,
            set(),
            allow_autonomous_skill_creation=False,
            now=lambda: self.clock,
        )
        with self.assertRaisesRegex(
            RuntimeFailure,
            "candidate storage must be isolated from the skill discovery root",
        ):
            core._collect_shadow_candidate(
                {
                    "summary": "A reusable procedure was demonstrated",
                    "artifact": {
                        "operation": "create",
                        "skill_name": "overlap-procedure",
                        "skill_markdown": (
                            "---\nname: overlap-procedure\n"
                            "description: Run the overlap procedure\n---\n"
                            "# Overlap procedure\n"
                        ),
                        "support_files": [],
                    },
                },
                {
                    "qualified_session_id": "session-overlap",
                    "updated_at": datetime.fromtimestamp(
                        self.clock - 1, timezone.utc
                    ).isoformat(),
                },
            )

    def test_historical_artifacts_are_retained_but_never_mutate(self) -> None:
        self.clock = 30 * 24 * 60 * 60 + 100
        fixture = self.source_fixture(
            [self.session("patch", 10), self.session("support", 20)]
        )
        source = self.adapter("session-source", "fake", fixture)
        self.init_skills_repo()
        skill = self.paths.skills / "existing-procedure"
        skill.mkdir()
        original_markdown = (
            "---\nname: existing-procedure\n"
            "description: Run the existing fixture procedure\n---\n"
            "# Existing procedure\n"
        )
        (skill / "SKILL.md").write_text(original_markdown)
        subprocess.run(
            ["git", "-C", str(self.paths.skills), "add", "existing-procedure"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.paths.skills),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "add existing skill",
            ],
            check=True,
        )
        starting_head = subprocess.run(
            ["git", "-C", str(self.paths.skills), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        patch_fixture = self.write(
            "historical-patch.json",
            {
                "mode": "success",
                "terminal_route": "skill",
                "artifact": {
                    "operation": "patch",
                    "skill_name": "existing-procedure",
                    "skill_markdown": original_markdown + "\nHistorical patch.\n",
                    "support_files": [],
                },
            },
        )
        support_fixture = self.write(
            "historical-support.json",
            {
                "mode": "success",
                "terminal_route": "support_file",
                "artifact": {
                    "operation": "support_file",
                    "skill_name": "existing-procedure",
                    "skill_markdown": original_markdown,
                    "support_files": [
                        {
                            "path": "references/historical.md",
                            "content": "historical detail\n",
                        }
                    ],
                },
            },
        )
        patcher = self.adapter("review-executor", "patcher", patch_fixture)
        supporter = self.adapter("review-executor", "supporter", support_fixture)
        core = DreamingRuntime(
            self.paths,
            {("fake", "patcher"), ("fake", "supporter")},
            allow_autonomous_skill_creation=False,
            now=lambda: self.clock,
        )
        patch_result = core.review(
            "fake", source, "fake:patch", [("patcher", patcher)]
        )
        support_result = core.review(
            "fake", source, "fake:support", [("supporter", supporter)]
        )
        for result, operation in (
            (patch_result, "patch"),
            (support_result, "support_file"),
        ):
            self.assertEqual(result["status"], "deferred")
            self.assertEqual(result["terminal_route"], "discard")
            self.assertEqual(
                result["policy_deferred"]["reason"],
                "historical-source-outside-mutation-window",
            )
            self.assertEqual(
                result["policy_deferred"]["original_operation"], operation
            )
        self.assertEqual((skill / "SKILL.md").read_text(), original_markdown)
        self.assertFalse((skill / "references").exists())
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(self.paths.skills), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
            starting_head,
        )
        retained = [
            json.loads(path.read_text())
            for path in (self.paths.state / "results").glob("*.json")
        ]
        self.assertEqual(
            sorted(item["artifact"]["operation"] for item in retained),
            ["patch", "support_file"],
        )
        self.assertFalse((self.paths.state / "draft-reviews").exists())

    def test_containment_allows_fresh_patch(self) -> None:
        fixture = self.source_fixture([self.session("one", 900)])
        source = self.adapter("session-source", "fake", fixture)
        self.init_skills_repo()
        skill = self.paths.skills / "existing-procedure"
        skill.mkdir()
        original_markdown = (
            "---\nname: existing-procedure\n"
            "description: Run the existing fixture procedure\n---\n"
            "# Existing procedure\n"
        )
        (skill / "SKILL.md").write_text(original_markdown)
        subprocess.run(
            ["git", "-C", str(self.paths.skills), "add", "existing-procedure"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.paths.skills),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "add existing skill",
            ],
            check=True,
        )
        starting_head = subprocess.run(
            ["git", "-C", str(self.paths.skills), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        updated_markdown = original_markdown + "\nFresh patch.\n"
        executor_fixture = self.write(
            "fresh-patch.json",
            {
                "mode": "success",
                "terminal_route": "skill",
                "artifact": {
                    "operation": "patch",
                    "skill_name": "existing-procedure",
                    "skill_markdown": updated_markdown,
                    "support_files": [],
                },
            },
        )
        executor = self.adapter("review-executor", "exec", executor_fixture)
        result = DreamingRuntime(
            self.paths,
            {("fake", "exec")},
            allow_autonomous_skill_creation=False,
            now=lambda: self.clock,
        ).review("fake", source, "fake:one", [("exec", executor)])
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["artifact_mutated"])
        self.assertEqual((skill / "SKILL.md").read_text(), updated_markdown)
        self.assertNotEqual(result["artifact_commit"], starting_head)
        review_packet = next(
            value
            for value in (
                json.loads(path.read_text())
                for path in (self.paths.state / "draft-reviews").glob("*.json")
            )
            if value.get("packet_kind") == "draft_review"
        )
        self.assertEqual(
            review_packet["existing_artifact"],
            {
                "skill_name": "existing-procedure",
                "skill_markdown": original_markdown,
            },
        )

    def test_patch_rejects_structural_content_loss_before_mutation(self) -> None:
        fixture = self.source_fixture([self.session("one", 900)])
        source = self.adapter("session-source", "fake", fixture)
        self.init_skills_repo()
        skill = self.paths.skills / "existing-procedure"
        skill.mkdir()
        original_markdown = (
            "---\nname: existing-procedure\n"
            "description: Run the existing fixture procedure\n"
            "platforms: [macos]\n---\n"
            "# Existing procedure\n\n## Preserve this section\n\nOriginal guidance.\n"
        )
        (skill / "SKILL.md").write_text(original_markdown)
        subprocess.run(
            ["git", "-C", str(self.paths.skills), "add", "existing-procedure"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.paths.skills),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "add existing skill",
            ],
            check=True,
        )
        executor_fixture = self.write(
            "destructive-patch.json",
            {
                "mode": "success",
                "terminal_route": "skill",
                "artifact": {
                    "operation": "patch",
                    "skill_name": "existing-procedure",
                    "skill_markdown": (
                        "---\nname: existing-procedure\n"
                        "description: Replace the existing fixture procedure\n---\n"
                        "# Replacement\n"
                    ),
                    "support_files": [],
                },
            },
        )
        executor = self.adapter("review-executor", "exec", executor_fixture)
        with self.assertRaisesRegex(RuntimeFailure, "patch-content-loss"):
            DreamingRuntime(
                self.paths,
                {("fake", "exec")},
                allow_autonomous_skill_creation=False,
                now=lambda: self.clock,
            ).review("fake", source, "fake:one", [("exec", executor)])
        self.assertEqual((skill / "SKILL.md").read_text(), original_markdown)
        self.assertEqual(json.loads(self.paths.transactions.read_text()), {})

    def test_skill_result_commits_artifact_and_source_routing_evidence(self) -> None:
        fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", fixture)
        self.paths.skills.mkdir(parents=True)
        self.initialize_git_repo()
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
        self.initialize_git_repo()
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
            timeout=30,
            run_timeout=10,
        )
        result_path = self.case / "slow-result.json"
        response = adapter.call(
            "run",
            snapshot=self.write("slow-snapshot.json", {"events": []}),
            result=result_path,
        )
        self.assertEqual(response["terminal_route"], "discard")

    def test_profile_audits_are_per_profile_and_idempotent(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 10, event_count=3)])
        source = self.adapter("session-source", "fake", source_fixture)
        procedure = {
            "trigger": "A reusable fixture task is complete.",
            "outcome": "Retain the fixture procedure.",
            "actions": ["Retain the ordered procedure."],
            "exclusions": ["Do not retain source details."],
        }
        executor_fixture = self.write(
            "multiple-profile-audit-executor.json",
            {
                "require_task_profile_context": True,
                "require_task_profile_id": True,
                "task_profiles": [
                    {
                        "source_event_ids": ["one-event-1"],
                        "task_type": "first-reusable-task",
                        "abstract_summary": "The first reusable procedure.",
                        "reuse_value": "reusable-procedure",
                        "procedure": procedure,
                    },
                    {
                        "source_event_ids": ["one-event-2"],
                        "task_type": "second-reusable-task",
                        "abstract_summary": "The second reusable procedure.",
                        "reuse_value": "reusable-procedure",
                        "procedure": {
                            **procedure,
                            "trigger": "A second reusable fixture task is complete.",
                        },
                    },
                ],
            },
        )
        executor = self.adapter("review-executor", "exec", executor_fixture)
        core = self.core({("fake", "exec")})
        profiled = core.profile("fake", source, "fake:one", "exec", executor)
        receipt = json.loads(Path(profiled["receipt"]).read_text())
        profile_ids = [profile["profile_id"] for profile in receipt["profiles"]]
        self.assertEqual(len(profile_ids), 2)

        results = [
            core.review_profile(
                "fake",
                source,
                "fake:one",
                receipt["source_revision"],
                "exec",
                executor,
                profile_id,
            )
            for profile_id in profile_ids
        ]
        self.assertEqual([result["profile_id"] for result in results], profile_ids)
        self.assertTrue(
            all("disposition_sha256" in result for result in results)
        )
        retry = core.review_profile(
            "fake",
            source,
            "fake:one",
            receipt["source_revision"],
            "exec",
            executor,
            profile_ids[0],
        )
        self.assertEqual(retry["status"], "already-dispositioned")
        dispositions = list(self.paths.profile_audit_dispositions.glob("*.json"))
        self.assertEqual(len(dispositions), 2)
        attempts = json.loads(self.paths.attempts.read_text())
        self.assertEqual([attempt["profile_id"] for attempt in attempts], profile_ids)
        self.assertFalse(self.paths.ledger.exists())

    def test_legacy_raw_review_does_not_suppress_profile_audit(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", source_fixture)
        procedure = {
            "trigger": "A reusable fixture task is complete.",
            "outcome": "Retain the fixture procedure.",
            "actions": ["Retain the ordered procedure."],
            "exclusions": ["Do not retain source details."],
        }
        executor_fixture = self.write(
            "legacy-then-profile-executor.json",
            {
                "task_profiles": [
                    {
                        "task_type": "reusable-task",
                        "abstract_summary": "A reusable procedure.",
                        "reuse_value": "reusable-procedure",
                        "procedure": procedure,
                    }
                ]
            },
        )
        executor = self.adapter("review-executor", "exec", executor_fixture)
        core = self.core({("fake", "exec")})
        self.assertEqual(
            core.review("fake", source, "fake:one", [("exec", executor)])["status"],
            "accepted",
        )
        profiled = core.profile("fake", source, "fake:one", "exec", executor)
        receipt = json.loads(Path(profiled["receipt"]).read_text())
        audited = core.review_profile(
            "fake",
            source,
            "fake:one",
            receipt["source_revision"],
            "exec",
            executor,
            receipt["profiles"][0]["profile_id"],
        )
        self.assertEqual(audited["status"], "accepted")
        self.assertTrue(self.paths.ledger.exists())
        self.assertEqual(len(list(self.paths.profile_audit_dispositions.glob("*.json"))), 1)

    def test_malformed_or_mismatched_profile_audit_disposition_fails_closed(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 10, event_count=3)])
        source = self.adapter("session-source", "fake", source_fixture)
        procedure = {
            "trigger": "A reusable fixture task is complete.",
            "outcome": "Retain the fixture procedure.",
            "actions": ["Retain the ordered procedure."],
            "exclusions": ["Do not retain source details."],
        }
        executor_fixture = self.write(
            "invalid-profile-disposition-executor.json",
            {
                "task_profiles": [
                    {
                        "source_event_ids": ["one-event-1"],
                        "task_type": "first-task",
                        "abstract_summary": "First reusable procedure.",
                        "reuse_value": "reusable-procedure",
                        "procedure": procedure,
                    },
                    {
                        "source_event_ids": ["one-event-2"],
                        "task_type": "second-task",
                        "abstract_summary": "Second reusable procedure.",
                        "reuse_value": "reusable-procedure",
                        "procedure": {
                            **procedure,
                            "trigger": "A second reusable fixture task is complete.",
                        },
                    },
                ]
            },
        )
        executor = self.adapter("review-executor", "exec", executor_fixture)
        core = self.core({("fake", "exec")})
        profiled = core.profile("fake", source, "fake:one", "exec", executor)
        receipt = json.loads(Path(profiled["receipt"]).read_text())
        targets = core.profile_audit_targets_for(
            "fake", source, "fake:one", receipt["source_revision"], "exec", executor
        )
        malformed = core._profile_audit_disposition_path(targets[0].profile["profile_id"])
        runtime_module.atomic_json(malformed, {"invalid": True})
        with self.assertRaisesRegex(RuntimeFailure, "profile-audit-disposition-invalid"):
            core.review_profile(
                "fake", source, "fake:one", receipt["source_revision"], "exec",
                executor, targets[0].profile["profile_id"],
            )
        malformed.unlink()
        first = core.review_profile(
            "fake", source, "fake:one", receipt["source_revision"], "exec",
            executor, targets[0].profile["profile_id"],
        )
        first_path = core._profile_audit_disposition_path(targets[0].profile["profile_id"])
        mismatch = core._profile_audit_disposition_path(targets[1].profile["profile_id"])
        mismatch.write_bytes(first_path.read_bytes())
        with self.assertRaisesRegex(RuntimeFailure, "profile-audit-disposition-invalid"):
            core.profile_audit_disposition_for(targets[1])
        self.assertIn("disposition_sha256", first)

    def test_scheduled_run_derives_reviews_from_eligible_profiles_only(self) -> None:
        sessions = [
            self.session("eligible", 1),
            self.session("no-learning", 2),
            self.session("already", 3),
            self.session("raw", 4),
        ]
        source_fixture = self.source_fixture(sessions)
        procedure = {
            "trigger": "A reusable fixture task is complete.",
            "outcome": "Retain the fixture procedure.",
            "actions": ["Retain the ordered procedure."],
            "exclusions": ["Do not retain source details."],
        }
        reusable = {
            "task_type": "reusable-task",
            "abstract_summary": "A reusable procedure.",
            "reuse_value": "reusable-procedure",
            "procedure": procedure,
        }
        executor_fixture = self.write(
            "profile-admission-executor.json",
            {
                "require_task_profile_context": True,
                "require_task_profile_id": True,
                "task_profiles_by_session": {
                    "fake:eligible": [reusable],
                    "fake:no-learning": [
                        {
                            "task_type": "one-off-task",
                            "abstract_summary": "A task with no durable learning.",
                            "reuse_value": "one-off",
                        }
                    ],
                    "fake:already": [reusable],
                },
                "task_profile_errors_by_session": {
                    "fake:raw": {
                        "code": "malformed-executor-result",
                        "message": "raw session is not profiled",
                    }
                },
            },
        )
        source = self.adapter("session-source", "fake", source_fixture)
        executor = self.adapter("review-executor", "exec", executor_fixture)
        core = DreamingRuntime(
            self.paths,
            {("fake", "exec")},
            policy_version=2,
            overlap_seconds=10,
            quiet_retry_seconds=5,
            allow_autonomous_skill_creation=False,
            now=lambda: self.clock,
        )
        profiled = core.profile("fake", source, "fake:already", "exec", executor)
        receipt = json.loads(Path(profiled["receipt"]).read_text())
        core.review_profile(
            "fake",
            source,
            "fake:already",
            receipt["source_revision"],
            "exec",
            executor,
            receipt["profiles"][0]["profile_id"],
        )
        config = self.write(
            "profile-admission-adapters.json",
            {
                "contract_version": 1,
                "routes": ["fake>exec"],
                "executor_order": ["exec"],
                "sources": {
                    "fake": {
                        "argv": [
                            sys.executable, str(FAKE), "--fixture",
                            str(source_fixture), "--adapter-id", "fake",
                            "--role", "session-source",
                        ]
                    }
                },
                "executors": {
                    "exec": {
                        "argv": [
                            sys.executable, str(FAKE), "--fixture",
                            str(executor_fixture), "--adapter-id", "exec",
                            "--role", "review-executor",
                        ]
                    }
                },
                "publishers": {},
            },
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
        self.assertFalse(report["ok"])
        eligible_receipt = next(
            json.loads(path.read_text())
            for path in self.paths.task_profile_receipts.glob("*.json")
            if json.loads(path.read_text())["qualified_session_id"] == "fake:eligible"
        )
        self.assertEqual(
            [(row["session_id"], row["profile_id"]) for row in report["reviews"]],
            [
                (
                    "fake:eligible",
                    eligible_receipt["profiles"][0]["profile_id"],
                )
            ],
        )
        self.assertEqual(
            {
                (row["session_id"], row["code"])
                for row in report["profile_review_skips"]
            },
            {
                ("fake:no-learning", "no-reusable-profile"),
                ("fake:already", "already-dispositioned"),
                ("fake:raw", "profile-unavailable"),
            },
        )
        attempts = json.loads(self.paths.attempts.read_text())
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(row.get("profile_audit") for row in attempts))
        self.assertFalse(self.paths.ledger.exists())

    def test_failed_profile_reviews_consume_the_hard_operation_ceiling(self) -> None:
        sessions = [self.session(f"failed-{index}", index + 1) for index in range(3)]
        source_fixture = self.source_fixture(sessions)
        procedure = {
            "trigger": "A reusable fixture task is complete.",
            "outcome": "Retain the fixture procedure.",
            "actions": ["Retain the procedure."],
            "exclusions": ["Do not retain source details."],
        }
        executor_fixture = self.write(
            "failed-profile-review-executor.json",
            {
                "mode": "fail",
                "task_profiles": [
                    {
                        "task_type": "failed-review-task",
                        "abstract_summary": "A reusable procedure.",
                        "reuse_value": "reusable-procedure",
                        "procedure": procedure,
                    }
                ],
            },
        )
        config = self.write(
            "failed-profile-review-adapters.json",
            {
                "contract_version": 1,
                "max_reviews_per_run": 2,
                "routes": ["fake>exec"],
                "executor_order": ["exec"],
                "sources": {
                    "fake": {
                        "argv": [
                            sys.executable, str(FAKE), "--fixture",
                            str(source_fixture), "--adapter-id", "fake",
                            "--role", "session-source",
                        ]
                    }
                },
                "executors": {
                    "exec": {
                        "argv": [
                            sys.executable, str(FAKE), "--fixture",
                            str(executor_fixture), "--adapter-id", "exec",
                            "--role", "review-executor",
                        ]
                    }
                },
                "publishers": {},
            },
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
        self.assertEqual(report["review_budget"], {
            "max_operations": 2, "started_operations": 2,
        })
        self.assertEqual(report["reviews"], [])
        self.assertEqual(report["deferred_reviews"], 1)
        self.assertEqual(len(report["errors"]), 2)
        self.assertTrue(
            all(row["phase"] == "review" for row in report["errors"])
        )

    def test_profile_audited_queue_drains_and_later_revision_reopens(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 10, event_count=3)])
        procedure = {
            "trigger": "A reusable fixture task is complete.",
            "outcome": "Retain the fixture procedure.",
            "actions": ["Retain the procedure."],
            "exclusions": ["Do not retain source details."],
        }
        executor_fixture = self.write(
            "queue-drain-executor.json",
            {
                "task_profiles": [
                    {
                        "source_event_ids": ["one-event-1"],
                        "task_type": "first-task",
                        "abstract_summary": "First reusable procedure.",
                        "reuse_value": "reusable-procedure",
                        "procedure": procedure,
                    },
                    {
                        "source_event_ids": ["one-event-2"],
                        "task_type": "second-task",
                        "abstract_summary": "Second reusable procedure.",
                        "reuse_value": "reusable-procedure",
                        "procedure": {
                            **procedure,
                            "trigger": "A second reusable fixture task is complete.",
                        },
                    },
                ],
            },
        )
        config = self.write(
            "queue-drain-adapters.json",
            {
                "contract_version": 1,
                "routes": ["fake>exec"],
                "executor_order": ["exec"],
                "sources": {
                    "fake": {
                        "argv": [
                            sys.executable, str(FAKE), "--fixture",
                            str(source_fixture), "--adapter-id", "fake",
                            "--role", "session-source",
                        ]
                    }
                },
                "executors": {
                    "exec": {
                        "argv": [
                            sys.executable, str(FAKE), "--fixture",
                            str(executor_fixture), "--adapter-id", "exec",
                            "--role", "review-executor",
                        ]
                    }
                },
                "publishers": {},
            },
        )

        def run() -> dict:
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
                check=True,
            )
            return json.loads(result.stdout)

        first = run()
        self.assertEqual(len(first["reviews"]), 2)
        queue = json.loads(self.paths.queue.read_text())
        self.assertEqual(queue[0]["status"], "profile-audited")
        self.assertEqual(len(json.loads(self.paths.attempts.read_text())), 2)

        queue[0]["status"] = "queued"
        runtime_module.atomic_json(self.paths.queue, queue)
        recovered = run()
        self.assertEqual(recovered["reviews"], [])
        self.assertEqual(
            json.loads(self.paths.queue.read_text())[0]["status"], "profile-audited"
        )
        self.assertEqual(len(json.loads(self.paths.attempts.read_text())), 2)

        fixture = json.loads(executor_fixture.read_text())
        fixture["contract_identity_overrides"] = {"fixture_generation": 2}
        executor_fixture.write_text(json.dumps(fixture))
        identity_changed = run()
        self.assertEqual(identity_changed["profiles"], [])
        self.assertEqual(len(list(self.paths.task_profile_receipts.glob("*.json"))), 1)

        source = json.loads(source_fixture.read_text())
        source["sessions"][0]["events"].append(
            event("fake", "one", 4, "assistant_message")
        )
        source["sessions"][0]["updated_at"] = 11
        source_fixture.write_text(json.dumps(source))
        later = run()
        self.assertEqual(len(later["profiles"]), 1)
        self.assertEqual(later["reviews"], [])
        self.assertEqual(len(list(self.paths.task_profile_receipts.glob("*.json"))), 2)
        statuses = [
            item["status"]
            for item in json.loads(self.paths.queue.read_text())
            if item["qualified_session_id"] == "fake:one"
        ]
        self.assertEqual(statuses, ["profile-audited", "profile-audited"])
        self.assertEqual(len(json.loads(self.paths.attempts.read_text())), 2)

    def test_profile_audit_disposition_survives_later_receipt_provenance(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 10, event_count=2)])
        source = self.adapter("session-source", "fake", source_fixture)
        procedure = {
            "trigger": "A reusable fixture task is complete.",
            "outcome": "Retain the fixture procedure.",
            "actions": ["Retain the procedure."],
            "exclusions": ["Do not retain source details."],
        }
        executor_fixture = self.write(
            "later-revision-profile-executor.json",
            {
                "task_profiles": [
                    {
                        "source_event_ids": ["one-event-1"],
                        "task_type": "stable-reusable-task",
                        "abstract_summary": "A stable reusable procedure.",
                        "reuse_value": "reusable-procedure",
                        "procedure": procedure,
                    }
                ],
            },
        )
        executor = self.adapter("review-executor", "exec", executor_fixture)
        core = self.core({("fake", "exec")})
        first = core.profile("fake", source, "fake:one", "exec", executor)
        first_receipt = json.loads(Path(first["receipt"]).read_text())
        profile_id = first_receipt["profiles"][0]["profile_id"]
        core.review_profile(
            "fake", source, "fake:one", first_receipt["source_revision"],
            "exec", executor, profile_id,
        )
        fixture = json.loads(source_fixture.read_text())
        fixture["sessions"][0]["events"].append(
            event("fake", "one", 3, "assistant_message")
        )
        fixture["sessions"][0]["updated_at"] = 11
        source_fixture.write_text(json.dumps(fixture))
        second = core.profile("fake", source, "fake:one", "exec", executor)
        second_receipt = json.loads(Path(second["receipt"]).read_text())
        self.assertNotEqual(
            first_receipt["receipt_sha256"],
            second_receipt["receipt_sha256"],
        )
        self.assertEqual(second_receipt["profiles"][0]["profile_id"], profile_id)
        target = core.profile_audit_targets_for(
            "fake", source, "fake:one", second_receipt["source_revision"],
            "exec", executor,
        )[0]
        origin_receipt_path = Path(first["receipt"])
        origin_receipt_bytes = origin_receipt_path.read_bytes()
        origin_snapshot_path = (
            self.paths.snapshots
            / f"{first_receipt['snapshot_sha256'].removeprefix('sha256:')}.json"
        )
        origin_snapshot_bytes = origin_snapshot_path.read_bytes()
        origin_receipt_path.unlink()
        with self.assertRaisesRegex(RuntimeFailure, "origin-receipt-shape"):
            core.profile_audit_disposition_for(target)
        origin_receipt_path.write_bytes(origin_receipt_bytes)
        origin_snapshot_path.unlink()
        with self.assertRaisesRegex(RuntimeFailure, "origin-snapshot-shape"):
            core.profile_audit_disposition_for(target)
        origin_snapshot_path.write_bytes(origin_snapshot_bytes)
        origin_receipt_path.write_text("{}")
        with self.assertRaisesRegex(RuntimeFailure, "origin-receipt-shape"):
            core.profile_audit_disposition_for(target)
        origin_receipt_path.write_bytes(origin_receipt_bytes)
        retried = core.review_profile(
            "fake", source, "fake:one", second_receipt["source_revision"],
            "exec", executor, profile_id,
        )
        self.assertEqual(retried["status"], "already-dispositioned")
        self.assertEqual(len(json.loads(self.paths.attempts.read_text())), 1)

    def test_profile_audit_contract_supersession_and_race_are_auditable(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", source_fixture)
        procedure = {
            "trigger": "A reusable fixture task is complete.",
            "outcome": "Retain the fixture procedure.",
            "actions": ["Retain the procedure."],
            "exclusions": ["Do not retain source details."],
        }
        executor_fixture = self.write(
            "profile-audit-contract-executor.json",
            {
                "task_profiles": [
                    {
                        "task_type": "contract-task",
                        "abstract_summary": "A reusable procedure.",
                        "reuse_value": "reusable-procedure",
                        "procedure": procedure,
                    }
                ],
            },
        )
        executor = self.adapter("review-executor", "exec", executor_fixture)
        core = self.core({("fake", "exec")})
        profiled = core.profile("fake", source, "fake:one", "exec", executor)
        receipt = json.loads(Path(profiled["receipt"]).read_text())
        target = core.profile_audit_targets_for(
            "fake", source, "fake:one", receipt["source_revision"], "exec", executor
        )[0]
        first = core._record_profile_audit_disposition(
            target, "exec", executor.identity,
            {
                "terminal_route": "discard",
                "summary": "A terminal profile review.",
                "routing_reason": "The fixture completed review.",
            },
        )
        self.clock += 1
        raced = core._record_profile_audit_disposition(
            target, "exec", executor.identity,
            {
                "terminal_route": "discard",
                "summary": "A terminal profile review.",
                "routing_reason": "The fixture completed review.",
            },
        )
        self.assertEqual(raced, first)
        with mock.patch.object(
            runtime_module, "CURRENT_PROFILE_AUDIT_CONTRACT_VERSION", 2
        ):
            status, disposition = core.profile_audit_disposition_admission_for(target)
            self.assertEqual(status, "superseded-requires-repair-backfill")
            self.assertEqual(disposition, first)
            result = core.review_profile(
                "fake", source, "fake:one", receipt["source_revision"],
                "exec", executor, target.profile["profile_id"],
            )
        self.assertEqual(
            result["status"],
            "profile-audit-disposition-superseded-requires-repair-backfill",
        )
        unknown = {**first, "profile_audit_contract_version": 99}
        unknown["disposition_sha256"] = runtime_module.digest(
            {
                key: value
                for key, value in unknown.items()
                if key != "disposition_sha256"
            }
        )
        runtime_module.atomic_json(
            core._profile_audit_disposition_path(target.profile["profile_id"]),
            unknown,
        )
        with self.assertRaisesRegex(RuntimeFailure, "profile-audit-disposition-invalid"):
            core.profile_audit_disposition_admission_for(target)

    def test_stale_profile_audit_queue_revision_is_superseded_first(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", source_fixture)
        executor = self.adapter(
            "review-executor", "exec", self.write("stale-audit-executor.json", {})
        )
        core = self.core({("fake", "exec")})
        old = source.call("inspect", session="fake:one")["session"]
        core._admit(old)
        fixture = json.loads(source_fixture.read_text())
        fixture["sessions"][0]["events"].append(
            event("fake", "one", 3, "assistant_message")
        )
        fixture["sessions"][0]["updated_at"] = 11
        source_fixture.write_text(json.dumps(fixture))
        with self.assertRaisesRegex(RuntimeFailure, "profile-audit-stale"):
            core.profile_audit_targets_for(
                "fake", source, "fake:one", old["source_revision"], "exec", executor
            )
        queue = {row["source_revision"]: row for row in core._state(self.paths.queue, [])}
        self.assertEqual(queue[old["source_revision"]]["status"], "superseded")
        self.assertEqual(
            queue[
                source.call("inspect", session="fake:one")["session"]["source_revision"]
            ]["status"],
            "queued",
        )

    def test_task_profile_receipt_is_immutable_and_identity_bound(self) -> None:
        fixture = self.source_fixture(
            [self.session("one", 10), self.session("two", 20)]
        )
        source = self.adapter("session-source", "fake", fixture)
        script = self.case / "profile-executor.py"
        script.write_text(
            """#!/usr/bin/env python3
import hashlib, json, pathlib, sys
def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()
command = sys.argv[1]
if command == "contract":
    print(json.dumps({"ok": True, "protocol": "dreaming.review-executor",
        "version": 1, "adapter_id": "profiler", "capabilities":
        ["source-blind", "mutation-fence", "completion-sentinel",
         "task-profile-v2"]}))
elif command == "doctor":
    print(json.dumps({"ok": True, "healthy": True, "boundary_ready": True}))
elif command == "run":
    snapshot = json.loads(pathlib.Path(
        sys.argv[sys.argv.index("--snapshot") + 1]).read_text())
    event_ids = [event["source_event_id"] for event in snapshot["events"][:2]]
    procedure = {"trigger": "A task has a reusable procedure.",
        "outcome": "The procedure is retained.",
        "actions": ["Identify the task.", "Retain the procedure."],
        "exclusions": ["Do not copy source-specific details."]}
    model_profile = {"source_event_ids": event_ids,
        "task_type": "retain-reusable-procedure",
        "abstract_summary": "Retain a reusable procedure from completed work.",
        "reuse_value": "reusable-procedure", "procedure": procedure,
        "confidence": "high", "sensitive_source": False,
        "task_state": "completed"}
    session_id = snapshot["identity"]["qualified_session_id"]
    profile = {**model_profile,
        "task_key": digest({"qualified_session_id": session_id,
                            "source_event_ids": event_ids}),
        "profile_id": digest({"qualified_session_id": session_id,
                              **model_profile}),
        "procedure_fingerprint": digest(procedure)}
    snapshot_sha = digest(snapshot)
    profile_set = digest({"snapshot_sha256": snapshot_sha,
        "qualified_session_id": session_id, "profiles": [profile]})
    result = {"status": "ok", "mutation_started": False,
        "completion_sentinel": "DREAMING_TASK_PROFILE_COMPLETE",
        "schema_version": 1, "kind": "llm_task_opportunity_profile",
        "snapshot_sha256": snapshot_sha,
        "qualified_session_id": session_id,
        "profile_set_id": profile_set, "profiles": [profile],
        "model": "fixture-profile-model"}
    pathlib.Path(sys.argv[sys.argv.index("--result") + 1]).write_text(
        json.dumps(result))
    print(json.dumps({"ok": True, **result}))
"""
        )
        script.chmod(0o755)
        executor = ExecutableAdapter(
            [sys.executable, str(script)],
            "review-executor",
        )
        core = self.core({("fake", "profiler")})
        first = core.profile(
            "fake", source, "fake:one", "profiler", executor
        )
        second = core.profile(
            "fake", source, "fake:one", "profiler", executor
        )
        self.assertEqual(first, second)
        receipt_path = Path(first["receipt"])
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(receipt["receipt_sha256"], first["receipt_sha256"])
        self.assertEqual(receipt["qualified_session_id"], "fake:one")
        self.assertEqual(receipt["profiles"][0]["task_state"], "completed")
        self.assertEqual(
            runtime_module.digest(
                {
                    key: value
                    for key, value in receipt.items()
                    if key != "receipt_sha256"
                }
            ),
            receipt["receipt_sha256"],
        )
        profile_index = json.loads(
            self.paths.task_profile_index.read_text()
        )
        profile_entry = next(iter(profile_index.values()))
        profile_entry["receipt"] = str(receipt_path)
        runtime_module.atomic_json(
            self.paths.task_profile_index, profile_index
        )
        self.assertEqual(
            core.indexed_task_profile_receipt_for(
                receipt["qualified_session_id"],
                receipt["source_revision"],
                receipt["executor"],
            ).path,
            receipt_path,
        )
        original_receipt = receipt_path.read_bytes()
        tampered_receipt = json.loads(original_receipt)
        tampered_receipt["profiles"][0]["abstract_summary"] = "tampered"
        receipt_path.write_text(json.dumps(tampered_receipt))
        with self.assertRaisesRegex(
            RuntimeFailure, "task-profile-receipt-invalid"
        ):
            core.collect_profile_candidate(
                receipt_path,
                receipt["profiles"][0]["profile_id"],
                self.case,
                "retained-procedure",
            )
        receipt_path.write_bytes(original_receipt)

        unmanaged_path = self.case / receipt_path.name
        unmanaged_path.write_bytes(original_receipt)
        with self.assertRaisesRegex(
            RuntimeFailure, "task-profile-receipt-invalid"
        ):
            core.collect_profile_candidate(
                unmanaged_path,
                receipt["profiles"][0]["profile_id"],
                self.case,
                "retained-procedure",
            )
        overlapping_core = DreamingRuntime(
            RuntimePaths(
                self.paths.state,
                self.paths.data,
                self.paths.data / "candidates" / "v1",
            ),
            {("fake", "profiler")},
            now=lambda: self.clock,
        )
        with self.assertRaisesRegex(
            RuntimeFailure,
            "candidate storage must be isolated",
        ):
            overlapping_core.collect_profile_candidate(
                receipt_path,
                receipt["profiles"][0]["profile_id"],
                self.case,
                "retained-procedure",
            )
        package = self.case / "profile-package"
        package.mkdir()
        (package / "SKILL.md").write_text(
            "---\nname: retained-procedure\n"
            "description: Retain a recurring reusable procedure\n---\n"
            "# Retained procedure\n"
        )
        profile_id = receipt["profiles"][0]["profile_id"]
        lock_environment = {
            **os.environ,
            "SKILLS_STATE_DIR": str(self.paths.state),
            "SKILLS_NOW_EPOCH": str(self.clock),
        }
        token = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "daemon-lock.py"),
                "acquire",
                "--mode",
                "session",
                "--owner",
                "test-profile-candidate",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            env=lock_environment,
        ).stdout.strip()
        prior_token = os.environ.get("SKILLS_LOCK_TOKEN")
        prior_state = os.environ.get("SKILLS_STATE_DIR")
        os.environ["SKILLS_LOCK_TOKEN"] = token
        os.environ["SKILLS_STATE_DIR"] = str(self.paths.state)
        try:
            collecting = core.collect_profile_candidate(
                receipt_path,
                profile_id,
                package,
                "retained-procedure",
            )
            self.assertEqual(collecting["state"], "collecting")
            second_result = core.profile(
                "fake", source, "fake:two", "profiler", executor
            )
            second_path = Path(second_result["receipt"])
            second_receipt = json.loads(second_path.read_text())
            second_profile = second_receipt["profiles"][0]
            ready = core.collect_profile_candidate(
                second_path,
                second_profile["profile_id"],
                package,
                "retained-procedure",
            )
            self.assertEqual(ready["state"], "ready_for_draft")
            self.assertEqual(ready["recommendation"], "ready_for_draft")
            record = core._candidate_lifecycle_call(
                "read", ready["lifecycle_id"]
            )
            self.assertEqual(
                [item["independence"] for item in record["evidence"]],
                ["verified", "verified"],
            )
        finally:
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "daemon-lock.py"),
                    "release",
                    token,
                ],
                check=True,
                env=lock_environment,
            )
            if prior_token is None:
                os.environ.pop("SKILLS_LOCK_TOKEN", None)
            else:
                os.environ["SKILLS_LOCK_TOKEN"] = prior_token
            if prior_state is None:
                os.environ.pop("SKILLS_STATE_DIR", None)
            else:
                os.environ["SKILLS_STATE_DIR"] = prior_state

    def test_task_profile_rejects_hostile_executor_results(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", source_fixture)
        procedure = {
            "trigger": "A task has a reusable procedure.",
            "outcome": "The procedure is retained.",
            "actions": ["Identify the task."],
            "exclusions": ["Do not copy source-specific details."],
        }
        valid_profile = {
            "task_type": "retain-reusable-procedure",
            "abstract_summary": "Retain a reusable procedure.",
            "reuse_value": "reusable-procedure",
            "procedure": procedure,
        }
        cases = {
            "bad-sentinel": {
                "task_profiles": [valid_profile],
                "task_profile_result_overrides": {
                    "completion_sentinel": "WRONG"
                },
            },
            "unhashable-event-id": {
                "task_profiles": [
                    {**valid_profile, "source_event_ids": [{"bad": "id"}]}
                ]
            },
            "invalid-procedure": {
                "task_profiles": [
                    {
                        **valid_profile,
                        "procedure": {**procedure, "exclusions": []},
                    }
                ]
            },
            "non-reusable-procedure": {
                "task_profiles": [
                    {
                        **valid_profile,
                        "reuse_value": "one-off",
                        "procedure": "not allowed",
                    }
                ]
            },
            "duplicate-task": {
                "task_profiles": [valid_profile, valid_profile]
            },
        }
        for name, fixture_body in cases.items():
            with self.subTest(name=name):
                executor_fixture = self.write(
                    f"{name}-executor.json", fixture_body
                )
                executor = self.adapter(
                    "review-executor", "exec", executor_fixture
                )
                core = DreamingRuntime(
                    self.paths,
                    {("fake", "exec")},
                    allow_autonomous_skill_creation=False,
                    now=lambda: self.clock,
                )
                with self.assertRaisesRegex(
                    RuntimeFailure, "task-profile-invalid"
                ):
                    core.profile(
                        "fake", source, "fake:one", "exec", executor
                    )
                self.assertEqual(
                    list(self.paths.task_profile_receipts.glob("*.json")),
                    [],
                )

    def test_profile_marks_session_deleted_after_model_run(self) -> None:
        source_fixture = self.source_fixture([self.session("one", 10)])
        source = self.adapter("session-source", "fake", source_fixture)
        executor_fixture = self.write(
            "delete-after-profile.json",
            {"delete_source_fixture": str(source_fixture)},
        )
        executor = self.adapter(
            "review-executor", "exec", executor_fixture
        )
        core = self.core({("fake", "exec")})
        current = source.call("inspect", session="fake:one")["session"]
        core._admit(current)
        result = core.profile(
            "fake",
            source,
            "fake:one",
            "exec",
            executor,
            expected_revision=current["source_revision"],
        )
        self.assertEqual(result, {"status": "deleted"})
        queue = json.loads(self.paths.queue.read_text())
        self.assertEqual(queue[0]["status"], "deleted")
        self.assertEqual(
            list(self.paths.task_profile_receipts.glob("*.json")),
            [],
        )

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
        self.initialize_git_repo()
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
