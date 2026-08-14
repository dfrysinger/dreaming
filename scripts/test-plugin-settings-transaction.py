#!/usr/bin/env python3
"""Deterministic CHK-05 tests for the plugin settings writer."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("plugin-settings-transaction.py")
SPEC = importlib.util.spec_from_file_location(
    "plugin_settings_transaction", SCRIPT
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class UnsupportedSwapper:
    def verify_supported(self, _directory: Path) -> None:
        raise module.SettingsError("settings-swap-unsupported")

    def exchange(self, _left: Path, _right: Path) -> None:
        raise AssertionError("exchange must not run")


class FailingRollbackSwapper:
    def __init__(self) -> None:
        self.real = module.MacOSSwapper()
        self.exchanges = 0

    def verify_supported(self, directory: Path) -> None:
        self.real.verify_supported(directory)

    def exchange(self, left: Path, right: Path) -> None:
        self.exchanges += 1
        if self.exchanges == 2:
            raise module.SettingsError("settings-swap-failed")
        self.real.exchange(left, right)


class Fixture:
    def __init__(
        self,
        root: Path,
        *,
        source_type: str = "marketplace",
        prior_present: bool = True,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True)
        self.settings = root / "settings.json"
        self.transactions = root / "transactions"
        self.qualifications = root / "qualifications"
        self.lock = root / "settings.lock"
        self.recovery = root / "recovery" / "state.json"
        self.transactions.mkdir()
        self.qualifications.mkdir()
        enabled = {"other@market": True}
        if prior_present:
            enabled["fixture@market"] = True
        self.before_document = {
            "editor": {"theme": "dark", "fontSize": 14},
            "enabledPlugins": enabled,
            "skillDirectories": ["/fixture/skills"],
        }
        self.before_bytes = (
            '{\n  "skillDirectories": ["/fixture/skills"],\n'
            '  "enabledPlugins": '
            + json.dumps(enabled, separators=(", ", ": "))
            + ',\n  "editor": {"theme": "dark", "fontSize": 14}\n}\n'
        ).encode()
        self.settings.write_bytes(self.before_bytes)
        os.chmod(self.settings, 0o600)
        self.plugin = {
            "plugin_id": "fixture@market",
            "source_identity": (
                "installed:market/fixture"
                if source_type == "marketplace"
                else "installed:_direct/fixture"
            ),
            "version": "1.0.0",
            "source_type": source_type,
            "settings_key": "fixture@market",
        }
        qualification_payload = {
            "schema_version": 1,
            "status": "qualified",
            "source_type": source_type,
            "copilot_version": "1.0.80",
            "plugin_id": self.plugin["plugin_id"],
            "settings_key": self.plugin["settings_key"],
            "disable_verified": True,
            "restore_verified": True,
        }
        self.qualification = {
            **qualification_payload,
            "qualification_sha256": module.hash_json(qualification_payload),
        }
        (self.qualifications / f"{self.qualification['qualification_sha256']}.json").write_text(
            json.dumps(self.qualification), encoding="utf-8"
        )
        before = module.read_regular(self.settings, "fixture")
        runtime_before = self.runtime_inventory()
        request_payload = {
            "schema_version": 1,
            "operation_id": "fixture-disable",
            "action": "disable",
            "plugin": self.plugin,
            "copilot_version": "1.0.80",
            "qualification_sha256": self.qualification[
                "qualification_sha256"
            ],
            "expected": {
                "settings_sha256": before.sha256,
                "settings_device": before.device,
                "settings_inode": before.inode,
                "settings_mode": before.mode,
                "prior_key_present": prior_present,
                "prior_key_value": True if prior_present else None,
                "runtime_before_sha256": module.hash_json(runtime_before),
            },
            "evidence": {
                "census_sha256": "a" * 64,
                "capability_inventory_sha256": "b" * 64,
            },
        }
        self.request = {
            **request_payload,
            "request_sha256": module.hash_json(request_payload),
        }

    def reseal(self) -> None:
        payload = {
            key: value
            for key, value in self.request.items()
            if key != "request_sha256"
        }
        self.request["request_sha256"] = module.hash_json(payload)

    def runtime_inventory(self) -> dict:
        document = json.loads(self.settings.read_text(encoding="utf-8"))
        disabled = (
            document.get("enabledPlugins", {}).get(
                self.plugin["settings_key"]
            )
            is False
        )
        owned = [] if disabled else ["plugin:fixture-skill"]
        return {
            "schema_version": 1,
            "copilot_version": "1.0.80",
            "plugin_identity": {
                field: self.plugin[field]
                for field in ("plugin_id", "source_identity", "version")
            },
            "plugin_enabled": not disabled,
            "owned_capability_ids": owned,
            "estate_capability_ids": ["builtin:base", *owned],
        }

    def execute(self, **overrides):
        arguments = {
            "request": self.request,
            "settings_path": self.settings,
            "transaction_root": self.transactions,
            "qualification_root": self.qualifications,
            "lock_path": self.lock,
            "recovery_state": self.recovery,
            "runtime_verifier": self.runtime_inventory,
            "swapper": module.MacOSSwapper(),
            "quiet_interval": 0,
        }
        arguments.update(overrides)
        return module.execute_disable(**arguments)


class PluginSettingsTransactionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="plugin-settings."))

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_marketplace_and_direct_sources_require_qualification(self) -> None:
        for source_type in ("marketplace", "direct"):
            with self.subTest(source_type=source_type):
                fixture = Fixture(
                    self.root / source_type, source_type=source_type
                )
                result = fixture.execute()
                self.assertTrue(result["ok"])
                self.assertEqual(
                    json.loads(fixture.settings.read_text())["enabledPlugins"][
                        "fixture@market"
                    ],
                    False,
                )

    def test_absent_key_is_enabled_only_with_matching_runtime_proof(self) -> None:
        fixture = Fixture(self.root / "absent", prior_present=False)
        result = fixture.execute()
        self.assertTrue(result["ok"])
        document = json.loads(fixture.settings.read_text())
        self.assertFalse(document["enabledPlugins"]["fixture@market"])

        blocked = Fixture(self.root / "absent-blocked", prior_present=False)
        runtime = blocked.runtime_inventory()
        runtime["plugin_enabled"] = False
        blocked.request["expected"]["runtime_before_sha256"] = module.hash_json(
            runtime
        )
        blocked.reseal()
        with self.assertRaisesRegex(
            module.SettingsError, "plugin-runtime-before-mismatch"
        ):
            blocked.execute(runtime_verifier=lambda: runtime)
        self.assertEqual(blocked.settings.read_bytes(), blocked.before_bytes)

    def test_success_changes_only_target_semantics_and_records_preimage(
        self,
    ) -> None:
        fixture = Fixture(self.root / "success")
        before = json.loads(fixture.settings.read_text())
        result = fixture.execute()
        after = json.loads(fixture.settings.read_text())
        self.assertTrue(result["ok"])
        self.assertEqual(after["editor"], before["editor"])
        self.assertEqual(
            after["skillDirectories"], before["skillDirectories"]
        )
        self.assertTrue(after["enabledPlugins"]["other@market"])
        self.assertFalse(after["enabledPlugins"]["fixture@market"])
        self.assertEqual(
            Path(result["before"]["preimage_path"]).read_bytes(),
            fixture.before_bytes,
        )
        retained = Path(result["before"]["retained_inode_path"])
        self.assertTrue(retained.exists())
        self.assertEqual(retained.read_bytes(), fixture.before_bytes)
        self.assertEqual(
            result["receipt_sha256"],
            module.hash_json(
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"ok", "receipt_sha256"}
                }
            ),
        )

    def test_success_preserves_mode_under_restrictive_umask(self) -> None:
        fixture = Fixture(self.root / "mode")
        os.chmod(fixture.settings, 0o644)
        state = module.read_regular(fixture.settings, "fixture")
        fixture.request["expected"].update(
            {
                "settings_sha256": state.sha256,
                "settings_device": state.device,
                "settings_inode": state.inode,
                "settings_mode": state.mode,
            }
        )
        fixture.reseal()
        prior_umask = os.umask(0o077)
        try:
            result = fixture.execute()
        finally:
            os.umask(prior_umask)
        self.assertTrue(result["ok"])
        self.assertEqual(
            module.read_regular(fixture.settings, "fixture").mode, 0o644
        )

    def test_malformed_or_stale_settings_never_mutate(self) -> None:
        malformed = Fixture(self.root / "malformed")
        malformed.settings.write_text("{bad-json", encoding="utf-8")
        state = module.read_regular(malformed.settings, "fixture")
        malformed.request["expected"].update(
            {
                "settings_sha256": state.sha256,
                "settings_device": state.device,
                "settings_inode": state.inode,
                "settings_mode": state.mode,
            }
        )
        malformed.reseal()
        with self.assertRaisesRegex(
            module.SettingsError, "settings-json-invalid"
        ):
            malformed.execute()
        self.assertEqual(malformed.settings.read_text(), "{bad-json")

        stale = Fixture(self.root / "stale")
        stale.settings.write_bytes(stale.before_bytes + b" ")
        with self.assertRaisesRegex(
            module.SettingsError, "settings-preimage-mismatch"
        ):
            stale.execute()
        self.assertEqual(stale.settings.read_bytes(), stale.before_bytes + b" ")

        null_enabled = Fixture(self.root / "null-enabled")
        null_enabled.settings.write_text(
            json.dumps({"enabledPlugins": None}), encoding="utf-8"
        )
        state = module.read_regular(null_enabled.settings, "fixture")
        null_enabled.request["expected"].update(
            {
                "settings_sha256": state.sha256,
                "settings_device": state.device,
                "settings_inode": state.inode,
                "settings_mode": state.mode,
                "prior_key_present": False,
                "prior_key_value": None,
            }
        )
        runtime = {
            "schema_version": 1,
            "copilot_version": "1.0.80",
            "plugin_identity": {
                field: null_enabled.plugin[field]
                for field in ("plugin_id", "source_identity", "version")
            },
            "plugin_enabled": True,
            "owned_capability_ids": ["plugin:fixture-skill"],
            "estate_capability_ids": [
                "builtin:base",
                "plugin:fixture-skill",
            ],
        }
        null_enabled.request["expected"]["runtime_before_sha256"] = (
            module.hash_json(runtime)
        )
        null_enabled.reseal()
        with self.assertRaisesRegex(
            module.SettingsError, "settings-json-invalid"
        ):
            null_enabled.execute(runtime_verifier=lambda: runtime)

    def test_unqualified_unsupported_and_write_failures_do_not_mutate(
        self,
    ) -> None:
        unqualified = Fixture(self.root / "unqualified")
        (unqualified.qualifications / f"{unqualified.qualification['qualification_sha256']}.json").unlink()
        with self.assertRaisesRegex(
            module.SettingsError, "plugin-source-unqualified"
        ):
            unqualified.execute()
        self.assertEqual(unqualified.settings.read_bytes(), unqualified.before_bytes)

        version_drift = Fixture(self.root / "version-drift")
        version_drift.request["copilot_version"] = "1.0.81"
        version_drift.reseal()
        with self.assertRaisesRegex(
            module.SettingsError, "plugin-source-unqualified"
        ):
            version_drift.execute()
        self.assertEqual(
            version_drift.settings.read_bytes(), version_drift.before_bytes
        )

        unsupported = Fixture(self.root / "unsupported")
        with self.assertRaisesRegex(
            module.SettingsError, "settings-swap-unsupported"
        ):
            unsupported.execute(swapper=UnsupportedSwapper())
        self.assertEqual(unsupported.settings.read_bytes(), unsupported.before_bytes)

        write_failure = Fixture(self.root / "write-failure")
        with mock.patch.object(
            module,
            "write_stage",
            side_effect=module.SettingsError("settings-stage-write-failed"),
        ):
            with self.assertRaisesRegex(
                module.SettingsError, "settings-stage-write-failed"
            ):
                write_failure.execute()
        self.assertEqual(
            write_failure.settings.read_bytes(), write_failure.before_bytes
        )

    def test_unexpected_post_exchange_error_rolls_back(self) -> None:
        fixture = Fixture(self.root / "unexpected-error")

        def barrier(name: str, _context: dict) -> None:
            if name == "after_exchange":
                raise OSError("fixture failure")

        result = fixture.execute(barrier=barrier)
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(
            result["cause"], "settings-transaction-step-failed"
        )
        self.assertEqual(fixture.settings.read_bytes(), fixture.before_bytes)

    def test_runtime_verification_failure_restores_exact_before_bytes(
        self,
    ) -> None:
        fixture = Fixture(self.root / "runtime-failure")
        calls = 0

        def verifier() -> dict:
            nonlocal calls
            calls += 1
            if calls == 1:
                return fixture.runtime_inventory()
            value = fixture.runtime_inventory()
            value["plugin_enabled"] = True
            value["owned_capability_ids"] = ["plugin:fixture-skill"]
            value["estate_capability_ids"] = [
                "builtin:base",
                "plugin:fixture-skill",
            ]
            return value

        result = fixture.execute(runtime_verifier=verifier)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(result["cause"], "plugin-runtime-after-mismatch")
        self.assertEqual(fixture.settings.read_bytes(), fixture.before_bytes)

    def test_fsync_failure_before_receipt_rolls_back(self) -> None:
        fixture = Fixture(self.root / "fsync-failure")
        real_fsync = module.fsync_directory
        failed = False

        def injected(path: Path) -> None:
            nonlocal failed
            if path == fixture.settings.parent and not failed:
                try:
                    document = json.loads(fixture.settings.read_text())
                except json.JSONDecodeError:
                    document = {}
                if (
                    document.get("enabledPlugins", {}).get("fixture@market")
                    is False
                ):
                    failed = True
                    raise OSError("fixture fsync failure")
            real_fsync(path)

        with mock.patch.object(module, "fsync_directory", side_effect=injected):
            result = fixture.execute()
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(fixture.settings.read_bytes(), fixture.before_bytes)
        self.assertFalse(
            (fixture.transactions / "fixture-disable/receipt.json").exists()
        )

    def test_rollback_failure_installs_recovery_fence(self) -> None:
        fixture = Fixture(self.root / "rollback-failure")
        calls = 0

        def verifier() -> dict:
            nonlocal calls
            calls += 1
            value = fixture.runtime_inventory()
            if calls > 1:
                value["plugin_enabled"] = True
            return value

        result = fixture.execute(
            runtime_verifier=verifier,
            swapper=FailingRollbackSwapper(),
        )
        self.assertEqual(result["status"], "recovery_required")
        self.assertTrue(fixture.recovery.exists())
        self.assertEqual(
            json.loads(fixture.recovery.read_text())["status"], "required"
        )

    def test_rename_immediately_before_exchange_is_preserved(self) -> None:
        fixture = Fixture(self.root / "pre-exchange")
        competing = (
            json.dumps(
                {
                    **fixture.before_document,
                    "userEdit": "before-exchange",
                }
            ).encode()
            + b"\n"
        )

        def barrier(name: str, context: dict) -> None:
            if name == "before_exchange":
                replacement = fixture.root / "replacement.json"
                replacement.write_bytes(competing)
                os.chmod(replacement, 0o600)
                os.replace(replacement, Path(context["settings_path"]))

        result = fixture.execute(barrier=barrier)
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(fixture.settings.read_bytes(), competing)

    def test_rename_immediately_after_exchange_enters_recovery(self) -> None:
        fixture = Fixture(self.root / "post-exchange")
        competing = json.dumps({"userEdit": "after-exchange"}).encode() + b"\n"

        def barrier(name: str, context: dict) -> None:
            if name == "after_exchange":
                replacement = fixture.root / "replacement.json"
                replacement.write_bytes(competing)
                os.chmod(replacement, 0o600)
                os.replace(replacement, Path(context["settings_path"]))

        result = fixture.execute(barrier=barrier)
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(fixture.settings.read_bytes(), competing)
        self.assertTrue(fixture.recovery.exists())
        displaced = Path(result["recovery"]["displaced"]["path"])
        self.assertEqual(displaced.read_bytes(), fixture.before_bytes)

    def test_retention_failure_keeps_recovery_fence_and_stage(self) -> None:
        fixture = Fixture(self.root / "retention-failure")
        competing = json.dumps({"userEdit": "after-exchange"}).encode() + b"\n"

        def barrier(name: str, context: dict) -> None:
            if name == "after_exchange":
                replacement = fixture.root / "replacement.json"
                replacement.write_bytes(competing)
                os.chmod(replacement, 0o600)
                os.replace(replacement, Path(context["settings_path"]))

        with mock.patch.object(
            module,
            "retain_file",
            side_effect=module.SettingsError(
                "settings-version-retention-failed"
            ),
        ):
            result = fixture.execute(barrier=barrier)
        self.assertEqual(result["status"], "recovery_required")
        recovery = json.loads(fixture.recovery.read_text())
        self.assertEqual(recovery["status"], "retention_pending")
        self.assertEqual(fixture.settings.read_bytes(), competing)
        self.assertTrue(
            Path(recovery["displaced"]["path"]).exists()
        )

    def test_missing_staged_name_after_exchange_keeps_recovery_fence(
        self,
    ) -> None:
        fixture = Fixture(self.root / "missing-stage")
        rogue = fixture.root / "writer-retained-preimage"

        def barrier(name: str, context: dict) -> None:
            if name == "after_exchange":
                os.replace(Path(context["staged_path"]), rogue)

        result = fixture.execute(barrier=barrier)
        self.assertEqual(result["status"], "recovery_required")
        self.assertTrue(fixture.recovery.exists())
        self.assertEqual(
            json.loads(fixture.recovery.read_text())["status"],
            "retention_pending",
        )
        self.assertEqual(rogue.read_bytes(), fixture.before_bytes)
        self.assertFalse(
            json.loads(fixture.settings.read_text())["enabledPlugins"][
                "fixture@market"
            ]
        )

    def test_open_descriptor_writes_are_preserved_on_both_inodes(self) -> None:
        preimage = Fixture(self.root / "open-preimage")
        descriptor = os.open(preimage.settings, os.O_WRONLY | os.O_APPEND)
        try:
            def write_preimage(name: str, _context: dict) -> None:
                if name == "after_exchange":
                    os.write(descriptor, b" ")
                    os.fsync(descriptor)

            result = preimage.execute(barrier=write_preimage)
        finally:
            os.close(descriptor)
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(
            preimage.settings.read_bytes(), preimage.before_bytes + b" "
        )

        active = Fixture(self.root / "open-active")

        def write_active(name: str, context: dict) -> None:
            if name == "after_exchange":
                descriptor = os.open(
                    Path(context["settings_path"]),
                    os.O_WRONLY | os.O_APPEND,
                )
                try:
                    os.write(descriptor, b" ")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)

        result = active.execute(barrier=write_active)
        self.assertEqual(result["status"], "recovery_required")
        self.assertTrue(active.settings.read_bytes().endswith(b" "))
        displaced = Path(result["recovery"]["displaced"]["path"])
        self.assertEqual(displaced.read_bytes(), active.before_bytes)

    def test_write_during_rollback_retains_every_competing_version(self) -> None:
        fixture = Fixture(self.root / "rollback-race")
        first = json.dumps({"userEdit": "first"}).encode() + b"\n"
        second = json.dumps({"userEdit": "second"}).encode() + b"\n"

        def barrier(name: str, context: dict) -> None:
            if name == "before_exchange":
                replacement = fixture.root / "first.json"
                replacement.write_bytes(first)
                os.chmod(replacement, 0o600)
                os.replace(replacement, Path(context["settings_path"]))
            elif name == "before_rollback":
                replacement = fixture.root / "second.json"
                replacement.write_bytes(second)
                os.chmod(replacement, 0o600)
                os.replace(replacement, Path(context["settings_path"]))

        result = fixture.execute(barrier=barrier)
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(fixture.settings.read_bytes(), second)
        displaced = Path(result["recovery"]["displaced"]["path"])
        self.assertEqual(displaced.read_bytes(), first)

    def test_recovery_state_blocks_later_transactions(self) -> None:
        fixture = Fixture(self.root / "recovery-block")
        fixture.recovery.parent.mkdir(parents=True)
        fixture.recovery.write_text('{"status":"required"}\n', encoding="utf-8")
        with self.assertRaisesRegex(
            module.SettingsError, "estate-recovery-required"
        ):
            fixture.execute()
        self.assertEqual(fixture.settings.read_bytes(), fixture.before_bytes)

    def test_cli_executes_the_same_reported_transaction(self) -> None:
        fixture = Fixture(self.root / "cli")
        request_path = fixture.root / "request.json"
        request_path.write_text(json.dumps(fixture.request), encoding="utf-8")
        verifier = fixture.root / "runtime-verifier.py"
        verifier.write_text(
            textwrap.dedent(
                """\
                import argparse
                import json

                parser = argparse.ArgumentParser()
                parser.add_argument("--settings", required=True)
                parser.add_argument("--plugin-id", required=True)
                args = parser.parse_args()
                settings = json.load(open(args.settings, encoding="utf-8"))
                disabled = settings.get("enabledPlugins", {}).get(args.plugin_id) is False
                owned = [] if disabled else ["plugin:fixture-skill"]
                print(json.dumps({
                    "schema_version": 1,
                    "copilot_version": "1.0.80",
                    "plugin_identity": {
                        "plugin_id": "fixture@market",
                        "source_identity": "installed:market/fixture",
                        "version": "1.0.0"
                    },
                    "plugin_enabled": not disabled,
                    "owned_capability_ids": owned,
                    "estate_capability_ids": ["builtin:base", *owned]
                }))
                """
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "disable",
                "--request",
                str(request_path),
                "--settings",
                str(fixture.settings),
                "--transaction-root",
                str(fixture.transactions),
                "--qualification-root",
                str(fixture.qualifications),
                "--lock",
                str(fixture.lock),
                "--recovery-state",
                str(fixture.recovery),
                "--runtime-verifier",
                sys.executable,
                str(verifier),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output["ok"])
        self.assertFalse(
            json.loads(fixture.settings.read_text())["enabledPlugins"][
                "fixture@market"
            ]
        )

    def test_cli_refuses_symlinked_settings_with_structured_error(self) -> None:
        fixture = Fixture(self.root / "cli-symlink")
        request_path = fixture.root / "request.json"
        request_path.write_text(json.dumps(fixture.request), encoding="utf-8")
        link = fixture.root / "settings-link.json"
        link.symlink_to(fixture.settings)
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "disable",
                "--request",
                str(request_path),
                "--settings",
                str(link),
                "--transaction-root",
                str(fixture.transactions),
                "--qualification-root",
                str(fixture.qualifications),
                "--lock",
                str(fixture.lock),
                "--recovery-state",
                str(fixture.recovery),
                "--runtime-verifier",
                "/usr/bin/false",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            json.loads(result.stdout)["error"]["code"],
            "settings-preimage-invalid",
        )
        self.assertEqual(fixture.settings.read_bytes(), fixture.before_bytes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
