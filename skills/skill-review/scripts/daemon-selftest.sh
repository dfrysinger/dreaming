#!/usr/bin/env bash
# Preflight for the effective-weekly dreaming daemon.

set -u
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
COPILOT="${COPILOT_BIN:-$HOME/.local/bin/copilot}"
COPILOT_COMPAT="${DREAMING_ENABLE_COPILOT_COMPAT:-1}"
if [[ "$COPILOT_COMPAT" == "1" ]]; then
  STATE_DIR="${SKILLS_STATE_DIR:-$HOME/.copilot/skill-state}"
  LOCAL_ROOT="${SKILLS_LOCAL_ROOT:-$HOME/.copilot/skills}"
else
  STATE_DIR="${DREAMING_STATE_DIR:-${SKILLS_STATE_DIR:-$HOME/.local/state/dreaming}}"
  LOCAL_ROOT="${DREAMING_SKILLS_ROOT:-${SKILLS_LOCAL_ROOT:-$HOME/.local/share/dreaming/skills}}"
fi
HALT_SWITCH="$STATE_DIR/skill-review/disable-daemon"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-daemon.sh
source "$SCRIPT_DIR/lib-daemon.sh"
if [[ "$COPILOT_COMPAT" == "1" ]]; then
  dreaming_build_plugin_args || exit 1
fi
REPO="$DREAMING_REPO_ROOT"
SHARED_ROOT="$DREAMING_SHARED_SKILLS_ROOT"

RESULT="$STATE_DIR/daemon-selftest.out"
mkdir -p "$STATE_DIR"
: > "$RESULT"
fails=0
active_child_pid=""
active_child_pgid_file="$STATE_DIR/.selftest-child-pgid.$$"
rm -f "$active_child_pgid_file"
export DREAMING_CHILD_PGID_FILE="$active_child_pgid_file"
ok() { echo "PASS  $*" | tee -a "$RESULT"; }
bad() { echo "FAIL  $*" | tee -a "$RESULT"; fails=$((fails + 1)); }
warn() { echo "WARN  $*" | tee -a "$RESULT"; }
interrupted() {
  trap '' INT TERM
  if [[ -z "$active_child_pid" ]]; then
    active_child_pid="$(jobs -pr 2>/dev/null | head -1)"
  fi
  if [[ -n "$active_child_pid" ]]; then
    skills_terminate_supervised_groups \
      "$active_child_pid" "$active_child_pgid_file" 50
    wait "$active_child_pid" 2>/dev/null || true
    active_child_pid=""
  fi
  rm -f "$active_child_pgid_file"
  bad "self-test interrupted before completion"
  echo "== result: $fails failure(s) ==" | tee -a "$RESULT"
  exit 143
}
trap interrupted INT TERM
run_signal_responsive() {
  rm -f "$active_child_pgid_file"
  set -m 2>/dev/null || true
  "$@" &
  active_child_pid=$!
  set +m 2>/dev/null || true
  if ! skills_is_process_group_leader "$active_child_pid"; then
    kill -TERM "$active_child_pid" 2>/dev/null || true
    wait "$active_child_pid" 2>/dev/null || true
    active_child_pid=""
    return 1
  fi
  wait "$active_child_pid"
  local status=$?
  active_child_pid=""
  rm -f "$active_child_pgid_file"
  return "$status"
}
run_isolated_test() {
  run_signal_responsive env -u DREAMING_ADAPTER_CONFIG \
    -u DREAMING_ADAPTER_CONFIG_MANAGED \
    -u DREAMING_ADAPTER_CONFIG_SHA256 \
    -u DREAMING_ADAPTER_ALLOWED_ROOT \
    -u DREAMING_CONFIG_FILE \
    -u DREAMING_CONFIG_POINTER \
    -u DREAMING_CONFIGURE_NATIVE_ADAPTERS \
    -u DREAMING_DEPS_DIR \
    -u DREAMING_EXECUTOR_TEST_ALLOW_ROOT \
    -u DREAMING_EXECUTOR_TEST_ALLOW_ROOTS \
    -u DREAMING_ORCHESTRATOR_STATE_DIR \
    -u DREAMING_DATA_DIR \
    -u DREAMING_STATE_DIR \
    -u DREAMING_SKILLS_ROOT \
    -u DREAMING_SHARED_SKILLS_ROOT \
    -u DREAMING_SHARED_BUNDLE_ID \
    -u DREAMING_SHARED_SOURCE_KIND \
    -u DREAMING_SHARED_PROTOCOL \
    -u DREAMING_SHARED_REVISION \
    -u DREAMING_SESSION_SOURCES \
    -u DREAMING_REVIEW_EXECUTORS \
    -u DREAMING_SKILL_TARGETS \
    -u DREAMING_SOURCE_EXECUTOR_ALLOW \
    -u DREAMING_COPILOT_BIN \
    -u DREAMING_CLAUDE_BIN \
    -u DREAMING_CODEX_BIN \
    -u DREAMING_COPILOT_SESSION_ROOT \
    -u DREAMING_CLAUDE_PROJECTS_ROOT \
    -u DREAMING_CODEX_ROLLOUT_ROOT \
    -u DREAMING_ENABLE_COPILOT_COMPAT \
    -u DREAMING_LIFECYCLE_LOCK_HELD \
    -u DREAMING_RECEIPT_FILE \
    -u SKILLS_STATE_DIR \
    -u SKILLS_LOCAL_ROOT \
    -u COPILOT_HOME \
    "$@"
}

