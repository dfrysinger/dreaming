# Conservative skill lifecycle and portfolio evaluation

## Objective

Make autonomous Dreaming publish only recently recurring, behaviorally useful
skills whose measured benefit exceeds their discovery and portfolio cost, then
withdraw and recoverably archive skills that no longer justify occupying agent
context.

## Lane

Critical.

This change alters durable admission state, evaluation authority, publication
membership, scheduled maintenance, and recoverable retirement. A false pass can
publish a harmful or distracting skill. A false retirement can remove a rare
but valuable procedure from normal discovery. The design therefore includes an
explicit rollback path and fail-closed evidence.

## Non-goals

- Build a hosted benchmark service, shared dataset service, model leaderboard,
  multi-user approval system, or general experimentation platform.
- Replace the existing cross-CLI trial harness, evaluation authority, evidence
  envelope, writer lease, publisher adapters, curator transactions, Git-backed
  archive, tombstones, or restore path.
- Publish benchmark prompts, retained session text, private repository content,
  provider credentials, or local evaluation artifacts.
- Adopt the schema, identity algorithm, prompt text, code, or private design of
  another proposal or skill-generation system. Compatibility is limited to
  opaque aliases and a future adapter boundary.
- Treat model families, model tiers, CLIs, or context-window sizes as
  interchangeable without measured evidence.
- Make one composite score authoritative. Routing, task value, and portfolio
  cost remain separate gates.
- Automatically mutate or archive hand-made skills. They remain
  recommendation-only under the existing curator authority.
- Remove the explicit approval required for live curator consolidation and
  pruning.
- Create model-specific installed catalogs when a publisher exposes only one
  catalog per CLI. Certification is recorded per configured model, but
  publication remains bounded by the publisher's actual capability.
- Make benchmark wall-clock duration authoritative in the first release.
  Duration is retained for diagnosis; success, turns, tokens, and tool use are
  the behavioral efficiency measures.
- Generalize the lifecycle beyond Dreaming's supported local macOS runtime and
  its configured Copilot CLI, Claude Code, and Codex publishers.

## User outcome

The owner can answer these questions from retained state and the dashboard:

1. Which repeated independent tasks justify each autonomous skill?
2. Which candidates are still waiting for recurrence, evaluation, or the next
   weekly portfolio run?
3. Does each skill activate when needed and stay out of unrelated tasks?
4. Does each skill improve task success, turns, tokens, or tool use?
5. How does the approved library perform against both a zero-skill anchor and
   its previous production version?
6. Which model results are direct, which are trusted proxies, and which are
   stale or untested?
7. Why was a skill published, withdrawn, quarantined, consolidated, archived,
   or restored?
8. Can the last known good catalog be restored without deleting evidence?

## Delivery sequence

### Immediate containment milestone

The first implementation slice stops new autonomous skill growth without
waiting for candidate storage, portfolio benchmarks, or expanded evaluation.

Every autonomous review path obeys the same containment boundary. The
scheduled core applies two deterministic rules:

1. A review result from a session whose source `updated_at` is more than 30
   days old may be analyzed, but every `create`, `patch`, or `support_file`
   result is converted to a non-mutating `discard` result with
   `policy_deferred: "historical-source-outside-mutation-window"`.
2. A fresh review result whose artifact operation is `create` is converted to
   a non-mutating `discard` result with
   `policy_deferred: "autonomous-create-requires-recurrence"`.

The original structured executor result remains in local result state for both
rules, so the future candidate importer can recover the proposed name, package,
evidence anchors, occurrence time, and source identity. The review ledger
records the deferred policy outcome and marks the queue item complete, so the
same session does not retry indefinitely.

Fresh `patch` and `support_file` operations against an existing skill remain
available through their existing draft-review and evidence path. Explicit
foreground skill creation remains unchanged.

The legacy end-of-task `dispatch` trigger is paused for this milestone because
it writes skills directly rather than passing through the scheduled core's
deterministic boundary. The autonomous skill contract also forbids create-new
actions and applies the same 30-day mutation window to direct sweep or dispatch
inspection. Dispatch may return only after it is routed through the
authoritative candidate admission boundary.

This slice does not:

- create the candidate registry;
- evaluate recurrence;
- change publisher inventory or withdraw an installed skill;
- enable automatic consolidation or archive;
- change the cross-CLI evaluation suite.

Both temporary transformations remain active throughout the shadow milestone.
Shadow candidate collection is additive to the deferred-discard result and
ledger row; it does not grant create authority or relax
`allow_autonomous_skill_creation`. A later enforcement milestone may supersede
IC-02 and IC-CHK-02 only after the candidate registry becomes authoritative.
Fresh create results may then enter `collecting` as their terminal policy
outcome, while old results become historical support that cannot independently
open or admit a candidate.

#### Immediate containment acceptance

- **IC-01:** A queued source older than 30 days may produce a structured local
  result, but every artifact operation completes as a deferred discard without
  a skill write, draft review, or Git commit.
- **IC-02:** A fresh executor `create` result completes as a deferred discard,
  retains the original local result file, and creates no skill directory or
  Git commit.
- **IC-03:** A fresh valid patch or support-file result still follows the
  existing draft review, evidence, commit, and ledger path.
- **IC-04:** Explicit foreground `/skill-create` behavior is unchanged.
- **IC-05:** Scheduled publication continues to serve the existing verified
  bundle and does not publish a deferred proposal.
- **IC-06:** Installed instructions do not launch autonomous end-of-task
  dispatch, and no autonomous skill-review mode authorizes direct creation.

#### Immediate containment check contract

- **IC-CHK-01:** Give the core old queued sessions whose fake executor returns
  create, patch, and support-file operations. Pass requires one retained result
  per session, queue completion, deferred-discard ledger rows, and zero draft
  reviews, skill changes, and commits. Any mutation or missing original result
  proves the age boundary discarded evidence or failed to contain writes.
- **IC-CHK-02:** Return one valid fresh create artifact from the fake executor.
  Pass requires a deferred-discard attempt and ledger row, the unchanged local
  structured result file, an empty skill root, and unchanged Git `HEAD`. A
  retry, draft review, skill directory, or commit proves create authority
  escaped containment.
- **IC-CHK-03:** Run the existing patch and support-file fixtures inside the
  30-day window. Pass requires their existing commits and evidence. Failure
  proves the containment rule blocked supported reuse rather than only new
  growth.
- **IC-CHK-04:** Run the existing foreground creation tests unchanged. Their
  pass proves the scheduled-core policy did not alter explicit user authority.
- **IC-CHK-05:** Run an installed scheduled canary containing one old source
  and one fresh create proposal. Pass requires both non-mutating outcomes,
  unchanged managed-skill `HEAD`, and unchanged verified publisher bundle IDs.
- **IC-CHK-06:** Verify the installed managed instructions explicitly pause
  end-of-task dispatch and reject any configured attempt to enable autonomous
  creation or change the 30-day mutation window.

#### Definition of Done: Conservative admission containment

- [ ] Scheduled sessions older than 30 days retain structured review results
      but cannot mutate skills.
- [ ] Fresh autonomous create proposals are durably recorded and deferred
      without skill files, draft review, commits, or publisher changes.
- [ ] Fresh patch and support-file behavior remains intact.
- [ ] Foreground user-authorized creation remains intact.
- [ ] Targeted core, daemon, installer, and self-test coverage passes.
- [ ] An installed scheduled canary proves managed-skill and publisher state
      remain unchanged.
- [ ] Installed instructions pause direct autonomous end-of-task mutation, and
      configured settings cannot relax the containment invariants.
- [ ] Implementation review has no verified in-scope material finding.

### Shadow candidate admission and evaluation milestone

This milestone can proceed while production activation remains gated. It
builds and proves the next three layers in dependency order without granting
publication, mutation, retirement, or scheduled-production authority:

1. **Candidate and lifecycle state.** Add the authoritative record and package
   storage, legal state transitions, stable lifecycle identity, exact revision
   identity, duplicate decisions, expiration, and read-only dashboard views.
2. **Recurrence-based admission.** Route fresh autonomous create findings into
   candidate collection, join matching independent observations, and compute
   `ready_for_draft` eligibility. Every decision remains shadow-only.
3. **Routing and task-value evaluation.** Extend the existing cross-CLI trial
   harness with positive, close-negative, unrelated, and conflict routing
   cases plus paired task-success and efficiency treatments. Evaluation may
   record evidence and recommendations but cannot authorize publication.

The first layer must land before recurrence because recurrence decisions need
a stable record and legal transition owner. Recurrence must land before
candidate evaluation because the evaluator needs an exact, admitted-for-test
revision tied to one lifecycle record. Throughout this milestone, candidate
collection is an additive shadow side effect of the existing fresh-create
deferred-discard path. IC-02 and IC-CHK-02 remain live regression checks, the
deferred ledger row remains the terminal scheduled-core outcome, and configured
attempts to enable `allow_autonomous_skill_creation` remain rejected.

