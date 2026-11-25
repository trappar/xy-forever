"""`xyf dev` (#284): Open-XY's own firmware build, booted once on the OP-XY as a development library entry.

`python sdk.py build --stock-os STOCK --out BUILD` writes BUILD/candidate/os.ldr (stock OS 1.2.1 with Open-XY's edits
and one module record) and BUILD/candidate/content/ (the catalog it extends and the machines' new files). Here:

  check  on the computer, before anything is sent: the image is Open-XY's assembly of the exact stock 1.2.1 image
         (Open-XY's tooling/firmware.py `assemble`, inverted): the stock records plus one module record at the
         profile's module base, every byte outside the profile's edit intervals the stock image's; an LDR stream
         whose entry is 0x11a00000, within the library's image capacity (as `xyf-host library-add`)
  push   one package transaction (package_store: staged, read back, published by one rename) of library entry
         xyf-dev: os.ldr, content/... and a manifest marking it an assembled set (generation, needs_os, profile), so
         the device's content switch installs exactly its content/ files; its content retained as blobs. The
         entry is replaced on every push; files that did not change are moved over on the device, not sent
  boot   menu.converge mode 2 (moddev.py): the content switch, then the boot once; nothing saved changes, and the
         next plain power-on converges back to the saved setup (#204)

The device applies its own checks as to every library entry: the strict package admission on the host side
(package_store.validate), the content switch's rules (no official file replaced, the catalog against the official
list) and the boot's admission of the image (file_boot.cpp admit: the cold LDR policy and the entry)."""

import hashlib
import json
import zlib
from pathlib import Path

from . import moddev, package_records, package_store
from .device_client import SLOTS, current_state
from .ldr import parse_ldr
from .xyf_entry import read_files_sha256

STOCK_SHA256 = '60358afdaa9b58ed40411602f6d5d4e1e3e02553ff2da7e25038e71dd002b52f'  # stock OS 1.2.1 (stock_facts.json)
STOCK_BYTES = 2373336
PROFILE = 'opxy-1.2.1'  # Open-XY's profile of it: profiles/opxy-1.2.1.json in an Open-XY checkout
ENTRY = 'xyf-dev'
NAME = 'Development build (xyf)'
ENTRY_POINT = 0x11A00000
CAPACITY = 0x700000  # the library's image capacity (host_install.IMAGE_CAPACITY)
CONTENT_STATE = '/yaffs2/xy-forever/content-state.txt'


class Refused(Exception):
    """What is given (or the OP-XY's state) is not what xyf dev boots: the message says why, in plain words."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# -- on the computer --------------------------------------------------------------------------------------
def candidate(folder):
    """The candidate folder of an `sdk.py build` output folder (or the candidate folder itself)."""
    folder = Path(folder)
    for found in (folder / 'candidate', folder):
        if (found / 'os.ldr').is_file():
            return found
    raise Refused('%s holds no candidate/os.ldr: give the folder `sdk.py build --out FOLDER` wrote' % folder)


def read_build(folder):
    """(os.ldr bytes, {path under content/: bytes}) of a build's candidate folder."""
    found = candidate(folder)
    content = found / 'content'
    others = sorted(p.name for p in found.iterdir() if p.name not in ('os.ldr', 'content'))
    if others:
        raise Refused('%s holds files xyf dev does not know: %s' % (found, ', '.join(others)))
    files = {}
    if content.is_dir():
        files = {p.relative_to(content).as_posix(): p.read_bytes() for p in sorted(content.rglob('*')) if p.is_file()}
    return (found / 'os.ldr').read_bytes(), files


def read_profile(sdk):
    """Open-XY's profile of stock OS 1.2.1 from an Open-XY checkout."""
    path = Path(sdk) / 'profiles' / (PROFILE + '.json')
    try:
        profile = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise Refused('no Open-XY profile at %s: run xyf from your Open-XY checkout or give --sdk DIR' % path) from None
    if profile.get('stock_sha256') != STOCK_SHA256:
        raise Refused('%s is not the profile of stock OS 1.2.1' % path)
    return profile


