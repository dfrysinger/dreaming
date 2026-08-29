#!/usr/bin/env bash
# CHK-07 / CHK-11 / CHK-12 for the shadow evaluation authority and suite
# preparation stage: the candidate-blind packet refuses every prohibited field
# and source, computed execution authority matches what the stage can actually
# do, and one-skill catalog preparation refuses unavailable, ambiguous, stale,
# or tampered authority before any lifecycle mutation.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
TEST_ROOT="$REPO_ROOT/.test-work"
mkdir -p "$TEST_ROOT"
# shellcheck source=lib-test-work.sh
source "$SCRIPT_DIR/lib-test-work.sh"
prune_test_work "$TEST_ROOT" "shadow-evaluation-preparation" 2
TMP="$(mktemp -d "$TEST_ROOT/shadow-evaluation-preparation.XXXXXX")"
cleanup() {
  local rc=$?
  trap - EXIT
  finish_test_work "$rc" "$TMP" "shadow evaluation preparation" 1
  exit "$rc"
}
trap cleanup EXIT

SCRIPT_DIR="$SCRIPT_DIR" TMP="$TMP" python3 - <<'PY'
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(os.environ["SCRIPT_DIR"])
TMP = Path(os.environ["TMP"])
FAILURES: list[str] = []


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


prep = load("shadow_evaluation_preparation", "shadow_evaluation_preparation.py")
estate = load("dreaming_estate", "dreaming-estate.py")
evaluation = load("skill_evaluation", "skill-evaluation.py")
routing = load("profile_evaluation_routing", "profile_evaluation_routing.py")


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"ok   {name}")
    else:
        FAILURES.append(f"{name}{': ' + detail if detail else ''}")
        print(f"FAIL {name} {detail}")


def refuses(name: str, code: str, call) -> None:
    try:
        call()
    except (prep.ShadowPreparationError, evaluation.ShadowPacketError) as error:
        check(name, error.code == code, f"expected {code}, got {error.code}")
        return
    except Exception as error:  # noqa: BLE001
        check(name, False, f"expected {code}, raised {type(error).__name__}: {error}")
        return
    check(name, False, f"expected {code}, but the call was accepted")


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


INCUMBENT = """---
name: repo-release-notes
description: Draft release notes for a repository from merged pull requests and tags, grouping changes and calling out breaking changes.
---

# Repository release notes

Collect merged pull requests since the last tag and write the notes.
"""

CANDIDATE = """---
name: flaky-test-triage
description: Triage a flaky test by separating environment-dependent failures from genuine race conditions.
---

# Flaky test triage

1. Re-run the failing test in isolation and record the failure rate.
2. Compare failing and passing runs for shared mutable state.
"""


def build_estate(root: Path, names: list[str]) -> tuple[Path, dict]:
    estate_root = root / "estate"
    estate_root.mkdir(parents=True, exist_ok=True)
    instances = []
    for name in names:
        skill = estate_root / name
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(INCUMBENT.replace("repo-release-notes", name))
        files, inventory_sha256 = estate.skill_inventory(skill)
        instances.append(
            {
                "skill_name": name,
                "absolute_path": str(skill.resolve()),
                "inventory_sha256": inventory_sha256,
                "canonical_capability_id": digest({"root": "test", "name": name}),
                "files": files,
            }
        )
    snapshot = {"schema_version": 1, "physical_instances": instances}
    return estate_root, {**snapshot, "snapshot_sha256": digest(snapshot)}


def build_package(root: Path) -> tuple[Path, list[dict], str]:
    package = root / "package" / "flaky-test-triage"
    package.mkdir(parents=True, exist_ok=True)
    (package / "SKILL.md").write_text(CANDIDATE)
    files = [
        {
            "path": path.relative_to(package).as_posix(),
            "sha256": prep.file_sha256(path),
            "size": path.stat().st_size,
        }
        for path in sorted(package.rglob("*"))
        if path.is_file()
    ]
    return package, files, prep.candidate_package_identity(files)


def traces(name: str, count: int = 3) -> list[dict]:
    return [
        {
            "canonical_occurrence_id": digest({"occurrence": index}),
            "skill_load_trace": [
                {
                    "source_event_id": f"event-{index}",
                    "invoked_name": name,
                    "catalog_skill_name": name,
                    "event_sha256": digest({"event": index}),
                }
            ],
        }
        for index in range(count)
    ]


