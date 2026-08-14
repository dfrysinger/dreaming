#!/usr/bin/env python3
"""Deterministic tests for plugin runtime inventory and SSH execution."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROXY = ROOT / "ssh-plugin-settings.py"
INVENTORY = ROOT / "plugin-runtime-inventory.py"
QUALIFIER = ROOT / "qualify-plugin-settings.py"
TRANSACTION = ROOT / "plugin-settings-transaction.py"
CONFIGURATOR = ROOT / "configure-plugin-estate-executors.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_digest(value: dict) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":")
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def run(command: list[str], *, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        input=stdin,
        capture_output=True,
        check=False,
        timeout=15,
    )


def output(process: subprocess.CompletedProcess) -> dict:
    lines = process.stdout.decode().splitlines()
    assert len(lines) == 1, process.stdout
    value = json.loads(lines[0])
    assert isinstance(value, dict)
    return value


def write(path: Path, value: str, mode: int = 0o600) -> None:
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)


def receive_command(
    root: Path,
    transaction: Path,
    estate: Path,
    *,
    receiver_sha: str | None = None,
) -> list[str]:
    return [
        sys.executable,
        str(PROXY),
        "--receive",
        "--action",
        "disable",
        "--transaction-python",
        sys.executable,
        "--transaction-script",
        str(transaction),
        "--runtime-python",
        sys.executable,
        "--runtime-verifier",
        str(INVENTORY),
        "--estate-script",
        str(estate),
        "--settings",
        str(root / "settings.json"),
        "--transaction-root",
        str(root / "transactions"),
        "--qualification-root",
        str(root / "qualifications"),
        "--lock",
        str(root / "settings.lock"),
        "--recovery-state",
        str(root / "recovery.json"),
        "--receiver-id-file",
        str(root / "receiver-id"),
        "--target-host-id",
        "target-host",
        "--target-home",
        str(root),
        "--copilot-binary",
        "/usr/bin/false",
        "--expected-receiver-id",
        "receiver-one",
        "--expected-receiver-sha",
        receiver_sha or digest(PROXY),
        "--expected-transaction-sha",
        digest(transaction),
        "--expected-runtime-verifier-sha",
        digest(INVENTORY),
        "--expected-estate-sha",
        digest(estate),
    ]


def test_inventory(root: Path, estate: Path) -> None:
    settings = root / "settings.json"
    settings.write_text('{"enabledPlugins":{"plugin@direct":true}}\n')
    command = [
        sys.executable,
        str(INVENTORY),
        "--estate-script",
        str(estate),
        "--expected-settings",
        str(settings),
        "--target-host-id",
        "target-host",
        "--target-home",
        str(root),
        "--copilot-binary",
        "/usr/bin/false",
        "--settings",
        str(settings),
        "--plugin-id",
        "plugin@direct",
    ]
    process = run(command)
    assert process.returncode == 0, process.stderr
    value = output(process)
    assert value == {
        "schema_version": 1,
        "copilot_version": "1.2.3",
        "plugin_identity": {
            "plugin_id": "plugin@direct",
            "source_identity": "source",
            "version": "v1",
        },
        "plugin_enabled": True,
        "owned_capability_ids": ["plugin:a", "plugin:b"],
        "estate_capability_ids": ["personal:x", "plugin:a", "plugin:b"],
    }
    settings.write_text('{"enabledPlugins":{"plugin@direct":false}}\n')
    value = output(run(command))
    assert value["plugin_enabled"] is False
    assert value["owned_capability_ids"] == []
    assert value["estate_capability_ids"] == ["personal:x"]
    bad = run([*command[:-4], "--settings", str(root / "other.json"), "--plugin-id", "plugin@direct"])
    assert bad.returncode == 2
    assert output(bad)["error"]["code"] == "settings identity mismatch"


def test_receiver(root: Path, estate: Path, transaction: Path) -> None:
    request = json.dumps(
        {"action": "disable", "marker": "descriptor-only"}
    ).encode()
    process = run(receive_command(root, transaction, estate), stdin=request)
    assert process.returncode == 0, process.stderr
    value = output(process)
    assert value["ok"] is True
    assert value["result"]["request"]["marker"] == "descriptor-only"
    assert value["result"]["request_path"].startswith("/dev/fd/")
    assert value["receiver"]["receiver_id"] == "receiver-one"
    rejected = run(
        receive_command(root, transaction, estate, receiver_sha="0" * 64),
        stdin=request,
    )
    assert rejected.returncode == 2
    assert output(rejected)["error"]["code"] == "receiver-code-mismatch"
    failed = run(
        receive_command(root, transaction, estate),
        stdin=json.dumps(
            {"action": "disable", "force_fail": True}
        ).encode(),
    )
    assert failed.returncode == 2
    failure = output(failed)
    assert failure["ok"] is False
    assert failure["error"]["code"] == "fixture-rejected"
    assert failure["receiver"]["receiver_id"] == "receiver-one"


def test_local_ssh(root: Path, estate: Path, transaction: Path) -> None:
    request = root / "request.json"
    request.write_text('{"action":"disable","marker":"over-ssh"}\n')
    ssh = root / "fake-ssh.py"
    write(
        ssh,
        """#!/usr/bin/env python3
