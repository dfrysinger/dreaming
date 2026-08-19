#!/usr/bin/env python3
"""Build and reconcile a bounded Copilot skill-estate census."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
ROOT_CLASSES = {
    "builtin",
    "custom",
    "dreaming_publisher",
    "personal",
    "plugin",
    "project",
}
AUTHORITIES = {
    "cli_builtin",
    "dreaming_managed",
    "legacy_machine",
    "plugin_managed",
    "unknown_provenance",
    "user_protected",
}
SHA256_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
LEGACY_POLICY_VERSION = 1
LEGACY_PROOF_VERSION = 1
LEGACY_PROOF_KIND = "legacy_git_creation"
USAGE_INDEX_SCHEMA_VERSION = 1
USAGE_PARSER_REVISION = 2
USAGE_QUIET_SECONDS = 300
MAX_USAGE_SESSION_ISSUES = 32
USAGE_ALIASES = {
    "architecture-guardrails": {
        "target": "guardrails",
        "evidence": [
            {
                "repository": "dfrysinger/skills",
                "commit": "ba32528e71dd0065ad9950cc30d413fb81c302d0",
                "kind": "git_rename",
                "from": "architecture-guardrails",
                "to": "guardrails",
            },
        ],
    },
    "autopilot-brief": {
        "target": "unattended-run",
        "evidence": [
            {
                "repository": "dfrysinger/skills",
                "commit": "ba32528e71dd0065ad9950cc30d413fb81c302d0",
                "kind": "git_rename",
                "from": "autopilot-brief",
                "to": "brief",
            },
            {
                "repository": "dfrysinger/skills",
                "commit": "68f0ce55c35f106b51b05f34e64f0f739fd5911f",
                "kind": "git_rename",
                "from": "brief",
                "to": "autopilot-loop",
            },
            {
                "repository": "dfrysinger/skills",
                "commit": "f25fabaca4aff4f1c0b85f4d89aaf3f8fdd28b72",
                "kind": "git_rename",
                "from": "autopilot-loop",
                "to": "unattended-run",
            },
        ],
    },
    "context-hygiene": {
        "target": "self-compact",
        "evidence": [
            {
                "repository": "dfrysinger/skills",
                "commit": "ba32528e71dd0065ad9950cc30d413fb81c302d0",
                "kind": "git_rename",
                "from": "context-hygiene",
                "to": "hygiene",
            },
            {
                "repository": "dfrysinger/skills",
                "commit": "68f0ce55c35f106b51b05f34e64f0f739fd5911f",
                "kind": "git_rename",
                "from": "hygiene",
                "to": "self-compact",
            },
        ],
    },
    "feature-development-loop": {
        "target": "development-loop",
        "evidence": [
            {
                "repository": "dfrysinger/skills",
                "commit": "ba32528e71dd0065ad9950cc30d413fb81c302d0",
                "kind": "git_rename",
                "from": "feature-development-loop",
                "to": "develop",
            },
            {
                "repository": "dfrysinger/skills",
                "commit": "68f0ce55c35f106b51b05f34e64f0f739fd5911f",
                "kind": "git_rename",
                "from": "develop",
                "to": "development-loop",
            },
        ],
    },
    "gaw-development": {
        "target": "gaw",
        "evidence": [
            {
                "repository": "personal-skills",
                "commit": "4adf93d6eebd8af101d5c71f985cfe78dbd6b216",
                "kind": "git_rename",
                "from": "gaw-development",
                "to": "gaw",
            },
        ],
    },
    "gated-pr-merge": {
        "target": "development-loop",
        "evidence": [
            {
                "repository": "personal-skills",
                "commit": "ac8a291fa0d5fbef0c1e368864fd7758fe47732c",
                "kind": "git_rename",
                "from": "gated-pr-merge",
                "to": "gated-change-loop",
            },
            {
                "repository": "personal-skills",
                "commit": "4b7cd72361b8d5cfb104a4c003df354736faa535",
                "kind": "git_rename",
                "from": "gated-change-loop",
                "to": "feature-development-loop",
            },
            {
                "repository": "dfrysinger/skills",
                "commit": "ba32528e71dd0065ad9950cc30d413fb81c302d0",
                "kind": "git_rename",
                "from": "feature-development-loop",
                "to": "develop",
            },
            {
                "repository": "dfrysinger/skills",
                "commit": "68f0ce55c35f106b51b05f34e64f0f739fd5911f",
                "kind": "git_rename",
                "from": "develop",
                "to": "development-loop",
            },
        ],
    },
    "loop": {
        "target": "microsoft-loop",
        "evidence": [
            {
                "repository": "personal-skills",
                "commit": "4adf93d6eebd8af101d5c71f985cfe78dbd6b216",
                "kind": "git_rename",
                "from": "loop",
                "to": "microsoft-loop",
            },
        ],
    },
    "nexus-dev": {
        "target": "nexus-gotchas",
        "evidence": [
            {
                "repository": "personal-skills",
                "commit": "d3747d163f71339acdd0152046ba900044c22f1d",
                "kind": "git_rename",
                "from": "nexus-dev",
                "to": "nexus-map",
            },
            {
                "repository": "personal-skills",
                "commit": "4adf93d6eebd8af101d5c71f985cfe78dbd6b216",
                "kind": "git_rename",
                "from": "nexus-map",
                "to": "nexus-gotchas",
            },
        ],
    },
    "prototype-reference-integration": {
        "target": "absorb-poc",
        "evidence": [
            {
                "repository": "personal-skills",
                "commit": "d3747d163f71339acdd0152046ba900044c22f1d",
                "kind": "git_rename",
                "from": "prototype-reference-integration",
                "to": "absorb-poc",
            },
        ],
    },
    "upstream-contribution": {
        "target": "upstream-pitch",
        "evidence": [
            {
                "repository": "personal-skills",
                "commit": "d3747d163f71339acdd0152046ba900044c22f1d",
                "kind": "git_rename",
                "from": "upstream-contribution",
                "to": "upstream-pitch",
            },
        ],
    },
}
PLUGIN_CAPABILITY_CLASSES = (
    "skills",
    "agents",
    "hooks",
    "mcp_servers",
    "lsp_servers",
)
PLUGIN_METADATA_FIELDS = {
    "$schema",
    "author",
    "compatibility",
    "description",
    "displayName",
    "engines",
    "homepage",
    "keywords",
    "license",
    "name",
    "repository",
    "version",
}
PLUGIN_SKILL_DISABLE_STATES = {
    "obsolete",
    "redundant",
    "regressing",
    "unsupported",
}
PLUGIN_NON_SKILL_DISABLE_STATES = {
    "declared_unnecessary",
    "superseded",
    "unused_complete_telemetry",
}
PLUGIN_DEPENDENCY_CLASSES = (
    "explicit_dependencies",
    "pins",
    "durable_prompts",
    "scheduled_jobs",
    "mcp_configurations",
    "agent_selections",
    "hook_configurations",
    "lsp_configurations",
    "ambiguous",
)
DEPENDENCY_BINARY_SUFFIXES = {
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".pyc",
    ".webp",
    ".zip",
}
RUNTIME_DEPENDENCY_WORDS_RE = re.compile(
    r"\b(?:call|calls|delegate|delegates|invoke|invokes|launch|launches|"
    r"own|owns|require|requires|route|routes|run|runs|use|uses)\b",
    re.IGNORECASE,
)
NEGATED_DEPENDENCY_RE = re.compile(
    r"\b(?:do\s+not|does\s+not|not(?:\s+the|\s+a|\s+an)?|without)\b",
    re.IGNORECASE,
)


class EstateError(RuntimeError):
    """A fail-closed estate collection or reconciliation error."""


class ProvenanceFailure(ValueError):
    """A non-authorizing provenance classification result."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def file_sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def skill_inventory(skill: Path) -> tuple[list[dict[str, str]], str]:
    if skill.is_symlink() or not skill.is_dir():
        raise EstateError(f"unsafe skill root: {skill}")
    files: list[dict[str, str]] = []
    for path in sorted(skill.rglob("*")):
        if path.is_symlink():
            raise EstateError(f"skill inventory contains symlink: {path}")
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(skill).as_posix(),
                    "sha256": file_sha256(path),
                }
            )
    if not any(item["path"] == "SKILL.md" for item in files):
        raise EstateError(f"skill has no SKILL.md: {skill}")
    return files, digest(files)


