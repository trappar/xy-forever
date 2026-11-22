"""Selection and bounded decoding of saved Mods configuration on the host."""

from . import native_json
from . import usb_proto as P

ROOT = '/yaffs2/xy-forever'
NAMES = ('config.json', 'config.json.old', 'config.txt', 'config.old')


def record_name(names):
    entries = set(names)
    return next((name for name in NAMES if name in entries or name.encode('ascii') in entries), None)


def limit(name):
    return native_json.DOCUMENT_BYTES if name in NAMES[:2] else 2048


def fields(data, name):
    """Decode the selected record, including legacy format and size checks."""
    cap = limit(name)
    if len(data) > cap:
        raise ValueError('%s exceeds %d bytes' % (name, cap))
    native = name in NAMES[:2]
    if native:
        try:
            data = native_json.config_text(data)
        except ValueError as error:
            raise ValueError('%s: %s' % (name, error)) from error
    text = data.decode('utf-8', 'replace')
    if not native and any(
        line.startswith('format=') and line.removesuffix('\r') != 'format=1' for line in text.split('\n')
    ):
        raise ValueError('%s: unsupported legacy configuration format' % name)
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def read(dev):
    """Return (name, exact bytes, fields), or None if no configuration is present."""
    code, entries = dev.list_dir(ROOT)
    if code == P.ids.S_NOT_FOUND:
        return None
    if code:
        raise ValueError('list saved configuration: %s' % P.status_name(code))
    name = record_name(raw for _, _, raw in entries)
    if name is None:
        return None
    code, data = dev.read_all(ROOT + '/' + name, limit=limit(name) + 1)
    if code:
        raise ValueError('read %s: %s' % (name, P.status_name(code)))
    return name, data, fields(data, name)
