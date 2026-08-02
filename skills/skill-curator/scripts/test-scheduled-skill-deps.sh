#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/scheduled-deps.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
export TMPDIR="$TMP"
SCANNER="$SCRIPT_DIR/scheduled-skill-deps.py"
OWNED=(skill-review skill-curator memory-curator skill-create skill-manage)
SHARED=(writing-great-skills dual-review authenticated-browse)
PUBLIC="$TMP/public"
LOCAL="$TMP/local"
DREAMING="$TMP/dreaming"
SHARED_ROOT="$TMP/shared"
PLISTS="$TMP/plists"
PREFIX="com.fixture.dreaming"
LEGACY_PREFIX="com.fixture.skills"

mkdir -p "$PUBLIC/skills" "$LOCAL" "$DREAMING/skills" "$SHARED_ROOT/skills" "$PLISTS"
mkdir -p "$DREAMING/scripts"
for name in "${OWNED[@]}"; do
  mkdir -p "$DREAMING/skills/$name"
  printf -- '---\nname: %s\ndescription: Dreaming fixture skill for dependency tests.\n---\n' "$name" \
    > "$DREAMING/skills/$name/SKILL.md"
done
for name in "${SHARED[@]}"; do
  mkdir -p "$SHARED_ROOT/skills/$name"
  printf -- '---\nname: %s\ndescription: Shared fixture skill for dependency tests.\n---\n' "$name" \
    > "$SHARED_ROOT/skills/$name/SKILL.md"
done
mkdir -p "$SHARED_ROOT/skills/writing-great-skills/references"
printf '# Fixture glossary\n' \
  > "$SHARED_ROOT/skills/writing-great-skills/references/GLOSSARY.md"
for name in public-target duplicate-target; do
  mkdir -p "$PUBLIC/skills/$name"
  printf -- '---\nname: %s\ndescription: Public fixture skill for dependency tests.\n---\n' "$name" \
    > "$PUBLIC/skills/$name/SKILL.md"
done
# Shared/catalog is one published identity, and stale copies of owned names do
# not participate in owned namespace resolution.
cp -R "$SHARED_ROOT/skills/dual-review" "$PUBLIC/skills/dual-review"
cp -R "$DREAMING/skills/skill-review" "$PUBLIC/skills/skill-review"

RUN="$DREAMING/skills/skill-review/scripts/run.sh"
PROMPT="$DREAMING/skills/skill-review/references/prompt.txt"
NESTED="$DREAMING/skills/skill-review/references/nested.md"
mkdir -p "$(dirname "$RUN")" "$(dirname "$PROMPT")"
cat > "$RUN" <<EOF
#!/usr/bin/env bash
ROOT_SCRIPT_DIR="\$DREAMING_REPO_ROOT/scripts"
cat "$PROMPT"
EOF
chmod +x "$RUN"
cat > "$PROMPT" <<EOF
Read $NESTED and use /dfrysinger-dreaming:skill-create.
EOF
cat > "$NESTED" <<EOF
Read $SHARED_ROOT/skills/dual-review/SKILL.md and use /public-target.
EOF

PLIST="$PLISTS/$PREFIX.daily.plist"
python3 - "$PLIST" "$RUN" <<'PY'
import plistlib,sys
path,run=sys.argv[1:]
plistlib.dump(
    {"Label":"com.fixture.dreaming.daily","ProgramArguments":["/bin/bash",run]},
    open(path,"wb"),
)
PY

run_scanner() {
  DREAMING_REPO_ROOT="$DREAMING" \
  DREAMING_SHARED_SKILLS_ROOT="$SHARED_ROOT" \
  SKILLS_REPO_ROOT="$PUBLIC" \
  SKILLS_LOCAL_ROOT="$LOCAL" \
  SKILLS_LAUNCH_AGENTS_DIR="$PLISTS" \
  DREAMING_LAUNCHD_PREFIX="$PREFIX" \
  SKILLS_LAUNCHD_PREFIX="$LEGACY_PREFIX" \
    "$SCANNER" "$@"
}

OUTPUT="$TMP/inventory.json"
run_scanner --inventory > "$OUTPUT"
python3 - "$OUTPUT" "$DREAMING" "$SHARED_ROOT" <<'PY'
import json,sys
payload=json.load(open(sys.argv[1]))
deps={row["skill"]:row for row in payload["dependencies"]}
assert {"skill-review","skill-create","dual-review","public-target"} <= set(deps)
assert deps["skill-review"]["namespace"] == "dreaming"
assert deps["dual-review"]["namespace"] == "shared"
inventory={row["name"]:row for row in payload["skills"]}
assert inventory["skill-review"]["path"].startswith(sys.argv[2])
assert inventory["dual-review"]["path"].startswith(sys.argv[3])
assert inventory["dual-review"]["published_identity"] == "shared/catalog"
assert inventory["dual-review"]["implicit_pin"] is True
scanned=set(payload["scanned_files"])
assert any(path.endswith("/scripts/run.sh") for path in scanned)
assert any(path.endswith("/references/prompt.txt") for path in scanned)
assert any(path.endswith("/references/nested.md") for path in scanned)
PY
if run_scanner --check dual-review >"$TMP/check.out" 2>"$TMP/check.err"; then
  echo "FAIL: shared/catalog dependency was not an implicit pin" >&2
  exit 1
