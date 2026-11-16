#!/usr/bin/env python3
"""Compare native expansion of supplied OS images with the independent mapper."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parent
sys.path.insert(0, str(SOURCE / 'tests'))
from test_ldr import Native
from xy_forever.build_formats import Firmware
from xy_forever.paths import DECRYPTED, STOCK_121, private_output
from xy_forever.ldr import inspect_ldr, parse_ldr
from xy_forever.validation import validate_package
from xy_forever.outer import ALLOWED, PROTECTED


def validate_image(path, native):
    data = path.read_bytes()
    report = inspect_ldr(data)
    error, summary = native.inspect(data, ALLOWED, PROTECTED)
    if error or (summary.records, summary.expanded, summary.entry) != (
            report['records'], report['expanded_write_bytes'], int(report['entry'], 16)):
        raise ValueError(f'Native parser disagrees for {path}: {error}')
    independent = Firmware(data)
    memory = [(lo, hi, bytearray(hi - lo)) for lo, hi, _ in independent.segments]
    for record in parse_ldr(data)[1:-1]:
        for delta in range(0, record.count, 65536):
            chunk = native.expand(data, record.offset, delta, min(65536, record.count - delta))
            at, end = record.target + delta, record.target + delta + len(chunk)
            for lo, hi, destination in memory:
                left, right = max(lo, at), min(hi, end)
                if left < right:
                    destination[left - lo:right - lo] = chunk[left - at:right - at]
    golden = independent.memory_manifest()
    for (lo, hi, destination), row in zip(memory, golden):
        if hashlib.sha256(destination).hexdigest() != row['sha256']:
            raise ValueError(f'Final mapped bytes differ at {lo:#x}..{hi:#x}')
    report.update(final_segments_compared=len(memory),
                  final_bytes_compared=sum(hi - lo for lo, hi, _ in memory),
                  independent_final_memory_matches=True, native_parser_matches=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    out = private_output(args.out)
    out.mkdir(parents=True, exist_ok=False)
    native = Native(out)
    reports = {}
    for version, image, package in (
        ('1.2.1', STOCK_121 / 'os.ldr', DECRYPTED / 'opxy_firmware_1_2_1/firmware_secure.zip'),
        ('1.2.5', DECRYPTED / 'opxy_firmware_1_2_5/firmware/blackfin/bin/os.ldr',
         DECRYPTED / 'opxy_firmware_1_2_5/opxy_firmware_1_2_5.zip')):
        report = validate_image(image, native)
        _, package_report = validate_package(package)
        if report['sha256'] != package_report['os']['sha256']:
            raise ValueError('Package OS differs from independently acquired image')
        reports[version] = dict(image=report, package=package_report)
    result = dict(schema=1, hardware_tested=False, device_access=False, reports=reports,
                  limitations=['Host comparison does not emulate ROM/cache/processor handoff',
                               'Packets do not prove bridge transport or companion recovery'])
    (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({v: r['image'] for v, r in reports.items()}, indent=2))


if __name__ == '__main__':
    main()
