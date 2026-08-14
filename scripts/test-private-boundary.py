#!/usr/bin/env python3
"""Deterministic checks for public repository and dashboard sentinel rejection."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("validate-private-boundary.py")
SENTINELS = {
    "settings": "CHK10-SETTINGS-PRIVATE-7d125afd",
    "credential": "CHK10-CREDENTIAL-PRIVATE-9367f9cb",
    "transcript": "CHK10-TRANSCRIPT-PRIVATE-451ea287",
    "case": "CHK10-CASE-PRIVATE-cfe25112",
    "authority": "CHK10-AUTHORITY-PRIVATE-0f89a614",
}


def run(
    repository: Path,
    manifest: Path,
    *responses: Path,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--repository",
        str(repository),
        "--sentinels",
        str(manifest),
    ]
    for response in responses:
        command.extend(["--response", str(response)])
    return subprocess.run(command, text=True, capture_output=True, check=False)


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)
    print(f"PASS  {message}")


def assert_no_sentinel_diagnostic(result: subprocess.CompletedProcess[str]) -> bool:
    output = result.stdout + result.stderr
    return all(value not in output for value in SENTINELS.values())


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="private-boundary.") as raw:
        root = Path(raw)
        repository = root / "public"
        repository.mkdir()
        subprocess.run(
            ["/usr/bin/git", "-C", str(repository), "init", "-q"],
            check=True,
        )
        subprocess.run(
            ["/usr/bin/git", "-C", str(repository), "config", "user.email", "fixture@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["/usr/bin/git", "-C", str(repository), "config", "user.name", "Fixture"],
            check=True,
        )
        (repository / "README.md").write_text("public fixture\n", encoding="utf-8")
        subprocess.run(
            ["/usr/bin/git", "-C", str(repository), "add", "README.md"],
            check=True,
        )
        manifest = root / "sentinels.json"
        manifest.write_text(json.dumps(SENTINELS), encoding="utf-8")
        response = root / "dashboard.json"
        response.write_text('{"ok":true,"data":{"status":"redacted"}}\n', encoding="utf-8")
        private_state = root / "private-state"
        private_state.mkdir()
        (private_state / "fixtures.json").write_text(
            json.dumps(SENTINELS),
            encoding="utf-8",
        )

        result = run(repository, manifest, response)
        require(result.returncode == 0, "private fixture state is excluded from public surfaces")

        nested = repository / "nested"
        nested.mkdir()
        result = run(nested, manifest, response)
        require(result.returncode == 2, "repository subdirectories fail closed")

        malformed_manifest = root / "malformed-sentinels.json"
        malformed_manifest.write_text("{}", encoding="utf-8")
        result = run(repository, malformed_manifest, response)
        require(result.returncode == 2, "empty sentinel manifests fail closed")

        missing_response = root / "missing-dashboard.json"
        result = run(repository, manifest, missing_response)
        require(result.returncode == 2, "missing dashboard responses fail closed")

        untracked = repository / "untracked-private.txt"
        untracked.write_text(SENTINELS["settings"], encoding="utf-8")
        result = run(repository, manifest, response)
        require(result.returncode == 0, "repository validation is scoped to tracked public blobs")
        untracked.unlink()

        leak = repository / "leak.txt"
        for name, sentinel in SENTINELS.items():
            leak.write_text(sentinel, encoding="utf-8")
            subprocess.run(
                ["/usr/bin/git", "-C", str(repository), "add", "leak.txt"],
                check=True,
            )
            result = run(repository, manifest, response)
            require(
                result.returncode == 1
                and "private-boundary violation:" in result.stderr
                and assert_no_sentinel_diagnostic(result),
                f"tracked {name} sentinel is rejected",
            )
            subprocess.run(
                ["/usr/bin/git", "-C", str(repository), "rm", "--cached", "-q", "leak.txt"],
                check=True,
            )
            leak.unlink()

        symlink = repository / "private-link"
        symlink.symlink_to(SENTINELS["credential"])
        subprocess.run(
            ["/usr/bin/git", "-C", str(repository), "add", "private-link"],
            check=True,
        )
        result = run(repository, manifest, response)
        require(
            result.returncode == 1
            and assert_no_sentinel_diagnostic(result),
            "tracked symlink blob sentinel is rejected without disclosure",
        )
        subprocess.run(
            ["/usr/bin/git", "-C", str(repository), "rm", "--cached", "-q", "private-link"],
            check=True,
        )
        symlink.unlink()

        first = subprocess.run(
            ["/usr/bin/git", "-C", str(repository), "hash-object", "-w", "--stdin"],
            input="first\n",
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        second = subprocess.run(
            ["/usr/bin/git", "-C", str(repository), "hash-object", "-w", "--stdin"],
            input="second\n",
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ["/usr/bin/git", "-C", str(repository), "update-index", "--index-info"],
            input=(
                f"100644 {first} 1\tconflict.txt\n"
                f"100644 {second} 2\tconflict.txt\n"
            ),
            text=True,
            check=True,
        )
        result = run(repository, manifest, response)
        require(result.returncode == 2, "unmerged index stages fail closed")
        subprocess.run(
            ["/usr/bin/git", "-C", str(repository), "update-index", "--force-remove", "conflict.txt"],
            check=True,
        )

        for name, sentinel in SENTINELS.items():
            response.write_text(json.dumps({"data": sentinel}), encoding="utf-8")
            result = run(repository, manifest, response)
            require(
                result.returncode == 1
                and "private-boundary violation:" in result.stderr
                and assert_no_sentinel_diagnostic(result),
                f"dashboard {name} sentinel is rejected",
            )

    print("private boundary tests: PASS")


if __name__ == "__main__":
    main()
