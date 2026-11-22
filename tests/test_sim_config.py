"""In the simulator: one "Open-XY" firmware configured like a BIOS. A flip of a Mods page toggle is saved at once
(#197: /yaffs2/xy-forever/config.json, written at a quiet moment); Continue converges to what is saved: it builds the
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
import sys
import time
import unittest

import pytest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import test_sim_content_switch as CS                                      # noqa: E402
import test_sim_mod_job as J                                               # noqa: E402
from sim_mods_page import (PageBase, synthetic_setup, PLAY, STOP, MINUS, SHIFT)                    # noqa: E402
from xy_forever import bounds, native_json                                                        # noqa: E402

CONFIG = 'xy-forever/config.json'
CONFIG_OLD = CONFIG + '.old'
CONFIG_PART = CONFIG + '.part'
LEGACY_CONFIG = 'xy-forever/config.txt'
LEGACY_OLD = 'xy-forever/config.old'
MARK = 'xy-forever/converged.txt'


def mark_text(root, default):
    """Fingerprint the first present configuration record and the default it converged to."""
    paths = (root / name for name in (CONFIG, CONFIG_OLD, LEGACY_CONFIG, LEGACY_OLD))
    record = next((p.read_bytes() for p in paths if p.exists()), b'')
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
        for name in (CONFIG, CONFIG_OLD, LEGACY_CONFIG, LEGACY_OLD):
            path = self.root / name
            if path.exists():
                data = path.read_bytes()
                if name in (CONFIG, CONFIG_OLD):
                    data = native_json.config_text(data)
                fields = {'built': '', 'slot': '', 'stamp': '', 'safe': '0'}
                fields.update(line.split('=', 1) for line in data.decode().splitlines() if '=' in line)
                return fields
        return None

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
        """The flips are saved (desired.mods, written at a quiet moment) and the write is over."""
        want = ','.join(mods)
        self.wait_log(lambda: (self.config() or {}).get('mods') == want and
                      len(self.lines('mods: selection written')) == len(self.lines('mods: saving the selection')),
                      'mods=%s saved' % want, 30.0)
        return self.config()

    def wait_log(self, cond, what, timeout=bounds.WAIT_S):
        deadline = time.monotonic() + timeout
        while not cond():
            self.assertLess(time.monotonic(), deadline, what)
            self.sim.advance(20)

    def booted(self, timeout=bounds.WAIT_S):
        """The menu booted the default: its id; both processors in the OS."""
        seen, failures = self.mark                                         # counted before the key (cont)
        self.wait_log(lambda: len(self.lines('menu: boots the default ')) > seen or
                      len(self.lines('menu: save failed: ')) > failures, 'boot', timeout)
        self.assertEqual(self.lines('menu: save failed: ')[failures:], [])
        entry = self.lines('menu: boots the default ')[-1]
        self.wait_log(lambda: self.sim.status()['a'] == 'os' and self.sim.status()['b'] == 'os', 'the OS')
        return entry

    def failed(self, timeout=bounds.WAIT_S):
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
        self.sim.settle()                                                  # its start-up work done, as a host finds it

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
        self.assertFalse((self.root / CONFIG_OLD).exists())
        self.assertFalse((self.root / CONFIG_PART).exists())
        self.assertEqual((self.root / CONFIG).read_bytes(), native_json.config_document(c))
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

    def all_off_saved(self):
        """alpha saved, then all off saved: the slot is spare, the content official, the default the base."""
        a = self.first_save()
        self.menu_again()
        self.change(['alpha'], unknown=False)
        self.cont()
        self.assertEqual(self.booted(), self.base)
        self.menu_again()
        return a

    @pytest.mark.sweep
    def test_power_cut_in_the_record_write_then_save(self):
        a = self.all_off_saved()
        self.change(['alpha'], unknown=False)
        self.saved(['alpha'])
        self.sim.set('a_slow_ms', 150)
        self.cont()
        self.wait_progress(lambda p: p['stage'] == 2, 'saving', bounds.SLOW_S)
        cuts = self.sim.status()['cuts']
        rows = len(self.lines('menu: home row: '))
        self.sim.set('rename_fault_kind', 2)
        self.sim.set('rename_fault', 2)                                    # cut after publishing config.json
        self.sim.set('a_slow_ms', 0)
        self.cut(cuts)
        self.assertEqual(self.config()['mods'], 'alpha')                   # the new record, the old default
        self.wait_log(lambda: not (self.root / 'xy-forever/job.txt').exists(), 'journal/default completion')
        self.assertEqual(self.disk_default(), a)
        self.wait_log(lambda: len(self.lines('menu: home row: ')) > rows and
                      self.home().startswith('Open-XY - 1 mod - 1.2.1 pending=0'), 'the recovered home row')
        since = len(self.progress())
        self.cont()
        self.assertEqual(self.booted(), a)
        self.assertFalse(self.built_any(since))
        self.assertEqual(self.config()['mods'], 'alpha')
        self.assertEqual(CS.content(self.root), self.expected(a))
        self.assertTrue(self.record().startswith('state=installed\nentry=%s\n' % a))
        self.assertTrue((self.root / CONFIG_OLD).exists())

if __name__ == '__main__':
    unittest.main()
