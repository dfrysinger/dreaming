#!/usr/bin/env bash
# Deterministic aggregate evaluation-input claim ledger checks.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
# shellcheck source=lib-test-work.sh
source "$SCRIPT_DIR/lib-test-work.sh"
prune_test_work "$TEST_ROOT" "evaluation-input-claims" 2
TMP="$(mktemp -d "$TEST_ROOT/evaluation-input-claims.XXXXXX")"
cleanup() {
  local rc=$?
  trap - EXIT
  finish_test_work "$rc" "$TMP" "evaluation-input-claims"
  exit "$rc"
}
trap cleanup EXIT

TZ=America/Denver python3 - "$SCRIPT_DIR/evaluation_input_claims.py" \
  "$SCRIPT_DIR/skill-evaluation.py" "$TMP" <<'PY'
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

module_path, evaluator_path, root_arg = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("evaluation_input_claims", module_path)
claims = importlib.util.module_from_spec(spec)
spec.loader.exec_module(claims)
sys.modules["evaluation_input_claims"] = claims
evaluator_spec = importlib.util.spec_from_file_location(
    "skill_evaluation", evaluator_path
)
evaluator = importlib.util.module_from_spec(evaluator_spec)
evaluator_spec.loader.exec_module(evaluator)
root = root_arg.resolve()
passes = 0


def passed(label):
    global passes
    passes += 1
    print(f"PASS  {label}")


def sha(label):
    return claims.identity({"fixture": label})


def use_state(name, epoch=1_787_148_000):
    os.environ["SKILLS_STATE_DIR"] = str(root / name / "state")
    os.environ["DREAMING_NOW_EPOCH"] = str(epoch)


def reserve(run_id, *, author="model-author", a="model-review-a", b="model-review-b"):
    return claims.reserve_claim(
        skill_path=str(root / "skill"),
        skill_key="fixture-skill-key",
        candidate_id=sha("candidate"),
        owner_run_id=run_id,
        author_model=author,
        reviewer_a_model=a,
        reviewer_b_model=b,
    )


def operation(kind, model, packet_id, tokens, elapsed=100):
    return {
        "operation": kind,
        "model": model,
        "observed_model": model,
        "packet_id": packet_id,
        "operation_id": sha(f"{kind}:{model}:{packet_id}:{tokens}:{elapsed}"),
        "usage": {
            "normalized_tokens": tokens,
            "input_tokens": tokens - 1,
            "output_tokens": 1,
        },
        "billing": {
            "status": "unavailable",
            "cost_usd": None,
            "provider": "copilot",
            "unavailable_reason": "provider_telemetry_unavailable",
            "native_line_item_id": None,
            "native_event_sha256": None,
            "native_event_size": None,
        },
        "elapsed_ms": elapsed,
    }


def dispatch(claim_id, slot, model, packet, *, manifest=None, validation=None,
             tokens=18_000, timeout=600, lineage=None):
    return claims.prepare_dispatch(
        claim_id=claim_id,
        skill_path=str(root / "skill"),
        skill_key="fixture-skill-key",
        candidate_id=sha("candidate"),
        slot_name=slot,
        model=model,
        packet_id=packet,
        manifest_sha256=manifest,
        validation_receipt_sha256=validation,
        requested_token_budget=tokens,
        requested_timeout_seconds=timeout,
        lineage_receipt_sha256s=lineage,
    )


def rejected_initial(run_id):
    claim = reserve(run_id)
    claim_id = claim["claim_id"]
    manifest = sha(f"{run_id}-initial-manifest")
    validation = sha(f"{run_id}-initial-validation")
    packet = sha(f"{run_id}-author-packet")
    dispatch(claim_id, "author", "model-author", packet)
    claims.complete_slot(
        claim_id=claim_id,
        slot_name="author",
        operation=operation("author", "model-author", packet, 10),
        manifest_sha256=manifest,
    )
    receipts = []
    for slot, model, decision in (
        ("review_a", "model-review-a", "reject"),
        ("review_b", "model-review-b", "accept"),
    ):
        packet = sha(f"{run_id}-{slot}-packet")
        dispatch(
            claim_id,
            slot,
            model,
            packet,
            manifest=manifest,
            validation=validation,
        )
        receipt = sha(f"{run_id}-{slot}-receipt")
        claims.complete_slot(
            claim_id=claim_id,
            slot_name=slot,
            operation=operation("review", model, packet, 10),
            manifest_sha256=manifest,
            review_receipt_sha256=receipt,
            decision=decision,
        )
        receipts.append(receipt)
    return claim_id, manifest, validation, sorted(receipts)


def complete_repair(run_id):
    claim_id, manifest, validation, receipts = rejected_initial(run_id)
    packet = sha(f"{run_id}-repair-packet")
    dispatch(
        claim_id,
        "repair",
        "model-author",
        packet,
        manifest=manifest,
        validation=validation,
        lineage=receipts,
    )
    repaired = sha(f"{run_id}-repaired-manifest")
    repair_operation = operation("repair", "model-author", packet, 10)
    repair_operation.update(
        {
            "initial_manifest_sha256": manifest,
            "validation_receipt_sha256": validation,
            "review_set_id": claims.inspect_claim(claim_id)["review_set_id"],
            "original_review_receipt_sha256s": receipts,
        }
    )
    claims.complete_slot(
        claim_id=claim_id,
        slot_name="repair",
        operation=repair_operation,
        manifest_sha256=repaired,
    )
    return claim_id, repaired


if hasattr(time, "tzset"):
    time.tzset()

use_state("empty-bootstrap")
empty_ledger = claims.ledger_path()
empty_ledger.parent.mkdir(parents=True)
empty_ledger.write_bytes(b"")
empty_ledger.chmod(0o644)
connection = claims.connect()
connection.close()
connection = claims.connect()
assert connection.execute("PRAGMA user_version").fetchone()[0] == claims.SCHEMA_VERSION
connection.close()
assert empty_ledger.stat().st_size > 0
assert empty_ledger.stat().st_mode & 0o777 == 0o600
empty_ledger.chmod(0o644)
connection = claims.connect()
connection.close()
assert empty_ledger.stat().st_mode & 0o777 == 0o600

use_state("header-bootstrap")
header_ledger = claims.ledger_path()
header_ledger.parent.mkdir(parents=True)
with sqlite3.connect(header_ledger) as connection:
    connection.execute("PRAGMA application_id=1179403602")
header_ledger.chmod(0o644)
assert header_ledger.stat().st_size > 0
connection = claims.connect()
assert connection.execute(
    "SELECT owner_integration_status FROM schema_metadata WHERE singleton=1"
).fetchone()[0] == claims.OWNER_INTEGRATION_STATUS
connection.close()
assert header_ledger.stat().st_mode & 0o777 == 0o600

use_state("concurrent-bootstrap")
barrier = threading.Barrier(8)


def first_opener(_):
    barrier.wait()
    connection = claims.connect()
    try:
        return connection.execute(
            "SELECT schema_version, owner_integration_status "
            "FROM schema_metadata WHERE singleton=1"
        ).fetchone()
    finally:
        connection.close()


with ThreadPoolExecutor(max_workers=8) as executor:
    opened = list(executor.map(first_opener, range(8)))
assert all(
    tuple(row) == (claims.SCHEMA_VERSION, claims.OWNER_INTEGRATION_STATUS)
    for row in opened
)

use_state("incompatible-bootstrap")
incompatible = claims.ledger_path()
incompatible.parent.mkdir(parents=True)
with sqlite3.connect(incompatible) as connection:
    connection.execute("CREATE TABLE foreign_owner(value TEXT)")
try:
    claims.connect()
except claims.ClaimLedgerError as error:
    assert "incompatible nonempty schema" in str(error)
