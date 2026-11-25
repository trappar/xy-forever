"""`xyf dev` (#284): a full firmware image, booted once on the OP-XY as a development library entry.

Any os.ldr the device would boot from its library: usually Open-XY's own build (`python sdk.py build --stock-os STOCK
--out BUILD` writes BUILD/candidate/os.ldr and BUILD/candidate/content/: the catalog it extends and the machines' new
files), or any other image. Here:

  check  on the computer, before anything is sent, what the device checks of a library image when it boots one
         (file_boot.cpp admit; native/ldr.cpp inspect): an LDR stream within the staging buffer, every write inside
         the cold policy's allowed RAM and outside its protected ranges (target/cold.cpp), entry 0x11a00000 on a
         stored instruction. The manifest's CRC-32 and SHA-256 are computed from the bytes sent
  which  "Open-XY build on 1.2.1" when the image is exactly Open-XY's assembly of the stock 1.2.1 image (Open-XY's
         tooling/firmware.py `assemble`, inverted: the stock records plus one module record at the profile's module
         base, every byte outside the profile's edit intervals the stock image's; needs the stock os.ldr and
         Open-XY's profile), else "custom image". Said, never required
  push   one package transaction (package_store: staged, read back, published by one rename) of library entry
         xyf-dev. With content: os.ldr, content/... and a manifest marking it an assembled set (generation,
         needs_os, profile), so the content switch installs exactly its content/ files over the official set; its
         content retained as blobs. Without: a plain firmware entry, booted with the official content. Replaced on
         every push; top-level files that did not change are moved over on the device, not sent
  boot   menu.converge mode 2 (moddev.py): the content switch, then the boot once; nothing saved changes, and the
         next plain power-on converges back to the saved setup (#204)

No device-side change: every entry goes through the device's own checks too (the content switch's rules, the boot's
admission)."""

import hashlib
import json
import zlib
from pathlib import Path

from . import moddev, package_records, package_store
from .device_client import SLOTS, current_state
from .ldr import parse_ldr, validate_memory
from .xyf_entry import read_files_sha256

STOCK_SHA256 = '60358afdaa9b58ed40411602f6d5d4e1e3e02553ff2da7e25038e71dd002b52f'  # stock OS 1.2.1 (stock_facts.json)
STOCK_BYTES = 2373336
PROFILE = 'opxy-1.2.1'  # Open-XY's profile of it: profiles/opxy-1.2.1.json in an Open-XY checkout
ENTRY = 'xyf-dev'
NAME = 'Development build (xyf)'
ENTRY_POINT = 0x11A00000
STAGING_BYTES = 0x7E0000  # file_boot.hpp staging_bytes: admit takes at most this less 512 bytes
# target/cold.cpp's cold policy (allowed, protected_ranges) in the product build (XYF_STACK_END: xyf_build.WIDE_STACK_END)
ALLOWED = ((0x08000000, 0x08040000), (0x11800000, 0x11C00000), (0x80000000, 0x90000000))
PROTECTED = (
    (0x0803E0C0, 0x0803E142),
    (0x11803000, 0x11804000),
    (0x8803D000, 0x8803D02A),
    (0x88A346A6, 0x88A3481C),
    (0x88A34AEC, 0x88A34D6C),
    (0x8A000000, 0x8A800000),
    (0x8AB00000, 0x8AB84000),
    (0x8B000000, 0x8B800000),
)
OPEN_XY, CUSTOM = 'Open-XY build on 1.2.1', 'custom image'
CONTENT_STATE = '/yaffs2/xy-forever/content-state.txt'


class Refused(Exception):
    """What is given (or the OP-XY's state) is not what xyf dev boots: the message says why, in plain words."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# -- on the computer --------------------------------------------------------------------------------------
def candidate(folder):
    """The candidate folder of an `sdk.py build` output folder (or any folder holding os.ldr)."""
    folder = Path(folder)
    for found in (folder / 'candidate', folder):
        if (found / 'os.ldr').is_file():
            return found
    raise Refused(
        '%s holds no candidate/os.ldr or os.ldr: give the folder `sdk.py build --out FOLDER` wrote, or an os.ldr'
        % folder
    )


def read_build(folder):
    """(os.ldr bytes, {path under content/: bytes}) of a build's candidate folder, or of an os.ldr file (no content)."""
    if Path(folder).is_file():
        return Path(folder).read_bytes(), {}
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


