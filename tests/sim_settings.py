"""Helpers for the parked sweeps that drive the Settings page in the simulator (the tests that were here went in
#233): Rows, its row and key constants."""

import time
import unittest

from sim_mods_page import MINUS, PLAY, PLUS, PageBase, synthetic_setup
from test_sim_config import MARK, mark_text

from xy_forever import xyf_host as host

MAIN, INFO, SETTINGS, OPTIONS = 0, 1, 3, 4  # xyf_menu Screen (#197)
SAVED = 'menu: line: Settings saved.'
ACCESS_DENIED = 0x200F
NEWER = 'note=settings.txt is from a newer XY Forever'


class Rows(PageBase):
    base = 'modbase'
    unverified = True

    @classmethod
    def setUpClass(cls):
        synthetic_setup(cls)
        (cls.seeded / MARK).write_text(mark_text(cls.seeded, cls.base))  # converged once (#197): plain boots at once

    real = False  # keys as the OP-XY's own (serial off)

    # -- helpers ---------------------------------------------------------------------------------
    def key(self, key, pressed):
        if self.real:
            self.assertTrue(self.sim.control('realkey %d %d' % (key, pressed)).get('ok'))
        else:
            super().key(key, pressed)

    def until(self, cond, what, timeout=30.0):
        deadline = time.monotonic() + timeout
        while True:
            got = cond()
            if got:
                return got
            self.assertLess(time.monotonic(), deadline, 'never ' + what)
            self.sim.advance(10)

    def count(self, marker):
        return len(self.lines(marker))

    def record(self):
        path = self.root / 'xy-forever/settings.txt'
        return dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line)

    def stored(self, key):
        """record()[key] while a save may be replacing the file (None in between)."""
        try:
            return self.record().get(key)
        except FileNotFoundError:
            return None

    def settings_page(self):
        """Home (Continue) -> Settings, the last row."""
        seen = self.count('menu: screen %d ' % SETTINGS)
        self.press(MINUS)
        self.press(PLAY)
        self.until(lambda: self.count('menu: screen %d ' % SETTINGS) > seen, 'the Settings page')

    def pick(self, row, moves):
        """Settings row `row` (from row 0): PLAY opens its options, `moves` x + (- when negative), PLAY picks; back
        to row 0. The option picked."""
        opened, picks = self.count('settings: options current'), self.count('settings: pick on row %d:' % row)
        for _ in range(row):
            self.press(PLUS)
        self.press(PLAY)
        self.until(lambda: self.count('settings: options current') > opened, 'the option list')
        for _ in range(abs(moves)):
            self.press(PLUS if moves > 0 else MINUS)
        self.press(PLAY)
        self.until(lambda: self.count('settings: pick on row %d:' % row) > picks, 'the pick')
        for _ in range(row):
            self.press(MINUS)
        return int(self.lines('settings: pick on row %d: option ' % row)[-1])

    def saved(self, seen):
        """Until a save's end since `seen` lines; still on the Settings page (nothing left)."""
        self.until(lambda: self.count(SAVED) > seen, 'the save')
        self.assertIn(self.lines('menu: screen ')[-1].split()[0], ('%d' % SETTINGS, '%d' % OPTIONS))  # never home

    def in_os(self):
        return self.sim.status()['a'] == 'os' and self.sim.status()['b'] == 'os'

    def reconnect(self):
        self.dev.t.close()
        self.sim.wait_listening(30.0)
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)


if __name__ == '__main__':
    unittest.main()
