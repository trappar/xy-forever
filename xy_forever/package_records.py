"""Package metadata selection; JSON wins whenever its file is present.

Legacy text remains lenient for library/display reads. Strict package admission
uses xyf_entry.parse_manifest after the bounded JSON adapter. Keep the original
bytes for files.sha256 and journal source checks before requesting this view.
"""
from pathlib import Path

from . import native_json

NATIVE = 'manifest.json'
LEGACY = 'info.txt'


def record_name(names):
    """The authoritative native/legacy file name, or None when both are absent."""
    names = set(names)
    return NATIVE if NATIVE in names else LEGACY if LEGACY in names else None


def local_name(folder):
    """Select by basename before checking whether the selected record is readable."""
    folder = Path(folder)
    for name in (NATIVE, LEGACY):
        path = folder / name
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        if not path.is_file():
            raise native_json.RecordError('%s: expected a regular package record' % path)
        return name
    return None


def text_view(data, name=LEGACY):
    if name != NATIVE:
        return data
    try:
        return native_json.package_text(data)
    except native_json.RecordError as error:
        raise native_json.RecordError('%s: %s' % (name, error)) from error


def values(data, name=LEGACY):
    """All fields, including the link/patch extras; legacy last-key-wins retained."""
    return dict(line.split('=', 1) for line in text_view(data, name).decode('utf-8', 'replace').splitlines()
                if '=' in line and not line.startswith('#'))


def read_values(folder):
    folder = Path(folder)
    name = local_name(folder)
    if name is None:
        raise FileNotFoundError('%s: no manifest.json or info.txt' % folder)
    return values((folder / name).read_bytes(), name)


def parse(data, name=LEGACY):
    from .xyf_entry import parse_manifest
    return parse_manifest(text_view(data, name))
