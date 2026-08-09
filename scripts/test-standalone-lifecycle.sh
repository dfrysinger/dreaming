#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$ROOT/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/standalone-lifecycle.XXXXXX")"
cleanup() {
  local status=$?
  if (( status != 0 )) && [[ -f "$TMP/job.log" ]]; then
    cat "$TMP/job.log" >&2
  fi
  if (( status != 0 )) && [[ -f "$TMP/state/daemon-selftest.out" ]]; then
    cat "$TMP/state/daemon-selftest.out" >&2
  fi
  chmod -R u+w "$TMP" 2>/dev/null || true
  rm -rf "$TMP"
  exit "$status"
}
trap cleanup EXIT
export TMPDIR="$TMP"

HOME_DIR="$TMP/home"
DATA="$TMP/data"
STATE="$TMP/state"
SKILLS="$DATA/skills"
DEST="$TMP/LaunchAgents"
SOURCE="$TMP/shared-source"
mkdir -p "$HOME_DIR" "$DEST"

for name in writing-great-skills dual-review authenticated-browse; do
  mkdir -p "$SOURCE/skills/$name"
  printf -- '---\nname: %s\ndescription: Standalone lifecycle fixture.\n---\n' "$name" \
    > "$SOURCE/skills/$name/SKILL.md"
done
RECEIPT="$TMP/receipt.json"
"$ROOT/scripts/dreaming-deps.py" generate-receipt "$SOURCE" \
  --revision 0123456789abcdef0123456789abcdef01234567 --output "$RECEIPT"

SOURCE_FIXTURE="$TMP/source.json"
EXECUTOR_FIXTURE="$TMP/executor.json"
PUBLISHER_FIXTURE="$TMP/publisher.json"
ADAPTER_CONFIG="$TMP/adapters.json"
python3 - "$ROOT" "$SOURCE_FIXTURE" "$EXECUTOR_FIXTURE" \
  "$PUBLISHER_FIXTURE" "$ADAPTER_CONFIG" <<'PY'
import json
import sys

root, source_path, executor_path, publisher_path, config_path = sys.argv[1:]
fake = f"{root}/skills/skill-review/scripts/fake-dreaming-adapter.py"
event = {
    "source": "fake",
    "qualified_session_id": "fake:one",
    "sequence": 1,
    "timestamp": 1,
    "kind": "session_end",
    "tool_name": None,
    "text": "complete",
    "source_event_id": "one-event-1",
}
source = {
    "source": "fake",
    "watermark": 10,
    "sessions": [{
        "native_session_id": "one",
        "repository_scope": "opaque-scope",
        "updated_at": 10,
        "completion_state": "terminal",
        "events": [event],
    }],
}
json.dump(source, open(source_path, "w"), sort_keys=True)
json.dump({"mode": "success"}, open(executor_path, "w"), sort_keys=True)
json.dump({"owned_bundle_ids": []}, open(publisher_path, "w"), sort_keys=True)

def adapter(role, adapter_id, fixture):
    return {
        "argv": [
            "/usr/bin/python3",
            fake,
            "--fixture",
            fixture,
            "--adapter-id",
            adapter_id,
            "--role",
            role,
        ]
    }

config = {
    "contract_version": 1,
    "routes": ["fake>fake-executor"],
    "executor_order": ["fake-executor"],
    "sources": {
        "fake": adapter("session-source", "fake", source_path),
    },
    "executors": {
        "fake-executor": adapter(
            "review-executor", "fake-executor", executor_path
        ),
    },
    "publishers": {
        "fake-publisher": adapter(
            "skill-publisher", "fake-publisher", publisher_path
        ),
    },
}
json.dump(config, open(config_path, "w"), sort_keys=True)
PY

OLD_RUN="$TMP/old-run.sh"
cat > "$OLD_RUN" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${OLD_SELFTEST:-0}" == "1" ]]; then
  printf '== result: 0 failure(s) ==\n' > "$SKILLS_STATE_DIR/daemon-selftest.out"
fi
SH
chmod +x "$OLD_RUN"
for kind in sweep selftest watchdog; do
  OLD_SELFTEST=0
  [[ "$kind" == "selftest" ]] && OLD_SELFTEST=1
  python3 - "$DEST/com.fixture.skills.$kind.plist" \
    "com.fixture.skills.$kind" "$OLD_RUN" "$STATE" "$OLD_SELFTEST" <<'PY'
import plistlib
import sys

