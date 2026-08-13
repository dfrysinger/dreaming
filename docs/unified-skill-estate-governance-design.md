# Unified skill estate governance

## Objective

Make the Mac mini continuously inventory, evaluate, consolidate, retire, and
report every skill effectively enabled for Copilot on the MacBook, while
protecting user-authored work and keeping every automatic mutation reversible.

## Lane

**Critical.** This change grants automatic authority over durable skill files
and Copilot plugin enablement, crosses the Mac mini/MacBook trust boundary, and
must fail closed against provenance mistakes, incomplete inventory, concurrent
settings edits, and unrecoverable retirement.

## Non-goals

- Do not move Dreaming scheduling, transcript processing, catalog authority, or
  decision making back to the MacBook.
- Do not make the Mac mini's own Copilot, Claude, or Codex skill estate a
  governance target in this release.
- Do not govern Claude Code or Codex plugins, skills, MCP servers, agents, or
  settings.
- Do not edit files inside an installed plugin package. Package-owned content
  changes only through the package owner; governance may reversibly disable the
  complete plugin.
- Do not uninstall plugins automatically. Copilot CLI exposes install and
  uninstall commands but no public disable command; this design uses the
  existing `enabledPlugins` settings boundary.
- Do not treat every physical skill directory as a distinct active capability.
  Disabled packages, stale caches, duplicate copies, and publisher generations
  are inventory evidence, not automatically enabled skills.
- Do not infer that an unmarked personal skill is user-authored or
  machine-authored.
- Do not weaken the recurrence, evaluation, portfolio, publication, archive,
  dependency, halt-switch, or rollback rules in
  `conservative-skill-lifecycle-design.md`.
- Do not create a second curator, archive format, lifecycle state machine,
  publisher, scheduler, or dashboard authority.

## User outcome

The dashboard answers, without hidden exclusions, for the user-level Copilot
estate and every explicitly registered project context:

1. How many skill instances are physically present on the MacBook?
2. Which canonical capabilities are effectively enabled?
3. Who owns each enabled capability and what may Dreaming do to it?
4. Which machine-created skills were kept, consolidated, withdrawn, or
   archived, and how can they be restored?
5. Which plugins are enabled, what capabilities each contributes, and why an
   enabled plugin was kept or automatically disabled?
6. Which skills remain recommendation-only because user ownership or
   provenance is uncertain?

Project-local skills are contextual rather than globally enabled. The census
must label its registered project contexts and must not claim completeness for
repositories outside that bounded set. An unregistered project context cannot
provide evidence for an automatic removal.

## Existing gap

The current dashboard enumerates only the six `.agent-created` skills in
Dreaming's learned catalog. The MacBook also has personal skills and
plugin-provided skills. Many personal skills were created by older
`skill-review` runs, but they are not current catalog entries. Plugin packages
may contain several skills and may also provide agents, hooks, MCP servers, or
LSP servers.

The conservative lifecycle design anticipated an existing-library import but
kept legacy skills recommendation-only and prohibited bulk archive. That was
appropriate before current provenance envelopes, Git-backed retirement,
curator transactions, remote publication, and recovery controls existed. This
follow-on design closes the inventory and authority gap without changing the
admission rules for newly learned skills.

## Reuse contract

| Concern | Existing owner | Extension required |
| --- | --- | --- |
| Scheduling and decisions | Mac mini `dreaming-core.py` | Add a bounded estate census, evaluation queue, and approved action dispatch |
| MacBook trust boundary | SSH publisher/receiver | Add receiver-bound read-only census and narrowly scoped estate mutation operations |
| Current learned skills | Candidate lifecycle, approved inventory, publisher | Import them into the estate view; do not add another lifecycle owner |
| Personal consolidation and retirement | `skill-curator`, `curator-run.py`, `archive-skill.sh`, `restore-skill.sh` | Extend authorization to remote MacBook personal-root transactions and broader provenance classes |
| Evaluation and portfolio value | `skill-evaluation.py` and portfolio benchmark | Evaluate estate capabilities and proposed removals against the complete enabled catalog |
| Pins and dependencies | `scheduled-skill-deps.py` | Include remote MacBook dependencies and fail closed on incomplete enumeration |
| Plugin runtime state | `~/.copilot/settings.json` `enabledPlugins` | Add an atomic, conflict-detecting disable/restore transaction |
| Dashboard | `dreaming-dashboard.py` | Read the canonical estate snapshot and action ledger |
| Halt and rollback | Shared halt switch, run manifests, publisher recovery | Block all estate mutations and restore remote transactions |

