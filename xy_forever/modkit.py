"""Mod packages from an open-xy kit (docs/decisions/008, 010, 014; docs/STORAGE.md).

    python3 -m xy_forever.modkit split KIT.json --sdk OPEN_XY [--sdk-commit C] --out DIR
                                 [--sdk-source URL@COMMIT] [--extension-catalog extensions.json]
    python3 -m xy_forever.modkit rebuild GENERATION_DIR --machine ID ... [--extension ID ...]
                                 --catalog op1_factory.json --out KIT.json
    python3 -m xy_forever.modkit prove KIT.json GENERATION_DIR --sdk OPEN_XY [--sdk-commit C]
                                 --catalog op1_factory.json [--stock os.ldr] [--scratch DIR]
    python3 -m xy_forever.modkit prove-patch GENERATION_DIR --sdk OPEN_XY [--sdk-commit C]
                                 --catalog op1_factory.json --stock os.ldr [--scratch DIR] [--keep DIR]
                                 [--official content.sha256] [--memory standard|expanded]
    python3 -m xy_forever.modkit known-base BASE_DIR... [--stock os.ldr] [--catalog JSON] [--write]

split reads a published kit (opxy-browser-kit/1) and writes one mod-base package for the kit
generation, one mod package per machine and one per extension under DIR/mods/<profile>/<generation>/.
Objects are copied unchanged. Finished content (svg, presets) comes from open-xy's own
builder/core.mjs (buildAssets), run under Node from OPEN_XY (an open-xy tree at the kit's SDK
commit, or with --sdk-commit that commit of an open-xy checkout, taken with git archive; never
modified). rebuild turns the base plus a selection's packages back into a kit that core.mjs
accepts; prove links every selection twice (original kit, rebuilt kit) with core.mjs and requires
byte-identical module, globals, numbers, patched os.ldr (website mode) and content. prove-patch
compares the native end-to-end assembly (link, patch, content; xy_forever/modpatch.py)
with core.mjs; --memory expanded does both with the website's experimental 4 MiB option.
Host only: no device, no network. Outputs go under the private root, outside every checkout.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import random
import re
import struct
import subprocess
import sys
import tempfile
from urllib.parse import quote, unquote

from .xyf_entry import ManifestError, files_sha256_text, parse_manifest, read_files_sha256

KIT_FORMAT = 'opxy-browser-kit/1'
PROFILE_SCHEMA = 1
DRIVER = Path(__file__).resolve().parents[1] / 'scripts/modkit_core.mjs'
KIT_KEYS = {'format', 'version', 'profile', 'profileSource', 'definitions', 'fixed', 'machines', 'objects',
            'stockCatalog', 'screenTemplate'}
KIT_OPTIONAL = {'linked', 'notices', 'extensions', 'serviceFallbacks'}
OBJECT_KEYS = {'name', 'owner', 'sha256', 'bytes', 'service', 'runtime', 'extension'}
EDIT_KINDS = ('heap', 'table', 'text', 'hook', 'startup')
EDIT_KEYS = {'kind', 'name', 'address', 'file_offset', 'length', 'before', 'after', 'symbol', 'call', 'service',
             'profiling', 'events'}
MACHINE_ID = re.compile(r'[a-z][a-z0-9_]{0,14}')
EXTENSION_ID = re.compile(r'[a-z][a-z0-9_]{0,31}')
RELOCATIONS = (6, 7, 10, 13, 18)   # LUIMM16, HUIMM16, PCREL24, PCREL24_JUMP_L, BYTE4_DATA (008)
SHN_ABS, SHN_COMMON = 0xfff1, 0xfff2
STT_FILE = 4
# Files a mod package may hold (anything else, e.g. edits or definitions, belongs only to the base).
MOD_FILES = re.compile(r'info\.txt|files\.sha256|objects\.txt|objects/[A-Za-z0-9_./-]+\.o|catalog\.txt|'
                       r'machine\.json|extension\.json|NOTICE\.txt|content/(display|presets|aux_presets)/[^\n]+')
PLACEHOLDER_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"/>\n'
# profile.identities in identities.txt order: scalars, then lists (native/mod_link reads them in this order).
RULE_SCALARS = ('engines', 'engine_none', 'pages', 'page_none', 'effect_page')
RULE_LISTS = ('stock_engines', 'sample_pages', 'effects', 'players')


class KitError(ValueError):
    """A kit or package this tool refuses; the message says why."""


def check(ok, message):
    if not ok:
        raise KitError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- ELF32 relocatable subset (core.mjs readObject)

def read_object(data, name='object'):
    """Sections (name, type, flags, size, group), symbols and relocations of a Blackfin ET_REL object."""
    check(52 <= len(data) <= 1 << 20, name + ': object size outside bounds')
    u16 = lambda at: struct.unpack_from('<H', data, at)[0]   # noqa: E731
    u32 = lambda at: struct.unpack_from('<I', data, at)[0]   # noqa: E731
    check(data[:6] == b'\x7fELF\x01\x01' and u16(16) == 1 and u16(18) == 106,
          name + ': not a Blackfin relocatable object')
    start, count, names_index = u32(32), u16(48), u16(50)
    check(u16(46) == 40 and 0 < count < 4096 and names_index < count and start + count * 40 <= len(data),
          name + ': invalid section table')
    sections = []
    for i in range(count):
        n, type_, flags, _, offset, size, link, info, align, stride = struct.unpack_from('<10I', data, start + i * 40)
        check(type_ == 8 or offset + size <= len(data), name + ': section exceeds file')
        sections.append(dict(index=i, name_offset=n, type=type_, flags=flags, offset=offset, size=size, link=link,
                             info=info, align=align or 1, stride=stride))

    def string(table, at):
        blob = data[table['offset']:table['offset'] + table['size']]
        end = blob.find(b'\0', at)
        check(at < len(blob) and end >= 0, name + ': invalid string offset')
        return blob[at:end].decode('utf-8')

    for s in sections:
        s['name'] = string(sections[names_index], s['name_offset'])
        s['group'] = None
        if s['flags'] & 2:
            check(not s['flags'] & ~7, name + ': unsupported allocated section flags')
            n = s['name']
            s['group'] = ('rodata' if re.match(r'(\.rodata(\.|$)|\.data\.rel\.ro(\.|$))', n) else
                          'data' if re.match(r'(\.data(\.|$)|\.sdata(\.|$))', n) else
                          'bss' if re.match(r'(\.bss(\.|$)|\.sbss(\.|$))', n) else
                          'text' if re.match(r'\.text(\.|$)', n) else None)
            check(s['group'] and s['type'] == (8 if s['group'] == 'bss' else 1),
                  name + ': unsupported allocated section ' + n)
    tables = [s for s in sections if s['type'] == 2]
    check(len(tables) == 1, name + ': expected one symbol table')
    table = tables[0]
    check(table['stride'] == 16 and table['size'] % 16 == 0 and table['link'] < count, name + ': invalid symbol table')
    symbols = []
    for at in range(table['offset'], table['offset'] + table['size'], 16):
        n, value, size, info, _, shndx = struct.unpack_from('<IIIBBH', data, at)
        check(shndx < count or shndx == SHN_ABS, name + ': COMMON or extended symbol sections are unsupported')
        symbols.append(dict(name=string(sections[table['link']], n), value=value, size=size, binding=info >> 4,
                            type=info & 15, section=shndx))
    relocations = []
    for s in sections:
        check(s['type'] != 9, name + ': REL without addends is unsupported')
        if s['type'] != 4:
            continue
        check(s['link'] == table['index'] and s['info'] < count and s['stride'] == 12 and s['size'] % 12 == 0,
              name + ': invalid relocation table')
        for at in range(s['offset'], s['offset'] + s['size'], 12):
            offset, info, addend = struct.unpack_from('<IIi', data, at)
            check(info & 255 in RELOCATIONS and info >> 8 < len(symbols), name + ': unsupported relocation')
            relocations.append(dict(section=s['info'], offset=offset, type=info & 255, symbol=info >> 8, addend=addend))
    return dict(sections=sections, symbols=symbols, relocations=relocations)


def object_summary(data, name):
    """Exports, imports, absolute symbols and code/data/state bytes of one object."""
    o = read_object(data, name)
    exports, imports, absolute = set(), set(), []
    for s in o['symbols']:
        if s['type'] == STT_FILE or not s['name']:
            continue
        if s['section'] == SHN_ABS:
            absolute.append(s['name'])
        if not s['binding']:
            continue
        check(s['binding'] == 1, '%s: weak symbol %s is unsupported' % (name, s['name']))
        (exports if s['section'] else imports).add(s['name'])
    sizes = dict(code=0, data=0, state=0)
    for s in o['sections']:
        if s['group']:
            sizes['code' if s['group'] == 'text' else 'state' if s['group'] == 'bss' else 'data'] += s['size']
    return dict(exports=exports, imports=imports, absolute=absolute, **sizes)


def make_object(sections=(), symbols=(), relocations=()):
    """A minimal Blackfin ET_REL object: sections [(name, bytes or bss size)], symbols
    [(name, section 1-based or 0 or SHN_ABS, value, binding, type)], relocations
    [(section, offset, type, symbol, addend)] with symbol 1-based. Used for test fixtures and
    absent-extension placeholders."""
    shstr, strtab = bytearray(b'\0'), bytearray(b'\0')

    def add(table, text):
        at = len(table)
        table.extend(text.encode() + b'\0')
        return at

    body, headers = bytearray(), [bytes(40)]
    user = []
    for name, content in sections:
        bss = isinstance(content, int)
        writable = bss or name.startswith(('.data', '.bss', '.sdata', '.sbss'))
        flags = 3 if writable else 6 if name.startswith('.text') else 2
        user.append((add(shstr, name), 8 if bss else 1, flags, b'' if bss else bytes(content),
                     content if bss else len(content)))
    symtab = bytearray(16)
    for name, section, value, binding, type_ in symbols:
        symtab += struct.pack('<IIIBBH', add(strtab, name), value, 0, binding << 4 | type_, 0, section)
    first = 52
    layout = []
    for name_at, type_, flags, content, size in user:
        layout.append((name_at, type_, flags, first + len(body), size, 0, 0, 4, 0))
        body += content
        body += bytes(-len(body) % 4)
    symtab_index = len(user) + 1 + len({r[0] for r in relocations})
    for target in sorted({r[0] for r in relocations}):
        rela = b''.join(struct.pack('<IIi', off, sym << 8 | t, add_) for sec, off, t, sym, add_ in relocations
                        if sec == target)
        layout.append((add(shstr, '.rela' + sections[target - 1][0]), 4, 0, first + len(body), len(rela),
                       symtab_index, target, 4, 12))
        body += rela
    layout.append((add(shstr, '.symtab'), 2, 0, first + len(body), len(symtab), symtab_index + 1, 1, 4, 16))
    body += symtab
    strtab_at = first + len(body)
    shstr_name = add(shstr, '.shstrtab')
    strtab_name = add(shstr, '.strtab')
    layout.append((strtab_name, 3, 0, strtab_at, len(strtab), 0, 0, 1, 0))
    body += strtab
    layout.append((shstr_name, 3, 0, first + len(body), len(shstr), 0, 0, 1, 0))
    body += shstr
    body += bytes(-len(body) % 4)
    for n, t, f, off, size, link, info, align, stride in layout:
        headers.append(struct.pack('<10I', n, t, f, 0, off, size, link, info, align, stride))
    count = len(headers)
    header = bytearray(52)
    header[:16] = b'\x7fELF\x01\x01\x01' + bytes(9)
    struct.pack_into('<HHIIIIIHHHHHH', header, 16, 1, 106, 1, 0, 0, first + len(body), 0, 52, 0, 0, 40, count,
                     count - 1)
    return bytes(header + body + b''.join(headers))


# ---------------------------------------------------------------- edits.bin (the profile's edits in binary form)

EDITS_MAGIC = b'XYFE'


def encode_edits(profile):
    """The profile's edits as the device reads them (docs/storage/mods.md, "Mod packages")."""
    services = list(profile.get('services', {}))
    strings, offsets = bytearray(), {}

    def text(value):
        if value not in offsets:
            offsets[value] = len(strings)
            strings.extend(value.encode() + b'\0')
        return offsets[value]

    service_at = [text(s) for s in services]
    records = bytearray()
    for edit in profile['patches']:
        kind = EDIT_KINDS.index(edit['kind']) + 1
        before = bytes.fromhex(edit['before'])
        after = bytes.fromhex(edit['after']) if 'after' in edit else b''
        check(len(before) == edit['length'] and (not after or len(after) == edit['length']), 'edit length mismatch')
        flags = (1 if edit.get('call') else 0) | (2 if edit.get('profiling') is False else 0) | (4 if after else 0)
        service = services.index(edit['service']) if 'service' in edit else 0xff
        symbol = text(edit['symbol']) if 'symbol' in edit else 0xffff
        records += struct.pack('<BBBBIIHH', kind, flags, service, 0, int(edit['address'], 16),
                               int(edit['file_offset'], 16), edit['length'], symbol) + before + after
    check(len(strings) < 0xffff and len(services) < 0xff, 'edit tables too large')
    head = EDITS_MAGIC + struct.pack('<HHHH', 1, len(profile['patches']), len(services), len(strings))
    return bytes(head + b''.join(struct.pack('<H', a) for a in service_at) + strings + records)