path, label, program, state, selftest = sys.argv[1:]
plistlib.dump(
    {
        "Label": label,
        "ProgramArguments": ["/bin/bash", program],
        "EnvironmentVariables": {
            "SKILLS_STATE_DIR": state,
            "OLD_SELFTEST": selftest,
        },
    },
    open(path, "wb"),
)
PY
done

FAKE_LAUNCHCTL="$TMP/launchctl"
cat > "$FAKE_LAUNCHCTL" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$LAUNCHCTL_LOG"
case "${1:-}" in
  kickstart)
    target="${3:-${2:-}}"
    label="${target##*/}"
    plist="$LAUNCHCTL_DEST/$label.plist"
    exec /usr/bin/python3 - "$plist" <<'PY'
import os
import plistlib
import subprocess
import sys

data = plistlib.load(open(sys.argv[1], "rb"))
environment = os.environ.copy()
environment.update(data.get("EnvironmentVariables", {}))
result = subprocess.run(
    data["ProgramArguments"],
    env=environment,
    check=False,
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
with open(environment["LAUNCHCTL_JOB_LOG"], "a", encoding="utf-8") as handle:
    handle.write(result.stdout)
raise SystemExit(result.returncode)
PY
    ;;
  print)
    printf 'state = waiting\nruns = 1\nlast exit code = 0\n'
    ;;
esac
SH
chmod +x "$FAKE_LAUNCHCTL"
export LAUNCHCTL_LOG="$TMP/launchctl.log"
export LAUNCHCTL_DEST="$DEST"
export LAUNCHCTL_JOB_LOG="$TMP/job.log"

run_install() {
  HOME="$HOME_DIR" \
  DREAMING_REPO_ROOT="$ROOT" \
  DREAMING_CONFIG_POINTER="$HOME_DIR/.config/dreaming/active-config" \
  DREAMING_DATA_DIR="$DATA" \
  DREAMING_STATE_DIR="$STATE" \
  DREAMING_SKILLS_ROOT="$SKILLS" \
  DREAMING_ADAPTER_CONFIG="$ADAPTER_CONFIG" \
  DREAMING_ENABLE_COPILOT_COMPAT=0 \
  DREAMING_CONFIG_FILE="$DATA/config.env" \
  DREAMING_DEPS_DIR="$DATA/deps" \
  DREAMING_RECEIPT_FILE="$RECEIPT" \
  DREAMING_DEPS_SOURCE="$SOURCE" \
  DREAMING_CANONICAL_SKILLS_ROOT="" \
  DREAMING_INSTALLED_PLUGINS_ROOT="$TMP/no-installed-plugins" \
  DREAMING_LAUNCHD_PREFIX="com.fixture.dreaming" \
  SKILLS_LAUNCHD_PREFIX="com.fixture.skills" \
  SKILLS_LAUNCH_AGENTS_DIR="$DEST" \
  SKILLS_LAUNCHD_DOMAIN="gui/fixture" \
  SKILLS_STATE_DIR="$STATE" \
  SKILLS_LOCAL_ROOT="$SKILLS" \
  LAUNCHCTL_BIN="$FAKE_LAUNCHCTL" \
  DREAMING_SKIP_DASHBOARD_HEALTH_CHECK=1 \
  DREAMING_SELFTEST_WAIT_SECS=10 \
    "$ROOT/scripts/install.sh" "$@"
}

run_persisted() {
  HOME="$HOME_DIR" \
  DREAMING_REPO_ROOT="$ROOT" \
  DREAMING_LAUNCHD_PREFIX="com.fixture.dreaming" \
  SKILLS_LAUNCHD_PREFIX="com.fixture.skills" \
  SKILLS_LAUNCH_AGENTS_DIR="$DEST" \
  SKILLS_LAUNCHD_DOMAIN="gui/fixture" \
  LAUNCHCTL_BIN="$FAKE_LAUNCHCTL" \
    "$ROOT/scripts/install.sh" "$@"
}

run_persisted_reinstall() {
  HOME="$HOME_DIR" \
  DREAMING_REPO_ROOT="$ROOT" \
  DREAMING_DEPS_SOURCE="$SOURCE" \
  DREAMING_CANONICAL_SKILLS_ROOT="" \
  DREAMING_INSTALLED_PLUGINS_ROOT="$TMP/no-installed-plugins" \
  DREAMING_LAUNCHD_PREFIX="com.fixture.dreaming" \
  SKILLS_LAUNCHD_PREFIX="com.fixture.skills" \
  SKILLS_LAUNCH_AGENTS_DIR="$DEST" \
  SKILLS_LAUNCHD_DOMAIN="gui/fixture" \
  LAUNCHCTL_BIN="$FAKE_LAUNCHCTL" \
    "$ROOT/scripts/install.sh" install
}

