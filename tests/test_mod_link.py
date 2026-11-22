"""Native mod linker (native/mod_link.hpp, xy_forever/modlink.py; docs/storage/mods.md).

A synthetic kit made here from scratch (small ELF objects with every relocation type, alignments,
services, runtime members, absent machines and extensions) is split into packages by modkit and
linked by the host-compiled native linker: hand checks always; byte-for-byte and refusal-for-refusal
against open-xy's core.mjs when Node and the open-xy upstream clone at the kit's SDK commit are present. Malformed
objects (truncations, bit flips) run under the address and undefined-behaviour sanitizers. The
real 0.0.6 package set, numbers.json and the Blackfin compile run when present and skip otherwise."""

import base64
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

from xy_forever import modkit as M
from xy_forever import modlink as L
from xy_forever import paths
from xy_forever import xyf_entry as E

OPEN_XY = paths.OPEN_XY_UPSTREAM  # a fresh upstream clone (never ../open-xy)
REAL = Path(os.environ.get('XYF_MOD_PACKAGES', paths.PUBLISHED_MODS))
GLOBAL, WEAK, LOCAL = 1, 2, 0
FUNC, OBJ, SECTION = 2, 1, 3
ABS = M.SHN_ABS
CATALOG = b'{"engine_types": [], "fx_types": [], "seq_types": []}\n'
TEMPLATE = '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="220"><rect/></svg>'
BASE = 0x8D2D0380


def has_core():
    return (
        shutil.which('node')
        and (OPEN_XY / '.git').exists()
        and subprocess.run(
            ['git', '-C', str(OPEN_XY), 'cat-file', '-e', L.SDK_COMMIT + '^{commit}'], capture_output=True
        ).returncode
        == 0
    )


def elf(sections, symbols=(), relocations=(), align=None):
    """A Blackfin ET_REL object. sections: [(name, bytes | bss size, alignment)]; symbols:
    [(name, section 1-based | 0 | ABS, value, binding, type, size)]; relocations: [(section 1-based,
    offset, type, symbol 1-based, addend)]."""
    shstr, strtab = bytearray(b'\0'), bytearray(b'\0')

    def add(table, text):
        at = len(table)
        table.extend(text.encode() + b'\0')
        return at

    body, layout = bytearray(), []
    targets = sorted({r[0] for r in relocations})
    symtab_index = len(sections) + 1 + len(targets)
    for name, content, alignment in sections:
        bss = isinstance(content, int)
        flags = 3 if name.startswith(('.data', '.bss', '.sdata', '.sbss')) else 6 if name.startswith('.text') else 2
        body += bytes(-(52 + len(body)) % max(alignment, 1))
        layout.append(
            (
                add(shstr, name),
                8 if bss else 1,
                flags,
                52 + len(body),
                content if bss else len(content),
                0,
                0,
                alignment,
                0,
            )
        )
        if not bss:
            body += content
    for target in targets:
        body += bytes(-len(body) % 4)
        rela = b''.join(struct.pack('<IIi', off, sym << 8 | t, a) for s, off, t, sym, a in relocations if s == target)
        layout.append(
            (
                add(shstr, '.rela' + sections[target - 1][0]),
                4,
                0,
                52 + len(body),
                len(rela),
                symtab_index,
                target,
                4,
                12,
            )
        )
        body += rela
    symtab = bytearray(16)
    for name, section, value, binding, type_, size in symbols:
        symtab += struct.pack(
            '<IIIBBH', add(strtab, name) if name else 0, value, size, binding << 4 | type_, 0, section
        )
    body += bytes(-len(body) % 4)
    layout.append((add(shstr, '.symtab'), 2, 0, 52 + len(body), len(symtab), symtab_index + 1, 1, 4, 16))
    body += symtab
    strtab_name, shstr_name = add(shstr, '.strtab'), add(shstr, '.shstrtab')
    layout.append((strtab_name, 3, 0, 52 + len(body), len(strtab), 0, 0, 1, 0))
    body += strtab
    layout.append((shstr_name, 3, 0, 52 + len(body), len(shstr), 0, 0, 1, 0))
    body += shstr
    body += bytes(-len(body) % 4)
    headers = [bytes(40)] + [struct.pack('<10I', h[0], h[1], h[2], 0, *h[3:]) for h in layout]
    header = bytearray(52)
    header[:16] = b'\x7fELF\x01\x01\x01' + bytes(9)
    struct.pack_into(
        '<HHIIIIIHHHHHH', header, 16, 1, 106, 1, 0, 0, 52 + len(body), 0, 52, 0, 0, 40, len(headers), len(headers) - 1
    )
    return bytes(header + body + b''.join(headers))


def code(*words):
    return struct.pack('<%dH' % len(words), *words)


def simple(exports=(), imports=(), data_imports=(), bss=0, text=None, local=()):
    """An object exporting functions (in .text) and taking each import by a 32-bit data pointer."""
    text = text or code(*([0] * max(2, 2 * len(exports))))
    sections = [('.text', text, 4), ('.data', bytes(4 * max(1, len(imports) + len(data_imports))), 4)]
    if bss:
        sections.append(('.bss', bss, 4))
    syms = [(n, 1, 4 * i, GLOBAL, FUNC, 4) for i, n in enumerate(exports)]
    syms += [(n, 0, 0, GLOBAL, 0, 0) for n in list(imports) + list(data_imports)]
    syms += list(local)
    first = len(exports) + 1
    relocs = [(2, 4 * i, 18, first + i, 0) for i in range(len(imports) + len(data_imports))]
    return elf(sections, syms, relocs)


def alpha_voice(**change):
    """The richest object: every relocation type, aligned sections, locals, data, rodata, bss."""
    text = code(0xE300, 0, 0xE200, 0, 0, 0, 0, 0, 0xE300, 0)  # call; jump.l; lo; hi; lo (runtime); call
    sections = [
        ('.text', change.get('text', text), 4),
        ('.text.fast', code(0x0010, 0x0010, 0, 0), 16),
        ('.rodata', bytes(range(12)), 8),
        ('.data', bytes(12), 4),
        ('.bss', 64, 32),
    ]
    syms = [
        ('alpha_register', 1, 0, GLOBAL, FUNC, 4),
        ('alpha_data', 4, 0, GLOBAL, OBJ, 12),
        ('fast', 2, 0, LOCAL, FUNC, 4),
        ('', 3, 0, LOCAL, SECTION, 0),
    ]
    imports = ['sdk_start', 'alpha_engine_id', 'sdk_idle', '___mulsi3', 'sdk_table'] + change.get('imports', [])
    syms += [(n, 0, 0, GLOBAL, 0, 0) for n in imports] + change.get('symbols', [])
    s = {n: i + 1 for i, (n, *_) in enumerate(syms) if n}
    relocs = [
        (1, 2, 10, s['sdk_start'], 0),
        (1, 6, 13, 3, 0),
        (1, 8, 6, s['alpha_engine_id'], 0),
        (1, 10, 7, s['sdk_idle'], 4),
        (1, 12, 6, s['___mulsi3'], 0),
        (1, 18, 10, s['alpha_register'], 0),
        (4, 0, 18, s['alpha_register'], 0),
        (4, 4, 18, 4, 6),
        (4, 8, 18, s['sdk_table'], 8),
    ]
    relocs += change.get('relocations', [])
    return elf(sections, syms, relocs)


