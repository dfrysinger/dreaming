# Cross-CLI evaluation implementation charter

## Objective

Achieve the Definition of Done in
`docs/evidence-backed-learning-plan.md`, the
"Cross-CLI skill certification Definition of Done" section: complete every
remaining M5 phase so Dreaming requires independent Copilot CLI certification
by default and can collect independent advisory Claude Code and Codex evidence
through the replaceable trial harness. Keep working through the plan; finish
only once every item in that section is verifiably met.

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
- **Landed baseline:** `af92f812164dcdccfee4957b382560a4f981d896`.
- **Installed candidate:** `cc5fe2ed6bc8516cff56f5d01e6809a29c327e29`.
- **Completed locally:** M5.1 policy and schemas at `7cab64a`; M5.2 sealed
  trial harness core at `745f32e`; M5.3 native adapters at `8002c9c`; M5.4
  Dreaming certification integration in the current candidate. The
  required/advisory implementation now defaults required evaluation to
  Copilot, keeps Claude and Codex opt-in advisory, partitions harness
  infrastructure state, separates `policy_id` from `observation_plan_id`,
  derives `required_certificate_set_id` from required evidence only, and
  preserves required authority across advisory-only policy changes.
- **Installed runtime:** `cc5fe2ed6bc8516cff56f5d01e6809a29c327e29` with
  generation `20260806T090147Z-install-48248`, behind the active halt switch.
- **Live-proof receipt:**
  `/Users/dfrysinger/.copilot/session-state/c8b94df9-d6e2-5cd7-a707-155405e27d8f/files/cross-cli-m5-live-proof.txt`;
  gate remains closed.
- **Current live status:** The new required/advisory candidate passes the
  deterministic harness, policy, certification, native-adapter, daemon, and
  installer suites. Deterministic end-to-end certification proves advisory
  regression and advisory collection failure remain visible without changing
  Copilot-required authority, while empty required sets and required failures
  refuse. Copilot and Claude independently completed every
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
  executable-identity rejection. The selected policy now makes Copilot the
  installed required default and treats configured Claude and Codex routes as
  advisory. Advisory unavailability remains visible but does not block
  Copilot-backed authority. The current generation-bound installed self-test
  now passes.
  The first attempt was infrastructure-invalid under sustained load between 38
  and 70. A retry entered under two compliant load samples and passed the
  subprocess probe plus every previously failed targeted suite, but load had
  risen to 20.65 immediately before the full suite. Its only failure was the
  harness output-flood fixture: the fixture's 100,000-byte cap required two
  pipe reads, so scheduler delay let its timeout win after the same suite had
  passed standalone. The runtime bounds remain unchanged; the fixture now uses
  a 4,096-byte cap so normal protocol envelopes fit while one read
  deterministically proves streaming overflow.
  Neither invalid attempt produced a passing receipt, and no test process
  remains. The corrected fixture passed three complete harness runs while load
  ranged from 19.76 to 26.17, and candidate `cc5fe2e` is installed as
  generation `20260806T090147Z-install-48248` behind the halt. Its next retry
  entered under load samples 15.84 and 12.12 with a 0.089-second subprocess
  probe, and all four targeted suites passed. The mandatory immediate
  pre-full-run load sample was 23.67, so the full self-test was not launched
  and no receipt was written. The next scheduled retry stopped at its first
  admission sample of 18.24, above the 18-CPU ceiling; it ran no probe, targeted
  suite, or installed self-test. The accepted retry entered under load samples
  11.04 and 10.19 with a 0.137-second subprocess probe. All four implicated
  targeted suites passed, the immediate pre-full sample was 11.87, and the
  installed self-test completed with `0 failure(s)`. The independently written
  `selftest-passed-generation` receipt exactly matches active generation
  `20260806T090147Z-install-48248`; the halt remains active.
  The first two three-trial capability attempts then failed closed because one
  no-skill control wandered through broad instruction search until it crossed
  the sealed output limit. The earliest divergence was the synthetic prompt's
  missing unavailable contract, not the production harness. The live fixture
  now requires exact skill invocation and a fixed unavailable response without
  search. Fresh bounded capability run `capability-cbf3d7c-bounded1` completed
  all required Copilot infrastructure with zero errors: three intended
  candidate trials passed, three intended controls failed behaviorally,
  related-task parity passed, both activation classes passed, and final
  certification was authoritative. Claude remained visible as advisory
  inconclusive because its OAuth token is revoked; Codex remained visible as
  advisory inconclusive because its quota is exhausted. Neither advisory
  failure blocked Copilot authority. Fresh bounded encoded-preference run
  `encoded-cbf3d7c-bounded1` then completed the same required matrix with zero
  Copilot infrastructure errors: all three intended candidates passed, all
  three intended controls failed behaviorally, related candidate/control
  trials passed with comparison parity, and both activation classes passed.
  Its certification is also authoritative. Claude again remained revoked-token
  advisory inconclusive and Codex quota-inconclusive, without granting or
  blocking authority.
- **Next checkpoint:** prove advisory pass, regression, inconclusive, and
  unavailable states cannot grant or block required authority. Then prove
  required failure, stale or partial evidence, unauthorized producers,
  overfitted candidates, and legacy evidence cannot authorize. Continue
  through rollback, review, final installed self-test, documentation, and
  enablement.
- **Completion authority:** the "Cross-CLI skill certification Definition of
  Done" section in the plan.