def decode_edits(data):
    """edits.bin -> [edit] with the fields patchFirmware uses (the inverse of encode_edits)."""
    check(data[:4] == EDITS_MAGIC, 'edits.bin: bad magic')
    version, count, nservices, nstrings = struct.unpack_from('<HHHH', data, 4)
    check(version == 1, 'edits.bin: unknown version %d' % version)
    at = 12 + 2 * nservices
    strings = data[at:at + nstrings]
    name = lambda off: strings[off:strings.index(b'\0', off)].decode()   # noqa: E731
    services = [name(struct.unpack_from('<H', data, 12 + 2 * i)[0]) for i in range(nservices)]
    at += nstrings
    edits = []
    for _ in range(count):
        kind, flags, service, _, address, offset, length, symbol = struct.unpack_from('<BBBBIIHH', data, at)
        at += 16
        edit = dict(kind=EDIT_KINDS[kind - 1], address=address, file_offset=offset, length=length,
                    before=data[at:at + length].hex())
        at += length
        if flags & 4:
            edit['after'] = data[at:at + length].hex()
            at += length
        if service != 0xff:
            edit['service'] = services[service]
        if symbol != 0xffff:
            edit['symbol'] = name(symbol)
        if edit['kind'] == 'hook':
            edit['call'] = bool(flags & 1)
        if flags & 2:
            edit['profiling'] = False
        edits.append(edit)
    check(at == len(data), 'edits.bin: trailing bytes')
    return services, edits


def edit_fields(edit):
    """The fields of a profile edit that decide the patched bytes (what edits.bin must keep)."""
    out = dict(kind=edit['kind'], address=int(edit['address'], 16), file_offset=int(edit['file_offset'], 16),
               length=edit['length'], before=edit['before'])
    for key in ('after', 'service', 'symbol', 'call', 'profiling'):
        if key in edit:
            out[key] = edit[key]
    return out


# ---------------------------------------------------------------- kit reading and checks

def load_kit(path):
    data = Path(path).read_bytes()
    try:
        kit = json.loads(data)
    except ValueError as e:
        raise KitError('kit is not JSON: %s' % e)
    check(isinstance(kit, dict), 'kit is not a JSON object')
    return kit


