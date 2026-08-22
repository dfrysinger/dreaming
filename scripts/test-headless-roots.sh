#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$ROOT/.test-work"
mkdir -p "$TEST_ROOT"
TMP="$(mktemp -d "$TEST_ROOT/headless-roots.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
export TMPDIR="$TMP"
SHARED="$TMP/shared"
for name in skill-create writing-great-skills dual-review authenticated-browse; do
  mkdir -p "$SHARED/skills/$name"
  printf -- '---\nname: %s\ndescription: Headless fixture shared dependency.\n---\n' "$name" \
    > "$SHARED/skills/$name/SKILL.md"
done
mkdir -p "$SHARED/skills/authenticated-browse/scripts"
PW="$SHARED/skills/authenticated-browse/scripts/pw-session.sh"
printf '#!/usr/bin/env bash\n:\n' > "$PW"
chmod +x "$PW"
RECEIPT="$TMP/receipt.json"
"$ROOT/scripts/dreaming-deps.py" generate-receipt "$SHARED" \
  --revision 0123456789abcdef0123456789abcdef01234567 --output "$RECEIPT"

CONFIG="$TMP/config.env"
cat > "$CONFIG" <<EOF
DREAMING_REPO_ROOT='$ROOT'
DREAMING_SHARED_SKILLS_ROOT='$SHARED'
DREAMING_RECEIPT_FILE='$RECEIPT'
EOF
PROMPT="$TMP/prompt.txt"
echo "fixture prompt" > "$PROMPT"
FAKE="$TMP/copilot"
cat > "$FAKE" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$FAKE_ARGS"
echo "DREAM_PASS_RESULT: ok fixture"
echo "AI Credits 0"
SH
chmod +x "$FAKE"
export FAKE_ARGS="$TMP/args"

DREAMING_REPO_ROOT= DREAMING_SHARED_SKILLS_ROOT= SKILLS_REPO_ROOT= \
  DREAMING_CONFIG_FILE="$CONFIG" COPILOT_BIN="$FAKE" \
  "$ROOT/skills/skill-review/scripts/daemon-pass.sh" \
  --prompt "$PROMPT" --name fixture --log "$TMP/pass.log" >/dev/null
python3 - "$FAKE_ARGS" "$ROOT" "$SHARED" <<'PY'
import sys
args=open(sys.argv[1]).read().splitlines()
pairs=[args[i+1] for i,value in enumerate(args[:-1]) if value=="--plugin-dir"]
assert pairs == [sys.argv[2], sys.argv[3]], pairs
PY
echo "PASS  headless Copilot receives dreaming and shared plugin roots"

PATH_RESULT="$(
  DREAMING_REPO_ROOT= DREAMING_SHARED_SKILLS_ROOT= SKILLS_REPO_ROOT= \
    DREAMING_CONFIG_FILE="$CONFIG" bash -c \
    'source "$1/skills/memory-curator/scripts/mem-lib.sh"; mem_locate_pw' _ "$ROOT"
)"
[[ "$PATH_RESULT" == "$PW" ]] || {
  echo "authenticated-browse helper resolved outside verified shared root" >&2
  exit 1
}
echo "PASS  authenticated-browse helper resolves directly from shared root"

BAD="$TMP/bad-config.env"
cat > "$BAD" <<EOF
DREAMING_REPO_ROOT='$ROOT'
DREAMING_SHARED_SKILLS_ROOT='$TMP/missing'
EOF
if DREAMING_REPO_ROOT= DREAMING_SHARED_SKILLS_ROOT= SKILLS_REPO_ROOT= \
  DREAMING_CONFIG_FILE="$BAD" COPILOT_BIN="$FAKE" \
  "$ROOT/skills/skill-review/scripts/daemon-pass.sh" \
  --prompt "$PROMPT" --name fixture --log "$TMP/bad.log" >"$TMP/out" 2>"$TMP/err"; then
  echo "incomplete shared root was accepted" >&2
  exit 1
fi
grep -q "verified shared skills root is incomplete" "$TMP/err"
echo "PASS  incomplete headless shared root fails closed"

echo "headless root tests: PASS"
