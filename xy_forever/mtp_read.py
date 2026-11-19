"""Read-only MTP/PTP client for the OP-XY over libusb (host only; ctypes + Homebrew libusb, no packages).

Shared by scripts/mtp_probe.py and `scripts/setup.py import-stock --from-device`. Read only, enforced here:
Session.transact is the one place a command leaves the host, and it refuses every operation outside READ_ONLY
before anything reaches the transport; no data phase is ever sent to the device (writes: mtp_write.Writer,
a subclass with its own checks). A transport is any object with write(bytes) -> libusb rc,
read(size) -> (rc, bytes) and close() (UsbTransport, or a fake in the tests).
"""
import ctypes
import ctypes.util
from pathlib import Path
import struct
import subprocess

from xy_forever import no_device

OP_XY_VID = 0x2367
READ_ONLY = {0x1001: 'GetDeviceInfo', 0x1002: 'OpenSession', 0x1003: 'CloseSession', 0x1004: 'GetStorageIDs',
             0x1005: 'GetStorageInfo', 0x1007: 'GetObjectHandles', 0x1008: 'GetObjectInfo', 0x1009: 'GetObject'}
MTP_CLASSES = (6, 255)          # still image (PTP), vendor class (the OP-XY's MTP, docs/HARDWARE.md)
ROOT = 0xFFFFFFFF               # GetObjectHandles parent: the storage root
OK, SESSION_ALREADY_OPEN = 0x2001, 0x201E
FOLDER = 0x3001                 # ObjectFormat Association
LIBUSB_ERROR_ACCESS, LIBUSB_ERROR_BUSY = -3, -6
CHUNK = 0x10000


class MtpError(Exception):
    """One line for the user; never a traceback."""


class Refused(MtpError):
    pass


def ptp_string(data, at):
    n = data[at]
    return data[at + 1:at + 1 + 2 * n].decode('utf-16-le', 'replace').rstrip('\0'), at + 1 + 2 * n


# the session (pure Python over any transport)

class Session:
    def __init__(self, transport):
        self.transport, self.tid, self.answer = transport, 0, ()

    def transact(self, code, *params):
        """(response code, data) of one read-only operation. The choke point: any other code is refused here
        (writes go only through mtp_write.Writer, which checks every path before its own command)."""
        if code not in READ_ONLY:
            raise Refused('MTP operation 0x%04x refused: this client only reads' % code)
        self._command(READ_ONLY[code], code, params)
        return self._response(READ_ONLY[code])

    def _command(self, name, code, params):
        self.tid += 1
        cmd = struct.pack('<IHHI', 12 + 4 * len(params), 1, code, self.tid) + b''.join(struct.pack('<I', p)
                                                                                       for p in params)
        rc = self.transport.write(cmd)
        if rc:
            raise MtpError('%s: command not sent (libusb %d)' % (name, rc))

    def _response(self, name):
        """(response code, data in) of the transaction just sent; self.answer: the response's parameters."""
        payload, pending = b'', b''
        while True:
            got, pending = pending or self.read(name), b''
            if len(got) < 12:
                raise MtpError('%s: short container (%d bytes)' % (name, len(got)))
            length, kind, rcode, _ = struct.unpack_from('<IHHI', got)
            if kind == 2:                                       # data phase (device to host)
                data = bytearray(got[12:])
                while len(data) < length - 12:
                    data += self.read(name, 'data stopped at %d/%d bytes' % (len(data), length - 12))
                payload, pending = bytes(data[:length - 12]), bytes(data[length - 12:])
            elif kind == 3:                                     # response
                n = max(0, min(length, len(got)) - 12) // 4
                self.answer = struct.unpack_from('<%dI' % n, got, 12)
                return rcode, payload
            else:
                raise MtpError('%s: unexpected container type %d' % (name, kind))

    def read(self, name, what='no answer'):
        for _ in range(3):                                      # a zero-length packet ends a data phase
            rc, got = self.transport.read(CHUNK)
            if rc:
                raise MtpError('%s: %s (libusb %d)' % (name, what, rc))
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
        if rcode == SESSION_ALREADY_OPEN:                       # as go-mtpfs: close it, open again
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
                out.append((h, ptp_string(info, 52)[0], struct.unpack_from('<H', info, 4)[0],
                            struct.unpack_from('<I', info, 8)[0]))
        return out

    def find(self, path):
        """The handle of /a/b/c, walked from each storage's root by names; None if it is not there."""
        for storage in self.storages():
            parent = ROOT
            for i, part in enumerate(p for p in path.split('/') if p):
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


