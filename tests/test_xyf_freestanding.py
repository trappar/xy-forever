"""XY Forever device modules compile freestanding with no libc/libgcc calls
(the cold link provides no memcpy, memset or division helpers)."""

import re
import tempfile
import unittest
from pathlib import Path

import pytest
from conftest import blackfin

ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    'xyf_store',
    'xyf_info',
    'xyf_manifest',
    'xyf_filelist',
    'xyf_recognise',
    'xyf_library',
    'xyf_catalog',
    'xyf_menu',
    'xyf_menu_view',
    'xyf_crc32',
    'xyf_state',
    'xyf_state_write',
    'xyf_state_write_library',
    'xyf_stage2',
    'stage2_menu',
    'stage2_hello',
    'usb_proto',
    'ui_gfx',
    'ui_screens',
    'ui_pages',
    'file_engine',
)
FLAGS = [
    '-mcpu=bf707',
    '-std=gnu++11',
    '-ffreestanding',
    '-fno-exceptions',
    '-fno-rtti',
    '-fno-threadsafe-statics',
    '-fno-use-cxa-atexit',
    '-fno-asynchronous-unwind-tables',
    '-fno-unwind-tables',
    '-fno-common',
    '-fno-tree-loop-distribute-patterns',
    '-fomit-frame-pointer',
    '-nostdinc++',
    '-Wall',
    '-Werror',
    '-Wno-unused-variable',
    '-Os',
    '-fstack-usage',
]
# The OS-only .tfw extractor, tfw.check's verdict and the native codecs they
# use. The codecs' aggregate copies (and tfw_check's zeroed View) call memcpy/memset, which
# native/memory.cpp supplies; the extractor itself calls nothing outside xyf_core. tfw::Archive::open hashes into a 1 KiB stack buffer; the extractor
# uses open_digested instead.
EXTRACTOR = ('tfw_os', 'tfw', 'tfw_check', 'tfw_signature', 'p224', 'aes', 'hash', 'inflate', 'ldr', 'package_policy')
SUPPLIED = {'_memcpy', '_memset'}
SELF_CONTAINED = {'tfw_os', 'tfw_signature', 'p224'}  # no calls outside xyf_core at all
LARGE_FRAMES = ('xyf_core::tfw::Archive::open(',)
# The signature check: its whole call tree, and the bound on its stack.
SIGNATURE = ('tfw_signature', 'p224', 'hash')
SIGNATURE_STACK = 3072
# Mod assembly's patch and content steps: no calls outside xyf_core at all
# (no memcpy, memset or division helpers), no BSS, no recursion.
MOD_ASSEMBLY = ('mod_patch', 'mod_content')
MOD_ASSEMBLY_TREE = MOD_ASSEMBLY + ('hash', 'ldr')
MOD_ASSEMBLY_STACK = 2048


def compiled(test, folder, names, tmp, include=False, assemble=()):
    """Compile each folder/<name>.cpp with FLAGS to <name>.s (and its .su) in tmp, in one container run (the
    toolchain is in the build image); each name in assemble is also assembled to <name>.o. Returns objdump -h of
    each assembled object."""
    commands = [
        [
            'g++',
            *FLAGS,
            *(['-I', ROOT / 'target'] if include else []),
            '-S',
            ROOT / folder / (name + '.cpp'),
            '-o',
            name + '.s',
        ]
        for name in names
    ]
    for name in assemble:
        commands += [['as', '-mcpu=bf707', '-o', name + '.o', name + '.s'], ['objdump', '-h', name + '.o']]
    results = blackfin(test, commands, tmp)
    return {name: results[len(names) + 2 * n + 1][1] for n, name in enumerate(assemble)}


