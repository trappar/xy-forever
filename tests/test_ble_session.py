"""Bluetooth (Nordic) application update session (1.2.1 bootloader).

The native session and the Python model run in lockstep against a host model of
the chip (NordicChip). Synthetic images only; the official images' hashes come
from a private fixture at run time and that test skips without it.
"""

import json
import random
import struct
import unittest
from hashlib import sha256

import pytest
import xyf_hosttest
from test_companions import cyacd, enter_response, response

from xy_forever import paths
from xy_forever.companions import (
    BLE_DELAY,
    BLE_EXCHANGE,
    BLE_LISTEN,
    BLE_PHASES,
    BLE_WRITE,
    BleSession,
    CompanionError,
    CyacdImage,
    CypressSession,
    crc16_ccitt,
    cypress_packet,
)

TERMINAL = ('complete', 'aborted', 'failed')
OLD, NEW = (1, 0, 1, 0), (1, 0, 2, 0)
FIXTURE = paths.FIXTURES / 'ble-images.json'


def image(size=0x304, seed=0):
    """Synthetic application: plausible vectors, deterministic body."""
    return bytes.fromhex('d02f0120d1900100') + bytes((i * 31 + seed) & 255 for i in range(size - 8))


def version_text(version):
    return '%d.%d.%d+%d' % tuple(version)


class NordicChip:
    """Host model of the Nordic chip; native/ble_session.hpp is the contract.

    mode: "app" (application running), "loop" (bootloader serial loop),
    "session" (bootloader update session), "off" (System OFF: in-session 0x18
    with an invalid application; no proven wake) or "jumped" (pre-session 0x18
    into an invalid application; outcome unknown). Every reset ends in "loop".
    """

    def __init__(self, mode='app', version=OLD, valid=True, reports=None):
        self.mode, self.valid, self.version = mode, valid, version if valid else None
        self.reports = reports  # the running application's own sw_version, if it differs
        self.image = None
        self.started, self.header, self.received = False, None, bytearray()
        self.hazards = []

    def sw_version(self):
        if self.mode == 'app' and self.reports:
            return version_text(self.reports)
        return version_text(self.version) if self.valid else '0.0.0+0'

    def metadata(self):
        if self.mode == 'app':
            return b'mode:normal;product:OP-XY;sw_version:' + self.sw_version().encode() + b';mac:00'
        return (
            b'mode:bootloader;product:OP-XY;sw_version:'
            + self.sw_version().encode()
            + b';bl_version:1000.0.10+0;boot_slot:1'
        )

    def reset(self):
        self.mode, self.started, self.header, self.received = 'loop', False, None, bytearray()

    def power_cut(self):
        if self.mode in ('off', 'jumped'):
            return  # 017: recovery not proven; the model keeps it stuck
        self.reset()

    def greeting(self):
        return {'loop': b'\xbb', 'app': bytes((0xAA, 7))}.get(self.mode)

    def receive(self, command, payload):
        """One frame from B; returns the chip's replies as (type, payload)."""
        if self.mode in ('off', 'jumped'):
            return []
        if command == 9:
            return [(0x0A, self.metadata())]
        if command != 0x0D or not payload:
            return []
        if self.mode == 'app':
            if payload == b'\xee':
                self.reset()  # replies, then reboots into its bootloader
                return [(0x0E, b'\x54')]
            return []
        if self.mode == 'loop':  # only 0xee and 0x18 act before a session
            if payload == b'\xee':
                self.mode = 'session'
            elif payload == b'\x18':
                self.mode = 'app' if self.valid else 'jumped'
                if not self.valid:
                    self.hazards.append('0x18 before a session with an invalid application')
            return []
        if payload == b'\xee':  # abandons the session
            self.reset()
            return []
        if payload == b'\x18':
            if self.valid:
                self.reset()
            else:
                self.mode = 'off'
                self.hazards.append('0x18 in a session with an invalid application')
            return []
        op, body = payload[0], payload[1:]
        if op == 2 and len(body) == 13:  # start: erase the settings page
            self.valid, self.version, self.started = False, None, True
            self.header, self.received = None, bytearray()
            return [(0x0E, b'\x54')]
        if op == 3 and self.started:
            if self.header is None:  # the chip parses magic, size and CRC only
                if len(body) != 64 or body[:4] != bytes.fromhex('beefcafe'):
                    return [(0x0E, b'\x84bad header')]
                self.header = (
                    int.from_bytes(body[5:9], 'big'),
                    int.from_bytes(body[9:11], 'big'),
                    struct.unpack('>4H', body[11:19]),
                )
                return [(0x0E, b'\x54')]
            if len(self.received) + len(body) > self.header[0]:
                return [(0x0E, b'\x84range')]
            self.received += body
            return [(0x0E, b'\x54')]
        if op == 5 and self.header is not None:
            size, crc, version = self.header
            if len(self.received) != size or crc16_ccitt(bytes(self.received)) != crc:
                return [(0x0E, b'\x84crc')]
            self.valid, self.version, self.image = True, version, bytes(self.received)
            return [(0x0E, b'\x54')]
        return [(0x0E, b'\x84')]


