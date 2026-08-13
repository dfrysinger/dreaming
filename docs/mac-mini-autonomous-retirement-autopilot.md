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
- **Current phase:** complete. Candidate `7ede47d` passed final installed
  certification, live two-host acceptance, and round-two implementation review.
- **Existing learned catalog:** the mini now contains the exact six learned
  skills and seven-commit MacBook history at
  `b4b1e3a6ff4612e5e6a3dc71b8bbf5e1e1269932`, with a clean worktree and no
  Git remote.
- **Existing publication:** the mini has no Dreaming Copilot registration.
  Its receiver-bound SSH publisher has replaced the MacBook Copilot
  registration with the exact six-skill content-addressed bundle while
  preserving the MacBook Claude and Codex descriptors and their shared bundle.
- **Live-proof status:** PASS at
  `/Users/dfrysinger/.copilot/session-state/c8b94df9-d6e2-5cd7-a707-155405e27d8f/files/macbook-client-publication-live-proof.md`.
  Activation generation `20260813T080718Z-install-19443` passed with zero
  failures and is enabled. The authenticated dashboard is healthy, lists six
  skills, and binds `copilot@MacBook` to the active receiver identity and
  script digests. The MacBook has one verified six-skill bundle, remains
  halted and unloaded with zero workers, and retains its exact Claude and
  Codex descriptors and shared bundle.
- **Completed baseline:** shadow lifecycle landed locally at `10c20c1`.
- **Curator review:** completed without skill mutation. It scanned `73` managed
  skills and proposed `9` agent-created consolidations into `3` new umbrellas
  plus `1` hand-made destination. The hand-made destination proposal remains
  manual under this design. No pure pruning had sufficient evidence.
- **Review:** design review closed the remote publication transaction,
  ambiguous SSH recovery, ownership-state separation, wrong-host refusal,
  existing-publication adoption, shared Claude/Codex bundle retention, and
  reverse-placement findings. Round-one implementation review found archive
  expansion, stale-summary, and preflight-order gaps; `7ede47d` fixed all
  three, and both reviewer families closed them in round two with no remaining
  material finding.
- **Remaining:** none for `Definition of Done: MacBook client publication`.