ALLOWANCES = {
    "max_evaluations_per_run": 1,
    "stage_seconds": 3000,
    "author_call_bound": 600,
    "author_doctor_bound": 60,
    "executor_call_bound": 600,
    "compile_bound": 120,
    "certify_bound": 300,
    "lifecycle_transition_bound": 60,
    "record_write_bound": 30,
    "lifecycle_read_bound": 30,
    "packet_build_bound": 60,
    "packet_validate_bound": 60,
    "package_file_ceiling": 64,
    "package_bytes_ceiling": 1048576,
    "catalog_file_ceiling": 64,
    "catalog_bytes_ceiling": 1048576,
    "prepare_throughput": 1048576,
    "hash_throughput": 1048576,
    "termination_grace": 10,
    "deadline_margin": 30,
}


def executors_document() -> list[dict]:
    limits = {
        "timeout_seconds": 600,
        "token_budget": 100000,
        "turn_budget": 40,
        "tool_budget": 200,
        "output_bytes": 1000000,
    }
    return [
        {
            "name": "shadow-executor",
            "model": "claude-sonnet-4.5",
            "adapter_id": digest({"adapter": "id"}),
            "adapter_version": 1,
            "adapter_executable_sha256": digest({"adapter": "executable"}),
            "cli_executable_sha256": digest({"cli": "executable"}),
            "cli_version": "1.0.0",
            "tool_policy_id": digest({"tool": "policy"}),
            "limits": limits,
            "sandbox_id": digest({"sandbox": "id"}),
            "real_backend": True,
            "real_backend_source": "native-copilot-cli model=claude-sonnet-4.5",
        }
    ]


# ---------------------------------------------------------------- CHK-12
work = TMP / "chk12"
estate_root, census = build_estate(work, ["repo-release-notes"])
package_dir, package_files, candidate_id = build_package(work)
target = prep.select_conflict_target(traces("repo-release-notes"), census)
check("chk12-target-is-the-loaded-catalog-skill", target["skill_name"] == "repo-release-notes")

catalog_dir = work / "scratch" / "catalog"
catalog = prep.materialize_catalog(
    target, catalog_dir, file_ceiling=64, bytes_ceiling=1048576
)
check("chk12-catalog-rehashes-to-census", catalog["inventory_sha256"] == target["inventory_sha256"])
check("chk12-catalog-holds-one-skill", len(list(catalog_dir.iterdir())) == 1)

refuses(
    "chk12-refuses-when-no-catalog-skill-loaded",
    "shadow-conflict-target-unavailable",
    lambda: prep.select_conflict_target(
        [
            {
                "canonical_occurrence_id": digest({"occurrence": 0}),
                "skill_load_trace": [
                    {
                        "source_event_id": "e",
                        "invoked_name": "unknown",
                        "catalog_skill_name": None,
                        "event_sha256": digest({"event": 0}),
                    }
                ],
            }
        ],
        census,
    ),
)
refuses(
    "chk12-refuses-target-absent-from-census",
    "shadow-conflict-target-ambiguous",
    lambda: prep.select_conflict_target(traces("not-in-the-census"), census),
)
duplicated = copy.deepcopy(census)
duplicated["physical_instances"].append(copy.deepcopy(duplicated["physical_instances"][0]))
duplicated["snapshot_sha256"] = digest(
    {key: value for key, value in duplicated.items() if key != "snapshot_sha256"}
)
refuses(
    "chk12-refuses-ambiguous-census-instance",
    "shadow-conflict-target-ambiguous",
    lambda: prep.select_conflict_target(traces("repo-release-notes"), duplicated),
)
tampered = copy.deepcopy(census)
tampered["physical_instances"][0]["skill_name"] = "repo-release-notes"
tampered["schema_version"] = 2
refuses(
    "chk12-refuses-uncorroborated-census-digest",
    "shadow-catalog-authority-unavailable",
    lambda: prep.select_conflict_target(traces("repo-release-notes"), tampered),
)
stale = copy.deepcopy(target)
(estate_root / "repo-release-notes" / "SKILL.md").write_text(INCUMBENT + "\ndrifted\n")
refuses(
    "chk12-refuses-stale-catalog-bytes",
    "shadow-catalog-snapshot-stale",
    lambda: prep.materialize_catalog(
        stale, work / "scratch" / "stale", file_ceiling=64, bytes_ceiling=1048576
    ),
)
refuses(
    "chk12-refuses-oversize-catalog",
    "preparation-oversize",
    lambda: prep.materialize_catalog(
        target, work / "scratch" / "small", file_ceiling=64, bytes_ceiling=1
    ),
)

