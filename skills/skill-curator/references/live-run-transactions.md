# Live curator transactions

`scripts/curator-run.py` is the mutation boundary for a machine-authorized or
manual `--live` pass. It uses the same SQLite writer lease as dreaming and records an atomic
manifest under
`~/.copilot/skill-state/skill-review/curator-runs/<run-id>.json`.

## Plan

Before the first edit, write one JSON plan in the exact order mutations will
run:

```json
{
  "operations": [
    {
      "kind": "commit",
      "action": "patch",
      "root": "public",
      "skill": "umbrella",
      "sources": ["narrow-sibling"],
      "paths": [
        "skills/umbrella/SKILL.md",
        "skills/umbrella/references/narrow-sibling.md"
      ]
    },
    {
      "kind": "archive",
      "skill": "narrow-sibling",
      "absorbed_into": "umbrella"
    }
  ]
}
```

`kind=commit` supports `action=patch|create`, an owning `root=public|local`,
and exact root-relative **file** paths. Directory scopes are not accepted:
every file the edit may add, modify, or remove must be enumerated. Public
registry files must be listed when a create changes them. `kind=archive`
resolves its root and registry paths from the frozen inventory.
Autonomous commit paths must remain inside the named destination skill; only
the public registry files are allowed as additional paths for a public create.
A consolidation archive must follow its destination commit in the plan.

Begin before mutation:

```bash
RUN_ID=$(scripts/curator-run.py begin --autonomous \
  --plan /path/to/curator-plan.json \
  --report ~/.copilot/skill-state/reports/<fresh>-curator-report.md)
```

`begin --autonomous` acquires the shared writer lease, validates both Git
identities, binds the fresh report digest, independently verifies provenance,
roots, pins, dependencies, halt, and pause, writes an immutable authorization
receipt, records starting commits and exact unrelated dirty state, and rejects
path overlap. Agent authority requires the marker, validated
`.agent-created.json` envelope, and matching `author` frontmatter. Autonomous
prunings also require the structured age/completion, use, reuse, evaluation,
and tombstone evidence described in `curator-prompt.md`.

For a manual-authority transaction, omit `--autonomous`:

```bash
RUN_ID=$(scripts/curator-run.py begin \
  --plan /path/to/manual-plan.json \
  --report /path/to/manual-report.md)
```

## Patch or create

Record intent before editing:

```bash
OP_ID=$(scripts/curator-run.py intent \
  --run "$RUN_ID" --kind commit --root public --action patch \
  --skill umbrella --paths \
    skills/umbrella/SKILL.md \
    skills/umbrella/references/narrow-sibling.md)
```

Make the declared edit, validate it, evaluate behavioral changes, and run the
required reviews. Then use the scoped commit wrapper:

```bash
scripts/curator-run.py commit \
  --run "$RUN_ID" --op "$OP_ID" --message-file /path/to/message.txt
```

It commits only declared paths and records the commit plus exact ledger/state
effects. Never use a broad `git add -A` or a separate commit during a live run.
Before each autonomous intent or commit, the runner rechecks pause, halt,
report identity, provenance, and dependencies. Inventory revalidation permits
only rows added, removed, or changed by already-authorized transaction
operations; unrelated inventory drift refuses the next mutation.

For a create, check the destination tombstone before intent and declare the
provenance files with the package. The tombstone guard returns `0` when a
tombstone matched and creation must stop, `1` when creation is safe, and any
other status when the check must fail closed:

```bash
if skill-review/scripts/check-tombstone.sh <destination>; then
  exit 1
else
  tombstone_status=$?
  [ "$tombstone_status" -eq 1 ] || exit "$tombstone_status"
fi
scripts/curator-run.py intent \
  --run "$RUN_ID" --kind commit --root local --action create \
  --skill <destination> --paths \
    <destination>/SKILL.md \
    <destination>/.agent-created \
    <destination>/.agent-created.json
```

After shared `/skill-create` writes the package, run
`skill-review/scripts/mark-agent-created.sh` with the authorized task key and
`--created-by skill-curator`. The scoped commit refuses a create whose marker
or evidence envelope is missing.

## Archive

`archive-skill.sh` records intent and completion itself when the run id is in
the environment:

```bash
SKILLS_CURATOR_RUN_ID="$RUN_ID" \
  skills/skill-manage/scripts/archive-skill.sh narrow-sibling \
    --absorbed-into umbrella
```

The archive still performs a current scheduled-dependency check immediately
before intent. The begin-time freeze prevents a partial run from discovering
an unsafe archive only after earlier mutations have landed.
For consolidation, the destination commit must already be complete. Completion
also verifies that the retirement record and tombstone name the exact planned
replacement and bind the report plus full provenance references.

## Publish, finish, or rollback

Renew the lease before/after long model or evaluation work:

```bash
scripts/curator-run.py renew --run "$RUN_ID"
```

Renewal remains available in `publish_failed` so supervised retry or rollback
can retain exclusive writer authority.

After every planned operation completes, publish any public-root change while
the lease is still held:

```bash
scripts/curator-run.py publish --run "$RUN_ID" --remote origin --branch main
```

Then finish:

```bash
scripts/curator-run.py finish --run "$RUN_ID"
```

On any failure, reverse the whole run:

```bash
scripts/curator-run.py rollback --run "$RUN_ID"
```

Rollback proceeds in global reverse operation order across both roots. It uses
`restore-skill.sh` for archives and `git revert` for patch/create commits,
removes only exact recorded ledger effects under an exclusive file lock, and
restores prior retirement/tombstone bytes. It refuses changed unrelated dirty
files, undeclared dirty paths, missing or rewritten commits, changed state
effects, or ambiguous root identities. An interrupted intent is recovered:
uncommitted declared paths are reset, while a single committed-but-unrecorded
archive is inferred and restored.

A rejected public push leaves the run in `publish_failed` with its lease held.
Every failed push is reconciled against the exact prior and transaction remote
heads before its outcome is classified. Rollback verifies both the recorded
remote URL and remote head before reversing any local operation, then checks
them again before pushing normal revert commits and recording the resulting
remote identity.
