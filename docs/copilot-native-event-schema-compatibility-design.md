# Copilot native event schema and usage authority — critical work order (Round 2)

Status: design only. No runtime code is authorized by this document.

Base: `f63f55b` (`feature/copilot-native-event-schema`).
Owner component: `skills/skill-review/scripts/dreaming-vendor-adapter.py`.
Observed subject: GitHub Copilot CLI `1.0.82-1`.

Round 1 (`f604d49`) proposed a purely additive vocabulary change. The blocking
guard G1 was executed before implementation and **falsified that premise**. This
revision is the executable Round-2 work order that the measurement supports.

---

## Objective

Restore a complete Copilot trial result and a complete comparator result on
Copilot CLI `1.0.82-1` by (a) admitting the exact 13 observed event types into
the existing literal vocabulary and (b) replacing the accidental generic
recursive token-usage reading with one explicit, event-scoped, deduplicated
Copilot usage authority that the evaluation run and the comparator both use,
while preserving fail-closed refusal of every unknown type and every malformed
or contradictory usage.

---

## Non-goals

1. The trial authentication boundary (frozen candidate `d26adac`).
2. Any byte of `skill-evaluation-harness.py`.
3. Sandbox profile, credential projection, or any trust anchor.
4. Candidate lifecycle, routing funnel, or the bounded execution stage.
5. Prefix, wildcard, regex, or namespace-scoped event admission.
6. Generic unknown-event passthrough, logging fallback, or quarantine channel.
7. A versioned event-vocabulary registry, a per-CLI-version map, or a second
   native parser or service.
8. Bumping `SUPPORTED_SOURCE_VERSIONS` (`:152-156`) or any exact identity schema.
9. Transport redesign or trace-projection redesign.
10. Implementing or interpreting MCP functionality. MCP lifecycle events are
    admitted as opaque metadata.
11. Compatibility with unobserved or future CLI versions. Refusal on a future
    unknown type is the designed signal.
12. Any change to Claude or Codex vocabularies, shape validation, or usage
    derivation. All four vendor branches other than `copilot` stay byte-equal in
    behaviour.
13. Changing what counts as skill activation, or what the final trace contains.

---

## Lane

**Critical.** Two fail-closed boundaries move together — native admission
(`validate_native_schema`, `unsupported-native-schema`) and session transcript
admission (`_copilot_events`, `unsupported-source-schema`) share the same
constant — and the change additionally alters how a *quantitative gate* (token
budget) is derived. A wrong usage authority silently under- or over-counts a
budget that exists to bound real spend.

---

## Observed failure and measured evidence

### The original block

After the authentication slice made real Copilot execution succeed (exit 0,
real `assistant.message`, no authentication error), the adapter's own `run`
still failed:

```
run_error: { "code": "unsupported-native-schema",
             "message": "copilot:session.mcp_server_removed" }
```

Copilot `1.0.82-1` emits 13 types absent from `COPILOT_EVENT_TYPES`
(`:157-214`, 56 literal members):

`model.call_finished`, `model.captured_assignment_context`, `model.message`,
`model.messages_snapshot`, `model.model_call_started`, `model.model_call_success`,
`model.response`, `model.tool_execution`, `model.turn_ended`,
`model.turn_started`, `session.mcp_server_removed`,
`session.mcp_server_status_changed`, `session.mcp_servers_loaded`.

### G1 — structural inertness probe (executed, blocking guard)

Real capture, two runs, production readers loaded by `importlib` from the
unmodified adapter. Variants per type: removed (baseline), inserted, duplicated,
reordered front, reordered back, interleaved, plus an all-13 composite.

| result | value |
|---|---|
| comparisons | 162 |
| identical | 142 |
| differing | 20 |
| inert types | **12 of 13** |
| non-inert types | **1** — `model.model_call_success` |
| differing field | `native_detailed_usage`, in every differing case |

**F1.** `model.model_call_success` carries a nested `data.responseChunk.usage`
dict. `recursive_values` (`:5193-5200`) yields it and the vendor-agnostic
`for key in ("usage", "token_usage")` branch of `native_detailed_usage`
(`:5388-5407`) and `native_token_usage` (`:5330-5341`) reads its `total_tokens`.
Baseline `native_detailed_usage` is `null`; with the event present it is a full
usage dict. `data.responseUsage` is usage-shaped but sits under a key production
does not read, so it is not matched (`usage_keys = 3` for 3 instances).

**F2.** Admitting the 13 does not by itself restore a working pipeline. On
unmodified real streams with every observed type present,
`native_detailed_usage` returns `null`, because real `assistant.message` in
`1.0.82-1` has **no `data.outputTokens`** (24 measured key paths, none is
`outputTokens`). Consequences:

- `evaluation_comparator` (`:4286-4288`) hard-requires `native_detailed_usage`
  and therefore still raises `usage-unproved`.
- `evaluation_run` (`:5822-5832`) survives only by falling back to
  `native_token_usage`, whose value is sourced *entirely* from
  `model.model_call_success` — the one type G1 measured as non-inert.

Vocabulary drift is accompanied by usage-shape drift.

**Trap discovered by G1 and closed by this design.** `outputTokens` still
exists in `1.0.82-1`, but only nested inside telemetry:
`model.messages_snapshot.data.messages[].outputTokens` and
`model.message.data.message.outputTokens`. Production misses them today only by
accident — those nested message objects have no `data` key, so the
`item["data"]["outputTokens"]` predicate does not fire. Snapshots repeat message
history, so any future recursive reading of them would be duplication- and
order-dependent. The outer-event iterator removes that hazard by construction.