def generation_of(kit):
    """The kit export's identity: version + digest of everything a link depends on."""
    canon = dict(version=kit['version'], profile=sha256(base64.b64decode(kit['profileSource'])),
                 objects=[[o['name'], o['sha256'], o.get('owner'), o.get('service'), o.get('runtime'),
                           o.get('extension')] for o in kit['objects']],
                 machines=[[m['id'], m['kind'], m['prefix'], m['identity'], m['absent'], m.get('features')]
                           for m in kit['machines']],
                 extensions=[e['id'] for e in kit.get('extensions', [])], definitions=kit['definitions'],
                 fixed=kit['fixed'], fallbacks=kit.get('serviceFallbacks', {}))
    return '%s-%s' % (kit['version'], sha256(json.dumps(canon, sort_keys=True).encode())[:12])


def processor_key(e):
    return 'master_processor' if 'master_processor' in e else 'master_filter' if 'master_filter' in e else None


def own_number_symbols(m):
    """The link-time numbers core.mjs numberSymbols defines for one machine (the only non-SDK imports allowed)."""
    p = m['prefix']
    if m['kind'] == 'player':
        return {p + '_player_id', p + '_page_id'}
    if m['kind'] == 'effect':
        return {p + '_fx_id', p + '_fx_page_id'}
    if m['kind'] == 'sample' and m['identity'].get('page') is None:
        return {p + '_engine_id', p + '_page_id', p + '_screen_page_id'}
    return {p + '_engine_id', p + '_page_id'}


MIB = 1 << 20


def heap_load(text):
    """(opcode, value) of a Blackfin+ Dreg/Preg = imm32 load as core.mjs heapLoad reads it, or None."""
    if not isinstance(text, str) or not re.fullmatch(r'[0-9a-f]{16}', text):
        return None
    opcode, high, low, zero = struct.unpack('<4H', bytes.fromhex(text))
    return (opcode, high * 65536 + low) if (opcode & 0xffc0) == 0xda00 and zero == 0 else None


def expanded_base(profile):
    """The module base of the profile's reviewed larger layout (core.mjs offersExpandedMemory, the 4 MiB option),
    or None when the profile offers none. The heap edits then load expanded_heap_after(...)."""
    x, heaps = profile.get('experimental_memory'), [p for p in profile.get('patches') or [] if p.get('kind') == 'heap']
    whole = lambda *ns: all(isinstance(n, int) and not isinstance(n, bool) and n % MIB == 0 for n in ns)  # noqa
    if not (isinstance(x, dict) and sorted(x) == ['heap_after', 'module_base', 'reserved_bytes']):
        return None
    base, fill = x['module_base'], profile['heap_fill_base']
    if not (isinstance(base, int) and isinstance(x['reserved_bytes'], int)):
        return None
    end = profile['module_base'] + profile['reserved_bytes']
    after = heap_load(x['heap_after'])
    ok = (whole(x['reserved_bytes'], profile['reserved_bytes'], base - fill, profile['module_base'] - fill) and
          x['reserved_bytes'] > profile['reserved_bytes'] and base + x['reserved_bytes'] == end and base > fill and
          after is not None and after[1] == base - fill and fill + profile['heap_fill_bytes'] == end and
          len(heaps) == 2 and
          all(p.get('kind') != 'hook' or p.get('length') not in (4, 6) or
              all(abs(t - int(str(p['address']), 0)) < 1 << 24 for t in (base, end)) for p in profile['patches']))
    for p in heaps if ok else ():
        before, replaced = heap_load(p.get('before')), heap_load(p.get('after'))
        ok = ok and before is not None and replaced is not None and before[1] == profile['heap_fill_bytes'] and \
            replaced[0] == before[0] == after[0] and replaced[1] == profile['module_base'] - fill
    return base if ok else None


def expanded_heap_after(after, value):
    """A heap edit's replacement (16 hex digits) with its loaded value changed: what the device writes."""
    opcode = struct.unpack('<H', bytes.fromhex(after)[:2])[0]
    return struct.pack('<4H', opcode, value >> 16, value & 0xffff, 0).hex()


def services_for(profile, users):
    return [name for name, wanted in profile.get('services', {}).items() if any(u in wanted for u in users)]


def analyse(kit):
    """Every check this tool makes before writing anything; returns the parsed kit model."""
    check(kit.get('format') == KIT_FORMAT,
          'unknown kit format %r (this tool reads %s)' % (kit.get('format'), KIT_FORMAT))
    unknown = set(kit) - KIT_KEYS - KIT_OPTIONAL
    check(not unknown, 'unknown kit fields %s: this kit is newer than this tool' % sorted(unknown))
    missing = KIT_KEYS - set(kit)
    check(not missing, 'kit lacks %s' % sorted(missing))
    check(isinstance(kit['version'], str) and re.fullmatch(r'\d+\.\d+\.\d+', kit['version']),
          'unknown kit version %r' % kit.get('version'))
    profile_bytes = base64.b64decode(kit['profileSource'])
    profile = kit['profile']
    check(json.loads(profile_bytes) == profile, 'kit profile differs from its profile source')
    check(profile.get('schema') == PROFILE_SCHEMA, 'unknown profile schema %r' % profile.get('schema'))
    check(re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,30}', str(profile.get('id'))), 'invalid profile id')
    for edit in profile['patches']:
        check(edit.get('kind') in EDIT_KINDS,
              'unknown edit kind %r: this kit is newer than this tool' % edit.get('kind'))
        extra = set(edit) - EDIT_KEYS
        check(not extra, 'unknown edit fields %s: this kit is newer than this tool' % sorted(extra))
    machines, extensions = kit['machines'], kit.get('extensions', [])
    ids = [m['id'] for m in machines]
    check(len(set(ids)) == len(ids), 'duplicate machine ids %s' % sorted({i for i in ids if ids.count(i) > 1}))
    check(all(isinstance(i, str) and MACHINE_ID.fullmatch(i) and i != 'base' for i in ids),
          'invalid machine id (or "base") in %s' % ids)
    eids = [e['id'] for e in extensions]
    check(len(set(eids)) == len(eids), 'duplicate extension ids %s' % sorted({i for i in eids if eids.count(i) > 1}))
    check(all(isinstance(i, str) and EXTENSION_ID.fullmatch(i) for i in eids), 'invalid extension id in %s' % eids)
    check(not set(ids) & set(eids), 'ids used by both a machine and an extension: %s' % sorted(set(ids) & set(eids)))
    for entry in machines + extensions:
        for key in ('patches', 'edits', 'definitions', 'address'):
            check(key not in entry, '%s carries %s: edits and addresses belong only to the base' % (entry['id'], key))
    names, objects = set(), []
    for index, o in enumerate(kit['objects']):
        extra = set(o) - OBJECT_KEYS
        check(not extra, 'unknown object fields %s: this kit is newer than this tool' % sorted(extra))
        check(o['name'] not in names, 'duplicate object %s' % o['name'])
        names.add(o['name'])
        check(re.fullmatch(r'[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*\.o', o['name']) and '..' not in o['name'],
              'unsafe object name %r' % o['name'])
        check(o.get('owner') is None or o['owner'] in ids, 'object %s has an unknown owner' % o['name'])
        check(o.get('extension') is None or (o['extension'] in eids and o.get('owner') is None),
              'object %s names an unknown extension' % o['name'])
        check(o.get('service') is None or o['service'] in profile.get('services', {}),
              'object %s names an unknown service' % o['name'])
        data = base64.b64decode(o['bytes'])
        check(sha256(data) == o['sha256'], 'object hash mismatch: ' + o['name'])
        objects.append(dict(order=index, name=o['name'], owner=o.get('owner'), service=o.get('service'),
                            runtime=bool(o.get('runtime')), extension=o.get('extension'), blob=data,
                            **object_summary(data, o['name'])))
    base = [o for o in objects if o['owner'] is None and o['extension'] is None]
    sdk_exports = set().union(*(o['exports'] for o in base))
    for m in machines:
        own = [o for o in objects if o['owner'] == m['id']]
        check(own, 'machine %s has no objects' % m['id'])
        defined = set().union(*(o['exports'] for o in own))
        allowed = sdk_exports | own_number_symbols(m) | defined
        for o in own:
            check(not o['absolute'], '%s defines absolute symbols %s: addresses belong only to the base'
                  % (o['name'], o['absolute']))
            bad = sorted(o['imports'] - allowed)
            check(not bad, 'machine %s imports %s, which are not SDK or runtime exports' % (m['id'], bad))
    for e in extensions:
        own = [o for o in objects if o['extension'] == e['id']]
        check(len(own) == 1, 'extension %s needs exactly one object' % e['id'])
        o = own[0]
        check(not o['absolute'], '%s defines absolute symbols %s: addresses belong only to the base'
              % (o['name'], o['absolute']))
        bad = sorted(o['imports'] - sdk_exports)
        check(not bad, 'extension %s imports %s, which are not SDK or runtime exports' % (e['id'], bad))
        check('sdk_extension_%s_hooks' % e['id'] in o['exports'], 'extension %s lacks its hooks accessor' % e['id'])
    return dict(kit=kit, profile=profile, profile_bytes=profile_bytes, objects=objects,
                generation=generation_of(kit))


