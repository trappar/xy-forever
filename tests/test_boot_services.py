"""Host checks for the loader path rule and the update-key reader; no target or hardware calls."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402


class BootServicesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="xyf-boot-services-")
        cls.binary = Path(cls.temp.name) / "boot-services"
        hostbuild.run([
            "/usr/bin/clang++", "-std=c++11", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
            str(ROOT / "target/boot_services.cpp"),
            str(ROOT / "target/boot_services_test.cpp"), "-o", str(cls.binary),
        ], check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_case(self, case):
        result = subprocess.run([str(self.binary), case], check=True,
                                capture_output=True, text=True)
        self.assertRegex(result.stdout, r"[1-9][0-9]* checks passed")

    def test_loader_namespace_paths(self): self.run_case("path")
    def test_otp_selector_capacity_and_failed_key_zeroing(self): self.run_case("otp")
    def test_key_provider_is_refused_off_the_device(self): self.run_case("binding")


if __name__ == "__main__":
    unittest.main()
