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
        [
            "source-blind",
            "mutation-fence",
            "completion-sentinel",
            "task-profile-v2",
        ],
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


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


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
        if args.mode == "profile":
            session_id = snapshot.get("identity", {}).get(
                "qualified_session_id"
            )
            profile_error = state.get("task_profile_errors_by_session", {}).get(
                session_id, state.get("task_profile_error")
            )
            if isinstance(profile_error, dict):
                fail(
                    str(profile_error.get("code", "malformed-executor-result")),
                    str(profile_error.get("message", "invalid task profile")),
                )
            snapshot_sha256 = digest(snapshot)
            profiles: list[dict[str, Any]] = []
            if args.task_profile_correction:
                templates = state.get(
                    "task_profile_corrections_by_session", {}
                ).get(
                    session_id,
                    state.get(
                        "task_profile_corrections",
                        state.get("task_profiles_by_session", {}).get(
                            session_id, state.get("task_profiles", [])
                        ),
                    ),
                )
            else:
                templates = state.get("task_profiles_by_session", {}).get(
                    session_id, state.get("task_profiles", [])
                )
            for template in templates:
                source_event_ids = template.get(
                    "source_event_ids",
                    [
                        event["source_event_id"]
                        for event in snapshot.get("events", [])
                    ],
                )
                goal_event_id = template.get("goal_event_id", next((event_id for event_id in source_event_ids if next((event for event in snapshot.get("events", []) if event.get("source_event_id") == event_id and event.get("kind") == "user_message"), None) is not None), None))
                model_profile = {
                    "source_event_ids": source_event_ids,
                    "goal_event_id": goal_event_id,
                    "task_type": template["task_type"],
                    "abstract_summary": template["abstract_summary"],
                    "reuse_value": template["reuse_value"],
                    "procedure": template.get("procedure"),
                    "confidence": template.get("confidence", "high"),
                    "sensitive_source": template.get(
                        "sensitive_source", False
                    ),
                    "task_state": template.get(
                        "task_state", "completed"
                    ),
                }
                # The model selects only the event identity.  The owner adds its timestamp.
                model_profile.pop("goal_event_id")
                procedure = model_profile["procedure"]
                selected_profile = {**model_profile, "goal_event_id": goal_event_id}
                profiles.append(
                    {
                        **selected_profile,
                        "task_key": digest(
                            {
                                "qualified_session_id": session_id,
                                "source_event_ids": source_event_ids,
                            }
                        ),
                        "profile_id": digest(
                            {
                                "qualified_session_id": session_id,
                                **selected_profile,
                            }
                        ),
                        "procedure_fingerprint": (
                            digest(procedure)
                            if isinstance(procedure, dict)
                            else None
                        ),
                    }
                )
                if state.get("legacy_task_profile_receipt"):
                    profiles[-1].pop("goal_event_id")
            result = {
                "status": "ok",
                "mutation_started": False,
                "completion_sentinel": "DREAMING_TASK_PROFILE_COMPLETE",
                "schema_version": 1,
                "kind": "llm_task_opportunity_profile",
                "snapshot_sha256": snapshot_sha256,
                "qualified_session_id": session_id,
                "profile_set_id": digest(
                    {
                        "snapshot_sha256": snapshot_sha256,
                        "qualified_session_id": session_id,
                        "profiles": profiles,
                    }
                ),
                "profiles": profiles,
                "model": "fake-profile-model",
            }
            result.update(state.get("task_profile_result_overrides", {}))
            delete_source_fixture = state.get("delete_source_fixture")
            if delete_source_fixture:
                source_fixture = Path(delete_source_fixture)
                source_state = load(source_fixture, {})
                source_state["sessions"] = [
                    session
                    for session in source_state.get("sessions", [])
                    if (
                        f"{source_state.get('source')}:{session.get('native_session_id')}"
                        != session_id
                    )
                ]
                save(source_fixture, source_state)
            save(Path(args.result), result)
            emit({"ok": True, **result})
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
        if state.get("require_task_profile_context"):
            if not args.task_profile_receipt:
                fail("task-profile-receipt-required", args.adapter_id)
            receipt = load(Path(args.task_profile_receipt), {})
            if (
                receipt.get("kind") != "task_profile_receipt"
                or receipt.get("snapshot_sha256") != digest(snapshot)
                or not any(
                    isinstance(profile, dict)
                    and profile.get("reuse_value") == "reusable-procedure"
                    for profile in receipt.get("profiles", [])
                )
            ):
                fail("task-profile-receipt-invalid", args.adapter_id)
            if args.task_profile_id:
                selected = [
                    profile
                    for profile in receipt.get("profiles", [])
                    if isinstance(profile, dict)
                    and profile.get("profile_id") == args.task_profile_id
                    and profile.get("reuse_value") == "reusable-procedure"
                ]
                if len(selected) != 1:
                    fail("task-profile-receipt-invalid", args.adapter_id)
            elif state.get("require_task_profile_id"):
                fail("task-profile-id-required", args.adapter_id)
            if state.get("require_task_occurrence_context"):
                if not args.task_occurrence_context:
                    fail("task-occurrence-context-required", args.adapter_id)
                occurrence_context = load(
                    Path(args.task_occurrence_context), {}
                )
                if (
                    occurrence_context.get("selected_profile_id")
                    != args.task_profile_id
                    or occurrence_context.get("review_contract")
                    != "profile-catalog-review-occurrence-v1"
                    or not isinstance(
                        occurrence_context.get("prior_overlaps"), list
                    )
                ):
                    fail("task-occurrence-context-invalid", args.adapter_id)
        if state.get("reject_task_profile_receipt") and args.task_profile_receipt:
            fail("unexpected-task-profile-receipt", args.adapter_id)
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
        if args.task_profile_id:
            occurrence_context = (
                load(Path(args.task_occurrence_context), {})
                if args.task_occurrence_context
                else {}
            )
            audit_receipt = load(Path(args.task_profile_receipt), {})
            selected = [
                profile
                for profile in audit_receipt.get("profiles", [])
                if isinstance(profile, dict)
                and profile.get("profile_id") == args.task_profile_id
            ]
            if len(selected) != 1:
                fail("task-profile-receipt-invalid", args.adapter_id)
            artifact = result["artifact"]
            operation = (
                artifact.get("operation") if isinstance(artifact, dict) else None
            )
            occurrence_relation = state.get(
                "occurrence_relation_by_profile", {}
            ).get(
                args.task_profile_id,
                state.get("occurrence_relation", "new-occurrence"),
            )
            derived_load_trace = []
            snapshot_events = snapshot.get("events", [])
            event_positions = {
                event.get("source_event_id"): index
                for index, event in enumerate(snapshot_events)
                if isinstance(event, dict)
            }
            selected_event_ids = selected[0]["source_event_ids"]
            first = event_positions[selected_event_ids[0]]
            last = event_positions[selected_event_ids[-1]]
            for event in snapshot_events[first : last + 1]:
                if (
                    not isinstance(event, dict)
                    or event.get("kind") != "tool_call"
                    or str(event.get("tool_name", "")).casefold() != "skill"
                ):
                    continue
                raw_input = event.get("text")
                try:
                    parsed_input = (
                        json.loads(raw_input)
                        if isinstance(raw_input, str)
                        else raw_input
                    )
                except json.JSONDecodeError:
                    parsed_input = None
                invoked_name = None
                if isinstance(parsed_input, dict):
                    invoked_name = parsed_input.get(
                        "skill",
                        parsed_input.get(
                            "skillName", parsed_input.get("name")
                        ),
                    )
                if not isinstance(invoked_name, str):
                    continue
                invoked_name = invoked_name.strip().lstrip("/")
                derived_load_trace.append(
                    {
                        "source_event_id": event["source_event_id"],
                        "invoked_name": invoked_name,
                        "catalog_skill_name": invoked_name,
                        "event_sha256": digest(event),
                    }
                )
            catalog_outcome = state.get("catalog_audit_outcome")
            if catalog_outcome is None:
                catalog_outcome = (
                    "no-covering-skill"
                    if operation == "create"
                    else "missed-skill"
                    if operation == "patch"
                    else "wrong-or-incomplete-skill"
                    if operation == "support_file"
                    else "correct-skill"
                    if derived_load_trace
                    else "missed-skill"
                )
            if (
                catalog_outcome == "missed-skill"
                and artifact is None
                and occurrence_relation != "boundary-conflict"
            ):
                artifact = {
                    "operation": "patch",
                    "skill_name": "fixture-skill",
                    "skill_markdown": (
                        "---\nname: fixture-skill\n"
                        "description: Use for reusable fixture tasks.\n---\n"
                        "# Fixture skill\n"
                    ),
                    "support_files": [],
                }
                operation = "patch"
                result["terminal_route"] = "skill"
                result["artifact"] = artifact
                result["evidence_event_ids"] = list(
                    selected[0]["source_event_ids"]
                )
            catalog_skill_name = state.get("catalog_skill_name")
            if (
                catalog_skill_name is None
                and catalog_outcome != "no-covering-skill"
            ):
                catalog_skill_name = (
                    artifact.get("skill_name")
                    if isinstance(artifact, dict)
                    else "fixture-skill"
                )
            catalog_names = sorted(
                set(
                    state.get("catalog_skill_names", [])
                    + (
                        [catalog_skill_name]
                        if isinstance(catalog_skill_name, str)
                        else []
                    )
                )
            )
            load_trace = state.get("skill_load_trace")
            if load_trace is None:
                load_trace = derived_load_trace
            result["catalog_audit"] = {
                "outcome": catalog_outcome,
                "skill_name": catalog_skill_name,
                "candidate_group_id": state.get("catalog_candidate_group_id"),
                "reviewer_contract": "profile-catalog-audit-v1",
                "catalog_sha256": digest(catalog_names),
                "catalog_skill_names": catalog_names,
                "tombstones_sha256": digest(
                    state.get("catalog_tombstones", [])
                ),
                "skill_load_trace": load_trace,
                "skill_load_trace_sha256": digest(load_trace),
                "candidate_groups": occurrence_context.get(
                    "candidate_groups", []
                ),
            }
            prior_ids = state.get(
                "occurrence_prior_ids_by_profile", {}
            ).get(args.task_profile_id, state.get("occurrence_prior_ids", []))
            result["occurrence_boundary"] = {
                "relation": occurrence_relation,
                "prior_canonical_occurrence_ids": prior_ids,
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
    run.add_argument("--mode", choices=("review", "profile"), default="review")
    run.add_argument("--task-profile-receipt")
    run.add_argument("--task-profile-executor")
    run.add_argument("--task-profile-id")
    run.add_argument("--task-occurrence-context")
    run.add_argument("--task-profile-correction")
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
        fixture_state = load(Path(args.fixture), {})
        capabilities = fixture_state.get("capabilities", capabilities)
        identity = {
            "ok": True,
            "protocol": protocol,
            "version": 1,
            "adapter_id": args.adapter_id,
            "capabilities": capabilities,
        }
        overrides = fixture_state.get("contract_identity_overrides", {})
        if not isinstance(overrides, dict):
            fail("fixture-invalid", "contract_identity_overrides")
        identity.update(overrides)
        emit(identity)
    fixture = Path(args.fixture)
    if args.role == "session-source":
        source_command(args, fixture)
    if args.role == "review-executor":
        executor_command(args, fixture)
    publisher_command(args, fixture)


if __name__ == "__main__":
    main()
