"""Stage 2 in the product (target/xyf_stage2.hpp ABI 3) in the device simulator: B's USB
service loads /yaffs2/xy-forever/stage2/main.xys2 at the first HELLO, checks it and runs stage 2's
code (target/stage2_main.cpp, built for the Mac, through the same services table), which answers
stage2.info. Every failure leaves stage 1 as before. Simulator clock only; no device access."""
import hashlib
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xyf_build                                    # noqa: E402
import xyf_host as host                             # noqa: E402
from xy_forever import sim, usb_proto as P, xyf_stage2 as S2   # noqa: E402
from xy_forever import usb_proto_ids as ids         # noqa: E402

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
STAGE1 = xyf_build.release.code()                  # this stage 1's release (XYF_VERSION_CODE)
SWITCHES = xyf_build.usb_switches('xyf-product')
IMAGE = bytes(range(256)) * 2                       # not run here: the host build of stage 2 is
CHECK_MAGIC, CHECK_SIZE, CHECK_STAGE1, CHECK_SHA = 2, 1, 9, 11    # xyf::stage2::Check


def stage2_file(version=7, min_stage1=STAGE1, image=IMAGE):
    return S2.pack(image, S2.WINDOW[0], S2.WINDOW[0], version=version, build_id='test', min_stage1=min_stage1,
                   abi=S2.ABI_SERVICE)


