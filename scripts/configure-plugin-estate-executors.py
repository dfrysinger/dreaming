#!/usr/bin/env python3
"""Bind sealed estate action configs to the remote plugin executor."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


ACTIONS = {
    "personal_archive",
    "personal_restore",
    "plugin_disable",
    "plugin_restore",
}


class ConfigError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_sealed(path: Path, keys: set[str]) -> dict[str, Any]:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ConfigError("estate-action-config-invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigError("estate-action-config-invalid") from error
    if (
        not isinstance(value, dict)
        or set(value) != keys | {"config_sha256"}
        or value.get("config_sha256")
        != digest(
            {
                key: item
                for key, item in value.items()
                if key != "config_sha256"
            }
        )
    ):
        raise ConfigError("estate-action-config-invalid")
    return value


def atomic_json(path: Path, value: dict[str, Any], mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def plugin_argv(args: argparse.Namespace, action: str) -> list[str]:
    return [
        args.python,
        str(args.proxy),
        "--action",
        action,
        "--ssh-bin",
        args.ssh_bin,
        "--host",
        args.host,
        *(
            ["--address-family", args.address_family]
            if args.address_family
            else []
        ),
        "--remote-python",
        args.remote_python,
        "--remote-script",
        args.remote_proxy,
        "--remote-transaction-python",
        args.remote_transaction_python,
        "--remote-transaction-script",
        args.remote_transaction_script,
        "--remote-runtime-python",
        args.remote_runtime_python,
        "--remote-runtime-verifier",
        args.remote_runtime_verifier,
        "--remote-estate-script",
        args.remote_estate_script,
        "--remote-settings",
        args.remote_settings,
        "--remote-transaction-root",
        args.remote_transaction_root,
        "--remote-qualification-root",
        args.remote_qualification_root,
        "--remote-lock",
        args.remote_lock,
        "--remote-recovery-state",
        args.remote_recovery_state,
        "--remote-receiver-id-file",
        args.remote_receiver_id_file,
        "--remote-copilot-binary",
        args.remote_copilot_binary,
        "--target-host-id",
        args.target_host_id,
        "--target-home",
        args.target_home,
        "--user-context-cwd",
        args.user_context_cwd or args.target_home,
        "--expected-receiver-id",
        args.expected_receiver_id,
        "--expected-local-sha",
        file_digest(args.proxy),
        "--expected-receiver-sha",
        args.expected_receiver_sha,
        "--expected-transaction-sha",
        args.expected_transaction_sha,
        "--expected-runtime-verifier-sha",
        args.expected_runtime_verifier_sha,
        "--expected-estate-sha",
        args.expected_estate_sha,
        "--timeout",
        str(args.timeout),
        "--request",
        "{request}",
    ]


def configure(args: argparse.Namespace) -> dict[str, str]:
    executors = load_sealed(
        args.executors_config,
        {"schema_version", "executors"},
    )
    authority = load_sealed(
        args.authority_config,
        {
            "schema_version",
            "evidence_root",
            "adapters",
            "receivers",
            "state_root",
            "halt_switch",
            "recovery_state",
            "curator_state",
        },
    )
    if (
        set(executors.get("executors", {})) != ACTIONS
        or set(authority.get("adapters", {})) != ACTIONS
        or set(authority.get("receivers", {})) != ACTIONS
        or args.adapter.is_symlink()
        or not args.adapter.is_file()
        or args.proxy.is_symlink()
        or not args.proxy.is_file()
    ):
        raise ConfigError("estate-action-config-invalid")
    for kind, action in (
        ("plugin_disable", "disable"),
        ("plugin_restore", "restore"),
    ):
        executors["executors"][kind] = {
            "argv": plugin_argv(args, action),
            "timeout": args.timeout + 30,
        }
    executors["config_sha256"] = digest(
        {
            key: item
            for key, item in executors.items()
            if key != "config_sha256"
        }
    )
    adapter_sha = "sha256:" + file_digest(args.adapter)
    for kind in ACTIONS:
        authority["adapters"][kind] = {
            "path": str(args.adapter),
            "sha256": adapter_sha,
            "argv": [
                str(args.adapter),
                "--config",
                str(args.executors_config),
                "--config-sha256",
                executors["config_sha256"],
                "--request",
                "{request}",
                "--authorization",
                "{authorization}",
            ],
        }
    authority["config_sha256"] = digest(
        {
            key: item
            for key, item in authority.items()
            if key != "config_sha256"
        }
    )
    executor_mode = args.executors_config.stat().st_mode & 0o777
    authority_mode = args.authority_config.stat().st_mode & 0o777
    atomic_json(args.executors_config, executors, executor_mode)
    atomic_json(args.authority_config, authority, authority_mode)
    return {
        "executors_config_sha256": executors["config_sha256"],
        "authority_config_sha256": authority["config_sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executors-config", type=Path, required=True)
    parser.add_argument("--authority-config", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--proxy", type=Path, required=True)
    parser.add_argument("--ssh-bin", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--address-family", choices=("4", "6"))
    parser.add_argument("--remote-python", required=True)
    parser.add_argument("--remote-proxy", required=True)
    parser.add_argument("--remote-transaction-python", required=True)
    parser.add_argument("--remote-transaction-script", required=True)
    parser.add_argument("--remote-runtime-python", required=True)
    parser.add_argument("--remote-runtime-verifier", required=True)
    parser.add_argument("--remote-estate-script", required=True)
    parser.add_argument("--remote-settings", required=True)
    parser.add_argument("--remote-transaction-root", required=True)
    parser.add_argument("--remote-qualification-root", required=True)
    parser.add_argument("--remote-lock", required=True)
    parser.add_argument("--remote-recovery-state", required=True)
    parser.add_argument("--remote-receiver-id-file", required=True)
    parser.add_argument("--remote-copilot-binary", required=True)
    parser.add_argument("--target-host-id", required=True)
    parser.add_argument("--target-home", required=True)
    parser.add_argument("--user-context-cwd")
    parser.add_argument("--expected-receiver-id", required=True)
    parser.add_argument("--expected-receiver-sha", required=True)
    parser.add_argument("--expected-transaction-sha", required=True)
    parser.add_argument("--expected-runtime-verifier-sha", required=True)
    parser.add_argument("--expected-estate-sha", required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    try:
        print(json.dumps(configure(args), sort_keys=True))
    except (ConfigError, OSError, TypeError, ValueError) as error:
        print(
            json.dumps(
                {"ok": False, "error": {"code": str(error)}},
                sort_keys=True,
            )
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
