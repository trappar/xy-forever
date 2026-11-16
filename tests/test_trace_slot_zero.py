"""Large zero diagnostic slot lookup (census dump transport); host only."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_trace_slot import data_record, image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402


class TraceSlotZeroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='trace-slot-zero-')
        cls.directory = Path(cls.temp.name)
        cls.binary = cls.directory / 'bridge'
        hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                       '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                       str(ROOT / 'native/trace_slot_zero.cpp'), str(ROOT / 'native/trace_slot.cpp'),
                       str(ROOT / 'native/ldr.cpp'),
                       str(ROOT / 'native/memory.cpp'), str(ROOT / 'tests/trace_slot_zero_bridge.cpp'),
                       '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def locate(self, data, target, count):
        path = self.directory / 'image.ldr'
        path.write_bytes(data)
        result = subprocess.run([str(self.binary), str(path), hex(target), str(count)],
                                capture_output=True, text=True, check=True)
        status, offset = result.stdout.split()
        return status, int(offset)

    def test_finds_unique_zero_slot_of_large_size(self):
        size = 0x40000
        data = image([data_record(0x20000, bytes(size))])
        status, offset = self.locate(data, 0x20000, size)
        self.assertEqual(status, 'ok')
        self.assertEqual(data[offset - 16 + 4:offset - 16 + 8], (0x20000).to_bytes(4, 'little'))

    def test_refusals(self):
        size = 0x2000
        self.assertEqual(self.locate(image([data_record(0x20000, b'\1' + bytes(size - 1))]), 0x20000, size)[0],
                         'preimage_mismatch')
        self.assertEqual(self.locate(image([data_record(0x20000, bytes(size))] * 2), 0x20000, size)[0],
                         'duplicate_slot')
        self.assertEqual(self.locate(image([data_record(0x20000, bytes(size)),
                                            data_record(0x20100, bytes(16))]), 0x20000, size)[0],
                         'overlapping_record')
        self.assertEqual(self.locate(image([data_record(0x20000, bytes(size))]), 0x30000, size)[0],
                         'missing_slot')
        self.assertEqual(self.locate(image([data_record(0x20000, bytes(size))]), 0x20000, 0x400001)[0],
                         'invalid_argument')
        self.assertEqual(self.locate(image([data_record(0x20000, bytes(size))]) + b'x', 0x20000, size)[0],
                         'invalid_image')


if __name__ == '__main__':
    unittest.main()
