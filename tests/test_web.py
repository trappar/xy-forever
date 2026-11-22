"""The web app in web/ (Vite + TypeScript, on clients/ts): its Vitest unit suite (`pnpm test` in web/)
runs here when Node and web/node_modules (`pnpm install`) are present, else it is skipped with the
reason. Of its e2e tests (`pnpm run test:e2e`, the system Chrome) only the Install page's run here
(`e2e/install.e2e.ts`: fake te-boot MIDI and MTP devices, no simulator; `e2e/install-sim.e2e.ts`: the install's end
against the device simulator through the fake Web Serial port; each skips itself without Chrome or the simulator)."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
sys.path.insert(0, str(ROOT))
from xy_forever import bounds, procgroup  # noqa: E402


class WebAppTest(unittest.TestCase):
    def test_pnpm_test(self):
        pnpm = shutil.which('pnpm')
        if not shutil.which('node') or not pnpm:
            self.skipTest('Node or pnpm not found')
        if not (WEB / 'node_modules' / '.bin' / 'playwright').exists():
            self.skipTest('web/node_modules absent: run pnpm install to include the web app suite (pnpm test)')
        try:
            run = procgroup.run([pnpm, 'test'], cwd=WEB, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired as e:
            self.fail('pnpm test in web/ took over 120 s: %s' % (e.stdout or b'')[-2000:])
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-6000:])
        self.assertRegex(run.stdout, r'Tests\s+\d+ passed')

    @bounds.slow                                    # #233: needs longer under the paced clock
    def test_install_page_in_chrome(self):
        pnpm = shutil.which('pnpm')
        if not shutil.which('node') or not pnpm:
            self.skipTest('Node or pnpm not found')
        if not (WEB / 'node_modules' / '.bin' / 'playwright').exists():
            self.skipTest('web/node_modules absent: run pnpm install')
        try:
            run = procgroup.run([pnpm, 'run', 'test:e2e', 'e2e/install.e2e.ts', 'e2e/install-sim.e2e.ts'], cwd=WEB,
                                capture_output=True, text=True, timeout=bounds.SLOW_S)
        except subprocess.TimeoutExpired as e:
            self.fail('the install browser test took over %g s: %s' % (bounds.SLOW_S, (e.stdout or b'')[-2000:]))
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-6000:])
        if 'install browser test skipped' in run.stdout + run.stderr or not re.search(r'\d+ passed', run.stdout):
            self.skipTest('the system Chrome cannot start')     # Playwright Test: every test skipped
        self.assertRegex(run.stdout, r'\d+ passed')

    def test_build_output_not_tracked(self):
        git = subprocess.run(['git', 'ls-files', '--', 'web'], cwd=ROOT, capture_output=True, text=True)
        if git.returncode == 0:                                      # pnpm install and build output never in git
            self.assertFalse([p for p in git.stdout.split() if p.split('/')[1] in ('node_modules', 'dist', '.vite')])


if __name__ == '__main__':
    unittest.main()
