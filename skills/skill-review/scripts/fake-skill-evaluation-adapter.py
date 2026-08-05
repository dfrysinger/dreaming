#!/usr/bin/env python3
"""Deterministic adapter fixture for the trial-harness contract tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def load(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return value


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True))


def identity(path: str) -> dict[str, Any]:
    return load(path)


def event(kind: str, text: str = "", **data: Any) -> dict[str, Any]:
    return {"kind": kind, "text": text, "data": data}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", required=True)
    parser.add_argument("command", choices=("version", "prepare", "run", "normalize", "collect", "compare"))
    parser.add_argument("--trial")
    parser.add_argument("--prepared")
    parser.add_argument("--output")
    parser.add_argument("--raw")
    parser.add_argument("--trace")
    parser.add_argument("--artifacts")
    parser.add_argument("--packet")
    parser.add_argument("--mutate")
    parser.add_argument("--fixture")
    args = parser.parse_args()
    expected = identity(args.identity)
    if args.command == "version":
        emit(expected)
        return 0
    if args.command == "compare":
        load(args.packet)
        # The observed transport is recorded so blinding can be checked from the sealed bundle.
        observed = {"argv": sys.argv, "cwd": os.getcwd(), "home": os.environ.get("HOME", ""),
                    "environment": sorted(os.environ)}
        output = {"winner": "A", "criteria": [{"id": "quality", "score": 1}],
                  "evidence": json.dumps(observed, sort_keys=True)}
        Path(args.output).write_bytes(canonical(output) + b"\n")
        emit({"response_sha256": digest(Path(args.output).read_bytes())})
        return 0
    if args.command == "normalize":
        raw = Path(args.raw)
        events = []
        for index, line in enumerate(raw.read_text().splitlines(), 1):
            source = json.loads(line)
            events.append({"sequence": index, "kind": source["kind"], "text": source.get("text", ""), "data": source.get("data", {})})
        trace = {"schema_version": 1, "events": events, "diagnostics": []}
        Path(args.trace).write_bytes(canonical(trace) + b"\n")
        emit({"raw_sha256": digest(raw.read_bytes()), "trace_sha256": digest(Path(args.trace).read_bytes())})
        return 0
    trial = load(args.trial)
    fixture = args.fixture or trial["case"]["fixture"]
    execution = dict(expected)
    if fixture == "prepared-model-mismatch" and trial["treatment"] == "candidate" and args.command == "prepare":
        execution["model"] = "wrong-model"
    if fixture == "prepared-budget-mismatch" and trial["treatment"] == "candidate" and args.command == "prepare":
        execution["limits"] = {**execution["limits"], "token_budget": 99}
    if fixture == "effective-model-mismatch" and trial["treatment"] == "candidate" and args.command == "run":
        execution["model"] = "wrong-model"
    if args.command == "prepare":
        emit({"prepared": {"fixture": fixture, "projection": trial["candidate_inventory"]}, "execution": execution})
        return 0
    if args.command == "run":
        prepared = load(args.prepared)
        if fixture == "output-flood":
            sys.stdout.write("x" * 10_000_000)
            sys.stdout.flush()
            time.sleep(60)
        if fixture == "timeout":
            marker = Path(trial["workspace"]) / "child-started"
            child = subprocess.Popen([sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('started'); import time; time.sleep(30)"])
            (Path(trial["workspace"]).parent / "child.pid").write_text(str(child.pid))
            time.sleep(30)
        events = [event("user_message", trial["case"]["prompt"])]
        candidate = trial["treatment"] == "candidate"
        load_skill = candidate and fixture not in {"missing-load", "activation-negative"}
        if fixture == "wrong-load":
            events.append(event("skill_load", "", candidate_id="sha256:" + "0" * 64,
                                skill_md_sha256="sha256:" + "0" * 64, path="candidate/SKILL.md", non_builtin=True))
        elif fixture == "false-trigger":
            load_skill = True
        if load_skill:
            events.append(event("skill_load", "", candidate_id=trial["candidate_id"],
                                skill_md_sha256=trial["skill_md_sha256"], path="candidate/SKILL.md", non_builtin=True))
        if fixture == "identity-leak":
            answer = "SUCCESS candidate-marker"
        else:
            answer = "SUCCESS" if candidate else "CONTROL"
        events.extend([event("final_answer", answer), event("trial_end", "")])
        Path(args.output).write_text("".join(json.dumps(item) + "\n" for item in events))
        emit({"prepared_digest": prepared["prepared_digest"], "effective_execution": execution, "completed": True})
        return 0
    if args.command == "collect":
        if args.mutate:
            target = Path(args.mutate)
            target.write_bytes(target.read_bytes() + b"mutated\n")
        if fixture == "collect-fail":
            print("fixture collection failure", file=sys.stderr)
            return 7
        paths: list[dict[str, Any]] = []
        if fixture not in {"artifact-missing"}:
            target = Path(args.artifacts) / "out.txt"
            target.write_text("actual artifact")
            paths.append({"path": "out.txt", "source_exists": True})
        else:
            paths.append({"path": "out.txt", "source_exists": False})
        emit({"completed_workspace": True, "declared_artifacts": paths})
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
