"""Direct typed extraction agrees with the former JSON-to-text semantic pipeline."""

import json
import random
import unittest
from pathlib import Path

import xyf_hosttest

ROOT = Path(__file__).resolve().parents[1]


class NativePackageRecord(unittest.TestCase):
    def test_direct_semantics_and_refusal_match_text_pipeline(self):
        decode = xyf_hosttest.module('package_record').decode
        sources = [
            case['source'].encode()
            for case in json.loads((ROOT / 'tests/native-json.json').read_text())
            if case['kind'] == 'package'
        ]
        base = dict(
            schema='xy-forever.package',
            schema_version=1,
            type='firmware',
            name='Firmware',
            bytes=23,
            crc32='12345678',
            sha256='a' * 64,
        )
        cases = [
            base,
            dict(base, generation='kit', needs_os='b' * 64),
            dict(
                schema='xy-forever.package',
                schema_version=1,
                type='mod',
                name='Synth',
                machine='synth',
                generation='kit',
            ),
            dict(
                schema='xy-forever.package',
                schema_version=1,
                type='mod-base',
                profile='kit',
                generation='kit',
                needs_os='b' * 64,
            ),
        ]
        choices = dict(
            type=['firmware', 'mod', 'mod-base', 'companion', '', ' firmware '],
            name=['', ' padded ', 'x' * 47, 'x' * 48],
            bytes=[0, 2**32 - 1, 2**32, -1, 1.5, '1', True],
            crc32=['01234567', 'ABCDEF00', '0' * 7],
            sha256=['b' * 64, 'b' * 63, 'B' * 64],
            generation=['kit', '.kit', ' kit ', 'kit/name'],
            needs_os=['a' * 64, ''],
            machines=[65535, 65536, '3'],
            services=[[], ['synth'], ['bad,item'], [0]],
            catalog_number=[None, [0, 2**32 - 1], [1], [1, 2, 3], ['1', 2]],
            engine=[None, 3, -1],
            module_base=[0, 2**32 - 1, '0x1'],
            unknown=['', 'x' * 127, 'x' * 128, 1, None],
            machine=['synth', 'sample', 'nope'],
            profile=['kit', '_kit'],
            licence=['x' * 63, 'x' * 64],
        )
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
                a, direct = decode(raw, True)
                b, reference = decode(raw, False)
                self.assertEqual(a, b)
                self.assertEqual(direct, reference)
