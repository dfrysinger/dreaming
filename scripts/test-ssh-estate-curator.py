#!/usr/bin/env python3
"""Deterministic CHK-03 checks for remote personal-skill transactions."""

from __future__ import annotations

import hashlib
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
from typing import Any

SCRIPT = Path(__file__).with_name("ssh-estate-curator.py").resolve()
REPO = SCRIPT.parent.parent
CURATOR = REPO / "skills/skill-curator/scripts/curator-run.py"
ARCHIVE = REPO / "skills/skill-manage/scripts/archive-skill.sh"
RESTORE = REPO / "skills/skill-manage/scripts/restore-skill.sh"
ESTATE = REPO / "skills/skill-review/scripts/dreaming-estate.py"
SCANNER_NAME = "dependency-scanner.py"
WORK_PARENT = REPO / ".test-work"
WORK_PARENT.mkdir(exist_ok=True)
WORK_ROOT = Path(tempfile.mkdtemp(prefix="estate-curator.", dir=WORK_PARENT))


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


transport = load_module("ssh_estate_curator_test", SCRIPT)
estate = load_module("dreaming_estate_chk03_test", ESTATE)
curator = load_module("curator_run_chk03_test", CURATOR)


def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
        **kwargs,
    )


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        self.home = root / "home"
        self.public = self.home / "code/skills"
        self.local = self.home / ".copilot/skills"
        self.state = root / "state"
        self.runs = self.state / "curator-runs"
        self.operations = root / "operations"
        self.recovery = root / "estate-recovery-required.json"
        self.local_recovery = root / "mini-recovery-required.json"
        self.receiver_id_file = root / "receiver-id"
        self.curator_state = root / "curator.json"
        self.halt = self.state / "disable-daemon"
        self.lock = self.state / "writer-lock.sqlite"
        self.scanner = root / SCANNER_NAME
        self.copilot = root / "copilot"
        self.inventory_path = root / "dependency-inventory.json"
        self.skill_name = "legacy-fixture"
        self.skill = self.local / self.skill_name
        self._init()

    def git(self, *arguments: str) -> str:
        result = run(["git", "-C", str(self.local), *arguments])
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    def _init_git(self, path: Path) -> None:
        path.mkdir(parents=True)
        result = run(["git", "-C", str(path), "init", "-q", "-b", "main"])
        if result.returncode:
            result = run(["git", "-C", str(path), "init", "-q"])
            if result.returncode:
                raise AssertionError(result.stderr)
        for key, value in (
            ("user.name", "skill-review"),
            ("user.email", "copilot@github.com"),
            ("core.hooksPath", "/dev/null"),
        ):
            checked = run(["git", "-C", str(path), "config", key, value])
            if checked.returncode:
                raise AssertionError(checked.stderr)

    def _init(self) -> None:
        self._init_git(self.public)
        self._init_git(self.local)
        (self.public / "skills").mkdir()
        (self.public / "README.md").write_text("fixture\n", encoding="utf-8")
        run(["git", "-C", str(self.public), "add", "."])
        run(["git", "-C", str(self.public), "commit", "-qm", "base"])
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text(
            textwrap.dedent(
                f"""\
                ---
                name: {self.skill_name}
                description: CHK-03 transaction fixture.
                author: skill-review
                ---

                # Fixture

                Exact bytes must survive retirement.
                """
            ),
            encoding="utf-8",
        )
        (self.skill / ".agent-created").write_bytes(b"")
        envelope = {
            "schema_version": 2,
            "skill": self.skill_name,
            "created_by": "skill-review",
            "source_session_id": "fixture-session",
            "source_mode": "dispatch",
            "review_prompt_version": "skill-review-2",
            "created_at": "2025-01-01T00:00:00+00:00",
            "evidence": [
                {
                    "task_key": "task:11111111-1111-1111-1111-111111111111",
                    "session_id": "fixture-session",
                    "observed_at": "2025-01-01T00:00:00+00:00",
                    "independence": "verified",
                    "evidence_kind": "successful-procedure",
                    "summary": "CHK-03 transaction fixture provenance.",
                }
            ],
            "routing": {"destination": "skill", "reason": "Fixture."},
            "claims": [],
            "evaluation": {
                "status": "not_evaluated",
                "evaluated_at": None,
                "candidate_id": None,
                "model": None,
                "source_case": None,
                "sibling_case": None,
                "waiver_class": None,
                "waiver_reason": None,
            },
        }
        (self.skill / ".agent-created.json").write_text(
            json.dumps(envelope), encoding="utf-8"
        )
        (self.local / "README.md").write_text("personal root\n", encoding="utf-8")
        run(["git", "-C", str(self.local), "add", "."])
        committed = run(["git", "-C", str(self.local), "commit", "-qm", "base"])
        if committed.returncode:
            raise AssertionError(committed.stderr)
        self.receiver_id_file.write_text("macbook-fixture\n", encoding="ascii")
        self.curator_state.write_text('{"paused":false}\n', encoding="utf-8")
        self.state.mkdir()
        (self.home / ".copilot/settings.json").write_text(
            '{"skillDirectories":[]}\n', encoding="utf-8"
        )
        self._write_copilot()
        self._write_scanner()
        self.set_inventory(live=True)

    def _write_copilot(self) -> None:
        self.copilot.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env python3
                import json
                import pathlib
                import sys

                args = sys.argv[1:]
                skill = pathlib.Path({str(self.skill)!r})
                if args == ["--version"]:
                    print("GitHub Copilot CLI 1.0.79")
                elif args == ["skill", "list", "--json"]:
                    rows = []
                    if skill.is_dir():
                        rows.append({{
                            "name": {self.skill_name!r},
                            "source": "personal",
                            "path": str(skill),
                            "enabled": True,
                        }})
                    print(json.dumps(rows))
                elif args == ["plugin", "list"]:
                    print("Installed plugins:\\n  (none)")
                else:
                    raise SystemExit(2)
                """
            ),
            encoding="utf-8",
        )
        os.chmod(self.copilot, 0o755)

    def _write_scanner(self) -> None:
        self.scanner.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env python3
                import json
                import pathlib
                import sys

                path = pathlib.Path({str(self.inventory_path)!r})
                data = json.loads(path.read_text(encoding="utf-8"))
                if "--inventory" in sys.argv:
                    print(json.dumps(data, sort_keys=True))
                    raise SystemExit(0)
                if "--check" in sys.argv:
                    if data.get("complete") is not True:
                        raise SystemExit(2)
                    name = sys.argv[sys.argv.index("--check") + 1]
                    rows = [row for row in data.get("skills", []) if row.get("name") == name]
                    if len(rows) != 1 or rows[0].get("pinned") or rows[0].get("implicit_pin"):
                        raise SystemExit(2)
                    raise SystemExit(0)
                raise SystemExit(2)
                """
            ),
            encoding="utf-8",
        )
        os.chmod(self.scanner, 0o755)

    def set_inventory(
        self,
        *,
        live: bool,
        complete: bool = True,
        pinned: bool = False,
        implicit_pin: bool = False,
    ) -> None:
        rows = (
            [
                {
                    "name": self.skill_name,
                    "root": "local",
                    "path": str(self.skill),
                    "pinned": pinned,
                    "implicit_pin": implicit_pin,
                }
            ]
            if live
            else []
        )
        self.inventory_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "complete": complete,
                    "skills": rows,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def receiver_identity(self) -> dict[str, str]:
        return {
            "receiver_id": "macbook-fixture",
            "receiver_sha256": transport.sha256_file(SCRIPT),
            "curator_sha256": transport.sha256_file(CURATOR),
            "archive_sha256": transport.sha256_file(ARCHIVE),
            "restore_sha256": transport.sha256_file(RESTORE),
            "estate_sha256": transport.sha256_file(ESTATE),
            "dependency_scanner_sha256": transport.sha256_file(self.scanner),
        }

    def receiver_arguments(self) -> list[str]:
        identity = self.receiver_identity()
        return [
            sys.executable,
            str(SCRIPT),
            "--receive",
            "--curator-runner",
            str(CURATOR),
            "--archive-tool",
            str(ARCHIVE),
            "--restore-tool",
            str(RESTORE),
            "--estate-script",
            str(ESTATE),
            "--dependency-scanner",
            str(self.scanner),
            "--public-root",
            str(self.public),
            "--personal-root",
            str(self.local),
            "--review-state-dir",
            str(self.state),
            "--runs-dir",
            str(self.runs),
            "--curator-state-file",
            str(self.curator_state),
            "--halt-switch",
            str(self.halt),
            "--lock-dir",
            str(self.lock),
            "--operation-root",
            str(self.operations),
            "--recovery-state",
            str(self.recovery),
            "--receiver-id-file",
            str(self.receiver_id_file),
            "--target-home",
            str(self.home),
            "--copilot-binary",
            str(self.copilot),
            "--user-context-cwd",
            str(self.home),
            "--expected-receiver-id",
            identity["receiver_id"],
            "--expected-receiver-sha",
            identity["receiver_sha256"],
            "--expected-curator-sha",
            identity["curator_sha256"],
            "--expected-archive-sha",
            identity["archive_sha256"],
            "--expected-restore-sha",
            identity["restore_sha256"],
            "--expected-estate-sha",
            identity["estate_sha256"],
            "--expected-dependency-scanner-sha",
            identity["dependency_scanner_sha256"],
        ]

    def environment(self, **extra: str) -> dict[str, str]:
        return {
            **os.environ,
            "TMPDIR": str(self.root / "scratch"),
            "PYTHONDONTWRITEBYTECODE": "1",
            **extra,
        }

    def census(self, *, live: bool) -> dict[str, Any]:
        self.assert_runtime_state(live)
        return estate.collect(
            {
                "host_id": "macbook-fixture",
                "target_home": str(self.home),
                "user_context_cwd": str(self.home),
                "copilot_binary": str(self.copilot),
            }
        )

    def assert_runtime_state(self, live: bool) -> None:
        if self.skill.is_dir() is not live:
            raise AssertionError("fixture runtime state differs from requested census")

    def classification(self) -> dict[str, Any]:
        files, _ = estate.skill_inventory(self.skill)
        return estate.classify_skill_authority(
            self.skill,
            {
                "class": "personal",
                "path": str(self.local),
                "legacy_proofs": {},
            },
            self.skill_name,
            files,
            evidence_tool=REPO
            / "skills/skill-review/scripts/evidence-envelope.py",
        )

    def halt_state(self) -> dict[str, Any]:
        return {
            "halt_switch": str(self.halt),
            "curator_state": str(self.curator_state),
            "curator_state_sha256": hashlib.sha256(
                self.curator_state.read_bytes()
            ).hexdigest(),
            "paused": False,
            "halted": False,
            "recovery_state": str(self.recovery),
            "recovery_required": False,
        }

    def decision(self, label: str) -> dict[str, Any]:
        payload = {"kind": label, "candidate": self.skill_name, "revision": 1}
        return {
            "status": "passed",
            "payload": payload,
            "sha256": f"sha256:{curator.hash_json(payload)}",
        }

    def request(
        self,
        op_id: str,
        *,
        kind: str = "archive",
        target_override: dict[str, Any] | None = None,
        census_override: dict[str, Any] | None = None,
        expected_head: str | None = None,
        expected_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        live = kind == "archive"
        census = census_override or self.census(live=live)
        if live:
            instance = next(
                row
                for row in census["physical_instances"]
                if row["skill_name"] == self.skill_name
            )
            classification = self.classification()
            target = {
                "skill": self.skill_name,
                "instance_id": instance["instance_id"],
                "canonical_capability_id": instance[
                    "canonical_capability_id"
                ],
                "absolute_path": str(self.skill),
                "relative_path": self.skill_name,
                "inventory_sha256": instance["inventory_sha256"],
                "authority_class": classification["authority"],
                "provenance": classification["provenance"],
                "verified_evidence": classification["_verified_evidence"],
                "provenance_inputs": {"policy": None, "proof": None},
            }
        else:
            archive_request = load_json(
                self.operations
                / "requests"
                / (
                    load_json(
                        self.operations
                        / "operations"
                        / "archive-roundtrip.json"
                    )["request_sha256"]
                    + ".json"
                )
            )
            target = dict(archive_request["target"])
            target["verified_evidence"] = {
                **target["verified_evidence"],
                "archive_request_sha256": archive_request["request_sha256"],
            }
        if target_override:
            target.update(target_override)
        inventory = json.loads(self.inventory_path.read_text(encoding="utf-8"))
        dirty = curator.dirty_snapshot(self.local)
        head = expected_head or self.git("rev-parse", "HEAD")
        expected = (
            {
                "live_tree": "absent",
                "retirement_record": "present",
                "tombstone": "present",
                "restore_source_head": head,
            }
            if kind == "archive"
            else {
                "live_tree_sha256": curator.hash_json(
                    curator.git_tree_inventory(
                        self.local,
                        json.loads(
                            (self.state / "retired" / f"{self.skill_name}.json").read_text(
                                encoding="utf-8"
                            )
                        )["restore_sha"],
                        self.skill_name,
                    )
                ),
                "retirement_record": "absent",
                "tombstone": "absent",
                "retirement_history": "present",
                "restore_source_head": json.loads(
                    (self.state / "retired" / f"{self.skill_name}.json").read_text(
                        encoding="utf-8"
                    )
                )["restore_sha"],
            }
        )
        if expected_override:
            expected.update(expected_override)
        payload = {
            "schema_version": 1,
            "protocol": "dreaming.estate-curator",
            "op_id": op_id,
            "operation": {"kind": kind, "order": 1},
            "receiver": self.receiver_identity(),
            "managed_root": {
                "path": str(self.local),
                "git_dir": str((self.local / ".git").resolve()),
                "expected_head": head,
            },
            "target": target,
            "pre_state": {
                "census": census,
                "census_snapshot_sha256": census["snapshot_sha256"],
                "dependency_inventory": inventory,
                "dependency_inventory_sha256": curator.hash_json(inventory),
                "unrelated_dirty": dirty,
                "unrelated_dirty_sha256": curator.hash_json(dirty),
                "halt": self.halt_state(),
            },
            "decision_evidence": {
                "routing": self.decision("routing"),
                "portfolio": self.decision("portfolio"),
                "policy": self.decision("policy"),
            },
            "expected_result": expected,
        }
        return {**payload, "request_sha256": curator.hash_json(payload)}

    def invoke(
        self,
        request: dict[str, Any] | bytes,
        *,
        environment: dict[str, str] | None = None,
        arguments: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        raw = request if isinstance(request, bytes) else json.dumps(request).encode()
        return subprocess.run(
            arguments or self.receiver_arguments(),
            input=raw,
            capture_output=True,
            text=False,
            check=False,
            timeout=60,
            env=environment or self.environment(),
        )

    def assert_unchanged(self, before_tree: str, before_state: dict[str, bytes]) -> None:
        if self.git("write-tree") != before_tree:
            raise AssertionError("personal Git tree changed")
        for relative, content in before_state.items():
            path = self.root / relative
            if content is None:
                if path.exists():
                    raise AssertionError(f"unexpected state appeared: {relative}")
            elif path.read_bytes() != content:
                raise AssertionError(f"state bytes changed: {relative}")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class SshEstateCuratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.case = WORK_ROOT / self.id().rsplit(".", 1)[-1]
        self.case.mkdir(parents=True)
        self.fixture = Fixture(self.case)

    def tearDown(self) -> None:
        shutil.rmtree(self.case)

    def result(self, process: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
        lines = process.stdout.decode().splitlines()
        self.assertTrue(lines, process.stderr.decode())
        return json.loads(lines[-1])

    def reseal(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = {
            key: value for key, value in request.items() if key != "request_sha256"
        }
        request["request_sha256"] = curator.hash_json(payload)
        return request

    def test_archive_restore_roundtrip_retry_collision_and_byte_equality(self) -> None:
        fixture = self.fixture
        original = {
            path.relative_to(fixture.skill).as_posix(): path.read_bytes()
            for path in fixture.skill.rglob("*")
            if path.is_file()
        }
        (fixture.local / "unrelated.txt").write_text("preserve me\n", encoding="utf-8")
        archive = fixture.request("archive-roundtrip")
        first = fixture.invoke(archive)
        self.assertEqual(first.returncode, 0, first.stderr.decode())
        result = self.result(first)
        self.assertEqual(result["status"], "committed")
        self.assertNotIn(str(fixture.root), json.dumps(result))
        self.assertFalse(fixture.skill.exists())
        self.assertTrue(
            (fixture.state / "retired" / f"{fixture.skill_name}.json").is_file()
        )
        self.assertTrue(
            (fixture.state / "tombstones" / f"{fixture.skill_name}.json").is_file()
        )
        archive_head = fixture.git("rev-parse", "HEAD")
        duplicate = fixture.invoke(archive)
        self.assertEqual(duplicate.returncode, 0, duplicate.stderr.decode())
        self.assertEqual(self.result(duplicate)["result_sha256"], result["result_sha256"])
        self.assertEqual(fixture.git("rev-parse", "HEAD"), archive_head)

        collision = json.loads(json.dumps(archive))
        collision["operation"]["order"] = 2
        self.reseal(collision)
        collided = fixture.invoke(collision)
        self.assertNotEqual(collided.returncode, 0)
        self.assertEqual(self.result(collided)["error"]["code"], "op-id-collision")
        self.assertEqual(fixture.git("rev-parse", "HEAD"), archive_head)

        fixture.set_inventory(live=False)
        restore = fixture.request("restore-roundtrip", kind="restore")
        restored = fixture.invoke(restore)
        self.assertEqual(restored.returncode, 0, restored.stderr.decode())
        restore_result = self.result(restored)
        self.assertEqual(restore_result["status"], "committed")
        self.assertNotIn(str(fixture.root), json.dumps(restore_result))
        recovered = {
            path.relative_to(fixture.skill).as_posix(): path.read_bytes()
            for path in fixture.skill.rglob("*")
            if path.is_file()
        }
        self.assertEqual(recovered, original)
        self.assertEqual(
            (fixture.local / "unrelated.txt").read_text(encoding="utf-8"),
            "preserve me\n",
        )
        self.assertFalse(
            (fixture.state / "retired" / f"{fixture.skill_name}.json").exists()
        )
        self.assertFalse(
            (fixture.state / "tombstones" / f"{fixture.skill_name}.json").exists()
        )
        histories = list(
            (fixture.state / "retirement-history").glob(
                f"{fixture.skill_name}-*.json"
            )
        )
        self.assertEqual(len(histories), 1)
        history = load_json(histories[0])
        self.assertEqual(
            history["restore_authorization"]["request_sha256"],
            restore["request_sha256"],
        )

    def test_disconnect_after_commit_reconciles_without_replay(self) -> None:
        fixture = self.fixture
        fake_ssh = fixture.root / "ssh"
        calls = fixture.root / "ssh-calls"
        fake_ssh.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env python3
                import pathlib
                import subprocess
                import sys

                calls = pathlib.Path({str(calls)!r})
                count = int(calls.read_text() or "0") if calls.exists() else 0
                calls.write_text(str(count + 1))
                process = subprocess.run(
                    ["/bin/sh", "-c", sys.argv[-1]],
                    input=sys.stdin.buffer.read(),
                    capture_output=True,
                )
                if count == 0:
                    raise SystemExit(255)
                sys.stdout.buffer.write(process.stdout)
                sys.stderr.buffer.write(process.stderr)
                raise SystemExit(process.returncode)
                """
            ),
            encoding="utf-8",
        )
        os.chmod(fake_ssh, 0o755)
        request = fixture.request("disconnect-commit")
        request_file = fixture.root / "request.json"
        request_file.write_text(json.dumps(request), encoding="utf-8")
        receiver = fixture.receiver_arguments()
        values: dict[str, str] = {}
        for index, value in enumerate(receiver):
            if value.startswith("--") and index + 1 < len(receiver):
                values[value] = receiver[index + 1]
        local = [
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
        ]
        for flag in (
            "--curator-runner",
            "--archive-tool",
            "--restore-tool",
            "--estate-script",
            "--dependency-scanner",
            "--public-root",
            "--personal-root",
            "--review-state-dir",
            "--runs-dir",
            "--curator-state-file",
            "--halt-switch",
            "--lock-dir",
            "--operation-root",
            "--recovery-state",
            "--receiver-id-file",
            "--target-home",
            "--copilot-binary",
            "--user-context-cwd",
        ):
            local.extend([f"--remote-{flag[2:]}", values[flag]])
        for flag in (
            "--expected-receiver-id",
            "--expected-receiver-sha",
            "--expected-curator-sha",
            "--expected-archive-sha",
            "--expected-restore-sha",
            "--expected-estate-sha",
            "--expected-dependency-scanner-sha",
        ):
            local.extend([flag, values[flag]])
        local.extend(
            [
                "--request",
                str(request_file),
                "--local-recovery-state",
                str(fixture.local_recovery),
            ]
        )
        completed = run(local, env=fixture.environment())
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "committed")
        self.assertEqual(calls.read_text(), "2")
        self.assertEqual(
            int(fixture.git("rev-list", "--count", "HEAD")), 2
        )
        self.assertFalse(fixture.local_recovery.exists())
        collision = json.loads(json.dumps(request))
        collision["operation"]["order"] = 2
        payload = {
            key: value
            for key, value in collision.items()
            if key != "request_sha256"
        }
        collision["request_sha256"] = curator.hash_json(payload)
        request_file.write_text(json.dumps(collision), encoding="utf-8")
        collided = run(local, env=fixture.environment())
        self.assertNotEqual(collided.returncode, 0)
        self.assertEqual(
            json.loads(collided.stdout)["error"]["code"], "op-id-collision"
        )
        self.assertFalse(fixture.local_recovery.exists())

        forged_ssh = fixture.root / "forged-ssh"
        forged = {
            "ok": True,
            "status": "committed",
            "op_id": "different-operation",
            "request_sha256": "0" * 64,
            "receiver": fixture.receiver_identity(),
        }
        forged_ssh.write_text(
            "#!/usr/bin/env python3\n"
            f"print({json.dumps(json.dumps(forged))})\n",
            encoding="utf-8",
        )
        os.chmod(forged_ssh, 0o755)
        forged_local = list(local)
        forged_local[forged_local.index("--ssh-bin") + 1] = str(forged_ssh)
        request_file.write_text(json.dumps(request), encoding="utf-8")
        forged_result = run(forged_local, env=fixture.environment())
        self.assertNotEqual(forged_result.returncode, 0)
        self.assertEqual(
            json.loads(forged_result.stdout)["error"]["code"],
            "remote-outcome-ambiguous",
        )
        self.assertTrue(fixture.local_recovery.exists())
        fixture.local_recovery.unlink()

        unauthenticated_ssh = fixture.root / "unauthenticated-ssh"
        unauthenticated_ssh.write_text(
            "#!/usr/bin/env python3\n"
            'print(\'{"ok":true,"error":{"code":"forged"}}\')\n',
            encoding="utf-8",
        )
        os.chmod(unauthenticated_ssh, 0o755)
        unauthenticated_local = list(local)
        unauthenticated_local[
            unauthenticated_local.index("--ssh-bin") + 1
        ] = str(unauthenticated_ssh)
        unauthenticated = run(
            unauthenticated_local, env=fixture.environment()
        )
        self.assertNotEqual(unauthenticated.returncode, 0)
        self.assertEqual(
            json.loads(unauthenticated.stdout),
            {"ok": False, "error": {"code": "forged"}},
        )
        self.assertFalse(fixture.local_recovery.exists())

    def test_fail_closed_authorization_and_state_matrix(self) -> None:
        cases: list[tuple[str, Any]] = []

        def incomplete(fixture: Fixture, request: dict[str, Any]) -> None:
            request["pre_state"]["census"]["scope"]["complete"] = False
            snapshot = {
                key: value
                for key, value in request["pre_state"]["census"].items()
                if key != "snapshot_sha256"
            }
            request["pre_state"]["census"]["snapshot_sha256"] = (
                f"sha256:{curator.hash_json(snapshot)}"
            )
            request["pre_state"]["census_snapshot_sha256"] = request["pre_state"][
                "census"
            ]["snapshot_sha256"]

        def protected(_: Fixture, request: dict[str, Any]) -> None:
            request["target"]["authority_class"] = "user_protected"

        def unknown(_: Fixture, request: dict[str, Any]) -> None:
            request["target"]["authority_class"] = "unknown_provenance"

        def target_mismatch(_: Fixture, request: dict[str, Any]) -> None:
            request["target"]["canonical_capability_id"] = "sha256:" + "0" * 64

        def dependency_incomplete(fixture: Fixture, request: dict[str, Any]) -> None:
            fixture.set_inventory(live=True, complete=False)
            inventory = load_json(fixture.inventory_path)
            request["pre_state"]["dependency_inventory"] = inventory
            request["pre_state"]["dependency_inventory_sha256"] = curator.hash_json(
                inventory
            )

        def pinned(fixture: Fixture, request: dict[str, Any]) -> None:
            fixture.set_inventory(live=True, pinned=True)
            inventory = load_json(fixture.inventory_path)
            request["pre_state"]["dependency_inventory"] = inventory
            request["pre_state"]["dependency_inventory_sha256"] = curator.hash_json(
                inventory
            )

        def dependency_ambiguous(
            fixture: Fixture, request: dict[str, Any]
        ) -> None:
            inventory = load_json(fixture.inventory_path)
            inventory["skills"].append(dict(inventory["skills"][0]))
            fixture.inventory_path.write_text(
                json.dumps(inventory, sort_keys=True), encoding="utf-8"
            )
            request["pre_state"]["dependency_inventory"] = inventory
            request["pre_state"]["dependency_inventory_sha256"] = curator.hash_json(
                inventory
            )

        def changed_marker(fixture: Fixture, request: dict[str, Any]) -> None:
            prior_evidence = request["target"]["verified_evidence"]
            (fixture.skill / ".agent-created").write_text(
                "changed\n", encoding="utf-8"
            )
            fixture.git("add", f"{fixture.skill_name}/.agent-created")
            fixture.git("commit", "-qm", "change marker")
            refreshed = fixture.request(request["op_id"])
            refreshed["target"]["verified_evidence"] = prior_evidence
            request.clear()
            request.update(refreshed)

        def relevant_dirty(fixture: Fixture, request: dict[str, Any]) -> None:
            (fixture.skill / "SKILL.md").write_text(
                "changed target\n", encoding="utf-8"
            )
            dirty = curator.dirty_snapshot(fixture.local)
            request["pre_state"]["unrelated_dirty"] = dirty
            request["pre_state"]["unrelated_dirty_sha256"] = curator.hash_json(dirty)

        def unrelated_drift(fixture: Fixture, request: dict[str, Any]) -> None:
            (fixture.local / "unrelated.txt").write_text("changed\n", encoding="utf-8")

        def stale_census(fixture: Fixture, _: dict[str, Any]) -> None:
            external = fixture.root / "external-skills"
            external.mkdir()
            (fixture.home / ".copilot/settings.json").write_text(
                json.dumps({"skillDirectories": [str(external)]}),
                encoding="utf-8",
            )

        def stale_head(fixture: Fixture, _: dict[str, Any]) -> None:
            (fixture.local / "advance.txt").write_text("advance\n", encoding="utf-8")
            fixture.git("add", "advance.txt")
            fixture.git("commit", "-qm", "advance")

        def halted(fixture: Fixture, _: dict[str, Any]) -> None:
            fixture.halt.touch()

        def recovery(fixture: Fixture, _: dict[str, Any]) -> None:
            fixture.recovery.write_text('{"status":"required"}\n', encoding="utf-8")

        def wrong_order(_: Fixture, request: dict[str, Any]) -> None:
            request["operation"]["order"] = 2

        def wrong_expected(_: Fixture, request: dict[str, Any]) -> None:
            request["expected_result"]["tombstone"] = "absent"

        def wrong_root(_: Fixture, request: dict[str, Any]) -> None:
            request["managed_root"]["path"] += "-other"

        cases.extend(
            [
                ("incomplete-census", incomplete),
                ("protected", protected),
                ("unknown", unknown),
                ("target-mismatch", target_mismatch),
                ("dependency-incomplete", dependency_incomplete),
                ("dependency-ambiguous", dependency_ambiguous),
                ("pinned", pinned),
                ("changed-marker", changed_marker),
                ("relevant-dirty", relevant_dirty),
                ("unrelated-drift", unrelated_drift),
                ("stale-census", stale_census),
                ("stale-head", stale_head),
                ("halted", halted),
                ("recovery", recovery),
                ("wrong-order", wrong_order),
                ("wrong-expected", wrong_expected),
                ("wrong-root", wrong_root),
            ]
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                case = self.case / label
                fixture = Fixture(case)
                request = fixture.request(f"fail-{label}")
                mutate(fixture, request)
                before_tree = fixture.git("write-tree")
                self.reseal(request)
                refused = fixture.invoke(request)
                self.assertNotEqual(refused.returncode, 0)
                self.assertEqual(fixture.git("write-tree"), before_tree)
                self.assertTrue(fixture.skill.exists())
                shutil.rmtree(case)

    def test_wrong_code_malformed_and_unsupported_requests_do_not_mutate(self) -> None:
        fixture = self.fixture
        before = fixture.git("write-tree")
        wrong = fixture.request("wrong-code")
        wrong["receiver"]["curator_sha256"] = "0" * 64
        self.reseal(wrong)
        refused = fixture.invoke(wrong)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(fixture.git("write-tree"), before)

        wrong_receiver = fixture.request("wrong-receiver")
        wrong_receiver["receiver"]["receiver_id"] = "another-host"
        self.reseal(wrong_receiver)
        refused = fixture.invoke(wrong_receiver)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(fixture.git("write-tree"), before)

        malformed = fixture.invoke(b"{not-json")
        self.assertNotEqual(malformed.returncode, 0)
        self.assertEqual(fixture.git("write-tree"), before)

        unsupported = fixture.request("unsupported")
        unsupported["operation"]["kind"] = "disable-plugin"
        self.reseal(unsupported)
        refused = fixture.invoke(unsupported)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(fixture.git("write-tree"), before)

    def test_failed_commit_and_injected_mid_transaction_failure_roll_back_exactly(self) -> None:
        for label, environment in (
            ("injected", {"DREAMING_ESTATE_CURATOR_INJECT_FAILURE": "after-helper"}),
            ("oserror", {"DREAMING_ESTATE_CURATOR_INJECT_FAILURE": "oserror"}),
            ("commit", {"CHK03_FAIL_COMMIT": "1"}),
        ):
            with self.subTest(label=label):
                case = self.case / label
                fixture = Fixture(case)
                (fixture.local / "unrelated.txt").write_text(
                    "unrelated\n", encoding="utf-8"
                )
                request = fixture.request(f"rollback-{label}")
                before_tree = fixture.git("write-tree")
                before_unrelated = (fixture.local / "unrelated.txt").read_bytes()
                env = fixture.environment(**environment)
                if label == "commit":
                    real_git = shutil.which("git")
                    assert real_git
                    fake_bin = fixture.root / "fake-bin"
                    fake_bin.mkdir()
                    fake_git = fake_bin / "git"
                    fake_git.write_text(
                        textwrap.dedent(
                            f"""\
                            #!/usr/bin/env bash
                            if [[ "${{CHK03_FAIL_COMMIT:-}}" == "1" ]]; then
                              for arg in "$@"; do
                                [[ "$arg" == "commit" ]] && exit 9
                              done
                            fi
                            exec {real_git!r} "$@"
                            """
                        ),
                        encoding="utf-8",
                    )
                    os.chmod(fake_git, 0o755)
                    env["PATH"] = f"{fake_bin}:{env['PATH']}"
                failed = fixture.invoke(request, environment=env)
                self.assertNotEqual(failed.returncode, 0)
                result = self.result(failed)
                self.assertEqual(result["status"], "rolled_back")
                self.assertEqual(fixture.git("write-tree"), before_tree)
                self.assertEqual(
                    (fixture.local / "unrelated.txt").read_bytes(),
                    before_unrelated,
                )
                self.assertTrue(fixture.skill.is_dir())
                self.assertFalse(
                    (fixture.state / "retired" / f"{fixture.skill_name}.json").exists()
                )
                self.assertFalse(
                    (fixture.state / "tombstones" / f"{fixture.skill_name}.json").exists()
                )
                duplicate = fixture.invoke(request)
                self.assertNotEqual(duplicate.returncode, 0)
                self.assertEqual(
                    self.result(duplicate)["result_sha256"],
                    result["result_sha256"],
                )
                shutil.rmtree(case)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(WORK_ROOT, ignore_errors=True)
