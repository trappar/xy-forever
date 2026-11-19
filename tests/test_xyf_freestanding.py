"""XY Forever device modules compile freestanding with no libc/libgcc calls
(the cold link provides no memcpy, memset or division helpers)."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('xyf_store', 'xyf_info', 'xyf_manifest', 'xyf_filelist', 'xyf_recognise', 'xyf_library', 'xyf_catalog', 'xyf_menu', 'xyf_menu_view', 'xyf_crc32', 'xyf_state', 'xyf_state_write', 'xyf_state_write_library', 'xyf_stage2', 'stage2_menu', 'stage2_hello', 'usb_proto', 'ui_gfx', 'ui_screens', 'ui_pages', 'file_engine')
FLAGS = ['-mcpu=bf707', '-std=gnu++11', '-ffreestanding', '-fno-exceptions', '-fno-rtti', '-fno-threadsafe-statics',
         '-fno-use-cxa-atexit', '-fno-asynchronous-unwind-tables', '-fno-unwind-tables', '-fno-common',
         '-fno-tree-loop-distribute-patterns', '-fomit-frame-pointer', '-nostdinc++', '-Wall', '-Werror',
         '-Wno-unused-variable', '-Os', '-fstack-usage']
# The OS-only .tfw extractor, tfw.check's verdict and the native codecs they
# use. The codecs' aggregate copies (and tfw_check's zeroed View) call memcpy/memset, which
# native/memory.cpp supplies; the extractor itself calls nothing outside xyf_core. tfw::Archive::open hashes into a 1 KiB stack buffer; the extractor
# uses open_digested instead.
EXTRACTOR = ('tfw_os', 'tfw', 'tfw_check', 'tfw_signature', 'p224', 'aes', 'hash', 'inflate', 'ldr', 'package_policy')
SUPPLIED = {'_memcpy', '_memset'}
SELF_CONTAINED = {'tfw_os', 'tfw_signature', 'p224'}      # no calls outside xyf_core at all
LARGE_FRAMES = ('xyf_core::tfw::Archive::open(',)
# The signature check: its whole call tree, and the bound on its stack.
SIGNATURE = ('tfw_signature', 'p224', 'hash')
SIGNATURE_STACK = 3072
# Mod assembly's patch and content steps: no calls outside xyf_core at all
# (no memcpy, memset or division helpers), no BSS, no recursion.
MOD_ASSEMBLY = ('mod_patch', 'mod_content')
MOD_ASSEMBLY_TREE = MOD_ASSEMBLY + ('hash', 'ldr')
MOD_ASSEMBLY_STACK = 2048
# The whole mod assembler (link, patch, content): one bound on the text +
# rodata of every native mod source at the firmware's flags (xy_forever/toolchain.CXX_FLAGS, -O2),
# which stage 2 must find room for. A tripwire against unnoticed growth, not a hard limit: measured
# 2026-11-14 at 49,596 B (-O2) after the linker and patch/content hardening; the bound is that
# rounded up to 1 KiB plus 1 KiB of headroom. Raise it deliberately when a change justifies it.
MOD_ASSEMBLER = ('mod_link', 'mod_elf', 'mod_numbers', 'mod_patch', 'mod_content')
MOD_ASSEMBLER_CODE = 51200


def code_bytes(gxx, name, flags, tmp):
    """text + rodata of native/<name>.cpp compiled and assembled for the Blackfin."""
    out = Path(tmp) / (name + '.s')
    subprocess.run([str(gxx), *flags, '-S', str(ROOT / 'native' / (name + '.cpp')), '-o', str(out)],
                   check=True, capture_output=True, cwd=tmp)
    subprocess.run([str(gxx.parent / 'bfin-elf-as'), '-mcpu=bf707', '-o', str(out.with_suffix('.o')), str(out)],
                   check=True, capture_output=True)
    sections = subprocess.run([str(gxx.parent / 'bfin-elf-objdump'), '-h', str(out.with_suffix('.o'))],
                              check=True, capture_output=True, text=True).stdout
    return sum(int(f[2], 16) for f in (row.split() for row in sections.splitlines())
               if len(f) > 2 and f[1].startswith(('.text', '.rodata')))


def compiler():
    from xy_forever import toolchain
    path = toolchain.location() / 'bin/bfin-elf-g++'
    return path if path.exists() else None


class FreestandingTest(unittest.TestCase):
    def test_no_library_calls_and_small_stacks(self):
        gxx = compiler()
        if not gxx:
            self.skipTest('Blackfin toolchain absent')
        with tempfile.TemporaryDirectory(prefix='xyf-free-') as tmp:
            for name in MODULES:
                out = Path(tmp) / (name + '.s')
                subprocess.run([str(gxx), *FLAGS, '-I', str(ROOT / 'target'), '-S', str(ROOT / 'target' / (name + '.cpp')),
                                '-o', str(out)], check=True, capture_output=True, cwd=tmp)
                calls = set(re.findall(r'\bcall\s+(_[A-Za-z0-9_.]+)', out.read_text()))
                external = {c for c in calls if not c.startswith('__Z')}
                self.assertEqual(external, set(), name)
                text = out.read_text()
                self.assertNotRegex(text, r'\.(bss|lcomm|comm)\b', name + ' uses BSS (no zero-init in the cold link)')
                usage = (Path(tmp) / (name + '.su')).read_text().splitlines()
                worst = max(int(line.split('\t')[1]) for line in usage)
                self.assertLess(worst, 1024, name)

    def test_tfw_extractor_is_freestanding(self):
        gxx = compiler()
        if not gxx:
            self.skipTest('Blackfin toolchain absent')
        with tempfile.TemporaryDirectory(prefix='xyf-free-native-') as tmp:
            for name in EXTRACTOR:
                out = Path(tmp) / (name + '.s')
                subprocess.run([str(gxx), *FLAGS, '-S', str(ROOT / 'native' / (name + '.cpp')), '-o', str(out)],
                               check=True, capture_output=True, cwd=tmp)
                text = out.read_text()
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
        gxx = compiler()
        if not gxx:
            self.skipTest('Blackfin toolchain absent')
        total, graph = 0, {}
        with tempfile.TemporaryDirectory(prefix='xyf-free-signature-') as tmp:
            for name in SIGNATURE:
                out = Path(tmp) / (name + '.s')
                subprocess.run([str(gxx), *FLAGS, '-S', str(ROOT / 'native' / (name + '.cpp')), '-o', str(out)],
                               check=True, capture_output=True, cwd=tmp)
                function = None
                for line in out.read_text().splitlines():
                    label = re.match(r'(_[A-Za-z0-9_.]+):$', line)
                    if label:
                        function = label.group(1)
                        graph.setdefault(function, set())
                    call = re.search(r'\bcall\s+(_[A-Za-z0-9_.]+)', line)
                    if call and function:
                        graph[function].add(call.group(1))
                total += sum(int(line.split('\t')[1]) for line in (Path(tmp) / (name + '.su')).read_text().splitlines())
        state = {}

        def visit(function, path):
            if state.get(function) == 'open':
                self.fail('recursion: ' + ' -> '.join(path + [function]))
            if state.get(function) == 'done':
                return
            state[function] = 'open'
            for callee in graph.get(function, ()):
                visit(callee, path + [function])
            state[function] = 'done'
        for function in graph:
            visit(function, [])
        self.assertLess(total, SIGNATURE_STACK)


    def test_mod_assembly_is_freestanding(self):
        """Cross-compile check (compile and assemble only; nothing is linked into a firmware build)."""
        gxx = compiler()
        if not gxx:
            self.skipTest('Blackfin toolchain absent')
        tools = gxx.parent
        total, graph = 0, {}
        with tempfile.TemporaryDirectory(prefix='xyf-free-mods-') as tmp:
            for name in MOD_ASSEMBLY_TREE:
                out = Path(tmp) / (name + '.s')
                subprocess.run([str(gxx), *FLAGS, '-S', str(ROOT / 'native' / (name + '.cpp')), '-o', str(out)],
                               check=True, capture_output=True, cwd=tmp)
                text = out.read_text()
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
                subprocess.run([str(tools / 'bfin-elf-as'), '-mcpu=bf707', '-o', str(out.with_suffix('.o')), str(out)],
                               check=True, capture_output=True)
                sections = subprocess.run([str(tools / 'bfin-elf-objdump'), '-h', str(out.with_suffix('.o'))],
                                          check=True, capture_output=True, text=True).stdout
                for row in sections.splitlines():
                    f = row.split()
                    if len(f) > 2 and f[1].startswith(('.data', '.bss')):
                        self.assertFalse(int(f[2], 16), name + ' ' + f[1])
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
        self.assertLess(total, MOD_ASSEMBLY_STACK)

    def test_mod_assembler_code_size(self):
        """One bound for the whole assembler at the firmware's flags (compile and assemble only)."""
        gxx = compiler()
        if not gxx:
            self.skipTest('Blackfin toolchain absent')
        from xy_forever import toolchain
        with tempfile.TemporaryDirectory(prefix='xyf-free-assembler-') as tmp:
            code = sum(code_bytes(gxx, name, toolchain.CXX_FLAGS, tmp) for name in MOD_ASSEMBLER)
        self.assertLessEqual(code, MOD_ASSEMBLER_CODE, 'text + rodata of %s at the firmware flags' % (MOD_ASSEMBLER,))


if __name__ == '__main__':
    unittest.main()
