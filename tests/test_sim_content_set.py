"""In the simulator: a base the web install adopted has no content set (#183). Its library entry is os.ldr,
manifest.json and files.sha256 only, as the adoption writes it (test_sim_adopt), and a mod build needs the base's
content.sha256. Saving a selection (config.set) accepts it while the live content looks official. Continue then
lists /yaffs2/content/ (dlc/ aside), hashes every file and writes firmware.version and content.sha256 beside the base,
before it builds, installs the content and boots. Content that is not the official set is refused, with the reason
named: a catalog that is not the official one at config.set, any other file during the build, before anything is
written. An OP-XY that had Open-XY (#331: its files beside the official ones, its machines' entries in
op1_factory.json) builds too: the official file is put back. The official set is the
simulator's synthetic content, given by XYF_SIM_OFFICIAL_CONTENT; the device's 1.2.1 digests are checked against the
private manifest when it is present. Synthetic packages (test_sim_mod_job's
kit). Needs clang++ and the private stock 1.2.1 images (skipped without them). No device access."""

import hashlib
import json
import os
import re
import unittest

import pytest
import test_sim_content_switch as CS
import test_sim_mod_job as J
from test_modpatch import CATALOG, INSERTS
from test_sim_adopt import expected_entry
from test_sim_config import ConfigBase

from xy_forever import modconfig, paths

ENV = 'XYF_SIM_OFFICIAL_CONTENT'
HEADER = paths.REPO / 'target/usb_service.hpp'


def official_env(files):
    """XYF_SIM_OFFICIAL_CONTENT for the official content files ({path: bytes}, dlc/ aside) with CATALOG: the list's
    and the catalog's SHA-256, its lists' official counts, each path's hash (target/xyf_official_content.hpp)."""
    listed = ''.join('%s  %s\n' % (hashlib.sha256(files[p]).hexdigest(), p) for p in sorted(files)).encode()
    hashes = sorted(hashlib.sha256(p.encode()).hexdigest()[:8] for p in files)
    digests = hashlib.sha256(listed).hexdigest(), hashlib.sha256(CATALOG).hexdigest()
    return '%s %s 1,1,1 %s' % (*digests, ' '.join(hashes))