package = prep.materialize_candidate(
    package_dir,
    package_files,
    candidate_id,
    work / "scratch" / "candidate",
    file_ceiling=64,
    bytes_ceiling=1048576,
)
check("chk12-candidate-rehashes-to-candidate-id", package["candidate_id"] == candidate_id)
refuses(
    "chk12-refuses-tampered-candidate-package",
    "shadow-candidate-package-tampered",
    lambda: prep.materialize_candidate(
        package_dir,
        package_files,
        digest({"not": "the candidate"}),
        work / "scratch" / "tampered",
        file_ceiling=64,
        bytes_ceiling=1048576,
    ),
)

# ---------------------------------------------------------------- CHK-07
lifecycle_id = "1cd202c1-70e2-5a12-9b32-4034761887dc"
executors = executors_document()
harness = SCRIPTS / "skill-evaluation-harness.py"
packet = evaluation.build_shadow_authoring_packet(
    lifecycle_id=lifecycle_id,
    candidate_id=candidate_id,
    skill_dir=Path(package["package_dir"]),
    catalog_dir=catalog_dir,
    executors=executors,
    harness=harness,
)
check("chk07-packet-key-set-is-closed", set(packet) == evaluation.SHADOW_AUTHORING_PACKET_KEYS)
check("chk07-packet-is-catalog-plus-candidate", packet["routing_mode"] == "catalog_plus_candidate")
check(
    "chk07-packet-carries-no-portfolio-state",
    "profile" not in json.dumps(packet).lower()
    and "occurrence" not in json.dumps(packet).lower()
    and "disposition" not in json.dumps(packet).lower(),
)
check(
    "chk07-packet-round-trips",
    evaluation.validate_shadow_authoring_packet(json.loads(json.dumps(packet)))["packet_id"]
    == packet["packet_id"],
)


def mutated(**changes) -> dict:
    value = copy.deepcopy(packet)
    value.update(changes)
    value["packet_id"] = evaluation.shadow_sha(
        {key: item for key, item in value.items() if key != "packet_id"}
    )
    return value


refuses(
    "chk07-refuses-unknown-field",
    "shadow-packet-unknown-field",
    lambda: evaluation.validate_shadow_authoring_packet({**packet, "extra": 1}),
)
refuses(
    "chk07-refuses-unbound-packet-id",
    "shadow-packet-schema-invalid",
    lambda: evaluation.validate_shadow_authoring_packet(
        {**packet, "packet_id": digest({"wrong": "id"})}
    ),
)
refuses(
    "chk07-refuses-template-drift",
    "shadow-packet-template-drift",
    lambda: evaluation.validate_shadow_authoring_packet(
        mutated(suite_template={"cases": packet["suite_template"]["cases"][:4]})
    ),
)
refuses(
    "chk07-refuses-candidate-mismatch",
    "shadow-packet-candidate-mismatch",
    lambda: evaluation.validate_shadow_authoring_packet(
        mutated(candidate_id=digest({"other": "candidate"}))
    ),
)
refuses(
    "chk07-refuses-absolute-path-leak",
    "shadow-packet-sensitive-value",
    lambda: evaluation.validate_shadow_authoring_packet(
        mutated(
            conflict_reference={
                **packet["conflict_reference"],
                "description": "/Users/someone/code/private/notes",
            }
        )
    ),
)
refuses(
    "chk07-refuses-undeclared-digest",
    "shadow-packet-sensitive-value",
    lambda: evaluation.validate_shadow_authoring_packet(
        mutated(
            conflict_reference={
                **packet["conflict_reference"],
                "description": f"see {digest({'unrelated': 'evidence'})} for context",
            }
        )
    ),
)
refuses(
    "chk07-refuses-uuid-identifier",
    "shadow-packet-sensitive-value",
    lambda: evaluation.validate_shadow_authoring_packet(
        mutated(
            conflict_reference={
                **packet["conflict_reference"],
                "description": "session 7f6d1b2c-1e2f-4a3b-9c8d-0e1f2a3b4c5d covers this",
            }
        )
    ),
)
refuses(
    "chk07-refuses-profile-identity",
    "shadow-packet-sensitive-value",
    lambda: evaluation.validate_shadow_authoring_packet(
        mutated(
            conflict_reference={
                **packet["conflict_reference"],
                "description": "the observed profile id selects this target",
            }
        )
    ),
)
refuses(
    "chk07-refuses-unattested-executor",
    "shadow-packet-executor-unattested",
    lambda: evaluation.build_shadow_authoring_packet(
        lifecycle_id=lifecycle_id,
        candidate_id=candidate_id,
        skill_dir=Path(package["package_dir"]),
        catalog_dir=catalog_dir,
        executors=[{**executors[0], "real_backend": False}],
        harness=harness,
    ),
)
refuses(
    "chk07-refuses-multiple-executors",
    "shadow-packet-executor-unattested",
    lambda: evaluation.build_shadow_authoring_packet(
        lifecycle_id=lifecycle_id,
        candidate_id=candidate_id,
        skill_dir=Path(package["package_dir"]),
        catalog_dir=catalog_dir,
        executors=[executors[0], {**executors[0], "name": "second"}],
        harness=harness,
    ),
)

