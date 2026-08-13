#!/usr/bin/env python3
"""Build and reconcile a bounded Copilot skill-estate census."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
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
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
LEGACY_POLICY_VERSION = 1
LEGACY_PROOF_VERSION = 1
LEGACY_PROOF_KIND = "legacy_git_creation"


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


def plugin_capabilities(
    package_root: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    capabilities: dict[str, list[str]] = {
        "skills": [],
        "agents": [],
        "hooks": [],
        "mcp_servers": [],
        "lsp_servers": [],
    }
    complete = True
    manifest_fields = {
        "skills": "skills",
        "agents": "agents",
        "hooks": "hooks",
        "mcpServers": "mcp_servers",
        "lspServers": "lsp_servers",
    }
    for field, target in manifest_fields.items():
        value = manifest.get(field)
        if value is None:
            continue
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item for item in value
        ):
            complete = False
            continue
        capabilities[target].extend(value)

    if not capabilities["skills"] and (package_root / "skills").is_dir():
        capabilities["skills"] = [
            f"./skills/{path.name}"
            for path in sorted((package_root / "skills").iterdir())
            if path.is_dir() and (path / "SKILL.md").is_file()
        ]
    if not capabilities["agents"] and (package_root / "agents").is_dir():
        capabilities["agents"] = [
            f"./agents/{path.name}"
            for path in sorted((package_root / "agents").iterdir())
            if path.is_file()
        ]
    if (package_root / "hooks/hooks.json").is_file():
        capabilities["hooks"].append("./hooks/hooks.json")
    if (package_root / ".mcp.json").is_file():
        value = read_json_file(package_root / ".mcp.json")
        if not isinstance(value, dict):
            complete = False
        else:
            servers = value.get("mcpServers", value)
            if not isinstance(servers, dict):
                complete = False
            else:
                capabilities["mcp_servers"].extend(
                    f".mcp.json#{name}" for name in sorted(servers)
                )
    for values in capabilities.values():
        values[:] = sorted(set(values))
    return {
        "complete": complete,
        **capabilities,
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
    for marketplace in sorted(installed_root.iterdir()):
        if marketplace.name.startswith(".") or not marketplace.is_dir():
            continue
        for package_root in sorted(marketplace.iterdir()):
            if package_root.is_symlink() or not package_root.is_dir():
                continue
            try:
                manifest, manifest_path = plugin_manifest(package_root)
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
                capabilities = plugin_capabilities(package_root, manifest)
                runtime_enabled = any(
                    path == package_root or package_root in path.parents
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
                        "package_root": str(package_root.resolve()),
                        "manifest_path": manifest_path,
                        "enabled": runtime_enabled or cli_enabled,
                        "capabilities": capabilities,
                    }
                )
                skills_root = package_root / "skills"
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
            except EstateError as error:
                plugins.append(
                    {
                        "plugin_id": None,
                        "source_identity": (
                            f"installed:{marketplace.name}/{package_root.name}"
                        ),
                        "package_root": str(package_root.resolve()),
                        "enabled": any(
                            path == package_root or package_root in path.parents
                            for path in runtime_paths
                        ),
                        "capabilities": {"complete": False},
                        "error": str(error),
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
    args = parser.parse_args()
    try:
        if args.command == "collect":
            result = collect(load_object(Path(args.config).expanduser().resolve()))
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
    print(json.dumps({"ok": True, "census": result}, sort_keys=True))


if __name__ == "__main__":
    main()
