# Bounded shadow evaluation of a routed candidate

## Objective

Let one scheduled owner pass claim one recurrence-gated shadow candidate,
have the existing candidate-blind authoring boundary design its observed-task
cases, run the existing `shadow-compile` / `shadow-execute` / `shadow-certify`
flow under an explicit allowance, retain the certificate as immutable evidence
bound to the exact candidate and occurrence authorities, and always leave
`evaluating` in the same pass without install or publication authority.

This design follows the principles in
`skills/skill-review/SKILL.md#dreaming-design-principles` and continues the
funnel specified in `docs/task-opportunity-evidence-design.md`.

## Lane

**Systemic.** This change gives the scheduled owner a third bounded model-and-
execution stage, adds a fourth adapter role to the existing registry, extends
the authoring boundary to a second subject class, and changes how the pass
deadline, accounting, dashboard, halt, and rollback treat a candidate that is
mid-evaluation. It is not a local fix inside one function.

## Non-goals

- Do not add a second evaluation queue. Ready candidates are derived from
  retained dispositions plus candidate-lifecycle authority.
- Do not add a second scheduled owner, scheduler, writer lease, or transcript
  transport.
- Do not add a second catalog stage or a deterministic semantic classifier.
- Do not evaluate more than one candidate per pass in the first generation.
- Do not grant install, publication, portfolio, or admission authority from a
  passing evaluation. `evaluating` has no forward edge to `portfolio_pending`
  in `DECLARED_TRANSITIONS` and this design does not add one.
- Do not invent a repair candidate. `missed-skill` and
  `wrong-or-incomplete-skill` stay report-only until immutable candidate
  evidence exists for them, which is a separate work order.
- Do not let the model see unrelated portfolio state, transcripts, credentials,
  home state, or dashboards when designing cases.
- Do not leave a candidate in `evaluating` across passes.
- Do not broaden into general portfolio governance.

## Constraint provenance and reframe gate

| Constraint | Provenance and binding evidence or owner | Protects | Revisit when |
| --- | --- | --- | --- |
| No second evaluation queue; ready candidates derive from retained dispositions and candidate-lifecycle records | Work-order instruction 2026-08-29 and the existing derived-view rule in `docs/task-opportunity-evidence-design.md` | One recurrence authority; no divergent duplicate queue state | A derived view provably cannot express claim fencing that only durable queue rows can carry |
| No second scheduled owner, scheduler, or writer lease | Existing sole-Mac-mini ownership and `daemon-lock.py` writer lease | No overlapping writers or competing model spend | The owner explicitly changes machine topology |
| Evaluation case design stays with the existing `evaluation-input-author` model boundary, candidate-blind to unrelated portfolio state | Existing boundary in `dreaming-vendor-adapter.py:3465-3520` with schema `:2986` and prompt `:3021`; design-doc rule that the LLM owns designing observed-task evaluation cases | One authoring contract, one privacy boundary, no second model stage | A measured model failure cannot be corrected through the packet, schema, or bounded review |
| Evaluation consumes the exact immutable candidate package and canonical occurrence identities, never reviewer claims or report snapshots | `docs/task-opportunity-evidence-design.md` hard invariants 1-3; `candidate-lifecycle.py:413` package identity | A certificate always names what was actually evaluated | Never; a weaker binding invalidates the certificate |
| A passing evaluation grants no install or publication authority | Design-doc Definition of Done ("without granting premature installation authority") and `DECLARED_TRANSITIONS` having no `evaluating -> portfolio_pending` edge | Unreviewed mutation cannot reach the estate | A separate reviewed work order defines an admission path |
| A pass that enters `evaluating` must leave it before the pass ends | Work-order instruction 2026-08-29; `candidate-lifecycle.py:1011` refuses `collect` in `evaluating`; `ELIGIBLE_CANDIDATE_GROUP_STATES` (`dreaming-core.py:100-105`) hides evaluating groups from the reviewer | A later semantic occurrence cannot fork a new recurrence group | Evaluating groups become visible for aliasing and evidence accumulation under a separate reviewed slice |
| The owner pass keeps its existing 3,600-second hard deadline | Installed `daemon-pass.sh:33` backstop inside the four-hour cadence, as recorded in `docs/task-opportunity-evidence-design.md` | Profiling, review, evaluation-input, and evaluation all settle without scheduler overlap | The four-hour cadence changes, or measured stage cost no longer fits and the deadline is redesigned |
| One candidate evaluated per pass in the first generation | Policy decision; no measured shadow-trial cost exists yet | An unmeasured stage cannot consume the pass | Measured cost from the first natural evaluation run shows headroom for more |
| Deterministic trial count per candidate is `executors x (cases + task_value_cases) + executors` | Derived from `skill-evaluation-harness.py:2344-2352` (control plus candidate treatment for `task_value`) and `:2330-2342` (one `version` call per executor) | Cost is predictable before the stage starts | The harness changes its treatment or attestation plan |
| Minimum conforming suite is five cases, one per required routing class | Derived from `skill-evaluation.py:10707-10713`, which requires exactly the set `routing_positive`, `routing_close_negative`, `routing_unrelated`, `routing_conflict`, `task_value` | The three gates are all observable | The evaluator changes its required class set |
| `max_evaluations_per_run`, the evaluation stage second budget, and per-trial `timeout_seconds` / `token_budget` / `turn_budget` / `tool_budget` / `output_bytes` | **Unresolved policy decision.** No measured shadow-trial cost exists in this repository; the only shadow runs are deterministic fixtures in `test-shadow-mutation-boundary.sh` | Bounded model cost and a deadline-safe pass | Set them from CHK-09's measured single-candidate run; do not hard-code a number before that measurement |

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
   view of candidate readiness and could diverge from the lifecycle record,
   and a separate owner could spend model capacity outside the pass deadline
   and accounting.

