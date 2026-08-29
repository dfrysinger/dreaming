# Bounded shadow evaluation of a routed candidate

## Objective

Let one scheduled owner pass claim one recurrence-gated shadow candidate under
an append-only open-attempt record, have the existing candidate-blind authoring
boundary design its observed-task cases from one closed packet, run the
existing `shadow-compile` / `shadow-execute` / `shadow-certify` flow in
`catalog_plus_candidate` routing mode against a one-skill approved target
catalog derived from the existing estate census, inside a pre-reserved and
mechanically enforced worst-case time bound, retain a semantic result bound to
the exact candidate, catalog, and occurrence authorities, and always leave
`evaluating` in the same pass while the owner holds its lease.

The stage must be able to reach a truthful `pass` on triggering, task
performance, and overall regression, because that is the parent funnel's
Definition of Done. It grants no install or publication authority regardless of
the outcome.

This design follows the principles in
`skills/skill-review/SKILL.md#dreaming-design-principles` and continues the
funnel specified in `docs/task-opportunity-evidence-design.md`.

Revision 3 resolves round-one findings T1-T3 and O4-O6 and round-two findings
R1-R3 and N1-N2. Revision 4 resolves the round-three preparation-bound finding
by moving all deterministic preparation ahead of the lifecycle claim and
extending the admission formula to cover it. Revision 5 resolves round-three
findings M1 and M2: the author call is bounded by the same stateless wrapper as
the other pinned tools and carries the vendor argument the adapter requires,
and the routing mode changes from `candidate_only` to `catalog_plus_candidate`
because the harness makes a `candidate_only` `pass` unreachable by
construction. Revision 6 corrects one static count: the claimed stage writes
four durable objects, not three, so `max_records_per_candidate` and every
statement derived from it now say four. Revision 7 closes the design phase: it
separates static design clearance from runtime proof, records both reviewers'
final static clearance, and moves reframe status to CLEAR. T1, T3, R1-R3,
N1-N2, M1, M2, and O4-O6 are carried forward unchanged.

## Lane

**Systemic.** This change gives the scheduled owner a third bounded model-and-
execution stage, gives the estate census a second read-only consumer as the
approved target catalog authority, adds a fourth adapter role to the existing
registry, extends
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
- Do not add a second catalog stage, a catalog audit, or a deterministic
  semantic classifier. The approved target catalog is materialized from the
  estate census the pass already builds, and the conflict target is selected
  from an owner-recomputed behavioral trace, not by similarity scoring.
- Do not narrow the parent Definition of Done. A design that cannot reach a
  truthful `pass` on triggering, task performance, and overall regression is
  not acceptable, and neither is a weakened `pass`, a manufactured fixture
  result, or an inconclusive diagnostic relabelled as a pass.
- Do not treat the certificate's `authoritative` bit as install, publication,
  or admission authority. In `catalog_plus_candidate` mode that bit can be
  true; the prohibition is enforced independently by lifecycle and stage
  policy.
- Do not evaluate more than one candidate per pass beyond the configured
  allowance, and do not evaluate the same subject twice in one pass.
- Do not grant install, publication, portfolio, or admission authority from a
  passing evaluation. `evaluating` has no forward edge to `portfolio_pending`
  in `DECLARED_TRANSITIONS` and this design does not add one.
- Do not invent a repair candidate. `missed-skill` and
  `wrong-or-incomplete-skill` stay report-only until immutable candidate
  evidence exists for them, which is a separate work order.
- Do not let the model see anything outside one closed packet.
- Do not call `execute_evaluation_input_owner` or `v2-input-owner-run`, and do
  not register the authoring adapter in the adapter registry; it is
  content-pinned, not configured.
- Do not add a key to the sealed `evaluation_input_owner` block.
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
| The stage refuses to start unless remaining pass time covers a worst-case bound whose every term is mechanically enforced, including preparation, revalidation, authoring, attestation, trials, compile, certify with its nested verification subprocess, settlement, and termination overhead | Round-one finding T2 and round-two finding R2; existing bounded process-group calls in `dreaming-core.py:388-413` and `skill-evaluation-harness.py:580-650` | The 3,600-second pass deadline holds without mid-run interruption and without an unenforced estimate | Measured cost shows the bound is unusably pessimistic and a supervised path is justified |
| The stage compiles in `catalog_plus_candidate` routing mode | Round-three finding M2, verified: `shadow_aggregate` hard-codes `"inconclusive" if manifest["routing_mode"] == "candidate_only"` into the routing gate (`skill-evaluation-harness.py:2137-2139`), so a `candidate_only` run can never aggregate to `pass` | The parent Definition of Done — passing triggering, task-performance, and overall-regression evaluation — becomes reachable | The harness stops treating `candidate_only` as inherently inconclusive |
| The approved target catalog is one skill, materialized from the existing content-addressed estate census | Round-three finding M2; `census.physical_instances` already carry `skill_name`, `absolute_path`, `inventory_sha256`, and a full per-file inventory (`dreaming-estate.py:1246-1266`) inside a snapshot digest (`:1739`), and the pass already builds it | Reuse of an owner-derived current authority; and every non-conflict routing case must load *zero* catalog skills (`skill-evaluation-harness.py:1879-1882`), so a minimal catalog is the only one that is not gratuitously flaky | A larger catalog is shown necessary, or the census stops being current at stage time |
| The conflict target is the first catalog skill in the owner-recomputed `skill_load_trace` of the authorizing occurrences | Round-three finding M2; `no-covering-skill` audits carry `catalog_skill_name: None` (`dreaming-core.py:1416-1418`), so no named target exists, and the trace is recomputed by the owner and required to match (`dreaming-core.py:1156-1214`, `:1333-1338`) | A deterministic, behavioral, owner-derived choice that is not a semantic classifier | Selection is shown to require similarity, keyword, or embedding scoring, which would be the forbidden classifier |
| The packet discloses exactly one catalog skill's name and SKILL.md description, and nothing else about the catalog | Round-three finding M2; `shadow_suite` requires the `routing_conflict` case to name exactly one catalog skill in catalog mode (`skill-evaluation.py:10665-10670`), and the route proof demands the executor load precisely it (`skill-evaluation-harness.py:1879-1882`) | Minimum disclosure that still lets the author write a conflict prompt that can truthfully pass | A conflict case is shown authorable with no knowledge of the incumbent |
| A passing evaluation grants no install or publication authority even when the certificate reports `authoritative: true` | Round-three finding M2 and the parent report-only constraint; the bit is adapter-attested, since `real_backend` is self-declared and `real_backend_source` is free text (`skill-evaluation.py:10767-10770`) | Report-only survives the routing-mode change without depending on a certificate field | The lifecycle gains a reviewed forward edge, which is a separate work order |
| All deterministic preparation — package materialization, inventory re-hashing, packet construction, packet validation — runs before the lifecycle claim, and the pre-claim admission covers preparation and the claimed stage together | Round-three finding: the previous ordering left post-claim deterministic work outside the reservation | Nothing unbounded or unaccounted survives inside `evaluating`, and a preparation overrun costs pass time only | A preparation step is shown to require the claim to have already happened, in which case it needs an explicit enforced `preparation_bound` inside the claimed stage |
| Pre-claim refusals write no attempt record, change no lifecycle state, and are retained as run-report and routing evidence only | Round-three finding, plus the append-only regime of R3: an unclaimed subject has no attempt to settle | The one-to-zero-or-one open/terminal relation stays exact and no record type is invented | Pre-claim refusal evidence is shown to need durability beyond deterministic reproduction from the same bytes |
| Preparation writes only to a pass-local scratch root outside every durable record root, and is never read across passes | Round-three finding; required so a fenced-out pass still writes zero durable bytes | Scratch cannot become recovery authority or contaminate the R1 zero-write rule | Any later pass needs to read a predecessor's scratch |
| Every subprocess the stage starts runs in its own process group under an enforced timeout, with terminate-then-kill cleanup and a named terminal refusal on breach | Round-two finding R2; `ExecutableAdapter._invoke` (`dreaming-core.py:388-413`) and harness `call` / `terminate` (`skill-evaluation-harness.py:580-650`) already implement this pattern | No term of the reservation can be exceeded by an unbounded child | A tool is shown to be uninterruptible without corrupting durable state |
| The owner pass keeps its existing 3,600-second hard deadline | Installed `daemon-pass.sh:33` backstop inside the four-hour cadence, as recorded in `docs/task-opportunity-evidence-design.md` | Profiling, review, evaluation-input, and evaluation all settle without scheduler overlap | The four-hour cadence changes, or measured stage cost no longer fits |
| Routing `evaluation_execution.available` is computed from live authority, never from a hard-coded constant, and fails closed | Round-one finding O4; current hard-coded `SHADOW_EXECUTION_BLOCKERS` at `profile_evaluation_routing.py:39-42` | The projection never claims an evaluation the stage cannot run, and never hides one it can | Never; a constant reintroduces the divergence this row exists to prevent |
| Semantic result identity excludes pass, lease, and timing fields; the terminal-attempt record carries them | Round-one finding O5 | Replay determinism and audit completeness are both satisfiable | A field is shown to be simultaneously semantic and per-attempt |
| Semantic result identity excludes the certificate receipt digest and every path-bearing certificate field, binding `status`, sealed `run_id`, harness `result_id`, and `authoritative` instead | Round-two finding N1; `shadow_certify` writes an absolute `result_dir` into the receipt (`skill-evaluation.py:11028`) and digests the whole receipt (`:11039`) | Replay determinism survives a changed run root | The evaluator stops embedding paths in the receipt, making the receipt digest genuinely semantic |
| Shadow authoring reuses the content-pinned authoring adapter and the sealed `evaluation_input_owner.author_model`, adding no authoring config key and no adapter registry entry | Round-two finding N2; `trusted_authoring_adapter_path` (`skill-evaluation.py:432-443`) and the strict key-set validator at `dreaming-core.py:5588-5614` | One authoring identity authority; no breaking config change on configured hosts | The owner block must express a shadow-specific model distinct from `author_model` |
| No subject is claimed twice in one pass; a persistent failure never starves other ready candidates | Round-one finding O6 | Work conservation and fair progress across candidates | Only one candidate is ever ready, making ordering moot |
| Deterministic trial count per candidate is `executors x (cases + task_value_cases) + executors` | Derived from `skill-evaluation-harness.py:2344-2352` (control plus candidate treatment for `task_value`) and `:2330-2342` (one `version` call per executor) | Cost is predictable before the stage starts | The harness changes its treatment or attestation plan |
| Minimum conforming suite is five cases, one per required routing class | Derived from `skill-evaluation.py:10706-10711`, which requires exactly the set `routing_positive`, `routing_close_negative`, `routing_unrelated`, `routing_conflict`, `task_value` | The three gates are all observable | The evaluator changes its required class set |
| Numeric values of `max_evaluations_per_run`, the evaluation stage second budget, per-call `timeout_seconds` / `token_budget` / `turn_budget` / `tool_budget` / `output_bytes`, every reservation term (`author_call_bound`, `author_doctor_bound`, `compile_bound`, `certify_bound`, `settlement_bound`, `deadline_margin`), and every preparation term (`package_file_ceiling`, `package_bytes_ceiling`, `catalog_file_ceiling`, `catalog_bytes_ceiling`, `prepare_throughput`, `hash_throughput`, `packet_build_bound`, `packet_validate_bound`, `lifecycle_read_bound`) | **Unresolved policy decision.** No measured shadow-trial cost exists in this repository; the only shadow runs are deterministic fixtures in `test-shadow-mutation-boundary.sh` | Bounded model cost and a deadline-safe pass | Set them from CHK-09's measured single-candidate run, or record an owner-set provisional bound with the measurement scheduled; do not hard-code a number before one of those. Enforcement of each term is settled here; only the numbers are open |

