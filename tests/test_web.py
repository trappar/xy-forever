"""The web app in web/ (Vite + TypeScript, on clients/ts): its Vitest unit suite (`npm test` in web/)
runs here when Node and web/node_modules (`npm ci` in web/) are present, else it is skipped with the
reason. Of its e2e tests (`npm run test:e2e`, the system Chrome) only the Install page's runs here
(`e2e/install.e2e.ts`: fake te-boot MIDI and MTP devices, no simulator; it skips itself without Chrome)."""
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
sys.path.insert(0, str(ROOT))
from xy_forever import procgroup  # noqa: E402


class WebAppTest(unittest.TestCase):
    def test_npm_test(self):
        npm = shutil.which('npm')
        if not shutil.which('node') or not npm:
            self.skipTest('Node or npm not found')
        if not (WEB / 'node_modules' / '.bin' / 'vitest').exists():
            self.skipTest('web/node_modules absent: run npm ci in web/ to include the web app suite (npm test)')
        try:
            run = procgroup.run([npm, 'test'], cwd=WEB, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired as e:
            self.fail('npm test in web/ took over 120 s: %s' % (e.stdout or b'')[-2000:])
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-6000:])
        self.assertRegex(run.stdout, r'Tests\s+\d+ passed')

    def test_install_page_in_chrome(self):
        npm = shutil.which('npm')
        if not shutil.which('node') or not npm:
            self.skipTest('Node or npm not found')
        if not (WEB / 'node_modules' / '.bin' / 'vitest').exists():
            self.skipTest('web/node_modules absent: run npm ci in web/')
        try:
            run = procgroup.run([npm, 'run', 'test:e2e', '--', 'e2e/install.e2e.ts'], cwd=WEB, capture_output=True, text=True,
                                timeout=240)
        except subprocess.TimeoutExpired as e:
            self.fail('the install browser test took over 240 s: %s' % (e.stdout or b'')[-2000:])
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-6000:])
        if 'install browser test skipped' in run.stdout + run.stderr:
            self.skipTest('the system Chrome cannot start')
        self.assertRegex(run.stdout, r'Tests\s+\d+ passed')

    def test_build_output_not_tracked(self):
        git = subprocess.run(['git', 'ls-files', '--', 'web'], cwd=ROOT, capture_output=True, text=True)
        if git.returncode == 0:                                      # npm ci and build output never in git
            self.assertFalse([p for p in git.stdout.split() if p.split('/')[1] in ('node_modules', 'dist', '.vite')])


if __name__ == '__main__':
    unittest.main()
