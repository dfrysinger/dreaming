# Multi-CLI Dreaming

## Objective

Let one Dreaming installation learn from completed Copilot CLI, Claude Code,
and Codex sessions while preserving one mutation owner, one evidence model,
and one reversible skill lifecycle.

Dreaming must install and operate when any nonempty subset of those CLIs is
available. Copilot, Claude, and Codex are peer adapters. None is a prerequisite
or privileged default.

The CLI that produced a session and the CLI that reviews it are independent
choices. Supporting a new session source must not require another Dreaming
daemon or another copy of its ledgers.

## User outcome

The owner can:

1. install Dreaming on a machine that has only Copilot, only Claude, only
   Codex, or any combination;
2. select which installed CLIs contribute sessions and which may perform
   reviews;
3. see the source and reviewing CLI on every reviewed-session record and
   evidence entry;
4. use the same routing, evaluation, promotion, curation, and rollback policy
   regardless of where a session originated;
5. disable one source or reviewer without disabling the others;
6. keep Dreaming's orchestration skills out of normal interactive context;
7. explicitly approve which session sources may be sent to which review
   executor;
8. make learned local skills available to selected CLIs from one canonical
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
- Require or generalize GitHub Copilot Memory handling. It remains an optional
  Copilot-only integration.
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
- `memory-curator` remains the only GitHub Copilot Memory deletion path when
  the Copilot integration is enabled. It is not part of the standalone core.
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
2. **Review executor**: runs the shared review contract through an installed
   CLI.
3. **Skill publisher**: exposes committed local skills to interactive CLIs.

Copilot, Claude, and Codex each implement all applicable interfaces. A source
does not require its matching executor, but every enabled source must have at
least one explicitly allowed, healthy executor route.

### Standalone core and storage

Dreaming owns its runtime and durable state outside every vendor directory:

```text
DREAMING_DATA_DIR=${XDG_DATA_HOME:-$HOME/.local/share}/dreaming
DREAMING_STATE_DIR=${XDG_STATE_HOME:-$HOME/.local/state}/dreaming
DREAMING_SKILLS_ROOT=$DREAMING_DATA_DIR/skills
```

`DREAMING_SKILLS_ROOT` is the single mutable Git repository for learned local
skills. Dreaming's orchestration skills and required shared dependencies are
installed into private, versioned bundles under `DREAMING_DATA_DIR`; they do
not depend on a user's Copilot, Claude, or Codex plugin installation.

An optional curated skills repository may be configured as a read-only search
and promotion source. It is not required for installation or review.

The scheduler, queue, review ledger, evidence envelopes, writer lease, halt
switch, watchdog, and ownership journal live under Dreaming's own roots.
Vendor directories contain only registrations, hooks, or source data owned by
that vendor adapter.

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

Dreaming compiles a review packet from its private copies of `skill-review`,
`skill-create`, `skill-manage`, the writing rubric, routing rules, and their
required references. The packet contains the instructions and exact script
paths needed for one pass. Executors do not resolve slash commands, user
skills, or vendor plugin registrations.

Dreaming also owns the independent draft-review gate. It renders the
`dual-review` protocol from the private dependency bundle and launches two
fresh reviewer invocations through configured executor adapters. A single-CLI
installation may use two isolated invocations of that CLI, preferring distinct
supported models when available. The evidence record states the executors and
models used; provider diversity improves confidence but is not a standalone
installation prerequisite.

Each executor implements:

```text
review-executor doctor
review-executor run --snapshot <path> --result <path> [--mode review|profile]
review-executor version
```

`doctor` proves authentication, headless invocation, private review-packet
injection, direct script access, tool restrictions, filesystem restrictions,
structured completion, and launchd execution. `run` receives:

- a source-qualified session reference;
- the immutable bounded normalized snapshot and digest;
- the unchanged routing and evidence rules;
- the Dreaming and shared skill roots;
- a required machine-readable completion sentinel.

The executor does not gain authority from being the source CLI. Any enabled
executor may review any source whose transfer route is explicitly allowed.
The resulting evidence entry records both `source` and `review_executor`.