Production activation is a separate operational gate. The installed
generation remains halted and uncertified until the supported generation-bound
self-test reports exactly `== result: 0 failure(s) ==`. Shadow development
uses deterministic fixtures and focused model trials only. It does not run the
six-hour installed self-test, enable LaunchAgents, or change publisher state.

This milestone does not:

- create zero-skill or production portfolio baselines;
- run weekly complete-library benchmarks or model-proxy calibration;
- import the existing installed skill library;
- publish, withdraw, quarantine, consolidate, archive, or restore a skill;
- remove the halt switch or certify the installed generation;
- permit a candidate recommendation to become mutation authority.

#### Shadow milestone acceptance

- **SH-AC-01:** A valid fresh autonomous create finding creates or updates one
  candidate lifecycle record and immutable draft package outside every skill
  discovery and publisher root, while managed-skill Git state and publisher
  bundle identities remain unchanged.
- **SH-AC-02:** Candidate records reject malformed content, unknown states,
  illegal transitions, mismatched exact revision identities, and writes
  without the shared writer lease.
- **SH-AC-03:** Matching independent fresh observations append to one stable
  lifecycle record and produce a shadow `ready_for_draft` recommendation only
  when every recurrence condition passes.
- **SH-AC-04:** One observation, repeated evidence from one task or session,
  old-only evidence, uncertain matching, an existing covering lifecycle, or a
  tombstone cannot produce a ready recommendation.
- **SH-AC-05:** Expiration, reopening, duplicate, supersession, and absorption
  decisions retain append-only evidence and transition history without
  changing a published inventory.
- **SH-AC-06:** The dashboard truthfully distinguishes collecting,
  `ready_for_draft`, expired, rejected, and evaluation states; separately
  exposes duplicate, same, uncertain, supersession, and absorption decisions;
  and labels every decision as shadow-only.
- **SH-AC-07:** Routing trials report positive recall, close-negative and
  unrelated false-load rates, conflict selection, and critical-case failures
  for the exact candidate and environment identity.
- **SH-AC-08:** Paired task-value trials report success, turns, tokens, and tool
  use for candidate and control treatments, reject incomplete or mismatched
  arms, and preserve separate gate results rather than one composite score.
- **SH-AC-09:** Candidate or environment drift makes routing and task-value
  evidence stale. Missing real-backend evidence remains `inconclusive`, not a
  pass.
- **SH-AC-10:** No shadow state, recommendation, or evaluation receipt can
  change managed skills, approved inventories, publishers, scheduled
  activation, quarantine, or retirement state.

#### Shadow milestone check contract

- **SH-CHK-01: Candidate authority and isolation.** Feed the core a fresh
  structured create result. Pass requires one valid record and package under
  the configured candidate roots, unchanged managed-skill `HEAD`, unchanged
  approved bundle pointers, and no native discovery. Any skill-root write or
  publisher change proves shadow authority escaped.
- **SH-CHK-02: State-machine fail closure.** Exercise every declared transition
  plus malformed records, unknown states, stale writes, identity mismatch, and
  missing lease ownership. Pass requires legal transitions to retain their
  complete history and every invalid operation to leave the prior record byte
  identity unchanged.
- **SH-CHK-03: Recurrence matrix.** Exercise two independent recent tasks, one
  repeated task, one repeated session, old-only support, a mixed old and fresh
  pair no more than 45 days apart, another qualifying pair more than 45 days
  apart, uncertain matching, a covering active record, and a tombstone. Pass
  requires only the two-independent-recent case and the mixed pair within the
  45-day spread to recommend `ready_for_draft`.
- **SH-CHK-04: Lifecycle retention.** Expire and reopen a candidate, then model
  duplicate, supersession, and absorption decisions. Pass requires all evidence
  and transition reasons to remain reachable and no publication mutation.
- **SH-CHK-05: Dashboard reader.** Render fixture records for each declared
  shadow lifecycle state and separate fixtures for same, duplicate, uncertain,
  supersession, and absorption decisions. Pass requires truthful labels,
  reasons, evidence counts, freshness, and an explicit non-authoritative
  marker. Missing or malformed state must be shown as unavailable or invalid
  rather than inferred as healthy.
- **SH-CHK-06: Routing matrix.** Run sealed positive, close-negative, unrelated,
  and conflict fixtures through the existing trial harness with a sealed
  snapshot of the relevant approved target catalog plus the candidate.
  Candidate-only routing remains diagnostic and cannot certify this check.
  Pass requires exact load evidence, per-case results, critical-case fail
  closure, and candidate, catalog, and environment binding.
- **SH-CHK-07: Paired task value.** Run complete candidate and control arms plus
  missing-arm, mismatched-scenario, over-budget, stale-identity, and
  missing-real-backend fixtures. Pass requires every complete receipt to bind
  backend execution identity and report separate success, turns, tokens, and
  tool-use measures for both treatments. Missing real-backend evidence is
  `inconclusive`; other invalid evidence is `inconclusive`, stale, or a
  regression according to its failure class.
- **SH-CHK-08: Mutation boundary.** Snapshot managed-skill Git state, approved
  inventory pointers, publisher state, LaunchAgent state, halt state, and
  production quarantine and retirement state before the complete shadow flow.
  Pass requires every snapshot to remain unchanged after candidate collection,
  recurrence, and evaluation, including an evaluation failure that attempts
  but cannot invoke a production quarantine transition.

#### Definition of Done: Shadow candidate admission and evaluation

- [x] Candidate records and isolated immutable packages implement stable
      lifecycle identity, exact revision identity, legal transitions,
      expiration, duplicate decisions, supersession, and absorption.
- [x] Fresh autonomous create findings enter candidate collection without
      skill, Git, approved-inventory, publisher, LaunchAgent, or retirement
      mutation.
- [x] Recent independent recurrence produces only a shadow
      `ready_for_draft` recommendation, and every insufficient-evidence case
      remains `collecting`, `expired`, or `rejected` while any same, duplicate,
      uncertain, supersession, or absorption outcome remains a separate
      recorded decision rather than an undeclared lifecycle state.
- [x] Read-only dashboard views expose candidate, recurrence, lifecycle, and
      evaluation state with explicit shadow-only authority.
- [x] The existing cross-CLI harness records exact-identity routing and paired
      task-value evidence, including success, turns, tokens, and tool use.
- [x] Missing, incomplete, mismatched, stale, or over-budget evidence fails
      closed as `inconclusive`, stale, or regression.
- [x] Deterministic checks prove the full shadow flow cannot change managed
      skills, approved inventories, publishers, activation, quarantine, or
      retirement state.
- [x] Focused real-model trials cover representative positive, close-negative,
      unrelated, conflict, candidate, and control cases without running the
      complete portfolio matrix.
- [x] The implementation passes its targeted deterministic suites, critical
      live proof, and required implementation review.
- [x] The installed generation remains halted throughout this milestone; this
      run does not certify the generation, write an activation receipt, remove
      the halt switch, or enable a LaunchAgent.

#### Shadow milestone completion evidence

Completed on 2026-08-11 on branch `feature/multi-cli-dreaming`, with the
installed generation still halted and no production authority granted.

- Final deterministic totals: candidate lifecycle `10`, core `32`, dashboard
  contracts `54`, dashboard integration `102`, skill evaluation `23`,
  evaluation harness `31`, vendor adapters `12`, and mutation boundary `8`.
  Process cleanup, Python compilation, and shell syntax checks also passed.
- The final real Codex `gpt-5.4-mini` run passed separate routing and task-value
  gates. Candidate:
  `sha256:05de0b79c5ad6d20d878af469b4f5d2755720bd0a6eb453d8a101de9ff689d2f`;
  catalog:
  `sha256:6d8c7b5bd73b40ade82fc3ea5bf86593f8fec87653bc918b825ac14f85a3f996`;
  run:
  `sha256:6216ef06ec0e2ddc75866cd03ca48a18172d96cd729ede14f176115baf2c82ab`;
  result:
  `sha256:0af389858e5e0f98f2551429fbcee3c663b2fd61cb868e01ed9566f6bf9c4a90`;
  authoritative receipt basename:
  `46fa118a2098d0ee7350b41d1168c69bb0c138d3ef0e36aa9d7f6c6636d9ceea`.
- Independent final judges ruled `SC-016` and `SC-018` `SUPPORTED`. The
  original Generation 10b receipt remains historical evidence but is stale
  for landing because executable behavior changed afterward.