New code is justified only for three missing boundaries:

1. a canonical MacBook estate census that existing catalog readers cannot
   produce;
2. a receiver-side plugin settings transaction, because no public Copilot
   plugin-disable command exists;
3. a remote curator operation wrapper, because the personal Git root being
   governed lives on the MacBook while mutation authority lives on the mini.

## Authority and identity model

### Physical instance

A physical instance is one discovered skill directory with:

- host ID;
- discovery surface;
- absolute root identity;
- package or publisher identity;
- skill name;
- complete regular-file inventory hash;
- effective-enabled state and evidence;
- precedence position, when the runtime exposes it.

Physical instances are retained for diagnosis. They are never counted as
separate enabled capabilities merely because the same bytes appear in several
publisher generations or caches.

### Canonical capability

A canonical capability groups instances only when identity is deterministic:

- an exact Dreaming lifecycle ID;
- an exact package ID plus package-relative skill path;
- an exact personal-root skill name and inventory lineage; or
- an explicit absorption/supersession link.

Same names or similar descriptions are matching evidence, not identity.
Semantic deduplication is a curator decision backed by evaluation, never a
filesystem heuristic.

### Plugin identity

A plugin is identified by its exact Copilot plugin ID, source identity, and
installed version. Its capability inventory includes every supplied skill,
agent, hook, MCP server, and LSP server. Unknown or unreadable capability
classes make automatic plugin disablement ineligible.

### Provenance and authority classes

Every enabled skill instance receives exactly one class:

| Class | Evidence | Automatic authority |
| --- | --- | --- |
| `dreaming_managed` | Current valid lifecycle/catalog identity | Existing lifecycle and curator rules |
| `legacy_machine` | Valid current envelope, or a verified legacy creation proof defined below | Consolidate, withdraw, archive, and restore |
| `user_protected` | Explicit user pin/adoption or verified human authorship | Recommendation only |
| `plugin_managed` | Exact enabled plugin/package identity | Keep or disable the complete plugin; never edit its files |
| `cli_builtin` | Exact Copilot version plus builtin package-relative identity | Keep and evaluate as routing context; never mutate |
| `unknown_provenance` | Missing, conflicting, malformed, or insufficient ownership evidence | Recommendation only |

Marker absence is not authorship evidence. Conflicting markers, malformed
envelopes, path ambiguity, or incomplete source history produce
`unknown_provenance`.

A legacy marker alone never grants authority. A legacy creation proof is valid
only when the importer can bind all of these facts into the census receipt:

- the non-symlink `.agent-created` marker was introduced in the same Git commit
  as the initial skill package;
- that creation commit is reachable from the expected personal-root history
  and the package inventory at creation is reproducible;
- the commit, retained session receipt, or historical Dreaming record carries
  a configured machine-author identity that predates this migration; and
- no later adoption, pin, provenance conflict, history rewrite, or user-authored
  predecessor claim exists.

The accepted legacy proof formats and machine-author identities are
versioned policy inputs. Their hashes are sealed into each action. Marker-only
packages and evidence that cannot be reproduced from Git or retained local
receipts remain `unknown_provenance`. `curator-run.py` remains the authorization
owner and must be extended to verify this exact proof rather than accepting a
new caller assertion.

## Canonical estate census

### Collection boundary

The Mac mini requests a receiver-bound census from the MacBook. The receiver
allows only declared roots and regular files, rejects symlinks and traversal,
and returns a bounded signed-or-hashed result tied to:

- receiver identity and code digest;
- MacBook host identity;
- census schema and policy IDs;
- exact Copilot version;
- exact settings bytes and SHA-256;
- complete configured skill-directory list;
- exact user-level runtime context and every explicitly registered project
  context, including repository identity and HEAD;
- personal-skill root Git identity and HEAD;
- installed plugin identities and capability inventories;
- effective runtime skill inventory from `copilot skill list --json`;
- Dreaming publisher ownership and active bundle IDs;
- collection timestamp and completeness status.

Filesystem discovery and runtime discovery are reconciled separately for the
user-level runtime and each registered project context. A directory that cannot
be proven effectively enabled is shown as physical-only. A runtime skill that
cannot be mapped to one physical owner makes that context incomplete and
blocks automatic removal decisions. The user-level context must be complete
before any automatic estate mutation. An incomplete registered project context
also blocks mutation; an unregistered repository is outside the completeness
claim and cannot be cited as removal evidence.

### Root classes

The census distinguishes:

