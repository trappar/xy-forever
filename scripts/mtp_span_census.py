#!/usr/bin/env python3
"""Read-only census of every CRC span the MTP USB probe checks before it switches.

usage: python3 scripts/mtp_span_census.py [--check usb|engine|probe] [--span N] [--dry-run]

diag.mtp_usb op 0 (usb_service.cpp probe_start) answers "identity" when any of these differs, checked in
this order, all on processor B (the USB service's own memory, which PEEK reads):
  1 usb     identity()                  target/usb_spans.hpp      (CDC serial, smxUSBD start/stop, mmc_tools;
                                                                   since 186-003 the menu-start result, not rerun)
  2 engine  engine_identity()           target/mtp_spans.hpp      (the stock MTP/USB engine)
  3 probe   probe_spans()               target/mtp_usb_spans.hpp  (TX start 88a0c0b0)
Each span is read with `xyf_host.py peek ADDRESS BYTES --out FILE` (retried when the keepalive holds
the port) and compared word by word with the authenticated stock 1.2.1 bootloader image. Works with
any build that answers PEEK (the product too): the spans are stock memory. Peeks only the listed spans.

Prints one line per span (equal, or each differing word: address, image value, device value, as
little-endian 32-bit words) and a verdict per check. Evidence (log.txt, report.json, peeks/) goes to
../private/xy-forever/sessions/<date>-mtp-span-census/run-NNN/. Exit 0: every span equal; 1: some differ.
"""
import argparse
import datetime
import json
import subprocess
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xyf_build as B  # noqa: E402
from xy_forever.build_formats import Firmware  # noqa: E402
from xy_forever.paths import SESSIONS, STOCK_121  # noqa: E402

# In probe_start's order: (key, the check in usb_service.cpp, its table).
CHECKS = (('usb', 'identity()', B.usb_spans), ('engine', 'engine_identity()', B.mtp_spans),
          ('probe', 'probe_spans()', B.mtp_usb_spans))
RETRIES = 6
TRANSIENT = ('BlockingIOError', 'TimeoutError', 'Resource temporarily unavailable')


def spans(only=None):
    """[(check key, check name, index, address, length, crc)] in the order probe_start checks them."""
    return [(key, name, i, a, n, crc) for key, name, table in CHECKS if only in (None, key)
            for i, (a, n, crc) in enumerate(table())]


def peekable(a, n):
    """PEEK's own window (xyf_host.py, spandiff.py): stock DDR and L2."""
    return 0x88000000 <= a and a + n <= 0x88b00000 or 0x08000000 <= a and a + n <= 0x08040000


def new_run_dir():
    base = SESSIONS / ('%s-mtp-span-census' % datetime.date.today().isoformat())
    n = 1
    while (base / ('run-%03d' % n)).exists():
        n += 1
    return base / ('run-%03d' % n)


def differing_words(address, image, live):
    """[(address, image word, device word)] for every 4-byte word (a short tail compared as is)."""
    out = []
    for at in range(0, len(image), 4):
        if image[at:at + 4] != live[at:at + 4]:
            word = lambda b: int.from_bytes(b.ljust(4, b'\0'), 'little')   # noqa: E731
            out.append((address + at, word(image[at:at + 4]), word(live[at:at + 4])))
    return out


class Census:
    def __init__(self, out, timeout, allowed, runner=subprocess.run, log=print):
        self.out, self.timeout, self.allowed, self.runner, self.say = out, timeout, set(allowed), runner, log

    def peek(self, address, length):
        """One span through `xyf_host.py peek`; only a listed span (address, length) is ever read."""
        if (address, length) not in self.allowed or not peekable(address, length):
            raise SystemExit('refused: 0x%08x + 0x%x is not a listed span' % (address, length))
        path = self.out / 'peeks' / ('%08x-%x.bin' % (address, length))
        cmd = [sys.executable, str(ROOT / 'xyf_host.py'), '--timeout', str(self.timeout), 'peek',
               '0x%08x' % address, '0x%x' % length, '--out', str(path)]
        for attempt in range(1, RETRIES + 1):
            path.unlink(missing_ok=True)
            r = self.runner(cmd, capture_output=True, text=True, cwd=str(ROOT))
            if r.returncode == 0 and path.exists() and path.stat().st_size == length:
                return path.read_bytes()
            text = (r.stdout or '') + (r.stderr or '')
            if not any(t in text for t in TRANSIENT) or attempt == RETRIES:
                raise SystemExit('peek 0x%08x + 0x%x failed (exit %d): %s'
                                 % (address, length, r.returncode, text.strip().splitlines()[-1:] or ''))
            self.say('  retry %d: port busy (keepalive?)' % attempt)
            time.sleep(0.5 * attempt)
        raise AssertionError('unreachable')


