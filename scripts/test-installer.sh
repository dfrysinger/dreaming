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

run_persisted() {
  COPILOT_HOME="$TMP/copilot-home" \
  DREAMING_CONFIG_POINTER="$TMP/config-pointer" \
  DREAMING_SKIP_PLUGIN_SYNC=1 \
  DREAMING_LAUNCHD_PREFIX="com.fixture.dreaming" \
  SKILLS_LAUNCHD_PREFIX="com.fixture.skills" \
  SKILLS_LAUNCH_AGENTS_DIR="$DEST" \
  SKILLS_LAUNCHD_DOMAIN="gui/fixture" \
  SKILLS_STATE_DIR="$STATE" \
  SKILLS_LOCAL_ROOT="$LOCAL" \
  LAUNCHCTL_BIN="$FAKE" \
  DREAMING_SELFTEST_WAIT_SECS=2 \
    "$INSTALLER" "$@"
}

FAKE_PUBLISHER="$TMP/fake-publisher.py"
cat > "$FAKE_PUBLISHER" <<'PY'
#!/usr/bin/env python3
import json
import os
import sys

command = sys.argv[1]
if os.environ.get("ADAPTER_ENV_LOG"):
    keys = (
        "DREAMING_REPO_ROOT",
        "DREAMING_DATA_DIR",
        "DREAMING_STATE_DIR",
        "DREAMING_SKILLS_ROOT",
        "DREAMING_DEPS_DIR",
        "DREAMING_ADAPTER_CONFIG",
        "DREAMING_ENABLE_COPILOT_COMPAT",
        "DREAMING_CONFIG_FILE",
        "DREAMING_RECEIPT_FILE",
        "DREAMING_SHARED_SKILLS_ROOT",
    )
    with open(os.environ["ADAPTER_ENV_LOG"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps({key: os.environ.get(key) for key in keys}) + "\n")
if command == "contract":
    role = sys.argv[sys.argv.index("--role") + 1]
    contracts = {
        "session-source": (
            "dreaming.session-source",
            [
                "stable-pagination",
                "qualified-identity",
                "bounded-render",
                "revision-inspect",
            ],
        ),
        "review-executor": (
            "dreaming.review-executor",
            ["source-blind", "mutation-fence", "completion-sentinel"],
        ),
        "skill-publisher": (
            "dreaming.skill-publisher",
            [
                "content-addressed-bundle",
                "ownership-safe-remove",
                "exact-inventory",
            ],
        ),
    }
    protocol, capabilities = contracts[role]
    result = {
        "ok": True,
        "protocol": protocol,
        "version": 1,
        "adapter_id": "fixture",
        "capabilities": capabilities,
    }
elif command == "doctor":
    result = {"ok": True, "healthy": True, "boundary_ready": True}
elif command == "verify":
    bundle_id = sys.argv[sys.argv.index("--bundle-id") + 1]
    result = {"ok": True, "verified": True, "bundle_id": bundle_id}
else:
    result = {"ok": True, "status": command}
print(json.dumps(result))
PY
chmod +x "$FAKE_PUBLISHER"
SUCCESS_CONFIG="$TMP/success-adapters.json"
python3 - "$SUCCESS_CONFIG" "$FAKE_PUBLISHER" <<'PY'
import json
import sys

path, publisher = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "contract_version": 1,
            "sources": {"fixture": {"argv": [publisher]}},
            "executors": {"fixture": {"argv": [publisher]}},
            "publishers": {"fixture": {"argv": [publisher]}},
            "routes": ["fixture>fixture"],
            "executor_order": ["fixture"],
        },
        handle,
    )
PY

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

