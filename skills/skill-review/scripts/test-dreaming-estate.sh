#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
# shellcheck source=lib-test-work.sh
source "$SCRIPT_DIR/lib-test-work.sh"
prune_test_work "$TEST_ROOT" "estate-census" 2
export PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/env python3 - "$SCRIPT_DIR" <<'PY'
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(sys.argv[1]).resolve()
REPO = SCRIPT_DIR.parents[2]
WORK_PARENT = REPO / ".test-work"
WORK_ROOT = Path(tempfile.mkdtemp(prefix="estate-census.", dir=WORK_PARENT))
MODULE_PATH = SCRIPT_DIR / "dreaming-estate.py"

spec = importlib.util.spec_from_file_location("dreaming_estate", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class EstateCensusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.case = WORK_ROOT / self.id().rsplit(".", 1)[-1]
        self.case.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.case)

    def skill(self, root: Path, name: str, body: str = "fixture") -> Path:
        skill = root / name
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {body}\n---\n",
            encoding="utf-8",
        )
        return skill

    def assert_blocked(self, result: dict, reason: str) -> None:
        self.assertFalse(result["eligible_for_disablement"])
        self.assertEqual(result["decision"], "keep")
        self.assertIn(reason, result["blocking_reasons"])

    def root(
        self,
        root_id: str,
        root_class: str,
        path: Path,
        authority: str,
        **identity: object,
    ) -> dict:
        return {
            "id": root_id,
            "class": root_class,
            "path": str(path),
            "authority": authority,
            "discovery_surface": root_id,
            **identity,
        }

    def write_envelope(self, skill: Path, *, legacy: bool = False) -> None:
        (skill / ".agent-created").write_text("", encoding="utf-8")
        (skill / "SKILL.md").write_text(
            f"---\nname: {skill.name}\ndescription: Fixture.\nauthor: skill-review\n---\n",
            encoding="utf-8",
        )
        if legacy:
            envelope = {
                "schema_version": 1,
                "skill": skill.name,
                "created_by": "skill-review",
                "source_session_id": "fixture-session",
                "created_at": "2025-01-01T00:00:00+00:00",
            }
        else:
            envelope = {
                "schema_version": 2,
                "skill": skill.name,
                "created_by": "skill-review",
                "source_session_id": "fixture-session",
                "source_mode": "dispatch",
                "review_prompt_version": "skill-review-2",
                "created_at": "2025-01-01T00:00:00+00:00",
                "evidence": [
                    {
                        "task_key": "task:11111111-1111-1111-1111-111111111111",
                        "session_id": "fixture-session",
                        "observed_at": "2025-01-01T00:00:00+00:00",
                        "independence": "verified",
                        "evidence_kind": "successful-procedure",
                        "summary": "Estate authority fixture.",
                    }
                ],
                "routing": {"destination": "skill", "reason": "Fixture."},
                "claims": [],
                "evaluation": {"status": "not_evaluated"},
            }
        (skill / ".agent-created.json").write_text(
            json.dumps(envelope), encoding="utf-8"
        )

    def write_bundle_manifest(self, root: Path) -> str:
        files = []
        for skill in sorted(root.iterdir()):
            if not skill.is_dir():
                continue
            for path in sorted(skill.rglob("*")):
                if path.is_file():
                    files.append(
                        {
                            "path": path.relative_to(root).as_posix(),
                            "sha256": module.file_sha256(path),
                        }
                    )
        proof = {
            "contract_version": 1,
            "files": files,
            "skills_revision": "fixture",
            "orchestration_skills_absent": True,
            "publication_name": "fixture",
        }
        bundle_id = module.digest(proof)
        (root / "dreaming-bundle-manifest.json").write_text(
            json.dumps({**proof, "bundle_id": bundle_id}), encoding="utf-8"
        )
        return bundle_id

    def sealed(self, payload: dict, field: str) -> dict:
        return {**payload, field: module.digest(payload)}

    def plugin_gate_fixture(self) -> dict:
        hook = {"type": "command", "bash": "true"}
        return {
            "plugin_id": "fixture@market",
            "source_identity": "installed:market/fixture",
            "version": "1.0.0",
            "enabled": True,
            "capabilities": {
                "complete": True,
                "unknown_metadata": [],
                "inventory_errors": [],
                "skills": ["./skills/one", "./skills/two"],
                "agents": ["./agents/reviewer.md"],
                "hooks": [
                    f"./hooks/hooks.json#sessionStart[0]@{module.digest(hook)}"
                ],
                "mcp_servers": ["./.mcp.json#fixture"],
                "lsp_servers": ["./.lsp.json#python"],
            },
        }

    def plugin_gate_evidence(self, plugin: dict) -> dict:
        capability_ids, reasons = module.plugin_capability_ids(
            plugin["capabilities"]
        )
        self.assertEqual(reasons, [])
        identity_sha256 = module.digest(module.plugin_identity(plugin))
        inventory_sha256 = module.digest(plugin["capabilities"])
        census_snapshot = {
            "schema_version": 1,
            "host_id": "fixture-macbook",
            "collected_at": "2026-01-01T00:00:00+00:00",
            "scope": {
                "label": "fixture",
                "complete": True,
                "registered_context_ids": ["user"],
                "outside_context_ids": [],
            },
            "totals": {},
            "authority_counts": {},
            "root_class_counts": {},
            "contexts": [
                {
                    "id": "user",
                    "complete": True,
                    "unresolved_count": 0,
                }
            ],
            "physical_instances": [],
            "enabled_instances": [],
            "unresolved_mappings": [],
            "evidence": {},
            "plugins": [plugin],
        }
        current_estate_sha256 = module.digest(census_snapshot)
        census = {
            **census_snapshot,
            "snapshot_sha256": current_estate_sha256,
        }
        census_receipt = {
            "schema_version": 1,
            "snapshot_sha256": current_estate_sha256,
            "receiver": {
                "receiver_id": "fixture-receiver",
                "receiver_sha256": "a" * 64,
                "collector_sha256": "b" * 64,
            },
            "census": census,
        }
        proposed_snapshot = {
            "schema_version": 1,
            "current_estate_sha256": current_estate_sha256,
            "disabled_plugin_identity_sha256": identity_sha256,
            "removed_capability_ids": capability_ids,
        }
        proposed_estate_sha256 = module.digest(proposed_snapshot)

        def receipt(kind: str) -> dict:
            payload = {
                "kind": kind,
                "plugin_identity_sha256": identity_sha256,
                "current_estate_sha256": current_estate_sha256,
                "proposed_estate_sha256": proposed_estate_sha256,
                "removed_capability_ids": capability_ids,
                "result": "passed",
            }
            return {
                "status": "passed",
                "payload": payload,
                "sha256": module.digest(payload),
            }

        return {
            "authority": {
                "current_receipt_sha256": module.digest(census_receipt),
                "expected_census_host_id": "fixture-macbook",
                "expected_receiver": dict(census_receipt["receiver"]),
            },
            "current_census_receipt": {
                "receipt_sha256": module.digest(census_receipt),
                "receipt": census_receipt,
            },
            "capability_evaluations": [
                {
                    "capability_id": capability_id,
                    "disposition": (
                        "redundant"
                        if capability_id.startswith("skills:")
                        else "superseded"
                    ),
                    "evidence_complete": True,
                    "plugin_identity_sha256": identity_sha256,
                    "capability_inventory_sha256": inventory_sha256,
                    "current_estate_sha256": current_estate_sha256,
                }
                for capability_id in capability_ids
            ],
            "dependency_inventory": {
                "complete": True,
                "plugin_identity_sha256": identity_sha256,
                "current_estate_sha256": current_estate_sha256,
                **{
                    field: []
                    for field in module.PLUGIN_DEPENDENCY_CLASSES
                },
            },
            "proposed_estate": {
                "complete": True,
                "current_estate_sha256": current_estate_sha256,
                "proposed_estate_sha256": proposed_estate_sha256,
                "removed_capability_ids": capability_ids,
                "plugin_identity_sha256": identity_sha256,
                "capability_inventory_sha256": inventory_sha256,
                "snapshot": proposed_snapshot,
                "routing": receipt("routing"),
                "portfolio": receipt("portfolio"),
            },
        }

    def reseal_census_receipt(self, evidence: dict) -> str:
        receipt_wrapper = evidence["current_census_receipt"]
        receipt = receipt_wrapper["receipt"]
        census = receipt["census"]
        snapshot = {
            key: value for key, value in census.items() if key != "snapshot_sha256"
        }
        census_sha256 = module.digest(snapshot)
        census["snapshot_sha256"] = census_sha256
        receipt["snapshot_sha256"] = census_sha256
        receipt_sha256 = module.digest(receipt)
        receipt_wrapper["receipt_sha256"] = receipt_sha256
        evidence["authority"]["current_receipt_sha256"] = receipt_sha256
        return census_sha256

    def rebind_plugin_gate_evidence(
        self, plugin: dict, evidence: dict
    ) -> None:
        capability_ids, _ = module.plugin_capability_ids(plugin["capabilities"])
        identity_sha256 = module.digest(module.plugin_identity(plugin))
        inventory_sha256 = module.digest(plugin["capabilities"])
        census_sha256 = self.reseal_census_receipt(evidence)
        for evaluation in evidence["capability_evaluations"]:
            evaluation["plugin_identity_sha256"] = identity_sha256
            evaluation["capability_inventory_sha256"] = inventory_sha256
            evaluation["current_estate_sha256"] = census_sha256
        dependencies = evidence["dependency_inventory"]
        dependencies["plugin_identity_sha256"] = identity_sha256
        dependencies["current_estate_sha256"] = census_sha256
        proposed = evidence["proposed_estate"]
        proposed["current_estate_sha256"] = census_sha256
        proposed["removed_capability_ids"] = capability_ids
        proposed["plugin_identity_sha256"] = identity_sha256
        proposed["capability_inventory_sha256"] = inventory_sha256
        proposed["snapshot"] = {
            "schema_version": 1,
            "current_estate_sha256": census_sha256,
            "disabled_plugin_identity_sha256": identity_sha256,
            "removed_capability_ids": capability_ids,
        }
        proposed["proposed_estate_sha256"] = module.digest(proposed["snapshot"])
        for kind in ("routing", "portfolio"):
            payload = {
                "kind": kind,
                "plugin_identity_sha256": identity_sha256,
                "current_estate_sha256": census_sha256,
                "proposed_estate_sha256": proposed["proposed_estate_sha256"],
                "removed_capability_ids": capability_ids,
                "result": "passed",
            }
            proposed[kind] = {
                "status": "passed",
                "payload": payload,
                "sha256": module.digest(payload),
            }

    def test_chk01_reconciles_physical_and_effective_estate(self) -> None:
        personal = self.case / "personal"
        plugin = self.case / "plugins" / "market" / "package" / "skills"
        disabled = self.case / "plugins" / "old" / "disabled" / "skills"
        builtin = self.case / "builtin"
        active = self.case / "publisher" / "active"
        stale = self.case / "publisher" / "stale"
        project = self.case / "project-skills"

        personal_same = self.skill(personal, "same-name", "personal")
        plugin_same = self.skill(plugin, "same-name", "plugin")
        disabled_skill = self.skill(disabled, "disabled-cache")
        builtin_skill = self.skill(builtin, "builtin-one")
        active_skill = self.skill(active, "learned")
        self.skill(stale, "learned")
        project_skill = self.skill(project, "project-only")
        active_bundle = self.write_bundle_manifest(active)
        stale_bundle = self.write_bundle_manifest(stale)

        roots = [
            self.root(
                "personal",
                "personal",
                personal,
                "unknown_provenance",
            ),
            self.root(
                "plugin-enabled",
                "plugin",
                plugin,
                "plugin_managed",
                plugin_id="package@market",
                source_identity="github:owner/repo",
                version="1.2.3",
                package={
                    "plugin_id": "package@market",
                    "source_identity": "github:owner/repo",
                    "version": "1.2.3",
                },
            ),
            self.root(
                "plugin-disabled-cache",
                "plugin",
                disabled,
                "plugin_managed",
                plugin_id="disabled@old",
                source_identity="github:owner/old",
                version="0.1.0",
                package={
                    "plugin_id": "disabled@old",
                    "source_identity": "github:owner/old",
                    "version": "0.1.0",
                },
            ),
            self.root(
                "builtin",
                "builtin",
                builtin,
                "cli_builtin",
                copilot_version="1.0.79",
            ),
            self.root(
                "publisher-active",
                "dreaming_publisher",
                active,
                "dreaming_managed",
                bundle_id=active_bundle,
            ),
            self.root(
                "publisher-stale",
                "dreaming_publisher",
                stale,
                "dreaming_managed",
                bundle_id=stale_bundle,
            ),
            self.root(
                "project",
                "project",
                project,
                "unknown_provenance",
                repository_identity="fixture/project",
            ),
        ]
        contexts = [
            {
                "id": "user",
                "kind": "user",
                "registered": True,
                "runtime_skills": [
                    {
                        "name": "same-name",
                        "source": "personal-copilot",
                        "path": str(personal_same),
                        "enabled": True,
                    },
                    {
                        "name": "same-name",
                        "source": "plugin",
                        "path": str(plugin_same),
                        "enabled": True,
                    },
                    {
                        "name": "builtin-one",
                        "source": "builtin",
                        "path": str(builtin_skill),
                        "enabled": True,
                    },
                    {
                        "name": "learned",
                        "source": "custom",
                        "path": str(active_skill),
                        "enabled": True,
                    },
                ],
            },
            {
                "id": "registered-project",
                "kind": "project",
                "registered": True,
                "runtime_skills": [
                    {
                        "name": "project-only",
                        "source": "project",
                        "path": str(project_skill),
                        "enabled": True,
                    },
                    {
                        "name": "unmapped",
                        "source": "project",
                        "path": str(self.case / "unmapped" / "unmapped"),
                        "enabled": True,
                    },
                ],
            },
            {
                "id": "unregistered-project",
                "kind": "project",
                "registered": False,
                "runtime_skills": [
                    {
                        "name": "outside",
                        "source": "project",
                        "path": str(self.case / "outside" / "outside"),
                        "enabled": True,
                    }
                ],
            },
        ]

        census = module.reconcile(
            host_id="macbook",
            roots=roots,
            contexts=contexts,
            collected_at="2026-08-13T00:00:00+00:00",
        )

        self.assertEqual(census["totals"]["physical_instances"], 7)
        self.assertEqual(census["totals"]["effective_instances"], 5)
        self.assertEqual(census["totals"]["canonical_capabilities"], 5)
        self.assertEqual(census["totals"]["physical_only_instances"], 2)
        self.assertEqual(census["totals"]["unresolved_runtime_skills"], 1)
        self.assertEqual(
            census["authority_counts"],
            {
                "cli_builtin": 1,
                "dreaming_managed": 2,
                "legacy_machine": 0,
                "plugin_managed": 2,
                "unknown_provenance": 2,
                "user_protected": 0,
            },
        )
        self.assertFalse(census["scope"]["complete"])
        self.assertEqual(
            census["scope"]["outside_context_ids"], ["unregistered-project"]
        )
        self.assertEqual(
            census["unresolved_mappings"][0]["reason"], "unmapped"
        )
        same_name_ids = {
            row["canonical_capability_id"]
            for row in census["enabled_instances"]
            if row["runtime_name"] == "same-name"
        }
        self.assertEqual(len(same_name_ids), 2)
        disabled_instance = next(
            row
            for row in census["physical_instances"]
            if row["absolute_path"] == str(disabled_skill)
        )
        self.assertTrue(disabled_instance["physical_only"])
        builtin_instance = next(
            row
            for row in census["physical_instances"]
            if row["absolute_path"] == str(builtin_skill)
        )
        self.assertEqual(builtin_instance["authority"], "cli_builtin")

    def test_chk02_classifies_the_full_provenance_authority_matrix(self) -> None:
        personal = self.case / "personal"
        personal.mkdir()
        subprocess.run(["git", "-C", str(personal), "init", "-q"], check=True)
        subprocess.run(
            ["git", "-C", str(personal), "config", "user.name", "Dreaming Machine"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(personal),
                "config",
                "user.email",
                "dreaming-machine@example.invalid",
            ],
            check=True,
        )

        current = self.skill(personal, "current-envelope")
        self.write_envelope(current)
        legacy_envelope = self.skill(personal, "legacy-envelope")
        self.write_envelope(legacy_envelope, legacy=True)
        legacy_proof = self.skill(personal, "legacy-proof")
        (legacy_proof / ".agent-created").write_text("", encoding="utf-8")
        user_owned = self.skill(personal, "user-owned")
        (user_owned / ".pinned").write_text("", encoding="utf-8")
        malformed = self.skill(personal, "malformed-envelope")
        (malformed / ".agent-created").write_text("", encoding="utf-8")
        (malformed / ".agent-created.json").write_text("{", encoding="utf-8")
        conflict = self.skill(personal, "conflicting-evidence")
        self.write_envelope(conflict)
        marker_only = self.skill(personal, "marker-only")
        (marker_only / ".agent-created").write_text("", encoding="utf-8")
        invalid_proof = self.skill(personal, "invalid-proof")
        (invalid_proof / ".agent-created").write_text("", encoding="utf-8")
        wrong_digest = self.skill(personal, "wrong-digest")
        (wrong_digest / ".agent-created").write_text("", encoding="utf-8")
        unsupported = self.skill(personal, "unsupported-version")
        (unsupported / ".agent-created").write_text("", encoding="utf-8")
        rewritten = self.skill(personal, "rewritten-history")
        (rewritten / ".agent-created").write_text("", encoding="utf-8")
        degenerate = self.skill(personal, "degenerate-checkpoint")
        (degenerate / ".agent-created").write_text("", encoding="utf-8")
        no_evidence = self.skill(personal, "no-evidence")

        subprocess.run(["git", "-C", str(personal), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(personal), "commit", "-qm", "machine-created skills"],
            check=True,
        )
        creation = subprocess.check_output(
            ["git", "-C", str(personal), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(personal),
                "commit",
                "--allow-empty",
                "-qm",
                "sealed history checkpoint",
            ],
            check=True,
        )
        stable_checkpoint = subprocess.check_output(
            ["git", "-C", str(personal), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(personal),
                "commit",
                "--allow-empty",
                "-qm",
                "rewritten history checkpoint",
            ],
            check=True,
        )
        rewritten_checkpoint = subprocess.check_output(
            ["git", "-C", str(personal), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(personal),
                "reset",
                "--hard",
                "-q",
                stable_checkpoint,
            ],
            check=True,
        )

        policy_payload = {
            "schema_version": 1,
            "accepted_legacy_proof_versions": [1],
            "machine_authors": [
                {
                    "name": "Dreaming Machine",
                    "email": "dreaming-machine@example.invalid",
                }
            ],
            "migration_cutoff": "2030-01-01T00:00:00+00:00",
            "protected_claim_paths": [".pinned", ".adopted"],
        }
        policy = self.sealed(policy_payload, "policy_sha256")

        def proof(
            name: str,
            version: int = 1,
            history_checkpoint: str = stable_checkpoint,
        ) -> dict:
            _, inventory_sha256 = module.git_skill_inventory(
                personal, creation, name
            )
            payload = {
                "schema_version": version,
                "kind": "legacy_git_creation",
                "skill": name,
                "creation_commit": creation,
                "history_checkpoint": history_checkpoint,
                "creation_inventory_sha256": inventory_sha256,
                "policy_sha256": policy["policy_sha256"],
            }
            return self.sealed(payload, "proof_sha256")

        valid_proof = proof("legacy-proof")
        invalid_value = proof("invalid-proof")
        invalid_value.pop("creation_inventory_sha256")
        invalid_value = self.sealed(
            {
                key: value
                for key, value in invalid_value.items()
                if key != "proof_sha256"
            },
            "proof_sha256",
        )
        wrong_digest_value = proof("wrong-digest")
        wrong_digest_value["proof_sha256"] = "sha256:" + "f" * 64
        unsupported_value = proof("unsupported-version", version=99)
        rewritten_value = proof(
            "rewritten-history", history_checkpoint=rewritten_checkpoint
        )
        degenerate_value = proof(
            "degenerate-checkpoint", history_checkpoint=creation
        )
        conflicting_value = proof("conflicting-evidence")
        conflicting_value["skill"] = "another-skill"
        conflicting_value = self.sealed(
            {
                key: value
                for key, value in conflicting_value.items()
                if key != "proof_sha256"
            },
            "proof_sha256",
        )

        same_machine_root = self.case / "same-machine"
        same_machine = self.skill(same_machine_root, "same-name")
        self.write_envelope(same_machine)
        same_unknown_root = self.case / "same-unknown"
        same_unknown = self.skill(same_unknown_root, "same-name")

        publisher = self.case / "publisher"
        publisher_skill = self.skill(publisher, "dreaming-current")
        publisher_bundle = self.write_bundle_manifest(publisher)
        plugin = self.case / "plugin" / "skills"
        plugin_skill = self.skill(plugin, "plugin-owned")
        builtin = self.case / "builtin"
        builtin_skill = self.skill(builtin, "builtin-owned")

        roots = [
            self.root(
                "personal",
                "personal",
                personal,
                "dreaming_managed",
                provenance_policy=policy,
                legacy_proofs={
                    "legacy-proof": valid_proof,
                    "invalid-proof": invalid_value,
                    "wrong-digest": wrong_digest_value,
                    "unsupported-version": unsupported_value,
                    "rewritten-history": rewritten_value,
                    "degenerate-checkpoint": degenerate_value,
                    "conflicting-evidence": conflicting_value,
                },
            ),
            self.root(
                "same-machine",
                "personal",
                same_machine_root,
                "user_protected",
            ),
            self.root(
                "same-unknown",
                "personal",
                same_unknown_root,
                "legacy_machine",
            ),
            self.root(
                "publisher",
                "dreaming_publisher",
                publisher,
                "unknown_provenance",
                bundle_id=publisher_bundle,
            ),
            self.root(
                "plugin",
                "plugin",
                plugin,
                "unknown_provenance",
                plugin_id="fixture@market",
                source_identity="installed:market/fixture",
                version="1.0.0",
                package={
                    "plugin_id": "fixture@market",
                    "source_identity": "installed:market/fixture",
                    "version": "1.0.0",
                },
            ),
            self.root(
                "builtin",
                "builtin",
                builtin,
                "unknown_provenance",
                copilot_version="1.0.79",
            ),
        ]
        enabled_paths = [
            current,
            legacy_envelope,
            legacy_proof,
            user_owned,
            malformed,
            conflict,
            marker_only,
            invalid_proof,
            wrong_digest,
            unsupported,
            rewritten,
            degenerate,
            no_evidence,
            same_machine,
            same_unknown,
            publisher_skill,
            plugin_skill,
            builtin_skill,
        ]
        census = module.reconcile(
            host_id="macbook",
            roots=roots,
            contexts=[
                {
                    "id": "user",
                    "kind": "user",
                    "registered": True,
                    "runtime_skills": [
                        {
                            "name": path.name,
                            "source": "fixture",
                            "path": str(path),
                            "enabled": True,
                        }
                        for path in enabled_paths
                    ],
                }
            ],
            collected_at="2026-08-13T00:00:00+00:00",
        )

        by_path = {
            row["absolute_path"]: row for row in census["physical_instances"]
        }
        expected = {
            current: "legacy_machine",
            legacy_envelope: "legacy_machine",
            legacy_proof: "legacy_machine",
            user_owned: "user_protected",
            malformed: "unknown_provenance",
            conflict: "unknown_provenance",
            marker_only: "unknown_provenance",
            invalid_proof: "unknown_provenance",
            wrong_digest: "unknown_provenance",
            unsupported: "unknown_provenance",
            rewritten: "unknown_provenance",
            degenerate: "unknown_provenance",
            no_evidence: "unknown_provenance",
            same_machine: "legacy_machine",
            same_unknown: "unknown_provenance",
            publisher_skill: "dreaming_managed",
            plugin_skill: "plugin_managed",
            builtin_skill: "cli_builtin",
        }
        for path, authority in expected.items():
            self.assertEqual(by_path[str(path)]["authority"], authority, str(path))
        self.assertEqual(
            census["authority_counts"],
            {
                "cli_builtin": 1,
                "dreaming_managed": 1,
                "legacy_machine": 4,
                "plugin_managed": 1,
                "unknown_provenance": 10,
                "user_protected": 1,
            },
        )
        self.assertEqual(
            by_path[str(legacy_proof)]["provenance"],
            {
                "status": "verified",
                "basis": "verified_legacy_git_proof",
                "policy_sha256": policy["policy_sha256"],
                "proof_sha256": valid_proof["proof_sha256"],
            },
        )
        self.assertEqual(
            by_path[str(marker_only)]["provenance"]["basis"], "marker_only"
        )
        self.assertEqual(
            by_path[str(wrong_digest)]["provenance"]["basis"],
            "invalid_legacy_proof_digest",
        )
        self.assertEqual(
            by_path[str(unsupported)]["provenance"]["basis"],
            "unsupported_legacy_proof_version",
        )
        self.assertEqual(
            by_path[str(rewritten)]["provenance"]["basis"],
            "legacy_history_checkpoint_rewritten",
        )
        self.assertEqual(
            by_path[str(degenerate)]["provenance"]["basis"],
            "legacy_history_checkpoint_not_distinct",
        )
        same_name_instances = [
            row for row in census["physical_instances"] if row["skill_name"] == "same-name"
        ]
        self.assertEqual(len(same_name_instances), 2)
        self.assertEqual(
            {row["authority"] for row in same_name_instances},
            {"legacy_machine", "unknown_provenance"},
        )
        self.assertEqual(
            len({row["instance_id"] for row in same_name_instances}), 2
        )
        self.assertTrue(census["scope"]["complete"])

    def test_publisher_identity_rejects_missing_manifest_file(self) -> None:
        publisher = self.case / "publisher"
        skill = self.skill(publisher, "managed")
        (skill / "reference.md").write_text("sealed", encoding="utf-8")
        bundle_id = self.write_bundle_manifest(publisher)
        (skill / "reference.md").unlink()
        census = module.reconcile(
            host_id="macbook",
            roots=[
                self.root(
                    "publisher",
                    "dreaming_publisher",
                    publisher,
                    "dreaming_managed",
                    bundle_id=bundle_id,
                )
            ],
            contexts=[],
            collected_at="fixture",
        )
        instance = census["physical_instances"][0]
        self.assertEqual(instance["authority"], "unknown_provenance")
        self.assertEqual(
            instance["provenance"]["basis"],
            "dreaming_bundle_inventory_mismatch",
        )

    def test_multiply_mapped_runtime_skill_fails_completeness(self) -> None:
        root = self.case / "root"
        skill = self.skill(root, "duplicate")
        roots = [
            self.root("one", "custom", root, "unknown_provenance"),
            self.root("two", "custom", root, "unknown_provenance"),
        ]
        census = module.reconcile(
            host_id="macbook",
            roots=roots,
            contexts=[
                {
                    "id": "user",
                    "kind": "user",
                    "registered": True,
                    "runtime_skills": [
                        {
                            "name": "duplicate",
                            "source": "custom",
                            "path": str(skill),
                            "enabled": True,
                        }
                    ],
                }
            ],
            collected_at="fixture",
        )
        self.assertFalse(census["scope"]["complete"])
        self.assertEqual(
            census["unresolved_mappings"][0]["reason"], "multiply_mapped"
        )

    def test_symlinked_skill_content_is_rejected(self) -> None:
        root = self.case / "root"
        skill = self.skill(root, "unsafe")
        target = self.case / "target"
        target.write_text("secret", encoding="utf-8")
        (skill / "linked").symlink_to(target)
        with self.assertRaises(module.EstateError):
            module.reconcile(
                host_id="macbook",
                roots=[self.root("unsafe", "custom", root, "unknown_provenance")],
                contexts=[],
                collected_at="fixture",
            )

    def usage_census(self, mappings: list[tuple[str, str]]) -> dict:
        snapshot = {
            "schema_version": 1,
            "host_id": "fixture-macbook",
            "collected_at": "2026-08-17T18:00:00+00:00",
            "scope": {"complete": True},
            "enabled_instances": [
                {
                    "runtime_name": name,
                    "runtime_enabled": True,
                    "canonical_capability_id": capability_id,
                }
                for name, capability_id in mappings
            ],
        }
        return {**snapshot, "snapshot_sha256": module.digest(snapshot)}

    def write_usage_events(self, session: str, events: list[dict]) -> Path:
        path = self.case / "sessions" / session / "events.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
        return path

    def usage_event(
        self,
        event_type: str,
        timestamp: str,
        *,
        call_id: str = "call-1",
        name: str = "fixture-skill",
        success: bool = True,
    ) -> dict:
        data = {"toolCallId": call_id}
        if event_type == "tool.execution_start":
            data.update({"toolName": "skill", "arguments": {"skill": name}})
        elif event_type == "tool.execution_complete":
            data["success"] = success
            if success:
                data["result"] = {
                    "content": f'Skill "{name}" loaded successfully.'
                }
        return {"type": event_type, "timestamp": timestamp, "data": data}

    def test_usage_aggregates_only_successful_correlated_skill_calls(self) -> None:
        capability_id = "sha256:" + "1" * 64
        self.write_usage_events(
            "one",
            [
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-16T10:00:00+00:00",
                    call_id="success-1",
                ),
                self.usage_event(
                    "tool.execution_complete",
                    "2026-08-16T10:00:01+00:00",
                    call_id="success-1",
                ),
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-17T10:00:00+00:00",
                    call_id="failed",
                ),
                self.usage_event(
                    "tool.execution_complete",
                    "2026-08-17T10:00:01+00:00",
                    call_id="failed",
                    success=False,
                ),
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-17T11:00:00+00:00",
                    call_id="success-2",
                ),
                self.usage_event(
                    "tool.execution_complete",
                    "2026-08-17T11:00:01+00:00",
                    call_id="success-2",
                ),
            ],
        )
        usage = module.collect_usage(
            self.usage_census([("fixture-skill", capability_id)]),
            self.case / "sessions",
            collected_at=datetime(2026, 8, 17, 18, tzinfo=timezone.utc),
            max_sessions=10,
            max_bytes=100_000,
            quiet_seconds=0,
        )
        self.assertTrue(usage["coverage"]["complete"])
        self.assertEqual(usage["coverage"]["sessions_scanned"], 1)
        self.assertEqual(
            usage["canonical_usage"],
            [
                {
                    "canonical_capability_id": capability_id,
                    "uses_7d": 2,
                    "uses_30d": 2,
                    "uses_90d": 2,
                    "uses_total": 2,
                    "last_successful_invocation": "2026-08-17T11:00:01+00:00",
                }
            ],
        )
        self.assertEqual(usage["unattributed"], [])

    def test_usage_accepts_namespaced_names_and_preserves_verified_calls(self) -> None:
        namespaced_id = "sha256:" + "6" * 64
        ordinary_id = "sha256:" + "7" * 64
        self.write_usage_events(
            "namespaced",
            [
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-17T10:00:00+00:00",
                    call_id="namespaced",
                    name="code-review--auto",
                ),
                self.usage_event(
                    "tool.execution_complete",
                    "2026-08-17T10:00:01+00:00",
                    call_id="namespaced",
                    name="code-review--auto",
                ),
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-17T11:00:00+00:00",
                    call_id="mismatch",
                    name="fixture-skill",
                ),
                self.usage_event(
                    "tool.execution_complete",
                    "2026-08-17T11:00:01+00:00",
                    call_id="mismatch",
                    name="different-skill",
                ),
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-17T12:00:00+00:00",
                    call_id="ordinary",
                    name="fixture-skill",
                ),
                self.usage_event(
                    "tool.execution_complete",
                    "2026-08-17T12:00:01+00:00",
                    call_id="ordinary",
                    name="fixture-skill",
                ),
            ],
        )
        usage = module.collect_usage(
            self.usage_census(
                [
                    ("code-review--auto", namespaced_id),
                    ("fixture-skill", ordinary_id),
                ]
            ),
            self.case / "sessions",
            collected_at=datetime(2026, 8, 17, 18, tzinfo=timezone.utc),
            max_sessions=10,
            max_bytes=100_000,
            quiet_seconds=0,
        )
        self.assertFalse(usage["coverage"]["complete"])
        self.assertEqual(
            {
                item["canonical_capability_id"]: item["uses_total"]
                for item in usage["canonical_usage"]
            },
            {namespaced_id: 1, ordinary_id: 1},
        )
        self.assertEqual(
            usage["coverage"]["failures"][0]["reason"],
            "usage_session_unverified_skill_completion",
        )

    def test_usage_marks_orphaned_successful_skill_completion_incomplete(self) -> None:
        capability_id = "sha256:" + "8" * 64
        self.write_usage_events(
            "orphaned",
            [
                self.usage_event(
                    "tool.execution_complete",
                    "2026-08-17T10:00:01+00:00",
                    call_id="orphaned",
                    name="fixture-skill",
                ),
            ],
        )
        usage = module.collect_usage(
            self.usage_census([("fixture-skill", capability_id)]),
            self.case / "sessions",
            collected_at=datetime(2026, 8, 17, 18, tzinfo=timezone.utc),
            max_sessions=10,
            max_bytes=100_000,
            quiet_seconds=0,
        )
        self.assertFalse(usage["coverage"]["complete"])
        self.assertEqual(usage["canonical_usage"][0]["uses_total"], 0)
        self.assertEqual(
            usage["coverage"]["failures"][0]["reason"],
            "usage_session_unmatched_skill_completion",
        )

    def test_usage_discards_invalid_sessions_and_never_scans_nested_artifacts(self) -> None:
        capability_id = "sha256:" + "2" * 64
        malformed = self.write_usage_events(
            "malformed",
            [
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-17T10:00:00+00:00",
                )
            ],
        )
        with malformed.open("a", encoding="utf-8") as handle:
            handle.write("{\n")
        nested = self.case / "sessions" / "container" / "files" / "retained"
        nested.mkdir(parents=True)
        (nested / "events.jsonl").write_text(
            json.dumps(
                self.usage_event(
                    "tool.execution_complete",
                    "2026-08-17T10:00:01+00:00",
                )
            ),
            encoding="utf-8",
        )
        usage = module.collect_usage(
            self.usage_census([("fixture-skill", capability_id)]),
            self.case / "sessions",
            collected_at=datetime(2026, 8, 17, 18, tzinfo=timezone.utc),
            max_sessions=10,
            max_bytes=100_000,
            quiet_seconds=0,
        )
        self.assertFalse(usage["coverage"]["complete"])
        self.assertEqual(usage["canonical_usage"][0]["uses_total"], 0)
        self.assertEqual(len(usage["coverage"]["failures"]), 1)
        failure = usage["coverage"]["failures"][0]
        self.assertRegex(failure["session_id"], r"^sha256:[0-9a-f]{64}$")
        self.assertNotEqual(
            failure["session_id"], "malformed"
        )
        self.assertNotIn("events.jsonl", json.dumps(usage))

    def test_usage_rejects_duplicate_future_and_ambiguous_attribution(self) -> None:
        first_id = "sha256:" + "3" * 64
        second_id = "sha256:" + "4" * 64
        self.write_usage_events(
            "duplicate",
            [
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-17T10:00:00+00:00",
                    call_id="duplicate",
                ),
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-17T10:00:01+00:00",
                    call_id="duplicate",
                ),
            ],
        )
        self.write_usage_events(
            "future",
            [
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-18T10:00:00+00:00",
                    call_id="future",
                )
            ],
        )
        self.write_usage_events(
            "ambiguous",
            [
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-17T12:00:00+00:00",
                    call_id="ambiguous",
                    name="shared",
                ),
                self.usage_event(
                    "tool.execution_complete",
                    "2026-08-17T12:00:01+00:00",
                    call_id="ambiguous",
                    name="shared",
                ),
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-17T13:00:00+00:00",
                    call_id="missing",
                    name="missing",
                ),
                self.usage_event(
                    "tool.execution_complete",
                    "2026-08-17T13:00:01+00:00",
                    call_id="missing",
                    name="missing",
                ),
            ],
        )
        usage = module.collect_usage(
            self.usage_census(
                [("shared", first_id), ("shared", second_id)]
            ),
            self.case / "sessions",
            collected_at=datetime(2026, 8, 17, 18, tzinfo=timezone.utc),
            max_sessions=10,
            max_bytes=100_000,
            quiet_seconds=0,
        )
        self.assertFalse(usage["coverage"]["complete"])
        self.assertEqual(
            {(item["name"], item["reason"]) for item in usage["unattributed"]},
            {("shared", "conflicting_mapping"), ("missing", "unmapped")},
        )
        self.assertTrue(
            all(item["uses_total"] == 0 for item in usage["canonical_usage"])
        )
        self.assertEqual(len(usage["coverage"]["failures"]), 4)
        self.assertEqual(
            {
                item["reason"]
                for item in usage["coverage"]["failures"]
            },
            {
                "usage_census_conflicting_mapping",
                "usage_session_duplicate_skill_start",
                "usage_session_future_timestamp",
            },
        )

    def test_usage_bounds_are_incomplete_not_zero_evidence(self) -> None:
        capability_id = "sha256:" + "5" * 64
        for session in ("one", "two"):
            self.write_usage_events(
                session,
                [
                    self.usage_event(
                        "tool.execution_start",
                        "2026-08-17T10:00:00+00:00",
                        call_id=session,
                    ),
                    self.usage_event(
                        "tool.execution_complete",
                        "2026-08-17T10:00:01+00:00",
                        call_id=session,
                    ),
                ],
            )
        usage = module.collect_usage(
            self.usage_census([("fixture-skill", capability_id)]),
            self.case / "sessions",
            collected_at=datetime(2026, 8, 17, 18, tzinfo=timezone.utc),
            max_sessions=1,
            max_bytes=100_000,
            quiet_seconds=0,
        )
        self.assertFalse(usage["coverage"]["complete"])
        self.assertEqual(usage["coverage"]["bound_reached"], "max_sessions")
        self.assertEqual(usage["canonical_usage"][0]["uses_total"], 1)

    def test_usage_index_advances_reuses_and_replaces_changed_sessions(self) -> None:
        capability_id = "sha256:" + "9" * 64
        collected_at = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
        root = self.case / "sessions"
        index = self.case / "state" / "usage-index.json"
        paths = []
        for offset, session in enumerate(("one", "two", "three"), start=1):
            path = self.write_usage_events(
                session,
                [
                    self.usage_event(
                        "tool.execution_start",
                        "2026-08-17T10:00:00+00:00",
                        call_id=session,
                    ),
                    self.usage_event(
                        "tool.execution_complete",
                        "2026-08-17T10:00:01+00:00",
                        call_id=session,
                    ),
                ],
            )
            stamp = collected_at.timestamp() - (4000 - offset * 100)
            os.utime(path, (stamp, stamp))
            paths.append(path)

        for expected in (1, 2, 3):
            usage = module.collect_usage(
                self.usage_census([("fixture-skill", capability_id)]),
                root,
                collected_at=collected_at,
                max_sessions=1,
                max_bytes=100_000,
                index_path=index,
            )
            self.assertEqual(usage["coverage"]["indexed_sessions"], expected)
            self.assertEqual(usage["coverage"]["sessions_parsed_this_run"], 1)

        unchanged = module.collect_usage(
            self.usage_census([("fixture-skill", capability_id)]),
            root,
            collected_at=collected_at,
            max_sessions=1,
            max_bytes=100_000,
            index_path=index,
        )
        self.assertTrue(unchanged["coverage"]["corpus_complete"])
        self.assertEqual(unchanged["coverage"]["sessions_parsed_this_run"], 0)
        self.assertEqual(unchanged["canonical_usage"][0]["uses_total"], 3)

        with paths[0].open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    self.usage_event(
                        "tool.execution_start",
                        "2026-08-17T11:00:00+00:00",
                        call_id="one-extra",
                    )
                )
                + "\n"
            )
            handle.write(
                json.dumps(
                    self.usage_event(
                        "tool.execution_complete",
                        "2026-08-17T11:00:01+00:00",
                        call_id="one-extra",
                    )
                )
                + "\n"
            )
        changed_stamp = collected_at.timestamp() - 600
        os.utime(paths[0], (changed_stamp, changed_stamp))
        changed = module.collect_usage(
            self.usage_census([("fixture-skill", capability_id)]),
            root,
            collected_at=collected_at,
            max_sessions=1,
            max_bytes=100_000,
            index_path=index,
        )
        self.assertEqual(changed["coverage"]["indexed_sessions"], 3)
        self.assertEqual(changed["coverage"]["sessions_parsed_this_run"], 1)
        self.assertEqual(changed["canonical_usage"][0]["uses_total"], 4)

    def test_usage_index_streams_oversized_session_then_moves_beyond_it(self) -> None:
        capability_id = "sha256:" + "a" * 64
        collected_at = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
        root = self.case / "sessions"
        index = self.case / "state" / "usage-index.json"
        oversized = self.write_usage_events(
            "oversized",
            [
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-17T10:00:00+00:00",
                    call_id="oversized",
                ),
                self.usage_event(
                    "tool.execution_complete",
                    "2026-08-17T10:00:01+00:00",
                    call_id="oversized",
                ),
                *[
                    {
                        "type": "user.message",
                        "timestamp": "2026-08-17T10:00:02+00:00",
                        "data": {"padding": "x" * 100},
                    }
                    for _ in range(20)
                ],
            ],
        )
        ordinary = self.write_usage_events(
            "ordinary",
            [
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-17T11:00:00+00:00",
                    call_id="ordinary",
                ),
                self.usage_event(
                    "tool.execution_complete",
                    "2026-08-17T11:00:01+00:00",
                    call_id="ordinary",
                ),
            ],
        )
        os.utime(
            oversized,
            (collected_at.timestamp() - 1000, collected_at.timestamp() - 1000),
        )
        os.utime(
            ordinary,
            (collected_at.timestamp() - 900, collected_at.timestamp() - 900),
        )
        budget = ordinary.stat().st_size + 1
        self.assertGreater(oversized.stat().st_size, budget)
        original_read_bytes = Path.read_bytes

        def guarded_read_bytes(path: Path) -> bytes:
            if path.name == "events.jsonl":
                raise AssertionError("transcript was read as one byte string")
            return original_read_bytes(path)

        with mock.patch.object(Path, "read_bytes", guarded_read_bytes):
            first = module.collect_usage(
                self.usage_census([("fixture-skill", capability_id)]),
                root,
                collected_at=collected_at,
                max_sessions=10,
                max_bytes=budget,
                index_path=index,
            )
        self.assertEqual(first["coverage"]["indexed_sessions"], 1)
        self.assertGreater(first["coverage"]["bytes_parsed_this_run"], budget)
        self.assertEqual(first["coverage"]["bound_reached"], "max_bytes")

        second = module.collect_usage(
            self.usage_census([("fixture-skill", capability_id)]),
            root,
            collected_at=collected_at,
            max_sessions=10,
            max_bytes=budget,
            index_path=index,
        )
        self.assertTrue(second["coverage"]["corpus_complete"])
        self.assertEqual(second["coverage"]["indexed_sessions"], 2)
        self.assertEqual(second["canonical_usage"][0]["uses_total"], 2)

    def test_usage_index_is_private_rebuildable_and_quiescent(self) -> None:
        capability_id = "sha256:" + "b" * 64
        collected_at = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
        root = self.case / "sessions"
        index = self.case / "state" / "usage-index.json"
        sentinel = "PRIVATE-PROMPT-AND-PATH-SENTINEL"
        stable = self.write_usage_events(
            "private-session-name",
            [
                {
                    "type": "user.message",
                    "timestamp": "2026-08-17T09:00:00+00:00",
                    "data": {"prompt": sentinel, "path": f"/private/{sentinel}"},
                },
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-17T10:00:00+00:00",
                ),
                self.usage_event(
                    "tool.execution_complete",
                    "2026-08-17T10:00:01+00:00",
                ),
            ],
        )
        recent = self.write_usage_events(
            "recent-session",
            [
                self.usage_event(
                    "tool.execution_start",
                    "2026-08-17T11:00:00+00:00",
                    call_id="recent",
                ),
                self.usage_event(
                    "tool.execution_complete",
                    "2026-08-17T11:00:01+00:00",
                    call_id="recent",
                ),
            ],
        )
        os.utime(
            stable,
            (collected_at.timestamp() - 600, collected_at.timestamp() - 600),
        )
        os.utime(
            recent,
            (collected_at.timestamp() - 100, collected_at.timestamp() - 100),
        )
        first = module.collect_usage(
            self.usage_census([("fixture-skill", capability_id)]),
            root,
            collected_at=collected_at,
            max_sessions=10,
            max_bytes=100_000,
            index_path=index,
        )
        self.assertEqual(first["coverage"]["indexed_sessions"], 1)
        self.assertEqual(first["coverage"]["pending_sessions"], 1)
        self.assertEqual(
            first["coverage"]["pending"][0]["reason"],
            "events_recently_modified",
        )
        index_text = index.read_text(encoding="utf-8")
        self.assertNotIn(sentinel, index_text)
        self.assertNotIn("private-session-name", index_text)
        self.assertNotIn("events.jsonl", index_text)

        index.write_text("{malformed", encoding="utf-8")
        os.utime(
            recent,
            (collected_at.timestamp() - 600, collected_at.timestamp() - 600),
        )
        rebuilt = module.collect_usage(
            self.usage_census([("fixture-skill", capability_id)]),
            root,
            collected_at=collected_at,
            max_sessions=10,
            max_bytes=100_000,
            index_path=index,
        )
        self.assertEqual(rebuilt["coverage"]["index_status"], "rebuilt")
        self.assertTrue(rebuilt["coverage"]["corpus_complete"])
        self.assertEqual(len(list(index.parent.glob("usage-index.json.rejected-*"))), 1)

    def test_usage_aliases_are_exact_unique_and_never_override_direct_names(self) -> None:
        development_id = "sha256:" + "c" * 64
        nexus_id = "sha256:" + "d" * 64
        absorb_id = "sha256:" + "e" * 64
        direct_id = "sha256:" + "f" * 64
        guardrails_id = "sha256:" + "1" * 64
        unattended_id = "sha256:" + "2" * 64
        compact_id = "sha256:" + "3" * 64
        gaw_id = "sha256:" + "4" * 64
        loop_id = "sha256:" + "5" * 64
        upstream_id = "sha256:" + "6" * 64
        events = []
        for index, name in enumerate(
            (
                "architecture-guardrails",
                "autopilot-brief",
                "context-hygiene",
                "feature-development-loop",
                "gaw-development",
                "gated-pr-merge",
                "loop",
                "nexus-dev",
                "prototype-reference-integration",
                "upstream-contribution",
                "caveman",
            )
        ):
            call_id = f"alias-{index}"
            events.extend(
                [
                    self.usage_event(
                        "tool.execution_start",
                        f"2026-08-17T{index:02d}:00:00+00:00",
                        call_id=call_id,
                        name=name,
                    ),
                    self.usage_event(
                        "tool.execution_complete",
                        f"2026-08-17T{index:02d}:00:01+00:00",
                        call_id=call_id,
                        name=name,
                    ),
                ]
            )
        self.write_usage_events("aliases", events)
        usage = module.collect_usage(
            self.usage_census(
                [
                    ("guardrails", guardrails_id),
                    ("unattended-run", unattended_id),
                    ("self-compact", compact_id),
                    ("development-loop", development_id),
                    ("gaw", gaw_id),
                    ("microsoft-loop", loop_id),
                    ("nexus-gotchas", nexus_id),
                    ("absorb-poc", absorb_id),
                    ("upstream-pitch", upstream_id),
                ]
            ),
            self.case / "sessions",
            collected_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
            max_sessions=10,
            max_bytes=100_000,
            quiet_seconds=0,
        )
        self.assertEqual(
            {
                item["canonical_capability_id"]: item["uses_total"]
                for item in usage["canonical_usage"]
            },
            {
                guardrails_id: 1,
                unattended_id: 1,
                compact_id: 1,
                development_id: 2,
                gaw_id: 1,
                loop_id: 1,
                nexus_id: 1,
                absorb_id: 1,
                upstream_id: 1,
            },
        )
        self.assertEqual(
            [(item["name"], item["reason"]) for item in usage["unattributed"]],
            [("caveman", "unmapped")],
        )

        direct = module.collect_usage(
            self.usage_census(
                [
                    ("feature-development-loop", direct_id),
                    ("guardrails", guardrails_id),
                    ("unattended-run", unattended_id),
                    ("self-compact", compact_id),
                    ("development-loop", development_id),
                    ("gaw", gaw_id),
                    ("microsoft-loop", loop_id),
                    ("nexus-gotchas", nexus_id),
                    ("absorb-poc", absorb_id),
                    ("upstream-pitch", upstream_id),
                ]
            ),
            self.case / "sessions",
            collected_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
            max_sessions=10,
            max_bytes=100_000,
            quiet_seconds=0,
        )
        self.assertEqual(
            {
                item["canonical_capability_id"]: item["uses_total"]
                for item in direct["canonical_usage"]
            },
            {
                direct_id: 1,
                development_id: 1,
                guardrails_id: 1,
                unattended_id: 1,
                compact_id: 1,
                gaw_id: 1,
                loop_id: 1,
                nexus_id: 1,
                absorb_id: 1,
                upstream_id: 1,
            },
        )
        invalid = json.loads(json.dumps(module.USAGE_ALIASES))
        invalid["feature-development-loop"]["evidence"][0]["from"] = "wrong"
        with self.assertRaisesRegex(module.EstateError, "usage_aliases_invalid"):
            module.validate_usage_aliases(invalid)

    def test_plugin_capabilities_cover_non_skill_surfaces(self) -> None:
        plugin = self.case / "plugin"
        self.skill(plugin / "skills", "one")
        (plugin / "agents").mkdir()
        (plugin / "agents/reviewer.md").write_text("fixture", encoding="utf-8")
        (plugin / "hooks").mkdir()
        (plugin / "hooks/hooks.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "hooks": {
                        "sessionStart": [
                            {"type": "command", "bash": "true"}
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        (plugin / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"fixture": {"command": "true"}}}),
            encoding="utf-8",
        )
        (plugin / ".lsp.json").write_text(
            json.dumps({"lspServers": {"python": {"command": "true"}}}),
            encoding="utf-8",
        )
        capabilities = module.plugin_capabilities(
            plugin,
            {
                "name": "fixture",
                "version": "1.0.0",
                "skills": ["./skills/one"],
                "agents": ["./agents"],
                "hooks": "./hooks/hooks.json",
                "mcpServers": "./.mcp.json",
                "lspServers": "./.lsp.json",
            },
        )
        self.assertTrue(capabilities["complete"])
        self.assertEqual(capabilities["skills"], ["./skills/one"])
        self.assertEqual(capabilities["agents"], ["./agents/reviewer.md"])
        self.assertEqual(
            capabilities["hooks"],
            [
                "./hooks/hooks.json#sessionStart[0]@"
                + module.digest({"type": "command", "bash": "true"})
            ],
        )
        self.assertEqual(
            capabilities["mcp_servers"], ["./.mcp.json#fixture"]
        )
        self.assertEqual(capabilities["lsp_servers"], ["./.lsp.json#python"])

    def test_plugin_capabilities_fail_closed_on_unknown_metadata(self) -> None:
        plugin = self.case / "plugin"
        self.skill(plugin / "skills", "one")
        capabilities = module.plugin_capabilities(
            plugin,
            {
                "name": "fixture",
                "version": "1.0.0",
                "skills": "./skills",
                "commands": ["./commands/one.md"],
            },
        )
        self.assertFalse(capabilities["complete"])
        self.assertEqual(capabilities["unknown_metadata"], ["commands"])

    def test_plugin_capabilities_merge_declared_and_conventional_files(
        self,
    ) -> None:
        plugin = self.case / "plugin"
        (plugin / "hooks").mkdir(parents=True)
        (plugin / "custom").mkdir()
        default_hook = {"type": "command", "bash": "default"}
        custom_hook = {"type": "command", "bash": "custom"}
        for path, event, hook in (
            (plugin / "hooks/hooks.json", "sessionStart", default_hook),
            (plugin / "custom/hooks.json", "sessionEnd", custom_hook),
        ):
            path.write_text(
                json.dumps({"version": 1, "hooks": {event: [hook]}}),
                encoding="utf-8",
            )
        (plugin / ".mcp.json").write_text(
            json.dumps(
                {"mcpServers": {"default": {"command": "default"}}}
            ),
            encoding="utf-8",
        )
        (plugin / "custom/mcp.json").write_text(
            json.dumps({"custom": {"command": "custom"}}),
            encoding="utf-8",
        )
        capabilities = module.plugin_capabilities(
            plugin,
            {
                "name": "fixture",
                "version": "1.0.0",
                "hooks": "./custom/hooks.json",
                "mcpServers": "./custom/mcp.json",
            },
        )
        self.assertTrue(capabilities["complete"])
        self.assertEqual(len(capabilities["hooks"]), 2)
        self.assertEqual(
            capabilities["mcp_servers"],
            ["./.mcp.json#default", "./custom/mcp.json#custom"],
        )

    def test_plugin_capabilities_retain_implicit_hooks_and_mcp_only(
        self,
    ) -> None:
        plugin = self.case / "plugin"
        (plugin / "hooks").mkdir(parents=True)
        hook = {"type": "command", "bash": "true"}
        (plugin / "hooks/hooks.json").write_text(
            json.dumps({"version": 1, "hooks": {"sessionStart": [hook]}}),
            encoding="utf-8",
        )
        (plugin / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"fixture": {"command": "true"}}}),
            encoding="utf-8",
        )
        (plugin / ".lsp.json").write_text(
            json.dumps({"lspServers": {"python": {"command": "true"}}}),
            encoding="utf-8",
        )
        capabilities = module.plugin_capabilities(
            plugin, {"name": "fixture", "version": "1.0.0"}
        )
        self.assertTrue(capabilities["complete"])
        self.assertEqual(len(capabilities["hooks"]), 1)
        self.assertEqual(
            capabilities["mcp_servers"], ["./.mcp.json#fixture"]
        )
        self.assertEqual(capabilities["lsp_servers"], [])

    def test_plugin_capability_paths_fail_closed(self) -> None:
        plugin = self.case / "plugin"
        plugin.mkdir()
        outside = self.case / "outside.md"
        outside.write_text("fixture", encoding="utf-8")
        with self.assertRaises(module.EstateError):
            module.plugin_declared_files(plugin, ["../outside.md"], "agents")

        (plugin / "agents-link").symlink_to(outside)
        with self.assertRaises(module.EstateError):
            module.plugin_declared_files(
                plugin, ["./agents-link"], "agents"
            )

        agents = plugin / "agents"
        agents.mkdir()
        (agents / "linked.md").symlink_to(outside)
        with self.assertRaises(module.EstateError):
            module.plugin_declared_files(plugin, ["./agents"], "agents")
        capabilities = module.plugin_capabilities(
            plugin,
            {
                "name": "fixture",
                "version": "1.0.0",
                "agents": "\0",
            },
        )
        self.assertFalse(capabilities["complete"])

    def test_plugin_capability_paths_accept_symlinked_ancestor(self) -> None:
        real = self.case / "real"
        plugin = real / "fixture"
        self.skill(plugin / "skills", "one")
        alias = self.case / "market"
        alias.symlink_to(real)
        capabilities = module.plugin_capabilities(
            alias / "fixture",
            {
                "name": "fixture",
                "version": "1.0.0",
                "skills": "./skills",
            },
        )
        self.assertTrue(capabilities["complete"])
        self.assertEqual(capabilities["skills"], ["./skills/one"])

    def test_plugin_capability_scan_errors_fail_closed(self) -> None:
        plugin = self.case / "plugin"
        (plugin / "agents").mkdir(parents=True)
        with mock.patch.object(
            module.os, "walk", side_effect=OSError("fixture scan failure")
        ):
            with self.assertRaises(module.EstateError):
                module.plugin_declared_files(
                    plugin, ["./agents"], "agents"
                )

    def test_plugin_capability_duplicate_declarations_are_preserved(
        self,
    ) -> None:
        plugin = self.case / "plugin"
        self.skill(plugin / "skills", "one")
        capabilities = module.plugin_capabilities(
            plugin,
            {
                "name": "fixture",
                "version": "1.0.0",
                "skills": ["./skills/one", "./skills/one"],
            },
        )
        self.assertEqual(
            capabilities["skills"], ["./skills/one", "./skills/one"]
        )
        _, reasons = module.plugin_capability_ids(capabilities)
        self.assertIn("skills_inventory_duplicated", reasons)

    def test_plugin_server_configs_reject_ambiguous_or_malformed_data(
        self,
    ) -> None:
        plugin = self.case / "plugin"
        plugin.mkdir()
        malformed_values = (
            {"mcpServers": {"mcpServers": {"command": "true"}}},
            {"mcpServers": {"": {"command": "true"}}},
            {"mcpServers": {"fixture": None}},
            {"mcpServers": {"fixture#other": {"command": "true"}}},
        )
        for manifest in malformed_values:
            with self.subTest(manifest=manifest):
                capabilities = module.plugin_capabilities(
                    plugin,
                    {
                        "name": "fixture",
                        "version": "1.0.0",
                        **manifest,
                    },
                )
                self.assertFalse(capabilities["complete"])
        (plugin / "servers.json").write_text(
            json.dumps(
                {
                    "mcpServers": {"one": {"command": "one"}},
                    "two": {"command": "two"},
                }
            ),
            encoding="utf-8",
        )
        capabilities = module.plugin_capabilities(
            plugin,
            {
                "name": "fixture",
                "version": "1.0.0",
                "mcpServers": "./servers.json",
            },
        )
        self.assertFalse(capabilities["complete"])

    def test_plugin_hooks_require_structured_definitions(self) -> None:
        plugin = self.case / "plugin"
        plugin.mkdir()
        (plugin / "hooks.json").write_text(
            json.dumps(
                {"version": 1, "hooks": {"sessionStart": [{"bash": "true"}]}}
            ),
            encoding="utf-8",
        )
        capabilities = module.plugin_capabilities(
            plugin,
            {
                "name": "fixture",
                "version": "1.0.0",
                "hooks": "./hooks.json",
            },
        )
        self.assertFalse(capabilities["complete"])

    def test_plugin_capabilities_do_not_fallback_after_declared_failure(
        self,
    ) -> None:
        plugin = self.case / "plugin"
        (plugin / "hooks").mkdir(parents=True)
        hook = {"type": "command", "bash": "default"}
        (plugin / "hooks/hooks.json").write_text(
            json.dumps({"version": 1, "hooks": {"sessionStart": [hook]}}),
            encoding="utf-8",
        )
        (plugin / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"default": {"command": "true"}}}),
            encoding="utf-8",
        )
        capabilities = module.plugin_capabilities(
            plugin,
            {
                "name": "fixture",
                "version": "1.0.0",
                "hooks": "./missing-hooks.json",
                "mcpServers": "./missing-mcp.json",
            },
        )
        self.assertFalse(capabilities["complete"])
        self.assertEqual(capabilities["hooks"], [])
        self.assertEqual(capabilities["mcp_servers"], [])

    def test_plugin_capability_identifiers_are_validated(self) -> None:
        plugin = self.plugin_gate_fixture()
        plugin["capabilities"]["agents"] = [" arbitrary\nvalue "]
        _, reasons = module.plugin_capability_ids(plugin["capabilities"])
        self.assertIn("agents_identifier_malformed", reasons)

    def test_chk04_all_redundant_plugin_is_reported_eligible(self) -> None:
        plugin = self.plugin_gate_fixture()
        result = module.evaluate_plugin_capability_gate(
            plugin, self.plugin_gate_evidence(plugin)
        )
        self.assertTrue(result["eligible_for_disablement"])
        self.assertEqual(result["decision"], "disable_eligible")
        self.assertEqual(result["blocking_reasons"], [])

    def test_chk04_requires_exact_capability_evaluation_coverage(self) -> None:
        cases = ("missing", "duplicate", "unknown")
        for case in cases:
            with self.subTest(case=case):
                plugin = self.plugin_gate_fixture()
                evidence = self.plugin_gate_evidence(plugin)
                if case == "missing":
                    evidence["capability_evaluations"].pop()
                    reason = "capability_evaluations_incomplete"
                elif case == "duplicate":
                    evidence["capability_evaluations"].append(
                        dict(evidence["capability_evaluations"][0])
                    )
                    reason = "capability_evaluation_duplicated"
                else:
                    unknown = dict(evidence["capability_evaluations"][0])
                    unknown["capability_id"] = "agents:./agents/unknown.md"
                    evidence["capability_evaluations"].append(unknown)
                    reason = "capability_evaluation_unknown"
                self.assert_blocked(
                    module.evaluate_plugin_capability_gate(plugin, evidence),
                    reason,
                )

    def test_chk04_rejects_disposition_class_crossovers(self) -> None:
        for prefix, disposition in (
            ("skills:", "superseded"),
            ("agents:", "regressing"),
        ):
            with self.subTest(prefix=prefix):
                plugin = self.plugin_gate_fixture()
                evidence = self.plugin_gate_evidence(plugin)
                evaluation = next(
                    item
                    for item in evidence["capability_evaluations"]
                    if item["capability_id"].startswith(prefix)
                )
                evaluation["disposition"] = disposition
                self.assert_blocked(
                    module.evaluate_plugin_capability_gate(plugin, evidence),
                    "capability_retained_or_unknown",
                )

    def test_chk04_rejects_unknown_top_level_evidence(self) -> None:
        plugin = self.plugin_gate_fixture()
        evidence = self.plugin_gate_evidence(plugin)
        evidence["dependency_inventory_v2"] = {"pins": ["fixture"]}
        self.assert_blocked(
            module.evaluate_plugin_capability_gate(plugin, evidence),
            "plugin_evidence_malformed",
        )

    def test_chk04_requires_unique_plugin_settings_keys(self) -> None:
        plugin = self.plugin_gate_fixture()
        evidence = self.plugin_gate_evidence(plugin)
        duplicate = {
            **plugin,
            "source_identity": "installed:other/fixture",
            "version": "2.0.0",
        }
        evidence["current_census_receipt"]["receipt"]["census"][
            "plugins"
        ].append(duplicate)
        self.rebind_plugin_gate_evidence(plugin, evidence)
        self.assert_blocked(
            module.evaluate_plugin_capability_gate(plugin, evidence),
            "current_census_plugin_settings_key_duplicated",
        )

    def test_chk04_requires_authoritative_census_and_receiver(self) -> None:
        mutations = {
            "current_census_receipt_not_authoritative": lambda evidence: evidence[
                "authority"
            ].__setitem__("current_receipt_sha256", "sha256:" + ("f" * 64)),
            "current_census_host_mismatch": lambda evidence: evidence[
                "authority"
            ].__setitem__("expected_census_host_id", "other-host"),
            "current_census_receiver_mismatch": lambda evidence: evidence[
                "authority"
            ]["expected_receiver"].__setitem__("receiver_id", "other-receiver"),
            "current_census_authority_malformed": lambda evidence: evidence[
                "authority"
            ]["expected_receiver"].__setitem__("receiver_sha256", "not-a-hash"),
        }
        for reason, mutate in mutations.items():
            with self.subTest(reason=reason):
                plugin = self.plugin_gate_fixture()
                evidence = self.plugin_gate_evidence(plugin)
                mutate(evidence)
                self.assert_blocked(
                    module.evaluate_plugin_capability_gate(plugin, evidence),
                    reason,
                )

    def test_chk04_requires_canonical_complete_census_contexts(self) -> None:
        mutations = (
            lambda census: census.pop("totals"),
            lambda census: census["contexts"][0].__setitem__(
                "complete", False
            ),
            lambda census: census["scope"].__setitem__(
                "outside_context_ids", ["unregistered-project"]
            ),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                plugin = self.plugin_gate_fixture()
                evidence = self.plugin_gate_evidence(plugin)
                census = evidence["current_census_receipt"]["receipt"][
                    "census"
                ]
                mutate(census)
                self.rebind_plugin_gate_evidence(plugin, evidence)
                self.assert_blocked(
                    module.evaluate_plugin_capability_gate(plugin, evidence),
                    "current_census_malformed",
                )

    def test_chk04_rejects_census_receipt_and_snapshot_tampering(self) -> None:
        plugin = self.plugin_gate_fixture()
        evidence = self.plugin_gate_evidence(plugin)
        evidence["current_census_receipt"]["receipt_sha256"] = (
            "sha256:" + ("f" * 64)
        )
        result = module.evaluate_plugin_capability_gate(plugin, evidence)
        self.assert_blocked(result, "current_census_receipt_malformed")
        self.assertIsNone(result["capability_inventory_sha256"])

        evidence = self.plugin_gate_evidence(plugin)
        evidence["current_census_receipt"]["receipt"]["census"][
            "host_id"
        ] = "tampered-host"
        self.assert_blocked(
            module.evaluate_plugin_capability_gate(plugin, evidence),
            "current_census_receipt_malformed",
        )

    def test_chk04_binds_capability_and_dependency_evidence(self) -> None:
        mutations = {
            "capability_evidence_plugin_mismatch": lambda evidence: evidence[
                "capability_evaluations"
            ][0].__setitem__("plugin_identity_sha256", "sha256:" + ("f" * 64)),
            "capability_evidence_inventory_mismatch": lambda evidence: evidence[
                "capability_evaluations"
            ][0].__setitem__(
                "capability_inventory_sha256", "sha256:" + ("f" * 64)
            ),
            "capability_evidence_census_mismatch": lambda evidence: evidence[
                "capability_evaluations"
            ][0].__setitem__("current_estate_sha256", "sha256:" + ("f" * 64)),
            "dependency_inventory_plugin_mismatch": lambda evidence: evidence[
                "dependency_inventory"
            ].__setitem__("plugin_identity_sha256", "sha256:" + ("f" * 64)),
            "dependency_inventory_census_mismatch": lambda evidence: evidence[
                "dependency_inventory"
            ].__setitem__("current_estate_sha256", "sha256:" + ("f" * 64)),
        }
        for reason, mutate in mutations.items():
            with self.subTest(reason=reason):
                plugin = self.plugin_gate_fixture()
                evidence = self.plugin_gate_evidence(plugin)
                mutate(evidence)
                self.assert_blocked(
                    module.evaluate_plugin_capability_gate(plugin, evidence),
                    reason,
                )

    def test_chk04_rejects_untyped_capability_binding_output(self) -> None:
        plugin = self.plugin_gate_fixture()
        evidence = self.plugin_gate_evidence(plugin)
        evidence["capability_evaluations"][0]["plugin_identity_sha256"] = {
            "bad": True
        }
        result = module.evaluate_plugin_capability_gate(plugin, evidence)
        self.assert_blocked(
            result, "capability_evaluation_binding_malformed"
        )
        malformed_id = evidence["capability_evaluations"][0]["capability_id"]
        self.assertNotIn(
            malformed_id,
            {
                evaluation["capability_id"]
                for evaluation in result["capability_evaluations"]
            },
        )

    def test_chk04_requires_complete_dependency_and_inventory_evidence(
        self,
    ) -> None:
        plugin = self.plugin_gate_fixture()
        evidence = self.plugin_gate_evidence(plugin)
        evidence["dependency_inventory"]["complete"] = False
        self.assert_blocked(
            module.evaluate_plugin_capability_gate(plugin, evidence),
            "dependency_inventory_incomplete",
        )

        plugin = self.plugin_gate_fixture()
        evidence = self.plugin_gate_evidence(plugin)
        plugin["capabilities"]["complete"] = False
        plugin["capabilities"]["inventory_errors"] = ["fixture failure"]
        self.rebind_plugin_gate_evidence(plugin, evidence)
        self.assert_blocked(
            module.evaluate_plugin_capability_gate(plugin, evidence),
            "capability_inventory_errors",
        )

    def test_chk04_reports_dependencies_even_with_other_malformed_classes(
        self,
    ) -> None:
        plugin = self.plugin_gate_fixture()
        evidence = self.plugin_gate_evidence(plugin)
        dependencies = evidence["dependency_inventory"]
        dependencies["pins"] = "malformed"
        dependencies["ambiguous"] = ["fixture dependency"]
        result = module.evaluate_plugin_capability_gate(plugin, evidence)
        self.assert_blocked(result, "dependency_pins_malformed")
        self.assertIn("plugin_has_dependencies", result["blocking_reasons"])
        self.assertEqual(
            result["dependency_inventory_sha256"],
            module.digest(dependencies),
        )

    def test_chk04_requires_verified_proposed_estate_preimage(self) -> None:
        plugin = self.plugin_gate_fixture()
        evidence = self.plugin_gate_evidence(plugin)
        evidence["proposed_estate"]["snapshot"][
            "removed_capability_ids"
        ] = []
        self.assert_blocked(
            module.evaluate_plugin_capability_gate(plugin, evidence),
            "proposed_estate_preimage_mismatch",
        )

    def test_chk04_proposed_estate_fail_closed_matrix(self) -> None:
        def set_current_mismatch(evidence: dict) -> None:
            evidence["proposed_estate"]["current_estate_sha256"] = (
                "sha256:" + ("f" * 64)
            )

        def set_removal_mismatch(evidence: dict) -> None:
            evidence["proposed_estate"]["removed_capability_ids"] = []

        def tamper_receipt(evidence: dict) -> None:
            evidence["proposed_estate"]["portfolio"]["sha256"] = (
                "sha256:" + ("f" * 64)
            )

        cases = {
            "proposed_estate_incomplete": lambda evidence: evidence[
                "proposed_estate"
            ].__setitem__("complete", False),
            "current_estate_census_mismatch": set_current_mismatch,
            "proposed_estate_removal_mismatch": set_removal_mismatch,
            "proposed_estate_portfolio_failed": tamper_receipt,
            "proposed_estate_routing_failed": lambda evidence: evidence[
                "proposed_estate"
            ]["routing"].__setitem__("status", "failed"),
            "proposed_estate_plugin_mismatch": lambda evidence: evidence[
                "proposed_estate"
            ].__setitem__("plugin_identity_sha256", "sha256:" + ("f" * 64)),
            "proposed_estate_inventory_mismatch": lambda evidence: evidence[
                "proposed_estate"
            ].__setitem__(
                "capability_inventory_sha256", "sha256:" + ("f" * 64)
            ),
        }
        for reason, mutate in cases.items():
            with self.subTest(reason=reason):
                plugin = self.plugin_gate_fixture()
                evidence = self.plugin_gate_evidence(plugin)
                mutate(evidence)
                self.assert_blocked(
                    module.evaluate_plugin_capability_gate(plugin, evidence),
                    reason,
                )

    def test_chk04_decision_receipts_bind_every_identity(self) -> None:
        fields = (
            "plugin_identity_sha256",
            "current_estate_sha256",
            "proposed_estate_sha256",
            "removed_capability_ids",
        )
        for kind in ("routing", "portfolio"):
            for field in fields:
                with self.subTest(kind=kind, field=field):
                    plugin = self.plugin_gate_fixture()
                    evidence = self.plugin_gate_evidence(plugin)
                    receipt = evidence["proposed_estate"][kind]
                    payload = receipt["payload"]
                    payload[field] = (
                        []
                        if field == "removed_capability_ids"
                        else "sha256:" + ("f" * 64)
                    )
                    receipt["sha256"] = module.digest(payload)
                    self.assert_blocked(
                        module.evaluate_plugin_capability_gate(
                            plugin, evidence
                        ),
                        f"proposed_estate_{kind}_failed",
                    )

    def test_chk04_malformed_estate_hashes_do_not_leak_types(self) -> None:
        plugin = self.plugin_gate_fixture()
        evidence = self.plugin_gate_evidence(plugin)
        evidence["proposed_estate"]["current_estate_sha256"] = {"bad": True}
        evidence["proposed_estate"]["proposed_estate_sha256"] = 42
        result = module.evaluate_plugin_capability_gate(plugin, evidence)
        self.assert_blocked(result, "current_estate_identity_malformed")
        self.assertIn(
            "proposed_estate_identity_malformed", result["blocking_reasons"]
        )
        self.assertIsNone(result["current_estate_sha256"])
        self.assertIsNone(result["proposed_estate_sha256"])

    def test_chk04_report_only_cli_emits_evaluation(self) -> None:
        plugin = self.plugin_gate_fixture()
        request = self.case / "plugin-evaluation.json"
        request.write_text(
            json.dumps(
                {
                    "plugin": plugin,
                    "evidence": self.plugin_gate_evidence(plugin),
                }
            ),
            encoding="utf-8",
        )
        before = {
            path.relative_to(self.case).as_posix(): path.read_bytes()
            for path in self.case.rglob("*")
            if path.is_file()
        }
        result = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "evaluate-plugin",
                "--input",
                str(request),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output["ok"])
        self.assertTrue(output["evaluation"]["eligible_for_disablement"])
        self.assertNotIn("census", output)
        after = {
            path.relative_to(self.case).as_posix(): path.read_bytes()
            for path in self.case.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)

    def test_chk04_mixed_valuable_plugin_stays_enabled(self) -> None:
        plugin = self.plugin_gate_fixture()
        evidence = self.plugin_gate_evidence(plugin)
        skill = next(
            item
            for item in evidence["capability_evaluations"]
            if item["capability_id"].startswith("skills:")
        )
        skill["disposition"] = "valuable"
        result = module.evaluate_plugin_capability_gate(plugin, evidence)
        self.assert_blocked(result, "capability_retained_or_unknown")

    def test_chk04_unknown_non_skill_capability_stays_enabled(self) -> None:
        plugin = self.plugin_gate_fixture()
        evidence = self.plugin_gate_evidence(plugin)
        agent = next(
            item
            for item in evidence["capability_evaluations"]
            if item["capability_id"].startswith("agents:")
        )
        agent["evidence_complete"] = False
        result = module.evaluate_plugin_capability_gate(plugin, evidence)
        self.assert_blocked(result, "capability_evidence_incomplete")

    def test_chk04_unknown_capability_metadata_stays_enabled(self) -> None:
        plugin = self.plugin_gate_fixture()
        evidence = self.plugin_gate_evidence(plugin)
        plugin["capabilities"]["complete"] = False
        plugin["capabilities"]["unknown_metadata"] = ["commands"]
        self.rebind_plugin_gate_evidence(plugin, evidence)
        result = module.evaluate_plugin_capability_gate(plugin, evidence)
        self.assert_blocked(result, "unknown_capability_metadata")

    def test_chk04_missing_capability_inventory_fields_stay_enabled(self) -> None:
        for field in ("unknown_metadata", "inventory_errors"):
            with self.subTest(field=field):
                plugin = self.plugin_gate_fixture()
                evidence = self.plugin_gate_evidence(plugin)
                del plugin["capabilities"][field]
                self.rebind_plugin_gate_evidence(plugin, evidence)
                result = module.evaluate_plugin_capability_gate(plugin, evidence)
                self.assert_blocked(result, "capability_inventory_malformed")

    def test_chk04_plugin_must_resolve_from_current_census(self) -> None:
        plugin = self.plugin_gate_fixture()
        evidence = self.plugin_gate_evidence(plugin)
        receipt = evidence["current_census_receipt"]["receipt"]
        receipt["census"]["plugins"] = []
        self.rebind_plugin_gate_evidence(plugin, evidence)
        result = module.evaluate_plugin_capability_gate(plugin, evidence)
        self.assert_blocked(result, "current_census_plugin_unresolved")

    def test_chk04_malformed_census_scope_stays_enabled(self) -> None:
        for malformed_scope in (None, []):
            with self.subTest(malformed_scope=malformed_scope):
                plugin = self.plugin_gate_fixture()
                evidence = self.plugin_gate_evidence(plugin)
                receipt = evidence["current_census_receipt"]["receipt"]
                receipt["census"]["scope"] = malformed_scope
                self.rebind_plugin_gate_evidence(plugin, evidence)
                result = module.evaluate_plugin_capability_gate(plugin, evidence)
                self.assert_blocked(result, "current_census_malformed")

    def test_chk04_explicit_dependency_stays_enabled(self) -> None:
        plugin = self.plugin_gate_fixture()
        evidence = self.plugin_gate_evidence(plugin)
        evidence["dependency_inventory"]["explicit_dependencies"] = [
            "skill:consumer"
        ]
        result = module.evaluate_plugin_capability_gate(plugin, evidence)
        self.assert_blocked(result, "plugin_has_dependencies")

    def test_chk04_pin_and_runtime_dependency_classes_stay_enabled(self) -> None:
        for dependency_class in module.PLUGIN_DEPENDENCY_CLASSES[1:]:
            with self.subTest(dependency_class=dependency_class):
                plugin = self.plugin_gate_fixture()
                evidence = self.plugin_gate_evidence(plugin)
                evidence["dependency_inventory"][dependency_class] = [
                    f"{dependency_class}:fixture"
                ]
                result = module.evaluate_plugin_capability_gate(plugin, evidence)
                self.assert_blocked(result, "plugin_has_dependencies")

    def test_chk04_failing_proposed_estate_stays_enabled(self) -> None:
        plugin = self.plugin_gate_fixture()
        evidence = self.plugin_gate_evidence(plugin)
        evidence["proposed_estate"]["portfolio"]["status"] = "failed"
        result = module.evaluate_plugin_capability_gate(plugin, evidence)
        self.assert_blocked(result, "proposed_estate_portfolio_failed")

    def test_plugin_list_parser_fails_closed(self) -> None:
        self.assertEqual(
            module.installed_plugin_names(
                "Installed plugins:\n  • deep@deep (v1.0.0)\n"
            ),
            {"deep@deep"},
        )
        with self.assertRaises(module.EstateError):
            module.installed_plugin_names("Installed plugins:\n  unexpected\n")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EstateCensusTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    shutil.rmtree(WORK_ROOT, ignore_errors=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)
PY