else:
    raise AssertionError("an incompatible nonempty ledger was overwritten")
with sqlite3.connect(incompatible) as connection:
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE name='foreign_owner'"
    ).fetchone()

use_state("owner-status")
connection = claims.connect()
connection.close()
with sqlite3.connect(claims.ledger_path()) as connection:
    connection.execute(
        "UPDATE schema_metadata SET owner_integration_status='forged'"
    )
try:
    claims.connect()
except claims.ClaimLedgerError as error:
    assert "owner status" in str(error)
else:
    raise AssertionError("an unsupported owner integration status was accepted")
use_state("legacy-schema")
legacy = claims.ledger_path()
legacy.parent.mkdir(parents=True)
with sqlite3.connect(legacy) as connection:
    connection.execute(
        "CREATE TABLE schema_metadata "
        "(singleton INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, "
        "owner_integration_status TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO schema_metadata VALUES (1, 1, ?)",
        (claims.OWNER_INTEGRATION_STATUS,),
    )
    connection.execute("PRAGMA user_version=1")
try:
    claims.connect()
except claims.ClaimLedgerError as error:
    assert "schema version" in str(error)
else:
    raise AssertionError("legacy claim schema was silently migrated")
passed("schema bootstrap is fail closed and version one is explicitly refused")

use_state("capacity")
first = reserve("run-capacity-1", a="model-z", b="model-a")
try:
    reserve("run-capacity-1")
except claims.ClaimLedgerError as error:
    assert "owner run" in str(error)
else:
    raise AssertionError("a second claim for one owner run was accepted")
for index in range(2, 5):
    reserve(f"run-capacity-{index}")
try:
    reserve("run-capacity-5")
except claims.ClaimLedgerError as error:
    assert "four claims" in str(error)
else:
    raise AssertionError("a fifth same-day claim was accepted")
with sqlite3.connect(claims.ledger_path()) as connection:
    assert connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 4
assert first["models"]["reviewer_a"] == "model-a"
assert first["models"]["reviewer_b"] == "model-z"
assert first["timezone"] == {"name": "MDT", "offset_minutes": -360}
assert [slot["slot_name"] for slot in first["slots"]] == [
    "author", "review_a", "review_b", "repair", "rereview_a", "rereview_b"
]
assert [slot["operation_kind"] for slot in first["slots"]] == [
    "author", "review", "review", "repair", "rereview", "rereview"
]
passed("four daily claims, one per run, canonical reviewers, and six fixed slots")

captured_day = first["local_day"]
os.environ["DREAMING_NOW_EPOCH"] = str(1_787_320_800)
assert claims.inspect_claim(first["claim_id"])["local_day"] == captured_day
passed("claim local day and timezone capture remain stable")

use_state("identities")
try:
    reserve("run-duplicate-models", a="model-author")
except claims.ClaimLedgerError as error:
    assert "three distinct" in str(error)
else:
    raise AssertionError("duplicate claim model identities were accepted")
passed("three exact non-default model identities are mandatory")

use_state("wrong-model")
wrong = reserve("run-wrong-model")
try:
    dispatch(wrong["claim_id"], "author", "substituted-model", sha("wrong-packet"))
except claims.ClaimLedgerError as error:
    assert "model identity mismatch" in str(error)
else:
    raise AssertionError("wrong model dispatched")
wrong_state = claims.inspect_claim(wrong["claim_id"])
assert wrong_state["status"] == "invalid"
assert wrong_state["slots"][0]["status"] == "unstarted"
assert wrong_state["terminal_publication"]["readiness_state"] == "invalid"
assert wrong_state["terminal_publication"]["readiness_reason"] == wrong_state[
    "terminal_reason"
]
assert not any(event["event_type"] == "slot_dispatching" for event in wrong_state["events"])
passed("pre-call model substitution invalidates without spending a slot")

use_state("wrong-order")
ordered = reserve("run-wrong-order")
try:
    dispatch(
        ordered["claim_id"], "review_a", "model-review-a", sha("review-packet"),
        manifest=sha("manifest"), validation=sha("validation"),
    )
except claims.ClaimLedgerError as error:
    assert "slot order" in str(error)
else:
    raise AssertionError("review dispatched before author")
assert claims.inspect_claim(ordered["claim_id"])["slots"][1]["status"] == "unstarted"
passed("slot order refusal is pre-call and leaves the requested slot unstarted")

use_state("recovery")
recovery = reserve("run-recovery")
dispatch(recovery["claim_id"], "author", "model-author", sha("recovery-packet"))
try:
    dispatch(recovery["claim_id"], "author", "model-author", sha("recovery-packet"))
except claims.ClaimLedgerError as error:
    assert "unknown-spent" in str(error)
else:
    raise AssertionError("dispatching crash was retried")
recovered = claims.inspect_claim(recovery["claim_id"])
assert recovered["slots"][0]["status"] == "failed"
assert recovered["slots"][0]["usage_status"] == "unavailable"
assert recovered["aggregate_actual"]["normalized_tokens"] is None
assert recovered["status"] == "invalid"
passed("dispatching recovery is failed unknown-spent and nonretryable")

use_state("failure")
failed = reserve("run-failure")
dispatch(failed["claim_id"], "author", "model-author", sha("failure-packet"))
claims.fail_dispatched_slot(
    failed["claim_id"], "author", "trusted_operation_timeout"
)
try:
    dispatch(failed["claim_id"], "author", "model-author", sha("failure-packet"))
except claims.ClaimLedgerError as error:
    assert "invalid" in str(error)
else:
    raise AssertionError("failed slot retried")
failed_state = claims.inspect_claim(failed["claim_id"])
assert failed_state["slots"][0]["normalized_tokens"] is None
assert failed_state["slots"][0]["billing_status"] is None
assert failed_state["terminal_reason"] == "budget_unknown"
passed("started timeout records unavailable usage rather than zero and blocks continuation")

use_state("bounds", epoch=1_787_148_000)
bounded = reserve("run-bounds")
author_packet = sha("bounds-author-packet")
author_budget = dispatch(
    bounded["claim_id"], "author", "model-author", author_packet,
    tokens=200_000, timeout=2_000,
)
assert author_budget["token_budget"] == 112_000
assert author_budget["timeout_seconds"] == 1_500
manifest = sha("bounds-manifest")
claims.complete_slot(
    claim_id=bounded["claim_id"],
    slot_name="author",
    operation=operation("author", "model-author", author_packet, 100_000),
    manifest_sha256=manifest,
)
os.environ["DREAMING_NOW_EPOCH"] = str(1_787_148_600)
review_packet = sha("bounds-review-packet")
review_budget = dispatch(
    bounded["claim_id"], "review_a", "model-review-a", review_packet,
    manifest=manifest, validation=sha("bounds-validation"),
    tokens=18_000, timeout=1_000,
)
assert review_budget["token_budget"] == 12_000
assert review_budget["timeout_seconds"] == 900
passed("trusted dispatch receives the lower token and elapsed aggregate bounds")

use_state("overspend")
overspent = reserve("run-overspend")
overspent_packet = sha("overspent-packet")
dispatch(overspent["claim_id"], "author", "model-author", overspent_packet)
try:
    claims.complete_slot(
        claim_id=overspent["claim_id"],
        slot_name="author",
        operation=operation(
            "author", "model-author", overspent_packet, 18_001
        ),
        manifest_sha256=sha("overspent-manifest"),
    )
except claims.ClaimLedgerError as error:
    assert "effective token budget" in str(error)
else:
    raise AssertionError("normalized tokens above the per-call cap were accepted")
