#!/usr/bin/env bash
# Append one source-qualified lifecycle hint; scheduled discovery remains complete.

set -euo pipefail

SOURCE="${1:-}"
SESSION="${2:-}"
[[ "$SOURCE" == "copilot" || "$SOURCE" == "claude" || "$SOURCE" == "codex" ]] || {
  echo "usage: dreaming-enqueue.sh {copilot|claude|codex} <native-session-id>" >&2
  exit 2
}
[[ -n "$SESSION" && "$SESSION" != *:* ]] || {
  echo "dreaming-enqueue.sh: native session id must be nonempty and unqualified" >&2
  exit 2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
"$SCRIPT_DIR/../skills/skill-review/scripts/dreaming-core.py" enqueue \
  --source "$SOURCE" \
  --session "$SOURCE:$SESSION"
