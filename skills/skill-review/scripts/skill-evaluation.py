#!/usr/bin/env python3
"""Prepare, score, and verify source/sibling skill evaluations."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SUITE_SCHEMA_VERSION = 2
POLICY_SCHEMA_VERSION = 2
AUTHORITY_SCHEMA_VERSION = 3
RUNNER_VERSION = "skill-evaluation-runner-1"
PROMPT_VERSION = "skill-evaluation-prompt-1"
COMPARATOR_VERSION = "skill-evaluation-comparator-1"
CASE_FILE = ".skill-evaluation-cases.json"
POLICY_FILE = ".skill-evaluation-policy.json"
LOCAL_SIDECARS = {
    ".agent-created",
    ".agent-created.json",
    ".promotion-reviewed.json",
    CASE_FILE,
    POLICY_FILE,
    ".pinned",
}
STATUS_ALLOWLIST = {"pass", "waived"}
WAIVER_CLASSES = {"documentation-only", "reference-only", "deterministic-helper"}
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CASE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
GRADER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
EXECUTOR_NAMES = ("copilot", "claude", "codex")
CASE_CLASSES = {
    "intended",
    "related",
    "activation_positive",
    "activation_negative",
}
POLICY_KINDS = {"capability_uplift", "encoded_preference"}
PROFILES = {"gate", "iterate"}
CERTIFICATE_STATUSES = {"pass", "regression", "inconclusive", "unavailable"}
HARNESS_CONTRACT_VERSION = 1
COMPILATION_SCHEMA_VERSION = 1
RESULT_MANIFEST_KEYS = {
    "schema_version",
    "kind",
    "state",
    "collection_state",
    "executor_states",
    "input_run_id",
    "invocation_nonce",
    "harness_version",
    "harness_executable_sha256",
    "profile",
    "candidate_id",
    "suite_id",
    "grader_set_id",
    "trials",
    "pairs",
    "executor_identities",
    "comparator_identity",
    "producer_audit",
    "file_inventory",
    "result_id",
}


class EvaluationError(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{field} must be non-empty text")
    return value


def resolve_path(path: Path, field: str) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError) as exc:
        raise EvaluationError(f"cannot resolve {field} {path}: {exc}") from exc


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvaluationError(f"{path} must contain a JSON object")
    return value


def atomic_write(path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
            os.fsync(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    finally:
        os.close(directory_fd)


def inventory(skill_dir: Path, destination: Path | None = None) -> list[dict[str, Any]]:
    if not (skill_dir / "SKILL.md").is_file():
        raise EvaluationError(f"missing SKILL.md in {skill_dir}")
    files: list[dict[str, Any]] = []
    for path in sorted(skill_dir.rglob("*")):
        relative = path.relative_to(skill_dir).as_posix()
        if path.is_symlink():
            raise EvaluationError(f"{relative}: symlinks are not valid evaluation inputs")
        if path.is_dir():
            continue
        if not path.is_file():
            raise EvaluationError(f"{relative}: runtime input must be a regular file")
        if relative in LOCAL_SIDECARS:
            continue
        if path.name in LOCAL_SIDECARS:
            raise EvaluationError(f"{relative}: reserved evaluation sidecar must be at skill root")
        content = path.read_bytes()
        files.append({"path": relative, "sha256": digest(content), "size": len(content)})
        if destination is not None:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            os.chmod(target, path.stat().st_mode & 0o777)
    return files


def candidate_id(skill_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    files = inventory(skill_dir)
    return f"sha256:{digest(canonical(files))}", files


def validate_patterns(value: Any, field: str, required: bool = False) -> list[dict[str, str]]:
    if not isinstance(value, list) or (required and not value):
        raise EvaluationError(f"{field} must be {'a non-empty' if required else 'an'} list")
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise EvaluationError(f"{field}[{index}] must be an object")
        item_id = require_text(item.get("id"), f"{field}[{index}].id")
        pattern = require_text(item.get("pattern"), f"{field}[{index}].pattern")
        if item_id in seen:
            raise EvaluationError(f"{field} has duplicate id {item_id}")
        seen.add(item_id)
        try:
            re.compile(pattern, re.MULTILINE)
        except re.error as exc:
            raise EvaluationError(f"{field}[{index}].pattern is invalid: {exc}") from exc
        result.append({"id": item_id, "pattern": pattern})
    return result


def validate_case(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError(f"{field} must be an object")
    task_id = require_text(value.get("task_id"), f"{field}.task_id")
    if not TASK_ID_RE.fullmatch(task_id):
        raise EvaluationError(f"{field}.task_id is invalid")
    return {
        "task_id": task_id,
        "prompt": require_text(value.get("prompt"), f"{field}.prompt"),
        "required_regex": validate_patterns(
            value.get("required_regex"), f"{field}.required_regex", required=True
        ),
        "forbidden_regex": validate_patterns(
            value.get("forbidden_regex", []), f"{field}.forbidden_regex"
        ),
        "friction_regex": validate_patterns(
            value.get("friction_regex", []), f"{field}.friction_regex"
        ),
    }


def load_cases(path: Path) -> tuple[dict[str, Any], str]:
    raw = load_json(path)
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise EvaluationError(f"case manifest schema_version must be {SCHEMA_VERSION}")
    value = {
        "schema_version": SCHEMA_VERSION,
        "source": validate_case(raw.get("source"), "source"),
        "sibling": validate_case(raw.get("sibling"), "sibling"),
    }
    if value["source"]["task_id"] == value["sibling"]["task_id"]:
        raise EvaluationError("source and sibling task_id values must differ")
    return value, digest(canonical(value))


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise EvaluationError(f"{field} must be a sha256 identity")
    return value


def require_nonnegative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EvaluationError(f"{field} must be a non-negative integer")
    return value


def require_positive_int(value: Any, field: str) -> int:
    value = require_nonnegative_int(value, field)
    if value == 0:
        raise EvaluationError(f"{field} must be a positive integer")
    return value


def sha256_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise EvaluationError(f"{path} must be a regular executable file")
    return f"sha256:{digest(path.read_bytes())}"


def trusted_harness_path() -> Path:
    path = Path(__file__).with_name("skill-evaluation-harness.py").resolve()
    if not path.is_file() or path.is_symlink():
        raise EvaluationError("reviewed Dreaming harness executable is unavailable")
    return path


def require_trusted_harness(path: Path) -> Path:
    resolved = path.resolve()
    trusted = trusted_harness_path()
    if resolved != trusted:
        raise EvaluationError("selected harness is not the reviewed Dreaming harness executable")
    return trusted


def canonical_file_inventory(root: Path, exclude: set[str] | None = None) -> list[dict[str, Any]]:
    excluded = exclude or set()
    result: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        if path.is_symlink():
            raise EvaluationError(f"{relative}: symlinks are forbidden in sealed bundles")
        if path.is_dir():
            continue
        if not path.is_file():
            raise EvaluationError(f"{relative}: sealed input must be a regular file")
        content = path.read_bytes()
        result.append(
            {
                "path": relative,
                "sha256": f"sha256:{digest(content)}",
                "size": len(content),
            }
        )
    return result


def require_exact_keys(value: dict[str, Any], field: str, keys: set[str]) -> None:
    unknown = sorted(set(value) - keys)
    missing = sorted(keys - set(value))
    if unknown or missing:
        details = []
        if unknown:
            details.append(f"unknown keys {unknown}")
        if missing:
            details.append(f"missing keys {missing}")
        raise EvaluationError(f"{field} has {'; '.join(details)}")


def validate_suite_grader(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError(f"{field} must be an object")
    require_exact_keys(value, field, {"id", "type", "safety", "identity"})
    grader_id = require_text(value.get("id"), f"{field}.id")
    if not GRADER_ID_RE.fullmatch(grader_id):
        raise EvaluationError(f"{field}.id is invalid")
    grader_type = require_text(value.get("type"), f"{field}.type")
    if grader_type not in {"regex", "json_schema", "file", "command", "trace", "numeric"}:
        raise EvaluationError(f"{field}.type is unsupported")
    safety = value.get("safety")
    if not isinstance(safety, bool):
        raise EvaluationError(f"{field}.safety must be a boolean")
    identity = require_sha256(value.get("identity"), f"{field}.identity")
    return {"id": grader_id, "type": grader_type, "safety": safety, "identity": identity}


def validate_suite_case(value: Any, field: str, graders: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError(f"{field} must be an object")
    case_id = require_text(value.get("id"), f"{field}.id")
    if not CASE_ID_RE.fullmatch(case_id):
        raise EvaluationError(f"{field}.id is invalid")
    case_class = value.get("class")
    if case_class not in CASE_CLASSES:
        raise EvaluationError(f"{field}.class is invalid")
    task_id = require_text(value.get("task_id"), f"{field}.task_id")
    if not TASK_ID_RE.fullmatch(task_id):
        raise EvaluationError(f"{field}.task_id is invalid")
    references = value.get("deterministic_graders")
    if not isinstance(references, list) or not references:
        raise EvaluationError(f"{field}.deterministic_graders must be a non-empty list")
    if not all(isinstance(reference, str) for reference in references):
        raise EvaluationError(f"{field}.deterministic_graders must contain grader IDs")
    if len(set(references)) != len(references):
        raise EvaluationError(f"{field}.deterministic_graders has duplicate grader IDs")
    missing = sorted(set(references) - set(graders))
    if missing:
        raise EvaluationError(f"{field}.deterministic_graders references unknown graders {missing}")
    if not any(graders[reference]["safety"] for reference in references):
        raise EvaluationError(f"{field} must reference a deterministic safety grader")
    allowed = {"id", "class", "task_id", "prompt", "deterministic_graders"}
    if case_class.startswith("activation_"):
        allowed.add("activation")
    require_exact_keys(value, field, allowed)
    result = {
        "id": case_id,
        "class": case_class,
        "task_id": task_id,
        "prompt": require_text(value.get("prompt"), f"{field}.prompt"),
        "deterministic_graders": references,
    }
    if case_class.startswith("activation_"):
        trigger = value.get("activation")
        if not isinstance(trigger, dict):
            raise EvaluationError(f"{field}.activation must be an object")
        require_exact_keys(trigger, f"{field}.activation", {"expected_load"})
        result["activation"] = {
            "expected_load": trigger.get("expected_load"),
        }
        if result["activation"]["expected_load"] != (case_class == "activation_positive"):
            raise EvaluationError(f"{field}.activation.expected_load does not match case class")
    elif "activation" in value:
        raise EvaluationError(f"{field}.activation is valid only for activation cases")
    return result


def compile_legacy_cases(raw: dict[str, Any]) -> dict[str, Any]:
    """Compile M2 source/sibling input without inventing M5 activation authority."""
    source = validate_case(raw.get("source"), "source")
    sibling = validate_case(raw.get("sibling"), "sibling")
    if source["task_id"] == sibling["task_id"]:
        raise EvaluationError("source and sibling task_id values must differ")
    grader_id = "legacy-regex"
    legacy_grader = {
        "id": grader_id,
        "type": "regex",
        "safety": True,
        "identity": f"sha256:{digest(canonical({'version': 1, 'kind': 'legacy-regex'}))}",
    }
    cases = []
    for case_id, case_class, case in (
        ("legacy-intended", "intended", source),
        ("legacy-related", "related", sibling),
    ):
        cases.append(
            {
                "id": case_id,
                "class": case_class,
                "task_id": case["task_id"],
                "prompt": case["prompt"],
                "deterministic_graders": [grader_id],
            }
        )
    suite = {
        "schema_version": SUITE_SCHEMA_VERSION,
        "compiled_from_schema_version": 1,
        "cross_executor_authority": False,
        "graders": [legacy_grader],
        "cases": cases,
    }
    return suite


def load_suite(path: Path) -> tuple[dict[str, Any], str]:
    raw = load_json(path)
    schema_version = raw.get("schema_version")
    normalized = "compiled_from_schema_version" in raw or "cross_executor_authority" in raw
    if normalized and (
        not isinstance(schema_version, int)
        or schema_version != SUITE_SCHEMA_VERSION
    ):
        raise EvaluationError("normalized suite schema_version must be integer 2")
    if schema_version == 1:
        suite = compile_legacy_cases(raw)
        return suite, f"sha256:{digest(canonical(suite))}"
    if schema_version != SUITE_SCHEMA_VERSION:
        raise EvaluationError("suite schema_version must be 1 or 2")
    keys = {"schema_version", "graders", "cases"}
    if normalized:
        keys.update({"compiled_from_schema_version", "cross_executor_authority"})
    require_exact_keys(raw, "suite", keys)
    if normalized:
        if raw.get("compiled_from_schema_version") is not None:
            raise EvaluationError("normalized version-2 suite has invalid source schema")
        if raw.get("cross_executor_authority") is not True:
            raise EvaluationError("normalized version-2 suite must retain cross-executor authority")
    graders_value = raw.get("graders")
    if not isinstance(graders_value, list) or not graders_value:
        raise EvaluationError("suite.graders must be a non-empty list")
    graders: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(graders_value):
        validated = validate_suite_grader(item, f"suite.graders[{index}]")
        if validated["id"] in graders:
            raise EvaluationError(f"suite.graders has duplicate id {validated['id']}")
        graders[validated["id"]] = validated
    cases_value = raw.get("cases")
    if not isinstance(cases_value, list) or not cases_value:
        raise EvaluationError("suite.cases must be a non-empty list")
    cases: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    task_ids: set[str] = set()
    classes: set[str] = set()
    for index, item in enumerate(cases_value):
        validated = validate_suite_case(item, f"suite.cases[{index}]", graders)
        if validated["id"] in case_ids:
            raise EvaluationError(f"suite.cases has duplicate id {validated['id']}")
        if validated["task_id"] in task_ids:
            raise EvaluationError(
                f"suite.cases task_id {validated['task_id']} is shared; task identities must be independent"
            )
        case_ids.add(validated["id"])
        task_ids.add(validated["task_id"])
        classes.add(validated["class"])
        cases.append(validated)
    missing_classes = sorted(CASE_CLASSES - classes)
    if missing_classes:
        raise EvaluationError(f"suite is missing required case classes {missing_classes}")
    suite = {
        "schema_version": SUITE_SCHEMA_VERSION,
        "compiled_from_schema_version": None,
        "cross_executor_authority": True,
        "graders": [graders[item["id"]] for item in graders_value],
        "cases": cases,
    }
    return suite, f"sha256:{digest(canonical(suite))}"


def validate_executor(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError(f"{field} must be an object")
    require_exact_keys(
        value,
        field,
        {
            "name",
            "model",
            "adapter_id",
            "adapter_version",
            "adapter_executable_sha256",
            "cli_executable_sha256",
        },
    )
    name = value.get("name")
    if name not in EXECUTOR_NAMES:
        raise EvaluationError(f"{field}.name must be one of {', '.join(EXECUTOR_NAMES)}")
    adapter_version = value.get("adapter_version")
    if not isinstance(adapter_version, int) or isinstance(adapter_version, bool) or adapter_version < 1:
        raise EvaluationError(f"{field}.adapter_version must be a positive integer")
    return {
        "name": name,
        "model": require_text(value.get("model"), f"{field}.model"),
        "adapter_id": require_sha256(value.get("adapter_id"), f"{field}.adapter_id"),
        "adapter_version": adapter_version,
        "adapter_executable_sha256": require_sha256(
            value.get("adapter_executable_sha256"), f"{field}.adapter_executable_sha256"
        ),
        "cli_executable_sha256": require_sha256(
            value.get("cli_executable_sha256"), f"{field}.cli_executable_sha256"
        ),
    }


def load_policy(path: Path) -> tuple[dict[str, Any], str]:
    raw = load_json(path)
    if raw.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise EvaluationError(f"policy schema_version must be {POLICY_SCHEMA_VERSION}")
    normalized = "trials_per_arm" in raw
    keys = {
        "schema_version",
        "profile",
        "policy_kind",
        "required_executors",
        "advisory_executors",
        "comparator",
    }
    if normalized:
        keys.add("trials_per_arm")
    require_exact_keys(raw, "policy", keys)
    profile = raw.get("profile")
    if profile not in PROFILES:
        raise EvaluationError("policy.profile must be gate or iterate")
    trials_per_arm = 3 if profile == "gate" else 1
    retained_trials = raw.get("trials_per_arm")
    if normalized and (
        not isinstance(retained_trials, int)
        or isinstance(retained_trials, bool)
        or retained_trials != trials_per_arm
    ):
        raise EvaluationError(
            "normalized policy trials_per_arm must be the profile-derived integer"
        )
    policy_kind = raw.get("policy_kind")
    if policy_kind not in POLICY_KINDS:
        raise EvaluationError("policy.policy_kind must be capability_uplift or encoded_preference")
    required_value = raw.get("required_executors")
    if not isinstance(required_value, list) or not required_value:
        raise EvaluationError("policy.required_executors must be a non-empty ordered list")
    required_executors = [
        validate_executor(item, f"policy.required_executors[{index}]")
        for index, item in enumerate(required_value)
    ]
    advisory_value = raw.get("advisory_executors")
    if not isinstance(advisory_value, list):
        raise EvaluationError("policy.advisory_executors must be an ordered list")
    advisory_executors = [
        validate_executor(item, f"policy.advisory_executors[{index}]")
        for index, item in enumerate(advisory_value)
    ]
    for field, executors in (
        ("required_executors", required_executors),
        ("advisory_executors", advisory_executors),
    ):
        names = [executor["name"] for executor in executors]
        if len(set(names)) != len(names):
            raise EvaluationError(f"policy.{field} cannot repeat an executor")
        canonical_order = [name for name in EXECUTOR_NAMES if name in names]
        if names != canonical_order:
            raise EvaluationError(f"policy.{field} must follow copilot, claude, codex order")
    required_names = {executor["name"] for executor in required_executors}
    advisory_names = {executor["name"] for executor in advisory_executors}
    if required_names & advisory_names:
        raise EvaluationError("policy required and advisory executors must be disjoint")
    comparator_value = raw.get("comparator")
    if not isinstance(comparator_value, dict):
        raise EvaluationError("policy.comparator must be an object")
    require_exact_keys(
        comparator_value,
        "policy.comparator",
        {
            "route",
            "model",
            "adapter_id",
            "adapter_version",
            "adapter_executable_sha256",
            "timeout_seconds",
            "token_budget",
            "rubric_id",
        },
    )
    comparator = {
        "route": require_text(comparator_value.get("route"), "policy.comparator.route"),
        "model": require_text(comparator_value.get("model"), "policy.comparator.model"),
        "adapter_id": require_sha256(comparator_value.get("adapter_id"), "policy.comparator.adapter_id"),
        "adapter_version": comparator_value.get("adapter_version"),
        "adapter_executable_sha256": require_sha256(
            comparator_value.get("adapter_executable_sha256"),
            "policy.comparator.adapter_executable_sha256",
        ),
        "timeout_seconds": require_nonnegative_int(
            comparator_value.get("timeout_seconds"), "policy.comparator.timeout_seconds"
        ),
        "token_budget": require_nonnegative_int(
            comparator_value.get("token_budget"), "policy.comparator.token_budget"
        ),
        "rubric_id": require_sha256(comparator_value.get("rubric_id"), "policy.comparator.rubric_id"),
    }
    if not isinstance(comparator["adapter_version"], int) or isinstance(comparator["adapter_version"], bool) or comparator["adapter_version"] < 1:
        raise EvaluationError("policy.comparator.adapter_version must be a positive integer")
    policy = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "profile": profile,
        "trials_per_arm": trials_per_arm,
        "policy_kind": policy_kind,
        "required_executors": required_executors,
        "advisory_executors": advisory_executors,
        "comparator": comparator,
    }
    return policy, policy_identity(policy)


def policy_identity(policy: dict[str, Any]) -> str:
    authority_policy = {
        key: policy[key]
        for key in (
            "schema_version",
            "profile",
            "trials_per_arm",
            "policy_kind",
            "required_executors",
            "comparator",
        )
    }
    return f"sha256:{digest(canonical(authority_policy))}"


def observation_plan_identity(policy: dict[str, Any], policy_id: str) -> str:
    return f"sha256:{digest(canonical({
        'schema_version': POLICY_SCHEMA_VERSION,
        'policy_id': policy_id,
        'advisory_executors': policy['advisory_executors'],
    }))}"


def cli_version(copilot: str) -> str:
    result = subprocess.run(
        [copilot, "--version"], check=True, capture_output=True, text=True, timeout=30
    )
    return require_text(result.stdout.strip(), "Copilot CLI version")


def runtime_contract(candidate: str, cases_sha: str, model: str, cli: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate,
        "case_manifest_sha256": cases_sha,
        "model": model,
        "cli_version": cli,
        "runner_version": RUNNER_VERSION,
        "prompt_version": PROMPT_VERSION,
        "comparator_version": COMPARATOR_VERSION,
        "flags": [
            "--effort=low",
            "--available-tools=skill,view",
            "--allow-tool=skill,view",
            "--no-custom-instructions",
            "--disable-builtin-mcps",
            "--disallow-temp-dir",
            "--no-remote",
            "--no-color",
            "--output-format=json",
            "--log-level=error",
        ],
        "working_directory": "fresh-empty-directory",
        "baseline_plugin": None,
        "candidate_plugin": "immutable-candidate-snapshot",
    }


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    run_dir = Path(args.run_dir).resolve()
    plugin = Path(args.plugin_dir).resolve()
    cases_path = Path(args.cases or skill_dir / CASE_FILE).resolve()
    if args.model == "auto":
        raise EvaluationError("evaluation requires an explicit non-auto model")
    cases, cases_sha = load_cases(cases_path)
    name_match = re.search(
        r"(?m)^name:\s*([a-z0-9][a-z0-9-]*)\s*$",
        (skill_dir / "SKILL.md").read_text(encoding="utf-8"),
    )
    if not name_match:
        raise EvaluationError("SKILL.md must contain a simple kebab-case name")
    name = name_match.group(1)
    destination = plugin / "skills" / name
    destination.mkdir(parents=True, exist_ok=True)
    files = inventory(skill_dir, destination)
    current_id = f"sha256:{digest(canonical(files))}"
    copilot = os.environ.get("COPILOT_BIN", str(Path.home() / ".local/bin/copilot"))
    contract = runtime_contract(current_id, cases_sha, args.model, cli_version(copilot))
    run_id = f"sha256:{digest(canonical(contract))}"
    atomic_write(
        plugin / ".claude-plugin" / "plugin.json",
        {
            "name": "skill-evaluation-candidate",
            "version": "0.0.0",
            "skills": [f"./skills/{name}"],
        },
        mode=0o644,
    )
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "skill": name,
        "skill_path": str(skill_dir),
        "cases_path": str(cases_path),
        "candidate_inventory": files,
        "candidate_id": current_id,
        "case_manifest_sha256": cases_sha,
        "source_case_id": f"sha256:{digest(canonical(cases['source']))}",
        "sibling_case_id": f"sha256:{digest(canonical(cases['sibling']))}",
        "run_id": run_id,
        "runtime": contract,
        "cases": cases,
    }
    atomic_write(run_dir / "metadata.json", metadata)
    for case_name in ("source", "sibling"):
        (run_dir / f"{case_name}.prompt").write_text(
            cases[case_name]["prompt"].rstrip()
            + "\n\nAnswer the task directly. Do not discuss this evaluation.\n",
            encoding="utf-8",
        )
    return metadata


def parse_run(path: Path, skill: str, require_skill: bool, expected_model: str) -> dict[str, Any]:
    messages: list[str] = []
    result_event: dict[str, Any] | None = None
    loaded = False
    models: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return {"valid": False, "error": str(exc)}
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return {"valid": False, "error": "non-JSON output in structured run log"}
        event_type = event.get("type")
        data = event.get("data", {})
        if not isinstance(data, dict):
            return {"valid": False, "error": f"{event_type} data must be an object"}
        if event_type == "assistant.message" and isinstance(data.get("content"), str):
            if isinstance(data.get("model"), str):
                models.add(data["model"])
            if data["content"].strip():
                messages.append(data["content"])
        elif event_type == "tool.execution_start":
            if isinstance(data.get("model"), str):
                models.add(data["model"])
            arguments = data.get("arguments")
            loaded = (
                loaded
                or data.get("toolName") == "skill"
                and isinstance(arguments, dict)
                and arguments.get("skill") == skill
            )
        elif event_type == "result":
            result_event = event
    if result_event is None or result_event.get("exitCode") != 0:
        return {"valid": False, "error": "missing successful result event"}
    if not messages:
        return {"valid": False, "error": "missing final assistant message"}
    if models != {expected_model}:
        return {
            "valid": False,
            "error": f"run used model identities {sorted(models)!r}, expected {expected_model!r}",
        }
    if loaded != require_skill:
        expected = "load" if require_skill else "not load"
        return {"valid": False, "error": f"candidate skill must {expected} in this run"}
    return {"valid": True, "answer": messages[-1], "skill_loaded": loaded}


def score(run: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    if not run.get("valid"):
        return {**run, "passed": False, "friction_count": None}
    answer = run["answer"]
    required = {
        item["id"]: bool(re.search(item["pattern"], answer, re.MULTILINE))
        for item in case["required_regex"]
    }
    forbidden = {
        item["id"]: len(re.findall(item["pattern"], answer, re.MULTILINE))
        for item in case["forbidden_regex"]
    }
    friction = {
        item["id"]: len(re.findall(item["pattern"], answer, re.MULTILINE))
        for item in case["friction_regex"]
    }
    return {
        **run,
        "required": required,
        "forbidden": forbidden,
        "friction": friction,
        "passed": all(required.values()) and not any(forbidden.values()),
        "friction_count": sum(friction.values()),
    }


def evaluation_dir() -> Path:
    root = Path(os.environ.get("SKILLS_STATE_DIR", str(Path.home() / ".copilot/skill-state")))
    return root / "skill-review" / "evaluations"


def latest_key(skill_path: str) -> str:
    return digest(str(Path(skill_path).resolve()).encode())


def write_receipt(receipt: dict[str, Any]) -> tuple[Path, str]:
    receipt_bytes = canonical(receipt)
    receipt_sha = digest(receipt_bytes)
    path = evaluation_dir() / "receipts" / f"{receipt_sha}.json"
    if path.exists():
        existing = load_json(path)
        if canonical(existing) != receipt_bytes:
            raise EvaluationError("content-addressed receipt collision")
    else:
        atomic_write(path, receipt)
    atomic_write(
        evaluation_dir() / "latest" / f"{latest_key(receipt['skill_path'])}.json",
        {
            "schema_version": SCHEMA_VERSION,
            "skill_path": receipt["skill_path"],
            "receipt_sha256": receipt_sha,
            "receipt_path": str(path),
        },
    )
    return path, receipt_sha


def update_envelope(skill_dir: Path, receipt_path: Path) -> None:
    envelope = skill_dir / ".agent-created.json"
    if not envelope.exists():
        return
    helper = Path(__file__).with_name("evidence-envelope.py")
    subprocess.run(
        [str(helper), "set-evaluation", str(envelope), str(receipt_path)],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def finalize(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir).resolve()
    metadata = load_json(run_dir / "metadata.json")
    cases = metadata["cases"]
    runs = {
        "source_baseline": score(
            parse_run(
                run_dir / "source-baseline.jsonl",
                metadata["skill"],
                False,
                metadata["runtime"]["model"],
            ),
            cases["source"],
        ),
        "source_candidate": score(
            parse_run(
                run_dir / "source-candidate.jsonl",
                metadata["skill"],
                True,
                metadata["runtime"]["model"],
            ),
            cases["source"],
        ),
        "sibling_baseline": score(
            parse_run(
                run_dir / "sibling-baseline.jsonl",
                metadata["skill"],
                False,
                metadata["runtime"]["model"],
            ),
            cases["sibling"],
        ),
        "sibling_candidate": score(
            parse_run(
                run_dir / "sibling-candidate.jsonl",
                metadata["skill"],
                True,
                metadata["runtime"]["model"],
            ),
            cases["sibling"],
        ),
    }
    valid = all(run["valid"] for run in runs.values())
    source_before = runs["source_baseline"]
    source_after = runs["source_candidate"]
    sibling_before = runs["sibling_baseline"]
    sibling_after = runs["sibling_candidate"]
    source_improved = bool(valid and not source_before["passed"] and source_after["passed"])
    sibling_regressed = bool(
        valid
        and sibling_before["passed"]
        and (
            not sibling_after["passed"]
            or sibling_after["friction_count"] > sibling_before["friction_count"]
        )
    )
    if valid and (not source_after["passed"] or sibling_regressed):
        status = "regression"
    elif valid and sibling_before["passed"] and source_improved and not sibling_regressed:
        status = "pass"
    else:
        status = "inconclusive"
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": "evaluation",
        "status": status,
        "evaluated_at": now_iso(),
        "skill": metadata["skill"],
        "skill_path": metadata["skill_path"],
        "candidate_id": metadata["candidate_id"],
        "candidate_inventory": metadata["candidate_inventory"],
        "run_id": metadata["run_id"],
        "case_manifest_sha256": metadata["case_manifest_sha256"],
        "cases_path": metadata["cases_path"],
        "source_case_id": metadata["source_case_id"],
        "sibling_case_id": metadata["sibling_case_id"],
        "runtime": metadata["runtime"],
        "source_improved": source_improved,
        "sibling_regressed": sibling_regressed,
        "runs": runs,
    }
    receipt_path, receipt_sha = write_receipt(receipt)
    update_envelope(Path(metadata["skill_path"]), receipt_path)
    return {"status": status, "receipt": str(receipt_path), "receipt_sha256": receipt_sha}


def verify_receipt_bytes(pointer: dict[str, Any]) -> tuple[dict[str, Any], str]:
    path = Path(require_text(pointer.get("receipt_path"), "receipt_path"))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EvaluationError(f"cannot read receipt: {exc}") from exc
    receipt_sha = digest(canonical(json.loads(raw)))
    if receipt_sha != pointer.get("receipt_sha256") or path.name != f"{receipt_sha}.json":
        raise EvaluationError("receipt hash or content-addressed path does not match")
    receipt = json.loads(raw)
    if not isinstance(receipt, dict):
        raise EvaluationError("receipt must be a JSON object")
    return receipt, receipt_sha


def gate(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    pointer_path = evaluation_dir() / "latest" / f"{latest_key(str(skill_dir))}.json"
    pointer = load_json(pointer_path)
    if pointer.get("skill_path") != str(skill_dir):
        raise EvaluationError("latest evaluation pointer belongs to another skill path")
    receipt, receipt_sha = verify_receipt_bytes(pointer)
    if receipt.get("status") not in STATUS_ALLOWLIST:
        raise EvaluationError(f"evaluation status {receipt.get('status')!r} is not allowed")
    current_id, _ = candidate_id(skill_dir)
    if receipt.get("candidate_id") != current_id:
        raise EvaluationError("evaluation is stale for the current candidate inventory")
    if receipt.get("kind") == "evaluation":
        cases, cases_sha = load_cases(Path(receipt["cases_path"]))
        if cases_sha != receipt.get("case_manifest_sha256"):
            raise EvaluationError("evaluation case manifest changed after the run")
        expected = runtime_contract(
            current_id,
            cases_sha,
            receipt["runtime"]["model"],
            receipt["runtime"]["cli_version"],
        )
        if receipt.get("runtime") != expected:
            raise EvaluationError("evaluation runtime contract is malformed")
        if receipt.get("run_id") != f"sha256:{digest(canonical(expected))}":
            raise EvaluationError("evaluation run_id does not match runtime inputs")
        if receipt.get("source_case_id") != f"sha256:{digest(canonical(cases['source']))}":
            raise EvaluationError("source case identity changed")
        if receipt.get("sibling_case_id") != f"sha256:{digest(canonical(cases['sibling']))}":
            raise EvaluationError("sibling case identity changed")
    elif receipt.get("kind") == "waiver":
        anchor_sha = require_text(
            receipt.get("waived_from_receipt_sha256"),
            "waived_from_receipt_sha256",
        )
        anchor_pointer = {
            "receipt_path": str(evaluation_dir() / "receipts" / f"{anchor_sha}.json"),
            "receipt_sha256": anchor_sha,
        }
        anchor, _ = verify_receipt_bytes(anchor_pointer)
        verify_evaluation_anchor(anchor, skill_dir)
        if (
            receipt.get("base_candidate_id") != anchor.get("candidate_id")
            or receipt.get("base_run_id") != anchor.get("run_id")
        ):
            raise EvaluationError("waiver does not bind its passing evaluation")
    else:
        raise EvaluationError("receipt kind is invalid")
    envelope_path = skill_dir / ".agent-created.json"
    if envelope_path.exists():
        envelope = load_json(envelope_path)
        evaluation = envelope.get("evaluation", {})
        if (
            evaluation.get("status") != receipt.get("status")
            or evaluation.get("candidate_id") != current_id
            or evaluation.get("receipt_sha256") != receipt_sha
        ):
            raise EvaluationError("evidence envelope does not mirror the current receipt")
    return {"status": receipt["status"], "candidate_id": current_id, "receipt_sha256": receipt_sha}


def verify_evaluation_anchor(receipt: dict[str, Any], skill_dir: Path) -> None:
    if (
        receipt.get("kind") != "evaluation"
        or receipt.get("status") != "pass"
        or receipt.get("skill_path") != str(skill_dir)
    ):
        raise EvaluationError("waiver anchor must be a passing evaluation for this skill")
    files = receipt.get("candidate_inventory")
    if not isinstance(files, list):
        raise EvaluationError("waiver anchor is missing candidate inventory")
    if receipt.get("candidate_id") != f"sha256:{digest(canonical(files))}":
        raise EvaluationError("waiver anchor candidate inventory is malformed")
    cases, cases_sha = load_cases(Path(receipt["cases_path"]))
    expected = runtime_contract(
        receipt["candidate_id"],
        cases_sha,
        receipt["runtime"]["model"],
        receipt["runtime"]["cli_version"],
    )
    if (
        receipt.get("case_manifest_sha256") != cases_sha
        or receipt.get("runtime") != expected
        or receipt.get("run_id") != f"sha256:{digest(canonical(expected))}"
        or receipt.get("source_case_id")
        != f"sha256:{digest(canonical(cases['source']))}"
        or receipt.get("sibling_case_id")
        != f"sha256:{digest(canonical(cases['sibling']))}"
    ):
        raise EvaluationError("waiver anchor evaluation contract is stale or malformed")


def waive(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    base_path = Path(args.base_receipt).resolve()
    if base_path.parent != (evaluation_dir() / "receipts").resolve():
        raise EvaluationError("base receipt must come from the evaluation receipt store")
    base_sha = base_path.stem
    base, verified_base_sha = verify_receipt_bytes(
        {"receipt_path": str(base_path), "receipt_sha256": base_sha}
    )
    verify_evaluation_anchor(base, skill_dir)
    current_id, current_files = candidate_id(skill_dir)
    before = {item["path"]: item for item in base["candidate_inventory"]}
    after = {item["path"]: item for item in current_files}
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    if not changed:
        raise EvaluationError("waiver requires an actual candidate change")
    if "SKILL.md" in changed:
        raise EvaluationError("SKILL.md changes cannot be waived")
    waiver_class = args.waiver_class
    if waiver_class in {"documentation-only", "reference-only"}:
        raise EvaluationError(f"{waiver_class} cannot waive runtime-visible skill files")
    if not all(path.startswith("scripts/") for path in changed):
        raise EvaluationError("deterministic-helper waivers may change only scripts/")
    if not args.test_script:
        raise EvaluationError("deterministic-helper waiver requires --test-script")
    test_path = (skill_dir / args.test_script).resolve()
    try:
        test_relative = test_path.relative_to(skill_dir).as_posix()
    except ValueError as exc:
        raise EvaluationError("test script must remain inside the skill") from exc
    if not test_relative.startswith("scripts/") or test_relative in changed:
        raise EvaluationError("test script must be an unchanged scripts/ file")
    if before.get(test_relative) != after.get(test_relative):
        raise EvaluationError("test script must match the base snapshot exactly")
    result = subprocess.run(
        [str(test_path)],
        cwd=skill_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise EvaluationError("deterministic helper test command failed")
    try:
        attestation = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise EvaluationError("helper test must emit one JSON attestation") from exc
    expected_files = {path: after[path]["sha256"] for path in changed}
    if not isinstance(attestation, dict) or attestation != {
        "status": "pass",
        "verified_files": expected_files,
    }:
        raise EvaluationError("helper test attestation does not bind every changed file")
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": "waiver",
        "status": "waived",
        "evaluated_at": now_iso(),
        "skill": skill_dir.name,
        "skill_path": str(skill_dir),
        "candidate_id": current_id,
        "base_candidate_id": base["candidate_id"],
        "base_run_id": base["run_id"],
        "waived_from_receipt_sha256": verified_base_sha,
        "changed_paths": changed,
        "waiver_class": waiver_class,
        "waiver_reason": require_text(args.reason, "waiver reason"),
        "test_script": test_relative,
        "test_attestation": attestation,
    }
    receipt_path, receipt_sha = write_receipt(receipt)
    update_envelope(skill_dir, receipt_path)
    return {"status": "waived", "receipt": str(receipt_path), "receipt_sha256": receipt_sha}


def v2_evaluation_dir() -> Path:
    return evaluation_dir() / "v2"


def v2_receipt_path(receipt_sha256: str) -> Path:
    return v2_evaluation_dir() / "receipts" / f"{receipt_sha256}.json"


def v2_authority_path(skill_dir: Path, current_candidate_id: str) -> Path:
    return (
        v2_evaluation_dir()
        / "authority"
        / latest_key(str(skill_dir))
        / f"{current_candidate_id}.json"
    )


def v2_certification_path(aggregate_sha256: str) -> Path:
    return v2_evaluation_dir() / "certifications" / f"{aggregate_sha256}.json"


def v2_latest_waiver_path(skill_dir: Path) -> Path:
    return v2_evaluation_dir() / "latest-waiver" / f"{latest_key(str(skill_dir))}.json"


def v2_portfolio_receipt_path(receipt_sha256: str) -> Path:
    return v2_evaluation_dir() / "dashboard-v1" / "portfolio" / f"{receipt_sha256}.json"


def v2_portfolio_pointer_path(aggregate_sha256: str) -> Path:
    return (
        v2_evaluation_dir()
        / "dashboard-v1"
        / "portfolio-by-aggregate"
        / f"{aggregate_sha256}.json"
    )


def v2_transition_dir(skill_dir: Path) -> Path:
    return (
        v2_evaluation_dir()
        / "dashboard-v1"
        / "authority-transitions"
        / latest_key(str(skill_dir))
    )


def identity_with(field: str, value: dict[str, Any]) -> str:
    return f"sha256:{digest(canonical({key: item for key, item in value.items() if key != field}))}"


def validate_certificate(
    value: Any,
    candidate: str,
    suite_id: str,
    policy_id: str,
    observation_plan_id: str,
    profile: str,
    requirement: str,
    expected_executor: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    field = f"aggregate.certificates[{index}]"
    if not isinstance(value, dict):
        raise EvaluationError(f"{field} must be an object")
    require_exact_keys(
        value,
        field,
        {
            "schema_version",
            "kind",
            "status",
            "candidate_id",
            "suite_id",
            "policy_id",
            "observation_plan_id",
            "profile",
            "requirement",
            "executor",
            "result_bundle_sha256",
            "result_bundle_id",
            "run_id",
            "certificate_id",
        },
    )
    if value.get("schema_version") != 2 or value.get("kind") != "executor_certificate":
        raise EvaluationError(f"{field} must be a schema-v2 executor_certificate")
    if value.get("status") not in CERTIFICATE_STATUSES:
        raise EvaluationError(f"{field}.status is invalid")
    if value.get("candidate_id") != candidate or value.get("suite_id") != suite_id or value.get("policy_id") != policy_id:
        raise EvaluationError(f"{field} does not bind the aggregate inputs")
    if value.get("profile") != profile:
        raise EvaluationError(f"{field}.profile does not match policy")
    if value.get("requirement") != requirement:
        raise EvaluationError(f"{field}.requirement does not match its aggregate partition")
    expected_observation = observation_plan_id if requirement == "advisory" else None
    if value.get("observation_plan_id") != expected_observation:
        raise EvaluationError(f"{field}.observation_plan_id does not match its partition")
    require_sha256(value.get("result_bundle_sha256"), f"{field}.result_bundle_sha256")
    require_sha256(value.get("result_bundle_id"), f"{field}.result_bundle_id")
    require_sha256(value.get("run_id"), f"{field}.run_id")
    executor = validate_executor(value.get("executor"), f"{field}.executor")
    if executor != expected_executor:
        raise EvaluationError(f"{field}.executor does not match its aggregate partition")
    expected_id = identity_with("certificate_id", value)
    if value.get("certificate_id") != expected_id:
        raise EvaluationError(f"{field}.certificate_id does not match certificate content")
    return {**value, "executor": executor}


def validate_aggregate(
    value: Any, skill_dir: Path, candidate: str, suite: dict[str, Any], suite_id: str,
    policy: dict[str, Any], policy_id: str, allow_advisory_drift: bool = False
) -> dict[str, Any]:
    if not suite["cross_executor_authority"]:
        raise EvaluationError("legacy-compiled suites cannot anchor cross-executor authority")
    if not isinstance(value, dict):
        raise EvaluationError("aggregate receipt must be an object")
    require_exact_keys(
        value,
        "aggregate",
        {
            "schema_version",
            "kind",
            "status",
            "skill_path",
            "candidate_id",
            "candidate_inventory",
            "suite_id",
            "policy_id",
            "observation_plan_id",
            "profile",
            "required_executors",
            "advisory_executors",
            "certificates",
            "required_certificate_set_id",
            "aggregate_id",
        },
    )
    if value.get("schema_version") != 2 or value.get("kind") != "aggregate_receipt":
        raise EvaluationError("aggregate receipt must be schema-v2 aggregate_receipt")
    if value.get("skill_path") != str(skill_dir):
        raise EvaluationError("aggregate receipt belongs to another skill path")
    if (
        value.get("candidate_id") != candidate
        or value.get("suite_id") != suite_id
        or value.get("policy_id") != policy_id
        or value.get("profile") != policy["profile"]
    ):
        raise EvaluationError("aggregate receipt input identity is stale or malformed")
    candidate_inventory = value.get("candidate_inventory")
    if not isinstance(candidate_inventory, list) or f"sha256:{digest(canonical(candidate_inventory))}" != candidate:
        raise EvaluationError("aggregate receipt candidate inventory is stale or malformed")
    required_executors = value.get("required_executors")
    advisory_executors = value.get("advisory_executors")
    if required_executors != policy["required_executors"]:
        raise EvaluationError("aggregate required executors differ from the current authority policy")
    if not isinstance(advisory_executors, list):
        raise EvaluationError("aggregate advisory_executors must be a list")
    advisory_executors = [
        validate_executor(item, f"aggregate.advisory_executors[{index}]")
        for index, item in enumerate(advisory_executors)
    ]
    advisory_names = [item["name"] for item in advisory_executors]
    if (
        len(set(advisory_names)) != len(advisory_names)
        or advisory_names != [name for name in EXECUTOR_NAMES if name in advisory_names]
        or set(advisory_names) & {item["name"] for item in required_executors}
    ):
        raise EvaluationError("aggregate advisory executor partition is invalid")
    aggregate_observation_plan_id = observation_plan_identity(
        {**policy, "advisory_executors": advisory_executors}, policy_id
    )
    if value.get("observation_plan_id") != aggregate_observation_plan_id:
        raise EvaluationError("aggregate observation_plan_id does not match advisory inputs")
    if not allow_advisory_drift and advisory_executors != policy["advisory_executors"]:
        raise EvaluationError("aggregate advisory executors differ from the current observation plan")
    certificates_value = value.get("certificates")
    if not isinstance(certificates_value, list):
        raise EvaluationError("aggregate.certificates must be a list")
    expected_partitions = [
        (requirement, executor)
        for requirement, executors in (
            ("required", required_executors),
            ("advisory", advisory_executors),
        )
        for executor in executors
    ]
    if len(certificates_value) != len(expected_partitions):
        raise EvaluationError("aggregate certificates must cover every selected executor")
    certificates = [
        validate_certificate(
            item,
            candidate,
            suite_id,
            policy_id,
            aggregate_observation_plan_id,
            policy["profile"],
            requirement,
            executor,
            index,
        )
        for index, (item, (requirement, executor)) in enumerate(
            zip(certificates_value, expected_partitions)
        )
    ]
    required_certificates = [
        certificate for certificate in certificates if certificate["requirement"] == "required"
    ]
    statuses = [certificate["status"] for certificate in required_certificates]
    expected_status = (
        "regression" if "regression" in statuses
        else "inconclusive" if any(status != "pass" for status in statuses)
        else "pass"
    )
    if value.get("status") != expected_status:
        raise EvaluationError(
            f"aggregate.status must be {expected_status!r} for the independent executor certificates"
        )
    expected_set_id = required_certificate_set_identity(
        candidate, suite_id, policy_id, policy["profile"], required_certificates
    )
    if value.get("required_certificate_set_id") != expected_set_id:
        raise EvaluationError("aggregate required_certificate_set_id does not match required evidence")
    expected_id = identity_with("aggregate_id", value)
    if value.get("aggregate_id") != expected_id:
        raise EvaluationError("aggregate.aggregate_id does not match aggregate content")
    return {**value, "certificates": certificates}


def required_certificate_set_identity(
    candidate: str,
    suite_id: str,
    policy_id: str,
    profile: str,
    certificates: list[dict[str, Any]],
) -> str:
    return f"sha256:{digest(canonical({
        'candidate_id': candidate,
        'suite_id': suite_id,
        'policy_id': policy_id,
        'profile': profile,
        'certificate_ids': [item['certificate_id'] for item in certificates],
    }))}"


def load_v2_inputs(skill_dir: Path, suite_path: str | None, policy_path: str | None) -> tuple[
    str, list[dict[str, Any]], dict[str, Any], str, dict[str, Any], str
]:
    current_candidate, files = candidate_id(skill_dir)
    suite, suite_id = load_suite(Path(suite_path).resolve() if suite_path else skill_dir / CASE_FILE)
    policy, policy_id = load_policy(
        Path(policy_path).resolve() if policy_path else skill_dir / POLICY_FILE
    )
    return current_candidate, files, suite, suite_id, policy, policy_id


def v2_prepare(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    current_candidate, files, suite, suite_id, policy, policy_id = load_v2_inputs(
        skill_dir, args.suite, args.policy
    )
    return {
        "candidate_id": current_candidate,
        "candidate_inventory": files,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "observation_plan_id": observation_plan_identity(policy, policy_id),
        "profile": policy["profile"],
        "trials_per_arm": policy["trials_per_arm"],
        "required_executors": policy["required_executors"],
        "advisory_executors": policy["advisory_executors"],
        "cross_executor_authority": suite["cross_executor_authority"],
    }


def v2_suite_validate(args: argparse.Namespace) -> dict[str, Any]:
    suite, suite_id = load_suite(Path(args.suite).resolve())
    return {
        "suite_id": suite_id,
        "schema_version": suite["schema_version"],
        "compiled_from_schema_version": suite["compiled_from_schema_version"],
        "cross_executor_authority": suite["cross_executor_authority"],
        "case_classes": [case["class"] for case in suite["cases"]],
    }


def v2_policy_validate(args: argparse.Namespace) -> dict[str, Any]:
    policy, policy_id = load_policy(Path(args.policy).resolve())
    return {
        "policy_id": policy_id,
        "observation_plan_id": observation_plan_identity(policy, policy_id),
        "profile": policy["profile"],
        "trials_per_arm": policy["trials_per_arm"],
        "required_executors": policy["required_executors"],
        "advisory_executors": policy["advisory_executors"],
        "comparator": policy["comparator"],
    }


def configured_executor_names(variable: str, default: str, allow_empty: bool) -> list[str]:
    configured = os.environ.get(variable, default)
    if configured == "" and allow_empty:
        return []
    names = [item.strip() for item in configured.split(",")]
    if not names or any(not item for item in names):
        qualifier = "an ordered comma-separated set" if allow_empty else "a non-empty ordered comma-separated set"
        raise EvaluationError(f"{variable} must be {qualifier}")
    if len(set(names)) != len(names) or any(item not in EXECUTOR_NAMES for item in names):
        raise EvaluationError(f"{variable} contains an unknown or duplicate executor")
    expected = [name for name in EXECUTOR_NAMES if name in names]
    if names != expected:
        raise EvaluationError(f"{variable} must follow copilot, claude, codex order")
    return names


def desired_executor_names() -> list[str]:
    return configured_executor_names("DREAMING_EVALUATION_EXECUTORS", "copilot", False)


def desired_advisory_executor_names() -> list[str]:
    return configured_executor_names("DREAMING_ADVISORY_EVALUATION_EXECUTORS", "", True)


def desired_executor_roles() -> tuple[list[str], list[str]]:
    required = desired_executor_names()
    advisory = desired_advisory_executor_names()
    overlap = set(required) & set(advisory)
    if overlap:
        raise EvaluationError(
            "DREAMING_EVALUATION_EXECUTORS and DREAMING_ADVISORY_EVALUATION_EXECUTORS must be disjoint"
        )
    return required, advisory


def validate_harness_executor(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError(f"{field} must be an object")
    require_exact_keys(
        value,
        field,
        {
            "name",
            "requirement",
            "model",
            "adapter_id",
            "adapter_version",
            "adapter_executable_sha256",
            "cli_executable_sha256",
            "cli_version",
            "tool_policy_id",
            "limits",
            "sandbox_id",
        },
    )
    requirement = value.get("requirement")
    if requirement not in {"required", "advisory"}:
        raise EvaluationError(f"{field}.requirement must be required or advisory")
    base = validate_executor(
        {key: value[key] for key in (
            "name", "model", "adapter_id", "adapter_version",
            "adapter_executable_sha256", "cli_executable_sha256",
        )},
        field,
    )
    limits = value.get("limits")
    if not isinstance(limits, dict):
        raise EvaluationError(f"{field}.limits must be an object")
    require_exact_keys(limits, f"{field}.limits", {"timeout_seconds", "token_budget", "output_bytes"})
    return {
        **base,
        "requirement": requirement,
        "cli_version": require_text(value.get("cli_version"), f"{field}.cli_version"),
        "tool_policy_id": require_sha256(value.get("tool_policy_id"), f"{field}.tool_policy_id"),
        "limits": {
            "timeout_seconds": require_positive_int(limits.get("timeout_seconds"), f"{field}.limits.timeout_seconds"),
            "token_budget": require_positive_int(limits.get("token_budget"), f"{field}.limits.token_budget"),
            "output_bytes": require_positive_int(limits.get("output_bytes"), f"{field}.limits.output_bytes"),
        },
        "sandbox_id": require_sha256(value.get("sandbox_id"), f"{field}.sandbox_id"),
    }


def validate_compilation_config(
    path: Path, suite: dict[str, Any], policy: dict[str, Any], harness_sha: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = load_json(path)
    require_exact_keys(
        raw,
        "compilation",
        {
            "schema_version",
            "kind",
            "harness_executable_sha256",
            "tool_policy_id",
            "retention_policy_id",
            "limits",
            "identity_markers",
            "graders",
            "case_runtime",
            "rubric",
            "executors",
            "comparator",
        },
    )
    if raw.get("schema_version") != COMPILATION_SCHEMA_VERSION or raw.get("kind") != "dreaming_evaluation_compilation":
        raise EvaluationError("unsupported Dreaming evaluation compilation config")
    if raw.get("harness_executable_sha256") != harness_sha:
        raise EvaluationError("compilation harness digest does not match the selected executable")
    tool_policy_id = require_sha256(raw.get("tool_policy_id"), "compilation.tool_policy_id")
    retention_policy_id = require_sha256(
        raw.get("retention_policy_id"), "compilation.retention_policy_id"
    )
    limits = raw.get("limits")
    if not isinstance(limits, dict):
        raise EvaluationError("compilation.limits must be an object")
    require_exact_keys(
        limits,
        "compilation.limits",
        {"timeout_seconds", "output_bytes", "file_bytes", "global_concurrency", "per_executor_concurrency"},
    )
    normalized_limits = {
        key: require_positive_int(limits.get(key), f"compilation.limits.{key}")
        for key in limits
    }
    markers = raw.get("identity_markers")
    if not isinstance(markers, list) or not markers or not all(isinstance(item, str) and item for item in markers):
        raise EvaluationError("compilation.identity_markers must be a non-empty text list")
    if len(set(markers)) != len(markers):
        raise EvaluationError("compilation.identity_markers cannot contain duplicates")
    grader_values = raw.get("graders")
    if not isinstance(grader_values, list) or not grader_values:
        raise EvaluationError("compilation.graders must be a non-empty list")
    source_graders = {item["id"]: item for item in suite["graders"]}
    graders: list[dict[str, Any]] = []
    for index, grader in enumerate(grader_values):
        field = f"compilation.graders[{index}]"
        if not isinstance(grader, dict):
            raise EvaluationError(f"{field} must be an object")
        require_exact_keys(grader, field, {"id", "type", "safety", "config"})
        grader_id = require_text(grader.get("id"), f"{field}.id")
        source = source_graders.get(grader_id)
        if source is None:
            raise EvaluationError(f"{field}.id is not declared by the schema-v2 suite")
        if grader.get("type") != source["type"] or grader.get("safety") != source["safety"]:
            raise EvaluationError(f"{field} changes the schema-v2 grader contract")
        if not isinstance(grader.get("config"), dict):
            raise EvaluationError(f"{field}.config must be an object")
        if source["identity"] != f"sha256:{digest(canonical(grader))}":
            raise EvaluationError(f"{field} does not match the suite grader identity")
        graders.append(grader)
    if {item["id"] for item in graders} != set(source_graders):
        raise EvaluationError("compilation.graders must define every suite grader exactly once")
    runtime_values = raw.get("case_runtime")
    if not isinstance(runtime_values, list):
        raise EvaluationError("compilation.case_runtime must be a list")
    runtime: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(runtime_values):
        field = f"compilation.case_runtime[{index}]"
        if not isinstance(item, dict):
            raise EvaluationError(f"{field} must be an object")
        require_exact_keys(item, field, {"id", "fixture", "artifacts", "semantic"})
        case_id = require_text(item.get("id"), f"{field}.id")
        artifacts = item.get("artifacts")
        if (
            case_id in runtime
            or not isinstance(artifacts, list)
            or len(set(artifacts)) != len(artifacts)
            or not all(isinstance(value, str) and value and not Path(value).is_absolute() and ".." not in Path(value).parts for value in artifacts)
        ):
            raise EvaluationError(f"{field} has invalid case runtime data")
        if not isinstance(item.get("semantic"), bool):
            raise EvaluationError(f"{field}.semantic must be a boolean")
        runtime[case_id] = {
            "fixture": require_text(item.get("fixture"), f"{field}.fixture"),
            "artifacts": artifacts,
            "semantic": item["semantic"],
        }
    if set(runtime) != {case["id"] for case in suite["cases"]}:
        raise EvaluationError("compilation.case_runtime must define every suite case exactly once")
    rubric = raw.get("rubric")
    if not isinstance(rubric, dict) or f"sha256:{digest(canonical(rubric))}" != policy["comparator"]["rubric_id"]:
        raise EvaluationError("compilation.rubric does not match policy comparator rubric identity")
    executors_value = raw.get("executors")
    if not isinstance(executors_value, list):
        raise EvaluationError("compilation.executors must be a list")
    executors = [
        validate_harness_executor(item, f"compilation.executors[{index}]")
        for index, item in enumerate(executors_value)
    ]
    required_names, advisory_names = desired_executor_roles()
    expected_names = required_names + advisory_names
    if [item["name"] for item in executors] != expected_names:
        raise EvaluationError("compilation executors differ from the configured required and advisory sets")
    expected_executors = [
        {**item, "requirement": requirement}
        for requirement, values in (
            ("required", policy["required_executors"]),
            ("advisory", policy["advisory_executors"]),
        )
        for item in values
    ]
    compiled_policy_executors = [
        {
            key: item[key]
            for key in (
                "name",
                "model",
                "adapter_id",
                "adapter_version",
                "adapter_executable_sha256",
                "cli_executable_sha256",
                "requirement",
            )
        }
        for item in executors
    ]
    if compiled_policy_executors != expected_executors:
        raise EvaluationError("compilation executors differ from the exact policy executor identities and roles")
    if any(item["tool_policy_id"] != tool_policy_id for item in executors):
        raise EvaluationError("executor tool policy differs from the compilation tool policy")
    comparator = raw.get("comparator")
    if comparator != policy["comparator"]:
        raise EvaluationError("compilation comparator differs from the exact policy comparator")
    harness_suite = {
        "schema_version": HARNESS_CONTRACT_VERSION,
        "kind": "skill_evaluation_suite",
        "grader_set_id": f"sha256:{digest(canonical(graders))}",
        "identity_markers": markers,
        "graders": graders,
        "cases": [
            {
                "id": case["id"],
                "class": case["class"],
                "task_id": case["task_id"],
                "prompt": case["prompt"],
                "fixture": runtime[case["id"]]["fixture"],
                "artifacts": runtime[case["id"]]["artifacts"],
                "graders": case["deterministic_graders"],
                "semantic": runtime[case["id"]]["semantic"],
            }
            for case in suite["cases"]
        ],
        "rubric": rubric,
    }
    normalized = {
        "schema_version": COMPILATION_SCHEMA_VERSION,
        "kind": "dreaming_evaluation_compilation",
        "harness_executable_sha256": harness_sha,
        "tool_policy_id": tool_policy_id,
        "retention_policy_id": retention_policy_id,
        "limits": normalized_limits,
        "identity_markers": markers,
        "graders": graders,
        "case_runtime": runtime_values,
        "rubric": rubric,
        "executors": executors,
        "comparator": comparator,
    }
    return normalized, harness_suite


def validate_routing(path: Path, executors: list[dict[str, Any]], comparator: dict[str, Any]) -> dict[str, Any]:
    routing = load_json(path)
    require_exact_keys(routing, "routing", {"schema_version", "kind", "executors", "comparator"})
    if routing.get("schema_version") != 1 or routing.get("kind") != "skill_evaluation_routing":
        raise EvaluationError("unsupported harness routing config")
    values = routing.get("executors")
    if not isinstance(values, list):
        raise EvaluationError("routing.executors must be a list")
    expected = {item["name"]: item for item in executors}
    actual: dict[str, dict[str, Any]] = {}
    for index, route in enumerate(values):
        field = f"routing.executors[{index}]"
        if not isinstance(route, dict):
            raise EvaluationError(f"{field} must be an object")
        require_exact_keys(route, field, {"name", "adapter_id", "adapter_executable_sha256", "argv"})
        name = require_text(route.get("name"), f"{field}.name")
        argv = route.get("argv")
        if name in actual or not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise EvaluationError(f"{field} has invalid argv or duplicate name")
        executable = Path(argv[0]).resolve()
        expected_executor = expected.get(name)
        if (
            expected_executor is None
            or route.get("adapter_id") != expected_executor["adapter_id"]
            or route.get("adapter_executable_sha256") != expected_executor["adapter_executable_sha256"]
            or sha256_file(executable) != expected_executor["adapter_executable_sha256"]
        ):
            raise EvaluationError(f"{field} is not an authorized exact executor route")
        actual[name] = route
    if list(actual) != [item["name"] for item in executors]:
        raise EvaluationError("routing executor set or order differs from the selected executor set")
    comparator_route = routing.get("comparator")
    if not isinstance(comparator_route, dict):
        raise EvaluationError("routing.comparator must be an object")
    require_exact_keys(
        comparator_route,
        "routing.comparator",
        {"route", "adapter_id", "adapter_executable_sha256", "argv"},
    )
    argv = comparator_route.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise EvaluationError("routing.comparator.argv must be a non-empty text list")
    if (
        any(comparator_route.get(key) != comparator[key] for key in ("route", "adapter_id", "adapter_executable_sha256"))
        or sha256_file(Path(argv[0]).resolve()) != comparator["adapter_executable_sha256"]
    ):
        raise EvaluationError("routing.comparator is not the authorized exact comparator route")
    return routing


def copy_sealed_tree(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    if not source.is_dir() or source.is_symlink():
        raise EvaluationError(f"{source} must be a real directory")
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_symlink():
            raise EvaluationError(f"{path}: symlinks are forbidden in sealed inputs")
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
            os.chmod(target, path.stat().st_mode & 0o777)
        else:
            raise EvaluationError(f"{path}: sealed input must be a regular file")


def v2_run_compile(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    run_dir = Path(args.run_dir).resolve()
    if not run_dir.is_dir() or run_dir.is_symlink() or any(run_dir.iterdir()):
        raise EvaluationError("run directory must exist, be real, and be empty")
    harness = require_trusted_harness(Path(args.harness))
    harness_sha = sha256_file(harness)
    candidate, files, suite, suite_id, policy, policy_id = load_v2_inputs(
        skill_dir, args.suite, args.policy
    )
    required_names, advisory_names = desired_executor_roles()
    if required_names != [item["name"] for item in policy["required_executors"]]:
        raise EvaluationError("policy required executors differ from DREAMING_EVALUATION_EXECUTORS")
    if advisory_names != [item["name"] for item in policy["advisory_executors"]]:
        raise EvaluationError(
            "policy advisory executors differ from DREAMING_ADVISORY_EVALUATION_EXECUTORS"
        )
    config_path = Path(args.config).resolve()
    config, harness_suite = validate_compilation_config(config_path, suite, policy, harness_sha)
    validate_routing(Path(args.routing).resolve(), config["executors"], config["comparator"])
    inventory(skill_dir, run_dir / "candidate")
    copy_sealed_tree(config_path.parent / "fixtures", run_dir / "fixtures")
    copy_sealed_tree(config_path.parent / "graders", run_dir / "graders")
    atomic_write(run_dir / "source-suite.json", suite)
    atomic_write(run_dir / "source-policy.json", policy)
    atomic_write(run_dir / "compilation.json", config)
    atomic_write(
        run_dir / "dreaming-input.json",
        {
            "schema_version": 1,
            "candidate_id": candidate,
            "candidate_inventory": files,
            "suite_id": suite_id,
            "policy_id": policy_id,
            "observation_plan_id": observation_plan_identity(policy, policy_id),
        },
    )
    atomic_write(run_dir / "suite.json", harness_suite)
    file_inventory = canonical_file_inventory(run_dir)
    harness_projection = [
        {
            "path": item["path"].removeprefix("candidate/"),
            "sha256": item["sha256"],
            "size": item["size"],
        }
        for item in file_inventory
        if item["path"].startswith("candidate/")
    ]
    manifest = {
        "schema_version": HARNESS_CONTRACT_VERSION,
        "kind": "skill_evaluation_run",
        "invocation_nonce": require_text(args.nonce, "invocation nonce"),
        "candidate_id": f"sha256:{digest(canonical(harness_projection))}",
        "suite_id": f"sha256:{digest(canonical(harness_suite))}",
        "profile": policy["profile"],
        "trials_per_arm": policy["trials_per_arm"],
        "executors": config["executors"],
        "comparator": config["comparator"],
        "harness_executable_sha256": harness_sha,
        "tool_policy_id": config["tool_policy_id"],
        "grader_set_id": harness_suite["grader_set_id"],
        "retention_policy_id": config["retention_policy_id"],
        "limits": config["limits"],
        "file_inventory": file_inventory,
    }
    manifest["run_id"] = f"sha256:{digest(canonical({
        key: manifest[key]
        for key in (
            "schema_version", "kind", "candidate_id", "suite_id", "profile",
            "trials_per_arm", "executors", "comparator",
            "harness_executable_sha256", "tool_policy_id", "grader_set_id",
            "retention_policy_id", "limits", "file_inventory",
        )
    }))}"
    atomic_write(run_dir / "manifest.json", manifest)
    return {
        "run_dir": str(run_dir),
        "run_id": manifest["run_id"],
        "candidate_id": candidate,
        "candidate_inventory": files,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "observation_plan_id": observation_plan_identity(policy, policy_id),
        "required_executors": required_names,
        "advisory_executors": advisory_names,
    }


def load_compiled_run(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    manifest = load_json(run_dir / "manifest.json")
    source_suite = load_json(run_dir / "source-suite.json")
    source_policy = load_json(run_dir / "source-policy.json")
    compilation = load_json(run_dir / "compilation.json")
    actual_inventory = canonical_file_inventory(run_dir, {"manifest.json"})
    if manifest.get("file_inventory") != actual_inventory:
        raise EvaluationError("compiled run file inventory was modified")
    expected_run_id = f"sha256:{digest(canonical({
        key: manifest.get(key)
        for key in (
            "schema_version", "kind", "candidate_id", "suite_id", "profile",
            "trials_per_arm", "executors", "comparator",
            "harness_executable_sha256", "tool_policy_id", "grader_set_id",
            "retention_policy_id", "limits", "file_inventory",
        )
    }))}"
    if manifest.get("run_id") != expected_run_id:
        raise EvaluationError("compiled run_id is not canonical")
    return manifest, source_suite, source_policy, compilation


def v2_run_execute(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir).resolve()
    result_dir = Path(args.result_dir).resolve()
    scratch_dir = Path(args.scratch).resolve()
    harness = require_trusted_harness(Path(args.harness))
    manifest, _, _, compilation = load_compiled_run(run_dir)
    if sha256_file(harness) != manifest.get("harness_executable_sha256"):
        raise EvaluationError("selected harness executable differs from the compiled run")
    validate_routing(Path(args.routing).resolve(), compilation["executors"], compilation["comparator"])
    if not result_dir.is_dir() or result_dir.is_symlink() or any(result_dir.iterdir()):
        raise EvaluationError("result directory must exist, be real, and be empty")
    if not scratch_dir.is_dir() or scratch_dir.is_symlink() or any(scratch_dir.iterdir()):
        raise EvaluationError("scratch directory must exist, be real, and be empty")
    subprocess.run(
        [
            str(harness),
            "run",
            "--input",
            str(run_dir),
            "--output",
            str(result_dir),
            "--routing",
            str(Path(args.routing).resolve()),
            "--scratch",
            str(scratch_dir),
        ],
        check=True,
    )
    return {"result_dir": str(result_dir), "run_id": manifest["run_id"]}


def result_bundle_identity(result_dir: Path, manifest: dict[str, Any]) -> tuple[str, str]:
    inventory_value = manifest.get("file_inventory")
    if inventory_value != canonical_file_inventory(result_dir, {"manifest.json"}):
        raise EvaluationError("result file inventory differs from the sealed manifest")
    result_id = require_sha256(manifest.get("result_id"), "result.result_id")
    bundle_sha = f"sha256:{digest(canonical({'manifest': manifest, 'files': inventory_value}))}"
    return bundle_sha, result_id


def verify_native_raw(path: Path, field: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise EvaluationError(f"{field} is missing")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise EvaluationError(f"{field} must contain native events")
    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"{field}[{index}] is not valid JSON") from exc
        if not isinstance(event, dict) or not event:
            raise EvaluationError(f"{field}[{index}] must be a non-empty native event object")


def verify_result_independently(
    skill_dir: Path,
    run_dir: Path,
    result_dir: Path,
    routing_path: Path,
    harness: Path,
    nonce: str,
    scratch: Path,
    suite_path: str | None,
    policy_path: str | None,
    allow_advisory_drift: bool = False,
) -> dict[str, Any]:
    candidate, files, suite, suite_id, policy, policy_id = load_v2_inputs(
        skill_dir, suite_path, policy_path
    )
    required_names, advisory_names = desired_executor_roles()
    if required_names != [item["name"] for item in policy["required_executors"]]:
        raise EvaluationError("current policy differs from DREAMING_EVALUATION_EXECUTORS")
    if (
        not allow_advisory_drift
        and advisory_names != [item["name"] for item in policy["advisory_executors"]]
    ):
        raise EvaluationError(
            "current policy differs from DREAMING_ADVISORY_EVALUATION_EXECUTORS"
        )
    run_manifest, source_suite, source_policy, compilation = load_compiled_run(run_dir)
    dreaming_input = load_json(run_dir / "dreaming-input.json")
    require_exact_keys(
        dreaming_input,
        "dreaming input",
        {
            "schema_version",
            "candidate_id",
            "candidate_inventory",
            "suite_id",
            "policy_id",
            "observation_plan_id",
        },
    )
    if source_suite != suite:
        raise EvaluationError("compiled run binds stale suite input")
    if policy_identity(source_policy) != policy_id:
        raise EvaluationError("compiled run binds stale required policy input")
    if not allow_advisory_drift and source_policy != policy:
        raise EvaluationError("compiled run binds stale advisory policy input")
    source_observation_plan_id = observation_plan_identity(source_policy, policy_id)
    expected_policy_executors = [
        {**item, "requirement": requirement}
        for requirement, values in (
            ("required", source_policy["required_executors"]),
            ("advisory", source_policy["advisory_executors"]),
        )
        for item in values
    ]
    compiled_policy_executors = [
        {
            key: item[key]
            for key in (
                "name",
                "model",
                "adapter_id",
                "adapter_version",
                "adapter_executable_sha256",
                "cli_executable_sha256",
                "requirement",
            )
        }
        for item in compilation.get("executors", [])
    ]
    if compiled_policy_executors != expected_policy_executors:
        raise EvaluationError("compiled executor identities differ from the current policy")
    if compilation.get("comparator") != policy["comparator"]:
        raise EvaluationError("compiled comparator identity differs from the current policy")
    if (
        run_manifest.get("executors") != compilation.get("executors")
        or run_manifest.get("comparator") != compilation.get("comparator")
        or run_manifest.get("tool_policy_id") != compilation.get("tool_policy_id")
        or run_manifest.get("retention_policy_id") != compilation.get("retention_policy_id")
        or run_manifest.get("limits") != compilation.get("limits")
    ):
        raise EvaluationError("compiled run manifest differs from its reviewed compilation")
    if any(
        item.get("tool_policy_id") != compilation.get("tool_policy_id")
        for item in compilation.get("executors", [])
    ):
        raise EvaluationError("compiled executor tool policy differs from the reviewed compilation")
    if dreaming_input != {
        "schema_version": 1,
        "candidate_id": candidate,
        "candidate_inventory": files,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "observation_plan_id": source_observation_plan_id,
    }:
        raise EvaluationError("compiled run binds a stale candidate")
    run_projection = [
        {
            "path": item["path"].removeprefix("candidate/"),
            "sha256": item["sha256"],
            "size": item["size"],
        }
        for item in run_manifest["file_inventory"]
        if item["path"].startswith("candidate/")
    ]
    if run_manifest.get("candidate_id") != f"sha256:{digest(canonical(run_projection))}":
        raise EvaluationError("compiled harness candidate identity is stale")
    harness = require_trusted_harness(harness)
    harness_sha = sha256_file(harness)
    if (
        harness_sha != run_manifest.get("harness_executable_sha256")
        or harness_sha != compilation.get("harness_executable_sha256")
    ):
        raise EvaluationError("unknown or changed harness producer")
    routing = validate_routing(routing_path, compilation["executors"], compilation["comparator"])
    if not scratch.is_dir() or scratch.is_symlink() or any(scratch.iterdir()):
        raise EvaluationError("verification scratch directory must exist, be real, and be empty")
    try:
        subprocess.run(
            [
                str(harness),
                "verify",
                "--result",
                str(result_dir),
                "--scratch",
                str(scratch),
                "--nonce",
                nonce,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = " ".join((exc.stderr or "").splitlines()).strip()
        suffix = f": {detail}" if detail else ""
        raise EvaluationError(
            f"independent result verification failed with exit status {exc.returncode}{suffix}"
        ) from exc
    result_manifest = load_json(result_dir / "manifest.json")
    require_exact_keys(result_manifest, "result manifest", RESULT_MANIFEST_KEYS)
    if (
        result_manifest.get("schema_version") != HARNESS_CONTRACT_VERSION
        or result_manifest.get("kind") != "skill_evaluation_result"
        or result_manifest.get("invocation_nonce") != nonce
        or result_manifest.get("input_run_id") != run_manifest["run_id"]
        or result_manifest.get("candidate_id") != run_manifest["candidate_id"]
        or result_manifest.get("profile") != source_policy["profile"]
        or result_manifest.get("harness_executable_sha256") != harness_sha
    ):
        raise EvaluationError("result manifest differs from the current sealed run")
    expected_result_id = f"sha256:{digest(canonical({
        key: value for key, value in result_manifest.items() if key != 'result_id'
    }))}"
    if result_manifest.get("result_id") != expected_result_id:
        raise EvaluationError("result identity is forged or malformed")
    bundle_sha, result_id = result_bundle_identity(result_dir, result_manifest)
    expected_executor_identities = {
        item["name"]: {
            key: value for key, value in item.items() if key not in {"name", "requirement"}
        }
        for item in compilation["executors"]
    }
    if result_manifest.get("executor_identities") != expected_executor_identities:
        raise EvaluationError("result executor identities differ from the required exact set")
    if result_manifest.get("comparator_identity") != compilation["comparator"]:
        raise EvaluationError("result comparator identity differs from the authorized comparator")
    producer_audit = result_manifest.get("producer_audit")
    if not isinstance(producer_audit, dict):
        raise EvaluationError("result producer audit is missing")
    require_exact_keys(
        producer_audit,
        "result.producer_audit",
        {"routing_config_sha256", "executor_argv_sha256", "comparator_argv_sha256", "environment_sha256"},
    )
    if producer_audit["routing_config_sha256"] != f"sha256:{digest(routing_path.read_bytes())}":
        raise EvaluationError("result producer routing digest differs from the authorized route")
    expected_executor_argv = {
        item["name"]: f"sha256:{digest(canonical(item['argv']))}"
        for item in routing["executors"]
    }
    if producer_audit["executor_argv_sha256"] != expected_executor_argv:
        raise EvaluationError("result executor argv identities differ from authorized routing")
    if producer_audit["comparator_argv_sha256"] != f"sha256:{digest(canonical(routing['comparator']['argv']))}":
        raise EvaluationError("result comparator argv identity differs from authorized routing")
    records: list[dict[str, Any]] = []
    for trial_id in result_manifest.get("trials", []):
        require_sha256(trial_id, "result.trials[]")
        root = result_dir / "trials" / trial_id.removeprefix("sha256:")
        record = load_json(root / "result.json")
        if record.get("trial_id") != trial_id:
            raise EvaluationError(f"trial {trial_id} has a forged result identity")
        executor = next(
            (item for item in compilation["executors"] if item["name"] == record.get("executor")),
            None,
        )
        if executor is None or record.get("model") != executor["model"]:
            raise EvaluationError(f"trial {trial_id} uses an unauthorized executor or model")
        trial = load_json(root / "trial.json")
        if (
            trial.get("trial_id") != trial_id
            or trial.get("candidate_id") != run_manifest["candidate_id"]
            or trial.get("executor") != executor
        ):
            raise EvaluationError(f"trial {trial_id} differs from its sealed input identity")
        if record.get("status") != "inconclusive":
            prepared = load_json(root / "prepared.json")
            prepared_digest = prepared.get("prepared_digest")
            if prepared_digest != f"sha256:{digest(canonical({
                key: value for key, value in prepared.items() if key != 'prepared_digest'
            }))}":
                raise EvaluationError(f"trial {trial_id} prepared execution digest is invalid")
            if record.get("prepared_digest") != prepared_digest:
                raise EvaluationError(f"trial {trial_id} did not bind the prepared execution")
            effective = {
                key: value
                for key, value in executor.items()
                if key not in {"name", "requirement"}
            }
            if record.get("effective_execution") != effective or prepared.get("execution") != effective:
                raise EvaluationError(f"trial {trial_id} prepared or effective execution drifted")
            raw = root / "raw.jsonl"
            trace = root / "trace.json"
            verify_native_raw(raw, f"trial {trial_id} raw log")
            if (
                record.get("raw_sha256") != f"sha256:{digest(raw.read_bytes())}"
                or record.get("trace_sha256") != f"sha256:{digest(trace.read_bytes())}"
            ):
                raise EvaluationError(f"trial {trial_id} raw or trace link is stale")
            artifact_inventory = canonical_file_inventory(root / "artifacts")
            if record.get("artifact_inventory") != artifact_inventory:
                raise EvaluationError(f"trial {trial_id} artifact inventory is stale")
            if not (root / "grader-results.json").is_file():
                raise EvaluationError(f"trial {trial_id} is missing deterministic grader evidence")
        records.append(record)
    if set(result_manifest.get("trials", [])) != {item.get("trial_id") for item in records}:
        raise EvaluationError("result trial matrix is partial or duplicated")
    comparisons = []
    for pair_id in result_manifest.get("pairs", []):
        require_sha256(pair_id, "result.pairs[]")
        comparison = load_json(
            result_dir / "comparisons" / f"{pair_id.removeprefix('sha256:')}.json"
        )
        if comparison.get("pair_id") != pair_id:
            raise EvaluationError(f"comparison {pair_id} has a forged identity")
        if comparison.get("comparator") != compilation["comparator"]:
            raise EvaluationError(f"comparison {pair_id} uses an unauthorized comparator")
        comparisons.append(comparison)
    if set(result_manifest.get("pairs", [])) != {item.get("pair_id") for item in comparisons}:
        raise EvaluationError("result comparison matrix is partial or duplicated")
    return {
        "candidate_id": candidate,
        "candidate_inventory": files,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "observation_plan_id": source_observation_plan_id,
        "policy": source_policy,
        "run_id": run_manifest["run_id"],
        "result_bundle_sha256": bundle_sha,
        "result_bundle_id": result_id,
        "state": result_manifest.get("state"),
        "collection_state": result_manifest.get("collection_state"),
        "executor_states": result_manifest.get("executor_states"),
        "records": records,
        "comparisons": comparisons,
    }


def reverify_certification_record(
    certification: dict[str, Any], skill_dir: Path
) -> dict[str, Any]:
    scratch_parent = v2_evaluation_dir() / "verification-scratch"
    scratch_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="authority-", dir=scratch_parent) as temporary:
        scratch = Path(temporary)
        return verify_result_independently(
            skill_dir,
            Path(require_text(certification.get("run_dir"), "certification.run_dir")).resolve(),
            Path(require_text(certification.get("result_dir"), "certification.result_dir")).resolve(),
            Path(require_text(certification.get("routing_path"), "certification.routing_path")).resolve(),
            require_trusted_harness(
                Path(require_text(certification.get("harness_path"), "certification.harness_path"))
            ),
            require_text(certification.get("invocation_nonce"), "certification.invocation_nonce"),
            scratch,
            None,
            None,
            True,
        )


def candidate_comparison_wins(comparisons: list[dict[str, Any]]) -> int:
    wins = 0
    for comparison in comparisons:
        winner = comparison.get("winner")
        assignment = comparison.get("assignment")
        if (
            comparison.get("status") == "complete"
            and winner in {"A", "B"}
            and isinstance(assignment, dict)
            and assignment.get(winner) == "candidate"
        ):
            wins += 1
    return wins


def executor_policy_status(
    executor_name: str,
    policy: dict[str, Any],
    state: str,
    records: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
) -> str:
    if policy["profile"] != "gate":
        return "inconclusive"
    selected = [item for item in records if item.get("executor") == executor_name]
    if state != "complete" or not selected:
        return "inconclusive"
    if any(item.get("status") == "inconclusive" or item.get("infrastructure_error") or item.get("cleanup_failed") for item in selected):
        return "inconclusive"
    by_case: dict[str, list[dict[str, Any]]] = {}
    for item in selected:
        by_case.setdefault(require_text(item.get("case_id"), "trial.case_id"), []).append(item)
    for case_records in by_case.values():
        case_class = case_records[0].get("case_class")
        candidate_records = [item for item in case_records if item.get("treatment") == "candidate"]
        control_records = [item for item in case_records if item.get("treatment") == "control"]
        if len(candidate_records) != 3 or (case_class in {"intended", "related"} and len(control_records) != 3):
            return "inconclusive"
        if case_class == "intended":
            if any(item.get("status") == "invalid" for item in case_records):
                return "inconclusive"
            candidate_passes = sum(item.get("status") == "pass" for item in candidate_records)
            control_passes = sum(item.get("status") == "pass" for item in control_records)
            if candidate_passes < 2:
                return "regression"
            if policy["policy_kind"] == "capability_uplift":
                if candidate_passes - control_passes < 1 or control_passes == 3:
                    return "regression"
            else:
                case_comparisons = [
                    item
                    for item in comparisons
                    if item.get("executor") == executor_name
                    and item.get("case_id") == case_records[0].get("case_id")
                ]
                if len(case_comparisons) != 3 or any(item.get("status") != "complete" for item in case_comparisons):
                    return "inconclusive"
                if candidate_comparison_wins(case_comparisons) < 2:
                    return "regression"
        elif case_class == "related":
            if any(item.get("status") == "invalid" for item in case_records):
                return "inconclusive"
            if (
                any(item.get("status") == "regression" for item in candidate_records)
                or any(item.get("deterministic_pass") is not True for item in candidate_records)
                or sum(item.get("status") == "pass" for item in candidate_records)
                < sum(item.get("status") == "pass" for item in control_records)
            ):
                return "regression"
        elif case_class == "activation_positive":
            if sum(item.get("skill_load_proved") is True for item in candidate_records) < 2:
                return "regression"
        elif case_class == "activation_negative":
            if any(item.get("skill_load_proved") is not True or item.get("status") == "regression" for item in candidate_records):
                return "regression"
        else:
            return "inconclusive"
    return "pass"


def make_executor_certificate(
    status: str,
    candidate: str,
    suite_id: str,
    policy_id: str,
    observation_plan_id: str,
    profile: str,
    requirement: str,
    executor: dict[str, Any],
    result_bundle_sha256: str,
    result_bundle_id: str,
    run_id: str,
) -> dict[str, Any]:
    certificate = {
        "schema_version": 2,
        "kind": "executor_certificate",
        "status": status,
        "candidate_id": candidate,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "observation_plan_id": observation_plan_id if requirement == "advisory" else None,
        "profile": profile,
        "requirement": requirement,
        "executor": executor,
        "result_bundle_sha256": result_bundle_sha256,
        "result_bundle_id": result_bundle_id,
        "run_id": run_id,
    }
    certificate["certificate_id"] = identity_with("certificate_id", certificate)
    return certificate


def write_certification_aggregate(
    skill_dir: Path,
    candidate: str,
    files: list[dict[str, Any]],
    suite: dict[str, Any],
    suite_id: str,
    policy_id: str,
    policy: dict[str, Any],
    certificates: list[dict[str, Any]],
) -> tuple[dict[str, Any], Path, str]:
    required_certificates = [
        item for item in certificates if item["requirement"] == "required"
    ]
    statuses = [item["status"] for item in required_certificates]
    aggregate_status = (
        "regression"
        if "regression" in statuses
        else "inconclusive"
        if any(item != "pass" for item in statuses)
        else "pass"
    )
    aggregate = {
        "schema_version": 2,
        "kind": "aggregate_receipt",
        "status": aggregate_status,
        "skill_path": str(skill_dir),
        "candidate_id": candidate,
        "candidate_inventory": files,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "observation_plan_id": observation_plan_identity(policy, policy_id),
        "profile": policy["profile"],
        "required_executors": policy["required_executors"],
        "advisory_executors": policy["advisory_executors"],
        "certificates": certificates,
        "required_certificate_set_id": required_certificate_set_identity(
            candidate, suite_id, policy_id, policy["profile"], required_certificates
        ),
    }
    aggregate["aggregate_id"] = identity_with("aggregate_id", aggregate)
    validated = validate_aggregate(
        aggregate,
        skill_dir,
        candidate,
        suite,
        suite_id,
        policy,
        policy_id,
    )
    path, receipt_sha = write_v2_receipt(validated)
    return validated, path, receipt_sha


def write_portfolio_receipt(
    skill_dir: Path,
    verified: dict[str, Any],
    suite: dict[str, Any],
    aggregate: dict[str, Any],
    aggregate_sha: str,
) -> tuple[dict[str, Any], str]:
    cases: list[dict[str, Any]] = []
    intended = {
        item["id"]
        for item in suite["cases"]
        if item.get("class") == "intended"
    }
    executor_names = [
        item["name"]
        for item in (
            verified["policy"]["required_executors"]
            + verified["policy"]["advisory_executors"]
        )
    ]
    for executor in executor_names:
        for case_id in sorted(intended):
            rows = [
                item
                for item in verified["records"]
                if item.get("executor") == executor
                and item.get("case_id") == case_id
            ]
            candidate = [
                item for item in rows if item.get("treatment") == "candidate"
            ]
            control = [item for item in rows if item.get("treatment") == "control"]
            invalid = any(
                item.get("status") in {"invalid", "inconclusive"}
                or item.get("infrastructure_error")
                or item.get("cleanup_failed")
                for item in rows
            )
            comparable = len(candidate) == 3 and len(control) == 3 and not invalid
            cases.append(
                {
                    "executor": executor,
                    "case_id": case_id,
                    "evaluation_class": verified["policy"]["policy_kind"],
                    "candidate_valid_trials": sum(
                        item.get("status") in {"pass", "regression"}
                        for item in candidate
                    ),
                    "candidate_successful_trials": sum(
                        item.get("status") == "pass" for item in candidate
                    ),
                    "control_valid_trials": sum(
                        item.get("status") in {"pass", "regression"}
                        for item in control
                    ),
                    "control_successful_trials": sum(
                        item.get("status") == "pass" for item in control
                    ),
                    "comparable": comparable,
                    "exclusion_reason": None if comparable else "incomplete_pair",
                }
            )
    receipt = {
        "schema_version": 1,
        "kind": "dashboard_portfolio_receipt",
        "skill_key": latest_key(str(skill_dir)),
        "candidate_id": verified["candidate_id"],
        "suite_id": verified["suite_id"],
        "policy_id": verified["policy_id"],
        "status": aggregate["status"],
        "aggregate_receipt_sha256": aggregate_sha,
        "aggregate_id": aggregate["aggregate_id"],
        "cases": cases,
    }
    receipt["portfolio_id"] = identity_with("portfolio_id", receipt)
    receipt_sha = digest(canonical(receipt))
    path = v2_portfolio_receipt_path(receipt_sha)
    if path.exists() and canonical(load_json(path)) != canonical(receipt):
        raise EvaluationError("dashboard portfolio receipt collision")
    if not path.exists():
        atomic_write(path, receipt)
    pointer = {
        "schema_version": 1,
        "aggregate_receipt_sha256": aggregate_sha,
        "portfolio_receipt_sha256": receipt_sha,
        "portfolio_id": receipt["portfolio_id"],
    }
    pointer_path = v2_portfolio_pointer_path(aggregate_sha)
    if pointer_path.exists() and load_json(pointer_path) != pointer:
        raise EvaluationError("dashboard portfolio pointer collision")
    if not pointer_path.exists():
        atomic_write(pointer_path, pointer)
    return receipt, receipt_sha


def load_portfolio_for_aggregate(aggregate_sha: str) -> tuple[dict[str, Any], str]:
    pointer = load_json(v2_portfolio_pointer_path(aggregate_sha))
    if pointer.get("aggregate_receipt_sha256") != aggregate_sha:
        raise EvaluationError("portfolio pointer aggregate does not match")
    receipt_sha = require_text(
        pointer.get("portfolio_receipt_sha256"),
        "portfolio pointer receipt digest",
    )
    receipt = load_json(v2_portfolio_receipt_path(receipt_sha))
    if digest(canonical(receipt)) != receipt_sha:
        raise EvaluationError("portfolio receipt digest does not match")
    if (
        receipt.get("aggregate_receipt_sha256") != aggregate_sha
        or receipt.get("portfolio_id") != identity_with("portfolio_id", receipt)
        or pointer.get("portfolio_id") != receipt.get("portfolio_id")
    ):
        raise EvaluationError("portfolio receipt identity does not match")
    return receipt, receipt_sha


def write_authority_transition(
    skill_dir: Path,
    candidate_id: str,
    status: str,
    authority_sha: str | None,
    aggregate_sha: str | None,
    portfolio_sha: str | None,
) -> dict[str, Any]:
    if status not in {"pass", "regression", "inconclusive", "revoked"}:
        raise EvaluationError("authority transition status is invalid")
    if status == "pass" and not all((authority_sha, aggregate_sha, portfolio_sha)):
        raise EvaluationError("pass transition requires complete authority evidence")
    if status in {"regression", "inconclusive"} and (
        authority_sha is not None or not aggregate_sha or not portfolio_sha
    ):
        raise EvaluationError("non-passing transition evidence is invalid")
    if status == "revoked" and authority_sha is not None:
        raise EvaluationError("revoked transition cannot retain authority")
    transition = {
        "schema_version": 1,
        "kind": "dashboard_authority_transition",
        "effective_at": now_iso(),
        "skill_key": latest_key(str(skill_dir)),
        "candidate_id": candidate_id,
        "status": status,
        "authority_sha256": authority_sha,
        "aggregate_receipt_sha256": aggregate_sha,
        "portfolio_receipt_sha256": portfolio_sha,
    }
    transition["transition_id"] = identity_with("transition_id", transition)
    path = v2_transition_dir(skill_dir) / f"{transition['transition_id'].removeprefix('sha256:')}.json"
    if path.exists() and canonical(load_json(path)) != canonical(transition):
        raise EvaluationError("authority transition collision")
    if not path.exists():
        atomic_write(path, transition)
    return transition


def v2_result_certify(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    verified = verify_result_independently(
        skill_dir,
        Path(args.run_dir).resolve(),
        Path(args.result_dir).resolve(),
        Path(args.routing).resolve(),
        Path(args.harness).resolve(),
        require_text(args.nonce, "invocation nonce"),
        Path(args.scratch).resolve(),
        args.suite,
        args.policy,
    )
    policy = verified["policy"]
    certificates = []
    for requirement, executors in (
        ("required", policy["required_executors"]),
        ("advisory", policy["advisory_executors"]),
    ):
        for executor in executors:
            executor_state = verified["executor_states"].get(executor["name"], {}).get(
                "state", "incomplete"
            )
            certificates.append(
                make_executor_certificate(
                    executor_policy_status(
                        executor["name"],
                        policy,
                        executor_state,
                        verified["records"],
                        verified["comparisons"],
                    ),
                    verified["candidate_id"],
                    verified["suite_id"],
                    verified["policy_id"],
                    verified["observation_plan_id"],
                    policy["profile"],
                    requirement,
                    executor,
                    verified["result_bundle_sha256"],
                    verified["result_bundle_id"],
                    verified["run_id"],
                )
            )
    suite = load_suite(
        Path(args.suite).resolve() if args.suite else skill_dir / CASE_FILE
    )[0]
    aggregate, path, receipt_sha = write_certification_aggregate(
        skill_dir,
        verified["candidate_id"],
        verified["candidate_inventory"],
        suite,
        verified["suite_id"],
        verified["policy_id"],
        policy,
        certificates,
    )
    _, portfolio_sha = write_portfolio_receipt(
        skill_dir,
        verified,
        suite,
        aggregate,
        receipt_sha,
    )
    if policy["profile"] == "gate" and aggregate["status"] in {
        "regression",
        "inconclusive",
    }:
        write_authority_transition(
            skill_dir,
            verified["candidate_id"],
            aggregate["status"],
            None,
            receipt_sha,
            portfolio_sha,
        )
    if policy["profile"] == "gate" and aggregate["status"] == "pass":
        certification = {
            "schema_version": 1,
            "kind": "dreaming_certification",
            "skill_path": str(skill_dir),
            "candidate_id": verified["candidate_id"],
            "suite_id": verified["suite_id"],
            "policy_id": verified["policy_id"],
            "required_certificate_set_id": aggregate["required_certificate_set_id"],
            "profile": policy["profile"],
            "aggregate_receipt_sha256": receipt_sha,
            "aggregate_id": aggregate["aggregate_id"],
            "result_bundle_sha256": verified["result_bundle_sha256"],
            "result_bundle_id": verified["result_bundle_id"],
            "run_id": verified["run_id"],
            "run_dir": str(Path(args.run_dir).resolve()),
            "result_dir": str(Path(args.result_dir).resolve()),
            "routing_path": str(Path(args.routing).resolve()),
            "harness_path": str(Path(args.harness).resolve()),
            "invocation_nonce": require_text(args.nonce, "invocation nonce"),
        }
        certification["certification_id"] = identity_with("certification_id", certification)
        atomic_write(v2_certification_path(receipt_sha), certification)
    return {
        "status": aggregate["status"],
        "aggregate": str(path),
        "aggregate_receipt_sha256": receipt_sha,
        "aggregate_id": aggregate["aggregate_id"],
        "certificates": certificates,
        "authoritative": policy["profile"] == "gate" and aggregate["status"] == "pass",
    }


def parse_unavailable(value: str) -> tuple[str, str]:
    name, separator, reason = value.partition("=")
    if not separator or name not in EXECUTOR_NAMES:
        raise EvaluationError("--unavailable must be executor=reason")
    return name, require_text(reason, f"unavailable reason for {name}")


def v2_unavailable_aggregate(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    candidate, files, suite, suite_id, policy, policy_id = load_v2_inputs(
        skill_dir, args.suite, args.policy
    )
    required_names, advisory_names = desired_executor_roles()
    if required_names != [item["name"] for item in policy["required_executors"]]:
        raise EvaluationError("policy required executors differ from DREAMING_EVALUATION_EXECUTORS")
    if advisory_names != [item["name"] for item in policy["advisory_executors"]]:
        raise EvaluationError(
            "policy advisory executors differ from DREAMING_ADVISORY_EVALUATION_EXECUTORS"
        )
    reasons = dict(parse_unavailable(item) for item in args.unavailable)
    selected = required_names + advisory_names
    if set(reasons) != set(selected):
        raise EvaluationError("unavailability evidence must name every selected executor exactly once")
    certificates = []
    observation_plan_id = observation_plan_identity(policy, policy_id)
    for requirement, executors in (
        ("required", policy["required_executors"]),
        ("advisory", policy["advisory_executors"]),
    ):
        for executor in executors:
            evidence = {
                "schema_version": 1,
                "kind": "skill_evaluation_unavailable",
                "candidate_id": candidate,
                "suite_id": suite_id,
                "policy_id": policy_id,
                "observation_plan_id": (
                    observation_plan_id if requirement == "advisory" else None
                ),
                "profile": policy["profile"],
                "requirement": requirement,
                "executor": executor,
                "reason": reasons[executor["name"]],
            }
            evidence["result_id"] = identity_with("result_id", evidence)
            evidence_path, evidence_sha = write_v2_receipt(evidence)
            certificates.append(
                make_executor_certificate(
                    "unavailable",
                    candidate,
                    suite_id,
                    policy_id,
                    observation_plan_id,
                    policy["profile"],
                    requirement,
                    executor,
                    f"sha256:{evidence_sha}",
                    evidence["result_id"],
                    f"sha256:{digest(canonical({'unavailable': str(evidence_path)}))}",
                )
            )
    aggregate, path, receipt_sha = write_certification_aggregate(
        skill_dir, candidate, files, suite, suite_id, policy_id, policy, certificates
    )
    return {
        "status": aggregate["status"],
        "aggregate": str(path),
        "aggregate_receipt_sha256": receipt_sha,
        "authoritative": False,
    }


def load_v2_receipt(path: Path) -> tuple[dict[str, Any], str]:
    resolved = path.resolve()
    if resolved.parent != (v2_evaluation_dir() / "receipts").resolve():
        raise EvaluationError("v2 receipt must come from the version-2 receipt store")
    receipt = load_json(resolved)
    receipt_sha = digest(canonical(receipt))
    if resolved.name != f"{receipt_sha}.json":
        raise EvaluationError("v2 receipt hash or content-addressed path does not match")
    return receipt, receipt_sha


def write_v2_receipt(receipt: dict[str, Any]) -> tuple[Path, str]:
    receipt_sha = digest(canonical(receipt))
    path = v2_receipt_path(receipt_sha)
    if path.exists() and canonical(load_json(path)) != canonical(receipt):
        raise EvaluationError("version-2 receipt collision")
    if not path.exists():
        atomic_write(path, receipt)
    return path, receipt_sha


def set_v3_envelope_pointer(skill_dir: Path, authority_sha256: str) -> None:
    envelope = skill_dir / ".agent-created.json"
    if not envelope.exists():
        return
    helper = Path(__file__).with_name("evidence-envelope.py")
    subprocess.run(
        [str(helper), "set-evaluation-v3", str(envelope), authority_sha256],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def validate_authority(
    authority: Any, skill_dir: Path, candidate: str, suite: dict[str, Any], suite_id: str,
    policy: dict[str, Any], policy_id: str
) -> dict[str, Any]:
    if not isinstance(authority, dict):
        raise EvaluationError("authority document must be an object")
    require_exact_keys(
        authority,
        "authority",
        {
            "schema_version",
            "kind",
            "skill_path",
            "candidate_id",
            "suite_id",
            "policy_id",
            "observation_plan_id",
            "required_certificate_set_id",
            "required_executors",
            "advisory_executors",
            "aggregate_receipt_sha256",
            "aggregate_id",
            "authority_id",
        },
    )
    if authority.get("schema_version") != AUTHORITY_SCHEMA_VERSION or authority.get("kind") != "cross_cli_authority":
        raise EvaluationError("authority document must be schema-v3 cross_cli_authority")
    if authority.get("skill_path") != str(skill_dir):
        raise EvaluationError("authority document belongs to another skill path")
    if authority.get("candidate_id") != candidate or authority.get("suite_id") != suite_id or authority.get("policy_id") != policy_id:
        raise EvaluationError("authority document input identity is stale")
    aggregate_sha = require_text(authority.get("aggregate_receipt_sha256"), "authority.aggregate_receipt_sha256")
    aggregate, verified_sha = load_v2_receipt(v2_receipt_path(aggregate_sha))
    if verified_sha != aggregate_sha:
        raise EvaluationError("authority aggregate receipt digest does not match")
    aggregate = validate_aggregate(
        aggregate,
        skill_dir,
        candidate,
        suite,
        suite_id,
        policy,
        policy_id,
        allow_advisory_drift=True,
    )
    if aggregate["status"] != "pass":
        raise EvaluationError("cross-CLI authority requires a passing aggregate receipt")
    if policy["profile"] != "gate":
        raise EvaluationError("cross-CLI authority requires a gate-profile policy")
    certification = load_json(v2_certification_path(aggregate_sha))
    require_exact_keys(
        certification,
        "certification record",
        {
            "schema_version",
            "kind",
            "skill_path",
            "candidate_id",
            "suite_id",
            "policy_id",
            "required_certificate_set_id",
            "profile",
            "aggregate_receipt_sha256",
            "aggregate_id",
            "result_bundle_sha256",
            "result_bundle_id",
            "run_id",
            "run_dir",
            "result_dir",
            "routing_path",
            "harness_path",
            "invocation_nonce",
            "certification_id",
        },
    )
    if (
        certification.get("schema_version") != 1
        or certification.get("kind") != "dreaming_certification"
        or certification.get("skill_path") != str(skill_dir)
        or certification.get("candidate_id") != candidate
        or certification.get("suite_id") != suite_id
        or certification.get("policy_id") != policy_id
        or certification.get("required_certificate_set_id")
        != aggregate["required_certificate_set_id"]
        or certification.get("profile") != "gate"
        or certification.get("aggregate_receipt_sha256") != aggregate_sha
        or certification.get("aggregate_id") != aggregate["aggregate_id"]
        or certification.get("certification_id") != identity_with("certification_id", certification)
    ):
        raise EvaluationError("authority certification record is stale or malformed")
    certificates = aggregate["certificates"]
    for field in ("result_bundle_sha256", "result_bundle_id", "run_id"):
        values = {item[field] for item in certificates}
        if values != {certification[field]}:
            raise EvaluationError(f"authority certification record does not bind aggregate {field}")
    verified = reverify_certification_record(certification, skill_dir)
    for field in (
        "candidate_id",
        "suite_id",
        "policy_id",
        "run_id",
        "result_bundle_sha256",
        "result_bundle_id",
    ):
        if certification[field] != verified[field]:
            raise EvaluationError(f"authority certification evidence differs at {field}")
    if authority.get("aggregate_id") != aggregate["aggregate_id"]:
        raise EvaluationError("authority document aggregate identity does not match")
    if (
        authority.get("observation_plan_id") != aggregate["observation_plan_id"]
        or authority.get("required_certificate_set_id")
        != aggregate["required_certificate_set_id"]
        or authority.get("required_executors")
        != [item["name"] for item in aggregate["required_executors"]]
        or authority.get("advisory_executors")
        != [item["name"] for item in aggregate["advisory_executors"]]
    ):
        raise EvaluationError("authority document partition identities do not match the aggregate")
    expected_id = identity_with("authority_id", authority)
    if authority.get("authority_id") != expected_id:
        raise EvaluationError("authority.authority_id does not match authority content")
    return authority


def v2_authority_write(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    candidate, _, suite, suite_id, policy, policy_id = load_v2_inputs(skill_dir, args.suite, args.policy)
    aggregate, aggregate_sha = load_v2_receipt(Path(args.aggregate).resolve())
    aggregate = validate_aggregate(aggregate, skill_dir, candidate, suite, suite_id, policy, policy_id)
    if aggregate["status"] != "pass" or policy["profile"] != "gate":
        raise EvaluationError("only a passing aggregate receipt can issue cross-CLI authority")
    certification = load_json(v2_certification_path(aggregate_sha))
    if (
        certification.get("kind") != "dreaming_certification"
        or certification.get("aggregate_receipt_sha256") != aggregate_sha
        or certification.get("certification_id") != identity_with("certification_id", certification)
    ):
        raise EvaluationError("aggregate lacks a valid Dreaming certification record")
    reverify_certification_record(certification, skill_dir)
    aggregate_path = v2_receipt_path(aggregate_sha)
    authority = {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "kind": "cross_cli_authority",
        "skill_path": str(skill_dir),
        "candidate_id": candidate,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "observation_plan_id": aggregate["observation_plan_id"],
        "required_certificate_set_id": aggregate["required_certificate_set_id"],
        "required_executors": [item["name"] for item in aggregate["required_executors"]],
        "advisory_executors": [item["name"] for item in aggregate["advisory_executors"]],
        "aggregate_receipt_sha256": aggregate_sha,
        "aggregate_id": aggregate["aggregate_id"],
    }
    authority["authority_id"] = identity_with("authority_id", authority)
    authority_path = v2_authority_path(skill_dir, candidate)
    atomic_write(authority_path, authority)
    authority_sha = digest(canonical(authority))
    _, portfolio_sha = load_portfolio_for_aggregate(aggregate_sha)
    write_authority_transition(
        skill_dir,
        candidate,
        "pass",
        authority_sha,
        aggregate_sha,
        portfolio_sha,
    )
    atomic_write(
        v2_evaluation_dir() / "latest" / f"{latest_key(str(skill_dir))}.json",
        {
            "schema_version": 2,
            "skill_path": str(skill_dir),
            "candidate_id": candidate,
            "authority_path": str(authority_path),
            "authority_sha256": authority_sha,
        },
    )
    set_v3_envelope_pointer(skill_dir, authority_sha)
    return {
        "authority": str(authority_path),
        "authority_sha256": authority_sha,
        "aggregate_receipt": str(aggregate_path),
        "aggregate_receipt_sha256": aggregate_sha,
        "portfolio_receipt_sha256": portfolio_sha,
    }


def v2_authority_validate(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = resolve_path(Path(args.skill_dir), "skill directory")
    candidate, _, suite, suite_id, policy, policy_id = load_v2_inputs(skill_dir, args.suite, args.policy)
    expected_path = resolve_path(
        v2_authority_path(skill_dir, candidate), "expected authority path"
    )
    path = (
        resolve_path(Path(args.authority), "authority path")
        if args.authority
        else expected_path
    )
    authority = load_json(path)
    if path != expected_path:
        raise EvaluationError("authority document path does not match skill and candidate identity")
    validate_authority(authority, skill_dir, candidate, suite, suite_id, policy, policy_id)
    authority_sha = digest(canonical(authority))
    latest = load_json(v2_evaluation_dir() / "latest" / f"{latest_key(str(skill_dir))}.json")
    latest_authority_path = resolve_path(
        Path(require_text(latest.get("authority_path"), "latest authority path")),
        "latest authority path",
    )
    normalized_latest = {**latest, "authority_path": str(latest_authority_path)}
    if normalized_latest != {
        "schema_version": 2,
        "skill_path": str(skill_dir),
        "candidate_id": candidate,
        "authority_path": str(path),
        "authority_sha256": authority_sha,
    }:
        raise EvaluationError("version-2 latest authority pointer is stale or malformed")
    envelope_path = skill_dir / ".agent-created.json"
    if envelope_path.exists():
        pointer = load_json(envelope_path).get("evaluation_v3_sha256")
        if pointer != authority_sha:
            raise EvaluationError("evidence envelope v3 authority pointer is stale")
    return {"status": "pass", "candidate_id": candidate, "authority_sha256": authority_sha}


def changed_inventory_paths(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[str]:
    base = {item["path"]: item for item in before}
    current = {item["path"]: item for item in after}
    return sorted(path for path in base.keys() | current.keys() if base.get(path) != current.get(path))


def validate_v2_waiver(
    waiver: Any, skill_dir: Path, candidate: str, files: list[dict[str, Any]], suite: dict[str, Any],
    suite_id: str, policy: dict[str, Any], policy_id: str
) -> dict[str, Any]:
    if not isinstance(waiver, dict) or waiver.get("schema_version") != 2 or waiver.get("kind") != "cross_cli_waiver":
        raise EvaluationError("waiver must be a schema-v2 cross_cli_waiver")
    require_exact_keys(
        waiver,
        "waiver",
        {
            "schema_version",
            "kind",
            "status",
            "skill_path",
            "candidate_id",
            "base_candidate_id",
            "base_aggregate_receipt_sha256",
            "suite_id",
            "policy_id",
            "required_executors",
            "changed_paths",
            "waiver_reason",
            "test_command",
            "test_script",
            "test_script_sha256",
            "test_attestation",
            "test_result_sha256",
            "waiver_id",
        },
    )
    if waiver.get("status") != "waived" or waiver.get("skill_path") != str(skill_dir):
        raise EvaluationError("waiver status or skill path is invalid")
    if waiver.get("candidate_id") != candidate or waiver.get("suite_id") != suite_id or waiver.get("policy_id") != policy_id:
        raise EvaluationError("cross-CLI waiver input identity is stale")
    if waiver.get("required_executors") != policy["required_executors"]:
        raise EvaluationError("cross-CLI waiver required executor set is stale")
    base_sha = require_text(waiver.get("base_aggregate_receipt_sha256"), "waiver.base_aggregate_receipt_sha256")
    base, verified_base_sha = load_v2_receipt(v2_receipt_path(base_sha))
    if verified_base_sha != base_sha:
        raise EvaluationError("cross-CLI waiver base aggregate digest does not match")
    base_candidate = require_sha256(waiver.get("base_candidate_id"), "waiver.base_candidate_id")
    if base.get("candidate_id") != base_candidate:
        raise EvaluationError("cross-CLI waiver does not bind its base aggregate")
    validate_aggregate(
        base,
        skill_dir,
        base_candidate,
        suite,
        suite_id,
        policy,
        policy_id,
        allow_advisory_drift=True,
    )
    if base.get("status") != "pass":
        raise EvaluationError("cross-CLI waiver base aggregate must pass")
    changed = changed_inventory_paths(base.get("candidate_inventory", []), files)
    if not changed or waiver.get("changed_paths") != changed:
        raise EvaluationError("cross-CLI waiver changed paths are stale or malformed")
    if "SKILL.md" in changed or not all(path.startswith("scripts/") for path in changed):
        raise EvaluationError("cross-CLI waivers may change only restricted scripts/ paths")
    test_script = require_text(waiver.get("test_script"), "waiver.test_script")
    if waiver.get("test_command") != [test_script]:
        raise EvaluationError("waiver test command is stale or malformed")
    test_path = (skill_dir / test_script).resolve()
    try:
        test_relative = test_path.relative_to(skill_dir).as_posix()
    except ValueError as exc:
        raise EvaluationError("waiver test script must remain inside the skill") from exc
    if test_relative != test_script or not test_relative.startswith("scripts/") or test_relative in changed:
        raise EvaluationError("waiver test script must be unchanged under scripts/")
    current = {item["path"]: item for item in files}
    base_files = {item["path"]: item for item in base["candidate_inventory"]}
    if current.get(test_relative) != base_files.get(test_relative):
        raise EvaluationError("waiver test script identity changed")
    if waiver.get("test_script_sha256") != current[test_relative]["sha256"]:
        raise EvaluationError("waiver test script digest is stale")
    attestation = waiver.get("test_attestation")
    expected_files = {path: current[path]["sha256"] for path in changed}
    if attestation != {"status": "pass", "verified_files": expected_files}:
        raise EvaluationError("waiver test attestation does not bind every changed path")
    if waiver.get("test_result_sha256") != digest(canonical(attestation)):
        raise EvaluationError("waiver test result digest is stale")
    expected_id = identity_with("waiver_id", waiver)
    if waiver.get("waiver_id") != expected_id:
        raise EvaluationError("waiver.waiver_id does not match waiver content")
    return waiver


def v2_waive(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    candidate, files, suite, suite_id, policy, policy_id = load_v2_inputs(skill_dir, args.suite, args.policy)
    base, base_sha = load_v2_receipt(Path(args.base_aggregate).resolve())
    base_candidate = require_sha256(base.get("candidate_id"), "base aggregate candidate_id")
    validate_aggregate(
        base,
        skill_dir,
        base_candidate,
        suite,
        suite_id,
        policy,
        policy_id,
        allow_advisory_drift=True,
    )
    if base["status"] != "pass":
        raise EvaluationError("cross-CLI waiver requires a passing version-2 aggregate receipt")
    changed = changed_inventory_paths(base.get("candidate_inventory", []), files)
    if not changed:
        raise EvaluationError("cross-CLI waiver requires a candidate change")
    if "SKILL.md" in changed or not all(path.startswith("scripts/") for path in changed):
        raise EvaluationError("cross-CLI waivers may change only restricted scripts/ paths")
    test_path = (skill_dir / args.test_script).resolve()
    try:
        test_script = test_path.relative_to(skill_dir).as_posix()
    except ValueError as exc:
        raise EvaluationError("waiver test script must remain inside the skill") from exc
    before = {item["path"]: item for item in base["candidate_inventory"]}
    after = {item["path"]: item for item in files}
    if not test_script.startswith("scripts/") or test_script in changed or before.get(test_script) != after.get(test_script):
        raise EvaluationError("waiver test script must be an unchanged scripts/ file")
    result = subprocess.run([str(test_path)], cwd=skill_dir, capture_output=True, text=True)
    if result.returncode != 0:
        raise EvaluationError("cross-CLI waiver test command failed")
    try:
        attestation = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise EvaluationError("cross-CLI waiver test command must emit one JSON attestation") from exc
    expected_files = {path: after[path]["sha256"] for path in changed}
    if attestation != {"status": "pass", "verified_files": expected_files}:
        raise EvaluationError("cross-CLI waiver test attestation does not bind every changed file")
    waiver = {
        "schema_version": 2,
        "kind": "cross_cli_waiver",
        "status": "waived",
        "skill_path": str(skill_dir),
        "candidate_id": candidate,
        "base_candidate_id": base_candidate,
        "base_aggregate_receipt_sha256": base_sha,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "required_executors": policy["required_executors"],
        "changed_paths": changed,
        "waiver_reason": require_text(args.reason, "waiver reason"),
        "test_command": [test_script],
        "test_script": test_script,
        "test_script_sha256": after[test_script]["sha256"],
        "test_attestation": attestation,
        "test_result_sha256": digest(canonical(attestation)),
    }
    waiver["waiver_id"] = identity_with("waiver_id", waiver)
    path, waiver_sha = write_v2_receipt(waiver)
    atomic_write(
        v2_latest_waiver_path(skill_dir),
        {
            "schema_version": 2,
            "skill_path": str(skill_dir),
            "candidate_id": candidate,
            "waiver_path": str(path),
            "waiver_sha256": waiver_sha,
        },
    )
    return {"status": "waived", "receipt": str(path), "receipt_sha256": waiver_sha}


def v2_waiver_validate(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    candidate, files, suite, suite_id, policy, policy_id = load_v2_inputs(skill_dir, args.suite, args.policy)
    waiver, waiver_sha = load_v2_receipt(Path(args.waiver).resolve())
    validate_v2_waiver(waiver, skill_dir, candidate, files, suite, suite_id, policy, policy_id)
    return {"status": "waived", "candidate_id": candidate, "receipt_sha256": waiver_sha}


def current_gate(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    skill_key = latest_key(str(skill_dir))
    v2_state_exists = (
        (v2_evaluation_dir() / "latest" / f"{skill_key}.json").exists()
        or v2_latest_waiver_path(skill_dir).exists()
        or (v2_evaluation_dir() / "authority" / skill_key).exists()
    )
    if not (skill_dir / POLICY_FILE).is_file():
        if v2_state_exists:
            raise EvaluationError("cross-CLI evaluation state exists; legacy gate downgrade is forbidden")
        legacy = argparse.Namespace(skill_dir=str(skill_dir))
        return gate(legacy)
    if load_json(skill_dir / POLICY_FILE).get("schema_version") != POLICY_SCHEMA_VERSION:
        if v2_state_exists:
            raise EvaluationError("cross-CLI evaluation state exists; legacy policy downgrade is forbidden")
        legacy = argparse.Namespace(skill_dir=str(skill_dir))
        return gate(legacy)
    candidate, files, suite, suite_id, policy, policy_id = load_v2_inputs(skill_dir, None, None)
    if desired_executor_names() != [item["name"] for item in policy["required_executors"]]:
        raise EvaluationError("active required executor set differs from DREAMING_EVALUATION_EXECUTORS")
    authority_path = v2_authority_path(skill_dir, candidate)
    if authority_path.is_file():
        validate_args = argparse.Namespace(
            skill_dir=str(skill_dir), authority=str(authority_path), suite=None, policy=None
        )
        return v2_authority_validate(validate_args)
    latest_path = v2_latest_waiver_path(skill_dir)
    latest = load_json(latest_path)
    require_exact_keys(
        latest,
        "latest waiver",
        {"schema_version", "skill_path", "candidate_id", "waiver_path", "waiver_sha256"},
    )
    waiver_path = Path(require_text(latest.get("waiver_path"), "latest waiver path")).resolve()
    waiver, waiver_sha = load_v2_receipt(waiver_path)
    if latest != {
        "schema_version": 2,
        "skill_path": str(skill_dir),
        "candidate_id": candidate,
        "waiver_path": str(waiver_path),
        "waiver_sha256": waiver_sha,
    }:
        raise EvaluationError("latest cross-CLI waiver pointer is stale or malformed")
    validate_v2_waiver(
        waiver, skill_dir, candidate, files, suite, suite_id, policy, policy_id
    )
    return {"status": "waived", "candidate_id": candidate, "receipt_sha256": waiver_sha}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("skill_dir")
    prepare_parser.add_argument("--cases")
    prepare_parser.add_argument("--model", required=True)
    prepare_parser.add_argument("--run-dir", required=True)
    prepare_parser.add_argument("--plugin-dir", required=True)
    finalize_parser = commands.add_parser("finalize")
    finalize_parser.add_argument("--run-dir", required=True)
    gate_parser = commands.add_parser("gate")
    gate_parser.add_argument("skill_dir")
    current_gate_parser = commands.add_parser("current-gate")
    current_gate_parser.add_argument("skill_dir")
    waive_parser = commands.add_parser("waive")
    waive_parser.add_argument("skill_dir")
    waive_parser.add_argument("--base-receipt", required=True)
    waive_parser.add_argument("--waiver-class", choices=sorted(WAIVER_CLASSES), required=True)
    waive_parser.add_argument("--reason", required=True)
    waive_parser.add_argument("--test-script")
    suite_validate_parser = commands.add_parser("v2-suite-validate")
    suite_validate_parser.add_argument("suite")
    policy_validate_parser = commands.add_parser("v2-policy-validate")
    policy_validate_parser.add_argument("policy")
    v2_prepare_parser = commands.add_parser("v2-prepare")
    v2_prepare_parser.add_argument("skill_dir")
    v2_prepare_parser.add_argument("--suite")
    v2_prepare_parser.add_argument("--policy")
    run_compile_parser = commands.add_parser("v2-run-compile")
    run_compile_parser.add_argument("skill_dir")
    run_compile_parser.add_argument("--run-dir", required=True)
    run_compile_parser.add_argument("--config", required=True)
    run_compile_parser.add_argument("--routing", required=True)
    run_compile_parser.add_argument("--nonce", required=True)
    run_compile_parser.add_argument("--harness", required=True)
    run_compile_parser.add_argument("--suite")
    run_compile_parser.add_argument("--policy")
    run_execute_parser = commands.add_parser("v2-run-execute")
    run_execute_parser.add_argument("--run-dir", required=True)
    run_execute_parser.add_argument("--result-dir", required=True)
    run_execute_parser.add_argument("--routing", required=True)
    run_execute_parser.add_argument("--scratch", required=True)
    run_execute_parser.add_argument("--harness", required=True)
    result_certify_parser = commands.add_parser("v2-result-certify")
    result_certify_parser.add_argument("skill_dir")
    result_certify_parser.add_argument("--run-dir", required=True)
    result_certify_parser.add_argument("--result-dir", required=True)
    result_certify_parser.add_argument("--routing", required=True)
    result_certify_parser.add_argument("--scratch", required=True)
    result_certify_parser.add_argument("--nonce", required=True)
    result_certify_parser.add_argument("--harness", required=True)
    result_certify_parser.add_argument("--suite")
    result_certify_parser.add_argument("--policy")
    unavailable_parser = commands.add_parser("v2-unavailable-aggregate")
    unavailable_parser.add_argument("skill_dir")
    unavailable_parser.add_argument("--unavailable", action="append", required=True)
    unavailable_parser.add_argument("--suite")
    unavailable_parser.add_argument("--policy")
    authority_write_parser = commands.add_parser("v2-authority-write")
    authority_write_parser.add_argument("skill_dir")
    authority_write_parser.add_argument("--aggregate", required=True)
    authority_write_parser.add_argument("--suite")
    authority_write_parser.add_argument("--policy")
    authority_validate_parser = commands.add_parser("v2-authority-validate")
    authority_validate_parser.add_argument("skill_dir")
    authority_validate_parser.add_argument("--authority")
    authority_validate_parser.add_argument("--suite")
    authority_validate_parser.add_argument("--policy")
    v2_waive_parser = commands.add_parser("v2-waive")
    v2_waive_parser.add_argument("skill_dir")
    v2_waive_parser.add_argument("--base-aggregate", required=True)
    v2_waive_parser.add_argument("--reason", required=True)
    v2_waive_parser.add_argument("--test-script", required=True)
    v2_waive_parser.add_argument("--suite")
    v2_waive_parser.add_argument("--policy")
    v2_waiver_validate_parser = commands.add_parser("v2-waiver-validate")
    v2_waiver_validate_parser.add_argument("skill_dir")
    v2_waiver_validate_parser.add_argument("--waiver", required=True)
    v2_waiver_validate_parser.add_argument("--suite")
    v2_waiver_validate_parser.add_argument("--policy")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = {
            "prepare": prepare,
            "finalize": finalize,
            "gate": gate,
            "current-gate": current_gate,
            "waive": waive,
            "v2-suite-validate": v2_suite_validate,
            "v2-policy-validate": v2_policy_validate,
            "v2-prepare": v2_prepare,
            "v2-run-compile": v2_run_compile,
            "v2-run-execute": v2_run_execute,
            "v2-result-certify": v2_result_certify,
            "v2-unavailable-aggregate": v2_unavailable_aggregate,
            "v2-authority-write": v2_authority_write,
            "v2-authority-validate": v2_authority_validate,
            "v2-waive": v2_waive,
            "v2-waiver-validate": v2_waiver_validate,
        }[args.command](args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (EvaluationError, KeyError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"REFUSED: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
