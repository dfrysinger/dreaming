# Dreaming repository split

## Objective

Move autonomous skill learning and maintenance into
`github.com/dfrysinger/dreaming`. Keep `github.com/dfrysinger/skills` focused
on independently useful skills.

## Non-goals

- Change the learning, memory deletion, pruning, or rollback policies.
- Duplicate shared skill-authoring logic in both repositories.
- Require the full `dfrysinger/skills` plugin when dreaming only needs a
  bounded dependency set.
- Rename existing state directories or discard daemon history.
- Broaden the macOS launchd implementation to another scheduler.

## Ownership boundary

### `dfrysinger/dreaming`

Owns:

- `skill-review`, `skill-curator`, `memory-curator`, `skill-create`, and
  `skill-manage`;
- daily and weekly pass prompts;
- orchestration, watchdog, self-test, and launchd assets;
- the dreaming installer and shared-skill dependency resolver;
- dreaming design, operations, and evidence-backed-learning documentation.

### `dfrysinger/skills`

Owns:

- independently useful skills, including `writing-great-skills`,
  `dual-review`, and `authenticated-browse`;
- the public skill catalog and plugin manifests.

These skills remain shared because each has a supported non-dreaming caller:

- `writing-great-skills`: human and agent skill-authoring rubric;
- `dual-review`: general implementation and design review;
- `authenticated-browse`: general authenticated browser access.

Using one of these from dreaming does not transfer its ownership to dreaming.

The tightly coupled authoring, provenance, evaluation, dependency-scanning,
transaction, and writer-lock helpers move with `skill-create` and
`skill-manage`. Headless passes load both repositories with
`copilot --plugin-dir`, so dependency availability does not depend on an
interactive session's installed plugins. The skills repository does not retain
copies of the five dreaming skills or their orchestration.

Three roots remain distinct:

- `DREAMING_REPO_ROOT`: executable orchestration and the five dreaming skills;
- `DREAMING_SHARED_SKILLS_ROOT`: immutable content-addressed shared-skill
  bundle under `~/.copilot/dreaming/deps/`;
- `SKILLS_REPO_ROOT`: optional managed public skill catalog.

Their canonical realpaths must differ. The shared bundle is never treated as a
writable public catalog. When no managed public checkout exists, autonomous
writes remain local and public-catalog mutations fail closed.

Skill references use explicit ownership namespaces:

- the five dreaming skill names resolve only to `DREAMING_REPO_ROOT`;
- the three shared dependency names resolve only to
  `DREAMING_SHARED_SKILLS_ROOT`;
- every other skill name resolves through the existing managed public/local
  roots, where a duplicate remains an error.

A shared dependency may also exist in the managed public catalog. That is the
same published skill identity, not a second live resolution candidate. It is
implicitly pinned while a dreaming job depends on it.

## Dependency bootstrap

The installer selects a shared-skills source in this order:

1. an explicit source override, which must be complete and receipt-compatible;
2. the canonical `~/code/skills` checkout, when complete and receipt-compatible;
3. an installed `dfrysinger-skills` plugin, when complete and
   receipt-compatible;
4. a temporary sparse checkout of the pinned `dfrysinger/skills` revision.

An incompatible automatic candidate is recorded and skipped. An explicit
override remains strict and fails immediately because the operator selected
that exact source. If every automatic candidate is incompatible or
unavailable, resolution fails without changing the selected bundle.

The source contributes exactly:

- `skills/writing-great-skills`;
- `skills/dual-review`;
- `skills/authenticated-browse`.

The installer never executes from the selected source. It verifies the source,
copies only those three directories into a new content-addressed bundle, writes
a minimal local plugin manifest, verifies the copied bytes, and atomically
selects that bundle. A writable checkout and an installed plugin are source
options, not runtime roots.

Every headless pass loads the immutable bundle and the dreaming repository with
`--plugin-dir`. Memory curation resolves `pw-session.sh` directly from
`DREAMING_SHARED_SKILLS_ROOT`; it does not search the installed-plugin cache.

Resolution validates an exact compatibility receipt containing:

- the shared-skill protocol version;
- the pinned remote skills revision used by sparse fallback;
- the relative path and SHA-256 of every file in the three shared skill
  directories.

Catalog manifests, catalog membership, and the skills plugin version are
outside the receipt because unrelated skill registration changes them. A
shared-skill content change requires a new dreaming receipt.

Resolution fails closed on a missing file, protocol mismatch, hash mismatch, or
bundle/source alias. The selected bundle and its verified identity are written
to `~/.copilot/dreaming/config.env` and rendered into every LaunchAgent.

The managed public catalog is resolved separately. A complete writable Git
checkout may be used for public operations. An installed plugin may be used as
a read-only catalog. With only the sparse dependency bundle available,
public-catalog coverage is explicitly limited and no public mutation is
allowed.

## Installation and migration

The top-level installer:

1. activates the existing halt switch;
2. backs up and boots out every installed dreaming or legacy
   skill-maintenance LaunchAgent;
3. resolves, verifies, and materializes shared skill dependencies;
4. synchronizes the `dfrysinger-dreaming` Copilot plugin unless explicitly
   disabled for a test fixture;
5. renders LaunchAgents that execute from the dreaming repository and read
   shared skills from the resolved skills root, with the public catalog
   configured independently;