run_override_reinstall() {
  DREAMING_RECEIPT_FILE="$RECEIPT" run_persisted_reinstall
}

run_install install >/dev/null
BACKUP="$(<"$STATE/dreaming/latest-migration-backup")"
persisted_status="$(run_persisted status)"
grep -q '^copilot_compat=0$' <<<"$persisted_status"
grep -q "^state_root=$STATE$" <<<"$persisted_status"
run_persisted_reinstall >/dev/null
python3 - "$DATA/config.env" <<'PY'
import sys

path = sys.argv[1]
lines = open(path).read().splitlines()
lines = [
    "DREAMING_REPO_ROOT='/nonexistent/stale-checkout'"
    if line.startswith("DREAMING_REPO_ROOT=")
    else "DREAMING_RECEIPT_FILE='/nonexistent/stale-receipt.json'"
    if line.startswith("DREAMING_RECEIPT_FILE=")
    else line
    for line in lines
]
open(path, "w").write("\n".join(lines) + "\n")
PY
persisted_status="$(run_persisted status)"
grep -q '^copilot_compat=0$' <<<"$persisted_status"
grep -q "^state_root=$STATE$" <<<"$persisted_status"
run_override_reinstall >/dev/null
for plist in "$DEST"/com.fixture.dreaming.*.plist; do
  ! grep -q 'COPILOT_HOME\|\.claude\|\.codex' "$plist" || {
    echo "standalone plist contains a vendor home" >&2
    exit 1
  }
  if [[ "$plist" != *.dashboard.plist ]]; then
    grep -q "<key>DREAMING_ADAPTER_CONFIG</key><string>$ADAPTER_CONFIG</string>" "$plist"
  fi
done
[[ ! -e "$HOME_DIR/.copilot" && ! -e "$HOME_DIR/.claude" &&
   ! -e "$HOME_DIR/.codex" ]] || {
  echo "standalone install created a vendor home" >&2
  exit 1
}

run_install selftest >/dev/null
run_install enable >/dev/null
"$FAKE_LAUNCHCTL" kickstart -k gui/fixture/com.fixture.dreaming.dreaming
python3 - "$STATE/review-ledger.json" <<'PY'
import json
import sys

ledger = json.load(open(sys.argv[1]))
assert [row["session_id"] for row in ledger] == ["fake:one"], ledger
PY

touch "$STATE/skill-review/disable-daemon"
python3 - "$SOURCE_FIXTURE" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path))
data["watermark"] = 20
data["sessions"].append({
    "native_session_id": "two",
    "repository_scope": "opaque-scope",
    "updated_at": 20,
    "completion_state": "terminal",
    "events": [{
        "source": "fake",
        "qualified_session_id": "fake:two",
        "sequence": 1,
        "timestamp": 1,
        "kind": "session_end",
        "tool_name": None,
        "text": "complete",
        "source_event_id": "two-event-1",
    }],
})
json.dump(data, open(path, "w"), sort_keys=True)
PY
"$FAKE_LAUNCHCTL" kickstart -k gui/fixture/com.fixture.dreaming.dreaming
[[ "$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' \
  "$STATE/review-ledger.json")" == "1" ]] || {
  echo "halted scheduler mutated the review ledger" >&2
  exit 1
}

run_install enable >/dev/null
"$FAKE_LAUNCHCTL" kickstart -k gui/fixture/com.fixture.dreaming.dreaming
[[ "$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' \
  "$STATE/review-ledger.json")" == "2" ]] || {
  echo "re-enabled scheduler did not review the queued session" >&2
  exit 1
}

run_install rollback "$BACKUP" >/dev/null
run_install selftest >/dev/null
run_install enable >/dev/null
run_install uninstall >/dev/null
[[ -z "$(find "$DEST" -maxdepth 1 -name 'com.fixture.*.plist' -print)" ]] || {
  echo "standalone uninstall retained managed LaunchAgents" >&2
  exit 1
}
[[ ! -e "$HOME_DIR/.copilot" && ! -e "$HOME_DIR/.claude" &&
   ! -e "$HOME_DIR/.codex" ]] || {
  echo "standalone lifecycle created a vendor home" >&2
  exit 1
}

echo "standalone lifecycle tests: PASS"