overspent_state = claims.inspect_claim(overspent["claim_id"])
assert overspent_state["status"] == "invalid"
assert overspent_state["slots"][0]["status"] == "failed"
assert overspent_state["slots"][0]["usage_status"] == "unavailable"
assert (
    overspent_state["slots"][0]["failure_reason"]
    == "effective_token_budget_exceeded"
)
assert not any(
    event["event_type"] == "slot_completed"
    for event in overspent_state["events"]
)
passed("per-call token cap spends the started slot with unknown usage")

use_state("elapsed-over-cap")
elapsed_claim = reserve("run-elapsed-over-cap")
elapsed_packet = sha("elapsed-over-cap-packet")
dispatch(
    elapsed_claim["claim_id"], "author", "model-author", elapsed_packet,
    tokens=100, timeout=1,
)
try:
    claims.complete_slot(
        claim_id=elapsed_claim["claim_id"],
        slot_name="author",
        operation=operation(
            "author", "model-author", elapsed_packet, 1, elapsed=1_001
        ),
        manifest_sha256=sha("elapsed-over-cap-manifest"),
    )
except claims.ClaimLedgerError as error:
    assert "effective timeout" in str(error)
else:
    raise AssertionError("operation elapsed_ms above its timeout was accepted")
elapsed_state = claims.inspect_claim(elapsed_claim["claim_id"])
assert elapsed_state["slots"][0]["status"] == "failed"
assert elapsed_state["slots"][0]["usage_status"] == "unavailable"
assert (
    elapsed_state["slots"][0]["failure_reason"]
    == "effective_timeout_exceeded"
)
assert elapsed_state["aggregate_actual"]["normalized_tokens"] is None
passed("per-call elapsed cap spends the started slot with unknown usage")

use_state("seventh-slot")
seventh = reserve("run-seventh-slot")
try:
    dispatch(seventh["claim_id"], "seventh", "model-author", sha("seventh"))
except claims.ClaimLedgerError as error:
    assert "not one of the fixed six slots" in str(error)
else:
    raise AssertionError("a seventh slot was accepted")
seventh_state = claims.inspect_claim(seventh["claim_id"])
assert seventh_state["status"] == "open"
assert all(slot["status"] == "unstarted" for slot in seventh_state["slots"])
passed("a fresh open claim specifically refuses a seventh fixed slot")

use_state("manifest-mismatch")
mismatch = reserve("run-manifest-mismatch")
mismatch_packet = sha("mismatch-author-packet")
dispatch(mismatch["claim_id"], "author", "model-author", mismatch_packet)
claims.complete_slot(
    claim_id=mismatch["claim_id"],
    slot_name="author",
    operation=operation("author", "model-author", mismatch_packet, 100),
    manifest_sha256=sha("initial-manifest"),
)
try:
    dispatch(
        mismatch["claim_id"], "review_a", "model-review-a",
        sha("mismatch-review-packet"), manifest=sha("other-manifest"),
        validation=sha("mismatch-validation"),
    )
except claims.ClaimLedgerError as error:
    assert "differs from the claim" in str(error)
else:
    raise AssertionError("review of another manifest dispatched")
assert claims.inspect_claim(mismatch["claim_id"])["slots"][1]["status"] == "unstarted"
passed("review against another manifest invalidates before dispatch")

use_state("completion-manifest-mismatch")
completion_mismatch = reserve("run-completion-manifest-mismatch")
completion_author_packet = sha("completion-mismatch-author-packet")
completion_manifest = sha("completion-mismatch-manifest")
completion_validation = sha("completion-mismatch-validation")
dispatch(
    completion_mismatch["claim_id"],
    "author",
    "model-author",
    completion_author_packet,
)
claims.complete_slot(
    claim_id=completion_mismatch["claim_id"],
    slot_name="author",
    operation=operation(
        "author", "model-author", completion_author_packet, 10
    ),
    manifest_sha256=completion_manifest,
)
completion_review_packet = sha("completion-mismatch-review-packet")
dispatch(
    completion_mismatch["claim_id"],
    "review_a",
    "model-review-a",
    completion_review_packet,
    manifest=completion_manifest,
    validation=completion_validation,
)
try:
    claims.complete_slot(
        claim_id=completion_mismatch["claim_id"],
        slot_name="review_a",
        operation=operation(
            "review", "model-review-a", completion_review_packet, 10
        ),
        manifest_sha256=sha("completion-mismatch-wrong-manifest"),
        review_receipt_sha256=None,
        decision="malformed",
    )
except claims.ClaimLedgerError as error:
    assert str(error) == "review completion differs from dispatched manifest"
else:
    raise AssertionError("review completion manifest mismatch was accepted")
completion_mismatch_state = claims.inspect_claim(
    completion_mismatch["claim_id"]
)
failed_review = completion_mismatch_state["slots"][1]
assert completion_mismatch_state["status"] == "invalid"
assert completion_mismatch_state["terminal_reason"] == "budget_unknown"
assert failed_review["status"] == "failed"
assert failed_review["usage_status"] == "unavailable"
assert failed_review["failure_reason"] == "review_manifest_invalid"
failed_event = next(
    event
    for event in completion_mismatch_state["events"]
    if event["event_type"] == "slot_failed_unknown_spent"
)
assert failed_event["details"] == {
    "failure_reason": "review_manifest_invalid",
    "usage_status": "unavailable",
}
passed("review manifest failure survives malformed trailing completion fields")

use_state("validation-divergence")
validation_claim = reserve("run-validation-divergence")
validation_claim_id = validation_claim["claim_id"]
validation_manifest = sha("validation-divergence-manifest")
validation_author_packet = sha("validation-divergence-author-packet")
dispatch(
    validation_claim_id,
    "author",
    "model-author",
    validation_author_packet,
)
claims.complete_slot(
    claim_id=validation_claim_id,
    slot_name="author",
    operation=operation(
        "author", "model-author", validation_author_packet, 100
    ),
    manifest_sha256=validation_manifest,
)
validation_one = sha("validation-divergence-one")
validation_review_packet = sha("validation-divergence-review-a-packet")
dispatch(
    validation_claim_id,
    "review_a",
    "model-review-a",
    validation_review_packet,
    manifest=validation_manifest,
    validation=validation_one,
)
validation_review_receipt = sha("validation-divergence-review-a-receipt")
claims.complete_slot(
    claim_id=validation_claim_id,
    slot_name="review_a",
    operation=operation(
        "review", "model-review-a", validation_review_packet, 100
    ),
    manifest_sha256=validation_manifest,
    review_receipt_sha256=validation_review_receipt,
    decision="accept",
)
try:
    dispatch(
        validation_claim_id,
        "review_b",
        "model-review-b",
        sha("validation-divergence-review-b-packet"),
        manifest=validation_manifest,
        validation=sha("validation-divergence-two"),
    )
except claims.ClaimLedgerError as error:
    assert "one validation receipt" in str(error)
else:
    raise AssertionError("initial reviews accepted divergent validation receipts")
validation_state = claims.inspect_claim(validation_claim_id)
assert validation_state["status"] == "invalid"
assert validation_state["slots"][2]["status"] == "unstarted"
assert validation_state["terminal_publication"]["validation_receipt_sha256"] == (
    validation_one
)
assert validation_state["terminal_publication"]["review_receipt_sha256s"] == [
    validation_review_receipt
]
passed("divergent review validation refuses before dispatch and still closes")

