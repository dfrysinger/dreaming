# Aggressive skill portfolio governance and decision dashboard

## Objective

Judge every enabled skill primarily by controlled evaluation, verified usage,
redundancy, and dependencies, then give the user a plain-language decision
queue while automatically pruning only machine-created skills and reversibly
disabling eligible plugin packages.

## Lane

**Critical.** This change affects durable personal skill files, Copilot plugin
enablement, user-authorized dashboard mutations, retained evaluation and usage
authority, and recoverable archive transactions.

The system must fail closed when inventory, evaluation, usage, dependencies,
target identity, settings identity, authorization, or restore evidence is
missing or stale.

## Non-goals

- Do not change transcript discovery, transcript scoring, the 25-review
  capacity limit, model routing, or candidate recurrence.
- Do not let one transcript occurrence prove that an active skill should be
  kept.
- Do not edit files inside an installed plugin package.
- Do not claim individual plugin-skill disablement when the installed Copilot
  version exposes only whole-plugin enablement.
- Do not uninstall plugins automatically.
- Do not automatically edit, consolidate, or archive user-created or
  unknown-origin personal skills.
- Do not allow dashboard code to edit files or settings directly.
- Do not treat missing usage, missing evaluation, or an inconclusive
  evaluation as positive evidence.
- Do not call a decision that excludes active-session tails complete transcript
  coverage.
- Do not let an exhausted collection budget, an old unread transcript, or an
  unresolved identity that could name the target become non-use evidence.
- Do not treat provenance or ownership as evidence that a skill is useful.
- Do not remove pins, bypass scheduled dependencies, discard Git history, or
  weaken restore verification.
- Do not add another inventory collector, evaluator, archive format, settings
  writer, scheduler, or transaction authority.
- Do not install preview code into the scheduled Mac mini generation, point a
  preview at live writable state, or use preview behavior as installed proof.

## Isolated parallel preview

Report-only implementation and browser review may proceed while an installed
capacity run is waiting for its untouched four-hour launch. This is a
development boundary, not a second production owner.

The preview uses:

- a separate Git worktree and feature branch created from the reviewed local
  governance design;
- a private snapshot of the skill inventory, evaluation, usage, dependency,
  decision, and receipt inputs needed by the dashboard;
- preview-specific state and data roots that contain no symlink or configured
  path back to live writable roots;
- an ordinary foreground dashboard process on a different port;
- mutation endpoints disabled at the server boundary, with every action
  rendered as unavailable and explained;
- no launchd plist, installer activation, scheduler, self-test generation,
  publication, plugin settings transaction, archive helper, or estate-action
  dispatch.

Snapshot capture is a separate, bounded read-only operation. It first verifies
that no capacity run is active and acquires the live writer lock without
waiting. If either check shows activity, capture exits without copying
anything. It holds the lock through source validation, copying, and final
validation, with a 30-second absolute capture deadline, then releases it only
after accepting or deleting the snapshot. Capture must begin at least ten
minutes before the installed owner's next known interval eligibility. The
capture records the relevant live generation marker before and after copying
and rejects the snapshot when the marker changes. For inputs without a
generation marker, it records source identity, size, and digest before and
after the copy and rejects any mismatch. It never follows a symbolic link.

The resulting manifest records source identities, file sizes, digests, capture
time, and the stable source generation. The preview verifies the complete
manifest before loading it, opens snapshot inputs read-only, and verifies the
identity and digest again before every request that reads snapshot data.
Missing, malformed, changed, replaced, or unexpectedly linked input makes the
preview unavailable rather than falling back to live state.

The capacity branch tip and tracked working tree, installed Mac mini
repository, activation generation, launchd label and plist inventory, timer,
and dashboard process remain untouched. The linked preview worktree may add
Git objects, worktree metadata, and its own branch ref to the shared local
repository. Capacity launchd run counts and retained live state may change
only through the already-installed capacity owner and are attributed to its
run records, never to the preview process tree.

Preview work may begin only from a local commit containing this isolation
contract and PORT-CHK-PREVIEW. The plan baton records that commit before the
worktree is created, and the preview branch must descend from it. Preview
commits are reviewed on their own branch. They may be merged into the main
local branch only after the capacity natural-run and rollback evidence is
terminal and retained. Installed governance proof starts only after that merge
passes the normal installer, self-test, halt, enable, and rollback boundaries.

## User policy

The portfolio policy is intentionally aggressive about reducing the number of
enabled skills because catalog size has a cost even when a skill is not used:
more descriptions compete during routing, more nearby instructions can load,
and more overlapping capabilities make evaluation and maintenance harder.

The policy separates **judgment** from **automatic authority**:

- every enabled personal skill receives a keep, evaluate, merge, disable, or
  archive recommendation regardless of who created it;
- every plugin skill receives an individual recommendation;
- every plugin package receives a whole-package recommendation;
- Dreaming may automatically change only Dreaming-managed and verified
  machine-created personal skills;
- Dreaming may automatically disable or restore a whole plugin only through
  the existing qualified plugin settings transaction;
- user-created and unknown-origin personal skills remain unchanged until the
  user explicitly acts in the dashboard or foreground CLI;
- an explicit dashboard action is user authority, not autonomous authority,
  and still requires identity, dependency, pin, dirty-tree, transaction, and
  restore checks.

## Plain-language model

The product uses these terms:

| Term | Meaning |
| --- | --- |
| Conversation inspection | Read one completed conversation and look for a reusable lesson |
| Supporting occurrence | One independent task where a reusable procedure appeared useful |
| Unpublished draft | Proposed instructions that agents cannot load |
| Evaluation | Controlled tasks run with and without the exact skill |
| Successful use | A skill-tool invocation whose matching completion proves the skill loaded |
| Recommendation | The system's suggested portfolio decision |
| Action | A disable, archive, restore, merge, keep, pin, or evaluation request |
| Receipt | Durable proof that an action completed or was rolled back |

The dashboard must not use "review" without a qualifying noun. It must not call
a recommendation evidence. It must not use "observed" when it means the time a
report was generated.

## Reuse contract

