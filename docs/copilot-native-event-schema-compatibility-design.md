# Copilot native event schema and usage authority — critical work order (Round 3)

Status: reviewed Round-3 work order. Implementation may resume only through
`development-loop`.

Base: `f63f55b` (`feature/copilot-native-event-schema`).
Current implementation predecessor: `540fafb`.
Owner component: `skills/skill-review/scripts/dreaming-vendor-adapter.py`.
Observed subjects: GitHub Copilot CLI `1.0.82-1` and `1.0.82-2`.

Round 1 (`f604d49`) proposed a purely additive vocabulary change. The blocking
guard G1 was executed before implementation and **falsified that premise**.
Round 2 became the executable measured-usage work order and resolved the four
paired Round-1 review findings — Terra **T1** and Opus **E1**, **E2**, **E3** —
recorded in full at the end of the document.

Round 3 is a narrow compatibility reframe triggered during the post-review live
proof of `540fafb`. Copilot CLI updated in place from `1.0.82-1` to `1.0.82-2`;
one run correctly refused the newly emitted exact type
`session.managed_settings_resolved`, while later runs that did not emit it
completed. The public generated Copilot SDK schema identifies that event as an
ephemeral managed-policy provenance snapshot. Its declared payload contains
booleans, `managedKeys`, `source`, and optional `settings` below
`data.settings`; it has no declared Shape A or Shape B usage path. This revision
adds that one measured type as opaque metadata and adds a nested-decoy guard
against the optional settings payload. It does not reopen the usage architecture.

---

## Objective

Restore a complete Copilot trial result and a complete comparator result on
Copilot CLI `1.0.82-2` by (a) admitting the exact 14 observed event types into
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
11. Compatibility with unobserved or future CLI versions after `1.0.82-2`.
    Refusal on a future unknown type remains the designed signal.
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

### Round-3 drift observed on `1.0.82-2`

The first successor proof after implementation review refused before raw output:

```
unsupported-native-schema: copilot:session.managed_settings_resolved
```

The exact same frozen adapter later completed against `1.0.82-2` when that
ephemeral event was not emitted, establishing that this is an intermittent
startup-policy event rather than a new mandatory usage carrier.

The public generated Copilot SDK contract supplies the missing shape evidence:

- event type: `session.managed_settings_resolved`;
- purpose: an ephemeral snapshot of effective enterprise managed settings and
  contributing channels, emitted when policy is applied at session start,
  resume, or account switch;
- required data: `bypassPermissionsDisabled`, `deviceManaged`, `failClosed`,
  `managedKeys`, `serverManaged`, and `source`;
- optional data: `clientManaged`, `permissionsAllowIntersected`,
  `sandboxEnabledByUndeterminedPolicy`, and `settings`;
- the potentially nested effective policy is at `data.settings`, not at
  `<event>.usage`, `<event>.data.usage`, `<event>.data.outputTokens`, or
  `data.responseChunk.usage`.

Evidence:
`github/copilot-sdk` generated `SessionManagedSettingsResolvedData` in
`python/copilot/generated/session_events.py` and
`rust/src/generated/session_events.rs`, plus its cross-language serialization
fixtures. A bounded four-run fresh-home probe did not reproduce the ephemeral
event and retained no prompt, policy value, credential, or raw JSONL. The
schema, combined with the already-implemented outer-event iterator, is enough
to classify it as opaque non-usage metadata: even an adversarial nested
`data.settings` object is not traversed by any Copilot reader.

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
| Legacy Shape A streams must keep succeeding | fixture `test-skill-evaluation-vendor-adapters.sh:172-219` emits `data.outputTokens`, `data.usage`, and `session.usage_checkpoint.usage` | If those fixtures are retired deliberately. Their *numbers* are not a constraint — see A5 and finding E1. |
| Shape A and Shape B declared paths are disjoint | Shape B lives at `data.responseChunk.usage`, two levels below `data`; G2 measured the only Shape-A-path `usage` dicts on real streams as belonging to `result`, with no token field | If a CLI version places call usage at `data.usage`. |
| G2 observed only all-success turns | both G2 streams exited 0 with `model.call_start` == `model.model_call_success` (1==1, 4==4) | On the first capture containing a failed, retried, or cancelled model call. Until then the design refuses such streams rather than modelling them (finding E2). |
| `id` stability is proved for `model.model_call_success` only | G2 recorded `id_present`/`ids_all_distinct` for the 5 success events; it recorded nothing about `model.call_start` ids | If a capture proves a stable id on `model.call_start`; only then may turns be deduplicated rather than counted. |
| Managed-settings resolution is metadata, not usage | Copilot SDK generated schema places policy provenance and optional effective settings under `data`, with no declared Shape A or Shape B usage path | If a later generated schema adds a token field at one of the declared usage paths, or if the CLI emits a different managed-settings event type. |

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

**2. Which constraint creates the blocker, and where did it come from?**
The exact-literal vocabulary is a fail-closed policy boundary: every transport
event must be reviewed before it can enter the adapter. It first refused 13
measured `1.0.82-1` types and, during successor proof, correctly refused one
newly measured `1.0.82-2` policy-provenance type. Behind the original refusal,
the inherited generic recursive `usage` scan was the sole accidental source of
Copilot token numbers and returned no detailed usage. The constraint remains
binding, but the assumption that its 69-member reviewed vocabulary was complete
for the current CLI was invalidated by the exact live refusal.

**3. What concrete invariant fails if the constraint changes?**
Weakening exact admission would allow an unreviewed future event shape to reach
the adapter without a design decision, violating I2's reviewed-vocabulary
boundary and the no-raw-output refusal contract. Leaving the reviewed
managed-settings event excluded fails the supported-caller invariant instead:
an otherwise valid `1.0.82-2` run intermittently cannot produce either a trial
result or comparator verdict even though the generated schema proves that event
claims no semantic or token authority. The invariant-preserving change is one
literal member plus an inertness guard, not a weaker admission rule.

