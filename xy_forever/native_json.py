"""Versioned XY Forever JSON records; the host twin of native/json.hpp.

The text views are internal adapters for the existing semantic readers. They are
never written as competing native records. JSON identity is independent of the
package's display version and of other on-device formats (state/jobs/settings).
"""
import json
import re

PACKAGE_SCHEMA = 'xy-forever.package'
CONFIG_SCHEMA = 'xy-forever.config'
DOCUMENT_BYTES, PROPERTY_COUNT, FIELD_COUNT, LIST_COUNT, MAX_DEPTH = 8448, 128, 32, 96, 4
VALUE_BYTES, KEY_BYTES = 127, 31
NUMBERS = frozenset('bytes machines module_base module_bytes stock_bytes edits objects extensions code data '
                    'state runtime_code runtime_data runtime_state catalog_engine_at catalog_fx_at catalog_seq_at '
                    'stock_records reserved_bytes heap_fill_base heap_fill_bytes expanded_module_base order link presets'.split())
ADDRESSES = frozenset(('module_base', 'heap_fill_base', 'expanded_module_base'))
OPTIONAL_NUMBERS = frozenset(('engine', 'effect', 'player', 'page'))
LISTS = frozenset(('services', 'features', 'claims', 'accessors'))


class RecordError(ValueError):
    """Useful refusal for a present JSON record; callers must not fall back."""


def _need(condition, message):
    if not condition:
        raise RecordError(message)


def _uint(value):
    _need(type(value) is int and 0 <= value <= 0xffffffff, 'JSON field type or integer range')
    return value


def _text(value, maximum=VALUE_BYTES):
    _need(isinstance(value, str) and len(value) <= maximum and
          all(0x20 <= ord(c) <= 0x7e for c in value), 'JSON field text')
    return value


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        _need(key not in out, 'duplicate JSON field')
        out[key] = value
    return out


def parse(data, schema):
    """Parse bounded JSON without modifying raw bytes needed by source/hash checks."""
    if isinstance(data, str):
        data = data.encode()
    _need(0 < len(data) <= DOCUMENT_BYTES, 'JSON document size')
    escaped = False
    for c in data:
        _need(c != 0, 'JSON syntax')
        _need(not (escaped and c == ord('u')), 'Unicode escapes unsupported in native JSON')
        escaped = not escaped and c == ord('\\')
    try:
        root = json.loads(data, object_pairs_hook=_pairs,
                          parse_int=lambda number: -0.0 if number == '-0' else int(number),
                          parse_constant=lambda _: (_ for _ in ()).throw(RecordError('JSON number')))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise RecordError(str(error)) from error
    _need(isinstance(root, dict), 'JSON object required')
    count, pending = 0, [(root, 0)]
    while pending:
        value, depth = pending.pop()
        count += 1
        _need(count <= PROPERTY_COUNT, 'JSON property limit')
        if isinstance(value, str):
            _text(value)
        if isinstance(value, (dict, list)):
            _need(depth < MAX_DEPTH, 'JSON nesting limit')
            _need(len(value) <= (FIELD_COUNT + 2 if isinstance(value, dict) else LIST_COUNT), 'JSON capacity')
            if isinstance(value, dict):
                _need(all(len(key) <= KEY_BYTES for key in value), 'JSON field name limit')
            pending.extend((v, depth + 1) for v in (value.values() if isinstance(value, dict) else value))
    _need(root.get('schema') == schema, 'unsupported JSON schema')
    _need(type(root.get('schema_version')) is int and root['schema_version'] == 1,
          'unsupported JSON schema version')
    return root


def _scalar(key, value):
    if key in NUMBERS | OPTIONAL_NUMBERS:
        if value is None and key in OPTIONAL_NUMBERS:
            return '-'
        number = _uint(value)
        return '0x%08x' % number if key in ADDRESSES else str(number)
    if key in LISTS:
        _need(isinstance(value, list) and len(value) <= LIST_COUNT, 'JSON list type or limit')
        for item in value:
            _need(_text(item) and ',' not in item, 'JSON list item')
        return ','.join(value) or '-'
    if key == 'catalog_number':
        if value is None:
            return '-'
        _need(isinstance(value, list) and len(value) == 2, 'JSON catalog_number pair')
        return ','.join(str(_uint(n)) for n in value)
    if key == 'cpu_peak_percent':
        _need(type(value) in (int, float), 'JSON CPU percent')
        return str(value)
    return _text(value)


def package_text(data):
    """JSON package -> bounded key/value view, including typed native extras."""
    root = parse(data, PACKAGE_SCHEMA)
    fields = {k: v for k, v in root.items() if k not in ('schema', 'schema_version')}
    _need('type' in fields and len(fields) <= FIELD_COUNT, 'JSON package fields')
    lines = []
    for key, value in fields.items():
        _need(re.fullmatch(r'[a-z0-9_]{1,31}', key), 'JSON field name')
        scalar = _scalar(key, value)
        _need(len(scalar) <= VALUE_BYTES, 'JSON field value limit')
        lines.append('%s=%s\n' % (key, scalar))
    result = ''.join(lines).encode('ascii')
    _need(len(result) <= 4096, 'JSON capacity')
    return result


