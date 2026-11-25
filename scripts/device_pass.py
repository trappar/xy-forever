#!/usr/bin/env python3
"""Device pass: the regression that proves an XY Forever build on the OP-XY.

Every device command goes through xyf_host.py's own main() (imported), with its
checks, confirmation flags and dry-run defaults. The OS is never booted: this
script refuses `continue` without --dry-run, `reboot --to os` and `uninstall`.

Stages, in order. The first FAIL stops its stage; every later step is SKIP
(SAFETY.md: stop at the first unexpected result). The end stage always runs.
  check    always: HELLO + STATUS within --wait s. No answer: one `NEED:` line,
           exit 3, no session folder, nothing sent but HELLO. (keepalive.sh
           processes already running are stopped first: they share the port.) HELLO
           confirm=1 (#65): FAIL, CONFIRM_ON.
  session  always: hold on.
  read     default: status; state slots; the default entry's package metadata; ls firmware/
           lists the default and ls /yaffs2/xy-forever lists firmware; 8 ranged
           reads of its os.ldr (compared with the local stock os.ldr when the manifest
           names it); screenshot; peek of the reboot marker (held bit set).
  install  --install BUILD_DIR: `install --yes-install` (xyf_host's build checks;
           the device verifies the partition), REBOOT, fresh boot back home.
  protocol default (after install, before any restart): one port per step. HELLO text
           recorded; a pre-v1 device (no cmds) gets SKIP "pre-v1 device" for every step,
           nothing sent but HELLO. v1: proto 1.x and the HELLO round trip; after
           --install, build=/mode/cmds equal the installed build's manifest and its spec
           bitmap; error model with requests that change nothing (an unknown type:
           unsupported; file.write_staged outside /yaffs2/xy-forever/ with a 16 B staging
           that is never complete: refused, the path stays absent, stage.end discards
           it; the same request one byte short: bad_request; CONTINUE mode 2: link, then
           STATUS); throughput of 1 KiB ranged reads of the default os.ldr and of 1 KiB
           stage.data into an announced-but-never-complete staging (discarded by
           stage.end). No reply above HELLO's read= limit unless --big-replies (whole-file
           reads of 1-4 KiB, 4 KiB stage.data; Test 038: a 4 KiB reply hung B). Every
           request passes Wire, which refuses anything that could write or leave home.
  boot     default: `continue --dry-run` and back with a new result; REBOOT to the
           menu and back; keys STOP, TRACK3 (188: the Mods row; the screen must change and
           show the Mods row selected, the first row not; no PLAY).
  writes   default: put-file /yaffs2/xy-forever/log/device-pass.txt (2,500 B,
           overwritten each pass) and log/device-pass-2.txt (1,000 B) back to back,
           ranged read-back of both (one read-write window); no write for ~10 s:
           STATUS shows B closed that one window (op 9: it was open) and fetched the
           catalog; put-file firmware/xyf-pass-scratch/info.txt (a scratch entry with
           no os.ldr, its name new each pass) and, once the window closed, the menu's
           catalog (PEEK of B's copy) shows that name, no restart; set-default to another
           entry and back to the starting default; rm the scratch entry (info.txt, then its
           folder: also the one build 163's pass left); file commands in a scratch
           folder /yaffs2/xy-forever/xyf-pass-files it creates and removes (a leftover's
           two files and the folder removed first, by name): mkdir, put a
           300,000 B and a 2,500 B file, ls shows both with their sizes, get both back
           identical, rm both, rmdir, ls shows the folder gone (put and get KiB/s); then
           the same folder again: mv a file into a subfolder, mv that subfolder (a whole
           folder), mv onto an existing name (state), rm all; then again: cp a 100,000 B
           file on the device, get both identical, cp onto an existing name (state), rm all;
           REBOOT; both files and the default checked again, the scratch entry absent.
  mods     default: only when the device holds the package set (mods/<the local set's
           generation>/ with fm2op) and a library entry with os.ldr and content.sha256
           (else SKIP with the reason; also when the library holds mods-a and mods-b): mods apply
           fm2op --wait (the device assembles, sweeps tmp/job-* first, and commits into library entry
           mods-a, or mods-b when mods-a is there; --wait ends once file commands are served
           again), ls tmp/ at once (answered within its bound), get its os.ldr, SHA-256 equal to the
           host's one-shot assembly of the same inputs (host assembly only here); the menu catalog lists
           it "content not installed" (only then:) boot of it refused (no_content); the entry removed
           file by file.
  content  default: only when the device holds the package set, its default is a stock entry with
           content.sha256 and content/ is official (no record or state=official, op1_factory.json's SHA-256 its
           content.sha256 line; else SKIP with the reason): mods apply fm2op --wait (entry mods-a or -b),
           set-default to it (B's content switch runs first), the record says installed for it,
           content/op1_factory.json equals the entry's catalog and its content/ files are there (hashed);
           set-default back to the previous default (the switch restores the official catalog), the record
           says official, every file of content-official/content.sha256 matches (each file hashed when that
           read is estimated <= 60 s at the step's measured speed, else sizes against the listing taken before
           plus the catalog's hash: the row says which), the entry's files and folders are gone, content/ is
           listed exactly as before; the entry removed. A failure after the first switch still sets the
           previous default back (the row says the device's state). The OS is never booted.
           writes (set-default), mods and content: before and after, all saved configuration records,
           the default, the library and the first home row (STOP, TRACK1, screenshot: no
           "(to apply)" or "(rebuild)" mark) must be as found.
  config   default (keys): only when the device holds the package set (alone), the saved configuration has
           no saved mods and the saved base, if given, is the default, the default has its official content (else
           SKIP, nothing sent but reads): STOP, TRACK3 (the Mods row seen selected), PLAY: the Mods page;
           PLUS to fm2op (its row from the local set), PLAY: on, saved at once (#197: saved mods=fm2op, read
           back while the page is still shown); STOP, TRACK1: the first row's text longer (the "(rebuild)" mark);
           the Mods page again, PLAY: fm2op off, saved (mods= empty); home: the first row as at the start. Never
           PLAY on the first row (it would build and boot). A failure: STOP (up to 2) until the home menu shows; the
           row says what may be saved (fm2op off on the Mods page saves it back).
  mtp      default: mtp-test --hold-ms 2000 (097: processor A's storage executor;
           a hang inside it needs a power cycle), then home answers without a
           restart and a read works.
  end      always once the device answered: status (a crash count left by a
           restart is cleared by a clean REBOOT), hold off, then scripts/keepalive.sh
           started on the session folder. No step anywhere turns a watchdog on: there is none.

Simulator (--sim HOST:PORT, docs/architecture/simulator.md "Simulator"): the same steps through
xyf-host --sim; steps the simulator does not host are SKIP with the reason (SIM_SKIPS).

End state: the XY Forever boot menu (home) with USB, hold off, crash count 0,
polled by keepalive.sh (a host stays attached). Not in the OS. Hold is off
because keepalive.sh gives up after 3 misses and SAFETY.md forbids hold with
no host; the v3 menu never times out once a host said HELLO.
Before any other device command: touch FOLDER/stop-keepalive (this script stops
running keepalives itself; one that will not stop is a NEED line, exit 3).

Evidence: ../private/xy-forever/sessions/<date>-device-pass-<n>/: one file per
step (every command, its stdout and stderr), artifacts, summary.json. A simulator run
(<date>-sim-pass-<n>) removes its folder when every step passed, unless --keep.
Exit codes: 0 nothing failed, 1 a step failed, 2 bad arguments or build, 3 NEED.
"""

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import signal
import struct
import sys
import time
import traceback
import zlib
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from scripts.device_pass_keepalive import Keepalive
from xy_forever import census, config_records, package_records, paths, xyf_build
from xy_forever import usb_proto as P
from xy_forever import usb_proto_ids as ids
from xy_forever import xyf_host as host

ROOT = Path(__file__).resolve().parents[1]

EXIT_PASS, EXIT_FAIL, EXIT_USAGE, EXIT_NEED = 0, 1, 2, 3
NEED = 'NEED: power off, then power on holding PLAY; release it at the logo'
NO_ANSWER, ERROR, PORT_BUSY = 100, 101, 102  # command results: connect failed (no HELLO), exception, port held
NOT_FOUND = P.STATUS_NAMES.index('not_found')
SESSIONS = paths.SESSIONS
STOCK_OS_LDR = paths.STOCK_121 / 'os.ldr'

# Waits (s), sized to the known outcome time (OPERATING.md, usb_service.hpp).
T_ANSWER = 5.0  # home answers HELLO within ~10 ms (session logs); xyf_host: 5 s per call
T_GONE = 5.0  # REBOOT: USB stops ~150 ms after the reply (home_reboot: 50 + 100 ms)
T_BACK = 15.0  # REBOOT -> service answers again in ~3.7 s (Test 040)
T_DRY = 20.0  # dry run: A mounts (up to ~5 s, 076), reads the default (0.5 s, 014), hand-off
# 0.4 s (090), then the reset drops USB
T_INSTALL = 120.0  # install ~14 s + verify 1.3 s (039); xyf_host's own bound is 900 s
HOLD_MS = 2000
T_MTP = 15.0  # mtp-test: HOLD_MS + the storage calls (097)
T_KEEPALIVE = 10.0  # keepalive.sh's first status
MARKER, MARKER_MAGIC = 0x8BFFF000, 0x52465859  # usb_service.hpp reboot marker ("YXFR")
MARKER_BITS = ((0x100, 'held'), (0x400, 'menu'))
PASS_FILE = '/yaffs2/xy-forever/log/device-pass.txt'
PASS_FILE2 = '/yaffs2/xy-forever/log/device-pass-2.txt'
SCRATCH_ID = 'xyf-pass-scratch'  # the library change (an entry without os.ldr)
SCRATCH_INFO = '/yaffs2/xy-forever/firmware/%s/info.txt' % SCRATCH_ID
FILES_DIR = '/yaffs2/xy-forever/xyf-pass-files'  # the file commands' scratch folder (removed again)
FILES_LEAVES = ('big.bin', 'small.txt')  # the files it holds (an interrupted pass leaves them)
LIBRARY = '/yaffs2/xy-forever/firmware'  # never empty: the default entry is in it
CONTENT_SCRATCH = '/yaffs2/content/xyf-pass-scratch.txt'  # no official content has this name
# file.rename in the same scratch folder: a file renamed into a subfolder, then that subfolder
# renamed as a whole (left by an interrupted pass: these names, removed first, deepest first).
MOVE_LEFT = ('a.txt', 'sub/b.txt', 'sub2/b.txt', '--dir sub', '--dir sub2')
STATE = P.STATUS_NAMES.index('state')
COPY_LEFT = ('a.bin', 'b.bin')  # file.copy's source and target in FILES_DIR
COPY_BYTES = 100000
FILES_BIG = 300000  # a few hundred KB: the measured put and get
T_FILES = 240.0
# The mods/apply step. fm2op on B: ~8,500 one-KiB requests (simulator count), ~45 s at the measured
# 5.6 ms per request; the bound leaves room for yaffs write pauses.
MODS_PROFILE, MODS_MACHINE, MODS_NAME = 'opxy-1.2.1', 'fm2op', 'XY Forever pass'
MODS_PACKAGES = Path(os.environ.get('XYF_MOD_PACKAGES', paths.PUBLISHED_MODS))
MODS_CATALOG = paths.CATALOG_121
T_MODS = 180.0
# The content/switch step. A switch is one file request per step (fm2op: a few dozen files); its own
# bound per set-default leaves room for yaffs write pauses. The content check hashes every official file only
# when that read is estimated at most FULL_READ seconds (the speed of the step's own gets), else sizes.
T_SWITCH = 300.0
FULL_READ = 60.0
CONTENT = '/yaffs2/content'
CONTENT_RECORD = '/yaffs2/xy-forever/content-state.txt'
CONTENT_SAVED_LIST = '/yaffs2/xy-forever/content-official/content.sha256'
SETTLE = 9.0  # B closes the write window 8 s after the last write (storage_server window_settle_ms) ...
T_WINDOW = 30.0  # ... then op 9 (yaffs checkpoint) and the catalog fetch: polled once a second
ALT_DEFAULTS = ('opxy-121', 'opxy-121-stock')
RANGES = 8
# The saved configuration (target/xyf_config.hpp) and what the home menu's first row shows. Screenshots (xyf_host screen_png: 480 x 216 RGB, filter none): ui_screens
# draw_menu puts seven rows 18 px apart from y 60 (build 184: Settings; row i's white bar: y 58 + 18 i .. +16,
# x 10..469, its text y 60 + 18 i .. +13 from x 20).
# #197: four rows (no MTP mode, no Info, no Uninstall row): 22 px apart (row_selected's probe, 20 px apart, stays
# inside each bar), row i's bar y 56 + 22 i .. +19, its text y 60 + 22 i .. +13.
ROW0_BAND = (59, 78)  # the first row's pixels (bar and text), compared before and after
HOME_ROWS, ROW_TOP, ROW_PITCH = 4, 60, 20  # home rows; the first row's probe base; rows apart (row_selected)
BAR_X, BAR_RIGHT = 12, 470  # inside a row's bar, left of its text; the bar's right end
T_PAGE = 30  # the Mods page reads its package list (screenshots 1.5 s apart until two equal)
T_SAVED = 10  # s: a flip on the Mods page is written (quiet_ms after it, then the config job)
MARK_GROWTH = 60  # px: "(rebuild)" lengthens the first row's text by more than this
# Protocol stage (docs/PROTOCOL.md).
PRE_V1 = 'pre-v1 device'
HELLO_ROUNDS = 5
REFUSED_PATH = '/yaffs2/upgrade/device-pass-refused.txt'  # readable; 1.13: the stock updater's inbox is never written
TINY = bytes(range(16))  # refused step: staged, never complete
TOKEN = bytes((0xFF,)) * 32  # its confirmation token: no staging's SHA-256
SPEED_BYTES = 64 * 1024  # per throughput measurement ...
SPEED_BUDGET = 8.0  # ... or this many seconds, whichever comes first
T_SPEED = 20.0
BIG_CHUNK = 4096  # --big-replies: stage.data size (Test 034: a 4 KiB frame got no reply)

