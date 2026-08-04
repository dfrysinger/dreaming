# Evidence-backed self-learning plan

## Objective

Make the personal self-learning system improve the quality of future work, not
merely grow a well-formatted skill library.

Every lesson must be routed to the right destination, carry enough evidence to
explain why it was retained, and pass a proportionate quality check before it
is promoted or materially rewrites an existing skill.

The system remains a personal, local proving ground. It does not become a
centralized memory service or duplicate product-level agent-learning
infrastructure.

## User outcome

The owner can answer these questions for every agent-created skill:

1. What repeated problem caused this skill to exist?
2. Which independent tasks support the lesson?
3. Why is this a skill rather than an instruction, factual memory, reference,
   or discarded observation?
4. When was its important evidence last checked?
5. Did it improve the task it was meant to help without harming a related task?
6. What scheduled work depends on it?
7. How can one complete curator run be undone?

## Lane

Systemic.

The work changes provenance metadata, creation and review contracts, promotion
gates, curator authority, validation, and rollback across multiple skills and
both managed roots.

## Lessons carried forward

This design combines four proven patterns without copying their scale:

- Keep always-loaded guidance small and move procedures behind on-demand skill
  loading. Claude Code documents this split between persistent instructions,
  bounded auto memory, and skills.
- Use provenance and lifecycle management for agent-created content. Hermes
  demonstrates pins, recoverable archives, dry runs, and curator-owned
  provenance.
- Verify factual claims against current evidence at use time rather than trying
  to reconcile every change continuously. GitHub Copilot Memory documents this
  citation-based approach.
- Measure whether a learned procedure helps and whether it damages related
  tasks. A structurally valid skill is not necessarily a useful skill.

Public references:

- https://code.claude.com/docs/en/memory
- https://hermes-agent.nousresearch.com/docs/user-guide/features/curator
- https://github.blog/ai-and-ml/github-copilot/building-an-agentic-memory-system-for-github-copilot/
- https://claude.com/blog/improving-skill-creator-test-measure-and-refine-agent-skills
- https://docs.benchflow.ai/running-benchmarks
- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- https://www.swebench.com/SWE-bench/guides/evaluation/

## Existing system to extend

Reuse the current owners instead of adding another learning subsystem:

- `skill-review` discovers lessons, patches skills, and creates new local
  agent-created skills.
- `skill-create` defines the authoring and validation contract.
- `memory-curator` migrates durable memories into skills before safe deletion.
- `skill-curator` proposes consolidation and reversible archival.
- `.agent-created.json` records provenance for curator-managed skills.
- The review ledger, tombstones, shared writer lease, dry-run approval gate,
  Git commits, self-test, and watchdog continue to own their current concerns.

## Core decisions

### 1. Route before writing

Use one artifact-routing contract in every learning path:

| Destination | Use for | Do not use for |
|---|---|---|
| Instruction | A stable rule that should influence nearly every relevant turn | Multi-step procedures or changing project facts |
| Factual memory | A concise current fact or user preference whose truth can change | Procedures or large reference material |
| Skill | A reusable procedure with a trigger, ordered policy, observable stopping condition, and clear interface | One-off narratives, facts, or advice without an executable process |
| Support file | Detailed reference, template, script, or reproduction material used by an existing skill | A separately invokable procedure |
| Discard | Transient failures, duplicated knowledge, unsupported beliefs, or lessons too narrow to reuse | Evidence-backed reusable behavior |

The router must allow an explicit `discard` result. Producing no artifact is a
successful outcome when the evidence does not justify persistence.

Instruction and factual-memory destinations are recommendations in M1, not
unattended writes. The review ledger records their destination and reason so
they remain distinguishable from discard. A later explicit owner action or the
existing memory surface may apply a recommendation. Factual memory is limited
to concise, volatile, non-procedural facts or preferences that the owner is
likely to ask about again; anything a skill can carry belongs in the skill.

`memory-curator` retains its existing `roll | dup | obsolete | keep`
categorization as the sole deletion authority. The shared routing contract
governs only whether a rolled lesson belongs in a skill, support file, or no
new artifact. `obsolete` and `keep` never become deletable merely because the
artifact router returns discard. Only rolled or exact-duplicate memories enter
the existing delete list.

### 2. Extend the existing provenance envelope

The `.agent-created` file remains the curator authority marker.
`.agent-created.json` gains a versioned evidence envelope:

```json
{
  "schema_version": 2,
  "skill": "example-skill",
  "created_by": "skill-review",
  "source_session_id": "uuid",
  "source_mode": "dispatch",
  "review_prompt_version": "skill-review-1",
  "created_at": "2026-08-02T00:00:00Z",
  "evidence": [
    {
      "task_key": "task:opaque-uuid",
      "session_id": "uuid",
      "observed_at": "2026-08-02T00:00:00Z",
      "independence": "verified",
      "evidence_kind": "successful-procedure",
      "summary": "A short non-sensitive description of the reusable friction"
    }
  ],
  "routing": {
    "destination": "skill",
    "reason": "Why procedure-level persistence is justified"
  },
  "claims": [
    {
      "claim": "The concrete behavior this skill relies on",
      "verification": "session-evidence",
      "last_verified_at": "2026-08-02T00:00:00Z"
    }
  ],
  "evaluation": {
    "status": "not_evaluated",
    "evaluated_at": null,
    "candidate_id": null,
    "run_id": null,
    "receipt_sha256": null,
    "case_manifest_sha256": null,
    "model": null,
    "source_case": null,
    "sibling_case": null,
    "waiver_class": null,
    "waiver_reason": null
  }
}
```

`created_by` is one of `skill-review`, `skill-create`, or `memory-curator`.
`evidence_kind` is one of `successful-procedure`, `failure-recovery`,
`owner-correction`, or `independent-recurrence`. Evaluation status is one of
`not_evaluated`, `pending`, `pass`, `regression`, `inconclusive`, or `waived`.
When status is `waived`, `waiver_class` is one of `documentation-only`,
`reference-only`, or `deterministic-helper`, and `waiver_reason` is non-empty.

