#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNNER="$SCRIPT_DIR/cutover-dreaming-host.py"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/cutover.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

pass() {
  echo "PASS: $*"
}

FAKE_SSH="$TMP/ssh"
cat > "$FAKE_SSH" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
host="$1"
command="$2"
export FAKE_HOST="$host"
root="$FAKE_ROOT/$host"
if [[ -f "$root/ssh-error" ]]; then
  echo "simulated ssh failure" >&2
  exit 255
fi
if [[ "$command" == /usr/bin/python3\ -c* && -f "$root/probe-error" ]]; then
  echo "simulated path probe failure" >&2
  exit 2
fi
if [[ "$command" == "/bin/hostname" ]]; then
  if [[ -f "$root/hostname" ]]; then
    cat "$root/hostname"
  else
    printf '%s.example\n' "$host"
  fi
  exit 0
fi
exec /bin/sh -c "$command"
EOF
chmod +x "$FAKE_SSH"

FAKE_LAUNCHCTL="$TMP/launchctl"
cat > "$FAKE_LAUNCHCTL" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
root="$FAKE_ROOT/$FAKE_HOST"
state="$root/services"
mkdir -p "$state"
label="${2##*/}"
case "$1" in
  print)
    if [[ -f "$root/domain-error" ]]; then
      echo "Could not find domain: 3: No such process" >&2
      exit 3
    fi
    if [[ -f "$root/permission-error" ]]; then
      echo "launchctl: permission denied" >&2
      exit 126
    fi
    if [[ -f "$root/print-error" ]]; then
      echo "simulated launchctl inspection failure" >&2
      exit 77
    fi
    if [[ ! -e "$state/$label" ]]; then
      echo "Could not find service \"$label\" in domain" >&2
      exit 113
    fi
    echo "state = waiting"
    if [[ -f "$state/$label.pid" ]]; then
      printf 'pid = %s\n' "$(<"$state/$label.pid")"
    fi
    ;;
  bootout)
    if [[ -f "$root/bootout-error" ]]; then
      echo "simulated bootout failure" >&2
      exit 77
    fi
    if [[ ! -e "$state/$label" ]]; then
      echo "Boot-out failed: 3: No such process" >&2
      exit 3
    fi
    rm -f "$state/$label" "$state/$label.pid"
    ;;
  *)
    exit 2
    ;;
esac
EOF
chmod +x "$FAKE_LAUNCHCTL"

FAKE_PS="$TMP/ps"
cat > "$FAKE_PS" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
processes="$FAKE_ROOT/$FAKE_HOST/processes"
[[ ! -f "$processes" ]] || cat "$processes"
EOF
chmod +x "$FAKE_PS"
export FAKE_ROOT="$TMP"

ENABLE="$TMP/enable"
cat > "$ENABLE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
label="${1:-com.test.dreaming.dreaming}"
rm -f "$FAKE_ROOT/$FAKE_HOST/halt"
touch "$FAKE_ROOT/$FAKE_HOST/services/$label"
EOF
chmod +x "$ENABLE"

ENABLE_FAIL="$TMP/enable-fail"
cat > "$ENABLE_FAIL" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exit 9
EOF
chmod +x "$ENABLE_FAIL"

ENABLE_HALTED="$TMP/enable-halted"
cat > "$ENABLE_HALTED" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
touch "$FAKE_ROOT/$FAKE_HOST/services/com.test.dreaming.dreaming"
EOF
chmod +x "$ENABLE_HALTED"

ENABLE_NO_SERVICE="$TMP/enable-no-service"
cat > "$ENABLE_NO_SERVICE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
rm -f "$FAKE_ROOT/$FAKE_HOST/halt"
EOF
chmod +x "$ENABLE_NO_SERVICE"

ENABLE_PRINT_ERROR="$TMP/enable-print-error"
cat > "$ENABLE_PRINT_ERROR" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
rm -f "$FAKE_ROOT/$FAKE_HOST/halt"
touch "$FAKE_ROOT/$FAKE_HOST/services/com.test.dreaming.dreaming"
touch "$FAKE_ROOT/$FAKE_HOST/print-error"
EOF
chmod +x "$ENABLE_PRINT_ERROR"

