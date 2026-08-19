#!/usr/bin/env python3
"""Durable aggregate claim accounting for trusted evaluation-input authoring."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1
OWNER_INTEGRATION_STATUS = "scheduled_owner_integration_pending"
MAX_CLAIMS_PER_LOCAL_DAY = 4
MAX_SLOTS = 6
MAX_NORMALIZED_TOKENS = 112_000
MAX_ELAPSED_MS = 25 * 60 * 1000
SHA256_PREFIX = "sha256:"
SLOT_DEFINITIONS = (
    ("author", "author", "author"),
    ("review_a", "review", "reviewer_a"),
    ("review_b", "review", "reviewer_b"),
    ("repair", "repair", "author"),
    ("rereview_a", "rereview", "reviewer_a"),
    ("rereview_b", "rereview", "reviewer_b"),
)


class ClaimLedgerError(RuntimeError):
    """A fail-closed claim ledger refusal."""


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def identity(value: Any) -> str:
    return f"{SHA256_PREFIX}{hashlib.sha256(canonical(value)).hexdigest()}"


def require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ClaimLedgerError(f"{field} must be non-empty canonical text")
    return value


def require_sha256(value: Any, field: str) -> str:
    text = require_text(value, field)
    if (
        not text.startswith(SHA256_PREFIX)
        or len(text) != len(SHA256_PREFIX) + 64
        or any(character not in "0123456789abcdef" for character in text[7:])
    ):
        raise ClaimLedgerError(f"{field} must be a sha256 identity")
    return text


def require_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ClaimLedgerError(f"{field} must be a positive integer")
    return value


def require_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ClaimLedgerError(f"{field} must be a non-negative integer")
    return value


def now_epoch() -> int:
    return int(
        os.environ.get(
            "DREAMING_NOW_EPOCH",
            os.environ.get("SKILLS_NOW_EPOCH", time.time()),
        )
    )


def local_time_facts(epoch: int) -> tuple[str, str, int]:
    observed = datetime.fromtimestamp(epoch).astimezone()
    offset = observed.utcoffset()
    if offset is None:
        raise ClaimLedgerError("host timezone offset is unavailable")
    timezone_name = observed.tzname()
    if not timezone_name:
        raise ClaimLedgerError("host timezone name is unavailable")
    return (
        observed.date().isoformat(),
        timezone_name,
        int(offset.total_seconds() // 60),
    )


def path_has_symlink(path: Path) -> bool:
    current = Path(os.path.abspath(path))
    while True:
        if current.is_symlink():
            return True
        if current.parent == current:
            return False
        current = current.parent


def ledger_path() -> Path:
    state = Path(
        os.environ.get(
            "SKILLS_STATE_DIR", Path.home() / ".copilot/skill-state"
        )
    )
    return state / "dreaming" / "evaluation-input-claims.sqlite3"


def lock_fence_digest() -> str | None:
    token = os.environ.get("SKILLS_LOCK_TOKEN")
    if not token:
        return None
    return f"{SHA256_PREFIX}{hashlib.sha256(token.encode()).hexdigest()}"


SCHEMA = """
CREATE TABLE schema_metadata (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  schema_version INTEGER NOT NULL,
  owner_integration_status TEXT NOT NULL
);

CREATE TABLE claims (
  claim_id TEXT PRIMARY KEY,
  local_day TEXT NOT NULL,
  created_epoch INTEGER NOT NULL,
  terminal_epoch INTEGER,
  timezone_name TEXT NOT NULL,
  timezone_offset_minutes INTEGER NOT NULL,
  candidate_id TEXT NOT NULL,
  skill_key TEXT NOT NULL,
  skill_path TEXT NOT NULL,
  owner_run_id TEXT NOT NULL UNIQUE,
  lock_fence_token_sha256 TEXT,
  author_model TEXT NOT NULL,
  reviewer_a_model TEXT NOT NULL,
  reviewer_b_model TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open', 'completed', 'invalid')),
  terminal_reason TEXT,
  initial_manifest_sha256 TEXT,
  review_set_id TEXT,
  max_slots INTEGER NOT NULL CHECK (max_slots = 6),
  max_normalized_tokens INTEGER NOT NULL,
  max_elapsed_ms INTEGER NOT NULL
);

CREATE INDEX claims_local_day_idx ON claims(local_day);

CREATE TABLE claim_slots (
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  slot_index INTEGER NOT NULL CHECK (slot_index BETWEEN 0 AND 5),
  slot_name TEXT NOT NULL,
  operation_kind TEXT NOT NULL,
  expected_model TEXT NOT NULL,
  status TEXT NOT NULL
    CHECK (status IN ('unstarted', 'dispatching', 'completed', 'failed')),
  started_epoch INTEGER,
  terminal_epoch INTEGER,
  requested_token_budget INTEGER,
  effective_token_budget INTEGER,
  requested_timeout_seconds INTEGER,
  effective_timeout_seconds INTEGER,
  usage_status TEXT NOT NULL
    CHECK (usage_status IN ('pending', 'available', 'unavailable')),
  normalized_tokens INTEGER,
  input_tokens INTEGER,
  output_tokens INTEGER,
  elapsed_ms INTEGER,
  billing_status TEXT,
  billing_cost_usd REAL,
  billing_provider TEXT,
  billing_unavailable_reason TEXT,
  billing_native_line_item_id TEXT,
  billing_native_event_sha256 TEXT,
  billing_native_event_size INTEGER,
  operation_id TEXT,
  observed_model TEXT,
  packet_id TEXT,
  manifest_sha256 TEXT,
  validation_receipt_sha256 TEXT,
  review_receipt_sha256 TEXT,
  review_set_id TEXT,
  decision TEXT,
  failure_reason TEXT,
  PRIMARY KEY (claim_id, slot_index),
  UNIQUE (claim_id, slot_name)
);

CREATE TABLE claim_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  slot_name TEXT,
  event_type TEXT NOT NULL,
  created_epoch INTEGER NOT NULL,
  details_json TEXT NOT NULL
);