4. **What is the simplest design without it?**
   Keep every authority singular and extend three existing components:
   register the already-implemented `skill-evaluation-executor` adapter role in
   the existing adapter registry; give the existing `evaluation-input-author`
   boundary a shadow subject mode over a fixed suite template; and drive the
   existing `skill-evaluation.py` shadow subcommands from the existing
   scheduled owner. Ready candidates come from the existing derived routing
   view. No new queue, owner, scheduler, transport, catalog stage, or
   classifier.

5. **Which option has fewer trusted components?**
   The extension option. It adds one adapter role to a registry loop that
   already validates roles generically (`dreaming-core.py:9507-9565`), one
   packet kind to an authoring boundary whose model contract is already generic
   over a template, and one durable receipt type. A parallel shadow evaluation
   subsystem would add a queue, an owner process, a lease, a second accounting
   path, and a second dashboard authority. The extension option is selected.

### Correction to the previous framing

The previous revision of this document claimed four new subsystems. Three of
those claims were too strong and are corrected here:

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
  (`skill-evaluation-harness.py:812-865`). A safety grader can be a declared
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

One stage inside the existing pass, four extension points, one new durable
record. Trusted components are unchanged except where noted.

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

### E2. Give the authoring boundary a shadow subject mode

Add `skill-evaluation.py shadow-author-packet`, producing kind
`safe_shadow_evaluation_authoring_packet`, and route to it from
`validate_evaluation_input_packet` when the operation names a shadow subject.

*Why a new packet schema is required:* the existing packet embeds a v1
`suite_template`, `policy_contract`, `compilation_contract`, `routing_contract`,
and `source_catalog` that have no shadow equivalent, and its validator refuses
any suite that is not a normalized cross-executor schema-2 suite. A shadow
packet carries instead: the candidate contract text and inventory, the fixed
shadow suite template, the derived executor contract, the harness digest, and
the routing mode.

*What is deliberately reused unchanged:* the model contract. The model still
returns only `{outcome, summary, cases:[{id, task_id, prompt}]}` under
`evaluation_input_author_schema` (`dreaming-vendor-adapter.py:2986-3018`) with
the existing prompt (`:3021-3060`), the existing task-id shape
`AUTHOR_TASK_ID_RE` (`:235`), the existing refusal codes
`AUTHOR_REASON_CODES` (`:236-240`), and the existing response validation
(`:3196-3270`) that pins `id` and `class` to the template and forbids
duplicate prompts. Candidate-blindness to unrelated portfolio state therefore
holds by construction: the packet is the model's only input, and it contains
one candidate.

*Fixed template fields, owned deterministically:* `id`, `class`, `routing`,
`artifacts`, `graders`, and `fixture` for five cases, one per required routing
class, in `candidate_only` routing mode.

*Why `candidate_only`:* it needs no `--catalog-dir`
(`skill-evaluation.py:10852-10856`), so no catalog stage is created. The
`routing_close_negative` and `routing_unrelated` cases still prove
non-triggering, and `routing_conflict` is expressed with empty `catalog_loads`
as the validator requires (`:10668-10680`).

