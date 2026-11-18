"""0.12: B's stack high-water mark, as STATUS reports it."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever.source_text import unit_text  # noqa: E402
from xy_forever import hostbuild  # noqa: E402


class StackMarkTests(unittest.TestCase):
    def test_used_counts_from_the_deepest_overwritten_word(self):
        with tempfile.TemporaryDirectory(prefix='stack-mark-') as temporary:
            binary = Path(temporary)/'mark'
            hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                           '-fsanitize=address,undefined', str(ROOT/'target/stack_mark_test.cpp'),
                           '-o', str(binary)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertIn('5 stack mark checks passed', result.stdout)

    def test_status_reports_the_live_mark(self):
        source = unit_text(ROOT / 'target/usb_service.cpp')
        body = source[source.index('void status(uint8_t seq) {'):]
        body = body[:body.index('\n}\n')]
        self.assertIn('stack_mark::used', body)       # computed for every STATUS, before the Result block is copied
        self.assertLess(body.index('stack_mark::used'), body.index('append_block(p, 4, &result'))
        self.assertIn('usb_service::stack_watch(painted)', (ROOT/'target/key_ui.cpp').read_text())


if __name__ == '__main__':
    unittest.main()
