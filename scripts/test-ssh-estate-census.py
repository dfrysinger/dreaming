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
ESTATE = (
    SCRIPT.parent.parent
    / "skills"
    / "skill-review"
    / "scripts"
    / "dreaming-estate.py"
)
sys.path.insert(0, str(ESTATE.parent))
ESTATE_SPEC = importlib.util.spec_from_file_location("dreaming_estate_subject", ESTATE)
assert ESTATE_SPEC and ESTATE_SPEC.loader
estate = importlib.util.module_from_spec(ESTATE_SPEC)
sys.modules[ESTATE_SPEC.name] = estate
ESTATE_SPEC.loader.exec_module(estate)
CONTENT_POLICY = (
    SCRIPT.parent.parent
    / "skills"
    / "skill-review"
    / "references"
    / "remote-subject-content-policy-v1.json"
)


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
                def collect_bundle(config):
                    census = collect(config)
                    return {
                        "census": census,
                        "usage": {
                            "schema_version": 1,
                            "snapshot_sha256": "sha256:" + "b" * 64,
                            "census_snapshot_sha256": census["snapshot_sha256"],
                            "host_id": census["host_id"],
                            "collected_at": "fixture",
                            "usage_index_path": config["usage_index_path"],
                        },
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
        self.assertEqual(
            result["usage"]["census_snapshot_sha256"],
            result["census"]["snapshot_sha256"],
        )
        self.assertEqual(
            result["usage"]["usage_index_path"],
            str(self.root / ".local/state/dreaming/copilot-usage-index.json"),
        )

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
            remote_copilot_session_root=None,
            remote_usage_index_path="/Users/fixture user/.local/state/dreaming/index.json",
            user_context_cwd=None,
            remote_project_contexts_file=None,
            ssh_bin="/usr/bin/ssh",
            address_family="6",
            host="fixture@fd7a::1",
            usage_max_sessions=100,
            usage_max_bytes=1000,
        )
        command = module.remote_command(args)
        self.assertEqual(command[:4], ["/usr/bin/ssh", "-6", "-o", "BatchMode=yes"])
        self.assertIn("'/Users/fixture user'", command[-1])
        self.assertIn(
            "'/Users/fixture user/.local/state/dreaming/index.json'",
            command[-1],
        )

    def remote_subject_fixture(
        self, skill_files: dict[str, bytes]
    ) -> tuple[dict[str, object], dict[str, str]]:
        skill = self.root / "skills" / "fixture-skill"
        skill.mkdir(parents=True)
        for relative, content in skill_files.items():
            target = skill / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        files, inventory_sha = estate.skill_inventory(skill)
        capability_id = "sha256:" + "c" * 64
        census = {
            "host_id": "macbook",
            "physical_instances": [
                {
                    "host_id": "macbook",
                    "root_id": "personal-copilot",
                    "relative_path": "fixture-skill",
                    "absolute_path": str(skill),
                    "canonical_capability_id": capability_id,
                    "inventory_sha256": inventory_sha,
                    "files": files,
                }
            ],
        }
        request = {
            "census_snapshot_sha256": "sha256:" + "a" * 64,
            "origin_host_id": "macbook",
            "origin_root_id": "personal-copilot",
            "origin_relative_path": "fixture-skill",
            "origin_path": str(skill),
            "canonical_capability_id": capability_id,
            "origin_inventory_sha256": inventory_sha,
        }
        return census, request

    def test_remote_subject_exports_exact_safe_text_without_sidecars(self) -> None:
        census, request = self.remote_subject_fixture(
            {
                "SKILL.md": (
                    b"---\nname: fixture-skill\ndescription: test\n---\n"
                    b"Discuss transcript handling and /Users/example paths.\n"
                ),
                "references/guide.md": b"Use EXAMPLE_TOKEN as a placeholder.\n",
                ".agent-created.json": b'{"metadata":"not evaluator input"}\n',
                ".skill-evaluation-policy.json": b'{"untrusted":true}\n',
            }
        )
        result = estate.export_remote_subject(
            census,
            request,
            estate.remote_subject_content_policy(CONTENT_POLICY),
        )
        self.assertEqual(result["kind"], "remote_evaluation_subject")
        self.assertEqual(
            [item["path"] for item in result["excluded_sidecars"]],
            [".agent-created.json", ".skill-evaluation-policy.json"],
        )
        self.assertEqual(
            [item["path"] for item in result["candidate_inventory"]],
            ["SKILL.md", "references/guide.md"],
        )
        self.assertEqual(
            [item["path"] for item in result["files"]],
            ["SKILL.md", "references/guide.md"],
        )
        self.assertRegex(result["candidate_id"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(result["receipt_sha256"], r"^sha256:[0-9a-f]{64}$")

    def test_remote_subject_refuses_credentials_and_binary_content(self) -> None:
        for name, content in (
            ("credential", b"token = github_pat_abcdefghijklmnop\n"),
            ("binary", b"\xff\xfe\x00\x01"),
        ):
            with self.subTest(name=name):
                census, request = self.remote_subject_fixture(
                    {
                        "SKILL.md": (
                            b"---\nname: fixture-skill\ndescription: test\n---\n"
                        ),
                        f"references/{name}.txt": content,
                    }
                )
                with self.assertRaises(estate.EstateError):
                    estate.export_remote_subject(
                        census,
                        request,
                        estate.remote_subject_content_policy(CONTENT_POLICY),
                    )
                for child in (self.root / "skills").iterdir():
                    if child.is_dir():
                        for path in sorted(child.rglob("*"), reverse=True):
                            if path.is_file():
                                path.unlink()
                            elif path.is_dir():
                                path.rmdir()
                        child.rmdir()

    def test_subject_remote_command_pins_host_key_and_request(self) -> None:
        known_hosts = self.root / "known-hosts"
        known_hosts.write_text("fixture ssh-ed25519 AAAA\n", encoding="ascii")
        args = Namespace(
            remote_python="/fixture/python",
            remote_script="/fixture/receiver.py",
            remote_estate_script="/fixture/estate.py",
            remote_receiver_id_file="/fixture/receiver-id",
            expected_receiver_id="fixture-client",
            expected_receiver_sha="a" * 64,
            expected_collector_sha="b" * 64,
            remote_content_policy="/fixture/policy.json",
            expected_content_policy_sha="d" * 64,
            target_host_id="macbook",
            target_home="/Users/fixture",
            remote_copilot_binary="/fixture/copilot",
            remote_copilot_session_root=None,
            remote_usage_index_path=None,
            user_context_cwd=None,
            remote_project_contexts_file=None,
            ssh_bin="/usr/bin/ssh",
            address_family="6",
            host="fixture@fd7a::1",
            known_hosts_file=str(known_hosts),
            census_snapshot_sha256="sha256:" + "1" * 64,
            origin_root_id="personal-copilot",
            origin_relative_path="fixture-skill",
            origin_path="/Users/fixture/.copilot/skills/fixture-skill",
            canonical_capability_id="sha256:" + "2" * 64,
            origin_inventory_sha256="sha256:" + "3" * 64,
        )
        command = module.subject_remote_command(args)
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn(f"UserKnownHostsFile={known_hosts}", command)
        self.assertIn("--receive-subject", command[-1])
        self.assertIn("--origin-root-id personal-copilot", command[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