NATIVE="$TMP/native"
NATIVE_DEST="$NATIVE/LaunchAgents"
NATIVE_STATE="$NATIVE/compat-state"
NATIVE_LOCAL="$NATIVE/local"
NATIVE_PUBLIC="$NATIVE/public"
mkdir -p "$NATIVE_DEST" "$NATIVE_STATE" "$NATIVE_LOCAL" "$NATIVE_PUBLIC/skills"
git -C "$NATIVE_LOCAL" init -q
FAKE_COPILOT="$NATIVE/copilot"
FAKE_CODEX="$NATIVE/codex"
printf '#!/usr/bin/env bash\nexit 0\n' > "$FAKE_COPILOT"
printf '#!/usr/bin/env bash\nexit 0\n' > "$FAKE_CODEX"
chmod +x "$FAKE_COPILOT" "$FAKE_CODEX"
run_native() {
  COPILOT_HOME="$NATIVE/copilot-home" \
  DREAMING_REPO_ROOT="$ROOT" \
  DREAMING_CONFIG_POINTER="$NATIVE/config-pointer" \
  DREAMING_DATA_DIR="$NATIVE/data" \
  DREAMING_STATE_DIR="$NATIVE/dreaming-state" \
  DREAMING_SKILLS_ROOT="$NATIVE/dreaming-skills" \
  DREAMING_CONFIG_FILE="$NATIVE/config.env" \
  DREAMING_DEPS_DIR="$NATIVE/deps" \
  DREAMING_RECEIPT_FILE="$RECEIPT" \
  DREAMING_DEPS_SOURCE="$SOURCE" \
  DREAMING_CANONICAL_SKILLS_ROOT="" \
  DREAMING_INSTALLED_PLUGINS_ROOT="$NATIVE/no-installed" \
  DREAMING_SKIP_PLUGIN_SYNC=1 \
  DREAMING_LAUNCHD_PREFIX="com.fixture.native" \
  SKILLS_LAUNCHD_PREFIX="com.fixture.native-legacy" \
  SKILLS_LAUNCH_AGENTS_DIR="$NATIVE_DEST" \
  SKILLS_LAUNCHD_DOMAIN="gui/native" \
  SKILLS_STATE_DIR="$NATIVE_STATE" \
  SKILLS_LOCAL_ROOT="$NATIVE_LOCAL" \
  SKILLS_REPO_ROOT="$NATIVE_PUBLIC" \
  LAUNCHCTL_BIN="$FAKE" \
    "$INSTALLER" "$@"
}
run_native_persisted() {
  COPILOT_HOME="$NATIVE/copilot-home" \
  DREAMING_CONFIG_POINTER="$NATIVE/config-pointer" \
  DREAMING_RECEIPT_FILE="$RECEIPT" \
  DREAMING_DEPS_SOURCE="$SOURCE" \
  DREAMING_CANONICAL_SKILLS_ROOT="" \
  DREAMING_INSTALLED_PLUGINS_ROOT="$NATIVE/no-installed" \
  DREAMING_SKIP_PLUGIN_SYNC=1 \
  DREAMING_LAUNCHD_PREFIX="com.fixture.native" \
  SKILLS_LAUNCHD_PREFIX="com.fixture.native-legacy" \
  SKILLS_LAUNCH_AGENTS_DIR="$NATIVE_DEST" \
  SKILLS_LAUNCHD_DOMAIN="gui/native" \
  SKILLS_STATE_DIR="$NATIVE_STATE" \
  SKILLS_LOCAL_ROOT="$NATIVE_LOCAL" \
  LAUNCHCTL_BIN="$FAKE" \
    "$INSTALLER" "$@"
}

(
  export DREAMING_ENABLE_COPILOT_COMPAT=0
  export DREAMING_CONFIGURE_NATIVE_ADAPTERS=1
  export DREAMING_SESSION_SOURCES=copilot
  export DREAMING_REVIEW_EXECUTORS=copilot
  export DREAMING_SKILL_TARGETS=copilot
  export DREAMING_SOURCE_EXECUTOR_ALLOW='copilot>copilot'
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  run_native install >/dev/null
)
NATIVE_ADAPTERS="$NATIVE/dreaming-state/adapters.json"
grep -q '"copilot"' "$NATIVE_ADAPTERS"
native_hash="$(shasum -a 256 "$NATIVE_ADAPTERS" | awk '{print $1}')"
(
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  run_native_persisted install >/dev/null
)
[[ "$(shasum -a 256 "$NATIVE_ADAPTERS" | awk '{print $1}')" == "$native_hash" ]] ||
  { echo "ordinary reinstall rewrote persisted adapter config" >&2; exit 1; }
(
  export DREAMING_SESSION_SOURCES=codex
  export DREAMING_REVIEW_EXECUTORS=codex
  export DREAMING_SKILL_TARGETS=codex
  export DREAMING_SOURCE_EXECUTOR_ALLOW='codex>codex'
  export DREAMING_CODEX_BIN="$FAKE_CODEX"
  run_native_persisted install >/dev/null
)
grep -q '"codex"' "$NATIVE_ADAPTERS" || {
  echo "selection change did not generate Codex adapter config:" >&2
  cat "$NATIVE_ADAPTERS" >&2
  exit 1
}
python3 - "$NATIVE_ADAPTERS" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
for key in ("sources", "executors", "publishers"):
    if set(config[key]) != {"codex"}:
        raise SystemExit(f"selection change retained stale active adapters: {config!r}")
