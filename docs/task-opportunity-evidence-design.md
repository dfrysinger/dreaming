# LLM task opportunity and skill learning design

## Objective

Use Dreaming's existing transcript review path to find work where a skill could
have helped, accumulate evidence across independent tasks, create or improve a
skill from the supporting transcripts, evaluate it, deploy it, and later retire
or repair it based on observed use and triggering.

The MacBook Pro remains the transcript source and learned-skill destination.
The Mac mini remains the sole scheduler, model-analysis owner, evidence owner,
evaluator, and deployment authority.

This design follows the principles in
`skills/skill-review/SKILL.md#dreaming-design-principles`.

## Lane

**Systemic.** This change replaces the scheduler's raw-session review input
with a durable profile-derived decision flow, changes the unit of recurrence
from a source session to a task occurrence, and changes how bounded model
capacity is spent and reported across profiling, review, candidate lifecycle,
dashboard, installation, and rollback.

## Non-goals

- Do not add another transcript transport, corpus walker, or scheduled owner.
- Do not make a source session the recurrence unit; sessions may remain active
  for months and contain many independent task occurrences.
- Do not use phrase matching or a fixed taxonomy as the primary source of task
  meaning.
- Do not require every transcript to produce a skill opportunity.
- Do not let one task occurrence create a new skill.
- Do not create a second catalog-audit model stage. The existing full reviewer
  owns catalog and observed-skill-load comparison for reusable profiles.
- Do not reserve a fixed raw input batch and leave model capacity unused when
  later eligible rows exist.
- Do not treat skill use, task opportunity, evaluation quality, or dependency
  protection as substitutes for one another.
- Do not enable automatic skill mutation until the report-only learning loop
  works end to end.

## Constraint provenance and reframe gate

| Constraint | Provenance and binding evidence or owner | Protects | Revisit when |
| --- | --- | --- | --- |
| Mac mini is the sole scheduled owner | User-owned two-computer topology and existing installed ownership | No overlapping writers or competing transcript consumers | The owner explicitly changes the machine topology |
| Existing authenticated session-source path is reused | User decision and working cross-machine boundary | No duplicate transcript transport or corpus | The existing path cannot deliver exact bounded revisions required by a supported task |
| Task meaning and semantic procedure grouping remain LLM-owned | User decision that deterministic transcript parsing is usually the wrong approach | Real semantic interpretation across varied wording | A measured model failure cannot be corrected through prompt, schema, or bounded review |
| Three independent task occurrences authorize new-skill drafting | Product-owner decision on 2026-08-27 | One task, retry, or clarification cannot create a skill | Natural evidence shows persistent false grouping or useful procedures routinely expire before reaching three |
| All three authorizing occurrences are within the current rolling 30-day window | Product-owner decision aligning creation evidence with 30-day recent-use policy | Old evidence cannot revive a skill while current evidence says it is unused | The portfolio policy window changes or measured recurrence is too sparse to represent current demand |
| `max_reviews_per_run=25` remains the initial expensive-review ceiling | Installed capacity work order `docs/dreaming-review-backlog-capacity-design.md` and its retained natural-run proof | Bounded model cost and scheduled duration | Eligible profile backlog does not burn down, the pass has unused safe time, or measured cost permits a larger bound |
| `max_profiles_per_run=100` and 600 profile seconds are provisional first-install bounds | Product-owner acceptance of separate profile capacity plus `scripts/configure-adapters.py`; installed proof must measure the resulting stage cost | Profiling cannot consume the entire owner pass | Profiling backlog does not burn down, measured calls show a better capacity split, or the full review stage cannot fill |
| Profile configuration rejects more than 500 operations or 1,800 profile seconds | Existing fail-closed configuration safety cap; not a throughput target | A malformed configuration cannot make profiling unbounded | Measured safe operation requires a larger ceiling and the installed deadline is redesigned |
| The installed owner pass has a 3,600-second hard deadline inside the four-hour schedule | Installed `daemon-pass.sh` one-hour owner backstop, four-hour natural cadence, and measured review cost in `docs/dreaming-review-backlog-capacity-design.md`; that work order's older 1,800-second standalone-pass assertion is superseded for the profile-derived generation | Both bounded model stages and settlement can complete without scheduler overlap | Natural proof approaches the deadline, the four-hour cadence changes, or either stage's measured cost no longer fits |
| Eligibility checks do not consume operation slots; started model calls do | User decision that every bottleneck should fill while eligible backlog exists | Cached or stale rows cannot waste scarce model capacity | A provider charges materially for an eligibility operation currently treated as free |

