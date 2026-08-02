#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
owned=(skill-review skill-curator memory-curator skill-create skill-manage)
shared=(writing-great-skills dual-review authenticated-browse)

for name in "${owned[@]}"; do
  [[ -f "$ROOT/skills/$name/SKILL.md" ]] || {
    echo "missing owned skill: $name" >&2
    exit 1
  }
done
for name in "${shared[@]}"; do
  [[ ! -e "$ROOT/skills/$name" ]] || {
    echo "shared skill must not be duplicated: $name" >&2
    exit 1
  }
done
if grep -R -n -E \
  '~/code/skills/skills/(skill-review|skill-curator|memory-curator|skill-create|skill-manage)(/|`|$)' \
  "$ROOT/skills" "$ROOT/docs" "$ROOT/scripts" --exclude='test-repository-boundary.sh'; then
  echo "own-path hardcode crosses the repository boundary" >&2
  exit 1
fi
echo "repository boundary tests: PASS"
