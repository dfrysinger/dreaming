# Multi-CLI Dreaming

## Objective

Let one Dreaming installation learn from completed Copilot CLI, Claude Code,
and Codex sessions while preserving one mutation owner, one evidence model, and
one reversible skill lifecycle.

The CLI that produced a session and the CLI that reviews it are independent
choices. Supporting a new session source must not require another Dreaming
daemon or another copy of its ledgers.

## User outcome

The owner can:

1. select which installed CLIs contribute sessions;
2. see the source CLI on every reviewed-session record and evidence entry;
3. use the same routing, evaluation, promotion, curation, and rollback policy
   regardless of where a session originated;
4. disable one source without disabling the others;
5. keep Dreaming's orchestration skills out of normal interactive context;
6. explicitly approve which session sources may be sent to which review
   executor;
7. make learned local skills available to selected CLIs from one canonical
   source.

## Lane

Systemic.

The change introduces a shared source boundary, namespaced session identity,
cross-CLI discovery and triggers, source-aware state migration, and a separate
publication path for learned skills.

## Non-goals

- Run an independent Dreaming pipeline inside each CLI.
- Require the source CLI to review its own sessions.
- Copy complete transcripts into a new long-lived database.
- Replace vendor session stores, synchronization, or search products.
- Generalize memory deletion beyond GitHub Copilot Memory.
- Make launchd portable to another operating system or scheduler.
- Treat vendor-private file formats as stable without explicit version checks.
- Install Dreaming's five orchestration skills into every interactive CLI.

## Reuse contract

Keep the existing learning owners:

- `skill-review` decides whether evidence justifies a durable artifact.
- The artifact router chooses instruction, factual memory, skill, support file,
  or discard.
- The review ledger prevents duplicate review.
- The shared writer lease serializes autonomous mutation.
- Evidence envelopes record task identity, routing, claims, and evaluation.
- `skill-create`, `skill-manage`, and `skill-curator` retain authoring,
  promotion, archive, restore, and rollback authority.
- `memory-curator` remains the only GitHub Copilot Memory deletion path.
- The daily Dreaming owner, self-test, watchdog, halt switch, and activation
  generation remain authoritative.

Extend only the edges that are tied to one CLI:

- session discovery and scoring;
- bounded transcript rendering;
- foreground or lifecycle triggers;
- headless review execution;
- learned-skill publication.

## Architecture

### One pipeline, three adapter boundaries

Dreaming gains three independent interfaces:

1. **Session source**: finds and reads completed sessions.
2. **Review executor**: runs the existing review prompt and tools.
3. **Skill publisher**: exposes committed local skills to interactive CLIs.

The first implementation adds Copilot, Claude, and Codex session sources while
keeping Copilot as the review executor. A later executor adapter can add Claude
or Codex without changing session ingestion or learning state.

### Session source interface

Each source implements the same commands:

```text
session-source watermark
session-source list --floor <time> --ceiling <opaque> --cursor <opaque> --page-size <n>
session-source inspect --session <source-qualified-id>
session-source render --session <source-qualified-id>
session-source doctor
```

`watermark` captures an opaque source high watermark for a scan generation.
`list` returns a next cursor and emits candidate metadata and comparable
scoring features in stable `updated_at + native_session_id` order within the
fixed floor and ceiling. `inspect` emits identity and source health without
transcript content. `render` emits a bounded normalized event stream for one
session. `doctor` validates paths, schema support, permissions, and a
representative read.

The commands emit JSON Lines. Errors are structured and nonzero. An adapter
must not return success-shaped empty data for a missing, unreadable, or
unsupported store.

### Source-qualified identity

A session reference contains:

```json
{
  "source": "copilot",
  "native_session_id": "opaque-source-id",
  "qualified_session_id": "copilot:opaque-source-id",
  "repository_scope": "opaque-local-scope-id",
  "started_at": "2026-08-02T00:00:00Z",
  "updated_at": "2026-08-02T00:00:00Z",
  "source_revision": "opaque-revision",
  "event_frontier": "opaque-monotonic-frontier",
  "snapshot_digest": "sha256:...",
  "completion_state": "terminal",
  "adapter_version": 1
}
```

