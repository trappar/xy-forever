"""XY Forever storage formats (docs/STORAGE.md): host-built device C++."""
import ctypes as C
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402
from xy_forever.modpatch import entry_layout_text  # noqa: E402
from xy_forever.xyf_entry import check_layout  # noqa: E402


class State(C.Structure):
    _fields_ = [('default_id', C.c_char * 32), ('once', C.c_char * 32), ('stage2', C.c_char * 64),
                ('stage2_previous', C.c_char * 64), ('stage2_tries', C.c_uint32)]


class Info(C.Structure):
    _fields_ = [('name', C.c_char * 48), ('version', C.c_char * 32), ('kind', C.c_uint32),
                ('bytes', C.c_uint32), ('crc32', C.c_uint32), ('has_bytes', C.c_bool), ('has_crc32', C.c_bool)]


class Machine(C.Structure):
    _fields_ = [('id', C.c_char * 32), ('kind', C.c_uint8), ('number', C.c_int16), ('page', C.c_int16)]


class Layout(C.Structure):
    _fields_ = [('profile', C.c_char * 32), ('count', C.c_uint32), ('machines', Machine * 256),
                ('selected_count', C.c_uint32), ('extension_count', C.c_uint32),
                ('selected', (C.c_char * 32) * 256), ('extensions', (C.c_char * 32) * 64), ('has_selected', C.c_bool)]


class StoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-store-')
        lib = Path(cls.tmp.name) / 'store.dylib'
        hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-O1', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                       '-ffreestanding', '-fno-builtin', '-fno-exceptions', '-fno-rtti',
                       str(ROOT / 'target/xyf_store.cpp'), str(ROOT / 'target/xyf_layout.cpp'), str(ROOT / 'target/xyf_info.cpp'), str(ROOT / 'tests/xyf_store_bridge.cpp'),
                       '-o', str(lib)], check=True, capture_output=True)
        cls.lib = C.CDLL(str(lib))
        cls.lib.store_sizes.restype = C.c_uint

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_struct_layouts_match(self):
        self.assertEqual([self.lib.store_sizes(i) for i in range(3)],
                         [C.sizeof(State), C.sizeof(Info), C.sizeof(Layout)])

    def test_ids(self):
        for good in (b'opxy-121', b'a', b'x' * 31, b'open_xy-2'):
            self.assertTrue(self.lib.store_valid_id(good), good)
        for bad in (b'', b'x' * 32, b'Upper', b'a/b', b'..', b'a b', b'\xc3\xa9'):
            self.assertFalse(self.lib.store_valid_id(bad), bad)

    def state(self, text):
        s = State()
        self.assertEqual(self.lib.store_state(text, len(text), C.byref(s)), 1)
        return s

    def test_state_parse_and_canonical_write(self):
        s = self.state(b'# XY Forever\r\ndefault = opxy-121 \r\nonce=open-xy-7\nunknown=1\n'
                       b'stage2=xyf-0.2.ldr\nstage2_tries=2\ndefault=opxy-125\n')
        self.assertEqual((s.default_id, s.once, s.stage2, s.stage2_tries),
                         (b'opxy-125', b'open-xy-7', b'xyf-0.2.ldr', 2))
        out = C.create_string_buffer(256)
        n = self.lib.store_write_state(C.byref(s), out, 256)
        self.assertEqual(out.raw[:n], b'default=opxy-125\nonce=open-xy-7\nstage2=xyf-0.2.ldr\nstage2_tries=2\n')
        again = self.state(out.raw[:n])
        self.assertEqual(bytes(again), bytes(s))
        self.assertEqual(self.lib.store_write_state(C.byref(s), out, 10), 0)   # too small -> 0

    def test_state_rejects_bad_ids_and_oversize(self):
        s = self.state(b'default=../os\nonce=OK\nstage2_tries=99999999999\n')
        self.assertEqual((s.default_id, s.once, s.stage2_tries), (b'', b'', 0))
        big = b'#' * 4097
        self.assertEqual(self.lib.store_state(big, len(big), C.byref(State())), 0)
        self.state(b'=x\nnoequals\n' + b'k' * 32 + b'=1\nv=' + b'x' * 128 + b'\n')   # all skipped, no crash

    def test_info(self):
        f = Info()
        text = b'name=OP-XY 1.2.5\nversion=1.2.5+0\nkind=official\nbytes=2303624\ncrc32=9f1c0a42\n'
        self.assertEqual(self.lib.store_info(text, len(text), C.byref(f)), 1)
        self.assertEqual((f.name, f.version, f.kind, f.bytes, f.crc32, f.has_bytes, f.has_crc32),
                         (b'OP-XY 1.2.5', b'1.2.5+0', 1, 2303624, 0x9f1c0a42, True, True))
        text = b'kind=open-xy\ncrc32=9F1C0A42\nbytes=-1\n'
        self.assertEqual(self.lib.store_info(text, len(text), C.byref(f)), 1)
        self.assertEqual((f.kind, f.has_crc32, f.has_bytes), (2, False, False))

    def test_host_tool_output_round_trips(self):
        import sys, zlib
        sys.path.insert(0, str(ROOT))
        from xy_forever.xyf_entry import info_text
        data = bytes(range(256)) * 9
        text = info_text('OP-XY 1.2.5', '1.2.5+0', 'open-xy', data).encode()
        f = Info()
        self.assertEqual(self.lib.store_info(text, len(text), C.byref(f)), 1)
        self.assertEqual((f.name, f.version, f.kind, f.bytes, f.crc32, f.has_bytes, f.has_crc32),
                         (b'OP-XY 1.2.5', b'1.2.5+0', 2, len(data), zlib.crc32(data), True, True))

    def layout(self, data):
        out = Layout()
        ok = self.lib.store_layout(data, len(data), C.byref(out))
        return out if ok else None

    def test_real_open_xy_layout(self):
        """A layout.json as open-xy's website writes it (modpatch.entry_layout_text: indented, selected, extensions)."""
        numbers = {'karplus': {'engine': 36, 'page': 84}, 'fm2op': {'engine': 41, 'page': 90},
                   'capture': {'effect': 12, 'page': 101}}
        data = entry_layout_text('opxy-1.2.1', ['karplus', 'fm2op'], ['capture'], numbers).encode()
        out = self.layout(data)
        expected = json.loads(data)
        self.assertEqual(out.profile.decode(), expected['profile'])
        got = {out.machines[i].id.decode(): out.machines[i] for i in range(out.count)}
        self.assertEqual(set(got), set(expected['machines']))
        for name, m in expected['machines'].items():
            kind = 'engine' if 'engine' in m else 'effect'
            self.assertEqual((got[name].kind, got[name].number, got[name].page),
                             (1 if kind == 'engine' else 2, m[kind], m['page']))

    def document(self, **fields):
        return dict(format='opxy-layout/1', profile='opxy-1.2.1', machines={}, **fields)

    def accepted_layout(self, data):
        text = json.dumps(data, separators=(',', ':'))
        out = self.layout(text.encode())
        self.assertIsNotNone(out, data)
        self.assertEqual(check_layout(text), out.selected_count if out.has_selected else None)
        return out

    def refused_layout(self, data):
        text = json.dumps(data, separators=(',', ':'))
        self.assertIsNone(self.layout(text.encode()), data)
        with self.assertRaises(ValueError):
            check_layout(text)

    def test_builder_players_selection_and_extensions(self):
        # Browser app.mjs shape: selected is firmware order, machines includes past builds.
        # Synthetic numbers; matches upstream a07b08f's fields, not a private kit fixture.
        doc = self.document(selected=['scales', 'chords'], extensions=['master_dj_filter'])
        doc['machines'] = {'old': {'engine': 36, 'page': 84}, 'chords': {'player': 1, 'page': 120},
                           'scales': {'player': 2, 'page': 121}, 'master_dj_filter': {'effect': 3, 'page': 21}}
        doc.update(memory='expanded', reserved_bytes=4194304)
        out = self.accepted_layout(doc)
        self.assertTrue(out.has_selected)
        self.assertEqual([bytes(out.selected[i]).split(b'\0')[0] for i in range(out.selected_count)],
                         [b'scales', b'chords'])
        self.assertEqual((out.extension_count, bytes(out.extensions[0]).split(b'\0')[0]), (1, b'master_dj_filter'))
        self.assertEqual((out.count, out.machines[1].kind, out.machines[1].number), (4, 3, 1))
        # Both root key orders work; selected need not follow its number map.
        self.accepted_layout(dict(reversed(list(doc.items()))))
        doc['selected'] = []
        self.assertEqual(self.accepted_layout(doc).selected_count, 0)
        for selected in (['chords', 'chords'], ['missing'], 'chords', [1]):
            self.refused_layout(dict(doc, selected=selected))
        for extensions in (['master_dj_filter'] * 2, None, ['Bad']):
            self.refused_layout(dict(doc, extensions=extensions))

    def test_sdk_layout_is_number_history_not_selection(self):
        # tooling.machines.layout_document has no selected/extensions keys.
        doc = self.document()
        doc['machines'] = {f'm{i}': {'engine': i, 'page': 82 + i} for i in range(79)}
        for profile in ('opxy-1.2.1', 'opxy-1.2.0'):
            doc['profile'] = profile
            out = self.accepted_layout(doc)
            self.assertEqual((out.count, out.has_selected, out.selected_count), (79, False, 0))
        for profile in ('opxy-1.2.5', '', 'other', None):
            self.refused_layout(dict(doc, profile=profile))
        del doc['profile']
        self.refused_layout(doc)

    def test_machine_selection_file_and_extension_bounds(self):
        doc = self.document(selected=[f'm{i}' for i in range(256)], extensions=[f'e{i}' for i in range(64)])
        doc['machines'] = {f'm{i}': {'engine': i, 'page': i} for i in range(256)}
        out = self.accepted_layout(doc)
        self.assertEqual((out.count, out.selected_count, out.extension_count), (256, 256, 64))
        self.assertEqual(bytes(out.selected[255]).split(b'\0')[0], b'm255')
        data = json.dumps(doc, separators=(',', ':')).encode()
        self.assertLess(len(data), 16384)
        exact = data + b' ' * (16384 - len(data))
        self.assertIsNotNone(self.layout(exact))
        self.assertEqual(check_layout(exact.decode()), 256)
        self.assertIsNone(self.layout(exact + b' '))
        with self.assertRaises(ValueError):
            check_layout((exact + b' ').decode())
        self.refused_layout(dict(doc, machines=dict(doc['machines'], extra={'engine': 0, 'page': 0})))
        self.refused_layout(dict(doc, selected=doc['selected'] + ['extra']))
        self.refused_layout(dict(doc, extensions=doc['extensions'] + ['extra']))
        for length in (16, 31, 32):
            name = 'x' * length
            case = self.document(selected=[name])
            case['machines'] = {name: {'player': 255, 'page': 1023}}
            if length == 32:
                self.refused_layout(case)
            else:
                self.accepted_layout(case)

    def test_unknown_fields_and_number_bounds(self):
        doc = self.document(selected=['m'])
        doc['machines'] = {'m': {'effect': 255, 'page': 1023, 'another_long_machine_property': 1e20}}
        doc.update(memory='expanded', reserved_bytes=4194304, unknown_int=2**100,
                   another_long_unknown_property={'nested': [None, True, 1e100, 'line\n\u2603']})
        self.accepted_layout(doc)
        for key, values in (('effect', (-1, 256, 2147483647, 2147483648, -2147483648, -2147483649, True, 1.0)),
                            ('page', (-1, 1024, 2**32))):
            for value in values:
                self.refused_layout(dict(doc, machines={'m': dict(doc['machines']['m'], **{key: value})}))
        for numbers in ({'engine': 1, 'player': 2, 'page': 3}, {'effect': 1, 'player': 2, 'page': 3}):
            self.refused_layout(dict(doc, machines={'m': numbers}))
        good = json.dumps(doc).encode()
        for token in (b'01', b'1.', b'1e', b'+1', b'NaN', b'"bad\\q"'):
            bad = good[:-1] + b', "unknown": ' + token + b'}'
            self.assertIsNone(self.layout(bad), token)
        # Duplicate known keys are refused; changing the format never silently downgrades it.
        for tail in (b'"profile":"opxy-1.2.0"', b'"format":"opxy-layout/1"', b'"machines":{}',
                     b'"selected":[]', b'"extensions":[],"extensions":[]'):
            self.assertIsNone(self.layout(good[:-1] + b',' + tail + b'}'))

    def test_layout_rejections(self):
        good = {'format': 'opxy-layout/1', 'profile': 'opxy-1.2.1',
                'machines': {'karplus': {'engine': 36, 'page': 84}}, 'extra': [1, {'a': [None, True]}]}
        self.assertIsNotNone(self.layout(json.dumps(good).encode()))
        bad = [
            dict(good, format='opxy-layout/2'),
            dict(good, machines={'Bad': {'engine': 1, 'page': 1}}),
            dict(good, machines={'x': {'engine': 1}}),
            dict(good, machines={'x': {'engine': 1, 'effect': 2, 'page': 1}}),
            dict(good, machines={'x': {'engine': 1.5, 'page': 1}}),
            dict(good, machines={f'm{i}': {'engine': i, 'page': 1} for i in range(257)}),
        ]
        for case in bad:
            self.assertIsNone(self.layout(json.dumps(case).encode()), case)
        deep = b'{"format":"opxy-layout/1","x":' + b'[' * 12 + b']' * 12 + b'}'
        self.assertIsNone(self.layout(deep))
        self.assertIsNone(self.layout(b'{"format":"opxy-layout/1"} trailing'))
        self.assertIsNone(self.layout(b'{"format":"opxy-layout/1","machines":{"a":{"engine":1,"page":1}'))
        self.assertIsNone(self.layout(b' ' * 16385))


if __name__ == '__main__':
    unittest.main()
