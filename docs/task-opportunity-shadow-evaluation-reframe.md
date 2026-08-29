# Bounded shadow evaluation of a routed candidate

## Objective

Let one scheduled owner pass claim one recurrence-gated shadow candidate under
an append-only open-attempt record, have the existing candidate-blind authoring
boundary design its observed-task cases from one closed packet, run the
existing `shadow-compile` / `shadow-execute` / `shadow-certify` flow inside a
pre-reserved and mechanically enforced worst-case time bound, retain a semantic
result bound to the exact candidate and occurrence authorities, and always
leave `evaluating` in the same pass while the owner holds its lease.

This design follows the principles in
`skills/skill-review/SKILL.md#dreaming-design-principles` and continues the
funnel specified in `docs/task-opportunity-evidence-design.md`.

Revision 3 resolves round-one findings T1-T3 and O4-O6 and round-two findings
R1-R3. Round two accepted the candidate-blind packet isolation of revision 2 as
resolved and the O4-O6 directions as consistent. Reframe status remains OPEN
pending round-three review.

## Lane

**Systemic.** This change gives the scheduled owner a third bounded model-and-
execution stage, adds a fourth adapter role to the existing registry, extends
the authoring boundary to a second subject class, adds append-only attempt
records and a cross-pass recovery sweep, adds a bounded-subprocess wrapper for
the evaluator and lifecycle tools, and changes how the pass deadline,
accounting, dashboard, halt, and rollback treat a candidate that is
mid-evaluation. It is not a local fix inside one function.

## Non-goals

- Do not add a second evaluation queue. Ready candidates are derived from
  retained dispositions plus candidate-lifecycle authority, and retry ordering
  is derived from append-only attempt records.
- Do not add a second scheduled owner, scheduler, writer lease, or transcript
  transport.
- Do not add a supervisor process, signal transport, or watchdog for mid-run
  interruption of `shadow-execute`.
- Do not add a second catalog stage or a deterministic semantic classifier.
- Do not evaluate more than one candidate per pass beyond the configured
  allowance, and do not evaluate the same subject twice in one pass.
- Do not grant install, publication, portfolio, or admission authority from a
  passing evaluation. `evaluating` has no forward edge to `portfolio_pending`
  in `DECLARED_TRANSITIONS` and this design does not add one.
- Do not invent a repair candidate. `missed-skill` and
  `wrong-or-incomplete-skill` stay report-only until immutable candidate
  evidence exists for them, which is a separate work order.
- Do not let the model see anything outside one closed packet.
- Do not leave a candidate in `evaluating` across passes on any owned path, and
  do not perform any durable write after lease loss.
- Do not rewrite, append fields to, or delete any attempt record after it is
  written.
- Do not broaden into general portfolio governance.

## Constraint provenance and reframe gate

| Constraint | Provenance and binding evidence or owner | Protects | Revisit when |
| --- | --- | --- | --- |
| No second evaluation queue; ready candidates derive from retained dispositions and candidate-lifecycle records, and retry order derives from append-only attempt records | Work-order instruction 2026-08-29 and the existing derived-view rule in `docs/task-opportunity-evidence-design.md` | One recurrence authority; no divergent duplicate queue state | A derived view provably cannot express fencing that only mutable queue rows can carry |
| No second scheduled owner, scheduler, or writer lease | Existing sole-Mac-mini ownership and the token-fenced lease in `daemon-lock.py:138-225` | No overlapping writers or competing model spend | The owner explicitly changes machine topology |
| Evaluation case design stays with the existing `evaluation-input-author` model boundary, and that adapter receives only one closed packet | Existing boundary in `dreaming-vendor-adapter.py:3465-3520` with schema `:2986` and prompt `:3021`; design-doc rule that the LLM owns designing observed-task evaluation cases | One authoring contract, one enforced privacy boundary, no second model stage | A measured model failure cannot be corrected through the packet, schema, or bounded review |
| Candidate-blindness is enforced by a versioned closed-schema packet with `additionalProperties: false`, an explicit prohibited-source list, and stable refusal codes | Round-one finding T3; existing packet-validation pattern in `dreaming-vendor-adapter.py:3303-3335` | An auditable, testable privacy boundary rather than a construction argument | The packet must carry a field whose prohibition is shown to block a required evaluation signal |
| Evaluation consumes the exact immutable candidate package and canonical occurrence identities, never reviewer claims or report snapshots | `docs/task-opportunity-evidence-design.md` hard invariants 1-3; `candidate-lifecycle.py:413` package identity | A result always names what was actually evaluated | Never; a weaker binding invalidates the result |
| A passing evaluation grants no install or publication authority | Design-doc Definition of Done ("without granting premature installation authority") and `DECLARED_TRANSITIONS` having no `evaluating -> portfolio_pending` edge | Unreviewed mutation cannot reach the estate | A separate reviewed work order defines an admission path |
| A pass that enters `evaluating` while holding a valid lease must leave it before the pass ends; a pass that loses its lease performs no durable write of any kind, and the next valid lease holder retains that pass's durable outcome and stop reason | Work-order instructions 2026-08-29 and round-one finding T1 as corrected by round-two finding R1; `candidate-lifecycle.py:1011` refuses `collect` in `evaluating`; `ELIGIBLE_CANDIDATE_GROUP_STATES` (`dreaming-core.py:100-105`) hides evaluating groups from the reviewer | A later semantic occurrence cannot fork a new recurrence group, and a fenced-out owner cannot corrupt state | Evaluating groups become visible for aliasing under a separate reviewed slice |
| Attempt evidence is append-only: one immutable open-attempt record written before the claim transition, settled by appending at most one immutable terminal-attempt record that names the open record's digest | Round-one finding T1 and round-two finding R3; existing content-addressed receipt pattern | Abnormal termination is detectable and deterministically recoverable with no mutable state and no in-place rewrite | Recovery is proven expressible from lifecycle state alone without ambiguity |
| Halt and deadline are observed only between bounded operations; `shadow-execute` runs to completion once started | Existing `halt_check` placement in `dreaming-core.py:8917`, `:9189`, `:9260`, `:9291`, `:9323`; round-one finding T2 | One enforcement model with no new supervisor process or signal transport | A supervised per-trial execution path exists in the harness without a new subsystem |
| The stage refuses to start unless remaining pass time covers a worst-case bound whose every term is mechanically enforced, including authoring, attestation, trials, compile, certify with its nested verification subprocess, settlement, and termination overhead | Round-one finding T2 and round-two finding R2; existing bounded process-group calls in `dreaming-core.py:388-413` and `skill-evaluation-harness.py:580-650` | The 3,600-second pass deadline holds without mid-run interruption and without an unenforced estimate | Measured cost shows the bound is unusably pessimistic and a supervised path is justified |
| Every subprocess the stage starts runs in its own process group under an enforced timeout, with terminate-then-kill cleanup and a named terminal refusal on breach | Round-two finding R2; `ExecutableAdapter._invoke` (`dreaming-core.py:388-413`) and harness `call` / `terminate` (`skill-evaluation-harness.py:580-650`) already implement this pattern | No term of the reservation can be exceeded by an unbounded child | A tool is shown to be uninterruptible without corrupting durable state |
| The owner pass keeps its existing 3,600-second hard deadline | Installed `daemon-pass.sh:33` backstop inside the four-hour cadence, as recorded in `docs/task-opportunity-evidence-design.md` | Profiling, review, evaluation-input, and evaluation all settle without scheduler overlap | The four-hour cadence changes, or measured stage cost no longer fits |
| Routing `evaluation_execution.available` is computed from live authority, never from a hard-coded constant, and fails closed | Round-one finding O4; current hard-coded `SHADOW_EXECUTION_BLOCKERS` at `profile_evaluation_routing.py:39-42` | The projection never claims an evaluation the stage cannot run, and never hides one it can | Never; a constant reintroduces the divergence this row exists to prevent |
| Semantic result identity excludes pass, lease, and timing fields; the terminal-attempt record carries them | Round-one finding O5 | Replay determinism and audit completeness are both satisfiable | A field is shown to be simultaneously semantic and per-attempt |
| No subject is claimed twice in one pass; a persistent failure never starves other ready candidates | Round-one finding O6 | Work conservation and fair progress across candidates | Only one candidate is ever ready, making ordering moot |
| Deterministic trial count per candidate is `executors x (cases + task_value_cases) + executors` | Derived from `skill-evaluation-harness.py:2344-2352` (control plus candidate treatment for `task_value`) and `:2330-2342` (one `version` call per executor) | Cost is predictable before the stage starts | The harness changes its treatment or attestation plan |
| Minimum conforming suite is five cases, one per required routing class | Derived from `skill-evaluation.py:10706-10711`, which requires exactly the set `routing_positive`, `routing_close_negative`, `routing_unrelated`, `routing_conflict`, `task_value` | The three gates are all observable | The evaluator changes its required class set |
| Numeric values of `max_evaluations_per_run`, the evaluation stage second budget, per-call `timeout_seconds` / `token_budget` / `turn_budget` / `tool_budget` / `output_bytes`, and every reservation term (`author_call_bound`, `compile_bound`, `certify_bound`, `settlement_bound`, `deadline_margin`) | **Unresolved policy decision.** No measured shadow-trial cost exists in this repository; the only shadow runs are deterministic fixtures in `test-shadow-mutation-boundary.sh` | Bounded model cost and a deadline-safe pass | Set them from CHK-09's measured single-candidate run, or record an owner-set provisional bound with the measurement scheduled; do not hard-code a number before one of those. Enforcement of each term is settled here; only the numbers are open |