`source` is `copilot`, `claude`, or `codex`. Native identifiers remain opaque.
`qualified_session_id` is the only session key used by shared ledgers.
`repository_scope` is a local opaque mapping, not a repository name or URL.
`event_frontier` is an adapter-defined monotonic position such as a durable
event sequence or append offset. `snapshot_digest` covers the ordered normalized
event prefix through that frontier. `source_revision` combines the frontier,
digest, completion state, and adapter version.

An adapter may declare a new revision append-only only when the old frontier is
a verified prefix of the new snapshot. Rewrites, deletions, reordering, and
adapter-version changes create a replacement revision, not an append.

The state store may retain this metadata and compact scoring features. It must
not retain full transcript text, repository content, credentials, private
links, or tool outputs.

### Normalized event stream

`render` maps source records to a small common vocabulary:

```json
{
  "source": "claude",
  "qualified_session_id": "claude:opaque-source-id",
  "sequence": 17,
  "timestamp": "2026-08-02T00:00:00Z",
  "kind": "user_message",
  "tool_name": null,
  "text": "bounded source text",
  "source_event_id": "opaque-event-id"
}
```

Supported kinds are:

- `user_message`
- `assistant_message`
- `tool_call`
- `tool_result`
- `checkpoint`
- `summary`
- `session_end`

Adapters preserve ordering and source event identity. Unknown source events are
reported in adapter diagnostics and omitted only when the adapter version
explicitly declares that behavior. A schema change that could alter message,
tool, or completion meaning fails closed.

`render` applies byte, event, and per-field limits before output. It marks
truncation explicitly. The review prompt treats truncated evidence as bounded
evidence, not as a complete session.

The source layer writes this output as an immutable, digest-named snapshot
before review starts. The review executor receives only the snapshot and its
identity. It never receives a native source query, file path, database handle,
or command to execute against the source store.

Immediately before ledger or skill mutation, Dreaming re-runs `inspect`. If the
frontier, digest, completion state, or adapter version changed, the review
result is stale. Dreaming records the stale result without mutation and queues
the latest revision.

### Source implementations

#### Copilot

The Copilot adapter initially preserves the supported
`session_store_sql`-backed query path through a bounded, read-only extraction
pass. That pass may use Copilot's session tools, but it has no Dreaming mutation
skills and produces the immutable normalized snapshot before the separate
review executor starts.

The adapter boundary allows a supported local or synchronized session API to
replace this implementation without changing Dreaming state or prompts.
Dreaming does not add an independent parser for Copilot's complete session
files unless that format receives a supported version contract.

#### Claude

The Claude adapter reads the native project session records used by Claude
Code resume. It validates the record shape before scoring or rendering.

Claude lifecycle hooks may enqueue a completed session reference. Hooks perform
no review and no skill mutation. They append one bounded request and return.
Scheduled discovery remains the recovery path when hooks are absent, disabled,
or interrupted.

#### Codex

The Codex adapter reads native persisted thread or history records and validates
their schema before use. It does not ingest sessions created with ephemeral
storage.

Codex lifecycle hooks may enqueue a completed session when a supported end
event is available. Otherwise scheduled discovery is the required path.
Noninteractive `codex exec --json` is an executor option, not a session-source
requirement.

### Candidate scoring

Every source adapter emits the features used by the shared scorer:

- user and assistant turn count;
- tool call count;
- distinct tool count;
- correction signals;
- skill-intent signals;
- completion state;
- duration and last-update time;
- daemon-origin exclusion.

Source adapters own extraction from native records. The shared scorer owns
weights, thresholds, ordering, limits, and the lookback window.

Equivalent normalized fixtures must produce equivalent scores. A source may
emit an unavailable feature, but it may not silently substitute zero when zero
would change eligibility.

### Completion admission

Every source emits one completion state:

- `active`: the source proves the session can still receive events;
- `terminal`: the source records a supported end event;
- `quiet`: no terminal marker exists, but the session has been unchanged for
  the configured quiet period and has no in-progress tool or turn.

