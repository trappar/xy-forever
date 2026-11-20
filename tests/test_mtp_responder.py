"""The MTP responder core (native/mtp_responder) against a scripted MTP client and a temp folder tree."""
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402

OK, GENERAL, NO_SESSION, NOT_SUPPORTED = 0x2001, 0x2002, 0x2003, 0x2005
INVALID_HANDLE, WRITE_PROTECTED, ACCESS_DENIED, PARTIAL_DELETION = 0x2009, 0x200D, 0x200F, 0x2012
NO_OBJECT_INFO, SESSION_OPEN, BUSY = 0x2015, 0x201E, 0x2019
STORAGE, ROOT_PARENT = 0x00010001, 0xFFFFFFFF
FOLDER, FILE = 0x3001, 0x3000


def ptp_string(text):
    if not text:
        return b"\0"
    units = text.encode("utf-16-le") + b"\0\0"
    return bytes([len(units) // 2]) + units


def read_string(data, at):
    count = data[at]
    if not count:
        return "", at + 1
    return data[at + 1:at + 1 + 2 * (count - 1)].decode("utf-16-le"), at + 1 + 2 * count


def object_info(name, size=0, fmt=FILE, parent=0):
    return (struct.pack("<IHHIHIIIIIIIHII", STORAGE, fmt, 0, size, 0, 0, 0, 0, 0, 0, 0, parent,
                        1 if fmt == FOLDER else 0, 0, 0) + ptp_string(name) + b"\0\0\0")


class Client:
    def __init__(self, bridge, folder):
        self.process = subprocess.Popen([str(bridge), str(folder)], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.tid = 0

    def close(self):
        self.process.stdin.close()
        self.process.wait(10)
        self.process.stdout.close()

    def call(self, command, payload=b""):
        self.process.stdin.write(command + struct.pack("<I", len(payload)) + payload)
        self.process.stdin.flush()
        size = struct.unpack("<I", self.process.stdout.read(4))[0]
        return self.process.stdout.read(size)

    def out(self, transfer):
        assert self.call(b"R", transfer) == b"\1", "the responder never took the transfer"

    def take(self, most=1 << 30):
        return self.call(b"I", struct.pack("<I", most))

    def status(self):
        return struct.unpack("<HIIIII", self.call(b"S"))   # device status, open files, handles, most calls/step,
        #                                                    cache hits, file calls in all

    def command(self, code, *params):
        self.tid += 1
        return struct.pack("<IHHI", 12 + 4 * len(params), 1, code, self.tid) + struct.pack("<%dI" % len(params), *params)

    def send_data(self, code, data, transfer=4096, stop=None):
        stream = struct.pack("<IHHI", 12 + len(data), 2, code, self.tid) + data
        for at in range(0, len(stream) if stop is None else stop, transfer):
            self.out(stream[at:at + transfer])

    def answers(self, stream):
        """[(type, code, tid, payload)] of the IN stream; it must end with a response."""
        containers, at = [], 0
        while at < len(stream):
            size, kind, code, tid = struct.unpack_from("<IHHI", stream, at)
            containers.append((kind, code, tid, stream[at + 12:at + size]))
            at += size
        assert at == len(stream) and containers and containers[-1][0] == 3, containers
        return containers

    def transaction(self, code, *params, data=None):
        """(response code, response params, data payload or None)."""
        self.out(self.command(code, *params))
        if data is not None:
            self.send_data(code, data)
        containers = self.answers(self.take())
        response = containers[-1]
        assert response[2] == self.tid, (response, self.tid)
        payload = containers[0][3] if len(containers) == 2 else None
        if payload is not None:
            assert containers[0][:3] == (2, code, self.tid)
        return response[1], struct.unpack("<%dI" % (len(response[3]) // 4), response[3]), payload

    def handles(self, parent=ROOT_PARENT):
        code, _, data = self.transaction(0x1007, STORAGE, 0, parent)
        assert code == OK, hex(code)
        count = struct.unpack_from("<I", data)[0]
        return list(struct.unpack_from("<%dI" % count, data, 4))

    def info(self, handle):
        code, _, data = self.transaction(0x1008, handle)
        if code != OK:
            return code
        fields = struct.unpack_from("<IHHIHIIIIIIIHII", data)
        name, _ = read_string(data, 52)
        return {"format": fields[1], "protection": fields[2], "size": fields[3], "parent": fields[11], "name": name}

    def named(self, parent=ROOT_PARENT):
        return {self.info(h)["name"]: h for h in self.handles(parent)}


class MtpResponderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="xyf-mtp-")
        cls.bridge = Path(cls.temporary.name) / "mtp_responder_bridge"
        hostbuild.run(["/usr/bin/clang++", "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror",
                       str(ROOT / "tests/mtp_responder_bridge.cpp"), str(ROOT / "native/mtp_responder.cpp"),
                       "-o", str(cls.bridge)], check=True, capture_output=True)
        cls.big = random.Random(35).randbytes(1 << 20)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(dir=self.temporary.name))
        (self.folder / "xy-forever").mkdir()
        (self.folder / "content").mkdir()
        (self.folder / "xy-forever/big.bin").write_bytes(self.big)
        (self.folder / "xy-forever/note.txt").write_bytes(b"hello")
        (self.folder / "bootloader.dev").write_bytes(b"dev")
        (self.folder / "readme.txt").write_bytes(b"in the root")
        (self.folder / "upgrade").mkdir()
        (self.folder / "upgrade/x.tfw").write_bytes(b"tfw")
        self.client = Client(self.bridge, self.folder)

    def tearDown(self):
        status = self.client.status()
        self.client.close()
        self.assertEqual(status[1], 0, "a file was left open")
        self.assertLessEqual(status[3], 1, "a step made more than one file call")

    def open(self):
        self.assertEqual(self.client.transaction(0x1002, 1)[0], OK)

    def test_device_info_session_and_storage(self):
        c = self.client
        code, _, data = c.transaction(0x1001)
        self.assertEqual(code, OK)
        self.assertEqual(struct.unpack_from("<HIH", data), (100, 6, 100))
        at = read_string(data, 8)[1] + 2
        count = struct.unpack_from("<I", data, at)[0]
        operations = set(struct.unpack_from("<%dH" % count, data, at + 4))
        self.assertTrue({0x1002, 0x1007, 0x1009, 0x100B, 0x100C, 0x100D, 0x101B} <= operations)
        self.assertEqual(c.transaction(0x1004)[0], NO_SESSION)
        self.open()
        self.assertEqual(c.transaction(0x1002, 2)[:2], (SESSION_OPEN, (1,)))
        code, _, data = c.transaction(0x1004)
        self.assertEqual((code, data), (OK, struct.pack("<II", 1, STORAGE)))
        code, _, data = c.transaction(0x1005, STORAGE)
        self.assertEqual(code, OK)
        self.assertEqual(struct.unpack_from("<HHHQQI", data), (3, 2, 0, 4096 * 65536, 4096 * 32768, 0xFFFFFFFF))
        self.assertEqual(c.transaction(0x1005, 7)[0], 0x2008)
        self.assertEqual(c.transaction(0x1014, 0x5001)[0], 0x200A)          # DeviceProp_Not_Supported
        code, _, data = c.transaction(0x1015, 0xD402)                       # the friendly name
        self.assertEqual((code, data), (OK, bytes([6]) + 'OP-XY\0'.encode('utf-16-le')))
        self.assertEqual(c.transaction(0x95C1, 1, 0, 0, 16)[0], NOT_SUPPORTED)   # Android GetPartialObject64
        self.assertEqual(c.transaction(0x1003)[0], OK)
        self.assertEqual(c.transaction(0x1007, STORAGE, 0, ROOT_PARENT)[0], NO_SESSION)

    def test_list_and_object_info(self):
        self.open()
        c = self.client
        root = c.named()
        self.assertEqual(set(root), {"xy-forever", "content", "bootloader.dev", "readme.txt", "upgrade"})
        self.assertEqual(c.named(), root, "a path keeps its handle")
        folder = c.info(root["xy-forever"])
        self.assertEqual((folder["format"], folder["parent"], folder["protection"]), (FOLDER, 0, 0))
        self.assertEqual(c.info(root["bootloader.dev"])["protection"], 1)       # the write rule: read only
        self.assertEqual(c.info(root["upgrade"])["protection"], 1)
        self.assertEqual(c.info(c.named(root["upgrade"])["x.tfw"])["protection"], 1)
        inside = c.named(root["xy-forever"])
        self.assertEqual(set(inside), {"big.bin", "note.txt"})
        note = c.info(inside["note.txt"])
        self.assertEqual((note["size"], note["parent"], note["protection"], note["format"]), (5, root["xy-forever"], 0, FILE))
        self.assertEqual(c.transaction(0x1007, STORAGE, FOLDER, ROOT_PARENT)[2][:4], struct.pack("<I", 3))
        self.assertEqual(c.transaction(0x1007, STORAGE, 0, inside["note.txt"])[0], 0x201A)
        self.assertEqual(c.info(999), INVALID_HANDLE)

    def test_object_info_from_the_listing(self):
        """Speed (2026-11-15): GetObjectInfo after a listing makes no file call; a change outside MTP (invalidate)
        makes the next one stat again; a relisting refreshes; a file gone since the listing is an invalid handle."""
        self.open()
        c = self.client
        root = c.named()                                   # the listing, then one GetObjectInfo per entry
        _, _, _, _, hits, calls = c.status()
        self.assertEqual(hits, len(root))
        for _ in range(3):
            self.assertEqual(c.info(root["readme.txt"])["size"], 11)
        self.assertEqual(c.status()[4:], (hits + 3, calls))
        (self.folder / "readme.txt").write_bytes(b"longer text now")
        self.assertEqual(c.info(root["readme.txt"])["size"], 11)          # cached: a change outside MTP not seen
        c.call(b"X")
        self.assertEqual(c.info(root["readme.txt"])["size"], 15)          # stat again
        self.assertEqual(c.status()[5], calls + 1)
        self.assertEqual(c.info(root["readme.txt"])["size"], 15)          # fresh again
        self.assertEqual(c.status()[5], calls + 1)
        (self.folder / "readme.txt").unlink()
        c.handles(ROOT_PARENT)                                            # relisted: no readme.txt
        self.assertEqual(c.info(root["readme.txt"]), INVALID_HANDLE)
        code, handle = self.upload(root["xy-forever"], "new.bin", b"12345")
        self.assertEqual(code, OK)
        before = c.status()[5]
        self.assertEqual(c.info(handle)["size"], 5)                     # our own upload: known
        self.assertEqual(c.status()[5], before)

    def test_download_one_mib_and_partial(self):
        self.open()
        c = self.client
        big = c.named(c.named()["xy-forever"])["big.bin"]
        code, _, data = c.transaction(0x1009, big)
        self.assertEqual(code, OK)
        self.assertEqual(data, self.big)
        code, params, data = c.transaction(0x101B, big, 1000, 5000)
        self.assertEqual((code, params, data), (OK, (5000,), self.big[1000:6000]))
        code, params, data = c.transaction(0x101B, big, len(self.big) - 10, 5000)
        self.assertEqual((code, params, data), (OK, (10,), self.big[-10:]))

    def test_4_kib_pieces_as_the_usb_driver_takes_them(self):
        """#144: with 4 KiB pieces (the default) a download fills the IN staging around the 12-byte header (the
        driver takes whole 512-byte packets only, so a read that waited for 4096 bytes of room would never start),
        every read and write call moves at most 4 KiB, and the bytes are the same."""
        self.open()
        c = self.client
        big = c.named(c.named()["xy-forever"])["big.bin"]
        calls = c.status()[5]
        c.out(c.command(0x1009, big))                                 # the default piece: 4 KiB
        containers = c.answers(c.call(b"J", struct.pack("<I", 1 << 30)))
        self.assertEqual((containers[0][3], containers[-1][1]), (self.big, OK))
        self.assertLess(c.status()[5] - calls, (1 << 20) // 4096 + 8)  # 4 KiB per read call, not 1 KiB
        for asked, piece in ((512, 1024), (5000, 4096), (4096, 4096)):
            self.assertEqual(struct.unpack("<I", c.call(b"Z", struct.pack("<I", asked)))[0], piece)
        c.out(c.command(0x101B, big, 1000, 9000))
        containers = c.answers(c.call(b"J", struct.pack("<I", 1 << 30)))
        self.assertEqual((containers[0][3], containers[-1][1]), (self.big[1000:10000], OK))
        body = random.Random(4).randbytes(50000)
        code, handle = self.upload(c.named()["xy-forever"], "up.bin", body)
        self.assertEqual(code, OK)
        self.assertEqual((self.folder / "xy-forever/up.bin").read_bytes(), body)

    def upload(self, parent, name, data, transfer=4096):
        c = self.client
        code, params, _ = c.transaction(0x100C, STORAGE, parent, data=object_info(name, len(data)))
        if code != OK:
            return code, None
        c.out(c.command(0x100D))
        c.send_data(0x100D, data, transfer)
        return c.answers(c.take())[-1][1], params[2]

    def test_upload_delete_and_folders(self):
        self.open()
        c = self.client
        root = c.named()
        data = random.Random(7).randbytes(300001)
        code, handle = self.upload(root["xy-forever"], "new.bin", data, transfer=512)
        self.assertEqual(code, OK)
        self.assertEqual((self.folder / "xy-forever/new.bin").read_bytes(), data)
        self.assertEqual(c.info(handle)["size"], len(data))
        self.assertIn("new.bin", c.named(root["xy-forever"]))
        self.assertEqual(self.upload(root["xy-forever"], "empty", b"")[0], OK)
        self.assertEqual((self.folder / "xy-forever/empty").read_bytes(), b"")
        self.assertEqual(c.transaction(0x100B, handle)[0], OK)
        self.assertFalse((self.folder / "xy-forever/new.bin").exists())
        self.assertEqual(c.info(handle), INVALID_HANDLE)
        # folders: create, fill, refuse to delete while not empty, empty it, delete it
        code, params, _ = c.transaction(0x100C, STORAGE, root["content"], data=object_info("kits", fmt=FOLDER))
        self.assertEqual(code, OK)
        kits = params[2]
        self.assertTrue((self.folder / "content/kits").is_dir())
        self.assertEqual(c.info(kits)["format"], FOLDER)
        code, inner = self.upload(kits, "kit 1.txt", b"abc")
        self.assertEqual(code, OK)
        self.assertEqual((self.folder / "content/kits/kit 1.txt").read_bytes(), b"abc")
        self.assertEqual(c.transaction(0x100B, kits)[0], PARTIAL_DELETION)
        self.assertEqual(c.transaction(0x100B, inner)[0], OK)
        self.assertEqual(c.transaction(0x100B, kits)[0], OK)
        self.assertFalse((self.folder / "content/kits").exists())
        self.assertEqual(c.transaction(0x100D, data=b"x")[0], NO_OBJECT_INFO)

    def test_refused_writes_outside_the_write_area(self):
        """The write rule (since 1.13): only the marker and /yaffs2/upgrade are read only (#65: and the Settings record)."""
        self.open()
        c = self.client
        root = c.named()
        inbox = c.named(root["upgrade"])
        before = sorted(os.listdir(self.folder))
        self.assertEqual(self.upload(ROOT_PARENT, "bootloader.dev", b"x")[0], ACCESS_DENIED)
        self.assertEqual(self.upload(ROOT_PARENT, "BootLoader.DEV", b"x")[0], ACCESS_DENIED)
        self.assertEqual(self.upload(root["upgrade"], "evil.tfw", b"x")[0], ACCESS_DENIED)
        self.assertEqual(c.transaction(0x100C, STORAGE, ROOT_PARENT, data=object_info("Upgrade", fmt=FOLDER))[0],
                         ACCESS_DENIED)
        self.assertEqual(c.transaction(0x100B, root["bootloader.dev"])[0], WRITE_PROTECTED)
        self.assertEqual(c.transaction(0x100B, root["upgrade"])[0], WRITE_PROTECTED)
        self.assertEqual(c.transaction(0x100B, inbox["x.tfw"])[0], WRITE_PROTECTED)
        self.assertEqual(c.transaction(0x9804, root["readme.txt"], 0xDC07, data=ptp_string("upgrade"))[0], ACCESS_DENIED)
        self.assertEqual(c.transaction(0x9804, root["readme.txt"], 0xDC07, data=ptp_string("bootloader.dev"))[0],
                         ACCESS_DENIED)
        self.assertEqual(c.transaction(0x9804, root["upgrade"], 0xDC07, data=ptp_string("u2"))[0], ACCESS_DENIED)
        # #65: the Settings record (Confirm on device) changes only on the device
        own = c.named(root["xy-forever"])
        self.assertEqual(self.upload(root["xy-forever"], "settings.txt", b"format=1\nconfirm=0\n")[0], ACCESS_DENIED)
        self.assertEqual(self.upload(root["xy-forever"], "Settings.OLD", b"format=1\nconfirm=0\n")[0], ACCESS_DENIED)
        self.assertEqual(c.transaction(0x9804, own["note.txt"], 0xDC07, data=ptp_string("settings.txt"))[0], ACCESS_DENIED)
        self.assertFalse((self.folder / "xy-forever/settings.txt").exists())
        self.assertEqual(sorted(os.listdir(self.folder)), before)
        self.assertEqual((self.folder / "bootloader.dev").read_bytes(), b"dev")
        self.assertEqual(os.listdir(self.folder / "upgrade"), ["x.tfw"])
        # everything else: the root, a name holding "bootloader", user/
        self.assertEqual(self.upload(ROOT_PARENT, "root.bin", b"r")[0], OK)
        self.assertEqual(self.upload(root["xy-forever"], "Bootloader.txt", b"b")[0], OK)
        code, params, _ = c.transaction(0x100C, STORAGE, ROOT_PARENT, data=object_info("user", fmt=FOLDER))
        self.assertEqual(code, OK)
        self.assertEqual(self.upload(params[2], "song.xy", b"s")[0], OK)
        self.assertEqual((self.folder / "user/song.xy").read_bytes(), b"s")
        self.assertEqual((self.folder / "root.bin").read_bytes(), b"r")
        self.assertEqual(c.transaction(0x100B, c.named()["readme.txt"])[0], OK)
        self.assertFalse((self.folder / "readme.txt").exists())

    def prop(self, handle, code):
        rc, _, data = self.client.transaction(0x9803, handle, code)
        return rc, data

    def test_object_properties(self):
        c = self.client
        code, _, data = c.transaction(0x1001)
        at = read_string(data, 8)[1] + 2
        count = struct.unpack_from("<I", data, at)[0]
        operations = set(struct.unpack_from("<%dH" % count, data, at + 4))
        self.assertTrue({0x9801, 0x9802, 0x9803, 0x9804} <= operations)
        self.assertNotIn(0x9805, operations)                 # answered for one object only: not listed
        self.assertNotIn(0x95C1, operations)                 # no Android partial-object operations
        self.assertNotIn("android.com", read_string(data, 8)[0])
        self.open()
        code, _, data = c.transaction(0x9801, FILE)
        props = struct.unpack_from("<%dH" % struct.unpack_from("<I", data)[0], data, 4)
        self.assertEqual(code, OK)
        self.assertEqual(set(props), {0xDC01, 0xDC02, 0xDC03, 0xDC04, 0xDC07, 0xDC09, 0xDC0B, 0xDC41, 0xDC44})
        code, _, data = c.transaction(0x9802, 0xDC04, FILE)
        self.assertEqual((code, data), (OK, struct.pack("<HHB", 0xDC04, 8, 0) + bytes(8) + struct.pack("<IB", 0, 0)))
        code, _, data = c.transaction(0x9802, 0xDC07, FILE)
        self.assertEqual((code, data), (OK, struct.pack("<HHB", 0xDC07, 0xFFFF, 1) + b"\0" + struct.pack("<IB", 0, 0)))
        self.assertEqual(c.transaction(0x9802, 0xDC99, FILE)[0], 0xA801)
        root = c.named()
        folder = c.named(root["xy-forever"])
        note, big = folder["note.txt"], folder["big.bin"]
        self.assertEqual(self.prop(big, 0xDC04), (OK, struct.pack("<Q", len(self.big))))
        self.assertEqual(self.prop(note, 0xDC07), (OK, ptp_string("note.txt")))
        self.assertEqual(self.prop(note, 0xDC44), (OK, ptp_string("note.txt")))
        self.assertEqual(self.prop(note, 0xDC01), (OK, struct.pack("<I", STORAGE)))
        self.assertEqual(self.prop(note, 0xDC02), (OK, struct.pack("<H", FILE)))
        self.assertEqual(self.prop(note, 0xDC03), (OK, struct.pack("<H", 0)))
        self.assertEqual(self.prop(note, 0xDC0B), (OK, struct.pack("<I", root["xy-forever"])))
        self.assertEqual(self.prop(note, 0xDC09), (OK, ptp_string("20250101T000000")))
        self.assertEqual(len(self.prop(note, 0xDC41)[1]), 16)
        self.assertEqual(self.prop(root["xy-forever"], 0xDC04), (OK, bytes(8)))
        self.assertEqual(self.prop(root["xy-forever"], 0xDC02), (OK, struct.pack("<H", FOLDER)))
        self.assertEqual(self.prop(root["readme.txt"], 0xDC03), (OK, struct.pack("<H", 0)))
        self.assertEqual(self.prop(root["bootloader.dev"], 0xDC03), (OK, struct.pack("<H", 1)))
        self.assertEqual(self.prop(note, 0xDC99)[0], 0xA801)
        self.assertEqual(self.prop(999, 0xDC04)[0], INVALID_HANDLE)
        # GetObjectPropList: one object at depth 0
        code, _, data = c.transaction(0x9805, note, 0, 0xDC04, 0, 0)
        self.assertEqual((code, data), (OK, struct.pack("<IIHHQ", 1, note, 0xDC04, 8, 5)))
        code, _, data = c.transaction(0x9805, note, 0, 0xFFFFFFFF, 0, 0)
        self.assertEqual((code, struct.unpack_from("<I", data)[0]), (OK, 9))
        self.assertEqual(c.transaction(0x9805, ROOT_PARENT, 0, 0xFFFFFFFF, 0, 0xFFFFFFFF)[0], 0xA808)
        # SetObjectPropValue: ObjectFileName = rename, inside the write area only
        code, _, _ = c.transaction(0x9804, note, 0xDC07, data=ptp_string("renamed.txt"))
        self.assertEqual(code, OK)
        self.assertFalse((self.folder / "xy-forever/note.txt").exists())
        self.assertEqual((self.folder / "xy-forever/renamed.txt").read_bytes(), b"hello")
        self.assertEqual(self.prop(note, 0xDC07), (OK, ptp_string("renamed.txt")))
        self.assertEqual(c.info(note)["name"], "renamed.txt")
        self.assertEqual(c.transaction(0x1009, note)[2], b"hello")
        self.assertEqual(c.transaction(0x9804, note, 0xDC07, data=ptp_string("renamed.txt"))[0], OK)   # same name
        self.assertEqual(c.transaction(0x9804, note, 0xDC07, data=ptp_string("big.bin"))[0], 0xA803)   # exists
        self.assertEqual(c.transaction(0x9804, note, 0xDC07, data=ptp_string("a/b"))[0], 0xA803)
        self.assertEqual(c.transaction(0x9804, note, 0xDC44, data=ptp_string("x"))[0], ACCESS_DENIED)
        self.assertEqual(c.transaction(0x9804, root["readme.txt"], 0xDC07, data=ptp_string("x.txt"))[0], OK)
        self.assertTrue((self.folder / "x.txt").exists())
        self.assertEqual(c.transaction(0x9804, root["bootloader.dev"], 0xDC07, data=ptp_string("b"))[0], ACCESS_DENIED)
        self.assertEqual((self.folder / "bootloader.dev").read_bytes(), b"dev")
        self.assertEqual(c.transaction(0x9804, 999, 0xDC07, data=ptp_string("x"))[0], INVALID_HANDLE)
        # a folder renamed: what is below keeps working
        code, params, _ = c.transaction(0x100C, STORAGE, root["content"], data=object_info("kits", fmt=FOLDER))
        kits = params[2]
        code, inner = self.upload(kits, "k.txt", b"abc")
        self.assertEqual(c.transaction(0x9804, kits, 0xDC07, data=ptp_string("kits2"))[0], OK)
        self.assertEqual((self.folder / "content/kits2/k.txt").read_bytes(), b"abc")
        self.assertEqual(c.transaction(0x1009, inner)[2], b"abc")
        self.assertEqual(set(c.named(root["content"])), {"kits2"})

    def test_malformed_containers(self):
        c = self.client
        c.out(b"\x07\0\0\0\1\0\x01")
        self.assertEqual(c.answers(c.take())[-1][1], GENERAL)
        c.out(struct.pack("<IHHI", 99, 1, 0x1002, 5) + struct.pack("<I", 1))   # length field differs
        self.assertEqual(c.answers(c.take())[-1][1:3], (GENERAL, 5))
        c.out(struct.pack("<IHHI", 14, 1, 0x1002, 6) + b"\1\0")              # not whole parameters
        self.assertEqual(c.answers(c.take())[-1][1], GENERAL)
        c.out(struct.pack("<IHHI", 20000, 2, 0x1018, 7) + bytes(4084))        # a data phase nobody asked for
        for size in (4096, 4096, 4096, 20000 - 4 * 4096):
            c.out(bytes(size))
        self.assertEqual(c.take(), b"")
        self.open()
        bad = object_info("x")[:20]                                            # a truncated ObjectInfo
        self.assertEqual(c.transaction(0x100C, STORAGE, c.named()["content"], data=bad)[0], 0x2023)
        self.assertEqual(c.transaction(0x1004)[0], OK)

    def test_cancelled_download_and_upload(self):
        self.open()
        c = self.client
        folder = c.named()["xy-forever"]
        big = c.named(folder)["big.bin"]
        c.out(c.command(0x1009, big))
        self.assertEqual(len(c.take(20000)), 20000)
        self.assertEqual(c.status()[1], 1)
        c.call(b"C")
        self.assertEqual(c.status()[0], BUSY)
        c.call(b"P")
        self.assertEqual(c.status()[:2], (OK, 0))
        self.assertEqual(c.take(), b"")
        self.assertEqual(c.transaction(0x1009, c.named(folder)["note.txt"])[2], b"hello")
        # an upload cancelled half way: the partial file is removed and its handle forgotten
        code, params, _ = c.transaction(0x100C, STORAGE, folder, data=object_info("half.bin", 100000))
        self.assertEqual(code, OK)
        c.out(c.command(0x100D))
        c.send_data(0x100D, bytes(100000), stop=3 * 4096)
        c.call(b"P")
        c.call(b"C")
        c.call(b"P")
        self.assertEqual(c.status()[:2], (OK, 0))
        self.assertFalse((self.folder / "xy-forever/half.bin").exists())
        self.assertEqual(c.info(params[2]), INVALID_HANDLE)
        self.assertEqual(self.upload(folder, "whole.bin", b"done")[0], OK)
        self.assertEqual(self.uploads(), [])

    def uploads(self):
        tmp = self.folder / "xy-forever/tmp"
        return sorted(p.name for p in tmp.iterdir()) if tmp.is_dir() else []

    def test_an_upload_replaces_in_one_step_or_leaves_the_old_file(self):
        """#4: SendObject writes tmp/put-<size>-<handle> and renames it onto the name when every announced byte
        came; a short transfer or a cancel leaves the old file (and its handle) and removes the upload."""
        self.open()
        c = self.client
        folder = c.named()["xy-forever"]
        note = self.folder / "xy-forever/note.txt"
        old = note.read_bytes()
        code, handle = self.upload(folder, "note.txt", b"new note\n" * 1000)
        self.assertEqual((code, note.read_bytes(), self.uploads()), (OK, b"new note\n" * 1000, []))
        self.assertEqual(c.info(handle)["size"], 9000)
        code, params, _ = c.transaction(0x100C, STORAGE, folder, data=object_info("note.txt", 5000))
        self.assertEqual(code, OK)
        c.out(c.command(0x100D))
        c.send_data(0x100D, bytes(4000))                                  # fewer bytes than announced
        self.assertEqual(c.answers(c.take())[-1][1], 0x2007)              # Incomplete_Transfer
        self.assertEqual((note.read_bytes(), self.uploads()), (b"new note\n" * 1000, []))
        self.assertEqual(c.info(params[2])["size"], 9000)                 # the old file, still listed
        code, params, _ = c.transaction(0x100C, STORAGE, folder, data=object_info("note.txt", 100000))
        c.out(c.command(0x100D))
        c.send_data(0x100D, bytes(100000), stop=3 * 4096)
        for op in (b"P", b"C", b"P"):
            c.call(op)
        self.assertEqual(c.status()[:2], (OK, 0))
        self.assertEqual((note.read_bytes(), self.uploads()), (b"new note\n" * 1000, []))
        self.assertNotEqual(old, note.read_bytes())


if __name__ == "__main__":
    unittest.main()