STAGES = (  # (stage, steps: (name, bound in s, what it does))
    ('check', (('check', None, 'HELLO + STATUS: the USB service answers at home (else NEED, exit 3)'),)),
    ('session', (('hold-on', T_ANSWER, 'hold on'),)),
    (
        'read',
        (
            ('status', T_ANSWER, 'STATUS: service version 4, role b'),
            ('read-state', T_ANSWER, 'whole reads of state-a.txt/state-b.txt: the default entry'),
            ('read-info', T_ANSWER, 'read default package metadata'),
            (
                'ls-library',
                T_ANSWER,
                'ls firmware/ lists the default entry, ls /yaffs2/xy-forever/ lists '
                'firmware (build 165 listed every folder empty)',
            ),
            ('read-ranges', T_ANSWER, '%d ranged reads (1 KiB) of firmware/<default>/os.ldr' % RANGES),
            ('screenshot', T_ANSWER, 'screenshot of home'),
            ('peek', T_ANSWER, 'peek 16 B at 0x%08x: reboot marker, held bit set' % MARKER),
        ),
    ),
    (
        'install',
        (
            ('install', T_INSTALL, 'install BUILD_DIR --yes-install: device verifies the partition'),
            ('install-restart', T_GONE + T_BACK, 'REBOOT --to menu; a fresh boot answers'),
        ),
    ),
    (
        'protocol',
        (
            (
                'hello',
                T_ANSWER,
                'HELLO text; pre-v1: every protocol step SKIP; v1: proto 1.x, %d HELLO round trips' % HELLO_ROUNDS,
            ),
            ('build', T_ANSWER, 'image=, version=, mode, cmds: those of the --install build (manifest, spec bitmap)'),
            ('unsupported', T_ANSWER, 'an unknown type (no command, not in cmds): unsupported'),
            (
                'refused',
                T_ANSWER,
                'stage 16 of 17 B; file.write_staged %s: refused, absent '
                'before and after; stage.end discards (state)' % REFUSED_PATH,
            ),
            ('bad-request', T_ANSWER, 'the same file.write_staged one byte short: bad_request'),
            ('continue-link', T_ANSWER, 'CONTINUE mode 2: link; STATUS still answers'),
            (
                'read-speed',
                T_SPEED,
                'ranged reads of firmware/<default>/os.ldr: 1 KiB, and HELLO read= '
                'if larger (%d KiB or %g s each); KiB/s' % (SPEED_BYTES >> 10, SPEED_BUDGET),
            ),
            (
                'stage-speed',
                T_SPEED,
                '1 KiB stage.data into a never-complete staging (%d KiB or %g s); '
                'stage.end discards it; KiB/s' % (SPEED_BYTES >> 10, SPEED_BUDGET),
            ),
        ),
    ),
    (
        'boot',
        (
            ('dry-run', T_DRY + T_BACK, 'continue --dry-run; back home with a new dry-run result'),
            ('restart', T_GONE + T_BACK, 'REBOOT --to menu; back home, crash restarts 0'),
            (
                'keys',
                T_ANSWER,
                'keys STOP, TRACK3: screen stable, then changed, the Mods row selected and the first row not (no PLAY)',
            ),
        ),
    ),
    (
        'writes',
        (
            ('put-file', 2 * T_ANSWER, 'put-file %s (2,500 B) and %s (1,000 B) back to back' % (PASS_FILE, PASS_FILE2)),
            ('read-back', T_ANSWER, 'ranged read-back of both, identical'),  # set-default: xyf_host waits 75 s
            (
                'window-closed',
                T_WINDOW,
                'no write for %g s: STATUS shows one window closed by B (op 9: it was '
                'open) and the catalog fetched' % SETTLE,
            ),
            (
                'library-change',
                T_ANSWER + T_WINDOW,
                'put-file %s (new name), window closed: the menu catalog (PEEK) shows it, no restart' % SCRATCH_INFO,
            ),
            (
                'set-default-alt',
                75.0,
                'set-default to another usable entry, content installed (xyf_host reads the slots back)',
            ),
            ('set-default-back', 75.0, 'set-default back to the starting default'),
            ('scratch-cleanup', 2 * T_ANSWER, 'rm %s and rm --dir its folder; ls firmware/: gone' % SCRATCH_INFO),
            (
                'file-commands',
                T_FILES,
                'in %s (created, removed): mkdir; put %d B and 2,500 B; ls sizes; get '
                'both identical; rm both; rmdir; ls: gone; put/get KiB/s' % (FILES_DIR, FILES_BIG),
            ),
            (
                'rename',
                T_FILES,
                'in %s (created, removed; build 169+): put a.txt (2,500 B); mv it to '
                'sub/b.txt; mv sub to sub2 (a whole folder with a file in it); ls; get '
                'sub2/b.txt identical; mv onto an existing name: state; rm all' % FILES_DIR,
            ),
            (
                'copy',
                T_FILES,
                'in %s (created, removed; build 170+): put a.bin (%d B); cp it to b.bin '
                '(on the device); get both identical; cp onto an existing name: state; rm all'
                % (FILES_DIR, COPY_BYTES),
            ),
            (
                'content-scratch',
                T_FILES,
                'put %s (2,500 B; build 168+: the content folder), get it '
                'identical, rm it; ls content/: gone' % CONTENT_SCRATCH,
            ),
            ('writes-restart', T_GONE + T_BACK, 'REBOOT --to menu'),
            ('after-restart', T_ANSWER, 'file and default unchanged on the fresh boot; no scratch entry'),
        ),
    ),
    (
        'mods',
        (
            (
                'apply',
                T_MODS + T_FILES,
                'mods apply %s --wait (device assembly into library entry mods-a/-b; '
                'then file commands served again: busy while B closes the window); ls '
                'tmp/ at once: answered; get its os.ldr: SHA-256 equals the host '
                'reference; listed "content not installed", boot refused (no_content); '
                'entry removed (SKIP without a package set or a base entry with '
                'content.sha256, or with mods-a and mods-b there)' % MODS_MACHINE,
            ),
        ),
    ),
    (
        'content',
        (
            (
                'switch',
                T_MODS + 2 * T_SWITCH + T_FILES,
                'mods apply %s --wait (entry mods-a/-b), set-default to it: the content switch runs; record '
                'installed for it, content/op1_factory.json = its catalog, its files there; set-default back: '
                'record official, every content.sha256 file matches (hashes, or sizes + the catalog hash when a '
                'full read is over %g s), its files and folders gone, content/ listed as before; entry removed '
                '(SKIP without a package set, a stock default with content.sha256 and official content)'
                % (MODS_MACHINE, FULL_READ),
            ),
        ),
    ),
    (
        'config',
        (
            (
                'pending',
                T_ANSWER * 6 + 2 * T_PAGE + 2 * T_SAVED,
                'keys (only with the package set, official content and a saved configuration without mods; '
                'else SKIP: reads only, no key): Mods, %s on: saved at once (mods=, the page still '
                'shown); home: the first row shows the "(rebuild)" mark; Mods, %s off: saved; home: the mark gone. '
                'Never PLAY on the first row, never a boot; on a failure STOP until home'
                % (MODS_MACHINE, MODS_MACHINE),
            ),
        ),
    ),
    (
        'mtp',
        (
            ('mtp-test', T_MTP, 'mtp-test --hold-ms %d --yes-test: all 11 steps' % HOLD_MS),
            ('home-after-mtp', T_ANSWER, 'no restart (frames kept, crash restarts 0), a read works, screenshot'),
        ),
    ),
    (
        'end',
        (
            ('end-status', T_BACK + T_GONE + T_BACK, 'STATUS; a clean REBOOT if crash restarts > 0'),
            ('hold-off', T_ANSWER, 'hold off'),
            ('keepalive', T_KEEPALIVE, 'start scripts/keepalive.sh FOLDER; its first STATUS'),
        ),
    ),
)
DEFAULT_STAGES = ('read', 'protocol', 'boot', 'writes', 'mods', 'content', 'config', 'mtp')
# Steps the device simulator does not host (--sim): SKIP with this reason.
SIM_SKIPS = {
    'keys': 'simulator: the menu screen and keys are not hosted',
    'keepalive': 'simulator: no keepalive.sh (the simulator keeps running without a host)',
}
SIM_NEED = 'NEED: start the simulator (uv run -m xy_forever.sim), then rerun with --sim 127.0.0.1:7341,7342'
CONFIRM_ON = 'Switch off Settings > Ask before USB control on the device first'


def confirm_on(out):
    """Whether the HELLO in xyf_host's output ("connected PORT: TEXT") says confirm=1."""
    hello = re.search(r'^connected .*?: (\S+)$', out, re.M)
    return bool(hello) and P.Hello(hello.group(1)).keys.get('confirm') == '1'


def mods_reference(packages, machines, name):
    """SHA-256 of the os.ldr the host's one-shot assembler (host/mod_assemble.cpp, proven against core.mjs)
    makes from the local package set, the stock 1.2.1 os.ldr and catalog: the device pass's reference only."""
    import tempfile

    from xy_forever import modpatch as MP

    paths.SESSIONS.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='mods-ref-', dir=paths.SESSIONS))
    try:
        binary = MP.build()
        (work / 'job.txt').write_text(
            MP.job_text(packages, machines, [], paths.STOCK_121 / 'os.ldr', MODS_CATALOG, name=name)
        )
        report = MP.run(binary, work / 'job.txt', work / 'ref')
        if [report[k] for k in ('input', 'patch', 'verify', 'content', 'copy')] != ['ok'] * 5:
            raise RuntimeError('host reference failed: %s' % report)
        return hashlib.sha256((work / 'ref' / 'os.ldr').read_bytes()).hexdigest()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def png_pixels(data):
    """xyf_host's screenshot PNG (8-bit RGB) -> rows of RGB bytes."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except OSError, SyntaxError, ValueError:
        image = None
    if image is None or image.format != 'PNG' or image.mode != 'RGB':
        raise StepFail("the screenshot is not xyf_host's plain RGB PNG")
    raw, stride = image.tobytes(), 3 * image.width
    return [raw[y * stride : (y + 1) * stride] for y in range(image.height)]


def white(rows, x, y):
    return rows[y][3 * x : 3 * x + 3] == b'\xff\xff\xff'


def row_selected(rows, row):
    """Is home menu row `row` (0..6) the selected one (its white bar)?"""
    return white(rows, BAR_X, ROW_TOP + ROW_PITCH * row + 8)


MODS_KINDS = ('synth', 'drum', 'sample', 'effect', 'player', 'extension')  # xyf_mods Kind order
# packages only (status, filter, all rows first); xyf_mods_view visible_rows, rows_top, row_height
MODS_VISIBLE_ROWS, MODS_ROWS_TOP, MODS_ROW_HEIGHT = 9, 43, 18


def mods_row(packages, machine):
    """Machine's row on the Mods page and the page's row count, as (position, rows), or None when the local
    set has no readable package of that name. Rows (packages only, the first selected when the page opens),
    filter All: the packages xyf_mods sooner() orders: kind (MODS_KINDS order), then name (its first 22 bytes, A-Z folded), then
    folder (without "ext-")."""

    def fold(text):
        return bytes(c + 32 if 65 <= c <= 90 else c for c in text)

    rows = []
    for package in sorted(Path(packages).glob('*')):
        folder = package.name
        if folder == 'base' or not (package / 'files.sha256').is_file():
            continue  # the device lists a package only with its files.sha256
        try:
            fields = package_records.read_values(package)
        except OSError, ValueError:
            continue
        extension = folder.startswith('ext-')
        kind = fields.get('machine', '')
        if (
            kind not in MODS_KINDS
            or (kind == 'extension') != extension
            or any(k not in fields for k in ('order', 'objects', 'code', 'data', 'state'))
        ):
            continue  # the device does not list it
        ident = (folder[4:] if extension else folder).encode()
        name = (fields.get('name') or '').encode()[:22] or ident[:22]
        rows.append(((MODS_KINDS.index(kind), fold(name), ident), folder))
    rows.sort()
    folders = [r[1] for r in rows]
    if folders.count(machine) != 1:
        return None
    return folders.index(machine), len(rows)


def mods_screen_row(position, rows):
    """xyf_mods_view first_row(): the selected row's place among the visible rows."""
    half = MODS_VISIBLE_ROWS // 2
    if rows <= MODS_VISIBLE_ROWS or position < half:
        return position
    first = min(position - half, rows - MODS_VISIBLE_ROWS)
    return position - first


def mods_row_selected(rows, row):
    """Is visible Mods page row `row` (0..8) the selected one (its white bar left of the text)?"""
    return white(rows, BAR_X, MODS_ROWS_TOP + MODS_ROW_HEIGHT * row + 6)


class StepFail(Exception):
    pass


def service_check(usb):
    """B's USB service, its result block version 4 or later (5: the build identity, 6: replies that could not get out,
    #314; a later version only uses words that were unused before, so the pass reads every one), else StepFail. A pass
    must be able to update a device running any newer build."""
    version = usb.get('version')
    if not isinstance(version, int) or version < 4 or usb.get('role') != 1:
        raise StepFail('unexpected service: version %s, role %s' % (version, usb.get('role')))


class StepSkip(Exception):
    """The step does not apply (a pre-v1 device): a SKIP row with this detail."""


class NoAnswer(Exception):
    """No XY Forever service answered HELLO (a protocol-stage port)."""


def refuse_os(argv, allow_boot=False):
    """No step may leave the device in the OS or remove XY Forever (allow_boot: scripts/mod_boot.py's
    boot step, library.boot of its scratch entry)."""
    if argv[0] == 'continue' and '--dry-run' not in argv:
        raise ValueError('device_pass never boots the OS: continue needs --dry-run')
    if argv[0] == 'reboot' and 'os' in argv:
        raise ValueError('device_pass never boots the OS: reboot --to os')
    if argv[0] == 'uninstall':
        raise ValueError('device_pass never uninstalls')
    if argv[0] == 'boot' and not allow_boot:
        raise ValueError('device_pass never boots the OS: boot (library.boot)')


WRITE_AREA = '/yaffs2/xy-forever/'
CONTINUE_LINK = struct.pack('<I', 2)  # CONTINUE mode 2: not part of v1, answered `link`