| Concern | Existing owner | Required extension |
| --- | --- | --- |
| Complete enabled-skill inventory | `dreaming-estate.py` census | Preserve one row per canonical enabled capability |
| Verified usage | `dreaming-estate.py` usage collector | Expose 7, 30, and 90-day successful loads, global completeness, a collection watermark, and pending files classified by quiet-period recency, stable budget deferral, read failure, parse failure, and changed-during-read, including identity, modification time, count, bytes, and failure-record identity |
| Candidate and active-skill evaluation | `skill-evaluation.py` and retained receipts | Join current exact-skill results into estate decisions |
| Complete-catalog routing and portfolio cost | Existing portfolio benchmark | Evaluate proposed removals as well as additions |
| Dependencies and pins | `scheduled-skill-deps.py` and curator inventory | Show blockers and bind them into actions |
| Personal withdraw, archive, and restore | `estate-action.py` through `curator-run.py`, `archive-skill.sh`, and `restore-skill.sh` adapters | Add automatic machine and explicit user-intent authority contracts without creating another writer |
| Plugin package disable and restore | `plugin-settings-transaction.py` | Reuse the existing lossless settings transaction |
| Estate action authority | `estate-action.py` | Remain the sole mutation authorization and dispatch owner; add user-intent, disposition, and machine-withdraw contracts |
| Dashboard data | `dreaming-dashboard.py` | Produce portfolio decisions and detail records |
| Dashboard presentation | `dashboard.js` | Replace governance log terminology and add decision workflows |
| Halt and recovery | Existing shared halt and recovery fences | Apply to every mutating dashboard action |
| Isolated browser preview | Existing dashboard server fixtures and explicit data/state-root configuration | Add a fail-closed snapshot input mode and disabled-action presentation; do not add another installed service |

New code is justified for four missing boundaries:

1. a decision engine that combines evaluation, usage, redundancy, and
   dependency facts for every enabled capability;
2. individual plugin-skill recommendations even when only whole-plugin
   mutation is supported;
3. append-only user decisions and action intents from the dashboard;
4. a dashboard view that presents decision facts rather than transaction
   internals.

## Supersession of the read-only dashboard invariant

This work order narrows invariant 15 and CHK-08 in
`docs/unified-skill-estate-governance-design.md`.

The dashboard remains unable to create **autonomous** authority and remains
unable to write managed roots, settings, receipts, or transaction state. An
authenticated, nonce-bound user action may create an immutable user-intent
record. `estate-action.py` is still the sole authority that may validate that
intent and dispatch an existing transaction.

The unified CHK-08 read-only requirement continues to apply when mutation
endpoints are disabled. When enabled by this work order, its replacement is
PORT-CHK-09: dashboard state alone cannot authorize mutation, while an explicit
current user intent may enter the existing fail-closed authority path.

## Authority and value are separate

Each capability has two independent classifications.

### Value state

Value state answers whether the skill appears worth keeping:

- `proven_useful`
- `used_evaluation_missing`
- `evaluate_now`
- `merge_candidate`
- `disable_candidate`
- `archive_candidate`
- `insufficient_information`

Protection is not a value state. Pins and dependencies are independent
retention constraints. A protected capability can therefore be both
`disable_candidate` and `user_decision_required`, with the regression visible
while automatic removal remains blocked.

### Mutation authority

Mutation authority answers what Dreaming may do:

- `automatic_personal`
- `user_decision_required`
- `automatic_plugin_package`
- `report_only`
- `immutable_builtin`

A skill can be `archive_candidate` and
`user_decision_required`. The dashboard must show both facts instead of
changing the recommendation to `keep`.

## Decision inputs

### Primary evidence

1. **Controlled evaluation**
   - exact skill inventory and content identity;
   - intended cases;
   - close negative and conflict cases;
   - related-task regressions;
   - proof that the skill loaded;
   - exact model, CLI, policy, and environment identity;
   - result currency.

2. **Verified usage**
   - successful loads in the last 7, 30, and 90 days;
   - distinct sessions or tasks when available;
   - last successful load;
   - whether the usage corpus and capability attribution are complete;
   - the per-capability 30-day decision coverage, including any excluded
     active-session tails and any stable backlog or identity blocker.

3. **Portfolio relationship**
   - overlapping or superseding skills;
   - complete-catalog routing effect;
   - marginal task value;
   - proposed-estate comparison with the skill removed.

4. **Removal safety**
   - explicit and implicit pins;
   - scheduled and durable dependencies;
   - clean target worktree;
   - exact inventory identity;
   - tested archive or settings restore path.

### Secondary context

Supporting occurrences may explain why a skill was created or why a rare skill
still matters. They do not substitute for evaluation or usage when deciding
whether an active skill should stay enabled.

Provenance controls automatic authority. It does not increase value.

### Thirty-day settled-use boundary

Global usage coverage remains an audit fact. `complete`,
`corpus_complete`, `attribution_complete`, pending sessions, failures, and the
collection work budget retain their existing meanings. The decision engine
does not relabel incomplete global coverage as complete.

For the 30-day pruning policy, each enabled capability also receives a
decision-coverage object with `window_days: 30` and one state:

- `used_30d`: at least one verified successful load in the last 30 days;
- `complete_zero_30d`: the complete retained corpus contains no successful
  load in the last 30 days;
- `settled_zero_30d`: every stable transcript eligible for collection whose
  modification time intersects the 30-day window has been indexed, no
  successful load appears in that settled corpus, and the only unread
  transcript data intersecting the window is in files modified inside the
  collector's existing quiet period;
- `blocked_stable_backlog`: an eligible transcript whose modification time
  intersects the decision window remains unread after the quiet period because
  the work budget was reached, the file changed during collection, or it could
  not be read or parsed;
- `blocked_identity`: an unattributed observed name could identify this
  capability through a reviewed alias or conflicting current mapping.

The collector classifies every pending file before the decision engine derives
coverage. The classification records the file identity, modification time,
bytes, and one reason: quiet-period recency, stable budget deferral, read
failure, parse failure, or changed during read. Aggregate counts do not
substitute for these records. A read or parse failure remains retryable and
visible in the global audit. It blocks a zero-use decision while its
modification time intersects that decision's window; after it ages outside the
window, it cannot contain an invocation inside the window and no longer blocks
that decision. The engine never clears a relevant failure through guessed
target attribution.

