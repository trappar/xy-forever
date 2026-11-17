"""xy_forever/mtp_read.py and `setup.py import-stock --from-device` against a fake OP-XY: a bulk transport that
answers PTP containers from an in-memory tree (synthetic bytes, never the stock files; the pins patched as
test_setup.py does). Covers the fetch + import, a missing file, the read-only choke point and the USB messages."""
import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import mtp_read as M  # noqa: E402

spec = importlib.util.spec_from_file_location('xyf_setup_mtp', ROOT / 'scripts/setup.py')
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

OS, BOOT, CATALOG = b'os 1.2.1' * 20000, b'boot 1.2.1', b'{"synthetic": "stock catalog"}'
PINS = {'1.2.1': {'os.ldr': hashlib.sha256(OS).hexdigest(), 'bootloader.ldr': hashlib.sha256(BOOT).hexdigest()}}
STOCK_TREE = {'/upgrade/firmware/blackfin/bin/os.ldr': OS, '/upgrade/firmware/blackfin/bin/bootloader.ldr': BOOT,
              '/upgrade/firmware/blackfin/bin/readme.txt': b'other', '/content/op1_factory.json': CATALOG,
              '/content/display/x.svg': b'<svg/>'}
WRITES = {0x100C: 'SendObjectInfo', 0x100D: 'SendObject', 0x100B: 'DeleteObject', 0x9804: 'SetObjectPropValue',
          0x100F: 'FormatStore', 0x1019: 'MoveObject', 0x101A: 'CopyObject', 0x9803: 'GetObjectPropValue',
          0x1010: 'ResetDevice', 0x101B: 'GetPartialObject', 0x9810: 'SetObjectReferences', 0xFFFF: 'unknown'}
STORAGE = 0x00010001


def container(kind, code, tid, payload=b''):
    return struct.pack('<IHHI', 12 + len(payload), kind, code, tid) + payload


