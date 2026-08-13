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
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_plugin_capabilities_cover_non_skill_surfaces(self) -> None:
        plugin = self.case / "plugin"
        self.skill(plugin / "skills", "one")
        (plugin / "agents").mkdir()
        (plugin / "agents/reviewer.md").write_text("fixture", encoding="utf-8")
        (plugin / "hooks").mkdir()
        (plugin / "hooks/hooks.json").write_text("{}", encoding="utf-8")
        (plugin / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"fixture": {"command": "true"}}}),
            encoding="utf-8",
        )
        capabilities = module.plugin_capabilities(
            plugin,
            {
                "name": "fixture",
                "version": "1.0.0",
                "skills": ["./skills/one"],
                "agents": ["./agents"],
            },
        )
        self.assertTrue(capabilities["complete"])
        self.assertEqual(capabilities["skills"], ["./skills/one"])
        self.assertEqual(capabilities["agents"], ["./agents"])
        self.assertEqual(capabilities["hooks"], ["./hooks/hooks.json"])
        self.assertEqual(capabilities["mcp_servers"], [".mcp.json#fixture"])

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
