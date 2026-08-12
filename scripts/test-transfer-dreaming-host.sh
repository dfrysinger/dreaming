#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNNER="$SCRIPT_DIR/transfer-dreaming-host.py"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/host-transfer.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/empty-git-template"
export GIT_TEMPLATE_DIR="$TMP/empty-git-template"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

expect_refusal() {
  local message="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    fail "$message"
  fi
}

make_repo() {
  local path="$1" name="$2"
  mkdir -p "$path"
  git -C "$path" init -q -b main
  git -C "$path" config user.email test@example.com
  git -C "$path" config user.name Test
  mkdir -p "$path/.git/hooks"
  printf '#!/bin/sh\nexit 0\n' > "$path/.git/hooks/pre-commit"
  chmod 755 "$path/.git/hooks/pre-commit"
  printf '%s\n' "$name" > "$path/README.md"
  git -C "$path" add README.md
  git -C "$path" commit -qm base
}

SOURCE="$TMP/source"
DEST="$TMP/destination"
BUNDLE="$TMP/bundle"
mkdir -p "$SOURCE" "$DEST"
for label in dreaming shared public local; do
  make_repo "$SOURCE/$label" "$label"
done
echo "untracked authority" > "$SOURCE/local/untracked.txt"
mkdir -p \
  "$SOURCE/skill-state/skill-review/retired" \
  "$SOURCE/skill-state/skill-review/tombstones" \
  "$SOURCE/skill-state/reports" \
  "$SOURCE/dreaming-state" \
  "$SOURCE/LaunchAgents"
echo '{"paused":false}' > "$SOURCE/skill-state/curator.json"
echo '{"skill":"old"}' > "$SOURCE/skill-state/skill-review/retired/old.json"
echo '{"skill":"old"}' > "$SOURCE/skill-state/skill-review/tombstones/old.json"
echo "report" > "$SOURCE/skill-state/reports/report.md"
echo '[]' > "$SOURCE/dreaming-state/review-ledger.json"
echo '{}' > "$SOURCE/dreaming-state/publisher-ownership.json"
mkdir -p "$SOURCE/skill-state/dreaming"
echo "excluded" > "$SOURCE/skill-state/dreaming/activation-generation"
echo '<plist/>' > "$SOURCE/LaunchAgents/com.test.dreaming.dreaming.plist"

common=(
  --bundle "$BUNDLE"
  --dreaming-root "$SOURCE/dreaming"
  --shared-root "$SOURCE/shared"
  --public-root "$SOURCE/public"
  --local-root "$SOURCE/local"
  --skill-state-root "$SOURCE/skill-state"
  --dreaming-state-root "$SOURCE/dreaming-state"
  --launch-agents-root "$SOURCE/LaunchAgents"
  --launch-agent-user test
)
"$RUNNER" "${common[@]}" capture >/dev/null
"$RUNNER" "${common[@]}" verify-bundle >/dev/null
[[ ! -e "$BUNDLE/state/skill_state/dreaming/activation-generation" ]] ||
  fail "machine-local activation state entered the bundle"

CONFIG="$BUNDLE/roots/dreaming/.git/config"
CONFIG_COPY="$TMP/git-config"
cp -p "$CONFIG" "$CONFIG_COPY"
FSMONITOR="$TMP/fsmonitor"
FSMONITOR_MARKER="$TMP/fsmonitor-ran"
printf '#!/bin/sh\ntouch %q\nexit 0\n' "$FSMONITOR_MARKER" > "$FSMONITOR"
chmod 755 "$FSMONITOR"
git config --file "$CONFIG" core.fsmonitor "$FSMONITOR"
expect_refusal "tampered Git config passed bundle verification" \
  "$RUNNER" "${common[@]}" verify-bundle
[[ ! -e "$FSMONITOR_MARKER" ]] ||
  fail "bundle verification executed tampered Git config"
cp -p "$CONFIG_COPY" "$CONFIG"

HOOK="$BUNDLE/roots/dreaming/.git/hooks/pre-commit"
HOOK_COPY="$TMP/pre-commit"
cp -p "$HOOK" "$HOOK_COPY"
echo '#!/bin/sh' > "$HOOK"
chmod 755 "$HOOK"
expect_refusal "injected Git hook passed bundle verification" \
  "$RUNNER" "${common[@]}" verify-bundle