use_state("repair-ready")
repair_claim = reserve("run-repair-ready")
repair_claim_id = repair_claim["claim_id"]
initial_manifest = sha("repair-initial-manifest")
initial_validation = sha("repair-initial-validation")
author_packet = sha("repair-author-packet")
dispatch(repair_claim_id, "author", "model-author", author_packet)
claims.complete_slot(
    claim_id=repair_claim_id,
    slot_name="author",
    operation=operation("author", "model-author", author_packet, 100),
    manifest_sha256=initial_manifest,
)
initial_receipts = []
for slot, model, decision in (
    ("review_a", "model-review-a", "reject"),
    ("review_b", "model-review-b", "accept"),
):
    packet = sha(f"repair-{slot}-packet")
    dispatch(
        repair_claim_id,
        slot,
        model,
        packet,
        manifest=initial_manifest,
        validation=initial_validation,
    )
    receipt = sha(f"repair-{slot}-receipt")
    claims.complete_slot(
        claim_id=repair_claim_id,
        slot_name=slot,
        operation=operation("review", model, packet, 100),
        manifest_sha256=initial_manifest,
        review_receipt_sha256=receipt,
        decision=decision,
    )
    initial_receipts.append(receipt)
assert claims.inspect_claim(repair_claim_id)["status"] == "open"
repair_packet = sha("repair-packet")
dispatch(
    repair_claim_id,
    "repair",
    "model-author",
    repair_packet,
    manifest=initial_manifest,
    validation=initial_validation,
    lineage=initial_receipts,
)
repaired_manifest = sha("repaired-manifest")
repair_operation = operation(
    "repair", "model-author", repair_packet, 100
)
repair_operation.update(
    {
        "initial_manifest_sha256": initial_manifest,
        "validation_receipt_sha256": initial_validation,
        "review_set_id": claims.inspect_claim(repair_claim_id)["review_set_id"],
        "original_review_receipt_sha256s": sorted(initial_receipts),
    }
)
claims.complete_slot(
    claim_id=repair_claim_id,
    slot_name="repair",
    operation=repair_operation,
    manifest_sha256=repaired_manifest,
)
rereview_receipts = []
repaired_validation = sha("repaired-validation")
for slot, model in (
    ("rereview_a", "model-review-a"),
    ("rereview_b", "model-review-b"),
):
    packet = sha(f"repair-{slot}-packet")
    dispatch(
        repair_claim_id,
        slot,
        model,
        packet,
        manifest=repaired_manifest,
        validation=repaired_validation,
    )
    receipt = sha(f"repair-{slot}-receipt")
    claims.complete_slot(
        claim_id=repair_claim_id,
        slot_name=slot,
        operation=operation("review", model, packet, 100),
        manifest_sha256=repaired_manifest,
        review_receipt_sha256=receipt,
        decision="accept",
    )
    rereview_receipts.append(receipt)
repair_facts = claims.assert_ready(
    repair_claim_id,
    skill_path=str(root / "skill"),
    skill_key="fixture-skill-key",
    candidate_id=sha("candidate"),
    manifest_sha256=repaired_manifest,
    validation_receipt_sha256=repaired_validation,
    review_receipt_sha256s=rereview_receipts,
)
assert repair_facts["review_set_id"] == claims.inspect_claim(
    repair_claim_id
)["review_set_id"]
try:
    claims.assert_ready(
        repair_claim_id,
        skill_path=str(root / "skill"),
        skill_key="fixture-skill-key",
        candidate_id=sha("candidate"),
        manifest_sha256=repaired_manifest,
        validation_receipt_sha256=repaired_validation,
        review_receipt_sha256s=initial_receipts,
    )
except claims.ClaimLedgerError as error:
    assert "differ from the claim ledger" in str(error)
else:
    raise AssertionError("initial review receipts authorized repaired readiness")
repair_state = claims.inspect_claim(repair_claim_id)
assert repair_state["aggregate_actual"]["started_operations"] == 6
assert repair_state["aggregate_actual"]["normalized_tokens"] == 600
passed("one repair and two re-reviews consume exactly six lineage-bound slots")

use_state("repair-substitution")
sub_claim, sub_manifest, sub_validation, sub_receipts = rejected_initial(
    "run-repair-substitution"
)
try:
    dispatch(
        sub_claim,
        "repair",
        "substituted-author",
        sha("substituted-repair-packet"),
        manifest=sub_manifest,
        validation=sub_validation,
        lineage=sub_receipts,
    )
except claims.ClaimLedgerError as error:
    assert "author identity unavailable" in str(error)
else:
    raise AssertionError("substituted repair author dispatched")
sub_state = claims.inspect_claim(sub_claim)
assert sub_state["status"] == "invalid"
assert sub_state["terminal_reason"] == "author_identity_unavailable"
assert sub_state["slots"][3]["status"] == "unstarted"
assert sub_state["aggregate_actual"]["started_operations"] == 3
passed("repair author substitution invalidates before spending the repair slot")

use_state("repair-lineage-mismatch")
lineage_claim, lineage_manifest, lineage_validation, lineage_receipts = (
    rejected_initial("run-repair-lineage-mismatch")
)
try:
    dispatch(
        lineage_claim,
        "repair",
        "model-author",
        sha("lineage-mismatch-packet"),
        manifest=lineage_manifest,
        validation=lineage_validation,
        lineage=[lineage_receipts[0], sha("foreign-review-receipt")],
    )
except claims.ClaimLedgerError as error:
    assert "lineage differs from the claim" in str(error)
else:
    raise AssertionError("foreign repair receipt lineage dispatched")
lineage_state = claims.inspect_claim(lineage_claim)
assert lineage_state["status"] == "invalid"
assert lineage_state["slots"][3]["status"] == "unstarted"
use_state("repair-manifest-mismatch")
wrong_manifest_claim, wrong_manifest, wrong_validation, wrong_receipts = (
    rejected_initial("run-repair-manifest-mismatch")
)
try:
    dispatch(
        wrong_manifest_claim,
        "repair",
        "model-author",
        sha("repair-manifest-mismatch-packet"),
        manifest=sha("foreign-initial-manifest"),
        validation=wrong_validation,
        lineage=wrong_receipts,
    )
except claims.ClaimLedgerError as error:
    assert "differs from the claim" in str(error)
else:
    raise AssertionError("foreign initial manifest dispatched for repair")
assert claims.inspect_claim(wrong_manifest_claim)["slots"][3]["status"] == "unstarted"
use_state("repair-review-set-mismatch")
wrong_set_claim, wrong_set_manifest, wrong_set_validation, wrong_set_receipts = (
    rejected_initial("run-repair-review-set-mismatch")
)
with sqlite3.connect(claims.ledger_path()) as connection:
    connection.execute(
        "UPDATE claims SET review_set_id=? WHERE claim_id=?",
        (sha("foreign-review-set"), wrong_set_claim),
    )
try:
    dispatch(
        wrong_set_claim,
        "repair",
        "model-author",
        sha("repair-review-set-mismatch-packet"),
        manifest=wrong_set_manifest,
        validation=wrong_set_validation,
        lineage=wrong_set_receipts,
    )
except claims.ClaimLedgerError as error:
    assert "review set identity is invalid" in str(error)
else:
    raise AssertionError("foreign review set dispatched for repair")
passed("wrong repair manifest, review set, or receipt lineage refuses before dispatch")

use_state("rereview-substitution")
rereview_sub_claim, rereview_sub_manifest = complete_repair(
    "run-rereview-substitution"
)
try:
    dispatch(
        rereview_sub_claim,
        "rereview_a",
        "substituted-reviewer",
        sha("substituted-rereview-packet"),
        manifest=rereview_sub_manifest,
        validation=sha("substituted-rereview-validation"),
    )
except claims.ClaimLedgerError as error:
    assert "reviewer identity unavailable" in str(error)
else:
    raise AssertionError("substituted re-reviewer dispatched")
rereview_sub_state = claims.inspect_claim(rereview_sub_claim)
assert rereview_sub_state["status"] == "invalid"
assert rereview_sub_state["terminal_reason"] == "reviewer_identity_unavailable"
assert rereview_sub_state["slots"][4]["status"] == "unstarted"
assert rereview_sub_state["aggregate_actual"]["started_operations"] == 4
passed("re-reviewer substitution invalidates before spending the re-review slot")

