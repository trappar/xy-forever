"""Direct typed extraction agrees with the former JSON-to-text semantic pipeline."""
import ctypes as C
import json
from pathlib import Path
import random
import tempfile
import unittest
from xy_forever import hostbuild
from test_xyf_manifest import Manifest

ROOT = Path(__file__).resolve().parents[1]


class NativePackageRecord(unittest.TestCase):
    def test_direct_semantics_and_refusal_match_text_pipeline(self):
        bridge = r'''
#include "xyf_package_record.hpp"
extern "C" int decode(const char* raw, unsigned n, xyf::manifest::Manifest* out, bool direct) {
    xyf::package_record::Workspace work;
    int status;
    if (direct) status = xyf::package_record::parse(raw, n, work);
    else {
        xyf::manifest::parse("", 0, work.manifest);
        const auto doc = xyf_core::json::parse(raw, n, work.json, xyf_core::json::package_schema);
        unsigned length = 0; char text[xyf::manifest::max_text];
        status = xyf_core::json::package_text(doc, text, sizeof(text), length);
        if (!status) xyf::manifest::parse(text, length, work.manifest);
    }
    *out = work.manifest;
    return status;
}
'''
        with tempfile.TemporaryDirectory(prefix='xyf-record-') as tmp:
            source = Path(tmp) / 'bridge.cpp'; source.write_text(bridge)
            lib = Path(tmp) / 'record.dylib'
            hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-O1', '-shared', '-fPIC',
                           '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'target'),
                           str(ROOT / 'native/json.cpp'), str(ROOT / 'target/xyf_manifest.cpp'),
                           str(ROOT / 'target/xyf_package_record.cpp'), str(source), '-o', str(lib)],
                          check=True, capture_output=True)
            decode = C.CDLL(str(lib)).decode
            decode.argtypes = [C.c_char_p, C.c_uint, C.POINTER(Manifest), C.c_bool]
            decode.restype = C.c_int
            sources = [case['source'].encode() for case in json.loads((ROOT / 'tests/native-json.json').read_text())
                       if case['kind'] == 'package']
            base = dict(schema='xy-forever.package', schema_version=1, type='firmware', name='Firmware',
                        bytes=23, crc32='12345678', sha256='a' * 64)
            cases = [base, dict(base, generation='kit', needs_os='b' * 64),
                     dict(schema='xy-forever.package', schema_version=1, type='mod', name='Synth',
                          machine='synth', generation='kit'),
                     dict(schema='xy-forever.package', schema_version=1, type='mod-base',
                          profile='kit', generation='kit', needs_os='b' * 64)]
            choices = dict(type=['firmware', 'mod', 'mod-base', 'companion', '', ' firmware '],
                           name=['', ' padded ', 'x' * 47, 'x' * 48], bytes=[0, 2**32-1, 2**32, -1, 1.5, '1', True],
                           crc32=['01234567', 'ABCDEF00', '0' * 7], sha256=['b' * 64, 'b' * 63, 'B' * 64],
                           generation=['kit', '.kit', ' kit ', 'kit/name'], needs_os=['a' * 64, ''],
                           machines=[65535, 65536, '3'], services=[[], ['synth'], ['bad,item'], [0]],
                           catalog_number=[None, [0, 2**32-1], [1], [1, 2, 3], ['1', 2]],
                           engine=[None, 3, -1], module_base=[0, 2**32-1, '0x1'],
                           unknown=['', 'x' * 127, 'x' * 128, 1, None], machine=['synth', 'sample', 'nope'],
                           profile=['kit', '_kit'], licence=['x' * 63, 'x' * 64])
            for key, values in choices.items():
                for value in values:
                    cases.append(dict(base, **{key: value}))
            rng = random.Random(95)
            for _ in range(300):
                case = dict(base)
                for key in rng.sample(list(choices), rng.randrange(1, 7)):
                    case[key] = rng.choice(choices[key])
                cases.append(case)
            cases.append(dict(base, **{'x%02d' % i: 'x' * 127 for i in range(26)}))
            sources += [json.dumps(case, separators=(',', ':')).encode() for case in cases]
            for raw in sources:
                with self.subTest(raw=raw):
                    direct, reference = Manifest(), Manifest()
                    a = decode(raw, len(raw), C.byref(direct), True)
                    b = decode(raw, len(raw), C.byref(reference), False)
                    self.assertEqual(a, b)
                    self.assertEqual(bytes(direct), bytes(reference))
