"""Read-only MTP/PTP client for the OP-XY over USB (host only; pyusb on Homebrew's libusb).

Shared by scripts/mtp_probe.py and `scripts/setup.py import-stock --from-device`. Read only, enforced here:
Session.transact is the one place a command leaves the host, and it refuses every operation outside READ_ONLY
before anything reaches the transport; no data phase is ever sent to the device. A transport is any object
with write(bytes), read(size) -> bytes (each raising OSError on a failed transfer) and close() (UsbTransport,
or a fake in the tests).
"""

import ctypes.util
import struct
import subprocess
from pathlib import Path

from xy_forever import device_lock, no_device
from xy_forever.transport import OP_XY_VID

READ_ONLY = {
    0x1001: 'GetDeviceInfo',
    0x1002: 'OpenSession',
    0x1003: 'CloseSession',
    0x1004: 'GetStorageIDs',
    0x1005: 'GetStorageInfo',
    0x1007: 'GetObjectHandles',
    0x1008: 'GetObjectInfo',
    0x1009: 'GetObject',
}
MTP_CLASSES = (6, 255)  # still image (PTP), vendor class (the OP-XY's MTP, docs/HARDWARE.md)
ROOT = 0xFFFFFFFF  # GetObjectHandles parent: the storage root
OK, SESSION_ALREADY_OPEN = 0x2001, 0x201E
FOLDER = 0x3001  # ObjectFormat Association
LIBUSB_ERROR_ACCESS, LIBUSB_ERROR_BUSY = -3, -6
CHUNK = 0x10000


class MtpError(Exception):
    """One line for the user; never a traceback."""


class Refused(MtpError):
    pass


def ptp_string(data, at):
    n = data[at]
    return data[at + 1 : at + 1 + 2 * n].decode('utf-16-le', 'replace').rstrip('\0'), at + 1 + 2 * n


def device_info(data):
    """GetDeviceInfo's identity strings: {manufacturer, model, version, serial} (#288: the USB identity record)."""
    at = ptp_string(data, 8)[1] + 2  # standard and extension versions, the extension's text, the functional mode
    for _ in range(5):  # operations, events, device properties, capture and playback formats
        at += 4 + 2 * struct.unpack_from('<I', data, at)[0]
    out = {}
    for name in ('manufacturer', 'model', 'version', 'serial'):
        out[name], at = ptp_string(data, at)
    return out


# the session (pure Python over any transport)