def classification(
    authority: str,
    status: str,
    basis: str,
    *,
    policy_sha256: str | None = None,
    proof_sha256: str | None = None,
    verified_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provenance = {"status": status, "basis": basis}
    if policy_sha256 is not None:
        provenance["policy_sha256"] = policy_sha256
    if proof_sha256 is not None:
        provenance["proof_sha256"] = proof_sha256
    result = {"authority": authority, "provenance": provenance}
    if verified_evidence is not None:
        result["_verified_evidence"] = verified_evidence
    return result


def public_classification(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "authority": value["authority"],
        "provenance": dict(value["provenance"]),
    }


def frontmatter_author(skill_md: Path) -> str:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as error:
        raise ProvenanceFailure("unreadable_skill_frontmatter") from error
    match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", text, re.DOTALL)
    if not match:
        raise ProvenanceFailure("malformed_skill_frontmatter")
    authors = re.findall(r"(?m)^author:\s*([^\s#]+)\s*$", match.group(1))
    if len(authors) != 1:
        raise ProvenanceFailure("ambiguous_skill_author")
    return authors[0]


def current_envelope_evidence(
    skill: Path, evidence_tool: Path
) -> dict[str, Any]:
    marker = skill / ".agent-created"
    envelope = skill / ".agent-created.json"
    if not marker.exists() and not envelope.exists():
        raise ProvenanceFailure("no_evidence")
    if not marker.is_file() or marker.is_symlink():
        raise ProvenanceFailure("invalid_creation_marker")
    if not envelope.exists():
        raise ProvenanceFailure("marker_only")
    if not envelope.is_file() or envelope.is_symlink():
        raise ProvenanceFailure("invalid_provenance_envelope")
    try:
        validation = subprocess.run(
            [str(evidence_tool), "validate", str(envelope)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ProvenanceFailure("provenance_validator_unavailable") from error
    if validation.returncode:
        raise ProvenanceFailure("malformed_provenance_envelope")
    try:
        provenance = json.loads(envelope.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProvenanceFailure("malformed_provenance_envelope") from error
    if not isinstance(provenance, dict) or provenance.get("skill") != skill.name:
        raise ProvenanceFailure("provenance_skill_mismatch")
    created_by = provenance.get("created_by", "skill-review")
    if frontmatter_author(skill / "SKILL.md") != created_by:
        raise ProvenanceFailure("provenance_author_mismatch")
    created_at = provenance.get("created_at")
    source_session_id = provenance.get("source_session_id")
    if not isinstance(created_at, str) or not created_at:
        raise ProvenanceFailure("provenance_created_at_missing")
    if not isinstance(source_session_id, str) or not source_session_id:
        raise ProvenanceFailure("provenance_source_missing")
    return {
        "basis": (
            "current_envelope"
            if provenance.get("schema_version") == 2
            else "legacy_envelope"
        ),
        "marker": str(marker),
        "marker_sha256": file_sha256(marker),
        "envelope": str(envelope),
        "envelope_sha256": file_sha256(envelope),
        "envelope_schema_version": provenance.get("schema_version", 1),
        "created_at": created_at,
        "source_session_id": source_session_id,
        "created_by": created_by,
        "skill_md": str(skill / "SKILL.md"),
        "skill_md_sha256": file_sha256(skill / "SKILL.md"),
        "author": created_by,
    }


def require_sealed_payload(
    value: Any, expected_fields: set[str], seal_field: str, reason: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise ProvenanceFailure(reason)
    seal = value.get(seal_field)
    payload = {key: item for key, item in value.items() if key != seal_field}
    if not isinstance(seal, str) or seal != digest(payload):
        raise ProvenanceFailure(f"{reason}_digest")
    return value


def validate_legacy_policy(value: Any) -> dict[str, Any]:
    policy = require_sealed_payload(
        value,
        {
            "schema_version",
            "accepted_legacy_proof_versions",
            "machine_authors",
            "migration_cutoff",
            "protected_claim_paths",
            "policy_sha256",
        },
        "policy_sha256",
        "invalid_legacy_policy",
    )
    if policy["schema_version"] != LEGACY_POLICY_VERSION:
        raise ProvenanceFailure("unsupported_legacy_policy_version")
    versions = policy["accepted_legacy_proof_versions"]
    if (
        not isinstance(versions, list)
        or not versions
        or any(not isinstance(item, int) or isinstance(item, bool) for item in versions)
        or len(versions) != len(set(versions))
    ):
        raise ProvenanceFailure("invalid_legacy_policy_versions")
    authors = policy["machine_authors"]
    if not isinstance(authors, list) or not authors:
        raise ProvenanceFailure("invalid_machine_author_policy")
    normalized_authors: set[tuple[str, str]] = set()
    for author in authors:
        if (
            not isinstance(author, dict)
            or set(author) != {"name", "email"}
            or not isinstance(author["name"], str)
            or not author["name"]
            or not isinstance(author["email"], str)
            or not author["email"]
        ):
            raise ProvenanceFailure("invalid_machine_author_policy")
        normalized_authors.add((author["name"], author["email"]))
    if len(normalized_authors) != len(authors):
        raise ProvenanceFailure("ambiguous_machine_author_policy")
    try:
        cutoff = datetime.fromisoformat(
            str(policy["migration_cutoff"]).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ProvenanceFailure("invalid_migration_cutoff") from error
    if cutoff.tzinfo is None:
        raise ProvenanceFailure("invalid_migration_cutoff")
    claim_paths = policy["protected_claim_paths"]
    if (
        not isinstance(claim_paths, list)
        or len(claim_paths) != len(set(claim_paths))
        or any(
            not isinstance(item, str)
            or not item
            or Path(item).is_absolute()
            or ".." in Path(item).parts
            for item in claim_paths
        )
    ):
        raise ProvenanceFailure("invalid_protected_claim_policy")
    return {
        **policy,
        "_machine_authors": normalized_authors,
        "_migration_cutoff": cutoff.astimezone(timezone.utc),
    }


def validate_legacy_proof(
    value: Any, skill_name: str, policy: dict[str, Any]
) -> dict[str, Any]:
    proof = require_sealed_payload(
        value,
        {
            "schema_version",
            "kind",
            "skill",
            "creation_commit",
            "history_checkpoint",
            "creation_inventory_sha256",
            "policy_sha256",
            "proof_sha256",
        },
        "proof_sha256",
        "invalid_legacy_proof",
    )
    version = proof["schema_version"]
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version not in policy["accepted_legacy_proof_versions"]
        or version != LEGACY_PROOF_VERSION
    ):
        raise ProvenanceFailure("unsupported_legacy_proof_version")
    if proof["kind"] != LEGACY_PROOF_KIND:
        raise ProvenanceFailure("unsupported_legacy_proof_kind")
    if proof["skill"] != skill_name:
        raise ProvenanceFailure("legacy_proof_skill_mismatch")
    if proof["policy_sha256"] != policy["policy_sha256"]:
        raise ProvenanceFailure("legacy_proof_policy_mismatch")
    if not isinstance(proof["creation_commit"], str) or not GIT_COMMIT_RE.fullmatch(
        proof["creation_commit"]
    ):
        raise ProvenanceFailure("invalid_legacy_creation_commit")
    if not isinstance(proof["history_checkpoint"], str) or not GIT_COMMIT_RE.fullmatch(
        proof["history_checkpoint"]
    ):
        raise ProvenanceFailure("invalid_legacy_history_checkpoint")
    if (
        not isinstance(proof["creation_inventory_sha256"], str)
        or not SHA256_ID_RE.fullmatch(proof["creation_inventory_sha256"])
    ):
        raise ProvenanceFailure("invalid_legacy_creation_inventory")
    return proof


def git_output(root: Path, *args: str, text: bool = True) -> str | bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=text,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ProvenanceFailure("legacy_git_unavailable") from error
    if result.returncode:
        raise ProvenanceFailure("legacy_git_verification_failed")
    return result.stdout


def require_git_ancestor(
    root: Path, ancestor: str, descendant: str, reason: str
) -> None:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ProvenanceFailure("legacy_git_unavailable") from error
    if result.returncode == 1:
        raise ProvenanceFailure(reason)
    if result.returncode:
        raise ProvenanceFailure("legacy_git_verification_failed")


def git_skill_inventory(
    root: Path, commit: str, relative: str
) -> tuple[list[dict[str, str]], str]:
    output = git_output(root, "ls-tree", "-r", "-z", commit, "--", relative, text=False)
    assert isinstance(output, bytes)
    files: list[dict[str, str]] = []
    prefix = relative.rstrip("/") + "/"
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split(" ")
        path = raw_path.decode("utf-8")
        if object_type != "blob" or mode == "120000" or not path.startswith(prefix):
            raise ProvenanceFailure("unsafe_legacy_creation_inventory")
        content = git_output(root, "cat-file", "blob", object_id, text=False)
        assert isinstance(content, bytes)
        files.append(
            {
                "path": path.removeprefix(prefix),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    files.sort(key=lambda item: item["path"])
    if not any(item["path"] == "SKILL.md" for item in files):
        raise ProvenanceFailure("legacy_creation_missing_skill")
    return files, digest(files)


def first_history_commit(root: Path, head: str, relative: str) -> str:
    output = git_output(root, "rev-list", "--reverse", head, "--", relative)
    assert isinstance(output, str)
    commits = output.splitlines()
    if not commits:
        raise ProvenanceFailure("legacy_creation_history_missing")
    return commits[0]


def commit_author(root: Path, commit: str) -> tuple[str, str]:
    output = git_output(root, "show", "-s", "--format=%an%x00%ae", commit)
    assert isinstance(output, str)
    values = output.rstrip("\n").split("\x00")
    if len(values) != 2 or not all(values):
        raise ProvenanceFailure("legacy_machine_author_missing")
    return values[0], values[1]


def commit_timestamp(root: Path, commit: str) -> datetime:
    output = git_output(root, "show", "-s", "--format=%aI", commit)
    assert isinstance(output, str)
    try:
        value = datetime.fromisoformat(output.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ProvenanceFailure("legacy_creation_timestamp_invalid") from error
    if value.tzinfo is None:
        raise ProvenanceFailure("legacy_creation_timestamp_invalid")
    return value.astimezone(timezone.utc)


def verify_legacy_git_creation(
    skill: Path,
    root: dict[str, Any],
    relative: str,
    policy_value: Any,
    proof_value: Any,
) -> dict[str, Any]:
    policy = validate_legacy_policy(policy_value)
    proof = validate_legacy_proof(proof_value, skill.name, policy)
    marker = skill / ".agent-created"
    if not marker.is_file() or marker.is_symlink():
        raise ProvenanceFailure("invalid_creation_marker")
    root_path = Path(root["path"]).expanduser().resolve()
    top = git_output(root_path, "rev-parse", "--show-toplevel")
    assert isinstance(top, str)
    if Path(top.strip()).resolve() != root_path:
        raise ProvenanceFailure("unexpected_personal_git_root")
    head = git_output(root_path, "rev-parse", "HEAD")
    assert isinstance(head, str)
    head = head.strip()
    creation = proof["creation_commit"]
    checkpoint = proof["history_checkpoint"]
    if checkpoint == creation:
        raise ProvenanceFailure("legacy_history_checkpoint_not_distinct")
    require_git_ancestor(
        root_path,
        creation,
        checkpoint,
        "legacy_history_checkpoint_precedes_creation",
    )
    require_git_ancestor(
        root_path,
        checkpoint,
        head,
        "legacy_history_checkpoint_rewritten",
    )
    status = git_output(
        root_path, "status", "--porcelain=v1", "--untracked-files=all", "--", relative
    )
    assert isinstance(status, str)
    if status:
        raise ProvenanceFailure("legacy_skill_worktree_dirty")
    if first_history_commit(root_path, head, relative) != creation:
        raise ProvenanceFailure("legacy_creation_not_initial_package")
    marker_relative = f"{relative}/.agent-created"
    if first_history_commit(root_path, head, marker_relative) != creation:
        raise ProvenanceFailure("legacy_marker_not_created_with_package")
    files, inventory_sha256 = git_skill_inventory(root_path, creation, relative)
    if inventory_sha256 != proof["creation_inventory_sha256"]:
        raise ProvenanceFailure("legacy_creation_inventory_mismatch")
    creation_marker = next(
        (item for item in files if item["path"] == ".agent-created"), None
    )
    if creation_marker is None or file_sha256(marker) != creation_marker["sha256"]:
        raise ProvenanceFailure("legacy_marker_changed")
    author = commit_author(root_path, creation)
    if author not in policy["_machine_authors"]:
        raise ProvenanceFailure("legacy_machine_author_untrusted")
    if commit_timestamp(root_path, creation) >= policy["_migration_cutoff"]:
        raise ProvenanceFailure("legacy_creation_not_before_migration")
    later = git_output(root_path, "rev-list", f"{creation}..{head}", "--", relative)
    assert isinstance(later, str)
    for commit in later.splitlines():
        if commit_author(root_path, commit) not in policy["_machine_authors"]:
            raise ProvenanceFailure("later_user_authorship_conflict")
    for claim_path in policy["protected_claim_paths"]:
        current_claim = skill / claim_path
        if current_claim.exists() or current_claim.is_symlink():
            raise ProvenanceFailure("later_user_protection_claim")
        claim_history = git_output(
            root_path,
            "rev-list",
            f"{creation}..{head}",
            "--",
            f"{relative}/{claim_path}",
        )
        assert isinstance(claim_history, str)
        if claim_history:
            raise ProvenanceFailure("later_user_protection_claim")
    return {
        "basis": "verified_legacy_git_proof",
        "policy_sha256": policy["policy_sha256"],
        "proof_sha256": proof["proof_sha256"],
        "creation_commit": creation,
        "history_checkpoint": checkpoint,
        "creation_inventory_sha256": inventory_sha256,
        "marker": str(marker),
        "marker_sha256": file_sha256(marker),
        "machine_author": {"name": author[0], "email": author[1]},
    }


def verify_publisher_identity(
    skill: Path,
    root: dict[str, Any],
    relative: str,
    files: list[dict[str, str]],
) -> None:
    bundle_id = root.get("bundle_id")
    if not isinstance(bundle_id, str) or not SHA256_ID_RE.fullmatch(bundle_id):
        raise ProvenanceFailure("invalid_dreaming_bundle_identity")
    manifest_path = Path(root["path"]).expanduser().resolve() / "dreaming-bundle-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ProvenanceFailure("missing_dreaming_bundle_manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProvenanceFailure("malformed_dreaming_bundle_manifest") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("bundle_id") != bundle_id
        or digest({key: value for key, value in manifest.items() if key != "bundle_id"})
        != bundle_id
    ):
        raise ProvenanceFailure("invalid_dreaming_bundle_manifest")
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, list):
        raise ProvenanceFailure("invalid_dreaming_bundle_inventory")
    expected: dict[str, str] = {}
    for item in manifest_files:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256"}
            or not isinstance(item["path"], str)
            or not isinstance(item["sha256"], str)
            or item["path"] in expected
        ):
            raise ProvenanceFailure("invalid_dreaming_bundle_inventory")
        expected[item["path"]] = item["sha256"]
    prefix = f"{relative}/"
    expected_skill = {
        path: sha256 for path, sha256 in expected.items() if path.startswith(prefix)
    }
    actual_skill = {
        f"{relative}/{item['path']}": item["sha256"] for item in files
    }
    if actual_skill != expected_skill:
        raise ProvenanceFailure("dreaming_bundle_inventory_mismatch")


def classify_skill_authority(
    skill: Path,
    root: dict[str, Any],
    relative: str,
    files: list[dict[str, str]],
    *,
    evidence_tool: Path | None = None,
) -> dict[str, Any]:
    root_class = root["class"]
    if root_class == "plugin":
        package = root.get("package")
        identity = {
            "plugin_id": root.get("plugin_id"),
            "source_identity": root.get("source_identity"),
            "version": root.get("version"),
        }
        if (
            all(isinstance(value, str) and value for value in identity.values())
            and package == identity
        ):
            return classification("plugin_managed", "verified", "exact_plugin_identity")
        return classification(
            "unknown_provenance", "invalid", "invalid_plugin_identity"
        )
    if root_class == "builtin":
        if (
            isinstance(root.get("copilot_version"), str)
            and root["copilot_version"]
            and relative
        ):
            return classification("cli_builtin", "verified", "exact_cli_identity")
        return classification(
            "unknown_provenance", "invalid", "invalid_cli_identity"
        )
    if root_class == "dreaming_publisher":
        try:
            verify_publisher_identity(skill, root, relative, files)
        except ProvenanceFailure as error:
            return classification("unknown_provenance", "invalid", error.reason)
        return classification(
            "dreaming_managed", "verified", "current_lifecycle_catalog"
        )

    pin = skill / ".pinned"
    if pin.exists() or pin.is_symlink():
        return classification("user_protected", "protected", "explicit_user_pin")
    if root_class != "personal":
        return classification("unknown_provenance", "insufficient", "no_evidence")

    proofs = root.get("legacy_proofs", {})
    if not isinstance(proofs, dict):
        return classification(
            "unknown_provenance", "invalid", "invalid_legacy_proof_index"
        )
    proof_present = skill.name in proofs
    envelope_present = (skill / ".agent-created.json").exists() or (
        skill / ".agent-created.json"
    ).is_symlink()
    envelope_evidence: dict[str, Any] | None = None
    if envelope_present:
        try:
            envelope_evidence = current_envelope_evidence(
                skill,
                evidence_tool
                or Path(__file__).with_name("evidence-envelope.py"),
            )
        except ProvenanceFailure as error:
            return classification("unknown_provenance", "invalid", error.reason)
    if proof_present:
        try:
            legacy_evidence = verify_legacy_git_creation(
                skill,
                root,
                relative,
                root.get("provenance_policy"),
                proofs[skill.name],
            )
        except ProvenanceFailure as error:
            return classification("unknown_provenance", "invalid", error.reason)
        if envelope_evidence is None:
            return classification(
                "legacy_machine",
                "verified",
                legacy_evidence["basis"],
                policy_sha256=legacy_evidence["policy_sha256"],
                proof_sha256=legacy_evidence["proof_sha256"],
                verified_evidence=legacy_evidence,
            )
    if envelope_evidence is not None:
        return classification(
            "legacy_machine",
            "verified",
            envelope_evidence["basis"],
            verified_evidence=envelope_evidence,
        )
    if (skill / ".agent-created").exists() or (skill / ".agent-created").is_symlink():
        return classification("unknown_provenance", "insufficient", "marker_only")
    return classification("unknown_provenance", "insufficient", "no_evidence")


def canonical_capability_id(
    root: dict[str, Any], relative: str, inventory_sha256: str
) -> str:
    root_class = root["class"]
    if root_class == "builtin":
        identity = {
            "class": root_class,
            "copilot_version": root.get("copilot_version"),
            "relative_path": relative,
        }
    elif root_class == "plugin":
        identity = {
            "class": root_class,
            "plugin_id": root.get("plugin_id"),
            "source_identity": root.get("source_identity"),
            "version": root.get("version"),
            "relative_path": relative,
        }
    elif root_class == "dreaming_publisher":
        identity = {
            "class": root_class,
            "bundle_id": root.get("bundle_id"),
            "relative_path": relative,
        }
    elif root_class == "personal":
        identity = {
            "class": root_class,
            "skill_name": Path(relative).name,
            "inventory_lineage": inventory_sha256,
        }
    elif root_class == "project":
        identity = {
            "class": root_class,
            "repository_identity": root.get("repository_identity"),
            "relative_path": relative,
            "inventory_lineage": inventory_sha256,
        }
    else:
        identity = {
            "class": root_class,
            "root_id": root["id"],
            "relative_path": relative,
            "inventory_lineage": inventory_sha256,
        }
    if any(value is None for value in identity.values()):
        raise EstateError(f"incomplete canonical identity for root {root['id']}")
    return digest(identity)


def scan_root(host_id: str, root: dict[str, Any]) -> list[dict[str, Any]]:
    required = {"id", "class", "path", "discovery_surface"}
    if not required.issubset(root):
        raise EstateError(f"root is missing fields: {sorted(required - set(root))}")
    if root["class"] not in ROOT_CLASSES:
        raise EstateError(f"unsupported root class: {root['class']}")
    path = Path(root["path"]).expanduser().resolve()
    if path.is_symlink():
        raise EstateError(f"root is a symlink: {path}")
    if not path.exists():
        return []
    if not path.is_dir():
        raise EstateError(f"root is not a directory: {path}")
    instances: list[dict[str, Any]] = []
    for skill in sorted(path.iterdir()):
        if skill.name.startswith(".") or not skill.is_dir():
            continue
        if not (skill / "SKILL.md").is_file():
            continue
        files, inventory_sha256 = skill_inventory(skill)
        relative = skill.relative_to(path).as_posix()
        authority = classify_skill_authority(skill, root, relative, files)
        physical_identity = {
            "host_id": host_id,
            "root_id": root["id"],
            "absolute_path": str(skill.resolve()),
            "inventory_sha256": inventory_sha256,
        }
        instances.append(
            {
                "instance_id": digest(physical_identity),
                "canonical_capability_id": canonical_capability_id(
                    root, relative, inventory_sha256
                ),
                "host_id": host_id,
                "root_id": root["id"],
                "root_class": root["class"],
                "discovery_surface": root["discovery_surface"],
                "absolute_path": str(skill.resolve()),
                "relative_path": relative,
                "skill_name": skill.name,
                "inventory_sha256": inventory_sha256,
                "files": files,
                **public_classification(authority),
                "owner": root.get("owner"),
                "package": root.get("package"),
                "physical_only": True,
            }
        )
    return instances


def _dependency_reference_lines(
    source: dict[str, Any],
    target_names: set[str],
) -> tuple[list[dict[str, str]], list[str]]:
    skill_root = Path(source["absolute_path"])
    references: list[dict[str, str]] = []
    unscanned: list[str] = []
    for inventory_item in source["files"]:
        relative = inventory_item["path"]
        path = skill_root / relative
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise EstateError(f"cannot read dependency source: {path}") from error
        if hashlib.sha256(raw).hexdigest() != inventory_item["sha256"]:
            raise EstateError(f"dependency source changed during collection: {path}")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            if path.suffix.casefold() in DEPENDENCY_BINARY_SUFFIXES or b"\0" in raw:
                continue
            unscanned.append(relative)
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            for target_name in target_names:
                if target_name == source["skill_name"]:
                    continue
                escaped = re.escape(target_name)
                if re.search(
                    rf"(?<![A-Za-z0-9._-])/"
                    rf"(?:[A-Za-z0-9._-]+:)?{escaped}"
                    rf"(?![A-Za-z0-9._-])",
                    line,
                ):
                    references.append(
                        {
                            "target": target_name,
                            "kind": "runtime_capability",
                            "source_file": relative,
                            "source_line": str(line_number),
                        }
                    )
                if re.search(rf"\.\./{escaped}(?:/|\b)", line):
                    references.append(
                        {
                            "target": target_name,
                            "kind": "installed_content",
                            "source_file": relative,
                            "source_line": str(line_number),
                        }
                    )
        for target_name in target_names:
            if target_name == source["skill_name"]:
                continue
            token_re = re.compile(
                rf"(?<![A-Za-z0-9._-])`{re.escape(target_name)}`"
                rf"(?![A-Za-z0-9._-])"
            )
            for match in token_re.finditer(content):
                context_start = max(0, match.start() - 180)
                context_end = min(len(content), match.end() + 100)
                context = " ".join(content[context_start:context_end].split())
                before = " ".join(
                    content[max(0, match.start() - 55) : match.start()].split()
                )
                if (
                    RUNTIME_DEPENDENCY_WORDS_RE.search(context)
                    and not NEGATED_DEPENDENCY_RE.search(before)
                ):
                    references.append(
                        {
                            "target": target_name,
                            "kind": "runtime_capability",
                            "source_file": relative,
                            "source_line": str(
                                content.count("\n", 0, match.start()) + 1
                            ),
                        }
                    )
    unique = {
        (
            item["target"],
            item["kind"],
            item["source_file"],
            item["source_line"],
        ): item
        for item in references
    }
    return [unique[key] for key in sorted(unique)], sorted(unscanned)


def apply_dependency_inventory(
    physical: list[dict[str, Any]],
    enabled_instances: list[dict[str, Any]],
    durable_inventory: dict[str, Any] | None,
    *,
    source_population_complete: bool,
) -> dict[str, Any]:
    by_instance = {item["instance_id"]: item for item in physical}
    enabled_by_capability: dict[str, list[dict[str, Any]]] = {}
    names_to_capabilities: dict[str, set[str]] = {}
    for mapping in enabled_instances:
        if mapping.get("runtime_enabled") is not True:
            continue
        capability_id = mapping["canonical_capability_id"]
        enabled_by_capability.setdefault(capability_id, []).append(mapping)
        name = normalize_skill_name(mapping.get("runtime_name"))
        if name is not None:
            names_to_capabilities.setdefault(name, set()).add(capability_id)

    representatives: dict[str, dict[str, Any]] = {}
    for capability_id, mappings in enabled_by_capability.items():
        representative = next(
            (
                by_instance.get(mapping["instance_id"])
                for mapping in mappings
                if mapping["instance_id"] in by_instance
            ),
            None,
        )
        if representative is not None:
            representatives[capability_id] = representative

    target_names = set(names_to_capabilities)
    source_references: list[dict[str, Any]] = []
    unscanned_source_files: list[str] = []
    for source_capability_id, source in sorted(representatives.items()):
        references, unscanned = _dependency_reference_lines(source, target_names)
        unscanned_source_files.extend(
            f"{source['skill_name']}/{relative}" for relative in unscanned
        )
        for reference in references:
            for target_capability_id in sorted(
                names_to_capabilities.get(reference["target"], set())
            ):
                source_references.append(
                    {
                        **reference,
                        "source_capability_id": source_capability_id,
                        "source_skill": source["skill_name"],
                        "target_capability_id": target_capability_id,
                    }
                )

    durable_complete = (
        isinstance(durable_inventory, dict)
        and durable_inventory.get("complete") is True
        and isinstance(durable_inventory.get("dependencies"), list)
        and isinstance(durable_inventory.get("skills"), list)
    )
    durable_targets: dict[str, list[str]] = {}
    pinned_names: set[str] = set()
    if durable_complete:
        durable_skill_names: set[str] = set()
        for item in durable_inventory["skills"]:
            if not isinstance(item, dict):
                durable_complete = False
                break
            name = normalize_skill_name(item.get("name"))
            if (
                name is None
                or name in durable_skill_names
                or not isinstance(item.get("pinned"), bool)
            ):
                durable_complete = False
                break
            durable_skill_names.add(name)
            if item["pinned"]:
                pinned_names.add(name)
        seen_durable_targets: set[str] = set()
        for item in durable_inventory["dependencies"]:
            normalized = (
                normalize_skill_name(item.get("skill"))
                if isinstance(item, dict)
                else None
            )
            sources = item.get("sources") if isinstance(item, dict) else None
            if (
                normalized is None
                or normalized not in durable_skill_names
                or normalized not in names_to_capabilities
                or normalized in seen_durable_targets
                or not isinstance(sources, list)
                or not sources
                or not all(isinstance(source, str) and source for source in sources)
            ):
                durable_complete = False
                break
            seen_durable_targets.add(normalized)
            durable_targets[normalized] = sorted(set(sources))

    blockers_by_capability: dict[str, list[dict[str, str]]] = {}
    installed_by_capability: dict[str, list[dict[str, str]]] = {}
    for reference in source_references:
        row = {
            "kind": reference["kind"],
            "source_skill": reference["source_skill"],
            "source_capability_id": reference["source_capability_id"],
            "source_file": reference["source_file"],
            "source_line": reference["source_line"],
        }
        destination = (
            blockers_by_capability
            if reference["kind"] == "runtime_capability"
            else installed_by_capability
        )
        destination.setdefault(reference["target_capability_id"], []).append(row)

    for target_name, sources in durable_targets.items():
        for capability_id in names_to_capabilities.get(target_name, set()):
            for source in sorted(set(sources)):
                blockers_by_capability.setdefault(capability_id, []).append(
                    {
                        "kind": "durable_owner",
                        "source_skill": "Scheduled or durable configuration",
                        "source_capability_id": "",
                        "source_file": source,
                        "source_line": "",
                    }
                )
    for target_name in pinned_names:
        for capability_id in names_to_capabilities.get(target_name, set()):
            blockers_by_capability.setdefault(capability_id, []).append(
                {
                    "kind": "explicit_pin",
                    "source_skill": "Explicit user pin",
                    "source_capability_id": "",
                    "source_file": ".pinned",
                    "source_line": "",
                }
            )
    for capability_id, representative in representatives.items():
        if representative.get("authority") == "user_protected":
            blockers_by_capability.setdefault(capability_id, []).append(
                {
                    "kind": "explicit_pin",
                    "source_skill": "Explicit user pin",
                    "source_capability_id": "",
                    "source_file": ".pinned",
                    "source_line": "",
                }
            )

    source_graph_complete = (
        source_population_complete and not unscanned_source_files
    )
    inventory_complete = durable_complete and source_graph_complete
    for capability_id, representative in representatives.items():
        blockers = sorted(
            blockers_by_capability.get(capability_id, []),
            key=lambda item: (
                item["kind"],
                item["source_skill"],
                item["source_file"],
                item["source_line"],
            ),
        )
        installed_dependencies = sorted(
            installed_by_capability.get(capability_id, []),
            key=lambda item: (
                item["source_skill"],
                item["source_file"],
                item["source_line"],
            ),
        )
        dependency = {
            "state": (
                "protected"
                if blockers
                else "clear"
                if inventory_complete
                else "incomplete"
            ),
            "complete": inventory_complete,
            "blockers": blockers,
            "installed_content_consumers": installed_dependencies,
        }
        for mapping in enabled_by_capability[capability_id]:
            instance = by_instance.get(mapping["instance_id"])
            if instance is not None:
                instance["dependencies"] = dependency
                instance["dependencies_complete"] = inventory_complete

    return {
        "schema_version": 1,
        "complete": inventory_complete,
        "durable_inventory_complete": durable_complete,
        "source_graph_complete": source_graph_complete,
        "unscanned_source_file_count": len(unscanned_source_files),
        "runtime_dependency_count": sum(
            len(items) for items in blockers_by_capability.values()
        ),
        "installed_content_dependency_count": sum(
            len(items) for items in installed_by_capability.values()
        ),
        "protected_capability_count": len(blockers_by_capability),
        "inventory_sha256": digest(durable_inventory),
    }


def validate_context(context: dict[str, Any]) -> None:
    required = {"id", "kind", "registered", "runtime_skills"}
    if not required.issubset(context):
        raise EstateError(
            f"context is missing fields: {sorted(required - set(context))}"
        )
    if context["kind"] not in {"user", "project"}:
        raise EstateError(f"unsupported context kind: {context['kind']}")
    if not isinstance(context["registered"], bool):
        raise EstateError("context registered must be a boolean")
    if not isinstance(context["runtime_skills"], list):
        raise EstateError("context runtime_skills must be a list")


def reconcile(
    *,
    host_id: str,
    roots: list[dict[str, Any]],
    contexts: list[dict[str, Any]],
    collected_at: str,
    evidence: dict[str, Any] | None = None,
    durable_dependency_inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not host_id:
        raise EstateError("host_id is required")
    if not roots:
        raise EstateError("at least one declared root is required")
    root_ids = [root.get("id") for root in roots]
    if len(root_ids) != len(set(root_ids)):
        raise EstateError("declared root IDs must be unique")

    physical = [
        instance for root in roots for instance in scan_root(host_id, root)
    ]
    by_path: dict[str, list[dict[str, Any]]] = {}
    for instance in physical:
        by_path.setdefault(instance["absolute_path"], []).append(instance)

    reconciled_contexts: list[dict[str, Any]] = []
    enabled_instances: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for context in contexts:
        validate_context(context)
        if not context["registered"]:
            reconciled_contexts.append(
                {
                    **{key: value for key, value in context.items() if key != "runtime_skills"},
                    "inside_completeness_claim": False,
                    "complete": None,
                    "runtime_skill_count": len(context["runtime_skills"]),
                    "mapped_skill_count": 0,
                    "unresolved_count": 0,
                }
            )
            continue
        context_unresolved: list[dict[str, Any]] = []
        mapped = 0
        for runtime_skill in context["runtime_skills"]:
            if not isinstance(runtime_skill, dict):
                raise EstateError("runtime skill must be an object")
            path_value = runtime_skill.get("path")
            if not isinstance(path_value, str) or not path_value:
                raise EstateError("runtime skill path is required")
            path = str(Path(path_value).expanduser().resolve())
            matches = by_path.get(path, [])
            if len(matches) != 1:
                row = {
                    "context_id": context["id"],
                    "runtime_name": runtime_skill.get("name"),
                    "runtime_source": runtime_skill.get("source"),
                    "runtime_path": path,
                    "reason": "unmapped" if not matches else "multiply_mapped",
                    "candidate_instance_ids": [
                        match["instance_id"] for match in matches
                    ],
                }
                context_unresolved.append(row)
                unresolved.append(row)
                continue
            instance = matches[0]
            mapped += 1
            instance["physical_only"] = False
            enabled_instances.append(
                {
                    "context_id": context["id"],
                    "runtime_name": runtime_skill.get("name"),
                    "runtime_source": runtime_skill.get("source"),
                    "runtime_enabled": runtime_skill.get("enabled") is True,
                    "instance_id": instance["instance_id"],
                    "canonical_capability_id": instance[
                        "canonical_capability_id"
                    ],
                    "authority": instance["authority"],
                }
            )
        reconciled_contexts.append(
            {
                **{key: value for key, value in context.items() if key != "runtime_skills"},
                "inside_completeness_claim": True,
                "complete": not context_unresolved,
                "runtime_skill_count": len(context["runtime_skills"]),
                "mapped_skill_count": mapped,
                "unresolved_count": len(context_unresolved),
            }
        )

    claimed = [
        context
        for context in reconciled_contexts
        if context["inside_completeness_claim"]
    ]
    complete = bool(claimed) and all(context["complete"] for context in claimed)
    dependency_summary = apply_dependency_inventory(
        physical,
        enabled_instances,
        durable_dependency_inventory,
        source_population_complete=complete,
    )
    canonical_ids = {
        instance["canonical_capability_id"] for instance in enabled_instances
    }
    authority_counts = Counter(
        instance["authority"] for instance in physical
    )
    root_class_counts = Counter(
        instance["root_class"] for instance in physical
    )
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "host_id": host_id,
        "collected_at": collected_at,
        "scope": {
            "label": "MacBook user-level and explicitly registered project contexts",
            "complete": complete,
            "registered_context_ids": [
                context["id"] for context in claimed
            ],
            "outside_context_ids": [
                context["id"]
                for context in reconciled_contexts
                if not context["inside_completeness_claim"]
            ],
        },
        "totals": {
            "physical_instances": len(physical),
            "effective_instances": len(enabled_instances),
            "canonical_capabilities": len(canonical_ids),
            "physical_only_instances": sum(
                instance["physical_only"] for instance in physical
            ),
            "unresolved_runtime_skills": len(unresolved),
        },
        "authority_counts": {
            authority: authority_counts.get(authority, 0)
            for authority in sorted(AUTHORITIES)
        },
        "root_class_counts": {
            root_class: root_class_counts.get(root_class, 0)
            for root_class in sorted(ROOT_CLASSES)
        },
        "contexts": reconciled_contexts,
        "physical_instances": physical,
        "enabled_instances": enabled_instances,
        "unresolved_mappings": unresolved,
        "evidence": {
            **(evidence or {}),
            "dependency_inventory": dependency_summary,
        },
    }
    return {**snapshot, "snapshot_sha256": digest(snapshot)}


def normalize_skill_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFC", value).strip().casefold()
    return (
        normalized
        if re.fullmatch(r"[a-z0-9](?:[a-z0-9:-]{0,198}[a-z0-9])?", normalized)
        else None
    )


def parse_usage_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def opaque_session_id(name: str) -> str:
    return "sha256:" + hashlib.sha256(name.encode("utf-8")).hexdigest()


def parse_usage_session(
    lines: Any, *, collected_at: datetime
) -> tuple[list[tuple[str, datetime]], datetime | None, list[str]]:
    starts: dict[str, tuple[str | None, datetime, int]] = {}
    completions: dict[str, tuple[bool, datetime, str | None, int]] = {}
    earliest: datetime | None = None
    issues: list[str] = []
    for event_index, raw_line in enumerate(lines):
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError as error:
            raise EstateError("usage_session_invalid_utf8") from error
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise EstateError("usage_session_malformed_json") from error
        if not isinstance(event, dict):
            raise EstateError("usage_session_event_not_object")
        timestamp = parse_usage_time(event.get("timestamp"))
        if timestamp is None:
            raise EstateError("usage_session_invalid_timestamp")
        if timestamp > collected_at:
            raise EstateError("usage_session_future_timestamp")
        earliest = timestamp if earliest is None else min(earliest, timestamp)
        event_type = event.get("type")
        data = event.get("data")
        if event_type == "tool.execution_start" and isinstance(data, dict):
            if str(data.get("toolName", "")).casefold() != "skill":
                continue
            call_id = data.get("toolCallId")
            arguments = data.get("arguments")
            name = (
                normalize_skill_name(
                    arguments.get("skill", arguments.get("name"))
                )
                if isinstance(arguments, dict)
                else None
            )
            if not isinstance(call_id, str) or not call_id:
                raise EstateError("usage_session_invalid_skill_start")
            if call_id in starts:
                raise EstateError("usage_session_duplicate_skill_start")
            starts[call_id] = (name, timestamp, event_index)
        elif event_type == "tool.execution_complete" and isinstance(data, dict):
            call_id = data.get("toolCallId")
            if not isinstance(call_id, str) or not call_id:
                continue
            if call_id in completions:
                raise EstateError("usage_session_duplicate_completion")
            result = data.get("result")
            content = result.get("content") if isinstance(result, dict) else None
            match = (
                re.match(r'^Skill "([^"]+)" loaded successfully\.', content)
                if isinstance(content, str)
                else None
            )
            loaded_name = normalize_skill_name(match.group(1)) if match else None
            completions[call_id] = (
                data.get("success") is True,
                timestamp,
                loaded_name,
                event_index,
            )
    successful: list[tuple[str, datetime]] = []
    if any(
        success and loaded_name is not None and call_id not in starts
        for call_id, (success, _, loaded_name, _) in completions.items()
    ):
        issues.append("usage_session_unmatched_skill_completion")
    for call_id, (name, started_at, start_index) in starts.items():
        completion = completions.get(call_id)
        if completion is None or not completion[0]:
            continue
        if name is None:
            issues.append("usage_session_invalid_skill_name")
            continue
        _, completed_at, loaded_name, completion_index = completion
        if (
            completion_index <= start_index
            or completed_at < started_at
            or loaded_name != name
        ):
            issues.append("usage_session_unverified_skill_completion")
            continue
        successful.append((name, completed_at))
    return successful, earliest, sorted(set(issues))


def usage_name_mappings(
    census: dict[str, Any],
) -> tuple[dict[str, set[str]], set[str], list[tuple[str, str]]]:
    mappings: dict[str, set[str]] = {}
    capability_ids: set[str] = set()
    issues: list[tuple[str, str]] = []
    for item in census.get("enabled_instances", []):
        if not isinstance(item, dict) or item.get("runtime_enabled") is not True:
            continue
        name = normalize_skill_name(item.get("runtime_name"))
        capability_id = item.get("canonical_capability_id")
        if not isinstance(capability_id, str):
            continue
        capability_ids.add(capability_id)
        if name is None:
            issues.append((capability_id, "usage_census_invalid_runtime_name"))
            continue
        mappings.setdefault(name, set()).add(capability_id)
    for capability_ids_for_name in mappings.values():
        if len(capability_ids_for_name) > 1:
            issues.extend(
                (capability_id, "usage_census_conflicting_mapping")
                for capability_id in capability_ids_for_name
            )
    return mappings, capability_ids, issues


def validate_usage_aliases(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise EstateError("usage_aliases_invalid")
    aliases: dict[str, str] = {}
    for historical, entry in value.items():
        if normalize_skill_name(historical) != historical or not isinstance(entry, dict):
            raise EstateError("usage_aliases_invalid")
        target = entry.get("target")
        evidence = entry.get("evidence")
        if (
            normalize_skill_name(target) != target
            or target == historical
            or target in value
            or not isinstance(evidence, list)
            or not evidence
        ):
            raise EstateError("usage_aliases_invalid")
        current = historical
        for item in evidence:
            if (
                not isinstance(item, dict)
                or set(item) != {"repository", "commit", "kind", "from", "to"}
                or not isinstance(item["repository"], str)
                or not item["repository"]
                or not isinstance(item["commit"], str)
                or GIT_COMMIT_RE.fullmatch(item["commit"]) is None
                or item["kind"] != "git_rename"
                or normalize_skill_name(item["from"]) != item["from"]
                or normalize_skill_name(item["to"]) != item["to"]
                or item["from"] != current
            ):
                raise EstateError("usage_aliases_invalid")
            current = item["to"]
        if current != target:
            raise EstateError("usage_aliases_invalid")
        aliases[historical] = target
    return aliases


def usage_fingerprint(info: os.stat_result) -> dict[str, int]:
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
    }


def valid_usage_day(value: Any) -> bool:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def validate_usage_index(value: Any) -> dict[str, Any]:
    parser_revision = (
        value.get("parser_revision", 1) if isinstance(value, dict) else None
    )
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != USAGE_INDEX_SCHEMA_VERSION
        or not isinstance(parser_revision, int)
        or isinstance(parser_revision, bool)
        or not 1 <= parser_revision <= USAGE_PARSER_REVISION
        or not isinstance(value.get("sessions"), dict)
    ):
        raise EstateError("usage_index_invalid")
    sessions: dict[str, Any] = {}
    for session_id, entry in value["sessions"].items():
        if not isinstance(session_id, str) or not SHA256_ID_RE.fullmatch(session_id):
            raise EstateError("usage_index_invalid")
        if not isinstance(entry, dict):
            raise EstateError("usage_index_invalid")
        fingerprint = entry.get("fingerprint")
        if (
            not isinstance(fingerprint, dict)
            or set(fingerprint) != {"device", "inode", "size", "mtime_ns"}
            or any(
                not isinstance(fingerprint.get(name), int)
                or isinstance(fingerprint.get(name), bool)
                or fingerprint[name] < 0
                for name in fingerprint
            )
        ):
            raise EstateError("usage_index_invalid")
        earliest = entry.get("earliest_event")
        if earliest is not None and parse_usage_time(earliest) is None:
            raise EstateError("usage_index_invalid")
        checkpointed = entry.get("checkpointed_at")
        if parse_usage_time(checkpointed) is None:
            raise EstateError("usage_index_invalid")
        issues = entry.get("issues")
        if (
            not isinstance(issues, list)
            or len(issues) > MAX_USAGE_SESSION_ISSUES
            or any(not isinstance(item, str) or len(item) > 128 for item in issues)
        ):
            raise EstateError("usage_index_invalid")
        usage = entry.get("usage")
        if not isinstance(usage, dict):
            raise EstateError("usage_index_invalid")
        validated_usage: dict[str, Any] = {}
        for name, summary in usage.items():
            if normalize_skill_name(name) != name or not isinstance(summary, dict):
                raise EstateError("usage_index_invalid")
            daily = summary.get("daily")
            last = summary.get("last_successful_invocation")
            if (
                not isinstance(daily, dict)
                or parse_usage_time(last) is None
                or any(
                    not valid_usage_day(day)
                    or not isinstance(count, int)
                    or isinstance(count, bool)
                    or count < 1
                    for day, count in daily.items()
                )
            ):
                raise EstateError("usage_index_invalid")
            validated_usage[name] = {
                "daily": dict(sorted(daily.items())),
                "last_successful_invocation": last,
            }
        sessions[session_id] = {
            "fingerprint": {name: fingerprint[name] for name in sorted(fingerprint)},
            "earliest_event": earliest,
            "usage": validated_usage,
            "issues": sorted(set(issues)),
            "checkpointed_at": checkpointed,
        }
    return {
        "schema_version": USAGE_INDEX_SCHEMA_VERSION,
        "parser_revision": parser_revision,
        "sessions": sessions,
    }


def write_usage_index(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise EstateError("usage_index_unwritable")
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8") + b"\n"
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if os.path.exists(temporary):
                os.unlink(temporary)
    except OSError as error:
        raise EstateError("usage_index_unwritable") from error


def load_usage_index(
    path: Path | None, *, collected_at: datetime
) -> tuple[dict[str, Any], str]:
    empty = {
        "schema_version": USAGE_INDEX_SCHEMA_VERSION,
        "parser_revision": USAGE_PARSER_REVISION,
        "sessions": {},
    }
    if path is None or not path.exists():
        return empty, "absent"
    try:
        if path.is_symlink() or not path.is_file():
            raise EstateError("usage_index_invalid")
        raw = path.read_bytes()
        index = validate_usage_index(json.loads(raw))
    except (OSError, json.JSONDecodeError, EstateError):
        stamp = collected_at.strftime("%Y%m%dT%H%M%SZ")
        suffix = hashlib.sha256(
            raw if "raw" in locals() else str(path).encode("utf-8")
        ).hexdigest()[:12]
        rejected = path.with_name(f"{path.name}.rejected-{stamp}-{suffix}")
        try:
            os.replace(path, rejected)
        except OSError as error:
            raise EstateError("usage_index_reject_failed") from error
        return empty, "rebuilt"
    if index["parser_revision"] < USAGE_PARSER_REVISION:
        index["sessions"] = {
            session_id: entry
            for session_id, entry in index["sessions"].items()
            if not entry["issues"]
        }
        index["parser_revision"] = USAGE_PARSER_REVISION
        write_usage_index(path, index)
        return index, "migrated"
    return index, "loaded"


def usage_session_summary(
    successful: list[tuple[str, datetime]],
    earliest: datetime | None,
    issues: list[str],
    fingerprint: dict[str, int],
    collected_at: datetime,
) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    for name, timestamp in successful:
        summary = usage.setdefault(
            name,
            {
                "daily": Counter(),
                "last_successful_invocation": timestamp,
            },
        )
        summary["daily"][timestamp.date().isoformat()] += 1
        summary["last_successful_invocation"] = max(
            timestamp, summary["last_successful_invocation"]
        )
    return {
        "fingerprint": fingerprint,
        "earliest_event": earliest.isoformat() if earliest else None,
        "usage": {
            name: {
                "daily": dict(sorted(summary["daily"].items())),
                "last_successful_invocation": summary[
                    "last_successful_invocation"
                ].isoformat(),
            }
            for name, summary in sorted(usage.items())
        },
        "issues": sorted(set(issues))[:MAX_USAGE_SESSION_ISSUES],
        "checkpointed_at": collected_at.isoformat(),
    }


def collect_usage(
    census: dict[str, Any],
    session_root: Path,
    *,
    collected_at: datetime,
    max_sessions: int,
    max_bytes: int,
    index_path: Path | None = None,
    quiet_seconds: int = USAGE_QUIET_SECONDS,
) -> dict[str, Any]:
    mappings, canonical_ids, mapping_issues = usage_name_mappings(census)
    aliases = validate_usage_aliases(USAGE_ALIASES)
    failures: list[dict[str, Any]] = []
    pending_records: dict[str, dict[str, Any]] = {}
    sessions_parsed = 0
    bytes_parsed = 0
    bound_reached: str | None = None
    corpus_failed = False
    index, index_status = load_usage_index(index_path, collected_at=collected_at)
    indexed_summaries = index["sessions"]

    def fail(
        session_id: str,
        reason: str,
        *,
        corpus: bool = False,
        candidate: dict[str, Any] | None = None,
        candidate_capability_ids: tuple[str, ...] = (),
    ) -> str:
        nonlocal corpus_failed
        corpus_failed = corpus_failed or corpus
        payload = {
            "session_id": session_id,
            "reason": reason,
            "modified_at": (
                datetime.fromtimestamp(
                    candidate["mtime_ns"] / 1_000_000_000,
                    timezone.utc,
                ).isoformat()
                if candidate is not None
                else None
            ),
            "bytes": candidate["size"] if candidate is not None else None,
            "candidate_capability_ids": list(candidate_capability_ids),
        }
        failure_id = digest(payload)
        failures.append({"failure_id": failure_id, **payload})
        return failure_id

    def record_pending(
        candidate: dict[str, Any],
        reason: str,
        failure_id: str | None = None,
    ) -> None:
        pending_records[candidate["session_id"]] = {
            "session_id": candidate["session_id"],
            "reason": reason,
            "modified_at": datetime.fromtimestamp(
                candidate["mtime_ns"] / 1_000_000_000,
                timezone.utc,
            ).isoformat(),
            "bytes": candidate["size"],
            "failure_id": failure_id,
        }

    if census.get("scope", {}).get("complete") is not True:
        fail(opaque_session_id("census"), "usage_census_incomplete")
    for capability_id, reason in mapping_issues:
        fail(
            opaque_session_id(f"census:{capability_id}"),
            reason,
            candidate_capability_ids=(capability_id,),
        )

    try:
        if session_root.is_symlink() or not session_root.is_dir():
            raise OSError("session root is unavailable")
        children = list(session_root.iterdir())
    except OSError as error:
        raise EstateError("session_root_unavailable") from error

    present_sessions = {opaque_session_id(session.name) for session in children}
    removed = set(indexed_summaries) - present_sessions
    if removed:
        for session_id in removed:
            del indexed_summaries[session_id]
        if index_path is not None:
            write_usage_index(index_path, index)

    candidates: list[dict[str, Any]] = []
    for session in children:
        session_id = opaque_session_id(session.name)
        if session.is_symlink():
            fail(session_id, "session_symlink", corpus=True)
            continue
        try:
            if not session.is_dir():
                continue
        except OSError:
            fail(session_id, "session_unreadable", corpus=True)
            continue
        events = session / "events.jsonl"
        if events.is_symlink():
            fail(session_id, "events_symlink", corpus=True)
            continue
        try:
            before = events.stat()
        except FileNotFoundError:
            present_sessions.discard(session_id)
            if session_id in indexed_summaries:
                del indexed_summaries[session_id]
                if index_path is not None:
                    write_usage_index(index_path, index)
            continue
        except OSError:
            fail(session_id, "events_unreadable", corpus=True)
            continue
        if not stat.S_ISREG(before.st_mode):
            fail(session_id, "events_not_regular", corpus=True)
            continue
        fingerprint = usage_fingerprint(before)
        candidates.append(
            {
                "name": session.name,
                "session_id": session_id,
                "events": events,
                "fingerprint": fingerprint,
                "size": before.st_size,
                "mtime_ns": before.st_mtime_ns,
            }
        )

    pending_candidates = [
        candidate
        for candidate in candidates
        if indexed_summaries.get(candidate["session_id"], {}).get("fingerprint")
        != candidate["fingerprint"]
    ]
    pending_candidates.sort(key=lambda item: (item["mtime_ns"], item["session_id"]))
    stable_pending: list[dict[str, Any]] = []
    for candidate in pending_candidates:
        age_seconds = collected_at.timestamp() - (
            candidate["mtime_ns"] / 1_000_000_000
        )
        if quiet_seconds > 0 and age_seconds < quiet_seconds:
            record_pending(candidate, "events_recently_modified")
        else:
            stable_pending.append(candidate)

    for position, candidate in enumerate(stable_pending):
        session_id = candidate["session_id"]
        before_fingerprint = candidate["fingerprint"]
        if sessions_parsed >= max_sessions:
            bound_reached = "max_sessions"
            for deferred in stable_pending[position:]:
                record_pending(deferred, "stable_budget_deferred")
            break
        oversized = candidate["size"] > max_bytes and sessions_parsed == 0
        if bytes_parsed + candidate["size"] > max_bytes and not oversized:
            bound_reached = "max_bytes"
            for deferred in stable_pending[position:]:
                record_pending(deferred, "stable_budget_deferred")
            break
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(candidate["events"], flags)
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode) or usage_fingerprint(
                    opened
                ) != before_fingerprint:
                    raise OSError("events changed before read")
                sessions_parsed += 1
                bytes_parsed += candidate["size"]
                with os.fdopen(descriptor, "rb", closefd=False) as handle:
                    successful, session_earliest, session_issues = (
                        parse_usage_session(handle, collected_at=collected_at)
                    )
                after = os.fstat(descriptor)
                if usage_fingerprint(after) != before_fingerprint:
                    raise OSError("events changed during read")
            finally:
                os.close(descriptor)
        except EstateError as error:
            reason = str(error)
            failure_id = fail(
                session_id,
                reason,
                corpus=True,
                candidate=candidate,
            )
            record_pending(candidate, reason, failure_id)
            if oversized:
                bound_reached = "max_bytes"
                for deferred in stable_pending[position + 1 :]:
                    record_pending(deferred, "stable_budget_deferred")
                break
            continue
        except OSError:
            reason = "events_changed_or_unreadable"
            failure_id = fail(
                session_id,
                reason,
                corpus=True,
                candidate=candidate,
            )
            record_pending(candidate, reason, failure_id)
            if oversized:
                bound_reached = "max_bytes"
                for deferred in stable_pending[position + 1 :]:
                    record_pending(deferred, "stable_budget_deferred")
                break
            continue
        indexed_summaries[session_id] = usage_session_summary(
            successful,
            session_earliest,
            session_issues,
            before_fingerprint,
            collected_at,
        )
        pending_records.pop(session_id, None)
        if index_path is not None:
            write_usage_index(index_path, index)
        if oversized:
            bound_reached = "max_bytes"
            for deferred in stable_pending[position + 1 :]:
                record_pending(deferred, "stable_budget_deferred")
            break

    exact_indexed = {
        candidate["session_id"]
        for candidate in candidates
        if indexed_summaries.get(candidate["session_id"], {}).get("fingerprint")
        == candidate["fingerprint"]
    }
    discovered_sessions = len(candidates)
    discovered_bytes = sum(candidate["size"] for candidate in candidates)
    indexed_sessions = len(exact_indexed)
    indexed_bytes = sum(
        candidate["size"]
        for candidate in candidates
        if candidate["session_id"] in exact_indexed
    )
    pending_sessions = discovered_sessions - indexed_sessions
    pending_bytes = discovered_bytes - indexed_bytes
    if pending_sessions == 0:
        bound_reached = None
    for candidate in candidates:
        if (
            candidate["session_id"] not in exact_indexed
            and candidate["session_id"] not in pending_records
        ):
            record_pending(candidate, "stable_budget_deferred")

    counts: dict[str, Counter[str]] = {}
    last_used: dict[str, datetime] = {}
    unattributed: dict[tuple[str, str, tuple[str, ...]], Counter[str]] = {}
    earliest_retained: datetime | None = None
    for session_id, summary in indexed_summaries.items():
        if session_id not in present_sessions:
            continue
        earliest = parse_usage_time(summary.get("earliest_event"))
        if earliest is not None:
            earliest_retained = (
                earliest
                if earliest_retained is None
                else min(earliest_retained, earliest)
            )
        for reason in summary.get("issues", []):
            candidate = next(
                (
                    item
                    for item in candidates
                    if item["session_id"] == session_id
                ),
                None,
            )
            fail(session_id, reason, corpus=True, candidate=candidate)
        for name, usage in summary.get("usage", {}).items():
            capability_ids = mappings.get(name, set())
            attribution_reason = "direct"
            if not capability_ids and name in aliases:
                capability_ids = mappings.get(aliases[name], set())
                attribution_reason = (
                    "alias"
                    if len(capability_ids) == 1
                    else (
                        "alias_target_missing"
                        if not capability_ids
                        else "alias_target_conflicting"
                    )
                )
            windows: Counter[str] = Counter()
            for day, value in usage["daily"].items():
                used_on = datetime.strptime(day, "%Y-%m-%d").date()
                age_days = (collected_at.date() - used_on).days
                windows["uses_total"] += value
                windows["uses_7d"] += value if 0 <= age_days < 7 else 0
                windows["uses_30d"] += value if 0 <= age_days < 30 else 0
                windows["uses_90d"] += value if 0 <= age_days < 90 else 0
            timestamp = parse_usage_time(usage["last_successful_invocation"])
            if timestamp is None:
                raise EstateError("usage_index_invalid")
            if len(capability_ids) == 1:
                capability_id = next(iter(capability_ids))
                counts.setdefault(capability_id, Counter()).update(windows)
                last_used[capability_id] = max(
                    timestamp, last_used.get(capability_id, timestamp)
                )
            else:
                reason = (
                    attribution_reason
                    if attribution_reason.startswith("alias_target_")
                    else ("unmapped" if not capability_ids else "conflicting_mapping")
                )
                unattributed.setdefault(
                    (name, reason, tuple(sorted(capability_ids))),
                    Counter(),
                ).update(windows)

    corpus_complete = pending_sessions == 0 and not corpus_failed
    attribution_complete = (
        census.get("scope", {}).get("complete") is True
        and not mapping_issues
        and not unattributed
    )
    complete = corpus_complete and attribution_complete and not failures
    usage_rows = []
    for capability_id in sorted(canonical_ids):
        value = counts.get(capability_id, Counter())
        usage_rows.append(
            {
                "canonical_capability_id": capability_id,
                "uses_7d": value["uses_7d"],
                "uses_30d": value["uses_30d"],
                "uses_90d": value["uses_90d"],
                "uses_total": value["uses_total"],
                "last_successful_invocation": (
                    last_used[capability_id].isoformat()
                    if capability_id in last_used
                    else None
                ),
            }
        )
    snapshot = {
        "schema_version": 1,
        "host_id": census.get("host_id"),
        "collected_at": census.get("collected_at"),
        "census_snapshot_sha256": census.get("snapshot_sha256"),
        "source": "copilot_local_session_state",
        "coverage": {
            "complete": complete,
            "corpus_complete": corpus_complete,
            "attribution_complete": attribution_complete,
            "earliest_retained_event": (
                earliest_retained.isoformat() if earliest_retained else None
            ),
            "discovered_sessions": discovered_sessions,
            "discovered_bytes": discovered_bytes,
            "indexed_sessions": indexed_sessions,
            "indexed_bytes": indexed_bytes,
            "pending_sessions": pending_sessions,
            "pending_bytes": pending_bytes,
            "sessions_scanned": sessions_parsed,
            "bytes_scanned": bytes_parsed,
            "sessions_parsed_this_run": sessions_parsed,
            "bytes_parsed_this_run": bytes_parsed,
            "max_sessions": max_sessions,
            "max_bytes": max_bytes,
            "quiet_seconds": quiet_seconds,
            "collection_watermark": collected_at.isoformat(),
            "bound_reached": bound_reached,
            "work_budget_stopped_run": bound_reached is not None,
            "index_status": index_status,
            "pending": [
                pending_records[session_id]
                for session_id in sorted(pending_records)
            ],
            "failures": sorted(
                failures,
                key=lambda item: (
                    item["session_id"],
                    item["reason"],
                    item["failure_id"],
                ),
            ),
        },
        "canonical_usage": usage_rows,
        "unattributed": [
            {
                "name": name,
                "reason": reason,
                "uses_7d": value["uses_7d"],
                "uses_30d": value["uses_30d"],
                "uses_90d": value["uses_90d"],
                "uses_total": value["uses_total"],
                "candidate_capability_ids": list(candidate_capability_ids),
            }
            for (name, reason, candidate_capability_ids), value in sorted(
                unattributed.items()
            )
        ],
    }
    return {**snapshot, "snapshot_sha256": digest(snapshot)}


def collect_bundle(config: dict[str, Any]) -> dict[str, Any]:
    census = collect(config)
    collected_at = parse_usage_time(census.get("collected_at"))
    if collected_at is None:
        raise EstateError("census collected_at is invalid")
    target_home = Path(config.get("target_home", Path.home())).expanduser().resolve()
    session_root = Path(
        config.get("copilot_session_root", target_home / ".copilot/session-state")
    ).expanduser()
    index_path = Path(
        config.get(
            "usage_index_path",
            target_home / ".local/state/dreaming/copilot-usage-index.json",
        )
    ).expanduser()
    max_sessions = config.get("usage_max_sessions", 10_000)
    max_bytes = config.get("usage_max_bytes", 1024 * 1024 * 1024)
    if (
        not isinstance(max_sessions, int)
        or isinstance(max_sessions, bool)
        or max_sessions < 1
        or not isinstance(max_bytes, int)
        or isinstance(max_bytes, bool)
        or max_bytes < 1
    ):
        raise EstateError("usage bounds must be positive integers")
    return {
        "census": census,
        "usage": collect_usage(
            census,
            session_root,
            collected_at=collected_at,
            max_sessions=max_sessions,
            max_bytes=max_bytes,
            index_path=index_path,
        ),
    }


def run_json(command: list[str], cwd: Path) -> Any:
    try:
        process = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise EstateError(f"command failed: {command[0]}: {error}") from error
    if process.returncode != 0:
        raise EstateError(
            f"command failed: {' '.join(command)}: {process.stderr.strip()}"
        )
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise EstateError(f"command returned malformed JSON: {command}") from error


def run_text(command: list[str], cwd: Path) -> str:
    try:
        process = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise EstateError(f"command failed: {command[0]}: {error}") from error
    if process.returncode != 0:
        raise EstateError(
            f"command failed: {' '.join(command)}: {process.stderr.strip()}"
        )
    return process.stdout


def copilot_version(binary: str) -> str:
    try:
        process = subprocess.run(
            [binary, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise EstateError(f"cannot inspect Copilot version: {error}") from error
    if process.returncode != 0:
        raise EstateError("cannot inspect Copilot version")
    first = process.stdout.splitlines()[0] if process.stdout else ""
    version = first.rsplit(" ", 1)[-1].rstrip(".")
    if not version or not version[0].isdigit():
        raise EstateError(f"unrecognized Copilot version: {first}")
    return version


def git_identity(path: Path) -> dict[str, str]:
    try:
        root = subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        head = subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise EstateError(f"registered project is not a Git checkout: {path}") from error
    return {"repository_identity": str(Path(root).resolve()), "head": head}


def read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EstateError(f"invalid JSON: {path}") from error


def plugin_manifest(package_root: Path) -> tuple[dict[str, Any], str]:
    candidates = (
        package_root / "plugin.json",
        package_root / ".github/plugin/plugin.json",
        package_root / ".claude-plugin/plugin.json",
    )
    for path in candidates:
        if not path.is_file():
            continue
        value = read_json_file(path)
        if not isinstance(value, dict):
            raise EstateError(f"plugin manifest must be an object: {path}")
        return value, path.relative_to(package_root).as_posix()
    raise EstateError(f"plugin manifest missing: {package_root}")


def installed_plugin_names(output: str) -> set[str]:
    names: set[str] = set()
    pattern = re.compile(r"^\s*[•*-]\s+([^\s]+)\s+\(v[^)]+\)\s*$")
    for line in output.splitlines():
        match = pattern.match(line)
        if match:
            names.add(match.group(1))
    if "Installed plugins:" in output and not names and "(none)" not in output:
        raise EstateError("Copilot plugin inventory could not be parsed")
    return names


def plugin_relative_path(package_root: Path, value: str) -> tuple[Path, str]:
    try:
        resolved_root = package_root.resolve()
        unresolved = resolved_root / value
        relative_parts = unresolved.relative_to(resolved_root).parts
        current = resolved_root
        for part in relative_parts:
            current = current / part
            if current.is_symlink():
                raise EstateError(f"plugin capability is symlinked: {value}")
        candidate = unresolved.resolve()
    except EstateError:
        raise
    except (OSError, ValueError) as error:
        raise EstateError(f"plugin capability path is invalid: {value}") from error
    if resolved_root not in candidate.parents:
        raise EstateError(f"plugin capability escapes package root: {value}")
    relative = candidate.relative_to(resolved_root).as_posix()
    if "#" in relative:
        raise EstateError(f"plugin capability path contains reserved '#': {value}")
    return candidate, f"./{relative}"


def plugin_manifest_paths(value: Any) -> list[str] | None:
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list) and all(
        isinstance(item, str) and item for item in value
    ):
        return value
    return None


def plugin_declared_files(
    package_root: Path,
    values: list[str],
    capability_class: str,
) -> list[str]:
    discovered: list[str] = []
    for value in values:
        path, relative = plugin_relative_path(package_root, value)
        try:
            if not path.exists():
                raise EstateError(f"plugin capability path is missing: {value}")
            if path.is_file():
                if capability_class == "skills":
                    raise EstateError(
                        f"plugin skill capability must be a directory: {value}"
                    )
                discovered.append(relative)
                continue
            if not path.is_dir():
                raise EstateError(f"plugin capability path is unsupported: {value}")

            walked: list[Path] = []

            def walk_error(error: OSError) -> None:
                raise EstateError(
                    f"plugin capability directory is unreadable: {value}"
                ) from error

            for directory, directory_names, file_names in os.walk(
                path, followlinks=False, onerror=walk_error
            ):
                directory_path = Path(directory)
                for name in sorted(directory_names):
                    child = directory_path / name
                    if child.is_symlink():
                        raise EstateError(
                            f"plugin capability is symlinked: {child}"
                        )
                    walked.append(child)
                for name in sorted(file_names):
                    child = directory_path / name
                    if child.is_symlink():
                        raise EstateError(
                            f"plugin capability is symlinked: {child}"
                        )
                    walked.append(child)

            if capability_class == "skills":
                candidates = [path, *(child for child in walked if child.is_dir())]
                for candidate in candidates:
                    skill_file = candidate / "SKILL.md"
                    if skill_file.is_symlink():
                        raise EstateError(
                            f"plugin capability is symlinked: {skill_file}"
                        )
                    if skill_file.is_file():
                        discovered.append(
                            f"./{candidate.relative_to(package_root.resolve()).as_posix()}"
                        )
            else:
                discovered.extend(
                    f"./{child.relative_to(package_root.resolve()).as_posix()}"
                    for child in walked
                    if child.is_file()
                )
        except EstateError:
            raise
        except (OSError, ValueError) as error:
            raise EstateError(f"plugin capability path is invalid: {value}") from error
    return sorted(discovered)


def plugin_server_names(
    value: Any,
    *,
    field: str,
    source: str,
    allow_wrapper: bool,
) -> list[str]:
    if not isinstance(value, dict):
        raise EstateError(f"plugin {field} {source} is malformed")
    if field in value:
        if not allow_wrapper or set(value) != {field}:
            raise EstateError(f"plugin {field} {source} is ambiguous")
        servers = value[field]
    else:
        servers = value
    if not isinstance(servers, dict):
        raise EstateError(f"plugin {field} {source} is malformed")
    names: list[str] = []
    for name, definition in sorted(servers.items()):
        if (
            not isinstance(name, str)
            or not name
            or "#" in name
            or not isinstance(definition, dict)
            or not definition
        ):
            raise EstateError(f"plugin {field} {source} is malformed")
        names.append(name)
    return names


def plugin_named_config_capabilities(
    package_root: Path,
    value: Any,
    field: str,
) -> list[str]:
    if isinstance(value, dict):
        return [
            f"manifest#{name}"
            for name in plugin_server_names(
                value,
                field=field,
                source="metadata",
                allow_wrapper=False,
            )
        ]
    paths = plugin_manifest_paths(value)
    if paths is None:
        raise EstateError(f"plugin {field} metadata is malformed")
    capabilities: list[str] = []
    for raw_path in paths:
        path, relative = plugin_relative_path(package_root, raw_path)
        config = read_json_file(path)
        capabilities.extend(
            f"{relative}#{name}"
            for name in plugin_server_names(
                config,
                field=field,
                source=f"config: {raw_path}",
                allow_wrapper=True,
            )
        )
    return sorted(capabilities)


def plugin_hook_capabilities(package_root: Path, value: Any) -> list[str]:
    paths = plugin_manifest_paths(value)
    if paths is None:
        raise EstateError("plugin hooks metadata is malformed")
    capabilities: list[str] = []
    for raw_path in paths:
        path, relative = plugin_relative_path(package_root, raw_path)
        config = read_json_file(path)
        if not isinstance(config, dict) or not isinstance(config.get("hooks"), dict):
            raise EstateError(f"plugin hooks config is malformed: {raw_path}")
        for event, hooks in sorted(config["hooks"].items()):
            if (
                not isinstance(event, str)
                or not event
                or "#" in event
                or not isinstance(hooks, list)
                or not all(
                    isinstance(hook, dict)
                    and hook
                    and isinstance(hook.get("type"), str)
                    and hook["type"]
                    for hook in hooks
                )
            ):
                raise EstateError(f"plugin hooks config is malformed: {raw_path}")
            capabilities.extend(
                f"{relative}#{event}[{index}]@{digest(hook)}"
                for index, hook in enumerate(hooks)
            )
    return sorted(capabilities)


def plugin_capabilities(
    package_root: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    capabilities: dict[str, list[str]] = {
        capability_class: [] for capability_class in PLUGIN_CAPABILITY_CLASSES
    }
    complete = True
    errors: list[str] = []
    failed: set[str] = set()
    unknown_metadata = sorted(
        set(manifest)
        - PLUGIN_METADATA_FIELDS
        - {"skills", "agents", "hooks", "mcpServers", "lspServers"}
    )
    for field, capability_class in (("skills", "skills"), ("agents", "agents")):
        value = manifest.get(field)
        if value is None:
            continue
        paths = plugin_manifest_paths(value)
        if paths is None:
            complete = False
            failed.add(capability_class)
            errors.append(f"malformed_{field}_metadata")
            continue
        try:
            capabilities[capability_class] = plugin_declared_files(
                package_root, paths, capability_class
            )
        except EstateError as error:
            complete = False
            failed.add(capability_class)
            errors.append(str(error))
    for field, capability_class in (
        ("mcpServers", "mcp_servers"),
        ("lspServers", "lsp_servers"),
    ):
        value = manifest.get(field)
        if value is None:
            continue
        try:
            capabilities[capability_class] = plugin_named_config_capabilities(
                package_root, value, field
            )
        except EstateError as error:
            complete = False
            failed.add(capability_class)
            errors.append(str(error))
    if manifest.get("hooks") is not None:
        try:
            capabilities["hooks"] = plugin_hook_capabilities(
                package_root, manifest["hooks"]
            )
        except EstateError as error:
            complete = False
            failed.add("hooks")
            errors.append(str(error))

    fallback = {
        "skills": package_root / "skills",
        "agents": package_root / "agents",
    }
    for capability_class, path in fallback.items():
        if capability_class in failed or not path.is_dir():
            continue
        try:
            fallback_values = plugin_declared_files(
                package_root, [f"./{path.name}"], capability_class
            )
            capabilities[capability_class].extend(
                item
                for item in fallback_values
                if item not in capabilities[capability_class]
            )
        except EstateError as error:
            complete = False
            errors.append(str(error))
    if (
        "hooks" not in failed
        and (package_root / "hooks/hooks.json").is_file()
    ):
        try:
            fallback_values = plugin_hook_capabilities(
                package_root, ["./hooks/hooks.json"]
            )
            capabilities["hooks"].extend(
                item for item in fallback_values if item not in capabilities["hooks"]
            )
        except EstateError as error:
            complete = False
            errors.append(str(error))
    if (
        "mcp_servers" not in failed
        and (package_root / ".mcp.json").is_file()
    ):
        try:
            fallback_values = plugin_named_config_capabilities(
                package_root, "./.mcp.json", "mcpServers"
            )
            capabilities["mcp_servers"].extend(
                item
                for item in fallback_values
                if item not in capabilities["mcp_servers"]
            )
        except EstateError as error:
            complete = False
            errors.append(str(error))
    if unknown_metadata:
        complete = False
    return {
        "complete": complete,
        "unknown_metadata": unknown_metadata,
        "inventory_errors": sorted(set(errors)),
        **{
            capability_class: sorted(values)
            for capability_class, values in capabilities.items()
        },
    }


def plugin_identity(plugin: dict[str, Any]) -> dict[str, str] | None:
    identity = {
        field: plugin.get(field)
        for field in ("plugin_id", "source_identity", "version")
    }
    if not all(isinstance(value, str) and value for value in identity.values()):
        return None
    return identity


def valid_plugin_capability_identifier(
    capability_class: str, identifier: str
) -> bool:
    if (
        identifier != identifier.strip()
        or any(ord(character) < 32 for character in identifier)
    ):
        return False
    if capability_class in {"skills", "agents"}:
        return re.fullmatch(r"\./[^#]+", identifier) is not None
    if capability_class == "hooks":
        return (
            re.fullmatch(
                r"\./[^#]+#[^#\[\]@]+\[[0-9]+\]@sha256:[0-9a-f]{64}",
                identifier,
            )
            is not None
        )
    return (
        re.fullmatch(r"(?:manifest|\./[^#]+)#[^#]+", identifier)
        is not None
    )


def plugin_capability_ids(capabilities: Any) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    if not isinstance(capabilities, dict):
        return [], ["capability_inventory_malformed"]
    expected_fields = {
        "complete",
        "unknown_metadata",
        "inventory_errors",
        *PLUGIN_CAPABILITY_CLASSES,
    }
    if set(capabilities) != expected_fields:
        reasons.append("capability_inventory_malformed")
    if capabilities.get("complete") is not True:
        reasons.append("capability_inventory_incomplete")
    unknown = capabilities.get("unknown_metadata")
    errors = capabilities.get("inventory_errors")
    if not isinstance(unknown, list) or not all(
        isinstance(item, str) and item for item in unknown
    ):
        reasons.append("unknown_capability_metadata_malformed")
    elif unknown:
        reasons.append("unknown_capability_metadata")
    if not isinstance(errors, list) or not all(
        isinstance(item, str) and item for item in errors
    ):
        reasons.append("capability_inventory_errors_malformed")
    elif errors:
        reasons.append("capability_inventory_errors")
    capability_ids: list[str] = []
    for capability_class in PLUGIN_CAPABILITY_CLASSES:
        values = capabilities.get(capability_class)
        if not isinstance(values, list) or not all(
            isinstance(item, str) and item for item in values
        ):
            reasons.append(f"{capability_class}_inventory_malformed")
            continue
        if not all(
            valid_plugin_capability_identifier(capability_class, item)
            for item in values
        ):
            reasons.append(f"{capability_class}_identifier_malformed")
        if len(values) != len(set(values)):
            reasons.append(f"{capability_class}_inventory_duplicated")
        capability_ids.extend(
            f"{capability_class}:{item}" for item in sorted(set(values))
        )
    if not capability_ids:
        reasons.append("empty_capability_inventory")
    return sorted(capability_ids), reasons


def current_census_plugin(
    value: Any, target_plugin: dict[str, Any], authority: Any
) -> tuple[dict[str, Any] | None, str, list[str]]:
    reasons: list[str] = []
    if not isinstance(authority, dict) or set(authority) != {
        "current_receipt_sha256",
        "expected_census_host_id",
        "expected_receiver",
    }:
        reasons.append("current_census_authority_malformed")
        authority = {}
    expected_receiver = authority.get("expected_receiver")
    if (
        not isinstance(expected_receiver, dict)
        or set(expected_receiver)
        != {"collector_sha256", "receiver_id", "receiver_sha256"}
        or not isinstance(expected_receiver.get("receiver_id"), str)
        or not expected_receiver["receiver_id"]
        or not HEX_SHA256_RE.fullmatch(
            str(expected_receiver.get("receiver_sha256"))
        )
        or not HEX_SHA256_RE.fullmatch(
            str(expected_receiver.get("collector_sha256"))
        )
    ):
        reasons.append("current_census_authority_malformed")
        expected_receiver = {}
    if not SHA256_ID_RE.fullmatch(
        str(authority.get("current_receipt_sha256"))
    ):
        reasons.append("current_census_authority_malformed")
    if (
        not isinstance(authority.get("expected_census_host_id"), str)
        or not authority.get("expected_census_host_id")
    ):
        reasons.append("current_census_authority_malformed")

    if not isinstance(value, dict) or set(value) != {
        "receipt_sha256",
        "receipt",
    }:
        return None, "", ["current_census_receipt_malformed"]
    receipt = value.get("receipt")
    receipt_sha256 = value.get("receipt_sha256")
    if (
        not isinstance(receipt, dict)
        or set(receipt) != {
            "schema_version",
            "snapshot_sha256",
            "receiver",
            "census",
        }
        or receipt.get("schema_version") != 1
        or not SHA256_ID_RE.fullmatch(str(receipt_sha256))
        or digest(receipt) != receipt_sha256
    ):
        return None, "", ["current_census_receipt_malformed"]
    if authority.get("current_receipt_sha256") != receipt_sha256:
        reasons.append("current_census_receipt_not_authoritative")
    census = receipt.get("census")
    snapshot_sha256 = receipt.get("snapshot_sha256")
    if not isinstance(census, dict):
        return None, "", ["current_census_malformed"]
    snapshot = {
        key: item for key, item in census.items() if key != "snapshot_sha256"
    }
    if (
        census.get("schema_version") != 1
        or set(census)
        != {
            "schema_version",
            "host_id",
            "collected_at",
            "scope",
            "totals",
            "authority_counts",
            "root_class_counts",
            "contexts",
            "physical_instances",
            "enabled_instances",
            "unresolved_mappings",
            "evidence",
            "plugins",
            "snapshot_sha256",
        }
        or not SHA256_ID_RE.fullmatch(str(snapshot_sha256))
        or census.get("snapshot_sha256") != snapshot_sha256
        or digest(snapshot) != snapshot_sha256
    ):
        reasons.append("current_census_malformed")
    if census.get("host_id") != authority.get("expected_census_host_id"):
        reasons.append("current_census_host_mismatch")
    scope = census.get("scope")
    contexts = census.get("contexts")
    if (
        not isinstance(scope, dict)
        or set(scope)
        != {
            "label",
            "complete",
            "registered_context_ids",
            "outside_context_ids",
        }
        or not isinstance(scope.get("registered_context_ids"), list)
        or not all(
            isinstance(item, str) and item
            for item in scope.get("registered_context_ids", [])
        )
        or not isinstance(scope.get("outside_context_ids"), list)
        or not isinstance(contexts, list)
        or not all(
            isinstance(context, dict)
            and isinstance(context.get("id"), str)
            and context.get("complete") is True
            and context.get("unresolved_count") == 0
            for context in contexts
        )
        or sorted(scope.get("registered_context_ids", []))
        != sorted(context["id"] for context in contexts)
        or scope.get("outside_context_ids")
    ):
        reasons.append("current_census_malformed")
    elif scope.get("complete") is not True:
        reasons.append("current_census_incomplete")
    receiver = receipt.get("receiver")
    if (
        not isinstance(receiver, dict)
        or set(receiver)
        != {"collector_sha256", "receiver_id", "receiver_sha256"}
        or not isinstance(receiver.get("receiver_id"), str)
        or not receiver["receiver_id"]
        or not HEX_SHA256_RE.fullmatch(str(receiver.get("receiver_sha256")))
        or not HEX_SHA256_RE.fullmatch(str(receiver.get("collector_sha256")))
    ):
        reasons.append("current_census_receiver_malformed")
    elif receiver != expected_receiver:
        reasons.append("current_census_receiver_mismatch")
    target_identity = plugin_identity(target_plugin)
    plugins = census.get("plugins")
    if target_identity is None or not isinstance(plugins, list):
        reasons.append("current_census_plugins_malformed")
        return None, str(snapshot_sha256 or ""), reasons
    identities = [
        plugin_identity(candidate)
        for candidate in plugins
        if isinstance(candidate, dict)
    ]
    if (
        len(identities) != len(plugins)
        or any(identity is None for identity in identities)
        or len({digest(identity) for identity in identities})
        != len(identities)
    ):
        reasons.append("current_census_plugins_malformed")
    plugin_ids = [
        identity["plugin_id"]
        for identity in identities
        if identity is not None
    ]
    if len(plugin_ids) != len(set(plugin_ids)):
        reasons.append("current_census_plugin_settings_key_duplicated")
    matches = [
        plugin
        for plugin in plugins
        if isinstance(plugin, dict) and plugin_identity(plugin) == target_identity
    ]
    if len(matches) != 1:
        reasons.append("current_census_plugin_unresolved")
        return None, str(snapshot_sha256 or ""), reasons
    if matches[0] != target_plugin:
        reasons.append("current_census_plugin_mismatch")
    return matches[0], str(snapshot_sha256 or ""), reasons


def passing_plugin_decision_receipt(
    value: Any,
    *,
    kind: str,
    identity_sha256: str,
    current_estate_sha256: str,
    proposed_estate_sha256: str,
    removed_capability_ids: list[str],
) -> bool:
    if not isinstance(value, dict) or set(value) != {"status", "payload", "sha256"}:
        return False
    payload = value.get("payload")
    expected = {
        "kind": kind,
        "plugin_identity_sha256": identity_sha256,
        "current_estate_sha256": current_estate_sha256,
        "proposed_estate_sha256": proposed_estate_sha256,
        "removed_capability_ids": removed_capability_ids,
        "result": "passed",
    }
    return (
        value.get("status") == "passed"
        and payload == expected
        and value.get("sha256") == digest(expected)
    )


def evaluate_plugin_capability_gate(
    plugin: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    reasons: list[str] = []
    if not isinstance(evidence, dict) or set(evidence) != {
        "authority",
        "current_census_receipt",
        "capability_evaluations",
        "dependency_inventory",
        "proposed_estate",
    }:
        reasons.append("plugin_evidence_malformed")
        evidence = evidence if isinstance(evidence, dict) else {}
    current_plugin, census_sha256, census_reasons = current_census_plugin(
        evidence.get("current_census_receipt"),
        plugin,
        evidence.get("authority"),
    )
    reasons.extend(census_reasons)
    authoritative_plugin = current_plugin if current_plugin is not None else plugin
    identity = plugin_identity(authoritative_plugin)
    if identity is None:
        identity = {}
        reasons.append("plugin_identity_incomplete")
    identity_sha256 = digest(identity)
    if authoritative_plugin.get("enabled") is not True:
        reasons.append("plugin_not_enabled")
    capability_ids, inventory_reasons = plugin_capability_ids(
        authoritative_plugin.get("capabilities")
    )
    reasons.extend(inventory_reasons)
    evaluated_inventory_sha256 = digest(
        authoritative_plugin.get("capabilities")
    )
    capability_inventory_sha256 = (
        evaluated_inventory_sha256 if current_plugin is not None else None
    )

    evaluations = evidence.get("capability_evaluations")
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(evaluations, list):
        reasons.append("capability_evaluations_malformed")
        evaluations = []
    for evaluation in evaluations:
        if not isinstance(evaluation, dict) or set(evaluation) != {
            "capability_id",
            "disposition",
            "evidence_complete",
            "plugin_identity_sha256",
            "capability_inventory_sha256",
            "current_estate_sha256",
        }:
            reasons.append("capability_evaluation_malformed")
            continue
        capability_id = evaluation["capability_id"]
        disposition = evaluation["disposition"]
        if not isinstance(capability_id, str) or not capability_id:
            reasons.append("capability_evaluation_identity_malformed")
            continue
        if capability_id in seen:
            reasons.append("capability_evaluation_duplicated")
            continue
        seen.add(capability_id)
        if capability_id not in capability_ids:
            reasons.append("capability_evaluation_unknown")
            continue
        binding_fields = (
            "plugin_identity_sha256",
            "capability_inventory_sha256",
            "current_estate_sha256",
        )
        if not all(
            isinstance(evaluation[field], str)
            and SHA256_ID_RE.fullmatch(evaluation[field])
            for field in binding_fields
        ):
            reasons.append("capability_evaluation_binding_malformed")
            continue
        capability_class = capability_id.split(":", 1)[0]
        allowed = (
            PLUGIN_SKILL_DISABLE_STATES
            if capability_class == "skills"
            else PLUGIN_NON_SKILL_DISABLE_STATES
        )
        if evaluation["evidence_complete"] is not True:
            reasons.append("capability_evidence_incomplete")
        if evaluation["plugin_identity_sha256"] != identity_sha256:
            reasons.append("capability_evidence_plugin_mismatch")
        if (
            evaluation["capability_inventory_sha256"]
            != evaluated_inventory_sha256
        ):
            reasons.append("capability_evidence_inventory_mismatch")
        if evaluation["current_estate_sha256"] != census_sha256:
            reasons.append("capability_evidence_census_mismatch")
        if not isinstance(disposition, str) or disposition not in allowed:
            reasons.append("capability_retained_or_unknown")
        accepted.append(dict(evaluation))
    if seen != set(capability_ids):
        reasons.append("capability_evaluations_incomplete")

    dependency_evidence = evidence.get("dependency_inventory")
    dependencies = dependency_evidence
    expected_dependency_fields = {
        "complete",
        "plugin_identity_sha256",
        "current_estate_sha256",
        *PLUGIN_DEPENDENCY_CLASSES,
    }
    if (
        not isinstance(dependencies, dict)
        or set(dependencies) != expected_dependency_fields
    ):
        reasons.append("dependency_inventory_malformed")
        dependencies = {}
    else:
        for field in PLUGIN_DEPENDENCY_CLASSES:
            if not isinstance(dependencies[field], list) or not all(
                isinstance(item, str) and item for item in dependencies[field]
            ):
                reasons.append(f"dependency_{field}_malformed")
        if dependencies["complete"] is not True:
            reasons.append("dependency_inventory_incomplete")
        if dependencies["plugin_identity_sha256"] != identity_sha256:
            reasons.append("dependency_inventory_plugin_mismatch")
        if dependencies["current_estate_sha256"] != census_sha256:
            reasons.append("dependency_inventory_census_mismatch")
        if any(
            isinstance(dependencies[field], list) and dependencies[field]
            for field in PLUGIN_DEPENDENCY_CLASSES
        ):
            reasons.append("plugin_has_dependencies")

    proposed = evidence.get("proposed_estate")
    current_estate_sha256 = ""
    proposed_estate_sha256 = ""
    if not isinstance(proposed, dict) or set(proposed) != {
        "complete",
        "current_estate_sha256",
        "proposed_estate_sha256",
        "removed_capability_ids",
        "plugin_identity_sha256",
        "capability_inventory_sha256",
        "snapshot",
        "routing",
        "portfolio",
    }:
        reasons.append("proposed_estate_malformed")
        proposed = {}
    else:
        current_estate_value = proposed["current_estate_sha256"]
        proposed_estate_value = proposed["proposed_estate_sha256"]
        if proposed["complete"] is not True:
            reasons.append("proposed_estate_incomplete")
        if (
            not isinstance(current_estate_value, str)
            or not SHA256_ID_RE.fullmatch(current_estate_value)
        ):
            reasons.append("current_estate_identity_malformed")
        else:
            current_estate_sha256 = current_estate_value
        if current_estate_sha256 and current_estate_sha256 != census_sha256:
            reasons.append("current_estate_census_mismatch")
        if (
            not isinstance(proposed_estate_value, str)
            or not SHA256_ID_RE.fullmatch(proposed_estate_value)
        ):
            reasons.append("proposed_estate_identity_malformed")
        else:
            proposed_estate_sha256 = proposed_estate_value
        if (
            proposed_estate_sha256
            and digest(proposed["snapshot"]) != proposed_estate_sha256
        ):
            reasons.append("proposed_estate_preimage_mismatch")
        if proposed["plugin_identity_sha256"] != identity_sha256:
            reasons.append("proposed_estate_plugin_mismatch")
        if (
            proposed["capability_inventory_sha256"]
            != evaluated_inventory_sha256
        ):
            reasons.append("proposed_estate_inventory_mismatch")
        expected_snapshot = {
            "schema_version": 1,
            "current_estate_sha256": current_estate_sha256,
            "disabled_plugin_identity_sha256": identity_sha256,
            "removed_capability_ids": capability_ids,
        }
        if proposed["snapshot"] != expected_snapshot:
            reasons.append("proposed_estate_snapshot_malformed")
        if proposed["removed_capability_ids"] != capability_ids:
            reasons.append("proposed_estate_removal_mismatch")
        for kind in ("routing", "portfolio"):
            if not passing_plugin_decision_receipt(
                proposed[kind],
                kind=kind,
                identity_sha256=identity_sha256,
                current_estate_sha256=current_estate_sha256,
                proposed_estate_sha256=proposed_estate_sha256,
                removed_capability_ids=capability_ids,
            ):
                reasons.append(f"proposed_estate_{kind}_failed")

    blocking_reasons = sorted(set(reasons))
    return {
        "schema_version": 1,
        "plugin_identity": identity,
        "plugin_identity_sha256": identity_sha256,
        "capability_inventory_sha256": capability_inventory_sha256,
        "capability_ids": capability_ids,
        "capability_evaluations": sorted(
            accepted, key=lambda item: item["capability_id"]
        ),
        "dependency_inventory_sha256": digest(dependency_evidence),
        "current_estate_sha256": current_estate_sha256 or None,
        "proposed_estate_sha256": proposed_estate_sha256 or None,
        "eligible_for_disablement": not blocking_reasons,
        "decision": "disable_eligible" if not blocking_reasons else "keep",
        "blocking_reasons": blocking_reasons,
    }


def discover_plugin_roots(
    installed_root: Path,
    runtime_skills: list[dict[str, Any]],
    enabled_plugin_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    roots: list[dict[str, Any]] = []
    plugins: list[dict[str, Any]] = []
    if not installed_root.exists():
        return roots, plugins
    if installed_root.is_symlink() or not installed_root.is_dir():
        raise EstateError(f"unsafe installed plugin root: {installed_root}")
    runtime_paths = [
        Path(row["path"]).expanduser().resolve()
        for row in runtime_skills
        if row.get("source") == "plugin" and isinstance(row.get("path"), str)
    ]
    try:
        marketplaces = sorted(installed_root.iterdir())
    except OSError as error:
        raise EstateError(
            f"cannot enumerate installed plugin root: {installed_root}"
        ) from error
    for marketplace in marketplaces:
        if marketplace.name.startswith("."):
            continue
        try:
            if not marketplace.is_dir():
                raise EstateError(
                    f"unsafe plugin marketplace directory: {marketplace}"
                )
            packages = sorted(marketplace.iterdir())
        except EstateError:
            raise
        except OSError as error:
            raise EstateError(
                f"cannot enumerate plugin marketplace: {marketplace}"
            ) from error
        for package_root in packages:
            try:
                if package_root.is_symlink() or not package_root.is_dir():
                    raise EstateError(
                        f"unsafe installed plugin package: {package_root}"
                    )
            except EstateError:
                raise
            except OSError as error:
                raise EstateError(
                    f"cannot inspect installed plugin package: {package_root}"
                ) from error
            try:
                resolved_package_root = package_root.resolve()
                manifest, manifest_path = plugin_manifest(resolved_package_root)
                name = manifest.get("name")
                version = manifest.get("version")
                if not isinstance(name, str) or not name:
                    raise EstateError(f"plugin name missing: {package_root}")
                if not isinstance(version, str) or not version:
                    raise EstateError(f"plugin version missing: {package_root}")
                source_identity = (
                    f"installed:{marketplace.name}/{package_root.name}"
                )
                plugin_id = (
                    name
                    if "@" in name
                    else f"{name}@{marketplace.name}"
                )
                capabilities = plugin_capabilities(resolved_package_root, manifest)
                runtime_enabled = any(
                    path == resolved_package_root
                    or resolved_package_root in path.parents
                    for path in runtime_paths
                )
                cli_enabled = (
                    plugin_id in enabled_plugin_names
                    or name in enabled_plugin_names
                )
                plugins.append(
                    {
                        "plugin_id": plugin_id,
                        "name": name,
                        "version": version,
                        "source_identity": source_identity,
                        "package_root": str(resolved_package_root),
                        "manifest_path": manifest_path,
                        "enabled": runtime_enabled or cli_enabled,
                        "capabilities": capabilities,
                    }
                )
                skills_root = resolved_package_root / "skills"
                if skills_root.is_dir():
                    roots.append(
                        {
                            "id": f"plugin:{source_identity}",
                            "class": "plugin",
                            "path": str(skills_root.resolve()),
                            "authority": "plugin_managed",
                            "discovery_surface": "installed-plugin",
                            "plugin_id": plugin_id,
                            "source_identity": source_identity,
                            "version": version,
                            "owner": plugin_id,
                            "package": {
                                "plugin_id": plugin_id,
                                "source_identity": source_identity,
                                "version": version,
                            },
                        }
                    )
            except (EstateError, OSError, ValueError) as error:
                plugins.append(
                    {
                        "plugin_id": None,
                        "source_identity": (
                            f"installed:{marketplace.name}/{package_root.name}"
                        ),
                        "package_root": str(package_root),
                        "enabled": any(
                            path == package_root or package_root in path.parents
                            for path in runtime_paths
                        ),
                        "capabilities": {"complete": False},
                        "error": str(error) or error.__class__.__name__,
                    }
                )
    return roots, plugins


def publisher_root(
    path: Path, settings_root: Path
) -> dict[str, Any]:
    manifest_path = path / "dreaming-bundle-manifest.json"
    if manifest_path.is_file():
        manifest = read_json_file(manifest_path)
        bundle_id = manifest.get("bundle_id") if isinstance(manifest, dict) else None
        if not isinstance(bundle_id, str) or not bundle_id.startswith("sha256:"):
            raise EstateError(f"publisher manifest has no bundle ID: {manifest_path}")
        return {
            "id": f"publisher:{bundle_id}",
            "class": "dreaming_publisher",
            "path": str(path),
            "authority": "dreaming_managed",
            "discovery_surface": "configured-skill-directory",
            "bundle_id": bundle_id,
            "owner": "dreaming",
        }
    return {
        "id": f"custom:{digest(str(path))}",
        "class": "custom",
        "path": str(path),
        "authority": "unknown_provenance",
        "discovery_surface": "configured-skill-directory",
        "owner": str(settings_root),
    }


def discover_roots(
    config: dict[str, Any],
    settings: dict[str, Any],
    contexts: list[dict[str, Any]],
    version: str,
    enabled_plugin_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    home = Path(config.get("target_home", Path.home())).expanduser().resolve()
    installed_root = Path(
        config.get("installed_plugins_root", home / ".copilot/installed-plugins")
    ).expanduser().resolve()
    runtime_skills = [
        row
        for context in contexts
        for row in context["runtime_skills"]
        if isinstance(row, dict)
    ]
    roots: list[dict[str, Any]] = [
        {
            "id": "personal-copilot",
            "class": "personal",
            "path": str(home / ".copilot/skills"),
            "authority": "unknown_provenance",
            "discovery_surface": "personal-copilot",
            "owner": "personal",
            "provenance_policy": config.get("provenance_policy"),
            "legacy_proofs": config.get("legacy_proofs", {}),
        }
    ]
    plugin_roots, plugins = discover_plugin_roots(
        installed_root, runtime_skills, enabled_plugin_names
    )
    roots.extend(plugin_roots)

    builtin_roots = {
        str(Path(row["path"]).expanduser().resolve().parent)
        for row in runtime_skills
        if row.get("source") == "builtin" and isinstance(row.get("path"), str)
    }
    for path in sorted(builtin_roots):
        roots.append(
            {
                "id": f"builtin:{version}:{path}",
                "class": "builtin",
                "path": path,
                "authority": "cli_builtin",
                "discovery_surface": "copilot-builtin",
                "copilot_version": version,
                "owner": "copilot-cli",
            }
        )

    configured_directories = settings.get("skillDirectories", [])
    if not isinstance(configured_directories, list) or not all(
        isinstance(value, str) and value for value in configured_directories
    ):
        raise EstateError("settings skillDirectories must be a string list")
    configured_paths = {
        Path(value).expanduser().resolve() for value in configured_directories
    }
    publisher_parents = {
        path.parent
        for path in configured_paths
        if path.parent.name == "remote-publisher-bundles"
    }
    for parent in publisher_parents:
        if parent.is_dir():
            configured_paths.update(
                path.resolve()
                for path in parent.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            )
    settings_path = Path(
        config.get("settings_path", home / ".copilot/settings.json")
    ).expanduser().resolve()
    roots.extend(
        publisher_root(path, settings_path)
        for path in sorted(configured_paths)
    )

    for project in config.get("project_contexts", []):
        project_path = Path(project["path"]).expanduser().resolve()
        identity = git_identity(project_path)
        skill_roots = project.get("skill_roots", [])
        if not isinstance(skill_roots, list) or not all(
            isinstance(value, str) and value for value in skill_roots
        ):
            raise EstateError("project skill_roots must be a string list")
        for value in skill_roots:
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = project_path / path
            path = path.resolve()
            roots.append(
                {
                    "id": f"project:{identity['repository_identity']}:{path}",
                    "class": "project",
                    "path": str(path),
                    "authority": "unknown_provenance",
                    "discovery_surface": "registered-project",
                    **identity,
                    "owner": identity["repository_identity"],
                }
            )

    explicit = config.get("roots", [])
    if not isinstance(explicit, list):
        raise EstateError("roots must be a list")
    roots.extend(explicit)
    deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
    for root in roots:
        key = (root["class"], str(Path(root["path"]).expanduser().resolve()))
        if key in deduplicated:
            raise EstateError(f"duplicate declared root: {key[1]}")
        deduplicated[key] = root
    return list(deduplicated.values()), plugins


def collect_durable_dependency_inventory(config: dict[str, Any]) -> dict[str, Any]:
    inline = config.get("durable_dependency_inventory")
    if inline is not None:
        if not isinstance(inline, dict):
            return {
                "complete": False,
                "dependencies": [],
                "skills": [],
                "error": "configured durable dependency inventory is not an object",
            }
        return inline
    scanner = Path(
        config.get(
            "dependency_scanner",
            Path(__file__).resolve().parents[2]
            / "skill-curator/scripts/scheduled-skill-deps.py",
        )
    ).expanduser().resolve()
    if not scanner.is_file():
        return {
            "complete": False,
            "dependencies": [],
            "skills": [],
            "error": f"durable dependency scanner is unavailable: {scanner}",
        }
    try:
        completed = subprocess.run(
            [str(scanner), "--inventory"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {
            "complete": False,
            "dependencies": [],
            "skills": [],
            "error": f"durable dependency scan failed: {error}",
        }
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit {completed.returncode}"
        return {
            "complete": False,
            "dependencies": [],
            "skills": [],
            "error": f"durable dependency scan refused: {detail}",
        }
    try:
        inventory = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        return {
            "complete": False,
            "dependencies": [],
            "skills": [],
            "error": f"durable dependency scan returned invalid JSON: {error}",
        }
    if not isinstance(inventory, dict):
        return {
            "complete": False,
            "dependencies": [],
            "skills": [],
            "error": "durable dependency scan returned a non-object",
        }
    return inventory


def collect(config: dict[str, Any]) -> dict[str, Any]:
    host_id = config.get("host_id")
    binary = config.get("copilot_binary", "copilot")
    project_contexts = config.get("project_contexts", [])
    user_cwd = Path(config.get("user_context_cwd", Path.home())).expanduser().resolve()
    if not isinstance(host_id, str):
        raise EstateError("config requires host_id")
    if not isinstance(project_contexts, list):
        raise EstateError("project_contexts must be a list")
    version = copilot_version(binary)
    contexts = [
        {
            "id": "user",
            "kind": "user",
            "registered": True,
            "cwd": str(user_cwd),
            "runtime_skills": run_json(
                [binary, "skill", "list", "--json"], user_cwd
            ),
        }
    ]
    for project in project_contexts:
        if not isinstance(project, dict) or not isinstance(project.get("path"), str):
            raise EstateError("project context requires a path")
        path = Path(project["path"]).expanduser().resolve()
        identity = git_identity(path)
        contexts.append(
            {
                "id": project.get("id") or digest(identity),
                "kind": "project",
                "registered": project.get("registered", True),
                "cwd": str(path),
                **identity,
                "runtime_skills": run_json(
                    [binary, "skill", "list", "--json"], path
                ),
            }
        )
    target_home = Path(config.get("target_home", Path.home())).expanduser().resolve()
    settings = Path(
        config.get("settings_path", target_home / ".copilot/settings.json")
    ).expanduser().resolve()
    try:
        settings_value = read_json_file(settings)
        if not isinstance(settings_value, dict):
            raise EstateError(f"settings must be an object: {settings}")
        settings_sha256 = file_sha256(settings)
    except OSError as error:
        raise EstateError(f"cannot read settings: {settings}") from error
    enabled_plugin_names = installed_plugin_names(
        run_text([binary, "plugin", "list"], user_cwd)
    )
    roots, plugins = discover_roots(
        config,
        settings_value,
        contexts,
        version,
        enabled_plugin_names,
    )
    durable_dependency_inventory = collect_durable_dependency_inventory(config)
    census = reconcile(
        host_id=host_id,
        roots=roots,
        contexts=contexts,
        collected_at=datetime.now(timezone.utc).isoformat(),
        evidence={
            "copilot_version": version,
            "settings_path": str(settings),
            "settings_sha256": settings_sha256,
        },
        durable_dependency_inventory=durable_dependency_inventory,
    )
    census["plugins"] = plugins
    census["totals"]["plugin_packages"] = len(plugins)
    census["totals"]["enabled_plugin_packages"] = sum(
        plugin.get("enabled") is True for plugin in plugins
    )
    snapshot = {
        key: value for key, value in census.items() if key != "snapshot_sha256"
    }
    census["snapshot_sha256"] = digest(snapshot)
    return census


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EstateError(f"invalid JSON: {path}") from error
    if not isinstance(value, dict):
        raise EstateError(f"JSON root must be an object: {path}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    fixture = subcommands.add_parser("reconcile")
    fixture.add_argument("--input", required=True)
    collect_parser = subcommands.add_parser("collect")
    collect_parser.add_argument("--config", required=True)
    plugin_parser = subcommands.add_parser("evaluate-plugin")
    plugin_parser.add_argument("--input", required=True)
    args = parser.parse_args()
    try:
        if args.command == "collect":
            result = collect(load_object(Path(args.config).expanduser().resolve()))
        elif args.command == "evaluate-plugin":
            request = load_object(Path(args.input).expanduser().resolve())
            if (
                set(request) != {"plugin", "evidence"}
                or not isinstance(request["plugin"], dict)
                or not isinstance(request["evidence"], dict)
            ):
                raise EstateError("plugin evaluation input is malformed")
            result = evaluate_plugin_capability_gate(
                request["plugin"], request["evidence"]
            )
        else:
            source = load_object(Path(args.input).expanduser().resolve())
            result = reconcile(
                host_id=source["host_id"],
                roots=source["roots"],
                contexts=source["contexts"],
                collected_at=source.get("collected_at", "fixture"),
                evidence=source.get("evidence"),
                durable_dependency_inventory=source.get(
                    "durable_dependency_inventory"
                ),
            )
    except (EstateError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        raise SystemExit(2)
    output_key = "evaluation" if args.command == "evaluate-plugin" else "census"
    print(json.dumps({"ok": True, output_key: result}, sort_keys=True))


if __name__ == "__main__":
    main()
