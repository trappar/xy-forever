"""Compose bounded structural checks before host staging."""
from collections import Counter
import hashlib
import zipfile

from .companions import CyacdImage, NordicApplication
from .ldr import inspect_ldr
from .packages import inspect_package


def validate_package(path):
    plan = inspect_package(path)
    report = dict(archive_sha256=plan.archive_sha256, members=len(plan.members),
                  roles=dict(Counter(m.role for m in plan.members)),
                  excluded=[m.source for m in plan.members if m.destination is None],
                  hardware_tested=False, vendor_signature_verified=False)
    with zipfile.ZipFile(path) as archive:
        for member in plan.members:
            if member.role not in ('os', 'backplane', 'ble'):
                continue
            with archive.open(member.source) as stream:
                data = stream.read(member.size + 1)
            if len(data) != member.size or hashlib.sha256(data).hexdigest() != member.sha256:
                raise ValueError('Package changed during component validation')
            if member.role == 'os':
                report['os'] = inspect_ldr(data)
            elif member.role == 'backplane':
                image = CyacdImage.parse(data)
                image.require_opxy_layout()
                report['backplane'] = dict(sha256=image.sha256, rows=len(image.rows),
                                          silicon_id=hex(image.silicon_id))
            elif member.role == 'ble':
                image = NordicApplication.parse(data)
                report['ble'] = dict(sha256=image.sha256, bytes=len(image.data),
                                    crc16=image.checksum)
    return plan, report
