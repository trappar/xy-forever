"""Mod assembly on the host, end to end (native/mod_link, mod_patch, mod_content,
host/mod_assemble.cpp, xy_forever/modpatch.py).

Synthetic fixtures made here from scratch run in the standard suite: a stock LDR, a profile with an
edit of every kind, a kit split by modkit, an official catalog. The adapter links natively, then patches
and builds the content from the same link; the os.ldr is compared with a small Python model of core.mjs
patchFirmware over the linker bridge's own module and globals (tests/test_mod_link.py proves those
against core.mjs), the content with a model of buildAssets, and every refusal is checked; a fuzz run
under the address and undefined-behaviour sanitizers damages every input (50 rounds; 1,200 in the gate tier). The published kit's packages,
the stock 1.2.1 os.ldr, op1_factory.json, Node and open-xy's core.mjs (private area, the open-xy upstream clone) run the
byte-for-byte proof against core.mjs when present."""

import base64
import copy
import hashlib
import json
import os
import random
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest
import test_modkit as TK
from test_ldr import header, image

from xy_forever import hostbuild, package_records, paths
from xy_forever import modkit as M
from xy_forever import modlink as L
from xy_forever import modpatch as P
from xy_forever import xyf_entry as E

BASE = 0x8D2D0380
TEXT = 0x8D000000
TEXT_AT = 112  # file offset of the text record's payload (see stock())
ENTRY = 0x11A00000
SANITIZE = ('-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-sanitize-recover=all')
CATALOG = (
    b'{\n  "engine_types": [\n    {"type": "e1"}\n  ],\n  "fx_types": [\n    {"type": "f1", "id": 1}\n  ],\n'
    b'  "seq_types": [\n    {"type": "s1", "id": 5}\n  ]\n}\n'
)
INSERTS = dict(
    zip(('engine_types', 'fx_types', 'seq_types'), [m.start() + 1 for m in re.finditer(rb'\}\n  \]', CATALOG)])
)
IDENTITIES = dict(
    engines=256,
    engine_none=33,
    stock_engines=[2, 3],
    pages=256,
    page_none=81,
    sample_pages=[2, 7],
    effects=[1, 5, 8],
    effect_page=2,
    players=[6, 7],
)
PACKAGES = Path(os.environ.get('XYF_MOD_PACKAGES', paths.PUBLISHED_MODS))
# The firmware's official content list (content.sha256 form, paths relative to content/), synthetic.
OFFICIAL = ['display/adsr.svg', 'op1_factory.json', 'presets/bass/alloy.json', 'samples/kick/one.wav']


def official_text(paths_):
    return ''.join('%s  %s\n' % (hashlib.sha256(p.encode()).hexdigest(), p) for p in paths_)


def stock():
    rng = random.Random(54)
    return image(
        [
            (4, ENTRY, 64, 0, rng.randbytes(64)),
            (4, TEXT, 256, 0, rng.randbytes(256)),
            (0x104, 0x8D100000, 0x500000, 0, b''),
        ],
        entry=ENTRY,
    )


def profile_for(data, text_at=TEXT, fill=(0x8D100000, 0x500000), base=BASE):
    def at(offset, length):
        return data[TEXT_AT + offset : TEXT_AT + offset + length].hex()

    def edit(kind, offset, length, **fields):
        return dict(
            kind=kind,
            name=kind,
            address='0x%08x' % (text_at + offset),
            file_offset='0x%x' % (TEXT_AT + offset),
            length=length,
            before=at(offset, length),
            **fields,
        )

    patches = [
        edit('heap', 0x00, 8, after='01da200100000000'),
        edit('table', 0x10, 4, after='00112233'),
        edit('text', 0x20, 2, after='0100', service='drums'),
        edit('startup', 0x30, 10, symbol='sdk_start'),
        edit('hook', 0x40, 4, symbol='sdk_ext_event', call=True, service='extensions', events=['x']),
        edit('hook', 0x50, 6, symbol='sdk_start', call=False),
        edit('hook', 0x60, 8, symbol='sdk_start', call=False, profiling=False),
        edit('hook', 0x70, 12, symbol='sdk_ext_event', call=True, service='master_filter'),
    ]
    return dict(
        schema=1,
        id='opxy-test',
        stock_sha256=hashlib.sha256(data).hexdigest(),
        stock_bytes=len(data),
        stock_records=5,
        stock_catalog_sha256=hashlib.sha256(CATALOG).hexdigest(),
        stock_catalog_inserts=INSERTS,
        module_base=base,
        reserved_bytes=0x200000,
        table_bytes=0x4000,
        heap_fill_base=fill[0],
        heap_fill_bytes=fill[1],
        identities=IDENTITIES,
        services={'extensions': ['extension'], 'master_filter': ['master_filter'], 'drums': ['drum']},
        patches=patches,
    )


def kit_for(profile):
    kit = TK.synthetic_kit()
    kit['profile'] = profile
    kit['profileSource'] = base64.b64encode(json.dumps(profile, indent=2).encode()).decode()
    kit['stockCatalog'] = base64.b64encode(CATALOG).decode()
    kit['machines'] += [
        dict(
            id='gamma',
            name='Gamma',
            kind='player',
            prefix='gamma',
            identity=dict(player=None, page=None),
            absent=dict(gamma_register='kit_absent'),
            catalog=',\n  {"type": "gamma", "id": 7}\n',
            origin='personal',
            artwork='<g id="gamma"/>',
            asset='gamma.svg',
        ),
        dict(
            id='delta',
            name='Delta',
            kind='synth',
            prefix='delta',
            identity=dict(engine=None, page=None),
            absent=dict(delta_register='kit_absent'),
            catalog='',
            origin='personal',
        ),
        dict(
            id='omega',
            name='Omega',
            kind='synth',
            prefix='omega',
            identity=dict(engine=None, page=None),
            absent=dict(omega_register='kit_absent'),
            catalog='',
            origin='personal',
        ),
    ]
    kit['objects'] += [
        TK.entry('gamma/seq.o', TK.obj(['gamma_register'], ['sdk_start', 'gamma_player_id']), owner='gamma'),
        TK.entry('delta/voice.o', TK.obj(['delta_register'], ['sdk_start', 'delta_engine_id']), owner='delta'),
        TK.entry('omega/voice.o', TK.obj(['omega_register'], ['sdk_start', 'omega_engine_id']), owner='omega'),
    ]
    return kit