two_skill = work / "scratch" / "two-skill"
(two_skill / "a").mkdir(parents=True)
(two_skill / "b").mkdir(parents=True)
(two_skill / "a" / "SKILL.md").write_text(INCUMBENT)
(two_skill / "b" / "SKILL.md").write_text(INCUMBENT)
refuses(
    "chk07-refuses-multi-skill-catalog-source",
    "shadow-packet-prohibited-source",
    lambda: evaluation.build_shadow_authoring_packet(
        lifecycle_id=lifecycle_id,
        candidate_id=candidate_id,
        skill_dir=Path(package["package_dir"]),
        catalog_dir=two_skill,
        executors=executors,
        harness=harness,
    ),
)

# The authored draft may contribute a task id and a prompt, and nothing else.
draft = {
    "schema_version": 1,
    "kind": "safe_evaluation_input_draft",
    "packet_id": packet["packet_id"],
    "candidate_id": packet["candidate_id"],
    "cases": [
        {
            "id": case["id"],
            "class": case["class"],
            "task_id": f"fixture-{case['id']}",
            "prompt": f"Fixture prompt {index}: perform the task and write SUCCESS.",
        }
        for index, case in enumerate(packet["suite_template"]["cases"])
    ],
}
suite = evaluation.assemble_shadow_suite(packet, draft)
check("chk07-assembled-suite-is-catalog-plus-candidate", suite["routing_mode"] == "catalog_plus_candidate")
check(
    "chk07-assembled-routing-comes-from-the-template",
    [case["routing"] for case in suite["cases"]]
    == [case["routing"] for case in packet["suite_template"]["cases"]],
)
refuses(
    "chk07-refuses-model-routing-override",
    "shadow-packet-template-drift",
    lambda: evaluation.assemble_shadow_suite(
        packet,
        {
            **draft,
            "cases": [{**case, "class": "task_value"} for case in draft["cases"]],
        },
    ),
)
refuses(
    "chk07-refuses-draft-bound-to-another-packet",
    "shadow-packet-schema-invalid",
    lambda: evaluation.assemble_shadow_suite(
        packet, {**draft, "packet_id": digest({"other": "packet"})}
    ),
)
refuses(
    "chk07-refuses-prompt-naming-the-candidate",
    "shadow-packet-sensitive-value",
    lambda: evaluation.assemble_shadow_suite(
        packet,
        {
            **draft,
            "cases": [
                {**case, "prompt": f"Use flaky test triage here, case {index}."}
                for index, case in enumerate(draft["cases"])
            ],
        },
    ),
)

