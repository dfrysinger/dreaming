#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CASE="$(mktemp -d "${TMPDIR:-/tmp}/dreaming-daemon-pass.XXXXXX")"
trap 'rm -rf "$CASE"' EXIT

PROMPT="$CASE/prompt.txt"
LOG="$CASE/pass.log"
FAKE_COPILOT="$CASE/copilot"
INVOCATION="$CASE/invocation.txt"
FAKE_RECONCILER="$CASE/curator-run"
RECONCILE_LOG="$CASE/reconcile.txt"

printf '%s\n' '/skill-curator scheduled-live' > "$PROMPT"
cat > "$FAKE_COPILOT" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$FAKE_COPILOT_INVOCATION"
printf '%s\n' \
  'DREAM_PASS_RESULT: ok reviewed=94 archived=0 consolidated=0 plugins_disabled=0 recommendations=94' \
  'AI Credits 1'
SH
chmod +x "$FAKE_COPILOT"
cat > "$FAKE_RECONCILER" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$FAKE_RECONCILE_LOG"
printf '%s\n' '{"ok": true, "state": "already-terminal"}'
SH
chmod +x "$FAKE_RECONCILER"

export COPILOT_BIN="$FAKE_COPILOT"
export FAKE_COPILOT_INVOCATION="$INVOCATION"
export DREAMING_CURATOR_RUNNER="$FAKE_RECONCILER"
export FAKE_RECONCILE_LOG="$RECONCILE_LOG"
export DREAMING_ADAPTER_CONFIG="$CASE/adapters.json"
export DREAMING_REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
export DREAMING_PASS_MAX_SECS=30
export DREAMING_PASS_GRACE_SECS=0
export SKILLS_CURATOR_RUNS_DIR="$CASE/curator-runs"

result="$(
  "$SCRIPT_DIR/daemon-pass.sh" \
    --prompt "$PROMPT" \
    --name skills-prune \
    --log "$LOG"
)"

[[ "$result" == "ok reviewed=94 archived=0 consolidated=0 plugins_disabled=0 recommendations=94" ]]
[[ -s "$INVOCATION" ]]
grep -q -- '/skill-curator scheduled-live' "$INVOCATION"
grep -q -- '--allow-all' "$INVOCATION"
grep -q -- '--no-remote' "$INVOCATION"

cat > "$FAKE_RECONCILER" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$FAKE_RECONCILE_LOG"
printf '%s\n' '{"ok": true, "state": "aborted-active"}'
SH
chmod +x "$FAKE_RECONCILER"
cat > "$FAKE_COPILOT" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$DREAMING_ESTATE_SESSION_ID" > "$FAKE_COPILOT_INVOCATION"
printf '%s\n' 'AI Credits 1'
SH
chmod +x "$FAKE_COPILOT"
export DREAMING_PARENT_RUN_ID=fixture-parent
if "$SCRIPT_DIR/daemon-pass.sh" \
    --prompt "$PROMPT" \
    --name skills-prune \
    --log "$LOG" >/dev/null 2>&1; then
  echo "daemon pass accepted an unfinished estate session" >&2
  exit 1
fi
[[ "$(<"$INVOCATION")" == "fixture-parent-estate-review" ]]
grep -q -- 'estate-session-reconcile --run fixture-parent-estate-review' \
  "$RECONCILE_LOG"

cat > "$FAKE_RECONCILER" <<'SH'
#!/usr/bin/env bash
printf '%s\n' '{"ok": true, "state": "absent"}'
SH
chmod +x "$FAKE_RECONCILER"
cat > "$FAKE_COPILOT" <<'SH'
#!/usr/bin/env bash
printf '%s\n' \
  'DREAM_PASS_RESULT: ok reviewed=94 archived=0 consolidated=0 plugins_disabled=0 recommendations=94' \
  'AI Credits 1'
SH
chmod +x "$FAKE_COPILOT"
if "$SCRIPT_DIR/daemon-pass.sh" \
    --prompt "$PROMPT" \
    --name skills-prune \
    --log "$LOG" >/dev/null 2>&1; then
  echo "daemon pass accepted success without an estate session" >&2
  exit 1
fi

STANDALONE="$CASE/standalone"
mkdir -p "$STANDALONE"
cp "$SCRIPT_DIR/daemon-pass.sh" "$SCRIPT_DIR/lib-daemon.sh" "$STANDALONE/"
cat > "$STANDALONE/dreaming-core.py" <<'PY'
#!/usr/bin/env python3
import json
import os
import time
from pathlib import Path

state = Path(os.environ["FAKE_CORE_STATE"])
state.mkdir(parents=True, exist_ok=True)
(state / "pid").write_text(str(os.getpid()))
time.sleep(10)
print(json.dumps({"ok": True}))
PY
chmod +x "$STANDALONE/dreaming-core.py"
export FAKE_CORE_STATE="$CASE/core-state"
export DREAMING_PASS_MAX_SECS=1
export DREAMING_PASS_GRACE_SECS=0
started="$(date +%s)"
if "$STANDALONE/daemon-pass.sh" \
    --prompt "$PROMPT" \
    --name skills-consolidate \
    --log "$LOG" >/dev/null 2>&1; then
  echo "daemon pass accepted a standalone core that exceeded its bound" >&2
  exit 1
fi
elapsed="$(( $(date +%s) - started ))"
(( elapsed < 10 )) ||
  { echo "standalone core outlived the configured pass bound" >&2; exit 1; }
core_pid="$(<"$FAKE_CORE_STATE/pid")"
if kill -0 "$core_pid" 2>/dev/null; then
  echo "standalone core process remained alive after the pass bound" >&2
  exit 1
fi

echo "daemon pass tests: PASS"
