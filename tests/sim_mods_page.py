"""Helpers for the simulator tests and sweeps that drive the menu's Mods page by injected keys (the page's own
tests went in #233): PageBase, synthetic_setup and the key constants."""
from pathlib import Path
import shutil
import struct
import time

from xy_forever import bounds, modsend as MS, package_records, sim
from xy_forever import usb_proto as P
import test_sim_mod_job as J
from test_ui_mods import FILTER_ON

PLAY, STOP, MINUS, PLUS, SHIFT = 51, 52, 53, 54, 55
MODS_ROW = 2                                                               # xyf_menu item_mods
VIEW_READING, VIEW_LIST = range(2)                                       # (#197: no building view)
LINE_NONE, LINE_INFO, LINE_ERROR, LINE_DONE = range(4)
BUILT = 'Built: Mods: %d (content not installed yet).'
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

    def progress(self):
        """Every "progress:" line so far (the progress screen's full redraws, #197: the converge sequence after Save,
        Boot without mods, Restore, or PLAY on the first row), as dicts (note= is the rest of the line)."""
        out = []
        for line in self.lines('progress: job='):
            head, _, note = ('job=' + line).partition(' note=')
            title = head.partition(' title=')[2]
            fields = dict(f.split('=', 1) for f in head.partition(' title=')[0].split(' '))
            fields = {k: int(v) for k, v in fields.items()}
            fields.update(title=title, note=note)
            out.append(fields)
        return out

    def wait_progress(self, cond, what, timeout=bounds.WAIT_S):
        deadline = time.monotonic() + timeout
        while True:
            shown = self.progress()
            if shown and cond(shown[-1]):
                return shown[-1]
            self.assertLess(time.monotonic(), deadline, 'progress never %s: %s' % (what, shown[-1:]))
            self.sim.advance(10)

    def lines(self, marker):
        log = (self.dir / 'log.txt').read_text(errors='replace') if (self.dir / 'log.txt').exists() else ''
        return [line[line.find(marker) + len(marker):] for line in log.splitlines() if marker in line]

    def bars(self, job):
        """The progress bar of job at every redraw ("progress:" and "mods bar:" lines, in order), as
        dicts with ms (B's clock), bar, phase, done, total, commit."""
        out = []
        log = (self.dir / 'log.txt').read_text(errors='replace') if (self.dir / 'log.txt').exists() else ''
        for line in log.splitlines():
            for marker in ('progress: job=', 'mods bar: '):
                at = line.find(marker)
                if at < 0:
                    continue
                head = line[at + len(marker) - (4 if marker.endswith('=') else 0):].partition(' title=')[0]
                f = dict(x.split('=', 1) for x in head.split(' ') if '=' in x)
                if int(f['job']) == job:
                    out.append({k: int(f[k]) for k in ('ms', 'bar', 'phase', 'done', 'total', 'commit')})
        return out


    def page(self):
        pages = self.pages()
        return pages[-1] if pages else None

    def wait(self, cond, what, timeout=bounds.WAIT_S):
        deadline = time.monotonic() + timeout
        while True:
            p = self.page()
            if p is not None and cond(p):
                return p
            self.assertLess(time.monotonic(), deadline, 'page never %s: %s' % (what, p))
            self.sim.advance(10)

    def open_page(self, timeout=bounds.WAIT_S):
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

    def ended(self, timeout=bounds.WAIT_S):
        return self.wait(lambda p: p['view'] == VIEW_LIST and p['line'] in (LINE_INFO, LINE_ERROR, LINE_DONE) and
                         p['job'], 'ended', timeout)

    def closed(self, timeout=30.0):
        """A's write window closed after the job (B closes it without waiting: settle_begin)."""
        deadline = time.monotonic() + timeout
        while self.sim.status()['rw_open']:
            self.assertLess(time.monotonic(), deadline, 'the write window never closed')
            self.sim.advance(20)

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


def synthetic_setup(cls, extra=0):
    """test_sim_mod_job.SyntheticJob's packages, modbase the default entry; extra fake machine packages
    (copies of alpha, listing only: they cannot be built)."""
    import json
    import tempfile
    from xy_forever import modkit as M, modpatch as MP
    from test_modpatch import CATALOG, OFFICIAL, finish, kit_for, official_text, profile_for, stock
    from test_modkit import rewrite
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
        fields = json.loads((folder / package_records.NATIVE).read_bytes())
        fields['id'] = folder.name
        fields['name'] = 'M%02d %s' % (i, fields['name'])
        rewrite(folder, package_records.NATIVE, (json.dumps(fields) + '\n').encode())
    cls.seed_with(gen, MP.info_values(gen / 'base/info.txt')['profile'], data, CATALOG, official_text(OFFICIAL))
    (cls.seeded / 'xy-forever/state-a.txt').write_bytes(sim.slot_text(cls.base, 2))
    cls.folders = sorted(p.name for p in gen.iterdir() if p.is_dir() and p.name != 'base')