6. removes legacy plist files, bootstraps and launchctl-enables the new jobs,
   and leaves the halt switch active;
7. runs deterministic checks and the launchd self-test;
8. enables autonomous runs only through an explicit `enable` command.

Existing state under `~/.copilot/skill-state` remains authoritative. Rollback
keeps the halt switch active, verifies every executable referenced by the
backed-up LaunchAgents, bootstraps and launchctl-enables the restored jobs while
they remain halted, and requires an explicit `enable` after self-test. It
refuses to reactivate a legacy plist whose code was retired.

## Skills repository retirement

The skills repository removes:

- the five dreaming skill directories;
- the four dreaming design and autopilot documents;
- dreaming plugin entries;
- all dreaming-specific README sections.

Its README ends with one short FYI section describing dreaming and linking to
the new repository. Generic shared-skill documentation may describe extension
hooks, but it does not duplicate dreaming behavior or operations.

Git history at the pre-removal commit remains the restore source for every
retired path. The new repository contains the live copy.

## Invariants

1. A fresh dreaming install can resolve all shared dependencies without a full
   skills checkout.
2. Sparse bootstrap fetches no unrelated skill directory.
3. Sparse dependencies are loadable by a real headless Copilot pass.
4. Installed LaunchAgents execute only files under the dreaming repository.
5. Dreaming fails before mutation when its shared root is missing, incomplete,
   or incompatible.
6. The immutable shared-skill bundle is never used as a writable public
   catalog.
7. Installed-job dependency scanning follows declared durable roots across the
   repository boundary and fails closed on missing or malformed owners.
8. Dreaming cannot run with unverified shared-skill revision, protocol, or
   file-hash skew.
9. Rollback cannot resume autonomous execution when a referenced executable is
   absent or before the restored jobs pass self-test.
10. Dreaming-owned helpers have one live copy in the dreaming repository.
11. The skills plugin exports none of the five dreaming skills.
12. The skills README mentions dreaming only in its final FYI section.
13. The existing writer lease, provenance schema, evaluation gate, archive
    refusal, memory deletion rails, and transaction rollback behavior remain
    intact.

## Deterministic checks

- dependency resolver fixtures cover explicit, canonical, installed-plugin,
  incompatible-candidate fallback, sparse-clone, incomplete-root,
  unavailable-source, protocol skew, helper-hash skew, root aliasing, and
  atomic bundle selection;
- a real headless canary with no installed `dfrysinger-skills` plugin loads the
  sparse shared bundle, reaches the required skills, and executes the
  authenticated-browse helper path;
- public-root fixtures prove managed, read-only catalog, and sparse-only modes
  cannot be confused;
- installed-job dependency fixtures follow a dreaming LaunchAgent into prompts
  and dreaming/shared/managed skill references, treat shared/catalog copies as
  one identity, preserve managed public/local ambiguity refusal, and reject
  missing external durable files;
- an unrelated public skill registration does not invalidate the immutable
  shared bundle receipt;
- plugin manifests in both repositories are internally consistent;
- every moved and retained skill validates;
- provenance, evaluation, promotion, dependency-scanning, curator transaction,
  and orchestration suites pass from their new owners;
- a repository-boundary check rejects duplicate dreaming helpers and residual
  dreaming plugin/docs paths in `dfrysinger/skills`;
- installer migration and rollback fixtures preserve exact plist bytes.
- rollback fixtures remove the old code before restore and prove no job is
  reactivated until the referenced code graph is restored and explicitly
  enabled.

## Live acceptance

1. Publish the reviewed dreaming repository.
2. Run the dreaming installer through its backup and bootout phase while the
   old skills-repository code still exists. Confirm no loaded LaunchAgent points
   at a path scheduled for retirement.
3. Publish and update the reviewed skills repository retirement while all
   autonomous jobs remain booted out and halted.
4. Complete the dreaming installation only after the shared compatibility
   receipt validates.
5. Confirm all installed LaunchAgents point to the dreaming repository and the
   resolved shared-skills root.
6. Run the self-test through launchd and require zero failures.
7. Kickstart dreaming with the halt switch present and observe a healthy
   recorded skip from the new repository.
8. Enable dreaming, confirm status is healthy, and verify both repositories and
   the local skills root retain their expected state.

## Rollback

- halt autonomous runs;
- keep the halt switch active;
- restore retired skills-repository paths from the recorded pre-removal commit
  when backed-up jobs reference them;
- run the dreaming installer's `rollback` command, which verifies the restored
  dependency graph and loads the backed-up LaunchAgents behind the halt switch;
- explicitly enable the restored owner only after its self-test passes;
- keep state and ledgers intact.

## Definition of Done

- [ ] The new public dreaming repository contains every autonomous skill,
      script, installer, launchd asset, and design document.
- [ ] The five tightly coupled dreaming skills and their helpers move together
      without duplicate live copies.
- [ ] The dependency resolver proves installed-plugin and exact sparse fallback
      behavior.
- [ ] The skills repository contains no dreaming skill, orchestration document,
      plugin entry, or README content beyond the final FYI link.
- [ ] Deterministic suites and bounded dual review have no material findings.
- [ ] Both repositories are published and clean.
- [ ] The installed LaunchAgents run from the dreaming repository, and the
      launchd self-test reports zero failures.
