# Mac mini migration and autonomous retirement charter

## Objective

Achieve the Definition of Done in
`docs/mac-mini-autonomous-retirement-design.md`, moving Dreaming's sole
scheduled ownership to the Mac mini and enabling recoverable autonomous
retirement for eligible agent-created skills.

## Charter

Work in `/Users/dfrysinger/code/dreaming` on
`feature/multi-cli-dreaming`. Keep all work local until the migration requires
the existing public skills repository transaction; do not push Dreaming.
Preserve the source halt switch until the Mac mini passes the exact installed
self-test. Never enable both machines. Run the curator review before mutation,
but treat its report as an audit record rather than a human approval gate for
eligible agent-created-only operations. Preserve manual authority over
hand-made skills. Use existing migration, installer, transaction, archive,
evaluation, review, and restore owners rather than creating alternatives.

### Required process skills

- **Governing:** `/dfrysinger-skills:development-loop` owns phase order,
  live-proof gating, implementation review, final validation, and landing.
- **Design:** `/dfrysinger-skills:design-doc` owns the reviewed work order
  before implementation.
- **Execution:** `/dfrysinger-skills:skill-curator`,
  `/dfrysinger-skills:macos-agent-shell`,
  `/dfrysinger-skills:macos-remote-access-troubleshooting`, and
  `/dfrysinger-skills:tailscale-macos-connectivity` own their respective
  curator and remote-host paths.
- **Review:** `/dfrysinger-skills:dual-review` owns design and implementation
  review.
- **Context:** `/dfrysinger-skills:self-compact` owns compaction after this
  charter and reminder are live.

## Current baton

- **Lane:** critical.
- **Workspace:** `/Users/dfrysinger/code/dreaming`.
- **Branch:** `feature/multi-cli-dreaming`.
- **Push policy:** do not push Dreaming; public skill-root pushes occur only
  through a completed curator transaction when required.
- **Design:** `docs/mac-mini-autonomous-retirement-design.md`.
- **Definition of Done:** `Definition of Done: Mac mini ownership and
  autonomous retirement`.
- **Source safety:** the source halt switch must remain present.
- **Destination:** Tailscale SSH host `mac-mini`, Apple Silicon, macOS 26.6,
  FileVault off, approximately 130 GiB free.
- **Destination readiness:** the exact Copilot CLI binary, GitHub CLI,
  candidate Dreaming tree, verified shared bundle, managed skill roots,
  transferable authority state, and halted LaunchAgents are installed. The
  final candidate and authority state are synchronized, the destination
  installer has rendered the corrected configuration, and the exact final
  source and destination authority manifests match.
- **Current phase:** local landing. The Mac mini is
  configured for authenticated Copilot and Codex source and review execution.
  Copilot is the scheduled publisher. Codex retains its current installed
  bundle but is not a scheduled publication target because its native plugin
  inventory can block under the daemon on a Dropbox-backed local marketplace.
  Claude Code remains installed but inactive because it is unauthenticated on
  both Macs. The final reviewed installed self-test passed with zero failures and is
  bound to activation generation `20260812T170255Z-install-88451`. The source
  remains halted and unloaded; the Mac mini is the sole active scheduler.
- **Live-proof status:** PASS. Earlier post-activation passes failed closed before
  roll or prune began while the adapter-path and Codex publication behavior
  were isolated. No retirement mutation occurred, every failed activation
  receipt is retained, and verified orphaned inventory processes were removed.
  The guarded ownership cutover is complete. Final reviewed scheduled run
  `20260812T174948Z-29405` finished `status: ok`;
  consolidation succeeded and weekly roll/prune were correctly not due. The
  supported runtime keeps Codex for discovery and review while routing
  scheduled publication only through healthy Copilot. Evidence is recorded in
  `mac-mini-live-proof.txt` and `mac-mini-cutover-receipt.json`.
- **Completed baseline:** shadow lifecycle landed locally at `10c20c1`.
- **Curator review:** completed without skill mutation. It scanned `73` managed
  skills and proposed `9` agent-created consolidations into `3` new umbrellas
  plus `1` hand-made destination. The hand-made destination proposal remains
  manual under this design. No pure pruning had sufficient evidence.
- **Review:** dual-review round two closed its initial three findings. The
  critical-lane judged ensemble confirmed additional fail-closed gaps; all
  material families were repaired, and both reviewer families closed the final
  fix delta with no unresolved material finding.
- **Remaining:** commit locally, mark the migration todos complete, and stop
  the charter schedule.
