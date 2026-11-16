"""Offline review must not mistake vendor baselines for installed device state."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from xy_forever.packages import PackageError
from xy_forever.update_plan import prepare_update, review_update
from test_native_package import BACKPLANE, BLE, OS, VALID_BLE, VALID_OS
from test_companions import cyacd


class UpdateReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="xyf-update-review-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def package(self, name="new.zip", extra=()):
        path = self.root / name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(OS, VALID_OS)
            archive.writestr("firmware/blackfin/bin/os.version", b"1.2.5+0\n")
            for key, value in extra:
                archive.writestr(key, value)
        return path

    def test_baseline_comparison_is_not_installed_state(self):
        old = self.package("old.zip", [("firmware/blackfin/content/a", b"old"),
                                       ("firmware/blackfin/content/old-only", b"keep user data")])
        new = self.package(extra=[("firmware/blackfin/content/a", b"new"),
                                  ("firmware/blackfin/content/b", b"added")])
        report = review_update(new, old)
        self.assertFalse(report["installed_state_known"])
        self.assertFalse(report["authenticated"])
        self.assertEqual(report["components"]["os"]["change"], "unchanged")
        self.assertEqual(report["content"]["changes"], {"added": ["content/b"],
            "changed": ["content/a"], "unchanged": [], "previous_only": ["content/old-only"]})

    def test_missing_baseline_never_claims_added_or_upgrade(self):
        report = review_update(self.package())
        self.assertEqual(report["components"]["os"]["change"], "unknown")
        self.assertIsNone(report["content"]["changes"])
        self.assertFalse(report["content"]["comparison_available"])

    def test_semantic_failure_creates_no_staging_tree(self):
        variants = [[(BACKPLANE, b"bad"), ("firmware/backplane/bin/backplane.version", b"1.0.8+0")],
                    [(BLE, b"bad"), ("firmware/ble/bin/ble.version", b"1.0.2+0")],
                    [(BLE, VALID_BLE)],
                    [("firmware/ble/bin/ble.version", b"1.0.2+0")],
                    [("firmware/firmware.version", b"1.2.5")]]
        for index, rows in enumerate(variants):
            output = self.root / ("stage" + str(index))
            with self.assertRaises(ValueError):
                prepare_update(self.package(str(index) + ".zip", rows), output)
            self.assertFalse(output.exists())

    def test_companions_validated_but_no_programming_permission(self):
        rows = [(BACKPLANE, cyacd()), ("firmware/backplane/bin/backplane.version", b"1.0.8+0"),
                (BLE, VALID_BLE), ("firmware/ble/bin/ble.version", b"1.0.2+0"),
                ("firmware/blackfin/bin/bootloader.ldr", b"must be excluded")]
        report = prepare_update(self.package(extra=rows), self.root / "stage")
        self.assertEqual(report["components"]["backplane"]["structure"]["rows"], 367)
        self.assertIn("separate authorization", report["components"]["ble"]["next_step"])
        self.assertEqual(len(report["excluded"]), 1)
        self.assertIsNone(report["excluded"][0]["destination"])
        self.assertFalse((self.root / "stage" / "INCOMPLETE").exists())
        self.assertFalse(any("bootloader" in p.name for p in (self.root / "stage").rglob("*")))

    def test_archive_changed_between_review_and_stage_is_incomplete(self):
        original = self.package()
        other = self.package("changed.zip", [("firmware/blackfin/content/new", b"new")])
        from xy_forever.packages import stage_package
        def swap(path, output):
            Path(path).write_bytes(other.read_bytes())
            return stage_package(path, output)
        with patch("xy_forever.update_plan.stage_package", swap):
            with self.assertRaisesRegex(PackageError, "changed after update review"):
                prepare_update(original, self.root / "stage")
        self.assertTrue((self.root / "stage" / "INCOMPLETE").exists())

    def test_cli_refuses_stage_outside_private(self):
        import xyf_package
        with self.assertRaises(SystemExit) as failure:
            xyf_package.main([str(self.package()), "--stage", str(self.root / "stage")])
        self.assertEqual(failure.exception.code, 1)
        self.assertFalse((self.root / "stage").exists())