**Reframe status: CLEAR.** The current evidence identifies one direct design:
reuse immutable task-profile receipts, make the existing full reviewer consume
reusable profiles, persist below-threshold observations in the existing
candidate lifecycle, and make both loops work-conserving. No additional
transport, scheduler, semantic classifier, or queue authority is required.

Implementation returns to this document before adding a new subsystem, before
preserving a raw-session review path for compatibility, or when any revisit
condition above fires. The reframe record must answer which user-visible
outcome is blocked, which constraint caused it, what invariant would fail if
the constraint changed, the simplest design without it, and which option has
fewer trusted components.

## Existing boundary

Dreaming already has the required transcript path:

1. The Mac mini's scheduled owner lists stable sessions through the authenticated
   MacBook Pro session-source adapter.
2. The source adapter returns one bounded, normalized, content-addressed session
   snapshot.
3. A Mac mini review executor sends that snapshot to an LLM.
4. The review result may patch an existing skill or retain a shadow proposal.
5. Reviewed skill bundles are published back to the MacBook Pro.

Task opportunity uses this path. It does not collect a second transcript copy or
send a separate aggregate from the MacBook Pro.

The transcript was originally written to an LLM and is already retained as plain
text on a user-owned computer. Passing a bounded snapshot to an LLM on behalf of
the same personal Dreaming system is expected behavior. Durable records should
still prefer concise evidence and event references so dashboards and history stay
useful.

## Reuse contract

This design reuses:

- `DREAMING_DATA_DIR/task-profiles/v1/<receipt-sha256>.json` for immutable,
  content-addressed task-profile receipts;
- `task-profile-index.json` only as the replaceable lookup from exact session
  revision and executor identity to an immutable receipt;
- the existing review attempt, transaction, result, and review-ledger records
  for catalog-aware reviewer execution and terminal disposition;
- the existing candidate lifecycle's `collecting` state for no-covering-skill
  procedure groups below recurrence;
- the existing evaluation, publisher, installer, halt, generation self-test,
  rollback, and restore boundaries after recurrence.

No second durable task-profile queue is introduced. The eligible review view is
derived from immutable reusable profiles minus their current validated review
dispositions. Candidate lifecycle records persist semantic groups and their
observation identities across scheduled runs.

New schema fields or records are required only where the existing review ledger
cannot prove which `profile_id`, task key, profile receipt, catalog identity,
observed skill-load trace, canonical task-occurrence identity, and recurrence
group a disposition consumed.

A profile audit disposition is historical truth about one task occurrence, not
a standing claim that the catalog will never change. Its immutable identity
binds:

- disposition schema and reviewer-contract versions;
- profile and receipt identities;
- exact catalog and tombstone identities observed by the reviewer;
- exact task-specific skill-load trace identity;
- boundary relation and canonical occurrence identity;
- terminal catalog outcome and optional recurrence group.

Ordinary catalog publication does not invalidate that occurrence's audit or
reopen the entire retained corpus. New occurrences are reviewed against the
new catalog. A disposition becomes ineligible only when its own profile or load
trace is proven wrong, or when a migration explicitly supersedes its schema or
reviewer-contract version. Such re-audits use a separately reported repair
backfill and never displace first-time eligible profile audits inside the 25
ordinary review operations.

## Durable profile and recurrence identity

One immutable task-profile receipt may contain several task profiles from one
session snapshot. Each profile has:

- `profile_id`, binding the normalized profile content and source session;
- `task_key`, binding the source session and exact ordered supporting event IDs;
- `procedure_fingerprint`, binding the normalized model-proposed procedure;
- the immutable receipt identity, exact source revision, snapshot, model, prompt
  contract, and observation time.

The immutable receipt remains available after later scheduled runs and after
the source session grows. Reprofiling a newer revision must recognize an
already indexed task key and must not create a second occurrence for it.

Catalog-aware review writes a durable disposition bound to the exact
`profile_id` and receipt. It also binds a canonical task-occurrence identity.
For an exact previously seen task key, the owner reuses the existing occurrence
identity without a model call.

If a later model expands, contracts, merges, or splits supporting event ranges,
the reviewer receives every overlapping prior profile from that source session
and returns one boundary relation for each new profile:

- `same-occurrence`, naming exactly one prior canonical occurrence;
- `new-occurrence`, asserting one distinct user goal;
- `boundary-conflict`, when the profile merges several prior goals, mixes a
  prior goal with a new goal, or cannot be mapped one-to-one.