`settled_zero_30d` is decision evidence under the versioned portfolio policy,
not a claim that the transcript corpus is complete. Its bound object records
the state, window boundaries, collection watermark and receipt identity,
excluded recent file count and bytes, relevant stable-backlog count and bytes,
pending-failure identities, and target-scoped identity blockers and candidate
identities. The dashboard renders it as "No use in settled transcripts for 30
days" and names the active-tail exclusion. It never renders "Never used" or
"Complete zero."

Archive maturity uses a separate decision-coverage object with
`window_days: 60`. `decision_grade_zero_60d` means the 60-day object is
`complete_zero_60d` or `settled_zero_60d` under the same active-tail,
stable-backlog, failure-aging, and identity rules; the corresponding non-zero
and blocked states are `used_60d`, `blocked_stable_backlog_60d`, and
`blocked_identity_60d`. It is evaluated at archive decision time; continuity
of earlier recommendations is not a substitute. A withdrawn skill must also
have remained withdrawn for the full 60 days. A use inside that window,
relevant stable unread file, or identity blocker prevents archive.

An unattributed historical name with no reviewed alias and no current mapping
candidate remains visible in the global audit but does not block unrelated
current skills. A conflicting mapping blocks only its candidate capabilities
when those identities are available. When the collector cannot identify the
candidate set, zero-use decisions remain blocked rather than guessing.

Direct usage and dependency evidence remain separate. A helper launched by an
enabled skill, scheduled job, or plugin member can be required even when it has
zero direct `skill` tool loads. Pins and complete dependency inventory override
non-use mutation authority without changing the non-use value finding.

The 30- and 60-day windows and active-tail exclusion are policy constants, not
new runtime configuration. Immediately before every automatic
removal-affecting dispatch, including withdrawal, archive, consolidation,
individual plugin disablement, and whole-plugin disablement,
`estate-action.py` acquires the writer lease, performs a fresh collection,
derives and appends a new current decision, and rechecks the target, dependency
set, disposition set, and policy identity. The earlier recommendation is only
a trigger for reconsideration; it never authorizes dispatch. The action binds
the newly appended decision and, when that decision still grants automatic
authority, dispatches before releasing the lease. An advanced watermark, new
receipt identity, shifted window, or changed active-tail count is expected and
is evaluated as input to the new decision rather than compared byte-for-byte
with the old one. A new verified use, relevant stable backlog, changed target
identity, blocker, dependency, disposition, or policy refuses authority and
creates no mutation receipt.

## Decision policy

The policy file is versioned and content-addressed. A policy change does not
rewrite prior decisions. It produces new recommendations bound to the new
policy identity.

### Hard precedence

Rules are evaluated in this order:

1. A current critical evaluation regression produces `disable_candidate`.
2. A complete evaluation that shows another enabled skill covers the same
   tasks produces `merge_candidate`.
3. A current passing evaluation plus verified recent use produces
   `proven_useful`.
4. A current passing evaluation with no recent use produces `proven_useful`
   only when the bound evaluation policy declares the retained cases
   `rare_or_safety_critical` and the portfolio relationship proves the
   capability is not redundant; otherwise it produces `evaluate_now` with
   `passing_but_unused` and either `rarity_unproven` or
   `portfolio_value_unproven`.
5. Recent verified use without a current evaluation produces
   `used_evaluation_missing`.
6. `complete_zero_30d` or `settled_zero_30d` without a current passing
   evaluation produces `evaluate_now`.
7. A failed or non-inferior-value evaluation with `complete_zero_30d` or
   `settled_zero_30d` produces `disable_candidate`.
8. A personal skill that has remained disabled or withdrawn for 60 days, has
   `decision_grade_zero_60d`, has no passing unique-value evaluation, and has
   no dependency produces `archive_candidate`.
9. Missing or conflicting primary evidence produces
    `insufficient_information`, never `proven_useful`.

After value classification, pins and dependencies are applied as removal
constraints. They never hide the value state. A pinned or required
`disable_candidate` remains enabled, appears in the human decision queue as a
blocking regression, and names every dependent capability. Automatic action is
refused until the dependency is removed or the user chooses a governed
replacement plan.

### Evaluation currency

- Passing or failing exact-skill evaluations remain current for 90 days when
  their bound model, CLI, harness, policy, skill content, and environment have
  not changed.
- A content change invalidates the skill's evaluation immediately.
- A model, CLI, harness, grader, or policy change invalidates dependent
  evaluation authority.
- A stale passing evaluation may explain history but cannot produce
  `proven_useful`.

### Aggressive evaluation queue

The evaluator prioritizes:

1. enabled skills with no successful use in 30 days;
2. enabled skills with no current evaluation;
3. overlapping skills;
4. skills implicated in routing conflicts or regressions;
5. plugins whose complete package may be disabled;
6. stale passing evaluations.

The initial migration queue includes every enabled skill. Work is bounded by a
configured daily case and AI-credit budget. Unfinished evaluation work remains
visible with its queue position and reason. Budget exhaustion never becomes a
keep decision.

## Personal skills

### Recommendations

All personal skills receive recommendations, including user-created,
machine-created, protected, and unknown-origin skills.

### Automatic actions

Only `dreaming_managed` and `legacy_machine` skills may be changed
automatically.

Automatic withdrawal, archive, or consolidation requires:

- `disable_candidate`, `archive_candidate`, or `merge_candidate`;
- complete evaluation evidence and decision-grade usage evidence required by
  that recommendation;
- complete dependency and pin inventory;
- no unrelated dirty work;
- exact provenance and target identity;
- a complete Git-backed restore path;
- open halt and pause controls;
- an exclusive writer lease;
- a proposed-estate routing and portfolio pass.

A machine-created `disable_candidate` first enters a reversible withdrawn
state through an `estate-action.py` `personal_withdraw` action. Withdrawal
removes the skill from active loading without deleting its Git-backed package,
writes a retirement record, and proves exact restore. After 60 days
continuously in the withdrawn state and a current
`decision_grade_zero_60d` result, an `archive_candidate` decision may authorize
archive. Consolidation uses the same authority owner, fresh pre-dispatch
collection, and restore proof, and may not bypass withdrawal or archive proof.

