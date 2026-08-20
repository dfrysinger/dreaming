#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
# shellcheck source=lib-test-work.sh
source "$SCRIPT_DIR/lib-test-work.sh"
prune_test_work "$TEST_ROOT" "evaluation-input-source-builder" 2
WORK="$(mktemp -d "$TEST_ROOT/evaluation-input-source-builder.XXXXXX")"
cleanup() {
  local rc=$?
  trap - EXIT
  finish_test_work "$rc" "$WORK" "evaluation-input source builder"
  exit "$rc"
}
trap cleanup EXIT

mkdir -p "$WORK/skill" "$WORK/bin"
cat >"$WORK/skill/SKILL.md" <<'EOF'
---
name: source-builder-fixture
description: Complete a synthetic formatting task
---

# Source builder fixture

Return a concise structured answer.
EOF

cat >"$WORK/cli.c" <<'EOF'
#include <stdio.h>
#include <string.h>
int main(int argc, char **argv) {
  if (argc == 2 && strcmp(argv[1], "--version") == 0) {
    puts("copilot fixture 1.0");
    return 0;
  }
  if (argc == 2 && strcmp(argv[1], "--help") == 0) {
    puts("--plugin-dir --output-format --model --available-tools "
         "--disable-builtin-mcps --no-custom-instructions --no-ask-user "
         "--no-remote");
    return 0;
  }
  return 64;
}
EOF
/usr/bin/clang -o "$WORK/bin/copilot" "$WORK/cli.c"

output="$WORK/source-pack"
"$SCRIPT_DIR/build-evaluation-input-source.py" \
  --skill "$WORK/skill" \
  --output "$output" \
  --executor copilot=fixture-executor-model \
  --binary "copilot=$WORK/bin/copilot" \
  --comparator-model fixture-comparator-model >"$WORK/result.json"

python3 - "$output" "$WORK/result.json" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
result = json.loads(Path(sys.argv[2]).read_text())
assert result["status"] == "built", result
expected = {
    "suite.json",
    "policy.json",
    "compilation.json",
    "routing.json",
    "authoring-catalog.json",
    "fixtures/synthetic-empty.json",
    "graders/contracts.json",
}
observed = {
    path.relative_to(root).as_posix()
    for path in root.rglob("*")
    if path.is_file()
}
assert observed == expected, (observed, expected)
compilation = json.loads((root / "compilation.json").read_text())
routing = json.loads((root / "routing.json").read_text())
policy = json.loads((root / "policy.json").read_text())
assert compilation["executors"][0]["cli_version"] == "copilot fixture 1.0"
assert policy["required_executors"][0]["name"] == "copilot"
assert policy["advisory_executors"] == []
assert routing["comparator"]["argv"][0].endswith(
    "dreaming-vendor-adapter.py"
)
PY

if "$SCRIPT_DIR/build-evaluation-input-source.py" \
  --skill "$WORK/skill" \
  --output "$output" \
  --executor copilot=fixture-executor-model \
  --binary "copilot=$WORK/bin/copilot" \
  --comparator-model fixture-comparator-model >"$WORK/collision.json"; then
  echo "builder replaced an existing source pack" >&2
  exit 1
fi
grep -q 'evaluation-input-source-build-refused' "$WORK/collision.json"

blocked="$WORK/blocked-source-pack"
mkdir "$blocked"
printf 'unrelated\n' >"$blocked/keep.txt"
if "$SCRIPT_DIR/build-evaluation-input-source.py" \
  --skill "$WORK/skill" \
  --output "$blocked" \
  --executor copilot=fixture-executor-model \
  --binary "copilot=$WORK/bin/copilot" \
  --comparator-model fixture-comparator-model >"$WORK/blocked.json"; then
  echo "builder replaced an unrelated output directory" >&2
  exit 1
fi
test "$(cat "$blocked/keep.txt")" = "unrelated"
test -z "$(find "$WORK" -maxdepth 1 -name '.evaluation-input-source.*' -print -quit)"
rm -rf "$blocked"
"$SCRIPT_DIR/build-evaluation-input-source.py" \
  --skill "$WORK/skill" \
  --output "$blocked" \
  --executor copilot=fixture-executor-model \
  --binary "copilot=$WORK/bin/copilot" \
  --comparator-model fixture-comparator-model >"$WORK/retry.json"
test -f "$blocked/suite.json"

python3 - "$SCRIPT_DIR/build-evaluation-input-source.py" "$WORK" <<'PY'
import importlib.util
import sys
from pathlib import Path

module_path = Path(sys.argv[1])
root = Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("source_builder", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
source = root / "atomic-source"
destination = root / "atomic-destination"
source.mkdir()
source.joinpath("complete.txt").write_text("complete\n")
destination.mkdir()
destination.joinpath("keep.txt").write_text("unrelated\n")
try:
    module.publish_directory_create_only(source, destination)
except module.BuildError as error:
    assert str(error) == "output was created before publication"
else:
    raise AssertionError("exclusive rename replaced an existing destination")
assert source.joinpath("complete.txt").read_text() == "complete\n"
assert destination.joinpath("keep.txt").read_text() == "unrelated\n"
PY

python3 - "$SCRIPT_DIR/skill-evaluation.py" \
  "$SCRIPT_DIR/dreaming-vendor-adapter.py" <<'PY'
import hashlib
import importlib.util
import re
import sys
from pathlib import Path

evaluator_path = Path(sys.argv[1])
evaluator = evaluator_path.read_text()
adapter = Path(sys.argv[2]).read_bytes()
match = re.search(
    r'TRUSTED_AUTHORING_ADAPTER_SHA256\s*=\s*\(\s*"([^"]+)"',
    evaluator,
)
assert match
assert match.group(1) == "sha256:" + hashlib.sha256(adapter).hexdigest()
spec = importlib.util.spec_from_file_location("skill_evaluation", evaluator_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
assert module.leaks_identity_marker(["fixture-skill"], "Use FixtureSkill")
assert module.leaks_identity_marker(["fixture-skill"], "Use fixtureskill")
assert not module.leaks_identity_marker(
    ["gaw"], "Refactor the parser so that debugging a workflow is easier."
)
assert module.leaks_identity_marker(["scout"], "Start scouting the repository.")
assert module.leaks_identity_marker(["gaw"], "Use a gawbased pipeline.")
assert module.leaks_identity_marker(["--"], "Keep -- literal")
PY

multi="$WORK/multi-source-pack"
DREAMING_EVALUATION_EXECUTORS=copilot \
DREAMING_ADVISORY_EVALUATION_EXECUTORS=claude \
  "$SCRIPT_DIR/build-evaluation-input-source.py" \
    --skill "$WORK/skill" \
    --output "$multi" \
    --executor copilot=fixture-executor-model \
    --executor claude=fixture-advisory-model \
    --binary "copilot=$WORK/bin/copilot" \
    --binary "claude=$WORK/bin/copilot" \
    --comparator-model fixture-comparator-model >"$WORK/multi.json"
python3 - "$multi" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
compilation = json.loads((root / "compilation.json").read_text())
policy = json.loads((root / "policy.json").read_text())
assert [item["name"] for item in compilation["executors"]] == [
    "copilot",
    "claude",
]
assert [item["requirement"] for item in compilation["executors"]] == [
    "required",
    "advisory",
]
assert [item["name"] for item in policy["advisory_executors"]] == ["claude"]
assert policy["policy_kind"] == "encoded_preference"
PY

echo "PASS  environment-bound evaluation-input source pack is semantically valid"