Only `terminal` and `quiet` sessions are eligible. A hook is a discovery hint,
not proof of completion. Scheduled discovery and the review path revalidate the
state.

An observed `active` session is written to a durable unsettled-session index
before its discovery page may advance. The index contains only source-qualified
identity, revision metadata, and the next quiet-state check time. Scheduled
runs re-inspect due entries independently from the discovery cursor. An entry
leaves the index only after it is queued as `terminal` or `quiet`, the source
proves that it was deleted, or an explicit repair action resolves an
unsupported record.

### Discovery, queue, and deduplication

Scheduled discovery asks every enabled source for candidates. Source failure is
recorded independently:

- one unavailable source does not erase or disable another;
- a source with unsupported schema contributes no candidates;
- Dreaming reports a partial-source run rather than claiming full coverage;
- mutation proceeds only for candidates whose source evidence was read
  successfully.

Each source has a settled watermark, overlap window, unsettled-session index,
and at most one durable scan generation:

```json
{
  "floor": "prior-settled-watermark-minus-overlap",
  "ceiling": "source-high-watermark-at-generation-start",
  "cursor": "opaque-page-position"
}
```

A generation keeps the same floor and ceiling across scheduled runs until it
reaches source exhaustion. Discovery:

1. resumes the existing generation or atomically captures a new ceiling;
2. reads stable keyset pages within the fixed floor and ceiling;
3. atomically queues eligible revisions and records active revisions in the
   unsettled-session index;
4. advances the generation cursor only after all page effects are durable;
5. moves the settled watermark to the generation ceiling only after source
   exhaustion, then clears the generation.

New or updated records above the fixed ceiling wait for the next generation.
The fixed generation prevents a page-limited backlog from falling behind a
moving time filter. The overlap and source-revision deduplication recover
timestamp ties, clock skew, interrupted page writes, and sessions updated near
a generation boundary. A source outage never advances its generation. A
backlog is drained across runs without skipping newer or older tied sessions.

Lifecycle hooks and foreground instructions append source-qualified references
to one queue. Queue writes are atomic and idempotent by
`qualified_session_id + source_revision`.

When a newer revision is queued, an older queued revision is superseded. An
older review already in flight may finish, but its pre-mutation snapshot check
prevents it from writing.

The review ledger gains source, revision, frontier, digest, and adapter version.
Existing unqualified entries migrate lazily as Copilot entries with
`source_revision=legacy-reviewed`. On first observation, Dreaming records the
current Copilot frontier and digest without review when the source session has
not changed since the ledger's review timestamp. A later durable update enters
the normal revision path. When source timestamps cannot prove this ordering,
the entry remains held for explicit repair rather than being silently re-run or
suppressed.

### Review executor

The executor receives:

- a source-qualified session reference;
- the immutable bounded normalized snapshot and digest;
- the unchanged routing and evidence rules;
- the Dreaming and shared skill roots;
- a required machine-readable completion sentinel.

The executor does not gain authority from being the source CLI. Copilot may
review a Claude or Codex session. The resulting evidence entry records both
`source` and `review_executor`.

The first executor remains a bounded Copilot process and reuses the existing
authentication, plugin-root, completion, and timeout logic. Its capability
profile changes: it is source-blind and cannot invoke native session tools or
read native session roots.

The Copilot path is split into two processes:

1. a read-only extraction process may use `session_store_sql`, has no mutation
   roots, and emits only a normalized snapshot;
2. a review and mutation process receives that snapshot, excludes
   `session_store_sql` and source-adapter tools, and runs inside an operating
   system filesystem boundary.

The review boundary allows only the immutable snapshot, the Dreaming and shared
skill bundles, the canonical local skill root, the public skills repository as
a read-only root, the private Dreaming state needed by the pass, and required
Copilot authentication material. Read-only public access preserves umbrella
skill discovery and the public-repository unchanged guard without granting
autonomous mutation authority. The boundary denies Copilot session state,
Claude session roots, Codex session roots, unrelated home-directory content,
and temporary-directory access. Generic shell access, when required by
existing skill-management scripts, remains inside the same filesystem boundary
and therefore cannot bypass source policy.

