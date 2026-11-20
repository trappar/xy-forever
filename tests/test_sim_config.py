"""In the simulator: one "Open-XY" firmware configured like a BIOS. A flip of a Mods page toggle is saved at once
(#197: /yaffs2/xy-forever/config.txt, written at a quiet moment); Continue converges to what is saved: it builds the
selection into the spare mods slot when needed, installs its content, writes the record and the default, and boots
it. Driven by injected keys; what boots is the simulator's "menu: boots the default" line and both processors in
the OS, with /yaffs2/content/ compared byte for byte with the entry. Covers: a first save and a plain power-on that
writes nothing; a rebuild into the other slot; no change boots at once; a saved flip kept over a power cycle; all
off (official content, the base the default, no build) and the same mods on again (no rebuild: the slot named by
slot= still holds what built= says); a build failure, a content switch failure and a power cut in the build (the
next plain power-on converges), the switch and the record write (the menu opens and Continue again repairs it); a
selection that cannot be built. Synthetic packages (test_sim_mod_job's kit). Needs clang++ and the private stock 1.2.1 images
(skipped without them). No device access."""
import hashlib
from pathlib import Path
import re
import sys
import struct
import time
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import test_sim_content_switch as CS                                      # noqa: E402
import test_sim_mod_job as J                                               # noqa: E402
from test_sim_mods_page import (PageBase, synthetic_setup, PLAY, STOP, MINUS, PLUS, SHIFT, VIEW_LIST)   # noqa: E402

CONFIG = 'xy-forever/config.txt'
MARK = 'xy-forever/converged.txt'


def mark_text(root, default):
    """converged.txt as a convergence writes it (#197): the saved record's SHA-256 (config.txt, else config.old, else
    none) and the default it converged to."""
    record = next((p.read_bytes() for p in (root / CONFIG, root / 'xy-forever/config.old') if p.exists()), b'')
    return 'format=1\ninputs=%s\ndefault=%s\n' % (hashlib.sha256(record).hexdigest(), default)


