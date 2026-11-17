#!/usr/bin/env python3
"""OTP probe (#15): the first read of the OP-XY's one-time-programmable memory (OTP) by XY
Forever's own code. RISKY STEP: run it only with the developer present, on a build with tfw.check
(157 or later), after the device pass.

What it does on the device (never interactive; every command through xyf_host.py's own main()):
  check     HELLO + STATUS: the menu answers and its HELLO lists tfw.check. Otherwise one `NEED:`
            line, exit 3, nothing sent but HELLO and STATUS.
  session   guard on (a hang inside the ROM read resets processor B within ~20 s and it comes
            back to the menu), hold on.
  control   tfw.check on /yaffs2/firmware.version: not_package, no key read (the command without OTP).
  put-*     put-file of a cut of each official package (1.2.1 and 1.2.5 by default) to
            /yaffs2/xy-forever/probe/: the package's first 0x480 bytes (wrapper, crypto header,
            name block) and its last 256 KiB (ZIP directory, ~80 KB), 263 KB, behind a 16-byte cut
            header (native/tfw_check.hpp). A whole package (~98 MB) cannot be written yet: the
            staged write holds 7 MB. tfw.check reads only those parts of a package anyway (decision
            003's step order: header, signature, name block, directory), so a cut checks the same.
  otp-*     tfw.check on each cut: B reads OTP selector 9 (the vendor public key), verifies the
            signature, reads selectors 3/4 (the AES key) and decrypts the directory. PASS: verdict
            pass, and the public key's SHA-256 equals that of the key recovered from the official
            signatures (../private/keys/opxy-tfw-public-key.json; never printed). Nothing is written
            by the check.
  end       status, guard off, hold off, scripts/keepalive.sh on the evidence folder (as device_pass).

What could go wrong on the first OTP read: the ROM read or init hangs or faults (the guard resets
B; the probe then reports FAIL and B comes back home), or answers other bytes (reason signature or
decrypt, no harm: the keys are only read). OTP is never written: no OTP write or program entry is
linked (xyf_build.py's boot-ROM rule). The cuts stay in /yaffs2/xy-forever/probe/ (ciphertext).

Before running: the device is in the XY Forever menu with USB (after the device pass; or power on
holding PLAY, release at the logo). Evidence: ../private/xy-forever/sessions/<date>-tfw-probe-<n>/
(a simulator run removes it when nothing failed, unless --keep). One PASS/FAIL table. Exit codes:
0 passed, 1 a step failed, 2 bad arguments, 3 NEED.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import device_pass as dp                                   # noqa: E402
import xyf_host as host                                    # noqa: E402
from xy_forever import paths, tfw_extract                  # noqa: E402
from xy_forever import usb_proto as P                      # noqa: E402

PACKAGES = ('opxy_firmware_1_2_1.tfw', 'opxy_firmware_1_2_5.tfw')
REMOTE = '/yaffs2/xy-forever/probe/'
CONTROL = '/yaffs2/firmware.version'
T_ANSWER = dp.T_ANSWER
T_PUT = 30.0               # 263 KB staged in 1 KiB chunks, written and read back
T_CHECK = 60.0             # header + ~80 KB directory read from A, one P-224 verify (spec timeout)
T_BACK = 40.0              # after a hang: the guard's ~20 s, then the restart home
NEED_BUILD = 'NEED: install a build with tfw.check (157 or later) first: python3 scripts/device_pass.py --install BUILD'


def remote(name):
    return REMOTE + name.replace('opxy_firmware_', 'tfw-').replace('.tfw', '.cut')


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

    def ok(self, argv, timeout=T_ANSWER):
        code, out, err = self.host(argv, timeout)
        if code:
            raise dp.StepFail('%s: %s' % (argv[0], dp.why(code, out, err)))
        return out

    def check_file(self, path):
        code, out, err = self.host(['tfw-check', path], T_CHECK)
        report = dp.parse_json(out)
        if not report or report.get('status') != 'ok':
            raise dp.StepFail('tfw-check %s: %s' % (path, report.get('status') if report else dp.why(code, out, err)))
        return report

    # -- steps -----------------------------------------------------------------------------
    def step_check(self):
        out = self.ok(['status'], self.opts.wait)
        hello = P.Hello(out.splitlines()[0].split(': ', 1)[-1]) if out else None
        self.state['hello'] = out.splitlines()[0] if out else ''
        if hello is None or hello.pre_v1 or not hello.answers(P.ids.TFW_CHECK):
            self.state['need'] = NEED_BUILD
            raise dp.StepFail('the device does not answer tfw.check (%s)' % self.state['hello'])
        return hello.describe()

    def step_guard_on(self):
        self.ok(['guard', 'on'])
        return 'guard on'

    def step_hold_on(self):
        self.ok(['hold', 'on'])
        return 'hold on'

    def step_control(self):
        report = self.check_file(CONTROL)
        if (report['verdict'], report['reason'], report['public_key_sha256']) != ('fail', 'not_package', '00' * 32):
            raise dp.StepFail('expected fail/not_package with no key read: %s' % report)
        return 'not_package, no key read'

    def step_put(self, name):
        data = tfw_extract.cut(self.opts.firmware / name)
        local = self.folder / (name + '.cut')
        local.write_bytes(data)
        self.ok(['put-file', local, remote(name), '--yes-write'], T_PUT)
        local.unlink()
        return '%s: %d B (CRC-32 checked by the device)' % (remote(name), len(data))

    def step_otp(self, name):
        report = self.check_file(remote(name))
        if (report['verdict'], report['reason']) != ('pass', 'pass'):
            raise dp.StepFail('%s: %s (%s)' % (name, report['verdict'], report['reason']))
        if report['public_key_sha256'] != self.expected:
            raise dp.StepFail('%s: pass, but the public key hash differs from the private key' % name)
        return 'pass; public key = the key recovered from the official signatures'

    def step_end_status(self):
        out = self.ok(['status'], T_BACK if self.state.get('hang') else T_ANSWER)
        return out.splitlines()[0] if out else ''

    def step_guard_off(self):
        self.ok(['guard', 'off'])
        return 'guard off'

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
            if name == 'control' or name.startswith('otp'):
                self.state['hang'] = True              # a hang in the check: the guard's restart takes ~20 s
        row = dict(stage=stage, step=name, result=result, seconds=time.monotonic() - start, detail=detail)
        if getattr(self, 'folder', None):
            (self.folder / ('%02d-%s.txt' % (len(self.rows) + 1, name))).write_text(''.join(self.log))
        self.rows.append(row)
        return row


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sim', metavar='HOST:PORT', help='run against the device simulator (xyf_host.py --sim)')
    parser.add_argument('--packages', default=','.join(PACKAGES),
                        help='official packages to cut and check (default %(default)s)')
    parser.add_argument('--firmware', type=Path, default=paths.FIRMWARE / 'official', help='where they are')
    parser.add_argument('--wait', type=float, default=T_ANSWER, help='seconds the check waits for the device')
    parser.add_argument('--keep', action='store_true', help='with --sim: keep the evidence folder when nothing failed')
    return parser.parse_args(argv)


def main(argv=None, device=None, keepalive=None, sessions=None):
    opts = parse_args(argv)
    names = [n.strip() for n in opts.packages.split(',') if n.strip()]
    try:
        expected = hashlib.sha256(tfw_extract.public_key()).hexdigest()
        missing = [n for n in names if not (opts.firmware / n).is_file()]
        if missing or not names:
            raise tfw_extract.ExtractError('official package missing: %s' % ', '.join(missing or ['(none named)']))
        if opts.sim:
            host.sim_address(opts.sim)
            host.SIM = opts.sim
    except (tfw_extract.ExtractError, ValueError) as error:
        print('tfw_probe: %s' % error, file=sys.stderr)
        return dp.EXIT_USAGE
    keepalive = keepalive or dp.Keepalive()
    probe = Probe(device or dp.HostDevice(), opts, keepalive)
    probe.expected = expected
    if not opts.sim:
        keepalive.stop_running()
    started = datetime.now(timezone.utc)
    check = probe.run('check', 'check', probe.step_check)
    if check['result'] == 'FAIL' and (probe.no_answer or probe.state.get('need')):
        print(probe.state.get('need') or (dp.SIM_NEED if opts.sim else dp.NEED))
        return dp.EXIT_NEED
    probe.folder = dp.new_folder(Path(sessions or dp.SESSIONS), 'sim-tfw-probe' if opts.sim else 'tfw-probe')
    steps = [('session', 'guard-on', probe.step_guard_on), ('session', 'hold-on', probe.step_hold_on),
             ('probe', 'control', probe.step_control)]
    for name in names:
        steps += [('probe', 'put-' + name[len('opxy_firmware_'):-4], lambda n=name: probe.step_put(n)),
                  ('probe', 'otp-' + name[len('opxy_firmware_'):-4], lambda n=name: probe.step_otp(n))]
    failed = None if check['result'] == 'PASS' else 'check'
    for stage, name, function in steps:
        if failed:
            probe.rows.append(dict(stage=stage, step=name, result='SKIP', seconds=0.0, detail='after FAIL of ' + failed))
            continue
        print('[%s] %s/%s' % (time.strftime('%H:%M:%S'), stage, name), file=sys.stderr, flush=True)
        if probe.run(stage, name, function)['result'] == 'FAIL':
            failed = '%s/%s' % (stage, name)
    lost = None
    for name, function in (('end-status', probe.step_end_status), ('guard-off', probe.step_guard_off),
                           ('hold-off', probe.step_hold_off), ('keepalive', probe.step_keepalive)):
        if lost:
            probe.rows.append(dict(stage='end', step=name, result='SKIP', seconds=0.0, detail=lost))
            continue
        if probe.run('end', name, function)['result'] == 'FAIL' and name == 'end-status':
            lost = 'device not answering: if it stays so, power off, wait 5 s, power on holding PLAY'
    code = dp.EXIT_FAIL if any(r['result'] == 'FAIL' for r in probe.rows) else dp.EXIT_PASS
    summary = dict(format='xyf-tfw-probe/1', folder=str(probe.folder), argv=list(argv or sys.argv[1:]),
                   started_utc=started.isoformat(), finished_utc=datetime.now(timezone.utc).isoformat(),
                   packages=names, hello=probe.state.get('hello'), steps=probe.rows, exit_code=code)
    (probe.folder / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(dp.table(probe.rows, dp.settle_folder(probe.folder, opts.sim, code == dp.EXIT_PASS, opts.keep)))
    return code


if __name__ == '__main__':
    sys.exit(main())
