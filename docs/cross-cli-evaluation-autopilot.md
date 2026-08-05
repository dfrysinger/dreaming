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
- **Current phase:** M5.5, rollout and live proof.
- **Landed baseline:** `b78c6ba6b7316f1406ac82677bcf4384af86ea2a`.
- **Completed locally:** M5.1 policy and schemas at `7cab64a`; M5.2 sealed
  trial harness core at `745f32e`; M5.3 native adapters at `8002c9c`; M5.4
  Dreaming certification integration in the current candidate.
- **Current candidate:** `4fadf1336c4dd528fe1b09d4fadcb31a1cbe070f` with
  installed generation `20260805T065210Z-install-97490`, behind the active
  halt switch.
- **Live-proof receipt:**
  `/Users/dfrysinger/.copilot/session-state/c8b94df9-d6e2-5cd7-a707-155405e27d8f/files/cross-cli-m5-live-proof.txt`;
  gate remains closed.
- **Current live status:** Copilot and Claude independently complete every
  one-trial capability and encoded-preference class, including exact native
  load proof, activation-negative behavior, related-task parity, blinded
  comparisons, deterministic artifacts, and sealed budgets. Copilot exposes no
  personal installed-plugin inventory under the metadata-only real-home
  exception. The targeted harness, cross-CLI policy, and Dreaming
  certification suites pass 29/29, 8/8, and 15/15. Codex remains the only
  blocker because its account usage limit does not reset until August 7, 2026
  at 10:41 PM.
- **Next checkpoint:** after Codex quota resets, rerun the one-trial capability
  and encoded-preference matrices through all three real providers. If Codex
  evidence is complete, run both full three-trial gates and continue through
  authority, negative evidence, rollback, review, final installed self-test,
  documentation, and enablement. Do not substitute or waive the required Codex
  executor.
- **Completion authority:** the "Cross-CLI skill certification Definition of
  Done" section in the plan.
