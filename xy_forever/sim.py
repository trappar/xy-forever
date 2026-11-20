"""XY Forever device simulator: build, seed, run and control (docs/architecture/simulator.md, "Simulator").

The simulator runs the device's own C++ on the Mac: B's USB service and menu link, A's storage
server, both sides of the file boot and the peer session, cold_storage's yaffs session. Each
processor is a process; they talk through the board (sim/runtime.cpp), which relays the link,
injects faults and power-cycles. B's USB CDC endpoint is a TCP port on 127.0.0.1 that carries
the device's raw byte stream; it exists only while the service has USB on, so a restart drops
the connection and the port comes back as the device re-enumerates.

The device sources are copied into the build folder and rewritten mechanically (REWRITES):
casts between device addresses and pointers go through emulated memory, stock calls reach the
host shims in sim/stock.cpp, and the Blackfin asm (cycle counter, cache, stack pointer) becomes
host calls. Nothing is rewritten that decides anything; a rewrite that no longer matches stops
the build. The stock 1.2.1 bootloader image (private) is loaded into each processor's memory,
so every CRC identity check runs against the real bytes.
"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib

from xy_forever import paths, procgroup
from xy_forever import usb_proto as P

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT / 'sim'
CXX = '/usr/bin/clang++'
DEFAULT_PORT = 7341
STOCK_BOOTLOADER = paths.STOCK_121 / 'bootloader.ldr'
STOCK_OS = paths.STOCK_121 / 'os.ldr'
STATE = paths.SESSIONS / 'simulator'           # default storage folder, boot partition and build cache

TARGET_SOURCES = ('usb_service', 'usb_proto', 'storage_server', 'file_engine', 'file_client', 'menu_link', 'file_boot',
                  'cold_storage', 'retained_lookup', 'cold_heap', 'peer_services', 'xyf_library', 'xyf_cold_library',
                  'xyf_manifest', 'xyf_recognise', 'xyf_filelist', 'xyf_catalog', 'xyf_state', 'xyf_state_write', 'xyf_store', 'xyf_info', 'xyf_crc32',
                  'boot_services', 'otp_keys', 'tfw_device_check', 'xyf_stage2', 'stage2_main',
                  'xyf_menu', 'xyf_mods', 'xyf_mods_io',                 # the menu's state and the Mods page
                  'ui_pages', 'ui_gfx',                                   # the bar's scale() (nothing is drawn)
                  'knob_input',                                           # the knob reader
                  'mtp_session')                                          # the MTP session (XYF_MTP_SESSION)
NATIVE_SOURCES = ('ldr', 'hash', 'trace_slot', 'trace_slot_zero', 'memory', 'peer_boot', 'inflate',
                  'tfw_check', 'tfw', 'tfw_signature', 'p224', 'aes',
                  'mod_job', 'mod_link', 'mod_elf', 'mod_numbers', 'mod_patch', 'mod_content',   # the job on B
                  'mtp_responder')                                                                  # the MTP responder
SIM_SOURCES = ('clock', 'runtime', 'link', 'stock', 'devices', 'boot', 'stage2')
# Files whose device-only (__bfin__) part the simulator hosts. boot_services.cpp is not one:
# its device part reads the OTP key and stays out.
DEVICE_PARTS = ('usb_service.cpp', 'usb_service.hpp', 'storage_server.cpp', 'storage_server.hpp', 'file_client.cpp',
                'file_client.hpp', 'peer_services.cpp',
                'cold_storage.cpp', 'retained_lookup.cpp', 'cold_heap.cpp', 'xyf_clock.hpp', 'otp_keys.cpp', 'tfw_device_check.cpp',
                'xyf_mods_io.cpp', 'xyf_mods_io.hpp', 'mtp_session.cpp')
STOCK_TEMPLATE = re.compile(r'template<U32 A,\s*class R,\s*class\.\.\. Args>\s*__attribute__\(\(noinline,\s*noclone\)\)\s*'
                            r'R stock\(Args\.\.\. args\)\s*\{\s*return reinterpret_cast<R\(\*\)\(Args\.\.\.\)>\(A\)\(args\.\.\.\);\s*\}')
REWRITES = (   # (pattern, replacement, files it must match in: exact count per file)
    (STOCK_TEMPLATE, 'template<U32 A, class R, class... Args> R stock(Args... args) '
                     '{ return ::xyf_sim::stock<A, R>(args...); }',
     {'usb_service_transport.ipp': 1, 'storage_server.cpp': 1, 'file_client.cpp': 1, 'peer_services.cpp': 1,
      'otp_keys.cpp': 1}),
    (re.compile(r'template<class F> F\* call\(U32 address\) \{ return reinterpret_cast<F\*>\(address\); \}'),
     'template<class F> ::xyf_sim::Call<F> call(U32 address) { return ::xyf_sim::Call<F>{address}; }',
     {'cold_storage.cpp': 1}),
    (re.compile(r'__asm__ volatile\("%0 = CYCLES; %1 = CYCLES2;" : "=d"\((\w+)\), "=d"\((\w+)\)\);'),
     r'::xyf_sim::cycles(\1, \2);', {'usb_service_transport.ipp': 1, 'file_boot.cpp': 1, 'xyf_clock.hpp': 1}),
    (re.compile(r'__asm__ volatile\("(?:FLUSHINV|FLUSH|IFLUSH) \[%0\];" : : "a"\((\w+)\) : "memory"\);'), r'(void)(\1);',
     {'usb_service_transport.ipp': 2, 'usb_service_stage2.ipp': 1, 'file_client.cpp': 2, 'file_boot.cpp': 2}),
    (re.compile(r'__asm__ volatile\("SSYNC;(?: CSYNC;)?" : : : "memory"\);'), ';',
     {'usb_service_transport.ipp': 1, 'usb_service_stage2.ipp': 1, 'file_client.cpp': 1, 'file_boot.cpp': 2}),
    (re.compile(r'section\("\.text\.entry"\), '), '', {'stage2_main.cpp': 1}),   # no such Mach-O section
    # Before each assembly step, so the test can stop at verify even when a slice spans several phases.
    (re.compile(r'(?m)^            p = mj::step\(s\.job\);$'),
     '            if (job_usb && s.job.phase == mj::Phase::verify && ::xyf_sim::hold_mod_verify()) return;\n'
     '            p = mj::step(s.job);', {'usb_service_save_log.ipp': 1}),
    # 192: 7 -> 10, the three in repair_fresh (as the write window; changing these counts needs the
    # developer's explicit yes, docs/SAFETY.md).
    (re.compile(r'__asm__ volatile\("ssync;" ::: "memory"\);'), ';', {'cold_storage.cpp': 10}),
    (re.compile(r'__asm__ volatile\("%0 = SP;" : "=d"\((\w+)\)\);'), r'\1 = ::xyf_sim::stack_pointer();',
     {'file_boot.cpp': 1}),
    (re.compile(r'extern "C" char xy_forever_module_end\[\] __asm__\("__end"\);'),
     'static char* const xy_forever_module_end = static_cast<char*>(::xyf_sim::host(0x8ab80000u));',
     {'file_boot.cpp': 1}),
    (re.compile(r'__attribute__\(\(section\("\.data"\)\)\)'), '', None),
    (re.compile(r'__attribute__\(\(section\("\.data"\), (aligned\(\d+\))\)\)'), r'__attribute__((\1))', None),
    (re.compile(r'\breinterpret_cast<'), '::xyf_sim::cast<', None),
)
HELLO_HOOK = '#include "usb_service.hpp"\n'
HELLO_OVERRIDE = ('#if defined(XYF_SIM_HELLO_TEXT)\n#undef XYF_USBP_HELLO_TEXT\n'
                  '#define XYF_USBP_HELLO_TEXT XYF_SIM_HELLO_TEXT\n#endif\n')


def product_defines(version=None, mode='xyf-product', switches=None, hello=None):
    """The product build's defines (xyf_build.py build()), the hello release version (another one: version)
    and mode, and optionally other usb build switches or a whole hello text (a pre-v1 device, say)."""
    import xyf_build
    from xy_forever import release
    switches = set(xyf_build.usb_switches(mode) if switches is None else switches)
    flags = ['-DXYF_SIM=1', '-DXYF_STACK_END=%#xu' % xyf_build.WIDE_STACK_END, '-DXYF_CENSUS_REGIONS=24',
             '-DXYF_PLAIN_IMAGES=1', '-DXYF_SERVICE_SCREEN=1', '-DXYF_USB_MENU=1', '-DXYF_MENU_ON_FAILURE=1',
             '-DXYF_SERVICE_ON_MARKER_ONLY=1', '-DXYF_REBOOT_TARGETS=1', '-DXYF_ONE_SESSION=1', '-DXYF_NO_EMBEDDED=1',
             '-DXYF_CENSUS_BASE=%#xu' % (xyf_build.REPORTER - xyf_build.CENSUS_BYTES),
             '-DXYF_CENSUS_BYTES=%#xu' % xyf_build.CENSUS_BYTES, '-DXYF_MENU_LINK=1', '-DXYF_FILE_BOOT=1',
             '-DXYF_REMOTE_READ=1', '-DXYF_USB_SERVICE=1', '-DXYF_STAGE2_SERVICE=1',
             '-DXYF_MTP_SESSION=1',                    # the MTP session (the product build's too)
             '-DXYF_VERSION="%s"' % (version or release.VERSION), '-DXYF_VERSION_CODE=%#xu' % release.code(version or
                                                                                                  release.VERSION),
             '-DXYF_BUILD_MODE="%s"' % mode[len('xyf-'):]]
    flags += ['-D%s=1' % s for s in sorted(switches)]
    if mode == xyf_build.CRASH_TRIGGER_MODE:            # diag.crash; sim/devices.cpp halts B in place of the fault
        flags += xyf_build.CRASH_TRIGGER_DEFINES
    if hello is not None:
        flags.append('-DXYF_SIM_HELLO_TEXT="%s"' % hello)
    return flags


def rewrite(name, text):
    for pattern, replacement, counts in REWRITES:
        text, n = pattern.subn(replacement, text)
        if counts is not None and name in counts and n != counts[name]:
            raise RuntimeError('simulator rewrite %r matched %d times in %s, expected %d: update xy_forever/sim.py'
                               % (pattern.pattern[:40], n, name, counts.get(name, 0)))
    if name in DEVICE_PARTS or name.startswith('usb_service_'):     # usb_service.cpp's parts (.ipp)
        text = text.replace('defined(__bfin__)', '(defined(__bfin__) || defined(XYF_SIM))')
    if name == 'usb_service.cpp':
        if text.count(HELLO_HOOK) != 1:
            raise RuntimeError('usb_service.cpp: hello hook not found')
        text = text.replace(HELLO_HOOK, HELLO_HOOK + HELLO_OVERRIDE)
    return text


def policy_source():
    """cold.cpp's allowed and protected ranges, verbatim."""
    source = (ROOT / 'target/cold.cpp').read_text()
    parts = []
    for name in ('allowed', 'protected_ranges'):
        start = source.index('const Range %s[] = {' % name)
        parts.append(source[start:source.index('};', start) + 2])
    return '\n'.join(parts) + '\n'


