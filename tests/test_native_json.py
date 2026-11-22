"""Shared native/Python/browser schema cases, source preservation and freestanding budgets."""
import ctypes
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from xy_forever import hostbuild, native_json as J, toolchain

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/native-json.json'


class NativeJson(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-json-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        cls.sources = [str(ROOT / p) for p in ('native/json.cpp', 'tests/native_json_bridge.cpp')]
        cls.flags = ['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror', '-O1']
        cls.binary = cls.work / 'json.dylib'
        hostbuild.run([*cls.flags, '-shared', '-fPIC', *cls.sources, '-o', str(cls.binary)],
                      check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(cls.binary))
        cls.lib.json_view.restype = ctypes.c_uint
        cls.lib.json_view.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]

    def native(self, source, config=False, capacity=4096):
        raw, out = ctypes.create_string_buffer(source), ctypes.create_string_buffer(capacity)
        result = self.lib.json_view(raw, len(source), int(config), out, capacity)
        self.assertEqual(raw.raw[:len(source)], source)
        return None if result & 0x80000000 else out.raw[:result]

    def test_shared_schema_fixtures(self):
        for case in json.loads(FIXTURES.read_text()):
            with self.subTest(case=case['name']):
                source, config = case['source'].encode(), case['kind'] == 'config'
                expected = case['text'].encode() if case['text'] is not None else None
                try:
                    python = (J.config_text if config else J.package_text)(source)
                except ValueError:
                    python = None
                self.assertEqual(python, expected)
                self.assertEqual(self.native(source, config), expected)
                if case.get('canonical'):
                    fields = dict(line.split('=', 1) for line in case['text'].splitlines())
                    self.assertEqual(J.config_document(fields), source + b'\n')

    def test_document_writers_use_native_types_and_round_trip(self):
        package = J.package_document(dict(type='mod', name='Example "quoted" \\u literal', machine='synth',
                                          generation='v1', order='2', services='synth,delay', engine='-',
                                          module_base='0x8d2d0380', catalog_number='11,12'))
        root = json.loads(package)
        self.assertEqual(root['services'], ['synth', 'delay'])
        self.assertEqual(root['module_base'], 0x8d2d0380)
        self.assertIsNone(root['engine'])
        self.assertEqual(self.native(package), J.package_text(package))
        config = J.config_document(dict(base='stock', profile='p', generation='v1', memory='expanded',
                                        unverified='1', mods='a,ext-b', slot='mods-a', stamp='a' * 64,
                                        built='stock v1 00001234 2 expanded'))
        root = json.loads(config)
        self.assertTrue(root['desired']['unverified'])
        self.assertEqual(root['desired']['mods'], ['a', 'ext-b'])
        self.assertEqual(root['built']['set'], 0x1234)
        self.assertEqual(self.native(config, True), J.config_text(config))

    def test_existing_modkit_extra_fields_keep_their_semantics(self):
        # The old strict manifest reader only knows common admission fields. The
        # real kit's extra fields must survive for patch/link/content/rebuild.
        from test_modpatch import stock, profile_for, kit_for, finish
        from xy_forever import modkit as M
        kit = self.work / 'kit.json'
        kit.write_text(json.dumps(kit_for(profile_for(stock()))))
        generation = Path(M.split_kit(kit, self.work / 'packages', finish=finish,
                                     sdk_source='https://example.com/sdk@abc')['root'])
        for file in sorted(generation.glob('*/info.txt')):
            with self.subTest(package=file.parent.name):
                fields = dict(line.split('=', 1) for line in file.read_text().splitlines() if '=' in line)
                data = J.package_document(fields)
                view = J.package_text(data)
                self.assertEqual(self.native(data), view)
                recovered = dict(line.split('=', 1) for line in view.decode().splitlines())
                # Decimal/hex renderings and every field are retained, except a
                # display CPU percentage's insignificant numeric spelling.
                for key, value in fields.items():
                    self.assertEqual(recovered[key], value)

    def test_explicit_capacity_and_numeric_limits(self):
        # Preserve the old 4096-byte semantic capacity even when JSON quoting
        # doubles its values. This measured 8202-byte document requires >8 KiB.
        fields = dict(type='firmware', **{'x%02d' % i: '"' * 127 for i in range(31)})
        fields['x30'] = fields['x30'][:-10]
        escaped = J.package_document(fields)
        self.assertEqual(len(escaped), 8202)
        self.assertEqual(len(J.package_text(escaped)), 4096)
        self.assertEqual(self.native(escaped), J.package_text(escaped))
        data = J.package_document(dict(type='firmware', bytes=0xffffffff, machines=65535))
        self.assertEqual(self.native(data), J.package_text(data))
        self.assertIsNone(self.native(data, capacity=1))
        for value in (-1, 0x100000000, True, 1.5, '1'):
            source = json.dumps(dict(schema=J.PACKAGE_SCHEMA, schema_version=1, type='firmware', bytes=value)).encode()
            self.assertIsNone(self.native(source))
            with self.assertRaises(J.RecordError):
                J.package_text(source)
        for data in (b'x' * (J.DOCUMENT_BYTES + 1), b'{}\0{}'):
            self.assertIsNone(self.native(data))
            with self.assertRaises(J.RecordError):
                J.package_text(data)

    def test_bounded_selection_properties_and_nesting(self):
        root = dict(schema=J.CONFIG_SCHEMA, schema_version=1,
                    desired=dict(base='stock', profile='p', generation='v1', memory='standard', unverified=False,
                                 mods=['m%02d' % i for i in range(96)]), built=None)
        source = json.dumps(root).encode()
        self.assertEqual(self.native(source, True), J.config_text(source))
        root['desired']['mods'].append('overflow')
        source = json.dumps(root).encode()
        self.assertIsNone(self.native(source, True))
        with self.assertRaises(J.RecordError):
            J.config_text(source)
        for nested in ({'a': {'b': {'c': {}}}}, {'a': list(range(97))}):
            source = json.dumps(dict(schema=J.PACKAGE_SCHEMA, schema_version=1, type='firmware', nested=nested)).encode()
            self.assertIsNone(self.native(source))
            with self.assertRaises(J.RecordError):
                J.package_text(source)

    def test_truncated_and_mutated_documents_under_sanitizers(self):
        binary = self.work / 'sanitized'
        hostbuild.run([*self.flags, '-DXYF_JSON_SANITIZE', '-fsanitize=address,undefined',
                       '-fno-omit-frame-pointer', *self.sources, '-o', str(binary)], check=True, capture_output=True)
        paths = []
        for i, case in enumerate(json.loads(FIXTURES.read_text())):
            path = self.work / ('case%d.json' % i)
            path.write_text(case['source']); paths.append(str(path))
        subprocess.run([str(binary), *paths], check=True, capture_output=True)

    def test_blackfin_code_stack_and_workspace(self):
        if not (toolchain.location() / 'bin/bfin-elf-g++').exists():
            self.skipTest('Blackfin toolchain absent')
        compiler, tools = toolchain.authenticate()
        asm, obj = self.work / 'json.s', self.work / 'json.o'
        subprocess.run([str(compiler['cxx']), *toolchain.CXX_FLAGS, '-fstack-usage', '-S',
                        str(ROOT / 'native/json.cpp'), '-o', str(asm)], check=True, capture_output=True)
        subprocess.run([str(tools['as']), '-mcpu=bf707', '-o', str(obj), str(asm)], check=True, capture_output=True)
        symbols = subprocess.run([str(tools['objdump']), '-t', str(obj)], check=True, capture_output=True, text=True).stdout
        self.assertNotIn('*UND*', symbols)
        sections = subprocess.run([str(tools['objdump']), '-h', str(obj)], check=True, capture_output=True, text=True).stdout
        self.assertNotRegex(sections, r'\.(?:data|bss)\s+0*[1-9a-f]')
        frames = [int(line.split('\t')[1]) for line in asm.with_suffix('.su').read_text().splitlines()]
        self.assertLessEqual(max(frames), 256)
        # The Blackfin ABI uses 20 bytes/property and four-byte structure alignment.
        self.assertEqual(8452 + 20 * J.PROPERTY_COUNT, 11012)


if __name__ == '__main__':
    unittest.main()