fi
grep -q "implicit pin" "$TMP/check.err"
echo "PASS  separate dreaming/shared/public namespaces"

LEGACY_RUN="$PUBLIC/skills/skill-review/legacy-run.sh"
printf '#!/usr/bin/env bash\n:\n' > "$LEGACY_RUN"
chmod +x "$LEGACY_RUN"
LEGACY_PLIST="$PLISTS/$LEGACY_PREFIX.sweep.plist"
python3 - "$LEGACY_PLIST" "$LEGACY_RUN" <<'PY'
import plistlib,sys
path,run=sys.argv[1:]
plistlib.dump(
    {"Label":"com.fixture.skills.sweep","ProgramArguments":["/bin/bash",run]},
    open(path,"wb"),
)
PY
if run_scanner >"$TMP/legacy.out" 2>"$TMP/legacy.err"; then
  echo "FAIL: legacy public path for an owned skill was accepted" >&2
  exit 1
fi
grep -q "non-authoritative public path for reserved skill skill-review" \
  "$TMP/legacy.err"
rm "$LEGACY_PLIST"
echo "PASS  retired owned-skill paths fail closed"

cp "$NESTED" "$TMP/nested.saved"
echo "Read $DREAMING/skills/skill-review/references/missing.md." >> "$NESTED"
if run_scanner >"$TMP/missing.out" 2>"$TMP/missing.err"; then
  echo "FAIL: missing external durable reference was accepted" >&2
  exit 1
fi
grep -q "referenced path is missing" "$TMP/missing.err"
mv "$TMP/nested.saved" "$NESTED"
echo "PASS  missing external durable owners fail closed"

mkdir -p "$LOCAL/duplicate-target"
cp "$PUBLIC/skills/duplicate-target/SKILL.md" "$LOCAL/duplicate-target/SKILL.md"
if run_scanner >"$TMP/duplicate.out" 2>"$TMP/duplicate.err"; then
  echo "FAIL: public/local ambiguity was accepted" >&2
  exit 1
fi
grep -q "ambiguous live skill name: duplicate-target" "$TMP/duplicate.err"
rm -rf "$LOCAL/duplicate-target"
echo "PASS  managed public/local ambiguity remains fail closed"

printf 'not a plist\n' > "$PLISTS/$PREFIX.broken.plist"
if run_scanner >"$TMP/plist.out" 2>"$TMP/plist.err"; then
  echo "FAIL: malformed LaunchAgent was accepted" >&2
  exit 1
fi
grep -q "cannot parse" "$TMP/plist.err"
rm "$PLISTS/$PREFIX.broken.plist"
echo "PASS  malformed external owner fails closed"

python3 - "$PLIST" "$DREAMING/missing.sh" <<'PY'
import plistlib,sys
path,missing=sys.argv[1:]
plistlib.dump(
    {"Label":"com.fixture.dreaming.daily","ProgramArguments":["/bin/bash",missing]},
    open(path,"wb"),
)
PY
if run_scanner >"$TMP/program.out" 2>"$TMP/program.err"; then
  echo "FAIL: missing dreaming program path was accepted" >&2
  exit 1
fi
grep -q "referenced path is missing" "$TMP/program.err"
echo "PASS  LaunchAgent program paths are traversed fail closed"

python3 - "$PLIST" "$RUN" <<'PY'
import plistlib,sys
path,run=sys.argv[1:]
plistlib.dump(
    {"Label":"com.fixture.dreaming.daily","ProgramArguments":["/bin/bash",run]},
    open(path,"wb"),
)
PY
REAL_PLIST="$PLISTS/$PREFIX.real.plist"
python3 - "$REAL_PLIST" \
  "$REPO_ROOT/skills/skill-review/scripts/daemon-selftest.sh" <<'PY'
import plistlib,sys
path,run=sys.argv[1:]
plistlib.dump(
    {"Label":"com.fixture.dreaming.real","ProgramArguments":["/bin/bash",run]},
    open(path,"wb"),
)
PY
DREAMING_REPO_ROOT="$REPO_ROOT" \
DREAMING_SHARED_SKILLS_ROOT="$SHARED_ROOT" \
SKILLS_REPO_ROOT="$PUBLIC" \
SKILLS_LOCAL_ROOT="$LOCAL" \
SKILLS_LAUNCH_AGENTS_DIR="$PLISTS" \
DREAMING_LAUNCHD_PREFIX="$PREFIX" \
SKILLS_LAUNCHD_PREFIX="$LEGACY_PREFIX" \
  "$SCANNER" --inventory > "$TMP/real-inventory.json"
python3 - "$TMP/real-inventory.json" <<'PY'
import json,sys
payload=json.load(open(sys.argv[1]))
assert payload["complete"] is True
assert any(path.endswith("/scripts/daemon-selftest.sh") for path in payload["scanned_files"])
PY
echo "PASS  shipped Dreaming layout scans to completion"

echo "scheduled dependency tests: PASS"
