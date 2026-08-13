# Mac mini Dreaming ownership and autonomous retirement

## Objective

Run Dreaming's scheduled processing and learned-skill authority on the Mac mini
while using the MacBook as the Copilot transcript source and learned-skill
installation destination.

## Lane

Critical.

This changes production scheduling, machine-local state ownership, credentials,
installed skill publication, and the authority to remove skills from normal
discovery. A failed migration can create two competing schedulers or lose
authority state. A bad retirement decision can remove useful instructions.

## Non-goals

- Do not run Dreaming concurrently on both Macs.
- Do not automatically modify, consolidate, or archive hand-made skills.
- Do not mutate skills supplied by installed plugins.
- Do not weaken explicit or scheduled-dependency pins.
- Do not delete skill history, evidence, retirement records, tombstones, or
  restore data.
- Do not bypass candidate evaluation when an umbrella skill is changed.
- Do not turn usage count alone into retirement authority.
- Do not implement the full portfolio benchmark or existing-skill lifecycle
  migration in this change.
- Do not remotely reboot the Mac mini.
- Do not install Dreaming-learned skills into the Mac mini's Copilot
  installation.
- Do not run scheduling, review execution, skill decisions, or publication
  decisions on the MacBook.
- Do not build general multi-host publication fan-out. The supported placement
  is one Mac mini server and one MacBook Copilot client.
- Do not synchronize hand-made personal skills or plugin-provided skills
  between the Macs.

## User-visible outcome

The Mac mini becomes the only machine running scheduled Dreaming work. A fresh
curator report may automatically proceed to a transaction when every changing
artifact is agent-created and unpinned. Retired skills disappear from normal
discovery but remain recoverable through Git history, retirement records,
tombstones, and the existing restore command.

Hand-made skills remain recommendations only. A proposed merge that would edit
a hand-made destination remains a manual decision.

The scheduler and review executors run on the Mac mini, but a session source
may remain on another Mac. The production Copilot source is read from the
MacBook over non-interactive SSH, so its existing session library remains
reviewable without copying hundreds of gigabytes or enabling a second
scheduler. Remote source commands preserve exact argument boundaries and fail
closed when SSH is unavailable. Review execution remains local to the Mac mini.
The mini transfers each exact learned-skill bundle to the MacBook and invokes
the existing Copilot publisher there over SSH. The MacBook installs the bundle
but does not decide its contents or run Dreaming.

## Reuse contract

This change reuses the existing owners:

- `scripts/install.sh` installs Dreaming, its verified dependency bundle,
  managed instructions, and LaunchAgents.
- `scripts/migrate-copilot-state.py` remains the owner for same-host adoption of
  its legacy allowlisted state; it is not a host-to-host migration tool.
- `daemon-selftest.sh` certifies the exact installed machine.
- `scheduled-skill-deps.py` protects skills required by durable jobs.
- `curator-run.py` owns the shared writer lease, exact transaction plan,
  scoped commits, finish, and rollback.
- `archive-skill.sh` owns recoverable removal, retirement records, Git commits,
  tombstones, and restore identity.
- The existing dry-run report remains the decision record.
- Existing evaluation and dual-review gates remain required when consolidation
  changes an umbrella skill.

Five bounded extensions are required:

1. A host-transfer helper must inventory, copy, and compare the exact
   repositories and machine-local Dreaming state because the existing
   `migrate-copilot-state.py` supports only a fixed same-host legacy subset.
2. `curator-run.py` must mint and enforce a transaction-bound authorization
   receipt because the removed human gate currently supplies the only
   backstop against a malformed report targeting hand-made content.
3. `curator-run.py` must own a lease-held publication phase so a public push is
   verified before the transaction becomes complete and releases its writer
   lease.
4. An SSH publication transport must carry the existing skill-publisher
   protocol to the MacBook. It may transfer an exact content-addressed bundle,
   but the existing vendor adapter remains the owner of Copilot installation,
   inventory, verification, replacement, and removal.
5. The existing Copilot publisher adapter must add transaction-aware snapshot
   and reconciliation commands. They atomically restore or adopt an exact
   descriptor so interrupted native inventory changes and the ownership journal
   cannot diverge.

No second publication, archive, or rollback owner is introduced.

## MacBook client publication