Multiple split profiles may alias the same canonical occurrence and therefore
still count once. A merged or otherwise conflicted profile is durably audited
but is ineligible for recurrence. It returns the exact source revision to a
bounded candidate-blind correction profile attempt that is instructed only to
separate task boundaries, not shown the skill catalog or candidate state.
The correction attempt has its own immutable contract identity, consumes one
profile-operation slot when started, and supersedes rather than rewrites the
conflicted profile set. If the current correction contract still cannot produce
one-to-one boundaries, the revision becomes `boundary-unresolved` and is not
retried until the source revision or correction contract changes. Recurrence
cannot advance until replacement profiles each have a one-to-one boundary
relation. Every alias and supersession remains inspectable.

A no-covering-skill disposition also records the candidate lifecycle and
semantic group chosen by the reviewer. The candidate lifecycle accumulates
distinct canonical task-occurrence observations across any number of scheduled
runs and source-session containers.

Three task occurrences authorize drafting only when all three observation times
fall within the current rolling 30-day window. `occurred_at` is the normalized
source timestamp of a model-selected `goal_event_id`, which must be one of the
profile's supporting user-event IDs and is validated against the exact
snapshot. The owner compares it with the pass's retained `decision_at`; a
missing or future occurrence timestamp fails validation. Profiling or review
time never substitutes for task-occurrence time. Older observations remain
inspectable history but do not count toward current authoring authority. The
same canonical task occurrence can appear only once in a recurrence group.
Different task keys from one session may count only when the reviewer confirms
their supporting events represent separate user goals; different sessions are
neither required nor sufficient by themselves.

## Learning loop

### 1. Profile the task before showing the skill library

The first model pass receives:

- the bounded normalized session snapshot;
- the source session identity and exact revision;
- the task-profile schema;
- no skill catalog, candidate name, usage state, evaluation state, recommendation,
  or retirement state.

It returns zero or more task profiles. Each profile contains:

- exact supporting source event IDs;
- an abstract task summary;
- whether the task contains reusable procedure value;
- a procedure trigger, outcome, ordered actions, and exclusions when reusable;
- a semantic procedure fingerprint input;
- confidence and sensitivity markers;
- whether the task completed, failed, or remained unresolved.

The owner validates the event references against the exact snapshot and computes
all content identities. Model-produced hashes are never trusted.

This pass is candidate-blind so existing skills cannot manufacture evidence that
their own category occurred.

`confidence`, `sensitive_source`, and `task_state` are retained evidence and
prioritization signals, not automatic rejection thresholds. A failed or
unresolved task can still demonstrate a useful recovery procedure, and a
sensitive source can still yield an abstract procedure without retaining source
text. Any future hard admission rule for these fields requires an explicit
design change backed by observed failures; deterministic code must not invent a
semantic threshold.

### 2. Retain independent opportunity evidence

Each accepted profile becomes an immutable opportunity observation bound to:

- source session identity;
- source revision and snapshot identity;
- source event IDs;
- one initiating `goal_event_id` and its owner-derived `occurred_at`;
- model and prompt contract identities;
- owner-computed procedure fingerprint;
- observed time;
- task key;
- completion state.

Evidence from one task occurrence counts once. Clarifications, retries, tool
calls, and assistant turns inside that occurrence do not increase recurrence.
A session is only a container: one continuously active session may contain many
independent task occurrences over days or months.

Observations are independent when they have distinct canonical
task-occurrence identities whose supporting evidence does not represent the
same user goal or a continuation, retry, or clarification of it. They may come
from the same source session. Reprofiling a later revision of one long-running
session must preserve or alias the occurrence identity for an already observed
task and add only genuinely new task occurrences. The task-profile model
proposes task boundaries and semantic similarity; deterministic owner code
validates exact event identity, durable aliases, non-duplication, and recurrence
counts.

The candidate-aware reviewer owns the semantic decision that differently worded
profiles belong to the same reusable procedure. Once it chooses the same skill
artifact, the owner derives one canonical lifecycle procedure fingerprint from
that artifact while retaining each matched profile's original receipt, task key,
session, and source-procedure identity as evidence. Deterministic code must not
infer semantic equivalence from text similarity alone.

### 3. Review reusable profiles against the skill estate and accumulate evidence

Every accepted reusable task profile enters the existing full skill reviewer.
The existing-skill audit is a responsibility of that reviewer, not a separate
unbounded agent or a prerequisite produced elsewhere. The reviewer sees the
validated profile, the exact skill-load trace for the task, prior reusable
profile groups, and the current skill catalog. It classifies the task as:

1. the correct existing skill loaded;
2. an existing skill should have loaded but did not;
3. the wrong or an incomplete skill loaded;
4. no existing skill covers the reusable procedure.

