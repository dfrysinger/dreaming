#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$ROOT/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/copilot-migration.XXXXXX")"
cleanup() {
  status=$?
  chmod -R u+w "$TMP" 2>/dev/null || true
  rm -rf "$TMP"
  exit "$status"
}
trap cleanup EXIT

LEGACY_SKILLS="$TMP/legacy-skills"
LEGACY_STATE="$TMP/legacy-state"
TARGET_SKILLS="$TMP/neutral/skills"
TARGET_STATE="$TMP/neutral/state"
JOURNAL="$TMP/neutral/migration.json"
mkdir -p "$LEGACY_SKILLS/learned" "$LEGACY_STATE"
printf '%s\n' '---' 'name: learned' 'description: migration fixture' '---' \
  > "$LEGACY_SKILLS/learned/SKILL.md"
git -C "$LEGACY_SKILLS" init -q
git -C "$LEGACY_SKILLS" config core.hooksPath /dev/null
git -C "$LEGACY_SKILLS" add learned/SKILL.md
git -C "$LEGACY_SKILLS" \
  -c user.name=fixture -c user.email=fixture@example.invalid \
  commit -qm 'fixture'
printf '[{"session_id":"legacy","reviewed_at":1}]\n' \
  > "$LEGACY_STATE/review-ledger.json"
printf '[{"session_id":"queued"}]\n' > "$LEGACY_STATE/queue.json"

"$ROOT/scripts/migrate-copilot-state.py" apply \
  --legacy-skills "$LEGACY_SKILLS" \
  --legacy-state "$LEGACY_STATE" \
  --target-skills "$TARGET_SKILLS" \
  --target-state "$TARGET_STATE" \
  --journal "$JOURNAL" >/dev/null

[[ "$(git -C "$TARGET_SKILLS" rev-parse HEAD)" == \
   "$(git -C "$LEGACY_SKILLS" rev-parse HEAD)" ]]
cmp "$LEGACY_STATE/review-ledger.json" "$TARGET_STATE/review-ledger.json"
cmp "$LEGACY_STATE/queue.json" "$TARGET_STATE/queue.json"
grep -q '"status": "active"' "$JOURNAL"
[[ -f "$LEGACY_SKILLS/learned/SKILL.md" ]]

"$ROOT/scripts/migrate-copilot-state.py" apply \
  --legacy-skills "$LEGACY_SKILLS" \
  --legacy-state "$LEGACY_STATE" \
  --target-skills "$TARGET_SKILLS" \
  --target-state "$TARGET_STATE" \
  --journal "$JOURNAL" >/dev/null

if "$ROOT/scripts/migrate-copilot-state.py" rollback \
  --legacy-skills "$LEGACY_SKILLS" \
  --legacy-state "$LEGACY_STATE" \
  --target-skills "$TARGET_SKILLS" \
  --target-state "$TMP/different-target-state" \
  --journal "$JOURNAL" >"$TMP/wrong-rollback.out" \
  2>"$TMP/wrong-rollback.err"; then
  echo "rollback unexpectedly accepted different requested paths" >&2
  exit 1
fi
grep -q "migration journal belongs to different requested paths" \
  "$TMP/wrong-rollback.err"
[[ -f "$TARGET_SKILLS/learned/SKILL.md" ]]
[[ -f "$TARGET_STATE/review-ledger.json" ]]

"$ROOT/scripts/migrate-copilot-state.py" rollback \
  --legacy-skills "$LEGACY_SKILLS" \
  --legacy-state "$LEGACY_STATE" \
  --target-skills "$TARGET_SKILLS" \
  --target-state "$TARGET_STATE" \
  --journal "$JOURNAL" >/dev/null

[[ ! -e "$TARGET_SKILLS" ]]
[[ ! -e "$TARGET_STATE/review-ledger.json" ]]
[[ ! -e "$TARGET_STATE/queue.json" ]]
[[ -f "$LEGACY_SKILLS/learned/SKILL.md" ]]
grep -q '"status": "rolled-back"' "$JOURNAL"

UNBORN_SKILLS="$TMP/unborn/skills"
UNBORN_STATE="$TMP/unborn/state"
UNBORN_JOURNAL="$TMP/unborn/migration.json"
mkdir -p "$UNBORN_SKILLS"
git -C "$UNBORN_SKILLS" init -q
git -C "$UNBORN_SKILLS" config core.hooksPath /dev/null
"$ROOT/scripts/migrate-copilot-state.py" apply \
  --legacy-skills "$LEGACY_SKILLS" \
  --legacy-state "$LEGACY_STATE" \
  --target-skills "$UNBORN_SKILLS" \
  --target-state "$UNBORN_STATE" \
  --journal "$UNBORN_JOURNAL" >/dev/null
