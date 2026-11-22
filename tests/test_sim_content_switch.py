"""The content switch on processor B in the simulator: library.set_default of an entry whose content
is not installed makes /yaffs2/content/ match it first (a job), then writes the default.

official -> mods set A -> mods set B -> official, /yaffs2/content/ compared byte for byte (files and folders)
with the expected tree at each step: official files untouched, the catalog exact, A's own files gone under B,
everything of the mods gone at the end, content/dlc/ and an unknown file left alone. A fault, a power cut and A
going silent at every rename of a switch: the record says the old state, switching or the new one (never
absent), nothing is bootable while it says switching, B answers, the window closes, the default stays; a repeat
repairs. Refusals before any change; boot once refused; a plain power-on with content not usable changes no content,
record or default (#197: it converges first, nothing to apply with no setup saved) and the menu says why.
Synthetic packages (test_sim_mod_job.py's kit) always; the published 0.0.6 set when present. Needs clang++ and
the private stock 1.2.1 images (skipped without them). No device access."""

import hashlib
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import pytest
from test_modpatch import CATALOG, OFFICIAL, finish, kit_for, profile_for, stock
from test_sim_mod_job import LIB, UNAVAILABLE, Base

from xy_forever import bounds
from xy_forever import modkit as M
from xy_forever import modpatch as MP
from xy_forever import modsend as MS
from xy_forever import usb_proto as P
from xy_forever import usb_proto_ids as ids
from xy_forever import xyf_host as host

RECORD = 'xy-forever/content-state.txt'
SAVED = 'xy-forever/content-official'
STRAY = {'dlc/pack.bin': b'a dlc pack', 'stray.txt': b'not ours'}
PLAY, PLUS, SHIFT = 51, 54, 55


def official_list():
    """content.sha256 of the seeded official content: each file's bytes are its path, the catalog CATALOG."""
    body = {p: (CATALOG if p == 'op1_factory.json' else p.encode()) for p in OFFICIAL}
    return body, ''.join('%s  %s\n' % (hashlib.sha256(body[p]).hexdigest(), p) for p in sorted(body))


def content(root):
    """/yaffs2/content/: {path: bytes} and the set of folders."""
    c = Path(root) / 'content'
    return (
        {p.relative_to(c).as_posix(): p.read_bytes() for p in c.rglob('*') if p.is_file()},
        {p.relative_to(c).as_posix() for p in c.rglob('*') if p.is_dir()},
    )


