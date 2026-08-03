#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$ROOT/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/installer.XXXXXX")"
trap 'chmod -R u+w "$TMP" 2>/dev/null || true; rm -rf "$TMP"' EXIT
export TMPDIR="$TMP"
INSTALLER="$ROOT/scripts/install.sh"
SOURCE="$TMP/source"
for name in writing-great-skills dual-review authenticated-browse; do
  mkdir -p "$SOURCE/skills/$name"
  printf -- '---\nname: %s\ndescription: Installer fixture shared skill.\n---\n' "$name" \
    > "$SOURCE/skills/$name/SKILL.md"
done
RECEIPT="$TMP/receipt.json"
"$ROOT/scripts/dreaming-deps.py" generate-receipt "$SOURCE" \
  --revision 0123456789abcdef0123456789abcdef01234567 --output "$RECEIPT"

DEST="$TMP/LaunchAgents"
STATE="$TMP/state"
PUBLIC="$TMP/public"
LOCAL="$TMP/local"
mkdir -p "$DEST" "$STATE" "$PUBLIC/skills" "$LOCAL"
git -C "$LOCAL" init -q
OLD="$TMP/old-run.sh"
printf '#!/usr/bin/env bash\n:\n' > "$OLD"
chmod +x "$OLD"
for kind in sweep selftest watchdog; do
  python3 - "$DEST/com.fixture.skills.$kind.plist" \
    "com.fixture.skills.$kind" "$OLD" <<'PY'
import plistlib,sys
path,label,program=sys.argv[1:]
plistlib.dump(
    {"Label":label,"ProgramArguments":["/bin/bash",program]},
    open(path,"wb"),
)
PY
done
BEFORE_HASH="$(shasum -a 256 "$DEST/com.fixture.skills.sweep.plist" | awk '{print $1}')"

FAKE="$TMP/launchctl"
cat > "$FAKE" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$LAUNCHCTL_LOG"
if [[ -n "${DREAMING_TEST_BLOCK_DIR:-}" && "${1:-}" == "bootout" &&
      ! -e "$DREAMING_TEST_BLOCK_DIR/entered" ]]; then
  mkdir -p "$DREAMING_TEST_BLOCK_DIR"
  touch "$DREAMING_TEST_BLOCK_DIR/entered"
  while [[ ! -e "$DREAMING_TEST_BLOCK_DIR/release" ]]; do sleep 0.05; done
fi
if [[ "${1:-}" == "kickstart" ]]; then
  printf '== result: 0 failure(s) ==\n' > "$DREAMING_SELFTEST_RESULT_FILE"
  if [[ -n "${DREAMING_TEST_REPLACE_GENERATION:-}" ]]; then
    printf '%s\n' "$DREAMING_TEST_REPLACE_GENERATION" \
      > "$SKILLS_STATE_DIR/dreaming/activation-generation"
  fi
fi
exit 0
SH
chmod +x "$FAKE"
export LAUNCHCTL_LOG="$TMP/launchctl.log"
export DREAMING_SELFTEST_RESULT_FILE="$TMP/selftest.out"

run_install() {
  COPILOT_HOME="$TMP/copilot-home" \
  DREAMING_REPO_ROOT="$ROOT" \
  DREAMING_CONFIG_POINTER="$TMP/config-pointer" \
  DREAMING_DATA_DIR="$TMP/data" \
  DREAMING_STATE_DIR="$TMP/dreaming-state" \
  DREAMING_SKILLS_ROOT="$TMP/dreaming-skills" \
  DREAMING_CONFIG_FILE="$TMP/config.env" \
  DREAMING_DEPS_DIR="$TMP/deps" \
  DREAMING_RECEIPT_FILE="$RECEIPT" \
  DREAMING_DEPS_SOURCE="$SOURCE" \
  DREAMING_CANONICAL_SKILLS_ROOT="" \
  DREAMING_INSTALLED_PLUGINS_ROOT="$TMP/no-installed" \
  DREAMING_SKIP_PLUGIN_SYNC=1 \
  DREAMING_LAUNCHD_PREFIX="com.fixture.dreaming" \
  SKILLS_LAUNCHD_PREFIX="com.fixture.skills" \
  SKILLS_LAUNCH_AGENTS_DIR="$DEST" \
  SKILLS_LAUNCHD_DOMAIN="gui/fixture" \
  SKILLS_STATE_DIR="$STATE" \
  SKILLS_LOCAL_ROOT="$LOCAL" \
  SKILLS_REPO_ROOT="$PUBLIC" \
  LAUNCHCTL_BIN="$FAKE" \
  DREAMING_SELFTEST_WAIT_SECS=2 \
    "$INSTALLER" "$@"
}

