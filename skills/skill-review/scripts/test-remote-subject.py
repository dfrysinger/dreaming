#!/usr/bin/env python3
"""Deterministic checks for remote evaluation-subject publication."""

from __future__ import annotations

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