### G2 — usage semantics probe (executed)

Two further real runs: one single model call, one four-model-call tooling run.
Retained redacted: counts, booleans, numeric token fields, key paths, type tags,
and digests of ids — never raw ids or values.

| measurement | single | multi |
|---|---|---|
| transport events | 28 | 357 |
| outer `model.call_start` | 1 | 4 |
| outer `model.model_call_success` | 1 | 4 |
| every event has a nonempty top-level `id` | yes | yes |
| ids distinct | yes | yes |
| `total == prompt + completion` for every event | yes | yes |
| all three fields non-bool, non-negative ints | yes | yes |
| `data.responseUsage` agrees with `data.responseChunk.usage` on all three | yes | yes (4/4) |
| usage object found nested anywhere else | no | no |
| Σ prompt / Σ completion / Σ total | 8349 / 103 / 8452 | 36440 / 1027 / 37467 |
| max total | 8452 | 9524 |

Per-call sequence in the multi run, with `data.turn` as an exact 0-based call
index:

| turn | prompt | completion | total |
|---|---|---|---|
| 0 | 8390 | 716 | 9106 |
| 1 | 9156 | 192 | 9348 |
| 2 | 9376 | 113 | 9489 |
| 3 | 9518 | 6 | 9524 |

**Per-call, not cumulative.** `completion_tokens` is 716 → 192 → 113 → 6, i.e.
strictly decreasing; a cumulative counter cannot decrease. `prompt_tokens` rises
because the resent conversation grows, which is exactly per-call behaviour. The
sum of completions (1027) does not equal the last completion (6), so the last
event is not a running total. `outer model.call_start == outer
model.model_call_success == turn count` in both streams.

**No independent native token total exists to cross-check.** The already-admitted
`result` event does carry a top-level `usage` dict, but it contains
`codeChanges`, `premiumRequests`, `sessionDurationMs`, `totalApiDurationMs` and
**no token field at all** — which is why production's generic branch currently
extracts nothing from it.

**Outer-only versus recursive divergence, measured on the same real streams:**

| census | single outer → recursive | multi outer → recursive |
|---|---|---|
| dicts seen | 28 → 162 | 357 → 1136 |
| typed items | 28 → 31 | 357 → 384 |
| dicts with a `usage` key | 1 → 2 | 1 → 5 |
| tool calls | 0 → 0 | 3 → 3 |
| turns / `model.call_start` count | 1 → 1 | 4 → 4 |
| trace matches | 2 → 2 | 14 → 14 |
| activation matches | 1 → 1 | 7 → 7 |
| distinct models | 1 → 1 | 1 → 1 |
| tool-prefix hits (comparator gate) | 0 → 0 | 158 → 158 |
| `data.outputTokens` hits | 0 → 0 | 0 → 0 |

Recursion inflates the inspected surface three- to six-fold and changes exactly
one thing on real data: how many `usage` dicts are visible. Every other Copilot
semantic result is already outer-only in effect. **Tightening Copilot readers to
outer events is behaviour-preserving on real data for everything except the
usage surface, which is precisely what this work order replaces.**

Evidence root (session-local, redacted, outside any repository):
`~/.copilot/session-state/c7947aa7-3025-4b4e-977d-294626e8e949/files/task-opportunity-profile-funnel-proof/copilot-native-event-schema-probe/`
— `g1-finding-record.json`, `g1-inertness-report.json`, `g1-supplementary.json`,
`g1-usage-derivation.json`, `g1-capture-summary.json`, `g2-usage-semantics.json`,
`g2-outer-vs-recursive.json`, and the G2 reproduction scripts. Raw JSONL and the
private working roots were deleted; artifacts scanned clean for credential
markers, run content, and raw identifiers.

---

## Root cause

Two independent drifts in one owner:

1. **Vocabulary drift.** `COPILOT_EVENT_TYPES` was enumerated against an older
   build; `1.0.82-1` added a `model.*` telemetry channel and MCP lifecycle
   events.
2. **Usage-shape drift.** The Copilot token numbers moved out of
   `assistant.message.data.outputTokens` and into
   `model.model_call_success.data.responseChunk.usage`, which the adapter reads
   only by accident, through a vendor-agnostic recursive `usage` scan that was
   never intended as a Copilot usage contract.

The second is the more dangerous one: the adapter's budget gate currently
depends on an unnamed, unvalidated, recursion-discovered field.

---

## Constraint provenance and revisit conditions

| Constraint | Provenance | Revisit when |
|---|---|---|
| Exact set membership, no patterns | literal set `:157-214`; `not in` at `:5232` | Never in this lane. |
| Copilot admission validates `type` only | deeper shape checks guarded by `vendor == "claude"` (`:5237`) and `vendor == "codex"` (`:5258`) | If a finding shows a Copilot type must be shape-checked to hold an invariant. Usage shape is validated by the new authority, not by the admission boundary. |
| Vocabulary is not keyed by CLI version | `evaluation_cli_version` (`:3978-3991`) records the version into identity/attestation (`:4036`, `:4092`, `:6194`); the validator never consults it | If two supported CLI versions ever need mutually exclusive vocabularies. |
| Usage is per model call | G2: completion 716→192→113→6, `data.turn` 0-3, `model.call_start` count equals success count | On any CLI upgrade, or if a cumulative field appears. |
| `total == prompt + completion` | G2: true for all 5 measured events across both streams | On any CLI upgrade. |
| Each transport event has a stable distinct `id` | G2: present and distinct in both streams | If an event without `id` appears — the design refuses rather than guessing. |
| No independent native token total | G2: `result.usage` has no token field | If the CLI adds one; then it becomes a cross-check, not a second authority. |
| Legacy Shape A must keep working | fixture `test-skill-evaluation-vendor-adapters.sh:172-201` emits `data.outputTokens`, `data.usage`, and `session.usage_checkpoint.usage` | If those fixtures are retired deliberately. |