**Reframe status: CLEAR** as of revision 7, on the static paired-review
evidence recorded in "Design clearance and development-loop proof gates" below.
Clearance authorizes writing the implementation. It does not authorize landing
it: the runtime proof gates named in that section remain mandatory before the
pull request, before landing, and before this work order's Definition of Done
can be checked.

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
   and no in-place rewrite. Its only new mechanisms are two small bounding
   helpers: one bounded-subprocess wrapper that copies a pattern the codebase
   already implements twice, and one bounded deterministic helper applying a
   declared size ceiling and a between-item deadline to the pre-claim
   preparation steps. Neither observes halt, interrupts work, nor holds state,
   so neither is a supervisor. A
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
| `conflict_reference` | Exactly `{name, description}` of the single approved-target-catalog skill, where `description` is that skill's SKILL.md front-matter description | Materialized one-skill catalog snapshot only |
| `compilation_contract.case_runtime` | Per-case runtime block consumed by the existing response validator | Builder constant |
| `executor_contract` | Executor identity digest, declared `model`, and `limits` | Derived executor document |
| `harness_digest` | `sha256` of the harness executable | Harness file |
| `routing_mode` | `catalog_plus_candidate` | Builder constant |

**Permitted source roots** are exactly four: the materialized candidate package
directory, builder constants, the derived executor identity document, and the
materialized one-skill catalog snapshot of E2c — and from that snapshot only
the skill's directory name and its SKILL.md front-matter description. No other
filesystem root, database, or environment value may contribute a byte.

**Prohibited fields and sources**, refused explicitly rather than merely
omitted: estate census rows, any other skill root, catalog inventory,
transcripts, session records, task-profile receipts, audit dispositions,
reviewer claims, dashboards, home state, credentials, environment variables,
catalog file inventories, catalog file bodies, any second catalog skill, census
rows, capability identities,
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

**Retained evidence.** The packet is built and validated before the claim, in
the pass-local scratch root of E5. Its exact bytes become immutable retained
evidence beside the result under `packet_id` only at settlement, so a subject
refused before the claim retains no packet, and a reviewer of a real attempt
can re-run the validator on precisely what the model received.

*What is deliberately reused unchanged:* the model contract. The model still
returns only `{outcome, summary, cases:[{id, task_id, prompt}]}` under
`evaluation_input_author_schema` (`dreaming-vendor-adapter.py:2986-3018`) with
the existing prompt (`:3021-3060`), the existing task-id shape
`AUTHOR_TASK_ID_RE` (`:235`), the existing refusal codes `AUTHOR_REASON_CODES`
(`:236-240`), and the existing response validation (`:3196-3270`) that pins
`id` and `class` to the template and forbids duplicate prompts.

*Why `catalog_plus_candidate` routing mode:* round-three finding M2 is correct
and verified. `shadow_aggregate` computes the routing gate as `"inconclusive"
if manifest["routing_mode"] == "candidate_only"` before any other test
(`skill-evaluation-harness.py:2137-2139`), so a `candidate_only` run aggregates
to `inconclusive` or `regression` and **can never reach `pass`**. Since the
parent funnel's Definition of Done requires passing triggering,
task-performance, and overall-regression evaluation, `candidate_only` cannot
satisfy it, and the earlier revisions of this document were wrong to select it.
The mode change also makes the `routing_conflict` case meaningful: in catalog
mode `shadow_suite` requires exactly one declared `catalog_loads` entry
(`skill-evaluation.py:10664-10670`) and the route proof requires the executor
to load precisely that skill and not the candidate
(`skill-evaluation-harness.py:1858-1882`).

### E2c. The approved target catalog, reused from the estate census

`catalog_plus_candidate` needs an approved target catalog. This design supplies
one without adding a catalog stage, a catalog audit, a queue, an owner, or a
classifier, because every input already exists and is already owner-derived.

**The authority.** The pass already builds the estate census. Each entry of
`census.physical_instances` is a real installed skill directory carrying
`skill_name`, `absolute_path`, `relative_path`, `root_id`, `root_class`,
`canonical_capability_id`, `inventory_sha256`, and a full per-file inventory
with per-file digests (`dreaming-estate.py:1246-1266`), inside a snapshot that
is itself content-addressed (`:1739`). The scheduled owner already consumes
exactly this list elsewhere (`dreaming-core.py:9308-9314`). Nothing new is
derived; one already-derived record is read.

**Why this is reuse, not a new stage.** A catalog *stage* would decide what the
portfolio should contain. This reads what the portfolio already contains, for
one named skill, and copies bytes. It renders no judgement, writes no
disposition, admits nothing, and produces no durable artifact beyond the
pass-local scratch snapshot of E5.

**Selecting the one conflict target, deterministically and without a
classifier.** A `no-covering-skill` audit carries `catalog_skill_name: None`
(`dreaming-core.py:1416-1418`), so the disposition names no incumbent. The
selection instead uses `skill_load_trace`, which the owner **recomputes** from
the content-addressed transcript snapshot and the profile's source event ids
and requires the reviewer result to match exactly (`dreaming-core.py:1156-1214`
and `:1333-1338`). It is therefore owner-derived immutable evidence, not a raw
reviewer claim, which is what the parent constraint demands.

The rule: order the authorizing occurrences canonically, concatenate their
traces in that order, and take the first entry whose `catalog_skill_name` is
not null and which resolves to exactly one census instance. This is a
behavioral fact — a skill that actually loaded while the observed task was
performed and still failed to cover it — not a similarity, keyword, or
embedding judgement, so it is not the forbidden semantic classifier.

**Materialization and binding.** The selected instance's declared `files` are
copied into `scratch/catalog/<skill_name>/`, and the copy is re-hashed to the
census `inventory_sha256` before use. `shadow_catalog` independently refuses a
symlinked directory or any entry lacking `SKILL.md`
(`skill-evaluation.py:10552-10559`), and `shadow_copy_tree` refuses symlinks
(`:10580-10581`). The retained result binds `catalog_id`, the census
`snapshot_sha256`, the instance `canonical_capability_id`, and the instance
`inventory_sha256`, so the exact catalog is reconstructible.

**Why exactly one skill.** For every non-`task_value` case the route proof
requires the loaded catalog set to equal the declared set exactly
(`skill-evaluation-harness.py:1879-1882`). Four of the five cases declare no
catalog load, so any incidental load of any other catalog skill is a
`regression`. A one-skill catalog is the smallest snapshot that supports the
required `routing_conflict` case while keeping the other four provable, and it
also minimizes what the packet must disclose.

**Stable refusals**, all pre-claim and report-only:
`shadow-conflict-target-unavailable` (no trace entry names a catalog skill),
`shadow-conflict-target-ambiguous` (the name resolves to zero or several census
instances), `shadow-catalog-snapshot-stale` (the census digest changed since
preparation, or the copied bytes do not re-hash to `inventory_sha256`).

### E2b. The exact packet-only authoring invocation

Round-two finding N2 requires the integration surface to be named rather than
implied. The stage does **not** call `execute_evaluation_input_owner`, does not
reuse `v2-input-owner-run`, and adds no second owner.

**Invocation.** The stage calls the authoring adapter directly, through the
bounded wrapper of E5:

```
<authoring_adapter> --vendor copilot \
  --role evaluation-input-author --model <author_model> \
  run --operation shadow-author \
      --packet <packet_path> --draft-output <draft_path>
```

`--vendor` is required by the adapter parser
(`dreaming-vendor-adapter.py:6650`), and the vendor value is not free: the
existing role doctor reports `healthy: true` only when the vendor is `copilot`
(`dreaming-vendor-adapter.py:3734-3748`), so the health check pins the vendor
this stage may use. Any other vendor fails the availability derivation below.

`--packet` is the only input path. The installed-capability operations require
`--skill-dir --suite --policy --config --routing --harness --catalog`
(`dreaming-vendor-adapter.py:3272-3300`); the `shadow-author` branch requires
none of them, which is what makes the packet the model's sole input channel.

