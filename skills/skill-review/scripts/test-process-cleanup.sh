#!/usr/bin/env bash

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." && pwd)"
TEST_ROOT="$REPO/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/process-cleanup.XXXXXX")"
supervisor_pid=""
nested_pgid=""

cleanup() {
  trap '' INT TERM
  if [[ "$nested_pgid" =~ ^[1-9][0-9]*$ ]]; then
    kill -KILL "-$nested_pgid" 2>/dev/null || true
  fi
  if [[ "$supervisor_pid" =~ ^[1-9][0-9]*$ ]]; then
    kill -KILL "$supervisor_pid" 2>/dev/null || true
    wait "$supervisor_pid" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT INT TERM

# shellcheck source=lib-daemon.sh
source "$SCRIPT_DIR/lib-daemon.sh"

FAKE="$TMP/term-ignoring-tree.sh"
cat > "$FAKE" <<'SH'
#!/usr/bin/env bash
echo "$$" > "$LEADER_FILE"
bash -c 'trap "" TERM; echo "$$" > "$DESCENDANT_FILE"; while :; do sleep 1; done' &
while :; do sleep 1; done
SH
chmod +x "$FAKE"

export LEADER_FILE="$TMP/leader.pid"
export DESCENDANT_FILE="$TMP/descendant.pid"
export DREAMING_CHILD_PGID_FILE="$TMP/nested.pgid"
: > "$TMP/log"

SUPERVISOR="$TMP/supervisor.sh"
cat > "$SUPERVISOR" <<'SH'
#!/usr/bin/env bash
set -u
# shellcheck source=lib-daemon.sh
source "$LIB_DAEMON"
worker_pid=""
interrupted() {
  trap '' INT TERM
  if [[ -z "$worker_pid" ]]; then
    worker_pid="$(jobs -pr 2>/dev/null | head -1)"
  fi
  if [[ -n "$worker_pid" ]]; then
    skills_terminate_supervised_groups "$worker_pid" "$DREAMING_CHILD_PGID_FILE" 10
    wait "$worker_pid" 2>/dev/null || true
  fi
  exit 143
}
trap interrupted INT TERM
set -m 2>/dev/null || true
skills_run_copilot_bounded "$LOG_FILE" "NEVER_MATCH" 60 1 -- "$FAKE_COMMAND" &
worker_pid=$!
set +m 2>/dev/null || true
printf '%s\n' "$worker_pid" > "$WORKER_FILE"
wait "$worker_pid"
SH
chmod +x "$SUPERVISOR"

export LIB_DAEMON="$SCRIPT_DIR/lib-daemon.sh"
export LOG_FILE="$TMP/log"
export FAKE_COMMAND="$FAKE"
export WORKER_FILE="$TMP/worker.pid"
"$SUPERVISOR" &
supervisor_pid=$!

for _ in {1..50}; do
  if [[ -s "$DREAMING_CHILD_PGID_FILE" &&
        -s "$LEADER_FILE" &&
        -s "$DESCENDANT_FILE" &&
        -s "$WORKER_FILE" ]]; then
    break
  fi
  sleep 0.1
done

nested_pgid="$(skills_read_registered_pgid "$DREAMING_CHILD_PGID_FILE")"
leader_pid="$(cat "$LEADER_FILE")"
descendant_pid="$(cat "$DESCENDANT_FILE")"
[[ "$leader_pid" =~ ^[1-9][0-9]*$ && "$descendant_pid" =~ ^[1-9][0-9]*$ ]]
[[ "$(/bin/ps -o pgid= -p "$leader_pid" | tr -d ' ')" == "$nested_pgid" ]]
[[ "$(/bin/ps -o pgid= -p "$descendant_pid" | tr -d ' ')" == "$nested_pgid" ]]

kill -TERM "$supervisor_pid"
status=0
wait "$supervisor_pid" || status=$?
supervisor_pid=""
[[ "$status" == "143" ]]

for _ in {1..20}; do
  if ! kill -0 "$leader_pid" 2>/dev/null &&
      ! kill -0 "$descendant_pid" 2>/dev/null; then
    break
  fi
  sleep 0.1
done

! kill -0 "$leader_pid" 2>/dev/null
! kill -0 "$descendant_pid" 2>/dev/null
! skills_process_group_alive "$nested_pgid"
nested_pgid=""
echo "PASS  registered nested process group survives leader exit and is fully reaped"

rm -f "$LEADER_FILE" "$DESCENDANT_FILE" "$DREAMING_CHILD_PGID_FILE"
: > "$TMP/direct.log"
status=0
skills_run_copilot_bounded \
  "$TMP/direct.log" "NEVER_MATCH" 1 1 -- "$FAKE" || status=$?
[[ "$status" != "0" ]]
direct_leader_pid="$(cat "$LEADER_FILE")"
direct_descendant_pid="$(cat "$DESCENDANT_FILE")"
! kill -0 "$direct_leader_pid" 2>/dev/null
! kill -0 "$direct_descendant_pid" 2>/dev/null
echo "PASS  bounded cleanup kills descendants after their leader exits"
