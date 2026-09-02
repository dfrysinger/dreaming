#!/usr/bin/env python3
"""Deterministic tests for sanitized estate recommendation records."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("estate-recommendation.py")


def digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def write_census(root: Path, *, extra_skill: bool = False) -> None:
    enabled_instances = [
        {
            "runtime_name": "fixture",
            "runtime_source": "skills",
            "instance_id": "fixture-instance",
            "authority": "legacy_machine",
        },
        {
            "runtime_name": "protected-fixture",
            "runtime_source": "skills",
            "instance_id": "protected-instance",
            "authority": "user_protected",
        },
    ]
    physical_instances = [
        {
            "instance_id": "fixture-instance",
            "root_class": "personal",
        },
        {
            "instance_id": "protected-instance",
            "root_class": "personal",
        },
    ]
    if extra_skill:
        enabled_instances.append(
            {
                "runtime_name": "extra-fixture",
                "runtime_source": "skills",
                "instance_id": "extra-instance",
                "authority": "unknown_provenance",
            }
        )
        physical_instances.append(
            {
                "instance_id": "extra-instance",
                "root_class": "personal",
            }
        )
    payload = {
        "schema_version": 1,
        "scope": {"complete": True},
        "unresolved_mappings": [],
        "enabled_instances": enabled_instances,
        "physical_instances": physical_instances,
        "plugins": [],
    }
    census = {**payload, "snapshot_sha256": digest(payload)}
    receipt = {
        "schema_version": 1,
        "snapshot_sha256": census["snapshot_sha256"],
        "receiver": {
            "collector_sha256": "a" * 64,
            "receiver_id": "fixture",
            "receiver_sha256": "b" * 64,
        },
        "census": census,
    }
    receipt_sha256 = digest(receipt)
    receipts = root / "estate-census-receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    (receipts / f"{receipt_sha256.removeprefix('sha256:')}.json").write_text(
        json.dumps(receipt)
    )
    (root / "estate-census-current.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "receipt_sha256": receipt_sha256,
                "snapshot_sha256": census["snapshot_sha256"],
                "census": census,
            }
        )
    )


def run(
    root: Path,
    status: str,
    *,
    target: str = "fixture",
    decision: str = "archive",
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--state-root",
            str(root),
            "--target-kind",
            "personal_skill",
            "--target",
            target,
            "--decision",
            decision,
            "--status",
            status,
            "--at",
            "2026-08-14T00:00:00+00:00",
        ],
        capture_output=True,
        check=False,
        text=True,
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        write_census(root)
        first = run(root, "recommended")
        assert first.returncode == 0, first.stderr
        write_census(root, extra_skill=True)
        replacement = run(root, "kept")
        assert replacement.returncode == 0, replacement.stderr
        second = run(
            root,
            "protected",
            target="protected-fixture",
            decision="keep",
        )
        assert second.returncode == 0, second.stderr
        private = run(
            root,
            "unknown",
            target="private transcript text",
        )
        assert private.returncode == 2
        assert "estate-recommendation-target-invalid" in private.stderr
        path = root / "estate-action-ledger.json"
        records = json.loads(path.read_text())
        assert len(records) == 2
        by_target = {item["target"]: item for item in records}
        assert by_target["fixture"]["status"] == "kept"
        assert by_target["fixture"]["authority"] == "legacy_machine"
        assert (
            by_target["protected-fixture"]["authority"]
            == "user_protected"
        )
        records[0]["decision"] = "tampered"
        path.write_text(json.dumps(records))
        rejected = run(root, "unknown")
        assert rejected.returncode == 2
        assert "estate-recommendation-record-invalid" in rejected.stderr
    print("estate recommendation tests: PASS")


if __name__ == "__main__":
    main()
