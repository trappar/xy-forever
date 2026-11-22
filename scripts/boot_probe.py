#!/usr/bin/env python3
"""#7: read both boot headers, the active image and two fixed RAM values on processor A.

No file, trim, sector-write, key-read, reset or OS-boot command is sent. The diagnostic selects boot
partitions only while A independently refuses writes and file operations; end restores partition 0.
Results and the raw image stay under the private folder. On a lost answer stop: no speculative reset.
Use --finish to retry restoration after a responsive diagnostic failure. End state: menu with keepalive.
"""

import argparse
import hashlib
import json
import struct
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path

from device_pass import Keepalive

from xy_forever import paths
from xy_forever import xyf_host as host

COMMAND, VERSION, MAX_BYTES = 0x64, 1, 0x700000
BEGIN, HEADER, SECTOR, END = range(4)


class ProbeError(Exception):
    pass


def call(dev, op, *words):
    info, dev.hello_info = dev.hello_info, None  # developer command, outside the published HELLO bitmap
    try:
        try:
            code, body = dev.call(COMMAND, struct.pack('<%dI' % (2 + len(words)), op, VERSION, *words), timeout=12)
        except OSError as error:
            raise ConnectionError(str(error)) from error
    finally:
        dev.hello_info = info
    if code == host.P.ids.S_LINK:
        raise ConnectionError('boot probe lost the processor link')
    if len(body) < 4 or struct.unpack_from('<I', body)[0] != VERSION:
        raise ProbeError('unsupported boot-probe reply version')
    if code:
        raise ProbeError('boot probe op %d: %s' % (op, host.P.status_name(code)))
    return body[4:]


def header(data, partition):
    if len(data) != 512:
        raise ProbeError('short header for partition %d' % partition)
    version, size, crc, valid, encrypted = struct.unpack_from('<IIIBB', data)
    return dict(
        partition=partition,
        version=version,
        bytes=size,
        crc32='%08x' % crc,
        valid=version >= 2 and 0 < size <= MAX_BYTES and valid != 0 and encrypted <= 1,
        valid_byte=valid,
        encrypted=encrypted,
    )


def run(dev, folder):
    """Only this diagnostic, no filesystem command or reboot. Always attempt end after responsive errors."""
    result = dict(
        format='xyf-boot-probe/1', headers=[], restored=False, go=False, recommendation='no-go: probe incomplete'
    )
    opened, lost = False, False
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                flags = call(dev, BEGIN)
                opened = True
                break
            except ProbeError as error:
                if 'busy' not in str(error) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        if len(flags) != 8:
            raise ProbeError('short A precondition snapshot')
        result['menu_pointer'], result['upgrade_resume'] = struct.unpack('<II', flags)
        for p in (1, 2):
            data = call(dev, HEADER, p)
            result['headers'].append(header(data, p))
            (folder / ('header-%d.bin' % p)).write_bytes(data)
        active = next((h for h in result['headers'] if h['valid']), None)
        if not active:
            raise ProbeError('neither boot partition has a valid image header')
        result['active_partition'] = active['partition']  # stock checks partition 1 before partition 2
        sha, crc, count = hashlib.sha256(), 0, 0
        with (folder / 'active-image.bin').open('wb') as output:
            for at in range(1, (active['bytes'] + 511) // 512 + 1):
                data = call(dev, SECTOR, active['partition'], at)
                if len(data) != 512:
                    raise ProbeError('short image sector %d' % at)
                data = data[: min(512, active['bytes'] - count)]
                output.write(data)
                sha.update(data)
                crc = zlib.crc32(data, crc)
                count += len(data)
        result.update(image_bytes=count, image_sha256=sha.hexdigest(), image_crc32='%08x' % crc)
        result['go'] = result['menu_pointer'] == 0 and result['upgrade_resume'] == 0
    except (ConnectionError, TimeoutError) as error:
        lost = True
        result['link_lost'] = True
        result['error'] = '%s; power cycle needed if the service no longer answers' % error
    except (ProbeError, OSError) as error:
        result['error'] = str(error)
    finally:
        if opened and not lost:
            try:
                call(dev, END)
                result['restored'] = True
            except (ProbeError, OSError, TimeoutError) as error:
                result['restore_error'] = str(error)
                if isinstance(error, (OSError, TimeoutError)):
                    result['link_lost'] = True
        result['go'] = result['go'] and result['restored'] and 'error' not in result
        result['recommendation'] = (
            'go for #8 design; no boot write authorized'
            if result['go']
            else 'no-go: A writer preconditions are nonzero'
            if result['restored'] and 'error' not in result
            else 'no-go: probe incomplete or partition restoration failed'
        )
        (folder / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sim', metavar='HOST:PORT,CONTROL')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--finish', action='store_true', help='only restore partition 0 and end an existing probe')
    args = parser.parse_args(argv)
    folder = args.out or paths.SESSIONS / (datetime.now().strftime('%Y-%m-%d-%H%M%S') + '-boot-probe')
    folder = paths.private_output(folder)
    keep = Keepalive()
    if not args.sim:
        keep.stop_running()
        time.sleep(2)
    if args.sim:
        host.SIM = args.sim
    try:
        dev, _, _ = host.connect(5)
    except SystemExit:
        print('NEED: power on holding PLAY')
        return 3
    result = None
    try:
        folder.mkdir(parents=True, exist_ok=False)
        if args.finish:
            result = dict(format='xyf-boot-probe/1', restored=False, go=False)
            try:
                call(dev, END)
                result['restored'] = True
            except (ConnectionError, TimeoutError) as error:
                result.update(error=str(error), link_lost=True)
            except ProbeError as error:
                result['error'] = str(error)
            (folder / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        else:
            result = run(dev, folder)
        print(json.dumps(result, indent=2))
        print('folder:', folder)
        return 0 if result['restored'] and 'error' not in result else 1
    finally:
        dev.t.close()
        if not args.sim and result is not None and not result.get('link_lost'):
            print('keepalive pid', keep.start(folder))


if __name__ == '__main__':
    sys.exit(main())
