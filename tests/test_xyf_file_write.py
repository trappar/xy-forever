"""XY Forever USB file write (Test 049, --xyf-product): b's path scope, the stock "wb" open
flags, a's write entry into the file engine, the b client sequence, the host tool and builder
wiring. No device execution."""
import ctypes as C
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever.source_text import unit_text  # noqa: E402
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
        items = (ROOT / 'target/storage_server.hpp').read_text()
        for other in ('command_item = 3', 'install_item = 4, reboot_item = 5', 'engine_run_item = 6',
                      'usb_mode_item = 7', 'dry_run_item = 9', 'engine_leave_item = 11'):
            self.assertIn(other, items)                      # 10 collides with none of them
        self.assertNotIn('guard_item', items)                # 8 and 12 stay reserved (the removed guard)

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


class FileWriteSourceTest(unittest.TestCase):
    def setUp(self):
        self.server = (ROOT / 'target/storage_server.cpp').read_text()
        self.usb = unit_text(ROOT / 'target/usb_service.cpp')

    def test_a_enters_the_engine(self):
        latch = self.server[self.server.index('void latch(Stock121PeerSession& session) {'):]
        latch = latch[:latch.index('\n}\n')]
        self.assertIn('if (item == file_write_item && engine != entry_write) entry = entry_write;', latch)
        self.assertIn('if (!seen || seen == engine_latched || engine == entry_run) return;', latch)   # once per item
        self.assertIn('r.identity_mismatch = engine_identity();', latch)
        serve = self.server[self.server.index('void serve(void* context'):]
        serve = serve[:serve.index('\n}\n')]
        self.assertLess(serve.index('latch(session);'), serve.index('serve_install('))
        self.assertIn('engine != entry_write && engine != entry_run && serve_install(', serve)
        for name in ('bool open_window(', 'void close_engine('):
            body = self.server[self.server.index(name):]
            body = body[:body.index('\n}\n')]
            self.assertIn('_write_window(', body)
            self.assertNotIn('guard_', body)                                 # there is no guard to pause
        engine = self.server[self.server.index('void serve_engine('):]
        engine = engine[:engine.index('\n}\n')]
        self.assertIn('if (!window_open && engine == entry_write) engine = entry_read;   // Test 066: one write per item',
                      engine)
        self.assertIn('const bool ok = engine == entry_run ? open_window(storage) : mount(storage);', engine)
        # the write entry's first change opens the window, then the request is decided again.
        self.assertIn('if (action == file_engine::refuse_read_only && engine == entry_write && !state.window && '
                      'open_window(storage)) {', engine)
        self.assertIn('storage.open_write_window(step)', self.server)
        self.assertIn('window_result = U32(storage.close_write_window());', self.server)

    def test_b_client_sequence(self):
        """B's file client (file_client.cpp), every request bounded."""
        client = (ROOT / 'target/file_client.cpp').read_text()
        c = client[client.index('uint32_t write_file('):]
        c = c[:c.index('\n}\n')]
        order = ['if (mounted == mounted_stale) return xyf::usbp::s_state;', 'const Reply m = mount();',
                 'mkdir(folder);', 'open(path, storage_server::write_open_flags);', 'write(o.value, bytes + written, n);',
                 'close(o.value);', 'o = open(path, 0);', 'read(o.value, chunk_bytes, chunk);',
                 '(total != size || c != crc)) code = xyf::usbp::s_crc;']
        at = [c.index(s) for s in order]
        self.assertEqual(at, sorted(at))
        self.assertNotIn('unmount()', c)                 # the window stays open for the next write
        self.assertIn('const U32 root_bytes = storage_server::write_root(path);', c)   # 19 or 16
        self.assertEqual((len('/yaffs2/xy-forever/'), len('/yaffs2/content/')), (19, 16))
        self.assertIn("folder[k] = '/';", c)                                 # restored after each mkdir
        self.assertNotIn('guard_pause', client)
        # Each request: built into the session's slot, then waited for with a bound (one poll per
        # answer(); await_answer loops on it while busy, unmount_poll calls it once per pass of B's loop).
        answer = client[client.index('Reply answer(Stock121PeerSession& session'):]
        answer = answer[:answer.index('\n}\n')]
        self.assertIn('return outcome(xyf_boot::clock_ms() - start > bound ? link : busy);', answer)
        self.assertIn('if (!xyf_boot::link_step()) return outcome(lost);', answer)
        wait = client[client.index('Reply await_answer('):]
        self.assertIn('if (r.outcome != busy) return r;', wait[:wait.index('\n}\n')])
        for name in ('Reply send(Stock121PeerSession& session', 'Reply partition(U32 op'):
            body = client[client.index(name):]
            self.assertIn('await_answer(', body[:body.index('\n}\n')])
        self.assertIn('answer(*session, closing_start, bound_ms(9, 0))', client)

    def test_b_handler_order_and_the_staged_sha(self):
        h = self.usb[self.usb.index('void file_write(uint8_t seq'):]
        h = h[:h.index('\n}\n')]
        order = ['!storage_server::valid_write_path(path)', '!result.put_ok || !put_expected',
                 '!sha_equal(payload + path_bytes + 1, put_sha)', 'file_client::release(mounted)',
                 'menu_link::usb_command(storage_server::file_write_item)', 'storage_used = 1;',
                 'file_client::write_file(']
        at = [h.index(s) for s in order]
        self.assertEqual(at, sorted(at))
        self.assertNotIn('guard_', h)                                        # there is no guard
        self.assertIn('else if (type == P::t_file_write) file_write(seq, payload, length);', self.usb)
        self.assertIn('if (!size || size > image_capacity)', self.usb)

    def test_protocol_numbers(self):
        self.assertEqual(P.FILE_WRITE, 0x13)
        self.assertIn('t_file_write = 0x13,', (ROOT / 'target/usb_proto_ids.hpp').read_text())