class Wire:
    """Every protocol-stage request goes through call(), which refuses anything that could change
    the device: no install, uninstall, set-default or dev command; FILE_WRITE only to a path
    outside the write area; CONTINUE only with mode 2; staging only as announced and never
    complete (stage.data stops one byte short, so stage.end can only discard it)."""

    def __init__(self, link, log):
        self.link, self.log = link, log
        self.announced, self.received = None, 0  # this port's staging

    def check(self, kind, payload, raw):
        name = ids.COMMANDS[kind]['name'] if kind in ids.COMMANDS else '0x%02x' % kind
        if raw or kind not in ids.COMMANDS:
            if not (raw and kind < P.REPLY and kind not in ids.COMMANDS and not payload):
                # empty: builds before 1.14 still act on 0x33 (diag.guard) and 0x60 (diag.hang) with a payload
                raise ValueError('device_pass sends a raw frame only for an unknown type, empty, not %s' % name)
        elif kind == P.PUT_DATA:
            if (
                self.announced is None
                or len(payload) < 5
                or struct.unpack_from('<I', payload)[0] + len(payload) - 4 >= self.announced
            ):
                raise ValueError('device_pass never completes a staging (stage.data)')
        elif kind == P.PUT_END:
            if self.announced is None or self.received >= self.announced:
                raise ValueError('device_pass sends stage.end only to discard its own incomplete staging')
        elif kind == P.FILE_WRITE:
            if payload.split(b'\0', 1)[0].decode('ascii', 'replace').startswith(WRITE_AREA):
                raise ValueError('device_pass never sends file.write_staged into %s' % WRITE_AREA)
        elif kind == P.CONTINUE:
            if payload != CONTINUE_LINK:
                raise ValueError('device_pass sends CONTINUE only with mode 2 (never boots the OS)')
        elif kind not in (P.HELLO, P.STATUS, P.READ_FILE, P.PUT_BEGIN):
            raise ValueError('device_pass protocol stage never sends %s' % name)
        return name

    def call(self, kind, payload=b'', timeout=None, raw=False):
        """(status, reply after the status). A missing reply or xyf_host's refusal is a StepFail."""
        name = self.check(kind, payload, raw)
        if timeout is None:
            timeout = ids.COMMANDS[kind]['timeout_ms'] / 1000.0 if kind in ids.COMMANDS else T_ANSWER
        start = time.monotonic()
        try:
            code, body = (self.link.raw if raw else self.link.call)(kind, payload, timeout)
        except host.Unsupported as error:
            raise StepFail('xyf_host refused %s: %s' % (name, error))
        except OSError as error:  # TimeoutError: no reply within timeout
            self.log.append(
                '> %s %d B: %s after %.1f ms\n' % (name, len(payload), error, (time.monotonic() - start) * 1000)
            )
            raise StepFail('%s: no reply within %g s (%s)' % (name, timeout, error))
        self.log.append(
            '> %s %d B -> %s, %d B, %.1f ms\n'
            % (name, len(payload), P.status_name(code), len(body), (time.monotonic() - start) * 1000)
        )
        if kind == P.PUT_BEGIN:
            self.announced, self.received = (struct.unpack_from('<I', payload)[0] if code == 0 else None), 0
        elif kind == P.PUT_DATA and code == 0 and len(body) >= 4:
            self.received = struct.unpack_from('<I', body)[0]
        elif kind == P.PUT_END:
            self.announced = None
        return code, body


class HostLink:
    """One open port for the protocol stage: xyf_host's Device, its checks and functions."""

    def __init__(self, dev):
        self.dev = dev

    def call(self, kind, payload, timeout):
        try:
            return self.dev.call(kind, payload, timeout)  # refuses types missing from HELLO cmds
        except TimeoutError:
            self.dev.parser.reset()  # a late reply is then dropped
            raise

    def raw(self, kind, payload, timeout):
        # The `unsupported` check must send a type the HELLO cmds bitmap does not list, which
        # host.Device.call rightly refuses. Same exchange with its bitmap check lifted for this
        # one request (Wire allows raw only for a type no command uses); xyf_host is unchanged.
        saved, self.dev.hello_info = self.dev.hello_info, None
        try:
            return self.call(kind, payload, timeout)
        finally:
            self.dev.hello_info = saved

    def close(self):
        with contextlib.suppress(OSError):
            self.dev.t.close()


