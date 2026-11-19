"""Protocol command switch inventory and optional-build compatibility in the local simulator."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
import xyf_build
import xyf_host as host
from xy_forever import sim, usb_proto as P, usb_proto_ids as ids
ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / 'protocol/v1.json').read_text())


@unittest.skipIf(sim.available(), sim.available())
class ProtocolSwitches(unittest.TestCase):
    def test_build_switches_as_the_spec_says(self):
        """switch_optional: a build without an optional switch answers its type with unsupported (and
        leaves it out of cmds); without any other switch the firmware does not build."""
        product = xyf_build.usb_switches('xyf-product')
        switched = [c for c in SPEC['commands'] if c['switch']]
        self.assertEqual({c['name']: c['switch_optional'] for c in switched},
                         {'diag.engine_test': True, 'file.write_staged': False, 'library.set_default': False,
                          'tfw.check': True, 'tfw.check_role': True, 'dev.a_rehearsal': True,
                          'file.list': False, 'file.delete': False, 'file.rmdir': False,
                          'file.write_begin': False, 'file.write_data': False, 'file.write_end': False,
                          'file.mkdir': False, 'file.rename': False, 'file.copy': False, 'stage2.info': True,
                          'mods.apply': False, 'job.status': False, 'job.cancel': False, 'package.session': False,
                          'package.list': False, 'state.get': False, 'storage.space': False})
        required = set()
        with tempfile.TemporaryDirectory(prefix='xyf-sim-') as tmp:
            for c in switched:
                switches = product - {c['switch']}
                if not c['switch_optional']:              # the spec says only that it fails, not where
                    if c['switch'] not in required:       # one failing build per switch (failures are not cached)
                        required.add(c['switch'])
                        with self.assertRaisesRegex(RuntimeError, '^simulator build failed: '):
                            sim.build(switches=switches)
                    continue
                out = sim.build(switches=switches)
                root = sim.seed(Path(tmp) / c['symbol'] / 'yaffs2')
                with sim.Simulator(root, Path(tmp) / c['symbol'] / 'bootpart.bin', build_dir=out) as running:
                    dev = host.Device(host.TcpPort(running.address), on_log=None)
                    try:
                        code, text = dev.hello()
                        self.assertEqual(code, 0)
                        kind = int(c['type'], 16)
                        self.assertFalse(P.Hello(text).answers(kind))
                        dev.hello_info = None                 # xyf_host refuses types missing from cmds
                        self.assertEqual(dev.call(kind, struct.pack('<I', 0)), (ids.S_UNSUPPORTED, b''))
                    finally:
                        dev.t.close()