- Round-one review findings were repaired: non-ASCII canonicalization,
  candidate-root isolation, complete route sealing, receipt result binding,
  exact same-model executor resolution, multi-skill Codex read attestation,
  and task-value catalog semantics. The scheduled-recurrence claim was dropped
  because scheduled observations intentionally remain `unverified` until a
  trusted independent task identity exists.
- Round two resolved every prior finding. One bounded evidence-integrity gap
  remained: `candidate_loaded` was trusted rather than re-derived during
  result verification. The verifier now derives it from sealed load evidence;
  both reviewer families closed the finding with no regression.
- Real Chrome proof covers the read-only candidate list and detail surfaces,
  including explicit shadow-only authority and the absence of mutation
  controls.

## Reuse contract

This work extends existing owners. It does not create a second learning or
evaluation stack.

| Concern | Existing owner to reuse | Required extension |
| --- | --- | --- |
| Session discovery and review | `dreaming-core.py`, source adapters, review ledger, review transactions | Route autonomous skill findings into candidate state instead of writing directly to the published skill root |
| Independent evidence | `evidence-envelope.py`, opaque task keys, exact transcript anchors | Apply evidence strength and recency as deterministic admission gates |
| Exact draft identity | `skill-evaluation.py` candidate inventory hash | Keep this content hash as the exact revision identity, separate from stable lifecycle identity |
| Behavioral trials | `skill-evaluation-harness.py`, native executor adapters, sealed receipts, deterministic graders | Add approved-bundle treatments, efficiency measurements, environment fingerprints, and cached benchmark reuse |
| Cross-CLI authority | `skill-evaluation.py` required and advisory executor certificates | Add routing, efficacy, portfolio, and proxy-currency decisions without pooling provider scores |
| Publication | `dreaming-core.py` content-addressed bundles and publisher ownership journal | Materialize a target's approved inventory rather than every directory in the skills root |
| Usage | Normalized native skill-load events and `skill-usage-report.sh` | Accept only proved loads; represent unavailable target telemetry as unknown |
| Curation | `skill-curator`, scheduled dependency scanner, curator transactions | Use portfolio pressure and marginal utility to prioritize dry-run consolidation and pruning |
| Retirement | `archive-skill.sh`, retirement records, tombstones, Git history, `restore-skill.sh` | Add lifecycle transition reasons and prevent unapproved recreation |
| Scheduling | `dreaming-run.sh`, cadence state, run manifests | Add bounded candidate evaluation and weekly portfolio certification to the existing daily owner |
| Presentation | `dreaming-dashboard.py` and the existing dashboard application | Show candidate, evaluation, baseline, proxy, publication, and lifecycle state without becoming an authority |

Two new local state helpers are required:

1. `candidate-lifecycle.py` owns candidate records and legal transitions. The
   existing evidence envelope describes support for an artifact that already
   exists; it cannot represent a non-published proposal or its expiration.
2. `skill-benchmark.py` owns versioned benchmark suites, environment
   fingerprints, immutable baseline pointers, cache lookup, portfolio budgets,
   and model-proxy calibration. Candidate receipts cannot represent a
   zero-skill or complete-bundle historical baseline.

Both helpers use the existing shared writer lease, atomic JSON writes, halt
switch, configured roots, and content-addressed receipt conventions. Neither
helper invokes a model or mutates a published catalog directly.

## Authority and identity model

### Stable lifecycle identity

The first accepted observation mints a random opaque UUID called
`lifecycle_id`. It remains stable through candidate revisions, admission,
publication, consolidation, withdrawal, and archive.

The lifecycle ID is not derived from task text, skill name, repository, model
output, or a semantic fingerprint. This prevents private content from entering
identifiers and prevents wording changes from creating a different identity.

### Exact candidate revision identity

The existing evaluator continues to derive `candidate_id` from the complete
staged skill inventory. Any content change creates a new candidate ID and makes
the previous evaluation stale.

One lifecycle record may therefore reference many candidate IDs over time:

```text
lifecycle_id
  candidate_id revision 1
  candidate_id revision 2
  admitted candidate_id
  active skill content identity
```

### Matching metadata

Each candidate stores a structured procedure descriptor containing:

- trigger conditions;
- intended outcome;
- ordered action summary;
- explicit exclusions;
- proposed artifact class and name;
- a versioned normalized match fingerprint.

The fingerprint is a search hint, not identity or admission authority. A model
may propose that two observations match, but deterministic code verifies the
evidence and records an explicit `same`, `different`, or `uncertain` decision.
Uncertain observations may remain separate unpublished candidates and are
eligible for later consolidation.

### External aliases

A lifecycle record may carry namespaced opaque aliases:

```json
{
  "namespace": "external-system",
  "value": "opaque-proposal-id"
}
```

Aliases do not affect admission, matching, evaluation, or publication. An
adapter may add them later without changing Dreaming's public schema or core
identity rules.

## Candidate and lifecycle state

### Storage

Candidate authority lives under the configured Dreaming state root:

```text
skill-review/candidates/v1/records/<lifecycle-id>.json
```

Exact immutable draft packages live under the configured Dreaming data root:

```text
candidates/v1/packages/<lifecycle-id>/<candidate-id>/
```

Draft packages never live under `DREAMING_SKILLS_ROOT`, never appear in a
publisher bundle, and never enter native skill discovery.

### Record shape

Each lifecycle record contains at least:

```json
{
  "schema_version": 1,
  "lifecycle_id": "opaque-uuid",
  "state": "collecting",
  "authority": "autonomous",
  "proposed_name": "example-skill",
  "procedure": {
    "schema_version": 1,
    "trigger": "plain description",
    "outcome": "plain description",
    "actions": ["bounded action"],
    "exclusions": ["nearby task that is not covered"],
    "match_fingerprint": "sha256:..."
  },
  "evidence": [],
  "candidate_revisions": [],
  "current_candidate_id": null,
  "evaluation": {},
  "publication": {},
  "lifecycle": {
    "created_at": "timestamp",
    "last_supported_at": "timestamp",
    "expires_at": "timestamp",
    "transition_history": []
  },
  "aliases": [],
  "absorbed_into": null
}
```

Every transition records the prior state, next state, timestamp, reason code,
and receipt or evidence identities that authorized it. Transition history is
append-only within the atomically replaced record.

### Candidate states

| State | Meaning | Discovery and publication |
| --- | --- | --- |
| `collecting` | One recent autonomous observation exists but recurrence is insufficient | Never visible to CLIs |
| `legacy_probation` | A pre-enforcement agent-created skill is retained while recurrence, adoption, and evaluation authority are established | Excluded from approved inventories unless explicitly adopted |
| `ready_for_draft` | Recurrence and independence gates pass | Never visible to CLIs |
| `evaluating` | An exact staged draft is undergoing candidate evaluation | Never visible to CLIs |
| `portfolio_pending` | Candidate-specific gates pass and the draft awaits the weekly bundle comparison | Never visible to CLIs |
| `admitted` | The exact revision passed the portfolio gate and became an active managed skill | Included only in approved target inventories |
| `expired` | Recurrence did not arrive before candidate expiration | Never visible to CLIs |
| `rejected` | Evaluation or policy showed that the procedure should not become a skill | Never visible to CLIs |
| `quarantined` | Evidence, evaluation, content, dependency, or production behavior is untrustworthy | Withdrawn from all target inventories |
| `absorbed` | Its support and history moved to another lifecycle record | Never recreated independently while the absorption remains valid |
| `archived` | The admitted skill was recoverably removed through Git-backed retirement | Not visible; restorable |

`admitted`, `quarantined`, and `archived` describe the managed skill after
admission. The same record survives the transition so evidence and proposal
history are not split between unrelated identities.

`legacy_probation` may transition to `ready_for_draft` after qualifying recent
recurrence, to `evaluating` after explicit user adoption supplies recurrence
authority, to `quarantined` when imported content is invalid or regressing, and
to `archived` only after the normal withdrawal period and approved curator
transaction. Unknown states and undeclared transitions are rejected.

Age alone never moves `legacy_probation` to quarantine or archive while
required-target usage is partial or unknown. After 90 days without adoption,
recurrence, or evaluation, it becomes an explicit curator recommendation. The
existing approval transaction, dependency checks, and recoverable archive are
still required, which prevents a delayed bulk archive after migration.

## Admission policy

### Autonomous observations

The first qualifying autonomous observation creates a `collecting` candidate,
not a skill, only when its evidence occurrence is no more than 30 days old.
Dreaming may retain an older observation as historical support, but old
evidence cannot independently open a candidate.

A candidate reaches `ready_for_draft` only when all conditions are true:

1. At least two evidence entries have `independence: "verified"`.
2. Those entries contain at least two distinct opaque task keys.
3. They come from at least two distinct source sessions.
4. They support the same reusable procedure, not merely the same project or
   topic.
