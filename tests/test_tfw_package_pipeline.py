"""Encrypted container -> random-access ZIP -> whole-package admission."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_native_package import package, VALID_OS, OS
from test_tfw import container

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402


class TfwPackagePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="xyf-tfw-pipeline-")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.bridge = cls.root / "bridge"
        hostbuild.run(["/usr/bin/clang++", "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror",
                       str(ROOT / "tests/tfw_package_bridge.cpp"),
                       *[str(ROOT / "native" / (name + ".cpp")) for name in
                          ("tfw", "aes", "package", "images", "zip", "inflate", "hash", "package_policy", "companions", "ldr")],
                        "-o", str(cls.bridge)], check=True, capture_output=True)

    def run_pipeline(self, data):
        path = self.root / "synthetic.tfw"
        path.write_bytes(data)
        result = subprocess.run([str(self.bridge), str(path)], capture_output=True, check=False)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_both_encrypted_wrappers_and_clear_payload_compose(self):
        archive = package([(OS, VALID_OS), ("firmware/blackfin/bin/bootloader.ldr", b"excluded"),
                           ("firmware/blackfin/content/example", b"a" * 100000)])
        for short, index in ((False, 0), (True, 0), (False, 1), (False, 0xfe)):
            with self.subTest(short=short, index=index):
                code, report = self.run_pipeline(container(archive, short=short, index=index))
                self.assertEqual(code, 0, report)
                self.assertEqual(report, {"phase": "admission", "status": "ok", "items": 3, "excluded": 1})

    def test_container_and_inner_package_fail_at_distinct_boundaries(self):
        valid = container(package([(OS, VALID_OS)]))
        corrupt = bytearray(valid);corrupt[-20] ^= 1
        for data, phase in ((bytes(corrupt), "tfw"), (container(b"not a zip"), "zip"),
                            (container(package([(OS, b"invalid loader")])), "admission"),
                            (container(package([(OS, VALID_OS), ("firmware/unknown", b"x")])), "admission")):
            code, report = self.run_pipeline(data)
            self.assertEqual(code, 1)
            self.assertEqual(report["phase"], phase)
