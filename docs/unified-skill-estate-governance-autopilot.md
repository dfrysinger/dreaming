# Unified skill estate governance charter

## Objective

Achieve the Definition of Done in
`docs/unified-skill-estate-governance-design.md`, the
"Definition of Done: Unified skill estate governance" section: make the Mac
mini govern the complete bounded Copilot skill estate enabled on the MacBook,
with protected human work, automatic recoverable machine-skill retirement,
qualified plugin disablement, and truthful estate-wide reporting. Keep working
through the design; finish only once every item in that section is verifiably
met.

## Charter

Keep building against the reviewed design at
`docs/unified-skill-estate-governance-design.md` in
`/Users/dfrysinger/code/dreaming` on `feature/multi-cli-dreaming`. Follow the
required process skills below.

If a reminder arrives while you are making a user-directed edit to the design,
brief, or charter, finish the current coherent edit and persist it before
reconciling against the authoritative file. Never replace an in-flight revision
with an older persisted version.

Use rubber-duck to resolve implementation ambiguity or a blocked trace. Keep
this baton current at meaningful phase transitions so a future agent can resume
without reconstructing history. Use subagents for independent, bounded work,
but keep one owner for live two-host proof and settings mutation. Do not push
Dreaming; keep its branch and commits local. The existing public skill
repository may change only through a completed curator-owned transaction.
Decide every reversible question autonomously rather than waiting for human
approval. The Mac mini and MacBook may be inspected, configured, and exercised
through their existing SSH trust boundary. Freely use real model tokens within
reason for required evaluation and review. Never weaken the halt switch,
provenance authority, evidence binding, recovery-required gate, user-protected
classification, or report-only fallback to make a check pass.

Stay on this course until every item in the
"Definition of Done: Unified skill estate governance" section is verifiably
met.

### Required process skills

- **Governing:** `/dfrysinger-skills:development-loop` owns phase order,
  candidate identity, live-proof gating, implementation review, final
  validation, and completion.
- **Execution:** `/dfrysinger-skills:skill-curator` owns evaluation and mutation
  authorization; `/dfrysinger-skills:git-backed-retirement` owns recoverable
  personal-skill retirement; `/dfrysinger-skills:macos-agent-shell` owns
  reliable remote Mac execution; `/dfrysinger-skills:dual-review` owns the
  required implementation review after live proof.
- **Context:** `/dfrysinger-skills:self-compact` owns compaction at the governing
  workflow's compaction points. Persist this baton first, invoke that skill as
  the final action, and do not compact merely because the hourly reminder fired
  or while live proof is active.

## Current baton

- **Lane:** critical.
- **Workspace:** `/Users/dfrysinger/code/dreaming`.
- **Branch:** `feature/multi-cli-dreaming`.
- **Push policy:** never push Dreaming.
- **Design:** `docs/unified-skill-estate-governance-design.md`.
- **Definition of Done:** `Definition of Done: Unified skill estate governance`.
- **Prior platform state:** the Mac mini is the sole Dreaming scheduler,
  transcript processor, evaluator, catalog authority, and publisher; the
  MacBook is the transcript source and learned-skill destination.
- **Review state:** the critical design passed dual review. Round 3 closed the
  final settings concurrency finding with no remaining material findings.
- **Settings decision:** plugin mutation uses same-directory macOS
  `renamex_np(..., RENAME_SWAP)` to retain the exact displaced settings inode;
  unsupported volumes and unqualified plugin source classes remain
  report-only.
- **Current phase:** install the current candidate and complete the reversible
  two-host census, mutation, reconciliation, restoration, and equality proof
  (CHK-09). CHK-02 through CHK-08 are complete.
- **Implementation order:** census and identity model; estate dashboard and
  unresolved mappings; evaluation and recommendations; remote personal-skill
  archive/restore; atomic settings transaction and source qualification;
  plugin disable/restore; dashboard truthfulness; installed two-host proof and
  rollback.
