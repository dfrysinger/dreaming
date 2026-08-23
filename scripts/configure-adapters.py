#!/usr/bin/env python3
"""Generate Dreaming's complete desired-set adapter configuration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

VENDORS = ("copilot", "claude", "codex")


class ConfigError(RuntimeError):
    pass


def selected(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    values = default if raw is None else raw.replace(",", " ").split()
    result: list[str] = []
    for value in values:
        if value not in VENDORS:
            raise ConfigError(f"{name} contains unsupported CLI: {value}")
        if value not in result:
            result.append(value)
    return result


def executable(vendor: str) -> str | None:
    override = os.environ.get(f"DREAMING_{vendor.upper()}_BIN")
    return override or shutil.which(vendor)


def adapter(
    script: Path,
    vendor: str,
    role: str,
    state_dir: Path,
    deny_roots: list[Path] | None = None,
) -> dict[str, object]:
    remote_source = (
        os.environ.get(f"DREAMING_{vendor.upper()}_SOURCE_SSH_HOST")
        if role == "session-source"
        else None
    )
    remote_publisher = (
        os.environ.get(f"DREAMING_{vendor.upper()}_PUBLISHER_SSH_HOST")
        if role == "skill-publisher"
        else None
    )
    if remote_source:
        ssh = os.environ.get("DREAMING_SOURCE_SSH_BIN", "/usr/bin/ssh")
        address_family = os.environ.get(
            f"DREAMING_{vendor.upper()}_SOURCE_SSH_ADDRESS_FAMILY",
            "",
        )
        if address_family not in {"", "4", "6"}:
            raise ConfigError(
                f"DREAMING_{vendor.upper()}_SOURCE_SSH_ADDRESS_FAMILY "
                "must be 4, 6, or empty"
            )
        remote_python = os.environ.get(
            f"DREAMING_{vendor.upper()}_SOURCE_SSH_PYTHON",
            sys.executable,
        )
        remote_script = os.environ.get(
            f"DREAMING_{vendor.upper()}_SOURCE_SSH_SCRIPT",
            str(script),
        )
        proxy = script.parents[3] / "scripts/ssh-session-source.py"
        argv = [
            sys.executable,
            str(proxy),
            "--ssh-bin",
            ssh,
            "--host",
            remote_source,
            *(["--address-family", address_family] if address_family else []),
            "--remote-python",
            remote_python,
            "--remote-script",
            remote_script,
            "--",
            "--vendor",
            vendor,
            "--role",
            role,
        ]
    elif remote_publisher:
        ssh = os.environ.get(
            "DREAMING_PUBLISHER_SSH_BIN",
            os.environ.get("DREAMING_SOURCE_SSH_BIN", "/usr/bin/ssh"),
        )
        address_family = os.environ.get(
            f"DREAMING_{vendor.upper()}_PUBLISHER_SSH_ADDRESS_FAMILY",
            "",
        )
        if address_family not in {"", "4", "6"}:
            raise ConfigError(
                f"DREAMING_{vendor.upper()}_PUBLISHER_SSH_ADDRESS_FAMILY "
                "must be 4, 6, or empty"
            )
        proxy = script.parents[3] / "scripts/ssh-skill-publisher.py"
        receiver_id = os.environ.get(
            f"DREAMING_{vendor.upper()}_PUBLISHER_RECEIVER_ID", ""
        )
        if not receiver_id:
            raise ConfigError(
                f"DREAMING_{vendor.upper()}_PUBLISHER_RECEIVER_ID is required"
            )
        remote_python = os.environ.get(
            f"DREAMING_{vendor.upper()}_PUBLISHER_SSH_PYTHON",
            sys.executable,
        )
        remote_script = os.environ.get(
            f"DREAMING_{vendor.upper()}_PUBLISHER_SSH_SCRIPT",
            str(proxy),
        )
        remote_adapter_python = os.environ.get(
            f"DREAMING_{vendor.upper()}_PUBLISHER_ADAPTER_PYTHON",
            remote_python,
        )
        remote_adapter_script = os.environ.get(
            f"DREAMING_{vendor.upper()}_PUBLISHER_ADAPTER_SCRIPT",
            str(script),
        )
        remote_home = os.environ.get(
            f"DREAMING_{vendor.upper()}_PUBLISHER_REMOTE_HOME",
            str(Path.home()),
        )
        argv = [
            sys.executable,
            str(proxy),
            "--ssh-bin",
            ssh,
            "--host",
            remote_publisher,
            *(["--address-family", address_family] if address_family else []),
            "--remote-python",
            remote_python,
            "--remote-script",
            remote_script,
            "--remote-adapter-python",
            remote_adapter_python,
            "--remote-adapter-script",
            remote_adapter_script,
            "--remote-bundle-root",
            os.environ.get(
                f"DREAMING_{vendor.upper()}_PUBLISHER_BUNDLE_ROOT",
                str(Path(remote_home) / ".local/share/dreaming/remote-publisher-bundles"),
            ),
            "--remote-ownership-journal",
            os.environ.get(
                f"DREAMING_{vendor.upper()}_PUBLISHER_OWNERSHIP_JOURNAL",
                str(Path(remote_home) / ".local/state/dreaming/publisher-ownership.json"),
            ),
            "--remote-operation-root",
            os.environ.get(
                f"DREAMING_{vendor.upper()}_PUBLISHER_OPERATION_ROOT",
                str(Path(remote_home) / ".local/state/dreaming/remote-publication"),
            ),
            "--remote-receiver-id-file",
            os.environ.get(
                f"DREAMING_{vendor.upper()}_PUBLISHER_RECEIVER_ID_FILE",
                str(Path(remote_home) / ".local/state/dreaming/receiver-id"),
            ),
            "--expected-receiver-id",
            receiver_id,
            "--expected-receiver-sha",
            hashlib.sha256(proxy.read_bytes()).hexdigest(),
            "--expected-adapter-sha",
            hashlib.sha256(script.read_bytes()).hexdigest(),
            "--summary",
            str(state_dir / "remote-publication-summary.json"),
            "--recovery-state",
            str(state_dir / "publication-recovery-required.json"),
            "--",
            "--vendor",
            vendor,
            "--role",
            role,
        ]
    else:
        argv = [
            sys.executable,
            str(script),
            "--vendor",
            vendor,
            "--role",
            role,
        ]
        binary = executable(vendor)
        if binary:
            argv.extend(["--binary", binary])
    if role == "session-source":
        max_field_bytes = positive_integer("DREAMING_MAX_FIELD_BYTES", "64000")
        max_events = positive_integer("DREAMING_MAX_EVENTS", "2000")
        max_snapshot_bytes = positive_integer(
            "DREAMING_MAX_SNAPSHOT_BYTES", "100000"
        )
        if max_snapshot_bytes < 2:
            raise ConfigError("DREAMING_MAX_SNAPSHOT_BYTES must be at least 2")
        argv.extend(
            [
                "--max-field-bytes",
                str(max_field_bytes),
                "--max-events",
                str(max_events),
                "--max-snapshot-bytes",
                str(max_snapshot_bytes),
            ]
        )
        override = os.environ.get(f"DREAMING_{vendor.upper()}_SESSION_ROOT")
        if override:
            argv.extend(["--source-root", str(Path(override).expanduser().resolve())])
        quiet = os.environ.get(
            f"DREAMING_{vendor.upper()}_QUIET_SECONDS",
            os.environ.get("DREAMING_QUIET_SECONDS"),
        )
        if quiet is not None:
            if not quiet.isdigit():
                raise ConfigError(
                    f"DREAMING_{vendor.upper()}_QUIET_SECONDS must be an integer"
                )
            argv.extend(["--quiet-seconds", quiet])
        timeout = os.environ.get(
            f"DREAMING_{vendor.upper()}_SOURCE_TIMEOUT",
            os.environ.get("DREAMING_SOURCE_TIMEOUT", "180"),
        )
        if not timeout.isdigit() or int(timeout) < 1:
            raise ConfigError("source timeout must be a positive integer")
        return {
            "argv": argv,
            "timeout": int(timeout),
            "run_timeout": int(timeout),
        }
    if role == "skill-publisher":
        if not remote_publisher:
            argv.extend(
                [
                    "--ownership-journal",
                    str(state_dir / "publisher-ownership.json"),
                ]
            )
        timeout = positive_integer("DREAMING_PUBLISHER_TIMEOUT", "90")
        return {"argv": argv, "timeout": timeout, "run_timeout": timeout}
    if role == "review-executor":
        for root in deny_roots or []:
            argv.extend(["--deny-root", str(root)])
        health_timeout = os.environ.get("DREAMING_EXECUTOR_HEALTH_TIMEOUT", "120")
        if not health_timeout.isdigit() or int(health_timeout) < 1:
            raise ConfigError("executor health timeout must be a positive integer")
        timeout = os.environ.get(
            f"DREAMING_{vendor.upper()}_EXECUTOR_TIMEOUT",
            os.environ.get("DREAMING_EXECUTOR_TIMEOUT", "600"),
        )
        if not timeout.isdigit() or int(timeout) < 1:
            raise ConfigError("executor timeout must be a positive integer")
        argv.extend(["--timeout", timeout])
        return {
            "argv": argv,
            "timeout": int(health_timeout),
            "run_timeout": int(timeout) + 30,
        }
    return {"argv": argv}


def source_root(vendor: str) -> Path:
    override = os.environ.get(f"DREAMING_{vendor.upper()}_SESSION_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    home = Path.home()
    defaults = {
        "copilot": Path(os.environ.get("COPILOT_HOME", home / ".copilot"))
        / "session-state",
        "claude": Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude"))
        / "projects",
        "codex": Path(os.environ.get("CODEX_HOME", home / ".codex")),
    }
    return defaults[vendor].expanduser().resolve()


def positive_integer(name: str, default: str) -> int:
    value = os.environ.get(name, default)
    if not value.isdigit() or int(value) < 1:
        raise ConfigError(f"{name} must be a positive integer")
    return int(value)


def environment_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    if value not in {"0", "1"}:
        raise ConfigError(f"{name} must be 0 or 1")
    return value == "1"


def required_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def argv_value(entry: object, flag: str) -> str | None:
    if not isinstance(entry, dict):
        return None
    argv = entry.get("argv")
    if not isinstance(argv, list) or not all(isinstance(value, str) for value in argv):
        return None
    try:
        index = argv.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        return None
    return argv[index + 1]


def strict_argv_value(
    entry: object, flag: str, label: str, *, required: bool = True
) -> str | None:
    if not isinstance(entry, dict):
        raise ConfigError(f"{label} is malformed")
    argv = entry.get("argv")
    if not isinstance(argv, list) or not all(isinstance(value, str) for value in argv):
        raise ConfigError(f"{label} argv is malformed")
    positions = [index for index, value in enumerate(argv) if value == flag]
    if not positions:
        if required:
            raise ConfigError(f"{label} is missing {flag}")
        return None
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise ConfigError(f"{label} has ambiguous {flag}")
    value = argv[positions[0] + 1]
    if not value or value.startswith("--"):
        raise ConfigError(f"{label} has invalid {flag}")
    return value


def load_estate_baseline(state_dir: Path) -> dict[str, object]:
    baseline_path = state_dir / "estate-adapters-baseline.json"
    if baseline_path.is_symlink() or not baseline_path.is_file():
        raise ConfigError("installation-owned estate adapter baseline is missing")
    expected_baseline_sha = required_environment(
        "DREAMING_ESTATE_ADAPTERS_BASELINE_SHA256"
    )
    observed_baseline_sha = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    if observed_baseline_sha != expected_baseline_sha:
        raise ConfigError("estate adapter baseline digest does not match")
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConfigError("estate adapter baseline is malformed") from error
    if not isinstance(baseline, dict) or baseline.get("contract_version") != 1:
        raise ConfigError("estate adapter baseline contract is invalid")
    for key in ("estate_census", "estate_curator"):
        if not isinstance(baseline.get(key), dict):
            raise ConfigError(f"estate adapter baseline is missing {key}")
    return baseline


def inherit_remote_copilot(
    existing: dict[str, object],
    estate_existing: dict[str, object] | None = None,
) -> None:
    source = existing.get("sources", {})
    source_entry = source.get("copilot") if isinstance(source, dict) else None
    source_argv = source_entry.get("argv", []) if isinstance(source_entry, dict) else []
    if isinstance(source_argv, list) and any(
        isinstance(value, str) and value.endswith("/scripts/ssh-session-source.py")
        for value in source_argv
    ):
        source_values = {
            "DREAMING_SOURCE_SSH_BIN": argv_value(source_entry, "--ssh-bin"),
            "DREAMING_COPILOT_SOURCE_SSH_HOST": argv_value(source_entry, "--host"),
            "DREAMING_COPILOT_SOURCE_SSH_ADDRESS_FAMILY": argv_value(
                source_entry, "--address-family"
            ),
            "DREAMING_COPILOT_SOURCE_SSH_PYTHON": argv_value(
                source_entry, "--remote-python"
            ),
            "DREAMING_COPILOT_SOURCE_SSH_SCRIPT": argv_value(
                source_entry, "--remote-script"
            ),
        }
        for name, value in source_values.items():
            if name not in os.environ and value is not None:
                os.environ[name] = value

    publishers = existing.get("publishers", {})
    publisher = (
        publishers.get("copilot") if isinstance(publishers, dict) else None
    )
    publisher_argv = publisher.get("argv", []) if isinstance(publisher, dict) else []
    if isinstance(publisher_argv, list) and any(
        isinstance(value, str) and value.endswith("/scripts/ssh-skill-publisher.py")
        for value in publisher_argv
    ):
        publisher_values = {
            "DREAMING_PUBLISHER_SSH_BIN": argv_value(publisher, "--ssh-bin"),
            "DREAMING_COPILOT_PUBLISHER_SSH_HOST": argv_value(publisher, "--host"),
            "DREAMING_COPILOT_PUBLISHER_SSH_ADDRESS_FAMILY": argv_value(
                publisher, "--address-family"
            ),
            "DREAMING_COPILOT_PUBLISHER_SSH_PYTHON": argv_value(
                publisher, "--remote-python"
            ),
            "DREAMING_COPILOT_PUBLISHER_SSH_SCRIPT": argv_value(
                publisher, "--remote-script"
            ),
            "DREAMING_COPILOT_PUBLISHER_ADAPTER_PYTHON": argv_value(
                publisher, "--remote-adapter-python"
            ),
            "DREAMING_COPILOT_PUBLISHER_ADAPTER_SCRIPT": argv_value(
                publisher, "--remote-adapter-script"
            ),
            "DREAMING_COPILOT_PUBLISHER_BUNDLE_ROOT": argv_value(
                publisher, "--remote-bundle-root"
            ),
            "DREAMING_COPILOT_PUBLISHER_OWNERSHIP_JOURNAL": argv_value(
                publisher, "--remote-ownership-journal"
            ),
            "DREAMING_COPILOT_PUBLISHER_OPERATION_ROOT": argv_value(
                publisher, "--remote-operation-root"
            ),
            "DREAMING_COPILOT_PUBLISHER_RECEIVER_ID_FILE": argv_value(
                publisher, "--remote-receiver-id-file"
            ),
            "DREAMING_COPILOT_PUBLISHER_RECEIVER_ID": argv_value(
                publisher, "--expected-receiver-id"
            ),
        }
        for name, value in publisher_values.items():
            if name not in os.environ and value is not None:
                os.environ[name] = value

    estate_source = estate_existing if estate_existing is not None else existing
    estate_entry = estate_source.get("estate_census")
    estate_argv = (
        estate_entry.get("argv", []) if isinstance(estate_entry, dict) else []
    )
    if estate_entry is not None:
        if not isinstance(estate_argv, list) or not any(
            isinstance(value, str)
            and value.endswith("/scripts/ssh-estate-census.py")
            for value in estate_argv
        ):
            raise ConfigError("estate census adapter is malformed")
        estate_values = {
            "DREAMING_ESTATE_SSH_BIN": strict_argv_value(
                estate_entry, "--ssh-bin", "estate census adapter"
            ),
            "DREAMING_COPILOT_ESTATE_SSH_HOST": strict_argv_value(
                estate_entry, "--host", "estate census adapter"
            ),
            "DREAMING_COPILOT_ESTATE_SSH_ADDRESS_FAMILY": strict_argv_value(
                estate_entry,
                "--address-family",
                "estate census adapter",
                required=False,
            ),
            "DREAMING_COPILOT_ESTATE_SSH_PYTHON": strict_argv_value(
                estate_entry, "--remote-python", "estate census adapter"
            ),
            "DREAMING_COPILOT_ESTATE_SSH_SCRIPT": strict_argv_value(
                estate_entry, "--remote-script", "estate census adapter"
            ),
            "DREAMING_COPILOT_ESTATE_COLLECTOR_SCRIPT": strict_argv_value(
                estate_entry,
                "--remote-estate-script",
                "estate census adapter",
            ),
            "DREAMING_COPILOT_ESTATE_RECEIVER_ID_FILE": strict_argv_value(
                estate_entry,
                "--remote-receiver-id-file",
                "estate census adapter",
            ),
            "DREAMING_COPILOT_ESTATE_BIN": strict_argv_value(
                estate_entry,
                "--remote-copilot-binary",
                "estate census adapter",
            ),
            "DREAMING_COPILOT_ESTATE_REMOTE_HOME": strict_argv_value(
                estate_entry, "--target-home", "estate census adapter"
            ),
            "DREAMING_COPILOT_ESTATE_RECEIVER_ID": strict_argv_value(
                estate_entry,
                "--expected-receiver-id",
                "estate census adapter",
            ),
            "DREAMING_COPILOT_ESTATE_SESSION_ROOT": strict_argv_value(
                estate_entry,
                "--remote-copilot-session-root",
                "estate census adapter",
                required=False,
            ),
            "DREAMING_COPILOT_ESTATE_USAGE_INDEX": strict_argv_value(
                estate_entry,
                "--remote-usage-index-path",
                "estate census adapter",
            ),
            "DREAMING_ESTATE_USAGE_MAX_SESSIONS": strict_argv_value(
                estate_entry, "--usage-max-sessions", "estate census adapter"
            ),
            "DREAMING_ESTATE_USAGE_MAX_BYTES": strict_argv_value(
                estate_entry, "--usage-max-bytes", "estate census adapter"
            ),
            "DREAMING_ESTATE_TIMEOUT": strict_argv_value(
                estate_entry, "--timeout", "estate census adapter"
            ),
            "DREAMING_COPILOT_ESTATE_PROJECT_CONTEXTS_FILE": strict_argv_value(
                estate_entry,
                "--remote-project-contexts-file",
                "estate census adapter",
                required=False,
            ),
        }
        for name, value in estate_values.items():
            if name not in os.environ and value is not None:
                os.environ[name] = value

    curator_entry = estate_source.get("estate_curator")
    curator_argv = (
        curator_entry.get("argv", []) if isinstance(curator_entry, dict) else []
    )
    if curator_entry is not None:
        if not isinstance(curator_argv, list) or not any(
            isinstance(value, str)
            and value.endswith("/scripts/ssh-estate-curator.py")
            for value in curator_argv
        ):
            raise ConfigError("estate curator adapter is malformed")
        curator_values = {
            "DREAMING_COPILOT_ESTATE_CURATOR_SSH_SCRIPT": strict_argv_value(
                curator_entry, "--remote-script", "estate curator adapter"
            ),
            "DREAMING_COPILOT_ESTATE_CURATOR_RUNNER": strict_argv_value(
                curator_entry,
                "--remote-curator-runner",
                "estate curator adapter",
            ),
            "DREAMING_COPILOT_ESTATE_ARCHIVE_TOOL": strict_argv_value(
                curator_entry, "--remote-archive-tool", "estate curator adapter"
            ),
            "DREAMING_COPILOT_ESTATE_RESTORE_TOOL": strict_argv_value(
                curator_entry, "--remote-restore-tool", "estate curator adapter"
            ),
            "DREAMING_COPILOT_ESTATE_DEPENDENCY_SCANNER": strict_argv_value(
                curator_entry,
                "--remote-dependency-scanner",
                "estate curator adapter",
            ),
            "DREAMING_COPILOT_ESTATE_PUBLIC_ROOT": strict_argv_value(
                curator_entry, "--remote-public-root", "estate curator adapter"
            ),
            "DREAMING_COPILOT_ESTATE_PERSONAL_ROOT": strict_argv_value(
                curator_entry, "--remote-personal-root", "estate curator adapter"
            ),
            "DREAMING_COPILOT_ESTATE_REVIEW_STATE_DIR": strict_argv_value(
                curator_entry,
                "--remote-review-state-dir",
                "estate curator adapter",
            ),
            "DREAMING_COPILOT_ESTATE_RUNS_DIR": strict_argv_value(
                curator_entry, "--remote-runs-dir", "estate curator adapter"
            ),
            "DREAMING_COPILOT_ESTATE_CURATOR_STATE_FILE": strict_argv_value(
                curator_entry,
                "--remote-curator-state-file",
                "estate curator adapter",
            ),
            "DREAMING_COPILOT_ESTATE_HALT_SWITCH": strict_argv_value(
                curator_entry, "--remote-halt-switch", "estate curator adapter"
            ),
            "DREAMING_COPILOT_ESTATE_LOCK_DIR": strict_argv_value(
                curator_entry, "--remote-lock-dir", "estate curator adapter"
            ),
            "DREAMING_COPILOT_ESTATE_OPERATION_ROOT": strict_argv_value(
                curator_entry,
                "--remote-operation-root",
                "estate curator adapter",
            ),
            "DREAMING_COPILOT_ESTATE_RECOVERY_STATE": strict_argv_value(
                curator_entry,
                "--remote-recovery-state",
                "estate curator adapter",
            ),
            "DREAMING_COPILOT_ESTATE_USER_CONTEXT_CWD": strict_argv_value(
                curator_entry,
                "--remote-user-context-cwd",
                "estate curator adapter",
            ),
            "DREAMING_ESTATE_CURATOR_TIMEOUT": strict_argv_value(
                curator_entry, "--timeout", "estate curator adapter"
            ),
        }
        for name, value in curator_values.items():
            if name not in os.environ and value is not None:
                os.environ[name] = value


def inherit_local_binaries(existing: dict[str, object]) -> None:
    for vendor in VENDORS:
        name = f"DREAMING_{vendor.upper()}_BIN"
        if name in os.environ:
            continue
        for role in ("executors", "sources", "publishers"):
            entries = existing.get(role)
            entry = entries.get(vendor) if isinstance(entries, dict) else None
            binary = argv_value(entry, "--binary")
            if binary is not None:
                os.environ[name] = binary
                break


def configure(output: Path, repo_root: Path, state_dir: Path) -> dict[str, object]:
    existing: dict[str, object] = {}
    if output.is_file():
        try:
            loaded = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigError(f"existing adapter config is invalid: {output}") from error
        if isinstance(loaded, dict):
            existing = loaded
    if environment_flag("DREAMING_PRESERVE_ESTATE_ADAPTERS"):
        estate_existing: dict[str, object] = {}
    elif os.environ.get("DREAMING_ESTATE_ADAPTERS_BASELINE_SHA256"):
        estate_existing = load_estate_baseline(state_dir)
    else:
        estate_existing = existing
    inherit_remote_copilot(existing, estate_existing)
    inherit_local_binaries(existing)
    configured = {
        vendor
        for role in ("sources", "executors", "publishers")
        for vendor in (
            existing.get(role, {}).keys()
            if isinstance(existing.get(role), dict)
            else ()
        )
        if vendor in VENDORS
    }
    detected = [
        vendor for vendor in VENDORS if executable(vendor) or vendor in configured
    ]

    def role_default(role: str, fallback: list[str]) -> list[str]:
        entries = existing.get(role)
        if not isinstance(entries, dict):
            return fallback
        return [vendor for vendor in VENDORS if vendor in entries]

    sources = selected("DREAMING_SESSION_SOURCES", role_default("sources", detected))
    executors = selected(
        "DREAMING_REVIEW_EXECUTORS", role_default("executors", sources)
    )
    targets = selected(
        "DREAMING_SKILL_TARGETS", role_default("publishers", sources)
    )
    detach_remote_copilot = (
        os.environ.get("DREAMING_DETACH_REMOTE_COPILOT_PUBLISHER") == "1"
    )
    if not sources or not executors:
        raise ConfigError("at least one session source and review executor are required")
    if detach_remote_copilot and (
        "copilot" in targets
        or os.environ.get("DREAMING_COPILOT_PUBLISHER_SSH_HOST")
        or os.environ.get("DREAMING_REQUIRE_REMOTE_COPILOT_PUBLISHER") == "1"
    ):
        raise ConfigError(
            "remote Copilot detach requires no Copilot target, no publisher host, "
            "and remote-only enforcement disabled"
        )
    required = set(sources) | set(executors) | set(targets)
    missing = sorted(
        vendor for vendor in required if not executable(vendor) and vendor not in configured
    )
    if missing:
        raise ConfigError("selected CLI binaries are unavailable: " + ", ".join(missing))
    if (
        os.environ.get("DREAMING_REQUIRE_REMOTE_COPILOT_PUBLISHER") == "1"
        and "copilot" in targets
        and not os.environ.get("DREAMING_COPILOT_PUBLISHER_SSH_HOST")
    ):
        raise ConfigError("remote Copilot publisher is required on this host")

    explicit_routes = os.environ.get("DREAMING_SOURCE_EXECUTOR_ALLOW")
    routes = (
        explicit_routes.replace(",", " ").split()
        if explicit_routes is not None
        else [f"{vendor}>{vendor}" for vendor in sources if vendor in executors]
    )
    valid_routes: list[str] = []
    for route in routes:
        if route.count(">") != 1:
            raise ConfigError(f"invalid source-to-executor route: {route}")
        source, executor = route.split(">", 1)
        if source not in sources or executor not in executors:
            raise ConfigError(f"route references an unselected adapter: {route}")
        if route not in valid_routes:
            valid_routes.append(route)
    uncovered = [
        source
        for source in sources
        if not any(route.startswith(source + ">") for route in valid_routes)
    ]
    if uncovered:
        raise ConfigError(
            "selected sources have no allowed executor route: " + ", ".join(uncovered)
        )

    script = repo_root / "skills/skill-review/scripts/dreaming-vendor-adapter.py"
    denied_roots = [source_root(vendor) for vendor in sources]
    retired_publishers: dict[str, object] = {}
    for key in ("retired_publishers", "publishers"):
        values = existing.get(key, {})
        if isinstance(values, dict):
            for vendor, entry in values.items():
                if vendor in VENDORS and vendor not in targets and isinstance(entry, dict):
                    argv = entry.get("argv", [])
                    if (
                        detach_remote_copilot
                        and vendor == "copilot"
                        and isinstance(argv, list)
                        and any(
                            isinstance(value, str)
                            and value.endswith("/scripts/ssh-skill-publisher.py")
                            for value in argv
                        )
                    ):
                        continue
                    retired_publishers[vendor] = entry
    config: dict[str, object] = {
        "contract_version": 1,
        "sources": {
            vendor: adapter(script, vendor, "session-source", state_dir)
            for vendor in sources
        },
        "executors": {
            vendor: adapter(
                script,
                vendor,
                "review-executor",
                state_dir,
                denied_roots,
            )
            for vendor in executors
        },
        "publishers": {
            vendor: adapter(script, vendor, "skill-publisher", state_dir)
            for vendor in targets
        },
        "retired_publishers": retired_publishers,
        "routes": valid_routes,
        "executor_order": executors,
        "policy_version": 2,
        "max_reviews_per_run": positive_integer(
            "DREAMING_MAX_REVIEWS_PER_RUN", "25"
        ),
        "max_profiles_per_run": positive_integer(
            "DREAMING_MAX_PROFILES_PER_RUN", "100"
        ),
        "max_profile_elapsed_seconds": positive_integer(
            "DREAMING_MAX_PROFILE_ELAPSED_SECONDS", "600"
        ),
        "max_snapshot_bytes": positive_integer(
            "DREAMING_MAX_SNAPSHOT_BYTES", "100000"
        ),
        "max_events": positive_integer("DREAMING_MAX_EVENTS", "2000"),
        "max_field_bytes": positive_integer("DREAMING_MAX_FIELD_BYTES", "64000"),
        "max_autonomous_session_age_days": 30,
        "allow_autonomous_skill_creation": False,
    }
    estate_host = os.environ.get(
        "DREAMING_COPILOT_ESTATE_SSH_HOST",
        os.environ.get(
            "DREAMING_COPILOT_PUBLISHER_SSH_HOST",
            os.environ.get("DREAMING_COPILOT_SOURCE_SSH_HOST", ""),
        ),
    )
    estate_receiver_id = os.environ.get(
        "DREAMING_COPILOT_ESTATE_RECEIVER_ID",
        os.environ.get("DREAMING_COPILOT_PUBLISHER_RECEIVER_ID", ""),
    )
    if estate_host:
        if not estate_receiver_id:
            raise ConfigError(
                "remote Copilot estate census requires a receiver ID"
            )
        proxy = repo_root / "scripts/ssh-estate-census.py"
        collector = repo_root / "skills/skill-review/scripts/dreaming-estate.py"
        remote_home = os.environ.get(
            "DREAMING_COPILOT_ESTATE_REMOTE_HOME", str(Path.home())
        )
        address_family = os.environ.get(
            "DREAMING_COPILOT_ESTATE_SSH_ADDRESS_FAMILY",
            os.environ.get(
                "DREAMING_COPILOT_PUBLISHER_SSH_ADDRESS_FAMILY", ""
            ),
        )
        if address_family not in {"", "4", "6"}:
            raise ConfigError(
                "DREAMING_COPILOT_ESTATE_SSH_ADDRESS_FAMILY must be 4, 6, or empty"
            )
        estate_argv = [
            sys.executable,
            str(proxy),
            "--ssh-bin",
            os.environ.get(
                "DREAMING_ESTATE_SSH_BIN",
                os.environ.get("DREAMING_PUBLISHER_SSH_BIN", "/usr/bin/ssh"),
            ),
            "--host",
            estate_host,
            *(["--address-family", address_family] if address_family else []),
            "--remote-python",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_SSH_PYTHON",
                os.environ.get(
                    "DREAMING_COPILOT_PUBLISHER_SSH_PYTHON", sys.executable
                ),
            ),
            "--remote-script",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_SSH_SCRIPT", str(proxy)
            ),
            "--remote-estate-script",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_COLLECTOR_SCRIPT", str(collector)
            ),
            "--remote-receiver-id-file",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_RECEIVER_ID_FILE",
                str(Path(remote_home) / ".local/state/dreaming/receiver-id"),
            ),
            "--remote-copilot-binary",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_BIN",
                os.environ.get("DREAMING_COPILOT_BIN", "copilot"),
            ),
            "--remote-usage-index-path",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_USAGE_INDEX",
                str(
                    Path(remote_home)
                    / ".local/state/dreaming/copilot-usage-index.json"
                ),
            ),
            "--usage-max-sessions",
            str(positive_integer("DREAMING_ESTATE_USAGE_MAX_SESSIONS", "10000")),
            "--usage-max-bytes",
            str(
                positive_integer(
                    "DREAMING_ESTATE_USAGE_MAX_BYTES", str(1024 * 1024 * 1024)
                )
            ),
            "--expected-receiver-id",
            estate_receiver_id,
            "--expected-receiver-sha",
            hashlib.sha256(proxy.read_bytes()).hexdigest(),
            "--expected-collector-sha",
            hashlib.sha256(collector.read_bytes()).hexdigest(),
            "--target-host-id",
            estate_receiver_id,
            "--target-home",
            remote_home,
            "--timeout",
            str(positive_integer("DREAMING_ESTATE_TIMEOUT", "180")),
        ]
        project_contexts = os.environ.get(
            "DREAMING_COPILOT_ESTATE_PROJECT_CONTEXTS_FILE"
        )
        if project_contexts:
            estate_argv.extend(
                ["--remote-project-contexts-file", project_contexts]
            )
        session_root = os.environ.get("DREAMING_COPILOT_ESTATE_SESSION_ROOT")
        if session_root:
            estate_argv.extend(
                ["--remote-copilot-session-root", session_root]
            )
        config["estate_census"] = {
            "argv": estate_argv,
            "timeout": positive_integer("DREAMING_ESTATE_TIMEOUT", "180") + 30,
        }
        curator_proxy = repo_root / "scripts/ssh-estate-curator.py"
        curator_runner = (
            repo_root / "skills/skill-curator/scripts/curator-run.py"
        )
        archive_tool = (
            repo_root / "skills/skill-manage/scripts/archive-skill.sh"
        )
        restore_tool = (
            repo_root / "skills/skill-manage/scripts/restore-skill.sh"
        )
        dependency_scanner = (
            repo_root
            / "skills/skill-curator/scripts/scheduled-skill-deps.py"
        )
        remote_review_state = Path(
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_REVIEW_STATE_DIR",
                str(Path(remote_home) / ".copilot/skill-state/skill-review"),
            )
        )
        curator_argv = [
            sys.executable,
            str(curator_proxy),
            "--ssh-bin",
            os.environ.get(
                "DREAMING_ESTATE_SSH_BIN",
                os.environ.get("DREAMING_PUBLISHER_SSH_BIN", "/usr/bin/ssh"),
            ),
            "--host",
            estate_host,
            *(["--address-family", address_family] if address_family else []),
            "--remote-python",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_SSH_PYTHON",
                os.environ.get(
                    "DREAMING_COPILOT_PUBLISHER_SSH_PYTHON", sys.executable
                ),
            ),
            "--remote-script",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_CURATOR_SSH_SCRIPT",
                str(curator_proxy),
            ),
            "--remote-curator-runner",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_CURATOR_RUNNER",
                str(curator_runner),
            ),
            "--remote-archive-tool",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_ARCHIVE_TOOL",
                str(archive_tool),
            ),
            "--remote-restore-tool",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_RESTORE_TOOL",
                str(restore_tool),
            ),
            "--remote-estate-script",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_COLLECTOR_SCRIPT", str(collector)
            ),
            "--remote-dependency-scanner",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_DEPENDENCY_SCANNER",
                str(dependency_scanner),
            ),
            "--remote-public-root",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_PUBLIC_ROOT",
                str(Path(remote_home) / "code/skills"),
            ),
            "--remote-personal-root",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_PERSONAL_ROOT",
                str(Path(remote_home) / ".copilot/skills"),
            ),
            "--remote-review-state-dir",
            str(remote_review_state),
            "--remote-runs-dir",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_RUNS_DIR",
                str(remote_review_state / "curator-runs"),
            ),
            "--remote-curator-state-file",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_CURATOR_STATE_FILE",
                str(remote_review_state.parent / "curator.json"),
            ),
            "--remote-halt-switch",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_HALT_SWITCH",
                str(remote_review_state / "disable-daemon"),
            ),
            "--remote-lock-dir",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_LOCK_DIR",
                str(remote_review_state / "writer-lock.sqlite"),
            ),
            "--remote-operation-root",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_OPERATION_ROOT",
                str(
                    Path(remote_home)
                    / ".local/state/dreaming/estate-transactions"
                ),
            ),
            "--remote-recovery-state",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_RECOVERY_STATE",
                str(
                    Path(remote_home)
                    / ".local/state/dreaming/estate-recovery-required.json"
                ),
            ),
            "--remote-receiver-id-file",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_RECEIVER_ID_FILE",
                str(Path(remote_home) / ".local/state/dreaming/receiver-id"),
            ),
            "--remote-target-home",
            remote_home,
            "--remote-copilot-binary",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_BIN",
                os.environ.get("DREAMING_COPILOT_BIN", "copilot"),
            ),
            "--remote-user-context-cwd",
            os.environ.get(
                "DREAMING_COPILOT_ESTATE_USER_CONTEXT_CWD",
                remote_home,
            ),
            "--expected-receiver-id",
            estate_receiver_id,
            "--expected-receiver-sha",
            hashlib.sha256(curator_proxy.read_bytes()).hexdigest(),
            "--expected-curator-sha",
            hashlib.sha256(curator_runner.read_bytes()).hexdigest(),
            "--expected-archive-sha",
            hashlib.sha256(archive_tool.read_bytes()).hexdigest(),
            "--expected-restore-sha",
            hashlib.sha256(restore_tool.read_bytes()).hexdigest(),
            "--expected-estate-sha",
            hashlib.sha256(collector.read_bytes()).hexdigest(),
            "--expected-dependency-scanner-sha",
            hashlib.sha256(dependency_scanner.read_bytes()).hexdigest(),
            "--local-recovery-state",
            str(state_dir / "estate-recovery-required.json"),
            "--timeout",
            str(positive_integer("DREAMING_ESTATE_CURATOR_TIMEOUT", "300")),
        ]
        if project_contexts:
            curator_argv.extend(
                ["--remote-project-contexts-file", project_contexts]
            )
        config["estate_curator"] = {
            "argv": curator_argv,
            "timeout": positive_integer("DREAMING_ESTATE_CURATOR_TIMEOUT", "300")
            + 30,
            "enabled": False,
        }
    if environment_flag("DREAMING_PRESERVE_ESTATE_ADAPTERS"):
        baseline = load_estate_baseline(state_dir)
        preserved_keys = ["estate_census", "estate_curator"]
        if environment_flag("DREAMING_PRESERVE_OPERATIONAL_ADAPTERS"):
            preserved_keys.extend(
                (
                    "allow_autonomous_skill_creation",
                    "max_autonomous_session_age_days",
                    "max_events",
                    "max_field_bytes",
                    "max_profile_elapsed_seconds",
                    "max_profiles_per_run",
                    "max_reviews_per_run",
                    "max_snapshot_bytes",
                    "policy_version",
                    "sources",
                    "executors",
                    "publishers",
                    "retired_publishers",
                    "routes",
                    "executor_order",
                )
            )
        for key in preserved_keys:
            if key in baseline:
                config[key] = baseline[key]
    configure_owner = environment_flag(
        "DREAMING_CONFIGURE_EVALUATION_INPUT_OWNER"
    )
    existing_owner = existing.get("evaluation_input_owner")
    if configure_owner:
        owner = {
            "enabled": environment_flag(
                "DREAMING_EVALUATION_INPUT_OWNER_ENABLED"
            ),
            "author_model": required_environment(
                "DREAMING_EVALUATION_INPUT_AUTHOR_MODEL"
            ),
            "reviewer_a_model": required_environment(
                "DREAMING_EVALUATION_INPUT_REVIEWER_A_MODEL"
            ),
            "reviewer_b_model": required_environment(
                "DREAMING_EVALUATION_INPUT_REVIEWER_B_MODEL"
            ),
            "content_root": str(state_dir / "evaluation-input-owner"),
        }
        if len(
            {
                owner["author_model"],
                owner["reviewer_a_model"],
                owner["reviewer_b_model"],
            }
        ) != 3:
            raise ConfigError(
                "evaluation-input owner requires three distinct models"
            )
        config["evaluation_input_owner"] = owner
    elif isinstance(existing_owner, dict):
        config["evaluation_input_owner"] = existing_owner

    configure_remote_subjects = environment_flag(
        "DREAMING_CONFIGURE_REMOTE_EVALUATION_SUBJECTS"
    )
    existing_remote_subjects = existing.get("remote_evaluation_subjects")
    if configure_remote_subjects:
        owner = config.get("evaluation_input_owner")
        if not isinstance(owner, dict):
            raise ConfigError(
                "remote evaluation subjects require evaluation-input owner configuration"
            )
        remote_host = required_environment(
            "DREAMING_REMOTE_SUBJECT_SSH_HOST"
        )
        origin_host_id = required_environment(
            "DREAMING_REMOTE_SUBJECT_ORIGIN_HOST_ID"
        )
        remote_home = os.environ.get(
            "DREAMING_REMOTE_SUBJECT_HOME", str(Path.home())
        )
        known_hosts = state_dir / "remote-subject-known-hosts"
        if known_hosts.is_symlink() or not known_hosts.is_file():
            raise ConfigError(
                "installation-owned remote subject known-hosts file is missing"
            )
        proxy = repo_root / "scripts/ssh-estate-census.py"
        collector = repo_root / "skills/skill-review/scripts/dreaming-estate.py"
        content_policy = (
            repo_root
            / "skills/skill-review/references/remote-subject-content-policy-v1.json"
        )
        address_family = os.environ.get(
            "DREAMING_REMOTE_SUBJECT_SSH_ADDRESS_FAMILY", ""
        )
        if address_family not in {"", "4", "6"}:
            raise ConfigError(
                "DREAMING_REMOTE_SUBJECT_SSH_ADDRESS_FAMILY must be 4, 6, or empty"
            )
        receiver_id = os.environ.get(
            "DREAMING_REMOTE_SUBJECT_RECEIVER_ID",
            os.environ.get("DREAMING_COPILOT_ESTATE_RECEIVER_ID", ""),
        )
        if not receiver_id:
            raise ConfigError("remote subject receiver ID is required")
        remote_python = os.environ.get(
            "DREAMING_REMOTE_SUBJECT_SSH_PYTHON", "/usr/bin/python3"
        )
        remote_script = required_environment(
            "DREAMING_REMOTE_SUBJECT_SSH_SCRIPT"
        )
        remote_collector = required_environment(
            "DREAMING_REMOTE_SUBJECT_ESTATE_SCRIPT"
        )
        remote_policy = required_environment(
            "DREAMING_REMOTE_SUBJECT_CONTENT_POLICY"
        )
        receiver = {
            "receiver_id": receiver_id,
            "receiver_sha256": hashlib.sha256(proxy.read_bytes()).hexdigest(),
            "collector_sha256": hashlib.sha256(
                collector.read_bytes()
            ).hexdigest(),
            "content_policy_sha256": hashlib.sha256(
                content_policy.read_bytes()
            ).hexdigest(),
        }
        command = [
            str(Path(sys.executable).resolve()),
            str(proxy),
            "--fetch-subject",
            "--ssh-bin",
            os.environ.get("DREAMING_REMOTE_SUBJECT_SSH_BIN", "/usr/bin/ssh"),
            "--host",
            remote_host,
            *(["--address-family", address_family] if address_family else []),
            "--remote-python",
            remote_python,
            "--remote-script",
            remote_script,
            "--remote-estate-script",
            remote_collector,
            "--remote-receiver-id-file",
            os.environ.get(
                "DREAMING_REMOTE_SUBJECT_RECEIVER_ID_FILE",
                str(Path(remote_home) / ".local/state/dreaming/receiver-id"),
            ),
            "--remote-copilot-binary",
            os.environ.get(
                "DREAMING_REMOTE_SUBJECT_COPILOT_BIN",
                str(Path(remote_home) / ".local/bin/copilot"),
            ),
            "--target-host-id",
            origin_host_id,
            "--target-home",
            remote_home,
            "--known-hosts-file",
            str(known_hosts),
            "--expected-known-hosts-sha",
            hashlib.sha256(known_hosts.read_bytes()).hexdigest(),
            "--remote-content-policy",
            remote_policy,
            "--expected-content-policy-sha",
            receiver["content_policy_sha256"],
            "--expected-receiver-id",
            receiver["receiver_id"],
            "--expected-receiver-sha",
            receiver["receiver_sha256"],
            "--expected-collector-sha",
            receiver["collector_sha256"],
            "--timeout",
            str(positive_integer("DREAMING_REMOTE_SUBJECT_TIMEOUT", "180")),
        ]
        config["remote_evaluation_subjects"] = {
            "enabled": environment_flag(
                "DREAMING_REMOTE_EVALUATION_SUBJECTS_ENABLED"
            ),
            "protocol_version": 1,
            "origin_host_id": origin_host_id,
            "command": command,
            "receiver": receiver,
            "max_files": 512,
            "max_file_bytes": 8 * 1024 * 1024,
            "max_decoded_bytes": 32 * 1024 * 1024,
            "max_encoded_bytes": 48 * 1024 * 1024,
            "snapshot_root": str(
                state_dir / "remote-evaluation-subjects"
            ),
        }
    elif isinstance(existing_remote_subjects, dict):
        config["remote_evaluation_subjects"] = existing_remote_subjects
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.{uuid.uuid4().hex}"
    temporary.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, output)
    if detach_remote_copilot:
        (state_dir / "remote-publication-summary.json").unlink(missing_ok=True)
        (state_dir / "publication-recovery-required.json").unlink(missing_ok=True)
    return config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()
    try:
        config = configure(
            Path(args.output).expanduser().resolve(),
            Path(args.repo_root).expanduser().resolve(),
            Path(args.state_dir).expanduser().resolve(),
        )
    except ConfigError as error:
        print(f"configure-adapters.py: {error}", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(config, sort_keys=True))


if __name__ == "__main__":
    main()
