#!/usr/bin/env python3
"""Real MTP on the composite's MTP interface (test build xyf-mtp-usb), from macOS, no clicks.

With the composite up (--no-trial when it already is; else the trial of scripts/mtp_usb_probe.py first), over MTP
(interface 2) and the serial (the composite's CDC), HELLO between the steps:
  1. OpenSession (SessionAlreadyOpen: CloseSession, OpenSession, as go-mtpfs), GetDeviceInfo, GetStorageIDs,
     GetStorageInfo, the root (parent 0xFFFFFFFF), then /yaffs2/xy-forever/ by GetObjectInfo names.
  2. config.txt over MTP (GetObject) against a serial read of the same file.
  3. a 3 MiB random file uploaded into xy-forever/tmp/ (SendObjectInfo + SendObject); one serial file read DURING
     the upload (expect busy or success, never a hang); read back over serial, downloaded again over MTP
     (GetObject), both compared; deleted over MTP (DeleteObject), the delete confirmed over serial.
  4. a folder created in tmp/ (SendObjectInfo, association) and removed (DeleteObject), confirmed over serial.
  5. the write rule (since 1.13): a file written and deleted under user/ and in
     the root (serial confirms both); refused: bootloader.dev written (SendObjectInfo of its name), renamed
     (SetObjectPropValue) and deleted (0x200D), a write into upgrade/ (or making upgrade/ when absent) and a root
     file renamed to "upgrade" (Access_Denied 0x200F; tmp/t2d-up.txt renamed to "upgrade" is accepted); serial confirms the marker and upgrade/ unchanged.
  5c. speed: 200 GetObjectInfo + GetObjectPropValue pairs on the children of
     xy-forever/mods/opxy-1.2.1/0.0.6-370a949e96aa (operations per second); 8 MiB up and down (3 MiB in step 3).
  5b. T2c object properties: GetObjectPropsSupported, ObjectSize and ObjectFileName (GetObjectPropValue) of
     config.txt and tmp/ against serial ls, a file renamed in tmp/ by SetObjectPropValue (serial sees the new name,
     not the old).
  6. CloseSession; the device's counters: the session (diag.mtp op 5), the operations log (op 6), the driver (op 1).
--ops-log: only HELLO, the session's state and the operations log (after a real client such as OpenMTP).
Evidence (log.txt, result.json) in ../private/xy-forever/sessions/<date>-mtp-files/run-NNN/. Every container's
first bytes are logged (hex). PASS/FAIL lines; exit 0 only if every check passed. It never retries by itself.
Needs pyusb and libusb, as mtp_usb_probe.py.
"""
import argparse
import datetime
import hashlib
import json
import random
import struct
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import xyf_host as H  # noqa: E402
from xy_forever import usb_proto as P  # noqa: E402
from xy_forever.paths import SESSIONS  # noqa: E402
import mtp_usb_probe as probe  # noqa: E402

T_MTP = 0x61                        # diag.mtp (usb_service.cpp mtp_tunnel): op 5 state, op 6 the operations log
STATE_WORDS = ('stage', 'bytes_in', 'bytes_out', 'longest_pass_ms', 'device_status', 'link', 'steps', 'requests',
               'busy', 'stalls', 'refused_busy', 'operations', 'longest_op_ms', 'log_count',
               # speed (2026-11-15): GetObjectInfo from the listing, read/write requests to A and their bytes, the
               # operations by time (command to response), the composite's longest menu-loop pass
               'cache_hits', 'data_requests', 'data_bytes', 'ops_under_2ms', 'ops_2_4ms', 'ops_5_20ms', 'ops_over_20ms',
               'longest_main_pass_ms')