def open_xy(catalog=CATALOG):
    """catalog as Open-XY's builder leaves it: its machines' entries inserted after each list's official ones."""
    entries = dict(
        engine_types=',\n    {"type": "fm2op", "default_params": [0]}\n,\n    {"type": "dust"}',
        fx_types=',\n    {"type": "ringmod", "id": 1}',
        seq_types=',\n    {"type": "chords", "id": 24}\n',
    )
    for key, at in sorted(INSERTS.items(), key=lambda kv: -kv[1]):
        catalog = catalog[:at] + entries[key].encode() + catalog[at:]
    return catalog


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
        # files read in several 8 KiB halves, one ending on a half and one empty: the list as the host makes it
        for path, size in (('samples/big/long.wav', 3 * 8192 + 1000), ('samples/big/even.wav', 2 * 8192)):
            (cls.seeded / 'content' / path).parent.mkdir(parents=True, exist_ok=True)
            (cls.seeded / 'content' / path).write_bytes(bytes((i * 7 + 3) & 0xFF for i in range(size)))
        (cls.seeded / 'content/presets/empty.json').write_bytes(b'')
        cls.official = CS.content(cls.seeded)
        files = {p: b for p, b in cls.official[0].items() if not p.startswith('dlc/')}
        cls.official_text = ''.join('%s  %s\n' % (hashlib.sha256(files[p]).hexdigest(), p) for p in sorted(files))
        before = os.environ.get(ENV)
        os.environ[ENV] = official_env(files)
        cls.addClassCleanup(lambda: os.environ.pop(ENV) if before is None else os.environ.update({ENV: before}))

    def log(self):
        return (self.dir / 'log.txt').read_text(errors='replace')

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
        self.sim.set('a_slow_ms', 40)  # the read long enough for the bar's redraws
        self.cont()
        shown = self.wait_progress(lambda p: p['title'] == 'Reading OS 1.2.1 content', 'reading the content')
        self.assertEqual(shown['bar'], 0)
        first = self.progress()[0]['title']  # the sequence's own screen, before
        self.wait_log(lambda: self.progress()[-1]['title'] != shown['title'], 'the build after the read')
        self.sim.set('a_slow_ms', 0)
        entry = self.booted()
        self.assertIn(entry, ('mods-a', 'mods-b'))
        self.saved_as(entry, ['alpha'])
        bars = [int(b) for b in re.findall(r'mods bar: job=%d bar=(\d+)' % shown['job'], self.log())]
        self.assertGreaterEqual(len(set(bars)), 3, bars)  # it moves as the files are read
        self.assertEqual(bars, sorted(bars))
        self.assertEqual(self.progress()[-1]['title'], first)  # then the usual screen
        times = dict(f.split('=') for f in (self.root / 'xy-forever/log/save-times.txt').read_text().split()[1:])
        self.assertGreater(int(times['cset_read']) + int(times['cset_sha']) + int(times['cset_open']), 0, times)
        self.assertEqual(self.entry('content.sha256'), self.official_text.encode())
        self.assertEqual(self.entry('firmware.version'), b'1.2.1+0\n')
        self.assertFalse((self.root / 'xy-forever/tmp' / ('cset-' + self.base)).exists())

    @pytest.mark.gate  # a refusal: the simulator core keeps the build
    def test_a_catalog_that_is_not_official_is_refused_at_saving(self):
        """config.set names why the build could not happen, before power-on."""
        (self.root / 'content/op1_factory.json').write_bytes(CATALOG + b' ')
        code, reason = self.set_mods(['alpha'])
        self.assertEqual(code, modconfig.P.ids.S_REFUSED)
        self.assertEqual(reason, "The OP-XY's op1_factory.json can't be repaired: copy in OS 1.2.1's own.")
        self.assertIsNone(self.config())

    @pytest.mark.gate  # a refusal: the simulator core keeps the build
    def test_other_content_that_is_not_official_fails_the_build_before_writing(self):
        """Only the official set becomes a content set; nothing is written otherwise."""
        sample = self.root / 'content/samples/kick/one.wav'
        sample.write_bytes(sample.read_bytes() + b'!')
        self.assertEqual(self.set_mods(['alpha']), (0, ''))
        self.saved(['alpha'])
        self.cont()
        self.assertEqual(self.failed(), "Failed: the content is not official OS 1.2.1's.")
        self.assertIsNone(self.entry('content.sha256'))
        self.assertIsNone(self.entry('firmware.version'))
        self.assertFalse((self.root / 'xy-forever/firmware/mods-a').exists())

    @pytest.mark.gate  # the simulator core keeps the stock build
    def test_an_op_xy_that_had_open_xy_builds(self):
        """#331: Open-XY's files stay out of the set; op1_factory.json is put back to the official bytes."""
        extras = {
            'display/fm2op.svg': b'<svg/>',
            'presets/fm2op/init.json': b'{}',
            'aux_presets/cv/one.json': b'{}',
            'presets/alpha/default.json': b'{"from": "open-xy"}',  # a machine both install: the mod's replaces it
        }
        for path, data in extras.items():
            (self.root / 'content' / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / 'content' / path).write_bytes(data)
        changed = open_xy()
        self.assertEqual(json.loads(changed)['engine_types'][0], json.loads(CATALOG)['engine_types'][0])
        (self.root / 'content/op1_factory.json').write_bytes(changed)
        self.assertEqual(self.set_mods(['alpha']), (0, ''))
        self.saved(['alpha'])
        self.cont()
        entry = self.booted()
        self.assertIn(entry, ('mods-a', 'mods-b'))
        self.assertEqual(self.disk_default(), entry)
        self.assertEqual(self.entry('content.sha256'), self.official_text.encode())
        self.assertEqual((self.root / 'xy-forever/content-official/op1_factory.json').read_bytes(), CATALOG)
        files, dirs = self.expected(entry)  # the mods' content, Open-XY's files beside it (the mod's own replaced)
        self.assertIn('presets/alpha/default.json', files)
        dirs |= {p.rsplit('/', k)[0] for p in extras for k in (1, 2) if p.count('/') >= k}
        self.assertEqual(CS.content(self.root), ({**extras, **files}, dirs))
        self.assertEqual(list((self.root / 'xy-forever/tmp').glob('put-*')), [])

    @pytest.mark.gate  # a refusal: the simulator core keeps the build
    def test_a_catalog_that_cannot_be_put_back_is_refused(self):
        """An official entry changed too: never put back; the build says what to do, before anything is written."""
        changed = open_xy(CATALOG.replace(b'"f1"', b'"f2"'))
        self.assertEqual(self.set_mods(['alpha']), (0, ''))
        self.saved(['alpha'])
        (self.root / 'content/op1_factory.json').write_bytes(changed)
        self.cont()
        self.assertEqual(self.failed(), "Failed: op1_factory.json can't be repaired. Copy in OS 1.2.1's own.")
        self.assertEqual((self.root / 'content/op1_factory.json').read_bytes(), changed)
        self.assertIsNone(self.entry('content.sha256'))
        self.assertIsNone(self.entry('firmware.version'))
