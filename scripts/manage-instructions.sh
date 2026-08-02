#!/usr/bin/env bash
# Install and remove Dreaming's owned modular Copilot instruction file.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${DREAMING_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd -P)}"
STATE_DIR="${SKILLS_STATE_DIR:-$HOME/.copilot/skill-state}"
COPILOT_ROOT="${COPILOT_HOME:-$HOME/.copilot}"
SOURCE="${DREAMING_INSTRUCTIONS_SOURCE:-$REPO_ROOT/config/dreaming.instructions.md}"
TARGET="${DREAMING_INSTRUCTIONS_TARGET:-$COPILOT_ROOT/instructions/dreaming.instructions.md}"
HASH_FILE="${DREAMING_INSTRUCTIONS_HASH_FILE:-$STATE_DIR/dreaming/managed-instructions.sha256}"
TARGET_FILE="${DREAMING_INSTRUCTIONS_TARGET_FILE:-$STATE_DIR/dreaming/managed-instructions.target}"

sha256() {
  shasum -a 256 "$1" | awk '{print $1}'
}

atomic_write() {
  local source="$1" target="$2" mode="$3" temporary
  mkdir -p "$(dirname "$target")"
  temporary="$(dirname "$target")/.${target##*/}.$$"
  cp "$source" "$temporary"
  chmod "$mode" "$temporary"
  mv -f "$temporary" "$target"
}

atomic_hash() {
  local value="$1" temporary
  mkdir -p "$(dirname "$HASH_FILE")"
  temporary="$(dirname "$HASH_FILE")/.${HASH_FILE##*/}.$$"
  printf '%s\n' "$value" > "$temporary"
  chmod 600 "$temporary"
  mv -f "$temporary" "$HASH_FILE"
}

atomic_target() {
  local value="$1" temporary
  mkdir -p "$(dirname "$TARGET_FILE")"
  temporary="$(dirname "$TARGET_FILE")/.${TARGET_FILE##*/}.$$"
  printf '%s\n' "$value" > "$temporary"
  chmod 600 "$temporary"
  mv -f "$temporary" "$TARGET_FILE"
}

validate_hash() {
  [[ "$1" =~ ^[0-9a-f]{64}$ ]]
}

load_ownership() {
  [[ -f "$HASH_FILE" && -f "$TARGET_FILE" ]] || return 1
  TRACKED_HASH="$(tr -d '[:space:]' < "$HASH_FILE")"
  TRACKED_TARGET="$(cat "$TARGET_FILE")"
  validate_hash "$TRACKED_HASH" && [[ "$TRACKED_TARGET" = /* ]]
}

cmd_install() {
  local expected current tracked
  [[ -f "$SOURCE" && ! -L "$SOURCE" ]] || {
    echo "managed instruction source is missing or is a symlink: $SOURCE" >&2
    return 1
  }
  expected="$(sha256 "$SOURCE")"
  if [[ -L "$TARGET" ]]; then
    echo "refusing to replace instruction symlink: $TARGET" >&2
    return 1
  fi
  if [[ -f "$TARGET" ]]; then
    current="$(sha256 "$TARGET")"
    if [[ -f "$HASH_FILE" || -f "$TARGET_FILE" ]]; then
      load_ownership || {
        echo "managed instruction ownership record is malformed" >&2
        return 1
      }
      [[ "$TRACKED_TARGET" == "$TARGET" ]] || {
        echo "managed instruction is owned at a different target: $TRACKED_TARGET" >&2
        return 1
      }
      [[ "$current" == "$TRACKED_HASH" ]] || {
        echo "refusing to overwrite modified managed instruction: $TARGET" >&2
        return 1
      }
    elif [[ "$current" != "$expected" ]]; then
      echo "refusing to overwrite unowned instruction file: $TARGET" >&2
      return 1
    fi
  elif [[ -f "$HASH_FILE" || -f "$TARGET_FILE" ]]; then
    load_ownership || {
      echo "managed instruction ownership record is malformed" >&2
      return 1
    }
    [[ "$TRACKED_TARGET" == "$TARGET" ]] || {
      echo "managed instruction is owned at a different target: $TRACKED_TARGET" >&2
      return 1
    }
  fi

  atomic_write "$SOURCE" "$TARGET" 644
  [[ "$(sha256 "$TARGET")" == "$expected" ]] || {
    echo "installed instruction hash verification failed" >&2
    return 1
  }
  atomic_target "$TARGET"
  atomic_hash "$expected"
  echo "installed managed Copilot instructions: $TARGET"
}

cmd_verify() {
  local expected current tracked
  [[ -f "$SOURCE" && ! -L "$SOURCE" ]] ||
    { echo "managed instruction source missing" >&2; return 1; }
  [[ -f "$TARGET" && ! -L "$TARGET" ]] ||
    { echo "managed instruction target missing or invalid" >&2; return 1; }
  load_ownership ||
    { echo "managed instruction ownership record missing or malformed" >&2; return 1; }
  expected="$(sha256 "$SOURCE")"
  current="$(sha256 "$TARGET")"
  [[ "$TRACKED_TARGET" == "$TARGET" ]] ||
    { echo "managed instruction target ownership mismatch" >&2; return 1; }
  [[ "$expected" == "$TRACKED_HASH" && "$current" == "$TRACKED_HASH" ]] || {
    echo "managed instruction hash mismatch" >&2
    return 1
  }
  echo "verified managed Copilot instructions: $TARGET"
}

cmd_uninstall() {
  local current managed_target
  if [[ ! -f "$HASH_FILE" ]]; then
    rm -f "$TARGET_FILE"
    echo "no managed instruction ownership record; retained ${TARGET}"
    return 0
  fi
  if ! load_ownership; then
    echo "malformed ownership record; retained ${TARGET}" >&2
    return 0
  fi
  managed_target="$TRACKED_TARGET"
  if [[ ! -e "$managed_target" && ! -L "$managed_target" ]]; then
    rm -f "$HASH_FILE" "$TARGET_FILE"
    echo "managed instruction already absent"
    return 0
  fi
  if [[ -L "$managed_target" || ! -f "$managed_target" ]]; then
    echo "managed instruction path changed type; retained ${managed_target}" >&2
    return 0
  fi
  current="$(sha256 "$managed_target")"
  if [[ "$current" != "$TRACKED_HASH" ]]; then
    echo "managed instruction was modified; retained ${managed_target}" >&2
    return 0
  fi
  rm -f "$managed_target" "$HASH_FILE" "$TARGET_FILE"
  rmdir "$(dirname "$managed_target")" 2>/dev/null || true
  echo "removed managed Copilot instructions: $managed_target"
}

cmd_status() {
  if cmd_verify >/dev/null 2>&1; then
    echo "managed-instructions=verified target=$TARGET"
  elif [[ -f "$HASH_FILE" || -f "$TARGET_FILE" ]]; then
    echo "managed-instructions=drifted target=$TARGET"
    return 1
  else
    echo "managed-instructions=unmanaged target=$TARGET"
  fi
}

case "${1:-}" in
  install) cmd_install ;;
  verify) cmd_verify ;;
  uninstall) cmd_uninstall ;;
  status) cmd_status ;;
  *) echo "usage: manage-instructions.sh {install|verify|uninstall|status}" >&2; exit 2 ;;
esac
