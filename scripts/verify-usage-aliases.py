#!/usr/bin/env python3
"""Verify the collector's static usage aliases against immutable Git renames."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any


class VerificationError(RuntimeError):
    pass


def load_collector(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("dreaming_estate_aliases", path)
    if spec is None or spec.loader is None:
        raise VerificationError(f"cannot load collector: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def repositories(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        path = Path(raw_path).expanduser().resolve()
        if (
            not separator
            or not name
            or name in result
            or not (path / ".git").exists()
        ):
            raise VerificationError(f"invalid repository mapping: {value}")
        result[name] = path
    return result


def rename_rows(repository: Path, commit: str) -> list[tuple[str, str]]:
    process = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "-r",
            "-M",
            f"{commit}^",
            commit,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise VerificationError(f"Git evidence is unavailable: {commit}")
    rows = []
    for line in process.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) == 3 and fields[0].startswith("R"):
            rows.append((fields[1], fields[2]))
    return rows


def skill_path_matches(path: str, name: str) -> bool:
    return path == f"{name}/SKILL.md" or path.endswith(f"/{name}/SKILL.md")


def verify(collector: Any, roots: dict[str, Path]) -> int:
    collector.validate_usage_aliases(collector.USAGE_ALIASES)
    checked = 0
    for historical, entry in sorted(collector.USAGE_ALIASES.items()):
        current = historical
        for evidence in entry["evidence"]:
            repository = roots.get(evidence["repository"])
            if repository is None:
                raise VerificationError(
                    f"missing repository mapping: {evidence['repository']}"
                )
            if evidence["from"] != current:
                raise VerificationError(f"non-contiguous alias chain: {historical}")
            if not any(
                skill_path_matches(before, evidence["from"])
                and skill_path_matches(after, evidence["to"])
                for before, after in rename_rows(repository, evidence["commit"])
            ):
                raise VerificationError(
                    f"rename evidence mismatch: {historical} {evidence['commit']}"
                )
            current = evidence["to"]
            checked += 1
        if current != entry["target"]:
            raise VerificationError(f"alias target mismatch: {historical}")
    return checked


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--collector",
        type=Path,
        default=Path(__file__).parents[1]
        / "skills/skill-review/scripts/dreaming-estate.py",
    )
    parser.add_argument("--repository", action="append", default=[])
    args = parser.parse_args()
    try:
        checked = verify(
            load_collector(args.collector.expanduser().resolve()),
            repositories(args.repository),
        )
    except (OSError, VerificationError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(f"verified {checked} immutable Git rename records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