class HostDevice:
    """The real device: xyf_host.main() in this process, output captured, every port closed."""

    def __init__(self):
        self.port = None

    def command(self, argv, timeout, install_timeout=None, mtp_timeout=None):
        out, err, opened = io.StringIO(), io.StringIO(), []
        saved = host.connect, host.INSTALL_TIMEOUT, host.MTP_TEST_TIMEOUT

        def connect(wait, *args, **kwargs):
            dev, port, text = saved[0](wait, *args, **kwargs)
            opened.append(dev.t)
            self.port = port
            return dev, port, text

        host.connect = connect
        host.INSTALL_TIMEOUT = install_timeout or host.INSTALL_TIMEOUT
        host.MTP_TEST_TIMEOUT = mtp_timeout or host.MTP_TEST_TIMEOUT
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = host.main(['--timeout', '%g' % timeout] + list(argv))
        except host.device_client.PortBusy as error:  # another program holds the port: no power cycle helps
            code = PORT_BUSY
            err.write('%s\n' % error.code)
        except SystemExit as error:  # connect(): no service answered HELLO
            code = error.code if isinstance(error.code, int) else NO_ANSWER
            err.write('%s\n' % error.code)
        except Exception as error:  # no reply, port lost, refused build
            code = ERROR
            err.write('%s: %s\n' % (type(error).__name__, error))
        finally:
            host.connect, host.INSTALL_TIMEOUT, host.MTP_TEST_TIMEOUT = saved
            for transport in opened:
                with contextlib.suppress(OSError):
                    transport.close()
        return code or 0, out.getvalue(), err.getvalue()

    def open(self, timeout):
        """A port for the protocol stage: (link, HELLO text), via xyf_host.connect()."""
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                dev, port, text = host.connect(timeout)
        except SystemExit as error:
            raise NoAnswer('%s; %s' % (error.code, err.getvalue().strip().splitlines()[-1:]))
        dev.on_log = None
        self.port = port
        return HostLink(dev), text

    def port_gone(self, timeout):
        """True once the last port disappears (the device reset or left USB; on the simulator its
        endpoint stops listening)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if host.device_client.SIM and not host.sim_listening(host.device_client.SIM):
                return True
            if not host.device_client.SIM and (not self.port or not os.path.exists(self.port)):
                return True
            host.pause(0.05)
        return False


def why(code, out, err):
    text = [line.strip() for line in (out + '\n' + err).replace('\r', '\n').splitlines() if line.strip()]
    if code == NO_ANSWER:
        tail = [line for line in text if 'HELLO' in line or 'not found' in line]
        return 'no answer (%s)' % (tail[-1] if tail else 'no port')[:120]
    if code == PORT_BUSY:
        return next((line for line in text if 'in use by' in line), 'the port is in use by another program')[:240]
    return ('%s (exit %d)' % (text[-1] if text else 'no output', code))[:160]


def parse_json(out):
    try:
        return json.loads(out[out.index('{') :])
    except ValueError:
        return None


def parse_info(data):
    fields = {}
    for line in data.decode('utf-8', 'replace').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            fields[key.strip()] = value.strip()
    return fields


def pass_file(name):
    lines = ['XY Forever device pass %s line %03d' % (name, i) for i in range(70)]
    return ('\n'.join(lines) + '\n').encode('ascii')[:2499] + b'\n'


class Pass:
    allow_boot = False  # refuse_os: never library.boot (mod_boot.py sets it)

    def __init__(self, dev, opts, keepalive, sleep=None):
        # sleep: the host's pause on the device (on the simulator its clock moves on: xyf_host.pause)
        self.dev, self.opts, self.keepalive, self.sleep = dev, opts, keepalive, sleep or host.pause
        self.folder = None
        self.prefix = ''
        self.log = []
        self.pending = []  # (file name, text) written once the folder exists
        self.state = {}
        self.no_answer = False
        self.port_busy = None  # the host's line when another program held the port
        self.hello = None  # P.Hello of the protocol stage

    # -- helpers -----------------------------------------------------------------------------
    def host(self, argv, timeout, **limits):
        argv = [str(a) for a in argv]
        refuse_os(argv, self.allow_boot)
        start = time.monotonic()
        code, out, err = self.dev.command(argv, timeout, **limits)
        self.log.append(
            '$ uv run xyf-host --timeout %g %s\nexit %d after %.2f s\n--- stdout\n%s\n--- stderr\n%s\n'
            % (timeout, ' '.join(argv), code, time.monotonic() - start, out, err)
        )
        return code, out, err

    def busy(self, code, err):
        """A held port: what to tell the user (the host's own line), kept for the NEED line."""
        if code == PORT_BUSY:
            self.port_busy = next((line for line in err.splitlines() if 'in use by' in line), err.strip())

    def ok(self, argv, timeout=T_ANSWER, **limits):
        code, out, err = self.host(argv, timeout, **limits)
        if code:
            self.busy(code, err)
            if code == NO_ANSWER:
                self.no_answer = True
            detail = why(code, out, err)
            raise StepFail(detail if detail.startswith(argv[0]) else '%s: %s' % (argv[0], detail))
        return out

    def note(self, text):
        self.log.append('# %s\n' % text)

    def artifact(self, suffix):
        return (self.folder or Path('.')) / ('%s-%s' % (self.prefix, suffix))

    def status(self, timeout=T_ANSWER):
        decoded = parse_json(self.ok(['status'], timeout))
        usb = (decoded or {}).get('usb_service')
        if not usb:
            raise StepFail('status: no USB service block in the reply')
        return decoded, usb

    def gone(self, timeout, what):
        start = time.monotonic()
        if not self.dev.port_gone(timeout):
            raise StepFail('USB still up %g s after %s' % (timeout, what))
        return time.monotonic() - start

    def restart(self):
        self.ok(['reboot', '--yes-reboot', '--to', 'menu'])
        start = time.monotonic()
        self.gone(T_GONE, 'REBOOT')
        decoded, usb = self.status(T_BACK)
        if usb.get('crash_restarts'):
            raise StepFail('came back with crash restarts %s: a crash restart, not the REBOOT' % usb['crash_restarts'])
        return time.monotonic() - start, decoded, usb

    def read(self, path, name, *extra):
        out = self.artifact(name)
        code, text, err = self.host(['read', path, '--out', out] + list(extra), T_ANSWER)
        if code == NOT_FOUND:
            return None
        if code:
            self.busy(code, err)
            if code == NO_ANSWER:
                self.no_answer = True
            raise StepFail('read %s: %s' % (path, why(code, text, err)))
        return out.read_bytes()

    def slots(self, tag):
        found = {path: self.read(path, '-'.join(filter(None, (tag, Path(path).name)))) for path in host.SLOTS}
        best = host.current_state(found)
        if not best or not best[2].get('default'):
            raise StepFail(
                'no valid state slot with a default (%s)'
                % ', '.join(
                    '%s %s' % (Path(p).name, 'missing' if d is None else '%d B' % len(d)) for p, d in found.items()
                )
            )
        return best

    def key(self, name):
        out = self.ok(['key', name])
        if 'menu closed' in out:
            raise StepFail('key %s closed the menu: the device left home' % name)

    def shot(self, name):
        path = self.artifact(name)
        self.ok(['screenshot', '--out', path])
        data = path.read_bytes()
        if not data.startswith(b'\x89PNG'):
            raise StepFail('screenshot %s is not a PNG' % path.name)
        return data

    # -- what the menu shows (device: screenshots; simulator: its log, --sim-log) ----------------------------
    def sim_lines(self, marker):
        log = Path(self.opts.sim_log).read_text(errors='replace') if Path(self.opts.sim_log).exists() else ''
        return [line[line.find(marker) + len(marker) :] for line in log.splitlines() if marker in line]

    def sim_page(self):
        """The simulator's last "mods page:" line as a dict (selected, on: list, view, shown), or {}."""
        lines = self.sim_lines('mods page: ')
        if not lines:
            return {}
        fields = dict(f.split('=', 1) for f in lines[-1].partition(' text=')[0].split(' ') if '=' in f)
        fields['on'] = [x for x in fields.get('on', '').split(',') if x]
        return fields

    def home_view(self, name):
        """STOP, TRACK1 (selects the first row; PLAY is never sent here): what the first row shows. Device: its
        pixels (band) and its text's right end (right), the bar checked; simulator: its log's last home row line
        (label, pending); None in the simulator without --sim-log."""
        if self.opts.sim and not self.opts.sim_log:
            return None
        self.key('stop')
        self.key('track1')
        self.sleep(0.3)
        if self.opts.sim:
            lines = self.sim_lines('menu: home row: ')
            if not lines:
                raise StepFail('simulator log %s: no "menu: home row:" line' % self.opts.sim_log)
            line = re.sub(r' memory=[^;]*; note=[^;]*;', '', lines[-1])  # the Settings row, the save note
            label, _, rest = line.rpartition(' pending=')
            return dict(band=line, label=label, pending=rest.startswith('1'), right=None, shot=None)
        shot = self.shot(name)
        rows = png_pixels(shot)
        if not row_selected(rows, 0) or row_selected(rows, 1):
            raise StepFail('%s: the first row is not the selected one after STOP, TRACK1: not the home menu' % name)
        right = max(
            (x for x in range(20, BAR_RIGHT) if any(not white(rows, x, y) for y in range(ROW_TOP + 2, ROW_TOP + 16))),
            default=0,
        )
        return dict(band=b''.join(rows[ROW0_BAND[0] : ROW0_BAND[1]]), right=right, pending=None, shot=shot)

    def saved_state(self, tag):
        """What the menu compares (the saved configuration, the default, the library) and the first home row."""
        records = {
            name: self.read(config_records.ROOT + '/' + name, tag + '-' + name, '--all')
            for name in config_records.NAMES
        }
        return dict(
            records,
            default=self.slots(tag)[2]['default'],
            library=sorted(self.names(LIBRARY)),
            home=self.home_view(tag + '-home.png'),
        )

    def same_state(self, before, tag):
        after = self.saved_state(tag)
        changed = [k for k in (*config_records.NAMES, 'default', 'library') if after[k] != before[k]]
        if before['home'] is not None and after['home']['band'] != before['home']['band']:
            changed.append('the first home row (a mark such as "(to apply)": see %s-home.png)' % tag)
        if changed:
            raise StepFail('not as found: %s' % ', '.join(changed))
        return 'saved configuration, the default, the library%s as found' % (
            '' if before['home'] is None else ' and the first home row'
        )

    # -- check and session -----------------------------------------------------------------------
    def step_check(self):
        if confirm_on(self.ok(['wait'], self.opts.wait)):  # HELLO only: nothing asks
            raise StepFail(CONFIRM_ON)
        decoded, usb = self.status(self.opts.wait)
        service_check(usb)
        self.state['check'] = usb
        return 'home answers; identity %s, crash restarts %s' % (usb.get('identity'), usb.get('crash_restarts'))

    def step_hold_on(self):
        self.ok(['hold', 'on'])
        return 'ok'

    # -- read ----------------------------------------------------------------------------------
    def step_status(self):
        decoded, usb = self.status()
        service_check(usb)
        return 'version %s, frames %s, reads %s, blocks %s' % (
            usb.get('version'),
            usb.get('frames'),
            usb.get('reads'),
            len(decoded.get('blocks', ())),
        )

    def step_read_state(self):
        path, seq, fields = self.slots(None)
        self.state['default'] = fields['default']
        return 'default %s (%s seq %d)' % (fields['default'], Path(path).name, seq)

    def read_metadata(self, folder, artifact, *extra, names=None):
        from scripts.device_pass_library import read_metadata

        return read_metadata(self, folder, artifact, extra, names, parse_info, StepFail)

    def step_read_info(self):
        folder = LIBRARY + '/' + self.state['default']
        info = self.read_metadata(folder, 'default')
        if not info.get('bytes', '').isdigit():
            raise StepFail('%s metadata has no image size' % folder)
        self.state['info'] = info
        return '%s: %s, %s B, crc32 %s' % (self.state['default'], info.get('name'), info['bytes'], info.get('crc32'))

    def step_ls_library(self):
        # folders known to be non-empty, so an empty listing (build 165) fails here, early.
        code, out, err = self.host(['ls', LIBRARY, '--json'], T_ANSWER)
        if code and 'does not answer' in out + err:
            raise StepSkip('the device has no file.list (before build 165)')
        names = self.names(LIBRARY)
        if self.state['default'] not in names:
            raise StepFail('ls %s: %d entries, no %s (the default)' % (LIBRARY, len(names), self.state['default']))
        top = self.names(LIBRARY.rsplit('/', 1)[0])
        if 'firmware' not in top:
            raise StepFail('ls /yaffs2/xy-forever: %d entries, no firmware' % len(top))
        return 'firmware/ lists %d entries with %s; /yaffs2/xy-forever lists %d with firmware' % (
            len(names),
            self.state['default'],
            len(top),
        )

    def step_read_ranges(self):
        path = '/yaffs2/xy-forever/firmware/%s/os.ldr' % self.state['default']
        size, crc = int(self.state['info']['bytes']), self.state['info'].get('crc32', '')
        local = STOCK_OS_LDR.read_bytes() if STOCK_OS_LDR.is_file() else b''
        same = len(local) == size and '%08x' % (zlib.crc32(local) & 0xFFFFFFFF) == crc
        got = b''
        for i in range(min(RANGES, -(-size // host.READ_RANGE))):
            offset = i * host.READ_RANGE
            data = self.read(path, 'range-%d.bin' % i, '--offset', offset)
            want = min(host.READ_RANGE, size - offset)
            if data is None or len(data) != want:
                raise StepFail('range at %d: %s B, expected %d' % (offset, 'no' if data is None else len(data), want))
            got += data
        if same and got != local[: len(got)]:
            raise StepFail('%d B differ from the local stock os.ldr' % len(got))
        return '%d B in %d ranges%s' % (
            len(got),
            -(-len(got) // host.READ_RANGE),
            ', identical to the local stock os.ldr' if same else ' (sizes only)',
        )

    def step_screenshot(self):
        self.shot('home.png')
        return 'saved %s' % self.artifact('home.png').name

    def step_peek(self):
        out = self.artifact('marker.bin')
        self.ok(['peek', '0x%08x' % MARKER, '16', '--out', out])
        magic, inverse, reason, crashes = struct.unpack('<4I', out.read_bytes())
        bits = [name for bit, name in MARKER_BITS if reason & bit]
        if magic != MARKER_MAGIC or inverse != ~MARKER_MAGIC & 0xFFFFFFFF:
            raise StepFail('no reboot marker (magic %08x)' % magic)
        if not reason & 0x100:
            raise StepFail('marker reason %#x has no held bit' % reason)
        return 'marker reason %#x (%s), crash restarts %d' % (reason, ', '.join(bits), crashes)

    # -- install -------------------------------------------------------------------------------
    def step_install(self):
        out = self.ok(['install', self.opts.install, '--yes-install'], install_timeout=T_INSTALL)
        m = re.search(r'install: (\w+), written (\d+), active partition (\d+), verified sectors (\d+)', out)
        if not m or m.group(1) != 'ok' or not int(m.group(4)):
            raise StepFail('install reply: %s' % (m.group(0) if m else 'none'))
        self.state['installed'] = self.opts.build_sha
        return 'written %s, partition %s, verified sectors %s, sha256 %s' % (
            m.group(2),
            m.group(3),
            m.group(4),
            self.opts.build_sha[:16],
        )

    def step_install_restart(self):
        back, _, usb = self.restart()
        if usb.get('put_size'):
            raise StepFail('the service still holds the staged image (put_size %s): no new boot' % usb['put_size'])
        return 'fresh boot home in %.1f s (runs the verified partition; no command reads the image)' % back

    # -- protocol ------------------------------------------------------------------------------
    @contextlib.contextmanager
    def wire(self):
        try:
            link, text = self.dev.open(T_ANSWER)
        except NoAnswer as error:
            self.no_answer = True
            raise StepFail('no answer (%s)' % str(error)[:120])
        self.note('connected: %s' % text)
        try:
            yield Wire(link, self.log), text
        finally:
            link.close()

    def v1(self):
        if self.hello is None or self.hello.pre_v1:
            raise StepSkip(PRE_V1)
        return self.hello

    def absent(self, wire, when):
        code, body = wire.call(P.READ_FILE, P.pack(READ_REQ, dict(path=REFUSED_PATH, offset=0, length=16)))
        if code == 0:
            raise StepFail('%s exists %s (%d B read)' % (REFUSED_PATH, when, len(body)))
        if code != NOT_FOUND:
            raise StepFail('read %s %s: %s, expected not_found' % (REFUSED_PATH, when, P.status_name(code)))

    def discard(self, wire):
        """stage.end on this port's incomplete staging: the device answers state and forgets it."""
        try:
            code, _ = wire.call(P.PUT_END)
        except StepFail as error:
            return str(error)
        return None if code == P.STATE else 'stage.end: %s, expected state (staging discarded)' % P.status_name(code)

    def step_hello(self):
        with self.wire() as (wire, text):
            self.hello = hello = P.Hello(text)
            proto = self.state['protocol'] = dict(hello=text, pre_v1=hello.pre_v1)
            installed = self.opts.build_image if self.state.get('installed') else None
            if hello.pre_v1:
                if installed:
                    raise StepFail('installed build %s speaks v1, but the HELLO is pre-v1: %s' % (installed, text))
                raise StepSkip('%s; HELLO %s' % (PRE_V1, text))
            if hello.major != '1':
                raise StepFail('protocol %s; this pass speaks 1.x (HELLO %s)' % (hello.proto, text))
            times = []
            for i in range(HELLO_ROUNDS):
                start = time.monotonic()
                code, body = wire.call(P.HELLO)
                times.append((time.monotonic() - start) * 1000)
                if code or body.decode('ascii', 'replace') != text:
                    raise StepFail('HELLO %d: %s, text %r differs' % (i + 1, P.status_name(code), body[:60]))
        times.sort()
        proto.update(
            proto=hello.proto,
            version=hello.keys.get('version'),
            image=hello.keys.get('image'),
            build=hello.keys.get('build'),
            mode=hello.keys.get('mode'),
            cmds=hello.keys.get('cmds'),
            read=hello.keys.get('read'),
            hello_ms=dict(median=round(times[len(times) // 2], 2), max=round(times[-1], 2), n=len(times)),
        )
        return '%s; HELLO round trip median %.1f ms, max %.1f ms; HELLO %s' % (
            hello.describe(),
            proto['hello_ms']['median'],
            proto['hello_ms']['max'],
            text,
        )

    def step_build(self):
        hello = self.v1()
        image, version, mode, cmds = (
            hello.keys.get('image'),
            hello.keys.get('version'),
            hello.keys.get('mode'),
            hello.cmds,
        )
        build = 'image %s, version %s' % (image, version)
        if not self.state.get('installed'):
            return '%s (%s), cmds %032x: recorded (no --install to compare)' % (build, mode, cmds)
        want_mode = self.opts.build_mode.split('-', 1)[-1]
        wrong = []
        if image != self.opts.build_image:
            wrong.append(
                'image=%s, expected %s: the installed build is not the one running' % (image, self.opts.build_image)
            )
        if version != self.opts.build_version:
            wrong.append('version=%s, expected %s' % (version, self.opts.build_version))
        if mode != want_mode:
            wrong.append('mode=%s, expected %s' % (mode, want_mode))
        if cmds != self.opts.build_cmds:
            wrong.append(
                'cmds %032x, expected %032x (spec bitmap for %s)' % (cmds, self.opts.build_cmds, self.opts.build_mode)
            )
        if wrong:
            raise StepFail('; '.join(wrong))
        return '%s (%s) is the installed build; cmds %032x = the spec bitmap for %s' % (
            build,
            mode,
            cmds,
            self.opts.build_mode,
        )

    def step_unsupported(self):
        hello = self.v1()
        kind = max(t for t in range(0x40, 0x60) if t not in ids.COMMANDS and not hello.cmds >> t & 1)
        with self.wire() as (wire, _):
            # raw: xyf_host refuses to send a type missing from cmds; this check must send one.
            code, _ = wire.call(kind, b'', T_ANSWER, raw=True)
        if code != ids.S_UNSUPPORTED:
            raise StepFail('unknown type 0x%02x: %s, expected unsupported' % (kind, P.status_name(code)))
        return 'type 0x%02x (no command, not in cmds): unsupported' % kind

    def step_refused(self):
        self.v1()
        with self.wire() as (wire, _):
            self.absent(wire, 'before the test')
            code, _ = wire.call(P.PUT_BEGIN, P.pack(BEGIN_REQ, dict(size=len(TINY) + 1, crc32=0, sha256=bytes(32))))
            if code:
                raise StepFail('stage.begin (%d B): %s' % (len(TINY) + 1, P.status_name(code)))
            failure = None
            try:
                code, body = wire.call(P.PUT_DATA, struct.pack('<I', 0) + TINY)
                if code:
                    raise StepFail('stage.data (%d B): %s' % (len(TINY), P.status_name(code)))
                # Incomplete staging and a token that matches nothing: even without the path
                # check the device would answer state and write nothing.
                code, body = wire.call(P.FILE_WRITE, P.pack(WRITE_REQ, dict(path=REFUSED_PATH, sha256=TOKEN)), T_ANSWER)
                written = struct.unpack_from('<I', body)[0] if len(body) >= 4 else 0
                if code != ids.S_REFUSED or written:
                    raise StepFail(
                        'file.write_staged %s: %s, written %d; expected refused, 0'
                        % (REFUSED_PATH, P.status_name(code), written)
                    )
            except StepFail as error:
                failure = error
            left = self.discard(wire)
            if failure or left:
                raise StepFail('; '.join(str(e) for e in (failure, left) if e))
            self.absent(wire, 'after the refused write')
        return 'file.write_staged %s: refused; absent before and after; %d of %d B staged, discarded (state)' % (
            REFUSED_PATH,
            len(TINY),
            len(TINY) + 1,
        )

    def step_bad_request(self):
        self.v1()
        # xyf_host has no function that sends a malformed request; Device.call sends this payload as is.
        payload = P.pack(WRITE_REQ, dict(path=REFUSED_PATH, sha256=TOKEN))[:-1]
        with self.wire() as (wire, _):
            code, _ = wire.call(P.FILE_WRITE, payload, T_ANSWER)
            if code != ids.S_BAD_REQUEST:
                raise StepFail('file.write_staged one byte short: %s, expected bad_request' % P.status_name(code))
            self.absent(wire, 'after the short request')
        return 'file.write_staged %d B (one short): bad_request; %s absent' % (len(payload), REFUSED_PATH)

    def step_continue_link(self):
        self.v1()
        with self.wire() as (wire, _):
            code, _ = wire.call(P.CONTINUE, CONTINUE_LINK)
            if code != ids.S_LINK:
                raise StepFail(
                    'CONTINUE mode 2: %s, expected link%s'
                    % (P.status_name(code), '; the menu may have closed' if code == 0 else '')
                )
            code, _ = wire.call(P.STATUS)
            if code:
                raise StepFail('STATUS after CONTINUE mode 2: %s' % P.status_name(code))
        return 'CONTINUE mode 2: link; STATUS still answers (menu kept)'

    def timed(self, send, total):
        """Calls send(done) -> bytes until total bytes or SPEED_BUDGET s: (bytes, seconds, KiB/s)."""
        done, start = 0, time.monotonic()
        while done < total and time.monotonic() - start < SPEED_BUDGET:
            done += send(done)
        seconds = time.monotonic() - start
        return done, seconds, round(done / 1024.0 / max(seconds, 1e-6), 1)

    def step_read_speed(self):
        hello = self.v1()
        if 'info' not in self.state:  # --stages without read
            self.step_read_state()
            self.step_read_info()
        path = '/yaffs2/xy-forever/firmware/%s/os.ldr' % self.state['default']
        size = int(self.state['info']['bytes'])
        limit = int(hello.keys['read']) if hello.keys.get('read', '').isdigit() else ids.MAX_READ
        if limit < 1:
            raise StepFail('HELLO read=%s: no read length allowed' % hello.keys.get('read'))
        speeds, parts = {}, []
        with self.wire() as (wire, _):
            for length in sorted({min(1024, limit), limit}):

                def read(at, length=length):
                    want = min(length, size - at)
                    code, body = wire.call(P.READ_FILE, P.pack(READ_REQ, dict(path=path, offset=at, length=length)))
                    if code or len(body) != want:
                        raise StepFail(
                            'file.read %d B at %d: %s, %d B (expected %d)'
                            % (length, at, P.status_name(code), len(body), want)
                        )
                    return want

                done, seconds, speeds[str(length)] = self.timed(read, min(SPEED_BYTES, size))
                parts.append(
                    '%d B ranges: %d KiB in %.2f s = %.1f KiB/s' % (length, done >> 10, seconds, speeds[str(length)])
                )
            if limit <= 1024:
                parts.append('read=%d: no larger range' % limit)
            parts.append(
                self.whole_read(wire, limit)
                if self.opts.big_replies
                else 'no reply above read=%d (--big-replies)' % limit
            )
        self.state['protocol']['read_kib_s'] = speeds
        return '; '.join(parts)

    def whole_read(self, wire, limit):
        """--big-replies: whole-file reads of PASS_FILE when it is above read= and at most max_data."""
        data, step = b'', min(1024, limit)
        while len(data) <= P.MAX_DATA:
            code, body = wire.call(P.READ_FILE, P.pack(READ_REQ, dict(path=PASS_FILE, offset=len(data), length=step)))
            if code == NOT_FOUND and not data:
                return 'whole-file read: not measured (%s not found)' % PASS_FILE
            if code:
                raise StepFail('read %s at %d: %s' % (PASS_FILE, len(data), P.status_name(code)))
            data += body
            if len(body) < step:
                break
        if not limit < len(data) <= P.MAX_DATA:
            return 'whole-file read: not measured (%s is %d B, not %d..%d)' % (
                PASS_FILE,
                len(data),
                limit + 1,
                P.MAX_DATA,
            )

        def whole(_):
            code, body = wire.call(P.READ_FILE, P.pack(READ_REQ, dict(path=PASS_FILE)))
            if code or body != data:
                raise StepFail(
                    'whole-file read of %d B: %s, %d B%s'
                    % (len(data), P.status_name(code), len(body), '' if code else ', differs')
                )
            return len(body)

        done, seconds, speed = self.timed(whole, 3 * len(data))
        self.state['protocol']['whole_read'] = dict(bytes=len(data), kib_s=speed)
        return 'whole-file reads of %d B (reply > read=): %d ok, %.1f KiB/s' % (len(data), done // len(data), speed)

    def step_stage_speed(self):
        self.v1()
        chunks = (host.PUT_CHUNK, BIG_CHUNK) if self.opts.big_replies else (host.PUT_CHUNK,)
        announced = SPEED_BYTES * len(chunks) + 1  # one byte more than is ever sent
        speeds, parts = {}, []
        with self.wire() as (wire, _):
            code, _ = wire.call(P.PUT_BEGIN, P.pack(BEGIN_REQ, dict(size=announced, crc32=0, sha256=bytes(32))))
            if code:
                raise StepFail('stage.begin (%d B): %s' % (announced, P.status_name(code)))
            failure, base = None, 0
            try:
                for chunk in chunks:

                    def put(done, chunk=chunk):
                        at = base + done
                        code, body = wire.call(P.PUT_DATA, struct.pack('<I', at) + filler(chunk))
                        got = struct.unpack_from('<I', body)[0] if code == 0 and len(body) >= 4 else None
                        if got != at + chunk:
                            raise StepFail(
                                'stage.data %d B at %d: %s, received %s' % (chunk, at, P.status_name(code), got)
                            )
                        return chunk

                    done, seconds, speeds[str(chunk)] = self.timed(put, SPEED_BYTES)
                    base += done
                    parts.append(
                        '%d B chunks: %d KiB in %.2f s = %.1f KiB/s' % (chunk, done >> 10, seconds, speeds[str(chunk)])
                    )
            except StepFail as error:
                failure = error
            left = self.discard(wire)
            if failure or left:
                raise StepFail('; '.join(str(e) for e in (failure, left) if e))
        if not self.opts.big_replies:
            parts.append('no %d B chunks (--big-replies)' % BIG_CHUNK)
        self.state['protocol']['stage_kib_s'] = speeds
        return '; '.join(parts + ['%d of %d B staged, discarded (state)' % (base, announced)])

    # -- boot ----------------------------------------------------------------------------------
    def step_dry_run(self):
        before, _ = self.status()
        self.ok(['continue', '--dry-run'])
        start = time.monotonic()
        self.gone(T_DRY, 'continue --dry-run')
        decoded, usb = self.status(T_BACK)
        back = time.monotonic() - start
        result = decoded.get('dry_run_file_boot')
        if not result:
            raise StepFail('no dry-run result after the restart')
        if result == before.get('dry_run_file_boot'):
            raise StepFail('dry-run result unchanged: stale block from an earlier dry run')
        if result.get('outcome') != 'file' or not result.get('committed'):
            raise StepFail(
                'dry run: outcome %s, load %s, admit %s, committed %s'
                % tuple(result.get(k) for k in ('outcome', 'load_reason', 'admit_reason', 'committed'))
            )
        if usb.get('crash_restarts'):
            raise StepFail('came back with crash restarts %s' % usb['crash_restarts'])
        return 'committed %s B, first answer %s ms, longest %s ms, home in %.1f s' % (
            result['committed'],
            result.get('first_answer_ms'),
            result.get('longest_answer_ms'),
            back,
        )

    def step_restart(self):
        return 'home in %.1f s' % self.restart()[0]

    def step_keys(self):
        # TRACK1..4 select the four rows (#197: Continue, the safe-mode row, Mods, Settings); TRACK3 is the Mods row.
        self.key('stop')
        first, second = self.shot('menu-a.png'), self.shot('menu-b.png')
        if first != second:
            raise StepFail('the screen changes by itself: key effects cannot be checked, PLAY not sent')
        self.key('track3')
        self.sleep(0.3)
        selected = self.shot('selected.png')
        if selected == second:
            raise StepFail('TRACK3 did not change the screen')
        rows = png_pixels(selected)
        if not row_selected(rows, 2) or row_selected(rows, 0) or row_selected(rows, 3):
            raise StepFail('after TRACK3 the Mods row is not the selected one (or another row is)')
        return 'STOP, TRACK3 changed the screen: the Mods row selected'

    # -- writes --------------------------------------------------------------------------------
    def step_put_file(self):
        _, usb, server = self.window_status()
        self.state['window0'] = server
        data = pass_file(self.folder.name if self.folder else 'plan')
        second = data[:999] + b'\n'
        for name, remote, body in (('device-pass.txt', PASS_FILE, data), ('device-pass-2.txt', PASS_FILE2, second)):
            local = self.artifact(name)
            local.write_bytes(body)
            self.ok(['put-file', local, remote, '--yes-write'])
        self.state['pass_file'], self.state['pass_file2'] = data, second
        return '%d B to %s, %d B to %s' % (len(data), PASS_FILE, len(second), PASS_FILE2)

    def check_pass_file(self, name):
        n = 0
        for remote, key, local in ((PASS_FILE, 'pass_file', name), (PASS_FILE2, 'pass_file2', '2-' + name)):
            data = self.read(remote, local, '--all')
            if data != self.state[key]:
                raise StepFail(
                    'read-back of %s differs: %s B, expected %d'
                    % (remote, 'no' if data is None else len(data), len(self.state[key]))
                )
            n += len(data)
        return n

    def step_read_back(self):
        n = self.check_pass_file('read-back.txt')
        return '%d B in %d ranges, identical' % (n, -(-n // host.READ_RANGE))

    # B's own record of the write window (storage_server Result on B, STATUS "remote_read"):
    # b_window = op 9's answer when B closed it (1 none was open, 3 closed one, 0 failed), b_windows
    # = windows closed, b_catalogs = catalogs fetched after writes.
    def window_status(self):
        decoded, usb = self.status()
        server = (decoded or {}).get('remote_read') or {}
        if 'b_windows' not in server:
            raise StepFail('status: no file server block with the write-window fields (a build before 163?)')
        return decoded, usb, server

    def settled(self, before, what):
        """Waits SETTLE s without a write, then until B fetched the catalog; the STATUS fields."""
        self.sleep(SETTLE)
        for _ in range(int(T_WINDOW - SETTLE)):  # one STATUS a second
            _, usb, server = self.window_status()
            if server['b_catalogs'] > before['b_catalogs']:
                return usb, server
            self.sleep(1.0)
        raise StepFail(
            '%s: no catalog fetched %g s after the last write (b_window %s, b_windows %s): the write '
            'window may still be open' % (what, T_WINDOW, server.get('b_window'), server.get('b_windows'))
        )

    def step_window_closed(self):
        before = self.state['window0']
        usb, server = self.settled(before, 'window-closed')
        closed = server['b_windows'] - before['b_windows']
        if server['b_window'] != 3 or closed != 1:
            raise StepFail(
                'op 9 answered %s (3 = it closed the open window), windows closed %d (expected 1 for '
                'the two writes)' % (server['b_window'], closed)
            )
        self.state['window1'], self.state['crashes1'] = server, usb.get('crash_restarts')
        return 'one window for both writes, closed by B (op 9 = 3); catalog fetched (%d)' % server['b_catalogs']

    def menu_catalog(self, name):
        out = self.artifact(name)
        self.ok(['peek', '0x%08x' % census.CATALOG_ADDRESS, '2048', '--out', out])
        found = census.catalog(out.read_bytes())
        if not found:
            raise StepFail("peek: B's catalog buffer holds no valid catalog")
        return found

    def step_library_change(self):
        from scripts.device_pass_library import change

        return change(self, SCRATCH_INFO, SCRATCH_ID, StepFail)

    def step_set_default_alt(self):
        default = self.state['default0'] = self.slots('before')[2]['default']  # after-restart checks it
        # A listed entry with its content installed: without, set-default starts a content switch; unlisted: fails.
        items = self.menu_catalog('alt-catalog.bin')['items']
        ready = [i['id'] for i in items if i['usable'] and not i['content_missing'] and i['id'] != default]
        alt = sorted(
            (i for i in ready if i == self.opts.alt_default or not self.opts.alt_default),
            key=lambda i: i not in ALT_DEFAULTS,
        )
        if not alt:
            raise StepSkip(
                'no entry but the default %s usable with its content installed%s; the menu lists: %s'
                % (
                    default,
                    ' (--alt-default %s)' % self.opts.alt_default if self.opts.alt_default else '',
                    ', '.join(
                        i['id']
                        + (' (content not installed)' if i['content_missing'] else '' if i['usable'] else ' (unusable)')
                        for i in items
                    ),
                )
            )
        self.state['saved0'] = self.saved_state('alt-before')  # as found again after set-default-back
        self.ok(['set-default', alt[0], '--yes-write'])
        self.state['alt'] = alt[0]
        return '%s -> %s (slots read back)' % (default, alt[0])

    def step_set_default_back(self):
        if 'alt' not in self.state:
            raise StepSkip('set-default-alt changed nothing')
        default, alt = self.state['default0'], self.state['alt']
        try:
            self.ok(['set-default', default, '--yes-write'])
        except StepFail as error:
            raise StepFail(
                '%s; default may still be %s: uv run xyf-host set-default %s --yes-write' % (error, alt, default)
            )
        return '%s -> %s (slots read back); %s' % (alt, default, self.same_state(self.state['saved0'], 'alt-after'))

    # The file commands (xyf_host ls / get / put / rm / mkdir).
    def ls(self, path):
        out = self.ok(['ls', path, '--json'])
        try:
            return json.loads(out[out.index('[') :])
        except ValueError:
            raise StepFail('ls %s: no JSON listing' % path)

    def names(self, path):
        """The names ls lists in path; a folder known to hold entries must not list empty."""
        return [e['name'] for e in self.ls(path)]

    def remove(self, *argv):
        """xyf_host rm ARGV: 'removed', or 'absent' (not_found)."""
        code, out, err = self.host(['rm'] + list(argv), T_ANSWER)
        if code not in (0, NOT_FOUND):
            raise StepFail('rm %s: %s' % (' '.join(argv), why(code, out, err)))
        return 'absent' if code else 'removed'

    def step_scratch_cleanup(self):
        folder = SCRATCH_INFO.rsplit('/', 1)[0]
        done = ['info.txt %s' % self.remove(SCRATCH_INFO), 'folder %s' % self.remove('--dir', folder)]
        names = self.names(LIBRARY)
        if SCRATCH_ID in names:
            raise StepFail('firmware/ still lists %s' % SCRATCH_ID)
        default = self.state.get('default0') or self.state.get('default')
        if default and default not in names:  # no vacuous pass
            raise StepFail(
                'ls firmware/: %d entries, no %s (the default): the listing is wrong' % (len(names), default)
            )
        return '%s: %s; firmware/ lists %d entries, none %s' % (SCRATCH_ID, ', '.join(done), len(names), SCRATCH_ID)

    def step_file_commands(self):
        base, name = FILES_DIR.rsplit('/', 1)
        # Left by an interrupted pass: its two known names, then the folder (no listing needed:
        # build 165's listing was empty). not_found is fine for each.
        left = [self.remove('%s/%s' % (FILES_DIR, leaf)) for leaf in FILES_LEAVES] + [self.remove('--dir', FILES_DIR)]
        if 'firmware' not in self.names(base):  # no vacuous pass
            raise StepFail('ls %s lists no firmware: the listing is wrong' % base)
        self.ok(['mkdir', FILES_DIR])
        files = tuple(zip(FILES_LEAVES, (filler(FILES_BIG), pass_file('files'))))
        seconds = {}
        for leaf, data in files:
            local = self.artifact(leaf)
            local.write_bytes(data)
            start = time.monotonic()
            self.ok(['put', local, '%s/%s' % (FILES_DIR, leaf), '--no-verify'])
            seconds['put ' + leaf] = time.monotonic() - start
        listing = {e['name']: (e['type'], e['size']) for e in self.ls(FILES_DIR)}
        want = {leaf: ('file', len(data)) for leaf, data in files}
        if listing != want:
            raise StepFail('ls %s: %s, expected %s' % (FILES_DIR, listing, want))
        for leaf, data in files:
            local = self.artifact('got-' + leaf)
            start = time.monotonic()
            self.ok(['get', '%s/%s' % (FILES_DIR, leaf), local])
            seconds['get ' + leaf] = time.monotonic() - start
            if local.read_bytes() != data:
                raise StepFail('get %s/%s: differs from what was put' % (FILES_DIR, leaf))
        for leaf, _ in files:
            self.ok(['rm', '%s/%s' % (FILES_DIR, leaf)])
        self.ok(['rm', '--dir', FILES_DIR])
        after = self.names(base)
        if name in after or 'firmware' not in after:
            raise StepFail('ls %s after rmdir: %s' % (base, sorted(after)))
        speed = {k: round(FILES_BIG / 1024.0 / max(seconds[k + ' big.bin'], 1e-6), 1) for k in ('put', 'get')}
        self.state['file_kib_s'] = speed
        return ('leftover removed; ' if 'removed' in left else '') + (
            'mkdir; put %d B in %.1f s (%.1f KiB/s, stage + write + device CRC) and 2,500 B; ls sizes match; '
            'get both identical (%d B in %.1f s, %.1f KiB/s, checks included); rm both; rmdir; gone'
            % (FILES_BIG, seconds['put big.bin'], speed['put'], FILES_BIG, seconds['get big.bin'], speed['get'])
        )

    def step_rename(self):
        left = [
            self.remove(
                *(
                    ['--dir', '%s/%s' % (FILES_DIR, leaf[6:])]
                    if leaf.startswith('--dir ')
                    else ['%s/%s' % (FILES_DIR, leaf)]
                )
            )
            for leaf in MOVE_LEFT
        ]
        left.append(self.remove('--dir', FILES_DIR))
        data = pass_file('rename')
        local, got = self.artifact('rename.txt'), self.artifact('got-rename.txt')
        local.write_bytes(data)
        self.ok(['mkdir', FILES_DIR])
        self.ok(['mkdir', FILES_DIR + '/sub'])
        self.ok(['put', local, FILES_DIR + '/a.txt', '--no-verify'])
        seconds = []
        for source, target in (('a.txt', 'sub/b.txt'), ('sub', 'sub2')):
            start = time.monotonic()
            self.ok(['mv', '%s/%s' % (FILES_DIR, source), '%s/%s' % (FILES_DIR, target)])
            seconds.append(time.monotonic() - start)
        top = {e['name']: e['type'] for e in self.ls(FILES_DIR)}
        inner = {e['name']: (e['type'], e['size']) for e in self.ls(FILES_DIR + '/sub2')}
        if top != {'sub2': 'dir'} or inner != {'b.txt': ('file', len(data))}:
            raise StepFail(
                'after mv: ls %s %s, ls sub2 %s; expected sub2 holding b.txt (%d B)'
                % (FILES_DIR, top, inner, len(data))
            )
        self.ok(['get', FILES_DIR + '/sub2/b.txt', got])
        if got.read_bytes() != data:
            raise StepFail('get %s/sub2/b.txt: differs from what was put' % FILES_DIR)
        code, out, err = self.host(['mv', FILES_DIR + '/sub2/b.txt', FILES_DIR + '/sub2'], T_ANSWER)
        if code != STATE:
            raise StepFail('mv onto an existing name: %s, expected state' % why(code, out, err))
        self.ok(['rm', FILES_DIR + '/sub2/b.txt'])
        self.ok(['rm', '--dir', FILES_DIR + '/sub2'])
        self.ok(['rm', '--dir', FILES_DIR])
        if FILES_DIR.rsplit('/', 1)[1] in self.names(FILES_DIR.rsplit('/', 1)[0]):
            raise StepFail('ls after rm: %s still listed' % FILES_DIR)
        return ('leftover removed; ' if 'removed' in left else '') + (
            'mv a.txt sub/b.txt in %.2f s; mv sub sub2 (a folder with a file) in %.2f s; ls and get identical; '
            'mv onto an existing name: state; removed' % tuple(seconds)
        )

    def step_copy(self):
        left = [self.remove('%s/%s' % (FILES_DIR, leaf)) for leaf in COPY_LEFT]
        left.append(self.remove('--dir', FILES_DIR))
        data = (pass_file('copy') * (COPY_BYTES // 2500 + 1))[:COPY_BYTES]
        local = self.artifact('copy.bin')
        local.write_bytes(data)
        a, b = ['%s/%s' % (FILES_DIR, leaf) for leaf in COPY_LEFT]
        self.ok(['mkdir', FILES_DIR])
        self.ok(['put', local, a, '--no-verify'])
        start = time.monotonic()
        self.ok(['cp', a, b])
        seconds = time.monotonic() - start
        for path in (a, b):
            got = self.artifact('got-' + path.rsplit('/', 1)[1])
            self.ok(['get', path, got])
            if got.read_bytes() != data:
                raise StepFail('get %s: differs from what was put' % path)
        code, out, err = self.host(['cp', a, b], T_ANSWER)
        if code != STATE:
            raise StepFail('cp onto an existing name: %s, expected state' % why(code, out, err))
        self.ok(['rm', a])
        self.ok(['rm', b])
        self.ok(['rm', '--dir', FILES_DIR])
        if FILES_DIR.rsplit('/', 1)[1] in self.names(FILES_DIR.rsplit('/', 1)[0]):
            raise StepFail('ls after rm: %s still listed' % FILES_DIR)
        return ('leftover removed; ' if 'removed' in left else '') + (
            'cp %d B on the device in %.2f s (%.1f KiB/s); get both identical; cp onto an existing name: state; '
            'removed' % (COPY_BYTES, seconds, COPY_BYTES / 1024.0 / max(seconds, 1e-6))
        )

    def remove_tree(self, folder):
        for entry in self.ls(folder):
            path = '%s/%s' % (folder, entry['name'])
            if entry['type'] == 'dir':
                self.remove_tree(path)
            else:
                self.ok(['rm', path])
        self.ok(['rm', '--dir', folder])

    def step_apply(self):
        """fm2op assembled by the device, its os.ldr compared with the host's reference."""
        packages = MODS_PACKAGES
        if not package_records.local_name(packages / 'base') or not MODS_CATALOG.is_file():
            raise StepSkip('no local package set or stock catalog (%s): the reference needs them' % packages)
        root = '/yaffs2/xy-forever/mods'  # mods/<generation>/ (#124)
        code, out, err = self.host(['ls', '%s/%s' % (root, packages.name), '--json'], T_ANSWER)
        try:
            held = [e['name'] for e in json.loads(out[out.index('[') :])] if code == 0 else []
        except ValueError:
            held = []
        if 'base' not in held or MODS_MACHINE not in held:
            raise StepSkip(
                'the device holds no package set %s/%s with %s (uv run xyf-host mods push %s)'
                % (root, packages.name, MODS_MACHINE, packages)
            )
        library = '/yaffs2/xy-forever/firmware'
        entries = self.names(library)
        base = None
        for entry in sorted(entries, key=lambda e: (e != 'opxy-121', e)):
            names = self.names('%s/%s' % (library, entry))
            if 'os.ldr' in names and 'content.sha256' in names:
                base = entry
                break
        if base is None:
            raise StepSkip(
                'no library entry holds os.ldr and content.sha256 (put %s to firmware/opxy-121/)'
                % (paths.XYF / 'content-manifests/1.2.1/content.sha256')
            )
        # The build becomes library entry mods-a or mods-b (the absent one); the pass removes it again
        # and never replaces an entry the device already holds.
        if 'mods-a' in entries and 'mods-b' in entries:
            raise StepSkip(
                'the library holds mods-a and mods-b: the build would replace one (the pass leaves '
                'the library as it was)'
            )
        expect = 'mods-b' if 'mods-a' in entries else 'mods-a'
        try:
            want = mods_reference(packages, [MODS_MACHINE], MODS_NAME)
        except Exception as error:
            raise StepFail('host reference: %s' % error)
        as_found = self.saved_state('apply-before')  # the menu compares these with saved configuration
        start = time.monotonic()
        out = self.ok(
            [
                'mods',
                'apply',
                '--base',
                base,
                '--generation',
                '%s/%s' % (MODS_PROFILE, packages.name),
                MODS_MACHINE,
                '--name',
                MODS_NAME,
                '--wait',
            ],
            T_MODS,
        )
        seconds = time.monotonic() - start
        found = re.search(r'job (\d+) started', out)
        if not found:
            raise StepFail('mods apply: no job id in %r' % out[-200:])
        # A new session's file command right after the job (build 172 froze here once): answered.
        listed = time.monotonic()
        self.ok(['ls', '/yaffs2/xy-forever/tmp'], T_ANSWER)
        listed = time.monotonic() - listed
        closed = re.search(r'storage window closed ([\d.]+) s', out)
        entry = re.search(r'library entry (mods-[ab])', out)
        if not entry or entry.group(1) != expect:
            raise StepFail('mods apply: expected library entry %s, got %r' % (expect, out[-200:]))
        folder = '%s/%s' % (library, expect)
        last = [line for line in out.splitlines() if line.startswith('job %s: done' % found.group(1))]
        local = self.artifact('mods-os.ldr')
        self.ok(['get', folder + '/os.ldr', local], T_FILES)
        data = local.read_bytes()
        if hashlib.sha256(data).hexdigest() != want:
            raise StepFail(
                '%s/os.ldr (%d B) differs from the host reference (left for inspection)' % (folder, len(data))
            )
        # The menu lists it, content not installed; only then library.boot, which must be refused (no_content):
        # a mod firmware without its content halts at boot.
        item = [i for i in self.menu_catalog('mods-catalog.bin')['items'] if i['id'] == expect]
        if not item or item[0]['usable'] or not item[0]['content_missing']:
            raise StepFail(
                'menu catalog: %s missing or not "content not installed": %s (library.boot not sent; '
                'entry left for inspection)' % (expect, item)
            )
        allow, self.allow_boot = self.allow_boot, True
        try:
            code, bout, berr = self.host(['boot', expect, '--yes-boot'], T_ANSWER)
        finally:
            self.allow_boot = allow
        if code != ids.S_NO_CONTENT:
            raise StepFail(
                'boot %s: exit %d, expected no_content (%d): %s'
                % (expect, code, ids.S_NO_CONTENT, why(code, bout, berr))
            )
        self.remove_tree(folder)
        if expect in self.names(library):
            raise StepFail('ls %s still lists %s' % (library, expect))
        leftovers = [n for n in self.names('/yaffs2/xy-forever/tmp') if n.startswith('job-')]
        return (
            '%s on the device in %.1f s (base %s; %s); window closed %s; ls tmp/ right after: %.1f s; library entry '
            '%s, os.ldr %d B identical to the host reference; listed "content not installed", boot refused '
            '(no_content); %s removed; tmp/job-* left: %d; %s'
            % (
                MODS_MACHINE,
                seconds,
                base,
                last[-1].split(': ', 1)[1] if last else 'done',
                'after %s s' % closed.group(1) if closed else 'within 1 s',
                listed,
                expect,
                len(data),
                folder,
                len(leftovers),
                self.same_state(as_found, 'apply-after'),
            )
        )

    # The content switch, both ways, on the device.
    def tree(self, folder, rel=''):
        """Every file under folder ({relative path: size}) and every folder (set of relative paths)."""
        files, dirs = {}, set()
        for e in self.ls(folder + ('/' + rel if rel else '')):
            path = rel + '/' + e['name'] if rel else e['name']
            if e['type'] == 'dir':
                dirs.add(path)
                sub = self.tree(folder, path)
                files.update(sub[0])
                dirs |= sub[1]
            else:
                files[path] = e['size']
        return files, dirs

    def fetch(self, path, name, meter=None):
        """get path; its bytes (meter: [bytes, seconds] the gets took, for the speed estimate)."""
        local = self.artifact(name)
        start = time.monotonic()
        self.ok(['get', path, local], T_FILES)
        data = local.read_bytes()
        if meter is not None:
            meter[0] += len(data)
            meter[1] += time.monotonic() - start
        return data

    def record_text(self, name):
        code, out, err = self.host(['get', CONTENT_RECORD, self.artifact(name)], T_ANSWER)
        if code == NOT_FOUND:
            return None
        if code:
            raise StepFail('get %s: %s' % (CONTENT_RECORD, why(code, out, err)))
        return self.artifact(name).read_text(errors='replace')

    @staticmethod
    def sha_lines(text, prefix=''):
        """{path: sha256} of a files.sha256 / content.sha256 text (only paths under prefix, without it)."""
        out = {}
        for line in text.splitlines():
            m = re.fullmatch(r'([0-9a-f]{64})  (\S.*)', line.strip())
            if m and m.group(2).startswith(prefix):
                out[m.group(2)[len(prefix) :]] = m.group(1)
        return out

    def step_switch(self):
        """fm2op built, made the default (content switch), the previous default set back (switch back)."""
        packages = MODS_PACKAGES
        root = '/yaffs2/xy-forever/mods'  # mods/<generation>/ (#124)
        code, out, err = self.host(['ls', '%s/%s' % (root, packages.name), '--json'], T_ANSWER)
        try:
            held = [e['name'] for e in json.loads(out[out.index('[') :])] if code == 0 else []
        except ValueError:
            held = []
        if 'base' not in held or MODS_MACHINE not in held:
            raise StepSkip('the device holds no package set %s/%s with %s' % (root, packages.name, MODS_MACHINE))
        entries = self.names(LIBRARY)
        if 'mods-a' in entries and 'mods-b' in entries:
            raise StepSkip('the library holds mods-a and mods-b: the build would replace one')
        previous = self.slots('switch-before')[2]['default']
        item = [i for i in self.menu_catalog('switch-catalog0.bin')['items'] if i['id'] == previous]
        if not item or not item[0]['usable'] or item[0]['content_missing']:  # official record: a stock entry
            raise StepSkip('the default %s is not usable with its content installed (%s)' % (previous, item))
        if 'content.sha256' not in self.names('%s/%s' % (LIBRARY, previous)):
            raise StepSkip('the default %s has no content.sha256' % previous)
        record0 = self.record_text('record0.txt')
        if record0 is not None and not record0.startswith('state=official\n'):
            raise StepSkip('content/ is not official: the record says %r' % record0.split('\n', 2)[:2])
        meter = [0, 0.0]
        official = self.sha_lines(
            self.fetch('%s/%s/content.sha256' % (LIBRARY, previous), 'official.sha256').decode(errors='replace')
        )
        catalog0 = self.fetch(CONTENT + '/op1_factory.json', 'catalog0.json', meter)
        if official.get('op1_factory.json') != hashlib.sha256(catalog0).hexdigest():
            raise StepSkip('content/op1_factory.json is not the official catalog of %s' % previous)
        before = self.tree(CONTENT)
        missing = sorted(p for p in official if p not in before[0])
        if missing:
            raise StepSkip('content/ lacks %d official files (%s...)' % (len(missing), missing[0]))
        # The build (as mods/apply, on the default as base).
        expect = 'mods-b' if 'mods-a' in entries else 'mods-a'
        as_found = self.saved_state('switch-before')  # the menu compares these with saved configuration
        start = time.monotonic()
        out = self.ok(
            [
                'mods',
                'apply',
                '--base',
                previous,
                '--generation',
                '%s/%s' % (MODS_PROFILE, packages.name),
                MODS_MACHINE,
                '--name',
                MODS_NAME,
                '--wait',
            ],
            T_MODS,
        )
        built = time.monotonic() - start
        found = re.search(r'library entry (mods-[ab])', out)
        if not found or found.group(1) != expect:
            raise StepFail(
                'mods apply: expected library entry %s, got %r (default %s, content official)'
                % (expect, out[-200:], previous)
            )
        folder = '%s/%s' % (LIBRARY, expect)
        stage = 'built'
        try:
            mine = self.sha_lines(
                self.fetch(folder + '/files.sha256', 'mods-files.sha256').decode(errors='replace'), 'content/'
            )
            if 'op1_factory.json' not in mine:
                raise StepFail('%s/files.sha256 lists no content/op1_factory.json' % folder)
            # Switch to the mod set.
            stage = 'switching'
            start = time.monotonic()
            self.ok(['set-default', expect, '--yes-write'], T_SWITCH)
            there = time.monotonic() - start
            stage = 'installed'
            if self.slots('switch-mods')[2]['default'] != expect:
                raise StepFail('the default is not %s after set-default' % expect)
            record = self.record_text('record1.txt') or ''
            if not record.startswith('state=installed\nentry=%s\n' % expect):
                raise StepFail('the record says %r, expected installed for %s' % (record[:60], expect))
            for path, sha in sorted(mine.items()):
                data = self.fetch('%s/%s' % (CONTENT, path), 'mods-' + path.replace('/', '_'), meter)
                if hashlib.sha256(data).hexdigest() != sha:
                    raise StepFail('content/%s (%d B) differs from %s/files.sha256' % (path, len(data), folder))
            added = sorted(p for p in mine if p not in official)
            if not [p for p in added if p.startswith('display/')] or not [p for p in added if p.startswith('presets/')]:
                raise StepFail('the mod set brought no display/ and presets/ files: %s' % added)
            # Switch back.
            stage = 'restoring'
            start = time.monotonic()
            self.ok(['set-default', previous, '--yes-write'], T_SWITCH)
            back = time.monotonic() - start
            stage = 'restored'
            if self.slots('switch-back')[2]['default'] != previous:
                raise StepFail('the default is not %s after set-default back' % previous)
            record = self.record_text('record2.txt') or ''
            if not record.startswith('state=official\n'):
                raise StepFail('the record says %r, expected official' % record[:60])
            saved = self.sha_lines(self.fetch(CONTENT_SAVED_LIST, 'saved.sha256').decode(errors='replace'))
            if saved != official:
                raise StepFail('content-official/content.sha256 differs from %s/content.sha256' % previous)
            after = self.tree(CONTENT)
            if after != before:
                gone = sorted(set(before[0]) - set(after[0]) | before[1] - after[1])
                extra = sorted(set(after[0]) - set(before[0]) | after[1] - before[1])
                sized = sorted(p for p in before[0] if p in after[0] and before[0][p] != after[0][p])
                raise StepFail(
                    'content/ not as before: gone %s, new %s, other size %s' % (gone[:5], extra[:5], sized[:5])
                )
            catalog = self.fetch(CONTENT + '/op1_factory.json', 'catalog2.json', meter)
            if hashlib.sha256(catalog).hexdigest() != official['op1_factory.json']:
                raise StepFail('content/op1_factory.json is not the official catalog after the switch back')
            total = sum(before[0][p] for p in official)
            speed = meter[0] / meter[1] if meter[1] > 0 else 0.0
            estimate = total / speed if speed else float('inf')
            if estimate <= FULL_READ:
                for path, sha in sorted(official.items()):
                    data = self.fetch('%s/%s' % (CONTENT, path), 'official-file.bin')
                    if hashlib.sha256(data).hexdigest() != sha:
                        raise StepFail(
                            'content/%s (%d B) differs from content-official/content.sha256' % (path, len(data))
                        )
                how = 'all %d official files hashed (%d B)' % (len(official), total)
            else:
                how = '%d official files by size, the catalog by hash (a full read of %d B: ~%.0f s at %.1f KiB/s)' % (
                    len(official),
                    total,
                    estimate,
                    speed / 1024,
                )
            self.remove_tree(folder)
            stage = 'removed'
            if expect in self.names(LIBRARY):
                raise StepFail('ls %s still lists %s' % (LIBRARY, expect))
        except Exception as error:
            raise StepFail('%s; %s' % (error, self.switch_recover(stage, previous, expect, folder)))
        return (
            '%s built in %.1f s (entry %s, base %s); switched to it in %.1f s (%d content files, record installed, '
            'catalog equal); back to %s in %.1f s (record official, %s; its files and folders gone; content/ '
            'as before: %d files, %d folders); %s removed; %s'
            % (
                MODS_MACHINE,
                built,
                expect,
                previous,
                there,
                len(mine),
                previous,
                back,
                how,
                len(before[0]),
                len(before[1]),
                folder,
                self.same_state(as_found, 'switch-after'),
            )
        )

    def switch_recover(self, stage, previous, entry, folder):
        """After a failure in content/switch: the previous default set back when the mod set may be in, the
        entry removed once that worked; the device's state, in words."""
        if stage == 'removed':
            return 'state: default %s, content official, %s removed' % (previous, entry)
        if stage in ('switching', 'installed', 'restoring'):
            code, out, err = self.host(['set-default', previous, '--yes-write'], T_SWITCH)
            if code:
                return (
                    "state: set-default %s FAILED again (%s): the content may be %s's or mid-switch (the menu "
                    'says "content needs repair"); %s kept; repair: uv run xyf-host set-default %s --yes-write'
                    % (previous, why(code, out, err), entry, entry, previous)
                )
        try:
            self.remove_tree(folder)
            left = '%s removed' % entry
        except StepFail as error:
            left = '%s not removed (%s)' % (entry, error)
        return 'state: default %s set back (content official by the switch; not re-checked), %s' % (previous, left)

    # #197: a flip on the Mods page is saved at once; the pass turns fm2op on, reads the record back while
    # the page is shown, sees the first row's mark, then turns it off again (saved back). Never PLAY on the first row.
    def config_ready(self):
        """The preconditions (reads only; StepSkip with the reason): fm2op's Mods page row."""
        if self.opts.sim and not self.opts.sim_log:
            raise StepSkip('simulator without --sim-log: the menu state is read from its log')
        found = mods_row(MODS_PACKAGES, MODS_MACHINE)
        if found is None:
            raise StepSkip(
                'no local package set with %s readable in it (%s): its Mods page row is not known'
                % (MODS_MACHINE, MODS_PACKAGES)
            )
        root = '/yaffs2/xy-forever/mods'  # mods/<generation>/ (#124)
        code, out, err = self.host(['ls', root, '--json'], T_ANSWER)
        try:
            sets = [e['name'] for e in json.loads(out[out.index('[') :])] if code == 0 else []
        except ValueError:
            sets = []
        if sets != [MODS_PACKAGES.name] or MODS_MACHINE not in self.names('%s/%s' % (root, MODS_PACKAGES.name)):
            raise StepSkip(
                'the device holds package sets %s, not %s alone with %s (the page lists the newest)'
                % (sets, MODS_PACKAGES.name, MODS_MACHINE)
            )
        default = self.slots('config-before')[2]['default']
        from scripts.device_pass_library import read_config

        fields = read_config(self, StepSkip)
        if fields.get('mods') or fields.get('base', default) not in ('', default):
            raise StepSkip(
                'the saved configuration is not "no mods on the default" (mods=%r, base=%r, default %s)'
                % (fields.get('mods'), fields.get('base'), default)
            )
        item = [i for i in self.menu_catalog('config-catalog.bin')['items'] if i['id'] == default]
        if not item or not item[0]['usable'] or item[0]['content_missing']:
            raise StepSkip('the default %s is not usable with its content installed (%s)' % (default, item))
        names = self.names('%s/%s' % (LIBRARY, default))
        if 'content.sha256' not in names:
            raise StepSkip('the default %s has no content.sha256' % default)
        state = self.record_text('config-content.txt')
        if state is not None and not state.startswith('state=official\n'):
            raise StepSkip('content/ is not official: the record says %r' % state.split('\n', 2)[:2])
        official = self.sha_lines(
            self.fetch('%s/%s/content.sha256' % (LIBRARY, default), 'config-official.sha256').decode(errors='replace')
        )
        if (
            official.get('op1_factory.json')
            != hashlib.sha256(self.fetch(CONTENT + '/op1_factory.json', 'config-catalog.json')).hexdigest()
        ):
            raise StepSkip('content/op1_factory.json is not the official catalog of %s' % default)
        return found

    def seen(self, name, before, what):
        """Device: a screenshot that differs from before (the key acted); simulator: None (its log is read)."""
        self.sleep(0.3)
        if self.opts.sim:
            return None
        shot = self.shot(name)
        if shot == before:
            raise StepFail('%s did not change the screen (%s)' % (what, name))
        return shot

    def sim_wait(self, cond, what, after=0):
        for _ in range(30):
            page = self.sim_page()
            if len(self.sim_lines('mods page: ')) > after and cond(page):
                return page
            self.sleep(0.1)
        raise StepFail('simulator: %s not seen (last page line %s)' % (what, self.sim_page()))

    def open_mods(self, name):
        """STOP, TRACK3: the Mods row, seen selected (never the first row); PLAY; the list read."""
        self.key('stop')
        self.key('track3')
        self.sleep(0.3)
        if self.opts.sim:
            rows = self.sim_lines('menu: screen ')
            if not rows or not rows[-1].startswith('0 row 2 '):
                raise StepFail('simulator: the Mods row is not selected (%s): PLAY not sent' % (rows[-1:] or 'no line'))
            shown = len(self.sim_lines('mods page: '))
        else:
            selected = self.shot(name + '-row.png')
            rows = png_pixels(selected)
            if not row_selected(rows, 2) or row_selected(rows, 0) or row_selected(rows, 3):
                raise StepFail('after TRACK3 the Mods row is not the selected one: PLAY not sent')
        self.key('play')
        if self.opts.sim:
            return self.sim_wait(lambda p: p.get('view') == '1' and p.get('shown', '0') != '0', 'the list read', shown)
        last = None
        for _ in range(T_PAGE * 2 // 3):
            self.sleep(1.5)
            shot = self.shot(name)
            if shot == last and shot != selected:
                return shot
            last = shot
        raise StepFail('the Mods page did not settle within %d s (still reading?)' % T_PAGE)

    def saved_mods(self, name, want):
        """Wait at most T_SAVED seconds for the saved selection to match the flip."""
        deadline = time.monotonic() + T_SAVED
        while True:
            try:
                record = config_records.record_name(self.names(config_records.ROOT))
                data = self.read(config_records.ROOT + '/' + record, name, '--all') if record else None
                fields = config_records.fields(data, record) if data is not None else {}
            except StepFail, ValueError:  # busy while the config job writes it: read again
                fields = {}
            if fields.get('mods') == want:
                return
            if time.monotonic() > deadline:
                raise StepFail('saved mods=%r, expected %r, %d s after the flip' % (fields.get('mods'), want, T_SAVED))
            self.sleep(1.0)

    def step_pending(self):
        position, total = self.config_ready()
        h0 = self.home_view('config-home0.png')
        if self.opts.sim and h0['pending']:
            raise StepSkip('the first row is pending already (%s): the pass does not touch it' % h0['label'])
        stage = 'nothing changed'
        try:
            page0 = self.open_mods('config-page0.png')
            for _ in range(position):
                self.key('plus')
            # Row 0 is selected as the page opens (no key, no change); either way the row is checked below.
            chosen = self.seen('config-chosen.png', page0, 'PLUS x%d' % position) if position else page0
            if self.opts.sim:
                self.sim_wait(lambda p: p.get('selected') == MODS_MACHINE, '%s selected' % MODS_MACHINE)
            else:  # the bar on the row worked out (sorted; packages only), alone
                pixels, row = png_pixels(chosen), mods_screen_row(position, total)
                if not all(mods_row_selected(pixels, r) == (r == row) for r in range(MODS_VISIBLE_ROWS)):
                    raise StepFail(
                        'after PLUS x%d the selected Mods page row is not visible row %d (of %d rows; '
                        'config-chosen.png): PLAY not sent' % (position, row, total)
                    )
            stage = '%s may be saved on' % MODS_MACHINE
            self.key('play')
            on = self.seen('config-on.png', chosen, 'PLAY on %s' % MODS_MACHINE)
            if self.opts.sim:
                self.sim_wait(lambda p: MODS_MACHINE in p['on'], '%s on' % MODS_MACHINE)
            self.saved_mods('config1.txt', MODS_MACHINE)  # saved, the page still shown
            self.key('stop')
            h1 = self.home_view('config-home1.png')
            if self.opts.sim:
                if not h1['pending'] or '(rebuild)' not in h1['label']:
                    raise StepFail('the first row shows no "(rebuild)" mark: %r' % h1['band'])
            elif h1['band'] == h0['band'] or h1['right'] < h0['right'] + MARK_GROWTH:
                raise StepFail(
                    'the first row did not get the longer "(rebuild)" text (text end x %d -> %d)'
                    % (h0['right'], h1['right'])
                )
            again = self.open_mods('config-page1.png')
            if self.opts.sim:
                if again.get('selected') != MODS_MACHINE or MODS_MACHINE not in again['on']:
                    raise StepFail('the page came back without %s selected and on: %s' % (MODS_MACHINE, again))
            elif again != on:
                raise StepFail('the page came back differently (config-page1.png, config-on.png): PLAY not sent')
            self.key('play')
            self.seen('config-off.png', again, 'PLAY on %s' % MODS_MACHINE)
            if self.opts.sim:
                self.sim_wait(lambda p: MODS_MACHINE not in p['on'], '%s off' % MODS_MACHINE)
            self.saved_mods('config2.txt', '')
            stage = '%s off again: saved without mods' % MODS_MACHINE
            self.key('stop')
            h3 = self.home_view('config-home3.png')
            if h3['band'] != h0['band']:
                raise StepFail(
                    'the first row is not as before: %s'
                    % ('%r' % h3['band'] if self.opts.sim else 'see config-home0.png, config-home3.png')
                )
        except Exception as error:
            raise StepFail('%s; %s' % (error, self.config_recover(stage)))
        return (
            '%s on (Mods row %d): saved at once, the first row showed the "(rebuild)" mark; %s off: saved, the mark '
            'gone (%s). No boot'
            % (MODS_MACHINE, position, MODS_MACHINE, h3['label'] if self.opts.sim else 'screenshots')
        )

    def config_recover(self, stage):
        """STOP until the home menu shows (STOP: the page goes back; never PLAY); the state in words."""
        home = None
        try:
            for _ in range(2):  # the page -> home, home: nothing
                self.key('stop')
                self.sleep(0.3)
            if not self.opts.sim:
                rows = png_pixels(self.shot('recover.png'))
                home = any(row_selected(rows, r) for r in range(HOME_ROWS))
        except StepFail:
            pass
        return 'state: %s; STOP sent%s. A saved %s is undone on the Mods page (%s off: saved at once)' % (
            stage,
            '' if self.opts.sim else (', the home menu shown' if home else ', the home menu NOT seen'),
            MODS_MACHINE,
            MODS_MACHINE,
        )

    def step_content_scratch(self):
        base, name = CONTENT_SCRATCH.rsplit('/', 1)
        data = pass_file('content')
        local, got = self.artifact(name), self.artifact('got-' + name)
        local.write_bytes(data)
        self.ok(['put', local, CONTENT_SCRATCH, '--no-verify'])
        self.ok(['get', CONTENT_SCRATCH, got])
        if got.read_bytes() != data:
            raise StepFail('get %s: differs from what was put' % CONTENT_SCRATCH)
        self.ok(['rm', CONTENT_SCRATCH])
        if name in self.names(base):
            raise StepFail('ls %s still lists %s' % (base, name))
        return 'put %d B, get identical, rm; ls content/: gone' % len(data)

    def step_writes_restart(self):
        return 'home in %.1f s' % self.restart()[0]

    def step_after_restart(self):
        self.check_pass_file('after-restart.txt')
        default = self.slots('after')[2]['default']
        if default != self.state['default0']:
            raise StepFail('default is %s after the restart, expected %s' % (default, self.state['default0']))
        if SCRATCH_ID in [e['name'] for e in self.ls(SCRATCH_INFO.rsplit('/', 2)[0])]:
            raise StepFail('%s is back after the restart' % SCRATCH_ID)
        return 'file identical, default %s, no %s' % (default, SCRATCH_ID)

    # -- mtp -----------------------------------------------------------------------------------
    def step_mtp_test(self):
        _, usb = self.status()
        self.state['frames'], self.state['crashes'] = usb.get('frames', 0), usb.get('crash_restarts')
        out = self.ok(['mtp-test', '--hold-ms', HOLD_MS, '--yes-test'], T_ANSWER, mtp_timeout=T_MTP - HOLD_MS / 1000.0)
        r = parse_json(out) or {}
        if r.get('status') != 'ok' or r.get('step') != 11 or not r.get('mounted') or not r.get('unmounted'):
            raise StepFail(
                'mtp-test: status %s, stopped at step %s, mounted %s, unmounted %s'
                % tuple(r.get(k) for k in ('status', 'step', 'mounted', 'unmounted'))
            )
        return 'all 11 steps: %s entries, read %s B, held %s ms' % (r.get('entries'), r.get('read'), r.get('wait_ms'))

    def step_home_after_mtp(self):
        _, usb = self.status()
        if usb.get('frames', 0) <= self.state['frames']:
            raise StepFail('frames %s <= %s: the device restarted' % (usb.get('frames'), self.state['frames']))
        if usb.get('crash_restarts') != self.state['crashes']:  # a restart during MTP
            raise StepFail(
                'crash restarts %s -> %s: the device restarted' % (self.state['crashes'], usb.get('crash_restarts'))
            )
        self.slots(None)
        self.shot('home-after-mtp.png')
        return 'no restart (frames %s), slots read' % usb.get('frames')

    # -- end -----------------------------------------------------------------------------------
    def step_end_status(self):
        _, usb = self.status(T_BACK)
        crashes = usb.get('crash_restarts')
        if not crashes:
            return 'home answers, crash restarts 0'
        return 'crash restarts %s cleared by a clean REBOOT (%.1f s)' % (crashes, self.restart()[0])

    def step_hold_off(self):
        self.ok(['hold', 'off'])
        return 'ok'

    def step_keepalive(self):
        pid = self.keepalive.start(self.folder)
        if pid is None:
            raise StepSkip('the enclosing device lease manages keepalive')
        self.note('keepalive.sh pid %d' % pid)
        self.state['keepalive_pid'] = pid
        reply = self.folder / 'keepalive.json'
        deadline = time.monotonic() + T_KEEPALIVE
        while time.monotonic() < deadline:
            if reply.is_file() and '"usb_service"' in reply.read_text(errors='replace'):
                return 'pid %d polls home; stop: touch FOLDER/stop-keepalive' % pid
            self.sleep(0.25)
        raise StepFail('keepalive.sh (pid %d) got no STATUS within %g s' % (pid, T_KEEPALIVE))

    # -- runner --------------------------------------------------------------------------------
    def run_step(self, index, stage, name):
        self.prefix, self.log, self.no_answer, self.port_busy = '%02d-%s' % (index, name), [], False, None
        start = time.monotonic()
        try:
            if self.opts.sim and name in SIM_SKIPS:
                raise StepSkip(SIM_SKIPS[name])
            detail, result = getattr(self, 'step_' + name.replace('-', '_'))(), 'PASS'
        except StepFail as error:
            detail, result = str(error), 'FAIL'
        except StepSkip as error:
            detail, result = str(error), 'SKIP'
        except Exception as error:  # a bug or a local file problem: still a FAIL row
            detail, result = '%s: %s' % (type(error).__name__, error), 'FAIL'
        seconds = time.monotonic() - start
        self.log.append('= %s %.2f s: %s\n' % (result, seconds, detail))
        self.pending.append((self.prefix + '.txt', ''.join(self.log)))
        self.flush()
        return dict(
            stage=stage, step=name, result=result, seconds=round(seconds, 2), detail=detail, file=self.prefix + '.txt'
        )

    def flush(self):
        if self.folder:
            for name, text in self.pending:
                (self.folder / name).write_text(text)
            self.pending = []


READ_REQ = ids.COMMANDS[P.READ_FILE]['request']
BEGIN_REQ = ids.COMMANDS[P.PUT_BEGIN]['request']
WRITE_REQ = ids.COMMANDS[P.FILE_WRITE]['request']


def filler(n):
    """Staged bytes without 0x58 ('X'), so no frame start can appear inside the data."""
    return bytes(i & 0x3F for i in range(n))


def build_identity(build_dir):
    """(image, release version, mode, cmds bitmap) the HELLO of this build must show: its manifest (image: the
    first 8 hex digits of its candidate's SHA-256), and the bitmap the spec gives for its mode (xyf_build.usb_switches,
    as --verify checks it)."""
    manifest = json.loads((Path(build_dir) / 'manifest.json').read_text())
    if manifest.get('schema') != xyf_build.MANIFEST_SCHEMA:
        raise ValueError(
            'manifest schema %r, this pass reads %d: rebuild it' % (manifest.get('schema'), xyf_build.MANIFEST_SCHEMA)
        )
    return (
        xyf_build.image_name(manifest['candidate_sha256']),
        manifest['version'],
        manifest['mode'],
        P.answered(xyf_build.usb_switches(manifest['mode'])),
    )


def selected_stages(opts):
    chosen = set(DEFAULT_STAGES if opts.stages is None else (s.strip() for s in opts.stages.split(',') if s.strip()))
    unknown = chosen - set(DEFAULT_STAGES)
    if unknown:
        raise ValueError('unknown stage(s) %s; choose from %s' % (', '.join(sorted(unknown)), ','.join(DEFAULT_STAGES)))
    if opts.install:
        chosen.add('install')
    return chosen | {'check', 'session', 'end'}


EVIDENCE = []  # this run's evidence folders: run_main writes error.txt into the last


def run_main(main):
    """A device script's __main__: SIGTERM ends the run as an error, and an error that stops the script after its
    evidence folder exists is written there too (error.txt), so no exit is silent (#50)."""
    signal.signal(signal.SIGTERM, lambda *_: sys.exit('stopped by SIGTERM'))
    try:
        return main()
    except BaseException as error:
        if EVIDENCE and not (isinstance(error, SystemExit) and isinstance(error.code, int)):
            with contextlib.suppress(OSError):
                (EVIDENCE[-1] / 'error.txt').write_text(traceback.format_exc())
        raise


def new_folder(root, kind='device-pass'):
    day = datetime.now().strftime('%Y-%m-%d')
    taken = [
        int(m.group(1))
        for p in root.glob('%s-%s-*' % (day, kind))
        if (m := re.fullmatch(r'.*-%s-(\d+)' % kind, p.name))
    ]
    folder = paths.private_output(root / ('%s-%s-%d' % (day, kind, max(taken, default=0) + 1)))
    folder.mkdir(parents=True, exist_ok=False)
    EVIDENCE.append(folder)
    return folder


def settle_folder(folder, sim, passed, keep):
    """A simulator run's evidence folder is removed when everything passed (unless keep); a
    real-device run always keeps its folder. Returns what the table's folder line shows."""
    if not sim or not passed or keep:
        return folder
    shutil.rmtree(folder)
    return '%s removed (simulator run, nothing failed; --keep keeps it)' % folder


def table(rows, folder):
    width = max(len('%s/%s' % (r['stage'], r['step'])) for r in rows)
    lines = ['%-*s  %-6s %7s  %s' % (width, 'step', 'result', 'seconds', 'detail')]
    lines += [
        '%-*s  %-6s %7.1f  %s' % (width, '%s/%s' % (r['stage'], r['step']), r['result'], r['seconds'], r['detail'])
        for r in rows
    ]
    lines.append('folder: %s' % folder)
    return '\n'.join(lines)


def print_plan(opts, chosen):
    print('Device pass plan: nothing is sent, nothing is written.')
    for stage, steps in STAGES:
        state = 'on' if stage in chosen else 'off'
        print('%s (%s)' % (stage, state))
        for name, bound, text in steps:
            bound = opts.wait if bound is None else bound
            print('  %-16s <= %5.0f s  %s' % (name, bound, text))
    if opts.install:
        print(
            'install build: %s (%s); %s'
            % (
                opts.install,
                opts.build_note,
                (
                    'its HELLO must show image=%s, version=%s, cmds %032x'
                    % (opts.build_image, opts.build_version, opts.build_cmds)
                ),
            )
        )
    if opts.big_replies and 'protocol' in chosen:
        print(
            '--big-replies: replies above HELLO read= and 4 KiB stage.data are sent (Test 038: a 4 KiB '
            'reply hung the device; a hang needs a power cycle)'
        )
    if opts.sim:
        print('simulator %s: SKIP %s' % (opts.sim, ', '.join(n for n in SIM_SKIPS)))
    print(
        'end state: home menu with USB, hold off, crash count 0, keepalive.sh polling '
        '(stop: touch FOLDER/stop-keepalive); never the OS.'
    )


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--plan', action='store_true', help='print the steps and exit; touches nothing')
    parser.add_argument('--stages', help='comma list of %s (default: all of them)' % ','.join(DEFAULT_STAGES))
    parser.add_argument('--install', metavar='BUILD_DIR', help='install this verified build (stage install)')
    parser.add_argument(
        '--big-replies',
        action='store_true',
        help='stage protocol: also whole-file reads of 1-4 KiB (replies above HELLO read=) and '
        '%d B stage.data chunks. CAN HANG THE DEVICE (Test 038: a 4 KiB reply hung it; '
        'a hang needs a power cycle, then later steps SKIP); off by default' % BIG_CHUNK,
    )
    parser.add_argument(
        '--alt-default',
        metavar='ID',
        help='entry for the set-default round trip '
        '(default: another usable entry with its content installed, %s first)' % ' or '.join(ALT_DEFAULTS),
    )
    parser.add_argument(
        '--sim',
        metavar='HOST:PORT,CONTROL',
        help='run against the device simulator (xyf-host --sim); steps it does not host are SKIP',
    )
    parser.add_argument(
        '--sim-log',
        metavar='FILE',
        help="with --sim: the simulator's log (its stdout), where the "
        'menu state is read (stage config, and the first home row the writes, mods and content steps '
        'compare); without it those checks SKIP',
    )
    parser.add_argument(
        '--wait',
        type=float,
        default=T_ANSWER,
        help='seconds the check waits for the device (default %g; longer when started '
        'before asking the developer to power on)' % T_ANSWER,
    )
    parser.add_argument(
        '--keep',
        action='store_true',
        help='with --sim: keep the evidence folder when every step passed (removed otherwise; '
        'a real-device run always keeps it)',
    )
    return parser.parse_args(argv)


def main(argv=None, device=None, keepalive=None, sessions=None, sleep=None):
    opts = parse_args(argv)
    try:
        chosen = selected_stages(opts)
    except ValueError as error:
        print('device_pass: %s' % error, file=sys.stderr)
        return EXIT_USAGE
    opts.build_sha = opts.build_note = opts.build_image = opts.build_version = opts.build_mode = opts.build_cmds = None
    if opts.install:
        try:
            data, notes = host.load_build(opts.install)
            opts.build_image, opts.build_version, opts.build_mode, opts.build_cmds = build_identity(opts.install)
        except (ValueError, OSError, KeyError) as error:
            print('device_pass: install build refused: %s' % error, file=sys.stderr)
            return EXIT_USAGE
        opts.build_sha = hashlib.sha256(data).hexdigest()
        opts.build_note = '; '.join(notes[1:])
    if opts.sim:
        try:
            host.sim_address(opts.sim)
        except ValueError as error:
            print('device_pass: %s' % error, file=sys.stderr)
            return EXIT_USAGE
        host.device_client.SIM = opts.sim
    if opts.plan:
        print_plan(opts, chosen)
        return EXIT_PASS
    keepalive = keepalive or Keepalive()
    run = Pass(device or HostDevice(), opts, keepalive, sleep)
    stopped = [] if opts.sim else keepalive.stop_running()  # the simulator shares no port with them

    started = datetime.now(UTC)
    rows, index = [], 0
    index += 1
    check = run.run_step(index, 'check', 'check')
    if check['result'] == 'FAIL' and run.port_busy:  # another program holds the port: not a power cycle
        print('NEED: %s' % run.port_busy)
        return EXIT_NEED
    if check['result'] == 'FAIL' and run.no_answer:
        print(SIM_NEED if opts.sim else NEED)
        return EXIT_NEED
    run.folder = new_folder(Path(sessions or SESSIONS), 'sim-pass' if opts.sim else 'device-pass')
    run.flush()
    rows.append(check)
    failed = None if check['result'] == 'PASS' else 'check/check'
    for stage, steps in STAGES[1:-1]:
        for name, _, _ in steps:
            index += 1
            if stage not in chosen:
                rows.append(dict(stage=stage, step=name, result='SKIP', seconds=0.0, detail='not selected', file=None))
            elif failed:
                rows.append(
                    dict(
                        stage=stage,
                        step=name,
                        result='SKIP',
                        seconds=0.0,
                        detail='after FAIL of %s' % failed,
                        file=None,
                    )
                )
            else:
                print('[%s] %s/%s' % (time.strftime('%H:%M:%S'), stage, name), file=sys.stderr, flush=True)
                row = run.run_step(index, stage, name)
                rows.append(row)
                if row['result'] == 'FAIL':
                    failed = '%s/%s' % (stage, name)
    # End of session: always. No answer (or #65's question on): the rest is SKIP.
    lost = CONFIRM_ON if check['detail'] == CONFIRM_ON else None
    for name, _, _ in STAGES[-1][1]:
        index += 1
        if lost:
            rows.append(dict(stage='end', step=name, result='SKIP', seconds=0.0, detail=lost, file=None))
            continue
        print('[%s] end/%s' % (time.strftime('%H:%M:%S'), name), file=sys.stderr, flush=True)
        row = run.run_step(index, 'end', name)
        rows.append(row)
        if name == 'end-status' and row['result'] == 'FAIL':
            lost = run.port_busy or (
                'device not answering: if it stays so, power off, then power on holding PLAY'
                if run.no_answer
                else 'after FAIL of end/end-status'
            )
    code = EXIT_FAIL if any(r['result'] == 'FAIL' for r in rows) else EXIT_PASS
    summary = dict(
        format='xyf-device-pass/1',
        folder=str(run.folder),
        argv=list(argv or sys.argv[1:]),
        started_utc=started.isoformat(),
        finished_utc=datetime.now(UTC).isoformat(),
        stages={stage: stage in chosen for stage, _ in STAGES},
        install_sha256=opts.build_sha,
        keepalives_stopped=stopped,
        keepalive_pid=run.state.get('keepalive_pid'),
        default_at_start=run.state.get('default'),
        protocol=run.state.get('protocol'),
        file_kib_s=run.state.get('file_kib_s'),
        steps=rows,
        exit_code=code,
        passed=sum(r['result'] == 'PASS' for r in rows),
        failed=sum(r['result'] == 'FAIL' for r in rows),
        skipped=sum(r['result'] == 'SKIP' for r in rows),
    )
    (run.folder / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(table(rows, settle_folder(run.folder, opts.sim, code == EXIT_PASS, opts.keep)))
    return code


if __name__ == '__main__':
    sys.exit(run_main(main))
