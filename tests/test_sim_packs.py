"""Install and remove plain sound packs from the inbox without touching other content."""
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import xyf_host as host
from test_sim_mod_job import UNAVAILABLE
from test_sim_settings import Rows
from test_sim_mods_page import MINUS, PLAY, PLUS, SHIFT
from xy_forever import package_store


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class PackChanges(Rows, unittest.TestCase):
    def seed_pack(self, pack_id='fixture'):
        folder = self.root / 'xy-forever/inbox' / pack_id
        folder.mkdir(parents=True)
        (folder / 'manifest.json').write_text(json.dumps(dict(name='Test Sounds', folder=pack_id, version='1.0')))
        media = folder / 'samples/kits/one/hand.wav'                    # six folders below yaffs2
        media.parent.mkdir(parents=True)
        media.write_bytes(b'RIFF' + bytes(range(128)))
        return folder

    def packs(self, *args):
        with patch.object(host, 'connect', return_value=(self.dev, 'sim', self.dev.hello_info.text)), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = host.main(['packs', *args])
        return code, out.getvalue()

    def test_serial_round_trip_and_duplicate(self):
        source = self.seed_pack()
        sample = (source / 'samples/kits/one/hand.wav').read_bytes()
        marker = self.root / 'bootloader.dev'
        if not marker.exists():
            marker.write_bytes(b'simulator recovery marker')
        updater = self.root / 'upgrade/keep.txt'
        updater.parent.mkdir(exist_ok=True)
        updater.write_bytes(b'simulator updater fixture')
        protected = {p: p.read_bytes() for p in (marker, updater, self.root / 'content/op1_factory.json')}
        target = self.root / 'content/dlc/fixture'
        code, out = self.packs('install', 'fixture', '--yes')
        self.assertEqual(code, 0, out)
        self.assertFalse(source.exists())
        self.assertEqual((target / 'samples/kits/one/hand.wav').read_bytes(), sample)
        self.seed_pack()
        self.assertIn('occupied', self.packs('install', 'fixture', '--yes')[1])
        code, out = self.packs('remove', 'fixture', '--yes')
        self.assertEqual(code, 0, out)
        self.assertFalse(target.exists())
        self.assertTrue(source.is_dir())
        for path, expected in protected.items():
            self.assertEqual(path.read_bytes(), expected)

    def test_refuse_wrong_identity_depth_and_special_entry(self):
        source = self.seed_pack()
        manifest = source / 'manifest.json'
        for value in ('../upgrade', 'other'):
            manifest.write_text(json.dumps(dict(folder=value)))
            self.assertIn('does not match', self.packs('install', 'fixture', '--yes')[1])
        manifest.write_text('{}')
        (source / 'samples/kits/one/deeper').mkdir()
        self.assertIn('device limits', self.packs('install', 'fixture', '--yes')[1])
        (source / 'samples/kits/one/deeper').rmdir()
        (source / 'stray').symlink_to(self.root / 'bootloader.dev')
        self.assertIn('unsupported pack entry', self.packs('install', 'fixture', '--yes')[1])
        self.assertFalse((self.root / 'content/dlc/fixture').exists())
        self.assertEqual(self.packs('remove', '../upgrade', '--yes')[0], 2)

    def test_destination_must_be_a_real_directory(self):
        source = self.seed_pack()
        outside = self.root / 'upgrade'
        outside.mkdir(exist_ok=True)
        (self.root / 'content/dlc').symlink_to(outside, target_is_directory=True)
        code, text = self.packs('install', 'fixture', '--yes')
        self.assertNotEqual(code, 0)
        self.assertIn('expected a directory', text)
        self.assertTrue((source / 'samples/kits/one/hand.wav').is_file())
        self.assertFalse((outside / 'fixture').exists())

    def test_manifest_limits_and_spelling(self):
        source = self.seed_pack()
        manifest = source / 'manifest.json'
        too_deep = '{"nested":' + '[' * 32 + 'null' + ']' * 32 + '}'
        for text in (too_deep, '[]', '{"folder":null}', '{"folder":"wrong","folder":"fixture"}', '{"n": NaN}', '{}'.ljust(4097)):
            manifest.write_text(text)
            code, detail = self.packs('install', 'fixture', '--yes')
            self.assertNotEqual(code, 0, detail)
            self.assertTrue(source.is_dir())
        manifest.write_text(r'{"f\u006flder":"fixt\u0075re"}'.ljust(4096))
        code, detail = self.packs('install', 'fixture', '--yes')
        self.assertEqual(code, 0, detail)

    def switch_pack_list(self):
        self.key(SHIFT, 1)
        self.press(PLAY)
        self.key(SHIFT, 0)

    def confirm_pack(self):
        self.press(PLAY)
        self.press(PLUS)
        self.press(PLAY)

    def test_menu_install_remove_and_refusals(self):
        source = self.seed_pack()
        target = self.root / 'content/dlc/fixture'
        self.settings_page()
        self.press(MINUS)
        self.press(PLAY)
        self.until(lambda: self.count('pack page 0: 0 entries, more=0'), 'installed list')
        self.switch_pack_list()
        self.until(lambda: self.count('pack id: fixture'), 'inbox item')
        self.press(PLAY)
        self.press(PLAY)                                      # Cancel is selected initially
        self.assertTrue(source.is_dir())
        self.confirm_pack()
        self.until(lambda: self.count('pack result: Pack installed.'), 'menu install')
        self.assertTrue((target / 'samples/kits/one/hand.wav').is_file())
        self.seed_pack()
        shown = self.count('pack id: fixture')
        self.switch_pack_list()
        self.until(lambda: self.count('pack id: fixture') > shown, 'installed item')
        self.switch_pack_list()
        self.until(lambda: self.count('pack id: fixture') > shown + 1, 'duplicate inbox item')
        self.confirm_pack()
        self.until(lambda: self.count('pack result: Pack ID already occupied.'), 'duplicate refused')
        shown = self.count('pack id: fixture')
        self.switch_pack_list()
        self.until(lambda: self.count('pack id: fixture') > shown, 'back on installed item')
        self.confirm_pack()
        self.until(lambda: self.count('pack result: Pack removed.'), 'menu remove')
        self.assertFalse(target.exists())
        self.assertTrue(source.is_dir())
        # A manifest can only describe its own folder.
        (source / 'manifest.json').write_text('{"folder":"different"}')
        shown = self.count('pack id: fixture')
        self.switch_pack_list()
        self.until(lambda: self.count('pack id: fixture') > shown, 'bad inbox item')
        self.confirm_pack()
        self.until(lambda: self.count('pack result: Invalid manifest or folder ID.'), 'manifest refused')
        self.assertFalse(target.exists())

    def test_native_manifest_is_not_a_sound_pack(self):
        source = self.seed_pack()
        manifest = source / 'manifest.json'
        target = self.root / 'content/dlc/fixture'
        for metadata in (
            '{"schema":"xy-forever.package","schema_version":1,"type":"mod"}',
            '{"schema":"xy-forever.package","schema":"vendor","schema_version":1}',
            r'{"sch\u0065ma":"xy-forever.pack\u0061ge","schema_version":2,"type":"mod"}',
        ):
            manifest.write_text(metadata)
            code, detail = self.packs('install', 'fixture', '--yes')
            self.assertNotEqual(code, 0, detail)
            self.assertIn('native package', detail)
            self.assertEqual(manifest.read_text(), metadata)
            self.assertFalse(target.exists())
        self.settings_page()
        self.press(MINUS)
        self.press(PLAY)
        self.until(lambda: self.count('pack page 0: 0 entries, more=0'), 'installed list')
        self.switch_pack_list()
        self.until(lambda: self.count('pack id: fixture'), 'native inbox item')
        self.confirm_pack()
        refused = 'pack result: Invalid manifest or folder ID.'
        installed = 'pack result: Pack installed.'
        self.until(lambda: self.count(refused) or self.count(installed), 'pack outcome')
        self.assertTrue(self.count(refused))
        self.assertEqual(manifest.read_text(), metadata)
        self.assertFalse(target.exists())

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
        self.sim.set('rename_fault_kind', 2)                 # power loss immediately after the rename
        self.sim.set('rename_fault', 1)
        self.confirm_pack()
        self.until(lambda: self.sim.status()['cuts'] == cuts + 1 and self.sim.status()['listening'], 'restart')
        self.reconnect()
        self.assertFalse(target.exists())
        self.until(lambda: not any(name.startswith('pkg-v1-') for name in self.leftovers()), 'scratch cleanup')
        self.assertEqual((self.root / 'content/op1_factory.json').read_bytes(), before)

    def test_renamed_away_before_interrupted_cleanup(self):
        self.seed_pack()
        self.assertEqual(self.packs('install', 'fixture', '--yes')[0], 0)
        with patch.object(package_store.Session, 'erase', side_effect=OSError('interrupted')):
            code, out = self.packs('remove', 'fixture', '--yes')
        self.assertEqual(code, 0, out)
        self.assertIn('cleanup pending: interrupted', out)
        self.assertFalse((self.root / 'content/dlc/fixture').exists())
        self.assertTrue((self.root / 'content/op1_factory.json').is_file())