echo "== dreaming self-test $(date '+%Y-%m-%dT%H:%M:%S%z') ==" | tee -a "$RESULT"
if [[ "$COPILOT_COMPAT" == "1" ]]; then
  [[ -x "$COPILOT" ]] && ok "copilot executable" || bad "copilot not executable at $COPILOT"
else
  [[ -n "${DREAMING_ADAPTER_CONFIG:-}" && -f "$DREAMING_ADAPTER_CONFIG" ]] &&
    ok "standalone adapter configuration" ||
    bad "standalone adapter configuration missing"
fi
"$REPO/scripts/dreaming-deps.py" verify "$SHARED_ROOT" >/dev/null 2>&1 &&
  ok "verified shared dependency bundle" || bad "shared dependency bundle verification"

MARKER='[AUTOREVIEW-DAEMON-SESSION:96efca49-7380-4494-86c4-ab4ab954ee3f]'
PROMPTS=(
  "$REPO/skills/skill-review/references/sweep-prompt.txt"
  "$REPO/skills/memory-curator/references/memory-curate-prompt.txt"
  "$REPO/skills/skill-curator/references/tick-prompt.txt"
)
for prompt in "${PROMPTS[@]}"; do
  if [[ -f "$prompt" && "$(grep -m1 . "$prompt")" == "$MARKER" ]] &&
      grep -q 'DREAM_PASS_RESULT: ok' "$prompt" &&
      grep -q 'DREAM_PASS_RESULT: aborted' "$prompt"; then
    ok "prompt contract: $(basename "$prompt")"
  else
    bad "prompt contract missing: $prompt"
  fi
done
SWEEP_PROMPT="$REPO/skills/skill-review/references/sweep-prompt.txt"
if grep -q 'Create zero new skills' "$SWEEP_PROMPT" &&
    grep -q 'autonomous-create-requires-recurrence' "$SWEEP_PROMPT" &&
    grep -q 'DREAM_PASS_RESULT: ok created=0' "$SWEEP_PROMPT" &&
    ! grep -q 'Create at most .*NEW skills' "$SWEEP_PROMPT" &&
    ! grep -q 'mark new skills' "$SWEEP_PROMPT"; then
  ok "sweep prompt forbids autonomous creation"
else
  bad "sweep prompt authorizes autonomous creation"
fi

CURATOR_PROMPT="$REPO/skills/skill-curator/references/curator-prompt.md"
CURATOR_TICK="$REPO/skills/skill-curator/references/tick-prompt.txt"
if grep -q 'Completed-project pruning lane' "$CURATOR_PROMPT" &&
    grep -q 'default to 14 days' "$CURATOR_PROMPT" &&
    grep -q 'completed-project pruning lane' "$CURATOR_TICK" &&
    grep -q 'config_overrides.completed_project_cooldown_days' "$CURATOR_TICK" &&
    grep -q 'permanent exact/fuzzy name-family' "$CURATOR_TICK"; then
  ok "curator completed-project policy"
else
  bad "curator completed-project policy missing"
fi

if grep -q 'implicit_pin=yes' "$CURATOR_PROMPT" &&
    grep -q 'scheduled-skill-deps.py --inventory' "$CURATOR_TICK" &&
    grep -q 'Include `implicit_pin`' "$CURATOR_TICK"; then
  ok "curator scheduled dependency policy"
else
  bad "curator scheduled dependency policy missing"
fi
if grep -q 'standing approval' "$CURATOR_TICK" &&
    grep -q 'estate-recommendation.py' "$CURATOR_TICK" &&
    grep -q 'estate-authorize' "$CURATOR_TICK" &&
    grep -q 'plugins_disabled=' "$CURATOR_TICK"; then
  ok "curator autonomous estate policy"