- **Implemented candidate:** local commits `79bf9b0`, `9cde88e`, `a4e270b`,
  `ef677e2`, `edab92c`, `d5587e3`, and `d5b8fd8` provide the bounded census,
  installed adapter upgrades, provenance authority matrix, sealed retry-safe
  remote personal-skill archive/restore receiver, complete report-only plugin
  capability gate, source-qualified lossless atomic settings disable
  transaction, conflict-safe ordered plugin restore ledger, and curator-owned
  evidence-bound estate action dispatch, and truthful read-only action,
  receipt, provenance, freshness, and recovery reporting. CHK-06 is commit
  `9713f83`; CHK-07 is commit `8d0d61e`; CHK-08 is commit `f59d564`. Never
  push these commits. Commit `105642b` adds the authority-bound inner-executor
  bridge, and `5ff87c2` keeps the installed dashboard action check executable.
- **Installed state:** the Mac mini completed managed install, self-test, and
  enable for clean candidate `5ff87c2` at activation generation
  `20260814T110611Z-install-3742`. The halt switch is absent, the self-test
  result is zero failures, the dashboard is running, and the remote source,
  publisher, executors, receiver-bound census adapter, and governed action
  bridge are present.
- **Live-proof status:** the first installed two-host census passed with a
  content-addressed receipt and complete reconciliation: 93 effective skill
  instances, 107 physical instances, 14 physical-only instances, five plugin
  packages, three enabled plugin packages, and zero unresolved runtime skills.
  Independent collection equivalence, restart persistence, dashboard privacy,
  and receiver/collector identity mismatch failures have passed. Stale-state
  reporting, reversible installed mutation, before/final estate equality, and
  rollback remain before the complete installed proof can pass. A fresh
  fixture-inclusive census on `5ff87c2` reports 94 effective instances, 108
  physical instances, 14 physical-only instances, five plugin packages, three
  enabled plugin packages, and zero unresolved runtime skills.
- **CHK-04 closure:** the final MacBook live proof found five plugin packages,
  three enabled packages, complete capability inventories, and zero unresolved
  mappings. The estate suite passed 44 tests; core, receiver transport,
  dashboard, and dashboard-contract suites passed. Two-family fix verification
  found no CHK-04 blocker. Trusted execution-anchor enforcement remains owned
  by CHK-07 rather than the report-only evaluator.
- **CHK-05 closure:** 19 deterministic tests exercised real macOS
  `RENAME_SWAP`, marketplace/direct qualification, absent-key semantics,
  restrictive umasks, stale and malformed settings, unsupported volumes,
  write and runtime failures, rename races, old-file-descriptor writes,
  rollback races, missing staged names, and recovery retention failures.
  Three bounded two-family review rounds closed every material finding.
- **CHK-06 closure:** 28 deterministic tests exercised sealed ledger ancestry,
  reverse-order stacked restores, byte-exact uncontended restoration,
  unrelated-edit preservation, target-key conflicts, runtime rollback, replay,
  receipt and chain tampering, missing ledger state, symlink confinement, and
  CLI restore. Three bounded two-family review rounds closed every material
  finding, including whole-ledger loss detection through an external sealed
  anchor.
- **CHK-07 closure:** 17 deterministic tests exercised current authoritative
  evidence binding for census, target, dependencies, model, routing,
  portfolio, policy, proposed estate, receiver, adapter, and halt state;
  configured authority roots; exact inner-target and receiver joins;
  concurrent retry safety; malformed and non-UTF-8 adapter output; and
  dual-fence recovery after persistence failures. Curator and SSH receiver
  regression suites passed. Bounded two-family review and finding-scoped
  closure found no remaining material defect. The installed action bridge also
  passed five focused tests covering all action kinds, request immutability,
  plugin envelopes, and configuration drift.
- **CHK-08 closure:** the Estate API now verifies census receipts and receiver
  identities end to end, delegates action verification to the canonical
  CHK-07 validator, distinguishes current, historical, stale, running,
  committed, rejected, rolled-back, recovery-required, halted, paused,
  protected, and unknown states, and exposes only sanitized identities and
  receipt hashes. The UI reports plugin and personal-skill decisions without
  offering mutation controls. Twelve focused action-reporting checks, 114
  dashboard integration checks, 54 dashboard contract checks, and the 16
  CHK-07 authority regressions passed. Two-family discovery and
  finding-scoped closure resolved malformed-state HTTP failures,
  cross-source recommendation attribution, and plugin-source reporting with
  no remaining material defect.
- **Completion authority:** only the design's named Definition of Done; do not
  substitute partial unit checks, dashboard rendering, or one successful
  mutation for the complete installed two-host proof.