### User actions

The user may explicitly act on any personal skill that lives in a managed Git
root.

User authority may choose keep, pin, run evaluation, merge, withdraw, archive,
or restore. It does not bypass:

- path and identity validation;
- pins unless the explicit action is unpin;
- dependency disclosure and confirmation;
- dirty-work protection;
- transaction journaling;
- Git-backed restore;
- post-action inventory verification.

The confirmation surface must show the exact skill, recommendation, reason,
dependencies, files affected, and restore method.

`estate-action.py` accepts two explicit personal authorization sources:

- `automatic_machine`, limited to current `dreaming_managed` or
  `legacy_machine` provenance and policy-authorized actions;
- `explicit_user_intent`, carrying a current immutable user intent for the
  exact target and permitting a user-authorized action regardless of
  provenance.

Both sources bind the same census, target, decision, dependency, policy,
portfolio, proposed-estate, halt, receiver, routing, and restore evidence.
`explicit_user_intent` additionally binds the intent identity, confirmation
nonce, user reason, and disposition state. `curator-run.py` and archive/restore
helpers are adapters beneath `estate-action.py`; they never accept dashboard
authority directly.

## Plugin skills and packages

### Individual skill recommendations

Every skill inside an enabled plugin receives its own value recommendation
from evaluation, usage, overlap, and dependencies.

Individual recommendations answer:

- keep this skill;
- this skill needs evaluation;
- this skill appears redundant;
- this skill appears harmful;
- this skill is unused;
- this skill is required by a dependency.

### Individual disable capability

Individual plugin-skill disablement is enabled only when an installed-host
qualification proves that the exact Copilot version exposes a supported,
reversible, ownership-safe per-skill enablement control.

The qualification must prove:

1. disabling one exact plugin skill removes only that skill;
2. the rest of the plugin's skills, agents, hooks, MCP servers, and LSP
   servers remain unchanged;
3. unrelated plugins remain unchanged;
4. restoring the prior setting returns the exact capability;
5. settings conflicts and runtime verification fail closed.

Copilot CLI 1.0.81 exposes plugin management and whole-plugin enablement but
the current repository has no proven per-skill disable contract. Until such a
qualification exists, individual plugin-skill decisions are recommendation
only.

The system must never simulate individual disablement by editing plugin files,
hiding paths, or copying a modified plugin.

### Whole-plugin recommendation and disable

A plugin receives `disable_candidate` only when:

- every skill capability is `disable_candidate`, `archive_candidate`, or
  redundant;
- every non-skill capability is fully inventoried and shown unnecessary,
  unused with complete telemetry, or superseded;
- no dependency requires the plugin or one of its capabilities;
- the proposed estate without the plugin passes routing and portfolio gates;
- the installed plugin source type has a current settings qualification;
- disable and restore can be verified through fresh runtime inventories.

One useful, protected, unevaluated, or unknown capability blocks automatic
whole-plugin disablement. The dashboard still reports which individual skills
made the package ineligible.

The user may explicitly disable a plugin from the dashboard after seeing its
complete capability inventory. The existing settings transaction remains the
only writer.

## Human decision dashboard

### Portfolio page

The default estate page shows one row per enabled canonical skill:

| Column | Meaning |
| --- | --- |
| Skill | Runtime skill name |
| Installed from | Personal root, Dreaming bundle, plugin, or builtin |
| Recommendation | Plain-language value decision |
| Why | Short reason based on evaluation, usage, overlap, or dependency |
| Evaluation | Pass, fail, inconclusive, stale, missing, or queued |
| Use 30d | Verified successful loads or Unknown |
| Last used | Last successful load or Never observed |
| Dependencies | Protected, clear, incomplete, or Unknown |
| Authority | Automatic, your decision, plugin package only, or immutable |
| Next action | Open, evaluate, keep, disable, archive, merge, or restore |

The page defaults to decision priority:

1. failing or harmful;
2. archive or disable candidate;
3. needs user decision;
4. needs evaluation;
5. protected;
6. proven useful.

### Decision queue

The dashboard provides filters for:

- needs your decision;
- evaluation failed;
- evaluation missing or stale;
- unused 30 days;
- unused 90 days;
- duplicate or merge candidate;
- plugin skill recommendation;
- whole-plugin disable candidate;
- protected dependency;
- insufficient information.

### Detail view

The detail view shows:

- complete current skill instructions;
- exact source and authority;
- 7, 30, and 90-day usage;
- last successful invocation;
- evaluation cases, outcomes, traces, model, and currency;
- supporting occurrences in a separate history section;
- overlapping skills and comparative evidence;
- dependencies and pins;
- recommendation reason and policy version;
- action history and receipts;
- restore instructions.

### User actions

The dashboard supports:

- Run evaluation
- Keep for 90 days
- Keep and pin
- Merge
- Withdraw or disable
- Archive
- Restore
- Correct origin classification
- Disable whole plugin
- Restore plugin

Unavailable actions remain visible but disabled with a precise reason.

### Terminology changes

Replace:

| Existing label | Replacement |
| --- | --- |
| Governance decisions and actions | Portfolio decisions |
| Authority | Who may change it |
| Decision | Recommendation |
| Action `recommendation` | No action taken |
| State `kept` | Still enabled |
| Evidence `recommendation` | Remove the column |
| Observed | Recommendation generated |
| Current | Latest recommendation |
| Historical | Older action receipt |
| Manual review | Needs your decision |
| Shadow candidate | Unpublished draft |
| Dream review | Conversation inspection |

Recommendation rows and completed action receipts appear in separate tables.

## Dashboard mutation boundary

The dashboard remains an authenticated local service. Read requests use the
existing token. Mutating requests require:

- the existing dashboard token in the authorization header;
- a same-origin request;
- JSON content type;
- an exact action kind and target identity;
- the current estate snapshot and recommendation identities;
- a one-time confirmation nonce from a prior read;
- a non-empty user reason for destructive or disabling actions.

The dashboard never edits a skill, Git root, plugin setting, policy file, or
receipt directly.

It writes an immutable user-intent record and invokes the existing authority:

