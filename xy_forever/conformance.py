"""The conformance suite of the serial protocol: protocol/v1.conformance.json.

The cases are data (their keywords: the file's `syntax`). This module loads and validates them
against protocol/v1.json (scripts/protogen.py calls validate()), interprets every case without a
device (check()), and runs them through a Link: one open port, xyf_host's transports and
usb_proto's codec. scripts/conformance.py is the command; clients/ts/test/conformance.ts is the
TypeScript runner of the same file. On a real device only cases marked real_device run, and
device_guard() refuses every frame that could change more than the test area: the same function
checks the data (validate) and every frame the runner sends. Stdlib only.
"""

import hashlib
import json
import re
import struct
import time
import zlib
from pathlib import Path

from xy_forever.conformance_connection import ConformanceConnection
from xy_forever.device_guard import REPLY_BIT, device_guard
from xy_forever.device_guard import path_in as _path_in
from xy_forever.transport import pause

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / 'protocol' / 'v1.conformance.json'
SPEC = ROOT / 'protocol' / 'v1.json'
FORMAT = 'xyf-conformance/1'

EFFECTS = ('staging', 'writes_test_area', 'restarts', 'writes_device', 'boots_os')
DEVICE_EFFECTS = ('staging', 'writes_test_area', 'restarts')  # what a real-device case may do
NEEDS = ('sim_control', 'sim_library')  # plus fixture:NAME
FIXTURES = ('stock_os_ldr', 'xyf_build')
ACTIONS = ('send', 'send_type', 'send_bytes', 'stream', 'each_type', 'stage', 'write', 'disconnect', 'reconnect', 'sim')
KEYS = {  # object kind -> (required, optional)
    'top': (
        {'$comment', 'format', 'protocol', 'doc', 'test_area', 'timing', 'syntax', 'blobs', 'uncovered', 'cases'},
        set(),
    ),
    'timing': ({'gone_ms', 'back_ms', 'silence_ms'}, set()),
    'case': ({'name', 'doc', 'effects', 'real_device', 'steps'}, {'rules', 'variant', 'needs', 'why_not', 'given'}),
    'given': (set(), {'boot', 'hello', 'files', 'faults'}),
    'file': ({'path', 'data'}, set()),
    'repeat': ({'var', 'from', 'to', 'step'}, set()),
    'expect': (set(), {'status', 'alone', 'fields', 'hello', 'save', 'events', 'replies', 'none_within_ms'}),
    'hello': (set(), {'prefix', 'keys', 'lacks', 'answers', 'pre_v1'}),
    'reply': ({'type', 'seq'}, {'status'}),
    'reconnect': (set(), {'hello', 'ready'}),
    'disconnect': (set(), set()),
    'write': ({'path', 'data'}, set()),
    'events': ({'log'}, set()),
    'count': ({'min'}, set()),
    'uncovered': ({'why'}, {'command', 'status', 'rule'}),
    'frame': ({'type', 'seq'}, {'payload'}),
}
STEP_COMMON = {'expect', 'repeat', 'seq', 'doc', 'fields', 'payload'}
EXPECTS = {  # action -> expect keys it may use
    'send': {'status', 'alone', 'fields', 'hello', 'save', 'events'},
    'send_type': {'status', 'alone'},
    'each_type': {'status', 'alone'},
    'send_bytes': {'replies', 'none_within_ms'},
}
# Value forms: primary key -> every key the form may have.
FORMS = (
    ('prefix', {'prefix', 'fill', 'length'}),
    ('text', {'text'}),
    ('hex', {'hex'}),
    ('fill', {'fill', 'count'}),
    ('ramp', {'ramp'}),
    ('blob', {'blob', 'from', 'length'}),
    ('size', {'size', 'plus'}),
    ('crc32', {'crc32'}),
    ('sha256', {'sha256'}),
    ('const', {'const', 'plus'}),
    ('hello', {'hello', 'plus'}),
    ('var', {'var', 'plus'}),
    ('frame', {'frame', 'from', 'length'}),
    ('concat', {'concat'}),
)
BLOB_FORMS = ('text', 'hex', 'fill', 'ramp', 'fixture')
CONSTRAINTS = {'len', 'len_min', 'len_max', 'min', 'max', 'matches', 'one_of'}
EVENT_LOG = 0xD0


class CaseError(ValueError):
    """The data cannot be interpreted (or breaks a rule of the suite)."""


class Fail(Exception):
    """An expectation was not met."""


class Skip(Exception):
    """The case does not apply here (with the reason)."""


class Unsafe(Exception):
    """device_guard refused a frame: nothing was sent."""


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise CaseError('duplicate key %r' % key)
        out[key] = value
    return out


def load(path=CASES):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_no_duplicates)


def load_spec(path=SPEC):
    return json.loads(Path(path).read_text(encoding='utf-8'))


# ---- the spec, indexed ----------------------------------------------------------------------


class Spec:
    def __init__(self, spec):
        self.raw = spec
        self.version = spec['protocol']['version']
        self.commands = {c['name']: c for c in spec['commands']}
        self.by_type = {int(c['type'], 16): c for c in spec['commands']}
        self.published = {n for n, c in self.commands.items() if not c.get('test_only')}
        self.statuses = {s['name']: s['code'] for s in spec['statuses']}
        self.status_names = {s['code']: s['name'] for s in spec['statuses']}
        self.constants = {k['name']: k['value'] for k in spec['constants']}
        self.streams = {s['name']: s for s in spec['streams']}
        self.rules = [r['name'] for r in spec['rules']]
        self.common = [e['status'] for e in spec['common_errors']]

    def type_of(self, name):
        return int(self.commands[name]['type'], 16)

    def status_name(self, code):
        return self.status_names.get(code, 'status_%d' % code)

    def in_every_build(self):
        """Published commands every build with the service answers (no optional switch)."""
        return {n for n in self.published if not self.commands[n].get('switch_optional')}


def field_kind(f):
    """int, str or bytes: what a value of this field is."""
    t = f['type']
    return 'int' if t in ('u8', 'u16', 'u32') else 'str' if t in ('cstr', 'text') else 'bytes'


# ---- values -----------------------------------------------------------------------------------


class Dry:
    """Stands for a value known only while running (a var, a hello key) during check()."""


class Context:
    """What values resolve against: blobs, constants, the last hello reply, saved vars."""

    def __init__(self, spec, data, fixtures=None, dry=False):
        self.spec, self.data, self.dry = spec, data, dry
        self.fixtures = fixtures or {}
        self.vars, self.hello_text, self._blobs = {}, None, {}

    def blob(self, name):
        if name not in self.data['blobs']:
            raise CaseError('unknown blob %r' % name)
        if name not in self._blobs:
            self._blobs[name] = make_blob(self.data['blobs'][name], name, self.fixtures, self.dry)
        return self._blobs[name]

    def hello_key(self, key):
        if self.dry:
            return Dry()
        if self.hello_text is None:
            raise Fail('no hello reply yet: {hello: %s} has no value' % key)
        keys = dict(item.split('=', 1) for item in self.hello_text.split(';') if '=' in item)
        if key not in keys:
            raise Fail('the hello reply has no key %r' % key)
        return keys[key]


