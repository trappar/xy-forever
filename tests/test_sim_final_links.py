"""A refuses a writable open whose final link reaches an inbox file (issue #6: final_link_reaches_inbox, the one
caller of the stock lookup 889fa2be). Simulator only: host links stand for yaffs links (absolute ones by their host
path), so yaffs hardlinks are not modelled here."""
import unittest

import test_simulator as S

WRITE = 0x8301                                  # the stock "wb" open flags
LOG = '/yaffs2/xy-forever/log/'


@unittest.skipIf(S.UNAVAILABLE, S.UNAVAILABLE)
class FinalLinks(unittest.TestCase):
    setUpClass = classmethod(S.SimulatorTest.setUpClass.__func__)
    tearDownClass = classmethod(S.SimulatorTest.tearDownClass.__func__)
    setUp = S.SimulatorTest.setUp
    tearDown = S.SimulatorTest.tearDown
    connect = S.SimulatorTest.connect
    write = S.SimulatorTest.write
    a_request = S.SimulatorTest.a_request
    command = S.SimulatorTest.command
    write_entry = S.SimulatorTest.write_entry

    def seed(self, entry=True):
        root = self.dir / 'yaffs2'
        log = root / 'xy-forever/log'
        (root / 'upgrade/sub').mkdir(parents=True)
        (root / 'upgrade/sub/x.tfw').write_bytes(b'unchanged')
        (root / 'user').mkdir()
        (root / 'user/song.bin').write_bytes(b'mine')
        (log / 'rel').symlink_to('../../upgrade/sub/x.tfw')                  # relative, from the link's folder
        (log / 'abs').symlink_to(root / 'upgrade/sub/x.tfw')                # absolute (/yaffs2/upgrade/sub/x.tfw)
        (log / 'chain').symlink_to('rel')                                   # relative -> relative
        (root / 'user/hop').symlink_to(root / 'xy-forever/log/rel')         # absolute -> relative
        (log / 'chain-abs').symlink_to(root / 'user/hop')
        (log / 'missing').symlink_to('../../upgrade/sub/new.tfw')           # a missing target in the inbox
        (log / 'dangling').symlink_to('../../user/new.bin')                 # a missing target outside it
        (log / 'song').symlink_to('../../user/song.bin')                    # benign
        (log / 'loop-a').symlink_to('loop-b')
        (log / 'loop-b').symlink_to('loop-a')
        (log / 'long').symlink_to('long-1')                                 # 7 links, then the inbox file
        for i in range(1, 7):
            (log / ('long-%d' % i)).symlink_to('long-%d' % (i + 1) if i < 6 else 'rel')
        self.connect(startup_ready=True)
        if entry:
            self.write_entry()
        return root

    def open_close(self, path, flags=WRITE):
        fd, error = self.a_request(S.PATH_CLASS, 0x29, flags, path=path)
        if fd >= 0:
            self.assertEqual(self.a_request(S.PARTITION_CLASS, 0x2d, fd), (0, 0))
        return fd, error

    def test_writable_opens_through_final_links_to_the_inbox_are_refused(self):
        root = self.seed()
        for name in ('rel', 'abs', 'chain', 'chain-abs', 'missing', 'loop-a', 'long'):
            self.assertEqual(self.open_close(LOG + name), (-1, S.EACCES), name)
        self.assertEqual((root / 'upgrade/sub/x.tfw').read_bytes(), b'unchanged')
        self.assertEqual(sorted(p.name for p in (root / 'upgrade/sub').iterdir()), ['x.tfw'])
        fd, error = self.open_close(LOG + 'chain-abs', 0)                   # reading through them: as before
        self.assertEqual(error, 0)
        self.assertGreaterEqual(fd, 0)

    def test_benign_and_dangling_links_still_open_and_links_delete_as_entries(self):
        root = self.seed()
        fd, error = self.open_close(LOG + 'song')
        self.assertEqual(error, 0)
        self.assertGreaterEqual(fd, 0)
        self.assertNotEqual(self.open_close(LOG + 'dangling')[1], S.EACCES)
        # unlink acts on the link entry: a benign one and one into the inbox both go, their targets stay
        for name in ('song', 'rel', 'abs'):
            self.assertEqual(self.a_request(S.PATH_CLASS, 0x25, path=LOG + name), (0, 0), name)
            self.assertFalse((root / 'xy-forever/log' / name).is_symlink(), name)
        self.assertTrue((root / 'user/song.bin').exists())
        self.assertEqual((root / 'upgrade/sub/x.tfw').read_bytes(), b'unchanged')

    def test_staged_write_through_a_final_link_is_refused(self):
        """The original reproduction: file.write_staged onto a log name that links to an inbox file."""
        root = self.seed(entry=False)
        dev = self.links[-1]
        for name in ('rel', 'abs', 'chain-abs'):
            self.assertEqual(self.write(dev, b'replaced', LOG + name)[0], S.ids.S_STORAGE, name)   # A's EACCES
        self.assertEqual((root / 'upgrade/sub/x.tfw').read_bytes(), b'unchanged')
        self.assertEqual(self.write(dev, b'new song', LOG + 'song')[0], 0)
        self.assertEqual((root / 'user/song.bin').read_bytes(), b'new song')


if __name__ == '__main__':
    unittest.main()
