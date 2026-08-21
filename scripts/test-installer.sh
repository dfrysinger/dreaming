#!/usr/bin/env bash
set -euo pipefail

# The fixture owns its desired state; ambient installed-runtime values must not
# turn generated adapter selections into an explicit external configuration.
unset DREAMING_ADAPTER_CONFIG
unset DREAMING_INSTALLER_CALLER_ADAPTER_CONFIG_EXPLICIT
unset DREAMING_INSTALLER_CALLER_ADAPTER_DESIRED_STATE

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
git -C "$LOCAL" config core.hooksPath /dev/null
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
  DREAMING_SKIP_DASHBOARD_HEALTH_CHECK="${DREAMING_SKIP_DASHBOARD_HEALTH_CHECK:-1}" \
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
  DREAMING_SKIP_DASHBOARD_HEALTH_CHECK="${DREAMING_SKIP_DASHBOARD_HEALTH_CHECK:-1}" \
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
grep -q "autonomous end-of-task review paused" "$INSTRUCTIONS"
if grep -q "dispatch .*skill-review.*without asking" "$INSTRUCTIONS"; then
  echo "managed instructions retained autonomous end-of-task dispatch" >&2
  exit 1
fi
SWEEP_PROMPT="$ROOT/skills/skill-review/references/sweep-prompt.txt"
grep -q "Create zero new skills" "$SWEEP_PROMPT"
grep -q "autonomous-create-requires-recurrence" "$SWEEP_PROMPT"
grep -q "DREAM_PASS_RESULT: ok created=0" "$SWEEP_PROMPT"
if grep -q "Create at most .*NEW skills" "$SWEEP_PROMPT" ||
    grep -q "mark new skills" "$SWEEP_PROMPT"; then
  echo "sweep prompt retained autonomous creation authority" >&2
  exit 1
fi
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
  if [[ "$kind" == "selftest" ]]; then
    if grep -q "<key>SKILLS_REVIEW_STATE_DIR</key>" "$plist"; then
      echo "installed selftest inherited production review state" >&2
      exit 1
    fi
    grep -q "<string>-u</string>" "$plist"
    grep -q "<string>SKILLS_REVIEW_STATE_DIR</string>" "$plist"
  else
    grep -q \
      "<key>SKILLS_REVIEW_STATE_DIR</key><string>$STATE/skill-review</string>" \
      "$plist"
  fi
done
dreaming_plist="$DEST/com.fixture.dreaming.dreaming.plist"
python3 - "$dreaming_plist" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as handle:
    plist = plistlib.load(handle)
if plist.get("StartInterval") != 14400:
    raise SystemExit(
        f"dreaming schedule interval is not exactly 14400: "
        f"{plist.get('StartInterval')!r}"
    )
if "StartCalendarInterval" in plist:
    raise SystemExit("dreaming schedule retained the daily calendar interval")
PY
dashboard_plist="$DEST/com.fixture.dreaming.dashboard.plist"
[[ -f "$dashboard_plist" ]] ||
  { echo "install missed dashboard" >&2; exit 1; }
grep -q "$ROOT/skills/skill-review/scripts/dreaming-dashboard.py" "$dashboard_plist"
grep -q "<key>SKILLS_STATE_DIR</key><string>$STATE</string>" "$dashboard_plist"
grep -q "<key>DREAMING_DASHBOARD_TOKEN_FILE</key>" "$dashboard_plist"
! grep -q "<key>DREAMING_DASHBOARD_TAILNET_HOST</key>" "$dashboard_plist"
dashboard_token="$TMP/dreaming-state/dashboard/access-token"
[[ -f "$dashboard_token" && "$(stat -f '%Lp' "$dashboard_token")" == "600" ]] ||
  { echo "install did not create a protected dashboard token" >&2; exit 1; }
token_before="$(<"$dashboard_token")"
[[ -d "$TMP/dreaming-skills/.git" ]] ||
  { echo "install did not initialize the neutral learned-skills root" >&2; exit 1; }
[[ -z "$(git -C "$TMP/dreaming-skills" remote)" ]] ||
  { echo "neutral learned-skills root unexpectedly has a remote" >&2; exit 1; }
first_bootstrap="$(grep -n '^bootstrap ' "$LAUNCHCTL_LOG" | head -1 | cut -d: -f1)"
last_bootout="$(grep -n '^bootout ' "$LAUNCHCTL_LOG" | tail -1 | cut -d: -f1)"
(( first_bootstrap > last_bootout )) ||
  { echo "install bootstrapped before prepare bootouts completed" >&2; exit 1; }
run_install install >/dev/null
[[ "$(<"$dashboard_token")" == "$token_before" ]] ||
  { echo "reinstall rotated the dashboard token" >&2; exit 1; }
dashboard_url="$(run_install dashboard-url)"
[[ "$dashboard_url" == "http://127.0.0.1:47673/#access_token=$token_before" ]] ||
  { echo "dashboard URL does not use fragment bootstrap" >&2; exit 1; }
if DREAMING_DASHBOARD_HOST=localhost run_install status \
    >"$TMP/non-loopback.out" 2>"$TMP/non-loopback.err"; then
  echo "installer accepted a non-loopback dashboard host" >&2
  exit 1
fi
grep -q "DREAMING_DASHBOARD_HOST must be 127.0.0.1" "$TMP/non-loopback.err"
if DREAMING_DASHBOARD_TAILNET_HOST=https://mac-mini.example.ts.net:47673 \
    run_install status >"$TMP/invalid-tailnet.out" 2>"$TMP/invalid-tailnet.err"; then
  echo "installer accepted a malformed tailnet dashboard host" >&2
  exit 1
fi
grep -q "DREAMING_DASHBOARD_TAILNET_HOST must be an exact lowercase .ts.net host" \
  "$TMP/invalid-tailnet.err"