def read_stock(path):
    """The stock OS 1.2.1 os.ldr at path, checked by size and SHA-256."""
    try:
        stock = Path(path).read_bytes()
    except OSError:
        raise Refused('no stock OS 1.2.1 at %s: run `xyf stock-os` (it copies it from your OP-XY)' % path) from None
    if len(stock) != STOCK_BYTES or sha256(stock) != STOCK_SHA256:
        raise Refused('%s is not the stock OS 1.2.1 os.ldr (size or SHA-256 differs)' % path)
    return stock


def check_image(image, stock, profile):
    """Refused unless image is Open-XY's assembly of stock: the stock records with the profile's edits, plus one
    module record at the profile's module base before FINAL, and nothing else changed."""
    try:
        blocks, records = parse_ldr(stock), parse_ldr(image)
    except ValueError as error:
        raise Refused('the candidate os.ldr is not an OS image (%s)' % error) from None
    if records[-1].target != ENTRY_POINT or len(image) > CAPACITY:
        raise Refused('the candidate os.ldr is not an OS image XY Forever boots (entry or size)')
    if len(blocks) != profile['stock_records'] or len(records) != len(blocks) + 1:
        raise Refused(
            'the candidate os.ldr is not an Open-XY build of OS 1.2.1 (%d records; Open-XY adds one to the stock %d)'
            % (len(records), len(blocks))
        )
    at, module = blocks[-1].offset, records[-2]
    if (
        module.offset != at
        or module.flags != 4
        or module.target != profile['module_base']
        or not 0 < module.count <= profile['reserved_bytes']
        or len(image) != len(stock) + 16 + module.count
    ):
        raise Refused("the candidate os.ldr's module is not where Open-XY puts it (profile %s)" % PROFILE)
    restored = bytearray(image[:at] + image[at + 16 + module.count :])
    restored[:16] = stock[:16]
    for patch in profile['patches']:
        offset = int(patch['file_offset'], 16)
        restored[offset : offset + patch['length']] = bytes.fromhex(patch['before'])
    if restored != stock:
        raise Refused('the candidate os.ldr differs from stock OS 1.2.1 outside the edits Open-XY makes')


def entry_files(image, content, needs_os=None):
    """The library entry: os.ldr, content/..., manifest.json (an assembled set) and files.sha256."""
    digest = sha256(image)
    files = {'os.ldr': image, **{'content/' + rel: data for rel, data in content.items()}}
    files[package_records.NATIVE] = package_records.document(
        dict(
            type='firmware',
            name=NAME,
            kind='open-xy',
            bytes=len(image),
            crc32='%08x' % (zlib.crc32(image) & 0xFFFFFFFF),
            sha256=digest,
            needs_os=needs_os or STOCK_SHA256,
            generation='dev-' + digest[:12],
            profile=PROFILE,
        )
    )
    try:
        files['files.sha256'] = package_store.list_text(files)
        package_store.validate(files)
    except ValueError as error:  # a path the package rules refuse (ListError, StoreError)
        raise Refused('the build cannot be a library entry: %s' % error) from None
    return files


# -- on the OP-XY -----------------------------------------------------------------------------------------
def stock_entries(dev, needs_os=None):
    """The library entries whose os.ldr is needs_os (by their manifest's sha256; no assembled set), or Refused."""
    code, entries = dev.list_dir(package_store.LIBRARY)
    if code:
        raise Refused('the library could not be read (%s)' % moddev.plain(code))
    found = []
    for kind, _, raw in entries:
        name = raw.decode('utf-8', 'replace')
        if kind != 1 or name == ENTRY:
            continue
        for record in (package_records.NATIVE, package_records.LEGACY):
            code, data = dev.read_all('%s/%s/%s' % (package_store.LIBRARY, name, record), limit=4096)
            if code:
                continue
            try:
                fields = package_records.parse(data, record)
            except ValueError:
                fields = {}
            if fields.get('sha256') == (needs_os or STOCK_SHA256) and 'generation' not in fields:
                found.append(name)
            break
    if not found:
        raise Refused("the OP-XY's library has no stock OS 1.2.1 (XY Forever adds it on its first start)")
    return found


