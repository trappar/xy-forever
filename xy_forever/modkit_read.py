"""Verified package input for modkit rebuilds; the raw file map is retained."""

from pathlib import Path

from . import package_records
from .xyf_entry import read_files_sha256


def read_package(folder, kind):
    """Verified raw files, manifest fields and link/patch extras, without changing source bytes."""
    from .modkit import MOD_FILES, KitError, check, sha256

    folder = Path(folder)
    check(folder.is_dir(), 'missing package %s' % folder)
    try:
        metadata = package_records.local_name(folder)
    except ValueError as error:
        raise KitError(str(error)) from error
    listed = read_files_sha256((folder / 'files.sha256').read_bytes())
    present = sorted(p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file())
    check(
        present == sorted([p for _, p in listed] + ['files.sha256']), '%s: files differ from files.sha256' % folder.name
    )
    files = {}
    for digest, rel in listed:
        data = (folder / rel).read_bytes()
        check(sha256(data) == digest, '%s: %s differs from files.sha256' % (folder.name, rel))
        files[rel] = data
    try:
        check(metadata is not None, '%s: no manifest.json or info.txt' % folder.name)
        manifest = package_records.parse(files[metadata], metadata)
    except ValueError as e:
        raise KitError('%s/%s: %s' % (folder.name, metadata or 'manifest.json or info.txt', e))
    check(manifest.get('type') == kind, '%s: expected type=%s' % (folder.name, kind))
    extra = package_records.values(files[metadata], metadata)
    if kind == 'mod':
        stray = sorted(p for p in files if not MOD_FILES.fullmatch(p))
        check(
            not stray, '%s carries %s: edits, definitions and addresses belong only to the base' % (folder.name, stray)
        )
    return files, manifest, extra
