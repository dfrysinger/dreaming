# Copilot native event schema compatibility — critical work order

Status: design only. No runtime code is authorized by this document.

Base: `f63f55b` (`feature/copilot-native-event-schema`).
Owner component: `skills/skill-review/scripts/dreaming-vendor-adapter.py`.
Observed subject: GitHub Copilot CLI `1.0.82-1`.

---

## Objective

Let the existing Copilot native event validator accept the exact 29-type event
vocabulary that Copilot CLI `1.0.82-1` actually emits, so evaluation and
comparator runs reach their result instead of refusing at the first unmodelled
event, while preserving fail-closed refusal of every unknown type and while
proving that no newly admitted metadata or model-telemetry event can create,
inflate, or contradict model identity, token usage, turn counts, tool counts,
or skill-activation evidence.

---

## Non-goals

This work order does **not**:

1. Change the trial authentication boundary. That is
   `docs/shadow-trial-authentication-boundary-design.md` in the frozen
   `dreaming-shadow-trial-auth` candidate; it is a separate, already-implemented
   slice.
2. Change `skill-evaluation-harness.py` in any byte.
3. Change the sandbox profile, credential projection, or any trust anchor.
4. Change the candidate lifecycle, the routing funnel, or the bounded
   shadow-evaluation execution stage.
5. Introduce prefix, wildcard, regex, or namespace-scoped event admission.
6. Introduce a generic unknown-event passthrough, an unknown-event logging
   fallback, or a quarantine channel.
7. Introduce a versioned event-vocabulary registry, a per-CLI-version map, or a
   second native parser.
8. Bump `SUPPORTED_SOURCE_VERSIONS` (`dreaming-vendor-adapter.py:152-156`) or any
   exact identity schema.
9. Redesign transport, event normalization, or trace projection.
10. Implement, interpret, or expose MCP functionality. MCP lifecycle events are
    admitted as opaque metadata only.
11. Guarantee compatibility with unobserved or future CLI versions. Refusal on a
    future unknown type is the designed signal, not a defect.
12. Change Claude or Codex vocabularies or their deeper shape validation.

---

## Lane

**Critical.**

`validate_native_schema` is a fail-closed admission boundary shared by every
Copilot evaluation surface, and its refusal happens *before* raw evidence is
written (`dreaming-vendor-adapter.py:5813` onward; the per-event raw record write is at
`:5870`). Widening it widens what every downstream semantic reader is allowed to
see. The same constant is a second boundary for session transcript ingestion
(`_copilot_events`, `dreaming-vendor-adapter.py:620-642`, error
`unsupported-source-schema`). Two independent fail-closed refusal surfaces move
together; that is a critical-lane change even though the code delta is small.

---

## Observed failure

Retained redacted evidence (session proof root, outside any repository):

- `.../task-opportunity-profile-funnel-proof/shadow-trial-auth-live/tracer-evidence.json`
- `.../task-opportunity-profile-funnel-proof/shadow-trial-auth-live/direct-event-type-counts.json`

After the trial authentication slice made real Copilot execution succeed
(exit 0, real `assistant.message`, no authentication error), the adapter's own
`run` still failed:

```
run_error: { "code": "unsupported-native-schema",
             "message": "copilot:session.mcp_server_removed" }
```

The model was reached and answered. The refusal is entirely on the adapter's
event vocabulary.

### Exact observed vocabulary — 29 types, 78 events

Per-type counts from `direct-event-type-counts.json`:

| type | count | in current allowlist |
|---|---|---|
| `assistant.idle` | 1 | yes |
| `assistant.message` | 2 | yes |
| `assistant.message_delta` | 27 | yes |
| `assistant.message_start` | 2 | yes |
| `assistant.reasoning` | 1 | yes |
| `assistant.tool_call_delta` | 8 | yes |
| `assistant.turn_end` | 2 | yes |
| `assistant.turn_start` | 2 | yes |
| `model.call_finished` | 2 | **no** |
| `model.call_start` | 2 | yes |
| `model.captured_assignment_context` | 2 | **no** |
| `model.message` | 4 | **no** |
| `model.messages_snapshot` | 1 | **no** |
| `model.model_call_started` | 2 | **no** |
| `model.model_call_success` | 2 | **no** |
| `model.response` | 1 | **no** |
| `model.tool_execution` | 1 | **no** |
| `model.turn_ended` | 2 | **no** |
| `model.turn_started` | 2 | **no** |
| `result` | 1 | yes |
| `session.info` | 2 | yes |
| `session.mcp_server_removed` | 1 | **no** |
| `session.mcp_server_status_changed` | 2 | **no** |
| `session.mcp_servers_loaded` | 1 | **no** |
| `session.tools_updated` | 1 | yes |
| `session.usage_checkpoint` | 1 | yes |
| `tool.execution_complete` | 1 | yes |
| `tool.execution_start` | 1 | yes |
| `user.message` | 1 | yes |

