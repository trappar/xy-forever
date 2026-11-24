"""The native mod linker on the host (native/mod_link.hpp; docs/storage/mods.md).

    uv run -m xy_forever.modlink prove GENERATION_DIR --sdk OPEN_XY [--sdk-commit C]
                                  [--catalog op1_factory.json] [--subsets] [--scratch DIR]
    uv run -m xy_forever.modlink link GENERATION_DIR --machine ID ... [--extension ID ...] --out module.bin
    uv run -m xy_forever.modlink cross

prove links every selection modkit prove uses (plus, with --subsets, every subset of the kit's
linked machines) twice: with native/mod_link compiled for the host, fed file by file from the
packages, and with open-xy's own builder/core.mjs linkKit on the kit modkit rebuild_kit makes of
the same packages. Module bytes, globals, aliases, numbers, objects, services, extensions and
sizes must be equal, and a refusal must be core.mjs's refusal (or modkit's, for its own checks).
cross compiles the linker for the Blackfin with the firmware's flags (compile only) and reports
library calls, BSS, the largest stack frame and the text + rodata bytes. Host only.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import modkit, paths

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('mod_link', 'mod_elf', 'mod_numbers')
DRIVER = ROOT / 'scripts/modlink_core.mjs'
SDK_COMMIT = '52c8de911a415e043fd4d1afc396c3869fcd1029'  # open-xy 0.0.6, the kit's SDK
CATALOG = paths.CATALOG_121


def statuses():
    """Status names in native/mod_link.hpp's order (the bridge prints the index)."""
    text = (ROOT / 'native/mod_link.hpp').read_text()
    body = re.search(r'enum class Status \{(.*?)\};', text, re.S).group(1)
    body = re.sub(r'//[^\n]*', '', body)
    return [w.strip() for w in body.split(',') if w.strip()]


# core.mjs's (or modkit's) words for each refusal; {} is the native detail. Statuses whose detail
# is not part of the text (it names the object or relocation) are listed in FIXED.
MESSAGES = dict(
    duplicate_selection='duplicate ids in the selection',
    no_machine='Choose each machine once.',
    unknown_id='not in this generation: ',
    generation='{} is from generation ',
    package='{}: package differs from the base',
    order='two packages claim the same link-order key',
    numbers_request='Machine numbers overlap a stock slot or exceed capacity: {}',
    fixed_differs='Fixed machine numbers differ: {}',
    accessor='Missing extension accessor: {}',
    processor_accessor='Missing master processor accessors: {}',
    object_size='Object size outside bounds',
    object_type='Expected a Blackfin relocatable object',
    section_table='Invalid object section table',
    section_size='Invalid object section size/alignment',
    section_file='Object section exceeds file',
    string_offset='Invalid object string offset',
    string_end='Unterminated object string',
    utf8='The encoded data was not valid for encoding utf-8',
    section_flags='Unsupported allocated section flags',
    section_kind='Unsupported allocated section: {}',
    symbol_tables='Expected one symbol table',
    symbol_table='Invalid symbol table',
    symbol_section='COMMON and extended symbol sections are unsupported',
    rel='REL without explicit addends is unsupported',
    relocation_table='Invalid relocation table',
    relocation_target='Relocation target is not initialized memory',
    relocation_type='Unsupported relocation',
    relocation_range='Relocation exceeds section',
    relocation_overlap='Overlapping relocations',
    claim='These extensions both use {}. Choose only one.',
    both_hold='{} in this layout',
    layout_fixed='{} has fixed numbers; the layout gives it others',
    forks='{} now forks another stock sampler; the layout holds its numbers for the old one',
    layout_invalid='{}: the layout gives it numbers it cannot use',
    no_free='No free {}: every one is held in this layout',
    no_valid='{}: no valid numbers',
    weak='Weak object symbols are unsupported',
    runtime_replaces='Runtime member replaces an existing definition: {}',
    capacity='These machines need more than the {} KiB available: ',
    empty='Empty module',
    symbol_outside='Symbol outside allocated section: {}',
    duplicate_global='Duplicate global symbol: {}',
    absent_extension='Invalid absent-extension accessor',
    absent_machine='Invalid absent-machine callback',
    service_fallback='Invalid service fallback',
    unresolved='Unresolved import: {}',
    overflow='Symbol arithmetic overflow',
    branch='Invalid or out-of-range relative branch',
)
PREFIX = {'unknown_id', 'generation', 'package', 'capacity'}  # the reference text goes on
COLLECTION = ('Unsupported curated kit', 'Invalid extension collection')