**4. What is the simplest design without the invalidated constraint?**
Treat the predecessor's 69-member set as measured history rather than a complete
current-version list. Add only `session.managed_settings_resolved` to the
existing set and prove its complete generated-schema shape inert under the
already-implemented outer-event readers. The Round-2 Shape A/Shape B usage
architecture remains unchanged.

**5. Which option has fewer trusted components and maintenance surfaces?**
The selected 69 → 70 literal edit plus one fixture reuses the existing
validator, iterator, readers, error surfaces, tests, and digest pin. Prefix
admission, unknown-event passthrough, a version-keyed registry, or a
managed-settings parser would each add a trusted rule or owner and protect no
supported behavior.

**Revisit conditions.**
A CLI version where usage is cumulative, where transport events lack stable
ids, where the usage object appears nested inside another admitted event, where
a second independent native token total appears, where a generated
managed-settings schema adds a declared usage path, or where another exact
managed-settings event is emitted reopens this record.

The selected Round-3 architecture is otherwise ready to become **CLEAR**. G2
establishes a deterministic, order-safe aggregation
rule — sum per-call `prompt`/`completion`/`total` over transport events
deduplicated by stable `id`, with a completeness check against outer
`model.call_start` — and the legacy shape is expressible as one closed canonical
parser over three declared paths in the same owner. No new subsystem, service,
registry, version map, or module is required, and no caller outside the existing
two learns anything new. CLEAR is contingent on exactly that: if the canonical
Shape A parser could not be expressed inside `dreaming-vendor-adapter.py`
without a new component, this would return to OPEN.

The Round-3 trigger is closed by the revised work order, not by a permissive
implementation: the exact type is measured from the refused live run, its
payload authority comes from the public generated SDK schema, and the new guard
places recursive type-, tool-, trace-, and usage-shaped decoys inside
`data.settings`. Round-1 paired review of `b3c05a5` found two material work-order
defects: the reframe record did not answer the governing invariant-failure
question, and G-A incorrectly described all 14 additions as red at predecessor
`540fafb`. Commit `4fe282d` repaired both. Claude Opus 5 and GPT-5.6 Terra each
completed the bounded fix-verification round, applied the design scope lens,
resolved both findings, and reported no fix-delta finding. That paired evidence
changes the durable status to **CLEAR**. Any new event name or any
managed-settings token field at a declared usage path reopens it.

---

## Enumeration of enforcement layers

| # | Layer | Location | Effect |
|---|---|---|---|
| L1 | Exact vocabulary set | predecessor `540fafb`, 69 members | Grows 69 → 70 by adding only `session.managed_settings_resolved`; cumulatively the set is the original 56 plus the 14 measured additions. |
| L2 | Native admission | `validate_native_schema:5203-5236`; callers `:4279`, `:5813` | Unchanged code; cumulative refusal set shrinks by 14 from the original base, and by only the managed-settings event from predecessor `540fafb`. |
| L3 | Session transcript admission | `_copilot_events:620-642`, `unsupported-source-schema` | Same constant, second consumer, widened by the same one-member Round-3 delta. Its `mapping` (`:626-634`) has no entry for any of the 14 cumulative additions and unmapped types are skipped at `:644`, so projection is unchanged — asserted, not assumed. |
| L4 | Model identity | `native_model:5275-5308` | Copilot branch scoped to outer events. Measured identical (single 1→1, multi 1→1 distinct models). Claude/Codex untouched. |
| L5 | Token usage | `native_token_usage:5310-5342` | Copilot branch replaced by the new authority. Claude/Codex generic path untouched. |
| L6 | Detailed usage, turns, tool calls | `native_detailed_usage:5344-5432`; `turns += 1` at `:5359` | Copilot usage replaced by the new authority; turns stay exact outer `model.call_start`; tool calls stay exact outer `data.toolRequests`. Measured identical outer-vs-recursive (0→0, 3→3). |
| L7 | Skill activation evidence | `native_skill_evidence:5434-5713` | Copilot scans scoped to outer events. Measured identical (1→1, 7→7). |
| L8 | Final trace | `normalized_native_events:5956-6022` (**omitted in Round 1**) | Copilot branch scoped to outer events. Measured identical (2→2, 14→14). |
| L9 | Comparator recursive gate | `:4289-4311` (**omitted in Round 1**) — recursive `event_types`, `tool_event` prefix test, `model.call_start` count must be exactly 1 | Scoped to outer events. Measured identical (tool-prefix hits 0→0, 158→158; call-start count 1→1, 4→4). Consumes the same usage authority as the run. |
| L10 | Failure message extraction | `native_failure_message:5716-5748` | Copilot branch matches `session.error`/`error`. Scoped to outer events; no measured divergence. |
| L11 | Raw evidence retention | `evaluation_run` records `:5861-5875`, per-event at `:5870` | Every admitted event retained verbatim; raw output grows. `executor-output-limit` unchanged. |
| L12 | Sentinel refusal test | `test-skill-evaluation-vendor-adapters.sh:1424-1428`, fixture sentinel at `:128` | Must stay green, unmodified. |
| L13 | Legacy fixture usage shape | fixture `:172-219` | Replaced by one canonical Shape A authority. Every legacy stream that succeeds today still succeeds and `TOKENOVER` still exceeds; the numbers migrate to the exact values tabulated in A5. |
| L14 | Adapter byte pin | `skill-evaluation.py:88-90`, consumers `:439`, `:5114`, `:5278` | Recompute. |
| L15 | Pin ratchet | `test-evaluation-input-source-builder.sh:165` | Self-healing; detects a forgotten recomputation. |
| L16 | Executor identity/attestation | `evaluation_cli_version:3978-3991`; `native_identity:6658` | Unchanged. |

Untouched: harness bytes, sandbox profile, credential projection, Claude/Codex
branches, `SUPPORTED_SOURCE_VERSIONS`.

---

## Selected architecture

### A1 — Vocabulary: add exactly 14 literal members

`COPILOT_EVENT_TYPES` grows 56 → 70. Literal strings only. No prefix, wildcard,
regex, passthrough, or registry. `validate_native_schema` itself is unmodified.

The fourteenth member is `session.managed_settings_resolved`. It remains opaque:
no match set or semantic reader consumes it, and its optional `data.settings`
payload is never recursively traversed.

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

