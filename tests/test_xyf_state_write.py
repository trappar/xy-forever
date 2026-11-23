"""The set-default write window (the planning and command decoding: tests/native/xyf_state_write_test.cpp)."""

import re
import unittest
from pathlib import Path

from xy_forever.source_text import unit_text

ROOT = Path(__file__).resolve().parents[1]


class StateWriteTest(unittest.TestCase):
    def test_write_window_is_narrow(self):
        """Source pins: the only write path, its flags, its path allowlist, gates re-installed."""
        src = (ROOT / 'target/cold_storage.cpp').read_text()
        body = src[
            src.index('Result Stock121ReadOnlySession::write_state_slot(') : src.index(
                'Result Stock121ReadOnlySession::open_write_window('
            )
        ]  # MTP window: test_xyf_mtp
        self.assertIn(
            'if (!state_slot_path(path) || !data || !readback || !count || count > 4096) return Result::write_refused;',
            body,
        )
        self.assertIn('y_open(path, 0x241, 0x180)', body)  # stock "wb": O_WRONLY|O_CREAT|O_TRUNC
        self.assertEqual(body.count('y_write('), 1)  # one yaffs_write call
        self.assertEqual(body.count('y_mount(device_, 0)'), 1)  # one read-write mount
        self.assertEqual(unit_text(ROOT / 'target/cold_storage.cpp').count('0x889fbb0cu'), 2)  # span table + y_write
        self.assertIn('reinterpret_cast<U32>(&deny_media_change)', body)  # gates re-installed
        for banned in ('889fae1c', '889fa5f2', '889fad92', '889fac36', '889fab0e'):  # unlink/rmdir/mkdir-like/sync-like
            self.assertNotIn(banned, body)
        allow = src[src.index('bool Stock121ReadOnlySession::state_slot_path(') :]  # the last function of the file
        self.assertEqual(
            sorted(re.findall(r'"(/yaffs2/[^"]+)"', allow)),
            ['/yaffs2/xy-forever/state-a.txt', '/yaffs2/xy-forever/state-b.txt'],
        )
        # Exactly two callers: the menu (file_boot.cpp, only in XYF_STATE_WRITE
        # builds: none today) and the USB service (usb_service.cpp); never cold.cpp
        # or any normal-boot code.
        users = sorted(p.name for p in (ROOT / 'target').glob('*.cpp') if 'write_state_slot(' in unit_text(p))
        self.assertEqual(users, ['cold_storage.cpp', 'file_boot.cpp', 'usb_service.cpp'])
        us_src = unit_text(ROOT / 'target/usb_service.cpp')  # Test 042: USB write compiled out by default
        self.assertLess(
            us_src.index('#if defined(XYF_USB_SET_DEFAULT)\nvoid a_set_default'),
            us_src.index('storage.write_state_slot('),
        )
        fb = unit_text(ROOT / 'target/file_boot.cpp')
        at = fb.index('storage.write_state_slot(')
        self.assertNotIn('#endif', fb[fb.rindex('#if defined(XYF_STATE_WRITE)\n', 0, at) : at])
        us = unit_text(ROOT / 'target/usb_service.cpp')
        self.assertIn('void a_set_default(cold_storage::Stock121ReadOnlySession& storage, uint32_t hash) {', us)


if __name__ == '__main__':
    unittest.main()
