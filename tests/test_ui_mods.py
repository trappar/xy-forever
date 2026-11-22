"""The Mods page on the host: the device's page model and view (target/xyf_mods.cpp,
xyf_mods_view.cpp) fed with real package texts: the list, toggles and keys, the memory estimate against
an independent Python sum, mods.apply's request bytes, the result lines, and previews (empty, a few on,
over the limit, building, done, failed) drawn inside the visible rows. Synthetic packages always; the
published 0.0.6 set when present, whose previews are kept in
<private>/xy-forever/previews/mods-page/. No device, no simulator."""

import itertools
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

import pytest
import ui_preview as P
from test_modpatch import PACKAGES, finish, kit_for, profile_for, stock

from xy_forever import modkit as M
from xy_forever import modsend as MS
from xy_forever import package_records, paths

PLAY, STOP, MINUS, PLUS, SHIFT = 51, 52, 53, 54, 55
NONE, REDRAW, LEAVE = range(3)
LINE_NONE, LINE_INFO, LINE_ERROR, LINE_DONE = range(4)
REQUEST_NONE, REQUEST_BUILD, REQUEST_UNVERIFIED, REQUEST_CANCEL = range(4)
TURN = 0x100  # menu::key_turn (a knob turn's row)
RED, ACCENT = 0xF9E7, 0xFD20
REAL_PREVIEWS = paths.PREVIEWS / 'mods-page'
KEEP = paths.XYF.is_dir()  # previews kept only in a private area


def info(folder):
    return package_records.read_values(folder)


def metadata_view(folder):
    name = package_records.local_name(folder)
    return package_records.text_view((folder / name).read_bytes(), name)


def estimate(gen, on):
    """The meter's figure computed here from the package texts: base + runtime + every service a chosen
    package names + each chosen package's code, data, state and 8 bytes per object."""
    base = info(gen / 'base')
    services = {}
    for line in (gen / 'base/services.txt').read_text().splitlines():
        if line.startswith('#') or not line.strip():
            continue
        name, *fields = line.split()
        f = dict(x.split('=', 1) for x in fields)
        services[name] = sum(int(f[k]) for k in ('code', 'data', 'state'))
    used = sum(int(base[k]) + int(base['runtime_' + k]) for k in ('code', 'data', 'state'))
    names = set()
    for folder in on:
        m = info(gen / folder)
        used += sum(int(m[k]) for k in ('code', 'data', 'state')) + 8 * int(m['objects'])
        names |= set(m['services'].split(',')) - {'-'}
    return used + sum(services[n] for n in names)


def build_lib():
    return P.build()


def load(lib, gen, base_text=None):
    """The page as xyf_mods_io leaves it after reading gen (folders in name order; the device's are in
    yaffs order, the rows are sorted the same either way)."""
    lib.mods_reset()
    b = base_text if base_text is not None else metadata_view(gen / 'base')
    s = (gen / 'base/services.txt').read_bytes()
    assert lib.mods_base(b) and lib.mods_services(s)
    for d in sorted(p for p in gen.iterdir() if p.is_dir() and p.name != 'base'):
        if (d / 'files.sha256').is_file():
            t = metadata_view(d)
            assert lib.mods_package(lib.mods_add(d.name.encode()), t), d
    lib.mods_ready(info(gen / 'base')['profile'].encode(), gen.name.encode())


def press(lib, key):
    effect = lib.mods_key(key, 1)
    lib.mods_key(key, 0)
    return effect


def select(lib, folder):
    """Moves the selection to the package in folder (ext-<id> for an extension)."""
    want = folder[4:] if folder.startswith('ext-') else folder
    for _ in range(lib.mods_listed()):
        if lib.mods_list_id(lib.mods_position()).decode() == want:
            return
        press(lib, PLUS)
    raise AssertionError('%s not listed' % folder)


def toggle(lib, folders):
    for f in folders:
        select(lib, f)
        assert press(lib, PLAY) == REDRAW


def shifted(lib, key):
    """Key with SHIFT held."""
    lib.mods_key(SHIFT, 1)
    effect = press(lib, key)
    lib.mods_key(SHIFT, 0)
    return effect


class Base:
    gen = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-mods-page-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.lib = build_lib()
        cls.fb = P.framebuffer(cls.lib)

    def hidden_writes(self):
        return sum(
            1
            for col in range(P.WIDTH)
            for y in range(P.HEIGHT, P.STRIDE)
            if self.fb[(P.WIDTH - 1 - col) * P.STRIDE + y]
        )

    def pixel(self, x, y):
        return self.fb[(P.WIDTH - 1 - x) * P.STRIDE + y]

    def draw(self, out=None):
        self.lib.mods_draw(self.fb)
        self.assertEqual(self.hidden_writes(), 0)
        if out:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            P.png(out, P.pixels(self.fb))

    def text(self):
        return self.lib.mods_text().decode()

    def previews(self, folder, few, over_base):
        """The six states drawn (and kept in folder): empty, reading, a few on, over the limit, building,
        cancelling, done, failed."""
        lib = self.lib
        lib.mods_reset()
        lib.mods_ready(b'', b'')
        self.draw(folder / 'mods-1-empty.png')
        self.assertEqual(lib.mods_shown(), 0)
        load(lib, self.gen)
        lib.mods_reading(23)
        self.draw(folder / 'mods-2-reading.png')
        load(lib, self.gen)
        toggle(lib, few)
        self.draw(folder / 'mods-3-list.png')
        if lib.mods_used() * 448 // lib.mods_limit():
            self.assertEqual(self.pixel(16, 25), ACCENT)  # the meter's used part
        shifted(lib, PLUS)  # SHIFT + +: the last row
        self.draw(folder / 'mods-4-last-row.png')
        if over_base:
            load(lib, self.gen, over_base)
            toggle(lib, few)
            self.draw(folder / 'mods-5-over-limit.png')
            self.assertEqual(self.pixel(460, 25), RED)  # the whole bar, red
            self.assertIn('Too big', lib.mods_refusal().decode())
        lib.mods_job(3, 2, 40, 120, b'Mods: %d' % len(few), 0, 0)
        lib.mods_progress(300)
        self.draw(folder / 'mods-6-building.png')
        lib.mods_job(3, 5, 4, 9, b'Mods: %d' % len(few), 1, 0)
        lib.mods_progress(4000)
        self.draw(folder / 'mods-7-cancelling.png')
        load(lib, self.gen)
        toggle(lib, few)
        lib.mods_job(3, 7, 2, 3, b'Mods: %d' % len(few), 0, 1)  # the library commit
        lib.mods_progress(9000)
        self.draw(folder / 'mods-7b-commit.png')
        self.assertEqual(press(lib, STOP), REDRAW)  # refused, said
        self.draw(folder / 'mods-7c-commit-stop.png')
        lib.mods_ended(8, 0, 0, 0)
        self.draw(folder / 'mods-8-done.png')
        self.assertEqual(lib.mods_line(), LINE_DONE)
        self.assertEqual(self.text(), 'Built: Mods: %d (content not installed yet).' % len(few))
        lib.mods_refused(3, 5)  # state: both mods ids taken
        self.draw(folder / 'mods-8b-slots-in-use.png')
        lib.mods_ended(9, 15, 4, 0)  # content, catalog
        self.draw(folder / 'mods-9-failed.png')
        self.assertEqual(self.text(), 'Build failed: the content catalog is not the official one.')