if config["routes"] != ["codex>codex"] or config["executor_order"] != ["codex"]:
    raise SystemExit(f"selection change retained stale routing: {config!r}")
PY
EXTERNAL_CONFIG="$NATIVE/external-adapters.json"
printf '{"externally_managed":true}\n' > "$EXTERNAL_CONFIG"
external_hash="$(shasum -a 256 "$EXTERNAL_CONFIG" | awk '{print $1}')"
(
  export DREAMING_ADAPTER_CONFIG="$EXTERNAL_CONFIG"
  export DREAMING_CONFIGURE_NATIVE_ADAPTERS=1
  export DREAMING_SESSION_SOURCES=codex
  export DREAMING_REVIEW_EXECUTORS=codex
  export DREAMING_SKILL_TARGETS=codex
  export DREAMING_SOURCE_EXECUTOR_ALLOW='codex>codex'
  export DREAMING_CODEX_BIN="$FAKE_CODEX"
  run_native install >/dev/null
)
[[ "$(shasum -a 256 "$EXTERNAL_CONFIG" | awk '{print $1}')" == "$external_hash" ]] ||
  { echo "installer overwrote externally managed adapter config" >&2; exit 1; }
echo "PASS  desired adapter state regenerates only when explicitly requested"

(
  export DREAMING_ADAPTER_CONFIG="$SUCCESS_CONFIG"
  run_install install >/dev/null
)
RELOCATED_CONFIG="$TMP/relocated-config.env"
STALE_CONFIG="$TMP/stale-config.env"
cp "$TMP/config.env" "$RELOCATED_CONFIG"
printf 'DREAMING_CONFIG_FILE=%q\n' "$STALE_CONFIG" >> "$RELOCATED_CONFIG"
(
  export DREAMING_CONFIG_FILE="$RELOCATED_CONFIG"
  export ADAPTER_ENV_LOG="$TMP/adapter-env.log"
  run_persisted selftest >/dev/null
)
(
  export DREAMING_CONFIG_FILE="$RELOCATED_CONFIG"
  export ADAPTER_ENV_LOG="$TMP/adapter-env.log"
  if ! run_persisted enable >"$TMP/persisted-enable.out" \
      2>"$TMP/persisted-enable.err"; then
    cat "$TMP/persisted-enable.out" "$TMP/persisted-enable.err" >&2
    exit 1
  fi
)
grep -q '"ok": true' "$TMP/persisted-enable.out"
EXPECTED_SHARED="$(
  set +u
  # shellcheck disable=SC1090
  source "$TMP/config.env"
  printf '%s\n' "$DREAMING_SHARED_SKILLS_ROOT"
)"
python3 - "$TMP/adapter-env.log" "$ROOT" "$TMP/data" \
  "$TMP/dreaming-state" "$TMP/dreaming-skills" "$TMP/deps" \
  "$SUCCESS_CONFIG" "$RELOCATED_CONFIG" "$RECEIPT" "$EXPECTED_SHARED" <<'PY'
import json
import sys

log, repo, data, state, skills, deps, adapters, config, receipt, shared = sys.argv[1:]
records = [json.loads(line) for line in open(log, encoding="utf-8")]
expected = {
    "DREAMING_REPO_ROOT": repo,
    "DREAMING_DATA_DIR": data,
    "DREAMING_STATE_DIR": state,
    "DREAMING_SKILLS_ROOT": skills,
    "DREAMING_DEPS_DIR": deps,
    "DREAMING_ADAPTER_CONFIG": adapters,
    "DREAMING_ENABLE_COPILOT_COMPAT": "1",
    "DREAMING_CONFIG_FILE": config,
    "DREAMING_RECEIPT_FILE": receipt,
    "DREAMING_SHARED_SKILLS_ROOT": shared,
}
if not records or any(record != expected for record in records):
    raise SystemExit(f"persisted runtime environment mismatch: {records!r}")
PY
[[ ! -e "$STALE_CONFIG" ]] ||
  { echo "installer used stale self-referenced config path" >&2; exit 1; }
echo "PASS  explicit config path overrides persisted self-reference"
echo "PASS  persisted config pointer exports complete runtime environment"

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
[[ ! -e "$INSTRUCTIONS" ]] ||
  { echo "rollback retained the managed Copilot instruction" >&2; exit 1; }
echo "PASS  rollback refuses missing executables and requires restored selftest"

