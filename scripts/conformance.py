#!/usr/bin/env python3
"""Conformance suite: run protocol/v1.conformance.json against the simulator or the OP-XY.

    uv run scripts/conformance.py --sim 127.0.0.1:7341     every case, against a running simulator
    uv run scripts/conformance.py                          the OP-XY over USB: cases marked real_device
    uv run scripts/conformance.py --check                  interpret every case; no device, no session
    uv run scripts/conformance.py --plan                   list the cases and what each does

The cases are data (xy_forever/conformance.py loads, validates and runs them; the TypeScript runner
clients/ts/test/conformance.ts runs the same file). One way to run the suite: this script. It is
not a stage of scripts/device_pass.py, but follows its conventions: no interactive input; the
device must answer HELLO within --wait s, else one `NEED:` line and exit 3 with no session folder;
evidence in ../private/xy-forever/sessions/<date>-conformance-<n>/ (sim-conformance-<n> with
--sim, removed when nothing failed unless --keep): one file per case (every frame), summary.json;
one PASS/FAIL/SKIP table; exit 1 if any case failed.

On a real device (no --sim) only cases marked real_device run, and every frame passes the device
guard (xy_forever.conformance.device_guard): reads, screenshots, staging, file.write_staged into
the test area /yaffs2/xy-forever/log/conformance/, restarts into the menu, and requests the device
refuses without acting; never boot.install, boot.uninstall, library.set_default, the OS or a test
command. keepalive.sh processes are stopped first (they share the port) and one is started on the
session folder at the end, as the device pass ends (stop it: touch FOLDER/stop-keepalive).

With --sim, cases that need the control port (HOST:PORT+1: faults, power) run too; fixtures
(--fixture stock_os_ldr=PATH, xyf_build=PATH) default to the private stock 1.2.1 os.ldr when present.
Exit codes: 0 nothing failed, 1 a case failed, 2 bad arguments or case data, 3 NEED.
"""

import argparse
import json
import os
import re
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import device_pass  # (NEED, session folders)
from device_pass_keepalive import Keepalive

from xy_forever import bounds, paths
from xy_forever import conformance as C
from xy_forever import xyf_host as host

EXIT_PASS, EXIT_FAIL, EXIT_USAGE, EXIT_NEED = 0, 1, 2, 3
REPLY_CAP = {'sim': bounds.SIM_CALL_S, 'device': 20.0}  # longest wait for one reply (spec timeouts: longer)


