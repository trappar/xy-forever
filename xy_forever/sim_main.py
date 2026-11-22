"""uv run -m xy_forever.sim: the device simulator in the foreground, for a host tool or the web app
(xy_forever/sim.py has the simulator itself)."""

import subprocess
import sys
from pathlib import Path

from xy_forever import paths, procgroup
from xy_forever.sim import DEFAULT_PORT, PRE_V1_HELLO, STATE, Simulator, available, build, otp_fields, seed
from xy_forever.sim_fresh import seed_fresh


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description='Run the XY Forever device simulator in the foreground.')
    parser.add_argument(
        '--port',
        type=int,
        default=DEFAULT_PORT,
        help='USB endpoint port (control: port + 1); 0: two free ports, printed',
    )
    parser.add_argument(
        '--state',
        type=Path,
        default=STATE,
        help='folder for yaffs2/ (seeded when missing) and bootpart.bin (default %(default)s)',
    )
    parser.add_argument('--plain', action='store_true', help='power on without PLAY (boots the default: no USB)')
    parser.add_argument('--version', help="hello version= (default: this checkout's release version)")
    parser.add_argument('--pre-v1', action='store_true', help='serve the pre-v1 hello text (builds 153 and earlier)')
    parser.add_argument(
        '--crash-trigger',
        action='store_true',
        help='the crash trigger test build (#102): diag.crash halts B (no fault, no screen)',
    )
    parser.add_argument(
        '--ask', action='store_true', help='#65: Ask before USB control as settings.txt says (default: it reads Off)'
    )
    parser.add_argument(
        '--fresh',
        choices=('stock', 'missing', 'wrong'),
        help='seed a missing yaffs2/ just after Track 5 (sim_fresh.py, #178)',
    )
    parser.add_argument(
        '--no-otp',
        action='store_true',
        help="processor B's OTP answers nothing (default: the private keys, when present)",
    )
    parser.add_argument(
        '--idle-wall',
        action='store_true',
        help="idle device time follows the clock on the wall, so the device's own timers (a deferred "
        "save, a keepalive) fire at a person's pace: for interactive use (mise run sim), never "
        'in tests (sim/clock.cpp)',
    )
    args = parser.parse_args(argv)
    why = available()
    if why:
        print('simulator unavailable: %s' % why, file=sys.stderr)
        return 2
    state = paths.private_output(args.state)
    root = state / 'yaffs2'
    if not root.exists():
        seed_fresh(root, args.fresh) if args.fresh else seed(root)
    out = build(
        version=args.version,
        hello=PRE_V1_HELLO if args.pre_v1 else None,
        mode='xyf-crash-trigger' if args.crash_trigger else 'xyf-product',
    )
    otp = None if args.no_otp else otp_fields()
    print('OTP: %s' % ('the private keys' if otp else 'answers nothing (tfw.check: reason no_otp)'), file=sys.stderr)
    sim = Simulator(
        root,
        state / 'bootpart.bin',
        port=args.port,
        binary=out,
        play=not args.plain,
        otp=otp,
        ask=args.ask,
        deadline_s=None,
    )
    # The ports' line (a launcher reads it: clients/ts/test/simulators.ts), then the board on them.
    print(
        'simulator: USB endpoint 127.0.0.1:%d (uv run xyf-host --sim 127.0.0.1:%d status), control 127.0.0.1:%d,'
        ' storage %s; Ctrl-C stops it' % (sim.port, sim.port, sim.control_port, root),
        file=sys.stderr,
        flush=True,
    )
    # Its own process group (the board and both processors), ended with this process however it ends:
    # Ctrl-C, SIGTERM or SIGHUP, or (tie_to_parent) the process that started this one gone (#133).
    listener = sim.listen_control()
    argv = sim.argv(listener.fileno()) + (['--idle-wall'] if args.idle_wall else [])
    process = procgroup.popen(argv, stdin=subprocess.PIPE if otp else None, pass_fds=(listener.fileno(),))
    listener.close()
    try:
        if otp:
            process.stdin.write(otp)
            process.stdin.close()
        return process.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        procgroup.kill_group(process)
