# Task opportunity evidence design

## Objective

Add privacy-preserving task-opportunity evidence so Dreaming can distinguish a
skill that was unused because no relevant work occurred from a skill that was
unused despite repeated relevant work, and use those observed task categories
to shape synthetic evaluation cases and skill-creation proposals.

## Non-goals

- Do not treat task opportunity as skill usage, evaluation evidence, a
  recommendation, or mutation authority.
- Do not copy transcript text, prompts, responses, repository paths,
  credentials, hostnames, or private names into evaluation inputs, portfolio
  decisions, dashboards, or cross-machine receipts.
- Do not send raw transcript content to a different model provider for
  classification.
- Do not require evaluation before reversible withdrawal of an eligible
  machine-created skill with settled non-use, no matching opportunity, no
  dependency, no pin, and an exact restore path.
- Do not build a general activity taxonomy for every possible engineering
  workflow. The first taxonomy supports only task categories that can affect a
  current Dreaming skill decision or an explicit skill-creation proposal.
- Do not replace the existing transcript collector, settled-use coverage,
  evaluation harness, evaluation-input registry, portfolio decision engine,
  writer lease, halt switch, or `estate-action.py` authority boundary.
- Do not infer rare or safety-critical value from low frequency. That status
  requires explicit policy or user authority.

## Lane

**Critical.** This change reads private transcript content, creates new
decision evidence, influences automatic withdrawal eligibility, and may shape
model-authored evaluation inputs.

The boundary fails closed when unread, unclassified, ambiguous, or
privacy-rejected task episodes remain visible and cannot become zero
opportunity, passing evaluation, keep authority, or removal authority.

Rollback disables opportunity collection and decision influence while
retaining prior receipts as historical evidence. Usage, evaluation,
dependency, pin, provenance, and user-disposition behavior then continue under
their existing contracts. Rollback does not delete transcript state,
opportunity receipts, evaluation evidence, skill roots, or action receipts.

## Evidence model

Dreaming keeps three primary signals separate:

1. **Usage:** Was the exact skill successfully loaded?
2. **Opportunity:** Did work occur where the capability could plausibly help?
3. **Evaluation:** Did the exact skill improve a controlled result?

The portfolio decision joins these signals without converting one into
another.

| Opportunity | Usage | Evaluation | Portfolio interpretation |
| --- | --- | --- | --- |
| Settled zero | Settled zero | Passing | Eligible machine-created personal skill: withdraw candidate; otherwise capable but unneeded |
| Observed | Settled zero | Passing | Capability may be valuable but has an activation problem |
| Observed | Settled zero | Failing | Capability does not help with observed demand |
| Observed | Observed | Passing | Strong keep evidence |
| Observed | Observed | Failing | Used capability may be harmful, stale, or misrouted |
| Settled zero | Observed | Passing | Historical or rare use; inspect recency and explicit criticality |
| Settled zero | Settled zero | Missing | Reversible withdrawal candidate when other safety predicates pass |
| Unknown | Any | Any | Opportunity contributes no authority |

A passing evaluation proves capability behavior. It does not prove portfolio
demand. Settled zero opportunity proves only that no matching task was found
inside the decision-grade classified corpus.

## Reuse contract

This design builds on:

- the existing stable-transcript enumeration, quiet-period exclusion, pending
  file classification, failure aging, and collection watermark used for
  settled-use decisions;
- the existing MacBook-origin and Mac-mini-owner transport, receiver identity,
  content policy, and immutable receipt boundaries;
- the current canonical capability identity, dependency inventory, pins,
  provenance, and user dispositions;
- the content-addressed evaluation-input registry and safe authoring boundary;
- the existing evaluation classes for intended, related, activation-positive,
  and activation-negative behavior;
- the existing derived in-memory portfolio queue and `estate-action.py` as the
  sole mutation authorization owner.

The new pieces are:

- a bounded task-episode projection from stable transcripts;
- a versioned, capability-neutral task taxonomy;
- an opportunity receipt that aggregates task categories without transcript
  content;
- a separately reviewed mapping from task categories to capabilities;
- decision rules that join opportunity, usage, and evaluation;
- an evaluation-authoring projection that supplies only category definitions
  and synthetic task shapes.

There is no second transcript walker, scheduler, durable work queue, mutation
endpoint, or evaluation engine.

## Data flow

### 1. Reuse the settled transcript boundary

