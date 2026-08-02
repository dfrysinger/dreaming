# Dreaming

Dreaming is a fail-closed, macOS `launchd` workflow that reviews completed
Copilot sessions, rolls durable memories into skills, evaluates agent-created
skills, and periodically consolidates or archives them. It owns five
orchestration skills; reusable authoring and review dependencies remain in
[`dfrysinger/skills`](https://github.com/dfrysinger/skills).

## Prerequisites

- macOS with `launchctl`, Git, Python 3, and Bash
- GitHub Copilot CLI installed and authenticated
- a local skills root at `~/.copilot/skills` (a Git repository with no remote)
- GitHub CLI authentication for unattended public-catalog operations

## Operations

All lifecycle commands use the top-level installer:

```bash
scripts/install.sh prepare    # halt, back up, and boot out old jobs
scripts/install.sh install    # materialize dependencies and load halted jobs
scripts/install.sh selftest   # kickstart the loaded self-test job
scripts/install.sh enable     # enable only after the current self-test passes
scripts/install.sh status
scripts/install.sh uninstall
scripts/install.sh rollback [backup-directory]
```

`install` syncs `dfrysinger/dreaming` through Copilot CLI by default. Set
`DREAMING_SKIP_PLUGIN_SYNC=1` for a local checkout or test fixture.
It also atomically installs the repository-managed end-of-task triggers at
`${COPILOT_HOME:-$HOME/.copilot}/instructions/dreaming.instructions.md`. A tracked
SHA-256 prevents overwriting user edits; uninstall removes only the unchanged
copy Dreaming installed. Ownership is recorded under
`~/.copilot/skill-state/dreaming/managed-instructions.{sha256,target}`.

## Roots and state

- `DREAMING_REPO_ROOT`: this repository; defaults to its canonical location
- `DREAMING_SHARED_SKILLS_ROOT`: selected immutable dependency bundle
- `SKILLS_REPO_ROOT`: optional writable or read-only public skills catalog
- `SKILLS_LOCAL_ROOT`: mutable local skills; defaults to `~/.copilot/skills`
- `SKILLS_STATE_DIR`: durable daemon state; defaults to
  `~/.copilot/skill-state`
- `DREAMING_DEPS_DIR`: content-addressed bundles; defaults to
  `~/.copilot/dreaming/deps`
- `DREAMING_CONFIG_FILE`: selected-root configuration; defaults to
  `~/.copilot/dreaming/config.env`

Shared dependencies are exactly `writing-great-skills`, `dual-review`, and
`authenticated-browse`. Installation verifies a pinned file-hash receipt,
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
executable is verified.

This project derives its autonomous learning workflow from Nous Research's
[Hermes Agent](https://github.com/NousResearch/hermes-agent) and includes
notices in `skills/skill-review/references/NOTICE.md`. Licensed under the MIT
License.
