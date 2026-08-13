#!/usr/bin/env python3
"""Deterministic checks for the SSH estate-census transport."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from argparse import Namespace
from pathlib import Path

SCRIPT = Path(__file__).with_name("ssh-estate-census.py")
SPEC = importlib.util.spec_from_file_location("ssh_estate_census", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class SshEstateCensusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_receiver_identity_binds_host_and_collector(self) -> None:
        receiver_id = self.root / "receiver-id"
        receiver_id.write_text("fixture-client\n", encoding="ascii")
        collector = self.root / "collector.py"
        collector.write_text("def collect(config): return config\n", encoding="utf-8")
        args = Namespace(
            receiver_id_file=str(receiver_id),
            estate_script=str(collector),
            expected_receiver_id="fixture-client",
            expected_receiver_sha=module.sha256_file(SCRIPT),
            expected_collector_sha=module.sha256_file(collector),
        )
        identity = module.receiver_identity(args)
        self.assertEqual(identity["receiver_id"], "fixture-client")
        args.expected_collector_sha = "0" * 64
        with self.assertRaises(module.CensusError):
            module.receiver_identity(args)

    def test_fake_ssh_collects_one_identity_bound_snapshot(self) -> None:
        fake_ssh = self.root / "ssh"
        fake_ssh.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import subprocess
                import sys
                process = subprocess.run(
                    ["/bin/sh", "-c", sys.argv[-1]],
                    capture_output=True,
                    text=True,
                )
                sys.stdout.write(process.stdout)
                sys.stderr.write(process.stderr)
                raise SystemExit(process.returncode)
                """
            ),
            encoding="utf-8",
        )
        os.chmod(fake_ssh, 0o755)
        collector = self.root / "collector.py"
        collector.write_text(
            textwrap.dedent(
                """\
                def collect(config):
                    return {
                        "schema_version": 1,
                        "snapshot_sha256": "sha256:" + "a" * 64,
                        "host_id": config["host_id"],
                    }
                """
            ),
            encoding="utf-8",
        )
        receiver_id = self.root / "receiver-id"
        receiver_id.write_text("fixture-client\n", encoding="ascii")
        command = [
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
            "--remote-estate-script",
            str(collector),
            "--remote-receiver-id-file",
            str(receiver_id),
            "--remote-copilot-binary",
            "/fixture/copilot",
            "--expected-receiver-id",
            "fixture-client",
            "--expected-receiver-sha",
            module.sha256_file(SCRIPT),
            "--expected-collector-sha",
            module.sha256_file(collector),
            "--target-host-id",
            "macbook",
            "--target-home",
            str(self.root),
        ]
        process = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["census"]["host_id"], "macbook")
        self.assertEqual(result["receiver"]["receiver_id"], "fixture-client")

    def test_remote_command_quotes_ipv6_and_paths(self) -> None:
        args = Namespace(
            remote_python="/fixture/python",
            remote_script="/fixture/receiver.py",
            remote_estate_script="/fixture/estate.py",
            remote_receiver_id_file="/fixture/receiver id",
            expected_receiver_id="fixture-client",
            expected_receiver_sha="a" * 64,
            expected_collector_sha="b" * 64,
            target_host_id="macbook",
            target_home="/Users/fixture user",
            remote_copilot_binary="/fixture/copilot",
            user_context_cwd=None,
            remote_project_contexts_file=None,
            ssh_bin="/usr/bin/ssh",
            address_family="6",
            host="fixture@fd7a::1",
        )
        command = module.remote_command(args)
        self.assertEqual(command[:4], ["/usr/bin/ssh", "-6", "-o", "BatchMode=yes"])
        self.assertIn("'/Users/fixture user'", command[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