import subprocess
import sys
process = subprocess.run(
    sys.argv[-1],
    input=sys.stdin.buffer.read(),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    shell=True,
)
sys.stdout.buffer.write(process.stdout)
sys.stderr.buffer.write(process.stderr)
raise SystemExit(process.returncode)
""",
        0o700,
    )
    receive = receive_command(root, transaction, estate)
    remote_pairs = {
        "--transaction-python": "--remote-transaction-python",
        "--transaction-script": "--remote-transaction-script",
        "--runtime-python": "--remote-runtime-python",
        "--runtime-verifier": "--remote-runtime-verifier",
        "--estate-script": "--remote-estate-script",
        "--settings": "--remote-settings",
        "--transaction-root": "--remote-transaction-root",
        "--qualification-root": "--remote-qualification-root",
        "--lock": "--remote-lock",
        "--recovery-state": "--remote-recovery-state",
        "--receiver-id-file": "--remote-receiver-id-file",
        "--copilot-binary": "--remote-copilot-binary",
    }
    values = {
        receive[index]: receive[index + 1]
        for index in range(len(receive) - 1)
        if receive[index].startswith("--")
    }
    command = [
        sys.executable,
        str(PROXY),
        "--action",
        "disable",
        "--request",
        str(request),
        "--ssh-bin",
        str(ssh),
        "--host",
        "fixture-host",
        "--remote-python",
        sys.executable,
        "--remote-script",
        str(PROXY),
        "--expected-local-sha",
        digest(PROXY),
    ]
    for receive_name, remote_name in remote_pairs.items():
        command.extend([remote_name, values[receive_name]])
    for name in (
        "--target-host-id",
        "--target-home",
        "--expected-receiver-id",
        "--expected-receiver-sha",
        "--expected-transaction-sha",
        "--expected-runtime-verifier-sha",
        "--expected-estate-sha",
    ):
        command.extend([name, values[name]])
    process = run(command)
    assert process.returncode == 0, process.stderr
    assert output(process)["result"]["request"]["marker"] == "over-ssh"


def test_qualification(root: Path) -> None:
    settings = root / "qualification-settings.json"
    before = (
        b'{\n  "enabledPlugins": {"other@market": true},\n'
        b'  "editor": {"theme": "dark"}\n}\n'
    )
    settings.write_bytes(before)
    settings.chmod(0o600)
    runtime = root / "qualification-runtime.py"
    write(
        runtime,
        """import argparse
