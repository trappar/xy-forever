"""Where XY Forever's private inputs and outputs live (outside every checkout).

The private root is, in order: XYF_PRIVATE; "private" in the main checkout's gitignored xyf.local.json
(written by `scripts/setup.py init --private DIR`); ../private next to the main checkout. Firmware, keys,
builds and device reports never enter git. The main checkout is this checkout in a plain clone; in a
`git worktree` it is the checkout that holds the shared .git (main_checkout), so a worktree anywhere finds
the same private root, toolchain, xyf.local.json and local docs/status/ as the main checkout.
"""
import json
import re
import os
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[1]


def main_checkout(repo=REPO):
    """repo itself when its .git is a folder (a plain clone) or absent; for a worktree (.git is a file), the
    folder holding `git rev-parse --git-common-dir` when that is a .git folder, else repo (git missing, a bare
    repository, a submodule)."""
    repo = Path(repo)
    if not (repo / '.git').is_file():
        return repo
    try:
        common = subprocess.run(['git', 'rev-parse', '--git-common-dir'], cwd=str(repo), capture_output=True,
                                text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return repo
    common = (repo / common).resolve()
    return common.parent if common.name == '.git' and common.is_dir() else repo


MAIN = main_checkout()                          # REPO in a plain clone
LOCAL_CONFIG = MAIN / 'xyf.local.json'          # the main checkout's settings (gitignored): {"private": DIR}
TOOLCHAIN = MAIN.parent / 'tools/blackfin'      # the Blackfin toolchain unless XYF_TOOLCHAIN (toolchain.location)
STATUS = MAIN / 'docs/status'                   # the local, gitignored status folder (docs/STATUS.template.md)


def private_root(environ=os.environ, config=LOCAL_CONFIG, main=MAIN):
    """XYF_PRIVATE, else the local config's "private", else ../private next to the main checkout."""
    if environ.get('XYF_PRIVATE'):
        return Path(environ['XYF_PRIVATE']).expanduser().resolve()
    try:
        configured = json.loads(Path(config).read_text()).get('private')
    except FileNotFoundError:
        configured = None
    return Path(configured or Path(main).parent / 'private').expanduser().resolve()


def leak_patterns(config=LOCAL_CONFIG):
    """The local config's "leak_patterns": extra regexes (the maintainer's name, emails) that
    tests/test_no_leaks.py refuses in tracked files, kept out of the repository. [] without a config."""
    try:
        value = json.loads(Path(config).read_text()).get('leak_patterns', [])
    except FileNotFoundError:
        return []
    if not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
        raise ValueError('%s: "leak_patterns" must be a list of regex strings' % config)
    return value


PRIVATE_ROOT = private_root()

FIRMWARE = PRIVATE_ROOT / 'firmware'
STOCK_121 = FIRMWARE / 'stock-1.2.1'            # os.ldr + bootloader.ldr: build and test inputs
DECRYPTED = FIRMWARE / 'decrypted'              # decrypted official packages (1.1.33, 1.2.1, 1.2.5)
# stock content/op1_factory.json of 1.2.1: from a decrypted package, else the copy `setup.py fixtures`
# takes from public open-xy (the same bytes, checked)
CATALOG_121 = next((p for p in (DECRYPTED / 'opxy_firmware_1_2_1/extracted/firmware/blackfin/content/op1_factory.json',
                                STOCK_121 / 'op1_factory.json') if p.is_file()), STOCK_121 / 'op1_factory.json')
XYF = PRIVATE_ROOT / 'xy-forever'
BUILDS = XYF / 'builds'                         # build outputs (xyf_build.py --out BUILDS/<name>)
RECOVERY = XYF / 'recovery'                     # dev-menu kits: known-good build, stock 1.2.1
FIXTURES = XYF / 'fixtures'                     # files some tests read (they skip when absent)
REPORTS = XYF / 'reports'                       # device report files (census dumps)
CLAIMS = XYF / 'claims'                         # local item claims of agents on this machine (scripts/plan.py)
DEVICE_LOCK = XYF / 'device.lock'               # held while a host tool talks to the OP-XY (xy_forever/device_lock.py)
ANALYSIS = XYF / 'analysis'                     # reverse-engineering evidence (FINDINGS.md per study)
LISTING_121 = ANALYSIS / 'bootloader-121-listing'   # 1.2.1 bootloader image + annotated listing
PREVIEWS = XYF / 'previews'                     # ui_preview.py output
SESSIONS = XYF / 'sessions'                     # scratch state of device sessions (scripts/)
OPEN_XY_UPSTREAM = XYF / 'open-xy-upstream'     # a fresh clone of upstream open-xy (never ../open-xy); its users
                                                # need the pinned SDK commit in it and skip without it
MOD_PACKAGES = XYF / 'mod-packages'             # `modkit split --out`: mods/<profile>/<generation>/<package>/
PUBLISHED_MODS = MOD_PACKAGES / 'mods' / 'opxy-1.2.1' / '0.0.6-370a949e96aa'   # the published 0.0.6 set, split
PUBLISHED_KIT = FIXTURES / 'open-xy-kit-0.0.6.json'   # open-xy's published plugin kit 0.0.6 (the set's source)


def private_output(path):
    """Accept an output path only under the private root and outside every git checkout."""
    path = Path(path).expanduser().resolve()
    if not path.is_relative_to(PRIVATE_ROOT):
        raise ValueError('Firmware output must be under the external private root: ' + str(PRIVATE_ROOT))
    if any((p / '.git').exists() for p in (path, *path.parents)):
        raise ValueError('Firmware output must stay outside every checkout')
    return path


IMAGE_NAME = re.compile(r'[0-9a-f]{8}')      # a build folder's name: its image (hello image=)
CHECK_TEST_BUILDS = 'XYF_CHECK_TEST_BUILDS'   # only the verified build kept for an active check's own suite


def build_image(folder):
    """(image, manifest) of a verified product build folder named by its image (the first 8 hex digits of its
    candidate's SHA-256, xyf_build.py image_name), else None."""
    folder = Path(folder)
    try:
        v = json.loads((folder / 'verification.json').read_text())
        m = json.loads((folder / 'manifest.json').read_text())
    except (OSError, ValueError):
        return None
    if not (IMAGE_NAME.fullmatch(folder.name) and v.get('passed') is True and v.get('mode') == 'xyf-product' and
            m.get('image') == folder.name and str(m.get('candidate_sha256', '')).startswith(folder.name) and
            (folder / 'candidate.ldr').is_file()):
        return None
    return folder.name, m


def name_by_image(out):
    """Moves a built and verified folder to BUILDS/<image> (replacing an older copy of the same image) and
    returns the new path."""
    image = json.loads((Path(out) / 'manifest.json').read_text())['image']
    target = BUILDS / image
    if Path(out).resolve() != target.resolve():
        if target.exists():
            import shutil
            shutil.rmtree(target)
        Path(out).rename(target)
    return target


def newest_verified_build():
    """The latest verified product from this checkout's sources. A check suite uses its private copy.

    The manifest's source hashes must still match, as with --verify. Outside a check run, look in BUILDS.
    """
    import xyf_build
    source = xyf_build.source_hashes()
    found = []
    builds = Path(os.environ.get(CHECK_TEST_BUILDS) or BUILDS)
    for d in builds.glob('*'):
        named = build_image(d)
        if named and named[1].get('source_sha256') == source:
            found.append(((d / 'verification.json').stat().st_mtime, d.name, d))
    return max(found)[2] if found else None