Thirteen absent types, in two families:

- **M-family (10):** every `model.*` type except `model.call_start`, which is
  already admitted and already semantically load-bearing.
- **S-family (3):** `session.mcp_server_removed`,
  `session.mcp_server_status_changed`, `session.mcp_servers_loaded`.

---

## Root cause

`COPILOT_EVENT_TYPES` (`dreaming-vendor-adapter.py:157-214`, 56 literal members) is
an exact literal set enumerated against an older CLI build. `validate_native_schema`
(`:5203-5236`) raises `unsupported-native-schema` on the first event whose `type`
is not a member. Copilot `1.0.82-1` emits an additional internal `model.*`
telemetry channel and MCP lifecycle events that did not exist when the set was
written.

The vocabulary drifted; the boundary did exactly what it was designed to do.

---

## Constraint provenance and revisit conditions

| Constraint | Provenance | Revisit when |
|---|---|---|
| Exact set membership, no patterns | `COPILOT_EVENT_TYPES` is a literal set at `:157-214`; `validate_native_schema:5232` compares with `not in` | Never within this lane. A pattern allow is an explicit non-goal. |
| Copilot events are validated on `type` only | `validate_native_schema:5231-5236` refuses on type; the deeper per-type shape checks at `:5237-5274` are guarded by `vendor == "claude"` and `vendor == "codex"` only | If a future finding shows a Copilot type must be shape-checked to keep an invariant. |
| Vocabulary is not keyed by CLI version | `evaluation_cli_version` (`:3978-3991`) records the version string; it is used in identity/attestation payloads (`:4036`, `:4092`, `:6194`) and never consulted by `validate_native_schema` | If two supported CLI versions ever need mutually exclusive vocabularies. Not the case today: one CLI, one host. |
| The 29 observed types are the authority | `direct-event-type-counts.json`, captured from a real pinned run under the adapter's own executor path | On any CLI upgrade. A new type means a new refusal and a new measurement. |
| Refusal precedes raw write | run path: `native_objects` → `validate_native_schema` → semantic readers → records assembled at `:5861-5875` | Never. This ordering is the reason a bad schema cannot leave evidence. |

### The challenged rule, precisely

The inherited rule is *"an event type absent from the exact set is
unrepresentable and the run must refuse."* This work order challenges only the
**contents** of the set for one vendor, not the rule. After the change, an
unmodelled type still refuses, still refuses before raw write, and still refuses
with the same code and the same `vendor:type` message.

---

## Reframe record — status OPEN

Five answers, per the reframe protocol.

**1. What is actually being asked?**
Make the Copilot native admission boundary agree with the Copilot CLI the host
actually runs, without weakening it into a passthrough.

**2. What is the smallest thing that could satisfy it?**
Adding the 13 measured types to the existing literal set. No new function, no
new constant, no new file, no new caller. One set literal grows by 13 members.

**3. What must be true for that to be correct?**
That admitting those 13 events changes nothing except refusal. Concretely, all
of these must hold with the events present:

- `native_model` (`:5275-5308`) returns the same identity and does not raise
  `exact-model-unproved`;
- `native_token_usage` (`:5310-5342`) returns the same total;
- `native_detailed_usage` (`:5344-5432`) returns the same `turns`,
  `input_tokens`, `output_tokens`, `total_tokens`, `tool_calls`;
- `native_skill_evidence` (`:5434-5713`) returns the same activation evidence
  list;
- `_copilot_events` (`:620-660`) produces the same projected transcript.

