#!/usr/bin/env bash
# Deterministic checks for skill frontmatter validation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/validate-skill.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

passes=0

pass() {
  echo "PASS  $*"
  passes=$((passes + 1))
}

fail() {
  echo "FAIL  $*" >&2
  exit 1
}

make_skill() {
  local name="$1"
  local description_line="$2"
  local dir="$TMP/$name"
  mkdir -p "$dir"
  cat >"$dir/SKILL.md" <<EOF
---
name: $name
$description_line
---

# $name

Test instructions.
EOF
}

make_skill valid-colon \
  'description: "Validate packages: require compile checks. Use when testing YAML parsing."'
"$SCRIPT_DIR/validate-skill.sh" "$TMP/valid-colon/SKILL.md" >/dev/null ||
  fail "quoted colon description was rejected"
pass "quoted colon description"

make_skill invalid-colon \
  'description: Validate packages: require compile checks. Use when testing YAML parsing.'
if output="$("$SCRIPT_DIR/validate-skill.sh" "$TMP/invalid-colon/SKILL.md" 2>&1)"; then
  fail "unquoted colon description was accepted"
fi
grep -Fq "failed to parse YAML frontmatter" <<<"$output" ||
  fail "invalid YAML did not report a parse failure"
pass "unquoted colon rejection"

make_skill multiline-description 'description: >-
  Validate multiline descriptions with YAML parsing.
  Use when testing folded frontmatter scalars.'
"$SCRIPT_DIR/validate-skill.sh" "$TMP/multiline-description/SKILL.md" >/dev/null ||
  fail "valid multiline description was rejected"
pass "multiline description"

echo
echo "$passes validation checks passed"
