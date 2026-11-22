"""Exercise the production request dispatcher with arrivals between polls."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from xy_forever import hostbuild

ROOT = Path(__file__).resolve().parents[1]


class PeerDispatchTest(unittest.TestCase):
    def test_arrivals_keep_their_command_storage_or_image_route(self):
        source = (ROOT / 'target/peer_services.cpp').read_text()
        start = source.index('    if(!role()){', source.index(
            'Result Stock121PeerSession::poll(U32 now)'))
        end = source.index('    } else {\n        if(active_){', start)
        body = source[start + len('    if(!role()){\n'):end]
        with tempfile.TemporaryDirectory(prefix='xyf-dispatch-') as temporary:
            out = Path(temporary)
            (out / 'role_a_dispatch.inc').write_text(body)
            binary = out / 'dispatch'
            hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra',
                           '-Werror', '-fsanitize=address,undefined',
                           '-fno-omit-frame-pointer', '-I', str(out),
                           str(ROOT / 'tests/peer_dispatch.cpp'), '-o', str(binary)],
                          check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True,
                                    capture_output=True, text=True)
            self.assertIn('dispatch arrival checks passed', result.stdout)