def finish(kit):
    """Stands in for core.mjs buildAssets: finished svgs, instrument presets, aux presets (both aux machines
    carry the cv role's init, each its own bytes: the later machine in collection order wins)."""
    out = {}
    for m in kit['machines']:
        files = {}
        if m.get('artwork'):
            files['content/display/' + m['asset']] = TK.TEMPLATE.replace('</svg>', m['artwork'] + '</svg>').encode()
        if m['kind'] in ('synth', 'drum', 'sample') and m['catalog']:
            files['content/presets/%s/default.json' % m['id']] = b'{"type": "%s"}\n' % m['id'].encode()
        if not m['catalog']:
            files['content/aux_presets/%s/warm.json' % m['id']] = b'{"type": "%s"}\n' % m['id'].encode()
            files['content/aux_presets/cv/init.json'] = b'{"type": "cv", "from": "%s"}\n' % m['id'].encode()
        out[m['id']] = files
    return out


# ---------------------------------------------------------------- core.mjs patchFirmware / buildAssets, in Python


def words(*values):
    return b''.join(struct.pack('<H', v) for v in values)


def redirect(e, dest):
    if e['kind'] == 'startup':
        return words(0xDC00, dest >> 16, dest & 0xFFFF, 0, 0)
    if e['length'] in (8, 12):
        return words(0xDE00 if e['call'] else 0xDC00, dest >> 16, dest & 0xFFFF, 0).ljust(e['length'], b'\0')
    value = ((dest - int(e['address'], 16)) // 2) & 0xFFFFFF
    return words((0xE300 if e['call'] else 0xE200) | value >> 16, value & 0xFFFF).ljust(e['length'], b'\0')


def model_patch(profile, link, module, data, profiling):
    out = bytearray(data)
    for e in profile['patches']:
        off, before = int(e['file_offset'], 16), bytes.fromhex(e['before'])
        if ('service' in e and e['service'] not in link['services']) or (e.get('profiling') is False and profiling):
            after = before
        elif e['kind'] in ('hook', 'startup'):
            after = redirect(e, link['globals'][e['symbol']])
        else:
            after = bytes.fromhex(e['after'])
        out[off : off + len(after)] = after
    at = len(data) - 16
    candidate = bytearray(out[:at] + header(4, profile['module_base'], len(module), 0) + module + out[at:])
    candidate[:16] = header(0x5004, struct.unpack_from('<I', data, 4)[0], 0, len(candidate) - 16)
    return bytes(candidate)


def model_assets(gen, machines, exts, numbers):
    rows = {r[1]: r for r in M.read_table((gen / 'base/machines.txt').read_bytes())}
    groups = {'engine_types': b'', 'fx_types': b'', 'seq_types': b''}
    files = {}
    ordered = sorted(machines, key=lambda i: int(rows[i][0]))  # collection order, as core.mjs
    for mid in ordered:
        kind = rows[mid][2][len('kind=') :]
        entry = gen / mid / 'catalog.txt'
        if entry.exists():
            text = entry.read_bytes()
            if kind in ('effect', 'player'):
                n = numbers[mid][kind] + (4 if kind == 'player' else 0)
                text = re.sub(rb'"id":\s*\d+', b'"id": %d' % n, text, count=1)
            groups[
                'engine_types' if kind not in ('effect', 'player') else 'fx_types' if kind == 'effect' else 'seq_types'
            ] += text
    extended = CATALOG
    for key, at in sorted(INSERTS.items(), key=lambda kv: -kv[1]):
        extended = extended[:at] + groups[key] + extended[at:]
    files['content/op1_factory.json'] = extended
    for folder in [gen / ('ext-' + e) for e in exts] + [gen / m for m in ordered]:  # files[path]: the last write
        for p in (folder / 'content').rglob('*'):
            if p.is_file():
                files[p.relative_to(folder).as_posix()] = p.read_bytes()
    return files


def refresh(folder):
    (folder / 'files.sha256').unlink()
    (folder / 'files.sha256').write_text(E.files_sha256_text(folder))


@pytest.mark.gate
class Synthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-modpatch-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        cls.binary = P.build(cls.work / 'bin', run=hostbuild.run)
        cls.bridge = L.build(cls.work / 'bin')
        cls.stock = stock()
        (cls.work / 'os.ldr').write_bytes(cls.stock)
        (cls.work / 'op1_factory.json').write_bytes(CATALOG)
        (cls.work / 'content.sha256').write_text(official_text(OFFICIAL))
        cls.profile = profile_for(cls.stock)
        cls.gen = cls.split(kit_for(cls.profile), 'packages')
        cls.runs = 0

    @classmethod
    def split(cls, kit, name):
        (cls.work / (name + '.json')).write_text(json.dumps(kit))
        return Path(
            M.split_kit(
                cls.work / (name + '.json'),
                cls.work / name,
                finish=finish,
                sdk_source='https://github.com/example/sdk@abc',
            )['root']
        )

    def folder(self):
        self.__class__.runs += 1
        folder = self.work / ('run%d' % self.runs)
        folder.mkdir()
        return folder

    def variant(self, change):
        """A copy of the package set changed by change(root), the named packages' files.sha256 refreshed."""
        target = self.folder() / 'packages'
        shutil.copytree(self.gen, target)
        for package in change(target) or ():
            refresh(target / package)
        return target

    def run_job(self, machines, exts=(), profiling=False, gen=None, edit=None, limits=None, unverified=True):
        """unverified: the user confirmed the base (a synthetic base is never a known one)."""
        folder = self.folder()
        lines = P.job_text(
            gen or self.gen,
            list(machines),
            list(exts),
            self.work / 'os.ldr',
            self.work / 'op1_factory.json',
            profiling,
            limits,
            official=self.work / 'content.sha256',
            unverified=unverified,
        ).splitlines()
        if edit:
            lines = edit(lines, folder)
        (folder / 'job.txt').write_text('\n'.join(lines) + '\n')
        (folder / 'out').mkdir()
        return P.run(self.binary, folder / 'job.txt', folder / 'out'), folder / 'out'

    def bridge_link(self, machines, exts, gen=None):
        """The native linker alone (the bridge): globals, services, numbers and the module."""
        folder = self.folder()
        r = L.link_native(self.bridge, gen or self.gen, list(machines), list(exts), folder, keep=folder / 'module.bin')
        self.assertEqual(r['status'], 'ok', r)
        return r, (folder / 'module.bin').read_bytes()

    # -- byte for byte against the model ------------------------------------------------------------
    def test_assembles_like_core_mjs(self):
        cases = (
            (['alpha'], [], False),
            (['alpha', 'beta', 'gamma', 'delta'], [], False),
            (['beta'], ['cap'], False),
            (['alpha', 'gamma'], ['cap', 'flt'], False),
            (['delta', 'alpha'], ['flt'], True),
            (['omega', 'delta'], [], False),
        )
        for machines, exts, profiling in cases:
            with self.subTest(machines=machines, extensions=exts, profiling=profiling):
                report, out = self.run_job(machines, exts, profiling)
                self.assertEqual(
                    (
                        report['input'],
                        report['link_status'],
                        report['patch'],
                        report['verify'],
                        report['content'],
                        report['copy'],
                    ),
                    ('ok',) * 6,
                    report,
                )
                link, module = self.bridge_link(machines, exts)
                self.assertEqual(report['numbers'], link['numbers'])
                self.assertEqual(report['initialized'], len(module))
                self.assertEqual(report['zero_steps'], 1)
                want = model_patch(self.profile, link, module, self.stock, profiling)
                self.assertEqual((out / 'os.ldr').read_bytes(), want)
                self.assertEqual(report['sha256'], hashlib.sha256(want).hexdigest())
                self.assertEqual(report['entry'], ENTRY)  # ldr::inspect, cold policy
                files = model_assets(self.gen, machines, exts, report['numbers'])
                got = P.tree(out)
                got.pop('os.ldr')
                listed = got.pop('files.sha256').decode()
                self.assertEqual(got, files)
                self.assertEqual(listed, P.list_text(files))  # exactly the content files, sorted

    def test_shared_aux_preset_keeps_the_later_machine(self):
        """Two aux machines carry content/aux_presets/cv/init.json: listed once, the later machine's bytes
        (collection order, whatever the selection's order), as core.mjs's files[path] keeps the last write."""
        report, out = self.run_job(['omega', 'delta'])
        self.assertEqual(report['content'], 'ok', report)
        self.assertEqual((out / 'content/aux_presets/cv/init.json').read_bytes(), b'{"type": "cv", "from": "omega"}\n')
        listed = (out / 'files.sha256').read_text()
        self.assertEqual(listed.count('content/aux_presets/cv/init.json'), 1)

    def test_numbers_and_inactive_edits(self):
        report, out = self.run_job(['beta', 'gamma'])
        catalog = (out / 'content/op1_factory.json').read_bytes()
        n = report['numbers']
        self.assertIn(b'{"type": "beta", "id": %d}' % n['beta']['effect'], catalog)  # an effect: its number
        self.assertIn(b'{"type": "gamma", "id": %d}' % (n['gamma']['player'] + 4), catalog)  # a player: number + 4
        data = (out / 'os.ldr').read_bytes()
        for e in self.profile['patches']:  # services unused: stock bytes
            if 'service' in e:
                off = int(e['file_offset'], 16)
                self.assertEqual(data[off : off + e['length']].hex(), e['before'])
        self.assertEqual(report['applied'], 5)

    def test_cold_policy_refusal_is_reported(self):
        """A stock (and so the module) in a range the cold policy protects (the embedded source)."""
        rng = random.Random(54)
        text, fill, base = 0x8B400000, (0x8B480000, 0x300000), 0x8B500000
        data = image(
            [
                (4, ENTRY, 64, 0, rng.randbytes(64)),
                (4, text, 256, 0, rng.randbytes(256)),
                (0x104, fill[0], fill[1], 0, b''),
            ],
            entry=ENTRY,
        )
        gen = self.split(kit_for(profile_for(data, text, fill, base)), 'protected')
        path = self.work / 'protected.ldr'
        path.write_bytes(data)
        report, out = self.run_job(
            ['alpha'], gen=gen, edit=lambda lines, f: self.swap(lines, 'stock', 'stock %s' % path)
        )
        self.assertEqual(report['link_status'], 'ok', report)
        self.assertEqual(report['patch'], 'policy', report)
        self.assertIn('protected_range', report['patch_detail'])
        self.assertFalse((out / 'os.ldr').exists())

    # -- refusals -------------------------------------------------------------------------------------
    def refused(self, machines, part, status, edit=None, exts=(), detail=None, gen=None, limits=None, unverified=True):
        report, out = self.run_job(machines, exts, gen=gen, edit=edit, limits=limits, unverified=unverified)
        got = report['link_status'] if part == 'link' else report[part]
        self.assertEqual(got, status, report)
        if detail:
            self.assertIn(detail, report['link_detail'] if part == 'link' else report[part + '_detail'])
        self.assertFalse([p for p in out.rglob('*') if p.is_file()], 'a refusal leaves no output')
        return report

    @staticmethod
    def swap(lines, key, value):
        return [value if l.split(' ', 1)[0] == key else l for l in lines]

    def test_refuses_stock_that_is_not_pinned(self):
        def tampered(lines, folder):
            data = bytearray(self.stock)
            data[200] ^= 1
            (folder / 'other.ldr').write_bytes(bytes(data))
            return self.swap(lines, 'stock', 'stock %s' % (folder / 'other.ldr'))

        self.refused(['alpha'], 'patch', 'stock', tampered, detail='sha256')

    def edits_variant(self, change):
        def edit(root):
            prof = copy.deepcopy(self.profile)
            change(prof['patches'])
            (root / 'base/edits.bin').write_bytes(M.encode_edits(prof))
            return ['base']

        return self.variant(edit)

    def test_refuses_a_preimage_mismatch(self):
        def preimage(p):
            p[1]['before'] = 'ffffffff'

        self.refused(['alpha'], 'patch', 'preimage', gen=self.edits_variant(preimage), detail='edit 1')

    def test_refuses_edits_outside_the_module_or_overlapping(self):
        def in_fill(p):  # an edit in the zero-fill (no stored bytes): no mapping
            p[1].update(address='0x8d100100')

        self.refused(['alpha'], 'patch', 'mapping', gen=self.edits_variant(in_fill), detail='edit 1')

        def overlap(p):
            q = copy.deepcopy(p[1])
            q['name'] = 'again'
            p.append(q)

        self.refused(['alpha'], 'patch', 'overlap', gen=self.edits_variant(overlap))

        def far(p):  # a hook whose symbol is a definition outside the module
            p[3]['symbol'] = 'stock_insert'

        self.refused(['alpha'], 'patch', 'destination', gen=self.edits_variant(far), detail='edit 3')

    def test_refuses_inputs_that_differ_from_their_hashes(self):
        def edits(root):  # edits.bin changed, files.sha256 not refreshed
            (root / 'base/edits.bin').write_bytes(b'XYFE')

        report = self.refused(['alpha'], 'input', 'sha256', gen=self.variant(edits), detail='base/edits.bin')
        self.assertEqual(report['link'], -1)

        def obj(root):
            (root / 'alpha/objects/alpha/voice.o').write_bytes(b'\0' * 64)

        self.refused(['alpha'], 'input', 'sha256', gen=self.variant(obj), detail='alpha/objects/alpha/voice.o')

    def test_refuses_an_unpinned_catalog(self):
        def other(lines, folder):
            (folder / 'catalog.json').write_bytes(CATALOG.replace(b'e1', b'e2'))
            return self.swap(lines, 'catalog', 'catalog %s' % (folder / 'catalog.json'))

        self.refused(['alpha'], 'content', 'catalog', other, detail='sha256')

    def test_refuses_catalog_offsets_that_do_not_land(self):
        def moved(root):
            info = TK.legacy_record(root / 'base')
            info.write_text(
                info.read_text().replace(
                    'catalog_engine_at=%d' % INSERTS['engine_types'],
                    'catalog_engine_at=%d' % (INSERTS['engine_types'] - 1),
                )
            )
            return ['base']

        self.refused(['alpha'], 'content', 'insert', gen=self.variant(moved), detail='engine_types')

        def number(root):  # catalog_number one byte off
            info = TK.legacy_record(root / 'beta')
            info.write_text(
                re.sub(r'catalog_number=(\d+),', lambda m: 'catalog_number=%d,' % (int(m[1]) + 1), info.read_text())
            )
            return ['beta']

        self.refused(['beta'], 'content', 'number', gen=self.variant(number), detail='beta')

    def test_reads_the_patch_facts_from_the_base_info(self):
        """mod_patch::read_profile: the base's info.txt alone gives the patcher its facts (no job lines)."""
        job = P.job_text(
            self.gen,
            ['alpha'],
            [],
            self.work / 'os.ldr',
            self.work / 'op1_factory.json',
            official=self.work / 'content.sha256',
        )
        self.assertFalse(re.search(r'^(facts|rule) ', job, re.M), job)

        def info(change):
            def edit(root):
                path = TK.legacy_record(root / 'base')
                path.write_text(change(path.read_text()))
                return ['base']

            return self.variant(edit)

        drop = lambda key: lambda t: re.sub(r'(?m)^%s=.*\n' % key, '', t)  # noqa: E731
        put = lambda key, v: lambda t: re.sub(r'(?m)^%s=.*$' % key, '%s=%s' % (key, v), t)  # noqa: E731
        for key in (
            'stock_records',
            'reserved_bytes',
            'heap_fill_base',
            'heap_fill_bytes',
            'needs_os',
            'catalog_sha256',
        ):
            with self.subTest(missing=key):
                self.refused(['alpha'], 'input', 'base', gen=info(drop(key)), detail=key)
        for key, value in (
            ('heap_fill_base', '0xzz'),
            ('stock_records', '4294967296'),
            ('reserved_bytes', ''),
            ('heap_fill_bytes', '0x123456789'),
            ('needs_os', 'AB' * 32),
        ):
            with self.subTest(malformed=key):
                self.refused(['alpha'], 'input', 'base', gen=info(put(key, value)), detail=key)
        twice = lambda t: t + 'heap_fill_bytes=4096\n'  # noqa: E731
        self.refused(['alpha'], 'input', 'base', gen=info(twice), detail='heap_fill_bytes')
        self.refused(['alpha'], 'input', 'base', gen=info(put('reserved_bytes', 4096)), detail='profile')
        hexed = info(put('stock_records', '0x%x' % self.profile['stock_records']))  # the 0x form
        report, _ = self.run_job(['alpha'], gen=hexed)
        self.assertEqual((report['input'], report['patch'], report['verify']), ('ok', 'ok', 'ok'), report)
        fill = info(put('heap_fill_bytes', self.profile['heap_fill_bytes'] + 4))  # read, and checked by the patch
        report, _ = self.run_job(['alpha'], gen=fill)
        self.assertEqual(report['input'], 'ok', report)
        self.assertNotEqual(report['patch'], 'ok', report)

    def test_refuses_packages_with_edits_or_addresses(self):
        def edits(root):
            (root / 'alpha/edits.bin').write_bytes(b'XYFE')
            return ['alpha']

        self.refused(['alpha'], 'content', 'package', gen=self.variant(edits), detail='edits.bin')

        def absolute(root):
            data = TK.obj(['alpha_register'], ['sdk_start', 'alpha_engine_id'], absolute=['fixed_address'])
            (root / 'alpha/objects/alpha/voice.o').write_bytes(data)
            return ['alpha']

        # the linker refuses it first, before the content step's own check
        self.refused(['alpha'], 'link', 'absolute', gen=self.variant(absolute), detail='alpha/voice.o')

    def test_refuses_missing_svg_or_preset(self):
        def unlisted(path):
            def change(root):
                (root / 'alpha' / path).unlink()
                return ['alpha']

            return change

        self.refused(
            ['alpha'], 'content', 'missing', gen=self.variant(unlisted('content/display/alpha.svg')), detail='alpha.svg'
        )
        self.refused(
            ['alpha'],
            'content',
            'missing',
            gen=self.variant(unlisted('content/presets/alpha/default.json')),
            detail='presets',
        )

    def test_refuses_duplicate_ids(self):
        self.refused(['alpha', 'alpha'], 'link', 'duplicate_selection', detail='alpha')

    def test_refuses_output_larger_than_its_bound(self):
        self.refused(['alpha'], 'link', 'limit', limits=dict(image=len(self.stock) + 8), detail='module buffer')
        self.refused(
            ['alpha'], 'patch', 'capacity', limits=dict(image=len(self.stock) + 20), detail='image'
        )  # no room for FINAL
        self.refused(['alpha'], 'content', 'capacity', limits=dict(catalog=len(CATALOG)))
        self.refused(['alpha'], 'content', 'capacity', limits=dict(list=100), detail='files.sha256')

    def test_verify_rejects_tampering(self):
        report, out = self.run_job(['alpha'])
        data = (out / 'os.ldr').read_bytes()
        job = out.parent / 'job.txt'

        def verify(blob):
            path = out.parent / 'tampered.ldr'
            path.write_bytes(blob)
            run = subprocess.run([str(self.binary), 'verify', str(job), str(path)], capture_output=True, text=True)
            return json.loads(run.stdout)['verify']

        self.assertEqual(verify(data), 'ok')
        for at in (40, TEXT_AT + 0x90, len(data) - 8):  # outside every edit, and FINAL
            blob = bytearray(data)
            blob[at] ^= 0x10
            self.assertEqual(verify(bytes(blob)), 'verify', at)
        self.assertEqual(verify(data[:-1]), 'verify')

    # -- hardening ----------------------------------------------------------------------
    def fixed(self, offset, address, length=4):
        """A well-formed table edit at file offset/address: stock's bytes before, 0xab after."""
        return dict(
            kind='table',
            name='extra',
            address='0x%08x' % address,
            file_offset='0x%x' % offset,
            length=length,
            before=self.stock[offset : offset + length].hex(),
            after='ab' * length,
        )

    def test_unknown_base_needs_the_users_confirmation(self):
        """A base not in the known-base table: refused, unless the user confirmed it; then reported unverified."""
        report = self.refused(['alpha'], 'patch', 'unverified', detail='base', unverified=False)
        self.assertEqual((report['base'], report['link_status']), ('unverified', 'ok'))
        payload = bytes(random.Random(7).randbytes(8))

        def arbitrary(p):  # a heap edit rewriting stock code with arbitrary bytes
            p[0]['after'] = payload.hex()

        gen = self.edits_variant(arbitrary)
        self.refused(['alpha'], 'patch', 'unverified', gen=gen, detail='base', unverified=False)
        report, out = self.run_job(['alpha'], gen=gen)
        self.assertEqual((report['patch'], report['verify'], report['base']), ('ok', 'ok', 'unverified'), report)
        self.assertEqual((out / 'os.ldr').read_bytes()[TEXT_AT : TEXT_AT + 8], payload)

    def test_known_base_entry(self):
        """modkit known-base: one table entry from a base folder (stock, catalog, edits.bin, numbers)."""
        text = P.known_bases_text([self.gen / 'base'], self.work / 'os.ldr', self.work / 'op1_factory.json')
        digest = hashlib.sha256(self.stock).hexdigest()
        self.assertIn('{%s}' % ', '.join('0x' + digest[i : i + 2] for i in range(0, 64, 2)), text)
        self.assertIn('{0x%xu, 0x%xu, 0x%xu' % (len(self.stock), 5, BASE), text)
        with self.assertRaises(M.KitError):
            P.known_bases_text([self.gen / 'base'], self.work / 'op1_factory.json')

    def test_refuses_fixed_edits_outside_a_records_payload(self):
        """Valid fixed edits (stock preimage) aimed at LDR headers, the fill record, the module's reservation, or
        spanning two records: refused by the mapping check even for a confirmed base."""
        cases = dict(
            first_header=self.fixed(4, ENTRY + 4),
            text_header=self.fixed(TEXT_AT - 16, TEXT + 0x80),
            fill_header=self.fixed(TEXT_AT + 256, 0x8D100100),
            reservation=self.fixed(TEXT_AT + 0x90, BASE),
            two_records=self.fixed(TEXT_AT + 254, TEXT + 254),
        )
        for name, extra in cases.items():
            with self.subTest(name):
                self.refused(
                    ['alpha'], 'patch', 'mapping', gen=self.edits_variant(lambda p: p.append(extra)), detail='edit 8'
                )

    def verify_with(self, job, blob, gen=None):
        path = job.parent / 'tampered.ldr'
        path.write_bytes(blob)
        if gen:
            job = job.parent / 'job-other.txt'
            job.write_text(
                ''.join(
                    'generation %s\n' % gen if l.startswith('generation ') else l + '\n'
                    for l in (job.parent / 'job.txt').read_text().splitlines()
                )
            )
        run = subprocess.run([str(self.binary), 'verify', str(job), str(path)], capture_output=True, text=True)
        return json.loads(run.stdout)

    def test_verify_maps_edits_and_returns_the_image_hash(self):
        report, out = self.run_job(['alpha'])
        data = (out / 'os.ldr').read_bytes()
        job = out.parent / 'job.txt'
        good = self.verify_with(job, data)
        self.assertEqual((good['verify'], good['base'], good['sha256']), ('ok', 'unverified', report['sha256']))
        # a base whose extra edit's window covers the text record's header
        gen = self.edits_variant(lambda p: p.append(self.fixed(TEXT_AT - 16, TEXT + 0x80)))
        r = self.verify_with(job, data, gen)
        self.assertEqual((r['verify'], r['detail']), ('mapping', 'edit 8'))

        def retarget(at, target):  # a record header's target changed, its checksum refreshed
            blob = bytearray(data)
            struct.pack_into('<I', blob, at + 4, target)
            blob[at + 2] = 0
            for i in range(16):
                blob[at + 2] ^= blob[at + i] if i != 2 else 0
            return bytes(blob)

        self.assertEqual(self.verify_with(job, retarget(TEXT_AT - 16, TEXT + 0x10))['verify'], 'mapping')
        self.assertEqual(self.verify_with(job, retarget(16, ENTRY + 0x10))['verify'], 'verify')

    def test_refuses_a_machine_the_link_did_not_select(self):
        report = self.refused(
            ['alpha'], 'content', 'unlinked', edit=lambda lines, f: lines + ['unlinked delta'], detail='machine delta'
        )
        self.assertEqual(report['patch'], 'ok')

    def entry_variant(self, text):
        """gamma's catalog.txt replaced; catalog_number = the first textual "id": N (core.mjs's rewrite)."""

        def change(root):
            (root / 'gamma/catalog.txt').write_bytes(text)
            m = re.search(rb'"id":\s*\d+', text)
            info = TK.legacy_record(root / 'gamma')
            info.write_text(
                re.sub(r'catalog_number=\S+', 'catalog_number=%d,%d' % (m.start(), m.end()), info.read_text())
            )
            return ['gamma']

        return self.variant(change)

    def test_catalog_number_is_the_entrys_own_id(self):
        cases = dict(
            in_a_string=b',\n  {"\\"id": 3, "type": "gamma", "id": 7}\n',
            nested=b',\n  {"type": "gamma", "meta": {"id": 3}, "id": 7}\n',
        )
        for name, text in cases.items():
            with self.subTest(name):
                self.refused(['gamma'], 'content', 'number', gen=self.entry_variant(text), detail='position gamma')
        deep = b',\n  {"type": "gamma", "id": 7, "x": [[[%s]]]}\n'
        report, _ = self.run_job(['gamma'], gen=self.entry_variant(deep % b'1'))  # 4 levels: kept
        self.assertEqual(report['content'], 'ok', report)
        self.refused(['gamma'], 'content', 'entry', gen=self.entry_variant(deep % b'[1]'), detail='catalog.txt gamma')
        large = b',' + b' ' * 500 + b'{"type": "gamma", "id": 7}\n'
        self.refused(['gamma'], 'content', 'entry', gen=self.entry_variant(large), detail='catalog.txt gamma')

    def official_list(self, paths_):
        def edit(lines, folder):
            (folder / 'content.sha256').write_text(official_text(paths_))
            return self.swap(lines, 'official', 'official %s' % (folder / 'content.sha256'))

        return edit

    def test_refuses_content_named_like_official_content(self):
        for machines, extra, detail in (
            (['gamma'], 'display/gamma.svg', 'file display/gamma.svg'),
            (['alpha'], 'presets/alpha/default.json', 'file presets/alpha/default.json'),
            (['alpha'], 'presets/alpha/other.json', 'id alpha'),
            (['delta'], 'aux_presets/delta/other.json', 'id delta'),
        ):
            with self.subTest(extra):
                self.refused(
                    machines, 'content', 'official', self.official_list(sorted(OFFICIAL + [extra])), detail=detail
                )
        self.refused(['alpha'], 'content', 'official', self.official_list(OFFICIAL[::-1]), detail='list')
        self.refused(
            ['alpha'],
            'content',
            'official',
            detail='list',  # not a list at all
            edit=lambda lines, f: self.swap(lines, 'official', 'official %s' % (self.work / 'os.ldr')),
        )

    def test_refuses_a_name_containing_bootloader(self):
        def add(path):  # the host's list writer refuses such a name: listed by hand
            def change(root):
                data = (root / 'alpha/objects/alpha/voice.o').read_bytes()
                (root / 'alpha' / path).parent.mkdir(parents=True, exist_ok=True)
                (root / 'alpha' / path).write_bytes(data)
                listed = (root / 'alpha/files.sha256').read_text().splitlines()
                listed.append('%s  %s' % (hashlib.sha256(data).hexdigest(), path))
                (root / 'alpha/files.sha256').write_text(
                    ''.join(l + '\n' for l in sorted(listed, key=lambda l: l[66:].encode()))
                )

            return self.variant(change)

        for path in ('content/presets/alpha/bootloader.json', 'objects/alpha/BootLoader.o'):
            with self.subTest(path):
                self.refused(['alpha'], 'content', 'package', gen=add(path), detail='file alpha')

    def fuzz(self, rounds):
        """`rounds` damaged jobs through the sanitized assembler (seed 5); returns its outcome counts."""
        binary = P.build(self.work / 'sanitized', flags=SANITIZE, run=hostbuild.run)
        folder = self.folder()
        (folder / 'job.txt').write_text(
            P.job_text(
                self.gen,
                ['alpha', 'beta', 'gamma', 'delta', 'omega'],
                ['cap', 'flt'],
                self.work / 'os.ldr',
                self.work / 'op1_factory.json',
                official=self.work / 'content.sha256',
                unverified=True,
            )
        )
        run = subprocess.run(
            [str(binary), 'fuzz', str(folder / 'job.txt'), '5', str(rounds)], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(run.returncode, 0, run.stderr[-3000:] + run.stdout[-500:])
        counts = json.loads(run.stdout)
        self.assertEqual(sum(counts.values()), rounds)
        return counts

    def test_fuzz_under_sanitizers(self):
        counts = self.fuzz(50)
        self.assertIn('content=ok', ' '.join(counts))  # some damaged jobs still assemble
        self.assertGreater(len({k.split(' link=')[1].split(' ')[0] for k in counts if ' link=' in k}), 3)

    @pytest.mark.gate
    def test_long_fuzz_reaches_every_stage(self):
        counts = self.fuzz(1200)
        statuses = ' '.join(counts)
        for word in (
            'input=sha256',
            'link=0 patch=ok',
            'patch=stock',
            'patch=edits',
            'patch=ok verify=verify',
            'content=catalog',
            'content=ok',
        ):
            self.assertIn(word, statuses)
        self.assertGreater(len({k.split(' link=')[1].split(' ')[0] for k in counts if ' link=' in k}), 3)


def _real_inputs():
    return (
        shutil.which('node')
        and package_records.local_name(PACKAGES / 'base')
        and TK.STOCK.is_file()
        and P.default_official(PACKAGES).is_file()
        and TK.CATALOG.is_file()
        and (TK.OPEN_XY / '.git').exists()
        and subprocess.run(
            ['git', '-C', str(TK.OPEN_XY), 'cat-file', '-e', TK.SDK_COMMIT + '^{commit}'], capture_output=True
        ).returncode
        == 0
    )


@unittest.skipUnless(
    _real_inputs(),
    'published-kit packages, the official content.sha256, stock os.ldr + op1_factory.json, Node or '
    'the open-xy upstream clone at %s absent' % TK.SDK_COMMIT[:7],
)
@pytest.mark.gate
class PublishedKit(unittest.TestCase):
    """The real packages: a sample of modkit prove's selections (TK.sampled_selections), the native end-to-end
    assembly against core.mjs."""

    @classmethod
    def setUpClass(cls):
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='modpatch-test-', dir=paths.SESSIONS)
        cls.addClassCleanup(cls.tmp.cleanup)
        first = M.read_table((PACKAGES / 'base/machines.txt').read_bytes())[0][1]
        kit = M.rebuild_kit(PACKAGES, [first], [], TK.CATALOG.read_bytes())
        selections = TK.sampled_selections(M.selections_for(kit, PACKAGES))
        cls.rows = {
            r['selection']: r
            for r in P.prove(
                PACKAGES,
                TK.OPEN_XY,
                TK.CATALOG,
                TK.STOCK,
                scratch=cls.tmp.name,
                sdk_commit=TK.SDK_COMMIT,
                selections=selections,
            )
        }

    def test_sampled_selections_are_byte_identical(self):
        self.assertEqual(len(self.rows), 8)
        for name, row in self.rows.items():
            with self.subTest(selection=name):
                self.assertTrue(P.proven(row), row)
                if name != 'all':
                    self.assertEqual(row['entry'], ENTRY)
        self.assertEqual(self.rows['all']['link'], 'capacity')  # refused alike, nothing written
        self.assertEqual(self.rows['one+master_dj_filter']['files'], 4)  # catalog, fm2op svg + preset, DJ art
        self.assertTrue(any(n.startswith('fits') for n in self.rows))
        self.assertTrue(all(r['base'] == 'known' for n, r in self.rows.items() if 'base' in r))

    def test_known_base_table_is_this_base(self):
        """native/mod_known_bases.inc is what modkit known-base writes for the kept package set."""
        self.assertEqual(P.KNOWN_BASES.read_text(), P.known_bases_text([PACKAGES / 'base'], TK.STOCK, TK.CATALOG))

    def test_working_memory_and_steps(self):
        row = self.rows['linked']
        self.assertEqual(row['memory']['patch_scratch'], 52224)
        self.assertLess(row['bytes'], 2373336 + 16 + 2080768 + 1)
        for name, r in self.rows.items():
            if name != 'all':  # one zero step per 128 KiB of module
                self.assertEqual(r['steps']['zero'], -(-r['initialized'] // (128 * 1024)), name)


# The website's experimental 4 MiB option: a small selection, one that fits only the larger layout, one
# 1,180 B below its 4080 KiB limit, and one over it (refused alike).
LARGE = ['troupe', 'x0x', 'wander']
NEAR = LARGE + ['weave', 'choir', 'macroosc', 'crease', 'solar', 'drone', 'papasrapa', 'chords']
OVER = LARGE + ['weave', 'choir', 'macroosc', 'crease', 'grains']
EXPANDED_SELECTIONS = [
    ('one', ['fm2op'], []),
    ('large', LARGE, []),
    ('near', NEAR, []),
    ('near+capture', NEAR[:-1] + ['vibe'], ['capture']),
    ('over', OVER, []),
]


@unittest.skipUnless(
    _real_inputs(),
    'published-kit packages, the official content.sha256, stock os.ldr + op1_factory.json, Node or '
    'the open-xy upstream clone at %s absent' % TK.SDK_COMMIT[:7],
)
@pytest.mark.gate
class PublishedKitExpanded(unittest.TestCase):
    """The real packages with the 4 MiB option (core.mjs memoryKit(kit, true), the website's checkbox) against
    the native assembly in expanded mode; the same selections in standard mode refuse what does not fit."""

    @classmethod
    def setUpClass(cls):
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='modpatch-expanded-', dir=paths.SESSIONS)
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.rows = {
            r['selection']: r
            for r in P.prove(
                PACKAGES,
                TK.OPEN_XY,
                TK.CATALOG,
                TK.STOCK,
                scratch=cls.tmp.name,
                sdk_commit=TK.SDK_COMMIT,
                selections=EXPANDED_SELECTIONS,
                memory='expanded',
            )
        }
        cls.standard = {
            r['selection']: r
            for r in P.prove(
                PACKAGES,
                TK.OPEN_XY,
                TK.CATALOG,
                TK.STOCK,
                scratch=cls.tmp.name,
                sdk_commit=TK.SDK_COMMIT,
                selections=EXPANDED_SELECTIONS[:2],
            )
        }
        cls.binary = P.build(Path(cls.tmp.name) / 'bin', run=hostbuild.run)

    def test_expanded_is_byte_identical(self):
        limit = 4080 * 1024
        for name, row in self.rows.items():
            with self.subTest(selection=name):
                self.assertTrue(P.proven(row), row)
        self.assertGreater(self.rows['large']['allocated'], 2032 * 1024)  # does not fit standard
        self.assertLess(limit - self.rows['near']['allocated'], 2048)  # 1,180 B left
        self.assertEqual((self.rows['over']['link'], self.rows['over']['link_detail']), ('capacity', '4080'))
        self.assertIn('need more than the 4080 KiB available', self.rows['over']['error'])
        self.assertTrue(all(r['base'] == 'known' for r in self.rows.values() if 'base' in r))

    def test_standard_refuses_what_only_expanded_fits(self):
        self.assertTrue(all(P.proven(r) for r in self.standard.values()), self.standard)
        self.assertEqual((self.standard['large']['link'], self.standard['large']['link_detail']), ('capacity', '2032'))
        self.assertEqual(self.standard['one']['ldr_identical'], True)

    def assemble(self, gen, memory, name):
        folder = Path(self.tmp.name) / name
        (folder / 'out').mkdir(parents=True)
        (folder / 'job.txt').write_text(P.job_text(gen, ['fm2op'], [], TK.STOCK, TK.CATALOG, memory=memory))
        return P.run(self.binary, folder / 'job.txt', folder / 'out')

    def test_bases_without_or_with_a_wrong_expanded_layout(self):
        """A base written before the 4 MiB option (no expanded_module_base) stays known for standard and is refused for
        expanded; a base whose expanded_module_base differs from the known one is not known in expanded mode."""
        for name, value in (('old', None), ('moved', '0x8d1d0380'), ('unaligned', '0x8d0d0384')):
            gen = Path(self.tmp.name) / ('gen-' + name)
            shutil.copytree(
                PACKAGES,
                gen,
                ignore=lambda d, names: [n for n in names if Path(d) == PACKAGES and n not in ('base', 'fm2op')],
            )
            lines = [
                l for l in TK.legacy_record(gen / 'base').read_text().splitlines() if not l.startswith('expanded_')
            ]
            lines += ['expanded_module_base=' + value] if value else []
            TK.rewrite(gen / 'base', 'info.txt', ('\n'.join(lines) + '\n').encode())
            with self.subTest(base=name):
                standard = self.assemble(gen, 'standard', name + '-standard')
                self.assertEqual((standard['input'], standard['patch'], standard['base']), ('ok', 'ok', 'known'))
                expanded = self.assemble(gen, 'expanded', name + '-expanded')
                if value is None or name == 'unaligned':
                    self.assertEqual((expanded['input'], expanded['input_detail']), ('base', 'expanded_module_base'))
                else:
                    self.assertEqual(expanded['patch'], 'unverified')


if __name__ == '__main__':
    unittest.main()