def kit():
    """A synthetic kit: six machines of every kind, services, runtime members, three extensions."""
    profile = dict(
        schema=1,
        id='opxy-test',
        stock_sha256='ab' * 32,
        stock_bytes=4096,
        stock_catalog_sha256=hashlib.sha256(CATALOG).hexdigest(),
        stock_catalog_inserts=dict(engine_types=20, fx_types=40, seq_types=52),
        module_base=BASE,
        reserved_bytes=0x200000,
        table_bytes=0x4000,
        stock_records=3,
        heap_fill_base=0x8D4D0380,
        heap_fill_bytes=0x1000,
        identities=dict(
            engines=64,
            engine_none=10,
            stock_engines=[2, 3, 12],
            pages=48,
            page_none=20,
            sample_pages=[2, 7],
            effects=[1, 5, 9, 13],
            effect_page=21,
            players=[6, 7, 8, 9],
        ),
        services={
            'samples': ['sample', 'drum'],
            'players': ['player'],
            'control': ['trackControl'],
            'extensions': ['extension'],
            'master_filter': ['master_filter'],
        },
        patches=[],
    )
    machines = [
        ('alpha', 'synth', dict(engine=None, page=None)),
        ('beta', 'effect', dict(effect=None, page=21)),
        ('gamma', 'sample', dict(engine=None, page=None)),
        ('delta', 'player', dict(player=None, page=None)),
        ('eps', 'drum', dict(engine=None, page=None)),
        ('zeta', 'synth', dict(engine=None, page=None)),
    ]
    objects = [
        ('alpha/voice.o', alpha_voice(), dict(owner='alpha')),
        (
            'alpha/screen.o',
            elf(
                [('.text', code(0xE300, 0, 0, 0), 2)],
                [('alpha_register', 0, 0, GLOBAL, 0, 0), ('alpha_page_id', 0, 0, GLOBAL, 0, 0)],
                [(1, 2, 10, 1, 0), (1, 4, 6, 2, 0)],
            ),
            dict(owner='alpha'),
        ),
        ('beta/fx.o', simple(['beta_register'], ['beta_fx_id', 'beta_fx_page_id', 'sdk_start']), dict(owner='beta')),
        (
            'gamma/voice.o',
            simple(['gamma_register'], ['gamma_engine_id', 'gamma_page_id', 'gamma_screen_page_id', 'sdk_sample_play']),
            dict(owner='gamma'),
        ),
        (
            'delta/player.o',
            simple(['delta_register'], ['delta_player_id', 'delta_page_id', 'sdk_player_play']),
            dict(owner='delta'),
        ),
        ('eps/kit.o', simple(['eps_register'], ['eps_engine_id', 'sdk_sample_play'], bss=100), dict(owner='eps')),
        (
            'zeta/big.o',
            elf(
                [('.text', code(0, 0), 4), ('.bss.a', 1 << 20, 4), ('.bss.b', 1 << 20, 4)],
                [('zeta_register', 1, 0, GLOBAL, FUNC, 2), ('zeta_engine_id', 0, 0, GLOBAL, 0, 0)],
            ),
            dict(owner='zeta'),
        ),
        (
            'sdk/core.o',
            simple(
                ['sdk_start', 'kit_absent', 'sdk_idle', 'sdk_table'],
                ['stock_insert', 'alpha_engine_id', 'sdk_control_hook', 'sdk_player_hook', '___mulsi3']
                + ['%s_register' % m for m, _, _ in machines]
                + [
                    'sdk_extension_cap_hooks',
                    'sdk_extension_flt_hooks',
                    'sdk_extension_flt_processor',
                    'sdk_extension_flt_panel',
                    'sdk_extension_dup_hooks',
                ],
                bss=16,
            ),
            {},
        ),
        ('sdk/samples.o', simple(['sdk_sample_play'], ['sdk_start']), dict(service='samples')),
        ('sdk/players.o', simple(['sdk_player_play', 'sdk_player_hook'], ['sdk_idle']), dict(service='players')),
        ('sdk/control.o', simple(['sdk_control_hook']), dict(service='control')),
        ('sdk/extension_cap.o', simple(['sdk_extension_cap_hooks'], ['sdk_start']), dict(extension='cap')),
        (
            'sdk/extension_flt.o',
            simple(
                ['sdk_extension_flt_hooks', 'sdk_extension_flt_processor', 'sdk_extension_flt_panel'], ['___mulsi3']
            ),
            dict(extension='flt'),
        ),
        ('sdk/extension_dup.o', simple(['sdk_extension_dup_hooks']), dict(extension='dup')),
        ('sdk/runtime/_unused.o', simple(['___unused']), dict(runtime=True)),
        ('sdk/runtime/_mulsi3.o', simple(['___mulsi3'], ['___helper']), dict(runtime=True)),
        ('sdk/runtime/_helper.o', simple(['___helper']), dict(runtime=True)),
    ]
    src = dict(repository='https://github.com/example/m', tag='v1.0.0', commit='1' * 40, maintainer='example')
    art = b'<svg xmlns="http://www.w3.org/2000/svg"><g/></svg>'
    exts = [
        dict(
            id='cap',
            origin='official',
            claims=['record_double_tap'],
            actions=['notify'],
            name='Capture',
            description='Keeps what you play.',
            claim_labels=dict(record_double_tap='Record double-tap'),
        ),
        dict(
            id='flt',
            origin='catalog',
            claims=[],
            actions=[],
            name='Filter',
            description='A filter.',
            claim_labels={},
            master_processor=dict(
                state_id='dj', asset='flt.svg', sha256=M.sha256(art), artwork=base64.b64encode(art).decode()
            ),
        ),
        dict(
            id='dup',
            origin='catalog',
            claims=['record_double_tap'],
            actions=[],
            name='Dup',
            description='Also wants the double tap.',
            claim_labels=dict(record_double_tap='Tap, Record: 100%'),
        ),
    ]
    source = json.dumps(profile, indent=2).encode()
    entries = []
    for name, data, tags in objects:
        entries.append(
            dict(
                name=name,
                owner=tags.pop('owner', None),
                sha256=hashlib.sha256(data).hexdigest(),
                bytes=base64.b64encode(data).decode(),
                **tags,
            )
        )
    absent = {m: {'%s_register' % m: 'sdk_idle' if m == 'delta' else 'kit_absent'} for m, _, _ in machines}
    return dict(
        format=M.KIT_FORMAT,
        version='0.0.9',
        profile=profile,
        profileSource=base64.b64encode(source).decode(),
        definitions=dict(stock_insert=0x8D5C3A28, stock_far=0x8C100000),
        fixed=dict(alpha=dict(engine=30, page=25)),
        linked=['alpha', 'beta'],
        objects=entries,
        extensions=exts,
        notices='MIT License\n',
        machines=[
            dict(
                id=m,
                name=m.title(),
                kind=k,
                prefix=m,
                identity=i,
                absent=absent[m],
                features=['trackControl'] if m == 'gamma' else None,
                catalog=',\n  {"type": "%s", "id": 0}\n' % m
                if k in ('effect', 'player')
                else ',\n  {"type": "%s"}\n' % m,
                origin='catalog',
                source=src,
            )
            for m, k, i in machines
        ],
        stockCatalog=base64.b64encode(CATALOG).decode(),
        screenTemplate=TEMPLATE,
        serviceFallbacks=dict(control=dict(sdk_control_hook='kit_absent'), players=dict(sdk_player_hook='sdk_idle')),
    )