def stock_image(out):
    """The stock 1.2.1 bootloader's loaded memory as {u32 address, u32 bytes, bytes} records."""
    from xy_forever.build_formats import Firmware
    firmware = Firmware(STOCK_BOOTLOADER.read_bytes())
    spans = sorted((r.address, r.address + r.length) for r in firmware.records if r.length and not r.ignored)
    merged = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    with open(out, 'wb') as f:
        for lo, hi in merged:
            f.write(struct.pack('<II', lo, hi - lo) + firmware.read(lo, hi - lo))


def available():
    """None when the simulator can be built here, else why not."""
    if not Path(CXX).exists():
        return 'no %s' % CXX
    if not STOCK_BOOTLOADER.is_file():
        return 'private stock 1.2.1 bootloader absent (%s)' % STOCK_BOOTLOADER
    return None


KEEP_BUILDS = 12          # the most recently used builds kept in a cache folder (others: a day unused)


def build(cache=None, **options):
    """Builds the simulator (cached by the hash of every input: sources, defines, the compiler,
    the stock image) and returns its folder. The tests share the default cache."""
    defines = product_defines(**options)
    inputs = hashlib.sha256(json.dumps(defines).encode())
    inputs.update(subprocess.run([CXX, '--version'], capture_output=True).stdout)
    here = Path(__file__).parent
    for path in sorted([*ROOT.glob('target/*.?pp'), *ROOT.glob('native/*.?pp'), *ROOT.glob('native/*.inc'),
                        *SIM.glob('*.?pp'), Path(__file__),
                        here / 'probe_report.py', here / 'build_formats.py', here / 'build_modules.py']):
        inputs.update(path.name.encode() + path.read_bytes())
    inputs.update(STOCK_BOOTLOADER.read_bytes())
    cache = Path(cache or STATE / 'builds')
    out = cache / inputs.hexdigest()[:16]
    binary = out / 'xyf_sim'
    if binary.is_file():
        os.utime(out)                                  # recently used: kept by prune()
        return out
    cache.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='build-', dir=cache))
    try:
        compile_into(work, defines)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    try:
        work.rename(out)
    except OSError:                                    # another build finished first
        shutil.rmtree(work, ignore_errors=True)
    prune(cache)
    return out