: > "$LAUNCHCTL_LOG"
run_install prepare >/dev/null
BACKUP="$(<"$STATE/dreaming/latest-migration-backup")"
[[ -f "$STATE/skill-review/disable-daemon" ]] ||
  { echo "prepare did not activate halt" >&2; exit 1; }
[[ "$(shasum -a 256 "$BACKUP/com.fixture.skills.sweep.plist" | awk '{print $1}')" == "$BEFORE_HASH" ]] ||
  { echo "prepare did not preserve exact plist bytes" >&2; exit 1; }
[[ "$(grep -c '^bootout ' "$LAUNCHCTL_LOG")" -ge 1 ]] ||
  { echo "prepare did not boot out installed labels" >&2; exit 1; }
echo "PASS  prepare halts, backs up exact bytes, then boots out labels"

: > "$LAUNCHCTL_LOG"
run_install install >/dev/null
INSTRUCTIONS="$TMP/copilot-home/instructions/dreaming.instructions.md"
[[ -f "$INSTRUCTIONS" && -f "$STATE/dreaming/managed-instructions.sha256" ]] ||
  { echo "install missed managed Copilot instructions" >&2; exit 1; }
status_output="$(run_install status)"
grep -q "managed-instructions=verified" <<<"$status_output"
grep -q "relevance-based retrieval" "$INSTRUCTIONS"
if grep -q "all memories load every turn" "$INSTRUCTIONS"; then
  echo "managed instructions use the obsolete Memory model" >&2
  exit 1
fi
for kind in dreaming selftest watchdog; do
  plist="$DEST/com.fixture.dreaming.$kind.plist"
  [[ -f "$plist" ]] || { echo "install missed $kind" >&2; exit 1; }
  grep -q "$ROOT/skills/skill-review/scripts/" "$plist"
  grep -q "<key>DREAMING_REPO_ROOT</key><string>$ROOT</string>" "$plist"
  grep -q "<key>DREAMING_SHARED_SKILLS_ROOT</key>" "$plist"
  grep -q "<key>DREAMING_DATA_DIR</key><string>$TMP/data</string>" "$plist"
  grep -q "<key>DREAMING_STATE_DIR</key><string>$TMP/dreaming-state</string>" "$plist"
  grep -q "<key>DREAMING_SKILLS_ROOT</key><string>$TMP/dreaming-skills</string>" "$plist"
  grep -q "<key>DREAMING_ORCHESTRATOR_STATE_DIR</key><string>$STATE/dreaming</string>" "$plist"
  grep -q "<key>COPILOT_HOME</key><string>$TMP/copilot-home</string>" "$plist"
done
[[ -d "$TMP/dreaming-skills/.git" ]] ||
  { echo "install did not initialize the neutral learned-skills root" >&2; exit 1; }
[[ -z "$(git -C "$TMP/dreaming-skills" remote)" ]] ||
  { echo "neutral learned-skills root unexpectedly has a remote" >&2; exit 1; }