UPLOAD_BYTES, BIG_BYTES = 3 << 20, 8 << 20
SPEED_FOLDER = ('mods', 'opxy-1.2.1', '0.0.6-370a949e96aa')       # below xy-forever/
SPEED_PAIRS = 200
OK, BUSY, ACCESS_DENIED, WRITE_PROTECTED, ALREADY_OPEN = 0x2001, 0x2019, 0x200F, 0x200D, 0x201E
STORAGE = 0x00010001
ROOT_PARENT = 0xFFFFFFFF
CONFIG_PATH = '/yaffs2/xy-forever/config.txt'
UPLOAD_NAME, FOLDER_NAME = 't2b-upload.bin', 't2b-folder'
RENAME_FROM, RENAME_TO = 't2c-rename-a.txt', 't2c-rename-b.txt'
OBJECT_SIZE, OBJECT_FILE_NAME = 0xDC04, 0xDC07
NAMES = {0x1001: 'GetDeviceInfo', 0x1002: 'OpenSession', 0x1003: 'CloseSession', 0x1004: 'GetStorageIDs',
         0x1005: 'GetStorageInfo', 0x1007: 'GetObjectHandles', 0x1008: 'GetObjectInfo', 0x1009: 'GetObject',
         0x100B: 'DeleteObject', 0x100C: 'SendObjectInfo', 0x100D: 'SendObject', 0x1014: 'GetDevicePropDesc',
         0x1015: 'GetDevicePropValue', 0x101B: 'GetPartialObject', 0x9801: 'GetObjectPropsSupported',
         0x9802: 'GetObjectPropDesc', 0x9803: 'GetObjectPropValue', 0x9804: 'SetObjectPropValue',
         0x9805: 'GetObjectPropList',
         0x95C1: 'GetPartialObject64', 0x9F01: 'test sink'}
DATA = {0: '-', 1: 'data in', 2: 'data out', 3: 'both', 4: 'cancelled'}


def new_run_dir():
    base = SESSIONS / ('%s-mtp-files' % datetime.date.today().isoformat())
    n = 1
    while (base / ('run-%03d' % n)).exists():
        n += 1
    return base / ('run-%03d' % n)


