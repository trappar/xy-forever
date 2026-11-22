"""0.12: B's stack high-water mark, as STATUS reports it (the mark itself: tests/native/stack_mark_test.cpp)."""

import unittest
from pathlib import Path

from xy_forever.source_text import unit_text

ROOT = Path(__file__).resolve().parents[1]


class StackMarkTests(unittest.TestCase):
    def test_status_reports_the_live_mark(self):
        source = unit_text(ROOT / 'target/usb_service.cpp')
        body = source[source.index('void status(uint8_t seq) {') :]
        body = body[: body.index('\n}\n')]
        self.assertIn('stack_mark::used', body)  # computed for every STATUS, before the Result block is copied
        self.assertLess(body.index('stack_mark::used'), body.index('append_block(p, 4, &result'))
        self.assertIn('usb_service::stack_watch(painted)', (ROOT / 'target/key_ui.cpp').read_text())


if __name__ == '__main__':
    unittest.main()
