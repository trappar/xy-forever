"""xyf-host inputs, frames and space (#288): the menu's input log and frame times decoded from diag.status, and the
watch printing each new event once."""

import re
import struct
import unittest
import unittest.mock
from pathlib import Path

from xy_forever import census, host_probe
from xy_forever import usb_proto as P

ROOT = Path(__file__).resolve().parents[1]


def block(magic, data):
    return struct.pack('<II', magic, len(data)) + data


def input_log(events, count=None):
    """An INP1 block: events (ms, what) logged in order; count: events ever logged (default len(events))."""
    count = len(events) if count is None else count
    slots = [(0, 0)] * census.INPUT_EVENTS
    for i, event in zip(range(count - len(events), count), events):
        slots[i % census.INPUT_EVENTS] = event
    body = struct.pack('<4I', census.INPUT_MAGIC, 1, count, 0) + b''.join(struct.pack('<II', *e) for e in slots)
    return block(census.INPUT_MAGIC, body)


def press(key, ms, pressed=True, injected=False):
    return ms, (1 if pressed else 0) | (0x80 if injected else 0) | key << 8


def turn(knob, change, ms):
    return ms, 2 | knob << 8 | (change & 0xFFFF) << 16


class Device:
    def __init__(self, *bodies):
        self.bodies = list(bodies)

    def call(self, command, payload=b'', timeout=None):
        if command == P.STATUS:
            return 0, self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]
        assert command == P.ids.STORAGE_SPACE and payload == struct.pack('<I', 1)
        return 0, struct.pack('<IQ', 1, 3 * 2**20)


class HostProbeTest(unittest.TestCase):
    def run_lines(self, fn, *args, **kwargs):
        out = []
        self.assertEqual(fn(*args, out=out.append, **kwargs), 0)
        return out

    def test_events_name_keys_knob_clicks_and_turns(self):
        other = block(0x31424E4B, b'\0' * 80)  # another block first: found by magic
        events = [press(9, 1000), press(9, 2300, False), press(11, 2400, injected=True), turn(4, -3, 2500)]
        lines = self.run_lines(host_probe.inputs, Device(other + input_log(events)), False)
        self.assertEqual(lines[0], '4 events logged since the menu started')
        self.assertEqual(
            [line.split(None, 2)[2] for line in lines[1:]],
            ['key 9 press', 'key 9 release', 'key 11 (knob 2 click) press (host)', 'knob id 4 turn -3'],
        )

    def test_a_full_ring_keeps_the_last_events_oldest_first(self):
        events = [press(k % 80, k) for k in range(40)]
        count, decoded = census.input_events(input_log(events[-census.INPUT_EVENTS :], count=40)[8:])
        self.assertEqual(count, 40)
        self.assertEqual([e['ms'] for e in decoded], list(range(8, 40)))

    def test_watch_prints_each_new_event_once_and_names_a_gap(self):
        first = input_log([press(9, 10)])
        second = input_log([press(9, 10), press(9, 20, False)])
        later = [press(5, 100 + i) for i in range(40)]  # 38 more than the ring holds between two reads
        third = input_log(later[-census.INPUT_EVENTS :], count=42)
        reads = iter([None, None, KeyboardInterrupt])

        def sleep(_):
            step = next(reads)
            if step:
                raise step()

        out = []
        with self.assertRaises(KeyboardInterrupt):
            host_probe.inputs(Device(first, second, third), True, out=out.append, sleep=sleep)
        self.assertEqual(out[1:3], ['      10 ms  key 9 press', '      20 ms  key 9 release'])
        self.assertEqual(out[3], '(8 events came faster than this read them)')
        self.assertEqual(len(out), 4 + census.INPUT_EVENTS)

    def test_frames_in_microseconds_and_the_whole_present_as_the_frame_cost(self):
        # units of 1024 cycles (2.56 us): draw, transfer, present (each last/min/max), then wait last/max
        fields = [census.FRAMES_MAGIC, 2, 5, 3125, 2750, 3500, 5625, 5500, 5750, 8750, 8500, 9375, 1, 2]
        lines = self.run_lines(host_probe.frames, Device(block(census.FRAMES_MAGIC, struct.pack('<14I', *fields))))
        self.assertEqual(lines[0], 'frames measured: 5')
        self.assertIn('last   8000 us  min   7040 us  max   8960 us', lines[1])
        self.assertIn('last  14400 us  min  14080 us  max  14720 us', lines[2])
        self.assertEqual(
            lines[-1], 'a frame costs the whole present, the loop blocked: at most 42 frames/s (24000 us each)'
        )

    def test_version_1_frames_are_not_read(self):
        old = struct.pack('<16I', census.FRAMES_MAGIC, 1, *([0] * 14))
        with self.assertRaisesRegex(OSError, 'version 2'):
            host_probe.frames(Device(block(census.FRAMES_MAGIC, old)), out=print)

    def test_watch_lines_are_flushed(self):
        with unittest.mock.patch('builtins.print') as printed:
            host_probe.say('x')
        printed.assert_called_once_with('x', flush=True)

    def test_space(self):
        self.assertEqual(self.run_lines(host_probe.space, Device(b'')), ['free: 3145728 bytes (3.0 MiB)'])

    def test_layouts_match_the_device_headers(self):
        knob = (ROOT / 'target/knob_input.hpp').read_text()
        self.assertIn('log_events = %d;' % census.INPUT_EVENTS, knob)
        self.assertIn('log_magic = 0x%08xu;' % census.INPUT_MAGIC, knob)
        key_ui = (ROOT / 'target/key_ui.hpp').read_text()
        self.assertIn('frames_magic = 0x%08xu;' % census.FRAMES_MAGIC, key_ui)
        self.assertIn('{frames_magic, %d}' % census.FRAMES_VERSION, (ROOT / 'target/key_ui.cpp').read_text())
        self.assertIn('sizeof(Frames) == %d * 4' % len(census.FRAMES_FIELDS), key_ui)
        self.assertTrue(re.search(r'kind_release = 0, kind_press = 1, kind_turn = 2', knob))


class SetDefaultBusyTest(unittest.TestCase):
    """#288: set-default right after a pack change met busy (the change's write window settling); it is asked again."""

    def test_busy_is_asked_again(self):
        from xy_forever import xyf_host

        class Port:
            waited = 0.0

            def pause(self, seconds):
                Port.waited += seconds

        dev = xyf_host.Device(Port())
        answers = [(P.ids.S_BUSY, b''), (P.ids.S_BUSY, b''), (0, struct.pack('<I', 0x586D1C48))]
        sent = []

        def call(command, payload, timeout):
            sent.append((command, payload))
            return answers.pop(0)

        dev.call = call
        self.assertEqual(dev.set_default('opxy-121'), (0, 0x586D1C48))
        self.assertEqual(sent, [(P.SET_DEFAULT, b'opxy-121\0')] * 3)
        self.assertEqual(Port.waited, 0.5)