**Resolving the adapter object.** There is no registry entry to resolve and
none is added. The authoring adapter is already content-pinned:
`trusted_authoring_adapter_path()` (`skill-evaluation.py:432-443`) resolves
`dreaming-vendor-adapter.py` as a sibling of the evaluator, refuses symlinks,
and refuses any file whose bytes differ from `TRUSTED_AUTHORING_ADAPTER_SHA256`.
The stage reuses that resolver. Re-pinning that constant when the adapter
changes is an existing release step and remains one.

**Resolving the exact author model identity.** The model comes from the
existing sealed `evaluation_input_owner` configuration block
(`EVALUATION_INPUT_OWNER_KEYS`, `dreaming-core.py:161-167`), validated at
`:5588-5614`, which already requires exactly five keys, three distinct
non-empty model identities, and `DREAMING_ADAPTER_CONFIG_MANAGED=1`. The stage
reads `author_model` verbatim.

*Why no new authoring config surface is required, and why one is not added:*
that validator compares the key set with strict equality
(`set(entry) != EVALUATION_INPUT_OWNER_KEYS`), so adding a shadow-specific key
would be a breaking configuration change on every configured host, for no gain.
Reusing `author_model` also inherits the existing distinctness and
managed-config guarantees for free.

*Coupling decision:* shadow authoring requires the block to be present, valid,
**and** `enabled: true`. One operator switch therefore governs all model
authoring on the host. Disabling the block disables shadow authoring too, which
is the behavior an operator flipping that switch expects; the alternative,
reading `author_model` from a disabled block, would let a stage keep spending
model capacity after an operator turned authoring off.

**Health.** Before deriving availability, the stage runs the existing role
doctor `evaluation_input_author_doctor` (`dreaming-vendor-adapter.py:3734`) as
`--role evaluation-input-author --model <author_model> doctor` through the
bounded wrapper and requires `healthy: true`. This is a direct bounded call,
not a `configured_adapters` registration, because the adapter is content-pinned
rather than configured.

**Extended surfaces.** Exactly four small extensions carry this:

| Surface | Location | Change |
| --- | --- | --- |
| Operation choices | `dreaming-vendor-adapter.py:6689` | Add `shadow-author` to `--operation` |
| Operation-to-packet-kind map | `dreaming-vendor-adapter.py:3490` | Add `shadow-author -> safe_shadow_evaluation_authoring_packet` |
| Required source paths | `evaluation_input_source_paths`, `dreaming-vendor-adapter.py:3272-3300` | Add a `shadow-author` branch requiring only `--packet` |
| Packet validation route | `validate_evaluation_input_packet`, `dreaming-vendor-adapter.py:3303-3335` | Route `shadow-author` to `skill-evaluation.py shadow-author-packet --validate` instead of `v2-input-author-packet` |

**Availability derivation.** `shadow-authoring-authority-unavailable` is
computed, fail-closed, when any of the following is not explicitly true: the
`evaluation_input_owner` block is present and valid; `enabled` is true;
`author_model` is a non-empty identity; the content-pinned authoring adapter
resolves and matches its reviewed digest; the role doctor reports healthy; and
`skill-evaluation.py` exposes `shadow-author-packet`. A missing or malformed
authority object yields `shadow-execution-authority-unknown`, never a silent
pass.

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
harness digest), shadow authoring authority available exactly as enumerated in
E2b, catalog authority available (a current readable census whose
`snapshot_sha256` verifies), the candidate package materializable, and every
allowance and reservation term explicitly configured by the owner. That last
condition is why no number is defaulted: an unconfigured bound is an absent
authority, not a value to guess.

**Fail-closed.** Missing, malformed, or partially populated authority yields
`available: false` with reason `shadow-execution-authority-unknown`. Availability
is true only when every named condition is explicitly true. Computed reason
codes are stable: `shadow-executor-unconfigured`, `shadow-executor-unhealthy`,
`shadow-executor-unattested`, `shadow-suite-authority-unavailable`,
`shadow-authoring-authority-unavailable`, `shadow-catalog-authority-unavailable`,
`shadow-candidate-package-unavailable`, `shadow-allowance-unconfigured`,
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
writer lease and the existing 3,600-second deadline.

**All deterministic preparation happens before the claim.** Round-three review
found the previous ordering defective: package materialization, inventory
re-hashing, packet construction, and packet validation ran *after* the
lifecycle claim but were absent from the reservation, so a claimed candidate
could consume unbounded, unaccounted pass time. Rather than bound them in
place, the stage moves them ahead of the claim, where an overrun costs only
pass time and leaves no claimed subject, no lifecycle transition, and no
record to recover.

The per-candidate order is therefore: **admit, prepare, revalidate, claim,
author, attest, execute, compile, certify, retain, exit** — where `admit`,
`prepare`, and `revalidate` are pre-claim, and everything from `claim` onward
is the bounded claimed stage.

- **Admit** — check that the remaining pass time covers the *whole* per-candidate
  worst case, preparation included, before touching a single byte.
- **Prepare** — materialize the candidate revision's bytes into a pass-local
  scratch directory, re-hash the materialized inventory to `candidate_id`,
  build the E2 packet, and run the packet validator. This produces a prepared
  identity `prepare_id = sha256(canonical({lifecycle_id, candidate_id,
  record_version, record_sha256, package_digest, packet_id,
  executors_document_digest}))`, held in memory for the rest of the candidate's
  turn. It is not a record: every field in it that matters durably already
  appears in the open-attempt record (`record_version`, `record_sha256`) or the
  terminal-attempt record (`packet_id`).
- **Revalidate** — immediately before the claim, re-read the lifecycle record
  and assert its `version` and `record_sha256` are byte-identical to the values
  observed at preparation, re-hash the prepared scratch tree to
  `package_digest`, and re-validate the retained packet bytes to `packet_id`.
  Any mismatch refuses before the claim.

After the claim only bounded model, harness, evaluator, and settlement work
remains. Nothing deterministic and unbounded survives inside `evaluating`.

**Pre-claim refusals write no record and change no state.** A subject refused
at admission, preparation, or revalidation was never claimed, so there is no
attempt to settle and the append-only one-to-zero-or-one relation is preserved
exactly. The refusal is retained as run-report and routing-projection evidence
with its stable code, the subject joins the pass-local attempted set so it
cannot be retried within the pass, and the stage continues to the next ready
candidate. Tamper evidence is not lost: the refusal is deterministic, so the
next pass reproduces it identically from the same bytes.

**Scratch is not durable authority.** Preparation writes only under
`DREAMING_STATE_DIR/shadow-evaluations/scratch/<pass_id>/<prepare_id>/`, never
under the durable record roots. It is removed on refusal and after settlement,
and the recovery sweep deletes any scratch root whose pass id is not the
current pass. No pass ever reads another pass's scratch, and no scratch byte is
ever cited as evidence, which is why leaving one behind does not violate the
zero-durable-write rule of a fenced-out pass.

**Selected enforcement architecture: full-invocation reservation.**
`shadow-execute` is a synchronous call that runs every trial to completion
(`skill-evaluation-harness.py:2330-2352`), so halt and deadline cannot be
observed inside it without a supervisor process the constraints forbid. Instead
the stage refuses to start unless the remaining pass time covers the entire
worst case:

```
authority_bound  = executors x executor_call_bound          # once per pass
                 + author_doctor_bound

materialize_bound = package_bytes_ceiling / prepare_throughput
rehash_bound      = package_bytes_ceiling / hash_throughput
catalog_bound     = catalog_bytes_ceiling / prepare_throughput
                  + catalog_bytes_ceiling / hash_throughput
prepare_bound     = materialize_bound + rehash_bound + catalog_bound
                  + packet_build_bound + packet_validate_bound
revalidate_bound  = lifecycle_read_bound + rehash_bound + packet_validate_bound

author_bound     = author_call_bound
attest_bound     = executors x executor_call_bound
trial_count      = executors x (cases + task_value_cases)
trial_bound      = trial_count x executor_call_bound
settlement_bound = lifecycle_transition_bound x max_transitions_per_candidate
                 + record_write_bound x max_records_per_candidate
termination_overhead = bounded_subprocess_count x termination_grace

claimed_bound = revalidate_bound + author_bound + attest_bound + trial_bound
              + compile_bound + certify_bound
              + settlement_bound + termination_overhead

worst_case = prepare_bound + claimed_bound

pass admission:      remaining_pass_seconds >= authority_bound + worst_case
                                              + deadline_margin
candidate admission: remaining_pass_seconds >= worst_case + deadline_margin
claim admission:     remaining_pass_seconds >= claimed_bound + deadline_margin
```

`executors`, `cases`, `task_value_cases`, `max_transitions_per_candidate`
(three: claim, exit, and one recovery allowance), `max_records_per_candidate`
(**four**: the open-attempt record, the retained packet, the semantic result,
and the terminal-attempt record — every durable object the claimed stage
writes, enumerated from the sequence above), `package_file_ceiling`,
`package_bytes_ceiling`, `catalog_file_ceiling`, `catalog_bytes_ceiling`, and
`bounded_subprocess_count` (`1 author + executors
attest + trial_count trials + 1 compile + 1 certify +
max_transitions_per_candidate lifecycle calls`; preparation and revalidation
start no subprocess) are all known before any byte is touched, so every
admission test is finite and computable in advance.

The three admissions are nested, not independent. Candidate admission is the
one that gates work: it is taken before preparation and covers preparation and
the claimed stage together. Claim admission is a defensive re-assertion that
must hold by construction, because preparation consumed at most
`prepare_bound`; if it ever fails, the stage claims nothing, retains
`pass_deadline_reservation_unmet`, and marks the pass unhealthy, because a
bound was under-declared. `package_bytes_ceiling` and `package_file_ceiling`
are what make `materialize_bound` and `rehash_bound` finite: a revision whose
declared inventory exceeds either ceiling is refused as `preparation-oversize`
before a single byte is copied.

