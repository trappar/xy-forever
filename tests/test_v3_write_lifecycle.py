"""Execute the device lifecycle functions against fault-injected YAFFS calls.

Only the Blackfin memory barrier is replaced. The function bodies under test
come directly from the production sources, including server open/close handling.
This validates control flow, not the stock implementation or physical storage.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}\n', start) + 3]


class V3WriteLifecycleTest(unittest.TestCase):
    def test_success_and_fault_paths(self):
        storage = (ROOT / 'target/cold_storage.cpp').read_text()
        server = ''.join((ROOT / 'target' / n).read_text()
                          for n in ('storage_server.cpp', 'storage_server_engine.ipp'))
        bodies = '\n'.join(function(storage, 'Result Stock121ReadOnlySession::' + name)
                           for name in ('open_write_window(', 'close_write_window(', 'reopen_read_only('))
        bodies = bodies.replace('__asm__ volatile("ssync;" ::: "memory");', '')
        bodies = 'namespace cold_storage {\n' + bodies + '\n}\n'
        bodies += '\n'.join(function(server, name) for name in ('bool open_window(', 'void close_engine('))
        with tempfile.TemporaryDirectory(prefix='xyf-write-lifecycle-') as temporary:
            out = Path(temporary)
            (out / 'lifecycle.inc').write_text(bodies)
            binary = out / 'lifecycle'
            hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                           '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                           '-DXYF_ONE_SESSION=1', '-DXYF_USB_FILE_WRITE=1', '-I', str(out),
                           str(ROOT / 'tests/v3_write_lifecycle.cpp'), '-o', str(binary)],
                          check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertIn('write lifecycle checks passed', result.stdout)