def prune(cache, keep=KEEP_BUILDS):
    """Removes builds beyond the keep most recently used that no build() asked for in a day (a
    simulator started from one may still be running), and partial builds over an hour old."""
    now = time.time()

    def mtime(path):                                   # None: gone (another test job pruned it first)
        try:
            return path.stat().st_mtime if path.is_dir() else None
        except OSError:
            return None
    times = {p: mtime(p) for p in cache.iterdir()}
    builds = sorted((p for p, t in times.items() if t is not None and not p.name.startswith('build-')),
                    key=times.get, reverse=True)
    old = [p for p in builds[keep:] if now - times[p] > 86400]
    partial = [p for p, t in times.items() if t is not None and p.name.startswith('build-') and now - t > 3600]
    for path in old + partial:
        shutil.rmtree(path, ignore_errors=True)


def compile_into(work, defines):
    from xy_forever.probe_report import report_only_fixture
    (work / 'target').mkdir()
    (work / 'native').mkdir()
    for path in ROOT.glob('target/*.?pp'):
        text = rewrite(path.name, path.read_text())
        owner = re.match(r'// Part of (\w+)\.cpp', text)      # a part of a source the simulator does not host
        if owner and owner.group(1) not in TARGET_SOURCES:      # (key_ui's) is not compiled either
            (work / 'target' / path.name).write_text(text)
            continue
        if '__asm__ volatile' in text and (path.suffix in ('.hpp', '.ipp') or path.stem in TARGET_SOURCES):
            raise RuntimeError('simulator: %s has asm no rewrite covers' % path.name)
        (work / 'target' / path.name).write_text(text)
    for path in [*ROOT.glob('native/*.?pp'), *ROOT.glob('native/*.inc')]:   # mod_patch: mod_known_bases.inc
        (work / 'native' / path.name).write_bytes(path.read_bytes())
    (work / 'gen').mkdir()
    (work / 'gen/cold_policy.inc').write_text(policy_source())
    (work / 'gen/trace_expected.inc').write_text(','.join(str(b) for b in report_only_fixture()) + '\n')
    stock_image(work / 'stock.mem')
    base = [CXX, '-std=c++17', '-O1', '-w', '-fno-strict-aliasing', '-pthread']
    device = base + ['-include', str(SIM / 'sim.hpp'), *defines, '-I', str(work / 'target'), '-I', str(work / 'gen')]
    jobs = [(device, work / 'target' / (n + '.cpp')) for n in TARGET_SOURCES]
    jobs += [(base, work / 'native' / (n + '.cpp')) for n in NATIVE_SOURCES]
    jobs += [(device + ['-I', str(SIM)], SIM / (n + '.cpp')) for n in SIM_SOURCES]

    def compile_one(job):
        flags, source = job
        obj = work / (source.parent.name + '-' + source.stem + '.o')
        result = subprocess.run([*flags, '-c', str(source), '-o', str(obj)], capture_output=True, text=True)
        return obj, result.stderr if result.returncode else None
    with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        compiled = list(pool.map(compile_one, jobs))
    # Every compile runs, and every failing source is named in source order: the error does not
    # depend on which compile finished first (a missing switch can break several sources).
    failed = [(source.name, error) for (_, source), (_, error) in zip(jobs, compiled) if error is not None]
    if failed:
        raise RuntimeError('simulator build failed: %s\n%s' % (', '.join(name for name, _ in failed), '\n'.join(
            '%s:\n%s' % (name, error[-4000:]) for name, error in failed)))
    objects = [obj for obj, _ in compiled]
    result = subprocess.run([CXX, '-pthread', *map(str, objects), '-o', str(work / 'xyf_sim')], capture_output=True,
                            text=True)
    if result.returncode:
        raise RuntimeError('simulator link failed:\n%s' % result.stderr[-4000:])
    for obj in objects:
        obj.unlink()