**Every term is mechanically enforced.** Round-two finding R2 requires an
enforcement mechanism per term, not an estimate. Two bounded process-group
callers already exist and are reused; one minimal wrapper is added for the
tools core invokes directly.

| Term | Enforcement mechanism | Terminal refusal on breach |
| --- | --- | --- |
| `authority_bound` | Existing harness `call` for each executor `version`, and the bounded wrapper for the author `doctor` call | `shadow-executor-unattested` / `shadow-authoring-authority-unavailable` |
| `materialize_bound`, `rehash_bound` | **Bounded deterministic helper.** The revision inventory declares file count and byte total, so the helper refuses `preparation-oversize` before copying when either exceeds its ceiling, and checks a monotonic deadline between per-file operations over that finite inventory. No supervisor and no subprocess is involved, and unlike a synchronous adapter call the work is decomposable, so a between-item deadline is genuinely enforceable | `preparation-oversize`, `preparation-deadline-exceeded` |
| `packet_build_bound`, `packet_validate_bound` | Same bounded deterministic helper over the same declared inventory; the packet's only variable-size member is the per-file `candidate_contract` inventory already bounded by `package_file_ceiling` | `preparation-deadline-exceeded` |
| `revalidate_bound` | Same helper for the lifecycle re-read, the scratch re-hash, and the packet re-validation, each over already-bounded inputs | `revalidation-deadline-exceeded` |
| `author_bound`, `author_doctor_bound` | **The same stateless bounded wrapper**, not `ExecutableAdapter._invoke`. Round-three finding M1 is correct: E2b deliberately resolves a content-pinned, *unregistered* adapter, so there is no registry entry, no `ExecutableAdapter`, and no configured `run_timeout` to enforce. The wrapper copies the `_invoke` pattern (`dreaming-core.py:388-413`) — own process group, `communicate(timeout=...)`, terminate-then-kill — driven by the named policy value `author_call_bound` | `author-call-timeout` |
| `attest_bound` | Existing harness `call` (`skill-evaluation-harness.py:597-650`): monotonic deadline, output limit, `terminate()` at `:580-593`. The bound is `executor.limits.timeout_seconds` per `version` call | `execute-timeout` |
| `trial_bound` | Same harness `call` per trial, one trial per `(executor, case, treatment)` as enumerated at `skill-evaluation-harness.py:2344-2352` | `execute-timeout` |
| `compile_bound` | **Minimal bounded wrapper.** Core invokes `skill-evaluation.py shadow-compile` through one new helper that copies the `_invoke` pattern exactly: own process group, `communicate(timeout=compile_bound)`, terminate-then-kill | `compile-timeout` |
| `certify_bound` | Same wrapper around `skill-evaluation.py shadow-certify`. Because certify shells to the harness `shadow-verify` subprocess, the wrapper must kill the **process group**, not the direct child; `subprocess.run(timeout=...)` alone is insufficient and is explicitly rejected here | `certify-timeout` |
| `settlement_bound` | Same wrapper around each `candidate-lifecycle.py transition` call, plus the bounded deterministic helper for each of the at most `max_records_per_candidate` local record writes. That count is **four**, one per durable object the claimed stage writes: the open-attempt record, the retained packet, the semantic result, and the terminal-attempt record. Each is a single canonical-JSON write of already-bounded content, so `record_write_bound` applies uniformly | `settlement-timeout` |
| `termination_overhead` | `termination_grace` is the worst-case cleanup per bounded subprocess: at most SIGTERM plus grace plus SIGKILL. The two existing callers already bound this at 5 s (`dreaming-core.py:406`) and 2 s per signal (`skill-evaluation-harness.py:589`); the new wrapper uses the same shape | Counted, not refused |

The wrapper is one function. It is not a supervisor: it does not observe halt,
does not interrupt work mid-call, and has no state. It exists solely so that no
term of the reservation can be exceeded by an unbounded child.

Every breach is a terminal result for the candidate and takes the normal owned
exit: the record returns to `ready_for_draft`, a terminal-attempt record is
appended with the named refusal, and the subject is retryable in a later pass.

`author_call_bound`, `author_doctor_bound`, `executor_call_bound`,
`compile_bound`, `certify_bound`, `lifecycle_transition_bound`,
`record_write_bound`, `lifecycle_read_bound`, `packet_build_bound`,
`packet_validate_bound`, `package_file_ceiling`, `package_bytes_ceiling`,
`prepare_throughput`, `hash_throughput`, `catalog_file_ceiling`,
`catalog_bytes_ceiling`, `termination_grace`, and
`deadline_margin` are named policy values set by CHK-09 or by an owner-set
provisional bound. No number is invented here; only the enforcement is settled.

Halt and deadline are checked **between** bounded operations, at the same
granularity the existing owners already use (`dreaming-core.py:8917`, `:9189`,
`:9260`, `:9291`, `:9323`): before admission, before preparation, between
preparation steps, before revalidation, before claim, before the authoring
call, before execute, before compile, and before certify. Once `shadow-execute`
starts, it runs to completion, and the reservation is what guarantees it cannot
overrun the pass deadline. This is a deliberate property, not an accident.

If any admission test fails, the stage retains stop reason
`pass_deadline_reservation_unmet`, prepares nothing further, and claims
nothing.

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
  packet_id, suite_id, environment_id, routing_id, harness_digest,
  executor_identity_digests (sorted),
  certificate_status, certificate_run_id, certificate_result_id,
  certificate_authoritative,
  authorizing_occurrence_ids (sorted), profile_ids (sorted) }))
```

**The certificate receipt digest is deliberately not an input.** Round-two
finding N1 is correct: `shadow_certify` writes `result_dir` as an absolute
filesystem path into the receipt (`skill-evaluation.py:11028`) and then
computes `receipt_id` over the whole receipt (`:11039`), so the receipt digest
changes when the same evaluation runs from a different run root. Its `reason`
field can also embed an exception string containing paths (`:11013`). A receipt
digest is therefore an operational identity, not a semantic one, and including
it would break replay determinism.

The semantic certificate content is bound instead by the receipt's
path-independent fields: `status`, the sealed `run_id`, the harness
`result_id`, and `authoritative`. `run_id` and `result_id` are both sealed
content identities carried through `shadow-compile` and re-verified field by
field by `shadow_certify` (`:10991-11002`), so binding them binds the exact
compiled run and the exact verified evidence without binding any path.

`certificate_sha256`, `receipt_id`, `result_dir`, `reason`, `run_dir`,
`pass_id`, `claim_time`, `lease_token`, `open_attempt_id`,
`terminal_attempt_id`, wall-clock durations, and retry counts are **excluded**
from `result_id` and live only in the terminal-attempt record. Replaying the
same evaluation over unchanged evidence therefore yields the same `result_id`
and different attempt identities, with no determinism contradiction.

**The `authoritative` bit is retained but never obeyed.** `shadow_certify`
sets `authoritative` true when the status is `pass` **and** `routing_mode ==
"catalog_plus_candidate"` **and** every executor attests `real_backend`
(`skill-evaluation.py:11032-11036`). Revisions 3 and 4 leaned on
`candidate_only` to make that bit always false; revision 5 removes that mode,
so the bit can now be true, and the earlier second enforcement of
no-install-authority is gone.

It is replaced by an enforcement that does not depend on a certificate field at
all, for two independent reasons. First, `evaluating` has no forward edge in
`DECLARED_TRANSITIONS`, so a passing result cannot move a record toward
publication however the bit reads. Second, the stage never reads the bit as
authority: it retains it as evidence and takes the identical `evaluating ->
ready_for_draft` exit for `pass`, `regression`, `inconclusive`, and `stale`
alike.

That is the safer construction regardless, because the bit is only
*adapter-attested*: `real_backend` is a boolean the executor declares about
itself and `real_backend_source` is free text, checked for type and consistency
but never independently verified (`skill-evaluation.py:10767-10770`). A field
an adapter can assert about itself is not a basis for installing anything.
`certificate_authoritative` stays inside `result_id` so an authoritative result
and a non-authoritative one over otherwise identical inputs cannot collide.

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
- the estate census `physical_instances` records and snapshot digest
  (`dreaming-estate.py:1246-1266`, `:1739`), read as the approved target
  catalog authority and not modified;
- the owner-recomputed `skill_load_trace` (`dreaming-core.py:1156-1214`),
  read as the conflict-target selector;
- `shadow_catalog` and `shadow_copy_tree` (`skill-evaluation.py:10552-10589`),
  which independently refuse symlinks and non-skill directories;
- `trusted_authoring_adapter_path` (`skill-evaluation.py:432-443`) and
  `evaluation_input_author_doctor` (`dreaming-vendor-adapter.py:3734`);
- the `evaluation_input_owner` config schema and validator
  (`dreaming-core.py:161-167`, `:5588-5614`), whose key set is not extended;
- the existing halt file, pass accounting, dashboard projection, rollback, and
  restore boundaries.

Extended:

- `ROLES` and `ROLE_CONFIG_KEYS` gain one evaluator role;
- `validate_evaluation_input_packet` gains one shadow branch, and
  `evaluation_input_source_paths`, the `--operation` choices
  (`dreaming-vendor-adapter.py:6689`), and the operation-to-packet-kind map
  (`:3490`) each gain one `shadow-author` entry;
- `skill-evaluation.py` gains `shadow-author-packet` and reuses
  `trusted_authoring_adapter_path` for the shadow stage;
- the existing sealed `evaluation_input_owner` block gains a second consumer,
  the shadow stage, reading `enabled` and `author_model` with no new key;
- the stateless bounded wrapper covers the pinned authoring adapter as well as
  the evaluator and lifecycle tools, since the pinned adapter has no registry
  entry and therefore no `ExecutableAdapter` timeout to reuse;
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
  -- pre-claim, no lifecycle mutation, no record written --------------------
  -> candidate admission: remaining_pass_seconds >= worst_case
       + deadline_margin, every term mechanically enforced
  -> materialize package into pass-local scratch; refuse preparation-oversize
       above the declared file/byte ceilings
  -> re-hash materialized inventory to candidate_id -> package_digest
  -> select conflict target from the owner-recomputed skill_load_trace;
       materialize that one census instance into scratch/catalog and
       re-hash it to its census inventory_sha256
  -> build shadow-author-packet (closed schema, four source roots)
  -> validate packet; refuse on any prohibited field, source, or value
  -> prepare_id = f(lifecycle, candidate, version, record digest,
       package_digest, packet_id, executors document)
  -> revalidate: lifecycle version + record digest unchanged since
       preparation; scratch re-hashes to package_digest; catalog scratch
       re-hashes to inventory_sha256 and the census snapshot digest is
       unchanged; retained packet bytes re-hash to packet_id
  -> claim admission: remaining_pass_seconds >= claimed_bound
       + deadline_margin
  -- claimed stage, bounded work only ---------------------------------------
  -> lease assert; append open-attempt record (write-ahead fence)
  -> lease assert; transition ready_for_draft -> evaluating
       (--expected-version, --expected-record-sha256)
  -> author adapter: --role evaluation-input-author --model <author_model>
       run --operation shadow-author --packet <path> --draft-output <path>
       -> five case prompts, or a refusal code
  -> shadow-compile   (bounded wrapper; --catalog-dir scratch/catalog;
       sealed run manifest binding candidate_id and catalog_id)
  -> shadow-execute   (synchronous; runs to completion inside the reservation)
  -> shadow-certify   (bounded wrapper incl. nested shadow-verify subprocess;
       same --catalog-dir, or the identity re-check reports stale)
  -> retain semantic result under result_id; retain packet under packet_id
  -> lease assert; transition evaluating -> ready_for_draft
  -> append terminal-attempt record naming open_attempt_id and result_id
  -> discard the pass-local scratch root
  -> add subject to pass-local attempted set; loop while allowance and time remain
  -> run report and dashboard projection
```