else
  bad "curator autonomous estate policy missing"
fi

for script in daemon-pass.sh daemon-run.sh daemon-lock.sh daemon-lock.py \
  dreaming-run.sh dreaming-state.py test-daemon-pass.sh test-dreaming-daemon.sh \
  test-process-cleanup.sh \
  dreaming-core.py test-dreaming-core.sh dreaming-estate.py \
  test-dreaming-estate.sh dreaming-vendor-adapter.py \
  test-vendor-adapters.sh dreaming-dashboard.py test-dreaming-dashboard.sh \
  test-dreaming-dashboard-contracts.sh \
  evidence-envelope.py append-skill-evidence.sh mark-agent-created.sh \
  test-evidence-envelope.sh skill-evaluation.py run-skill-evaluation.sh \
  test-skill-evaluation.sh test-cross-cli-evaluation.sh \
  build-evaluation-input-source.py test-evaluation-input-source-builder.sh \
  skill-evaluation-harness.py fake-skill-evaluation-adapter.py \
  test-skill-evaluation-harness.sh test-skill-evaluation-vendor-adapters.sh \
  test-dreaming-certification.sh; do
  [[ -x "$SCRIPT_DIR/$script" ]] && ok "executable: $script" || bad "not executable: $script"
done

ROOT_SCRIPT_DIR="$REPO/scripts"
for script in install.sh dreaming-deps.py test-shared-deps.sh \
  test-headless-roots.sh test-installer.sh test-repository-boundary.sh \
  validate-private-boundary.py test-private-boundary.py \
  manage-instructions.sh configure-adapters.py migrate-copilot-state.py \
  ssh-session-source.py ssh-skill-publisher.py test-ssh-skill-publisher.py \
  ssh-estate-census.py test-ssh-estate-census.py \
  estate-action.py estate-action-adapter.py \
  test-estate-action.py test-estate-action-adapter.py \
  estate-recommendation.py test-estate-recommendation.py \
  plugin-settings-transaction.py plugin-runtime-inventory.py \
  qualify-plugin-settings.py ssh-plugin-settings.py \
  configure-plugin-estate-executors.py \
  test-plugin-settings-transaction.py test-plugin-remote-executor.py \
  dreaming-enqueue.sh test-copilot-migration.sh \
  transfer-dreaming-host.py test-transfer-dreaming-host.sh \
  cutover-dreaming-host.py test-cutover-dreaming-host.sh \
  validate-plugin-manifests.mjs; do
  [[ -x "$ROOT_SCRIPT_DIR/$script" ]] && ok "executable: scripts/$script" ||
    bad "not executable: scripts/$script"
done
if [[ "$COPILOT_COMPAT" == "1" ]]; then
  if "$ROOT_SCRIPT_DIR/manage-instructions.sh" verify >>"$RESULT" 2>&1; then
    ok "managed Copilot instructions"
  else
    bad "managed Copilot instructions"
  fi
else
  ok "Copilot instruction management disabled"
fi

MANAGE_SCRIPT_DIR="$REPO/skills/skill-manage/scripts"
for script in promotion-review.py promote-skill.sh test-promotion-review.sh; do
  [[ -x "$MANAGE_SCRIPT_DIR/$script" ]] && ok "executable: skill-manage/$script" ||
    bad "not executable: skill-manage/$script"
done

CURATOR_SCRIPT_DIR="$REPO/skills/skill-curator/scripts"
for script in scheduled-skill-deps.py curator-run.py \
  test-scheduled-skill-deps.sh test-curator-run.sh; do
  [[ -x "$CURATOR_SCRIPT_DIR/$script" ]] && ok "executable: skill-curator/$script" ||
    bad "not executable: skill-curator/$script"
done

if run_isolated_test "$SCRIPT_DIR/test-dreaming-daemon.sh" --quick >>"$RESULT" 2>&1; then
  ok "deterministic dreaming checks"
else
  bad "deterministic dreaming checks"
fi
if run_isolated_test "$SCRIPT_DIR/test-daemon-pass.sh" >>"$RESULT" 2>&1; then
  ok "scheduled curator pass checks"
else
  bad "scheduled curator pass checks"
fi
if run_isolated_test "$SCRIPT_DIR/test-process-cleanup.sh" >>"$RESULT" 2>&1; then
  ok "nested process cleanup checks"
else
  bad "nested process cleanup checks"
