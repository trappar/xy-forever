#!/usr/bin/env python3
"""Pin and stage XY Forever's os.ldr for the web app's install page (#178).

    python3 scripts/web_release.py [BUILD] --notes "What changed in this release"
    python3 scripts/web_release.py --check   (the staged image matches the pin; exit 1 when not)

The page installs only the image whose SHA-256 web/src/install/release.json pins, fetched from its own origin at
release/os.ldr. This script takes a verified product build (builds/<image>/, `xyf_build.py --verify` passed),
checks again that it shares no bytes with the stock pair (xy_forever/stock_bytes.py), writes the pin (commit it in a
release pull request) and copies candidate.ldr to web/public/release/os.ldr, which Vite serves and builds into
dist/ (gitignored: a build output, never committed). Builds are deterministic, so the pinned image can be rebuilt
from its commit by anyone with their own device's stock files.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import paths, release, stock_bytes  # noqa: E402

PIN = ROOT / 'web/src/install/release.json'
STAGED = ROOT / 'web/public/release/os.ldr'
SCHEMA = 1


def pin_of(data):
    digest = hashlib.sha256(data).hexdigest()
    return {'schema': SCHEMA, 'version': release.VERSION, 'image': digest[:8], 'sha256': digest, 'bytes': len(data)}


def check(pin_path=PIN, staged=STAGED):
    """None when the staged image matches the pin, else why not."""
    pin = json.loads(pin_path.read_text())
    if not pin.get('sha256'):
        return 'no release pinned in %s' % pin_path
    if not staged.is_file():
        return 'nothing staged at %s: run scripts/web_release.py BUILD' % staged
    if pin_of(staged.read_bytes())['sha256'] != pin['sha256']:
        return 'the staged image is not the pinned %s' % pin['image']
    return None


def stage(build, stocks, pin_path=PIN, staged=STAGED, *, notes=None):
    """Pins and stages a verified product build with the notes the update page shows."""
    named = paths.build_image(build)
    if not named:
        raise ValueError('%s is not a verified product build folder (builds/<image>/)' % build)
    data = (Path(build) / 'candidate.ldr').read_bytes()
    shared = stock_bytes.shared_runs(data, stocks)
    if shared:
        raise ValueError('%s shares stock bytes: %s' % (named[0], shared[:3]))
    pin = pin_of(data)
    if pin['version'] != named[1].get('version'):
        raise ValueError('the build is release %s, this checkout %s' % (named[1].get('version'), pin['version']))
    if notes is None and pin_path.is_file():
        previous = json.loads(pin_path.read_text())
        if previous.get('version') == pin['version']:
            notes = previous.get('notes')
    if not isinstance(notes, str) or not notes.strip():
        raise ValueError('give --notes describing what changed in this release')
    pin['notes'] = notes.strip()
    pin_path.write_text(json.dumps(pin, indent=2) + '\n')
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(build) / 'candidate.ldr', staged)
    return pin


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('build', nargs='?', type=Path, help='defaults to the newest verified product build of this source')
    parser.add_argument('--notes', help='release changes shown on the Device page')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        why = check()
        print(why or 'staged image matches the pin')
        return 1 if why else 0
    build = args.build or paths.newest_verified_build()
    if build is None:
        print('no verified product build of this source: run scripts/check.py (or xyf_build.py) first')
        return 1
    stocks = {name: (paths.STOCK_121 / name).read_bytes() for name in ('os.ldr', 'bootloader.ldr')}
    try:
        pin = stage(build, stocks, notes=args.notes)
    except ValueError as error:
        print('refused: %s' % error)
        return 1
    print('pinned %s (%s, %d bytes) in %s; staged %s' % (pin['image'], pin['version'], pin['bytes'],
                                                        PIN.relative_to(ROOT), STAGED.relative_to(ROOT)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