first_bootstrap="$(grep -n '^bootstrap ' "$LAUNCHCTL_LOG" | head -1 | cut -d: -f1)"
last_bootout="$(grep -n '^bootout ' "$LAUNCHCTL_LOG" | tail -1 | cut -d: -f1)"
(( first_bootstrap > last_bootout )) ||
  { echo "install bootstrapped before prepare bootouts completed" >&2; exit 1; }
[[ "$(<"$STATE/dreaming/latest-migration-backup")" == "$BACKUP" ]] ||
  { echo "install changed exact rollback pointer" >&2; exit 1; }
if run_install enable >"$TMP/enable.out" 2>"$TMP/enable.err"; then
  echo "enable succeeded before selftest" >&2
  exit 1
fi
generation="$(<"$STATE/dreaming/activation-generation")"
if DREAMING_TEST_REPLACE_GENERATION=concurrent-install \
    run_install selftest >"$TMP/raced-selftest.out" 2>"$TMP/raced-selftest.err"; then
  echo "selftest accepted a result from a replaced activation generation" >&2
  exit 1
fi
grep -q "activation generation changed while selftest was running" \
  "$TMP/raced-selftest.err"
[[ ! -e "$STATE/dreaming/selftest-passed-generation" ]] ||
  { echo "raced selftest recorded a passing generation" >&2; exit 1; }
printf '%s\n' "$generation" > "$STATE/dreaming/activation-generation"
run_install selftest >/dev/null
run_install enable >/dev/null
[[ ! -e "$STATE/skill-review/disable-daemon" ]] ||
  { echo "enable did not remove halt after selftest" >&2; exit 1; }

BLOCK="$TMP/lifecycle-block"
DREAMING_TEST_BLOCK_DIR="$BLOCK" run_install install >"$TMP/raced-install.out" 2>&1 &
install_pid=$!
for _ in $(seq 1 100); do
  [[ -e "$BLOCK/entered" ]] && break
  sleep 0.05
done
[[ -e "$BLOCK/entered" ]] ||
  { echo "concurrent install did not reach the lifecycle hold point" >&2; exit 1; }
(
  set +e
  run_install enable >"$TMP/concurrent-enable.out" 2>"$TMP/concurrent-enable.err"
  printf '%s\n' "$?" > "$TMP/concurrent-enable.status"
) &
enable_pid=$!
sleep 0.2
[[ ! -e "$TMP/concurrent-enable.status" ]] ||
  { echo "enable bypassed the active lifecycle lock" >&2; exit 1; }
[[ -e "$STATE/skill-review/disable-daemon" ]] ||
  { echo "concurrent install did not preserve the halt" >&2; exit 1; }
touch "$BLOCK/release"
wait "$install_pid"
wait "$enable_pid"
[[ "$(<"$TMP/concurrent-enable.status")" != "0" ]] ||
  { echo "enable accepted the concurrently installed generation" >&2; exit 1; }
[[ -e "$STATE/skill-review/disable-daemon" ]] ||
  { echo "raced lifecycle commands cleared the halt" >&2; exit 1; }
echo "PASS  lifecycle lock serializes install, selftest, enable, and rollback"
echo "PASS  install ordering, rendered roots, generation-bound selftest, and exact backup pointer"

touch "$STATE/skill-review/disable-daemon"
rm "$OLD"
: > "$LAUNCHCTL_LOG"
if run_install rollback "$BACKUP" >"$TMP/rollback.out" 2>"$TMP/rollback.err"; then
  echo "rollback accepted a missing backed-up executable" >&2
  exit 1
fi
grep -q "executable missing or not executable" "$TMP/rollback.err"
[[ ! -s "$LAUNCHCTL_LOG" ]] ||
  { echo "rollback mutated launchd before validating all executables" >&2; exit 1; }
printf '#!/usr/bin/env bash\n:\n' > "$OLD"
chmod +x "$OLD"
run_install rollback "$BACKUP" >/dev/null
[[ -f "$STATE/skill-review/disable-daemon" ]] ||
  { echo "rollback removed halt" >&2; exit 1; }