def slot_text(default, seq):
    body = ('default=%s\nseq=%d\n' % (default, seq)).encode()
    return body + b'crc32=%08x\n' % (zlib.crc32(body) & 0xffffffff)


def info_text(data, name, version):
    from .xyf_entry import info_text as canonical
    return canonical(name, version, 'official', data).encode('ascii')


def seed(root, image=None):
    """A typical library: opxy-121 (the default) and opxy-121-stock, both the stock 1.2.1
    os.ldr (a plain official image), and state-a.txt naming the default; content/ with a stand-in
    op1_factory.json. Returns root."""
    root = Path(root)
    image = image if image is not None else STOCK_OS.read_bytes()
    base = root / 'xy-forever'
    for entry, name in (('opxy-121', 'OP-XY 1.2.1'), ('opxy-121-stock', 'OP-XY 1.2.1 (stock copy)')):
        folder = base / 'firmware' / entry
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'os.ldr').write_bytes(image)
        (folder / 'info.txt').write_bytes(info_text(image, name, '1.2.1'))
    (base / 'log').mkdir(exist_ok=True)
    (base / 'state-a.txt').write_bytes(slot_text('opxy-121', 1))
    (base / 'settings.txt').write_bytes(b'format=1\nconfirm=0\n')   # the Settings file: tests run unattended
    (root / 'firmware.version').write_text('1.2.1\n')
    content = root / 'content'                         # the OS's content folder (writable), a stand-in catalog
    content.mkdir(exist_ok=True)
    (content / 'op1_factory.json').write_text('{"xyf_sim": "stand-in op1_factory.json"}\n')
    return root