`source_session_id` remains a deprecated compatibility mirror of the first
evidence entry's `session_id`. An absent `schema_version` means schema v1.
Lazy v1-to-v2 migration maps `source_session_id` into the first evidence entry,
retains `review_prompt_version`, and preserves all unrecognized fields.
Schema-v1 readers therefore continue to find their named scalar fields while
schema-v2 readers use the evidence list.

The envelope contains summaries, identifiers, and local evidence only. It must
not copy repository content, conversation transcripts, credentials, internal
URLs, or private proper nouns into the public repository.

Promotion from local to public strips the private evidence envelope as it does
today. All private evidence lives in `.agent-created.json`, not in support
files. The public skill keeps only publishable technique.

### 3. Count independent tasks, not mentions

Evidence strength is the number of distinct task instances supporting the same
procedure, not the number of messages or sessions that repeat one incident.

A distinct task differs in at least one meaningful dimension: user ask, day,
target repository or project, input, failure mode, or correction.

Each evidence entry carries an opaque `task_key`. Prefer a platform task ID
when one exists. Otherwise the task owner mints a random opaque UUID once, and
the handoff or rotation baton carries it explicitly into continuation sessions.
Task identity must never be derived from repository names, dates, asks,
failure text, session IDs, or other private or collision-prone content.

Dispatch resolves task identity in this order: platform task ID, explicit
handoff/rotation task key, then a newly minted UUID for a task that is known to
start in the current session. A scheduled sweep that cannot prove whether a
historical session starts or continues a task records the observation as
`independence: "unverified"` and does not increase evidence strength. Verified
distinct task count is derived from unique task keys on entries whose
`independence` is `verified`; it is not stored separately.

One task may justify a local draft when the procedure is expensive to
rediscover. It does not justify promotion or broadening without a recorded
reason.

### 4. Verify changing claims at the boundary

Procedural steps can be durable while factual assumptions change.

Claims tied to code, tool behavior, paths, versions, or service capabilities
must say how they are verified:

- `current-source`: reread the named current source before relying on it;
- `deterministic-check`: run the named script or command;
- `session-evidence`: the claim is historical context, not a current fact;
- `owner-policy`: the claim is an explicit stable user policy.

The skill body should point to verification where a stale claim could produce a
wrong action. The system does not add citations to every sentence.

### 5. Evaluate effect before promotion or major rewrite

Creation remains cheap; authority becomes earned.

Evaluation is required when:

- promoting a local agent-created skill into the public plugin;
- replacing or broadening a class-level umbrella;
- a curator consolidation materially changes how an existing skill executes;
- a skill is implicated in a repeated regression.

The initial evaluation contract has two cases:

1. **Source case:** a representative task the skill is intended to improve.
2. **Sibling case:** a related task where an overfitted rule could cause harm.

Run each case with and without the candidate skill. Record:

- terminal success or failure;
- observable acceptance checkpoints;
- unnecessary or harmful actions;
- model, candidate identity, and evidence paths.

For the initial single-executor evaluator, the gate passes when the candidate
improves the source case and causes no material sibling regression. An
inconclusive comparison never becomes a pass.

Section 8 extends this contract to repeated trials, capability and
encoded-preference skill classes, activation controls, per-CLI certificates,
and an exact aggregate policy. When cross-CLI certification is enabled,
section 8 replaces this single-executor pass condition and waiver authority
rather than applying only the rules that happen to be stricter.

An evaluation may be explicitly waived only for a documentation-only,
reference-only, or deterministic helper change whose effect is already fully
proved by existing tests. The waiver carries a reason.

Promotion and major rewrites use an allowlist: only `pass`, or `waived` with a
valid enumerated waiver class, non-empty reason, and a content-addressed
passing evaluation anchor, may proceed.
`not_evaluated` triggers a fresh evaluation; `pending`, `regression`, and
`inconclusive` remain local.

Under cross-CLI certification, a waiver is valid only for an enumerated
documentation, reference, or deterministic-helper change whose base candidate
has a current gate-profile aggregate certificate. The waiver binds the new
candidate, base aggregate receipt, policy, suite, required executor set,
restricted changed-file inventory, test command, and test-result digest in the
version-2 receipt store. It cannot anchor to a legacy single-executor receipt
or remove an executor requirement.

### 6. Protect scheduled dependencies

A skill referenced by an installed LaunchAgent, daemon prompt, or durable
scheduled configuration is treated as implicitly pinned.

The curator may report it but may not place it in `consolidations:` or
`prunings:` until the dependency is removed or retargeted.

Session-only reminders that cannot be enumerated reliably remain outside the
automatic guarantee. Their owners must pin long-lived dependencies explicitly.

### 7. Roll back a complete curator run

Git remains the storage and reversal mechanism. Add a run manifest rather than
another backup format.

Before live mutation, record:

- dry-run report path and approval timestamp;
- public and local root starting commits;
- pre-existing dirty paths;
- planned consolidations and prunings.

After each scoped commit, append its root and commit ID. Also record every
tombstone written and every curator-ledger entry appended. A rollback command
reverses commits in reverse order, clears only the recorded tombstones and
ledger effects through existing restore semantics, refuses unrelated or
ambiguous state, and writes a second manifest for the rollback itself.

No hard reset, broad checkout, or deletion is allowed.

### 8. Certify skill effects across selected CLIs

#### Objective

Let one Dreaming evaluation policy certify the exact same skill candidate
through any explicitly selected set of Copilot CLI, Claude Code, and Codex
executors, while preserving intended-task improvement, related-task safety,
activation accuracy, and exact evidence provenance.

#### Lane

Critical.

This extension changes the promotion gate, durable evaluation schema,
cross-provider transfer boundary, and fail-closed evidence contract. A false
pass can publish a harmful skill. A false transfer can send a private
evaluation prompt or artifact to an executor the owner did not authorize.