The Copilot invocation does not use `--allow-all` or `--allow-all-paths`.
`--available-tools` or `--excluded-tools` removes native session tools before
model execution, while `--allow-all-tools` may approve only the remaining
bounded tool set for noninteractive use.

The process starts only when both the Copilot tool exclusions and the
filesystem boundary are active. Failing to create either boundary leaves the
queue item retryable without review or mutation.

### Source-to-executor transfer policy

Cross-vendor review is explicit because session evidence may contain private
code, prompts, tool output, and organization data.

Configuration contains a complete allowlist of source-to-executor routes:

```text
DREAMING_SOURCE_EXECUTOR_ALLOW="copilot>copilot,claude>copilot,codex>copilot"
```

Same-vendor transfer is not implied. A selected source without an allowed,
available executor route fails configuration preflight. The installer explains
that an allowed route sends bounded session evidence to the executor's model
provider.

Before snapshot persistence or transfer, adapters:

- remove known credential and environment-secret fields;
- omit unneeded source metadata and native file paths;
- enforce per-event, per-field, and total byte limits;
- preserve explicit redaction and truncation markers;
- reject source records whose sensitive fields cannot be separated safely.

Denied routes are never rendered. Transfer policy is checked before source
read, again before executor launch, and recorded by route name and policy
version in the private run result.

Every executor adapter, including the first Copilot executor, satisfies the
same contract:

- isolated noninteractive invocation;
- no native source-store tools or source-adapter commands;
- an operating system boundary that denies every native session root;
- explicit permission and network posture;
- Dreaming orchestration skills loaded without global interactive install;
- user and project instructions disabled unless the pass requires them;
- bounded timeout and output;
- completion sentinel independent from process exit;
- deterministic self-test under launchd.

### Evidence and artifact changes

Every new evidence entry adds:

```json
{
  "session_id": "claude:opaque-source-id",
  "source": "claude",
  "source_revision": "opaque-revision",
  "review_executor": "copilot"
}
```

The envelope continues to contain summaries and opaque identifiers only.
Promotion strips the private envelope as before. Public skill content must not
name private sessions, repositories, organizations, or source-specific paths.

Artifact routing and evaluation remain source-neutral. A procedure observed in
different CLIs may count as independent evidence only when the task keys are
independent. The source difference alone does not make two observations
independent.

### Cross-source task correlation

Task identity keeps the existing opaque task-key policy:

1. a platform task ID, when the source exposes one;
2. an explicit `DREAMING_TASK_KEY` propagated by a wrapper, handoff, or
   lifecycle trigger;
3. a newly minted random task key only when the producer knows the current
   session starts a new task;
4. otherwise `independence=unverified`.

Dreaming never derives a verified task key from transcript text, repository
name, timestamps, native session IDs, or source CLI. A local keyed correlation
hint may mark two observations as possible duplicates, but it cannot increase
evidence strength or appear in public artifacts. Mirrored work across two CLIs
therefore counts once only when an explicit opaque task key connects it;
otherwise neither observation increases verified independent-task count.

### Learned-skill publication

`SKILLS_LOCAL_ROOT` remains the single mutable source for agent-created local
skills. Dreaming does not maintain mutable copies under `~/.claude` and
`~/.codex`.

After a committed local change, a publisher materializes a read-only,
content-addressed local plugin bundle and reconciles that bundle with selected
CLIs through their native plugin or skill mechanism. Publication does not grant
the interactive CLI access to Dreaming's five orchestration skills.

The publisher records bundle identity and per-CLI ownership. Removing one CLI
removes only registrations owned by Dreaming. A foreign installation with the
same name fails closed.

Every target adapter implements:

```text
skill-publisher doctor
skill-publisher inventory
skill-publisher install --bundle <path> --bundle-id <sha256>
skill-publisher verify --bundle-id <sha256>
skill-publisher remove
```

The bundle manifest records the content hash, complete file inventory, local
skill Git revision, and the absence of Dreaming orchestration skills.
Installation is enabled only when the target CLI has a tested native operation
that preserves exact source identity and supports ownership-safe removal.
Symlink farms and mutable per-CLI copies are not fallbacks.

