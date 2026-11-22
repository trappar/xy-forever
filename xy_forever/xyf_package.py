#!/usr/bin/env python3
"""Offline official-package tools on the host; never opens a device.

    python3 xyf_package.py ZIP [--baseline ZIP] [--stage DIR]   review / stage a decrypted vendor ZIP
    python3 xyf_package.py extract-os PACKAGE.tfw --out DIR     OS-only library entry from an official .tfw
    python3 xyf_package.py content-manifest CONTENT --out DIR   content.sha256 of an extracted content set
    python3 xyf_package.py content-compare A B [--paths]        two lists or folders; exit 0 when identical
    python3 xyf_package.py index build GENERATION_DIR --out DIR  local mod package index + archives
    python3 xyf_package.py index verify DIR                      every archive matches index.json

extract-os runs the device's own extractor and signature check (native/tfw_os, tfw_signature,
p224, compiled for the Mac) and writes DIR/os.ldr and DIR/manifest.json (docs/STORAGE.md). It never
reads the bootloader member. The AES key comes from the private area (keys/opxy-update-keys.json,
or --keys FILE); without one it stops with "no .tfw key". The vendor public key comes from
keys/opxy-tfw-public-key.json (or --public-key FILE): the header's ECDSA P-224 signature must
verify, and without the key it stops with "no .tfw public key". --allow-unverified extracts
without the key: the output then says the signature was not checked and the entry is
kind=custom. Requiring the signature is the default because this output goes onto a device.
"""
import argparse
import json
from pathlib import Path
import sys
import time

from xy_forever.paths import private_output
from xy_forever.update_plan import prepare_update, review_update


def extract_os(argv):
    from xy_forever.tfw_extract import extract_os as extract
    parser = argparse.ArgumentParser(prog='xyf_package.py extract-os', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('package', type=Path, help='official .tfw package (encrypted, as distributed)')
    parser.add_argument('--out', required=True, type=Path, help='new entry folder under private/ (must not exist)')
    parser.add_argument('--keys', type=Path, help='key file (default: <private>/keys/opxy-update-keys.json)')
    parser.add_argument('--public-key', type=Path,
                        help='vendor public key file (default: <private>/keys/opxy-tfw-public-key.json)')
    parser.add_argument('--allow-unverified', action='store_true',
                        help='extract even when no public key is available (signature NOT checked; kind=custom)')
    parser.add_argument('--name', help='display name (default: "OP-XY <version>")')
    args = parser.parse_args(argv)
    try:
        started = time.monotonic()
        report = extract(args.package, private_output(args.out), keys=args.keys, public_key_file=args.public_key,
                         require_signature=not args.allow_unverified, name=args.name)
    except (OSError, ValueError) as error:     # ExtractError is a ValueError
        parser.exit(1, 'extract-os failed: %s\n' % error)
    summary = {k: report[k] for k in ('folder', 'version', 'package_version', 'bytes', 'crc32', 'sha256', 'records',
                                      'signature')}
    if summary['signature'] != 'verified':
        summary['signature'] = 'NOT CHECKED: no public key (--allow-unverified); entry kind=custom'
    summary['seconds'] = round(time.monotonic() - started, 2)
    print(json.dumps(summary, indent=2))
    return 0


def index_command(argv):
    from xy_forever import modindex
    parser = argparse.ArgumentParser(prog='xyf_package.py index', description=modindex.__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='index_command', required=True)
    b = sub.add_parser('build', help='index.json + one archive per package of a generation folder (local only)')
    b.add_argument('generation', type=Path, help='generation folder as modkit split writes it')
    b.add_argument('--out', required=True, type=Path, help='index folder (an existing index gains the generation)')
    v = sub.add_parser('verify', help='check every archive of a local index against index.json')
    v.add_argument('folder', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.index_command == 'build':
            record = modindex.build(args.generation, args.out)
            print('%s: %d packages, %d archive bytes (not published; licence and source are placeholders)' % (
                args.out / 'index.json', len(record['packages']), sum(p['archive']['size'] for p in record['packages'])))
        else:
            print('%d packages verified' % modindex.verify(args.folder))
    except (modindex.Refused, OSError) as error:
        parser.exit(1, 'index %s: refused: %s\n' % (args.index_command, error))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ['index']:
        return index_command(argv[1:])
    if argv[:1] == ['extract-os']:
        return extract_os(argv[1:])
    if argv[:1] in (['content-manifest'], ['content-compare']):
        from xy_forever.content_manifest import main as content_main
        return content_main(argv)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("archive", type=Path, help="decrypted official firmware ZIP (not the encrypted .tfw)")
    parser.add_argument("--baseline", type=Path, help="previous vendor ZIP; does not establish installed device state")
    parser.add_argument("--stage", type=Path, help="new host staging directory under private/; never mirror to device root")
    args = parser.parse_args(argv)
    try:
        if args.stage:
            output = private_output(args.stage)
            report = prepare_update(args.archive, output, args.baseline)
            (output / "review.json").write_text(json.dumps(report, indent=2) + "\n")
        else:
            report = review_update(args.archive, args.baseline)
    except (OSError, ValueError) as error:
        parser.exit(1, "Package review failed: " + str(error) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