def make_blob(b, name, fixtures, dry=False):
    if not isinstance(b, dict) or len([k for k in BLOB_FORMS if k in b]) != 1:
        raise CaseError('blob %s: one of %s' % (name, ', '.join(BLOB_FORMS)))
    if 'fixture' in b:
        if b['fixture'] not in FIXTURES:
            raise CaseError('blob %s: unknown fixture %r' % (name, b['fixture']))
        path = fixtures.get(b['fixture'])
        if path is None:
            if dry:
                return b''
            raise Skip('needs fixture %s (--fixture %s=PATH)' % (b['fixture'], b['fixture']))
        return Path(path).read_bytes()
    return _bytes_form(b, 'blob %s' % name)


def _bytes_form(v, where):
    if 'text' in v and set(v) == {'text'}:
        return v['text'].encode('utf-8')
    if 'hex' in v and set(v) == {'hex'}:
        return _hex(v['hex'], where)
    if 'fill' in v and set(v) == {'fill', 'count'}:
        if not (_int(v['fill']) and 0 <= v['fill'] <= 255 and _int(v['count']) and v['count'] >= 0):
            raise CaseError('%s: fill is a byte, count a number' % where)
        return bytes((v['fill'],)) * v['count']
    if 'ramp' in v and set(v) == {'ramp'}:
        if not _int(v['ramp']) or v['ramp'] < 0:
            raise CaseError('%s: ramp is a number' % where)
        return bytes(i & 0xFF for i in range(v['ramp']))
    raise CaseError('%s: not a blob form' % where)


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _hex(text, where):
    if not isinstance(text, str) or not re.fullmatch(r'([0-9a-f]{2})*', text):
        raise CaseError('%s: %r is not lowercase hex' % (where, text))
    return bytes.fromhex(text)


def form_of(v):
    """The value form of an object, or None for a constraint."""
    if not isinstance(v, dict):
        return None
    if set(v) & CONSTRAINTS:
        if set(v) - CONSTRAINTS:
            raise CaseError('constraint with unknown keys %s' % sorted(set(v) - CONSTRAINTS))
        return None
    for primary, keys in FORMS:
        if primary in v:
            if set(v) - keys:
                raise CaseError('value {%s} with unknown keys %s' % (primary, sorted(set(v) - keys)))
            return primary
    raise CaseError('unknown value form %s' % sorted(v))


def resolve(v, want, ctx, where='value'):
    """A value as int, str or bytes (want: int, str, bytes or any)."""
    if isinstance(v, bool) or v is None:
        raise CaseError('%s: %r is not a value' % (where, v))
    if isinstance(v, int):
        if want in ('int', 'any'):
            return v
        if want == 'str':
            return str(v)
        raise CaseError('%s: a number where bytes are wanted' % where)
    if isinstance(v, str):
        return _hex(v, where) if want == 'bytes' else v if want in ('str', 'any') else _bad(where, v, want)
    if isinstance(v, list):
        raise CaseError('%s: a list is not a value' % where)
    form = form_of(v)
    if form is None:
        raise CaseError('%s: a constraint where a value is wanted' % where)
    out = _form(v, form, ctx, where)
    if isinstance(out, Dry):
        return 0 if want == 'int' else '' if want == 'str' else b'' if want == 'bytes' else out
    if isinstance(out, str) and want == 'bytes':
        return out.encode('utf-8')
    if isinstance(out, str) and want == 'int':
        if not out.isdigit():
            raise Fail('%s: %r is not a number' % (where, out))
        return int(out)
    if isinstance(out, int) and want == 'str':
        return str(out)
    if isinstance(out, (bytes, bytearray)) and want in ('int', 'str'):
        raise CaseError('%s: bytes where a %s is wanted' % (where, want))
    if isinstance(out, int) and want == 'bytes':
        raise CaseError('%s: a number where bytes are wanted' % where)
    return out


def _bad(where, v, want):
    raise CaseError('%s: %r where a %s is wanted' % (where, v, want))


def _form(v, form, ctx, where):
    plus = v.get('plus', 0)
    if not _int(plus):
        raise CaseError('%s: plus must be a number' % where)
    if form == 'prefix':
        length = resolve(v['length'], 'int', ctx, where)
        fill = v['fill']
        if not isinstance(v['prefix'], str) or not isinstance(fill, str) or len(fill.encode()) != 1:
            raise CaseError('%s: prefix is text, fill one ASCII character' % where)
        return v['prefix'] + fill * max(0, length - len(v['prefix'].encode('utf-8')))
    if form == 'text':
        if not isinstance(v['text'], str):
            raise CaseError('%s: text must be a string' % where)
        return v['text']
    if form in ('hex', 'fill', 'ramp'):
        return _bytes_form(v, where)
    if form == 'blob':
        data = ctx.blob(v['blob'])
        start = resolve(v.get('from', 0), 'int', ctx, where)
        end = len(data) if 'length' not in v else start + resolve(v['length'], 'int', ctx, where)
        return data[start:end]
    if form == 'size':
        return len(ctx.blob(v['size'])) + plus
    if form == 'crc32':
        return zlib.crc32(ctx.blob(v['crc32'])) & 0xFFFFFFFF
    if form == 'sha256':
        return hashlib.sha256(ctx.blob(v['sha256'])).digest()
    if form == 'const':
        if v['const'] not in ctx.spec.constants:
            raise CaseError('%s: unknown constant %r' % (where, v['const']))
        return ctx.spec.constants[v['const']] + plus
    if form == 'hello':
        value = ctx.hello_key(v['hello'])
        if plus and not isinstance(value, Dry):
            if not value.isdigit():
                raise Fail('%s: hello %s=%s is not a number' % (where, v['hello'], value))
            return int(value) + plus
        return value
    if form == 'var':
        if ctx.dry:
            return Dry()
        if v['var'] not in ctx.vars:
            raise CaseError('%s: var %r is not set' % (where, v['var']))
        value = ctx.vars[v['var']]
        return value + plus if plus else value
    if form == 'frame':
        f = v['frame']
        check_keys(f, 'frame', where)
        kind = int(f['type'], 16) if isinstance(f['type'], str) and re.fullmatch(r'0x[0-9a-f]{2}', f['type']) else None
        if kind is None or not _int(f['seq']) or not 0 <= f['seq'] <= 255:
            raise CaseError('%s: frame type "0xNN", seq 0..255' % where)
        frame = encode(kind, f['seq'], resolve(f.get('payload', ''), 'bytes', ctx, where))
        start = resolve(v.get('from', 0), 'int', ctx, where)
        end = len(frame) if 'length' not in v else start + resolve(v['length'], 'int', ctx, where)
        return frame[start:end]
    if form == 'concat':
        if not isinstance(v['concat'], list):
            raise CaseError('%s: concat takes a list' % where)
        return b''.join(resolve(x, 'bytes', ctx, where) for x in v['concat'])
    raise CaseError('%s: form %s' % (where, form))


