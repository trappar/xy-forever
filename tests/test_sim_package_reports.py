"""Required report APIs against the real simulator service; exact identities and bounded pages."""
import hashlib
import struct
import time
import unittest
from xy_forever import bounds, sim, usb_proto as P, package_store as S
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
            self.sim.advance(20)

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
        legacy_sha = hashlib.sha256((self.root / 'xy-forever/firmware/opxy-121/os.ldr').read_bytes()).hexdigest()
        self.assertEqual(library['opxy-121']['sha256'], legacy_sha)
        self.assertEqual(library['opxy-121']['identity'], legacy_sha)
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
        self.sim.set('statfs_override', 1)
        self.sim.set('statfs_block_bytes', 0x80000000)
        self.sim.set('statfs_free_blocks', 3)
        code, body = self.request(P.ids.STORAGE_SPACE, 1)
        self.assertEqual((code, len(body)), (0, 12))
        self.assertEqual(struct.unpack('<3I', body), (1, 0x80000000, 1))
        self.sim.set('statfs_override', 0)
        for cmd, words in ((P.ids.PACKAGE_LIST, (2, 0, 0, 0)), (P.ids.STATE_GET, (2, 0, 0)), (P.ids.STORAGE_SPACE, (2,))):
            self.assertEqual(self.request(cmd, *words)[0], P.ids.S_UNSUPPORTED)
            self.assertEqual(self.dev.call(cmd, b'')[0], P.ids.S_BAD_REQUEST)
        self.assertEqual(self.request(P.ids.STATE_GET, 1, 5, 0)[0], P.ids.S_BAD_REQUEST)
        self.assertEqual(self.request(P.ids.PACKAGE_LIST, 1, 3, 0, 0)[0], P.ids.S_BAD_REQUEST)

    def test_native_metadata_precedence_and_exact_identity(self):
        from xy_forever import native_json as J
        folder = self.root / 'xy-forever/firmware/native'
        folder.mkdir()
        image = b'LDR'
        raw = J.package_document(dict(type='firmware', name='Native', version='8', kind='custom',
                                     bytes=len(image), crc32='00000000', sha256=hashlib.sha256(image).hexdigest()))
        (folder / 'manifest.json').write_bytes(raw)
        (folder / 'info.txt').write_bytes(b'type=firmware\nversion=legacy\n')
        (folder / 'os.ldr').write_bytes(image)
        listing = b'raw list identity\n'
        (folder / 'files.sha256').write_bytes(listing)
        rows = {r['id']: r for r in self.packages(1)}
        self.assertEqual((rows['native']['version'], rows['native']['valid']), ('8', '1'))
        self.assertEqual(rows['native']['identity'], hashlib.sha256(listing).hexdigest())
        self.assertEqual((folder / 'manifest.json').read_bytes(), raw)
        for bad in (b'', b'{', b'{}', b'{"pack":"Sound pack"}', raw.replace(b'"schema_version":1', b'"schema_version":2')):
            with self.subTest(raw=bad):
                (folder / 'manifest.json').write_bytes(bad)
                row = {r['id']: r for r in self.packages(1)}['native']
                self.assertEqual((row['type'], row['valid']), ('unknown', '0'))
                self.assertNotEqual(row['version'], 'legacy')

    @bounds.slow                                    # #233: needs longer under the paced clock
    def test_large_inbox_identity_is_streamed_without_size_cap(self):
        inbox = self.root / 'xy-forever/inbox'
        inbox.mkdir()
        file = inbox / 'large-without-extension'
        block = bytes(range(256)) * 4096
        sha = hashlib.sha256()
        with file.open('wb') as stream:
            for _ in range(100):  # representative official inbox size, bounded test-side memory too
                stream.write(block)
                sha.update(block)
        code, body = self.dev.call(P.ids.PACKAGE_LIST, struct.pack('<4I', 1, 0, 0, 0), timeout=bounds.SLOW_S)
        self.assertEqual(code, 0)
        row = dict(line.split('=', 1) for line in body[16:].decode().splitlines())
        self.assertEqual(row['identity'], sha.hexdigest())
        self.assertEqual(row['sha256'], sha.hexdigest())

    def test_unresolved_journal_retains_blobs_in_both_collectors(self):
        value = b'journal-only content'
        sha = hashlib.sha256(value).hexdigest()
        blobs = self.root / 'xy-forever/blobs'
        blobs.mkdir(exist_ok=True)
        blob = blobs / sha
        blob.write_bytes(value)
        journal = self.root / 'xy-forever/job.txt'
        journal.write_bytes(b'version=2\n')
        with S.Session(self.dev) as session:
            self.assertEqual(session.collect(), 0)
        self.sim.advance(10000)
        for _ in range(20):
            self.dev.hello()
            time.sleep(0.01)
        self.assertEqual(blob.read_bytes(), value)

    def test_path_and_offset_bounds(self):
        target = self.root / 'xy-forever/mods' / ('g' * 127) / ('p' * 127)
        target.mkdir(parents=True)
        self.assertEqual(self.request(P.ids.PACKAGE_LIST, 1, 2, 0, 0)[0], P.ids.S_TOO_LARGE)
        self.assertEqual(self.request(P.ids.STATE_GET, 1, 0, 0xffffffff)[0], P.ids.S_RANGE)

    def test_space_statfs_return_value_unsigned_product_and_zero_free(self):
        # Missing state leaves YAFFS errno set; the following successful statfs must ignore it.
        self.assertEqual(self.request(P.ids.STATE_GET, 1, 4, 0)[0], P.ids.S_NOT_FOUND)
        self.assertEqual(self.sim.ask_a(0x11907388, 0x31, body=b'/yaffs2\0'), (0, 2))
        self.sim.set('statfs_override', 1)
        for block, free in ((4096, 0), (1, 0xffffffff), (0x80000001, 3), (0xffffffff, 0xffffffff)):
            self.sim.set('statfs_block_bytes', block)
            self.sim.set('statfs_free_blocks', free)
            code, body = self.request(P.ids.STORAGE_SPACE, 1)
            value = block * free
            self.assertEqual((code, struct.unpack('<3I', body)), (0, (1, value & 0xffffffff, value >> 32)))
        self.sim.set('statfs_fail', 1)
        self.assertEqual(self.request(P.ids.STORAGE_SPACE, 1), (P.ids.S_STORAGE, b''))
        self.sim.set('statfs_fail', 0)
        self.sim.set('statfs_block_bytes', 0)
        self.assertEqual(self.request(P.ids.STORAGE_SPACE, 1), (P.ids.S_STORAGE, b''))
        self.sim.set('statfs_block_bytes', 4096)
        self.sim.set('statfs_free_blocks', 4)
        self.assertEqual(self.request(P.ids.STORAGE_SPACE, 1), (0, struct.pack('<3I', 1, 16384, 0)))
