"""In the simulator: a base the web install adopted has no content set (#183). Its library entry is os.ldr,
manifest.json and files.sha256 only, as the adoption writes it (test_sim_adopt), and a mod build needs the base's
content.sha256. Saving a selection (config.set) accepts it while the live content looks official. Continue then
lists /yaffs2/content/ (dlc/ aside), hashes every file and writes firmware.version and content.sha256 beside the base,
before it builds, installs the content and boots. Content that is not the official set is refused, with the reason
named: a catalog that is not the official one at config.set, any other file during the build, before anything is
written. The official set is the simulator's synthetic content, given by XYF_SIM_OFFICIAL_CONTENT; the device's
1.2.1 digests are checked against the private manifest when it is present. Synthetic packages (test_sim_mod_job's
kit). Needs clang++ and the private stock 1.2.1 images (skipped without them). No device access."""

import hashlib
import os
import re
import unittest

import pytest
import test_sim_content_switch as CS
import test_sim_mod_job as J
from test_modpatch import CATALOG
from test_sim_adopt import expected_entry
from test_sim_config import ConfigBase

from xy_forever import modconfig, paths

ENV = 'XYF_SIM_OFFICIAL_CONTENT'
HEADER = paths.REPO / 'target/usb_service.hpp'


def header_digest(name):
    body = re.search(r'%s\[32\] = \{([^}]*)\}' % name, HEADER.read_text()).group(1)
    return bytes(int(x, 16) for x in re.findall(r'0x([0-9a-f]{2})', body)).hex()


class OfficialFacts(unittest.TestCase):
    def test_the_digests_are_the_official_1_2_1_set(self):
        manifest = paths.XYF / 'content-manifests/1.2.1'
        if not (manifest / 'content.sha256').is_file():
            self.skipTest('no private 1.2.1 content manifest')
        text = (manifest / 'content.sha256').read_bytes()
        self.assertEqual(header_digest('official_list_sha256'), hashlib.sha256(text).hexdigest())
        catalog = re.search(rb'^([0-9a-f]{64})  op1_factory\.json$', text, re.M).group(1).decode()
        self.assertEqual(header_digest('official_catalog_sha256'), catalog)
        version = (manifest / 'firmware.version').read_bytes()
        self.assertIn('official_version[] = "%s\\n"' % version.strip().decode(), HEADER.read_text())


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class ContentSet(ConfigBase, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        entry = cls.seeded / 'xy-forever/firmware' / cls.base
        stock = (entry / 'os.ldr').read_bytes()
        for name in ('info.txt', 'content.sha256'):  # the adoption's entry: no content set
            (entry / name).unlink()
        for name, data in expected_entry(stock).items():
            (entry / name).write_bytes(data)
        (cls.seeded / 'content/stray.txt').unlink()  # the official files and a sound pack (dlc/) only
        cls.official = CS.content(cls.seeded)
        listed = cls.official_text.encode()
        before = os.environ.get(ENV)
        os.environ[ENV] = '%s %s' % (hashlib.sha256(listed).hexdigest(), hashlib.sha256(CATALOG).hexdigest())
        cls.addClassCleanup(lambda: os.environ.pop(ENV) if before is None else os.environ.update({ENV: before}))

    def entry(self, name):
        path = self.root / 'xy-forever/firmware' / self.base / name
        return path.read_bytes() if path.exists() else None

    def set_mods(self, mods):
        return modconfig.set_inputs(
            self.dev, mods, modconfig.UNVERIFIED, sleep=lambda s: self.sim.advance(int(s * 1000))
        )

    @pytest.mark.sim
    def test_a_web_installed_base_gets_its_content_set_then_builds(self):
        """Kept: every OP-XY installed from the web app builds its first mods this way."""
        self.assertEqual(self.set_mods(['alpha']), (0, ''))
        self.saved(['alpha'])
        self.cont()
        entry = self.booted()
        self.assertIn(entry, ('mods-a', 'mods-b'))
        self.saved_as(entry, ['alpha'])
        self.assertEqual(self.entry('content.sha256'), self.official_text.encode())
        self.assertEqual(self.entry('firmware.version'), b'1.2.1+0\n')
        self.assertFalse((self.root / 'xy-forever/tmp' / ('cset-' + self.base)).exists())

    @pytest.mark.sim
    def test_a_catalog_that_is_not_official_is_refused_at_saving(self):
        """Kept: config.set names why the build could not happen, before power-on."""
        (self.root / 'content/op1_factory.json').write_bytes(CATALOG + b' ')
        code, reason = self.set_mods(['alpha'])
        self.assertEqual(code, modconfig.P.ids.S_REFUSED)
        self.assertEqual(reason, "The OP-XY's content is not official OS 1.2.1, and the base has no content set.")
        self.assertIsNone(self.config())

    @pytest.mark.sim
    def test_other_content_that_is_not_official_fails_the_build_before_writing(self):
        """Kept: only the official set becomes a content set; nothing is written otherwise."""
        sample = self.root / 'content/samples/kick/one.wav'
        sample.write_bytes(sample.read_bytes() + b'!')
        self.assertEqual(self.set_mods(['alpha']), (0, ''))
        self.saved(['alpha'])
        self.cont()
        self.assertEqual(self.failed(), "Failed: the content is not official OS 1.2.1's.")
        self.assertIsNone(self.entry('content.sha256'))
        self.assertIsNone(self.entry('firmware.version'))
        self.assertFalse((self.root / 'xy-forever/firmware/mods-a').exists())