- Dreaming-owned remote publisher bundles;
- personal native skills under `~/.copilot/skills`;
- installed plugin package roots;
- the version-pinned Copilot CLI builtin skill root;
- configured external skill directories;
- project-local skill roots for explicitly registered repository contexts;
- stale publisher generations and package caches.

Only effectively enabled skills enter portfolio evaluation. All instances
remain visible in diagnostic totals. CLI builtins are enabled canonical
capabilities with `cli_builtin` authority. Their root identity is bound to the
captured Copilot version, and a CLI upgrade invalidates the census. Builtins
are never action targets and do not make an otherwise mapped census
incomplete.

### Snapshot and ledger

The mini writes one immutable census receipt and one replaceable current
snapshot. The current snapshot points to its receipt hash. Action records bind
the exact pre-action census and must never be authorized from dashboard data.

## Evaluation and decision policy

### Common evidence

An automatic action requires:

- a complete current census;
- an explicit census-scope receipt naming the user-level and registered project
  contexts used by the decision;
- current environment and complete-catalog routing evidence;
- task-value or redundancy evidence appropriate to the capability;
- current dependency and pin inventory;
- an exact pre-action target identity;
- a passing proposed-estate portfolio comparison;
- an open halt switch and exclusive writer lease;
- a tested restore path for the action kind.

Usage is evidence, not sole authority. Zero recorded use cannot by itself prove
that a skill or plugin is worthless.

### Personal skills

`legacy_machine` skills follow the existing curator policy with these changes:

- current evidence may authorize automatic consolidation or archive;
- no blanket 90-day waiting period applies when stronger completed-project,
  supersession, regression, or redundancy evidence exists;
- age-only retirement still requires complete affirmative non-use telemetry
  for the configured interval;
- a consolidation destination must pass evaluation with the source cases;
- archive remains Git-backed and writes retirement, tombstone, provenance, and
  restore records;
- withdrawal from effective discovery precedes archive when the source is
  currently active.

`user_protected` and `unknown_provenance` skills may be evaluated and shown in
recommendations but cannot be patched, moved, withdrawn, or archived
automatically.

### Dreaming-managed skills

Current learned skills retain the lifecycle policy from the conservative
design. Estate governance contributes complete-catalog and portfolio evidence,
but cannot bypass recurrence, exact revision evaluation, or approved-inventory
publication.

### Plugin skills

A plugin-level decision considers the plugin's complete capability set, not
only its skills. Automatic disablement is allowed only when all conditions are
true:

1. the plugin is currently enabled and has one exact unambiguous identity;
2. every skill capability is redundant, regressing, obsolete, or unsupported
   under current evidence;
3. every non-skill capability is enumerated and either unused with complete
   telemetry, superseded by an enabled capability, or explicitly declared
   unnecessary by policy;
4. no pin, durable prompt, scheduled job, MCP configuration, agent selection,
   hook, or LSP configuration depends on the plugin;
5. the proposed estate without the plugin passes routing and portfolio gates;
6. the settings transaction and runtime verification can be completed and
   rolled back.

If one capability remains valuable or unknown, the plugin stays enabled.
Individual plugin skills are never deleted, hidden by path edits, or patched.

## Action matrix

| Target | Keep | Consolidate | Withdraw/archive | Disable | Restore |
| --- | --- | --- | --- | --- | --- |
| Dreaming-managed | Automatic | Automatic under curator gates | Automatic under lifecycle gates | N/A | Existing restore and recertification |
| Legacy machine personal | Automatic | Automatic | Automatic | N/A | Git-backed restore, then recertify |
| User-protected personal | Automatic | Recommend only | Recommend only | N/A | User-directed |
| Unknown personal | Automatic | Recommend only | Recommend only | N/A | User-directed |
| Plugin-managed | Automatic | Recommend package-owner change only | Never edit package | Automatic only at whole-plugin gate | Settings restore and runtime verify |
| CLI builtin | Automatic | Never | Never | Never | N/A |

## Remote personal-skill transaction

The mini remains the decision and run-manifest owner. The MacBook receiver is a
constrained executor:

1. The mini seals the census hash, action plan, expected personal-root Git HEAD,
   expected dirty-path fingerprint, provenance evidence, dependency inventory,
   and operation order.
2. The receiver verifies its identity, code digest, halt state, root boundary,
   Git identity, expected HEAD, expected dirt, and allowed operation.
3. The receiver invokes the existing curator/archive helpers with the sealed
   authorization context.
4. It returns commit IDs, retirement records, tombstones, post-state inventory,
   and hashes.
5. The mini verifies the result before advancing the operation.

