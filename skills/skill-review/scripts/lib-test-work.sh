#!/usr/bin/env bash

prune_test_work() {
  local root="$1" prefix="$2" keep="${3:-2}"
  python3 - "$root" "$prefix" "$keep" <<'PY'
import shutil
import sys
import os
from pathlib import Path

root = Path(sys.argv[1]).resolve()
prefix = sys.argv[2]
keep = int(sys.argv[3])
paths = sorted(
    (
        path
        for path in root.glob(f"{prefix}.*")
        if path.is_dir() and path.parent.resolve() == root
    ),
    key=lambda path: path.stat().st_mtime_ns,
    reverse=True,
)
for path in paths[keep:]:
    for current, directories, files in os.walk(path):
        retained = []
        for name in directories:
            child = Path(current) / name
            if child.is_symlink():
                continue
            os.chmod(child, 0o755)
            retained.append(name)
        directories[:] = retained
        for name in files:
            child = Path(current) / name
            if not child.is_symlink():
                os.chmod(child, 0o644)
    shutil.rmtree(path)
PY
}

finish_test_work() {
  local status="$1" path="$2" label="$3" writable="${4:-0}"
  if [[ "$status" -eq 0 ]]; then
    if [[ "$writable" -eq 1 ]]; then
      chmod -R u+w "$path" 2>/dev/null || true
    fi
    rm -rf "$path"
  else
    echo "DIAGNOSTIC retained failed $label evidence: $path" >&2
  fi
}