class Native:
    """The native BleSession behind the Python model's interface."""

    def __init__(self, lib, data, known, revision=7):
        self.storage = lib.BleSession()
        self.check(
            self.storage.init(
                data, [(bytes.fromhex(digest), tuple(version)) for digest, version in known.items()], revision
            )
        )

    @staticmethod
    def check(status):
        if status:
            raise CompanionError('native status %d' % status)

    phase = property(lambda self: BLE_PHASES[self.storage.phase()])
    offset = property(lambda self: self.storage.offset())
    in_session = property(lambda self: self.storage.in_session())
    application_valid = property(lambda self: self.storage.valid())

    def request(self, capacity=249):
        status, produced, backing, (action, command, timeout_ms, delay_us) = self.storage.request(capacity)
        assert backing[:16] + backing[capacity + 16 :] == b'\xa5' * 32
        if status:
            assert produced == 0 and backing[16 : 16 + capacity] == b'\xa5' * capacity
        self.check(status)
        return (action, command, backing[16 : 16 + produced], timeout_ms, delay_us)

    def complete(self, success):
        self.check(self.storage.complete(success))

    def accept(self, command, payload):
        self.check(self.storage.accept(command, payload))

    def transport_failed(self):
        self.check(self.storage.failed())

    def abort(self):
        self.check(self.storage.abort())


def quiet(call, *args):
    try:
        call(*args)
        return True
    except CompanionError:
        return False


def drive(session, chip, cut=None):
    """Run the session against the chip. cut(session, request) returns None,
    "before" (power cut instead of sending), "after" (cut once the chip has the
    frame, before its reply) or "stop" (return, nothing sent, no cut).
    Returns (requests, outcome)."""
    requests = []
    while session.phase not in TERMINAL:
        req = session.request()
        when = cut(session, req) if cut else None
        if when == 'stop':  # leave the chip as it is; the caller continues
            return requests, 'stopped'
        if when == 'before':
            chip.power_cut()
            return requests, 'cut'
        requests.append(req)
        action, command, payload = req[:3]
        if action == BLE_LISTEN:
            greeting = chip.greeting()
            quiet(session.accept, 5, greeting) if greeting else quiet(session.transport_failed)
            continue
        replies = chip.receive(command, payload) if action != BLE_DELAY else []
        if when == 'after':
            chip.power_cut()
            return requests, 'cut'
        if action in (BLE_WRITE, BLE_DELAY):
            quiet(session.complete, True)
        elif replies:
            quiet(session.accept, *replies[0])
        else:
            quiet(session.transport_failed)
    return requests, session.phase