class Switch(Base):
    @classmethod
    def seed_content(cls):
        body, text = official_list()
        for path, data in {**body, **STRAY}.items():
            target = cls.seeded / 'content' / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (cls.seeded / 'xy-forever/firmware' / cls.base / 'content.sha256').write_text(text)
        cls.official = content(cls.seeded)
        cls.official_text = text

    def reconnect(self):
        self.dev.t.close()
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)

    def build(self, machines, exts=()):
        f = self.finished(self.start(machines, exts))
        self.assertEqual(MS.PHASES[f['phase']], 'done', f)
        return f['detail']

    def expected(self, entry):
        """content/ after a finished switch to entry (None: the official content)."""
        files, dirs = dict(self.official[0]), set(self.official[1])
        if entry:
            mine = content(self.root / LIB / entry)
            files.update(mine[0])
            dirs |= mine[1]
        return files, dirs

    def record(self):
        path = self.root / RECORD
        return path.read_text() if path.exists() else None

    def default(self):
        found = self.catalog()
        return found['items'][found['default']]['id'] if found['default'] < len(found['items']) else None

    def switch(self, entry, timeout=bounds.WAIT_S):
        """library.set_default; for a started switch, its job.status until the end (and the window closed)."""
        code, _ = self.dev.set_default(entry)
        return code, (self.finished(0, timeout) if code == ids.S_STARTED else None)

    def switched(self, entry, official=False, installed=False):
        """A switch to entry that succeeds (installed: its content may be in already, then no switch runs)."""
        code, f = self.switch(entry)
        if not (installed and code == 0):
            self.assertEqual(code, ids.S_STARTED)
            self.assertEqual((MS.PHASES[f['phase']], f['error'], f['detail']), ('done', 0, entry), f)
            self.assertEqual(f['done'], f['total'], f)
        self.assertEqual(content(self.root), self.expected(None if official else entry))
        self.assertEqual(self.default(), entry)
        self.assertEqual(self.sim.status()['rw_open'], 0)
        if official:
            self.assertEqual(self.record(), 'state=official\n')
        return f

    def usable(self):
        return {i['id']: (i['usable'], i['content_missing']) for i in self.catalog()['items']}


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class SyntheticSwitch(Switch, unittest.TestCase):
    base = 'modbase'
    unverified = True

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-simswitch-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        data = stock()
        (cls.work / 'kit.json').write_text(json.dumps(kit_for(profile_for(data))))
        gen = Path(
            M.split_kit(
                cls.work / 'kit.json',
                cls.work / 'packages',
                finish=finish,
                sdk_source='https://github.com/example/sdk@abc',
            )['root']
        )
        cls.seed_with(gen, MP.info_values(gen / 'base/info.txt')['profile'], data, CATALOG, official_list()[1])
        cls.seed_content()

    @pytest.mark.sim
    def test_round_trip_official_a_b_official(self):
        """Kept: The content switch round trip official, A, B, official."""
        a = self.build(['alpha'])
        self.assertEqual(self.record(), None)
        self.assertEqual(self.usable()[a], (False, True))
        f = self.switched(a)
        own = {p: b for p, b in content(self.root / LIB / a)[0].items() if p != 'op1_factory.json'}
        self.assertTrue(own, 'the synthetic set installs files of its own')
        self.assertEqual(
            self.record(),
            'state=installed\nentry=%s\n' % a  # each file's SHA-256 written
            + ''.join('file=%s\t%s\n' % (p, hashlib.sha256(own[p]).hexdigest()) for p in sorted(own)),
        )
        log = (self.root / 'xy-forever/log/save-times.txt').read_text().splitlines()[-1]  # the switch's line
        fields = dict(f.split('=', 1) for f in log.split())
        self.assertEqual(
            (fields['kind'], fields['result'], fields['written'], fields['read']),
            ('switch', 'ok', str(len(own) + 1), '0'),
            log,
        )
        self.assertEqual((self.root / SAVED / 'content.sha256').read_text(), self.official_text)
        self.assertEqual((self.root / SAVED / 'op1_factory.json').read_bytes(), CATALOG)
        self.assertEqual(self.usable()[a], (True, False))
        self.assertEqual(self.usable()['opxy-121'], (False, True))  # stock entries need the official content
        self.assertEqual(self.dev.library_boot('opxy-121'), ids.S_NO_CONTENT)  # boot once: never installs
        print(
            '\nswitch to %s: %d steps, longest pass %d ms, %d ms device clock'
            % (a, f['steps'], f['longest_ms'], f['elapsed_ms']),
            file=sys.stderr,
        )
        b = self.build(['gamma'])  # built while A's content is installed
        self.assertNotEqual(a, b)
        self.switched(b)
        gone = set(own) - set(content(self.root / LIB / b)[0])
        self.assertTrue(gone, 'A has files B lacks')
        self.assertFalse(gone & set(content(self.root)[0]))
        self.assertEqual(self.usable()[a], (False, True))
        self.switched('opxy-121', official=True)  # the Undo
        self.assertEqual(content(self.root), self.official)  # byte for byte, folders too
        self.assertEqual(self.record(), 'state=official\n')
        self.assertEqual(self.usable()['opxy-121'], (True, False))
        self.assertEqual(self.dev.set_default('opxy-121-stock')[0], 0)  # official: no switch
        self.assertEqual(self.dev.call(P.STATUS)[0], 0)

    # -- faults at every rename of a switch (official -> A) ------------------------------------------------
    def renames(self, entry):
        """The renames of a switch official -> entry with nothing saved yet: the saved list and catalog, the record
        (switching), each file, the catalog, the record again (record -> .old, .part -> record)."""
        return len([p for p in content(self.root / LIB / entry)[0] if p != 'op1_factory.json']) + 7

    def fresh_official(self):
        """Back to the official state with nothing saved (each case starts the same)."""
        code, f = self.switch('opxy-121')
        self.assertIn(code, (0, ids.S_STARTED))
        self.assertEqual(content(self.root), self.official)
        shutil.rmtree(self.root / SAVED, ignore_errors=True)
        (self.root / RECORD).unlink(missing_ok=True)

    def fault_case(self, entry, n, kind):
        """A switch to entry with the fault kind (1 fail, 2 power cut, 3 A silent) after the nth rename."""
        self.sim.set('rename_fault_kind', kind)
        self.sim.set('rename_fault', n)
        before_default = self.default()
        if kind == 1:
            code, f = self.switch(entry)
            self.assertEqual((code, MS.PHASES[f['phase']]), (ids.S_STARTED, 'failed'), f)
            self.assertEqual(MS.ERRORS[f['error']], 'write' if f['detail'] == 'journal write' else 'rename', f)
        elif kind == 2:
            cuts = self.sim.status()['cuts']
            self.assertEqual(self.dev.set_default(entry)[0], ids.S_STARTED)
            self.wait_sim(lambda s: s['cuts'] == cuts + 1 and s['listening'], 'power cut and restart')
            self.reconnect()
            self.assertEqual(self.dev.hello()[0], 0)
        else:
            self.assertEqual(self.dev.set_default(entry)[0], ids.S_STARTED)
            self.wait_sim(lambda s: s['a_silent'] == 1, 'A silent at the rename')
            reply = {}
            thread = threading.Thread(target=lambda: reply.setdefault('r', MS.job_status(self.dev, 0)), daemon=True)
            thread.start()
            for _ in range(12):  # the request's bound, then the close's
                self.sim.advance(6000)
                thread.join(0.1)
                if 'r' in reply:
                    break
            thread.join(10.0)
            code, f = reply['r']
            while code == 0 and f['phase'] < MS.DONE:
                self.sim.advance(50)
                code, f = MS.job_status(self.dev, 0)
            self.assertEqual((code, MS.PHASES[f['phase']], MS.ERRORS[f['error']]), (0, 'failed', 'link'), f)
            self.assertEqual(self.dev.call(P.STATUS, timeout=5.0)[0], 0)  # B answers
            self.sim.set('a_silent', 0)
            self.sim.control('power play')  # a power cycle holding PLAY
            self.sim.wait_listening(30.0)
            self.reconnect()
            self.assertEqual(self.dev.hello()[0], 0)
        if kind in (2, 3):
            # The menu replays durable intent itself. Wait for its window close before inspecting files.
            until = time.monotonic() + 30
            while (self.root / 'xy-forever/job.txt').exists() or (self.root / 'xy-forever/job.old').exists():
                self.assertLess(time.monotonic(), until, 'automatic replay')
                self.dev.hello()
                self.sim.advance(10)
            self.assertEqual(MS.served(self.dev, out=lambda _: None), 0)
        self.assertEqual(self.sim.status()['rw_open'], 0)
        record = self.record()
        self.assertIn(
            record if record is None else record.split('\n')[0], (None, 'state=switching', 'state=installed'), record
        )
        if record and record.startswith('state=switching'):  # nothing boots from a plain power-on
            self.assertFalse(any(u for u, _ in self.usable().values()), self.usable())
        if kind == 1 or not (record or '').startswith('state=installed'):
            self.assertEqual(self.default(), before_default)  # the default only after a success
        self.sim.set('rename_fault', 0)
        self.switched(entry, installed=(record or '').startswith('state=installed'))  # a repeat repairs

    def faults(self, kind):
        a = self.build(['alpha'])
        total = self.renames(a)
        self.assertGreater(total, 6)
        for n in range(1, total + 1):
            with self.subTest(rename=n):
                self.fresh_official()
                self.fault_case(a, n, kind)
        if kind == 1:  # that was every rename: one more never hits
            self.fresh_official()
            self.sim.set('rename_fault_kind', 1)
            self.sim.set('rename_fault', total + 1)
            self.switched(a)
            self.sim.set('rename_fault', 0)

    def wait_sim(self, test, what, timeout=30.0):
        deadline = time.monotonic() + timeout
        while True:
            status = self.sim.status()
            if test(status):
                return status
            self.assertLess(time.monotonic(), deadline, what)
            self.sim.advance(50)

    @pytest.mark.gate  # a simulator sweep
    @pytest.mark.uses('sim/')
    def test_a_failed_rename_at_each_step(self):
        self.faults(1)

    @pytest.mark.gate  # a simulator sweep
    @pytest.mark.uses('sim/')
    def test_a_power_cut_at_each_step(self):
        self.faults(2)

    @pytest.mark.sweep  # parked: a serial chain of simulator restarts, the run's critical path
    def test_a_silent_at_each_step(self):
        self.faults(3)


if __name__ == '__main__':
    unittest.main()
