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
- **Installed candidate:** `635a581b0499f5a218f1b858289cff757ce7729d`.
- **Completed locally:** M5.1 policy and schemas at `7cab64a`; M5.2 sealed
  trial harness core at `745f32e`; M5.3 native adapters at `8002c9c`; M5.4
  Dreaming certification integration in the current candidate. The
  required/advisory implementation now defaults required evaluation to
  Copilot, keeps Claude and Codex opt-in advisory, partitions harness
  infrastructure state, separates `policy_id` from `observation_plan_id`,
  derives `required_certificate_set_id` from required evidence only, and
  preserves required authority across advisory-only policy changes.
- **Installed runtime:** `635a581b0499f5a218f1b858289cff757ce7729d`
  with generation `20260806T183040Z-install-29428`, behind the active halt
  switch.
- **Live-proof receipt:**
  `/Users/dfrysinger/.copilot/session-state/c8b94df9-d6e2-5cd7-a707-155405e27d8f/files/cross-cli-m5-live-proof.txt`;
  the existing receipt covers older candidate `cc5fe2e` and is stale. Rollout
  remains halted pending exact-tree installed self-test and refreshed live
  proof.
- **Historical live evidence:** The evidence below established the
  required/advisory behavior on earlier generations. It remains useful as
  regression and rollback evidence, but it does not satisfy exact-tree rollout
  gates for installed generation `20260806T183040Z-install-29428`. The
  required/advisory candidate passes the
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
  Copilot-backed authority. A generation-bound installed self-test passed for
  an older candidate; the exact-tree generation has not yet produced a passing
  receipt.
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
  Fresh deterministic authority checks passed all 61 policy, certification,
  harness, cancellation, cleanup, stale-evidence, producer-identity, and legacy
  rejection contracts. The halted rollback restored the prior runner, whose
  legacy evaluator refused version-2 authority; both authoritative aggregate
  evidence hashes remained unchanged. Reinstalling the reviewed tree created
  generation `20260806T131624Z-install-23818`, restored verified managed
  Copilot instructions and repository-backed LaunchAgents, and preserved the
  halt. Admission samples were 16.42 and 15.43 with a 0.187-second subprocess
  probe. All four implicated suites passed, the immediate pre-full sample was
  13.30, and the installed self-test completed with `0 failure(s)`. Its receipt
  exactly matches the restored generation.
  A dedicated Copilot gate then evaluated an intentionally broad,
  overfitted candidate. The intended candidate passed all three trials while
  its controls failed, but all three related candidate trials failed against
  passing controls and all three activation-negative trials regressed. The
  required Copilot certificate and aggregate were `regression`,
  `authoritative: false`, and authority issuance refused.
- **Behavior-validation status:** Formal external validation designed 50
  scenarios across seven behavioral areas and executed the 12 highest-value
  scenarios. Final blind adjudication recorded eight supported, one flaky, one
  insufficient, and two not-supported scenarios; 38 remain explicitly
  deferred by the global execution budget. Investigation found two
  product-facing replay and diagnostic defects. Retained normalized suite and
  policy inputs now replay through `v2-result-certify`, and independent
  verifier failures now emit one public `REFUSED:` line before nested detail.
  External CLI follow-up reproduces the retained overfit regression certificate
  and the forged-nonce refusal with unchanged pointers. SC-017 remains a
  validation-fixture finding: its detached worktree path was correctly rejected
  by the reviewed-harness trust boundary, while the reviewed checkout proved
  advisory regression and unavailability do not change Copilot authority.
  SC-025 remains honestly recorded as flaky after its permitted retry. This
  behavior-validation evidence must be refreshed or explicitly bound to the
  exact final candidate before rollout.
- **Review closure:** dual review, critical ensemble review, remediation, and
  fix-delta review are complete. Final reviewed commit
  `635a581b0499f5a218f1b858289cff757ce7729d` is installed locally. Its final
  delta changes only the synthetic standalone-core timeout regression and this
  charter; Claude Opus 5 and GPT-5.6 Terra independently reported no findings
  and confirmed the test remains discriminating.
- **Current checkpoint:** commit
  `71ac85d7a63b2a6de45a946dc339e1b3456367c0` is installed as generation
  `20260807T002657Z-install-27227`. Its representative-load,
  generation-bound self-test ran to completion with
  `== result: 5 failure(s) ==`; the activation generation remained unchanged
  and no `selftest-passed-generation` receipt was written. The failed groups
  were standalone core, native adapter matrix, trial harness, skill-evaluation
  vendor adapters, and Dreaming certification. Retained evidence identifies
  several fixture processes that timed out before producing their expected
  deterministic result. The short-timeout vendor test also reported the
  correct `executor-timeout` response with no raw output, but incorrectly
  required the native child to publish a PID before cancellation. The
  correction preserves the intentional one- and three-second timeout
  regressions, gives non-timeout fixtures a 120-second test budget, and checks
  confirmed-start process-group cleanup in the explicit-cancellation phase.
  All five implicated suites pass serially under representative host load:
  standalone core, native adapter matrix, trial harness, skill-evaluation
  vendor adapters, and Dreaming certification. No production timeout, load
  gate, or arbitrary sleep changed. The halt remains active; Dreaming and its
  watchdog remain stopped.
- **Next checkpoint:** commit the focused correction locally, reinstall the
  exact tree behind the halt, and rerun the complete generation-bound installed
  self-test. Require `== result: 0 failure(s) ==`, an unchanged activation
  generation, and a matching passing-generation receipt. Then refresh
  exact-tree live proof, audit every Definition-of-Done criterion, and enable
  only if all gates pass.
- **Completion authority:** the "Cross-CLI skill certification Definition of
  Done" section in the plan.
