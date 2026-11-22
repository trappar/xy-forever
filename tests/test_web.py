"""The web app in web/ (Vite + TypeScript, on clients/ts): its build output stays out of git. Its Vitest suite
runs in `mise run web-test`, its browser tests (the system Chrome, fake devices and the simulator) in `mise run
web-e2e`, which CI runs when web/ changes (.github/workflows/e2e.yml), never in the routine suite (#233)."""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WebAppTest(unittest.TestCase):
    def test_build_output_not_tracked(self):
        git = subprocess.run(['git', 'ls-files', '--', 'web'], cwd=ROOT, capture_output=True, text=True)
        if git.returncode == 0:  # pnpm install and build output never in git
            self.assertFalse([p for p in git.stdout.split() if p.split('/')[1] in ('node_modules', 'dist', '.vite')])


if __name__ == '__main__':
    unittest.main()