def same_refusal(status, detail, reference):
    """Whether the native refusal (status, <= 32-byte detail) is the reference's error text."""
    if status == 'collection':
        return reference in COLLECTION
    template = MESSAGES.get(status)
    if template is None:
        return False
    if '{}' not in template:
        return reference.startswith(template) if status in PREFIX else reference == template
    head, tail = template.split('{}')
    if status == 'unknown_id':
        return reference.startswith(head) and ("'%s'" % detail) in reference
    if len(detail.encode()) >= 32:  # truncated detail
        return reference.startswith(head + detail)
    text = head + detail + tail
    return reference.startswith(text) if status in PREFIX else reference == text


# ---------------------------------------------------------------- the host bridge


def build(sanitize=False):
    """tests/mod_link_bridge.cpp with the linker sources (hosttest/CMakeLists.txt; `uv sync` builds it). XYF_SANITIZE=1
    in the environment makes every build the ASan/UBSan one (the whole mod test set once under the sanitizers)."""
    import xyf_hosttest

    sanitize = sanitize or bool(os.environ.get('XYF_SANITIZE'))
    return xyf_hosttest.program('mod_link_bridge' + ('_sanitized' if sanitize else ''))


def rules_text(profile):
    """profile.identities for the bridge's --numbers mode (the linker reads the base's identities.txt)."""
    r = profile['identities']
    lines = ['%s %d' % (k, r[k]) for k in ('engines', 'engine_none', 'pages', 'page_none', 'effect_page')]
    lines += [
        '%s %s' % (k, ','.join(str(n) for n in r[k]))
        for k in ('stock_engines', 'sample_pages', 'effects', 'players')
        if r.get(k)
    ]
    return '\n'.join(lines) + '\n'


def base_tables(generation_dir):
    """Machines, extensions and linked ids from the base, in collection order."""
    base = Path(generation_dir) / 'base'
    rows = modkit.read_table((base / 'machines.txt').read_bytes())
    machines = [dict(id=r[1], **dict(modkit.fields_of(r[2:]).items())) for r in rows]
    for m in machines:
        m['features'] = [] if m.get('features', '-') == '-' else m['features'].split(',')
    extensions = [r[1] for r in modkit.read_table((base / 'extensions.txt').read_bytes())]
    linked = (
        [r[0] for r in modkit.read_table((base / 'linked.txt').read_bytes())] if (base / 'linked.txt').exists() else []
    )
    services = [r[0] for r in modkit.read_table((base / 'services.txt').read_bytes())]
    return machines, extensions, linked, services


