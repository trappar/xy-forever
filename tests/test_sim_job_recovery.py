"""Automatic menu/service repair and undo, with repeated power cuts at IO boundaries; no device access."""

import hashlib
import shutil
import time
import unittest
import zlib

import pytest
import test_sim_content_switch as CS

from xy_forever import modsend as MS
from xy_forever import package_records

Switch, UNAVAILABLE, content, LIB = CS.Switch, CS.UNAVAILABLE, CS.content, CS.LIB

JOURNAL = 'xy-forever/job.txt'
OFFER = 'Source missing. PLAY on the first row undoes: '  # usb_service content_line


def identity(root, entry):
    folder = root / LIB / entry
    metadata = folder / package_records.local_name(folder)
    listed = folder / 'files.sha256'
    return (
        hashlib.sha256(metadata.read_bytes()).hexdigest(),
        hashlib.sha256(listed.read_bytes()).hexdigest() if listed.exists() else '0' * 64,
    )


def intent(root, target, previous, undo=False):
    a, b = identity(root, target), identity(root, previous)
    text = (
        'version=1\ntarget=%s\nprevious=%s\ndefault=1\nundo=%d\ntarget_info=%s\ntarget_list=%s\n'
        'previous_info=%s\nprevious_list=%s\n' % (target, previous, undo, *a, *b)
    ).encode()
    return text + ('crc32=%08x\n' % (zlib.crc32(text) & 0xFFFFFFFF)).encode()


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
        self.wait(
            lambda: not (self.root / JOURNAL).exists() and not (self.root / 'xy-forever/job.old').exists(),
            'repair cleanup',
        )
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

    def cut_at_sampled_io_boundaries(self, undo):
        """The automatic repair (undo: of an undo), measured once uncut, then cut at a sample of its IO
        boundaries (every mutating A-side operation is one; the native tests cut at every read and close too):
        each cut run powers on the exact snapshot with the boundary armed, and the next power-on repairs."""
        previous = self.default()
        target = self.build(['alpha'])
        if undo:
            self.switched(target)
        snapshot = self.dir / 'snapshot'
        shutil.copytree(self.root, snapshot)
        (snapshot / JOURNAL).write_bytes(intent(self.root, target, previous, undo))
        want = dict(target=previous, official=True) if undo else dict(target=target)

        def power_on_snapshot(cut_after=None):
            shutil.rmtree(self.root)
            shutil.copytree(snapshot, self.root)
            cuts = self.sim.status()['cuts']
            if cut_after:
                self.sim.set('cut_after', cut_after)
            self.sim.control('power play')
            end = time.monotonic() + 30
            while cut_after and self.sim.status()['cuts'] == cuts:
                self.assertLess(time.monotonic(), end, 'the armed mutation boundary')
                self.sim.advance(10)
            self.sim.wait_listening(30)
            self.reconnect()
            self.dev.hello()
            self.repaired(**want)

        before = self.sim.status()['storage_ops']
        power_on_snapshot()
        total = self.sim.status()['storage_ops'] - before
        self.assertGreater(total, 20)
        # A sample of 13: power cycles of one board (no simulator restart between cuts) keep it cheap, #229.
        for at in sorted(set(range(1, total + 1, max(1, total // 12))) | {total}):
            with self.subTest(io=at):
                power_on_snapshot(at)

    @pytest.mark.sweep  # parked: a serial chain of simulator power cycles, the run's critical path
    def test_repair_cut_at_sampled_io_boundaries(self):
        self.cut_at_sampled_io_boundaries(undo=False)

    @pytest.mark.sweep  # parked: a serial chain of simulator power cycles, the run's critical path
    def test_undo_cut_at_sampled_io_boundaries(self):
        self.cut_at_sampled_io_boundaries(undo=True)

    @pytest.mark.gate  # a simulator sweep
    @pytest.mark.uses('sim/')
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
                self.sim.wait_listening(30)
                self.reconnect()
                self.dev.hello()
                self.repaired(previous if undo else target, official=undo)