The Mac mini is the sole learned-skill writer. Its
`~/.local/share/dreaming/skills` repository contains the complete agent-created
catalog and Git history. The six learned skills that predate the host migration
are seeded from the MacBook into this repository before remote publication is
enabled.

The configured Copilot publisher on the mini is an SSH transport rather than a
local Copilot publisher:

1. Dreaming materializes and verifies the content-addressed bundle on the mini.
2. Before every operation, the transport requires the receiver's configured
   random identity and the exact receiver and vendor-adapter script digests.
   A missing, wrong-host, or code-skewed receiver refuses before staging or
   Copilot invocation.
3. For `install`, the transport validates the local manifest and file hashes,
   sends a bounded archive over authenticated batch-mode SSH, and stages it
   under a content-addressed MacBook bundle directory.
4. A receiver on the MacBook rejects absolute paths, parent traversal,
   symbolic links, duplicate entries, undeclared files, missing files, hash
   mismatches, and bundle identity mismatches before replacing the durable
   staged bundle.
5. The receiver writes a durable operation record containing the new bundle,
   the previously verified Copilot descriptor, and phase before invoking the
   existing publisher. It adopts the existing MacBook journal at
   `~/.local/state/dreaming/publisher-ownership.json`, so the first remote
   publication supersedes the pre-cutover six-skill bundle rather than
   creating a duplicate registration.
6. The receiver invokes the existing Copilot skill-publisher adapter, performs
   local inventory verification in the same operation, and records either
   `committed` or `rolled_back`. A local verification failure invokes the
   adapter's transaction-aware reconciliation command, which removes the
   interrupted descriptor, restores the exact prior inventory and journal
   snapshot without creating a self-superseding descriptor, and verifies both
   before returning failure.
7. If SSH loses the response after Copilot invocation, the operation remains
   `installing`. The next `install`, `verify`, or recovery request reconciles
   that record through the adapter's reconciliation command. A fully installed
   new bundle is adopted into the journal and becomes `committed`; every other
   state removes the interrupted descriptor, restores the exact prior inventory
   and journal, and becomes `rolled_back`. Dreaming does not report either
   bundle as active until it receives the reconciled remote receipt.
8. `verify`, `inventory`, and `remove` execute against the MacBook adapter over
   the same SSH boundary. Successful install or verify mirrors a
   content-addressed, receiver-bound publication summary into the mini's
   `remote-publication-summary.json` for the dashboard. This mini record is a
   verified view, not installation authority, and is excluded from host
   authority transfer because it can be regenerated from the MacBook receipt.

The transport immediately retries reconciliation after a lost or ambiguous
response. While SSH remains unavailable, the mini records
`publication_recovery_required`, blocks later publication and learned-skill
mutation, and exposes the state through the dashboard, watchdog, and installed
self-test. The receiver also reconciles a non-terminal operation before
accepting any command. Recovery therefore runs on the first restored
connection rather than waiting for a later scheduled publication.

The MacBook's historical learned-skill repository becomes an inert migration
source after its history is seeded on the mini. Runtime availability on the
MacBook comes from the verified Copilot publication bundle, not from a second
Dreaming writer or Git synchronization loop.

The cutover removes the mini's existing local Dreaming Copilot registration
through the old adapter before replacing its configuration with the SSH
publisher. The MacBook's existing Copilot descriptor and bundle are retained
as the receiver's initial previously verified publication. A deployment marker
requires remote Copilot publication on the mini; missing or explicitly empty
publisher SSH configuration is an installation error rather than a fallback to
local publication.

The remote-only marker, journal adoption, registration replacement, and
reconciliation apply only to the Copilot publisher. Existing Claude and Codex
descriptors in the adopted MacBook journal remain untouched. A staged bundle
directory is deleted only when no Copilot, Claude, or Codex descriptor in that
journal references it.

## Authority policy

### Autonomous operations

A fresh curator report may execute without per-run approval when all affected
sources and changed destinations are agent-created, every dependency inventory
is complete, no explicit or implicit pin applies, and the shared halt switch is
absent. The curator-specific pause flag must also be false.

These facts are not trusted from report prose or model output. Before
transaction creation, `curator-run.py` independently reads the report, computes
its digest and age, resolves every declared source and destination against the
managed roots, reads marker-backed provenance, reruns dependency inventory,
checks explicit and implicit pins, reads pause and halt state, and writes an
immutable authorization receipt. It repeats report digest, report age, halt,
pause, provenance, and pin checks immediately before each intent.

