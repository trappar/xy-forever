#!/usr/bin/env python3
"""The web app's pinned Open-XY 0.0.7 kit, without teenage engineering's material (#183).

    uv run scripts/web_open_xy.py stage     (into web/public/open-xy/0.0.7/; the Pages build serves it)
    uv run scripts/web_open_xy.py check DIST   (no teenage engineering bytes in the built site; exit 1 when there are)
    uv run scripts/web_open_xy.py strip KIT.json.gz OUT.json.gz   (the published kit, stripped: the release asset)

The Mods page reads Open-XY's live kit first. When it can't (open-xy.com sends no CORS header yet, or its kit is
newer than this app's split), it uses this pinned one: the published 0.0.7 kit (SHA-256 KIT_SHA256) with the stock
catalog emptied and every edit's `before` bytes zeroed, in the profile and in profileSource. The browser refills both
from Open-XY's public repository at v0.0.7 (raw.githubusercontent.com: profiles/opxy-1.2.1.json and its
content/op1_factory.json), and Open-XY's builder checks them (the profile's and the catalog's SHA-256).

The kit comes from https://open-xy.com/kit.json.gz while it is still the pinned 0.0.7 one, else from this repository's
release `open-xy-kit-0.0.7`, which holds the stripped kit only (STRIPPED_SHA256 pins the stripped JSON either way).
Neither copy is ever committed. Beside it: Open-XY v0.0.7's builder modules (MIT; src/vendor/open-xy/, the
"copied from" line dropped), its LICENSE, the kit's notices and every machine's and extension's source.
"""

import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.0.7'
KIT_URL = 'https://open-xy.com/kit.json.gz'
KIT_SHA256 = 'e971adfaf1a541b1e500cb40cd3abca38e74f0a40c9d3b7c8fedd7388c83c354'  # the published .json.gz
STRIPPED_SHA256 = '7512f995d1e4f2b2c041c971a1dff1a932f8f853422af011d1fd47ca698e60fe'  # the stripped kit's JSON (web/src/kit/pinned.ts pins the same)
RELEASE_TAG, RELEASE_ASSET = 'open-xy-kit-0.0.7', 'kit.json.gz'
RAW = 'https://raw.githubusercontent.com/etcetc000/open-xy/v0.0.7/'
PROFILE = ('profiles/opxy-1.2.1.json', '6bd2a299b7490b01657119315361974ea317c80bcac99c088525b08ff3303181')
CATALOG = (
    'profiles/opxy-1.2.1/content/op1_factory.json',
    '36d14444dcc12cac8f33784c4d0e96079cfa0b5042ac9edfe31d34cc9b303ff0',
)
OUT = ROOT / 'web/public/open-xy' / VERSION
VENDOR = ROOT / 'web/src/vendor/open-xy'
MODULES = ('builder/core.mjs', 'sdk/browser/graphics.mjs', 'tooling/default_preset.json')
BEFORE = re.compile(rb'("before"\s*:\s*")([0-9A-Fa-f]*)(")')
HEX = re.compile(rb'"([0-9A-Fa-f]{2,})"')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def zeroed(text, preimages=frozenset()):
    """Every `before` hex value of a JSON text zeroed, and any other hex string equal to one (a limit edit's choice
    can be the stock value), its length kept; nothing else changes."""
    text = BEFORE.sub(lambda m: m.group(1) + b'0' * len(m.group(2)) + m.group(3), text)
    return HEX.sub(
        lambda m: b'"%s"' % (b'0' * len(m.group(1))) if m.group(1).lower() in preimages else m.group(0), text
    )


def strip(kit_json):
    """The published kit's JSON bytes -> the stripped kit's: the catalog empty and every `before` zeroed (the profile's
    and profileSource's, and a choice equal to one), by edits of the text: every other byte as published."""
    kit = json.loads(kit_json)
    source = base64.b64decode(kit['profileSource'])
    preimages = frozenset(e['before'].lower().encode() for e in kit['profile']['patches'] if set(e['before']) - {'0'})
    for value in (kit['stockCatalog'], kit['profileSource']):
        assert kit_json.count(json.dumps(value).encode()) == 1
    text = kit_json.replace(json.dumps(kit['stockCatalog']).encode(), b'""', 1)
    stripped_source = base64.b64encode(zeroed(source, preimages)).decode()
    text = text.replace(json.dumps(kit['profileSource']).encode(), json.dumps(stripped_source).encode(), 1)
    text = zeroed(text, preimages)
    stripped = json.loads(text)
    assert stripped['stockCatalog'] == '' and len(text) == len(kit_json) - len(kit['stockCatalog'])
    return text


def te_material(profile, catalog):
    """The byte strings the built site must not hold: the catalog (raw and base64), the profile (raw and base64) and
    every edit's `before` value of 4 bytes or more (as hex: shorter ones match unrelated text)."""
    found = {catalog, base64.b64encode(catalog), profile, base64.b64encode(profile)}
    for edit in json.loads(profile)['patches']:
        before = edit.get('before', '')
        if len(before) >= 8 and set(before) != {'0'}:
            found |= {before.lower().encode(), before.upper().encode()}
    return found


