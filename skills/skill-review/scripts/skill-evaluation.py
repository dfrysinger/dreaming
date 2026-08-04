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
    if schema_version == 1:
        suite = compile_legacy_cases(raw)
        return suite, f"sha256:{digest(canonical(suite))}"
    if schema_version != SUITE_SCHEMA_VERSION:
        raise EvaluationError("suite schema_version must be 1 or 2")
    require_exact_keys(raw, "suite", {"schema_version", "graders", "cases"})
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
    require_exact_keys(
        raw,
        "policy",
        {"schema_version", "profile", "policy_kind", "required_executors", "comparator"},
    )
    profile = raw.get("profile")
    if profile not in PROFILES:
        raise EvaluationError("policy.profile must be gate or iterate")
    policy_kind = raw.get("policy_kind")
    if policy_kind not in POLICY_KINDS:
        raise EvaluationError("policy.policy_kind must be capability_uplift or encoded_preference")
    executors_value = raw.get("required_executors")
    if not isinstance(executors_value, list) or not executors_value:
        raise EvaluationError("policy.required_executors must be a non-empty ordered list")
    executors = [
        validate_executor(item, f"policy.required_executors[{index}]")
        for index, item in enumerate(executors_value)
    ]
    names = [executor["name"] for executor in executors]
    if len(set(names)) != len(names):
        raise EvaluationError("policy.required_executors cannot repeat an executor")
    canonical_order = [name for name in EXECUTOR_NAMES if name in names]
    if names != canonical_order:
        raise EvaluationError(
            "policy.required_executors must follow copilot, claude, codex order"
        )
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
        "trials_per_arm": 3 if profile == "gate" else 1,
        "policy_kind": policy_kind,
        "required_executors": executors,
        "comparator": comparator,
    }
    return policy, f"sha256:{digest(canonical(policy))}"


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


def identity_with(field: str, value: dict[str, Any]) -> str:
    return f"sha256:{digest(canonical({key: item for key, item in value.items() if key != field}))}"


def validate_certificate(
    value: Any, candidate: str, suite_id: str, policy_id: str, policy: dict[str, Any], index: int
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
            "profile",
            "executor",
            "result_bundle_sha256",
            "certificate_id",
        },
    )
    if value.get("schema_version") != 2 or value.get("kind") != "executor_certificate":
        raise EvaluationError(f"{field} must be a schema-v2 executor_certificate")
    if value.get("status") not in CERTIFICATE_STATUSES:
        raise EvaluationError(f"{field}.status is invalid")
    if value.get("candidate_id") != candidate or value.get("suite_id") != suite_id or value.get("policy_id") != policy_id:
        raise EvaluationError(f"{field} does not bind the aggregate inputs")
    if value.get("profile") != policy["profile"]:
        raise EvaluationError(f"{field}.profile does not match policy")
    require_sha256(value.get("result_bundle_sha256"), f"{field}.result_bundle_sha256")
    executor = validate_executor(value.get("executor"), f"{field}.executor")
    expected_id = identity_with("certificate_id", value)
    if value.get("certificate_id") != expected_id:
        raise EvaluationError(f"{field}.certificate_id does not match certificate content")
    return {**value, "executor": executor}


