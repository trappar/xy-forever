#!/usr/bin/env python3
"""Crash-screen probe (#102): what the stock code does with exceptions on B, read-only. SAFE STEP: it only
reads (PEEK of ranges the build already allows); run it on any build with PEEK, before and after installing
the crash-screen build.

B's crash screen (target/key_ui_crash.ipp) takes the exception vector while the menu has the screen and
treats every exception as a fault. That holds if the stock code registered no handler that recovers from
an exception: this probe reads the stock exception dispatch (1.2.1, 88a33d44 register_exception):
  B[0x11b00ac0]              exception handlers registered (0: EVT3 still holds the start-up vector)
  B[0x11b00b30 + EXCAUSE]    slot of the handler for that EXCAUSE (0: none; EXCAUSE < 0x2f)
  0x11b005d0 + slot * 8      {entry, argument} of that handler
and IMASK/IPEND/ILAT (0x1fc02104): the level B's menu runs at, and whether a hardware error is latched.

Steps (never interactive; every command through xyf_host.py's own main()):
  check     STATUS: the menu answers. Otherwise one `NEED:` line, exit 3.
  session   hold on.
  read-l1   peek 0x11b00000 8192 (L1 data: the stock tables; never L1 instruction memory).
  read-core peek 0x1fc02104 12.
  decode    PASS: no handler is registered for a software exception (EXCPT, EXCAUSE 0..15), so each one
            the stock code registered is for a fault. The handled causes are listed in the detail and
            in summary.json; a FAIL means the vector must pass those causes on to the stock vector.
  end       status, hold off, scripts/keepalive.sh on the evidence folder (as device_pass).

Before running: the device is in the XY Forever menu with USB (power on holding PLAY, release at the logo).
Evidence: ../private/xy-forever/sessions/<date>-crash-probe-<n>/ (a simulator run removes it when nothing
failed, unless --keep). One PASS/FAIL table. Exit codes: 0 passed, 1 a step failed, 2 bad arguments, 3 NEED.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import device_pass as dp                                   # noqa: E402
import xyf_host as host                                    # noqa: E402

L1, L1_BYTES = 0x11b00000, 0x2000
CORE = 0x1fc02104                 # IMASK, IPEND, ILAT
COUNT, CAUSE_SLOT, HANDLERS, CAUSES = 0x11b00ac0, 0x11b00b30, 0x11b005d0, 0x2f


def decode(l1, core):
    """The stock exception dispatch and the core's interrupt state, from the two reads."""
    def byte(address):
        return l1[address - L1]
    handled = {}
    for cause in range(CAUSES):
        slot = byte(CAUSE_SLOT + cause)
        if slot:
            entry, argument = struct.unpack_from('<II', l1, HANDLERS + slot * 8 - L1)
            handled['0x%02x' % cause] = dict(slot=slot, entry='0x%08x' % entry, argument='0x%08x' % argument)
    imask, ipend, ilat = struct.unpack('<3I', core)
    return dict(registered=byte(COUNT), handled=handled, software=sorted(c for c in handled if int(c, 16) < 0x10),
                imask='0x%08x' % imask, ipend='0x%08x' % ipend, ilat='0x%08x' % ilat,
                hardware_error_latched=bool(ilat >> 5 & 1), hardware_error_masked=not imask >> 5 & 1)


