"""Immutable canonical task-occurrence resolutions.

The index is deliberately a projection: receipts and resolutions remain the
content-addressed evidence.  A task key is lookup material only, never an
occurrence identity.
"""
from __future__ import annotations
import hashlib, json, os, uuid
from pathlib import Path
from typing import Any

class TaskOccurrenceError(ValueError): pass

def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
def digest(value: Any) -> str: return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()
def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71: raise TaskOccurrenceError(field)
    return value
def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); temp=path.parent / f".{path.name}.{uuid.uuid4().hex}"
    try:
        with temp.open("xb") as f: f.write(canonical(value)+b"\n"); f.flush(); os.fsync(f.fileno())
        os.chmod(temp, 0o400); os.replace(temp,path)
    finally: temp.unlink(missing_ok=True)

def validate_resolution(value: Any) -> dict[str, Any]:
    keys={"schema_version","kind","profile_id","task_key","profile_receipt_sha256","source_revision","qualified_session_id","goal_event_id","occurred_at","boundary_relation","canonical_occurrence_id","prior_canonical_occurrence_id","review_contract","resolution_sha256"}
    if not isinstance(value,dict) or set(value)!=keys: raise TaskOccurrenceError("resolution-shape")
    body={k:v for k,v in value.items() if k!="resolution_sha256"}
    if value["schema_version"] != 1 or value["kind"] != "task_occurrence_resolution" or value["resolution_sha256"] != digest(body): raise TaskOccurrenceError("resolution-identity")
    for key in ("profile_id","task_key","profile_receipt_sha256"): _sha(value[key],key)
    if value["canonical_occurrence_id"] is not None: _sha(value["canonical_occurrence_id"], "canonical_occurrence_id")
    if value["boundary_relation"] not in {"same-occurrence","new-occurrence","boundary-conflict","boundary-unresolved"}: raise TaskOccurrenceError("boundary-relation")
    prior=value["prior_canonical_occurrence_id"]
    if value["boundary_relation"] == "same-occurrence":
        _sha(prior,"prior-occurrence")
        if value["canonical_occurrence_id"] != prior: raise TaskOccurrenceError("same-occurrence-alias")
    elif prior is not None: raise TaskOccurrenceError("unexpected-prior-occurrence")
    if value["boundary_relation"] in {"boundary-conflict","boundary-unresolved"} and value["canonical_occurrence_id"] is not None: raise TaskOccurrenceError("conflict-occurrence")
    if not all(isinstance(value[k],str) and value[k] for k in ("source_revision","qualified_session_id","goal_event_id","occurred_at","review_contract")): raise TaskOccurrenceError("resolution-metadata")
    return value

def build_resolution(*, profile: dict[str,Any], receipt: dict[str,Any], relation: str, review_contract: str, prior_occurrence_id: str|None=None) -> dict[str,Any]:
    if receipt.get("schema_version") != 2: raise TaskOccurrenceError("legacy-receipt-no-authority")
    required=("profile_id","task_key","goal_event_id","occurred_at")
    if any(not isinstance(profile.get(k),str) or not profile[k] for k in required): raise TaskOccurrenceError("profile-anchor")
    if relation == "same-occurrence":
        occurrence=_sha(prior_occurrence_id,"prior-occurrence")
    elif relation == "new-occurrence":
        occurrence=digest({"qualified_session_id":receipt.get("qualified_session_id"),"goal_event_id":profile["goal_event_id"]})
        prior_occurrence_id=None
    elif relation in {"boundary-conflict","boundary-unresolved"}:
        occurrence=None; prior_occurrence_id=None
    else: raise TaskOccurrenceError("boundary-relation")
    body={"schema_version":1,"kind":"task_occurrence_resolution","profile_id":profile["profile_id"],"task_key":profile["task_key"],"profile_receipt_sha256":receipt.get("receipt_sha256"),"source_revision":receipt.get("source_revision"),"qualified_session_id":receipt.get("qualified_session_id"),"goal_event_id":profile["goal_event_id"],"occurred_at":profile["occurred_at"],"boundary_relation":relation,"canonical_occurrence_id":occurrence,"prior_canonical_occurrence_id":prior_occurrence_id,"review_contract":review_contract}
    return {**body,"resolution_sha256":digest(body)}

INDEX_SCHEMA_VERSION = 1

def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskOccurrenceError("index-read") from exc

def validate_index(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "kind", "entries"}:
        raise TaskOccurrenceError("index-shape")
    if value["schema_version"] != INDEX_SCHEMA_VERSION or value["kind"] != "task_occurrence_index" or not isinstance(value["entries"], dict):
        raise TaskOccurrenceError("index-contract")
    for task_key, entry in value["entries"].items():
        _sha(task_key, "index-task-key")
        if not isinstance(entry, dict) or set(entry) != {"resolution_sha256", "canonical_occurrence_id"}:
            raise TaskOccurrenceError("index-entry")
        _sha(entry["resolution_sha256"], "index-resolution")
        if entry["canonical_occurrence_id"] is not None: _sha(entry["canonical_occurrence_id"], "index-occurrence")
    return value

def resolution_path(root: Path, resolution_sha256: str) -> Path:
    return root / f"{_sha(resolution_sha256, 'resolution-sha256').removeprefix('sha256:')}.json"

def load_exact(root: Path, index_path: Path, task_key: str) -> dict[str, Any] | None:
    _sha(task_key, "task-key")
    if not index_path.exists(): return None
    index = validate_index(_read(index_path)); entry=index["entries"].get(task_key)
    if entry is None: return None
    path=resolution_path(root, entry["resolution_sha256"])
    resolution=validate_resolution(_read(path))
    if resolution["resolution_sha256"] != entry["resolution_sha256"] or resolution["task_key"] != task_key or resolution["canonical_occurrence_id"] != entry["canonical_occurrence_id"]:
        raise TaskOccurrenceError("index-provenance")
    return resolution

def persist(root: Path, index_path: Path, resolution: dict[str, Any]) -> dict[str, Any]:
    resolution=validate_resolution(resolution); path=resolution_path(root,resolution["resolution_sha256"])
    if path.exists():
        if _read(path) != resolution: raise TaskOccurrenceError("resolution-collision")
    else: _write(path,resolution)
    index={"schema_version": INDEX_SCHEMA_VERSION, "kind":"task_occurrence_index", "entries": {}}
    if index_path.exists(): index=validate_index(_read(index_path))
    entries=dict(index["entries"]); prior=entries.get(resolution["task_key"])
    entry={"resolution_sha256":resolution["resolution_sha256"], "canonical_occurrence_id":resolution["canonical_occurrence_id"]}
    if prior is not None and prior != entry: raise TaskOccurrenceError("index-task-key-conflict")
    entries[resolution["task_key"]]=entry
    # The projection is mutable, but writes are atomic and wholly derived from immutable evidence.
    temp=index_path.parent / f".{index_path.name}.{uuid.uuid4().hex}"; index_path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with temp.open("xb") as f: f.write(canonical({**index,"entries":entries})+b"\n");f.flush();os.fsync(f.fileno())
        os.chmod(temp,0o600);os.replace(temp,index_path)
    finally: temp.unlink(missing_ok=True)
    return resolution