def encode(kind, seq, payload):
    from xy_forever import usb_proto as P

    return P.encode(kind, seq, payload)


def check_keys(obj, kind, where):
    if not isinstance(obj, dict):
        raise CaseError('%s: must be an object' % where)
    required, optional = KEYS[kind]
    missing, unknown = required - set(obj), set(obj) - required - optional
    if missing or unknown:
        raise CaseError(
            '%s: %s'
            % (
                where,
                '; '.join(
                    filter(
                        None,
                        (
                            missing and 'missing %s' % ', '.join(sorted(missing)),
                            unknown and 'unknown key(s) %s' % ', '.join(sorted(unknown)),
                        ),
                    )
                ),
            )
        )


# ---- requests ---------------------------------------------------------------------------------


def request_payload(step, ctx, where):
    """(type, payload, command or None) of a send/send_type step."""
    spec = ctx.spec
    if 'send' in step:
        name = step['send']
        if name not in spec.published:
            raise CaseError('%s: %r is not a published command of the spec' % (where, name))
        command = spec.commands[name]
        if ('fields' in step) == ('payload' in step):
            raise CaseError('%s: send takes fields or payload' % where)
        if 'payload' in step:
            return spec.type_of(name), resolve(step['payload'], 'bytes', ctx, where), command
        return spec.type_of(name), pack(command['request']['fields'], step['fields'], ctx, where), command
    kind = step['send_type']
    if not isinstance(kind, str) or not re.fullmatch(r'0x[0-7][0-9a-f]', kind):
        raise CaseError('%s: send_type is "0xNN" below 0x80' % where)
    if int(kind, 16) in spec.by_type:
        raise CaseError('%s: send_type %s is a command of the spec: use send' % (where, kind))
    if 'fields' in step:
        raise CaseError('%s: send_type takes a payload, not fields' % where)
    return int(kind, 16), resolve(step.get('payload', ''), 'bytes', ctx, where), None


def pack(layout, given, ctx, where):
    from xy_forever import usb_proto as P

    if not isinstance(given, dict):
        raise CaseError('%s: fields must be an object' % where)
    names = [f['name'] for f in layout]
    unknown = set(given) - set(names)
    if unknown:
        raise CaseError('%s: unknown field(s) %s' % (where, ', '.join(sorted(unknown))))
    values = {
        f['name']: resolve(given[f['name']], field_kind(f), ctx, '%s.%s' % (where, f['name']))
        for f in layout
        if f['name'] in given
    }
    for f in layout:
        if not f.get('optional') and f['name'] not in values:
            raise CaseError('%s: field %s missing' % (where, f['name']))
        value = values.get(f['name'])
        size = (
            len(value.encode(f['encoding']) + (b'\0' if f['type'] == 'cstr' else b''))
            if isinstance(value, str)
            else len(value)
            if isinstance(value, bytes)
            else 0
        )
        if 'max_bytes' in f and size > f['max_bytes']:  # as the TypeScript codec: send it as a payload
            raise CaseError('%s: %s above %d bytes' % (where, f['name'], f['max_bytes']))
    table = tuple(
        (
            f['name'],
            'bytes' if f['type'].startswith('bytes') else f['type'],
            int(f['type'][6:-1]) if f['type'].startswith('bytes[') else None,
            bool(f.get('optional')),
            f.get('encoding'),
        )
        for f in layout
    )
    try:
        return P.pack(table, values)
    except (ValueError, KeyError, UnicodeEncodeError, struct.error) as error:
        raise CaseError('%s: %s' % (where, error))


def decode_fields(command, payload):
    """{name: value} of a reply after its status, by the reply layout (lenient); None: status alone."""
    from xy_forever import usb_proto as P

    reply = command['reply']
    status = struct.unpack_from('<I', payload)[0]
    body = payload[4:]
    fields = reply['fields']
    if status != 0:
        if not body or reply['on_error'] == 'status':
            return None
        if reply['on_error'] != 'full':
            fields = [f for f in fields if f['name'] in reply['on_error']]
    table = tuple(
        (
            f['name'],
            'bytes' if f['type'].startswith('bytes') else f['type'],
            int(f['type'][6:-1]) if f['type'].startswith('bytes[') else None,
            bool(f.get('optional')),
            f.get('encoding'),
        )
        for f in fields
    )
    try:
        return P.unpack(table, body, exact=False)
    except (ValueError, UnicodeDecodeError) as error:
        raise Fail('reply fields do not decode: %s' % error)


# ---- validation (scripts/protogen.py) ---------------------------------------------------------


def walk_frames(spec, case, ctx):
    """Every (type, payload) a case sends, resolved without a device (vars and hello keys dry)."""
    from xy_forever import usb_proto as P

    where, given, out = case['name'], case.get('given', {}), []
    if given.get('boot') == 'fresh':
        out += [(spec.type_of('hello'), b''), (spec.type_of('device.reboot'), struct.pack('<I', 2))]
    writes = [(f['path'], f['data']) for f in given.get('files', [])]
    for step in case['steps']:
        action = step_action(step, where)
        if action in ('send', 'send_type'):
            out.append(request_payload(step, ctx, where)[:2])
        elif action in ('send_bytes', 'stream'):
            data = (
                resolve(step['send_bytes'], 'bytes', ctx, where)
                if action == 'send_bytes'
                else b''.join(bytes.fromhex(c) for c in spec.streams[step['stream']]['chunks'])
            )
            out += [(k, p) for k, _, p in P.Parser().feed(data)]
        elif action == 'stage':
            out += stage_frames(spec, resolve(step['stage'], 'bytes', ctx, where))
        elif action == 'write':
            writes.append((step['write']['path'], step['write']['data']))
        elif action == 'reconnect' and step['reconnect'].get('hello', True):
            out.append((spec.type_of('hello'), b''))
            if step['reconnect'].get('ready'):
                out.append((spec.type_of('storage.space'), struct.pack('<I', 1)))
    for path, data in writes:
        body = resolve(data, 'bytes', ctx, where)
        out += stage_frames(spec, body)
        out.append((spec.type_of('file.write_staged'), write_payload(resolve(path, 'str', ctx, where), body)))
    return out


def write_payload(path, body):
    return path.encode('utf-8') + b'\0' + hashlib.sha256(body).digest()


def stage_frames(spec, body):
    chunk = spec.constants['put_chunk']
    out = [
        (
            spec.type_of('stage.begin'),
            struct.pack('<II', len(body), zlib.crc32(body) & 0xFFFFFFFF) + hashlib.sha256(body).digest(),
        )
    ]
    out += [
        (spec.type_of('stage.data'), struct.pack('<I', at) + body[at : at + chunk]) for at in range(0, len(body), chunk)
    ]
    out.append((spec.type_of('stage.end'), b''))
    return out