DREAMING_DASHBOARD_TAILNET_HOST=mac-mini.example.ts.net:47673 \
  run_install install >/dev/null
grep -q \
  "<key>DREAMING_DASHBOARD_TAILNET_HOST</key><string>mac-mini.example.ts.net:47673</string>" \
  "$dashboard_plist"
tailnet_status="$(
  DREAMING_DASHBOARD_TAILNET_HOST=mac-mini.example.ts.net:47673 \
    run_install status
)"
grep -q '^dashboard_tailnet_url=https://mac-mini.example.ts.net:47673/$' \
  <<<"$tailnet_status"
run_install install >/dev/null
! grep -q "<key>DREAMING_DASHBOARD_TAILNET_HOST</key>" "$dashboard_plist"
[[ "$(<"$dashboard_token")" == "$token_before" ]] ||
  { echo "invalid dashboard host changed the dashboard token" >&2; exit 1; }
status_output="$(run_install status)"
grep -q '^dashboard_url=http://127.0.0.1:47673/$' <<<"$status_output"
grep -q '^dashboard_token=ready$' <<<"$status_output"
echo "PASS  installer owns one dashboard job and preserves its protected fragment token"
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
if DREAMING_SKIP_DASHBOARD_HEALTH_CHECK=0 \
    DREAMING_DASHBOARD_HEALTH_WAIT_SECS=0.1 \
    DREAMING_DASHBOARD_PORT=1 \
    run_install selftest >"$TMP/failed-health.out" 2>"$TMP/failed-health.err"; then
  echo "selftest accepted a failed dashboard health check" >&2
  exit 1
fi
[[ ! -e "$STATE/dreaming/selftest-passed-generation" ]] ||
  { echo "failed dashboard health recorded a passing generation" >&2; exit 1; }
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

LOCAL_PUBLISHER_LOG="$NATIVE/local-publisher.log"
FAKE_LOCAL_PUBLISHER="$NATIVE/fake-local-publisher.py"
cat > "$FAKE_LOCAL_PUBLISHER" <<'PY'
#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["LOCAL_PUBLISHER_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\n")
print(json.dumps({"ok": True, "removed": True}))
PY
chmod +x "$FAKE_LOCAL_PUBLISHER"
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
python3 - \
  "$NATIVE_DEST/com.fixture.native.dreaming.plist" \
  "$NATIVE/dreaming-state/adapters.json" <<'PY'
import hashlib
import plistlib
import sys

plist_path, config_path = sys.argv[1:]
with open(plist_path, "rb") as handle:
    environment = plistlib.load(handle)["EnvironmentVariables"]
with open(config_path, "rb") as handle:
    digest = hashlib.sha256(handle.read()).hexdigest()
if environment.get("DREAMING_ADAPTER_CONFIG_MANAGED") != "1":
    raise SystemExit("managed adapter authority was not installed")
if environment.get("DREAMING_ADAPTER_CONFIG_SHA256") != digest:
    raise SystemExit("installed adapter digest does not bind the managed config")
PY
python3 - "$NATIVE/dreaming-state/adapters.json" "$FAKE_LOCAL_PUBLISHER" <<'PY'
import json
import sys

path = sys.argv[1]
config = json.load(open(path, encoding="utf-8"))
config["publishers"]["copilot"]["argv"] = [sys.executable, sys.argv[2]]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(config, handle)
PY
if (
  export DREAMING_ENABLE_COPILOT_COMPAT=0
  export DREAMING_CONFIGURE_NATIVE_ADAPTERS=1
  export DREAMING_SESSION_SOURCES=copilot
  export DREAMING_REVIEW_EXECUTORS=copilot
  export DREAMING_SKILL_TARGETS=copilot
  export DREAMING_SOURCE_EXECUTOR_ALLOW='copilot>copilot'
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  export DREAMING_COPILOT_PUBLISHER_SSH_HOST='fixture-client@fd7a:115c:a1e0::3'
  export DREAMING_REQUIRE_REMOTE_COPILOT_PUBLISHER=1
  export LOCAL_PUBLISHER_LOG
  run_native install >/dev/null 2>&1
); then
  echo "invalid remote publisher configuration passed preflight" >&2
  exit 1
fi
[[ ! -e "$LOCAL_PUBLISHER_LOG" ]] ||
  { echo "local publisher was removed before remote preflight passed" >&2; exit 1; }
ESTATE_BASELINE_SOURCE="$NATIVE/estate-adapters-baseline.source.json"
cp "$NATIVE/dreaming-state/adapters.json" "$ESTATE_BASELINE_SOURCE"
python3 - "$ESTATE_BASELINE_SOURCE" <<'PY'
import json
import sys

path = sys.argv[1]
config = json.load(open(path, encoding="utf-8"))
config["estate_census"] = {
    "argv": [
        sys.executable,
        "/baseline/scripts/ssh-estate-census.py",
        "--ssh-bin",
        "/usr/bin/ssh",
        "--host",
        "baseline-host",
        "--remote-python",
        "/baseline/python3",
        "--remote-script",
        "/baseline/census-receiver.py",
        "--remote-estate-script",
        "/baseline/dreaming-estate.py",
        "--remote-receiver-id-file",
        "/baseline/receiver-id",
        "--remote-copilot-binary",
        "/baseline/copilot",
        "--remote-usage-index-path",
        "/baseline/usage.json",
        "--usage-max-sessions",
        "10000",
        "--usage-max-bytes",
        "1073741824",
        "--expected-receiver-id",
        "baseline-receiver",
        "--target-home",
        "/baseline/home",
        "--timeout",
        "180",
    ],
    "timeout": 210,
}
config["estate_curator"] = {
    "argv": [
        sys.executable,
        "/baseline/scripts/ssh-estate-curator.py",
        "--remote-script",
        "/baseline/curator-receiver.py",
        "--remote-curator-runner",
        "/baseline/curator-run.py",
        "--remote-archive-tool",
        "/baseline/archive.sh",
        "--remote-restore-tool",
        "/baseline/restore.sh",
        "--remote-dependency-scanner",
        "/baseline/dependencies.py",
        "--remote-public-root",
        "/baseline/public",
        "--remote-personal-root",
        "/baseline/personal",
        "--remote-review-state-dir",
        "/baseline/review",
        "--remote-runs-dir",
        "/baseline/runs",
        "--remote-curator-state-file",
        "/baseline/curator.json",
        "--remote-halt-switch",
        "/baseline/halt",
        "--remote-lock-dir",
        "/baseline/lock.sqlite",
        "--remote-operation-root",
        "/baseline/operations",
        "--remote-recovery-state",
        "/baseline/recovery.json",
        "--remote-user-context-cwd",
        "/baseline/home",
        "--timeout",
        "300",
        "--host",
        "baseline-host",
        "--expected-receiver-id",
        "baseline-receiver",
    ],
    "enabled": False,
    "timeout": 330,
}
with open(path, "w", encoding="utf-8") as output:
    json.dump(config, output, sort_keys=True)
