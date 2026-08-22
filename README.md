# Dreaming

Dreaming is a fail-closed, macOS `launchd` workflow that reviews completed
Copilot CLI, Claude Code, and Codex sessions, turns reusable work into local
skills, evaluates agent-created skills, and periodically consolidates or
archives them. It owns five
orchestration skills; reusable authoring and review dependencies remain in
[`dfrysinger/skills`](https://github.com/dfrysinger/skills).

## Prerequisites

- macOS with `launchctl`, Git, Python 3, and Bash
- at least one selected CLI: Copilot CLI, Claude Code, or Codex
- authentication for each selected review executor
- a neutral local skills root, created automatically as a Git repository with
  no remote
- optional GitHub CLI authentication for unattended public-catalog operations

## Operations

All lifecycle commands use the top-level installer:

```bash
scripts/install.sh prepare    # halt, back up, and boot out old jobs
scripts/install.sh install    # materialize dependencies and load halted jobs
scripts/install.sh selftest   # kickstart the loaded self-test job
scripts/install.sh enable     # enable only after the current self-test passes
scripts/install.sh status
scripts/install.sh dashboard-url   # print the private fragment-token URL
scripts/install.sh dashboard-open  # open the dashboard in the default browser
scripts/install.sh uninstall
scripts/install.sh rollback [backup-directory]  # restore backed-up jobs/hooks
scripts/install.sh rollback-migration           # separately remove unchanged migrated copies
```

The compatibility installation syncs `dfrysinger/dreaming` through Copilot CLI
and atomically installs the repository-managed Dreaming instructions at
`${COPILOT_HOME:-$HOME/.copilot}/instructions/dreaming.instructions.md`. A tracked
SHA-256 prevents overwriting user edits; uninstall removes only the unchanged
copy Dreaming installed. Ownership is recorded under
`~/.copilot/skill-state/dreaming/managed-instructions.{sha256,target}`.

For standalone or multi-CLI operation, select complete desired sets and disable
the Copilot compatibility layout:

```bash
export DREAMING_ENABLE_COPILOT_COMPAT=0
export DREAMING_SESSION_SOURCES="copilot claude codex"
export DREAMING_REVIEW_EXECUTORS="copilot claude codex"
export DREAMING_SOURCE_EXECUTOR_ALLOW="copilot>copilot claude>claude codex>codex"
export DREAMING_SKILL_TARGETS="copilot claude codex"
scripts/install.sh install
scripts/install.sh selftest
scripts/install.sh enable
```

Lists contain only selected CLIs. Executor order is significant. Cross-vendor
review is disabled unless its `source>executor` route is explicitly present.
Scheduled discovery remains the complete path when no lifecycle hook is
configured. A supported Claude or Codex hook can call
`scripts/dreaming-enqueue.sh <vendor> <native-session-id>` as a bounded hint.

## Roots and state

- `DREAMING_REPO_ROOT`: this repository; defaults to its canonical location
- `DREAMING_SHARED_SKILLS_ROOT`: selected immutable dependency bundle
- `SKILLS_REPO_ROOT`: optional writable or read-only public skills catalog
- `DREAMING_DATA_DIR`: private bundles and snapshots; defaults to
  `${XDG_DATA_HOME:-$HOME/.local/share}/dreaming`
- `DREAMING_STATE_DIR`: queue, ledger, ownership, and scheduler state; defaults
  to `${XDG_STATE_HOME:-$HOME/.local/state}/dreaming`
- `DREAMING_SKILLS_ROOT`: canonical mutable learned skills; defaults to
  `$DREAMING_DATA_DIR/skills`
- `DREAMING_ADAPTER_CONFIG`: generated complete desired-set configuration
- `DREAMING_DEPS_DIR`: content-addressed shared dependency bundles
- `DREAMING_CONFIG_FILE`: persisted selected-root configuration
- `DREAMING_DASHBOARD_PORT`: private dashboard port; defaults to `47673`
- `DREAMING_DASHBOARD_TOKEN_FILE`: protected dashboard bearer token

The dashboard binds only to `127.0.0.1`. Its API requires the generated
mode-`0600` bearer token; the browser receives the token through a URL fragment
and removes it from the address after exact-origin session bootstrap.

Set `DREAMING_MIGRATE_COPILOT=1` during installation to copy a clean existing
Copilot-scoped skills repository and supported Dreaming state into neutral
roots. The migration verifies Git history and file hashes, records ownership in
`$DREAMING_STATE_DIR/copilot-migration.json`, retains the source for rollback,
and refuses conflicting neutral data. Interrupted verified activation resumes
from that journal. Normal rollback and uninstall retain migrated ledgers,
evidence, skill history, and source configuration for audit. The explicit
`rollback-migration` command activates the halt and removes only unchanged
targets that the migration journal proves Dreaming created; any changed or
missing target stops before deletion.

Shared dependencies are exactly `skill-create`, `writing-great-skills`,
`dual-review`, and `authenticated-browse`. Installation verifies a pinned file-hash receipt,
copies only those directories, verifies the copy, and atomically selects the
bundle. Compatible local checkouts or installed plugins avoid a network fetch;
incompatible automatic candidates fall through to the pinned sparse checkout.
An explicit source override remains strict. Source checkouts and installed
plugin caches are never executed.

## Safety and rollback

Dreaming preserves the existing writer lease, provenance, evaluation, deletion,
transaction, and tombstone policies. Missing roots, malformed durable owners,
dependency skew, ambiguous public/local skills, or root aliasing stop before
mutation. Installation and rollback leave the halt switch active; `enable`
requires a successful self-test for the current activation generation.
Rollback restores exact backed-up plist bytes only after every referenced
executable is verified. Uninstall and rollback continue removing or restoring
managed jobs and hooks when publication cleanup is unavailable, while reporting
the unremoved CLI registrations as residuals.

This project derives its autonomous learning workflow from Nous Research's
[Hermes Agent](https://github.com/NousResearch/hermes-agent) and includes
notices in `skills/skill-review/references/NOTICE.md`. Licensed under the MIT
License.