**4. Why is that not provable from what we already hold?**
Because every one of those readers walks the event body recursively.
`recursive_values` (`:5193-5200`) yields the event dict *and every nested dict at
any depth*, and the semantic predicates then match on `item.get("type")`,
`item["data"]["outputTokens"]`, `item["data"]["toolRequests"]`,
`item["usage"]`, `item["token_usage"]`, and `data.model`. Admission risk is
therefore **structural, not type-keyed**. A `model.messages_snapshot` or
`model.message` body that embeds prior conversation state could, depending on
its internal shape, present a nested object that satisfies one of those
predicates and thereby inflate `output_tokens`, add phantom `tool_calls` or
`turns`, or introduce a second `data.model` value and trip
`exact-model-unproved`.

The retained evidence is a **type census only**. It does not contain event
bodies. Nothing currently held proves or disproves nesting.

**5. What would change the answer?**
A redacted structural measurement of all 29 observed types. If none of the 13
bodies contains a nested object bearing a semantic sentinel key-path, the
additive change is inert by measurement and the reframe closes CLEAR. If any
does, admission cannot be purely additive — the semantic readers would have to
be scoped, which alters existing semantics for already-admitted types too, and
that is a distinct design round outside this work order.

**Status is OPEN until guard G1 (below) measures.** The expected outcome is
CLEAR; the honest current state is that it is unmeasured.

---

## Enumeration of enforcement layers

Every layer that independently constrains Copilot event types, traced rather
than guessed.

| # | Layer | Location | Effect of the additive change |
|---|---|---|---|
| L1 | The exact vocabulary set | `dreaming-vendor-adapter.py:157-214`, 56 literal members at base | The only edited line range. Grows 56 → 69: exactly the 13 measured types, no other edit. |
| L2 | Native run/comparator admission | `validate_native_schema:5203-5236`, called from comparator `:4279` and `evaluation_run` `:5813` | Unchanged code. Refusal set shrinks by exactly 13 types. |
| L3 | Session transcript admission | `_copilot_events:620-642`, error `unsupported-source-schema` | **Also widened.** Same constant, different consumer, different error code. Its `mapping` (`:626-634`) has no entry for any of the 13, and unmapped types hit `if not kind: continue` (`:644`), so admitted types are projected as nothing. Must be asserted, not assumed. |
| L4 | Model identity | `native_model:5275-5308`; copilot matches `model.call_start`, `session.start`, `session.model_change` | No new type is added to that match set. Residual risk is nested-body only; G1 measures it. |
| L5 | Token usage | `native_token_usage:5310-5342`; `native_detailed_usage:5344-5432` | No new type is consumed. Residual risk: nested `usage`/`token_usage` dicts and nested `data.outputTokens`, both matched at any depth. |
| L6 | Turn and tool counting | `native_detailed_usage:5356-5371` (`turns += 1` at `:5359`) | `turns` increments on `model.call_start` only. `model.model_call_started` and `model.turn_started` are **not** counted; that is deliberate and must be asserted so a later reader does not "fix" it. |
| L7 | Skill activation evidence | `native_skill_evidence:5434-5713`; matches `session.skills_loaded`, `tool.execution_complete`, `skill.invoked`, `assistant.message` | No new type is consumed. Residual risk is nested-body only. |
| L8 | Raw evidence retention | `evaluation_run` record assembly `:5861-5875`, per-event retention at `:5870` | Each admitted event is retained verbatim as `dreaming.native`. Raw output grows; content that was previously never written now is. Output-size bounds and `executor-output-limit` are unchanged and must be shown still to hold. |
| L9 | Sentinel refusal test | `test-skill-evaluation-vendor-adapters.sh:1424-1428` (`test_unknown_native_schema_cannot_hide_activation`), fixture emits `new_skill_activation_event` at `:128` | Must stay green unchanged. It is the proof the boundary is still fail-closed. |
| L10 | Adapter byte pin | `skill-evaluation.py:88-90` `TRUSTED_AUTHORING_ADAPTER_SHA256`, consumed at `:439`, `:5114`, `:5278` | Must be recomputed. See closure section. |
| L11 | Pin ratchet | `test-evaluation-input-source-builder.sh:165` recomputes the constant from the adapter file | Self-healing; must still pass. |
| L12 | Executor identity/attestation | `evaluation_cli_version:3978-3991` and identity payloads `:4036`, `:4092`, `:6194`; `native_identity:6658` | Unchanged. No identity key, no `adapter_version`, no `sandbox_id` input changes. |
| L13 | Error strings and docs | `unsupported-native-schema`, `unsupported-source-schema` | Unchanged codes, unchanged messages. |