def step_action(step, where):
    if not isinstance(step, dict):
        raise CaseError('%s: a step must be an object' % where)
    actions = [a for a in ACTIONS if a in step]
    if len(actions) != 1:
        raise CaseError('%s: a step has exactly one of %s (has %s)' % (where, ', '.join(ACTIONS), actions or 'none'))
    unknown = set(step) - STEP_COMMON - set(ACTIONS)
    if unknown:
        raise CaseError('%s: unknown step key(s) %s' % (where, ', '.join(sorted(unknown))))
    action = actions[0]
    if action not in ('send', 'send_type') and ('fields' in step or 'payload' in step):
        raise CaseError('%s: fields/payload belong to send' % where)
    if 'seq' in step and action != 'send':
        raise CaseError('%s: seq belongs to send' % where)
    if 'expect' in step and action not in EXPECTS:
        raise CaseError('%s: %s takes no expect' % (where, action))
    if action in EXPECTS and 'expect' not in step:
        raise CaseError('%s: %s needs an expect' % (where, action))
    if 'repeat' in step and action not in ('send', 'send_type'):
        raise CaseError('%s: repeat belongs to send' % where)
    return action


def derived(spec, case, test_area=None):
    """(effects, needs) a case's data implies. A write or change outside test_area (1.7: the content
    folder) writes the device, not the test area."""
    effects, needs = set(), set()

    def wrote(step):
        fields = step.get('fields', {})
        names = (
            ('target',) if step.get('send') == 'file.copy' else ('path', 'source', 'target')
        )  # copy reads its source
        paths = [p for p in (fields.get(n) for n in names) if isinstance(p, str)]
        outside = test_area and any(not _path_in(p, test_area) for p in paths)
        effects.add('writes_device' if outside else 'writes_test_area')

    given = case.get('given', {})
    if given.get('files'):
        effects |= {'staging', 'writes_test_area'}
    if given.get('faults'):
        needs.add('sim_control')
    for step in case['steps']:
        action = step_action(step, case['name'])
        status = step.get('expect', {}).get('status')
        name = step.get('send')
        if action in ('stage', 'write') or (name == 'stage.begin' and status == 'ok'):
            effects.add('staging')
        if action == 'write' or (name == 'file.write_staged' and status in ('ok', 'storage')):  # storage: maybe part
            wrote(step)
        if name in ('file.delete', 'file.rmdir', 'file.mkdir', 'file.rename', 'file.copy') and 'ok' in (
            status if isinstance(status, list) else [status]
        ):
            wrote(step)
        if name == 'file.copy' and status in ('ok', 'storage', 'crc', 'not_found', 'too_large'):
            effects.add('staging')  # 1.9: the copy takes the staging buffer
        if name in ('boot.install', 'boot.uninstall', 'boot.uninstall_start', 'library.set_default') and status == 'ok':
            effects.add('writes_device')
        fields = step.get('fields', {})
        if status == 'ok' and (
            (name == 'menu.continue' and fields.get('mode', 0) == 0 and 'payload' not in step)
            or (name == 'device.reboot' and fields.get('target') == 1)
            or name in ('library.boot', 'boot.uninstall_start')
        ):
            effects.add('boots_os')
        if action == 'sim':
            needs.add('sim_control')
    if given.get('boot') == 'fresh' or any('disconnect' in s for s in case['steps']):
        if 'boots_os' not in effects:
            effects.add('restarts')
    return effects, needs


def fixtures_used(data, value, out):
    if isinstance(value, dict):
        for k, v in value.items():
            if (
                k in ('blob', 'size', 'crc32', 'sha256')
                and isinstance(v, str)
                and v in data['blobs']
                and 'fixture' in data['blobs'][v]
            ):
                out.add('fixture:' + data['blobs'][v]['fixture'])
            fixtures_used(data, v, out)
    elif isinstance(value, list):
        for v in value:
            fixtures_used(data, v, out)
    return out


def coverage(spec, data):
    """{(command or '*', status)} the cases expect, and the rules and streams they show."""
    pairs, rules, streams = set(), set(), set()
    for case in data['cases']:
        rules |= set(case.get('rules', []))
        for step in case['steps']:
            status = step.get('expect', {}).get('status')
            statuses = status if isinstance(status, list) else [status] if status else []
            target = step.get('send') or '*'
            pairs |= {(target, s) for s in statuses}
            if 'stream' in step:
                streams.add(step['stream'])
    return pairs, rules, streams


def validate(spec_json, data):
    """Raises CaseError listing every problem of the case file."""
    spec = Spec(spec_json)
    errors = []

    def fail(where, message):
        errors.append('%s: %s' % (where, message))

    try:
        check_keys(data, 'top', 'conformance')
        check_keys(data['timing'], 'timing', 'timing')
    except CaseError as error:
        raise CaseError(str(error))
    if data['format'] != FORMAT:
        fail('format', 'must be %r' % FORMAT)
    if data['protocol'] != spec.version:
        fail('protocol', 'must be the spec version %s' % spec.version)
    if not isinstance(data['doc'], list) or not all(isinstance(p, str) and p for p in data['doc']):
        fail('doc', 'must be a list of paragraphs')
    if not re.fullmatch(r'/yaffs2/xy-forever/([a-z0-9_-]+/)+', str(data['test_area'])):
        fail('test_area', 'must be a folder under /yaffs2/xy-forever/, ending in /')
    ctx = Context(spec, data, dry=True)
    for name in data['blobs']:
        try:
            make_blob(data['blobs'][name], name, {}, dry=True)
        except CaseError as error:
            fail('blobs', str(error))
    names = set()
    for i, case in enumerate(data['cases']):
        where = 'cases[%d]' % i
        try:
            check_keys(case, 'case', where)
            where = case['name']
            if not re.fullmatch(r'[a-z0-9_.-]+/[a-z0-9_.-]+', where):
                fail(where, 'name is group/what, lowercase')
            if where in names:
                fail(where, 'duplicate name')
            names.add(where)
            check_case(spec, data, case, ctx)
        except CaseError as error:
            fail(where, str(error))
    if errors:
        raise CaseError('\n'.join(errors))
    pairs, rules, streams = coverage(spec, data)
    covered = {s for _, s in pairs}
    listed = set()
    for i, u in enumerate(data['uncovered']):
        where = 'uncovered[%d]' % i
        try:
            check_keys(u, 'uncovered', where)
        except CaseError as error:
            fail(where, str(error))
            continue
        if 'rule' in u:
            if u['rule'] not in spec.rules or set(u) != {'rule', 'why'}:
                fail(where, 'rule must name a rule of the spec, alone')
            elif u['rule'] in rules:
                fail(where, 'rule %s is shown by a case: remove it here' % u['rule'])
            listed.add(('rule', u['rule']))
        elif 'command' in u:
            c = spec.commands.get(u['command'])
            if c is None or u.get('status') not in ['ok'] + c['errors']:
                fail(where, 'command and ok or one of its documented errors')
            elif (u['command'], u['status']) in pairs:
                fail(where, '%s %s is shown by a case: remove it here' % (u['command'], u['status']))
            listed.add((u['command'], u.get('status')))
        elif u.get('status') in spec.common:
            if u['status'] in covered:
                fail(where, 'status %s is shown by a case' % u['status'])
            listed.add(('*', u['status']))
        else:
            fail(where, 'names a rule, a command and status, or a common status')
    for name in sorted(spec.published):
        c = spec.commands[name]
        for status in ['ok'] + c['errors']:
            if (name, status) not in pairs and (name, status) not in listed:
                fail('coverage', '%s %s: no case expects it, and uncovered does not list it' % (name, status))
    for status in spec.common:
        if status not in covered and ('*', status) not in listed:
            fail('coverage', 'common status %s: no case expects it, and uncovered does not list it' % status)
    for rule in spec.rules:
        if rule not in rules and ('rule', rule) not in listed:
            fail('coverage', 'rule %s: no case shows it, and uncovered does not list it' % rule)
    for st in spec.raw['streams']:
        if st['direction'] == 'to_device' and st['name'] not in streams:
            fail('coverage', 'stream %r: no case sends it' % st['name'])
    if errors:
        raise CaseError('\n'.join(errors))