prepare_case() {
  local name="$1"
  SRC="${name}-source"
  DST="${name}-destination"
  RECEIPT="$TMP/${name}-receipt.json"
  mkdir -p "$TMP/$SRC/services" "$TMP/$DST/services" "$TMP/$DST/dreaming"
  touch "$TMP/$SRC/halt" "$TMP/$DST/halt"
  printf '%s-source.example\n' "$name" > "$TMP/$SRC/hostname"
  printf '%s-destination.example\n' "$name" > "$TMP/$DST/hostname"
  printf 'generation-%s\n' "$name" > "$TMP/$DST/dreaming/activation-generation"
  printf 'generation-%s\n' "$name" > "$TMP/$DST/dreaming/selftest-passed-generation"
  printf 'selftest detail\n== result: 0 failure(s) ==\n' > "$TMP/$DST/selftest.out"
  touch "$TMP/$SRC/services/com.test.dreaming.dreaming"
}

cutover() {
  local enable="$1"
  shift
  "$RUNNER" cutover \
    --ssh "$FAKE_SSH" \
    --source "$SRC" \
    --destination "$DST" \
    --user test \
    --source-halt "$TMP/$SRC/halt" \
    --destination-halt "$TMP/$DST/halt" \
    --launchctl "$FAKE_LAUNCHCTL" \
    --ps "$FAKE_PS" \
    --receipt "$RECEIPT" \
    --destination-selftest-result "$TMP/$DST/selftest.out" \
    --destination-enable-command "$enable" \
    "$@"
}

record_pass() {
  local evidence="$1"
  "$RUNNER" record-pass \
    --ssh "$FAKE_SSH" \
    --source "$SRC" \
    --destination "$DST" \
    --user test \
    --source-halt "$TMP/$SRC/halt" \
    --destination-halt "$TMP/$DST/halt" \
    --launchctl "$FAKE_LAUNCHCTL" \
    --ps "$FAKE_PS" \
    --receipt "$RECEIPT" \
    --run-evidence "$evidence"
}

assert_status() {
  local path="$1" expected="$2"
  python3 - "$path" "$expected" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1]))
assert payload["status"] == sys.argv[2], payload
PY
}

assert_recovered() {
  [[ -e "$TMP/$DST/halt" ]] || fail "$1 did not restore destination halt"
  [[ ! -e "$TMP/$DST/services/com.test.dreaming.dreaming" ]] ||
    fail "$1 did not unload destination scheduler"
  assert_status "$RECEIPT" "$2"
}

write_run() {
  local path="$1" mode="$2"
  python3 - "$RECEIPT" "$path" "$mode" <<'PY'
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

receipt_path, evidence_path, mode = sys.argv[1:]
activated = datetime.fromisoformat(json.load(open(receipt_path))["activated_at"])
started = activated + timedelta(seconds=2)
record = {
    "run_id": Path(evidence_path).stem,
    "status": "ok",
    "started_at": started.isoformat(),
    "ended_at": (started + timedelta(seconds=2)).isoformat(),
    "cadence_committed": True,
}
if mode == "failed":
    record["status"] = "aborted"
elif mode == "stale":
    record["started_at"] = (activated - timedelta(seconds=2)).isoformat()
    record["ended_at"] = (activated - timedelta(seconds=1)).isoformat()
elif mode == "uncommitted":
    record["cadence_committed"] = False
elif mode == "unterminated":
    record.pop("ended_at")
elif mode == "reversed":
    record["ended_at"] = (started - timedelta(seconds=1)).isoformat()
json.dump(record, open(evidence_path, "w"))
PY
}

prepare_case happy
cutover "$ENABLE" >/dev/null
[[ ! -e "$TMP/$SRC/services/com.test.dreaming.dreaming" ]] ||
  fail "happy cutover left source scheduler loaded"