The first three outcomes may create a report-only trigger, description, or
procedure-repair recommendation without waiting for recurrence. They do not
create a new skill. For the fourth outcome, the same reviewer semantically
matches the profile to a prior no-covering-skill procedure group or starts a new
group. Neither path reruns transcript profiling, creates a second durable queue,
or counts one task occurrence more than once.

A reusable procedure becomes authoring-ready when:

- at least three verified independent observations have been semantically
  matched to the same reusable procedure;
- the observations have at least three distinct canonical task-occurrence
  identities, whether they occur in one long-running session or several
  sessions;
- all three authorizing observations are within the current rolling 30-day
  window;
- no tombstone, covering lifecycle, or explicit user disposition blocks it.

The candidate-aware reviewer owns the semantic grouping decision and records
why each observation belongs or does not belong. Deterministic code validates
source identities, canonical occurrence identities, aliases, and accepted
counts; it does not group profiles by text similarity. The opportunity
observations feed the existing shadow-candidate lifecycle only after this
recurrence gate.

Review fixes stage a new immutable candidate revision without creating another
opportunity observation. The exact successor makes the prior recommendation
stale and must be rebound to the retained recurrence evidence before evaluation
or deployment can continue.

One-off tasks remain evidence but do not create a candidate.

### 4. Apply the learning to the skill estate

Every reusable task profile enters one catalog-aware expensive review. That
single review performs the existing-skill audit and, when no skill covers the
task, semantic recurrence grouping. It receives:

- the exact bounded transcript snapshot;
- the validated task profile;
- the exact skill-load trace observed for that task occurrence;
- overlapping or aliased prior profiles needed to prevent recounting;
- the current skill catalog and tombstones;
- artifact-routing and writing rules.

It chooses one outcome:

1. patch the skill that should already cover the task;
2. add a support file to an existing umbrella;
3. retain a no-covering-skill procedure group while it remains below recurrence;
4. create or update a shadow candidate when the current review supplies the
   third independent observation;
5. retain a recommendation;
6. retain no durable learning.

The full reviewer may read the original transcript evidence. It must generalize
the procedure and avoid copying credentials, private code, customer data, or
source-specific details into the skill.

Any profile-informed create, patch, or support-file artifact remains report-only
until recurrence and evaluation pass. Matching an existing skill does not grant
immediate mutation authority.

### 5. Evaluate the candidate

An authoring-ready candidate is evaluated on three separate questions:

1. **Triggering:** Does the description load the skill for relevant tasks and
   leave it unloaded for unrelated tasks?
2. **Task performance:** Does using the skill make the observed task class more
   reliable, correct, or efficient than the baseline?
3. **Overall regression:** Does adding the skill harm unrelated task performance,
   routing, cost, or latency?

Evaluation uses the supporting transcript evidence to design representative
tests. Test fixtures may be synthetic when private details are unnecessary, but
the task shape comes from observed work.

A candidate deploys only after all required evaluation gates pass.

### 6. Learn from deployed behavior

After deployment, Dreaming keeps skill use and matching task opportunities as
separate signals:

- matching opportunities with successful skill loads support usefulness;
- matching opportunities without skill loads indicate a triggering-description
  problem;
- skill loads without matching opportunities indicate over-triggering;
- neither use nor matching opportunities over a settled window supports
  reversible retirement;
- a task-performance regression supports repair or disablement.

Retirement remains subject to provenance, dependency, pin, restore, halt, and
writer-lease rules. Hand-made and plugin-owned skills retain their existing
authority boundaries.

## Throughput

Task profiling is cheaper than full skill review and has its own bounded queue.
The existing 25-attempt limit remains a limit on expensive full reviews, not on
lightweight task profiles.

The profiler runs until one of its explicit elapsed-time, token, session, or
model-operation budgets is reached. Every exact session revision receives one
terminal profiling disposition: existing receipt reused, reusable procedure
found, no reusable procedure, stale revision, malformed result, model failure,
or deferred. One-off and no-learning sessions are recorded without consuming a
full-review attempt.

The expensive-review queue is derived from validated reusable task profiles, not
from the raw transcript queue. A raw session cannot consume a review attempt
merely because it was discovered. The reviewer audits every reusable profile
against actual skill loads and the current catalog; new-skill authoring becomes
eligible only when the current review brings a semantically grouped procedure to
three independent task occurrences. The derived view retains the profile receipt, task key, canonical occurrence,
disposition version, prior audit outcome, proposed recurrence group, and
current count.

Both bounded stages are work-conserving:

- profiling scans past cached, already dispositioned, stale, active, or
  otherwise ineligible queue rows until it starts `max_profiles_per_run` new
  model operations, reaches its elapsed or other operation budget, or has no
  eligible unprofiled revision left;