5. At least one qualifying occurrence is no more than 30 days old.
6. The oldest and newest qualifying occurrences are no more than 45 days
   apart.
7. No active, collecting, rejected, quarantined, absorbed, or archived
   lifecycle record or tombstone already covers the procedure without an
   explicit merge, supersession, or reopening decision.
8. The procedure has a bounded trigger, ordered behavior, observable stopping
   condition, and clear exclusions.

Repeated mentions from one task do not increase evidence strength. Historical
sessions with unverified task independence do not satisfy the quorum.

A collecting candidate expires after 30 days without new verified support.
Expiration retains the record and evidence. A later fresh observation may
reopen it, but the normal admission quorum and recurrence window are evaluated
again from qualifying evidence.

### Explicit user authority

An explicit foreground request to create a skill may set
`authority: "user_authorized"` and bypass autonomous recurrence. It does not
bypass:

- structural validation;
- private-content review;
- candidate-specific behavioral evaluation when Dreaming will publish it;
- target publication policy;
- portfolio accounting.

Hand-made skills outside Dreaming's managed root remain outside autonomous
mutation. Pins protect a skill from retirement; they do not fabricate
evaluation evidence or publication authority.

### Duplicate, supersession, and absorption decisions

Before creating a new lifecycle record, Dreaming searches active, collecting,
expired, rejected, quarantined, absorbed, and archived records plus
tombstones.

- `same` appends evidence to the existing lifecycle record.
- `different` may create another collecting candidate.
- `uncertain` creates no published artifact and records a manual or later
  consolidation question.
- `rejected` appends matching evidence but remains rejected. It may reopen only
  through explicit user action or a materially changed procedure descriptor
  with new qualifying recurrence and a recorded reason that the prior
  rejection no longer applies.
- `supersedes` preserves both histories, withdraws the older skill only after
  the replacement passes, and records the replacement lifecycle ID.
- `absorbs` transfers every evidence reference and alias to the surviving
  umbrella before the source can be archived.

A semantic model decision never deletes evidence or bypasses evaluation.

## Candidate evaluation

The existing cross-CLI evaluator remains the execution and evidence authority.
This design extends its versioned suite and policy rather than replacing it.

### Three independent gates

Every autonomous candidate must pass:

1. **Routing quality:** the description causes discovery and loading on the
   right tasks and avoids incorrect or conflicting loads.
2. **Task value:** using the skill improves correctness, task success, policy
   conformance, turns, tokens, or tool use without a related-task regression.
3. **Portfolio cost:** adding the candidate to the approved library does not
   move unrelated work or total catalog cost outside the configured budget.

No weighted average can turn a failure in one gate into an overall pass.

### Skill value classes

Each candidate declares one value class:

| Class | Primary false-negative cost | Required evidence |
| --- | --- | --- |
| `correctness_required` | A relevant task becomes incorrect, unsafe, or policy-violating | Every critical positive case must pass; no critical related or negative case may regress |
| `capability_uplift` | A task that commonly fails remains unsolved | Candidate success must exceed control success |
| `efficiency_uplift` | A solvable task uses avoidable turns, tokens, or tools | Success must remain non-inferior and at least one configured efficiency measure must improve |
| `preference_conformance` | The task succeeds through the wrong workflow or output contract | Candidate must satisfy the encoded preference and win the existing blind comparison gate |

False positives and false negatives are therefore costed by the behavior they
change. A missed correctness skill can be worse than an unnecessary load. A
broad efficiency skill loaded on many unrelated tasks can impose the larger
aggregate cost.

### Routing cases

The suite contains:

- positive cases where the skill should load;
- close negative cases using similar language where it should not load;
- unrelated controls;
- conflict cases where another approved skill is the correct choice.

Cases may be marked `critical`. A critical positive miss or critical negative
load is a hard regression. Non-critical cases report precision, recall,
false-load rate, missed-load rate, and conflict rate. The policy defines
minimums by value class rather than one universal precision-versus-recall
weight.

Routing admission is tested with the complete approved target catalog plus the
candidate. Candidate-only activation remains available for diagnosis but
cannot certify catalog routing.

The admission receipt binds the catalog used for that decision, but later
bundle changes do not invalidate every admitted skill independently. The
weekly portfolio routing certificate binds the complete active bundle and
re-establishes catalog-wide routing authority for every included lifecycle
revision. A changed bundle invalidates that bundle-level certificate and must
produce a new one before additions activate.

Exact admitted-skill task-value receipts remain immutable admission evidence.
They become `recertification_due`, not an automatic publication regression,
when their time-to-live expires without a content, model, or observed behavior
change. Weekly maintenance rotates due active skills through direct
candidate-specific reevaluation. An observed regression or changed exact
candidate invalidates authority immediately; simple age alone freezes further
additions until the due evaluation is refreshed but does not withdraw an
otherwise healthy skill.

### Efficacy and efficiency cases

The harness supports these exact treatments:

1. `zero`: no non-built-in skills;
2. `production`: the current approved target bundle;
3. `proposed`: the production bundle plus the exact staged candidate batch;
4. `candidate_only`: the exact candidate as the only non-built-in skill,
   used only when isolation is needed for attribution.

Candidate-specific intended and related cases compare matched arms with the
same CLI, exact model, task input, working-state snapshot, tool policy,
timeout, context profile, grader set, and trial number. Arm order remains
alternated.

Every trial retains:

- deterministic task success and safety results;
- skill-load proof;
- input, output, and total tokens when available;
- model-visible turns;
- tool-call count and forbidden-tool evidence;
- final-state artifacts and normalized trace;
- elapsed duration as non-authoritative diagnostic data;
- exact candidate, bundle, environment, suite, and policy identity.

An unavailable metric is `unknown`, not zero. A provider that cannot expose an
authoritative metric cannot satisfy a gate that requires that metric.

### Scenario construction

Candidate evaluation uses:

- exact source-derived cases;
- independently authored sibling cases;
- close negative and conflict cases;
- holdout cases not used while editing the candidate.

Source transcript text is not copied into public files. Local cases contain
only the minimum task input required to reproduce the behavioral claim.
Replaying one source task alone cannot authorize a skill.

## Environment fingerprints and cache currency

### Observable environment identity

Every retained result binds an `environment_fingerprint` containing:

- exact configured model identifier;
- CLI, adapter, harness, comparator, grader, and tool-policy identities;
- known system and user instruction input digests;
- skill bundle ID;
- benchmark suite and case IDs;
- context-window limit reported by the target, when available;
- controlled starting context profile and token load;
- model parameters exposed by the target;
- fixture or repository snapshot identity;
- relevant operating-system and executable identities;
- a visibility declaration for system inputs the target does not expose.

The fingerprint distinguishes `complete`, `partial`, and `opaque` fields. A
hash over visible inputs does not claim that provider-side model weights,
hidden prompts, routing, or service configuration are unchanged.

### Context profiles

The benchmark defines at least:

- `normal`: representative starting context load;
- `high_pressure`: a controlled context load near the system's normal
  compaction or truncation region.

The context profile and measured starting tokens are bound into the result.
Results from one profile do not certify another.

### Cache rules

A cached result is reusable only when all content-addressed inputs and the
visible environment fingerprint match and its time-to-live has not expired.

Default validity:

- staged-candidate routing and task-value result before admission: 30 days;
- admitted-skill task-value receipt: immutable admission evidence, with direct
  recertification due within 90 days;
- zero-skill anchor on an immutable model snapshot: 90 days;
- zero-skill anchor on a moving model alias or partial environment: 30 days;
- production and proposed portfolio result: the current weekly cadence bucket;
- proxy calibration: until either model identity, target environment,
  benchmark suite, or calibration policy changes, with a maximum age of 90
  days.

Any candidate, suite, policy, model, CLI, adapter, harness, grader, tool,
context-profile, fixture, or known instruction change invalidates the
dependent candidate result immediately. A bundle change invalidates only
bundle-level routing and portfolio results. It does not invalidate unchanged
candidate-specific task-value evidence.

## Portfolio benchmark and baseline history

### Benchmark suite

Dreaming maintains one versioned local portfolio suite with three partitions:

1. routing controls unrelated to candidate-specific triggers;
2. general task-success and efficiency cases;
3. normal and high-pressure context cases.

The core suite remains stable enough to compare bundles over time. A holdout
partition is not exposed to skill authoring prompts. When a task becomes
invalid, leaked, obsolete, or overfitted, the replacement creates a new suite
ID. The old and new suites run together once as a bridge; trend lines do not
silently compare incompatible suites.

### Immutable references

The benchmark registry retains:

1. `zero_anchor`: the exact target environment with no non-built-in skills;
2. `production_baseline`: the complete last accepted target bundle;
3. `proposed_bundle`: the production bundle plus staged additions and approved
   removals;