The existing collector identifies transcript files that are stable, recent
active tails, deferred stable work, unreadable, malformed, or changed during
read. Opportunity collection consumes the same classified file set and the
same collection watermark.

A transcript file excluded as an active tail remains excluded from both usage
and opportunity decisions. Stable unread work whose modification time
intersects the decision window blocks settled zero opportunity. Read and parse
failures age out only when their modification time predates the decision
window, matching settled-use behavior.

### 2. Project task episodes on the origin host

The trusted origin-host collector divides a stable transcript into bounded task
episodes. An episode begins with one user-directed task or one autonomous task
request and ends at the next independent request, terminal completion,
terminal refusal, cancellation, or session end.

One conversation may contain several episodes. Retries, clarifications, tool
calls, and assistant turns inside one episode do not create additional demand.
The episode identity binds:

- source transcript identity;
- stable byte range or source event range;
- episode ordinal;
- start and terminal timestamps;
- classifier contract identity.

Episode projection may read transcript content locally. It emits no transcript
text. If episode boundaries cannot be determined within the configured size,
event, or time bounds, the source becomes `episode_projection_failed` and
blocks settled zero opportunity for intersecting windows.

### 3. Classify against a capability-neutral taxonomy

The classifier receives no candidate skill name, capability identity,
recommendation, usage count, evaluation result, or mutation state.

The taxonomy is content-addressed and versioned. Each category contains:

- a stable category ID;
- a plain description of the task shape;
- allowed structural and semantic signals;
- a synthetic example template suitable for later evaluation authoring;
- sensitivity and exclusion rules;
- parent categories used only for display.

Initial classification is origin-local and deterministic. It may use normalized
event kinds, tool families, declared task metadata, bounded local text rules,
and outcome signals. An episode that does not meet one category exactly is
`unclassified`; one episode cannot count in more than one category.

Every unclassified episode receives one bounded candidate-neutral uncertainty
projection:

- `possible_category_ids`: categories still plausible from partial structural
  or semantic signals;
- `out_of_scope: true`: deterministic proof that the episode cannot belong to
  any category in the taxonomy.

The projection contains only the content-addressed episode identity and
category IDs. It contains no transcript text. Empty possible categories without
`out_of_scope: true`, an unknown category, conflicting fields, or a missing
projection is invalid and makes relevance unknowable.

Model-backed semantic classification is outside the first implementation. A
later extension requires a separate reviewed privacy policy and may not send
raw content across providers.

### 4. Aggregate an opportunity receipt

The origin host publishes a content-addressed opportunity receipt through the
existing receiver boundary. The receipt contains:

- the owner-supplied request identity and requested window end;
- taxonomy and classifier identities;
- source usage-receipt and collection-watermark identities;
- decision window;
- stable episode count;
- excluded active-tail count and bytes;
- relevant stable backlog and failure identities;
- classified and unclassified episode counts;
- per-category episode count, distinct-session count, distinct-day count,
  completion count, and last occurrence;
- one bounded uncertainty projection for every unclassified episode;
- no transcript text or source-local path.

The Mac mini validates the receipt before joining it to portfolio evidence.
Unknown fields, missing categories, inconsistent totals, stale receiver
identity, taxonomy drift, or source-receipt mismatch refuse the whole
opportunity view.

### 5. Map categories to capabilities separately

Task classification remains capability-neutral. A separate content-addressed
mapping relates category IDs to canonical capabilities with one relationship:

- `direct`: the capability is intended for this task category;
- `related`: the capability may improve this category;
- `negative`: the capability should not activate for this category.

The mapping is derived from the skill contract and receives deterministic
validation plus two independent reviews. It binds the exact skill candidate,
taxonomy, category definitions, relationship, and review receipts.

Changing a skill or taxonomy makes the mapping stale. A stale or missing
mapping produces unknown opportunity for that capability. It does not make the
category disappear and does not become zero.

### 6. Derive per-capability opportunity coverage

For each decision window, the owner projects one state:

- `observed_opportunity`: at least one mapped direct or related episode exists;
- `complete_zero_opportunity`: the complete classified corpus contains no
  mapped episode;
- `settled_zero_opportunity`: every relevant stable episode is classified and
  mapped, no mapped episode exists, and only active tails remain unread;
- `blocked_stable_backlog`: relevant stable transcript work remains unread;
- `blocked_classification`: one or more relevant stable episodes are
  unclassified or failed projection;