# ---------------------------------------------------------------- writing packages

def info_text(known, extra):
    """info.txt: known keys (xyf_entry.MANIFEST_KEYS order) then extra keys; the device reader must accept it."""
    from .xyf_entry import MANIFEST_KEYS
    lines = ['%s=%s' % (k, known[k]) for k in MANIFEST_KEYS if known.get(k) is not None]
    for key, value in extra:
        value = str(value)
        check(re.fullmatch(r'[a-z0-9_]{1,31}', key) and '\n' not in value and len(value.encode()) <= 127 and value,
              'info.txt value for %s does not fit (127 bytes)' % key)
        lines.append('%s=%s' % (key, value))
    text = ('\n'.join(lines) + '\n').encode()
    parse_manifest(text)
    return text


def licence_of(text):
    for marker, name in (('GNU AFFERO GENERAL PUBLIC LICENSE', 'AGPL-3.0'), ('GNU GENERAL PUBLIC LICENSE', 'GPL-3.0'),
                         ('MIT License', 'MIT'), ('Apache License', 'Apache-2.0'), ('BSD', 'BSD')):
        if marker in text:
            return name
    return 'see NOTICE.txt'


def notice_sections(notices, names):
    """{machine name: notice text} for the machine vendor licences the kit's notices carry."""
    out, parts = {}, notices.split('\n\n')
    starts = [i for i, p in enumerate(parts) if p in names]
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(parts)
        out[parts[i]] = '\n\n'.join(parts[i + 1:end]) + '\n'
    return out


def run_driver(sdk, jobs, scratch):
    """Run scripts/modkit_core.mjs (open-xy's core.mjs from sdk) on jobs; returns its results."""
    core = Path(sdk) / 'builder/core.mjs'
    check(core.is_file(), 'no builder/core.mjs in %s' % sdk)
    jobs_path, out_path = Path(scratch) / 'jobs.json', Path(scratch) / 'results.json'
    jobs_path.write_text(json.dumps(jobs))
    run = subprocess.run(['node', str(DRIVER), '--core', str(core), '--jobs', str(jobs_path), '--out', str(out_path)],
                         capture_output=True, text=True)
    check(run.returncode == 0, 'core.mjs driver failed: ' + (run.stderr or run.stdout)[-2000:])
    return json.loads(out_path.read_text())


def write_package(folder, files):
    """files: {relative path: bytes}; info.txt last, then files.sha256 over everything else."""
    folder.mkdir(parents=True)
    for rel, data in sorted(files.items()):
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (folder / 'files.sha256').write_text(files_sha256_text(folder))


def table(header, rows):
    return ('# ' + header + '\n' + ''.join(' '.join(str(c) for c in r) + '\n' for r in rows)).encode()


def dash(value):
    return '-' if value is None or value == [] or value == '' else ','.join(value) if isinstance(value, list) else value


def identities_table(identities):
    """base identities.txt: profile.identities, one rule per line (a list comma-separated, - = empty)."""
    check(set(identities) <= set(RULE_SCALARS + RULE_LISTS), 'unknown numbering rules %s'
          % sorted(set(identities) - set(RULE_SCALARS + RULE_LISTS)))
    rows = [(k, int(identities[k])) for k in RULE_SCALARS]
    rows += [(k, dash([str(int(n)) for n in identities.get(k) or []])) for k in RULE_LISTS]
    return table('rule value (profile.identities: the numbering rules; lists comma-separated, - = none)', rows)


def claim_labels_field(e):
    """extensions.txt labels=: each claim's label percent-encoded (UTF-8; only A-Za-z0-9_.-~ kept), in claim order,
    comma-separated; an empty entry = no label (core.mjs claimLabel then looks further); - = no claims."""
    labels = e.get('claim_labels') or {}
    return dash([quote(labels.get(c) or '', safe='') for c in e.get('claims') or []])


def core_assets(kit_path, kit, sdk, tmp):
    """Each machine's finished content from core.mjs buildAssets (that machine alone): {id: {path: bytes}}."""
    jobs = [dict(name=m['id'], kit=str(Path(kit_path).resolve()), machines=[m['id']], extensions=[],
                 assets_only=True, write=str(Path(tmp) / 'assets' / m['id'])) for m in kit['machines']]
    results = {r['name']: r for r in run_driver(sdk, jobs, tmp)}
    assets = {}
    for m in kit['machines']:
        r = results[m['id']]
        check('error' not in r, 'core.mjs could not finish %s content: %s' % (m['id'], r.get('error')))
        folder = Path(tmp) / 'assets' / m['id']
        assets[m['id']] = {p: (folder / p).read_bytes() for p in r['files'] if p != 'content/op1_factory.json'}
    return assets


def sdk_tree(sdk, commit, tmp):
    """sdk itself, or (with commit) builder/, sdk/browser/ and tooling/default_preset.json of that commit of the
    open-xy checkout sdk, extracted with git archive into tmp (the checkout is not changed)."""
    if not commit:
        return Path(sdk)
    import io
    import tarfile
    run = subprocess.run(['git', '-C', str(sdk), 'archive', '--format=tar', commit, 'builder', 'sdk/browser',
                          'tooling/default_preset.json'], capture_output=True)
    check(run.returncode == 0, 'git archive %s failed: %s' % (commit, run.stderr.decode()[-500:]))
    out = Path(tmp) / 'open-xy'
    with tarfile.open(fileobj=io.BytesIO(run.stdout)) as tar:
        for member in tar.getmembers():
            check(not member.name.startswith('/') and '..' not in member.name.split('/'), 'unsafe archive member')
        tar.extractall(out)
    return out


