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
            ),
            self.root(
                "plugin-disabled-cache",
                "plugin",
                disabled,
                "plugin_managed",
                plugin_id="disabled@old",
                source_identity="github:owner/old",
                version="0.1.0",
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
                bundle_id="sha256:" + "a" * 64,
            ),
            self.root(
                "publisher-stale",
                "dreaming_publisher",
                stale,
                "dreaming_managed",
                bundle_id="sha256:" + "b" * 64,
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