Unexpected dirty paths, a changed HEAD, a changed marker, missing Git history,
or an ambiguous SSH result stops the run in recovery-required state. The mini
reconciles by operation ID before retrying.

## Plugin disable and restore transaction

### Enablement semantics and enforcement gate

Plugin identity and settings-key derivation are source-type contracts, not
guesses:

- marketplace installs use the exact runtime/package identity and configured
  `name@marketplace` key;
- direct installs use the exact direct-install identity and the key form proven
  by the installed Copilot version;
- an absent key is treated as effectively enabled only when runtime inventory
  proves that state for the same plugin identity.

Before mutation is enabled for a source type, an installed-host qualification
must prove, using a reversible non-essential plugin of that source type, that
the derived explicit `false` key removes exactly that plugin's capabilities and
that restoring the prior key presence/value returns them. Marketplace and
direct installs qualify independently. An unqualified source type remains
report-only. A Copilot version change invalidates every qualification.

### Settings writer boundary

The receiver serializes all Dreaming settings operations through one
cross-process lock. Copilot CLI, editors, and other user processes are
explicitly non-cooperating writers; the Dreaming lock does not exclude them.
The receiver therefore uses a lossless atomic-exchange transaction rather than
claiming that a hash check plus rename is a compare-and-swap:

1. read the file and bind its inode, bytes, mode, and hash;
2. build and fsync a same-directory staged file containing the semantic
   single-key patch and matching required metadata;
3. atomically exchange the settings and staged paths with macOS
   `renamex_np(..., RENAME_SWAP)`, so the staged path retains the exact settings
   inode and bytes present at the exchange instant;
4. compare that exchanged-out preimage with the bound input;
5. if it matches, fsync the directory and verify the active semantic result;
6. if it differs, atomically exchange it back when the active path still
   contains the staged governance output, then report conflict;
7. if another writer also changed the active path before rollback, retain every
   exchanged inode under the private recovery directory, stop mutation, and
   require reconciliation rather than discarding any version.

The staged preimage is retained until runtime verification and receipt commit.
In-place writers holding an old file descriptor continue writing the
exchanged-out inode, so the receiver waits for that inode to become stable
before comparing or disposing of it. A volume without verified
`RENAME_SWAP` support remains report-only. All Dreaming writers still honor the
shared lock, but correctness against Copilot CLI and editors comes from the
atomic exchange retaining the exact displaced file, not from cooperation.

Qualification injects deterministic rename-based and open-file-descriptor
writes at barriers immediately before exchange, immediately after exchange,
and during rollback. Every injected byte must either remain active, be restored
as the uncontended preimage, or survive in the private recovery set with a
recovery-required receipt. No timing-dependent probabilistic race is accepted.

### Disable

The receiver:

1. acquires the settings-writer boundary and snapshots the exact settings
   bytes, SHA-256, inode, mode, and plugin runtime inventory;
2. parses JSON and requires the expected effective pre-action plugin state,
   including the qualified source-specific key and prior key presence/value;
3. uses the atomic-exchange operation to write only the qualified
   `enabledPlugins[plugin_id] = false` semantic patch while preserving all
   unrelated JSON values;
4. fsyncs the file and parent directory;
5. starts a bounded fresh Copilot inventory process;
6. proves that every capability owned solely by the plugin disappeared and
   unrelated capabilities remained;
7. writes an immutable transaction receipt containing before bytes, before and
   after hashes, semantic patch, plugin identity, census hash, and runtime
   verification.

Any parse failure, concurrent edit, inability to map capabilities, runtime
startup failure, or verification mismatch aborts. Before the transaction
releases its writer boundary, an immediate abort may restore the exact before
bytes from the retained exchanged-out inode only when the current file still
equals the transaction's verified output bytes. Otherwise it retains both
versions and enters recovery-required state. If immediate restore cannot be
verified, the shared recovery-required state blocks all later estate mutations.

### Restore

Restore is automatic when rollback requires it and may also be explicitly
requested later. It is a semantic reversal of one settings key:

1. acquire the settings-writer boundary and verify the target key still has the
   exact governance-written value from the receipt;
2. restore that key's exact prior presence/value while preserving unrelated
   current settings and later governance transactions;
3. use the same lossless atomic-exchange and post-write verification boundary;
4. prove through runtime inventory that the plugin's capabilities returned.

When the whole file still equals the transaction's expected after bytes, the
result must equal the exact before bytes. When unrelated settings or later
plugin transactions changed, restore preserves those changes and reverses only
the target key. A change to the target key is a conflict and is never
overwritten. Receipts form an ordered settings ledger so repeated disable and
restore operations for one plugin cannot be applied out of order.

