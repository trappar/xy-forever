"""The file engine's path policy and request decision (target/file_engine.cpp,
docs/architecture/link.md), compiled for the host: every operation against every
protected path and its spellings, the limits, the request classes; and the wiring that makes A
apply it to every request and B before it sends. No device execution."""
import ctypes as C
import itertools
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever.source_text import unit_text  # noqa: E402
from xy_forever import hostbuild  # noqa: E402
from xy_forever.census import ENGINE_ACTIONS   # noqa: E402

PARTITION, PATH, QUERY, READ, SECTOR, WRITE = 0x11907694, 0x119076b4, 0x11907388, 0x11907368, 0x11907348, 0x11907710
BUFFER = 0x8803f6e0
OPS = dict(read=1, list=2, stat=3, write=4, stream=5, mkdir=6, unlink=7, rmdir=8, rename=9, raw=10)
VERDICTS = ['allow', 'refuse_path', 'refuse_outside', 'refuse_protected', 'refuse_tmp', 'refuse_raw',
            'refuse_operation', 'refuse_depth']
MAX_PATH = 240                                          # bytes with the NUL
TMP = '/yaffs2/xy-forever/tmp/'

ROOTS = ['/yaffs2', '/yaffs2/', '/yaffs2//', '/yaffs2///']
MARKERS = ['/yaffs2/bootloader.dev', '/yaffs2//bootloader.dev', '/yaffs2/bootloader.dev/', '/yaffs2///bootloader.dev//',
           '/yaffs2/BOOTLOADER.DEV', '/yaffs2/Bootloader.Dev', '/yaffs2/bOoTlOaDeR.dEv/']
# A name that merely starts (or ends) like a protected one, or the marker's name in another folder.
NEAR = ['/yaffs2/bootloader.dev.bak', '/yaffs2/bootloader.de', '/yaffs2/xbootloader.dev', '/yaffs2/a/bootloader.dev',
        '/yaffs2/bootloader.dev/x', '/yaffs2/bootloader.dev ', '/yaffs2/.bootloader.dev', '/yaffs2/bootloader.dev..',
        '/yaffs2/xy-forever', '/yaffs2/xy-forever/state-a.txt', '/yaffs2/xy-forever/firmware/opxy-121/os.ldr',
        '/yaffs2/.hidden', '/yaffs2/...', '/yaffs2/a..b', '/yaffs2/bootloader.d\xe9v', '/yaffs2/\xe9t\xe9/♫.xy',
        '/yaffs2/projects/song.xy', '/yaffs2//projects///song.xy/', '/yaffs2/upgrades', '/yaffs2/upgrade.tfw',
        '/yaffs2/a/upgrade', '/yaffs2/a/upgrade/x.tfw', '/yaffs2/user/song.xy', '/yaffs2/xbootloader.devx/y']
# The stock updater's inbox and everything below it (the write rule): read only, any ASCII case.
INBOX = ['/yaffs2/upgrade', '/yaffs2/upgrade/', '/yaffs2//upgrade//', '/yaffs2/UPGRADE/x.tfw', '/yaffs2/Upgrade/a/b',
         '/yaffs2/upgrade/bootloader.dev']
DOTS = ['/yaffs2/.', '/yaffs2/..', '/yaffs2/./bootloader.dev', '/yaffs2/a/../bootloader.dev',
        '/yaffs2/bootloader.dev/.', '/yaffs2/bootloader.dev/..', '/yaffs2/xy-forever/..',
        '/yaffs2/../yaffs2/bootloader.dev', '/yaffs2//./', '/yaffs2/./']
OUTSIDE = ['', '/', '/yaffs', '/yaffs20', '/yaffs20/bootloader.dev', '/yaffs2bootloader.dev', 'yaffs2/bootloader.dev',
           '/fat32', '/fat32/bootloader.dev', '/YAFFS2/bootloader.dev', '/Yaffs2', '//yaffs2/bootloader.dev',
           'bootloader.dev', ' /yaffs2/bootloader.dev', '/yaffs2\xe9/x']
CONTROL = [bytes([c]) for c in list(range(1, 0x20)) + [0x7f]]


def raw(path):
    return path.encode('utf-8') if isinstance(path, str) else path


class FileEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-engine-')
        lib = Path(cls.tmp.name) / 'engine.dylib'
        hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-O1', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                       '-ffreestanding', '-fno-builtin', '-fno-exceptions', '-fno-rtti', '-I', str(ROOT / 'target'),
                       str(ROOT / 'target/file_engine.cpp'), str(ROOT / 'tests/file_engine_bridge.cpp'),
                       '-o', str(lib)], check=True, capture_output=True)
        cls.lib = C.CDLL(str(lib))
        cls.lib.engine_decide.argtypes = [C.c_uint, C.c_char_p, C.c_char_p]
        cls.lib.engine_decide.restype = C.c_uint
        cls.lib.engine_request.argtypes = [C.c_uint] * 4 + [C.c_char_p] + [C.c_int] * 8
        cls.lib.engine_request.restype = C.c_uint

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def decide(self, op, path, second=None):
        """path / second: str, bytes (as the C string up to its first NUL), or None (no pointer)."""
        buffers = [None if p is None else C.create_string_buffer(raw(p), len(raw(p)) + 1) for p in (path, second)]
        return VERDICTS[self.lib.engine_decide(OPS[op], *[None if b is None else C.cast(b, C.c_char_p)
                                                            for b in buffers])]

    def request(self, vt, op, argument=0, pointer=0, path=None, on=1, session=1, window=1, fd_known=1, fd_space=1,
                dir_known=1, dir_space=1, partition=0):
        value = self.lib.engine_request(vt, op, argument, pointer, None if path is None else raw(path), on, session,
                                        window, fd_known, fd_space, dir_known, dir_space, partition)
        return ENGINE_ACTIONS[value - 0x60]

    # -- the path policy ------------------------------------------------------------------------
    def expected(self, op, kind):
        """What the policy says for an operation on a path of a kind."""
        if op == 'raw':
            return 'refuse_raw'
        if kind in ('outside', 'path'):
            return 'refuse_' + kind
        if op in ('unlink', 'rmdir', 'rename') and kind in ('root', 'marker', 'inbox'):
            return 'refuse_protected'
        if op in ('write', 'mkdir') and kind in ('marker', 'inbox'):      # the write rule
            return 'refuse_protected'
        if op == 'stream':
            return 'allow' if kind == 'tmp' else 'refuse_tmp'
        return 'allow'

    def test_every_operation_on_every_spelling(self):
        kinds = ([(p, 'root') for p in ROOTS] + [(p, 'marker') for p in MARKERS] + [(p, 'near') for p in NEAR] +
                 [(p, 'inbox') for p in INBOX] +
                 [(p, 'path') for p in DOTS] + [(p, 'outside') for p in OUTSIDE] +
                 [(TMP + 'a.part', 'tmp'), (TMP + 'sub//b/', 'tmp')])
        for (path, kind), op in itertools.product(kinds, OPS):
            with self.subTest(op=op, path=path):
                if op == 'rename':
                    self.assertEqual(self.decide(op, path, '/yaffs2/renamed'), self.expected(op, kind))
                else:
                    self.assertEqual(self.decide(op, path), self.expected(op, kind))

    def test_the_marker_and_the_inbox_are_read_only_the_root_may_be_made(self):
        for path in MARKERS + INBOX:                            # the write rule: all but those two
            for op in ('read', 'list', 'stat'):
                self.assertEqual(self.decide(op, path), 'allow', (op, path))
            for op in ('write', 'mkdir', 'unlink', 'rmdir'):
                self.assertEqual(self.decide(op, path), 'refuse_protected', (op, path))
            self.assertEqual(self.decide('rename', '/yaffs2/new-name', path), 'refuse_protected', path)   # onto it
            self.assertEqual(self.decide('rename', path, '/yaffs2/new-name'), 'refuse_protected', path)
        for path in ROOTS:
            for op in ('read', 'list', 'stat', 'mkdir'):
                self.assertEqual(self.decide(op, path), 'allow', (op, path))

    def test_rename(self):
        for target in ROOTS:                                    # replacing the root = removing it
            self.assertEqual(self.decide('rename', '/yaffs2/a', target), 'refuse_protected', target)
        for source in ROOTS + MARKERS:
            self.assertEqual(self.decide('rename', source, '/yaffs2/b'), 'refuse_protected', source)
        for target in OUTSIDE:
            self.assertEqual(self.decide('rename', '/yaffs2/a', target), 'refuse_outside', target)
        for target in DOTS:
            self.assertEqual(self.decide('rename', '/yaffs2/a', target), 'refuse_path', target)
        self.assertEqual(self.decide('rename', '/yaffs2/a', None), 'refuse_path')
        self.assertEqual(self.decide('rename', '/yaffs2/a', '/yaffs2/a\x01'), 'refuse_path')
        for path in NEAR:
            self.assertEqual(self.decide('rename', path, '/yaffs2/x/y'), 'allow', path)
            self.assertEqual(self.decide('rename', '/yaffs2/x', path), 'allow', path)

    def test_operation_values_fail_closed(self):
        for value in (0, 11, 12, 0x60, 0xffffffff):
            self.assertEqual(VERDICTS[self.lib.engine_decide(value, b'/yaffs2/a', None)], 'refuse_operation', value)
        for op in OPS:                                          # a second path only for rename
            if op not in ('rename', 'raw'):
                self.assertEqual(self.decide(op, '/yaffs2/a', '/yaffs2/b'), 'refuse_operation', op)
        self.assertEqual(self.decide('raw', None), 'refuse_raw')
        for op in OPS:
            if op != 'raw':
                self.assertEqual(self.decide(op, None), 'refuse_path', op)

    def test_length_limit(self):
        self.assertEqual(self.lib.engine_max_path(), MAX_PATH)
        longest = '/yaffs2/' + 'a' * (MAX_PATH - 1 - len('/yaffs2/'))
        self.assertEqual(len(longest), MAX_PATH - 1)
        marker = '/yaffs2/bootloader.dev' + '/' * (MAX_PATH - 1 - len('/yaffs2/bootloader.dev'))
        self.assertEqual(len(marker), MAX_PATH - 1)
        for op in OPS:
            if op in ('raw', 'rename'):
                continue
            self.assertEqual(self.decide(op, longest), self.expected(op, 'near'), op)
            self.assertEqual(self.decide(op, longest + 'a'), 'refuse_path', op)              # one byte more
            self.assertEqual(self.decide(op, marker), self.expected(op, 'marker'), op)
            self.assertEqual(self.decide(op, marker + '/'), 'refuse_path', op)
            self.assertEqual(self.decide(op, longest + 'a' * 300), 'refuse_path', op)
        self.assertEqual(self.decide('rename', '/yaffs2/a', longest), 'allow')
        self.assertEqual(self.decide('rename', '/yaffs2/a', longest + 'a'), 'refuse_path')
        self.assertEqual(self.decide('stream', TMP + 'b' * (MAX_PATH - 1 - len(TMP))), 'allow')
        self.assertEqual(self.decide('stream', TMP + 'b' * (MAX_PATH - len(TMP))), 'refuse_path')

    def test_control_bytes_and_embedded_nul(self):
        for c in CONTROL:
            for path in (b'/yaffs2/bootloader.dev' + c, b'/yaffs2/a' + c + b'b', c + b'/yaffs2/a', b'/yaffs2/' + c,
                         b'/yaffs2' + c, b'/yaffs2/xy-forever/tmp/' + c):
                for op in OPS:
                    if op == 'raw':
                        continue
                    with self.subTest(op=op, path=path):
                        second = b'/yaffs2/x' if op == 'rename' else None
                        self.assertEqual(self.decide(op, path, second), 'refuse_path')
        # A C string ends at its first NUL: what follows is never part of the path.
        self.assertEqual(self.decide('unlink', b'/yaffs2/bootloader.dev\0.bak'), 'refuse_protected')
        self.assertEqual(self.decide('unlink', b'/yaffs2/bootloader.dev.bak\0'), 'allow')
        self.assertEqual(self.decide('unlink', b'/yaffs2/a\0/../bootloader.dev'), 'allow')
        self.assertEqual(self.decide('rmdir', b'/yaffs2\0/x'), 'refuse_protected')
        self.assertEqual(self.decide('read', b'\0/yaffs2/a'), 'refuse_outside')

    def test_non_ascii_bytes_pass(self):
        for byte in range(0x80, 0x100):
            for op in ('read', 'write', 'unlink', 'mkdir'):
                self.assertEqual(self.decide(op, b'/yaffs2/x' + bytes([byte])), 'allow', (op, byte))
        self.assertEqual(self.decide('unlink', b'/yaffs2/bootloader.dev\xc2\xa0'), 'allow')   # another name
        self.assertEqual(self.decide('unlink', b'/yaffs2/BOOTLOADER.D\xc9V'), 'allow')        # no Latin-1 folding

    def test_tmp_folder(self):
        for path in (TMP + 'a', TMP + 'a/b/c.part', TMP + '/a', '/yaffs2//xy-forever//tmp//a//', TMP + '\xe9'):
            self.assertEqual(self.decide('stream', path), 'allow', path)
        for path in ('/yaffs2/xy-forever/tmp', TMP, '/yaffs2/xy-forever/tmpx/a', '/yaffs2/xy-forever/TMP/a',
                     '/yaffs2/XY-FOREVER/tmp/a', '/yaffs2/tmp/a', '/yaffs2/x/xy-forever/tmp/a', '/yaffs2/xy-forever/a',
                     '/yaffs2/bootloader.dev'):
            self.assertEqual(self.decide('stream', path), 'refuse_tmp', path)
        self.assertEqual(self.decide('stream', TMP + '../a'), 'refuse_path')
        self.assertEqual(self.decide('write', '/yaffs2/projects/x.xy'), 'allow')    # direct writes: not yet tmp-only

    def test_depth_rule(self):
        """#124: no folder made more than 6 folders below /yaffs2, no file written, streamed or renamed into one
        (the stock dev-menu MTP's limit); reads, listing, stat and removals at any depth; A sees bad_path."""
        def deep(n, root='/yaffs2'):
            return root + ''.join('/d%d' % i for i in range(n))
        for spelled in (lambda p: p, lambda p: p.replace('/d', '//d') + '/'):
            self.assertEqual(self.decide('mkdir', spelled(deep(6))), 'allow')
            self.assertEqual(self.decide('mkdir', spelled(deep(7))), 'refuse_depth')
            self.assertEqual(self.decide('write', spelled(deep(7))), 'allow')            # a file in the 6th folder
            self.assertEqual(self.decide('write', spelled(deep(8))), 'refuse_depth')
        self.assertEqual(self.decide('stream', deep(4, TMP + 'x')), 'allow')           # 7 names
        self.assertEqual(self.decide('stream', deep(5, TMP + 'x')), 'refuse_depth')
        for op in ('read', 'list', 'stat', 'unlink', 'rmdir'):
            self.assertEqual(self.decide(op, deep(30)), 'allow', op)
        self.assertEqual(self.decide('rename', deep(3), deep(7)), 'allow')
        self.assertEqual(self.decide('rename', deep(3), deep(8)), 'refuse_depth')
        self.assertEqual(self.decide('rename', deep(8), deep(3)), 'refuse_depth')      # A's rename_from: the source too
        self.assertEqual(self.decide('mkdir', '/yaffs2/upgrade' + deep(9, '')), 'refuse_protected')
        self.lib.engine_depth.argtypes = [C.c_char_p]
        self.lib.engine_depth.restype = C.c_uint
        self.assertEqual([self.lib.engine_depth(p) for p in (b'/yaffs2', b'/yaffs2//a/b/', b'/yaffs2/a/../b', b'/x')],
                         [0, 2, 0xffffffff, 0xffffffff])
        PATH_OPS = dict(open=(0x29, 0x8301), mkdir=(0x26, 0))
        self.assertEqual(self.request(PATH, *PATH_OPS['open'], BUFFER, deep(8)), 'refuse_bad_path')
        self.assertEqual(self.request(PATH, *PATH_OPS['mkdir'], BUFFER, deep(7)), 'refuse_bad_path')
        self.assertEqual(self.request(PATH, 0x29, 0, BUFFER, deep(20)), 'run_open')
        self.assertEqual(self.request(PATH, 0x25, 0, BUFFER, deep(20)), 'run_path')

    def test_header_enums_match(self):
        text = (ROOT / 'target/file_engine.hpp').read_text()
        ops = text[text.index('enum Operation'):text.index('};', text.index('enum Operation'))]
        self.assertEqual(re.findall(r'\bop_([a-z]+)', ops), list(OPS))
        verdicts = text[text.index('enum Verdict'):text.index('};', text.index('enum Verdict'))]
        self.assertEqual(re.findall(r'^\s+(allow|refuse_[a-z]+)', verdicts, re.M), VERDICTS)
        actions = text[text.index('enum Action'):text.index('};', text.index('enum Action'))]
        actions = re.sub(r'//[^\n]*', '', actions)
        self.assertEqual(re.findall(r'\b((?:refuse|run)_[a-z_]+)\b', actions), ENGINE_ACTIONS)

    # -- A's request decision -------------------------------------------------------------------
    def test_requests_run(self):
        ok = '/yaffs2/projects/song.xy'
        self.assertEqual(self.request(PARTITION, 8, on=1, session=0, window=0), 'run_mount')
        self.assertEqual(self.request(PARTITION, 9, session=0), 'run_unmount')
        for op in (0x2c, 0x2d):
            self.assertEqual(self.request(PARTITION, op), 'run_file', hex(op))
        self.assertEqual(self.request(PARTITION, 0x30), 'run_closedir')
        self.assertEqual(self.request(PATH, 0x29, 0, BUFFER, ok), 'run_open')
        self.assertEqual(self.request(PATH, 0x29, 0x8301, BUFFER, ok), 'run_open')
        self.assertEqual(self.request(PATH, 0x2e, 0, BUFFER, '/yaffs2/'), 'run_opendir')
        for op in (0x21, 0x25, 0x26, 0x27):
            self.assertEqual(self.request(PATH, op, 0, BUFFER, ok), 'run_path', hex(op))
        for op in (0x22, 0x23, 0x31):
            self.assertEqual(self.request(QUERY, op, 0, BUFFER, ok), 'run_query', hex(op))
        self.assertEqual(self.request(READ, 0x2a), 'run_read')
        self.assertEqual(self.request(SECTOR, 0x2f), 'run_readdir')
        self.assertEqual(self.request(WRITE, 0x2b), 'run_write')

    def test_rename_pair(self):
        """Path 0x21 with rename_from keeps a source (checked as one: never /yaffs2 or the marker);
        rename_to needs one and decides the pair (never onto /yaffs2) and is a change (the window)."""
        RNFR, RNTO = 0x52464e52, 0x4f544e52
        ok, other = '/yaffs2/xy-forever/a.txt', '/yaffs2/xy-forever/b.txt'
        self.assertEqual(self.request(PATH, 0x21, RNFR, BUFFER, ok, window=0), 'run_rename_from')
        for path in MARKERS + ROOTS:
            self.assertEqual(self.request(PATH, 0x21, RNFR, BUFFER, path), 'refuse_protected_path', path)
        self.assertEqual(self.request(PATH, 0x21, RNFR, BUFFER, '/fat32/a'), 'refuse_outside_path')
        self.assertEqual(self.request(PATH, 0x21, RNFR, 0x1000, ok), 'refuse_bad_path')
        lib = self.lib
        lib.engine_rename_to.argtypes = [C.c_char_p, C.c_char_p, C.c_int]
        lib.engine_rename_to.restype = C.c_uint

        def to(source, target, window=1):
            return ENGINE_ACTIONS[lib.engine_rename_to(None if source is None else raw(source), raw(target), window) - 0x60]
        self.assertEqual(to(ok, other), 'run_rename_to')
        self.assertEqual(to(ok, other, window=0), 'refuse_read_only')
        self.assertEqual(to(None, other), 'refuse_op')                       # no rename_from just before
        # #4: rename_over only from an upload's temporary file (A then checks its size against the name)
        lib.engine_rename_over.argtypes = [C.c_char_p, C.c_char_p, C.c_int]
        lib.engine_rename_over.restype = C.c_uint
        upload = '/yaffs2/xy-forever/tmp/put-0000000a-deadbeef'

        def over(source, target, window=1):
            return ENGINE_ACTIONS[lib.engine_rename_over(None if source is None else raw(source), raw(target), window) - 0x60]
        self.assertEqual(over(upload, other), 'run_rename_over')
        self.assertEqual(over(upload, other, window=0), 'refuse_read_only')
        self.assertEqual(over(None, other), 'refuse_op')
        for source in (ok, upload + 'x', upload.replace('put-', 'pot-'), upload.upper(), '/yaffs2/tmp/put-0000000a-deadbeef'):
            self.assertEqual(over(source, other), 'refuse_tmp_path', source)
        for path in MARKERS + INBOX + ROOTS:
            self.assertEqual(over(upload, path), 'refuse_protected_path', path)
        for path in ROOTS:
            self.assertEqual(to(ok, path), 'refuse_protected_path', path)
        for path in MARKERS + INBOX:
            self.assertEqual(to(path, other), 'refuse_protected_path', path)
            self.assertEqual(to(ok, path), 'refuse_protected_path', path)     # onto the marker, into the inbox
        self.assertEqual(to(ok, '/yaffs2/../x'), 'refuse_bad_path')
        self.assertEqual(self.request(PATH, 0x21, 4, BUFFER, ok), 'run_path')     # a plain access

    def test_not_exposed_and_unknown(self):
        for op in (0x34, 0x36, 0x00, 0x01, 0x29, 0x2a, 0x2b, 0xff):
            self.assertEqual(self.request(PARTITION, op), 'refuse_op', hex(op))
        for op in (0x28, 0x35, 0x22, 0x2a, 0x2f, 0x00, 0xff):
            self.assertEqual(self.request(PATH, op, 0, BUFFER, '/yaffs2/a'), 'refuse_op', hex(op))
        for op in (0x21, 0x29, 0x2a, 0xff):
            self.assertEqual(self.request(QUERY, op, 0, BUFFER, '/yaffs2/a'), 'refuse_op', hex(op))
        for vt, ops in ((READ, (0x2b, 0x29, 0x11)), (SECTOR, (0x2a, 0x2b, 0x30)), (WRITE, (0x2a, 0x2f, 0x29))):
            for op in ops:
                self.assertEqual(self.request(vt, op), 'refuse_op', (hex(vt), hex(op)))
        for vt in (0, 1, 0x119075e8, 0xffffffff):
            self.assertEqual(self.request(vt, 0x2a), 'refuse_class', hex(vt))

    def test_raw_layer_is_refused_in_every_state(self):
        raw_ops = [(PARTITION, 4), (PARTITION, 0x14), (PARTITION, 0x18), (SECTOR, 0x11), (SECTOR, 0x12), (WRITE, 0x15),
                   (WRITE, 0x16)]
        for (vt, op), state in itertools.product(raw_ops, itertools.product((0, 1), repeat=8)):
            self.assertTrue(self.lib.engine_raw(vt, op))
            self.assertEqual(self.request(vt, op, 1, BUFFER, '/yaffs2/a', *state), 'refuse_raw_layer', (hex(op), state))
        for vt, op in ((PARTITION, 8), (PARTITION, 9), (PARTITION, 0x2c), (SECTOR, 0x2f), (WRITE, 0x2b), (PATH, 0x29),
                       (READ, 0x2a), (QUERY, 0x22), (PATH, 0x14), (READ, 0x11), (QUERY, 0x15)):
            self.assertFalse(self.lib.engine_raw(vt, op), (hex(vt), hex(op)))

    def test_brick_paths_are_refused_in_every_state(self):
        for path, (op, kind) in itertools.product(ROOTS + MARKERS, ((0x25, 'unlink'), (0x27, 'rmdir'))):
            for state in itertools.product((0, 1), repeat=6):
                self.assertEqual(self.request(PATH, op, 0, BUFFER, path, 1, *state), 'refuse_protected_path',
                                 (path, kind, state))
            self.assertEqual(self.request(PATH, op, 0, BUFFER, path, on=0), 'refuse_off')
        for path in MARKERS + INBOX:                            # never written or made (write rule); read as any file
            self.assertEqual(self.request(PATH, 0x29, 0x8301, BUFFER, path), 'refuse_protected_path', path)
            self.assertEqual(self.request(PATH, 0x26, 0, BUFFER, path), 'refuse_protected_path', path)
            self.assertEqual(self.request(PATH, 0x29, 0, BUFFER, path), 'run_open', path)

    def test_paths_and_the_stock_buffer(self):
        for vt, op in ((PATH, 0x29), (PATH, 0x25), (PATH, 0x2e), (QUERY, 0x22)):
            self.assertEqual(self.request(vt, op, 0, BUFFER + 4, '/yaffs2/a'), 'refuse_bad_path')
            self.assertEqual(self.request(vt, op, 0, 0, None), 'refuse_bad_path')
            self.assertEqual(self.request(vt, op, 0, BUFFER, None), 'refuse_bad_path')
            self.assertEqual(self.request(vt, op, 0, BUFFER, '/fat32/a'), 'refuse_outside_path')
            self.assertEqual(self.request(vt, op, 0, BUFFER, '/yaffs2/../a'), 'refuse_bad_path')
            self.assertEqual(self.request(vt, op, 0, BUFFER, '/yaffs2/a\x1b'), 'refuse_bad_path')
            self.assertEqual(self.request(vt, op, 0, BUFFER, '/yaffs2/' + 'a' * 232), 'refuse_bad_path')

    def test_session_window_and_handles(self):
        ok = '/yaffs2/a'
        for vt, op in ((PARTITION, 0x2d), (PARTITION, 0x30), (READ, 0x2a), (SECTOR, 0x2f), (WRITE, 0x2b)):
            self.assertEqual(self.request(vt, op, session=0), 'refuse_mount', hex(op))
        for vt, op in ((PATH, 0x29), (PATH, 0x21), (PATH, 0x25), (QUERY, 0x22)):
            self.assertEqual(self.request(vt, op, 0, BUFFER, ok, session=0), 'refuse_mount', hex(op))
        # Changes need the read-write window; reads do not.
        for vt, op, flags in ((PATH, 0x29, 0x8301), (PATH, 0x29, 1), (PATH, 0x29, 0x200), (PATH, 0x25, 0),
                              (PATH, 0x26, 0), (PATH, 0x27, 0), (WRITE, 0x2b, 0)):
            self.assertEqual(self.request(vt, op, flags, BUFFER, ok, window=0), 'refuse_read_only', (hex(op), flags))
        for vt, op in ((PATH, 0x29), (PATH, 0x21), (PATH, 0x2e), (QUERY, 0x23)):
            self.assertTrue(self.request(vt, op, 0, BUFFER, ok, window=0).startswith('run_'), hex(op))
        for vt, op in ((READ, 0x2a), (PARTITION, 0x2c), (PARTITION, 0x2d)):
            self.assertEqual(self.request(vt, op, window=0), 'run_' + ('read' if vt == READ else 'file'))
        # Handles: only those a opened for b.
        for vt, op in ((PARTITION, 0x2c), (PARTITION, 0x2d), (READ, 0x2a), (WRITE, 0x2b)):
            self.assertEqual(self.request(vt, op, fd_known=0), 'refuse_handle', hex(op))
            self.assertNotEqual(self.request(vt, op, dir_known=0), 'refuse_handle', hex(op))
        for vt, op in ((PARTITION, 0x30), (SECTOR, 0x2f)):
            self.assertEqual(self.request(vt, op, dir_known=0), 'refuse_handle', hex(op))
            self.assertNotEqual(self.request(vt, op, fd_known=0), 'refuse_handle', hex(op))
        self.assertEqual(self.request(PATH, 0x29, 0, BUFFER, ok, fd_space=0), 'refuse_full')
        self.assertEqual(self.request(PATH, 0x2e, 0, BUFFER, ok, dir_space=0), 'refuse_full')
        self.assertEqual(self.request(PATH, 0x26, 0, BUFFER, ok, fd_space=0, dir_space=0), 'run_path')
        # Off: everything but the raw layer is refuse_off, before the class is looked at.
        for vt, op in ((PARTITION, 8), (PARTITION, 9), (READ, 0x2a), (0x119075e8, 1)):
            self.assertEqual(self.request(vt, op, on=0), 'refuse_off')

    def test_a_selected_boot_partition_refuses_every_request(self):
        """Install mode selected a boot partition: yaffs is not what the chip addresses, so every
        request is refused in every state (mount, unmount, reads and changes alike); the raw layer
        keeps its own refusal (the run entry's sign that b's bootloader asks)."""
        requests = [(PARTITION, 8, 0, 0, None), (PARTITION, 9, 0, 0, None), (PARTITION, 0x2d, 0, 0, None),
                    (PARTITION, 0x30, 0, 0, None), (PATH, 0x29, 0, BUFFER, '/yaffs2/a'),
                    (PATH, 0x29, 0x8301, BUFFER, '/yaffs2/a'), (PATH, 0x26, 0, BUFFER, '/yaffs2/a'),
                    (PATH, 0x25, 0, BUFFER, '/yaffs2/bootloader.dev'), (PATH, 0x2e, 0, BUFFER, '/yaffs2'),
                    (QUERY, 0x22, 0, BUFFER, '/yaffs2/a'), (READ, 0x2a, 0, 0, None), (SECTOR, 0x2f, 0, 0, None),
                    (WRITE, 0x2b, 0, 0, None), (PATH, 0x29, 0, BUFFER, '/fat32/a'), (0x119075e8, 1, 0, 0, None)]
        for (vt, op, argument, pointer, path), state in itertools.product(requests, itertools.product((0, 1), repeat=7)):
            self.assertEqual(self.request(vt, op, argument, pointer, path, *state, partition=1), 'refuse_partition',
                             (hex(vt), hex(op), state))
        self.assertEqual(self.request(PARTITION, 0x18, 0, partition=1), 'refuse_raw_layer')
        self.assertEqual(self.request(PARTITION, 8, partition=0), 'run_mount')

    def test_order_policy_before_state(self):
        """A refused path is refused whatever the state (the policy is not a mount question)."""
        for state in itertools.product((0, 1), repeat=6):
            self.assertEqual(self.request(PATH, 0x29, 0, BUFFER, '/fat32/x', 1, *state), 'refuse_outside_path')


    def test_upload_name_and_size(self):
        """#4: an upload's temporary file name, tmp/put-<size>-<tag> (8 lowercase hex digits each)."""
        lib = self.lib
        out = C.create_string_buffer(64)
        lib.engine_upload_name(out, 0x12345, 0xdeadbeef)
        self.assertEqual(out.value, b'/yaffs2/xy-forever/tmp/put-00012345-deadbeef')
        size = C.c_uint()
        self.assertTrue(lib.engine_upload_size(out.value, C.byref(size)))
        self.assertEqual(size.value, 0x12345)
        for bad in (b'/yaffs2/xy-forever/tmp/put-0001234-deadbeef', b'/yaffs2/xy-forever/tmp/put-00012345_deadbeef',
                    b'/yaffs2/xy-forever/tmp/put-0001234G-deadbeef', b'/yaffs2/xy-forever/tmp/put-00012345-deadbeef/x'):
            self.assertFalse(lib.engine_upload_size(bad, C.byref(size)), bad)