[[ -e "$TMP/$DST/services/com.test.dreaming.dreaming" ]] ||
  fail "happy cutover did not load destination scheduler"
[[ -e "$TMP/$SRC/halt" && ! -e "$TMP/$DST/halt" ]] ||
  fail "happy cutover halt state is wrong"
assert_status "$RECEIPT" activated
python3 - "$RECEIPT" <<'PY'
import json
import sys

receipt = json.load(open(sys.argv[1]))
selftest = receipt["destination_selftest"]
assert selftest["activation_generation"] == "generation-happy"
assert len(selftest["activation_generation_sha256"]) == 64
assert len(selftest["selftest_generation_sha256"]) == 64
PY
cp "$RECEIPT" "$TMP/activated-template.json"
write_run "$TMP/$DST/happy-run.json" ok
record_pass "$TMP/$DST/happy-run.json"
assert_status "$RECEIPT" complete
pass "happy cutover and committed scheduled pass"

prepare_case custom-prefix
rm "$TMP/$SRC/services/com.test.dreaming.dreaming"
touch "$TMP/$SRC/services/org.source.dreaming.dreaming"
cutover "$ENABLE org.destination.dreaming.dreaming" \
  --source-prefix org.source.dreaming \
  --source-legacy-prefix org.source.skills \
  --destination-prefix org.destination.dreaming \
  --destination-legacy-prefix org.destination.skills >/dev/null
assert_status "$RECEIPT" activated
pass "per-host launchd namespaces remain configurable"

prepare_case no-source-label
rm "$TMP/$SRC/services/com.test.dreaming.dreaming"
if cutover "$ENABLE" >/dev/null 2>&1; then
  fail "cutover accepted an empty source launchd namespace"
fi
[[ -e "$TMP/$DST/halt" ]] || fail "empty source namespace activated destination"
pass "empty source launchd namespace is refused"

prepare_case aliases
printf 'same-host.example\n' > "$TMP/$SRC/hostname"
printf 'same-host.example\n' > "$TMP/$DST/hostname"
if cutover "$ENABLE" >/dev/null 2>&1; then
  fail "cutover accepted aliases for the same host"
fi
[[ -e "$TMP/$SRC/services/com.test.dreaming.dreaming" ]] ||
  fail "same-host refusal mutated source"
[[ -e "$TMP/$DST/halt" ]] || fail "same-host refusal activated destination"
pass "same-host aliases are refused before mutation"

prepare_case inspect-error
touch "$TMP/$SRC/print-error"
if cutover "$ENABLE" >/dev/null 2>&1; then
  fail "cutover treated launchctl inspection error as not loaded"
fi
[[ -e "$TMP/$SRC/services/com.test.dreaming.dreaming" ]] ||
  fail "launchctl inspection refusal mutated source"
[[ -e "$TMP/$DST/halt" ]] || fail "launchctl inspection refusal activated destination"
pass "launchctl not-found is distinct from inspection failure"

prepare_case domain-error
touch "$TMP/$SRC/domain-error"
if cutover "$ENABLE" >/dev/null 2>&1; then
  fail "cutover treated launchctl domain error as service absence"
fi
[[ -e "$TMP/$SRC/services/com.test.dreaming.dreaming" ]] ||
  fail "launchctl domain refusal mutated source"
[[ -e "$TMP/$DST/halt" ]] || fail "launchctl domain refusal activated destination"
pass "launchctl domain errors fail closed"

prepare_case permission-error
touch "$TMP/$SRC/permission-error"
if cutover "$ENABLE" >/dev/null 2>&1; then
  fail "cutover treated launchctl permission error as not loaded"
fi
[[ -e "$TMP/$SRC/services/com.test.dreaming.dreaming" ]] ||
  fail "launchctl permission refusal mutated source"
[[ -e "$TMP/$DST/halt" ]] || fail "launchctl permission refusal activated destination"
pass "launchctl permission errors fail closed"