- evaluation requests enter the bounded evaluator queue;
- every personal mutation passes through `estate-action.py`, which dispatches
  `curator-run.py` and the archive/restore helpers as adapters;
- plugin actions use `estate-action.py` and
  `plugin-settings-transaction.py`;
- keep and pin decisions use scoped append-only disposition records;
- origin corrections create an explicit user-attested provenance record
  without rewriting historical evidence.

The HTTP response returns an accepted intent ID. Completion is observed from
the action ledger. A browser disconnect does not cancel or repeat a committed
transaction.

## Data model

### Portfolio decision

```json
{
  "schema_version": 1,
  "decision_id": "sha256:...",
  "capability_id": "opaque canonical identity",
  "target_identity_sha256": "sha256:...",
  "recommendation": "evaluate_now",
  "reason_codes": ["evaluation_missing", "unused_30d"],
  "evaluation": {
    "status": "missing",
    "receipt_sha256": null,
    "current": false
  },
  "usage": {
    "complete": false,
    "uses_7d": 0,
    "uses_30d": 0,
    "uses_90d": 0,
    "last_successful_invocation": null
  },
  "decision_coverage": {
    "window_days": 30,
    "window_start": "timestamp",
    "window_end": "timestamp",
    "state": "settled_zero_30d",
    "usage_receipt_sha256": "sha256:...",
    "collection_watermark": "opaque monotonic identity",
    "excluded_recent": {
      "count": 2,
      "bytes": 4096
    },
    "relevant_stable_backlog": {
      "count": 0,
      "bytes": 0,
      "oldest_modified_at": null
    },
    "pending_failure_ids": [],
    "identity_blockers": [],
    "candidate_capability_ids": []
  },
  "archive_coverage": null,
  "dependencies": {
    "complete": true,
    "blocking": []
  },
  "mutation_authority": "user_decision_required",
  "policy_sha256": "sha256:...",
  "census_sha256": "sha256:...",
  "generated_at": "timestamp"
}
```

### User disposition

```json
{
  "schema_version": 1,
  "intent_id": "opaque random identity",
  "capability_id": "opaque canonical identity",
  "action": "keep",
  "reason": "Still needed for quarterly recovery work",
  "effective_until": "timestamp",
  "target_identity_sha256": "sha256:...",
  "decision_id": "sha256:...",
  "created_at": "timestamp",
  "status": "accepted"
}
```

Keep dispositions expire after 90 days by default. They do not fabricate
evaluation or usage evidence. They temporarily suppress automatic mutation and
remain visible as user authority.

Every automatic personal action binds the current disposition-set identity.
`estate-action.py` re-reads and verifies that identity immediately before
dispatch. A Keep or Pin accepted after authorization but before dispatch makes
the authorization stale and refuses mutation. Expiration recomputes a
recommendation but never revives or executes a previously authorized action.

## Failure model

| Failure | Required behavior |
| --- | --- |
| Evaluation missing | Show Needs evaluation; do not show Proven useful |
| Evaluation stale | Show Stale evaluation and queue refresh |
| Usage unavailable | Show Unknown; do not convert to zero |
| No usage with complete corpus | Show the measured zero and apply policy |
| No usage with only recent active tails unread | Show settled 30-day non-use, the excluded file count, and the collection watermark |
| Collection stops with stable eligible transcripts unread | Keep affected zero-use decisions blocked and show the stable backlog |
| Stable unread file cannot be read or parsed | Retry and show it in the global audit; block windows intersecting its modification time, then stop blocking only after it ages outside the window |
| Historical name has no current mapping candidate | Keep it in the global audit without blocking unrelated current capabilities |
| Direct-use zero belongs to an indirect dependency | Preserve the non-use value finding but block removal with the dependency |
| Recommendation generated but no action taken | Show recommendation only; no receipt |
| Completed action authority later changes | Keep the receipt as history, not current evidence |
| User action targets stale census identity | Reject and require refreshed confirmation |
| User closes browser during action | Continue transaction and expose status by intent ID |
| Duplicate POST | Idempotently return the existing intent |
| Halt, pause, or recovery fence active | Reject mutating intent |
| Personal skill has unrelated dirty work | Refuse mutation and preserve worktree |
| Dependency inventory incomplete | Disable archive controls and explain why |
| Plugin has one unknown capability | Block automatic whole-plugin disable |
| Individual plugin disable unsupported | Show recommendation; disable control explains platform limit |
| Settings change concurrently | Preserve all versions and enter recovery-required state |
| Archive commit fails | Restore working tree and write no success receipt |
| Dashboard data malformed | Show unavailable and disable actions |
| User disposition expires | Recompute recommendation; do not execute immediately from stale data |
| Keep or Pin arrives after automatic authorization | Refuse dispatch as stale; require a new decision and authorization |
| Snapshot capture sees an active run, held writer lock, or less than ten minutes to known interval eligibility | Exit immediately without waiting or copying |
| Snapshot capture exceeds 30 seconds | Delete the incomplete snapshot and release the writer lock |
| Source generation or digest changes during capture | Reject and remove the incomplete snapshot |
| Preview input resolves outside its snapshot root | Refuse startup; the serving process never reads the live path |
| Preview input changes after startup verification | Per-request verification refuses the request and requires a new snapshot |
| Preview receives a mutating request | Reject it before intent creation or transaction dispatch |

## Hard invariants

1. Every enabled skill receives a recommendation or an explicit insufficient
   information result.
2. Provenance never counts as value evidence.
3. A recommendation never counts as an action receipt.
4. Missing or inconclusive evaluation never becomes a passing evaluation.
5. Missing usage never becomes zero usage. Settled 30-day non-use always names
   its active-tail exclusion and never claims complete coverage.
6. Every automatic personal mutation targets only
   `dreaming_managed` or `legacy_machine`.
7. User-created and unknown-origin personal skills require explicit user
   action.
8. Plugin files are never edited.
9. Individual plugin-skill disablement requires a version-qualified native
   control.
10. Whole-plugin disablement considers every skill and non-skill capability.
11. Every disable or archive has a verified restore path before mutation.
12. Pins and dependencies block automatic removal.
13. Dashboard handlers never mutate managed roots or settings directly.
14. Every mutating dashboard request binds the current target, decision,
    census, policy, disposition set, and one-time confirmation nonce.