- `blocked_mapping`: a classified category could map to the capability but the
  exact mapping is missing, stale, or conflicting;
- `unavailable`: the opportunity root or receipt is invalid.

The coverage object binds the category IDs and aggregate counts that produced
the state. Global incomplete coverage never becomes per-capability zero unless
every unclassified or unmapped episode is proven irrelevant to that
capability.

`complete_zero_opportunity` is strictly stronger than
`settled_zero_opportunity`. The policy term
`decision_grade_zero_opportunity` means either state for the applicable
window. Every withdrawal or archive rule that accepts settled zero opportunity
also accepts complete zero opportunity.

For an unclassified episode, every capability with a `direct` or `related`
mapping to one of its `possible_category_ids` receives
`blocked_classification`. An
episode with valid `out_of_scope: true` blocks no capability. A missing or
invalid uncertainty projection makes relevance unknowable and blocks zero
opportunity for every capability rather than guessing.

### 7. Shape evaluation without exposing transcript content

Evaluation authoring receives:

- the exact skill contract;
- the existing contract-test templates;
- mapped category IDs and definitions;
- aggregate frequency and recency bands;
- synthetic example templates from the taxonomy;
- no transcript text, source path, usage count for the candidate, current
  recommendation, or user disposition.

The authored suite retains the existing intended, related,
activation-positive, and activation-negative cases. It may add at most three
observed-demand cases, selected by distinct-session count and recency with
canonical category identity as the tie-breaker.

Every observed-demand case is synthetic. Its manifest records the category ID
and opportunity-receipt identity that justified inclusion. The prompt may not
repeat source text or identify the skill. Existing deterministic privacy,
identity-leak, fixture, grader, review, and model-independence checks remain
mandatory.

An evaluation report distinguishes:

- `contract_coverage`: whether the skill performs its declared behavior;
- `observed_demand_coverage`: which observed categories were tested;
- `activation_coverage`: whether the skill loads when relevant and stays
  unloaded when unrelated.

A passing contract-only evaluation cannot claim observed portfolio value.

### 8. Ground skill creation

A new skill proposal may cite opportunity evidence when one category meets the
versioned creation threshold. The initial threshold is:

- at least three episodes;
- at least two distinct stable sessions;
- occurrences on at least two distinct days inside 30 days.

Explicit user intent or an explicit rare or safety-critical policy may create a
proposal without meeting the frequency threshold.

The proposal receives category definitions, aggregate facts, and synthetic
templates. It receives no transcript text. Evaluation of the proposed skill
uses the same category-bound synthetic cases. Creation evidence does not
authorize publication, promotion, or replacement of another skill.

## Decision policy

Opportunity modifies portfolio interpretation but remains separate evidence.
Hard safety precedence for critical regression, dependencies, pins, stale
identity, and explicit user authority remains unchanged.

The value rules are:

1. A current critical evaluation regression remains `disable_candidate`.
2. Current observed opportunity, current use, and a passing evaluation produce
   `proven_useful`.
3. Current observed opportunity, settled non-use, and a passing evaluation
   produce `activation_candidate`, not `proven_useful`.
4. Current observed opportunity and a failing evaluation produce
   `disable_candidate` or `merge_candidate` according to the evaluation.
5. Settled non-use plus decision-grade zero opportunity produces
   `withdraw_candidate` for an eligible machine-created personal skill when
   dependency, pin, provenance, dirty-work, restore, halt, and writer-lease
   predicates pass. Evaluation is not required.
6. A passing evaluation with decision-grade zero opportunity produces
   `capable_unneeded` unless rule 5 applies or explicit rare or
   safety-critical policy applies. Rule 5 takes precedence for eligible
   machine-created personal skills; a passing evaluation does not block their
   reversible withdrawal. `capable_unneeded` applies to every other skill class
   and to eligible skills whose withdrawal predicates fail. It does not create
   keep authority.
7. Missing, stale, blocked, or unavailable opportunity produces
   `opportunity_unknown`; opportunity then grants neither keep nor removal
   authority.
8. User-created skills remain recommendation-only without explicit user
   intent.
9. Plugin skills remain recommendation-only when the installed platform cannot
   disable one skill independently.
10. Archive still requires continuous withdrawal, fresh decision-grade 60-day
    non-use, fresh decision-grade 60-day zero opportunity, no dependency, no
    pin, and an exact Git-backed restore path. Decision-grade 60-day zero
    opportunity means complete or settled zero opportunity under a 60-day
    receipt and mapping.

