#!/usr/bin/env python3
"""Deterministic checks for the SSH skill-publisher transport."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tempfile
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