- expensive review scans past cached, already reviewed, stale, superseded, or
  otherwise ineligible profile rows until it starts
  `max_reviews_per_run` reviewer operations, reaches the enclosing pass
  deadline or another explicit operation budget, or has no eligible reusable
  profile left.

Queue rows inspected during eligibility checks do not spend an operation slot.
Once a model operation starts, its success, refusal, malformed result, timeout,
or other terminal failure spends that slot because it consumed the bounded
resource. Neither stage takes one fixed input batch and stops merely because
some members of that batch were cached, stale, or ineligible.

Each run reconciles its accounting:

- every examined queue row is classified as cached, newly attempted, skipped,
  failed, stale, or deferred;
- every new model operation has one terminal result;
- every retained reusable profile is either awaiting catalog audit, awaiting
  recurrence, eligible for expensive review, or already dispositioned;
- cached-receipt validation never increments the new-model-operation count.

When eligible backlog is at least the remaining operation allowance, the run
must either fill that allowance or name the earlier elapsed, token, timeout,
halt, lease, or health bound that stopped it. A run may report unused capacity
only when it proves that fewer eligible inputs existed.

An accounting mismatch makes the profiling pass unhealthy and is visible in
the dashboard. It cannot be summarized as low profile yield.

The first production bounds are `max_profiles_per_run=100`, independently
capped at 500 by configuration validation, and
`max_profile_elapsed_seconds=600`, capped at 1,800. The profiler stops starting
new model operations when either bound is reached and carries the remaining
queue into the next natural run. `max_reviews_per_run` remains capped at 25.
The enclosing installed pass has a 3,600-second hard deadline inside the
four-hour schedule. Installed proof must show that 600 seconds of profiling,
25 started reviews at measured natural cost, accounting, dashboard settlement,
and publication fit that deadline. Failure to fit reopens the capacity design;
the implementation may not normalize predictable truncation as an ordinary
per-run stopping bound. This profile-derived generation supersedes the prior
capacity work order's 1,800-second standalone-core assertion; the installed
wrapper's effective deadline is the authority and must be captured in proof.

A malformed model profile is rejected before it can become task evidence and
is retained as a diagnostic per-session profiling failure. Unaffected sessions
continue through the pass, but the pass reports unsuccessful so the stalled
session cannot look healthy. The failure does not consume a full-review attempt
or authorize recurrence, and the queued source revision remains eligible for a
later independent retry.

The first backfill processes recent stable sessions first. After catch-up, only
new or changed source revisions require profiling.

Profile mode is an optional review-executor capability named
`task-profile-v2`. A valid legacy review executor remains eligible for ordinary
review but is never called with profile arguments unless it advertises that
capability.

## Affected data flow

```text
authenticated session-source revisions
  -> raw discovery queue
  -> stable exact-revision eligibility
  -> candidate-blind profile model operation
  -> immutable task-profile receipt
  -> derived reusable-profile review view
  -> one catalog-aware full review per reusable profile
       -> correct-skill disposition
       -> existing-skill repair recommendation
       -> no-covering-skill candidate lifecycle observation
  -> three current independent task occurrences
  -> shadow candidate
  -> trigger, task-performance, and regression evaluation
  -> reviewed install, publication, rollback, and restore
```

Discovery queue state remains the authority for raw source revisions.
Task-profile receipts remain the authority for observed reusable tasks. Review
dispositions remain the authority for whether a profile needs expensive review.
Candidate lifecycle records remain the authority for recurrence and drafting
readiness. The dashboard projects these sources without creating authority.

## Deterministic responsibilities

Deterministic code owns:

- stable-session and quiet-period admission;
- bounded snapshot rendering;
- source, event, model, and prompt identities;
- schema validation;
- task-key, canonical-occurrence, and procedure-fingerprint computation;
- recurrence counting;
- budgets and queue ordering;
- content-addressed evidence;
- lifecycle transitions;
- deployment, halt, rollback, and restore checks.

The LLM owns:

- understanding task meaning;
- separating distinct tasks in a conversation;
- identifying reusable procedures;
- matching semantically equivalent procedures across varied wording;
- drafting and repairing skill content;
- designing observed-task evaluation cases.

Using deterministic text rules for primary task understanding requires explicit
user approval and a documented reason the LLM path cannot carry the behavior.

## Failure behavior

