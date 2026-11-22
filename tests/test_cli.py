import contextlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from test_ldr import image

from xy_forever.__main__ import main
from xy_forever.validation import validate_package


class CliTests(unittest.TestCase):
    def test_invalid_os_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / 'bad.zip'
            with zipfile.ZipFile(package, 'w') as archive:
                archive.writestr('firmware/blackfin/bin/os.ldr', b'not a boot stream')
                archive.writestr('firmware/blackfin/bin/bootloader.ldr', b'excluded')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['inspect-package', str(package)]), 2)

    def test_structural_validation_is_distinct_from_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / 'custom.zip'
            with zipfile.ZipFile(package, 'w') as archive:
                archive.writestr('firmware/blackfin/bin/os.ldr', image([(4, 0x1000, 4, 0, b'abcd')]))
                archive.writestr('firmware/blackfin/bin/bootloader.ldr', b'excluded')
            _, report = validate_package(package)
            self.assertFalse(report['vendor_signature_verified'])
            self.assertFalse(report['hardware_tested'])
            self.assertEqual(report['roles']['excluded-bootloader'], 1)
