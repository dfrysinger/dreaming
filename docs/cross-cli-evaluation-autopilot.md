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
- **Current candidate:** `e934a5e42ad60f4cb33ee0cfbf29416d331d6cc1` with
  installed generation `20260805T075531Z-install-19150`, behind the active
  halt switch.
- **Live-proof receipt:**
  `/Users/dfrysinger/.copilot/session-state/c8b94df9-d6e2-5cd7-a707-155405e27d8f/files/cross-cli-m5-live-proof.txt`;
  gate remains closed.
- **Current live status:** Copilot and Claude independently completed every
  one-trial capability and encoded-preference class on the previous adapter
  identity, including exact native load proof, activation-negative behavior,
  related-task parity, blinded comparisons, deterministic artifacts, and
  sealed budgets. Copilot exposed no personal installed-plugin inventory under
  the metadata-only real-home exception. The current targeted harness,
  cross-CLI policy, Dreaming certification, native-adapter, and installer
  suites pass. Activation generation `20260805T075531Z-install-19150` passed
  the full installed self-test with zero failures while the halt remained
  active. Current pinned one-trial capability and encoded-preference matrices
  pass every Copilot and Claude class on adapter hash
  `602aa04a5305ead9c556ade76cfe29e616acd2ba00973d5b770aff70a90cbe99`.
  The retained matrices bind immutable Copilot CLI bytes after a mutable
  installed executable changed during an earlier run and correctly triggered
  executable-identity rejection. Codex remains gated on account quota
  availability after August 7, 2026 at 10:41 PM; it is required and cannot be
  substituted or waived.
- **Next checkpoint:** after Codex quota is available, run the same pinned
  one-trial matrices through all three real providers. If Codex evidence is
  complete, run both full three-trial gates and continue through authority,
  negative evidence, rollback, review, final installed self-test,
  documentation, and enablement.
- **Completion authority:** the "Cross-CLI skill certification Definition of
  Done" section in the plan.