class FileEngineWiringTest(unittest.TestCase):
    """A applies the engine to every request; B applies its policy before it sends."""

    def setUp(self):
        self.server = ''.join((ROOT / 'target' / n).read_text() for n in ('storage_server.cpp', 'storage_server_marker.ipp'))
        self.usb = unit_text(ROOT / 'target/usb_service.cpp')

    def body(self, text, start):
        b = text[text.index(start):]
        return b[:b.index('\n}\n')]

    def test_rename_is_gated_and_checked_on_both_processors(self):
        """A calls the approved yaffs rename (889fa834) only from a_side::rename_object, only while
        every engine span matches (they cover 889fa604 and 88a01afa), after the pair's checks, the
        source's existence and the target's absence; B checks the pair before the confirmation and
        before anything reaches A."""
        gate = self.body(self.server, 'int rename_object(const char* from, const char* to) {')
        self.assertLess(gate.index('if (!spans_match()) return -2;'), gate.index('stock<0x889fa834u, int>(from, to)'))
        timing = (ROOT / 'target/storage_server_timing.ipp').read_text()
        match = self.body(timing, 'bool spans_match() {')                 # #144: a pass kept 1 s, a mismatch never
        self.assertIn('if (spans_matched && now - spans_matched_at < uint64_t(spans_recheck_ms) * 400000u) return true;',
                      match)
        self.assertIn('spans_matched = engine_identity() == 0 ? 1u : 0u;', match)
        self.assertEqual(self.server.count('0x889fa834u'), 1)
        spans = (ROOT / 'target/mtp_spans.hpp').read_text()
        table = [(int(a, 16), int(n, 16)) for a, n in re.findall(r'\{(0x[0-9a-f]+)u, (0x[0-9a-f]+)u', spans)]
        for address in (0x889fa834, 0x889fa604, 0x88a01afa):
            self.assertTrue(any(a <= address < a + n for a, n in table), hex(address))
        now = self.body(self.server, 'U32 rename_now(char* to, bool over) {')
        order = ['if (!valid_rename(from, to) || reaches_protected(rename_source) || reaches_protected(to)) return error_access;', 'if (!stat_into(from, marker_record[1])) return error_noent;',
                 'file_engine::upload_size(from, size)', 'marker_record[1][7] != size)) return error_inval;',
                 'if (stat_into(to, marker_record[1]) && (!over || (marker_record[1][2] & 0xf000u) != 0x8000u))',
                 'return over ? error_isdir : error_exist;', 'same_object()', 'a_side::rename_object(from, to)',
                 'restore_marker(0x21)']
        at = [now.index(o) for o in order]
        self.assertEqual(at, sorted(at))
        # #144: B skips the depth walk (#124) only for rename_over, whose source A takes only as a regular file (above)
        client = (ROOT / 'target/file_client.cpp').read_text()
        self.assertIn('const uint32_t deep = over ? U32(xyf::usbp::s_ok) : deeper_ok(from, to, mounted);', client)
        # The write rule: every change by hand, on A, also through links (reaches_protected: the marker, /yaffs2/upgrade)
        self.assertIn('(!write_root(path_copy) || reaches_protected(path_copy))', self.server)
        guard = self.body(self.server, '__attribute__((noinline)) bool reaches_protected(char* path) {')
        self.assertIn('stat_into(marker_path, marker_record[0])', guard)
        self.assertIn('stat_into(inbox_path, marker_record[0])', guard)
        usb = self.body(self.usb, 'void file_rename(uint8_t seq')
        order = ['code = P::s_bad_request;', 'storage_server::valid_rename(from, to)', 'sha_equal(', 'enter_writes()',
                 'file_client::rename(from, to, error, mounted)']
        at = [usb.index(o) for o in order]
        self.assertEqual(at, sorted(at))

    def test_a_decides_every_request_before_any_executor(self):
        serve = self.body(self.server, 'void serve(void* context')
        self.assertEqual(serve.count('serve_engine('), 1)
        self.assertTrue(serve.rstrip().endswith('serve_engine(request, vtable, op, storage, session);'))
        # Only install mode (the boot-partition writer's exact request set) is served otherwise.
        self.assertIn('if (install_enabled && engine != entry_write && engine != entry_run && serve_install(', serve)
        self.assertNotIn('stock<', serve)
        engine = self.body(self.server, 'void serve_engine(')
        first = engine.index('file_engine::decide_request(')
        self.assertLess(first, engine.index('stock<'))
        self.assertLess(first, engine.index('session.reply('))
        self.assertEqual(engine.count('file_engine::decide_request('), 1)
        # The raw layer in the run entry: no answer, nothing runs, a follows b's restart.
        peer = engine[engine.index('if (action == file_engine::refuse_raw_layer && engine == entry_run) {'):]
        peer = peer[:peer.index('return;')]
        self.assertIn('session.follow(request);', peer)
        self.assertNotIn('reply', peer)
        self.assertNotIn('stock<', peer)
        # F1: outside install mode a boot request is b's bootloader in every entry: no answer either.
        boot = serve[serve.index('if (!install_mode && peer_services::boot_request('):]
        boot = boot[:boot.index('return;')]
        self.assertIn('session.follow(request);', boot)
        self.assertNotIn('reply', boot)
        self.assertLess(serve.index('if (!install_mode && peer_services::boot_request('), serve.index('serve_install('))
        # the exemption holds only while the writer is active or a boot partition is selected.
        self.assertIn('const bool install_mode = install_enabled && engine != entry_write && engine != entry_run &&\n'
                      '        ((session.last_command() >> 8 & 0xffu) == install_item || boot_selected());', serve)
        run = self.body(self.server, 'void run_engine(Stock121PeerSession& session')
        self.assertIn('if (session.follow_now(xyf_boot::clock_ms())) break;', run)
        tail = run[run.rindex('close_engine(storage);'):]
        self.assertIn('usb_service::reset_self();', tail)
        # Each run action has exactly its executor; refusals never reach one.
        for action, executor in (('run_file', '0x889f76cau'), ('run_closedir', '0x889f76cau'),
                                 ('run_open', '0x889f6feau'), ('run_opendir', '0x889f6feau'),
                                 ('run_path', '0x889f6feau'), ('run_query', '0x889f72e4u'),
                                 ('run_read', '0x889f6cd6u'), ('run_readdir', '0x889f6b7cu'),
                                 ('run_write', '0x889f6dcau')):
            case = engine[engine.index('case file_engine::%s' % action):]
            case = case[:case.index('break;')]
            self.assertIn('stock<%s, void>(request);' % executor, case, action)
        default = engine[engine.index('    default: {'):]
        self.assertNotRegex(default, r'stock<0x889f(76ca|6fea|72e4|6cd6|6b7c|6dca)u')
        self.assertIn('word(vtable + 0x14) != executor', engine)          # the CRC-checked class executor

    def test_a_decides_on_its_own_copy_of_the_path(self):
        """The stock path buffer is the interrupt's (any later request's body lands there);
        a copies it (<= max_path bytes), decides on the copy and points the request at the copy
        before any executor runs, so the executor uses the path that was checked."""
        engine = self.body(self.server, 'void serve_engine(')
        copy = engine.index('if (!(path_copy[i] = char(byte(path_buffer + i)))) break;')
        point = engine.index('word(request + 0x18) = reinterpret_cast<U32>(path_copy);')
        decide = engine.index('file_engine::decide_request(')
        self.assertLess(copy, point)
        self.assertLess(point, decide)
        self.assertIn('for (U32 i = 0; i != max_path; ++i)', engine[engine.rindex('\n', 0, copy):copy])
        self.assertIn('path_pointer == path_buffer ? path_copy : 0, state, source);', engine)
        self.assertEqual(engine.count('path_buffer'), 3)           # the pointer check, the copy, the decision
        self.assertIn('char path_copy[max_path]', self.server)

    def test_a_keeps_the_marker_around_every_removal(self):
        """Unlink and rmdir run between a stat of the marker before (a path resolving to the
        marker's object is refused) and after (a marker that was there and is gone is re-created,
        lowercase, the name the 1.2.1 bootloader asks for)."""
        self.assertIn('const char marker_path[] = "/yaffs2/bootloader.dev";', self.server)
        engine = self.body(self.server, 'void serve_engine(')
        case = engine[engine.index('case file_engine::run_path: {'):]
        case = case[:case.index('#else')]
        before = case.index('const bool marker = (op == 0x25u || op == 0x27u) && stat_into(marker_path, marker_record[0]);')
        refuse = case.index('if (marker && stat_into(path_copy, marker_record[1]) && same_object()) {')
        run = case.index('stock<0x889f6feau, void>(request);')
        after = case.index('if (marker && !stat_into(marker_path, marker_record[1])) restore_marker(op);')
        self.assertLess(before, refuse)
        self.assertLess(refuse, run)
        self.assertLess(run, after)
        self.assertIn('session.reply(request, 0xffffffffu, error_access);', case[refuse:run])

    def test_no_other_storage_decision_remains(self):
        for name in ('decide_mtp', 'decide_write', 'valid_mtp_path', 'mtp_protected_path', 'mtp_boot_layer',
                     'serve_mtp', 'run_mtp', 'serve_write', 'open_fw', 'close_fw', 'mtp_state', 'write_mode',
                     'Action decide('):
            for path in ROOT.glob('target/*.?pp'):
                self.assertNotIn(name, path.read_text(), (name, path.name))

    def test_b_checks_before_it_sends(self):
        """Every path B's file client sends passes the engine's policy first; a refused path
        never reaches a stock builder."""
        client = (ROOT / 'target/file_client.cpp').read_text()
        allowed = self.body(client, 'bool allowed(const char* path, file_engine::Operation op)')
        self.assertIn('return file_engine::decide_engine(op, path) == file_engine::allow;', allowed)
        for name in ('Reply path_request(', 'Reply query('):
            b = self.body(client, name)
            self.assertIn('if (!allowed(path, ', b, name)
            self.assertIn('return outcome(refused);', b[b.index('if (!allowed(path, '):b.index('claim(session, why)')])
            self.assertLess(b.index('if (!allowed(path, '), b.index('place(path);'), name)   # then claim, place
            self.assertLess(b.index('place(path);'), b.index('stock<'), name)
        self.assertEqual(client.count('stock<0x889f8d22u'), 1)       # the path builder: only in path_request
        self.assertEqual(client.count('stock<0x889f8e36u'), 1)       # the query builder: only in query
        read = self.body(client, 'uint32_t read_file(')
        check = read.index('file_engine::decide_engine(file_engine::op_read, path) != file_engine::allow')
        self.assertLess(check, read.index('mount_ready(mounted)'))
        listing = self.body(client, 'uint32_t list(')
        self.assertLess(listing.index('file_engine::decide_engine(file_engine::op_list, path) != file_engine::allow'),
                        listing.index('mount_ready(mounted)'))
        change = self.body(self.usb, 'void file_change(')
        self.assertLess(change.index('!storage_server::valid_change_path(path, policy)) code = P::s_refused;'),
                        change.index('enter_writes()'))                     # the write area, then the policy
        self.assertIn('return xyf::usbp::s_refused;', read[check:check + 200])
        write = self.body(self.server, 'bool valid_change_path(')
        self.assertIn('write_root(path) && file_engine::decide_engine(op, path) == file_engine::allow', write)
        self.assertIn('bool valid_write_path(const char* path) { return valid_change_path(path, file_engine::op_write); }',
                      self.server)
        cw = self.body(client, 'uint32_t open_write(')
        self.assertLess(cw.index('if (!storage_server::valid_write_path(path)'), cw.index('mount()'))
        fw = self.body(self.usb, 'void file_write(uint8_t seq')
        self.assertLess(fw.index('storage_server::valid_write_path(path)) code = P::s_refused;'),
                        fw.index('enter_writes()'))
        self.assertNotIn('stock<0x889f', self.body(self.usb, 'bool mtp_do(U32 n, MtpSelfTest& t, uint8_t* out)'))

if __name__ == '__main__':
    unittest.main()