**Declared-path access is not recursion.** Reading a named field inside an outer
event — `data.model`, `data.toolRequests[].toolCallId`, `data.skills[]`, and the
three Shape A usage paths `<event>.usage`, `<event>.data.usage`,
`<event>.data.outputTokens` — is exact declared access, and every reader keeps
doing it (invariant I6b). What A2 removes is only the recursive descent into
arbitrary nested bodies, which is what let a snapshot's copy of a message look
like a top-level event.

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
7. **Completeness.** Shape B is *engaged* when at least one outer
   `model.model_call_success` exists. When engaged, the number of distinct usage
   ids must equal the number of outer `model.call_start` events; otherwise
   `copilot:usage-incomplete`. (G2: 1 == 1 and 4 == 4.)
8. Return `None` only when **no** outer `model.model_call_success` exists at all
   — the Shape A path then applies. Once engaged, Shape B either proves the
   complete budget or refuses; it never partially degrades to Shape A, and
   malformed or contradictory usage never degrades to Shape A.

No new error *code* is introduced: the existing `usage-unproved` code carries an
exact machine-readable reason string, so no consumer surface widens.

**A started call without a success refuses (Round-1 finding E2).**
G2 measured **only all-success turns** — 1/1 and 4/4, both runs exiting 0. This
design therefore makes no claim about failure, retry, or cancellation paths. An
outer `model.call_start` with no distinct matching `model.model_call_success`
yields `copilot:usage-incomplete` and refuses the trial *and* the comparator,
whatever the cause — provider error, silent retry, or cancellation — because the
system cannot prove the complete token budget it is gating on. This is intended
fail-closed behaviour, not a compatibility promise, and it is deliberately
stricter than "the run produced an answer".

Whatever event a failed or cancelled call emits on this CLI version has not been
observed. It is therefore still outside `COPILOT_EVENT_TYPES` and still raises
`unsupported-native-schema` before any raw output is written. That explicit
refusal is the designed signal to return to design with a fresh capture. **No
failure event name is invented here**, and **no claim of failure-path vocabulary
coverage is made.**

**Scope of `id` evidence.** G2 recorded stable, distinct, duplication-preserving
top-level ids on `model.model_call_success` (5 of 5 events across both streams).
It recorded **nothing** about ids on `model.call_start`. Turns therefore remain a
positional count of outer `model.call_start` (`:5359`, rule unchanged), and only
success events are deduplicated. One consequence is stated here rather than
discovered later: duplicating an entire stream doubles the `model.call_start`
count while the id-keyed usage set stays deduplicated, so the completeness rule
refuses. That is fail-closed and correct — a whole-stream duplicate is not
evidence of one trial — and it bounds the "duplication-safe" property to the
usage sum itself (AC-11), not to arbitrary stream surgery.

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

**Declared semantic change on the Shape B path: `max` → `sum`.** Today Copilot
totals are `max(totals)` (`:5414`, with `max(inputs)` at `:5410`). Per-call usage
must be summed to represent a trial: on the G2 multi run that is 37467 rather
than 9524. This is an intended correction of a gate that exists to bound spend,
and it is asserted by AC-10 rather than left to be discovered.

This paragraph is scoped to Shape B. It says nothing about the legacy fixtures,
whose numbers change for a different and independent reason set out in A5. The
Round-1 claim that "every existing fixture is unaffected because sum equals max
with one contributor" is **withdrawn** and must not be carried forward.

### A5 — Canonical Shape A, and the closed two-shape rule

**Withdrawn claim (Round-1 finding E1).** Round 1 promised that legacy Shape A
streams would keep producing "today's exact numbers, byte-for-byte". That
promise is impossible, because the two current readers already disagree on every
legacy fixture stream. Measured by loading the unmodified base adapter at
`f63f55b` and calling both functions directly:

| legacy fixture stream | today `native_token_usage` | today `native_detailed_usage` |
|---|---|---|
| `CURRENTCOPILOT`/`SHADOWCANDIDATE`/`SHADOWCATALOG`, candidate | **15** | 10 / **30** / **40**, turns 1, tool_calls 1 |
| the same, no candidate | **15** | 10 / 30 / 40, turns 1, tool_calls 0 |
| comparator branch | **5** | 10 / 5 / **15**, turns 1, tool_calls 0 |
| comparator branch, `FIXTURE_COMPARATOR_ZERO_TURN` | **5** | 10 / 5 / 15, turns 1, tool_calls 0 |
| run branch (no `outputTokens`) | **15** | **None** |
| `TOKENOVER` run branch | **1000** | **None** |

(triples are input / output / total.)

There is no single set of numbers to preserve. `output 30` is a plain double
count: the fixture's `data.outputTokens` (15) and its `data.usage.output_tokens`
(15) both append to `outputs`, at `:5364` and `:5406`. And `native_token_usage`
returns bare output tokens whenever any exist (`:5339-5340`), which is why it
answers 5 on a stream whose declared total is 15.

So Shape A receives the same treatment as Shape B: **one canonical parser, one
authority, both readers converging on it.** The existing fixtures are
*compatibility inputs* — every stream that succeeds today must still succeed and
budget enforcement must still fire — and are **not** a numeric authority.

```
copilot_legacy_usage(values) -> dict | None      # Shape A authority
```

**Declared paths, outer events only.** Exactly three, each inside an outer
event: `<event>.usage`, `<event>.data.usage`, `<event>.data.outputTokens`. No
recursion, no search, no other path. They are disjoint from Shape B's
`model.model_call_success.data.responseChunk.usage`, which sits two levels below
`data` and is therefore invisible to Shape A — checked against the real captured
events, where the only Shape-A-path `usage` dict belongs to `result`.

**Per-mapping normalization.** For each `usage` mapping found at a declared
path:

- `input` = `input_tokens` | `inputTokens`, plus `cache_creation_input_tokens`
  and `cache_read_input_tokens` when present, preserving the existing rule at
  `:5393-5403`;
- `output` = `output_tokens` | `outputTokens`;
- `total` = `total_tokens` | `totalTokens`.