run_install install >/dev/null
MIGRATION_LEGACY_SKILLS="$TMP/migration-legacy-skills"
MIGRATION_LEGACY_STATE="$TMP/migration-legacy-state"
mkdir -p "$MIGRATION_LEGACY_SKILLS/learned" "$MIGRATION_LEGACY_STATE"
printf '%s\n' '---' 'name: learned' 'description: installer migration fixture' '---' \
  > "$MIGRATION_LEGACY_SKILLS/learned/SKILL.md"
git -C "$MIGRATION_LEGACY_SKILLS" init -q
git -C "$MIGRATION_LEGACY_SKILLS" add learned/SKILL.md
git -C "$MIGRATION_LEGACY_SKILLS" \
  -c user.name=fixture -c user.email=fixture@example.invalid \
  commit -qm fixture
printf '[{"session_id":"legacy"}]\n' \
  > "$MIGRATION_LEGACY_STATE/review-ledger.json"
printf '[{"session_id":"queued"}]\n' > "$MIGRATION_LEGACY_STATE/queue.json"
"$ROOT/scripts/migrate-copilot-state.py" apply \
  --legacy-skills "$MIGRATION_LEGACY_SKILLS" \
  --legacy-state "$MIGRATION_LEGACY_STATE" \
  --target-skills "$TMP/dreaming-skills" \
  --target-state "$TMP/dreaming-state" \
  --journal "$TMP/dreaming-state/copilot-migration.json" >/dev/null
printf '[{"session_id":"changed-after-migration"}]\n' \
  > "$TMP/dreaming-state/queue.json"
(
  export DREAMING_LEGACY_COPILOT_SKILLS="$MIGRATION_LEGACY_SKILLS"
  export DREAMING_LEGACY_COPILOT_STATE="$MIGRATION_LEGACY_STATE"
  run_install rollback "$BACKUP" >/dev/null
)
grep -q '"status": "active"' "$TMP/dreaming-state/copilot-migration.json"
grep -q 'changed-after-migration' "$TMP/dreaming-state/queue.json"
[[ -f "$TMP/dreaming-skills/learned/SKILL.md" ]]
[[ -f "$TMP/dreaming-state/review-ledger.json" ]]
echo "PASS  agent rollback leaves changed migrated audit data intact"

if (
  export DREAMING_LEGACY_COPILOT_SKILLS="$MIGRATION_LEGACY_SKILLS"
  export DREAMING_LEGACY_COPILOT_STATE="$MIGRATION_LEGACY_STATE"
  run_install rollback-migration >"$TMP/rollback-migration.out" \
    2>"$TMP/rollback-migration.err"
); then
  echo "rollback-migration accepted changed migrated state" >&2
  exit 1
fi
grep -q "neutral state changed after migration" "$TMP/rollback-migration.err"
grep -q '"status": "active"' "$TMP/dreaming-state/copilot-migration.json"
grep -q 'changed-after-migration' "$TMP/dreaming-state/queue.json"
[[ -f "$TMP/dreaming-skills/learned/SKILL.md" ]]
[[ -f "$TMP/dreaming-state/review-ledger.json" ]]
[[ -f "$STATE/skill-review/disable-daemon" ]]
echo "PASS  explicit migration rollback fails closed without partial deletion"

run_install install >/dev/null
echo "post-install user edit" >> "$INSTRUCTIONS"
FAIL_CONFIG="$TMP/failing-adapters.json"
cat > "$FAIL_CONFIG" <<JSON
{
  "contract_version": 1,
  "sources": {},
  "executors": {},
  "publishers": {
    "missing": {"argv": ["$TMP/missing-publisher"]}
  },
  "routes": [],
  "executor_order": []
}
JSON
(
  export DREAMING_ADAPTER_CONFIG="$FAIL_CONFIG"
  run_install uninstall >"$TMP/uninstall.out" 2>"$TMP/uninstall.err"
)
grep -q "publication cleanup incomplete; residual registrations may remain" \
  "$TMP/uninstall.err"
grep -q '"ok": false' "$TMP/uninstall.err"
grep -q "publication residuals remain" "$TMP/uninstall.out"
grep -q "post-install user edit" "$INSTRUCTIONS" ||
  { echo "full uninstall removed a modified managed instruction" >&2; exit 1; }
for kind in dreaming selftest watchdog; do
  [[ ! -e "$DEST/com.fixture.dreaming.$kind.plist" ]] ||
    { echo "full uninstall retained LaunchAgent $kind" >&2; exit 1; }
done
echo "PASS  uninstall reports publication residuals and still removes managed jobs"

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