Eligible operations are:

- recoverable pruning of an agent-created skill;
- absorption of an agent-created skill into an agent-created umbrella after
  the changed umbrella passes its evaluation and review gates;
- creation of a new agent-created umbrella followed by evaluated absorption of
  agent-created sources.

Public-root operations may also proceed autonomously. Their GitHub publication
is part of the transaction rather than an unrecorded shell step:

- the transaction records prior and new public `HEAD`;
- push is allowed only after every local operation finishes but before the
  transaction is marked complete or releases its writer lease;
- the pushed commit must be the exact recorded new `HEAD` and a fast-forward
  from the recorded prior `HEAD`;
- remote publication identity is recorded;
- a failed or rejected push leaves the transaction in a non-complete
  `publish_failed` state with the writer lease retained for rollback or
  supervised recovery;
- rollback after publication creates and pushes an explicit revert restoring
  the prior tree rather than rewriting public history.

### Operations that remain manual

The curator may report but may not automatically execute:

- pruning or archiving a hand-made skill;
- editing a hand-made destination;
- changing plugin-provided skills;
- touching any explicit or implicit pin;
- acting from incomplete provenance, dependency, usage, evaluation, or
  transaction evidence.

### Recoverability

Automatic retirement never means deletion without history. Every retired skill
must retain:

- the last Git commit containing the complete package;
- its retirement reason and source report;
- its supporting evidence references;
- a tombstone preventing accidental autonomous recreation;
- the existing explicit restore path.

The retirement record and curator transaction bind the immutable curator
report digest and the source skill's marker-backed evidence references. A bare
report pathname is not sufficient authority.

## Machine migration flow

1. Keep the source halt switch present.
2. Use the host-transfer helper to capture a content manifest for the exact
   source commit, verified dependency bundle, managed skill repositories,
   publisher ownership state, LaunchAgent configuration, and the transferable
   machine-local authority set:
   - `skill-review/retired/`;
   - `skill-review/tombstones/`;
   - `curator.json` and retained curator reports;
   - publisher ownership and durable publication records;
   - review ledger, queue, unsettled, discovery, attempts, and transactions.
   Installer-owned machine-local records are explicitly excluded from transfer
   and equality comparison: `activation-generation`, active self-test label,
   self-test-passed generation, active migration backup pointers, and active
   lifecycle or daemon locks. `install.sh` creates the destination's versions.
3. Install required CLIs on the Mac mini without logging credentials.
4. Copy the exact manifest-listed repositories and state over authenticated
   SSH, excluding credentials and transient locks.
5. Install Dreaming on the Mac mini with its own machine-local paths.
6. Establish Copilot and GitHub authentication on the Mac mini.
7. Compare the destination manifest with the captured source manifest. Keep
   destination scheduling disabled and run the complete installed self-test.
8. Require exactly `== result: 0 failure(s) ==`.
9. Over authenticated SSH, unload the source Dreaming LaunchAgents, confirm
   the source halt switch is present, and save a two-host cutover receipt.
10. Activate only the destination.
11. Prove one scheduled pass has one owner, then inspect both hosts and append
    their halt, launchd, process, run-ID, and timestamp evidence to the cutover
    receipt.

The source state remains an inert rollback copy until the destination has
completed its first healthy scheduled pass.

## Failure model

- **Two active owners:** both machines discover or mutate the same conceptual
  library independently. The source halt plus destination-disabled install
  prevents this before cutover.
- **Incomplete state copy:** the destination invents fresh authority or loses
  publisher ownership. Exact source/destination manifest comparison stops
  activation.
- **Credential failure:** deterministic tests pass but real headless Copilot
  work cannot run. The installed self-test must include the real auth probe.
- **Unsafe curator report:** incomplete dependency enumeration or malformed
  provenance proposes a protected skill. The run refuses before transaction
  creation.
- **Mid-run failure:** some umbrella edits or archives complete and others do
  not. `curator-run.py rollback` restores both managed roots.
- **Bad autonomous judgment:** a useful agent-created skill is archived. Git,
  retirement records, evidence, and tombstones preserve an explicit restore
  path.
