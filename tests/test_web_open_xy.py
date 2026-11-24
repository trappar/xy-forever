"""scripts/web_open_xy.py: the site's pinned Open-XY kit without teenage engineering's material (#183). A synthetic
kit: the catalog emptied, every edit's `before` (and a choice equal to one) zeroed in the profile and profileSource,
every other value unchanged; the scan finds the material in plain and gzip files. With the published 0.0.7 kit
(private fixtures): the stripped kit is the pinned one, and the staged folder holds none of its material."""

import base64
import gzip
import json
import tempfile
import unittest
from pathlib import Path

import pytest
import web_open_xy as W

from xy_forever import paths

KIT_007 = paths.FIXTURES / 'open-xy-kit-0.0.7.json.gz'
CATALOG = b'{"engine_types": [], "stock": "teenage"}\n'


def synthetic():
    profile = {
        'id': 'opxy-1.2.1',
        'patches': [
            {'kind': 'hook', 'name': 'a', 'before': 'deadbeefcafe0001', 'after': '0011223344556677'},
            {
                'kind': 'limit',
                'name': 'b',
                'before': 'abcdef0102030405',
                'choices': {'-1': 'abcdef0102030405', '-2': 'ff'},
            },
        ],
    }
    source = json.dumps(profile, indent=2).encode()
    kit = {
        'format': 'opxy-browser-kit/1',
        'version': '0.0.7',
        'profile': profile,
        'profileSource': base64.b64encode(source).decode(),
        'machines': [
            {'id': 'm', 'name': 'M', 'origin': 'official', 'source': {'repository': 'https://x/y', 'tag': 'v1'}}
        ],
        'stockCatalog': base64.b64encode(CATALOG).decode(),
    }
    return json.dumps(kit).encode(), source


class Strip(unittest.TestCase):
    def test_only_the_material_changes(self):
        raw, source = synthetic()
        stripped = json.loads(W.strip(raw))
        kit = json.loads(raw)
        self.assertEqual(stripped['stockCatalog'], '')
        patches = stripped['profile']['patches']
        self.assertEqual([p['before'] for p in patches], ['0' * 16, '0' * 16])
        self.assertEqual(patches[1]['choices'], {'-1': '0' * 16, '-2': 'ff'})
        self.assertEqual(json.loads(base64.b64decode(stripped['profileSource'])), stripped['profile'])
        for key in ('format', 'version', 'machines'):
            self.assertEqual(stripped[key], kit[key])
        self.assertEqual(patches[0]['after'], '0011223344556677')
        material = W.te_material(source, CATALOG)
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'clean.json').write_bytes(W.strip(raw))
            self.assertEqual(W.scan(tmp, material), [])
            Path(tmp, 'kit.json.gz').write_bytes(gzip.compress(raw))
            Path(tmp, 'page.js').write_bytes(b'x="DEADBEEFCAFE0001"')
            self.assertEqual({p for p, _ in W.scan(tmp, material)}, {'kit.json.gz', 'page.js'})

    @pytest.mark.gate  # a 27 MB kit
    @unittest.skipUnless(KIT_007.is_file(), 'the published 0.0.7 kit (private fixtures) absent')
    def test_the_published_kit_stages_without_material(self):
        raw = gzip.decompress(KIT_007.read_bytes())
        kit = json.loads(raw)
        text = W.strip(raw)
        self.assertEqual(W.sha256(text), W.STRIPPED_SHA256)
        with tempfile.TemporaryDirectory() as tmp:
            out = W.stage(Path(tmp) / 'open-xy', out=lambda line: None, text=text)
            profile, catalog = base64.b64decode(kit['profileSource']), base64.b64decode(kit['stockCatalog'])
            self.assertEqual(W.check(out, profile, catalog, out=lambda line: None), 0)
            self.assertEqual(gzip.decompress((out / 'kit.json.gz').read_bytes()), text)
            self.assertIn('machine fm2op', (out / 'SOURCES.txt').read_text())


if __name__ == '__main__':
    unittest.main()