**Reframe status: OPEN.**

### Reframe record

1. **Which user-visible outcome is blocked?**
   A recurrence-gated shadow candidate is never evaluated. The funnel produces a
   `candidate-evaluation` route naming an exact immutable package, and nothing
   consumes it, so no user ever sees a triggering, task-performance, or
   regression result for a skill the system proposed. The design-doc Definition
   of Done item "creates or repairs one real shadow skill, then passes
   triggering, task-performance, and overall-regression evaluation" cannot be
   satisfied.

2. **Which constraint caused it?**
   "Do not add a second evaluation queue, owner, scheduler, transcript
   transport, catalog stage, or deterministic semantic classifier," combined
   with the funnel's existing scope boundary. Under a strict reading, every
   route to the shadow evaluator looked like new machinery, so the previous
   successor stopped at a read-only projection. That reading was too strict:
   the constraint forbids **duplicate authorities**, not **extensions of
   existing ones**.

3. **What invariant would fail if the constraint changed?**
   If a second queue or owner were allowed, hard invariant 3 ("one canonical
   task occurrence contributes at most one observation to one recurrence
   group") and invariant 10 ("exactly one installed Mac mini scheduler owns the
   pass") would both be at risk: a separate evaluation queue would hold its own
   view of candidate readiness and could diverge from the lifecycle record, and
   a separate owner could spend model capacity outside the pass deadline and
   accounting.

4. **What is the simplest design without it?**
   Keep every authority singular and extend existing components: register the
   already-implemented `skill-evaluation-executor` adapter role in the existing
   adapter registry; give the existing `evaluation-input-author` boundary a
   shadow subject mode over one closed packet; drive the existing
   `skill-evaluation.py` shadow subcommands from the existing scheduled owner;
   derive readiness from the existing routing view; and make abnormal
   termination recoverable with an append-only write-ahead record rather than a
   supervisor process.

5. **Which option has fewer trusted components?**
   The extension option. It adds one adapter role to a registry loop that
   already validates roles generically (`dreaming-core.py:9507-9565`), one
   packet kind to an authoring boundary whose model contract is already generic
   over a template, and three append-only record types with no mutable state
   and no in-place rewrite. Its only new mechanism is one bounded-subprocess
   helper that copies a pattern the codebase already implements twice. A
   parallel shadow evaluation subsystem would add a queue, an owner process, a
   lease, a supervisor for mid-run interruption, a second accounting path, and a
   second dashboard authority. The extension option is selected.

### Correction to the previous framing

The first revision of this document claimed four new subsystems. Three of those
claims were too strong and are corrected here:

- **Adapter role is registry data, not a subsystem.** `configured_adapters`
  (`dreaming-core.py:9507-9565`) is a generic loop over `ROLE_CONFIG_KEYS`
  (`:146`) that builds an `ExecutableAdapter`, calls `doctor`, and records
  identity. `dreaming-vendor-adapter.py` already implements
  `skill-evaluation-executor` `doctor`, `version`, `prepare`, `run`,
  `normalize`, and `collect` (`:6758-6772`), and `evaluation_identity`
  (`:3822-3879`) already emits the complete shadow executor identity including
  `real_backend` and `real_backend_source` under `--shadow-contract`. Core does
  not invent identity; it records what the adapter attests.
- **Fixtures are labels, not a filesystem subsystem.** The harness validates
  `case["fixture"]` only as a non-empty string (`skill-evaluation-harness.py:1713`)
  and passes it to the adapter in the trial spec. No fixture tree is resolved
  by the harness.
- **Graders are declarative, not code.** The harness evaluates `regex`,
  `json_schema`, `file`, `trace`, `numeric`, and `command` graders itself
  (`skill-evaluation-harness.py:809-865`). A safety grader can be a declared
  `regex` grader. No grader runtime is added.

Two claims stand and are the real work:

- **A shadow authoring packet does not exist.** The authoring boundary routes
  every operation through `skill-evaluation.py v2-input-author-packet`
  (`dreaming-vendor-adapter.py:3309-3335`), which loads the
  installed-capability suite via `load_suite` (`skill-evaluation.py:645-690`)
  and requires `cross_executor_authority` (`:2298-2305`). The shadow suite is a
  different document (`skill-evaluation.py:10592-10720`).
- **The existing evaluation-input owner has the wrong subject class.**
  `execute_evaluation_input_owner` (`dreaming-core.py:9391-9454`, called at
  `:10454`) selects estate-census rows by `capability_id` and `skill_path` and
  emits authoring input, not a shadow certificate. It is not reusable as-is,
  and giving a shadow candidate a synthetic capability id would be the second
  queue this design forbids.

## Selected architecture

One stage inside the existing pass, six extension points, two immutable record
types, no mutable stage state.

### E1. Register the shadow evaluation executor role

Add one entry to `ROLES` (`dreaming-core.py:106`) and one to
`ROLE_CONFIG_KEYS` (`:146`), for example config section `evaluators` mapping to
role `skill-evaluation-executor` with protocol
`dreaming.skill-evaluation-executor`.

*Why a new config surface is required:* the runtime discovers adapters only
through the configured sections; there is no other way to name an executable,
its timeout, and its health. The surface is one additional section in the
existing adapter config, validated by the existing loop, with no new file, no
new sealing, and no new owner. Adapter argv must carry
`--role skill-evaluation-executor --shadow-contract --model <exact model>`,
because `evaluation_identity` refuses `model=default`
(`dreaming-vendor-adapter.py:3823-3825`) and only emits the shadow limit and
backend attestation fields under `--shadow-contract`.

The executors and routing documents that `shadow-compile` requires are then
*derived*, not authored: core calls the configured adapter's `version`, writes
`{schema_version: 2, kind: shadow_candidate_evaluation_executors, executors:
[response + name]}` and the matching routing document whose `argv[0]` is the
configured adapter path. `shadow_attest`
(`skill-evaluation-harness.py:1887-1894`) then compares the adapter to itself,
which is the intended attestation.

### E2. Give the authoring boundary a shadow subject mode over a closed packet

Add `skill-evaluation.py shadow-author-packet`, producing kind
`safe_shadow_evaluation_authoring_packet` at `schema_version: 1`, and route to
it from `validate_evaluation_input_packet` when the operation names a shadow
subject. The builder and the validator are the same deterministic owner: the
builder emits, the validator re-derives, and the adapter refuses any packet the
validator does not accept.

*Why a new packet schema is required:* the existing packet embeds a v1
`suite_template`, `policy_contract`, `compilation_contract`, `routing_contract`,
and `source_catalog` that have no shadow equivalent, and its validator refuses
any suite that is not a normalized cross-executor schema-2 suite.

*The adapter receives only this packet.* The shadow authoring invocation takes
one argument, the packet path. It takes no skill directory, suite, policy,
config, routing, harness, or catalog argument, unlike
`evaluation_input_source_paths` for the installed-capability operations
(`dreaming-vendor-adapter.py:3272-3300`). There is no second input channel to
audit.

**Permitted fields.** The packet is a closed object with
`additionalProperties: false` at every level and exactly these members:

| Field | Content | Source root |
| --- | --- | --- |
| `schema_version`, `kind` | Constants | Builder constant |
| `packet_id` | `sha256(canonical(packet without packet_id))` | Builder |
| `lifecycle_id`, `candidate_id` | Claimed subject identity | Lifecycle record |
| `candidate_contract` | Proposed skill name, contract text, and per-file `{path, sha256, bytes}` inventory | Materialized package directory only |
| `suite_template` | Five fixed cases: `id`, `class`, `routing`, `artifacts`, `deterministic_graders`, `fixture`, `semantic` | Builder constant |
| `compilation_contract.case_runtime` | Per-case runtime block consumed by the existing response validator | Builder constant |
| `executor_contract` | Executor identity digest, declared `model`, and `limits` | Derived executor document |
| `harness_digest` | `sha256` of the harness executable | Harness file |
| `routing_mode` | `candidate_only` | Builder constant |

**Permitted source roots** are exactly three: the materialized candidate
package directory, builder constants, and the derived executor identity
document. No other filesystem root, database, or environment value may
contribute a byte.

**Prohibited fields and sources**, refused explicitly rather than merely
omitted: estate census rows, any other skill root, catalog inventory,
transcripts, session records, task-profile receipts, audit dispositions,
reviewer claims, dashboards, home state, credentials, environment variables,
absolute filesystem paths, adapter `argv`, canonical occurrence identities,
`profile_id` values, lease tokens, pass identity, and wall-clock timestamps.
Occurrence and profile authorities are deliberately excluded from the packet:
they bind the *result*, not the *case design*, and the model must not see them.

**additionalProperties behavior.** Unknown keys are a refusal, never ignored
and never stripped. The validator compares the exact key set at every object
level and fails on any difference in either direction.

**Sensitive-value rejection basis.** Every string leaf is scanned before the
packet leaves the builder and again on validation. A leaf is rejected when it
contains an absolute path outside the packet, a `sha256:` digest not present in
the packet's declared authority set, a UUID-shaped identifier, a `profile_id`
shaped value, or a credential-shaped token. The scan reuses the existing
authoring sensitive-value rejector, extended to cover the new kind.

**Stable refusal codes:** `shadow-packet-schema-invalid`,
`shadow-packet-unknown-field`, `shadow-packet-prohibited-source`,
`shadow-packet-sensitive-value`, `shadow-packet-candidate-mismatch`,
`shadow-packet-template-drift`, `shadow-packet-executor-unattested`.

**Retained evidence.** The exact packet bytes are retained immutably beside the
result under `packet_id`, so a reviewer can re-run the validator on precisely
what the model received.

*What is deliberately reused unchanged:* the model contract. The model still
returns only `{outcome, summary, cases:[{id, task_id, prompt}]}` under
`evaluation_input_author_schema` (`dreaming-vendor-adapter.py:2986-3018`) with
the existing prompt (`:3021-3060`), the existing task-id shape
`AUTHOR_TASK_ID_RE` (`:235`), the existing refusal codes `AUTHOR_REASON_CODES`
(`:236-240`), and the existing response validation (`:3196-3270`) that pins
`id` and `class` to the template and forbids duplicate prompts.

*Why `candidate_only` routing mode:* `shadow-compile` refuses a `--catalog-dir`
in that mode (`skill-evaluation.py:10853`), so no catalog stage can be created.
`routing_close_negative` and `routing_unrelated` still prove non-triggering,
and `routing_conflict` is expressed with empty `catalog_loads` as the validator
requires (`skill-evaluation.py:10661-10678`).

### E3. Compute execution availability instead of asserting it

Replace the hard-coded `SHADOW_EXECUTION_BLOCKERS` constant
(`profile_evaluation_routing.py:39-42`) with an `execution_authority` argument
to `build_evaluation_routing_row`, and compute
`evaluation_execution = {available, reasons}` from it. The routing module keeps
no static blocker list and makes no assumption about the runtime.

The caller supplies the authority as facts it has already established:
configured evaluator present, evaluator `doctor` healthy, evaluator `version`
attested against the derived executors document, shadow suite authority
available (template constant plus `shadow-compile` subcommand plus resolvable
harness digest), shadow authoring authority available (packet builder present
and an `evaluation-input-author` adapter configured and healthy), and the
candidate package materializable.

**Fail-closed.** Missing, malformed, or partially populated authority yields
`available: false` with reason `shadow-execution-authority-unknown`. Availability
is true only when every named condition is explicitly true. Computed reason
codes are stable: `shadow-executor-unconfigured`, `shadow-executor-unhealthy`,
`shadow-executor-unattested`, `shadow-suite-authority-unavailable`,
`shadow-authoring-authority-unavailable`, `shadow-candidate-package-unavailable`,
`shadow-execution-authority-unknown`.

The scheduled stage and the routing projection consume the **same** derivation
function on the same facts, so the row cannot disagree with the stage.

### E4. Derive the ready candidate from existing authority

Reuse `derive_evaluation_routing`. A candidate is ready when its row has
`route == "candidate-evaluation"` with `evaluation_execution.available` true,
meaning the disposition is a current v3 `no-covering-skill` audit with a
one-to-one occurrence boundary, the bound lifecycle record has three distinct
current canonical occurrences inside the 30-day window, a shadow-ready
recommendation naming the current candidate, no uncertain, covering, or
tombstone blocker, and live execution authority.

No queue row is written. Ordering is defined in "Claiming, ordering, and
lifecycle exit" below and is computed from the routing view plus the append-only
attempt records.

### E5. Run the stage inside the existing pass under an enforced full-invocation reservation

The stage runs after review and evaluation-input settle, under the existing
writer lease and the existing 3,600-second deadline. It performs, per claimed
candidate, in order: reserve, claim, materialize, build packet, author,
compile, execute, certify, retain, exit.

**Selected enforcement architecture: full-invocation reservation.**
`shadow-execute` is a synchronous call that runs every trial to completion
(`skill-evaluation-harness.py:2330-2352`), so halt and deadline cannot be
observed inside it without a supervisor process the constraints forbid. Instead
the stage refuses to start unless the remaining pass time covers the entire
worst case:

```
author_bound     = author_call_bound
attest_bound     = executors x executor_call_bound
trial_count      = executors x (cases + task_value_cases)
trial_bound      = trial_count x executor_call_bound
settlement_bound = lifecycle_transition_bound x max_transitions_per_candidate
                 + record_write_bound
termination_overhead = bounded_subprocess_count x termination_grace

worst_case = author_bound + attest_bound + trial_bound
           + compile_bound + certify_bound
           + settlement_bound + termination_overhead

start allowed iff remaining_pass_seconds >= worst_case + deadline_margin
```

`executors`, `cases`, `task_value_cases`, `max_transitions_per_candidate`
(three: claim, exit, and one recovery allowance), and
`bounded_subprocess_count` (`1 author + executors attest + trial_count trials +
1 compile + 1 certify + max_transitions_per_candidate lifecycle calls`) are all
known before the stage starts, so the formula is finite and computable at
reservation time.

**Every term is mechanically enforced.** Round-two finding R2 requires an
enforcement mechanism per term, not an estimate. Two bounded process-group
callers already exist and are reused; one minimal wrapper is added for the
tools core invokes directly.

| Term | Enforcement mechanism | Terminal refusal on breach |
| --- | --- | --- |
| `author_bound` | Existing `ExecutableAdapter._invoke` (`dreaming-core.py:388-413`): `start_new_session=True`, `communicate(timeout=run_timeout)`, `killpg SIGTERM`, `wait(5)`, `killpg SIGKILL`. The bound is the `evaluation-input-author` entry's configured `run_timeout` | `author-call-timeout` |
| `attest_bound` | Existing harness `call` (`skill-evaluation-harness.py:597-650`): monotonic deadline, output limit, `terminate()` at `:580-593`. The bound is `executor.limits.timeout_seconds` per `version` call | `execute-timeout` |
| `trial_bound` | Same harness `call` per trial, one trial per `(executor, case, treatment)` as enumerated at `skill-evaluation-harness.py:2344-2352` | `execute-timeout` |
| `compile_bound` | **Minimal bounded wrapper.** Core invokes `skill-evaluation.py shadow-compile` through one new helper that copies the `_invoke` pattern exactly: own process group, `communicate(timeout=compile_bound)`, terminate-then-kill | `compile-timeout` |
| `certify_bound` | Same wrapper around `skill-evaluation.py shadow-certify`. Because certify shells to the harness `shadow-verify` subprocess, the wrapper must kill the **process group**, not the direct child; `subprocess.run(timeout=...)` alone is insufficient and is explicitly rejected here | `certify-timeout` |
| `settlement_bound` | Same wrapper around each `candidate-lifecycle.py transition` call, plus a bounded local write for the result and attempt records | `settlement-timeout` |
| `termination_overhead` | `termination_grace` is the worst-case cleanup per bounded subprocess: at most SIGTERM plus grace plus SIGKILL. The two existing callers already bound this at 5 s (`dreaming-core.py:406`) and 2 s per signal (`skill-evaluation-harness.py:589`); the new wrapper uses the same shape | Counted, not refused |

The wrapper is one function. It is not a supervisor: it does not observe halt,
does not interrupt work mid-call, and has no state. It exists solely so that no
term of the reservation can be exceeded by an unbounded child.

Every breach is a terminal result for the candidate and takes the normal owned
exit: the record returns to `ready_for_draft`, a terminal-attempt record is
appended with the named refusal, and the subject is retryable in a later pass.

`author_call_bound`, `executor_call_bound`, `compile_bound`, `certify_bound`,
`lifecycle_transition_bound`, `record_write_bound`, `termination_grace`, and
`deadline_margin` are named policy values set by CHK-09 or by an owner-set
provisional bound. No number is invented here; only the enforcement is settled.

Halt and deadline are checked **between** bounded operations, at the same
granularity the existing owners already use (`dreaming-core.py:8917`, `:9189`,
`:9260`, `:9291`, `:9323`): before reserve, before claim, before the authoring
call, before compile, before execute, and before certify. Once `shadow-execute`
starts, it runs to completion, and the reservation is what guarantees it cannot
overrun the pass deadline. This is a deliberate property, not an accident.

If the reservation cannot be met, the stage retains stop reason
`pass_deadline_reservation_unmet` and claims nothing.

### E6. Append-only records and a recovery sweep

Round-two finding R3 rejects a record that is written and later "closed". All
attempt evidence is append-only: every record is written exactly once,
content-addressed, and never rewritten, extended, or deleted. "Open" is a
**derived** property, not a stored flag.

**Open-attempt record** —
`DREAMING_DATA_DIR/shadow-evaluations/v1/attempts/open/<open_attempt_id>.json`,
written **before** the `ready_for_draft -> evaluating` transition. It is the
write-ahead fence and the sole cross-pass recovery authority.

```
open_attempt_id = sha256(canonical({
  schema_version, kind: shadow_evaluation_open_attempt,
  origin, lifecycle_id, candidate_id, record_version, record_sha256,
  lease_token, pass_id, claim_time, stage_generation }))
```

Its body is exactly that tuple and nothing else. `origin` is `claim` for a
normal claim or `orphan-recovery` when a recovering owner observes a record in
`evaluating` with no open-attempt record and writes the pair itself. The record
never gains `packet_id`, `result_id`, an outcome, or a duration.

**Terminal-attempt record** —
`DREAMING_DATA_DIR/shadow-evaluations/v1/attempts/terminal/<terminal_attempt_id>.json`,
appended when the attempt reaches any terminal state.

```
terminal_attempt_id = sha256(canonical({
  schema_version, kind: shadow_evaluation_terminal_attempt,
  open_attempt_id, outcome, stop_reason, result_id_or_null,
  packet_id_or_null, retained_by_pass_id, retained_by_lease_token,
  settle_time, phase_durations }))
```

`retained_by_pass_id` names the pass that **wrote this record**, which is the
owning pass on a normal settlement and the recovering pass after lease loss or
a crash. It is not necessarily the pass named in the open record.

**Relation.** One open-attempt record has zero or one terminal-attempt record.
An attempt is *open* exactly when no terminal record names its
`open_attempt_id`; this is derived by scanning the terminal directory, so no
state is mutated to represent settlement.

**Collision handling.**

- A write whose target digest already exists with a byte-identical body is a
  successful idempotent no-op.
- A write whose target digest already exists with a differing body is refused
  as `shadow-evaluation-attempt-collision` and never overwrites; the pass is
  marked unhealthy.
- Two distinct terminal records naming the same `open_attempt_id` are refused
  as `shadow-evaluation-attempt-settled-twice`; the earliest by `(settle_time,
  terminal_attempt_id)` is authoritative for recovery and accounting, the rest
  are retained as evidence, and the pass is marked unhealthy.
- Two open records for the same `(lifecycle_id, candidate_id, record_version)`
  that are both unsettled are refused as
  `shadow-evaluation-attempt-collision`; the earliest by `(claim_time,
  open_attempt_id)` is recovered, the rest receive terminal records with
  outcome `recovery-superseded`, and the pass is marked unhealthy.

**Semantic result** —
`DREAMING_DATA_DIR/shadow-evaluations/v1/results/<result_id>.json`, immutable
and content-addressed over the evaluation's meaning only:

```
result_id = sha256(canonical({
  lifecycle_id, candidate_id, record_version,
  packet_id, suite_id, harness_digest,
  executor_identity_digests (sorted),
  certificate_status, certificate_sha256,
  authorizing_occurrence_ids (sorted), profile_ids (sorted) }))
```

`pass_id`, `claim_time`, `lease_token`, `open_attempt_id`,
`terminal_attempt_id`, wall-clock durations, and retry counts are **excluded**
from `result_id` and live only in the attempt records. Replaying the same
evaluation over unchanged evidence therefore yields the same `result_id` and
different attempt identities, with no determinism contradiction.

*Why durable records are required:* the evaluator writes its own certificate
receipt, but that receipt binds evaluator identities only. It does not name the
`profile_id` set, the canonical occurrence identities, the lifecycle record
version, the packet the model saw, or the pass and lease that spent the
allowance. The result record binds the semantic authorities; the attempt pair
binds the operational ones and makes abnormal termination recoverable.

**Recovery sweep.** At the start of every pass, before any readiness
derivation, the stage enumerates unsettled open-attempt records and processes
them in deterministic order `(claim_time, open_attempt_id)`. See "Lease loss
and recovery".

## Reuse contract

Reused unchanged:

- `skill-evaluation.py shadow-compile`, `shadow-execute`, `shadow-certify`
  (`:11344-11368`) and `skill-evaluation-harness.py shadow-run`, `shadow-verify`;
- `dreaming-vendor-adapter.py` role `skill-evaluation-executor`, and role
  `evaluation-input-author`'s schema, prompt, task-id shape, refusal codes, and
  response validation;
- `candidate-lifecycle.py` `read`, `transition`, `SHADOW_TRANSITIONS`, package
  identity `candidate_identity` (`:413`) and `verify_package` (`:446`);
- `daemon-lock.py` token-fenced `assert` and `renew` (`:203-225`);
- the existing halt file, pass accounting, dashboard projection, rollback, and
  restore boundaries.

Extended:

- `ROLES` and `ROLE_CONFIG_KEYS` gain one evaluator role;
- `validate_evaluation_input_packet` gains one shadow branch;
- `skill-evaluation.py` gains `shadow-author-packet`;
- **`profile_evaluation_routing.py` gains a computed `execution_authority`
  argument and loses its hard-coded `SHADOW_EXECUTION_BLOCKERS` constant**;
- the existing authoring sensitive-value rejector covers the new packet kind.

Added: three append-only record types (open attempt, terminal attempt,
semantic result), one bounded-subprocess wrapper copying the existing
`_invoke` pattern, one recovery sweep, and one run-report section.

## Data flow

```
pass start (lease held)
  -> recovery sweep over unsettled open-attempt records,
       ordered (claim_time, open_attempt_id); append terminal-attempt records
       and the durable stop reason for any pass fenced out by lease loss
  -> retained profile-audit dispositions + candidate lifecycle records
  -> derive_evaluation_routing(execution_authority = live computed facts)
  -> rows with route=candidate-evaluation and evaluation_execution.available
  -> exclude subjects already attempted in this pass
  -> order by (last_attempt_time, earliest_current_occurrence, lifecycle_id)
  -> reserve: remaining_pass_seconds >= worst_case + deadline_margin,
       every term mechanically enforced
  -> lease assert; append open-attempt record (write-ahead fence)
  -> lease assert; transition ready_for_draft -> evaluating
       (--expected-version, --expected-record-sha256)
  -> materialize package; re-verify inventory hashes to candidate_id
  -> build shadow-author-packet (closed schema, three source roots)
  -> validate packet; refuse on any prohibited field, source, or value
  -> evaluation-input-author model call -> five case prompts, or a refusal code
  -> shadow-compile   (bounded wrapper; sealed run manifest)
  -> shadow-execute   (synchronous; runs to completion inside the reservation)
  -> shadow-certify   (bounded wrapper incl. nested shadow-verify subprocess)
  -> retain semantic result under result_id; retain packet under packet_id
  -> lease assert; transition evaluating -> ready_for_draft
  -> append terminal-attempt record naming open_attempt_id and result_id
  -> add subject to pass-local attempted set; loop while allowance and time remain
  -> run report and dashboard projection
```

## Deterministic and model responsibilities

Deterministic code owns: recovery, readiness derivation, execution-availability
computation, ordering, reservation, claim fencing, package materialization and
re-verification, packet construction and validation, the fixed suite template,
executor and routing derivation from adapter attestation, allowance accounting,
both record identities, and every lifecycle transition.

The model owns exactly one thing: the `task_id` and `prompt` text of five
cases, over a template it cannot change, from a packet it cannot extend.

## Claiming, ordering, and lifecycle exit

**Ordering.** Ready subjects are sorted by `(last_attempt_time,
earliest_current_occurrence_time, lifecycle_id)`. `last_attempt_time` is the
`settle_time` of the subject's most recent terminal-attempt record, or the
`claim_time` of an unsettled open record; a never-attempted subject uses the
epoch and therefore always sorts first. This is a derived index rebuildable by
scanning the append-only records at any time: it holds no claim state, no
mutable rows, and no independent notion of readiness, so it is not a second
queue.

**Pass-local attempted set.** The stage keeps an in-memory set of
`(lifecycle_id, candidate_id)` pairs claimed or recovered during the current
pass. Every terminal result, including every refusal, adds its subject to the
set, and readiness derivation excludes the set. A persistent failure therefore
consumes one attempt and the stage moves on to the next ready candidate while
allowance and reservation permit; it can neither be reclaimed within the pass
nor starve its successors.

**Claim.** The pass re-reads the lifecycle record, requires the record digest
and `record_version` to equal the routed subject, asserts the lease, appends
the open-attempt record, asserts the lease again, then transitions to
`evaluating` with `--expected-version` and `--expected-record-sha256`. A
mismatch is `evaluation-claim-stale`: the candidate is left untouched, a
terminal-attempt record with outcome `claim-stale` is appended, and the stage
proceeds to the next ready candidate.

**Owned exit is unconditional.** While the pass holds a valid lease, every path
after a successful claim ends with a transition back to `ready_for_draft`
before the stage returns, including authoring refusal, packet refusal, compile
refusal, execute failure, any bounded-wrapper timeout refusal, certify
`regression`, `inconclusive`, or `stale`, allowance exhaustion, adapter
failure, and unexpected exception. The exit is written in a
`finally`-equivalent position so that no early return can skip it, and it is
guarded by a lease assertion.

**Halt and reservation.** If the halt file appears, or the remaining pass time
no longer covers the next candidate's reservation, the stage stops before
claiming, retains `halted` or `pass_deadline_reservation_unmet`, and claims
nothing further. Because the reservation is taken before the claim, a halt can
never interrupt a claimed evaluation mid-invocation.

**Terminal results are retryable.** Every non-pass result is terminal for that
pass and retryable in a later pass: the candidate returns to `ready_for_draft`,
keeps its evidence, keeps its recurrence count, and stays visible to the
catalog-aware reviewer, so later semantic occurrences alias to the same group
instead of forking.

**Semantic grouping is never lost,** because on every owned path the group is
in `evaluating` only inside a single pass, on every abnormal path the next
pass's recovery sweep restores it before any new work, and
`ELIGIBLE_CANDIDATE_GROUP_STATES` already includes `ready_for_draft`.

## Lease loss and recovery

Round-one finding T1 and round-two finding R1 are resolved by separating two
regimes and by attributing all durable post-loss evidence to the next valid
owner.

**Normal owned execution.** The lease is asserted immediately before each
durable write: the open-attempt record, the claim transition, the result
retention, the exit transition, and the terminal-attempt record. All assertions
succeed, and the unconditional owned exit above applies.

**Lease loss produces no durable evidence from the fenced-out pass.** If any
assertion fails, the pass is no longer the owner. It performs **no durable
write of any kind**: no transition, no result, no terminal-attempt record, no
lifecycle repair, and no durable accounting or run-report row. It emits
`lease_lost` as **process-return and run-report-only evidence** — a non-zero
process status and a report on its own process output — and returns. That
evidence is diagnostic, not authoritative, and is never persisted to the
durable accounting store by the fenced-out process, because persisting it would
itself be the durable mutation this rule forbids. Recovery is not this pass's
job.

Consequently `lease_lost` is **not** a durable stop reason. Durable stop
reasons and durable outcomes for a fenced pass are retained by the next valid
lease holder, attributed to that holder, and reference the lost pass only as
context carried in the open-attempt record.

**Crash or abnormal termination.** No exit transition runs. The open-attempt
record, already durable and written before the claim, is the recovery
authority, and it carries everything the next owner needs: `origin`,
`lifecycle_id`, `candidate_id`, `record_version`, `record_sha256`,
`lease_token`, `pass_id`, `claim_time`, `stage_generation`.

**Recovery sweep**, run by the next valid lease holder before any readiness
derivation, over unsettled open records in `(claim_time, open_attempt_id)`
order. Every action below is performed by the recovering pass while holding the
lease, and every appended terminal record names the recovering pass in
`retained_by_pass_id`:

| Observed state | Action by the next valid owner |
| --- | --- |
| Unsettled open record, lifecycle record in `evaluating`, version and digest match | Assert lease, transition to `ready_for_draft`, append a terminal record with outcome `evaluation-abandoned` and stop reason `recovered_after_owner_loss`, count as recovered in this pass's accounting |
| Unsettled open record, record in `evaluating`, version or digest differ | Append a terminal record with outcome `recovery-superseded`; perform no transition; mark the pass degraded |
| Unsettled open record, record not in `evaluating` | Append a terminal record with outcome `recovery-not-required`; perform no transition |
| Two or more unsettled open records for the same `(lifecycle_id, candidate_id, record_version)` | Refuse `shadow-evaluation-attempt-collision`; recover only the earliest by `(claim_time, open_attempt_id)`; append `recovery-superseded` terminal records for the rest; mark the pass unhealthy |
| Two distinct terminal records naming one `open_attempt_id` | Refuse `shadow-evaluation-attempt-settled-twice`; treat the earliest by `(settle_time, terminal_attempt_id)` as authoritative; retain the rest as evidence; mark the pass unhealthy |
| A record digest already present with a differing body | Refuse `shadow-evaluation-attempt-collision`; never overwrite; mark the pass unhealthy |
| Lifecycle record in `evaluating` with no open-attempt record | Append an open record with `origin: orphan-recovery` naming the recovering pass and lease, transition to `ready_for_draft`, append its terminal record with outcome `evaluation-orphaned`; mark the pass degraded |

The orphan row keeps the one-to-zero-or-one relation intact: every terminal
record names a real open record, and no third record type is introduced.

A subject recovered by the sweep is added to the pass-local attempted set, so a
recovering pass never immediately re-claims what it just repaired.

## Work-conserving behavior

The stage does not reserve a batch. It repeats: derive readiness with live
execution authority, exclude the pass-local attempted set, order, reserve,
claim, evaluate, exit, until the evaluation allowance is spent, the reservation
can no longer be met, or no ready candidate remains. Eligibility derivation,
ordering, and recovery spend no allowance; only a started authoring model call
or a started trial batch does.

Unused allowance must be explained by exactly one retained stop reason. Every
durable stop reason is written by the pass that held the lease when it was
written:

- `eligible_input_exhausted` — a final readiness derivation returned no
  claimable `candidate-evaluation` row, and the run report lists the near-miss
  rows with their gate reasons and, for subjects skipped because they were
  already attempted or recovered this pass, that reason explicitly;
- `evaluation_allowance_spent`;
- `pass_deadline_reservation_unmet`;
- `evaluation_elapsed_budget_exhausted`;
- `halted`;
- `executor_unavailable`;
- `recovered_after_owner_loss` — retained by the recovering pass for each
  attempt it settled on behalf of a fenced-out or crashed predecessor.

`lease_lost` is deliberately **absent** from this list. A pass that loses its
lease writes nothing durable, so it contributes no durable accounting row at
all; its `lease_lost` status exists only as a process return and a report on
that process's own output. The durable record of what happened to its claimed
subject is the terminal-attempt record and stop reason written later by the
next valid owner.

Reconciliation is therefore scoped to what a pass durably did while holding the
lease: every subject it claimed, plus every attempt it settled on a
predecessor's behalf. A run with unused allowance and no stop reason fails
accounting reconciliation. A pass has no obligation to account for a
predecessor's abandoned work beyond the attempts its own sweep settled.

## Failure model

| Failure | Result |
| --- | --- |
| No `skill-evaluation-executor` configured, unhealthy, or unattested | Routing rows report `available: false` with the matching computed reason; stage skips with `executor_unavailable`; no lifecycle state changes |
| Execution authority incomplete or malformed | Fail closed: `available: false` with `shadow-execution-authority-unknown`; stage does not start |
| Remaining pass time below the computed reservation | Retain `pass_deadline_reservation_unmet`; claim nothing |
| Lifecycle record changed between routing and claim | `evaluation-claim-stale`; append a terminal record; no transition; try the next ready candidate |
| Package bytes missing, or inventory does not re-hash to `candidate_id` | Refuse `candidate-package-tampered`, retain a terminal result, exit `evaluating`, do not build a packet or call the model |
| Packet fails its own validator | Retain the matching `shadow-packet-*` refusal, exit `evaluating`, never call the model |
| Authoring model returns `insufficient_information` | Retain the refusal code as a terminal result, exit `evaluating`, retry in a later pass |
| Authoring result malformed, duplicated, or template-drifted | Retain `authoring-result-invalid`, exit `evaluating`; the existing adapter validation is the gate |
| Designed suite fails `shadow_suite` validation | Retain `suite-invalid`, exit `evaluating` |
| `shadow-compile` refuses | Retain `compile-refused` with the evaluator message, exit `evaluating` |
| `shadow-execute` fails, exceeds a per-call limit, or an adapter attestation mismatches | Retain `execute-failed` or `execute-timeout`, exit `evaluating`; no partial result is certified |
| Authoring adapter exceeds `author_call_bound` | Existing `_invoke` terminates the process group; retain `author-call-timeout`, exit `evaluating` |
| `shadow-compile` exceeds `compile_bound` | Bounded wrapper terminates the process group; retain `compile-timeout`, exit `evaluating` |
| `shadow-certify` or its nested `shadow-verify` subprocess exceeds `certify_bound` | Bounded wrapper terminates the whole process group, not just the direct child; retain `certify-timeout`, exit `evaluating` |
| A lifecycle transition or record write exceeds `settlement_bound` | Bounded wrapper terminates the process group; retain `settlement-timeout`; the record is repaired by the next pass's recovery sweep |
| `shadow-certify` returns `stale` | Retain `certificate-stale`; candidate or executor drifted mid-run; exit and retry later |
| `shadow-certify` returns `regression` or `inconclusive` | Retain the result as terminal evidence, exit `evaluating`, grant no authority |
| `shadow-certify` returns `pass` | Retain the result, exit to `ready_for_draft`, grant no install or publication authority |
| Halt file appears between operations | Stop before the next claim, retain `halted`; any claimed candidate still completes its owned exit |
| Remaining pass time insufficient for the full computed formula | Refuse before the claim with `pass_deadline_reservation_unmet`; no open-attempt record is written |
| Lease assertion fails at any point | Perform no durable write; emit `lease_lost` as process-return and run-report-only evidence; leave the unsettled open-attempt record for the next valid owner, which retains the durable outcome and stop reason |
| Crash or power loss mid-stage | Next pass's recovery sweep appends an `evaluation-abandoned` terminal record and returns the lifecycle record to `ready_for_draft` before deriving readiness |
| Record in `evaluating` with no open-attempt record | Recovering owner appends an `origin: orphan-recovery` open record and its `evaluation-orphaned` terminal record, returns the record to `ready_for_draft`, marks the pass degraded |
| Two unsettled open records for one subject and record version | Refuse `shadow-evaluation-attempt-collision`; recover the earliest; supersede the rest; mark the pass unhealthy |
| Two terminal records naming one open record | Refuse `shadow-evaluation-attempt-settled-twice`; earliest is authoritative; retain the rest as evidence; mark the pass unhealthy |
| Any record digest already present with differing content | Refuse `shadow-evaluation-attempt-collision` or `shadow-evaluation-result-collision`; never overwrite; mark the pass unhealthy. A byte-identical body is an idempotent no-op |
| Same subject reached twice in one pass | Impossible by the pass-local attempted set; if observed, refuse `evaluation-subject-reclaimed` and mark the pass unhealthy |

## Hard invariants

1. On every path where the pass holds a valid lease for the whole stage, no
   pass ends with a candidate in `evaluating`.
2. After a failed lease assertion, the pass performs no durable write of any
   kind, including no accounting or run-report persistence; its `lease_lost`
   status exists only as a process return and process-output report.
3. Durable recovery outcomes and durable stop reasons for a fenced-out or
   crashed pass are retained only by the next valid lease holder and attributed
   to that holder.
4. Every candidate left in `evaluating` by abnormal termination has exactly one
   unsettled, immutable open-attempt record naming its origin, lifecycle,
   candidate, record version, record digest, lease token, pass, and claim time;
   the next valid owner settles it before deriving readiness or claiming new
   work.
5. Every record is written exactly once and is content-addressed over its own
   body; no record is rewritten, extended, or deleted. A byte-identical rewrite
   is an idempotent no-op and a differing body under the same digest is a
   refusal.
6. Every terminal-attempt record names exactly one existing `open_attempt_id`,
   and every open-attempt record has zero or one terminal-attempt record.
   Openness is derived by scan, never stored as a mutable flag.
7. Every semantic result names the exact `candidate_id` whose materialized
   bytes re-hash to it, the exact canonical occurrence identities that
   authorized it, and the exact `packet_id` the model saw.
8. `result_id` is computed over semantic fields only and never over pass, lease,
   attempt, or timing fields; those live only in the attempt records.
9. A shadow evaluation grants no install, publication, portfolio, or admission
   authority, and adds no forward lifecycle edge.
10. The authoring adapter receives exactly one closed packet and no other input
    path; any unknown key, prohibited source, or sensitive value is a refusal
    before the model call.
11. Evaluation never creates, consumes, or resets recurrence evidence; a
    returned candidate keeps its evidence, count, and group identity.
12. Every claimed subject has exactly one retained terminal-attempt record per
    pass, and no `(lifecycle_id, candidate_id)` is claimed twice in one pass.
13. `evaluation_execution.available` in a routing row is computed from the same
    live authority facts the stage uses, fails closed, and is true if and only
    if the stage would attempt the evaluation.
14. Every term of the reservation formula is mechanically enforced by an
    existing or specified bounded process-group call with terminate-then-kill
    cleanup and a named terminal refusal; no term is an unenforced estimate.
15. The stage never starts an invocation whose computed worst-case bound,
    including termination overhead, exceeds the remaining pass time less the
    deadline margin.
16. Every started authoring call and started trial batch spends allowance;
    recovery, readiness derivation, ordering, and reservation never do.
17. Repair outcomes remain report-only and never enter this stage.
18. The pass keeps exactly one writer lease and one owner.

## Acceptance criteria

- **AC-E1:** With one recurrence-gated candidate and healthy execution
  authority, one pass produces one semantic result whose certificate status is
  one of `pass`, `regression`, `inconclusive`, or `stale`, and the record ends
  in `ready_for_draft` with exactly one open-attempt record and exactly one
  terminal-attempt record naming it.
- **AC-E2:** The result's candidate identity equals the lifecycle
  `current_candidate_id`, and the sealed run manifest's candidate inventory
  equals the lifecycle revision `files`.
- **AC-E3a:** The semantic result names the exact `profile_id` set, canonical
  occurrence identities, lifecycle record version, `packet_id`, suite id,
  harness digest, and executor identities, and contains no pass, lease,
  attempt, or timing field.
- **AC-E3b:** The open-attempt record names exactly `origin`, `lifecycle_id`,
  `candidate_id`, `record_version`, `record_sha256`, `lease_token`, `pass_id`,
  `claim_time`, and `stage_generation`, and never gains an outcome, duration,
  `packet_id`, or `result_id`. Its terminal-attempt record names that exact
  `open_attempt_id`, the outcome, the stop reason, `retained_by_pass_id`,
  `settle_time`, phase durations, and the `result_id` when one exists.
- **AC-E4:** A `pass` result produces no install, publication, or forward
  lifecycle transition, and no estate mutation.
- **AC-E5:** Every failure row in the failure model that occurs while the lease
  is held leaves the lifecycle record in `ready_for_draft` with exactly one
  appended terminal-attempt record, and a later pass can claim the same
  candidate again.
- **AC-E6:** After simulated lease loss the fenced-out pass performs zero
  durable writes — no transition, no result, no terminal-attempt record, no
  durable accounting or run-report row — and surfaces `lease_lost` only as a
  process return and process-output report, leaving its pre-loss open-attempt
  record unsettled. The next valid pass's recovery sweep, before deriving
  readiness, appends the terminal-attempt record with `evaluation-abandoned`,
  returns the lifecycle record to `ready_for_draft`, and retains the durable
  stop reason `recovered_after_owner_loss` attributed to itself, in
  `(claim_time, open_attempt_id)` order; superseded, not-required, collision,
  settled-twice, and orphan cases behave as tabulated.
- **AC-E7:** After any terminal result, a fourth matching occurrence aliases to
  the same lifecycle group; no new group is created.
- **AC-E8:** The authoring adapter is invoked with exactly one packet path; a
  packet carrying an unknown key, a prohibited field or source, or a sensitive
  value is refused with its stable code before any model call; the exact packet
  bytes are retained under `packet_id`.
- **AC-E9:** Unused evaluation allowance is always explained by exactly one
  durable stop reason written by the lease-holding pass, and accounting
  reconciles exactly the subjects that pass claimed plus the attempts its sweep
  settled. `lease_lost` never appears as a durable stop reason, and a fenced-out
  pass contributes no durable accounting row.
- **AC-E10:** Replaying an evaluation over unchanged evidence with pinned
  author and executor fixtures produces an identical `result_id` and distinct
  `open_attempt_id` and `terminal_attempt_id` values.
- **AC-E11:** The stage computes the full worst-case bound — authoring,
  attestation, trials, compile, certify, settlement, and termination overhead —
  before claiming, and refuses with `pass_deadline_reservation_unmet` without
  writing an open-attempt record when remaining pass time is below that bound
  plus the deadline margin. Every completed pass finishes inside the
  3,600-second deadline with the margin intact.
- **AC-E14:** Every bounded subprocess the stage starts is terminated within
  its declared bound plus `termination_grace`, its whole process group is gone,
  and the breach surfaces as its named terminal refusal — including
  `shadow-certify`'s nested `shadow-verify` child.
- **AC-E12:** For every permutation of execution authority, a routing row's
  `evaluation_execution.available` and its computed reasons exactly match what
  the stage does: the stage never starts when `available` is false, never
  reports `executor_unavailable` when `available` is true, and unknown or
  malformed authority yields `available: false` with
  `shadow-execution-authority-unknown`.
- **AC-E13:** With two ready candidates where the first-sorted fails
  terminally, the second is still claimed and evaluated in the same pass while
  allowance and reservation permit, and neither subject is claimed twice; in a
  later pass the previously attempted subject sorts after any never-attempted
  subject.

## Check contract

| Check | Criterion | Setup | Pass evidence | Failure evidence |
| --- | --- | --- | --- | --- |
| CHK-01 | AC-E1, AC-E2 | One lifecycle record with three distinct current occurrences, shadow-ready recommendation, one staged package; healthy fixture evaluator and fixture author | One result retained; manifest `candidate_inventory` equals revision `files`; final state `ready_for_draft`; exactly one open record and one terminal record naming it | No result, a result naming a different candidate id, an unsettled open record, or any record written twice |
| CHK-02 | AC-E3a, AC-E3b | CHK-01 with two distinct authorizing profiles | Result lists both `profile_id`s, all three canonical occurrence ids, record version, `packet_id`, suite id, harness digest, executor identities, and no pass/lease/timing field; open record's key set is exactly the nine declared fields; terminal record names that `open_attempt_id`, outcome, stop reason, `retained_by_pass_id`, `settle_time`, durations, `result_id` | Any authority absent or mismatched, a pass/lease/timing field in the result, or an outcome/result field in the open record |
| CHK-03 | AC-E4 | CHK-01 forced to `pass` | Skill roots byte-identical before and after; no publisher call; no transition other than in and out of `evaluating` | Any estate mutation, publication, or forward transition |
| CHK-04 | AC-E5 | One fixture per lease-held failure row: tampered package, packet refusal, authoring refusal, malformed authoring result, invalid suite, compile refusal, execute failure, certify `stale` | Each leaves `ready_for_draft`, appends exactly one terminal record with its named outcome, and a second pass re-claims the same candidate | Any row leaving `evaluating`, appending no terminal record, appending two, or blocking re-claim |
| CHK-05 | AC-E6 | Six fixtures: lease assertion forced to fail mid-stage; open record left unsettled by a killed pass; two unsettled open records for one subject and version; two terminal records naming one open record; record in `evaluating` with no open record; record not in `evaluating` with an unsettled open record | A byte-level snapshot of the whole data directory before and after the fenced-out pass is identical, and its `lease_lost` appears only in process output; the next pass sweeps in `(claim_time, open_attempt_id)` order before readiness derivation, appends `evaluation-abandoned` with `recovered_after_owner_loss` attributed to itself; collision recovers the earliest and supersedes the rest, unhealthy; settled-twice takes the earliest, unhealthy; orphan appends an `origin: orphan-recovery` pair, degraded; not-required appends `recovery-not-required` with no transition | Any durable byte written by the fenced-out pass, a durable `lease_lost` accounting row, an unsettled open record surviving a sweep, non-deterministic recovery order, an overwritten record, or a terminal record naming no open record |
| CHK-06 | AC-E7 | Run CHK-04's certify-`stale` fixture, then supply a fourth matching occurrence | Reviewer group context includes the same `lifecycle_id`; no new lifecycle record | A new lifecycle id appears for the same procedure |
| CHK-07 | AC-E8 | Packets built with, in turn: an extra unknown key, an estate-census field, a transcript fragment, an absolute path, a UUID occurrence id, a `profile_id`, a credential-shaped token | Each refused with its stable `shadow-packet-*` code before any model call; adapter invocation carries exactly one packet path argument; retained packet bytes re-validate | Any prohibited content reaching the model, a silently stripped key, or a second adapter input path |
| CHK-08 | AC-E9, AC-E13 | Two ready candidates where the first-sorted always fails terminally, with allowance for two; then a variant with allowance for one; then zero ready candidates | Run one evaluates both subjects, each exactly once, and reconciles; run two stops with `evaluation_allowance_spent`; run three stops with `eligible_input_exhausted` listing near-miss and already-attempted reasons; a later pass sorts the attempted subject after a never-attempted one | A failing candidate blocking its successor, any subject claimed twice in one pass, or unused allowance with no stop reason |
| CHK-09 | AC-E11, AC-E14, and sets the unresolved allowances | One real configured evaluator, one candidate, natural pass; a synthetic run with remaining pass time set just below the computed bound; and one deliberately overrunning fixture per bounded term (author, executor call, compile, certify with a sleeping nested `shadow-verify`, lifecycle transition) | Every term of the printed formula is present and finite before the claim; the synthetic run refuses with `pass_deadline_reservation_unmet` and writes no open record; each overrun fixture is terminated within its bound plus `termination_grace`, leaves no surviving process in the group, and surfaces its named refusal; retained per-call and per-phase cost becomes the configured `author_call_bound`, `executor_call_bound`, `compile_bound`, `certify_bound`, `lifecycle_transition_bound`, `record_write_bound`, `termination_grace`, and `deadline_margin` | A missing or infinite formula term, a start accepted below the bound, an open record written by a refused reservation, a surviving child process after any bound, or allowances configured without this measurement or an owner-recorded provisional bound |
| CHK-10 | AC-E10 | CHK-01 run twice over unchanged evidence with **both** a pinned deterministic author fixture returning a fixed case set and a pinned deterministic executor fixture returning fixed trial output | Identical `result_id`; distinct `open_attempt_id` and `terminal_attempt_id`; both terminal records name the same `result_id` and different open records | Differing `result_id` without a changed semantic input, or identical attempt identities across passes |
| CHK-11 | AC-E12 | The authority permutation matrix: unconfigured, configured-unhealthy, healthy-unattested, suite authority missing, authoring authority missing, package unavailable, malformed authority object, and all-present | Each permutation's routing row reports the matching computed reason and `available` value, and the stage's actual behavior matches the row in every case | Any row claiming availability the stage lacks, any stage start under `available: false`, or a hard-coded blocker constant surviving in `profile_evaluation_routing.py` |

CHK-01 through CHK-08, CHK-10, and CHK-11 are deterministic and run with
fixture author and evaluator adapters in the standalone suites. CHK-09 is the
only check requiring a real model and a natural installed pass; its synthetic
reservation-refusal half is deterministic and runs with the others.

## Migration and rollback

Migration is additive. No existing record changes shape. Absent the new
`evaluators` config section, execution availability computes false, every
routing row reports `shadow-executor-unconfigured`, the stage reports
`executor_unavailable`, changes no state, and the rest of the pass is
unaffected, so an unconfigured host keeps today's behavior exactly.

The routing change is the one behavioral difference on an unconfigured host:
`evaluation_execution.reasons` becomes computed rather than constant, so the
projection's reason codes change names. This is a projection-only change with
no state migration; the dashboard reads the new codes from the same key.

Because all attempt evidence is append-only, migration adds two directories,
`shadow-evaluations/v1/attempts/open/` and `.../terminal/`, and never migrates,
rewrites, or reformats an existing record. A record written by any generation
remains valid for every later generation, because its digest is computed over
its own declared schema version and body.

Rollback reinstalls the prior generation and disables the stage. It deletes no
task-profile receipt, disposition, lifecycle record, certificate, open-attempt
record, terminal-attempt record, or semantic result. Because the open-attempt
records are the recovery authority, rollback must retain **both** directories:
retaining only the open directory would make every settled attempt look
unsettled and cause a restored sweep to re-recover already-repaired records.
A host rolled back mid-evaluation is repaired by the sweep when the stage is
restored, and until then the candidate sits in `evaluating`, which is visible,
detectable, and reported as degraded rather than silent.

A rolled-back host that never restores the stage leaves unsettled open records
permanently. That is acceptable and detectable: they are inert immutable files,
the dashboard reports the affected lifecycle records as degraded, and no
authority reads them except the sweep.

Restore re-enables the stage only after the generation-bound self-test, and the
first restored pass runs the recovery sweep before deriving readiness.

## Definition of Done: fenced bounded shadow evaluation of a routed candidate

- [ ] The `skill-evaluation-executor` role is registered in the existing
      adapter registry and health-checked by the existing loop, with no second
      configuration file, owner, or lease.
- [ ] Executors and routing documents are derived from adapter attestation, and
      `shadow_attest` accepts them against a real `version` response.
- [ ] `profile_evaluation_routing.py` computes `evaluation_execution` from
      injected live authority, fails closed on unknown authority, and contains
      no hard-coded blocker constant.
- [ ] `shadow-author-packet` builds and validates a closed, versioned packet
      with `additionalProperties: false`, an explicit prohibited-source list,
      sensitive-value rejection, and stable refusal codes; the author adapter
      is invoked with that packet and nothing else.
- [ ] The existing `evaluation-input-author` boundary designs five case prompts
      with its schema, prompt, task-id shape, refusal codes, and response
      validation unchanged.
- [ ] The stage computes a full-invocation worst-case bound covering authoring,
      attestation, trials, compile, certify, settlement, and termination
      overhead, and refuses to start below it without writing any record.
- [ ] Every term of that bound is mechanically enforced by a bounded
      process-group call with terminate-then-kill cleanup and a named terminal
      refusal, including `shadow-certify`'s nested verification subprocess, and
      halt is checked between bounded operations only.
- [ ] One scheduled pass claims ready candidates derived from existing
      dispositions and lifecycle authority, ordered by last attempt then
      earliest occurrence then lifecycle id, with no new queue row.
- [ ] An immutable open-attempt record is durable before the claim transition,
      is never rewritten or extended, and is settled only by appending one
      immutable terminal-attempt record naming its digest; openness is derived
      by scan, and collision, settled-twice, superseded, and orphan cases behave
      as tabulated.
- [ ] The materialized package re-hashes to the claimed `candidate_id` before
      any packet is built.
- [ ] `shadow-compile`, `shadow-execute`, and `shadow-certify` run unmodified
      and produce one certificate covering triggering, task performance, and
      overall regression.
- [ ] The semantic result binds candidate, occurrences, profiles, lifecycle
      version, and packet, and excludes pass, lease, and timing fields, which
      live only in the terminal-attempt record.
- [ ] Every lease-held path exits `evaluating` in the same pass; a fenced-out
      pass writes zero durable bytes and reports `lease_lost` only through its
      process return and output; the next valid owner retains the durable
      outcome and stop reason and repairs abandoned, superseded, colliding, and
      orphaned records before any new work.
- [ ] No subject is claimed twice in one pass, and a persistent failure does
      not starve later candidates.
- [ ] A fourth matching occurrence after any terminal result aliases to the
      same group.
- [ ] Unused evaluation allowance is always explained by one durable stop
      reason written by the lease-holding pass, `lease_lost` is never a durable
      stop reason, and accounting reconciles the subjects that pass claimed plus
      the attempts its sweep settled.
- [ ] A passing result grants no install, publication, or admission authority.
- [ ] CHK-01 through CHK-08, CHK-10, and CHK-11 pass deterministically; CHK-09
      records the measured cost that sets the configured allowances.
- [ ] The plan baton, this work order, and the local commits are complete, and
      the slice has a reviewed PR targeting `feature/multi-cli-dreaming`.

## Changing reframe status from OPEN to CLEAR

Reframe status becomes CLEAR when a round-three design review records all of:

1. **Owner approval of the extension framing** — that registering a fourth
   adapter role, adding a shadow subject mode to the existing authoring
   boundary, adding three append-only record types, and adding one
   bounded-subprocess wrapper are extensions of existing authorities rather than
   the forbidden second queue, owner, scheduler, transport, catalog stage, or
   classifier.
2. **A dry-run attestation trace** showing the configured
   `skill-evaluation-executor` adapter's `version` response satisfying
   `shadow_attest` against the derived executors document, proving E1 needs no
   invented identity.
3. **One validated shadow suite** produced from the fixed template plus five
   model-authored prompts, accepted by `shadow_suite` in `candidate_only`
   routing mode with no `--catalog-dir`, proving E2 needs no catalog stage and
   no second authoring stack.
4. **A proven finite and enforced reservation** — CHK-09 evidence showing the
   whole formula, term by term, computed before a claim; a refusal with
   `pass_deadline_reservation_unmet` and no record written when remaining time
   is insufficient; and one overrun fixture per bounded term (author, executor
   call, compile, certify with a sleeping nested `shadow-verify`, lifecycle
   transition) terminated within its bound plus `termination_grace` with no
   surviving process in the group and its named refusal surfaced. Acceptance
   must also record that halt is deliberately not observed inside
   `shadow-execute`, and that the reservation rather than interruption is what
   protects the pass deadline.
5. **Acceptance of the append-only fenced-recovery regime** — that a fenced-out
   pass writes zero durable bytes and that `lease_lost` is process-return
   evidence only; that the next valid owner retains the durable outcome and
   stop reason; that one immutable open record settled by one immutable
   terminal record is sufficient recovery authority; and that the tabulated
   collision, settled-twice, supersession, not-required, and orphan behaviors
   are correct.
6. **A resolved allowance decision** — either CHK-09's measured per-call and
   per-phase cost, or an explicit owner-set provisional bound with the
   measurement scheduled, replacing the "unresolved policy decision" row in the
   constraint table.
7. **Confirmation that no revisit condition in the constraint table has
   fired**, in particular the pass-deadline and packet-prohibition rows, given
   the added stage.

Until all seven are recorded, implementation returns to this document rather
than starting the stage.
