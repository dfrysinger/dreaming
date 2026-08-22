---
name: skill-curator
description: Periodic self-learning curator that reviews dfrysinger/skills, consolidates narrow siblings into umbrella skills, and archives unused ones. Default dry-run. Use when invoked by the dreaming orchestrator, when the user says "curate skills" / "clean up skills" / "skill cleanup", or after noticing many narrow agent-created skills.
hand-invoked: false
---

> **Paths note:** Dreaming-owned code resolves from `$DREAMING_REPO_ROOT`;
> shared dependencies resolve from `$DREAMING_SHARED_SKILLS_ROOT`.
> `$SKILLS_REPO_ROOT` remains the optional public catalog, while
> `$SKILLS_LOCAL_ROOT` and `$SKILLS_STATE_DIR` retain their existing meanings.


# skill-curator

A direct port of Hermes Agent's `agent/curator.py` background skill-maintenance system, adapted to Copilot CLI. The umbrella-building consolidation prompt (`references/curator-prompt.md`) is lifted **verbatim** from upstream — same hard rules, same dry-run banner, same structured YAML output.

This skill does NOT touch:
- Skills installed from other plugins (builtin marketplace skills) — only the two skill roots managed by this system are in scope: PUBLIC `~/code/skills/skills/` (curated/recommend-only for hand-made content) and LOCAL `~/.copilot/skills/` (agent-managed, mutable).
- Pinned skills (those with a `.pinned` file in their dir).

## When to use

- Invoked as the prune pass of the effective-weekly dreaming job.
- User asks for skill cleanup / consolidation.
- After several `/skill-create` invocations in a short period — sibling clustering becomes likely.

## Prerequisites

- macOS, `git`, `jq`, `bash`.
- Both skill roots are accessible:
  - PUBLIC repo `~/code/skills/` (clone of `dfrysinger/skills`, pushable).
  - LOCAL native `~/.copilot/skills/` (local git repo, no remote).
- Curator state file lives at `~/.copilot/skill-state/curator.json` (created on first run).
- Usage data comes from Copilot CLI's cloud session store via `session_store_sql` (no local telemetry needed; usage is recorded every time the `skill` tool fires).

## Quick start

```bash
# Status: usage report + curator state
/skill-curator status

# Tick (foreground/legacy gate): runs --dry-run only if >= interval_hours since
# last run; otherwise silent skip. Scheduled cadence belongs to dreaming.
/skill-curator tick

# Dry-run consolidation pass (default — produces a report, does not mutate)
/skill-curator
/skill-curator --dry-run

# Live consolidation pass (mutates skill files + git commits)
/skill-curator --live

# Unattended Mac mini review of the governed MacBook estate
/skill-curator scheduled-live
```

## Procedure

### Mode: `tick` (scheduled gate)

This mode remains available for foreground and legacy callers. The dreaming
orchestrator bypasses it because one shared cadence governs all three passes.

1. Run `scripts/should-run-now.sh`. It returns exit 0 if `≥ interval_hours` (default 168h = 7d) have passed since `last_run_at`, OR exit 1 (skip) otherwise.
2. **First-run behavior**: when `last_run_at` is null, seed it to now and skip. The first real dry-run happens after one full interval.
3. If the gate passes, **automatically continue into `--dry-run` mode** (procedure below). If the gate skips, exit silently with a one-line status.

### Mode: `status`

1. Read curator state: `scripts/curator-state.sh read` → JSON with `last_run_at`, `run_count`, `paused`, `last_run_summary`.
2. Build usage report via `session_store_sql`:
   ```sql
   -- See scripts/skill-usage-report.sh for the canonical query.
   -- Returns: skill_name, use_count, last_used_at, days_since_use, state
   ```
3. Apply the lifecycle thresholds (from `references/hermes-curator-config.md`):
   - active: used in last 30 days OR new (no activity yet)
   - stale: unused 30-90 days
   - archive-eligible: unused > 90 days
   - completed-project candidate: agent-created, idle for at least
     `config_overrides.completed_project_cooldown_days` (default 14), tied to a
     demonstrably finished bounded project, and not reusable or suitable for
     consolidation
   - pinned: ignored regardless
4. Run `scripts/scheduled-skill-deps.py --inventory` before rendering the
   table. Enumeration must complete successfully; malformed durable config,
   missing explicit managed paths, or ambiguous live skill names abort the
   run. Render `name | root | use_count | last_used | state | pinned |
   implicit_pin`. Treat either pin column as protected and include the
   `implicit_pin_sources` in the report evidence.

