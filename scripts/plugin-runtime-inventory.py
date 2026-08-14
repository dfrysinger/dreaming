#!/usr/bin/env python3
"""Render the plugin runtime inventory required by settings transactions."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


class InventoryError(RuntimeError):
    pass


def load_estate(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise InventoryError("estate collector is unavailable")
    specification = importlib.util.spec_from_file_location(
        "plugin_runtime_estate", path
    )
    if specification is None or specification.loader is None:
        raise InventoryError("estate collector is unavailable")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def inventory(args: argparse.Namespace) -> dict[str, Any]:
    settings = Path(args.settings).expanduser().resolve()
    expected_settings = Path(args.expected_settings).expanduser().resolve()
    if settings != expected_settings or settings.is_symlink() or not settings.is_file():
        raise InventoryError("settings identity mismatch")
    collector = load_estate(Path(args.estate_script).expanduser().resolve())
    try:
        census = collector.collect(
            {
                "host_id": args.target_host_id,
                "target_home": args.target_home,
                "user_context_cwd": args.user_context_cwd or args.target_home,
                "copilot_binary": args.copilot_binary,
            }
        )
    except (collector.EstateError, OSError, TypeError, ValueError) as error:
        raise InventoryError("estate collection failed") from error
    plugins = [
        plugin
        for plugin in census.get("plugins", [])
        if isinstance(plugin, dict) and plugin.get("plugin_id") == args.plugin_id
    ]
    if len(plugins) != 1:
        raise InventoryError("plugin identity is unavailable")
    plugin = plugins[0]
    package_identity = {
        key: plugin.get(key)
        for key in ("plugin_id", "source_identity", "version")
    }
    if any(not isinstance(value, str) or not value for value in package_identity.values()):
        raise InventoryError("plugin identity is malformed")
    enabled_ids = {
        item.get("canonical_capability_id")
        for item in census.get("enabled_instances", [])
        if isinstance(item, dict)
        and isinstance(item.get("canonical_capability_id"), str)
        and item["canonical_capability_id"]
    }
    owned_ids = sorted(
        {
            item.get("canonical_capability_id")
            for item in census.get("physical_instances", [])
            if isinstance(item, dict)
            and item.get("root_class") == "plugin"
            and item.get("owner") == args.plugin_id
            and item.get("canonical_capability_id") in enabled_ids
        }
    )
    evidence = census.get("evidence")
    copilot_version = (
        evidence.get("copilot_version") if isinstance(evidence, dict) else None
    )
    if (
        not isinstance(plugin.get("enabled"), bool)
        or not isinstance(copilot_version, str)
        or not copilot_version
    ):
        raise InventoryError("plugin runtime inventory is malformed")
    return {
        "schema_version": 1,
        "copilot_version": copilot_version,
        "plugin_identity": package_identity,
        "plugin_enabled": plugin["enabled"],
        "owned_capability_ids": owned_ids,
        "estate_capability_ids": sorted(enabled_ids),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--estate-script", required=True)
    parser.add_argument("--expected-settings", required=True)
    parser.add_argument("--target-host-id", required=True)
    parser.add_argument("--target-home", required=True)
    parser.add_argument("--user-context-cwd")
    parser.add_argument("--copilot-binary", required=True)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--plugin-id", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(inventory(args), sort_keys=True))
    except (InventoryError, ImportError, OSError, SyntaxError) as error:
        print(
            json.dumps(
                {"ok": False, "error": {"code": str(error)}},
                sort_keys=True,
            )
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
