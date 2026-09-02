#!/usr/bin/env python3
"""Deterministic remote-subject receiver bundle checks."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MODULE_PATH = SCRIPT_DIR / "build-remote-subject-receiver.py"
spec = importlib.util.spec_from_file_location(
    "remote_subject_receiver_builder", MODULE_PATH
)
builder = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(builder)


class ReceiverBundleTest(unittest.TestCase):
    def setUp(self) -> None:
        test_root = REPO_ROOT / ".test-work"
        test_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="remote-subject-receiver.", dir=test_root
        )
        self.root = Path(self.temporary.name)
        self.output = self.root / "bundles"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_bundle_is_exact_immutable_and_replayable(self) -> None:
        result = builder.build(REPO_ROOT, self.output)
        self.assertEqual(result["status"], "published")
        bundle = Path(result["bundle_root"])
        manifest = json.loads((bundle / "manifest.json").read_bytes())
        self.assertEqual(manifest["bundle_id"], result["bundle_id"])
        self.assertEqual(manifest["protocol_version"], 1)
        self.assertEqual(len(manifest["files"]), 4)
        self.assertEqual(os.stat(bundle).st_mode & 0o777, 0o500)
        replay = builder.build(REPO_ROOT, self.output)
        self.assertEqual(replay["status"], "existing")
        self.assertEqual(replay["bundle_id"], result["bundle_id"])

    def test_tampered_existing_bundle_is_refused(self) -> None:
        result = builder.build(REPO_ROOT, self.output)
        bundle = Path(result["bundle_root"])
        target = bundle / "scripts" / "ssh-estate-census.py"
        os.chmod(bundle, 0o700)
        os.chmod(target.parent, 0o700)
        os.chmod(target, 0o600)
        target.write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(
            builder.BundleError, "inventory differs"
        ):
            builder.build(REPO_ROOT, self.output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
