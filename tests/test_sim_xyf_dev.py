"""`xyf dev` against the device's own code (xy_forever/dev_entry.py, #284): a build as Open-XY assembles it (the
synthetic stock image, test_xyf's assembly) is pushed as library entry xyf-dev with its content, then booted once
(menu.converge mode 2). The content switch installs exactly the build's content/ files over the official content;
the default, the state files and the saved inputs stay. A second push of the same build sends no os.ldr again. Needs
clang++ and the private stock 1.2.1 images (skipped without them). No device access."""

import tempfile
import unittest
from pathlib import Path

import pytest
from test_modpatch import profile_for, stock
from test_sim_content_switch import Switch, content
from test_sim_mod_job import LIB, UNAVAILABLE
from test_xyf import assemble

from xy_forever import dev_entry, sim
from xy_forever import xyf_host as host

BUILD = {'op1_factory.json': b'{"catalog": "with mine"}\n', 'display/mine.svg': b'<svg id="mine"/>'}


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class DevEntry(Switch, unittest.TestCase):
    base = 'modbase'

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-simdev-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        cls.stock = stock()
        cls.seeded = sim.seed(cls.work / 'seed')
        entry = cls.seeded / LIB / cls.base
        entry.mkdir(parents=True)
        (entry / 'os.ldr').write_bytes(cls.stock)
        (entry / 'info.txt').write_bytes(sim.info_text(cls.stock, 'Base', '1.2.1'))
        cls.seed_content()
        cls.runs = 0
        cls.image = assemble(cls.stock, profile_for(cls.stock))
        cls.files = dev_entry.entry_files(cls.image, BUILD, needs_os=dev_entry.sha256(cls.stock))

    @pytest.mark.gate  # a simulator chain: a push, a content switch, a boot, a second push
    @pytest.mark.uses('sim/')
    def test_push_and_boot_once(self):
        slots = {p: (self.root / 'xy-forever' / p).read_bytes() for p in ('state-a.txt',)}
        dev_entry.preflight(self.dev, needs_os=dev_entry.sha256(self.stock))
        lines, details = [], []
        self.assertEqual(dev_entry.run(self.dev, self.files, lines.append, details.append), 0, lines + details)
        self.assertRegex(lines[0], r'^\[push\] \d+\.\d\d s: xyf-dev: os\.ldr \d+ KB, 2 of 2 content files sent$')
        self.assertRegex(lines[1], r'^\[boot\] \d+\.\d\d s: xyf-dev started once; the next plain start goes back')
        self.assertRegex(lines[2], r'^total \d+\.\d\d s$')
        files, _ = content(self.root)
        self.assertEqual(files, {**self.official[0], **BUILD})
        self.assertIn('entry=xyf-dev', self.record())
        self.assertEqual((self.root / LIB / 'xyf-dev' / 'os.ldr').read_bytes(), self.image)
        self.assertEqual({p: (self.root / 'xy-forever' / p).read_bytes() for p in slots}, slots)

        self.sim.control('power play')  # PLAY at power-on: the menu again, as for the next xyf dev
        self.sim.wait_listening(30.0)
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)
        self.sim.settle()
        lines.clear()
        self.assertEqual(dev_entry.run(self.dev, self.files, lines.append), 0, lines)
        self.assertRegex(lines[0], r'xyf-dev: os\.ldr unchanged, 2 of 2 content files sent$')


if __name__ == '__main__':
    unittest.main()