Every field that is present must be a non-bool `int` and `>= 0`, else
`copilot:usage-malformed`. Then, per mapping:

- `input` **and** `output` present → a **pair authority**. A present `total`
  must equal `input + output`, else `copilot:usage-malformed`; otherwise
  `total := input + output`.
- else `total` present → a **total authority**; components are *unclaimed*.
- else exactly one of `input`/`output` present → `copilot:usage-incomplete`.
- else no recognised token field at all → **not an authority; ignored.**

That last rule is not a convenience loophole. The already-admitted real `result`
event carries a `usage` dict of `codeChanges`, `premiumRequests`,
`sessionDurationMs`, `totalApiDurationMs`, measured in both G2 streams, and it
must not refuse a valid run.

**Agreement and dedupe — no silent `max` across mixed sources.**

- All pair authorities must carry an identical `(input, output, total)` triple;
  otherwise `copilot:usage-ambiguous`.
- All total authorities must carry an identical `total`; otherwise
  `copilot:usage-ambiguous`.
- A pair authority and a total authority must agree on `total`; otherwise
  `copilot:usage-contradiction`.

**`data.outputTokens`.** Summed over the outer events that declare it, which
preserves the established Copilot semantic (`copilot_output_tokens +=` at
`:5324`; `sum(outputs)` at `:5411`), producing one `declared_output`. Zero
declaring events means *no observation*, which is distinct from an observation
of `0`.

**Resolution.**

| authorities present | result |
|---|---|
| pair | `(input, output, total)`. A present `declared_output` must equal `output`, else `copilot:usage-contradiction`. |
| total only, `declared_output` present | `output := declared_output`; `input := total - declared_output`, which must be `>= 0` else `copilot:usage-incomplete`. |
| total only, no `declared_output` | `(None, None, total)` — the total is proved, the components are unclaimed. |
| none, `declared_output` present | `copilot:usage-incomplete`. An output count is not a token total. |
| none | `None`. No Shape A authority exists. |

The fourth row is a deliberate behaviour change: today `native_token_usage`
returns bare output tokens as though they were the whole usage (`:5339-5340`),
which under-reports the budget. No fixture exercises it.

A record whose components are unclaimed proves a **total** and nothing else.
`native_token_usage` returns that total; `native_detailed_usage` returns `None`,
exactly as it does today on the same streams, and the run's existing fallback at
`:5822-5832` still supplies the total to the budget gate.

**Expected fixture numbers after the migration.**

| legacy fixture stream | canonical Shape A derivation | `native_token_usage` | `native_detailed_usage` |
|---|---|---|---|
| `CURRENTCOPILOT` etc., candidate | pair (10, 15, 25); `declared_output` = 15 + 0 = 15 = output ✓ | **25** (was 15) | 10 / **15** / **25**, turns 1, tool_calls 1 (was 10 / 30 / 40) |
| the same, no candidate | as above | **25** (was 15) | 10 / 15 / 25, turns 1, tool_calls 0 |
| comparator branch | total 15; `declared_output` 5 → input 10 | **15** (was 5) | 10 / 5 / 15 — unchanged |
| comparator, `ZERO_TURN` | as above | **15** (was 5) | 10 / 5 / 15 — unchanged; the stream still fails the tool-free gate at `:4308-4316` as designed |
| run branch | total 15, no `declared_output` | **15** — unchanged | **None** — unchanged |
| `TOKENOVER` | total 1000, no `declared_output` | **1000** — unchanged | **None** — unchanged |

Every legacy stream that succeeds today still succeeds; `token-limit-exceeded`
still fires on `TOKENOVER` with the identical number; the double count is gone;
and the two readers now agree everywhere both produce a value. These migrated
numbers are the intentional correctness result. They are asserted directly by
AC-17 and CHK-16 as *new expected values* — not described as preserved, and not
hidden behind a compatibility claim.

The obsolete `assistant.message.data.outputTokens` dependency is **not removed**.
It survives inside canonical Shape A as an observation that must corroborate, or
complete, a declared usage mapping.

**Coexistence: full normalized triple equality (Round-1 finding T1).**

| condition | rule |
|---|---|
| Shape B absent entirely | Shape A is the authority. |
| Shape A absent entirely | Shape B is the authority. |
| both present, Shape A claims components | `(input, output, total)` must be equal **field by field**. Equal totals with unequal components refuse with `copilot:usage-contradiction`. |
| both present, Shape A is total-only | `total` must be equal, else `copilot:usage-contradiction`. Shape A makes no component claim, so there is no component to contradict. |
| neither | `None` → existing `usage-unproved`. |

Only **after** equality holds is Shape B selected as the returned authority, for
its per-call ids and its `model.call_start` relation. Comparing totals alone was
the T1 hole and is closed: a stream whose two shapes agree on 25 while claiming
`10 / 15` and `15 / 10` now refuses. The total-only row is recorded as an
explicit, separately checked sub-rule (CHK-27) rather than left implicit, so
that it cannot be mistaken for the hole it replaces.

No `max`, no precedence-by-magnitude, no fallback across a refusal. A shape that
refuses refuses the trial; it never yields to the other shape.

**Not observed in any real stream.** G2 found no Shape A declared path carrying
a token field on real `1.0.82-1` output. Coexistence is a fixture-and-future
condition, and it is fail-closed by construction.

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

New code is four things: 14 literal strings, one outer-event iterator, two
shape parsers (`copilot_call_usage`, `copilot_legacy_usage`), and the single
aggregation entry point `copilot_usage` that both readers call. No new module,
file, class hierarchy, service, or configuration surface.

---

## Source-to-runtime data flow