def finish(k):
    return {m['id']: {} for m in k['machines']}


def strip_none(k):
    for m in k['machines']:
        if m.get('features') is None:
            m.pop('features', None)
    return k


def refresh(folder):
    (folder / 'files.sha256').unlink()
    (folder / 'files.sha256').write_text(E.files_sha256_text(folder))


def u16(data, at):
    return struct.unpack_from('<H', data, at)[0]


def headers(data):
    """Section headers [name, type, flags, addr, offset, size, link, info, align, entsize] and where they start."""
    start, count = struct.unpack_from('<I', data, 32)[0], struct.unpack_from('<H', data, 48)[0]
    return start, [list(struct.unpack_from('<10I', data, start + 40 * i)) for i in range(count)]


def edited(data, change):
    """data with its section headers changed in place by change(headers)."""
    start, rows = headers(data)
    change(rows)
    out = bytearray(data)
    for i, row in enumerate(rows):
        struct.pack_into('<10I', out, start + 40 * i, *row)
    return bytes(out)


def fnv1a(data, value=0x811C9DC5):
    for b in data:
        value = (value ^ b) * 0x01000193 & 0xFFFFFFFF
    return value


def bucket_names(count, length, mask=0x1FFF):
    """count distinct names of one length whose FNV-1a hashes share their low bits (one chain)."""
    inverse = pow(0x01000193, -1, mask + 1)
    want = fnv1a(b'z') & mask
    names = []
    for i in range(count * 4):
        head = b'p' * (length - 10) + b'%07d' % i
        h = fnv1a(head)
        for a in range(0x30, 0x7B):
            h1 = (h ^ a) * 0x01000193 & mask
            # the last byte c must give ((h1 ^ c) * prime) & mask == want
            c = h1 ^ (want * inverse & mask)
            if 0x30 <= c < 0x7B:
                names.append((head + bytes([a, c])).decode())
                break
        if len(names) == count:
            return names
    raise AssertionError('not enough names')


def hash_names(stages, seed=5):
    """2**stages distinct names of one length with one full FNV-1a hash (chained 4-byte collisions)."""
    rng = random.Random(seed)
    names, state = [b'q'], fnv1a(b'q')
    for _ in range(stages):
        seen = {}
        while True:
            block = bytes(rng.randrange(0x30, 0x7B) for _ in range(4))
            h = fnv1a(block, state)
            if h in seen and seen[h] != block:
                break
            seen[h] = block
        names = [n + b for n in names for b in (seen[h], block)]
        state = h
    return [n.decode() for n in names]


