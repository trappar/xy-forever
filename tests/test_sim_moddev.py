"""5.6 in the simulator: `xyf_host.py mods dev`, the mod developers' loop (push what changed by SHA-256, build on
the device, make it the default with its content, --restore). Synthetic packages (test_sim_mod_job's kit),
nothing on the device's mods/ at first. Needs clang++ and the private stock 1.2.1 images (skipped without
them). No device access."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import xyf_host as host                                                   # noqa: E402
from xy_forever import moddev, modsend as MS                              # noqa: E402
import test_sim_content_switch as CS                                      # noqa: E402
import test_sim_mod_job as J                                               # noqa: E402
from test_sim_mods_page import synthetic_setup                            # noqa: E402

PRESET = 'content/presets/alpha/default.json'


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class ModDev(CS.Switch, unittest.TestCase):
    base = 'modbase'
    unverified = True

    @classmethod
    def setUpClass(cls):
        synthetic_setup(cls)
        cls.seed_content()
        shutil.rmtree(cls.seeded / 'xy-forever/mods')                      # the dev's first push sends it all

    def setUp(self):
        super().setUp()
        self.gen = Path(tempfile.mkdtemp(dir=self.dir)) / self.generation
        shutil.copytree(self.gen_dir, self.gen)
        self.cache = self.dir / 'cache'
        patch = mock.patch.dict(os.environ, {'XYF_CACHE': str(self.cache)})
        patch.start()
        self.addCleanup(patch.stop)

    def host(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = host.run(['--sim', self.sim.address, '--timeout', '10', 'mods', 'dev'] + list(args))
        text = out.getvalue()
        return code, text

    def loop(self, *extra):
        code, text = self.host(str(self.gen / 'alpha'), '--with', 'beta', '--base', self.base, '--unverified', *extra)
        self.assertEqual(code, 0, text)
        for phase in ('check', 'push', 'apply', 'set-default'):
            self.assertRegex(text, r'\[%s\] \d+\.\d\d s' % phase)
        self.assertRegex(text, r'total \d+\.\d\d s')
        return text

    def sent(self, text):
        """[(package, files sent)] from the push lines."""
        return [(m.group(1), int(m.group(2))) for m in re.finditer(r'^(\S+)\s+sent\s+(\d+) of', text, re.M)]

    def edit(self, rel, data):
        """Changes one file of alpha and its files.sha256 line (as a rebuild by modkit would)."""
        (self.gen / 'alpha' / rel).write_bytes(data)
        listing = self.gen / 'alpha' / MS.LIST
        lines = [('%s  %s' % (hashlib.sha256(data).hexdigest(), p)) if p == rel else '%s  %s' % (s, p)
                 for s, p in MS.list_paths(listing.read_bytes())]
        listing.write_text('\n'.join(lines) + '\n')

    def default(self):
        return host.current_state({p: (self.root / p[len('/yaffs2/'):]).read_bytes()
                                   if (self.root / p[len('/yaffs2/'):]).exists() else None for p in host.SLOTS})[2]['default']

    def test_loop_change_same_size_and_restore(self):
        text = self.loop()                                                   # 1: everything is new
        self.assertEqual(sorted(n for n, _ in self.sent(text)), ['alpha', 'base', 'beta'], text)
        first = self.default()
        self.assertIn(first, ('mods-a', 'mods-b'))
        self.assertEqual(json.loads((self.cache / moddev.STATE_NAME).read_text())['sim:' + self.sim.address]['default'],
                         self.base)
        self.assertEqual((self.root / PRESET).read_bytes(), (self.gen / 'alpha' / PRESET).read_bytes())

        text = self.loop()                                                   # 2: nothing changed: no file sent
        self.assertEqual(self.sent(text), [], text)
        self.assertIn('skipped', text)

        old = (self.gen / 'alpha' / PRESET).read_bytes()                    # 3: one file, another size
        bigger = old.replace(b'{', b'{ ', 1)
        self.edit(PRESET, bigger)
        prior = self.default()
        text = self.loop()
        self.assertEqual(self.sent(text), [('alpha', 2)], text)            # the file and files.sha256
        self.assertEqual((self.root / PRESET).read_bytes(), bigger)
        self.assertNotEqual(self.default(), prior)                          # rebuilt: the slot not the default

        same = bigger.replace(b'{ ', b' {', 1)                              # 4: same size, other bytes
        self.assertEqual(len(same), len(bigger))
        self.edit(PRESET, same)
        text = self.loop()
        self.assertEqual(self.sent(text), [('alpha', 2)], text)
        self.assertEqual((self.root / 'xy-forever/mods' / self.profile / self.generation / 'alpha' / PRESET)
                         .read_bytes(), same)
        self.assertEqual((self.root / PRESET).read_bytes(), same)

        code, text = self.host('--restore')                                 # 5: the way back
        self.assertEqual(code, 0, text)
        self.assertIn('restored: default %s' % self.base, text)
        self.assertEqual(self.default(), self.base)
        self.assertNotIn('sim:' + self.sim.address, json.loads((self.cache / moddev.STATE_NAME).read_text()))
        code, text = self.host('--restore')
        self.assertEqual(code, 1, text)                                     # nothing remembered now

    def test_saved_configuration_refuses_then_reports(self):
        record = self.root / 'xy-forever/config.txt'
        record.write_text('base=%s\nprofile=%s\ngeneration=%s\nunverified=1\nmods=\n' %
                          (self.base, self.profile, self.generation))
        before = self.default()
        code, text = self.host(str(self.gen / 'alpha'), '--base', self.base, '--unverified')
        self.assertEqual(code, 2, text)
        self.assertIn('(to apply)', text)
        self.assertEqual(self.default(), before)                            # nothing written
        self.assertFalse((self.root / 'xy-forever/mods').exists())
        text = self.loop('--ignore-config')
        self.assertIn('the Continue line now ends in "(to apply)"', text)
        self.assertIn('mods=\n', record.read_text())                        # the record is the menu's own
        code, text = self.host('--restore')
        self.assertEqual(code, 0, text)
        self.assertIn('the Continue line ends in "nothing"', text)

    def test_refusals_before_connecting(self):
        code, text = self.host(str(self.gen), '--base', self.base)
        self.assertEqual(code, 2)
        self.assertIn('--only ID', text)
        code, text = self.host(str(self.gen / 'alpha'), '--boot', '--watch')
        self.assertEqual(code, 2)
        code, text = self.host(str(self.gen / 'alpha'), '--with', 'nosuch')
        self.assertEqual(code, 2)
        self.assertIn("no package 'nosuch'", text)


class Watch(unittest.TestCase):
    """--watch: a pass, then another after a file changes (device calls replaced)."""

    def test_reruns_on_change(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        (work / 'base').mkdir()
        (work / 'alpha').mkdir()
        (work / 'alpha' / 'x').write_bytes(b'1')
        runs = []

        def once(*a, **k):
            runs.append(1)
            return 0, {}

        def sleep(_):
            if len(runs) == 1:
                (work / 'alpha' / 'x').write_bytes(b'22')
        with mock.patch.object(moddev, 'dev_once', once):
            code = moddev.watch(None, 'p', None, work, ['alpha'], (), None, out=lambda line: None, sleep=sleep,
                                rounds=2)
        self.assertEqual((code, len(runs)), (0, 2))


if __name__ == '__main__':
    unittest.main()
