#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" python3 - <<'PY'
import json
import tempfile
from pathlib import Path

from task_occurrence import (
    TaskOccurrenceError,
    build_correction_attempt,
    build_resolution,
    load_exact,
    persist,
    persist_correction_attempt,
)


def sha(character: str) -> str:
    return "sha256:" + character * 64


profile = {
    "profile_id": sha("1"),
    "task_key": sha("2"),
    "source_event_ids": ["event-1", "event-2"],
    "goal_event_id": "event-1",
    "occurred_at": "2026-08-01T00:00:00Z",
}
receipt = {
    "schema_version": 2,
    "receipt_sha256": sha("3"),
    "snapshot_sha256": sha("4"),
    "source_revision": sha("5"),
    "qualified_session_id": "copilot:continuous",
}
executor_identity = {"sha256": sha("6")}

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    resolutions = root / "resolutions"
    index = root / "index.json"
    corrections = root / "corrections"

    first = build_resolution(
        profile=profile,
        receipt=receipt,
        relation="new-occurrence",
        review_contract="review-v1",
        review_executor="copilot",
        review_executor_identity=executor_identity,
        decision_at="2026-08-02T00:00:00Z",
    )
    persist(resolutions, index, first)
    assert load_exact(resolutions, index, profile["task_key"]) == first

    alias_profile = {
        **profile,
        "profile_id": sha("7"),
        "task_key": sha("8"),
        "source_event_ids": ["event-1"],
    }
    alias = build_resolution(
        profile=alias_profile,
        receipt=receipt,
        relation="same-occurrence",
        prior_occurrence_ids=[first["canonical_occurrence_id"]],
        overlap_resolution_ids=[first["resolution_sha256"]],
        review_contract="review-v1",
        review_executor="copilot",
        review_executor_identity=executor_identity,
        decision_at="2026-08-02T00:01:00Z",
    )
    persist(resolutions, index, alias)
    assert alias["canonical_occurrence_id"] == first["canonical_occurrence_id"]

    conflict_profile = {
        **profile,
        "profile_id": sha("9"),
        "task_key": sha("a"),
    }
    conflict = build_resolution(
        profile=conflict_profile,
        receipt=receipt,
        relation="boundary-conflict",
        prior_occurrence_ids=[first["canonical_occurrence_id"]],
        overlap_resolution_ids=[first["resolution_sha256"]],
        review_contract="review-v1",
        review_executor="copilot",
        review_executor_identity=executor_identity,
        decision_at="2026-08-02T00:02:00Z",
    )
    persist(resolutions, index, conflict)
    attempt = build_correction_attempt(
        qualified_session_id=receipt["qualified_session_id"],
        source_revision=receipt["source_revision"],
        profile_receipt_sha256=receipt["receipt_sha256"],
        conflict_resolution_sha256s=[conflict["resolution_sha256"]],
        correction_contract="correction-v1",
        profile_executor="copilot",
        profile_executor_identity=executor_identity,
        started_at="2026-08-02T00:03:00Z",
        terminal_status="replacement-profiled",
        replacement_profile_receipt_sha256=sha("b"),
    )
    persist_correction_attempt(corrections, attempt)
    corrected_receipt = {**receipt, "receipt_sha256": sha("b")}
    corrected_profile = {**conflict_profile, "profile_id": sha("c")}
    corrected = build_resolution(
        profile=corrected_profile,
        receipt=corrected_receipt,
        relation="new-occurrence",
        review_contract="review-v1",
        review_executor="copilot",
        review_executor_identity=executor_identity,
        decision_at="2026-08-02T00:04:00Z",
        correction_attempt_sha256=attempt["attempt_sha256"],
        supersedes_resolution_sha256=conflict["resolution_sha256"],
    )
    persist(resolutions, index, corrected)
    assert load_exact(resolutions, index, conflict_profile["task_key"]) == corrected

    stored = resolutions / (
        corrected["resolution_sha256"].removeprefix("sha256:") + ".json"
    )
    value = json.loads(stored.read_text())
    value["occurred_at"] = "2026-08-03T00:00:00Z"
    stored.chmod(0o600)
    stored.write_text(json.dumps(value))
    stored.chmod(0o400)
    try:
        load_exact(resolutions, index, conflict_profile["task_key"])
    except TaskOccurrenceError as error:
        assert error.reason == "resolution-identity"
    else:
        raise AssertionError("tampered immutable resolution was accepted")

print("PASS  immutable occurrence resolution, alias, correction, and tamper refusal")
PY
