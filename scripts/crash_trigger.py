#!/usr/bin/env python3
"""Crash trigger (#102): a fault on purpose on B while its menu has the screen, to see the crash screen on real
faults. RISKY STEP (device:risky; the developer at the device). Test build only: `xyf_build.py --xyf-crash-trigger`
(the product plus diag.crash; never the product, whose builder audit refuses it). One fault per boot: B halts on
the crash screen until a power cycle; no reset, no storage write. The trigger is refused (busy) while A's
read-write window may be open (target/usb_service_crash_trigger.ipp) and (state) when the vector is not armed.

Kinds (diag.crash 0x63, payload u8 kind; target/key_ui_crash.ipp) and the cause line the screen should show
(target/crash_screen.hpp, EXCAUSE = SEQSTAT bits 0-5):
  excpt       1  EXCPT 1                      Cause 0x01: software exception (EXCPT)
  illegal     3  the undefined opcode 0x0001  Cause 0x21: undefined instruction
  unmapped    4  32-bit read of 0x90000000    Cause 0x26: data access to unmapped memory
No kind 2: misaligned reads do not fault on B (docs/hardware/boot.md); diag.crash answers unsupported to it.
"At" (RETX) is the faulting instruction, the module.asm label xyf_crash_at_<kind>; for EXCPT the instruction after
it (label + 2). "called from" (RETS) is the same for every kind: the return address of home_pump's CALL of
crash_command (the trigger's own calls are tail jumps). With --install or --build the script prints both from that
build's module.asm. 0x90000000 lies just above the 256 MiB of SDRAM; no stock data CPLB covers it (table at L2
0x0803c0b4: L2, boot ROM, SDRAM 0x80000000..0x90000000, 0x40000000..0x48000000, 4 KiB at 0x70000000 and 0x38000000),
and neither PEEK nor any MMR rule allows it. "SEQSTAT 0x...": the cause in the low 6 bits; bit 13 (0x2000, a soft
reset since power-on) may be set. The whole screen: "Crashed", "XY Forever stopped on an exception (processor B).",
the cause, "At 0x..., called from 0x...", "SEQSTAT 0x...", "image <8 hex>", "Turn the OP-XY off and on again."
Another cause, other addresses, a restart of B or a frozen menu without the screen: report it.

Steps (every command through xyf_host.py's own main(); diag.crash as a raw frame: it is in no build's cmds):
  check     STATUS: the menu answers. Otherwise one `NEED:` line, exit 3.
  session   hold on.
  install   --install BUILD_DIR (verified, mode xyf-crash-trigger): install --yes-install, REBOOT to the menu, back
            home with crash restarts 0.
  identity  HELLO mode=crash-trigger (and with a build, its image=), crash restarts 0.
  cplb      unmapped only: PEEK of the stock data CPLB table (L2, read-only): no entry covers 0x90000000.
  trigger   diag.crash KIND answers ok (busy: a read-write window may be open; wait 15 s and rerun).
  silent    the same wait for every kind: 5 s after the ok, then STATUS, no answer: B stopped (expected; on a
            halted B the attempt itself takes about 60 s: the host's first HELLO write blocks, #118). B still answering
            means no fault: FAIL. Then one `NEED:` line: what to read on the screen, then
            power off, wait 5 s, power on holding PLAY, and run this again with --after. Exit 3.
            (A FAIL after the check: hold off, as B still answers.)
  --after:  check, identity (crash restarts 0: B was not restarted alone), the screen as read (--screen TEXT,
            recorded), hold off, keepalive.sh (as device_pass).
Evidence: ../private/xy-forever/sessions/<date>-crash-trigger-<n>/ (--after: the day's last trigger folder without
an after result, else a new one); summary.json, after.json, one log per step. A simulator run (python3 -m
xy_forever.sim --crash-trigger: diag.crash halts B there, no fault, no screen) uses sim-crash-trigger, removed when
nothing failed unless --keep. Exit codes: 0 passed, 1 a step failed, 2 bad arguments, 3 NEED.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import struct
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import device_pass as dp                                   # noqa: E402
import xyf_build                                           # noqa: E402
import xyf_host as host                                    # noqa: E402
from xy_forever import usb_proto as P, usb_proto_ids as ids   # noqa: E402

T_CRASH = 0x63                    # diag.crash (dev, test build only; usb_service_crash_trigger.ipp)
KINDS = {'excpt': (1, 0x01, 'software exception (EXCPT)', 2),
         'illegal': (3, 0x21, 'undefined instruction', 0), 'unmapped': (4, 0x26, 'data access to unmapped memory', 0)}
UNMAPPED = 0x90000000
CPLB_TABLE, CPLB_BYTES = 0x0803c0b4, 0x78  # stock data CPLB descriptors {address, data}, -1 ends (inside PEEK's L2)
T_SILENT = 6.0
T_SETTLE = 5.0                    # silent: B's time to fault after the ok (the same for every kind)
MODE = xyf_build.CRASH_TRIGGER_MODE[len('xyf-'):]


def cplb_entries(data):
    """(start, end) of each stock data CPLB in the table: page size 1 KiB * 4^PSIZE (DCPLB_DATA bits 16-19)."""
    out = []
    for at in range(0, len(data) - 3, 8):
        start = struct.unpack_from('<I', data, at)[0]
        if start == 0xffffffff or at + 8 > len(data):
            break
        size = 1024 << 2 * (struct.unpack_from('<I', data, at + 4)[0] >> 16 & 15)
        out.append((start, start + size))
    return out


def expected(asm_text, kind):
    """(At, called from) the screen shows for a kind of this build: from its module.asm."""
    labels = {m.group(2): int(m.group(1), 16) for m in re.finditer(r'^([0-9a-f]{8}) <([^>]+)>:$', asm_text, re.M)}
    at = labels['xyf_crash_at_' + kind] + KINDS[kind][3]
    target, rets = 'xyf_crash_fault', None
    for _ in range(4):                     # follow tail jumps back to the CALL that sets RETS
        site = re.search(r'^([0-9a-f]{8}):\s+((?:[0-9a-f]{2} )+)\s*(CALL|JUMP\.L) 0x[0-9a-f]+ <%s>;' % re.escape(target),
                         asm_text, re.M)
        if not site:
            break
        if site.group(3) == 'CALL':
            rets = int(site.group(1), 16) + len(site.group(2).split())
            break
        start = int(site.group(1), 16)
        target = max((n for n, a in labels.items() if a <= start), key=lambda n: labels[n])
    return at, rets


def screen_lines(kind, at, rets):
    _, cause, name, _ = KINDS[kind]
    where = 'At 0x%08x, called from %s' % (at, '0x%08x' % rets if rets is not None else '(see module.asm)') \
        if at is not None else 'At <xyf_crash_at_%s in module.asm>' % kind
    return ['Cause 0x%02x: %s' % (cause, name), where, 'SEQSTAT 0x0000%04x (or 0x0000%04x)' % (cause, 0x2000 | cause)]


class Run(dp.Pass):
    """A device_pass Pass for its helpers (host, ok, status, restart, gone); its own steps."""

    def step_check(self):
        out = self.ok(['status'], self.opts.wait)
        self.state['hello'] = out.splitlines()[0] if out else ''
        return self.state['hello']

    def step_hold_on(self):
        self.ok(['hold', 'on'])
        return 'hold on'

    def step_install(self):
        detail = dp.Pass.step_install(self)
        return '%s; %s' % (detail, self.step_install_restart())

    def step_identity(self):
        decoded, usb = self.status()
        out = self.log[-1]
        m = re.search(r'connected \S+: (XYF1\S*)', out)
        hello = P.Hello(m.group(1) if m else '')
        wrong = []
        if hello.keys.get('mode') != MODE:
            wrong.append('mode=%s, expected %s (the crash trigger test build)' % (hello.keys.get('mode'), MODE))
        if self.opts.image and hello.keys.get('image') != self.opts.image:
            wrong.append('image=%s, expected %s' % (hello.keys.get('image'), self.opts.image))
        if usb.get('crash_restarts'):
            wrong.append('crash restarts %s' % usb['crash_restarts'])
        if wrong:
            raise dp.StepFail('; '.join(wrong))
        self.state['identity'] = dict(mode=hello.keys.get('mode'), image=hello.keys.get('image'),
                                      crash_restarts=usb.get('crash_restarts'))
        return 'image %s (%s), crash restarts %s' % (hello.keys.get('image'), MODE, usb.get('crash_restarts'))

    def step_cplb(self):
        path = self.folder / 'cplb.bin'
        self.ok(['peek', hex(CPLB_TABLE), hex(CPLB_BYTES), '--out', path])
        entries = cplb_entries(path.read_bytes())
        covering = [(a, b) for a, b in entries if a <= UNMAPPED < b]
        self.state['cplb'] = ['0x%08x..0x%08x' % e for e in entries]
        if len(entries) != 14 or covering:
            raise dp.StepFail('%d data CPLBs; covering 0x%08x: %s' % (len(entries), UNMAPPED, covering))
        return '14 data CPLBs, none covers 0x%08x' % UNMAPPED

    def step_trigger(self):
        try:
            link, text = self.dev.open(dp.T_ANSWER)
        except dp.NoAnswer as error:
            self.no_answer = True
            raise dp.StepFail('no answer (%s)' % str(error)[:120])
        self.note('connected: %s' % text)
        try:
            code, _ = link.raw(T_CRASH, bytes([KINDS[self.opts.kind][0]]), 3.0)
        finally:
            link.close()
        self.note('diag.crash %s: %s' % (self.opts.kind, P.status_name(code)))
        if code == ids.S_BUSY:
            raise dp.StepFail('busy: a read-write window may be open (or a job, MTP, an upload); wait 15 s, rerun')
        if code != ids.S_OK:
            raise dp.StepFail('diag.crash answered %s (unsupported: not the crash trigger test build; state: the '
                              'vector is not armed)' % P.status_name(code))
        self.state['sent_utc'] = datetime.now(timezone.utc).isoformat()
        return 'diag.crash %s (kind %d): ok' % (self.opts.kind, KINDS[self.opts.kind][0])

    def step_silent(self):
        self.sleep(T_SETTLE)
        code, out, err = self.host(['status'], T_SILENT)
        if code == 0:
            raise dp.StepFail('B still answers %g s after the trigger: no fault (%s)'
                              % (T_SETTLE, (out.splitlines() or [''])[0][:100]))
        return 'no answer %g s after the trigger (B stopped, as expected): %s' % (T_SETTLE,
                                                                                 dp.why(code, out, err)[:80])

    def step_screen(self):
        self.state['screen'] = self.opts.screen
        return 'read on the screen: %s' % self.opts.screen if self.opts.screen else 'no --screen text given'

    def step_hold_off(self):
        self.ok(['hold', 'off'])
        return 'hold off'

    def step_keepalive(self):
        if self.opts.sim:
            return 'simulator: no keepalive'
        pid = self.keepalive.start(self.folder)
        return 'keepalive.sh pid %d (stop: touch %s/stop-keepalive)' % (pid, self.folder)

    def run(self, stage, name, function):
        start, self.log = time.monotonic(), []
        try:
            detail, result = function(), 'PASS'
        except dp.StepFail as error:
            detail, result = str(error), 'FAIL'
        row = dict(stage=stage, step=name, result=result, seconds=time.monotonic() - start, detail=detail)
        if self.folder:
            prefix = 'after-' if self.opts.after else ''
            (self.folder / ('%s%02d-%s.txt' % (prefix, len(self.rows) + 1, name))).write_text(''.join(self.log))
        self.rows.append(row)
        return row


def after_folder(sessions, kind):
    """The day's last trigger folder without an after result, else a new folder."""
    day = datetime.now().strftime('%Y-%m-%d')
    found = sorted((p for p in sessions.glob('%s-%s-*' % (day, kind)) if (p / 'summary.json').is_file()
                    and not (p / 'after.json').is_file()), key=lambda p: int(p.name.rsplit('-', 1)[1]))
    return found[-1] if found else dp.new_folder(sessions, kind)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--kind', choices=sorted(KINDS), help='the fault to trigger (one per boot)')
    parser.add_argument('--after', action='store_true', help='after the power cycle: the menu answers, no restart')
    parser.add_argument('--install', metavar='BUILD_DIR', help='install this verified crash-trigger build first')
    parser.add_argument('--build', metavar='BUILD_DIR', help='the installed crash-trigger build (expected addresses)')
    parser.add_argument('--screen', metavar='TEXT', help='with --after: what the screen showed (recorded)')
    parser.add_argument('--sim', metavar='HOST:PORT', help='run against the device simulator (xyf_host.py --sim)')
    parser.add_argument('--wait', type=float, default=dp.T_ANSWER, help='seconds the check waits for the device')
    parser.add_argument('--keep', action='store_true', help='with --sim: keep the evidence folder when nothing failed')
    return parser.parse_args(argv)


