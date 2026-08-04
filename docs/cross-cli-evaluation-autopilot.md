# Cross-CLI evaluation implementation charter

## Objective

Achieve the Definition of Done in
`docs/evidence-backed-learning-plan.md`, the
"Cross-CLI skill certification Definition of Done" section: complete every
remaining M5 phase so Dreaming certifies skills independently through Copilot
CLI, Claude Code, and Codex using the replaceable trial harness. Keep working
through the plan; finish only once every item in that section is verifiably
met.

## Charter

Keep building against the plan at
`docs/evidence-backed-learning-plan.md` through M5 in
`/Users/dfrysinger/code/dreaming`. Follow the required process skills below.
Use rubber-duck to brainstorm solutions and align on paths forward whenever
you get stuck. Keep the plan and its phase status up to date so future agents
can pick it up. Use subagents to parallelize independent investigation,
validation, and review work. Do not push; keep work local for this run. If
coordinating with other agents, do not wait for them to push to main;
cherry-pick what you need from their worktree or branch. Decide every
reversible question yourself with rubber-duck rather than asking the user.
Freely use real model tokens within reason and raise the local app or CLI for
testing when needed. Stay on this course until the objective's Definition of
Done, the "Cross-CLI skill certification Definition of Done" section, is met.

### Required process skills

- **Governing:** `/dfrysinger-skills:development-loop` owns phase order,
  acceptance, live proof, review, and completion.
- **Execution:** `/deep:behavior-validation` owns the final real CLI behavior
  proof when deterministic and contract tests are green.
- **Context:** `/dfrysinger-skills:self-compact` owns compaction at the
  governing workflow's compaction points or when context becomes noisy. Persist
  the current phase, candidate identity, checks, review state, and live-proof
  receipt before invoking it.

## Current baton

- **Lane:** critical.
- **Branch:** `feature/multi-cli-dreaming`.
- **Push policy:** local only.
- **Plan:** `docs/evidence-backed-learning-plan.md`.
- **Harness contract:** `docs/skill-evaluation-trial-harness-design.md`.
- **Current phase:** M5.1, policy and schemas.
- **Landed baseline:** `b78c6ba6b7316f1406ac82677bcf4384af86ea2a`.
- **Uncommitted reviewed design:** both design documents and this phased work
  order.
- **Live-proof receipt:** not started; required before implementation review.
- **Completion authority:** the "Cross-CLI skill certification Definition of
  Done" section in the plan.