The target capability matrix is versioned test data. Each row records the
minimum supported CLI version, registration operation, inventory proof,
removal operation, and unsupported result. A selected target without a proven
row fails explicit preflight or remains residual when restored from saved
configuration.

The initial matrix is:

| Target | Immutable registration | Inventory and verification | Removal | Initial status |
|---|---|---|---|---|
| Copilot CLI | Mount the exact bundle on every managed launch with `copilot --plugin-dir <bundle>` | Run `copilot --plugin-dir <bundle> plugin list` and verify the external plugin name, manifest version, path, and bundle digest | Atomically remove the bundle path from the managed launcher configuration | Supported only through a launcher Dreaming or `remote-agent-stack` owns |
| Claude Code | Add the bundle path as a uniquely named user-scoped local marketplace, then install the learned-skill plugin from that marketplace | Use `claude plugin list --json`, resolve the installed plugin, and compare its manifest and complete skill inventory with the bundle manifest | Uninstall the owned plugin, then remove only the owned marketplace entry | Gated on an install-time conformance probe proving exact local-source identity and ownership-safe replacement |
| Codex | Add the bundle path with `codex plugin marketplace add`, then install with `codex plugin add <plugin>@<marketplace>` | Use `codex plugin list --available --json` and compare marketplace, installed state, manifest, and complete skill inventory with the bundle manifest | Remove the owned plugin, then remove only the owned marketplace entry | Gated on an install-time conformance probe proving ownership-safe replacement |

The generated plugin and marketplace names include a Dreaming-owned stable
prefix and the bundle digest. The owner journal maps each selected target to
the exact names, paths, and digest it installed. Replacement prepares and
verifies the new bundle before changing the active registration. If a target
cannot preserve the old working registration until the replacement is ready,
that CLI version is unsupported.

Publication is a separate phase from learning. A publication failure does not
roll back a valid local skill commit, and it does not mark the learning pass as
fully published.

## Configuration

The durable configuration adds:

```text
DREAMING_SESSION_SOURCES=copilot,claude,codex
DREAMING_REVIEW_EXECUTOR=copilot
DREAMING_SOURCE_EXECUTOR_ALLOW="copilot>copilot,claude>copilot,codex>copilot"
DREAMING_SKILL_TARGETS=copilot,claude,codex
```

Each list is a complete desired set. Explicitly selected unavailable CLIs or
unsupported transfer or publication capabilities fail configuration-apply
preflight. A saved selection for a temporarily unavailable CLI is retained and
reported as residual, matching the ownership behavior in
`remote-agent-stack`.

Scheduled execution does not repeat configuration-apply preflight. A malformed,
unavailable, or unsupported source is a source-local failure: its partial page
is discarded, its cursor does not advance, and healthy sources continue.
Shared queue, ledger, lock, or evidence-envelope corruption aborts the complete
run.

Source-specific path overrides are supported for tests and nonstandard
installations. Automatic discovery accepts only known owned roots and rejects
symlinks or roots that escape the configured user directory.

## Installer and trigger ownership

The Dreaming installer owns adapter configuration, source self-tests, queue
state, and launchd rendering.

`remote-agent-stack` may expose the three desired-set selections and delegate
to Dreaming. It must not parse vendor sessions, render Dreaming prompts, or
copy learned skills itself.

Managed triggers follow native surfaces:

- Copilot: the existing managed Dreaming instruction.
- Claude: a managed lifecycle-hook entry and an optional instruction that
  describes foreground proposal behavior.
- Codex: a managed lifecycle hook when a supported end event exists; otherwise
  no foreground trigger is required because scheduled discovery is complete.

Trigger installers use exact ownership markers or structured configuration
entries. Uninstall removes only unchanged or owned entries.

## Failure model