PY
ESTATE_BASELINE_SHA="$(
  shasum -a 256 "$ESTATE_BASELINE_SOURCE" | awk '{print $1}'
)"
REMOTE_KNOWN_HOSTS_SOURCE="$NATIVE/remote-subject-known-hosts.source"
printf 'fixture-host-key\n' > "$REMOTE_KNOWN_HOSTS_SOURCE"
(
  export DREAMING_ENABLE_COPILOT_COMPAT=0
  export DREAMING_CONFIGURE_NATIVE_ADAPTERS=1
  export DREAMING_SESSION_SOURCES=copilot
  export DREAMING_REVIEW_EXECUTORS=copilot
  export DREAMING_SKILL_TARGETS=copilot
  export DREAMING_SOURCE_EXECUTOR_ALLOW='copilot>copilot'
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  export DREAMING_COPILOT_SOURCE_SSH_HOST='fixture@fd7a:115c:a1e0::1'
  export DREAMING_COPILOT_SOURCE_SSH_ADDRESS_FAMILY=6
  export DREAMING_COPILOT_SOURCE_SSH_PYTHON='/fixture/python3'
  export DREAMING_COPILOT_SOURCE_SSH_SCRIPT='/fixture/dreaming-vendor-adapter.py'
  export DREAMING_COPILOT_PUBLISHER_SSH_HOST='fixture-client@fd7a:115c:a1e0::3'
  export DREAMING_COPILOT_PUBLISHER_SSH_ADDRESS_FAMILY=6
  export DREAMING_COPILOT_PUBLISHER_RECEIVER_ID='fixture-receiver'
  export DREAMING_REQUIRE_REMOTE_COPILOT_PUBLISHER=1
  export DREAMING_CONFIGURE_EVALUATION_INPUT_OWNER=1
  export DREAMING_EVALUATION_INPUT_OWNER_ENABLED=0
  export DREAMING_EVALUATION_INPUT_AUTHOR_MODEL=author-model
  export DREAMING_EVALUATION_INPUT_REVIEWER_A_MODEL=reviewer-a-model
  export DREAMING_EVALUATION_INPUT_REVIEWER_B_MODEL=reviewer-b-model
  export DREAMING_PRESERVE_ESTATE_ADAPTERS=1
  export DREAMING_ESTATE_ADAPTERS_BASELINE_SOURCE="$ESTATE_BASELINE_SOURCE"
  export DREAMING_ESTATE_ADAPTERS_BASELINE_SHA256="$ESTATE_BASELINE_SHA"
  export DREAMING_CONFIGURE_REMOTE_EVALUATION_SUBJECTS=1
  export DREAMING_REMOTE_EVALUATION_SUBJECTS_ENABLED=0
  export DREAMING_REMOTE_SUBJECT_SSH_HOST='fixture@fd7a:115c:a1e0::1'
  export DREAMING_REMOTE_SUBJECT_SSH_ADDRESS_FAMILY=6
  export DREAMING_REMOTE_SUBJECT_SSH_SCRIPT='/fixture/remote-subject/ssh-estate-census.py'
  export DREAMING_REMOTE_SUBJECT_ESTATE_SCRIPT='/fixture/remote-subject/dreaming-estate.py'
  export DREAMING_REMOTE_SUBJECT_CONTENT_POLICY='/fixture/remote-subject/content-policy.json'
  export DREAMING_REMOTE_SUBJECT_KNOWN_HOSTS_SOURCE="$REMOTE_KNOWN_HOSTS_SOURCE"
  export DREAMING_REMOTE_SUBJECT_ORIGIN_HOST_ID=fixture-receiver
  export DREAMING_REMOTE_SUBJECT_RECEIVER_ID=fixture-receiver
  export LOCAL_PUBLISHER_LOG
  run_native install >/dev/null
)
python3 - "$LOCAL_PUBLISHER_LOG" <<'PY'
import json
import sys

calls = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
if calls != [["remove"]]:
    raise SystemExit(f"local publisher was not retired exactly once: {calls!r}")
PY
NATIVE_ADAPTERS="$NATIVE/dreaming-state/adapters.json"
grep -q '"copilot"' "$NATIVE_ADAPTERS"
cmp "$REMOTE_KNOWN_HOSTS_SOURCE" \
  "$NATIVE/dreaming-state/remote-subject-known-hosts"
[[ "$(stat -f '%Lp' "$NATIVE/dreaming-state/remote-subject-known-hosts")" == "600" ]]
cmp "$ESTATE_BASELINE_SOURCE" \
  "$NATIVE/dreaming-state/estate-adapters-baseline.json"
