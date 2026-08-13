#!/usr/bin/env python3
"""Deterministic checks for the SSH skill-publisher transport."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile
from argparse import Namespace
from pathlib import Path

SCRIPT = Path(__file__).with_name("ssh-skill-publisher.py")
SPEC = importlib.util.spec_from_file_location("ssh_skill_publisher", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class SshSkillPublisherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        skill = self.bundle / "learned"
        skill.mkdir(parents=True)
        body = b"---\nname: learned\ndescription: fixture\n---\n"
        (skill / "SKILL.md").write_bytes(body)
        manifest = {
            "contract_version": 1,
            "bundle_id": "sha256:" + "a" * 64,
            "files": [
                {
                    "path": "learned/SKILL.md",
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
            ],
        }
        (self.bundle / "dreaming-bundle-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        self.bundle_id = manifest["bundle_id"]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_valid_archive_extracts_exact_bundle(self) -> None:
        payload = module.archive_bundle(self.bundle, self.bundle_id)
        destination = self.root / "published" / ("a" * 64)
        destination.parent.mkdir()
        module.safe_extract(payload, destination, self.bundle_id)
        self.assertEqual(
            (destination / "learned/SKILL.md").read_bytes(),
            (self.bundle / "learned/SKILL.md").read_bytes(),
        )
        module.validate_bundle(destination, self.bundle_id)

    def assert_archive_rejected(self, entries: list[tuple[zipfile.ZipInfo | str, bytes]]) -> None:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, body in entries:
                archive.writestr(name, body)
        destination = self.root / f"rejected-{len(list(self.root.glob('rejected-*')))}"
        with self.assertRaises(module.PublisherError):
            module.safe_extract(output.getvalue(), destination, self.bundle_id)
        self.assertFalse(destination.exists())

    def test_rejects_traversal_symlink_duplicate_and_incomplete_archives(self) -> None:
        self.assert_archive_rejected([("../escape", b"x")])
        symlink = zipfile.ZipInfo("learned/SKILL.md")
        symlink.create_system = 3
        symlink.external_attr = 0o120777 << 16
        self.assert_archive_rejected([(symlink, b"target")])
        self.assert_archive_rejected(
            [("learned/SKILL.md", b"one"), ("learned/SKILL.md", b"two")]
        )
        self.assert_archive_rejected(
            [("dreaming-bundle-manifest.json", b"{}")]
        )

    def test_receiver_identity_binds_host_and_code(self) -> None:
        receiver_id = self.root / "receiver-id"
        receiver_id.write_text("client-one\n", encoding="ascii")
        adapter = self.root / "adapter.py"
        adapter.write_text("print('fixture')\n", encoding="utf-8")
        args = Namespace(
            receiver_id_file=str(receiver_id),
            expected_receiver_id="client-one",
            expected_receiver_sha=module.sha256_file(SCRIPT),
            expected_adapter_sha=module.sha256_file(adapter),
            adapter_script=str(adapter),
        )
        identity = module.receiver_identity(args)
        self.assertEqual(identity["receiver_id"], "client-one")
        args.expected_receiver_id = "wrong-client"
        with self.assertRaises(module.PublisherError):
            module.receiver_identity(args)

    def test_remote_command_preserves_empty_and_quoted_arguments(self) -> None:
        args = Namespace(
            remote_python="/fixture/python",
            remote_script="/fixture/receiver.py",
            remote_adapter_python="/fixture/python",
            remote_adapter_script="/fixture/adapter.py",
            remote_bundle_root="/fixture/bundles",
            remote_ownership_journal="/fixture/journal.json",
            remote_operation_root="/fixture/operations",
            remote_receiver_id_file="/fixture/receiver-id",
            expected_receiver_id="client-one",
            expected_receiver_sha="b" * 64,
            expected_adapter_sha="c" * 64,
            ssh_bin="/usr/bin/ssh",
            address_family="6",
            host="fixture@host",
        )
        command = module.remote_command(
            args,
            [
                "--vendor",
                "copilot",
                "--role",
                "skill-publisher",
                "verify",
                "--bundle-id",
                'sha256:"value with spaces"',
            ],
        )
        self.assertEqual(command[:4], ["/usr/bin/ssh", "-6", "-o", "BatchMode=yes"])
        self.assertIn("'sha256:\"value with spaces\"'", command[-1])

    def test_internal_recovery_commands_reach_adapter(self) -> None:
        args = Namespace(
            adapter_python="/usr/bin/python3",
            adapter_script="/fixture/adapter.py",
            ownership_journal="/fixture/publisher-ownership.json",
        )
        snapshot = module.adapter_command(
            args,
            [
                "--vendor",
                "copilot",
                "--role",
                "skill-publisher",
                "snapshot",
                "--bundle",
                str(self.bundle),
                "--bundle-id",
                self.bundle_id,
            ],
        )
        self.assertEqual(snapshot[6], "--ownership-journal")
        self.assertEqual(snapshot[8], "snapshot")
        reconcile = module.adapter_command(
            args,
            [
                "--vendor",
                "copilot",
                "--role",
                "skill-publisher",
                "reconcile",
                "--operation",
                "/fixture/operation.json",
                "--outcome",
                "auto",
            ],
        )
        self.assertEqual(reconcile[8], "reconcile")

    def test_fake_ssh_publication_and_recovery_transaction(self) -> None:
        fake_ssh = self.root / "ssh"
        fake_ssh.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import subprocess
                import sys

                process = subprocess.run(
                    ["/bin/sh", "-c", sys.argv[-1]],
                    input=sys.stdin.buffer.read(),
                    capture_output=True,
                )
                sys.stdout.buffer.write(process.stdout)
                sys.stderr.buffer.write(process.stderr)
                raise SystemExit(process.returncode)
                """
            ),
            encoding="utf-8",
        )
        os.chmod(fake_ssh, 0o755)
        adapter = self.root / "adapter.py"
        adapter.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import pathlib
                import sys

                COMMANDS = {
                    "contract", "doctor", "inventory", "install", "snapshot",
                    "reconcile", "verify", "remove",
                }
                args = sys.argv[1:]
                command = next(value for value in args if value in COMMANDS)
                journal = pathlib.Path(args[args.index("--ownership-journal") + 1])
                calls = journal.with_suffix(".calls")
                calls.parent.mkdir(parents=True, exist_ok=True)
                with calls.open("a", encoding="utf-8") as handle:
                    handle.write(command + "\\n")

                def option(name):
                    return args[args.index(name) + 1]

                def load(path, default):
                    try:
                        return json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        return default

                def save(path, value):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(value), encoding="utf-8")

                def descriptor(bundle, bundle_id):
                    root = pathlib.Path(bundle)
                    manifest = load(root / "dreaming-bundle-manifest.json", {})
                    return {
                        "vendor": "copilot",
                        "bundle": str(root),
                        "bundle_id": bundle_id,
                        "name": manifest.get("publication_name", "fixture-publication"),
                        "skills": sorted(
                            path.parent.name for path in root.glob("*/SKILL.md")
                        ),
                    }

                state = load(journal, {})
                if command == "contract":
                    result = {
                        "ok": True,
                        "protocol": "dreaming.skill-publisher",
                        "role": "skill-publisher",
                        "capabilities": [
                            "content-addressed-bundle",
                            "ownership-safe-remove",
                            "exact-inventory",
                        ],
                    }
                elif command == "doctor":
                    result = {"ok": True, "healthy": True}
                elif command == "inventory":
                    row = state.get("copilot")
                    result = {
                        "ok": True,
                        "owned_bundle_ids": [row["bundle_id"]] if row else [],
                    }
                elif command == "snapshot":
                    new = descriptor(option("--bundle"), option("--bundle-id"))
                    result = {"ok": True, "prior": state.get("copilot"), "new": new}
                elif command == "install":
                    row = descriptor(option("--bundle"), option("--bundle-id"))
                    state["copilot"] = row
                    save(journal, state)
                    result = {"ok": True, "installed": True, "bundle_id": row["bundle_id"]}
                elif command == "verify":
                    row = state.get("copilot")
                    bundle_id = option("--bundle-id")
                    verified = bool(
                        row
                        and row.get("bundle_id") == bundle_id
                        and pathlib.Path(row["bundle"]).is_dir()
                    )
                    result = {
                        "ok": True,
                        "verified": verified,
                        "bundle_id": bundle_id if verified else None,
                    }
                elif command == "reconcile":
                    operation = load(pathlib.Path(option("--operation")), {})
                    outcome = option("--outcome")
                    commit = outcome == "auto" and operation.get("new")
                    row = operation.get("new") if commit else operation.get("prior")
                    if row:
                        state["copilot"] = row
                    else:
                        state.pop("copilot", None)
                    save(journal, state)
                    result = {
                        "ok": True,
                        "status": "committed" if commit else "rolled_back",
                        "descriptor": row,
                    }
                else:
                    state.pop("copilot", None)
                    save(journal, state)
                    result = {"ok": True, "removed": True}
                print(json.dumps(result))
                """
            ),
            encoding="utf-8",
        )
        receiver_id = self.root / "receiver-id"
        receiver_id.write_text("fixture-client\n", encoding="ascii")
        journal = self.root / "publisher-ownership.json"
        journal.write_text(
            json.dumps({"claude": {"preserved": True}}), encoding="utf-8"
        )
        operations = self.root / "operations"
        summary = self.root / "summary.json"
        recovery = self.root / "recovery.json"
        base = [
            sys.executable,
            str(SCRIPT),
            "--ssh-bin",
            str(fake_ssh),
            "--host",
            "fixture",
            "--remote-python",
            sys.executable,
            "--remote-script",
            str(SCRIPT),
            "--remote-adapter-python",
            sys.executable,
            "--remote-adapter-script",
            str(adapter),
            "--remote-bundle-root",
            str(self.root / "published"),
            "--remote-ownership-journal",
            str(journal),
            "--remote-operation-root",
            str(operations),
            "--remote-receiver-id-file",
            str(receiver_id),
            "--expected-receiver-id",
            "fixture-client",
            "--expected-receiver-sha",
            module.sha256_file(SCRIPT),
            "--expected-adapter-sha",
            module.sha256_file(adapter),
            "--summary",
            str(summary),
            "--recovery-state",
            str(recovery),
            "--",
            "--vendor",
            "copilot",
            "--role",
            "skill-publisher",
        ]
        install = subprocess.run(
            base
            + [
                "install",
                "--bundle",
                str(self.bundle),
                "--bundle-id",
                self.bundle_id,
            ],
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
        result = json.loads(install.stdout)
        self.assertEqual(result["descriptor"]["skills"], ["learned"])
        self.assertTrue(summary.is_file())
        state = json.loads(journal.read_text(encoding="utf-8"))
        self.assertEqual(state["claude"], {"preserved": True})
        self.assertEqual(state["copilot"]["bundle_id"], self.bundle_id)

        pending = {
            "schema_version": 1,
            "vendor": "copilot",
            "phase": "installing",
            "receiver": {
                "receiver_id": "fixture-client",
                "receiver_sha256": module.sha256_file(SCRIPT),
                "adapter_sha256": module.sha256_file(adapter),
            },
            "prior": None,
            "new": state["copilot"],
        }
        operations.mkdir(exist_ok=True)
        (operations / "copilot.json").write_text(
            json.dumps(pending), encoding="utf-8"
        )
        doctor = subprocess.run(
            base + ["doctor"], text=True, capture_output=True, timeout=30
        )
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        self.assertEqual(
            json.loads(doctor.stdout)["recovered_operation"]["phase"], "committed"
        )

        calls = journal.with_suffix(".calls")
        before = calls.read_text(encoding="utf-8")
        wrong_receiver = list(base)
        wrong_receiver[wrong_receiver.index("--expected-receiver-id") + 1] = "wrong"
        refused = subprocess.run(
            wrong_receiver + ["doctor"],
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(calls.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