### E3. Derive the ready candidate from existing authority

Reuse `derive_evaluation_routing`. A candidate is ready when its row has
`route == "candidate-evaluation"`, meaning the disposition is a current v3
`no-covering-skill` audit with a one-to-one occurrence boundary, and the bound
lifecycle record has three distinct current canonical occurrences inside the
30-day window, a shadow-ready recommendation naming the current candidate, and
no uncertain, covering, or tombstone blocker.

No queue row is written. Ordering is deterministic: candidates sort by
`(earliest current occurrence time, lifecycle_id)`, and the pass claims the
first whose lifecycle record still matches its routed subject digest.

### E4. Run the stage inside the existing pass

The stage runs after review and evaluation-input settle, under the existing
writer lease and the existing 3,600-second deadline. It performs, in order:
claim, materialize, author, compile, execute, certify, retain, exit.

### E5. One new durable record

`DREAMING_DATA_DIR/shadow-evaluations/v1/<receipt-sha256>.json`, immutable and
content-addressed.

*Why a durable receipt is required:* the evaluator writes its own receipt under
its shadow receipt root, but that receipt binds evaluator identities only. It
does not name the `profile_id` set, the canonical occurrence identities, the
lifecycle record version, or the pass that spent the allowance, so the funnel
could not later prove which observed tasks authorized which certificate. The
new record binds them and is the dashboard's projection source.

## Reuse contract

Reused unchanged:

- `skill-evaluation.py shadow-compile`, `shadow-execute`, `shadow-certify`
  (`:11344-11368`) and `skill-evaluation-harness.py shadow-run`, `shadow-verify`;
- `dreaming-vendor-adapter.py` role `skill-evaluation-executor` and role
  `evaluation-input-author`, including the author schema, prompt, task-id
  shape, refusal codes, and response validation;
- `candidate-lifecycle.py` `read`, `transition`, `SHADOW_TRANSITIONS`, package
  identity `candidate_identity` (`:413`) and `verify_package` (`:446`);
- `profile_evaluation_routing.py` routing rows and the recurrence gate;
- the existing writer lease, halt file, pass accounting, dashboard projection,
  rollback, and restore boundaries.

Extended: `ROLES`, `ROLE_CONFIG_KEYS`, `validate_evaluation_input_packet`,
and one new `skill-evaluation.py` subcommand.

Added: one durable receipt type and one run-report section.

## Data flow

```
retained profile-audit dispositions + candidate lifecycle records
  -> derive_evaluation_routing
  -> rows with route=candidate-evaluation and an exact evaluation_subject
  -> deterministic claim of one subject (digest and version re-verified)
  -> transition ready_for_draft -> evaluating (expected version and digest)
  -> materialize package from candidates/v1/packages/<lifecycle>/<candidate>
       and re-verify inventory against candidate_id
  -> shadow authoring packet (one candidate, fixed template, no portfolio state)
  -> evaluation-input-author model call -> five case prompts, or a refusal code
  -> shadow-compile   (sealed run manifest)
  -> shadow-execute   (trials through the attested adapter)
  -> shadow-certify   (pass | regression | inconclusive | stale)
  -> immutable shadow-evaluation receipt bound to candidate, occurrences,
     profiles, lifecycle version, and pass id
  -> transition evaluating -> ready_for_draft   (always, in the same pass)
  -> run report and dashboard projection
```

## Deterministic and model responsibilities

Deterministic code owns: readiness derivation and ordering, claim fencing,
package materialization and re-verification, the fixed suite template, executor
and routing derivation from adapter attestation, allowance accounting, receipt
identity, and every lifecycle transition.

The model owns exactly one thing: the `task_id` and `prompt` text of five
cases, over a template it cannot change.

## Claiming, bounded work, and the same-pass exit

**Claim.** The pass re-reads the lifecycle record, requires
`candidate_record_digest(record)` and `record_version` to equal the routed
subject, then transitions to `evaluating` with `--expected-version` and
`--expected-record-sha256`. A mismatch is `evaluation-claim-stale`: the
candidate is left untouched and the pass proceeds to the next stage.