The review boundary allows only the immutable snapshot, the Dreaming and shared
skill bundles, the canonical Dreaming skills root, an optional curated skills
repository as a read-only root, the private Dreaming state needed by the pass,
and the selected executor's required authentication material. Read-only
curated access preserves existing-skill discovery and unchanged-repository
guards without granting autonomous mutation authority.

The boundary denies every native session root, unrelated home-directory
content, and temporary-directory access. Generic shell access, when required
by existing skill-management scripts, remains inside the same filesystem
boundary and therefore cannot bypass source policy.

The process starts only when both the executor's tool restrictions and the
filesystem boundary are active. Failing to create either boundary leaves the
queue item retryable without review or mutation.

### Executor implementations

#### Copilot

The Copilot executor uses `copilot -p` with the compiled review packet and
explicit access to Dreaming's vendor-neutral scripts. It does not use
`--allow-all` or `--allow-all-paths`.
`--available-tools` or `--excluded-tools` removes `session_store_sql`,
source-adapter tools, and unrelated built-in tools before model execution.
`--allow-all-tools` may approve only the remaining bounded tool set.

Copilot source extraction is a separate read-only process. It may use
`session_store_sql`, has no mutation roots, and emits only a normalized
snapshot. The Copilot review process cannot invoke that extraction path.

#### Claude

The Claude executor uses `claude -p --safe-mode` with the compiled review
packet as its explicit system prompt, a bounded tool list, structured output
schema, maximum budget, disabled session persistence, and explicit access to
Dreaming's vendor-neutral scripts. Safe mode preserves normal Claude
authentication while disabling user and project instructions, skills, hooks,
MCP servers, memory, plugins, and other interactive customizations. The
executor does not require those disabled surfaces.

The surrounding operating system boundary remains required because Claude's
allowed-tool list controls model tools but does not replace filesystem
isolation for permitted shell commands.

#### Codex

The Codex executor uses `codex exec --ephemeral --ignore-user-config
--ignore-rules` with the compiled review packet as its prompt, command-line
configuration, an output schema, a JSON event stream, direct access to
Dreaming's vendor-neutral scripts, and bounded sandbox directories.

Dreaming launches it with an ephemeral `CODEX_HOME` that contains no
`AGENTS.md`, configuration, rules, skills, plugins, or session history. The
ephemeral home projects only the validated authentication material required by
the installed Codex version as a read-only reference to the owner's real
credential store; Dreaming does not copy credentials into durable state. A
dedicated working directory contains no project instructions. If `doctor`
cannot prove that the active authentication mode works through this minimal
projection, the Codex executor is unavailable rather than falling back to the
owner's full home.

No plugin or skill registration is required for the review process.

Codex's native sandbox is used when it proves the complete path contract.
Otherwise Dreaming supplies the same outer operating system boundary used by
the other executors.

### Executor selection

Configuration stores an ordered desired set, not a permanent default. For each
queue item Dreaming selects the first executor that:

1. is configured and installed;
2. passed its executor self-test;
3. has an allowed route from the item source;
4. can start its required filesystem boundary.

If an executor fails before mutation, Dreaming may try the next configured
executor only when that route is also explicitly allowed. It records every
attempt.

Executor attempts use the existing writer lease and transaction journal.
Fallback is allowed only before the journal records `mutation_started`. A
failure after that point must finish recovery or rollback before any executor
may retry the queue item. Dreaming never changes providers silently after an
executor has produced a mutation result.

### Source-to-executor transfer policy

Cross-vendor review is explicit because session evidence may contain private
code, prompts, tool output, and organization data.

Configuration contains a complete allowlist of source-to-executor routes:

```text
DREAMING_SOURCE_EXECUTOR_ALLOW="copilot>copilot,claude>claude,codex>codex"
```

Same-vendor transfer is not implied. A selected source without an allowed,
available executor route fails configuration preflight. The installer explains
that an allowed route sends bounded session evidence to the executor's model
provider. It proposes same-vendor routes for selected CLIs and requires a
separate explicit choice for every cross-vendor route.

Before snapshot persistence or transfer, adapters:

- remove known credential and environment-secret fields;
- omit unneeded source metadata and native file paths;
- enforce per-event, per-field, and total byte limits;
- preserve explicit redaction and truncation markers;
- reject source records whose sensitive fields cannot be separated safely.