[[ "$(stat -f '%Lp' "$NATIVE/dreaming-state/estate-adapters-baseline.json")" == "600" ]]
python3 - "$NATIVE_ADAPTERS" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
def keys(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key)
            yield from keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from keys(nested)

leaked = sorted(
    key
    for key in keys(config)
    if "schedule" in key.lower() or "start_interval" in key.lower()
)
if leaked:
    raise SystemExit(f"adapter configuration contains schedule state: {leaked}")
PY
python3 - "$NATIVE_ADAPTERS" "$FAKE_COPILOT" "$ESTATE_BASELINE_SOURCE" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
baseline = json.load(open(sys.argv[3], encoding="utf-8"))
for key in ("estate_census", "estate_curator"):
    if config.get(key) != baseline.get(key):
        raise SystemExit(f"{key} changed during remote subject rollout")
argv = config["sources"]["copilot"]["argv"]
expected = [
    config["sources"]["copilot"]["argv"][0],
    config["sources"]["copilot"]["argv"][1],
    "--ssh-bin",
    "/usr/bin/ssh",
    "--host",
    "fixture@fd7a:115c:a1e0::1",
    "--address-family",
    "6",
    "--remote-python",
    "/fixture/python3",
    "--remote-script",
    "/fixture/dreaming-vendor-adapter.py",
    "--",
]
if argv[: len(expected)] != expected:
    raise SystemExit(f"remote source adapter is malformed: {argv!r}")
if not argv[1].endswith("/scripts/ssh-session-source.py"):
    raise SystemExit(f"remote source proxy is missing: {argv!r}")
if config["executors"]["copilot"]["argv"][1].endswith(
    "/scripts/ssh-session-source.py"
):
    raise SystemExit("remote source configuration changed the local executor")
executor = config["executors"]["copilot"]["argv"]
if executor[executor.index("--binary") + 1] != sys.argv[2]:
    raise SystemExit(f"local executor binary is not pinned: {executor!r}")
publisher = config["publishers"]["copilot"]["argv"]
if not publisher[1].endswith("/scripts/ssh-skill-publisher.py"):
    raise SystemExit(f"remote publisher proxy is missing: {publisher!r}")
if publisher[publisher.index("--host") + 1] != "fixture-client@fd7a:115c:a1e0::3":
    raise SystemExit(f"remote publisher host is malformed: {publisher!r}")
if publisher[publisher.index("--expected-receiver-id") + 1] != "fixture-receiver":
    raise SystemExit(f"remote publisher identity is malformed: {publisher!r}")
if "--ownership-journal" in publisher:
    raise SystemExit(f"mini-local publisher journal leaked into remote argv: {publisher!r}")
estate = config.get("estate_census", {}).get("argv", [])
if not estate or not estate[1].endswith("/scripts/ssh-estate-census.py"):
    raise SystemExit(f"remote estate census is missing: {config!r}")
if "--fetch-subject" in estate:
    raise SystemExit("existing census route was changed into a subject route")
owner = config.get("evaluation_input_owner", {})
if owner.get("enabled") is not False:
    raise SystemExit(f"evaluation-input owner was unexpectedly enabled: {owner!r}")
remote = config.get("remote_evaluation_subjects", {})
remote_argv = remote.get("command", [])
if (
    remote.get("enabled") is not False
    or remote.get("protocol_version") != 1
    or remote.get("origin_host_id") != "fixture-receiver"
    or "--fetch-subject" not in remote_argv
):
    raise SystemExit(f"disabled remote subject route is malformed: {remote!r}")
if remote_argv[remote_argv.index("--known-hosts-file") + 1].endswith(
    ".source"
):
    raise SystemExit("remote subject route did not use installed host-key evidence")
if remote_argv[remote_argv.index("--remote-script") + 1] == estate[
    estate.index("--remote-script") + 1
]:
    raise SystemExit("remote subject route reused the census receiver path")
curator = config.get("estate_curator", {})
curator_argv = curator.get("argv", [])
if (
    curator.get("enabled") is not False
    or not curator_argv
    or not curator_argv[1].endswith("/scripts/ssh-estate-curator.py")
):
    raise SystemExit(f"disabled remote estate curator route is missing: {config!r}")
if "--request" in curator_argv:
    raise SystemExit("estate curator route must remain unarmed without a sealed request")