15. Halt, pause, recovery-required, stale identity, and incomplete authority
    fail closed.
16. All action history remains visible after later recommendations change.
17. The serving preview cannot resolve, read, write, dispatch to, or fall back
    to live state, skill, settings, transaction, or scheduler paths. The
    separate capture operation may read live source files only after proving no
    live run is active and acquiring the writer lock without waiting. It holds
    that lock for at most 30 seconds, never begins within ten minutes of known
    interval eligibility, and never writes through the live boundary.
18. Preview mode never registers or changes a launchd owner.
19. Stable unread transcript work whose modification time intersects a decision
    window blocks zero-use authority until collection drains it. Read and parse
    failures remain visible and retryable; they stop blocking a window only
    after their modification time predates it, never through guessed
    attribution.
20. An unmapped historical name blocks only capabilities it could identify;
    indirect dependencies block removal even when direct usage is zero.
21. Every automatic removal-affecting action binds and freshly re-derives the
    complete decision-coverage object for its required window under the writer
    lease, authorizes only the newly appended decision, and dispatches before
    releasing that lease.

## Migration

1. Add portfolio decision records in report-only mode.
2. Render the new portfolio table and decision details while retaining the old
   estate API fields for compatibility.
3. Backfill one decision for every enabled canonical capability.
4. Extend the existing usage receipt with the collection watermark and
   per-pending-file reason, identity, modification time, and bytes. Join
   current evaluation receipts and usage summaries, derive bound
   per-capability 30- and 60-day decision coverage, and preserve the existing
   global coverage facts unchanged.
5. Generate individual plugin-skill recommendations.
6. Add the human decision queue with all controls disabled.
7. Qualify intent creation, idempotency, authentication, stale-state refusal,
   and action routing in fixtures.
8. Enable Run evaluation and Keep actions.
9. Enable explicit personal archive and restore through existing transactions.
10. Enable whole-plugin disable and restore through existing qualified
    settings transactions.
11. Enable automatic machine-created pruning only after every enabled
    capability has a report-only decision whose inputs are current or
    explicitly `insufficient_information`, and two consecutive daily runs
    produce zero disagreement between recomputed decisions and dry-run action
    receipts. New or invalidated capabilities return the system to report-only
    mode until they satisfy the same gate.
12. Remove the old combined recommendation/action table after compatibility
    readers no longer require it.

Individual plugin-skill disable remains gated until an installed Copilot
version passes the native-control qualification.

## Rollback

Rollback is configuration-first:

1. Stop any foreground preview process and prove its port is closed.
2. Remove the preview worktree. Retain its local branch until integration or
   explicitly delete it after its commits are no longer needed.
3. Delete the private snapshot and manifest because they are disposable,
   non-evidentiary copies. Retained browser proof may keep rendered captures
   but never the copied governance state.
4. Disable dashboard mutation endpoints.
5. Disable automatic portfolio actions.
6. Restore the prior read-only estate presentation.
7. Continue reading new decision and user-intent records as inert history.
8. Restore disabled plugins through their ordered settings receipts.
9. Restore archived skills through their Git-backed retirement records.
10. Preserve evaluation, usage, recommendation, and action evidence.

Rollback never deletes user dispositions, decisions, receipts, retirement
history, or recovery state.

Fail-closed rollback proof requires:

- disabled HTTP mutation endpoints return method-not-allowed;
- no automatic personal or plugin action can be authorized;
- prior read-only estate routes remain available;
- plugin restore returns exact effective capabilities;
- personal restore returns the exact archived package;
- unrelated settings and Git work remain unchanged.
- the preview port is closed, its worktree is absent, and its disposable
  snapshot is absent without deleting retained browser proof or branch commits.

## Acceptance criteria

- Every enabled skill appears once in the portfolio table.
- Every personal skill receives a value recommendation regardless of origin.
- User-created and unknown-origin skills show recommendations but are never
  changed automatically.
- Every plugin skill receives an individual recommendation.
- Every plugin receives a complete-package recommendation.
- Unsupported individual plugin disablement is visible and unavailable, not
  silently simulated.
- Evaluation status, currency, cases, and receipts are visible for every
  evaluated skill.
- Verified 7, 30, and 90-day usage and last use are visible or explicitly
  Unknown.
- Zero-use skills distinguish complete zero, settled 30-day non-use with
  active tails excluded, stable collection backlog, and identity ambiguity.
- Every decision record binds its decision-window coverage, usage receipt and
  watermark, excluded active tails, relevant stable backlog, terminal
  failures, and target-scoped identity blockers.
- Unmapped retired names do not make unrelated current skills unknown.
- Direct-use zero never overrides a complete dependency or enabled plugin
  member relationship.
- Supporting occurrences are shown separately from evaluation and usage.
- Recommendation reasons are plain and name the deciding facts.
- Recommendation history is separate from completed action receipts.
- The dashboard has a filterable human decision queue.
- User Keep, Evaluate, Archive, Restore, Disable plugin, and Restore plugin
  actions use governed transactions.
- Automatic withdrawal, archive, or consolidation affects only verified
  machine-created personal skills.
- A plugin package is automatically disabled only when every capability and
  dependency gate passes.
- Every archive and disable is reversible and verified.
- Halt, pause, stale state, conflicts, incomplete evidence, and recovery state
  block mutation.
- A preview can render the report-only portfolio from a verified private
  snapshot while the installed activation, schedule definition, launchd
  inventory, capacity branch tip, and tracked working tree remain unchanged.
- Any live-state changes during the preview window are bound to retained run
  records from the installed capacity owner, with no live path open in the
  preview process tree.
- Preview mutation requests fail before user-intent creation or transaction
  dispatch.

## Deterministic check contract

### PORT-CHK-PREVIEW: Isolated report-only preview

- **Protects:** Parallel dashboard development cannot disturb capacity proof or
  create a second mutation or scheduling owner.