use_state("repair-insufficient")
ii_claim, ii_manifest, ii_validation, ii_receipts = rejected_initial(
    "run-repair-insufficient"
)
ii_packet = sha("repair-insufficient-packet")
dispatch(
    ii_claim,
    "repair",
    "model-author",
    ii_packet,
    manifest=ii_manifest,
    validation=ii_validation,
    lineage=ii_receipts,
)
ii_operation = operation("repair", "model-author", ii_packet, 25)
ii_operation.update(
    {
        "initial_manifest_sha256": ii_manifest,
        "validation_receipt_sha256": ii_validation,
        "review_set_id": claims.inspect_claim(ii_claim)["review_set_id"],
        "original_review_receipt_sha256s": ii_receipts,
    }
)
claims.complete_slot(
    claim_id=ii_claim,
    slot_name="repair",
    operation=ii_operation,
    manifest_sha256=None,
    terminal_reason="repair_insufficient_information",
)
ii_state = claims.inspect_claim(ii_claim)
assert ii_state["status"] == "completed"
assert ii_state["terminal_reason"] == "repair_insufficient_information"
assert ii_state["slots"][3]["status"] == "completed"
assert ii_state["slots"][3]["normalized_tokens"] == 25
assert ii_state["terminal_publication"]["manifest_sha256"] is None
assert ii_state["terminal_publication"]["validation_receipt_sha256"] is None
assert ii_state["terminal_publication"]["review_receipt_sha256s"] == []
passed("repair insufficient information is terminal with actual usage")

for label, slot, failure in (
    ("repair-timeout", "repair", "trusted_operation_timeout"),
    ("repair-malformed", "repair", "trusted_operation_malformed"),
):
    use_state(label)
    claim_id, initial, validation, receipts = rejected_initial(f"run-{label}")
    packet = sha(f"{label}-packet")
    dispatch(
        claim_id,
        "repair",
        "model-author",
        packet,
        manifest=initial,
        validation=validation,
        lineage=receipts,
    )
    claims.fail_dispatched_slot(claim_id, "repair", failure)
    state = claims.inspect_claim(claim_id)
    assert state["slots"][3]["status"] == "failed"
    assert state["slots"][3]["failure_reason"] == failure
    assert state["aggregate_actual"]["normalized_tokens"] is None

use_state("repair-token-failure")
token_claim, token_initial, token_validation, token_receipts = rejected_initial(
    "run-repair-token-failure"
)
token_packet = sha("repair-token-failure-packet")
dispatch(
    token_claim,
    "repair",
    "model-author",
    token_packet,
    manifest=token_initial,
    validation=token_validation,
    lineage=token_receipts,
    tokens=1,
)
token_operation = operation("repair", "model-author", token_packet, 2)
token_operation.update(
    {
        "initial_manifest_sha256": token_initial,
        "validation_receipt_sha256": token_validation,
        "review_set_id": claims.inspect_claim(token_claim)["review_set_id"],
        "original_review_receipt_sha256s": token_receipts,
    }
)
try:
    claims.complete_slot(
        claim_id=token_claim,
        slot_name="repair",
        operation=token_operation,
        manifest_sha256=sha("repair-token-failure-manifest"),
    )
except claims.ClaimLedgerError as error:
    assert "effective token budget" in str(error)
else:
    raise AssertionError("repair token overspend completed")
assert claims.inspect_claim(token_claim)["slots"][3]["status"] == "failed"

for label, failure in (
    ("rereview-timeout", "trusted_operation_timeout"),
    ("rereview-malformed", "trusted_operation_malformed"),
):
    use_state(label)
    claim_id, repaired = complete_repair(f"run-{label}")
    packet = sha(f"{label}-packet")
    dispatch(
        claim_id,
        "rereview_a",
        "model-review-a",
        packet,
        manifest=repaired,
        validation=sha(f"{label}-validation"),
    )
    claims.fail_dispatched_slot(claim_id, "rereview_a", failure)
    state = claims.inspect_claim(claim_id)
    assert state["slots"][4]["status"] == "failed"
    assert state["slots"][4]["failure_reason"] == failure
passed("repair and re-review token, timeout, and malformed failures spend their slots")

use_state("insufficient")
insufficient = reserve("run-insufficient")
insufficient_packet = sha("insufficient-packet")
dispatch(
    insufficient["claim_id"], "author", "model-author", insufficient_packet
)
claims.complete_slot(
    claim_id=insufficient["claim_id"],
    slot_name="author",
    operation=operation("author", "model-author", insufficient_packet, 50),
    manifest_sha256=None,
    terminal_reason="insufficient_information",
)
insufficient_state = claims.inspect_claim(insufficient["claim_id"])
assert insufficient_state["status"] == "completed"
assert insufficient_state["terminal_reason"] == "insufficient_information"
assert insufficient_state["slots"][0]["usage_status"] == "available"
assert insufficient_state["terminal_publication"]["readiness_state"] == (
    "insufficient_information"
)
assert insufficient_state["terminal_publication"]["readiness_reason"] == (
    "insufficient_information"
)
passed("insufficient information is terminal with actual author usage")

use_state("ready")
ready = reserve("run-ready")
ready_claim = ready["claim_id"]
ready_manifest = sha("ready-manifest")
ready_validation = sha("ready-validation")
ready_author_packet = sha("ready-author-packet")
dispatch(ready_claim, "author", "model-author", ready_author_packet)
claims.complete_slot(
    claim_id=ready_claim,
    slot_name="author",
    operation=operation("author", "model-author", ready_author_packet, 100),
    manifest_sha256=ready_manifest,
)
review_receipts = []
for slot, model in (("review_a", "model-review-a"), ("review_b", "model-review-b")):
    packet = sha(f"ready-{slot}-packet")
    dispatch(
        ready_claim, slot, model, packet, manifest=ready_manifest,
        validation=ready_validation,
    )
    receipt = sha(f"ready-{slot}-receipt")
    claims.complete_slot(
        claim_id=ready_claim,
        slot_name=slot,
        operation=operation("review", model, packet, 75),
        manifest_sha256=ready_manifest,
        review_receipt_sha256=receipt,
        decision="accept",
    )
    review_receipts.append(receipt)
try:
    dispatch(
        ready_claim,
        "repair",
        "model-author",
        sha("unneeded-repair-packet"),
        manifest=ready_manifest,
        validation=ready_validation,
        lineage=review_receipts,
    )
except claims.ClaimLedgerError as error:
    assert "at least one rejected" in str(error)
else:
    raise AssertionError("repair dispatched after two accepting reviews")
