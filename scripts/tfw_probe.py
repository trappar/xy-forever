#!/usr/bin/env python3
"""OTP probe (#15): legacy B, or --roles a,b for both processors' one-time-programmable
memory (OTP) through XY Forever's own checker. RISKY STEP: run only with the developer's explicit yes,
on a checked build after the device pass. --roles a,b requires tfw.check_role v1;
readable negative results are evidence and do not abort the remaining role checks.

What it does on the device (never interactive; every command through xyf_host.py's own main()):
  check     HELLO + STATUS: the menu answers and its HELLO lists tfw.check. Otherwise one `NEED:`
            line, exit 3, nothing sent but HELLO and STATUS.
  control   tfw.check on /yaffs2/firmware.version (A: xy-forever/state-a.txt): not_package, no key read (the command without OTP).
  put-*     put-file of a cut of each official package (1.2.1 and 1.2.5 by default) to
            /yaffs2/xy-forever/probe/: the package's first 0x480 bytes (wrapper, crypto header,
            name block) and its last 256 KiB (ZIP directory, ~80 KB), 263 KB, behind a 16-byte cut
            header (native/tfw_check.hpp). A whole package (~98 MB) cannot be written yet: the
            staged write holds 7 MB. tfw.check reads only those parts of a package anyway
            (header, signature, name block, directory), so a cut checks the same.
  otp-*     tfw.check on each cut: the selected processor reads OTP selector 9 (the vendor public key), verifies the
            signature, reads selectors 3/4 (the AES key) and decrypts the directory. PASS: verdict
            pass, and the public key's SHA-256 equals that of the key recovered from the official
            signatures (../private/keys/opxy-tfw-public-key.json; never printed). Nothing is written
            by the check. --roles a,b records each role, public-key hash match and verdict;
            readable negative results continue. A read/storage failure stops the probe.
  cleanup   remove only the cuts whose uploads were attempted, with a cleanup log first. A failed
            check with a valid reply still permits cleanup; transport loss sends no more commands.
  end       on success only, scripts/keepalive.sh on the evidence folder. No hold, guard or reset.

What could go wrong on the first OTP read: the ROM read or init hangs or faults (there is no
watchdog: the probe reports "power cycle needed" within --check-timeout and sends nothing else),
or answers other bytes (reason signature or
decrypt, no harm: the keys are only read). OTP is never written: no OTP write or program entry is
linked (xyf_build.py's boot-ROM rule). After a lost connection, the summary names any cuts left
in /yaffs2/xy-forever/probe/ for cleanup after a power cycle. They contain ciphertext only.

Before running: the device is in the XY Forever menu with USB (after the device pass; or power on
holding PLAY, release at the logo). Evidence: ../private/xy-forever/sessions/<date>-tfw-probe-<n>/
(a simulator run removes it when nothing failed, unless --keep). One PASS/FAIL table. Exit codes:
0 passed, 1 a step failed, 2 bad arguments, 3 NEED.
"""

import argparse
import hashlib
import json
import math
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import device_pass as dp

from xy_forever import paths, tfw_extract
from xy_forever import usb_proto as P
from xy_forever import xyf_host as host

PACKAGES = ('opxy_firmware_1_2_1.tfw', 'opxy_firmware_1_2_5.tfw')
REMOTE = '/yaffs2/xy-forever/probe/'
CONTROL = '/yaffs2/firmware.version'
T_ANSWER = dp.T_ANSWER
T_PUT = 30.0  # 263 KB staged in 1 KiB chunks, written and read back
T_CHECK = 5.0  # internal header/directory reads and one P-224 verify, plus a few seconds
NEED_BUILD = 'NEED: install a build with tfw.check (157 or later) first: uv run scripts/device_pass.py --install BUILD'


def remote(name):
    return REMOTE + name.replace('opxy_firmware_', 'tfw-').replace('.tfw', '.cut')


