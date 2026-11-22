#!/usr/bin/env python3
"""Exercise #11's package commits and shared-blob GC through the normal USB service.

First prove against a simulator: --sim HOST:PORT --base opxy-121 --yes-write.
Without --sim the same plan uses USB. No boot, reset, live-content or state write;
the existing base image is read over USB and copied into two temporary entries.
"""

import argparse
import hashlib
import secrets
import sys
from pathlib import Path

from xy_forever import package_store as store
from xy_forever import xyf_entry
from xy_forever import xyf_host as host

ROOT = Path(__file__).resolve().parents[1]


def exercise(dev, base):
    tag = secrets.token_hex(5)
    ids = ['pkg-pass-' + tag + suffix for suffix in ('-a', '-b')]
    content = {'display/package-pass-%s.svg' % tag: ('<svg>XYF package pass v1 %s</svg>\n' % tag).encode()}
    unique = {'display/package-pass-%s-old.svg' % tag: ('<svg>old reference v1 %s</svg>\n' % tag).encode()}
    shared_hash = hashlib.sha256(next(iter(content.values()))).hexdigest()
    unique_hash = hashlib.sha256(next(iter(unique.values()))).hexdigest()
    states = ('state-a.txt', 'state-b.txt', 'state.txt')
    with store.Session(dev) as session:
        before = {name: session.read(store.ROOT + '/' + name, optional=True) for name in states}
        image = session.read(store.LIBRARY + '/' + base + '/os.ldr')
        for index, entry in enumerate(ids):
            if session.entries(store.LIBRARY + '/' + entry):
                raise store.StoreError('probe destination already exists: ' + entry)
            sources = dict(content, **unique) if index == 0 else content
            files = {
                'os.ldr': image,
                'info.txt': xyf_entry.info_text('Package pass v1', '0', 'custom', image).encode(),
                'content.sha256': store.list_text(sources),
                'firmware.version': b'package-pass-v1\n',
            }
            files['files.sha256'] = store.list_text(files)
            store.validate(files)
            session.retain(xyf_entry.read_files_sha256(files['content.sha256']), sources)
            session.publish(store.LIBRARY + '/' + entry, files)
            for path, data in files.items():
                if session.read(store.LIBRARY + '/' + entry + '/' + path) != data:
                    raise store.StoreError('published bytes differ: ' + entry)
        session.remove(store.LIBRARY + '/' + ids[0])
        session.collect()
        if (
            session.read(store.BLOBS + '/' + shared_hash, optional=True) is None
            or session.read(store.BLOBS + '/' + unique_hash, optional=True) is not None
        ):
            raise store.StoreError('GC did not preserve exactly the remaining references')
        session.remove(store.LIBRARY + '/' + ids[1])
        session.collect()
        if session.read(store.BLOBS + '/' + shared_hash, optional=True) is not None:
            raise store.StoreError('last-reference GC left the probe blob')
        if before != {name: session.read(store.ROOT + '/' + name, optional=True) for name in states}:
            raise store.StoreError('boot state changed')
    return ids


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sim')
    parser.add_argument('--base', default='opxy-121')
    parser.add_argument('--yes-write', action='store_true')
    args = parser.parse_args(argv)
    print(
        'Plan: copy %s into two fresh pkg-pass-* entries; retain synthetic content blobs, remove the entries,'
        % args.base
    )
    print('      verify shared/last-reference GC and identical state slots. No live content or boot operation.')
    if not args.yes_write:
        print('DRY RUN: nothing sent. Re-run with --yes-write to execute this storage test.')
        return 0
    host.SIM = args.sim
    dev, _, _ = host.connect(30)
    try:
        ids = exercise(dev, args.base)
        print('PASS: atomic add/remove, shared/last-reference GC, state unchanged; removed ' + ', '.join(ids))
        return 0
    except (OSError, ValueError) as error:
        print('FAILED: %s; stop and inspect the scratch entries before any further device action.' % error)
        return 1
    finally:
        dev.t.close()


if __name__ == '__main__':
    sys.exit(main())