fi
if run_isolated_test "$SCRIPT_DIR/test-dreaming-core.sh" >>"$RESULT" 2>&1; then
  ok "deterministic standalone core checks"
else
  bad "deterministic standalone core checks"
fi
if run_isolated_test "$SCRIPT_DIR/test-dreaming-estate.sh" >>"$RESULT" 2>&1; then
  ok "deterministic estate census checks"
else
  bad "deterministic estate census checks"
fi
if run_isolated_test "$SCRIPT_DIR/test-dreaming-dashboard-contracts.sh" >>"$RESULT" 2>&1; then
  ok "deterministic dashboard contract checks"
else
  bad "deterministic dashboard contract checks"
fi
if run_isolated_test "$SCRIPT_DIR/test-dreaming-dashboard.sh" >>"$RESULT" 2>&1; then
  ok "deterministic dashboard boundary and scale checks"
else
  bad "deterministic dashboard boundary and scale checks"
fi
if run_isolated_test "$SCRIPT_DIR/test-vendor-adapters.sh" >>"$RESULT" 2>&1; then
  ok "deterministic native adapter and CLI matrix checks"
else
  bad "deterministic native adapter and CLI matrix checks"
fi
if run_isolated_test "$SCRIPT_DIR/test-evidence-envelope.sh" >>"$RESULT" 2>&1; then
  ok "deterministic evidence-envelope checks"
else
  bad "deterministic evidence-envelope checks"
fi
if run_isolated_test "$SCRIPT_DIR/test-skill-evaluation.sh" >>"$RESULT" 2>&1; then
  ok "deterministic skill-evaluation checks"
else
  bad "deterministic skill-evaluation checks"
fi
if run_isolated_test "$SCRIPT_DIR/test-cross-cli-evaluation.sh" >>"$RESULT" 2>&1; then
  ok "deterministic cross-CLI evaluation checks"
else
  bad "deterministic cross-CLI evaluation checks"
fi
if run_isolated_test "$SCRIPT_DIR/test-evaluation-input-source-builder.sh" >>"$RESULT" 2>&1; then
  ok "deterministic evaluation-input source-builder checks"
else
  bad "deterministic evaluation-input source-builder checks"
fi
if run_isolated_test "$SCRIPT_DIR/test-skill-evaluation-harness.sh" >>"$RESULT" 2>&1; then
  ok "deterministic trial-harness checks"
else
  bad "deterministic trial-harness checks"
fi
if run_isolated_test "$SCRIPT_DIR/test-skill-evaluation-vendor-adapters.sh" >>"$RESULT" 2>&1; then
  ok "deterministic skill-evaluation adapter checks"
else
  bad "deterministic skill-evaluation adapter checks"
fi
if run_isolated_test "$SCRIPT_DIR/test-dreaming-certification.sh" >>"$RESULT" 2>&1; then
  ok "deterministic Dreaming certification checks"
else
  bad "deterministic Dreaming certification checks"
fi
if run_isolated_test "$MANAGE_SCRIPT_DIR/test-promotion-review.sh" >>"$RESULT" 2>&1; then
  ok "deterministic promotion checks"
else
  bad "deterministic promotion checks"
fi
if run_isolated_test "$CURATOR_SCRIPT_DIR/test-scheduled-skill-deps.sh" >>"$RESULT" 2>&1; then
  ok "deterministic scheduled dependency checks"
else
  bad "deterministic scheduled dependency checks"
fi
if run_isolated_test "$CURATOR_SCRIPT_DIR/test-curator-run.sh" >>"$RESULT" 2>&1; then
  ok "deterministic curator transaction checks"
else
  bad "deterministic curator transaction checks"
fi
if run_isolated_test "$ROOT_SCRIPT_DIR/test-shared-deps.sh" >>"$RESULT" 2>&1; then
  ok "deterministic shared dependency checks"
else
  bad "deterministic shared dependency checks"
fi
if run_isolated_test "$ROOT_SCRIPT_DIR/test-headless-roots.sh" >>"$RESULT" 2>&1; then
  ok "deterministic headless root checks"
else
  bad "deterministic headless root checks"
fi
if run_isolated_test "$ROOT_SCRIPT_DIR/test-installer.sh" >>"$RESULT" 2>&1; then
  ok "deterministic installer checks"
else
  bad "deterministic installer checks"
fi
if run_isolated_test "$ROOT_SCRIPT_DIR/test-ssh-skill-publisher.py" >>"$RESULT" 2>&1; then
  ok "deterministic SSH skill publisher checks"
else
  bad "deterministic SSH skill publisher checks"
