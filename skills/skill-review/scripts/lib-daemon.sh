#!/usr/bin/env bash
# lib-daemon.sh — shared helpers for the skills self-learning launchd daemon.
# Sourced by daemon-run.sh and daemon-selftest.sh.
#
# Provides two things the unattended launchd jobs need but a normal interactive
# shell does not:
#
#  A. Non-interactive GitHub token auth (skills_setup_git_auth). Under a
#     background launchd GUI-session context the default git credential helper
#     (osxkeychain) prompts for keychain access and HANGS with no TTY — a plain
#     `git push` never returns. We export GH_TOKEN + an inline credential helper
#     via GIT_CONFIG_* so EVERY child git inherits it (including the git pushes
#     the headless `copilot` sweep makes). Nothing is persisted to git config and
#     the token is never written to logs. Token sources, first valid wins:
#       1. `gh auth token` — authoritative (correct account/push rights).
#       2. explicit login-keychain read (`security -w`, base64-decoded) — the
#          reads the token directly; survives the restricted launchd keychain
#          search list where (1) can fail. Fallback only.
#
#  B. A completion-aware bounded copilot runner (skills_run_copilot_bounded).
#     Under launchd the headless `copilot` process completes its work and prints
#     its end-of-session summary footer, then FAILS TO EXIT (sits at 0% CPU
#     indefinitely) — independent of MCP/remote flags. We watch the log for a
#     completion marker, allow a short flush grace, then TERM/KILL. Success is
#     judged by the marker, not the (watchdog-forced) exit code.

# Echo a usable GitHub token to stdout (empty if none). Never logs the token.
skills_derive_github_token() {
  local host="${1:-github.com}" tok raw
  tok="$(gh auth token -h "$host" 2>/dev/null || true)"
  if [[ -z "$tok" ]]; then
    raw="$(/usr/bin/security find-generic-password -s "gh:${host}" -a "$USER" -w \
          "$HOME/Library/Keychains/login.keychain-db" 2>/dev/null || true)"
    if [[ -n "$raw" ]]; then
      tok="$(printf '%s' "${raw#go-keyring-base64:}" | base64 -D 2>/dev/null || true)"
    fi
  fi
  printf '%s' "$tok"
}

# Export GH_TOKEN + an inline credential helper so all child git processes
# authenticate over HTTPS without the hanging osxkeychain helper. Returns
# non-zero (and exports nothing) if no token could be derived.
skills_setup_git_auth() {
  local host="${1:-github.com}" tok
  tok="$(skills_derive_github_token "$host")"
  [[ -n "$tok" ]] || return 1
  export GH_TOKEN="$tok"
  export GIT_TERMINAL_PROMPT=0
  # Belt-and-suspenders: make sure no git tracing is on that could echo the
  # credential exchange (and thus the token) into a log.
  unset GIT_TRACE GIT_TRACE_CURL GIT_CURL_VERBOSE GIT_TRACE_PACKET 2>/dev/null || true
  export GIT_CONFIG_COUNT=2
  export GIT_CONFIG_KEY_0="credential.helper";  export GIT_CONFIG_VALUE_0=""
  export GIT_CONFIG_KEY_1="credential.helper"
  export GIT_CONFIG_VALUE_1='!f() { test "$1" = get && printf "username=x-access-token\npassword=%s\n" "$GH_TOKEN"; }; f'
  return 0
}

skills_process_identity() {
  local pid="$1"
  /bin/ps -o lstart= -p "$pid" 2>/dev/null | /usr/bin/awk '{$1=$1; print}'
}

skills_process_group_alive() {
  local pgid="$1"
  [[ "$pgid" =~ ^[1-9][0-9]*$ ]] || return 1
  kill -0 "-$pgid" 2>/dev/null
}

skills_read_registered_pgid() {
  local path="$1" pgid=""
  [[ -n "$path" && -f "$path" ]] || return 1
  IFS= read -r pgid < "$path" || return 1
  [[ "$pgid" =~ ^[1-9][0-9]*$ ]] || return 1
  printf '%s\n' "$pgid"
}

skills_is_process_group_leader() {
  local pid="$1" pgid=""
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  pgid="$(/bin/ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')"
  [[ "$pgid" == "$pid" ]]
}

skills_terminate_supervised_groups() {
  local worker_pid="$1" pgid_file="$2" nested_pgid="" owned_nested_pgid=""
  local attempts="${3:-50}"
  local worker_group_owned=0

  if skills_is_process_group_leader "$worker_pid"; then
    worker_group_owned=1
  fi

  nested_pgid="$(skills_read_registered_pgid "$pgid_file" 2>/dev/null || true)"
  if [[ -n "$nested_pgid" ]] &&
      skills_is_process_group_leader "$nested_pgid"; then
    owned_nested_pgid="$nested_pgid"
  fi

  if (( worker_group_owned )); then
    kill -TERM "-$worker_pid" 2>/dev/null || true
  else
    kill -TERM "$worker_pid" 2>/dev/null || true
  fi

  for ((i = 0; i < attempts; i++)); do
    nested_pgid="$(skills_read_registered_pgid "$pgid_file" 2>/dev/null || true)"
    if [[ -n "$nested_pgid" && "$nested_pgid" != "$owned_nested_pgid" ]] &&
        skills_is_process_group_leader "$nested_pgid"; then
      owned_nested_pgid="$nested_pgid"
    fi
    if [[ -n "$owned_nested_pgid" ]] &&
        skills_process_group_alive "$owned_nested_pgid"; then
      kill -TERM "-$owned_nested_pgid" 2>/dev/null || true
    fi
    if ! kill -0 "$worker_pid" 2>/dev/null &&
        { [[ -z "$owned_nested_pgid" ]] ||
          ! skills_process_group_alive "$owned_nested_pgid"; } &&
        { [[ -z "$nested_pgid" ]] ||
          ! skills_process_group_alive "$nested_pgid"; }; then
      return 0
    fi
    sleep 0.2
  done

  if [[ -n "$owned_nested_pgid" ]] &&
      skills_process_group_alive "$owned_nested_pgid"; then
    kill -KILL "-$owned_nested_pgid" 2>/dev/null || true
  fi
  if kill -0 "$worker_pid" 2>/dev/null; then
    if (( worker_group_owned )); then
      kill -KILL "-$worker_pid" 2>/dev/null || true
    else
      kill -KILL "$worker_pid" 2>/dev/null || true
    fi
  fi
}

skills_lock_tool() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  printf '%s\n' "$script_dir/daemon-lock.py"
}