### The challenged rules, precisely

Two inherited rules are challenged, and only these two:

- *"An event type absent from the exact set is unrepresentable."* The **set
  contents** change for one vendor; the rule does not.
- *"Token usage is whatever a recursive scan finds under a key named `usage` or
  `token_usage`."* This was never a designed Copilot contract. It is replaced,
  **for Copilot only**, by an explicit event-scoped authority. Claude and Codex
  keep the existing generic behaviour unchanged.

---

## Reframe record — status CLEAR

**1. What user outcome is blocked?**
A complete trial result and a complete comparator result. Not "a validator
error" — the run cannot produce evidence, and the comparator cannot produce a
verdict, on the only CLI the host has.

**2. What caused the block?**
The exact-type constraint refused 13 real types, and — behind it — the generic
recursive `usage` scan is the sole, accidental source of Copilot token numbers,
which returns nothing for detailed usage on this CLI version.

**3. What is rejected outright?**
Removing or weakening fail-closed validation: no prefix admission, no
passthrough, no unknown-event tolerance, and no silent `max`/fallback across
contradictory usage authorities. Also rejected: a registry, a second parser, or
a version-keyed vocabulary service for a single current caller.

**4. What is selected?**
The existing validator plus one explicit current-Copilot usage adapter: grow the
literal set by 13, add one narrow outer-event iterator, and add one event-scoped
usage parser that both the run and the comparator consume.

**5. What would reopen this?**
A CLI version where usage is cumulative, where transport events lack stable
ids, where the usage object appears nested inside another admitted event, or
where a second independent native token total appears. Each has a check.

**Status is CLEAR.** G2 establishes a deterministic, duplication-safe and
order-safe aggregation rule — sum per-call `prompt`/`completion`/`total` over
transport events deduplicated by stable `id` — and no second parser or service
is required.

---

## Enumeration of enforcement layers

| # | Layer | Location | Effect |
|---|---|---|---|
| L1 | Exact vocabulary set | `:157-214`, 56 members | Grows 56 → 69: exactly the 13. Only edit to the vocabulary. |
| L2 | Native admission | `validate_native_schema:5203-5236`; callers `:4279`, `:5813` | Unchanged code; refusal set shrinks by exactly 13. |
| L3 | Session transcript admission | `_copilot_events:620-642`, `unsupported-source-schema` | Same constant, second consumer, also widened. Its `mapping` (`:626-634`) has no entry for any of the 13 and unmapped types are skipped at `:644`, so projection is unchanged — asserted, not assumed. |
| L4 | Model identity | `native_model:5275-5308` | Copilot branch scoped to outer events. Measured identical (single 1→1, multi 1→1 distinct models). Claude/Codex untouched. |
| L5 | Token usage | `native_token_usage:5310-5342` | Copilot branch replaced by the new authority. Claude/Codex generic path untouched. |
| L6 | Detailed usage, turns, tool calls | `native_detailed_usage:5344-5432`; `turns += 1` at `:5359` | Copilot usage replaced by the new authority; turns stay exact outer `model.call_start`; tool calls stay exact outer `data.toolRequests`. Measured identical outer-vs-recursive (0→0, 3→3). |
| L7 | Skill activation evidence | `native_skill_evidence:5434-5713` | Copilot scans scoped to outer events. Measured identical (1→1, 7→7). |
| L8 | Final trace | `normalized_native_events:5956-6022` (**omitted in Round 1**) | Copilot branch scoped to outer events. Measured identical (2→2, 14→14). |
| L9 | Comparator recursive gate | `:4289-4311` (**omitted in Round 1**) — recursive `event_types`, `tool_event` prefix test, `model.call_start` count must be exactly 1 | Scoped to outer events. Measured identical (tool-prefix hits 0→0, 158→158; call-start count 1→1, 4→4). Consumes the same usage authority as the run. |
| L10 | Failure message extraction | `native_failure_message:5716-5748` | Copilot branch matches `session.error`/`error`. Scoped to outer events; no measured divergence. |
| L11 | Raw evidence retention | `evaluation_run` records `:5861-5875`, per-event at `:5870` | Every admitted event retained verbatim; raw output grows. `executor-output-limit` unchanged. |
| L12 | Sentinel refusal test | `test-skill-evaluation-vendor-adapters.sh:1424-1428`, fixture sentinel at `:128` | Must stay green, unmodified. |
| L13 | Legacy fixture usage shape | fixture `:172-201` | Shape A must keep producing today's exact numbers. |
| L14 | Adapter byte pin | `skill-evaluation.py:88-90`, consumers `:439`, `:5114`, `:5278` | Recompute. |
| L15 | Pin ratchet | `test-evaluation-input-source-builder.sh:165` | Self-healing; detects a forgotten recomputation. |
| L16 | Executor identity/attestation | `evaluation_cli_version:3978-3991`; `native_identity:6658` | Unchanged. |

Untouched: harness bytes, sandbox profile, credential projection, Claude/Codex
branches, `SUPPORTED_SOURCE_VERSIONS`.

---

## Selected architecture

### A1 — Vocabulary: add exactly 13 literal members

`COPILOT_EVENT_TYPES` grows 56 → 69. Literal strings only. No prefix, wildcard,
regex, passthrough, or registry. `validate_native_schema` itself is unmodified.