def ptp_str(text):
    raw = (text + '\0').encode('utf-16-le')
    return bytes([len(raw) // 2]) + raw


class FakeOpXy:
    """A transport: commands go in (write), containers come out (read) in transfers of at most `size` bytes.
    It would carry out any operation it is sent, so a refused one must never reach it."""

    def __init__(self, tree):
        self.objects = {}                       # handle -> (parent, name, data or None for a folder)
        for path, data in tree.items():
            parent = M.ROOT
            parts = path.strip('/').split('/')
            for i, part in enumerate(parts):
                folder = i < len(parts) - 1
                found = [h for h, o in self.objects.items() if o[0] == parent and o[1] == part]
                if not found:
                    self.objects[len(self.objects) + 1] = (parent, part, None if folder else data)
                    found = [len(self.objects)]
                parent = found[0]
        self.sent, self.out, self.closed, self.session = [], [], False, False

    def write(self, data):
        self.sent.append(data)
        _, kind, code, tid = struct.unpack_from('<IHHI', data)
        params = struct.unpack_from('<%dI' % ((len(data) - 12) // 4), data, 12)
        rcode, payload = self.answer(code, params)
        if payload is not None:
            self.out.append(container(2, code, tid, payload))
        self.out.append(container(3, rcode, tid))
        return 0

    def answer(self, code, params):
        if code == 0x1002:
            if self.session:
                return M.SESSION_ALREADY_OPEN, None
            self.session = True
            return M.OK, None
        if code == 0x1003:
            self.session = False
            return M.OK, None
        if code == 0x1004:
            return M.OK, struct.pack('<II', 1, STORAGE)
        if code == 0x1007:
            kids = [h for h, o in self.objects.items() if o[0] == params[2]]
            return M.OK, struct.pack('<%dI' % (len(kids) + 1), len(kids), *kids)
        if code == 0x1008:
            parent, name, data = self.objects[params[0]]
            return M.OK, (struct.pack('<IHHI', STORAGE, M.FOLDER if data is None else 0x3000, 0, len(data or b''))
                          + bytes(40) + ptp_str(name) + b'\0\0\0')
        if code == 0x1009:
            return M.OK, self.objects[params[0]][2]
        if code == 0x100B:                      # what the client must never send
            del self.objects[params[0]]
            return M.OK, None
        return 0x2005, None                     # OperationNotSupported

    def read(self, size):
        if not self.out:
            return -7, b''                      # LIBUSB_ERROR_TIMEOUT
        head = self.out[0]
        self.out[0] = head[size:]
        if not self.out[0]:
            self.out.pop(0)
        return 0, head[:size]

    def close(self):
        self.closed = True


class Fetch(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.incoming, self.dest = self.work / 'private/incoming', self.work / 'private/firmware/stock-1.2.1'
        patcher = mock.patch.dict(S.PINS['open_xy'], {'catalog_sha256': hashlib.sha256(CATALOG).hexdigest()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def from_device(self, tree):
        device, out = FakeOpXy(tree), io.StringIO()
        with contextlib.redirect_stdout(out):
            S.import_from_device(self.incoming, self.dest, PINS, transport=device)
        return device, out.getvalue()

    def test_the_three_files_are_read_then_imported(self):
        device, out = self.from_device(STOCK_TREE)
        self.assertEqual((self.dest / 'os.ldr').read_bytes(), OS)          # 160 000 bytes: several transfers
        self.assertEqual((self.dest / 'bootloader.ldr').read_bytes(), BOOT)
        self.assertEqual((self.dest / 'op1_factory.json').read_bytes(), CATALOG)
        self.assertEqual(sorted(p.name for p in self.incoming.iterdir()), sorted(S.DEVICE_FILES.values()))
        self.assertIn('from your device copy', out)
        sent = {struct.unpack_from('<H', c, 6)[0] for c in device.sent}
        self.assertLessEqual(sent, set(M.READ_ONLY))
        self.assertTrue(device.closed)
        self.assertFalse(device.session)

    def test_an_open_session_is_closed_and_reopened(self):
        device = FakeOpXy(STOCK_TREE)
        device.session = True
        M.fetch({'/content/op1_factory.json': 'c.json'}, self.incoming, device, log=lambda _: None)
        self.assertEqual((self.incoming / 'c.json').read_bytes(), CATALOG)

    def test_a_missing_pair_file_is_one_line_pointing_at_the_manual_copy(self):
        tree = dict(STOCK_TREE)
        del tree['/upgrade/firmware/blackfin/bin/bootloader.ldr']
        with self.assertRaisesRegex(S.SetupError, r'^/upgrade/firmware/blackfin/bin/bootloader\.ldr: not on the '
                                    r'OP-XY .*copy the files by hand instead \(ONBOARDING\.md step 4\)$'):
            self.from_device(tree)
        self.assertFalse(self.dest.exists())

    def test_a_missing_or_modified_catalog_leaves_it_to_fixtures(self):
        for label, catalog in (('missing', None), ('modified', CATALOG + b' modded')):
            with self.subTest(label):
                tree = dict(STOCK_TREE)
                if catalog is None:
                    del tree['/content/op1_factory.json']
                else:
                    tree['/content/op1_factory.json'] = catalog
                with tempfile.TemporaryDirectory() as tmp:
                    self.incoming, self.dest = Path(tmp) / 'incoming', Path(tmp) / 'stock'
                    _, out = self.from_device(tree)
                    self.assertEqual(sorted(p.name for p in self.dest.iterdir()), sorted(S.PAIR))
                self.assertRegex(out, 'not on the OP-XY; fixtures downloads it' if catalog is None
                                 else r'ignored, not the stock 1\.2\.1 file')

    def test_usb_failures_become_one_line_setup_errors(self):
        def refuse(*_):
            raise M.MtpError('no OP-XY (VID 0x2367) on USB')
        with mock.patch.object(S.mtp_read, 'open_op_xy', refuse), \
                self.assertRaisesRegex(S.SetupError, r'^no OP-XY .*\(ONBOARDING\.md step 4\)$'):
            S.import_from_device(self.incoming, self.dest, PINS)
        err = io.StringIO()
        with mock.patch.object(S.mtp_read, 'open_op_xy', refuse), contextlib.redirect_stderr(err):
            self.assertEqual(S.main(['import-stock', '--from-device']), 1)
        self.assertEqual(len(err.getvalue().splitlines()), 1)

    def test_import_stock_takes_dir_or_from_device(self):
        for argv in (['import-stock'], ['import-stock', 'x', '--from-device']):
            with self.subTest(argv), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(S.main(argv), 1)
                self.assertIn('DIR or --from-device', err.getvalue())


class ReadOnly(unittest.TestCase):
    def test_writes_and_unknown_operations_never_reach_the_transport(self):
        device = FakeOpXy(STOCK_TREE)
        session = M.Session(device)
        session.open()
        handle = session.find('/upgrade/firmware/blackfin/bin/os.ldr')
        before, objects = len(device.sent), dict(device.objects)
        for code in list(WRITES) + [c for c in range(0x1000, 0x1030) if c not in M.READ_ONLY]:
            with self.subTest('0x%04x %s' % (code, WRITES.get(code, ''))):
                with self.assertRaisesRegex(M.Refused, '0x%04x refused: this client only reads' % code):
                    session.transact(code, handle)
        self.assertEqual(len(device.sent), before)
        self.assertEqual(device.objects, objects)

    def test_the_allowlist_is_reads_only(self):
        self.assertEqual(set(M.READ_ONLY), {0x1001, 0x1002, 0x1003, 0x1004, 0x1005, 0x1007, 0x1008, 0x1009})

    def test_find_walks_by_names(self):
        session = M.Session(FakeOpXy(STOCK_TREE))
        session.open()
        self.assertEqual(session.get(session.find('/content/display/x.svg')), b'<svg/>')
        self.assertIsNone(session.find('/content/display/y.svg'))
        self.assertIsNone(session.find('/nowhere/os.ldr'))


class UsbMessages(unittest.TestCase):
    MTP = {'number': 0, 'cls': (255, 0, 0), 'endpoints': [(0x81, 2, 512), (0x01, 2, 512), (0x82, 3, 64)]}

    def test_no_op_xy_or_not_in_mtp_mode(self):
        with self.assertRaisesRegex(M.MtpError, r'^no OP-XY \(VID 0x2367\) on USB$'):
            M.choose([('d', (0x05ac, 1), [self.MTP])])
        cdc = [{'number': 0, 'cls': (2, 2, 1), 'endpoints': [(0x83, 3, 8)]},
               {'number': 1, 'cls': (10, 0, 0), 'endpoints': [(0x81, 2, 512), (0x01, 2, 512)]}]
        for label, found in (('serial', cdc), ('audio', [{'number': 0, 'cls': (1, 1, 0), 'endpoints': []}])):
            with self.subTest(label), self.assertRaisesRegex(M.MtpError, 'on USB but not in MTP mode'):
                M.choose([('d', (M.OP_XY_VID, 0x21), found)])
        self.assertEqual(M.choose([('d', (M.OP_XY_VID, 0x21), cdc + [self.MTP])])[2], (0, 0x81, 0x01))

    def test_a_held_interface_names_the_programs_to_quit(self):
        for rc in (M.LIBUSB_ERROR_ACCESS, M.LIBUSB_ERROR_BUSY):
            self.assertRegex(M.usb_problem('claim interface 0', rc), 'held by another program: quit OpenMTP, '
                             'Image Capture and Android File Transfer')
        self.assertEqual(M.usb_problem('claim interface 0', -4), 'claim interface 0 failed (libusb -4)')

    def test_missing_libusb(self):
        with mock.patch.object(M.subprocess, 'run', side_effect=OSError), \
                mock.patch.object(M.ctypes.util, 'find_library', return_value=None), \
                self.assertRaisesRegex(M.MtpError, r'^libusb-1\.0 not found \(brew install libusb\)$'):
            M.load_libusb()


if __name__ == '__main__':
    unittest.main()