| Failure | Result |
| --- | --- |
| Session source unavailable | Retain prior evidence and defer new profiling |
| Snapshot malformed or identity mismatch | Refuse the profile operation |
| Model timeout or malformed output | Record a scoped failed attempt and retry under the ordinary budget |
| Event reference absent from the snapshot | Reject that profile |
| Model reports no durable learning | Record the completed profile without creating a candidate |
| Cached receipt is current | Reuse it, record the cached outcome, and spend no profile-operation slot |
| Session grows after a task was profiled | Reuse the exact task key or retain a reviewer-approved alias to the prior canonical occurrence; profile only genuinely new tasks as new occurrences |
| Profile changes before review | Mark that profile revision stale, spend no review-operation slot, and derive the current eligible view |
| Profile merges or ambiguously overlaps prior tasks | Record `boundary-conflict`, spend the started review slot, run at most the current immutable correction contract, and grant no recurrence authority while unresolved |
| Skill-load trace is absent or unbound | Do not start the reviewer; retain an input-incomplete eligibility result until an exact empty-or-populated trace exists |
| Correct existing skill loaded | Record the audited disposition and do not create or repair a skill |
| Existing skill was missed, wrong, or incomplete | Retain a report-only repair recommendation bound to profile and load-trace evidence |
| Similarity uncertain | Retain separate observations until later evidence resolves the match |
| Candidate has insufficient recurrence | Keep it shadow-only |
| Capacity remains while eligible rows exist | Continue scanning; do not stop at the end of an arbitrary input batch |
| Accounting does not reconcile | Mark the pass unhealthy and expose the unmatched identities |
| Trigger evaluation fails | Repair the description and rerun the bounded evaluation |
| Task-performance evaluation fails | Repair or reject the candidate |
| Overall regression fails | Do not deploy |
| Deployed skill misses matching opportunities | Rework its description |
| Deployed skill has no use or matching opportunity | Consider reversible retirement |

## Proof-first rollout

1. Run one private task-profile model call against an existing real bounded
   snapshot.
2. Validate its event references and retain one immutable opportunity observation.
3. Run its catalog-aware review and retain an exact disposition.
4. Retain no-covering-skill observations in the existing candidate lifecycle
   across separate scheduled runs.
5. Prove three independent task occurrences, including multiple occurrences
   from one long-running session, can satisfy recurrence without counting a
   retry or reprofile twice.
6. Draft one real shadow skill or patch from the supporting transcripts.
7. Run triggering, task-performance, and regression evaluation in report-only
   mode.
8. Show the evidence, candidate, evaluation, and recommendation in the private
   dashboard.
9. Harden model budgets, retries, installed configuration, halt behavior, and
   rollback only after the complete report-only loop works.
10. Install one reviewed candidate, prove a natural scheduled run, then retain
   rollback and restore evidence.

## Deterministic checks

### LLM-OPP-01: Existing snapshot boundary

One stable MacBook Pro session is fetched through the existing session-source
adapter and profiled without a second transcript transport.

### LLM-OPP-02: Candidate-blind profile

Changing the installed skill catalog does not change the task-profile prompt or
its supplied context.

### LLM-OPP-03: Exact evidence references

Every retained profile cites ordered event IDs present in the exact snapshot.
Missing, duplicate, or reordered references are rejected.

### LLM-OPP-04: Independent recurrence

Three separately profiled task occurrences with distinct canonical occurrence
identities satisfy the recurrence gate when the reviewer groups them to one
procedure and all three fall within the rolling 30-day window. Two occurrences
do not. Three retries or reprofiles of one occurrence still count once. Three
distinct task occurrences in one long-running session may pass; three session
IDs alone do not prove independence.

An old task profiled today remains old because authority uses the validated
`goal_event_id` source timestamp. A current task profiled after queue delay
retains its source occurrence time. A non-user goal event, missing timestamp, or
event outside the supporting set fails validation.

### LLM-OPP-05: Existing-skill repair

A matching opportunity with no skill load routes to description or content
repair rather than creating a duplicate skill.

### LLM-OPP-06: New-skill lifecycle

A recurring procedure with no existing umbrella creates a shadow candidate,
passes triggering, task-performance, and regression evaluation, then becomes
eligible for deployment.

### LLM-OPP-07: Retirement and missed triggering

No use plus no matching opportunity yields a reversible retirement
recommendation. Matching opportunity without use yields a triggering repair
recommendation.

### LLM-OPP-08: Rollback

Disabling task profiling stops new observations while preserving transcripts,
prior evidence, candidate records, evaluations, deployed skills, and restore
history.

### LLM-OPP-09: Durable cross-run accumulation

Profile one reusable task, stop the run, then process two later independent
occurrences in later runs and different combinations of source-session
containers. The immutable first receipt and candidate-lifecycle observation
remain authoritative, the third review sees count three, and restart or
publication does not reset recurrence.