def check_case(spec, data, case, ctx):
    """Structure, values, encodings and the marks of one case (raises CaseError)."""
    where = case['name']
    if not isinstance(case['doc'], str) or not case['doc']:
        raise CaseError('doc must be text')
    if case.get('variant', 'v1') not in ('v1', 'pre_v1'):
        raise CaseError('variant is v1 or pre_v1')
    for rule in case.get('rules', []):
        if rule not in spec.rules:
            raise CaseError('unknown rule %r' % rule)
    if not isinstance(case['effects'], list) or set(case['effects']) - set(EFFECTS):
        raise CaseError('effects are a list of %s' % ', '.join(EFFECTS))
    needs = case.get('needs', [])
    for need in needs:
        if need not in NEEDS and not (need.startswith('fixture:') and need[8:] in FIXTURES):
            raise CaseError('unknown need %r' % need)
    if not isinstance(case['real_device'], bool):
        raise CaseError('real_device must be true or false')
    if case['real_device'] == ('why_not' in case):
        raise CaseError('why_not is required exactly when real_device is false')
    given = case.get('given', {})
    check_keys(given, 'given', '%s given' % where)
    if given.get('boot', 'any') not in ('any', 'fresh'):
        raise CaseError('given.boot is fresh')
    if given.get('hello', True) is not True and (given.get('hello') is not False or given.get('boot') != 'fresh'):
        raise CaseError('given.hello false needs given.boot fresh')
    for f in given.get('files', []):
        check_keys(f, 'file', '%s given.files' % where)
        path = resolve(f['path'], 'str', ctx, where)
        if not _path_in(path, data['test_area']):
            raise CaseError('given.files: %s is outside the test area' % path)
        resolve(f['data'], 'bytes', ctx, where)
    for name, value in given.get('faults', {}).items():
        if not re.fullmatch(r'[a-z_]+', name) or not _int(value):
            raise CaseError('given.faults: name -> number')
    if not isinstance(case['steps'], list) or not case['steps']:
        raise CaseError('steps must be a non-empty list')
    for n, step in enumerate(case['steps']):
        check_step(spec, data, case, step, '%s step %d' % (where, n + 1), ctx)
    effects, need_set = derived(spec, case, data.get('test_area'))
    if effects != set(case['effects']):
        raise CaseError('effects %s, but the steps do %s' % (sorted(case['effects']), sorted(effects)))
    need_set |= fixtures_used(data, case, set())
    if need_set != set(needs) - {'sim_library'}:
        raise CaseError('needs %s, but the steps need %s' % (sorted(needs), sorted(need_set)))
    if case['real_device']:
        if set(case['effects']) - set(DEVICE_EFFECTS) or needs:
            raise CaseError('a real-device case may only %s, and needs nothing' % ', '.join(DEVICE_EFFECTS))
        for kind, payload in walk_frames(spec, case, ctx):
            why = device_guard(spec, kind, payload, data['test_area'])
            if why:
                raise CaseError('marked real_device, but sends what the device guard refuses: %s' % why)


def check_step(spec, data, case, step, where, ctx):
    action = step_action(step, where)
    if 'repeat' in step:
        check_keys(step['repeat'], 'repeat', where + ' repeat')
        for k in ('from', 'to', 'step'):
            resolve(step['repeat'][k], 'int', ctx, where)
    command = None
    if action in ('send', 'send_type'):
        kind, payload, command = request_payload(step, ctx, where)
        if len(payload) > spec.constants['max_payload']:
            raise CaseError('%s: payload above max_payload' % where)
        if 'seq' in step and not (_int(step['seq']) and 0 <= step['seq'] <= 255):
            raise CaseError('%s: seq is 0..255' % where)
    elif action == 'send_bytes':
        resolve(step['send_bytes'], 'bytes', ctx, where)
    elif action == 'stream':
        st = spec.streams.get(step['stream'])
        if st is None or st['direction'] != 'to_device':
            raise CaseError('%s: %r is not a to_device stream of the spec' % (where, step['stream']))
    elif action == 'each_type':
        if step['each_type'] != 'unlisted':
            raise CaseError('%s: each_type is "unlisted"' % where)
    elif action == 'stage':
        resolve(step['stage'], 'bytes', ctx, where)
    elif action == 'write':
        check_keys(step['write'], 'write', where)
        path = resolve(step['write']['path'], 'str', ctx, where)
        if not _path_in(path, data['test_area']):
            raise CaseError('%s: write only into the test area' % where)
        resolve(step['write']['data'], 'bytes', ctx, where)
    elif action in ('disconnect', 'reconnect'):
        check_keys(step[action], action, where)
        if not isinstance(step[action].get('hello', True), bool):
            raise CaseError('%s: hello is true or false' % where)
        if not isinstance(step[action].get('ready', False), bool) or (
            step[action].get('ready') and not step[action].get('hello', True)
        ):
            raise CaseError('%s: ready is a boolean and requires hello' % where)
    elif action == 'sim':
        if not isinstance(step['sim'], str) or not re.fullmatch(r'[a-z]+( [a-z_]+)*( -?[0-9]+)?', step['sim']):
            raise CaseError('%s: sim is a control line' % where)
    if 'expect' in step:
        check_expect(spec, step, action, command, where, ctx)


