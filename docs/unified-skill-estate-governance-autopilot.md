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
- **Current phase:** implement the source-qualified atomic settings disable
  transaction (CHK-05). CHK-02 through CHK-04 are complete; mini-side action
  planning, dispatch, and installed reversible proof remain intentionally
  deferred until the complete evidence-binding and settings-ledger path exists.
- **Implementation order:** census and identity model; estate dashboard and
  unresolved mappings; evaluation and recommendations; remote personal-skill
  archive/restore; atomic settings transaction and source qualification;
  plugin disable/restore; installed two-host proof and rollback.
- **Implemented candidate:** local commits `79bf9b0`, `9cde88e`, `a4e270b`,
  `ef677e2`, `edab92c`, and `d5587e3` provide the bounded census, installed
  adapter upgrades, provenance authority matrix, sealed retry-safe remote
  personal-skill archive/restore receiver, and complete report-only plugin
  capability gate. Never push these commits.
- **Installed state:** the Mac mini completed managed install, self-test, and
  enable for `a4e270b`. The halt switch is absent, the self-test exit code is
  `0`, the dashboard is running, and the remote source, publisher, executors,
  and receiver-bound `estate_census` adapter are present.
- **Live-proof status:** the first installed two-host census passed with a
  content-addressed receipt and complete reconciliation: 93 effective skill
  instances, 107 physical instances, 14 physical-only instances, five plugin
  packages, three enabled plugin packages, and zero unresolved runtime skills.
  Independent collection equivalence, restart persistence, dashboard privacy,
  and receiver/collector identity mismatch failures have passed. Stale-state
  reporting, reversible installed mutation, before/final estate equality, and
  rollback remain before the complete installed proof can pass.
- **CHK-04 closure:** the final MacBook live proof found five plugin packages,
  three enabled packages, complete capability inventories, and zero unresolved
  mappings. The estate suite passed 44 tests; core, receiver transport,
  dashboard, and dashboard-contract suites passed. Two-family fix verification
  found no CHK-04 blocker. Trusted execution-anchor enforcement remains owned
  by CHK-07 rather than the report-only evaluator.
- **Completion authority:** only the design's named Definition of Done; do not
  substitute partial unit checks, dashboard rendering, or one successful
  mutation for the complete installed two-host proof.
