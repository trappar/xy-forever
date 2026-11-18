import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("capture_session", ROOT / "scripts/capture_session.py")
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class CaptureSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "checkout"
        self.root.mkdir()
        self.out = Path(self.temp.name) / "reports" / "capture"
        self.patch = patch.object(capture.xyf_paths, "REPORTS", self.out.parent)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.device = Mock()
        self.device.call.return_value = (0, b"status bytes")
        self.connect = Mock(return_value=(self.device, "fake", "XY Forever"))

    def run_capture(self, paths=()):
        with patch.object(capture.host, "decode_status", return_value={"usb_service": {"version": 4}}):
            return capture.capture(self.out, paths, 1, self.connect)

    def test_default_does_not_connect_or_create_files(self):
        with patch.object(capture, "capture") as live, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(capture.main(["--out", str(self.out), "--state"]), 0)
        live.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_read_only_commands_and_missing_report_are_recorded(self):
        self.device.read_all.side_effect = [(0, b"default=stock\n"), (8, b"")]
        paths = ["/yaffs2/xy-forever/state-a.txt", "/yaffs2/xy-forever/log/prod-069-a.bin"]
        manifest = self.run_capture(paths)
        self.device.call.assert_called_once_with(capture.P.STATUS)
        self.assertEqual(self.device.read_all.call_count, 2)
        self.device.t.close.assert_called_once()
        self.assertTrue(manifest["complete"])
        self.assertEqual(manifest["reads"][1]["status"], "not_found")
        self.assertEqual((self.out / "state-a.txt").read_bytes(), b"default=stock\n")

    def test_transport_failure_stops_and_preserves_partial_evidence(self):
        self.device.read_all.side_effect = [(0, b"state"), (12, b"partial")]
        with self.assertRaisesRegex(RuntimeError, "link"):
            self.run_capture(["/yaffs2/xy-forever/state-a.txt", "/yaffs2/xy-forever/state-b.txt", "unused"])
        self.assertEqual(self.device.read_all.call_count, 2)
        self.assertFalse(json.loads((self.out / "capture.json").read_text())["complete"])
        self.assertEqual((self.out / "state-b.txt.partial").read_bytes(), b"partial")
        self.device.t.close.assert_called_once()

    def test_existing_or_checkout_destination_refused_before_connect(self):
        with self.assertRaises(ValueError):
            capture.capture(self.root / "bad", [], 1, self.connect)
        self.out.mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            self.run_capture()
        (self.out / ".git").mkdir()
        with self.assertRaisesRegex(ValueError, "outside every checkout"):
            capture.capture(self.out / "nested", [], 1, self.connect)
        self.connect.assert_not_called()

    def test_status_error_aborts_before_any_read(self):
        self.device.call.return_value = (3, b"")
        with self.assertRaisesRegex(RuntimeError, "STATUS"):
            self.run_capture(["unused"])
        self.device.read_all.assert_not_called()
        self.device.t.close.assert_called_once()

    def test_report_names_cannot_escape_read_scope(self):
        with self.assertRaises(ValueError):
            capture.plan(reports=["../keys"])
        self.assertEqual(len(capture.plan(True, ["069"])), 4)
        self.assertEqual(capture.plan(reports=["prod", "069"]), ["/yaffs2/xy-forever/log/prod-a.bin", "/yaffs2/xy-forever/log/prod-b.bin",
                                                                "/yaffs2/xy-forever/log/prod-069-a.bin", "/yaffs2/xy-forever/log/prod-069-b.bin"])


if __name__ == "__main__":
    unittest.main()