future_state = claims.inspect_claim(ready_claim)
assert future_state["status"] == "open"
assert all(
    slot["status"] == "unstarted"
    for slot in future_state["slots"][3:]
)
assert not any(
    event["event_type"] == "slot_dispatching"
    and event["slot_name"] in {"repair", "rereview_a", "rereview_b"}
    for event in future_state["events"]
)
passed("two accepting reviews preserve initial readiness and refuse repair")
facts = claims.assert_ready(
    ready_claim,
    skill_path=str(root / "skill"),
    skill_key="fixture-skill-key",
    candidate_id=sha("candidate"),
    manifest_sha256=ready_manifest,
    validation_receipt_sha256=ready_validation,
    review_receipt_sha256s=review_receipts,
)
assert facts["review_set_id"] == claims.review_set_identity(
    ready_claim, sha("candidate"), ready_manifest, "model-author",
    ["model-review-b", "model-review-a"],
)
claims.complete_claim_ready(
    ready_claim,
    skill_path=str(root / "skill"),
    skill_key="fixture-skill-key",
    candidate_id=sha("candidate"),
    manifest_sha256=ready_manifest,
    validation_receipt_sha256=ready_validation,
    review_receipt_sha256s=review_receipts,
)
ready_state = claims.inspect_claim(ready_claim)
assert ready_state["terminal_reason"] == "ready"
assert ready_state["terminal_publication"] == {
    "readiness_state": "ready",
    "readiness_reason": "validated_and_reviewed",
    "manifest_sha256": ready_manifest,
    "validation_receipt_sha256": ready_validation,
    "review_receipt_sha256s": sorted(review_receipts),
    "transition_id": None,
    "acknowledged_epoch": None,
}
assert claims.pending_terminal_publications()[0]["claim_id"] == ready_claim
terminal_transition = {
    "schema_version": 1,
    "kind": "evaluation_input_readiness_transition",
    "claim_id": ready_claim,
    "state": "ready",
    "reason": "validated_and_reviewed",
    "input_manifest_sha256": ready_manifest,
    "validation_receipt_sha256": ready_validation,
    "review_receipt_sha256s": sorted(review_receipts),
}
terminal_transition["transition_id"] = claims.identity(terminal_transition)
wrong_transition = dict(terminal_transition)
wrong_transition["claim_id"] = sha("wrong-terminal-claim")
wrong_transition["transition_id"] = claims.identity(
    {key: value for key, value in wrong_transition.items() if key != "transition_id"}
)
try:
    claims.acknowledge_terminal_publication(ready_claim, wrong_transition)
except claims.ClaimLedgerError as error:
    assert "facts differ" in str(error)
else:
    raise AssertionError("terminal publication acknowledged another claim")
claims.acknowledge_terminal_publication(ready_claim, terminal_transition)
claims.acknowledge_terminal_publication(ready_claim, terminal_transition)
acknowledged = claims.inspect_claim(ready_claim)["terminal_publication"]
assert acknowledged["transition_id"] == terminal_transition["transition_id"]
assert acknowledged["acknowledged_epoch"] is not None
assert claims.pending_terminal_publications() == []
different_transition = dict(terminal_transition)
different_transition["created_at"] = "later"
different_transition["transition_id"] = claims.identity(
    {
        key: value
        for key, value in different_transition.items()
        if key != "transition_id"
    }
)
try:
    claims.acknowledge_terminal_publication(
        ready_claim, different_transition
    )
except claims.ClaimLedgerError as error:
    assert "differs" in str(error)
else:
    raise AssertionError("terminal publication accepted another transition")
passed("author and ordered reviews bind one canonical accepting review set")
passed("terminal readiness publication is retained and exactly acknowledged")

use_state("scheduled-owner-fence")
owner_fence = {
    "owner_pid": 4242,
    "owner_process_identity": "pid:4242:start:fixture",
    "owner_process_group_identity": "pgid:4242:leader:fixture",
    "owner_boot_identity": sha("boot-fixture"),
    "owner_config_sha256": sha("owner-config"),
}
os.environ["DREAMING_ORCHESTRATED"] = "1"
try:
    reserve("orchestrated-owner-without-fence")
except claims.ClaimLedgerError as error:
    assert "complete scheduled owner fence" in str(error)
else:
    raise AssertionError("orchestrated claim silently fell back to manual mode")
del os.environ["DREAMING_ORCHESTRATED"]
try:
    claims.reserve_claim(
        skill_path=str(root / "skill"),
        skill_key="fixture-skill-key",
        candidate_id=sha("candidate"),
        owner_run_id="scheduled-owner-without-token",
        author_model="scheduled-author",
        reviewer_a_model="scheduled-review-a",
        reviewer_b_model="scheduled-review-b",
        owner_fence=owner_fence,
    )
except claims.ClaimLedgerError as error:
    assert "writer lease token" in str(error)
else:
    raise AssertionError("scheduled owner claim did not require a lease token")
os.environ["SKILLS_LOCK_TOKEN"] = "00000000-0000-4000-8000-000000000001"
scheduled = claims.reserve_claim(
    skill_path=str(root / "skill"),
    skill_key="fixture-skill-key",
    candidate_id=sha("candidate"),
    owner_run_id="scheduled-owner-run",
    author_model="scheduled-author",
    reviewer_a_model="scheduled-review-a",
    reviewer_b_model="scheduled-review-b",
    owner_fence=owner_fence,
)
fence = scheduled["lock_fence"]
assert fence["owner_mode"] == "scheduled"
assert fence["scheduled_owner_integration"] == claims.OWNER_INTEGRATION_STATUS
assert fence["owner_pid"] == 4242
assert fence["owner_process_identity"] == owner_fence["owner_process_identity"]
assert fence["owner_process_group_identity"] == owner_fence[
    "owner_process_group_identity"
]
assert fence["owner_boot_identity"] == owner_fence["owner_boot_identity"]
assert fence["owner_config_sha256"] == owner_fence["owner_config_sha256"]
assert fence["token_sha256"] == "sha256:" + __import__("hashlib").sha256(
    os.environ["SKILLS_LOCK_TOKEN"].encode()
).hexdigest()
del os.environ["SKILLS_LOCK_TOKEN"]
open_claims = claims.open_scheduled_claims()
assert len(open_claims) == 1
assert open_claims[0]["claim_id"] == scheduled["claim_id"]
assert open_claims[0]["dispatching_slots"] == []
before_wrong_owner = claims.inspect_claim(scheduled["claim_id"])
try:
    claims.recover_open_scheduled_claim(
        scheduled["claim_id"],
        expected_owner_run_id="different-owner-run",
        expected_owner_pid=owner_fence["owner_pid"],
        expected_owner_process_identity=owner_fence[
            "owner_process_identity"
        ],
        expected_owner_process_group_identity=owner_fence[
            "owner_process_group_identity"
        ],
        expected_owner_boot_identity=owner_fence["owner_boot_identity"],
    )
except claims.ClaimLedgerError as error:
    assert "facts differ" in str(error)
else:
    raise AssertionError("open-claim recovery accepted different owner facts")
assert claims.inspect_claim(scheduled["claim_id"]) == before_wrong_owner
recovered = claims.recover_open_scheduled_claim(
    scheduled["claim_id"],
    expected_owner_run_id="scheduled-owner-run",
    expected_owner_pid=owner_fence["owner_pid"],
    expected_owner_process_identity=owner_fence["owner_process_identity"],
    expected_owner_process_group_identity=owner_fence[
        "owner_process_group_identity"
    ],
    expected_owner_boot_identity=owner_fence["owner_boot_identity"],
)
assert recovered["terminal_reason"] == "owner_interrupted"
assert recovered["dispatching_slots"] == []
assert claims.open_scheduled_claims() == []
scheduled_inspection = claims.inspect_claim(scheduled["claim_id"])
assert scheduled_inspection["status"] == "invalid"
assert scheduled_inspection["terminal_publication"]["readiness_reason"] == (
    "owner_interrupted"
)
passed("scheduled claims bind writer, process, boot, and configuration fences")
passed("open scheduled claims require exact owner facts before terminalization")

