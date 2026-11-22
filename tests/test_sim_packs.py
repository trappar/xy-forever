"""Install and remove plain sound packs from the inbox without touching other content."""

import contextlib
import io
import json
import unittest
from unittest.mock import patch

import pytest
from sim_mods_page import MINUS, PLAY, PLUS
from sim_settings import Rows
from test_sim_mod_job import UNAVAILABLE

from xy_forever import package_store
from xy_forever import xyf_host as host


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class PackChanges(Rows, unittest.TestCase):
    def seed_pack(self, pack_id='fixture'):
        folder = self.root / 'xy-forever/inbox' / pack_id
        folder.mkdir(parents=True)
        (folder / 'manifest.json').write_text(json.dumps(dict(name='Test Sounds', folder=pack_id, version='1.0')))
        media = folder / 'samples/kits/one/hand.wav'  # six folders below yaffs2
        media.parent.mkdir(parents=True)
        media.write_bytes(b'RIFF' + bytes(range(128)))
        return folder

    def packs(self, *args):
        with (
            patch.object(host, 'connect', return_value=(self.dev, 'sim', self.dev.hello_info.text)),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            code = host.main(['packs', *args])
        return code, out.getvalue()

    def confirm_pack(self):
        self.press(PLAY)
        self.press(PLUS)
        self.press(PLAY)

    @pytest.mark.sweep
    def test_menu_cut_on_detach_resumes_cleanup(self):
        self.seed_pack()
        target = self.root / 'content/dlc/fixture'
        self.assertEqual(self.packs('install', 'fixture', '--yes')[0], 0)
        before = (self.root / 'content/op1_factory.json').read_bytes()
        self.settings_page()
        self.press(MINUS)
        self.press(PLAY)
        self.until(lambda: self.count('pack page 0: 1 entries, more=0'), 'installed pack')
        cuts = self.sim.status()['cuts']
        self.sim.set('rename_fault_kind', 2)  # power loss immediately after the rename
        self.sim.set('rename_fault', 1)
        self.confirm_pack()
        self.until(lambda: self.sim.status()['cuts'] == cuts + 1 and self.sim.status()['listening'], 'restart')
        self.reconnect()
        self.assertFalse(target.exists())
        self.until(lambda: not any(name.startswith('pkg-v1-') for name in self.leftovers()), 'scratch cleanup')
        self.assertEqual((self.root / 'content/op1_factory.json').read_bytes(), before)

    @pytest.mark.sweep
    def test_renamed_away_before_interrupted_cleanup(self):
        self.seed_pack()
        self.assertEqual(self.packs('install', 'fixture', '--yes')[0], 0)
        with patch.object(package_store.Session, 'erase', side_effect=OSError('interrupted')):
            code, out = self.packs('remove', 'fixture', '--yes')
        self.assertEqual(code, 0, out)
        self.assertIn('cleanup pending: interrupted', out)
        self.assertFalse((self.root / 'content/dlc/fixture').exists())
        self.assertTrue((self.root / 'content/op1_factory.json').is_file())