def split_kit(kit_path, out, sdk=None, sdk_source=None, extension_catalog=None, finish=None, sdk_commit=None):
    """Write the base and every mod package under out/mods/<profile>/<generation>/; returns the size report.
    finish(kit) -> {machine id: {path: bytes}} gives the finished content (default: core.mjs from sdk)."""
    kit = load_kit(kit_path)
    model = analyse(kit)
    profile, objects, generation = model['profile'], model['objects'], model['generation']
    root = Path(out) / 'mods' / profile['id'] / generation
    check(not root.exists(), 'output already exists: %s' % root)
    pins = {}
    if extension_catalog:
        for entry in json.loads(Path(extension_catalog).read_text()).get('extensions', []):
            pins[entry['id']] = entry
    common = dict(generation=generation, profile=profile['id'])
    report = dict(generation=generation, profile=profile['id'], root=str(root), packages={})
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root.parent, prefix='.split-') as tmp:
        assets = finish(kit) if finish else core_assets(kit_path, kit, sdk_tree(sdk, sdk_commit, tmp), tmp)
        stage = Path(tmp) / generation
        write_base(stage / 'base', model, common, sdk_source)
        sizes = report['packages']
        sizes['base'] = package_sizes(stage / 'base', base_objects(objects))
        notices = notice_sections(kit.get('notices', ''), {m['name'] for m in kit['machines']})
        for order, m in enumerate(kit['machines']):
            write_machine(stage / m['id'], model, m, order, common, assets[m['id']], notices.get(m['name']))
            sizes[m['id']] = package_sizes(stage / m['id'], [o for o in objects if o['owner'] == m['id']])
        for order, e in enumerate(kit.get('extensions', [])):
            folder = stage / ('ext-' + e['id'])
            write_extension(folder, model, e, order, common, pins.get(e['id']))
            sizes['ext-' + e['id']] = package_sizes(folder, [o for o in objects if o['extension'] == e['id']])
        stage.rename(root)
    report['total_bytes'] = sum(p['bytes'] for p in sizes.values())
    return report


def base_objects(objects):
    return [o for o in objects if o['owner'] is None and o['extension'] is None]


def package_sizes(folder, objects):
    files = [p for p in folder.rglob('*') if p.is_file()]
    return dict(bytes=sum(p.stat().st_size for p in files), files=len(files),
                code=sum(o['code'] for o in objects), data=sum(o['data'] for o in objects),
                state=sum(o['state'] for o in objects))


def role(o):
    return 'runtime' if o['runtime'] else 'service=' + o['service'] if o['service'] else 'core'


def write_base(folder, model, common, sdk_source):
    kit, profile = model['kit'], model['profile']
    objects = base_objects(model['objects'])
    files = {'profile.json': model['profile_bytes'], 'edits.bin': encode_edits(profile),
             'screen.svg': kit['screenTemplate'].encode()}
    if kit.get('notices'):
        files['notices.txt'] = kit['notices'].encode()
    for o in objects:
        files['objects/' + o['name']] = o['blob']
    files['objects.txt'] = table('order role path (link order: ascending order across all packages)',
                                 [(o['order'], role(o), 'objects/' + o['name']) for o in objects])
    files['definitions.txt'] = table('symbol address', [(k, '0x%08x' % v) for k, v in kit['definitions'].items()])
    files['fixed.txt'] = table('machine numbers', [(k, ' '.join('%s=%d' % kv for kv in v.items()))
                                                   for k, v in kit['fixed'].items()])
    files['fallbacks.txt'] = table('service symbol fallback (aliased while the service is unused)',
                                   [(s, sym, fb) for s, pairs in kit.get('serviceFallbacks', {}).items()
                                    for sym, fb in pairs.items()])
    service_rows = []
    for name, users in profile.get('services', {}).items():
        own = [o for o in objects if o['service'] == name]
        service_rows.append((name, 'users=' + ','.join(users),
                             *('%s=%d' % (k, sum(o[k] for o in own)) for k in ('code', 'data', 'state'))))
    files['services.txt'] = table('service users (machine kinds and features, extension, master_filter) and the '
                                  'bytes its objects add when a selection uses it', service_rows)
    if kit.get('linked'):
        files['linked.txt'] = table('machines the kit export links natively', [(i,) for i in kit['linked']])
    rows, absent = [], []
    for order, m in enumerate(kit['machines']):
        slot = 'player' if m['kind'] == 'player' else 'effect' if m['kind'] == 'effect' else 'engine'
        src = m.get('source') or {}
        rows.append((order, m['id'], 'kind=' + m['kind'], 'prefix=' + m['prefix'],
                     '%s=%s' % (slot, dash(m['identity'].get(slot))), 'page=%s' % dash(m['identity'].get('page')),
                     'features=' + dash(m.get('features')), 'origin=' + m['origin'],
                     *('%s=%s' % (k, dash(src.get(k))) for k in ('repository', 'tag', 'commit', 'maintainer'))))
        absent += [(m['id'], sym, fb) for sym, fb in m['absent'].items()]
    files['machines.txt'] = table('order id kind prefix requested numbers (- = given by the layout) features origin '
                                  'source', rows)
    files['absent.txt'] = table('machine symbol fallback (aliased while the machine is not selected)', absent)
    ext_rows = []
    for order, e in enumerate(kit.get('extensions', [])):
        ext_rows.append((order, e['id']) + extension_row(model, e))
    files['extensions.txt'] = table('order id link services claims labels accessors processor state_id asset origin',
                                    ext_rows)
    files['identities.txt'] = identities_table(profile['identities'])
    insert = profile['stock_catalog_inserts']
    sizes = dict(code=0, data=0, state=0)
    for o in objects:
        if role(o) == 'core':
            for k in sizes:
                sizes[k] += o[k]
    licence = 'MIT AND GPL-3.0-or-later WITH GCC-exception-3.1' if 'GCC arithmetic runtime' in kit.get('notices', '') \
        else 'MIT'
    known = dict(type='mod-base', name='open-xy SDK base', version=kit['version'], needs_os=profile['stock_sha256'],
                 source=sdk_source, licence=licence, **common)
    extra = [('format', KIT_FORMAT), ('profile_sha256', sha256(model['profile_bytes'])),
             ('module_base', '0x%08x' % profile['module_base']),
             ('module_bytes', profile['reserved_bytes'] - profile['table_bytes']),
             ('stock_bytes', profile['stock_bytes']), ('edits', len(profile['patches'])), ('objects', len(objects)),
             ('machines', len(kit['machines'])), ('extensions', len(kit.get('extensions', []))),
             ('code', sizes['code']), ('data', sizes['data']), ('state', sizes['state']),
             ('catalog_sha256', profile['stock_catalog_sha256']),
             ('catalog_engine_at', insert['engine_types']), ('catalog_fx_at', insert['fx_types']),
             ('catalog_seq_at', insert['seq_types']), ('stock_records', profile['stock_records']),
             ('reserved_bytes', profile['reserved_bytes']), ('heap_fill_base', '0x%08x' % profile['heap_fill_base']),
             ('heap_fill_bytes', profile['heap_fill_bytes'])]
    runtime = [o for o in objects if o['runtime']]
    extra += [('runtime_%s' % k, sum(o[k] for o in runtime)) for k in ('code', 'data', 'state')]
    x = expanded_base(profile)
    if x is not None:                    # the 4 MiB option: older readers skip the unknown key
        extra.append(('expanded_module_base', '0x%08x' % x))
    files['info.txt'] = info_text(known, extra)
    write_package(folder, files)


def extension_row(model, e):
    o = next(x for x in model['objects'] if x['extension'] == e['id'])
    key = processor_key(e)
    accessors = ['hooks']
    if key:
        accessors += [s for s in ('processor', 'panel', 'filter')
                      if 'sdk_extension_%s_%s' % (e['id'], s) in o['exports']]
    users = ['extension'] + (['master_filter'] if key else [])
    proc = e[key] if key else {}
    return ('link=%d' % o['order'], 'services=' + dash(services_for(model['profile'], users)),
            'claims=' + dash(e.get('claims')), 'labels=' + claim_labels_field(e),
            'accessors=' + ','.join(accessors), 'processor=' + (key or '-'),
            'state_id=' + (proc.get('state_id') or '-'), 'asset=' + (proc.get('asset') or '-'), 'origin=' + e['origin'])


def artwork_from(finished, template):
    """The machine fragment core.mjs buildAssets put before the template's </svg>, or None."""
    at = template.find('</svg>')
    head, tail = template[:at], template[at + 6:]
    if at < 0 or not finished.startswith(head) or not finished.endswith(tail + '') or len(finished) < len(template):
        return None
    inserted = finished[at:len(finished) - len(tail)]
    return inserted[:-6] if inserted.endswith('</svg>') else None