Reprofile the first task from a later session revision with an expanded or
contracted event range. The catalog-aware reviewer aliases it to the original
canonical occurrence, and the recurrence count remains unchanged.

Merge two prior task ranges into one new profile. The reviewer must return
`boundary-conflict`; that profile cannot add recurrence until a correction
profile attempt produces one-to-one task boundaries. Split one prior range into
two new profiles; both may alias the same occurrence and still count once.

### LLM-OPP-10: Catalog-aware reviewer ownership

Feed a reusable profile and exact observed skill-load trace to the full
reviewer. It records exactly one of correct skill, missed skill, wrong or
incomplete skill, or no covering skill. No separate auditor receipt is required
before the reviewer can start. One valid disposition remains terminal for that
profile occurrence across ordinary catalog publication. An explicit
disposition-schema or reviewer-contract migration can supersede it only through
the separately reported repair backfill.

### LLM-OPP-11: Profile-derived review admission

Mix reusable profiles, no-learning profiles, raw unprofiled sessions, cached
profiles, stale revisions, and already dispositioned profiles. Only current
undispositioned reusable profiles reach the full reviewer. Every other row has
a named non-consuming eligibility result.

Add a pre-cutover raw-session terminal review for the same source revision. It
remains historical and does not suppress the new profile-bound catalog audit.

### LLM-OPP-12: Work-conserving profiling

Place cached, active, stale, and already dispositioned rows before more than the
remaining profile allowance of eligible unprofiled revisions. The run scans
past the ineligible rows and starts the full remaining operation allowance
unless its elapsed or health bound fires. The report names the exact stopping
bound.

### LLM-OPP-13: Work-conserving expensive review

Place cached, stale, and already reviewed profiles before more than 25 eligible
reusable profiles. The run starts 25 catalog-aware reviewer operations. Stale
and cached checks spend no slot; a started operation with a terminal failure
does. Fewer than 25 operations pass only when the report proves eligible input
exhaustion or names an earlier explicit bound.

### LLM-OPP-14: Complete accounting

For successful, malformed, timed-out, cached, stale, no-learning, reusable,
reviewed, recurrence-waiting, and deferred fixtures, independently derive the
expected queue, operation, profile, review, and recurrence totals. The run and
dashboard totals match every identity exactly. Removing any terminal record or
double-counting a cached receipt makes the check fail.

### LLM-OPP-15: Installed deadline, owner, rollback, and restore

Run the installed generation under the four-hour schedule with both model
stages at their configured allowances. The pass completes within its
3,600-second hard deadline, and the proof captures the effective
`DREAMING_PASS_MAX_SECS` or wrapper default as 3,600 so an old 1,800-second
override fails visibly. Exactly one Mac mini owner acquires the writer lease,
and a second owner receives the existing refusal. Roll back, compare the
retained evidence identities byte-for-byte, then restore the corrected
generation and require its generation-bound self-test before ownership resumes.
Any wrong effective deadline, deadline overrun, second lease acquisition,
evidence mutation, or failed self-test is the failure signal.

## Hard invariants

1. An immutable task-profile receipt is never rewritten in place.
2. The mutable profile index and dashboard are projections, never evidence
   authority.
3. One canonical task occurrence contributes at most one observation to one
   recurrence group, regardless of receipt count or changed event boundaries.
4. New-skill drafting requires three current independent canonical occurrences;
   an existing-skill repair may be recommended from one audited occurrence.
5. Only a started model operation spends a profile or review operation slot.
6. Raw session rows, cached receipts, no-learning profiles, stale revisions,
   and already dispositioned profiles cannot spend expensive review capacity.
7. Every started operation and every examined queue row has exactly one
   retained terminal accounting state.
8. The raw-session review path and profile-derived review path cannot both be
   active in one installed generation.
9. Halt, rollback, and restore preserve all immutable evidence and prevent
   unreviewed mutation.
10. Exactly one installed Mac mini scheduler owns the pass.
11. A legacy raw-session review result never satisfies profile-bound audit
    completion.
12. Ordinary catalog publication never reopens historical profile audits; an
    explicit audit-contract migration uses a separate repair backfill.

## Acceptance criteria

- **AC-01:** Reusable task profiles remain readable and identity-valid across
  process restart, later scheduled runs, source-session growth, publication,
  rollback, and restore.
- **AC-02:** Three independent task occurrences can accumulate across any
  combination of long-running or separate source sessions; one occurrence,
  clarification, retry, changed event boundary, or reprofile counts once.
- **AC-03:** The full reviewer itself performs catalog and observed-load audit
  for every reusable profile and records one terminal disposition.
