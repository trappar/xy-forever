"""The web app in web/ (Vite + TypeScript, on clients/ts): its Vitest unit suite (`npm test` in web/)
runs here when Node and web/node_modules (`npm ci` in web/) are present, else it is skipped with the
reason. Its e2e test (`npm run test:e2e`: system Chrome against the simulator) is not run here."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'


class WebAppTest(unittest.TestCase):
    def test_npm_test(self):
        npm = shutil.which('npm')
        if not shutil.which('node') or not npm:
            self.skipTest('Node or npm not found')
        if not (WEB / 'node_modules' / '.bin' / 'vitest').exists():
            self.skipTest('web/node_modules absent: run npm ci in web/ to include the web app suite (npm test)')
        try:
            run = subprocess.run([npm, 'test'], cwd=WEB, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired as e:
            self.fail('npm test in web/ took over 120 s: %s' % (e.stdout or b'')[-2000:])
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-6000:])
        self.assertRegex(run.stdout, r'Tests\s+\d+ passed')

    def test_build_output_not_tracked(self):
        git = subprocess.run(['git', 'ls-files', '--', 'web'], cwd=ROOT, capture_output=True, text=True)
        if git.returncode == 0:                                      # npm ci and build output never in git
            self.assertFalse([p for p in git.stdout.split() if p.split('/')[1] in ('node_modules', 'dist', '.vite')])


if __name__ == '__main__':
    unittest.main()