def catalog_number(m):
    """Start and end of the number core.mjs catalogEntry rewrites ("id": N), for effects and players."""
    if m['kind'] not in ('effect', 'player') or not m['catalog']:
        return None
    found = re.search(r'"id":\s*\d+', m['catalog'])
    check(found, '%s: catalog entry has no "id" to number' % m['id'])
    start = len(m['catalog'][:found.start()].encode())
    return '%d,%d' % (start, start + len(found.group().encode()))


def write_machine(folder, model, m, order, common, assets, notice):
    own = [o for o in model['objects'] if o['owner'] == m['id']]
    files = dict(assets)
    for o in own:
        files['objects/' + o['name']] = o['blob']
    files['objects.txt'] = table('order role path', [(o['order'], 'machine', 'objects/' + o['name']) for o in own])
    if m['catalog']:
        files['catalog.txt'] = m['catalog'].encode()
    host = {k: v for k, v in m.items() if k not in ('id', 'kind', 'prefix', 'identity', 'absent', 'catalog', 'origin',
                                                       'source', 'features')}
    if 'preview' in host:
        host['preview'] = {k: v for k, v in host['preview'].items() if k != 'wasm'}
    finished = assets.get('content/display/%s' % m.get('asset'))
    if 'artwork' in host and '$' not in host['artwork'] and finished is not None and \
            artwork_from(finished.decode(), model['kit']['screenTemplate']) == host['artwork']:
        del host['artwork']                     # the finished svg holds it (rebuild_kit takes it back out)
    files['machine.json'] = (json.dumps(host, indent=1, ensure_ascii=False) + '\n').encode()
    if notice:
        files['NOTICE.txt'] = notice.encode()
    src = m.get('source')
    known = dict(type='mod', name=m['name'], machine=m['kind'], version=src and src['tag'],
                 source=src and '%s@%s' % (src['repository'], src['commit']),
                 licence=licence_of(notice) if notice else 'unknown', **common)
    check(len(m['name'].encode()) < 48 and (not src or len(src['tag']) < 32), '%s: name or tag too long' % m['id'])
    slot = 'player' if m['kind'] == 'player' else 'effect' if m['kind'] == 'effect' else 'engine'
    users = [m['kind']] + list(m.get('features') or [])
    extra = [('id', m['id']), ('order', order), ('prefix', m['prefix']), ('origin', m['origin']),
             ('objects', len(own)), ('code', sum(o['code'] for o in own)), ('data', sum(o['data'] for o in own)),
             ('state', sum(o['state'] for o in own)),
             ('services', dash(services_for(model['profile'], users))), ('features', dash(m.get('features'))),
             (slot, dash(m['identity'].get(slot))), ('page', dash(m['identity'].get('page'))),
             ('catalog_number', catalog_number(m) or '-'), ('asset', m.get('asset') or '-'),
             ('presets', sum(1 for p in assets if '/presets/' in p or '/aux_presets/' in p))]
    if src:
        extra.append(('maintainer', src['maintainer']))
    if isinstance(m.get('cpu'), dict) and m['cpu'].get('peak_percent') is not None:
        extra.append(('cpu_peak_percent', m['cpu']['peak_percent']))
    files['info.txt'] = info_text(known, extra)
    write_package(folder, files)


def write_extension(folder, model, e, order, common, pin):
    o = next(x for x in model['objects'] if x['extension'] == e['id'])
    files = {'objects/' + o['name']: o['blob'],
             'objects.txt': table('order role path', [(o['order'], 'extension', 'objects/' + o['name'])])}
    key = processor_key(e)
    host = {k: v for k, v in e.items() if k != 'id'}
    if key:
        proc = e[key]
        art = base64.b64decode(proc['artwork'])
        check(sha256(art) == proc['sha256'] and re.fullmatch(r'[a-z][a-z0-9_]*\.svg', proc['asset']),
              '%s: processor artwork differs from its hash' % e['id'])
        files['content/display/' + proc['asset']] = art
        host[key] = {k: v for k, v in proc.items() if k != 'artwork'}
    files['extension.json'] = (json.dumps(host, indent=1, ensure_ascii=False) + '\n').encode()
    row = extension_row(model, e)
    known = dict(type='mod', name=e['name'], machine='extension', version=pin and pin['tag'],
                 source=pin and '%s@%s' % (pin['repository'], pin['commit']), licence='unknown', **common)
    extra = [('id', e['id']), ('order', order), ('origin', e['origin']), ('objects', 1), ('code', o['code']),
             ('data', o['data']), ('state', o['state'])] + [tuple(f.split('=', 1)) for f in row[:-1]
                                                            if not f.startswith('labels=')]
    if pin:
        extra.append(('maintainer', pin['maintainer']))
    files['info.txt'] = info_text(known, extra)
    write_package(folder, files)


# ---------------------------------------------------------------- reading packages back

def read_table(data):
    return [line.split(' ') for line in data.decode().splitlines() if line and not line.startswith('#')]


def fields_of(row):
    return dict(f.split('=', 1) for f in row if '=' in f)


def undash(value, number=False):
    if value == '-':
        return None
    return int(value) if number else value


def read_package(folder, kind):
    """A package's files after its files.sha256 and info.txt checks: {path: bytes}, manifest fields, extra keys."""
    folder = Path(folder)
    check(folder.is_dir(), 'missing package %s' % folder)
    listed = read_files_sha256((folder / 'files.sha256').read_bytes())
    present = sorted(p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file())
    check(present == sorted([p for _, p in listed] + ['files.sha256']),
          '%s: files differ from files.sha256' % folder.name)
    files = {}
    for digest, rel in listed:
        data = (folder / rel).read_bytes()
        check(sha256(data) == digest, '%s: %s differs from files.sha256' % (folder.name, rel))
        files[rel] = data
    try:
        manifest = parse_manifest(files['info.txt'])
    except ManifestError as e:
        raise KitError('%s/info.txt: %s' % (folder.name, e))
    check(manifest.get('type') == kind, '%s: expected type=%s' % (folder.name, kind))
    extra = dict(line.split('=', 1) for line in files['info.txt'].decode().splitlines()
                 if '=' in line and not line.lstrip().startswith('#'))
    if kind == 'mod':
        stray = sorted(p for p in files if not MOD_FILES.fullmatch(p))
        check(not stray, '%s carries %s: edits, definitions and addresses belong only to the base'
              % (folder.name, stray))
    return files, manifest, extra


def object_entry(order, path, data, **tags):
    entry = dict(name=path[len('objects/'):], owner=None, sha256=sha256(data), bytes=base64.b64encode(data).decode())
    entry.update({k: v for k, v in tags.items() if v is not None})
    return order, entry


def placeholder_object(eid, accessors):
    """Stands in for an extension whose package is absent: exports its accessors, is never linked."""
    syms = [('sdk_extension_%s_%s' % (eid, a), 1, 0, 1, 2) for a in accessors]
    return make_object([('.text', b'')], syms)


