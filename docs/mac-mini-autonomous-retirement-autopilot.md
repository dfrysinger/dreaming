# Mac mini migration and autonomous retirement charter

## Objective

Achieve the Definition of Done in
`docs/mac-mini-autonomous-retirement-design.md`, making the Mac mini the sole
Dreaming processor and learned-skill authority while this MacBook supplies
Copilot transcripts and receives the verified learned-skill publication.

## Charter

Work in `/Users/dfrysinger/code/dreaming` on
`feature/multi-cli-dreaming`. Keep all work local until the migration requires
the existing public skills repository transaction; do not push Dreaming.
Preserve the MacBook halt switch and never enable scheduling on both machines.
Keep transcript reads, review execution, learned-skill decisions, publication
transport, rollback, and health reporting under their existing owners. The mini
must not install the Dreaming-learned Copilot bundle locally. The MacBook may
install a receiver-verified bundle but must not schedule Dreaming or decide its
contents. Preserve manual authority over hand-made skills.

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
- **Definition of Done:** `Definition of Done: MacBook client publication`.
- **Source safety:** the source halt switch must remain present.
- **Destination:** Tailscale SSH host `mac-mini`, Apple Silicon, macOS 26.6,
  FileVault off, approximately 130 GiB free.
- **Destination readiness:** the exact Copilot CLI binary, GitHub CLI,
  candidate Dreaming tree, verified shared bundle, managed skill roots,
  transferable authority state, and halted LaunchAgents are installed. The
  final candidate and authority state are synchronized, the destination
  installer has rendered the corrected configuration, and the exact final
  source and destination authority manifests match.
- **Placement:** Copilot transcripts remain on the MacBook and are read by the
  mini through the SSH source. Review execution and learned-skill decisions
  remain on the mini. Copilot learned-skill bundles are transferred to and
  installed on the MacBook through a receiver-verified SSH publisher. Codex
  sessions and review execution remain local to the mini; Codex is not a
  publication target.
- **Current phase:** live validation. The candidate is committed locally
  through `e45a4ba`; the mini is halted on the remote-only topology while the
  corrected activation generation completes certification.
- **Existing learned catalog:** the mini now contains the exact six learned
  skills and seven-commit MacBook history at
  `b4b1e3a6ff4612e5e6a3dc71b8bbf5e1e1269932`, with a clean worktree and no
  Git remote.
- **Existing publication:** the mini has no Dreaming Copilot registration.
  Its receiver-bound SSH publisher has replaced the MacBook Copilot
  registration with the exact six-skill content-addressed bundle while
  preserving the MacBook Claude and Codex descriptors and their shared bundle.
- **Live-proof status:** the real SSH install and verify transaction passed,
  the mini summary is committed and receiver-bound, publication recovery is
  clear, all six skills resolve on the MacBook, the mini has no local bundle,
  and the MacBook halt and unloaded scheduler are unchanged. Final installed
  certification, enable, dashboard observation, rollback proof, and
  implementation review remain.
- **Completed baseline:** shadow lifecycle landed locally at `10c20c1`.
- **Curator review:** completed without skill mutation. It scanned `73` managed
  skills and proposed `9` agent-created consolidations into `3` new umbrellas
  plus `1` hand-made destination. The hand-made destination proposal remains
  manual under this design. No pure pruning had sufficient evidence.
- **Review:** design review closed the remote publication transaction,
  ambiguous SSH recovery, ownership-state separation, wrong-host refusal,
  existing-publication adoption, shared Claude/Codex bundle retention, and
  reverse-placement findings.
- **Remaining:** complete installed certification for `e45a4ba`, enable the
  certified generation, capture the dashboard and reverse-placement proof,
  complete implementation review and final validation, update the acceptance
  receipt and design checklist, and commit locally without pushing Dreaming.