4. the immutable history of every accepted production bundle.

Accepting a new bundle advances the production pointer but never overwrites the
zero anchor or prior production results. Decisions compare both:

- proposed versus previous production, for marginal regression;
- proposed versus zero anchor, for cumulative portfolio drift.

### Initial portfolio policy

The versioned local policy starts with these conservative defaults:

- no critical deterministic case may regress;
- no previously passing related case may become a majority failure;
- aggregate unrelated-task success may be no more than 2 percentage points
  below the zero anchor;
- when success does not improve, paired median turns and total tokens may be
  no more than 5 percent above the previous production bundle;
- cumulative paired median turns and total tokens may be no more than 10
  percent above the applicable zero anchor for autonomous additions;
- the catalog description inventory may consume no more than 2 percent of the
  smallest configured supported context window;
- no critical routing control may load an unrelated skill;
- any incomplete, stale, unmatched, or statistically insufficient comparison
  is `inconclusive`, not pass.

Thresholds live in a content-addressed local policy file. Changing a threshold
creates a new policy ID and requires a bridge run against the active production
bundle. A threshold change cannot relabel an existing result.

### Portfolio pressure

Crossing a portfolio limit:

1. freezes autonomous additions;
2. keeps the last known good bundle installed;
3. marks staged candidates `portfolio_pending` or `quarantined` with the exact
   failed dimension;
4. starts a curator dry run focused on overlap, low marginal value, stale
   evidence, and removal candidates;
5. requires a passing proposed-bundle benchmark before publication resumes.

The curator first shortlists skills using verified usage, evidence age,
overlap, candidate-specific value, and regression history. Expensive
remove-one ablation runs only for that shortlist. Skill count alone never
authorizes removal.

## Model proxy calibration

### Purpose

Cheaper models may screen candidate changes only after Dreaming proves that
their decisions predict the configured stronger production model for the same
evaluation dimension and target environment.

Proxy authority is specific to:

- one cheaper exact model;
- one stronger exact model;
- one CLI and adapter route;
- one suite and context profile;
- one routing, task-value, or portfolio metric family.

Evidence does not transfer across provider families or dimensions unless a
separate calibration proves it.

### Calibration corpus

The study runs the same matched treatments across configured cheap, mid, and
strong models using:

- known useful skills;
- marginal skills;
- overlapping skills;
- irrelevant skills;
- deliberately harmful test fixtures;
- both normal and high-pressure context profiles.

The retained comparison includes continuous effect deltas and the final
pass/regression/inconclusive decisions.

### Trust gate

A cheap-to-strong mapping becomes `trusted` only when all are true for one
metric family:

1. At least 60 independent paired decisions cover at least 10 candidate or
   bundle variants and at least two benchmark cycles.
2. No critical case is false-safe, meaning the cheap model passes while the
   strong model regresses.
3. The one-sided 95 percent upper confidence bound for non-critical false-safe
   decisions is at most 5 percent.
4. Pass/regression direction agrees in at least 90 percent of valid pairs.
5. Rank correlation for continuous deltas is at least 0.8.
6. Neither model, environment fingerprint, suite, nor policy has changed.

An `inconclusive` strong-model result cannot establish proxy trust.

### Use after trust

A trusted cheap model may:

- run frequent candidate screening;
- reuse cached passing results for the mapped strong-model gate;
- reduce strong-model candidate runs to a configured random audit sample.

The weekly production portfolio run remains direct on the configured strong
model in the first release. Moving portfolio authority to a proxy requires a
separate reviewed policy change backed by accumulated calibration evidence.

Default strong-model audit sampling is 20 percent of proxy-authorized
candidate decisions, with at least one direct audit in every weekly batch.
Any false-safe audit immediately changes the mapping to `stale`, invalidates
unaudited proxy authority, and requires direct strong-model evaluation.

Every trusted mapping retains reverse references to the lifecycle revisions and
active target inventories whose only strong-model authority came from that
mapping. A false-safe audit routes every affected target through the
safety-withdrawal transaction, removing all dependent revisions from its
inventory. No prior bundle may be restored because it may contain another
revision that depended on the same mapping. The mapping becomes `stale` only
after every affected target reaches `active` on the reduced bundle or
`ownership_removed`. Direct recertification is required before any withdrawn
revision can return.

If the trust gate does not pass, Dreaming stores independent baselines for the
configured production models. It does not average or translate their scores.

## Scheduled operation

### Daily scheduled run

The existing single scheduled owner keeps its order:

1. discover and consolidate retained sessions;
2. route findings and update candidate evidence;
3. expire unsupported candidates;
4. draft at most one newly ready candidate;
5. run at most one cached or cheap-proxy candidate screening job;
6. publish no autonomous additions;
7. retain the existing weekly-not-due records for heavier passes.

Candidate drafting and screening are bounded so session backlog burn-down
remains the daily priority.

### Weekly maintenance run

During the existing weekly bucket, the owner runs:

1. normal daily consolidation;
2. memory roll under its existing policy;
3. finish candidate-specific gate evaluation for the staged batch and rotate
   enough due active skills to cover the active catalog within 90 days;
4. refresh the production portfolio and complete-bundle routing benchmark on
   the configured strong model;
5. refresh the zero anchor only when invalid, expired, or required by a bridge
   run;
6. compare the proposed batch with previous production and the zero anchor;
7. publish the passing target inventories atomically;
8. run curator dry-run analysis, including consolidation or culling pressure;
9. retain explicit approval for any live curator mutation.

If there are no staged changes, the production portfolio benchmark still runs
weekly to detect hidden model or provider drift. The zero arm may remain cached
until its currency expires.

Unavailable or inconclusive candidate and benchmark outcomes are authoritative
non-publication results, not orchestration failures. They record
`ok: candidates-pending` or `ok: portfolio-deferred`, allow the curator dry run
to execute, and advance the weekly bucket so expensive work does not retry on
every daily tick. Corrupt state, process leakage, credential residue, writer
lease failure, or an unverifiable publisher transition remains a hard pass
failure and preserves the existing fail-fast behavior.

### Event-driven runs

The affected authority becomes stale immediately after:

- model, CLI, adapter, harness, comparator, grader, tool, or policy change;
- known instruction or context-profile change;
- direct edit to a candidate or admitted skill;
- target bundle change outside the admitted lifecycle;
- production regression report;
- portfolio suite replacement.

The next scheduled run may rebuild stale evidence, but publication keeps the
last known good bundle until a new exact result passes.

## Publication authority

### Approved target inventory

Each publisher target has an active inventory pointer and immutable prepared
inventory records. Each record contains:

- target and publisher identity;
- ordered skill lifecycle IDs and exact content identities;
- required direct or proxy certifications;
- portfolio suite, policy, environment, and baseline IDs;
- admitted bundle ID;
- prior bundle ID;
- approval timestamp and weekly run ID.

`dreaming-core.py` materializes a bundle only from the active inventory. A
directory present in the managed skill root but absent from that inventory is
not published.

### Publication transaction

Publication uses one prepare, install, verify, activate transaction per target:

1. Write an immutable pending inventory and materialize its immutable bundle.
2. Record the prior active inventory and bundle IDs.
3. Install the pending bundle without changing the active pointer.
4. Verify the publisher reports the pending bundle ID and the local bundle
   still matches its proof.
5. Atomically advance the active inventory pointer.
6. Mark the transaction complete.

On install or verification failure, the pending record remains non-authoritative
and the prior active pointer is unchanged. Startup and reconciliation inspect
incomplete transactions, verify the publisher's actual bundle, and either
finish activation only when every proof is present or restore the prior bundle.
They never infer authority from the newest inventory timestamp.

### Safety withdrawal transaction

Removing quarantined or invalid revisions is not an addition and does not wait
for a zero anchor, strong-model availability, or a passing portfolio
comparison.

For each affected target, Dreaming prepares the active inventory minus the
withdrawn revisions and materializes that removal-only bundle. It then:

1. writes an authoritative `withdrawal_required` pointer to the reduced
   inventory and records the prior bundle ID as forbidden;
2. calls the existing publisher `remove` operation before attempting another
   install, so the known-invalid bundle is no longer served;
3. installs and verifies the reduced bundle;
4. changes the pointer to `active` only after verification;
5. records `ownership_removed` when reduced installation fails.

Startup and reconciliation treat `withdrawal_required` and
`ownership_removed` as safety states. They never restore or reinstall the
forbidden prior bundle. They retry the reduced immutable bundle when the target
becomes healthy and otherwise keep Dreaming ownership removed. Ordinary weekly
publication cannot use a safety-state target until withdrawal completes.

The later weekly benchmark certifies the reduced bundle before new additions
resume.

### Target and model limits

Publication is per CLI target because that is the boundary publisher adapters
control. If one target exposes a single catalog to several models, the local
policy defines the required model support set for that target.

