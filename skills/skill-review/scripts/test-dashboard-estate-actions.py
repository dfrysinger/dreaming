#!/usr/bin/env python3
"""Focused CHK-08 dashboard action-state contracts."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]


def load(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


dashboard = load(
    "dreaming_dashboard",
    REPO / "skills/skill-review/scripts/dreaming-dashboard.py",
)
estate_test = load(
    "estate_action_test_fixture",
    REPO / "scripts/test-estate-action.py",
)
estate_action = estate_test.module
passes = 0


def check(value: bool, message: str) -> None:
    global passes
    if not value:
        raise AssertionError(message)
    passes += 1
    print(f"PASS  {message}")


class Fixture:
    def __init__(self, root: Path, kind: str):
        self.action = estate_test.Fixture(root / "authority", kind)
        self.action.state.mkdir(parents=True, exist_ok=True)
        self.state = root / "dashboard-state"
        self.control = root / "control"
        self.orchestrator = root / "orchestrator"
        self.data = root / "data"
        self.skills = root / "skills"
        for path in (
            self.state,
            self.control,
            self.orchestrator,
            self.data,
            self.skills,
        ):
            path.mkdir(parents=True, exist_ok=True)
        config = self.state / "estate-action/config.json"
        config.parent.mkdir(parents=True)
        config.write_bytes(self.action.config_path.read_bytes())
        paths = dashboard.DashboardPaths(
            self.state,
            self.control,
            self.orchestrator,
            self.data,
            self.skills,
            REPO,
            REPO / "skills/skill-review/assets/dashboard",
            self.control / "dashboard/access-token",
        )
        self.service = dashboard.DashboardData(paths)

    def actions(self) -> dict[str, object]:
        return self.service._estate_actions()

    def dispatch(self) -> None:
        self.action.dispatch()

    def persist_running(self) -> None:
        operation = self.action.state / self.action.authorization["action_id"]
        operation.mkdir(parents=True)
        (operation / "authorization.json").write_text(
            json.dumps(self.action.authorization), encoding="utf-8"
        )
        (operation / "index.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "action_id": self.action.authorization["action_id"],
                    "authorization_sha256": self.action.authorization[
                        "authorization_sha256"
                    ],
                    "phase": "running",
                }
            ),
            encoding="utf-8",
        )

    def advance_model_authority(self) -> None:
        evidence = copy.deepcopy(self.action.evidence)
        evidence["model"]["value"]["model_sha256"] = "sha256:" + "f" * 64
        evidence["model"]["sha256"] = estate_action.digest(
            evidence["model"]["value"]
        )
        self.action._write_authority(evidence)

    def write_decisions(self) -> None:
        records = []
        for action_id, target, authority, decision, status in (
            (
                "protected-fixture",
                "human-skill",
                "user_protected",
                "keep",
                "protected",
            ),
            (
                "unknown-fixture",
                "mystery-skill",
                "unknown_provenance",
                "investigate",
                "unknown",
            ),
        ):
            payload = {
                "action_id": action_id,
                "target": target,
                "authority": authority,
                "decision": decision,
                "status": status,
                "target_kind": "personal_skill",
                "at": "2026-08-13T12:00:00Z",
            }
            records.append(
                {**payload, "record_sha256": dashboard.sha(payload)}
            )
        (self.state / "estate-action-ledger.json").write_text(
            json.dumps(records), encoding="utf-8"
        )


def fixture(kind: str) -> tuple[tempfile.TemporaryDirectory[str], Fixture]:
    temporary = tempfile.TemporaryDirectory(
        prefix="dashboard-estate-actions-",
        dir=REPO / ".test-work",
    )
    return temporary, Fixture(Path(temporary.name), kind)


(REPO / ".test-work").mkdir(exist_ok=True)

temporary, current = fixture("plugin_disable")
with temporary:
    current.dispatch()
    actions = current.actions()
    check(
        actions["status"] == "current"
        and actions["items"][0]["target"] == "fixture@market"
        and actions["items"][0]["receipt_sha256"].startswith("sha256:"),
        "verified plugin disable is current and exposes receipt identity",
    )
    check(
        "installed:market/fixture" not in json.dumps(actions),
        "action reporting excludes raw executor request payloads",
    )

for kind in ("plugin_restore", "personal_archive"):
    temporary, current = fixture(kind)
    with temporary:
        current.dispatch()
        item = current.actions()["items"][0]
        check(
            item["kind"] == kind and item["status"] == "committed",
            f"{kind.replace('_', ' ')} is reported from a verified receipt",
        )

temporary, current = fixture("plugin_disable")
with temporary:
    current.persist_running()
    current.advance_model_authority()
    actions = current.actions()
    check(
        actions["status"] == "stale"
        and actions["items"][0]["stale"] is True
        and actions["writers_blocked"] is True,
        "unresolved action with drifted evidence is stale and blocks writers",
    )

temporary, current = fixture("plugin_disable")
with temporary:
    current.dispatch()
    current.advance_model_authority()
    actions = current.actions()
    check(
        actions["status"] == "current"
        and actions["items"][0]["evidence_state"] == "historical",
        "completed action remains healthy when later authority advances",
    )

temporary, current = fixture("plugin_disable")
with temporary:
    current.dispatch()
    result = next(
        (current.action.state / "plugin_disable-fixture/results").iterdir()
    )
    value = json.loads(result.read_text(encoding="utf-8"))
    value["receipt"]["adapter_count"] = 999
    result.chmod(0o600)
    result.write_text(json.dumps(value), encoding="utf-8")
    check(
        current.actions()["status"] == "invalid",
        "tampered action receipt makes reporting explicitly invalid",
    )

temporary, current = fixture("plugin_disable")
with temporary:
    current.action.state.mkdir(parents=True, exist_ok=True)
    (current.action.state / "recovery-required.json").write_text(
        "{}", encoding="utf-8"
    )
    actions = current.actions()
    check(
        actions["status"] == "recovery required"
        and actions["writers_blocked"] is True,
        "recovery fence is conspicuous and blocks writers",
    )

temporary, current = fixture("plugin_disable")
with temporary:
    current.write_decisions()
    statuses = {item["status"] for item in current.actions()["items"]}
    check(
        statuses == {"protected", "unknown"},
        "protected and unknown targets remain explicit recommendations",
    )

temporary, current = fixture("plugin_disable")
with temporary:
    config = current.state / "estate-action/config.json"
    config.write_text("{}", encoding="utf-8")
    actions = current.actions()
    check(
        actions["status"] == "invalid"
        and actions["writers_blocked"] is True,
        "malformed authority configuration is invalid rather than HTTP-fatal",
    )

for gate, expected in (("halt", "halted"), ("pause", "paused")):
    temporary, current = fixture("plugin_disable")
    with temporary:
        if gate == "halt":
            current.action.halt.write_text("", encoding="utf-8")
        else:
            current.action.curator_state.write_text(
                '{"paused":true}', encoding="utf-8"
            )
        actions = current.actions()
        check(
            actions["status"] == expected
            and actions["writers_blocked"] is True,
            f"{gate} state is reported as a writer block",
        )

print(f"dashboard estate action checks passed: {passes}")
