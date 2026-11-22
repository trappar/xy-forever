"""Native whole-package admission before writes, plus staged-write failures."""
import hashlib
import io
import json
from pathlib import Path
import random
import struct
import subprocess
import tempfile
import unittest
import zipfile

import pytest

from xy_forever.companions import CyacdImage, NordicApplication
from xy_forever.packages import destination_for
from test_companions import cyacd
from test_ldr import image


from xy_forever import hostbuild

ROOT = Path(__file__).resolve().parents[1]
OS = "firmware/blackfin/bin/os.ldr"
BACKPLANE = "firmware/backplane/bin/backplane.cyacd"
BLE = "firmware/ble/bin/ble.bin"
VALID_OS = image([(4, 0x1000, 2, 0, b"xy")])
VALID_BLE = struct.pack("<II", 0x20001000, 0x19329) + bytes(range(32))


def package(rows, *, comment=b"", method=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=method) as archive:
        for name, data in rows:
            archive.writestr(name, data)
        archive.comment = comment
    return output.getvalue()


@pytest.mark.gate
class NativePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="xyf-synthetic-")
        cls.directory = Path(cls.temporary.name)
        cls.bridge = cls.directory / "package_bridge"
        cls.command = ["/usr/bin/clang++", "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror",
                       str(ROOT / "tests/package_bridge.cpp"),
                       *[str(ROOT / "native" / (name + ".cpp")) for name in
                         ("package", "images", "zip", "inflate", "hash", "package_policy", "companions", "ldr")],
                       "-o", str(cls.bridge)]
        hostbuild.run(cls.command, check=True, capture_output=True)
        cls.cyacd = cyacd()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_bridge(self, mode, data, fault="", *, binary=None):
        path = self.directory / "input.bin"
        path.write_bytes(data)
        result = subprocess.run([str(binary or self.bridge), mode, str(path), str(fault)], capture_output=True)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode, json.loads(result.stdout), result.stderr

    def complete_rows(self):
        return [(OS, VALID_OS), ("firmware/blackfin/bin/os.version", b"1.2.5+0@" + b"a" * 40 + b"\n"),
                (BACKPLANE, self.cyacd), ("firmware/backplane/bin/backplane.version", b"1.0.8+0\n"),
                (BLE, VALID_BLE), ("firmware/ble/bin/ble.version", b"1.0.2+0\n"),
                ("firmware/firmware.version", b"1.2.5+0\n"),
                ("firmware/blackfin/content/display/test.svg", b"svg"),
                ("firmware/blackfin/bin/bootloader.ldr", b"excluded bootloader bytes"),
                ("firmware/ble/bin/nordic_bootloader_0.bin", b"excluded Nordic bootloader"),
                ("firmware/blackfin/content/empty", b"")]

    def test_whole_package_hashes_roles_versions_and_staging(self):
        rows = self.complete_rows()
        data = package(rows)
        code, report, _ = self.run_bridge("stage", data)
        self.assertEqual(code, 0, report)
        self.assertTrue(report["published"])
        self.assertEqual(report["publish_attempts"], 1)
        self.assertEqual(report["aborted"], 0)
        self.assertEqual(report["archive_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(report["os_version"], [1, 2, 5, 0])
        self.assertEqual(report["backplane_version"], [1, 0, 8, 0])
        self.assertEqual(report["ble_version"], [1, 0, 2, 0])
        self.assertEqual(report["firmware_version"], [1, 2, 5, 0])
        expected_paths = []
        for row, native in zip(rows, report["items"]):
            name, content = row
            _, destination = destination_for(name)
            self.assertEqual(native["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(native["size"], len(content))
            self.assertEqual(native["destination"], destination or "")
            if destination:
                expected_paths.append(destination)
        self.assertEqual(report["paths"], expected_paths)
        self.assertEqual(report["begun"], report["ended"])
        self.assertFalse(any("bootloader" in path for path in report["paths"]))
        self.assertNotIn("os.ldr", report["paths"])

    def test_admission_alone_has_no_writer_callbacks(self):
        code, report, _ = self.run_bridge("admit", package(self.complete_rows()))
        self.assertEqual(code, 0, report)
        self.assertEqual(report["begun"], 0)
        self.assertEqual(report["publish_attempts"], 0)

    def test_corrupt_final_member_prevents_every_write(self):
        for last in ("firmware/blackfin/content/late", "firmware/blackfin/bin/bootloader.ldr"):
            data = bytearray(package([(OS, VALID_OS), (last, b"unique corrupted final member")], method=zipfile.ZIP_STORED))
            data[data.index(b"unique corrupted final member")] ^= 1
            code, report, _ = self.run_bridge("stage", data)
            self.assertEqual(code, 1)
            self.assertEqual(report["status"], "archive")
            self.assertEqual(report["begun"], 0)
            self.assertFalse(report["published"])

    def test_unknown_final_member_prevents_every_write(self):
        code, report, _ = self.run_bridge("stage", package([(OS, VALID_OS), ("firmware/unknown", b"bad")]))
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "policy")
        self.assertEqual(report["begun"], 0)

    def test_invalid_os_loader_and_caller_ram_policy_rejected(self):
        for data in (b"not an OS", image([(4, 0x40000000, 2, 0, b"xy")], entry=0x40000000)):
            code, report, _ = self.run_bridge("stage", package([(OS, data)]))
            self.assertEqual(code, 1)
            self.assertEqual(report["status"], "os")
            self.assertEqual(report["begun"], 0)
        code, report, _ = self.run_bridge("stage", package([(OS, VALID_OS)]), "protect-os")
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "os")

    def test_exact_cyacd_header_rows_and_checksums_required(self):
        damaged = bytearray(self.cyacd)
        damaged[-5] = ord("0") if damaged[-5] != ord("0") else ord("1")
        for content in (bytes(damaged), cyacd(silicon=0x100A11A1), cyacd([(0, 0, bytes(256))]),
                        cyacd([(1, 25, bytes(256))]), b"100A11A00001\n"):
            code, report, _ = self.run_bridge("stage", package([(OS, VALID_OS), (BACKPLANE, content),
                ("firmware/backplane/bin/backplane.version", b"1.0.8+0\n")]))
            self.assertEqual(code, 1)
            self.assertEqual(report["status"], "backplane")
            self.assertEqual(report["begun"], 0)

    def test_ble_application_vectors_and_length_required(self):
        for content in (b"", bytes(8), struct.pack("<II", 0x20001000, 0x4541),
                        struct.pack("<II", 0x20000102, 0x19329), VALID_BLE + b"x"):
            code, report, _ = self.run_bridge("stage", package([(OS, VALID_OS), (BLE, content),
                ("firmware/ble/bin/ble.version", b"1.0.2+0\n")]))
            self.assertEqual(code, 1)
            self.assertEqual(report["status"], "ble")
            self.assertEqual(report["begun"], 0)

    def test_missing_or_orphan_companion_versions_and_bad_versions(self):
        for rows in (
            [(OS, VALID_OS), (BLE, VALID_BLE)],
            [(OS, VALID_OS), ("firmware/ble/bin/ble.version", b"1.0.2+0\n")],
            [(OS, VALID_OS), (BACKPLANE, self.cyacd)],
            [(OS, VALID_OS), ("firmware/blackfin/bin/os.version", b"unparseable")],
            [(OS, VALID_OS), ("firmware/firmware.version", b"999999999.1.2+0")],
        ):
            code, report, _ = self.run_bridge("stage", package(rows))
            self.assertEqual(code, 1)
            self.assertEqual(report["status"], "version")
            self.assertEqual(report["begun"], 0)

    def test_missing_os_and_small_scratch_fail_before_writes(self):
        code, report, _ = self.run_bridge("stage", package([("firmware/blackfin/content/x", b"x")]))
        self.assertEqual(code, 1)
        self.assertEqual(report["begun"], 0)
        code, report, _ = self.run_bridge("stage", package([(OS, VALID_OS)]), "small-scratch")
        self.assertEqual(code, 1)
        self.assertEqual(report["begun"], 0)

    def test_source_change_before_stage_has_zero_writes(self):
        code, report, _ = self.run_bridge("stage", package(self.complete_rows()), "mutate-before")
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "changed")
        self.assertEqual(report["begun"], 0)
        self.assertEqual(report["aborted"], 1)
        self.assertEqual(report["publish_attempts"], 0)

    def test_source_change_during_stage_or_only_in_comment_never_publishes(self):
        for fault in ("mutate-during", "mutate-after-end", "mutate-comment"):
            code, report, _ = self.run_bridge("stage", package(self.complete_rows(), comment=b"archive comment"), fault)
            self.assertEqual(code, 1)
            self.assertGreater(report["begun"], 0)
            self.assertEqual(report["aborted"], 1)
            self.assertEqual(report["publish_attempts"], 0)
            self.assertFalse(report["published"])

    def test_backend_failures_abort_and_publish_only_on_full_success(self):
        for fault in ("begin-fail", "write-fail", "end-fail", "publish-fail"):
            code, report, _ = self.run_bridge("stage", package([(OS, VALID_OS)]), fault)
            self.assertEqual(code, 1)
            self.assertEqual(report["aborted"], 1)
            self.assertFalse(report["published"])
            self.assertEqual(report["publish_attempts"], int(fault == "publish-fail"))

    def test_forged_destination_rejected_before_writes(self):
        code, report, _ = self.run_bridge("stage", package([(OS, VALID_OS)]), "forged-path")
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "policy")
        self.assertEqual(report["begun"], 0)
        self.assertEqual(report["publish_attempts"], 0)

    def test_cyacd_native_rows_match_python_and_respect_output_guards(self):
        parsed = CyacdImage.parse(self.cyacd)
        parsed.require_opxy_layout()
        for index in (0, 1, 100, 365, 366):
            code, report, _ = self.run_bridge("image-cyacd", self.cyacd, index)
            self.assertEqual(code, 0, report)
            self.assertEqual(report["rows"], 367)
            self.assertEqual(report["row_number"], parsed.rows[index].number)
            self.assertEqual(report["row_size"], 256)
            self.assertTrue(report["guards"])
            self.assertEqual(report["row_sha256"], hashlib.sha256(parsed.rows[index].data).hexdigest())
        self.assertEqual(self.run_bridge("image-cyacd", self.cyacd, 367)[0], 1)

    def test_ble_native_vectors_and_crc_match_python(self):
        rng = random.Random(1702)
        for length in (8, 12, 40, 256, 0x70000):
            content = VALID_BLE[:8] + bytes(rng.randrange(256) for _ in range(length - 8))
            expected = NordicApplication.parse(content)
            code, report, _ = self.run_bridge("image-ble", content)
            self.assertEqual(code, 0)
            self.assertEqual(report["stack"], expected.initial_stack)
            self.assertEqual(report["reset"], expected.reset_vector)
            self.assertEqual(report["crc16"], expected.checksum)

    def test_directory_crc_and_empty_content(self):
        rows = [("firmware/", b""), ("firmware/blackfin/", b""),
                (OS, VALID_OS), ("firmware/blackfin/content/empty", b"")]
        code, report, _ = self.run_bridge("stage", package(rows))
        self.assertEqual(code, 0, report)
        self.assertEqual(report["begun"], 2)
        self.assertEqual(len(report["items"]), 2)
        bad = bytearray(package([(OS, VALID_OS), ("firmware/last/", b"")]))
        with zipfile.ZipFile(io.BytesIO(bad)) as archive:
            entry = archive.getinfo("firmware/last/")
            local = entry.header_offset
            name_size, extra_size = struct.unpack_from("<HH", bad, local + 26)
            bad[local + 30 + name_size + extra_size] = 0x07
        code, report, _ = self.run_bridge("stage", bad)
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "archive")
        self.assertEqual(report["begun"], 0)

    def test_damage_and_write_faults_under_sanitizers(self):
        target = self.directory / "sanitized"
        command = list(self.command)
        command[command.index("-O2")] = "-O1"
        command[-1] = str(target)
        command[1:1] = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        hostbuild.run(command, check=True, capture_output=True)
        original = package(self.complete_rows(), comment=b"comment")
        for fault in ("", "mutate-before", "mutate-during", "mutate-comment", "write-fail", "forged-path"):
            _, _, errors = self.run_bridge("stage", original, fault, binary=target)
            self.assertNotIn(b"AddressSanitizer", errors)
            self.assertNotIn(b"runtime error:", errors)
        rng = random.Random(19512)
        for _ in range(30):
            damaged = bytearray(original)
            damaged[rng.randrange(len(damaged))] ^= rng.randrange(1, 256)
            _, _, errors = self.run_bridge("stage", damaged, binary=target)
            self.assertNotIn(b"AddressSanitizer", errors)
            self.assertNotIn(b"runtime error:", errors)


if __name__ == "__main__":
    unittest.main()