def rebuild_kit(generation_dir, machines, extensions, catalog):
    """The base plus the selected packages as a kit core.mjs accepts (docs/storage/mods.md, "Mod packages")."""
    root = Path(generation_dir)
    check(len(set(machines)) == len(machines) and len(set(extensions)) == len(extensions),
          'duplicate ids in the selection')
    check(machines, 'choose at least one machine (an extension needs at least one machine)')
    base, bman, binfo = read_package(root / 'base', 'mod-base')
    generation = bman['generation']
    profile_bytes = base['profile.json']
    profile = json.loads(profile_bytes)
    check(binfo.get('format') == KIT_FORMAT, 'unknown base format %r' % binfo.get('format'))
    check(sha256(profile_bytes) == binfo['profile_sha256'] and profile['id'] == bman['profile'],
          'base profile differs from its manifest')
    services, edits = decode_edits(base['edits.bin'])
    check(services == list(profile.get('services', {})) and edits == [edit_fields(e) for e in profile['patches']],
          'base edits.bin differs from its profile')
    check(sha256(catalog) == binfo['catalog_sha256'], 'stock op1_factory.json differs from the one the base names')
    check(base.get('identities.txt') == identities_table(profile['identities']) and
          all(binfo.get(k) == str(profile[k]) for k in ('stock_records', 'reserved_bytes', 'heap_fill_bytes')) and
          binfo.get('heap_fill_base') == '0x%08x' % profile['heap_fill_base'],
          'base identities.txt or patch facts differ from its profile')
    x = expanded_base(profile)
    check(binfo.get('expanded_module_base') in (None, '0x%08x' % x if x is not None else None),
          'base expanded_module_base differs from its profile')
    rows = {r[1]: (int(r[0]), r[2:]) for r in read_table(base['machines.txt'])}
    absent = {}
    for mid, sym, fb in read_table(base['absent.txt']):
        absent.setdefault(mid, {})[sym] = fb
    ext_rows = {r[1]: (int(r[0]), fields_of(r[2:])) for r in read_table(base['extensions.txt'])}
    unknown = sorted(set(machines) - set(rows)) + sorted(set(extensions) - set(ext_rows))
    check(not unknown, 'not in this generation: %s' % unknown)
    objects = []
    for order, kind, path in read_table(base['objects.txt']):
        service = kind[len('service='):] if kind.startswith('service=') else None
        objects.append(object_entry(int(order), path, base[path], service=service,
                                    runtime=True if kind == 'runtime' else None))
    kit_machines = []
    for mid, (order, row) in sorted(rows.items(), key=lambda kv: kv[1][0]):
        f = fields_of(row)
        slot = next(k for k in ('engine', 'effect', 'player') if k in f)
        m = dict(id=mid, kind=f['kind'], prefix=f['prefix'],
                 identity={slot: undash(f[slot], True), 'page': undash(f['page'], True)},
                 absent=absent.get(mid, {}), origin=f['origin'])
        if f['repository'] != '-':
            m['source'] = {k: f[k] for k in ('repository', 'tag', 'commit', 'maintainer')}
        if f['features'] != '-':
            m['features'] = f['features'].split(',')
        if mid in machines:
            files, man, info = read_package(root / mid, 'mod')
            check(man['generation'] == generation, '%s is from generation %s, the base from %s: a selection cannot mix '
                  'generations' % (mid, man['generation'], generation))
            check(man['machine'] == m['kind'] and info.get('id') == mid and info.get('prefix') == m['prefix'],
                  '%s: package differs from the base collection' % mid)
            check(not set(json.loads(files['machine.json'])) & {'absent', 'identity', 'kind', 'prefix', 'id',
                                                                 'catalog'},
                  '%s: machine.json repeats collection fields' % mid)
            m.update(json.loads(files['machine.json']))
            if 'asset' in m and 'artwork' not in m:
                m['artwork'] = artwork_from(files['content/display/' + m['asset']].decode(),
                                            base['screen.svg'].decode())
                check(m['artwork'] is not None, '%s: display svg does not hold its artwork' % mid)
            m['catalog'] = files.get('catalog.txt', b'').decode()
            for order_, kind, path in read_table(files['objects.txt']):
                check(kind == 'machine', '%s: unexpected object role %s' % (mid, kind))
                objects.append(object_entry(int(order_), path, files[path], owner=mid))
        kit_machines.append(m)
    kit_extensions = []
    for eid, (order, f) in sorted(ext_rows.items(), key=lambda kv: kv[1][0]):
        claims = [] if f['claims'] == '-' else f['claims'].split(',')
        accessors = f['accessors'].split(',')
        path = 'sdk/extension_%s.o' % eid
        if eid in extensions:
            files, man, info = read_package(root / ('ext-' + eid), 'mod')
            check(man['generation'] == generation, 'ext-%s is from generation %s, the base from %s: a selection cannot '
                  'mix generations' % (eid, man['generation'], generation))
            check(man['machine'] == 'extension' and info.get('id') == eid,
                  'ext-%s: package differs from the base' % eid)
            e = dict(id=eid, **json.loads(files['extension.json']))
            if f['processor'] != '-':
                proc = e[f['processor']]
                proc['artwork'] = base64.b64encode(files['content/display/' + proc['asset']]).decode()
            for order_, kind, opath in read_table(files['objects.txt']):
                check(kind == 'extension', 'ext-%s: unexpected object role %s' % (eid, kind))
                objects.append(object_entry(int(order_), opath, files[opath], extension=eid))
        else:
            labels = [unquote(x) for x in f['labels'].split(',')] if claims else []
            check(len(labels) == len(claims), 'ext-%s: labels differ from claims in extensions.txt' % eid)
            e = dict(id=eid, origin=f['origin'], claims=claims, actions=[], name=eid, description=eid,
                     claim_labels={c: x or c.replace('_', ' ') for c, x in zip(claims, labels)})
            if f['processor'] != '-':
                proc = dict(asset=f['asset'], sha256=sha256(PLACEHOLDER_SVG),
                            artwork=base64.b64encode(PLACEHOLDER_SVG).decode())
                if f['state_id'] != '-':
                    proc['state_id'] = f['state_id']
                e[f['processor']] = proc
            objects.append(object_entry(int(f['link']), 'objects/' + path, placeholder_object(eid, accessors),
                                        extension=eid))
        kit_extensions.append(e)
    orders = [o for o, _ in objects]
    check(len(set(orders)) == len(orders), 'two packages claim the same link-order key')
    kit = dict(format=binfo['format'], version=bman['version'], profile=profile,
               profileSource=base64.b64encode(profile_bytes).decode(),
               definitions={r[0]: int(r[1], 16) for r in read_table(base['definitions.txt'])},
               fixed={r[0]: {k: int(v) for k, v in fields_of(r[1:]).items()} for r in read_table(base['fixed.txt'])},
               machines=kit_machines, objects=[e for _, e in sorted(objects, key=lambda x: x[0])],
               notices=base.get('notices.txt', b'').decode(), extensions=kit_extensions,
               stockCatalog=base64.b64encode(catalog).decode(), screenTemplate=base['screen.svg'].decode(),
               serviceFallbacks={})
    for service, sym, fb in read_table(base['fallbacks.txt']):
        kit['serviceFallbacks'].setdefault(service, {})[sym] = fb
    if 'linked.txt' in base:
        kit['linked'] = [r[0] for r in read_table(base['linked.txt'])]
    return kit


# ---------------------------------------------------------------- the round-trip proof

def selections_for(kit, generation_dir, seed=5):
    """Named selections: one machine, the linked set, one of each kind, random catalog sets, the largest set that
    fits, the whole catalog, each extension with one machine, both extensions."""
    ids = [m['id'] for m in kit['machines']]
    first = 'fm2op' if 'fm2op' in ids else ids[0]
    out = [('one', [first], [])]
    if kit.get('linked'):
        out.append(('linked', list(kit['linked']), []))
    kinds = {}
    for m in kit['machines']:
        kinds.setdefault((m['kind'], tuple(m.get('features') or ())), m['id'])
    out.append(('kinds', [i for i in ids if i in kinds.values()], []))
    rng = random.Random(seed)
    for n in range(5):
        pick = set(rng.sample(ids, rng.randint(2, 8)))
        out.append(('random%d' % n, [i for i in ids if i in pick], []))
    fit = fitting(generation_dir, ids)
    out.append(('fits%d' % len(fit), fit, []))
    out.append(('all', ids, []))
    exts = [e['id'] for e in kit.get('extensions', [])]
    for e in exts:
        out.append(('one+' + e, [first], [e]))
    if len(exts) > 1:
        out.append(('one+all-extensions', [first], exts))
        if kit.get('linked'):
            out.append(('linked+all-extensions', list(kit['linked']), exts))
    return out


