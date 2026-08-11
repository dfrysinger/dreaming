#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/shared-deps.XXXXXX")"
trap 'chmod -R u+w "$TMP" 2>/dev/null || true; rm -rf "$TMP"' EXIT
export TMPDIR="$TMP"
TOOL="$REPO_ROOT/scripts/dreaming-deps.py"
SKILLS=(writing-great-skills dual-review authenticated-browse)
passes=0

pass() { echo "PASS  $*"; passes=$((passes + 1)); }
fail() { echo "FAIL  $*" >&2; exit 1; }

make_source() {
  local root="$1" marker="$2" skill
  for skill in "${SKILLS[@]}"; do
    mkdir -p "$root/skills/$skill/references"
    printf -- '---\nname: %s\ndescription: Fixture shared skill for deterministic tests.\n---\n\n%s\n' \
      "$skill" "$marker" > "$root/skills/$skill/SKILL.md"
    printf '%s:%s\n' "$skill" "$marker" > "$root/skills/$skill/references/detail.txt"
  done
}

make_installed_plugin() {
  local root="$1" source="$2"
  mkdir -p "$root/.claude-plugin"
  cp -R "$source/skills" "$root/skills"
  printf '{"name":"dfrysinger-skills","skills":"./skills/"}\n' \
    > "$root/.claude-plugin/plugin.json"
}

SOURCE="$TMP/source"
make_source "$SOURCE" common
mkdir -p "$SOURCE/skills/unrelated-skill"
printf -- '---\nname: unrelated-skill\ndescription: Must never enter the shared bundle.\n---\n' \
  > "$SOURCE/skills/unrelated-skill/SKILL.md"
git -C "$SOURCE" init -q
git -C "$SOURCE" config user.email test@example.com
git -C "$SOURCE" config user.name Test
git -C "$SOURCE" config core.hooksPath /dev/null
git -C "$SOURCE" add .
git -C "$SOURCE" commit -qm fixture
REVISION="$(git -C "$SOURCE" rev-parse HEAD)"
RECEIPT="$TMP/receipt.json"
"$TOOL" generate-receipt "$SOURCE" --revision "$REVISION" --output "$RECEIPT"

new_case() {
  CASE="$TMP/$1"
  mkdir -p "$CASE/dreaming" "$CASE/deps" "$CASE/installed" "$CASE/public/skills"
  export DREAMING_REPO_ROOT="$CASE/dreaming"
  export DREAMING_DEPS_DIR="$CASE/deps"
  export DREAMING_CONFIG_FILE="$CASE/config.env"
  export DREAMING_RECEIPT_FILE="$RECEIPT"
  export DREAMING_INSTALLED_PLUGINS_ROOT="$CASE/installed"
  export DREAMING_CANONICAL_SKILLS_ROOT=""
  export SKILLS_REPO_ROOT="$CASE/public"
  unset DREAMING_DEPS_SOURCE DREAMING_SPARSE_REPO_URL
}

new_case explicit
export DREAMING_DEPS_SOURCE="$SOURCE"
OUT="$CASE/out.json"
"$TOOL" materialize > "$OUT"
grep -q '"source_kind": "explicit"' "$OUT" || fail "explicit source was not selected"
BUNDLE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundle"])' "$OUT")"
[[ "$(find "$BUNDLE/skills" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')" == "3" ]] ||
  fail "bundle copied an unexpected skill directory"
"$TOOL" verify "$BUNDLE" >/dev/null
pass "explicit source materializes exactly three verified skills"

new_case canonical
export DREAMING_CANONICAL_SKILLS_ROOT="$SOURCE"
"$TOOL" materialize > "$CASE/out.json"
grep -q '"source_kind": "canonical"' "$CASE/out.json" ||
  fail "canonical source was not selected"
pass "canonical checkout fallback"

new_case installed
PLUGIN="$CASE/installed/cache/dfrysinger-skills"
make_installed_plugin "$PLUGIN" "$SOURCE"
"$TOOL" materialize > "$CASE/out.json"
grep -q '"source_kind": "installed"' "$CASE/out.json" ||
  fail "installed plugin source was not selected"
pass "installed manifest discovery"

