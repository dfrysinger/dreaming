#!/usr/bin/env python3
"""Deterministic report-only dashboard preview contracts."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
TEST_ROOT = REPO / ".test-work"
TEST_ROOT.mkdir(exist_ok=True)


def load():
    specification = importlib.util.spec_from_file_location(
        "dreaming_dashboard_preview",
        REPO / "skills/skill-review/scripts/dreaming-dashboard.py",
    )
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


dashboard = load()
passes = 0


def check(value: bool, message: str) -> None:
    global passes
    if not value:
        raise AssertionError(message)
    passes += 1
    print(f"PASS  {message}")


def roots(root: Path) -> dict[str, Path]:
    result = {
        name: root / f"source-{name}"
        for name in dashboard.PREVIEW_ROOT_NAMES
    }
    for path in result.values():
        path.mkdir(parents=True)
        (path / "fixture.json").write_text(
            json.dumps({"root": path.name}), encoding="utf-8"
        )
    token = result["state"] / "dashboard" / "access-token"
    token.parent.mkdir()
    token.write_text("A" * 43 + "\n", encoding="ascii")
    token.chmod(0o600)
    return result


def capture(root: Path, source: dict[str, Path]) -> Path:
    destination = root / "preview"
    dashboard.capture_preview_snapshot(
        destination=destination,
        roots=source,
        lock_state=source["control_state"],
        next_eligible_at=time.time() + 3600,
    )
    return destination


with tempfile.TemporaryDirectory(prefix="dashboard-preview-", dir=TEST_ROOT) as raw:
    root = Path(raw)
    source = roots(root)
    snapshots = source["data"] / "snapshots"
    snapshots.mkdir()
    (snapshots / "included.json").write_text("{}", encoding="utf-8")
    dependency_bundle = source["data"] / "deps" / "bundles" / "fixture"
    dependency_bundle.mkdir(parents=True)
    (dependency_bundle / "ignored.json").write_text("{}", encoding="utf-8")
    (source["data"] / "deps" / "current").symlink_to(
        Path("bundles") / "fixture"
    )
    (source["control_state"] / "daemon.lock-copy").write_text(
        "keep", encoding="utf-8"
    )
    (source["state"] / "remote-publication-summary.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "committed",
                "receiver_id": "external-receiver",
                "receiver_sha256": "a" * 64,
                "adapter_sha256": "b" * 64,
                "descriptor": {"skills": ["external-config-sentinel"]},
            }
        ),
        encoding="utf-8",
    )
    (source["state"] / "adapters.json").write_text("{}", encoding="utf-8")
    remote_publication = source["state"] / "remote-publication.json"
    remote_publication.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "committed",
                "receiver_id": "external-receiver",
                "receiver_sha256": "a" * 64,
                "adapter_sha256": "b" * 64,
                "descriptor": {"skills": ["fixture-skill"]},
            }
        ),
        encoding="utf-8",
    )
    similarly_named = source["control_state"] / "other" / "daemon.lock"
    similarly_named.parent.mkdir()
    similarly_named.write_text("keep", encoding="utf-8")
    lock_observation = {"held": False}
    original_copy = dashboard._copy_snapshot_file

    def locked_copy(source_path: Path, destination: Path, deadline: float) -> None:
        if source_path == source["control_state"] / "fixture.json":
            contender = subprocess.run(
                [
                    sys.executable,
                    str(REPO / "skills/skill-review/scripts/daemon-lock.py"),
                    "acquire",
                    "--mode",
                    "session",
                    "--owner",
                    "preview-contender",
                ],
                env={
                    **os.environ,
                    "SKILLS_STATE_DIR": str(source["control_state"]),
                    "SKILLS_LOCK_DIR": str(source["control_state"] / "daemon.lock"),
                    "SKILLS_LOCK_NONBLOCKING": "1",
                },
                capture_output=True,
                text=True,
            )
            lock_observation["held"] = contender.returncode != 0
        original_copy(source_path, destination, deadline)

    dashboard._copy_snapshot_file = locked_copy
    try:
        preview = capture(root, source)
    finally:
        dashboard._copy_snapshot_file = original_copy
    paths = dashboard.DashboardPaths.preview(
        preview, REPO / "skills/skill-review/assets/dashboard"
    )
    check(
        all(
            getattr(paths, name).is_relative_to(preview)
            for name in dashboard.PREVIEW_ROOT_NAMES
        ),
        "preview paths stay inside the manifested private root",
    )
    check(
        paths.preview_root == preview and paths.preview_manifest == preview / dashboard.PREVIEW_MANIFEST_NAME,
        "preview mode never falls back to dashboard defaults",
    )
    alternate_assets = root / "alternate-assets"
    alternate_assets.mkdir()
    symlinked_assets = root / "symlinked-assets"
    symlinked_assets.symlink_to(
        REPO / "skills/skill-review/assets/dashboard", target_is_directory=True
    )
    for invalid_assets in (alternate_assets, symlinked_assets):
        try:
            dashboard.DashboardPaths.preview(preview, invalid_assets)
            raise AssertionError("non-worktree preview assets accepted")
        except dashboard.DashboardError:
            pass
    check(True, "preview assets must be real files from the executing worktree")
    manifest_paths = {
        item["path"]
        for item in json.loads(
            (preview / dashboard.PREVIEW_MANIFEST_NAME).read_text(encoding="utf-8")
        )["files"]
    }
    check(
        (preview / "data" / "snapshots" / "included.json").is_file()
        and not (preview / "data" / "deps").exists()
        and not any(path.startswith("data/deps/") for path in manifest_paths),
        "dependency cache symlink is excluded while dashboard snapshot data is captured",
    )
    lock_runtime_paths = {
        f"control_state/{name}" for name in dashboard.PREVIEW_LOCK_RUNTIME_NAMES
    }
    check(
        lock_observation["held"]
        and not any(path in manifest_paths for path in lock_runtime_paths)
        and not any((preview / path).exists() for path in lock_runtime_paths)
        and (preview / "control_state" / "daemon.lock-copy").read_text() == "keep"
        and (preview / "control_state" / "other" / "daemon.lock").read_text()
        == "keep",
        "capture excludes only its held writer lease and retains similarly named files",
    )
    derived = root / "derived-preview"
    dashboard.capture_preview_snapshot(
        destination=derived,
        roots={
            name: getattr(paths, name)
            for name in dashboard.PREVIEW_ROOT_NAMES
        },
        lock_state=root / "derived-lock-state",
        next_eligible_at=time.time() + 3600,
    )
    check(
        (derived / dashboard.PREVIEW_MANIFEST_NAME).is_file()
        and not (derived / "control_state" / "daemon.lock").exists(),
        "a lock-free snapshot can be captured again with a separate lease state",
    )
    dashboard.verify_preview_manifest(paths)
    external_config = root / "external-adapters.json"
    external_config.write_text(
        json.dumps(
            {
                "publishers": {
                    "copilot": {
                        "argv": [
                            "--expected-receiver-id",
                            "external-receiver",
                            "--expected-receiver-sha",
                            "a" * 64,
                            "--expected-adapter-sha",
                            "b" * 64,
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    prior_adapter_config = os.environ.get("DREAMING_ADAPTER_CONFIG")
    os.environ["DREAMING_ADAPTER_CONFIG"] = str(external_config)
    try:
        publication_targets = dashboard.DashboardData(paths)._publication_targets(
            paths.state / "missing-publication.json",
            paths.state / "remote-publication-summary.json",
        )
    finally:
        if prior_adapter_config is None:
            os.environ.pop("DREAMING_ADAPTER_CONFIG", None)
        else:
            os.environ["DREAMING_ADAPTER_CONFIG"] = prior_adapter_config
    check(
        publication_targets == {}
        and "external-config-sentinel" not in publication_targets,
        "preview publication targets ignore an external adapter configuration",
    )
    handler = object.__new__(dashboard.DashboardHandler)
    handler.path = "/api/v1/health"
    handler.data = dashboard.DashboardData(paths)
    handler._request_guard = lambda api: None
    handler._json_response = lambda result: None
    handler._dispatch()
    system_results = []
    handler.path = "/api/v1/system"
    handler._json_response = system_results.append
    handler._dispatch()
    check(
        len(system_results) == 1
        and "ignored.json" not in json.dumps(system_results[0])
        and not any(item["name"] == "Dependencies" for item in system_results[0]["categories"]),
        "preview APIs use only snapshot data and do not fall back to excluded dependencies",
    )
    handler.path = "/api/v1/health"
    handler._json_response = lambda result: None
    before_write_method = sorted(
        path.relative_to(preview).as_posix() for path in preview.rglob("*")
    )
    errors = []
    handler._error = errors.append
    handler._method_not_allowed()
    check(
        len(errors) == 1
        and errors[0].status == 405
        and sorted(path.relative_to(preview).as_posix() for path in preview.rglob("*"))
        == before_write_method,
        "preview write methods are 405 and create no snapshot state",
    )
    manifest = preview / dashboard.PREVIEW_MANIFEST_NAME
    original_manifest = manifest.read_bytes()
    manifest.write_text("{}", encoding="utf-8")
    try:
        dashboard.verify_preview_manifest(paths)
        raise AssertionError("tampered manifest accepted")
    except dashboard.DashboardError:
        check(True, "manifest tampering fails closed")
    manifest.write_bytes(original_manifest)
    changed = preview / "state" / "fixture.json"
    saved = changed.read_bytes()
    changed.unlink()
    changed.write_bytes(saved)
    try:
        handler._dispatch()
        raise AssertionError("replacement snapshot file accepted")
    except dashboard.DashboardError:
        check(True, "post-startup file replacement fails closed on the next API request")

with tempfile.TemporaryDirectory(prefix="dashboard-preview-", dir=TEST_ROOT) as raw:
    root = Path(raw)
    source = roots(root)
    included = source["data"] / "snapshots"
    included.mkdir()
    included.joinpath("escape").symlink_to(source["state"])
    try:
        capture(root, source)
        raise AssertionError("symlinked source accepted")
    except dashboard.DashboardError:
        check(True, "capture rejects source symlink escapes")

with tempfile.TemporaryDirectory(prefix="dashboard-preview-", dir=TEST_ROOT) as raw:
    root = Path(raw)
    source = roots(root)
    output_link = root / "output-link"
    output_link.symlink_to(source["state"], target_is_directory=True)
    try:
        dashboard.capture_preview_snapshot(
            destination=output_link / "preview",
            roots=source,
            lock_state=source["control_state"],
            next_eligible_at=time.time() + 3600,
        )
        raise AssertionError("symlinked destination parent accepted")
    except dashboard.DashboardError:
        check(
            not (source["state"] / "preview").exists(),
            "capture rejects an absent destination beneath a symlinked parent",
        )
    try:
        dashboard.capture_preview_snapshot(
            destination=source["state"] / "preview",
            roots=source,
            lock_state=source["control_state"],
            next_eligible_at=time.time() + 3600,
        )
        raise AssertionError("destination inside source accepted")
    except dashboard.DashboardError:
        check(True, "capture rejects an effective destination inside an input root")

with tempfile.TemporaryDirectory(prefix="dashboard-preview-", dir=TEST_ROOT) as raw:
    root = Path(raw)
    source = roots(root)
    included = source["data"] / "snapshots"
    included.mkdir()
    (included / "fixture.json").write_text("{}", encoding="utf-8")
    original_copy = dashboard._copy_snapshot_file

    def changing_copy(source_path: Path, destination: Path, deadline: float) -> None:
        original_copy(source_path, destination, deadline)
        included.joinpath("fixture.json").write_text(
            '{"changed":true}', encoding="utf-8"
        )

    dashboard._copy_snapshot_file = changing_copy
    try:
        try:
            capture(root, source)
            raise AssertionError("changed source accepted")
        except dashboard.DashboardError as error:
            check(
                error.code == "preview_capture_changed"
                and not (root / "preview").exists(),
                "capture rejects source changes and deletes the incomplete snapshot",
            )
    finally:
        dashboard._copy_snapshot_file = original_copy

with tempfile.TemporaryDirectory(prefix="dashboard-preview-", dir=TEST_ROOT) as raw:
    root = Path(raw)
    source = roots(root)
    original_run = dashboard.subprocess.run
    committed = {"value": False}

    def timeout_after_commit(command, *args, **kwargs):
        if "acquire" in command:
            result = original_run(command, *args, **kwargs)
            assert result.returncode == 0
            committed["value"] = True
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return original_run(command, *args, **kwargs)

    dashboard.subprocess.run = timeout_after_commit
    try:
        try:
            capture(root, source)
            raise AssertionError("ambiguous acquire accepted")
        except dashboard.DashboardError as error:
            check(
                error.code == "preview_capture_acquire_ambiguous"
                and committed["value"],
                "post-commit acquire timeout is reconciled and reported",
            )
    finally:
        dashboard.subprocess.run = original_run
    contender = subprocess.run(
        [
            sys.executable,
            str(REPO / "skills/skill-review/scripts/daemon-lock.py"),
            "acquire",
            "--mode",
            "session",
            "--owner",
            "post-timeout-contender",
        ],
        env={
            **os.environ,
            "SKILLS_STATE_DIR": str(source["control_state"]),
            "SKILLS_LOCK_DIR": str(source["control_state"] / "daemon.lock"),
            "SKILLS_LOCK_NONBLOCKING": "1",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(REPO / "skills/skill-review/scripts/daemon-lock.py"),
            "release",
            contender.stdout.strip(),
        ],
        env={
            **os.environ,
            "SKILLS_STATE_DIR": str(source["control_state"]),
            "SKILLS_LOCK_DIR": str(source["control_state"] / "daemon.lock"),
        },
        check=True,
    )
    check(True, "a subsequent contender acquires after ambiguous acquire reconciliation")

with tempfile.TemporaryDirectory(prefix="dashboard-preview-", dir=TEST_ROOT) as raw:
    root = Path(raw)
    source = roots(root)
    original_run = dashboard.subprocess.run
    committed = {"value": False}

    def failure_after_commit(command, *args, **kwargs):
        if "acquire" in command:
            result = original_run(command, *args, **kwargs)
            assert result.returncode == 0
            committed["value"] = True
            return subprocess.CompletedProcess(command, 2, "", "post-commit failure")
        return original_run(command, *args, **kwargs)

    dashboard.subprocess.run = failure_after_commit
    try:
        try:
            capture(root, source)
            raise AssertionError("failed acquire accepted")
        except dashboard.DashboardError as error:
            check(
                error.code == "preview_capture_locked"
                and committed["value"],
                "post-commit acquire failure is reconciled and reported",
            )
    finally:
        dashboard.subprocess.run = original_run
    contender = subprocess.run(
        [
            sys.executable,
            str(REPO / "skills/skill-review/scripts/daemon-lock.py"),
            "acquire",
            "--mode",
            "session",
            "--owner",
            "post-failure-contender",
        ],
        env={
            **os.environ,
            "SKILLS_STATE_DIR": str(source["control_state"]),
            "SKILLS_LOCK_DIR": str(source["control_state"] / "daemon.lock"),
            "SKILLS_LOCK_NONBLOCKING": "1",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(REPO / "skills/skill-review/scripts/daemon-lock.py"),
            "release",
            contender.stdout.strip(),
        ],
        env={
            **os.environ,
            "SKILLS_STATE_DIR": str(source["control_state"]),
            "SKILLS_LOCK_DIR": str(source["control_state"] / "daemon.lock"),
        },
        check=True,
    )
    check(True, "a subsequent contender acquires after failed acquire reconciliation")

release_attempts = {"count": 0}
original_run = dashboard.subprocess.run

def failed_release(command, *args, **kwargs):
    if "release" in command:
        release_attempts["count"] += 1
        return subprocess.CompletedProcess(command, 2, "", "lock failure")
    return original_run(command, *args, **kwargs)

dashboard.subprocess.run = failed_release
try:
    try:
        dashboard._release_preview_lock(
            REPO / "skills/skill-review/scripts/daemon-lock.py",
            "12345678-1234-4234-9234-123456789abc",
            {},
            time.monotonic() + 3,
        )
        raise AssertionError("release failure accepted")
    except dashboard.DashboardError as error:
        check(
            error.code == "preview_capture_release_failed"
            and release_attempts["count"] == 2,
            "release status failure is retried and surfaced",
        )
finally:
    dashboard.subprocess.run = original_run

with tempfile.TemporaryDirectory(prefix="dashboard-preview-", dir=TEST_ROOT) as raw:
    root = Path(raw)
    source = roots(root)
    (source["state"] / "dreaming-run-active.json").write_text(
        '{"active":true}', encoding="utf-8"
    )
    try:
        capture(root, source)
        raise AssertionError("active run accepted")
    except dashboard.DashboardError as error:
        check(
            error.code == "preview_capture_active"
            and not (root / "preview").exists(),
            "capture refuses an active run before copying",
        )

with tempfile.TemporaryDirectory(prefix="dashboard-preview-", dir=TEST_ROOT) as raw:
    root = Path(raw)
    source = roots(root)
    original_copy = dashboard._copy_snapshot_file

    def expired_copy(source_path: Path, destination: Path, deadline: float) -> None:
        raise dashboard.DashboardError(
            2, "preview_capture_timeout", "Preview capture exceeded thirty seconds"
        )

    dashboard._copy_snapshot_file = expired_copy
    try:
        try:
            capture(root, source)
            raise AssertionError("expired capture accepted")
        except dashboard.DashboardError as error:
            check(
                error.code == "preview_capture_timeout"
                and not (root / "preview").exists(),
                "deadline expiry deletes the incomplete snapshot before releasing the lock",
            )
    finally:
        dashboard._copy_snapshot_file = original_copy

with tempfile.TemporaryDirectory(prefix="dashboard-preview-", dir=TEST_ROOT) as raw:
    root = Path(raw)
    source = roots(root)
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "skills/skill-review/scripts/daemon-lock.py"),
            "acquire",
            "--mode",
            "session",
            "--owner",
            "preview-test",
        ],
        env={**os.environ, "SKILLS_STATE_DIR": str(source["control_state"])},
        capture_output=True,
        text=True,
        check=True,
    )
    token = result.stdout.strip()
    try:
        try:
            capture(root, source)
            raise AssertionError("held writer lock accepted")
        except dashboard.DashboardError as error:
            check(error.code == "preview_capture_locked", "capture refuses a held writer lock")
    finally:
        subprocess.run(
            [
                sys.executable,
                str(REPO / "skills/skill-review/scripts/daemon-lock.py"),
                "release",
                token,
            ],
            env={**os.environ, "SKILLS_STATE_DIR": str(source["control_state"])},
            check=True,
        )

with tempfile.TemporaryDirectory(prefix="dashboard-preview-", dir=TEST_ROOT) as raw:
    root = Path(raw)
    source = roots(root)
    try:
        dashboard.capture_preview_snapshot(
            destination=root / "too-soon",
            roots=source,
            lock_state=source["control_state"],
            next_eligible_at=time.time() + 30,
        )
        raise AssertionError("near interval capture accepted")
    except dashboard.DashboardError as error:
        check(error.code == "preview_capture_too_close", "capture refuses near interval eligibility")

check(
    dashboard.DashboardData._portfolio_recommendation("missing", "unknown", None)[0]
    == "insufficient_information",
    "missing usage and evaluation never become positive evidence",
)
check(
    dashboard.DashboardData._portfolio_recommendation("regression", "complete", 99)[0]
    == "disable_candidate"
    and dashboard.DashboardData._portfolio_authority("user_protected")
    == "Your decision"
    and dashboard.DashboardData._portfolio_authority("dreaming_managed")
    == "Automatic",
    "value recommendation is independent of mutation authority",
)
check(
    dashboard.DashboardData._portfolio_recommendation("pass", "incomplete", 1)[0]
    == "proven_useful"
    and dashboard.DashboardData._portfolio_recommendation(
        "missing", "incomplete", 1
    )[0]
    == "used_evaluation_missing"
    and dashboard.DashboardData._portfolio_recommendation(
        "missing", "incomplete", 0
    )[0]
    == "insufficient_information",
    "incomplete usage preserves positive lower bounds without treating zero as complete non-use",
)

print(f"{passes} preview checks passed")