#### Non-goals

- Support operating systems beyond Dreaming's existing macOS runtime.
  "Cross-platform" in this milestone means Copilot CLI, Claude Code, and Codex.
- Build a hosted benchmark service, dashboard, leaderboard, dataset registry,
  container service, or multi-user approval system.
- Import or depend on private evaluation infrastructure.
- Make scores from different CLI, model, or harness combinations directly
  interchangeable.
- Evaluate every local draft or small wording change.
- Let the trial harness decide promotion, consolidation, waiver, or rollback
  policy.
- Replace `writing-great-skills`, content review, or public-safety review with
  behavioral evaluation.
- Send repository content, private transcripts, credentials, or unrelated
  home-directory state to an evaluation executor.

#### Reuse contract

Dreaming continues to own:

- candidate inventory and content identity in `skill-evaluation.py`;
- local case manifests and evidence-envelope linkage;
- promotion and consolidation gates in `skill-manage`;
- selected CLI configuration and exact route authorization;
- content-addressed receipts and stale-result rejection;
- the shared writer lease, halt switch, Git history, and rollback controls.

The separate trial harness described in
`skill-evaluation-trial-harness-design.md` owns clean trial execution,
executor-specific skill projection, trace capture, deterministic graders,
blind comparison, repetition, and result aggregation. It returns sealed
evidence. It does not write Dreaming state or decide whether evidence is
sufficient.

The existing `review-executor` role is not reused for trials. Review execution
is source-blind and intentionally has no skill-under-test or task tool surface.
Evaluation needs a different boundary with controlled tools, a candidate
treatment, final-state artifacts, and proof that the skill loaded. A new
`skill-evaluation-executor` adapter role carries that contract without
weakening review isolation.

#### Skill classes

Each evaluation suite declares one class:

- `capability_uplift`: the skill should make a task succeed that the base
  executor cannot complete reliably.
- `encoded_preference`: the base executor may already complete the task, but
  the skill must follow a specified workflow, policy, or output contract more
  faithfully.

Capability uplift requires a paired improvement over the control arm. Encoded
preference requires candidate conformance and a blind preference advantage;
it does not fail merely because the control also completes the task. These are
the complete intended-case pass semantics when cross-CLI certification is
enabled; the initial source-case improvement rule applies only to
`capability_uplift`.

#### Case classes

One local `.skill-evaluation-cases.json` file defines a versioned suite with:

1. one or more `intended` cases;
2. one or more `related` cases that must not regress;
3. `activation_positive` prompts that should load the skill;
4. `activation_negative` prompts that must not load the skill.

Version 1 source and sibling manifests remain readable. A source case compiles
to one `capability_uplift` intended case. A sibling case compiles to one
related case. Legacy manifests do not imply activation coverage or
cross-executor certification.

Assertions may use:

- deterministic final-state checks;
- structured result or JSON-schema checks;
- exact, regular-expression, or bounded numeric checks;
- declared trajectory checks such as required skill load or forbidden tool;
- blind model comparison for semantic quality.

Assertions are not appended to the task prompt. Deterministic outcome checks
are authoritative when final state is objectively inspectable. Model graders
may judge semantic quality but cannot override a failed deterministic safety or
outcome check.

#### Treatments and pairing

Every behavioral case runs as a paired experiment:

- `control`: no candidate skill and no user-installed non-built-in skills;
- `candidate`: the exact candidate snapshot is the only non-built-in skill.

Both arms use the same CLI, exact model, task input, working-state snapshot,
tool policy, timeout, token budget, grader set, and trial number. Each arm
starts with a fresh CLI home and working directory. Arm order alternates by
pair to reduce systematic ordering effects.

The gate profile runs three trials per arm. A one-trial `iterate` profile may
be used while authoring, but it cannot produce a promotion or consolidation
certificate.

#### Executor selection and certification

`DREAMING_EVALUATION_EXECUTORS` is an explicit ordered set containing
`copilot`, `claude`, and/or `codex`. No absent or merely installed CLI becomes
required implicitly. Each selected executor must have a healthy
`skill-evaluation-executor` adapter and an explicitly configured model.

Each CLI produces its own certificate. Results are never pooled into one
cross-provider score. The aggregate gate passes only when every required
executor has a current passing certificate for the same candidate, suite,
policy, and gate profile.

An unavailable required executor yields `inconclusive`, not pass. The owner may
change the required executor set and run a new certification policy. Removing
an executor changes the policy identity and cannot reuse the prior aggregate
receipt.

#### Default gate policy

For each required executor:

- every intended candidate arm must pass at least two of three trials;
- a `capability_uplift` intended case must improve by at least one successful
  trial over control and cannot pass when control succeeds in all trials;
- an `encoded_preference` intended case must pass at least two of three trials
  and win at least two of three blind comparisons against control;
- every related candidate case must preserve every deterministic invariant,
  pass at least as many trials as control, and add no forbidden action;
- each activation-positive prompt must load the candidate in at least two of
  three trials;
- each activation-negative prompt must load the candidate in zero of three
  trials;
- an infrastructure error, missing trace, model mismatch, budget mismatch,
  unproved skill load, invalid grader, invalid required comparator output, or
  incomplete pair makes the executor certificate `inconclusive`.

All three required encoded-preference comparisons must be schema-valid and
bound to matched pairs. One or two valid comparisons cannot authorize a
certificate, even if every valid comparison favors the candidate.

The suite may declare stricter thresholds. It may not weaken deterministic
safety assertions, admit an unpaired treatment, or treat an infrastructure
failure as a candidate result.

#### Provenance schema

Cross-CLI authority is stored in a schema-v3 document under the version-2
evaluation state directory:

```text
evaluations/v2/authority/<skill-key>/<candidate-id>.json
```

