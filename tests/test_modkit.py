"""Mod packages from an open-xy kit (xy_forever/modkit.py; docs/storage/mods.md).

Synthetic kits made here from scratch run in the standard suite: package layout, the device's
manifest and files.sha256 readers (tests/test_xyf_manifest.py's bridge), edits.bin, the rebuilt
kit and every refusal. The real published kit, stock os.ldr and op1_factory.json (private area)
and open-xy's core.mjs (the open-xy upstream clone at the kit's SDK commit) run the byte-for-byte proof when
present and skip otherwise."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from xy_forever import modkit as M, package_records as R, paths, xyf_entry as E  # noqa: E402
import test_xyf_manifest as TM  # noqa: E402

SDK_COMMIT = '52c8de911a415e043fd4d1afc396c3869fcd1029'    # open-xy main, release 0.0.6 (the kit's SDK)
OPEN_XY = paths.OPEN_XY_UPSTREAM                     # a fresh upstream clone (never ../open-xy)
REAL_KIT = paths.PUBLISHED_KIT
STOCK = paths.STOCK_121 / 'os.ldr'
CATALOG = paths.CATALOG_121
TEMPLATE = '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="220"><rect/></svg>'
CATALOG_BYTES = b'{"engine_types": [], "fx_types": [], "seq_types": []}\n'
GLOBAL, FUNC, OBJ = 1, 2, 1


def obj(exports=(), imports=(), text=b'\0\0\0\0', data=b'', bss=0, absolute=()):
    sections = [('.text', text)] + ([('.data', data)] if data else []) + ([('.bss', bss)] if bss else [])
    syms = [(n, 1, 0, GLOBAL, FUNC) for n in exports] + [(n, 0, 0, GLOBAL, 0) for n in imports]
    syms += [(n, M.SHN_ABS, 0x8d000000, 0, OBJ) for n in absolute]
    relocs = [(1, 0, 18, len(exports) + 1, 0)] if imports else []
    return M.make_object(sections, syms, relocs)


def entry(name, data, **tags):
    return dict(name=name, owner=tags.pop('owner', None), sha256=hashlib.sha256(data).hexdigest(),
                bytes=base64.b64encode(data).decode(), **tags)


def synthetic_kit():
    """A small kit with every part the tool handles: core, service and runtime objects, a synth, an effect,
    an event extension, a master processor, edits of every kind."""
    profile = dict(schema=1, id='opxy-test', stock_sha256='ab' * 32, stock_bytes=4096,
                   stock_catalog_sha256=hashlib.sha256(CATALOG_BYTES).hexdigest(),
                   stock_catalog_inserts=dict(engine_types=20, fx_types=40, seq_types=52),
                   module_base=0x8d2d0380, reserved_bytes=0x200000, table_bytes=0x4000, stock_records=3,
                   heap_fill_base=0x8d4d0380, heap_fill_bytes=0x1000,
                   identities=dict(engines=64, engine_none=10, stock_engines=[2, 3], pages=48, page_none=20,
                                   sample_pages=[], effects=[1, 5], effect_page=21, players=[6]),
                   services={'extensions': ['extension'], 'master_filter': ['master_filter'], 'drums': ['drum']},
                   patches=[
                       dict(kind='heap', name='heap', address='0x8d4d03ba', file_offset='0x107616', length=8,
                            before='01da400100000000', after='01da200100000000'),
                       dict(kind='startup', name='label', address='0x8d000010', file_offset='0x10', length=10,
                            before='00' * 10, symbol='sdk_start'),
                       dict(kind='hook', address='0x8d000040', file_offset='0x40', length=4, before='ffe38bfe',
                            symbol='sdk_ext_event', call=True, service='extensions', events=['context']),
                       dict(kind='hook', address='0x8d000050', file_offset='0x50', length=4, before='ffe38bfe',
                            symbol='sdk_start', call=False, profiling=False),
                       dict(kind='text', name='allow', address='0x8d000060', file_offset='0x60', length=2,
                            before='0000', after='0100', service='drums')])
    source = json.dumps(profile, indent=2).encode()
    art = b'<svg xmlns="http://www.w3.org/2000/svg"><g/></svg>'
    objects = [
        entry('alpha/voice.o', obj(['alpha_register'], ['sdk_start', 'alpha_engine_id'], data=b'1234', bss=64),
              owner='alpha'),
        entry('beta/fx.o', obj(['beta_register'], ['sdk_start', 'beta_fx_id']), owner='beta'),
        entry('sdk/core.o', obj(['sdk_start', 'kit_absent', 'sdk_ext_event'], ['stock_insert'], bss=16)),
        entry('sdk/drums.o', obj(['sdk_drum'], ['sdk_start']), service='drums'),
        entry('sdk/extension_cap.o', obj(['sdk_extension_cap_hooks'], ['sdk_start']), extension='cap'),
        entry('sdk/extension_flt.o', obj(['sdk_extension_flt_hooks', 'sdk_extension_flt_processor',
                                          'sdk_extension_flt_panel'], ['___mulsi3']), extension='flt'),
        entry('sdk/runtime/_mul.o', obj(['___mulsi3']), runtime=True)]
    src = dict(repository='https://github.com/example/alpha', tag='v1.0.0', commit='1' * 40, maintainer='example')
    machines = [
        dict(id='alpha', name='Alpha', kind='synth', prefix='alpha', identity=dict(engine=None, page=None),
             absent=dict(alpha_register='kit_absent'), catalog=',\n  {"type": "alpha"}\n', origin='catalog',
             source=src, artwork='<g id="alpha"/>', asset='alpha.svg', preview=dict(wasm='AAAA', defaults=[1]),
             presets={}, keywords=['fm']),
        dict(id='beta', name='Beta', kind='effect', prefix='beta', identity=dict(effect=None, page=2),
             absent=dict(beta_register='kit_absent'), catalog=',\n  {"type": "beta", "id": 0}\n',
             origin='official', source=dict(src, repository='https://github.com/example/beta'))]
    extensions = [
        dict(id='cap', origin='official', claims=['record_double_tap'], actions=['notify'], name='Capture',
             description='Keeps what you play.', claim_labels=dict(record_double_tap='Record double-tap')),
        dict(id='flt', origin='catalog', claims=[], actions=[], name='Filter', description='A filter.',
             claim_labels={}, master_processor=dict(state_id='dj', asset='flt.svg', sha256=M.sha256(art),
                                                    artwork=base64.b64encode(art).decode()))]
    return dict(format=M.KIT_FORMAT, version='0.0.9', profile=profile,
                profileSource=base64.b64encode(source).decode(), definitions=dict(stock_insert=0x8d5c3a28),
                fixed=dict(alpha=dict(engine=34, page=82)), linked=['alpha'], machines=machines, objects=objects,
                notices='OP-XY SDK modules\n\nMIT License\n', extensions=extensions,
                stockCatalog=base64.b64encode(CATALOG_BYTES).decode(), screenTemplate=TEMPLATE,
                serviceFallbacks=dict(drums=dict(sdk_drum_entry='kit_absent')))


def finish(kit):
    """Stands in for core.mjs buildAssets: the finished svg and an init preset per machine."""
    out = {}
    for m in kit['machines']:
        files = {}
        if m.get('artwork'):
            files['content/display/' + m['asset']] = TEMPLATE.replace('</svg>', m['artwork'] + '</svg>').encode()
        if m['kind'] != 'effect':
            files['content/presets/%s/default.json' % m['id']] = b'{\n  "type": "%s"\n}\n' % m['id'].encode()
        out[m['id']] = files
    return out


def rewrite(folder, path, data):
    """Change one file of a package and refresh its files.sha256 (a well-formed but hostile package)."""
    (folder / path).parent.mkdir(parents=True, exist_ok=True)
    (folder / path).write_bytes(data)
    (folder / 'files.sha256').unlink()
    (folder / 'files.sha256').write_text(E.files_sha256_text(folder))


def legacy_record(folder):
    """An editable text metadata fixture for tests of legacy parsing."""
    native, old = folder / R.NATIVE, folder / R.LEGACY
    if native.is_file():
        data = R.text_view(native.read_bytes(), R.NATIVE)
        native.unlink()
        rewrite(folder, R.LEGACY, data)
    return old


class Synthetic(unittest.TestCase):
    parse, outcome, listing = TM.ManifestTest.parse, TM.ManifestTest.outcome, TM.ManifestTest.listing

    @classmethod
    def setUpClass(cls):
        TM.ManifestTest.setUpClass.__func__(cls)                  # the device readers, host-built
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        cls.kit = synthetic_kit()
        cls.report = cls.split(cls.kit, 'packages')
        cls.gen = Path(cls.report['root'])

    @classmethod
    def split(cls, kit, name):
        path = cls.work / (name + '.json')
        path.write_text(json.dumps(kit))
        return M.split_kit(path, cls.work / name, finish=finish, sdk_source='https://github.com/example/sdk@abc')

    def refused(self, kit, pattern):
        path = self.work / 'refused.json'
        path.write_text(json.dumps(kit))
        with self.assertRaisesRegex(M.KitError, pattern):
            M.split_kit(path, self.work / 'refused', finish=finish)
        self.assertFalse((self.work / 'refused').exists() and any((self.work / 'refused').rglob(R.NATIVE)))

    def copy_generation(self):
        target = self.work / ('copy-%d' % len(list(self.work.glob('copy-*'))))
        shutil.copytree(self.gen, target)
        return target

    def test_object_reader_round_trip(self):
        data = obj(['a', 'b'], ['c'], data=b'xy\0\0', bss=12, absolute=['abs'])
        s = M.object_summary(data, 'o')
        self.assertEqual((s['exports'], s['imports'], s['absolute']), ({'a', 'b'}, {'c'}, ['abs']))
        self.assertEqual((s['code'], s['data'], s['state']), (4, 4, 12))
        self.assertEqual(M.read_object(data)['relocations'][0]['type'], 18)
        with self.assertRaisesRegex(M.KitError, 'not a Blackfin'):
            M.read_object(b'\x7fELF\x01\x01' + bytes(60))

    def test_package_folders_and_sizes(self):
        self.assertEqual(sorted(p.name for p in self.gen.iterdir()), ['alpha', 'base', 'beta', 'ext-cap', 'ext-flt'])
        self.assertEqual(self.gen.parent.name, 'opxy-test')
        self.assertRegex(self.gen.name, r'^0\.0\.9-[0-9a-f]{12}$')
        sizes = self.report['packages']
        self.assertEqual((sizes['alpha']['code'], sizes['alpha']['data'], sizes['alpha']['state']), (4, 4, 64))
        self.assertEqual(self.report['total_bytes'], sum(p.stat().st_size for p in self.gen.rglob('*') if p.is_file()))
        alpha = json.loads((self.gen / 'alpha' / R.NATIVE).read_bytes())
        for key, value in dict(code=4, data=4, state=64, order=0, engine=None, page=None, asset='alpha.svg', presets=1).items():
            self.assertEqual(alpha[key], value)
        self.assertEqual(json.loads((self.gen / 'beta' / R.NATIVE).read_bytes())['catalog_number'], [21, 28])
        self.assertEqual((self.gen / 'beta/catalog.txt').read_bytes()[21:28], b'"id": 0')
        flt = json.loads((self.gen / 'ext-flt' / R.NATIVE).read_bytes())
        for key, value in dict(machine='extension', services=['extensions', 'master_filter'],
                               accessors=['hooks', 'processor', 'panel'], processor='master_processor',
                               state_id='dj', link=5).items():
            self.assertEqual(flt[key], value)
        self.assertNotIn('artwork', json.loads((self.gen / 'alpha/machine.json').read_text()))   # held by the svg
        self.assertNotIn('wasm', (self.gen / 'alpha/machine.json').read_text())

    def test_device_readers_accept_every_package(self):
        for folder in sorted(self.gen.iterdir()):
            with self.subTest(package=folder.name):
                raw = (folder / R.NATIVE).read_bytes()
                self.assertFalse((folder / R.LEGACY).exists())
                status, _, _, fields = self.outcome(R.text_view(raw, R.NATIVE))
                self.assertEqual(status, 'ok')
                self.assertEqual(fields['generation'], self.gen.name)
                self.assertEqual(fields['profile'], 'opxy-test')
                if folder.name == 'base':
                    self.assertEqual((fields['type'], fields['needs_os']), ('mod-base', 'ab' * 32))
                else:
                    self.assertEqual(fields['type'], 'mod')
                    self.assertNotIn('needs_os', fields)          # the device reader refuses it for type=mod
                listed = (folder / 'files.sha256').read_bytes()
                status, entries, _, _ = self.listing(listed)
                self.assertEqual(status, 'ok')
                paths_ = [p for _, p, _ in entries]
                self.assertEqual(paths_, sorted(paths_, key=str.encode))
                self.assertEqual(listed.decode(), E.files_sha256_text(folder))
                self.assertIn((M.sha256(raw), R.NATIVE), [(sha, p) for sha, p, _ in entries])

    def test_edits_binary_round_trip(self):
        services, edits = M.decode_edits((self.gen / 'base/edits.bin').read_bytes())
        profile = self.kit['profile']
        self.assertEqual(services, list(profile['services']))
        self.assertEqual(edits, [M.edit_fields(e) for e in profile['patches']])
        self.assertEqual(edits[3]['profiling'], False)
        self.assertEqual(edits[2]['service'], 'extensions')

    def expected(self, machines, extensions):
        kit = self.kit
        out = copy.deepcopy(kit)
        out['machines'] = []
        for m in kit['machines']:
            if m['id'] in machines:
                full = copy.deepcopy(m)
                if 'preview' in full:
                    full['preview'].pop('wasm')
                out['machines'].append(full)
            else:
                keep = ('id', 'kind', 'prefix', 'identity', 'absent', 'origin', 'source', 'features')
                out['machines'].append({k: v for k, v in m.items() if k in keep})
        out['objects'] = [o for o in kit['objects'] if o.get('extension') is None and o['owner'] in (None, *machines)
                          or o.get('extension') in extensions]
        out['extensions'] = [e for e in kit['extensions'] if e['id'] in extensions]
        return out

    def test_rebuild_gives_the_kit_back(self):
        for machines, exts in ((['alpha'], []), (['alpha', 'beta'], []), (['beta'], ['cap']),
                               (['alpha'], ['cap', 'flt']), (['beta'], ['flt'])):
            with self.subTest(machines=machines, extensions=exts):
                got = M.rebuild_kit(self.gen, machines, exts, CATALOG_BYTES)
                want = self.expected(machines, exts)
                placeholders = [o for o in got['objects'] if o.get('extension') not in (None, *exts)]
                got['objects'] = [o for o in got['objects'] if o not in placeholders]
                got['extensions'] = [e for e in got['extensions'] if e['id'] in exts]
                self.assertEqual(json.loads(json.dumps(got)), json.loads(json.dumps(want)))
                for o in placeholders:          # an absent extension: its accessors only, never linked
                    eid = o['extension']
                    exports = M.object_summary(base64.b64decode(o['bytes']), o['name'])['exports']
                    want_acc = {'cap': {'hooks'}, 'flt': {'hooks', 'processor', 'panel'}}[eid]
                    self.assertEqual(exports, {'sdk_extension_%s_%s' % (eid, a) for a in want_acc})
                    self.assertEqual(o['name'], 'sdk/extension_%s.o' % eid)

    # -- refusals --------------------------------------------------------------------------------
    def test_refuses_unknown_kit_versions(self):
        for change, pattern in ((dict(format='opxy-browser-kit/2'), 'unknown kit format'),
                                (dict(version='1.0'), 'unknown kit version'),
                                (dict(future=1), 'newer than this tool')):
            with self.subTest(change=change):
                self.refused(dict(synthetic_kit(), **change), pattern)
        kit = synthetic_kit()
        kit['profile']['patches'][0]['kind'] = 'relocate'
        kit['profileSource'] = base64.b64encode(json.dumps(kit['profile']).encode()).decode()
        self.refused(kit, 'unknown edit kind')
        kit = synthetic_kit()
        kit['profile']['schema'] = 2
        self.refused(kit, 'profile differs from its profile source')
        kit['profileSource'] = base64.b64encode(json.dumps(kit['profile']).encode()).decode()
        self.refused(kit, 'unknown profile schema')

    def test_refuses_foreign_imports(self):
        cases = (('alpha/voice.o', obj(['alpha_register'], ['beta_register']), 'machine alpha imports'),
                 ('alpha/voice.o', obj(['alpha_register'], ['stock_insert']), 'machine alpha imports'),
                 ('sdk/extension_cap.o', obj(['sdk_extension_cap_hooks'], ['alpha_engine_id']),
                  'extension cap imports'))
        for name, data, pattern in cases:
            with self.subTest(name=name, pattern=pattern):
                kit = synthetic_kit()
                o = next(o for o in kit['objects'] if o['name'] == name)
                o.update(entry(name, data, owner=o['owner']), **{k: o[k] for k in ('extension',) if k in o})
                self.refused(kit, pattern + r'.*not SDK or runtime exports')

    def test_refuses_edits_or_addresses_outside_the_base(self):
        kit = synthetic_kit()
        o = kit['objects'][0]
        o.update(entry(o['name'], obj(['alpha_register'], ['sdk_start'], absolute=['fixed_address']), owner='alpha'))
        self.refused(kit, 'absolute symbols')
        kit = synthetic_kit()
        kit['machines'][1]['patches'] = []
        self.refused(kit, 'belong only to the base')
        gen = self.copy_generation()
        rewrite(gen / 'alpha', 'edits.bin', b'XYFE')
        with self.assertRaisesRegex(M.KitError, 'belong only to the base'):
            M.rebuild_kit(gen, ['alpha'], [], CATALOG_BYTES)

    def test_refuses_duplicate_ids(self):
        kit = synthetic_kit()
        kit['machines'].append(copy.deepcopy(kit['machines'][0]))
        self.refused(kit, 'duplicate machine ids')
        kit = synthetic_kit()
        kit['extensions'].append(copy.deepcopy(kit['extensions'][0]))
        self.refused(kit, 'duplicate extension ids')
        kit = synthetic_kit()
        kit['objects'].append(copy.deepcopy(kit['objects'][0]))
        self.refused(kit, 'duplicate object')
        with self.assertRaisesRegex(M.KitError, 'duplicate ids'):
            M.rebuild_kit(self.gen, ['alpha', 'alpha'], [], CATALOG_BYTES)

    def test_base_carries_rules_patch_facts_and_labels(self):
        base = self.gen / 'base'
        self.assertEqual((base / 'identities.txt').read_text().splitlines()[1:],
                         ['engines 64', 'engine_none 10', 'pages 48', 'page_none 20', 'effect_page 21',
                          'stock_engines 2,3', 'sample_pages -', 'effects 1,5', 'players 6'])
        info = json.loads((base / R.NATIVE).read_bytes())
        for key, value in dict(stock_records=3, reserved_bytes=2097152, heap_fill_base=0x8d4d0380, heap_fill_bytes=4096).items():
            self.assertEqual(info[key], value)
        rows = (base / 'extensions.txt').read_text().splitlines()
        self.assertIn(' claims=record_double_tap labels=Record%20double-tap ', rows[1])
        self.assertNotIn('labels', json.loads((self.gen / 'ext-cap' / R.NATIVE).read_bytes()))   # extension.json holds them
        for path, old, new in (('identities.txt', 'players 6', 'players 7'), (R.NATIVE, '"stock_records":3', '"stock_records":4')):
            with self.subTest(path=path):
                gen = self.copy_generation()
                rewrite(gen / 'base', path, (base / path).read_text().replace(old, new).encode())
                with self.assertRaisesRegex(M.KitError, 'identities.txt or patch facts differ'):
                    M.rebuild_kit(gen, ['alpha'], [], CATALOG_BYTES)

    def test_expanded_layout_rules(self):
        """expanded_base follows core.mjs offersExpandedMemory; split writes expanded_module_base only when
        offered; rebuild refuses a key that differs from the profile."""
        mib = 1 << 20
        load = lambda value: M.struct.pack('<4H', 0xda01, value >> 16, value & 0xffff, 0).hex()  # noqa: E731
        fill, base, end = 0x81000000, 0x81000000 + 18 * mib, 0x81000000 + 20 * mib
        hooks = [dict(kind='hook', address='0x%08x' % (base - 0x1000), length=4)]
        heaps = [dict(kind='heap', before=load(20 * mib), after=load(18 * mib)) for _ in range(2)]
        profile = dict(module_base=base, reserved_bytes=2 * mib, heap_fill_base=fill, heap_fill_bytes=20 * mib,
                       patches=heaps + hooks,
                       experimental_memory=dict(module_base=base - 2 * mib, reserved_bytes=4 * mib, heap_after=load(16 * mib)))
        self.assertEqual(M.expanded_base(profile), base - 2 * mib)
        self.assertEqual(M.expanded_heap_after(heaps[0]['after'], 16 * mib), load(16 * mib))
        broken = (dict(experimental_memory=None), dict(patches=heaps[:1] + hooks),
                  dict(experimental_memory=dict(profile['experimental_memory'], heap_after=load(15 * mib))),
                  dict(experimental_memory=dict(profile['experimental_memory'], reserved_bytes=3 * mib)),
                  dict(experimental_memory=dict(profile['experimental_memory'], module_base=base - 2 * mib + 4)),
                  dict(experimental_memory=dict(profile['experimental_memory'], extra=1)),
                  dict(patches=heaps + [dict(hooks[0], address='0x%08x' % (base + (1 << 24)))]),
                  dict(heap_fill_bytes=24 * mib))
        for change in broken:
            with self.subTest(change=sorted(change)):
                self.assertIsNone(M.expanded_base(dict(profile, **change)))
        self.assertNotIn('expanded_module_base', (self.gen / 'base' / R.NATIVE).read_text())   # not offered
        gen = self.copy_generation()
        info = json.loads((gen / 'base' / R.NATIVE).read_bytes())
        info['expanded_module_base'] = 0x8d0d0380
        rewrite(gen / 'base', R.NATIVE, (json.dumps(info) + '\n').encode())
        with self.assertRaisesRegex(M.KitError, 'expanded_module_base differs'):
            M.rebuild_kit(gen, ['alpha'], [], CATALOG_BYTES)

    def test_refuses_mixed_generations(self):
        gen = self.copy_generation()
        info = (gen / 'beta' / R.NATIVE).read_text().replace(self.gen.name, '0.0.8-000000000000')
        rewrite(gen / 'beta', R.NATIVE, info.encode())
        M.rebuild_kit(gen, ['alpha'], [], CATALOG_BYTES)            # beta not chosen: fine
        with self.assertRaisesRegex(M.KitError, 'cannot mix generations'):
            M.rebuild_kit(gen, ['alpha', 'beta'], [], CATALOG_BYTES)

    def test_refuses_damaged_or_incomplete_selections(self):
        gen = self.copy_generation()
        (gen / 'alpha/objects/alpha/voice.o').write_bytes(b'tampered')
        cases = ((gen, ['alpha'], [], CATALOG_BYTES, 'differs from files.sha256'),
                 (self.gen, [], ['cap'], CATALOG_BYTES, 'at least one machine'),
                 (self.gen, ['gamma'], [], CATALOG_BYTES, 'not in this generation'),
                 (self.gen, ['alpha'], [], b'{}', 'op1_factory.json differs'))
        for folder, machines, exts, catalog, pattern in cases:
            with self.subTest(pattern=pattern), self.assertRaisesRegex(M.KitError, pattern):
                M.rebuild_kit(folder, machines, exts, catalog)

    def test_existing_output_is_not_replaced(self):
        path = self.work / 'packages.json'
        with self.assertRaisesRegex(M.KitError, 'already exists'):
            M.split_kit(path, self.work / 'packages', finish=finish)


@unittest.skipUnless(shutil.which('node') and REAL_KIT.is_file() and STOCK.is_file() and CATALOG.is_file() and
                     (OPEN_XY / '.git').exists() and subprocess.run(
                         ['git', '-C', str(OPEN_XY), 'cat-file', '-e', SDK_COMMIT + '^{commit}'],
                         capture_output=True).returncode == 0,
                     'published kit, stock os.ldr + op1_factory.json, Node or the open-xy upstream clone at %s absent' % SDK_COMMIT[:7])
class PublishedKit(unittest.TestCase):
    """The real kit: split, then every selection linked by core.mjs from the kit and from the packages."""

    @classmethod
    def setUpClass(cls):
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='modkit-test-', dir=paths.SESSIONS)
        cls.addClassCleanup(cls.tmp.cleanup)
        work = Path(cls.tmp.name)
        cls.report = M.split_kit(REAL_KIT, work / 'out', OPEN_XY, sdk_commit=SDK_COMMIT)
        cls.rows = {r['selection']: r for r in M.prove(REAL_KIT, cls.report['root'], OPEN_XY, CATALOG, STOCK,
                                                       scratch=work, sdk_commit=SDK_COMMIT)}

    def test_every_selection_is_byte_identical(self):
        for name, row in self.rows.items():
            with self.subTest(selection=name):
                self.assertTrue(row['identical'], row)
                if name == 'all':                   # 79 machines exceed 2032 KiB: the same refusal both ways
                    self.assertIn('need more than the 2032 KiB', row['error'])
                else:
                    self.assertIsNone(row['error'])
                    self.assertTrue(row['ldr'] and row['content_matches_packages'], row)

    def test_extension_memory_matches_record_014(self):
        allocated = {name: self.rows[name]['allocated'] for name in
                     ('one', 'one+capture', 'one+master_dj_filter', 'one+all-extensions')}
        self.assertEqual(allocated, {'one': 148120, 'one+capture': 161440, 'one+master_dj_filter': 194484,
                                     'one+all-extensions': 202584})

    def test_package_set(self):
        packages = self.report['packages']
        self.assertEqual(len(packages), 1 + 79 + 2)
        self.assertEqual((packages['fm2op']['code'], packages['fm2op']['data'], packages['fm2op']['state']),
                         (10692, 7112, 7532))


if __name__ == '__main__':
    unittest.main()