def preflight(dev, needs_os=None):
    """Refused when the OP-XY cannot take the entry: no stock OS 1.2.1 in its library, its content not switchable
    (the official content, and no content list of OS 1.2.1 yet), or xyf-dev its default."""
    bases = stock_entries(dev, needs_os)
    code, head = dev.read_file(CONTENT_STATE, 0, 256)
    official = code != 0 or b'state=official' in head
    if official and not any(
        dev.read_file('%s/%s/content.sha256' % (package_store.LIBRARY, b), 0, 16)[0] == 0 for b in bases
    ):
        raise Refused(
            'the OP-XY has no content list of OS 1.2.1 yet: save mods once on the OP-XY (its Mods page), '
            'then run xyf dev again'
        )
    slots = {}
    for path in SLOTS:
        code, body = dev.read_file(path)
        slots[path] = body if code == 0 else None
    state = current_state(slots)
    if state and state[2].get('default') == ENTRY:
        raise Refused('%s is the default on the OP-XY: choose another default first' % ENTRY)


def push(dev, files, detail=lambda line: None):
    """One package transaction: the content retained as blobs, then the entry published. The phase's note."""
    target = '%s/%s' % (package_store.LIBRARY, ENTRY)
    content = [(sha, rel[len('content/') :]) for sha, rel in read_files_sha256(files['files.sha256'])]
    content = [(sha, rel) for sha, rel in content if ('content/' + rel) in files]
    with package_store.Session(dev) as session:
        old = session.read(target + '/files.sha256', optional=True)
        before = {rel: sha for sha, rel in read_files_sha256(old)} if old else {}
        keep = [  # moved over on the device: top-level files only (publish's scratch has no subfolders to move into)
            rel
            for rel, data in files.items()
            if '/' not in rel and rel != 'files.sha256' and before.get(rel) == sha256(data)
        ]
        session.retain(content, {rel: files['content/' + rel] for _, rel in content})
        session.publish(target, files, keep=keep)
        removed = session.collect()
    sent = [rel for rel in files if rel not in keep]
    detail('%s: sent %s; kept %d; blobs collected %d' % (ENTRY, ', '.join(sorted(sent)), len(keep), removed))
    image = 'os.ldr unchanged' if 'os.ldr' in keep else 'os.ldr %d KB' % (len(files['os.ldr']) // 1024)
    changed = sum(1 for rel in sent if rel.startswith('content/'))
    return '%s: %s, %d of %d content files sent' % (
        ENTRY,
        image,
        changed,
        sum(1 for rel in files if rel.startswith('content/')),
    )


def boot(dev, detail=lambda line: None):
    """menu.converge mode 2 of the entry, followed until USB goes away: (booted, the phase's note)."""
    code, _ = moddev.patient(dev, lambda: moddev.converge(dev, moddev.ONCE, ENTRY))
    if code:
        return False, 'refused: %s' % moddev.plain(code)
    booted, why = moddev.follow(dev, detail)
    if not booted:
        return False, why
    return True, '%s started once; the next plain start goes back to the saved setup' % ENTRY


def run(dev, files, out=print, detail=lambda line: None):
    """[push], [boot], total on a connected, ready OP-XY. The exit code: 0 booted, 1 not."""
    phases = moddev.Phases(out)

    def pushed():
        try:
            return True, push(dev, files, detail)
        except package_store.StoreError as error:  # the OP-XY refused a step of the transaction
            return False, 'failed: %s' % error

    booted = phases.run('push', pushed) and phases.run('boot', lambda: boot(dev, detail))
    phases.total()
    return 0 if booted else 1