def package_document(fields):
    """Existing validated native field values -> their typed JSON representation."""
    out = dict(schema=PACKAGE_SCHEMA, schema_version=1)
    for key, value in fields.items():
        if value is None:
            continue
        if key in NUMBERS | OPTIONAL_NUMBERS:
            value = None if value == '-' and key in OPTIONAL_NUMBERS else int(str(value), 16 if key in ADDRESSES else 10)
        elif key in LISTS:
            value = [] if value in ('', '-') else str(value).split(',')
        elif key == 'catalog_number':
            value = None if value == '-' else [int(n) for n in str(value).split(',')]
        elif key == 'cpu_peak_percent':
            value = float(value)
        out[key] = value
    data = (json.dumps(out, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode()
    package_text(data)
    return data


def config_text(data):
    root = parse(data, CONFIG_SCHEMA)
    desired, built = root.get('desired'), root.get('built')
    _need(isinstance(desired, dict), 'JSON desired object')
    lines = ['format=1\n']
    for key in ('base', 'profile', 'generation', 'memory'):
        value = _text(desired.get(key), 31)
        if key != 'memory':
            _need(re.fullmatch(r'[a-z0-9_-]+' if key == 'base' else r'(?:[a-z0-9][a-z0-9._-]*)?', value),
                  'JSON configuration identifier')
        _need(key != 'memory' or value in ('standard', 'expanded'), 'JSON memory choice')
        lines.append('%s=%s\n' % (key, value))
    _need(type(desired.get('unverified')) is bool, 'JSON unverified boolean')
    lines.append('unverified=%d\n' % desired['unverified'])
    safe = desired.get('safe', False)
    _need(type(safe) is bool, 'JSON safe boolean')
    if safe:
        lines.append('safe=1\n')
    _need('built' in root and (built is None or isinstance(built, dict)), 'JSON built object or null')
    if built is not None:
        for key in ('base', 'generation', 'memory', 'slot', 'sha256'):
            _text(built.get(key), 64 if key == 'sha256' else 31)
        _need(built['memory'] in ('standard', 'expanded') and built['slot'] in ('mods-a', 'mods-b'), 'JSON built state')
        _need(re.fullmatch(r'[a-z0-9_-]+', built['base']) and
              re.fullmatch(r'[a-z0-9][a-z0-9._-]*', built['generation']) and
              re.fullmatch(r'[0-9a-f]{64}', built['sha256']), 'JSON generated build identity')
        count, selected = _uint(built.get('count')), _uint(built.get('set'))
        _need(count <= LIST_COUNT, 'JSON build selection count')
        lines.append('built=%s %s %08x %d%s\nslot=%s\nstamp=%s\n' %
                     (built['base'], built['generation'], selected, count,
                      ' expanded' if built['memory'] == 'expanded' else '', built['slot'], built['sha256']))
    mods = desired.get('mods')
    _need(isinstance(mods, list) and len(mods) <= LIST_COUNT, 'JSON mods list')
    for mod in mods:
        _need(isinstance(mod, str) and len(mod) <= (36 if mod.startswith('ext-') else 31)
              and re.fullmatch(r'[a-z0-9_.-]+', mod), 'JSON mod identifier')
    _need(len(set(mods)) == len(mods), 'duplicate JSON selection')
    lines.append('mods=%s\n' % ','.join(mods))
    result = ''.join(lines).encode('ascii')
    _need(len(result) <= 2048, 'JSON capacity')
    return result


def config_document(fields):
    """Validated legacy/native selection fields -> independent desired/build objects."""
    desired = {key: fields.get(key, '') for key in ('base', 'profile', 'generation')}
    desired.update(memory=fields.get('memory', 'standard'), unverified=str(fields.get('unverified', '0')) == '1')
    if str(fields.get('safe', '0')) == '1':
        desired['safe'] = True
    desired['mods'] = fields['mods'].split(',') if fields.get('mods') else []
    built = None
    if fields.get('built') and fields.get('slot') in ('mods-a', 'mods-b'):
        parts = fields['built'].split(' ')
        _need(len(parts) in (4, 5), 'legacy built state')
        built = dict(base=parts[0], generation=parts[1], set=int(parts[2], 16), count=int(parts[3]),
                     memory='expanded' if len(parts) == 5 and parts[4] == 'expanded' else 'standard',
                     slot=fields['slot'], sha256=fields.get('stamp', ''))
    data = (json.dumps(dict(schema=CONFIG_SCHEMA, schema_version=1, desired=desired, built=built),
                       separators=(',', ':'), ensure_ascii=False, allow_nan=False) + '\n').encode()
    config_text(data)
    return data