Layers deliberately **not** touched: harness bytes, sandbox profile, credential
projection, Claude/Codex vocabularies, `SUPPORTED_SOURCE_VERSIONS`.

---

## Classification of the 13

Classification is by *what the adapter does with the type*, which is knowable
today, plus a nesting hypothesis, which is not.

| type | family | consumed by any semantic reader? | role | nesting hypothesis |
|---|---|---|---|---|
| `session.mcp_servers_loaded` | S | no | MCP lifecycle metadata | low — server descriptors |
| `session.mcp_server_status_changed` | S | no | MCP lifecycle metadata | low |
| `session.mcp_server_removed` | S | no | MCP lifecycle metadata | low |
| `model.model_call_started` | M | no | provider-call telemetry | low–medium; may carry `model` |
| `model.model_call_success` | M | no | provider-call telemetry | medium; may carry usage |
| `model.call_finished` | M | no | provider-call telemetry | medium; may carry usage |
| `model.turn_started` | M | no | turn telemetry | low |
| `model.turn_ended` | M | no | turn telemetry | medium; may carry usage |
| `model.captured_assignment_context` | M | no | routing/telemetry context | medium |
| `model.tool_execution` | M | no | tool telemetry | medium; may carry tool call ids |
| `model.message` | M | no | message telemetry | **high** — likely embeds message objects |
| `model.response` | M | no | response telemetry | **high** |
| `model.messages_snapshot` | M | no | conversation snapshot | **highest** — a snapshot by name |

**No type-keyed consumption exists for any of the 13.** Every risk is nesting
risk, and every nesting hypothesis above is a hypothesis. G1 replaces the last
column with measurement.

Note the shape of the risk precisely: it is not that a `model.*` event is
*interpreted*; it is that `recursive_values` cannot tell a telemetry copy of a
message from the message.

---

## Selected architecture

**One change: grow the existing literal set. Nothing else.**

```
COPILOT_EVENT_TYPES = {
    ...existing members, unchanged...
    + the 13 measured types, inserted in the file's existing ordering style
}
```

Trusted components before: `COPILOT_EVENT_TYPES`, `validate_native_schema`, the
five semantic readers. Trusted components after: identical. No new function, no
new constant, no new module, no new caller, no new configuration surface.

### Decision — no shallow shape validation is added

The current Copilot boundary validates `type` and nothing else; the deeper
per-type shape checks in `validate_native_schema` are explicitly guarded by
`vendor == "claude"` (`:5237`) and `vendor == "codex"` (`:5258` onward). Adding
a `data`-must-be-an-object requirement for the 13 would be inventing a schema
unsupported by evidence, and would risk refusing a legitimately dataless event.
Structural safety is established by G1 measurement plus the invariance checks,
not by a guessed shape rule.

Envelope validation is unchanged and still applies: `native_objects`
(`:5170-5190`) still refuses non-JSON lines and non-object events, and the
`{"events": [...]}` unwrap (`:5220-5228`) still refuses a malformed nested list.

### Decision — no version registry

`cli_version` is *recorded* in identity and attestation, not *consulted* by the
validator. One supported host, one supported CLI, one caller. A registry would
add a component to serve a single current consumer, which the reuse contract
forbids. The vocabulary is sealed by adapter bytes (L10) and the recorded
`cli_version` / `cli_executable_sha256` make any run's vocabulary attributable
after the fact.

### Decision — the S-family and M-family are admitted together

Splitting them has no operational value: `model.*` types are emitted on every
run, so deferring any one of the 13 leaves the boundary broken. Admission is
all-13-or-none. This is exactly why G1 must precede implementation — if G1 shows
a non-inert type, the additive architecture is *not* sufficient and this work
order stops rather than shipping a partial admission.

### If G1 shows nesting — the declared stop

Do not weaken an assertion, do not special-case the offending type, do not add a
body filter. Stop, record the measurement, and open a Round 2 design for bounded
semantic scoping. Reframe stays OPEN.

---

## Reuse contract