fi
if run_isolated_test "$ROOT_SCRIPT_DIR/test-ssh-estate-census.py" >>"$RESULT" 2>&1; then
  ok "deterministic SSH estate census checks"
else
  bad "deterministic SSH estate census checks"
fi
if run_isolated_test "$ROOT_SCRIPT_DIR/test-estate-action.py" >>"$RESULT" 2>&1; then
  ok "deterministic estate action authority checks"
else
  bad "deterministic estate action authority checks"
fi
if run_isolated_test "$ROOT_SCRIPT_DIR/test-estate-action-adapter.py" >>"$RESULT" 2>&1; then
  ok "deterministic estate action adapter checks"
else
  bad "deterministic estate action adapter checks"
fi
if run_isolated_test "$ROOT_SCRIPT_DIR/test-estate-recommendation.py" >>"$RESULT" 2>&1; then
  ok "deterministic estate recommendation checks"
else
  bad "deterministic estate recommendation checks"
fi
if run_isolated_test "$ROOT_SCRIPT_DIR/test-plugin-settings-transaction.py" >>"$RESULT" 2>&1; then
  ok "deterministic plugin settings transaction checks"
else
  bad "deterministic plugin settings transaction checks"
fi
if run_isolated_test "$ROOT_SCRIPT_DIR/test-plugin-remote-executor.py" >>"$RESULT" 2>&1; then
  ok "deterministic plugin remote executor checks"
else
  bad "deterministic plugin remote executor checks"
fi
if run_isolated_test "$ROOT_SCRIPT_DIR/test-copilot-migration.sh" >>"$RESULT" 2>&1; then
  ok "deterministic Copilot migration checks"
else
  bad "deterministic Copilot migration checks"
fi
if run_isolated_test "$ROOT_SCRIPT_DIR/test-transfer-dreaming-host.sh" >>"$RESULT" 2>&1; then
  ok "deterministic host transfer checks"
else
  bad "deterministic host transfer checks"
fi
if run_isolated_test "$ROOT_SCRIPT_DIR/test-cutover-dreaming-host.sh" >>"$RESULT" 2>&1; then
  ok "deterministic two-host cutover checks"
else
  bad "deterministic two-host cutover checks"
fi
if run_isolated_test "$ROOT_SCRIPT_DIR/test-repository-boundary.sh" >>"$RESULT" 2>&1; then
  ok "repository boundary checks"
else
  bad "repository boundary checks"
fi
if run_isolated_test "$ROOT_SCRIPT_DIR/test-private-boundary.py" >>"$RESULT" 2>&1; then
  ok "private boundary checks"
else
  bad "private boundary checks"
fi
if run_isolated_test node "$ROOT_SCRIPT_DIR/validate-plugin-manifests.mjs" >>"$RESULT" 2>&1; then
  ok "plugin manifest consistency"
else
  bad "plugin manifest consistency"
fi

if [[ -d "$LOCAL_ROOT/.git" && -z "$(git -C "$LOCAL_ROOT" remote 2>/dev/null)" ]]; then
  ok "local skills root is a git repo with no remote"
else
  bad "local skills root must be a git repo with no remote"
fi

if [[ "$COPILOT_COMPAT" == "1" ]]; then
  AUTHLOG="$STATE_DIR/.selftest-copilot.$$"
  : > "$AUTHLOG"
  if run_signal_responsive skills_run_copilot_bounded "$AUTHLOG" "SELFTEST_OK" 150 5 -- \
    "$COPILOT" -p "Reply with exactly: SELFTEST_OK" \
    --allow-all-tools --no-custom-instructions --no-color --no-remote \
    "${DREAMING_PLUGIN_ARGS[@]}" \
    --disable-builtin-mcps --log-level error; then
    ok "copilot headless auth"
  else
    bad "copilot headless auth"
  fi
  rm -f "$AUTHLOG"
elif run_signal_responsive env -u DREAMING_EXECUTOR_TEST_ALLOW_ROOT \
    -u DREAMING_EXECUTOR_TEST_ALLOW_ROOTS \
    "$SCRIPT_DIR/dreaming-core.py" doctor >/dev/null 2>&1; then
  ok "standalone adapter health"
else
  bad "standalone adapter health"
fi

[[ -e "$HALT_SWITCH" ]] && warn "halt switch is present" || ok "halt switch absent"
trap '' INT TERM
printf '== result: %s failure(s) ==\n' "$fails" | tee -a "$RESULT"
exit "$([[ $fails -eq 0 ]] && echo 0 || echo 1)"