def link_native(bridge, generation_dir, machines, extensions, scratch, layout=None, name='link', keep=None, args=()):
    """One native link: the bridge's result as a dict (module digest, tables), or status + detail.
    args: more bridge options (tests: "-2 PACKAGE/PATH=FILE")."""
    out = Path(keep) if keep else Path(scratch) / (name + '.module')
    command = [str(bridge), str(generation_dir), str(out)]
    command += [a for m in machines for a in ('-m', m)] + [a for e in extensions for a in ('-e', e)]
    if layout is not None:
        lp = Path(scratch) / (name + '.layout')
        slots = ('engine', 'effect', 'player')
        lp.write_text(
            ''.join(
                '%s %d %d %d\n'
                % (
                    mid,
                    next(i for i, s in enumerate(slots) if s in ident),
                    next(ident[s] for s in slots if s in ident),
                    ident['page'],
                )
                for mid, ident in layout.items()
            )
        )
        command += ['-l', str(lp)]
    command += list(args)
    run = subprocess.run(command, capture_output=True, text=True)
    if run.returncode not in (0, 1) or not run.stdout.startswith('status '):
        raise RuntimeError('bridge crashed (%d): %s %s' % (run.returncode, run.stdout[-500:], run.stderr[-3000:]))
    names = statuses()
    result = dict(globals={}, aliases={}, numbers={}, objects=[], services=[], usage={})
    _, ext_ids, _, _ = (
        base_tables(generation_dir)
        if (Path(generation_dir) / 'base/extensions.txt').exists()
        else (None, [], None, None)
    )
    slots = ('engine', 'effect', 'player')
    for line in run.stdout.splitlines():
        word, _, rest = line.partition(' ')
        if word == 'status':
            index, _, detail = rest.partition(' ')
            result['status'], result['detail'] = names[int(index)], detail
        elif word in ('step', 'zero_steps', 'scratch', 'largest', 'object_bytes'):
            result[word] = int(rest)
        elif word == 'work':
            units, _, at = rest.partition(' ')
            result['work'] = dict(units=int(units), step=int(at))
        elif word == 'slowest':
            us, _, where = rest.partition(' ')
            result['slowest'] = dict(us=float(us), file=where)
        elif word == 'usage':
            result['usage'] = {k: int(v) for k, v in (f.split('=') for f in rest.split())}
        elif word == 'sizes':
            result['sizes'] = dict(zip(('initialized', 'bss', 'allocated', 'available'), map(int, rest.split())))
        elif word == 'service':
            result['services'].append(rest)
        elif word == 'extensions':
            result['extensions'] = [e for i, e in enumerate(ext_ids) if int(rest) >> i & 1]
        elif word == 'object':
            result['objects'].append(rest)
        elif word == 'number':
            mid, slot, n, page = rest.split()
            result['numbers'][mid] = {slots[int(slot)]: int(n), 'page': int(page)}
        elif word == 'name':
            kind, value, sym = rest.split(' ', 2)
            (result['aliases'] if kind == '3' else result['globals'])[sym] = int(value)
    if result['status'] == 'ok':
        data = out.read_bytes()
        result['module_sha256'], result['module_bytes'] = hashlib.sha256(data).hexdigest(), len(data)
        if not keep:
            out.unlink()
    result['services'].sort()
    return result


KINDS = ('synth', 'drum', 'sample', 'effect', 'player')


def numbers_native(bridge, rules_path, fixed, layout, machines, scratch):
    """numberLayout alone: fixed and machines are [id, kind, identity], layout {id: identity}.
    Returns the numbers {id: identity} or (status, detail)."""
    slots = ('engine', 'effect', 'player')

    def row(role, mid, kind, ident):
        slot = next(i for i, s in enumerate(slots) if s in ident)
        value = ident[slots[slot]]
        return '%s %s %d %d %s %s\n' % (
            role,
            mid,
            KINDS.index(kind) if kind in KINDS else 5,
            slot,
            '-' if value is None else value,
            '-' if ident['page'] is None else ident['page'],
        )

    case = Path(scratch) / 'numbers-case.txt'
    case.write_text(
        ''.join(row('fixed', *f) for f in fixed)
        + ''.join(row('layout', k, None, v) for k, v in layout.items())
        + ''.join(row('machine', *m) for m in machines)
    )
    run = subprocess.run([str(bridge), '--numbers', str(rules_path), str(case)], capture_output=True, text=True)
    if run.returncode not in (0, 1):
        raise RuntimeError('bridge crashed: ' + run.stderr[-2000:])
    lines = run.stdout.splitlines()
    index, _, detail = lines[0][len('status ') :].partition(' ')
    if index != '0':
        return statuses()[int(index)], detail
    out = {}
    for line in lines[1:]:
        _, mid, slot, n, page = line.split()
        out[mid] = {slots[int(slot)]: int(n), 'page': int(page)}
    return out


def check_objects(bridge, files):
    """readObject's checks on each file: [(status, section name)]."""
    run = subprocess.run([str(bridge), '--objects', *map(str, files)], capture_output=True, text=True)
    if run.returncode:
        raise RuntimeError('bridge crashed (%d): %s' % (run.returncode, run.stderr[-2000:]))
    names = statuses()
    out = []
    for line in run.stdout.splitlines():
        index, _, name = line[len('status ') :].partition(' ')
        out.append((names[int(index)], name))
    return out


