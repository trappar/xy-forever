"""Set-default planning and command decoding (host build of the device C++)."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from xy_forever.source_text import unit_text
from xy_forever import hostbuild

ROOT = Path(__file__).resolve().parents[1]


class StateWriteTest(unittest.TestCase):
    def test_planner(self):
        with tempfile.TemporaryDirectory(prefix='xyf-state-write-') as tmp:
            binary = Path(tmp) / 'sw'
            hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                           '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                           *(str(ROOT / 'target' / f) for f in ('xyf_store.cpp', 'xyf_info.cpp', 'xyf_crc32.cpp', 'xyf_state.cpp',
                                                                 'xyf_library.cpp', 'xyf_state_write.cpp', 'xyf_state_write_library.cpp',
                                                                 'xyf_state_write_test.cpp')),
                            '-o', str(binary)], check=True, capture_output=True, text=True)
            out = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertRegex(out.stdout, r'[1-9][0-9]* checks passed')

    def test_write_window_is_narrow(self):
        """Source pins: the only write path, its flags, its path allowlist, gates re-installed."""
        src = (ROOT / 'target/cold_storage.cpp').read_text()
        body = src[src.index('Result Stock121ReadOnlySession::write_state_slot('):
                   src.index('Result Stock121ReadOnlySession::open_write_window(')]   # MTP window: test_xyf_mtp
        self.assertIn('if (!state_slot_path(path) || !data || !readback || !count || count > 4096) return Result::write_refused;', body)
        self.assertIn('y_open(path, 0x241, 0x180)', body)                # stock "wb": O_WRONLY|O_CREAT|O_TRUNC
        self.assertEqual(body.count('y_write('), 1)                      # one yaffs_write call
        self.assertEqual(body.count('y_mount(device_, 0)'), 1)           # one read-write mount
        self.assertEqual(src.count('0x889fbb0cu'), 2)                    # yaffs_write: span table + y_write only
        self.assertIn('reinterpret_cast<U32>(&deny_media_change)', body)  # gates re-installed
        for banned in ('889fae1c', '889fa5f2', '889fad92', '889fac36', '889fab0e'):   # unlink/rmdir/mkdir-like/sync-like
            self.assertNotIn(banned, body)
        allow = src[src.index('bool Stock121ReadOnlySession::state_slot_path('):]             # the last function of the file
        self.assertEqual(sorted(re.findall(r'"(/yaffs2/[^"]+)"', allow)),
                         ['/yaffs2/xy-forever/state-a.txt', '/yaffs2/xy-forever/state-b.txt'])
        # Exactly two callers: the menu (file_boot.cpp, only in XYF_STATE_WRITE
        # builds: none today) and the USB service (usb_service.cpp); never cold.cpp
        # or any normal-boot code.
        users = sorted(p.name for p in (ROOT / 'target').glob('*.cpp') if 'write_state_slot(' in unit_text(p))
        self.assertEqual(users, ['cold_storage.cpp', 'file_boot.cpp', 'usb_service.cpp'])
        us_src = unit_text(ROOT / 'target/usb_service.cpp')     # Test 042: USB write compiled out by default
        self.assertLess(us_src.index('#if defined(XYF_USB_SET_DEFAULT)\nvoid a_set_default'), us_src.index('storage.write_state_slot('))
        fb = unit_text(ROOT / 'target/file_boot.cpp')
        at = fb.index('storage.write_state_slot(')
        self.assertNotIn('#endif', fb[fb.rindex('#if defined(XYF_STATE_WRITE)\n', 0, at):at])
        us = unit_text(ROOT / 'target/usb_service.cpp')
        self.assertIn('void a_set_default(cold_storage::Stock121ReadOnlySession& storage, uint32_t hash) {', us)


if __name__ == '__main__':
    unittest.main()
