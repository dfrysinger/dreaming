#!/usr/bin/env python3
"""Deterministic executable adapter fixture for the Dreaming v1 protocols."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROLE_PROTOCOLS = {
    "session-source": (
        "dreaming.session-source",
        [
            "stable-pagination",
            "qualified-identity",
            "bounded-render",
            "revision-inspect",
        ],
    ),
    "review-executor": (
        "dreaming.review-executor",
        ["source-blind", "mutation-fence", "completion-sentinel"],
    ),
    "skill-publisher": (
        "dreaming.skill-publisher",
        ["content-addressed-bundle", "ownership-safe-remove", "exact-inventory"],
    ),
}


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def emit(value: dict[str, Any], status: int = 0) -> None:
    print(json.dumps(value, sort_keys=True))
    raise SystemExit(status)


def fail(code: str, message: str) -> None:
    emit({"ok": False, "error": {"code": code, "message": message}}, 2)


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        fail("fixture-invalid", str(error))


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def session_identity(source: str, record: dict[str, Any]) -> dict[str, Any]:
    events = record["events"]
    snapshot_digest = "sha256:" + hashlib.sha256(canonical(events)).hexdigest()
    frontier = str(events[-1]["sequence"]) if events else "0"
    revision_input = {
        "frontier": frontier,
        "snapshot_digest": snapshot_digest,
        "completion_state": record["completion_state"],
        "adapter_version": record.get("adapter_version", 1),
    }
    revision = "sha256:" + hashlib.sha256(canonical(revision_input)).hexdigest()
    native = record["native_session_id"]
    return {
        "source": source,
        "native_session_id": native,
        "qualified_session_id": f"{source}:{native}",
        "repository_scope": record.get("repository_scope", "scope-1"),
        "started_at": record.get("started_at", record["updated_at"]),
        "updated_at": record["updated_at"],
        "source_revision": revision,
        "event_frontier": frontier,
        "snapshot_digest": snapshot_digest,
        "completion_state": record["completion_state"],
        "adapter_version": record.get("adapter_version", 1),
    }


def source_command(args: argparse.Namespace, fixture: Path) -> None:
    data = load(fixture, {})
    source = data.get("source", args.adapter_id)
    records = data.get("sessions", [])
    if args.command == "doctor":
        emit({"ok": True, "healthy": True})
    if args.command == "watermark":
        emit({"ok": True, "watermark": data.get("watermark", 0)})
    if args.command == "list":
        if data.get("touch_on_list"):
            Path(data["touch_on_list"]).parent.mkdir(parents=True, exist_ok=True)
            Path(data["touch_on_list"]).touch()
        floor = json.loads(args.floor)
        ceiling = json.loads(args.ceiling)
        ordered = sorted(
            records, key=lambda item: (item["updated_at"], item["native_session_id"])
        )
        eligible = [
            record
            for record in ordered
            if (floor is None or record["updated_at"] >= floor)
            and record["updated_at"] <= ceiling
        ]
        start = int(args.cursor or 0)
        page = eligible[start : start + args.page_size]
        next_cursor = str(start + len(page))
        emit(
            {
                "ok": True,
                "items": [session_identity(source, item) for item in page],
                "next_cursor": next_cursor,
                "exhausted": start + len(page) >= len(eligible),
            }
        )
    if args.command in {"inspect", "render"}:
        native = args.session.split(":", 1)[-1]
        record = next(
            (item for item in records if item["native_session_id"] == native), None
        )
        if record is None:
            fail("session-missing", args.session)
        if args.command == "inspect":
            emit({"ok": True, "session": session_identity(source, record)})
        emit(
            {
                "ok": True,
                "events": record["events"],
                "truncated": bool(record.get("truncated", False)),
            }
        )
    fail("unsupported-command", args.command)


def executor_command(args: argparse.Namespace, fixture: Path) -> None:
    state = load(fixture, {})
    if args.command == "doctor":
        emit(
            {
                "ok": True,
                "healthy": state.get("healthy", True),
                "boundary_ready": state.get("boundary_ready", True),
            }
        )
    if args.command == "version":
        emit({"ok": True, "executor_version": state.get("executor_version", "fake-1")})
    if args.command == "run":
        snapshot = load(Path(args.snapshot), {})
        if snapshot.get("packet_kind") == "draft_review":
            result = {
                "status": "ok",
                "mutation_started": False,
                "completion_sentinel": "DREAMING_DRAFT_REVIEW_COMPLETE",
                "decision": state.get("draft_review_decision", "approve"),
                "summary": state.get(
                    "draft_review_summary", "The proposal satisfies the fixture rubric"
                ),
                "model": state.get("draft_review_model", "fake-default"),
            }
            save(Path(args.result), result)
            emit({"ok": True, **result})
        if "events" not in snapshot:
            fail("snapshot-invalid", args.snapshot)
        mutate_fixture = state.get("mutate_source_fixture")
        if mutate_fixture:
            source_fixture = Path(mutate_fixture)
            source_state = load(source_fixture, {})
            target = source_state["sessions"][0]
            sequence = target["events"][-1]["sequence"] + 1
            target["events"].append(
                {
                    "source": source_state["source"],
                    "qualified_session_id": (
                        f"{source_state['source']}:{target['native_session_id']}"
                    ),
                    "sequence": sequence,
                    "timestamp": sequence,
                    "kind": "session_end",
                    "tool_name": None,
                    "text": "changed during review",
                    "source_event_id": f"event-{sequence}",
                }
            )
            target["updated_at"] += 1
            source_state["watermark"] = max(
                source_state["watermark"], target["updated_at"]
            )
            save(source_fixture, source_state)
        mode = state.get("mode", "success")
        default_evidence = (
            [snapshot["events"][0]["source_event_id"]]
            if state.get("terminal_route", "discard") in {"skill", "support_file"}
            and snapshot.get("events")
            else []
        )
        result = {
            "status": "ok" if mode == "success" else "failed",
            "mutation_started": state.get(
                "mutation_started", mode == "fail-after-mutation"
            ),
            "completion_sentinel": (
                "DREAMING_REVIEW_COMPLETE" if mode == "success" else None
            ),
            "terminal_route": state.get("terminal_route", "discard"),
            "summary": state.get("summary", "No durable procedure"),
            "routing_reason": state.get(
                "routing_reason", "The bounded session contains no reusable lesson"
            ),
            "artifact": state.get("artifact"),
            "evidence_event_ids": state.get(
                "evidence_event_ids",
                default_evidence,
            ),
        }
        save(Path(args.result), result)
        emit({"ok": True, **result})
    fail("unsupported-command", args.command)


def publisher_command(args: argparse.Namespace, fixture: Path) -> None:
    state = load(fixture, {"owned_bundle_ids": []})
    if args.command == "doctor":
        emit({"ok": True, "healthy": state.get("healthy", True)})
    if args.command == "inventory":
        emit({"ok": True, "owned_bundle_ids": state.get("owned_bundle_ids", [])})
    if args.command == "install":
        bundle = Path(args.bundle)
        manifest = load(bundle / "dreaming-bundle-manifest.json", {})
        if manifest.get("bundle_id") != args.bundle_id:
            fail("bundle-proof-invalid", args.bundle_id)
        owned = state.setdefault("owned_bundle_ids", [])
        if args.bundle_id not in owned:
            owned.append(args.bundle_id)
        state["installed_bundle"] = str(bundle)
        save(fixture, state)
        emit({"ok": True, "installed": True, "bundle_id": args.bundle_id})
    if args.command == "verify":
        verified = args.bundle_id in state.get("owned_bundle_ids", [])
        emit(
            {
                "ok": True,
                "verified": verified,
                "bundle_id": args.bundle_id if verified else None,
            }
        )
    if args.command == "remove":
        state["owned_bundle_ids"] = []
        state.pop("installed_bundle", None)
        save(fixture, state)
        emit({"ok": True, "removed": True})
    fail("unsupported-command", args.command)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--fixture", required=True)
    result.add_argument("--adapter-id", required=True)
    result.add_argument("--role", required=True, choices=ROLE_PROTOCOLS)
    sub = result.add_subparsers(dest="command", required=True)
    contract = sub.add_parser("contract")
    contract.add_argument("--role", dest="contract_role", required=True)
    for name in ("doctor", "watermark", "version", "inventory", "remove"):
        sub.add_parser(name)
    listing = sub.add_parser("list")
    listing.add_argument("--floor", required=True)
    listing.add_argument("--ceiling", required=True)
    listing.add_argument("--cursor", required=True)
    listing.add_argument("--page-size", required=True, type=int)
    for name in ("inspect", "render"):
        command = sub.add_parser(name)
        command.add_argument("--session", required=True)
    run = sub.add_parser("run")
    run.add_argument("--snapshot", required=True)
    run.add_argument("--result", required=True)
    install = sub.add_parser("install")
    install.add_argument("--bundle", required=True)
    install.add_argument("--bundle-id", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--bundle-id", required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    if args.command == "contract":
        if args.contract_role != args.role:
            fail("role-mismatch", args.contract_role)
        protocol, capabilities = ROLE_PROTOCOLS[args.role]
        emit(
            {
                "ok": True,
                "protocol": protocol,
                "version": 1,
                "adapter_id": args.adapter_id,
                "capabilities": capabilities,
            }
        )
    fixture = Path(args.fixture)
    if args.role == "session-source":
        source_command(args, fixture)
    if args.role == "review-executor":
        executor_command(args, fixture)
    publisher_command(args, fixture)


if __name__ == "__main__":
    main()