def fitting(generation_dir, ids, margin=96 * 1024):
    """Machines in collection order while their sizes still fit beside the base and every service (a sure fit)."""
    root = Path(generation_dir)
    info = lambda p: dict(l.split('=', 1) for l in (p / 'info.txt').read_text().splitlines() if '=' in l)  # noqa
    sizes = lambda f, prefix='': sum(int(f[prefix + k]) for k in ('code', 'data', 'state'))  # noqa: E731
    base = info(root / 'base')
    used = sizes(base) + sizes(base, 'runtime_') + sum(
        sizes(fields_of(row[1:])) for row in read_table((root / 'base/services.txt').read_bytes()))
    out = []
    for i in ids:
        m = info(root / i)
        size = sizes(m) + 8 * int(m['objects'])          # + alignment
        if used + size <= int(base['module_bytes']) - margin:
            out.append(i)
            used += size
    return out


def prove(kit_path, generation_dir, sdk, catalog_path, stock=None, scratch=None, selections=None, sdk_commit=None):
    """Link each selection from the original kit and from the rebuilt kit with core.mjs; returns per-selection rows."""
    kit = load_kit(kit_path)
    catalog = Path(catalog_path).read_bytes()
    selections = selections or selections_for(kit, generation_dir)
    rows = []
    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        jobs = []
        for name, machines, exts in selections:
            rebuilt = Path(tmp) / ('kit-%s.json' % re.sub(r'[^a-z0-9+_-]', '_', name))
            rebuilt.write_text(json.dumps(rebuild_kit(generation_dir, machines, exts, catalog)))
            for side, path in (('original', Path(kit_path).resolve()), ('rebuilt', rebuilt)):
                jobs.append(dict(name=name + '/' + side, kit=str(path), machines=machines, extensions=exts,
                                 stock=str(stock) if stock else None))
        results = {r['name']: r for r in run_driver(sdk_tree(sdk, sdk_commit, tmp), jobs, tmp)}
    for name, machines, exts in selections:
        a, b = results[name + '/original'], results[name + '/rebuilt']
        a.pop('name'), b.pop('name')
        same = a == b
        content = content_matches(generation_dir, machines, exts, a) if same and 'error' not in a else None
        rows.append(dict(selection=name, machines=len(machines), extensions=exts, identical=same,
                         error=a.get('error'), allocated=(a.get('sizes') or {}).get('allocated'),
                         ldr=bool(a.get('ldr_sha256')), content_matches_packages=content))
    return rows


def content_matches(generation_dir, machines, exts, result):
    """The finished files in the packages equal core.mjs's buildAssets output for this selection."""
    root = Path(generation_dir)
    expected = {}
    for mid in machines:
        for p in (root / mid / 'content').rglob('*'):
            if p.is_file():
                expected[p.relative_to(root / mid).as_posix()] = sha256(p.read_bytes())
    for eid in exts:
        for p in (root / ('ext-' + eid) / 'content').rglob('*'):
            if p.is_file():
                expected[p.relative_to(root / ('ext-' + eid)).as_posix()] = sha256(p.read_bytes())
    produced = {p: h for p, h in result['files'].items() if p != 'content/op1_factory.json'}
    return produced == expected


# ---------------------------------------------------------------- command line

def main(argv=None):
    parser = argparse.ArgumentParser(prog='python3 -m xy_forever.modkit', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    s = sub.add_parser('split')
    s.add_argument('kit', type=Path)
    s.add_argument('--sdk', type=Path, required=True, help='open-xy tree at the kit SDK commit (builder/core.mjs)')
    s.add_argument('--sdk-commit', help='take core.mjs from this commit of the --sdk checkout (git archive)')
    s.add_argument('--out', type=Path, required=True)
    s.add_argument('--sdk-source', help='base source=, e.g. https://github.com/<owner>/open-xy@<commit>')
    s.add_argument('--extension-catalog', type=Path, help="open-xy's catalog/extensions.json at the kit commit")
    r = sub.add_parser('rebuild')
    r.add_argument('generation', type=Path)
    r.add_argument('--machine', action='append', default=[])
    r.add_argument('--extension', action='append', default=[])
    r.add_argument('--catalog', type=Path, required=True, help='stock content/op1_factory.json')
    r.add_argument('--out', type=Path, required=True)
    p = sub.add_parser('prove')
    p.add_argument('kit', type=Path)
    p.add_argument('generation', type=Path)
    p.add_argument('--sdk', type=Path, required=True)
    p.add_argument('--sdk-commit')
    p.add_argument('--catalog', type=Path, required=True)
    p.add_argument('--stock', type=Path, help='stock os.ldr 1.2.1 (patchFirmware is compared when given)')
    p.add_argument('--scratch', type=Path)
    q = sub.add_parser('prove-patch')
    q.add_argument('generation', type=Path)
    q.add_argument('--sdk', type=Path, required=True)
    q.add_argument('--sdk-commit')
    q.add_argument('--catalog', type=Path, required=True)
    q.add_argument('--stock', type=Path, required=True, help='stock os.ldr 1.2.1')
    q.add_argument('--scratch', type=Path)
    q.add_argument('--keep', type=Path, help='keep link results and both outputs here (private root)')
    q.add_argument('--official', type=Path, help='official content list (content.sha256; default: the private '
                                                 'content-manifests/<version>/content.sha256 of the base profile)')
    q.add_argument('--memory', choices=('standard', 'expanded'), default='standard',
                   help="expanded: the website's experimental 4 MiB option (core.mjs memoryKit)")
    k = sub.add_parser('known-base', help='native/mod_known_bases.inc from official base folders')
    k.add_argument('base', type=Path, nargs='+')
    k.add_argument('--stock', type=Path, help='check the stock os.ldr the base pins')
    k.add_argument('--catalog', type=Path, help='check the official catalog the base pins')
    k.add_argument('--write', action='store_true', help='write native/mod_known_bases.inc (else print it)')
    args = parser.parse_args(argv)
    from .paths import private_output
    try:
        if args.command == 'split':
            out = private_output(args.out)
            report = split_kit(args.kit, out, args.sdk, args.sdk_source, args.extension_catalog,
                               sdk_commit=args.sdk_commit)
            print(json.dumps(report, indent=1))
        elif args.command == 'rebuild':
            out = private_output(args.out)
            kit = rebuild_kit(args.generation, args.machine, args.extension, args.catalog.read_bytes())
            out.write_text(json.dumps(kit))
            print(out)
        elif args.command == 'known-base':
            from . import modpatch
            text = modpatch.known_bases_text(args.base, args.stock, args.catalog)
            if args.write:
                modpatch.KNOWN_BASES.write_text(text)
            print(text, end='')
        elif args.command == 'prove-patch':
            from . import modpatch
            rows = modpatch.prove(args.generation, args.sdk, args.catalog, args.stock,
                                  private_output(args.scratch) if args.scratch else None, sdk_commit=args.sdk_commit,
                                  keep=private_output(args.keep) if args.keep else None, official=args.official,
                                  memory=args.memory)
            for row in rows:
                print(json.dumps(row))
            if not all(modpatch.proven(r) for r in rows):
                print('NOT IDENTICAL', file=sys.stderr)
                return 1
            print('all %d selections: os.ldr, content, list and numbers byte-identical to core.mjs (%d refused alike)'
                  % (len(rows), sum(1 for r in rows if r.get('error'))))
        else:
            rows = prove(args.kit, args.generation, args.sdk, args.catalog, args.stock,
                         private_output(args.scratch) if args.scratch else None, sdk_commit=args.sdk_commit)
            for row in rows:
                print(json.dumps(row))
            if not all(r['identical'] and r['content_matches_packages'] is not False for r in rows):
                print('NOT IDENTICAL', file=sys.stderr)
                return 1
            print('all %d selections byte-identical' % len(rows))
    except (KitError, ManifestError, OSError) as e:
        print('modkit: %s' % e, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
