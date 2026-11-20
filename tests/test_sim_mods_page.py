"""The menu's Mods page in the simulator, driven by injected keys (key commands).

The simulator hosts B's menu state machine and the page (sim/devices.cpp; the screen is not hosted): each
change of the page is logged as one "mods page:" line, which these tests read. Opening the page from the
home menu, the list filling while it is read (A slowed), the toggles and the memory estimate, Build (the
job mods.apply starts: its entry must equal the same selection built through mods.apply byte for byte),
(the page no longer builds: Continue saves, tests/test_sim_config.py) the result lines (content.sha256 missing, a catalog that is not the official one, an unknown base and its
SHIFT confirmation), keys during the job, cancel, and the page staying responsive while it lists 80+
packages (the longest pump pass is printed). Build 174's commit: a built set becomes library entry
mods-a or mods-b, listed "content not installed", and the page says so; the commit stage (keys and STOP in
it: not cancelled, said), a second Build at once (busy while the window closes, then the other id), a failed
commit, both ids taken, a USB job while the page is used, leaving and coming back. Synthetic packages always;
the published 0.0.6 set when present. Needs clang++ and the private stock 1.2.1 images (skipped without
them). No device access."""
from pathlib import Path
import shutil
import struct
import sys
import time
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from xy_forever import modsend as MS, modstore, sim                        # noqa: E402
from xy_forever import usb_proto as P                                      # noqa: E402
import test_sim_mod_job as J                                               # noqa: E402
import test_sim_content_switch as CS                                      # noqa: E402
from test_ui_mods import FILTER_ON, KINDS, display_order, estimate        # noqa: E402

PLAY, STOP, MINUS, PLUS, SHIFT = 51, 52, 53, 54, 55
MODS_ROW = 2                                                               # xyf_menu item_mods
VIEW_READING, VIEW_LIST, VIEW_BUILDING = range(3)
LINE_NONE, LINE_INFO, LINE_ERROR, LINE_DONE = range(4)
BUILT = 'Built: Mods: %d is in the Firmware library (content not installed yet).'
NO_CANCEL = 'Cannot cancel now: it is being added to the library.'
BUSY = 'Build failed: busy (a job, an upload or a save is running): try again.'
BUSY_READ = 'Busy: a build or content change is running.'                 # the page waits for a job