def otp_fields(keys=None, public_key_file=None):
    """Processor B's OTP for the simulator's ROM stand-in, from the private area (keys/
    opxy-update-keys.json, keys/opxy-tfw-public-key.json; xy_forever/tfw_extract.py reads them):
    selectors 3/4 = the .tfw key of index 0 (16 bytes each), 5/6 = index 1, 9 = the vendor public
    key, as the stock wrappers read them. Bytes for --otp-stdin; None when a file is missing."""
    from .tfw_extract import ExtractError, key_records, public_key
    try:
        records, vendor = key_records(keys), public_key(public_key_file)
    except ExtractError:
        return None
    out = bytearray(bytes((9, 56)) + vendor)
    for at in range(0, len(records), 33):
        index, key = records[at], records[at + 1:at + 33]
        if index <= 1:
            out += bytes((3 + 2 * index, 16)) + key[:16] + bytes((4 + 2 * index, 16)) + key[16:]
    return bytes(out)


def free_port():
    """A free pair of local TCP ports (endpoint, control), picked at random below every system's
    ephemeral range (macOS 49152-, Linux 32768-): there the OS hands ports to every outgoing
    connection and bind to 0, and under parallel tests one often took the pair between the check
    here and the simulator's bind."""
    for _ in range(50):
        port = random.randrange(20000, 32000, 2)
        with socket.socket() as a, socket.socket() as b:
            try:
                a.bind(('127.0.0.1', port))
                b.bind(('127.0.0.1', port + 1))
            except OSError:
                continue
            return port
    raise RuntimeError('no free local port pair')