prepare_case ssh-error
touch "$TMP/$SRC/ssh-error"
if cutover "$ENABLE" >/dev/null 2>&1; then
  fail "cutover treated SSH failure as not loaded"
fi
[[ -e "$TMP/$SRC/services/com.test.dreaming.dreaming" ]] ||
  fail "SSH refusal mutated source"
[[ -e "$TMP/$DST/halt" ]] || fail "SSH refusal activated destination"
pass "SSH inspection errors fail closed"

prepare_case halt-probe-error
touch "$TMP/$SRC/probe-error"
if cutover "$ENABLE" >/dev/null 2>&1; then
  fail "cutover treated halt probe error as absence"
fi
[[ -e "$TMP/$SRC/services/com.test.dreaming.dreaming" ]] ||
  fail "halt probe refusal mutated source"
[[ -e "$TMP/$DST/halt" ]] || fail "halt probe refusal activated destination"
pass "halt absence is distinct from probe failure"

prepare_case bootout-error
touch "$TMP/$SRC/bootout-error"
if cutover "$ENABLE" >/dev/null 2>&1; then
  fail "cutover ignored source bootout failure"
fi
[[ -e "$TMP/$SRC/services/com.test.dreaming.dreaming" ]] ||
  fail "bootout failure unexpectedly removed source service"
[[ -e "$TMP/$DST/halt" ]] || fail "bootout failure activated destination"
assert_status "$RECEIPT" source_unload_failed
pass "source bootout failure is durable and fail-closed"

prepare_case process-stack
printf '100\n' > "$TMP/$SRC/services/com.test.dreaming.dreaming.pid"
cat > "$TMP/$SRC/processes" <<'EOF'
100 1 100 /opaque/scheduled-root
101 100 101 /opaque/arbitrary-child
102 101 101 /usr/local/bin/copilot
EOF
if cutover "$ENABLE" >/dev/null 2>&1; then
  fail "cutover accepted a surviving scheduled process tree"
fi
[[ -e "$TMP/$DST/halt" ]] || fail "surviving process tree activated destination"
assert_status "$RECEIPT" source_unload_failed
python3 - "$RECEIPT" <<'PY'
import json
import sys

receipt = json.load(open(sys.argv[1]))
commands = {item["command"] for item in receipt["source_before"]["processes"]}
assert commands == {
    "/opaque/scheduled-root",
    "/opaque/arbitrary-child",
    "/usr/local/bin/copilot",
}, commands
PY
pass "complete launchd-rooted process tree is tracked without string allowlists"

prepare_case selftest-failed
printf '== result: 1 failure(s) ==\n' > "$TMP/$DST/selftest.out"
if cutover "$ENABLE" >/dev/null 2>&1; then
  fail "cutover accepted a failing selftest"
fi
[[ -e "$TMP/$SRC/services/com.test.dreaming.dreaming" ]] ||
  fail "failing selftest mutated source"
pass "failing selftest is refused before mutation"

prepare_case selftest-contradictory
printf '== result: 1 failure(s) ==\n== result: 0 failure(s) ==\n' \
  > "$TMP/$DST/selftest.out"
if cutover "$ENABLE" >/dev/null 2>&1; then
  fail "cutover accepted contradictory selftest output"
fi
pass "selftest must contain one exact terminal result"

prepare_case selftest-stale
printf 'different-generation\n' > "$TMP/$DST/dreaming/selftest-passed-generation"
if cutover "$ENABLE" >/dev/null 2>&1; then
  fail "cutover accepted stale-generation selftest evidence"
fi
[[ -e "$TMP/$SRC/services/com.test.dreaming.dreaming" ]] ||
  fail "stale-generation selftest mutated source"
pass "selftest evidence is bound to current activation generation"

prepare_case empty-enable
if cutover "" >/dev/null 2>&1; then
  fail "cutover accepted an empty enable command"
fi
[[ -e "$TMP/$SRC/services/com.test.dreaming.dreaming" ]] ||
  fail "empty enable command unloaded source"
