"""tfw.check: the native verdict (native/tfw_check) on synthetic packages and cuts, the
official packages with the private keys, and the command on the simulator, whose boot-ROM stand-in
answers B's OTP reads with the private keys (xy_forever/sim.py otp_fields) or fails on request.

Synthetic packages use the public test AES vector and a synthetic P-224 key (test_tfw_os.py). The
private parts skip without ../private/keys and the official packages; nothing here prints a key.
"""
import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import p224_reference as ecdsa
from test_tfw import container, KEY
from test_tfw_os import (BOOTLOADER, Extractor, KEYS, OS, PUBLIC_KEY, SIGNING_KEY, build_zip, package, signed,
                         standard)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xyf_host as host                              # noqa: E402
from xy_forever import paths, sim, tfw_extract       # noqa: E402
from xy_forever import usb_proto_ids as ids          # noqa: E402

OFFICIAL = sorted((paths.FIRMWARE / 'official').glob('opxy_firmware_*.tfw'))
PRIVATE = None if tfw_extract.KEYS.is_file() and tfw_extract.PUBLIC_KEY.is_file() and OFFICIAL else \
    'private keys or official packages absent'
REASONS = ('pass', 'read', 'not_package', 'key_index', 'no_otp', 'public_key', 'signature', 'decrypt', 'directory')


class NativeCheck(Extractor):
    """The host build of the device's check (host/tfw_extract.cpp check)."""

    def check(self, data, *args, keys=KEYS, public_key=PUBLIC_KEY, name='check'):
        path = self.root / (name + '.bin')
        path.write_bytes(data)
        return tfw_extract.run(self.binary, 'check', path, *args, keys=keys, public_key=public_key)

    def reason(self, data, reason, **options):
        report = self.check(data, **options)
        self.assertEqual((report['status'], report['reason']), ('pass' if reason == 'pass' else 'fail', reason), report)
        return report

    def test_reason_codes_match_the_spec(self):
        import json
        spec = json.loads((ROOT / 'protocol/v1.json').read_text())
        command = next(c for c in spec['commands'] if c['name'] == 'tfw.check')
        reason = next(f for f in command['reply']['fields'] if f['name'] == 'reason')
        self.assertEqual(sorted(map(int, reason['values'])), list(range(len(REASONS))))
        enum = (ROOT / 'native/tfw_check.hpp').read_text().split('enum class Reason')[1].split('};')[0]
        self.assertEqual([line.split(',')[0].split()[0] for line in enum.splitlines()[1:]
                          if line.strip() and not line.strip().startswith('//')],
                         list(REASONS))

    def test_a_signed_package_passes_and_the_public_key_hash_is_reported(self):
        report = self.reason(signed(package()), 'pass')
        self.assertEqual(report['public_key_sha256'], hashlib.sha256(PUBLIC_KEY).hexdigest())
        self.assertEqual((report['public_key_calls'], report['key_calls'], report['entries']), (1, 1, 6))

    def test_keys_are_read_only_for_a_well_formed_header(self):
        for data in (b'', b'short', b'not a package at all, but longer than its header' * 30,
                     signed(package()) + b'x', signed(package())[:-1], bytes(0x380) + bytes(288)):
            with self.subTest(size=len(data)):
                report = self.reason(data, 'not_package')
                self.assertEqual((report['public_key_calls'], report['key_calls'], report['public_key_sha256']),
                                 (0, 0, ''))

    def test_the_device_public_key(self):
        good = signed(package())
        self.assertEqual(self.reason(good, 'no_otp', public_key=None)['key_calls'], 0)
        off_curve = PUBLIC_KEY[:-1] + bytes([PUBLIC_KEY[-1] ^ 1])
        report = self.reason(good, 'public_key', public_key=off_curve)
        self.assertEqual(report['public_key_sha256'], hashlib.sha256(off_curve).hexdigest())
        other = ecdsa.encode_point(ecdsa.public_key(SIGNING_KEY + 1))
        self.assertEqual(self.reason(good, 'signature', public_key=other)['key_calls'], 0)
        self.reason(package(), 'signature')                                        # r = s = 0
        changed = bytearray(good)
        changed[0x80 + 0x90] ^= 1                                                  # a signed field (version)
        self.reason(bytes(changed), 'signature')

    def test_key_indexes_other_than_zero_are_refused_unread(self):
        sig = bytearray(package())
        sig[0x80 + 0x89] = 1                        # signature key index: refused with the header
        report = self.reason(signed(bytes(sig)), 'not_package')
        self.assertEqual((report['public_key_calls'], report['key_calls']), (0, 0))
        payload, _, _ = build_zip(standard())
        report = self.reason(signed(container(payload, index=1)), 'key_index', keys=bytes([1]) + KEY)
        self.assertEqual((report['public_key_calls'], report['key_calls']), (1, 0))
        self.reason(signed(container(payload, index=0xfe)), 'key_index')

    def test_the_device_aes_key(self):
        good = signed(package())
        self.reason(good, 'no_otp', keys=b'')
        self.reason(good, 'decrypt', keys=bytes([0]) + bytes(32))
        self.reason(good, 'decrypt', keys=bytes([0, KEY[0] ^ 1]) + KEY[1:])

    def test_a_malformed_directory(self):
        no_os = [m for m in standard() if m['name'] != OS.encode()]
        two = standard() + [dict(standard()[2])]
        bad_record = [dict(m, flags=8) if m['name'] == BOOTLOADER.encode() else m for m in standard()]
        for name, members, options in (('no os.ldr', no_os, {}), ('os.ldr twice', two, {}),
                                       ('comment', standard(), {'comment': b'x'}), ('flags', bad_record, {}),
                                       ('count', standard(), {'eocd': {'here': 5, 'total': 5}})):
            with self.subTest(name):
                payload, _, _ = build_zip(members, **options)
                self.reason(signed(container(payload)), 'directory')

    def test_a_cut_is_checked_as_its_package_and_nothing_outside_it_is_read(self):
        good = signed(package())
        piece = tfw_extract.cut(good, tail=len(good) - 0x480 - 16)
        self.assertEqual(struct.unpack_from('<4I', piece)[:2], (tfw_extract.CUT_MAGIC, len(good)))
        report = self.reason(piece, 'pass')
        self.assertEqual(report['public_key_sha256'], hashlib.sha256(PUBLIC_KEY).hexdigest())
        # The whole package, read only where a cut holds it: header, name block, then the tail.
        _, _, directory = build_zip(standard())
        start = (0x480 + directory) & ~15
        allow = ('--allow', '0:%d' % 0x480, '--allow', '%d:%d' % (start - 16, len(good) + 16))   # + the end probe
        report = self.check(good, *allow, name='allowed')
        self.assertEqual((report['reason'], report['refused_reads']), ('pass', 0), report)
        short_tail = tfw_extract.cut(good, tail=64)                 # the directory is not in it
        self.reason(short_tail, 'read')
        wrong = bytearray(piece)
        struct.pack_into('<I', wrong, 4, len(good) + 16)            # its sizes disagree
        self.reason(bytes(wrong), 'not_package')
        self.reason(piece + b'x', 'not_package')