# libusb (ctypes)

def load_libusb():
    try:
        prefix = subprocess.run(['brew', '--prefix', 'libusb'], capture_output=True, text=True).stdout.strip()
    except OSError:
        prefix = ''
    for path in ([prefix + '/lib/libusb-1.0.dylib'] if prefix else []) + [ctypes.util.find_library('usb-1.0') or '']:
        if path:
            try:
                return ctypes.CDLL(path)
            except OSError:
                pass
    raise MtpError('libusb-1.0 not found (brew install libusb)')


class DeviceDescriptor(ctypes.Structure):
    _fields_ = [('bLength', ctypes.c_uint8), ('bDescriptorType', ctypes.c_uint8), ('bcdUSB', ctypes.c_uint16),
                ('bDeviceClass', ctypes.c_uint8), ('bDeviceSubClass', ctypes.c_uint8),
                ('bDeviceProtocol', ctypes.c_uint8), ('bMaxPacketSize0', ctypes.c_uint8),
                ('idVendor', ctypes.c_uint16), ('idProduct', ctypes.c_uint16), ('bcdDevice', ctypes.c_uint16),
                ('iManufacturer', ctypes.c_uint8), ('iProduct', ctypes.c_uint8),
                ('iSerialNumber', ctypes.c_uint8), ('bNumConfigurations', ctypes.c_uint8)]


class Endpoint(ctypes.Structure):
    _fields_ = [('bLength', ctypes.c_uint8), ('bDescriptorType', ctypes.c_uint8),
                ('bEndpointAddress', ctypes.c_uint8), ('bmAttributes', ctypes.c_uint8),
                ('wMaxPacketSize', ctypes.c_uint16), ('bInterval', ctypes.c_uint8),
                ('bRefresh', ctypes.c_uint8), ('bSynchAddress', ctypes.c_uint8),
                ('extra', ctypes.c_void_p), ('extra_length', ctypes.c_int)]


class AltSetting(ctypes.Structure):
    _fields_ = [('bLength', ctypes.c_uint8), ('bDescriptorType', ctypes.c_uint8),
                ('bInterfaceNumber', ctypes.c_uint8), ('bAlternateSetting', ctypes.c_uint8),
                ('bNumEndpoints', ctypes.c_uint8), ('bInterfaceClass', ctypes.c_uint8),
                ('bInterfaceSubClass', ctypes.c_uint8), ('bInterfaceProtocol', ctypes.c_uint8),
                ('iInterface', ctypes.c_uint8), ('endpoint', ctypes.POINTER(Endpoint)),
                ('extra', ctypes.c_void_p), ('extra_length', ctypes.c_int)]


class Interface(ctypes.Structure):
    _fields_ = [('altsetting', ctypes.POINTER(AltSetting)), ('num_altsetting', ctypes.c_int)]


class Config(ctypes.Structure):
    _fields_ = [('bLength', ctypes.c_uint8), ('bDescriptorType', ctypes.c_uint8),
                ('wTotalLength', ctypes.c_uint16), ('bNumInterfaces', ctypes.c_uint8),
                ('bConfigurationValue', ctypes.c_uint8), ('iConfiguration', ctypes.c_uint8),
                ('bmAttributes', ctypes.c_uint8), ('MaxPower', ctypes.c_uint8),
                ('interface', ctypes.POINTER(Interface)), ('extra', ctypes.c_void_p), ('extra_length', ctypes.c_int)]