@pytest.mark.gate
@pytest.mark.toolchain
class FreestandingTest(unittest.TestCase):
    def test_no_library_calls_and_small_stacks(self):
        with tempfile.TemporaryDirectory(prefix='xyf-free-') as tmp:
            compiled(self, 'target', MODULES, tmp, include=True)
            for name in MODULES:
                text = (Path(tmp) / (name + '.s')).read_text()
                calls = set(re.findall(r'\bcall\s+(_[A-Za-z0-9_.]+)', text))
                external = {c for c in calls if not c.startswith('__Z')}
                self.assertEqual(external, set(), name)
                self.assertNotRegex(text, r'\.(bss|lcomm|comm)\b', name + ' uses BSS (no zero-init in the cold link)')
                usage = (Path(tmp) / (name + '.su')).read_text().splitlines()
                worst = max(int(line.split('\t')[1]) for line in usage)
                self.assertLess(worst, 1024, name)

    def test_tfw_extractor_is_freestanding(self):
        with tempfile.TemporaryDirectory(prefix='xyf-free-native-') as tmp:
            compiled(self, 'native', EXTRACTOR, tmp)
            for name in EXTRACTOR:
                text = (Path(tmp) / (name + '.s')).read_text()
                calls = set(re.findall(r'\bcall\s+(_[A-Za-z0-9_.]+)', text))
                external = {c for c in calls if not c.startswith('__Z')}
                self.assertLessEqual(external, set() if name in SELF_CONTAINED else SUPPLIED, name)
                self.assertNotRegex(text, r'\.(bss|lcomm|comm)\b', name + ' uses BSS')
                for line in (Path(tmp) / (name + '.su')).read_text().splitlines():
                    function, size = line.split('\t')[:2]
                    if not any(f in function for f in LARGE_FRAMES):
                        self.assertLess(int(size), 1024, function)

    def test_signature_check_stack_is_bounded(self):
        """No recursion anywhere in the signature check's modules, so the sum of all their
        frames bounds the stack of any call chain through them."""
        total, graph = 0, {}
        with tempfile.TemporaryDirectory(prefix='xyf-free-signature-') as tmp:
            compiled(self, 'native', SIGNATURE, tmp)
            for name in SIGNATURE:
                function = None
                for line in (Path(tmp) / (name + '.s')).read_text().splitlines():
                    label = re.match(r'(_[A-Za-z0-9_.]+):$', line)
                    if label:
                        function = label.group(1)
                        graph.setdefault(function, set())
                    call = re.search(r'\bcall\s+(_[A-Za-z0-9_.]+)', line)
                    if call and function:
                        graph[function].add(call.group(1))
                total += sum(int(line.split('\t')[1]) for line in (Path(tmp) / (name + '.su')).read_text().splitlines())
        self.assert_no_recursion(graph)
        self.assertLess(total, SIGNATURE_STACK)

    def test_mod_assembly_is_freestanding(self):
        """Cross-compile check (compile and assemble only; nothing is linked into a firmware build)."""
        total, graph = 0, {}
        with tempfile.TemporaryDirectory(prefix='xyf-free-mods-') as tmp:
            heads = compiled(self, 'native', MOD_ASSEMBLY_TREE, tmp, assemble=MOD_ASSEMBLY)
            for name in MOD_ASSEMBLY_TREE:
                text = (Path(tmp) / (name + '.s')).read_text()
                function = None
                for line in text.splitlines():
                    label = re.match(r'(_[A-Za-z0-9_.]+):$', line)
                    if label:
                        function = label.group(1)
                        graph.setdefault(function, set())
                    call = re.search(r'\bcall\s+(_[A-Za-z0-9_.]+)', line)
                    if call and function:
                        graph[function].add(call.group(1))
                frames = [int(line.split('\t')[1]) for line in (Path(tmp) / (name + '.su')).read_text().splitlines()]
                total += sum(frames)
                if name not in MOD_ASSEMBLY:
                    continue
                calls = set(re.findall(r'\bcall\s+(_[A-Za-z0-9_.]+)', text))
                self.assertEqual({c for c in calls if not c.startswith('__Z')}, set(), name)
                self.assertNotRegex(text, r'\.(bss|lcomm|comm)\b', name + ' uses BSS')
                self.assertLess(max(frames), 1024, name)
                for row in heads[name].splitlines():
                    f = row.split()
                    if len(f) > 2 and f[1].startswith(('.data', '.bss')):
                        self.assertFalse(int(f[2], 16), name + ' ' + f[1])
        self.assert_no_recursion(graph)
        self.assertLess(total, MOD_ASSEMBLY_STACK)

    def assert_no_recursion(self, graph):
        state = {}

        def visit(function, path):
            self.assertNotEqual(state.get(function), 'open', 'recursion: ' + ' -> '.join(path + [function]))
            if state.get(function) == 'done':
                return
            state[function] = 'open'
            for callee in graph.get(function, ()):
                visit(callee, path + [function])
            state[function] = 'done'

        for function in graph:
            visit(function, [])


if __name__ == '__main__':
    unittest.main()
