"""XY Forever USB file write (Test 049, --xyf-product): b's path scope, the stock "wb" open
flags, the host tool and builder wiring, the library add plan and the SCREEN PNG. No device
execution."""
import ctypes as C
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402
sys.path.insert(0, str(ROOT / 'tests'))
import xyf_build as builder
from xy_forever import usb_proto as P
from xy_forever.paths import STOCK_121

GOOD = b'/yaffs2/xy-forever/firmware/opxy-121/os.ldr'


class FileWritePathTest(unittest.TestCase):
    """B's FILE_WRITE scope (valid_write_path): below /yaffs2/xy-forever/, and allowed by the file
    engine's policy for a write open. A's own decision is the engine's (tests/test_file_engine.py)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-file-write-')
        lib = Path(cls.tmp.name) / 'fw.dylib'
        hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-O1', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                       '-ffreestanding', '-fno-builtin', '-fno-exceptions', '-fno-rtti', '-DXYF_USB_FILE_WRITE=1',
                       '-I', str(ROOT / 'target'), str(ROOT / 'target/storage_server.cpp'),
                       str(ROOT / 'target/file_engine.cpp'), str(ROOT / 'tests/file_write_bridge.cpp'),
                       '-o', str(lib)], check=True, capture_output=True)
        cls.lib = C.CDLL(str(lib))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_constants(self):
        self.assertEqual(self.lib.fw_item(), 10)
        self.assertEqual(self.lib.fw_open_flags(), 0x8301)

    def test_paths(self):
        for path in (b'/yaffs2/xy-forever/', b'/yaffs2/xy-forever/../a', b'/yaffs2/xy-forever/./a',
                     b'/yaffs2/xy-forever//a', b'/yaffs2/xy-forever/a/..', b'/yaffs2/',
                     b'yaffs2/xy-forever/a', b'/yaffs2/xy-forever/' + b'a' * 240, b'/yaffs2/bootloader.dev',
                     b'/yaffs2/BootLoader.DEV', b'/yaffs2/upgrade', b'/yaffs2/upgrade/x.tfw', b'/yaffs2/UPGRADE/a/b',
                     b'/yaffs2/xy-forever/a\x01b', b'/yaffs2/xy-forever/a\tb', b'/yaffs2/xy-forever/a\x7f'):
            self.assertFalse(self.lib.fw_valid_path(path), path)
        # The write rule (since 1.13): the root, user/ and every other folder are writable.
        for path in (b'/yaffs2/firmware.version', b'/yaffs2/xy-forever', b'/yaffs2/xy-foreverx/a', b'/yaffs2/projects/x',
                     b'/yaffs2/user/song.xy', b'/yaffs2/upgrades/x', b'/yaffs2/a/upgrade/x', b'/yaffs2/bootloader.dev2'):
            self.assertTrue(self.lib.fw_valid_path(path), path)
        for path in (GOOD, b'/yaffs2/xy-forever/test.txt', b'/yaffs2/xy-forever/state-a.txt',
                     b'/yaffs2/xy-forever/' + b'a' * (239 - 19), b'/yaffs2/xy-forever/\xc3\xa9t\xc3\xa9.txt'):
            self.assertTrue(self.lib.fw_valid_path(path), path)
        self.assertFalse(self.lib.fw_valid_path(b'/yaffs2/xy-forever/' + b'a' * (240 - 19)))


class StockOpenFlagsTest(unittest.TestCase):
    """write_open_flags is what stock fopen("wb") (0803c928) passes to 889f3b50, and the
    provider open (889f1534) turns it into O_WRONLY|O_CREAT|O_TRUNC with mode 0x180."""

    def test_wb_flags_from_the_stock_mode_parser(self):
        table = [0, 0, 1, 2]                                  # 0x0803ca24 (checked below when the firmware is present)
        boot = STOCK_121 / 'bootloader.ldr'
        if boot.exists():
            from xy_forever.build_formats import Firmware
            table = list(struct.unpack('<4I', Firmware(boot.read_bytes()).read(0x0803ca24, 16)))
            self.assertEqual(table, [0, 0, 1, 2])
        stream = 0x1a | 0x20                                  # 'w' then 'b'
        flags = table[stream & 3]
        if stream & 0x4: flags |= 0x8
        if stream & 0x8: flags |= 0x200
        if stream & 0x10: flags |= 0x100
        flags |= 0x8000 if stream & 0x20 else 0x4000
        self.assertEqual(flags, 0x8301)
        # provider 889f1534: low bits 1 -> O_WRONLY, mode 0x180; bit 9 -> |0x240; bit 8 -> |0x40
        posix = 1 | 0x240 | 0x40
        self.assertEqual(posix, 0x241)


class FileWriteBuilderTest(unittest.TestCase):
    def test_wiring(self):
        self.assertEqual(builder.FILE_WRITE_MODES, builder.PRODUCT_MODES)
        self.assertFalse(hasattr(builder, 'FILE_WRITE_CALLS'))              # b's write goes through the client
        self.assertTrue(builder.FORBIDDEN_TARGETS.isdisjoint(builder.FILE_CLIENT_CALLS | {'889f6dca'}))
        covered = [(a, n) for a, n, _ in builder.mtp_spans()]
        self.assertTrue(any(a <= 0x889f6dca < a + n for a, n in covered))   # a's write executor


class FileWriteHostTest(unittest.TestCase):
    def setUp(self):
        import xyf_host
        self.host = xyf_host

    def test_dry_run_and_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'x.txt'; f.write_bytes(b'hello\n')
            self.assertEqual(self.host.main(['put-file', str(f), '/yaffs2/xy-forever/test.txt']), 0)   # dry run
            for bad in ('/yaffs2/test.txt', '/yaffs2/xy-forever/', '/yaffs2/xy-forever/../x', '/yaffs2/xy-forever/a b',
                        '/yaffs2/xy-forever/./x', '/yaffs2/xy-foreverx/a'):
                self.assertEqual(self.host.main(['put-file', str(f), bad, '--yes-write']), 2, bad)
            e = Path(tmp) / 'empty'; e.write_bytes(b'')
            self.assertEqual(self.host.main(['put-file', str(e), '/yaffs2/xy-forever/e', '--yes-write']), 2)

    def test_put_file_on_a_fake_device(self):
        from test_usb_service import FakeDevice
        host = self.host

        class Writer(FakeDevice):
            def __init__(self):
                super().__init__(); self.written = {}
            def handle(self, kind, payload):
                if kind == P.FILE_WRITE and self.greeted:
                    end = payload.index(b'\0')
                    path, sha = payload[:end].decode(), payload[end + 1:]
                    if len(sha) != 32 or sha != self.expected[2]:
                        return struct.pack('<III', 6, 0, 0)
                    self.written[path] = bytes(self.staged)
                    return struct.pack('<III', 0, len(self.staged), zlib.crc32(self.staged) & 0xffffffff)
                return super().handle(kind, payload)

        fake = Writer()
        def connect(timeout):
            dev = host.Device(fake); dev.hello()
            return dev, 'fake', 'XYF1'
        original = host.connect
        host.connect = connect
        try:
            with tempfile.TemporaryDirectory() as tmp:
                f = Path(tmp) / 'x.bin'; data = bytes(range(256)) * 9; f.write_bytes(data)
                self.assertEqual(host.main(['put-file', str(f), '/yaffs2/xy-forever/test.bin', '--yes-write']), 0)
                self.assertEqual(fake.written['/yaffs2/xy-forever/test.bin'], data)
        finally:
            host.connect = original


if __name__ == '__main__':
    unittest.main()


class LibraryAddTest(unittest.TestCase):
    """Host admission/refusal checks; atomic publication is tested in the simulator."""

    # The complete CLI/write path is exercised against the real simulator in
    # test_sim_library_add and test_sim_package_store (including power cuts).

    def test_refusals(self):
        import xyf_host as host
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp) / 'junk'; junk.write_bytes(b'not an ldr stream at all')
            self.assertEqual(host.main(['library-add', 'x', str(junk), '--name', 'a', '--version', 'b']), 2)
            self.assertEqual(host.main(['library-add', 'x', str(junk), '--name', 'a=b', '--version', 'b']), 2)
            self.assertEqual(host.main(['library-add', 'x', str(junk), '--name', 'a', '--version', 'b\nc']), 2)

    def test_content_sources_in_the_plan(self):
        """--content: a content folder or a content.sha256 + firmware.version folder; a release mismatch or a missing
        source is refused with how to give one; none at all is a note (the entry still boots)."""
        import contextlib
        import hashlib
        import io
        import xyf_host as host
        from test_ldr import image
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ldr = tmp / 'os.ldr'
            ldr.write_bytes(image([(4, 0x11a00000, 8, 0, bytes(range(8)))], entry=0x11a00000))
            content = tmp / 'pkg/firmware/blackfin/content'
            (content / 'presets').mkdir(parents=True)
            (content / 'presets/a.json').write_bytes(b'a')
            (content / 'op1_factory.json').write_bytes(b'{}')
            (tmp / 'pkg/firmware/firmware.version').write_bytes(b'1.2.1+0\n')
            listed = tmp / 'listed'
            listed.mkdir()
            (listed / 'content.sha256').write_bytes(b'%s  op1_factory.json\n' % hashlib.sha256(b'{}').hexdigest().encode())
            (listed / 'firmware.version').write_bytes(b'1.2.5+0\n')

            def plan(*extra):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = host.main(['library-add', 'x', str(ldr), '--name', 'a', '--version', '1.2.1', *extra])
                return code, out.getvalue()
            code, text = plan('--content', str(content))
            self.assertEqual(code, 0, text)
            self.assertIn('then firmware.version (1.2.1+0) and content.sha256 (2 files', text)
            code, text = plan('--content', str(listed))
            self.assertEqual(code, 2, text)
            self.assertIn('refused: --content is firmware 1.2.5+0, the entry is --version 1.2.1', text)
            code, text = plan('--content', str(tmp / 'absent'))
            self.assertEqual(code, 2, text)
            self.assertIn('not a folder', text)
            self.assertIn('Give --content DIR', text)
            code, text = plan()
            self.assertEqual(code, 0, text)
            self.assertIn('note: firmware/x gets no content.sha256 + firmware.version', text)


class ScreenTest(unittest.TestCase):
    """Test 053: the host's PNG conversion of the framebuffer view (SCREEN)."""

    def test_png_orientation_and_colours(self):
        import xyf_host as host
        raw = bytearray(host.SCREEN_BYTES)
        def put(x, y, v):
            struct.pack_into('<H', raw, ((479 - x) * 256 + y) * 2, v)
        put(0, 0, 0xffff); put(479, 215, 0xf800); put(10, 5, 0x07e0)
        png = host.screen_png(bytes(raw))
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        w, h = struct.unpack('>II', png[16:24])
        self.assertEqual((w, h), (480, 216))
        idat = png[png.index(b'IDAT') + 4:png.index(b'IEND') - 8]
        rows = zlib.decompress(idat)
        px = lambda x, y: rows[y * (1 + 480 * 3) + 1 + x * 3:y * (1 + 480 * 3) + 4 + x * 3]
        self.assertEqual(px(0, 0), b'\xff\xff\xff')
        self.assertEqual(px(479, 215), b'\xff\x00\x00')
        self.assertEqual(px(10, 5), b'\x00\xff\x00')
        self.assertEqual(px(1, 1), b'\x00\x00\x00')
