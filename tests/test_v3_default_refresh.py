"""Exercise the real USB default command and A/B cache updates with fake I/O."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever.source_text import unit_text  # noqa: E402
from xy_forever import hostbuild  # noqa: E402


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}\n', start) + 3]


class V3DefaultRefreshTest(unittest.TestCase):
    def test_same_session_default_and_failure_paths(self):
        usb = unit_text(ROOT / 'target/usb_service.cpp')
        boot = (ROOT / 'target/file_boot.cpp').read_text()
        ui = (ROOT / 'target/key_ui.cpp').read_text()
        fs = (ROOT / 'target/xyf_library_test.cpp').read_text()
        fs = fs[fs.index('struct File {'):fs.index('static uint8_t scratch')]
        owner = function(usb, 'void a_set_default(').replace(
            'cold_storage::Stock121ReadOnlySession&', 'TestFs&')
        for name, area in [('a_scan', 'scan_data'), ('a_plan', 'test_plan'), ('a_readback', 'readback')]:
            owner = owner.replace('state_write::' + name, 'reinterpret_cast<uintptr_t>(&' + area + ')')
        start = boot.index('if (xyf::state_write::collect(a_collector, command)) {')
        update = boot[boot.index('\n', start):boot.index('            session.touch(now());', start)]
        bodies = 'namespace usb_service {\n' + owner + '\n}\n'
        bodies += 'void a_commit() {\n' + update + '\n}\n'
        bodies += function(boot, 'U32 a_default_index(')
        bodies += 'namespace xyf_boot {\n' + function(boot, 'bool refresh_catalog()') + '\n}\n'
        bodies += 'namespace key_ui {\n' + function(ui, 'void refresh_catalog()') + '\n}\n'
        bodies += 'void settle_writes(bool = true) {}   // no write window in this harness\n'
        bodies += function(usb, 'const xyf::catalog::Item* catalog_item(')
        bodies += function(usb, 'U32 default_commit(U32 hash)')                  # set_default's A/B write
        bodies += function(usb, 'void set_default(uint8_t seq')
        with tempfile.TemporaryDirectory(prefix='xyf-default-refresh-') as temporary:
            out = Path(temporary)
            (out / 'fake_fs.inc').write_text(fs)
            (out / 'default.inc').write_text(bodies)
            binary = out / 'default'
            sources = ['xyf_store', 'xyf_info', 'xyf_library', 'xyf_state',
                       'xyf_crc32', 'xyf_state_write', 'xyf_catalog', 'xyf_menu', 'usb_proto']
            hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                           '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                           '-DXYF_ONE_SESSION=1', '-I', str(out), '-I', str(ROOT / 'target'),
                           *[str(ROOT / 'target' / (name + '.cpp')) for name in sources],
                           str(ROOT / 'tests/v3_default_refresh.cpp'), '-o', str(binary)],
                          check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertIn('default refresh checks passed', result.stdout)