- **AC-04:** Only current undispositioned reusable profiles spend expensive
  review operations.
- **AC-05:** Profiling and review fill their remaining model-operation
  allowances while enough eligible backlog exists, unless an explicit earlier
  bound is retained.
- **AC-06:** Every queue row and started model operation has one terminal,
  dashboard-visible accounting state.
- **AC-07:** New-skill drafting requires three current observations inside the
  rolling 30-day window; older receipts remain evidence but not current
  authority.
- **AC-08:** Existing-skill missed, wrong, or incomplete use may produce a
  report-only repair recommendation from one task occurrence, but no profile
  directly grants installed mutation authority.
- **AC-09:** The corrected natural scheduled run, dashboard, rollback, and
  restore preserve the sole Mac mini owner and all prior evidence.

Each acceptance criterion is exercised by LLM-OPP-09 through LLM-OPP-14 plus
the existing snapshot, evidence, repair, evaluation, and rollback checks:
AC-01 by LLM-OPP-03, 08, and 09; AC-02 and AC-07 by LLM-OPP-04 and 09; AC-03
and AC-08 by LLM-OPP-05 and 10; AC-04 by LLM-OPP-11; AC-05 by LLM-OPP-12 and
13; AC-06 by LLM-OPP-14; and AC-09 by LLM-OPP-15.

## Migration and rollback

Migration is additive and report-only:

1. Preserve every existing immutable task-profile receipt.
2. Rebuild the current profile index from validated receipts where required;
   the index never replaces receipt authority.
3. Treat every pre-cutover raw-session review result as historical evidence
   only. It never satisfies profile audit completion.
4. Derive reusable-profile review eligibility only from dispositions carrying
   the new profile-audit schema version and exact profile, receipt, catalog,
   tombstone, load-trace, reviewer-contract, boundary, and occurrence
   identities.
5. Import existing verified candidate-lifecycle observations by exact
   `profile_id` and task key. When exact identity and non-conflicting boundaries
   validate, initialize the canonical occurrence from that task key. Ambiguous
   historical rows remain visible and do not count toward recurrence.
6. Keep the existing raw-session review path disabled after cutover rather than
   running both paths.

Rollback reinstalls the prior reviewed generation behind halt and disables new
profile-derived review and recurrence writes. It does not delete task-profile
receipts, review dispositions, candidate lifecycle observations, evaluations,
or accounting records. Exact restore re-enables the corrected generation only
after generation-bound self-test. Fail-closed evidence is a rollback manifest
showing byte-identical retained evidence, no raw-session and profile-derived
review running together, no second scheduler, and no candidate created from
fewer than three current canonical task occurrences.

## Definition of Done: Profile-derived task-opportunity funnel

- [ ] Existing immutable task-profile receipts migrate without re-profiling or
      loss, and later session revisions cannot recount one task occurrence.
- [ ] Reusable task profiles, rather than raw queued transcripts, are the only
      input to expensive full review.
- [ ] The full reviewer receives exact transcript evidence, actual skill-load
      trace, current catalog and tombstones, and relevant prior profile groups,
      then records one durable catalog-audit disposition.
- [ ] Correct-skill, missed-skill, wrong-or-incomplete-skill, and no-covering-skill
      outcomes are distinguishable and idempotent.
- [ ] No-covering-skill observations accumulate durably across scheduled runs;
      three current independent canonical task occurrences can come from one
      long-running session, several sessions, or both.
- [ ] Clarifications, retries, repeated profiling, and changed task boundaries
      cannot increase recurrence for one user goal.
- [ ] Profiling and review accounting reconciles every queue row, started model
      operation, retained profile, catalog disposition, recurrence state, and
      terminal result.
- [ ] Lightweight profiling throughput is independent of the 25 full-review
      operation limit, and eligibility, no-learning, cached, stale, and already
      dispositioned rows spend no inappropriate operation slot.
- [ ] With eligible backlog, profiling and expensive review fill their operation
      allowances unless an explicit earlier resource, deadline, halt, lease, or
      health bound stops them; unused capacity proves eligible input exhaustion.
- [ ] The corrected report-only flow creates or repairs one real shadow skill,
      then passes triggering, task-performance, and overall-regression
      evaluation without granting premature installation authority.
- [ ] Installed proof covers the corrected
      profile-to-audit-to-recurrence funnel, 30-day authority, bounded profiling,
      halt, sole-scheduler ownership, one natural scheduled run, browser
      accounting, rollback, and exact restore.
- [ ] The corrected implementation, paired review, proof receipts, plan baton,
      and local commits are complete; nothing is pushed.