### Mode: `--dry-run` (default)

1. **Print the DRY-RUN BANNER verbatim** (`references/dry-run-banner.md`). This is a hard guard: the prompt below tells you to refuse all mutating actions.
2. Load the **CURATOR_REVIEW_PROMPT** (`references/curator-prompt.md`) and follow it as your operating instructions for the rest of this run.
3. Gather inputs:
   - List of all skills across BOTH roots: `~/code/skills/skills/**/SKILL.md` and `~/.copilot/skills/**/SKILL.md`. `scripts/list-clusters.sh` already scans both.
   - Usage report from step 2 above.
   - Pinned set (skills with `.pinned`).
   - Complete durable dependency inventory from
     `scripts/scheduled-skill-deps.py --inventory`. Any non-zero result aborts
     the dry-run instead of producing a mutation-capable report from an
     incomplete inventory.
4. Identify **prefix clusters** (skills sharing a first word or domain keyword — `pr-*`, `gh-*`, etc.). A mature library tends toward 10-25 clusters; smaller ones have only a few.
5. For each cluster with 2+ members, decide: **merge into existing umbrella**, **create new umbrella**, or **demote to references/templates/scripts** of an existing skill. The curator prompt has the full decision tree.
6. Evaluate the completed-project lane for agent-created skills even when they
   are younger than 90 days. Read
   `config_overrides.completed_project_cooldown_days` from `curator.json`
   (default 14), then require explicit evidence that the bounded project ended,
   the configured period since both creation and last use, and no reusable
   procedure or umbrella destination. Uncertainty means keep, not prune.
7. Mark every scheduled dependency as `implicit_pin=yes` in the report table
   and exclude it from consolidations and prunings just like `.pinned`.
8. **Do NOT mutate.** Produce a report that describes what you WOULD do.
9. The report must end with the structured YAML block (see prompt for exact shape):
   ```yaml
   consolidations:
     - from: <old-skill-name>
       into: <umbrella-skill-name>
       reason: <one sentence>
   prunings:
     - name: <skill-name>
       reason: <one sentence — why archive with no merge target>
   ```
10. Save the report to `~/.copilot/skill-state/reports/{YYYYMMDD-HHMMSS}-curator-report.md` and update `curator.json` with `last_run_at`, `run_count++`, `last_report_path`.

### Mode: `--live`

1. Honour the pause flag first: `scripts/curator-state.sh read` → if `paused`
   is true, abort and report it. `--live` is the destructive mode, so a pause
   set to stop the curator must stop it here above all. (`should-run-now.sh`
   checks this on the scheduled path, but `--live` is invoked directly and
   never passes through it.)
2. Honour the shared halt switch
   `~/.copilot/skill-state/skill-review/disable-daemon` before any mutation.
   It is the global stop for autonomous and live maintenance paths.
3. Re-read the most recent dry-run report. If none exists or it's >7 days old,
   abort and tell the user to run dry-run first.
4. Separate autonomous entries from manual-only recommendations. Agent-created,
   unpinned sources and agent-created destinations may proceed without
   confirmation. Any hand-made, plugin-provided, pinned, malformed, or
   uncertain entry remains report-only.
5. Re-check the shared halt switch and curator pause immediately before
   transaction creation and every mutation intent.
6. Build the complete ordered transaction plan described in
   `references/live-run-transactions.md`, then call `scripts/curator-run.py
   begin --autonomous` **before the first edit**. This writes an immutable
   authorization receipt, freezes dependency decisions, records both root
   identities and unrelated dirty paths, and acquires the shared writer lease.
