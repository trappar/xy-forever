#!/usr/bin/env python3
"""Serial + MTP as one composite device on the OP-XY (test build xyf-mtp-usb), from macOS, no clicks.

What it does, each step with a time bound:
  1. serial (the stock serial service): HELLO (10 s; mode mtp-usb), then diag.mtp_usb op 0 (the trial): the device
     writes /yaffs2/xy-forever/tmp/composite.flag and does the product's full restart.
  2. USB: waits for the composite 2367:0021, device class 0xEF (30 s). The boot that finds the flag deletes it first, then starts USB once as the
     composite (record 031 b2). Prints the device's and each interface's descriptors as macOS sees them, whether a
     /dev/cu.usbmodem* node appeared (if not: what ioreg shows of the device's interfaces and their drivers).
  3. serial over the composite: HELLO with xyf_host's normal connect (the device restarts by itself when no valid
     HELLO arrives within 20 s of its start), status, one file read (config.txt).
  4. MTP on interface 2 with the serial port still open: GetDeviceInfo, OpenSession, the 4 MiB test sink (0x9801),
     CloseSession; every container logged raw (hex). Then HELLO and status again over the serial.
  5. The device's account over the serial: op 1 (its words, the driver's counters, the sink's SHA-256), the marks
     file and the result file it wrote. The marks on screen ("USB", "MTP") are the developer's to read.
If the composite never appears or the serial HELLO fails: waits for the fallback restart (40 s), then reads the marks
and the result file over the stock serial; the last mark names where it stopped.
--marks-only: HELLO, the marks file and the result file, nothing else (after a freeze and the developer's power cycle).
--no-trial: skip step 1 (the composite is already up this boot).
Evidence (log.txt, result.json, marks.txt, result.txt, ioreg.txt) in ../private/xy-forever/sessions/<date>-mtp-usb-probe/run-NNN/.
Prints PASS/FAIL lines; exit 0 only if every check passed. It never retries by itself.

Needs pyusb and libusb (brew install libusb; python3 -m pip install pyusb). Nothing is installed or changed on the
Mac; the only side effect is `pkill -x ptpcamerad` when claiming interface 2 fails (launchd restarts it).
"""
import argparse
import datetime
import glob
import hashlib
import json
import random
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xyf_host as H  # noqa: E402
from xy_forever import usb_proto as P  # noqa: E402
from xy_forever.paths import SESSIONS  # noqa: E402

T_MTP_USB = 0x62                    # diag.mtp_usb (dev, test build only; usb_service.cpp)
VID, PID = 0x2367, 0x0021           # target/mtp_usb.cpp kDevInfo (xyf_build.py MTP_USB_IDS: the stock pair)
COMPOSITE_CLASS = 0xef              # kDevInfo's device class: the stock OS and the serial fallback share the pair
USB_IDS = '%04x:%04x' % (VID, PID)
EP_OUT, EP_IN, EP_INT = 0x0a, 0x89, 0x83
MTP_INTERFACE = 2
# What the composite should look like (record 031 b2): interface number -> (class triple, endpoint addresses).
INTERFACES = {0: ((2, 2, 1), {0x81}), 1: ((0x0a, 0, 0), {0x08, 0x8b}), 2: ((6, 1, 1), {0x89, 0x0a, 0x83})}
SINK_BYTES = 4 << 20
OP_GET_DEVICE_INFO, OP_OPEN, OP_CLOSE, OP_SINK = 0x1001, 0x1002, 0x1003, 0x9f01   # the sink moved off 0x9801
RC_OK = 0x2001
CONFIG_PATH = '/yaffs2/xy-forever/config.txt'
PROBE_WORDS = ('state', 'flag', 'delete', 'gate', 'slot', 'started_ms', 'first_byte_ms', 'hello_ms', 'passes',
               'max_gap_ms', 'last_ms', 'sinks', 'mark_first_ms', 'mark_total_ms')
