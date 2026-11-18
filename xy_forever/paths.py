"""Where XY Forever's private inputs and outputs live (outside every checkout).

The private root is, in order: XYF_PRIVATE; "private" in the repository's gitignored
xyf.local.json (written by `scripts/setup.py init --private DIR`); ../private next to this
repository. Firmware, keys, builds and device reports never enter git.
"""
import json
import re
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCAL_CONFIG = REPO / 'xyf.local.json'          # this clone's settings (gitignored): {"private": DIR}


def private_root(environ=os.environ, config=LOCAL_CONFIG):
    """XYF_PRIVATE, else the local config's "private", else ../private next to the repository."""
    if environ.get('XYF_PRIVATE'):
        return Path(environ['XYF_PRIVATE']).expanduser().resolve()
    try:
        configured = json.loads(Path(config).read_text()).get('private')
    except FileNotFoundError:
        configured = None
    return Path(configured or REPO.parent / 'private').expanduser().resolve()


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
    """The verified product build tests use when present: of BUILDS/<image> builds made from this checkout's
    source as it is now (manifest source_sha256 == xyf_build.source_hashes(), the tie --verify requires;
    a build of another branch or an older commit never counts), the one verified last. None when there is
    none: scripts/check.py builds one before its suite, `setup.py fixtures` too."""
    import xyf_build
    source = xyf_build.source_hashes()
    found = []
    for d in BUILDS.glob('*'):
        named = build_image(d)
        if named and named[1].get('source_sha256') == source:
            found.append(((d / 'verification.json').stat().st_mtime, d.name, d))
    return max(found)[2] if found else None
