"""A refuses mutations reached through aliases into inbox descendants (issue #6). Simulator only."""

import unittest

import pytest
import test_simulator as S


@unittest.skipIf(S.UNAVAILABLE, S.UNAVAILABLE)
class ProtectedPaths(unittest.TestCase):
    setUpClass = classmethod(S.SimulatorTest.setUpClass.__func__)
    tearDownClass = classmethod(S.SimulatorTest.tearDownClass.__func__)
    setUp = S.SimulatorTest.setUp
    tearDown = S.SimulatorTest.tearDown
    connect = S.SimulatorTest.connect
    file = S.SimulatorTest.file
    write = S.SimulatorTest.write
    a_request = S.SimulatorTest.a_request
    command = S.SimulatorTest.command
    write_entry = S.SimulatorTest.write_entry

    def aliases(self):
        root = self.dir / 'yaffs2'
        (root / 'upgrade/sub/deep/empty').mkdir(parents=True)
        (root / 'upgrade/sub/deep/x.tfw').write_bytes(b'unchanged')
        (root / 'alias-sub').symlink_to(root / 'upgrade/sub')
        (root / 'nested').symlink_to('alias-sub/deep')
        (root / 'up-down').symlink_to('upgrade/sub/deep/../deep')
        (root / 'user/safe/empty').mkdir(parents=True)
        (root / 'safe').symlink_to(root / 'user/safe')
        (root / 'xy-forever/log/move.txt').write_bytes(b'move')
        self.connect()
        self.write_entry()
        return root

    def pair(self, source, target):
        self.assertEqual(self.a_request(S.PATH_CLASS, 0x21, 0x52464E52, path=source), (0, 0))
        return self.a_request(S.PATH_CLASS, 0x21, 0x4F544E52, path=target)

    @pytest.mark.sim
    def test_mutations_to_descendant_directory_aliases_are_refused(self):
        """Kept: Protected paths: aliases to protected folders cannot be changed."""
        root = self.aliases()
        for alias in ('alias-sub/deep', 'nested', 'up-down'):
            base = '/yaffs2/' + alias
            for op, flags, suffix in (
                (0x29, 0x8301, '/x.tfw'),
                (0x29, 0x8301, '/new.tfw'),
                (0x25, 0, '/x.tfw'),
                (0x26, 0, '/new-dir'),
                (0x27, 0, '/empty'),
            ):
                self.assertEqual(
                    self.a_request(S.PATH_CLASS, op, flags, path=base + suffix),
                    (-1, S.EACCES),
                    (alias, hex(op), suffix),
                )
            self.assertEqual(self.pair(base + '/x.tfw', S.BASE + 'log/out.tfw'), (-1, S.EACCES))
            self.assertEqual(self.pair(S.BASE + 'log/move.txt', base + '/move.txt'), (-1, S.EACCES))
        self.assertEqual((root / 'upgrade/sub/deep/x.tfw').read_bytes(), b'unchanged')
        self.assertEqual(sorted(p.name for p in (root / 'upgrade/sub/deep').iterdir()), ['empty', 'x.tfw'])
        self.assertEqual((root / 'xy-forever/log/move.txt').read_bytes(), b'move')
        # The same protected targets remain readable.
        fd, error = self.a_request(S.PATH_CLASS, 0x29, path='/yaffs2/nested/x.tfw')
        self.assertEqual(error, 0)
        self.assertGreaterEqual(fd, 0)
        self.assertEqual(self.a_request(S.PARTITION_CLASS, 0x2D, fd), (0, 0))

    @pytest.mark.sim
    def test_serial_delete_refuses_the_original_alias_reproduction(self):
        """Kept: Protected paths: the alias delete that once got through stays refused."""
        root = self.aliases()
        dev = self.links[-1]
        self.assertEqual(dev.change(S.P.DELETE_FILE, '/yaffs2/alias-sub/deep/x.tfw'), (S.ids.S_REFUSED, S.EACCES))
        self.assertEqual((root / 'upgrade/sub/deep/x.tfw').read_bytes(), b'unchanged')

    @pytest.mark.sim
    def test_alias_hiding_ancestry_beyond_the_walk_bound_is_refused(self):
        """Kept: Protected paths: an alias deeper than the walk bound is refused."""
        root = self.aliases()
        deep = root.joinpath(*(['d'] * 130))
        deep.mkdir(parents=True)
        (deep / 'old.bin').write_bytes(b'old')
        (root / 'deep-alias').symlink_to(deep)
        self.assertEqual(self.a_request(S.PATH_CLASS, 0x25, path='/yaffs2/deep-alias/old.bin'), (-1, S.EACCES))
        self.assertEqual((deep / 'old.bin').read_bytes(), b'old')


if __name__ == '__main__':
    unittest.main()
