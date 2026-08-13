#!/usr/bin/env python3
"""Execute one sealed personal-skill estate transaction over SSH."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

PROTOCOL = "dreaming.estate-curator"
SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 4 * 1024 * 1024
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
AUTHENTICATED_IDENTITY: dict[str, str] | None = None
AUTHENTICATED_REQUEST: dict[str, str] | None = None


class CuratorError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def hash_json(value: Any) -> str:
    return hash_bytes(canonical_bytes(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def emit(value: dict[str, Any], status: int = 0) -> None:
    print(json.dumps(value, sort_keys=True))
    raise SystemExit(status)


def atomic_json(path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}"
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def immutable_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, indent=2, sort_keys=True).encode() + b"\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    except FileExistsError:
        if path.read_bytes() != data:
            raise CuratorError("receiver-record-collision")
        return
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CuratorError(code) from error
    if not isinstance(value, dict):
        raise CuratorError(code)
    return value


def receiver_identity(args: argparse.Namespace) -> dict[str, str]:
    identity_path = Path(args.receiver_id_file).expanduser().resolve()
    try:
        receiver_id = identity_path.read_text(encoding="ascii").strip()
    except OSError as error:
        raise CuratorError("receiver-identity-missing") from error
    paths = {
        "receiver_sha256": Path(__file__).resolve(),
        "curator_sha256": Path(args.curator_runner).expanduser().resolve(),
        "archive_sha256": Path(args.archive_tool).expanduser().resolve(),
        "restore_sha256": Path(args.restore_tool).expanduser().resolve(),
        "estate_sha256": Path(args.estate_script).expanduser().resolve(),
        "dependency_scanner_sha256": Path(
            args.dependency_scanner
        ).expanduser().resolve(),
    }
    expected = {
        "receiver_sha256": args.expected_receiver_sha,
        "curator_sha256": args.expected_curator_sha,
        "archive_sha256": args.expected_archive_sha,
        "restore_sha256": args.expected_restore_sha,
        "estate_sha256": args.expected_estate_sha,
        "dependency_scanner_sha256": args.expected_dependency_scanner_sha,
    }
    if receiver_id != args.expected_receiver_id:
        raise CuratorError("receiver-identity-mismatch")
    actual = {}
    for field, path in paths.items():
        if not path.is_file() or path.is_symlink():
            raise CuratorError("receiver-code-unavailable")
        actual[field] = sha256_file(path)
        if actual[field] != expected[field]:
            raise CuratorError("receiver-code-mismatch")
    return {"receiver_id": receiver_id, **actual}


def parse_request(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_REQUEST_BYTES:
        raise CuratorError("request-size-invalid")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CuratorError("request-malformed") from error
    if not isinstance(value, dict):
        raise CuratorError("request-malformed")
    op_id = value.get("op_id")
    payload = {key: item for key, item in value.items() if key != "request_sha256"}
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("protocol") != PROTOCOL
        or not isinstance(op_id, str)
        or not SAFE_ID.fullmatch(op_id)
        or value.get("request_sha256") != hash_json(payload)
    ):
        raise CuratorError("request-seal-invalid")
    return value


def index_value(value: dict[str, Any]) -> dict[str, Any]:
    payload = {key: item for key, item in value.items() if key != "index_sha256"}
    return {**payload, "index_sha256": hash_json(payload)}


def load_index(path: Path) -> dict[str, Any]:
    value = load_json(path, "receiver-state-invalid")
    seal = value.get("index_sha256")
    payload = {key: item for key, item in value.items() if key != "index_sha256"}
    if not isinstance(seal, str) or seal != hash_json(payload):
        raise CuratorError("receiver-state-invalid")
    return value


def result_receipt(
    operation_root: Path,
    index: dict[str, Any],
) -> dict[str, Any]:
    receipt_sha = index.get("result_sha256")
    if not isinstance(receipt_sha, str):
        raise CuratorError("receiver-state-invalid")
    receipt = load_json(
        operation_root / "results" / f"{receipt_sha}.json",
        "receiver-state-invalid",
    )
    payload = {key: item for key, item in receipt.items() if key != "result_sha256"}
    if receipt.get("result_sha256") != hash_json(payload):
        raise CuratorError("receiver-state-invalid")
    return receipt


def write_result(
    operation_root: Path,
    index_path: Path,
    index: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        **result,
    }
    receipt = {**payload, "result_sha256": hash_json(payload)}
    immutable_json(
        operation_root / "results" / f"{receipt['result_sha256']}.json",
        receipt,
    )
    updated = index_value(
        {
            **{
                key: value
                for key, value in index.items()
                if key != "index_sha256"
            },
            "phase": result["status"],
            "result_sha256": receipt["result_sha256"],
        }
    )
    atomic_json(index_path, updated)
    return receipt


def subprocess_json(
    command: list[str],
    environment: dict[str, str],
) -> dict[str, Any]:
    process = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    if process.returncode:
        raise CuratorError("transaction-step-refused")
    values = [line for line in process.stdout.splitlines() if line.strip()]
    if not values:
        return {}
    try:
        value = json.loads(values[-1])
    except json.JSONDecodeError as error:
        raise CuratorError("transaction-output-malformed") from error
    if not isinstance(value, dict):
        raise CuratorError("transaction-output-malformed")
    return value


def subprocess_text(
    command: list[str],
    environment: dict[str, str],
) -> str:
    process = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    if process.returncode:
        raise CuratorError("transaction-step-refused")
    return process.stdout.strip()


def transaction_environment(
    args: argparse.Namespace,
    identity: dict[str, str],
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "SKILLS_REPO_ROOT": str(Path(args.public_root).expanduser().resolve()),
            "SKILLS_LOCAL_ROOT": str(
                Path(args.personal_root).expanduser().resolve()
            ),
            "SKILLS_REVIEW_STATE_DIR": str(
                Path(args.review_state_dir).expanduser().resolve()
            ),
            "SKILLS_CURATOR_RUNS_DIR": str(
                Path(args.runs_dir).expanduser().resolve()
            ),
            "SKILLS_CURATOR_STATE_FILE": str(
                Path(args.curator_state_file).expanduser().resolve()
            ),
            "SKILLS_HALT_SWITCH": str(
                Path(args.halt_switch).expanduser().resolve()
            ),
            "SKILLS_LOCK_DIR": str(Path(args.lock_dir).expanduser().resolve()),
            "CURATOR_DEPENDENCY_SCANNER": str(
                Path(args.dependency_scanner).expanduser().resolve()
            ),
            "CURATOR_ESTATE_CLASSIFIER": str(
                Path(args.estate_script).expanduser().resolve()
            ),
            "CURATOR_RESTORE_TOOL": str(
                Path(args.restore_tool).expanduser().resolve()
            ),
            "SKILLS_CURATOR_RUNNER": str(
                Path(args.curator_runner).expanduser().resolve()
            ),
            "CURATOR_REMOTE_RECEIVER_ID": identity["receiver_id"],
            "CURATOR_REMOTE_RECEIVER_SCRIPT": str(Path(__file__).resolve()),
            "CURATOR_REMOTE_ARCHIVE_TOOL": str(
                Path(args.archive_tool).expanduser().resolve()
            ),
            "CURATOR_REMOTE_RESTORE_TOOL": str(
                Path(args.restore_tool).expanduser().resolve()
            ),
            "CURATOR_REMOTE_ESTATE_SCRIPT": str(
                Path(args.estate_script).expanduser().resolve()
            ),
            "CURATOR_REMOTE_RECOVERY_STATE": str(
                Path(args.recovery_state).expanduser().resolve()
            ),
            "CURATOR_REMOTE_TARGET_HOME": str(
                Path(args.target_home).expanduser().resolve()
            ),
            "CURATOR_REMOTE_COPILOT_BINARY": args.copilot_binary,
            "CURATOR_REMOTE_USER_CONTEXT_CWD": str(
                Path(args.user_context_cwd).expanduser().resolve()
            ),
            "DREAMING_SCRATCH_DIR": str(
                Path(args.operation_root).expanduser().resolve() / "scratch"
            ),
        }
    )
    if args.project_contexts_file:
        environment["CURATOR_REMOTE_PROJECT_CONTEXTS_FILE"] = str(
            Path(args.project_contexts_file).expanduser().resolve()
        )
    return environment


def transaction_plan(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "operations": [
            {
                "kind": request["operation"]["kind"],
                "skill": request["target"]["skill"],
            }
        ]
    }


def run_id(request: dict[str, Any]) -> str:
    return f"estate-{request['op_id']}"


def manifest_path(args: argparse.Namespace, request: dict[str, Any]) -> Path:
    return (
        Path(args.runs_dir).expanduser().resolve()
        / f"{run_id(request)}.json"
    )


def collect_current_census(
    args: argparse.Namespace,
    request: dict[str, Any],
    environment: dict[str, str],
) -> dict[str, Any]:
    target = request["target"]
    config: dict[str, Any] = {
        "host_id": request["receiver"]["receiver_id"],
        "target_home": str(Path(args.target_home).expanduser().resolve()),
        "user_context_cwd": str(
            Path(args.user_context_cwd).expanduser().resolve()
        ),
        "copilot_binary": args.copilot_binary,
        "provenance_policy": target["provenance_inputs"]["policy"],
        "legacy_proofs": (
            {target["skill"]: target["provenance_inputs"]["proof"]}
            if target["provenance_inputs"]["proof"] is not None
            else {}
        ),
    }
    if args.project_contexts_file:
        try:
            contexts = json.loads(
                Path(args.project_contexts_file)
                .expanduser()
                .resolve()
                .read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as error:
            raise CuratorError("post-census-configuration-invalid") from error
        if not isinstance(contexts, list):
            raise CuratorError("post-census-configuration-invalid")
        config["project_contexts"] = contexts
    operation_root = Path(args.operation_root).expanduser().resolve()
    config_path = (
        operation_root
        / "census-configs"
        / f"{request['request_sha256']}.json"
    )
    immutable_json(config_path, config)
    result = subprocess_json(
        [
            sys.executable,
            str(Path(args.estate_script).expanduser().resolve()),
            "collect",
            "--config",
            str(config_path),
        ],
        environment,
    )
    census = result.get("census")
    if result.get("ok") is not True or not isinstance(census, dict):
        raise CuratorError("post-census-invalid")
    return census


def build_committed_result(
    args: argparse.Namespace,
    request: dict[str, Any],
    environment: dict[str, str],
) -> dict[str, Any]:
    manifest = load_json(manifest_path(args, request), "transaction-state-invalid")
    operations = manifest.get("operations")
    if (
        manifest.get("status") != "complete"
        or not isinstance(operations, list)
        or len(operations) != 1
        or operations[0].get("status") != "complete"
    ):
        raise CuratorError("transaction-state-invalid")
    operation = operations[0]
    post_census = collect_current_census(args, request, environment)
    if post_census.get("scope", {}).get("complete") is not True:
        raise CuratorError("post-census-incomplete")
    target_instances = [
        row
        for row in post_census.get("physical_instances", [])
        if isinstance(row, dict)
        and row.get("instance_id") == request["target"]["instance_id"]
    ]
    evidence: dict[str, Any]
    if request["operation"]["kind"] == "archive":
        if target_instances:
            raise CuratorError("archive-post-census-mismatch")
        effects = operation["effects_after"]
        if (
            effects["retirement"].get("exists") is not True
            or effects["tombstone"].get("exists") is not True
        ):
            raise CuratorError("transaction-evidence-invalid")
        evidence = {
            "live_tree": "absent",
            "retirement_record_sha256": effects["retirement"]["sha256"],
            "tombstone_sha256": effects["tombstone"]["sha256"],
            "restore_source_head": operation["before_head"],
            "archive_request_sha256": request["request_sha256"],
            "post_census_snapshot_sha256": post_census["snapshot_sha256"],
        }
    else:
        if (
            len(target_instances) != 1
            or target_instances[0].get("canonical_capability_id")
            != request["target"]["canonical_capability_id"]
            or target_instances[0].get("authority")
            != request["target"]["authority_class"]
        ):
            raise CuratorError("restore-post-census-mismatch")
        before_history = {
            key for key in operation["effects_before"] if key.startswith("history:")
        }
        after_history = {
            key for key in operation["effects_after"] if key.startswith("history:")
        }
        added = after_history - before_history
        if len(added) != 1:
            raise CuratorError("transaction-evidence-invalid")
        history = operation["effects_after"][next(iter(added))]
        evidence = {
            "live_tree_sha256": request["expected_result"]["live_tree_sha256"],
            "retirement_record": "absent",
            "tombstone": "absent",
            "retirement_history_sha256": history["sha256"],
            "restore_source_head": request["expected_result"][
                "restore_source_head"
            ],
            "post_census_snapshot_sha256": post_census["snapshot_sha256"],
        }
    return {
        "ok": True,
        "op_id": request["op_id"],
        "status": "committed",
        "request_sha256": request["request_sha256"],
        "operation": request["operation"],
        "target": {
            key: request["target"][key]
            for key in (
                "skill",
                "instance_id",
                "canonical_capability_id",
                "inventory_sha256",
                "authority_class",
            )
        },
        "git": {
            "before_head": operation["before_head"],
            "after_head": operation["commit"],
        },
        "evidence": evidence,
    }


def rollback_result(
    args: argparse.Namespace,
    request: dict[str, Any],
    environment: dict[str, str],
) -> dict[str, Any]:
    runner = str(Path(args.curator_runner).expanduser().resolve())
    subprocess_text(
        [runner, "rollback", "--run", run_id(request)],
        environment,
    )
    manifest = load_json(manifest_path(args, request), "transaction-state-invalid")
    if manifest.get("status") != "rolled_back":
        raise CuratorError("transaction-rollback-failed")
    operation = manifest["operations"][0]
    return {
        "ok": False,
        "op_id": request["op_id"],
        "status": "rolled_back",
        "request_sha256": request["request_sha256"],
        "operation": request["operation"],
        "target": {
            "skill": request["target"]["skill"],
            "instance_id": request["target"]["instance_id"],
            "canonical_capability_id": request["target"][
                "canonical_capability_id"
            ],
        },
        "git": {
            "before_head": operation.get("before_head")
            or manifest["roots"]["local"]["initial_head"],
            "after_head": subprocess_text(
                [
                    "git",
                    "-C",
                    environment["SKILLS_LOCAL_ROOT"],
                    "rev-parse",
                    "HEAD",
                ],
                environment,
            ),
        },
        "error": {"code": "transaction-rolled-back"},
    }


def reconcile_running(
    args: argparse.Namespace,
    request: dict[str, Any],
    environment: dict[str, str],
) -> dict[str, Any]:
    manifest_file = manifest_path(args, request)
    if not manifest_file.exists():
        return {
            "ok": False,
            "op_id": request["op_id"],
            "status": "rolled_back",
            "request_sha256": request["request_sha256"],
            "operation": request["operation"],
            "target": {
                "skill": request["target"]["skill"],
                "instance_id": request["target"]["instance_id"],
                "canonical_capability_id": request["target"][
                    "canonical_capability_id"
                ],
            },
            "git": {
                "before_head": request["managed_root"]["expected_head"],
                "after_head": request["managed_root"]["expected_head"],
            },
            "error": {"code": "transaction-not-started"},
        }
    manifest = load_json(manifest_file, "transaction-state-invalid")
    if manifest.get("status") == "complete":
        return build_committed_result(args, request, environment)
    operations = manifest.get("operations", [])
    if (
        manifest.get("status") == "active"
        and len(operations) == 1
        and operations[0].get("status") == "complete"
    ):
        subprocess_text(
            [
                str(Path(args.curator_runner).expanduser().resolve()),
                "finish",
                "--run",
                run_id(request),
            ],
            environment,
        )
        return build_committed_result(args, request, environment)
    if manifest.get("status") == "rolled_back":
        operation = operations[0] if operations else {}
        return {
            "ok": False,
            "op_id": request["op_id"],
            "status": "rolled_back",
            "request_sha256": request["request_sha256"],
            "operation": request["operation"],
            "target": {
                "skill": request["target"]["skill"],
                "instance_id": request["target"]["instance_id"],
                "canonical_capability_id": request["target"][
                    "canonical_capability_id"
                ],
            },
            "git": {
                "before_head": operation.get("before_head")
                or manifest["roots"]["local"]["initial_head"],
                "after_head": operation.get("rollback_commit")
                or manifest["roots"]["local"]["initial_head"],
            },
            "error": {"code": "transaction-rolled-back"},
        }
    return rollback_result(args, request, environment)


def execute_transaction(
    args: argparse.Namespace,
    request_path: Path,
    request: dict[str, Any],
    environment: dict[str, str],
) -> dict[str, Any]:
    operation_root = Path(args.operation_root).expanduser().resolve()
    plan_path = (
        operation_root
        / "plans"
        / f"{request['request_sha256']}.json"
    )
    immutable_json(plan_path, transaction_plan(request))
    runner = str(Path(args.curator_runner).expanduser().resolve())
    subprocess_text(
        [
            runner,
            "begin",
            "--plan",
            str(plan_path),
            "--run-id",
            run_id(request),
            "--remote-request",
            str(request_path),
        ],
        environment,
    )
    skill = request["target"]["skill"]
    if request["operation"]["kind"] == "archive":
        archive_environment = {
            **environment,
            "SKILLS_CURATOR_RUN_ID": run_id(request),
        }
        subprocess_text(
            [str(Path(args.archive_tool).expanduser().resolve()), skill],
            archive_environment,
        )
    else:
        context = subprocess_text(
            [runner, "restore-context", "--run", run_id(request), "--skill", skill],
            environment,
        )
        op_id = subprocess_text(
            [
                runner,
                "intent",
                "--run",
                run_id(request),
                "--kind",
                "restore",
                "--root",
                "local",
                "--skill",
                skill,
            ],
            environment,
        )
        restore_environment = {
            **environment,
            "SKILLS_CURATOR_RUN_ID": run_id(request),
            "SKILLS_RESTORE_CONTEXT": context,
        }
        subprocess_text(
            [str(Path(args.restore_tool).expanduser().resolve()), skill],
            restore_environment,
        )
        subprocess_text(
            [runner, "complete", "--run", run_id(request), "--op", op_id],
            environment,
        )
    if os.environ.get("DREAMING_ESTATE_CURATOR_INJECT_FAILURE") == "after-helper":
        raise CuratorError("injected-transaction-failure")
    if os.environ.get("DREAMING_ESTATE_CURATOR_INJECT_FAILURE") == "oserror":
        raise OSError("injected receiver failure")
    subprocess_text(
        [runner, "finish", "--run", run_id(request)],
        environment,
    )
    return build_committed_result(args, request, environment)


def receive(args: argparse.Namespace) -> None:
    global AUTHENTICATED_IDENTITY, AUTHENTICATED_REQUEST
    identity = receiver_identity(args)
    AUTHENTICATED_IDENTITY = identity
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    request = parse_request(raw)
    AUTHENTICATED_REQUEST = {
        "op_id": request["op_id"],
        "request_sha256": request["request_sha256"],
    }
    if request.get("receiver") != identity:
        raise CuratorError("request-receiver-mismatch")
    operation_root = Path(args.operation_root).expanduser().resolve()
    operation_root.mkdir(parents=True, exist_ok=True)
    lock_path = operation_root / "receiver.lock"
    lock_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(lock_descriptor, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        request_path = (
            operation_root / "requests" / f"{request['request_sha256']}.json"
        )
        immutable_json(request_path, request)
        index_path = operation_root / "operations" / f"{request['op_id']}.json"
        if index_path.exists():
            index = load_index(index_path)
            if index.get("request_sha256") != request["request_sha256"]:
                raise CuratorError("op-id-collision")
            if index.get("phase") in {
                "committed",
                "rolled_back",
                "rejected",
                "recovery_required",
            }:
                receipt = result_receipt(operation_root, index)
                emit({**receipt, "receiver": identity}, 0 if receipt.get("ok") else 2)
        else:
            index = index_value(
                {
                    "schema_version": SCHEMA_VERSION,
                    "op_id": request["op_id"],
                    "request_sha256": request["request_sha256"],
                    "phase": "prepared",
                }
            )
            atomic_json(index_path, index)
        recovery = Path(args.recovery_state).expanduser().resolve()
        if recovery.exists():
            result = {
                "ok": False,
                "op_id": request["op_id"],
                "status": "recovery_required",
                "request_sha256": request["request_sha256"],
                "operation": request["operation"],
                "target": {
                    "skill": request["target"]["skill"],
                    "instance_id": request["target"]["instance_id"],
                    "canonical_capability_id": request["target"][
                        "canonical_capability_id"
                    ],
                },
                "error": {"code": "estate-recovery-required"},
            }
            receipt = write_result(operation_root, index_path, index, result)
            emit({**receipt, "receiver": identity}, 2)
        environment = transaction_environment(args, identity)
        try:
            if index.get("phase") == "prepared":
                running = index_value(
                    {
                        **{
                            key: value
                            for key, value in index.items()
                            if key != "index_sha256"
                        },
                        "phase": "running",
                    }
                )
                atomic_json(index_path, running)
                index = running
                result = execute_transaction(
                    args, request_path, request, environment
                )
            else:
                result = reconcile_running(args, request, environment)
        except (
            CuratorError,
            OSError,
            subprocess.SubprocessError,
            ValueError,
            TypeError,
            KeyError,
        ) as error:
            error_code = (
                error.code
                if isinstance(error, CuratorError)
                else "transaction-step-failed"
            )
            if manifest_path(args, request).exists():
                try:
                    result = rollback_result(
                        args, request, environment
                    )
                except (
                    CuratorError,
                    OSError,
                    subprocess.SubprocessError,
                    ValueError,
                    TypeError,
                    KeyError,
                ):
                    recovery_payload = {
                        "schema_version": SCHEMA_VERSION,
                        "status": "estate_recovery_required",
                        "op_id": request["op_id"],
                        "request_sha256": request["request_sha256"],
                        "error": {"code": "transaction-recovery-required"},
                    }
                    atomic_json(recovery, recovery_payload)
                    result = {
                        "ok": False,
                        "op_id": request["op_id"],
                        "status": "recovery_required",
                        "request_sha256": request["request_sha256"],
                        "operation": request["operation"],
                        "target": {
                            "skill": request["target"]["skill"],
                            "instance_id": request["target"]["instance_id"],
                            "canonical_capability_id": request["target"][
                                "canonical_capability_id"
                            ],
                        },
                        "error": {"code": "transaction-recovery-required"},
                    }
            else:
                result = {
                    "ok": False,
                    "op_id": request["op_id"],
                    "status": "rejected",
                    "request_sha256": request["request_sha256"],
                    "operation": request["operation"],
                    "target": {
                        "skill": request["target"]["skill"],
                        "instance_id": request["target"]["instance_id"],
                        "canonical_capability_id": request["target"][
                            "canonical_capability_id"
                        ],
                    },
                    "error": {"code": error_code},
                }
        receipt = write_result(operation_root, index_path, index, result)
        emit({**receipt, "receiver": identity}, 0 if result.get("ok") else 2)


def common_paths(parser: argparse.ArgumentParser, prefix: str = "") -> None:
    flag = f"--{prefix}" if prefix else "--"
    destination = f"{prefix.replace('-', '_')}" if prefix else ""

    def add(name: str) -> None:
        parser.add_argument(f"{flag}{name}", required=True, dest=f"{destination}{name.replace('-', '_')}")

    for name in (
        "curator-runner",
        "archive-tool",
        "restore-tool",
        "estate-script",
        "dependency-scanner",
        "public-root",
        "personal-root",
        "review-state-dir",
        "runs-dir",
        "curator-state-file",
        "halt-switch",
        "lock-dir",
        "operation-root",
        "recovery-state",
        "receiver-id-file",
        "target-home",
        "copilot-binary",
        "user-context-cwd",
    ):
        add(name)


def identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-receiver-id", required=True)
    parser.add_argument("--expected-receiver-sha", required=True)
    parser.add_argument("--expected-curator-sha", required=True)
    parser.add_argument("--expected-archive-sha", required=True)
    parser.add_argument("--expected-restore-sha", required=True)
    parser.add_argument("--expected-estate-sha", required=True)
    parser.add_argument("--expected-dependency-scanner-sha", required=True)


def receiver_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receive", action="store_true")
    common_paths(parser)
    parser.add_argument("--project-contexts-file")
    identity_arguments(parser)
    return parser


def local_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-bin", default="/usr/bin/ssh")
    parser.add_argument("--host", required=True)
    parser.add_argument("--address-family", choices=("4", "6"))
    parser.add_argument("--remote-python", required=True)
    parser.add_argument("--remote-script", required=True)
    common_paths(parser, "remote-")
    parser.add_argument("--remote-project-contexts-file")
    identity_arguments(parser)
    parser.add_argument("--request", required=True)
    parser.add_argument("--local-recovery-state", required=True)
    parser.add_argument("--timeout", type=int, default=300)
    return parser


def remote_command(args: argparse.Namespace) -> list[str]:
    receiver = [
        args.remote_python,
        args.remote_script,
        "--receive",
    ]
    for name in (
        "curator_runner",
        "archive_tool",
        "restore_tool",
        "estate_script",
        "dependency_scanner",
        "public_root",
        "personal_root",
        "review_state_dir",
        "runs_dir",
        "curator_state_file",
        "halt_switch",
        "lock_dir",
        "operation_root",
        "recovery_state",
        "receiver_id_file",
        "target_home",
        "copilot_binary",
        "user_context_cwd",
    ):
        receiver.extend(
            [f"--{name.replace('_', '-')}", getattr(args, f"remote_{name}")]
        )
    if args.remote_project_contexts_file:
        receiver.extend(
            ["--project-contexts-file", args.remote_project_contexts_file]
        )
    for name in (
        "receiver_id",
        "receiver_sha",
        "curator_sha",
        "archive_sha",
        "restore_sha",
        "estate_sha",
        "dependency_scanner_sha",
    ):
        receiver.extend(
            [f"--expected-{name.replace('_', '-')}", getattr(args, f"expected_{name}")]
        )
    return [
        args.ssh_bin,
        *([f"-{args.address_family}"] if args.address_family else []),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "--",
        args.host,
        shlex.join(receiver),
    ]


def parse_remote(process: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
    try:
        values = [
            json.loads(line)
            for line in process.stdout.decode("utf-8").splitlines()
            if line.strip()
        ]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CuratorError("remote-output-malformed") from error
    if len(values) != 1 or not isinstance(values[0], dict):
        raise CuratorError("remote-outcome-ambiguous")
    return values[0]


def local(args: argparse.Namespace) -> None:
    if args.host.startswith("-"):
        raise CuratorError("receiver-host-invalid")
    request_path = Path(args.request).expanduser().resolve()
    try:
        raw = request_path.read_bytes()
    except OSError as error:
        raise CuratorError("request-unreadable") from error
    request = parse_request(raw)
    last_error = "remote-outcome-ambiguous"
    for _ in range(2):
        try:
            process = subprocess.run(
                remote_command(args),
                input=raw,
                capture_output=True,
                check=False,
                timeout=args.timeout,
            )
            result = parse_remote(process)
            receiver = result.get("receiver")
            if not isinstance(receiver, dict):
                remote_error = result.get("error", {})
                code = (
                    remote_error.get("code")
                    if isinstance(remote_error, dict)
                    else None
                )
                if isinstance(code, str) and code:
                    emit({"ok": False, "error": {"code": code}}, 2)
                raise CuratorError("remote-receiver-invalid")
            if (
                receiver.get("receiver_id") != args.expected_receiver_id
                or receiver.get("receiver_sha256") != args.expected_receiver_sha
                or receiver.get("curator_sha256") != args.expected_curator_sha
                or receiver.get("archive_sha256") != args.expected_archive_sha
                or receiver.get("restore_sha256") != args.expected_restore_sha
                or receiver.get("estate_sha256") != args.expected_estate_sha
                or receiver.get("dependency_scanner_sha256")
                != args.expected_dependency_scanner_sha
            ):
                raise CuratorError("remote-receiver-invalid")
            if (
                result.get("op_id") != request["op_id"]
                or result.get("request_sha256") != request["request_sha256"]
            ):
                raise CuratorError("remote-outcome-ambiguous")
        except (OSError, subprocess.SubprocessError, CuratorError) as error:
            last_error = (
                error.code if isinstance(error, CuratorError)
                else "remote-outcome-ambiguous"
            )
            continue
        local_recovery = Path(args.local_recovery_state).expanduser().resolve()
        if result.get("status") in {"committed", "rolled_back", "rejected"}:
            if local_recovery.exists():
                existing = load_json(local_recovery, "local-recovery-state-invalid")
                if existing.get("op_id") == request["op_id"]:
                    local_recovery.unlink()
        emit(result, 0 if result.get("ok") is True else 2)
    recovery = {
        "schema_version": SCHEMA_VERSION,
        "status": "estate_recovery_required",
        "op_id": request["op_id"],
        "request_sha256": request["request_sha256"],
        "error": {"code": last_error},
    }
    atomic_json(Path(args.local_recovery_state).expanduser().resolve(), recovery)
    emit({"ok": False, **recovery}, 2)


def main() -> None:
    try:
        if "--receive" in sys.argv[1:]:
            receive(receiver_parser().parse_args())
        local(local_parser().parse_args())
    except CuratorError as error:
        value: dict[str, Any] = {
            "ok": False,
            "error": {"code": error.code},
        }
        if AUTHENTICATED_IDENTITY is not None:
            value["receiver"] = AUTHENTICATED_IDENTITY
        if AUTHENTICATED_REQUEST is not None:
            value.update(AUTHENTICATED_REQUEST)
        emit(value, 2)
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        TypeError,
        KeyError,
    ):
        value = {
            "ok": False,
            "error": {"code": "estate-curator-failed"},
        }
        if AUTHENTICATED_IDENTITY is not None:
            value["receiver"] = AUTHENTICATED_IDENTITY
        if AUTHENTICATED_REQUEST is not None:
            value.update(AUTHENTICATED_REQUEST)
        emit(value, 2)


if __name__ == "__main__":
    main()