- **Hand-made collateral:** an automatic consolidation tries to edit a
  hand-made destination. Machine-enforced authorization validation refuses the
  operation before transaction creation and again before intent.
- **Public publication failure:** the local transaction finishes but GitHub
  rejects or serves the wrong public commit. The transaction remains
  incomplete and the active installation keeps the prior public revision.
- **Remote bundle transfer failure:** SSH disconnects or the archive is
  incomplete. The receiver leaves the prior content-addressed bundle and
  Copilot registration in place; an incomplete staging directory is never
  installed.
- **Remote verification failure:** the MacBook installs a different bundle or
  its Copilot inventory does not contain every declared skill. The receiver
  restores the prior verified descriptor, records `rolled_back`, and reports
  failure.
- **Ambiguous SSH outcome:** the receiver begins or completes Copilot
  replacement but the SSH response is lost. The durable `installing` record is
  reconciled by immediate retry and before the receiver accepts another
  operation. Either the exact new inventory and journal are verified and
  committed or the interrupted descriptor is removed and the exact prior
  inventory and journal are restored. An unreachable receiver leaves a visible
  recovery-required state and blocks later mutation.
- **Wrong-host publication:** configuration points the publisher at the mini or
  another host. Receiver identity and exact script-digest checks refuse before
  bundle staging or Copilot invocation.
- **Receiver code skew:** the MacBook receiver or vendor adapter differs from
  the exact reviewed scripts configured on the mini. The protocol handshake
  refuses before mutation.
- **Publication-state divergence:** the MacBook journal is authoritative for
  Copilot installation while the mini retains a stale
  `remote-publication-summary.json`. Every publication operation refreshes the
  summary from a receiver-bound committed receipt; the dashboard marks absent
  or mismatched summaries unverified.

## Hard invariants

1. At most one Mac may have Dreaming scheduling enabled.
2. The source halt switch remains present through destination certification.
3. Destination activation requires the exact zero-failure installed self-test.
4. A dry-run report is required, content-addressed, and may be no more than
   seven days old.
5. Dependency enumeration must be complete before report or mutation.
6. Explicit and implicit pins are never autonomous sources or destinations.
7. Every automatically removed source must be agent-created with valid
   provenance.
8. A hand-made skill is never automatically changed or archived.
9. Consolidation cannot archive a source until the exact changed destination
   passes evaluation and review.
10. Every mutation and public push runs inside the existing transaction and
    writer lease; completion and lease release occur only after remote identity
    verification.
11. Every archive is recoverable, writes a tombstone, and binds its report
    digest and evidence references.
12. The shared halt switch and curator pause flag stop autonomous retirement at
    both planning and pre-mutation boundaries.
13. Destination activation requires exact equality with the captured source
    migration manifest, excluding explicitly declared machine-local
    credentials and transient state.
14. Single-owner authority is proved from both real hosts over authenticated
    SSH, not inferred from fixtures.
15. The learned-skill Git repository and all review and mutation authority live
    on the Mac mini.
16. The active Copilot learned-skill bundle is installed on the MacBook and is
    absent from the Mac mini.
17. Remote publication succeeds only after the MacBook verifies the exact
    bundle identity, every declared file hash, receiver identity, script
    digests, Copilot inventory, and a committed operation receipt.
18. A rejected pre-invocation transfer or a failed local verification leaves
    the previously verified MacBook publication active.
19. An ambiguous post-invocation outcome is reconciled before another
    receiver operation and leaves either the exact prior or exact new inventory
    and journal active, never an unowned or unverified registration.
20. The MacBook journal owns Copilot installation state. The mini mirror is
    accepted only when bound to the current receiver identity and committed
    receipt.
21. The mini refuses local learned-skill publication while its remote-only
    deployment marker is present.
22. A non-terminal remote publication blocks learned-skill mutation and remains
    unhealthy in the dashboard, watchdog, and self-test until reconciled.

## Acceptance criteria

- **AC-01:** The Mac mini matches the captured source manifest for the exact
  Dreaming revision, verified dependency bundle, managed roots, publisher
  ownership, retirement records, tombstones, curator state and reports, and
  supported review state, with required CLIs installed.
- **AC-02:** The Mac mini installed self-test reports zero failures, including
  a real background Copilot authentication probe.