[[ "$(git -C "$UNBORN_SKILLS" rev-parse HEAD)" == \
   "$(git -C "$LEGACY_SKILLS" rev-parse HEAD)" ]]
[[ -z "$(git -C "$UNBORN_SKILLS" remote)" ]]
cmp "$LEGACY_STATE/queue.json" "$UNBORN_STATE/queue.json"
echo "PASS  empty unborn Git target is adoptable"

CRASH_SKILLS="$TMP/crash/skills"
CRASH_STATE="$TMP/crash/state"
CRASH_JOURNAL="$TMP/crash/migration.json"
python3 - "$ROOT/scripts/migrate-copilot-state.py" \
  "$LEGACY_SKILLS" "$LEGACY_STATE" "$CRASH_SKILLS" "$CRASH_STATE" \
  "$CRASH_JOURNAL" <<'PY'
import importlib.util
import json
import os
import sys
from pathlib import Path

script, legacy_skills, legacy_state, target_skills, target_state, journal_path = (
    sys.argv[1:]
)
spec = importlib.util.spec_from_file_location("copilot_migration", script)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
journal = module.prepare(
    Path(legacy_skills).resolve(),
    Path(legacy_state).resolve(),
    Path(target_skills).resolve(),
    Path(target_state).resolve(),
    Path(journal_path).resolve(),
)
target_skills_path = Path(target_skills)
target_skills_path.parent.mkdir(parents=True, exist_ok=True)
os.replace(journal["skills"]["staged"], target_skills_path)
first = journal["state_files"][0]
target_state_path = Path(target_state)
target_state_path.mkdir(parents=True, exist_ok=True)
os.replace(first["staged"], target_state_path / first["name"])
assert json.loads(Path(journal_path).read_text())["status"] == "verified"
PY
"$ROOT/scripts/migrate-copilot-state.py" apply \
  --legacy-skills "$LEGACY_SKILLS" \
  --legacy-state "$LEGACY_STATE" \
  --target-skills "$CRASH_SKILLS" \
  --target-state "$CRASH_STATE" \
  --journal "$CRASH_JOURNAL" >/dev/null
cmp "$LEGACY_STATE/review-ledger.json" "$CRASH_STATE/review-ledger.json"
cmp "$LEGACY_STATE/queue.json" "$CRASH_STATE/queue.json"
grep -q '"status": "active"' "$CRASH_JOURNAL"
if "$ROOT/scripts/migrate-copilot-state.py" apply \
  --legacy-skills "$LEGACY_SKILLS" \
  --legacy-state "$LEGACY_STATE" \
  --target-skills "$CRASH_SKILLS" \
  --target-state "$TMP/crash/different-state" \
  --journal "$CRASH_JOURNAL" >"$TMP/rebind.out" 2>"$TMP/rebind.err"; then
  echo "migration journal unexpectedly accepted different requested paths" >&2
  exit 1
fi
grep -q "migration journal belongs to different requested paths" "$TMP/rebind.err"
"$ROOT/scripts/migrate-copilot-state.py" rollback \
  --legacy-skills "$LEGACY_SKILLS" \
  --legacy-state "$LEGACY_STATE" \
  --target-skills "$CRASH_SKILLS" \
  --target-state "$CRASH_STATE" \
  --journal "$CRASH_JOURNAL" >/dev/null
[[ ! -e "$CRASH_SKILLS" ]]
[[ ! -e "$CRASH_STATE/review-ledger.json" ]]
[[ ! -e "$CRASH_STATE/queue.json" ]]
echo "PASS  verified journal resumes partial activation with ownership intact"

STALE_SKILLS="$TMP/stale/skills"
STALE_STATE="$TMP/stale/state"
STALE_JOURNAL="$TMP/stale/migration.json"
STALE_STAGE="$TMP/stale/.migration-stage-interrupted"
mkdir -p "$STALE_STAGE"
echo partial > "$STALE_STAGE/partial"
python3 - "$LEGACY_SKILLS" "$LEGACY_STATE" "$STALE_SKILLS" "$STALE_STATE" \
  "$STALE_STAGE" "$STALE_JOURNAL" <<'PY'
import json
import sys
from pathlib import Path

