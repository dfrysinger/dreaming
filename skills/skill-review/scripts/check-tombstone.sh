#!/usr/bin/env bash
# check-tombstone.sh — before skill-review CREATES a skill, check whether a
# same-or-similar skill was previously archived/consolidated by the curator.
# Recreating it would cause the create→archive→recreate thrash the rubber-duck
# flagged. Tombstones are written by skill-curator's archive step (for
# agent-created skills) at
# ~/.copilot/skill-state/skill-review/tombstones/<name>.json (daemon state,
# outside the public repo). Override canonically with
# SKILLS_REVIEW_STATE_DIR. SKILLS_STATE_DIR retains its legacy meaning as the
# review-state directory itself.
#
# Exit codes:
#   0  a tombstone matched — caller MUST NOT create; patch `replacement` or skip
#   1  no tombstone matched — safe to proceed
#   2  tombstone state is ambiguous or unreadable — caller MUST fail closed
#
# On match (exit 0) prints the tombstone JSON so the caller can read
# `replacement` (the umbrella the content went into) and `reason`.
#
# Usage:
#   check-tombstone.sh <candidate-name>

set -euo pipefail

[[ $# -eq 1 ]] || { echo "usage: $(basename "$0") <candidate-name>" >&2; exit 2; }
NAME="$1"
[[ "$NAME" != */* && "$NAME" != "." && "$NAME" != ".." ]] || {
  echo "REFUSED: invalid candidate skill name: $NAME" >&2
  exit 2
}

if [[ -n "${SKILLS_REVIEW_STATE_DIR:-}" ]]; then
  STATE_DIR="$SKILLS_REVIEW_STATE_DIR"
elif [[ -n "${SKILLS_STATE_DIR:-}" ]]; then
  STATE_DIR="$SKILLS_STATE_DIR"
else
  STATE_DIR="$HOME/.copilot/skill-state/skill-review"
fi

TOMB_DIRS=("$STATE_DIR/tombstones")
if [[ -n "${SKILLS_REVIEW_STATE_DIR:-}" &&
      -n "${SKILLS_STATE_DIR:-}" &&
      "$SKILLS_STATE_DIR" != "$STATE_DIR" ]]; then
  # Read the pre-migration location as well. A match in either location blocks
  # recreation until restore-skill.sh clears both records.
  TOMB_DIRS+=("$SKILLS_STATE_DIR/tombstones")
fi

# Fuzzy: candidate shares the leading token with a tombstoned name, or vice
# versa (e.g. 'gh-token-load' vs a tombstoned 'gh-auth-fix', both 'gh-*').
python3 - "$NAME" "${TOMB_DIRS[@]}" <<'PY'
import json
import os
import stat
import sys

name, *directories = sys.argv[1:]

def toks(s): return set(s.lower().replace('_', '-').split('-'))

cand = toks(name)
try:
    for directory in directories:
        try:
            mode = os.lstat(directory).st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(mode):
            raise OSError(f"tombstone path is not a real directory: {directory}")
        for entry in sorted(os.scandir(directory), key=lambda item: item.name):
            if not entry.name.endswith(".json"):
                continue
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                raise OSError(f"tombstone is not a regular file: {entry.path}")
            with open(entry.path) as handle:
                raw = handle.read()
            record = json.loads(raw)
            if not isinstance(record, dict) or not isinstance(record.get("skill"), str):
                raise ValueError(f"invalid tombstone record: {entry.path}")
            base = entry.name[:-5]
            shared = cand & toks(base)
            if base == name or (
                shared
                and next(iter(sorted(cand)), "") in toks(base)
                and len(shared) >= 2
            ):
                print(raw, end="" if raw.endswith("\n") else "\n")
                sys.exit(0)
except (OSError, ValueError, json.JSONDecodeError) as error:
    print(f"REFUSED: tombstone state is ambiguous: {error}", file=sys.stderr)
    sys.exit(2)
sys.exit(1)
PY
