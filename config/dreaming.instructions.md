# Dreaming skill-learning triggers

These instructions add end-of-task learning behavior without changing the
user's main `copilot-instructions.md`.

## Tier 1 — foreground proposal

After a task, propose `/skill-create` only when all of these are true:

1. the work produced a reusable procedure with at least three non-obvious steps;
2. the procedure is likely to be useful again;
3. it is stable rather than tied to one branch, incident, or temporary setup;
4. no skill in the dreaming, shared, managed public, or local roots already
   covers it.

Ask before creating a skill in the foreground. Offer one concise proposal; do
not create it unilaterally.

## Tier 2 — autonomous end-of-task review

At the end of a qualifying heavy task, dispatch `skill-review` as an
end-of-task subagent in `dispatch` mode without asking when both are true:

1. the task required roughly ten or more substantive tool calls; and
2. it produced a reusable procedure with at least three steps.

Skip dispatch when the task is not heavy, the learning is not reusable, or an
equivalent skill already exists. The review workflow is git-reversible,
provenance-marked, ledger-deduplicated, and tombstone-guarded.

## Patch trigger

When using a skill reveals a missing or incorrect reusable step, propose
`/skill-manage patch <name>` with the specific change. Ask before patching in
the foreground.

## Memory curation

Copilot Memory uses relevance-based retrieval and citation validation; memories
do **not** all load every turn. Use `memory-curator` when the user asks to
curate memories, when retrieved memories are stale or duplicated, or when the
memory list has grown large. The installed weekly job is the routine backstop.

## Halt controls

- Pause all autonomous dreaming:
  `touch ~/.copilot/skill-state/skill-review/disable-daemon`
- Resume only through the verified installer flow:
  `"${DREAMING_REPO_ROOT:-$HOME/code/dreaming}/scripts/install.sh" selftest &&
  "${DREAMING_REPO_ROOT:-$HOME/code/dreaming}/scripts/install.sh" enable`
- Pause curator state:
  `"${DREAMING_REPO_ROOT:-$HOME/code/dreaming}/skills/skill-curator/scripts/curator-state.sh" set paused true`
- Resume curator state:
  `"${DREAMING_REPO_ROOT:-$HOME/code/dreaming}/skills/skill-curator/scripts/curator-state.sh" set paused false`
- Pause memory curation by setting `"paused": true` in
  `~/.copilot/skill-state/memory-curator/state.json`.

The halt switch always wins. Do not bypass it from a foreground or scheduled
run.
