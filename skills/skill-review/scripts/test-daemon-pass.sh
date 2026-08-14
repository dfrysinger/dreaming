#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CASE="$(mktemp -d "${TMPDIR:-/tmp}/dreaming-daemon-pass.XXXXXX")"
trap 'rm -rf "$CASE"' EXIT

PROMPT="$CASE/prompt.txt"
LOG="$CASE/pass.log"
FAKE_COPILOT="$CASE/copilot"
INVOCATION="$CASE/invocation.txt"

printf '%s\n' '/skill-curator scheduled-live' > "$PROMPT"
cat > "$FAKE_COPILOT" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$FAKE_COPILOT_INVOCATION"
printf '%s\n' \
  'DREAM_PASS_RESULT: ok reviewed=94 archived=0 consolidated=0 plugins_disabled=0 recommendations=94' \
  'AI Credits 1'
SH
chmod +x "$FAKE_COPILOT"

export COPILOT_BIN="$FAKE_COPILOT"
export FAKE_COPILOT_INVOCATION="$INVOCATION"
export DREAMING_ADAPTER_CONFIG="$CASE/adapters.json"
export DREAMING_REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
export DREAMING_SHARED_SKILLS_ROOT="${DREAMING_SHARED_SKILLS_ROOT:?}"
export DREAMING_PASS_MAX_SECS=30
export DREAMING_PASS_GRACE_SECS=0

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

echo "daemon pass tests: PASS"
