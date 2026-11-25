"""`uv run xyf-host mods dev` (#204): a development build of one mod, built alone on the device and booted once, the
saved setup untouched. In the simulator: a saved setup (alpha in one mods slot, its content installed, the default,
the fingerprint); mods dev pushes a development set (its own kit generation: store.txt development=1), builds it alone
into the other slot, installs its content and boots it once (menu.converge mode 2, followed to the end of USB); a
PLAY start opens the menu; a new development version replaces the old set and builds again; a plain power-on then
finds the default's content missing and converges back to the saved setup by itself. The device's Mods page never
lists a development set. Synthetic packages (test_sim_config's). Needs clang++ and the private stock 1.2.1 images
(skipped without them). No device access. The rest runs without a device or simulator."""

import json
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_sim_content_switch as CS
import test_sim_mod_job as J
from test_sim_config import MARK, ConfigBase

from xy_forever import moddev, modstore
from xy_forever import modsend as MS
from xy_forever import usb_proto as P

ONCE = 'menu: boots once '


def development_set(cls, version):
    """alpha renamed, split again: version 1 is the saved set's own generation (a name is not in its hash), version 2
    another one (another kit version)."""
    from test_modpatch import finish, kit_for, profile_for, stock

    from xy_forever import modkit as M

    kit = kit_for(profile_for(stock()))
    if version > 1:
        kit['version'] = '0.0.%d' % (90 + version)
    for machine in kit['machines']:
        if machine['id'] == 'alpha':
            machine['name'] = 'Alpha dev %d' % version
    path = cls.work / ('dev-%d.json' % version)
    path.write_text(json.dumps(kit))
    out = M.split_kit(path, cls.work / ('dev-%d' % version), finish=finish, sdk_source='https://github.com/x/sdk@abc')
    return Path(out['root'])


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class DevBuild(ConfigBase, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dev_sets = [development_set(cls, 1), development_set(cls, 2)]
        assert cls.dev_sets[0].name == cls.generation != cls.dev_sets[1].name

    def xyf_dev(self, gen):
        """mods dev GEN/alpha against the menu the test holds (the port is there): its exit code and lines."""
        lines = []
        args = SimpleNamespace(package_dir=gen / 'alpha', only=None, base=None, unverified=True, wait=1.0)
        code = moddev.command(args, lambda _: (self.dev, 'sim', self.dev.hello_info.text), out=lines.append)
        return code, lines

    def once(self, gen, slot):
        """mods dev boots the development build of gen once from slot: its content installed, nothing saved changed."""
        kept = [self.root / name for name in ('xy-forever/config.json', MARK)]
        saved = [p.read_bytes() if p.exists() else None for p in kept]
        default = self.disk_default()
        booted = len(self.lines(ONCE))
        code, lines = self.xyf_dev(gen)
        self.assertEqual(code, 0, '\n'.join(lines))
        for phase in ('[wait] ', '[push] ', '[build] ', '[boot] ', 'total '):
            self.assertTrue(any(line.startswith(phase) for line in lines), (phase, lines))
        self.wait_log(lambda: self.sim.status()['a'] == 'os' and self.sim.status()['b'] == 'os', 'the OS')
        self.assertEqual(self.lines(ONCE)[booted:], [slot])
        self.assertEqual(self.record().splitlines()[:2], ['state=installed', 'entry=%s' % slot])
        self.assertEqual(CS.content(self.root), self.expected(slot))
        manifest = json.loads((self.root / 'xy-forever/firmware' / slot / 'manifest.json').read_bytes())
        self.assertEqual(
            (manifest['name'], manifest['generation'], manifest['machines']), ('alpha (development)', gen.name, 1)
        )
        self.assertEqual([p.read_bytes() if p.exists() else None for p in kept], saved)
        self.assertEqual(self.disk_default(), default)
        self.assertFalse((self.root / 'xy-forever/job.txt').exists())  # the switch ended its journal
        store = self.root / 'xy-forever/mods' / ('dev-' + gen.name) / modstore.FILE
        self.assertEqual(store.read_bytes(), modstore.DEVELOPMENT_TEXT)

    @pytest.mark.gate  # a simulator chain: three builds, four content switches, four starts
    @pytest.mark.uses('sim/')
    def test_boot_once_rebuild_and_back_to_the_saved_setup(self):
        a = self.first_save()
        b = 'mods-b' if a == 'mods-a' else 'mods-a'
        self.menu_again()
        self.once(self.dev_sets[0], b)
        self.menu_again()  # PLAY at power-on: the menu, nothing converged
        self.wait_log(lambda: 'pending=1' in self.home(), 'the first row: (to apply)')
        self.open_page()  # the Mods page lists the saved set only
        self.assertEqual(self.page()['count'], len(self.folders))
        self.assertTrue(self.page()['text'].startswith('Booted once: alpha (development).'), self.page())
        self.once(self.dev_sets[1], b)  # a new version: the old set removed, built again
        self.assertFalse((self.root / 'xy-forever/mods' / ('dev-' + self.dev_sets[0].name)).exists())
        self.assertTrue((self.root / 'xy-forever/mods' / self.generation / 'base').is_dir())  # the saved set stays
        before = self.snapshot()
        self.mark = len(self.lines('menu: boots the default ')), len(self.lines('menu: save failed: '))
        self.sim.control('power plain')  # converges back by itself
        self.assertEqual(self.booted(), a)
        self.assertEqual(self.lines('menu: power-on converge: doubt ')[-1], '16')
        self.saved_as(a, ['alpha'])
        self.assertEqual(self.snapshot()['xy-forever/converged.txt'], before['xy-forever/converged.txt'])


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class NothingSaved(DevBuild):
    """No setup saved (the stock entry the default, no config.json) and Start at menu: after the boot once a plain
    power-on opens the menu (no converge); PLAY on its first row installs the default's own content back (the switch
    alone: nothing saved is written) and boots it."""

    settings_text = b'format=1\nconfirm=0\npower_on=always\n'

    @pytest.mark.gate  # a simulator chain: one build, two content switches, three starts
    @pytest.mark.uses('sim/')
    def test_boot_once_then_the_menu_then_back(self):
        self.once(self.dev_sets[1], 'mods-a')
        doubts = len(self.lines('menu: power-on converge: doubt '))
        rows = len(self.lines('menu: home row: '))
        self.sim.control('power plain')  # Start at menu: the menu, though the default's content is missing
        self.sim.wait_listening(30.0)
        self.reconnect()
        self.assertEqual(self.dev.hello()[0], 0)
        self.wait_log(lambda: len(self.lines('menu: home row: ')) > rows and 'pending=1' in self.home(), 'pending')
        self.assertEqual(len(self.lines('menu: power-on converge: doubt ')), doubts)
        self.cont()
        self.assertEqual(self.booted(), self.base)
        self.assertEqual(CS.content(self.root), self.expected(None))
        self.assertIn(self.record(), (None, 'state=official\n'))
        self.assertFalse((self.root / 'xy-forever/config.json').exists())
        self.assertFalse((self.root / 'xy-forever/job.txt').exists())

    test_boot_once_rebuild_and_back_to_the_saved_setup = None


class Host(unittest.TestCase):
    """Without a device: the request, the development mark, the follow loop."""

    def test_request_bytes(self):
        body = struct.pack('<II', 1, 2) + b'mods-b\0'
        self.assertEqual(moddev.converge_request(2, 'mods-b'), body + __import__('hashlib').sha256(body).digest())
        self.assertEqual(len(moddev.converge_request(0)), 41)  # the spec's minimum

    def test_development_mark(self):
        self.assertTrue(modstore.development(modstore.DEVELOPMENT_TEXT))
        self.assertTrue(modstore.development(b'version=1\r\ndevelopment=1\r\n'))
        self.assertFalse(modstore.development(modstore.TEXT))
        self.assertFalse(modstore.development(b'version=1\ndevelopment=10\n'))
        self.assertEqual(modstore.version_of(modstore.DEVELOPMENT_TEXT), modstore.VERSION)

    def follow(self, replies):
        """moddev.follow over scripted menu.converge (state, job, reason) and job.status replies."""
        lines, calls = [], iter(replies)

        def call(kind, payload=b'', timeout=5.0):
            item = next(calls)
            if isinstance(item, BaseException):
                raise item
            if kind == P.ids.MENU_CONVERGE:
                state, job, reason = item
                return 0, struct.pack('<III', 1, state, job) + reason.encode()
            return 0, struct.pack('<11I', item, 4, 1, 2, 0, 0, 10, 3, 1, 0, 0) + b'mods-b'.ljust(32, b'\0')

        dev = SimpleNamespace(call=call, t=None)
        return moddev.follow(dev, out=lines.append, sleep=lambda _: None), lines

    def test_follow(self):
        ok, lines = self.follow([(1, 7, ''), 7, (3, 7, '')])
        self.assertTrue(ok)
        self.assertIn('installing its content', lines[0])
        self.assertEqual(self.follow([(1, 7, ''), 7, OSError('gone')])[0], True)  # USB went away
        ok, lines = self.follow([(2, 7, 'Installing content failed: Continue again repairs it.')])
        self.assertFalse(ok)
        self.assertIn('stayed in its menu: Installing content failed', lines[-1])

    def test_resolve_takes_one_mod(self):
        with self.assertRaisesRegex(MS.Refused, 'neither'):
            moddev.resolve(Path('/nonexistent'))


if __name__ == '__main__':
    unittest.main()