new_case canonical_skew
BAD_CANONICAL="$CASE/canonical"
mkdir -p "$BAD_CANONICAL"
cp -R "$SOURCE/skills" "$BAD_CANONICAL/skills"
echo "newer local content" >> \
  "$BAD_CANONICAL/skills/writing-great-skills/SKILL.md"
export DREAMING_CANONICAL_SKILLS_ROOT="$BAD_CANONICAL"
PLUGIN="$CASE/installed/cache/dfrysinger-skills"
make_installed_plugin "$PLUGIN" "$SOURCE"
"$TOOL" materialize > "$CASE/out.json"
python3 - "$CASE/out.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
assert d["source_kind"] == "installed", d
assert any("canonical source does not match" in item for item in d["rejected_sources"])
PY
pass "incompatible canonical checkout falls through to compatible installed plugin"

new_case installed_skew
BAD_PLUGIN_SOURCE="$CASE/bad-plugin-source"
mkdir -p "$BAD_PLUGIN_SOURCE"
cp -R "$SOURCE/skills" "$BAD_PLUGIN_SOURCE/skills"
echo "newer installed content" >> \
  "$BAD_PLUGIN_SOURCE/skills/dual-review/SKILL.md"
PLUGIN="$CASE/installed/cache/dfrysinger-skills"
make_installed_plugin "$PLUGIN" "$BAD_PLUGIN_SOURCE"
export DREAMING_SPARSE_REPO_URL="$SOURCE"
"$TOOL" materialize > "$CASE/out.json"
python3 - "$CASE/out.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
assert d["source_kind"] == "sparse", d
assert any("installed source does not match" in item for item in d["rejected_sources"])
PY
pass "incompatible installed plugin falls through to pinned sparse source"

new_case sparse
export DREAMING_SPARSE_REPO_URL="$SOURCE"
"$TOOL" materialize > "$CASE/out.json"
grep -q '"source_kind": "sparse"' "$CASE/out.json" ||
  fail "sparse source was not selected"
BUNDLE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundle"])' "$CASE/out.json")"
[[ "$(find "$BUNDLE/skills" -mindepth 1 -maxdepth 1 -type d -print | wc -l | tr -d ' ')" == "3" ]] ||
  fail "sparse bundle contains unrelated skills"
pass "pinned sparse fallback checks out exact dependency paths"

new_case incomplete
mkdir -p "$CASE/incomplete/skills/writing-great-skills"
touch "$CASE/incomplete/skills/writing-great-skills/SKILL.md"
export DREAMING_DEPS_SOURCE="$CASE/incomplete"
if "$TOOL" materialize >"$CASE/out" 2>"$CASE/err"; then
  fail "incomplete explicit source was accepted"
fi
grep -q "explicit shared dependency source is incomplete" "$CASE/err"
pass "incomplete explicit source fails closed"

new_case unavailable
export DREAMING_SPARSE_REPO_URL="$CASE/missing-repository"
if "$TOOL" materialize >"$CASE/out" 2>"$CASE/err"; then
  fail "unavailable sparse source was accepted"
fi
grep -q "no compatible shared dependency source" "$CASE/err"
grep -q "sparse dependency checkout failed" "$CASE/err"
pass "unavailable source fails closed"

new_case sparse_skew
export DREAMING_CANONICAL_SKILLS_ROOT="$SOURCE"
export DREAMING_SPARSE_REPO_URL="$SOURCE"
cp "$RECEIPT" "$CASE/skew.json"
python3 - "$CASE/skew.json" <<'PY'
import json,sys
p=sys.argv[1]
d=json.load(open(p))
d["files"][sorted(d["files"])[0]]="0"*64
json.dump(d,open(p,"w"))
PY
export DREAMING_RECEIPT_FILE="$CASE/skew.json"
if "$TOOL" materialize >"$CASE/out" 2>"$CASE/err"; then
  fail "receipt-skewed sparse source was accepted"
fi
grep -q "no compatible shared dependency source" "$CASE/err"
grep -q "canonical source does not match" "$CASE/err"
grep -q "sparse source does not match" "$CASE/err"
if find "$CASE/deps" -maxdepth 1 -type d -name '.sparse-source-*' | grep -q .; then
  fail "rejected sparse source was not cleaned up"
fi
pass "sparse verification failure preserves prior rejection context"