## Data flow

1. The mini scheduler acquires the existing writer lease.
2. It requests and verifies the MacBook census.
3. It imports or refreshes estate records and authority classes.
4. It schedules bounded evaluations and builds a proposed estate.
5. It runs complete-catalog routing and portfolio comparisons.
6. It emits a curator report with keep, consolidation, archive, plugin-disable,
   and recommendation-only decisions.
7. `curator-run.py` seals the report, census, dependencies, settings identity,
   and action plan.
8. The mini dispatches ordered operations through the receiver.
9. The receiver performs only the sealed operation and returns receipts.
10. The mini verifies the final census, records the run, and refreshes the
    dashboard snapshot.

No dashboard route, report renderer, or MacBook scheduled job can authorize a
mutation.

## Dashboard integration

The dashboard adds an **Estate** view sourced from the mini's verified snapshot:

- physical instances, enabled canonical capabilities, disabled capabilities,
  and unresolved instances;
- counts by authority class and source type;
- each skill's owner, effective state, source, provenance confidence,
  evaluation state, usage completeness, dependencies, and latest decision;
- each plugin's version, all capability classes, enabled state, retained-value
  rationale, disable eligibility, and restore receipt;
- recent consolidations, archives, disables, restores, failures, and
  recommendation-only findings;
- census freshness, receiver identity, settings hash, completeness, and
  recovery-required status.

The existing learned-skill page remains the detailed lifecycle view for
Dreaming-managed skills. Totals label whether they represent physical
instances or enabled canonical capabilities.

## Realistic failure model

| Failure | Required behavior |
| --- | --- |
| Disabled plugin files remain on disk | Show physical-only; do not count them as enabled |
| Same skill exists in several bundles or packages | Preserve instances; do not merge identity by name |
| Runtime skill cannot be mapped to an owner | Mark census incomplete and block removal |
| Copilot builtin root moves after CLI update | Invalidate census and reclassify the version-bound builtin inventory |
| Project context is not registered | Exclude it from completeness claims and never use it as removal evidence |
| Missing `.agent-created` marker | Classify unknown, never human or machine by assumption |
| Malformed or conflicting provenance | Recommendation only and visible error |
| Plugin contains an unenumerated MCP server or agent | Keep plugin enabled |
| One plugin skill is redundant but another capability is valuable | Keep plugin enabled |
| Settings change after census or during replacement | Preserve the changed bytes, refuse mutation, and enter conflict or recovery-required state |
| SSH disconnect occurs after remote mutation | Reconcile operation ID; never blind retry |
| Personal skill has uncommitted edits | Refuse archive and preserve worktree |
| Dependency enumeration fails | Keep target and fail the run closed |
| Portfolio evidence is stale or unavailable | Freeze removals and retain current estate |
| Settings rollback meets a user edit | Report conflict; never overwrite user settings |
| Dashboard state is stale or malformed | Show unhealthy/unavailable; never synthesize authority |
| Mini or receiver code identity changes | Invalidate census and pending authorizations |

## Hard invariants

1. The Mac mini is the sole scheduler, decision owner, and action-manifest
   owner.
2. The MacBook runs no Dreaming scheduler or autonomous reviewer.
3. Every effective Copilot skill in the user-level context and each explicitly
   registered project context is represented exactly once as an enabled
   canonical capability or explicitly reported unresolved; the dashboard names
   the bounded contexts and never claims completeness outside them.
4. Physical copies and enabled capabilities are reported as different counts.
5. Missing provenance never grants autonomous mutation authority.
6. User-protected and unknown-provenance skills are never automatically
   modified or archived.
7. Plugin package contents are never edited by governance.
8. A plugin is disabled only as a complete package after every capability and
   dependency is accounted for.
9. Every automatic removal passes a proposed complete-estate routing and
   portfolio gate.
10. Personal retirement is Git-backed and restorable.
11. Plugin disablement and restore are qualified per install source, preserve
    unrelated settings and later transactions, and never overwrite a detected
    intervening edit; an uncontended full-file round trip is byte-exact.
12. Pins and incomplete dependency discovery block mutation.
13. The halt switch and recovery-required state block every estate writer.
14. Ambiguous remote outcomes are reconciled, not retried blindly.
15. The dashboard is a reader and cannot create authority.
16. No transcript text, credential, private case, or settings bytes appear in
    public repositories or dashboard payloads.

## Acceptance criteria

- **AC-01:** A census fixture containing personal, plugin, publisher, builtin,
  registered project, cached, disabled, and duplicate instances produces
  complete physical and effective inventories without name-based identity
  merging and labels the bounded runtime contexts.
