#!/usr/bin/env python3
"""Deterministic CHK-07 checks for estate action authorization."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

SCRIPT = Path(__file__).with_name("estate-action.py")
CURATOR = (
    Path(__file__).parent.parent
    / "skills/skill-curator/scripts/curator-run.py"
)
SPEC = importlib.util.spec_from_file_location("estate_action", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class Fixture:
    def __init__(self, root: Path, kind: str = "personal_archive"):
        self.root = root
        self.root.mkdir(parents=True)
        self.state = root / "state"
        self.review_state = root / "review-state"
        self.authority_root = root / "authority"
        self.config_path = (
            self.review_state / "estate-action/config.json"
        )
        self.halt = root / "disable-daemon"
        self.recovery = root / "recovery.json"
        self.curator_state = root / "curator-state.json"
        self.curator_state.write_text(
            '{"paused":false}\n', encoding="utf-8"
        )
        self.counter = root / "adapter-count"
        self.adapter = root / "adapter.py"
        self._write_adapter()
        self.executor_argv = [
            str(self.adapter),
            "--request",
            "{request}",
            "--authorization",
            "{authorization}",
            "--counter",
            str(self.counter),
        ]
        self.kind = kind
        self.target_inventory = module.digest(
            [{"path": "SKILL.md", "sha256": "a" * 64}]
        )
        self.inner_target = (
            {
                "skill": "fixture",
                "instance_id": "personal:fixture",
                "inventory_sha256": self.target_inventory,
            }
            if kind.startswith("personal_")
            else {
                "plugin_id": "fixture@market",
                "source_identity": "installed:market/fixture",
                "version": "1.0.0",
                "settings_key": "fixture@market",
            }
        )
        self.target_identity = module.digest(self.inner_target)
        self.executor_receiver = {
            "receiver_id": "macbook",
            "receiver_sha256": "2" * 64,
        }
        self.executor_request = self._executor_request(kind)
        self.action = {
            "kind": kind,
            "order": 1,
            "target_identity_sha256": self.target_identity,
            "target_inventory_sha256": self.target_inventory,
            "executor_request_sha256": module.digest(
                self.executor_request
            ),
            "adapter_sha256": module.file_digest(self.adapter),
        }
        self.evidence = self._evidence()
        self._write_authority(self.evidence)
        self._write_config()
        self.candidate = {
            "schema_version": 1,
            "protocol": module.PROTOCOL,
            "action_id": f"{kind}-fixture",
            "action": self.action,
            "evidence": self.evidence,
            "executor": {
                "protocol": module.ACTION_CONTRACTS[kind]["protocol"],
                "request": self.executor_request,
                "argv": self.executor_argv,
            },
        }
        self.authorization = module.authorize(
            self.candidate, self.config_path
        )

    def _executor_request(self, kind: str) -> dict[str, Any]:
        contract = module.ACTION_CONTRACTS[kind]
        if contract["protocol"] == "dreaming.estate-curator":
            return {
                "schema_version": 1,
                "protocol": contract["protocol"],
                "op_id": f"{kind}-inner",
                "receiver": self.executor_receiver,
                "target": self.inner_target,
                "operation": {
                    "kind": contract["request_action"],
                    "order": 1,
                },
            }
        return {
            "schema_version": 1,
            "protocol": contract["protocol"],
            "operation_id": f"{kind}-inner",
            "action": contract["request_action"],
            "plugin": self.inner_target,
        }

    def _sealed(self, value: Any) -> dict[str, Any]:
        return {"sha256": module.digest(value), "value": value}

    def _decision(
        self,
        kind: str,
        census_sha256: str,
        proposed_sha256: str,
        model_sha256: str,
    ) -> dict[str, Any]:
        payload = {
            "kind": kind,
            "action": self.kind,
            "target_identity_sha256": self.target_identity,
            "current_census_sha256": census_sha256,
            "proposed_estate_sha256": proposed_sha256,
            "model_sha256": model_sha256,
            "result": "passed",
        }
        return {
            "status": "passed",
            "payload": payload,
            "sha256": module.digest(payload),
        }

    def _evidence(self) -> dict[str, dict[str, Any]]:
        census_payload = {
            "schema_version": 1,
            "host_id": "macbook",
            "scope": {"complete": True},
        }
        census = {
            **census_payload,
            "snapshot_sha256": module.digest(census_payload),
        }
        model = {
            "model_id": "gpt-fixture",
            "status": "passed",
            "evaluation_sha256": "sha256:" + "1" * 64,
        }
        proposed_snapshot = {
            "enabled_capabilities": ["builtin:base"],
            "removed_target": self.target_identity,
        }
        proposed = {
            "complete": True,
            "current_census_sha256": module.digest(census),
            "target_identity_sha256": self.target_identity,
            "action": self.kind,
            "snapshot": proposed_snapshot,
            "proposed_estate_sha256": module.digest(proposed_snapshot),
        }
        contract = module.ACTION_CONTRACTS[self.kind]
        values = {
            "census": census,
            "dependencies": {
                "complete": True,
                "target_identity_sha256": self.target_identity,
                "blocking": [],
            },
            "halt_state": {
                "halted": False,
                "paused": False,
                "recovery_required": False,
                "curator_state_sha256": module.file_digest(
                    self.curator_state
                ),
            },
            "model": model,
            "policy": {
                "status": "active",
                "allowed_actions": [self.kind],
                "target_identity_sha256": self.target_identity,
            },
            "proposed_estate": proposed,
            "receiver": {
                **self._receiver_config(self.kind),
                "adapter_sha256": module.file_digest(self.adapter),
            },
            "target": {
                "identity_sha256": self.target_identity,
                "inventory_sha256": self.target_inventory,
                "authority": contract["authority"],
                "decision": contract["decision"],
            },
        }
        model_sha256 = module.digest(model)
        values["routing"] = self._decision(
            "routing",
            module.digest(census),
            proposed["proposed_estate_sha256"],
            model_sha256,
        )
        values["portfolio"] = self._decision(
            "portfolio",
            module.digest(census),
            proposed["proposed_estate_sha256"],
            model_sha256,
        )
        return {
            label: self._sealed(value)
            for label, value in values.items()
        }

    def _receiver_config(self, kind: str) -> dict[str, Any]:
        return {
            "receiver_id": "macbook",
            "receiver_sha256": "sha256:" + "2" * 64,
            "executor_receiver": (
                self.executor_receiver
                if kind.startswith("personal_")
                else None
            ),
        }

    def _write_authority(
        self, evidence: dict[str, dict[str, Any]]
    ) -> None:
        for label, sealed in evidence.items():
            object_path = module.evidence_object_path(
                self.authority_root, label, sealed["sha256"]
            )
            object_path.parent.mkdir(parents=True, exist_ok=True)
            object_path.write_text(
                json.dumps(sealed["value"]), encoding="utf-8"
            )
            pointer = self.authority_root / "current" / f"{label}.json"
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.write_text(json.dumps(sealed), encoding="utf-8")

    def _write_config(self) -> None:
        payload = {
            "schema_version": 1,
            "evidence_root": str(self.authority_root),
            "state_root": str(self.state),
            "halt_switch": str(self.halt),
            "recovery_state": str(self.recovery),
            "curator_state": str(self.curator_state),
            "adapters": {
                kind: {
                    "path": str(self.adapter),
                    "sha256": module.file_digest(self.adapter),
                    "argv": (
                        self.candidate["executor"]["argv"]
                        if hasattr(self, "candidate")
                        else self.executor_argv
                    ),
                }
                for kind in module.ACTION_CONTRACTS
            },
            "receivers": {
                kind: self._receiver_config(kind)
                for kind in module.ACTION_CONTRACTS
            },
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(
                {
                    **payload,
                    "config_sha256": module.digest(payload),
                }
            ),
            encoding="utf-8",
        )

    def reauthorize_adapter(self) -> None:
        self.action["adapter_sha256"] = module.file_digest(self.adapter)
        self.evidence["receiver"]["value"]["adapter_sha256"] = self.action[
            "adapter_sha256"
        ]
        self.evidence["receiver"]["sha256"] = module.digest(
            self.evidence["receiver"]["value"]
        )
        self._write_authority(self.evidence)
        self._write_config()
        self.candidate["action"] = self.action
        self.candidate["evidence"] = self.evidence
        self.authorization = module.authorize(
            self.candidate, self.config_path
        )

    def _write_adapter(self, mode: str = "committed") -> None:
        self.adapter.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env python3
                import argparse
                import hashlib
                import json
                import time
                from pathlib import Path

                def digest(value):
                    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
                    return "sha256:" + hashlib.sha256(raw).hexdigest()

                parser = argparse.ArgumentParser()
                parser.add_argument("--request", required=True)
                parser.add_argument("--authorization", required=True)
                parser.add_argument("--counter", required=True)
                args = parser.parse_args()
                request = json.loads(Path(args.request).read_text())
                authorization = json.loads(Path(args.authorization).read_text())
                time.sleep(0.2)
                counter = Path(args.counter)
                count = int(counter.read_text()) + 1 if counter.exists() else 1
                counter.write_text(str(count))
                status = {mode!r}
                payload = {{
                    "schema_version": 1,
                    "protocol": "dreaming.estate-action-result",
                    "action_id": authorization["action_id"],
                    "authorization_sha256": authorization["authorization_sha256"],
                    "executor_request_sha256": digest(request),
                    "status": status,
                    "ok": status == "committed",
                    "receipt": {{"adapter_count": count}},
                }}
                print(json.dumps({{**payload, "result_sha256": digest(payload)}}))
                raise SystemExit(0 if status == "committed" else 2)
                """
            ),
            encoding="utf-8",
        )
        os.chmod(self.adapter, 0o755)

    def dispatch(
        self,
        *,
        authorization: dict[str, Any] | None = None,
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if current is not None:
            self._write_authority(current)
        return module.dispatch(
            authorization or self.authorization,
            config_path=self.config_path,
            timeout=10,
        )


class EstateActionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="estate-action."
        )
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_all_supported_action_contracts_authorize(self) -> None:
        for kind in module.ACTION_CONTRACTS:
            fixture = Fixture(self.root / kind, kind)
            self.assertEqual(
                module.validate_authorization(
                    fixture.authorization
                )["action"]["kind"],
                kind,
            )

    def test_successful_dispatch_is_retry_safe(self) -> None:
        fixture = Fixture(self.root / "success")
        first = fixture.dispatch()
        second = fixture.dispatch()
        self.assertTrue(first["ok"])
        self.assertEqual(second, first)
        self.assertEqual(fixture.counter.read_text(), "1")

    def test_concurrent_dispatch_executes_adapter_once(self) -> None:
        fixture = Fixture(self.root / "concurrent")
        candidate = fixture.root / "candidate.json"
        authorization = fixture.root / "authorization.json"
        candidate.write_text(
            json.dumps(fixture.candidate), encoding="utf-8"
        )
        environment = {
            **os.environ,
            "SKILLS_REVIEW_STATE_DIR": str(fixture.review_state),
        }
        authorized = subprocess.run(
            [
                sys.executable,
                str(CURATOR),
                "estate-authorize",
                "--candidate",
                str(candidate),
                "--output",
                str(authorization),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        self.assertEqual(authorized.returncode, 0, authorized.stderr)
        command = [
            sys.executable,
            str(CURATOR),
            "estate-dispatch",
            "--authorization",
            str(authorization),
        ]
        first = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        second = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        first_output = first.communicate(timeout=30)
        second_output = second.communicate(timeout=30)
        self.assertEqual(first.returncode, 0, first_output)
        self.assertEqual(second.returncode, 0, second_output)
        self.assertEqual(fixture.counter.read_text(), "1")

    def test_post_execution_persistence_failure_installs_recovery(
        self,
    ) -> None:
        fixture = Fixture(self.root / "persistence-failure")
        original = module.immutable_json

        def fail_result(path: Path, value: dict[str, Any]) -> None:
            if "results" in path.parts:
                raise OSError("injected result persistence failure")
            original(path, value)

        with mock.patch.object(
            module, "immutable_json", side_effect=fail_result
        ):
            with self.assertRaises(OSError):
                fixture.dispatch()
        self.assertEqual(fixture.counter.read_text(), "1")
        self.assertTrue(fixture.recovery.exists())
        index = json.loads(
            (
                fixture.state
                / fixture.authorization["action_id"]
                / "index.json"
            ).read_text()
        )
        self.assertEqual(index["phase"], "recovery_required")

    def test_executor_target_must_match_authorized_target(self) -> None:
        fixture = Fixture(self.root / "target-mismatch")
        candidate = copy.deepcopy(fixture.candidate)
        candidate["executor"]["request"]["target"]["skill"] = "other"
        candidate["action"]["executor_request_sha256"] = module.digest(
            candidate["executor"]["request"]
        )
        with self.assertRaisesRegex(
            module.ActionError, "estate-action-executor-target-mismatch"
        ):
            module.authorize(candidate, fixture.config_path)

    def test_personal_receiver_must_match_configured_identity(self) -> None:
        fixture = Fixture(self.root / "receiver-binding")
        candidate = copy.deepcopy(fixture.candidate)
        candidate["executor"]["request"]["receiver"] = {
            "receiver_id": "other",
            "receiver_sha256": "9" * 64,
        }
        candidate["action"]["executor_request_sha256"] = module.digest(
            candidate["executor"]["request"]
        )
        with self.assertRaisesRegex(
            module.ActionError, "estate-action-receiver-invalid"
        ):
            module.authorize(candidate, fixture.config_path)

    def test_authorizer_requires_the_configured_adapter(self) -> None:
        fixture = Fixture(self.root / "adapter-authority")
        other = fixture.root / "other-adapter"
        other.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        os.chmod(other, 0o755)
        candidate = copy.deepcopy(fixture.candidate)
        candidate["executor"]["argv"][0] = str(other)
        candidate["action"]["adapter_sha256"] = module.file_digest(other)
        with self.assertRaisesRegex(
            module.ActionError, "estate-action-adapter-untrusted"
        ):
            module.authorize(candidate, fixture.config_path)

    def test_authorizer_requires_the_configured_adapter_argv(self) -> None:
        fixture = Fixture(self.root / "adapter-argv-authority")
        candidate = copy.deepcopy(fixture.candidate)
        candidate["executor"]["argv"].extend(
            ["--config", str(fixture.root / "self-sealed.json")]
        )
        with self.assertRaisesRegex(
            module.ActionError, "estate-action-adapter-untrusted"
        ):
            module.authorize(candidate, fixture.config_path)

    def test_authorizer_rejects_self_asserted_evidence(self) -> None:
        fixture = Fixture(self.root / "self-asserted")
        candidate = copy.deepcopy(fixture.candidate)
        candidate["evidence"]["model"]["value"]["model_id"] = "forged"
        candidate["evidence"]["model"]["sha256"] = module.digest(
            candidate["evidence"]["model"]["value"]
        )
        with self.assertRaisesRegex(
            module.ActionError, "estate-action-evidence-not-current"
        ):
            module.authorize(candidate, fixture.config_path)

    def test_every_bound_evidence_change_invalidates_action(self) -> None:
        fixture = Fixture(self.root / "drift")
        before = fixture.counter.exists()
        for label in sorted(module.EVIDENCE_KEYS):
            current = copy.deepcopy(fixture.evidence)
            value = current[label]["value"]
            if label == "census":
                value["host_id"] = "another-host"
                payload = {
                    key: item
                    for key, item in value.items()
                    if key != "snapshot_sha256"
                }
                value["snapshot_sha256"] = module.digest(payload)
            elif label == "dependencies":
                value["revision"] = "changed"
            elif label == "halt_state":
                value["halted"] = True
            elif label == "model":
                value["model_id"] = "another-model"
            elif label == "policy":
                value["revision"] = "changed"
            elif label in {"routing", "portfolio"}:
                value["payload"]["revision"] = "changed"
                value["sha256"] = module.digest(value["payload"])
            elif label == "proposed_estate":
                value["snapshot"]["revision"] = "changed"
                value["proposed_estate_sha256"] = module.digest(
                    value["snapshot"]
                )
            elif label == "receiver":
                value["receiver_sha256"] = "sha256:" + "3" * 64
            else:
                value["inventory_sha256"] = "sha256:" + "4" * 64
            current[label]["sha256"] = module.digest(value)
            with self.assertRaises(module.ActionError, msg=label):
                fixture.dispatch(current=current)
            fixture._write_authority(fixture.evidence)
        self.assertEqual(fixture.counter.exists(), before)

    def test_changed_adapter_halt_recovery_and_curator_state_block(self) -> None:
        adapter = Fixture(self.root / "adapter")
        adapter.adapter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        with self.assertRaisesRegex(
            module.ActionError,
            "estate-action-adapter-(?:changed|untrusted)",
        ):
            adapter.dispatch()

        halted = Fixture(self.root / "halted")
        halted.halt.write_text("", encoding="utf-8")
        with self.assertRaisesRegex(
            module.ActionError, "estate-action-halted"
        ):
            halted.dispatch()

        recovering = Fixture(self.root / "recovering")
        recovering.recovery.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(
            module.ActionError, "estate-action-recovery-required"
        ):
            recovering.dispatch()

        paused = Fixture(self.root / "paused")
        paused.curator_state.write_text(
            '{"paused":true}\n', encoding="utf-8"
        )
        with self.assertRaisesRegex(
            module.ActionError, "estate-action-paused"
        ):
            paused.dispatch()

    def test_ambiguous_previous_dispatch_enters_recovery(self) -> None:
        fixture = Fixture(self.root / "ambiguous")
        operation = fixture.state / fixture.authorization["action_id"]
        operation.mkdir(parents=True)
        module.immutable_json(
            operation / "authorization.json", fixture.authorization
        )
        module.immutable_json(
            operation / "request.json", fixture.executor_request
        )
        module.atomic_json(
            operation / "index.json",
            {
                "schema_version": 1,
                "action_id": fixture.authorization["action_id"],
                "authorization_sha256": fixture.authorization[
                    "authorization_sha256"
                ],
                "phase": "running",
            },
        )
        with self.assertRaisesRegex(
            module.ActionError, "estate-action-ambiguous"
        ):
            fixture.dispatch()
        self.assertTrue(fixture.recovery.exists())
        self.assertFalse(fixture.counter.exists())

    def test_malformed_executor_result_enters_recovery(self) -> None:
        fixture = Fixture(self.root / "malformed")
        fixture.adapter.write_text(
            "#!/bin/sh\necho not-json\n", encoding="utf-8"
        )
        os.chmod(fixture.adapter, 0o755)
        fixture.reauthorize_adapter()
        with self.assertRaisesRegex(
            module.ActionError, "estate-action-result-invalid"
        ):
            fixture.dispatch()
        self.assertTrue(fixture.recovery.exists())

    def test_non_utf8_executor_output_enters_recovery(self) -> None:
        fixture = Fixture(self.root / "non-utf8")
        fixture.adapter.write_bytes(
            b"#!/bin/sh\nprintf '\\377\\376\\n'\n"
        )
        os.chmod(fixture.adapter, 0o755)
        fixture.reauthorize_adapter()
        with self.assertRaises(UnicodeDecodeError):
            fixture.dispatch()
        self.assertTrue(fixture.recovery.exists())

    def test_recovery_result_never_becomes_terminal_before_fence(
        self,
    ) -> None:
        fixture = Fixture(self.root / "recovery-order")
        fixture._write_adapter("recovery_required")
        fixture.reauthorize_adapter()
        original = module.atomic_json

        def fail_fence(path: Path, value: dict[str, Any]) -> None:
            if path == fixture.recovery:
                raise OSError("injected recovery fence failure")
            original(path, value)

        with mock.patch.object(
            module, "atomic_json", side_effect=fail_fence
        ):
            result = fixture.dispatch()
        self.assertEqual(result["status"], "recovery_required")
        self.assertTrue(
            (fixture.state / "recovery-required.json").exists()
        )
        self.assertFalse(fixture.recovery.exists())
        index = json.loads(
            (
                fixture.state
                / fixture.authorization["action_id"]
                / "index.json"
            ).read_text()
        )
        self.assertEqual(index["phase"], "recovery_required")

    def test_recovery_index_blocks_when_both_fence_writes_fail(
        self,
    ) -> None:
        fixture = Fixture(self.root / "dual-fence-failure")
        fixture._write_adapter("recovery_required")
        fixture.reauthorize_adapter()
        original = module.atomic_json
        local_recovery = fixture.state / "recovery-required.json"

        def fail_fences(path: Path, value: dict[str, Any]) -> None:
            if path in {fixture.recovery, local_recovery}:
                raise OSError("injected dual fence failure")
            original(path, value)

        with mock.patch.object(
            module, "atomic_json", side_effect=fail_fences
        ):
            with self.assertRaises(OSError):
                fixture.dispatch()
        index = json.loads(
            (
                fixture.state
                / fixture.authorization["action_id"]
                / "index.json"
            ).read_text()
        )
        self.assertEqual(index["phase"], "recovery_required")

        candidate = copy.deepcopy(fixture.candidate)
        candidate["action_id"] = "later-action"
        later = module.authorize(candidate, fixture.config_path)
        with self.assertRaisesRegex(
            module.ActionError, "estate-action-recovery-required"
        ):
            fixture.dispatch(authorization=later)
        self.assertEqual(fixture.counter.read_text(), "1")

    def test_cli_authorize_verify_and_dispatch(self) -> None:
        fixture = Fixture(self.root / "cli")
        candidate = fixture.root / "candidate.json"
        authorization = fixture.root / "authorization.json"
        candidate.write_text(
            json.dumps(fixture.candidate), encoding="utf-8"
        )
        environment = {
            **os.environ,
            "SKILLS_REVIEW_STATE_DIR": str(fixture.review_state),
        }
        authorized = subprocess.run(
            [
                sys.executable,
                str(CURATOR),
                "estate-authorize",
                "--candidate",
                str(candidate),
                "--output",
                str(authorization),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        self.assertEqual(
            authorized.returncode,
            0,
            authorized.stdout + authorized.stderr,
        )
        verified = subprocess.run(
            [
                sys.executable,
                str(CURATOR),
                "estate-verify",
                "--authorization",
                str(authorization),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        self.assertEqual(verified.returncode, 0, verified.stdout)
        dispatched = subprocess.run(
            [
                sys.executable,
                str(CURATOR),
                "estate-dispatch",
                "--authorization",
                str(authorization),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        self.assertEqual(
            dispatched.returncode,
            0,
            dispatched.stdout + dispatched.stderr,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
