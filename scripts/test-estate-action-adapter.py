#!/usr/bin/env python3
"""Deterministic checks for the governed estate action adapter."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
ADAPTER = ROOT / "estate-action-adapter.py"
ACTION_TEST = ROOT / "test-estate-action.py"
SPEC = importlib.util.spec_from_file_location(
    "estate_action_test_fixture", ACTION_TEST
)
fixture_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = fixture_module
SPEC.loader.exec_module(fixture_module)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


class AdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.inner = self.root / "inner.py"
        self.inner.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import argparse
                import json
                from pathlib import Path

                parser = argparse.ArgumentParser()
                parser.add_argument("--request", required=True)
                parser.add_argument("--mode", required=True)
                parser.add_argument("--envelope", required=True)
                parser.add_argument("--clobber")
                args = parser.parse_args()
                if args.clobber:
                    Path(args.clobber).write_text(
                        '{"op_id":"substituted"}'
                    )
                request = json.loads(Path(args.request).read_text())
                status = args.mode
                inner = {
                    "ok": status == "committed",
                    "status": status,
                    "request_sha256": request.get(
                        "request_sha256", "inner-request"
                    ),
                }
                result = (
                    {"ok": inner["ok"], "result": inner}
                    if args.envelope == "plugin"
                    else inner
                )
                print(json.dumps(result, sort_keys=True))
                raise SystemExit(0 if inner["ok"] else 2)
                """
            ),
            encoding="utf-8",
        )
        os.chmod(self.inner, 0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_adapter_config(
        self, mode: str = "committed", clobber: Path | None = None
    ) -> Path:
        executors = {
            kind: {
                "argv": [
                    str(self.inner),
                    "--request",
                    "{request}",
                    "--mode",
                    mode,
                    "--envelope",
                    "plugin" if kind.startswith("plugin_") else "flat",
                    *(
                        ["--clobber", str(clobber)]
                        if clobber is not None
                        else []
                    ),
                ],
                "timeout": 10,
            }
            for kind in fixture_module.module.ACTION_CONTRACTS
        }
        payload = {
            "schema_version": 1,
            "executors": executors,
        }
        config = {**payload, "config_sha256": digest(payload)}
        path = self.root / f"adapter-{mode}.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def prepare(
        self, kind: str = "personal_archive", mode: str = "committed"
    ) -> tuple[Path, Path, Path]:
        fixture = fixture_module.Fixture(self.root / f"{kind}-{mode}", kind)
        config = self.write_adapter_config(mode)
        config_sha256 = json.loads(config.read_text())["config_sha256"]
        fixture.adapter = ADAPTER
        fixture.candidate["executor"]["argv"] = [
            str(ADAPTER),
            "--config",
            str(config),
            "--config-sha256",
            config_sha256,
            "--request",
            "{request}",
            "--authorization",
            "{authorization}",
        ]
        fixture.reauthorize_adapter()
        authorization = fixture.root / "authorization.json"
        authorization.write_text(
            json.dumps(fixture.authorization), encoding="utf-8"
        )
        request = fixture.root / "request.json"
        request.write_text(
            json.dumps(fixture.executor_request), encoding="utf-8"
        )
        return config, authorization, request

    def run_adapter(
        self,
        config: Path,
        authorization: Path,
        request: Path,
        expected_sha256: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        config_sha256 = (
            expected_sha256
            if expected_sha256 is not None
            else json.loads(config.read_text())["config_sha256"]
        )
        return subprocess.run(
            [
                sys.executable,
                str(ADAPTER),
                "--config",
                str(config),
                "--config-sha256",
                config_sha256,
                "--request",
                str(request),
                "--authorization",
                str(authorization),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_all_action_kinds_return_sealed_committed_results(self) -> None:
        for kind in fixture_module.module.ACTION_CONTRACTS:
            with self.subTest(kind=kind):
                config, authorization, request = self.prepare(kind)
                process = self.run_adapter(config, authorization, request)
                self.assertEqual(process.returncode, 0, process.stderr)
                result = json.loads(process.stdout)
                expected = {
                    key: value
                    for key, value in result.items()
                    if key != "result_sha256"
                }
                self.assertEqual(result["status"], "committed")
                self.assertTrue(result["ok"])
                self.assertEqual(result["result_sha256"], digest(expected))
                self.assertNotIn("target", result["receipt"])

    def test_inner_rejection_stays_rejected(self) -> None:
        config, authorization, request = self.prepare(
            "plugin_disable", "rejected"
        )
        process = self.run_adapter(config, authorization, request)
        self.assertEqual(process.returncode, 2, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["ok"])

    def test_config_digest_mismatch_requires_recovery(self) -> None:
        config, authorization, request = self.prepare()
        process = self.run_adapter(
            config,
            authorization,
            request,
            "sha256:" + "0" * 64,
        )
        self.assertEqual(process.returncode, 2)
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(
            result["receipt"]["error"],
            "estate-action-adapter-config-invalid",
        )

    def test_request_drift_requires_recovery(self) -> None:
        config, authorization, request = self.prepare()
        value = json.loads(request.read_text())
        value["op_id"] = "changed"
        request.write_text(json.dumps(value), encoding="utf-8")
        process = self.run_adapter(config, authorization, request)
        self.assertEqual(process.returncode, 2)
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(
            result["receipt"]["error"], "estate-action-request-invalid"
        )

    def test_inner_executor_reads_verified_request_copy(self) -> None:
        fixture = fixture_module.Fixture(
            self.root / "verified-copy", "personal_archive"
        )
        request = fixture.root / "request.json"
        request.write_text(
            json.dumps(fixture.executor_request), encoding="utf-8"
        )
        config = self.write_adapter_config(clobber=request)
        config_sha256 = json.loads(config.read_text())["config_sha256"]
        fixture.adapter = ADAPTER
        fixture.candidate["executor"]["argv"] = [
            str(ADAPTER),
            "--config",
            str(config),
            "--config-sha256",
            config_sha256,
            "--request",
            "{request}",
            "--authorization",
            "{authorization}",
        ]
        fixture.reauthorize_adapter()
        authorization = fixture.root / "authorization.json"
        authorization.write_text(
            json.dumps(fixture.authorization), encoding="utf-8"
        )
        process = self.run_adapter(config, authorization, request)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["status"], "committed")
        self.assertEqual(json.loads(request.read_text())["op_id"], "substituted")


if __name__ == "__main__":
    unittest.main()