PY
FULL_BASELINE_SOURCE="$NATIVE/operational-adapters-baseline.source.json"
cp "$NATIVE_ADAPTERS" "$FULL_BASELINE_SOURCE"
FULL_BASELINE_SHA="$(
  shasum -a 256 "$FULL_BASELINE_SOURCE" | awk '{print $1}'
)"
(
  export DREAMING_ENABLE_COPILOT_COMPAT=0
  export DREAMING_CONFIGURE_NATIVE_ADAPTERS=1
  export DREAMING_SESSION_SOURCES=copilot
  export DREAMING_REVIEW_EXECUTORS=copilot
  export DREAMING_SKILL_TARGETS=copilot
  export DREAMING_SOURCE_EXECUTOR_ALLOW='copilot>copilot'
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  export DREAMING_COPILOT_SOURCE_SSH_HOST='fixture@fd7a:115c:a1e0::1'
  export DREAMING_COPILOT_SOURCE_SSH_ADDRESS_FAMILY=6
  export DREAMING_COPILOT_SOURCE_SSH_PYTHON='/fixture/python3'
  export DREAMING_COPILOT_SOURCE_SSH_SCRIPT='/fixture/dreaming-vendor-adapter.py'
  export DREAMING_COPILOT_PUBLISHER_SSH_HOST='fixture-client@fd7a:115c:a1e0::3'
  export DREAMING_COPILOT_PUBLISHER_SSH_ADDRESS_FAMILY=6
  export DREAMING_COPILOT_PUBLISHER_RECEIVER_ID='fixture-receiver'
  export DREAMING_REQUIRE_REMOTE_COPILOT_PUBLISHER=1
  export DREAMING_CONFIGURE_EVALUATION_INPUT_OWNER=1
  export DREAMING_EVALUATION_INPUT_OWNER_ENABLED=1
  export DREAMING_EVALUATION_INPUT_AUTHOR_MODEL=author-model
  export DREAMING_EVALUATION_INPUT_REVIEWER_A_MODEL=reviewer-a-model
  export DREAMING_EVALUATION_INPUT_REVIEWER_B_MODEL=reviewer-b-model
  export DREAMING_PRESERVE_ESTATE_ADAPTERS=1
  export DREAMING_PRESERVE_OPERATIONAL_ADAPTERS=1
  export DREAMING_ESTATE_ADAPTERS_BASELINE_SOURCE="$FULL_BASELINE_SOURCE"
  export DREAMING_ESTATE_ADAPTERS_BASELINE_SHA256="$FULL_BASELINE_SHA"
  export DREAMING_CONFIGURE_REMOTE_EVALUATION_SUBJECTS=1
  export DREAMING_REMOTE_EVALUATION_SUBJECTS_ENABLED=1
  export DREAMING_REMOTE_SUBJECT_SSH_HOST='fixture@fd7a:115c:a1e0::1'
  export DREAMING_REMOTE_SUBJECT_SSH_ADDRESS_FAMILY=6
  export DREAMING_REMOTE_SUBJECT_SSH_SCRIPT='/fixture/remote-subject/ssh-estate-census.py'
  export DREAMING_REMOTE_SUBJECT_ESTATE_SCRIPT='/fixture/remote-subject/dreaming-estate.py'
  export DREAMING_REMOTE_SUBJECT_CONTENT_POLICY='/fixture/remote-subject/content-policy.json'
  export DREAMING_REMOTE_SUBJECT_KNOWN_HOSTS_SOURCE="$REMOTE_KNOWN_HOSTS_SOURCE"
  export DREAMING_REMOTE_SUBJECT_ORIGIN_HOST_ID=fixture-receiver
  export DREAMING_REMOTE_SUBJECT_RECEIVER_ID=fixture-receiver
  run_native install >/dev/null
)
python3 - "$NATIVE_ADAPTERS" "$FULL_BASELINE_SOURCE" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
baseline = json.load(open(sys.argv[2], encoding="utf-8"))
if config["evaluation_input_owner"]["enabled"] is not True:
    raise SystemExit("caller owner enablement was overwritten by persisted config")
if config["remote_evaluation_subjects"]["enabled"] is not True:
    raise SystemExit("caller remote enablement was overwritten by persisted config")
for key in ("sources", "executors", "publishers", "routes", "executor_order"):
    if config.get(key) != baseline.get(key):
        raise SystemExit(f"enabled bridge install changed existing {key}")
PY
echo "PASS  explicit bridge enablement overrides persisted disabled config"
FAKE_SSH="$NATIVE/fake-ssh.py"
SSH_ARGV_LOG="$NATIVE/ssh-argv.json"
cat > "$FAKE_SSH" <<'PY'
#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["SSH_ARGV_LOG"], "w", encoding="utf-8") as handle:
    json.dump(sys.argv[1:], handle)
PY
chmod +x "$FAKE_SSH"
SSH_ARGV_LOG="$SSH_ARGV_LOG" python3 "$ROOT/scripts/ssh-session-source.py" \
  --ssh-bin "$FAKE_SSH" \
  --host fixture \
  --remote-python /fixture/python3 \
  --remote-script /fixture/adapter.py \
  -- --vendor copilot --role session-source list --cursor '' \
  --floor '"value with spaces"' >/dev/null
python3 - "$SSH_ARGV_LOG" <<'PY'
import json
import shlex
import sys

ssh_argv = json.load(open(sys.argv[1], encoding="utf-8"))
remote = shlex.split(ssh_argv[-1])
cursor = remote[remote.index("--cursor") + 1]
floor = remote[remote.index("--floor") + 1]
if cursor != "" or floor != '"value with spaces"':
    raise SystemExit(f"remote argument boundaries were not preserved: {remote!r}")
PY
python3 - "$NATIVE_ADAPTERS" "$NATIVE/config.env" <<'PY'
import json
import pathlib
import sys

adapters = pathlib.Path(sys.argv[1])
config = json.loads(adapters.read_text(encoding="utf-8"))
config.pop("estate_census", None)
config.pop("estate_curator", None)
adapters.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
env = pathlib.Path(sys.argv[2])
lines = [
    line
    for line in env.read_text(encoding="utf-8").splitlines()
    if not line.startswith("DREAMING_COPILOT_")
    and not line.startswith("DREAMING_SOURCE_SSH_BIN=")
]
env.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
(
  export PATH="/usr/bin:/bin"
  run_native_persisted install >/dev/null
)
python3 - "$NATIVE_ADAPTERS" "$FAKE_COPILOT" "$ESTATE_BASELINE_SOURCE" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
baseline = json.load(open(sys.argv[3], encoding="utf-8"))
source = config["sources"]["copilot"]["argv"]
executor = config["executors"]["copilot"]["argv"]
publisher = config["publishers"]["copilot"]["argv"]
estate = config.get("estate_census", {}).get("argv", [])
curator = config.get("estate_curator", {})
if source[source.index("--host") + 1] != "fixture@fd7a:115c:a1e0::1":
    raise SystemExit(f"upgrade lost the inherited remote source: {source!r}")
if publisher[publisher.index("--host") + 1] != "fixture-client@fd7a:115c:a1e0::3":
    raise SystemExit(f"upgrade lost the inherited remote publisher: {publisher!r}")
if executor[executor.index("--binary") + 1] != sys.argv[2]:
    raise SystemExit(f"upgrade lost the inherited executor binary: {executor!r}")
if config.get("estate_census") != baseline.get("estate_census"):
    raise SystemExit(f"upgrade did not restore the sealed estate census: {config!r}")
