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
        "evidence": evidence or {},
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
    raw: bytes, *, collected_at: datetime
) -> tuple[list[tuple[str, datetime]], datetime | None, list[str]]:
    starts: dict[str, tuple[str, datetime, int]] = {}
    completions: dict[str, tuple[bool, datetime, str | None, int]] = {}
    earliest: datetime | None = None
    issues: list[str] = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise EstateError("usage_session_invalid_utf8") from error
    for event_index, line in enumerate(text.splitlines()):
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
                normalize_skill_name(arguments.get("skill"))
                if isinstance(arguments, dict)
                else None
            )
            if not isinstance(call_id, str) or not call_id:
                raise EstateError("usage_session_invalid_skill_start")
            if name is None:
                issues.append("usage_session_invalid_skill_name")
                continue
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


def collect_usage(
    census: dict[str, Any],
    session_root: Path,
    *,
    collected_at: datetime,
    max_sessions: int,
    max_bytes: int,
) -> dict[str, Any]:
    mappings, canonical_ids, mapping_issues = usage_name_mappings(census)
    counts: dict[str, Counter[str]] = {}
    last_used: dict[str, datetime] = {}
    unattributed: dict[tuple[str, str], Counter[str]] = {}
    failures: list[dict[str, str]] = []
    sessions_scanned = 0
    bytes_scanned = 0
    earliest_retained: datetime | None = None
    complete = True
    bound_reached: str | None = None

    def fail(session: str, reason: str) -> None:
        nonlocal complete
        complete = False
        failures.append({"session_id": opaque_session_id(session), "reason": reason})

    if census.get("scope", {}).get("complete") is not True:
        fail("census", "usage_census_incomplete")
    for capability_id, reason in mapping_issues:
        fail(f"census:{capability_id}", reason)

    try:
        if session_root.is_symlink() or not session_root.is_dir():
            raise OSError("session root is unavailable")
        children = sorted(session_root.iterdir(), key=lambda path: path.name)
    except OSError:
        children = []
        complete = False
        failures.append(
            {"session_id": opaque_session_id("session-root"), "reason": "session_root_unavailable"}
        )

    for session in children:
        if session.is_symlink():
            fail(session.name, "session_symlink")
            continue
        try:
            if not session.is_dir():
                continue
        except OSError:
            fail(session.name, "session_unreadable")
            continue
        events = session / "events.jsonl"
        if events.is_symlink():
            fail(session.name, "events_symlink")
            continue
        try:
            before = events.stat()
        except FileNotFoundError:
            continue
        except OSError:
            fail(session.name, "events_unreadable")
            continue
        if not stat.S_ISREG(before.st_mode):
            fail(session.name, "events_not_regular")
            continue
        if sessions_scanned >= max_sessions:
            complete = False
            bound_reached = "max_sessions"
            break
        if bytes_scanned + before.st_size > max_bytes:
            complete = False
            bound_reached = "max_bytes"
            break
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(events, flags)
            try:
                opened = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_dev != before.st_dev
                    or opened.st_ino != before.st_ino
                    or opened.st_size != before.st_size
                ):
                    raise OSError("events changed before read")
                with os.fdopen(descriptor, "rb", closefd=False) as handle:
                    raw = handle.read(before.st_size + 1)
                after = os.fstat(descriptor)
                if (
                    len(raw) != before.st_size
                    or after.st_size != before.st_size
                    or after.st_mtime_ns != before.st_mtime_ns
                ):
                    raise OSError("events changed during read")
            finally:
                os.close(descriptor)
        except OSError:
            fail(session.name, "events_changed_or_unreadable")
            continue
        sessions_scanned += 1
        bytes_scanned += len(raw)
        try:
            successful, session_earliest, session_issues = parse_usage_session(
                raw, collected_at=collected_at
            )
        except EstateError as error:
            fail(session.name, str(error))
            continue
        for reason in session_issues:
            fail(session.name, reason)
        if session_earliest is not None:
            earliest_retained = (
                session_earliest
                if earliest_retained is None
                else min(earliest_retained, session_earliest)
            )
        session_counts: dict[str, Counter[str]] = {}
        session_last: dict[str, datetime] = {}
        session_unattributed: dict[tuple[str, str], Counter[str]] = {}
        for name, timestamp in successful:
            capability_ids = mappings.get(name, set())
            age_seconds = (collected_at - timestamp).total_seconds()
            windows = Counter(
                {
                    "uses_total": 1,
                    "uses_7d": int(age_seconds <= 7 * 86400),
                    "uses_30d": int(age_seconds <= 30 * 86400),
                    "uses_90d": int(age_seconds <= 90 * 86400),
                }
            )
            if len(capability_ids) == 1:
                capability_id = next(iter(capability_ids))
                session_counts.setdefault(capability_id, Counter()).update(windows)
                session_last[capability_id] = max(
                    timestamp, session_last.get(capability_id, timestamp)
                )
            else:
                reason = "unmapped" if not capability_ids else "conflicting_mapping"
                session_unattributed.setdefault((name, reason), Counter()).update(windows)
        for capability_id, value in session_counts.items():
            counts.setdefault(capability_id, Counter()).update(value)
        for capability_id, value in session_last.items():
            last_used[capability_id] = max(
                value, last_used.get(capability_id, value)
            )
        for key, value in session_unattributed.items():
            unattributed.setdefault(key, Counter()).update(value)

    if unattributed:
        complete = False
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
            "earliest_retained_event": (
                earliest_retained.isoformat() if earliest_retained else None
            ),
            "sessions_scanned": sessions_scanned,
            "bytes_scanned": bytes_scanned,
            "max_sessions": max_sessions,
            "max_bytes": max_bytes,
            "bound_reached": bound_reached,
            "failures": failures,
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
            }
            for (name, reason), value in sorted(unattributed.items())
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
            )
    except (EstateError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        raise SystemExit(2)
    output_key = "evaluation" if args.command == "evaluate-plugin" else "census"
    print(json.dumps({"ok": True, output_key: result}, sort_keys=True))


if __name__ == "__main__":
    main()