No shape validation is added at the admission boundary: the Copilot boundary
validates `type` only by design (`:5237`, `:5258` guard the deeper checks for
other vendors), and G1 measured 12 of 13 fully inert. Usage shape is validated
where it is *used*, not where it is admitted.

### A2 — One narrow outer-event iterator

Add a single helper:

```
copilot_outer_events(values) -> Iterable[dict]
```

It yields only transport-level Copilot events: each top-level value, or — for a
value whose key set is exactly `{"events"}` — the one-level list already
recognised by `validate_native_schema:5220-5228`. It never descends into event
bodies and never recurses.

Every Copilot branch in `native_model`, `native_token_usage`,
`native_detailed_usage`, `native_skill_evidence`, `normalized_native_events`,
`native_failure_message`, and the comparator gate `:4289-4311` iterates this
helper instead of `recursive_values`. `recursive_values` itself is unmodified
and remains the iterator for Claude and Codex.

Justification is measured, not aesthetic: outer-only changes **nothing** on real
`1.0.82-1` data except the number of visible `usage` dicts (see the divergence
table). It removes the snapshot-duplication and ordering hazard by construction
rather than by hoping no nested object ever matches.

**Declared-path carve-out for Shape A.** The legacy fixture places usage at
`<event>.usage`, `<event>.data.usage`, and `<event>.data.outputTokens`. Those
are exact declared paths inside an outer event, not recursion, and the Shape A
reader reads exactly those three and nothing else.

### A3 — Event-scoped Copilot usage parser

```
copilot_call_usage(values) -> dict | None      # Shape B authority
```

Rules, each backed by a G2 measurement:

1. Iterate outer events; select `type == "model.model_call_success"`.
2. Read exactly `data.responseChunk.usage`. No search, no recursion, no
   alternative path.
3. Require exactly the observed numeric fields `prompt_tokens`,
   `completion_tokens`, `total_tokens`; each a non-bool `int`, each `>= 0`, and
   `total_tokens == prompt_tokens + completion_tokens`. Any violation refuses
   with `usage-unproved` / `copilot:usage-malformed`.
4. If `data.responseUsage` is present, its three fields must equal the
   authority's. Disagreement refuses with `copilot:usage-contradiction`. (G2:
   they agreed in 5 of 5 events; this is a cheap intra-event cross-check, not a
   second authority.)
5. Deduplicate by the event's top-level `id`. `id` must be a nonempty string;
   absence refuses with `copilot:usage-ambiguous`. Two events sharing an `id`
   must be byte-identical in their usage triple; otherwise
   `copilot:usage-ambiguous`.
6. Sum `prompt_tokens`, `completion_tokens`, `total_tokens` over the deduplicated
   set. Summation over a set keyed by `id` is order-independent and
   duplication-safe by construction.
7. The number of distinct usage ids must equal the number of outer
   `model.call_start` events; otherwise `copilot:usage-incomplete`. (G2: 1 == 1
   and 4 == 4.)
8. Return `None` only when no outer `model.model_call_success` exists at all —
   the Shape A path then applies. Malformed or contradictory usage never
   degrades to Shape A.

No new error *code* is introduced: the existing `usage-unproved` code carries an
exact machine-readable reason string, so no consumer surface widens.

### A4 — One Copilot usage authority for both callers

```
copilot_usage(values) -> dict | None
   { turns, input_tokens, output_tokens, total_tokens, tool_calls }
```

- `input_tokens` = Σ `prompt_tokens`; `output_tokens` = Σ `completion_tokens`;
  `total_tokens` = Σ `total_tokens`; and `total_tokens == input_tokens +
  output_tokens` is re-asserted after summation.
- `turns` = count of outer `model.call_start` (unchanged rule, `:5359`).
- `tool_calls` = existing exact outer `data.toolRequests` evidence (unchanged
  rule; measured identical outer-vs-recursive).

`native_detailed_usage` returns this for Copilot. `native_token_usage` returns
its `total_tokens` for Copilot. **They cannot disagree**, which is the defect
that let `evaluation_run` and `evaluation_comparator` reach different verdicts
on the same stream. Claude and Codex keep their existing implementations
verbatim.

**Declared semantic change: `max` → `sum`.** Today Copilot totals are
`max(totals)` (`:5414`, with `max(inputs)` at `:5410`). Per-call usage must be summed to represent a trial.
On the G2 multi run this is 37467 rather than 9524. This is an intended
correction of a gate that exists to bound spend, and it is stated as an
acceptance criterion rather than left to be discovered. Single-call streams and
every existing fixture are unaffected because sum equals max when there is one
contributor.

### A5 — Closed two-shape compatibility rule

| | Shape A (legacy, fixtures) | Shape B (Copilot 1.0.82-1) |
|---|---|---|
| declared paths | `<event>.usage`, `<event>.data.usage`, `<event>.data.outputTokens` | `model.model_call_success.data.responseChunk.usage` |
| fields | today's `input_tokens`/`inputTokens`, `output_tokens`/`outputTokens`, `total_tokens`/`totalTokens` | `prompt_tokens`, `completion_tokens`, `total_tokens` |
| aggregation | today's exact behaviour, unchanged | dedupe by `id`, then sum |

Precedence and agreement:

- Shape B present and valid → Shape B is the authority.
- Shape B absent entirely → Shape A, with today's exact numbers.
- **Both present** → the two `total_tokens` must be equal; disagreement refuses
  with `copilot:usage-contradiction`. No `max`, no preference, no silent
  fallback.
- Neither present → `usage-unproved`, as today.