- **AC-03:** Source scheduling remains halted and only the Mac mini owns the
  active scheduled installation after cutover.
- **AC-04:** A fresh curator dry run covers both managed roots, uses complete
  dependency inventory, records provenance, and changes neither Git tree.
- **AC-05:** An eligible agent-created pruning proceeds from a fresh report
  without requesting confirmation and produces a recoverable archive,
  retirement record, tombstone, report/evidence binding, and clean completed
  transaction.
- **AC-06:** An agent-created consolidation cannot archive its source until the
  changed agent-created destination passes evaluation and review.
- **AC-07:** Hand-made, plugin-provided, pinned, malformed, or uncertain inputs
  remain report-only and cause no automatic mutation.
- **AC-08:** The halt switch, stale report, transaction failure, or incomplete
  dependency scan prevents mutation. Curator pause has the same effect.
- **AC-09:** Rollback restores both managed roots after a forced partial
  transaction failure.
- **AC-10:** An automatically archived skill can be restored through the
  existing restore path.
- **AC-11:** An autonomous public-root operation publishes only the exact
  finished transaction commit, records remote identity, and can be reversed by
  a normal pushed revert.
- **AC-12:** Before rollback re-enables the source Mac, post-cutover local-root
  commits, retirement records, tombstones, and reports are transferred back and
  verified. The MacBook publisher journal remains authoritative and the mini's
  regenerable remote-publication summary is not transferred as authority.
- **AC-13:** The Mac mini learned-skill repository contains the six preexisting
  MacBook skills and their seven-commit history before publication cutover.
- **AC-14:** A mini-side publish installs and verifies the exact generated
  bundle in Copilot on the MacBook, supersedes the pre-cutover Dreaming bundle,
  leaves no other Dreaming-owned bundle registered there, and installs no
  learned bundle on the mini.
- **AC-15:** Corrupt, truncated, traversing, symlinked, duplicate, oversized,
  or identity-mismatched bundle transfers are rejected before the MacBook
  publisher is invoked, leaving the prior publication active.
- **AC-16:** Ordinary installer regeneration preserves the remote Copilot
  publisher configuration. Missing, explicitly empty, wrong-identity, or
  code-skewed publisher configuration refuses installation or publication
  while the mini's remote-only marker is present.
- **AC-17:** The mini dashboard lists the seeded learned skills from the
  authoritative mini repository and reports the MacBook publication target
  only from a receiver-bound committed receipt.
- **AC-18:** Losing SSH after Copilot invocation is recoverable. Reconciliation
  proves the exact new inventory and journal active or restores the exact prior
  inventory and journal before the receiver accepts another operation.
  Immediate retry begins on transport loss; an unreachable receiver blocks
  mutation and appears as recovery-required in the dashboard, watchdog, and
  self-test.
- **AC-19:** Reversing the placement preserves the MacBook journal and active
  bundle, removes the mini's remote-only publisher configuration and mirrored
  summary, restores a local publisher configuration on the MacBook, and leaves
  exactly one verified Copilot registration.

## Deterministic check contract

- **CHK-01: Single-owner migration.** The cutover command must query both real
  hosts over authenticated SSH. It unloads and verifies source labels and halt
  state before destination enablement, then records both hosts' launchd,
  process, run-ID, and timestamp state after one destination pass. Missing or
  contradictory evidence refuses cutover.
- **CHK-02: Installed destination certification.** Run the real Mac mini
  self-test. The only passing signal is the exact zero-failure result.
- **CHK-03: Report and migration integrity.** Run a dry review with complete
  dependencies and compare both managed Git trees before and after. Separately
  compare the captured source migration manifest with the destination before
  activation. Any undeclared difference fails.
- **CHK-04: Autonomous pruning.** Feed a fresh report containing one valid,
  unpinned agent-created pruning. Pass requires no confirmation prompt and a
  complete recoverable archive transaction whose authorization and retirement
  records bind the report digest and resolvable evidence references.
- **CHK-05: Protected classes.** Repeat with hand-made, plugin, explicit-pin,
  implicit-pin, malformed-provenance, and incomplete-dependency inputs. Each
  refusal must be emitted by `curator-run.py` or `archive-skill.sh` with a
  nonzero exit before `begin` or `intent`; prompt compliance is not evidence.