It is not a skill-root file, is excluded from candidate inventory and trial
projection by location, and never reaches public promotion. `.agent-created.json`
remains schema v2 and retains its existing single-executor evaluation object
for compatibility, but that object is non-authoritative while cross-CLI
certification is enabled. A schema-v2 scalar is never synthesized by
collapsing multiple executor certificates. The schema-v2 envelope may carry an
opaque top-level `evaluation_v3_sha256` pointer; existing readers preserve but
do not authorize that unknown field.

```json
{
  "schema_version": 3,
  "evaluation": {
    "status": "pass",
    "evaluated_at": "2026-08-04T00:00:00Z",
    "policy_id": "sha256:...",
    "suite_id": "sha256:...",
    "candidate_id": "sha256:...",
    "profile": "gate",
    "required_executors": ["copilot", "claude", "codex"],
    "certifications": {
      "copilot": {
        "status": "pass",
        "model": "exact-model-id",
        "receipt_sha256": "sha256:..."
      }
    },
    "aggregate_receipt_sha256": "sha256:..."
  }
}
```

The cross-CLI gate resolves the authority document from the configured
version-2 evaluation state, requires the envelope pointer to match its digest,
and ignores schema-v2 evaluation authority. The authority document is written
atomically before the pointer is updated, and only after the schema-v3 reader
and promotion gate are active. Older envelope writers cannot open or relabel
the version-2 authority document, and an older promotion gate cannot interpret
the opaque pointer as a schema-v2 pass.

The aggregate receipt binds:

- candidate inventory and ID;
- complete case manifest and suite ID;
- required executor set and policy ID;
- profile and trial count;
- every executor, model, CLI version, adapter version, harness version, tool
  policy, budget, grader version, comparator route, comparator model,
  comparator version, and trial result digest;
- every raw-log and normalized-trace digest;
- blind-comparison assignments without revealing them to the comparator;
- final per-executor and aggregate decisions.

Any bound input change makes the certificate stale. Receipt paths remain
content-addressed. The envelope contains only receipt identities and
non-sensitive summaries; prompts, raw traces, and artifacts remain local
evaluation state and are removed according to an explicit retention policy.

Content hashes prove identity and later tampering, not honest production.
Dreaming's trust anchor is the reviewed, allowlisted harness and adapter
executables that it launches by exact digest. A Dreaming-owned verifier checks
the run nonce, producer digests, native event structure, prepared and effective
execution records, file inventory, and result hashes, then reruns required
deterministic graders over the sealed artifacts before issuing a certificate.
Evidence from an unknown producer, an unapproved executable digest, or a bundle
whose deterministic results cannot be reproduced is untrusted and
`inconclusive`.

#### Promotion and consolidation policy

Promotion or a material consolidation requires:

1. public-safety inventory and content review;
2. a gate-profile aggregate certificate for the exact candidate;
3. passing certificates for every executor required by the active policy;
4. no stale, inconclusive, or regressing case;
5. a current schema-v3 authority document linked to the evidence envelope.

A cross-CLI waiver substitutes for items 2 and 3 only under the restricted
waiver contract in section 5. It inherits the base aggregate's policy, suite,
and required executor set and must be current for the exact changed candidate.

Evaluation proves behavior under named executors and cases. It does not prove
universal correctness. Public documentation may state only the executors,
models, suite version, and guarantees actually certified.

#### Privacy and transfer policy

Evaluation cases and artifacts are local inputs. Each executor route is
explicitly enabled before a prompt or artifact is sent to that CLI. The
compiled trial packet contains only the declared case input, candidate
snapshot, tool policy, grader inputs, and any separately authorized comparator
route. It contains no native session path, review ledger, unrelated skill,
repository credential, or inherited user instruction.

The executor receives credentials through its existing narrow native
authentication boundary. Trial outputs are untrusted and cannot choose receipt
paths, grader commands, retention policy, or Dreaming mutations.

#### Migration and rollback

The schema-v3 authority reader ships before any authority writer. Existing
receipts remain immutable under the version-1 receipt directory. Cross-CLI
authority documents, receipts, and latest pointers use a version-2 directory,
so rollback cannot overwrite or reinterpret earlier evidence.

When cross-CLI certification is enabled, a legacy passing or waived receipt is
insufficient for promotion and triggers a fresh gate-profile evaluation. No
bulk backfill runs. A new cross-CLI waiver may anchor only to a current
version-2 aggregate certificate.

Rollback:

1. activate the halt switch;
2. stop evaluation workers and wait for trial leases to expire;
3. restore the previous evaluator, adapter configuration, and promotion gate;
4. retain schema-v3 authority documents and version-2 receipts as inert audit
   data;
5. run the previous self-test and keep promotion disabled until it passes.

The restored schema-v2 writer has no path to the version-2 authority directory.
The restored promotion gate has no path from a schema-v3 authority document or
version-2 receipt to a schema-v2 pass. Rollback never deletes candidate skills,
cases, traces, or receipts automatically.

#### Fail-closed evidence

The critical boundary is proved when deliberate tests show that:

- an unauthorized executor receives no process invocation or trial packet;
- an inherited instruction, unrelated skill, or native session root is absent
  from each trial;
- a changed candidate, case, model, CLI, adapter, harness, grader, budget, or
  required-executor set invalidates the gate;
- a missing arm, trial, trace, collected artifact record, skill-load event, or
  deterministic grader result produces `inconclusive`; an artifact proved
  absent from a valid completed workspace remains a deterministic task result;
- an unknown or changed harness, adapter, comparator, or producer identity
  produces `inconclusive`;
- a related-task or activation-negative regression blocks promotion;
- a forged, moved, or hash-mismatched receipt is rejected;
- rollback leaves new evidence inert and cannot silently downgrade it into a
  passing legacy record.

## Data flow

### End-of-task learning

1. `skill-review` acquires the shared writer lease before its first mutation
   and renews it immediately before every envelope, content, commit, or ledger
   write.