def check_image(image):
    """Refused unless the device would admit image from its library (file_boot.cpp admit, native/ldr.cpp inspect)."""
    if not 32 <= len(image) <= STAGING_BYTES - 512:
        raise Refused('the os.ldr is %d bytes: XY Forever boots 32 to %d' % (len(image), STAGING_BYTES - 512))
    try:
        records = parse_ldr(image)
    except ValueError as error:
        raise Refused('the os.ldr is not an OS image (%s)' % error) from None
    if records[-1].target != ENTRY_POINT:
        raise Refused(
            'the os.ldr starts at %#x: XY Forever boots images that start at %#x' % (records[-1].target, ENTRY_POINT)
        )
    try:
        validate_memory(records, ALLOWED, PROTECTED)
    except ValueError as error:
        raise Refused('the os.ldr writes where XY Forever never loads an image (%s)' % error) from None


def open_xy_build(image, stock, profile):
    """True when image is exactly Open-XY's assembly of stock: the stock records with the profile's edits, plus one
    module record at the profile's module base before FINAL, and nothing else changed."""
    blocks, records = parse_ldr(stock), parse_ldr(image)
    if len(blocks) != profile['stock_records'] or len(records) != len(blocks) + 1:
        return False
    at, module = blocks[-1].offset, records[-2]
    if (
        module.offset != at
        or module.flags != 4
        or module.target != profile['module_base']
        or not 0 < module.count <= profile['reserved_bytes']
        or len(image) != len(stock) + 16 + module.count
    ):
        return False
    restored = bytearray(image[:at] + image[at + 16 + module.count :])
    restored[:16] = stock[:16]
    for patch in profile['patches']:
        offset = int(patch['file_offset'], 16)
        restored[offset : offset + patch['length']] = bytes.fromhex(patch['before'])
    return restored == stock


def which(image, stock_path, sdk):
    """OPEN_XY when image is Open-XY's build on the stock image at stock_path with sdk's profile, else CUSTOM (also
    when either is not there to compare with)."""
    try:
        return OPEN_XY if open_xy_build(image, read_stock(stock_path), read_profile(sdk)) else CUSTOM
    except Refused:
        return CUSTOM


def entry_files(image, content, kind=CUSTOM, needs_os=None):
    """The library entry: os.ldr, content/..., manifest.json and files.sha256. With content, an assembled set (its
    content/ files go over the official set); without, a plain firmware entry (the official content)."""
    digest = sha256(image)
    files = {'os.ldr': image, **{'content/' + rel: data for rel, data in content.items()}}
    fields = dict(
        type='firmware',
        name=NAME,
        kind='open-xy' if kind == OPEN_XY else 'custom',
        bytes=len(image),
        crc32='%08x' % (zlib.crc32(image) & 0xFFFFFFFF),
        sha256=digest,
    )
    if content:
        fields.update(needs_os=needs_os or STOCK_SHA256, generation='dev-' + digest[:12], profile=PROFILE)
    files[package_records.NATIVE] = package_records.document(fields)
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
    """Refused when the OP-XY cannot take the entry: xyf-dev its default, or, in the official content, no stock OS
    1.2.1 with a content list in its library: the content switch of a boot once needs one, even to stay official (the
    menu then says "Installing content failed"), and the device makes it only when mods are saved (#183)."""
    code, head = dev.read_file(CONTENT_STATE, 0, 256)
    official = code != 0 or b'state=official' in head
    if official and not any(
        dev.read_file('%s/%s/content.sha256' % (package_store.LIBRARY, b), 0, 16)[0] == 0
        for b in stock_entries(dev, needs_os)
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


def push(dev, files, detail=lambda line: None, kind=CUSTOM):
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
    total = sum(1 for rel in files if rel.startswith('content/'))
    changed = sum(1 for rel in sent if rel.startswith('content/'))
    content = '%d of %d content files sent' % (changed, total) if total else 'the official content'
    return '%s: %s, %s, %s' % (ENTRY, kind, image, content)


def boot(dev, detail=lambda line: None):
    """menu.converge mode 2 of the entry, followed until USB goes away: (booted, the phase's note)."""
    code, _ = moddev.patient(dev, lambda: moddev.converge(dev, moddev.ONCE, ENTRY))
    if code:
        return False, 'refused: %s' % moddev.plain(code)
    booted, why = moddev.follow(dev, detail)
    if not booted:
        return False, why
    return True, '%s started once; the next plain start goes back to the saved setup' % ENTRY


def run(dev, files, out=print, detail=lambda line: None, kind=CUSTOM):
    """[push], [boot], total on a connected, ready OP-XY. The exit code: 0 booted, 1 not."""
    phases = moddev.Phases(out)

    def pushed():
        try:
            return True, push(dev, files, detail, kind)
        except package_store.StoreError as error:  # the OP-XY refused a step of the transaction
            return False, 'failed: %s' % error

    booted = phases.run('push', pushed) and phases.run('boot', lambda: boot(dev, detail))
    phases.total()
    return 0 if booted else 1