def build_facts(opts):
    """Checks --install/--build; sets opts.image and opts.asm (module.asm text) or raises ValueError."""
    opts.image = opts.asm = None
    build = opts.install or opts.build
    if not build:
        return
    if opts.install:
        host.load_build(opts.install)               # verified, with the USB service (else ValueError)
    image, _, mode, _ = dp.build_identity(build)
    if mode != xyf_build.CRASH_TRIGGER_MODE:
        raise ValueError('%s is a %s build, not %s' % (build, mode, xyf_build.CRASH_TRIGGER_MODE))
    opts.image, opts.asm = image, (Path(build) / 'module.asm').read_text()


def main(argv=None, device=None, keepalive=None, sessions=None, sleep=time.sleep):
    opts = parse_args(argv)
    try:
        if opts.after == bool(opts.kind) or (opts.after and opts.install):
            raise ValueError('choose --kind KIND (the trigger) or --after (no --install)')
        build_facts(opts)
        if opts.sim:
            host.sim_address(opts.sim)
    except (ValueError, OSError, KeyError) as error:
        print('crash_trigger: %s' % error, file=sys.stderr)
        return dp.EXIT_USAGE
    host.SIM = opts.sim or None
    opts.build_sha = None
    if opts.install:
        import hashlib
        opts.build_sha = hashlib.sha256((Path(opts.install) / 'candidate.ldr').read_bytes()).hexdigest()
    keepalive = keepalive or dp.Keepalive()
    run = Run(device or dp.HostDevice(), opts, keepalive, sleep)
    run.rows = []
    if not opts.sim:
        keepalive.stop_running()
    started = datetime.now(timezone.utc)
    check = run.run('check', 'check', run.step_check)
    if check['result'] == 'FAIL' and run.no_answer:
        print(dp.SIM_NEED if opts.sim else dp.NEED)
        return dp.EXIT_NEED
    sessions = Path(sessions or dp.SESSIONS)
    kind = 'sim-crash-trigger' if opts.sim else 'crash-trigger'
    run.folder = after_folder(sessions, kind) if opts.after else dp.new_folder(sessions, kind)
    if opts.after:
        steps = [('after', 'identity', run.step_identity), ('after', 'screen', run.step_screen),
                 ('after', 'hold-off', run.step_hold_off), ('after', 'keepalive', run.step_keepalive)]
    else:
        steps = [('session', 'hold-on', run.step_hold_on)] + \
            ([('install', 'install', run.step_install)] if opts.install else []) + \
            [('trigger', 'identity', run.step_identity)] + \
            ([('trigger', 'cplb', run.step_cplb)] if opts.kind == 'unmapped' else []) + \
            [('trigger', 'trigger', run.step_trigger), ('trigger', 'silent', run.step_silent)]
    failed = None if check['result'] == 'PASS' else 'check'
    for stage, name, function in steps:
        if failed:
            run.rows.append(dict(stage=stage, step=name, result='SKIP', seconds=0.0, detail='after FAIL of ' + failed))
            continue
        print('[%s] %s/%s' % (time.strftime('%H:%M:%S'), stage, name), file=sys.stderr, flush=True)
        if run.run(stage, name, function)['result'] == 'FAIL':
            failed = '%s/%s' % (stage, name)
    if failed and failed != 'check' and not opts.after:
        run.run('end', 'hold-off', run.step_hold_off)        # B still answers: no hold left behind
    passed = not any(r['result'] == 'FAIL' for r in run.rows)
    lines = None
    if opts.kind:
        at, rets = expected(opts.asm, opts.kind) if opts.asm else (None, None)
        lines = screen_lines(opts.kind, at, rets)
    code = dp.EXIT_FAIL if not passed else dp.EXIT_PASS if opts.after else dp.EXIT_NEED
    summary = dict(format='xyf-crash-trigger/1', step='after' if opts.after else 'trigger', kind=opts.kind,
                   folder=str(run.folder), argv=list(argv if argv is not None else sys.argv[1:]),
                   started_utc=started.isoformat(), finished_utc=datetime.now(timezone.utc).isoformat(),
                   hello=run.state.get('hello'), identity=run.state.get('identity'), cplb=run.state.get('cplb'),
                   sent_utc=run.state.get('sent_utc'), expected_screen=lines, screen=run.state.get('screen'),
                   build=opts.install or opts.build, steps=run.rows, exit_code=code)
    (run.folder / ('after.json' if opts.after else 'summary.json')).write_text(json.dumps(summary, indent=1) + '\n')
    print(dp.table(run.rows, dp.settle_folder(run.folder, opts.sim, passed and opts.after, opts.keep)))
    if passed and not opts.after:
        print('NEED: read the OP-XY screen (kind %s): expect %s. Then power off, wait 5 s, power on holding PLAY '
              '(release at the logo), and run: python3 scripts/crash_trigger.py --after --screen "WHAT IT SHOWED"%s'
              % (opts.kind, ' | '.join(lines), ' --sim ' + opts.sim if opts.sim else ''))
    return code


if __name__ == '__main__':
    sys.exit(main())