cp -p "$HOOK_COPY" "$HOOK"

README="$BUNDLE/roots/dreaming/README.md"
README_MODE="$(stat -f '%Lp' "$README")"
chmod 755 "$README"
expect_refusal "permission-only bundle tampering passed verification" \
  "$RUNNER" "${common[@]}" verify-bundle
chmod "$README_MODE" "$README"

PLIST="$BUNDLE/launch-agents/com.test.dreaming.dreaming.plist"
PLIST_MODE="$(stat -f '%Lp' "$PLIST")"
chmod 600 "$PLIST"
expect_refusal "LaunchAgent permission tampering passed verification" \
  "$RUNNER" "${common[@]}" verify-bundle
chmod "$PLIST_MODE" "$PLIST"
"$RUNNER" "${common[@]}" verify-bundle >/dev/null

HOOKS_DIRECTORY="$BUNDLE/roots/dreaming/.git/hooks"
HOOKS_MODE="$(stat -f '%Lp' "$HOOKS_DIRECTORY")"
chmod 777 "$HOOKS_DIRECTORY"
expect_refusal "Git metadata directory permission tampering passed verification" \
  "$RUNNER" "${common[@]}" verify-bundle
chmod "$HOOKS_MODE" "$HOOKS_DIRECTORY"

mkdir -p "$BUNDLE/roots/dreaming/__pycache__"
echo "unverified" > "$BUNDLE/roots/dreaming/__pycache__/injected.pyc"
expect_refusal "ignored repository file passed bundle verification" \
  "$RUNNER" "${common[@]}" verify-bundle
rm -rf "$BUNDLE/roots/dreaming/__pycache__"

mkdir "$BUNDLE/roots/shared/injected-empty-directory"
expect_refusal "injected content directory passed bundle verification" \
  "$RUNNER" "${common[@]}" verify-bundle
rmdir "$BUNDLE/roots/shared/injected-empty-directory"

mkdir -p "$BUNDLE/state/skill_state/dreaming"
echo "unverified" \
  > "$BUNDLE/state/skill_state/dreaming/activation-generation"
expect_refusal "excluded machine-local state passed bundle verification" \
  "$RUNNER" "${common[@]}" verify-bundle
rm -rf "$BUNDLE/state/skill_state/dreaming"
"$RUNNER" "${common[@]}" verify-bundle >/dev/null

git -C "$SOURCE/dreaming" worktree add -q -b linked-fixture \
  "$TMP/linked-dreaming"
LINKED_BUNDLE="$TMP/linked-bundle"
linked_args=("${common[@]}")
linked_args[1]="$LINKED_BUNDLE"
linked_args[3]="$TMP/linked-dreaming"
expect_refusal "linked Git worktree was captured" \
  "$RUNNER" "${linked_args[@]}" capture

git clone -q --shared "$SOURCE/dreaming" "$TMP/alternates-dreaming"
ALTERNATES_BUNDLE="$TMP/alternates-bundle"
alternates_args=("${common[@]}")
alternates_args[1]="$ALTERNATES_BUNDLE"
alternates_args[3]="$TMP/alternates-dreaming"
expect_refusal "repository with object alternates was captured" \
  "$RUNNER" "${alternates_args[@]}" capture

ln -s "$SOURCE/skill-state/curator.json" \
  "$SOURCE/skill-state/reports/linked.json"
SYMLINK_BUNDLE="$TMP/symlink-bundle"
symlink_args=("${common[@]}")
symlink_args[1]="$SYMLINK_BUNDLE"
expect_refusal "nested authority-state symlink was captured" \
  "$RUNNER" "${symlink_args[@]}" capture
rm "$SOURCE/skill-state/reports/linked.json"

ln -s "$SOURCE/LaunchAgents" "$TMP/linked-LaunchAgents"
launch_symlink_args=("${common[@]}")
launch_symlink_args[1]="$TMP/launch-symlink-bundle"
launch_symlink_args[15]="$TMP/linked-LaunchAgents"
expect_refusal "symlinked LaunchAgents root was captured" \
  "$RUNNER" "${launch_symlink_args[@]}" capture