# a whole build as the page sees it: (phase, total, typical share of the time) per stage, the commit last.
BUILD = [
    (0, 0, 3),
    (1, 40, 15),
    (2, 6, 10),
    (3, 9, 10),
    (4, 3, 8),
    (5, 120, 25),
    (6, 30, 15),
    (7, 2, 4),
    ('commit', 3, 10),
]


def build_trace(slow=1, stall=None):
    """(ms, phase, done, total, commit) every 10 ms of a 10 s build (slow x longer; stall: one step of that
    stage takes 20 s)."""
    ms, out = 0, []
    for stage, (phase, total, share) in enumerate(BUILD):
        commit = phase == 'commit'
        steps = max(total, 1)
        step_ms = int(share * 100 * slow) // steps
        for done in range(steps):
            span = 20000 if stall == stage and done == 0 else step_ms
            for t in range(0, max(span, 10), 10):
                out.append((ms + t, 7 if commit else phase, done, total, int(commit)))
            ms += max(span, 10)
    return out


@pytest.mark.gate
class Progress(Base, unittest.TestCase):
    """The building screen's bar (forward only, moving every 500 ms) and its incremental drawing."""

    def run_trace(self, trace):
        lib = self.lib
        lib.mods_reset()
        lib.mods_ready(b'', b'')
        values = []
        for ms, phase, done, total, commit in trace:
            lib.mods_job(1, phase, done, total, b'Mods: 1', 0, commit)
            values.append((ms, lib.mods_progress(ms)))
        return values

    def test_table(self):
        shares = [self.lib.mods_stage_share(i) for i in range(9)]
        self.assertEqual(sum(shares), 1000000)
        self.assertTrue(all(self.lib.mods_stage_ms(i) for i in range(9)))

    def test_bar_only_moves_forward_and_keeps_moving(self):
        for name, trace in (
            ('typical', build_trace()),
            ('slow', build_trace(slow=3)),
            ('fast', build_trace(slow=0.2)),
            ('stalled link', build_trace(stall=2)),
            ('stalled commit', build_trace(stall=8)),
        ):
            values = self.run_trace(trace)
            last_change = 0
            for (_, a), (ms, b) in itertools.pairwise(values):
                self.assertGreaterEqual(b, a, name)
                if b != a:
                    last_change = ms
                self.assertLessEqual(ms - last_change, 500, (name, ms, b))
            self.assertLess(values[-1][1], 1000000, name)
            self.assertGreater(values[-1][1], 900000, name)

    def test_update_draws_only_what_changed(self):
        """After a full repaint each update leaves the rest of the framebuffer alone (sentinels in the title
        bar and in the bar's filled part survive) and the result equals a full repaint of the same state."""
        lib, fb = self.lib, self.fb
        lib.mods_reset()
        lib.mods_ready(b'', b'')
        states = [
            (1, 2, 0, 6, 0, 0, 200),
            (1, 2, 1, 6, 0, 0, 700),
            (1, 3, 4, 9, 0, 0, 2600),
            (1, 5, 60, 120, 0, 0, 5200),
            (1, 5, 61, 120, 1, 0, 5300),
            (1, 7, 1, 3, 0, 1, 8600),
            ('refuse', 0, 0, 0, 0, 0, 8800),
            (1, 7, 2, 3, 0, 1, 9300),
        ]
        lib.mods_job(1, 1, 0, 40, b'Mods: 7', 0, 0)
        lib.mods_progress(0)
        lib.mods_draw(fb)
        SENTINEL = 0x1234
        for job, phase, done, total, cancelling, commit, ms in states:
            if job == 'refuse':
                lib.mods_refuse_cancel()
            else:
                lib.mods_job(job, phase, done, total, b'Mods: 7', cancelling, commit)
            lib.mods_progress(ms)
            filled = [x for x in range(16, 464) if self.pixel(x, 48) == ACCENT]
            marks = [(4, 4), (240, 26)] + ([(filled[0], 48)] if filled else [])
            for x, y in marks:
                self.fb[(P.WIDTH - 1 - x) * P.STRIDE + y] = SENTINEL
            lib.mods_update(fb)
            for x, y in marks:
                self.assertEqual(self.pixel(x, y), SENTINEL, (job, phase, x, y))
                self.fb[(P.WIDTH - 1 - x) * P.STRIDE + y] = ACCENT if y == 48 else 0
            updated = list(fb)
            lib.mods_draw(fb)
            self.assertEqual(updated, list(fb), (job, phase, done))
            self.assertEqual(self.hidden_writes(), 0)

    def test_update_draws_the_usb_mark_and_line(self):
        """On the building screen a change of the USB mark or the activity line alone is drawn by an update
        (the bar's filled part stays) and equals a full repaint; the line sits below the footer."""
        lib, fb = self.lib, self.fb
        lib.mods_reset()
        lib.mods_ready(b'', b'')
        lib.mods_job(1, 5, 60, 120, b'Mods: 7', 0, 0)
        lib.mods_progress(5200)
        try:
            lib.ui_chrome(b'build 186', 0, b'')
            lib.mods_draw(fb)
            for usb, line in ((1, b''), (1, b'USB: building Open-XY'), (0, b'USB: building Open-XY'), (0, b'')):
                lib.ui_chrome(b'build 186', usb, line)
                filled = [x for x in range(16, 464) if self.pixel(x, 48) == ACCENT]
                self.fb[(P.WIDTH - 1 - filled[0]) * P.STRIDE + 48] = 0x1234
                lib.mods_update(fb)
                self.assertEqual(self.pixel(filled[0], 48), 0x1234)
                self.fb[(P.WIDTH - 1 - filled[0]) * P.STRIDE + 48] = ACCENT
                self.assertEqual(self.pixel(P.WIDTH - 20, 12) == ACCENT, bool(usb))
                self.assertEqual(
                    any(self.pixel(x, P.HEIGHT - 11 + y) == ACCENT for x in range(16, 60) for y in range(8)), bool(line)
                )
                updated = list(fb)
                lib.mods_draw(fb)
                self.assertEqual(updated, list(fb), (usb, line))
        finally:
            lib.ui_chrome(b'build 186', 0, b'')

    def test_previews_176(self):
        """The building screen at three points of a build (kept for review)."""
        lib = self.lib
        lib.mods_reset()
        lib.mods_ready(b'', b'')
        for name, (phase, done, total, commit), ms in (
            ('early', (1, 12, 40, 0), 900),
            ('middle', (5, 70, 120, 0), 5600),
            ('commit', (7, 2, 3, 1), 9400),
        ):
            lib.mods_job(1, phase, done, total, b'Mods: 7', 0, commit)
            lib.mods_progress(ms)
            self.draw(REAL_PREVIEWS / ('176-building-%s.png' % name) if KEEP else None)
        if KEEP:  # home (unchanged), the compact library
            lib.flow_start(1)
            lib.flow_draw(self.fb, b'')
            P.png(REAL_PREVIEWS / '176-home.png', P.pixels(self.fb))
            lib.ui_render_library(self.fb, 3, 7)
            P.png(REAL_PREVIEWS / '176-library.png', P.pixels(self.fb))