| Need | Reused | Not built |
|---|---|---|
| Admission | existing `validate_native_schema` | second parser |
| Vocabulary | existing `COPILOT_EVENT_TYPES` literal | registry, map, pattern matcher |
| Version identity | existing `evaluation_cli_version`, `native_identity` | version-keyed vocabulary |
| Refusal proof | existing `test_unknown_native_schema_cannot_hide_activation` | new sentinel framework |
| Fixtures | existing fixture CLI in `test-skill-evaluation-vendor-adapters.sh` | new test harness |
| Byte pin | existing `TRUSTED_AUTHORING_ADAPTER_SHA256` + existing ratchet | new pin |

---

## Source-to-runtime data flow

```
Copilot CLI 1.0.82-1  --json  →  stdout JSONL
        │
        ▼
native_objects (:5170)            refuses non-JSON / non-object      [unchanged]
        │
        ▼
validate_native_schema (:5203)    type ∈ COPILOT_EVENT_TYPES         [set grows by 13]
        │                         else unsupported-native-schema      [unchanged]
        ▼
native_model (:5275)              model.call_start | session.start | session.model_change
native_detailed_usage (:5344)     model.call_start → turns; data.outputTokens; usage/token_usage
native_token_usage (:5310)        assistant.message.data.outputTokens; usage/token_usage
native_skill_evidence (:5434)     session.skills_loaded | tool.execution_complete
                                  | skill.invoked | assistant.message
        │   all four descend through recursive_values (:5193)         [unchanged]
        ▼
records: dreaming.execution, dreaming.native × N, dreaming.usage (:5861-5875)
        │
        ▼
raw trial output written                                              [unchanged, larger]
```

Second, independent path:

```
~/.copilot session-state JSONL
        │
        ▼
_copilot_events (:620)            type ∈ COPILOT_EVENT_TYPES          [set grows by 13]
                                  else unsupported-source-schema      [unchanged]
        │
        ▼
mapping (:626) → unmapped types skipped at :644                       [13 admitted types map to nothing]
```

---

## Threat and failure model

| # | Threat | Mechanism | Mitigation | Proof |
|---|---|---|---|---|
| T1 | Usage inflation | nested `usage` / `token_usage` / `data.outputTokens` inside an admitted telemetry event, summed by `native_token_usage` / `native_detailed_usage` | measured inertness; invariance under insert/remove/reorder/duplicate | G1, CHK-5, CHK-6 |
| T2 | Phantom turns | nested `type: "model.call_start"` inside an admitted event | same | G1, CHK-6 |
| T3 | Phantom tool calls | nested `data.toolRequests` or a nested `tool.execution_complete` | same | G1, CHK-6 |
| T4 | Exact-model bypass or false conflict | nested `data.model` under `model.call_start` / `session.start` / `session.model_change` shapes; `native_model:5303` raises on >1 identity | same | G1, CHK-5 |
| T5 | Phantom activation | nested `session.skills_loaded` / `skill.invoked` / `assistant.message` with a matching `toolCallId` | same | G1, CHK-7 |
| T6 | Boundary becomes a passthrough | over-broad edit (prefix, wildcard, `startswith`) | exact literal members only; grep assertion | CHK-3, CHK-11 |
| T7 | Unknown type stops refusing | accidental relaxation of `not in` | sentinel unchanged and green | CHK-3 |
| T8 | Malformed envelope admitted | `native_objects` or the nested-`events` unwrap relaxed | untouched; explicit checks | CHK-4 |
| T9 | Transcript boundary silently widened | shared constant, unnoticed second consumer | declared, asserted | CHK-8 |
| T10 | Evidence bloat / output-limit regression | snapshot events are large; every event is retained verbatim | existing `executor-output-limit` unchanged | CHK-9 |
| T11 | Content exposure in retained evidence | snapshot/message telemetry may duplicate prompt and response text into raw output | already true for `assistant.message`; retained evidence is candidate-scoped; no new credential surface | CHK-10 |
| T12 | Stale adapter pin | adapter bytes change | recompute pin; ratchet | CHK-12 |
| T13 | Pin collision with the frozen auth candidate | both candidates edit adapter bytes and the same pin | sequential integration, recompute after merge | closure section |

Failure modes that remain **fail-closed and unchanged**: unknown type, malformed
JSON, non-object event, malformed nested envelope, exact-model unproved, usage
unproved, token-limit unproved, executor output limit.

---

## Hard invariants

