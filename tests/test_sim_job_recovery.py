"""Automatic menu/service repair and undo, with repeated power cuts at IO boundaries; no device access."""
import hashlib
import shutil
import time
import unittest

import pytest
import zlib
from xy_forever import modsend as MS, package_records
import test_sim_content_switch as CS
Switch, UNAVAILABLE, content, LIB = CS.Switch, CS.UNAVAILABLE, CS.content, CS.LIB

JOURNAL = 'xy-forever/job.txt'
OFFER = 'Source missing. PLAY on the first row undoes: '                   # usb_service content_line


def identity(root, entry):
    folder = root / LIB / entry
    metadata = folder / package_records.local_name(folder)
    listed = folder / 'files.sha256'
    return (hashlib.sha256(metadata.read_bytes()).hexdigest(),
            hashlib.sha256(listed.read_bytes()).hexdigest() if listed.exists() else '0' * 64)


def intent(root, target, previous, undo=False):
    a, b = identity(root, target), identity(root, previous)
    text = ('version=1\ntarget=%s\nprevious=%s\ndefault=1\nundo=%d\ntarget_info=%s\ntarget_list=%s\n'
            'previous_info=%s\nprevious_list=%s\n' % (target, previous, undo, *a, *b)).encode()
    return text + ('crc32=%08x\n' % (zlib.crc32(text) & 0xffffffff)).encode()


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class Recovery(Switch, unittest.TestCase):
    base = 'modbase'
    unverified = True
    setUpClass = classmethod(CS.SyntheticSwitch.setUpClass.__func__)

    def wait(self, predicate, what, seconds=30):
        end = time.monotonic() + seconds
        while not predicate():
            self.assertLess(time.monotonic(), end, what)
            self.dev.hello()
            self.sim.advance(10)

    def restart(self, plain=False):
        self.sim.control('power plain' if plain else 'power play')
        if not plain:
            self.sim.wait_listening(30)
            self.reconnect()
            self.assertEqual(self.dev.hello()[0], 0)

    def repaired(self, target, official=False):
        self.wait(lambda: not (self.root / JOURNAL).exists() and not (self.root / 'xy-forever/job.old').exists(), 'repair cleanup')
        self.assertEqual(MS.served(self.dev, out=lambda _: None), 0)
        self.assertEqual(self.default(), target)
        self.assertEqual(content(self.root), self.expected(None if official else target))
        self.assertEqual(self.sim.status()['rw_open'], 0)

    @pytest.mark.sim
    def test_startup_finishes_then_cleans_intent_and_keeps_usb(self):
        """Kept: Power-on replay of a job journal finishes, cleans up and keeps USB."""
        previous = self.default()
        target = self.build(['alpha'])
        (self.root / JOURNAL).write_bytes(intent(self.root, target, previous))
        self.restart()
        self.repaired(target)
        self.assertEqual(self.dev.hello()[0], 0)

    @pytest.mark.sweep
    def test_repair_and_undo_cut_at_every_io_boundary(self):
        previous = self.default()
        target = self.build(['alpha'])
        original = self.dir / 'before-repair'
        shutil.copytree(self.root, original)
        for undo in (False, True):
            if undo:
                self.switched(target)
            journal = intent(self.root, target, previous, undo)
            snapshot = self.dir / ('undo' if undo else 'replay')
            shutil.copytree(self.root, snapshot)
            (snapshot / JOURNAL).write_bytes(journal)
            # Measure the complete automatic repair. Every mutating A-side operation is a cut boundary (native tests also cut every read/close).
            self.dev.t.close(); self.sim.stop()
            shutil.rmtree(self.root); shutil.copytree(snapshot, self.root)
            self.sim.start(); self.reconnect(); self.dev.hello()
            self.repaired(previous if undo else target, official=undo)
            total = self.sim.status()['storage_ops']
            self.assertGreater(total, 20)
            boundaries = sorted(set(range(1, total + 1, max(1, total // 12))) | {total})   # a sample: the native tests cut at every one
            for at in boundaries:
                with self.subTest(undo=undo, io=at):
                    self.dev.t.close(); self.sim.stop()
                    shutil.rmtree(self.root); shutil.copytree(snapshot, self.root)
                    self.sim.start(); self.reconnect(); self.dev.hello()
                    self.repaired(previous if undo else target, official=undo)
                    shutil.rmtree(self.root); shutil.copytree(snapshot, self.root)
                    # A new power-on now sees the exact snapshot and the armed IO boundary.
                    cuts = self.sim.status()['cuts']
                    self.sim.set('cut_after', at)
                    self.sim.control('power play')
                    end = time.monotonic() + 30
                    while self.sim.status()['cuts'] == cuts:
                        self.assertLess(time.monotonic(), end, 'the armed mutation boundary')
                        self.sim.advance(10)
                    self.sim.wait_listening(30); self.reconnect(); self.dev.hello()
                    self.repaired(previous if undo else target, official=undo)
            self.dev.t.close(); self.sim.stop()
            shutil.rmtree(self.root); shutil.copytree(original, self.root)
            self.sim.start(); self.reconnect(); self.dev.hello()

    @pytest.mark.sweep
    def test_repeated_interrupted_replay_and_undo(self):
        previous = self.default()
        target = self.build(['alpha'])
        for undo in (False, True):
            with self.subTest(undo=undo):
                (self.root / JOURNAL).write_bytes(intent(self.root, target, previous, undo))
                cuts = self.sim.status()['cuts']
                self.sim.set('a_slow_ms', 20)
                self.sim.set('cut_after', 6)
                self.sim.control('power play')
                end = time.monotonic() + 30
                while self.sim.status()['cuts'] == cuts:
                    self.assertLess(time.monotonic(), end, 'first interrupted replay')
                    self.sim.advance(5)
                self.sim.set('cut_after', 1)
                while self.sim.status()['cuts'] < cuts + 2:
                    self.assertLess(time.monotonic(), end, 'replay interrupted again')
                    self.sim.advance(5)
                self.sim.set('a_slow_ms', 0)
                self.sim.wait_listening(30); self.reconnect(); self.dev.hello()
                self.repaired(previous if undo else target, official=undo)
