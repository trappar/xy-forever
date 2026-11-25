"""`xyf dev` against the device's own code (xy_forever/dev_entry.py, #284). A build as Open-XY assembles it (the
synthetic stock image, test_xyf's assembly) is pushed as library entry xyf-dev with its content, then booted once
(menu.converge mode 2): the content switch installs exactly the build's content/ files over the official content; the
default, the state files and the saved inputs stay; a second push of the same build sends no os.ldr again. A custom
image without content boots once with the official content. On an OP-XY that never saved mods (the stock base has no
content set) the boot once first makes it (#183's job: [content], once; a second run skips it); with mods' content
installed it goes on with the saved official list (the job's error 15); content that is not the official set fails
with the build's own message. The official set is the seeded content, given by XYF_SIM_OFFICIAL_CONTENT. Needs clang++
and the private stock 1.2.1 images (skipped without them). No device access."""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

import pytest
from test_modpatch import CATALOG, profile_for, stock
from test_sim_adopt import expected_entry
from test_sim_content_switch import Switch, content
from test_sim_mod_job import LIB, UNAVAILABLE
from test_xyf import assemble

from xy_forever import dev_entry, sim
from xy_forever import xyf_host as host

BUILD = {'op1_factory.json': b'{"catalog": "with mine"}\n', 'display/mine.svg': b'<svg id="mine"/>'}
ENV = 'XYF_SIM_OFFICIAL_CONTENT'
PUSH = r'^\[push\] \d+\.\d\d s: xyf-dev: '
SET = 'xy-forever/firmware/modbase/'


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
        for name, data in expected_entry(cls.stock).items():  # the stock entry as the adoption writes it
            (entry / name).write_bytes(data)
        (cls.seeded / 'xy-forever/state-a.txt').write_bytes(sim.slot_text(cls.base, 2))  # the base is the default
        cls.seed_content()
        (entry / 'firmware.version').write_bytes(b'1.2.1+0\n')
        files = {p: b for p, b in cls.official[0].items() if not p.startswith('dlc/')}
        listed = ''.join('%s  %s\n' % (hashlib.sha256(files[p]).hexdigest(), p) for p in sorted(files)).encode()
        before = os.environ.get(ENV)
        os.environ[ENV] = '%s %s' % (hashlib.sha256(listed).hexdigest(), hashlib.sha256(CATALOG).hexdigest())
        cls.addClassCleanup(lambda: os.environ.pop(ENV) if before is None else os.environ.update({ENV: before}))
        cls.listed = listed
        cls.runs = 0
        cls.image = assemble(cls.stock, profile_for(cls.stock))
        cls.files = dev_entry.entry_files(cls.image, BUILD, dev_entry.OPEN_XY, needs_os=dev_entry.sha256(cls.stock))

    def xyf_dev(self, files, kind=dev_entry.CUSTOM):
        """preflight, then [push], [content] when it says so, [boot], total: (exit code, lines, details)."""
        lines, details = [], []
        first = dev_entry.preflight(self.dev, needs_os=dev_entry.sha256(self.stock))
        code = dev_entry.run(self.dev, files, lines.append, details.append, kind, content_first=first)
        return code, lines, details

    def no_content_set(self):
        """The stock entry as an OP-XY that never saved mods has it: no content.sha256, no firmware.version."""
        for name in ('content.sha256', 'firmware.version'):
            (self.root / SET / name).unlink()

    def menu_again(self):
        """PLAY at power-on: the menu again, as for the next xyf dev."""
        self.sim.control('power play')
        self.sim.wait_listening(30.0)
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)
        self.sim.settle()

    @pytest.mark.gate  # a simulator chain: a push, a content switch, a boot, a second push
    @pytest.mark.uses('sim/')
    def test_push_and_boot_once(self):
        slots = {p: (self.root / 'xy-forever' / p).read_bytes() for p in ('state-a.txt',)}
        code, lines, details = self.xyf_dev(self.files, dev_entry.OPEN_XY)
        self.assertEqual(code, 0, lines + details)
        self.assertRegex(lines[0], PUSH + r'Open-XY build on 1\.2\.1, os\.ldr \d+ KB, 2 of 2 content files sent$')
        self.assertRegex(lines[1], r'^\[boot\] \d+\.\d\d s: xyf-dev started once; the next plain start goes back')
        self.assertRegex(lines[2], r'^total \d+\.\d\d s$')
        self.assertEqual(content(self.root)[0], {**self.official[0], **BUILD})
        self.assertIn('entry=xyf-dev', self.record())
        self.assertEqual((self.root / LIB / 'xyf-dev' / 'os.ldr').read_bytes(), self.image)
        self.assertEqual({p: (self.root / 'xy-forever' / p).read_bytes() for p in slots}, slots)

        self.menu_again()
        code, lines, _ = self.xyf_dev(self.files, dev_entry.OPEN_XY)
        self.assertEqual(code, 0, lines)
        self.assertRegex(lines[0], r'Open-XY build on 1\.2\.1, os\.ldr unchanged, 2 of 2 content files sent$')

    @pytest.mark.gate  # a simulator chain: a push, the boot once
    @pytest.mark.uses('sim/')
    def test_a_custom_image_without_content(self):
        code, lines, _ = self.xyf_dev(dev_entry.entry_files(self.stock, {}))
        self.assertEqual(code, 0, lines)
        self.assertRegex(lines[0], PUSH + r'custom image, os\.ldr \d+ KB, the official content$')
        self.assertRegex(lines[1], r'^\[boot\] \d+\.\d\d s: xyf-dev started once')
        self.assertEqual(content(self.root), self.official)

    @pytest.mark.gate  # a simulator chain: the content set, a switch, a boot; again without the content set
    @pytest.mark.uses('sim/')
    def test_an_op_xy_that_never_saved_mods(self):
        self.no_content_set()
        code, lines, details = self.xyf_dev(self.files, dev_entry.OPEN_XY)
        self.assertEqual(code, 0, lines + details)
        self.assertEqual([line.split(' ', 1)[0] for line in lines], ['[push]', '[content]', '[boot]', 'total'])
        self.assertRegex(lines[1], r'^\[content\] \d+\.\d\d s: recorded the official 1\.2\.1 content \(once\)$')
        self.assertEqual((self.root / SET / 'content.sha256').read_bytes(), self.listed)
        self.assertEqual((self.root / SET / 'firmware.version').read_bytes(), b'1.2.1+0\n')
        self.assertEqual(content(self.root)[0], {**self.official[0], **BUILD})
        self.first_use = lines

        self.menu_again()  # later runs skip it
        code, lines, _ = self.xyf_dev(dev_entry.entry_files(self.stock, {}))
        self.assertEqual(code, 0, lines)
        self.assertEqual([line.split(' ', 1)[0] for line in lines], ['[push]', '[boot]', 'total'])
        self.assertEqual(content(self.root), self.official)

    @pytest.mark.gate  # a simulator chain: a boot once with content, then one without the content set
    @pytest.mark.uses('sim/')
    def test_mods_content_installed_and_no_content_set(self):
        self.assertEqual(self.xyf_dev(self.files, dev_entry.OPEN_XY)[0], 0)
        self.menu_again()
        self.no_content_set()  # the job refuses (15: not the official state); the switch uses the saved official list
        code, lines, details = self.xyf_dev(dev_entry.entry_files(self.stock, {}))
        self.assertEqual(code, 0, lines + details)
        self.assertEqual([line.split(' ', 1)[0] for line in lines], ['[push]', '[boot]', 'total'])
        self.assertEqual(content(self.root), self.official)
        self.assertFalse((self.root / SET / 'content.sha256').exists())

    @pytest.mark.gate  # a refusal before anything is written
    @pytest.mark.uses('sim/')
    def test_content_that_is_not_official_fails_as_a_build_does(self):
        self.no_content_set()
        sample = self.root / 'content' / 'samples/kick/one.wav'
        sample.write_bytes(sample.read_bytes() + b'!')
        code, lines, _ = self.xyf_dev(self.files, dev_entry.OPEN_XY)
        self.assertEqual(code, 1)
        self.assertEqual(
            lines[1].split(': ', 1)[1], "the OP-XY stayed in its menu: Failed: the content is not official OS 1.2.1's."
        )
        self.assertFalse((self.root / SET / 'content.sha256').exists())
        self.assertIsNone(self.record())


if __name__ == '__main__':
    unittest.main()