- **I1.** `skill-evaluation-harness.py` is byte-identical to its integration base.
- **I2.** Admission remains exact literal set membership. No prefix, no
  wildcard, no regex, no `startswith`, no namespace rule, no passthrough.
- **I3.** An event type outside the set still raises `unsupported-native-schema`
  with message `copilot:<type>`, before any raw output is written.
- **I4.** `SUPPORTED_SOURCE_VERSIONS`, `adapter_version`, identity keys,
  `sandbox_id` inputs, and attestation payload shapes are unchanged.
- **I5.** Claude and Codex vocabularies and shape validation are unchanged.
- **I6.** For any evidence stream, removing all 13 newly admitted event types
  yields byte-identical `native_model`, `native_token_usage`,
  `native_detailed_usage`, and `native_skill_evidence` results. This is the
  central invariant; it is what "metadata is not evidence" means operationally.
- **I7.** Inserting, reordering, or duplicating newly admitted events does not
  change those four results.
- **I8.** `turns` continues to count `model.call_start` only, never
  `model.model_call_started` or `model.turn_started`.
- **I9.** No newly admitted type is added to any semantic match set.
- **I10.** The change is additive only: no existing member is removed or renamed.
- **I11.** Both consumers of `COPILOT_EVENT_TYPES` are declared and asserted; no
  third consumer exists.

---

## Observable acceptance criteria

- **AC-1.** A fixture stream reproducing all 29 observed types is accepted by
  `validate_native_schema` for vendor `copilot`.
- **AC-2.** Each of the 13 types, presented individually in an otherwise minimal
  valid stream, is accepted — 13 separate assertions, not one bulk assertion.
- **AC-3.** A sentinel type absent from the set is still refused with
  `unsupported-native-schema`, and no raw output file exists afterwards.
- **AC-4.** A malformed envelope — non-JSON line, non-object event, and
  `{"events": <not a list of objects>}` — is still refused.
- **AC-5.** With all 13 present, `native_model` returns the exact configured
  model and does not raise `exact-model-unproved`.
- **AC-6.** With all 13 present, `native_detailed_usage` and
  `native_token_usage` return results identical to the same stream with all 13
  removed, including `turns` and `tool_calls`.
- **AC-7.** With all 13 present, `native_skill_evidence` returns evidence
  identical to the same stream with all 13 removed.
- **AC-8.** AC-5 through AC-7 also hold when the 13 are duplicated and when they
  are reordered relative to the semantic events.
- **AC-9.** A session transcript file containing the 13 types is accepted by
  `_copilot_events` and projects the same transcript as the same file with them
  removed.
- **AC-10.** A real adapter run against the current CLI reaches an
  `assistant.message` trace and a successful trial result, with no
  `unsupported-native-schema`.
- **AC-11.** AC-1 through AC-9 pass with no account authentication, no
  credential projection, and no model call — fixture-only.
- **AC-12.** No prefix, wildcard, regex, or membership-by-pattern construct
  appears in the diff.
- **AC-13.** Claude and Codex fixture behaviour is unchanged.
- **AC-14.** The adapter byte pin is recomputed and every consumer of it passes.

---

## Guards that must precede implementation

### G1 — redacted structural inertness probe (required, blocking)

**This guard must run and pass before any line of `COPILOT_EVENT_TYPES` is
edited.** It is the measurement that closes the reframe.

Method:

1. Execute one real Copilot run using the existing adapter executor path under
   the already-implemented trial authentication boundary, capturing raw JSONL to
   a private root outside the repository.
2. For every event, compute a **structural skeleton**: the set of dotted
   key-paths present, with all scalar values discarded. Retain skeletons and
   per-type counts only. Never retain values.
3. For each of the 13 types, assert that its skeleton contains **no** nested
   occurrence of any semantic sentinel:
   - any nested object whose `type` is a member of
     `{model.call_start, session.start, session.model_change,
       session.skills_loaded, tool.execution_complete, skill.invoked,
       assistant.message}`;
   - any nested `usage` or `token_usage` object;
   - any nested `data.outputTokens`, `data.toolRequests`, or `data.model`;
   - any nested `data.skills` list.
4. Independently, differentially confirm the same conclusion by running the four
   semantic readers over the captured stream with and without the 13 types and
   comparing results. This is the behavioural form of I6 measured on real data
   rather than a fixture.