class PageBase(J.Base):
    """One simulator per test (test_sim_mod_job.Base); helpers to drive and read the page."""

    def key(self, key, pressed):
        self.assertEqual(self.dev.call(P.KEY, struct.pack('<II', key, pressed))[0], 0)

    def press(self, key):
        self.key(key, 1)
        self.key(key, 0)

    def pages(self):
        """Every "mods page:" line so far, as dicts (text= is the rest of the line)."""
        out = []
        log = (self.dir / 'log.txt').read_text(errors='replace') if (self.dir / 'log.txt').exists() else ''
        for line in log.splitlines():
            at = line.find('mods page: ')
            if at < 0:
                continue
            body = line[at + len('mods page: '):]
            head, _, text = body.partition(' text=')
            fields = dict(f.split('=', 1) for f in head.split(' '))
            fields = {k: (int(v) if v.isdigit() else v) for k, v in fields.items()}
            fields['on'] = [x for x in str(fields['on']).split(',') if x]
            fields['text'] = text
            out.append(fields)
        return out

    def lines(self, marker):
        log = (self.dir / 'log.txt').read_text(errors='replace') if (self.dir / 'log.txt').exists() else ''
        return [line[line.find(marker) + len(marker):] for line in log.splitlines() if marker in line]

    def bars(self, job):
        """The building bar of job at every redraw ("mods page:" and "mods bar:" lines, in order), as
        dicts with ms (B's clock), bar, phase, done, total, commit."""
        out = []
        log = (self.dir / 'log.txt').read_text(errors='replace') if (self.dir / 'log.txt').exists() else ''
        for line in log.splitlines():
            for marker in ('mods page: ', 'mods bar: '):
                at = line.find(marker)
                if at < 0:
                    continue
                head = line[at + len(marker):].partition(' text=')[0]
                f = dict(x.split('=', 1) for x in head.split(' ') if '=' in x)
                if int(f['job']) == job and (marker == 'mods bar: ' or int(f['view']) == VIEW_BUILDING):
                    out.append({k: int(f[k]) for k in ('ms', 'bar', 'phase', 'done', 'total', 'commit')})
                elif int(f['job']) == job and out and 'end' not in out[0]:
                    out[0]['end'] = int(f['ms'])                            # the job ended (the list again)
        return out

    def steady(self, job, what):
        """The bar never goes back and changes at least every 500 ms of B's clock while the job runs; the stage
        times (ms, from the first redraw in each), printed."""
        samples = self.bars(job)
        self.assertGreater(len(samples), 5, what)
        changed = samples[0]['ms']
        for a, b in zip(samples, samples[1:]):
            self.assertGreaterEqual(b['bar'], a['bar'], (what, a, b))
            if b['bar'] != a['bar']:
                self.assertLessEqual(b['ms'] - changed, 500, (what, a, b))
                changed = b['ms']
        starts = {}
        for q in samples:
            starts.setdefault(8 if q['commit'] else min(q['phase'], 7), q['ms'])
        order = sorted(starts.items(), key=lambda kv: kv[1])
        end = samples[0].get('end', samples[-1]['ms'])
        times = {k: (order[i + 1][1] if i + 1 < len(order) else end) - t for i, (k, t) in enumerate(order)}
        print('\n%s: %d redraws over %d ms, last bar %d; stage ms %s' % (what, len(samples), samples[-1]['ms'] -
              samples[0]['ms'], samples[-1]['bar'], times), file=sys.stderr)
        return times

    def page(self):
        pages = self.pages()
        return pages[-1] if pages else None

    def wait(self, cond, what, timeout=60.0):
        deadline = time.monotonic() + timeout
        while True:
            p = self.page()
            if p is not None and cond(p):
                return p
            self.assertLess(time.monotonic(), deadline, 'page never %s: %s' % (what, p))
            time.sleep(0.01)

    def open_page(self, timeout=60.0):
        for _ in range(MODS_ROW):
            self.press(PLUS)
        self.press(PLAY)
        return self.wait(lambda p: p['view'] == VIEW_LIST and p['shown'] == 1, 'listed', timeout)

    def select(self, folder):
        want = folder[4:] if folder.startswith('ext-') else folder
        for _ in range(200):
            if self.page()['selected'] == want:
                return
            self.press(PLUS)
        self.fail('%s never selected' % folder)

    def walk(self):
        """The rows' selections in order from the first row (SHIFT + -), by + presses (one full turn)."""
        self.shifted(MINUS)
        first = self.page()['selected']
        order = []
        for _ in range(self.page()['listed']):
            order.append(self.page()['selected'])
            before = len(self.pages())
            self.press(PLUS)
            self.wait(lambda p: len(self.pages()) > before, 'moved')
        self.assertEqual(self.page()['selected'], first)
        return order

    def shifted(self, key, field=None):
        """Key with SHIFT held; the page's answer (with field: the first line where it changed)."""
        before, was = len(self.pages()), field and self.page()[field]
        self.key(SHIFT, 1)
        self.press(key)
        self.key(SHIFT, 0)
        return self.wait(lambda p: len(self.pages()) > before and (not field or p[field] != was),
                         'answered SHIFT + %d' % key)

    def spin(self, clicks, knob=1):
        """Knob turns as the PSoC reports them (the simulator's knob frames): clicks clockwise (< 0:
        anticlockwise), one click = 2 counts (knob_input counts_per_row)."""
        reply = self.sim.control('knob %d %d %d' % (knob, 1 if clicks > 0 else -1, 2 * abs(clicks)))
        self.assertTrue(reply.get('ok'), reply)

    def shift_spin(self, clicks, knob=1):
        """SHIFT held, one click: the filter steps; the page's answer."""
        before, was = len(self.pages()), self.page()['filter']
        self.key(SHIFT, 1)
        self.spin(clicks, knob)
        q = self.wait(lambda p: len(self.pages()) > before and p['filter'] != was, 'answered SHIFT + turn', 10.0)
        self.key(SHIFT, 0)
        return q

    def goto(self, row):
        for _ in range(200):
            if self.page()['selected'] == row:
                return
            before = len(self.pages())
            self.press(MINUS)
            self.wait(lambda p: len(self.pages()) > before, 'moved')
        self.fail('%s never selected' % row)

    def play(self):
        before = len(self.pages())
        self.press(PLAY)
        return self.wait(lambda p: len(self.pages()) > before, 'answered PLAY')

    def to_filter(self, want):
        """SHIFT + a knob turn clockwise until the filter is want."""
        for _ in range(FILTER_ON + 1):
            if self.page()['filter'] == want:
                return self.page()
            self.shift_spin(1)
        self.fail('filter %d never reached' % want)

    def toggle(self, folders):
        for f in folders:
            self.select(f)
            before = len(self.pages())
            self.press(PLAY)
            self.wait(lambda p: len(self.pages()) > before, 'toggled')

    def ended(self, timeout=180.0):
        return self.wait(lambda p: p['view'] == VIEW_LIST and p['line'] in (LINE_INFO, LINE_ERROR, LINE_DONE) and
                         p['job'], 'ended', timeout)

    def closed(self, timeout=30.0):
        """A's write window closed after the job (B closes it without waiting: settle_begin)."""
        deadline = time.monotonic() + timeout
        while self.sim.status()['rw_open']:
            self.assertLess(time.monotonic(), deadline, 'the write window never closed')
            time.sleep(0.02)

    def committed(self, p):
        """The page said Built for its job: the library entry job.status names (mods-a or mods-b), its files
        in the library, nothing left in tmp/, listed with the page's name, content not installed (unusable).
        Returns the entry id."""
        self.assertEqual((p['view'], p['line'], p['text']), (VIEW_LIST, LINE_DONE, BUILT % len(p['on'])), p)
        f = self.status(p['job'])
        self.assertEqual((MS.PHASES[f['phase']], MS.ERRORS[f['error']]), ('done', 'none'), f)
        self.assertIn(f['detail'], ('mods-a', 'mods-b'), f)
        self.assertFalse(self.output(p['job']).exists())
        self.assertTrue((self.entry(f['detail']) / 'os.ldr').is_file())
        self.assertIn(MS.served(self.dev, out=lambda line: None), (0, J.ids.S_NOT_FOUND))   # A rescanned
        self.closed()
        self.assertEqual(self.leftovers(), [])
        item = self.listed(f['detail'])
        self.assertEqual((item['name'], item['usable'], item['content_missing']), ('Mods: %d' % len(p['on']), False, True))
        return f['detail']

    def same_as_mods_apply(self, p, unverified):
        """The page's committed entry equals mods.apply's for the same selection (order, name and flag),
        which takes the other mods id."""
        mine = self.committed(p)
        machines = [x for x in p['on'] if not x.startswith('ext-')]
        exts = [x[4:] for x in p['on'] if x.startswith('ext-')]
        code, job, missing = MS.start(self.dev, self.base, self.profile, self.generation, machines, exts,
                                      'Mods: %d' % len(p['on']), unverified)
        self.assertEqual((code, missing), (0, 0))
        f = self.finished(job)
        self.assertEqual(MS.PHASES[f['phase']], 'done', f)
        self.assertEqual({mine, f['detail']}, {'mods-a', 'mods-b'})
        got, want = J.tree(self.entry(mine)), J.tree(self.entry(f['detail']))
        self.assertTrue(want and 'os.ldr' in want)
        self.assertEqual(sorted(got), sorted(want))
        for path in want:
            self.assertEqual(got[path], want[path], path)
        return mine


