"""The web app's kit split (web/src/kit/split.ts, run under Node by web/scripts/kit-split.ts) against its host
reference, xy_forever/modkit.py split: the same packages byte for byte and the same refusals.

The synthetic kits of tests/test_modkit.py and tests/test_modpatch.py (capability edits, settings extensions) run in
the gate tier with the hosts' stand-in content. The published Open-XY 0.0.7 kit (private fixtures) runs, when present
with its SDK in the open-xy upstream clone, with each side's own Open-XY builder: the host's from the upstream release
commit, the app's vendored copy (checked unchanged against that commit). It also shows a newer kit offering its new
entry (the same code reads whatever kit it is given; the app fetches it at run time), and the 0.0.6 kit, made for
another firmware profile, refused."""

import base64
import copy
import gzip
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest
import test_modkit as TK
import test_modpatch as TP

from xy_forever import modkit as M
from xy_forever import paths

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = 'web/scripts/kit-split.ts'
USES = ('web/scripts/kit-split.ts', 'web/src/kit/', 'web/src/vendor/open-xy/')
SOURCE = 'https://github.com/example/sdk@abc'
KIT_007 = paths.FIXTURES / 'open-xy-kit-0.0.7.json.gz'  # https://open-xy.com/kit.json.gz on 2026-11-23
KIT_007_SHA256 = 'e971adfaf1a541b1e500cb40cd3abca38e74f0a40c9d3b7c8fedd7388c83c354'
SDK_007 = '28dd1a2e36599108c5eecf1a03f3b28601e50b39'  # open-xy v0.0.7: the kit's SDK, the app's vendored builder
SOURCE_007 = 'https://github.com/etcetc000/open-xy@' + SDK_007
VENDORED = {
    'builder/core.mjs': 'web/src/vendor/open-xy/builder/core.mjs',
    'sdk/browser/graphics.mjs': 'web/src/vendor/open-xy/sdk/browser/graphics.mjs',
    'tooling/default_preset.json': 'web/src/vendor/open-xy/tooling/default_preset.json',
}


def node_split(kits, out, assets=(), sdk_source=SOURCE):
    """One line per kit from the script: {generation, root} or {error}."""
    cmd = ['node', SCRIPT, '--out', str(out), '--sdk-source', sdk_source]
    cmd += [a for kit in kits for a in ('--kit', str(kit))] + [a for f in assets for a in ('--assets', str(f))]
    run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr[-2000:]
    return [json.loads(line) for line in run.stdout.splitlines()]


def tree(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file()}


def write_json(path, value):
    path.write_text(json.dumps(value))
    return path


def capability_kit():
    return TP.capability_kit(TP.stock())


def with_profile(kit, change):
    change(kit['profile'])
    kit['profileSource'] = base64.b64encode(json.dumps(kit['profile']).encode()).decode()
    return kit


def refusals():
    """(name, kit, the message both sides give)."""
    out = [
        ('format', dict(TK.synthetic_kit(), format='opxy-browser-kit/2'), 'unknown kit format'),
        ('field', dict(TK.synthetic_kit(), future=1), 'newer than this tool'),
        (
            'edit kind',
            with_profile(TK.synthetic_kit(), lambda p: p['patches'][0].update(kind='relocate')),
            'unknown edit kind',
        ),
        ('schema', TK.synthetic_kit(), 'profile differs from its profile source'),
    ]
    out[-1][1]['profile']['schema'] = 2
    kit = TK.synthetic_kit()
    kit['objects'][0]['sha256'] = '0' * 64
    out.append(('hash', kit, 'object hash mismatch'))
    kit = TK.synthetic_kit()
    o = next(o for o in kit['objects'] if o['name'] == 'alpha/voice.o')
    o.update(TK.entry(o['name'], TK.obj(['alpha_register'], ['beta_register']), owner=o['owner']))
    out.append(('import', kit, 'not SDK or runtime exports'))
    kit = TK.synthetic_kit()
    kit['machines'].append(copy.deepcopy(kit['machines'][0]))
    out.append(('duplicate', kit, 'duplicate machine ids'))
    kit = capability_kit()
    kit['extensions'][-1]['settings']['release_floor']['value'] = -67
    out.append(('floor', kit, 'invalid extension release floor'))
    kit = capability_kit()
    out.append(('limit', with_profile(kit, lambda p: p['patches'][-1].update(length=4)), 'unsupported limit length'))
    return out