class PortTaken(RuntimeError):
    """The simulator stopped before its endpoint listened (a port already in use, most likely)."""


class Simulator:
    """One running simulator: start() it, talk to 127.0.0.1:port, control() its board."""

    def __init__(self, root, bootpart, port=None, build_dir=None, play=True, log=None, capacity=0x400000, otp=None,
                 virtual=True, otp_initialized=False, otp_a=None, a_otp_initialized=False):
        """otp: B's OTP fields for the ROM stand-in (otp_fields()); None: an OTP that answers nothing.
        virtual: device time moves only under the simulator's control (sim/clock.cpp: test runs); False: real
        time (interactive runs). otp_initialized starts B with both its ROM and the stock initialization
        guard already set, as after the stock bootloader's encrypted-image read."""
        self.root, self.bootpart = Path(root), Path(bootpart)
        # B records retain their original framing; bit7 marks independent A selectors.
        a_records = bytearray()
        at = 0
        while otp_a and at < len(otp_a):
            selector, count = otp_a[at:at + 2]
            a_records += bytes((selector | 0x80, count)) + otp_a[at + 2:at + 2 + count]
            at += 2 + count
        self.otp, self.virtual = (otp or b'') + a_records, virtual
        self.a_otp_initialized = a_otp_initialized
        self.otp_initialized = otp_initialized
        self.chosen = port is None                     # a free pair picked here: start() may pick again
        self.port = port or free_port()
        self.control_port = self.port + 1
        self.build_dir = Path(build_dir) if build_dir else build()
        self.play, self.log, self.capacity = play, log, capacity
        self.process = None

    @property
    def address(self):
        return '127.0.0.1:%d' % self.port

    def argv(self):
        return [str(self.build_dir / 'xyf_sim'), '--root', str(self.root), '--bootpart', str(self.bootpart),
                '--stock', str(self.build_dir / 'stock.mem'), '--port', str(self.port),
                '--control', str(self.control_port), '--capacity', str(self.capacity)] + ([] if self.play else ['--plain']) + \
            (['--otp-stdin'] if self.otp else []) + (['--virtual'] if self.virtual else []) + \
            (['--otp-initialized'] if self.otp_initialized else []) + \
            (['--a-otp-initialized'] if self.a_otp_initialized else [])

    def start(self, timeout=10.0):
        for attempt in range(3):
            log = open(self.log, 'ab') if self.log else subprocess.DEVNULL
            self.process = subprocess.Popen(self.argv(), stdin=subprocess.PIPE if self.otp else subprocess.DEVNULL,
                                            stdout=log, stderr=log)
            if self.otp:                               # on a pipe: never on disk or the command line
                self.process.stdin.write(self.otp)
                self.process.stdin.close()
            if self.log:
                log.close()
            try:
                self.wait_listening(timeout)
                return self
            except PortTaken:
                # Another process took a port between free_port() and the simulator's bind (tests
                # running in parallel): a new pair, when the pair was picked here. Never "quit" over
                # the control port: it may be another simulator's.
                self._close_control()
                self.process.kill()
                self.process.wait()
                self.process = None
                if not self.chosen or attempt == 2:
                    raise
                self.port = free_port()
                self.control_port = self.port + 1

    def control(self, line, timeout=5.0):
        """One control line on one kept connection (the board serves many lines per client), so a test
        polling status does not leave a closed socket per call (TIME_WAIT exhausts local ports)."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                if getattr(self, '_control', None) is None:
                    self._control = socket.create_connection(('127.0.0.1', self.control_port), timeout=timeout)
                    self._control_in = b''
                s = self._control
                s.settimeout(timeout)
                s.sendall(line.encode() + b'\n')
                while b'\n' not in self._control_in:
                    chunk = s.recv(4096)
                    if not chunk:
                        raise ConnectionResetError('control connection closed')
                    self._control_in += chunk
                reply, self._control_in = self._control_in.split(b'\n', 1)
                return json.loads(reply.decode())
            except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError):
                self._close_control()
                if line == 'quit' or time.monotonic() > deadline:
                    raise
                time.sleep(0.05)

    def _close_control(self):
        if getattr(self, '_control', None) is not None:
            self._control.close()
        self._control = None

    def status(self):
        return self.control('status')

    def set(self, name, value):
        reply = self.control('set %s %d' % (name, value))
        if not reply.get('ok'):
            raise ValueError('simulator: no fault %r' % name)

    def advance(self, ms):
        """Moves device time ms ahead (both processors' cycle counters and the board's clock), so a time-out
        passes without waiting for it. Virtual clock: in the simulator's own steps, each taken once both
        processors are idle (the link keeps alive as on the device: keepalives, answers and time-outs
        happen when they are due); returns once the clock is there and both processors are idle again.
        Real time: one jump."""
        if ms <= 0:
            return
        if not self.control('advance %d' % ms, timeout=60.0 + ms / 1000.0).get('ok'):
            raise ValueError('simulator: advance %r refused' % ms)

    def jump(self, ms):
        """Moves device time ms ahead at once, whatever the processors are doing (a pass that took that long)."""
        if not self.control('jump %d' % ms).get('ok'):
            raise ValueError('simulator: jump %r refused' % ms)

    def reset(self, which):
        """Resets processor 'a' or 'b' alone (no handler runs, the other keeps running); after the
        board's reset delay its stock bootloader stand-in runs (status a_bootloader/b_bootloader)."""
        reply = self.control('reset %s' % which)
        if not reply.get('ok'):
            raise RuntimeError('simulator: %s' % reply.get('error'))

    def inject(self, cls, op, argument=0, argument2=0, body=b''):
        """Sends one raw stock request to processor A, as if from B but past B's checks (the
        board's own link end); returns its tag for injected()."""
        line = 'inject %x %x %x %x' % (cls, op, argument, argument2) + (' ' + body.hex() if body else '')
        reply = self.control(line)
        if not reply.get('ok'):
            raise RuntimeError('simulator: %s' % reply.get('error'))
        return reply['tag']

    def injected(self, tag):
        """A's answer to an injected request: {done, result (signed), error (errno), body (bytes)}."""
        return self.control('injected %d' % tag)

    def ask_a(self, cls, op, argument=0, argument2=0, body=b'', timeout=3.0):
        """inject() and wait for A's answer: (result, errno), or None if A did not answer in time."""
        tag = self.inject(cls, op, argument, argument2, body)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            reply = self.injected(tag)
            if reply['done']:
                return reply['result'], reply['error']
            time.sleep(0.01)
        return None

    def wait_listening(self, timeout=10.0):
        """Until the USB endpoint accepts a connection (the service has USB on)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process and self.process.poll() is not None:     # the control port's bind failed, say
                raise PortTaken('simulator exited with %s' % self.process.returncode)
            try:
                status = self.status()
                if status.get('pid') != self.process.pid:      # another simulator's control port: ours failed
                    raise PortTaken('another simulator (pid %s) answers on port %d' % (status.get('pid'),
                                                                                     self.control_port))
                if status.get('listening'):
                    return
                if status.get('b') == 'crashed' and status.get('b_boots') == 1:   # the endpoint's bind failed, say
                    raise PortTaken('simulator: B crashed at its first boot')
            except OSError:
                pass
            time.sleep(0.01)
        raise TimeoutError('simulator endpoint not listening within %g s' % timeout)

    def stop(self):
        if not self.process:
            return
        try:
            self.control('quit', timeout=2.0)
            self.process.wait(timeout=5.0)
        except Exception:
            self.process.kill()
            self.process.wait()
        self._close_control()
        self.process = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Run the XY Forever device simulator in the foreground.')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help='USB endpoint port (control: port + 1)')
    parser.add_argument('--state', type=Path, default=STATE,
                        help='folder for yaffs2/ (seeded when missing), bootpart.bin and builds (default %(default)s)')
    parser.add_argument('--plain', action='store_true', help='power on without PLAY (boots the default: no USB)')
    parser.add_argument('--version', help='hello version= (default: this checkout\'s release version)')
    parser.add_argument('--pre-v1', action='store_true', help='serve the pre-v1 hello text (builds 153 and earlier)')
    parser.add_argument('--crash-trigger', action='store_true',
                        help='the crash trigger test build (#102): diag.crash halts B (no fault, no screen)')
    parser.add_argument('--virtual-clock', action='store_true',
                        help='device time moves only under the simulator\'s control, as in the tests (default: real time)')
    parser.add_argument('--no-otp', action='store_true',
                        help="processor B's OTP answers nothing (default: the private keys, when present)")
    args = parser.parse_args(argv)
    why = available()
    if why:
        print('simulator unavailable: %s' % why, file=sys.stderr)
        return 2
    state = paths.private_output(args.state)
    root = state / 'yaffs2'
    if not root.exists():
        seed(root)
    hello = 'XYF1;service=usb;role=b;proto=1;install=stock-88a06a7e' if args.pre_v1 else None
    print('building the simulator...', file=sys.stderr, flush=True)
    out = build(state / 'builds', version=args.version, hello=hello,
                mode='xyf-crash-trigger' if args.crash_trigger else 'xyf-product')
    otp = None if args.no_otp else otp_fields()
    print('OTP: %s' % ('the private keys' if otp else 'answers nothing (tfw.check: reason no_otp)'), file=sys.stderr)
    sim = Simulator(root, state / 'bootpart.bin', port=args.port, build_dir=out, play=not args.plain, otp=otp,
                    virtual=args.virtual_clock)
    print('simulator: USB endpoint 127.0.0.1:%d (python3 xyf_host.py --sim 127.0.0.1:%d status), control 127.0.0.1:%d,'
          ' storage %s; Ctrl-C stops it' % (sim.port, sim.port, sim.control_port, root), file=sys.stderr, flush=True)
    # Its own process group (the board and both processors), ended with this process however it ends:
    # Ctrl-C, SIGTERM or SIGHUP, or (tie_to_parent) the process that started this one gone (#133).
    process = procgroup.popen(sim.argv(), stdin=subprocess.PIPE if otp else None)
    try:
        if otp:
            process.stdin.write(otp)
            process.stdin.close()
        return process.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        procgroup.kill_group(process)


if __name__ == '__main__':
    procgroup.tie_to_parent()
    sys.exit(main())
