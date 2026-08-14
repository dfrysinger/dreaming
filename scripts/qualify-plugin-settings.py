#!/usr/bin/env python3
"""Qualify one installed Copilot plugin source type by disable and restore."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


class QualificationError(RuntimeError):
    pass


def load_transaction(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise QualificationError("transaction-code-unavailable")
    specification = importlib.util.spec_from_file_location(
        "plugin_settings_qualification_transaction", path
    )
    if specification is None or specification.loader is None:
        raise QualificationError("transaction-code-unavailable")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def qualification(module: Any, args: argparse.Namespace) -> dict[str, Any]:
    settings = Path(os.path.abspath(os.path.expanduser(args.settings)))
    root = Path(args.transaction_root).expanduser().resolve()
    qualification_root = Path(args.qualification_root).expanduser().resolve()
    lock = Path(args.lock).expanduser().resolve()
    recovery = Path(args.recovery_state).expanduser().resolve()
    plugin = {
        "plugin_id": args.plugin_id,
        "source_identity": args.source_identity,
        "version": args.version,
        "source_type": args.source_type,
        "settings_key": args.settings_key,
    }
    qualification_payload = {
        "schema_version": module.SCHEMA_VERSION,
        "status": "qualified",
        "source_type": args.source_type,
        "copilot_version": args.copilot_version,
        "disable_verified": True,
        "restore_verified": True,
    }
    record = {
        **qualification_payload,
        "qualification_sha256": module.hash_json(qualification_payload),
    }
    qualification_root.mkdir(parents=True, exist_ok=True)
    os.chmod(qualification_root, 0o700)
    output = qualification_root / f"{record['qualification_sha256']}.json"
    if output.exists():
        existing = module.load_object(output, "plugin-source-unqualified")
        module.validate_qualification(
            existing,
            {
                "plugin": plugin,
                "copilot_version": args.copilot_version,
                "qualification_sha256": record["qualification_sha256"],
            },
        )
        return {
            "ok": True,
            "status": "committed",
            "qualification_sha256": record["qualification_sha256"],
            "already_qualified": True,
        }
    preimage = module.read_regular(settings, "settings-preimage-invalid")
    document = module.settings_document(preimage)
    enabled = document.get("enabledPlugins") or {}
    prior_present = args.settings_key in enabled
    prior_value = enabled.get(args.settings_key)
    if prior_present and prior_value is not True:
        raise QualificationError("qualification-plugin-not-enabled")
    base_request = {
        "plugin": plugin,
        "copilot_version": args.copilot_version,
    }
    runtime_verifier = module.command_runtime_verifier(
        args.runtime_verifier,
        settings,
        base_request,
    )
    runtime_before = module.validate_runtime(runtime_verifier(), base_request)
    if runtime_before["plugin_enabled"] is not True:
        raise QualificationError("qualification-plugin-not-enabled")
    evidence_sha = module.hash_json(runtime_before)
    with tempfile.TemporaryDirectory(
        prefix=".qualification-", dir=qualification_root
    ) as temporary:
        provisional_root = Path(temporary)
        provisional = provisional_root / output.name
        module.immutable_json(provisional, record)
        ledger = module.load_ledger(root)
        disable_payload = {
            "schema_version": module.SCHEMA_VERSION,
            "operation_id": f"qualify-{uuid.uuid4().hex}",
            "action": "disable",
            "plugin": plugin,
            "copilot_version": args.copilot_version,
            "qualification_sha256": record["qualification_sha256"],
            "expected": {
                "settings_sha256": preimage.sha256,
                "settings_device": preimage.device,
                "settings_inode": preimage.inode,
                "settings_mode": preimage.mode,
                "prior_key_present": prior_present,
                "prior_key_value": prior_value,
                "runtime_before_sha256": module.hash_json(runtime_before),
                "ledger_sha256": ledger["ledger_sha256"],
            },
            "evidence": {
                "census_sha256": evidence_sha,
                "capability_inventory_sha256": evidence_sha,
            },
        }
        disable_request = {
            **disable_payload,
            "request_sha256": module.hash_json(disable_payload),
        }
        disabled = module.execute_disable(
            request=disable_request,
            settings_path=settings,
            transaction_root=root,
            qualification_root=provisional_root,
            lock_path=lock,
            recovery_state=recovery,
            runtime_verifier=runtime_verifier,
            swapper=module.MacOSSwapper(),
        )
        if disabled.get("ok") is not True:
            raise QualificationError(
                "qualification-disable-"
                + str(
                    disabled.get("cause")
                    or disabled.get("status")
                    or "failed"
                )
            )
        disabled_state = module.read_regular(
            settings, "settings-preimage-invalid"
        )
        disabled_runtime = module.validate_runtime(
            runtime_verifier(), base_request
        )
        ledger = module.load_ledger(root)
        restore_payload = {
            "schema_version": module.SCHEMA_VERSION,
            "operation_id": f"qualify-{uuid.uuid4().hex}",
            "action": "restore",
            "plugin": plugin,
            "copilot_version": args.copilot_version,
            "qualification_sha256": record["qualification_sha256"],
            "restores_receipt_sha256": disabled["receipt_sha256"],
            "expected": {
                "settings_sha256": disabled_state.sha256,
                "settings_device": disabled_state.device,
                "settings_inode": disabled_state.inode,
                "settings_mode": disabled_state.mode,
                "runtime_before_sha256": module.hash_json(disabled_runtime),
                "ledger_sha256": ledger["ledger_sha256"],
            },
            "evidence": {
                "census_sha256": module.hash_json(disabled_runtime),
                "capability_inventory_sha256": module.hash_json(
                    disabled_runtime
                ),
            },
        }
        restore_request = {
            **restore_payload,
            "request_sha256": module.hash_json(restore_payload),
        }
        restored_result = module.execute_restore(
            request=restore_request,
            settings_path=settings,
            transaction_root=root,
            qualification_root=provisional_root,
            lock_path=lock,
            recovery_state=recovery,
            runtime_verifier=runtime_verifier,
            swapper=module.MacOSSwapper(),
        )
        if restored_result.get("ok") is not True:
            raise QualificationError(
                "qualification-restore-"
                + str(
                    restored_result.get("cause")
                    or restored_result.get("status")
                    or "failed"
                )
            )
    restored = module.read_regular(settings, "settings-preimage-invalid")
    restored_runtime = module.validate_runtime(runtime_verifier(), base_request)
    if (
        restored.data != preimage.data
        or restored.mode != preimage.mode
        or module.hash_json(restored_runtime) != module.hash_json(runtime_before)
    ):
        raise QualificationError("qualification-restore-mismatch")
    module.atomic_json(output, record)
    return {
        "ok": True,
        "status": "committed",
        "qualification_sha256": record["qualification_sha256"],
        "already_qualified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transaction-script", required=True)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--transaction-root", required=True)
    parser.add_argument("--qualification-root", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--recovery-state", required=True)
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--source-identity", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--source-type", choices=("marketplace", "direct"), required=True
    )
    parser.add_argument("--settings-key", required=True)
    parser.add_argument("--copilot-version", required=True)
    parser.add_argument(
        "--runtime-verifier", required=True, nargs=argparse.REMAINDER
    )
    args = parser.parse_args()
    try:
        module = load_transaction(
            Path(args.transaction_script).expanduser().resolve()
        )
        print(json.dumps(qualification(module, args), sort_keys=True))
    except (
        QualificationError,
        ImportError,
        OSError,
        RuntimeError,
        SyntaxError,
        TypeError,
        ValueError,
    ) as error:
        print(
            json.dumps(
                {"ok": False, "error": {"code": str(error)}},
                sort_keys=True,
            )
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