def run_reference(sdk, jobs, scratch):
    jobs_path, out_path = Path(scratch) / 'modlink-jobs.json', Path(scratch) / 'modlink-results.json'
    jobs_path.write_text(json.dumps(jobs))
    run = subprocess.run(
        [
            'node',
            str(DRIVER),
            '--core',
            str(Path(sdk) / 'builder/core.mjs'),
            '--jobs',
            str(jobs_path),
            '--out',
            str(out_path),
        ],
        capture_output=True,
        text=True,
    )
    if run.returncode:
        raise RuntimeError('core.mjs driver failed: ' + (run.stderr or run.stdout)[-2000:])
    return {r['name']: r for r in json.loads(out_path.read_text())}


FIELDS = (
    'module_sha256',
    'module_bytes',
    'globals',
    'aliases',
    'numbers',
    'objects',
    'services',
    'extensions',
    'sizes',
)


def compare(native, reference):
    """[] when equal; else the fields (or the refusal) that differ."""
    if 'error' in reference:
        if native['status'] == 'ok':
            return ['native linked, reference refused: ' + reference['error']]
        if not same_refusal(native['status'], native['detail'], reference['error']):
            return ['refusal %s %r vs %r' % (native['status'], native['detail'], reference['error'])]
        return []
    if native['status'] != 'ok':
        return ['native refused: %s %s' % (native['status'], native['detail'])]
    return [f for f in FIELDS if native.get(f) != reference.get(f)]


def prove_selections(generation_dir, selections, sdk, catalog, scratch, bridge=None, open_mode='full', layouts=None):
    """Link each (name, machines, extensions) natively and with core.mjs; returns one row each."""
    generation_dir = Path(generation_dir).resolve()
    with tempfile.TemporaryDirectory(dir=scratch, prefix='modlink-') as tmp:
        tmp = Path(tmp)
        bridge = bridge or build()
        jobs, refusals = [], {}
        for name, machines, exts in selections:
            layout = (layouts or {}).get(name)
            kit_path = tmp / ('kit-%s.json' % re.sub(r'[^A-Za-z0-9+_-]', '_', name))
            try:
                kit = modkit.rebuild_kit(generation_dir, machines or [_first(generation_dir)], exts, catalog)
            except modkit.KitError as e:
                refusals[name] = str(e)
                continue
            kit_path.write_text(json.dumps(kit))
            jobs.append(
                dict(name=name, kit=str(kit_path), machines=machines, extensions=exts, open=open_mode, layout=layout)
            )
        reference = run_reference(sdk, jobs, tmp) if jobs else {}
        rows = []
        for name, machines, exts in selections:
            ref = reference.get(name) or dict(error=refusals[name])
            ref.pop('name', None)
            native = link_native(
                bridge,
                generation_dir,
                machines,
                exts,
                tmp,
                (layouts or {}).get(name),
                re.sub(r'[^A-Za-z0-9+_-]', '_', name),
            )
            diff = compare(native, ref)
            rows.append(
                dict(
                    selection=name,
                    machines=len(machines),
                    extensions=exts,
                    identical=not diff,
                    diff=diff,
                    status=native['status'],
                    detail=native['detail'],
                    error=ref.get('error'),
                    allocated=(native.get('sizes') or {}).get('allocated'),
                    steps=native.get('step'),
                    object_bytes=native.get('object_bytes'),
                    largest=native.get('largest'),
                    scratch=native.get('scratch'),
                    slowest=native.get('slowest'),
                    usage=native.get('usage'),
                )
            )
    return rows


def _first(generation_dir):
    return base_tables(generation_dir)[0][0]['id']


def selections(generation_dir, subsets=False):
    """modkit prove's selections (from the base tables), plus every subset of the linked machines."""
    machines, extensions, linked, _ = base_tables(generation_dir)
    kit = dict(
        machines=[dict(id=m['id'], kind=m['kind'], features=m['features']) for m in machines],
        linked=linked,
        extensions=[dict(id=e) for e in extensions],
    )
    out = modkit.selections_for(kit, generation_dir)
    if subsets and linked:
        for mask in range(1, 1 << len(linked)):
            pick = [m for i, m in enumerate(linked) if mask >> i & 1]
            if len(pick) not in (len(linked),):
                out.append(('linked-subset-%d' % mask, pick, []))
    return out


# ---------------------------------------------------------------- Blackfin compile check


