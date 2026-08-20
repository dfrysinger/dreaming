#!/usr/bin/env python3
"""Deterministic checks for remote evaluation-subject publication."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = load_module("dreaming_core_remote_subject", SCRIPT_DIR / "dreaming-core.py")
estate = load_module("dreaming_estate_remote_subject", SCRIPT_DIR / "dreaming-estate.py")
evaluation = load_module(
    "skill_evaluation_remote_subject", SCRIPT_DIR / "skill-evaluation.py"
)
dashboard = load_module(
    "dreaming_dashboard_remote_subject", SCRIPT_DIR / "dreaming-dashboard.py"
)
POLICY = SCRIPT_DIR.parent / "references" / "remote-subject-content-policy-v1.json"


class RemoteSubjectTest(unittest.TestCase):
    def setUp(self) -> None:
        test_root = Path(__file__).resolve().parents[3] / ".test-work"
        test_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="remote-subject.", dir=test_root
        )
        self.root = Path(self.temporary.name)
        self.previous_state = os.environ.get("SKILLS_STATE_DIR")
        os.environ["SKILLS_STATE_DIR"] = str(self.root / "claim-state")
        self.skill = self.root / "origin" / "fixture-skill"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text(
            "---\nname: fixture-skill\ndescription: fixture\n---\n"
            "Discuss transcript handling under /Users/example safely.\n",
            encoding="utf-8",
        )
        (self.skill / ".agent-created.json").write_text(
            '{"excluded":true}\n', encoding="utf-8"
        )
        self.store = self.root / "state" / "remote-subjects"
        self.store.mkdir(parents=True, mode=0o700)
        os.chmod(self.store, 0o700)
        self.installed = self.root / "installed"
        self.installed.mkdir()

    def tearDown(self) -> None:
        if self.previous_state is None:
            os.environ.pop("SKILLS_STATE_DIR", None)
        else:
            os.environ["SKILLS_STATE_DIR"] = self.previous_state
        self.temporary.cleanup()

    def response(
        self, *, policy_path: Path = POLICY
    ) -> tuple[dict, dict[str, str], dict[str, str]]:
        files, inventory_sha = estate.skill_inventory(self.skill)
        request = {
            "census_snapshot_sha256": "sha256:" + "a" * 64,
            "origin_host_id": "macbook",
            "origin_root_id": "personal-copilot",
            "origin_relative_path": "fixture-skill",
            "origin_path": str(self.skill),
            "canonical_capability_id": "sha256:" + "b" * 64,
            "origin_inventory_sha256": inventory_sha,
        }
        census = {
            "host_id": "macbook",
            "physical_instances": [
                {
                    "host_id": request["origin_host_id"],
                    "root_id": request["origin_root_id"],
                    "relative_path": request["origin_relative_path"],
                    "absolute_path": request["origin_path"],
                    "canonical_capability_id": request[
                        "canonical_capability_id"
                    ],
                    "inventory_sha256": inventory_sha,
                    "files": files,
                }
            ],
        }
        policy = estate.remote_subject_content_policy(policy_path)
        subject = estate.export_remote_subject(census, request, policy)
        receiver = {
            "receiver_id": "fixture",
            "receiver_sha256": "1" * 64,
            "collector_sha256": "2" * 64,
            "content_policy_sha256": policy["sha256"].removeprefix("sha256:"),
        }
        return {"ok": True, "receiver": receiver, "subject": subject}, request, receiver

    def test_publishes_exact_read_only_snapshot_and_replays(self) -> None:
        response, request, receiver = self.response()
        result = core.publish_remote_subject_snapshot(
            response,
            request,
            receiver,
            POLICY,
            self.store,
            installed_skill_roots=[self.installed],
        )
        self.assertEqual(result["status"], "published")
        candidate = Path(result["candidate_root"])
        self.assertEqual(
            (candidate / "SKILL.md").read_bytes(),
            (self.skill / "SKILL.md").read_bytes(),
        )
        self.assertFalse((candidate / ".agent-created.json").exists())
        self.assertEqual(
            os.stat(candidate / "SKILL.md").st_mode & 0o777,
            0o400,
        )
        self.assertEqual(os.stat(candidate).st_mode & 0o777, 0o500)
        replay = core.publish_remote_subject_snapshot(
            response,
            request,
            receiver,
            POLICY,
            self.store,
            installed_skill_roots=[self.installed],
        )
        self.assertEqual(replay["status"], "existing")
        self.assertEqual(replay["candidate_root"], result["candidate_root"])
        binding = evaluation.remote_evaluation_subject(candidate)
        self.assertEqual(binding["subject_key"], result["subject_key"])
        self.assertEqual(
            evaluation.latest_key(str(candidate)),
            result["subject_key"].removeprefix("sha256:"),
        )
        self.assertEqual(binding["origin_host_id"], "macbook")
        self.assertEqual(binding["origin_root_id"], "personal-copilot")
        self.assertEqual(binding["origin_relative_path"], "fixture-skill")
        claim = evaluation.reserve_claim(
            skill_path=str(candidate),
            skill_key=evaluation.latest_key(str(candidate)),
            candidate_id=binding["candidate_id"],
            subject=binding,
            owner_run_id="remote-subject-test-run",
            author_model="author-model",
            reviewer_a_model="reviewer-a-model",
            reviewer_b_model="reviewer-b-model",
        )
        self.assertEqual(claim["subject"], binding)
        self.assertEqual(
            claim["skill_key"], result["subject_key"].removeprefix("sha256:")
        )

    def test_evaluator_refuses_tampered_remote_snapshot(self) -> None:
        response, request, receiver = self.response()
        result = core.publish_remote_subject_snapshot(
            response,
            request,
            receiver,
            POLICY,
            self.store,
            installed_skill_roots=[self.installed],
        )
        candidate = Path(result["candidate_root"])
        skill_file = candidate / "SKILL.md"
        os.chmod(candidate.parent, 0o700)
        os.chmod(candidate, 0o700)
        os.chmod(skill_file, 0o600)
        skill_file.write_text(
            "---\nname: fixture-skill\ndescription: changed\n---\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            evaluation.EvaluationError,
            "candidate identity is stale",
        ):
            evaluation.latest_key(str(candidate))

    def test_remote_subject_refuses_legacy_authority_and_bad_inventory_id(
        self,
    ) -> None:
        response, request, receiver = self.response()
        published = core.publish_remote_subject_snapshot(
            response,
            request,
            receiver,
            POLICY,
            self.store,
            installed_skill_roots=[self.installed],
        )
        candidate = Path(published["candidate_root"])
        with self.assertRaisesRegex(
            evaluation.EvaluationError, "subject-bound v2 evaluation"
        ):
            evaluation.prepare(
                argparse.Namespace(
                    skill_dir=str(candidate),
                    run_dir=str(self.root / "legacy-run"),
                    plugin_dir=str(self.root / "legacy-plugin"),
                    cases=None,
                    model="fixture-model",
                )
            )
        for command, arguments in (
            (
                evaluation.gate,
                argparse.Namespace(skill_dir=str(candidate)),
            ),
            (
                evaluation.waive,
                argparse.Namespace(
                    skill_dir=str(candidate),
                    base_receipt=str(self.root / "missing"),
                ),
            ),
            (
                evaluation.current_gate,
                argparse.Namespace(skill_dir=str(candidate)),
            ),
        ):
            with self.assertRaisesRegex(
                evaluation.EvaluationError, "subject-bound v2 evaluation"
            ):
                command(arguments)
        run_dir = self.root / "legacy-finalize"
        run_dir.mkdir()
        (run_dir / "metadata.json").write_text(
            json.dumps({"skill_path": str(candidate)}), encoding="utf-8"
        )
        with self.assertRaisesRegex(
            evaluation.EvaluationError, "subject-bound v2 evaluation"
        ):
            evaluation.finalize(argparse.Namespace(run_dir=str(run_dir)))

        receipt_path = candidate.parent / "transport-receipt.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt["subject"]["origin_inventory_sha256"] = "not-a-sha256"
        receipt_identity = {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
        receipt["receipt_sha256"] = "sha256:" + evaluation.digest(
            evaluation.canonical(receipt_identity)
        )
        os.chmod(candidate.parent, 0o700)
        os.chmod(receipt_path, 0o600)
        receipt_path.write_bytes(evaluation.canonical(receipt))
        with self.assertRaisesRegex(
            evaluation.EvaluationError, "subject identity is invalid"
        ):
            evaluation.remote_evaluation_subject(candidate)

    def test_builds_current_bootstrap_and_changed_overlay_rows(self) -> None:
        response, request, receiver = self.response()
        published = core.publish_remote_subject_snapshot(
            response,
            request,
            receiver,
            POLICY,
            self.store,
            installed_skill_roots=[self.installed],
        )
        capability_id = request["canonical_capability_id"]
        physical = {
            "instance_id": "fixture-instance",
            "host_id": request["origin_host_id"],
            "root_id": request["origin_root_id"],
            "relative_path": request["origin_relative_path"],
            "absolute_path": request["origin_path"],
            "canonical_capability_id": capability_id,
            "inventory_sha256": request["origin_inventory_sha256"],
        }
        census = {
            "schema_version": 1,
            "host_id": request["origin_host_id"],
            "physical_instances": [physical],
            "enabled_instances": [
                {
                    "instance_id": physical["instance_id"],
                    "canonical_capability_id": capability_id,
                    "runtime_enabled": True,
                }
            ],
        }
        census["snapshot_sha256"] = core.digest(census)
        usage = {
            "schema_version": 1,
            "census_snapshot_sha256": census["snapshot_sha256"],
        }
        usage["snapshot_sha256"] = core.digest(usage)
        owner = {"evaluator": str(SCRIPT_DIR / "skill-evaluation.py")}
        overlay = core.build_remote_evaluation_overlay(
            owner,
            census,
            usage,
            receiver,
            census_receipt_sha256="sha256:" + "c" * 64,
            usage_receipt_sha256="sha256:" + "d" * 64,
            snapshot_store=self.store,
        )
        row = overlay["rows"][0]
        self.assertEqual(
            row["snapshot_state"], "remote_candidate_snapshot_ready"
        )
        self.assertEqual(row["candidate_id"], published["candidate_id"])
        self.assertEqual(row["content_path"], published["candidate_root"])
        self.assertEqual(row["evaluation"]["state"], "input_missing")
        overlay_store = self.root / "state" / "evaluation-input-overlays"
        overlay_path = core.publish_remote_evaluation_overlay(
            overlay, overlay_store
        )
        self.assertEqual(overlay_path.read_bytes(), core.canonical(overlay))
        self.assertEqual(
            core.publish_remote_evaluation_overlay(overlay, overlay_store),
            overlay_path,
        )
        self.assertEqual(overlay_path.stat().st_mode & 0o777, 0o400)
        self.assertFalse(
            (self.root / "state" / "evaluation-input-overlay-current.json").exists()
        )
        core.promote_remote_evaluation_overlay(
            overlay,
            overlay_store,
            census,
            usage,
            receiver,
            census_receipt_sha256=overlay["census_receipt_sha256"],
            usage_receipt_sha256=overlay["usage_receipt_sha256"],
            enabled_capability_ids={capability_id},
            transport_receiver=overlay["transport_receiver"],
        )
        pointer = core.read_json(
            self.root / "state" / "evaluation-input-overlay-current.json",
            None,
        )
        pointer_identity = {
            key: value
            for key, value in pointer.items()
            if key != "pointer_sha256"
        }
        self.assertEqual(pointer["overlay_sha256"], overlay["overlay_sha256"])
        self.assertEqual(
            pointer["census_receipt_sha256"],
            overlay["census_receipt_sha256"],
        )
        self.assertEqual(pointer["pointer_sha256"], core.digest(pointer_identity))
        (self.root / "state" / "adapters.json").write_text(
            json.dumps(
                {
                    "evaluation_input_owner": {"enabled": True},
                    "remote_evaluation_subjects": {
                        "enabled": True,
                        "protocol_version": 1,
                        "origin_host_id": census["host_id"],
                        "receiver": receiver,
                    },
                }
            ),
            encoding="utf-8",
        )
        paths = dashboard.DashboardPaths(
            state=self.root / "state",
            control_state=self.root / "control",
            review_state=self.root / "review",
            orchestrator_state=self.root / "orchestrator",
            data=self.root / "data",
            skills=self.root / "skills",
            repo=SCRIPT_DIR.parents[2],
            assets=SCRIPT_DIR.parent / "assets" / "dashboard",
            token=self.root / "token",
        )
        projected = dashboard.DashboardData(paths)._estate_remote_evaluation(
            census,
            {
                key: receiver[key]
                for key in (
                    "receiver_id",
                    "receiver_sha256",
                    "collector_sha256",
                )
            },
            overlay["census_receipt_sha256"],
            {
                "_snapshot_sha256": usage["snapshot_sha256"],
                "_receipt_sha256": overlay["usage_receipt_sha256"],
            },
        )
        self.assertEqual(projected["status"], "current")
        self.assertEqual(
            projected["_rows"][capability_id]["candidate_id"],
            published["candidate_id"],
        )

        empty_store = self.root / "state" / "empty-remote-subjects"
        empty_store.mkdir(mode=0o700)
        bootstrap = core.build_remote_evaluation_overlay(
            owner,
            census,
            usage,
            receiver,
            census_receipt_sha256="sha256:" + "c" * 64,
            usage_receipt_sha256="sha256:" + "d" * 64,
            snapshot_store=empty_store,
        )
        self.assertEqual(
            bootstrap["rows"][0]["snapshot_state"],
            "remote_candidate_not_fetched",
        )
        self.assertIsNone(bootstrap["rows"][0]["candidate_id"])
        transport_response = json.loads(json.dumps(response))
        transport_response["subject"]["census_snapshot_sha256"] = census[
            "snapshot_sha256"
        ]
        transport_response["subject"]["receipt_sha256"] = core.digest(
            {
                key: value
                for key, value in transport_response["subject"].items()
                if key != "receipt_sha256"
            }
        )
        fake_transport = self.root / "fake-transport.py"
        fake_transport.write_text(
            "import json\n"
            f"print(json.dumps({transport_response!r}, sort_keys=True, separators=(',', ':')))\n",
            encoding="utf-8",
        )
        os.chmod(fake_transport, 0o700)
        transport_row = {
            **bootstrap["rows"][0],
            "skill_path": request["origin_path"],
            "runnable_phase": "transport",
        }
        transport = core.execute_remote_subject_transport(
            {
                "enabled": True,
                "command": [sys.executable, str(fake_transport)],
                "receiver": receiver,
                "content_policy": str(POLICY),
                "snapshot_store": str(empty_store),
            },
            census,
            transport_row,
            core.RuntimePaths(
                state=self.root / "owner-state",
                data=self.root / "owner-data",
                skills=self.root / "owner-skills",
            ),
            halt_check=lambda: False,
            lease_check=lambda: True,
        )
        self.assertEqual(transport["status"], "published")
        self.assertEqual(
            transport["selected_capability_id"], capability_id
        )
        self.assertFalse(
            (
                self.root
                / "claim-state"
                / "dreaming"
                / "evaluation-input-claims.sqlite3"
            ).exists()
        )
        refused_store = self.root / "state" / "refused-remote-subjects"
        refused_store.mkdir(mode=0o700)
        refused_transport = self.root / "refused-transport.py"
        refused_transport.write_text(
            "import sys\nprint('receiver denied candidate', file=sys.stderr)\n"
            "raise SystemExit(2)\n",
            encoding="utf-8",
        )
        os.chmod(refused_transport, 0o700)
        refused = core.execute_remote_subject_transport(
            {
                "enabled": True,
                "command": [sys.executable, str(refused_transport)],
                "receiver": receiver,
                "content_policy": str(POLICY),
                "snapshot_store": str(refused_store),
            },
            census,
            transport_row,
            core.RuntimePaths(
                state=self.root / "refused-owner-state",
                data=self.root / "refused-owner-data",
                skills=self.root / "refused-owner-skills",
            ),
            halt_check=lambda: False,
            lease_check=lambda: True,
        )
        self.assertEqual(refused["status"], "refused")
        self.assertTrue(refused["refusal_receipt_sha256"].startswith("sha256:"))
        refused_overlay = core.build_remote_evaluation_overlay(
            owner,
            census,
            usage,
            receiver,
            census_receipt_sha256="sha256:" + "c" * 64,
            usage_receipt_sha256="sha256:" + "d" * 64,
            snapshot_store=refused_store,
        )
        refused_row = refused_overlay["rows"][0]
        self.assertEqual(
            refused_row["snapshot_state"], "remote_candidate_refused"
        )
        self.assertEqual(
            refused_row["snapshot_refusal"]["receipt_sha256"],
            refused["refusal_receipt_sha256"],
        )
        self.assertIn(
            "receiver denied candidate",
            refused_row["snapshot_refusal"]["message"],
        )
        dashboard_row = dashboard.DashboardData._apply_remote_evaluation(
            [
                {
                    **physical,
                    "evaluation": {"state": "missing"},
                    "evaluation_complete": False,
                }
            ],
            {
                "configured": True,
                "origin_host_id": request["origin_host_id"],
                "origin_host": "MacBook",
                "execution_host": "Mac mini",
                "_rows": {capability_id: refused_row},
            },
        )[0]
        self.assertEqual(
            dashboard_row["remote_evaluation"]["refusal_reason"],
            "The origin computer refused or could not provide a safe copy.",
        )
        race_store = self.root / "state" / "race-remote-subjects"
        race_store.mkdir(mode=0o700)
        race = core.execute_remote_subject_transport(
            {
                "enabled": True,
                "command": [sys.executable, str(fake_transport)],
                "receiver": receiver,
                "content_policy": str(POLICY),
                "snapshot_store": str(race_store),
            },
            census,
            transport_row,
            core.RuntimePaths(
                state=self.root / "race-owner-state",
                data=self.root / "race-owner-data",
                skills=self.root / "race-owner-skills",
            ),
            halt_check=lambda: False,
            lease_check=lambda: not any(
                race_store.rglob("transport-receipt.json")
            ),
        )
        self.assertEqual(race["status"], "lock_lost")
        self.assertTrue(any(race_store.rglob("transport-receipt.json")))
        self.assertFalse(
            (
                self.root
                / "race-owner-state"
                / "evaluation-input-overlays"
            ).exists()
        )

        slow_transport = self.root / "slow-transport.py"
        slow_transport.write_text(
            "import time\ntime.sleep(30)\n", encoding="utf-8"
        )
        os.chmod(slow_transport, 0o700)
        halt_checks = iter([False, True])
        halted_store = self.root / "state" / "halted-remote-subjects"
        halted_store.mkdir(mode=0o700)
        halted = core.execute_remote_subject_transport(
            {
                "enabled": True,
                "command": [sys.executable, str(slow_transport)],
                "receiver": receiver,
                "content_policy": str(POLICY),
                "snapshot_store": str(halted_store),
            },
            census,
            transport_row,
            core.RuntimePaths(
                state=self.root / "halted-owner-state",
                data=self.root / "halted-owner-data",
                skills=self.root / "halted-owner-skills",
            ),
            halt_check=lambda: next(halt_checks, True),
            lease_check=lambda: True,
        )
        self.assertEqual(halted["status"], "halted")
        self.assertFalse(any(halted_store.iterdir()))

        changed_census = json.loads(json.dumps(census))
        changed_census["physical_instances"][0]["canonical_capability_id"] = (
            "sha256:" + "f" * 64
        )
        changed_census["physical_instances"][0]["inventory_sha256"] = (
            "changed-inventory"
        )
        changed_census["enabled_instances"][0][
            "canonical_capability_id"
        ] = "sha256:" + "f" * 64
        changed_census["snapshot_sha256"] = core.digest(
            {
                key: value
                for key, value in changed_census.items()
                if key != "snapshot_sha256"
            }
        )
        changed_usage = {
            "schema_version": 1,
            "census_snapshot_sha256": changed_census["snapshot_sha256"],
        }
        changed_usage["snapshot_sha256"] = core.digest(changed_usage)
        changed = core.build_remote_evaluation_overlay(
            owner,
            changed_census,
            changed_usage,
            receiver,
            census_receipt_sha256="sha256:" + "1" * 64,
            usage_receipt_sha256="sha256:" + "2" * 64,
            snapshot_store=self.store,
        )
        changed_row = changed["rows"][0]
        self.assertEqual(
            changed_row["snapshot_state"], "remote_candidate_changed"
        )
        self.assertEqual(
            changed_row["superseded_candidate_ids"],
            [published["candidate_id"]],
        )
        self.assertIsNone(changed_row["content_path"])

    def test_local_policy_rejects_content_accepted_by_stale_receiver(self) -> None:
        permissive = self.root / "permissive-policy.json"
        permissive.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "remote_subject_content_policy",
                    "denied_patterns": [
                        {"label": "never", "pattern": "DO_NOT_MATCH"}
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        (self.skill / "references").mkdir()
        (self.skill / "references" / "secret.txt").write_text(
            "authorization: bearer abcdefghijklmnop\n",
            encoding="utf-8",
        )
        response, request, receiver = self.response(policy_path=permissive)
        with self.assertRaisesRegex(
            core.RuntimeFailure, "remote-candidate-content-unsafe"
        ):
            core.publish_remote_subject_snapshot(
                response,
                request,
                receiver,
                POLICY,
                self.store,
                installed_skill_roots=[self.installed],
            )
        self.assertEqual(list(self.store.iterdir()), [])

    def test_store_overlap_and_capacity_refuse_before_publication(self) -> None:
        response, request, receiver = self.response()
        with self.assertRaisesRegex(
            core.RuntimeFailure, "remote-candidate-store-invalid"
        ):
            core.publish_remote_subject_snapshot(
                response,
                request,
                receiver,
                POLICY,
                self.store,
                installed_skill_roots=[self.root],
            )
        original_limit = core.REMOTE_SUBJECT_STORE_MAX_BYTES
        core.REMOTE_SUBJECT_STORE_MAX_BYTES = 1
        try:
            with self.assertRaisesRegex(
                core.RuntimeFailure, "remote-candidate-store-full"
            ):
                core.publish_remote_subject_snapshot(
                    response,
                    request,
                    receiver,
                    POLICY,
                    self.store,
                    installed_skill_roots=[self.installed],
                )
        finally:
            core.REMOTE_SUBJECT_STORE_MAX_BYTES = original_limit
        self.assertEqual(list(self.store.iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