7. For each destination patch or umbrella create, call `curator-run.py intent`
   before editing. For a create, first run
   `skill-review/scripts/check-tombstone.sh <destination>` and stop when a
   tombstone exists; declare `SKILL.md`, `.agent-created`, and
   `.agent-created.json` in the intent. Then:
   - Use shared `/skill-create` to author a new umbrella.
   - For a new umbrella, run
     `skill-review/scripts/mark-agent-created.sh <destination> <session-id>
     curator-live --task-key <authorized-task-key> --independence verified
     --evidence-kind curator-consolidation --summary <privacy-safe-summary>
     --routing-reason <report-reason> --created-by skill-curator` before
     validation. The transaction's parent writer lease remains authoritative.
   - Use `/skill-manage patch` to add the absorbed content as a labeled section in `<into>/SKILL.md` (or as a `references/<from>.md` file if it's session-specific detail).
   - If `<from>` has support files (`references/`, `templates/`, `scripts/`, `assets/`), re-home them into `<into>`'s matching subdirs and rewrite the destination paths in `<into>/SKILL.md`. Never leave dangling references.
   - Author distinct source and sibling cases for the changed umbrella and run
     `skill-review/scripts/run-skill-evaluation.sh <into-dir> --model
     <exact-model>`. Continue only when `skill-evaluation.py gate <into-dir>`
     passes.
   - Run `writing-great-skills` and `dual-review` from the verified
     `$DREAMING_SHARED_SKILLS_ROOT`.
   - Commit only through `curator-run.py commit`; it scopes the commit to the
     declared paths and records exact ledger/state effects.
8. Archive each approved source with `SKILLS_CURATOR_RUN_ID=<run-id>`:
   - Consolidation: `/skill-manage archive <from> --absorbed-into <into>`.
   - Pruning: `/skill-manage archive <name>` with no replacement.
   `archive-skill.sh` records intent before `git rm` and completion only after
   the commit, retirement record, and tombstone are durable.
9. Commit messages for patch/create operations use:
   ```
   skill-curator: consolidate <from> into <into>

   Auto-generated by /skill-curator --live based on dry-run report
   ~/.copilot/skill-state/reports/<timestamp>-curator-report.md.
   Reason: <one-sentence reason from the YAML block>.

   Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
   ```
10. If the transaction changed the PUBLIC root, call `curator-run.py publish`
    while the writer lease is still held. A rejected or mismatched push leaves
    the run in `publish_failed` for rollback or supervised recovery.
11. Call `curator-run.py finish` only when every planned operation is complete
    and any public publication is verified. On any failure, call
    `curator-run.py rollback`; do not hand-repair one root. Rollback of a
    published run pushes a normal revert rather than rewriting history.
12. Update curator state: `last_run_at`, `run_count++`, `last_run_summary` = "consolidated N, pruned M".

### Mode: `scheduled-live`

This is the unattended Mac mini estate-governance path. The user's standing
authorization applies only to machine-authorized targets that pass every
current evidence gate. It removes any interactive confirmation; it does not
weaken provenance, dependency, evaluation, halt, recovery, or restore
requirements.

1. Refresh the receiver-bound MacBook census with `dreaming-core.py census`.
   Require complete bounded scope and zero unresolved mappings.
   Before refreshing, acquire the scheduled review lease with
   `curator-run.py estate-session-begin`. Use `estate-session-renew` during
   long reviews and always release it with `estate-session-finish`; do not
   create a personal-skill transaction solely to hold this lease.
2. Run `scheduled-skill-deps.py --inventory`; any ambiguity aborts the pass.
3. Review every current personal capability and plugin package under
   `docs/unified-skill-estate-governance-design.md`.
4. Save the full report before mutation. Persist every keep, protected,
   unknown, or recommendation-only result with
   `scripts/estate-recommendation.py` so the dashboard shows the whole review,
   not only executed actions.
5. For eligible `legacy_machine` personal skills, dispatch the sealed
   `personal_archive` or `personal_restore` estate action to the MacBook.
   Never mutate the mini's mirror as a substitute for the receiver action.
6. For eligible plugins, evaluate the complete package capability gate and
   dispatch only a source-qualified `plugin_disable` or receipt-bound
   `plugin_restore` action. Never edit or uninstall plugin contents.
7. Use only `curator-run.py estate-authorize`, `estate-verify`, and
   `estate-dispatch` for remote estate mutations. Recollect the census after
   each commit and stop on stale, ambiguous, rolled-back, or recovery-required
   state.
8. Protected and unknown-provenance targets remain recommendation-only
   regardless of the report's prose.

## Hard rules (non-negotiable)

1. **Only touch skills in the two managed roots** (`~/code/skills/skills/` and `~/.copilot/skills/`). Other plugins' skills are off-limits.
2. **Never delete by hand.** Archive via `archive-skill.sh` is the maximum destructive action; it keeps the skill recoverable from git history.
3. **Never touch a pinned skill.** This includes both an explicit `.pinned`
   file and an `implicit_pin` discovered from installed jobs and their
   recursively referenced durable prompts/scripts. Pin = "preserve this", and
   it bypasses every transition. Incomplete dependency enumeration fails
   closed.
4. **`use_count == 0` is not evidence of low value.** Usage telemetry is new and sparse. Judge consolidation on CONTENT, not on counters.
5. **Ninety days is a fallback, not a minimum.** An agent-created skill from a
   completed bounded project may be proposed after
   `config_overrides.completed_project_cooldown_days` (default 14), but only
   with explicit completion evidence and no reusable or mergeable content.
6. **Pairwise distinctness is not the bar.** The bar is: "would a human maintainer write this as N separate skills, or as one skill with N labeled subsections?" Lean toward umbrella.
7. **`keep` is legitimate only when the skill is already a class-level umbrella.** "Narrow but distinct" is a reason to demote, not a reason to keep.
8. **Tiered authority by provenance.** A skill is *agent-created* when its directory contains a `.agent-created` marker (written by skill-review). Authority differs by tier:
   - **Agent-created skills** are within the curator's autonomous authority. Dry-run may place them in `consolidations:` / `prunings:`, and `--live` or `scheduled-live` may archive or absorb them when machine authorization passes. Archiving an agent-created skill writes a tombstone (via `archive-skill.sh`) so skill-review will not recreate it.
   - **Hand-made skills** (no `.agent-created` marker) are **recommend-only**. The curator may surface them in a separate `manual_review:` list with a rationale, but MUST NOT put them in `consolidations:` / `prunings:` or mutate them automatically. A hand-made destination also remains manual.


9. **A merged umbrella is a new draft.** Whenever `--live` absorbs one skill
   into another, the resulting SKILL.md must meet `writing-great-skills` from
   `$DREAMING_SHARED_SKILLS_ROOT`, pass the source/sibling effect gate, and go
   through `dual-review`
   before the commit. `archive-skill.sh --absorbed-into` machine-enforces the
   current destination receipt. Merging is where duplication and sprawl enter
   the library, and no human reads the diff.

## Pitfalls

- **Skipping the dry-run banner.** The banner is what gates mutation. If you start the curator prompt without printing the banner, the model may interpret instructions like "use `skill_manage(action=patch)`" as live ops. Always print the banner first in dry-run mode.
- **Producing a "report" with no structured YAML.** Downstream `--live` mode parses the YAML to know what to execute. A free-text-only report is unusable.
- **Live mode without an authorized dry-run.** Always refuse: the only
  sanctioned autonomous path is a fresh content-addressed report plus
  machine-verified provenance, roots, pins, dependencies, halt, and pause.
- **Forgetting Package integrity.** If you flatten `<from>/SKILL.md` into a references file but leave `<from>/scripts/foo.sh` behind, the absorbed content has dangling links. Re-home support files OR archive `<from>` whole.

## References

- `references/curator-prompt.md` — the verbatim CURATOR_REVIEW_PROMPT (lifted from Hermes `agent/curator.py`).
- `references/dry-run-banner.md` — verbatim CURATOR_DRY_RUN_BANNER.
- `references/hermes-curator-config.md` — defaults: 7-day interval, 30-day stale, 90-day archive, plus how to override.

## Scripts

- `scripts/skill-usage-report.sh` — query `session_store_sql` for skill use_count + last_used_at.
- `scripts/curator-state.sh` — read/write `~/.copilot/skill-state/curator.json`.
- `scripts/list-clusters.sh` — group skills by name prefix to surface candidate clusters.
- `scripts/scheduled-skill-deps.py --inventory` — enumerate scheduled
  dependencies and emit report-ready `pinned` / `implicit_pin` rows.
- `scripts/curator-run.py` — begin/intent/commit/finish/rollback transaction
  boundary for live runs.
- `scripts/curator-report.sh` — write a dry-run report to `~/.copilot/skill-state/reports/`.

## Verification

After `--dry-run`:
- A new report exists under `~/.copilot/skill-state/reports/{ts}-curator-report.md`.
- The report contains the structured YAML block with `consolidations:` and `prunings:` lists (may be empty).
- `curator.json` shows `last_run_at` = now, `run_count` incremented.
- **No git changes** to either root (verify with `git -C ~/code/skills status` and `git -C ~/.copilot/skills status` — both pristine).

After `--live`:
- All `consolidations[].from` skills are gone from their owning root's tree, each with a retirement record.
- All `consolidations[].into` skills have updated `SKILL.md` referencing absorbed content.
- All `prunings[].name` skills are gone from their owning root's tree, without `absorbed_into` in their commit message.
- Each mutation has its own commit in its owning root.
- The curator run manifest is `status=complete`; failed runs are
  `status=rolled_back` with every operation reversed.
- Public commits were published by the transaction before completion, with
  exact prior/new/remote identities recorded. Local commits stay local.