CREATE TRIGGER claim_events_immutable_update
BEFORE UPDATE ON claim_events
BEGIN
  SELECT RAISE(ABORT, 'claim events are immutable');
END;

CREATE TRIGGER claim_events_immutable_delete
BEFORE DELETE ON claim_events
BEGIN
  SELECT RAISE(ABORT, 'claim events are immutable');
END;

CREATE TRIGGER claims_never_deleted
BEFORE DELETE ON claims
BEGIN
  SELECT RAISE(ABORT, 'claims are never deleted or refunded');
END;

CREATE TRIGGER claim_slots_never_deleted
BEFORE DELETE ON claim_slots
BEGIN
  SELECT RAISE(ABORT, 'claim slots are never deleted or refunded');
END;
"""


def _configure(connection: sqlite3.Connection) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA fullfsync=ON")
    connection.execute("PRAGMA checkpoint_fullfsync=ON")


def _execute_schema(connection: sqlite3.Connection) -> None:
    statement = ""
    for line in SCHEMA.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ClaimLedgerError("claim ledger schema definition is incomplete")


def _validate_schema(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    metadata = connection.execute(
        """
        SELECT schema_version, owner_integration_status
        FROM schema_metadata
        WHERE singleton=1
        """
    ).fetchall()
    if (
        version != SCHEMA_VERSION
        or len(metadata) != 1
        or metadata[0]["schema_version"] != SCHEMA_VERSION
        or metadata[0]["owner_integration_status"]
        != OWNER_INTEGRATION_STATUS
    ):
        raise ClaimLedgerError(
            "claim ledger schema version or owner status is unsupported"
        )


def _bootstrap_schema(connection: sqlite3.Connection) -> bool:
    connection.execute("BEGIN IMMEDIATE")
    try:
        objects = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        installed = False
        if not objects and version == 0:
            _execute_schema(connection)
            connection.execute(
                """
                INSERT INTO schema_metadata
                  (singleton, schema_version, owner_integration_status)
                VALUES (1, ?, ?)
                """,
                (SCHEMA_VERSION, OWNER_INTEGRATION_STATUS),
            )
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            installed = True
        elif not objects or version == 0:
            raise ClaimLedgerError(
                "claim ledger contains an incompatible nonempty schema"
            )
        _validate_schema(connection)
        connection.commit()
        return installed
    except BaseException:
        connection.rollback()
        raise


def connect(*, create: bool = True) -> sqlite3.Connection:
    path = ledger_path()
    if create:
        if path_has_symlink(path.parent):
            raise ClaimLedgerError("claim ledger state root must not use symlinks")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise ClaimLedgerError("claim ledger must not be a symlink")
        existed = path.exists()
        if existed:
            if not path.is_file():
                raise ClaimLedgerError("claim ledger must be a regular file")
            os.chmod(path, 0o600)
        connection = sqlite3.connect(path, timeout=5, isolation_level=None)
        if not existed:
            os.chmod(path, 0o600)
        _configure(connection)
        try:
            installed = _bootstrap_schema(connection)
            connection.execute("PRAGMA journal_mode=WAL")
            _validate_schema(connection)
        except (ClaimLedgerError, sqlite3.Error) as error:
            connection.close()
            if isinstance(error, ClaimLedgerError):
                raise
            raise ClaimLedgerError(
                f"claim ledger schema validation failed: {error}"
            ) from error
        if installed:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    else:
        if not path.is_file() or path.is_symlink():
            raise ClaimLedgerError("claim ledger is unavailable")
        connection = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, timeout=5, isolation_level=None
        )
        _configure(connection)
    try:
        _validate_schema(connection)
    except (ClaimLedgerError, sqlite3.Error) as error:
        connection.close()
        if isinstance(error, ClaimLedgerError):
            raise
        raise ClaimLedgerError(
            f"claim ledger schema validation failed: {error}"
        ) from error
    return connection


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def append_event(
    connection: sqlite3.Connection,
    claim_id: str,
    event_type: str,
    details: dict[str, Any],
    *,
    slot_name: str | None = None,
    created_epoch: int | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO claim_events
          (claim_id, slot_name, event_type, created_epoch, details_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            claim_id,
            slot_name,
            event_type,
            now_epoch() if created_epoch is None else created_epoch,
            canonical(details).decode(),
        ),
    )


def review_set_identity(
    claim_id: str,
    candidate_id: str,
    initial_manifest_sha256: str,
    author_model: str,
    reviewer_models: list[str] | tuple[str, ...],
) -> str:
    reviewers = sorted(require_text(item, "reviewer model") for item in reviewer_models)
    if len(reviewers) != 2 or len(set(reviewers)) != 2:
        raise ClaimLedgerError("review set requires two distinct reviewer models")
    return identity(
        {
            "kind": "evaluation_input_review_set",
            "claim_id": require_sha256(claim_id, "claim_id"),
            "candidate_id": require_sha256(candidate_id, "candidate_id"),
            "initial_manifest_sha256": require_sha256(
                initial_manifest_sha256, "initial manifest"
            ),
            "author_model": require_text(author_model, "author model"),
            "reviewer_models": reviewers,
        }
    )


def reserve_claim(
    *,
    skill_path: str,
    skill_key: str,
    candidate_id: str,
    owner_run_id: str,
    author_model: str,
    reviewer_a_model: str,
    reviewer_b_model: str,
) -> dict[str, Any]:
    skill_path = str(Path(require_text(skill_path, "skill path")).resolve())
    skill_key = require_text(skill_key, "skill key")
    candidate_id = require_sha256(candidate_id, "candidate_id")
    owner_run_id = require_text(owner_run_id, "owner run ID")
    author_model = require_text(author_model, "author model")
    supplied_reviewers = [
        require_text(reviewer_a_model, "reviewer A model"),
        require_text(reviewer_b_model, "reviewer B model"),
    ]
    if author_model == "default" or any(model == "default" for model in supplied_reviewers):
        raise ClaimLedgerError("claim models must be explicit non-default identities")
    if len({author_model, *supplied_reviewers}) != 3:
        raise ClaimLedgerError("claim requires three distinct exact model identities")
    reviewer_a_model, reviewer_b_model = sorted(supplied_reviewers)
    created = now_epoch()
    local_day, timezone_name, timezone_offset = local_time_facts(created)
    claim_id = identity(
        {
            "kind": "evaluation_input_authoring_claim",
            "schema_version": SCHEMA_VERSION,
            "created_epoch": created,
            "local_day": local_day,
            "skill_path": skill_path,
            "skill_key": skill_key,
            "candidate_id": candidate_id,
            "owner_run_id": owner_run_id,
            "author_model": author_model,
            "reviewer_models": [reviewer_a_model, reviewer_b_model],
        }
    )
    with transaction() as connection:
        if connection.execute(
            "SELECT 1 FROM claims WHERE owner_run_id=?", (owner_run_id,)
        ).fetchone():
            raise ClaimLedgerError("owner run has already spent its one claim")
        count = connection.execute(
            "SELECT COUNT(*) FROM claims WHERE local_day=?", (local_day,)
        ).fetchone()[0]
        if count >= MAX_CLAIMS_PER_LOCAL_DAY:
            raise ClaimLedgerError("local calendar day has spent all four claims")
        if connection.execute(
            "SELECT 1 FROM claims WHERE claim_id=?", (claim_id,)
        ).fetchone():
            raise ClaimLedgerError("claim identity already exists")
        connection.execute(
            """
            INSERT INTO claims (
              claim_id, local_day, created_epoch, terminal_epoch,
              timezone_name, timezone_offset_minutes, candidate_id,
              skill_key, skill_path, owner_run_id, lock_fence_token_sha256,
              author_model, reviewer_a_model, reviewer_b_model,
              status, terminal_reason, initial_manifest_sha256, review_set_id,
              max_slots, max_normalized_tokens, max_elapsed_ms
            ) VALUES (
              ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
              'open', NULL, NULL, NULL, ?, ?, ?
            )
            """,
            (
                claim_id,
                local_day,
                created,
                timezone_name,
                timezone_offset,
                candidate_id,
                skill_key,
                skill_path,
                owner_run_id,
                lock_fence_digest(),
                author_model,
                reviewer_a_model,
                reviewer_b_model,
                MAX_SLOTS,
                MAX_NORMALIZED_TOKENS,
                MAX_ELAPSED_MS,
            ),
        )
        models = {
            "author": author_model,
            "reviewer_a": reviewer_a_model,
            "reviewer_b": reviewer_b_model,
        }
        for slot_index, (slot_name, operation_kind, model_role) in enumerate(
            SLOT_DEFINITIONS
        ):
            connection.execute(
                """
                INSERT INTO claim_slots (
                  claim_id, slot_index, slot_name, operation_kind,
                  expected_model, status, usage_status
                ) VALUES (?, ?, ?, ?, ?, 'unstarted', 'pending')
                """,
                (
                    claim_id,
                    slot_index,
                    slot_name,
                    operation_kind,
                    models[model_role],
                ),
            )
        append_event(
            connection,
            claim_id,
            "claim_reserved",
            {
                "candidate_id": candidate_id,
                "local_day": local_day,
                "max_elapsed_ms": MAX_ELAPSED_MS,
                "max_normalized_tokens": MAX_NORMALIZED_TOKENS,
                "max_slots": MAX_SLOTS,
                "owner_integration": "scheduled_owner_integration_pending",
                "owner_run_id": owner_run_id,
                "reviewer_order": [reviewer_a_model, reviewer_b_model],
                "skill_key": skill_key,
            },
            created_epoch=created,
        )
    return inspect_claim(claim_id)


def _claim(connection: sqlite3.Connection, claim_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM claims WHERE claim_id=?",
        (require_sha256(claim_id, "claim_id"),),
    ).fetchone()
    if row is None:
        raise ClaimLedgerError("claim does not exist")
    return row


def _slot(
    connection: sqlite3.Connection, claim_id: str, slot_name: str
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM claim_slots WHERE claim_id=? AND slot_name=?",
        (claim_id, require_text(slot_name, "claim slot")),
    ).fetchone()
    if row is None:
        raise ClaimLedgerError("claim slot is not one of the fixed six slots")
    return row


def _invalidate(
    connection: sqlite3.Connection,
    claim: sqlite3.Row,
    reason: str,
    *,
    event_type: str,
    details: dict[str, Any],
    slot_name: str | None = None,
) -> None:
    reason = require_text(reason, "claim invalid reason")
    if claim["status"] == "open":
        connection.execute(
            """
            UPDATE claims
            SET status='invalid', terminal_reason=?, terminal_epoch=?
            WHERE claim_id=? AND status='open'
            """,
            (reason, now_epoch(), claim["claim_id"]),
        )
    append_event(
        connection,
        claim["claim_id"],
        event_type,
        {"reason": reason, **details},
        slot_name=slot_name,
    )


def _fail_slot(
    connection: sqlite3.Connection,
    claim: sqlite3.Row,
    slot: sqlite3.Row,
    reason: str,
) -> None:
    connection.execute(
        """
        UPDATE claim_slots
        SET status='failed', terminal_epoch=?, usage_status='unavailable',
            normalized_tokens=NULL, input_tokens=NULL, output_tokens=NULL,
            elapsed_ms=NULL, billing_status=NULL, billing_cost_usd=NULL,
            billing_provider=NULL, billing_unavailable_reason=NULL,
            billing_native_line_item_id=NULL, billing_native_event_sha256=NULL,
            billing_native_event_size=NULL, operation_id=NULL,
            observed_model=NULL, review_receipt_sha256=NULL,
            decision=NULL, failure_reason=?
        WHERE claim_id=? AND slot_name=? AND status='dispatching'
        """,
        (now_epoch(), reason, claim["claim_id"], slot["slot_name"]),
    )
    connection.execute(
        """
        UPDATE claims
        SET status='invalid', terminal_reason='budget_unknown', terminal_epoch=?
        WHERE claim_id=? AND status='open'
        """,
        (now_epoch(), claim["claim_id"]),
    )
    append_event(
        connection,
        claim["claim_id"],
        "slot_failed_unknown_spent",
        {"failure_reason": reason, "usage_status": "unavailable"},
        slot_name=slot["slot_name"],
    )


def fail_dispatched_slot(claim_id: str, slot_name: str, reason: str) -> None:
    reason = require_text(reason, "slot failure reason")
    with transaction() as connection:
        claim = _claim(connection, claim_id)
        slot = _slot(connection, claim["claim_id"], slot_name)
        if slot["status"] != "dispatching":
            raise ClaimLedgerError(
                "only a dispatching slot can be failed; spent slots cannot retry"
            )
        _fail_slot(connection, claim, slot, reason)


def _reconcile_dispatching(
    connection: sqlite3.Connection, claim: sqlite3.Row
) -> list[str]:
    rows = connection.execute(
        """
        SELECT * FROM claim_slots
        WHERE claim_id=? AND status='dispatching'
        ORDER BY slot_index
        """,
        (claim["claim_id"],),
    ).fetchall()
    for slot in rows:
        _fail_slot(connection, claim, slot, "recovered_unknown_spent")
    return [row["slot_name"] for row in rows]


def prepare_dispatch(
    *,
    claim_id: str,
    skill_path: str,
    skill_key: str,
    candidate_id: str,
    slot_name: str,
    model: str,
    packet_id: str,
    manifest_sha256: str | None,
    validation_receipt_sha256: str | None,
    requested_token_budget: int,
    requested_timeout_seconds: int,
) -> dict[str, int | str]:
    skill_path = str(Path(require_text(skill_path, "skill path")).resolve())
    skill_key = require_text(skill_key, "skill key")
    candidate_id = require_sha256(candidate_id, "candidate_id")
    model = require_text(model, "operation model")
    packet_id = require_sha256(packet_id, "packet_id")
    requested_token_budget = require_positive_int(
        requested_token_budget, "requested token budget"
    )
    requested_timeout_seconds = require_positive_int(
        requested_timeout_seconds, "requested timeout"
    )
    slot_name = require_text(slot_name, "claim slot")
    error: str | None = None
    result: dict[str, int | str] | None = None
    recovered: list[str] = []
    with transaction() as connection:
        claim = _claim(connection, claim_id)
        slot = _slot(connection, claim["claim_id"], slot_name)
        if slot["operation_kind"] not in {"author", "review"}:
            append_event(
                connection,
                claim["claim_id"],
                "pre_call_refused",
                {
                    "reason": "unsupported_future_slot",
                    "requested_slot": slot_name,
                },
                slot_name=slot_name,
            )
            error = f"claim slot {slot_name} is reserved for a later slice"
        else:
            recovered = _reconcile_dispatching(connection, claim)
        if error is None and recovered:
            error = (
                "recovered dispatching slot as failed unknown-spent before new dispatch"
            )
        elif error is None and claim["status"] != "open":
            append_event(
                connection,
                claim["claim_id"],
                "pre_call_refused",
                {
                    "claim_status": claim["status"],
                    "reason": "claim_not_open",
                    "requested_slot": slot_name,
                },
                slot_name=slot_name,
            )
            error = f"claim is {claim['status']} and cannot dispatch"
        elif error is None:
            mismatch: str | None = None
            if claim["skill_path"] != skill_path or claim["skill_key"] != skill_key:
                mismatch = "skill_identity_mismatch"
            elif claim["candidate_id"] != candidate_id:
                mismatch = "candidate_identity_mismatch"
            if mismatch is None and slot["expected_model"] != model:
                mismatch = "model_identity_mismatch"
            if mismatch is None and slot["status"] != "unstarted":
                mismatch = "slot_already_spent"
            if mismatch is not None:
                _invalidate(
                    connection,
                    claim,
                    mismatch,
                    event_type="pre_call_refused",
                    details={
                        "expected_model": slot["expected_model"],
                        "provided_model": model,
                        "requested_slot": slot_name,
                    },
                    slot_name=slot_name,
                )
                error = mismatch.replace("_", " ")
            else:
                prior = connection.execute(
                    """
                    SELECT slot_name, status FROM claim_slots
                    WHERE claim_id=? AND slot_index<?
                    ORDER BY slot_index
                    """,
                    (claim["claim_id"], slot["slot_index"]),
                ).fetchall()
                if any(row["status"] != "completed" for row in prior):
                    _invalidate(
                        connection,
                        claim,
                        "slot_order_invalid",
                        event_type="pre_call_refused",
                        details={
                            "prior_slots": [
                                {
                                    "slot": row["slot_name"],
                                    "status": row["status"],
                                }
                                for row in prior
                            ]
                        },
                        slot_name=slot_name,
                    )
                    error = "claim slot order is invalid"
                else:
                    review_set_id = claim["review_set_id"]
                    if slot_name == "author":
                        if (
                            manifest_sha256 is not None
                            or validation_receipt_sha256 is not None
                        ):
                            _invalidate(
                                connection,
                                claim,
                                "author_binding_invalid",
                                event_type="pre_call_refused",
                                details={},
                                slot_name=slot_name,
                            )
                            error = "author dispatch cannot bind a manifest"
                    else:
                        if manifest_sha256 is None:
                            error = "review dispatch requires the initial manifest"
                        else:
                            manifest_sha256 = require_sha256(
                                manifest_sha256, "initial manifest"
                            )
                            if (
                                claim["initial_manifest_sha256"]
                                != manifest_sha256
                            ):
                                _invalidate(
                                    connection,
                                    claim,
                                    "manifest_identity_mismatch",
                                    event_type="pre_call_refused",
                                    details={
                                        "provided_manifest_sha256": manifest_sha256
                                    },
                                    slot_name=slot_name,
                                )
                                error = "review manifest differs from the claim"
                            elif validation_receipt_sha256 is None:
                                error = (
                                    "review dispatch requires validation receipt"
                                )
                            else:
                                validation_receipt_sha256 = require_sha256(
                                    validation_receipt_sha256,
                                    "validation receipt",
                                )
                                expected_review_set = review_set_identity(
                                    claim["claim_id"],
                                    claim["candidate_id"],
                                    manifest_sha256,
                                    claim["author_model"],
                                    [
                                        claim["reviewer_a_model"],
                                        claim["reviewer_b_model"],
                                    ],
                                )
                                if (
                                    review_set_id is not None
                                    and review_set_id != expected_review_set
                                ):
                                    _invalidate(
                                        connection,
                                        claim,
                                        "review_set_identity_mismatch",
                                        event_type="pre_call_refused",
                                        details={},
                                        slot_name=slot_name,
                                    )
                                    error = "claim review set identity is invalid"
                                else:
                                    review_set_id = expected_review_set
                                    connection.execute(
                                        """
                                        UPDATE claims SET review_set_id=?
                                        WHERE claim_id=? AND review_set_id IS NULL
                                        """,
                                        (
                                            review_set_id,
                                            claim["claim_id"],
                                        ),
                                    )
                    if error is not None and claim["status"] == "open":
                        refreshed = _claim(connection, claim["claim_id"])
                        if refreshed["status"] == "open":
                            _invalidate(
                                connection,
                                refreshed,
                                "pre_call_binding_invalid",
                                event_type="pre_call_refused",
                                details={"detail": error},
                                slot_name=slot_name,
                            )
                    if error is None:
                        used_tokens = connection.execute(
                            """
                            SELECT COALESCE(SUM(normalized_tokens), 0)
                            FROM claim_slots
                            WHERE claim_id=? AND usage_status='available'
                            """,
                            (claim["claim_id"],),
                        ).fetchone()[0]
                        current = now_epoch()
                        remaining_tokens = (
                            claim["max_normalized_tokens"] - used_tokens
                        )
                        remaining_ms = claim["max_elapsed_ms"] - max(
                            0, (current - claim["created_epoch"]) * 1000
                        )
                        remaining_seconds = remaining_ms // 1000
                        if remaining_tokens <= 0 or remaining_seconds <= 0:
                            _invalidate(
                                connection,
                                claim,
                                "aggregate_budget_exhausted",
                                event_type="pre_call_refused",
                                details={
                                    "remaining_elapsed_ms": max(0, remaining_ms),
                                    "remaining_normalized_tokens": max(
                                        0, remaining_tokens
                                    ),
                                },
                                slot_name=slot_name,
                            )
                            error = "aggregate claim budget is exhausted"
                        else:
                            effective_tokens = min(
                                requested_token_budget, remaining_tokens
                            )
                            effective_timeout = min(
                                requested_timeout_seconds, remaining_seconds
                            )
                            if effective_tokens <= 0 or effective_timeout <= 0:
                                raise ClaimLedgerError(
                                    "aggregate remaining budget is not positive"
                                )
                            cursor = connection.execute(
                                """
                                UPDATE claim_slots
                                SET status='dispatching', started_epoch=?,
                                    requested_token_budget=?,
                                    effective_token_budget=?,
                                    requested_timeout_seconds=?,
                                    effective_timeout_seconds=?,
                                    usage_status='pending', packet_id=?,
                                    manifest_sha256=?,
                                    validation_receipt_sha256=?,
                                    review_set_id=?
                                WHERE claim_id=? AND slot_name=?
                                  AND status='unstarted'
                                """,
                                (
                                    current,
                                    requested_token_budget,
                                    effective_tokens,
                                    requested_timeout_seconds,
                                    effective_timeout,
                                    packet_id,
                                    manifest_sha256,
                                    validation_receipt_sha256,
                                    review_set_id,
                                    claim["claim_id"],
                                    slot_name,
                                ),
                            )
                            if cursor.rowcount != 1:
                                raise ClaimLedgerError(
                                    "claim slot dispatch compare-and-swap failed"
                                )
                            append_event(
                                connection,
                                claim["claim_id"],
                                "slot_dispatching",
                                {
                                    "effective_timeout_seconds": effective_timeout,
                                    "effective_token_budget": effective_tokens,
                                    "manifest_sha256": manifest_sha256,
                                    "packet_id": packet_id,
                                    "review_set_id": review_set_id,
                                },
                                slot_name=slot_name,
                                created_epoch=current,
                            )
                            result = {
                                "claim_id": claim["claim_id"],
                                "slot": slot_name,
                                "token_budget": effective_tokens,
                                "timeout_seconds": effective_timeout,
                            }
    if error is not None:
        raise ClaimLedgerError(error)
    if result is None:
        raise ClaimLedgerError("claim dispatch did not produce a result")
    return result


def _billing_facts(operation: dict[str, Any]) -> dict[str, Any]:
    billing = operation.get("billing")
    if not isinstance(billing, dict):
        raise ClaimLedgerError("trusted operation billing is unavailable")
    status = billing.get("status")
    if status not in {"available", "unavailable"}:
        raise ClaimLedgerError("trusted operation billing status is invalid")
    provider = require_text(billing.get("provider"), "billing provider")
    if status == "unavailable":
        if (
            billing.get("cost_usd") is not None
            or not billing.get("unavailable_reason")
            or any(
                billing.get(field) is not None
                for field in (
                    "native_line_item_id",
                    "native_event_sha256",
                    "native_event_size",
                )
            )
        ):
            raise ClaimLedgerError("unavailable billing provenance is malformed")
    else:
        cost = billing.get("cost_usd")
        if (
            isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or cost < 0
            or billing.get("unavailable_reason") is not None
            or not billing.get("native_event_sha256")
            or billing.get("native_event_size") is None
        ):
            raise ClaimLedgerError("available billing provenance is malformed")
        require_sha256(
            billing.get("native_event_sha256"), "billing native event digest"
        )
        require_nonnegative_int(
            billing.get("native_event_size"), "billing native event size"
        )
    return {
        "status": status,
        "cost_usd": billing.get("cost_usd"),
        "provider": provider,
        "unavailable_reason": billing.get("unavailable_reason"),
        "native_line_item_id": billing.get("native_line_item_id"),
        "native_event_sha256": billing.get("native_event_sha256"),
        "native_event_size": billing.get("native_event_size"),
    }


def complete_slot(
    *,
    claim_id: str,
    slot_name: str,
    operation: dict[str, Any],
    manifest_sha256: str | None,
    review_receipt_sha256: str | None = None,
    decision: str | None = None,
    terminal_reason: str | None = None,
) -> None:
    if not isinstance(operation, dict):
        raise ClaimLedgerError("trusted operation must be an in-memory object")
    operation_id = require_sha256(operation.get("operation_id"), "operation_id")
    observed_model = require_text(
        operation.get("observed_model"), "observed model"
    )
    packet_id = require_sha256(operation.get("packet_id"), "operation packet")
    elapsed_ms = require_nonnegative_int(
        operation.get("elapsed_ms"), "operation elapsed_ms"
    )
    usage = operation.get("usage")
    if not isinstance(usage, dict):
        raise ClaimLedgerError("trusted operation usage is unavailable")
    normalized_tokens = require_positive_int(
        usage.get("normalized_tokens"), "normalized token usage"
    )
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if input_tokens is not None:
        input_tokens = require_nonnegative_int(input_tokens, "input token usage")
    if output_tokens is not None:
        output_tokens = require_nonnegative_int(
            output_tokens, "output token usage"
        )
    billing = _billing_facts(operation)
    slot_name = require_text(slot_name, "claim slot")
    error: str | None = None
    with transaction() as connection:
        claim = _claim(connection, claim_id)
        slot = _slot(connection, claim["claim_id"], slot_name)
        if slot["status"] != "dispatching":
            raise ClaimLedgerError(
                "only a dispatching slot can complete; spent slots cannot retry"
            )
        if slot["operation_kind"] not in {"author", "review"}:
            _fail_slot(
                connection, claim, slot, "unsupported_operation_kind"
            )
            error = (
                f"claim slot {slot_name} is reserved for a later slice"
            )
        expected_operation = slot["operation_kind"]
        if error is None and (
            operation.get("operation") != expected_operation
            or operation.get("model") != slot["expected_model"]
            or observed_model != slot["expected_model"]
            or packet_id != slot["packet_id"]
        ):
            _fail_slot(
                connection, claim, slot, "trusted_operation_identity_invalid"
            )
            error = "trusted operation identity differs from the reserved slot"
        if error is None and (
            normalized_tokens > slot["effective_token_budget"]
        ):
            _fail_slot(
                connection, claim, slot, "effective_token_budget_exceeded"
            )
            error = "trusted operation exceeds its effective token budget"
        if error is None and (
            elapsed_ms > slot["effective_timeout_seconds"] * 1000
        ):
            _fail_slot(
                connection, claim, slot, "effective_timeout_exceeded"
            )
            error = "trusted operation exceeds its effective timeout"
        current = now_epoch()
        used_tokens = connection.execute(
            """
            SELECT COALESCE(SUM(normalized_tokens), 0)
            FROM claim_slots
            WHERE claim_id=? AND usage_status='available'
            """,
            (claim["claim_id"],),
        ).fetchone()[0]
        if error is None and (
            used_tokens + normalized_tokens > claim["max_normalized_tokens"]
            or max(0, (current - claim["created_epoch"]) * 1000)
            > claim["max_elapsed_ms"]
        ):
            _fail_slot(connection, claim, slot, "aggregate_budget_exceeded")
            error = "trusted operation exceeds the aggregate claim budget"
        if error is None:
            if slot_name == "author":
                if terminal_reason is None:
                    manifest_sha256 = require_sha256(
                        manifest_sha256, "initial manifest"
                    )
                elif manifest_sha256 is not None:
                    raise ClaimLedgerError(
                        "terminal author operation cannot bind a manifest"
                    )
            else:
                manifest_sha256 = require_sha256(
                    manifest_sha256, "review manifest"
                )
                if manifest_sha256 != slot["manifest_sha256"]:
                    _fail_slot(
                        connection, claim, slot, "review_manifest_invalid"
                    )
                    error = "review completion differs from dispatched manifest"
                if error is None:
                    review_receipt_sha256 = require_sha256(
                        review_receipt_sha256, "review receipt"
                    )
                    if decision not in {"accept", "reject"}:
                        raise ClaimLedgerError("review decision is invalid")
            if error is None:
                connection.execute(
                    """
                    UPDATE claim_slots
                    SET status='completed', terminal_epoch=?,
                        usage_status='available', normalized_tokens=?,
                        input_tokens=?, output_tokens=?, elapsed_ms=?,
                        billing_status=?, billing_cost_usd=?,
                        billing_provider=?, billing_unavailable_reason=?,
                        billing_native_line_item_id=?,
                        billing_native_event_sha256=?,
                        billing_native_event_size=?, operation_id=?,
                        observed_model=?, manifest_sha256=?,
                        review_receipt_sha256=?, decision=?, failure_reason=NULL
                    WHERE claim_id=? AND slot_name=? AND status='dispatching'
                    """,
                    (
                        current,
                        normalized_tokens,
                        input_tokens,
                        output_tokens,
                        elapsed_ms,
                        billing["status"],
                        billing["cost_usd"],
                        billing["provider"],
                        billing["unavailable_reason"],
                        billing["native_line_item_id"],
                        billing["native_event_sha256"],
                        billing["native_event_size"],
                        operation_id,
                        observed_model,
                        manifest_sha256,
                        review_receipt_sha256,
                        decision,
                        claim["claim_id"],
                        slot_name,
                    ),
                )
                if slot_name == "author" and terminal_reason is None:
                    connection.execute(
                        """
                        UPDATE claims SET initial_manifest_sha256=?
                        WHERE claim_id=? AND status='open'
                        """,
                        (manifest_sha256, claim["claim_id"]),
                    )
                append_event(
                    connection,
                    claim["claim_id"],
                    "slot_completed",
                    {
                        "billing_status": billing["status"],
                        "decision": decision,
                        "elapsed_ms": elapsed_ms,
                        "manifest_sha256": manifest_sha256,
                        "normalized_tokens": normalized_tokens,
                        "observed_model": observed_model,
                        "operation_id": operation_id,
                        "review_receipt_sha256": review_receipt_sha256,
                        "review_set_id": slot["review_set_id"],
                    },
                    slot_name=slot_name,
                    created_epoch=current,
                )
                if terminal_reason is not None:
                    connection.execute(
                        """
                        UPDATE claims
                        SET status='completed', terminal_reason=?,
                            terminal_epoch=?
                        WHERE claim_id=? AND status='open'
                        """,
                        (terminal_reason, current, claim["claim_id"]),
                    )
                    append_event(
                        connection,
                        claim["claim_id"],
                        "claim_terminal",
                        {"reason": terminal_reason, "status": "completed"},
                        created_epoch=current,
                    )
                elif decision == "reject":
                    connection.execute(
                        """
                        UPDATE claims
                        SET status='invalid',
                            terminal_reason='independent_review_rejected',
                            terminal_epoch=?
                        WHERE claim_id=? AND status='open'
                        """,
                        (current, claim["claim_id"]),
                    )
                    append_event(
                        connection,
                        claim["claim_id"],
                        "claim_terminal",
                        {
                            "reason": "independent_review_rejected",
                            "status": "invalid",
                        },
                        created_epoch=current,
                    )
    if error is not None:
        raise ClaimLedgerError(error)


def _ready_facts(
    connection: sqlite3.Connection,
    claim_id: str,
    *,
    skill_path: str,
    skill_key: str,
    candidate_id: str,
    manifest_sha256: str,
    validation_receipt_sha256: str,
    review_receipt_sha256s: list[str],
) -> dict[str, Any]:
    claim = _claim(connection, claim_id)
    if claim["status"] not in {"open", "completed"} or (
        claim["status"] == "completed" and claim["terminal_reason"] != "ready"
    ):
        raise ClaimLedgerError("claim is not valid for readiness")
    if (
        claim["skill_path"] != str(Path(skill_path).resolve())
        or claim["skill_key"] != skill_key
        or claim["candidate_id"] != candidate_id
        or claim["initial_manifest_sha256"] != manifest_sha256
    ):
        raise ClaimLedgerError("readiness identity differs from the claim")
    rows = connection.execute(
        """
        SELECT * FROM claim_slots
        WHERE claim_id=? AND slot_index BETWEEN 0 AND 2
        ORDER BY slot_index
        """,
        (claim["claim_id"],),
    ).fetchall()
    if [row["slot_name"] for row in rows] != [
        "author",
        "review_a",
        "review_b",
    ] or any(
        row["status"] != "completed" or row["usage_status"] != "available"
        for row in rows
    ):
        raise ClaimLedgerError(
            "readiness requires completed author and two review slots"
        )
    reviews = rows[1:]
    if any(
        row["manifest_sha256"] != manifest_sha256
        or row["validation_receipt_sha256"] != validation_receipt_sha256
        or row["decision"] != "accept"
        for row in reviews
    ):
        raise ClaimLedgerError(
            "readiness reviews do not accept the same validated manifest"
        )
    expected_receipts = sorted(
        row["review_receipt_sha256"] for row in reviews
    )
    if sorted(review_receipt_sha256s) != expected_receipts:
        raise ClaimLedgerError(
            "readiness review receipts differ from the claim ledger"
        )
    expected_review_set = review_set_identity(
        claim["claim_id"],
        claim["candidate_id"],
        manifest_sha256,
        claim["author_model"],
        [claim["reviewer_a_model"], claim["reviewer_b_model"]],
    )
    if claim["review_set_id"] != expected_review_set or any(
        row["review_set_id"] != expected_review_set for row in reviews
    ):
        raise ClaimLedgerError("readiness review set identity is invalid")
    aggregate_tokens = connection.execute(
        """
        SELECT SUM(normalized_tokens) FROM claim_slots
        WHERE claim_id=? AND status='completed'
        """,
        (claim["claim_id"],),
    ).fetchone()[0]
    if aggregate_tokens is None or aggregate_tokens > claim["max_normalized_tokens"]:
        raise ClaimLedgerError("readiness aggregate usage is unavailable or excessive")
    elapsed_at = claim["terminal_epoch"] or now_epoch()
    if max(0, (elapsed_at - claim["created_epoch"]) * 1000) > claim["max_elapsed_ms"]:
        raise ClaimLedgerError("readiness aggregate elapsed bound is exceeded")
    return {
        "claim_id": claim["claim_id"],
        "review_set_id": expected_review_set,
        "aggregate_normalized_tokens": aggregate_tokens,
    }


def assert_ready(
    claim_id: str,
    *,
    skill_path: str,
    skill_key: str,
    candidate_id: str,
    manifest_sha256: str,
    validation_receipt_sha256: str,
    review_receipt_sha256s: list[str],
) -> dict[str, Any]:
    connection = connect(create=False)
    try:
        return _ready_facts(
            connection,
            claim_id,
            skill_path=skill_path,
            skill_key=skill_key,
            candidate_id=require_sha256(candidate_id, "candidate_id"),
            manifest_sha256=require_sha256(
                manifest_sha256, "initial manifest"
            ),
            validation_receipt_sha256=require_sha256(
                validation_receipt_sha256, "validation receipt"
            ),
            review_receipt_sha256s=[
                require_sha256(value, "review receipt")
                for value in review_receipt_sha256s
            ],
        )
    finally:
        connection.close()


def complete_claim_ready(
    claim_id: str,
    *,
    skill_path: str,
    skill_key: str,
    candidate_id: str,
    manifest_sha256: str,
    validation_receipt_sha256: str,
    review_receipt_sha256s: list[str],
) -> dict[str, Any]:
    with transaction() as connection:
        facts = _ready_facts(
            connection,
            claim_id,
            skill_path=skill_path,
            skill_key=skill_key,
            candidate_id=candidate_id,
            manifest_sha256=manifest_sha256,
            validation_receipt_sha256=validation_receipt_sha256,
            review_receipt_sha256s=review_receipt_sha256s,
        )
        claim = _claim(connection, claim_id)
        if claim["status"] == "open":
            current = now_epoch()
            connection.execute(
                """
                UPDATE claims
                SET status='completed', terminal_reason='ready',
                    terminal_epoch=?
                WHERE claim_id=? AND status='open'
                """,
                (current, claim["claim_id"]),
            )
            append_event(
                connection,
                claim["claim_id"],
                "claim_terminal",
                {
                    "reason": "ready",
                    "review_set_id": facts["review_set_id"],
                    "status": "completed",
                },
                created_epoch=current,
            )
        return facts


def inspect_claim(claim_id: str) -> dict[str, Any]:
    connection = connect(create=False)
    try:
        claim = _claim(connection, claim_id)
        slots = connection.execute(
            """
            SELECT * FROM claim_slots
            WHERE claim_id=?
            ORDER BY slot_index
            """,
            (claim["claim_id"],),
        ).fetchall()
        events = connection.execute(
            """
            SELECT * FROM claim_events
            WHERE claim_id=?
            ORDER BY event_id
            """,
            (claim["claim_id"],),
        ).fetchall()
        pending_usage = any(
            slot["status"] == "dispatching" for slot in slots
        )
        unknown_usage = any(
            slot["status"] == "failed"
            and slot["usage_status"] == "unavailable"
            for slot in slots
        )
        known_tokens = sum(
            slot["normalized_tokens"] or 0
            for slot in slots
            if slot["usage_status"] == "available"
        )
        completed = [slot for slot in slots if slot["status"] == "completed"]
        billing_available = (
            bool(completed)
            and all(slot["billing_status"] == "available" for slot in completed)
        )
        elapsed_at = claim["terminal_epoch"] or now_epoch()
        return {
            "schema_version": SCHEMA_VERSION,
            "claim_id": claim["claim_id"],
            "local_day": claim["local_day"],
            "created_epoch": claim["created_epoch"],
            "terminal_epoch": claim["terminal_epoch"],
            "timezone": {
                "name": claim["timezone_name"],
                "offset_minutes": claim["timezone_offset_minutes"],
            },
            "candidate_id": claim["candidate_id"],
            "skill_key": claim["skill_key"],
            "skill_path": claim["skill_path"],
            "owner_run_id": claim["owner_run_id"],
            "lock_fence": {
                "token_sha256": claim["lock_fence_token_sha256"],
                "scheduled_owner_integration": "pending",
            },
            "models": {
                "author": claim["author_model"],
                "reviewer_a": claim["reviewer_a_model"],
                "reviewer_b": claim["reviewer_b_model"],
            },
            "status": claim["status"],
            "terminal_reason": claim["terminal_reason"],
            "initial_manifest_sha256": claim["initial_manifest_sha256"],
            "review_set_id": claim["review_set_id"],
            "limits": {
                "slots": claim["max_slots"],
                "normalized_tokens": claim["max_normalized_tokens"],
                "elapsed_ms": claim["max_elapsed_ms"],
            },
            "aggregate_actual": {
                "started_operations": sum(
                    slot["status"] != "unstarted" for slot in slots
                ),
                "normalized_tokens": (
                    None if pending_usage or unknown_usage else known_tokens
                ),
                "usage_status": (
                    "unavailable"
                    if unknown_usage
                    else "pending"
                    if pending_usage
                    else "available"
                ),
                "operation_elapsed_ms": sum(
                    slot["elapsed_ms"] or 0 for slot in completed
                ),
                "claim_wall_elapsed_ms": max(
                    0, (elapsed_at - claim["created_epoch"]) * 1000
                ),
                "billing_status": (
                    "available" if billing_available else "unavailable"
                ),
                "billing_cost_usd": (
                    sum(slot["billing_cost_usd"] or 0 for slot in completed)
                    if billing_available
                    else None
                ),
            },
            "slots": [
                {
                    key: slot[key]
                    for key in (
                        "slot_index",
                        "slot_name",
                        "operation_kind",
                        "expected_model",
                        "status",
                        "started_epoch",
                        "terminal_epoch",
                        "requested_token_budget",
                        "effective_token_budget",
                        "requested_timeout_seconds",
                        "effective_timeout_seconds",
                        "usage_status",
                        "normalized_tokens",
                        "input_tokens",
                        "output_tokens",
                        "elapsed_ms",
                        "billing_status",
                        "billing_cost_usd",
                        "billing_provider",
                        "billing_unavailable_reason",
                        "billing_native_line_item_id",
                        "billing_native_event_sha256",
                        "billing_native_event_size",
                        "operation_id",
                        "observed_model",
                        "packet_id",
                        "manifest_sha256",
                        "validation_receipt_sha256",
                        "review_receipt_sha256",
                        "review_set_id",
                        "decision",
                        "failure_reason",
                    )
                }
                for slot in slots
            ],
            "events": [
                {
                    "event_id": event["event_id"],
                    "slot_name": event["slot_name"],
                    "event_type": event["event_type"],
                    "created_epoch": event["created_epoch"],
                    "details": json.loads(event["details_json"]),
                }
                for event in events
            ],
        }
    finally:
        connection.close()