suite_path = work / "suite.json"
suite_path.write_text(json.dumps(suite))
_inventory, skills = evaluation.shadow_catalog(catalog_dir)
accepted, suite_id = evaluation.shadow_suite(suite_path, skills)
check("chk07-suite-accepted-by-shadow-compile-contract", accepted["routing_mode"] == "catalog_plus_candidate")
check("chk07-suite-has-five-cases", len(accepted["cases"]) == 5 and suite_id.startswith("sha256:"))

# ---------------------------------------------------------------- CHK-11
ok, values = prep.allowance_authority({"shadow_evaluation": ALLOWANCES})
check("chk11-configured-allowances-are-authority", ok and len(values) == len(ALLOWANCES))
for missing in ("author_call_bound", "stage_seconds", "max_evaluations_per_run"):
    partial = {key: value for key, value in ALLOWANCES.items() if key != missing}
    unavailable, _ = prep.allowance_authority({"shadow_evaluation": partial})
    check(f"chk11-missing-{missing}-is-unconfigured", unavailable is False)
check(
    "chk11-absent-allowance-block-is-unconfigured",
    prep.allowance_authority({})[0] is False,
)
check(
    "chk11-zero-allowance-is-unconfigured",
    prep.allowance_authority(
        {"shadow_evaluation": {**ALLOWANCES, "max_evaluations_per_run": 0}}
    )[0]
    is False,
)
check(
    "chk11-boolean-allowance-is-unconfigured",
    prep.allowance_authority(
        {"shadow_evaluation": {**ALLOWANCES, "compile_bound": True}}
    )[0]
    is False,
)

complete = prep.execution_authority_facts(
    evaluator_configured=True,
    evaluator_healthy=True,
    evaluator_attested=True,
    suite_authority=True,
    authoring_authority_available=True,
    catalog_authority_available=True,
    candidate_package_available=True,
    allowances_configured=True,
)
check("chk11-fact-set-matches-the-derivation", set(complete) == routing.EXECUTION_AUTHORITY_KEYS)
derived = routing.derive_execution_authority(complete)
check("chk11-complete-authority-is-available", derived == {"available": True, "reasons": []})
for fact, reason in routing.EXECUTION_AUTHORITY_FACTS:
    single = routing.derive_execution_authority({**complete, fact: False})
    check(
        f"chk11-missing-{fact}-blocks-with-{reason}",
        single["available"] is False and single["reasons"] == [reason],
    )
check(
    "chk11-allowances-report-shadow-allowance-unconfigured",
    routing.derive_execution_authority({**complete, "allowances_configured": False})["reasons"]
    == ["shadow-allowance-unconfigured"],
)
for bad in (None, {}, {"evaluator_configured": True}, {**complete, "extra": True}, {**complete, "suite_authority": "yes"}):
    unknown = routing.derive_execution_authority(bad)
    check(
        "chk11-unrecognized-authority-is-fail-closed",
        unknown == {"available": False, "reasons": ["shadow-execution-authority-unknown"]},
        json.dumps(bad, default=str),
    )

# The stage never enters evaluating and never claims install authority here.
check(
    "chk11-preparation-module-never-transitions-lifecycle",
    "evaluating" not in (SCRIPTS / "shadow_evaluation_preparation.py").read_text(),
)

# ------------------------------------------------------- bounded wrapper
result = prep.bounded_call(
    [sys.executable, "-c", "print('bounded')"],
    timeout=30,
    termination_grace=5,
    code="shadow-preparation-timeout",
)
check("bounded-call-returns-child-output", result.returncode == 0 and "bounded" in result.stdout)
refuses(
    "bounded-call-terminates-an-overrunning-process-group",
    "shadow-preparation-timeout",
    lambda: prep.bounded_call(
        [
            sys.executable,
            "-c",
            "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);time.sleep(60)",
        ],
        timeout=2,
        termination_grace=2,
        code="shadow-preparation-timeout",
    ),
)
refuses(
    "bounded-call-refuses-an-unconfigured-bound",
    "shadow-allowance-unconfigured",
    lambda: prep.bounded_call(
        [sys.executable, "-c", "pass"], timeout=0, termination_grace=5, code="x"
    ),
)

if FAILURES:
    print(f"\n{len(FAILURES)} check(s) failed:")
    for failure in FAILURES:
        print(f"  - {failure}")
    sys.exit(1)
print("\nshadow evaluation preparation: all checks passed")
PY