**Exit is unconditional.** Every path after a successful claim ends with a
transition back to `ready_for_draft` before the stage returns, including
authoring refusal, compile refusal, execute failure, certify `regression`,
`inconclusive`, or `stale`, allowance exhaustion, adapter failure, and
unexpected exception. The exit is written in a `finally`-equivalent position so
that no early return can skip it.

**Halt and deadline.** If the halt file appears or the remaining pass time
falls below the stage's reserved settlement margin, the stage stops before
starting a new trial batch, retains a terminal `halted` or
`evaluation_elapsed_budget_exhausted` result, and exits `evaluating`.

**Crash recovery.** A candidate found in `evaluating` at the start of a pass,
with no current pass claim, is returned to `ready_for_draft` with a retained
`evaluation-abandoned` result before readiness is derived. This is the
belt-and-braces guarantee that no candidate is stuck across passes.

**Terminal results are retryable.** Every non-pass result is terminal for that
pass and retryable in a later pass: the candidate returns to
`ready_for_draft`, keeps its evidence, keeps its recurrence count, and stays
visible to the catalog-aware reviewer, so later semantic occurrences alias to
the same group instead of forking.

**Semantic grouping is never lost,** because the group is only ever in
`evaluating` inside a single pass, and `ELIGIBLE_CANDIDATE_GROUP_STATES`
already includes `ready_for_draft`.

## Work-conserving behavior

The stage does not reserve a batch. It repeats: derive readiness, claim the
next ready candidate, evaluate, exit, until either the evaluation allowance is
spent or no ready candidate remains. Eligibility derivation spends no
allowance; only a started authoring model call or a started trial batch does.

Unused allowance must be explained by exactly one retained stop reason:

- `eligible_input_exhausted` — a final readiness derivation returned no
  `candidate-evaluation` row, and the run report lists the near-miss rows with
  their gate reasons;
- `evaluation_allowance_spent`;
- `evaluation_elapsed_budget_exhausted`;
- `pass_deadline_margin`;
- `halted`;
- `executor_unavailable`.

A run with unused allowance and no stop reason fails accounting reconciliation.

## Failure model

| Failure | Result |
| --- | --- |
| No `skill-evaluation-executor` configured or its `doctor` is unhealthy | Skip the stage, retain stop reason `executor_unavailable`, change no lifecycle state |
| Lifecycle record changed between routing and claim | `evaluation-claim-stale`; no transition; try the next ready candidate |
| Package bytes missing, or inventory does not re-hash to `candidate_id` | Refuse `candidate-package-tampered`, retain a terminal result, exit `evaluating`, do not run the model |
| Authoring model returns `insufficient_information` | Retain the refusal code as a terminal result, exit `evaluating`, retry in a later pass |
| Authoring result malformed, duplicated, or template-drifted | Retain `authoring-result-invalid`, exit `evaluating`; the existing adapter validation is the gate |
| Designed suite fails `shadow_suite` validation | Retain `suite-invalid`, exit `evaluating` |
| `shadow-compile` refuses | Retain `compile-refused` with the evaluator message, exit `evaluating` |
| `shadow-execute` fails, times out, or an adapter attestation mismatches | Retain `execute-failed`, exit `evaluating`; no partial result is certified |
| `shadow-certify` returns `stale` | Retain `certificate-stale`; the candidate or executor drifted mid-run; exit and retry later |
| `shadow-certify` returns `regression` or `inconclusive` | Retain the certificate as terminal evidence, exit `evaluating`, grant no authority |
| `shadow-certify` returns `pass` | Retain the certificate, exit to `ready_for_draft`, grant no install or publication authority |
| Halt file appears mid-stage | Stop before the next trial batch, retain `halted`, exit `evaluating` |
| Pass deadline margin reached | Retain `evaluation_elapsed_budget_exhausted`, exit `evaluating` |
| Writer lease lost | Do not transition; mark the pass unhealthy; the crash-recovery sweep repairs state next pass |
| Candidate found in `evaluating` with no current claim | Return to `ready_for_draft` with `evaluation-abandoned` before readiness derivation |
| Receipt digest collision with differing content | Refuse `shadow-evaluation-receipt-collision` and mark the pass unhealthy |

## Hard invariants

1. A candidate is in `evaluating` only within one pass; no pass ends with a
   candidate in `evaluating`.
2. Every certificate names the exact `candidate_id` whose materialized bytes
   re-hash to it, and the exact canonical occurrence identities that authorized
   it.
3. A shadow evaluation grants no install, publication, portfolio, or admission
   authority, and adds no forward lifecycle edge.