| Failure | Required behavior |
|---|---|
| Session store missing | Source reports unavailable; other sources continue |
| Unsupported source schema | Source fails closed and contributes no candidates |
| Truncated transcript | Review records bounded evidence; no completeness claim |
| Hook fires twice | Queue deduplicates by session revision |
| Hook is absent or interrupted | Scheduled discovery finds the session later |
| Source session changes | New revision is eligible; old revision remains recorded |
| Source changes during review | Review result is stale; no mutation; newest revision queued |
| Executor unavailable | No review mutation; queue entry remains retryable |
| Executor source boundary unavailable | No executor launch or mutation; queue entry remains retryable |
| Transfer route denied | Source is not rendered or sent to the executor |
| One publisher unavailable | Other targets reconcile; residual ownership remains |
| Foreign plugin collision | Target fails without overwrite |
| Halt switch active | Discovery may report health; no review or publication mutates |
| Adapter emits malformed JSON | Source page is discarded; source cursor stays; healthy sources continue |
| Source path escapes its root | Adapter rejects the session |

## Migration

1. Treat existing review-ledger session IDs as `source=copilot`.
2. Add source fields lazily and use the `legacy-reviewed` revision baseline
   contract before any re-review.
3. Keep the existing Copilot scorer and review path behavior unchanged behind
   the Copilot source adapter.
4. Introduce adapters with scheduled discovery before enabling hooks.
5. Enable Claude and Codex one source at a time after their fixture and live
   source checks pass.
6. Add learned-skill publication only after multi-source review is stable.
7. Keep Copilot as the sole executor until another executor passes the complete
   bounded-run and launchd self-test contract.

Disabling the feature removes managed hooks and target registrations, but
retains review ledgers, evidence envelopes, local skill history, and source
configuration for rollback and audit.

## Deterministic check contract

### Adapter conformance

For each source fixture:

- `doctor` accepts a supported store and rejects missing, escaped, symlinked,
  malformed, and unsupported-version stores.
- `list` emits stable source-qualified identities and comparable feature keys.
- `render` preserves order, source event IDs, and explicit truncation.
- repeated reads of unchanged input produce the same source revision.
- a durable appended event changes the revision.
- a rewrite, deletion, reorder, or adapter-version change is not classified as
  append-only.
- active, terminal, and quiet states exercise the shared completion gate.

Failure signal: nonzero exit with structured error and no candidate or event
output.

### Cross-source equivalence

Encode one semantically equivalent session in Copilot, Claude, and Codex fixture
formats.

Expected result:

- normalized event kinds and ordering are equivalent;
- shared scoring produces the same eligibility decision;
- source-qualified IDs remain distinct;
- source difference alone does not create independent task evidence.

Failure proves either adapter drift or source-specific policy leakage into the
shared pipeline.

### Discovery cursor

- more candidates than one page are drained without gaps;
- a page-limited generation keeps its floor and ceiling until exhaustion even
  when later scheduled runs observe a newer source high watermark;
- equal timestamps are ordered by native session ID;
- a crash after queue write but before cursor write repeats safely;
- a crash before complete page durability does not advance the cursor;
- source downtime followed by recovery replays the overlap window;
- malformed output from one source leaves its cursor unchanged while another
  source advances.
- a session observed as `active` remains in the unsettled index after the
  discovery cursor passes it and is queued after it becomes `quiet` without a
  new source event;
- an active session updated above a generation ceiling is reconsidered through
  the next generation without duplicating its unsettled entry.

### Queue and ledger

- duplicate hook delivery creates one queue item;
- a new source revision creates one replacement or successor item;
- an older queued revision is superseded by a newer one;
- a source change during review prevents mutation and queues the new revision;
- a failed executor leaves the item retryable;
- successful review records source, revision, executor, and terminal route;
- lazy migration maps an old unqualified entry to Copilot without losing
  fields;
- no ledger or envelope contains transcript text.
- an unchanged `legacy-reviewed` Copilot entry seeds a baseline without
  re-review; an updated one becomes eligible exactly once.

### Transfer policy

- a denied source-to-executor route never invokes `render`;
- route removal after queueing blocks executor launch;
- the Copilot review process cannot invoke `session_store_sql` or source
  adapters;
- the review invocation contains neither `--allow-all` nor
  `--allow-all-paths`, and its advertised tool inventory excludes native
  session tools;