for kind in sweep selftest watchdog; do
  restored_hash="$(shasum -a 256 "$DEST/com.fixture.skills.$kind.plist" | awk '{print $1}')"
  backup_hash="$(shasum -a 256 "$BACKUP/com.fixture.skills.$kind.plist" | awk '{print $1}')"
  [[ "$restored_hash" == "$backup_hash" ]] ||
    { echo "rollback did not restore exact $kind bytes" >&2; exit 1; }
done
if run_install enable >"$TMP/enable.out" 2>"$TMP/enable.err"; then
  echo "rollback enabled before restored selftest" >&2
  exit 1
fi
run_install selftest >/dev/null
run_install enable >/dev/null
echo "PASS  rollback refuses missing executables and requires restored selftest"

echo "post-install user edit" >> "$INSTRUCTIONS"
run_install uninstall >/dev/null 2>&1
grep -q "post-install user edit" "$INSTRUCTIONS" ||
  { echo "full uninstall removed a modified managed instruction" >&2; exit 1; }
for kind in dreaming selftest watchdog; do
  [[ ! -e "$DEST/com.fixture.dreaming.$kind.plist" ]] ||
    { echo "full uninstall retained LaunchAgent $kind" >&2; exit 1; }
done
echo "PASS  full uninstall retains edited instructions while removing jobs"

OWNERSHIP="$TMP/instruction-ownership"
mkdir -p "$OWNERSHIP"
run_instruction_manager() {
  COPILOT_HOME="$OWNERSHIP/copilot" \
  DREAMING_REPO_ROOT="$ROOT" \
  SKILLS_STATE_DIR="$OWNERSHIP/state" \
    "$ROOT/scripts/manage-instructions.sh" "$@"
}

run_instruction_manager install >/dev/null
run_instruction_manager verify >/dev/null
COPILOT_HOME="$OWNERSHIP/other-copilot" \
DREAMING_REPO_ROOT="$ROOT" \
SKILLS_STATE_DIR="$OWNERSHIP/state" \
  "$ROOT/scripts/manage-instructions.sh" uninstall >/dev/null
[[ ! -e "$OWNERSHIP/copilot/instructions/dreaming.instructions.md" ]] ||
  { echo "matching managed instruction survived uninstall" >&2; exit 1; }
[[ ! -e "$OWNERSHIP/state/dreaming/managed-instructions.sha256" ]] ||
  { echo "matching ownership hash survived uninstall" >&2; exit 1; }
[[ ! -e "$OWNERSHIP/state/dreaming/managed-instructions.target" ]] ||
  { echo "matching ownership target survived uninstall" >&2; exit 1; }

mkdir -p "$OWNERSHIP/copilot/instructions"
echo "user-owned instructions" > "$OWNERSHIP/copilot/instructions/dreaming.instructions.md"
if run_instruction_manager install >"$OWNERSHIP/out" 2>"$OWNERSHIP/err"; then
  echo "instruction manager overwrote an unowned file" >&2
  exit 1
fi
grep -q "unowned instruction" "$OWNERSHIP/err"
grep -q "user-owned instructions" "$OWNERSHIP/copilot/instructions/dreaming.instructions.md"

rm -f "$OWNERSHIP/copilot/instructions/dreaming.instructions.md"
run_instruction_manager install >/dev/null
echo "user edit" >> "$OWNERSHIP/copilot/instructions/dreaming.instructions.md"
run_instruction_manager uninstall >/dev/null 2>&1
grep -q "user edit" "$OWNERSHIP/copilot/instructions/dreaming.instructions.md" ||
  { echo "uninstall removed a modified managed instruction" >&2; exit 1; }
[[ -f "$OWNERSHIP/state/dreaming/managed-instructions.sha256" ]] ||
  { echo "modified managed instruction lost its ownership evidence" >&2; exit 1; }
echo "PASS  managed instruction install/uninstall ownership"

echo "installer tests: PASS"
