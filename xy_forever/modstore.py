"""The device's mod package store (#124, docs/storage/mods.md): how a package set sits on the OP-XY.

/yaffs2/xy-forever/mods/<generation>/store.txt names the store's version ("version=1"). Each package folder
holds its files flat: a top-level file (info.txt, files.sha256, objects.txt, catalog.txt, ...) under its own
name, every other file under its stored name, the first 12 hex digits of its SHA-256 plus the extension of
its last part (`content/presets/acidline/corroded.json` -> `3f9a1c0b7e21.json`). files.sha256 is the map
back: the device job (native/mod_job.hpp stored_name) reads a listed path from its stored name. So every
file sits 4 folders below /yaffs2 whatever the package's own folders are (the depth rule: docs/SAFETY.md).
Two listed files with the same content share one stored file; two with different content and the same
stored name are refused (a 48-bit prefix clash: rebuild or rename the package). No store.txt: an older
layout (mods/<profile>/<generation>/ before protocol 1.16, or a mirrored package tree), listed so that it
can be removed. clients/ts/src/modstore.ts is the same.
"""

import hashlib
import re

VERSION = 1
FILE = 'store.txt'
TEXT = b'version=%d\n' % VERSION
EXTENSION = re.compile(r'\.[A-Za-z0-9]{1,7}')
VERSION_LINE = re.compile(rb'version=([0-9]{1,9})(?=[\r\n]|\Z)')


class Clash(Exception):
    """Two different files of one package would get the same stored name."""


def stored_name(rel, sha):
    """A package file's name in the store: rel (files.sha256's path) as it is when it has no folder, else its
    SHA-256's first 12 hex digits plus its extension (from the last part's last '.', not its first character,
    then 1-7 of A-Z a-z 0-9)."""
    if '/' not in rel:
        return rel
    last = rel.rsplit('/', 1)[1]
    dot = last.rfind('.')
    return sha[:12].lower() + (last[dot:] if dot > 0 and EXTENSION.fullmatch(last[dot:]) else '')


def version_of(data):
    """store.txt's version (0 when its first line names none)."""
    m = VERSION_LINE.match(data or b'')
    return int(m.group(1)) if m else 0


def stored_files(name, files):
    """A package's files as local_set gives them ([(rel, bytes)], files.sha256 last) -> [(stored name, rel,
    bytes)] in the same order, one per stored name (files with the same content share it). Raises Clash."""
    out, seen = [], {}
    for rel, data in files:
        sha = hashlib.sha256(data).hexdigest()
        stored = stored_name(rel, sha)
        if stored in seen:
            if seen[stored] != sha:
                raise Clash('%s: %s and another file share the stored name %s' % (name, rel, stored))
            continue
        seen[stored] = sha
        out.append((stored, rel, data))
    return out


def listed_names(list_entries):
    """files.sha256's [(sha256 hex, rel)] -> the stored names it needs."""
    return {stored_name(rel, sha) for sha, rel in list_entries}


def copy_set(generation_dir, folder):
    """Writes a generation folder as `modkit split` left it into folder (a mods/<generation>/ on a simulator's or a
    test's storage) as a push leaves it: store.txt, then each package's files under their stored names."""
    from pathlib import Path

    root, folder = Path(generation_dir), Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / FILE).write_bytes(TEXT)
    for package in sorted(p for p in root.iterdir() if (p / 'files.sha256').is_file()):
        listed = (package / 'files.sha256').read_bytes()
        files = [
            (rel, (package / rel).read_bytes())
            for rel in (line.split('  ', 1)[1] for line in listed.decode().splitlines())
        ] + [('files.sha256', listed)]
        (folder / package.name).mkdir(exist_ok=True)
        for stored, _, data in stored_files(package.name, files):
            (folder / package.name / stored).write_bytes(data)