skills_lock_acquire() {
  local mode="$1" owner="$2"
  if [[ "$mode" == "process" ]]; then
    "$(skills_lock_tool)" acquire --mode process --owner "$owner" \
      --pid "$$" --process-identity "$(skills_process_identity "$$")"
  else
    "$(skills_lock_tool)" acquire --mode "$mode" --owner "$owner"
  fi
}

skills_lock_assert() {
  "$(skills_lock_tool)" assert "$1" --pid "$$" \
    --process-identity "$(skills_process_identity "$$" || true)"
}

skills_lock_renew() {
  "$(skills_lock_tool)" renew "$1" --pid "$$" \
    --process-identity "$(skills_process_identity "$$" || true)"
}

skills_lock_release() {
  "$(skills_lock_tool)" release "$1"
}

# Run copilot headlessly but DON'T trust it to exit (see header note B). Watch
# LOGFILE for DONE_RE; once seen, allow GRACE_SECS for final flushing then
# terminate the whole process GROUP (copilot spawns a --server child and git
# children — killing only the parent can orphan them). ABS_MAX_SECS is an
# absolute backstop. Returns 0 iff DONE_RE appeared in LOGFILE (work completed),
# regardless of how the process was reaped. LOGFILE must be FRESH per run so a
# stale footer can't cause a false-early completion.

