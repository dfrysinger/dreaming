#!/usr/bin/env python3
"""Sealed, local skill-evaluation trial evidence harness.

This deliberately owns evidence production only.  It does not import Dreaming
policy code and it never decides whether a candidate may be promoted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


CONTRACT_VERSION = 1
HARNESS_VERSION = "skill-evaluation-harness-1"
SHADOW_CONTRACT_VERSION = 2
SHADOW_HARNESS_VERSION = "skill-evaluation-harness-shadow-2"
MAX_FILE_BYTES = 1_000_000
MAX_INPUT_FILES = 256
MAX_RESULT_FILES = 20_000
MAX_TRIALS = 1_024
RESULT_FILES_PER_TRIAL = 6
RESULT_FILES_PER_PAIR = 3
RESULT_FILES_OVERHEAD = 64
MAX_OUTPUT_BYTES = 1_000_000
MAX_EVENTS = 1_000
READ_CHUNK_BYTES = 65_536
TRIAL_STATUSES = ("pass", "fail", "invalid", "regression", "inconclusive")
COMPARISON_STATUSES = ("complete", "inconclusive")
CASE_CLASSES = ("intended", "related", "activation_positive", "activation_negative")
BEHAVIOR_CLASSES = {"intended", "related"}
EVENT_KINDS = {
    "user_message", "assistant_message", "skill_load", "tool_call",
    "tool_result", "artifact_write", "final_answer", "usage", "trial_end",
    "authority_request",
}
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RUN_ID_FIELDS = (
    "schema_version", "kind", "candidate_id", "suite_id", "profile", "trials_per_arm",
    "executors", "comparator", "harness_executable_sha256", "tool_policy_id",
    "grader_set_id", "retention_policy_id", "limits", "file_inventory",
)
EXECUTOR_IDENTITY_KEYS = {
    "adapter_id", "adapter_version", "adapter_executable_sha256", "model",
    "cli_executable_sha256", "cli_version", "tool_policy_id", "limits", "sandbox_id",
}
ADAPTER_IDENTITY_KEYS = ("adapter_id", "adapter_version", "adapter_executable_sha256")
COMPARATOR_IDENTITY_KEYS = {
    "route", "model", "adapter_id", "adapter_version", "adapter_executable_sha256",
    "timeout_seconds", "token_budget", "rubric_id",
}
# The harness owns its child environment.  Routing supplies argv only.
HARNESS_PATH = os.pathsep.join(dict.fromkeys(
    [str(Path(sys.executable).resolve().parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
))
HARNESS_ROOT = Path(__file__).resolve().parent
ORIGINAL_CWD = Path.cwd().resolve()


class HarnessError(ValueError):
    """A contract violation which must fail closed."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha(value: Any) -> str:
    return sha_bytes(canonical(value))


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HarnessError(f"invalid JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise HarnessError(f"{path} must contain an object")
    return value


def write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    if len(data) > MAX_FILE_BYTES:
        raise HarnessError(f"refusing oversized result file {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}"
    temporary.write_bytes(data)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    write_bytes(path, canonical(value) + b"\n")


def require_keys(value: Any, keys: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HarnessError(f"{field} must be an object")
    actual = set(value)
    if actual != keys:
        raise HarnessError(f"{field} keys differ: missing={sorted(keys - actual)} unknown={sorted(actual - keys)}")
    return value


def text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise HarnessError(f"{field} must be non-empty text")
    return value


def identity(value: Any, field: str) -> str:
    value = text(value, field)
    if not SHA_RE.fullmatch(value):
        raise HarnessError(f"{field} must be a sha256 identity")
    return value


def integer(value: Any, field: str, minimum: int = 0, maximum: int = 100000) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise HarnessError(f"{field} must be an integer in [{minimum}, {maximum}]")
    return value


def under(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise HarnessError(f"path escapes owned root: {path}") from error
    return resolved


def relative_component(value: Any, field: str) -> str:
    value = text(value, field)
    if value.startswith("/") or ".." in Path(value).parts or Path(value).is_absolute():
        raise HarnessError(f"{field} must be a contained relative path")
    return value


def regular_inventory(root: Path, exclude: set[str] | None = None, limit: int = MAX_INPUT_FILES) -> list[dict[str, Any]]:
    exclude = exclude or set()
    result: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel in exclude:
            continue
        if path.is_symlink():
            raise HarnessError(f"symlink forbidden: {rel}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise HarnessError(f"non-regular file forbidden: {rel}")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise HarnessError(f"oversized file: {rel}")
        result.append({"path": rel, "sha256": sha_bytes(path.read_bytes()), "size": size})
        if len(result) > limit:
            raise HarnessError(f"too many files under {root} (bound {limit})")
    return result


def normalize_inventory(declared: Any, field: str, limit: int) -> list[dict[str, Any]]:
    if not isinstance(declared, list) or not declared:
        raise HarnessError(f"{field} must be a non-empty inventory")
    if len(declared) > limit:
        raise HarnessError(f"{field} exceeds its file-count bound {limit}")
    normalized: list[dict[str, Any]] = []
    paths: set[str] = set()
    for index, item in enumerate(declared):
        require_keys(item, {"path", "sha256", "size"}, f"{field}[{index}]")
        rel = relative_component(item["path"], f"{field}[{index}].path")
        if rel in paths:
            raise HarnessError(f"duplicate inventory path {rel}")
        paths.add(rel)
        identity(item["sha256"], f"{field}[{index}].sha256")
        integer(item["size"], f"{field}[{index}].size", 0, MAX_FILE_BYTES)
        normalized.append({"path": rel, "sha256": item["sha256"], "size": item["size"]})
    return normalized


def verify_inventory(root: Path, declared: Any, field: str, limit: int) -> list[dict[str, Any]]:
    normalized = normalize_inventory(declared, field, limit)
    actual = regular_inventory(root, {"manifest.json"}, limit)
    if normalized != actual:
        raise HarnessError(f"{field} does not match filesystem")
    return normalized


def derive_run_id(manifest: dict[str, Any]) -> str:
    """Canonical run identity over every sealed input the harness may act on."""
    missing = [field for field in RUN_ID_FIELDS if field not in manifest]
    if missing:
        raise HarnessError(f"run manifest missing bound fields {missing}")
    return sha({field: manifest[field] for field in RUN_ID_FIELDS})


def recheck_input(run: Path, sealed_inventory: list[dict[str, Any]], stage: str) -> None:
    """The sealed input must still be byte-identical at every trust boundary."""
    if regular_inventory(run, {"manifest.json"}, MAX_INPUT_FILES) != sealed_inventory:
        raise HarnessError(f"sealed input changed before {stage}")


def load_manifest(run: Path) -> dict[str, Any]:
    manifest = read_json(run / "manifest.json")
    keys = {
        "schema_version", "kind", "run_id", "invocation_nonce", "candidate_id",
        "suite_id", "profile", "trials_per_arm", "executors", "comparator",
        "harness_executable_sha256", "tool_policy_id", "grader_set_id",
        "retention_policy_id", "limits", "file_inventory",
    }
    require_keys(manifest, keys, "run manifest")
    if manifest["schema_version"] != CONTRACT_VERSION or manifest["kind"] != "skill_evaluation_run":
        raise HarnessError("unsupported run manifest version or kind")
    for field in ("run_id", "candidate_id", "suite_id", "harness_executable_sha256",
                  "tool_policy_id", "grader_set_id", "retention_policy_id"):
        identity(manifest[field], field)
    text(manifest["invocation_nonce"], "invocation_nonce")
    if manifest["profile"] not in {"gate", "iterate"}:
        raise HarnessError("profile must be gate or iterate")
    expected_trials = 3 if manifest["profile"] == "gate" else 1
    if integer(manifest["trials_per_arm"], "trials_per_arm", 1, 3) != expected_trials:
        raise HarnessError("gate requires three trials and iterate requires one")
    require_keys(manifest["limits"], {"timeout_seconds", "output_bytes", "file_bytes", "global_concurrency", "per_executor_concurrency"}, "limits")
    integer(manifest["limits"]["timeout_seconds"], "limits.timeout_seconds", 1, 600)
    integer(manifest["limits"]["output_bytes"], "limits.output_bytes", 1, MAX_OUTPUT_BYTES)
    integer(manifest["limits"]["file_bytes"], "limits.file_bytes", 1, MAX_FILE_BYTES)
    integer(manifest["limits"]["global_concurrency"], "limits.global_concurrency", 1, 32)
    integer(manifest["limits"]["per_executor_concurrency"], "limits.per_executor_concurrency", 1, 32)
    if manifest["limits"]["per_executor_concurrency"] > manifest["limits"]["global_concurrency"]:
        raise HarnessError("per-executor concurrency exceeds global bound")
    if sha_bytes(Path(__file__).read_bytes()) != manifest["harness_executable_sha256"]:
        raise HarnessError("harness executable digest mismatch")
    validate_executors(manifest["executors"])
    validate_comparator(manifest["comparator"])
    normalize_inventory(manifest["file_inventory"], "run file_inventory", MAX_INPUT_FILES)
    if manifest["run_id"] != derive_run_id(manifest):
        raise HarnessError("run_id is not the canonical digest of the sealed run inputs")
    inventory = verify_inventory(run, manifest["file_inventory"], "run file_inventory", MAX_INPUT_FILES)
    suite = read_json(run / "suite.json")
    if sha(suite) != manifest["suite_id"]:
        raise HarnessError("suite digest mismatch")
    candidate = [entry for entry in inventory if entry["path"].startswith("candidate/")]
    if not candidate or candidate[0]["path"] != "candidate/SKILL.md":
        raise HarnessError("candidate inventory must start with candidate/SKILL.md")
    projection = [
        {"path": entry["path"].removeprefix("candidate/"), "sha256": entry["sha256"], "size": entry["size"]}
        for entry in candidate
    ]
    if sha(projection) != manifest["candidate_id"]:
        raise HarnessError("candidate inventory digest mismatch")
    validate_suite(suite, manifest, inventory)
    return {"manifest": manifest, "suite": suite, "projection": projection, "inventory": inventory}


def validate_executors(value: Any) -> None:
    if not isinstance(value, list) or not value:
        raise HarnessError("executors must be a non-empty list")
    seen: set[str] = set()
    required = {"name", "requirement", *EXECUTOR_IDENTITY_KEYS}
    required_count = 0
    for index, executor in enumerate(value):
        require_keys(executor, required, f"executors[{index}]")
        name = text(executor["name"], f"executors[{index}].name")
        if name in seen:
            raise HarnessError("duplicate executor")
        seen.add(name)
        requirement = text(executor["requirement"], f"executors[{index}].requirement")
        if requirement not in {"required", "advisory"}:
            raise HarnessError("executor requirement must be required or advisory")
        required_count += requirement == "required"
        text(executor["model"], "executor model")
        identity(executor["adapter_id"], "executor adapter_id")
        integer(executor["adapter_version"], "executor adapter_version", 1, 1)
        identity(executor["adapter_executable_sha256"], "executor adapter executable")
        identity(executor["cli_executable_sha256"], "executor CLI executable")
        text(executor["cli_version"], "executor cli_version")
        identity(executor["tool_policy_id"], "executor tool_policy_id")
        identity(executor["sandbox_id"], "executor sandbox_id")
        require_keys(executor["limits"], {"timeout_seconds", "token_budget", "output_bytes"}, "executor limits")
        integer(executor["limits"]["timeout_seconds"], "executor timeout", 1, 600)
        integer(executor["limits"]["token_budget"], "executor token budget", 1, 1_000_000)
        integer(executor["limits"]["output_bytes"], "executor output budget", 1, MAX_OUTPUT_BYTES)
    if required_count == 0:
        raise HarnessError("at least one executor must be required")


def validate_comparator(value: Any) -> None:
    require_keys(value, COMPARATOR_IDENTITY_KEYS, "comparator")
    text(value["route"], "comparator route")
    text(value["model"], "comparator model")
    identity(value["adapter_id"], "comparator adapter_id")
    integer(value["adapter_version"], "comparator adapter_version", 1, 1)
    identity(value["adapter_executable_sha256"], "comparator executable")
    integer(value["timeout_seconds"], "comparator timeout", 1, 600)
    integer(value["token_budget"], "comparator token budget", 1, 1_000_000)
    identity(value["rubric_id"], "comparator rubric")


def validate_suite(suite: dict[str, Any], manifest: dict[str, Any], inventory: list[dict[str, Any]] | None = None) -> None:
    require_keys(suite, {"schema_version", "kind", "grader_set_id", "identity_markers", "graders", "cases", "rubric"}, "suite")
    if suite["schema_version"] != CONTRACT_VERSION or suite["kind"] != "skill_evaluation_suite":
        raise HarnessError("unsupported suite")
    if suite["grader_set_id"] != manifest["grader_set_id"]:
        raise HarnessError("grader set identity mismatch")
    if sha(suite["graders"]) != suite["grader_set_id"]:
        raise HarnessError("grader set digest does not bind the grader definitions")
    if not isinstance(suite["identity_markers"], list) or not all(isinstance(x, str) and x for x in suite["identity_markers"]):
        raise HarnessError("identity_markers must be text")
    if sha(suite["rubric"]) != manifest["comparator"]["rubric_id"]:
        raise HarnessError("rubric identity mismatch")
    if not isinstance(suite["graders"], list) or not suite["graders"]:
        raise HarnessError("suite graders required")
    sealed = {entry["path"]: entry for entry in inventory} if inventory is not None else None
    graders: set[str] = set()
    for item in suite["graders"]:
        require_keys(item, {"id", "type", "safety", "config"}, "grader definition")
        grader_id = text(item["id"], "grader id")
        if grader_id in graders:
            raise HarnessError("duplicate grader")
        graders.add(grader_id)
        if item["type"] not in {"regex", "json_schema", "file", "command", "trace", "numeric"} or not isinstance(item["safety"], bool) or not isinstance(item["config"], dict):
            raise HarnessError("invalid grader fields")
        if item["type"] == "command":
            validate_command_grader(item, sealed)
    if not isinstance(suite["cases"], list) or not suite["cases"]:
        raise HarnessError("suite cases required")
    ids: set[str] = set()
    tasks: set[str] = set()
    for case in suite["cases"]:
        require_keys(case, {"id", "class", "task_id", "prompt", "fixture", "artifacts", "graders", "semantic"}, "case")
        case_id = text(case["id"], "case id")
        task = text(case["task_id"], "task id")
        if case_id in ids or task in tasks:
            raise HarnessError("case and task identifiers must be unique")
        ids.add(case_id); tasks.add(task)
        if case["class"] not in set(CASE_CLASSES):
            raise HarnessError("unsupported case class")
        text(case["prompt"], "case prompt")
        text(case["fixture"], "case fixture")
        if not isinstance(case["artifacts"], list) or len(set(case["artifacts"])) != len(case["artifacts"]):
            raise HarnessError("case artifacts must be a unique list")
        for artifact in case["artifacts"]:
            relative_component(artifact, "case artifact path")
        if not isinstance(case["graders"], list) or not case["graders"] or not set(case["graders"]) <= graders:
            raise HarnessError("case references invalid graders")
        if not any(item["id"] in case["graders"] and item["safety"] for item in suite["graders"]):
            raise HarnessError("case needs safety grader")
        if not isinstance(case["semantic"], bool):
            raise HarnessError("case semantic must be bool")


def validate_command_grader(grader: dict[str, Any], sealed: dict[str, dict[str, Any]] | None) -> str:
    config = require_keys(grader["config"], {"argv", "timeout_seconds", "program_sha256"}, "command grader config")
    argv = config["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
        raise HarnessError("invalid command grader argv")
    program = relative_component(argv[0], "command grader program")
    identity(config["program_sha256"], "command grader program_sha256")
    integer(config["timeout_seconds"], "command grader timeout_seconds", 1, 60)
    if sealed is not None:
        entry = sealed.get(f"graders/{program}")
        if entry is None:
            raise HarnessError(f"command grader program graders/{program} is not part of the sealed input")
        if entry["sha256"] != config["program_sha256"]:
            raise HarnessError("command grader program digest differs from the sealed input")
    return program


def load_routing(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Routing is a trusted path-to-argv map only.  It carries no policy."""
    routing = read_json(path)
    require_keys(routing, {"schema_version", "kind", "executors", "comparator"}, "routing")
    if routing["schema_version"] != CONTRACT_VERSION or routing["kind"] != "skill_evaluation_routing":
        raise HarnessError("unsupported routing config")
    if not isinstance(routing["executors"], list):
        raise HarnessError("routing executors invalid")
    executor_routes: dict[str, Any] = {}
    for route in routing["executors"]:
        require_keys(route, {"name", "adapter_id", "adapter_executable_sha256", "argv"}, "executor route")
        name = text(route["name"], "route name")
        if name in executor_routes or not isinstance(route["argv"], list) or not route["argv"] or not all(isinstance(x, str) and x for x in route["argv"]):
            raise HarnessError("invalid executor route argv")
        executable = Path(route["argv"][0])
        if not executable.is_file() or executable.is_symlink():
            raise HarnessError("untrusted executor executable")
        if sha_bytes(executable.read_bytes()) != route["adapter_executable_sha256"]:
            raise HarnessError("routing executable digest mismatch")
        executor_routes[name] = route
    expected = {item["name"]: item for item in manifest["executors"]}
    if set(executor_routes) != set(expected):
        raise HarnessError("routing executor set differs from manifest")
    for name, executor in expected.items():
        route = executor_routes[name]
        if route["adapter_id"] != executor["adapter_id"] or route["adapter_executable_sha256"] != executor["adapter_executable_sha256"]:
            raise HarnessError("routing changes executor identity")
    comparator = routing["comparator"]
    require_keys(comparator, {"route", "adapter_id", "adapter_executable_sha256", "argv"}, "comparator route")
    if not isinstance(comparator["argv"], list) or not comparator["argv"] or not all(isinstance(x, str) and x for x in comparator["argv"]):
        raise HarnessError("invalid comparator argv")
    executable = Path(comparator["argv"][0])
    if not executable.is_file() or executable.is_symlink() or sha_bytes(executable.read_bytes()) != comparator["adapter_executable_sha256"]:
        raise HarnessError("untrusted comparator executable")
    supplied = manifest["comparator"]
    if any(comparator[key] != supplied[key] for key in ("route", "adapter_id", "adapter_executable_sha256")):
        raise HarnessError("routing changes comparator identity")
    audit = {
        "routing_config_sha256": sha_bytes(path.read_bytes()),
        "executor_argv_sha256": {name: sha(route["argv"]) for name, route in sorted(executor_routes.items())},
        "comparator_argv_sha256": sha(comparator["argv"]),
        "environment_sha256": sha({"PATH": HARNESS_PATH, "LANG": "C", "LC_ALL": "C"}),
    }
    return {"executors": executor_routes, "comparator": comparator, "audit": audit}


class Scratch:
    """Disposable harness state outside every sealed tree.  Never part of a result."""

    def __init__(self, supplied: Path, owned: list[Path]) -> None:
        base = Path(supplied).resolve()
        if Path(supplied).is_symlink() or not base.is_dir():
            raise HarnessError("scratch directory must exist and be a real directory")
        if any(base.iterdir()):
            raise HarnessError("scratch directory must be empty")
        for tree in owned:
            resolved = tree.resolve()
            if base == resolved or resolved in base.parents or base in resolved.parents:
                raise HarnessError("scratch directory must lie outside the sealed input and result trees")
        self.base = base
        self.root = base / f"harness-{os.getpid()}-{os.urandom(4).hex()}"
        self.root.mkdir()
        os.chmod(self.root, 0o700)

    def fresh(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts, os.urandom(8).hex())
        path.mkdir(parents=True)
        os.chmod(path, 0o700)
        return path

    def discard(self, path: Path) -> None:
        shutil.rmtree(under(path, self.root), ignore_errors=True)

    def remove(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        if self.root.exists() or any(self.base.iterdir()):
            raise HarnessError("harness scratch state could not be removed")


def owned_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def clean_directory(path: Path) -> Path:
    """Adapters never inherit the invoking or repository working directory."""
    resolved = Path(path).resolve()
    if Path(path).is_symlink() or not resolved.is_dir():
        raise HarnessError("adapter working directory must be a real directory")
    if (resolved == ORIGINAL_CWD or resolved == HARNESS_ROOT
            or HARNESS_ROOT in resolved.parents or resolved in HARNESS_ROOT.parents):
        raise HarnessError("adapter working directory must not be the invocation or harness directory")
    if any(resolved.iterdir()):
        raise HarnessError("adapter working directory must be clean")
    return resolved


def terminate(process: subprocess.Popen[bytes]) -> None:
    for number in (signal.SIGTERM, signal.SIGKILL):
        if process.poll() is not None:
            break
        try:
            os.killpg(os.getpgid(process.pid), number)
        except (ProcessLookupError, PermissionError, OSError):
            break
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            continue
    if process.poll() is None:
        process.wait()


def call(argv: list[str], environment: dict[str, str], timeout: int, output_limit: int, cwd: Path) -> tuple[dict[str, Any], str]:
    """Call one owned process group with bounded streaming capture."""
    directory = clean_directory(cwd)
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, cwd=str(directory), start_new_session=True, close_fds=True,
        )
    except OSError as error:
        raise HarnessError(f"cannot start adapter: {error}") from error
    captured = {"out": bytearray(), "err": bytearray()}
    total = 0
    overflow = False
    expired = False
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "out")
    selector.register(process.stderr, selectors.EVENT_READ, "err")
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                expired = True
                break
            for key, _ in selector.select(min(remaining, 0.25)):
                data = os.read(key.fileobj.fileno(), READ_CHUNK_BYTES)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                total += len(data)
                if total > output_limit:
                    overflow = True
                    break
                captured[key.data].extend(data)
            if overflow:
                break
        if not overflow and not expired:
            remaining = deadline - time.monotonic()
            try:
                process.wait(timeout=max(remaining, 0.0))
            except subprocess.TimeoutExpired:
                expired = True
    finally:
        selector.close()
        if overflow or expired or process.poll() is None:
            terminate(process)
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass
    stdout = bytes(captured["out"]).decode("utf-8", errors="replace")
    stderr = bytes(captured["err"]).decode("utf-8", errors="replace")[:512]
    if overflow:
        raise HarnessError("adapter output exceeds bound")
    if expired:
        raise HarnessError("adapter timeout; process group cancelled")
    if process.returncode != 0:
        detail = stderr
        try:
            failure = json.loads(stdout)
            error = failure.get("error") if isinstance(failure, dict) else None
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                message = error.get("message")
                detail = error["code"] + (
                    f": {message}" if isinstance(message, str) and message else ""
                )
        except json.JSONDecodeError:
            pass
        raise HarnessError(f"adapter failed ({process.returncode}): {detail[:512]}")
    try:
        response = json.loads(stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HarnessError("adapter did not emit one JSON object") from error
    if not isinstance(response, dict):
        raise HarnessError("adapter response is not object")
    return response, stderr


def harness_environment(home: Path) -> dict[str, str]:
    """A fixed minimal environment.  No caller or routing allowlist exists."""
    home.mkdir(parents=True, exist_ok=True)
    return {"PATH": HARNESS_PATH, "HOME": str(home), "LANG": "C", "LC_ALL": "C"}


def attest_executor(response: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    if set(response) != EXECUTOR_IDENTITY_KEYS:
        raise HarnessError("adapter identity response has wrong keys")
    for key in ADAPTER_IDENTITY_KEYS:
        if response[key] != expected[key]:
            raise HarnessError(f"adapter identity mismatch: {key}")
    for key in sorted(EXECUTOR_IDENTITY_KEYS - set(ADAPTER_IDENTITY_KEYS)):
        if response[key] != expected[key]:
            raise HarnessError(f"execution identity mismatch: {key}")
    return response


def attest_comparator(response: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    if set(response) != COMPARATOR_IDENTITY_KEYS:
        raise HarnessError("comparator identity response has wrong keys")
    for key in sorted(COMPARATOR_IDENTITY_KEYS):
        if response[key] != expected[key]:
            raise HarnessError(f"comparator identity mismatch: {key}")
    return response


def trial_id(manifest: dict[str, Any], executor: dict[str, Any], case: dict[str, Any], treatment: str, repetition: int) -> str:
    return sha({"run_id": manifest["run_id"], "executor": executor["name"], "model": executor["model"],
                "case": case["id"], "treatment": treatment, "repetition": repetition})


def pair_id(manifest: dict[str, Any], executor: dict[str, Any], case: dict[str, Any], repetition: int) -> str:
    return sha({"run_id": manifest["run_id"], "executor": executor["name"], "model": executor["model"],
                "case": case["id"], "repetition": repetition})


def split_cases(suite: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    behavior = [item for item in suite["cases"] if item["class"] in BEHAVIOR_CLASSES]
    activation = [item for item in suite["cases"] if item["class"].startswith("activation_")]
    return behavior, activation


def plan_matrix(manifest: dict[str, Any], suite: dict[str, Any]) -> tuple[list[str], list[str], int]:
    """Regenerate the exact expected trial and pair identities for this run."""
    behavior, activation = split_cases(suite)
    trials: list[str] = []
    pairs: list[str] = []
    files = RESULT_FILES_OVERHEAD
    for executor in manifest["executors"]:
        for case in behavior:
            for repetition in range(manifest["trials_per_arm"]):
                for treatment in ("control", "candidate"):
                    trials.append(trial_id(manifest, executor, case, treatment, repetition))
                    files += RESULT_FILES_PER_TRIAL + len(case["artifacts"])
                pairs.append(pair_id(manifest, executor, case, repetition))
                files += RESULT_FILES_PER_PAIR
        for case in activation:
            for repetition in range(manifest["trials_per_arm"]):
                trials.append(trial_id(manifest, executor, case, "candidate", repetition))
                files += RESULT_FILES_PER_TRIAL + len(case["artifacts"])
    if len(set(trials)) != len(trials) or len(set(pairs)) != len(pairs):
        raise HarnessError("trial matrix produced ambiguous identities")
    return trials, pairs, files


def check_projection(manifest: dict[str, Any], suite: dict[str, Any]) -> tuple[list[str], list[str]]:
    trials, pairs, files = plan_matrix(manifest, suite)
    if len(trials) > MAX_TRIALS:
        raise HarnessError(f"projected trial matrix of {len(trials)} exceeds the bound {MAX_TRIALS}")
    if files > MAX_RESULT_FILES:
        raise HarnessError(f"projected result inventory of {files} files exceeds the bound {MAX_RESULT_FILES}")
    return trials, pairs


def copy_candidate(run: Path, projection: list[dict[str, Any]], target: Path) -> None:
    for entry in projection:
        source = run / "candidate" / entry["path"]
        destination = target / entry["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        os.chmod(destination, 0o400)


def trace_from(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
        raise HarnessError("normalizer did not produce a valid trace")
    value = read_json(path)
    require_keys(value, {"schema_version", "events", "diagnostics"}, "normalized trace")
    if value["schema_version"] != CONTRACT_VERSION or not isinstance(value["events"], list) or not isinstance(value["diagnostics"], list):
        raise HarnessError("invalid normalized trace")
    if len(value["events"]) > MAX_EVENTS:
        raise HarnessError("too many normalized events")
    for index, event in enumerate(value["events"], 1):
        if not isinstance(event, dict) or set(event) != {"sequence", "kind", "text", "data"}:
            raise HarnessError("normalized event schema invalid")
        if event["sequence"] != index or event["kind"] not in EVENT_KINDS or not isinstance(event["text"], str) or not isinstance(event["data"], dict):
            raise HarnessError("normalized event invalid")
    return value["events"]


def proof(events: list[dict[str, Any]], treatment: str, candidate_id: str, skill_digest: str, case_class: str) -> tuple[bool, str | None]:
    loads = [event for event in events if event["kind"] == "skill_load"]
    if treatment == "control":
        if any(item["data"].get("non_builtin", True) for item in loads):
            return False, "control loaded non-built-in skill"
        return True, None
    if case_class == "activation_negative":
        if loads:
            return False, "activation-negative loaded a skill"
        return True, None
    if case_class == "related" and not loads:
        return True, None
    matching = [
        item for item in loads
        if item["data"].get("candidate_id") == candidate_id
        and item["data"].get("skill_md_sha256") == skill_digest
        and item["data"].get("path") == "candidate/SKILL.md"
    ]
    if len(matching) != 1 or len(loads) != 1:
        return False, "candidate exact skill-load proof missing or ambiguous"
    return True, None


def final_text(events: list[dict[str, Any]]) -> str:
    answers = [event["text"] for event in events if event["kind"] == "final_answer"]
    return "\n".join(answers)


def safe_artifact_path(root: Path, relative: str) -> Path:
    relative_component(relative, "declared artifact path")
    return under(root / relative, root)


def grade(grader: dict[str, Any], answer: str, events: list[dict[str, Any]], artifacts: Path,
          program_root: Path | None, scratch: Scratch | None) -> dict[str, Any]:
    config = grader["config"]
    kind = grader["type"]
    passed = False
    detail: dict[str, Any] = {}
    if kind == "regex":
        required = config.get("required", [])
        forbidden = config.get("forbidden", [])
        if not isinstance(required, list) or not isinstance(forbidden, list) or not all(isinstance(x, str) for x in required + forbidden):
            raise HarnessError("invalid regex grader config")
        matched = [pattern for pattern in required if re.search(pattern, answer, re.MULTILINE)]
        blocked = [pattern for pattern in forbidden if re.search(pattern, answer, re.MULTILINE)]
        passed = len(matched) == len(required) and not blocked
        detail = {"required_matched": matched, "forbidden_matched": blocked}
    elif kind == "json_schema":
        try:
            value = json.loads(answer)
        except json.JSONDecodeError:
            value = None
        passed = schema_matches(value, config)
        detail = {"parsed": value is not None}
    elif kind == "file":
        path = text(config.get("path"), "file grader path")
        artifact = safe_artifact_path(artifacts, path)
        exists = artifact.is_file() and not artifact.is_symlink()
        detail = {"path": path, "exists": exists}
        passed = exists
        if exists:
            contents = artifact.read_bytes()
            detail["sha256"] = sha_bytes(contents)
            detail["size"] = len(contents)
            if "sha256" in config:
                passed = passed and detail["sha256"] == identity(config["sha256"], "file grader sha256")
            if "contains" in config:
                passed = passed and text(config["contains"], "file grader contains").encode() in contents
    elif kind == "trace":
        required = config.get("required_kinds", [])
        forbidden = config.get("forbidden_kinds", [])
        if not isinstance(required, list) or not isinstance(forbidden, list) or not all(isinstance(x, str) for x in required + forbidden):
            raise HarnessError("invalid trace grader config")
        kinds = [event["kind"] for event in events]
        passed = all(item in kinds for item in required) and not any(item in kinds for item in forbidden)
        detail = {"kinds": kinds}
    elif kind == "numeric":
        value = config.get("value")
        minimum = config.get("minimum")
        maximum = config.get("maximum")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not isinstance(minimum, (int, float)) or not isinstance(maximum, (int, float)):
            raise HarnessError("invalid numeric grader config")
        passed = minimum <= value <= maximum
        detail = {"value": value}
    elif kind == "command":
        passed, detail = run_command_grader(grader, artifacts, program_root, scratch)
    else:
        raise HarnessError("unsupported grader")
    return {"id": grader["id"], "type": kind, "safety": grader["safety"], "passed": passed, "detail": detail}


def run_command_grader(grader: dict[str, Any], artifacts: Path, program_root: Path | None,
                       scratch: Scratch | None) -> tuple[bool, dict[str, Any]]:
    if program_root is None or scratch is None:
        raise HarnessError("command grader cannot run without sealed programs and owned scratch")
    config = grader["config"]
    program = validate_command_grader(grader, None)
    executable = under(program_root / program, program_root)
    if not executable.is_file() or (program_root / program).is_symlink():
        raise HarnessError("sealed command grader program missing")
    if sha_bytes(executable.read_bytes()) != config["program_sha256"]:
        raise HarnessError("sealed command grader program digest mismatch")
    workspace = scratch.fresh("graders")
    try:
        response, _ = call(
            [str(executable), *config["argv"][1:], str(artifacts)],
            harness_environment(workspace / "home"), config["timeout_seconds"], MAX_OUTPUT_BYTES,
            owned_directory(workspace / "cwd"),
        )
        if set(response) != {"passed", "detail"} or not isinstance(response["passed"], bool):
            raise HarnessError("command grader response invalid")
        return response["passed"], {"command": response["detail"]}
    finally:
        scratch.discard(workspace)


def schema_matches(value: Any, schema: dict[str, Any]) -> bool:
    """Bounded in-repository JSON schema subset: type, required, properties, enum."""
    allowed = {"type", "required", "properties", "enum"}
    if set(schema) - allowed:
        raise HarnessError("unsupported json_schema keyword")
    expected = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "number": (int, float), "boolean": bool, "null": type(None)}
    if expected not in types or not isinstance(value, types[expected]) or (expected == "number" and isinstance(value, bool)):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if expected == "object":
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        if not isinstance(required, list) or not isinstance(properties, dict) or not all(isinstance(x, str) for x in required):
            raise HarnessError("invalid json_schema object")
        if not all(key in value for key in required):
            return False
        return all(key not in value or schema_matches(value[key], sub) for key, sub in properties.items() if isinstance(sub, dict))
    return True


def seal_command_programs(run: Path, suite: dict[str, Any], result: Path) -> None:
    """Copy the sealed grader program bytes into the result bundle for replay."""
    for grader in suite["graders"]:
        if grader["type"] != "command":
            continue
        program = validate_command_grader(grader, None)
        source = under(run / "graders" / program, run / "graders")
        if not source.is_file() or (run / "graders" / program).is_symlink():
            raise HarnessError("sealed command grader program missing from the input bundle")
        data = source.read_bytes()
        if sha_bytes(data) != grader["config"]["program_sha256"]:
            raise HarnessError("sealed command grader program digest mismatch")
        write_bytes(result / "graders" / program, data, 0o500)


def run_trial(run: Path, result: Path, scratch: Scratch, sealed: dict[str, Any], executor: dict[str, Any],
              route: dict[str, Any], case: dict[str, Any], treatment: str, repetition: int) -> dict[str, Any]:
    manifest, suite, projection = sealed["manifest"], sealed["suite"], sealed["projection"]
    trial = trial_id(manifest, executor, case, treatment, repetition)
    root = result / "trials" / trial.removeprefix("sha256:")
    home, workspace = root / "home", root / "workspace"
    raw, trace, artifacts = root / "raw.jsonl", root / "trace.json", root / "artifacts"
    candidate_root = root / "candidate"
    for directory in (home, workspace, artifacts):
        directory.mkdir(parents=True, exist_ok=True)
    skill_digest = next(entry["sha256"] for entry in projection if entry["path"] == "SKILL.md")
    if treatment == "candidate":
        copy_candidate(run, projection, candidate_root)
    trial_spec = {
        "schema_version": CONTRACT_VERSION, "trial_id": trial, "case": case, "treatment": treatment,
        "executor": executor, "candidate_id": manifest["candidate_id"],
        "candidate_inventory": projection if treatment == "candidate" else [],
        "skill_md_sha256": skill_digest if treatment == "candidate" else None,
        "home": str(home), "workspace": str(workspace),
        "candidate_root": str(candidate_root) if treatment == "candidate" else None,
        "raw": str(raw), "trace": str(trace), "artifacts": str(artifacts),
    }
    spec_path = root / "trial.json"
    write_json(spec_path, trial_spec)
    env = harness_environment(home)
    timeout, output_bytes = executor["limits"]["timeout_seconds"], executor["limits"]["output_bytes"]
    calls = scratch.fresh("trials")
    record: dict[str, Any] = {
        "schema_version": CONTRACT_VERSION, "trial_id": trial, "executor": executor["name"],
        "model": executor["model"], "case_id": case["id"], "case_class": case["class"],
        "treatment": treatment, "repetition": repetition, "status": "inconclusive",
        "skill_load_proved": False, "errors": [], "authoritative": manifest["profile"] == "gate",
    }
    try:
        prepared, _ = call([*route["argv"], "prepare", "--trial", str(spec_path)], env, timeout, output_bytes, owned_directory(calls / "prepare"))
        require_keys(prepared, {"prepared", "execution"}, "prepared response")
        attest_executor(prepared["execution"], executor)
        prepared_record = {"schema_version": CONTRACT_VERSION, "trial_id": trial,
                           "adapter_prepared": prepared["prepared"], "execution": prepared["execution"]}
        prepared_record["prepared_digest"] = sha(prepared_record)
        prepared_path = root / "prepared.json"
        write_json(prepared_path, prepared_record)
        run_response, _ = call([*route["argv"], "run", "--trial", str(spec_path), "--prepared", str(prepared_path), "--output", str(raw)],
                               env, timeout, output_bytes, owned_directory(calls / "run"))
        require_keys(run_response, {"prepared_digest", "effective_execution", "completed"}, "run response")
        if run_response["prepared_digest"] != prepared_record["prepared_digest"] or run_response["completed"] is not True:
            raise HarnessError("run did not consume prepared record")
        attest_executor(run_response["effective_execution"], executor)
        if not raw.is_file() or raw.is_symlink() or raw.stat().st_size > manifest["limits"]["output_bytes"]:
            raise HarnessError("adapter raw log is missing or exceeds its bound")
        os.chmod(raw, 0o600)
        normalized, _ = call([*route["argv"], "normalize", "--raw", str(raw), "--trace", str(trace)],
                             env, timeout, output_bytes, owned_directory(calls / "normalize"))
        require_keys(normalized, {"raw_sha256", "trace_sha256"}, "normalize response")
        if normalized["raw_sha256"] != sha_bytes(raw.read_bytes()) or normalized["trace_sha256"] != sha_bytes(trace.read_bytes()):
            raise HarnessError("normalization digest mismatch")
        os.chmod(trace, 0o600)
        events = trace_from(trace)
        valid_proof, proof_error = proof(events, treatment, manifest["candidate_id"], skill_digest, case["class"])
        record["skill_load_proved"] = valid_proof
        if not valid_proof:
            record["errors"].append(proof_error)
            record["status"] = "regression" if case["class"] == "activation_negative" else "invalid"
        collected, _ = call([*route["argv"], "collect", "--trial", str(spec_path), "--artifacts", str(artifacts)],
                            env, timeout, output_bytes, owned_directory(calls / "collect"))
        require_keys(collected, {"completed_workspace", "declared_artifacts"}, "collect response")
        if collected["completed_workspace"] is not True or not isinstance(collected["declared_artifacts"], list):
            raise HarnessError("artifact collection infrastructure failure")
        artifact_inventory = regular_inventory(artifacts, None, MAX_INPUT_FILES)
        declared_paths: set[str] = set()
        existing_paths: set[str] = set()
        for status in collected["declared_artifacts"]:
            require_keys(status, {"path", "source_exists"}, "declared artifact status")
            path = status["path"]
            if not isinstance(path, str) or path not in case["artifacts"] or path in declared_paths or not isinstance(status["source_exists"], bool):
                raise HarnessError("artifact collector declared an invalid path")
            declared_paths.add(path)
            if status["source_exists"]:
                existing_paths.add(path)
        if declared_paths != set(case["artifacts"]) or existing_paths != {item["path"] for item in artifact_inventory}:
            raise HarnessError("artifact collector exported a different inventory")
        grader_map = {item["id"]: item for item in suite["graders"]}
        if any(grader_map[item]["type"] == "command" for item in case["graders"]):
            recheck_input(run, sealed["inventory"], "command graders")
        results = [
            grade(grader_map[item], final_text(events), events, artifacts, run / "graders", scratch)
            for item in case["graders"]
        ]
        if regular_inventory(artifacts, None, MAX_INPUT_FILES) != artifact_inventory:
            raise HarnessError("a grader mutated the declared artifacts")
        write_json(root / "grader-results.json", {"schema_version": CONTRACT_VERSION, "results": results})
        deterministic_pass = all(item["passed"] for item in results)
        if record["status"] not in {"invalid", "regression"}:
            record["status"] = "pass" if deterministic_pass else "fail"
        record.update({"prepared_digest": prepared_record["prepared_digest"],
                       "effective_execution": run_response["effective_execution"],
                       "raw_sha256": sha_bytes(raw.read_bytes()), "trace_sha256": sha_bytes(trace.read_bytes()),
                       "artifact_inventory": artifact_inventory, "deterministic_pass": deterministic_pass})
    except HarnessError as error:
        record["errors"].append(str(error))
        record["status"] = "inconclusive"
        record["infrastructure_error"] = True
    finally:
        # Authentication and home projection are removed before ordinary workspace cleanup.
        scratch.discard(calls)
        try:
            shutil.rmtree(home)
        except OSError as error:
            record["errors"].append(f"credential projection cleanup failed: {error}")
            record["status"] = "inconclusive"
            record["cleanup_failed"] = True
            record["shared_safety_failure"] = True
        for path in (workspace, candidate_root):
            try:
                shutil.rmtree(path, ignore_errors=path == candidate_root)
            except OSError as error:
                record["errors"].append(f"evidence cleanup failed: {error}")
                record["status"] = "inconclusive"
                record["cleanup_failed"] = True
    write_json(root / "result.json", record)
    return record


def fair_pair(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    """A pair may be compared only when both arms are proved, valid, and matched."""
    differences: list[str] = []
    if left.get("effective_execution") != right.get("effective_execution"):
        differences.append("effective_execution")
    for field in ("case_id", "case_class", "repetition", "executor", "model"):
        if left.get(field) != right.get(field):
            differences.append(f"mismatched_{field}")
    for arm, record in (("control", left), ("candidate", right)):
        if record.get("status") not in {"pass", "fail"}:
            differences.append(f"{arm}_arm_not_comparable")
        if record.get("skill_load_proved") is not True:
            differences.append(f"{arm}_arm_load_unproved")
    return sorted(set(differences))


def blind_assignment(nonce: str, pair: str) -> dict[str, str]:
    """Structural blinding derives from the caller nonce plus the pair identity."""
    digest = sha({"invocation_nonce": nonce, "pair_id": pair}).removeprefix("sha256:")
    left = "control" if int(digest[0], 16) % 2 == 0 else "candidate"
    return {"A": left, "B": "candidate" if left == "control" else "control"}


def comparator_packet(result: Path, case: dict[str, Any], assignment: dict[str, str],
                      left: dict[str, Any], right: dict[str, Any], suite: dict[str, Any]) -> dict[str, Any] | None:
    by_arm = {"control": left, "candidate": right}
    outputs: dict[str, str] = {}
    for label in ("A", "B"):
        arm = by_arm[assignment[label]]
        trial_dir = result / "trials" / arm["trial_id"].removeprefix("sha256:")
        outputs[label] = final_text(trace_from(trial_dir / "trace.json"))
    packet = {"schema_version": CONTRACT_VERSION, "task_id": case["task_id"], "rubric": suite["rubric"],
              "A": outputs["A"], "B": outputs["B"]}
    encoded = canonical(packet).decode("utf-8")
    if [marker for marker in suite["identity_markers"] if marker in encoded]:
        return None
    return packet


def run_comparison(result: Path, scratch: Scratch, pair: str, executor: dict[str, Any], case: dict[str, Any],
                   left: dict[str, Any], right: dict[str, Any], suite: dict[str, Any], manifest: dict[str, Any],
                   route: dict[str, Any], comparator_identity: dict[str, Any]) -> dict[str, Any]:
    comparison: dict[str, Any] = {
        "schema_version": CONTRACT_VERSION, "pair_id": pair, "executor": executor["name"],
        "case_id": case["id"], "case_class": case["class"], "status": "inconclusive",
        "comparator": comparator_identity, "errors": [],
    }
    differences = fair_pair(left, right)
    if differences:
        comparison["errors"] = differences
        return comparison
    assignment = blind_assignment(manifest["invocation_nonce"], pair)
    packet = comparator_packet(result, case, assignment, left, right, suite)
    if packet is None:
        comparison["errors"] = ["identity marker in blind packet"]
        return comparison
    # The comparator sees an opaque scratch path only: no pair identity, no treatment.
    workspace = scratch.fresh("comparisons")
    packet_path, output_path = workspace / "packet.json", workspace / "response.json"
    try:
        write_json(packet_path, packet)
        response, _ = call(
            [*route["argv"], "compare", "--packet", str(packet_path), "--output", str(output_path)],
            harness_environment(workspace / "home"), manifest["comparator"]["timeout_seconds"],
            MAX_OUTPUT_BYTES, owned_directory(workspace / "cwd"),
        )
        require_keys(response, {"response_sha256"}, "comparator response")
        value = read_json(output_path)
        require_keys(value, {"winner", "criteria", "evidence"}, "comparator output")
        if value["winner"] not in {"A", "B", "tie"} or not isinstance(value["criteria"], list) or not isinstance(value["evidence"], str):
            raise HarnessError("invalid comparator verdict")
        if response["response_sha256"] != sha_bytes(output_path.read_bytes()):
            raise HarnessError("comparator response digest mismatch")
        stem = pair.removeprefix("sha256:")
        durable_packet = result / "comparisons" / f"{stem}.packet.json"
        durable_response = result / "comparisons" / f"{stem}.response.json"
        write_bytes(durable_packet, packet_path.read_bytes())
        write_bytes(durable_response, output_path.read_bytes())
        # Unblind only after comparator output is durable.
        comparison.update({"status": "complete", "assignment": assignment,
                           "packet_sha256": sha_bytes(durable_packet.read_bytes()),
                           "response_sha256": sha_bytes(durable_response.read_bytes()),
                           "winner": value["winner"]})
    except HarnessError as error:
        comparison["errors"] = [str(error)]
    finally:
        scratch.discard(workspace)
    return comparison


def build_aggregate(manifest: dict[str, Any], records: list[dict[str, Any]],
                    comparisons: list[dict[str, Any]], infrastructure: dict[str, Any]) -> dict[str, Any]:
    """Diagnostics stay partitioned by executor and case class.  No pooled score surface."""
    executors: dict[str, Any] = {}
    for executor in manifest["executors"]:
        name = executor["name"]
        classes: dict[str, Any] = {}
        for case_class in CASE_CLASSES:
            trials = [item for item in records if item["executor"] == name and item["case_class"] == case_class]
            pairs = [item for item in comparisons if item["executor"] == name and item["case_class"] == case_class]
            if not trials and not pairs:
                continue
            classes[case_class] = {
                "trial_counts": {status: sum(item["status"] == status for item in trials) for status in TRIAL_STATUSES},
                "treatment_counts": {
                    treatment: {status: sum(item["status"] == status for item in trials if item["treatment"] == treatment)
                                for status in TRIAL_STATUSES}
                    for treatment in ("control", "candidate")
                },
                "skill_load_proved": sum(bool(item.get("skill_load_proved")) for item in trials),
                "comparison_counts": {status: sum(item["status"] == status for item in pairs) for status in COMPARISON_STATUSES},
                "comparison_winners": {label: sum(item.get("winner") == label for item in pairs) for label in ("A", "B", "tie")},
                "errors": sorted({error for item in trials for error in item.get("errors", [])} |
                                 {error for item in pairs for error in item.get("errors", [])}),
            }
        executors[name] = {
            "model": executor["model"],
            "requirement": executor["requirement"],
            "state": infrastructure["executor_states"][name]["state"],
            "case_classes": classes,
        }
    return {
        "schema_version": CONTRACT_VERSION, "authoritative": manifest["profile"] == "gate",
        "executors": executors, "infrastructure": infrastructure,
    }


def infrastructure_state(
    manifest: dict[str, Any], records: list[dict[str, Any]], input_recheck: str
) -> dict[str, Any]:
    executor_states: dict[str, Any] = {}
    for executor in manifest["executors"]:
        own = [item for item in records if item["executor"] == executor["name"]]
        errors = sum(bool(item.get("infrastructure_error")) for item in own)
        cleanup = sum(bool(item.get("cleanup_failed")) for item in own)
        executor_states[executor["name"]] = {
            "requirement": executor["requirement"],
            "state": "complete" if errors == 0 and cleanup == 0 else "incomplete",
            "infrastructure_errors": errors,
            "cleanup_failures": cleanup,
        }
    cleanup_failures = sum(item["cleanup_failures"] for item in executor_states.values())
    shared_safety_failures = sum(
        bool(item.get("shared_safety_failure")) for item in records
    )
    shared_complete = input_recheck == "unchanged" and shared_safety_failures == 0
    required_complete = shared_complete and all(
        item["state"] == "complete"
        for item in executor_states.values()
        if item["requirement"] == "required"
    )
    collection_complete = shared_complete and all(
        item["state"] == "complete" for item in executor_states.values()
    )
    return {
        "input_recheck": input_recheck,
        "infrastructure_errors": sum(bool(item.get("infrastructure_error")) for item in records),
        "cleanup_failures": cleanup_failures,
        "shared_safety_failures": shared_safety_failures,
        "required_state": "complete" if required_complete else "incomplete",
        "collection_state": "complete" if collection_complete else "incomplete",
        "executor_states": executor_states,
    }


def seal_result(result: Path, sealed: dict[str, Any], trial_records: list[dict[str, Any]],
                comparisons: list[dict[str, Any]], identities: dict[str, Any], audit: dict[str, Any],
                input_recheck: str) -> None:
    manifest, suite, projection = sealed["manifest"], sealed["suite"], sealed["projection"]
    infrastructure = infrastructure_state(manifest, trial_records, input_recheck)
    write_json(result / "aggregate.json", build_aggregate(manifest, trial_records, comparisons, infrastructure))
    write_json(result / "sealed-input.json", {
        "schema_version": CONTRACT_VERSION, "run_manifest": manifest, "suite": suite,
        "candidate_inventory": projection,
    })
    inventory = regular_inventory(result, {"manifest.json"}, MAX_RESULT_FILES)
    output = {
        "schema_version": CONTRACT_VERSION, "kind": "skill_evaluation_result",
        "state": infrastructure["required_state"],
        "collection_state": infrastructure["collection_state"],
        "executor_states": infrastructure["executor_states"],
        "input_run_id": manifest["run_id"], "invocation_nonce": manifest["invocation_nonce"],
        "harness_version": HARNESS_VERSION, "harness_executable_sha256": sha_bytes(Path(__file__).read_bytes()),
        "profile": manifest["profile"], "candidate_id": manifest["candidate_id"],
        "suite_id": manifest["suite_id"], "grader_set_id": manifest["grader_set_id"],
        "trials": [record["trial_id"] for record in trial_records],
        "pairs": [item["pair_id"] for item in comparisons],
        "executor_identities": identities["executors"], "comparator_identity": identities["comparator"],
        "producer_audit": audit, "file_inventory": inventory,
    }
    output["result_id"] = sha(output)
    write_json(result / "manifest.json", output)


def run(args: argparse.Namespace) -> int:
    run_root, output, routing_path = Path(args.input).resolve(), Path(args.output).resolve(), Path(args.routing).resolve()
    if not run_root.is_dir() or run_root.is_symlink():
        raise HarnessError("run directory must be a real read-only directory")
    if not output.is_dir() or output.is_symlink() or any(output.iterdir()):
        raise HarnessError("output directory must exist, be real, and be empty")
    sealed = load_manifest(run_root)
    manifest, suite = sealed["manifest"], sealed["suite"]
    routing = load_routing(routing_path, manifest)
    routes, comparator_route, audit = routing["executors"], routing["comparator"], routing["audit"]
    check_projection(manifest, suite)
    scratch = Scratch(Path(args.scratch), [run_root, output])
    identities: dict[str, Any] = {"executors": {}, "comparator": None}
    try:
        # Version establishes each configured executable's effective identity before any task packet.
        for executor in manifest["executors"]:
            workspace = scratch.fresh("version")
            response, _ = call([*routes[executor["name"]]["argv"], "version"],
                               harness_environment(workspace / "home"),
                               executor["limits"]["timeout_seconds"], executor["limits"]["output_bytes"],
                               owned_directory(workspace / "cwd"))
            identities["executors"][executor["name"]] = attest_executor(response, executor)
            scratch.discard(workspace)
        workspace = scratch.fresh("version")
        response, _ = call([*comparator_route["argv"], "version"], harness_environment(workspace / "home"),
                           manifest["comparator"]["timeout_seconds"], MAX_OUTPUT_BYTES, owned_directory(workspace / "cwd"))
        identities["comparator"] = attest_comparator(response, manifest["comparator"])
        scratch.discard(workspace)
        seal_command_programs(run_root, suite, output)
        trial_records: list[dict[str, Any]] = []
        comparisons: list[dict[str, Any]] = []
        behavior, activation = split_cases(suite)
        for executor in manifest["executors"]:
            route = routes[executor["name"]]
            for case in behavior:
                for repetition in range(manifest["trials_per_arm"]):
                    arms = ("control", "candidate") if repetition % 2 == 0 else ("candidate", "control")
                    records: dict[str, dict[str, Any]] = {}
                    for treatment in arms:
                        record = run_trial(run_root, output, scratch, sealed, executor, route, case, treatment, repetition)
                        trial_records.append(record); records[treatment] = record
                    pair = pair_id(manifest, executor, case, repetition)
                    comparison = run_comparison(output, scratch, pair, executor, case, records["control"],
                                                records["candidate"], suite, manifest, comparator_route,
                                                identities["comparator"])
                    comparisons.append(comparison)
                    write_json(output / "comparisons" / f"{pair.removeprefix('sha256:')}.json", comparison)
            for case in activation:
                for repetition in range(manifest["trials_per_arm"]):
                    trial_records.append(run_trial(run_root, output, scratch, sealed, executor, route, case,
                                                   "candidate", repetition))
        try:
            recheck_input(run_root, sealed["inventory"], "result sealing")
            input_recheck = "unchanged"
        except HarnessError as error:
            input_recheck = str(error)
    finally:
        scratch.remove()
    seal_result(output, sealed, trial_records, comparisons, identities, audit, input_recheck)
    print(json.dumps({"state": read_json(output / "manifest.json")["state"],
                      "result": str(output / "manifest.json")}, sort_keys=True))
    return 0


def verify(args: argparse.Namespace) -> int:
    result = Path(args.result).resolve()
    manifest = read_json(result / "manifest.json")
    require_keys(manifest, {
        "schema_version", "kind", "state", "collection_state", "executor_states",
        "input_run_id", "invocation_nonce", "harness_version",
        "harness_executable_sha256", "profile", "candidate_id", "suite_id", "grader_set_id",
        "trials", "pairs", "executor_identities", "comparator_identity", "producer_audit",
        "file_inventory", "result_id",
    }, "result manifest")
    if manifest["schema_version"] != CONTRACT_VERSION or manifest["kind"] != "skill_evaluation_result":
        raise HarnessError("unsupported result bundle")
    expected = dict(manifest); result_id = expected.pop("result_id")
    if result_id != sha(expected):
        raise HarnessError("result manifest identity mismatch")
    if sha_bytes(Path(__file__).read_bytes()) != manifest["harness_executable_sha256"]:
        raise HarnessError("producer executable identity mismatch")
    require_keys(manifest["producer_audit"], {"routing_config_sha256", "executor_argv_sha256",
                                              "comparator_argv_sha256", "environment_sha256"}, "producer audit")
    verify_inventory(result, manifest["file_inventory"], "result file_inventory", MAX_RESULT_FILES)
    sealed = read_json(result / "sealed-input.json")
    require_keys(sealed, {"schema_version", "run_manifest", "suite", "candidate_inventory"}, "sealed input")
    if sealed["schema_version"] != CONTRACT_VERSION:
        raise HarnessError("unsupported sealed input")
    run_manifest, suite = sealed["run_manifest"], sealed["suite"]
    if not isinstance(run_manifest, dict) or run_manifest.get("invocation_nonce") != manifest["invocation_nonce"]:
        raise HarnessError("sealed input nonce mismatch")
    validate_executors(run_manifest["executors"])
    validate_comparator(run_manifest["comparator"])
    normalize_inventory(run_manifest["file_inventory"], "sealed run file_inventory", MAX_INPUT_FILES)
    projection = normalize_inventory(sealed["candidate_inventory"], "sealed candidate_inventory", MAX_INPUT_FILES)
    if sha(suite) != run_manifest["suite_id"] or run_manifest["suite_id"] != manifest["suite_id"]:
        raise HarnessError("sealed suite identity mismatch")
    if sha(projection) != run_manifest["candidate_id"] or run_manifest["candidate_id"] != manifest["candidate_id"]:
        raise HarnessError("sealed candidate identity mismatch")
    if run_manifest["grader_set_id"] != manifest["grader_set_id"]:
        raise HarnessError("sealed grader set identity mismatch")
    derived = derive_run_id(run_manifest)
    if run_manifest["run_id"] != derived or manifest["input_run_id"] != derived:
        raise HarnessError("result does not bind the canonical sealed run identity")
    if manifest["profile"] != run_manifest["profile"]:
        raise HarnessError("result profile differs from the sealed run")
    validate_suite(suite, run_manifest, run_manifest["file_inventory"])
    if args.nonce and args.nonce != manifest["invocation_nonce"]:
        raise HarnessError("caller nonce mismatch")
    expected_trials, expected_pairs, _ = plan_matrix(run_manifest, suite)
    if sorted(manifest["trials"]) != sorted(expected_trials):
        raise HarnessError("result trial set differs from the sealed trial matrix")
    if sorted(manifest["pairs"]) != sorted(expected_pairs):
        raise HarnessError("result pair set differs from the sealed pair matrix")
    graders = {item["id"]: item for item in suite["graders"]}
    cases = {item["id"]: item for item in suite["cases"]}
    skill_digest = next(entry["sha256"] for entry in projection if entry["path"] == "SKILL.md")
    scratch = Scratch(Path(args.scratch), [result])
    try:
        records = [verify_trial(result, run_manifest, graders, cases, skill_digest, scratch, trial)
                   for trial in expected_trials]
        records_by_id = {record["trial_id"]: record for record in records}
        comparisons = [
            verify_comparison(
                result,
                manifest,
                run_manifest,
                suite,
                cases,
                records_by_id,
                pair,
            )
            for pair in expected_pairs
        ]
    finally:
        scratch.remove()
    recorded = read_json(result / "aggregate.json")
    require_keys(recorded, {"schema_version", "authoritative", "executors", "infrastructure"}, "aggregate")
    require_keys(
        recorded["infrastructure"],
        {
            "input_recheck",
            "infrastructure_errors",
            "cleanup_failures",
            "shared_safety_failures",
            "required_state",
            "collection_state",
            "executor_states",
        },
        "aggregate infrastructure",
    )
    input_recheck = text(recorded["infrastructure"]["input_recheck"], "aggregate input_recheck")
    infrastructure = infrastructure_state(run_manifest, records, input_recheck)
    if build_aggregate(run_manifest, records, comparisons, infrastructure) != recorded:
        raise HarnessError("aggregate.json does not match the recomputed trial and comparison evidence")
    if (
        manifest["state"] != infrastructure["required_state"]
        or manifest["collection_state"] != infrastructure["collection_state"]
        or manifest["executor_states"] != infrastructure["executor_states"]
    ):
        raise HarnessError("result completion state contradicts recorded infrastructure")
    verify_inventory(result, manifest["file_inventory"], "result file_inventory", MAX_RESULT_FILES)
    print(json.dumps({"ok": True, "state": manifest["state"], "result_id": manifest["result_id"]}, sort_keys=True))
    return 0


def verify_trial(result: Path, run_manifest: dict[str, Any], graders: dict[str, Any],
                 cases: dict[str, Any], skill_digest: str, scratch: Scratch, trial: str) -> dict[str, Any]:
    root = result / "trials" / trial.removeprefix("sha256:")
    if not root.is_dir() or root.is_symlink():
        raise HarnessError(f"missing trial directory for {trial}")
    record = read_json(root / "result.json")
    for field in ("trial_id", "executor", "model", "case_id", "case_class", "treatment", "repetition",
                  "status", "skill_load_proved", "errors", "authoritative"):
        if field not in record:
            raise HarnessError(f"trial record {trial} is missing {field}")
    if record["trial_id"] != trial:
        raise HarnessError(f"trial record identity mismatch for {trial}")
    if record["status"] not in TRIAL_STATUSES:
        raise HarnessError(f"unsupported trial status for {trial}")
    case = cases.get(record["case_id"])
    if case is None or case["class"] != record["case_class"]:
        raise HarnessError(f"trial {trial} references an unsealed case")
    if trial != trial_id(run_manifest, next(item for item in run_manifest["executors"] if item["name"] == record["executor"]),
                         case, record["treatment"], record["repetition"]):
        raise HarnessError(f"trial {trial} identity does not derive from its own record")
    if record["status"] == "inconclusive":
        if not (record.get("infrastructure_error") or record.get("cleanup_failed")) or not record["errors"]:
            raise HarnessError(f"inconclusive trial {trial} records no infrastructure failure")
        return record
    events = trace_from(root / "trace.json")
    proved, _ = proof(events, record["treatment"], run_manifest["candidate_id"], skill_digest, record["case_class"])
    if proved != bool(record["skill_load_proved"]):
        raise HarnessError(f"recorded skill-load proof for {trial} contradicts its trace")
    if not proved and record["status"] not in {"invalid", "regression"}:
        raise HarnessError(f"trial {trial} scored an unproved skill load")
    if proved and record["status"] in {"invalid", "regression"}:
        raise HarnessError(f"trial {trial} recorded an unexplained invalid status")
    grade_path = root / "grader-results.json"
    if not grade_path.is_file():
        raise HarnessError(f"scored trial {trial} has no grader results")
    grades = read_json(grade_path)
    require_keys(grades, {"schema_version", "results"}, "grader results")
    recomputed = [grade(graders[item], final_text(events), events, root / "artifacts", result / "graders", scratch)
                  for item in case["graders"]]
    if recomputed != grades["results"]:
        raise HarnessError(f"falsified deterministic grader result for {trial}")
    deterministic_pass = all(item["passed"] for item in recomputed)
    if record["status"] in {"pass", "fail"} and (record["status"] == "pass") != deterministic_pass:
        raise HarnessError(f"trial {trial} status contradicts its deterministic grades")
    return record


def pair_context(
    run_manifest: dict[str, Any],
    suite: dict[str, Any],
    pair: str,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    behavior, _ = split_cases(suite)
    for executor in run_manifest["executors"]:
        for case in behavior:
            for repetition in range(run_manifest["trials_per_arm"]):
                if pair_id(run_manifest, executor, case, repetition) == pair:
                    return executor, case, repetition
    raise HarnessError(f"comparison {pair} is not part of the sealed pair matrix")


def verify_comparison(
    result: Path,
    manifest: dict[str, Any],
    run_manifest: dict[str, Any],
    suite: dict[str, Any],
    cases: dict[str, Any],
    records: dict[str, dict[str, Any]],
    pair: str,
) -> dict[str, Any]:
    path = result / "comparisons" / f"{pair.removeprefix('sha256:')}.json"
    if not path.is_file() or path.is_symlink():
        raise HarnessError(f"missing comparison record for {pair}")
    comparison = read_json(path)
    for field in ("pair_id", "executor", "case_id", "case_class", "status", "comparator", "errors"):
        if field not in comparison:
            raise HarnessError(f"comparison {pair} is missing {field}")
    if comparison["pair_id"] != pair or comparison["status"] not in COMPARISON_STATUSES:
        raise HarnessError(f"comparison {pair} identity or status invalid")
    if comparison["comparator"] != manifest["comparator_identity"]:
        raise HarnessError(f"comparison {pair} records a different comparator identity")
    executor, case, repetition = pair_context(run_manifest, suite, pair)
    if (
        comparison["executor"] != executor["name"]
        or comparison["case_id"] != case["id"]
        or comparison["case_class"] != case["class"]
    ):
        raise HarnessError(f"comparison {pair} metadata differs from the sealed pair")
    control_id = trial_id(run_manifest, executor, case, "control", repetition)
    candidate_id = trial_id(run_manifest, executor, case, "candidate", repetition)
    left, right = records[control_id], records[candidate_id]
    differences = fair_pair(left, right)
    if comparison["status"] == "complete":
        if differences:
            raise HarnessError(f"comparison {pair} used an invalid or unmatched arm")
        assignment = blind_assignment(manifest["invocation_nonce"], pair)
        if comparison.get("assignment") != assignment:
            raise HarnessError(f"comparison {pair} unblinding assignment is not derived from the sealed nonce")
        expected_packet = comparator_packet(result, case, assignment, left, right, suite)
        if expected_packet is None:
            raise HarnessError(f"comparison {pair} transferred a treatment-identifying packet")
        stem = pair.removeprefix("sha256:")
        packet_path = result / "comparisons" / f"{stem}.packet.json"
        response_path = result / "comparisons" / f"{stem}.response.json"
        if (
            not packet_path.is_file()
            or read_json(packet_path) != expected_packet
            or sha_bytes(packet_path.read_bytes()) != comparison.get("packet_sha256")
        ):
            raise HarnessError(f"comparison {pair} packet is missing, altered, or not derived from sealed trials")
        if (
            not response_path.is_file()
            or sha_bytes(response_path.read_bytes()) != comparison.get("response_sha256")
        ):
            raise HarnessError(f"comparison {pair} response evidence is missing or altered")
        response = read_json(response_path)
        require_keys(response, {"winner", "criteria", "evidence"}, "comparator output")
        if (
            response["winner"] not in {"A", "B", "tie"}
            or response["winner"] != comparison.get("winner")
            or not isinstance(response["criteria"], list)
            or not isinstance(response["evidence"], str)
        ):
            raise HarnessError(f"comparison {pair} response does not match its recorded winner")
    elif "assignment" in comparison:
        raise HarnessError(f"inconclusive comparison {pair} unblinded without durable comparator output")
    elif differences and not set(differences).issubset(set(comparison["errors"])):
        raise HarnessError(f"comparison {pair} omits its invalid-arm evidence")
    return comparison


def shadow_identity(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return identity(value, field)


def shadow_catalog_skills(catalog: Any) -> list[dict[str, Any]]:
    if not isinstance(catalog, list):
        raise HarnessError("shadow catalog_skills must be a list")
    names: set[str] = set()
    values: list[dict[str, Any]] = []
    for index, item in enumerate(catalog):
        require_keys(
            item,
            {"name", "catalog_skill_id", "skill_md_sha256", "path"},
            f"shadow catalog_skills[{index}]",
        )
        name = text(item["name"], f"shadow catalog_skills[{index}].name")
        if name in names:
            raise HarnessError("shadow catalog has duplicate skill name")
        names.add(name)
        values.append(
            {
                "name": name,
                "catalog_skill_id": identity(
                    item["catalog_skill_id"], f"shadow catalog_skills[{index}].catalog_skill_id"
                ),
                "skill_md_sha256": identity(
                    item["skill_md_sha256"], f"shadow catalog_skills[{index}].skill_md_sha256"
                ),
                "path": relative_component(item["path"], f"shadow catalog_skills[{index}].path"),
            }
        )
    return values


def shadow_validate_suite(suite: dict[str, Any], catalog: list[dict[str, Any]]) -> None:
    require_keys(
        suite,
        {
            "schema_version",
            "kind",
            "routing_mode",
            "environment",
            "environment_id",
            "grader_set_id",
            "graders",
            "cases",
        },
        "shadow suite",
    )
    if (
        suite["schema_version"] != SHADOW_CONTRACT_VERSION
        or suite["kind"] != "shadow_candidate_evaluation_suite"
        or suite["routing_mode"] not in {"catalog_plus_candidate", "candidate_only"}
        or not isinstance(suite["environment"], dict)
        or not suite["environment"]
    ):
        raise HarnessError("unsupported shadow suite")
    if suite["routing_mode"] == "catalog_plus_candidate" and not catalog:
        raise HarnessError("catalog routing requires a sealed approved catalog")
    if sha(suite["environment"]) != suite.get("environment_id"):
        raise HarnessError("shadow environment identity mismatch")
    if not isinstance(suite["graders"], list) or not suite["graders"]:
        raise HarnessError("shadow suite graders required")
    if sha(suite["graders"]) != suite["grader_set_id"]:
        raise HarnessError("shadow grader set identity mismatch")
    grader_map: dict[str, dict[str, Any]] = {}
    for item in suite["graders"]:
        require_keys(item, {"id", "type", "safety", "config"}, "shadow grader")
        grader_id = text(item["id"], "shadow grader id")
        if grader_id in grader_map or not isinstance(item["safety"], bool):
            raise HarnessError("invalid shadow grader")
        grader_map[grader_id] = item
    catalog_by_name = {item["name"]: item for item in catalog}
    classes: set[str] = set()
    identifiers: set[str] = set()
    tasks: set[str] = set()
    for index, case in enumerate(suite["cases"] if isinstance(suite["cases"], list) else []):
        require_keys(
            case,
            {
                "id",
                "class",
                "task_id",
                "prompt",
                "critical",
                "routing",
                "artifacts",
                "graders",
                "fixture",
            },
            f"shadow cases[{index}]",
        )
        case_id = text(case["id"], f"shadow cases[{index}].id")
        task = text(case["task_id"], f"shadow cases[{index}].task_id")
        if case_id in identifiers or task in tasks:
            raise HarnessError("shadow case and task identifiers must be unique")
        identifiers.add(case_id)
        tasks.add(task)
        case_class = text(case["class"], f"shadow cases[{index}].class")
        if case_class not in {
            "routing_positive",
            "routing_close_negative",
            "routing_unrelated",
            "routing_conflict",
            "task_value",
        }:
            raise HarnessError("unsupported shadow case class")
        classes.add(case_class)
        if not isinstance(case["critical"], bool):
            raise HarnessError("shadow case critical must be boolean")
        require_keys(case["routing"], {"candidate_load", "catalog_loads"}, "shadow case routing")
        if not isinstance(case["routing"]["candidate_load"], bool):
            raise HarnessError("shadow candidate_load must be boolean")
        expected_catalog = case["routing"]["catalog_loads"]
        if (
            not isinstance(expected_catalog, list)
            or len(set(expected_catalog)) != len(expected_catalog)
            or not all(isinstance(name, str) and name in catalog_by_name for name in expected_catalog)
        ):
            raise HarnessError("shadow case catalog loads must name sealed catalog skills")
        if suite["routing_mode"] == "candidate_only" and expected_catalog:
            raise HarnessError("candidate-only routing cannot declare catalog loads")
        if case_class == "routing_positive" and not case["routing"]["candidate_load"]:
            raise HarnessError("routing positive must require the candidate")
        if case_class in {"routing_close_negative", "routing_unrelated", "routing_conflict"} and case["routing"]["candidate_load"]:
            raise HarnessError("routing negative or conflict cannot require the candidate")
        if (
            case_class == "routing_conflict"
            and suite["routing_mode"] == "catalog_plus_candidate"
            and len(expected_catalog) != 1
        ):
            raise HarnessError("routing conflict must name exactly one selected catalog skill")
        if case_class == "task_value" and not case["routing"]["candidate_load"]:
            raise HarnessError("task-value candidate arm must require the candidate")
        if (
            not isinstance(case["artifacts"], list)
            or len(set(case["artifacts"])) != len(case["artifacts"])
            or not all(isinstance(path, str) for path in case["artifacts"])
            or not isinstance(case["graders"], list)
            or not case["graders"]
            or not set(case["graders"]) <= set(grader_map)
            or not any(grader_map[item]["safety"] for item in case["graders"])
        ):
            raise HarnessError("shadow case artifacts or graders are invalid")
        text(case["prompt"], "shadow case prompt")
        text(case["fixture"], "shadow case fixture")
    required = {
        "routing_positive",
        "routing_close_negative",
        "routing_unrelated",
        "routing_conflict",
        "task_value",
    }
    if classes != required:
        raise HarnessError("shadow suite must contain one or more of every routing and task-value class")


def shadow_validate_executors(executors: Any) -> list[dict[str, Any]]:
    if not isinstance(executors, list) or not executors:
        raise HarnessError("shadow executors must be a non-empty list")
    names: set[str] = set()
    normalized: list[dict[str, Any]] = []
    required = {
        *EXECUTOR_IDENTITY_KEYS,
        "name",
        "real_backend",
        "real_backend_source",
    }
    for index, executor in enumerate(executors):
        require_keys(executor, required, f"shadow executors[{index}]")
        name = text(executor["name"], f"shadow executors[{index}].name")
        if name in names:
            raise HarnessError("duplicate shadow executor")
        names.add(name)
        if not isinstance(executor["real_backend"], bool):
            raise HarnessError("shadow real_backend must be boolean")
        text(executor["real_backend_source"], "shadow real_backend_source")
        base = {
            key: executor[key]
            for key in EXECUTOR_IDENTITY_KEYS
            if key != "limits"
        }
        base["limits"] = {
            key: executor["limits"][key]
            for key in ("timeout_seconds", "token_budget", "output_bytes")
            if key in executor["limits"]
        }
        validate_executors([{**base, "name": name, "requirement": "required"}])
        require_keys(
            executor["limits"],
            {"timeout_seconds", "token_budget", "turn_budget", "tool_budget", "output_bytes"},
            "shadow executor limits",
        )
        for field, maximum in (
            ("timeout_seconds", 600),
            ("token_budget", 1_000_000),
            ("turn_budget", 100_000),
            ("tool_budget", 100_000),
            ("output_bytes", MAX_OUTPUT_BYTES),
        ):
            integer(executor["limits"][field], f"shadow executor {field}", 1, maximum)
        normalized.append(executor)
    return normalized


def shadow_trial_id(manifest: dict[str, Any], executor: dict[str, Any], case: dict[str, Any],
                    treatment: str) -> str:
    return sha(
        {
            "run_id": manifest["run_id"],
            "executor": executor["name"],
            "case_id": case["id"],
            "treatment": treatment,
        }
    )


def shadow_plan(manifest: dict[str, Any], suite: dict[str, Any]) -> list[str]:
    identifiers: list[str] = []
    for executor in manifest["executors"]:
        for case in suite["cases"]:
            treatments = ("control", "candidate") if case["class"] == "task_value" else ("candidate",)
            identifiers.extend(shadow_trial_id(manifest, executor, case, treatment) for treatment in treatments)
    if len(identifiers) != len(set(identifiers)):
        raise HarnessError("shadow trial matrix is ambiguous")
    return identifiers


def shadow_loads(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event in events:
        if event["kind"] == "authority_request":
            raise HarnessError("evaluation evidence cannot request lifecycle authority")
        if event["kind"] != "skill_load":
            continue
        data = event["data"]
        require_keys(data, {"candidate_id", "catalog_skill_id", "skill_md_sha256", "path", "non_builtin"},
                     "shadow skill load")
        if not isinstance(data["non_builtin"], bool):
            raise HarnessError("shadow skill load non_builtin must be boolean")
        result.append(dict(data))
    return result


def shadow_usage(events: list[dict[str, Any]], executor: dict[str, Any]) -> dict[str, int]:
    values = [event["data"] for event in events if event["kind"] == "usage"]
    if len(values) != 1:
        raise HarnessError("normalized trace must contain exactly one usage event")
    usage = values[0]
    require_keys(
        usage,
        {"turns", "input_tokens", "output_tokens", "total_tokens", "tool_calls"},
        "normalized usage",
    )
    result = {key: integer(usage[key], f"normalized usage.{key}", 0, 1_000_000) for key in usage}
    if result["total_tokens"] != result["input_tokens"] + result["output_tokens"]:
        raise HarnessError("normalized usage total_tokens must equal input plus output tokens")
    limits = executor["limits"]
    if (
        result["total_tokens"] > limits["token_budget"]
        or result["turns"] > limits["turn_budget"]
        or result["tool_calls"] > limits["tool_budget"]
    ):
        raise HarnessError("normalized usage exceeds sealed executor budget")
    return result


def shadow_route_proof(events: list[dict[str, Any]], case: dict[str, Any], treatment: str,
                       manifest: dict[str, Any], candidate_skill_sha: str) -> tuple[bool, str | None, list[dict[str, Any]]]:
    loads = shadow_loads(events)
    if treatment == "control":
        candidate_expected = False
    else:
        candidate_expected = case["routing"]["candidate_load"]
    expected_catalog = [] if manifest["routing_mode"] == "candidate_only" else case["routing"]["catalog_loads"]
    catalog = {item["name"]: item for item in manifest["catalog_skills"]}
    candidate = [
        load for load in loads
        if load["candidate_id"] == manifest["candidate_id"]
        and load["catalog_skill_id"] is None
        and load["skill_md_sha256"] == candidate_skill_sha
        and load["path"] == "candidate/SKILL.md"
        and load["non_builtin"] is True
    ]
    other_candidate = [
        load for load in loads
        if load["candidate_id"] is not None
        and load not in candidate
        and load["non_builtin"] is True
    ]
    actual_catalog: list[str] = []
    for load in loads:
        if not load["non_builtin"] or load in candidate:
            continue
        matched = next(
            (
                name for name, item in catalog.items()
                if load["candidate_id"] is None
                and load["catalog_skill_id"] == item["catalog_skill_id"]
                and load["skill_md_sha256"] == item["skill_md_sha256"]
                and load["path"] == item["path"]
            ),
            None,
        )
        if matched is None:
            return False, "unsealed, wrong, or ambiguous non-built-in skill load", loads
        actual_catalog.append(matched)
    if other_candidate:
        return False, "wrong candidate identity was loaded", loads
    if candidate_expected != (len(candidate) == 1):
        return False, "candidate exact skill-load proof is missing, unexpected, or ambiguous", loads
    if len(candidate) > 1 or (
        case["class"] != "task_value"
        and sorted(actual_catalog) != sorted(expected_catalog)
    ):
        return False, "sealed catalog selection differs from the declared routing case", loads
    return True, None, loads


def shadow_attest(response: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    keys = set(EXECUTOR_IDENTITY_KEYS) | {"real_backend", "real_backend_source"}
    if set(response) != keys:
        raise HarnessError("shadow executor identity response has wrong keys")
    for key in keys:
        if response[key] != expected[key]:
            raise HarnessError(f"shadow execution identity mismatch: {key}")
    return response


def shadow_run_trial(run: Path, result: Path, scratch: Scratch, manifest: dict[str, Any],
                     suite: dict[str, Any], executor: dict[str, Any], route: dict[str, Any],
                     case: dict[str, Any], treatment: str) -> dict[str, Any]:
    trial = shadow_trial_id(manifest, executor, case, treatment)
    root = result / "trials" / trial.removeprefix("sha256:")
    home, workspace, artifacts = root / "home", root / "workspace", root / "artifacts"
    raw, trace = root / "raw.jsonl", root / "trace.json"
    for directory in (home, workspace, artifacts):
        directory.mkdir(parents=True, exist_ok=True)
    candidate_skill_sha = next(
        item["sha256"] for item in manifest["candidate_inventory"] if item["path"] == "SKILL.md"
    )
    spec = {
        "schema_version": SHADOW_CONTRACT_VERSION,
        "trial_id": trial,
        "case": case,
        "treatment": treatment,
        "candidate_id": manifest["candidate_id"],
        "catalog_id": manifest["catalog_id"],
        "suite_id": manifest["suite_id"],
        "environment_id": manifest["environment_id"],
        "executor": executor,
        "candidate_inventory": manifest["candidate_inventory"],
        "skill_md_sha256": candidate_skill_sha,
        "catalog_skills": manifest["catalog_skills"],
        "candidate_root": str(run / "candidate"),
        "catalog_root": str(run / "catalog") if manifest["catalog_id"] else None,
        "workspace": str(workspace),
        "raw": str(raw),
        "trace": str(trace),
        "artifacts": str(artifacts),
    }
    write_json(root / "trial.json", spec)
    record: dict[str, Any] = {
        "schema_version": SHADOW_CONTRACT_VERSION,
        "trial_id": trial,
        "candidate_id": manifest["candidate_id"],
        "catalog_id": manifest["catalog_id"],
        "suite_id": manifest["suite_id"],
        "environment_id": manifest["environment_id"],
        "harness_executable_sha256": manifest["harness_executable_sha256"],
        "executor_identity": {
            key: executor[key] for key in EXECUTOR_IDENTITY_KEYS | {"real_backend", "real_backend_source"}
        },
        "executor": executor["name"],
        "case_id": case["id"],
        "case_class": case["class"],
        "treatment": treatment,
        "status": "inconclusive",
        "success": False,
        "candidate_loaded": False,
        "skill_loads": [],
        "usage": None,
        "errors": [],
    }
    calls = scratch.fresh("shadow-trials")
    try:
        timeout, output_bytes = executor["limits"]["timeout_seconds"], executor["limits"]["output_bytes"]
        prepared, _ = call(
            [*route["argv"], "prepare", "--trial", str(root / "trial.json")],
            harness_environment(home),
            timeout,
            output_bytes,
            owned_directory(calls / "prepare"),
        )
        require_keys(prepared, {"prepared", "execution"}, "shadow prepared response")
        shadow_attest(prepared["execution"], executor)
        prepared_record = {
            "schema_version": SHADOW_CONTRACT_VERSION,
            "trial_id": trial,
            "adapter_prepared": prepared["prepared"],
            "execution": prepared["execution"],
        }
        prepared_record["prepared_digest"] = sha(prepared_record)
        write_json(root / "prepared.json", prepared_record)
        run_response, _ = call(
            [
                *route["argv"],
                "run",
                "--trial",
                str(root / "trial.json"),
                "--prepared",
                str(root / "prepared.json"),
                "--output",
                str(raw),
            ],
            harness_environment(home),
            timeout,
            output_bytes,
            owned_directory(calls / "run"),
        )
        require_keys(run_response, {"prepared_digest", "effective_execution", "completed"}, "shadow run response")
        if run_response["prepared_digest"] != prepared_record["prepared_digest"] or run_response["completed"] is not True:
            raise HarnessError("shadow run did not consume the sealed prepared record")
        shadow_attest(run_response["effective_execution"], executor)
        if not raw.is_file() or raw.is_symlink() or raw.stat().st_size > output_bytes:
            raise HarnessError("shadow adapter raw log is missing or exceeds its bound")
        normalized, _ = call(
            [*route["argv"], "normalize", "--raw", str(raw), "--trace", str(trace)],
            harness_environment(home),
            timeout,
            output_bytes,
            owned_directory(calls / "normalize"),
        )
        require_keys(normalized, {"raw_sha256", "trace_sha256"}, "shadow normalize response")
        if normalized["raw_sha256"] != sha_bytes(raw.read_bytes()) or normalized["trace_sha256"] != sha_bytes(trace.read_bytes()):
            raise HarnessError("shadow normalization digest mismatch")
        events = trace_from(trace)
        route_ok, route_error, loads = shadow_route_proof(
            events, case, treatment, manifest, candidate_skill_sha
        )
        try:
            usage = shadow_usage(events, executor)
        except HarnessError as error:
            if "exceeds sealed executor budget" in str(error):
                record["status"] = "regression"
            else:
                raise
            record["errors"].append(str(error))
            usage = None
        collected, _ = call(
            [*route["argv"], "collect", "--trial", str(root / "trial.json"), "--artifacts", str(artifacts)],
            harness_environment(home),
            timeout,
            output_bytes,
            owned_directory(calls / "collect"),
        )
        require_keys(collected, {"completed_workspace", "declared_artifacts"}, "shadow collect response")
        if collected["completed_workspace"] is not True:
            raise HarnessError("shadow artifact collection did not complete")
        inventory = regular_inventory(artifacts, None, MAX_INPUT_FILES)
        declared = {item.get("path") for item in collected["declared_artifacts"] if isinstance(item, dict)}
        if declared != set(case["artifacts"]) or {item["path"] for item in inventory} != declared:
            raise HarnessError("shadow artifact collection differs from the sealed declaration")
        grader_map = {item["id"]: item for item in suite["graders"]}
        grades = [
            grade(grader_map[grader], final_text(events), events, artifacts, None, None)
            for grader in case["graders"]
        ]
        success = all(item["passed"] for item in grades)
        record.update(
            {
                "success": success,
                "candidate_loaded": any(
                    load["candidate_id"] == manifest["candidate_id"] for load in loads
                ),
                "skill_loads": loads,
                "usage": usage,
                "raw_sha256": sha_bytes(raw.read_bytes()),
                "trace_sha256": sha_bytes(trace.read_bytes()),
                "grades": grades,
                "artifact_inventory": inventory,
            }
        )
        if record["status"] != "regression":
            if not route_ok:
                record["status"] = "regression"
                record["errors"].append(route_error or "shadow route proof failed")
            elif not success:
                record["status"] = "regression"
                record["errors"].append("deterministic task grader failed")
            else:
                record["status"] = "pass"
    except HarnessError as error:
        record["errors"].append(str(error))
        record["status"] = "inconclusive"
        record["infrastructure_error"] = True
    finally:
        scratch.discard(calls)
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(workspace, ignore_errors=True)
    write_json(root / "result.json", record)
    return record


def shadow_aggregate(manifest: dict[str, Any], suite: dict[str, Any],
                     records: list[dict[str, Any]]) -> dict[str, Any]:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_case.setdefault(record["case_id"], []).append(record)
    routing_cases: list[dict[str, Any]] = []
    task_pairs: list[dict[str, Any]] = []
    critical_failures: list[str] = []
    route_statuses: list[str] = []
    task_statuses: list[str] = []
    for case in suite["cases"]:
        rows = by_case.get(case["id"], [])
        if case["class"] == "task_value":
            for executor in manifest["executors"]:
                own = [row for row in rows if row["executor"] == executor["name"]]
                arms = {row["treatment"]: row for row in own}
                complete = set(arms) == {"control", "candidate"} and all(
                    arm["status"] == "pass" and arm["usage"] is not None for arm in arms.values()
                )
                status = "pass" if complete else (
                    "regression" if any(arm["status"] == "regression" for arm in arms.values()) else "inconclusive"
                )
                task_statuses.append(status)
                task_pairs.append(
                    {
                        "case_id": case["id"],
                        "scenario_id": case["id"],
                        "executor_model": executor["model"],
                        "status": status,
                        "arms": {
                            treatment: None if treatment not in arms else {
                                "success": arms[treatment]["success"],
                                **(arms[treatment]["usage"] if arms[treatment]["usage"] is not None else {}),
                                "tool_use": None if arms[treatment]["usage"] is None else arms[treatment]["usage"]["tool_calls"],
                            }
                            for treatment in ("control", "candidate")
                        },
                    }
                )
            continue
        for executor in manifest["executors"]:
            own = [row for row in rows if row["executor"] == executor["name"]]
            if len(own) != 1:
                status = "inconclusive"
                record = None
            else:
                record = own[0]
                status = record["status"]
            route_statuses.append(status)
            routing_cases.append(
                {
                    "case_id": case["id"],
                    "scenario_id": case["id"],
                    "class": case["class"],
                    "critical": case["critical"],
                    "executor_model": executor["model"],
                    "status": status,
                    "candidate_loaded": None if record is None else record["candidate_loaded"],
                    "skill_loads": [] if record is None else record["skill_loads"],
                    "expected_catalog_loads": case["routing"]["catalog_loads"],
                }
            )
            if case["critical"] and status != "pass":
                critical_failures.append(f"{executor['name']}:{case['id']}")
    routing_gate = (
        "regression" if "regression" in route_statuses else
        "inconclusive" if manifest["routing_mode"] == "candidate_only" or "inconclusive" in route_statuses else
        "pass"
    )
    task_gate = (
        "regression" if "regression" in task_statuses else
        "inconclusive" if "inconclusive" in task_statuses or not all(
            executor["real_backend"] for executor in manifest["executors"]
        ) else "pass"
    )
    status = (
        "regression" if "regression" in {routing_gate, task_gate} else
        "inconclusive" if "inconclusive" in {routing_gate, task_gate} else "pass"
    )
    def count(case_class: str, predicate: Any, label: str) -> dict[str, int]:
        values = [item for item in routing_cases if item["class"] == case_class]
        return {label: sum(bool(predicate(item)) for item in values), "total": len(values)}
    return {
        "schema_version": SHADOW_CONTRACT_VERSION,
        "candidate_id": manifest["candidate_id"],
        "catalog_id": manifest["catalog_id"],
        "suite_id": manifest["suite_id"],
        "environment_id": manifest["environment_id"],
        "harness_executable_sha256": manifest["harness_executable_sha256"],
        "executor_identities": [
            {
                "name": executor["name"],
                "model": executor["model"],
                "real_backend": executor["real_backend"],
                "real_backend_source": executor["real_backend_source"],
            }
            for executor in manifest["executors"]
        ],
        "routing_mode": manifest["routing_mode"],
        "routing_diagnostic": manifest["routing_mode"] == "candidate_only",
        "routing_gate": routing_gate,
        "task_value_gate": task_gate,
        "status": status,
        "routing": {
            "positive_recall": count(
                "routing_positive",
                lambda item: item["candidate_loaded"] is True and item["status"] == "pass",
                "loaded",
            ),
            "close_negative_false_load": count(
                "routing_close_negative",
                lambda item: item["candidate_loaded"] is True,
                "false_loads",
            ),
            "unrelated_false_load": count(
                "routing_unrelated",
                lambda item: item["candidate_loaded"] is True,
                "false_loads",
            ),
            "conflict_selection": count(
                "routing_conflict",
                lambda item: item["status"] == "pass",
                "selected_expected",
            ),
            "critical_failures": critical_failures,
            "per_case": routing_cases,
        },
        "task_value": {"pairs": task_pairs},
    }


def shadow_load_manifest(run: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = read_json(run / "manifest.json")
    require_keys(
        manifest,
        {
            "schema_version",
            "kind",
            "run_id",
            "invocation_nonce",
            "candidate_id",
            "candidate_inventory",
            "catalog_id",
            "catalog_inventory",
            "catalog_skills",
            "suite_id",
            "environment_id",
            "routing_mode",
            "executors",
            "routing_id",
            "harness_executable_sha256",
            "file_inventory",
        },
        "shadow run manifest",
    )
    if manifest["schema_version"] != SHADOW_CONTRACT_VERSION or manifest["kind"] != "shadow_candidate_evaluation_run":
        raise HarnessError("unsupported shadow run manifest")
    for field in (
        "run_id",
        "candidate_id",
        "suite_id",
        "environment_id",
        "routing_id",
        "harness_executable_sha256",
    ):
        identity(manifest[field], field)
    shadow_identity(manifest["catalog_id"], "catalog_id")
    if sha_bytes(Path(__file__).read_bytes()) != manifest["harness_executable_sha256"]:
        raise HarnessError("shadow harness executable digest mismatch")
    candidate = normalize_inventory(manifest["candidate_inventory"], "shadow candidate inventory", MAX_INPUT_FILES)
    catalog_inventory = manifest["catalog_inventory"]
    if manifest["catalog_id"] is None:
        if catalog_inventory != [] or manifest["catalog_skills"] != []:
            raise HarnessError("candidate-only shadow manifest cannot carry a catalog")
    elif (
        normalize_inventory(catalog_inventory, "shadow catalog inventory", MAX_INPUT_FILES)
        != catalog_inventory
        or sha(catalog_inventory) != manifest["catalog_id"]
    ):
        raise HarnessError("shadow catalog identity mismatch")
    catalog = shadow_catalog_skills(manifest["catalog_skills"])
    suite = read_json(run / "suite.json")
    if sha(suite) != manifest["suite_id"] or suite.get("environment_id") != manifest["environment_id"]:
        raise HarnessError("shadow suite or environment identity mismatch")
    shadow_validate_suite(suite, catalog)
    executors = shadow_validate_executors(manifest["executors"])
    if executors != manifest["executors"] or suite["routing_mode"] != manifest["routing_mode"]:
        raise HarnessError("shadow executor or routing mode mismatch")
    expected_run = sha(
        {
            key: manifest[key]
            for key in (
                "schema_version",
                "kind",
                "candidate_id",
                "candidate_inventory",
                "catalog_id",
                "catalog_inventory",
                "catalog_skills",
                "suite_id",
                "environment_id",
                "routing_mode",
                "executors",
                "routing_id",
                "harness_executable_sha256",
                "file_inventory",
            )
        }
    )
    if manifest["run_id"] != expected_run:
        raise HarnessError("shadow run_id is not canonical")
    if regular_inventory(run, {"manifest.json"}, MAX_INPUT_FILES) != manifest["file_inventory"]:
        raise HarnessError("shadow sealed input inventory changed")
    actual_candidate = [
        {**item, "path": item["path"].removeprefix("candidate/")}
        for item in manifest["file_inventory"] if item["path"].startswith("candidate/")
    ]
    actual_catalog = [
        {**item, "path": item["path"].removeprefix("catalog/")}
        for item in manifest["file_inventory"] if item["path"].startswith("catalog/")
    ]
    if actual_candidate != candidate or actual_catalog != catalog_inventory:
        raise HarnessError("shadow candidate or catalog inventory is stale")
    return manifest, suite


def shadow_run(args: argparse.Namespace) -> int:
    run_root, output, routing_path = Path(args.input).resolve(), Path(args.output).resolve(), Path(args.routing).resolve()
    if not run_root.is_dir() or run_root.is_symlink() or not output.is_dir() or output.is_symlink() or any(output.iterdir()):
        raise HarnessError("shadow run and output directories must be real and output empty")
    manifest, suite = shadow_load_manifest(run_root)
    routing_value = read_json(routing_path)
    require_keys(routing_value, {"schema_version", "kind", "executors"}, "shadow routing")
    if routing_value["schema_version"] != SHADOW_CONTRACT_VERSION or routing_value["kind"] != "shadow_candidate_evaluation_routing":
        raise HarnessError("unsupported shadow routing")
    if sha(routing_value) != manifest["routing_id"]:
        raise HarnessError("shadow routing changes an exact executor route")
    routes: dict[str, dict[str, Any]] = {}
    for route in routing_value["executors"]:
        require_keys(route, {"name", "adapter_id", "adapter_executable_sha256", "argv"}, "shadow executor route")
        name = text(route["name"], "shadow route name")
        executable = Path(route["argv"][0]) if isinstance(route["argv"], list) and route["argv"] else None
        expected = next((item for item in manifest["executors"] if item["name"] == name), None)
        if (
            name in routes
            or expected is None
            or executable is None
            or not executable.is_file()
            or executable.is_symlink()
            or route["adapter_id"] != expected["adapter_id"]
            or route["adapter_executable_sha256"] != expected["adapter_executable_sha256"]
            or sha_bytes(executable.read_bytes()) != expected["adapter_executable_sha256"]
            or not all(isinstance(item, str) and item for item in route["argv"])
        ):
            raise HarnessError("shadow routing changes an exact executor route")
        routes[name] = route
    if set(routes) != {item["name"] for item in manifest["executors"]}:
        raise HarnessError("shadow routing executor set differs from manifest")
    scratch = Scratch(Path(args.scratch), [run_root, output])
    try:
        identities: dict[str, dict[str, Any]] = {}
        for executor in manifest["executors"]:
            workspace = scratch.fresh("shadow-version")
            response, _ = call(
                [*routes[executor["name"]]["argv"], "version"],
                harness_environment(workspace / "home"),
                executor["limits"]["timeout_seconds"],
                executor["limits"]["output_bytes"],
                owned_directory(workspace / "cwd"),
            )
            identities[executor["name"]] = shadow_attest(response, executor)
            scratch.discard(workspace)
        records: list[dict[str, Any]] = []
        for executor in manifest["executors"]:
            route = routes[executor["name"]]
            for case in suite["cases"]:
                for treatment in (("control", "candidate") if case["class"] == "task_value" else ("candidate",)):
                    records.append(
                        shadow_run_trial(run_root, output, scratch, manifest, suite, executor, route, case, treatment)
                    )
    finally:
        scratch.remove()
    aggregate = shadow_aggregate(manifest, suite, records)
    write_json(output / "aggregate.json", aggregate)
    write_json(output / "sealed-input.json", {"manifest": manifest, "suite": suite})
    inventory = regular_inventory(output, {"manifest.json"}, MAX_RESULT_FILES)
    result = {
        "schema_version": SHADOW_CONTRACT_VERSION,
        "kind": "shadow_candidate_evaluation_result",
        "run_id": manifest["run_id"],
        "candidate_id": manifest["candidate_id"],
        "catalog_id": manifest["catalog_id"],
        "suite_id": manifest["suite_id"],
        "environment_id": manifest["environment_id"],
        "harness_executable_sha256": manifest["harness_executable_sha256"],
        "routing_mode": manifest["routing_mode"],
        "routing_id": manifest["routing_id"],
        "executor_identities": identities,
        "trials": shadow_plan(manifest, suite),
        "aggregate": aggregate,
        "file_inventory": inventory,
    }
    result["result_id"] = sha(result)
    write_json(output / "manifest.json", result)
    print(json.dumps({"status": aggregate["status"], "result": str(output / "manifest.json")}, sort_keys=True))
    return 0


def shadow_verify(args: argparse.Namespace) -> int:
    result = Path(args.result).resolve()
    manifest = read_json(result / "manifest.json")
    require_keys(
        manifest,
        {
            "schema_version",
            "kind",
            "run_id",
            "candidate_id",
            "catalog_id",
            "suite_id",
            "environment_id",
            "harness_executable_sha256",
            "routing_mode",
            "routing_id",
            "executor_identities",
            "trials",
            "aggregate",
            "file_inventory",
            "result_id",
        },
        "shadow result manifest",
    )
    expected = dict(manifest)
    result_id = expected.pop("result_id")
    if (
        manifest["schema_version"] != SHADOW_CONTRACT_VERSION
        or manifest["kind"] != "shadow_candidate_evaluation_result"
        or result_id != sha(expected)
        or sha_bytes(Path(__file__).read_bytes()) != manifest["harness_executable_sha256"]
        or regular_inventory(result, {"manifest.json"}, MAX_RESULT_FILES) != manifest["file_inventory"]
    ):
        raise HarnessError("shadow result identity or inventory is invalid")
    sealed = read_json(result / "sealed-input.json")
    require_keys(sealed, {"manifest", "suite"}, "shadow sealed input")
    run_manifest = sealed["manifest"]
    if not isinstance(run_manifest, dict) or not isinstance(sealed["suite"], dict):
        raise HarnessError("shadow sealed input is malformed")
    for field in (
        "run_id",
        "candidate_id",
        "catalog_id",
        "suite_id",
        "environment_id",
        "harness_executable_sha256",
        "routing_mode",
        "routing_id",
    ):
        if manifest[field] != run_manifest.get(field):
            raise HarnessError("shadow result does not bind its sealed run identity")
    if (
        run_manifest.get("suite_id") != sha(sealed["suite"])
        or run_manifest.get("environment_id") != sealed["suite"].get("environment_id")
        or run_manifest.get("candidate_id") != sha(run_manifest.get("candidate_inventory"))
        or (
            run_manifest.get("catalog_id") is not None
            and run_manifest.get("catalog_id") != sha(run_manifest.get("catalog_inventory"))
        )
    ):
        raise HarnessError("shadow sealed candidate, catalog, suite, or environment is malformed")
    temporary = Path(args.scratch).resolve()
    if not temporary.is_dir() or temporary.is_symlink() or any(temporary.iterdir()):
        raise HarnessError("shadow verification scratch directory must be real and empty")
    # Reuse the manifest validator with an isolated, reconstructed sealed input.
    try:
        # The run directory is intentionally not available to a result verifier. Validate
        # every sealed object and recompute all evidence from the durable result instead.
        shadow_catalog_skills(run_manifest["catalog_skills"])
        shadow_validate_executors(run_manifest["executors"])
        shadow_validate_suite(sealed["suite"], run_manifest["catalog_skills"])
        if manifest["trials"] != shadow_plan(run_manifest, sealed["suite"]):
            raise HarnessError("shadow result trial matrix differs from sealed scenarios")
        records = []
        candidate_sha = next(
            item["sha256"] for item in run_manifest["candidate_inventory"] if item["path"] == "SKILL.md"
        )
        graders = {item["id"]: item for item in sealed["suite"]["graders"]}
        cases = {item["id"]: item for item in sealed["suite"]["cases"]}
        for trial in manifest["trials"]:
            root = result / "trials" / trial.removeprefix("sha256:")
            record = read_json(root / "result.json")
            case = cases.get(record.get("case_id"))
            executor = next(
                (
                    item
                    for item in run_manifest["executors"]
                    if item["name"] == record.get("executor")
                ),
                None,
            )
            if (
                case is None
                or executor is None
                or record.get("executor") != executor["name"]
                or trial != shadow_trial_id(run_manifest, executor, case, record.get("treatment"))
                or record.get("candidate_id") != run_manifest["candidate_id"]
                or record.get("catalog_id") != run_manifest["catalog_id"]
                or record.get("suite_id") != run_manifest["suite_id"]
                or record.get("environment_id") != run_manifest["environment_id"]
                or record.get("executor_identity") != {
                    key: executor[key] for key in EXECUTOR_IDENTITY_KEYS | {"real_backend", "real_backend_source"}
                }
            ):
                raise HarnessError("shadow trial identity is forged or stale")
            if record["status"] == "inconclusive":
                if not record.get("infrastructure_error") or not record.get("errors"):
                    raise HarnessError("shadow inconclusive trial lacks a failure reason")
                records.append(record)
                continue
            events = trace_from(root / "trace.json")
            proved, _, loads = shadow_route_proof(events, case, record["treatment"], run_manifest, candidate_sha)
            usage_error = None
            try:
                usage = shadow_usage(events, executor)
            except HarnessError as error:
                usage = None
                usage_error = str(error)
            recomputed = [
                grade(graders[item], final_text(events), events, root / "artifacts", None, None)
                for item in case["graders"]
            ]
            success = all(item["passed"] for item in recomputed)
            candidate_loaded = any(
                load["candidate_id"] == run_manifest["candidate_id"] for load in loads
            )
            if (
                record.get("skill_loads") != loads
                or record.get("usage") != usage
                or record.get("grades") != recomputed
                or record.get("success") != success
                or record.get("candidate_loaded") != candidate_loaded
            ):
                raise HarnessError("shadow trial evidence was falsified after execution")
            expected_status = "pass" if proved and usage_error is None and success else "regression"
            if record["status"] != expected_status:
                raise HarnessError("shadow trial status contradicts its durable evidence")
            records.append(record)
        aggregate = shadow_aggregate(run_manifest, sealed["suite"], records)
        if aggregate != read_json(result / "aggregate.json") or aggregate != manifest["aggregate"]:
            raise HarnessError("shadow aggregate is not derived from the sealed trial evidence")
    finally:
        if any(temporary.iterdir()):
            raise HarnessError("shadow verification scratch was not cleaned")
    print(json.dumps({"ok": True, "status": manifest["aggregate"]["status"], "result_id": result_id}, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    commands = value.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--input", required=True)
    run_parser.add_argument("--output", required=True)
    run_parser.add_argument("--routing", required=True)
    run_parser.add_argument("--scratch", required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--result", required=True)
    verify_parser.add_argument("--scratch", required=True)
    verify_parser.add_argument("--nonce")
    shadow_run_parser = commands.add_parser("shadow-run")
    shadow_run_parser.add_argument("--input", required=True)
    shadow_run_parser.add_argument("--output", required=True)
    shadow_run_parser.add_argument("--routing", required=True)
    shadow_run_parser.add_argument("--scratch", required=True)
    shadow_verify_parser = commands.add_parser("shadow-verify")
    shadow_verify_parser.add_argument("--result", required=True)
    shadow_verify_parser.add_argument("--scratch", required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "run":
            return run(args)
        if args.command == "verify":
            return verify(args)
        if args.command == "shadow-run":
            return shadow_run(args)
        return shadow_verify(args)
    except HarnessError as error:
        print(f"skill-evaluation-harness: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