2. `skill-review` identifies a candidate lesson.
3. The routing contract chooses instruction, memory, skill, support file, or
   discard.
4. Skill/support-file routes search loaded skills and both roots for reuse.
5. The evidence helper initializes or appends the versioned envelope using a
   locked temporary file, `fsync`, and atomic replace. For a new agent-created
   skill it writes and validates the envelope first, then creates the authority
   marker. A valid envelope without a marker is an incomplete draft that the
   helper may resume. A marker without a valid envelope is quarantined until an
   explicit re-stamp supplies the missing source inputs; no autonomous caller
   may infer them.
6. `writing-great-skills`, validation, and `dual-review` govern content.
7. The local commit and review ledger are written under the same lease. Ledger
   entries include routed outcomes with destination, reason, and task key so
   recommendation and discard paths are observable.

### Scheduled consolidation

1. Daily sweep selects unreviewed sessions.
2. Routing and evidence recording use the same helpers as dispatch.
3. Existing evidence is appended only when the task is genuinely distinct.
4. The weekly curator uses evidence strength, usage, project completion,
   scheduled dependencies, and evaluation status in its dry-run proposal.

### Promotion

1. Publishability gate removes private/project-specific details.
2. A promotion inventory lists every file in the skill. Every listed file,
   including support files, is included in the existing independent review and
   must be explicitly classified public-safe. Known private sentinels,
   transcript markers, credentials, private URLs, and unresolved task-specific
   reproductions fail closed.
3. Evidence gate checks the required intended, related, and activation cases
   through every executor named by the active certification policy.
4. Promotion strips local provenance files.
5. Public validation, review, versioning, commit, push, and plugin refresh run
   through existing paths.

## Failure model

| Failure | Required behavior |
|---|---|
| No marker and no envelope | Treat as hand-made and preserve the existing recommendation-only path |
| Agent-created marker with missing or malformed envelope | Fail closed before autonomous create, promotion, or curator mutation |
| Valid envelope without marker | Treat as an incomplete draft; resume marker creation only under the writer lease |
| Envelope present but schema-invalid | Fail closed before autonomous mutation |
| Source session unavailable | Keep existing content; record evidence unavailable |
| Duplicate evidence | Do not add the existing task key |
| Factual claim cannot be verified | Do not use the claim; correct or narrow the skill |
| Evaluation is inconclusive | Keep local draft; do not promote or broaden authority |
| Sibling regression | Reject the candidate change and retain the prior skill |
| Required evaluation executor is unavailable | Record `inconclusive`; do not reuse another executor's certificate |
| Trial pair is incomplete or budgets differ | Reject the pair as invalid evidence |
| Skill load cannot be proved | Reject the candidate trial as invalid evidence |
| Activation-negative prompt loads the skill | Reject the candidate and keep it local |
| Evaluation route is not authorized | Do not invoke the executor or materialize its packet |
| Cross-CLI receipt input changes | Mark the aggregate certificate stale |
| Evaluation rollback encounters schema-v3 authority | Keep promotion halted; never reinterpret it as a legacy pass |
| Scheduled dependency discovery fails | Do not archive or consolidate the candidate |
| Curator run partially commits | Manifest remains incomplete; rollback or resume explicitly |
| Rollback encounters unrelated changes | Refuse and report exact blocking paths |

## Security and privacy

- Evidence metadata stays in the local root and local state.
- Public promotion removes `.agent-created` and `.agent-created.json`; its
  complete file inventory and review gate reject private evidence elsewhere.
- Evidence summaries must not contain credentials, tokens, private URLs,
  transcript text, or copied private code.
- Current-source verification follows the caller's existing repository access;
  the envelope grants no new access.
- Citation or source text remains untrusted input.

## Milestones

### M1: Routing and evidence envelope

Deliver:

- one public-safe artifact-routing reference shared by `skill-review`,
  `skill-create`, and `memory-curator`;
- existing routing prose in those callers is replaced with links to the shared
  contract rather than retained as a second artifact-routing authority;
- `memory-curator` retains its separate deletion categorization and maps only
  its rolled skill-write path through the shared artifact router;
- a versioned evidence-envelope helper;
- backward-compatible migration of existing `.agent-created.json` files;
- creation and patch flows that initialize or append evidence;
- review-ledger support for routed non-artifact outcomes;
- validator and deterministic tests;
- curator reads evidence but does not yet enforce evaluation status, including
  a compatibility check for every provenance field its prompt names.

Acceptance:

- a reusable procedure routes to a skill and records one source task;
- a factual observation routes away from skill creation;
- a transient or unsupported observation routes to discard;
- repeated evidence from the same task does not add another task key;
- a distinct second task adds another task key;
- a handoff or rotation carrying the same task key remains one verified task;
- an unlinked historical session remains unverified rather than inflating
  evidence strength;
- legacy provenance remains readable;
- schema-v1 readers retain the scalar source-session and prompt-version fields;
- malformed evidence fails closed before autonomous mutation;
- public promotion still strips local provenance, and no `.agent-created*`
  path exists in the public root;
- a private sentinel in either `SKILL.md` or a support file blocks promotion;
- obsolete-but-unrolled memory never enters the memory-curator delete list.

### M2: Evaluation gate

Deliver:

- source/sibling case manifest;
- with/without-skill runner using the existing bounded Copilot harness;
- result comparison and waiver record;
- required gate for promotion and major umbrella rewrites;
- a small regression fixture proving an overfitted skill can fail the sibling
  case.

Acceptance:

- a helpful candidate passes;
- an overfitted candidate is rejected;
- an inconclusive run stays local;
- every result is tied to an exact candidate and model identity.

### M3: Dependency protection and run rollback

Deliver:

- installed LaunchAgent and daemon-prompt dependency scanner;
- curator implicit-pin integration;
- live-run manifest across both roots;
- reversible commit-based rollback command;
- interrupted-run and unrelated-dirty-path tests.