dreaming_load_roots() {
  local script_dir default_repo config legacy_config
  local requested_repo requested_shared requested_public requested_receipt
  local requested_data requested_state requested_skills requested_adapter requested_compat
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  default_repo="$(cd "$script_dir/../../.." && pwd -P)"
  config="${DREAMING_CONFIG_FILE:-${DREAMING_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/dreaming}/config.env}"
  legacy_config="$HOME/.copilot/dreaming/config.env"
  if [[ -z "${DREAMING_CONFIG_FILE:-}" && ! -f "$config" && -f "$legacy_config" ]]; then
    config="$legacy_config"
  fi
  requested_repo="${DREAMING_REPO_ROOT:-}"
  requested_shared="${DREAMING_SHARED_SKILLS_ROOT:-}"
  requested_public="${SKILLS_REPO_ROOT:-}"
  requested_receipt="${DREAMING_RECEIPT_FILE:-}"
  requested_data="${DREAMING_DATA_DIR:-}"
  requested_state="${DREAMING_STATE_DIR:-}"
  requested_skills="${DREAMING_SKILLS_ROOT:-}"
  requested_adapter="${DREAMING_ADAPTER_CONFIG:-}"
  requested_compat="${DREAMING_ENABLE_COPILOT_COMPAT:-}"
  if [[ -f "$config" ]]; then
    # Generated by scripts/dreaming-deps.py.
    # shellcheck disable=SC1090
    source "$config"
  fi
  DREAMING_REPO_ROOT="${requested_repo:-${DREAMING_REPO_ROOT:-$default_repo}}"
  DREAMING_SHARED_SKILLS_ROOT="${requested_shared:-${DREAMING_SHARED_SKILLS_ROOT:-}}"
  SKILLS_REPO_ROOT="${requested_public:-${SKILLS_REPO_ROOT:-}}"
  DREAMING_RECEIPT_FILE="${requested_receipt:-${DREAMING_RECEIPT_FILE:-}}"
  DREAMING_DATA_DIR="${requested_data:-${DREAMING_DATA_DIR:-}}"
  DREAMING_STATE_DIR="${requested_state:-${DREAMING_STATE_DIR:-}}"
  DREAMING_SKILLS_ROOT="${requested_skills:-${DREAMING_SKILLS_ROOT:-}}"
  DREAMING_ADAPTER_CONFIG="${requested_adapter:-${DREAMING_ADAPTER_CONFIG:-}}"
  DREAMING_ENABLE_COPILOT_COMPAT="${requested_compat:-${DREAMING_ENABLE_COPILOT_COMPAT:-}}"
  export DREAMING_REPO_ROOT DREAMING_SHARED_SKILLS_ROOT SKILLS_REPO_ROOT
  export DREAMING_RECEIPT_FILE
  export DREAMING_DATA_DIR DREAMING_STATE_DIR DREAMING_SKILLS_ROOT
  export DREAMING_ADAPTER_CONFIG DREAMING_ENABLE_COPILOT_COMPAT
}

dreaming_require_roots() {
  dreaming_load_roots
  [[ -d "$DREAMING_REPO_ROOT/skills/skill-review" ]] || {
    echo "dreaming repository root is incomplete: $DREAMING_REPO_ROOT" >&2
    return 1
  }
  [[ -d "$DREAMING_SHARED_SKILLS_ROOT/skills/skill-create" &&
     -d "$DREAMING_SHARED_SKILLS_ROOT/skills/writing-great-skills" &&
     -d "$DREAMING_SHARED_SKILLS_ROOT/skills/dual-review" &&
     -d "$DREAMING_SHARED_SKILLS_ROOT/skills/authenticated-browse" ]] || {
    echo "verified shared skills root is incomplete: ${DREAMING_SHARED_SKILLS_ROOT:-unset}" >&2
    return 1
  }
  local repo_real shared_real public_real
  repo_real="$(cd "$DREAMING_REPO_ROOT" && pwd -P)"
  shared_real="$(cd "$DREAMING_SHARED_SKILLS_ROOT" && pwd -P)"
  [[ "$repo_real" != "$shared_real" ]] || {
    echo "dreaming and shared roots alias" >&2
    return 1
  }
  if [[ -n "$SKILLS_REPO_ROOT" && -d "$SKILLS_REPO_ROOT" ]]; then
    public_real="$(cd "$SKILLS_REPO_ROOT" && pwd -P)"
    [[ "$repo_real" != "$public_real" && "$shared_real" != "$public_real" ]] || {
      echo "dreaming/shared/public roots must have distinct canonical paths" >&2
      return 1
    }
  fi
  DREAMING_RECEIPT_FILE="${DREAMING_RECEIPT_FILE:-$DREAMING_REPO_ROOT/scripts/shared-deps-receipt.json}" \
    "$DREAMING_REPO_ROOT/scripts/dreaming-deps.py" verify \
      "$DREAMING_SHARED_SKILLS_ROOT" >/dev/null || {
    echo "shared skills root failed compatibility verification" >&2
    return 1
  }
}