STATES = ('none', 'deferred', 'stock', 'composite', 'fallback')
FLAGS = ('unchecked', 'absent', 'found', 'A-silent')
DRIVER_WORDS = ('passes', 'longest_pass_urbs', 'longest_pass_bytes', 'urbs_received', 'urbs_dropped', 'in_sent',
                'kicks', 'kick_rx', 'deconfigures', 'resets', 'cancels', 'resets_bus', 'sink_bytes', 'source_bytes',
                'sink_sum', 'device_infos', 'sessions_opened', 'sessions_closed', 'state', 'marks_written',
                'marks_failed', 'mtp_commands', 'serial_rx', 'serial_tx', 'serial_held', 'serial_busy', 'line_codings',
                'control_lines', 'handler_in', 'handler_out', 'handler_held', 'handler_urbs_in')
# After the digest: why the span gate answered "identity" (usb_service.cpp probe_spans; 0 = none).
REFUSAL_WORDS = ('check', 'index', 'address', 'length', 'crc_now', 'failing')
REFUSAL_CHECKS = ('none', 'boot-time identity() usb_spans.hpp failed', 'engine_identity() mtp_spans.hpp',
                  'probe_spans() mtp_usb_spans.hpp')
MAX_GAP_MS = 250                    # "responsive": no menu-loop pass longer than this with the composite up
# The progress marks (usb_service.cpp marks_path; one line "NN MS NAME" per mark, after a header).
MARKS_PATH = '/yaffs2/xy-forever/tmp/mtp-probe-marks.txt'
# The mark names (mtp_usb.cpp kMarkNames, steps 1..14) and what follows when a mark is the last one.
MARKS = (
    ('flag-found', 'the delete was refused (stock serial follows), or writing mark 2 froze'),
    ('flag-deleted', 'the span gate (refused: stock serial) or register (88a09d5c)'),
    ('registered', 'writing the next mark (nothing else between)'),
    ('before-start-0x200', 'start(0x200): the controller init, its 5-tick wait on GP timer 7 (record 030)'),
    ('after-start-0x200', 'reading the stack\'s init flag, writing the next mark'),
    ('attach-ok', 'no SETUP reached the driver (the host never enumerated it)'),
    ('attach-failed', 'nothing: the descriptor build failed; the fallback restart 20 s after the start'),
    ('first-setup', 'configure: arming bulk OUT 0x0a and 0x08 (88a092ee, 88a08f08, kicks)'),
    ('configured', 'no serial byte arrived (macOS bound no CDC driver, or the host never wrote the port)'),
    ('first-serial-byte', 'no valid HELLO frame (bytes lost or garbled), or its reply'),
    ('first-hello', 'nothing: the composite stays this boot (marks end here)'),
    ('first-mtp-command', 'the MTP transaction'),
    ('fallback-restart', 'leave (stop(0), unregister), the result file, or the restart'),
    ('register-refused', 'start(0x400): the stock serial service'),
)
PASS_MARKS = [1, 2, 3, 4, 5, 6, 8, 9, 10, 11]
# The result file (usb_service.cpp result_path): a header, then lines 'probe', 'driver', 'refusal' of 8-digit hex
# words and 'sha256' of 64 hex digits: op 1's reply after its status word. Written at the first HELLO and at the
# fallback restart.
RESULT_PATH = '/yaffs2/xy-forever/tmp/mtp-probe-result.txt'
APPEAR_S = 30.0                     # the restart (~6 s) plus the boot to the menu's first pass
FALLBACK_S = 40.0                   # no HELLO: the device restarts 20 s after its start, then the stock serial


