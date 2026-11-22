"""The depth rule (#124, docs/SAFETY.md): no file more than MAX_FOLDERS folders below /yaffs2.

The stock dev-menu MTP walks /yaffs2 with fixed 10-entry stacks (889f1ff6): after it has walked a file 7
folders deep, the dev menu's Track 5 in that boot hangs and the next boot needs a storage repair (8: it cannot
read or delete the file; 9: it crashes). XY Forever's file service refuses deeper folders and files
(target/file_engine.hpp, file_client::rename for a folder moved deeper); the host tools check first so that
they say why. Reads, listing and removals work at any depth, so an older deep tree can still be deleted.
"""

from xy_forever import usb_proto as P

MAX_FOLDERS = 6
TEXT = (
    "more than %d folders below /yaffs2 (deeper hangs the dev menu's MTP, then Track 5: docs/SAFETY.md)" % MAX_FOLDERS
)


def folders(path, folder=False):
    """The folders below /yaffs2 that a device path sits in (a folder: counting itself)."""
    names = [n for n in path[len('/yaffs2') :].split('/') if n]
    return len(names) - (0 if folder else 1)


def refusal(path, folder=False, below=0):
    """None when path (a folder: with `below` levels of folders inside it) keeps the rule, else the message."""
    if folders(path, folder) + below <= MAX_FOLDERS:
        return None
    return 'refused: %s%s is %s' % (path, ' with the folders inside it' if below else '', TEXT)


def folders_below(list_dir, folder, most=MAX_FOLDERS + 1):
    """Levels of folders inside a device folder (0: none), through list_dir(path) -> (code, [(kind, size, name)]);
    stops counting at most. (code, levels)."""
    code, entries = list_dir(folder)
    if code:
        return code, 0
    deepest = 0
    for kind, _, name in entries:
        if kind == 1 and most > 0:
            code, below = folders_below(list_dir, folder + '/' + name.decode('utf-8'), most - 1)
            if code:
                return code, 0
            deepest = max(deepest, below + 1)
    return 0, min(deepest, most)


def command_refusal(args):
    """xyf_host.py: the message refusing a mkdir, put, put-file, mv or cp whose path breaks the rule, else None."""
    command = args.command
    if command == 'mkdir':
        return refusal(args.path, True)
    if command in ('put', 'put-file'):
        return refusal(args.remote)
    return refusal(args.target.rstrip('/')) if command in ('mv', 'cp') else None


def move_note(code):
    """xyf_host.py mv: why the device refused a move (the folder walk of file.rename), appended to its result line."""
    if code != P.STATUS_NAMES.index('refused'):
        return ''
    return (
        '\n  (a folder moved deeper must keep every folder in it within %d below /yaffs2: docs/SAFETY.md)' % MAX_FOLDERS
    )


def copy_refusal(list_dir, source, target):
    """cp -r: 0 when the folder source, copied to target, keeps the rule; else prints why and returns the exit code."""
    code, below = folders_below(list_dir, source)
    deep = code or refusal(target, True, below)
    if deep:
        print(deep if not code else 'ls %s: %s' % (source, P.status_name(code)))
    return code or (2 if deep else 0)