legacy_skills, legacy_state, target_skills, target_state, staging, journal = sys.argv[1:]
Path(journal).parent.mkdir(parents=True, exist_ok=True)
Path(journal).write_text(
    json.dumps(
        {
            "version": 1,
            "status": "failed",
            "legacy_skills": str(Path(legacy_skills).resolve()),
            "legacy_state": str(Path(legacy_state).resolve()),
            "target_skills": str(Path(target_skills).resolve()),
            "target_state": str(Path(target_state).resolve()),
            "staging_root": str(Path(staging).resolve()),
            "skills": None,
            "state_files": [],
        }
    )
)
PY
"$ROOT/scripts/migrate-copilot-state.py" apply \
  --legacy-skills "$LEGACY_SKILLS" \
  --legacy-state "$LEGACY_STATE" \
  --target-skills "$STALE_SKILLS" \
  --target-state "$STALE_STATE" \
  --journal "$STALE_JOURNAL" >/dev/null
[[ ! -e "$STALE_STAGE" ]]
grep -q '"status": "active"' "$STALE_JOURNAL"
echo "PASS  stale failed staging is cleaned before restart"

ALIASED_ROOT="$TMP/aliased-root"
ALIASED_LINK="$TMP/aliased-link"
ALIASED_SKILLS="$ALIASED_ROOT/skills"
ALIASED_STATE="$ALIASED_ROOT/state"
ALIASED_JOURNAL="$ALIASED_ROOT/migration.json"
ALIASED_STAGE="$ALIASED_LINK/.migration-stage-interrupted"
mkdir -p "$ALIASED_ROOT/.migration-stage-interrupted"
ln -s "$ALIASED_ROOT" "$ALIASED_LINK"
echo partial > "$ALIASED_ROOT/.migration-stage-interrupted/partial"
python3 - "$LEGACY_SKILLS" "$LEGACY_STATE" "$ALIASED_SKILLS" "$ALIASED_STATE" \
  "$ALIASED_STAGE" "$ALIASED_JOURNAL" <<'PY'
import json
import sys
from pathlib import Path

legacy_skills, legacy_state, target_skills, target_state, staging, journal = sys.argv[1:]
Path(journal).write_text(
    json.dumps(
        {
            "version": 1,
            "status": "failed",
            "legacy_skills": str(Path(legacy_skills).resolve()),
            "legacy_state": str(Path(legacy_state).resolve()),
            "target_skills": str(Path(target_skills).resolve()),
            "target_state": str(Path(target_state).resolve()),
            "staging_root": staging,
            "skills": None,
            "state_files": [],
        }
    )
)
PY
"$ROOT/scripts/migrate-copilot-state.py" apply \
  --legacy-skills "$LEGACY_SKILLS" \
  --legacy-state "$LEGACY_STATE" \
  --target-skills "$ALIASED_SKILLS" \
  --target-state "$ALIASED_STATE" \
  --journal "$ALIASED_JOURNAL" >/dev/null
[[ ! -e "$ALIASED_ROOT/.migration-stage-interrupted" ]]
grep -q '"status": "active"' "$ALIASED_JOURNAL"
echo "PASS  staging cleanup accepts canonical parent aliases"

"$ROOT/scripts/migrate-copilot-state.py" apply \
  --legacy-skills "$LEGACY_SKILLS" \
  --legacy-state "$LEGACY_STATE" \
  --target-skills "$TARGET_SKILLS" \
  --target-state "$TARGET_STATE" \
  --journal "$JOURNAL" >/dev/null
printf '[{"session_id":"changed"}]\n' > "$TARGET_STATE/queue.json"
if "$ROOT/scripts/migrate-copilot-state.py" rollback \
  --legacy-skills "$LEGACY_SKILLS" \
  --legacy-state "$LEGACY_STATE" \
  --target-skills "$TARGET_SKILLS" \
  --target-state "$TARGET_STATE" \
  --journal "$JOURNAL" >"$TMP/rollback.out" 2>"$TMP/rollback.err"; then
  echo "rollback unexpectedly accepted changed state" >&2
  exit 1
fi
grep -q "neutral state changed after migration: $TARGET_STATE/queue.json" \
  "$TMP/rollback.err"
[[ -f "$TARGET_SKILLS/learned/SKILL.md" ]]
cmp "$LEGACY_STATE/review-ledger.json" "$TARGET_STATE/review-ledger.json"
grep -q '"session_id":"changed"' "$TARGET_STATE/queue.json"
grep -q '"status": "active"' "$JOURNAL"

echo "copilot migration tests: PASS"