def validate_aggregate(
    value: Any, skill_dir: Path, candidate: str, suite: dict[str, Any], suite_id: str,
    policy: dict[str, Any], policy_id: str
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
            "profile",
            "certificates",
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
    certificates_value = value.get("certificates")
    if not isinstance(certificates_value, list):
        raise EvaluationError("aggregate.certificates must be a list")
    certificates = [
        validate_certificate(item, candidate, suite_id, policy_id, policy, index)
        for index, item in enumerate(certificates_value)
    ]
    expected_executors = policy["required_executors"]
    actual_executors = [certificate["executor"] for certificate in certificates]
    if actual_executors != expected_executors:
        raise EvaluationError("aggregate certificates must contain every required executor in policy order")
    statuses = [certificate["status"] for certificate in certificates]
    expected_status = (
        "regression" if "regression" in statuses
        else "inconclusive" if any(status != "pass" for status in statuses)
        else "pass"
    )
    if value.get("status") != expected_status:
        raise EvaluationError(
            f"aggregate.status must be {expected_status!r} for the independent executor certificates"
        )
    expected_id = identity_with("aggregate_id", value)
    if value.get("aggregate_id") != expected_id:
        raise EvaluationError("aggregate.aggregate_id does not match aggregate content")
    return {**value, "certificates": certificates}


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
        "profile": policy["profile"],
        "trials_per_arm": policy["trials_per_arm"],
        "required_executors": policy["required_executors"],
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
        "profile": policy["profile"],
        "trials_per_arm": policy["trials_per_arm"],
        "required_executors": policy["required_executors"],
        "comparator": policy["comparator"],
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
    aggregate = validate_aggregate(aggregate, skill_dir, candidate, suite, suite_id, policy, policy_id)
    if aggregate["status"] != "pass":
        raise EvaluationError("cross-CLI authority requires a passing aggregate receipt")
    if authority.get("aggregate_id") != aggregate["aggregate_id"]:
        raise EvaluationError("authority document aggregate identity does not match")
    expected_id = identity_with("authority_id", authority)
    if authority.get("authority_id") != expected_id:
        raise EvaluationError("authority.authority_id does not match authority content")
    return authority


def v2_authority_write(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    candidate, _, suite, suite_id, policy, policy_id = load_v2_inputs(skill_dir, args.suite, args.policy)
    aggregate = load_json(Path(args.aggregate).resolve())
    aggregate = validate_aggregate(aggregate, skill_dir, candidate, suite, suite_id, policy, policy_id)
    if aggregate["status"] != "pass":
        raise EvaluationError("only a passing aggregate receipt can issue cross-CLI authority")
    aggregate_path, aggregate_sha = write_v2_receipt(aggregate)
    authority = {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "kind": "cross_cli_authority",
        "skill_path": str(skill_dir),
        "candidate_id": candidate,
        "suite_id": suite_id,
        "policy_id": policy_id,
        "aggregate_receipt_sha256": aggregate_sha,
        "aggregate_id": aggregate["aggregate_id"],
    }
    authority["authority_id"] = identity_with("authority_id", authority)
    authority_path = v2_authority_path(skill_dir, candidate)
    atomic_write(authority_path, authority)
    authority_sha = digest(canonical(authority))
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
    }


def v2_authority_validate(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    candidate, _, suite, suite_id, policy, policy_id = load_v2_inputs(skill_dir, args.suite, args.policy)
    path = Path(args.authority).resolve() if args.authority else v2_authority_path(skill_dir, candidate)
    authority = load_json(path)
    expected_path = v2_authority_path(skill_dir, candidate)
    if path != expected_path:
        raise EvaluationError("authority document path does not match skill and candidate identity")
    validate_authority(authority, skill_dir, candidate, suite, suite_id, policy, policy_id)
    authority_sha = digest(canonical(authority))
    latest = load_json(v2_evaluation_dir() / "latest" / f"{latest_key(str(skill_dir))}.json")
    if latest != {
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
    validate_aggregate(base, skill_dir, base_candidate, suite, suite_id, policy, policy_id)
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
    validate_aggregate(base, skill_dir, base_candidate, suite, suite_id, policy, policy_id)
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
    return {"status": "waived", "receipt": str(path), "receipt_sha256": waiver_sha}


def v2_waiver_validate(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = Path(args.skill_dir).resolve()
    candidate, files, suite, suite_id, policy, policy_id = load_v2_inputs(skill_dir, args.suite, args.policy)
    waiver, waiver_sha = load_v2_receipt(Path(args.waiver).resolve())
    validate_v2_waiver(waiver, skill_dir, candidate, files, suite, suite_id, policy, policy_id)
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
            "waive": waive,
            "v2-suite-validate": v2_suite_validate,
            "v2-policy-validate": v2_policy_validate,
            "v2-prepare": v2_prepare,
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