dreaming_build_plugin_args() {
  dreaming_require_roots || return 1
  DREAMING_PLUGIN_ARGS=(
    --plugin-dir "$DREAMING_REPO_ROOT"
    --plugin-dir "$DREAMING_SHARED_SKILLS_ROOT"
  )
}
#
#   skills_run_copilot_bounded LOGFILE DONE_RE ABS_MAX_SECS GRACE_SECS -- COPILOT_BIN ARGS...
skills_run_copilot_bounded() (
  local log="$1" done_re="$2" abs_max="$3" grace="$4"; shift 4
  [[ "${1:-}" == "--" ]] && shift
  local cpid="" cpgid="" waited=0 done_at=-1
  local copilot_group_owned=0
  local pgid_file="${DREAMING_CHILD_PGID_FILE:-}"
  unregister_copilot_group() {
    local registered=""
    [[ -n "$pgid_file" && -n "$cpgid" ]] || return 0
    registered="$(skills_read_registered_pgid "$pgid_file" 2>/dev/null || true)"
    if [[ "$registered" == "$cpgid" ]] &&
        ! skills_process_group_alive "$cpgid"; then
      rm -f "$pgid_file"
    fi
  }
  terminate_copilot_group() {
    local grace_secs="${1:-5}"
    if [[ -z "$cpid" ]]; then
      cpid="$(jobs -pr 2>/dev/null | head -1)"
    fi
    if [[ -z "$cpgid" ]]; then
      cpgid="$cpid"
    fi
    if (( ! copilot_group_owned )) &&
        skills_is_process_group_leader "$cpgid"; then
      copilot_group_owned=1
    fi
    if (( copilot_group_owned )) &&
        skills_process_group_alive "$cpgid"; then
      if (( grace_secs > 0 )); then
        kill -TERM "-$cpgid" 2>/dev/null || true
        sleep "$grace_secs"
      fi
      if (( grace_secs == 0 )) ||
          skills_process_group_alive "$cpgid"; then
        kill -KILL "-$cpgid" 2>/dev/null || true
      fi
    elif [[ -n "$cpid" ]] && kill -0 "$cpid" 2>/dev/null; then
      if (( grace_secs > 0 )); then
        kill -TERM "$cpid" 2>/dev/null || true
        sleep "$grace_secs"
      fi
      if (( grace_secs == 0 )) || kill -0 "$cpid" 2>/dev/null; then
        kill -KILL "$cpid" 2>/dev/null || true
      fi
    fi
    wait "$cpid" 2>/dev/null || true
  }
  trap 'trap "" INT TERM; terminate_copilot_group; exit 143' INT TERM
  trap unregister_copilot_group EXIT
  # Monitor mode so the backgrounded job leads its own process group (pgid==pid),
  # enabling a whole-tree kill via the negative pid. stdin from /dev/null so a
  # stray read can never block the run.
  set -m 2>/dev/null || true
  "$@" </dev/null >>"$log" 2>&1 &
  cpid=$!
  cpgid=$cpid
  set +m 2>/dev/null || true
  if ! skills_is_process_group_leader "$cpid"; then
    kill -TERM "$cpid" 2>/dev/null || true
    wait "$cpid" 2>/dev/null || true
    return 1
  fi
  copilot_group_owned=1
  if [[ -n "$pgid_file" ]]; then
    local pgid_tmp="${pgid_file}.$$"
    umask 077
    printf '%s\n' "$cpgid" > "$pgid_tmp"
    mv -f "$pgid_tmp" "$pgid_file"
  fi
  while kill -0 "$cpid" 2>/dev/null; do
    if (( done_at < 0 )) && grep -qE "$done_re" "$log" 2>/dev/null; then
      done_at=$waited
    fi
    if (( waited >= abs_max )); then
      terminate_copilot_group 0
      break
    fi
    if (( done_at >= 0 && waited - done_at >= grace )); then
      terminate_copilot_group
      break
    fi
    sleep 3; waited=$((waited+3))
  done
  wait "$cpid" 2>/dev/null || true
  if (( copilot_group_owned )) &&
      skills_process_group_alive "$cpgid"; then
    terminate_copilot_group
  fi
  grep -qE "$done_re" "$log" 2>/dev/null
)
