#!/usr/bin/env python3
"""Execute one receiver-bound plugin settings transaction over SSH."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


MAX_REQUEST_BYTES = 4 * 1024 * 1024


class TransportError(RuntimeError):
    pass


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def emit(value: dict[str, Any], status: int) -> None:
    print(json.dumps(value, sort_keys=True))
    raise SystemExit(status)


def identity(
    args: argparse.Namespace,
) -> tuple[dict[str, str], dict[str, Path]]:
    try:
        receiver_id = (
            Path(args.receiver_id_file)
            .expanduser()
            .resolve()
            .read_text(encoding="ascii")
            .strip()
        )
    except OSError as error:
        raise TransportError("receiver-identity-missing") from error
    inputs = {
        "receiver_sha256": Path(__file__),
        "transaction_sha256": Path(args.transaction_script).expanduser(),
        "runtime_verifier_sha256": Path(args.runtime_verifier).expanduser(),
        "estate_sha256": Path(args.estate_script).expanduser(),
    }
    expected = {
        "receiver_sha256": args.expected_receiver_sha,
        "transaction_sha256": args.expected_transaction_sha,
        "runtime_verifier_sha256": args.expected_runtime_verifier_sha,
        "estate_sha256": args.expected_estate_sha,
    }
    if receiver_id != args.expected_receiver_id:
        raise TransportError("receiver-identity-mismatch")
    result = {"receiver_id": receiver_id}
    resolved: dict[str, Path] = {}
    for key, path in inputs.items():
        if path.is_symlink():
            raise TransportError("receiver-code-unavailable")
        resolved[key] = path.resolve()
        if not resolved[key].is_file():
            raise TransportError("receiver-code-unavailable")
        result[key] = sha256_file(resolved[key])
        if result[key] != expected[key]:
            raise TransportError("receiver-code-mismatch")
    return result, resolved


def transaction_command(
    args: argparse.Namespace,
    request_path: str,
    paths: dict[str, Path],
) -> list[str]:
    runtime = [
        args.runtime_python,
        str(paths["runtime_verifier_sha256"]),
        "--estate-script",
        str(paths["estate_sha256"]),
        "--expected-settings",
        args.settings,
        "--target-host-id",
        args.target_host_id,
        "--target-home",
        args.target_home,
        "--user-context-cwd",
        args.user_context_cwd or args.target_home,
        "--copilot-binary",
        args.copilot_binary,
    ]
    return [
        args.transaction_python,
        str(paths["transaction_sha256"]),
        args.action,
        "--request",
        request_path,
        "--settings",
        args.settings,
        "--transaction-root",
        args.transaction_root,
        "--qualification-root",
        args.qualification_root,
        "--lock",
        args.lock,
        "--recovery-state",
        args.recovery_state,
        "--runtime-verifier",
        *runtime,
    ]


def parse_single_result(stdout: bytes) -> dict[str, Any]:
    try:
        lines = [line for line in stdout.decode("utf-8").splitlines() if line.strip()]
        values = [json.loads(line) for line in lines]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TransportError("transaction-output-malformed") from error
    if len(values) != 1 or not isinstance(values[0], dict):
        raise TransportError("transaction-output-ambiguous")
    if not isinstance(values[0].get("ok"), bool):
        raise TransportError("transaction-output-malformed")
    return values[0]


def receive(args: argparse.Namespace) -> None:
    receiver, paths = identity(args)
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if not raw or len(raw) > MAX_REQUEST_BYTES:
        raise TransportError("request-size-invalid")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TransportError("request-malformed") from error
    if not isinstance(value, dict) or value.get("action") != args.action:
        raise TransportError("request-action-mismatch")
    try:
        with tempfile.TemporaryFile() as request:
            request.write(raw)
            request.flush()
            request.seek(0)
            process = subprocess.run(
                transaction_command(
                    args, f"/dev/fd/{request.fileno()}", paths
                ),
                capture_output=True,
                check=False,
                timeout=args.timeout,
                pass_fds=(request.fileno(),),
            )
    except (OSError, subprocess.SubprocessError) as error:
        raise TransportError("transaction-execution-failed") from error
    result = parse_single_result(process.stdout)
    if (process.returncode == 0) != (result["ok"] is True):
        raise TransportError("transaction-status-mismatch")
    emit({**result, "receiver": receiver}, 0 if result["ok"] else 2)


def remote_command(args: argparse.Namespace) -> list[str]:
    receiver = [
        args.remote_python,
        args.remote_script,
        "--receive",
        "--action",
        args.action,
        "--transaction-python",
        args.remote_transaction_python,
        "--transaction-script",
        args.remote_transaction_script,
        "--runtime-python",
        args.remote_runtime_python,
        "--runtime-verifier",
        args.remote_runtime_verifier,
        "--estate-script",
        args.remote_estate_script,
        "--settings",
        args.remote_settings,
        "--transaction-root",
        args.remote_transaction_root,
        "--qualification-root",
        args.remote_qualification_root,
        "--lock",
        args.remote_lock,
        "--recovery-state",
        args.remote_recovery_state,
        "--receiver-id-file",
        args.remote_receiver_id_file,
        "--target-host-id",
        args.target_host_id,
        "--target-home",
        args.target_home,
        "--user-context-cwd",
        args.user_context_cwd or args.target_home,
        "--copilot-binary",
        args.remote_copilot_binary,
        "--expected-receiver-id",
        args.expected_receiver_id,
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
    ]
    return [
        args.ssh_bin,
        *([f"-{args.address_family}"] if args.address_family else []),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "StrictHostKeyChecking=yes",
        "--",
        args.host,
        shlex.join(receiver),
    ]


def local(args: argparse.Namespace) -> None:
    if args.host.startswith("-"):
        raise TransportError("receiver-host-invalid")
    if sha256_file(Path(__file__).resolve()) != args.expected_local_sha:
        raise TransportError("local-proxy-code-mismatch")
    request = Path(args.request)
    if (
        request.is_symlink()
        or not request.is_file()
        or request.stat().st_size < 1
        or request.stat().st_size > MAX_REQUEST_BYTES
    ):
        raise TransportError("request-size-invalid")
    with request.open("rb") as handle:
        raw = handle.read(MAX_REQUEST_BYTES + 1)
    try:
        process = subprocess.run(
            remote_command(args),
            input=raw,
            capture_output=True,
            check=False,
            timeout=args.timeout + 30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise TransportError("remote-outcome-ambiguous") from error
    result = parse_single_result(process.stdout)
    receiver = result.get("receiver")
    if (
        not isinstance(receiver, dict)
        or receiver.get("receiver_id") != args.expected_receiver_id
        or receiver.get("receiver_sha256") != args.expected_receiver_sha
        or receiver.get("transaction_sha256") != args.expected_transaction_sha
        or receiver.get("runtime_verifier_sha256")
        != args.expected_runtime_verifier_sha
        or receiver.get("estate_sha256") != args.expected_estate_sha
        or (process.returncode == 0) != (result["ok"] is True)
    ):
        raise TransportError("remote-result-invalid")
    emit(result, 0 if result["ok"] else 2)


def receiver_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receive", action="store_true")
    parser.add_argument("--action", choices=("disable", "restore"), required=True)
    parser.add_argument("--transaction-python", required=True)
    parser.add_argument("--transaction-script", required=True)
    parser.add_argument("--runtime-python", required=True)
    parser.add_argument("--runtime-verifier", required=True)
    parser.add_argument("--estate-script", required=True)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--transaction-root", required=True)
    parser.add_argument("--qualification-root", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--recovery-state", required=True)
    parser.add_argument("--receiver-id-file", required=True)
    parser.add_argument("--target-host-id", required=True)
    parser.add_argument("--target-home", required=True)
    parser.add_argument("--user-context-cwd")
    parser.add_argument("--copilot-binary", required=True)
    parser.add_argument("--expected-receiver-id", required=True)
    parser.add_argument("--expected-receiver-sha", required=True)
    parser.add_argument("--expected-transaction-sha", required=True)
    parser.add_argument("--expected-runtime-verifier-sha", required=True)
    parser.add_argument("--expected-estate-sha", required=True)
    parser.add_argument("--timeout", type=positive_int, default=300)
    return parser


def local_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("disable", "restore"), required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--ssh-bin", default="/usr/bin/ssh")
    parser.add_argument("--host", required=True)
    parser.add_argument("--address-family", choices=("4", "6"))
    parser.add_argument("--remote-python", required=True)
    parser.add_argument("--remote-script", required=True)
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
    parser.add_argument("--expected-local-sha", required=True)
    parser.add_argument("--expected-receiver-sha", required=True)
    parser.add_argument("--expected-transaction-sha", required=True)
    parser.add_argument("--expected-runtime-verifier-sha", required=True)
    parser.add_argument("--expected-estate-sha", required=True)
    parser.add_argument("--timeout", type=positive_int, default=300)
    return parser


def main() -> None:
    try:
        if "--receive" in sys.argv[1:]:
            receive(receiver_parser().parse_args())
            return
        local(local_parser().parse_args())
    except (TransportError, OSError) as error:
        emit(
            {"ok": False, "error": {"code": str(error)}},
            2,
        )


if __name__ == "__main__":
    main()