def reheader(data, offset, value):
    """data with the u32 at offset set to value and the header CRC made right again."""
    head = bytearray(data[:S2.HEADER_BYTES])
    struct.pack_into('<I', head, offset, value)
    struct.pack_into('<I', head, 92, zlib.crc32(bytes(head[:92])))
    return bytes(head) + data[S2.HEADER_BYTES:]


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class Stage2ServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-s2-')
        cls.out = sim.build()
        cls.seeded = sim.seed(Path(cls.tmp.name) / 'seed')
        cls.runs = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def start(self, data=None, play=True):
        type(self).runs += 1
        self.dir = Path(self.tmp.name) / ('run-%d' % self.runs)
        shutil.copytree(self.seeded, self.dir / 'yaffs2')
        if data is not None:
            (self.dir / 'yaffs2/xy-forever/stage2').mkdir(parents=True)
            (self.dir / 'yaffs2/xy-forever/stage2/main.xys2').write_bytes(data)
        self.sim = sim.Simulator(self.dir / 'yaffs2', self.dir / 'bootpart.bin', build_dir=self.out, play=play,
                                 log=self.dir / 'log.txt').start()
        self.addCleanup(self.sim.stop)

    def connect(self):
        dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.addCleanup(dev.t.close)
        return dev

    def record(self, dev):
        code, body = dev.call(P.STATUS)
        self.assertEqual(code, 0)
        return host.decode_status(body)['usb_service']['stage2']

    def info(self, dev, payload=b''):
        dev.hello_info = None                          # xyf_host refuses types missing from cmds
        return dev.call(ids.STAGE2_INFO, payload)

    def refused(self, data, status, detail=None, opens=2):
        """Stage 2 not loaded: HELLO stage2=0 and the build's own cmds, stage2.info unsupported,
        STATUS says why, and every other command still answers."""
        self.start(data)
        dev = self.connect()
        code, text = dev.hello()
        self.assertEqual((code, text), (0, P.hello_text(xyf_build.release.VERSION, None, 'product', SWITCHES, confirm=0)))
        self.assertEqual(self.info(dev), (ids.S_UNSUPPORTED, b''))
        record = self.record(dev)
        self.assertEqual(record['status'], status, record)
        if detail is not None:
            self.assertEqual(record['detail'], detail, record)
        self.assertEqual(self.sim.status()['stage2_opens'], opens)
        self.assertEqual(self.sim.status()['stage2_calls'], 0)
        self.assertEqual(dev.read_file('/yaffs2/xy-forever/state-a.txt')[0], 0)
        return dev, record

    def test_loaded_and_answering(self):
        data = stage2_file()
        self.start(data)
        dev = self.connect()
        code, text = dev.hello()
        self.assertEqual((code, text), (0, P.hello_text(xyf_build.release.VERSION, None, 'product', SWITCHES, stage2=7, confirm=0)))
        self.assertTrue(P.Hello(text).answers(ids.STAGE2_INFO) and text.endswith(';stage2=7'))
        turns = []
        for _ in range(2):
            code, body = dev.call(ids.STAGE2_INFO)
            self.assertEqual((code, len(body)), (0, 20))
            version, prefix, services, seen = struct.unpack('<I8sII', body)
            self.assertEqual((version, prefix, services), (7, hashlib.sha256(IMAGE).digest()[:8], 1))
            turns.append(seen)
        self.assertGreater(turns[1], turns[0])
        self.assertEqual(dev.call(ids.STAGE2_INFO, b'\0')[0], ids.S_BAD_REQUEST)     # stage 2's own check
        record = self.record(dev)
        self.assertEqual({k: record[k] for k in ('status', 'version', 'bytes', 'trace_code', 'trace_value', 'reads')},
                         dict(status='loaded', version=7, bytes=len(IMAGE), trace_code=1, trace_value=7, reads=1))
        self.assertEqual(self.sim.status()['stage2_opens'], 2)                     # header, then the file
        self.assertEqual(dev.hello(), (0, text))                                    # no second load
        self.assertEqual(self.sim.status()['stage2_opens'], 2)
        self.assertEqual(dev.read_file('/yaffs2/xy-forever/state-a.txt')[0], 0)    # stage 1 as before

    def test_file_missing(self):
        self.refused(None, 'missing', ids.S_NOT_FOUND, opens=1)

    def test_header_corrupt(self):
        data = bytearray(stage2_file())
        data[0] ^= 0xff                                                              # magic
        self.refused(bytes(data), 'check', CHECK_MAGIC)

    def test_hash_mismatch(self):
        data = bytearray(stage2_file())
        data[32] ^= 1                                                                # image_sha256[0]
        struct.pack_into('<I', data, 92, zlib.crc32(bytes(data[:92])))
        self.refused(bytes(data), 'check', CHECK_SHA)

    def test_file_too_large(self):
        self.refused(stage2_file() + b'\0', 'read', ids.S_TOO_LARGE)                 # longer than its header says
        self.sim.stop()
        too_big = reheader(stage2_file(), 24, S2.WINDOW[1] - S2.WINDOW[0] + 4)       # image_bytes above the window
        self.refused(too_big, 'check', CHECK_SIZE, opens=1)

    def test_min_stage1_newer_than_the_release(self):
        self.refused(stage2_file(min_stage1=STAGE1 + 1), 'check', CHECK_STAGE1)     # for a later stage 1

    def test_a_file_for_an_old_build_numbered_stage1_still_loads(self):
        self.assertGreater(STAGE1, 195)                 # every build number lies below 0.1.0's code
        self.assertEqual(xyf_build.STAGE2_SERVICE_MIN_STAGE1, 0x100)

    def test_link_error_while_loading(self):
        self.start(stage2_file())
        dev = self.connect()
        self.sim.set('cut_body', 200)                  # the whole-file read's body is cut, then the link drops
        reply = {}
        thread = threading.Thread(target=lambda: reply.update(r=dev.call(P.HELLO, b'', timeout=30.0)), daemon=True)
        thread.start()
        deadline = time.monotonic() + 30.0
        while self.sim.status()['cut_body']:                 # the cut happened: B waits on the rest
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)
        for _ in range(5):                                   # past the request bound, on the simulator clock
            self.sim.advance(6000)                           # (again if B took HELLO only after the first)
            thread.join(0.2)
            if 'r' in reply:
                break
        thread.join(30.0)
        code, body = reply['r']
        self.assertEqual((code, body.decode()), (0, P.hello_text(xyf_build.release.VERSION, None, 'product', SWITCHES, confirm=0)))
        self.assertEqual(self.info(dev), (ids.S_UNSUPPORTED, b''))
        record = self.record(dev)
        self.assertEqual((record['status'], record['detail']), ('link', ids.S_LINK), record)
        self.assertEqual(self.sim.status()['stage2_calls'], 0)

    def until(self, predicate, what):
        deadline = time.monotonic() + 60                 # host time bounds a hang only
        while time.monotonic() < deadline:
            s = self.sim.status()
            if predicate(s):
                return s
            time.sleep(0.01)
        self.fail('%s not reached: %s' % (what, self.sim.status()))

    def test_plain_boot_never_reads_the_file(self):
        self.start(stage2_file())                      # the menu with USB, no HELLO yet: nothing read
        self.assertEqual(self.sim.status()['stage2_opens'], 0)
        self.sim.control('power plain')                # no PLAY: straight to the default, no USB service
        s = self.until(lambda s: s['power_ons'] == 2 and s['a'] == 'os' and s['b'] == 'os', 'plain boot')
        self.assertEqual((s['stage2_opens'], s['stage2_calls']), (0, 0))
        self.sim.control('power play')
        self.until(lambda s: s['listening'], 'menu with USB')
        self.assertEqual(self.sim.status()['stage2_opens'], 0)
        dev = self.connect()
        self.assertTrue(dev.hello()[1].endswith(';stage2=7'))
        self.assertEqual(self.sim.status()['stage2_opens'], 2)


if __name__ == '__main__':
    unittest.main()
