#!/usr/bin/env bash
# restore-skill.sh — bring a retired skill back from git history.
# Inverse of archive-skill.sh.
#
# archive-skill.sh deletes the skill and writes a retirement record naming the
# commit that still holds it. This reads that record and checks the skill's
# tree back out of that commit. When the record is missing (retired before
# records existed, or state dir wiped), it falls back to finding the delete
# commit in the log, so history alone is always enough.
#
# Root-aware: public repo => git + registry; local native => git only.
#
# Usage: restore-skill.sh <name>

set -euo pipefail
TRAILER="${SKILLS_COAUTHOR_TRAILER:-Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>}"

if [[ $# -ne 1 ]]; then
  echo "usage: $(basename "$0") <name>" >&2
  exit 2
fi

NAME="$1"
REPO_ROOT="${SKILLS_REPO_ROOT:-$HOME/code/skills}"
LOCAL_ROOT="${SKILLS_LOCAL_ROOT:-$HOME/.copilot/skills}"
REPO_ROOT="$(cd "$REPO_ROOT" && pwd -P)"
LOCAL_ROOT="$(cd "$LOCAL_ROOT" && pwd -P)"
if [[ -n "${SKILLS_REVIEW_STATE_DIR:-}" ]]; then
  STATE_DIR="$SKILLS_REVIEW_STATE_DIR"
elif [[ -n "${SKILLS_STATE_DIR:-}" ]]; then
  # Compatibility: before SKILLS_REVIEW_STATE_DIR existed, this override
  # named the review-state directory itself, regardless of its basename.
  STATE_DIR="$SKILLS_STATE_DIR"
else
  STATE_DIR="$HOME/.copilot/skill-state/skill-review"
fi
LEGACY_STATE_DIR=""
if [[ -n "${SKILLS_REVIEW_STATE_DIR:-}" &&
      -n "${SKILLS_STATE_DIR:-}" &&
      "$SKILLS_STATE_DIR" != "$STATE_DIR" ]]; then
  LEGACY_STATE_DIR="$SKILLS_STATE_DIR"
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_SCRIPT="$SCRIPT_DIR/../../skill-review/scripts/daemon-lock.sh"
LOCK_TOKEN=""
release_lock() {
  if [[ -n "$LOCK_TOKEN" ]]; then
    "$LOCK_SCRIPT" release "$LOCK_TOKEN" >/dev/null || true
  fi
}
trap release_lock EXIT

GIT_ROOT=""
SRC_REL=""
DEST_REL=""
RESTORE_SHA=""
DELETE_SHA=""
RECORD=""
RECORD_STATE_DIR=""
RECORD_SOURCE="history"

if [[ -n "${SKILLS_RESTORE_GIT_ROOT:-}" ||
      -n "${SKILLS_RESTORE_SRC_REL:-}" ||
      -n "${SKILLS_RESTORE_SHA:-}" ]]; then
  [[ -n "${SKILLS_RESTORE_GIT_ROOT:-}" &&
     -n "${SKILLS_RESTORE_SRC_REL:-}" &&
     -n "${SKILLS_RESTORE_SHA:-}" ]] || {
    echo "REFUSED: incomplete transaction restore identity." >&2
    exit 1
  }
  GIT_ROOT="$(cd "$SKILLS_RESTORE_GIT_ROOT" && pwd -P)"
  [[ "$GIT_ROOT" == "$(cd "$REPO_ROOT" && pwd -P)" ||
     "$GIT_ROOT" == "$(cd "$LOCAL_ROOT" && pwd -P)" ]] || {
    echo "REFUSED: transaction restore root is not managed: $GIT_ROOT" >&2
    exit 1
  }
  SRC_REL="$SKILLS_RESTORE_SRC_REL"
  DEST_REL="$SRC_REL"
  RESTORE_SHA="$SKILLS_RESTORE_SHA"
  if [[ "$GIT_ROOT" == "$(cd "$REPO_ROOT" && pwd -P)" ]]; then
    [[ "$SRC_REL" == "skills/$NAME" ]] || {
      echo "REFUSED: public transaction restore path does not match $NAME." >&2
      exit 1
    }
  else
    [[ "$SRC_REL" == "$NAME" ]] || {
      echo "REFUSED: local transaction restore path does not match $NAME." >&2
      exit 1
    }
  fi
  RECORD="$STATE_DIR/retired/$NAME.json"
  RECORD_STATE_DIR="$STATE_DIR"
  RECORD_SOURCE="record"
else
  CANONICAL_RECORD="$STATE_DIR/retired/$NAME.json"
  LEGACY_RECORD=""
  [[ -n "$LEGACY_STATE_DIR" ]] &&
    LEGACY_RECORD="$LEGACY_STATE_DIR/retired/$NAME.json"

  [[ ! -L "$CANONICAL_RECORD" ]] || {
    echo "REFUSED: retirement record is a symlink: $CANONICAL_RECORD" >&2
    exit 1
  }
  if [[ -n "$LEGACY_RECORD" ]]; then
    [[ ! -L "$LEGACY_RECORD" ]] || {
      echo "REFUSED: legacy retirement record is a symlink: $LEGACY_RECORD" >&2
      exit 1
    }
  fi

  if [[ -f "$CANONICAL_RECORD" &&
        -n "$LEGACY_RECORD" &&
        -f "$LEGACY_RECORD" ]]; then
    echo "REFUSED: retirement state is ambiguous; records exist at both:" >&2
    echo "         $CANONICAL_RECORD" >&2
    echo "         $LEGACY_RECORD" >&2
    echo "         Migrate or remove the duplicate explicitly before restoring." >&2
    exit 1
  elif [[ -f "$CANONICAL_RECORD" ]]; then
    RECORD="$CANONICAL_RECORD"
    RECORD_STATE_DIR="$STATE_DIR"
    RECORD_SOURCE="record"
  elif [[ -n "$LEGACY_RECORD" && -f "$LEGACY_RECORD" ]]; then
    RECORD="$LEGACY_RECORD"
    RECORD_STATE_DIR="$LEGACY_STATE_DIR"
    RECORD_SOURCE="legacy-record"
    echo "legacy retirement record: $RECORD" >&2
  fi

  if [[ -n "$RECORD" ]]; then
    if ! RECORD_VALUES=$(python3 - "$RECORD" <<'PY'
import json
import sys

record = json.load(open(sys.argv[1]))
required = ("git_root", "path", "restore_sha")
missing = [key for key in required if not isinstance(record.get(key), str) or not record[key]]
if missing:
    raise ValueError("missing or invalid fields: " + ", ".join(missing))
values = (
    record["git_root"],
    record["path"],
    record.get("dest") or record["path"],
    record["restore_sha"],
)
if any("\t" in value or "\n" in value for value in values):
    raise ValueError("record identity contains unsupported whitespace")
print("\t".join(values))
PY
    ); then
      echo "REFUSED: invalid retirement record: $RECORD" >&2
      exit 1
    fi
    IFS=$'\t' read -r GIT_ROOT SRC_REL DEST_REL RESTORE_SHA <<<"$RECORD_VALUES"
  else
    # No record: search each root's history for the commit that deleted the
    # skill. The restore point is that commit's parent.
    for ROOT in "$REPO_ROOT" "$LOCAL_ROOT"; do
      [[ -d "$ROOT/.git" ]] || continue
      for CANDIDATE in "skills/$NAME" "$NAME"; do
        # --no-renames is required: git's default rename detection reports a
        # move as R, not D, so the delete commit would never be found.
        SHA=$(git -C "$ROOT" log -1 --no-renames --format=%H --diff-filter=D \
          -- "$CANDIDATE/SKILL.md" 2>/dev/null || true)
        [[ -n "$SHA" ]] || continue
        if [[ -n "$GIT_ROOT" ]]; then
          echo "ambiguous: '$NAME' was deleted in more than one root; restore by hand." >&2
          exit 1
        fi
        GIT_ROOT="$ROOT"
        SRC_REL="$CANDIDATE"
        RESTORE_SHA="$SHA^"
        DELETE_SHA="$SHA"
      done
    done
  fi
fi

if [[ -z "$GIT_ROOT" ]]; then
  echo "no retired skill named '$NAME': no retirement record and no delete commit in either root." >&2
  exit 1
fi
GIT_ROOT="$(cd "$GIT_ROOT" && pwd -P)"

# Verify the skill is actually present at the restore point before touching the
# working tree — a wrong SHA should fail here, not half-restore.
if ! git -C "$GIT_ROOT" cat-file -e "$RESTORE_SHA:$SRC_REL/SKILL.md" 2>/dev/null; then
  echo "REFUSED: $SRC_REL/SKILL.md is not present in $RESTORE_SHA." >&2
  exit 1
fi

[[ -n "$DEST_REL" ]] || DEST_REL="$SRC_REL"
DEST="$GIT_ROOT/$DEST_REL"
if [[ -e "$DEST" ]]; then
  echo "REFUSED: a live skill already exists at $DEST. Rename or remove it before restore." >&2
  exit 1
fi

if [[ -z "${SKILLS_CURATOR_ROLLBACK:-}" ]]; then
  LOCK_TOKEN="$("$LOCK_SCRIPT" acquire --mode session --owner "restore-skill:$NAME")"
fi

USE_REGISTRY=0
[[ "$GIT_ROOT" == "$REPO_ROOT" ]] && USE_REGISTRY=1

HISTORY_DIR=""
if [[ -z "${SKILLS_CURATOR_ROLLBACK:-}" ]]; then
  HISTORY_STATE_DIR="${RECORD_STATE_DIR:-$STATE_DIR}"
  HISTORY_DIR="$HISTORY_STATE_DIR/retirement-history"
  mkdir -p "$HISTORY_DIR"
  if [[ -n "$RECORD" ]]; then
    python3 - "$RECORD" "$HISTORY_DIR" <<'PY'
import os
import sys

source, destination_dir = sys.argv[1:]
if os.stat(os.path.dirname(source)).st_dev != os.stat(destination_dir).st_dev:
    raise SystemExit(
        "REFUSED: retirement record and history directory are on different "
        "filesystems; migrate the legacy state directory before restoring."
    )
PY
  fi
fi

cd "$GIT_ROOT"
git checkout "$RESTORE_SHA" -- "$SRC_REL"

# The restore point may predate a layout change, so put the skill where it
# belongs today and leave no empty scaffolding behind.
if [[ "$DEST_REL" != "$SRC_REL" ]]; then
  mkdir -p "$(dirname "$DEST_REL")"
  git mv "$SRC_REL" "$DEST_REL"
  PARENT="$(dirname "$SRC_REL")"
  while [[ "$PARENT" != "." && -d "$PARENT" ]] && [[ -z "$(ls -A "$PARENT")" ]]; do
    rmdir "$PARENT"
    PARENT="$(dirname "$PARENT")"
  done
fi

# Clear any curator tombstone — a deliberate restore means the skill is wanted
# again, so skill-review should no longer be blocked from touching it.
TOMBS=("$STATE_DIR/tombstones/$NAME.json")
if [[ -n "$LEGACY_STATE_DIR" ]]; then
  TOMBS+=("$LEGACY_STATE_DIR/tombstones/$NAME.json")
fi
for TOMB in "${TOMBS[@]}"; do
  if [[ -f "$TOMB" || -L "$TOMB" ]]; then
    rm -f "$TOMB"
    echo "tombstone cleared: $TOMB"
  fi
done

# Re-register in the plugin allowlist (public repo only).
if [[ "$USE_REGISTRY" -eq 1 ]]; then
  if [[ -n "${SKILLS_RESTORE_MANIFEST_SNAPSHOT:-}" ]]; then
    SNAPSHOT="$(cd "$SKILLS_RESTORE_MANIFEST_SNAPSHOT" && pwd -P)"
    while IFS= read -r manifest; do
      [[ -f "$SNAPSHOT/$manifest" ]] || {
        echo "REFUSED: transaction manifest snapshot is incomplete: $manifest" >&2
        exit 1
      }
      cp -p "$SNAPSHOT/$manifest" "$REPO_ROOT/$manifest"
    done < <("$SCRIPT_DIR/registry.sh" --manifest-paths)
  else
    "$SCRIPT_DIR/registry.sh" register "$NAME" || true
  fi
fi

# Stage only this restore's own paths: a bare `git add -A` sweeps unrelated
# working-tree changes into the commit.
STAGE=("$SRC_REL" "$DEST_REL")
if [[ "$USE_REGISTRY" -eq 1 ]]; then
  while IFS= read -r M; do STAGE+=("$M"); done < <("$SCRIPT_DIR/registry.sh" --manifest-paths)
fi
git add -- "${STAGE[@]}"
if ! git commit --only -m "skills/$NAME: restore

Checked $SRC_REL back out of $RESTORE_SHA into $DEST_REL.

${TRAILER}" -- "${STAGE[@]}"; then
  git reset -q -- "${STAGE[@]}" || true
  echo "WARNING: restore succeeded on disk but was not committed." >&2
  exit 1
fi

if [[ -z "${SKILLS_CURATOR_ROLLBACK:-}" ]]; then
  RESTORE_COMMIT="$(git rev-parse HEAD)"
  HISTORY="$HISTORY_DIR/$NAME-$RESTORE_COMMIT.json"
  if [[ -n "$RECORD" ]]; then
    # Moving the active record into history is the state transition. rename(2)
    # keeps one durable evidence file visible at all times; enrichment happens
    # only after the move and can never destroy the original record contents.
    python3 - "$RECORD" "$HISTORY" "$RESTORE_COMMIT" "$RECORD_SOURCE" <<'PY'
import json, os, sys, tempfile
from datetime import datetime, timezone

source, destination, restore_commit, record_source = sys.argv[1:]
if os.path.exists(destination):
    raise SystemExit(f"REFUSED: retirement history already exists: {destination}")

source_dir = os.path.dirname(source)
destination_dir = os.path.dirname(destination)
os.replace(source, destination)
for directory_path in dict.fromkeys((source_dir, destination_dir)):
    directory = os.open(directory_path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)

try:
    record = json.load(open(destination))
    record["restored_at"] = datetime.now(timezone.utc).isoformat()
    record["restore_commit"] = restore_commit
    record["record_source"] = record_source
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(destination)}.",
        dir=destination_dir,
    )
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(record, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        directory = os.open(destination_dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
except Exception as error:
    raise SystemExit(
        f"retirement evidence moved safely to {destination}, "
        f"but history metadata enrichment failed: {error}"
    )
PY
  else
    python3 - "$HISTORY" "$NAME" "$SRC_REL" "$DEST_REL" "$GIT_ROOT" \
      "$RESTORE_SHA" "$DELETE_SHA" "$RESTORE_COMMIT" <<'PY'
import json, os, sys, tempfile
from datetime import datetime, timezone

(destination, name, source_rel, dest_rel, git_root, restore_sha,
 delete_sha, restore_commit) = sys.argv[1:]
record = {
    "skill": name,
    "path": source_rel,
    "dest": dest_rel,
    "git_root": git_root,
    "restore_sha": restore_sha,
    "delete_commit": delete_sha or None,
    "retired_at": None,
    "reason": "unknown",
    "replacement": None,
    "record_source": "git-history",
    "restored_at": datetime.now(timezone.utc).isoformat(),
    "restore_commit": restore_commit,
}
if os.path.exists(destination):
    raise SystemExit(f"REFUSED: retirement history already exists: {destination}")
descriptor, temporary = tempfile.mkstemp(
    prefix=f".{os.path.basename(destination)}.",
    dir=os.path.dirname(destination),
)
try:
    with os.fdopen(descriptor, "w") as handle:
        json.dump(record, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    directory = os.open(os.path.dirname(destination), os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
  fi
  echo "retirement history: $HISTORY"
elif [[ -n "$RECORD" ]]; then
  rm -f "$RECORD"
fi

echo "restored: $DEST_REL from $RESTORE_SHA (root: $GIT_ROOT)"