def synthetic_setup(cls, extra=0):
    """test_sim_mod_job.SyntheticJob's packages, modbase the default entry; extra fake machine packages
    (copies of alpha, listing only: they cannot be built)."""
    import json
    import tempfile
    from xy_forever import modkit as M, modpatch as MP
    from test_modpatch import CATALOG, OFFICIAL, finish, kit_for, official_text, profile_for, stock
    cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-simpage-')
    cls.addClassCleanup(cls.tmp.cleanup)
    cls.work = Path(cls.tmp.name)
    data = stock()
    (cls.work / 'kit.json').write_text(json.dumps(kit_for(profile_for(data))))
    gen = Path(M.split_kit(cls.work / 'kit.json', cls.work / 'packages', finish=finish,
                           sdk_source='https://github.com/example/sdk@abc')['root'])
    for i in range(extra):
        folder = gen / ('m%02d' % i)
        shutil.copytree(gen / 'alpha', folder)
        text = (folder / 'info.txt').read_text().replace('id=alpha', 'id=m%02d' % i)
        (folder / 'info.txt').write_text(text.replace('name=', 'name=M%02d ' % i, 1))
    cls.seed_with(gen, MP.info_values(gen / 'base/info.txt')['profile'], data, CATALOG, official_text(OFFICIAL))
    (cls.seeded / 'xy-forever/state-a.txt').write_bytes(sim.slot_text(cls.base, 2))
    cls.folders = sorted(p.name for p in gen.iterdir() if p.is_dir() and p.name != 'base')


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class SyntheticPage(PageBase, unittest.TestCase):
    base = 'modbase'
    unverified = True

    @classmethod
    def setUpClass(cls):
        synthetic_setup(cls)

    def test_toggle_and_meter(self):
        p = self.open_page()
        first = display_order(self.gen_dir)[0][0]                          # the first package selected
        self.assertEqual((p['shown_rows'], p['count'], p['selected'], p['on']),
                         (len(self.folders),) * 2 + (first[4:] if first.startswith('ext-') else first, []))
        self.assertEqual(p['used'], estimate(self.gen_dir, []))
        on = ['alpha', 'gamma', 'ext-cap']
        self.toggle(on)
        p = self.page()
        self.assertEqual(sorted(p['on']), sorted(on))
        self.assertEqual(p['used'], estimate(self.gen_dir, on))
        self.toggle(['gamma'])                                              # off again
        self.assertEqual(self.page()['used'], estimate(self.gen_dir, ['alpha', 'ext-cap']))
        self.toggle(['gamma'])
        self.assertEqual((self.page()['changed'], self.page()['job']), (1, 0))   # pending until Continue saves

    def test_a_set_without_this_store_version_is_not_listed(self):
        """#124: no store.txt (an older layout): the set is skipped; a newer store version is skipped and said;
        this version again: listed."""
        store = self.root / 'xy-forever/mods' / self.generation / modstore.FILE
        store.unlink()
        for _ in range(MODS_ROW):
            self.press(PLUS)
        for text, said in ((None, 'No complete mod package set found.'), (b'version=2\n', 'need a newer XY Forever'),
                           (modstore.TEXT, None)):
            if text is not None:
                store.write_bytes(text)
            self.press(PLAY)
            if said is None:
                self.wait(lambda p: p['view'] == VIEW_LIST and p['count'] == len(self.folders), 'listed')
                break
            p = self.wait(lambda p: said in p['text'], said)
            self.assertEqual(p['count'], 0)
            self.press(STOP)
            self.wait(lambda p: p['shown'] == 0, 'left')

    def test_selection_kept_across_visits(self):
        self.open_page()
        self.toggle(['beta', 'ext-flt'])
        self.press(STOP)
        self.wait(lambda p: p['shown'] == 0, 'left')
        self.press(PLAY)                                                    # the home menu kept its row: Mods
        p = self.wait(lambda p: p['shown'] == 1, 'opened again')
        self.assertEqual((p['view'], sorted(p['on'])), (VIEW_LIST, ['beta', 'ext-flt']))

    def test_order_filter_and_all_shown(self):
        """Rows by kind then name; the filter cycles All, each kind present (its count), On only; all shown
        on / off touches only the rows shown; the meter, the status and the home row count every mod."""
        order = display_order(self.gen_dir)
        counts = [sum(1 for _, k in order if k == i) for i in range(len(KINDS))]
        p = self.open_page()
        self.assertEqual((p['filter'], p['listed']), (0, len(order)))
        ids = [f[4:] if f.startswith('ext-') else f for f, _ in order]
        self.assertEqual(self.walk(), ids)                                  # only the packages
        seen = [(0, len(order))]
        for knob in range(1, 1 + FILTER_ON + 1):                            # SHIFT + turn, any knob
            q = self.shift_spin(1, (knob - 1) % 4 + 1)
            seen.append((q['filter'], q['listed']))
            if q['filter'] == 0:
                break
        want = [(0, len(order))] + [(k + 1, n) for k, n in enumerate(counts) if n] + [(FILTER_ON, 0), (0, len(order))]
        self.assertEqual(seen, want)
        back = [(0, len(order))]
        for knob in range(1, 1 + FILTER_ON + 1):                            # anticlockwise: back, wrapping
            q = self.shift_spin(-1, (knob - 1) % 4 + 1)
            back.append((q['filter'], q['listed']))
            if q['filter'] == 0:
                break
        self.assertEqual(back, want[::-1])
        self.toggle(['ext-cap'])                                            # outside the synths
        synth = KINDS.index('synth')
        self.to_filter(synth + 1)
        q = self.shifted(PLAY, 'on')                                        # SHIFT + PLAY: every synth on, nothing else touched
        synths = [f for f, k in order if k == synth]
        self.assertEqual(sorted(q['on']), sorted(synths + ['ext-cap']))
        self.assertEqual((q['used'], q['changed'], q['job']), (estimate(self.gen_dir, synths + ['ext-cap']), 1, 0))
        self.press(STOP)
        self.wait(lambda p: p['shown'] == 0, 'left')
        n = len(synths) + 1
        self.assertIn('Open-XY - %d mods - ' % n, self.lines('menu: home row: ')[-1])
        self.assertIn('(changed)', self.lines('menu: home row: ')[-1])
        self.assertIn('pending=1', self.lines('menu: home row: ')[-1])
        self.press(PLAY)                                                    # back on the page: as it was left
        p = self.wait(lambda p: p['shown'] == 1, 'opened again')
        self.assertEqual((p['filter'], p['listed']), (synth + 1, len(synths)))
        q = self.shifted(12, 'on')                                          # all shown on: SHIFT + a knob push: off
        self.assertEqual((q['on'], q['used']), (['ext-cap'], estimate(self.gen_dir, ['ext-cap'])))

    def test_on_only_keeps_a_row_turned_off(self):
        order = display_order(self.gen_dir)
        self.open_page()
        on = ['alpha', 'gamma']
        self.toggle(on)
        p = self.to_filter(FILTER_ON)
        self.assertEqual(p['listed'], 2)
        self.toggle(['alpha'])                                              # off while shown: stays listed
        self.assertEqual((self.page()['listed'], self.page()['on']), (2, ['gamma']))
        self.assertEqual(self.walk(), [f for f, _ in order if f in on])
        self.to_filter(0)
        p = self.to_filter(FILTER_ON)                                       # chosen again: what is on now
        self.assertEqual((p['listed'], p['used']), (1, estimate(self.gen_dir, ['gamma'])))

