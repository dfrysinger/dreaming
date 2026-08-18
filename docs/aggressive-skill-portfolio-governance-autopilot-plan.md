# Aggressive skill portfolio governance autopilot plan

## Order of work

1. Finish the retained proof and cleanup for
   `docs/dreaming-review-backlog-capacity-design.md` without changing its
   installed cadence until the natural-tick evidence is adjudicated.
2. Implement
   `docs/aggressive-skill-portfolio-governance-design.md` in report-only
   slices before enabling any mutation.
3. Add governed user intents, personal withdrawal/archive/restore, and
   whole-plugin disable/restore only after their report-only checks pass.
4. Complete installed, browser, rollback, and paired-review proof.

## Current baton

- Branch: `feature/multi-cli-dreaming`
- Reviewed governance design commit: `6f82ab2`
- Capacity implementation commit: `f2b4fcb`
- Capacity natural-tick watcher evidence:
  `~/.copilot/session-state/c7947aa7-3025-4b4e-977d-294626e8e949/files/behavior-validation-review-capacity-f2b4fcb/traces/SC-010/04-natural-successor.json`
- Capacity work remaining: adjudicate SC-010, retry direct-tailnet SC-007,
  desktop and 390px visual proof, governance-lever proof, rollback SC-025,
  paired implementation review, final install, report, local proof commit.
- Governance work remaining: all implementation and PORT-CHK-01 through
  PORT-CHK-14.

## Definition of Done: Capacity closure and aggressive portfolio governance

- [ ] Every item under
      `## Definition of Done: Dreaming review backlog capacity` in
      `docs/dreaming-review-backlog-capacity-design.md` is supported by
      retained evidence.
- [ ] Every item under
      `## Definition of Done: Aggressive skill portfolio governance` in
      `docs/aggressive-skill-portfolio-governance-design.md` is supported by
      retained evidence.
- [ ] The Mac mini remains the sole scheduled Dreaming owner.
- [ ] Final installed generations pass their required self-tests and live
      acceptance flows.
- [ ] Both implementation slices pass paired review with no unresolved
      in-scope must-fix finding.
- [ ] Rollback restores exact skills and plugin settings while preserving
      decisions, dispositions, receipts, and recovery evidence.
- [ ] All work is committed locally on `feature/multi-cli-dreaming`; nothing
      is pushed.
- [ ] The hourly charter re-brief is stopped.