- **CHK-06: Consolidation gate.** Attempt to archive an absorbed source with a
  missing, stale, or regressing destination receipt. Each attempt must refuse;
  an exact passing receipt and review may proceed.
- **CHK-07: Halt, pause, and freshness.** Add the halt switch, set curator pause,
  alter the report after authorization, or age it beyond seven days. Mutation
  must refuse at begin and immediately before intent through report digest and
  age revalidation.
- **CHK-08: Transaction rollback.** Force failure after the first declared
  operation. Rollback must restore both roots and record the run as rolled back.
- **CHK-09: Restore.** Archive a fixture skill, restore it, and prove package
  identity, report digest, and evidence links remain available.
- **CHK-10: Public publication.** Finish an authorized public-root transaction,
  enter its lease-held publication phase, push to a fixture remote, and verify
  exact prior/new/remote identities before completion and lease release. A
  rejected push must leave the run non-complete with its lease retained. Force
  rollback after a successful push and require a normal revert commit that
  restores the prior tree.
- **CHK-11: Reverse migration.** Add destination-only local retirement state,
  then exercise rollback preparation. Source re-enable must refuse until local
  Git history and authority state are copied back and exactly verified.
- **CHK-12: Remote publisher protocol.** Exercise contract, doctor, inventory,
  install, verify, and remove through a fake SSH boundary. Exact empty and
  quoted arguments must survive transport, receiver identity and script
  digests must match, and remote failures must preserve their nonzero result.
- **CHK-13: Bundle receiver safety.** Feed valid and invalid archives to an
  isolated receiver. A valid archive must produce the declared bundle and
  invoke the fixture publisher once. Traversal, symbolic links, duplicates,
  undeclared files, missing files, hash mismatch, identity mismatch, truncation,
  and size overflow must fail before invocation and leave the prior bundle
  unchanged.
- **CHK-14: Publisher configuration persistence.** Generate, reinstall, and
  regenerate adapters with a remote Copilot publisher. The publisher must
  remain remote while the source and executors retain their configured
  placement. Explicit host replacement must replace it only with matching
  receiver identity and scripts. Missing or explicitly empty remote
  configuration must fail while the remote-only marker is present.
- **CHK-15: Live Mac placement.** Seed the exact six-skill Git history on the
  mini, publish from the mini, and inspect both real Copilot inventories. Pass
  requires every skill to resolve under the newly staged content-addressed
  MacBook bundle, no other Dreaming-owned bundle on the MacBook, no Dreaming
  bundle on the mini, a dashboard count of six with a verified MacBook target,
  and the MacBook scheduler halt and unload state unchanged.
- **CHK-16: Ambiguous publication recovery.** Kill the SSH transport after the
  fixture publisher changes registration but before the result reaches the
  mini. Immediate retry must begin. Reconciliation must commit an exactly
  matching new inventory and journal or remove the interrupted descriptor and
  restore the exact prior inventory and journal. A subsequent publish must
  leave exactly one Copilot Dreaming bundle. While the receiver is unreachable,
  mutation must remain blocked and every health surface must report recovery
  required.
- **CHK-17: Existing-publication adoption.** Start with the MacBook's
  pre-cutover journal and six-skill registration, then publish remotely. Pass
  requires the old descriptor to become superseded and then removed, exactly
  one Dreaming-owned bundle to remain, and every skill to resolve beneath it.
- **CHK-18: Remote publication rollback.** Reverse a completed remote
  publication. Preserve and verify the MacBook journal and active bundle,
  remove the mini mirror and remote-only publisher configuration, restore the
  MacBook local publisher configuration, and prove exactly one scheduler and
  one Copilot publication owner remain.
- **CHK-19: Shared bundle retention.** Adopt a journal whose Copilot, Claude,
  and Codex descriptors share a bundle directory. Copilot replacement and
  cleanup must leave the Claude and Codex descriptors and every bundle path
  they reference intact.

## Rollback

1. Create the shared halt switch on the Mac mini.
2. Disable the Mac mini Dreaming LaunchAgents and wait for active leases to
   end.
3. Run transaction rollback for any incomplete curator operation.
4. If a finished public transaction was pushed, create and push the recorded
   revert before restoring scheduling.
5. Restore the prior curator code and policy.
6. Keep automatically archived skills archived unless a recorded restore is
   required; restore only through `restore-skill.sh`.
