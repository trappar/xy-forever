"""#314: B's own system MMR reads carry the ADSP-BF70x anomaly 19000058 workaround (docs/architecture/code-rules.md):
the helper pads the load with NOPs on both sides and aligns it to 8 bytes, and the menu loop's display DMA status
reads go through it. Source only; no device access."""

import re
import unittest

from xy_forever.paths import REPO

KEY_UI = REPO / 'target/key_ui.cpp'
DISPLAY = REPO / 'target/key_ui_display.ipp'


class SmmrReadsTest(unittest.TestCase):
    def test_helper_pads_and_aligns_the_load(self):
        text = KEY_UI.read_text()
        helper = text[text.index('inline uint32_t smmr_read32(') :]
        helper = helper[: helper.index('\n}\n')]
        self.assertIn('NOP; NOP; NOP;\\n\\t.p2align 3\\n\\t%0 = [%1];\\n\\tNOP; NOP; NOP;', helper)
        self.assertIn('always_inline', text[text.index('smmr_read32') - 80 : text.index('smmr_read32')])

    def test_display_dma_status_is_read_only_through_it(self):
        for path in (KEY_UI, DISPLAY):
            text = path.read_text()
            self.assertEqual(re.findall(r'word\(display_dma_stat\)', text), [], path.name)
        self.assertIn('smmr_read32(display_dma_stat)', DISPLAY.read_text())


if __name__ == '__main__':
    unittest.main()
