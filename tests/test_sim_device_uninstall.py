"""Confirmed device and web uninstall through the shipping B writer using the actual 1.2.1 image."""

import os
import shutil
import struct
import unittest
import zlib
from unittest.mock import patch

import pytest
from conftest import verified_build
from test_sim_adopt import AdoptBase
from test_sim_content_set import ENV, official_env, open_xy
from test_sim_mod_job import UNAVAILABLE
from test_sim_open_xy_cleanup import CATALOG, KEPT, OFFICIAL, seeder
from test_sim_stock_writer import represented
from test_xyf_stage2_service import stage2_file

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

    @pytest.mark.gate
    def test_first_connection_during_settings_uninstall_keeps_the_stock_stage(self):
        extra = self.root / 'xy-forever/stage2'
        extra.mkdir(exist_ok=True)
        (extra / 'main.xys2').write_bytes(stage2_file())
        (self.root / 'os.ldr').write_bytes(b'installer')
        self.dev.t.close()
        self.dev = None
        self.sim.control('power play')
        self.sim.wait_listening(10)
        self.quiet()
        for key in (53, 51, 53, 51, 54, 51):
            self.press(key)
        self.wait(lambda: self.lines('menu: usb line: Checking official OS'), 'local stock read')
        previous = self.sim.status()['stage2_opens']
        self.connect()  # first HELLO this boot, while Settings owns staging
        self.assertEqual(self.sim.status()['stage2_opens'], previous)
        self.wait(lambda: not (self.root / 'xy-forever').exists(), 'stock writer and cleanup')
        self.assertEqual((self.root / 'os.ldr').read_bytes(), self.image)

    @pytest.mark.gate
    def test_failed_checkpoint_stops_before_writing_boot(self):
        (self.root / 'os.ldr').write_bytes(b'installer')
        self.start_job()
        self.wait(lambda: self.lines('menu: usb line: Restoring root OS file'), 'the private upload')
        self.sim.set('fail_unmount', 1)
        self.wait(lambda: self.job()[1]['phase'] == 9, 'failed filesystem close')
        self.assertEqual(self.job()[1]['detail'], 'Storage close failed')
        self.assertTrue((self.root / 'xy-forever/firmware/opxy-121/os.ldr').exists())
        raw = self.dir / 'bootpart.bin'
        self.assertFalse(raw.exists() and any(raw.read_bytes()[:512]))

    @pytest.mark.gate
    def test_content_root_must_be_a_real_folder(self):
        source = self.root / 'elsewhere'
        (self.root / 'content').rename(source)
        (self.root / 'content').symlink_to(source, target_is_directory=True)
        (source / 'keep').write_text('owner file')
        self.start_job()
        self.wait(lambda: self.job()[1]['phase'] == 9, 'root link refusal')
        self.assertEqual(self.job()[1]['detail'], 'Invalid content folder')
        self.assertEqual((source / 'keep').read_text(), 'owner file')

    @pytest.mark.sweep
    def test_cuts_across_root_publication_boot_commit_and_cleanup(self):
        """The device writer touches only the inactive slot until its commit. Check both complete images at each cut."""
        manager = (verified_build(self) / 'candidate.ldr').read_bytes()
        (self.root / 'os.ldr').write_bytes(manager)
        seed = self.dir / 'installed'
        shutil.copytree(self.root, seed)
        media = self.dir / 'bootpart.bin'
        # The existing, active XY Forever boot image in slot 1 (the simulator accepts plaintext fixtures).
        header = bytearray(512)
        struct.pack_into('<3I', header, 0, 3, len(manager), zlib.crc32(manager))
        header[12] = 1
        first_image = header + manager
        # Persistent changes in this clean-content run: create/write/close temporary OS, rename, stock
        # writer erase blocks / 128-sector body writes / commit / retire, then manager tree deletions.
        pieces = (len(self.image) + 1023) // 1024
        publish = 1 + pieces + 1 + 1
        erases = (len(self.image) + 512 + 65535) // 65536
        bodies = (len(self.image) // 512 + 1 + 127) // 128
        commit = publish + erases + bodies + 1
        for at in (1, pieces // 2, publish - 1, publish, commit - 1, commit, commit + 1, commit + 3):
            with self.subTest(cut=at):
                self.dev.t.close()
                self.sim.stop()
                shutil.rmtree(self.root)
                shutil.copytree(seed, self.root)
                media.write_bytes(first_image)
                self.sim = sim.Simulator(self.root, media, binary=self.binary, log=self.dir / 'cut.log').start()
                self.connect()
                self.quiet()
                self.sim.set('cut_after', at)
                self.start_job()
                self.wait(lambda: self.sim.status()['cuts'] == 1, 'simulated power loss')
                raw = media.read_bytes()
                # Which complete images can the real bootloader choose? It tests slot 1 first.
                old = raw[12]
                second = raw[0x400000:]
                new = len(second) >= 512 and second[12]
                self.assertEqual(bool(old), at <= commit)
                self.assertEqual(bool(new), at >= commit)
                if old:
                    self.assertEqual(raw[512 : 512 + len(manager)], manager)
                    self.assertTrue((self.root / 'xy-forever/firmware/opxy-121/os.ldr').is_file())
                if new:
                    self.assertEqual(second[512 : 512 + len(self.image)], represented(self.image))
                self.assertEqual((self.root / 'os.ldr').read_bytes(), self.image if at >= publish else manager)
                for path, expected in KEPT.items():
                    self.assertEqual((self.root / 'content' / path).read_bytes(), expected)