class Run:
    def __init__(self, out):
        self.out, self.t0, self.lines, self.checks = out, time.monotonic(), [], []
        out.mkdir(parents=True)

    def log(self, text):
        line = '[%7.2f s] %s' % (time.monotonic() - self.t0, text)
        self.lines.append(line)
        print(line, flush=True)
        (self.out / 'log.txt').write_text('\n'.join(self.lines) + '\n')

    def check(self, name, ok, detail=''):
        self.checks.append((name, bool(ok), detail))
        self.log('%s %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + detail) if detail else ''))
        return ok


def new_run_dir():
    base = SESSIONS / ('%s-mtp-usb-probe' % datetime.date.today().isoformat())
    n = 1
    while (base / ('run-%03d' % n)).exists():
        n += 1
    return base / ('run-%03d' % n)


def raw_call(dev, kind, payload, timeout):
    """Device.call without the HELLO's command list (diag.mtp_usb is not in the published spec)."""
    info, dev.hello_info = dev.hello_info, None
    try:
        return dev.call(kind, payload, timeout)
    finally:
        dev.hello_info = info


def container(kind, code, tx, params=(), body=0):
    return struct.pack('<IHHI', 12 + 4 * len(params) + body, kind, code, tx) + b''.join(struct.pack('<I', p) for p in params)


def mtp_string(data, at):
    n = data[at]
    text = data[at + 1:at + 1 + 2 * n].decode('utf-16-le', 'replace').rstrip('\0')
    return text, at + 1 + 2 * n


def parse_device_info(data):
    """The DeviceInfo dataset: the data container's body, its 12-byte header already removed by transact()."""
    std, ext_id, ext_ver = struct.unpack_from('<HIH', data, 0)
    ext, at = mtp_string(data, 8)
    at += 2                                                                  # functional mode
    n = struct.unpack_from('<I', data, at)[0]
    ops = list(struct.unpack_from('<%dH' % n, data, at + 4))
    at += 4 + 2 * n
    for _ in range(4):                                                       # events, properties, formats
        at += 4 + 2 * struct.unpack_from('<I', data, at)[0]
    strings = []
    for _ in range(4):
        s, at = mtp_string(data, at)
        strings.append(s)
    return dict(standard=std, vendor_extension=ext_id, extension_version=ext_ver, extension=ext,
                operations=['0x%04x' % o for o in ops], manufacturer=strings[0], model=strings[1],
                device_version=strings[2], serial=strings[3])


RAW_LOG_BYTES = 1024                # longest container logged whole (hex); longer ones: head and length


def log_container(log, raw):
    """Every container received, raw (hex), so a parser question settles itself from the log."""
    length, kind, code, tx = struct.unpack_from('<IHHI', raw)
    hexed = raw[:RAW_LOG_BYTES].hex() + ('' if len(raw) <= RAW_LOG_BYTES else '... (%d bytes)' % len(raw))
    log('container in: length %d type %d code 0x%04x tx %d: %s' % (length, kind, code, tx, hexed))


def transact(usb_dev, tx, code, params=(), data_out=None, timeout_ms=3000, write_timeout_ms=3000, log=None):
    """One MTP transaction; returns (data phase or None, response code, response params). Data and response
    come back without their 12-byte container header; log (if given) receives each container raw."""
    usb_dev.write(EP_OUT, container(1, code, tx, params), timeout=timeout_ms)
    if data_out is not None:
        usb_dev.write(EP_OUT, container(2, code, tx, (), len(data_out)) + data_out, timeout=write_timeout_ms)
    data, deadline = None, time.monotonic() + timeout_ms / 1000.0
    pending = b''
    while time.monotonic() < deadline:
        pending += bytes(usb_dev.read(EP_IN, 16384, timeout=timeout_ms))
        while len(pending) >= 12 and len(pending) >= struct.unpack_from('<I', pending)[0]:
            length, kind, rcode, rtx = struct.unpack_from('<IHHI', pending)
            if log:
                log_container(log, pending[:length])
            body, pending = pending[12:length], pending[length:]
            if kind == 2:
                data = body
            elif kind == 3:
                if rtx != tx:
                    raise IOError('response for transaction %d, expected %d' % (rtx, tx))
                return data, rcode, list(struct.unpack_from('<%dI' % (len(body) // 4), body))
    raise TimeoutError('no MTP response to 0x%04x' % code)


def wait_usb(usb_core, present, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        dev = usb_core.find(idVendor=VID, idProduct=PID, bDeviceClass=COMPOSITE_CLASS)
        if (dev is not None) == present:
            return dev if present else True
        time.sleep(0.1)
    return None


def claim(usb, dev, run, interface):
    """Claim one interface without touching the configuration (macOS's CDC driver holds interfaces 0 and 1)."""
    for attempt in range(2):
        try:
            usb.util.claim_interface(dev, interface)
            return True
        except usb.core.USBError as error:
            run.log('claim interface %d failed: %s' % (interface, error))
            if attempt == 0:
                run.log('stopping ptpcamerad (macOS Image Capture holds PTP devices; launchd restarts it)')
                subprocess.run(['pkill', '-x', 'ptpcamerad'], check=False)
                time.sleep(0.5)
    return False


def describe(dev, run, result):
    """The device's and each interface's descriptors as macOS (libusb) reads them; True if they are the composite's."""
    d = dict(bcdUSB='0x%04x' % dev.bcdUSB, device_class=[dev.bDeviceClass, dev.bDeviceSubClass, dev.bDeviceProtocol],
             speed=getattr(dev, 'speed', None), interfaces={})
    cfg = dev.get_active_configuration()
    for intf in cfg:
        d['interfaces'][intf.bInterfaceNumber] = dict(
            alt=intf.bAlternateSetting,
            cls=[intf.bInterfaceClass, intf.bInterfaceSubClass, intf.bInterfaceProtocol],
            endpoints={'0x%02x' % e.bEndpointAddress: dict(attributes=e.bmAttributes, max_packet=e.wMaxPacketSize,
                                                           interval=e.bInterval) for e in intf},
            extra=bytes(intf.extra_descriptors or b'').hex())
    d['config_extra'] = bytes(cfg.extra_descriptors or b'').hex()      # the IAD, if libusb kept it here
    result['usb'] = d
    run.log('device: class %s, bcdUSB %s, speed %s' % (d['device_class'], d['bcdUSB'], d['speed']))
    for n in sorted(d['interfaces']):
        i = d['interfaces'][n]
        run.log('  interface %d alt %d class %s endpoints %s extra %s' %
                (n, i['alt'], i['cls'], json.dumps(i['endpoints']), i['extra'] or '-'))
    ok = d['device_class'] == [0xef, 2, 1] and sorted(d['interfaces']) == [0, 1, 2]
    for n, (cls, eps) in INTERFACES.items():
        i = d['interfaces'].get(n)
        ok = ok and i is not None and i['cls'] == list(cls) and {int(e, 16) for e in i['endpoints']} == eps
    return ok


def ioreg_dump(run):
    """What macOS bound to the device's interfaces (saved whole; the driver and node lines printed)."""
    out = subprocess.run(['ioreg', '-r', '-n', 'OP-XY (XY Forever)', '-l', '-w0'], capture_output=True, text=True).stdout
    if not out.strip():
        out = subprocess.run(['ioreg', '-p', 'IOUSB', '-l', '-w0'], capture_output=True, text=True).stdout
    (run.out / 'ioreg.txt').write_text(out)
    keys = ('+-o ', '"bInterfaceClass"', '"bInterfaceNumber"', '"IOCalloutDevice"', '"USB Interface Name"')
    for line in out.splitlines():
        if any(k in line for k in keys):
            run.log('ioreg: ' + line.strip()[:160])


def mtp_part(usb, dev, run, payload, result):
    """Step 4: GetDeviceInfo, OpenSession, the 4 MiB sink, CloseSession on interface 2."""
    if not run.check('claim interface 2 (MTP)', claim(usb, dev, run, MTP_INTERFACE)):
        return
    state = dict(tx=0, opened=False, closed=False)

    def step(code, params=(), data_out=None, **kw):
        state['tx'] += 1
        return transact(dev, state['tx'], code, params, data_out, log=run.log, **kw)

    try:
        t = time.monotonic()
        data, rc, _ = step(OP_GET_DEVICE_INFO)
        try:                                       # a parse error is the script's, not the device's: go on
            info = parse_device_info(data) if data else None
        except (struct.error, IndexError, UnicodeDecodeError) as error:
            info = None
            run.log('DeviceInfo not parsed: %s: %s' % (type(error).__name__, error))
        result['device_info'] = info
        run.check('GetDeviceInfo', rc == RC_OK and info and info['standard'] == 100,
                  'rc 0x%04x in %.0f ms, %s' % (rc, 1000 * (time.monotonic() - t), json.dumps(info)))
        _, rc, _ = step(OP_OPEN, (1,))
        state['opened'] = rc == RC_OK
        run.check('OpenSession', rc == RC_OK, 'rc 0x%04x' % rc)
        t = time.monotonic()
        _, rc, params = step(OP_SINK, (len(payload),), payload, timeout_ms=10000, write_timeout_ms=30000)
        seconds = time.monotonic() - t
        result['sink'] = dict(seconds=round(seconds, 3), rate_kib_s=round(len(payload) / 1024 / seconds, 1),
                              params=params)
        run.check('sink 4 MiB answered', rc == RC_OK and params[:1] == [len(payload)] and
                  params[1:2] == [sum(payload) & 0xffffffff],
                  'rc 0x%04x, %.2f s (%.0f KiB/s), params %s' % (rc, seconds, len(payload) / 1024 / seconds, params))
        _, rc, _ = step(OP_CLOSE)
        state['closed'] = rc == RC_OK
        run.check('CloseSession', rc == RC_OK, 'rc 0x%04x' % rc)
    except Exception as error:                     # noqa: BLE001
        run.check('MTP transactions', False, '%s: %s' % (type(error).__name__, error))
        if state['opened'] and not state['closed']:
            try:
                _, rc, _ = step(OP_CLOSE)
                run.log('cleanup CloseSession: rc 0x%04x' % rc)
            except Exception as again:             # noqa: BLE001
                run.log('cleanup CloseSession failed: %s: %s' % (type(again).__name__, again))
    finally:
        try:
            usb.util.release_interface(dev, MTP_INTERFACE)
        except Exception:                          # noqa: BLE001
            pass


def decode_result(body):
    words = struct.unpack_from('<%dI' % (len(PROBE_WORDS) + len(DRIVER_WORDS)), body)
    probe = dict(zip(PROBE_WORDS, words[:len(PROBE_WORDS)]))
    driver = dict(zip(DRIVER_WORDS, words[len(PROBE_WORDS):]))
    probe['slot'] = '0x%08x' % probe['slot']
    probe['state'] = STATES[probe['state']] if probe['state'] < len(STATES) else probe['state']
    probe['flag'] = FLAGS[probe['flag']] if probe['flag'] < len(FLAGS) else probe['flag']
    return probe, driver, body[4 * len(words):4 * len(words) + 32].hex()


def decode_refusal(body):
    """The refusal words after the digest, or None."""
    at = 4 * (len(PROBE_WORDS) + len(DRIVER_WORDS)) + 32
    if len(body) < at + 4 * len(REFUSAL_WORDS):
        return None
    r = dict(zip(REFUSAL_WORDS, struct.unpack_from('<%dI' % len(REFUSAL_WORDS), body, at)))
    r['check'] = REFUSAL_CHECKS[r['check']] if r['check'] < len(REFUSAL_CHECKS) else 'check_%d' % r['check']
    for k in ('address', 'length', 'crc_now'):
        r[k] = '0x%08x' % r[k]
    return r


def parse_marks(text):
    """The marks file: a header line, then 'NN MS NAME' lines; returns (header, [dict])."""
    lines = text.splitlines()
    marks = []
    for line in lines[1:]:
        parts = line.split(' ', 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            marks.append(dict(step=int(parts[0]), ms=int(parts[1]), name=parts[2]))
    return (lines[0] if lines else ''), marks


def read_marks(dev, run, result):
    """Read and print the marks file (READ_FILE over serial); never fatal."""
    try:
        code, body = dev.read_file(MARKS_PATH)
    except Exception as error:                     # noqa: BLE001 (evidence only)
        run.log('marks: read failed: %s: %s' % (type(error).__name__, error))
        result['marks'] = None
        return None
    if code:
        run.log('marks: %s (%s)' % (P.status_name(code), MARKS_PATH))
        result['marks'] = P.status_name(code)
        return None
    text = body.decode('ascii', 'replace')
    (run.out / 'marks.txt').write_text(text)
    header, marks = parse_marks(text)
    run.log('marks: %s' % header)
    previous = None
    for m in marks:
        run.log('  %02d %-32s %9d ms%s' % (m['step'], m['name'], m['ms'],
                                           '' if previous is None else '  (+%d ms)' % (m['ms'] - previous)))
        previous = m['ms']
    if marks:
        last = marks[-1]
        after = MARKS[last['step'] - 1][1] if 1 <= last['step'] <= len(MARKS) else '?'
        run.log('marks: last %02d %s; if B froze, it froze in: %s' % (last['step'], last['name'], after))
    result['marks'] = dict(header=header, marks=marks)
    return marks


def result_body(text):
    """The result file back into op 1's reply body (after the status word); None if a part is missing."""
    parts = {}
    for line in text.splitlines()[1:]:
        label, _, rest = line.partition(' ')
        parts[label] = rest.split()
    try:
        words = [int(w, 16) for w in parts['probe'] + parts['driver']]
        if len(parts['probe']) != len(PROBE_WORDS) or len(parts['driver']) != len(DRIVER_WORDS):
            return None
        refusal = [int(w, 16) for w in parts['refusal']]
        return struct.pack('<%dI' % len(words), *words) + bytes.fromhex(parts['sha256'][0]) + \
            struct.pack('<%dI' % len(refusal), *refusal)
    except (KeyError, IndexError, ValueError):
        return None


def read_result(dev, run, result):
    """Read, save and decode the result file (READ_FILE over serial); never fatal. Returns the body or None."""
    try:
        code, body = dev.read_file(RESULT_PATH)
    except Exception as error:                     # noqa: BLE001 (evidence only)
        run.log('result file: read failed: %s: %s' % (type(error).__name__, error))
        return None
    if code:
        run.log('result file: %s (%s)' % (P.status_name(code), RESULT_PATH))
        return None
    text = body.decode('ascii', 'replace')
    (run.out / 'result.txt').write_text(text)
    run.log('result file: %s' % (text.splitlines()[0] if text else '(empty)'))
    parsed = result_body(text)
    if parsed is None:
        run.log('result file: not parsed:\n%s' % text)
        return None
    probe, driver, digest = decode_result(parsed)
    result.setdefault('file_result', dict(probe=probe, driver=driver, device_sha256=digest,
                                          refusal_words=decode_refusal(parsed)))
    run.log('result file probe %s' % json.dumps(probe))
    run.log('driver %s' % json.dumps(driver))
    run.log('sha256 %s; refusal %s' % (digest, json.dumps(result['refusal_words'])))
    return parsed


def evidence(dev, run, result):
    """The marks and the result file over whatever serial is up; never fatal."""
    marks = read_marks(dev, run, result)
    read_result(dev, run, result)
    return marks


def finish(run, result):
    result['checks'] = [dict(name=n, ok=ok, detail=d) for n, ok, d in run.checks]
    (run.out / 'result.json').write_text(json.dumps(result, indent=2, default=str) + '\n')
    failed = [n for n, ok, _ in run.checks if not ok]
    run.log('composite %s%s' % ('PASS' if not failed else 'FAIL', '' if not failed else ': ' + ', '.join(failed)))
    raise SystemExit(0 if not failed else 1)


def fallback(run, result, t_start, why):
    """No composite serial: wait for the device's own restart and read its account over the stock serial."""
    run.log('%s: waiting for the fallback restart (stock serial, up to %.0f s)' % (why, FALLBACK_S))
    try:
        dev, port, hello = H.connect(FALLBACK_S)
    except SystemExit:
        run.check('stock serial back after the fallback', False,
                  'not back %.0f s after the trial: the OP-XY needs a power cycle, then --marks-only'
                  % (time.monotonic() - t_start))
        finish(run, result)
    result['hello_fallback'] = hello
    run.check('stock serial back after the fallback', 'mode=mtp-usb' in hello,
              '%.1f s after the trial, %s' % (time.monotonic() - t_start, port))
    evidence(dev, run, result)
    dev.t.close()
    finish(run, result)


def marks_only():
    """--marks-only: HELLO, the marks file and the result file, nothing else (after a power cycle)."""
    run = Run(new_run_dir())
    result = dict(marks_only=True)
    dev, port, hello = H.connect(10.0)
    result['hello'] = hello
    run.log('serial %s: %s' % (port, hello))
    evidence(dev, run, result)
    dev.t.close()
    (run.out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    raise SystemExit(0 if isinstance(result.get('marks'), dict) else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--seed', type=int, default=3402, help='seed of the 4 MiB sink data')
    parser.add_argument('--marks-only', action='store_true',
                        help='only read and print the progress marks and the result file (after a power cycle)')
    parser.add_argument('--no-trial', action='store_true', help='the composite is already up: skip the trial')
    args = parser.parse_args()
    if args.marks_only:
        marks_only()
    try:
        import usb.core
        import usb.util
        usb.core.find(idVendor=0xffff, idProduct=0xffff)    # NoBackendError without libusb
    except Exception as error:                     # ImportError, or NoBackendError without libusb
        raise SystemExit('pyusb with libusb is needed (brew install libusb; python3 -m pip install pyusb): %s' % error)
    run = Run(new_run_dir())
    payload = random.Random(args.seed).randbytes(SINK_BYTES)
    digest = hashlib.sha256(payload).hexdigest()
    result = dict(seed=args.seed, sink_sha256=digest, sink_sum=sum(payload) & 0xffffffff)
    run.log('evidence in %s; sink sha256 %s' % (run.out, digest))
    t_start = time.monotonic()

    # 1. the trial over the current (stock) serial.
    if not args.no_trial:
        dev, port, hello = H.connect(10.0)
        result['hello_before'] = hello
        run.log('serial before: %s' % port)
        if not run.check('hello names the test build', 'mode=mtp-usb' in hello, hello):
            finish(run, result)
        code, _ = raw_call(dev, T_MTP_USB, bytes([0]), 15.0)
        if code == P.STATUS_NAMES.index('identity'):
            rcode, body = raw_call(dev, T_MTP_USB, bytes([1]), 5.0)
            result['refusal'] = decode_refusal(body) if rcode == 0 else P.status_name(rcode)
            run.log('refusal %s (scripts/mtp_span_census.py finds the word)' % json.dumps(result['refusal']))
        try:
            dev.t.close()
        except OSError:
            pass
        t_start = time.monotonic()
        if not run.check('diag.mtp_usb trial (flag written, restart)', code == 0, P.status_name(code)):
            finish(run, result)

    # 2. the composite on the bus.
    usb_dev = wait_usb(usb.core, True, APPEAR_S)
    if not run.check('composite %s appeared' % USB_IDS, usb_dev is not None,
                     '%.2f s after the trial reply' % (time.monotonic() - t_start)):
        ioreg_dump(run)
        fallback(run, result, t_start, 'no composite')
    run.check('descriptors: EF/02/01; 0 CDC comm 0x81; 1 CDC data 0x08 0x8b; 2 MTP 0x89 0x0a 0x83',
              describe(usb_dev, run, result), json.dumps(result.get('usb')))
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not glob.glob('/dev/cu.usbmodem*'):
        time.sleep(0.1)
    nodes = sorted(glob.glob('/dev/cu.usbmodem*'))
    result['nodes'] = nodes
    if not run.check('a /dev/cu.usbmodem* node appeared (macOS bound its CDC driver)', bool(nodes),
                     ', '.join(nodes) or 'none'):
        ioreg_dump(run)
        usb.util.dispose_resources(usb_dev)
        fallback(run, result, t_start, 'no serial node')

    # 3. the serial service over the composite.
    try:
        dev, port, hello = H.connect(12.0)
    except SystemExit:
        ioreg_dump(run)
        run.check('HELLO over the composite', False, 'no reply')
        usb.util.dispose_resources(usb_dev)
        fallback(run, result, t_start, 'no HELLO')
    result['hello_composite'] = hello
    run.check('HELLO over the composite', 'mode=mtp-usb' in hello,
              '%.2f s after the trial, %s: %s' % (time.monotonic() - t_start, port, hello))
    code, body = dev.call(P.STATUS, b'', 5.0)
    run.check('status over the composite', code == 0, '%s, %d bytes' % (P.status_name(code), len(body)))
    code, body = dev.read_file(CONFIG_PATH)
    run.check('file read over the composite (%s)' % CONFIG_PATH, code == 0,
              '%s, %d bytes' % (P.status_name(code), len(body)))

    # 4. MTP on interface 2, the serial port still open; then the serial again.
    try:
        mtp_part(usb, usb_dev, run, payload, result)
    except Exception as error:                     # noqa: BLE001
        run.check('MTP transactions', False, '%s: %s' % (type(error).__name__, error))
    finally:
        usb.util.dispose_resources(usb_dev)
    try:
        code, text = dev.hello()
        run.check('HELLO again after MTP', code == 0 and 'mode=mtp-usb' in text, text)
        code, body = dev.call(P.STATUS, b'', 5.0)
        run.check('status again after MTP', code == 0, P.status_name(code))
    except Exception as error:                     # noqa: BLE001
        run.check('HELLO again after MTP', False, '%s: %s' % (type(error).__name__, error))

    # 5. the device's account: op 1, the marks, the result file.
    try:
        code, body = raw_call(dev, T_MTP_USB, bytes([1]), 5.0)
        if run.check('op 1 (the composite\'s words)', code == 0, P.status_name(code)):
            probe, driver, device_digest = decode_result(body)
            result.update(probe=probe, driver=driver, device_sha256=device_digest, refusal_words=decode_refusal(body))
            run.log('probe %s' % json.dumps(probe))
            run.log('driver %s' % json.dumps(driver))
            run.check('device state composite, flag found and deleted, gate passed',
                      probe['state'] == 'composite' and probe['flag'] == 'found' and probe['delete'] == 0 and
                      probe['gate'] == 0, json.dumps(probe))
            run.check('device received the 4 MiB intact (SHA-256)', device_digest == digest and probe['sinks'] >= 1 and
                      driver['sink_bytes'] == SINK_BYTES, device_digest)
            run.check('main loop responsive', probe['passes'] > 0 and probe['max_gap_ms'] <= MAX_GAP_MS,
                      '%d passes, longest %d ms (bound %d)' % (probe['passes'], probe['max_gap_ms'], MAX_GAP_MS))
            run.check('no urb dropped', driver['urbs_dropped'] == 0,
                      'kicks %d, kick_rx %d, serial held %d, busy %d' % (driver['kicks'], driver['kick_rx'],
                                                                        driver['serial_held'], driver['serial_busy']))
            run.log('serial: %d bytes in, %d out; line codings %d, control lines 0x%x; HELLO %d ms after the start' %
                    (driver['serial_rx'], driver['serial_tx'], driver['line_codings'], driver['control_lines'],
                     probe['hello_ms']))
    except Exception as error:                     # noqa: BLE001
        run.check('op 1 (the composite\'s words)', False, '%s: %s' % (type(error).__name__, error))
    marks = evidence(dev, run, result)
    run.check('marks: the pass order %s' % PASS_MARKS, marks is not None and [m['step'] for m in marks] == PASS_MARKS,
              '%s' % (None if marks is None else [m['step'] for m in marks]))
    dev.t.close()
    run.log('on screen now: "USB" lit while serial frames arrive; "MTP" lit for 5 s after the MTP steps')
    finish(run, result)


if __name__ == '__main__':
    main()
