"""Confirmed device and web uninstall through the shipping B writer using the actual 1.2.1 image."""

import os
import struct
import unittest
import zlib
from unittest.mock import patch

import pytest
from test_sim_adopt import AdoptBase
from test_sim_content_set import ENV, official_env, open_xy
from test_sim_mod_job import UNAVAILABLE
from test_sim_open_xy_cleanup import CATALOG, KEPT, OFFICIAL, seeder

from xy_forever import sim
from xy_forever import usb_proto_ids as ids


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class DeviceUninstall(AdoptBase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(os.environ, {ENV: official_env(OFFICIAL)}))
        self.start(seed=seeder({}))
        self.wait(lambda: self.lines('menu: line: '), 'first start')
        self.quiet()

    def start_job(self):
        code, payload = self.dev.call(ids.UNINSTALL_START, struct.pack('<I', 1))
        self.assertEqual(code, 0)
        self.assertEqual(len(payload), 20)
        return struct.unpack_from('<I', payload)[0]

    def press(self, key):
        for pressed in (1, 0):
            self.assertTrue(self.sim.control(f'realkey {key} {pressed}').get('ok'))

    @pytest.mark.sim
    def test_web_trigger_keeps_running_after_disconnect(self):
        """Kept: disconnect after acceptance, restore the root image and finish boot cleanup without the browser."""
        (self.root / 'os.ldr').write_bytes(b'earlier installer')
        (self.root / 'content/display/extra.svg').write_text('Open-XY')
        (self.root / 'content/op1_factory.json').write_bytes(open_xy())
        (self.root / 'bootloader.dev').touch()
        folder = self.root / 'xy-forever/saved'
        folder.mkdir()
        (folder / 'linked').symlink_to(self.root / 'content/user', target_is_directory=True)
        job = self.start_job()
        code, status = self.job()
        self.assertEqual((code, status['job']), (0, job))
        self.dev.t.close()
        self.dev = None
        self.wait(lambda: not (self.root / 'xy-forever').exists(), 'autonomous stock restore')
        self.assertEqual((self.root / 'os.ldr').read_bytes(), self.image)
        self.assertTrue((self.root / 'bootloader.dev').exists())
        self.assertFalse((self.root / 'content/display/extra.svg').exists())
        self.assertEqual((self.root / 'content/op1_factory.json').read_bytes(), CATALOG)
        for path, expected in KEPT.items():
            self.assertEqual((self.root / 'content' / path).read_bytes(), expected)
        raw = (self.dir / 'bootpart.bin').read_bytes()
        self.assertEqual(struct.unpack_from('<3I', raw), (3, len(self.image), zlib.crc32(self.image)))

    @pytest.mark.gate
    def test_settings_confirms_then_refuses_bad_stock(self):
        # Cancel is selected initially. A confirmation from this page starts the same job.
        (self.root / sim.STOCK_UPGRADE).write_bytes(b'wrong')
        before = self.snapshot()
        self.assertEqual(self.dev.call(ids.UNINSTALL_START, bytes(4))[0], ids.S_BAD_REQUEST)
        for key in (53, 51, 53, 51, 51):  # Settings, last setting, accept the default Cancel
            self.press(key)
        self.assertFalse(self.lines('menu: Checking official OS'))
        for key in (51, 54, 51):  # reopen, explicitly choose Uninstall
            self.press(key)
        self.wait(lambda: self.job()[1].get('detail') == 'OS differs from stock 1.2.1', 'bad OS refusal')
        self.assertEqual(self.job()[1]['phase'], 9)
        self.assertEqual(self.snapshot(), before)