def check_expect(spec, step, action, command, where, ctx):
    e = step['expect']
    check_keys(e, 'expect', where + ' expect')
    if set(e) - EXPECTS[action]:
        raise CaseError('%s: %s cannot expect %s' % (where, action, ', '.join(sorted(set(e) - EXPECTS[action]))))
    statuses = e.get('status', [])
    for s in statuses if isinstance(statuses, list) else [statuses]:
        if s not in spec.statuses:
            raise CaseError('%s: unknown status %r' % (where, s))
    if 'alone' in e and e['alone'] is not True:
        raise CaseError('%s: alone is true' % where)
    reply_fields = {f['name']: f for f in command['reply']['fields']} if command else {}
    for name, value in e.get('fields', {}).items():
        if name not in reply_fields:
            raise CaseError('%s: the reply has no field %r' % (where, name))
        check_expected(value, field_kind(reply_fields[name]), ctx, '%s %s' % (where, name))
    for _var, name in e.get('save', {}).items():
        if name not in reply_fields:
            raise CaseError('%s: save from unknown field %r' % (where, name))
    if 'hello' in e:
        if not command or command['name'] != 'hello':
            raise CaseError('%s: hello expectations belong to hello' % where)
        h = e['hello']
        check_keys(h, 'hello', where + ' hello')
        for key, value in h.get('keys', {}).items():
            check_expected(value, 'str', ctx, '%s hello %s' % (where, key))
        for name in h.get('answers', []):
            if name not in spec.published:
                raise CaseError('%s: answers lists %r, not a published command' % (where, name))
        if h.get('answers') and set(h['answers']) != spec.in_every_build():
            raise CaseError(
                '%s: answers must list every command each build answers: %s'
                % (where, ', '.join(sorted(spec.in_every_build())))
            )
    if 'events' in e:
        check_keys(e['events'], 'events', where)
        check_keys(e['events']['log'], 'count', where)
    for r in e.get('replies', []):
        check_keys(r, 'reply', where + ' replies')
        if not re.fullmatch(r'0x[89a-f][0-9a-f]', str(r['type'])) or r.get('status', 'ok') not in spec.statuses:
            raise CaseError('%s: a reply has a type 0x80.., a seq and a status' % where)
    if 'none_within_ms' in e and not (_int(e['none_within_ms']) and e['none_within_ms'] > 0):
        raise CaseError('%s: none_within_ms is a number' % where)


def check_expected(value, kind, ctx, where):
    if isinstance(value, dict) and form_of(value) is None:
        for k, v in value.items():
            if k in ('len', 'len_min', 'len_max', 'min', 'max'):
                resolve(v, 'int', ctx, where)
            elif k == 'matches':
                if not isinstance(v, str):
                    raise CaseError('%s: matches takes a pattern' % where)
                re.compile(v)
            elif k == 'one_of':
                if not isinstance(v, list) or not v:
                    raise CaseError('%s: one_of takes a list' % where)
                for x in v:
                    resolve(x, kind, ctx, where)
        return
    resolve(value, kind, ctx, where)


def check(spec_json, data, fixtures=None):
    """Interpret every case without a device: validate(), then each case's values, frames and
    expectations resolved with dry vars. Returns the number of cases (CaseError on any problem)."""
    validate(spec_json, data)
    spec = Spec(spec_json)
    ctx = Context(spec, data, fixtures, dry=True)
    for case in data['cases']:
        walk_frames(spec, case, ctx)
    return len(data['cases'])


# ---- running ----------------------------------------------------------------------------------


class Link:
    """One open port: frames out (through the device guard on a real device), replies in."""

    def __init__(self, transport, spec, log, guard_area=None):
        from xy_forever import usb_proto as P

        self.t, self.spec, self.log, self.guard_area = transport, spec, log, guard_area
        self.parser = P.Parser()
        self.seq = 0
        self.closed = False

    def write(self, data):
        if self.guard_area is not None:
            from xy_forever import usb_proto as P

            for kind, _, payload in P.Parser().feed(data):
                why = device_guard(self.spec, kind, payload, self.guard_area)
                if why:
                    raise Unsafe(why)
        self.log('> %d B %s' % (len(data), data[:48].hex() + ('...' if len(data) > 48 else '')))
        self.t.write(data)

    def next_seq(self):
        self.seq = self.seq % 255 + 1
        return self.seq

    def read_frames(self, timeout):
        """Frames arriving within timeout (stops at the first read that returns some)."""
        try:
            data = self.t.read(timeout)
        except OSError:
            self.closed = True
            raise
        frames = self.parser.feed(data) if data else []
        for kind, seq, payload in frames:
            self.log('< 0x%02x seq %d %d B %s' % (kind, seq, len(payload), payload[:24].hex()))
        return frames

    def request(self, kind, payload, timeout, seq=None):
        """(reply payload, log events) of one request; stale frames are dropped."""
        from xy_forever import usb_proto as P

        seq = self.next_seq() if seq is None else seq
        self.write(P.encode(kind, seq, payload))
        events, deadline = 0, time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                frames = self.read_frames(min(0.2, max(0.0, deadline - time.monotonic())))
            except OSError as error:
                raise Fail('connection lost waiting for the reply to 0x%02x: %s' % (kind, error))
            for k, s, body in frames:
                if k == EVENT_LOG and s == 0:
                    events += 1
                elif k == kind | REPLY_BIT and s == seq:
                    if len(body) < 4:
                        raise Fail('reply to 0x%02x without a status' % kind)
                    return body, events
        self.parser.reset()
        raise Fail('no reply to 0x%02x seq %d within %.1f s' % (kind, seq, timeout))

    def quiet(self, ms):
        """Frames that arrive within ms (no reply expected)."""
        got, deadline = [], time.monotonic() + ms / 1000.0
        while time.monotonic() < deadline:
            got += [f for f in self.read_frames(max(0.0, deadline - time.monotonic())) if f[0] != EVENT_LOG]
        return got

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.t.close()
            except OSError:
                pass


