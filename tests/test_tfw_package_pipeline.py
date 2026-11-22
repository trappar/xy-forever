"""Encrypted container -> random-access ZIP -> whole-package admission."""
import unittest

from test_native_package import package, VALID_OS, OS
from test_tfw import container

import xyf_hosttest


class TfwPackagePipelineTests(unittest.TestCase):
    def run_pipeline(self, data):
        return xyf_hosttest.module("tfw_package").run(data)

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