dest_args=(
  --bundle "$BUNDLE"
  --dreaming-root "$DEST/dreaming"
  --shared-root "$DEST/shared"
  --public-root "$DEST/public"
  --local-root "$DEST/local"
  --skill-state-root "$DEST/skill-state"
  --dreaming-state-root "$DEST/dreaming-state"
  --launch-agents-root "$DEST/LaunchAgents"
  --launch-agent-user test
)
mkdir -p "$DEST/LaunchAgents"

SYMLINK_DEST="$TMP/symlink-destination"
mkdir -p "$SYMLINK_DEST/skill-state/reports" \
  "$SYMLINK_DEST/LaunchAgents"
ln -s "$SOURCE/skill-state/curator.json" \
  "$SYMLINK_DEST/skill-state/reports/linked.json"
symlink_dest_args=(
  --bundle "$BUNDLE"
  --dreaming-root "$SYMLINK_DEST/dreaming"
  --shared-root "$SYMLINK_DEST/shared"
  --public-root "$SYMLINK_DEST/public"
  --local-root "$SYMLINK_DEST/local"
  --skill-state-root "$SYMLINK_DEST/skill-state"
  --dreaming-state-root "$SYMLINK_DEST/dreaming-state"
  --launch-agents-root "$SYMLINK_DEST/LaunchAgents"
  --launch-agent-user test
)
expect_refusal "install accepted a destination authority-state symlink" \
  "$RUNNER" "${symlink_dest_args[@]}" install
[[ ! -e "$SYMLINK_DEST/dreaming" ]] ||
  fail "symlink refusal occurred after installation began"

PARTIAL_DEST="$TMP/partial-destination"
mkdir -p "$PARTIAL_DEST/shared" "$PARTIAL_DEST/LaunchAgents"
partial_args=(
  --bundle "$BUNDLE"
  --dreaming-root "$PARTIAL_DEST/dreaming"
  --shared-root "$PARTIAL_DEST/shared"
  --public-root "$PARTIAL_DEST/public"
  --local-root "$PARTIAL_DEST/local"
  --skill-state-root "$PARTIAL_DEST/skill-state"
  --dreaming-state-root "$PARTIAL_DEST/dreaming-state"
  --launch-agents-root "$PARTIAL_DEST/LaunchAgents"
  --launch-agent-user test
)
expect_refusal "partial install unexpectedly succeeded" \
  env TRANSFER_DREAMING_HOST_TEST_FAIL=install-after-state:skill_state/curator.json \
  "$RUNNER" "${partial_args[@]}" install
[[ ! -e "$PARTIAL_DEST/dreaming" ]] ||
  fail "partial install left the dreaming repository"
[[ ! -e "$PARTIAL_DEST/public" && ! -e "$PARTIAL_DEST/local" ]] ||
  fail "partial install left repository roots"
[[ -d "$PARTIAL_DEST/shared" && -z "$(ls -A "$PARTIAL_DEST/shared")" ]] ||
  fail "partial install did not restore the pre-existing empty shared root"
[[ ! -e "$PARTIAL_DEST/skill-state/curator.json" ]] ||
  fail "partial install left authority state"

"$RUNNER" "${dest_args[@]}" install >/dev/null
if "$RUNNER" "${dest_args[@]}" compare >/dev/null 2>&1; then
  fail "destination without installer-owned LaunchAgents matched"
fi
cp "$BUNDLE/launch-agents/"*.plist "$DEST/LaunchAgents/"
"$RUNNER" "${dest_args[@]}" compare >/dev/null
chmod 600 "$DEST/LaunchAgents/com.test.dreaming.dreaming.plist"
expect_refusal "destination LaunchAgent permission tampering matched" \
  "$RUNNER" "${dest_args[@]}" compare
chmod "$PLIST_MODE" "$DEST/LaunchAgents/com.test.dreaming.dreaming.plist"
"$RUNNER" "${dest_args[@]}" compare >/dev/null
[[ -f "$DEST/local/untracked.txt" ]] ||
  fail "repository worktree state was not transferred"
[[ -f "$DEST/skill-state/skill-review/retired/old.json" ]] ||
  fail "retirement state was not transferred"

echo "destination-only history" > "$SOURCE/local/new-history.txt"
git -C "$SOURCE/local" add new-history.txt
git -C "$SOURCE/local" commit -qm "destination-only history"
mkdir -p "$SOURCE/skill-state/skill-review/retirement-history"
echo '{"skill":"old","restored_at":"fixture"}' \
  > "$SOURCE/skill-state/skill-review/retirement-history/old.json"