class ConfigBase(PageBase, CS.Switch):
    """The helpers (test_sim_safemode uses them too)."""
    base = 'modbase'
    unverified = True

    @classmethod
    def setUpClass(cls):
        synthetic_setup(cls)
        cls.seed_content()

    # -- reading the device's files ----------------------------------------------------------------
    def config(self):
        path = self.root / CONFIG
        if not path.exists():
            return None
        return dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line)

    def disk_default(self):
        """The default the state slots on disk name (the newer valid slot), as A's scan picks it."""
        best = (-1, None)
        for name in ('state-a.txt', 'state-b.txt'):
            path = self.root / 'xy-forever' / name
            if not path.exists():
                continue
            text = path.read_bytes()
            body, _, crc = text.rpartition(b'crc32=')
            if crc.strip() != b'%08x' % (zlib.crc32(body) & 0xffffffff):
                continue
            f = dict(line.split('=', 1) for line in body.decode().splitlines() if '=' in line)
            best = max(best, (int(f['seq']), f['default']))
        return best[1]

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file()}

    def home(self):
        rows = self.lines('menu: home row: ')
        return rows[-1] if rows else ''

    # -- keys at the device ------------------------------------------------------------------------
    def change(self, folders, unknown=True):
        """Open the Mods page, (allow the unknown synthetic base), toggle folders, back to the home menu."""
        self.open_page()
        if unknown and not self.page()['text'].startswith('Unknown mod bases allowed'):
            self.key(SHIFT, 1); self.press(STOP); self.key(SHIFT, 0)       # SHIFT + STOP
            self.wait(lambda p: p['text'].startswith('Unknown mod bases allowed'), 'unknown base allowed', 5.0)
        self.toggle(folders)
        self.press(STOP)
        self.wait(lambda p: p['shown'] == 0, 'left the page', 5.0)
        for _ in range(2):                                                 # Mods row -> Continue
            self.press(MINUS)

    def cont(self):
        """PLAY on the first row (Continue): it converges to what is saved and boots (#197: no dialog)."""
        self.mark = len(self.lines('menu: boots the default ')), len(self.lines('menu: save failed: '))
        self.key(PLAY, 1)                                                  # it boots: no release reaches B

    def saved(self, mods):
        """The flips are saved (config.txt mods=, written at a quiet moment) and the write is over."""
        want = ','.join(mods)
        self.wait_log(lambda: (self.config() or {}).get('mods') == want and
                      len(self.lines('mods: selection written')) == len(self.lines('mods: saving the selection')),
                      'mods=%s saved' % want, 30.0)
        return self.config()

    def wait_log(self, cond, what, timeout=60.0):
        deadline = time.monotonic() + timeout
        while not cond():
            self.assertLess(time.monotonic(), deadline, what)
            time.sleep(0.02)

    def booted(self, timeout=180.0):
        """The menu booted the default: its id; both processors in the OS."""
        seen, failures = self.mark                                         # counted before the key (cont)
        self.wait_log(lambda: len(self.lines('menu: boots the default ')) > seen or
                      len(self.lines('menu: save failed: ')) > failures, 'boot', timeout)
        self.assertEqual(self.lines('menu: save failed: ')[failures:], [])
        entry = self.lines('menu: boots the default ')[-1]
        self.wait_log(lambda: self.sim.status()['a'] == 'os' and self.sim.status()['b'] == 'os', 'the OS')
        return entry

    def failed(self, timeout=180.0):
        seen = self.mark[1]
        self.wait_log(lambda: len(self.lines('menu: save failed: ')) > seen, 'the save fails', timeout)
        return self.lines('menu: save failed: ')[-1]

    def menu_again(self):
        """A power cycle with PLAY: the menu, USB on."""
        rows = len(self.lines('menu: home row: '))                        # rows of earlier boots do not count
        self.sim.control('power play')
        self.sim.wait_listening(30.0)
        self.reconnect()
        self.assertEqual(self.dev.hello()[0], 0)
        self.wait_log(lambda: len(self.lines('menu: home row: ')) > rows, 'the home row')

    def plain_boots_without_writing(self, entry):
        before = self.snapshot()
        self.sim.control('power plain')
        self.wait_log(lambda: self.sim.status()['a'] == 'os' and self.sim.status()['b'] == 'os', 'plain boot')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.disk_default(), entry)

    def saved_as(self, entry, mods):
        c = self.config()
        self.assertEqual(c['mods'].split(',') if c['mods'] else [], mods)
        self.assertEqual(self.disk_default(), entry)
        self.assertEqual(CS.content(self.root), self.expected(None if entry == self.base else entry))
        record = self.record()
        if entry == self.base:
            self.assertIn(record, (None, 'state=official\n'))
        else:
            self.assertTrue(record.startswith('state=installed\nentry=%s\n' % entry), record)
        self.assertFalse((self.root / 'xy-forever/config.old').exists())
        self.assertFalse((self.root / 'xy-forever/config.txt.part').exists())
        return c

    def built_any(self, since):
        """A sequence built (its progress screen had save_build) since progress line `since` (len(self.progress()))."""
        return any(p['save'] & 2 for p in self.progress()[since:])

    def first_save(self, mods=('alpha',)):
        self.change(list(mods))
        self.wait_log(lambda: 'pending=1' in self.home(), 'pending')
        self.cont()
        entry = self.booted()
        self.assertIn(entry, ('mods-a', 'mods-b'))
        self.saved_as(entry, list(mods))
        return entry

    def cut(self, cuts):
        """The power cut after `cuts` (read before the fault was armed) and the restart, USB answering."""
        self.wait_log(lambda: self.sim.status()['cuts'] == cuts + 1 and self.sim.status()['listening'], 'cut, restart')
        self.reconnect()
        self.assertEqual(self.dev.hello()[0], 0)