Acceptance:

- a scheduled skill cannot be proposed for archive;
- retargeting the schedule removes the implicit pin;
- a multi-root curator run can be reverted in one command;
- rollback refuses to overwrite unrelated work.

Implemented in v0.79.0:

- installed LaunchAgents are discovered with the installer-compatible label
  prefix, then followed through exact managed paths and recursive durable
  prompt/script references;
- explicit and implicit pins are checked while the shared writer lease is
  held, and incomplete discovery fails closed;
- `curator-run.py` freezes every planned archive dependency before mutation,
  records atomic intent/completion manifests, scopes each commit to exact
  files, and renews the shared lease through the run;
- rollback restores archives through `restore-skill.sh`, reverts patch/create
  commits through an isolated Git index, restores exact manifest/state bytes,
  and removes only recorded ledger effects;
- deterministic acceptance covers retargeting, aliases, malformed/missing
  references, lock conflicts, staged and untracked unrelated work, two roots,
  interrupted operations, state tampering, missing/rewritten commits, and
  ambiguous root identity.

### M4: Hot-context budget

Investigate the available Copilot memory surfaces before implementation.

Deliver only if the platform exposes an enforceable local boundary:

- a visible budget for always-loaded personal instructions and service-injected
  memory context;
- routing pressure that moves procedure detail into skills;
- warnings rather than silent truncation.

Do not build a second memory store solely to create a budget.

Investigation result:

- Copilot CLI exposes aggregate context visibility through `/context`, source
  discovery and toggles through `/instructions`, and path-specific modular
  instructions.
- Copilot Memory is relevance-retrieved and service-managed. It does not expose
  a local token allocation, count limit, write rejection threshold, or
  machine-readable injected-memory budget.
- The CLI exposes no local setting or API that can reserve, reject, or warn on
  a combined personal-instruction and memory allocation.
- Products with enforceable budgets own the loading boundary. A repository
  script here could count source files, but it could not observe or enforce
  what Copilot Memory retrieves, so it would be a second policy store rather
  than a platform budget.

Decision: close M4 without implementation. Continue using progressive
disclosure, modular instructions, memory curation, `/instructions`, and
`/context`. Reopen only when Copilot exposes a machine-readable local boundary
that can reject or warn before personal instruction or memory context is
loaded.

### M5: Cross-CLI skill certification

Implementation proceeds in this order. Each phase leaves a testable boundary
for the next phase and must satisfy its deterministic checks before the next
phase becomes authoritative.

#### M5.1: Policy and schemas

Define suite schema v2, candidate and policy identities, required-executor
selection, per-executor certificate and aggregate-receipt schemas, version-2
authority state, cross-CLI waiver rules, stale detection, migration, retention,
and rollback behavior. The existing evaluator remains authoritative.

#### M5.2: Trial harness core

Build the replaceable harness from
`skill-evaluation-trial-harness-design.md`: sealed input and result bundles,
fresh workspaces, matched scheduling, deterministic graders, blind comparison,
artifact handling, process cleanup, producer identity, and deterministic fake
executors. The harness emits evidence but no Dreaming decision.

#### M5.3: Native CLI adapters

Add the versioned `skill-evaluation-executor` role and implement the same
prepare, run, normalize, collect, version, isolation, and native skill-load
proof semantics for Copilot CLI, Claude Code, and Codex. Each adapter must pass
the harness contract before it can be selected.

#### M5.4: Dreaming certification integration

Compile Dreaming suites into sealed runs, authorize selected executor and
comparator routes, verify result bundles and producer identity, recompute
deterministic graders, issue independent executor certificates, write the
aggregate receipt and schema-v3 authority document, and enforce promotion,
consolidation, waiver, and stale-evidence policy.

#### M5.5: Rollout and live proof

Install readers before writers, exercise migration and halt behavior, prove
rollback leaves version-2 authority inert, run the real public synthetic suite
through all three CLIs, complete implementation review, update operating
documentation, and enable the new gate only after the final self-test passes.

Deliver:

- schema-v2 evaluation suites with intended, related, activation-positive, and
  activation-negative cases;
- capability-uplift and encoded-preference policies;
- an explicit `DREAMING_EVALUATION_EXECUTORS` desired set;
- a versioned `skill-evaluation-executor` adapter contract for Copilot, Claude,
  and Codex;
- a separate trial harness conforming to
  `skill-evaluation-trial-harness-design.md`;
- three-trial paired gate runs and one-trial non-authoritative iteration runs;
- deterministic outcome graders, trajectory checks, and blind comparison;
- per-executor certificates and one content-addressed aggregate receipt;
- schema-v3 cross-CLI authority documents in version-2 evaluation state,
  linked from unchanged schema-v2 evidence envelopes;
- promotion and consolidation enforcement;
- versioned state migration, retention, halt, and rollback behavior.

Acceptance:

- the same candidate and suite run through Copilot, Claude, and Codex without
  inherited user instructions or unrelated skills;
- every candidate trial proves whether the named skill loaded;
- a helpful capability skill improves at least one successful trial over its
  control on every required executor;
- an encoded-preference skill wins at least two of three blind comparisons,
  satisfies its deterministic policy checks, and has no invalid comparison;
- a related-task regression blocks only the candidate certificate and cannot
  mutate the prior skill;
- positive trigger cases load the skill at the required rate and every
  negative trigger case remains unloaded;
- changing any candidate, case, executor, model, CLI, adapter, harness, grader,
  budget, profile, or required-executor input invalidates the certificate;
- an unavailable executor, partial pair, missing trace, artifact collection or
  sealing failure, or other infrastructure failure is `inconclusive`; an
  artifact genuinely absent from a valid completed workspace is a deterministic
  task failure;
- promotion refuses stale, partial, regressing, or unauthorized evidence;
- rollback leaves version-2 receipts inert and cannot turn them into legacy
  passing authority.

## Deterministic check contract

### Routing