use_state("scheduled-owner-dispatch-recovery")
os.environ["SKILLS_LOCK_TOKEN"] = "00000000-0000-4000-8000-000000000003"
scheduled_dispatch = claims.reserve_claim(
    skill_path=str(root / "skill"),
    skill_key="fixture-skill-key",
    candidate_id=sha("candidate"),
    owner_run_id="scheduled-dispatch-run",
    author_model="scheduled-author",
    reviewer_a_model="scheduled-review-a",
    reviewer_b_model="scheduled-review-b",
    owner_fence=owner_fence,
)
del os.environ["SKILLS_LOCK_TOKEN"]
claims.prepare_dispatch(
    claim_id=scheduled_dispatch["claim_id"],
    skill_path=str(root / "skill"),
    skill_key="fixture-skill-key",
    candidate_id=sha("candidate"),
    slot_name="author",
    model="scheduled-author",
    packet_id=sha("scheduled-author-packet"),
    manifest_sha256=None,
    validation_receipt_sha256=None,
    requested_token_budget=100,
    requested_timeout_seconds=10,
    lineage_receipt_sha256s=[],
)
assert claims.open_scheduled_claims()[0]["dispatching_slots"] == ["author"]
dispatch_recovery = claims.recover_open_scheduled_claim(
    scheduled_dispatch["claim_id"],
    expected_owner_run_id="scheduled-dispatch-run",
    expected_owner_pid=owner_fence["owner_pid"],
    expected_owner_process_identity=owner_fence["owner_process_identity"],
    expected_owner_process_group_identity=owner_fence[
        "owner_process_group_identity"
    ],
    expected_owner_boot_identity=owner_fence["owner_boot_identity"],
)
assert dispatch_recovery["dispatching_slots"] == ["author"]
dispatch_inspection = claims.inspect_claim(scheduled_dispatch["claim_id"])
assert dispatch_inspection["slots"][0]["status"] == "failed"
assert dispatch_inspection["slots"][0]["usage_status"] == "unavailable"
assert dispatch_inspection["slots"][0]["failure_reason"] == "owner_interrupted"
passed("interrupted dispatch is retained as unknown-spent before claim closure")

use_state("empty-owner-recovery")
with mock.patch.object(
    evaluator, "host_boot_identity", side_effect=AssertionError("not called")
):
    assert evaluator.reconcile_open_owner_claims(
        authority_check=lambda: None,
        owner_wait_seconds=0,
        group_wait_seconds=0,
    ) == {"recovered_claims": [], "recovery_required": []}
assert claims.pending_terminal_publications() == []
passed("owner recovery is a read-only no-op before the claim ledger exists")

use_state("owner-liveness-recovery")
os.environ["SKILLS_LOCK_TOKEN"] = "00000000-0000-4000-8000-000000000004"
live_claim = claims.reserve_claim(
    skill_path=str(root / "skill"),
    skill_key="fixture-skill-key",
    candidate_id=sha("candidate"),
    owner_run_id="live-owner-run",
    author_model="scheduled-author",
    reviewer_a_model="scheduled-review-a",
    reviewer_b_model="scheduled-review-b",
    owner_fence=owner_fence,
)
del os.environ["SKILLS_LOCK_TOKEN"]
with (
    mock.patch.object(
        evaluator, "host_boot_identity", return_value=sha("boot-fixture")
    ),
    mock.patch.object(
        evaluator,
        "wait_for_recorded_process_exit",
        return_value={"status": "present"},
    ),
):
    live_result = evaluator.reconcile_open_owner_claims(
        authority_check=lambda: None,
        owner_wait_seconds=0,
        group_wait_seconds=0,
    )
assert live_result["recovered_claims"] == []
assert live_result["recovery_required"] == [
    {"claim_id": live_claim["claim_id"], "reason": "prior_owner_live"}
]
assert claims.inspect_claim(live_claim["claim_id"])["status"] == "open"
marker = evaluator.persist_evaluation_input_recovery_required(
    live_result["recovery_required"],
    authority_check=lambda: None,
)
assert marker["record_sha256"] == evaluator.identity_with(
    "record_sha256",
    {
        key: value
        for key, value in marker.items()
        if key != "record_sha256"
    },
)
assert evaluator.evaluation_input_recovery_path().is_file()
with (
    mock.patch.object(
        evaluator, "host_boot_identity", return_value=sha("boot-fixture")
    ),
    mock.patch.object(
        evaluator,
        "wait_for_recorded_process_exit",
        return_value={"status": "reused"},
    ),
    mock.patch.object(
        evaluator,
        "inspect_recorded_process_group",
        return_value={"status": "reused", "pgid": 4242},
    ),
    mock.patch.object(
        evaluator,
        "resolve_input_readiness",
        return_value={
            "state": "drafting",
            "candidate_id": sha("candidate"),
            "skill_key": "fixture-skill-key",
        },
    ),
):
    reused_result = evaluator.reconcile_open_owner_claims(
        authority_check=lambda: None,
        owner_wait_seconds=0,
        group_wait_seconds=0,
    )
assert reused_result["recovery_required"] == []
assert reused_result["recovered_claims"][0]["claim_id"] == live_claim["claim_id"]
assert claims.inspect_claim(live_claim["claim_id"])["status"] == "invalid"
assert evaluator.persist_evaluation_input_recovery_required(
    reused_result["recovery_required"],
    authority_check=lambda: None,
) is None
assert not evaluator.evaluation_input_recovery_path().exists()
passed("live owners block only the lane and reused identities are never signaled")
passed("lane-scoped recovery requirements persist until their claim clears")

use_state("owner-unreadable-recovery")
os.environ["SKILLS_LOCK_TOKEN"] = "00000000-0000-4000-8000-000000000005"
unreadable_claim = claims.reserve_claim(
    skill_path=str(root / "skill"),
    skill_key="fixture-skill-key",
    candidate_id=sha("candidate"),
    owner_run_id="unreadable-owner-run",
    author_model="scheduled-author",
    reviewer_a_model="scheduled-review-a",
    reviewer_b_model="scheduled-review-b",
    owner_fence=owner_fence,
)
del os.environ["SKILLS_LOCK_TOKEN"]
with (
    mock.patch.object(
        evaluator, "host_boot_identity", return_value=sha("boot-fixture")
    ),
    mock.patch.object(
        evaluator,
        "wait_for_recorded_process_exit",
        return_value={"status": "unreadable"},
    ),
):
    unreadable_result = evaluator.reconcile_open_owner_claims(
        authority_check=lambda: None,
        owner_wait_seconds=0,
        group_wait_seconds=0,
    )
assert unreadable_result["recovery_required"] == [
    {
        "claim_id": unreadable_claim["claim_id"],
        "reason": "prior_owner_identity_unreadable",
    }
]
assert claims.inspect_claim(unreadable_claim["claim_id"])["status"] == "open"
passed("unreadable same-boot owner identity remains non-mutating")

use_state("boot-identity-unreadable-recovery")
os.environ["SKILLS_LOCK_TOKEN"] = "00000000-0000-4000-8000-000000000006"
boot_unreadable_claim = claims.reserve_claim(
    skill_path=str(root / "skill"),
    skill_key="fixture-skill-key",
    candidate_id=sha("candidate"),
    owner_run_id="boot-unreadable-owner-run",
    author_model="scheduled-author",
    reviewer_a_model="scheduled-review-a",
    reviewer_b_model="scheduled-review-b",
    owner_fence=owner_fence,
)
del os.environ["SKILLS_LOCK_TOKEN"]
with mock.patch.object(
    evaluator,
    "host_boot_identity",
    side_effect=evaluator.EvaluationError("host boot identity is unavailable"),
):
    boot_unreadable_result = evaluator.reconcile_open_owner_claims(
        authority_check=lambda: None,
        owner_wait_seconds=0,
        group_wait_seconds=0,
    )
assert boot_unreadable_result["recovered_claims"] == []
assert boot_unreadable_result["recovery_required"] == [
    {
        "claim_id": boot_unreadable_claim["claim_id"],
        "reason": "host_boot_identity_unreadable",
    }
]
assert claims.inspect_claim(boot_unreadable_claim["claim_id"])["status"] == "open"
boot_unreadable_marker = evaluator.persist_evaluation_input_recovery_required(
    boot_unreadable_result["recovery_required"],
    authority_check=lambda: None,
)
assert boot_unreadable_marker["claims"] == [
    {
        "claim_id": boot_unreadable_claim["claim_id"],
        "reason": "host_boot_identity_unreadable",
    }
]
assert evaluator.evaluation_input_recovery_path().is_file()
passed("unavailable boot identity durably blocks every open claim")