@pytest.mark.gate
@pytest.mark.uses(*USES)
@unittest.skipUnless(shutil.which('node'), 'Node absent')
class Synthetic(unittest.TestCase):
    def test_packages_and_refusals_match_the_host(self):
        """One Node run for every kit (its start is most of the time)."""
        with tempfile.TemporaryDirectory(prefix='xyf-kit-browser-') as tmp:
            work = Path(tmp)
            kits, assets, refs = [], [], []
            for name, kit, finish in (
                ('modkit', TK.synthetic_kit(), TK.finish),
                ('limits', capability_kit(), TP.finish),
            ):
                path = write_json(work / (name + '.json'), kit)
                refs.append(Path(M.split_kit(path, work / 'py' / name, finish=finish, sdk_source=SOURCE)['root']))
                given = {i: {p: base64.b64encode(d).decode() for p, d in f.items()} for i, f in finish(kit).items()}
                kits.append(path)
                assets.append(write_json(work / (name + '-assets.json'), given))
            cases = refusals()
            for name, kit, pattern in cases:
                path = write_json(work / ('refused-%s.json' % name.replace(' ', '-')), kit)
                with self.subTest(host=name), self.assertRaisesRegex(M.KitError, pattern):
                    M.analyse(M.load_kit(path))
                kits.append(path)
                assets.append(assets[0])
            results = node_split(kits, work / 'ts', assets)
            for ref, result in zip(refs, results):
                with self.subTest(kit=ref.name):
                    self.assertEqual(result.get('generation'), ref.name, result)
                    self.assertEqual(tree(result['root']), tree(ref))
            for (name, _kit, pattern), result in zip(cases, results[len(refs) :]):
                with self.subTest(browser=name):
                    self.assertRegex(result.get('error', ''), pattern)


def published_kit_ready():
    if not (shutil.which('node') and KIT_007.is_file() and (paths.OPEN_XY_UPSTREAM / '.git').exists()):
        return False
    probe = ['git', '-C', str(paths.OPEN_XY_UPSTREAM), 'cat-file', '-e', SDK_007 + '^{commit}']
    return subprocess.run(probe, capture_output=True).returncode == 0 and M.sha256(KIT_007.read_bytes()) == (
        KIT_007_SHA256
    )


@pytest.mark.gate
@pytest.mark.uses(*USES)
@unittest.skipUnless(
    published_kit_ready(),
    'the published 0.0.7 kit (private fixtures), Node or the open-xy upstream clone at %s absent' % SDK_007[:7],
)
class PublishedKit(unittest.TestCase):
    def test_published_kit(self):
        """The host's and the app's packages of the published kit, and a newer kit with a late entry."""
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='kit-browser-', dir=paths.SESSIONS) as tmp:
            work = Path(tmp)
            newer = json.loads(gzip.decompress(KIT_007.read_bytes()))
            late = newer['machines'][-1]['id']
            older = copy.deepcopy(newer)
            older['machines'].pop()
            older['objects'] = [o for o in older['objects'] if o.get('owner') != late]
            kit = write_json(work / 'kit.json', newer)
            ref = Path(M.split_kit(kit, work / 'py', paths.OPEN_XY_UPSTREAM, SOURCE_007, sdk_commit=SDK_007)['root'])
            kits = [KIT_007, write_json(work / 'older.json', older)]
            kits += [paths.PUBLISHED_KIT] if paths.PUBLISHED_KIT.is_file() else []  # 0.0.6: another profile
            result, earlier, *other = node_split(kits, work / 'ts', (), SOURCE_007)
            for upstream, vendored in VENDORED.items():
                with self.subTest(vendored=upstream):
                    show = ['git', '-C', str(paths.OPEN_XY_UPSTREAM), 'show', '%s:%s' % (SDK_007, upstream)]
                    copied = (ROOT / vendored).read_bytes()
                    if vendored.endswith('.mjs'):
                        copied = copied.split(b'\n', 1)[1]  # its "copied from" line
                    self.assertEqual(copied, subprocess.run(show, capture_output=True, check=True).stdout)
            self.assertEqual(result.get('generation'), ref.name, result)
            self.assertEqual(tree(result['root']), tree(ref))
            self.assertNotEqual(earlier['generation'], result['generation'])
            self.assertNotIn(late, {p.name for p in Path(earlier['root']).iterdir()})
            self.assertIn(late, {p.name for p in Path(result['root']).iterdir()})
            for refused in other:
                self.assertRegex(refused.get('error', ''), 'made for another firmware profile', refused)


if __name__ == '__main__':
    unittest.main()