CONFIG_PREVIEWS = paths.PREVIEWS / 'mods-config'


@pytest.mark.gate
class SaveModel(Base, unittest.TestCase):
    """The home row (#197: no Save dialog: PLAY converges to what is saved; a selection that cannot be built says why on
    the first row's status line), the safe-mode row (Boot without mods and Restore Open-XY, both at once), the Mods
    status row and the progress screen; previews kept in <private>/xy-forever/previews/mods-config/ when private/ is
    there."""

    CONTINUE, SAFE_MODE, RESTORE, APPLY, MAIN = 2, 3, 4, 9, 0

    def keep(self, name):
        if KEEP:
            CONFIG_PREVIEWS.mkdir(parents=True, exist_ok=True)
            P.png(CONFIG_PREVIEWS / name, P.pixels(self.fb))

    def test_first_row_keys(self):
        lib = self.lib
        lib.flow_start(1)
        lib.flow_config(b'', 0, None)
        self.assertEqual(lib.flow_key(PLAY), self.CONTINUE)  # nothing pending: boots at once
        for label in (b'Open-XY - 7 mods - 1.2.1 (to apply)', b'Open-XY - 7 mods - 1.2.1 (rebuild)'):
            lib.flow_start(1)
            lib.flow_config(label, 1, None)
            self.assertEqual((lib.flow_key(PLAY), lib.flow_screen()), (self.APPLY, self.MAIN))  # converges, no dialog
        lib.flow_config(b'Open-XY - 7 mods - 1.2.1 (rebuild)', 1, b'Too big for the mods memory: turn some mods off.')
        self.assertEqual((lib.flow_key(PLAY), lib.flow_screen()), (self.APPLY, self.MAIN))  # it fails with the reason
        self.assertEqual((lib.flow_key(STOP), lib.flow_screen()), (0, self.MAIN))

    def test_safe_row_and_previews(self):
        lib, fb = self.lib, self.fb
        lib.flow_start(1)
        lib.flow_config(b'Open-XY - 7 mods - 1.2.1 (rebuild)', 1, None)
        lib.flow_draw(fb, b'')
        self.keep('home-pending.png')
        lib.flow_config(b'Open-XY - 7 mods - 1.2.1 (rebuild)', 1, b'Too big for the mods memory: turn some mods off.')
        lib.flow_draw(fb, b'')
        self.keep('home-blocked.png')  # the reason on the first row's line
        lib.flow_start(1)
        lib.flow_config(b'Open-XY - 7 mods - 1.2.1', 0, None)
        lib.flow_draw(fb, b'')
        self.keep('home-no-setup.png')  # the safe-mode row dimmed
        lib.flow_safe(b'Boot without mods', 0)
        lib.flow_draw(fb, b'')
        self.keep('home-open-xy.png')
        lib.flow_key(PLUS)
        self.assertEqual((lib.flow_key(PLAY), lib.flow_screen()), (self.SAFE_MODE, self.MAIN))  # #197: at once
        lib.flow_config(b'OP-XY 1.2.1 (safe mode)', 0, None)
        lib.flow_safe(b'Restore Open-XY (7 mods)', 1)
        lib.flow_draw(fb, b'')
        self.keep('home-safe-mode.png')
        self.assertEqual((lib.flow_key(PLAY), lib.flow_screen()), (self.RESTORE, self.MAIN))
        lib.mods_reset()
        lib.mods_ready(b'', b'')
        for name, (build, stage, done, total), ms in (
            ('progress-building', (1, 0, 0, 0), 900),
            ('progress-content', (1, 1, 3, 8), 9400),
            ('progress-saving', (1, 2, 0, 0), 9900),
            ('progress-no-build', (0, 1, 2, 8), 300),
        ):
            if not stage:
                lib.mods_saving(build, 0, 0, 0)
                lib.mods_job(0x10001, 5, 70, 120, b'Mods: 7', 0, 0)
                lib.mods_saving(build, 0, 0, 0)
            else:
                lib.mods_saving(build, stage, done, total)
            bar = lib.mods_progress(ms)
            self.assertLessEqual(bar, 1000000)
            self.draw()
            self.keep(name + '.png')
        for name, title in (
            ('progress-safe-mode', b'Booting without mods'),
            ('progress-restore', b'Restoring Open-XY'),
            ('progress-apply', b'Applying changes'),
        ):  # #197: the same screen from the home menu
            lib.mods_reset()
            lib.mods_ready(b'', b'')
            lib.mods_saving(0, 1, 2, 3)
            lib.mods_progress_title(title)
            self.assertLessEqual(lib.mods_progress(1200), 1000000)
            self.draw()
            self.keep(name + '.png')