Denied routes are never rendered. Transfer policy is checked before source
read, again before executor launch, and recorded by route name and policy
version in the private run result.

Every executor adapter satisfies the same contract:

- isolated noninteractive invocation;
- no native source-store tools or source-adapter commands;
- an operating system boundary that denies every native session root;
- explicit permission and network posture;
- the compiled review packet and vendor-neutral scripts supplied from
  Dreaming's private bundles without global interactive install;
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
  "review_executor": "claude"
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

`DREAMING_SKILLS_ROOT` is the single mutable source for agent-created local
skills. Dreaming does not maintain mutable copies under `~/.copilot`,
`~/.claude`, or `~/.codex`.

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
Symlink farms and mutable per-CLI source copies are not fallbacks. A target may
receive a read-only, content-addressed materialization only when its native
registration cannot consume the shared bundle directly; that materialization
is replaced atomically and is never edited in place.

The target capability matrix is versioned test data. Each row records the
minimum supported CLI version, registration operation, inventory proof,
removal operation, and unsupported result. A selected target without a proven
row fails explicit preflight or remains residual when restored from saved
configuration.

The initial matrix is:

| Target | Immutable registration | Inventory and verification | Removal | Initial status |
|---|---|---|---|---|
| Copilot CLI | Register the exact learned-skills directory with `copilot skill add <bundle-skills-directory>` | Run `copilot skill list --json` from an ordinary user launch and verify the custom directory, skill inventory, and bundle digest | Run `copilot skill remove <bundle-skills-directory>` for the exact owned registration | Supported for ordinary user-launched Copilot sessions through native custom-directory registration |
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
DREAMING_SESSION_SOURCES="<selected installed sources>"
DREAMING_REVIEW_EXECUTORS="<selected installed executors in owner order>"
DREAMING_SOURCE_EXECUTOR_ALLOW="<explicit selected routes>"
DREAMING_SKILL_TARGETS="<selected installed targets>"
DREAMING_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/dreaming"
DREAMING_STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/dreaming"
DREAMING_SKILLS_ROOT="$DREAMING_DATA_DIR/skills"
```

Each list is a complete desired set. The executor list is ordered. At least one
source, one executor, and one allowed route between them are required.

The installer writes only detected CLIs that the owner selects. It does not
seed absent CLIs into desired state. For example, a Claude-only installation
writes:

```text
DREAMING_SESSION_SOURCES=claude
DREAMING_REVIEW_EXECUTORS=claude
DREAMING_SOURCE_EXECUTOR_ALLOW="claude>claude"
DREAMING_SKILL_TARGETS=claude
```

Explicitly selected unavailable CLIs or unsupported transfer or publication
capabilities fail configuration-apply preflight. A saved selection for a
temporarily unavailable CLI is retained and reported as residual, matching the
ownership behavior in `remote-agent-stack`.

Scheduled execution does not repeat configuration-apply preflight. A malformed,
unavailable, or unsupported source is a source-local failure: its partial page
is discarded, its cursor does not advance, and healthy sources continue.
Shared queue, ledger, lock, or evidence-envelope corruption aborts the complete
run.

Source-specific path overrides are supported for tests and nonstandard
installations. Automatic discovery accepts only known owned roots and rejects
symlinks or roots that escape the configured user directory.

## Installer and trigger ownership

The Dreaming installer owns adapter configuration, source and executor
self-tests, neutral data and state roots, queue state, and launchd rendering.
It detects all three CLIs and succeeds when any selected source has at least
one selected, allowed, healthy executor. It never installs or requires an
unselected CLI.

Dreaming's required orchestration and shared-skill dependencies are always
materialized privately from their pinned sources. An identical user plugin may
serve as an input cache only when its verified files are copied into the
private versioned bundle. The review boundary never mounts or executes a user
plugin path.

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

The Copilot Memory curator is installed and scheduled only when that optional
integration is selected. Its absence does not reduce session learning,
evaluation, skill maintenance, or publication for Claude- or Codex-only
installations.

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
| Preferred executor unavailable | Next ordered executor may run only through an explicitly allowed route |
| No allowed executor available | No review mutation; queue entry remains retryable and health reports the missing route |
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
3. Stop autonomous mutation before relocating an existing
   `~/.copilot/skills` local Git repository.
4. Verify that repository is clean, preserve its Git history, publish and
   verify an immutable Copilot bundle through `copilot skill add`, prove an
   ordinary user-launched `copilot skill list --json` sees the learned skills,
   then atomically adopt the repository as `DREAMING_SKILLS_ROOT`. A failed
   publication or move leaves the old root and configuration active.
5. Move Dreaming queue, ledger, and scheduler state from vendor-scoped paths
   into the neutral Dreaming state root through an ownership-journaled
   transaction.
6. Keep existing Copilot discovery and review behavior behind independent
   Copilot source and executor adapters.
7. Enable each CLI adapter only after its fixture, headless executor, launchd,
   and live behavior checks pass.
8. Add cross-vendor routes only through an explicit configuration change.

Disabling the feature removes managed hooks and target registrations, but
retains review ledgers, evidence envelopes, local skill history, and source
configuration for rollback and audit.

LaunchAgent rollback and migration-data rollback are separate operations.
Normal rollback restores backed-up jobs and removes managed hooks without
deleting migrated audit data. Explicit migration rollback runs behind the halt
and deletes only unchanged targets that the ownership journal proves Dreaming
created; verification of every target completes before any deletion.

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

### Executor conformance

For Copilot, Claude, and Codex independently:

- `doctor` succeeds with that CLI installed and the other two binaries absent;
- the executor receives Dreaming's compiled review packet and can invoke only
  the permitted vendor-neutral scripts without a user plugin installation;
- native session tools and roots are unavailable to the review process;
- user and project instruction canaries are absent from the executor context;
- the executor emits the required structured result and completion sentinel;
- a timeout, authentication failure, malformed result, or missing boundary
  leaves the queue item retryable without mutation;
- the same normalized fixture produces the same routing and evidence decision,
  allowing only explicitly documented model-quality variance in prose.

The executor-preference checks prove selection order, allowed-route filtering,
fallback before mutation, recovery before retry after `mutation_started`, and
no provider switch after a mutation result.

The independent-review checks prove that a single-CLI installation can run two
fresh reviewer contexts, records their executor and model identities, and does
not require another vendor for skill creation or patching.

Codex-specific checks place canaries in the owner's `CODEX_HOME/AGENTS.md` and
the source project `AGENTS.md`, then prove neither reaches the executor while
the projected authentication still succeeds. Unsupported authentication
layouts fail `doctor` without copying credentials or exposing the full Codex
home.

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
- every review process excludes its own native session tools and all source
  adapters;
- each executor invocation uses its supported restrictive tool and sandbox
  controls rather than a vendor-wide unrestricted mode;
- canary records in denied Copilot, Claude, and Codex session roots are
  unreadable from the review process even through generic shell commands;
- an optional curated skills root is readable but not writable, its unchanged
  snapshot and check complete inside the boundary, and configured-root umbrella
  discovery remains available;
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

### Standalone installation matrix

Run install, self-test, discovery, review, publication, disable, re-enable, and
uninstall with these binary sets:

| Installed CLIs | Required result |
|---|---|
| Copilot only | Complete learning path with no Claude or Codex lookup |
| Claude only | Complete learning path with no Copilot or Codex lookup |
| Codex only | Complete learning path with no Copilot or Claude lookup |
| Each two-CLI pair | Same-vendor routes work; cross-vendor routes remain opt-in |
| All three | Ordered executor selection and independent source failure work |

Each single-CLI test runs with the other binaries removed from `PATH`, empty
vendor directories for the absent CLIs, and no copied authentication material
from them.

The installer-output assertion proves that each single-CLI configuration names
only that source, executor, route, and publication target; configuration apply
must pass without manual editing.

The matrix also runs without a configured curated skills repository. Review,
local skill creation, evaluation, and rollback must remain complete; only
curated-repository search and promotion are absent.

Dependency tests modify an installed user plugin after installation and prove
the private Dreaming bundle and review result remain unchanged.

### Live behavior validation

Deterministic fixtures and fake CLI binaries remain the primary regression
suite. Live acceptance uses `tuistory` to drive the real interactive CLIs and
`behavior-validation` to design, execute, preserve, and independently judge
observable scenarios.

High-value live scenarios include:

- first-run authentication and workspace-trust screens are detected and
  reported as setup requirements rather than successful executor runs;
- an authenticated headless executor completes without interactive prompts;
- private Dreaming instructions load while user and project instructions do
  not;
- a denied tool, source root, or publication target remains inaccessible;
- interruption, timeout, and relaunch leave the queue and ownership journal
  recoverable;
- interactive publication makes only learned skills visible and never exposes
  Dreaming's orchestration skills.

Behavior-validation artifacts live under the session run directory. Each
scenario gets an isolated home, state root, data root, working directory, and
`tuistory` relay. Harness or authentication failures are recorded as
infrastructure failures, never as proof that Dreaming behavior passed or
failed.

### End-to-end scenarios

1. A Claude-only installation discovers a Claude session, reviews it with
   Claude, routes it to discard, and records it without skill mutation.
2. A Codex-only installation creates evidence for an existing local skill,
   commits the envelope update, and publishes a read-only Codex bundle.
3. A Copilot-only installation completes discovery, review, mutation, and
   publication with no Claude or Codex paths present.
4. A Copilot foreground dispatch and a Claude hook contend for the writer
   lease; exactly one mutates and the other remains retryable.
5. One source has an unsupported schema while the other two contain eligible
   sessions; the run reports partial source coverage and reviews only readable
   evidence.
6. A Claude source is reviewed by Codex only after the owner enables
   `claude>codex`; disabling that route prevents rendering and execution.
7. The preferred executor fails before mutation and the next allowed executor
   completes the same queue item.
8. The halt switch appears after discovery and before review; no ledger,
   envelope, skill, or publication state changes.

## Delivery plan

### Milestone 1: standalone core

- Move defaults and ownership into neutral Dreaming data, state, and skill
  roots.
- Refactor skill creation, management, curation, and repository guards so the
  curated repository is optional and read-only when present.
- Add source-qualified identities, lazy ledger migration, adapter interfaces,
  ordered executor selection, and fixture CLIs.
- Add completion admission, durable scan generations, immutable snapshots,
  stale-review rejection, transfer policy, and the source-blind executor
  boundary.
- Prove the installer and scheduler without any real vendor CLI by using fake
  adapters.

#### Milestone 1 Definition of Done

- Dreaming's configurable data, state, queue, ledger, snapshot, and learned
  skill roots are vendor-neutral, while the existing Copilot runtime remains
  compatible until its adapter migration in Milestone 2.
- Versioned session-source, review-executor, and skill-publisher interfaces
  exist with deterministic fake adapters and structured failure behavior.
- Source-qualified identity, completion admission, durable scan generations,
  the unsettled-session index, immutable bounded snapshots, stale-result
  rejection, explicit transfer routes, and ordered pre-mutation executor
  fallback are implemented in shared code.
- Existing ledger records have a tested lazy-migration path that cannot silently
  re-review or suppress an ambiguous legacy session.
- Installation, self-test, scheduled discovery, review routing, disable,
  re-enable, rollback, and uninstall complete with fake adapters while all real
  Copilot, Claude, and Codex binaries and vendor homes are absent.
- Deterministic checks cover the Milestone 1 portions of the design's adapter,
  discovery, queue, ledger, transfer-policy, standalone-installation, and
  failure contracts.
- A launchd live-proof run demonstrates the fake-adapter standalone path on the
  reviewed tree, and paired implementation review has no material finding.

### Milestone 2: Copilot standalone adapter

- Place existing Copilot discovery behind the source interface.
- Split Copilot extraction from source-blind review.
- Migrate existing Copilot-scoped skill and state roots transactionally.
- Prove the complete Copilot-only installation matrix.

This milestone is first because it reuses the shipped implementation, not
because Copilot owns the architecture.

#### Milestone 2 Definition of Done

- Native Copilot sessions pass strict source discovery, inspection, bounded
  rendering, revision, scoring-feature, and completion admission checks.
- Copilot review runs headlessly without source tools, custom instructions, or
  native session paths in the review packet.
- A clean Copilot-scoped skills repository and supported Dreaming state move
  through a verified, ownership-journaled, reversible neutral-root migration.
- One immutable learned-skill bundle registers through `copilot skill`, verifies
  exact inventory, and removes only its owned directory registration.
- The Copilot-only desired-set configuration completes install, self-test,
  discovery, review, publication, disable, re-enable, rollback, and uninstall
  without Claude or Codex paths.

### Milestone 3: Claude standalone adapter

- Add strict Claude session reading and scoring.
- Add the bounded Claude headless executor.
- Add Claude publication and optional enqueue hook.
- Prove the complete Claude-only installation matrix, including trust and
  authentication setup behavior.

#### Milestone 3 Definition of Done

- Native Claude project sessions pass strict source and normalized-event
  conformance, including quiet completion and scheduled recovery.
- Claude review uses noninteractive safe mode with skills, hooks, user
  instructions, project instructions, and tools disabled.
- An optional Claude lifecycle hook can append one idempotent session hint;
  scheduled discovery remains complete without it.
- Claude publication verifies the exact immutable local marketplace plugin and
  removes only Dreaming-owned plugin and marketplace entries.
- The Claude-only desired-set path reports expired or missing authentication as
  setup required rather than a successful review.

### Milestone 4: Codex standalone adapter

- Add strict Codex session reading and scoring.
- Add the bounded Codex headless executor.
- Add Codex publication and a hook only when a supported end event exists.
- Prove the complete Codex-only installation matrix, including authentication
  setup behavior.

#### Milestone 4 Definition of Done

- Codex reads the versioned thread catalog and rollout path through a read-only
  database connection, rejects escaped paths, and normalizes supported rollout
  events.
- Codex review is ephemeral, ignores user configuration and project rules, uses
  a read-only sandbox, and emits a schema-validated result.
- A Codex lifecycle hook is configured only where a supported end event can
  provide a native session ID; scheduled discovery remains complete otherwise.
- Codex publication verifies the exact immutable local marketplace plugin and
  removes only Dreaming-owned plugin and marketplace entries.
- The Codex-only desired-set path reports missing authentication as setup
  required rather than a successful review.

### Milestone 5: combined operation

- Add explicit cross-vendor routes and ordered executor fallback.
- Add scheduled multi-source discovery and partial-coverage reporting.
- Reconcile one content-addressed learned-skill bundle with every selected
  target.
- Run the two-CLI and three-CLI deterministic and live behavior matrices.
- Keep scheduled discovery as the complete backstop for every source.

#### Milestone 5 Definition of Done

- Complete desired-set configuration supports every nonempty single-, two-, and
  three-CLI selection without naming an absent CLI.
- Explicit cross-vendor routes, ordered pre-mutation fallback, route removal,
  and source-local failure preserve healthy source progress.
- One content-addressed bundle reconciles independently to every selected
  publication target while retaining residual ownership for unavailable
  targets.
- Combined scheduled discovery, queue deduplication, source-qualified ledgers,
  halt behavior, rollback, and uninstall pass on the same reviewed tree.

## Definition of Done

- One Dreaming daemon safely reviews sessions from every selected source.
- Dreaming installs and completes its full learning path with only Copilot,
  only Claude, or only Codex installed.
- No vendor binary, home directory, authentication state, memory service, or
  plugin installation is assumed by the standalone core.
- The learning pipeline has no source-specific mutation policy.
- Source identity and revision survive queue, ledger, evidence, and reports.
- Unsupported or unreadable sources fail independently and visibly.
- Scheduled discovery recovers every supported session without requiring a
  hook.
- Learned local skills can be exposed to selected CLIs without mutable copies
  or orchestration-skill leakage.
- Existing Copilot behavior and state migrate into neutral ownership without
  destructive rewriting.
- Every adapter, trigger, publisher, and executor path has deterministic
  ownership and failure tests.
- Live `tuistory` scenarios are preserved and independently judged through
  `behavior-validation`; harness failures cannot become successful evidence.
- The complete live scenarios pass under launchd with the halt switch,
  rollback, and watchdog intact.