Outcome handling:

- **All 13 inert** → reframe moves to CLEAR, implementation is authorized,
  and the captured skeletons become the fixture source of truth.
- **Any type non-inert** → **stop**. Record which type, which sentinel path, and
  the differential delta. Do not implement. Open a Round 2 design.

G1 output is a redacted artifact under the session proof root, never in the
repository, and must be scanned for credential markers and prompt content before
retention.

### G2 — harness purity (inherited, non-blocking here)

`test-skill-evaluation-harness.sh` already ratchets the harness digest. It must
remain green; this work order changes no harness byte (I1).

---

## Check contract

Every check names its layer, the artifact it asserts on, and whether it needs a
real model.

| ID | Layer | Check | Fixture-only? |
|---|---|---|---|
| CHK-1 | L1/L2 | 29-type fixture stream accepted by `validate_native_schema` for `copilot` | yes |
| CHK-2 | L1/L2 | 13 individual acceptance assertions, one per newly admitted type, each in an otherwise minimal valid stream | yes |
| CHK-3 | L2/L9 | existing `test_unknown_native_schema_cannot_hide_activation` still green, unmodified; sentinel still refused; no raw file written | yes |
| CHK-4 | L2 | malformed envelopes still refused: non-JSON line, non-object event, `{"events": 3}`, `{"events": [1]}` | yes |
| CHK-5 | L4 | `native_model` with all 13 present returns the exact model and does not raise `exact-model-unproved`; and returns the identical value with the 13 removed | yes |
| CHK-6 | L5/L6 | `native_token_usage` and `native_detailed_usage` byte-identical with and without the 13, including `turns` and `tool_calls`; and `turns` unchanged when `model.model_call_started` / `model.turn_started` are duplicated | yes |
| CHK-7 | L7 | `native_skill_evidence` identical with and without the 13, on a stream that does contain a genuine activation | yes |
| CHK-8 | L3 | `_copilot_events` accepts a transcript containing the 13 and projects the identical transcript as the same file with them removed | yes |
| CHK-9 | L8 | `executor-output-limit` and token-limit behaviour unchanged; a flooded stream still refuses | yes |
| CHK-10 | L8 | retained raw output for a 13-bearing stream contains no credential marker; retention scan clean | yes |
| CHK-11 | L1 | diff contains only literal string members; grep proves no `startswith`, no `re.`, no `*`, no prefix comparison introduced near the vocabulary or the validator | yes |
| CHK-12 | L10/L11 | `TRUSTED_AUTHORING_ADAPTER_SHA256` recomputed; `test-evaluation-input-source-builder.sh` ratchet green; consumers at `skill-evaluation.py:439`, `:5114`, `:5278` accept | yes |
| CHK-13 | L5/L12 | Claude and Codex fixtures unchanged and green; `SUPPORTED_SOURCE_VERSIONS` unchanged | yes |
| CHK-14 | L4/L12 | executor identity and attestation payload keys byte-identical to base for an unchanged run | yes |
| CHK-15 | real | real adapter run against current CLI reaches `assistant.message` and a successful trial result with no `unsupported-native-schema` | **no** |
| CHK-16 | G1 | structural inertness probe passes for all 13, with the differential reader comparison | **no** |

CHK-1 through CHK-14 are auth-independent, model-independent, and must pass in
CI-equivalent form. CHK-15 and CHK-16 require the real CLI and the already-shipped
authentication boundary.

**Ordering:** CHK-16 (G1) first and blocking. Then CHK-1..CHK-14. Then CHK-15.

### Fixture derivation rule

Fixtures for CHK-1..CHK-9 are derived from the G1 skeletons — real key-paths,
synthetic values. They are not hand-invented shapes, and they carry no captured
content.

---

## Rollback and fail-closed evidence

- **Primary rollback:** revert the 13 additions to `COPILOT_EVENT_TYPES` and
  recompute the adapter pin. The boundary returns to explicit refusal with the
  identical error code and message. There is no durable schema migration, no
  persisted state, no config surface, and no artifact format change to unwind.
- **Fail-closed direction:** the reverted state *refuses more*, never less. A
  rollback cannot open a hole.
- **Negative evidence:** CHK-3 is the standing proof that removing a type from
  the set restores refusal, because the sentinel type is permanently outside it.