[[ ! -e "$RECEIPT" ]] || fail "empty enable command created transaction receipt"
pass "empty enable command is refused before source mutation"

prepare_case enable-failed
if cutover "$ENABLE_FAIL" >/dev/null 2>&1; then
  fail "cutover accepted a failing enable command"
fi
assert_recovered "failing enable command" activation_failed
pass "failing enable command re-halts and records recovery"

prepare_case remains-halted
if cutover "$ENABLE_HALTED" >/dev/null 2>&1; then
  fail "cutover accepted a destination that remained halted"
fi
assert_recovered "remaining-halted verification" activation_failed
pass "remaining-halted destination is rolled back"

prepare_case scheduler-missing
if cutover "$ENABLE_NO_SERVICE" >/dev/null 2>&1; then
  fail "cutover accepted a destination without its scheduler"
fi
assert_recovered "missing-scheduler verification" activation_failed
pass "missing destination scheduler is rolled back"

ENABLE_SOURCE_CONFLICT="$TMP/enable-source-conflict"
prepare_case source-conflict
cat > "$ENABLE_SOURCE_CONFLICT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
rm -f "\$FAKE_ROOT/\$FAKE_HOST/halt"
touch "\$FAKE_ROOT/\$FAKE_HOST/services/com.test.dreaming.dreaming"
touch "\$FAKE_ROOT/$SRC/services/com.test.dreaming.dreaming"
EOF
chmod +x "$ENABLE_SOURCE_CONFLICT"
if cutover "$ENABLE_SOURCE_CONFLICT" >/dev/null 2>&1; then
  fail "cutover accepted source reactivation during enable"
fi
assert_recovered "source-conflict verification" activation_failed
[[ -e "$TMP/$SRC/services/com.test.dreaming.dreaming" ]] ||
  fail "source conflict fixture did not reactivate source"
pass "post-enable source conflict rolls destination back"

prepare_case post-enable-inspection
if cutover "$ENABLE_PRINT_ERROR" >/dev/null 2>&1; then
  fail "cutover accepted post-enable launchctl inspection failure"
fi
assert_recovered "post-enable inspection failure" recovery_failed
python3 - "$RECEIPT" <<'PY'
import json
import sys

receipt = json.load(open(sys.argv[1]))
assert receipt["recovery"]["errors"]
assert "halt_restored" in receipt["recovery"]["actions"]
PY
pass "unverifiable recovery remains durable and fail-closed"

SRC="happy-source"
DST="happy-destination"

record_refusal() {
  local name="$1" mode="$2"
  RECEIPT="$TMP/record-${name}-receipt.json"
  cp "$TMP/activated-template.json" "$RECEIPT"
  local evidence="$TMP/$DST/${name}.json"
  if [[ "$mode" == "empty" ]]; then
    : > "$evidence"
  elif [[ "$mode" == "non-json" ]]; then
    printf 'not json\n' > "$evidence"
  else
    write_run "$evidence" "$mode"
  fi
  if record_pass "$evidence" >/dev/null 2>&1; then
    fail "record-pass accepted $name evidence"
  fi
  assert_status "$RECEIPT" activated
}

record_refusal failed-status failed
record_refusal stale-start stale
record_refusal malformed non-json
record_refusal empty empty
record_refusal uncommitted uncommitted
record_refusal unterminated unterminated
record_refusal reversed-terminal reversed
pass "record-pass rejects incomplete, stale, malformed, and uncommitted evidence"

RECEIPT="$TMP/record-source-active-receipt.json"
cp "$TMP/activated-template.json" "$RECEIPT"
write_run "$TMP/$DST/source-active.json" ok
touch "$TMP/$SRC/services/com.test.dreaming.dreaming"
if record_pass "$TMP/$DST/source-active.json" >/dev/null 2>&1; then
  fail "record-pass accepted a reactivated source"
fi
assert_status "$RECEIPT" activated
rm "$TMP/$SRC/services/com.test.dreaming.dreaming"
pass "record-pass rechecks source ownership"

echo "cutover tests: PASS"