- **AC-02:** Every enabled runtime skill maps to one canonical capability; an
  unmapped or multiply mapped skill marks the census incomplete.
- **AC-03:** Valid current Dreaming, verified legacy creation proof, explicit
  user, plugin, CLI builtin, marker-only legacy, and malformed/absent
  provenance fixtures receive the correct authority class.
- **AC-04:** Legacy machine-created personal skills can be consolidated or
  archived automatically through the remote curator transaction, while user
  and unknown skills remain unchanged.
- **AC-05:** A personal archive leaves a verified restore commit and retirement
  record, and restore returns the exact package.
- **AC-06:** A plugin with only fully superseded and dependency-free
  capabilities can be disabled; a plugin with one valuable, unknown, pinned,
  or unenumerated capability cannot.
- **AC-07:** Each qualified plugin install source has a real installed-host
  disable/restore proof; disable changes only the intended settings key,
  removes the plugin's effective capabilities, preserves unrelated
  capabilities, and retains every settings version displaced by a raced
  non-cooperating writer.
- **AC-08:** Plugin restore returns exact prior settings bytes when the file is
  otherwise unchanged, composes with later plugin transactions, preserves
  unrelated later settings edits, and refuses to overwrite a changed target
  key.
- **AC-09:** Every automatic action is bound to a current census, complete
  dependencies, current target identity, and passing proposed-estate routing
  and portfolio evidence.
- **AC-10:** Halt, stale census, code drift, settings drift, Git drift,
  incomplete inventory, failed verification, or ambiguous SSH outcome prevents
  subsequent mutation.
- **AC-11:** The dashboard exposes complete estate totals, authority classes,
  plugin capability sets, action history, freshness, and recovery status
  without becoming an authority source.
- **AC-12:** The installed two-host topology completes one report-only full
  census and one reversible mutation exercise while the MacBook remains free
  of Dreaming workers.
- **AC-13:** Public-repository validation rejects settings bytes, transcript
  content, credentials, private evaluation cases, and local authority records.

## Deterministic check contract

### CHK-01: Canonical census reconciliation

- **Protects:** AC-01 and AC-02.
- **Setup:** Build a receiver fixture with duplicate publisher generations,
  same-name plugin and personal skills, CLI builtins, registered and
  unregistered project contexts, a disabled plugin cache, one unmapped runtime
  skill, and exact package identities.
- **Pass signal:** Physical and enabled totals differ correctly; deterministic
  identities remain separate; builtins map without becoming action targets;
  the unmapped registered-context runtime skill makes completeness false; the
  unregistered context is visibly outside the completeness claim.
- **Failure signal:** Counts are silently deduplicated by name, disabled files
  appear enabled, or completeness remains true.
- **Why it proves the contract:** It exercises the boundary between files on
  disk and capabilities the runtime can actually load.

### CHK-02: Provenance authority matrix

- **Protects:** AC-03 and AC-04.
- **Setup:** Import valid current, legacy marker-plus-envelope, reproducible
  legacy Git/session proof, marker-only legacy, explicit user-owned,
  plugin-owned, CLI builtin, unmarked, malformed, and conflicting fixtures.
- **Pass signal:** Exactly one authority class is assigned; only machine-owned
  classes with a verified authorization proof enter automatic personal-skill
  action plans; builtins are keep-only.
- **Failure signal:** Marker absence becomes user ownership, malformed evidence
  becomes machine authority, or protected fixtures enter a mutation plan.
- **Why it proves the contract:** It prevents the ownership inference that
  could destroy user work.

### CHK-03: Remote curator transaction

- **Protects:** AC-04, AC-05, AC-09, and AC-10.
- **Setup:** Exercise clean, dirty, stale-HEAD, changed-marker, incomplete
  dependency, successful archive, failed commit, disconnect-after-commit, and
  restore fixtures through the receiver.
- **Pass signal:** Only the sealed clean operation commits; ambiguous completion
  reconciles to one result; restore reproduces the exact package.
- **Failure signal:** A blind retry creates a second mutation, dirty work is
  lost, or an archive lacks a restore record.
- **Why it proves the contract:** It verifies that mini-owned authority can
  safely control a MacBook-owned Git root.

### CHK-04: Complete plugin capability gate

- **Protects:** AC-06 and AC-09.
- **Setup:** Model plugins with all-redundant skills; mixed valuable and
  redundant skills; MCP, agent, hook, and LSP capabilities; unknown capability
  metadata; explicit dependencies; and a passing/failing proposed estate.