class Session:
    def __init__(self, transport):
        self.transport, self.tid, self.answer = transport, 0, ()

    def transact(self, code, *params):
        """(response code, data) of one read-only operation. The choke point: any other code is refused here."""
        if code not in READ_ONLY:
            raise Refused('MTP operation 0x%04x refused: this client only reads' % code)
        self._command(READ_ONLY[code], code, params)
        return self._response(READ_ONLY[code])

    def _command(self, name, code, params):
        self.tid += 1
        cmd = struct.pack('<IHHI', 12 + 4 * len(params), 1, code, self.tid) + b''.join(
            struct.pack('<I', p) for p in params
        )
        try:
            self.transport.write(cmd)
        except OSError as error:  # usb.core.USBError
            raise MtpError('%s: command not sent (%s)' % (name, error)) from None

    def _response(self, name):
        """(response code, data in) of the transaction just sent; self.answer: the response's parameters."""
        payload, pending = b'', b''
        while True:
            got, pending = pending or self.read(name), b''
            if len(got) < 12:
                raise MtpError('%s: short container (%d bytes)' % (name, len(got)))
            length, kind, rcode, _ = struct.unpack_from('<IHHI', got)
            if kind == 2:  # data phase (device to host)
                data = bytearray(got[12:])
                while len(data) < length - 12:
                    data += self.read(name, 'data stopped at %d/%d bytes' % (len(data), length - 12))
                payload, pending = bytes(data[: length - 12]), bytes(data[length - 12 :])
            elif kind == 3:  # response
                n = max(0, min(length, len(got)) - 12) // 4
                self.answer = struct.unpack_from('<%dI' % n, got, 12)
                return rcode, payload
            else:
                raise MtpError('%s: unexpected container type %d' % (name, kind))

    def read(self, name, what='no answer'):
        for _ in range(3):  # a zero-length packet ends a data phase
            try:
                got = self.transport.read(CHUNK)
            except OSError as error:  # usb.core.USBError
                raise MtpError('%s: %s (%s)' % (name, what, error)) from None
            if got:
                return got
        raise MtpError('%s: %s (empty transfers)' % (name, what))

    def check(self, code, *params):
        rcode, payload = self.transact(code, *params)
        if rcode != OK:
            raise MtpError('%s: response 0x%04x' % (READ_ONLY[code], rcode))
        return payload

    def open(self):
        rcode, _ = self.transact(0x1002, 1)
        if rcode == SESSION_ALREADY_OPEN:  # as go-mtpfs: close it, open again
            self.transact(0x1003)
            rcode, _ = self.transact(0x1002, 1)
        if rcode != OK:
            raise MtpError('OpenSession: response 0x%04x' % rcode)

    def close(self):
        try:
            self.transact(0x1003)
        except MtpError:
            pass

    def storages(self):
        ids = self.check(0x1004)
        return list(struct.unpack_from('<%dI' % struct.unpack_from('<I', ids)[0], ids, 4)) if len(ids) >= 4 else []

    def children(self, storage, parent):
        """[(handle, name, format, size)] of one folder (parent ROOT: the storage root)."""
        handles = self.check(0x1007, storage, 0, parent)
        count = struct.unpack_from('<I', handles)[0] if len(handles) >= 4 else 0
        out = []
        for h in struct.unpack_from('<%dI' % count, handles, 4):
            info = self.check(0x1008, h)
            if len(info) >= 53:
                out.append(
                    (
                        h,
                        ptp_string(info, 52)[0],
                        struct.unpack_from('<H', info, 4)[0],
                        struct.unpack_from('<I', info, 8)[0],
                    )
                )
        return out

    def find(self, path):
        """The handle of /a/b/c, walked from each storage's root by names; None if it is not there."""
        for storage in self.storages():
            parent = ROOT
            for part in (p for p in path.split('/') if p):
                match = [c for c in self.children(storage, parent) if c[1] == part]
                if not match:
                    break
                parent = match[0][0]
            else:
                return parent
        return None

    def get(self, handle):
        return self.check(0x1009, handle)


