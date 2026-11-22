"""Review official decrypted archives without assuming what a device has installed.

No device transports, keys, activation or companion programming are available here.
A baseline is another vendor archive, not a measurement of installed components.
"""

import hashlib
import re
import zipfile
from dataclasses import asdict
from pathlib import Path

from .companions import CyacdImage, NordicApplication
from .ldr import inspect_ldr
from .packages import PackageError, inspect_package, stage_package

_VERSION = re.compile(rb'([0-9]{1,5})\.([0-9]{1,5})\.([0-9]{1,5})\+([0-9]{1,5})(?:@[0-9a-fA-F]{40})?\r?\n?\Z')
_VERSION_PATHS = {
    'os': 'firmware/blackfin/bin/os.version',
    'backplane': 'firmware/backplane/bin/backplane.version',
    'ble': 'firmware/ble/bin/ble.version',
    'firmware': 'firmware/firmware.version',
}


def _version(data):
    match = _VERSION.fullmatch(data)
    if not match or any(int(field) > 65535 for field in match.groups()):
        raise PackageError('invalid component version')
    return list(map(int, match.groups()))


def review_archive(path):
    """Fully hash archive and check payload structures; does not authenticate it."""
    path = Path(path)
    plan = inspect_package(path)
    by_name = {member.source: member for member in plan.members}
    versions, components = {}, {}
    with zipfile.ZipFile(path) as archive:

        def read(member):
            data = archive.read(member.source)
            if len(data) != member.size or hashlib.sha256(data).hexdigest() != member.sha256:
                raise PackageError('archive changed during semantic inspection')
            return data

        for role, name in _VERSION_PATHS.items():
            if name in by_name:
                if by_name[name].size > 128:
                    raise PackageError('oversized component version')
                versions[role] = _version(read(by_name[name]))
        for member in plan.members:
            if member.role not in ('os', 'backplane', 'ble'):
                continue
            data = read(member)
            if member.role == 'os':
                detail = inspect_ldr(data)
            elif member.role == 'backplane':
                image = CyacdImage.parse(data)
                image.require_opxy_layout()
                detail = {'rows': len(image.rows), 'silicon_id': hex(image.silicon_id)}
            else:
                image = NordicApplication.parse(data)
                detail = {
                    'initial_stack': hex(image.initial_stack),
                    'reset_vector': hex(image.reset_vector),
                    'crc16': image.checksum,
                }
            components[member.role] = {
                'sha256': member.sha256,
                'bytes': member.size,
                'version': versions.get(member.role),
                'structure': detail,
            }
    for role in ('backplane', 'ble'):
        if (role in versions) != (role in components):
            raise PackageError('companion payload and version must occur together: ' + role)
    # Cover excluded members and archive metadata too, including a late mutation.
    if inspect_package(path) != plan:
        raise PackageError('archive changed during semantic inspection')
    return plan, {
        'archive_sha256': plan.archive_sha256,
        'archive_bytes': plan.archive_bytes,
        'versions': versions,
        'components': components,
    }


def review_update(path, baseline=None):
    desired, report = review_archive(path)
    previous = None
    if baseline is not None:
        previous, old = review_archive(baseline)
        report['baseline_archive_sha256'] = previous.archive_sha256
        report['baseline_versions'] = old['versions']
    else:
        old = {'components': {}}
    report['schema'] = 1
    report['purpose'] = 'offline-review-only'
    report['installed_state_known'] = False
    report['authenticated'] = False
    report['hardware_tested'] = False
    for role, component in report['components'].items():
        prior = old['components'].get(role)
        component['change'] = (
            'unknown'
            if baseline is None
            else 'added'
            if prior is None
            else 'unchanged'
            if prior['sha256'] == component['sha256']
            else 'changed'
        )
        component['previous_version'] = prior['version'] if prior else None
        component['next_step'] = (
            'stage as a new library entry; device admission still required'
            if role == 'os'
            else 'compare with live identity and version; programming requires separate authorization'
        )
    current = {m.destination: m for m in desired.members if m.role == 'content'}
    prior_content = {m.destination: m for m in previous.members if m.role == 'content'} if previous else {}
    changes = {'added': [], 'changed': [], 'unchanged': [], 'previous_only': []}
    for name, member in sorted(current.items()):
        prior = prior_content.get(name)
        kind = 'added' if prior is None else 'unchanged' if prior.sha256 == member.sha256 else 'changed'
        changes[kind].append(name)
    changes['previous_only'] = sorted(set(prior_content) - set(current))
    report['content'] = {
        'files': len(current),
        'bytes': sum(m.size for m in current.values()),
        'comparison_available': baseline is not None,
        'changes': changes if baseline is not None else None,
        'policy': 'preserve user changes; previous-only files are not deletion instructions',
    }
    report['excluded'] = [asdict(m) for m in desired.members if m.destination is None]
    report['required_before_device_install'] = [
        'authenticate the package against an independently trusted source',
        'check OS load destinations against the actual loader memory policy',
        'inventory current content and companion versions on the device',
        'prove companion transport, compatibility and interrupted-update recovery',
        'keep root bootloader, SPI metadata and OTP untouched',
    ]
    return report


def prepare_update(path, output, baseline=None):
    """Stage reviewed bytes on the host. No target write or activation plan exists."""
    report = review_update(path, baseline)
    plan = stage_package(path, output)
    if plan.archive_sha256 != report['archive_sha256']:
        # The staging tree is complete as an archive extraction, but not as this
        # reviewed update. Invalidate it before returning a failure.
        (Path(output) / 'INCOMPLETE').write_text('Archive changed after update review.\n')
        raise PackageError('archive changed after update review')
    return report