A `usage` dict that carries **none** of the recognised token fields is not a
usage authority and is ignored. This is not a loophole invented for
convenience: the already-admitted `result` event carries exactly such a dict
(`codeChanges`, `premiumRequests`, `sessionDurationMs`, `totalApiDurationMs`),
measured in both G2 streams, and it must not refuse a valid run.

The obsolete `assistant.message.data.outputTokens` dependency is **not removed**.
It survives inside Shape A, which the fixtures prove still works.

### A6 — Admitted metadata stays inert everywhere else

No newly admitted type is added to any match set in `native_model`,
`native_skill_evidence`, `normalized_native_events`, or the comparator gate.
`model.model_call_success` is consumed by the usage authority and by nothing
else. G1 measured the other 12 inert across 12 variants each.

---

## Reuse contract

| Need | Reused | Not built |
|---|---|---|
| Admission | `validate_native_schema`, unmodified | second parser |
| Vocabulary | existing literal set | registry, map, pattern matcher |
| Envelope handling | the existing one-level `{"events": [...]}` rule | new transport |
| Error surface | existing `usage-unproved` code with exact reasons | new error codes |
| Version identity | `evaluation_cli_version`, `native_identity` | version-keyed vocabulary |
| Refusal proof | existing sentinel test | new sentinel framework |
| Fixtures | existing fixture CLI | new harness |
| Byte pin | `TRUSTED_AUTHORING_ADAPTER_SHA256` + existing ratchet | new pin |

New code is three things: 13 literal strings, one iterator, one usage parser
plus its single aggregation entry point.

---

## Source-to-runtime data flow

```
Copilot CLI 1.0.82-1 --output-format json  →  stdout JSONL
        │
        ▼
native_objects (:5170)                 refuses non-JSON / non-object   [unchanged]
        │
        ▼
validate_native_schema (:5203)         type ∈ COPILOT_EVENT_TYPES      [set 56 → 69]
        │                              else unsupported-native-schema  [unchanged]
        ▼
copilot_outer_events(values)           transport events only           [NEW]
        │   (top-level values, or the one-level {"events":[...]} list)
        ├─► native_model                 model.call_start | session.start
        │                                | session.model_change
        ├─► native_skill_evidence        session.skills_loaded | tool.execution_complete
        │                                | skill.invoked | assistant.message
        ├─► normalized_native_events     trace kinds
        ├─► comparator gate (:4289)      event_types, tool_event, call-start count
        └─► copilot_call_usage           model.model_call_success
                                          .data.responseChunk.usage      [NEW]
                                          validate → dedupe by id → sum
        │
        ▼
copilot_usage → { turns, input_tokens, output_tokens, total_tokens, tool_calls }
        │                                                                [NEW]
        ├─► native_detailed_usage (copilot branch)
        └─► native_token_usage    (copilot branch) = total_tokens
        │
        ▼
evaluation_run (:5822)     token gate, records, raw output    [same authority]
evaluation_comparator (:4286) usage gate, verdict             [same authority]
```

Second, independent path, unchanged in behaviour:

```
~/.copilot session-state JSONL
   → _copilot_events (:620)   type ∈ COPILOT_EVENT_TYPES  [set 56 → 69]
                              else unsupported-source-schema
   → mapping (:626) has no entry for the 13 → skipped at :644 → projection identical
```

---

## Threat and failure model

| # | Threat | Mechanism | Mitigation | Proof |
|---|---|---|---|---|
| T1 | Usage double-count from snapshots | `model.messages_snapshot` repeats message history containing `outputTokens` | outer-event iterator never descends into bodies | CHK-6, CHK-11 |
| T2 | Usage double-count from repeated transport events | an exactly duplicated event | dedupe by stable `id` | CHK-13 |
| T3 | Order dependence | readers that take `max`, first, or last | summation over an id-keyed set | CHK-14 |
| T4 | Silent under-count | one model call missing its usage event | distinct-id count must equal outer `model.call_start` count | CHK-15 |
| T5 | Malformed usage accepted | partial or non-int fields | exact required fields, non-bool non-negative ints, `total == prompt + completion` | CHK-12 |
| T6 | Two authorities disagreeing | Shape A and Shape B both present | equality required, else refuse | CHK-17 |
| T7 | Intra-event contradiction | `data.responseUsage` diverging from `data.responseChunk.usage` | equality required when present | CHK-18 |
| T8 | Run and comparator disagreeing | separate derivations | both consume `copilot_usage` | CHK-19 |
| T9 | Phantom model / activation / trace / tool events | nested objects matching type predicates | outer-event iterator plus measured invariance | CHK-7, CHK-8, CHK-9, CHK-10 |
| T10 | Boundary becomes a passthrough | prefix or wildcard edit | literal members only; grep assertion | CHK-3, CHK-20 |
| T11 | Unknown type stops refusing | relaxed `not in` | sentinel unchanged and green | CHK-3 |
| T12 | Malformed envelope admitted | `native_objects` or the `{"events"}` unwrap relaxed | untouched, explicitly tested | CHK-4 |
| T13 | Transcript boundary silently widened | shared constant | declared and asserted | CHK-5 |
| T14 | Legacy shape broken | Copilot usage rewritten | Shape A fixtures must produce today's exact numbers | CHK-16 |
| T15 | Claude/Codex regression | shared helpers touched | their branches use `recursive_values` unchanged; suites green | CHK-21 |
| T16 | Evidence bloat | snapshot events are large and retained verbatim | `executor-output-limit` unchanged | CHK-22 |
| T17 | Stale adapter pin | adapter bytes change | recompute; ratchet | CHK-23 |
| T18 | Pin collision with the frozen auth candidate | both change adapter bytes and the same pin | sequential integration with recomputation | closure §8 |

