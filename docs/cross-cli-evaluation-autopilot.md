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
- **Current candidate:** `f10ba3b` with installed generation
  `20260805T062012Z-install-64992`, behind the active halt switch.
- **Live-proof receipt:**
  `/Users/dfrysinger/.copilot/session-state/c8b94df9-d6e2-5cd7-a707-155405e27d8f/files/cross-cli-m5-live-proof.txt`;
  gate remains closed.
- **Current live status:** Claude completes the one-trial capability matrix;
  Copilot now completes a direct process-confined native probe and must rerun
  the matrix with the sealed 1 MB output budget; Codex fails closed because its
  account usage limit does not reset until August 7, 2026 at 10:41 PM.
- **Next checkpoint:** rerun the one-trial matrix on the clean current
  candidate, inspect Copilot and Claude evidence, then wait for the real Codex
  route rather than substituting or waiving a required executor.
- **Completion authority:** the "Cross-CLI skill certification Definition of
  Done" section in the plan.