- A directly certified model satisfies only itself.
- A trusted proxy may satisfy only its exact mapped stronger model and metric
  family.
- An untested model is labeled unsupported by the retained evaluation
  authority.
- Dreaming does not claim that a target-wide installed catalog behaves
  identically across untested models.

### Atomic behavior

A candidate batch becomes visible only after:

1. every included exact skill revision is still current;
2. every required target gate passes;
3. the proposed portfolio bundle passes;
4. the pending inventory and content-addressed bundle are durable;
5. publisher installation and verification succeed;
6. the active inventory pointer advances atomically.

On any failure, the target keeps its last known good bundle. Dreaming never
replaces a healthy catalog with an empty, partial, stale, or unverified bundle.
This preservation rule applies to additions and ordinary replacements. A
safety withdrawal follows the removal transaction above and must not preserve
a bundle known to contain invalid authority.

## Active skill lifecycle

### Activity evidence

The lifecycle clock may be refreshed by:

- a proved native skill load for the exact skill;
- new verified independent task evidence;
- a current passing candidate-specific reevaluation;
- a current portfolio benchmark proving retained marginal value;
- explicit user adoption.

Catalog listing, dashboard viewing, file reads, evaluation setup, or
publication does not count as use.

Unknown target usage remains unknown. Missing telemetry is not interpreted as
zero use.

### Grace, stale, quarantine, and archive

- An admitted autonomous skill receives a 30-day grace period.
- An age-based non-use clock starts only when every required target declares
  complete load telemetry for the measured interval and reports no load.
- When any required target reports partial or unknown telemetry, age alone may
  place the skill on the curator shortlist but cannot quarantine or archive it.
- After 30 measured days of affirmative non-use with no new support, explicit
  adoption, or current evaluation value, it becomes `quarantined`.
- Quarantine immediately removes the skill from every approved target
  inventory but leaves its files and evidence intact.
- An evaluation regression, content mismatch, obsolete dependency, invalid
  evidence, failed proxy audit, or explicit supersession quarantines
  immediately regardless of age.
- After 90 days without recovery, an autonomous quarantined skill becomes
  archive-eligible.
- Archive uses the existing Git-backed retirement transaction and tombstone.
  It never deletes unrecoverable content.

An archived skill returns only through explicit user restore, user adoption,
or new qualifying independent recurrence followed by current evaluation.
Restore does not reset age or evaluation currency silently.

### Pins and dependencies

Explicit pins and discovered scheduled dependencies continue to block archive.
They do not force publication of a regressing skill. A pinned regressing skill
remains retained but quarantined until the owner resolves or explicitly
overrides the evaluation problem.

## Dashboard integration

The existing dashboard contract remains authoritative for read-only access,
authenticated loopback serving, scheduled-run nesting, date presentation,
backlog reporting, exact evidence, storage truthfulness, and unavailable-state
handling.

The Overview page retains two historical chart units:

1. one dual-axis chart for active skill count and evaluation rates;
2. one separate chart for review-backlog burn-down.

Evaluation is no longer presented as one universal percentage. The evaluation
axis exposes separate routing, task-value, and portfolio pass or regression
series, with direct and proxy-certified results distinguishable. The latest
skills list shows the same three dimensions and target publication state.

Activity keeps one scheduled parent card with ordered child work. Daily
candidate observations and screening appear beneath that run. Weekly cards add
candidate certification, production benchmark, proposed-bundle comparison,
publication, and curator dry-run children in actual execution order. A weekly
skip continues to show the last weekly run and time until the next due bucket.

Learned skills and skill details add:

- lifecycle state and transition reason;
- independent support count and newest qualifying evidence time;
- direct, proxy, stale, and untested model status;
- routing, task-value, and portfolio results;
- active target inventories;
- quarantine, absorption, archive, and restore history.

The System page reports benchmark receipt bytes as measured storage, not as a
separate capacity limit unless one is configured and enforced.

## Migration

Migration is lazy for historical evidence but explicit for publication
authority.

### Phase 1: readers and shadow state

1. Ship candidate, benchmark, inventory, and lifecycle readers before writers.
2. Add a disabled enforcement flag.
3. Snapshot the publisher bundle IDs installed at migration start as rollback
   anchors.
4. Run candidate matching, evaluation, and portfolio logic in report-only
   mode.

### Phase 2: baseline creation

1. Build the initial stable portfolio suite and local policy.
2. Record zero-skill and production baselines for the configured required
   targets and models.
3. Record environment visibility as complete, partial, or opaque.
4. Do not enable enforcement until the current bundle has a valid benchmark
   result or has been explicitly quarantined.

### Phase 3: existing skill import

- Hand-made skills are imported as `user_authorized` and remain outside
  autonomous archive authority.
- Existing agent-created skills become `legacy_probation` lifecycle records.
- Their evidence is preserved, but old or unverified observations do not
  satisfy the new recurrence gate.
- An existing agent-created skill becomes active only through explicit user
  adoption or current qualifying recurrence plus evaluation.
- Unadopted legacy probation skills are withdrawn from publisher inventories
  when enforcement begins. They are not immediately deleted.

### Phase 4: enforcement

1. Enable candidate admission and approved-inventory publication.
2. Freeze direct autonomous writes into the active skill root.
3. Enable weekly portfolio batch publication.
4. Enable quarantine and stale withdrawal.
5. Enable archive eligibility only after one complete 90-day lifecycle window
   and a verified restore exercise.

No bulk archive occurs during migration.

## Rollback

1. Activate the shared halt switch.
2. Stop the scheduled owner and wait for active evaluation and writer leases to
   end.
3. Restore the previous runtime, evaluator, scheduler, and publisher code.
4. Restore each publisher to the pre-migration last known good bundle ID from
   the ownership journal and verify it.
5. Leave candidate records, benchmark receipts, baseline history, lifecycle
   records, and proxy calibration as inert local evidence.
6. Roll back any completed live curator transaction through
   `curator-run.py rollback`; restore archived skills only through
   `restore-skill.sh`.
7. Run the prior self-test and keep autonomous publication disabled until it
   passes.

Older code has no path from new candidate, benchmark, or approved-inventory
state to legacy evaluation authority. Rollback never relabels a new result as a
legacy pass and never deletes new evidence automatically.

## Realistic failure model

| Failure | Required behavior |
| --- | --- |
| One old session looks reusable | Retain historical evidence only; do not open or admit a candidate |
| Same task appears through several sessions | Preserve the shared task key and count one independent task |
| Two unrelated procedures receive similar model wording | Keep them unpublished until matching is explicit; never merge by hash alone |
| Candidate state is malformed or transition is illegal | Refuse the transition and publication; preserve prior record |
| Staged draft enters a normal skill root | Self-test and publisher inventory validation fail before publication |
| Candidate content changes after evaluation | Candidate ID changes and every dependent candidate gate becomes stale |
| A positive routing case misses a correctness skill | Mark regression; do not average it with other cases |
| A negative case loads an irrelevant directive skill | Mark regression for that case and block the affected gate |
| Tokens or turns are unavailable | Record unknown; do not fabricate zero or an improvement |
| Control and candidate environments differ | Reject the pair as invalid evidence |
| Provider silently changes behind an alias | Weekly production rerun or cache expiry detects drift; visible hashes alone never claim complete equality |
| Zero anchor is expired | Keep production installed, block additions, allow safety withdrawals, and refresh the anchor |
| New production baseline is accepted | Append immutable history; never overwrite zero or prior production references |
| Several small accepted changes accumulate regression | Cumulative comparison against the zero anchor triggers the portfolio budget |
| Portfolio budget fails | Freeze additions, keep last known good publication, and generate curation pressure |
| Cheap model passes but strong audit fails | Mark proxy mapping stale and invalidate unaudited proxy authority |
| Required strong model is unavailable | Keep candidate pending or inconclusive; do not substitute an untrusted proxy |
| Usage telemetry is absent | Report unknown; age may shortlist but cannot quarantine or archive |
| Pinned skill regresses | Quarantine from publication but refuse archive |
| Scheduled dependency discovery fails | Refuse archive or consolidation |
| Publisher install fails after a pending addition | Retain the active pointer and previous bundle; report the failed target |
| Publisher cannot install a safety withdrawal | Remove Dreaming ownership from the target rather than serve a known-invalid revision |
| Existing agent-created skill lacks qualifying evidence | Import to legacy probation and withdraw when enforcement begins; do not delete |
| Dashboard cannot parse new state | Show unavailable or unhealthy, never an empty successful state |
| Rollback encounters active workers or unrelated changes | Refuse rollback until the exact blocker is resolved |

## Hard invariants

1. One autonomous observation never creates or publishes a skill.
2. Old or independence-unverified evidence cannot satisfy autonomous
   admission.