def run(rows, image, census, say):
    """Compares every row; returns the report (also printed line by line)."""
    report = dict(checks=[])
    for key, name, _ in CHECKS:
        mine = [r for r in rows if r[0] == key]
        if not mine:
            continue
        check = dict(key=key, check=name, spans=len(mine), differing=[])
        say('== check %s %s: %d spans' % (key, name, len(mine)))
        for _, _, i, a, n, crc in mine:
            expected = image.read(a, n)
            live = census.peek(a, n)
            words = differing_words(a, expected, live)
            crc_live = zlib.crc32(live)
            if not words and crc_live == crc:
                say('%s #%d 0x%08x + 0x%x: equal' % (key, i, a, n))
                continue
            say('%s #%d 0x%08x + 0x%x: DIFFERS, %d word(s); device CRC 0x%08x, table 0x%08x'
                % (key, i, a, n, len(words), crc_live, crc))
            for at, want, got in words:
                say('    0x%08x image 0x%08x device 0x%08x' % (at, want, got))
            check['differing'].append(dict(index=i, address='0x%08x' % a, length='0x%x' % n,
                                           crc_device='0x%08x' % crc_live, crc_table='0x%08x' % crc,
                                           words=[['0x%08x' % at, '0x%08x' % w, '0x%08x' % g]
                                                  for at, w, g in words]))
        verdict = ('refuses: spans %s differ' % ', '.join(str(d['index']) for d in check['differing'])
                   if check['differing'] else 'passes')
        check['verdict'] = verdict
        say('== %s %s %s' % (key, name, verdict))
        report['checks'].append(check)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--check', choices=[c[0] for c in CHECKS], help='only this check (default: all three, in order)')
    ap.add_argument('--span', type=int, help='only this span index of the chosen check')
    ap.add_argument('--timeout', type=float, default=15.0, help='xyf_host.py --timeout per peek (default 15 s)')
    ap.add_argument('--dry-run', action='store_true', help='list the spans and stop (no device access)')
    ap.add_argument('--bootloader', type=Path, default=STOCK_121 / 'bootloader.ldr')
    args = ap.parse_args(argv)
    if args.span is not None and not args.check:
        ap.error('--span needs --check')
    rows = [r for r in spans(args.check) if args.span is None or r[2] == args.span]
    if not rows:
        ap.error('no such span')
    image = Firmware(args.bootloader.read_bytes())
    for key, _, i, a, n, crc in rows:              # the table and the image agree (the builder's check)
        if zlib.crc32(image.read(a, n)) != crc or not peekable(a, n):
            raise SystemExit('%s #%d 0x%08x: table CRC differs from the image, or not peekable' % (key, i, a))
    if args.dry_run:
        for key, name, i, a, n, crc in rows:
            print('%s %s #%d 0x%08x + 0x%x crc 0x%08x' % (key, name, i, a, n, crc))
        print('%d spans, %d bytes; processor B (PEEK)' % (len(rows), sum(r[4] for r in rows)))
        return 0
    out = new_run_dir()
    (out / 'peeks').mkdir(parents=True)
    lines = []

    def say(text):
        print(text, flush=True)
        lines.append(text)
    say('mtp span census %s: %d spans, %d bytes, processor B; evidence in %s'
        % (datetime.datetime.now().isoformat(timespec='seconds'), len(rows), sum(r[4] for r in rows), out))
    census = Census(out, args.timeout, [(r[3], r[4]) for r in rows], log=say)
    try:
        report = run(rows, image, census, say)
    finally:
        (out / 'log.txt').write_text('\n'.join(lines) + '\n')
    report.update(args=vars(args) | dict(bootloader=str(args.bootloader)), out=str(out))
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    bad = [c for c in report['checks'] if c['differing']]
    say('CENSUS %s' % ('all spans equal' if not bad else
                       'first refusing check: %s %s' % (bad[0]['key'], bad[0]['check'])))
    (out / 'log.txt').write_text('\n'.join(lines) + '\n')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