- **Setup:** Record the capacity branch tip and tracked status, complete
  launchd label and plist inventory, installed plist bytes, and activation
  generation. Prove capture refuses both an active capacity run and a held
  writer lock. Prove it holds an acquired lock through final validation,
  deletes an incomplete snapshot at its 30-second deadline, and refuses to
  start within ten minutes of known interval eligibility. Then create a stable
  manifested snapshot in a private root, configure preview state and data
  roots, and render the portfolio on a non-installed port. After startup, try
  changing, replacing, and symlinking a snapshot file, a live-root fallback,
  and every mutating endpoint. If a natural capacity tick occurs during the
  preview window, bind each run-count and live-state delta to its retained
  installed-owner run record.
- **Pass:** Valid snapshot data renders; active or locked capture exits without
  waiting or copying; source changes reject the capture; post-startup invalid
  or changed inputs fail closed on the next request; mutation requests create
  no intent or transaction; capture holds the writer lock through acceptance
  or deletion but never longer than 30 seconds; the preview process has no
  live-root path open;
  capacity branch tip and tracked status, installed plist bytes, activation
  generation, and the complete launchd label and plist inventory remain
  unchanged. Every live-state or run-count delta is attributable to a retained
  run from the already-installed capacity owner.
- **Failure:** The preview reads or writes live state, accepts an action,
  starts an installed helper, adds, removes, or changes a launchd label or
  plist, changes the capacity branch or tracked tree, claims an unattributed
  live-state delta, or continues after its snapshot identity changes.
- **Why:** It proves the preview is a separate read-only development surface,
  not an unreviewed second Dreaming owner.

### PORT-CHK-01: Complete recommendation coverage

- **Protects:** Every enabled skill is judged.
- **Setup:** Seed personal, Dreaming, plugin, builtin, duplicate physical, and
  unresolved instances.
- **Pass:** Exactly one decision exists per enabled canonical capability;
  unresolved instances remain explicit and do not create false capabilities.
- **Failure:** An enabled capability is omitted or a physical duplicate is
  counted twice.
- **Why:** It proves the portfolio has no hidden origin-based exclusion.

### PORT-CHK-02: Value and authority separation

- **Protects:** Safety limits do not become keep evidence.
- **Setup:** Give identical failing evaluation and zero-use evidence to one
  machine-created and one user-created skill.
- **Pass:** Both receive the same `disable_candidate` value recommendation;
  only the machine-created skill receives automatic mutation authority.
- **Failure:** The user-created skill is labeled keep or the automatic action
  is authorized for it.
- **Why:** It proves provenance controls action, not judgment.

### PORT-CHK-03: Evaluation and usage precedence

- **Protects:** Decisions follow the declared policy.
- **Setup:** Cover passing, failing, stale, missing, and inconclusive
  evaluations with recent use, complete non-use, settled 30-day non-use,
  active recent tails, stable budget-blocked transcripts, read and parse
  failures inside and outside the decision window, and a stable
  budget-deferred file whose modification time predates the window. Include
  target-specific and unrelated unmapped names, declared rare cases,
  redundancy, pins, and direct or indirect dependencies. Assert each decision
  record contains the source-receipt identity, watermark, window,
  excluded-tail facts, relevant stable-backlog facts, pending failures, and
  identity blockers used to derive its state.
- **Pass:** Every matrix row produces the specified value state and reason
  codes; only recent active tails can produce `settled_zero_30d`; stable
  backlog intersecting the decision window and target identity ambiguity
  remain blocked; an older stable deferral does not block the current window;
  unrelated retired names do not block; pins and dependencies preserve but do
  not hide a regression.
- **Failure:** Missing evidence becomes positive evidence, a critical
  regression is labeled useful, a stable unread file becomes non-use evidence,
  an unrelated old name blocks every skill, or protection replaces its value
  state.
- **Why:** It proves the primary evidence model.

### PORT-CHK-04: Recommendation is not evidence or receipt

- **Protects:** Dashboard truthfulness.
- **Setup:** Seed recommendation-only, running, committed, historical, and
  recovery-required records.
- **Pass:** Recommendations appear only in the decision table; completed
  transactions appear only in action history with receipts.
- **Failure:** A recommendation appears in an Evidence column or shows a
  receipt.
- **Why:** It proves the misleading repeated recommendation state is removed.

### PORT-CHK-05: Plain terminology

- **Protects:** User comprehension.
- **Setup:** Render portfolio, candidate, activity, and evidence pages.
- **Pass:** The banned ambiguous labels appear only in a glossary or backward
  compatibility payload, and every visible replacement matches this design.
- **Failure:** Unqualified review, shadow candidate, Evidence recommendation,
  or Observed recommendation appears in the UI.
- **Why:** It prevents the product from recreating the same conceptual
  confusion.

### PORT-CHK-06: Individual plugin recommendations

- **Protects:** Plugin packages do not hide low-value skills.
- **Setup:** Seed a plugin with useful, unused, failing, and unknown skills plus
  a non-skill capability.
- **Pass:** Each skill receives its own recommendation and the package decision
  names every blocker.
- **Failure:** The plugin has only one undifferentiated keep recommendation.
- **Why:** It proves per-capability judgment.

### PORT-CHK-07: Individual plugin disable gate

- **Protects:** Unsupported per-skill mutation fails closed.
- **Setup:** Mandatory: run with no qualification and a failed qualification.
  Conditional register-only: when the installed CLI exposes a candidate native
  control, run a passing version-bound qualification.
- **Pass:** Mandatory cases remain recommendation-only. If the conditional
  qualification is registered, it changes only the exact skill and restores
  it.
- **Failure:** Plugin files are edited, sibling capabilities change, or an
  unqualified version enables the control.
- **Why:** It proves "if possible" means proven native support, not simulation.

### PORT-CHK-08: Whole-plugin decision

- **Protects:** One useful or unknown capability blocks package disablement.
- **Setup:** Exercise complete packages with all removable capabilities and
  packages with one useful, unknown, or dependent capability.
- **Pass:** Only the all-removable package becomes disable-eligible.
- **Failure:** A partial judgment disables the plugin.
- **Why:** It proves complete-package authority.

### PORT-CHK-09: Human decision intent

- **Protects:** Dashboard actions are explicit and bound.
- **Setup:** Submit valid, duplicate, stale, unauthenticated, cross-origin,
  malformed, halted, paused, and recovery-blocked intents; authorize an
  automatic archive, then accept Keep before dispatch.