@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class Config(ConfigBase, unittest.TestCase):
    # -- the tests ---------------------------------------------------------------------------------
    def pending_save_rehearsal(self):
        from xy_forever import a_rehearsal as R, sim
        self.sim.set('defer_save_boot', 1)
        self.change(['alpha'])
        self.cont()
        self.wait_log(lambda: self.lines('menu: deferred Save boot'), 'actual Save completed before boot')
        self.assertEqual(self.dev.stage(sim.STOCK_OS.read_bytes()), 0)
        extra = struct.pack('<II', 1, 120000) + hashlib.sha256(sim.STOCK_OS.read_bytes()).digest()
        return R, extra

    def test_pending_save_boot_waits_for_rehearsal_cancel_then_resumes(self):
        R, extra = self.pending_save_rehearsal()
        self.sim.set('a_slow_ms', 1000)
        self.assertEqual(R.call(self.dev, 0, extra)[0], 0)
        self.sim.set('defer_save_boot', 0)
        self.assertEqual(self.dev.call(2)[0], 0)  # USB/status remains available with pending Save
        self.assertFalse(self.lines('menu: saved'))
        self.assertEqual(R.call(self.dev, 2)[0], 0)
        self.sim.set('a_slow_ms', 0)
        self.assertIn(self.booted(), ('mods-a', 'mods-b'))  # pending user action was retained

    def test_pending_save_boot_stays_pending_after_rehearsal_link_failure(self):
        R, extra = self.pending_save_rehearsal()
        self.sim.set('a_silent', 1)
        before = self.sim.status()
        self.assertEqual(R.call(self.dev, 0, extra)[0], 0)
        self.sim.set('defer_save_boot', 0)
        self.sim.jump(5001)
        self.wait_log(lambda: R.call(self.dev, 1)[1]['phase'] == 0, 'rehearsal link failure')
        self.assertEqual(R.call(self.dev, 1)[1]['result'], 12)
        self.assertFalse(self.lines('menu: saved'))
        self.assertEqual(self.dev.call(2)[0], 0)
        after = self.sim.status()
        for key in ('a_boots', 'b_boots', 'storage_ops', 'raw_boot_changes'):
            self.assertEqual(after[key], before[key], key)

    def test_save_boot_power_on_rebuild_and_no_change(self):
        self.wait_log(lambda: 'pending=0' in self.home(), 'the home row')
        self.change(['alpha', 'gamma'])
        self.saved(['alpha', 'gamma'])
        self.wait_log(lambda: self.home().startswith('Open-XY - 2 mods - 1.2.1 (rebuild) pending=1'), 'the mark')
        self.cont()
        a = self.booted()
        c = self.saved_as(a, ['alpha', 'gamma'])
        self.assertEqual((c['base'], c['slot'], c['unverified']), (self.base, a, '1'))
        self.assertEqual(c['stamp'], dict(l.split('=', 1) for l in (self.entry(a) / 'info.txt').read_text().splitlines()
                                          if '=' in l)['sha256'])
        self.plain_boots_without_writing(a)
        self.menu_again()
        self.wait_log(lambda: self.home().startswith('Open-XY - 2 mods - 1.2.1 pending=0'),
                      'the completed home row')
        self.assertTrue(self.home().startswith('Open-XY - 2 mods - 1.2.1 pending=0'), self.home())
        p = self.open_page()
        self.assertEqual((p['on'], p['changed']), (['alpha', 'gamma'], 0))   # the saved toggles
        self.press(STOP)
        self.wait(lambda p: p['shown'] == 0, 'left', 5.0)
        self.press(MINUS); self.press(MINUS)                              # back on Continue
        self.change(['ext-cap'], unknown=False)
        self.cont()
        b = self.booted()
        self.assertEqual({a, b}, {'mods-a', 'mods-b'})                    # rebuilt into the other slot
        self.saved_as(b, ['alpha', 'gamma', 'ext-cap'])
        self.assertTrue((self.entry(a) / 'os.ldr').is_file())              # the old slot: spare
        self.menu_again()
        self.wait_log(lambda: self.home().endswith('safe=Boot without mods'),   # once the record is read
                      'the safe-mode row')
        before, saves = self.snapshot(), len(self.lines('menu: save'))
        self.cont()                                            # no change: boots at once
        self.assertEqual(self.booted(), b)
        self.assertEqual(len(self.lines('menu: save')), saves)
        self.assertEqual(self.snapshot(), before)

    def test_a_saved_flip_kept_all_off_and_on_again(self):
        a = self.first_save()
        self.menu_again()
        self.change(['gamma'], unknown=False)
        self.saved(['alpha', 'gamma'])                                     # nothing built, nothing booted
        self.assertEqual((self.sim.status()['b'], self.disk_default()), ('running', a))
        self.menu_again()                                                  # a power cycle: the flip is kept
        self.wait_log(lambda: self.home().startswith('Open-XY - 2 mods - 1.2.1 (rebuild) pending=1'), 'kept')
        self.change(['gamma'], unknown=False)                              # off again: as built
        self.saved(['alpha'])
        self.wait_log(lambda: 'pending=0' in self.home(), 'nothing to apply')
        self.change(['alpha'], unknown=False)                              # all off
        self.saved([])
        self.assertIn('Base (to apply)', self.home())                      # the base entry's name (seeded 'Base')
        since = len(self.progress())
        self.cont()
        self.assertEqual(self.booted(), self.base)
        self.assertFalse(self.built_any(since))
        c = self.saved_as(self.base, [])
        self.assertEqual(c['slot'], a)                                     # kept as the spare Open-XY
        self.menu_again()
        self.change(['alpha'], unknown=False)                              # the same mods on again: no rebuild
        since = len(self.progress())
        self.cont()
        self.assertEqual(self.booted(), a)
        self.assertFalse(self.built_any(since))
        self.saved_as(a, ['alpha'])

    def test_build_failure_then_save_again(self):
        self.change(['alpha'])
        self.saved(['alpha'])                                              # (its own write: before the fault)
        self.sim.set('rename_fault_kind', 1)
        self.sim.set('rename_fault', 1)                                    # the job's first rename fails
        self.cont()
        self.assertIn('Build failed', self.failed())
        self.assertEqual((self.config()['mods'], self.config()['slot'], self.disk_default()), ('alpha', '', self.base))
        self.assertEqual(CS.content(self.root), self.expected(None))       # the selection saved, nothing else
        self.wait_log(lambda: '(rebuild) pending=1' in self.home(), 'still pending')
        self.sim.set('rename_fault', 0)
        self.cont()
        self.saved_as(self.booted(), ['alpha'])

    def test_stop_cancels_the_build(self):
        self.change(['alpha'])
        self.sim.set('a_slow_ms', 30)
        self.cont()
        self.wait_progress(lambda p: p['save'] & 2, 'building', 60.0)
        self.press(STOP)
        self.assertIn('Cancelled', self.failed())
        self.sim.set('a_slow_ms', 0)
        self.assertEqual((self.config()['mods'], self.config()['slot'], self.disk_default()), ('alpha', '', self.base))
        self.wait_log(lambda: '(rebuild) pending=1' in self.home(), 'still pending')

    def test_continue_waits_for_a_usb_job(self):
        """with a USB build running, PLAY on the first row shows the busy line and does nothing (the flip saved,
        nothing built); PLAY pressed while the converge's own build runs is its progress screen (no busy line) and it
        boots."""
        busy = 'menu: line: Busy: a build or content change is running.'
        self.change(['alpha'])
        self.saved(['alpha'])
        self.sim.set('a_slow_ms', 4)
        job = self.start(['gamma'])
        self.until_phase(job, 2)
        for n in (1, 2):
            self.cont()
            self.key(PLAY, 0)
            self.wait_log(lambda: len(self.lines(busy)) == n, 'the busy line %d' % n, 10.0)
        self.assertEqual(self.lines('menu: apply'), [])
        self.sim.set('a_slow_ms', 0)
        self.assertEqual(J.MS.PHASES[self.finished(job)['phase']], 'done')
        self.assertEqual((self.config()['slot'], self.disk_default()), ('', self.base))
        self.wait_log(lambda: '(rebuild) pending=1' in self.home(), 'still pending')
        self.sim.set('a_slow_ms', 30)
        self.cont()
        self.wait_progress(lambda p: p['save'] & 2, 'building', 60.0)
        self.press(PLAY)                                                   # on the converge's own progress screen
        self.sim.set('a_slow_ms', 0)
        self.saved_as(self.booted(), ['alpha'])
        self.assertEqual(len(self.lines(busy)), 2)

    def all_off_saved(self):
        """alpha saved, then all off saved: the slot is spare, the content official, the default the base."""
        a = self.first_save()
        self.menu_again()
        self.change(['alpha'], unknown=False)
        self.cont()
        self.assertEqual(self.booted(), self.base)
        self.menu_again()
        return a

    def test_switch_failure_then_save_again_repairs(self):
        a = self.all_off_saved()
        self.change(['alpha'], unknown=False)
        record = self.saved(['alpha'])                                     # saved at once (built= still alpha's)
        self.sim.set('rename_fault_kind', 1)
        self.sim.set('rename_fault', 1)                                    # no build: the switch's first rename
        self.cont()
        self.assertIn('Installing content failed', self.failed())
        self.assertEqual((self.config(), self.disk_default()), (record, self.base))
        self.wait_log(lambda: 'pending=1' in self.home(), 'still pending')
        self.sim.set('rename_fault', 0)
        self.cont()
        self.assertEqual(self.booted(), a)
        self.saved_as(a, ['alpha'])

    def test_power_cut_in_the_build_then_power_on_converges(self):
        self.change(['alpha'])
        self.saved(['alpha'])
        self.sim.set('rename_fault_kind', 2)
        self.sim.set('rename_fault', 1)
        cuts = self.sim.status()['cuts']
        self.cont()
        self.cut(cuts)                                                     # back in the menu (PLAY held)
        self.until_swept()                                                 # its USB start sweeps tmp/job-* (#3)
        self.assertEqual((self.config()['mods'], self.config()['slot']), ('alpha', ''))   # saved, never built
        self.assertEqual(self.disk_default(), self.base)                  # the previous setup is in place
        self.sim.set('rename_fault', 0)
        self.mark = len(self.lines('menu: boots the default ')), len(self.lines('menu: save failed: '))
        self.sim.control('power plain')                                    # #197: the saved inputs converge first
        a = self.booted()
        self.saved_as(a, ['alpha'])
        self.plain_boots_without_writing(a)

    def test_power_cut_in_the_switch_then_save_repairs(self):
        a = self.all_off_saved()
        self.change(['alpha'], unknown=False)
        self.saved(['alpha'])
        self.sim.set('rename_fault_kind', 2)
        self.sim.set('rename_fault', 1)
        cuts = self.sim.status()['cuts']
        self.cont()
        self.cut(cuts)
        self.assertEqual(self.config()['mods'], 'alpha')                   # the flip, saved before the switch
        self.wait_log(lambda: not (self.root / 'xy-forever/job.txt').exists(), 'automatic content replay')
        self.assertTrue(self.record().startswith('state=installed'), self.record())
        # The replay ends the switch with its record and default: what is saved (the flip) is in place.
        self.wait_log(lambda: self.home().startswith('Open-XY - 1 mod - 1.2.1 pending=0'), 'the replay completed it')
        self.assertEqual(self.disk_default(), a)
        self.cont()
        self.assertEqual(self.booted(), a)
        self.saved_as(a, ['alpha'])

    def test_power_cut_in_the_record_write_then_save(self):
        a = self.all_off_saved()
        self.change(['alpha'], unknown=False)
        self.saved(['alpha'])
        self.sim.set('a_slow_ms', 150)
        self.cont()
        self.wait_progress(lambda p: p['stage'] == 2, 'saving', 120.0)
        cuts = self.sim.status()['cuts']
        self.sim.set('rename_fault_kind', 2)
        self.sim.set('rename_fault', 2)                                    # cut after .part -> config.txt
        self.sim.set('a_slow_ms', 0)
        self.cut(cuts)
        self.assertEqual(self.config()['mods'], 'alpha')                   # the new record, the old default
        self.wait_log(lambda: not (self.root / 'xy-forever/job.txt').exists(), 'journal/default completion')
        self.assertEqual(self.disk_default(), a)
        self.wait_log(lambda: 'pending=0' in self.home(), 'the completed saved configuration')
        since = len(self.progress())
        self.cont()
        self.assertEqual(self.booted(), a)
        self.assertFalse(self.built_any(since))
        self.assertEqual(self.config()['mods'], 'alpha')
        self.assertEqual(CS.content(self.root), self.expected(a))
        self.assertTrue(self.record().startswith('state=installed\nentry=%s\n' % a))
        # The cut was before config.old cleanup. Its surviving old copy is harmless; no config rewrite was needed.
        self.assertTrue((self.root / 'xy-forever/config.old').exists())

    def test_a_selection_that_cannot_be_built(self):
        """Saved all the same; the first row's line says why; Continue fails with it on the progress screen's end and
        nothing else changes."""
        self.change(['ext-cap'])                                           # an extension alone
        self.saved(['ext-cap'])
        self.wait_log(lambda: 'note=Turn on at least one machine first.;' in self.home(), 'the first row\'s line')
        self.assertIn('pending=1', self.home())
        self.cont()
        self.assertIn('Turn on at least one machine first.', self.failed())
        self.assertEqual((self.config()['slot'], self.disk_default()), ('', self.base))
        self.assertEqual(CS.content(self.root), self.expected(None))


if __name__ == '__main__':
    unittest.main()