```
Copilot CLI 1.0.82-2 --output-format json  →  stdout JSONL
        │
        ▼
native_objects (:5170)                 refuses non-JSON / non-object   [unchanged]
        │
        ▼
validate_native_schema (:5203)         type ∈ COPILOT_EVENT_TYPES      [set 56 → 70]
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
        ├─► copilot_call_usage           Shape B: model.model_call_success
        │                                 .data.responseChunk.usage      [NEW]
        │                                 validate → dedupe by id → sum
        │                                 → completeness vs call_start
        └─► copilot_legacy_usage         Shape A: <event>.usage,         [NEW]
                                          <event>.data.usage,
                                          <event>.data.outputTokens
                                          normalize → agree → resolve
        │
        ▼
copilot_usage → { turns, input_tokens, output_tokens, total_tokens, tool_calls }
        │        A and B coexisting → full normalized triple must be equal
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
   → _copilot_events (:620)   type ∈ COPILOT_EVENT_TYPES  [set 56 → 70]
                              else unsupported-source-schema
   → mapping (:626) has no entry for the 14 → skipped at :644 → projection identical
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
| T6 | Two authorities disagreeing | Shape A and Shape B both present | full normalized triple equality required, not total alone, else refuse | CHK-17, CHK-27 |
| T7 | Intra-event contradiction | `data.responseUsage` diverging from `data.responseChunk.usage` | equality required when present | CHK-18 |
| T8 | Run and comparator disagreeing | separate derivations | both consume `copilot_usage` | CHK-19 |
| T9 | Phantom model / activation / trace / tool events | nested objects matching type predicates | outer-event iterator plus measured invariance | CHK-7, CHK-8, CHK-9, CHK-10 |
| T10 | Boundary becomes a passthrough | prefix or wildcard edit | literal members only; grep assertion | CHK-3, CHK-20 |
| T11 | Unknown type stops refusing | relaxed `not in` | sentinel unchanged and green | CHK-3 |
| T12 | Malformed envelope admitted | `native_objects` or the `{"events"}` unwrap relaxed | untouched, explicitly tested | CHK-4 |
| T13 | Transcript boundary silently widened | shared constant | declared and asserted | CHK-5 |
| T14 | Legacy streams broken | Copilot usage rewritten | canonical Shape A: every legacy fixture stream still succeeds, `TOKENOVER` still exceeds, and the migrated numbers are asserted exactly | CHK-16, CHK-30 |
| T15 | Claude/Codex regression | shared helpers touched | their branches use `recursive_values` unchanged; suites green | CHK-21 |
| T16 | Evidence bloat | snapshot events are large and retained verbatim | `executor-output-limit` unchanged | CHK-22 |
| T17 | Stale adapter pin | adapter bytes change | recompute; ratchet | CHK-23 |
| T18 | Pin collision with the frozen auth candidate | both change adapter bytes and the same pin | sequential integration with recomputation | closure §8 |
| T19 | Incomplete budget accepted | a model call starts and never reports usage — provider failure, silent retry, or cancellation | Shape B completeness: distinct usage ids must equal outer `model.call_start`; otherwise refuse. Deliberately unconditional on cause | CHK-26 |
| T20 | Mixed legacy sources silently maxed | several Shape A mappings disagree, or a pair and a total disagree | exact agreement within each authority class, then cross-class total agreement, else refuse. No `max`, no first-wins, no largest-wins | CHK-28 |
| T21 | An output count read as a total | `data.outputTokens` present with no usage mapping anywhere | refuses `copilot:usage-incomplete` instead of returning the output count | CHK-29 |
| T22 | Unobserved failure vocabulary pre-admitted | a guess at the event a failed call emits | no failure event name is invented; an unobserved type still raises `unsupported-native-schema` | CHK-3 |

Fail-closed and unchanged: unknown type, malformed JSON, non-object event,
malformed nested envelope, `exact-model-unproved`, `token-limit-exceeded`,
`executor-output-limit`.

Fail-closed and **newly stricter**: a model call with no reported usage (T19),
disagreeing legacy usage sources (T20), and a bare output count offered as a
total (T21). Each refuses where the base adapter would have produced a number.
None of the three is exercised by any existing fixture.

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
- **I6.** *Usage derivation only.* Copilot **usage** is derived exclusively
  from the declared paths named in A3 and A5 — Shape B's
  `model.model_call_success.data.responseChunk.usage` and its sibling
  `data.responseUsage`, and Shape A's `<event>.usage`, `<event>.data.usage`,
  `<event>.data.outputTokens`. No other object, at any depth, contributes to a
  token number. This invariant governs usage and nothing else.
- **I6b.** *Every other Copilot reader keeps its existing exact declared nested
  paths, and they are not prohibited.* Reading a declared field inside an outer
  event is the normal, intended mechanism; what A2 removes is only the
  **recursive descent into arbitrary nested bodies**. Specifically, and
  unchanged in behaviour:

  | reader | declared paths retained |
  |---|---|
  | `native_model` | `data.model` on `model.call_start`, `session.start`, `session.model_change` (`:5284-5285`) |
  | turns | outer `type == "model.call_start"` only (`:5358-5359`) |
  | tool calls | `data.toolRequests[].toolCallId` (`:5365-5370`) |
  | `native_skill_evidence` | `data.skills[]` (`:5524`), `data.toolCallId` and `data.result.content` (`:5544-5546`), `data.skillName`/`data.name` and `data.resolvedPath` (`:5602-5605`), `data.toolRequests[].toolCallId` (`:5610-5623`), tool-name check (`:5692`) |
  | `normalized_native_events` | the existing text and name fields (`:5943`, `:6001`, `:6015`) |
  | `native_failure_message` | its existing `session.error`/`error` message extraction (`:5716-5748`) |
  | comparator gate | outer `type` and its prefix test only (`:4289-4311`) |

  Each of these is applied to the outer event yielded by `copilot_outer_events`
  instead of to every recursively discovered dict. G1 and G2 measured the
  results identical on the real streams.
- **I7.** Copilot usage is a pure function of the id-deduplicated set of outer
  `model.model_call_success` events (Shape B) or of the declared Shape A paths —
  invariant under duplication, reordering, and interleaving of any *other*
  event. Duplicating `model.call_start` is not "any other event": it breaks
  Shape B completeness and refuses, by design (A3, T19).
- **I8.** `native_token_usage` and `native_detailed_usage` derive Copilot totals
  from the same authority and can never disagree.
- **I9.** Malformed, ambiguous, incomplete, or contradictory usage refuses. It
  never degrades to the other shape, never takes a `max` across sources, and
  never derives a total from an output count alone.
- **I9b.** `native_token_usage` and `native_detailed_usage` are permitted to
  differ in *shape* — an `int` versus a dict, and `None` where components are
  unclaimed — but never in the value of `total_tokens` when both produce one.
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
- **AC-2.** Each of the 14 types, presented individually in an otherwise minimal
  valid stream, is accepted — 14 separate assertions.
- **AC-3.** A sentinel type outside the set is still refused with
  `unsupported-native-schema`, and no raw output file exists afterwards.
- **AC-4.** Malformed envelopes are still refused: non-JSON line, non-object
  event, `{"events": 3}`, `{"events": [1]}`.
- **AC-5.** A transcript containing the 14 is accepted by `_copilot_events` and
  projects identically to the same file with them removed.
- **AC-6.** `copilot_outer_events` yields exactly the transport events: one per
  top-level value, or the members of a one-level `{"events": [...]}` envelope,
  and never any object nested inside an event body.
- **AC-7.** `native_model`, `native_skill_evidence`, `normalized_native_events`,
  and the comparator `event_types`/`tool_event`/`model.call_start`-count gate
  return results identical to the pre-change recursive implementation on the
  retained real streams.
- **AC-8.** Those same results are unchanged when the 14 are inserted, removed,
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
- **AC-17.** Each legacy Shape A fixture stream produces exactly the migrated
  numbers tabulated in A5 — 25 / 25 for the `CURRENTCOPILOT` family, 15 / 15 for
  the comparator branch, 15 and `None` for the run branch, 1000 and `None` for
  `TOKENOVER` — and the two readers agree on `total_tokens` wherever both
  produce one. These are asserted as new expected values.
- **AC-18.** A stream carrying both shapes accepts only when the **full
  normalized triple** is equal field by field; a stream whose two shapes agree
  on `total_tokens` but disagree on `input_tokens` or `output_tokens` refuses
  with `copilot:usage-contradiction`.
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
- **AC-24.** Every acceptance criterion except AC-23 — that is, AC-1 through
  AC-22 and AC-25 through AC-33 — passes with no account authentication, no
  credential projection, and no model call.
- **AC-25.** The diff contains no prefix, wildcard, regex, or pattern-based
  admission.
- **AC-26.** The adapter byte pin is recomputed and every pin consumer passes.
- **AC-27.** When Shape A is a total-only record and Shape B is present, equal
  totals are accepted and unequal totals refuse with
  `copilot:usage-contradiction`; the absent component claim is not treated as a
  match and not treated as a conflict.
- **AC-28.** An outer `model.call_start` with no distinct matching
  `model.model_call_success` refuses with `usage-unproved` and reason
  `copilot:usage-incomplete`, in both `evaluation_run` and
  `evaluation_comparator`, and no raw output file exists afterwards.
- **AC-29.** Two Shape A pair authorities carrying different triples refuse with
  `copilot:usage-ambiguous`; a pair authority and a total authority carrying
  different totals refuse with `copilot:usage-contradiction`. No result is
  produced by taking a `max`, the first, or the largest.
- **AC-30.** A stream carrying `data.outputTokens` and no usage mapping at any
  declared path refuses with `copilot:usage-incomplete`.
- **AC-31.** A stream whose only Shape A authority is total-only yields
  `native_token_usage == total` and `native_detailed_usage is None`, and the
  run's existing fallback still applies the budget gate to that total.
- **AC-32.** `TOKENOVER` still raises `token-limit-exceeded` with the identical
  number, and every legacy fixture stream that produces a trial result today
  still produces one.
- **AC-33.** No Copilot reader other than the usage authority changes its result
  on any fixture or retained real stream; the declared nested paths of I6b
  continue to be read.
- **AC-34.** A full public-schema `session.managed_settings_resolved` event is
  admitted as opaque metadata. Adding or removing it leaves model, usage,
  turns, tools, activation, trace, failure, and comparator-gate results
  identical, even when `data.settings` contains nested objects shaped like
  transport events, tool requests, or token usage.

---

## Guards that must precede implementation

Guards are written and executed **before** the production edit. Each is expected
to be red except where noted.

- **G-A — Round-2 vocabulary and sentinel (already executed).** The 13
  `1.0.82-1` additions were refused before the Round-2 edit and are accepted at
  predecessor `540fafb`; a sentinel outside the set remains refused throughout.
  Guards the first 13 assertions of AC-2 and AC-3.
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
- **G-E — legacy stream compatibility and migration.** For every legacy fixture
  stream, assert two separate things. First, *succeeds-today-succeeds-after*:
  the stream still produces a trial result or a verdict, and `TOKENOVER` still
  raises `token-limit-exceeded` with the same number — green before and after.
  Second, the **migrated numeric expectations** of the A5 table — red before,
  green after, which is what makes the migration visible instead of silent.
  Guards AC-17, AC-19, AC-31, AC-32, T14.
- **G-F — comparator and run agreement.** One stream through both entry points;
  assert equal totals and that neither raises. Guards AC-20, T8.
- **G-G — completeness and coexistence.** A stream with a `model.call_start` and
  no matching success; a stream with both shapes agreeing on total but not on
  components; a stream with both shapes fully equal; a total-only Shape A beside
  Shape B; conflicting Shape A sources; a bare `data.outputTokens`. Each asserts
  the exact refusal reason or the exact accepted triple. Guards AC-18, AC-27
  through AC-30, T19, T20, T21.
- **G-H — managed-settings metadata.** Before the Round-3 production edit,
  assert that `session.managed_settings_resolved` alone is refused while the 13
  Round-2 literals are accepted and the sentinel remains refused. Then add one complete
  `session.managed_settings_resolved` fixture using the public generated schema,
  including `data.settings` with nested event-, tool-, and usage-shaped decoys.
  Compare every Copilot semantic reader and comparator gate with and without the
  event; every result must be identical. Guards AC-34 and the Round-3 revisit
  condition.

**G-A through G-H are the pre-implementation guard set.** G-H is added before
the Round-3 production edit. The already-executed
G1 and G2 probes are their evidentiary basis and are not repeated.

---

## Check contract

| ID | Layer | Check | Fixture-only? |
|---|---|---|---|
| CHK-1 | L1/L2 | observed-vocabulary fixture accepted | yes |
| CHK-2 | L1/L2 | 14 individual acceptance assertions | yes |
| CHK-3 | L2/L12 | sentinel still refused, no raw file; test source unmodified | yes |
| CHK-4 | L2 | malformed envelopes still refused | yes |
| CHK-5 | L3 | transcript acceptance and projection invariance | yes |
| CHK-6 | L4-L10 | `copilot_outer_events` yields transport events only | yes |
| CHK-7 | L4 | `native_model` identical to pre-change on real retained streams | yes |
| CHK-8 | L7 | `native_skill_evidence` identical | yes |
| CHK-9 | L8 | `normalized_native_events` identical | yes |
| CHK-10 | L9 | comparator `event_types` / `tool_event` / call-start count identical | yes |
| CHK-11 | L4-L10 | all of CHK-7..CHK-10 hold under insert / remove / reorder / duplicate / interleave of the 14 | yes |
| CHK-12 | L5/L6 | usage field validation: required fields, non-bool ints, non-negative, `total == prompt + completion` | yes |
| CHK-13 | L5/L6 | dedup by `id`: duplicating every usage event changes nothing | yes |
| CHK-14 | L5/L6 | order invariance: reversal and interleaving change nothing | yes |
| CHK-15 | L5/L6 | id-count versus outer `model.call_start` mismatch refuses | yes |
| CHK-16 | L13 | canonical Shape A: every legacy fixture stream still succeeds, and each produces the exact migrated numbers of the A5 table | yes |
| CHK-17 | L5/L6 | both shapes present: equal accepted, unequal refuses | yes |
| CHK-18 | L5/L6 | `responseUsage` divergence refuses | yes |
| CHK-19 | L5/L9 | run and comparator report the same total | yes |
| CHK-20 | L1 | no `startswith`, `re.`, wildcard, or prefix comparison near the vocabulary, the validator, or the usage parser | yes |
| CHK-21 | L5/L16 | Claude and Codex suites green; their branches still use `recursive_values`; `SUPPORTED_SOURCE_VERSIONS` unchanged | yes |
| CHK-22 | L11 | `executor-output-limit` and token-limit behaviour unchanged; flooded stream still refuses | yes |
| CHK-23 | L14/L15 | pin recomputed; ratchet green; consumers `:439`, `:5114`, `:5278` accept | yes |
| CHK-24 | L16 | identity and attestation keys byte-identical | yes |
| CHK-25 | real | real run yields a complete trial result; real comparator yields a verdict | **no** |
| CHK-26 | L5/L6/L9 | `model.call_start` without a distinct matching success refuses `copilot:usage-incomplete` in run and comparator; no raw file | yes |
| CHK-27 | L5/L6 | coexistence full-triple equality: equal triple accepted; equal total with unequal components refuses; total-only Shape A compares totals only | yes |
| CHK-28 | L5/L6 | conflicting Shape A authorities refuse; no `max`, first-wins, or largest-wins across mixed legacy sources | yes |
| CHK-29 | L5/L6 | bare `data.outputTokens` with no usage mapping refuses `copilot:usage-incomplete` | yes |
| CHK-30 | L11/L13 | `TOKENOVER` still raises `token-limit-exceeded` with the identical number; the run-branch legacy stream still yields `native_detailed_usage is None` with the total supplied through the existing fallback | yes |
| CHK-31 | L4-L10 | the declared nested paths of I6b are still read: model identity, turns, tool calls, skill activation, trace, failure message, and the comparator gate produce identical results on every fixture and retained real stream | yes |
| CHK-32 | L1-L10 | full-schema managed-settings event is admitted and semantically inert despite nested settings decoys | yes |

Every check except CHK-25 is auth-independent and model-independent. Ordering:
guards G-A..G-H, then CHK-1..CHK-24 and CHK-26..CHK-32, then CHK-25.

**Fixture derivation rule.** Usage fixtures reproduce the exact numeric shapes
measured in G2 — the single-call triple and the four-call sequence — with
synthetic ids. They are not hand-invented shapes and carry no captured content.

---

## Rollback and fail-closed evidence

- **Primary rollback:** revert the 14 vocabulary additions, the outer-event
  iterator, and the usage parser, then recompute the adapter pin. The boundary
  returns to explicit refusal with the identical code and message, and Copilot
  usage returns to the generic recursive scan.
- **Fail-closed direction, stated honestly:** on the *admission* boundary the
  reverted state refuses strictly more — rollback cannot open a hole there. On
  the *usage* boundary it does not: reverting restores the base adapter's
  under-counting (`native_token_usage` returning a bare output count) and its
  two disagreeing readers. Rollback is a return to the known base defect, not a
  safety improvement, and it is only correct as a whole-slice revert.
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

1. Guards G-A through G-G retain their recorded pre-Round-2 signals, and G-H is
   executed before the Round-3 production edit. G-E retains its split signal,
   green for stream success and red for migrated numbers; G-H records the
   fourteenth type red, the 13 predecessor additions green, and the sentinel
   green-by-refusal before the edit.
2. `COPILOT_EVENT_TYPES` grows from the predecessor's 69 members to 70 by adding
   only `session.managed_settings_resolved`; cumulatively it contains the
   original 56 plus exactly the 14 measured additions.
3. `validate_native_schema` is unmodified.
4. `copilot_outer_events` exists, yields transport events only, and every
   Copilot branch of `native_model`, `native_token_usage`,
   `native_detailed_usage`, `native_skill_evidence`, `normalized_native_events`,
   `native_failure_message`, and the comparator gate uses it.
5. `recursive_values` is unmodified and remains the Claude and Codex iterator.
6. `copilot_call_usage` implements A3 exactly: exact path, exact required
   fields, non-bool non-negative ints, `total == prompt + completion`,
   `responseUsage` agreement, dedup by `id`, per-call summation, and id-count
   equality with outer `model.call_start` whenever Shape B is engaged.
6b. `copilot_legacy_usage` implements A5 exactly: the three declared paths and
   nothing else, per-mapping normalization, within-class agreement, cross-class
   total agreement, the `declared_output` rules, and the resolution table —
   including refusing a bare output count.
7. `native_token_usage` and `native_detailed_usage` derive Copilot totals from
   the one authority, and CHK-19 proves the run and the comparator agree.
8. The two-shape rule of A5 is implemented with **full normalized triple**
   equality — not total-only — and explicit precedence; no `max` and no silent
   fallback survives anywhere on the Copilot path.
9. Both declared semantic changes are asserted by CHK/AC rather than left
   implicit: Shape B's `max` → `sum` (AC-10), and the Shape A migration to one
   canonical authority with the exact numbers of the A5 table (AC-17).
10. Every check except CHK-25 passes — CHK-1 through CHK-24 and CHK-26 through
    CHK-32; the 14 per-type acceptance assertions exist individually; the
    sentinel test is green and its source unchanged.
11. Every legacy Shape A fixture stream still succeeds, `TOKENOVER` still
    exceeds with the identical number (CHK-30), each stream produces the exact
    migrated numbers of the A5 table (CHK-16), and the real `result.usage` shape
    neither contributes nor refuses (AC-19). No claim that legacy numbers were
    preserved appears anywhere in the change, its tests, or its commit message.
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
18. The completeness rule is implemented and checked (CHK-26), no failure event
    name was invented, and the change makes no claim of failure-path vocabulary
    coverage.
19. I6b holds: every non-usage Copilot reader still reads its declared nested
    paths, proved identical by CHK-31.
20. The complete public-schema `session.managed_settings_resolved` fixture,
    including adversarial nested `data.settings` decoys, is semantically inert
    under every Copilot reader and comparator gate (AC-34/CHK-32).

Shipping the vocabulary without the usage authority is the measured F2 state and
must not be recorded as done. Shipping the Shape B authority while leaving Shape
A on the two disagreeing readers is the measured E1 state and must not be
recorded as done either.

---

## Round-1 paired review resolution record — T1, E1, E2, E3

| ID | Reviewer finding | Verified? | Resolution in this revision |
|---|---|---|---|
| **T1** | Coexistence compared `total_tokens` only, so a stream whose two shapes agree on the total while disagreeing on components would be accepted. | Yes — the Round-1 A5 rule read "the two `total_tokens` must be equal". | A5 coexistence now requires equality of the **full normalized triple**, field by field. Equal total with unequal components refuses `copilot:usage-contradiction`. Shape B is selected only *after* equality holds. The one asymmetric case — a total-only Shape A record, which makes no component claim — is an explicit sub-rule with its own check rather than an implicit gap. New: AC-18 (rewritten), AC-27, CHK-27, threat T6 updated, G-G. |
| **E1** | The promise to preserve "today's exact numbers, byte-for-byte" for Shape A is impossible, because the two current readers already disagree. | Yes — measured against the unmodified base adapter: 15 vs 40, 15 vs 40, 5 vs 15, 15 vs `None`, 1000 vs `None`. The 30 is a double count of one 15 through `:5364` and `:5406`. | The promise is **withdrawn in the document**, and Shape A is given the same treatment as Shape B: one canonical parser `copilot_legacy_usage` over three declared outer-event paths, with both readers converging on it. Fixtures become compatibility *inputs*: every stream that succeeds today still succeeds and `TOKENOVER` still exceeds with the same number. The migrated numbers are tabulated exactly and asserted as new expected values. Inconsistent reader outputs are retired deliberately, with no hidden compatibility claim. New: A5 rewritten, AC-17 rewritten, AC-29 through AC-32, CHK-16 rewritten, CHK-28, CHK-29, CHK-30, threats T14/T20/T21, G-E split into a green half and a red half, DoD 6b/8/9/11. |
| **E2** | G2 measured only all-success turns, so the completeness rule needed to state what happens when a call starts and never succeeds. | Yes — both G2 streams exited 0 with 1==1 and 4==4; no failure, retry, or cancellation was ever captured. | A3 now states it directly: an outer `model.call_start` with no distinct matching `model.model_call_success` yields `copilot:usage-incomplete` and refuses the trial and the comparator, **whatever the cause**, because the complete token budget cannot be proved. Recorded as intended fail-closed behaviour, explicitly not a compatibility promise. Any unobserved failure event type remains outside the vocabulary and still raises `unsupported-native-schema`, which is the designed signal to return to design with a fresh capture; **no failure event name is invented** and no failure-path vocabulary coverage is claimed. The `id` evidence is scoped honestly: proved for `model.model_call_success`, unmeasured for `model.call_start`, so turns stay a positional count and whole-stream duplication refuses rather than being silently deduplicated. New: constraint-provenance rows with revisit conditions, threats T19 and T22, AC-28, CHK-26, G-G, DoD 18, and the bounded restatement of the "duplication-safe" claim. |
| **E3** | I6 was written broadly enough to read as prohibiting *any* inspection below the transport event, which would outlaw the declared nested paths that turns, tool calls, skill activation, and trace legitimately use. | Yes — the Round-1 I6 said "no Copilot semantic reader inspects any object below the transport event". | I6 is rescoped strictly to **usage derivation**. New I6b states the opposite explicitly for everything else and enumerates the retained declared paths per reader with citations: `data.model`; outer `model.call_start`; `data.toolRequests[].toolCallId`; `data.skills[]`, `data.toolCallId`, `data.result.content`, `data.skillName`/`data.name`, `data.resolvedPath`; the trace text and name fields; the failure-message extraction; and the comparator's outer type prefix test. A2's restriction is clarified as removing *recursive descent into arbitrary nested bodies*, not declared-field access. New: AC-33, CHK-31, DoD 19, architecture and check wording updated. |

**Round-1 findings not requiring change.** The Round-1 R1-a through R1-d entries
above are unaffected. No reviewer challenged the vocabulary addition itself, the
outer-event iterator, or the decision to keep admission validating `type` only.