import json
parser = argparse.ArgumentParser()
parser.add_argument("--settings", required=True)
parser.add_argument("--plugin-id", required=True)
args = parser.parse_args()
document = json.load(open(args.settings))
enabled = document.get("enabledPlugins", {}).get("fixture@market", True) is not False
owned = ["plugin:fixture"] if enabled else []
print(json.dumps({
    "schema_version": 1,
    "copilot_version": "1.2.3",
    "plugin_identity": {
        "plugin_id": args.plugin_id,
        "source_identity": "installed:market/fixture",
        "version": "v1",
    },
    "plugin_enabled": enabled,
    "owned_capability_ids": owned,
    "estate_capability_ids": ["personal:x", *owned],
}))
""",
    )
    command = [
        sys.executable,
        str(QUALIFIER),
        "--transaction-script",
        str(TRANSACTION),
        "--settings",
        str(settings),
        "--transaction-root",
        str(root / "qualification-transactions"),
        "--qualification-root",
        str(root / "qualification-records"),
        "--lock",
        str(root / "qualification.lock"),
        "--recovery-state",
        str(root / "qualification-recovery.json"),
        "--plugin-id",
        "fixture@market",
        "--source-identity",
        "installed:market/fixture",
        "--version",
        "v1",
        "--source-type",
        "marketplace",
        "--settings-key",
        "fixture@market",
        "--copilot-version",
        "1.2.3",
        "--runtime-verifier",
        sys.executable,
        str(runtime),
    ]
    result = output(run(command))
    assert result["ok"] is True
    assert result["already_qualified"] is False
    assert settings.read_bytes() == before
    record = (
        root
        / "qualification-records"
        / f"{result['qualification_sha256']}.json"
    )
    assert record.is_file()
    repeated = output(run(command))
    assert repeated["ok"] is True
    assert repeated["already_qualified"] is True
    assert settings.read_bytes() == before


def test_configuration(root: Path) -> None:
    actions = (
        "personal_archive",
        "personal_restore",
        "plugin_disable",
        "plugin_restore",
    )
    executors_payload = {
        "schema_version": 1,
        "executors": {
            action: {"argv": ["/usr/bin/false", "{request}"], "timeout": 30}
            for action in actions
        },
    }
    authority_payload = {
        "schema_version": 1,
        "evidence_root": str(root),
        "adapters": {
            action: {
                "path": str(ROOT / "estate-action-adapter.py"),
                "sha256": "sha256:" + digest(ROOT / "estate-action-adapter.py"),
                "argv": [
                    str(ROOT / "estate-action-adapter.py"),
                    "{request}",
                    "{authorization}",
                ],
            }
            for action in actions
        },
        "receivers": {
            action: {
                "executor_receiver": {} if action.startswith("personal_") else None,
                "receiver_id": "receiver-one",
                "receiver_sha256": "sha256:" + "a" * 64,
            }
            for action in actions
        },
        "state_root": str(root),
        "halt_switch": str(root / "halt"),
        "recovery_state": str(root / "recovery"),
        "curator_state": str(root / "curator"),
    }
    executors = root / "executors.json"
    authority = root / "authority.json"
    executors.write_text(
        json.dumps(
            {
                **executors_payload,
                "config_sha256": object_digest(executors_payload),
            }
        )
    )
    authority.write_text(
        json.dumps(
            {
                **authority_payload,
                "config_sha256": object_digest(authority_payload),
            }
        )
    )
    command = [
        sys.executable,
        str(CONFIGURATOR),
        "--executors-config",
        str(executors),
        "--authority-config",
        str(authority),
        "--adapter",
        str(ROOT / "estate-action-adapter.py"),
        "--python",
        sys.executable,
        "--proxy",
        str(PROXY),
        "--ssh-bin",
        "/usr/bin/ssh",
        "--host",
        "fixture-host",
        "--remote-python",
        sys.executable,
        "--remote-proxy",
        str(PROXY),
        "--remote-transaction-python",
        sys.executable,
        "--remote-transaction-script",
        str(TRANSACTION),
        "--remote-runtime-python",
        sys.executable,
        "--remote-runtime-verifier",
        str(INVENTORY),
        "--remote-estate-script",
        str(root / "estate.py"),
        "--remote-settings",
        str(root / "settings.json"),
        "--remote-transaction-root",
        str(root / "transactions"),
        "--remote-qualification-root",
        str(root / "qualifications"),
        "--remote-lock",
        str(root / "lock"),
        "--remote-recovery-state",
        str(root / "recovery"),
        "--remote-receiver-id-file",
        str(root / "receiver-id"),
        "--remote-copilot-binary",
        "/usr/bin/false",
        "--target-host-id",
        "target-host",
        "--target-home",
        str(root),
        "--expected-receiver-id",
        "receiver-one",
        "--expected-receiver-sha",
        digest(PROXY),
        "--expected-transaction-sha",
        digest(TRANSACTION),
        "--expected-runtime-verifier-sha",
        digest(INVENTORY),
        "--expected-estate-sha",
        "b" * 64,
    ]
    first = output(run(command))
    assert set(first) == {
        "authority_config_sha256",
        "executors_config_sha256",
    }
    configured = json.loads(executors.read_text())
    argv = configured["executors"]["plugin_disable"]["argv"]
    assert argv[0:2] == [sys.executable, str(PROXY)]
    assert argv[argv.index("--expected-local-sha") + 1] == digest(PROXY)
    before = (executors.read_bytes(), authority.read_bytes())
    output(run(command))
    assert (executors.read_bytes(), authority.read_bytes()) == before


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        write(root / "receiver-id", "receiver-one\n")
        (root / "settings.json").write_text("{}\n")
        estate = root / "estate.py"
        write(
            estate,
            """import json
from pathlib import Path
class EstateError(RuntimeError):
    pass
def collect(config):
    enabled = json.loads((Path(config["target_home"]) / "settings.json").read_text()).get("enabledPlugins", {}).get("plugin@direct", True)
    owned = ["plugin:a", "plugin:b"] if enabled else []
    return {
        "plugins": [{"plugin_id": "plugin@direct", "source_identity": "source", "version": "v1", "enabled": enabled}],
        "physical_instances": [
            {"root_class": "plugin", "owner": "plugin@direct", "canonical_capability_id": "plugin:a"},
            {"root_class": "plugin", "owner": "plugin@direct", "canonical_capability_id": "plugin:b"},
        ],
        "enabled_instances": [{"canonical_capability_id": item} for item in owned + ["personal:x"]],
        "evidence": {"copilot_version": "1.2.3"},
    }
""",
        )
        transaction = root / "transaction.py"
        write(
            transaction,
            """import argparse
import json
parser = argparse.ArgumentParser()
parser.add_argument("action")
parser.add_argument("--request", required=True)
args, _ = parser.parse_known_args()
request = json.loads(open(args.request).read())
if request.get("force_fail"):
    print(json.dumps({"ok": False, "error": {"code": "fixture-rejected"}}))
    raise SystemExit(2)
print(json.dumps({"ok": True, "result": {"action": args.action, "request": request, "request_path": args.request}}))
""",
        )
        test_inventory(root, estate)
        test_receiver(root, estate, transaction)
        test_local_ssh(root, estate, transaction)
        test_qualification(root)
        test_configuration(root)
    print("plugin remote executor tests: PASS")


if __name__ == "__main__":
    main()