4. The model designing cases sees exactly one candidate and no unrelated
   portfolio, transcript, credential, or dashboard state.
5. Evaluation never creates, consumes, or resets recurrence evidence; a
   returned candidate keeps its evidence, count, and group identity.
6. Every claimed candidate has exactly one retained terminal result per pass.
7. Every started authoring call and started trial batch spends allowance;
   readiness derivation never does.
8. Receipts are immutable and content-addressed; a differing body under the
   same digest is a refusal, never an overwrite.
9. Repair outcomes remain report-only and never enter this stage.
10. The pass keeps exactly one writer lease and one owner.

## Acceptance criteria

- **AC-E1:** With one recurrence-gated candidate and a healthy configured
  evaluator, one pass produces one certificate whose status is one of `pass`,
  `regression`, `inconclusive`, or `stale`, and the record ends in
  `ready_for_draft`.
- **AC-E2:** The certificate's candidate identity equals the lifecycle
  `current_candidate_id`, and the sealed run manifest's candidate inventory
  equals the lifecycle revision `files`.
- **AC-E3:** The retained receipt names the exact `profile_id` set, canonical
  occurrence identities, lifecycle record version, suite id, executor
  identities, and pass id.
- **AC-E4:** A `pass` certificate produces no install, publication, or forward
  lifecycle transition, and no estate mutation.
- **AC-E5:** Every failure row in the failure model leaves the record in
  `ready_for_draft` with one retained terminal result, and a later pass can
  claim the same candidate again.
- **AC-E6:** A candidate injected in `evaluating` with no current claim is
  returned to `ready_for_draft` before readiness derivation.
- **AC-E7:** After any terminal result, a fourth matching occurrence aliases to
  the same lifecycle group; no new group is created.
- **AC-E8:** The authoring packet contains exactly one candidate and no
  unrelated skill, transcript, credential, or dashboard content; a packet that
  does is refused before the model call.
- **AC-E9:** Unused evaluation allowance is always explained by exactly one
  retained stop reason, and accounting reconciles every claimed candidate.
- **AC-E10:** Replaying a pass over unchanged evidence produces the same
  receipt digest for the same candidate, suite, and executor identities.
- **AC-E11:** The pass completes inside the 3,600-second deadline with the
  reserved settlement margin intact.

## Check contract

| Check | Criterion | Setup | Pass evidence | Failure evidence |
| --- | --- | --- | --- | --- |
| CHK-01 | AC-E1, AC-E2 | One lifecycle record with three distinct current occurrences, shadow-ready recommendation, one staged package; one healthy fixture evaluator | One certificate retained; manifest `candidate_inventory` equals revision `files`; final state `ready_for_draft` | No certificate, or a certificate naming a different candidate id |
| CHK-02 | AC-E3 | Same as CHK-01 with two distinct authorizing profiles | Receipt lists both `profile_id`s, all three canonical occurrence ids, the claimed record version, suite id, executor identities, pass id | Any authority absent or not matching the lifecycle record |
| CHK-03 | AC-E4 | CHK-01 forced to `pass` | Skill roots byte-identical before and after; no publisher call; no transition other than in and out of `evaluating` | Any estate mutation, publication, or forward transition |
| CHK-04 | AC-E5 | One fixture per failure row: tampered package, authoring refusal, malformed authoring result, invalid suite, compile refusal, execute failure, certify `stale` | Each leaves `ready_for_draft`, retains its named terminal result, and a second pass re-claims the same candidate | Any row leaving `evaluating`, retaining no result, or blocking re-claim |
| CHK-05 | AC-E6 | Record pre-set to `evaluating` with no current claim | Stage returns it to `ready_for_draft` with `evaluation-abandoned` before readiness derivation | Candidate remains in `evaluating` or is claimed while stale |
| CHK-06 | AC-E7 | Run CHK-04's certify-`stale` fixture, then supply a fourth matching occurrence | Reviewer group context includes the same `lifecycle_id`; no new lifecycle record | A new lifecycle id appears for the same procedure |
| CHK-07 | AC-E8 | Packet built with an unrelated skill and a transcript fragment injected | Packet builder refuses before any model call; the sensitive-value rejector names the field | Any unrelated content reaching the model input |
| CHK-08 | AC-E9 | Two ready candidates with an allowance of one; then zero ready candidates | First run stops with `evaluation_allowance_spent`; second stops with `eligible_input_exhausted` and lists near-miss gate reasons; accounting reconciles | Unused allowance with no stop reason, or a claimed candidate with no accounting row |
| CHK-09 | AC-E11, and sets the unresolved allowances | One real configured evaluator, one candidate, natural pass | Retained wall-clock cost per authoring call and per trial; the deadline margin holds; the measured numbers become the configured allowances | Deadline overrun, or allowances configured without this measurement |
| CHK-10 | AC-E10 | Run CHK-01 twice over unchanged evidence with a deterministic fixture executor | Identical receipt digest | Differing digest without a changed input identity |