3. Stable lifecycle identity and exact content identity are separate.
4. Candidate drafts never enter any native discovery root or publisher bundle.
5. Every newly admitted revision has current routing and task-value authority,
   and every active bundle has current complete-catalog routing and portfolio
   authority for each required target. A recertification-due active skill
   freezes additions but is not withdrawn without regression evidence.
6. Critical routing and deterministic task regressions cannot be averaged into
   a pass.
7. Cached evidence is reused only for an exact visible environment fingerprint
   and unexpired currency.
8. Hidden provider state is represented as unknown, never implied stable by a
   local hash.
9. Zero-skill and prior production baselines are immutable.
10. Every accepted bundle remains comparable to both its predecessor and the
    applicable zero anchor.
11. A proxy is authoritative only for its exact measured model pair, target,
    metric family, suite, context profile, and policy.
12. The last known good publisher bundle remains installed on incomplete or
    failing additions; known-invalid revisions follow safety withdrawal.
13. Stale, quarantined, expired, absorbed, and archived skills are absent from
    approved publisher inventories.
14. Pins and scheduled dependencies block archive, but cannot turn regression
    into publication authority.
15. Archive remains Git-backed and reversible.
16. Hand-made skills remain outside autonomous mutation.
17. No private transcript, case artifact, credential, or internal system
    representation enters a public skill or repository document.
18. The dashboard reads lifecycle authority but never creates it.

## Acceptance criteria

- **AC-01:** A recent first autonomous observation creates one unpublished
  collecting candidate and does not change any publisher bundle.
- **AC-02:** An old one-off observation and an unverified historical
  observation cannot open or admit a candidate.
- **AC-03:** Two distinct verified task keys from two sessions inside the
  recurrence window make the matching candidate ready; repeated sessions for
  one task do not.
- **AC-04:** Existing active, candidate, archived, absorbed, and tombstoned
  matches prevent silent duplicate creation.
- **AC-05:** User-authorized creation bypasses recurrence only and remains
  subject to validation, evaluation, publication, and portfolio accounting.
- **AC-06:** Routing evaluation exercises positive, close-negative, unrelated,
  and conflict cases against the complete approved catalog and reports hard
  critical failures separately from aggregate metrics.
- **AC-07:** Task evaluation retains success, turns, tokens, tool calls, skill
  loads, and exact treatment identity; unavailable metrics remain unknown.
- **AC-08:** Any bound candidate, suite, model, CLI, adapter, harness, policy,
  tool, context, fixture, or instruction change invalidates dependent
  candidate authority; a bundle change invalidates bundle-level routing and
  portfolio authority without invalidating unchanged task-value evidence.
- **AC-09:** Accepted production bundles append immutable history and remain
  comparable to both the previous bundle and the zero anchor.
- **AC-10:** Daily runs perform bounded candidate work without autonomous
  publication; weekly runs perform full production and proposed portfolio
  comparison.
- **AC-11:** Portfolio policy failure freezes additions, retains the last known
  good bundle, and emits consolidation or culling pressure.
- **AC-12:** A cheap model cannot grant proxy authority until the exact mapping
  passes the stated sample, false-safe, agreement, and correlation gates.
- **AC-13:** Each publisher installs only its approved exact inventory; one
  target's pass cannot authorize another.
- **AC-14:** Regression withdraws an autonomous skill through a safety
  publication before archive; age-based withdrawal requires complete
  affirmative non-use telemetry, and archive remains restorable.
- **AC-15:** Pins and scheduled dependencies prevent archive even while a
  regressing skill remains quarantined.
- **AC-16:** Existing agent-created skills migrate to legacy probation without
  deletion or fabricated recurrence.
- **AC-17:** The dashboard exposes candidate state, evidence recency, gate
  dimensions, baseline freshness, proxy status, target publication, and
  transition reasons without synthesizing missing values.
- **AC-18:** Rollback restores pre-migration publisher bundles and leaves new
  evidence inert.
- **AC-19:** Public promotion and repository validation reject private cases,
  transcript text, credentials, and local authority files.
- **AC-20:** No overall score can authorize a skill when routing, task-value, or
  portfolio authority is failing, stale, or inconclusive.

## Deterministic check contract

### CHK-01: Candidate admission state machine

- **Protects:** AC-01, AC-02, AC-03, AC-05.
- **Setup:** Feed recent, old, verified, unverified, duplicate-task, and
  user-authorized evidence into a fixture registry under the writer lease.
- **Pass signal:** Only the recent first observation creates `collecting`; only
  two qualifying independent observations reach `ready_for_draft`; user
  authority bypasses recurrence but retains required downstream gates.
- **Failure signal:** A skill directory or publisher inventory changes, old
  evidence counts, or one task reaches readiness.
- **Why it proves the contract:** It exercises every admission transition using
  the timestamps, task keys, sessions, and authority that control publication
  eligibility.

### CHK-02: Identity, matching, and duplicate history

- **Protects:** AC-04 and the separation required by AC-08.
- **Setup:** Create two revisions of one lifecycle, then seed active, expired,
  rejected, absorbed, archived, and tombstoned matching fixtures plus one
  uncertain near-match.
- **Pass signal:** The lifecycle ID remains stable, candidate IDs change with
  content, same matches append evidence, absorbed or tombstoned matches do not
  recreate, and uncertain matches stay unpublished.
- **Failure signal:** Content edits change lifecycle identity, semantic hashes
  become authority, or a retired procedure silently returns.
- **Why it proves the contract:** It demonstrates that stable proposal history
  and exact evaluated content cannot be confused.

### CHK-03: Draft-root isolation

- **Protects:** AC-01 and AC-13.
- **Setup:** Place an evaluated candidate package in the candidate data root,
  seed unrelated files in managed roots, and materialize every publisher
  bundle.
- **Pass signal:** Only inventory-listed admitted skills appear in bundles;
  candidate packages and unrelated managed-root directories are absent.
- **Failure signal:** A staged, stale, quarantined, or unlisted package appears
  in any bundle.
- **Why it proves the contract:** It tests the exact boundary that prevents
  unpublished work from polluting discovery.

### CHK-04: Routing matrix

- **Protects:** AC-06 and AC-20.
- **Setup:** Run deterministic fake executors for positive, close-negative,
  unrelated, conflict, and critical cases against a production catalog plus
  candidate.
- **Pass signal:** Metrics match the fixture events, critical misses and false
  loads regress independently, and aggregate success cannot mask them.
- **Failure signal:** Candidate-only routing is accepted as catalog authority
  or a critical failure produces pass.
- **Why it proves the contract:** It verifies both discovery competition and
  non-composite gate semantics.

### CHK-05: Efficacy and efficiency evidence

- **Protects:** AC-07 and AC-20.
- **Setup:** Produce matched zero, production, proposed, and candidate-only
  trials with known success, token, turn, tool-call, load, and unknown-metric
  outcomes.
- **Pass signal:** The receipt preserves every known value, keeps unknown
  distinct from zero, rejects unmatched arms, and applies the selected value
  class policy.
- **Failure signal:** Missing usage becomes zero, a failed task passes through
  efficiency gains, or mismatched environments are compared.
- **Why it proves the contract:** It demonstrates that task value is measured
  from sealed behavior rather than invocation alone.

### CHK-06: Environment and cache invalidation matrix

- **Protects:** AC-08.
- **Setup:** Change each fingerprint field independently, expire each TTL,
  change only the bundle, and vary one opaque provider-state declaration
  without changing visible hashes.
- **Pass signal:** Every bound visible change and expiration invalidates the
  correct dependent result; a bundle-only change invalidates bundle routing and
  portfolio evidence but preserves unchanged task-value evidence; opaque state
  prevents a complete-stability claim.
- **Failure signal:** Any changed input reuses authority or a partial
  fingerprint is labeled complete.
- **Why it proves the contract:** Cache safety depends on exact input identity
  plus explicit treatment of variables Dreaming cannot observe.

### CHK-07: Baseline lineage and anti-ratchet

- **Protects:** AC-09.
- **Setup:** Admit separate fixture series with individually small success,
  turn, and token regressions and append them to one zero anchor.
- **Pass signal:** Prior results remain immutable, marginal deltas use the
  predecessor, cumulative deltas use the zero anchor, and accumulated drift
  in every governed dimension eventually crosses policy.
- **Failure signal:** A new baseline overwrites history or cumulative
  regression remains hidden.
- **Why it proves the contract:** It reproduces the exact ratcheting failure
  the baseline lineage is intended to prevent.

### CHK-08: Scheduled cadence

- **Protects:** AC-10.
- **Setup:** Simulate daily ticks, weekly-not-due ticks, a weekly boundary,
  candidate backlog, cache hits, and bounded evaluation capacity.