def scan(folder, material):
    """[(file, what)] for every file of folder (a .gz also decompressed) holding one of material."""
    hits = []
    for path in sorted(Path(folder).rglob('*')):
        if not path.is_file():
            continue
        data = path.read_bytes()
        bodies = [data]
        if data[:2] == b'\x1f\x8b':
            try:
                bodies.append(gzip.decompress(data))
            except OSError, EOFError:
                pass
        for body in bodies:
            for item in material:
                if item in body:
                    hits.append((path.relative_to(folder).as_posix(), item[:24]))
    return hits


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'xy-forever-site-build'})  # urllib's own is refused
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def kit_json(out=print):
    """The stripped kit's JSON: from open-xy.com while it serves the pinned kit, else the release asset."""
    try:
        published = fetch(KIT_URL)
    except OSError as error:
        published, why = None, 'unreachable (%s)' % error
    else:
        why = 'not the pinned 0.0.7 kit (SHA-256 %s)' % sha256(published)[:12]
    if published is not None and sha256(published) == KIT_SHA256:
        out('kit: %s (the pinned 0.0.7 kit), stripped here' % KIT_URL)
        return strip(gzip.decompress(published))
    out('kit: %s is %s; the release %s instead' % (KIT_URL, why, RELEASE_TAG))
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.environ.get('GITHUB_REPOSITORY', 'trappar/xy-forever')
        cmd = ['gh', 'release', 'download', RELEASE_TAG, '--repo', repo, '--pattern', RELEASE_ASSET, '--dir', tmp]
        subprocess.run(cmd, check=True)
        return gzip.decompress((Path(tmp) / RELEASE_ASSET).read_bytes())


def sources_text(kit):
    """Where each machine's and extension's source is (their licences: AGPL or GPL, per repository)."""
    lines = [
        'Open-XY %s, served by XY Forever for its Mods page. Its builder and SDK: https://github.com/etcetc000/open-xy'
        " at tag v%s (LICENSE beside this file). The kit is Open-XY's published one with teenage engineering's"
        ' material removed (the stock catalog and the firmware bytes its edits replace).'
        % (kit['version'], kit['version']),
        '',
        'The machines and extensions it links, each with its source (licence and notices in that repository):',
    ]
    for kind, items in (('machine', kit['machines']), ('extension', kit.get('extensions') or [])):
        for item in items:
            src = item.get('source') or {}
            where = ' '.join(str(src[k]) for k in ('repository', 'tag', 'commit') if src.get(k))
            lines.append(
                '%s %s (%s, %s): %s'
                % (kind, item['id'], item.get('name', ''), item.get('origin', ''), where or 'Open-XY')
            )
    return '\n'.join(lines) + '\n'


def stage(out_dir=OUT, out=print, text=None):
    """The stripped kit and Open-XY's modules and notices into out_dir; their sizes printed."""
    text = kit_json(out) if text is None else text
    if sha256(text) != STRIPPED_SHA256:
        raise SystemExit('refused: the stripped kit is not the pinned one (SHA-256 %s)' % sha256(text))
    kit = json.loads(text)
    out_dir = Path(out_dir)
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    (out_dir / 'kit.json.gz').write_bytes(gzip.compress(text, compresslevel=9, mtime=0))
    for name in MODULES:
        data = (VENDOR / name).read_bytes()
        if name.endswith('.mjs'):
            data = data.split(b'\n', 1)[1]  # the "copied from" line: the release's own bytes
        (out_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (out_dir / name).write_bytes(data)
    shutil.copyfile(VENDOR / 'LICENSE', out_dir / 'LICENSE')
    (out_dir / 'NOTICES.txt').write_text(kit.get('notices') or '')
    (out_dir / 'SOURCES.txt').write_text(sources_text(kit))
    for p in sorted(out_dir.rglob('*')):
        if p.is_file():
            out('  %s: %d bytes' % (p.relative_to(out_dir).as_posix(), p.stat().st_size))
    return out_dir


def check(dist, profile=None, catalog=None, out=print):
    """0 when dist holds none of teenage engineering's material (the profile and catalog: from Open-XY's repository)."""
    profile = profile if profile is not None else fetch(RAW + PROFILE[0])
    catalog = catalog if catalog is not None else fetch(RAW + CATALOG[0])
    if sha256(profile) != PROFILE[1] or sha256(catalog) != CATALOG[1]:
        out("refused: Open-XY's v0.0.7 profile or catalog is not the pinned one")
        return 1
    hits = scan(dist, te_material(profile, catalog))
    for path, what in hits:
        out('teenage engineering material in %s: %r...' % (path, what))
    out('%s: %s' % (dist, 'teenage engineering material found' if hits else 'no teenage engineering material'))
    return 1 if hits else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('stage')
    c = sub.add_parser('check')
    c.add_argument('dist', type=Path)
    s = sub.add_parser('strip')
    s.add_argument('kit', type=Path)
    s.add_argument('out', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'stage':
        stage()
        return 0
    if args.command == 'check':
        return check(args.dist)
    data = args.kit.read_bytes()
    if sha256(data) != KIT_SHA256:
        raise SystemExit('refused: not the pinned 0.0.7 kit')
    args.out.write_bytes(gzip.compress(strip(gzip.decompress(data)), compresslevel=9, mtime=0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
