"""xyf-host cp: copy a file or folder on the device with file.copy and file.mkdir."""

import time

from xy_forever import depth
from xy_forever import usb_proto as P


def copy_command(dev, source, target, recursive):
    """cp: the source's kind and size from its folder's listing, then copy_entry."""
    parent, _, name = source.rpartition('/')
    code, entries = dev.list_dir(parent or '/yaffs2')
    found = [(k, s) for k, s, n in entries if n == name.encode('utf-8')]
    if code or not found:
        code = code or P.STATUS_NAMES.index('not_found')
        print('cp %s: %s' % (source, P.status_name(code)))
        return code
    if found[0][0] == 1 and not recursive:
        print('cp: %s is a folder (cp -r copies folders)' % source)
        return 2
    deep = found[0][0] == 1 and depth.copy_refusal(dev.list_dir, source, target)
    return deep or copy_entry(dev, source, target, *found[0])


def copy_entry(dev, source, target, kind, size):
    """One file.copy per file; a folder: file.mkdir of the target, then each entry of its listing."""
    if kind == 0:
        start = time.monotonic()
        code, done, error, partial = dev.copy(source, target, size)
        print(
            'cp %s %s: %s%s%s%s'
            % (
                source,
                target,
                P.status_name(code),
                '' if code else ', %d B in %.2f s' % (done, time.monotonic() - start),
                ' (error %d)' % error if error else '',
                '; a partial target may remain' if partial else '',
            )
        )
        return code
    if kind != 1:
        print('cp %s: skipped (not a file or folder)' % source)
        return 0
    code, error = dev.change(P.ids.MAKE_DIR, target)
    print('mkdir %s: %s%s' % (target, P.status_name(code), ' (error %d)' % error if error else ''))
    if code:
        return code
    code, entries = dev.list_dir(source)
    if code:
        print('ls %s: %s' % (source, P.status_name(code)))
        return code
    for k, s, n in entries:
        name = n.decode('utf-8')
        code = copy_entry(dev, source + '/' + name, target + '/' + name, k, s)
        if code:
            return code
    return 0