- **Pass signal:** Only the fully enumerated, dependency-free, passing plugin is
  eligible for disablement.
- **Failure signal:** One low-value skill authorizes a whole-plugin disable or
  an unknown non-skill capability is ignored.
- **Why it proves the contract:** It makes plugin-level authority depend on the
  package's real blast radius.

### CHK-05: Settings disable transaction

- **Protects:** AC-07 and AC-10.
- **Setup:** Use marketplace and direct-install identity fixtures, absent-key
  enabled state, valid settings with unrelated keys, malformed JSON, changed
  pre-write bytes, barrier-injected rename and open-file-descriptor writes
  immediately before exchange, immediately after exchange, and during
  rollback, unsupported-volume behavior, write failure, runtime verification
  failure, and a successful plugin disable.
- **Pass signal:** Success changes only the target semantic key and verifies the
  runtime delta; source types remain report-only before qualification; every
  failure leaves the displaced independent bytes active, restores uncontended
  exact before bytes, or retains every competing version in recovery-required
  evidence.
- **Failure signal:** Any injected version is discarded, an unsupported volume
  mutates settings, unrelated settings change, partial bytes remain, or success
  is recorded without runtime proof.
- **Why it proves the contract:** It validates the only new durable
  configuration writer.

### CHK-06: Conflict-safe plugin restore

- **Protects:** AC-08.
- **Setup:** Restore once from exact expected post-disable bytes; disable two
  plugins and restore them in supported orders; add an unrelated user edit;
  and independently change the target key before restore.
- **Pass signal:** The uncontended case restores byte-for-byte; stacked
  transactions reverse only their own keys; unrelated edits survive; a target
  key edit reports conflict and remains untouched.
- **Failure signal:** Restore loses an unrelated edit, changes another plugin's
  state, overwrites a changed target key, or fails the uncontended byte-exact
  round trip.
- **Why it proves the contract:** It proves reversibility without turning
  rollback into a source of data loss.

### CHK-07: Estate action evidence binding

- **Protects:** AC-09 and AC-10.
- **Setup:** Change each census, target inventory, dependency set, model,
  routing receipt, portfolio receipt, policy, receiver digest, and halt state
  after authorization.
- **Pass signal:** Every drift invalidates the pending action while leaving the
  current estate unchanged.
- **Failure signal:** Any changed input is accepted by an old authorization.
- **Why it proves the contract:** It ensures decisions apply only to the exact
  estate that was evaluated.

### CHK-08: Dashboard truthfulness

- **Protects:** AC-11.
- **Setup:** Serve complete, stale, incomplete, malformed, recovery-required,
  disabled-plugin, restored-plugin, archived, protected, and unknown fixtures.
- **Pass signal:** API and UI label each state, separate physical and effective
  totals, and expose authority without offering mutation endpoints.
- **Failure signal:** Missing data becomes zero, protected skills disappear
  from totals, or dashboard state authorizes an action.
- **Why it proves the contract:** It keeps the review surface complete and
  non-authoritative.

### CHK-09: Installed two-host proof

- **Protects:** AC-12.
- **Setup:** On the installed mini/MacBook topology, run a full report-only
  census, then exercise one reversible fixture or approved low-risk mutation
  through the real receiver and restore it.
- **Pass signal:** The mini owns every run record; the MacBook shows no Dreaming
  worker; before and final estate inventories match; all receipts verify.
- **Failure signal:** A MacBook scheduler appears, final inventory differs, or
  the action cannot be reconciled and restored.
- **Why it proves the contract:** It tests the actual host boundary rather than
  only local fixtures.

### CHK-10: Private-boundary validation

- **Protects:** AC-13.
- **Setup:** Seed settings, credentials, transcript sentinels, private cases,
  and authority receipts into fixture state before repository and dashboard
  export.
- **Pass signal:** Repository validation and API serializers reject or redact
  every sentinel.
- **Failure signal:** Any private byte appears in a public tracked file or
  dashboard response.
- **Why it proves the contract:** It exercises both durable and presentation
  exfiltration paths.

## Migration

### Phase 1: report-only census

1. Add receiver census and mini-side schema readers with mutation disabled.
2. Inventory every physical instance and reconcile the user-level runtime,
   CLI builtins, and every explicitly registered project context.
3. Import existing personal skills into authority classes without moving,
   withdrawing, or archiving them.
4. Show the complete estate and all unresolved mappings on the dashboard.
5. Require at least one complete stable installed census before proceeding.

### Phase 2: evaluation and recommendations

