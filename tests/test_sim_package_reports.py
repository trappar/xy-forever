"""Required report APIs against the real simulator service; exact identities and bounded pages."""
import hashlib
import struct
import time
import unittest
from xy_forever import sim, usb_proto as P, package_store as S
import test_sim_package_store as PS
package = PS.package


@unittest.skipIf(sim.available(), sim.available())
class Reports(unittest.TestCase):
    setUpClass = classmethod(PS.Packages.setUpClass.__func__)
    setUp = PS.Packages.setUp
    close = PS.Packages.close

    def request(self, command, *words):
        until = time.monotonic() + 15
        while True:
            result = self.dev.call(command, struct.pack('<' + 'I' * len(words), *words))
            if result[0] != P.ids.S_BUSY:
                return result
            self.assertLess(time.monotonic(), until)
            time.sleep(0.02)

    def packages(self, area):
        outer = inner = 0
        result = []
        for _ in range(100):
            code, body = self.request(P.ids.PACKAGE_LIST, 1, area, outer, inner)
            self.assertEqual(code, 0)
            self.assertLessEqual(len(body) + 4, 1024)
            version, outer, inner, more = struct.unpack('<4I', body[:16])
            self.assertEqual(version, 1)
            if body[16:]:
                result.append(dict(line.split('=', 1) for line in body[16:].decode().splitlines()))
            if not more:
                return result
        self.fail('nonconvergent package cursors')

    def test_lists_all_areas_and_exact_identity(self):
        files = package('report', {'asset.bin': b'shared content'})
        folder = self.root / 'xy-forever/firmware/report'
        folder.mkdir()
        for rel, data in files.items():
            (folder / rel).write_bytes(data)
        inbox = self.root / 'xy-forever/inbox'
        inbox.mkdir()
        (inbox / 'no-extension').write_bytes(files['os.ldr'])
        for generation, name in [('gen-a', 'alpha'), ('gen-a', 'beta'), ('gen-b', 'gamma')]:
            target = self.root / 'xy-forever/mods' / generation / name
            target.mkdir(parents=True, exist_ok=True)
            info = ('type=mod\nname=%s\nversion=7\nmachine=synth\ngeneration=%s\n' % (name, generation)).encode()
            (target / 'info.txt').write_bytes(info)
            (target / 'files.sha256').write_bytes(S.list_text({'info.txt': info}))
        library = {r['id']: r for r in self.packages(1)}
        self.assertEqual(library['report']['identity'], hashlib.sha256(files['files.sha256']).hexdigest())
        self.assertEqual(library['report']['sha256'], hashlib.sha256(files['os.ldr']).hexdigest())
        self.assertEqual(library['report']['valid'], '1')
        dropped = self.packages(0)
        self.assertEqual([(r['id'], r['identity']) for r in dropped],
                         [('no-extension', hashlib.sha256(files['os.ldr']).hexdigest())])
        mods = {r['id']: r for r in self.packages(2)}
        self.assertEqual(set(mods), {'gen-a/alpha', 'gen-a/beta', 'gen-b/gamma'})
        self.assertTrue(all(r['type'] == 'mod' and r['version'] == '7' for r in mods.values()))

    def test_state_pages_space_versions_and_errors(self):
        text = b'state=official\n' + b'#padding\n' * 250
        (self.root / 'xy-forever/content-state.txt').write_bytes(text)
        got = b''
        while True:
            code, body = self.request(P.ids.STATE_GET, 1, 3, len(got))
            self.assertEqual(code, 0)
            self.assertLessEqual(len(body) + 4, 1024)
            version, part, offset, more = struct.unpack('<4I', body[:16])
            self.assertEqual((version, part, offset), (1, 3, len(got)))
            got += body[16:]
            if not more:
                break
        self.assertEqual(got, text)
        self.assertEqual(self.request(P.ids.STATE_GET, 1, 4, 0)[0], P.ids.S_NOT_FOUND)
        code, body = self.request(P.ids.STORAGE_SPACE, 1)
        self.assertEqual((code, len(body)), (0, 12))
        self.assertEqual(struct.unpack('<I', body[:4]), (1,))
        for cmd, words in ((P.ids.PACKAGE_LIST, (2, 0, 0, 0)), (P.ids.STATE_GET, (2, 0, 0)), (P.ids.STORAGE_SPACE, (2,))):
            self.assertEqual(self.request(cmd, *words)[0], P.ids.S_UNSUPPORTED)
            self.assertEqual(self.dev.call(cmd, b'')[0], P.ids.S_BAD_REQUEST)
        self.assertEqual(self.request(P.ids.STATE_GET, 1, 5, 0)[0], P.ids.S_BAD_REQUEST)
        self.assertEqual(self.request(P.ids.PACKAGE_LIST, 1, 3, 0, 0)[0], P.ids.S_BAD_REQUEST)