class Runner(ConformanceConnection):
    """Runs cases against a target (an object with open(), present(), port_gone(link, timeout) and,
    on the simulator, control(line)). real: a real device (safe cases only, every frame guarded)."""

    def __init__(self, spec_json, data, target, real, fixtures=None, sim_control=False, reply_cap=5.0):
        self.spec = Spec(spec_json)
        self.data, self.target, self.real = data, target, real
        self.fixtures = fixtures or {}
        self.sim_control = sim_control
        self.reply_cap = reply_cap
        self.timing = data['timing']
        self.hello_text = None
        self.lines = []
        self.ctx = None

    def log(self, text):
        self.lines.append(text)

    # -- connection --------------------------------------------------------------------------
    def greet(self, link, hello, deadline):
        """hello (hello=False: a type no command uses, which before hello gets state and does
        nothing) until answered. A real device's port can appear before its service reads it, so
        there each attempt waits 0.5 s, as xyf_host.connect does; the simulator answers at once."""
        probe = next(t for t in range(0x06, 0x80) if t not in self.spec.by_type)
        while True:
            try:
                if hello:
                    return self.hello(link, 0.5 if self.real else self.cap('hello'))
                return link.request(probe, b'', 0.5)
            except Fail:
                if not self.real or time.monotonic() > deadline:
                    raise

    def hello(self, link, timeout=None):
        body, _ = link.request(self.spec.type_of('hello'), b'', timeout or self.cap('hello'))
        status = struct.unpack_from('<I', body)[0]
        if status:
            raise Fail('hello: %s' % self.spec.status_name(status))
        self.hello_text = body[4:].decode('ascii', 'replace')
        if self.ctx:
            self.ctx.hello_text = self.hello_text
        return self.hello_text

    def cap(self, name):
        return min(self.spec.commands[name]['timeout_ms'] / 1000.0, self.reply_cap) if name else self.reply_cap

    def start(self, wait):
        """HELLO within wait s: the hello text, or None (nothing answered)."""
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if self.target.present():
                try:
                    link = Link(self.target.open(), self.spec, self.log)
                except OSError:
                    pause(self.target, 0.2)
                    continue
                try:
                    return self.greet(link, True, deadline)
                except Fail, OSError:
                    pass
                finally:
                    link.close()
            pause(self.target, 0.2)
        return None

    @property
    def pre_v1(self):
        keys = dict(i.split('=', 1) for i in (self.hello_text or '').split(';') if '=' in i)
        return not re.fullmatch(r'[0-9a-f]{32}', keys.get('cmds', ''))

    # -- cases -------------------------------------------------------------------------------
    def why_skip(self, case):
        if self.real and not case['real_device']:
            return 'not on a real device: %s' % case['why_not']
        if case.get('variant', 'v1') != ('pre_v1' if self.pre_v1 else 'v1'):
            return 'needs a %s device; this one is %s' % (case.get('variant', 'v1'), 'pre-v1' if self.pre_v1 else 'v1')
        for need in case.get('needs', []):
            if need in ('sim_control', 'sim_library') and not self.sim_control:
                return 'needs the simulator (%s)' % need
            if need.startswith('fixture:') and need[8:] not in self.fixtures:
                return 'needs fixture %s (--fixture %s=PATH)' % (need[8:], need[8:])
        return None

    def run_case(self, case):
        """(result, detail): PASS, FAIL or SKIP."""
        self.lines = []
        why = self.why_skip(case)
        if why:
            return 'SKIP', why
        self.ctx = Context(self.spec, self.data, self.fixtures)
        self.ctx.hello_text = self.hello_text
        link, given = None, case.get('given', {})
        try:
            if given.get('boot') == 'fresh':
                link = self.open()
                self.restart(link)
                link = self.open(hello=given.get('hello', True))
            else:
                link = self.open()
            for name, value in given.get('faults', {}).items():
                self.control('set %s %d' % (name, value))
            for f in given.get('files', []):
                self.write(link, resolve(f['path'], 'str', self.ctx), resolve(f['data'], 'bytes', self.ctx))
            for n, step in enumerate(case['steps']):
                self.log('# step %d' % (n + 1))
                try:
                    link = self.step(link, step) or link
                except Fail as error:
                    raise Fail('step %d: %s' % (n + 1, error))
            return 'PASS', '%d steps' % len(case['steps'])
        except Skip as error:
            return 'SKIP', str(error)
        except Unsafe as error:
            return 'FAIL', 'refused by the device guard (nothing sent): %s' % error
        except Fail as error:
            return 'FAIL', str(error)
        except OSError as error:
            return 'FAIL', 'port: %s' % error
        finally:
            if link:
                link.close()
            if given.get('faults'):
                for name in given['faults']:
                    try:
                        self.control('set %s %d' % (name, -1 if name == 'space' else 0))
                    except Fail, OSError:
                        pass

    def control(self, line):
        if not self.sim_control:
            raise Skip('needs the simulator control port')
        reply = self.target.control(line)
        if not reply.get('ok', True):
            raise Fail('simulator control %r: %s' % (line, reply))
        self.log('# sim %s' % line)

    def restart(self, link):
        body, _ = link.request(self.spec.type_of('device.reboot'), struct.pack('<I', 2), self.cap('device.reboot'))
        if struct.unpack_from('<I', body)[0]:
            raise Fail('device.reboot: %s' % self.spec.status_name(struct.unpack_from('<I', body)[0]))
        self.disconnect(link)

    def disconnect(self, link):
        if not self.target.port_gone(link, self.timing['gone_ms'] / 1000.0):
            raise Fail('the connection did not drop within %d ms' % self.timing['gone_ms'])
        link.close()
        self.log('# disconnected')

    def stage(self, link, body):
        chunk = self.spec.constants['put_chunk']
        self.expect_ok(
            link,
            'stage.begin',
            struct.pack('<II', len(body), zlib.crc32(body) & 0xFFFFFFFF) + hashlib.sha256(body).digest(),
        )
        for at in range(0, len(body), chunk):
            reply = self.expect_ok(link, 'stage.data', struct.pack('<I', at) + body[at : at + chunk])
            received = struct.unpack_from('<I', reply, 4)[0] if len(reply) >= 8 else None
            if received != at + len(body[at : at + chunk]):
                raise Fail('stage.data at %d: received %s' % (at, received))
        self.expect_ok(link, 'stage.end', b'')

    def write(self, link, path, body):
        self.stage(link, body)
        reply = self.expect_ok(link, 'file.write_staged', write_payload(path, body))
        written, crc = struct.unpack_from('<II', reply, 4)
        if (written, crc) != (len(body), zlib.crc32(body) & 0xFFFFFFFF):
            raise Fail('file.write_staged %s: written %d, read-back CRC %08x' % (path, written, crc))

    def expect_ok(self, link, name, payload):
        body, _ = link.request(self.spec.type_of(name), payload, self.cap(name))
        status = struct.unpack_from('<I', body)[0]
        if status:
            raise Fail('%s: %s, expected ok' % (name, self.spec.status_name(status)))
        return body

    def step(self, link, step):
        action = step_action(step, 'step')
        if action in ('send', 'send_type'):
            if 'repeat' in step:
                r = step['repeat']
                start, stop, by = (resolve(r[k], 'int', self.ctx) for k in ('from', 'to', 'step'))
                if by <= 0:
                    raise CaseError('repeat step must be above 0')
                for value in range(start, stop, by):
                    self.ctx.vars[r['var']] = value
                    self.send(link, step)
            else:
                self.send(link, step)
        elif action == 'each_type':
            keys = dict(i.split('=', 1) for i in self.hello_text.split(';') if '=' in i)
            listed = int(keys['cmds'], 16)
            for kind in range(0x80):
                if kind in self.spec.by_type or listed >> kind & 1:
                    continue
                body, _ = link.request(kind, b'', self.reply_cap)
                try:
                    self.check_reply(step['expect'], None, body)
                except Fail as error:
                    raise Fail('type 0x%02x: %s' % (kind, error))
        elif action == 'send_bytes':
            self.send_bytes(link, resolve(step['send_bytes'], 'bytes', self.ctx), [], step['expect'])
        elif action == 'stream':
            st = self.spec.streams[step['stream']]
            expect = dict(
                replies=[dict(type='0x%02x' % (int(f['type'], 16) | REPLY_BIT), seq=f['seq']) for f in st['frames']]
            )
            self.send_bytes(link, None, [bytes.fromhex(c) for c in st['chunks']], expect)
        elif action == 'stage':
            self.stage(link, resolve(step['stage'], 'bytes', self.ctx))
        elif action == 'write':
            self.write(
                link, resolve(step['write']['path'], 'str', self.ctx), resolve(step['write']['data'], 'bytes', self.ctx)
            )
        elif action == 'disconnect':
            self.disconnect(link)
        elif action == 'reconnect':
            link.close()
            return self.open(hello=step['reconnect'].get('hello', True), ready=step['reconnect'].get('ready', False))
        elif action == 'sim':
            link.close()
            self.control(step['sim'])
        return None

    def send(self, link, step):
        kind, payload, command = request_payload(step, self.ctx, 'step')
        name = command['name'] if command else None
        body, events = link.request(kind, payload, self.cap(name), step.get('seq'))
        self.check_reply(step['expect'], command, body, events)

    def send_bytes(self, link, data, chunks, expect):
        from xy_forever import usb_proto as P

        if data is not None:
            link.write(data)
        for chunk in chunks:
            link.write(chunk)
            pause(self.target, 0.005)  # separate reads on the device side
        if 'none_within_ms' in expect:
            on_sim = hasattr(self.target, 'pause')
            if on_sim:  # the simulator: its own time passes
                pause(self.target, expect['none_within_ms'] / 1000.0)
            got = link.quiet(50 if on_sim else expect['none_within_ms'])
            if got:
                raise Fail(
                    'expected no frame within %d ms, got %s'
                    % (expect['none_within_ms'], ', '.join('0x%02x seq %d' % (k, s) for k, s, _ in got))
                )
            return
        # A hello with a fresh seq after the bytes: every reply before its reply must be expected.
        barrier = 200 + link.next_seq() % 50
        link.write(P.encode(self.spec.type_of('hello'), barrier, b''))
        got, deadline = [], time.monotonic() + self.reply_cap
        while time.monotonic() < deadline and not any(k == 0x81 and s == barrier for k, s, _ in got):
            got += [f for f in link.read_frames(0.2) if f[0] != EVENT_LOG]
        if not got or got[-1][:2] != (0x81, barrier):
            raise Fail('no reply to the hello sent after the bytes')
        want = [(int(r['type'], 16), r['seq'], r.get('status')) for r in expect['replies']]
        have = [
            (k, s, self.spec.status_name(struct.unpack_from('<I', b)[0]) if len(b) >= 4 else None)
            for k, s, b in got[:-1]
        ]
        if [(k, s) for k, s, _ in have] != [(k, s) for k, s, _ in want] or any(
            w[2] and w[2] != h[2] for w, h in zip(want, have)
        ):
            raise Fail(
                'replies %s, expected %s'
                % (
                    ', '.join('0x%02x seq %d %s' % h for h in have) or 'none',
                    ', '.join('0x%02x seq %d %s' % (k, s, st or 'any') for k, s, st in want) or 'none',
                )
            )

    def check_reply(self, e, command, body, events=0):
        status = struct.unpack_from('<I', body)[0]
        name = self.spec.status_name(status)
        if 'status' in e:
            want = e['status'] if isinstance(e['status'], list) else [e['status']]
            if name not in want:
                raise Fail('%s, expected %s' % (name, ' or '.join(want)))
        if e.get('alone') and len(body) != 4:
            raise Fail('%s with %d bytes after the status, expected the status alone' % (name, len(body) - 4))
        values = (
            decode_fields(command, body) if command and (e.get('fields') or e.get('save') or e.get('hello')) else None
        )
        for field, expected in e.get('fields', {}).items():
            if values is None or field not in values:
                raise Fail('%s: the reply has no field %s' % (name, field))
            self.match(values[field], expected, field)
        for var, field in e.get('save', {}).items():
            if values is None or field not in values:
                raise Fail('cannot save %s: no field %s' % (var, field))
            self.ctx.vars[var] = values[field]
        if command and command['name'] == 'hello' and status == 0:
            self.hello_text = self.ctx.hello_text = body[4:].decode('ascii', 'replace')
        if 'hello' in e:
            self.check_hello(e['hello'], self.hello_text)
        if 'events' in e and events < resolve(e['events']['log']['min'], 'int', self.ctx):
            raise Fail('%d log events, expected at least %s' % (events, e['events']['log']['min']))

    def check_hello(self, h, text):
        items = text.split(';')
        if 'prefix' in h and items[0] != h['prefix']:
            raise Fail('hello starts %r, expected %r' % (items[0], h['prefix']))
        keys = dict(i.split('=', 1) for i in items[1:] if '=' in i)
        for key, expected in h.get('keys', {}).items():
            if key not in keys:
                raise Fail('hello has no key %s' % key)
            self.match(keys[key], expected, 'hello ' + key)
        for key in h.get('lacks', []):
            if key in keys:
                raise Fail('hello has key %s, expected none' % key)
        cmds = keys.get('cmds', '')
        pre_v1 = not re.fullmatch(r'[0-9a-f]{32}', cmds)
        if 'pre_v1' in h and pre_v1 != h['pre_v1']:
            raise Fail('hello is %s, expected %s' % ('pre-v1' if pre_v1 else 'v1', 'pre-v1' if h['pre_v1'] else 'v1'))
        for name in h.get('answers', []):
            if pre_v1 or not int(cmds, 16) >> self.spec.type_of(name) & 1:
                raise Fail('cmds does not list %s' % name)

    def match(self, actual, expected, what):
        kind = 'int' if isinstance(actual, int) else 'str' if isinstance(actual, str) else 'bytes'
        if isinstance(expected, dict) and form_of(expected) is None:
            for k, v in expected.items():
                if k in ('len', 'len_min', 'len_max'):
                    n, bound = len(actual), resolve(v, 'int', self.ctx)
                    if (k == 'len' and n != bound) or (k == 'len_min' and n < bound) or (k == 'len_max' and n > bound):
                        raise Fail('%s: length %d, expected %s %d' % (what, n, k, bound))
                elif k in ('min', 'max'):
                    bound = resolve(v, 'int', self.ctx)
                    if (k == 'min' and actual < bound) or (k == 'max' and actual > bound):
                        raise Fail('%s: %s, expected %s %d' % (what, actual, k, bound))
                elif k == 'matches':
                    if not re.search(v, actual):
                        raise Fail('%s: %r does not match %s' % (what, actual, v))
                elif k == 'one_of':
                    if actual not in [resolve(x, kind, self.ctx) for x in v]:
                        raise Fail('%s: %r is not one of %s' % (what, actual, v))
            return
        want = resolve(expected, kind, self.ctx, what)
        if actual != want:
            shown = (
                (lambda x: x[:32].hex() + ('... (%d B)' % len(x) if len(x) > 32 else '')) if kind == 'bytes' else repr
            )
            raise Fail('%s: %s, expected %s' % (what, shown(actual), shown(want)))