@unittest.skipIf(PRIVATE, PRIVATE)
class OfficialPackages(Extractor):
    """The three official packages (about 98 MB each; only the header and the directory are read)."""

    def run_check(self, path, keys=None, public_key=None):
        return tfw_extract.run(self.binary, 'check', path, keys=tfw_extract.key_records() if keys is None else keys,
                               public_key=tfw_extract.public_key() if public_key is None else public_key)

    def test_each_official_package_and_its_cut_pass(self):
        expected = hashlib.sha256(tfw_extract.public_key()).hexdigest()
        for path in OFFICIAL:
            with self.subTest(package=path.name):
                report = self.run_check(path)
                self.assertEqual((report['reason'], report['public_key_sha256']), ('pass', expected))
                self.assertLess(report['reads'], 10000)
                piece = self.root / (path.name + '.cut')
                piece.write_bytes(tfw_extract.cut(path))
                self.assertEqual(self.run_check(piece)['reason'], 'pass')

    def test_a_corrupted_header_and_a_wrong_key_fail(self):
        piece = bytearray(tfw_extract.cut(OFFICIAL[-1]))
        piece[16 + 0x80 + 0x9c] ^= 1                              # the signed ciphertext hash
        path = self.root / 'corrupt.cut'
        path.write_bytes(bytes(piece))
        self.assertEqual(self.run_check(path)['reason'], 'signature')
        path.write_bytes(tfw_extract.cut(OFFICIAL[-1]))
        self.assertEqual(self.run_check(path, keys=bytes([0]) + bytes(32))['reason'], 'decrypt')


