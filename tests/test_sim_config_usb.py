"""In the simulator: the saved inputs over USB (#26, config.get and config.set). A set is a flip on the Mods page: the
device reads its package list again (busy until then), checks the selection and saves it, or refuses with the reason
and changes nothing; it never builds, and the next boot converges to it (#197). Safe mode is a field of config.set
(tested here at the protocol); xyf_host.py never sets it and sends it back as saved. The developer path (a mod build
of my choosing made the default and booted once, behind the saved inputs) leaves them as they are: the next plain
power-on returns to the saved setup. xyf_host.py mods status / set. Synthetic packages (test_sim_config). Needs
clang++ and the private stock 1.2.1 images (skipped without them). No device access."""
import contextlib
import io
from pathlib import Path
import struct
import sys
import unittest
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import test_sim_mod_job as J                                               # noqa: E402
from test_sim_config import CONFIG, ConfigBase                             # noqa: E402
import xyf_host as host                                                    # noqa: E402
from xy_forever import modconfig as MC, modsend as MS                      # noqa: E402
from xy_forever import usb_proto_ids as ids                                # noqa: E402

UNKNOWN = MC.UNVERIFIED                                                    # the synthetic base is not a known one


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class ConfigUsb(ConfigBase, unittest.TestCase):
    def get(self):
        code, f = MC.get(self.dev)
        self.assertEqual(code, 0, ids.STATUS_NAMES[code])
        return f

    def set(self, mods, flags=UNKNOWN, expect=0):
        code, reason = MC.set_inputs(self.dev, mods, flags)
        self.assertEqual(code, expect, '%s: %s' % (ids.STATUS_NAMES[code], reason))
        return reason

    def written(self, mods):
        f = MC.written(self.dev)
        self.assertEqual((f['saving'], f['mods']), (0, mods))
        self.assertEqual(self.saved(mods)['mods'], ','.join(mods))
        return f

    def cli(self, *argv):
        """xyf_host.py mods ARGV against the simulator, on its own connection: (exit code, output)."""
        self.dev.t.close()
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), warnings.catch_warnings():
                warnings.simplefilter('ignore', ResourceWarning)
                code = host.main(['--sim', self.sim.address, 'mods'] + list(argv))
        finally:
            self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        return code, out.getvalue()

    def plain(self):
        """A plain power-on: the library entry that boots (it converges first when the inputs changed)."""
        seen = len(self.lines('menu: boots the default '))
        self.sim.control('power plain')
        self.wait_log(lambda: self.sim.status()['a'] == 'os' and self.sim.status()['b'] == 'os', 'the OS', 300.0)
        self.assertEqual(self.lines('menu: save failed: '), [])
        return self.lines('menu: boots the default ')[-1] if len(self.lines('menu: boots the default ')) > seen else None

    def test_set_status_never_builds_then_power_on_builds(self):
        self.wait_log(lambda: 'pending=' in self.home(), 'the home row')
        f = self.get()
        self.assertEqual((f['record'], f['count'], f['mods'], f['saving'], f['base']), (0, 0, [], 0, self.base))
        before, progress = self.snapshot(), len(self.progress())
        code, body = self.dev.call(ids.CONFIG_SET, MC.request(['alpha', 'ext-cap'], UNKNOWN))
        self.assertEqual(code, ids.S_BUSY)                                 # the package list is read first
        self.set(['alpha', 'ext-cap'])
        f = self.written(['alpha', 'ext-cap'])
        self.assertEqual((f['record'], f['count'], f['flags'], f['generation']), (1, 2, UNKNOWN, self.generation))
        changed = {k for k, v in self.snapshot().items() if before.get(k) != v}
        self.assertEqual(changed, {CONFIG})                                # only the record: nothing built
        self.assertEqual((self.config()['slot'], self.config()['safe'], self.disk_default()), ('', '0', self.base))
        self.assertEqual(len(self.progress()), progress)                   # no progress screen, no job
        self.wait_log(lambda: '(rebuild) pending=1' in self.home(), 'the first row: (rebuild)')
        p = self.open_page()                                               # the page shows it as saved
        self.assertEqual((sorted(p['on']), p['changed']), (['alpha', 'ext-cap'], 0))
        entry = self.plain()                                               # the next boot builds it
        self.assertIn(entry, ('mods-a', 'mods-b'))
        self.saved_as(entry, ['alpha', 'ext-cap'])

    def test_each_refusal_changes_nothing(self):
        self.wait_log(lambda: 'pending=' in self.home(), 'the home row')
        before = self.snapshot()
        for mods, flags, reason in (
                (['nosuch'], UNKNOWN, 'Not in the mod packages: nosuch'),
                (['ext-cap'], UNKNOWN, 'Turn on at least one machine first.'),
                (['alpha'], UNKNOWN | MC.EXPANDED, '4 MiB mod memory needs newer mod packages: set it to 2 MiB.'),
                (['alpha'], UNKNOWN | MC.SAFE, 'Nothing is built yet to boot without: set no mods instead.')):
            self.assertEqual(self.set(mods, flags, ids.S_REFUSED), reason)
        body = MC.request(['alpha'], UNKNOWN)
        self.assertEqual(self.dev.call(ids.CONFIG_SET, body[:-1] + bytes([body[-1] ^ 1]))[0], ids.S_SHA)
        self.assertEqual(self.dev.call(ids.CONFIG_SET, struct.pack('<II', 1, 8) + b'\0' + bytes(32))[0], ids.S_BAD_REQUEST)
        self.assertEqual(self.dev.call(ids.CONFIG_GET, struct.pack('<I', 2))[0], ids.S_UNSUPPORTED)
        self.assertEqual(self.snapshot(), before)
        f = self.get()
        self.assertEqual((f['record'], f['saving']), (0, 0))
        p = self.open_page()
        self.assertEqual((p['on'], p['changed']), ([], 0))                 # the toggles back as saved
        # No stock firmware in the library (every entry moved away; PLAY at power-on only opens the menu): no base.
        (self.root / 'xy-forever/firmware').rename(self.root / 'xy-forever/away')
        (self.root / 'xy-forever/firmware').mkdir()
        self.menu_again()
        self.assertEqual(self.set(['alpha'], UNKNOWN, ids.S_REFUSED), 'No stock OP-XY firmware in the library.')
        self.assertFalse((self.root / CONFIG).exists())

    def test_safe_mode_off_and_on(self):
        a = self.first_save()
        self.menu_again()
        self.set(['alpha'], UNKNOWN | MC.SAFE)                             # Boot without mods (the protocol's field)
        f = self.written(['alpha'])
        self.assertEqual((f['flags'], self.config()['safe'], self.disk_default()), (UNKNOWN | MC.SAFE, '1', a))
        self.assertEqual(self.plain(), self.base)                          # the next boot: the base, official content
        self.saved_as(self.base, ['alpha'])
        self.menu_again()
        code, out = self.cli('set', 'alpha')                               # the host tool keeps safe mode as saved
        self.assertEqual(code, 0, out)
        self.assertEqual((self.written(['alpha'])['flags'], self.config()['safe']), (UNKNOWN | MC.SAFE, '1'))
        progress = len(self.progress())
        self.set(['alpha'])                                                # safe mode off: the selection again
        self.assertEqual(self.written(['alpha'])['flags'], UNKNOWN)
        self.assertEqual(self.plain(), a)                                  # the slot still holds it: no rebuild
        self.assertFalse(self.built_any(progress))
        self.saved_as(a, ['alpha'])

    def test_developer_boot_once_then_power_on_returns(self):
        """A mod build of my choosing (mods.apply), made the default (library.set_default) and booted once
        (library.boot), as `mods dev --boot` and scripts/test_entry.py --boot do: the saved inputs never change, and
        the next plain power-on converges back to the saved setup."""
        a = self.first_save()
        self.menu_again()
        record = (self.root / CONFIG).read_bytes()
        code, job, _ = MS.start(self.dev, self.base, self.profile, self.generation, ['alpha', 'gamma'], unverified=True)
        self.assertEqual(code, 0)
        dev_entry = MS.wait(self.dev, job, out=lambda line: None)['detail']
        self.assertNotEqual(dev_entry, a)
        self.assertEqual(self.dev.set_default(dev_entry)[0], ids.S_STARTED)  # its content first (a job)
        self.assertEqual(MS.wait(self.dev, 0, out=lambda line: None)['phase'], MS.DONE)
        MS.served(self.dev, out=lambda line: None)
        self.assertEqual(self.disk_default(), dev_entry)
        self.assertEqual(self.dev.library_boot(dev_entry), 0)
        self.wait_log(lambda: self.sim.status()['a'] == 'os' and self.sim.status()['b'] == 'os', 'booted once')
        self.assertEqual((self.root / CONFIG).read_bytes(), record)        # the saved inputs as they were
        self.assertEqual(self.plain(), a)                                  # converged back, then booted
        self.assertEqual((self.root / CONFIG).read_bytes(), record)
        self.saved_as(a, ['alpha'])

    def test_cli(self):
        self.wait_log(lambda: 'pending=' in self.home(), 'the home row')
        code, out = self.cli('set', 'alpha', '--ext', 'cap', '--unverified')
        self.assertEqual(code, 0, out)
        self.assertIn('mods set: saved', out)
        code, out = self.cli('status')
        self.assertEqual(code, 0, out)
        self.assertIn('mods: alpha, ext-cap', out)
        self.assertNotIn('safe mode', out)
        code, out = self.cli('set', 'nosuch')
        self.assertEqual(code, ids.S_REFUSED, out)
        self.assertIn('refused: Not in the mod packages: nosuch', out)
        code, out = self.cli('set')                                        # no mods
        self.assertEqual(code, 0, out)
        self.assertIn('saved: 0 mods on %s' % self.base, self.cli('status')[1])
        for gone in ('off', 'on'):                                         # safe mode stays on the device
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                host.main(['--sim', self.sim.address, 'mods', gone])

if __name__ == '__main__':
    unittest.main()