class Probe:
    def __init__(self, dev, opts, keepalive):
        self.dev, self.opts, self.keepalive = dev, opts, keepalive
        self.log, self.rows, self.state = [], [], {}
        self.no_answer = False

    def host(self, argv, timeout):
        argv = [str(a) for a in argv]
        start = time.monotonic()
        code, out, err = self.dev.command(argv, timeout)
        self.log.append('$ xyf_host.py --timeout %g %s\nexit %d after %.2f s\n--- stdout\n%s\n--- stderr\n%s\n'
                        % (timeout, ' '.join(argv), code, time.monotonic() - start, out, err))
        if code == dp.NO_ANSWER:
            self.no_answer = True
        return code, out, err

    def ok(self, argv, timeout=dp.T_ANSWER):
        code, out, err = self.host(argv, timeout)
        if code:
            raise dp.StepFail('%s: %s' % (argv[0], dp.why(code, out, err)))
        return out

    def peek(self, address, count, name):
        path = self.folder / name
        self.ok(['peek', hex(address), hex(count), '--out', path])
        data = path.read_bytes()
        if len(data) != count:
            raise dp.StepFail('peek 0x%08x: %d of %d bytes' % (address, len(data), count))
        return data

    # -- steps -----------------------------------------------------------------------------
    def step_check(self):
        out = self.ok(['status'], self.opts.wait)
        self.state['hello'] = out.splitlines()[0] if out else ''
        return self.state['hello']

    def step_hold_on(self):
        self.ok(['hold', 'on'])
        return 'hold on'

    def step_read_l1(self):
        self.state['l1'] = self.peek(L1, L1_BYTES, 'l1.bin')
        return '%d bytes of L1 data from 0x%08x' % (L1_BYTES, L1)

    def step_read_core(self):
        self.state['core'] = self.peek(CORE, 12, 'core.bin')
        return 'IMASK/IPEND/ILAT'

    def step_decode(self):
        d = self.state['decoded'] = decode(self.state['l1'], self.state['core'])
        detail = '%d registered; causes %s; IMASK %s IPEND %s ILAT %s' % (
            d['registered'], ', '.join('%s -> %s' % (c, h['entry']) for c, h in d['handled'].items()) or 'none',
            d['imask'], d['ipend'], d['ilat'])
        if d['software']:
            raise dp.StepFail('a software exception has a stock handler: ' + detail)
        return detail

    def step_end_status(self):
        out = self.ok(['status'], dp.T_ANSWER)
        return out.splitlines()[0] if out else ''

    def step_hold_off(self):
        self.ok(['hold', 'off'])
        return 'hold off'

    def step_keepalive(self):
        if self.opts.sim:
            return 'simulator: no keepalive'
        pid = self.keepalive.start(self.folder)
        return 'keepalive.sh pid %d (stop: touch %s/stop-keepalive)' % (pid, self.folder)

    def run(self, stage, name, function):
        start = time.monotonic()
        self.log = []
        try:
            detail, result = function(), 'PASS'
        except dp.StepFail as error:
            detail, result = str(error), 'FAIL'
        row = dict(stage=stage, step=name, result=result, seconds=time.monotonic() - start, detail=detail)
        if getattr(self, 'folder', None):
            (self.folder / ('%02d-%s.txt' % (len(self.rows) + 1, name))).write_text(''.join(self.log))
        self.rows.append(row)
        return row


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sim', metavar='HOST:PORT', help='run against the device simulator (xyf_host.py --sim)')
    parser.add_argument('--wait', type=float, default=dp.T_ANSWER, help='seconds the check waits for the device')
    parser.add_argument('--keep', action='store_true', help='with --sim: keep the evidence folder when nothing failed')
    return parser.parse_args(argv)


def main(argv=None, device=None, keepalive=None, sessions=None):
    opts = parse_args(argv)
    if opts.sim:
        try:
            host.sim_address(opts.sim)
        except ValueError as error:
            print('crash_probe: %s' % error, file=sys.stderr)
            return dp.EXIT_USAGE
        host.SIM = opts.sim
    keepalive = keepalive or dp.Keepalive()
    probe = Probe(device or dp.HostDevice(), opts, keepalive)
    if not opts.sim:
        keepalive.stop_running()
    started = datetime.now(timezone.utc)
    check = probe.run('check', 'check', probe.step_check)
    if check['result'] == 'FAIL' and probe.no_answer:
        print(dp.SIM_NEED if opts.sim else dp.NEED)
        return dp.EXIT_NEED
    probe.folder = dp.new_folder(Path(sessions or dp.SESSIONS), 'sim-crash-probe' if opts.sim else 'crash-probe')
    steps = [('session', 'hold-on', probe.step_hold_on), ('probe', 'read-l1', probe.step_read_l1),
             ('probe', 'read-core', probe.step_read_core), ('probe', 'decode', probe.step_decode)]
    failed = None if check['result'] == 'PASS' else 'check'
    for stage, name, function in steps:
        if failed:
            probe.rows.append(dict(stage=stage, step=name, result='SKIP', seconds=0.0, detail='after FAIL of ' + failed))
            continue
        print('[%s] %s/%s' % (time.strftime('%H:%M:%S'), stage, name), file=sys.stderr, flush=True)
        if probe.run(stage, name, function)['result'] == 'FAIL':
            failed = '%s/%s' % (stage, name)
    lost = None
    for name, function in (('end-status', probe.step_end_status),
                           ('hold-off', probe.step_hold_off), ('keepalive', probe.step_keepalive)):
        if lost:
            probe.rows.append(dict(stage='end', step=name, result='SKIP', seconds=0.0, detail=lost))
            continue
        if probe.run('end', name, function)['result'] == 'FAIL' and name == 'end-status':
            lost = 'device not answering: if it stays so, power off, wait 5 s, power on holding PLAY'
    code = dp.EXIT_FAIL if any(r['result'] == 'FAIL' for r in probe.rows) else dp.EXIT_PASS
    summary = dict(format='xyf-crash-probe/1', folder=str(probe.folder), argv=list(argv or sys.argv[1:]),
                   started_utc=started.isoformat(), finished_utc=datetime.now(timezone.utc).isoformat(),
                   hello=probe.state.get('hello'), decoded=probe.state.get('decoded'), steps=probe.rows,
                   exit_code=code)
    (probe.folder / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(dp.table(probe.rows, dp.settle_folder(probe.folder, opts.sim, code == dp.EXIT_PASS, opts.keep)))
    return code


if __name__ == '__main__':
    sys.exit(main())