class FileWriteBuilderTest(unittest.TestCase):
    def test_wiring(self):
        self.assertEqual(builder.FILE_WRITE_MODES, builder.PRODUCT_MODES)
        self.assertFalse(hasattr(builder, 'FILE_WRITE_CALLS'))              # b's write goes through the client
        self.assertTrue(builder.FORBIDDEN_TARGETS.isdisjoint(builder.FILE_CLIENT_CALLS | {'889f6dca'}))
        src = (ROOT / 'xyf_build.py').read_text()
        self.assertIn("if mode in FILE_WRITE_MODES:\n        flags = [*flags, '-DXYF_USB_FILE_WRITE=1']", src)
        self.assertIn("require(not has('serve_only')", src)
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
    """xyf_host library-add: os.ldr then info.txt, each on a fresh boot; default unchanged."""

    def test_flow_on_a_fake_device(self):
        import xyf_host as host
        from test_usb_service import FakeDevice
        calls, files, devices = [], {}, []

        class Booting(FakeDevice):
            def close(self): calls.append('close')
            def handle(self, kind, payload):
                if kind in (P.REBOOT, P.FILE_WRITE, P.SET_DEFAULT, P.CONTINUE):
                    calls.append(kind)
                if kind == P.FILE_WRITE and self.greeted:
                    end = payload.index(b'\0')
                    files[payload[:end].decode()] = bytes(self.staged)
                    return struct.pack('<III', 0, len(self.staged), zlib.crc32(self.staged) & 0xffffffff)
                return super().handle(kind, payload)

        def connect(timeout):
            fake = Booting(); devices.append(fake)
            dev = host.Device(fake); dev.hello()
            return dev, 'fake%d' % len(devices), 'XYF1'
        original, sleep = host.connect, host.time.sleep
        host.connect, host.time.sleep = connect, lambda s: None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ldr = Path(tmp) / 'os.ldr'
                from test_ldr import image
                data = image([(4, 0x11a00000, 8, 0, bytes(range(8)))], entry=0x11a00000)
                from xy_forever.ldr import parse_ldr
                self.assertEqual(parse_ldr(data)[-1].target, 0x11a00000)
                ldr.write_bytes(data)
                self.assertEqual(host.main(['library-add', 'opxy-xyz', str(ldr), '--name', 'Test image',
                                            '--version', '9.9.9+0', '--yes-write']), 0)
        finally:
            host.connect, host.time.sleep = original, sleep
        self.assertEqual(calls, [P.REBOOT, 'close', P.FILE_WRITE, P.REBOOT, 'close', P.FILE_WRITE, P.REBOOT, 'close'])
        self.assertEqual(files['/yaffs2/xy-forever/firmware/opxy-xyz/os.ldr'], data)
        info = files['/yaffs2/xy-forever/firmware/opxy-xyz/info.txt'].decode()
        self.assertEqual(info, 'name=Test image\nversion=9.9.9+0\nkind=official\nbytes=%d\ncrc32=%08x\n' %
                         (len(data), zlib.crc32(data) & 0xffffffff))
        self.assertNotIn(P.SET_DEFAULT, calls)                 # the default is never changed by library-add

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
    """Test 053: read-only framebuffer view (SCREEN) and the host PNG conversion."""

    def test_device_bounds(self):
        src = unit_text(ROOT / 'target/usb_service.cpp')
        s = src[src.index('void screen(uint8_t seq'):]
        s = s[:s.index('\n}\n')]
        self.assertIn('fb < 0x80000000u || fb > 0x90000000u - screen_bytes || (fb & 1u) || offset >= screen_bytes', s)
        self.assertIn('lines(fb + offset, n, true);', s)
        self.assertNotIn('stock<', s)                                  # memory reads only
        self.assertIn('else if (type == P::t_screen) screen(seq, payload, length);', src)
        hdr = (ROOT / 'target/usb_service.hpp').read_text()
        self.assertIn('screen_pointer = 0x8803dc58u, screen_bytes = 0x3c000u;', hdr)
        self.assertIn('const uint32_t framebuffer = 0x8803dc58u;', (ROOT / 'target/key_ui.cpp').read_text())
        self.assertEqual(P.SCREEN, 4)

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