class Mtp:
    """MTP over pyusb, one transaction at a time; containers logged (first bytes, hex)."""

    def __init__(self, dev, log):
        self.dev, self.log, self.tx = dev, log, 0

    def transact(self, code, params=(), data_out=None, timeout_s=10.0, quiet=False):
        """(data or None, response code, response params). Data comes back as one bytes object."""
        self.tx += 1
        command = probe.container(1, code, self.tx, params)
        self.log('out: %s %s: %s' % (NAMES.get(code, '0x%04x' % code), list(params), command.hex()))
        ms = int(timeout_s * 1000)
        self.dev.write(probe.EP_OUT, command, timeout=ms)
        if data_out is not None:
            head = probe.container(2, code, self.tx, (), len(data_out))
            self.log('out: data container, %d bytes: %s' % (len(data_out), (head + data_out[:32]).hex()))
            self.dev.write(probe.EP_OUT, head + data_out, timeout=ms)
        data, buf, deadline = None, bytearray(), time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            buf += bytes(self.dev.read(probe.EP_IN, 1 << 16, timeout=ms))
            while len(buf) >= 12 and len(buf) >= struct.unpack_from('<I', buf)[0]:
                length, kind, rcode, rtx = struct.unpack_from('<IHHI', buf)
                if length < 12:
                    raise IOError('container length %d: %s' % (length, bytes(buf[:64]).hex()))
                if not quiet or kind == 3:
                    self.log('in: type %d code 0x%04x tx %d, %d bytes: %s%s' % (
                        kind, rcode, rtx, length, bytes(buf[:min(length, 64)]).hex(), '...' if length > 64 else ''))
                body = bytes(buf[12:length])
                del buf[:length]
                if kind == 2:
                    data = body
                elif kind == 3:
                    if rtx != self.tx:
                        raise IOError('response for transaction %d, expected %d' % (rtx, self.tx))
                    return data, rcode, list(struct.unpack_from('<%dI' % (len(body) // 4), body))
        raise TimeoutError('no MTP response to 0x%04x' % code)


def object_info(name, size, parent, folder=False):
    """An ObjectInfo dataset (PTP 1.1 5.5.2)."""
    def string(text):
        units = text.encode('utf-16-le')
        return bytes([len(units) // 2 + 1]) + units + b'\0\0' if text else b'\0'
    head = struct.pack('<IHHIHIIIIIIIHII', STORAGE, 0x3001 if folder else 0x3000, 0, size, 0, 0, 0, 0, 0, 0, 0,
                       parent, 1 if folder else 0, 0, 0)
    return head + string(name) + string('') + string('') + string('')


def probe_string(text):
    """A PTP string (u8 count with the NUL, UTF-16LE)."""
    units = text.encode('utf-16-le') + b'\0\0'
    return bytes([len(units) // 2]) + units


def info_name(data):
    """(name, format, size, parent) from an ObjectInfo dataset."""
    fmt, size, parent = struct.unpack_from('<H', data, 4)[0], struct.unpack_from('<I', data, 8)[0], \
        struct.unpack_from('<I', data, 38)[0]
    name, _ = probe.mtp_string(data, 52)
    return name, fmt, size, parent


def device_words(dev):
    """diag.mtp op 5 (the session) and op 6 (the operations log), decoded."""
    code, body = probe.raw_call(dev, T_MTP, bytes([5]), 5.0)
    state = dict(zip(STATE_WORDS, struct.unpack_from('<%dI' % len(STATE_WORDS), body))) if code == 0 else None
    code, body = probe.raw_call(dev, T_MTP, bytes([6]), 5.0)
    ops = []
    if code == 0 and len(body) >= 4:
        for at in range(4, len(body) - 19, 20):       # T2c: 5 words: op | response, 3 parameters, data | count | ms
            first, p1, p2, p3, last = struct.unpack_from('<5I', body, at)
            op, response, count = first & 0xffff, first >> 16, (last >> 4) & 0xf
            ops.append(dict(ms=last >> 8, op='0x%04x %s' % (op, NAMES.get(op, '')),
                            params=['0x%08x' % v for v in (p1, p2, p3)[:min(count, 3)]] + (['...'] if count > 3 else []),
                            response='0x%04x' % response if response else '-', data=DATA.get(last & 0xf, last & 0xf)))
    return state, ops


def print_ops(run, state, ops):
    run.log('session: %s' % json.dumps(state))
    run.log('operations log (oldest first, %d shown):' % len(ops))
    for e in ops:
        run.log('  %8d ms  %-32s params %-36s response %s  %s' % (e['ms'], e['op'], ' '.join(e['params']) or '-',
                                                                   e['response'], e['data']))


def ops_log_only():
    dev, port, hello = H.connect(10.0)
    run = probe.Run(new_run_dir())
    run.log('serial %s: %s' % (port, hello))
    state, ops = device_words(dev)
    print_ops(run, state, ops)
    (run.out / 'result.json').write_text(json.dumps(dict(state=state, ops=ops), indent=1) + '\n')
    raise SystemExit(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--no-trial', action='store_true', help='the composite is already up: skip the trial')
    parser.add_argument('--ops-log', action='store_true', help='only print the MTP operations log and counters')
    parser.add_argument('--seed', type=int, default=3502)
    args = parser.parse_args()
    if args.ops_log:
        ops_log_only()
    try:
        import usb.core
        import usb.util
        usb.core.find(idVendor=0xffff, idProduct=0xffff)
    except Exception as error:                     # noqa: BLE001
        raise SystemExit('pyusb with libusb is needed (brew install libusb; python3 -m pip install pyusb): %s' % error)
    run = probe.Run(new_run_dir())
    result = dict(seed=args.seed)
    blob = random.Random(args.seed).randbytes(UPLOAD_BYTES)
    run.log('evidence in %s' % run.out)

    if not args.no_trial:                         # the trial over the stock serial (as mtp_usb_probe.py step 1)
        dev, port, hello = H.connect(10.0)
        if not run.check('hello names the test build', 'mode=mtp-usb' in hello, hello):
            probe.finish(run, result)
        code, _ = probe.raw_call(dev, probe.T_MTP_USB, bytes([0]), 15.0)
        dev.t.close()
        if not run.check('trial (flag written, restart)', code == 0, P.status_name(code)):
            probe.finish(run, result)
    usb_dev = probe.wait_usb(usb.core, True, probe.APPEAR_S if not args.no_trial else 3.0)
    if not run.check('composite %s present' % probe.USB_IDS, usb_dev is not None):
        probe.finish(run, result)
    dev, port, hello = H.connect(12.0)
    run.check('HELLO over the composite', any(m in hello for m in ('mode=mtp-usb', 'mode=product')),
              '%s: %s' % (port, hello))

    def hello_check(where):
        code, text = dev.hello()
        return run.check('HELLO %s' % where, code == 0, text if code else '')

    if not run.check('claim interface 2 (MTP)', probe.claim(usb, usb_dev, run, probe.MTP_INTERFACE)):
        probe.finish(run, result)
    m = Mtp(usb_dev, run.log)
    opened = False
    try:
        # 1. session, storage, the walk to xy-forever/
        _, rc, _ = m.transact(0x1002, (1,))
        if rc == ALREADY_OPEN:
            m.transact(0x1003)
            _, rc, _ = m.transact(0x1002, (1,))
        opened = run.check('OpenSession', rc == OK, '0x%04x' % rc)
        data, rc, _ = m.transact(0x1001)
        info = probe.parse_device_info(data) if data else None
        run.check('GetDeviceInfo', rc == OK and info is not None, json.dumps(info))
        data, rc, _ = m.transact(0x1004)
        ids = list(struct.unpack_from('<%dI' % struct.unpack_from('<I', data)[0], data, 4)) if data else []
        run.check('GetStorageIDs', rc == OK and ids == [STORAGE], str(ids))
        data, rc, _ = m.transact(0x1005, (STORAGE,))
        if data:
            kind, fs, access, cap, free = struct.unpack_from('<HHHQQ', data)
            result['storage'] = dict(type=kind, filesystem=fs, access=access, capacity=cap, free=free,
                                     description=probe.mtp_string(data, 26)[0])
        run.check('GetStorageInfo', rc == OK and data is not None, json.dumps(result.get('storage')))

        def listing(parent):
            data, rc, _ = m.transact(0x1007, (STORAGE, 0, parent), timeout_s=20.0)
            handles = list(struct.unpack_from('<%dI' % struct.unpack_from('<I', data)[0], data, 4)) if data else []
            names = {}
            for h in handles:
                d, r, _ = m.transact(0x1008, (h,), quiet=True)
                if r == OK and d:
                    name, fmt, size, _ = info_name(d)
                    names[name] = (h, fmt, size)
            return rc, names

        rc, root = listing(ROOT_PARENT)
        run.check('root listed (parent 0xFFFFFFFF)', rc == OK and 'xy-forever' in root,
                  ', '.join(sorted(root)))
        result['root'] = sorted(root)
        xyf = root['xy-forever'][0]
        rc, inside = listing(xyf)
        xyf_inside = inside
        run.check('xy-forever/ listed', rc == OK and 'config.txt' in inside and 'tmp' in inside,
                  ', '.join(sorted(inside)))
        hello_check('after the walk')

        # 2. config.txt both ways
        code, serial_config = dev.read_all(CONFIG_PATH)
        data, rc, _ = m.transact(0x1009, (inside['config.txt'][0],))
        run.check('config.txt over MTP equals the serial read', rc == OK and code == 0 and data == serial_config,
                  'MTP %d bytes, serial %d bytes (%s)' % (len(data or b''), len(serial_config), P.status_name(code)))

        # 3. 3 MiB up, a serial read during it, back over serial and MTP, delete
        tmp = inside['tmp'][0]
        path = '/yaffs2/xy-forever/tmp/' + UPLOAD_NAME
        _, rc, params = m.transact(0x100C, (STORAGE, tmp), object_info(UPLOAD_NAME, len(blob), tmp))
        run.check('SendObjectInfo (3 MiB into tmp/)', rc == OK, '0x%04x %s' % (rc, params))
        during = {}

        def serial_during():
            time.sleep(1.0)
            t = time.monotonic()
            try:
                code, body = dev.read_file(CONFIG_PATH)
                during.update(code=P.status_name(code), seconds=round(time.monotonic() - t, 3), bytes=len(body))
            except Exception as error:                 # noqa: BLE001
                during.update(error='%s: %s' % (type(error).__name__, error), seconds=round(time.monotonic() - t, 3))

        reader = threading.Thread(target=serial_during)
        reader.start()
        t = time.monotonic()
        _, rc, _ = m.transact(0x100D, (), blob, timeout_s=120.0)
        up_s = time.monotonic() - t
        reader.join(20.0)
        result['upload'] = dict(seconds=round(up_s, 2), kib_s=round(len(blob) / 1024 / up_s, 1))
        run.check('SendObject 3 MiB', rc == OK, '0x%04x, %.2f s (%.0f KiB/s)' % (rc, up_s, len(blob) / 1024 / up_s))
        result['serial_during_upload'] = during
        run.check('serial read during the upload: busy or ok, no hang', not reader.is_alive() and
                  during.get('code') in ('ok', 'busy'), json.dumps(during))
        hello_check('after the upload')
        t = time.monotonic()
        code, back = dev.read_all(path)
        run.check('upload read back over serial equals', code == 0 and back == blob,
                  '%s, %d bytes, %.2f s, sha256 %s' % (P.status_name(code), len(back), time.monotonic() - t,
                                                      hashlib.sha256(back).hexdigest()[:16]))
        rc, in_tmp = listing(tmp)
        handle = in_tmp.get(UPLOAD_NAME, (0,))[0]
        t = time.monotonic()
        data, rc, _ = m.transact(0x1009, (handle,), timeout_s=120.0, quiet=True)
        down_s = time.monotonic() - t
        result['download'] = dict(seconds=round(down_s, 2), kib_s=round(len(blob) / 1024 / down_s, 1))
        run.check('GetObject 3 MiB equals', rc == OK and data == blob,
                  '0x%04x, %d bytes, %.2f s (%.0f KiB/s)' % (rc, len(data or b''), down_s, len(blob) / 1024 / down_s))
        _, rc, _ = m.transact(0x100B, (handle,))
        code, _ = dev.read_file(path)
        run.check('DeleteObject, gone over serial', rc == OK and code == P.STATUS_NAMES.index('not_found'),
                  '0x%04x, serial %s' % (rc, P.status_name(code)))
        hello_check('after the delete')

        # 4. a folder
        _, rc, params = m.transact(0x100C, (STORAGE, tmp), object_info(FOLDER_NAME, 0, tmp, folder=True))
        code, entries = dev.list_dir('/yaffs2/xy-forever/tmp')
        seen = any(e[2] == FOLDER_NAME.encode() for e in entries)
        run.check('folder created (serial sees it)', rc == OK and seen, '0x%04x %s' % (rc, params))
        _, rc, _ = m.transact(0x100B, (params[2] if len(params) > 2 else 0,))
        code, entries = dev.list_dir('/yaffs2/xy-forever/tmp')
        run.check('folder removed (serial)', rc == OK and not any(e[2] == FOLDER_NAME.encode() for e in entries),
                  '0x%04x' % rc)

        # 5. the write rule (since 1.13): open everywhere but bootloader.dev and upgrade/
        def write_and_delete(label, parent, folder_path, name):
            body = b'xyf write rule ' + name.encode()
            _, rc, params = m.transact(0x100C, (STORAGE, parent), object_info(name, len(body), parent))
            handle = params[2] if rc == OK and len(params) > 2 else 0
            rc2 = m.transact(0x100D, (), body)[1] if handle else None
            code, back = dev.read_file(folder_path + name)
            run.check('%s: written (serial reads it)' % label, rc == OK and rc2 == OK and code == 0 and back == body,
                      '0x%04x, 0x%04x, serial %s' % (rc, rc2 or 0, P.status_name(code)))
            rc3 = m.transact(0x100B, (handle,))[1] if handle else None
            code, _ = dev.read_file(folder_path + name)
            run.check('%s: deleted (serial: not_found)' % label, rc3 == OK and code == P.STATUS_NAMES.index('not_found'),
                      '0x%04x, serial %s' % (rc3 or 0, P.status_name(code)))
        if 'user' in root:
            write_and_delete('under user/', root['user'][0], '/yaffs2/user/', 't2d-user.bin')
        else:
            run.log('no user/ folder in the root: the user/ write not tried')
        write_and_delete('in the root', ROOT_PARENT, '/yaffs2/', 't2d-root.bin')
        code, before_root = dev.list_dir('/yaffs2')
        inbox_code, inbox_before = dev.list_dir('/yaffs2/upgrade')
        code, marker_before = dev.read_file('/yaffs2/bootloader.dev')
        _, rc, _ = m.transact(0x100C, (STORAGE, ROOT_PARENT), object_info('bootloader.dev', 3, ROOT_PARENT))
        run.check('write of bootloader.dev refused', rc == ACCESS_DENIED, '0x%04x' % rc)
        if 'bootloader.dev' in root:
            _, rc, _ = m.transact(0x9804, (root['bootloader.dev'][0], OBJECT_FILE_NAME), probe_string('t2d-marker'))
            run.check('rename of bootloader.dev refused', rc == ACCESS_DENIED, '0x%04x' % rc)
            _, rc, _ = m.transact(0x100B, (root['bootloader.dev'][0],))
            run.check('delete of /yaffs2/bootloader.dev refused', rc == WRITE_PROTECTED, '0x%04x' % rc)
        else:
            run.check('bootloader.dev listed in the root', False, 'not listed: rename and delete refusals not tried')
        if 'upgrade' in root:
            _, rc, _ = m.transact(0x100C, (STORAGE, root['upgrade'][0]), object_info('t2d.tfw', 3, root['upgrade'][0]))
            run.check('write into upgrade/ refused', rc == ACCESS_DENIED, '0x%04x' % rc)
        else:
            _, rc, _ = m.transact(0x100C, (STORAGE, ROOT_PARENT), object_info('upgrade', 0, ROOT_PARENT, folder=True))
            run.check('making upgrade/ refused (none on the device)', rc == ACCESS_DENIED, '0x%04x' % rc)
        # 188: the rule protects /yaffs2/upgrade only: a file elsewhere may be called "upgrade" (tmp/ accepts it;
        # the -011 run's 0x2001 here was this case, the device right), a root file renamed to it is refused.
        for parent, where, want in ((tmp, 'in tmp/ accepted', OK), (ROOT_PARENT, 'in the root refused', ACCESS_DENIED)):
            _, rc, params = m.transact(0x100C, (STORAGE, parent), object_info('t2d-up.txt', 3, parent))
            moved = params[2] if rc == OK and len(params) > 2 else 0
            if not moved:
                run.check('rename to "upgrade" ' + where, False, 'probe file not made: 0x%04x' % rc)
                continue
            m.transact(0x100D, (), b't2d')
            _, rc, _ = m.transact(0x9804, (moved, OBJECT_FILE_NAME), probe_string('upgrade'))
            run.check('rename to "upgrade" ' + where, rc == want, '0x%04x' % rc)
            m.transact(0x100B, (moved,))
        code, after_root = dev.list_dir('/yaffs2')
        inbox_code2, inbox_after = dev.list_dir('/yaffs2/upgrade')
        code, marker_after = dev.read_file('/yaffs2/bootloader.dev')
        run.check('serial: the root, bootloader.dev and upgrade/ unchanged', before_root == after_root and
                  marker_before == marker_after and (inbox_code, inbox_before) == (inbox_code2, inbox_after),
                  'root %d entries, upgrade/ %s' % (len(after_root), P.status_name(inbox_code2)))
        # 5b. object properties (T2c); each sub-step on its own: an exception fails that sub-step only
        def substep(label, body):
            try:
                body()
            except Exception as error:                 # noqa: BLE001
                run.check(label, False, '%s: %s' % (type(error).__name__, error))

        def prop(handle, code):
            data, rc, _ = m.transact(0x9803, (handle, code))
            if rc != OK or data is None:
                return rc, None
            return rc, struct.unpack_from('<Q', data)[0] if code == OBJECT_SIZE else probe.mtp_string(data, 0)[0]

        def props_supported():
            data, rc, _ = m.transact(0x9801, (0x3000,))
            props = list(struct.unpack_from('<%dH' % struct.unpack_from('<I', data)[0], data, 4)) if data else []
            run.check('GetObjectPropsSupported', rc == OK and {OBJECT_SIZE, OBJECT_FILE_NAME} <= set(props),
                      ' '.join('0x%04x' % v for v in props))

        def config_props():
            code, body = dev.read_all(CONFIG_PATH)
            rc1, size = prop(xyf_inside['config.txt'][0], OBJECT_SIZE)
            rc2, name = prop(xyf_inside['config.txt'][0], OBJECT_FILE_NAME)
            run.check('config.txt ObjectSize, ObjectFileName (serial)', code == 0 and rc1 == OK and rc2 == OK and
                      name == 'config.txt' and size == len(body), '0x%04x %s, 0x%04x %r; serial %s, %d bytes' % (
                          rc1, size, rc2, name, P.status_name(code), len(body)))

        def tmp_props():
            code, entries = dev.list_dir('/yaffs2/xy-forever')
            rc1, size = prop(tmp, OBJECT_SIZE)
            rc2, name = prop(tmp, OBJECT_FILE_NAME)
            run.check('tmp/ ObjectSize 0, ObjectFileName (serial ls)', rc1 == OK and rc2 == OK and name == 'tmp' and
                      size == 0 and code == 0 and any(e[2] == b'tmp' for e in entries),
                      '0x%04x %s, 0x%04x %r' % (rc1, size, rc2, name))

        def rename():
            _, rc, params = m.transact(0x100C, (STORAGE, tmp), object_info(RENAME_FROM, 5, tmp))
            renamed = params[2] if rc == OK and len(params) > 2 else 0
            if not run.check('rename: upload %s' % RENAME_FROM, renamed != 0, '0x%04x %s' % (rc, params)):
                return
            try:
                _, rc, _ = m.transact(0x100D, (), b't2c\r\n')
                _, rc_set, _ = m.transact(0x9804, (renamed, OBJECT_FILE_NAME), probe_string(RENAME_TO))
                code, entries = dev.list_dir('/yaffs2/xy-forever/tmp')
                names = {e[2] for e in entries} if code == 0 else set()
                _, name = prop(renamed, OBJECT_FILE_NAME)
                run.check('rename by SetObjectPropValue (serial sees it)', rc == OK and rc_set == OK and
                          RENAME_TO.encode() in names and RENAME_FROM.encode() not in names and name == RENAME_TO,
                          'upload 0x%04x, set 0x%04x, now %r' % (rc, rc_set, name))
            finally:
                _, rc, _ = m.transact(0x100B, (renamed,))
                run.log('renamed file deleted: 0x%04x' % rc)

        substep('GetObjectPropsSupported', props_supported)
        substep('config.txt ObjectSize, ObjectFileName (serial)', config_props)
        substep('tmp/ ObjectSize 0, ObjectFileName (serial ls)', tmp_props)
        substep('rename by SetObjectPropValue (serial sees it)', rename)

        # 5c. speed: GetObjectInfo + GetObjectPropValue pairs, as OpenMTP asks per entry; 8 MiB both ways
        def speed_pairs():
            parent, names = xyf, xyf_inside
            for part in SPEED_FOLDER:
                parent = names[part][0]
                _, names = listing(parent)
            handles = [h for h, _, _ in names.values()]
            t = time.monotonic()
            bad = 0
            for i in range(SPEED_PAIRS):
                h = handles[i % len(handles)]
                bad += m.transact(0x1008, (h,), quiet=True)[1] != OK
                bad += m.transact(0x9803, (h, OBJECT_SIZE), quiet=True)[1] != OK
            seconds = time.monotonic() - t
            result['speed_pairs'] = dict(pairs=SPEED_PAIRS, seconds=round(seconds, 3),
                                         ops_per_s=round(2 * SPEED_PAIRS / seconds, 1), children=len(handles))
            run.check('speed: %d GetObjectInfo + GetObjectPropValue pairs' % SPEED_PAIRS, bad == 0,
                      '%d children, %.2f s, %.0f operations/s (%.2f ms each)' % (
                          len(handles), seconds, 2 * SPEED_PAIRS / seconds, seconds * 500 / SPEED_PAIRS))

        def speed_big():
            big = random.Random(args.seed + 1).randbytes(BIG_BYTES)
            _, rc, params = m.transact(0x100C, (STORAGE, tmp), object_info('t2d-8mib.bin', len(big), tmp))
            handle = params[2] if rc == OK and len(params) > 2 else 0
            t = time.monotonic()
            rc = m.transact(0x100D, (), big, timeout_s=180.0)[1] if handle else rc
            up_s = time.monotonic() - t
            t = time.monotonic()
            data, rc2, _ = m.transact(0x1009, (handle,), timeout_s=180.0, quiet=True) if handle else (None, 0, [])
            down_s = time.monotonic() - t
            m.transact(0x100B, (handle,))
            result['big'] = dict(bytes=BIG_BYTES, up_kib_s=round(BIG_BYTES / 1024 / up_s, 1),
                                 down_kib_s=round(BIG_BYTES / 1024 / down_s, 1))
            run.check('speed: 8 MiB up and down, equal', rc == OK and rc2 == OK and data == big,
                      'up %.2f s (%.0f KiB/s), down %.2f s (%.0f KiB/s)' % (
                          up_s, BIG_BYTES / 1024 / up_s, down_s, BIG_BYTES / 1024 / down_s))
        substep('speed: GetObjectInfo + GetObjectPropValue pairs', speed_pairs)
        substep('speed: 8 MiB up and down', speed_big)
        run.log('speed: 3 MiB up %s KiB/s, down %s KiB/s; 8 MiB %s' % (
            result.get('upload', {}).get('kib_s'), result.get('download', {}).get('kib_s'), json.dumps(result.get('big'))))
        _, rc, _ = m.transact(0x1003)
        opened = not run.check('CloseSession', rc == OK, '0x%04x' % rc)
    except Exception as error:                         # noqa: BLE001
        run.check('MTP steps', False, '%s: %s' % (type(error).__name__, error))
        if opened:
            try:
                m.transact(0x1003, timeout_s=3.0)
            except Exception as again:                 # noqa: BLE001
                run.log('cleanup CloseSession failed: %s' % again)
    finally:
        try:
            usb.util.release_interface(usb_dev, probe.MTP_INTERFACE)
        except Exception:                              # noqa: BLE001
            pass
        usb.util.dispose_resources(usb_dev)

    # 6. the device's account
    try:
        hello_check('at the end')
        state, ops = device_words(dev)
        result.update(session=state, ops=ops)
        print_ops(run, state, ops)
        code, body = probe.raw_call(dev, probe.T_MTP_USB, bytes([1]), 5.0)
        if code == 0:
            p, driver, _ = probe.decode_result(body)
            result.update(probe=p, driver=driver)
            run.log('driver %s' % json.dumps(driver))
            run.check('main loop: longest pass', p['max_gap_ms'] <= probe.MAX_GAP_MS,
                      '%d ms (bound %d); session slice longest %d ms, longest operation %d ms' % (
                          p['max_gap_ms'], probe.MAX_GAP_MS, state['longest_pass_ms'], state['longest_op_ms']))
        run.check('no stall cancel, no lost link', state and state['stalls'] == 0 and state['link'] == 0,
                  json.dumps(state))
        if state:
            run.log('speed words: %d GetObjectInfo from the listing; %d read/write requests, %.0f bytes each; '
                    'operations <2 ms %d, 2-4 %d, 5-20 %d, >20 %d; longest menu-loop pass %d ms' % (
                        state['cache_hits'], state['data_requests'],
                        state['data_bytes'] / max(1, state['data_requests']), state['ops_under_2ms'],
                        state['ops_2_4ms'], state['ops_5_20ms'], state['ops_over_20ms'], state['longest_main_pass_ms']))
    except Exception as error:                         # noqa: BLE001
        run.check('the device\'s account', False, '%s: %s' % (type(error).__name__, error))
    dev.t.close()
    probe.finish(run, result)


if __name__ == '__main__':
    main()