SIM_UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
DIR = '/yaffs2/xy-forever/packages/'


@unittest.skipIf(SIM_UNAVAILABLE, SIM_UNAVAILABLE)
class SimulatorCheck(unittest.TestCase):
    """tfw.check through the real B code on the simulator. The official packages are hard-linked
    into the simulated storage (a copy where a link is impossible) and removed with it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-sim-tfw-', dir=str(paths.SESSIONS))
        cls.addClassCleanup(cls.tmp.cleanup)
        root = sim.seed(Path(cls.tmp.name) / 'yaffs2')
        folder = root / 'xy-forever' / 'packages'
        folder.mkdir()
        (folder / 'note.txt').write_bytes(b'not a package\n' * 100)
        if not PRIVATE:
            for path in OFFICIAL:
                try:
                    os.link(path, folder / path.name)
                except OSError:
                    (folder / path.name).write_bytes(path.read_bytes())
            last = OFFICIAL[-1]
            (folder / 'good.cut').write_bytes(tfw_extract.cut(last))
            corrupt = bytearray(tfw_extract.cut(last))
            corrupt[16 + 0x80 + 0x9c] ^= 1
            (folder / 'corrupt.cut').write_bytes(bytes(corrupt))
        cls.otp = None if PRIVATE else sim.otp_fields()
        cls.sim = sim.Simulator(root, Path(cls.tmp.name) / 'bootpart.bin', otp=cls.otp,
                                log=Path(cls.tmp.name) / 'log.txt').start()
        cls.addClassCleanup(cls.sim.stop)

    def setUp(self):
        # A new connection per test: the probe script's own connections replace any older one.
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.addCleanup(self.dev.t.close)
        self.assertEqual(self.dev.hello()[0], 0)

    def tearDown(self):
        self.sim.set('otp_fail', 0)

    def check(self, name, fail=0):
        self.sim.set('otp_fail', fail)
        before = self.sim.status()['otp_reads']
        code, body = self.dev.call(ids.TFW_CHECK, (DIR + name).encode() + b'\0', timeout=60.0)
        self.assertEqual((code, len(body)), (0, 40))
        verdict, reason = struct.unpack_from('<II', body)
        return verdict, REASONS[reason], body[8:], self.sim.status()['otp_reads'] - before

    def test_not_a_package_reads_no_key_and_errors_are_statuses(self):
        self.assertEqual(self.check('note.txt'), (1, 'not_package', bytes(32), 0))
        for path, status in ((DIR + 'missing.tfw', 'not_found'), ('/fat32/x.tfw', 'bad_request')):
            code, body = self.dev.call(ids.TFW_CHECK, path.encode() + b'\0')
            self.assertEqual((ids.STATUS_NAMES[code], body), (status, b''))
        code, _ = self.dev.call(ids.TFW_CHECK, DIR.encode() + b'note.txt')            # no NUL
        self.assertEqual(ids.STATUS_NAMES[code], 'bad_request')

    @unittest.skipIf(PRIVATE, PRIVATE)
    def test_official_packages_pass_with_the_otp_keys(self):
        expected = hashlib.sha256(tfw_extract.public_key()).digest()
        for path in OFFICIAL + [Path('good.cut')]:
            with self.subTest(package=path.name):
                self.assertEqual(self.check(path.name), (0, 'pass', expected, 3))   # selectors 9, 3, 4

    @unittest.skipIf(PRIVATE, PRIVATE)
    def test_each_failure_reason(self):
        expected = hashlib.sha256(tfw_extract.public_key()).digest()
        self.assertEqual(self.check('corrupt.cut')[:3], (1, 'signature', expected))
        self.assertEqual(self.check('good.cut', fail=1), (1, 'no_otp', bytes(32), 1))
        verdict, reason, digest, _ = self.check('good.cut', fail=2)
        self.assertEqual((verdict, reason), (1, 'public_key'))
        self.assertNotIn(digest, (expected, bytes(32)))
        self.assertEqual(self.check('good.cut', fail=3)[:2], (1, 'signature'))
        self.assertEqual(self.check('good.cut', fail=4), (1, 'decrypt', expected, 3))
        self.assertEqual(self.check('good.cut')[:2], (0, 'pass'))

    @unittest.skipIf(PRIVATE, PRIVATE)
    def test_the_device_probe_script(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        import tfw_probe
        with tempfile.TemporaryDirectory(dir=str(paths.SESSIONS)) as sessions:
            import contextlib
            import io
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = tfw_probe.main(['--sim', self.sim.address], sessions=sessions)
            self.assertEqual(code, 0, out.getvalue())
            self.assertRegex(out.getvalue(), r'probe/otp-1_2_5 +PASS')
            self.assertEqual(list(Path(sessions).iterdir()), [])          # a passing simulator run leaves nothing
            self.sim.set('otp_fail', 1)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = tfw_probe.main(['--sim', self.sim.address, '--packages', 'opxy_firmware_1_2_5.tfw'],
                                      sessions=sessions)
            self.assertEqual(code, 1)
            self.assertIn('fail (no_otp)', out.getvalue())
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = tfw_probe.main(['--sim', '127.0.0.1:1', '--wait', '0.5'], sessions=sessions)
            self.assertEqual(code, 3)
        host.SIM = None

    def test_no_otp_without_keys(self):
        if self.otp:
            self.skipTest('the simulator has the private keys')
        status = self.sim.status()
        self.assertEqual(status['otp_fields'], 0)


import xyf_build as builder                          # noqa: E402


def line(address, code):
    return '%08x:\t00 00       \t%s;\t\t/* comment */' % (address, code)


class BuilderRules(unittest.TestCase):
    """xyf_build.py's OTP and boot-ROM rules on synthetic listings (the real one: every product build)."""
    WRAPPER_READ = '__ZN10xy_forever8otp_keys12_GLOBAL__N_15stockILj67108936EjJjPhEEET0_DpT1_'
    WRAPPER_INIT = '__ZN10xy_forever8otp_keys12_GLOBAL__N_15stockILj67108948EvJjEEET0_DpT1_'

    def listing(self, read_owner=None, caller='__ZN12_GLOBAL__N_113tfw_check_runEPKcPh', extra=()):
        read_owner = read_owner or self.WRAPPER_READ
        parts = [
            '8ab0f76c <%s>:' % self.WRAPPER_INIT,
            line(0x8ab0f76c, 'P2.H = 0x400'), line(0x8ab0f770, 'R0 = 0x0 (X)'), line(0x8ab0f772, 'P2.L = 0x54'),
            line(0x8ab0f776, 'JUMP (P2)'), '',
            '8ab0f778 <%s>:' % read_owner,
            line(0x8ab0f778, 'P2.L = 0x48'), line(0x8ab0f77c, 'P2.H = 0x400'), line(0x8ab0f780, 'JUMP (P2)'), '',
            '8ab0f784 <__ZN10xy_forever8otp_keys12_GLOBAL__N_17prepareEv>:',
            line(0x8ab0f784, 'P1.H = 0x11b0'), line(0x8ab0f788, 'P1.L = 0x1650'),
            line(0x8ab0f78c, 'CALL 0x8ab0f76c <%s>' % self.WRAPPER_INIT), '',
            '8ab0f7ac <__ZN10xy_forever8otp_keys10public_keyEPvPh>:',
            line(0x8ab0f7ac, 'CALL 0x8ab0f778 <%s>' % read_owner), '',
            '8ab0f900 <%s>:' % caller,
            line(0x8ab0f900, 'R1.H = 0x8ab0'),
            '8ab0f904:\t00 00       \tR1.L = 0xf7ac;\t\t/* R1=0x8ab0f7ac <__ZN10xy_forever8otp_keys10public_keyEPvPh> */',
            *extra]
        return '\n'.join(parts) + '\n'

    def symbols(self, listing):
        return {m: {} for m in __import__('re').findall(r'<([^>+]+)>:', listing)}

    def test_halves_in_either_order_build_a_constant(self):
        lines = [line(0, 'P2.H = 0x400'), line(2, 'P2.L = 0x48'), line(4, 'R0.L = 0x54'), line(6, 'R1 = R0'),
                 line(8, 'R0.H = 0x400'), line(10, 'P0.L = 0x50'), line(12, 'P0 = [P1]'), line(14, 'P0.H = 0x400')]
        self.assertEqual(builder.loaded_values(lines), {0x04000048, 0x04000054})
        stale = [line(0, 'P2.H = 0x8ab0'), line(2, 'P2.L = 0xb764'), line(4, 'IF CC JUMP 0x8ab0015c'),
                 line(6, 'P2.H = 0x402'), line(8, 'P2.L = 0x1c4'), line(10, 'CALL (P2)')]
        self.assertEqual(builder.loaded_values(stale), {0x8ab0b764, 0x040201c4})   # not 0x0402b764
        listing = self.listing(extra=[line(0x8ab0f908, 'P3.L = 0x4c'), line(0x8ab0f90c, 'P3.H = 0x400')])
        self.assertEqual(builder.rom_references(listing), {'04000048', '04000054', '0400004c'})

    def test_the_rom_and_forbidden_lists(self):
        self.assertEqual(builder.ROM_TARGETS, {'040201c4', '04000048', '04000054'})
        self.assertEqual(builder.OTP_CALLS, {'04000048', '04000054'})
        for entry in ('88a25d28', '88a25c6c', '88a2901a', '88a28cfc', '88a27d00', '88a196cc', '88a18cae', '88a1871c',
                      '88a14820', '11a05340', '88a21456', '88a34ed0', '0400004c', '04000050'):
            self.assertIn(entry, builder.FORBIDDEN_TARGETS)
        self.assertFalse(builder.ROM_TARGETS & builder.FORBIDDEN_TARGETS)
        self.assertEqual(builder.TFW_CHECK_MODES, ('xyf-product',))
        self.assertIn('XYF_TFW_CHECK', builder.usb_switches('xyf-product'))
        self.assertNotIn('XYF_TFW_CHECK', builder.usb_switches('xyf-stage2'))

    def test_otp_rules(self):
        good = self.listing()
        builder.check_otp_rules(good, self.symbols(good), True)
        with self.assertRaisesRegex(ValueError, 'without XYF_TFW_CHECK'):
            builder.check_otp_rules(good, self.symbols(good), False)
        stray = self.listing(read_owner='__ZN12_GLOBAL__N_15stockILj67108936EjJjPhEEET0_DpT1_')
        with self.assertRaisesRegex(ValueError, 'loaded outside otp_keys'):
            builder.check_otp_rules(stray, self.symbols(stray), True)
        other = self.listing(caller='__ZN12_GLOBAL__N_19read_fileEhPKhj')
        with self.assertRaisesRegex(ValueError, 'outside the tfw.check handler'):
            builder.check_otp_rules(other, self.symbols(other), True)
        twice = self.listing(extra=[line(0x8ab0f908, 'P3.L = 0x48'), line(0x8ab0f90c, 'P3.H = 0x400')])
        with self.assertRaisesRegex(ValueError, 'loaded by 2 functions'):
            builder.check_otp_rules(twice, self.symbols(twice), True)
        plain = '8ab00000 <__ZN12_GLOBAL__N_19read_fileEhPKhj>:\n' + line(0x8ab00000, 'RTS') + '\n'
        builder.check_otp_rules(plain, self.symbols(plain), False)

    def test_sources(self):
        keys = (ROOT / 'target/otp_keys.cpp').read_text()
        self.assertIn('#if defined(__bfin__) && defined(XYF_TFW_CHECK)', keys)
        self.assertEqual(keys.count('stock<0x04000054u, void>(0u)'), 1)
        self.assertEqual(keys.count('stock<0x04000048u, U32>(selector, buffer)'), 1)
        self.assertNotRegex(keys, r'0x0400004c|0x04000050|selector, 5|5u, scratch|6u, scratch')
        for path in [*ROOT.glob('target/*.?pp'), *ROOT.glob('native/*.?pp')]:
            if path.name not in ('otp_keys.cpp', 'boot_services.cpp'):
                self.assertNotIn('0x04000048', path.read_text(), path.name)
                self.assertNotIn('0x04000054', path.read_text(), path.name)
        service = (ROOT / 'target/usb_service.cpp').read_text()
        self.assertIn('#if defined(XYF_TFW_CHECK)\n        else if (type == P::t_tfw_check) tfw_check(seq, payload, length);',
                      service)