Fail-closed and unchanged: unknown type, malformed JSON, non-object event,
malformed nested envelope, `exact-model-unproved`, `token-limit-exceeded`,
`executor-output-limit`.

---

## Hard invariants

- **I1.** `skill-evaluation-harness.py` is byte-identical to its integration base.
- **I2.** Admission remains exact literal set membership: no prefix, wildcard,
  regex, `startswith`, namespace rule, or passthrough.
- **I3.** An event type outside the set still raises `unsupported-native-schema`
  with message `copilot:<type>`, before any raw output is written.
- **I4.** `SUPPORTED_SOURCE_VERSIONS`, `adapter_version`, identity keys,
  `sandbox_id` inputs, and attestation shapes are unchanged.
- **I5.** Claude and Codex admission, shape validation, and usage derivation are
  behaviourally unchanged and continue to use `recursive_values`.
- **I6.** No Copilot semantic reader inspects any object below the transport
  event, except the exact declared paths named in A3 and A5.
- **I7.** Copilot usage is a pure function of the deduplicated set of outer
  `model.model_call_success` events (Shape B) or the declared Shape A paths —
  invariant under duplication, reordering, and interleaving of any other event.
- **I8.** `native_token_usage` and `native_detailed_usage` derive Copilot totals
  from the same authority and can never disagree.
- **I9.** Malformed, ambiguous, incomplete, or contradictory usage refuses. It
  never degrades to the other shape and never takes a `max`.
- **I10.** `turns` counts outer `model.call_start` only; `tool_calls` counts
  outer `data.toolRequests` evidence only.
- **I11.** No newly admitted type appears in any match set other than the usage
  authority's `model.model_call_success`.
- **I12.** The change to the vocabulary is additive only: no existing member is
  removed or renamed.
- **I13.** All consumers of `COPILOT_EVENT_TYPES` are declared and asserted; no
  third consumer exists.

---

## Observable acceptance criteria

- **AC-1.** A fixture stream reproducing the exact observed vocabulary is
  accepted by `validate_native_schema` for `copilot`.
- **AC-2.** Each of the 13 types, presented individually in an otherwise minimal
  valid stream, is accepted — 13 separate assertions.
- **AC-3.** A sentinel type outside the set is still refused with
  `unsupported-native-schema`, and no raw output file exists afterwards.
- **AC-4.** Malformed envelopes are still refused: non-JSON line, non-object
  event, `{"events": 3}`, `{"events": [1]}`.
- **AC-5.** A transcript containing the 13 is accepted by `_copilot_events` and
  projects identically to the same file with them removed.
- **AC-6.** `copilot_outer_events` yields exactly the transport events: one per
  top-level value, or the members of a one-level `{"events": [...]}` envelope,
  and never any object nested inside an event body.
- **AC-7.** `native_model`, `native_skill_evidence`, `normalized_native_events`,
  and the comparator `event_types`/`tool_event`/`model.call_start`-count gate
  return results identical to the pre-change recursive implementation on the
  retained real streams.
- **AC-8.** Those same results are unchanged when the 13 are inserted, removed,
  reordered, duplicated, or interleaved.
- **AC-9.** A single-call stream yields `input_tokens = prompt_tokens`,
  `output_tokens = completion_tokens`, `total_tokens = total_tokens`,
  `turns = 1`.
- **AC-10.** A four-call stream yields the per-call sums, not the maximum, and
  `turns = 4`.
- **AC-11.** Duplicating every `model.model_call_success` event leaves the usage
  result unchanged.
- **AC-12.** Reversing or interleaving the stream leaves the usage result
  unchanged.
- **AC-13.** A usage object missing a required field, carrying a bool, a float,
  a negative, or a `total != prompt + completion` refuses with `usage-unproved`
  and reason `copilot:usage-malformed`.
- **AC-14.** An event without a nonempty `id`, or two events sharing an `id`
  with different usage, refuses with reason `copilot:usage-ambiguous`.
- **AC-15.** Fewer distinct usage ids than outer `model.call_start` events
  refuses with reason `copilot:usage-incomplete`.
- **AC-16.** `data.responseUsage` disagreeing with `data.responseChunk.usage`
  refuses with reason `copilot:usage-contradiction`.
- **AC-17.** A legacy Shape A fixture stream produces exactly today's usage
  numbers, byte-for-byte.
- **AC-18.** A stream carrying both shapes with equal totals is accepted; with
  unequal totals it refuses with `copilot:usage-contradiction`.
- **AC-19.** A `usage` dict carrying no recognised token field — the real
  `result.usage` shape — neither contributes nor refuses.
- **AC-20.** `evaluation_run` and `evaluation_comparator` report the same total
  for the same stream.
- **AC-21.** Claude and Codex fixtures are unchanged and green;
  `SUPPORTED_SOURCE_VERSIONS` is unchanged.
- **AC-22.** Executor identity and attestation payload keys are byte-identical
  to base for an unchanged run.
- **AC-23.** A real adapter run on the current CLI produces a complete trial
  result with a real `assistant.message` trace, and a real comparator run
  produces a verdict — neither raising `unsupported-native-schema` nor
  `usage-unproved`.
- **AC-24.** AC-1 through AC-22 pass with no account authentication, no
  credential projection, and no model call.
- **AC-25.** The diff contains no prefix, wildcard, regex, or pattern-based
  admission.
- **AC-26.** The adapter byte pin is recomputed and every pin consumer passes.

---

## Guards that must precede implementation