if curator != baseline.get("estate_curator"):
    raise SystemExit(f"upgrade did not restore the sealed estate curator: {config!r}")
PY
python3 - "$NATIVE/config.env" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
names = {
    "DREAMING_PRESERVE_ESTATE_ADAPTERS",
    "DREAMING_PRESERVE_OPERATIONAL_ADAPTERS",
    "DREAMING_ESTATE_ADAPTERS_BASELINE_SOURCE",
    "DREAMING_ESTATE_ADAPTERS_BASELINE_SHA256",
}
lines = [
    line
    for line in path.read_text(encoding="utf-8").splitlines()
    if line.partition("=")[0] not in names
]
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
(
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  run_native_persisted install >/dev/null
)
python3 - "$NATIVE_ADAPTERS" <<'PY'
import json
import sys

path = sys.argv[1]
config = json.load(open(path, encoding="utf-8"))
argv = config["estate_curator"]["argv"]
argv[argv.index("--expected-curator-sha") + 1] = "0" * 64
with open(path, "w", encoding="utf-8") as handle:
    json.dump(config, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
(
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  run_native_persisted install >/dev/null
)
python3 - "$NATIVE_ADAPTERS" "$ROOT/skills/skill-curator/scripts/curator-run.py" <<'PY'
import hashlib
import json
import pathlib
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
argv = config["estate_curator"]["argv"]
actual = hashlib.sha256(pathlib.Path(sys.argv[2]).read_bytes()).hexdigest()
if argv[argv.index("--expected-curator-sha") + 1] != actual:
    raise SystemExit("ordinary reinstall retained a stale estate curator digest")
PY
native_hash="$(shasum -a 256 "$NATIVE_ADAPTERS" | awk '{print $1}')"
(
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  run_native_persisted install >/dev/null
)
[[ "$(shasum -a 256 "$NATIVE_ADAPTERS" | awk '{print $1}')" == "$native_hash" ]] ||
  { echo "ordinary reinstall rewrote persisted adapter config" >&2; exit 1; }
cp "$NATIVE_ADAPTERS" "$NATIVE/managed-adapters.before-local-upgrade.json"
cp "$NATIVE/config.env" "$NATIVE/config.before-local-upgrade.env"
python3 - "$NATIVE_ADAPTERS" "$NATIVE/config.env" "$ROOT" <<'PY'
import json
import pathlib
import sys

adapters = pathlib.Path(sys.argv[1])
config_env = pathlib.Path(sys.argv[2])
local_adapter = str(
    pathlib.Path(sys.argv[3])
    / "skills/skill-review/scripts/dreaming-vendor-adapter.py"
)
config = json.loads(adapters.read_text(encoding="utf-8"))
for role in ("sources", "executors", "publishers"):
    for entry in config.get(role, {}).values():
        entry["argv"][1] = local_adapter
config.pop("estate_census", None)
config.pop("estate_curator", None)
adapters.write_text(
    json.dumps(config, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
lines = [
    line
    for line in config_env.read_text(encoding="utf-8").splitlines()
    if not line.startswith("DREAMING_ADAPTER_CONFIG_MANAGED=")
]
config_env.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
(
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  run_native_persisted install >/dev/null
)
grep -Eq "^DREAMING_ADAPTER_CONFIG_MANAGED='?1'?$" "$NATIVE/config.env" ||
  { echo "legacy local adapter ownership was not upgraded" >&2; exit 1; }
cp "$NATIVE/managed-adapters.before-local-upgrade.json" "$NATIVE_ADAPTERS"
cp "$NATIVE/config.before-local-upgrade.env" "$NATIVE/config.env"
(
  export DREAMING_SESSION_SOURCES=copilot
  export DREAMING_REVIEW_EXECUTORS=copilot
  export DREAMING_SKILL_TARGETS=copilot
  export DREAMING_SOURCE_EXECUTOR_ALLOW='copilot>copilot'
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  run_native_persisted install >/dev/null
)
python3 - "$NATIVE_ADAPTERS" <<'PY'
import json
import sys

argv = json.load(open(sys.argv[1], encoding="utf-8"))["sources"]["copilot"]["argv"]
if "--host" not in argv or argv[argv.index("--host") + 1] != "fixture@fd7a:115c:a1e0::1":
    raise SystemExit(f"desired-state regeneration lost the remote source: {argv!r}")
publisher = json.load(open(sys.argv[1], encoding="utf-8"))["publishers"]["copilot"]["argv"]
if publisher[publisher.index("--host") + 1] != "fixture-client@fd7a:115c:a1e0::3":
    raise SystemExit(f"desired-state regeneration lost the remote publisher: {publisher!r}")
PY
(
  export DREAMING_SESSION_SOURCES=copilot
  export DREAMING_REVIEW_EXECUTORS=copilot
  export DREAMING_SKILL_TARGETS=copilot
  export DREAMING_SOURCE_EXECUTOR_ALLOW='copilot>copilot'
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  export DREAMING_COPILOT_SOURCE_SSH_HOST='fixture@fd7a:115c:a1e0::2'
  run_native_persisted install >/dev/null
)
python3 - "$NATIVE_ADAPTERS" <<'PY'
import json
import sys

argv = json.load(open(sys.argv[1], encoding="utf-8"))["sources"]["copilot"]["argv"]
if argv[argv.index("--host") + 1] != "fixture@fd7a:115c:a1e0::2":
    raise SystemExit(f"explicit remote source update was ignored: {argv!r}")
PY
(
  export DREAMING_SESSION_SOURCES=copilot
  export DREAMING_REVIEW_EXECUTORS=copilot
  export DREAMING_SKILL_TARGETS=copilot
  export DREAMING_SOURCE_EXECUTOR_ALLOW='copilot>copilot'
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  export DREAMING_COPILOT_PUBLISHER_SSH_HOST='fixture-client@fd7a:115c:a1e0::4'
  run_native_persisted install >/dev/null
)
python3 - "$NATIVE_ADAPTERS" <<'PY'
import json
import sys

argv = json.load(open(sys.argv[1], encoding="utf-8"))["publishers"]["copilot"]["argv"]
if argv[argv.index("--host") + 1] != "fixture-client@fd7a:115c:a1e0::4":
    raise SystemExit(f"explicit remote publisher update was ignored: {argv!r}")
PY
if (
  export DREAMING_SESSION_SOURCES=copilot
  export DREAMING_REVIEW_EXECUTORS=copilot
  export DREAMING_SKILL_TARGETS=copilot
  export DREAMING_SOURCE_EXECUTOR_ALLOW='copilot>copilot'
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  export DREAMING_COPILOT_PUBLISHER_SSH_HOST=''
  run_native_persisted install >/dev/null 2>&1
); then
  echo "remote-only publisher accepted an empty SSH host" >&2
  exit 1
fi
(
  export DREAMING_SESSION_SOURCES=copilot
  export DREAMING_REVIEW_EXECUTORS=copilot
  export DREAMING_SKILL_TARGETS=copilot
  export DREAMING_SOURCE_EXECUTOR_ALLOW='copilot>copilot'
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  export DREAMING_COPILOT_PUBLISHER_SSH_HOST='fixture-client@fd7a:115c:a1e0::4'
  run_native_persisted install >/dev/null
)
(
  export DREAMING_SESSION_SOURCES=copilot
  export DREAMING_REVIEW_EXECUTORS=copilot
  export DREAMING_SKILL_TARGETS=copilot
  export DREAMING_SOURCE_EXECUTOR_ALLOW='copilot>copilot'
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  export DREAMING_COPILOT_SOURCE_SSH_HOST=''
  run_native_persisted install >/dev/null
)
python3 - "$NATIVE_ADAPTERS" <<'PY'
import json
import sys

argv = json.load(open(sys.argv[1], encoding="utf-8"))["sources"]["copilot"]["argv"]
if any(item.endswith("/scripts/ssh-session-source.py") for item in argv):
    raise SystemExit(f"explicit local-source reset was ignored: {argv!r}")
PY
printf '{}\n' > "$NATIVE/dreaming-state/remote-publication-summary.json"
printf '{}\n' > "$NATIVE/dreaming-state/publication-recovery-required.json"
(
  export DREAMING_SESSION_SOURCES=copilot
  export DREAMING_REVIEW_EXECUTORS=copilot
  export DREAMING_SKILL_TARGETS=''
  export DREAMING_SOURCE_EXECUTOR_ALLOW='copilot>copilot'
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  export DREAMING_COPILOT_PUBLISHER_SSH_HOST=''
  export DREAMING_REQUIRE_REMOTE_COPILOT_PUBLISHER=0
  export DREAMING_DETACH_REMOTE_COPILOT_PUBLISHER=1
  run_native_persisted install >/dev/null
)
python3 - "$NATIVE_ADAPTERS" "$NATIVE/dreaming-state" <<'PY'
import json
import pathlib
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
if "copilot" in config["publishers"] or "copilot" in config["retired_publishers"]:
    raise SystemExit(f"detached remote publisher remained actionable: {config!r}")
state = pathlib.Path(sys.argv[2])
for name in (
    "remote-publication-summary.json",
    "publication-recovery-required.json",
):
    if (state / name).exists():
        raise SystemExit(f"detached remote publisher mirror remained: {name}")
PY
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
if config["publishers"]["codex"].get("timeout") != 90:
    raise SystemExit(f"publisher timeout does not cover native publication: {config!r}")
PY
EXTERNAL_CONFIG="$NATIVE/external-adapters.json"
cat > "$EXTERNAL_CONFIG" <<JSON
{
  "estate_curator": {
    "argv": [
      "/usr/bin/python3",
      "$ROOT/scripts/ssh-estate-curator.py",
      "--custom-external-route"
    ]
  },
  "externally_managed": true
}
JSON
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
(
  export DREAMING_COPILOT_BIN="$FAKE_COPILOT"
  run_native_persisted install >/dev/null
)
[[ "$(shasum -a 256 "$EXTERNAL_CONFIG" | awk '{print $1}')" == "$external_hash" ]] ||
  { echo "persisted reinstall overwrote external estate adapter config" >&2; exit 1; }
grep -Eq "^DREAMING_ADAPTER_CONFIG_MANAGED='?0'?$" "$NATIVE/config.env" ||
  { echo "external adapter ownership was not persisted" >&2; exit 1; }
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
echo "rollback drift fixture" >> "$INSTRUCTIONS"
if run_install rollback "$BACKUP" >"$TMP/rollback-drift.out" \
    2>"$TMP/rollback-drift.err"; then
  echo "rollback accepted a drifted managed instruction" >&2
  exit 1
fi
[[ -f "$STATE/skill-review/disable-daemon" ]] ||
  { echo "failed rollback removed halt" >&2; exit 1; }
[[ ! -e "$STATE/dreaming/selftest-passed-generation" ]] ||
  { echo "failed rollback retained the prior selftest generation" >&2; exit 1; }
if run_install enable >"$TMP/enable-after-failed-rollback.out" \
    2>"$TMP/enable-after-failed-rollback.err"; then
  echo "enable accepted a partially restored generation" >&2
  exit 1
fi
cp "$ROOT/config/dreaming.instructions.md" "$INSTRUCTIONS"
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
[[ ! -e "$DEST/com.fixture.dreaming.dashboard.plist" ]] ||
  { echo "rollback restored a dashboard absent from its backup" >&2; exit 1; }
DREAMING_SKIP_DASHBOARD_HEALTH_CHECK=0 run_install selftest >/dev/null
run_install enable >/dev/null
[[ -f "$INSTRUCTIONS" ]] ||
  { echo "rollback omitted the managed Copilot instruction" >&2; exit 1; }
grep -q "managed-instructions=verified" <<<"$(run_install status)"
echo "PASS  rollback refuses missing executables and restores selftest prerequisites"

run_install install >/dev/null
MIGRATION_LEGACY_SKILLS="$TMP/migration-legacy-skills"
MIGRATION_LEGACY_STATE="$TMP/migration-legacy-state"
mkdir -p "$MIGRATION_LEGACY_SKILLS/learned" "$MIGRATION_LEGACY_STATE"
printf '%s\n' '---' 'name: learned' 'description: installer migration fixture' '---' \
  > "$MIGRATION_LEGACY_SKILLS/learned/SKILL.md"
git -C "$MIGRATION_LEGACY_SKILLS" init -q
git -C "$MIGRATION_LEGACY_SKILLS" config core.hooksPath /dev/null
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
for kind in dreaming selftest watchdog dashboard; do
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

REMOTE_CONFIG="$TMP/remote-config"
mkdir -p "$REMOTE_CONFIG/state"
printf 'fixture-host-key\n' > \
  "$REMOTE_CONFIG/state/remote-subject-known-hosts"
chmod 600 "$REMOTE_CONFIG/state/remote-subject-known-hosts"
cat > "$REMOTE_CONFIG/state/estate-adapters-baseline.json" <<'JSON'
{
  "contract_version": 1,
  "estate_census": {"argv": ["/baseline/census"], "timeout": 210},
  "estate_curator": {
    "argv": ["/baseline/curator"],
    "enabled": false,
    "timeout": 330
  }
}
JSON
if DREAMING_SESSION_SOURCES=copilot \
  DREAMING_REVIEW_EXECUTORS=copilot \
  DREAMING_SKILL_TARGETS='' \
  DREAMING_COPILOT_BIN=/bin/echo \
  DREAMING_PRESERVE_ESTATE_ADAPTERS=1 \
  DREAMING_ESTATE_ADAPTERS_BASELINE_SHA256="$(printf '0%.0s' {1..64})" \
  /usr/bin/python3 "$ROOT/scripts/configure-adapters.py" \
    --output "$REMOTE_CONFIG/state/refused-adapters.json" \
    --repo-root "$ROOT" \
    --state-dir "$REMOTE_CONFIG/state" \
    >"$REMOTE_CONFIG/refused.out" 2>"$REMOTE_CONFIG/refused.err"; then
  echo "mismatched estate adapter baseline digest was accepted" >&2
  exit 1
fi
grep -q "estate adapter baseline digest does not match" \
  "$REMOTE_CONFIG/refused.err"
[[ ! -e "$REMOTE_CONFIG/state/refused-adapters.json" ]] ||
  { echo "refused estate baseline wrote adapter configuration" >&2; exit 1; }
DREAMING_SESSION_SOURCES=copilot \
DREAMING_REVIEW_EXECUTORS=copilot \
DREAMING_SKILL_TARGETS='' \
DREAMING_COPILOT_BIN=/bin/echo \
DREAMING_CONFIGURE_EVALUATION_INPUT_OWNER=1 \
DREAMING_EVALUATION_INPUT_OWNER_ENABLED=0 \
DREAMING_EVALUATION_INPUT_AUTHOR_MODEL=author-model \
DREAMING_EVALUATION_INPUT_REVIEWER_A_MODEL=reviewer-a-model \
DREAMING_EVALUATION_INPUT_REVIEWER_B_MODEL=reviewer-b-model \
DREAMING_CONFIGURE_REMOTE_EVALUATION_SUBJECTS=1 \
DREAMING_REMOTE_EVALUATION_SUBJECTS_ENABLED=0 \
DREAMING_REMOTE_SUBJECT_SSH_HOST=fixture-host \
DREAMING_REMOTE_SUBJECT_ORIGIN_HOST_ID=fixture-origin \
DREAMING_REMOTE_SUBJECT_RECEIVER_ID=fixture-receiver \
DREAMING_REMOTE_SUBJECT_SSH_SCRIPT=/remote/receiver/ssh-estate-census.py \
DREAMING_REMOTE_SUBJECT_ESTATE_SCRIPT=/remote/receiver/dreaming-estate.py \
DREAMING_REMOTE_SUBJECT_CONTENT_POLICY=/remote/receiver/policy.json \
  /usr/bin/python3 "$ROOT/scripts/configure-adapters.py" \
  --output "$REMOTE_CONFIG/state/adapters.json" \
  --repo-root "$ROOT" \
  --state-dir "$REMOTE_CONFIG/state" >/dev/null
/usr/bin/python3 - "$REMOTE_CONFIG/state/adapters.json" <<'PY'
import json
import sys
from pathlib import Path

value = json.load(open(sys.argv[1], encoding="utf-8"))
owner = value["evaluation_input_owner"]
remote = value["remote_evaluation_subjects"]
assert owner["enabled"] is False
assert remote["enabled"] is False
assert remote["protocol_version"] == 1
assert remote["origin_host_id"] == "fixture-origin"
assert remote["max_files"] == 512
assert remote["max_file_bytes"] == 8 * 1024 * 1024
assert remote["max_decoded_bytes"] == 32 * 1024 * 1024
assert remote["max_encoded_bytes"] == 48 * 1024 * 1024
assert remote["snapshot_root"].endswith(
    "/state/remote-evaluation-subjects"
)
command_executable = Path(remote["command"][0])
assert command_executable == Path(sys.executable).resolve()
assert command_executable.is_file()
assert not command_executable.is_symlink()
assert "--fetch-subject" in remote["command"]
assert remote["receiver"]["content_policy_sha256"]
PY
echo "PASS  disabled remote subject config pins identity and bounds"

echo "installer tests: PASS"
