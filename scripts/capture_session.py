#!/usr/bin/env python3
"""Capture next-session evidence through XY Forever's own service.

Without --capture this prints the plan and never opens USB. HELLO/STATUS and
optional reads do not write storage, but HELLO affects the menu's host-presence
state: this is not suitable for measuring a menu with no host attached.
"""

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from xy_forever import paths as xyf_paths
from xy_forever import usb_proto as P
from xy_forever import xyf_host as host


def plan(state=False, reports=()):
    paths = list(host.SLOTS) if state else []
    for report in reports:
        # A report's name (xyf_build.py MODES: prod, pfile, ...), or an old build's three-digit number (prod-NNN).
        if re.fullmatch(r'[0-9]{3}', report):
            report = 'prod-' + report
        elif not re.fullmatch(r'[a-z0-9]{1,16}', report):
            raise ValueError("report: a report name (prod, pfile) or an old build's three digits")
        paths.extend(f'/yaffs2/xy-forever/log/{report}-{role}.bin' for role in ('a', 'b'))
    return paths


def capture(out, paths, timeout, connect=host.connect):
    """Fail on the first unexpected result, preserving partial evidence."""
    out = Path(out).resolve()
    # Firmware and device reports must stay outside every checkout. This project's
    # private reports directory is the sole destination for this session helper.
    reports_root = xyf_paths.REPORTS.resolve()
    if not out.is_relative_to(reports_root):
        raise ValueError(f'capture directory must be under {reports_root}')
    if any((parent / '.git').exists() for parent in (out, *out.parents)):
        raise ValueError('device reports must stay outside every checkout')
    out.mkdir(parents=True, exist_ok=False)
    manifest = {
        'format': 'xyf-session-capture/1',
        'started_utc': datetime.now(UTC).isoformat(),
        'complete': False,
        'writes_storage': False,
        'changes_host_presence': True,
        'files': [],
        'reads': [],
    }
    dev = None

    def save(name, data):
        (out / name).write_bytes(data)
        manifest['files'].append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})

    try:
        start = time.monotonic()
        dev, port, hello = connect(timeout)
        manifest.update(port=port, hello=hello, connect_seconds=round(time.monotonic() - start, 3))
        code, body = dev.call(P.STATUS)
        manifest['status_code'] = code
        save('status.bin', body)
        if code:
            raise RuntimeError(f'STATUS: {P.status_name(code)}')
        decoded = host.decode_status(body)
        if not decoded.get('usb_service'):
            raise RuntimeError('STATUS has no recognized USB service result')
        save('status.json', (json.dumps(decoded, indent=2) + '\n').encode())
        for remote in paths:
            start = time.monotonic()
            code, data = dev.read_all(remote, limit=2 << 20)
            record = {
                'remote': remote,
                'status': P.status_name(code),
                'seconds': round(time.monotonic() - start, 3),
                'bytes': len(data),
            }
            manifest['reads'].append(record)
            if code == P.STATUS_NAMES.index('not_found') and not data:
                continue  # A missing report is evidence, not a successful OS boot.
            if code:
                if data:
                    save(Path(remote).name + '.partial', data)
                raise RuntimeError(f'READ {remote}: {P.status_name(code)}')
            save(Path(remote).name, data)
        manifest['complete'] = True
    except BaseException as error:
        manifest['error'] = str(error)
        raise
    finally:
        if dev is not None:
            try:
                dev.t.close()
            except OSError as error:
                manifest['close_error'] = str(error)
        manifest['finished_utc'] = datetime.now(UTC).isoformat()
        (out / 'capture.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path, help='new private reports directory')
    parser.add_argument('--state', action='store_true', help='read both boot-state slots')
    parser.add_argument(
        '--report', action='append', default=[], help="read NAME-a/b.bin (prod, pfile; an old build's NNN: prod-NNN)"
    )
    parser.add_argument('--timeout', type=float, default=30.0, help='HELLO wait in seconds')
    parser.add_argument('--capture', action='store_true', help='connect to the device now')
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 3600:
        parser.error('timeout must be > 0 and <= 3600 seconds')
    try:
        paths = plan(args.state, args.report)
        if not args.capture:
            print(
                json.dumps(
                    {
                        'dry_run': True,
                        'out': str(args.out),
                        'commands': ['HELLO', 'STATUS'],
                        'reads': paths,
                        'note': 'HELLO changes host presence; no storage writes or reboots.',
                    },
                    indent=2,
                )
            )
            return 0
        result = capture(args.out, paths, args.timeout)
        print(f'Captured {len(result["files"])} files in {args.out}')
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(f'Capture stopped: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