CHK-01 through CHK-08 and CHK-10 are deterministic and run with a fixture
evaluator adapter in the standalone suites. CHK-09 is the only check requiring
a real model and a natural installed pass.

## Migration and rollback

Migration is additive. No existing record changes shape. Absent the new
`evaluators` config section, the stage reports `executor_unavailable`, changes
no state, and the rest of the pass is unaffected, so an unconfigured host keeps
today's behavior exactly.

Rollback reinstalls the prior generation and disables the stage. It deletes no
task-profile receipt, disposition, lifecycle record, certificate, or shadow
evaluation receipt. Because a candidate is never left in `evaluating` across
passes, rollback never strands a lifecycle record. Restore re-enables the stage
only after the generation-bound self-test, and the first restored pass runs the
crash-recovery sweep before deriving readiness.

## Definition of Done: bounded shadow evaluation of a routed candidate

- [ ] The `skill-evaluation-executor` role is registered in the existing
      adapter registry and health-checked by the existing loop, with no second
      configuration file, owner, or lease.
- [ ] Executors and routing documents are derived from adapter attestation, and
      `shadow_attest` accepts them against a real `version` response.
- [ ] The existing `evaluation-input-author` boundary designs five case prompts
      for a shadow subject with its schema, prompt, task-id shape, refusal
      codes, and response validation unchanged.
- [ ] One scheduled pass claims one ready candidate derived from existing
      dispositions and lifecycle authority, with no new queue row.
- [ ] The materialized package re-hashes to the claimed `candidate_id` before
      any model call.
- [ ] `shadow-compile`, `shadow-execute`, and `shadow-certify` run unmodified
      and produce one certificate covering triggering, task performance, and
      overall regression.
- [ ] One immutable receipt binds the certificate to the exact candidate,
      canonical occurrences, profiles, lifecycle version, and pass.
- [ ] Every path exits `evaluating` in the same pass, and a crash-recovery
      sweep repairs any candidate found in `evaluating` without a claim.
- [ ] A fourth matching occurrence after any terminal result aliases to the
      same group.
- [ ] Unused evaluation allowance is always explained by one retained stop
      reason, and accounting reconciles every claimed candidate.
- [ ] A passing certificate grants no install, publication, or admission
      authority.
- [ ] CHK-01 through CHK-08 and CHK-10 pass deterministically; CHK-09 records
      the measured cost that sets the configured allowances.
- [ ] The plan baton, this work order, and the local commits are complete, and
      the slice has a reviewed PR targeting `feature/multi-cli-dreaming`.

## Changing reframe status from OPEN to CLEAR

Reframe status becomes CLEAR when a design review records all of:

1. **Owner approval of the extension framing** — that registering a fourth
   adapter role, adding a shadow subject mode to the existing authoring
   boundary, and adding one durable receipt are extensions of existing
   authorities rather than the forbidden second queue, owner, scheduler,
   transport, catalog stage, or classifier.
2. **A dry-run attestation trace** showing the configured
   `skill-evaluation-executor` adapter's `version` response satisfying
   `shadow_attest` against the derived executors document, proving E1 needs no
   invented identity.
3. **One validated shadow suite** produced from the fixed template plus five
   model-authored prompts, accepted by `shadow_suite` in `candidate_only`
   routing mode with no `--catalog-dir`, proving E2 needs no catalog stage and
   no second authoring stack.
4. **A resolved allowance decision** — either CHK-09's measured per-call and
   per-trial cost, or an explicit owner-set provisional bound with the
   measurement scheduled, replacing the "unresolved policy decision" row in the
   constraint table.
5. **Confirmation that no revisit condition in the constraint table has
   fired**, in particular the pass-deadline row, given the added stage.

Until all five are recorded, implementation returns to this document rather
than starting the stage.