@pytest.mark.gate
class BleSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = xyf_hosttest.module('ble_session')

    def pair(self, data=None, version=NEW, revision=7):
        data = image() if data is None else data
        known = {sha256(data).hexdigest(): version, '00' * 32: OLD}
        return Native(self.lib, data, known, revision), BleSession(data, known, revision)

    def run_both(self, chip_args, data=None, cut=None, version=NEW):
        """Native and Python against identical chips; their requests must match."""
        results = []
        for session in self.pair(data, version):
            chip = NordicChip(**chip_args)
            requests, outcome = drive(session, chip, cut)
            results.append((session, chip, requests, outcome))
        self.assertEqual(results[0][2], results[1][2])
        self.assertEqual(results[0][3], results[1][3])
        return results

    def test_known_image_size_matches_struct(self):
        self.assertEqual(self.lib.known_size(), 32 + 4 * 4)  # sha256, four version words

    def test_full_update_from_every_starting_mode_matches_python_model(self):
        for size in (8, 248, 252, 4096, 0x70000):
            data = image(size)
            for chip_args in ({'mode': 'app'}, {'mode': 'loop'}, {'mode': 'loop', 'valid': False}):
                with self.subTest(size=size, chip=chip_args):
                    for session, chip, requests, outcome in self.run_both(chip_args, data):
                        self.assertEqual(outcome, 'complete')
                        self.assertEqual((chip.mode, chip.valid, chip.version, chip.image), ('app', True, NEW, data))
                        self.assertEqual(chip.hazards, [])
                        self.assertTrue(session.application_valid)
                        self.assertFalse(session.in_session)
                        chunks = [r[2] for r in requests if r[2][:1] == b'\x03'][1:]
                        self.assertTrue(all(len(c) == 249 for c in chunks[:-1]))
                        self.assertEqual(b''.join(c[1:] for c in chunks), data)

    def test_difference_1_header_has_crc_at_9_and_version_at_11(self):
        data = image()
        known = {sha256(data).hexdigest(): (1, 2, 3, 4)}
        for session in (Native(self.lib, data, known), BleSession(data, known, 7)):
            drive(session, NordicChip(), cut=lambda s, r: 'stop' if s.phase == 'header' else None)
            header = session.request()[2][1:]
            self.assertEqual(header[:9], bytes.fromhex('beefcafe03') + len(data).to_bytes(4, 'big'))
            self.assertEqual(header[9:11], crc16_ccitt(data).to_bytes(2, 'big'))
            self.assertEqual(header[11:19], bytes.fromhex('0001000200030004'))
            self.assertEqual(header[19:23], bytes.fromhex('0001f000'))
            self.assertEqual(header[23:27], (0x84000 | 7).to_bytes(4, 'big'))
            self.assertEqual(header[27:], bytes(37))

    def test_difference_2_application_gets_0xee_then_greeting_gets_second_0xee(self):
        session, chip, requests, outcome = self.run_both({'mode': 'app'})[0]
        self.assertEqual(
            requests[:7],
            [
                (BLE_EXCHANGE, 9, b'', 100, 0),  # mode:normal
                (BLE_EXCHANGE, 0x0D, b'\xee', 100, 0),  # to the application: 0e[54], reboot
                (BLE_DELAY, 0, b'', 0, 250000),
                (BLE_EXCHANGE, 9, b'', 100, 0),  # mode:bootloader
                (BLE_LISTEN, 5, b'', 500, 0),  # [0xbb]
                (BLE_WRITE, 0x0D, b'\xee', 0, 0),  # second 0xee: the session starts
                (BLE_EXCHANGE, 0x0D, bytes.fromhex('020001000000020000b000084007'), 1500, 0),
            ],
        )
        # Already in the loop (017: expected on a cold boot): only the greeting's 0xee.
        session, chip, requests, outcome = self.run_both({'mode': 'loop'})[0]
        self.assertEqual(
            [r[:3] for r in requests[:3]], [(BLE_EXCHANGE, 9, b''), (BLE_LISTEN, 5, b''), (BLE_WRITE, 0x0D, b'\xee')]
        )
        self.assertEqual(outcome, 'complete')

    def test_difference_3_application_reply_to_0xee_is_read_and_checked(self):
        for event in (('accept', 0x0E, b'\x84'), ('accept', 0x0D, b'\x54'), ('transport_failed',)):
            for session in self.pair():
                quiet(session.accept, 0x0A, b'mode:normal;sw_version:1.0.1+0')
                self.assertEqual(session.phase, 'enter')
                self.assertEqual(session.request()[:3], (BLE_EXCHANGE, 0x0D, b'\xee'))
                self.assertFalse(quiet(getattr(session, event[0]), *event[1:]))
                self.assertEqual((session.phase, session.in_session), ('failed', False))

    def test_difference_4_exit_and_live_version_check_after_finish(self):
        session, chip, requests, outcome = self.run_both({'mode': 'app'})[0]
        finish = requests.index((BLE_EXCHANGE, 0x0D, b'\x05', 1000, 0))
        self.assertEqual(
            requests[finish + 1 :],
            [
                (BLE_WRITE, 0x0D, b'\x18', 0, 0),  # in the session: reset into the loop
                (BLE_LISTEN, 5, b'', 2500, 0),  # [0xbb]
                (BLE_WRITE, 0x0D, b'\x18', 0, 0),  # answer: run the application
                (BLE_EXCHANGE, 9, b'', 100, 0),
            ],
        )  # mode:normal, sw_version 1.0.2+0
        # Finish alone is not completion; a different running version fails the check.
        for session in self.pair():
            chip = NordicChip(reports=(1, 0, 9, 0))
            self.assertEqual(drive(session, chip)[1], 'failed')
            self.assertEqual((chip.mode, chip.valid, session.application_valid), ('app', True, True))
            stopped = self.pair()[0 if isinstance(session, Native) else 1]
            drive(stopped, NordicChip(), cut=lambda s, r: 'stop' if s.phase == 'exit' else None)
            self.assertEqual((stopped.phase, stopped.application_valid), ('exit', True))

    def test_difference_5_only_an_exact_sha256_match_is_admitted_before_any_traffic(self):
        data = image()
        known = {sha256(data).hexdigest(): NEW}
        for altered in (data[:-1] + bytes((data[-1] ^ 1,)), image(seed=1), data[:-4]):
            with self.assertRaises(CompanionError):
                BleSession(altered, known, 7)
            with self.assertRaises(CompanionError):
                Native(self.lib, altered, known)
        for empty in ({},):
            with self.assertRaises(CompanionError):
                BleSession(data, empty, 7)
            with self.assertRaises(CompanionError):
                Native(self.lib, data, empty)
        storage = self.lib.BleSession()
        self.assertNotEqual(storage.init(data, [(sha256(data[:-4]).digest(), NEW)], 7), 0)
        self.assertEqual(BLE_PHASES[storage.phase()], 'failed')
        self.assertNotEqual(storage.request(249)[0], 0)
        # The platform word is not checked by the chip: any revision updates the same way.
        for revision in (0, 7, 999):
            for session in (Native(self.lib, data, known, revision), BleSession(data, known, revision)):
                self.assertEqual(drive(session, NordicChip())[1], 'complete')

    def test_difference_6_no_pending_phase_0x92_stops(self):
        self.assertFalse([p for p in BLE_PHASES if 'pending' in p])
        for phase in ('start', 'header', 'data', 'finish'):
            for session in self.pair():
                drive(session, NordicChip(), cut=lambda s, r: 'stop' if s.phase == phase else None)
                self.assertEqual(session.phase, phase)
                self.assertFalse(quiet(session.accept, 0x0E, b'\x92'))
                self.assertEqual(
                    (session.phase, session.in_session, session.application_valid), ('failed', True, False)
                )

    def test_every_session_reply_error_ends_in_a_defined_state_and_abort_recovers(self):
        for phase in ('start', 'header', 'data', 'finish'):
            for reply in ((0x0E, b'\x84'), (0x0E, b'\x84text'), (0x0D, b'\x54'), (0x0A, b'\x54'), None):
                for session in self.pair():
                    chip = NordicChip()
                    drive(session, chip, cut=lambda s, r: 'stop' if s.phase == phase else None)
                    chip.receive(*session.request()[1:3])  # the chip acts; its reply is lost or bad
                    if reply:
                        self.assertFalse(quiet(session.accept, *reply))
                    else:
                        self.assertFalse(quiet(session.transport_failed))
                    self.assertEqual((session.phase, session.in_session), ('failed', True))
                    self.assertFalse(quiet(session.request))
                    session.abort()
                    self.assertEqual(session.request()[:3], (BLE_WRITE, 0x0D, b'\xee'))
                    chip.receive(0x0D, b'\xee')
                    session.complete(True)
                    self.assertEqual((session.phase, session.in_session), ('aborted', False))
                    self.assertEqual((chip.mode, chip.valid), ('loop', phase == 'finish'))
                    retry = self.pair()[0 if isinstance(session, Native) else 1]
                    self.assertEqual(drive(retry, chip)[1], 'complete')
                    self.assertEqual((chip.mode, chip.version, chip.hazards), ('app', NEW, []))

    def test_abort_is_refused_outside_a_session(self):
        for session in self.pair():
            for _ in range(3):
                self.assertFalse(quiet(session.abort))
                self.assertNotEqual(session.phase, 'aborting')
                drive(session, NordicChip(), cut=lambda s, r: 'stop' if s.phase == 'answer' else None)
        for session in self.pair():
            self.assertEqual(drive(session, NordicChip())[1], 'complete')
            self.assertFalse(quiet(session.abort))

    def test_power_cut_at_every_step_leaves_a_reachable_chip_and_a_retry_succeeds(self):
        mid = len(image()) // 248 // 2 * 248
        cuts = [  # (name, cut, chip after: mode, valid, sw_version)
            ('before any traffic', lambda s, r: 'before', ('loop', True, '1.0.1+0')),
            (
                'after 0xee to the application',
                lambda s, r: 'before' if s.phase == 'greeting' else None,
                ('loop', True, '1.0.1+0'),
            ),
            (
                'in the session, before start',
                lambda s, r: 'before' if s.phase == 'start' else None,
                ('loop', True, '1.0.1+0'),
            ),
            ('after start (erased)', lambda s, r: 'after' if s.phase == 'start' else None, ('loop', False, '0.0.0+0')),
            (
                'mid-chunk',
                lambda s, r: 'after' if s.phase == 'data' and s.offset == mid else None,
                ('loop', False, '0.0.0+0'),
            ),
            ('before finish', lambda s, r: 'before' if s.phase == 'finish' else None, ('loop', False, '0.0.0+0')),
            (
                'after finish, before exit',
                lambda s, r: 'before' if s.phase == 'exit' else None,
                ('loop', True, '1.0.2+0'),
            ),
            (
                'after exit, before release',
                lambda s, r: 'before' if s.phase == 'release' else None,
                ('loop', True, '1.0.2+0'),
            ),
        ]
        for start in ('app', 'loop'):
            for name, cut, expected in cuts:
                with self.subTest(start=start, cut=name):
                    for session, chip, requests, outcome in self.run_both({'mode': start}, cut=cut):
                        self.assertEqual(outcome, 'cut')
                        self.assertEqual((chip.mode, chip.valid, chip.sw_version()), expected)
                        self.assertIn(b'sw_version:' + expected[2].encode(), chip.metadata())
                        retry = self.pair()[0 if isinstance(session, Native) else 1]
                        self.assertEqual(drive(retry, chip)[1], 'complete')
                        self.assertEqual((chip.mode, chip.version, chip.image, chip.hazards), ('app', NEW, image(), []))

    def test_forbidden_0x18_cannot_be_forced(self):
        """0x18 inside a session is requested only after finish returned 0x54."""
        for session in self.pair():  # directed: a failed finish, then every API call
            drive(session, NordicChip(), cut=lambda s, r: 'stop' if s.phase == 'finish' else None)
            self.assertFalse(quiet(session.accept, 0x0E, b'\x84'))
            for call, args in (
                ('complete', (True,)),
                ('complete', (False,)),
                ('accept', (0x0E, b'\x54')),
                ('transport_failed', ()),
                ('request', ()),
            ):
                self.assertFalse(quiet(getattr(session, call), *args))
                self.assertEqual((session.phase, session.application_valid), ('failed', False))
            session.abort()
            self.assertEqual(session.request()[2], b'\xee')
        payloads = [
            b'\x54',
            b'\x84',
            b'\x92',
            b'\x18',
            b'\xbb',
            b'\xee',
            b'mode:normal;sw_version:1.0.2+0',
            b'mode:bootloader;sw_version:0.0.0+0',
            b'mode:normal;sw_version:1.0.1+0',
        ]
        rng = random.Random(0x18)
        for walk in range(300):
            seed = rng.randrange(1 << 30)
            transcripts = []
            for session in self.pair(image(8)):
                local, chip, trace, finish_ok = random.Random(seed), NordicChip(), [], False
                for _ in range(80):
                    try:
                        req = session.request()
                    except CompanionError:
                        req = None
                    if req and req[2] == b'\x18':
                        self.assertTrue(finish_ok, '0x18 requested without finish 0x54 (walk %d)' % walk)
                    trace.append((session.phase, req))
                    if local.random() < 0.7 and req:  # the chip's honest answer
                        if req[0] == BLE_LISTEN:
                            event = ('accept', 5, chip.greeting() or b'\xaa')
                        elif req[0] in (BLE_WRITE, BLE_DELAY):
                            chip.receive(req[1], req[2]) if req[0] == BLE_WRITE else None
                            event = ('complete', True)
                        else:
                            replies = chip.receive(req[1], req[2])
                            event = ('accept',) + replies[0] if replies else ('transport_failed',)
                    else:  # anything at all
                        event = local.choice(
                            [('complete', True), ('complete', False), ('transport_failed',), ('abort',)]
                            + [('accept', c, p) for c in (5, 9, 0x0A, 0x0D, 0x0E) for p in payloads]
                        )
                    ok = quiet(getattr(session, event[0]), *event[1:])
                    if ok and req and req[2] == b'\x05' and event[0] == 'accept':
                        finish_ok = finish_ok or (event[1] == 0x0E and event[2][:1] == b'\x54')
                    trace.append(event)
                transcripts.append(trace)
            self.assertEqual(transcripts[0], transcripts[1])

    def test_real_official_images_from_private_fixture(self):
        if not FIXTURE.exists():
            self.skipTest('private fixture %s absent' % FIXTURE)
        entries = json.loads(FIXTURE.read_text())['images']
        known = {e['sha256']: tuple(int(x) for x in e['version'].replace('+', '.').split('.')) for e in entries}
        for entry in entries:
            path = paths.PRIVATE_ROOT / entry['image']
            if not path.exists():
                self.skipTest('official image absent: ' + entry['image'])
            data = path.read_bytes()
            self.assertEqual(
                (sha256(data).hexdigest(), len(data), crc16_ccitt(data)),
                (entry['sha256'], entry['size'], entry['crc16']),
            )
            for session in (Native(self.lib, data, known), BleSession(data, known, 7)):
                chip = NordicChip(mode='loop', valid=False)
                requests, outcome = drive(session, chip)
                self.assertEqual(outcome, 'complete')
                self.assertEqual((chip.image, version_text(chip.version)), (data, entry['version']))
                self.assertEqual(len([r for r in requests if r[2][:1] == b'\x03']) - 1, (len(data) + 247) // 248)
            with self.assertRaises(CompanionError):
                BleSession(data[:-4] + bytes(4), known, 7)

    def test_probe_requires_metadata_reply_and_twenty_polls_bound_it(self):
        for session in self.pair():
            self.assertFalse(quiet(session.accept, 0x0E, b'mode:bootloader'))
            self.assertEqual(session.phase, 'failed')
        for session in self.pair():
            for event in (('accept', 0x0A, b'mode:normal'), ('accept', 0x0E, b'\x54'), ('complete', True)):
                quiet(getattr(session, event[0]), *event[1:])
            self.assertEqual(session.phase, 'probe')
            for _ in range(19):
                session.accept(0x0A, b'mode:normal;hw_rev:7')
                self.assertEqual(session.request()[:2], (BLE_EXCHANGE, 9))
            self.assertFalse(quiet(session.accept, 0x0A, b'mode:normal'))
            self.assertEqual(session.phase, 'failed')
        for session in self.pair():  # an unknown mode is no mode
            self.assertFalse(quiet(session.accept, 0x0A, b'mode:application'))

    def test_greeting_must_be_exactly_bb(self):
        for payload in (b'\xaa\x07', b'\xbb\x00', b'\xee'):
            for session in self.pair():
                session.accept(0x0A, b'mode:bootloader;sw_version:1.0.1+0')
                self.assertEqual(session.phase, 'greeting')
                self.assertFalse(quiet(session.accept, 5, payload))
                self.assertEqual((session.phase, session.in_session), ('failed', False))

    def test_version_parser_matches_python(self):
        for text in (b'1.0.2+0', b'65535.0.0+65535', b'0.0.0+0'):
            status, out = self.lib.version(text)
            self.assertEqual(status, 0)
            self.assertEqual(out, tuple(int(x) for x in text.replace(b'+', b'.').split(b'.')))
        for text in (b'1.0.2', b'1.0.2+', b'1.0.2+0x', b'65536.0.0+0', b'.0.2+0', b'1.0.2.0', b'a'):
            status, out = self.lib.version(text)
            self.assertNotEqual(status, 0)
            self.assertEqual(out, (9, 9, 9, 9))

    def test_capacity_failure_does_not_advance_or_modify_output(self):
        session = self.pair()[0]
        drive(session, NordicChip(), cut=lambda s, r: 'stop' if s.phase == 'start' else None)
        self.assertFalse(quiet(session.request, 13))
        self.assertEqual(session.phase, 'start')
        session.accept(0x0E, b'\x54')
        self.assertFalse(quiet(session.request, 64))
        self.assertEqual(session.phase, 'header')

    def test_default_session_and_transport_failures_stay_stopped(self):
        storage = self.lib.BleSession()  # constructed over 0xA5 bytes
        self.assertEqual(BLE_PHASES[storage.phase()], 'uninitialized')
        for call, args in (('complete', (True,)), ('failed', ()), ('abort', ()), ('accept', (0x0E, b'\x54'))):
            self.assertNotEqual(getattr(storage, call)(*args), 0)
        for session in self.pair():
            self.assertFalse(quiet(session.transport_failed))
            self.assertEqual(session.phase, 'failed')
            self.assertFalse(quiet(session.request))

    def test_metadata_parser_is_bounded_and_requires_exact_mode_field(self):
        for payload, expected in (
            (b'mode:bootloader', 1),
            (b'hw_rev:7;mode:bootloader;serial:01234567', 1),
            (b'mode:application;', 0),
        ):
            self.assertEqual(self.lib.mode(payload), (0, expected))
        for payload in (
            b'',
            b'modejunk:bootloader',
            b'mode:bootloader;mode:application',
            b'mode:bootloader\0',
            b'mode',
            b';mode:bootloader',
            b'x' * 249,
        ):
            status, out = self.lib.mode(payload)
            self.assertNotEqual(status, 0)
            self.assertEqual(out, 0)

    def test_platform_identity_is_derived_from_hardware_revision(self):
        for revision in (0, 1, 7, 999):
            self.assertEqual(self.lib.word(revision), (0, (0x21 << 14) | revision))
        for revision in (1000, 1023, 0xFFFFFFFF):
            status, out = self.lib.word(revision)
            self.assertNotEqual(status, 0)
            self.assertEqual(out, 0)

    def test_cypress_security_key_is_caller_supplied_and_length_checked(self):
        key = b'ABCDEF'  # Synthetic; captured stock key stays private.
        status, produced, output = self.lib.cypress_entry(key, 6, 32)
        self.assertEqual(status, 0)
        self.assertEqual(output[16 : 16 + produced], cypress_packet(0x38, key))
        for size, capacity in ((5, 32), (6, 12)):
            status, produced, output = self.lib.cypress_entry(key, size, capacity)
            self.assertNotEqual(status, 0)
            self.assertEqual(produced, 0)
            self.assertEqual(output, b'\xa5' * (capacity + 32))

    def test_backplane_wrapper_uses_private_key_and_proven_62_byte_mtu(self):
        image = CyacdImage.parse(cyacd())
        rows = [(row.array_id, row.number, bytes(row.data)) for row in image.rows]
        native = self.lib.BackplaneSession()
        key = b'ABCDEF'
        self.assertEqual(native.init(rows, len(rows), key, 6), 0)
        golden = CypressSession(image, 62)
        while not golden.finished:
            status, produced, output = native.request(62)
            self.assertEqual(status, 0)
            self.assertLessEqual(produced, 62)
            expected = cypress_packet(0x38, key) if golden.position == -2 else golden.request()
            self.assertEqual(output[16 : 16 + produced], expected)
            if golden.position == -2:
                reply = enter_response()
            elif golden.position == -1:
                reply = response(bytes.fromhex('0000ff01'))
            else:
                step = golden.steps[golden.position]
                reply = response(
                    step.expected_payload if step.expected_payload else b'\1' if step.expect_nonzero else b''
                )
            self.assertEqual(native.accept(reply), 0)
            golden.accept(reply)
        self.assertEqual(native.phase(), 6)
        status, produced, output = native.exit(62)
        self.assertEqual(status, 0)
        self.assertEqual(output[16 : 16 + produced], golden.exit_request())

    def test_backplane_wrapper_rejects_missing_key_and_uninitialized_use(self):
        native = self.lib.BackplaneSession()
        status, produced, _ = native.request(62)
        self.assertNotEqual(status, 0)
        self.assertEqual(produced, 0)
        self.assertNotEqual(native.init(None, 367, None, 6), 0)
        self.assertNotEqual(native.exit(62)[0], 0)


if __name__ == '__main__':
    unittest.main()