NEXT_BUNDLE="$TMP/next-bundle"
next_args=("${common[@]}")
next_args[1]="$NEXT_BUNDLE"
"$RUNNER" "${next_args[@]}" capture >/dev/null
sync_args=("${dest_args[@]}")
sync_args[1]="$NEXT_BUNDLE"

echo '{"paused":"changed-after-capture"}' > "$DEST/skill-state/curator.json"
TAMPERED_PRIOR="$TMP/tampered-prior"
cp -a "$BUNDLE" "$TAMPERED_PRIOR"
python3 - "$TAMPERED_PRIOR/manifest.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text())
payload["state"]["skill_state"]["curator.json"]["sha256"] = hashlib.sha256(
    b'{"paused":"changed-after-capture"}\n'
).hexdigest()
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
expect_refusal "tampered prior manifest authorized synchronization" \
  "$RUNNER" "${sync_args[@]}" --prior-bundle "$TAMPERED_PRIOR" synchronize
expect_refusal "synchronize overwrote authority changed after capture" \
  "$RUNNER" "${sync_args[@]}" --prior-bundle "$BUNDLE" synchronize
cp -p "$BUNDLE/state/skill_state/curator.json" \
  "$DEST/skill-state/curator.json"

"$RUNNER" "${sync_args[@]}" --prior-bundle "$BUNDLE" synchronize >/dev/null
"$RUNNER" "${sync_args[@]}" compare >/dev/null
[[ -f "$DEST/local/new-history.txt" ]] ||
  fail "new local Git history was not synchronized"
[[ -f "$DEST/skill-state/skill-review/retirement-history/old.json" ]] ||
  fail "new retirement authority was not synchronized"

echo "third history" > "$SOURCE/dreaming/third-history.txt"
git -C "$SOURCE/dreaming" add third-history.txt
git -C "$SOURCE/dreaming" commit -qm "third history"
echo '{"paused":"third"}' > "$SOURCE/skill-state/curator.json"
THIRD_BUNDLE="$TMP/third-bundle"
third_capture_args=("${common[@]}")
third_capture_args[1]="$THIRD_BUNDLE"
"$RUNNER" "${third_capture_args[@]}" capture >/dev/null
third_sync_args=("${dest_args[@]}")
third_sync_args[1]="$THIRD_BUNDLE"

expect_refusal "replacement rename failure unexpectedly succeeded" \
  env TRANSFER_DREAMING_HOST_TEST_FAIL=sync-replacement-rename:dreaming \
  "$RUNNER" "${third_sync_args[@]}" --prior-bundle "$NEXT_BUNDLE" synchronize
"$RUNNER" "${sync_args[@]}" compare >/dev/null
[[ ! -e "$DEST/dreaming/third-history.txt" ]] ||
  fail "replacement rename failure did not restore the prior repository"

expect_refusal "partial synchronize unexpectedly succeeded" \
  env TRANSFER_DREAMING_HOST_TEST_FAIL=sync-after-state:skill_state/curator.json \
  "$RUNNER" "${third_sync_args[@]}" --prior-bundle "$NEXT_BUNDLE" synchronize
"$RUNNER" "${sync_args[@]}" compare >/dev/null
[[ "$(cat "$DEST/skill-state/curator.json")" == '{"paused":false}' ]] ||
  fail "partial synchronize did not restore prior authority state"

CLEANUP_LOG="$TMP/cleanup.log"
TRANSFER_DREAMING_HOST_TEST_FAIL=sync-cleanup \
  "$RUNNER" "${third_sync_args[@]}" --prior-bundle "$NEXT_BUNDLE" \
  synchronize >/dev/null 2>"$CLEANUP_LOG"
grep -q 'committed but transaction cleanup failed' "$CLEANUP_LOG" ||
  fail "cleanup failure was not reported as post-commit"
"$RUNNER" "${third_sync_args[@]}" compare >/dev/null
[[ -f "$DEST/dreaming/third-history.txt" ]] ||
  fail "cleanup failure incorrectly rolled back committed synchronization"

echo "tampered" >> "$DEST/dreaming/README.md"
expect_refusal "destination tampering passed manifest comparison" \
  "$RUNNER" "${third_sync_args[@]}" compare

echo "host transfer tests: PASS"
