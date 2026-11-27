"""In the simulator: the first start after Track 5 on an OP-XY that had Open-XY (#334). After the adoption (test_sim_adopt)
a light check lists /yaffs2/content/ and reads only op1_factory.json: Open-XY's files (its machines' presets, screens
and aux presets) are removed with the folders they leave empty, and its catalog entries are taken out, accepted only
at the official SHA-256 (#331's rule). user/, dlc/ and a folder that was already empty stay. xy-forever/cleanup.txt
says what was done, for the install page, and the menu says it after PLAY. Official content is left as it is, and a
catalog that cannot be put back writes nothing and stops with the device's words. The official set is the simulator's
synthetic content, given by XYF_SIM_OFFICIAL_CONTENT (test_sim_content_set). Needs clang++ and the private stock 1.2.1
images (skipped without them). No device access."""

import os
import unittest

import pytest
import test_sim_content_switch as CS
from test_modpatch import CATALOG
from test_sim_adopt import AdoptBase
from test_sim_config import mark_text
from test_sim_content_set import ENV, official_env, open_xy
from test_sim_mod_job import UNAVAILABLE

from xy_forever import modsend as MS
from xy_forever import sim

OFFICIAL = {
    'op1_factory.json': CATALOG,
    'display/synth.svg': b'<svg id="synth"/>',
    'presets/synth/init.json': b'{"synth": 1}',
    'samples/kick/one.wav': bytes(range(200)),
}
OPEN_XY = {  # Open-XY's files beside the official ones
    'display/fm2op.svg': b'<svg/>',
    'presets/fm2op/init.json': b'{}',
    'presets/fm2op/bright.json': b'{"b": 1}',
    'aux_presets/cv/one.json': b'{}',
    'aux_presets/conductor/two.json': b'{}',
}
KEPT = {'user/mine.json': b'{"mine": 1}', 'dlc/pack/sound.wav': b'pack'}  # the owner's, a sound pack
EMPTY = 'projects/empty'  # a folder that was empty: never removed


def seeder(extras, catalog=CATALOG):
    def seed(root):
        for path, data in {**OFFICIAL, **KEPT, **extras, 'op1_factory.json': catalog}.items():
            (root / 'content' / path).parent.mkdir(parents=True, exist_ok=True)
            (root / 'content' / path).write_bytes(data)
        (root / 'content' / EMPTY).mkdir(parents=True, exist_ok=True)

    return seed


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class FirstStartCleanup(AdoptBase):
    def setUp(self):
        super().setUp()
        before = os.environ.get(ENV)
        os.environ[ENV] = official_env(OFFICIAL)
        self.addCleanup(lambda: os.environ.pop(ENV) if before is None else os.environ.update({ENV: before}))

    def official(self):
        files = {**OFFICIAL, **KEPT}
        return files, {p.rsplit('/', k)[0] for p in files for k in (1, 2) if p.count('/') >= k} | {EMPTY, 'projects'}

    def note(self):
        path = self.root / 'xy-forever/cleanup.txt'
        return path.read_bytes() if path.exists() else None

    @pytest.mark.sim
    def test_open_xy_leftovers_are_removed_at_the_first_start(self):
        """Kept: every OP-XY moving from Open-XY starts from the official content this way."""
        self.start(seed=seeder(OPEN_XY, open_xy()))
        self.wait(lambda: self.lines('menu: line: '), 'the first start')
        self.assertEqual(
            self.lines('menu: line: '), ["Removed 5 files from Open-XY, put back OS 1.2.1's op1_factory.json."]
        )
        self.adopted()
        self.assertEqual(CS.content(self.root), self.official())
        self.assertEqual(self.note(), b'files=5\ncatalog=1\n')
        self.assertEqual(list((self.root / 'xy-forever/tmp').glob('put-*')), [])

    @pytest.mark.gate  # the official content: the simulator core keeps the adoption
    def test_official_content_is_left_as_it_is(self):
        """A stock OP-XY records a clean scan and leaves content alone."""
        self.start(seed=seeder({}))
        self.wait(lambda: self.lines('menu: line: '), 'the first start')
        self.assertEqual(self.lines('menu: line: '), ['OS 1.2.1 is in the library and is the default.'])
        self.assertEqual(CS.content(self.root), self.official())
        self.assertEqual(self.note(), b'files=0\ncatalog=0\n')

    def old_install(self, root):
        sim.seed(root, self.image)
        (root / 'xy-forever/cleanup.txt').unlink()
        (root / 'xy-forever/converged.txt').write_text(mark_text(root, 'opxy-121'))
        seeder(OPEN_XY, open_xy())(root)

    @pytest.mark.sim
    def test_upgrade_cleans_once_before_the_normal_boot(self):
        """An old default and matching fingerprint must still go through the cleanup on upgrade."""
        self.start(play=False, seed=self.old_install)
        self.wait(self.in_os, 'first boot after upgrading')
        self.assertEqual(CS.content(self.root), self.official())
        self.assertEqual(self.note(), b'files=5\ncatalog=1\n')
        self.assertFalse((self.root / 'xy-forever/firmware/opxy-121/content.sha256').exists())
        # A later file is left alone: the upgrade check has finished.
        extra = self.root / 'content/display/later.svg'
        extra.write_bytes(b'later')
        power_ons = self.sim.status()['power_ons']
        self.sim.control('power plain')
        self.wait(lambda: self.sim.status()['power_ons'] > power_ons and self.in_os(), 'next normal boot')
        self.assertEqual(extra.read_bytes(), b'later')

    @pytest.mark.gate
    def test_cut_after_the_catalog_rename_retries_cleanup_on_play(self):
        """The migration record comes after the cleanup, so a cut leaves work for the next start."""
        self.start(seed=seeder({}))
        self.wait(lambda: self.note() is not None, 'first-start cleanup')
        self.quiet()
        self.old_install(self.root)
        self.sim.set('rename_fault_kind', 2)
        self.sim.set('rename_fault', 1)
        self.sim.control('power play')
        self.wait(lambda: self.sim.status()['cuts'] == 1, 'cut after restoring the catalog')
        self.wait(lambda: self.note() is not None, 'resumed cleanup')
        self.assertEqual(CS.content(self.root), self.official())
        self.assertEqual(self.note(), b'files=5\ncatalog=0\n')
        self.wait(lambda: self.lines('menu: line: Removed 5 files'), 'back in the menu')
        self.connect()

    @pytest.mark.gate  # a refusal: the simulator core keeps the cleanup
    def test_a_catalog_that_cannot_be_put_back_writes_nothing(self):
        """An official entry changed too: nothing removed or put back, and the menu says what to do."""
        changed = open_xy(CATALOG.replace(b'"f1"', b'"f2"'))
        self.start(play=False, seed=seeder(OPEN_XY, changed))
        self.wait(lambda: self.lines('menu: save failed: '), 'the refusal')
        self.assertEqual(
            self.lines('menu: save failed: '), ["Failed: op1_factory.json can't be repaired. Copy in OS 1.2.1's own."]
        )
        self.connect()  # the menu stays, with USB
        code, f = self.job()
        self.assertEqual((code, MS.PHASES[f['phase']], f['error'], f['detail']), (0, 'failed', 22, 'tidy opxy-121'), f)
        files, dirs = self.official()
        dirs |= {'aux_presets', 'aux_presets/cv', 'aux_presets/conductor', 'presets/fm2op'}
        self.assertEqual(CS.content(self.root), ({**files, **OPEN_XY, 'op1_factory.json': changed}, dirs))
        self.assertIsNone(self.note())


if __name__ == '__main__':
    unittest.main()