@pytest.mark.gate
class SyntheticPage(Base, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        work = Path(cls.tmp.name)
        (work / 'kit.json').write_text(json.dumps(kit_for(profile_for(stock()))))
        cls.gen = Path(
            M.split_kit(
                work / 'kit.json', work / 'packages', finish=finish, sdk_source='https://github.com/example/sdk@abc'
            )['root']
        )
        cls.folders = sorted(p.name for p in cls.gen.iterdir() if p.is_dir() and p.name != 'base')

    def test_generation_order(self):
        c = self.lib.mods_compare
        self.assertGreater(c(b'0.0.10-aaaaaaaaaaaa', b'0.0.6-bbbbbbbbbbbb'), 0)
        self.assertLess(c(b'0.0.6-ffffffffffff', b'0.1.0-000000000000'), 0)
        self.assertEqual(c(b'1.2.3-abc', b'1.2.3-abc'), 0)
        self.assertGreater(c(b'1.2.3-abd', b'1.2.3-abc'), 0)
        self.assertGreater(c(b'1.2.3.1-a', b'1.2.3-a'), 0)

    def test_status_row_previews(self):
        """The status row: saved, a flip being saved (#197), a selection that cannot be built."""
        lib = self.lib
        load(lib, self.gen)
        self.draw(CONFIG_PREVIEWS / 'mods-status-none.png' if KEEP else None)
        toggle(lib, ['alpha', 'gamma'])
        self.assertEqual(lib.mods_changed(), 1)
        self.draw(CONFIG_PREVIEWS / 'mods-status-changed.png' if KEEP else None)
        toggle(lib, ['alpha', 'gamma', 'ext-cap'])
        self.assertEqual(lib.mods_save_refusal(), b'Turn on at least one machine first.')
        self.draw(CONFIG_PREVIEWS / 'mods-status-blocked.png' if KEEP else None)

    def test_list_toggles_meter_and_request(self):
        lib = self.lib
        load(lib, self.gen)
        self.assertEqual(lib.mods_shown(), len(self.folders))
        rows = [lib.mods_row_id(r).decode() for r in range(lib.mods_shown())]
        machines = [r for r in rows if 'ext-' + r not in self.folders]
        self.assertEqual(rows[: len(machines)], machines)  # machines first, then extensions
        self.assertEqual(lib.mods_used(), estimate(self.gen, []))
        self.assertEqual(lib.mods_limit(), int(info(self.gen / 'base')['module_bytes']))
        self.assertEqual((lib.mods_request(), lib.mods_line(), lib.mods_save_refusal()), (REQUEST_NONE, LINE_NONE, b''))
        self.assertEqual(lib.mods_changed(), 0)
        on = ['alpha', 'gamma', 'ext-cap']
        toggle(lib, on)
        self.assertEqual(lib.mods_line(), LINE_NONE)  # a toggle clears the last result
        self.assertEqual(lib.mods_used(), estimate(self.gen, on))
        toggle(lib, ['gamma'])  # off again
        self.assertEqual(lib.mods_used(), estimate(self.gen, ['alpha', 'ext-cap']))
        toggle(lib, ['gamma'])
        self.assertEqual((lib.mods_changed(), lib.mods_save_refusal()), (1, b''))
        lib.mods_mark_saved()
        self.assertEqual(lib.mods_changed(), 0)
        toggle(lib, ['alpha', 'alpha', 'gamma'])  # off: changed; on again: saved
        self.assertEqual(lib.mods_changed(), 1)
        toggle(lib, ['gamma'])
        self.assertEqual(lib.mods_changed(), 0)
        self.assertEqual(shifted(lib, STOP), REDRAW)  # SHIFT+STOP: unknown bases
        self.assertEqual(
            (lib.mods_request(), lib.mods_line(), self.text()),
            (REQUEST_NONE, LINE_INFO, 'Unknown mod bases allowed for the next build.'),
        )
        self.assertEqual(shifted(lib, STOP), REDRAW)
        self.assertEqual(self.text(), 'Unknown mod bases refused.')
        self.assertEqual(shifted(lib, STOP), REDRAW)
        payload = lib.mods_payload(b'modbase', 1, 1024)
        want = MS.apply_request(
            'modbase',
            info(self.gen / 'base')['profile'],
            self.gen.name,
            [m for m in machines if m in on],
            ['cap'],
            'Mods: 3',
            True,
        )
        self.assertEqual(payload, want[:-32])
        self.assertEqual(lib.mods_payload(b'modbase', 0, 20), b'')  # does not fit: nothing

    def test_keys(self):
        lib = self.lib
        load(lib, self.gen)
        self.assertEqual(press(lib, STOP), LEAVE)
        self.assertEqual(press(lib, 6), NONE)  # other keys: nothing
        self.assertEqual(lib.mods_position(), 0)  # the first package
        self.assertEqual(press(lib, MINUS), REDRAW)  # wraps to the last row
        self.assertEqual(lib.mods_position(), lib.mods_shown() - 1)
        self.assertEqual(press(lib, PLUS), REDRAW)  # and back to the first
        self.assertEqual(lib.mods_position(), 0)
        press(lib, PLUS)
        press(lib, PLUS)
        self.assertEqual(shifted(lib, PLUS), REDRAW)  # SHIFT + +: the last row
        self.assertEqual(lib.mods_position(), lib.mods_shown() - 1)
        self.assertEqual(shifted(lib, MINUS), REDRAW)  # SHIFT + -: the first
        self.assertEqual(lib.mods_position(), 0)
        self.assertEqual(press(lib, PLUS | TURN), REDRAW)  # a turn moves as - / +
        self.assertEqual((lib.mods_position(), lib.mods_filter()), (1, 0))
        self.assertEqual(press(lib, MINUS | TURN), REDRAW)
        self.assertEqual(lib.mods_position(), 0)
        self.assertEqual(press(lib, MINUS), REDRAW)  # wraps to the last row
        self.assertEqual(lib.mods_position(), lib.mods_shown() - 1)
        lib.mods_job(1, 2, 1, 5, b'Mods: 1', 0, 0)  # building: only STOP acts
        for key in (PLAY, PLUS, MINUS, SHIFT, 6):
            self.assertEqual(press(lib, key), NONE)
        self.assertEqual(press(lib, STOP), REDRAW)
        self.assertEqual(lib.mods_request(), REQUEST_CANCEL)
        lib.mods_job(1, 2, 1, 5, b'Mods: 1', 1, 0)
        self.assertEqual(press(lib, STOP), NONE)  # once
        lib.mods_job(1, 7, 1, 3, b'Mods: 1', 0, 1)  # the commit cannot be cancelled
        for key in (PLAY, PLUS, MINUS, SHIFT, 6):
            self.assertEqual(press(lib, key), NONE)
        self.assertEqual(press(lib, STOP), REDRAW)  # said, not cancelled
        self.assertEqual(
            (lib.mods_request(), lib.mods_note()),
            (REQUEST_NONE, b'Cannot cancel now: it is being added to the library.'),
        )  # its footer
        self.assertEqual(press(lib, STOP), NONE)  # said once
        self.assertEqual(lib.mods_request(), REQUEST_NONE)
        lib.mods_ended(8, 0, 0, 0)
        self.assertEqual(lib.mods_line(), LINE_DONE)
        self.assertEqual(press(lib, STOP), LEAVE)  # the list again: STOP leaves

    def test_device_pass_reads_the_selected_row(self):
        """scripts/device_pass.py's Mods page geometry (config/pending checks the bar by screenshot) is the
        one drawn: every position of the list, the bar on mods_screen_row's visible row alone."""
        import test_device_pass

        dp = test_device_pass.dp
        lib = self.lib
        load(lib, self.gen)
        total = lib.mods_listed()
        for position in range(total):
            goto(lib, position)
            self.draw()
            rows, want = P.pixels(self.fb), dp.mods_screen_row(position, total)
            self.assertEqual(
                [r for r in range(dp.MODS_VISIBLE_ROWS) if dp.mods_row_selected(rows, r)], [want], position
            )

    def test_result_lines(self):
        lib = self.lib
        load(lib, self.gen)
        cases = [
            ((8, 2), 'Build failed: content.sha256 missing beside the base.'),
            ((8, 1), 'Build failed: the base firmware has no os.ldr.'),
            ((15, 0), 'Build failed: busy (a job, an upload or a save is running): try again.'),
            ((12, 0), 'Build failed: the storage side did not answer.'),
            ((3, 5), 'Build failed: both mod slots are in use by the default and boot-once.'),
            ((3, 0), 'Build failed: XY Forever cannot start a job now.'),
        ]
        for (status, missing), want in cases:
            lib.mods_refused(status, missing)
            self.assertEqual((self.text(), lib.mods_line()), (want, LINE_ERROR))
        lib.mods_job(7, 7, 3, 3, b'Mods: 2', 0, 1)
        lib.mods_ended(8, 0, 0, 0)
        self.assertEqual(self.text(), 'Built: Mods: 2 (content not installed yet).')
        self.assertNotIn('tmp/', self.text())
        ended = [
            ((9, 15, 4), 'Build failed: the content catalog is not the official one.'),
            ((9, 14, 18), 'Unknown mod base: SHIFT+STOP on the Mods page allows it.'),
            ((9, 12, 18), 'Unknown mod base: SHIFT+STOP on the Mods page allows it.'),
            ((9, 17, 2), 'Build failed: writing to storage failed.'),  # a commit rename
            ((9, 13, 45), 'Build failed: the mods do not fit in the mods memory.'),
            ((9, 21, 0), 'Build failed: the storage side stopped answering.'),
            ((9, 12, 3), 'Build failed: base 3.'),
            ((10, 19, 0), 'Cancelled. Nothing was kept.'),
        ]
        for (phase, error, status), want in ended:
            lib.mods_ended(phase, error, status, 0)
            self.assertEqual(self.text(), want)

    def test_previews(self):
        base = metadata_view(self.gen / 'base')
        over = re.sub(rb'module_bytes=\d+', b'module_bytes=%d' % (estimate(self.gen, ['alpha']) + 1), base)
        self.previews(Path(self.tmp.name) / 'previews', ['alpha', 'beta', 'ext-cap'], over)


KINDS = ['synth', 'drum', 'sample', 'effect', 'player', 'extension']  # xyf_mods Kind order
FILTER_ON = len(KINDS) + 1


def folder_of(package_id, kind):
    return ('ext-' if kind == KINDS.index('extension') else '') + package_id


def display_order(gen):
    """The page's display order computed here: kind (Kind order), then the shown name (22 chars, A-Z
    folded) as bytes, then the folder id; [(folder, kind index)]."""
    rows = []
    for d in gen.iterdir():
        if not d.is_dir() or d.name == 'base' or not (d / 'files.sha256').is_file():
            continue
        m = info(d)
        pid = d.name[4:] if d.name.startswith('ext-') else d.name
        name = (m.get('name') or pid).encode()[:22]
        folded = bytes(c + 32 if 65 <= c <= 90 else c for c in name)
        rows.append((KINDS.index(m['machine']), folded, pid.encode(), d.name))
    return [(r[3], r[0]) for r in sorted(rows)]


def goto(lib, position):
    for _ in range(lib.mods_listed()):
        if lib.mods_position() == position:
            return
        press(lib, MINUS)
    raise AssertionError('position %d never reached' % position)


def listed(lib):
    return [folder_of(lib.mods_list_id(r).decode(), lib.mods_list_kind(r)) for r in range(lib.mods_listed())]


def to_filter(lib, want):
    """Steps the filter (SHIFT + a knob turn clockwise) to want."""
    for _ in range(FILTER_ON + 1):
        if lib.mods_filter() == want:
            return
        assert shifted(lib, PLUS | TURN) == REDRAW
    raise AssertionError('filter %d never reached' % want)


@pytest.mark.gate
class PageOrder(Base, unittest.TestCase):
    """The order (kind, then name), the filter row (All, each kind present, On only), all shown on / off,
    and the meter counting every mod whatever the filter shows. The published set when present (previews kept
    under <private>/xy-forever/previews/mods-config/modspage-*.png), else the synthetic one."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if package_records.local_name(PACKAGES / 'base'):
            cls.gen, cls.real = PACKAGES, True
        else:
            work = Path(cls.tmp.name)
            (work / 'kit.json').write_text(json.dumps(kit_for(profile_for(stock()))))
            cls.gen = Path(
                M.split_kit(
                    work / 'kit.json', work / 'packages', finish=finish, sdk_source='https://github.com/example/sdk@abc'
                )['root']
            )
            cls.real = False
        cls.order = display_order(cls.gen)
        cls.counts = [sum(1 for _, k in cls.order if k == i) for i in range(len(KINDS))]

    def keep(self, name):
        out = CONFIG_PREVIEWS / ('modspage-%s.png' % name) if KEEP else None
        self.draw(out)

    def test_order(self):
        lib = self.lib
        load(lib, self.gen)
        self.assertEqual((lib.mods_filter(), lib.mods_listed()), (0, len(self.order)))
        self.assertEqual(listed(lib), [f for f, _ in self.order])
        kinds = [lib.mods_sorted_kind(r) for r in range(lib.mods_shown())]
        self.assertEqual(kinds, sorted(kinds))  # grouped by kind
        if self.real:
            self.assertEqual(
                dict(zip(KINDS, self.counts)),
                {'synth': 57, 'drum': 5, 'sample': 5, 'effect': 10, 'player': 2, 'extension': 2},
            )
        # the build order (requests, saves) is unchanged: machines by order=, then extensions
        rows = [lib.mods_row_id(r).decode() for r in range(lib.mods_shown())]
        orders = [int(info(self.gen / f)['order']) for f in (folder_of(r, 0) for r in rows) if (self.gen / f).is_dir()]
        self.assertEqual(orders, sorted(orders))
        # read in any folder order: the same display order
        lib.mods_reset()
        b, sv = metadata_view(self.gen / 'base'), (self.gen / 'base/services.txt').read_bytes()
        lib.mods_base(b)
        lib.mods_services(sv)
        for d in sorted((p for p in self.gen.iterdir() if p.is_dir() and p.name != 'base'), reverse=True):
            t = metadata_view(d)
            lib.mods_package(lib.mods_add(d.name.encode()), t)
        self.assertEqual(listed(lib), [f for f, _ in self.order])

    def test_filter_cycle_and_counts(self):
        lib = self.lib
        load(lib, self.gen)
        toggle(lib, [self.order[0][0], self.order[-1][0]])
        goto(lib, 0)
        seen = [(lib.mods_filter(), lib.mods_listed())]
        for _ in range(FILTER_ON + 1):  # SHIFT + turn clockwise: the next
            self.assertEqual(shifted(lib, PLUS | TURN), REDRAW)
            self.assertLess(lib.mods_position(), lib.mods_listed())
            seen.append((lib.mods_filter(), lib.mods_listed()))
            if lib.mods_filter() == 0:
                break
        want = (
            [(0, len(self.order))]
            + [(k + 1, n) for k, n in enumerate(self.counts) if n]
            + [(FILTER_ON, 2), (0, len(self.order))]
        )
        self.assertEqual(seen, want)
        back = [(lib.mods_filter(), lib.mods_listed())]
        for _ in range(FILTER_ON + 1):  # anticlockwise: the one before, wrapping
            self.assertEqual(shifted(lib, MINUS | TURN), REDRAW)
            back.append((lib.mods_filter(), lib.mods_listed()))
            if lib.mods_filter() == 0:
                break
        self.assertEqual(back, want[::-1])
        self.assertEqual(press(lib, PLUS | TURN), REDRAW)  # a turn without SHIFT moves
        self.assertEqual((lib.mods_filter(), lib.mods_position()), (0, 1))
        self.assertEqual(press(lib, PLUS), REDRAW)  # (- / + with SHIFT: first / last, not filters)
        self.assertEqual(shifted(lib, PLUS), REDRAW)
        self.assertEqual((lib.mods_filter(), lib.mods_position()), (0, len(self.order) - 1))
        goto(lib, 1)
        self.assertEqual(self.order[1][1], self.order[0][1])  # (two of the first kind)
        sel = lib.mods_list_id(1)
        self.assertEqual(shifted(lib, PLUS | TURN), REDRAW)  # that kind: the selection stays (listed)
        self.assertEqual((lib.mods_filter(), lib.mods_list_id(lib.mods_position())), (self.order[0][1] + 1, sel))
        self.assertEqual(shifted(lib, PLUS | TURN), REDRAW)  # the next kind: not listed, the first row
        self.assertNotEqual(lib.mods_filter(), self.order[0][1] + 1)
        self.assertEqual(lib.mods_position(), 0)
        for k, n in enumerate(self.counts):
            if n:
                to_filter(lib, k + 1)
                self.assertEqual(listed(lib), [f for f, kind in self.order if kind == k])
        press(lib, STOP)  # (leaves; the page keeps its state)

    def test_all_shown_on_off_touches_only_shown_rows(self):
        lib = self.lib
        load(lib, self.gen)
        outside = next(f for f, k in self.order if k != KINDS.index('effect' if self.real else 'synth'))
        toggle(lib, [outside])
        kind = KINDS.index('effect' if self.real else 'synth')
        to_filter(lib, kind + 1)
        shown = [f for f, k in self.order if k == kind]
        self.assertEqual(lib.mods_all_on(), 0)
        self.assertEqual(shifted(lib, PLAY), REDRAW)  # SHIFT + PLAY: all shown
        self.assertEqual([lib.mods_list_on(r) for r in range(lib.mods_listed())], [1] * len(shown))
        self.assertEqual(lib.mods_used(), estimate(self.gen, shown + [outside]))  # the meter counts everything
        self.assertEqual((lib.mods_changed(), lib.mods_all_on()), (1, 1))
        self.keep('type-all-on') if self.real else None
        self.assertEqual(shifted(lib, 11), REDRAW)  # all on: SHIFT + knob 2 push: all shown off
        self.assertEqual(lib.mods_used(), estimate(self.gen, [outside]))
        to_filter(lib, 0)
        shifted(lib, PLAY)  # All: every row on (over the limit)
        everything = [f for f, _ in self.order]
        self.assertEqual(lib.mods_used(), estimate(self.gen, everything))
        if self.real:
            self.assertGreater(lib.mods_used(), lib.mods_limit())
            machines = sum(1 for _, k in self.order if KINDS[k] != 'extension')  # at most 64 machines
            extensions = len(self.order) - machines
            self.assertEqual(
                lib.mods_save_refusal(),
                b'At most 64 machines in one build.'
                if machines > 64
                else b'At most 8 extensions in one build.'
                if extensions > 8
                else b'Too big for the mods memory: turn some mods off.',
            )  # refused: not saved
            self.keep('over-limit')
            self.assertEqual(self.pixel(460, 25), RED)  # the meter: over
        shifted(lib, 13)
        self.assertEqual((lib.mods_used(), lib.mods_changed()), (estimate(self.gen, []), 0))

    def test_on_only_keeps_rows_until_the_filter_changes(self):
        lib = self.lib
        load(lib, self.gen)
        on = [self.order[1][0], self.order[3][0], self.order[-2][0]]
        toggle(lib, on)
        to_filter(lib, FILTER_ON)
        self.assertEqual(listed(lib), [f for f, _ in self.order if f in on])
        toggle(lib, [on[1]])  # off while shown: stays listed
        self.assertEqual(listed(lib), [f for f, _ in self.order if f in on])
        self.assertEqual(lib.mods_used(), estimate(self.gen, [on[0], on[2]]))
        self.assertEqual(lib.mods_all_on(), 0)  # SHIFT + PLAY would turn them on
        to_filter(lib, 0)
        to_filter(lib, FILTER_ON)  # chosen again: what is on now
        self.assertEqual(listed(lib), [f for f, _ in self.order if f in (on[0], on[2])])
        toggle(lib, [on[0], on[2]])
        to_filter(lib, 0)
        to_filter(lib, FILTER_ON)
        self.assertEqual(lib.mods_listed(), 0)
        self.draw()  # "Nothing on."

    def test_previews(self):
        """All (top, and the first kind boundary), a kind, On only; (all shown on in a kind and over the limit
        after all on: test_all_shown_on_off_touches_only_shown_rows)."""
        lib = self.lib
        load(lib, self.gen)
        self.keep('all')
        self.assertEqual(lib.mods_position(), 0)
        boundary = next(i for i in range(1, len(self.order)) if self.order[i][1] != self.order[i - 1][1])
        select(lib, self.order[boundary][0])
        self.keep('all-groups')
        to_filter(lib, KINDS.index('drum') + 1 if self.counts[KINDS.index('drum')] else KINDS.index('player') + 1)
        self.keep('filter-type')
        to_filter(lib, 0)
        few = [f for f in ('fm2op', 'acidline', 'karplus', 'ext-capture') if (self.gen / f).is_dir()] or [
            self.order[0][0],
            self.order[-1][0],
        ]
        toggle(lib, few)
        to_filter(lib, FILTER_ON)
        self.keep('on-only')


@pytest.mark.gate
@unittest.skipUnless(package_records.local_name(PACKAGES / 'base'), 'published 0.0.6 package set absent')
class PublishedPage(Base, unittest.TestCase):
    """The real 0.0.6 set: 79 machines + 2 extensions; the estimate against the linker's own figures
    (test_modkit) and the 2032 KiB limit; previews kept for review."""

    gen = PACKAGES
    test_device_pass_reads_the_selected_row = (
        SyntheticPage.test_device_pass_reads_the_selected_row
    )  # 81 rows: scrolling

    def test_real_set_meter(self):
        lib = self.lib
        load(lib, self.gen)
        self.assertEqual((lib.mods_shown(), lib.mods_limit()), (81, 2080768))
        linker = {
            ('fm2op',): 148120,
            ('fm2op', 'ext-capture'): 161440,
            ('fm2op', 'ext-master_dj_filter'): 194484,
            ('fm2op', 'ext-capture', 'ext-master_dj_filter'): 202584,
        }
        for on, allocated in linker.items():
            load(lib, self.gen)
            toggle(lib, on)
            used = lib.mods_used()
            self.assertEqual(used, estimate(self.gen, on))
            self.assertTrue(0 <= used - allocated <= 256, (on, used, allocated))  # a little above the linker
        load(lib, self.gen)
        everything = sorted(p.name for p in self.gen.iterdir() if p.is_dir() and p.name != 'base')
        toggle(lib, [f for f in everything if not f.startswith('ext-')][:32])
        self.assertGreater(lib.mods_used(), 2080768)  # 32 machines: over 2032 KiB
        self.assertEqual(lib.mods_save_refusal(), b'Too big for the mods memory: turn some mods off.')

    def test_mod_memory(self):
        """The base's expanded_module_base gives the 4 MiB layout's region (module_bytes + 2 MiB = 4080 KiB);
        the Settings row flips the pending choice (changed until saved), the meter and the fit check use the pending
        layout, mods.apply's flags carry bit 1; a selection over 2032 KiB fits expanded and is refused standard with
        the existing text; shared services counted once. A base without the key (an older set): the row is
        unavailable and PLAY changes nothing. Preview mods-expanded.png in XYF_MEMORY_PREVIEW when set."""
        import os

        lib = self.lib
        STANDARD, EXPANDED, UNAVAILABLE, READING = 2, 3, 1, 0
        load(lib, self.gen)
        self.assertEqual(
            (lib.mods_limit(), lib.mods_expanded_limit(), lib.mods_memory_limit()), (2080768, 4177920, 2080768)
        )
        self.assertEqual((lib.mods_memory_row(0), lib.mods_changed()), (STANDARD, 0))
        self.assertEqual(lib.mods_flip_memory(1), b'')
        self.assertEqual(
            (lib.mods_expanded(), lib.mods_memory_row(1), lib.mods_changed(), lib.mods_memory_limit()),
            (1, EXPANDED, 1, 4177920),
        )
        machines = [
            f
            for f in sorted(p.name for p in self.gen.iterdir() if p.is_dir() and p.name != 'base')
            if not f.startswith('ext-')
        ]
        toggle(lib, machines[:32])
        used = lib.mods_used()
        self.assertTrue(2080768 < used <= 4177920, used)
        self.assertEqual((lib.mods_refusal(), lib.mods_save_refusal()), (b'', b''))  # fits the 4 MiB layout
        payload = lib.mods_payload(b'opxy-121', 1, 2048)
        self.assertEqual(int.from_bytes(payload[-4:], 'little'), 3)  # unverified | expanded
        on = list(machines[:32])
        services = {
            s.split()[0]: sum(
                int(x.split('=')[1]) for x in s.split()[1:] if x.split('=')[0] in ('code', 'data', 'state')
            )
            for s in (self.gen / 'base/services.txt').read_text().splitlines()
            if s and not s.startswith('#')
        }
        pulled = set().union(*(set(info(self.gen / f)['services'].split(',')) - {'-'} for f in on))
        self.assertEqual(lib.mods_shared(), sum(services[s] for s in pulled))
        self.assertGreater(lib.mods_shared(), 0)
        out_dir = os.environ.get('XYF_MEMORY_PREVIEW')
        if out_dir:
            self.draw(Path(out_dir) / 'mods-expanded.png')
        self.assertEqual(lib.mods_flip_memory(1), b'')  # back to standard
        self.assertEqual((lib.mods_expanded(), lib.mods_memory_limit()), (0, 2080768))
        self.assertEqual(lib.mods_save_refusal(), b'Too big for the mods memory: turn some mods off.')
        payload = lib.mods_payload(b'opxy-121', 0, 2048)
        self.assertEqual(int.from_bytes(payload[-4:], 'little'), 0)
        lib.mods_mark_saved()
        lib.mods_set_memory(1, 1)  # saved expanded: no change
        self.assertEqual(lib.mods_changed(), 0)
        lib.mods_set_memory(0, 1)
        self.assertEqual(lib.mods_changed(), 1)  # the memory alone
        # an older base (no expanded_module_base): unavailable; PLAY changes nothing; expanded can go back
        old = b''.join(
            l + b'\n'
            for l in metadata_view(self.gen / 'base').splitlines()
            if not l.startswith(b'expanded_module_base=')
        )
        load(lib, self.gen, old)
        self.assertEqual((lib.mods_expanded_limit(), lib.mods_memory_row(0)), (0, UNAVAILABLE))
        self.assertEqual(lib.mods_flip_memory(1), b'These mod packages have no 4 MiB layout.')
        self.assertEqual((lib.mods_expanded(), lib.mods_changed()), (0, 0))
        lib.mods_set_memory(1, 1)
        toggle(lib, ['fm2op'])
        self.assertEqual(lib.mods_save_refusal(), b'4 MiB mod memory needs newer mod packages: set it to 2 MiB.')
        self.assertEqual((lib.mods_memory_row(1), lib.mods_flip_memory(1)), (EXPANDED, b''))
        self.assertEqual((lib.mods_expanded(), lib.mods_changed(), lib.mods_save_refusal()), (0, 1, b''))
        lib.mods_reset()  # nothing read yet
        self.assertEqual((lib.mods_memory_row(0), lib.mods_memory_row(1)), (READING, UNAVAILABLE))
        self.assertEqual(lib.mods_flip_memory(0), b'Still reading the mod packages.')
        for bad in (
            b'0x',
            b'0x8d0d0380a',
            b'8d0d0380',
            b'0x8d2d0380',
            b'0xg0000000',
        ):  # not below module_base, or not hex
            text = (
                b''.join(
                    l + b'\n'
                    for l in metadata_view(self.gen / 'base').splitlines()
                    if not l.startswith(b'expanded_module_base=')
                )
                + b'expanded_module_base='
                + bad
                + b'\n'
            )
            load(lib, self.gen, text)
            self.assertEqual(lib.mods_expanded_limit(), 0, bad)

    def test_previews_kept(self):
        many = ['fm2op', 'acidline', 'karplus', 'ext-capture']
        over = ['fm2op', 'acidline', 'karplus', 'ext-capture'] + [
            'grains',
            'drone',
            'halo',
            'orbit',
            'solar',
            'tandem',
            'bloom',
            'chimes',
            'cirrus',
            'cocoa',
            'dust',
            'felt',
        ]
        lib = self.lib
        self.previews(REAL_PREVIEWS, many, None)
        load(lib, self.gen)
        toggle(
            lib,
            over
            + [
                'ext-master_dj_filter',
                'mdkit',
                'mmsid',
                'macroosc',
                'matrixy',
                'mach',
                'metal',
                'mmens',
                'mmnois',
                'mmpar',
            ],
        )
        self.assertGreater(lib.mods_used(), lib.mods_limit())
        self.draw(REAL_PREVIEWS / 'mods-5-over-limit.png')
        self.assertEqual(self.pixel(460, 25), RED)
        load(lib, self.gen)
        toggle(lib, many)
        self.draw(REAL_PREVIEWS / '176-mods-list.png')  # 9 rows under the compact bar
        print('\nreal-set previews: %s' % REAL_PREVIEWS, file=sys.stderr)


if __name__ == '__main__':
    unittest.main()