## Deterministic and model responsibilities

Deterministic code owns: recovery, readiness derivation, execution-availability
computation, ordering, admission, conflict-target selection from the
owner-recomputed trace, pre-claim package and catalog materialization,
re-hashing, packet construction and validation, pre-claim revalidation of the
lifecycle and package authority, claim fencing, the fixed suite template,
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
`(lifecycle_id, candidate_id)` pairs claimed, recovered, or refused before
claim during the current pass. Every terminal result and every pre-claim
refusal adds its subject to the set, and readiness derivation excludes the set. A persistent failure therefore
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
durable write: the open-attempt record, the claim transition, the packet
retention, the result retention, the exit transition, and the terminal-attempt
record. All assertions
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
| Remaining pass time below the pass, candidate, or claim admission bound | Retain `pass_deadline_reservation_unmet`; prepare nothing further and claim nothing. A claim-admission failure additionally marks the pass unhealthy, because a bound was under-declared |
| Declared revision inventory exceeds `package_file_ceiling` or `package_bytes_ceiling` | Refuse `preparation-oversize` before copying a byte; no claim, no record; discard scratch; next candidate |
| Preparation or revalidation exceeds its deterministic bound | Refuse `preparation-deadline-exceeded` or `revalidation-deadline-exceeded`; no claim, no record; discard scratch; next candidate |
| Lifecycle record changed between routing and preparation, or between preparation and claim | Refuse `evaluation-claim-stale` before the claim; no transition and no record, because nothing was claimed; next ready candidate |
| Package bytes missing, or inventory does not re-hash to `candidate_id`, at preparation or at revalidation | Refuse `candidate-package-tampered` before the claim; no packet is built or revalidated, no model call, no transition, no record; discard scratch; the refusal is retained as run-report evidence and reproduces deterministically next pass |
| Packet fails its own validator at build or at revalidation | Refuse the matching `shadow-packet-*` code before the claim; never call the model; no transition and no record |
| Authoring model returns `insufficient_information` | Retain the refusal code as a terminal result, exit `evaluating`, retry in a later pass |
| Authoring result malformed, duplicated, or template-drifted | Retain `authoring-result-invalid`, exit `evaluating`; the existing adapter validation is the gate |
| Designed suite fails `shadow_suite` validation | Retain `suite-invalid`, exit `evaluating` |
| `shadow-compile` refuses | Retain `compile-refused` with the evaluator message, exit `evaluating` |
| `shadow-execute` fails, exceeds a per-call limit, or an adapter attestation mismatches | Retain `execute-failed` or `execute-timeout`, exit `evaluating`; no partial result is certified |
| Authoring adapter exceeds `author_call_bound` | The stateless bounded wrapper terminates the process group — the pinned adapter has no registry entry and therefore no `ExecutableAdapter` timeout; retain `author-call-timeout`, exit `evaluating` |
| `shadow-compile` exceeds `compile_bound` | Bounded wrapper terminates the process group; retain `compile-timeout`, exit `evaluating` |
| `shadow-certify` or its nested `shadow-verify` subprocess exceeds `certify_bound` | Bounded wrapper terminates the whole process group, not just the direct child; retain `certify-timeout`, exit `evaluating` |
| A lifecycle transition or record write exceeds `settlement_bound` | Bounded wrapper terminates the process group; retain `settlement-timeout`; the record is repaired by the next pass's recovery sweep |
| `shadow-certify` returns `stale` | Retain `certificate-stale`; candidate or executor drifted mid-run; exit and retry later |
| `shadow-certify` returns `regression` or `inconclusive` | Retain the result as terminal evidence, exit `evaluating`, grant no authority |
| `shadow-certify` returns `pass` | Retain the result, exit to `ready_for_draft`, grant no install or publication authority |
| `shadow-certify` returns `pass` with `authoritative: true` | Identical handling: the bit is retained as evidence, never read as authority, and the exit and estate behaviour are byte-identical to the `authoritative: false` case |
| No trace entry names a catalog skill for any authorizing occurrence | Refuse `shadow-conflict-target-unavailable` before the claim; report-only; retryable when a later occurrence supplies one |
| The selected name resolves to zero or several census instances | Refuse `shadow-conflict-target-ambiguous` before the claim; no guessing and no similarity fallback |
| Census digest changed since preparation, or catalog bytes do not re-hash to `inventory_sha256` | Refuse `shadow-catalog-snapshot-stale` before the claim; discard scratch; next candidate |
| The incumbent's SKILL.md description trips the packet sensitive-value scan | Refuse `shadow-packet-sensitive-value` before any model call; the candidate is not evaluable this pass |
| Halt file appears between operations | Stop before the next claim, retain `halted`; any claimed candidate still completes its owned exit |
| Remaining pass time insufficient for the full computed formula, preparation included | Refuse before preparation with `pass_deadline_reservation_unmet`; no scratch, no open-attempt record |
| Scratch root left behind by a fenced-out or crashed pass | The next owner's recovery sweep deletes every scratch root whose pass id is not the current pass; scratch is never read as authority |
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
   bytes re-hash to it, the exact `catalog_id` whose one skill re-hashed to its
   census `inventory_sha256` under an unchanged census snapshot digest, the
   exact canonical occurrence identities that authorized it, and the exact
   `packet_id` the model saw.
8. `result_id` is computed over path-independent semantic fields only. It never
   includes the certificate receipt digest, `result_dir`, `run_dir`, the
   certificate `reason` string, or any pass, lease, attempt, or timing field;
   those live only in the terminal-attempt record.
9. A shadow evaluation grants no install, publication, portfolio, or admission
   authority, and adds no forward lifecycle edge. This holds independently of
   the certificate's `authoritative` bit, which `catalog_plus_candidate` mode
   may set true: the stage never reads that bit as authority, and takes the
   identical `evaluating -> ready_for_draft` exit and identical estate
   behaviour for every certificate status.
10. The authoring adapter is the content-pinned adapter, invoked with
    `--operation shadow-author` and exactly one `--packet` path and no other
    input path, under the exact `author_model` identity from the sealed
    `evaluation_input_owner` block; any unknown key, prohibited source, or
    sensitive value is a refusal before the model call.
11. Evaluation never creates, consumes, or resets recurrence evidence; a
    returned candidate keeps its evidence, count, and group identity.
12. Every claimed subject has exactly one retained terminal-attempt record per
    pass; a subject refused before the claim has none, because it was never
    claimed; and no `(lifecycle_id, candidate_id)` is prepared or claimed twice
    in one pass.
13. `evaluation_execution.available` is true if and only if execution
    **authority** is present, computed from the same live facts the stage uses
    and failing closed on unknown authority. It is an authority claim, not a
    scheduling promise: the stage never starts when `available` is false, and
    when `available` is true the stage may still decline for allowance,
    reservation, halt, or pass-local attempted-set reasons, each of which is a
    named stop reason. `executor_unavailable` is reported only when `available`
    is false.
14. Every term of the formula is mechanically enforced: subprocess terms by a
    bounded process-group call with terminate-then-kill cleanup, and
    deterministic in-process terms by the bounded deterministic helper with a
    declared size ceiling and a between-item deadline. Every term has a named
    refusal and no term is an unenforced estimate.
15. No deterministic preparation, materialization, re-hashing, packet
    construction, or packet validation occurs after the claim; the claimed
    stage contains only bounded model, harness, evaluator, and settlement
    work. The stage never starts preparation whose computed
    worst case, preparation and termination overhead included, exceeds the
    remaining pass time less the deadline margin, and never claims when the
    claimed bound alone no longer fits.
16. Every started authoring call and started trial batch spends allowance;
    recovery, readiness derivation, ordering, admission, preparation, and
    pre-claim refusal never do.