class Probe:
    def __init__(self, dev, opts, keepalive):
        self.dev, self.opts, self.keepalive = dev, opts, keepalive
        self.log, self.rows, self.state = [], [], {}
        self.no_answer = False
        self.attempted, self.remaining, self.matrix = [], [], []

    def host(self, argv, timeout):
        argv = [str(a) for a in argv]
        start = time.monotonic()
        saved = host.TFW_CHECK_TIMEOUT
        try:
            if argv[0] == 'tfw-check':
                host.TFW_CHECK_TIMEOUT = timeout  # HostDevice's --timeout otherwise bounds only connect()
            code, out, err = self.dev.command(argv, timeout)
        finally:
            host.TFW_CHECK_TIMEOUT = saved
        self.log.append(
            '$ uv run xyf-host --timeout %g %s\nexit %d after %.2f s\n--- stdout\n%s\n--- stderr\n%s\n'
            % (timeout, ' '.join(argv), code, time.monotonic() - start, out, err)
        )
        if code in (dp.NO_ANSWER, dp.ERROR, P.ids.S_LINK):  # either processor lost: never probe again
            self.no_answer = True
        return code, out, err

    def ok(self, argv, timeout=T_ANSWER):
        code, out, err = self.host(argv, timeout)
        if self.no_answer:
            raise dp.StepFail('power cycle needed: %s stopped answering' % argv[0])
        if code:
            raise dp.StepFail('%s: %s' % (argv[0], dp.why(code, out, err)))
        return out

    def check_file(self, path, role=None):
        command = ['tfw-check', path] + (['--role', role] if role else [])
        deadline = time.monotonic() + (self.opts.wait if role else 0)
        while True:
            code, out, err = self.host(command, self.opts.check_timeout)
            if not role or code != P.ids.S_BUSY or time.monotonic() >= deadline:
                break
            host.pause(0.05)  # A closes the preceding upload's write window between requests
        if self.no_answer:
            raise dp.StepFail('power cycle needed: tfw.check stopped answering; no more commands sent')
        report = dp.parse_json(out)
        if not report or report.get('status') != 'ok':
            raise dp.StepFail('tfw-check %s: %s' % (path, report.get('status') if report else dp.why(code, out, err)))
        return report

    # -- steps -----------------------------------------------------------------------------
    def step_check(self):
        out = self.ok(['status'], self.opts.wait)
        hello = P.Hello(out.splitlines()[0].split(': ', 1)[-1]) if out else None
        self.state['hello'] = out.splitlines()[0] if out else ''
        if (
            hello is None
            or hello.pre_v1
            or not hello.answers(P.ids.TFW_CHECK_ROLE if self.opts.roles else P.ids.TFW_CHECK)
        ):
            self.state['need'] = NEED_BUILD
            raise dp.StepFail('the device does not answer tfw.check (%s)' % self.state['hello'])
        return hello.describe()

    def step_control(self, role=None):
        report = self.check_file('/yaffs2/xy-forever/state-a.txt' if role == 'a' else CONTROL, role)
        if (report['verdict'], report['reason'], report['public_key_sha256']) != ('fail', 'not_package', '00' * 32):
            raise dp.StepFail('expected fail/not_package with no key read: %s' % report)
        return 'not_package, no key read'

    def step_put(self, name):
        data = tfw_extract.cut(self.opts.firmware / name)
        local = self.folder / (name + '.cut')
        local.write_bytes(data)
        self.attempted.append(remote(name))
        self.remaining.append(remote(name))
        try:
            self.ok(['put-file', local, remote(name), '--yes-write'], T_PUT)
        finally:
            local.unlink()
        return '%s: %d B (CRC-32 checked by the device)' % (remote(name), len(data))

    def step_otp(self, name, role=None):
        report = self.check_file(remote(name), role)
        if role:
            if report.get('role') != role or report.get('verdict') not in ('pass', 'fail'):
                raise dp.StepFail('invalid processor result')
            if report.get('reason') == 'read':
                raise dp.StepFail('processor %s could not read the cut' % role)
            match = report['public_key_sha256'] == self.expected
            self.matrix.append(dict(package=name, role=role, public_key_match=match, verdict=report['verdict']))
            return '%s: %s; public key hash %s' % (
                role.upper(),
                report['verdict'],
                'matches' if match else 'differs or absent',
            )
        if (report['verdict'], report['reason']) != ('pass', 'pass'):
            raise dp.StepFail('%s: %s (%s)' % (name, report['verdict'], report['reason']))
        if report['public_key_sha256'] != self.expected:
            raise dp.StepFail('%s: pass, but the public key hash differs from the private key' % name)
        return 'pass; public key = the key recovered from the official signatures'

    def record_cleanup(self):
        folder = self.folder if self.opts.sim else self.cleanup_dir
        folder.mkdir(parents=True, exist_ok=True)
        self.cleanup_log = folder / ('cleanup-%s.json' % self.folder.name)
        self.cleanup_log.write_text(
            json.dumps(
                dict(
                    format='xyf-cleanup/1',
                    paths=self.attempted,
                    why='remove the OTP probe cuts after checking; no other files are removed',
                    restore='regenerate the cuts from the corresponding official packages with scripts/tfw_probe.py',
                ),
                indent=1,
            )
            + '\n'
        )

    def step_cleanup(self):
        removed = []
        for path in list(self.remaining):
            code, out, err = self.host(['rm', path], T_ANSWER)
            if self.no_answer:
                raise dp.StepFail('power cycle needed during cleanup; no more commands sent')
            if code not in (0, P.ids.S_NOT_FOUND):
                raise dp.StepFail('rm %s: %s' % (path, dp.why(code, out, err)))
            self.remaining.remove(path)
            removed.append(path)
        return '%d attempted cuts removed (or already absent); log %s' % (len(removed), self.cleanup_log)

    def step_keepalive(self):
        if self.opts.sim:
            return 'simulator: no keepalive'
        pid = self.keepalive.start(self.folder)
        if pid is None:
            return 'the enclosing device lease manages keepalive'
        return 'keepalive.sh pid %d (stop: touch %s/stop-keepalive)' % (pid, self.folder)

    def run(self, stage, name, function):
        start = time.monotonic()
        self.log = []
        try:
            detail, result = function(), 'PASS'
        except (dp.StepFail, tfw_extract.ExtractError, OSError) as error:
            detail, result = str(error), 'FAIL'
        row = dict(stage=stage, step=name, result=result, seconds=time.monotonic() - start, detail=detail)
        if getattr(self, 'folder', None):
            (self.folder / ('%02d-%s.txt' % (len(self.rows) + 1, name))).write_text(''.join(self.log))
        self.rows.append(row)
        return row


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sim', metavar='HOST:PORT,CONTROL', help='run against the device simulator (xyf-host --sim)')
    parser.add_argument(
        '--roles', help='comma-separated a,b for versioned per-processor evidence; default: legacy B probe'
    )
    parser.add_argument(
        '--packages', default=','.join(PACKAGES), help='official packages to cut and check (default %(default)s)'
    )
    parser.add_argument('--firmware', type=Path, default=paths.FIRMWARE / 'official', help='where they are')
    parser.add_argument('--wait', type=float, default=T_ANSWER, help='seconds the check waits for the device')
    parser.add_argument(
        '--check-timeout',
        type=float,
        default=T_CHECK,
        help='seconds for one tfw.check reply (default %(default)s; no recovery commands on timeout)',
    )
    parser.add_argument('--keep', action='store_true', help='with --sim: keep the evidence folder when nothing failed')
    return parser.parse_args(argv)


