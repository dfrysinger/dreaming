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

module_path, evaluator_path, root_arg = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("evaluation_input_claims", module_path)
claims = importlib.util.module_from_spec(spec)
spec.loader.exec_module(claims)
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
             tokens=18_000, timeout=600):
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
    )


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
assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
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
passed("empty, header-only, repeated, and concurrent bootstrap is fail closed")

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
assert completion_mismatch_state["events"][-1]["details"] == {
    "failure_reason": "review_manifest_invalid",
    "usage_status": "unavailable",
}
passed("review manifest failure survives malformed trailing completion fields")

use_state("unsupported-completion")
unsupported_completion = reserve("run-unsupported-completion")
unsupported_packet = sha("unsupported-completion-packet")
with sqlite3.connect(claims.ledger_path()) as connection:
    connection.execute(
        """
        UPDATE claim_slots
        SET status='dispatching', started_epoch=?,
            effective_token_budget=100, effective_timeout_seconds=10,
            packet_id=?
        WHERE claim_id=? AND slot_name='repair'
        """,
        (
            int(os.environ["DREAMING_NOW_EPOCH"]),
            unsupported_packet,
            unsupported_completion["claim_id"],
        ),
    )
try:
    claims.complete_slot(
        claim_id=unsupported_completion["claim_id"],
        slot_name="repair",
        operation=operation(
            "review", "model-author", unsupported_packet, 1
        ),
        manifest_sha256=sha("unsupported-completion-manifest"),
        review_receipt_sha256=sha("unsupported-completion-receipt"),
        decision="accept",
    )
except claims.ClaimLedgerError as error:
    assert "reserved for a later slice" in str(error)
else:
    raise AssertionError("repair completion was mapped to a review")
unsupported_completion_state = claims.inspect_claim(
    unsupported_completion["claim_id"]
)
assert unsupported_completion_state["slots"][3]["status"] == "failed"
assert (
    unsupported_completion_state["slots"][3]["failure_reason"]
    == "unsupported_operation_kind"
)
passed("defensive completion never maps a future repair slot to review")

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
for slot, model in (
    ("repair", "model-author"),
    ("rereview_a", "model-review-a"),
    ("rereview_b", "model-review-b"),
):
    try:
        dispatch(
            ready_claim,
            slot,
            model,
            sha(f"future-{slot}-packet"),
            manifest=ready_manifest,
            validation=ready_validation,
        )
    except claims.ClaimLedgerError as error:
        assert "reserved for a later slice" in str(error)
    else:
        raise AssertionError(f"future slot {slot} dispatched")
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
assert [
    event["details"]["reason"]
    for event in future_state["events"]
    if event["event_type"] == "pre_call_refused"
    and event["slot_name"] in {"repair", "rereview_a", "rereview_b"}
] == ["unsupported_future_slot"] * 3
passed("future repair and rereview slots refuse without spending or invalidating")
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
assert claims.inspect_claim(ready_claim)["terminal_reason"] == "ready"
passed("author and ordered reviews bind one canonical accepting review set")

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
passed("historical inspection ignores live adapter bytes and no completion CLI exists")

assert passes == 20
print(f"PASS  {passes} deterministic evaluation-input claim ledger checks")
PY
