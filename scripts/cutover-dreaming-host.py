#!/usr/bin/env python3
"""Perform and record a fail-closed two-host Dreaming scheduler cutover."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

NEW_KINDS = ("dreaming", "selftest", "watchdog", "dashboard")
LEGACY_KINDS = (
    "sweep",
    "curator",
    "memory",
    "selftest",
    "watchdog",
    "dreaming",
    "dashboard",
)
SELFTEST_RESULT = "== result: 0 failure(s) =="
SELFTEST_RESULT_LINE = re.compile(r"^== result: [0-9]+ failure\(s\) ==$")
SERVICE_PID = re.compile(r"^\s*pid = ([1-9][0-9]*)\s*$", re.MULTILINE)
REMOTE_PATH_PROBE = """\
import os
import sys

try:
    os.lstat(sys.argv[1])
except FileNotFoundError:
    print("absent")
except OSError as error:
    print(f"path probe failed: {error}", file=sys.stderr)
    raise SystemExit(2)
else:
    print("present")
"""


class CutoverError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as error:
        raise CutoverError(f"{field} is not a valid timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CutoverError(f"{field} must include a timezone")
    return parsed


def run(
    command: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise CutoverError(f"{' '.join(command)} failed: {detail}")
    return result


def remote(
    ssh: str,
    host: str,
    command: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run([ssh, host, shlex.join(command)], check=check)


def labels(prefix: str, legacy_prefix: str) -> list[str]:
    return [
        *(f"{prefix}.{kind}" for kind in NEW_KINDS),
        *(f"{legacy_prefix}.{kind}" for kind in LEGACY_KINDS),
    ]


def role_labels(args: argparse.Namespace, role: str) -> list[str]:
    prefix = getattr(args, f"{role}_prefix") or f"com.{args.user}.dreaming"
    legacy_prefix = (
        getattr(args, f"{role}_legacy_prefix") or f"com.{args.user}.skills"
    )
    return labels(prefix, legacy_prefix)


def remote_exists(ssh: str, host: str, path: str) -> bool:
    result = remote(
        ssh,
        host,
        ["/usr/bin/python3", "-c", REMOTE_PATH_PROBE, path],
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise CutoverError(f"cannot inspect remote path {host}:{path}: {detail}")
    state = result.stdout.strip()
    if state not in {"present", "absent"}:
        raise CutoverError(f"remote path probe returned malformed state for {host}:{path}")
    return state == "present"


def remote_file(ssh: str, host: str, path: str) -> bytes:
    result = subprocess.run(
        [ssh, host, shlex.join(["/bin/cat", path])],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip() or "no output"
        raise CutoverError(f"cannot read remote evidence {host}:{path}: {detail}")
    return result.stdout


def launchctl_not_found(
    result: subprocess.CompletedProcess[str], operation: str
) -> bool:
    detail = f"{result.stdout}\n{result.stderr}".lower()
    if operation == "print":
        return result.returncode == 113 and (
            "could not find service" in detail or "service not found" in detail
        )
    return result.returncode in {3, 113} and (
        "could not find service" in detail
        or "no such process" in detail
        or "service not found" in detail
    )


def inspect_service(
    ssh: str, host: str, launchctl: str, uid: int, label: str
) -> tuple[bool, int | None]:
    result = remote(
        ssh,
        host,
        [launchctl, "print", f"gui/{uid}/{label}"],
        check=False,
    )
    if result.returncode == 0:
        match = SERVICE_PID.search(result.stdout)
        return True, int(match.group(1)) if match else None
    if launchctl_not_found(result, "print"):
        return False, None
    detail = result.stderr.strip() or result.stdout.strip() or "no output"
    raise CutoverError(f"cannot inspect launchd service {host}:{label}: {detail}")


def process_table(ssh: str, host: str, ps: str) -> list[dict[str, Any]]:
    output = remote(
        ssh,
        host,
        [ps, "-axww", "-o", "pid=,ppid=,pgid=,command="],
    ).stdout
    processes: list[dict[str, Any]] = []
    for raw in output.splitlines():
        if not raw.strip():
            continue
        fields = raw.strip().split(None, 3)
        if len(fields) != 4 or not all(field.isdigit() for field in fields[:3]):
            raise CutoverError(f"malformed process inspection output for {host}")
        processes.append(
            {
                "pid": int(fields[0]),
                "ppid": int(fields[1]),
                "pgid": int(fields[2]),
                "command": fields[3],
            }
        )
    return processes


def process_tree(
    processes: list[dict[str, Any]], roots: set[int]
) -> list[dict[str, Any]]:
    selected = set(roots)
    changed = True
    while changed:
        changed = False
        for process in processes:
            if process["pid"] not in selected and process["ppid"] in selected:
                selected.add(process["pid"])
                changed = True
    return [process for process in processes if process["pid"] in selected]


def surviving_processes(
    processes: list[dict[str, Any]],
    tracked: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tracked_pids = {process["pid"] for process in tracked}
    tracked_groups = {
        process["pgid"]
        for process in tracked
        if isinstance(process.get("pgid"), int) and process["pgid"] > 1
    }
    return [
        process
        for process in processes
        if process["pid"] in tracked_pids
        or process["pgid"] in tracked_groups
    ]


def host_snapshot(
    ssh: str,
    host: str,
    service_labels: list[str],
    halt_switch: str,
    launchctl: str,
    ps: str,
    tracked_processes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    uid_raw = remote(ssh, host, ["/usr/bin/id", "-u"]).stdout.strip()
    if not uid_raw.isdigit():
        raise CutoverError(f"remote uid is malformed for {host}")
    uid = int(uid_raw)
    service_state: dict[str, bool] = {}
    service_pids: dict[str, int | None] = {}
    for label in service_labels:
        loaded, pid = inspect_service(ssh, host, launchctl, uid, label)
        service_state[label] = loaded
        service_pids[label] = pid
    all_processes = process_table(ssh, host, ps)
    roots = {
        pid
        for label, pid in service_pids.items()
        if pid is not None and not label.endswith(".dashboard")
    }
    processes = process_tree(all_processes, roots)
    if tracked_processes:
        by_pid = {process["pid"]: process for process in processes}
        for process in surviving_processes(all_processes, tracked_processes):
            by_pid[process["pid"]] = process
        processes = list(by_pid.values())
    hostname = remote(ssh, host, ["/bin/hostname"]).stdout.strip()
    if not hostname:
        raise CutoverError(f"remote hostname is empty for {host}")
    return {
        "host": host,
        "hostname": hostname,
        "captured_at": now_iso(),
        "uid": uid,
        "halt_present": remote_exists(ssh, host, halt_switch),
        "services": service_state,
        "service_pids": service_pids,
        "processes": processes,
    }


def scheduling_loaded(snapshot: dict[str, Any]) -> list[str]:
    return [
        label
        for label, loaded in snapshot["services"].items()
        if loaded and not label.endswith(".dashboard")
    ]


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def one_line_remote_file(ssh: str, host: str, path: str) -> tuple[str, bytes]:
    data = remote_file(ssh, host, path)
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise CutoverError(f"remote evidence is not UTF-8: {host}:{path}") from error
    if len(lines) != 1 or not lines[0]:
        raise CutoverError(f"remote evidence is not exactly one line: {host}:{path}")
    return lines[0], data


def selftest_generation_paths(args: argparse.Namespace) -> tuple[str, str]:
    result_parent = PurePosixPath(args.destination_selftest_result).parent
    generation_root = result_parent / "dreaming"
    return (
        args.destination_activation_generation
        or str(generation_root / "activation-generation"),
        args.destination_selftest_generation
        or str(generation_root / "selftest-passed-generation"),
    )


def require_selftest(args: argparse.Namespace) -> dict[str, str]:
    activation_path, passed_path = selftest_generation_paths(args)
    generation_before, _ = one_line_remote_file(
        args.ssh, args.destination, activation_path
    )
    passed_generation, passed_data = one_line_remote_file(
        args.ssh, args.destination, passed_path
    )
    data = remote_file(
        args.ssh, args.destination, args.destination_selftest_result
    )
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise CutoverError("destination installed self-test evidence is not UTF-8") from error
    result_lines = [line for line in lines if SELFTEST_RESULT_LINE.fullmatch(line)]
    if not lines or lines[-1] != SELFTEST_RESULT or result_lines != [SELFTEST_RESULT]:
        raise CutoverError("destination installed self-test has not passed exactly")
    generation_after, generation_data_after = one_line_remote_file(
        args.ssh, args.destination, activation_path
    )
    if generation_before != generation_after:
        raise CutoverError("destination activation generation changed during inspection")
    if passed_generation != generation_after:
        raise CutoverError(
            "destination self-test belongs to a different activation generation"
        )
    return {
        "path": args.destination_selftest_result,
        "sha256": hashlib.sha256(data).hexdigest(),
        "activation_generation": generation_after,
        "activation_generation_path": activation_path,
        "activation_generation_sha256": hashlib.sha256(
            generation_data_after
        ).hexdigest(),
        "selftest_generation_path": passed_path,
        "selftest_generation_sha256": hashlib.sha256(passed_data).hexdigest(),
    }


def bootout_service(
    ssh: str,
    host: str,
    launchctl: str,
    uid: int,
    label: str,
) -> str:
    result = remote(
        ssh,
        host,
        [launchctl, "bootout", f"gui/{uid}/{label}"],
        check=False,
    )
    if result.returncode == 0:
        return "unloaded"
    if launchctl_not_found(result, "bootout"):
        return "not_loaded"
    detail = result.stderr.strip() or result.stdout.strip() or "no output"
    raise CutoverError(f"cannot unload launchd service {host}:{label}: {detail}")


def unload_source(args: argparse.Namespace, snapshot: dict[str, Any]) -> None:
    for label, loaded in snapshot["services"].items():
        if loaded:
            bootout_service(
                args.ssh,
                args.source,
                args.launchctl,
                snapshot["uid"],
                label,
            )


def snapshot_for(
    args: argparse.Namespace,
    role: str,
    tracked_processes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return host_snapshot(
        args.ssh,
        getattr(args, role),
        role_labels(args, role),
        getattr(args, f"{role}_halt"),
        args.launchctl,
        args.ps,
        tracked_processes,
    )


def same_host(source: dict[str, Any], destination: dict[str, Any]) -> bool:
    source_name = source["hostname"].rstrip(".").lower()
    destination_name = destination["hostname"].rstrip(".").lower()
    return source_name == destination_name


def recover_destination(
    args: argparse.Namespace,
    observed: dict[str, Any] | None,
) -> dict[str, Any]:
    recovery: dict[str, Any] = {
        "started_at": now_iso(),
        "actions": [],
        "errors": [],
    }
    tracked: list[dict[str, Any]] = []
    if observed is None:
        try:
            observed = snapshot_for(args, "destination")
        except CutoverError as error:
            recovery["errors"].append(f"pre-recovery inspection: {error}")
    if observed is not None:
        tracked = observed["processes"]
        recovery["destination_before_recovery"] = observed
    try:
        remote(args.ssh, args.destination, ["/usr/bin/touch", args.destination_halt])
        recovery["actions"].append("halt_restored")
    except CutoverError as error:
        recovery["errors"].append(str(error))
    uid = observed["uid"] if observed is not None else None
    if uid is None:
        try:
            uid_raw = remote(
                args.ssh, args.destination, ["/usr/bin/id", "-u"]
            ).stdout.strip()
            if not uid_raw.isdigit():
                raise CutoverError("destination uid is malformed during recovery")
            uid = int(uid_raw)
        except CutoverError as error:
            recovery["errors"].append(str(error))
    if uid is not None:
        for label in role_labels(args, "destination"):
            try:
                result = bootout_service(
                    args.ssh,
                    args.destination,
                    args.launchctl,
                    uid,
                    label,
                )
                recovery["actions"].append(f"{result}:{label}")
            except CutoverError as error:
                recovery["errors"].append(str(error))
    try:
        after = snapshot_for(args, "destination", tracked)
        recovery["destination_after_recovery"] = after
        if not after["halt_present"]:
            recovery["errors"].append("destination halt switch is absent after recovery")
        if scheduling_loaded(after):
            recovery["errors"].append(
                "destination scheduling services remain loaded after recovery"
            )
        if after["processes"]:
            recovery["errors"].append(
                "destination scheduled processes remain active after recovery"
            )
    except CutoverError as error:
        recovery["errors"].append(f"post-recovery inspection: {error}")
    recovery["finished_at"] = now_iso()
    recovery["status"] = "recovered" if not recovery["errors"] else "recovery_failed"
    return recovery


def command_cutover(args: argparse.Namespace) -> int:
    receipt_path = Path(args.receipt).expanduser().resolve()
    if receipt_path.exists():
        raise CutoverError(f"cutover receipt already exists: {receipt_path}")
    enable = shlex.split(args.destination_enable_command)
    if not enable:
        raise CutoverError("destination enable command is empty")
    source_before = snapshot_for(args, "source")
    destination_before = snapshot_for(args, "destination")
    if same_host(source_before, destination_before):
        raise CutoverError("source and destination resolve to the same host")
    if not source_before["halt_present"]:
        raise CutoverError("source halt switch is absent")
    if not destination_before["halt_present"]:
        raise CutoverError("destination must remain halted before cutover")
    if not scheduling_loaded(source_before):
        raise CutoverError(
            "source has no loaded scheduling labels in the configured namespaces"
        )
    selftest = require_selftest(args)
    payload = {
        "schema_version": 1,
        "status": "prepared",
        "source_before": source_before,
        "destination_before": destination_before,
        "destination_selftest": selftest,
        "prepared_at": now_iso(),
    }
    atomic_json(receipt_path, payload)
    try:
        unload_source(args, source_before)
        source_unloaded = snapshot_for(
            args, "source", source_before["processes"]
        )
        if scheduling_loaded(source_unloaded) or source_unloaded["processes"]:
            raise CutoverError("source scheduling services or processes remain active")
    except CutoverError as error:
        payload["status"] = "source_unload_failed"
        payload["failure"] = str(error)
        payload["failed_at"] = now_iso()
        atomic_json(receipt_path, payload)
        raise
    payload["status"] = "source_unloaded"
    payload["source_unloaded"] = source_unloaded
    payload["source_unloaded_at"] = now_iso()
    atomic_json(receipt_path, payload)
    payload["status"] = "activating"
    payload["activation_started_at"] = now_iso()
    atomic_json(receipt_path, payload)
    destination_after: dict[str, Any] | None = None
    try:
        remote(args.ssh, args.destination, enable)
        destination_after = snapshot_for(args, "destination")
        source_after = snapshot_for(
            args, "source", source_before["processes"]
        )
        if (
            not source_after["halt_present"]
            or scheduling_loaded(source_after)
            or source_after["processes"]
        ):
            raise CutoverError("source is not inert after destination activation")
        destination_label = role_labels(args, "destination")[0]
        if destination_after["halt_present"]:
            raise CutoverError("destination remains halted after enable")
        if not destination_after["services"].get(destination_label):
            raise CutoverError("destination scheduler is not loaded")
        payload["status"] = "activated"
        payload["source_after"] = source_after
        payload["destination_after"] = destination_after
        payload["activated_at"] = now_iso()
        atomic_json(receipt_path, payload)
    except (CutoverError, OSError) as error:
        recovery = recover_destination(args, destination_after)
        payload["status"] = (
            "activation_failed"
            if recovery["status"] == "recovered"
            else "recovery_failed"
        )
        payload["failure"] = str(error)
        payload["recovery"] = recovery
        payload["failed_at"] = now_iso()
        atomic_json(receipt_path, payload)
        raise CutoverError(
            f"destination activation failed; recovery {recovery['status']}: {error}"
        ) from error
    print(receipt_path)
    return 0


def command_record_pass(args: argparse.Namespace) -> int:
    receipt_path = Path(args.receipt).expanduser().resolve()
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CutoverError(f"cannot read cutover receipt: {error}") from error
    if not isinstance(payload, dict):
        raise CutoverError("cutover receipt is not a JSON object")
    if payload.get("status") != "activated":
        raise CutoverError("cutover receipt is not awaiting a scheduled pass")
    evidence = remote_file(args.ssh, args.destination, args.run_evidence)
    if not evidence.strip():
        raise CutoverError("scheduled pass evidence is empty")
    try:
        run_receipt = json.loads(evidence)
    except json.JSONDecodeError as error:
        raise CutoverError("scheduled pass evidence is not JSON") from error
    if not isinstance(run_receipt, dict):
        raise CutoverError("scheduled pass evidence is not a JSON object")
    if run_receipt.get("status") != "ok":
        raise CutoverError("scheduled pass did not finish successfully")
    if run_receipt.get("cadence_committed") is not True:
        raise CutoverError("scheduled pass has no committed terminal evidence")
    started_at = parse_timestamp(run_receipt.get("started_at"), "started_at")
    ended_at = parse_timestamp(run_receipt.get("ended_at"), "ended_at")
    activated_at = parse_timestamp(payload.get("activated_at"), "activated_at")
    if started_at <= activated_at:
        raise CutoverError("scheduled pass did not start after destination activation")
    if ended_at < started_at:
        raise CutoverError("scheduled pass terminal timestamp precedes its start")
    run_id = run_receipt.get("run_id")
    if not isinstance(run_id, str) or Path(args.run_evidence).stem != run_id:
        raise CutoverError("scheduled pass evidence identity is malformed")
    source = snapshot_for(args, "source")
    destination = snapshot_for(args, "destination")
    if same_host(source, destination):
        raise CutoverError("source and destination resolve to the same host")
    if (
        not source["halt_present"]
        or scheduling_loaded(source)
        or source["processes"]
    ):
        raise CutoverError("source is not inert after scheduled pass")
    destination_label = role_labels(args, "destination")[0]
    if destination["halt_present"] or not destination["services"].get(
        destination_label
    ):
        raise CutoverError("destination is not the active scheduler")
    payload["status"] = "complete"
    payload["scheduled_pass"] = {
        "evidence_path": args.run_evidence,
        "evidence_sha256": hashlib.sha256(evidence).hexdigest(),
        "run_id": run_id,
        "status": run_receipt["status"],
        "started_at": run_receipt["started_at"],
        "ended_at": run_receipt["ended_at"],
        "cadence_committed": True,
        "recorded_at": now_iso(),
    }
    payload["source_final"] = source
    payload["destination_final"] = destination
    atomic_json(receipt_path, payload)
    return 0


def add_common(root: argparse.ArgumentParser) -> None:
    root.add_argument("--ssh", default="ssh")
    root.add_argument("--source", required=True)
    root.add_argument("--destination", required=True)
    root.add_argument("--user", required=True)
    root.add_argument("--source-halt", required=True)
    root.add_argument("--destination-halt", required=True)
    root.add_argument("--source-prefix")
    root.add_argument("--source-legacy-prefix")
    root.add_argument("--destination-prefix")
    root.add_argument("--destination-legacy-prefix")
    root.add_argument("--launchctl", default="/bin/launchctl")
    root.add_argument("--ps", default="/bin/ps")
    root.add_argument("--receipt", required=True)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    cutover = sub.add_parser("cutover")
    add_common(cutover)
    cutover.add_argument("--destination-selftest-result", required=True)
    cutover.add_argument("--destination-activation-generation")
    cutover.add_argument("--destination-selftest-generation")
    cutover.add_argument("--destination-enable-command", required=True)
    cutover.set_defaults(func=command_cutover)
    record = sub.add_parser("record-pass")
    add_common(record)
    record.add_argument("--run-evidence", required=True)
    record.set_defaults(func=command_record_pass)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except (CutoverError, OSError, ValueError, KeyError) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
