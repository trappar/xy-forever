"""Knob turns in the menu (target/knob_input.*) in the simulator.

The board hands PSoC frames to B's stock receive queue as the hardware does (control "knob", "knob_raw",
"knob_jump"; sim/runtime.hpp): the slot written, the write index advanced, the read index following as the
stock poll would. B's menu loop runs the reader after that point, as key_ui.cpp does after its stock poll,
and logs one "knob:" line per pass that read or moved anything. Turns move the home menu, the Firmware
library and the Mods list by one row per N counts, bounded per pass; malformed frames, implausible index
steps and a different stock identity move nothing and show in the counters (also in diag.status, block
KNB1). Needs clang++ and the private stock 1.2.1 images (skipped without them). No device access."""
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import xyf_host as host                                                    # noqa: E402
from xy_forever import usb_proto as P                                      # noqa: E402
import test_sim_mod_job as J                                               # noqa: E402
from test_sim_mods_page import MINUS, PLAY, SHIFT, STOP, VIEW_BUILDING, VIEW_LIST, PageBase, synthetic_setup  # noqa: E402
from test_ui_mods import FILTER_ON, KINDS, display_order                   # noqa: E402

N, MAX_MOVES, MAX_FRAMES, LEFTOVER_MS = 2, 4, 32, 600                      # knob_input.hpp
HOME, LIBRARY, UNINSTALL, SETTINGS = 0, 1, 3, 6                            # xyf_menu Screen
PLUS = 54


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class Knobs(PageBase, unittest.TestCase):
    base = 'modbase'
    unverified = True

    @classmethod
    def setUpClass(cls):
        synthetic_setup(cls)

    def setUp(self):
        super().setUp()
        self.until(lambda: self.lines('knob: reader state '), 'the reader started')

    # -- helpers ---------------------------------------------------------------------------------
    def control(self, line):
        reply = self.sim.control(line)
        self.assertTrue(reply.get('ok'), (line, reply))

    def turn(self, knob, delta, count=1):
        self.control('knob %d %d %d' % (knob, delta, count))

    def knobs(self):
        """Every "knob:" pass line so far, as dicts of ints."""
        out = []
        for line in self.lines('knob: rows='):
            fields = dict(f.split('=', 1) for f in ('rows=' + line).split(' '))
            out.append({k: int(v) for k, v in fields.items()})
        return out

    def last(self):
        k = self.knobs()
        return k[-1] if k else None

    def until(self, cond, what, timeout=10.0):
        deadline = time.monotonic() + timeout
        while True:
            got = cond()
            if got:
                return got
            self.assertLess(time.monotonic(), deadline, 'never %s: %s' % (what, self.last()))
            time.sleep(0.01)

    def until_frames(self, frames, what=''):
        return self.until(lambda: (self.last() or {}).get('frames', -1) >= frames and self.last(), 'read %d %s'
                          % (frames, what))

    def counters(self):
        code, body = self.dev.call(P.STATUS)
        self.assertEqual(code, 0)
        k = host.decode_status(body)['knob_input']
        self.assertIsNotNone(k)
        return k

    # -- tests -----------------------------------------------------------------------------------
    def test_turns_move_home_and_library_n_counts_per_row(self):
        self.turn(1, 1)                                                    # one count, below N: no row
        k = self.until_frames(1)
        self.assertEqual((k['main'], k['moves']), (0, 0), k)
        self.turn(1, 1)                                                    # the 2nd count (one click): one row down
        k = self.until_frames(N)
        self.assertEqual((k['rows'], k['main'], k['moves']), (1, 1, 1), k)
        self.turn(2, -1, N)                                                # counter-clockwise: up
        k = self.until_frames(2 * N)
        self.assertEqual((k['rows'], k['main']), (-1, 0), k)
        self.turn(3, -1, N)                                                # wraps to the last row, as -
        self.assertEqual(self.until_frames(3 * N)['main'], 5)                  # 188: 6 home rows (no MTP mode)
        self.turn(4, 1, N)                                                 # and back to Continue
        self.assertEqual(self.until_frames(4 * N)['main'], 0)
        # a slow click: one count, the second 400 ms later (within LEFTOVER_MS): one row
        self.turn(1, 1)
        self.until_frames(9)
        self.sim.advance(400)
        self.turn(1, 1)
        k = self.until_frames(10)
        self.assertEqual((k['main'], k['idle'], k['moves']), (1, 0, 5), k)
        # a lone count is dropped after LEFTOVER_MS: one count, a pause, one count: no row
        self.turn(1, 1)
        self.until_frames(11)
        self.sim.advance(LEFTOVER_MS + 200)
        self.until(lambda: self.last()['idle'] == 1, 'the leftover dropped')
        self.turn(1, 1)
        k = self.until_frames(12)
        self.assertEqual((k['main'], k['idle'], k['moves']), (1, 1, 5), k)
        self.sim.advance(LEFTOVER_MS + 200)                     # that one too
        self.until(lambda: self.last()['idle'] == 2, 'the last count dropped')
        # opposite counts cancel: +1 then -1 is no move, and nothing is left over
        self.turn(1, 1)
        self.turn(1, -1)
        k = self.until_frames(14)
        self.assertEqual((k['main'], k['idle'], k['moves']), (1, 2, 5), k)
        self.press(PLAY)                                                   # into the Firmware library
        self.until(lambda: self.lines('menu: screen %d ' % LIBRARY), 'the library')
        self.turn(1, 1, N)
        k = self.until_frames(14 + N)
        self.assertEqual((k['screen'], k['library'], k['main']), (LIBRARY, 1, 1), k)
        self.turn(2, -1, N)
        k = self.until_frames(14 + 2 * N)
        self.assertEqual((k['screen'], k['library']), (LIBRARY, 0), k)
        c = self.counters()
        self.assertEqual((c['state'], c['frames'], c['dropped'], c['resyncs'], c['moves']), ('on', 14 + 2 * N, 0, 0, 7), c)
        self.assertEqual((c['knob1'], c['knob2'], c['knob3'], c['knob4']), (6 + N, -2 * N, -N, N), c)
        self.assertEqual(c['last'], '41 %02x 45 03 02 ff ff 00' % ((0xc4 + 14 + 2 * N - 1) & 0xff), c)
        self.press(STOP)                                                   # pushes are still PLAY: Continue
        self.turn(1, -1, N)
        self.assertEqual(self.until_frames(14 + 3 * N)['main'], 0)
        self.key(12, 1)                                                    # continue_boot (no change: boots)
        self.until(lambda: self.lines('menu: boots the default '), 'continued')

    def test_turns_move_the_mods_list(self):
        """Turns move the list (packages only); SHIFT + a turn of any knob steps the filter, one step per
        click, wrapping; SHIFT + a knob push turns every listed row on (then off); a push toggles the row."""
        ids = [f[4:] if f.startswith('ext-') else f for f, _ in display_order(self.gen_dir)]
        counts = [sum(1 for _, k in display_order(self.gen_dir) if k == i) for i in range(len(KINDS))]
        self.open_page()
        self.assertEqual(self.page()['selected'], ids[0])
        self.turn(3, 1, N)
        self.assertEqual(self.wait(lambda p: p['selected'] != ids[0], 'moved down')['selected'], ids[1])
        self.turn(4, 1, N)
        self.assertEqual(self.wait(lambda p: p['selected'] != ids[1], 'moved down again')['selected'], ids[2])
        self.turn(1, -1, N)
        self.wait(lambda p: p['selected'] == ids[1], 'moved up')
        self.turn(2, -1, N)
        self.wait(lambda p: p['selected'] == ids[0], 'back on the first row')
        kinds = [k + 1 for k, n in enumerate(counts) if n]                 # the filters after All
        filters = [0] + kinds + [FILTER_ON]
        for step, knob in enumerate((1, 2, 3, 4, 1, 2, 3, 4)):             # clockwise, every knob, wrapping
            q = self.shift_spin(1, knob)
            self.assertEqual(q['filter'], filters[(step + 1) % len(filters)], (step, knob, q))
        q = self.shift_spin(-1, 3)                                         # anticlockwise: back one
        self.assertEqual(q['filter'], filters[(8 - 1) % len(filters)], q)
        self.to_filter(1)                                                  # synths (the synthetic set has some)
        p = self.page()
        self.assertEqual(p['listed'], counts[0])
        self.turn(3, 1, N)                                                 # without SHIFT: moves within them
        p = self.wait(lambda q: q['selected'] != p['selected'], 'moved within the synths')
        self.assertEqual(p['filter'], 1)
        self.key(SHIFT, 1)                                                 # SHIFT + a knob push: all shown on
        self.press(12)
        self.key(SHIFT, 0)
        self.assertEqual(sorted(self.wait(lambda q: q['on'], 'all shown on')['on']),
                         sorted(f for f, k in display_order(self.gen_dir) if k == 0))
        self.key(SHIFT, 1)
        self.press(PLAY)                                                   # SHIFT + PLAY: all on, so off
        self.key(SHIFT, 0)
        self.wait(lambda q: not q['on'], 'all shown off')
        self.press(12)                                                     # a push: the row
        self.assertEqual(self.wait(lambda q: q['on'], 'toggled by a knob push')['on'], [p['selected']])

    def test_a_burst_is_bounded_per_pass(self):
        """200 frames of +1, 40 reaching the queue per pass: the reader takes MAX_FRAMES a pass and moves at
        most MAX_MOVES rows (a spin's excess is dropped, never queued); 200 at once (more than the queue
        holds between two passes): ignored, a resync."""
        self.control('set knob_per_pass 40')
        self.turn(1, 1, 200)
        k = self.until_frames(200, 'burst')
        passes = [q for q in self.knobs() if q['read']]
        self.assertTrue(all(q['read'] <= MAX_FRAMES and abs(q['rows']) <= MAX_MOVES for q in passes), passes)
        self.assertLessEqual(k['moves'], 200 // N)
        self.assertEqual((k['max_read'], k['max_moves'], k['resyncs']), (MAX_FRAMES, MAX_MOVES, 0), k)
        self.assertEqual(k['main'], k['moves'] % 6)                   # 188: 6 home rows (no MTP mode)
        print('\nknob burst: 200 frames in %d passes, %d rows, %d counts dropped'
              % (len(passes), k['moves'], k['idle']), file=sys.stderr)
        moves = k['moves']
        self.control('set knob_per_pass 0')
        self.turn(1, 1, 200)
        k = self.until(lambda: self.last()['resyncs'] == 1 and self.last(), 'resynchronised')
        self.assertEqual((k['moves'], k['skipped'], k['frames']), (moves, 200, 200), k)
        c = self.counters()
        self.assertEqual((c['max_read'], c['max_moves'], c['resyncs'], c['skipped']), (MAX_FRAMES, MAX_MOVES, 1, 200), c)

    def test_malformed_frames_and_index_steps_are_ignored(self):
        for raw in ('48014503010001',                                      # byte 0 not 0x41
                    '4101450401000100',                                    # length not a multiple of 3
                    '4101450000',                                          # length 0
                    '4101452a' + '010001' * 14,                            # 14 records: too long
                    '41014503010040',                                      # +64: out of range
                    '4101450601000102ff00'):                               # knob 2 -256 in a 2-record frame
            self.control('knob_raw %s' % raw)
        self.control('knob_raw 4101420a00000000000000000000')              # a key frame: not a knob frame
        self.control('knob_raw 41014503070005')                            # id 7 only: accepted, no counts
        # The accepted frame goes last: frames == 1 then means the reader has read every frame sent so far.
        # A frame still unread at the jump below is skipped with it (1001, as on the OP-XY).
        k = self.until(lambda: (self.last() or {}).get('frames') == 1 and self.last(), 'read them')
        self.assertEqual((k['dropped'], k['moves'], k['main'], k['idle']), (6, 0, 0, 0), k)
        self.control('knob_jump 1000')                                     # a 1,000-frame jump
        k = self.until(lambda: self.last()['resyncs'] == 1 and self.last(), 'jump ignored')
        self.assertEqual((k['skipped'], k['moves']), (1000, 0), k)
        self.turn(1, 1, N)                                                 # turns work after it
        self.assertEqual(self.until_frames(1 + N)['main'], 1)
        self.control('knob_jump -10')                                      # backwards (a restart)
        k = self.until(lambda: self.last()['resyncs'] == 2 and self.last(), 'step back ignored')
        self.turn(1, 1, N)
        k = self.until_frames(1 + 2 * N)
        self.assertEqual((k['main'], k['dropped'], k['resyncs']), (2, 6, 2), k)
        c = self.counters()
        self.assertEqual((c['frames'], c['dropped'], c['resyncs'], c['moves'], c['knob1']), (1 + 2 * N, 6, 2, 2, 2 * N), c)
        self.assertEqual(c['write_index'], 25 + 8 + 1000 + N - 10 + N, c)

    def test_indexes_that_wrap_at_128(self):
        """If the stock indexes wrap at 128 instead of counting on, turns still move (no resync)."""
        self.control('set knob_wrap 1')
        self.control('set knob_per_pass 3')
        self.turn(1, 1, 150)                                               # 25 + 150: past 127, then 0..
        k = self.until_frames(150)
        self.assertEqual((k['resyncs'], k['dropped'], k['moves']), (0, 0, 150 // N), k)
        self.assertEqual(self.counters()['write_index'], (25 + 150) % 128)

    def test_no_moves_in_the_uninstall_dialog_or_during_a_job(self):
        self.turn(1, 1, 3 * N)                                             # row 3: Uninstall (188)
        self.assertEqual(self.until_frames(3 * N)['main'], 3)
        self.press(PLAY)
        self.until(lambda: self.lines('menu: screen %d ' % UNINSTALL), 'the dialog')
        dialog = len(self.lines('menu: screen '))
        self.turn(1, 1, 2 * N)
        self.turn(2, -1, N)
        k = self.until_frames(6 * N)
        self.assertEqual((k['screen'], k['accept'], k['moves']), (UNINSTALL, 0, 3), k)
        self.assertGreater(k['idle'], 0, k)
        self.assertEqual(len(self.lines('menu: screen ')), dialog)        # nothing redrawn
        self.press(STOP)
        self.turn(1, -1, N)                                                # row 2: Mods
        self.assertEqual(self.until_frames(7 * N)['main'], 2)
        self.press(PLAY)
        self.wait(lambda p: p['view'] == VIEW_LIST and p['shown'] == 1, 'listed')
        self.key(SHIFT, 1); self.press(STOP); self.key(SHIFT, 0)          # SHIFT + STOP: the unknown base allowed
        self.toggle(['beta'])
        self.sim.set('a_slow_ms', 30)
        self.press(STOP)                                                   # Continue, Save: the build's view
        self.wait(lambda p: p['shown'] == 0, 'left')
        self.press(MINUS); self.press(MINUS); self.press(PLAY); self.press(PLAY)
        self.wait(lambda p: p['view'] == VIEW_BUILDING, 'building')
        pages = len(self.pages())
        self.turn(3, 1, 3 * N)
        k = self.until_frames(10 * N)
        self.assertEqual(k['moves'], 4, k)
        self.assertTrue(all(q['view'] == VIEW_BUILDING or q['selected'] == 'beta' for q in self.pages()[pages:]),
                        self.pages()[pages:])
        self.sim.set('a_slow_ms', 0)

    def test_a_different_stock_identity_keeps_the_reader_off(self):
        self.control('set knob_identity_fail 1')
        self.sim.control('power')
        self.until(lambda: len(self.lines('knob: reader state ')) == 2, 'the menu again', 60.0)
        self.assertEqual(self.lines('knob: reader state ')[-1], '2')
        self.turn(1, 1, 3 * N)
        self.sim.advance(500)
        self.assertEqual(self.knobs(), [])                                 # nothing read, nothing moved
        self.until(lambda: self.sim.status().get('listening'), 'USB again', 60.0)
        self.dev.t.close()                                                 # USB re-enumerated: a new session
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)
        c = self.counters()
        self.assertEqual((c['state'], c['frames'], c['moves']), ('identity', 0, 0), c)


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class OldKnobRecord(Knobs):
    """The demo knob direction left; a settings.txt written by build 184 (knob=reversed) still loads, and
    clockwise moves down as everywhere."""
    settings_text = b'format=1\nconfirm=0\nknob=reversed\n'

    def test_old_record_loads_and_turns_are_normal(self):
        self.until(lambda: self.lines('settings: confirm=0'), 'the settings read')
        self.turn(1, 1, N)                                                 # clockwise: one row down
        k = self.until_frames(N)
        self.assertEqual((k['rows'], k['moves']), (1, 1), k)


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class SettingsPage(PageBase, unittest.TestCase):
    """The menu with Settings (home row 5, above Info); its Mod memory row is tests/test_sim_memory.py."""
    base = 'modbase'
    unverified = True

    @classmethod
    def setUpClass(cls):
        synthetic_setup(cls)

    def wait(self, marker, timeout=10.0):
        deadline = time.monotonic() + timeout
        while not self.lines(marker):
            self.assertLess(time.monotonic(), deadline, 'never ' + marker)
            time.sleep(0.01)

    def wait_rows(self, n, timeout=10.0):
        deadline = time.monotonic() + timeout
        while len(self.lines('menu: screen 0 row ')) < n:
            self.assertLess(time.monotonic(), deadline, 'the menu never moved')
            time.sleep(0.01)

    def test_real_keys_drive_the_menu_as_injected_ones(self):
        """Control "realkey K P" sends a key as the PSoC does (not the host's KEY command): logged as
        real, and it moves the menu as an injected one does."""
        self.wait('settings: confirm=0')
        rows = len(self.lines('menu: screen 0 row '))
        for pressed in (1, 0):
            self.assertTrue(self.sim.control('realkey %d %d' % (PLUS, pressed)).get('ok'))
        self.wait('menu: real key %d pressed' % PLUS)
        self.wait_rows(rows + 1)
        self.assertTrue(self.lines('menu: screen 0 row ')[-1].startswith('1 '), self.lines('menu: screen 0 row ')[-1])
        self.press(PLUS)                                                   # injected: the next row
        self.wait_rows(rows + 2)
        self.assertTrue(self.lines('menu: screen 0 row ')[-1].startswith('2 '), self.lines('menu: screen 0 row ')[-1])
        self.assertFalse(self.lines('confirm: key '))                      # no prompt: nothing taken
        self.assertFalse(self.sim.control('realkey 54').get('ok'))


def _only(cls, keep):
    for name in [n for n in dir(Knobs) if n.startswith('test_') and n not in keep]:
        setattr(cls, name, None)


_only(OldKnobRecord, ('test_old_record_loads_and_turns_are_normal',))

if __name__ == '__main__':
    unittest.main()
