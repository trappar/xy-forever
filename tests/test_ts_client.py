"""The TypeScript client (clients/ts/: generated src/v1.ts, scripts/protogen.py).

The drift test (test_protocol_spec) keeps the generated file current without Node. Here, when the
tools are on this machine: Node runs clients/ts/test/v1.test.ts (golden frames through the typed
codecs, encoder and parser; every stream vector through the parser), and the client's own
TypeScript compiler type-checks it strictly when `pnpm install` has installed it in clients/ts (or
XYF_TSC names one). Nothing is installed here; each test is skipped cleanly without its tool. The
client's Node suite (Vitest, with its own simulators: `pnpm test` in clients/ts) runs here when
node_modules is installed (NodeSuiteTest, its own process in scripts/run_tests.py; about 120 s at
most); the browser suite (`pnpm run test:browser`) is not run here. No device, no network.

The conformance runner (clients/ts/test/conformance.ts) interprets every case of
protocol/v1.conformance.json, as the Python runner does, and runs them against the simulator over
TCP (skipped without Node, or without clang++ and the private stock images the simulator needs)."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import paths, procgroup, sim  # noqa: E402

CLIENT = ROOT / 'clients' / 'ts'
NODE = shutil.which('node')
LOCAL_TSC = CLIENT / 'node_modules' / '.bin' / 'tsc'                # pnpm install
TSC = os.environ.get('XYF_TSC') or (str(LOCAL_TSC) if LOCAL_TSC.exists() else None)
CASES = json.loads((ROOT / 'protocol' / 'v1.conformance.json').read_text())['cases']
SIM_UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
BUILD = paths.newest_verified_build()                    # a verified build of this source, when present


def node_strips_types():
    """Node 22.18+ and 23.6+ run .ts files without flags."""
    if not NODE:
        return False
    version = subprocess.run([NODE, '--version'], capture_output=True, text=True).stdout.strip()
    m = re.match(r'v(\d+)\.(\d+)', version)
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= ((22, 18) if m.group(1) == '22' else (23, 6))


class TypeScriptClientTest(unittest.TestCase):
    @unittest.skipUnless(node_strips_types(), 'Node 22.18+/23.6+ not found')
    def test_golden_frames_and_streams_in_node(self):
        run = procgroup.run([NODE, 'test/v1.test.ts'], cwd=CLIENT, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertRegex(run.stdout, r'^ok \d+ checks')

    @unittest.skipUnless(node_strips_types(), 'Node 22.18+/23.6+ not found')
    def test_mod_index_fetch_in_node(self):
        """loadIndex + fetchPackages over a local HTTP server (clients/ts/test/modindex.test.ts)."""
        run = procgroup.run([NODE, 'test/modindex.test.ts'], cwd=CLIENT, capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertRegex(run.stdout, r'modindex: \d+ checks passed')

    @unittest.skipUnless(TSC, 'no TypeScript compiler (pnpm install, or XYF_TSC)')
    def test_type_checks_strictly(self):
        command = [TSC] if os.access(TSC, os.X_OK) and not TSC.endswith('.js') else [NODE or 'node', TSC]
        run = procgroup.run(command + ['-p', 'tsconfig.json'], cwd=CLIENT, capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)          # strict settings: tsconfig.json

    @unittest.skipUnless(node_strips_types(), 'Node 22.18+/23.6+ not found')
    def test_conformance_cases_interpret(self):
        """Every case of the file is interpretable by the TypeScript runner (the Python runner: test_conformance)."""
        run = procgroup.run([NODE, 'test/conformance.ts', '--check'], cwd=CLIENT, capture_output=True, text=True,
                            timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(run.stdout.strip(), 'ok %d cases interpreted' % len(CASES))

    @unittest.skipUnless(node_strips_types(), 'Node 22.18+/23.6+ not found')
    @unittest.skipIf(SIM_UNAVAILABLE, SIM_UNAVAILABLE)
    def test_conformance_against_the_simulator(self):
        fixtures = ['--fixture', 'stock_os_ldr=%s' % sim.STOCK_OS]
        if BUILD:
            fixtures += ['--fixture', 'xyf_build=%s' % (BUILD / 'candidate.ldr')]
        with tempfile.TemporaryDirectory(prefix='xyf-ts-conf-') as tmp:
            root = sim.seed(Path(tmp) / 'yaffs2')
            with sim.Simulator(root, Path(tmp) / 'bootpart.bin', build_dir=sim.build(), log=Path(tmp) / 'log.txt') as s:
                run = procgroup.run([NODE, 'test/conformance.ts', '--sim', s.address] + fixtures, cwd=CLIENT,
                                    capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        rows = {line.split()[0]: line.split()[1] for line in run.stdout.splitlines()[1:]}
        self.assertEqual(len(rows), len(CASES))
        self.assertEqual({n for n, r in rows.items() if r != 'PASS'},
                         {'hello/pre-v1'} | (set() if len(fixtures) > 2 else {'boot.install/build'}))

    def test_module_shape(self):
        """Erasable syntax only (Node strips the types) and nothing the firmware build reads."""
        source = (CLIENT / 'src' / 'v1.ts').read_text()
        self.assertNotRegex(source, r'(?m)^\s*(export\s+)?(const\s+)?enum\s|^\s*(export\s+)?namespace\s|\bimport\b')
        self.assertNotIn('navigator', source)                        # no Web Serial code
        git = subprocess.run(['git', 'ls-files', '--', 'clients/ts'], cwd=ROOT, capture_output=True, text=True)
        if git.returncode == 0:                                      # pnpm install output never in git
            self.assertFalse([p for p in git.stdout.split() if p.split('/')[2] in ('node_modules', 'dist')])



@unittest.skipIf(os.environ.get('XYF_JS_SUITES_ELSEWHERE'), 'XYF_JS_SUITES_ELSEWHERE: run in the web job')
@unittest.skipUnless(node_strips_types(), 'Node 22.18+/23.6+ not found')
@unittest.skipUnless((CLIENT / 'node_modules' / '.bin' / 'vitest').exists(),
                     'clients/ts/node_modules absent: run pnpm install to include the Node suite (pnpm test)')
class NodeSuiteTest(unittest.TestCase):
    def test_pnpm_test(self):
        """The client's own suite: type check, codec checks, Vitest against its simulators; must pass."""
        pnpm = shutil.which('pnpm')
        if not pnpm:
            self.skipTest('pnpm not found')
        try:
            run = procgroup.run([pnpm, 'test'], cwd=CLIENT, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired as e:
            self.fail('pnpm test in clients/ts took over 120 s: %s' % (e.stdout or b'')[-2000:])
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-6000:])   # the exit code is the verdict


if __name__ == '__main__':
    unittest.main()