class Target:
    """The port: the simulator's TCP endpoint (--sim) or the OP-XY's /dev/cu.usbmodem*."""

    def __init__(self, sim=None):
        self.sim, self.port = sim, None

    def present(self):
        return host.sim_listening(self.sim) if self.sim else host.find_port() is not None

    def pause(self, seconds):
        """The host waits on the device: on the simulator its clock moves on instead (no host time)."""
        host.transport.sim_pause(self.sim, seconds) if self.sim else time.sleep(seconds)

    def open(self):
        self.port = 'sim:' + self.sim if self.sim else host.find_port()
        if not self.port:
            raise OSError('no port')
        return host.open_port(self.port)

    def port_gone(self, link, timeout):
        """True once the connection drops (TCP closed, or the tty vanished)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                link.read_frames(0.05)
            except OSError:
                return True
            if not self.sim and self.port and not os.path.exists(self.port):
                return True
        return False

    def control(self, line):
        host_name, port = host.sim_address(self.sim)
        with socket.create_connection((host_name, port + 1), timeout=5.0) as s:
            s.sendall(line.encode() + b'\n')
            data = b''
            while not data.endswith(b'\n'):
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        return json.loads(data.decode() or '{}')


def table(rows, folder):
    width = max([len('case')] + [len(r['case']) for r in rows])
    lines = ['%-*s  %-6s %7s  %s' % (width, 'case', 'result', 'seconds', 'detail')]
    lines += ['%-*s  %-6s %7.1f  %s' % (width, r['case'], r['result'], r['seconds'], r['detail']) for r in rows]
    lines.append('folder: %s' % folder)
    return '\n'.join(lines)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sim', metavar='HOST:PORT', help='the device simulator (xyf-host --sim) instead of USB')
    parser.add_argument('--check', action='store_true', help='interpret every case and exit; touches nothing')
    parser.add_argument('--plan', action='store_true', help='list the cases and exit; touches nothing')
    parser.add_argument('--only', metavar='REGEX', help='run only the cases whose name matches')
    parser.add_argument(
        '--fixture',
        action='append',
        default=[],
        metavar='NAME=PATH',
        help='a local file for cases that need fixture:NAME (%s)' % ', '.join(C.FIXTURES),
    )
    parser.add_argument(
        '--wait',
        type=float,
        default=device_pass.T_ANSWER,
        help='seconds to wait for the device to answer HELLO (default %(default)g)',
    )
    parser.add_argument(
        '--keep',
        action='store_true',
        help='with --sim: keep the evidence folder when nothing failed (removed otherwise; '
        'a real-device run always keeps it)',
    )
    parser.add_argument('--cases', type=Path, default=C.CASES, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def fixtures_of(opts):
    found = {}
    if opts.sim and paths.STOCK_121.joinpath('os.ldr').is_file():
        found['stock_os_ldr'] = str(paths.STOCK_121 / 'os.ldr')
    for item in opts.fixture:
        name, _, path = item.partition('=')
        if name not in C.FIXTURES or not Path(path).is_file():
            raise ValueError('--fixture %s: NAME is one of %s and PATH a file' % (item, ', '.join(C.FIXTURES)))
        found[name] = path
    return found


def main(argv=None, sessions=None):
    opts = parse_args(argv)
    try:
        if opts.sim:
            host.sim_address(opts.sim)
        fixtures = fixtures_of(opts)
        spec, data = C.load_spec(), C.load(opts.cases)
        count = C.check(spec, data, fixtures)
        pattern = re.compile(opts.only) if opts.only else None
    except (ValueError, OSError, re.error) as error:  # CaseError is a ValueError
        print('conformance: %s' % error, file=sys.stderr)
        return EXIT_USAGE
    cases = [c for c in data['cases'] if not pattern or pattern.search(c['name'])]
    if opts.check:
        print('ok %d cases interpreted' % count)
        return EXIT_PASS
    if opts.plan:
        for c in cases:
            print(
                '%-40s %-8s %-36s %s'
                % (
                    c['name'],
                    'device' if c['real_device'] else 'sim',
                    ','.join(c['effects']) or 'read-only',
                    ','.join(c.get('needs', [])),
                )
            )
        return EXIT_PASS
    real = not opts.sim
    host.SIM = opts.sim
    keepalive = Keepalive()
    stopped = keepalive.stop_running() if real else []
    runner = C.Runner(
        spec,
        data,
        Target(opts.sim),
        real,
        fixtures,
        sim_control=bool(opts.sim),
        reply_cap=REPLY_CAP['device' if real else 'sim'],
    )
    hello = runner.start(opts.wait)
    if hello is None:
        print(device_pass.SIM_NEED if opts.sim else device_pass.NEED)
        return EXIT_NEED
    folder = device_pass.new_folder(Path(sessions or paths.SESSIONS), 'sim-conformance' if opts.sim else 'conformance')
    started, rows = datetime.now(UTC), []
    for n, case in enumerate(cases, 1):
        print('[%s] %s' % (time.strftime('%H:%M:%S'), case['name']), file=sys.stderr, flush=True)
        start = time.monotonic()
        result, detail = runner.run_case(case)
        seconds = time.monotonic() - start
        name = '%02d-%s.txt' % (n, re.sub(r'[^a-z0-9_.-]+', '-', case['name']))
        (folder / name).write_text(
            '# %s: %s\n# %s\n%s\n= %s %.2f s: %s\n'
            % (
                case['name'],
                case['doc'],
                'effects: %s' % (', '.join(case['effects']) or 'read-only'),
                '\n'.join(runner.lines),
                result,
                seconds,
                detail,
            )
        )
        rows.append(dict(case=case['name'], result=result, seconds=round(seconds, 2), detail=detail, file=name))
    if real:
        pid = keepalive.start(folder)
        print('keepalive.sh pid %d polls the menu; stop it: touch %s/stop-keepalive' % (pid, folder), file=sys.stderr)
    code = EXIT_FAIL if any(r['result'] == 'FAIL' for r in rows) else EXIT_PASS
    summary = dict(
        format='xyf-conformance-run/1',
        folder=str(folder),
        argv=list(argv or sys.argv[1:]),
        target=opts.sim or 'usb',
        hello=hello,
        started_utc=started.isoformat(),
        finished_utc=datetime.now(UTC).isoformat(),
        keepalives_stopped=stopped,
        cases=rows,
        exit_code=code,
        passed=sum(r['result'] == 'PASS' for r in rows),
        failed=sum(r['result'] == 'FAIL' for r in rows),
        skipped=sum(r['result'] == 'SKIP' for r in rows),
    )
    (folder / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(table(rows, device_pass.settle_folder(folder, opts.sim, code == EXIT_PASS, opts.keep)))
    return code


if __name__ == '__main__':
    sys.exit(main())