new_case protocol
python3 - "$RECEIPT" "$CASE/skew.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
d["protocol_version"]=99
json.dump(d,open(sys.argv[2],"w"))
PY
export DREAMING_RECEIPT_FILE="$CASE/skew.json"
export DREAMING_DEPS_SOURCE="$SOURCE"
if "$TOOL" materialize >"$CASE/out" 2>"$CASE/err"; then
  fail "protocol skew was accepted"
fi
grep -q "protocol mismatch" "$CASE/err"
pass "protocol skew fails closed"

new_case hash
cp "$RECEIPT" "$CASE/skew.json"
python3 - "$CASE/skew.json" <<'PY'
import json,sys
p=sys.argv[1]
d=json.load(open(p))
key=sorted(d["files"])[0]
d["files"][key]="0"*64
json.dump(d,open(p,"w"))
PY
export DREAMING_RECEIPT_FILE="$CASE/skew.json"
export DREAMING_DEPS_SOURCE="$SOURCE"
if "$TOOL" materialize >"$CASE/out" 2>"$CASE/err"; then
  fail "hash skew was accepted"
fi
grep -q "hash_skew" "$CASE/err"
pass "file hash skew fails closed"

new_case atomic
export DREAMING_DEPS_SOURCE="$SOURCE"
"$TOOL" materialize > "$CASE/first.json"
FIRST="$(readlink "$CASE/deps/current")"
CONFIG_HASH="$(shasum -a 256 "$CASE/config.env" | awk '{print $1}')"
cp "$RECEIPT" "$CASE/skew.json"
python3 - "$CASE/skew.json" <<'PY'
import json,sys
p=sys.argv[1]
d=json.load(open(p))
d["files"][sorted(d["files"])[0]]="f"*64
json.dump(d,open(p,"w"))
PY
export DREAMING_RECEIPT_FILE="$CASE/skew.json"
if "$TOOL" materialize >"$CASE/out" 2>"$CASE/err"; then
  fail "failing rematerialization returned success"
fi
[[ "$(readlink "$CASE/deps/current")" == "$FIRST" ]] ||
  fail "failed materialization changed atomic selection"
[[ "$(shasum -a 256 "$CASE/config.env" | awk '{print $1}')" == "$CONFIG_HASH" ]] ||
  fail "failed materialization changed selected config"
pass "failed materialization preserves atomic selection"

new_case alias
export DREAMING_DEPS_SOURCE="$SOURCE"
"$TOOL" materialize > "$CASE/first.json"
BUNDLE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundle"])' "$CASE/first.json")"
export DREAMING_DEPS_SOURCE="$BUNDLE"
if "$TOOL" materialize >"$CASE/out" 2>"$CASE/err"; then
  fail "source/bundle alias was accepted"
fi
grep -q "source and immutable bundle" "$CASE/err"
export DREAMING_DEPS_SOURCE="$SOURCE"
export SKILLS_REPO_ROOT="$BUNDLE"
if "$TOOL" materialize >"$CASE/out" 2>"$CASE/err"; then
  fail "shared/public alias was accepted"
fi
grep -q "shared bundle and public catalog" "$CASE/err"
pass "canonical source/bundle and shared/public aliases are rejected"

PLACEHOLDER="$TMP/placeholder-receipt.json"
cat > "$PLACEHOLDER" <<'JSON'
{
  "protocol_version": 1,
  "pinned_revision": "__DREAMING_PINNED_SKILLS_REVISION__",
  "files": "__DREAMING_SHARED_SKILL_FILE_HASHES__"
}
JSON
if DREAMING_RECEIPT_FILE="$PLACEHOLDER" DREAMING_DEPS_SOURCE="$SOURCE" \
  DREAMING_REPO_ROOT="$TMP/placeholder-dreaming" \
  DREAMING_DEPS_DIR="$TMP/placeholder-deps" \
  DREAMING_CONFIG_FILE="$TMP/placeholder-config" \
  "$TOOL" materialize >"$TMP/placeholder.out" 2>"$TMP/placeholder.err"; then
  fail "release placeholder weakened production validation"
fi
grep -q "release placeholder" "$TMP/placeholder.err"
pass "release placeholder is test-overridable but invalid in production"

echo "shared dependency tests: PASS ($passes)"
