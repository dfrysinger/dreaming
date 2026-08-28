# Task opportunity evidence implementation charter

Keep building against
`docs/aggressive-skill-portfolio-governance-autopilot-plan.md` through the
task-opportunity scope in `/Users/dfrysinger/code/dreaming`. The reviewed
systemic work order is `docs/task-opportunity-evidence-design.md`, including its
constraint-provenance record, reframe gate, acceptance criteria, check
contract, and
`## Definition of Done: Profile-derived task-opportunity funnel`.

If a reminder arrives during a user-directed edit to the plan, design, brief,
or charter, finish and persist that coherent edit before reconciling against
the file. Never replace in-flight work with an older persisted version.

## Workspace, branch, and publication policy

- Work from `/Users/dfrysinger/code/dreaming`.
- Preserve unrelated user changes, including the pre-existing mode-only
  modifications to
  `skills/skill-review/scripts/evaluation_input_claims.py` and
  `skills/skill-review/scripts/test-dashboard-preview.py`.
- Keep `feature/multi-cli-dreaming` as the integration target.
- Build small functional slices on owned branches, freeze and prove each slice,
  run required review, push the reviewed branch, and open a PR targeting
  `feature/multi-cli-dreaming` when the remote target exists.
- Do not merge an unproved slice merely to make progress. Do not push the
  coordinator's dirty integration worktree or unrelated changes.
- Production installation, scheduler mutation, rollback, and restore remain
  approval-gated by their existing fail-closed boundaries even when branch and
  PR publication is authorized.

## Critical-path audit and coordination

At run start, every phase boundary, and every hourly re-brief, follow
`/dfrysinger-skills:development-loop` to:

1. Rebuild the remaining dependency graph and mark the critical path.
2. Assign every substantial independent ready scope to an available subagent
   when delegation is safe.
3. Give every file-writing delegate an isolated worktree and owned branch.
   Never allow concurrent writers in the coordinator worktree.
4. Keep the coordinator on integration, architecture decisions, blocker
   resolution, proof admission, and unowned critical-path work.
5. Batch coherent fixes before expensive builds, reviews, or installed proof.
6. Advance another independent ready item while an agent, build, or external
   boundary is pending.
7. Preserve one proof owner, one frozen candidate, and one running installed
   candidate during live proof. Other agents remain read-only then.
8. Consume a delegate's frozen reviewed commit when its integration boundary is
   ready; do not wait for it to reach the integration branch.

No user-assigned peer agent currently owns a branch or scope. Any agent created
by this run must be recorded in the plan baton with its scope, worktree,
branch, expected evidence, dependencies, and integration boundary.

## Decision hierarchy and boundaries

- The reviewed design is authoritative over older raw-session behavior and the
  earlier 1,800-second standalone-pass assertion.
- Reframe status is `CLEAR`. Return to `design-doc` before adding a second
  queue, scheduler, transcript transport, catalog-audit stage, deterministic
  semantic classifier, or another subsystem mainly preserving an inherited
  implementation default.
- Candidate-blind LLM profiling owns task interpretation. Deterministic code
  owns identities, schemas, task-occurrence aliases, accounting, capacity,
  lifecycle authority, and fail-closed refusal.
- Sessions are containers, not recurrence identities. Three current canonical
  task occurrences may come from one long-running session or several sessions.
- Eligibility checks do not spend model-operation capacity. Started calls do.
- Pre-cutover raw-session reviews remain historical and never satisfy
  profile-bound audit completion.
- Ordinary catalog publication does not reopen historical profile audits.
- Keep the Mac mini as sole scheduler, reviewer, evaluator, evidence owner, and
  deployment authority. Keep the MacBook Pro as transcript source and learned
  skill destination.
- Do not broaden this run into unrelated portfolio governance, model routing,
  transcript transport, security hardening, or cleanup of pre-existing changes.

## Functional slice order

1. Durable profile-audit disposition schema, legacy migration semantics, and
   profile-derived review admission.
2. Work-conserving profiling/review traversal and complete terminal accounting.
3. Canonical task-occurrence identity, merge/split conflict handling,
   source-derived 30-day authority, and durable three-occurrence recurrence.
4. Catalog-aware outcome routing, shadow candidate or repair flow, evaluation,
   and dashboard projection.
5. Installed Mac mini proof: configured bounds, natural four-hour cadence,
   sole-owner refusal, browser evidence, rollback preservation, and exact
   restore.

Each slice must remain independently useful and must not claim completion of a
later slice. Prefer focused PRs in this order; combine adjacent slices only
when their state transition cannot be implemented or proved independently.

## Durable proof gates

The session database and receipt artifacts under
`/Users/dfrysinger/.copilot/session-state/c7947aa7-3025-4b4e-977d-294626e8e949/files/task-opportunity-profile-funnel-proof/`
hold the runtime gates. Keep these claims `INCONCLUSIVE` until the exact frozen
candidate passes the development-loop validator:

- `profile-derived-review-admission`
- `work-conserving-capacity-accounting`
- `canonical-occurrence-recurrence`
- `catalog-audit-candidate-routing`
- `installed-natural-owner`
- `dashboard-accounting`
- `rollback-exact-restore`

Hourly charter re-brief schedule: `#6`.

Before implementation review or PR publication for a runtime slice, validate
every claim reached by that slice. Before final completion, validate all seven
claims against one frozen reviewed candidate and close their todos and receipt
rows.

## Required process skills

- **Governing:** `/dfrysinger-skills:development-loop` owns phase order,
  runtime-proof admission, review, final validation, PR publication, and
  completion. Invoke it after compaction when it is no longer active.
- **Execution:** `/dreaming-proof-first` for focused tracer-first iteration and
  installed Dreaming proof;
  `/dfrysinger-skills:dual-review` for each frozen implementation slice;
  `/writing-great-skills`, `/skill-create`, or `/skill-manage` only if the
  recurrence flow produces a real candidate or repair;
  `/behavior-validation` for installed end-to-end judgment;
  `/visual-proof` for dashboard evidence; and
  `/live-governance-lever-verification` for halt, sole-owner, rollback, and
  restore. Invoke each only in its owning phase.
- **Context:** `/dfrysinger-skills:self-compact` owns same-session compaction.
  Persist the plan baton and proof references, confirm this charter's single
  hourly reminder remains live, then invoke it as the final action. Never use a
  bare compact or compact during active live proof.

Use rubber-duck to settle ambiguous implementation choices without routine user
approval. Freely use real model operations within the reviewed bounds. Keep the
plan's Current baton accurate through every slice, PR, proof receipt, review
finding, installed generation, rollback, and restore.

## Completion

Continue until every checkbox under
`## Definition of Done: Profile-derived task-opportunity funnel` in
`docs/task-opportunity-evidence-design.md` has current retained evidence, all
seven live-proof receipts validate against the final frozen candidate, no
in-scope must-fix review finding remains, the plan and local commits are
current, the requested functional PRs have been published, and this hourly
reminder has been stopped.