- Each destination has one positive fixture.
- Ambiguous input resolves to discard or manual review, never forced creation.
- Routing records accept only the five named destinations and require a
  non-empty reason and task key.
- Unknown or malformed destinations fail validation.
- Destination correctness is a live model acceptance property, not a
  deterministic model-output assertion.

### Evidence envelope

- Initialize schema v2 atomically.
- Read legacy schema v1.
- Preserve the v1 scalar compatibility fields in schema v2.
- Append a distinct task.
- Deduplicate the same task.
- Derive distinct task count from unique verified task keys.
- Preserve one task key across a simulated handoff and rotation.
- Distinguish two same-repository, same-day tasks with separate minted keys.
- Exclude unverified historical observations from evidence strength.
- Reject invalid destination, missing task key or source ID, invalid timestamp, and
  malformed JSON.
- Preserve unknown future fields during updates.
- Treat no-marker/no-envelope skills as hand-made; reject marker-without-envelope.
- Resume a valid envelope-without-marker draft and require explicit inputs to
  repair marker-without-envelope.
- Confirm every provenance field named by the curator resolves on schema v2.
- Confirm promotion leaves no `.agent-created*` path in the public root.
- Confirm private sentinels in `SKILL.md` and support files block promotion.
- Confirm an obsolete-but-unrolled memory is never placed on the delete list.

### Evaluation

- Candidate identity covers every runtime input.
- Source and sibling cases cannot share the same task identifier.
- Pass, regression, inconclusive, and waiver states are explicit.
- Promotion and major rewrite gates allow only `pass` or a valid narrow
  `waived` record.
- `not_evaluated` triggers evaluation; `pending`, `regression`, and
  `inconclusive` are rejected.

### Cross-CLI certification

- **Suite schema:** Create one schema-v2 suite containing every case class.
  Reject duplicate IDs, missing deterministic safety assertions, invalid
  grader references, and shared task IDs where independence is required. This
  proves invalid policy cannot reach a trial.
- **Legacy compile:** Compile a schema-v1 source/sibling manifest into one
  capability intended case and one related case. Confirm it lacks activation
  and cross-executor authority. This proves backward compatibility does not
  invent evidence.
- **Treatment isolation:** Seed unrelated instructions and skills in the real
  user homes, then run both arms in isolated homes. Candidate sees only the
  candidate snapshot; control sees no non-built-in skill. Any seeded content in
  a packet or trace fails the check.
- **Paired budgets:** Deliberately change timeout, model, tools, or token budget
  in one arm. The pair must be invalid before scoring. This proves the reported
  delta compares matched trials.
- **Skill-load proof:** Return a successful answer without a normalized skill
  load event. Candidate trial must be invalid. Return a load event in a
  negative activation trial. Certificate must regress.
- **Outcome authority:** Make the final answer claim success while the
  deterministic artifact check fails. The trial must fail. This proves
  self-report cannot override final state.
- **Blind comparison:** Swap A/B assignment while preserving outputs. Include
  asymmetric outputs containing a declared candidate identity marker.
  Comparator input must either remove non-semantic transport metadata without
  changing judged content or refuse the comparison as identity-leaking; the
  unblinding record must recover the correct winner afterward. Make two of
  three comparator responses invalid and confirm the executor certificate is
  `inconclusive`, not pass.
- **Repetition:** Provide three deterministic fixture trials for control and
  candidate. Confirm capability, encoded-preference, related, and activation
  thresholds produce pass, regression, and inconclusive states exactly.
- **Per-executor isolation:** Give one executor a regression and two passes.
  Only that executor certificate regresses, while the aggregate gate fails.
  This proves scores are not pooled.
- **Authorization:** Configure an executor without an allow entry. Assert no
  process starts and no packet exists.
- **Receipt binding:** Change each bound input independently and confirm the
  latest gate becomes stale. Move or alter a receipt and confirm hash
  verification fails.
- **Producer trust:** Change the harness, adapter, prepared execution, effective
  execution, or comparator executable digest. Reject the bundle. Change a
  deterministic grader result without changing its sealed artifacts and
  confirm Dreaming's recomputation rejects it.
- **Waiver migration:** Attempt to anchor a deterministic-helper waiver to a
  legacy passing receipt and reject it. Anchor the same restricted change to a
  current version-2 aggregate, bind its tests and required executors, and
  confirm it becomes stale when any bound input changes.
- **Schema migration:** Read schema-v2 envelopes unchanged, write the schema-v3
  authority document under version-2 evaluation state only after its reader is
  active, and confirm old envelope writers cannot open, relabel, or authorize
  it.
- **Rollback:** Halt during active fixture trials, restore the old evaluator,
  retain version-2 receipts, and prove the old gate cannot accept them.

### Scheduled dependencies

- Detect direct skill references in installed daemon prompts and LaunchAgents.
- Ignore archived skills and unrelated text.
- Fail closed when dependency enumeration is incomplete.

### Rollback

- Record both roots and every created commit.
- Record every tombstone path and curator-ledger mutation.
- Revert in reverse order.
- Restore archived skills through existing restore semantics.
- Preserve unrelated dirty paths.
- Refuse missing commits, rewritten history, or ambiguous root identity.
- Record rollback as a reversible operation.

## Live acceptance

### M1 live acceptance

Run a real end-of-task review against three controlled sessions:

1. reusable procedure → skill + evidence envelope;
2. changing factual observation → factual-memory recommendation, no skill;
3. transient failure → discard, no artifact.

Inspect the actual local root, envelope, review ledger, and absence of public
repository changes. The first controlled pass must be 3/3. If one case
disagrees, rerun that unchanged case once to distinguish variance from a
reproducible misroute. A repeated misroute requires a corrected prompt or
contract. An isolated miss is recorded as variance, but the gate remains closed
until that case produces two consecutive correct unchanged runs.

### M2

Run the real model on a source/sibling fixture pair with and without one
candidate skill. Confirm the useful candidate passes and an intentionally
overfitted candidate fails.