def interfaces(lib, dev):
    """[{number, cls: (class, subclass, protocol), endpoints: [(address, type, size)]}] of the active config."""
    cfg = ctypes.POINTER(Config)()
    if lib.libusb_get_active_config_descriptor(dev, ctypes.byref(cfg)) or not cfg:
        return []
    out = []
    for i in range(cfg.contents.bNumInterfaces):
        alt = cfg.contents.interface[i].altsetting[0]
        out.append({'number': alt.bInterfaceNumber,
                    'cls': (alt.bInterfaceClass, alt.bInterfaceSubClass, alt.bInterfaceProtocol),
                    'endpoints': [(e.bEndpointAddress, e.bmAttributes & 3, e.wMaxPacketSize)
                                  for e in (alt.endpoint[k] for k in range(alt.bNumEndpoints))]})
    lib.libusb_free_config_descriptor(cfg)
    return out


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
    raise MtpError('the OP-XY is on USB but not in MTP mode (no MTP interface): start the dev menu\'s MTP '
                   '(COM held while switching on, Track 3, Track 2)')


def usb_problem(step, rc):
    if rc in (LIBUSB_ERROR_ACCESS, LIBUSB_ERROR_BUSY):
        return ('the OP-XY\'s MTP interface is held by another program: quit OpenMTP, Image Capture and Android '
                'File Transfer, then retry')
    return '%s failed (libusb %d)' % (step, rc)


class UsbTransport:
    def __init__(self, lib, ctx, handle, number, ep_in, ep_out, timeout_ms):
        self.lib, self.ctx, self.handle, self.number = lib, ctx, handle, number
        self.ep_in, self.ep_out, self.timeout_ms = ep_in, ep_out, timeout_ms

    def transfer(self, ep, buf):
        moved = ctypes.c_int()
        rc = self.lib.libusb_bulk_transfer(self.handle, ctypes.c_uint8(ep), buf, len(buf.raw), ctypes.byref(moved),
                                           ctypes.c_uint(self.timeout_ms))
        return rc, buf.raw[:moved.value]

    def write(self, data):
        return self.transfer(self.ep_out, ctypes.create_string_buffer(data, len(data)))[0]

    def read(self, size):
        return self.transfer(self.ep_in, ctypes.create_string_buffer(size))

    def close(self):
        if self.handle:
            self.lib.libusb_release_interface(self.handle, self.number)
            self.lib.libusb_close(self.handle)
            self.handle = None
            self.lib.libusb_exit(self.ctx)


def open_op_xy(timeout_ms=5000, log=None):
    """A UsbTransport on the first OP-XY in MTP mode; MtpError (one line) otherwise. log: each device's
    interfaces as they are listed (mtp_probe prints them)."""
    no_device.refuse('USB MTP (libusb)')
    lib = load_libusb()
    ctx = ctypes.c_void_p()
    if lib.libusb_init(ctypes.byref(ctx)):
        raise MtpError('libusb_init failed')
    devs = ctypes.POINTER(ctypes.c_void_p)()
    n = lib.libusb_get_device_list(ctx, ctypes.byref(devs))
    handle = ctypes.c_void_p()
    try:
        devices = []
        for i in range(max(n, 0)):
            d, dev = DeviceDescriptor(), ctypes.c_void_p(devs[i])
            lib.libusb_get_device_descriptor(dev, ctypes.byref(d))
            if d.idVendor == OP_XY_VID:
                found = interfaces(lib, dev)
                if log:
                    log('device %04x:%04x' % (d.idVendor, d.idProduct))
                    for f in found:
                        log('interface %d class %d/%d/%d endpoints %s' % ((f['number'],) + f['cls'] + (
                            ', '.join('%02x/%d/%d' % e for e in f['endpoints']),)))
                devices.append((dev, (d.idVendor, d.idProduct), found))
        dev, _, (number, ep_in, ep_out) = choose(devices)
        rc = lib.libusb_open(dev, ctypes.byref(handle))
        if rc:
            handle = None
            raise MtpError(usb_problem('libusb_open', rc))
        rc = lib.libusb_claim_interface(handle, number)
        if rc:
            raise MtpError(usb_problem('claim interface %d' % number, rc))
    except MtpError:
        if handle:
            lib.libusb_close(handle)
        if n >= 0:
            lib.libusb_free_device_list(devs, 1)
        lib.libusb_exit(ctx)
        raise
    lib.libusb_free_device_list(devs, 1)
    return UsbTransport(lib, ctx, handle, number, ep_in, ep_out, timeout_ms)