- **Retained evidence compatibility:** raw outputs produced while the additions
  were live remain readable; nothing re-validates historical raw output against
  the vocabulary.

---

## Digest, regeneration, and consumer closure

Exact, because this is the layer most often left half-done.

1. `dreaming-vendor-adapter.py` bytes change. Base digest at `f63f55b`:
   `cb77c7945f8efe858b3d2f2d1e3b28527cfe7590257abe95ec1421d72aaace22`.
2. The **only** committed pin of those bytes is
   `TRUSTED_AUTHORING_ADAPTER_SHA256` (`skill-evaluation.py:88-90`). Recompute
   it in the same commit.
3. Consumers of that pin: `skill-evaluation.py:439` (authoring adapter
   admission), `:5114` (adapter entry check), `:5278` (repair adapter check).
   All three compare equality against the same constant, so a single correct
   recomputation closes all three. Each must be exercised, not assumed.
4. `test-evaluation-input-source-builder.sh:165` recomputes the constant from
   the adapter file by regex and is therefore self-healing; it is the ratchet
   that detects a *forgotten* recomputation.
5. Evaluation input source bundles are content-addressed and built at runtime,
   not stored in the repository. No bundle regeneration is required. Verify by
   confirming no committed artifact embeds the adapter digest other than
   `skill-evaluation.py`.
6. `skill-evaluation-harness.py` is unchanged; its digest pin
   (`test-skill-evaluation-harness.sh`) must remain green untouched.
7. **Preserved groups:** no identity key, no `adapter_version`, no `sandbox_id`
   input, no generated config, no installed config, no launchd artifact changes.
   Do not blanket-regenerate installed configuration.
8. **Integration ordering with the frozen auth candidate.** The candidate at
   `dreaming-shadow-trial-auth` `d26adac` also changes adapter bytes and the same
   pin. The two cannot both hold a correct pin independently. Integrate
   sequentially: merge one, recompute the pin against the merged adapter bytes,
   then merge the second and recompute again. A merge that carries a pin
   computed against pre-merge bytes is a defect the ratchet will catch — and it
   must be caught before, not after.

---

## Definition of Done: Copilot native event schema compatibility

This slice is done when, and only when, all of the following hold. This DoD is
specific to this work order and is not reusable boilerplate.

1. **G1 has run and passed.** A redacted structural inertness probe over a real
   Copilot `1.0.82-1` run shows that none of the 13 admitted types carries a
   nested semantic sentinel, and the differential reader comparison over real
   captured data confirms it. The reframe record is updated from OPEN to CLEAR
   with the artifact path recorded and no captured content retained.
2. `COPILOT_EVENT_TYPES` contains exactly its previous 56 members plus exactly the
   13 measured types, i.e. 69 literal members, as literal strings, with no other edit to the file's
   vocabulary section.
3. `validate_native_schema` is unmodified.
4. No semantic match set gained a member; `native_model`,
   `native_token_usage`, `native_detailed_usage`, `native_skill_evidence`, and
   `recursive_values` are unmodified.
5. CHK-1 through CHK-14 pass. The 13 per-type acceptance assertions exist
   individually.
6. I6 is proved by executed test, not argued: the four semantic readers return
   identical results with the 13 present and absent, and under duplication and
   reordering.
7. `test_unknown_native_schema_cannot_hide_activation` is green and its source
   is unchanged.
8. `_copilot_events` acceptance and projection invariance is asserted (CHK-8),
   so the second boundary is not silently widened.
9. The adapter pin is recomputed, all three pin consumers pass, and the builder
   ratchet is green.
10. Claude, Codex, harness, and identity surfaces are byte-unchanged and their
    suites are green.
11. CHK-15 passes: a real adapter run on the current CLI produces a successful
    trial with a real `assistant.message` trace and no
    `unsupported-native-schema`.
12. The diff contains no pattern-based admission, no unknown-event fallback, no
    registry, no version map, and no new file.
13. The commit is local, on `feature/copilot-native-event-schema`, with the
    required trailers. Nothing is pushed, published, installed, or merged.
14. The integration-ordering note (closure item 8) is carried into whatever
    integrates this candidate alongside `d26adac`.

Anything less — in particular, implementing before G1, or admitting a subset —
is not this slice and must not be recorded as done.