@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class MenuQuality(PageBase, unittest.TestCase):
    """- / + repeat while held (400 ms, then every 80 ms; key_repeat.hpp), timed by B's clock in the
    simulator's log; the knob pushes (keys 10..13) act as PLAY; the building bar over a whole build."""
    base = 'modbase'
    unverified = True

    @classmethod
    def setUpClass(cls):
        synthetic_setup(cls)

    def held(self, key, ms, during=None):
        """Holds key, runs during(), then ms more of device time; returns (pressed ms, released ms, repeat ms list)."""
        before = len(self.lines('menu: repeat key '))
        self.key(key, 1)
        if during:
            during()
        self.sim.advance(ms)
        self.key(key, 0)
        self.sim.advance(300)                                               # nothing may follow the release
        down = int(self.lines('menu: key %d pressed at ' % key)[-1].split()[0])
        up = int(self.lines('menu: key %d released at ' % key)[-1].split()[0])
        repeats = [int(x.split(' at ')[1].split()[0]) for x in self.lines('menu: repeat key ')[before:]]
        return down, up, repeats

    def check_repeats(self, down, up, repeats, what):
        self.assertTrue(repeats, what)
        self.assertGreaterEqual(repeats[0] - down, 400, what)
        # at most two slowed requests late (a_slow_ms 30): the virtual clock's steps put it at either bound (#144:
        # A's two cycle reads per request moved it from below 460 to 460)
        self.assertLessEqual(repeats[0] - down, 400 + 60, what)
        gaps = [b - a for a, b in zip(repeats, repeats[1:])]
        self.assertTrue(all(g >= 80 for g in gaps), (what, gaps))         # never a burst
        self.assertTrue(all(r <= up for r in repeats), (what, up, repeats))   # none after the release

    def test_hold_repeats_then_stops_at_release(self):
        rows = len(self.lines('menu: screen 0 row '))
        down, up, repeats = self.held(PLUS, 1200)
        self.check_repeats(down, up, repeats, 'home')
        gaps = [b - a for a, b in zip(repeats, repeats[1:])]
        self.assertLessEqual(sorted(gaps)[len(gaps) // 2], 90, gaps)        # a steady 80 ms
        moves = self.lines('menu: screen 0 row ')[rows:]
        self.assertEqual(len(moves), 1 + len(repeats))                      # the press, then each repeat
        self.assertEqual(int(moves[-1].split()[0]), (1 + len(repeats)) % 5)          # #21: 5 home rows (no MTP mode, no Info)
        print('\nkey repeat: held %d ms, first after %d ms, %d repeats, gaps %d..%d ms'
              % (up - down, repeats[0] - down, len(repeats), min(gaps), max(gaps)), file=sys.stderr)
        before = len(self.lines('menu: repeat key '))
        self.press(MINUS)                                                   # a tap: no repeat
        self.sim.advance(600)
        self.assertEqual(len(self.lines('menu: repeat key ')), before)

    def test_hold_during_a_job_and_a_slow_pass_no_burst(self):
        """A USB job runs (A slowed) while + is held on the Mods list, and B's clock jumps 1.5 s (a pass that
        took that long): one move after it, never a burst; nothing after the release or the job's end."""
        self.open_page()
        self.sim.set('a_slow_ms', 30)
        job = self.start(['alpha', 'gamma'])
        down, up, repeats = self.held(PLUS, 900, during=lambda: (self.sim.advance(600), self.sim.jump(1500)))
        self.check_repeats(down, up, repeats, 'during a job')
        self.assertTrue(any(b - a >= 1400 for a, b in zip(repeats, repeats[1:])), repeats)   # the jump: one move
        self.assertNotEqual(MS.PHASES[self.status(job)['phase']], 'done')    # it was held during the job
        self.sim.set('a_slow_ms', 0)
        self.finished(job)
        count = len(self.lines('menu: repeat key '))
        self.sim.advance(400)
        self.assertEqual(len(self.lines('menu: repeat key ')), count)       # no queued moves after the job
        p = self.page()
        self.assertEqual(p['selected'], self.page_order()[(1 + len(repeats)) % len(self.folders)])   # packages only

    def page_order(self):
        """The rows' ids in order (the first package first), from the selection walk of the log."""
        order = []
        for q in self.pages():
            if q['shown'] and q['view'] == VIEW_LIST and q['selected'] not in order:
                order.append(q['selected'])
        return order

    def test_knob_pushes_are_play(self):
        p = self.open_page()                                                # Mods row, then a knob as PLAY
        first = self.page()['selected']                                     # the first package
        self.press(12)
        self.assertEqual(self.wait(lambda p: p['on'], 'toggled by a knob', 5.0)['on'], [first])
        self.press(13)
        self.wait(lambda p: not p['on'], 'toggled back', 5.0)
        self.press(STOP)
        self.wait(lambda p: p['shown'] == 0, 'left')
        before = len(self.lines('menu: action '))
        self.press(10)                                                      # home kept the Mods row: opens it
        self.wait(lambda p: p['shown'] == 1, 'opened by a knob', 5.0)
        self.assertEqual(self.lines('menu: action ')[before:], [])
        self.press(STOP)
        self.wait(lambda p: p['shown'] == 0, 'left')
        for _ in range(2):
            self.press(MINUS)
        self.key(11, 1)                                                     # home: Continue (no change: boots)
        self.wait(lambda p: self.lines('menu: boots the default '), 'continued', 10.0)

@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class PageAndSwitch(PageBase, CS.Switch, unittest.TestCase):
    """The content switch shares the job slot with the page's build. A switch running while the
    page is opened fresh: busy, no reading until it ends (also during a USB job's write phase); with the
    list held, Build is refused busy (nothing started) and the switch goes on to its end (default set, record
    written); the page's next Build reads the saved official catalog. Set-default to the entry a page build just
    committed: the switch runs and the record says it is installed. (CS.Switch's helpers for the switch.)"""
    base = 'modbase'
    unverified = True

    @classmethod
    def setUpClass(cls):
        synthetic_setup(cls)
        cls.seed_content()                                                  # official files and their list

    def mods_entry(self, machines):
        f = self.finished(self.start(machines))
        self.assertEqual(MS.PHASES[f['phase']], 'done', f)
        self.closed()
        return f['detail']

    def slow_switch(self, entry, slow_ms):
        """set_default of entry with A slowed: the switch starts (started) and is still running."""
        self.sim.set('a_slow_ms', slow_ms)
        self.assertEqual(self.dev.set_default(entry)[0], J.ids.S_STARTED)
        f = self.status()
        self.assertLess(f['phase'], MS.DONE, f)
        return f['job']

    def switch_ended(self, job, entry):
        self.sim.set('a_slow_ms', 0)
        f = self.finished(job)
        self.assertEqual((MS.PHASES[f['phase']], f['error'], f['detail']), ('done', 0, entry), f)
        self.closed()
        self.assertEqual(CS.content(self.root), self.expected(entry))
        self.assertEqual(self.default(), entry)
        self.assertTrue(self.record().startswith('state=installed\nentry=%s\n' % entry), self.record())

    def opened_busy(self, what):
        """The page opened fresh (no list held) while a job owns the slot: the busy line at once, no
        reading (no storage request) while it lasts."""
        for _ in range(MODS_ROW):
            self.press(PLUS)
        self.press(PLAY)
        p = self.wait(lambda p: p['shown'] == 1 and p['text'] == BUSY_READ, 'busy ' + what, 10.0)
        self.assertEqual((p['view'], p['line'], p['count']), (VIEW_READING, LINE_INFO, 0), p)
        return p

    def listed_after(self, what):
        """The list once the job is over (the page stayed open), never a storage error, every pump pass
        short; the longest is printed."""
        p = self.wait(lambda p: p['view'] == VIEW_LIST and p['shown'] == 1, 'listed after ' + what, 60.0)
        self.assertEqual((p['count'], p['shown_rows'], p['line']), (len(self.folders),) * 2 + (LINE_NONE,), p)
        self.assertEqual([q['text'] for q in self.pages() if 'Could not read' in q['text']], [])
        self.assertLess(p['longest'], 60, p)
        print('\nopened during %s: longest pump pass %d ms' % (what, p['longest']), file=sys.stderr)
        return p

    def test_opened_during_a_usb_job_write_phase(self):
        """A USB mods.apply in its write phase (A slowed): the page opened fresh says busy, sends nothing, lists
        when the job is over; the job's result is the same."""
        self.sim.set('a_slow_ms', 30)
        job = self.start(['alpha', 'gamma'])
        self.until_phase(job, MS.PHASES.index('write'))
        self.opened_busy('a USB job')
        self.assertEqual(MS.PHASES[self.status(job)['phase']], 'write')     # opened in it
        self.sim.advance(500)
        self.assertEqual(self.page()['text'], BUSY_READ)
        self.sim.set('a_slow_ms', 0)
        f = self.finished(job)
        self.assertEqual((MS.PHASES[f['phase']], MS.ERRORS[f['error']], f['detail']), ('done', 'none', 'mods-a'), f)
        self.listed_after('a USB job')
        self.assertTrue(self.entry('mods-a').exists())

    def test_opened_during_a_content_switch(self):
        """A content switch (A slowed): the page opened fresh says busy and sends nothing; STOP leaves it and
        opening it again is still busy; the switch ends as without the page and the page then lists."""
        a = self.mods_entry(['alpha'])
        job = self.slow_switch(a, 30)
        self.opened_busy('a content switch')
        self.press(STOP)                                                    # leaves the page: the switch goes on
        self.wait(lambda p: p['shown'] == 0, 'left')
        self.assertLess(self.status(job)['phase'], MS.DONE)                 # not cancelled, not ended
        self.press(PLAY)                                                    # opened again: still busy
        self.wait(lambda p: p['shown'] == 1, 'opened again')
        self.assertEqual(self.page()['text'], BUSY_READ)
        self.switch_ended(job, a)
        p = self.listed_after('a content switch')
        self.assertEqual(p['job'], 0)
        self.assertEqual([q for q in self.pages() if q['view'] == VIEW_BUILDING], [])   # never shown as its build

@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class ManyPackages(PageBase, unittest.TestCase):
    """80 more machine packages: the page shows at once and fills while it reads (A slowed to 2 ms a
    request); USB answers meanwhile; the longest pump pass stays short."""
    base = 'modbase'
    unverified = True

    @classmethod
    def setUpClass(cls):
        synthetic_setup(cls, extra=80)

    def test_listing_stays_responsive(self):
        total = len(self.folders)
        self.assertEqual(total, 7 + 80)
        self.sim.set('a_slow_ms', 2)
        for _ in range(MODS_ROW):
            self.press(PLUS)
        t = time.monotonic()
        self.press(PLAY)
        first = self.wait(lambda p: p['shown'] == 1, 'opened', 10.0)
        opened = time.monotonic() - t
        self.assertEqual(first['view'], VIEW_READING)                       # at once, reading
        slowest = 0.0
        while self.page()['view'] == VIEW_READING:
            s = time.monotonic()
            self.assertEqual(self.dev.call(P.STATUS, timeout=5.0)[0], 0)
            slowest = max(slowest, time.monotonic() - s)
            time.sleep(0.02)
        p = self.page()
        self.sim.set('a_slow_ms', 0)
        partial = [q['shown_rows'] for q in self.pages() if q['view'] == VIEW_READING]
        self.assertTrue(any(0 < n < total for n in partial), partial)       # filled as entries arrived
        self.assertEqual((p['shown_rows'], p['line']), (total, LINE_NONE))
        self.assertLess(p['longest'], 60, p)
        for _ in range(3):                                                  # keys work at once (past
            self.press(PLUS)                                                # the filter and all rows)
        self.press(PLAY)
        self.assertEqual(len(self.page()['on']), 1)
        print('\nManyPackages: %d packages listed in %.2f s host (A +2 ms a request), page shown after %.3f s, '
              'longest pump pass %d ms, slowest USB status %.3f s, %d redraws while reading'
              % (total, time.monotonic() - t, opened, p['longest'], slowest, len(partial)), file=sys.stderr)


def _published():
    return J._published()


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
@unittest.skipUnless(_published(), 'published-kit packages, stock os.ldr, op1_factory.json or content.sha256 absent')
class PublishedPage(PageBase, unittest.TestCase):
    """The real 0.0.6 set (82 packages, a known base, opxy-121 the default entry): listed, the meter for
    fm2op + capture, Build equal to mods.apply's entry."""
    base = 'opxy-121'

    @classmethod
    def setUpClass(cls):
        J.PublishedJob.setUpClass.__func__(cls)

    def test_real_set_listed_and_metered(self):
        t = time.monotonic()
        p = self.open_page(120.0)
        self.assertEqual((p['shown_rows'], p['limit']), (81, 2080768))
        listed = time.monotonic() - t
        on = ['fm2op', 'ext-capture']
        self.toggle(on)
        p = self.page()
        self.assertEqual(p['used'], estimate(self.gen_dir, on))
        # sorted while read; the display order as specified; the longest pass within ManyPackages' bound
        self.assertLess(p['longest'], 60, p)
        ids = [f[4:] if f.startswith('ext-') else f for f, _ in display_order(self.gen_dir)]
        self.assertEqual(self.walk(), ids)
        print('\nPublishedPage: 81 packages listed in %.2f s host, longest pump pass %d ms'
              % (listed, p['longest']), file=sys.stderr)
