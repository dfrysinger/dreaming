#!/usr/bin/env python3
"""Generate Dreaming's complete desired-set adapter configuration."""

from __future__ import annotations

import argparse
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
    else:
        argv = [
            sys.executable,
            str(script),
            "--vendor",
            vendor,
            "--role",
            role,
        ]
    if role == "session-source":
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


def configure(output: Path, repo_root: Path, state_dir: Path) -> dict[str, object]:
    detected = [vendor for vendor in VENDORS if executable(vendor)]
    sources = selected("DREAMING_SESSION_SOURCES", detected)
    executors = selected("DREAMING_REVIEW_EXECUTORS", sources)
    targets = selected("DREAMING_SKILL_TARGETS", sources)
    if not sources or not executors:
        raise ConfigError("at least one session source and review executor are required")
    required = set(sources) | set(executors) | set(targets)
    missing = sorted(vendor for vendor in required if not executable(vendor))
    if missing:
        raise ConfigError("selected CLI binaries are unavailable: " + ", ".join(missing))

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
    existing = {}
    if output.is_file():
        try:
            loaded = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigError(f"existing adapter config is invalid: {output}") from error
        if isinstance(loaded, dict):
            existing = loaded
    retired_publishers: dict[str, object] = {}
    for key in ("retired_publishers", "publishers"):
        values = existing.get(key, {})
        if isinstance(values, dict):
            for vendor, entry in values.items():
                if vendor in VENDORS and vendor not in targets and isinstance(entry, dict):
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
        "max_autonomous_session_age_days": 30,
        "allow_autonomous_skill_creation": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.{uuid.uuid4().hex}"
    temporary.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, output)
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