Guards are written and executed **before** the production edit. Each is expected
to be red except where noted.

- **G-A — vocabulary and sentinel.** Assert the 13 are currently refused
  (red after the change becomes green), and that a sentinel type outside the set
  is refused both before and after (green throughout). Guards AC-2, AC-3.
- **G-B — direct-versus-nested iterator.** Assert `copilot_outer_events` yields
  transport events only, using a stream whose events embed objects that would
  match every Copilot type predicate if recursion were used. Guards AC-6, AC-7,
  T1, T9.
- **G-C — usage single call, multi call, dedup, order.** Fixtures reproducing
  the measured single-call and four-call shapes; assert sums, dedup invariance,
  and order invariance. Guards AC-9 through AC-12.
- **G-D — usage malformed and contradictory.** Missing field, bool, float,
  negative, broken sum, absent `id`, colliding `id`, id-count mismatch,
  `responseUsage` divergence, both-shapes divergence. Each asserts the exact
  refusal reason. Guards AC-13 through AC-16, AC-18.
- **G-E — legacy fixture compatibility.** The existing Shape A fixture stream
  must produce today's exact numbers. Green before and after. Guards AC-17,
  AC-19, T14.
- **G-F — comparator and run agreement.** One stream through both entry points;
  assert equal totals and that neither raises. Guards AC-20, T8.

**G-A through G-F are the pre-implementation guard set.** The already-executed
G1 and G2 probes are their evidentiary basis and are not repeated.

---

## Check contract

| ID | Layer | Check | Fixture-only? |
|---|---|---|---|
| CHK-1 | L1/L2 | observed-vocabulary fixture accepted | yes |
| CHK-2 | L1/L2 | 13 individual acceptance assertions | yes |
| CHK-3 | L2/L12 | sentinel still refused, no raw file; test source unmodified | yes |
| CHK-4 | L2 | malformed envelopes still refused | yes |
| CHK-5 | L3 | transcript acceptance and projection invariance | yes |
| CHK-6 | L4-L10 | `copilot_outer_events` yields transport events only | yes |
| CHK-7 | L4 | `native_model` identical to pre-change on real retained streams | yes |
| CHK-8 | L7 | `native_skill_evidence` identical | yes |
| CHK-9 | L8 | `normalized_native_events` identical | yes |
| CHK-10 | L9 | comparator `event_types` / `tool_event` / call-start count identical | yes |
| CHK-11 | L4-L10 | all of CHK-7..CHK-10 hold under insert / remove / reorder / duplicate / interleave of the 13 | yes |
| CHK-12 | L5/L6 | usage field validation: required fields, non-bool ints, non-negative, `total == prompt + completion` | yes |
| CHK-13 | L5/L6 | dedup by `id`: duplicating every usage event changes nothing | yes |
| CHK-14 | L5/L6 | order invariance: reversal and interleaving change nothing | yes |
| CHK-15 | L5/L6 | id-count versus outer `model.call_start` mismatch refuses | yes |
| CHK-16 | L13 | Shape A legacy fixtures produce today's exact numbers | yes |
| CHK-17 | L5/L6 | both shapes present: equal accepted, unequal refuses | yes |
| CHK-18 | L5/L6 | `responseUsage` divergence refuses | yes |
| CHK-19 | L5/L9 | run and comparator report the same total | yes |
| CHK-20 | L1 | no `startswith`, `re.`, wildcard, or prefix comparison near the vocabulary, the validator, or the usage parser | yes |
| CHK-21 | L5/L16 | Claude and Codex suites green; their branches still use `recursive_values`; `SUPPORTED_SOURCE_VERSIONS` unchanged | yes |
| CHK-22 | L11 | `executor-output-limit` and token-limit behaviour unchanged; flooded stream still refuses | yes |
| CHK-23 | L14/L15 | pin recomputed; ratchet green; consumers `:439`, `:5114`, `:5278` accept | yes |
| CHK-24 | L16 | identity and attestation keys byte-identical | yes |
| CHK-25 | real | real run yields a complete trial result; real comparator yields a verdict | **no** |

CHK-1 through CHK-24 are auth-independent and model-independent. Ordering:
guards G-A..G-F, then CHK-1..CHK-24, then CHK-25.

**Fixture derivation rule.** Usage fixtures reproduce the exact numeric shapes
measured in G2 — the single-call triple and the four-call sequence — with
synthetic ids. They are not hand-invented shapes and carry no captured content.

---

## Rollback and fail-closed evidence

- **Primary rollback:** revert the 13 vocabulary additions, the outer-event
  iterator, and the usage parser, then recompute the adapter pin. The boundary
  returns to explicit refusal with the identical code and message, and Copilot
  usage returns to the generic recursive scan.
- **Fail-closed direction:** the reverted state refuses *more*, never less.
  Rollback cannot open a hole.
- **Partial rollback is forbidden.** Reverting the usage parser while keeping
  the vocabulary leaves the comparator failing at `usage-unproved` — the
  measured F2 state. The two parts ship and revert together.
- **Negative evidence:** CHK-3 is the standing proof that a type outside the set
  still refuses; CHK-12 through CHK-18 are the standing proof that bad usage
  refuses rather than degrading.
- **No durable migration:** no persisted state, no config surface, no artifact
  format change. Historical raw output is never re-validated against the
  vocabulary.

---

## Digest, regeneration, and consumer closure

1. `dreaming-vendor-adapter.py` bytes change. Base digest at `f63f55b`:
   `cb77c7945f8efe858b3d2f2d1e3b28527cfe7590257abe95ec1421d72aaace22`.