use_state("prior-boot-recovery")
os.environ["SKILLS_LOCK_TOKEN"] = "00000000-0000-4000-8000-000000000007"
prior_boot_claim = claims.reserve_claim(
    skill_path=str(root / "skill"),
    skill_key="fixture-skill-key",
    candidate_id=sha("candidate"),
    owner_run_id="prior-boot-owner-run",
    author_model="scheduled-author",
    reviewer_a_model="scheduled-review-a",
    reviewer_b_model="scheduled-review-b",
    owner_fence=owner_fence,
)
del os.environ["SKILLS_LOCK_TOKEN"]
with (
    mock.patch.object(
        evaluator, "host_boot_identity", return_value=sha("boot-new")
    ),
    mock.patch.object(
        evaluator,
        "inspect_recorded_process_group",
        side_effect=AssertionError("prior-boot group must not be probed"),
    ),
    mock.patch.object(
        evaluator,
        "resolve_input_readiness",
        return_value={
            "state": "review_required",
            "candidate_id": sha("candidate"),
            "skill_key": "fixture-skill-key",
        },
    ),
):
    prior_boot_result = evaluator.reconcile_open_owner_claims(
        authority_check=lambda: None,
        owner_wait_seconds=0,
        group_wait_seconds=0,
    )
assert prior_boot_result["recovery_required"] == []
assert prior_boot_result["recovered_claims"][0]["claim_id"] == (
    prior_boot_claim["claim_id"]
)
assert claims.inspect_claim(prior_boot_claim["claim_id"])["status"] == "invalid"
with (
    mock.patch.object(
        evaluator,
        "inspect_recorded_process_group",
        return_value={"status": "reused", "pgid": 4242},
    ),
    mock.patch.object(evaluator.os, "killpg") as forbidden_signal,
):
    assert evaluator.terminate_recorded_process_group(
        owner_fence["owner_process_group_identity"],
        timeout_seconds=0,
        authority_check=lambda: None,
    )
forbidden_signal.assert_not_called()
passed("prior-boot and reused process groups recover without signaling")

first_boot = (
    "{ sec = 1786472601, usec = 468071 } "
    "Tue Aug 11 12:23:21 2026"
)
second_boot = (
    "{ sec = 1786472601, usec = 468071 } "
    "Wed Aug 12 03:23:21 2026"
)
assert evaluator.boot_identity_from_sysctl(first_boot) == (
    evaluator.boot_identity_from_sysctl(second_boot)
)
try:
    evaluator.boot_identity_from_sysctl("not a boot identity")
except evaluator.EvaluationError as error:
    assert "unavailable" in str(error)
else:
    raise AssertionError("malformed boot identity was accepted")
passed("boot identity ignores timezone-rendered sysctl text")

with (
    mock.patch.object(
        evaluator,
        "inspect_recorded_process",
        return_value={"status": "absent"},
    ),
    mock.patch.object(
        evaluator, "process_group_alive", return_value="present"
    ),
):
    orphan_without_leader = evaluator.inspect_recorded_process_group(
        owner_fence["owner_process_group_identity"]
    )
assert orphan_without_leader["status"] == "unreadable"
with (
    mock.patch.object(
        evaluator,
        "inspect_recorded_process_group",
        return_value=orphan_without_leader,
    ),
    mock.patch.object(evaluator.os, "killpg") as forbidden_orphan_signal,
):
    assert not evaluator.terminate_recorded_process_group(
        owner_fence["owner_process_group_identity"],
        timeout_seconds=0,
        authority_check=lambda: None,
    )
forbidden_orphan_signal.assert_not_called()
passed("leaderless process groups defer rather than risking a reused group")

owned_group = subprocess.Popen(
    ["/bin/sleep", "30"],
    start_new_session=True,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
try:
    owned_group_identity = evaluator.process_group_identity(owned_group.pid)
    with ThreadPoolExecutor(max_workers=1) as executor:
        reaped = executor.submit(owned_group.wait)
        assert evaluator.terminate_recorded_process_group(
            owned_group_identity,
            timeout_seconds=4,
            authority_check=lambda: None,
        )
        assert reaped.result(timeout=5) != 0
    assert evaluator.process_group_alive(owned_group.pid) == "absent"
finally:
    if owned_group.poll() is None:
        os.killpg(owned_group.pid, 9)
        owned_group.wait()
passed("exact owned process groups are terminated and proved absent")

use_state("ready")
ledger = claims.ledger_path()
with sqlite3.connect(ledger) as connection:
    event_id = connection.execute(
        "SELECT MIN(event_id) FROM claim_events"
    ).fetchone()[0]
    for statement in (
        ("UPDATE claim_events SET event_type='forged' WHERE event_id=?", (event_id,)),
        ("DELETE FROM claim_events WHERE event_id=?", (event_id,)),
    ):
        try:
            connection.execute(*statement)
            connection.commit()
        except sqlite3.IntegrityError as error:
            assert "immutable" in str(error)
            connection.rollback()
        else:
            raise AssertionError("immutable claim event was mutated")
passed("claim events are immutable and auditable")

before = claims.inspect_claim(ready_claim)
adapter_fixture = root / "adapter-fixture.py"
adapter_fixture.write_text("# first adapter bytes\n")
adapter_fixture.write_text("# changed adapter bytes\n")
after = claims.inspect_claim(ready_claim)
assert before == after
unknown = subprocess.run(
    [sys.executable, str(evaluator_path), "v2-input-claim-complete"],
    text=True,
    capture_output=True,
    check=False,
)
assert unknown.returncode != 0
assert "invalid choice" in unknown.stderr
unauthorized_reconcile = subprocess.run(
    [sys.executable, str(evaluator_path), "v2-input-owner-reconcile"],
    text=True,
    capture_output=True,
    check=False,
)
assert unauthorized_reconcile.returncode != 0
assert "requires inherited orchestration" in unauthorized_reconcile.stderr
use_state("authorized-reconcile")
reserve("authorized-reconcile-open-claim")
token = "00000000-0000-4000-8000-000000000002"
owner_pid = os.getpid()
owner_identity = " ".join(
    subprocess.run(
        ["/bin/ps", "-o", "lstart=", "-p", str(owner_pid)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
)
lock = subprocess.run(
    [
        sys.executable,
        str(evaluator_path.with_name("daemon-lock.py")),
        "acquire",
        "--mode",
        "process",
        "--owner",
        "evaluation-input-test",
        "--pid",
        str(owner_pid),
        "--process-identity",
        owner_identity,
        "--token",
        token,
    ],
    text=True,
    capture_output=True,
    check=False,
)
assert lock.returncode == 0, lock.stderr
authorized_environment = dict(os.environ)
authorized_environment.update(
    {
        "DREAMING_ORCHESTRATED": "1",
        "SKILLS_LOCK_HELD_BY_PARENT": "1",
        "DREAMING_PARENT_RUN_ID": "authorized-reconcile-run",
        "SKILLS_LOCK_TOKEN": token,
        "SKILLS_LOCK_OWNER_PID": str(owner_pid),
        "SKILLS_LOCK_OWNER_IDENTITY": owner_identity,
    }
)
authorized_reconcile = subprocess.run(
    [sys.executable, str(evaluator_path), "v2-input-owner-reconcile"],
    env=authorized_environment,
    text=True,
    capture_output=True,
    check=False,
)
assert authorized_reconcile.returncode == 0, authorized_reconcile.stderr
assert json.loads(authorized_reconcile.stdout) == {
    "recovered_claims": [],
    "recovery_marker": None,
    "recovery_required": [],
    "status": "reconciled",
    "terminal_publications": [],
}
passed("historical inspection is stable and owner reconciliation requires authority")

assert passes == 39
print(f"PASS  {passes} deterministic evaluation-input claim ledger checks")
PY
