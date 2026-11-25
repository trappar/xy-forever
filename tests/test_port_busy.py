"""A serial port another program holds (Resource busy on macOS and Linux, access denied on Windows): the host tools say
who holds it, when the system can tell, and how to free it, never to power-cycle the OP-XY (the device is fine)."""

import errno
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from xy_forever import device_client, xyf

PORT = '/dev/cu.usbmodemXYF1'


class Clock:
    """time.monotonic for connect, moved on by its pauses."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def pause(self, seconds):
        self.now += seconds


class HeldPortTest(unittest.TestCase):
    def connect(self, opens, holders=(('Google Chrome', 7632),), timeout=600.0):
        clock, waited = Clock(), []
        with (
            mock.patch.object(device_client, 'SIM', None),
            mock.patch.object(device_client.device_lock, 'hold'),
            mock.patch.object(device_client, 'time', clock),
            mock.patch.object(device_client, 'pause', clock.pause),
            mock.patch.object(device_client, 'find_port', return_value=PORT),
            mock.patch.object(device_client, 'open_port', side_effect=opens),
            mock.patch.object(device_client, 'port_holders', return_value=list(holders)),
        ):
            try:
                device_client.connect(timeout, log=lambda line: None, waiting=lambda: waited.append(1))
            except SystemExit as error:
                return error, clock.now, waited
        self.fail('connected')

    def test_a_held_port_is_named_at_once_without_a_power_cycle(self):
        error, spent, waited = self.connect(OSError(errno.EBUSY, 'Resource busy'))
        self.assertIsInstance(error, device_client.PortBusy)
        self.assertIn('/dev/cu.usbmodemXYF1 is in use by Google Chrome (PID 7632)', error.code)
        self.assertIn('click Disconnect in the XY Forever page or close it', error.code)
        self.assertNotIn('PLAY', error.code)
        self.assertEqual(waited, [])  # no "power it on holding PLAY" line
        self.assertLess(spent, device_client.BUSY_S + 1)  # not the whole --wait

    def test_unknown_holder_and_short_waits(self):
        error, _, _ = self.connect(PermissionError(errno.EACCES, 'Access denied'), holders=(), timeout=1.0)
        self.assertIsInstance(error, device_client.PortBusy)
        self.assertIn('in use by another program', error.code)

    def test_a_port_free_again_within_the_grace_is_no_holder_and_no_answer_still_says_play(self):
        error, _, _ = self.connect([OSError(errno.EBUSY, 'busy')] * 3 + [OSError(errno.ENOENT, 'gone')] * 40, timeout=8)
        self.assertNotIsInstance(error, device_client.PortBusy)
        self.assertIn('PLAY', error.code)

    def test_which_errors_mean_held(self):
        held = device_client.port_busy
        self.assertTrue(held(OSError(errno.EBUSY, 'Resource busy')))
        self.assertTrue(held(PermissionError(errno.EACCES, 'denied')))
        self.assertTrue(held(OSError("could not open port 'COM3': PermissionError(13, 'Access is denied.', None, 5)")))
        self.assertFalse(held(OSError(errno.ENOENT, 'No such file or directory')))
        self.assertFalse(held(TimeoutError('did not open within 2 s')))

    def test_holders_from_lsof(self):
        listing = 'p7632\ncGoogle Chrome\np%d\ncpython3\np99\ncscreen\n' % os.getpid()

        def run(argv, **kwargs):
            self.assertEqual(argv, ['lsof', '-F', 'pc', PORT])
            return SimpleNamespace(stdout=listing)

        with mock.patch.object(device_client.sys, 'platform', 'darwin'):
            self.assertEqual(device_client.port_holders(PORT, run), [('Google Chrome', 7632), ('screen', 99)])
            self.assertEqual(device_client.port_holders(PORT, mock.Mock(side_effect=FileNotFoundError())), [])
        with mock.patch.object(device_client.sys, 'platform', 'win32'):
            self.assertEqual(device_client.port_holders('COM3', run), [])

    def test_xyf_says_the_holder_in_one_line(self):
        def connect(*args, **kwargs):
            raise device_client.PortBusy(device_client.busy_text(PORT, [('Google Chrome', 7632)]))

        lines = []
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(out=Path(tmp) / 'os.ldr', wait=600.0, verbose=False)
            self.assertEqual(xyf.stock_os(args, lines.append, connect), 1)
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r'^xyf stock-os: the OP-XY.s serial port .* in use by Google Chrome \(PID 7632\)')
        self.assertNotIn('PLAY', lines[0])


if __name__ == '__main__':
    unittest.main()