- **Pass:** Valid intent is accepted once; duplicate is idempotent; every other
  case is rejected without mutation; the later Keep invalidates the automatic
  authorization.
- **Failure:** A stale or unauthenticated request creates authority.
- **Why:** It proves the dashboard is a governed user surface.

### PORT-CHK-10: Personal archive and restore

- **Protects:** User-authorized and automatic archives remain recoverable.
- **Setup:** Withdraw and later archive machine-created automatically, archive
  user-created by explicit intent, attempt automatic user-created archive, and
  attempt to invoke the curator adapter without `estate-action.py`.
- **Pass:** First two commit exact scoped deletion plus retirement records; the
  third and fourth are refused; both completed archives restore exactly.
- **Failure:** User content changes automatically or restore loses bytes.
- **Why:** It proves the requested authority boundary.

### PORT-CHK-11: Plugin disable and restore

- **Protects:** Whole-plugin actions preserve settings and capabilities.
- **Setup:** Execute disable and restore with unrelated settings, concurrent
  edits, runtime mismatch, and stacked transactions.
- **Pass:** Successful paths change only the exact plugin key; conflicts retain
  all bytes and require recovery.
- **Failure:** Unrelated settings are lost or capabilities do not match the
  receipt.
- **Why:** It proves reversible non-destructive plugin pruning.

### PORT-CHK-12: Automatic aggressive pruning

- **Protects:** Aggressive policy remains evidence-bound.
- **Setup:** Seed complete and settled 30-day non-use, complete and settled
  60-day non-use for withdrawn skills, a use in days 31 through 60, recent
  active tails, stable collection backlog, read and parse failures inside and
  outside each decision window, failing evaluation, redundancy, missing
  evidence, pins, direct dependencies, indirect plugin-member dependencies,
  and a new use that arrives between recommendation and dispatch. Exercise the
  fresh-use race separately for withdrawal, archive, consolidation, individual
  plugin disablement, and whole-plugin disablement.
- **Pass:** Eligible machine-created disable candidates withdraw; only skills
  withdrawn for 60 days with current decision-grade 60-day zero archive;
  settled-zero decisions name their active-tail exclusion; failures block only
  intersecting windows; and protected, stable-backlog, stale-action, and
  user-decision targets do not mutate. Under one writer lease, every automatic
  action kind collects again, appends and binds a new current decision, then
  dispatches only if that new decision grants authority. A seeded active-tail
  size change alone does not block an otherwise eligible action, while every
  seeded fresh use prevents mutation and creates no mutation receipt.
  Automation remains report-only until every capability has a current or
  explicit insufficient-information decision and two consecutive daily
  dry-run comparisons agree.
- **Failure:** Missing or stable unread data authorizes removal, a dependency
  is treated as unused, a fresh use fails to cancel dispatch, or eligible
  machine-created work remains permanently recommendation-only.
- **Why:** It proves both aggression and safety.

### PORT-CHK-13: Browser decision workflow

- **Protects:** The real dashboard supports understanding and action.
- **Setup:** Drive desktop and 390-pixel views with a complete mixed portfolio.
- **Pass:** A user can find a failing skill, understand why, inspect evaluation
  and usage, submit an action, and observe its final receipt without reading
  raw JSON.
- **Failure:** The workflow requires interpreting recommendation/evidence
  jargon or using the terminal.
- **Why:** It proves the product outcome rather than API presence.

### PORT-CHK-14: Rollback

- **Protects:** The critical boundary can be disabled safely.
- **Setup:** Roll back after decisions, user dispositions, a personal archive,
  and a plugin disable.
- **Pass:** Mutation endpoints and automation are disabled, read-only
  presentation works, archived and disabled targets restore, and history
  remains.
- **Failure:** Rollback deletes evidence, leaves a target disabled, or permits
  new mutation.
- **Why:** It proves fail-closed reversibility.

## Definition of Done: Aggressive skill portfolio governance

- [ ] The isolated preview passes PORT-CHK-PREVIEW and is available for
      browser review without changing installed capacity state.
- [ ] Every enabled skill receives a plain-language value recommendation
      independent of mutation authority.
- [ ] Evaluation and verified usage are the primary keep-or-prune evidence.
- [ ] Per-capability 30-day decision coverage permits settled non-use with
      recent active tails excluded, while stable unread transcripts and
      target identity ambiguity remain blocking.
- [ ] The existing usage receipt classifies every pending transcript and every
      decision binds the collector watermark, decision window, exclusions,
      relevant backlog, failures, and identity blockers used to authorize it.
- [ ] Archive requires a current decision-grade 60-day zero result in addition
      to 60 continuous days withdrawn.
- [ ] Supporting occurrences, recommendations, actions, and receipts are
      presented as distinct concepts.
- [ ] Every plugin skill and complete plugin package receives a recommendation.
- [ ] Individual plugin-skill disablement is either version-qualified and
      reversible or explicitly unavailable.
- [ ] Automatic personal mutations remain limited to verified machine-created
      skills.
- [ ] User-created and unknown-origin skills can be acted on only through an
      explicit user intent.
- [ ] The dashboard provides a filterable decision queue, detail evidence, and
      governed Keep, Evaluate, Archive, Restore, Disable plugin, and Restore
      plugin actions.
- [ ] Aggressive non-use, regression, and redundancy policy is active without
      converting missing evidence into removal authority.
- [ ] Every action passes identity, dependency, pin, halt, recovery, and
      restore checks.
- [ ] Deterministic checks PORT-CHK-01 through PORT-CHK-14 pass, with the
      conditional native-control branch of PORT-CHK-07 required only after a
      candidate installed Copilot version exposes that control.
- [ ] Desktop and narrow browser proof demonstrates the complete human
      decision workflow.
- [ ] Installed plugin disable, restore, personal archive, and restore proof
      passes on the Mac mini and MacBook.
- [ ] Paired implementation review has no unresolved in-scope must-fix finding.
- [ ] Rollback proof disables mutation, restores targets, and preserves
      retained history.
- [ ] The reviewed design, implementation, and proof references are committed
      locally; nothing is pushed.
