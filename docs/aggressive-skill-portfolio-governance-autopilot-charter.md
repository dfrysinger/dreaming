# Aggressive skill portfolio governance autopilot charter

## Workspace and authority

- Capacity closure remains in `/Users/dfrysinger/code/dreaming` on
  `feature/multi-cli-dreaming`.
- Report-only governance preview work uses a separate worktree at
  `/Users/dfrysinger/code/dreaming-governance-preview` on
  `feature/aggressive-skill-governance-preview`.
- Create that worktree only after the plan baton records a local commit that
  contains the preview isolation contract and PORT-CHK-PREVIEW. The preview
  branch must descend from that commit.
- Follow
  `docs/aggressive-skill-portfolio-governance-autopilot-plan.md`.
- Completion is governed by
  `## Definition of Done: Capacity closure and aggressive portfolio governance`.
- Never push. Preserve unrelated work and keep every commit local.
- Finish the existing capacity proof before changing its installed scheduler,
  merging preview commits into the capacity branch, or beginning installed
  governance mutation proof.
- The isolated report-only preview may be implemented and browser-reviewed in
  parallel under `PORT-CHK-PREVIEW`.

## Required process skills

### Governing skill

- `development-loop` owns phase order, implementation, live-proof gates,
  review closure, rollback evidence, and completion.

### Execution skills

- `behavior-validation` owns installed end-to-end scenario evidence.
- `visual-proof` owns desktop and narrow dashboard evidence.
- `live-governance-lever-verification` owns halt, enablement, and rollback
  control proof.
- `dual-review` owns paired implementation review and bounded closure rounds.
- `nexus-testing` may be used only for dashboard/browser runtime mechanics it
  directly covers.

Invoke an execution skill only when entering the phase it owns.

### Context skill

- `self-compact` owns every same-session compaction. Persist the current baton
  and proof references and confirm this charter reminder is live before using
  its verified continuation protocol. Never use a bare compact.

## Autonomy mandate

Continue through implementation, tests, installed proof, review, rollback, and
local commits without routine approval. Stop only for unavailable authority,
authentication requiring user action, destructive or irreversible action, or
an acceptance conflict that cannot be resolved safely.

## Plan hygiene

- Maintain the plan's Current baton as the single source of phase status.
- Implement report-only decisions and UI before enabling mutation.
- Keep preview data in a private manifested snapshot. Preview processes must
  use separate data and state roots, a separate port, disabled mutation
  endpoints, and no launchd registration.
- Never point preview code at live writable state or treat preview results as
  installed proof.
- Keep preview commits on their separate local branch until capacity natural
  cadence and rollback proof are terminal and retained.
- Preserve the capacity branch tip and tracked working tree after the preview
  worktree is created. Shared Git objects and worktree metadata are allowed;
  capacity branch changes are not.
- Keep evaluation, usage, recommendation, action, and receipt concepts
  separate in code, API, and UI.
- Preserve `estate-action.py` as the sole mutation authorization and dispatch
  owner; curator and settings transactions remain adapters.
- Keep individual plugin-skill disablement recommendation-only unless the
  installed CLI passes the native-control qualification.
- Run targeted checks first, then live proof, then paired implementation
  review.

## Installation and push policy

- Use existing installer, self-test, halt, enable, rollback, Git-backed
  retirement, and plugin-settings transaction boundaries.
- Keep the Mac mini as the sole active scheduled owner.
- Run the preview only as an ordinary foreground process. It must not install,
  load, unload, kickstart, or edit a launchd job.
- Transfer reviewed commits without remotes. Never push branches, commits, or
  tags.

## Completion

Stop the hourly reminder only after every checkbox under
`## Definition of Done: Capacity closure and aggressive portfolio governance`
is verified and committed locally.
