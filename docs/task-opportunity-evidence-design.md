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

## Non-goals

- Do not add another transcript transport, corpus walker, or scheduled owner.
- Do not use phrase matching or a fixed taxonomy as the primary source of task
  meaning.
- Do not require every transcript to produce a skill opportunity.
- Do not let one mention or one session create an active skill.
- Do not treat skill use, task opportunity, evaluation quality, or dependency
  protection as substitutes for one another.
- Do not enable automatic skill mutation until the report-only learning loop
  works end to end.

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
- model and prompt contract identities;
- owner-computed procedure fingerprint;
- observed time;
- task key;
- completion state.

Evidence from one task counts once. Clarifications, retries, tool calls, and
assistant turns inside that task do not increase recurrence.

Two observations are independent only when they have distinct task keys and
source sessions. The task-profile model may propose similarity, but deterministic
owner code applies recurrence counts and identity rules.

The candidate-aware reviewer owns the semantic decision that differently worded
profiles belong to the same reusable procedure. Once it chooses the same skill
artifact, the owner derives one canonical lifecycle procedure fingerprint from
that artifact while retaining each matched profile's original receipt, task key,
session, and source-procedure identity as evidence. Deterministic code must not
infer semantic equivalence from text similarity alone.

### 3. Accumulate evidence before authoring

A reusable procedure becomes authoring-ready when:

- at least two verified independent observations share a procedure fingerprint;
- the observations come from at least two source sessions;
- at least one observation is within 30 days;
- the observations are no more than 45 days apart;
- no tombstone, covering lifecycle, or explicit user disposition blocks it.

The opportunity observations feed the existing shadow-candidate lifecycle. They
replace the current permanently `unverified` session-derived observation with
verified task identity where the evidence proves independence.

Review fixes stage a new immutable candidate revision without creating another
opportunity observation. The exact successor makes the prior recommendation
stale and must be rebound to the retained recurrence evidence before evaluation
or deployment can continue.

One-off tasks remain evidence but do not create a candidate.

### 4. Apply the learning to the skill estate

Only profiles with durable learning value enter the existing full skill reviewer.
That reviewer receives:

- the exact bounded transcript snapshot;
- the validated task profile;
- the current skill catalog and tombstones;
- artifact-routing and writing rules.

It chooses one outcome:

1. patch the skill that should already cover the task;
2. add a support file to an existing umbrella;
3. collect or update a shadow candidate for a new skill;
4. retain a recommendation;
5. retain no durable learning.

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
model-operation budgets is reached. High-confidence reusable profiles are
prioritized for full review. One-off and no-learning sessions are recorded
without consuming a full review attempt.

The first production bounds are `max_profiles_per_run=100`, independently
capped at 500 by configuration validation, and
`max_profile_elapsed_seconds=600`, capped at 1,800. The profiler stops starting
new model operations when either bound is reached and carries the remaining
queue into the next natural run. The enclosing pass has a one-hour backstop so
an in-flight profile can finish and the ordinary review and publication phases
can still settle. `max_reviews_per_run` remains capped at 25.

A malformed model profile is rejected before it can become task evidence and
is retained as a per-session profiling failure. It does not fail an otherwise
healthy scheduled run, consume a full-review attempt, or authorize recurrence.
The queued source revision remains eligible for a later independent retry.

The first backfill processes recent stable sessions first. After catch-up, only
new or changed source revisions require profiling.

Profile mode is an optional review-executor capability named
`task-profile-v2`. A valid legacy review executor remains eligible for ordinary
review but is never called with profile arguments unless it advertises that
capability.

## Deterministic responsibilities

Deterministic code owns:

- stable-session and quiet-period admission;
- bounded snapshot rendering;
- source, event, model, and prompt identities;
- schema validation;
- task-key and procedure-fingerprint computation;
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
| Similarity uncertain | Retain separate observations until later evidence resolves the match |
| Candidate has insufficient recurrence | Keep it shadow-only |
| Trigger evaluation fails | Repair the description and rerun the bounded evaluation |
| Task-performance evaluation fails | Repair or reject the candidate |
| Overall regression fails | Do not deploy |
| Deployed skill misses matching opportunities | Rework its description |
| Deployed skill has no use or matching opportunity | Consider reversible retirement |

## Proof-first rollout

1. Run one private task-profile model call against an existing real bounded
   snapshot.
2. Validate its event references and retain one immutable opportunity observation.
3. Feed that observation into the existing shadow-candidate lifecycle.
4. Prove two independent observations can satisfy recurrence.
5. Draft one real shadow skill or patch from the supporting transcripts.
6. Run triggering, task-performance, and regression evaluation in report-only
   mode.
7. Show the evidence, candidate, evaluation, and recommendation in the private
   dashboard.
8. Harden model budgets, retries, installed configuration, halt behavior, and
   rollback only after the complete report-only loop works.
9. Install one reviewed candidate, prove a natural scheduled run, then retain
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

Repeated wording in one task counts once. Matching procedures from two distinct
task keys and sessions satisfy the recurrence evidence gate.

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

## Definition of Done

- [x] The MacBook Pro and Mac mini topology is named consistently.
- [x] Task profiling reuses the existing authenticated session-source snapshot
      path and creates no second transcript transport.
- [x] An LLM, without skill-library context, identifies task opportunities and
      reusable procedures from bounded real transcripts.
- [x] Retained observations bind exact snapshot and event identities.
- [x] Verified independent observations can cross the existing recurrence gate.
- [x] One recurring opportunity produces a real shadow candidate or existing
      skill repair from its transcript evidence.
- [x] Triggering, task-performance, and overall-regression evaluations pass for
      the accepted candidate.
- [x] The report-only dashboard distinguishes task opportunity, skill use,
      evaluation, and deployment authority.
- [x] Missed triggering produces description repair. Settled absence of use and
      opportunity remains the prerequisite for reversible retirement; incomplete
      profile coverage cannot claim that authority.
- [x] Lightweight profiling throughput is independent of the 25 full-review
      attempt limit.
- [x] Installed proof covers bounded profiling, halt, sole-scheduler ownership,
      one natural run, rollback, and restore.
- [ ] The implementation and proof references are committed locally; nothing is
      pushed.
