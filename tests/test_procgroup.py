"""No test leaves a process running (#133): xy_forever/procgroup.py, the simulator wrapper
(`python3 -m xy_forever.sim`) and the TypeScript suites' simulator helper (clients/ts/test/simulators.ts).

procgroup.run() ends the command's whole process group on a timeout and on a normal end. The
simulator tests start a simulator the way the TypeScript suites do (startAll from Node, a state
folder of their own) and end the Node process: by SIGTERM (its exit hooks stop the simulator) and by
SIGKILL (no hooks: the wrapper sees its parent gone and ends its xyf_sim group). Then no process
naming that state folder may remain. Skipped without Node, clients/ts/node_modules or the simulator."""
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from xy_forever import paths, procgroup, sim  # noqa: E402
from test_ts_client import CLIENT, NODE, SIM_UNAVAILABLE, node_strips_types  # noqa: E402

# A child that starts a grandchild naming the marker (argv[1]), then waits: run() must end both.
TREE = ('import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)", '
        'sys.argv[1]]); print("up", flush=True); time.sleep(300 if sys.argv[2] == "wait" else 0)')
# One simulator through the suites' own helper, as their global setup starts it.
START = '''
import { pathToFileURL } from 'node:url';
const [helper, state, port] = process.argv.slice(1);
const { startAll } = await import(pathToFileURL(helper).href);
await startAll(state, Number(port), [['orphan', 0, []]]);
console.log('up');
setInterval(() => undefined, 1000);
'''


def running(marker):
    """The processes whose command line names marker."""
    return subprocess.run(['pgrep', '-fl', marker], capture_output=True, text=True).stdout.strip()


def gone(marker, within):
    deadline = time.monotonic() + within
    while running(marker) and time.monotonic() < deadline:
        time.sleep(0.1)
    return running(marker)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.marker = 'xyf-procgroup-%s' % uuid.uuid4().hex
        self.addCleanup(subprocess.run, ['pkill', '-KILL', '-f', self.marker])

    def test_timeout_ends_the_whole_group(self):
        start = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            procgroup.run([sys.executable, '-c', TREE, self.marker, 'wait'], capture_output=True, text=True, timeout=2)
        self.assertLess(time.monotonic() - start, 30)
        self.assertIn('up', caught.exception.stdout or '')
        self.assertEqual(gone(self.marker, 5), '')

    def test_normal_end_ends_what_the_command_left(self):
        run = procgroup.run([sys.executable, '-c', TREE, self.marker, 'end'], capture_output=True, text=True,
                            timeout=60)
        self.assertEqual((run.returncode, run.stdout), (0, 'up\n'))
        self.assertEqual(gone(self.marker, 5), '')


@unittest.skipUnless(node_strips_types(), 'Node 22.18+/23.6+ not found')
@unittest.skipUnless((CLIENT / 'node_modules' / 'ws').exists(), 'clients/ts/node_modules absent (npm ci in clients/ts)')
@unittest.skipIf(SIM_UNAVAILABLE, SIM_UNAVAILABLE)
class SuiteSimulatorTest(unittest.TestCase):
    """A Node process that started a simulator ends: nothing from that run remains."""

    def started(self):
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)
        state = Path(tempfile.mkdtemp(prefix='procgroup-', dir=paths.SESSIONS))
        self.addCleanup(shutil.rmtree, state, True)
        self.addCleanup(subprocess.run, ['pkill', '-KILL', '-f', state.name])   # a failure leaves nothing either
        node = procgroup.popen([NODE, '--input-type=module', '-e', START, str(CLIENT / 'test' / 'simulators.ts'),
                                str(state), str(sim.free_port())], cwd=CLIENT, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
        self.addCleanup(procgroup.kill_group, node)
        output, deadline = b'', time.monotonic() + 180
        while b'up\n' not in output:
            ready = select.select([node.stdout], [], [], max(0.0, deadline - time.monotonic()))[0]
            chunk = os.read(node.stdout.fileno(), 4096) if ready else b''
            if not chunk:
                self.fail('the simulator did not start: %s' % output.decode(errors='replace')[-2000:])
            output += chunk
        self.assertIn('xyf_sim', running(state.name))
        return node, state.name

    def check_ends(self, sig):
        node, marker = self.started()
        os.kill(node.pid, sig)                    # Node alone, not its group
        node.wait(timeout=30)
        self.assertEqual(gone(marker, 20), '')

    def test_terminated_runner_stops_its_simulator(self):
        self.check_ends(signal.SIGTERM)

    def test_killed_runner_leaves_no_simulator(self):
        self.check_ends(signal.SIGKILL)


if __name__ == '__main__':
    unittest.main()