def main(argv=None, device=None, keepalive=None, sessions=None, cleanup_dir=None):
    opts = parse_args(argv)
    names = [n.strip() for n in opts.packages.split(',') if n.strip()]
    try:
        roles = opts.roles.split(',') if opts.roles else [None]
        if any(role not in ('a', 'b') for role in roles if role is not None) or len(set(roles)) != len(roles):
            raise ValueError('--roles takes a,b (each at most once)')
        if not math.isfinite(opts.check_timeout) or opts.check_timeout <= 0:
            raise ValueError('--check-timeout must be positive and finite')
        if any(not re.fullmatch(r'opxy_firmware_\d+(?:_\d+)*\.tfw', n) for n in names):
            raise ValueError('--packages takes official package basenames only')
        expected = hashlib.sha256(tfw_extract.public_key()).hexdigest()
        missing = [n for n in names if not (opts.firmware / n).is_file()]
        if missing or not names:
            raise tfw_extract.ExtractError('official package missing: %s' % ', '.join(missing or ['(none named)']))
        if opts.sim:
            host.sim_address(opts.sim)
            host.device_client.SIM = opts.sim
    except (tfw_extract.ExtractError, ValueError) as error:
        print('tfw_probe: %s' % error, file=sys.stderr)
        return dp.EXIT_USAGE
    keepalive = keepalive or dp.Keepalive()
    probe = Probe(device or dp.HostDevice(), opts, keepalive)
    probe.cleanup_dir = Path(cleanup_dir or paths.PRIVATE_ROOT / 'archive')
    probe.expected = expected
    if not opts.sim:
        keepalive.stop_running()
        if opts.roles:
            time.sleep(2)  # let the previous keeper release its USB port
    started = datetime.now(UTC)
    check = probe.run('check', 'check', probe.step_check)
    if check['result'] == 'FAIL' and (probe.no_answer or probe.state.get('need')):
        print(probe.state.get('need') or (dp.SIM_NEED if opts.sim else dp.NEED))
        return dp.EXIT_NEED
    probe.folder = dp.new_folder(Path(sessions or dp.SESSIONS), 'sim-tfw-probe' if opts.sim else 'tfw-probe')
    steps = [
        ('probe', 'control' + ('-' + role if role else ''), lambda r=role: probe.step_control(r)) for role in roles
    ]
    for name in names:
        steps += [('probe', 'put-' + name[len('opxy_firmware_') : -4], lambda n=name: probe.step_put(n))]
        steps += [
            (
                'probe',
                'otp-' + name[len('opxy_firmware_') : -4] + ('-' + role if role else ''),
                lambda n=name, r=role: probe.step_otp(n, r),
            )
            for role in roles
        ]
    failed = None if check['result'] == 'PASS' else 'check'
    for stage, name, function in steps:
        if failed:
            probe.rows.append(
                dict(stage=stage, step=name, result='SKIP', seconds=0.0, detail='after FAIL of ' + failed)
            )
            continue
        print('[%s] %s/%s' % (time.strftime('%H:%M:%S'), stage, name), file=sys.stderr, flush=True)
        if probe.run(stage, name, function)['result'] == 'FAIL':
            failed = '%s/%s' % (stage, name)
    if probe.attempted:
        probe.record_cleanup()  # before any deletion, including a cleanup after FAIL
        if probe.no_answer:
            probe.rows.append(
                dict(
                    stage='end',
                    step='cleanup',
                    result='SKIP',
                    seconds=0.0,
                    detail='power cycle needed; left: ' + ', '.join(probe.remaining),
                )
            )
        else:
            probe.run('end', 'cleanup', probe.step_cleanup)
    if not any(r['result'] == 'FAIL' for r in probe.rows):
        probe.run('end', 'keepalive', probe.step_keepalive)
    code = dp.EXIT_FAIL if any(r['result'] == 'FAIL' for r in probe.rows) else dp.EXIT_PASS
    summary = dict(
        format='xyf-tfw-probe/1',
        folder=str(probe.folder),
        argv=list(argv or sys.argv[1:]),
        started_utc=started.isoformat(),
        finished_utc=datetime.now(UTC).isoformat(),
        packages=names,
        hello=probe.state.get('hello'),
        steps=probe.rows,
        exit_code=code,
        remaining_cuts=probe.remaining,
        power_cycle_needed=probe.no_answer,
        roles=probe.matrix,
    )
    (probe.folder / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(dp.table(probe.rows, dp.settle_folder(probe.folder, opts.sim, code == dp.EXIT_PASS, opts.keep)))
    return code


if __name__ == '__main__':
    sys.exit(main())