2. The only committed pin of those bytes is `TRUSTED_AUTHORING_ADAPTER_SHA256`
   (`skill-evaluation.py:88-90`). Recompute it in the same commit.
3. Pin consumers: `skill-evaluation.py:439`, `:5114`, `:5278`. All three compare
   equality against that one constant, so one correct recomputation closes all
   three — each must be exercised, not assumed.
4. `test-evaluation-input-source-builder.sh:165` recomputes the constant from the
   adapter file by regex and is self-healing; it is the ratchet that detects a
   forgotten recomputation.
5. Evaluation input source bundles are content-addressed and built at runtime,
   not stored. No bundle regeneration. Verify that no committed artifact other
   than `skill-evaluation.py` embeds the adapter digest.
6. `skill-evaluation-harness.py` is unchanged; its digest pin in
   `test-skill-evaluation-harness.sh` must remain green untouched.
7. Preserved groups: no identity key, no `adapter_version`, no `sandbox_id`
   input, no generated config, no installed config, no launchd artifact. Do not
   blanket-regenerate installed configuration.
8. **Integration ordering with the frozen auth candidate.** `d26adac` in
   `dreaming-shadow-trial-auth` also changes adapter bytes and the same pin.
   Integrate sequentially: merge one, recompute the pin against the merged
   adapter bytes, then merge the second and recompute again. A pin computed
   against pre-merge bytes is a defect the ratchet will catch — and it must be
   caught before integration, not after.

---

## G1 / F1 / F2 resolution record

| ID | Round 1 position | Measured outcome | Resolution in this revision |
|---|---|---|---|
| G1 | reframe OPEN pending a structural inertness probe | executed: 162 comparisons, 142 identical, 12 of 13 inert | Guard discharged. 12 types admitted as inert metadata; the 13th is handled explicitly. |
| F1 | "no newly admitted type is semantically consumed" | **false** — `model.model_call_success` changes `native_detailed_usage` through nested `data.responseChunk.usage` | Withdrawn. That event is promoted to an explicit, validated usage authority (A3) instead of being read by accident. |
| F2 | "additive vocabulary is sufficient" | **false** — real `assistant.message` has no `data.outputTokens`, so the comparator still fails `usage-unproved` and the run degrades to a fallback | Withdrawn. A4 gives the run and the comparator one shared authority; A5 keeps the legacy shape working. |
| R1-a | layer enumeration omitted `normalized_native_events` | confirmed omission | Added as L8, with CHK-9 and AC-7. |
| R1-b | layer enumeration omitted the comparator recursive gate | confirmed omission | Added as L9, with CHK-10 and AC-7. |
| R1-c | "the residual risk is nesting inside snapshots" | measured: no nested `usage`; but `outputTokens` *does* exist nested in `model.messages_snapshot` and `model.message` | Closed by construction with the outer-event iterator (A2), not by hoping the predicate keeps missing it. |
| R1-d | reframe status OPEN | G2 settled per-call semantics, id stability, and field relationships | Status **CLEAR**; revisit conditions retained in the constraint table. |
| — | Round 1 proposed no shape validation anywhere | still correct at the admission boundary | Shape validation lives in the usage parser, where the value is consumed. |

---

## Definition of Done: Copilot event vocabulary and usage authority

This slice is done when, and only when, all of the following hold. It is
specific to this work order.

1. Guards G-A through G-F exist, were executed before the production edit, and
   their pre-change red or green signal was recorded.
2. `COPILOT_EVENT_TYPES` contains exactly its previous 56 members plus exactly
   the 13 measured types — 69 literal members — and no other vocabulary edit.
3. `validate_native_schema` is unmodified.
4. `copilot_outer_events` exists, yields transport events only, and every
   Copilot branch of `native_model`, `native_token_usage`,
   `native_detailed_usage`, `native_skill_evidence`, `normalized_native_events`,
   `native_failure_message`, and the comparator gate uses it.
5. `recursive_values` is unmodified and remains the Claude and Codex iterator.
6. `copilot_call_usage` implements A3 exactly: exact path, exact required
   fields, non-bool non-negative ints, `total == prompt + completion`,
   `responseUsage` agreement, dedup by `id`, per-call summation, and id-count
   equality with outer `model.call_start`.
7. `native_token_usage` and `native_detailed_usage` derive Copilot totals from
   the one authority, and CHK-19 proves the run and the comparator agree.
8. The two-shape rule of A5 is implemented with explicit precedence and equality
   checking; no `max` and no silent fallback survives on the Copilot path.
9. The `max` → `sum` change is asserted by CHK/AC, not left implicit.
10. CHK-1 through CHK-24 pass; the 13 per-type acceptance assertions exist
    individually; the sentinel test is green and its source unchanged.
11. Legacy Shape A fixtures produce today's exact numbers (CHK-16), and the real
    `result.usage` shape neither contributes nor refuses (AC-19).
12. Claude, Codex, harness, and identity surfaces are byte-unchanged and their
    suites are green.
13. CHK-25 passes: a real run produces a complete trial result and a real
    comparator produces a verdict on the current CLI.
14. The adapter pin is recomputed, all three pin consumers pass, and the builder
    ratchet is green.
15. The diff contains no pattern-based admission, no unknown-event fallback, no
    registry, no version map, and no new module.
16. The commit is local, on `feature/copilot-native-event-schema`, with the
    required trailers. Nothing is pushed, published, installed, or merged.
17. The integration-ordering note (closure §8) is carried to whoever integrates
    this candidate alongside `d26adac`.

Shipping the vocabulary without the usage authority is the measured F2 state and
must not be recorded as done.