Immediately before automatic withdrawal or archive,
`estate-action.py` acquires the writer lease and sends one fresh collection
request to the origin host. The request binds a random request identity, the
decision window end, target census identity, and required usage and opportunity
policy identities. The origin performs usage and opportunity collection and
returns receipts bound to that request. The owner accepts only receipts whose
request identity and window end exactly match the same-run request. An absent,
older, unrefreshed, or mismatched receipt yields `opportunity_unknown` and
refuses dispatch.

The owner validates the fresh origin receipt and derives a new decision; it
does not claim to collect origin transcripts locally. A new matching
opportunity, new use, stable backlog, classification failure, mapping conflict,
dependency, pin, disposition, or target change refuses dispatch.

## Schemas

### Opportunity receipt

```json
{
  "schema_version": 1,
  "kind": "task_opportunity_receipt",
  "receipt_id": "sha256:...",
  "request_id": "opaque random identity",
  "requested_window_end": "timestamp",
  "taxonomy_id": "sha256:...",
  "classifier_id": "sha256:...",
  "source_usage_receipt_sha256": "sha256:...",
  "collection_watermark": "opaque monotonic identity",
  "window_start": "timestamp",
  "window_end": "timestamp",
  "stable_episode_count": 14,
  "classified_episode_count": 13,
  "unclassified_episode_count": 1,
  "excluded_recent": {"count": 2, "bytes": 4096},
  "relevant_stable_backlog": {"count": 0, "bytes": 0},
  "failure_ids": [],
  "uncertainty": [
    {
      "episode_id": "sha256:...",
      "possible_category_ids": ["authenticated-api-read"],
      "out_of_scope": false
    }
  ],
  "categories": [
    {
      "category_id": "authenticated-api-read",
      "episode_count": 7,
      "distinct_session_count": 4,
      "distinct_day_count": 3,
      "completed_count": 5,
      "last_observed_at": "timestamp"
    }
  ]
}
```

### Capability opportunity coverage

```json
{
  "schema_version": 1,
  "kind": "capability_opportunity_coverage",
  "capability_id": "sha256:...",
  "candidate_id": "sha256:...",
  "mapping_id": "sha256:...",
  "opportunity_receipt_sha256": "sha256:...",
  "window_days": 30,
  "state": "observed_opportunity",
  "direct_category_ids": ["authenticated-api-read"],
  "related_category_ids": [],
  "episode_count": 7,
  "distinct_session_count": 4,
  "last_observed_at": "timestamp",
  "blockers": []
}
```

Portfolio decisions reference this coverage object beside, not inside, usage
and evaluation evidence.

## Failure model

| Failure | Required behavior |
| --- | --- |
| Active transcript tail | Exclude it explicitly and retain its count and bytes |
| Stable transcript unread or malformed | Block intersecting zero-opportunity windows |
| Episode boundary is ambiguous | Record projection failure; do not split or count guessed episodes |
| Episode matches no exact category | Record `unclassified`; do not guess |
| Taxonomy changes | Make prior mappings and current opportunity projections stale |
| Skill contract changes | Make its category mapping stale |
| Mapping is missing or conflicting | Produce `blocked_mapping`, never zero opportunity |
| Opportunity totals do not reconcile | Refuse the whole receipt |
| Receipt receiver or source identity is stale | Refuse the whole receipt |
| Synthetic evaluation prompt resembles source text | Reject the evaluation input before model execution |
| Passing evaluation covers no observed category | Report contract capability only |
| No opportunity with incomplete classification | Report unknown, never settled zero |
| Fresh opportunity appears before withdrawal | Recompute the decision and refuse dispatch |
| Opportunity feature is disabled | Retain receipts as history and remove their decision influence |

## Migration and rollout

1. Add the taxonomy, episode projection, opportunity receipt, and capability
   mapping in report-only mode.
2. Backfill at most 30 days from the existing stable transcript corpus under
   bounded file, byte, episode, and elapsed-time limits.
3. Render usage, opportunity, and evaluation as separate dashboard concepts.
4. Produce category-grounded evaluation previews without changing existing
   evaluation authority.
5. Compare two consecutive daily report-only portfolio decisions.
6. Enable opportunity influence for recommendation state only.
7. Enable automatic machine-created withdrawal only after installed, browser,
   halt, fresh-use race, rollback, and restore proof pass.
