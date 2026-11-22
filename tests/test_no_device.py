"""xy_forever/no_device.py: no test opens a real OP-XY. Every opener refuses before it looks for a device: each
test here replaces what the opener would touch next (the glob, the open, libusb) with a call that fails
the test, so none of them can reach a device even when the guard is broken."""

import os
import socket
import subprocess
import sys
import unittest
from unittest import mock

import conformance
import setup
import usb_alive

from xy_forever import mtp_read, no_device, transport
from xy_forever import xyf_host as host
from xy_forever.no_device import DeviceRefused

REFUSED = r'^tests may not open a real device: '
DEVICE = '/dev/cu.usbmodemTEST1'


def reached(what):
    def fail(*args, **kwargs):
        raise AssertionError('the guard let a test reach %s' % what)

    return fail


class GuardTest(unittest.TestCase):
    def test_on_in_every_test_and_its_subprocesses(self):
        self.assertTrue(no_device.on())
        self.assertEqual(os.environ.get(no_device.ENV), '1')
        out = subprocess.run(
            [sys.executable, '-c', 'from xy_forever import no_device; print(no_device.on())'],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertEqual(out, 'True\n')

    def test_a_test_cannot_switch_it_off(self):
        with mock.patch.dict(os.environ, {no_device.ENV: ''}):
            with self.assertRaisesRegex(DeviceRefused, REFUSED + 'it$'):
                no_device.refuse('it')

    def test_off_outside_tests(self):
        env = {k: v for k, v in os.environ.items() if k != no_device.ENV}
        out = subprocess.run(
            [sys.executable, '-c', 'from xy_forever import no_device; no_device.refuse("it"); print("allowed")'],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        ).stdout
        self.assertEqual(out, 'allowed\n')

    def test_not_an_exception(self):
        """No `except Exception` (connect's retry, a step's failure, a skip) can swallow it."""
        self.assertFalse(issubclass(DeviceRefused, Exception))


class SerialTest(unittest.TestCase):
    def test_find_port_refuses_before_the_glob(self):
        with mock.patch.object(host, 'SIM', None), mock.patch.object(transport.glob, 'glob', reached('the glob')):
            with self.assertRaisesRegex(DeviceRefused, REFUSED + r'the serial port \(/dev/cu\.usbmodem\*\)$'):
                host.find_port()

    def test_connect_refuses(self):
        with mock.patch.object(host, 'SIM', None), mock.patch.object(transport.glob, 'glob', reached('the glob')):
            with self.assertRaisesRegex(DeviceRefused, REFUSED):
                host.connect(5.0)

    def test_a_script_run_by_a_test_refuses(self):
        code = (
            'import glob, sys\nfrom xy_forever import xyf_host\n'
            'glob.glob = lambda pattern: sys.exit("reached the glob")\nxyf_host.find_port()'
        )
        run = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertIn('DeviceRefused: tests may not open a real device: the serial port', run.stderr)

    def test_serial_port_refuses_before_opening(self):
        with mock.patch.object(transport, '_opener', reached('the open')):
            for path in (DEVICE, '/dev/tty.usbmodemTEST1', '/dev/ttyACM0'):
                with self.assertRaisesRegex(DeviceRefused, REFUSED + path):
                    transport.SerialPort(path)
            with self.assertRaisesRegex(DeviceRefused, REFUSED + DEVICE):
                host.open_port(DEVICE)

    def test_open_tty_refuses_before_opening(self):
        with mock.patch.object(transport.os, 'open', reached('os.open')):
            with self.assertRaisesRegex(DeviceRefused, REFUSED + DEVICE):
                transport._open_tty(DEVICE)

    def test_a_pseudo_terminal_and_the_simulator_stay_open(self):
        for path in ('/dev/ttys012', '/dev/ttys012-gone', '/dev/pts/3'):
            no_device.refuse_tty(path)
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen(4)
        self.addCleanup(server.close)
        sim = '127.0.0.1:%d' % server.getsockname()[1]
        with mock.patch.object(host, 'SIM', sim), mock.patch.object(transport.glob, 'glob', reached('the glob')):
            port = host.find_port()
            self.assertEqual(port, 'sim:' + sim)
            host.open_port(port).close()

    def test_conformance_without_sim_refuses(self):
        with mock.patch.object(host, 'SIM', None), mock.patch.object(transport.glob, 'glob', reached('the glob')):
            for call in (conformance.Target().present, conformance.Target().open):
                with self.assertRaisesRegex(DeviceRefused, REFUSED):
                    call()


class UsbTest(unittest.TestCase):
    def test_mtp_open_refuses_before_libusb(self):
        with mock.patch.object(mtp_read, 'load_libusb', reached('libusb')):
            with self.assertRaisesRegex(DeviceRefused, REFUSED + r'USB MTP \(libusb\)$'):
                mtp_read.open_op_xy()

    def test_setup_import_from_device_refuses(self):
        with mock.patch.object(mtp_read, 'load_libusb', reached('libusb')):
            with self.assertRaisesRegex(DeviceRefused, REFUSED + r'USB MTP'):
                setup.import_from_device(incoming=os.devnull)

    def test_usb_alive_refuses_before_libusb(self):
        with mock.patch.object(usb_alive, 'load', reached('libusb')):
            with self.assertRaisesRegex(DeviceRefused, REFUSED + r'USB \(libusb\)$'):
                usb_alive.main(['--seconds', '0'])


if __name__ == '__main__':
    unittest.main()