7. Transfer destination-only local skill commits, retirement records,
   tombstones, and reports back to the source and verify their manifest. Do not
   transfer the mini's regenerable remote-publication summary or overwrite the
   authoritative MacBook publisher journal.
8. Reconcile any non-terminal remote publication receipt. Preserve and verify
   the active MacBook bundle and journal, then remove the mini's mirrored
   summary and remote-only publisher configuration.
9. Restore the MacBook's local publisher configuration using its existing
   journal. Remove superseded staged bundles only after exact inventory
   verification.
10. Re-enable the original Mac only if the synchronized state and exact
    installed self-test pass.
11. Never enable both schedulers during rollback.

Fail-closed evidence is the absence of managed-root changes when any authority,
dependency, provenance, freshness, pin, halt, evaluation, or transaction check
fails.

## Definition of Done: MacBook client publication

- [x] The Mac mini learned-skill repository contains the six preexisting
      learned skills and their seven-commit Git history.
- [x] Copilot transcript reads continue from the MacBook while review execution
      and all learned-skill decisions remain on the mini.
- [x] The configured Copilot publisher transfers and verifies exact bundles on
      the MacBook over authenticated batch-mode SSH.
- [x] The MacBook Copilot inventory contains the active learned bundle and the
      Mac mini Copilot inventory does not; no superseded Dreaming registration
      remains on either host.
- [x] Failed or malformed transfer cases leave the previous MacBook
      publication active.
- [x] Ambiguous post-invocation outcomes reconcile to the exact prior or exact
      new verified inventory and journal before another receiver operation;
      an unreachable receiver visibly blocks mutation.
- [x] Installer and adapter tests cover remote publication, persistence, host
      replacement, receiver identity and code skew, existing-publication
      adoption, and remote-only refusal.
- [x] The installed Mac mini self-test reports exactly zero failures.
- [x] The mini dashboard lists the six seeded learned skills and shows a
      verified MacBook publication target from the mirrored committed receipt.
- [x] The dashboard, watchdog, and self-test report recovery-required while a
      remote publication operation is non-terminal.
- [x] The MacBook remains halted, unloaded, and free of Dreaming workers.
- [x] The completed remote publication can be reversed while preserving one
      verified MacBook registration and one scheduler.
- [x] Copilot publication replacement leaves existing MacBook Claude and Codex
      descriptors and shared bundle paths intact.
- [x] Required implementation review has no verified in-scope material finding.

## Definition of Done: Mac mini ownership and autonomous retirement

Final evidence: the installed Mac mini candidate passed the complete self-test
with zero failures at activation generation
`20260813T004423Z-install-69160`. It then inspected a queued Copilot session
through the MacBook SSH source and completed run
`20260813T012330Z-5866` with exit code zero. The run accepted `25` Copilot
reviews without increasing the deleted-session count. The MacBook remained
halted, unloaded, and free of Dreaming workers.

- [x] A fresh read-only curator report inventories the current managed skill
      library and records proposed consolidations, prunings, and manual-only
      recommendations.
- [x] Machine-enforced authorization independently verifies report identity,
      freshness, provenance, root ownership, pins, halt, pause, and dependency
      completeness before transaction creation and before each intent.
- [x] Curator policy and implementation allow fresh eligible agent-created-only
      operations to proceed without per-run human confirmation.
- [x] Hand-made, plugin-provided, pinned, malformed, uncertain, or
      dependency-incomplete cases remain non-mutating.
- [x] Automatic archives preserve Git history, retirement records, evidence,
      tombstones, and restore authority.
- [x] Automatic consolidations retain destination evaluation and review gates.
- [x] Halt, freshness, single-owner, transaction, rollback, and restore checks
      pass.
- [x] Public-root autonomous publication and its pushed-revert rollback are
      transaction-bound and validated.
- [x] The exact Dreaming revision, verified dependencies, managed skill roots,
      and supported state are installed on the Mac mini.
- [x] The Mac mini reads the MacBook Copilot session library through the
      SSH-backed source while review execution remains local.
- [x] The Mac mini installed self-test reports exactly zero failures.
- [x] One real scheduled pass succeeds on the Mac mini while the source Mac
      remains halted and inactive.
- [x] Required implementation review has no verified in-scope material finding.