1. Build complete-estate routing and portfolio baselines.
2. Evaluate legacy machine skills and plugin capability sets.
3. Produce report-only consolidation, archive, plugin-disable, and protected
   recommendations.
4. Resolve incomplete mappings and dependency-discovery gaps.

### Phase 3: personal-skill enforcement

1. Exercise remote archive and restore against a fixture skill.
2. Enable automatic consolidation and retirement only for valid
   `legacy_machine` skills.
3. Preserve user and unknown classes as recommendation-only.
4. Re-census and verify after every transaction.

### Phase 4: plugin enforcement

1. Exercise settings disable and restore against fixtures, including deliberate
   barrier-injected non-cooperating rename and open-file-descriptor writes,
   unsupported atomic-exchange behavior, and stacked plugin transactions.
2. Qualify marketplace and direct install sources independently through one
   reversible installed-host plugin disable/restore proof for each source type
   that will receive automatic authority.
3. Enable whole-plugin automatic disablement only for qualified source types
   after complete capability, dependency, routing, portfolio, and restore gates
   pass.
4. Keep uninstall permanently outside automatic authority.

Migration does not bulk-delete skills. It may produce many automatic actions,
but each target receives an individually sealed decision and reversible
transaction.

## Rollback

1. Activate the shared halt switch and stop the mini scheduler.
2. Wait for the writer lease to clear or reconcile the active operation.
3. Restore the prior mini runtime and receiver code.
4. Roll back active personal-skill curator runs through `curator-run.py`; use
   `restore-skill.sh` for completed archives.
5. Restore plugin settings from the ordered ledger in reverse transaction
   order. Reverse only each target key, preserve unrelated current settings,
   and stop on a target-key conflict or failed atomic-exchange transaction.
6. Verify the MacBook's runtime inventory and Dreaming publisher bundle.
7. Leave census, evaluation, decision, and action receipts as inert local
   evidence.
8. Re-enable scheduling only after the prior installed self-test passes.

Rollback never deletes evidence, rewrites a user-edited settings file, changes
plugin package contents, or grants old readers authority over the new estate
schema.

## Fail-closed evidence

The critical boundary is proven only when deliberate tests show that:

- missing or ambiguous runtime ownership makes the census incomplete;
- marker absence and malformed provenance cannot authorize mutation;
- one valuable, unknown, pinned, or unenumerated plugin capability blocks
  whole-plugin disablement;
- stale census, settings, Git, dependency, evaluator, policy, receiver, or
  portfolio identity blocks action;
- remote disconnect after mutation is reconciled without duplicate action;
- failed personal retirement leaves user files and Git history intact;
- failed plugin disable restores exact settings bytes;
- deterministic non-cooperating settings writes at every atomic-exchange and
  rollback boundary are preserved or retained for fail-closed recovery;
- stacked plugin restores preserve unrelated settings and later transactions;
- plugin restore refuses to overwrite an intervening target-key edit;
- halt and recovery-required state block both personal and plugin writers;
- dashboard and repository exports cannot expose settings bytes, transcripts,
  credentials, cases, or authority receipts.

## Definition of Done: Unified skill estate governance

- [ ] The Mac mini produces a verified complete census of every physical and
      effectively enabled Copilot skill in the MacBook's user-level and
      explicitly registered project contexts, including CLI builtins, while
      labeling the bounded scope.
- [ ] Canonical capability identity, physical-instance identity, provenance
      classes, and unresolved mappings are implemented and visible.
- [ ] Legacy machine-created personal skills are automatically evaluated,
      consolidated, withdrawn, archived, and restored through the existing
      curator and Git-backed retirement owners.
- [ ] User-protected and unknown-provenance personal skills remain
      recommendation-only.
- [ ] Plugin decisions account for the complete skill, agent, hook, MCP, and
      LSP capability set and never edit package contents.
- [ ] Eligible low-value plugins can be transactionally disabled through
      source-qualified `enabledPlugins` keys and restored compositionally
      without overwriting user changes.
- [ ] Every automatic action is bound to current census, dependency, target,
      routing, portfolio, policy, receiver, and halt-state evidence.
- [ ] Ambiguous SSH outcomes and failed verification enter recovery-required
      state and reconcile safely.
- [ ] The dashboard truthfully shows complete estate totals, authority,
      provenance, plugin value, decisions, freshness, and recovery state.
- [ ] The deterministic check contract passes, including the installed
      two-host report-only census and reversible mutation proof.
- [ ] Rollback restores the pre-enforcement estate while preserving evidence.
- [ ] Public validation proves that private settings, transcripts, credentials,
      cases, and local authority state cannot leave the local boundary.
