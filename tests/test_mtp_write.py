"""xy_forever/mtp_write.py against a fake OP-XY that also takes writes (test_mtp_read's fake plus SendObjectInfo,
SendObject and their data phase): folders, uploads, read back, the scratch-only deletes, and refusals that never
reach the transport (protected names in any case, the depth rule, paths outside /yaffs2). No device access."""
from pathlib import Path
import struct
import unittest

from xy_forever import mtp_read as M, mtp_write as W
import test_mtp_read as R

ROOT = Path(__file__).resolve().parents[1]

WRITE_OPS = set(W.WRITES)


class WritableFake(R.FakeOpXy):
    """FakeOpXy that carries out SendObjectInfo/SendObject (data phases in any number of OUT transfers)."""

    def __init__(self, tree, deny=()):
        super().__init__(tree)
        self.deny, self.pending, self.data, self.upload, self.zlps, self.codes = set(deny), None, None, None, 0, []

    def write(self, data):
        if not data:
            self.zlps += 1
            return 0
        if self.data is not None:
            self.data += data
            return self.finish()
        length, kind, code, tid = struct.unpack_from('<IHHI', data)
        if kind == 2:
            assert self.pending and self.pending[0] == code, 'a data phase nobody asked for'
            self.total, self.data = length - 12, bytearray(data[12:])
            return self.finish()
        self.codes.append(code)
        if code in (0x100C, 0x100D):
            self.pending = (code, tid, struct.unpack_from('<%dI' % ((length - 12) // 4), data, 12))
            return 0
        return super().write(data)

    def finish(self):
        if len(self.data) < self.total:
            return 0
        assert len(self.data) == self.total, 'more data than the container said'
        (code, tid, params), body = self.pending, bytes(self.data)
        self.pending = self.data = None
        if code == 0x100C:
            fmt, name = struct.unpack_from('<H', body, 4)[0], M.ptp_string(body, 52)[0]
            if name in self.deny:
                self.out.append(R.container(3, 0x200F, tid))
                return 0
            handle = max(self.objects, default=0) + 1
            self.objects[handle] = (params[1], name, None if fmt == M.FOLDER else b'')
            self.upload = handle if fmt != M.FOLDER else None
            self.out.append(R.container(3, M.OK, tid, struct.pack('<III', params[0], params[1], handle)))
        else:
            parent, name, _ = self.objects[self.upload]
            self.objects[self.upload] = (parent, name, body)
            self.out.append(R.container(3, M.OK, tid))
        return 0

    def tree(self):
        def path(h):
            parent, name, _ = self.objects[h]
            return ('' if parent == M.ROOT else path(parent)) + '/' + name
        return {path(h): o[2] for h, o in self.objects.items()}


def writer(tree=None, scratch='/yaffs2/scratch', **kw):
    device = WritableFake(R.STOCK_TREE if tree is None else tree, **kw)
    w = W.Writer(device, scratch)
    w.open()
    return device, w


class Writes(unittest.TestCase):
    def test_folders_uploads_read_back_and_cleanup(self):
        device, w = writer()
        where = w.mkdir('/yaffs2/scratch/run/small')
        self.assertEqual(device.tree()['/scratch/run/small'], None)
        big = bytes(range(256)) * 800                                    # 204 800 bytes: four OUT transfers
        _, h = w.put('/yaffs2/scratch/run/big.bin', big)
        self.assertEqual(w.get(h), big)
        self.assertEqual(w.create('/yaffs2/scratch/run/small/a.bin', b'a' * 500, where)[1], max(device.objects))
        self.assertEqual(device.zlps, 1)                                 # 12 + 500 bytes: a ZLP ends the phase
        w.put('/yaffs2/scratch/run/small/empty.bin', b'')
        w.put('/yaffs2/scratch/run/big.bin', b'again')                   # replaced inside the scratch folder
        self.assertEqual(device.tree()['/scratch/run/big.bin'], b'again')
        removed = w.remove_tree()
        self.assertEqual(removed[-1], '/yaffs2/scratch')
        self.assertEqual(set(removed), {'/yaffs2/scratch', '/yaffs2/scratch/run', '/yaffs2/scratch/run/big.bin',
                                        '/yaffs2/scratch/run/small', '/yaffs2/scratch/run/small/a.bin',
                                        '/yaffs2/scratch/run/small/empty.bin'})
        self.assertEqual(set(device.tree()), set(WritableFake(R.STOCK_TREE).tree()))    # the rest untouched
        self.assertEqual(w.remove_tree(), [])                                           # already gone

    def test_the_depth_rule(self):
        device, w = writer()
        w.mkdir('/yaffs2/scratch/a/b/c/d/e')                             # the 6th folder below /yaffs2
        w.put('/yaffs2/scratch/a/b/c/d/e/f.bin', b'x')                   # a file in it: the deepest allowed
        sent = len(device.sent)
        for refused in (lambda: w.mkdir('/yaffs2/scratch/a/b/c/d/e/g'),
                        lambda: w.put('/yaffs2/scratch/a/b/c/d/e/g/f.bin', b'x')):
            self.assertRaisesRegex(M.Refused, 'folders below /yaffs2', refused)
        self.assertEqual(len(device.sent), sent)
        self.assertIn('/yaffs2/scratch/a/b/c/d/e/f.bin', w.remove_tree())

    def test_refusals_never_reach_the_transport(self):
        device, w = writer()
        device.sent.clear()
        for path in ('/upgrade/x', '/yaffs2', '/yaffs2/upgrade/x', '/yaffs2/UPGRADE/x', '/yaffs2/Upgrade',
                     '/yaffs2/bootloader.dev', '/yaffs2/BootLoader.DEV', '/yaffs2/scratch/../upgrade/x',
                     '/yaffs2//x', '/content/x'):
            self.assertRaises(M.Refused, w.put, path, b'x')
            self.assertRaises(M.Refused, w.mkdir, path)
        for path in ('/yaffs2/content/op1_factory.json', '/yaffs2/scratch2/x', '/yaffs2', '/yaffs2/upgrade',
                     '/yaffs2/scratch/../content/op1_factory.json'):
            self.assertRaises(M.Refused, w.delete, path)
            self.assertRaises(M.Refused, w.remove_tree, path)
        self.assertEqual(device.sent, [])
        for scratch in ('/yaffs2', '/yaffs2/upgrade/s', '/yaffs2/UpGrade', '/tmp/s', '/yaffs2/a/b/c/d/e/f/g'):
            self.assertRaises(M.Refused, W.Writer, device, scratch)

    def test_an_existing_file_outside_the_scratch_folder_is_never_replaced(self):
        device, w = writer()
        self.assertRaisesRegex(M.Refused, 'outside the scratch', w.put, '/yaffs2/content/display/x.svg', b'x')
        self.assertFalse(WRITE_OPS & set(device.codes))
        self.assertEqual(device.tree()['/content/display/x.svg'], b'<svg/>')
        w.mkdir('/yaffs2/content/new')                                   # a new folder elsewhere is a write
        self.assertIn('/content/new', device.tree())

    def test_device_refusals_are_one_line_errors(self):
        device, w = writer(deny={'no.bin'})
        w.mkdir('/yaffs2/scratch')
        self.assertRaisesRegex(M.MtpError, 'SendObjectInfo /yaffs2/scratch/no.bin: response 0x200f',
                               w.put, '/yaffs2/scratch/no.bin', b'x')
        self.assertRaisesRegex(M.MtpError, 'no folder', w.put, '/yaffs2/missing/x.bin', b'x')

    def test_the_read_session_still_refuses_writes(self):
        device = WritableFake(R.STOCK_TREE)
        for code in WRITE_OPS:
            self.assertRaises(M.Refused, M.Session(device).transact, code, 1)
        self.assertEqual(device.sent, [])

    def test_object_info_dataset(self):
        info = W.object_info('kits', 0, True)
        self.assertEqual(struct.unpack_from('<H', info, 4)[0], M.FOLDER)
        self.assertEqual(struct.unpack_from('<H', info, 42)[0], 1)       # AssociationType: generic folder
        self.assertEqual(M.ptp_string(info, 52)[0], 'kits')
        info = W.object_info('a.bin', 7, False)
        self.assertEqual(struct.unpack_from('<HHI', info, 4), (W.FILE, 0, 7))


if __name__ == '__main__':
    unittest.main()