17. Repair outcomes remain report-only and never enter this stage.
18. The pass keeps exactly one writer lease and one owner.
19. The approved target catalog is exactly one census-derived skill, selected
    by the first catalog load in the owner-recomputed skill-load trace of the
    canonically ordered authorizing occurrences. No similarity, keyword, or
    embedding judgement participates, and an absent or ambiguous target is a
    pre-claim refusal rather than a guess.
20. The packet discloses from the catalog only that one skill's directory name
    and SKILL.md description; every other catalog byte, census row, capability
    identity, and second skill remains a prohibited source.

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
  environment id, routing id, harness digest, executor identities, and the
  certificate's `status`, `run_id`, `result_id`, and `authoritative` flag. Its
  identity domain contains no certificate receipt digest, `result_dir`,
  `run_dir`, certificate `reason` string, or any pass, lease, attempt, or
  timing field.
- **AC-E3b:** The open-attempt record names exactly `origin`, `lifecycle_id`,
  `candidate_id`, `record_version`, `record_sha256`, `lease_token`, `pass_id`,
  `claim_time`, and `stage_generation`, and never gains an outcome, duration,
  `packet_id`, or `result_id`. Its terminal-attempt record names that exact
  `open_attempt_id`, the outcome, the stop reason, `retained_by_pass_id`,
  `settle_time`, phase durations, and the `result_id` when one exists.
- **AC-E4:** A `pass` result produces no install, publication, or forward
  lifecycle transition, and no estate mutation, and this holds identically when
  the certificate reports `authoritative: true`.
- **AC-E5:** Every failure row in the failure model that occurs while the lease
  is held **after the claim** leaves the lifecycle record in `ready_for_draft`
  with exactly one appended terminal-attempt record, and a later pass can claim
  the same candidate again. Every **pre-claim** refusal row leaves the
  lifecycle record untouched in `ready_for_draft`, writes no attempt record and
  no result, discards its scratch root, retains its stable refusal code in the
  run report, and is reproduced identically by a later pass over the same
  bytes.
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
  `open_attempt_id` and `terminal_attempt_id` values, **including when the
  second replay uses a different run and result directory**, which changes the
  certificate receipt digest without changing `result_id`.
- **AC-E11:** The stage computes the full worst-case bound — materialization,
  re-hashing, packet construction, packet validation, revalidation, authoring,
  attestation, trials, compile, certify, settlement, and termination overhead —
  **before preparation begins**, and refuses with
  `pass_deadline_reservation_unmet` without writing a scratch root or an
  open-attempt record when remaining pass time is below that bound plus the
  deadline margin. The settlement term counts every durable object the claimed
  stage writes: `max_transitions_per_candidate` lifecycle transitions and
  `max_records_per_candidate` = **four** record writes — open-attempt, retained
  packet, semantic result, terminal-attempt. The claim-admission re-assertion
  of `claimed_bound` never fails on a correctly declared bound, and no
  deterministic preparation step runs after the claim. Every completed pass
  finishes inside the 3,600-second deadline with the margin intact.
- **AC-E14:** Every bounded subprocess the stage starts is terminated within
  its declared bound plus `termination_grace`, its whole process group is gone,
  and the breach surfaces as its named terminal refusal — including
  `shadow-certify`'s nested `shadow-verify` child. Every deterministic
  in-process term refuses within its bound too: an over-ceiling inventory is
  refused as `preparation-oversize` before any byte is copied, and an overrun
  preparation or revalidation refuses as `preparation-deadline-exceeded` or
  `revalidation-deadline-exceeded` and leaves no scratch root and no record.
  Each of the four durable record writes is bounded by `record_write_bound`,
  and a claimed stage never writes a fifth durable object.
- **AC-E12:** For every permutation of execution authority, including each
  authoring-authority sub-condition of E2b, a routing row's
  `evaluation_execution.available` and its computed reasons exactly match the
  authority facts: the stage never starts when `available` is false, never
  reports `executor_unavailable` when `available` is true, and unknown or
  malformed authority yields `available: false` with
  `shadow-execution-authority-unknown`. When `available` is true the stage may
  still decline for allowance, reservation, halt, or attempted-set reasons, and
  each such decline carries its own named stop reason rather than an
  authority reason.
- **AC-E15:** The authoring adapter is the content-pinned adapter resolved by
  `trusted_authoring_adapter_path`, invoked as
  `--role evaluation-input-author --model <author_model> run --operation
  shadow-author --packet <path> --draft-output <path>`, where `author_model` is
  read verbatim from the sealed `evaluation_input_owner` block; no
  installed-capability owner entry point is invoked and no adapter registry
  entry is added.
- **AC-E16:** No code path reads the certificate's `authoritative` bit as
  install, publication, or admission authority. Two runs identical except for
  that bit produce byte-identical estate contents, the same
  `evaluating -> ready_for_draft` exit, the same retained record shapes, and
  differ only in the retained value of `certificate_authoritative`.
- **AC-E17:** The stage compiles in `catalog_plus_candidate` mode against a
  catalog containing exactly one skill, whose bytes re-hash to the census
  `inventory_sha256` under an unchanged census `snapshot_sha256`, selected by
  the first catalog load in the owner-recomputed skill-load trace of the
  canonically ordered authorizing occurrences; an absent or ambiguous target
  refuses before the claim with its stable code; and the retained result binds
  `catalog_id`, `snapshot_sha256`, `canonical_capability_id`, and
  `inventory_sha256`.
- **AC-E18:** With a real-backend executor and a genuine incumbent, the stage
  can reach `status: pass`, meaning every routing case proved its exact
  expected candidate and catalog loads and every `task_value` pair completed on
  both arms — the parent Definition of Done's triggering, task-performance, and
  overall-regression evaluation. No fixture result, weakened gate, or
  inconclusive diagnostic is ever recorded as a pass.
- **AC-E13:** With two ready candidates where the first-sorted fails
  terminally, the second is still claimed and evaluated in the same pass while
  allowance and reservation permit, and neither subject is prepared or claimed
  twice; a subject refused before the claim likewise blocks its successors
  neither in this pass nor the next; in a later pass the previously attempted
  subject sorts after any never-attempted subject.

## Check contract

These checks are development-loop evidence: they are executed against the
implementation, not against this document, and several carry the proof gates
tabulated under "Design clearance and development-loop proof gates". They are
mandatory before the pull request and before the Definition of Done, and they
are not prerequisites for writing the code that produces them.

