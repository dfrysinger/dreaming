#!/usr/bin/env python3
"""Build one environment-bound source pack for safe evaluation-input authoring."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

MAX_OUTPUT_BYTES = 100_000
TIMEOUT_SECONDS = 120
TOKEN_BUDGET = 20_000


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(canonical(value))
    os.chmod(path, 0o600)


def publish_directory_create_only(source: Path, destination: Path) -> None:
    if sys.platform != "darwin":
        raise BuildError("atomic create-only directory publication requires macOS")
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameatx_np
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    at_fdcwd = -2
    rename_exclusive = 0x00000004
    if (
        rename(
            at_fdcwd,
            os.fsencode(source),
            at_fdcwd,
            os.fsencode(destination),
            rename_exclusive,
        )
        != 0
    ):
        failure = ctypes.get_errno()
        if failure in {errno.EEXIST, errno.ENOTEMPTY}:
            raise BuildError("output was created before publication")
        raise OSError(failure, os.strerror(failure), str(destination))


def skill_name(skill: Path) -> str:
    path = skill / "SKILL.md"
    if path.is_symlink() or not path.is_file():
        raise BuildError("skill must contain a regular SKILL.md")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise BuildError("SKILL.md frontmatter is required")
    for line in lines[1:]:
        if line == "---":
            break
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip()
            if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
                return name
    raise BuildError("SKILL.md has no canonical name")


def parse_mapping(values: list[str], field: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, selected = value.partition("=")
        if (
            not separator
            or key not in {"copilot", "claude", "codex"}
            or not selected
            or key in result
        ):
            raise BuildError(f"{field} entries must be unique vendor=value pairs")
        result[key] = selected
    return result


def configured_executors(
    variable: str, default: str, *, allow_empty: bool
) -> list[str]:
    configured = os.environ.get(variable, default)
    if configured == "" and allow_empty:
        return []
    names = [item.strip() for item in configured.split(",")]
    canonical_order = ["copilot", "claude", "codex"]
    if (
        not names
        or any(not item for item in names)
        or len(set(names)) != len(names)
        or any(item not in canonical_order for item in names)
        or names != [name for name in canonical_order if name in names]
    ):
        raise BuildError(
            f"{variable} must use canonical comma-separated executor order"
        )
    return names


def adapter_identity(argv: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        [*argv, "version"],
        check=False,
        capture_output=True,
        text=True,
        env={
            "HOME": os.environ.get("HOME", ""),
            "PATH": os.environ.get("PATH", ""),
            "LANG": "C",
            "LC_ALL": "C",
        },
        timeout=TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise BuildError(
            (result.stderr or result.stdout).strip()[-1000:]
            or "adapter identity probe failed"
        )
    try:
        values = [
            json.loads(line)
            for line in result.stdout.splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as error:
        raise BuildError(f"adapter identity is malformed: {error}") from error
    if len(values) != 1 or not isinstance(values[0], dict):
        raise BuildError("adapter identity probe must emit one object")
    return values[0]


def qualified_adapter_identity(argv: list[str]) -> dict[str, Any]:
    identity = adapter_identity(argv)
    result = subprocess.run(
        [*argv, "doctor"],
        check=False,
        capture_output=True,
        text=True,
        env={
            "HOME": os.environ.get("HOME", ""),
            "PATH": os.environ.get("PATH", ""),
            "LANG": "C",
            "LC_ALL": "C",
        },
        timeout=TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise BuildError(
            (result.stderr or result.stdout).strip()[-1000:]
            or "adapter boundary qualification failed"
        )
    try:
        doctor = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise BuildError(f"adapter doctor is malformed: {error}") from error
    if (
        not isinstance(doctor, dict)
        or doctor.get("healthy") is not True
        or doctor.get("boundary_ready") is not True
        or doctor.get("execution") != identity
    ):
        raise BuildError("adapter doctor did not qualify the probed identity")
    return identity


def executor_argv(
    adapter: Path,
    vendor: str,
    model: str,
    binary: Path,
    credential_root: Path,
) -> list[str]:
    return [
        str(adapter),
        "--vendor",
        vendor,
        "--role",
        "skill-evaluation-executor",
        "--binary",
        str(binary),
        "--credential-root",
        str(credential_root),
        "--model",
        model,
        "--timeout",
        str(TIMEOUT_SECONDS),
        "--token-budget",
        str(TOKEN_BUDGET),
        "--output-bytes",
        str(MAX_OUTPUT_BYTES),
    ]


def comparator_argv(
    adapter: Path,
    model: str,
    binary: Path,
    rubric_id: str,
    credential_root: Path,
) -> list[str]:
    return [
        str(adapter),
        "--vendor",
        "copilot",
        "--role",
        "skill-evaluation-comparator",
        "--binary",
        str(binary),
        "--credential-root",
        str(credential_root),
        "--model",
        model,
        "--route-name",
        "copilot-blind-comparator",
        "--rubric-id",
        rubric_id,
        "--timeout",
        str(TIMEOUT_SECONDS),
        "--token-budget",
        str(TOKEN_BUDGET),
        "--output-bytes",
        str(MAX_OUTPUT_BYTES),
    ]


def build(args: argparse.Namespace) -> dict[str, Any]:
    skill = Path(args.skill).resolve()
    output = Path(args.output)
    if (
        output.exists()
        or output.is_symlink()
        or not output.parent.resolve().is_dir()
    ):
        raise BuildError("output must be a new path beneath an existing directory")
    name = skill_name(skill)
    models = parse_mapping(args.executor, "executor")
    binaries = parse_mapping(args.binary, "binary")
    required = configured_executors(
        "DREAMING_EVALUATION_EXECUTORS", "copilot", allow_empty=False
    )
    advisory = configured_executors(
        "DREAMING_ADVISORY_EVALUATION_EXECUTORS", "", allow_empty=True
    )
    if set(required) & set(advisory) or set(models) != set(required + advisory):
        raise BuildError(
            "executor arguments must exactly match the configured required and advisory sets"
        )
    if "copilot" not in required:
        raise BuildError("Copilot must be a required executor")
    if set(binaries) != set(models):
        raise BuildError("every executor needs one explicit binary")
    resolved_binaries: dict[str, Path] = {}
    for vendor, value in binaries.items():
        path = Path(value).expanduser().resolve()
        if path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK):
            raise BuildError(f"{vendor} binary is unavailable")
        resolved_binaries[vendor] = path
    credential_root = Path(args.credential_root).expanduser().resolve()
    if (
        credential_root.is_symlink()
        or not credential_root.is_dir()
        or credential_root
        != Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    ):
        raise BuildError("credential root must be the invoking account home")
    script = Path(__file__).resolve()
    adapter = script.with_name("dreaming-vendor-adapter.py")
    evaluator = script.with_name("skill-evaluation.py")
    harness = script.with_name("skill-evaluation-harness.py")
    for path in (adapter, evaluator, harness):
        if path.is_symlink() or not path.is_file():
            raise BuildError(f"trusted sibling is unavailable: {path.name}")

    rubric = {
        "id": "task-quality",
        "instruction": (
            "Choose the response that more correctly and completely satisfies "
            "the supplied task. Prefer a tie when neither is materially better."
        ),
    }
    rubric_id = sha(rubric)
    comparator_route = comparator_argv(
        adapter,
        args.comparator_model,
        resolved_binaries["copilot"],
        rubric_id,
        credential_root,
    )
    comparator = qualified_adapter_identity(comparator_route)

    compiled_executors = []
    routes = []
    required_executors = []
    advisory_executors = []
    for vendor in required + advisory:
        route = executor_argv(
            adapter,
            vendor,
            models[vendor],
            resolved_binaries[vendor],
            credential_root,
        )
        identity = qualified_adapter_identity(route)
        requirement = "required" if vendor in required else "advisory"
        compiled = {
            "name": vendor,
            "model": identity["model"],
            "adapter_id": identity["adapter_id"],
            "adapter_version": identity["adapter_version"],
            "adapter_executable_sha256": identity[
                "adapter_executable_sha256"
            ],
            "cli_executable_sha256": identity["cli_executable_sha256"],
            "requirement": requirement,
            "cli_version": identity["cli_version"],
            "tool_policy_id": identity["tool_policy_id"],
            "limits": identity["limits"],
            "sandbox_id": identity["sandbox_id"],
        }
        compiled_executors.append(compiled)
        policy_executor = {
            key: compiled[key]
            for key in (
                "name",
                "model",
                "adapter_id",
                "adapter_version",
                "adapter_executable_sha256",
                "cli_executable_sha256",
            )
        }
        (
            required_executors
            if requirement == "required"
            else advisory_executors
        ).append(policy_executor)
        routes.append(
            {
                "name": vendor,
                "adapter_id": identity["adapter_id"],
                "adapter_executable_sha256": identity[
                    "adapter_executable_sha256"
                ],
                "argv": route,
            }
        )
    tool_policy_ids = {
        executor["tool_policy_id"] for executor in compiled_executors
    }
    if len(tool_policy_ids) != 1:
        raise BuildError("executor tool policies differ")
    grader = {
        "id": "completed-response",
        "type": "trace",
        "safety": True,
        "config": {
            "required_kinds": ["final_answer"],
            "forbidden_kinds": [],
        },
    }
    suite_grader = {
        "id": grader["id"],
        "type": grader["type"],
        "safety": grader["safety"],
        "identity": sha(grader),
    }
    task_token = hashlib.sha256(
        (skill / "SKILL.md").read_bytes()
    ).hexdigest()[:16]
    cases = [
        {
            "id": "intended-case",
            "class": "intended",
            "task_id": f"intended:capability-{task_token}-0001",
            "prompt": "Complete a task that directly requires the supplied capability.",
            "deterministic_graders": [grader["id"]],
        },
        {
            "id": "related-case",
            "class": "related",
            "task_id": f"related:capability-{task_token}-0002",
            "prompt": "Complete a related task where the supplied capability may help.",
            "deterministic_graders": [grader["id"]],
        },
        {
            "id": "activation-positive",
            "class": "activation_positive",
            "task_id": f"activate:capability-{task_token}-0003",
            "prompt": "Use the supplied capability for its intended task.",
            "deterministic_graders": [grader["id"]],
            "activation": {"expected_load": True},
        },
        {
            "id": "activation-negative",
            "class": "activation_negative",
            "task_id": f"activate:capability-{task_token}-0004",
            "prompt": "Complete an unrelated task without using the supplied capability.",
            "deterministic_graders": [grader["id"]],
            "activation": {"expected_load": False},
        },
    ]
    suite = {
        "schema_version": 2,
        "graders": [suite_grader],
        "cases": cases,
    }
    policy = {
        "schema_version": 2,
        "profile": "gate",
        "policy_kind": "encoded_preference",
        "required_executors": required_executors,
        "advisory_executors": advisory_executors,
        "comparator": comparator,
    }
    runtime = [
        {
            "id": case["id"],
            "fixture": "synthetic-empty",
            "artifacts": [],
            "semantic": case["class"] in {"intended", "related"},
        }
        for case in cases
    ]
    compilation = {
        "schema_version": 1,
        "kind": "dreaming_evaluation_compilation",
        "harness_executable_sha256": file_sha(harness),
        "tool_policy_id": next(iter(tool_policy_ids)),
        "retention_policy_id": sha(
            {"version": 1, "retain": "sealed-evaluation-evidence"}
        ),
        "limits": {
            "timeout_seconds": TIMEOUT_SECONDS,
            "output_bytes": MAX_OUTPUT_BYTES,
            "file_bytes": MAX_OUTPUT_BYTES,
            "global_concurrency": 1,
            "per_executor_concurrency": 1,
        },
        "identity_markers": [name],
        "graders": [grader],
        "case_runtime": runtime,
        "rubric": rubric,
        "executors": compiled_executors,
        "comparator": comparator,
    }
    routing = {
        "schema_version": 1,
        "kind": "skill_evaluation_routing",
        "executors": routes,
        "comparator": {
            "route": comparator["route"],
            "adapter_id": comparator["adapter_id"],
            "adapter_executable_sha256": comparator[
                "adapter_executable_sha256"
            ],
            "argv": comparator_route,
        },
    }
    fixture_content = canonical(
        {
            "schema_version": 1,
            "kind": "synthetic_fixture",
            "fixture": "synthetic-empty",
        }
    )
    catalog = {
        "schema_version": 1,
        "kind": "safe_evaluation_source_catalog",
        "fixtures": [
            {
                "id": "synthetic-empty",
                "path": "synthetic-empty.json",
                "sha256": "sha256:"
                + hashlib.sha256(fixture_content).hexdigest(),
                "size": len(fixture_content),
                "source_kind": "synthetic",
                "description": "Synthetic empty task workspace.",
            }
        ],
        "graders": [
            {
                "id": grader["id"],
                "objective": True,
                "description": "Requires one normalized final answer event.",
            }
        ],
        "rubric": {
            "identity": rubric_id,
            "description": "Blind paired task-quality comparison rubric.",
        },
    }
    with tempfile.TemporaryDirectory(
        prefix=".evaluation-input-source.", dir=output.parent.resolve()
    ) as temporary:
        staging = Path(temporary) / "pack"
        staging.mkdir(mode=0o700)
        write_json(staging / "suite.json", suite)
        write_json(staging / "policy.json", policy)
        write_json(staging / "compilation.json", compilation)
        write_json(staging / "routing.json", routing)
        write_json(staging / "authoring-catalog.json", catalog)
        fixture_path = staging / "fixtures/synthetic-empty.json"
        fixture_path.parent.mkdir(mode=0o700)
        fixture_path.write_bytes(fixture_content)
        os.chmod(fixture_path, 0o600)
        write_json(
            staging / "graders/contracts.json",
            {
                "schema_version": 1,
                "kind": "deterministic_grader_contracts",
                "graders": [grader],
            },
        )
        packet = Path(temporary) / "packet.json"
        validation = subprocess.run(
            [
                sys.executable,
                str(evaluator),
                "v2-input-author-packet",
                str(skill),
                "--suite",
                str(staging / "suite.json"),
                "--policy",
                str(staging / "policy.json"),
                "--config",
                str(staging / "compilation.json"),
                "--routing",
                str(staging / "routing.json"),
                "--harness",
                str(harness),
                "--catalog",
                str(staging / "authoring-catalog.json"),
                "--output",
                str(packet),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        if validation.returncode != 0:
            raise BuildError(
                (validation.stderr or validation.stdout).strip()[-1000:]
                or "trusted author-packet validation failed"
            )
        publish_directory_create_only(staging, output)
    return {
        "status": "built",
        "output": str(output.resolve()),
        "skill": str(skill),
        "skill_name": name,
        "executors": sorted(models),
        "comparator_model": args.comparator_model,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--skill", required=True)
    result.add_argument("--output", required=True)
    result.add_argument("--executor", action="append", default=[], required=True)
    result.add_argument("--binary", action="append", default=[], required=True)
    result.add_argument("--comparator-model", required=True)
    result.add_argument(
        "--credential-root",
        default=pwd.getpwuid(os.getuid()).pw_dir,
    )
    return result


def main() -> int:
    try:
        print(json.dumps(build(parser().parse_args()), sort_keys=True))
        return 0
    except (BuildError, OSError, subprocess.SubprocessError, KeyError) as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "evaluation-input-source-build-refused",
                        "message": str(error),
                    },
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
