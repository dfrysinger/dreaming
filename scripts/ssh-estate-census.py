#!/usr/bin/env python3
"""Collect a bounded Copilot estate census from another Mac over SSH."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

MAX_CONFIG_BYTES = 1024 * 1024


class CensusError(RuntimeError):
    """A fail-closed remote census transport error."""


def sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def emit(value: dict[str, Any], status: int = 0) -> None:
    print(json.dumps(value, sort_keys=True))
    raise SystemExit(status)


def receiver_identity(args: argparse.Namespace) -> dict[str, str]:
    receiver_id_path = Path(args.receiver_id_file).expanduser().resolve()
    try:
        receiver_id = receiver_id_path.read_text(encoding="ascii").strip()
    except OSError as error:
        raise CensusError(f"receiver identity missing: {receiver_id_path}") from error
    receiver_sha = sha256_file(Path(__file__).resolve())
    collector_sha = sha256_file(Path(args.estate_script).expanduser().resolve())
    if receiver_id != args.expected_receiver_id:
        raise CensusError("receiver identity mismatch")
    if receiver_sha != args.expected_receiver_sha:
        raise CensusError("receiver code mismatch")
    if collector_sha != args.expected_collector_sha:
        raise CensusError("collector code mismatch")
    return {
        "receiver_id": receiver_id,
        "receiver_sha256": receiver_sha,
        "collector_sha256": collector_sha,
    }


def load_collector(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("dreaming_estate_receiver", path)
    if spec is None or spec.loader is None:
        raise CensusError(f"cannot load collector: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def receiver_config(args: argparse.Namespace) -> dict[str, Any]:
    config: dict[str, Any] = {
        "host_id": args.target_host_id,
        "target_home": args.target_home,
        "user_context_cwd": args.user_context_cwd or args.target_home,
        "copilot_binary": args.copilot_binary,
    }
    if args.project_contexts_file:
        path = Path(args.project_contexts_file).expanduser().resolve()
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise CensusError(f"project contexts unreadable: {path}") from error
        if len(raw) > MAX_CONFIG_BYTES:
            raise CensusError("project contexts file is too large")
        try:
            contexts = json.loads(raw)
        except json.JSONDecodeError as error:
            raise CensusError(f"project contexts malformed: {path}") from error
        if not isinstance(contexts, list):
            raise CensusError("project contexts must be a list")
        config["project_contexts"] = contexts
    return config


def receive(args: argparse.Namespace) -> None:
    identity = receiver_identity(args)
    collector = load_collector(Path(args.estate_script).expanduser().resolve())
    try:
        census = collector.collect(receiver_config(args))
    except collector.EstateError as error:
        raise CensusError(str(error)) from error
    emit({"ok": True, "census": census, "receiver": identity})


def remote_command(args: argparse.Namespace) -> list[str]:
    receiver = [
        args.remote_python,
        args.remote_script,
        "--receive",
        "--estate-script",
        args.remote_estate_script,
        "--receiver-id-file",
        args.remote_receiver_id_file,
        "--expected-receiver-id",
        args.expected_receiver_id,
        "--expected-receiver-sha",
        args.expected_receiver_sha,
        "--expected-collector-sha",
        args.expected_collector_sha,
        "--target-host-id",
        args.target_host_id,
        "--target-home",
        args.target_home,
        "--copilot-binary",
        args.remote_copilot_binary,
    ]
    if args.user_context_cwd:
        receiver.extend(["--user-context-cwd", args.user_context_cwd])
    if args.remote_project_contexts_file:
        receiver.extend(
            ["--project-contexts-file", args.remote_project_contexts_file]
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


def parse_result(process: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        values = [
            json.loads(line)
            for line in process.stdout.splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as error:
        raise CensusError("remote census returned malformed JSON") from error
    if len(values) != 1 or not isinstance(values[0], dict):
        raise CensusError("remote census returned an ambiguous result")
    result = values[0]
    if process.returncode != 0 or result.get("ok") is not True:
        raise CensusError(str(result.get("error", "remote census failed")))
    return result


def local(args: argparse.Namespace) -> None:
    if args.host.startswith("-"):
        raise CensusError("receiver host is invalid")
    try:
        process = subprocess.run(
            remote_command(args),
            check=False,
            capture_output=True,
            text=True,
            timeout=args.timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CensusError(f"remote census failed: {error}") from error
    result = parse_result(process)
    receiver = result.get("receiver")
    if (
        not isinstance(receiver, dict)
        or receiver.get("receiver_id") != args.expected_receiver_id
        or receiver.get("receiver_sha256") != args.expected_receiver_sha
        or receiver.get("collector_sha256") != args.expected_collector_sha
    ):
        raise CensusError("remote census identity is invalid")
    emit(result)


def common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--target-host-id", required=True)
    parser.add_argument("--target-home", required=True)
    parser.add_argument("--user-context-cwd")
    return parser


def receiver_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(parents=[common_parser()])
    parser.add_argument("--receive", action="store_true")
    parser.add_argument("--estate-script", required=True)
    parser.add_argument("--receiver-id-file", required=True)
    parser.add_argument("--expected-receiver-id", required=True)
    parser.add_argument("--expected-receiver-sha", required=True)
    parser.add_argument("--expected-collector-sha", required=True)
    parser.add_argument("--copilot-binary", required=True)
    parser.add_argument("--project-contexts-file")
    return parser


def local_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(parents=[common_parser()])
    parser.add_argument("--ssh-bin", default="/usr/bin/ssh")
    parser.add_argument("--host", required=True)
    parser.add_argument("--address-family", choices=("4", "6"))
    parser.add_argument("--remote-python", required=True)
    parser.add_argument("--remote-script", required=True)
    parser.add_argument("--remote-estate-script", required=True)
    parser.add_argument("--remote-receiver-id-file", required=True)
    parser.add_argument("--remote-copilot-binary", required=True)
    parser.add_argument("--remote-project-contexts-file")
    parser.add_argument("--expected-receiver-id", required=True)
    parser.add_argument("--expected-receiver-sha", required=True)
    parser.add_argument("--expected-collector-sha", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    return parser


def main() -> None:
    try:
        if "--receive" in sys.argv[1:]:
            receive(receiver_parser().parse_args())
        local(local_parser().parse_args())
    except CensusError as error:
        emit({"ok": False, "error": str(error)}, 2)
    except (OSError, subprocess.SubprocessError) as error:
        emit({"ok": False, "error": f"remote census failed: {error}"}, 2)


if __name__ == "__main__":
    main()