| Check | Criterion | Setup | Pass evidence | Failure evidence |
| --- | --- | --- | --- | --- |
| CHK-01 | AC-E1, AC-E2 | One lifecycle record with three distinct current occurrences, shadow-ready recommendation, one staged package; one census fixture whose skill-load trace names one resolvable incumbent; healthy fixture evaluator and fixture author | One result retained; manifest `candidate_inventory` equals revision `files`; final state `ready_for_draft`; exactly one open record and one terminal record naming it | No result, a result naming a different candidate id, an unsettled open record, or any record written twice |
| CHK-02 | AC-E3a, AC-E3b | CHK-01 with two distinct authorizing profiles | Result lists both `profile_id`s, all three canonical occurrence ids, record version, `packet_id`, suite id, harness digest, executor identities, and no pass/lease/timing field; open record's key set is exactly the nine declared fields; terminal record names that `open_attempt_id`, outcome, stop reason, `retained_by_pass_id`, `settle_time`, durations, `result_id` | Any authority absent or mismatched, a pass/lease/timing field in the result, or an outcome/result field in the open record |
| CHK-03 | AC-E4, AC-E16 | CHK-01 forced to `pass` twice: once with a fixture executor attesting `real_backend: false` and once attesting `real_backend: true`, which makes `shadow_certify` set `authoritative: true` | Skill roots byte-identical before and after both runs; no publisher call; no transition other than in and out of `evaluating`; the two runs differ only in the retained `certificate_authoritative` value and the resulting `result_id` | Any estate mutation, publication, forward transition, or any behavioural difference between the two runs beyond the retained bit |
| CHK-12 | AC-E17 | Catalog permutations: a trace whose first catalog load resolves to exactly one census instance; a trace with no catalog load; a name resolving to two instances; a census digest changed between preparation and revalidation; catalog bytes altered after materialization; and an incumbent description containing an absolute path | The first permutation materializes exactly one skill directory, re-hashes to `inventory_sha256`, compiles with `--catalog-dir`, and binds `catalog_id`, `snapshot_sha256`, `canonical_capability_id`, and `inventory_sha256` in the result; the rest refuse before the claim with `shadow-conflict-target-unavailable`, `shadow-conflict-target-ambiguous`, `shadow-catalog-snapshot-stale`, `shadow-catalog-snapshot-stale`, and `shadow-packet-sensitive-value` respectively, writing no record | A catalog with zero or several skills, a selection made by similarity or keyword, a claim made on a stale census, an unbound catalog identity, or a prohibited catalog byte reaching the packet |
| CHK-04 | AC-E5 | Two fixture groups. Post-claim: authoring refusal, malformed authoring result, invalid suite, compile refusal, execute failure, certify `stale`. Pre-claim: over-ceiling inventory, tampered package at preparation, tampered package at revalidation, packet validator refusal, lifecycle version changed between preparation and claim | Each post-claim row leaves `ready_for_draft`, appends exactly one terminal record with its named outcome, and a second pass re-claims the same candidate. Each pre-claim row leaves the lifecycle record byte-identical, writes no open record, no terminal record, and no result, leaves no scratch root, carries its stable refusal code in the run report, and reproduces identically in a second pass | Any post-claim row leaving `evaluating`, appending no terminal record, appending two, or blocking re-claim; any pre-claim row transitioning the record, writing any attempt record, leaving scratch behind, or refusing non-deterministically |
| CHK-05 | AC-E6 | Six fixtures: lease assertion forced to fail mid-stage; open record left unsettled by a killed pass; two unsettled open records for one subject and version; two terminal records naming one open record; record in `evaluating` with no open record; record not in `evaluating` with an unsettled open record | A byte-level snapshot of every durable root — lifecycle records, `attempts/`, `results/`, `packets/`, accounting, run reports, but not the non-authoritative scratch root — before and after the fenced-out pass is identical, and its `lease_lost` appears only in process output; the next pass deletes every scratch root not belonging to it; the next pass sweeps in `(claim_time, open_attempt_id)` order before readiness derivation, appends `evaluation-abandoned` with `recovered_after_owner_loss` attributed to itself; collision recovers the earliest and supersedes the rest, unhealthy; settled-twice takes the earliest, unhealthy; orphan appends an `origin: orphan-recovery` pair, degraded; not-required appends `recovery-not-required` with no transition | Any durable byte written by the fenced-out pass, a durable `lease_lost` accounting row, an unsettled open record surviving a sweep, non-deterministic recovery order, an overwritten record, or a terminal record naming no open record |
| CHK-06 | AC-E7 | Run CHK-04's certify-`stale` fixture, then supply a fourth matching occurrence | Reviewer group context includes the same `lifecycle_id`; no new lifecycle record | A new lifecycle id appears for the same procedure |
| CHK-07 | AC-E8 | Packets built with, in turn: an extra unknown key, an estate-census field, a transcript fragment, an absolute path, a UUID occurrence id, a `profile_id`, a credential-shaped token | Each refused with its stable `shadow-packet-*` code before any model call; adapter invocation carries exactly one packet path argument; retained packet bytes re-validate | Any prohibited content reaching the model, a silently stripped key, or a second adapter input path |
| CHK-08 | AC-E9, AC-E13 | Two ready candidates where the first-sorted always fails terminally, with allowance for two; then a variant with allowance for one; then zero ready candidates | Run one evaluates both subjects, each exactly once, and reconciles; run two stops with `evaluation_allowance_spent`; run three stops with `eligible_input_exhausted` listing near-miss, already-attempted, and pre-claim refusal reasons; a variant whose first-sorted subject is refused before the claim still evaluates the second and spends no allowance on the refusal; a later pass sorts the attempted subject after a never-attempted one | A failing or pre-claim-refused candidate blocking its successor, any subject prepared or claimed twice in one pass, allowance spent on a pre-claim refusal, or unused allowance with no stop reason |
| CHK-09 | AC-E11, AC-E14, AC-E18, and sets the unresolved allowances | One real configured evaluator with a real backend, one candidate, one genuine census incumbent as the conflict target, natural pass; two synthetic runs with remaining pass time set just below the candidate-admission bound and just below the claim-admission bound; one deliberately overrunning fixture per bounded subprocess term (author, executor call, compile, certify with a sleeping nested `shadow-verify`, lifecycle transition); one over-ceiling revision inventory; one artificially slowed materialization and one slowed revalidation; and one durable-write counter over the natural run | Every term of the printed formula — preparation, revalidation, authoring, attestation, trials, compile, certify, settlement, termination — is present and finite **before preparation starts**; the natural run performs zero materialization, re-hashing, packet build, or packet validation after the claim, proved by an ordered phase trace; the synthetic runs refuse with `pass_deadline_reservation_unmet` writing neither scratch nor open record; each overrun subprocess fixture is terminated within its bound plus `termination_grace` with no surviving process in its group; the over-ceiling inventory refuses `preparation-oversize` with zero bytes copied; the slowed fixtures refuse `preparation-deadline-exceeded` and `revalidation-deadline-exceeded`; the natural run's claimed stage performs exactly four durable record writes — open-attempt, retained packet, semantic result, terminal-attempt — matching the `max_records_per_candidate` term of the printed formula; retained per-call and per-phase cost becomes the configured `author_call_bound`, `author_doctor_bound`, `executor_call_bound`, `compile_bound`, `certify_bound`, `lifecycle_transition_bound`, `record_write_bound`, `lifecycle_read_bound`, `packet_build_bound`, `packet_validate_bound`, `package_file_ceiling`, `package_bytes_ceiling`, `prepare_throughput`, `hash_throughput`, `termination_grace`, and `deadline_margin` additionally, the natural run reaches a certificate status derived from real trials, and if that status is `pass` it is recorded as the parent Definition of Done's triggering, task-performance, and overall-regression evidence, with the routing gate proving exact candidate and catalog loads for all five classes | Any pass claimed from a fixture executor, a weakened gate, an inconclusive result reported as a pass, a missing or infinite formula term, any deterministic preparation observed after the claim, a start accepted below any admission bound, a scratch root or open record written by a refused admission, a surviving child process after any bound, a durable record write not counted by `max_records_per_candidate`, an unrefused over-ceiling inventory, or allowances configured without this measurement or an owner-recorded provisional bound |
| CHK-10 | AC-E10, AC-E3a | CHK-01 run twice over unchanged evidence with **both** a pinned deterministic author fixture returning a fixed case set and a pinned deterministic executor fixture returning fixed trial output, and with the second run given a **different run and result directory** | Identical `result_id` across both runs even though the certificate `receipt_id` and `result_dir` differ; distinct `open_attempt_id` and `terminal_attempt_id`; both terminal records name the same `result_id` and different open records; the retained result contains no path-bearing field | Differing `result_id` when only the run root changed, identical attempt identities across passes, or any `result_dir`, `run_dir`, `reason`, or receipt digest inside the result identity domain |
| CHK-11 | AC-E12, AC-E15 | The authority permutation matrix: evaluator unconfigured, configured-unhealthy, healthy-unattested, suite authority missing, package unavailable, census absent or digest-mismatched, malformed authority object, all-present; plus one permutation per E2b authoring sub-condition: `evaluation_input_owner` absent, invalid key set, `enabled: false`, empty `author_model`, authoring adapter digest mismatched, author doctor unhealthy, and `shadow-author-packet` absent; plus one permutation with every other authority present and one reservation allowance unconfigured | Each permutation's routing row reports the matching computed reason and `available` value; every authoring sub-condition yields `shadow-authoring-authority-unavailable`, an absent or unverifiable census yields `shadow-catalog-authority-unavailable`, an unconfigured allowance yields `shadow-allowance-unconfigured` with `available: false` and no defaulted value anywhere in the row, and a malformed authority object yields `shadow-execution-authority-unknown`; in the all-present case the adapter is invoked exactly once with `--operation shadow-author` and a single `--packet` path under the configured `author_model`, and no `v2-input-owner-run` or `configured_adapters` path is entered; declines under `available: true` carry allowance, reservation, halt, or attempted-set stop reasons rather than authority reasons | Any row claiming authority the host lacks, any stage start under `available: false`, an `executor_unavailable` report under `available: true`, an authority reason used for a scheduling decline, a second adapter input path, or a hard-coded blocker constant surviving in `profile_evaluation_routing.py` |

CHK-01 through CHK-08 and CHK-10 through CHK-12 are deterministic and run with
fixture author and evaluator adapters in the standalone suites. CHK-03's
`authoritative: true` half uses a fixture attesting `real_backend`, which is
legitimate for proving that stage policy ignores the bit, and is never counted
as a Definition-of-Done pass. CHK-09 is the
only check requiring a real model and a natural installed pass; its synthetic
reservation-refusal half is deterministic and runs with the others.

## Migration and rollback

Migration is additive. No existing record changes shape. Absent the new
`evaluators` config section, execution availability computes false, every
routing row reports `shadow-executor-unconfigured`, the stage reports
`executor_unavailable`, changes no state, and the rest of the pass is
unaffected, so an unconfigured host keeps today's behavior exactly.

The catalog authority is read-only and additive too. The census is already
built; this stage adds no field to it and writes nothing back. A host with no
census, no resolvable incumbent, or an ambiguous one simply refuses before the
claim with its stable code and evaluates nothing, which is the same
report-only outcome as an unconfigured evaluator. Rollback is deleting the
stage: the census, the dispositions, and the lifecycle records are untouched by
it.

Shadow authoring adds no configuration key. It reads `enabled` and
`author_model` from the existing sealed `evaluation_input_owner` block, so a
host that has never configured that block simply reports
`shadow-authoring-authority-unavailable` and the stage does not start. A host
that later disables the block disables shadow authoring with it, by design.

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
- [ ] The authoring adapter is resolved by `trusted_authoring_adapter_path`,
      invoked with `--operation shadow-author` and one `--packet` path under
      the sealed `evaluation_input_owner.author_model`, health-checked by the
      existing role doctor, with no new config key, no registry entry, and no
      installed-capability owner call.
- [ ] All deterministic preparation — materialization, re-hashing, packet
      construction, packet validation — completes before the lifecycle claim,
      and an immediately pre-claim revalidation re-asserts the exact lifecycle
      version and record digest, the package digest, and the packet id.
- [ ] The stage computes a full worst-case bound covering preparation,
      revalidation, authoring, attestation, trials, compile, certify,
      settlement, and termination overhead, and refuses before preparation
      begins when it does not fit, writing neither a scratch root nor a record.
      Its settlement term counts all four durable record writes the claimed
      stage performs — open-attempt, retained packet, semantic result,
      terminal-attempt — and the stage writes no fifth durable object.
- [ ] Every term of that bound is mechanically enforced: subprocess terms by a
      bounded process-group call with terminate-then-kill cleanup, including
      `shadow-certify`'s nested verification subprocess, and deterministic
      in-process terms by a declared size ceiling plus a between-item deadline;
      each has a named refusal, and halt is checked between bounded operations
      only.
- [ ] A pre-claim refusal changes no lifecycle state, writes no attempt record
      and no result, discards its scratch root, and is retained as run-report
      evidence that a later pass reproduces deterministically.
- [ ] One scheduled pass claims ready candidates derived from existing
      dispositions and lifecycle authority, ordered by last attempt then
      earliest occurrence then lifecycle id, with no new queue row.
- [ ] An immutable open-attempt record is durable before the claim transition,
      is never rewritten or extended, and is settled only by appending one
      immutable terminal-attempt record naming its digest; openness is derived
      by scan, and collision, settled-twice, superseded, and orphan cases behave
      as tabulated.