def cross(folder, run=None):
    """Compile the linker sources for the Blackfin with the firmware's flags (in the build container: run is
    container.blackfin, or a test's wrapper of it); returns a report."""
    from . import container, toolchain

    commands = []
    for name in SOURCES:
        commands += [
            ['g++', *toolchain.CXX_FLAGS, '-fstack-usage', '-S', ROOT / 'native' / (name + '.cpp'), '-o', name + '.s'],
            ['as', '-mcpu=bf707', '-o', name + '.o', name + '.s'],
            ['objdump', '-h', name + '.o'],
        ]
    results = (run or container.blackfin)(commands, folder)
    report = dict(sources={}, text_rodata=0)
    for n, name in enumerate(SOURCES):
        if any(status for status, _, _ in results[3 * n : 3 * n + 3]):
            raise RuntimeError('%s does not compile for the Blackfin' % name)
        text = (Path(folder) / (name + '.s')).read_text()
        calls = sorted({c for c in re.findall(r'\bcall\s+(_[A-Za-z0-9_.]+)', text) if not c.startswith('__Z')})
        heads = results[3 * n + 2][1]
        sections = {
            m.group(1): int(m.group(2), 16) for m in re.finditer(r'^\s*\d+\s+(\S+)\s+([0-9a-f]+)\s', heads, re.M)
        }
        size = sum(v for k, v in sections.items() if k.startswith(('.text', '.rodata')))
        frames = [int(l.split('\t')[1]) for l in (Path(folder) / (name + '.su')).read_text().splitlines()]
        report['sources'][name] = dict(
            calls=calls,
            bss=sections.get('.bss', 0),
            data=sections.get('.data', 0),
            text_rodata=size,
            largest_frame=max(frames),
        )
        report['text_rodata'] += size
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='uv run -m xy_forever.modlink', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prove')
    p.add_argument('generation', type=Path)
    p.add_argument('--sdk', type=Path, required=True, help='open-xy tree or checkout (builder/core.mjs)')
    p.add_argument('--sdk-commit', help='take core.mjs from this commit of the --sdk checkout (git archive)')
    p.add_argument(
        '--catalog', type=Path, default=CATALOG, help='stock content/op1_factory.json (rebuild_kit checks it)'
    )
    p.add_argument('--subsets', action='store_true', help='also every subset of the linked machines')
    p.add_argument('--scratch', type=Path)
    l = sub.add_parser('link')
    l.add_argument('generation', type=Path)
    l.add_argument('--machine', action='append', default=[])
    l.add_argument('--extension', action='append', default=[])
    l.add_argument('--out', type=Path, required=True)
    sub.add_parser('cross')
    args = parser.parse_args(argv)
    try:
        if args.command == 'cross':
            with tempfile.TemporaryDirectory(prefix='modlink-cross-') as tmp:
                report = cross(tmp)
            print(json.dumps(report, indent=1))
            bad = [
                n
                for n, r in report['sources'].items()
                if r['calls'] or r['bss'] or r['data'] or r['largest_frame'] >= 1024
            ]
            return 1 if bad else 0
        scratch = paths.private_output(args.scratch) if getattr(args, 'scratch', None) else paths.SESSIONS
        scratch.mkdir(parents=True, exist_ok=True)
        if args.command == 'link':
            out = paths.private_output(args.out)
            with tempfile.TemporaryDirectory(dir=scratch, prefix='modlink-') as tmp:
                bridge = build()
                result = link_native(bridge, args.generation.resolve(), args.machine, args.extension, tmp, keep=out)
                print(json.dumps({k: result[k] for k in ('status', 'detail', 'sizes', 'step', 'usage') if k in result}))
            return 0
        with tempfile.TemporaryDirectory(dir=scratch, prefix='modlink-sdk-') as tmp:
            sdk = modkit.sdk_tree(args.sdk, args.sdk_commit, tmp)
            rows = prove_selections(
                args.generation, selections(args.generation, args.subsets), sdk, args.catalog.read_bytes(), scratch
            )
        for row in rows:
            print(json.dumps(row))
        if not all(r['identical'] for r in rows):
            print('NOT IDENTICAL', file=sys.stderr)
            return 1
        print(
            'all %d selections identical to core.mjs (%d refused alike)'
            % (len(rows), sum(1 for r in rows if r['error']))
        )
    except (modkit.KitError, OSError, RuntimeError, ValueError) as e:
        print('modlink: %s' % e, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
