#!/usr/bin/env python3
"""Authorize and dispatch one complete evidence-bound estate action."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PROTOCOL = "dreaming.estate-action"
RESULT_PROTOCOL = "dreaming.estate-action-result"
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")
EVIDENCE_KEYS = {
    "census",
    "dependencies",
    "halt_state",
    "model",
    "policy",
    "portfolio",
    "proposed_estate",
    "receiver",
    "routing",
    "target",
}
ACTION_CONTRACTS = {
    "personal_archive": {
        "protocol": "dreaming.estate-curator",
        "request_action": "archive",
        "authority": "legacy_machine",
        "decision": "archive_eligible",
    },
    "personal_restore": {
        "protocol": "dreaming.estate-curator",
        "request_action": "restore",
        "authority": "legacy_machine",
        "decision": "restore_eligible",
    },
    "plugin_disable": {
        "protocol": "dreaming.plugin-settings",
        "request_action": "disable",
        "authority": "plugin_managed",
        "decision": "disable_eligible",
    },
    "plugin_restore": {
        "protocol": "dreaming.plugin-settings",
        "request_action": "restore",
        "authority": "plugin_managed",
        "decision": "restore_eligible",
    },
}


class ActionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return "sha256:" + value.hexdigest()


def load_object(path: Path, code: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise ActionError(code)
        value = json.loads(path.read_text(encoding="utf-8"))
    except ActionError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ActionError(code) from error
    if not isinstance(value, dict):
        raise ActionError(code)
    return value


def load_authority_config(path: Path) -> dict[str, Any]:
    value = load_object(path, "estate-action-config-invalid")
    if set(value) != {
        "schema_version",
        "evidence_root",
        "adapters",
        "receivers",
        "state_root",
        "halt_switch",
        "recovery_state",
        "curator_state",
        "config_sha256",
    }:
        raise ActionError("estate-action-config-invalid")
    payload = {
        key: item for key, item in value.items() if key != "config_sha256"
    }
    adapters = value.get("adapters")
    receivers = value.get("receivers")
    evidence_root = Path(str(value.get("evidence_root", "")))
    configured_paths = [
        Path(str(value.get(field, "")))
        for field in (
            "state_root",
            "halt_switch",
            "recovery_state",
            "curator_state",
        )
    ]
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("config_sha256") != digest(payload)
        or not evidence_root.is_absolute()
        or evidence_root.is_symlink()
        or not evidence_root.is_dir()
        or not isinstance(adapters, dict)
        or set(adapters) != set(ACTION_CONTRACTS)
        or not isinstance(receivers, dict)
        or set(receivers) != set(ACTION_CONTRACTS)
        or any(not path.is_absolute() for path in configured_paths)
    ):
        raise ActionError("estate-action-config-invalid")
    for kind in ACTION_CONTRACTS:
        adapter = adapters[kind]
        receiver = receivers[kind]
        if (
            not isinstance(adapter, dict)
            or set(adapter) != {"path", "sha256"}
            or not isinstance(adapter.get("path"), str)
            or not Path(adapter["path"]).is_absolute()
            or not SHA256_RE.fullmatch(str(adapter.get("sha256")))
            or not isinstance(receiver, dict)
            or set(receiver)
            != {
                "executor_receiver",
                "receiver_id",
                "receiver_sha256",
            }
            or not isinstance(receiver.get("receiver_id"), str)
            or not receiver["receiver_id"]
            or not SHA256_RE.fullmatch(
                str(receiver.get("receiver_sha256"))
            )
            or (
                kind.startswith("personal_")
                and not isinstance(receiver.get("executor_receiver"), dict)
            )
            or (
                kind.startswith("plugin_")
                and receiver.get("executor_receiver") is not None
            )
        ):
            raise ActionError("estate-action-config-invalid")
    return value


def configured_adapter(config: dict[str, Any], kind: str) -> Path:
    configured = config["adapters"][kind]
    path = Path(configured["path"])
    if (
        path.is_symlink()
        or not path.is_file()
        or file_digest(path) != configured["sha256"]
    ):
        raise ActionError("estate-action-adapter-untrusted")
    return path


def evidence_object_path(
    evidence_root: Path, label: str, sha256: str
) -> Path:
    return evidence_root / "objects" / label / f"{sha256[7:]}.json"


def load_current_evidence(
    config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    evidence_root = Path(config["evidence_root"])
    current: dict[str, dict[str, Any]] = {}
    for label in sorted(EVIDENCE_KEYS):
        pointer = load_object(
            evidence_root / "current" / f"{label}.json",
            f"estate-action-{label}-authority-invalid",
        )
        sealed = sealed_evidence(pointer, label)
        artifact = load_object(
            evidence_object_path(evidence_root, label, sealed["sha256"]),
            f"estate-action-{label}-authority-invalid",
        )
        if artifact != sealed["value"]:
            raise ActionError(
                f"estate-action-{label}-authority-invalid"
            )
        current[label] = sealed
    return current


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}"
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(
                json.dumps(value, indent=2, sort_keys=True).encode("utf-8")
                + b"\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def immutable_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400
        )
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != data:
            raise ActionError("estate-action-record-collision")
        return
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    fsync_directory(path.parent)


def sealed_evidence(value: Any, label: str) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"sha256", "value"}
        or not SHA256_RE.fullmatch(str(value.get("sha256")))
        or value["sha256"] != digest(value.get("value"))
    ):
        raise ActionError(f"estate-action-{label}-invalid")
    return value


def sealed_receipt(value: Any, label: str) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"status", "payload", "sha256"}
        or value.get("status") != "passed"
        or not isinstance(value.get("payload"), dict)
        or value.get("sha256") != digest(value["payload"])
        or value["payload"].get("result") != "passed"
    ):
        raise ActionError(f"estate-action-{label}-invalid")
    return value


def validate_evidence(
    evidence: Any, action: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    if not isinstance(evidence, dict) or set(evidence) != EVIDENCE_KEYS:
        raise ActionError("estate-action-evidence-incomplete")
    checked = {
        label: sealed_evidence(value, label)
        for label, value in evidence.items()
    }
    values = {label: item["value"] for label, item in checked.items()}
    contract = ACTION_CONTRACTS[action["kind"]]
    census = values["census"]
    scope = census.get("scope") if isinstance(census, dict) else None
    if (
        not isinstance(census, dict)
        or not isinstance(scope, dict)
        or scope.get("complete") is not True
        or census.get("snapshot_sha256")
        != digest(
            {
                key: value
                for key, value in census.items()
                if key != "snapshot_sha256"
            }
        )
    ):
        raise ActionError("estate-action-census-invalid")
    target = values["target"]
    if (
        not isinstance(target, dict)
        or target.get("identity_sha256") != action["target_identity_sha256"]
        or target.get("inventory_sha256")
        != action["target_inventory_sha256"]
        or target.get("authority") != contract["authority"]
        or target.get("decision") != contract["decision"]
    ):
        raise ActionError("estate-action-target-invalid")
    dependencies = values["dependencies"]
    if (
        not isinstance(dependencies, dict)
        or dependencies.get("complete") is not True
        or dependencies.get("target_identity_sha256")
        != action["target_identity_sha256"]
        or dependencies.get("blocking") != []
    ):
        raise ActionError("estate-action-dependencies-invalid")
    model = values["model"]
    if (
        not isinstance(model, dict)
        or not isinstance(model.get("model_id"), str)
        or not model["model_id"]
        or model.get("status") != "passed"
        or not SHA256_RE.fullmatch(str(model.get("evaluation_sha256")))
    ):
        raise ActionError("estate-action-model-invalid")
    proposed = values["proposed_estate"]
    if (
        not isinstance(proposed, dict)
        or proposed.get("complete") is not True
        or proposed.get("current_census_sha256") != checked["census"]["sha256"]
        or proposed.get("target_identity_sha256")
        != action["target_identity_sha256"]
        or proposed.get("action") != action["kind"]
        or proposed.get("proposed_estate_sha256")
        != digest(proposed.get("snapshot"))
    ):
        raise ActionError("estate-action-proposed-estate-invalid")
    for label in ("routing", "portfolio"):
        receipt = sealed_receipt(values[label], label)
        payload = receipt["payload"]
        if (
            payload.get("kind") != label
            or payload.get("action") != action["kind"]
            or payload.get("target_identity_sha256")
            != action["target_identity_sha256"]
            or payload.get("current_census_sha256")
            != checked["census"]["sha256"]
            or payload.get("proposed_estate_sha256")
            != proposed["proposed_estate_sha256"]
            or payload.get("model_sha256") != checked["model"]["sha256"]
        ):
            raise ActionError(f"estate-action-{label}-invalid")
    policy = values["policy"]
    allowed_actions = (
        policy.get("allowed_actions") if isinstance(policy, dict) else None
    )
    if (
        not isinstance(policy, dict)
        or policy.get("status") != "active"
        or not isinstance(allowed_actions, list)
        or any(
            not isinstance(item, str) or item not in ACTION_CONTRACTS
            for item in allowed_actions
        )
        or len(allowed_actions) != len(set(allowed_actions))
        or action["kind"] not in allowed_actions
        or policy.get("target_identity_sha256")
        != action["target_identity_sha256"]
    ):
        raise ActionError("estate-action-policy-invalid")
    receiver = values["receiver"]
    executor_receiver = (
        receiver.get("executor_receiver")
        if isinstance(receiver, dict)
        else None
    )
    if (
        not isinstance(receiver, dict)
        or set(receiver)
        != {
            "adapter_sha256",
            "executor_receiver",
            "receiver_id",
            "receiver_sha256",
        }
        or not isinstance(receiver.get("receiver_id"), str)
        or not receiver["receiver_id"]
        or receiver.get("adapter_sha256") != action["adapter_sha256"]
        or not SHA256_RE.fullmatch(str(receiver.get("receiver_sha256")))
        or (
            action["kind"].startswith("personal_")
            and (
                not isinstance(executor_receiver, dict)
                or executor_receiver.get("receiver_id")
                != receiver["receiver_id"]
                or receiver["receiver_sha256"]
                != "sha256:"
                + str(executor_receiver.get("receiver_sha256", ""))
            )
        )
        or (
            action["kind"].startswith("plugin_")
            and executor_receiver is not None
        )
    ):
        raise ActionError("estate-action-receiver-invalid")
    halt = values["halt_state"]
    if (
        not isinstance(halt, dict)
        or halt.get("halted") is not False
        or halt.get("paused") is not False
        or halt.get("recovery_required") is not False
        or not SHA256_RE.fullmatch(str(halt.get("curator_state_sha256")))
    ):
        raise ActionError("estate-action-halt-state-invalid")
    return checked


def validate_executor(
    executor: Any, action: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(executor, dict) or set(executor) != {
        "argv",
        "protocol",
        "request",
    }:
        raise ActionError("estate-action-executor-invalid")
    contract = ACTION_CONTRACTS[action["kind"]]
    argv = executor["argv"]
    request = executor["request"]
    if (
        executor["protocol"] != contract["protocol"]
        or not isinstance(argv, list)
        or not argv
        or any(not isinstance(item, str) or not item for item in argv)
        or argv.count("{request}") != 1
        or argv.count("{authorization}") != 1
        or not isinstance(request, dict)
        or digest(request) != action["executor_request_sha256"]
        or request.get("protocol") != contract["protocol"]
    ):
        raise ActionError("estate-action-executor-invalid")
    requested_action = (
        request.get("operation", {}).get("kind")
        if contract["protocol"] == "dreaming.estate-curator"
        else request.get("action")
    )
    if requested_action != contract["request_action"]:
        raise ActionError("estate-action-executor-invalid")
    inner_target = (
        request.get("target")
        if contract["protocol"] == "dreaming.estate-curator"
        else request.get("plugin")
    )
    if not isinstance(inner_target, dict) or digest(inner_target) != action[
        "target_identity_sha256"
    ]:
        raise ActionError("estate-action-executor-target-mismatch")
    return executor


def validate_authorization(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "protocol",
        "action_id",
        "action",
        "authority",
        "evidence",
        "executor",
        "authorization_sha256",
    }:
        raise ActionError("estate-action-authorization-invalid")
    action = value["action"]
    authority = value["authority"]
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("protocol") != PROTOCOL
        or not isinstance(value.get("action_id"), str)
        or not SAFE_ID.fullmatch(value["action_id"])
        or not isinstance(action, dict)
        or set(action)
        != {
            "adapter_sha256",
            "executor_request_sha256",
            "kind",
            "order",
            "target_identity_sha256",
            "target_inventory_sha256",
        }
        or action.get("kind") not in ACTION_CONTRACTS
        or not isinstance(action.get("order"), int)
        or isinstance(action.get("order"), bool)
        or action["order"] != 1
        or not all(
            SHA256_RE.fullmatch(str(action.get(field)))
            for field in (
                "adapter_sha256",
                "executor_request_sha256",
                "target_identity_sha256",
                "target_inventory_sha256",
            )
        )
        or not isinstance(authority, dict)
        or set(authority) != {"config_sha256", "evidence_root"}
        or not SHA256_RE.fullmatch(
            str(authority.get("config_sha256"))
        )
        or not isinstance(authority.get("evidence_root"), str)
        or not Path(authority["evidence_root"]).is_absolute()
    ):
        raise ActionError("estate-action-authorization-invalid")
    payload = {
        key: item for key, item in value.items() if key != "authorization_sha256"
    }
    if value.get("authorization_sha256") != digest(payload):
        raise ActionError("estate-action-authorization-invalid")
    evidence = validate_evidence(value["evidence"], action)
    executor = validate_executor(value["executor"], action)
    if (
        action["kind"].startswith("personal_")
        and executor["request"].get("receiver")
        != evidence["receiver"]["value"].get("executor_receiver")
    ):
        raise ActionError("estate-action-receiver-invalid")
    return value


def authorize(
    candidate: dict[str, Any], config_path: Path
) -> dict[str, Any]:
    if set(candidate) != {
        "schema_version",
        "protocol",
        "action_id",
        "action",
        "evidence",
        "executor",
    }:
        raise ActionError("estate-action-candidate-invalid")
    config = load_authority_config(config_path)
    current_evidence = load_current_evidence(config)
    if candidate["evidence"] != current_evidence:
        raise ActionError("estate-action-evidence-not-current")
    expected_adapter = configured_adapter(config, candidate["action"]["kind"])
    if (
        not candidate.get("executor", {}).get("argv")
        or candidate["executor"]["argv"][0] != str(expected_adapter)
        or candidate.get("action", {}).get("adapter_sha256")
        != file_digest(expected_adapter)
        or candidate["evidence"]["receiver"]["value"]
        != {
            **config["receivers"][candidate["action"]["kind"]],
            "adapter_sha256": file_digest(expected_adapter),
        }
    ):
        raise ActionError("estate-action-adapter-untrusted")
    authorization = {
        **candidate,
        "authority": {
            "config_sha256": config["config_sha256"],
            "evidence_root": config["evidence_root"],
        },
        "authorization_sha256": digest(candidate),
    }
    payload = {
        key: item
        for key, item in authorization.items()
        if key != "authorization_sha256"
    }
    authorization["authorization_sha256"] = digest(payload)
    return validate_authorization(authorization)


def verify_authority(
    authorization: dict[str, Any], config_path: Path
) -> dict[str, dict[str, Any]]:
    config = load_authority_config(config_path)
    if authorization["authority"] != {
        "config_sha256": config["config_sha256"],
        "evidence_root": config["evidence_root"],
    }:
        raise ActionError("estate-action-config-changed")
    adapter = configured_adapter(config, authorization["action"]["kind"])
    if (
        str(adapter) != authorization["executor"]["argv"][0]
        or file_digest(adapter) != authorization["action"]["adapter_sha256"]
        or authorization["evidence"]["receiver"]["value"]
        != {
            **config["receivers"][authorization["action"]["kind"]],
            "adapter_sha256": file_digest(adapter),
        }
    ):
        raise ActionError("estate-action-adapter-changed")
    return load_current_evidence(config)


def verify_current_evidence(
    authorization: dict[str, Any], current: dict[str, Any]
) -> None:
    checked = validate_evidence(current, authorization["action"])
    for label in sorted(EVIDENCE_KEYS):
        if (
            checked[label]["sha256"]
            != authorization["evidence"][label]["sha256"]
        ):
            raise ActionError(f"estate-action-{label}-changed")


def verify_live_boundaries(
    authorization: dict[str, Any],
    *,
    halt_switch: Path,
    recovery_state: Path,
    curator_state: Path,
    local_recovery_state: Path,
) -> None:
    if halt_switch.exists():
        raise ActionError("estate-action-halted")
    if recovery_state.exists():
        raise ActionError("estate-action-recovery-required")
    if local_recovery_state.exists():
        raise ActionError("estate-action-recovery-required")
    state = load_object(curator_state, "estate-action-curator-state-invalid")
    if state.get("paused") is not False:
        raise ActionError("estate-action-paused")
    expected = authorization["evidence"]["halt_state"]["value"]
    if file_digest(curator_state) != expected["curator_state_sha256"]:
        raise ActionError("estate-action-halt-state-changed")
    executable = Path(authorization["executor"]["argv"][0])
    if (
        not executable.is_absolute()
        or executable.is_symlink()
        or not executable.is_file()
        or file_digest(executable)
        != authorization["action"]["adapter_sha256"]
    ):
        raise ActionError("estate-action-adapter-changed")


def install_recovery_fences(
    *,
    local_recovery_state: Path,
    recovery_state: Path,
    value: dict[str, Any],
) -> None:
    errors: list[OSError] = []
    for path in (local_recovery_state, recovery_state):
        try:
            atomic_json(path, value)
        except OSError as error:
            errors.append(error)
    if len(errors) == 2:
        raise errors[0]


def unresolved_action_state(
    state_root: Path, current_action_id: str
) -> bool:
    for child in state_root.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        index_path = child / "index.json"
        if not index_path.exists():
            continue
        try:
            index = load_object(index_path, "estate-action-state-invalid")
        except ActionError:
            return True
        phase = index.get("phase")
        if phase == "running" and child.name == current_action_id:
            continue
        if phase in {"running", "recovery_required"}:
            return True
    return False


def result_payload(value: Any, authorization: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "protocol",
        "action_id",
        "authorization_sha256",
        "executor_request_sha256",
        "status",
        "ok",
        "receipt",
        "result_sha256",
    }:
        raise ActionError("estate-action-result-invalid")
    payload = {
        key: item for key, item in value.items() if key != "result_sha256"
    }
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("protocol") != RESULT_PROTOCOL
        or value.get("action_id") != authorization["action_id"]
        or value.get("authorization_sha256")
        != authorization["authorization_sha256"]
        or value.get("executor_request_sha256")
        != authorization["action"]["executor_request_sha256"]
        or value.get("status")
        not in {"committed", "rejected", "rolled_back", "recovery_required"}
        or not isinstance(value.get("ok"), bool)
        or not isinstance(value.get("receipt"), dict)
        or value.get("result_sha256") != digest(payload)
        or (value["status"] == "committed") != value["ok"]
    ):
        raise ActionError("estate-action-result-invalid")
    return value


def load_result(operation_root: Path, index: dict[str, Any]) -> dict[str, Any]:
    result_sha256 = index.get("result_sha256")
    if not SHA256_RE.fullmatch(str(result_sha256)):
        raise ActionError("estate-action-state-invalid")
    value = load_object(
        operation_root / "results" / f"{result_sha256[7:]}.json",
        "estate-action-state-invalid",
    )
    payload = {
        key: item for key, item in value.items() if key != "result_sha256"
    }
    if value.get("result_sha256") != digest(payload):
        raise ActionError("estate-action-state-invalid")
    return value


def _dispatch_locked(
    authorization: dict[str, Any],
    *,
    config_path: Path,
    state_root: Path,
    halt_switch: Path,
    recovery_state: Path,
    curator_state: Path,
    local_recovery_state: Path,
    timeout: int,
) -> dict[str, Any]:
    authorization = validate_authorization(authorization)
    current_evidence = verify_authority(authorization, config_path)
    verify_current_evidence(authorization, current_evidence)
    if unresolved_action_state(state_root, authorization["action_id"]):
        install_recovery_fences(
            local_recovery_state=local_recovery_state,
            recovery_state=recovery_state,
            value={
                "schema_version": SCHEMA_VERSION,
                "status": "estate_action_recovery_required",
                "action_id": authorization["action_id"],
                "authorization_sha256": authorization[
                    "authorization_sha256"
                ],
                "reason": "unresolved_prior_dispatch",
            },
        )
        raise ActionError("estate-action-recovery-required")
    verify_live_boundaries(
        authorization,
        halt_switch=halt_switch,
        recovery_state=recovery_state,
        curator_state=curator_state,
        local_recovery_state=local_recovery_state,
    )
    operation_root = state_root / authorization["action_id"]
    operation_root.mkdir(parents=True, exist_ok=True)
    immutable_json(
        operation_root / "authorization.json", authorization
    )
    immutable_json(
        operation_root / "request.json",
        authorization["executor"]["request"],
    )
    index_path = operation_root / "index.json"
    if index_path.exists():
        index = load_object(index_path, "estate-action-state-invalid")
        if (
            index.get("authorization_sha256")
            != authorization["authorization_sha256"]
        ):
            raise ActionError("estate-action-id-collision")
        if index.get("phase") in {
            "committed",
            "rejected",
            "rolled_back",
            "recovery_required",
        }:
            return load_result(operation_root, index)
        install_recovery_fences(
            local_recovery_state=local_recovery_state,
            recovery_state=recovery_state,
            value={
                "schema_version": SCHEMA_VERSION,
                "status": "estate_action_recovery_required",
                "action_id": authorization["action_id"],
                "authorization_sha256": authorization[
                    "authorization_sha256"
                ],
                "reason": "ambiguous_previous_dispatch",
            },
        )
        raise ActionError("estate-action-ambiguous")
    index = {
        "schema_version": SCHEMA_VERSION,
        "action_id": authorization["action_id"],
        "authorization_sha256": authorization["authorization_sha256"],
        "phase": "running",
    }
    atomic_json(index_path, index)
    substitutions = {
        "{request}": str(operation_root / "request.json"),
        "{authorization}": str(operation_root / "authorization.json"),
    }
    command = [
        substitutions.get(item, item)
        for item in authorization["executor"]["argv"]
    ]
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        stdout = process.stdout.decode("utf-8")
        lines = [line for line in stdout.splitlines() if line.strip()]
        if not lines:
            raise ActionError("estate-action-result-missing")
        try:
            raw_result = json.loads(lines[-1])
        except json.JSONDecodeError as error:
            raise ActionError("estate-action-result-invalid") from error
        result = result_payload(raw_result, authorization)
        if process.returncode == 0 and result["status"] != "committed":
            raise ActionError("estate-action-result-invalid")
        if process.returncode != 0 and result["status"] == "committed":
            raise ActionError("estate-action-result-invalid")
        if result["status"] == "recovery_required":
            install_recovery_fences(
                local_recovery_state=local_recovery_state,
                recovery_state=recovery_state,
                value={
                    "schema_version": SCHEMA_VERSION,
                    "status": "estate_action_recovery_required",
                    "action_id": authorization["action_id"],
                    "authorization_sha256": authorization[
                        "authorization_sha256"
                    ],
                    "reason": "executor_recovery_required",
                },
            )
        immutable_json(
            operation_root
            / "results"
            / f"{result['result_sha256'][7:]}.json",
            result,
        )
        index["phase"] = result["status"]
        index["result_sha256"] = result["result_sha256"]
        atomic_json(index_path, index)
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeError,
        ActionError,
    ) as error:
        recovery_payload = {
            "schema_version": SCHEMA_VERSION,
            "status": "estate_action_recovery_required",
            "action_id": authorization["action_id"],
            "authorization_sha256": authorization[
                "authorization_sha256"
            ],
            "reason": (
                error.code
                if isinstance(error, ActionError)
                else "executor_outcome_ambiguous"
            ),
        }
        try:
            install_recovery_fences(
                local_recovery_state=local_recovery_state,
                recovery_state=recovery_state,
                value=recovery_payload,
            )
        except OSError:
            pass
        index["phase"] = "recovery_required"
        try:
            atomic_json(index_path, index)
        except OSError:
            pass
        raise
    return result


def dispatch(
    authorization: dict[str, Any],
    *,
    config_path: Path,
    timeout: int,
) -> dict[str, Any]:
    config = load_authority_config(config_path)
    state_root = Path(config["state_root"])
    halt_switch = Path(config["halt_switch"])
    recovery_state = Path(config["recovery_state"])
    curator_state = Path(config["curator_state"])
    local_recovery_state = state_root / "recovery-required.json"
    state_root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        state_root / "estate-action.lock",
        os.O_RDWR | os.O_CREAT,
        0o600,
    )
    with os.fdopen(descriptor, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _dispatch_locked(
            authorization,
            config_path=config_path,
            state_root=state_root,
            halt_switch=halt_switch,
            recovery_state=recovery_state,
            curator_state=curator_state,
            local_recovery_state=local_recovery_state,
            timeout=timeout,
        )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    authorize_parser = sub.add_parser("authorize")
    authorize_parser.add_argument("--candidate", required=True)
    authorize_parser.add_argument("--output", required=True)
    authorize_parser.add_argument("--config", required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--authorization", required=True)
    verify_parser.add_argument("--config", required=True)
    dispatch_parser = sub.add_parser("dispatch")
    dispatch_parser.add_argument("--authorization", required=True)
    dispatch_parser.add_argument("--config", required=True)
    dispatch_parser.add_argument("--timeout", type=int, default=300)
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "authorize":
            value = authorize(
                load_object(
                    Path(args.candidate), "estate-action-candidate-invalid"
                ),
                Path(args.config),
            )
            immutable_json(Path(args.output), value)
            print(json.dumps({"ok": True, "authorization": value}))
        elif args.command == "verify":
            authorization = validate_authorization(
                load_object(
                    Path(args.authorization),
                    "estate-action-authorization-invalid",
                )
            )
            verify_current_evidence(
                authorization,
                verify_authority(authorization, Path(args.config)),
            )
            print(json.dumps({"ok": True, "authorization": authorization}))
        else:
            result = dispatch(
                load_object(
                    Path(args.authorization),
                    "estate-action-authorization-invalid",
                ),
                config_path=Path(args.config),
                timeout=args.timeout,
            )
            print(json.dumps({"ok": result["ok"], "result": result}))
            raise SystemExit(0 if result["ok"] else 2)
    except (
        ActionError,
        OSError,
        subprocess.SubprocessError,
        TypeError,
        ValueError,
        KeyError,
    ) as error:
        code = (
            error.code
            if isinstance(error, ActionError)
            else "estate-action-step-failed"
        )
        print(json.dumps({"ok": False, "error": {"code": code}}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