- canary records in denied Copilot, Claude, and Codex session roots are
  unreadable from the review process even through generic shell commands;
- the public skills root is readable but not writable, its unchanged snapshot
  and check complete inside the boundary, and both-root umbrella discovery
  remains available;
- boundary setup failure prevents executor launch and mutation;
- redaction and byte limits apply before snapshot hashing;
- a field that cannot be safely separated fails the source item;
- run results contain route and policy identity but no transcript text.

### Task correlation

- an explicit opaque task key joins mirrored sessions across two sources;
- source difference alone never proves independence;
- heuristic correlation can reduce confidence but cannot create verified
  evidence;
- unrelated sessions in one repository scope remain distinct only when their
  producers provide distinct task keys.

### Trigger ownership

Fixture configurations prove install, update, interruption recovery, and
uninstall for Copilot instructions, Claude hooks, and any supported Codex hook.
Foreign entries and user edits are preserved.

### Publisher ownership

For each target CLI:

- first selection installs the exact generated local bundle;
- rerun updates only an owned registration;
- deselection removes only owned registrations;
- foreign same-name installations fail closed;
- unavailable saved targets remain residual;
- publication never exposes Dreaming orchestration skills.
- an unsupported CLI version is explicit residual or preflight failure;
- bundle inventory and content hash are verified after native registration.

### End-to-end scenarios

1. A Claude session is discovered by scheduled scan, reviewed by the Copilot
   executor, routed to discard, and ledgered without skill mutation.
2. A Codex session creates evidence for an existing local skill, commits the
   envelope update, and publishes a new read-only bundle to selected CLIs.
3. A Copilot foreground dispatch and a Claude hook contend for the writer
   lease; exactly one mutates and the other remains retryable.
4. One source has an unsupported schema while the other two contain eligible
   sessions; the run reports partial source coverage and reviews only readable
   evidence.
5. The halt switch appears after discovery and before review; no ledger,
   envelope, skill, or publication state changes.

## Delivery plan

### Milestone 1: source boundary

- Add source-qualified identities and lazy ledger migration.
- Extract shared scoring policy from the Copilot query.
- Add the source adapter command and fixture harness.
- Add completion admission, cursor checkpoints, immutable snapshots, and stale
  review rejection.
- Split Copilot extraction from review and enforce the source-blind executor
  boundary.
- Place existing Copilot behavior behind the adapter interface without changing
  observed results.

### Milestone 2: Claude and Codex ingestion

- Add strict native-record adapters.
- Add scheduled multi-source discovery and partial-coverage reporting.
- Add explicit source-to-executor transfer configuration and redaction.
- Review Claude and Codex evidence with the existing Copilot executor.
- Add source fields to evidence envelopes and reports.

### Milestone 3: native triggers

- Add bounded Claude hook enqueue.
- Add a Codex hook only when a supported session-end event is available.
- Extend installer ownership and self-test.
- Keep scheduled discovery as the complete backstop.

### Milestone 4: learned-skill publication

- Materialize one content-addressed local learned-skill bundle.
- Add target capability probes and enable only matrix rows that pass.
- Reconcile the bundle with supported selected installations.
- Keep Dreaming orchestration skills private to headless runs.

### Milestone 5: optional executor adapters

- Add Claude or Codex only when each can satisfy the complete bounded executor
  contract.
- Compare cost, reliability, and learning quality before changing the default.

## Definition of Done

- One Dreaming daemon safely reviews sessions from every selected source.
- The learning pipeline has no source-specific mutation policy.
- Source identity and revision survive queue, ledger, evidence, and reports.
- Unsupported or unreadable sources fail independently and visibly.
- Scheduled discovery recovers every supported session without requiring a
  hook.
- Learned local skills can be exposed to selected CLIs without mutable copies
  or orchestration-skill leakage.
- Existing Copilot behavior and state migrate without destructive rewriting.
- Every adapter, trigger, publisher, and executor path has deterministic
  ownership and failure tests.
- The complete live scenarios pass under launchd with the halt switch,
  rollback, and watchdog intact.