- [ ] The materialized package re-hashes to the claimed `candidate_id` before
      any packet is built and again immediately before the claim, and
      preparation writes only to a pass-local scratch root outside every
      durable record root.
- [ ] `shadow-compile`, `shadow-execute`, and `shadow-certify` run unmodified,
      in `catalog_plus_candidate` mode with `--catalog-dir` pointing at the
      one-skill census snapshot, and produce one certificate covering
      triggering, task performance, and overall regression.
- [ ] A real-backend run can reach `status: pass`, satisfying the parent
      funnel's Definition of Done, and no fixture, weakened gate, or
      inconclusive diagnostic is ever recorded as a pass.
- [ ] The catalog contains exactly one census-derived skill bound by
      `snapshot_sha256`, `canonical_capability_id`, and `inventory_sha256`,
      chosen without any similarity, keyword, or embedding judgement, with
      absent and ambiguous targets refusing before the claim.
- [ ] The semantic result binds candidate, occurrences, profiles, lifecycle
      version, packet, and the certificate's path-independent `status`,
      `run_id`, `result_id`, and `authoritative` fields, and excludes the
      certificate receipt digest, every path-bearing field, and all pass,
      lease, and timing fields, which live only in the terminal-attempt record.
- [ ] The stage compiles in `catalog_plus_candidate` mode against a one-skill
      catalog materialized from the estate census, with the conflict target
      chosen from the owner-recomputed skill-load trace, so a truthful `pass`
      on triggering, task performance, and overall regression is reachable.
- [ ] An `authoritative: true` certificate produces byte-identical estate and
      lifecycle behaviour to an `authoritative: false` one; no code path reads
      the bit as install, publication, or admission authority.
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
- [ ] Every allowance and bound is read from explicit owner-provided
      configuration; a missing value makes the stage fail closed and
      unavailable rather than defaulting, and any provisional value is recorded
      as provisional with its measurement scheduled.
- [ ] CHK-01 through CHK-08 and CHK-10 through CHK-12 pass deterministically;
      CHK-09 records the measured cost that sets the configured allowances and
      the one real-backend certificate status.
- [ ] Development-loop proof gates PG-1 through PG-7 are all recorded. They are
      runtime evidence, so they are produced by the implementation rather than
      required before it, but none of this Definition of Done is complete
      without them.
- [ ] The plan baton, this work order, and the local commits are complete, and
      the slice has a reviewed PR targeting `feature/multi-cli-dreaming`.

## Design clearance and development-loop proof gates

Design-doc closes on static evidence. Runtime evidence belongs to
development-loop, which cannot run before the code that produces it exists.
The previous revision conflated the two: it required an attestation trace, a
model-authored suite, timeout fixtures, a replay trace, a measured allowance,
and a real-backend certificate before clearance, then forbade starting the
stage until all of them existed. Nothing could ever satisfy it. Revision 7
splits the list at that seam.

### Static design clearance, recorded at `fe38fe0`

Two independent reviewers read the work order to a fixed point. Terra's final
round found one static defect, the four-write reservation under-count, and that
defect is fixed at `fe38fe0`. Opus cleared `71145af` and read `fe38fe0` as
regression-free. The following architecture decisions are therefore **cleared**,
and each is a static property of this document that a reviewer can check by
reading it against the cited code:

1. **Existing owner and registry reuse.** The stage is a bounded stage inside
   the one existing scheduled owner pass. Registering a fourth adapter role,
   adding a shadow subject mode to the existing authoring boundary, adding two
   append-only record types, and adding one bounded-subprocess wrapper are
   extensions of existing authorities. No second queue, owner, scheduler,
   transcript transport, catalog stage, or deterministic semantic classifier is
   introduced. See "Reuse contract" and the non-goals.
2. **Exact packet and config authority.** The closed, versioned
   shadow-author-packet schema in E2 names its permitted fields, its four
   permitted source roots, its prohibited fields and sources, its
   `additionalProperties: false` behavior, and its stable refusal codes. E2b
   names the exact packet-only invocation, the content-pinned unregistered
   adapter, the sealed `evaluation_input_owner.author_model`, and the existing
   doctor predicate that pins the vendor. No authoring config key and no
   adapter registry entry are added, so no configured host breaks.
3. **Append-only recovery model.** One immutable open-attempt record written
   before the claim, settled by appending one immutable terminal-attempt record
   naming its digest; openness derived by scan; a fenced-out pass writing zero
   durable bytes; `lease_lost` as process-return evidence only; the next valid
   owner retaining the durable outcome and stop reason; and the tabulated
   collision, settled-twice, supersession, not-required, and orphan behaviors.
   E6 and "Lease loss and recovery" are internally consistent and leave no
   mutable state and no in-place rewrite.
4. **Enforced-bound design.** Every term of the worst-case formula names an
   existing enforced timeout or a specified minimal bounded wrapper, with a
   named terminal refusal, cleanup overhead, and a finite operation and
   subprocess count. All deterministic preparation precedes the claim, the
   claim-admission re-assertion is defensive, and settlement counts all four
   durable writes. What is cleared here is that the design is mechanically
   enforceable, not that any number is right.
5. **Census catalog reuse.** The approved target catalog in E2c is one skill
   materialized from an already-derived estate-census physical instance, in a
   snapshot the pass already builds and the owner already reads. Conflict-target
   selection is the first catalog load in the owner-recomputed
   `skill_load_trace`, a behavioral fact, not a semantic judgement. This is
   reuse of an existing authority, not a second catalog-audit stage.
6. **Truthful pass semantics.** `catalog_plus_candidate` is selected because
   `shadow_aggregate` makes a `candidate_only` `pass` unreachable by
   construction (`skill-evaluation-harness.py:2137-2139`), so the parent
   funnel's Definition of Done could not otherwise be met. No gate is weakened,
   no fixture result is manufactured, and an inconclusive diagnostic is never
   reported as a pass.
7. **No install or publication authority.** A passing result grants none. The
   certificate's `authoritative` bit may become true in
   `catalog_plus_candidate` mode with a real backend, and the stage still never
   reads it: `evaluating` has no forward edge to any install-bearing state, and
   the stage takes the identical exit for every status. Two independent
   prohibitions, neither depending on the bit's value.
8. **No revisit condition has fired.** Every row of the constraint-provenance
   table was re-read against the added stage, in particular the pass-deadline,
   packet-prohibition, receipt-digest, and authoring-identity rows. The one
   deliberately unresolved row is the numeric-allowance row, which is a policy
   decision rather than a fired revisit condition; see below.

Implementation may begin from this clearance. If implementation discovers that
any cleared decision above is unbuildable as written, work returns to this
document and the status returns to OPEN with the concrete blocker recorded,
rather than being worked around in code.

### Numbers stay unresolved in the design

No numeric value is baked into this design, and implementation must not invent
a default. The implementation reads every allowance and bound from explicit
owner-provided configuration. Where a required value is absent, the stage is
**fail-closed**: `evaluation_execution.available` computes false with a named
reason, the routing projection says so, and the stage runs nothing. An owner
may set an explicit provisional value to unblock the first runs, which is
recorded as a provisional bound with the measurement scheduled; CHK-09 then
replaces it with measured cost. A silently defaulted number is the failure mode
this rule exists to prevent, because it would make the reservation an estimate
rather than a declared policy.

### Development-loop proof gates

These gates are runtime evidence. They are **mandatory before the pull request,
before landing, and before this work order's Definition of Done can be
checked**, and they no longer block writing the implementation that produces
them. Each names the check that carries it.

| Gate | Evidence required | Carried by |
| --- | --- | --- |
| PG-1 attestation | A dry-run trace showing the configured `skill-evaluation-executor` adapter's `version` response satisfying `shadow_attest` against the derived executors document, proving E1 needs no invented identity | CHK-01, CHK-11 |
| PG-2 authored suite | One validated shadow suite from the fixed template plus five model-authored prompts, accepted by `shadow_suite` in `catalog_plus_candidate` mode against the one-skill census catalog with the `routing_conflict` case naming that skill; plus one trace of the exact E2b invocation showing a single `--packet` argument, `--vendor copilot`, the content-pinned adapter, and the configured `author_model` | CHK-01, CHK-07, CHK-11, CHK-12 |
| PG-3 enforced reservation | The whole formula computed term by term before preparation starts; an ordered phase trace showing no deterministic preparation after the claim; a `pass_deadline_reservation_unmet` refusal writing neither scratch nor record; one overrun fixture per bounded subprocess term terminated within its bound plus `termination_grace` with no surviving process group; and the `preparation-oversize`, `preparation-deadline-exceeded`, and `revalidation-deadline-exceeded` refusals demonstrated. The record must also state that halt is deliberately not observed inside `shadow-execute`, and that the reservation rather than interruption protects the pass deadline | CHK-09 |
| PG-4 recovery fixtures | The lease-loss, unsettled-open, collision, settled-twice, orphan, and not-required fixtures behaving as tabulated, with a byte-level durable-root snapshot proving the fenced-out pass wrote nothing | CHK-05 |
| PG-5 replay determinism | A replay trace across two run roots showing an identical `result_id` while the certificate `receipt_id` and `result_dir` differ, proving the N1 identity domain is path-independent | CHK-10 |
| PG-6 allowance resolution | Either CHK-09's measured per-call and per-phase cost, or an owner-set provisional bound with the measurement scheduled, replacing the unresolved-policy row in the constraint table | CHK-09 |
| PG-7 truthful pass | One real-backend run reaching a certificate status derived from real trials, with the routing gate proving exact candidate and catalog loads for all five classes; if that status is `pass`, it is the parent Definition of Done's triggering, task-performance, and overall-regression evidence | CHK-09 |

A gate that fails is not a design failure by itself. It becomes one, and
reopens this document, only when the failure shows a cleared decision above is
wrong rather than an implementation defect or a mis-set number.
