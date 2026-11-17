"""Native firmware counts share strict/lenient readers; no external layout parser is involved."""
import unittest

import test_xyf_manifest as M


class NativeFirmwareMetadata(unittest.TestCase):
    setUpClass = classmethod(M.ManifestTest.setUpClass.__func__)
    tearDownClass = classmethod(M.ManifestTest.tearDownClass.__func__)
    parse = M.ManifestTest.parse
    outcome = M.ManifestTest.outcome
    legacy_same = M.ManifestTest.legacy_same

    def test_optional_native_machine_count(self):
        for count in (0, 1, 64, 256, 65535):
            text = ('name=Native firmware\nmachines=%d\n' % count).encode()
            self.assertEqual(self.outcome(text)[3]['machines'], count)
            legacy = self.legacy_same(text)
            self.assertTrue(legacy.has_machines)
            self.assertEqual(legacy.machines, count)
        self.assertFalse(self.legacy_same(b'name=Old entry\n').has_machines)
        for value in ('-1', '01', '1.0', '65536', '4294967296', 'no'):
            self.assertEqual(self.outcome(('machines=%s\n' % value).encode())[0], 'bad_number')

    def test_existing_base_machine_count_and_line_budget(self):
        text = b'type=mod-base\nprofile=opxy-1.2.1\ngeneration=kit-1\nneeds_os=' + b'a' * 64 + b'\nmachines=79\n'
        self.assertEqual(self.outcome(text)[3]['machines'], 79)
        # Recognizing an existing base key changes no line limit and keeps its old key ordering.
        exact = text + b''.join(('extra_%d=x\n' % i).encode() for i in range(27))
        self.assertEqual(self.outcome(exact)[0], 'ok')
        self.assertEqual(self.outcome(exact + b'over=x\n')[0], 'too_many_keys')
        mod = b'type=mod\nname=M\nmachine=synth\ngeneration=kit-1\nmachines=1\n'
        self.assertEqual(self.outcome(mod)[0], 'key_not_for_type')
