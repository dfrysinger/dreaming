#!/usr/bin/env python3
"""Bridge a governed estate action to its configured inner executor."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
PROTOCOL = "dreaming.estate-action-result"
MAX_JSON_BYTES = 1024 * 1024
ACTION_KINDS = {
    "personal_archive",
    "personal_restore",
    "plugin_disable",
    "plugin_restore",
}


class AdapterError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def load_json(path: Path, code: str) -> dict[str, Any]:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_JSON_BYTES
    ):
        raise AdapterError(code)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AdapterError(code) from error
    if not isinstance(value, dict):
        raise AdapterError(code)
    return value


def authority_module() -> Any:
    path = Path(__file__).resolve().with_name("estate-action.py")
    if path.is_symlink() or not path.is_file():
        raise AdapterError("estate-action-authority-invalid")
    specification = importlib.util.spec_from_file_location(
        "estate_action_adapter_authority", path
    )
    if specification is None or specification.loader is None:
        raise AdapterError("estate-action-authority-invalid")
    module = importlib.util.module_from_spec(specification)
    try:
        specification.loader.exec_module(module)
    except (ImportError, OSError, SyntaxError) as error:
        raise AdapterError("estate-action-authority-invalid") from error
    return module


def validated_authorization(path: Path) -> dict[str, Any]:
    authority = authority_module()
    try:
        return authority.validate_authorization(
            load_json(path, "estate-action-authorization-invalid")
        )
    except authority.ActionError as error:
        raise AdapterError("estate-action-authorization-invalid") from error


def load_config(path: Path, expected_sha256: str) -> dict[str, Any]:
    value = load_json(path, "estate-action-adapter-config-invalid")
    if set(value) != {
        "schema_version",
        "executors",
        "config_sha256",
    }:
        raise AdapterError("estate-action-adapter-config-invalid")
    payload = {
        key: item for key, item in value.items() if key != "config_sha256"
    }
    executors = value.get("executors")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("config_sha256") != digest(payload)
        or value.get("config_sha256") != expected_sha256
        or not isinstance(executors, dict)
        or set(executors) != ACTION_KINDS
    ):
        raise AdapterError("estate-action-adapter-config-invalid")
    for executor in executors.values():
        if (
            not isinstance(executor, dict)
            or set(executor) != {"argv", "timeout"}
            or not isinstance(executor.get("argv"), list)
            or not executor["argv"]
            or any(
                not isinstance(item, str) or not item
                for item in executor["argv"]
            )
            or executor["argv"].count("{request}") != 1
            or not isinstance(executor.get("timeout"), int)
            or isinstance(executor["timeout"], bool)
            or executor["timeout"] < 1
        ):
            raise AdapterError("estate-action-adapter-config-invalid")
        executable = Path(executor["argv"][0])
        if (
            not executable.is_absolute()
            or executable.is_symlink()
            or not executable.is_file()
        ):
            raise AdapterError("estate-action-adapter-config-invalid")
    return value


def inner_receipt(
    raw_result: dict[str, Any], normalized: dict[str, Any]
) -> dict[str, Any]:
    receipt_identity = None
    for field in (
        "receipt_sha256",
        "result_sha256",
        "request_sha256",
        "qualification_sha256",
    ):
        value = normalized.get(field)
        if isinstance(value, str) and value:
            receipt_identity = value
            break
    return {
        "inner_status": normalized["status"],
        "inner_result_sha256": digest(raw_result),
        "inner_receipt_identity": receipt_identity,
    }


def normalize_inner_result(
    result: Any, kind: str, returncode: int
) -> dict[str, Any]:
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise AdapterError("estate-action-inner-result-invalid")
    normalized = result
    if kind.startswith("plugin_"):
        nested = result.get("result")
        if nested is None and result["ok"] is False:
            return {
                "ok": False,
                "status": "recovery_required",
                "error": result.get("error"),
            }
        if (
            not isinstance(nested, dict)
            or not isinstance(nested.get("ok"), bool)
            or nested["ok"] != result["ok"]
        ):
            raise AdapterError("estate-action-inner-result-invalid")
        normalized = nested
    if (
        normalized.get("status")
        not in {
            "committed",
            "rejected",
            "rolled_back",
            "recovery_required",
        }
        or not isinstance(normalized.get("ok"), bool)
        or (normalized["status"] == "committed") != normalized["ok"]
        or (returncode == 0) != normalized["ok"]
    ):
        raise AdapterError("estate-action-inner-result-invalid")
    return normalized


def result_payload(
    authorization: dict[str, Any],
    status: str,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "action_id": authorization["action_id"],
        "authorization_sha256": authorization["authorization_sha256"],
        "executor_request_sha256": authorization["action"][
            "executor_request_sha256"
        ],
        "status": status,
        "ok": status == "committed",
        "receipt": receipt,
    }
    return {**payload, "result_sha256": digest(payload)}


def execute(args: argparse.Namespace) -> dict[str, Any]:
    authorization = validated_authorization(args.authorization)
    request = load_json(args.request, "estate-action-request-invalid")
    if digest(request) != authorization["action"]["executor_request_sha256"]:
        raise AdapterError("estate-action-request-invalid")
    config = load_config(args.config, args.config_sha256)
    executor = config["executors"][authorization["action"]["kind"]]
    try:
        with tempfile.TemporaryFile() as verified_request:
            verified_request.write(canonical(request))
            verified_request.flush()
            verified_request.seek(0)
            request_path = f"/dev/fd/{verified_request.fileno()}"
            command = [
                request_path if item == "{request}" else item
                for item in executor["argv"]
            ]
            process = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=executor["timeout"],
                pass_fds=(verified_request.fileno(),),
            )
        stdout = process.stdout.decode("utf-8")
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise AdapterError("estate-action-inner-executor-failed") from error
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise AdapterError("estate-action-inner-result-missing")
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise AdapterError("estate-action-inner-result-invalid") from error
    normalized = normalize_inner_result(
        result, authorization["action"]["kind"], process.returncode
    )
    return result_payload(
        authorization,
        normalized["status"],
        inner_receipt(result, normalized),
    )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--config", type=Path, required=True)
    value.add_argument("--config-sha256", required=True)
    value.add_argument("--request", type=Path, required=True)
    value.add_argument("--authorization", type=Path, required=True)
    return value


def main() -> None:
    args = parser().parse_args()
    try:
        result = execute(args)
    except AdapterError as error:
        try:
            authorization = validated_authorization(args.authorization)
        except AdapterError:
            raise SystemExit(2) from error
        result = result_payload(
            authorization,
            "recovery_required",
            {"error": str(error)},
        )
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
