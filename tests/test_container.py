"""xy_forever/container.py and docker/toolchain.Dockerfile: the image runs mise.toml's Python and uv, and a run
mounts the checkout, its venv and the private folder's firmware as the docstring says (no Docker needed here)."""

import re
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from xy_forever import container

ROOT = Path(__file__).resolve().parents[1]


class Image(unittest.TestCase):
    def test_the_image_runs_mise_tomls_python_and_uv(self):
        tools = tomllib.loads((ROOT / 'mise.toml').read_text())['tools']
        dockerfile = (ROOT / 'docker/toolchain.Dockerfile').read_text()
        self.assertIn('uv python install %s ' % tools['python'], dockerfile)
        self.assertIn('UV_PYTHON=%s ' % tools['python'], dockerfile)
        self.assertRegex(dockerfile, r'ghcr\.io/astral-sh/uv:%s@sha256:[0-9a-f]{64}' % re.escape(tools['uv']))

    def test_the_image_is_pinned_by_digest(self):
        self.assertRegex(container.image(), r'^ghcr\.io/trappar/xy-forever-toolchain@sha256:[0-9a-f]{64}$')


class Command(unittest.TestCase):
    def command(self, platform, firmware=False, out=None):
        with tempfile.TemporaryDirectory() as tmp:
            root, private = Path(tmp).resolve() / 'checkout', Path(tmp).resolve() / 'firmware'
            if firmware:
                private.mkdir()
            with (
                mock.patch.object(container.sys, 'platform', platform),
                mock.patch.object(container.paths, 'FIRMWARE', private),
            ):
                cmd = container.command(['xyf-build', '--xyf-product'], root, out and Path(tmp) / out, {})
            self.assertTrue((root / 'build/container-venv').is_dir())  # the user's, not Docker's
            return cmd, root, private

    def test_linux_runs_as_the_user_and_docker_desktop_maps_it(self):
        cmd, root, _ = self.command('linux')
        self.assertEqual(cmd[cmd.index('--user') + 1], '%d:%d' % (container.os.getuid(), container.os.getgid()))
        self.assertIn('%s:/src' % root, cmd)
        self.assertIn('%s/build/container-venv:/opt/venv' % root, cmd)
        self.assertEqual(cmd[-3:], ['sh', 'xyf-build', '--xyf-product'])
        cmd, _, _ = self.command('darwin')
        self.assertNotIn('--user', cmd)

    def test_the_stock_pair_is_read_only_and_out_is_under_the_private_root(self):
        cmd, _, private = self.command('darwin', firmware=True, out='o')
        self.assertIn('%s:/tmp/private/firmware:ro' % private, cmd)
        self.assertIn('XYF_PRIVATE=/tmp/private', cmd)
        self.assertTrue(any(a.endswith(':' + container.OUT) for a in cmd))
        cmd, _, _ = self.command('darwin')
        self.assertFalse(any('/tmp/private/' in a for a in cmd))


if __name__ == '__main__':
    unittest.main()