8. Keep archive and plugin mutation disabled until their existing independent
   maturity gates pass.

Prior decisions remain bound to their original policy. Migration appends new
opportunity-aware decisions and does not rewrite history.

## Deterministic check contract

### OPP-CHK-01: Shared settled-corpus boundary

- **Protects:** Usage and opportunity cannot disagree about which transcript
  bytes were decision-grade.
- **Setup:** Seed stable files, active tails, stable deferrals, parse failures,
  and changed-during-read files across a 30-day window.
- **Pass:** Usage and opportunity bind the same source receipt, watermark,
  exclusions, and intersecting blockers.
- **Failure signal:** Either lane accepts bytes the other classifies as
  non-decision-grade.
- **Why it proves the contract:** Zero opportunity is meaningful only over the
  same bounded corpus used for non-use.

### OPP-CHK-02: Episode identity and deduplication

- **Protects:** One conversation retry loop cannot inflate task demand.
- **Setup:** Provide one task with clarifications and retries, two independent
  tasks in one session, and one interrupted task.
- **Pass:** The projection emits three stable episode identities with one count
  per independent task.
- **Failure signal:** Turns or tool calls are counted as separate demand.
- **Why it proves the contract:** Portfolio frequency must represent tasks, not
  transcript verbosity.

### OPP-CHK-03: Candidate-blind classification

- **Protects:** A skill cannot manufacture evidence that its own task category
  occurred.
- **Setup:** Classify identical episodes while changing candidate skill names,
  usage, recommendations, and evaluation results.
- **Pass:** Category output and receipt identity remain identical.
- **Failure signal:** Candidate state changes classification.
- **Why it proves the contract:** Opportunity must be observed independently
  before it is mapped to a capability.

### OPP-CHK-04: Privacy projection

- **Protects:** Transcript content cannot enter receipts, dashboards, mappings,
  evaluation packets, or synthetic cases.
- **Setup:** Seed unique paths, credentials, private names, prompt phrases, and
  response phrases into transcripts.
- **Pass:** Only category IDs and bounded aggregate facts survive; every seeded
  sentinel is absent from public and model-facing outputs.
- **Failure signal:** Any sentinel appears outside the private origin
  projection boundary.
- **Why it proves the contract:** The feature may influence evaluation without
  republishing the evidence it classified.

### OPP-CHK-05: Classification and mapping uncertainty

- **Protects:** Unknown work cannot become no opportunity.
- **Setup:** Include unclassified episodes, stale taxonomy, missing mapping,
  conflicting mapping, unrelated fully mapped categories, valid possible
  categories, explicit out-of-scope proof, and a missing uncertainty
  projection.
- **Pass:** Valid possible categories block only mapped capabilities; explicit
  out-of-scope proof blocks none; missing or invalid uncertainty projection
  blocks zero opportunity for every capability.
- **Failure signal:** Unknown evidence becomes settled zero or blocks the whole
  estate despite a valid bounded relevance projection.
- **Why it proves the contract:** The system remains aggressive without
  guessing.

### OPP-CHK-06: Opportunity, usage, and evaluation matrix

- **Protects:** One evidence type cannot silently substitute for another.
- **Setup:** Exercise every row in the evidence table with complete current
  identities.
- **Pass:** Each row produces the specified interpretation and reason codes.
- **Failure signal:** A passing evaluation alone grants demand, non-use alone
  proves poor quality, or opportunity alone counts as skill use.
- **Why it proves the contract:** The table is the product contract for joining
  the three signals.

### OPP-CHK-07: Observed-demand evaluation grounding

- **Protects:** Evaluations represent actual task shapes without exposing
  transcripts or leaking candidate identity.
- **Setup:** Supply four mapped categories with different frequency and
  recency, including transcript sentinels.
- **Pass:** At most three canonical category-bound synthetic cases are added,
  selected deterministically, and contain no sentinel or skill marker.
- **Failure signal:** Raw text enters a case, category selection is unstable,
  or contract and activation cases disappear.
- **Why it proves the contract:** Observed demand improves relevance without
  weakening evaluation safety.

### OPP-CHK-08: Skill-creation grounding

- **Protects:** Dreaming does not create skills for one-off or hypothetical
  work without explicit authority.
- **Setup:** Provide categories below and above the creation threshold plus an
  explicit rare-critical intent.