- **Pass signal:** Daily runs cap drafting and screening and never publish;
  weekly runs refresh production, evaluate proposed bundles, record unavailable
  evaluation as a non-publication pass, still run curator analysis, advance the
  cadence bucket, and preserve pass order and run IDs.
- **Failure signal:** Heavy portfolio work runs daily, a ready candidate
  publishes before weekly authority, or backlog work is starved.
- **Why it proves the contract:** It verifies rate and ordering through the
  actual scheduler state machine.

### CHK-09: Portfolio budget and curation pressure

- **Protects:** AC-11 and AC-20.
- **Setup:** Run passing, marginal, cumulative-regression, context-budget, and
  critical-regression fixture bundles.
- **Pass signal:** Only the passing bundle advances; failures retain the prior
  bundle, freeze additions, and emit the exact failed dimensions for curator
  dry-run input.
- **Failure signal:** A failing bundle installs, an overall score masks a gate,
  or skill count alone triggers removal.
- **Why it proves the contract:** It couples measured portfolio cost to
  publication and curation without creating deletion authority.

### CHK-10: Model proxy calibration

- **Protects:** AC-12.
- **Setup:** Feed fixture comparisons below, at, and above every sample,
  false-safe, agreement, correlation, freshness, and critical-case threshold.
- **Pass signal:** Only the exact qualifying mapping becomes trusted; a
  false-safe audit withdraws every inventory entry that depends solely on the
  mapping through the safety-withdrawal transaction, never restores a prior
  bundle containing a dependent revision, makes the mapping stale, and
  invalidates unaudited proxy authority.
- **Failure signal:** Cross-family transfer, weak sample size, stale model IDs,
  or simple correlation alone grants authority.
- **Why it proves the contract:** It verifies that cheaper-model substitution
  is empirical, directional, and revocable.

### CHK-11: Target inventory and atomic publication

- **Protects:** AC-13 and AC-18.
- **Setup:** Give three publishers different approved inventories, inject
  stale certifications, candidate drift, addition install failure, safety
  withdrawal failure, and verification failure, then restart and reconcile
  after every failure point.
- **Pass signal:** Each target receives only its inventory; invalid targets
  retain their prior active pointer and verified bundle after failed additions;
  pending inventory is never read as active after restart; a safety withdrawal
  records the prior bundle as forbidden, removes the target publisher before
  retry, and reconciliation never restores that bundle; rollback restores
  recorded pre-migration bundles.
- **Failure signal:** One target's pass authorizes another, partial bundles
  install, or failure replaces the last known good bundle.
- **Why it proves the contract:** It exercises publication at the real
  target-owned boundary rather than only validating state files.

### CHK-12: Lifecycle withdrawal, archive, and restore

- **Protects:** AC-14 and AC-15.
- **Setup:** Advance autonomous, pinned, implicitly pinned, regressing,
  complete-telemetry non-use, unknown-telemetry, stale, and recovered fixtures
  through grace, quarantine, archive eligibility, archive, and restore.
- **Pass signal:** Quarantine withdraws immediately; pins block archive but not
  withdrawal; unknown telemetry cannot start the age clock; archive uses
  existing retirement records and tombstones; restore preserves age and
  requires new authority before publication.
- **Failure signal:** A regressing skill remains published, a pin fabricates a
  pass, or archive loses recoverability.
- **Why it proves the contract:** It separates context removal from destructive
  authority and proves reversibility.

### CHK-13: Legacy migration

- **Protects:** AC-16.
- **Setup:** Import hand-made, agent-created, pinned, evaluated, unevaluated,
  old, and recently recurring fixture skills.
- **Pass signal:** Hand-made skills remain user-authorized; agent-created
  skills enter legacy probation; qualifying recent evidence is evaluated
  normally; unknown usage cannot start an age archive clock; after 90 days an
  unsupported legacy record becomes a curator recommendation rather than an
  automatic archive; no skill is deleted or granted fabricated recurrence.
- **Failure signal:** Old creation dates become fresh evidence, migration
  silently publishes probation skills, or bulk archive occurs.
- **Why it proves the contract:** It tests the policy against the actual
  pre-existing library shapes.

### CHK-14: Dashboard truthfulness

- **Protects:** AC-17.
- **Setup:** Serve valid, stale, unknown, malformed, proxy, direct, withdrawn,
  and historical-baseline fixture state.
- **Pass signal:** The API and UI show the separate dimensions, freshness,
  target status, and transition reasons; malformed or absent state is
  unavailable or unhealthy.
- **Failure signal:** Missing values become zero, one percentage hides failed
  gates, or the dashboard mutates authority.
- **Why it proves the contract:** It verifies the owner's review surface
  without making presentation a second state owner.

### CHK-15: Privacy and public inventory

- **Protects:** AC-19.
- **Setup:** Seed candidate packages, benchmark cases, receipts, aliases,
  transcript sentinels, credentials, and local authority paths before public
  promotion and repository validation.
- **Pass signal:** Every private or local authority artifact is rejected or
  stripped, and the public inventory contains only approved skill runtime
  files.
- **Failure signal:** Any sentinel or local evaluation file enters a public
  commit or published plugin.
- **Why it proves the contract:** It exercises the complete file inventory at
  the public transfer boundary.

### CHK-16: Critical rollback

- **Protects:** AC-18.
- **Setup:** Stop a fixture run after candidate state writes, after benchmark
  receipts, after inventory approval, after one successful publisher install,
  and during a curator archive transaction.
- **Pass signal:** The halt switch stops new work, previous bundles are
  restored and verified, curator rollback restores archived content, new
  evidence remains inert, and old gates cannot interpret it.
- **Failure signal:** Rollback deletes evidence, accepts new authority through
  an old reader, or leaves a partial publisher state.
- **Why it proves the contract:** It demonstrates recovery at every durable
  boundary changed by this design.

## Fail-closed evidence

The critical boundary is proved only when deliberate tests show that:

- one observation, old evidence, unverified independence, or a same-task
  continuation cannot create publication authority;
- a malformed candidate record or illegal transition cannot change a bundle;
- a staged package cannot enter native discovery through filesystem placement;
- candidate drift invalidates routing, task-value, portfolio, proxy, and target
  authority;
- bundle churn invalidates bundle-level routing and portfolio evidence without
  erasing unchanged admitted-skill task-value evidence;
- missing or mismatched trial arms, metrics, loads, fingerprints, suites,
  baselines, or receipts produce `inconclusive` or stale state;
- a critical routing or deterministic task regression blocks publication even
  when aggregate metrics improve;
- expired zero or production evidence blocks additions while preserving the
  last known good bundle;
- accumulated small regressions cross the fixed-anchor portfolio budget;
- an untrusted or stale cheap-model mapping cannot substitute for a strong
  model;
- publication failure cannot replace the prior verified bundle;
- safety withdrawal removes a regressing skill or removes the target publisher
  even when portfolio evidence is unavailable;
- unknown usage telemetry cannot start age-based quarantine or archive;
- incomplete usage or dependency discovery cannot authorize archive;
- rollback restores prior publication and cannot reinterpret new state as old
  authority;
- public validation rejects every private evaluation and transcript sentinel.

## Definition of Done: Conservative skill lifecycle and portfolio evaluation

- [ ] Autonomous findings enter candidate state and require recent independent
      recurrence before drafting.
- [ ] Stable lifecycle IDs, exact candidate IDs, duplicate matching,
      supersession, absorption, expiration, and quarantine are implemented and
      validated.
- [ ] Staged candidates are isolated from every discovery and publication
      root.
- [ ] Existing cross-CLI evaluation records routing, task value, efficiency,
      context profile, and exact environment identity for complete-catalog
      treatments.
- [ ] Versioned portfolio suites, zero anchors, immutable production history,
      cache currency, and anti-ratchet comparisons are authoritative.
- [ ] Model-proxy calibration is implemented with no trusted mapping by
      default and the stated false-safe revocation behavior.
- [ ] Daily candidate work is bounded; weekly maintenance performs full
      portfolio certification and batch publication.
- [ ] Publisher adapters install only approved exact target inventories and
      preserve the last known good bundle on failure.
- [ ] Portfolio pressure freezes additions and feeds the existing approved
      curator dry-run and reversible archive flow.
- [ ] Existing agent-created skills migrate to legacy probation without
      deletion or fabricated authority.
- [ ] Dashboard views expose separate gate dimensions, baseline lineage,
      freshness, proxy status, lifecycle reasons, and target publication.
- [ ] Every acceptance criterion is covered by the deterministic check
      contract and the critical fail-closed evidence passes.
- [ ] Migration and rollback are exercised against installed local publishers.
- [ ] Public inventory validation proves that private cases, transcripts,
      credentials, and local authority state cannot leave the local boundary.