def fetch(files, folder, transport, log=print):
    """Copy {device path: local name} into folder over MTP; returns the device paths that were not there."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    session, missing = Session(transport), []
    try:
        session.open()
        for device_path, name in files.items():
            handle = session.find(device_path)
            if handle is None:
                missing.append(device_path)
                continue
            body = session.get(handle)
            part = folder / (name + '.part')
            part.write_bytes(body)
            part.replace(folder / name)
            log('%s: %d bytes -> %s' % (device_path, len(body), folder / name))
    finally:
        session.close()
        transport.close()
    return missing


# USB (pyusb on libusb 1.0; imported here, as setup.py's bootstrap imports this module before uv sync)


def load_libusb():
    """pyusb's libusb 1.0 backend: Homebrew's libusb, else one the system finds; MtpError (one line) without it."""
    import usb.backend.libusb1

    try:
        prefix = subprocess.run(['brew', '--prefix', 'libusb'], capture_output=True, text=True).stdout.strip()
    except OSError:
        prefix = ''
    homebrew = prefix and Path(prefix, 'lib/libusb-1.0.dylib')
    backend = usb.backend.libusb1.get_backend(
        find_library=lambda name: str(homebrew) if homebrew and homebrew.is_file() else ctypes.util.find_library(name)
    )
    if backend is None:
        raise MtpError('libusb-1.0 not found (brew install libusb)')
    return backend


def interfaces(dev):
    """[{number, cls: (class, subclass, protocol), endpoints: [(address, type, size)]}] of the device's
    configuration (the OP-XY has one), alternate setting 0: descriptors only, the device is not opened."""
    return [
        {
            'number': intf.bInterfaceNumber,
            'cls': (intf.bInterfaceClass, intf.bInterfaceSubClass, intf.bInterfaceProtocol),
            'endpoints': [(e.bEndpointAddress, e.bmAttributes & 3, e.wMaxPacketSize) for e in intf],
        }
        for intf in dev[0]
        if intf.bAlternateSetting == 0
    ]


def mtp_interface(found):
    """(number, bulk IN, bulk OUT) of the first MTP/PTP-class interface with both bulk endpoints, else None."""
    for intf in found:
        bulk_in = [a for a, kind, _ in intf['endpoints'] if kind == 2 and a & 0x80]
        bulk_out = [a for a, kind, _ in intf['endpoints'] if kind == 2 and not a & 0x80]
        if intf['cls'][0] in MTP_CLASSES and bulk_in and bulk_out:
            return intf['number'], bulk_in[0], bulk_out[0]
    return None


def choose(devices):
    """devices: [(device, (vid, pid), interfaces)]. The first OP-XY in MTP mode as (device, ids, (number, in, out))."""
    op_xy = [d for d in devices if d[1][0] == OP_XY_VID]
    if not op_xy:
        raise MtpError('no OP-XY (VID 0x2367) on USB')
    for dev, ids, found in op_xy:
        picked = mtp_interface(found)
        if picked:
            return dev, ids, picked
    raise MtpError(
        "the OP-XY is on USB but not in MTP mode (no MTP interface): start the dev menu's MTP "
        '(COM held while switching on, Track 3, Track 2)'
    )


def usb_problem(step, rc):
    if rc in (LIBUSB_ERROR_ACCESS, LIBUSB_ERROR_BUSY):
        return (
            "the OP-XY's MTP interface is held by another program: quit OpenMTP, Image Capture and Android "
            'File Transfer, then retry'
        )
    return '%s failed (libusb %s)' % (step, rc)


class UsbTransport:
    """Bulk transfers on the claimed MTP interface; a failed one raises usb.core.USBError (an OSError)."""

    def __init__(self, dev, number, ep_in, ep_out, timeout_ms):
        self.dev, self.number, self.ep_in, self.ep_out, self.timeout_ms = dev, number, ep_in, ep_out, timeout_ms

    def write(self, data):
        self.dev.write(self.ep_out, data, self.timeout_ms)

    def read(self, size):
        return bytes(self.dev.read(self.ep_in, size, self.timeout_ms))

    def close(self):
        if self.dev:
            import usb.util

            usb.util.release_interface(self.dev, self.number)
            usb.util.dispose_resources(self.dev)
            self.dev = None


def open_op_xy(timeout_ms=5000, log=None):
    """A UsbTransport on the first OP-XY in MTP mode; MtpError (one line) otherwise. log: each device's
    interfaces as they are listed (mtp_probe prints them)."""
    no_device.refuse('USB MTP (libusb)')
    device_lock.hold()
    backend = load_libusb()
    import usb.core
    import usb.util

    devices = []
    for dev in usb.core.find(find_all=True, idVendor=OP_XY_VID, backend=backend):
        found = interfaces(dev)
        if log:
            log('device %04x:%04x' % (dev.idVendor, dev.idProduct))
            for f in found:
                log(
                    'interface %d class %d/%d/%d endpoints %s'
                    % ((f['number'],) + f['cls'] + (', '.join('%02x/%d/%d' % e for e in f['endpoints']),))
                )
        devices.append((dev, (dev.idVendor, dev.idProduct), found))
    dev, _, (number, ep_in, ep_out) = choose(devices)
    try:
        usb.util.claim_interface(dev, number)  # opens the device, then claims
    except usb.core.USBError as error:
        usb.util.dispose_resources(dev)
        raise MtpError(usb_problem('claim interface %d' % number, error.backend_error_code))
    return UsbTransport(dev, number, ep_in, ep_out, timeout_ms)
