"""`xyf_host.py library-add`: an official os.ldr as library entry ID (docs/storage/layout.md).

Writes firmware/ID/os.ldr and info.txt and, from the user's own extracted official content (--content), the
entry's content set: firmware.version, then content.sha256 (docs/storage/packages.md: the two come together or
not at all; the list goes last, so it is never there without its version). Mod builds (the Mods page's Save,
`mods apply`) need their base's content.sha256. The list is computed here, at run time, from the files the user
gives; the repository never holds official content or its hashes. Without --content the entry is still added
(it boots and can be the default) and the tool says what is missing and how to add it.
"""
from pathlib import Path
import re
import zlib

from .content_manifest import LIST, VERSION, ContentError, list_text, read_version, scan
from .ldr import parse_ldr
from .xyf_entry import ListError, info_text, read_files_sha256

ID_RE = re.compile(r'^[a-z0-9_-]{1,31}$')
INFO_TEXT_RE = re.compile(r'^[ -<>-~]{1,47}$')   # printable ASCII except '='
LIBRARY = '/yaffs2/xy-forever/firmware/'
HOW = ('Give --content DIR: the official package\'s extracted content folder (firmware/blackfin/content, its '
       'firmware.version two folders up, or --version-file FILE), or a folder holding content.sha256 + '
       'firmware.version (`xyf_package.py content-manifest CONTENT --out DIR` writes one).')


def add_arguments(parser):
    parser.add_argument('id'); parser.add_argument('os_ldr', type=Path)
    parser.add_argument('--name', required=True); parser.add_argument('--version', required=True)
    parser.add_argument('--content', type=Path, help='the official content of this release (see the plan): the entry '
                        'gets content.sha256 + firmware.version, which mod builds need of their base')
    parser.add_argument('--version-file', type=Path, help='firmware.version for --content (default: two folders up)')
    parser.add_argument('--yes-write', action='store_true')


def content_set(source, version_file=None):
    """--content -> (content.sha256 bytes, firmware.version bytes, version text, files). source is an extracted
    official content folder or a folder holding content.sha256 + firmware.version; ContentError says why not."""
    source = Path(source)
    if (source / LIST).is_file():
        data = (source / LIST).read_bytes()
        try:
            count = len(read_files_sha256(data))
        except ListError as error:
            raise ContentError('%s: %s' % (source / LIST, error))
        raw, version = read_version(source, version_file or source / VERSION)
    else:
        if not source.is_dir():
            raise ContentError('not a folder')
        raw, version = read_version(source, version_file)
        entries = scan(source)
        data, count = list_text(entries), len(entries)
    if not count:
        raise ContentError('empty content list: %s' % source)
    return data, raw, version, count


def missing_note(entry_id):
    return ('note: firmware/%s gets no content.sha256 + firmware.version, so mod builds (the Mods page\'s Save, '
            '`mods apply --base %s`) refuse it as their base; booting it and making it the default work.\n      %s'
            % (entry_id, entry_id, HOW))


def plan(args, capacity, out=print):
    """Checks and prints the plan -> (exit code, [(remote path, bytes)] to write, or None: stop here)."""
    if not ID_RE.match(args.id):
        out('refused: id must be 1-31 of [a-z0-9_-]'); return 2, None
    if not INFO_TEXT_RE.match(args.name) or not INFO_TEXT_RE.match(args.version) or len(args.version) > 31:
        out('refused: name (<= 47) and version (<= 31) must be printable ASCII without = or newlines'); return 2, None
    data = args.os_ldr.read_bytes()
    try:
        records = parse_ldr(data)
    except Exception as error:
        out('refused: not an LDR stream: %s' % error); return 2, None
    if records[-1].target != 0x11a00000 or not data or len(data) > capacity:
        out('refused: entry must be 0x11a00000 and size 1..%d' % capacity); return 2, None
    base = LIBRARY + args.id + '/'
    info = info_text(args.name, args.version, 'official', data).encode('ascii')
    files = [(base + 'os.ldr', data), (base + 'info.txt', info)]
    out('Plan: %sos.ldr (%d bytes, CRC-32 %08x), then info.txt:' % (base, len(data), zlib.crc32(data) & 0xffffffff))
    out('      ' + info.decode().strip().replace('\n', '\n      '))
    if args.content is not None:
        try:
            listed, raw, version, count = content_set(args.content, args.version_file)
        except (ContentError, OSError) as error:
            out('refused: --content %s: %s\n         %s' % (args.content, error, HOW)); return 2, None
        if version.split('+')[0] != args.version.split('+')[0]:
            out('refused: --content is firmware %s, the entry is --version %s (give the content of the same '
                'release)' % (version, args.version)); return 2, None
        files += [(base + VERSION, raw), (base + LIST, listed)]
        out('      then %s (%s) and %s (%d files, from %s)' % (VERSION, version, LIST, count, args.content))
    out('      Each write on a fresh boot (REBOOT before, between and after). The default is unchanged.')
    if args.content is None:
        out(missing_note(args.id))
    if not args.yes_write:
        out('DRY RUN: nothing sent. Re-run with --yes-write to write.')
        return 0, None
    return 0, files


def write(dev, args, files, fresh, put, out=print):
    """Writes the planned files, each on a fresh boot (fresh(dev) -> dev or None; put(dev, data, remote) -> code)."""
    for remote, body in files:
        dev = fresh(dev)
        if dev is None:
            return 11
        code = put(dev, body, remote)
        if code:
            return code
    dev = fresh(dev)                                   # leave a fresh boot (CONTINUE/set-default ready)
    out('library-add %s: ok%s' % (args.id, '' if len(files) > 2 else ' (no content list)'))
    if len(files) == 2:
        out(missing_note(args.id))
    return 0 if dev is not None else 11