- **Pass:** Only the threshold-qualified and explicitly authorized categories
  produce proposals, each with synthetic evidence.
- **Failure signal:** A one-off category creates a skill or raw transcript text
  enters a proposal.
- **Why it proves the contract:** Creation follows demonstrated demand while
  retaining a deliberate exception for rare critical work.

### OPP-CHK-09: Fresh pre-dispatch collection

- **Protects:** A new task opportunity cancels stale automatic withdrawal or
  archive authority.
- **Setup:** Authorize an eligible action, then add a matching stable episode
  before dispatch. Repeat with the origin unavailable and only an older receipt
  present.
- **Pass:** Fresh same-request collection appends a new decision and refuses
  the action; origin unavailability yields `opportunity_unknown` and refuses
  dispatch.
- **Failure signal:** The old or unrefreshed decision still dispatches.
- **Why it proves the contract:** Recommendations remain triggers for
  reconsideration, never reusable mutation tokens.

### OPP-CHK-10: Rollback

- **Protects:** Opportunity rollout is reversible without deleting evidence or
  changing installed skills.
- **Setup:** Disable opportunity collection and decision influence after
  report-only and action receipts exist.
- **Pass:** New opportunity work stops, historical receipts remain readable,
  portfolio decisions revert to the prior evidence policy, and skill roots,
  scheduler inventory, usage evidence, evaluation evidence, and action
  receipts remain byte-identical.
- **Failure signal:** Rollback deletes history, leaves opportunity authority
  active, or changes an unrelated root.
- **Why it proves the contract:** The critical privacy and authority boundary
  can be removed safely.

## Acceptance criteria

- Stable transcripts produce bounded, deduplicated task episodes under the
  existing settled-corpus boundary.
- Classification is candidate-blind and uses a content-addressed taxonomy.
- Public, cross-machine, dashboard, portfolio, and model-facing outputs contain
  no transcript content or source-local paths.
- Unclassified episodes and stale or conflicting mappings block zero
  opportunity only where relevant.
- Usage, opportunity, and evaluation remain separate evidence in schemas, API,
  UI, and policy.
- Evaluation suites retain contract and activation cases and add no more than
  three deterministic category-grounded synthetic cases.
- A passing evaluation with no observed opportunity cannot create keep
  authority.
- Eligible machine-created settled-zero skills may withdraw without
  evaluation; user-created and unsupported plugin skills remain governed by
  their existing authority boundaries.
- Skill-creation proposals require repeated observed demand, explicit user
  intent, or explicit rare or safety-critical policy.
- Fresh pre-dispatch collection cancels stale action authority.
- Rollback disables opportunity influence while preserving all retained
  evidence and unrelated installed state.

## Definition of Done: Task opportunity evidence

- [ ] A content-addressed capability-neutral taxonomy and bounded origin-host
      task-episode projection are implemented.
- [ ] Opportunity receipts bind the existing settled-corpus receipt and
      collection watermark without retaining transcript content.
- [ ] Exact reviewed category-to-capability mappings are separate from task
      classification and become stale on skill or taxonomy changes.
- [ ] Per-capability opportunity coverage distinguishes observed, complete
      zero, settled zero, blocked backlog, blocked classification, blocked
      mapping, and unavailable states.
- [ ] Portfolio decisions retain separate usage, opportunity, evaluation,
      dependency, pin, provenance, and user-authority evidence.
- [ ] Category-grounded evaluation authoring adds only bounded synthetic cases
      and preserves contract and activation coverage.
- [ ] Skill-creation proposals are grounded in repeated opportunity evidence or
      explicit authority without exposing transcript content.
- [ ] Automatic machine-created withdrawal no longer requires evaluation when
      settled non-use, decision-grade zero opportunity, dependency, pin,
      provenance, dirty-work, restore, halt, writer-lease, and
      fresh-collection predicates pass.
- [ ] OPP-CHK-01 through OPP-CHK-10 pass.
- [ ] Report-only desktop and narrow browser proof shows usage, opportunity,
      and evaluation as distinct concepts.
- [ ] Installed proof covers bounded collection, candidate-blind
      classification, fresh-opportunity cancellation, halt, and sole-scheduler
      ownership.
- [ ] Rollback preserves opportunity, usage, evaluation, decision, action, and
      restore evidence while removing opportunity influence.
- [ ] Paired design review has no unresolved material finding.
- [ ] The reviewed design and proof references are committed locally; nothing
      is pushed.