### M3

Create a disposable local skill referenced by a test LaunchAgent, prove the
curator protects it, remove the reference, execute a reversible two-root test
run, and roll it back without touching unrelated fixtures.

### M5

Run a public, synthetic suite with no private repository or session content:

1. one capability skill whose deterministic outcome fails in control and
   succeeds in candidate;
2. one encoded-preference skill whose control completes the task but violates
   a required workflow;
3. one related task that an intentionally overfitted candidate damages;
4. positive and negative activation prompts.

Run the gate profile through real Copilot, Claude, and Codex executors on the
exact reviewed tree. Inspect each native raw log, normalized trace,
deterministic artifact, blinded comparison, per-executor certificate,
aggregate receipt, and schema-v3 authority document. Confirm the helpful
candidates pass, the overfitted candidate cannot promote, no negative prompt
loads the skill, and no unrelated home or skill content appears.

Then alter one bound input, prove the gate becomes stale, replace one
authorized producer or comparator digest and prove the bundle is refused,
activate the halt switch during a disposable run, and execute the documented
rollback without deleting evaluation evidence or enabling promotion.

## Rollout and compatibility

- Schema v1 provenance remains valid throughout M1.
- Schema v2 is written only after its reader and validator are installed.
- Existing skills are migrated lazily when new evidence is appended.
- No bulk migration is required to deploy M1.
- Evaluation is mandatory for promotion and consolidation rewrites after the
  M2 live acceptance suite passed.
- M1-created envelopes begin as `not_evaluated`. Once M2 ships, promotion of a
  pre-gate skill triggers a fresh evaluation rather than requiring a bulk
  backfill.
- Curator mutation remains dry-run plus explicit approval.
- Every milestone is separately releasable and reversible.
- M5 writes version-2 receipts and schema-v3 authority documents only after
  their readers, validators, gates, and rollback checks are installed.
- Existing M2 receipts remain immutable historical evidence but do not satisfy
  an M5 cross-CLI policy.
- M5 is enabled only after the real three-executor acceptance suite passes.
  Until then, the existing M2 gate remains authoritative.

## Definition of Done

### Plan

- [x] Objective, non-goals, reuse contract, data flow, failure model,
      privacy boundary, milestones, tests, live acceptance, rollout, and
      rollback are explicit.

### M1 Definition of Done

- [x] Artifact-routing contract is one shared source of truth.
- [x] Evidence-envelope schema and helper are implemented and documented.
- [x] Legacy provenance is backward compatible.
- [x] Dispatch, sweep, creation, and memory-roll paths use the routing contract.
- [x] Evidence deduplication counts independent tasks rather than mentions.
- [x] Malformed evidence fails closed before autonomous mutation.
- [x] Deterministic routing, schema, migration, and guard tests pass.
- [x] Real three-case acceptance produces skill, factual-memory recommendation,
      and discard outcomes with no public mutation.
- [x] Dual review has no verified in-scope material finding.
- [x] M1 is pushed, installed, and observed through the existing self-test.

### Later milestones

- [x] M2 evaluation gate is implemented and rejects an overfitted skill.
- [x] M3 scheduled dependency protection and run rollback are implemented.
- [x] M4 is either implemented against a real platform boundary or explicitly
      closed as unnecessary.
- [ ] M5 cross-CLI certification is implemented and enforces the selected
      executor policy.

### Cross-CLI skill certification Definition of Done

- [ ] Dreaming owns suite policy, candidate identity, certification decisions,
      receipts, authority documents, promotion gates, and rollback authority.
- [ ] The trial harness is replaceable and cannot write Dreaming state or
      decide policy.
- [ ] Copilot, Claude, and Codex implement the same versioned
      `skill-evaluation-executor` contract.
- [ ] Gate-profile evidence uses three matched trials per arm and records exact
      executor, model, CLI, adapter, harness, grader, comparator, tool, and
      budget identity.
- [ ] Capability, encoded-preference, related-task, and activation policies
      have deterministic pass, regression, and inconclusive fixtures.
- [ ] Every required executor has an independent certificate; results are not
      pooled across providers.
- [ ] Promotion and consolidation reject partial, stale, regressing,
      unauthorized, or legacy-only evidence.
- [ ] Schema-v2 cases and envelopes remain readable; schema-v3 authority in
      version-2 evaluation state cannot be read, relabeled, or authorized by an
      older gate.
- [ ] Unauthorized routes, inherited instructions, unrelated skills, and
      native session roots are absent from trial packets and traces.
- [ ] The real three-CLI acceptance suite passes on the reviewed tree.
- [ ] Rollback preserves evidence, restores the prior gate, and keeps promotion
      halted until self-test passes.

### All phases Definition of Done

- [x] M1 routing and evidence envelopes are released and installed.
- [x] M2 measures source benefit and sibling regressions, and gates promotion
      or major rewrites.
- [x] M3 protects scheduled dependencies and reverses a complete multi-root
      curator run without overwriting unrelated work.
- [x] M4 either enforces a real visible hot-context budget or records evidence
      that no enforceable local boundary exists and closes without a second
      memory store.
- [x] Every implemented milestone has deterministic coverage, a matching live
      proof, clean dual review, a released version, and installed self-test
      evidence.

## Explicit non-goals

- Build a centralized multi-user service, scheduler, artifact database, or
  approval UI.
- Replace GitHub Copilot Memory or duplicate repository-fact storage.
- Copy private session content into a public skill or commit.
- Require citations for timeless procedural wording.
- Evaluate every small wording or reference-only edit.
- Automatically publish agent-created skills.
- Make curator mutation unattended.
- Introduce a second writer lock, ledger, or provenance authority.
- Treat all models or CLI harnesses as statistically interchangeable.
- Require a hosted service or private benchmark system for local promotion.
- Add Linux or Windows lifecycle support as part of cross-CLI certification.