@pytest.mark.gate
class Synthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='mod-link-test-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        path = cls.work / 'kit.json'
        path.write_text(json.dumps(strip_none(kit())))
        cls.gen = Path(M.split_kit(path, cls.work / 'packages', finish=finish)['root'])
        cls.bridge = L.build()
        cls.sanitized = L.build(sanitize=True)
        cls.core = None
        if has_core():
            cls.core = M.sdk_tree(OPEN_XY, L.SDK_COMMIT, cls.work)

    def link(self, machines, extensions=(), gen=None, layout=None, bridge=None):
        return L.link_native(
            bridge or self.bridge, gen or self.gen, list(machines), list(extensions), self.work, layout
        )

    def variant(self, change):
        """A copy of the package set changed by change(folder), files.sha256 refreshed."""
        target = self.work / ('variant-%d' % len(list(self.work.glob('variant-*'))))
        shutil.copytree(self.gen, target)
        for package in change(target) or ():
            refresh(target / package)
        return target

    def setUp(self):
        self.queued = []  # refusal's core.mjs links: one Node run when the test ends
        self.addCleanup(self.queued_agree_with_core)

    def queue_core(self, machines, extensions, gen):
        """Link natively now; core.mjs links the same selection (light) in queued_agree_with_core."""
        if not self.core:
            return
        gen, n = Path(gen).resolve(), len(self.queued)
        name = 'queued-%s-%d' % (self._testMethodName, n)
        try:
            kit = M.rebuild_kit(gen, list(machines) or [L._first(gen)], list(extensions), CATALOG)
        except M.KitError as e:
            kit = str(e)
        if not isinstance(kit, str):
            (self.work / (name + '.json')).write_text(json.dumps(kit))
        self.queued.append(
            (
                name,
                kit,
                list(machines),
                list(extensions),
                L.link_native(self.bridge, gen, list(machines), list(extensions), self.work, name=name),
            )
        )

    def queued_agree_with_core(self):
        jobs = [
            dict(name=name, kit=str(self.work / (name + '.json')), machines=machines, extensions=exts, open='light')
            for name, kit, machines, exts, _ in self.queued
            if not isinstance(kit, str)
        ]
        reference = L.run_reference(self.core, jobs, self.work) if jobs else {}
        for name, kit, _, _, native in self.queued:
            ref = dict(reference.get(name) or dict(error=kit))
            ref.pop('name', None)
            self.assertEqual(L.compare(native, ref), [], name)

    def against_core(self, selections, gen=None, layouts=None):
        if not self.core:
            return None
        rows = L.prove_selections(
            gen or self.gen, selections, self.core, CATALOG, self.work, self.bridge, open_mode='light', layouts=layouts
        )
        for row in rows:
            self.assertTrue(row['identical'], row)
        return rows

    # ---------------------------------------------------------------- links

    def test_hand_checks(self):
        r = self.link(['alpha'])
        self.assertEqual(r['status'], 'ok', r)
        g = r['globals']
        module = self.work / 'mod.bin'
        L.link_native(self.bridge, self.gen, ['alpha'], [], self.work, keep=module)
        data = module.read_bytes()
        self.assertEqual(len(data), r['sizes']['initialized'])

        # alpha/voice.o's .text is first, at the module base (the text group's alignment is 16).
        def target(at):  # a PCREL24 pair at `at` - 2: the address it reaches
            high, low = u16(data, at - 2), u16(data, at)
            value = (high & 0xFF) << 16 | low
            value -= (value & 0x800000) << 1
            return BASE + at - 2 + 2 * value

        self.assertEqual(u16(data, 0) & 0xFF00, 0xE300)
        self.assertEqual(target(2), g['sdk_start'])
        self.assertEqual(target(18), g['alpha_register'])
        self.assertEqual(g['alpha_register'], BASE)
        self.assertEqual(u16(data, 4) & 0xFF00, 0xE200)
        self.assertEqual(target(6) % 16, 0)  # .text.fast, 16-byte aligned
        self.assertEqual(u16(data, 8), 30)  # alpha_engine_id, fixed
        self.assertEqual(u16(data, 10), (g['sdk_idle'] + 4) >> 16)
        self.assertEqual(u16(data, 12), g['___mulsi3'] & 0xFFFF)
        at = g['alpha_data'] - BASE
        first, second, third = struct.unpack_from('<3I', data, at)
        self.assertEqual((first, third), (g['alpha_register'], g['sdk_table'] + 8))
        rodata = second - 6 - BASE  # the .rodata section symbol + 6
        self.assertEqual((rodata % 8, data[rodata : rodata + 12]), (0, bytes(range(12))))
        self.assertEqual(r['numbers']['alpha'], dict(engine=30, page=25))
        self.assertIn('sdk/runtime/_mulsi3.o', r['objects'])
        self.assertIn('sdk/runtime/_helper.o', r['objects'])  # the closure: _mulsi3 imports ___helper
        self.assertNotIn('sdk/runtime/_unused.o', r['objects'])
        self.assertEqual(r['objects'][-2:], ['sdk/runtime/_helper.o', 'sdk/runtime/_mulsi3.o'])  # runtime last, by name
        self.assertEqual(r['aliases']['beta_register'], g['kit_absent'])
        self.assertEqual(r['aliases']['delta_register'], g['sdk_idle'])
        self.assertEqual(r['aliases']['sdk_player_hook'], g['sdk_idle'])  # players service unused
        self.assertEqual(r['aliases']['sdk_extension_flt_panel'], g['kit_absent'])
        self.assertEqual(g['beta_fx_id'], 0)  # unselected machines' numbers are 0
        self.assertEqual(r['services'], [])

    def test_services_numbers_and_extensions(self):
        r = self.link(['gamma', 'delta', 'beta'], ['flt'])
        self.assertEqual(r['status'], 'ok', r)
        self.assertEqual(r['services'], ['control', 'extensions', 'master_filter', 'players', 'samples'])
        self.assertEqual(r['extensions'], ['flt'])
        g = r['globals']
        self.assertEqual(g['gamma_page_id'], 2)  # a sample engine with its own screen
        self.assertEqual(g['gamma_screen_page_id'], r['numbers']['gamma']['page'])
        self.assertNotIn('sdk_player_hook', r['aliases'])
        self.assertEqual(r['aliases']['sdk_extension_cap_hooks'], g['kit_absent'])

    def test_every_selection_matches_core(self):
        if not self.core:
            self.skipTest('Node or the open-xy upstream clone at %s absent' % L.SDK_COMMIT[:7])
        ids = ['alpha', 'beta', 'gamma', 'delta', 'eps']
        sels = [('m%d' % mask, [m for i, m in enumerate(ids) if mask >> i & 1], []) for mask in range(1, 32)]
        sels += [('ext-%s' % e, ['beta'], e.split('+')) for e in ('cap', 'flt', 'dup', 'cap+flt', 'flt+dup')]
        sels += [
            ('zeta', ['zeta'], []),
            ('none', [], []),
            ('ext-only', [], ['cap']),
            ('claims', ['alpha'], ['cap', 'dup']),
            ('unknown', ['nope'], []),
            ('twice', ['alpha', 'alpha'], []),
            ('unknown-ext', ['alpha'], ['nope']),
        ]
        rows = self.against_core(sels)
        refused = {r['selection']: r['status'] for r in rows if r['error']}
        self.assertEqual(
            refused,
            dict(
                zeta='capacity',
                none='no_machine',
                claims='claim',
                unknown='unknown_id',
                twice='duplicate_selection',
                **{'ext-only': 'no_machine', 'unknown-ext': 'unknown_id'},
            ),
        )

    def test_layouts_match_core(self):
        layouts = {
            'held': dict(gamma=dict(engine=40, page=30), old=dict(engine=41, page=31)),
            'forks': dict(gamma=dict(engine=40, page=7)),
            'both': dict(x=dict(engine=40, page=30), y=dict(engine=40, page=31)),
            'fixed': dict(alpha=dict(engine=31, page=25)),
            'unusable': dict(gamma=dict(engine=2, page=30)),
            'dropped-effect': dict(beta=dict(effect=2, page=21)),
        }
        native = {name: self.link(['alpha', 'beta', 'gamma'], layout=lay) for name, lay in layouts.items()}
        self.assertEqual(native['held']['numbers']['gamma'], dict(engine=40, page=30))
        self.assertIn('old', native['held']['numbers'])
        self.assertEqual((native['forks']['status'], native['forks']['detail']), ('forks', 'gamma'))
        self.assertEqual(native['both']['status'], 'both_hold')
        self.assertEqual(native['fixed']['status'], 'layout_fixed')
        self.assertEqual(native['unusable']['status'], 'layout_invalid')
        self.assertEqual(native['dropped-effect']['status'], 'ok')
        self.against_core([(n, ['alpha', 'beta', 'gamma'], []) for n in layouts], layouts=layouts)

    # ---------------------------------------------------------------- refusals

    def refusal(self, change, machines, expected, extensions=()):
        gen = self.variant(change) if change else self.gen
        r = self.link(machines, extensions, gen=gen)
        self.assertEqual(r['status'], expected[0], r)
        if len(expected) > 1:
            self.assertEqual(r['detail'], expected[1])
        self.queue_core(machines, extensions, gen)
        return r

    def put_object(self, package, name, data):
        def change(folder):
            (folder / package / 'objects' / name).write_bytes(data)
            return [package]

        return change

    def test_refuses_capacity(self):
        self.refusal(None, ['zeta'], ('capacity', '2032'))
        self.assertEqual(
            L.same_refusal(
                'capacity', '2032', 'These machines need more than the 2032 KiB available: zeta 2048.0 KiB, sdk 0.1 KiB'
            ),
            True,
        )

    def test_refuses_undefined_symbol(self):
        self.refusal(
            self.put_object(
                'alpha', 'alpha/voice.o', alpha_voice(imports=['sdk_missing'], relocations=[(1, 14, 6, 10, 0)])
            ),
            ['alpha'],
            ('unresolved', 'sdk_missing'),
        )

    def test_refuses_duplicate_definition(self):
        self.refusal(
            self.put_object('alpha', 'alpha/voice.o', alpha_voice(symbols=[('kit_absent', 1, 2, GLOBAL, FUNC, 2)])),
            ['alpha'],
            ('duplicate_global', 'kit_absent'),
        )

    def test_refuses_unsupported_relocation(self):
        self.refusal(
            self.put_object('alpha', 'alpha/voice.o', alpha_voice(relocations=[(1, 14, 3, 1, 0)])),
            ['alpha'],
            ('relocation_type',),
        )

    def test_refuses_malformed_and_truncated_objects(self):
        good = alpha_voice()
        for name, data, status in (
            ('short', good[:40], 'object_size'),
            ('cut', good[:200], 'section_table'),
            ('magic', b'\x7fELG' + good[4:], 'object_type'),
            ('overlap', alpha_voice(relocations=[(1, 8, 7, 1, 0)]), 'relocation_overlap'),
            ('opcode', alpha_voice(text=code(0, 0, 0xE200, 0, 0, 0, 0, 0, 0xE300, 0)), 'branch'),
            ('overflow', alpha_voice(relocations=[(1, 14, 6, 6, -0x80000000)]), 'overflow'),
            ('weak', alpha_voice(symbols=[('alpha_weak', 1, 0, WEAK, FUNC, 2)]), 'weak'),
            ('outside', alpha_voice(symbols=[('alpha_far', 3, 10, GLOBAL, OBJ, 8)]), 'symbol_outside'),
        ):
            with self.subTest(name):
                self.refusal(self.put_object('alpha', 'alpha/voice.o', data), ['alpha'], (status,))

    def test_refuses_mixed_generations(self):
        def change(folder):
            info = (TK.legacy_record(folder / 'beta')).read_text().replace('generation=', 'generation=0.0.8-')
            (TK.legacy_record(folder / 'beta')).write_text(info)
            return ['beta']

        self.refusal(change, ['alpha', 'beta'], ('generation', 'beta'))

    def test_refuses_zero_machines_and_extension_alone(self):
        self.refusal(None, [], ('no_machine',))
        self.refusal(None, [], ('no_machine',), ['cap'])

    def test_refuses_runtime_replacing_a_definition(self):
        self.refusal(
            self.put_object('base', 'sdk/runtime/_helper.o', simple(['___helper', 'sdk_idle'])),
            ['alpha'],
            ('runtime_replaces', 'sdk/runtime/_helper.o'),
        )

    def test_reads_the_numbering_rules_from_identities_txt(self):
        def edit(path, change):
            def apply(folder):
                p = folder / 'base' / path
                if change is None:
                    p.unlink()
                else:
                    p.write_text(change(p.read_text()))
                return ['base']

            return apply

        def link(change, path='identities.txt'):
            return self.link(['alpha'], gen=self.variant(edit(path, change)))

        sub = lambda a, b: lambda t: t.replace(a, b)
        self.assertEqual(
            (self.gen / 'base/identities.txt').read_text().splitlines()[1:3], ['engines 64', 'engine_none 10']
        )
        self.assertEqual(link(sub('engines 64', 'engines 5'))['status'], 'collection')  # the file's rules are used
        self.assertEqual(link(sub('sample_pages 2,7', 'sample_pages -'))['status'], 'ok')
        for change, line in (
            (sub('engines 64', 'engines x'), 2),
            (sub('pages 48', 'pages 48 1'), 4),
            (lambda t: t + 'players 6\n', 11),
            (sub('effect_page 21', 'effect_pages 21'), 6),
            (sub('effects 1,5', 'effects 1,,5'), 9),
        ):
            r = link(change)
            self.assertEqual((r['status'], r['detail']), ('table', 'identities.txt:%d' % line), r)
        for change in (sub('players 6,7,8,9\n', ''), sub('page_none 20', 'page_none 4096')):
            r = link(change)
            self.assertEqual((r['status'], r['detail']), ('table', 'base/identities.txt'), r)
        with self.assertRaisesRegex(RuntimeError, 'missing base/identities.txt'):
            link(None)

    def test_claim_refusal_names_the_label(self):
        r = self.link(['alpha'], ['cap', 'dup'])
        self.assertEqual((r['status'], r['detail']), ('claim', 'Record double-tap'))  # cap's: first in the collection
        self.assertEqual(self.link(['alpha'], ['dup', 'cap'])['detail'], 'Record double-tap')
        rows = (self.gen / 'base/extensions.txt').read_text().splitlines()
        self.assertIn('labels=Tap%2C%20Record%3A%20100%25', rows[3])
        self.assertIn('claims=- labels=-', rows[2])

        def labels(cap, dup='Tap%2C%20Record%3A%20100%25'):
            def apply(folder):
                p = folder / 'base/extensions.txt'
                text = p.read_text().replace('labels=Record%20double-tap', 'labels=' + cap)
                p.write_text(text.replace('labels=Tap%2C%20Record%3A%20100%25', 'labels=' + dup))
                return ['base']

            return self.link(['alpha'], ['cap', 'dup'], gen=self.variant(apply))

        self.assertEqual(labels('', 'Tap')['detail'], 'Tap')  # no label: core.mjs looks further
        self.assertEqual(labels('', '')['detail'], 'record double tap')  # none at all: the claim's words
        self.assertEqual(labels('%41%62')['detail'], 'Ab')
        for bad in ('Record%2', 'Record%zz', 'a,b'):
            r = labels(bad)
            self.assertEqual((r['status'], r['detail']), ('table', 'extensions.txt:2'), (bad, r))

        def unlabelled(folder):  # no labels= field
            p = folder / 'base/extensions.txt'
            p.write_text(p.read_text().replace(' labels=Record%20double-tap', ''))
            return ['base']

        r = self.link(['alpha'], ['cap'], gen=self.variant(unlabelled))
        self.assertEqual((r['status'], r['detail']), ('table', 'extensions.txt:2'), r)

    def test_refuses_bad_absent_and_fallback_tables(self):
        def absent(folder):
            p = folder / 'base/absent.txt'
            p.write_text(p.read_text().replace('beta beta_register kit_absent', 'beta beta_register nothing_here'))
            return ['base']

        def fallback(folder):
            p = folder / 'base/fallbacks.txt'
            p.write_text(
                p.read_text().replace('control sdk_control_hook kit_absent', 'control sdk_control_hook nowhere')
            )
            return ['base']

        def request(folder):
            p = folder / 'base/machines.txt'
            p.write_text(p.read_text().replace('kind=synth prefix=alpha engine=-', 'kind=synth prefix=alpha engine=2'))
            return ['base']

        self.refusal(absent, ['alpha'], ('absent_machine', 'beta_register'))
        self.refusal(fallback, ['alpha'], ('service_fallback', 'sdk_control_hook'))
        self.refusal(request, ['alpha'], ('numbers_request', 'alpha'))

    # ---------------------------------------------------------------- malformed input

    def mutants(self, data, count, seed):
        rng = random.Random(seed)
        out = [data[:n] for n in sorted({0, 1, 51, 52, 53} | {rng.randrange(len(data)) for _ in range(count // 4)})]
        while len(out) < count:
            b = bytearray(data)
            for _ in range(rng.choice((1, 1, 1, 2, 3))):
                at = rng.randrange(len(b))
                b[at] ^= 1 << rng.randrange(8)
            out.append(bytes(b))
        return out

    def test_object_reader_fuzz_matches_core(self):
        data = alpha_voice()
        mutants = self.mutants(data, 300, 541)
        folder = self.work / 'fuzz'
        folder.mkdir(exist_ok=True)
        files = []
        for i, m in enumerate(mutants):
            files.append(folder / ('%d.o' % i))
            files[-1].write_bytes(m)
        native = []
        for start in range(0, len(files), 500):
            native += L.check_objects(self.sanitized, files[start : start + 500])
        self.assertEqual(len(native), len(files))
        self.assertIn('ok', {s for s, _ in native})
        self.assertGreater(len({s for s, _ in native}), 6)
        if not self.core:
            return
        ref = L.run_reference(self.core, [dict(name='objects', objects=[str(f) for f in files])], self.work)['objects']
        for i, ((status, name), message) in enumerate(zip(native, ref['objects'])):
            with self.subTest(mutant=i):
                if message is None:
                    self.assertEqual(status, 'ok')
                else:
                    self.assertTrue(L.same_refusal(status, name, message), (status, name, message))

    def test_link_fuzz_under_sanitizers(self):
        data = alpha_voice()
        mutants = self.mutants(data, 40, 7)
        native, jobs, gen = {}, [], self.variant(lambda folder: None)  # one copy, its object replaced per mutant
        for i, m in enumerate(mutants):
            self.put_object('alpha', 'alpha/voice.o', m)(gen)
            refresh(gen / 'alpha')
            native[i] = self.link(['alpha', 'beta'], ['cap'], gen=gen, bridge=self.sanitized)
            if self.core:
                path = self.work / ('fuzz-kit-%d.json' % i)
                path.write_text(json.dumps(M.rebuild_kit(gen, ['alpha', 'beta'], ['cap'], CATALOG)))
                jobs.append(
                    dict(name=str(i), kit=str(path), machines=['alpha', 'beta'], extensions=['cap'], open='light')
                )
        shutil.rmtree(gen)
        outcomes = {r['status'] for r in native.values()}
        self.assertIn('ok', outcomes)
        self.assertGreater(len(outcomes), 3)
        if self.core:
            reference = L.run_reference(self.core, jobs, self.work)
            for i, r in native.items():
                with self.subTest(mutant=i):
                    ref = dict(reference[str(i)])
                    ref.pop('name')
                    self.assertEqual(L.compare(r, ref), [])

    # ---------------------------------------------------------------- hostile packages
    # Work is counted, not timed: the bridge reports the most XYF_WORK units one step took.

    def hostile(self, change, machines=('alpha',), args=()):
        gen = self.variant(change)
        return L.link_native(self.sanitized, gen, list(machines), [], self.work, args=args)

    def test_refuses_names_over_255_bytes(self):
        long = 'a' * 200000
        many = bytearray(
            elf([('.text', code(0, 0), 4)], [(long, 1, 0, GLOBAL, FUNC, 2)] + [('', 1, 0, LOCAL, 0, 0)] * 2000)
        )
        symtab = next(r for r in headers(many)[1] if r[1] == 2)
        for at in range(symtab[4] + 16, symtab[4] + symtab[5], 16):  # every symbol names the one long string
            struct.pack_into('<I', many, at, 1)
        cases = (
            ('symbols', bytes(many)),
            ('section', elf([('.text.' + 'x' * 300, code(0, 0), 4)], [('alpha_register', 1, 0, GLOBAL, FUNC, 2)])),
            ('256', alpha_voice(symbols=[('b' * 256, 1, 0, GLOBAL, FUNC, 2)])),
        )
        for name, data in cases:
            with self.subTest(name):
                r = self.hostile(self.put_object('alpha', 'alpha/voice.o', data))
                self.assertEqual((r['status'], r['detail']), ('name_length', 'alpha/voice.o'), r)
                self.assertLess(r['work']['units'], 1000000, r['work'])  # before: 800M in one step
        r = self.hostile(
            self.put_object('alpha', 'alpha/voice.o', alpha_voice(symbols=[('b' * 255, 1, 0, GLOBAL, FUNC, 2)]))
        )
        self.assertEqual(r['status'], 'ok', r)

        def definitions(folder):  # a table's name: refused when interned
            with open(folder / 'base/definitions.txt', 'a') as f:
                f.write('c%s 0x1\n' % ('c' * 299))
            return ['base']

        r = self.hostile(definitions)
        self.assertEqual((r['status'], r['detail']), ('name_length', 'c' * 32), r)

    # ---------------------------------------------------------------- device rules
    def test_refuses_absolute_symbols_in_package_objects(self):
        for name, sym in (
            ('global', ('alpha_abs', 0xFFF1, 0x8D000000, GLOBAL, OBJ, 0)),
            ('local', ('abs_local', 0xFFF1, 0x8C000000, LOCAL, OBJ, 0)),
        ):
            with self.subTest(name):
                r = self.hostile(self.put_object('alpha', 'alpha/voice.o', alpha_voice(symbols=[sym])))
                self.assertEqual((r['status'], r['detail']), ('absolute', 'alpha/voice.o'), r)

    def test_refuses_imports_from_other_packages(self):
        for name, machines in (('number symbol', ['alpha']), ('number symbol, both chosen', ['alpha', 'beta'])):
            with self.subTest(name):
                r = self.hostile(
                    self.put_object(
                        'alpha', 'alpha/voice.o', alpha_voice(imports=['beta_fx_id'], relocations=[(1, 14, 6, 10, 0)])
                    ),
                    machines,
                )
                self.assertEqual((r['status'], r['detail']), ('foreign_import', 'beta_fx_id'), r)
        r = self.hostile(
            self.put_object(
                'alpha', 'alpha/voice.o', alpha_voice(imports=['beta_register'], relocations=[(1, 14, 6, 10, 0)])
            ),
            ['alpha', 'beta'],
        )
        self.assertEqual((r['status'], r['detail']), ('foreign_import', 'beta_register'), r)
        self.assertEqual(self.link(['alpha', 'beta'])['status'], 'ok')  # own package, base and own numbers

    def test_refuses_too_many_claims(self):
        def claims(count):
            def apply(folder):
                p = folder / 'base/extensions.txt'
                text = re.sub(
                    r'(\S+ cap .*?)claims=\S+ labels=\S+',
                    lambda m: (
                        m.group(1)
                        + 'claims=%s labels=%s'
                        % (','.join('c%d' % i for i in range(count)), ','.join('L%d' % i for i in range(count)))
                    ),
                    p.read_text(),
                    count=1,
                )
                p.write_text(text)
                return ['base']

            return apply

        r = L.link_native(self.sanitized, self.variant(claims(17)), ['alpha'], ['cap'], self.work)
        self.assertEqual((r['status'], r['detail']), ('claim_count', 'cap'), r)
        r = L.link_native(self.sanitized, self.variant(claims(16)), ['alpha'], ['cap'], self.work)
        self.assertEqual(r['status'], 'ok', r)

    def test_refuses_nul_bytes_and_long_info_values(self):
        def append(package, name, data):
            def apply(folder):
                path = TK.legacy_record(folder / package) if name == 'info.txt' else folder / package / name
                with path.open('ab') as f:
                    f.write(data)
                return [package]

            return apply

        for package, name in (
            ('alpha', 'info.txt'),
            ('alpha', 'objects.txt'),
            ('base', 'info.txt'),
            ('base', 'services.txt'),
        ):
            with self.subTest(package + '/' + name):
                r = self.hostile(append(package, name, b'# x\0y\n'))
                self.assertEqual((r['status'], r['detail']), ('text_nul', name), r)

        def replace(old, new):
            def apply(folder):
                p = TK.legacy_record(folder / 'alpha')
                self.assertIn(old, p.read_text())
                p.write_text(p.read_text().replace(old, new))
                return ['alpha']

            return apply

        for key, cap in (('prefix', 48), ('id', 33)):
            with self.subTest(key):
                r = self.hostile(replace('\n%s=alpha\n' % key, '\n%s=alpha%s\n' % (key, 'a' * (cap - 5))))
                self.assertEqual((r['status'], r['detail']), ('text_length', key), r)

    def test_records_limit_is_16_bit(self):
        def scratch(records):
            run = subprocess.run(
                [str(self.bridge), '--records', str(records)], capture_output=True, text=True, check=True
            )
            return int(run.stdout)

        self.assertGreater(scratch(65535), 0)
        self.assertEqual(scratch(65536), 0)

        def no_objects(folder):  # base and the chosen package list no objects
            for package in ('base', 'alpha'):
                p = folder / package / 'objects.txt'
                p.write_text(''.join(line for line in p.read_text().splitlines(True) if line.startswith('#')))
            return ['base', 'alpha']

        def nothing_linked(folder):  # only inactive service and unchosen runtime objects; base unaligned
            p = folder / 'base/objects.txt'
            p.write_text(
                ''.join(
                    line
                    for line in p.read_text().splitlines(True)
                    if line.startswith('#')
                    or line.split()[1:2] == ['runtime']
                    or line.split()[1].startswith('service=')
                )
            )
            p = folder / 'alpha/objects.txt'
            p.write_text(''.join(line for line in p.read_text().splitlines(True) if line.startswith('#')))
            p, old = TK.legacy_record(folder / 'base'), 'module_base=0x%08x' % BASE
            self.assertIn(old, p.read_text())
            p.write_text(p.read_text().replace(old, 'module_base=0x%08x' % (BASE + 1)))
            with open(folder / 'base/definitions.txt', 'a') as f:  # what the aliases need, without sdk/core.o
                f.write('kit_absent 0x8d000000\nsdk_idle 0x8d000004\n')
            return ['base', 'alpha']

        for name, change in (('no objects', no_objects), ('nothing linked', nothing_linked)):
            with self.subTest(name):
                r = self.hostile(change)  # before: stale tables, then wild writes or no end
                self.assertEqual(r['status'], 'empty', r)
                self.assertLess(r['step'], 40, r)

    def test_overlap_check_is_linear(self):
        count = 2000  # one-entry relocation tables, each at its own 2-byte section
        many = elf(
            [('.text.%d' % i, code(0), 2) for i in range(count)],
            [('alpha_register', 1, 0, GLOBAL, FUNC, 2)],
            [(i + 1, 0, 6, 1, 0) for i in range(count)],
        )
        gen = self.variant(self.put_object('alpha', 'alpha/voice.o', many))
        r = self.link(['alpha'], gen=gen, bridge=self.sanitized)
        self.assertEqual(r['status'], 'ok', r)
        self.assertLess(r['work']['units'], 3000000, r['work'])  # before: over 8M header decodes
        self.against_core([('case', ['alpha'], [])], gen=gen)
        big = 300  # tables at sections that all alias one 256 KiB range

        def alias(rows):
            for row in rows[2 : big + 1]:
                row[4], row[5] = rows[1][4], rows[1][5]

        aliased = edited(
            elf(
                [('.text.0', bytes(1 << 18), 4)] + [('.text.%d' % i, code(0), 2) for i in range(1, big)],
                [('alpha_register', 1, 0, GLOBAL, FUNC, 2)],
                [(i + 1, 0, 6, 1, 0) for i in range(big)],
            ),
            alias,
        )
        r = self.refusal(self.put_object('alpha', 'alpha/voice.o', aliased), ['alpha'], ('capacity',))
        self.assertLess(r['work']['units'], 3000000, r['work'])  # before: a 32 KiB clear per table

    def test_name_chains_are_bounded(self):
        def objects(names, relocations=0):
            definer = elf(
                [('.text', code(*[0] * 2 * len(names)), 4)],
                [('alpha_register', 1, 0, GLOBAL, FUNC, 2)] + [(n, 1, 0, GLOBAL, FUNC, 2) for n in names],
            )
            importer = elf(
                [('.data', bytes(4 * max(1, relocations)), 4)],
                [(names[0], 0, 0, GLOBAL, 0, 0)],
                [(1, 4 * i, 18, 1, 0) for i in range(relocations)],
            )

            def change(folder):
                (folder / 'alpha/objects/alpha/voice.o').write_bytes(definer)
                (folder / 'alpha/objects/alpha/screen.o').write_bytes(importer)
                return ['alpha']

            return change

        r = self.hostile(objects(bucket_names(60, 200), 5000))  # many lookups of a chain's oldest name
        self.assertEqual(r['status'], 'ok', r)
        self.assertLess(r['work']['units'], 15000000, r['work'])  # before: about 60M in one step
        r = self.hostile(objects(bucket_names(70, 200)))
        self.assertEqual(r['status'], 'name_chain', r)
        same = hash_names(3)
        self.assertEqual(len({fnv1a(n.encode()) for n in same}), 1)
        r = self.hostile(objects(same))
        self.assertEqual((r['status'], r['detail']), ('name_chain', same[4]), r)

    def test_pass_two_refuses_a_changed_file(self):
        """The file pass 2 reads has pass 1's hash and size (forged) but different sections."""
        one = elf(
            [
                ('.text', code(0, 0, 0, 0), 4),
                ('.text.fast', code(0x10, 0x10, 0, 0), 4),
                ('.data', bytes(12), 4),
                ('.text.x', bytes(12), 4),
            ],
            [('alpha_register', 1, 0, GLOBAL, FUNC, 2)],
            [(3, 0, 18, 1, 0)],
        )
        first = edited(one, lambda rows: rows[4].__setitem__(2, 0)) + bytes(8)  # .text.x not allocated

        def forge(data):  # new last 5 bytes so that FNV-1a(data) == FNV-1a(first): meet in the middle
            data, state, inverse = data[:-5], fnv1a(data[:-5]), pow(0x01000193, -1, 1 << 32)
            forward = {fnv1a(bytes([a, b]), state): bytes([a, b]) for a in range(256) for b in range(256)}
            target = fnv1a(first) * inverse & 0xFFFFFFFF
            for tail in range(1 << 24):
                c, d, e = tail >> 16, tail >> 8 & 255, tail & 255
                h = (((target ^ e) * inverse & 0xFFFFFFFF) ^ d) * inverse & 0xFFFFFFFF ^ c
                if h in forward:
                    return data + forward[h] + bytes([c, d, e])
            raise AssertionError('no forgery')

        def larger(rows):
            rows[2][5] = 12  # .text.fast 8 -> 12 bytes

        def regroup(rows):
            rows[2][0] = rows[3][0]  # .text.fast named .data

        def retarget(rows):
            rows[4][2] = 6  # .text.x allocated and the .data relocations aimed at it
            rows[5][7] = 4

        def put(folder):
            (folder / 'alpha/objects/alpha/voice.o').write_bytes(first)
            return ['alpha']

        for name, change in (('size', larger), ('group', regroup), ('unplaced', retarget)):
            with self.subTest(name):
                second = forge(edited(first, change))
                self.assertEqual((fnv1a(second), len(second)), (fnv1a(first), len(first)))
                path = self.work / ('second-%s.o' % name)
                path.write_bytes(second)
                r = self.hostile(put, args=('-2', 'alpha/objects/alpha/voice.o=%s' % path))
                self.assertEqual((r['status'], r['detail']), ('changed', 'alpha/voice.o'), r)
        self.assertEqual(self.hostile(put)['status'], 'ok')


@unittest.skipUnless(
    has_core() and REAL.is_dir() and L.CATALOG.is_file(),
    'the 0.0.6 package set, op1_factory.json, Node or the open-xy upstream clone at %s absent' % L.SDK_COMMIT[:7],
)
@pytest.mark.gate
class PublishedPackages(unittest.TestCase):
    """The real package set: a sample of modkit prove's selections (TK.sampled_selections), natively and
    with core.mjs."""

    @classmethod
    def setUpClass(cls):
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='mod-link-real-', dir=paths.SESSIONS)
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        cls.core = M.sdk_tree(OPEN_XY, L.SDK_COMMIT, cls.work)
        cls.rows = {
            r['selection']: r
            for r in L.prove_selections(
                REAL, TK.sampled_selections(L.selections(REAL)), cls.core, L.CATALOG.read_bytes(), cls.work
            )
        }

    def test_sampled_selections_are_identical(self):
        self.assertEqual(len(self.rows), 8)
        for name, row in self.rows.items():
            with self.subTest(selection=name):
                self.assertTrue(row['identical'], row)
                self.assertEqual(row['status'], 'capacity' if name == 'all' else 'ok')

    def test_sizes_match_records_008_and_014(self):
        allocated = {
            n: self.rows[n]['allocated'] for n in ('one', 'one+capture', 'one+master_dj_filter', 'one+all-extensions')
        }
        self.assertEqual(
            allocated,
            {'one': 148120, 'one+capture': 161440, 'one+master_dj_filter': 194484, 'one+all-extensions': 202584},
        )

    def test_whole_kit_fits_the_default_limits(self):
        usage = self.rows['all']['usage']
        limits = dict(objects=640, sections=4096, names=4608, records=4096, text=128 * 1024, absent=1024)
        for key, bound in limits.items():
            self.assertLess(usage[key], bound, key)


@unittest.skipUnless(
    has_core() and (REAL / 'base/profile.json').is_file(),
    'numbers.json (open-xy upstream clone) or the 1.2.1 profile absent',
)
@pytest.mark.gate
class NumbersFixture(unittest.TestCase):
    """open-xy tests/fixtures/numbers.json against the native numberLayout (the 1.2.1 rules)."""

    def test_cases(self):
        fixture = json.loads(
            subprocess.run(
                ['git', '-C', str(OPEN_XY), 'show', L.SDK_COMMIT + ':tests/fixtures/numbers.json'],
                capture_output=True,
                check=True,
            ).stdout
        )
        with tempfile.TemporaryDirectory(prefix='mod-link-numbers-') as tmp:
            bridge = L.build()
            rules = Path(tmp) / 'rules.txt'
            rules.write_text(L.rules_text(json.loads((REAL / 'base/profile.json').read_text())))
            fixed = [tuple(f) for f in fixture['fixed']]
            for case in fixture['cases']:
                with self.subTest(case['name']):
                    got = L.numbers_native(bridge, rules, fixed, case.get('layout') or {}, case['machines'], tmp)
                    if 'error' in case:
                        self.assertIsInstance(got, tuple, got)
                        text, fragment = L.MESSAGES[got[0]].replace('{}', got[1]), case['error']
                        truncated = (
                            len(got[1].encode()) >= 32
                            and any(  # the detail's end starts the fragment
                                got[1].endswith(fragment[:n]) for n in range(6, len(fragment))
                            )
                        )
                        self.assertTrue(fragment in text or truncated, (fragment, got))
                    else:
                        for f in fixed:
                            got.pop(f[0], None)
                        self.assertEqual(got, case['numbers'])


@pytest.mark.gate
class BlackfinCompile(unittest.TestCase):
    """The linker compiles for the device with the firmware's flags: no library calls, no BSS or data,
    small frames (compile only; nothing is linked into a build)."""

    def test_freestanding(self):
        from xy_forever import toolchain

        tools = toolchain.location() / 'bin/bfin-elf-g++'
        if not tools.exists():
            self.skipTest('Blackfin toolchain absent')
        with tempfile.TemporaryDirectory(prefix='mod-link-cross-') as tmp:
            report = L.cross(tmp)
        for name, r in report['sources'].items():
            with self.subTest(name):
                self.assertEqual(r['calls'], [])
                self.assertEqual((r['bss'], r['data']), (0, 0))
                self.assertLess(r['largest_frame'], 1024)


if __name__ == '__main__':
    unittest.main()
