"""XY Forever USB service frames (host side of target/usb_proto.hpp).

Frame (little endian): b'XF' | type u8 | seq u8 | length u32 | payload | crc32 u32
(zlib CRC over every byte before it). Replies use type | 0x80 and start with a
u32 status.
"""
import struct
import zlib

from xy_forever import usb_proto_ids as ids
# Numbers: generated from protocol/v1.json (scripts/protogen.py; docs/PROTOCOL.md).
from xy_forever.usb_proto_ids import (  # noqa: F401  (re-exported for the host tools)
    HELLO, STATUS, READ_FILE, SCREEN, PEEK, MTP_TEST, PUT_BEGIN, PUT_DATA, PUT_END, FILE_WRITE, INSTALL,
    UNINSTALL, REBOOT, CONTINUE, HOLD, KEY, SET_DEFAULT, LOG, REPLY, MAX_PAYLOAD, MAX_DATA,
    LIST_DIR, DELETE_FILE, REMOVE_DIR, MAKE_DIR, RENAME_FILE, COPY_FILE,
    STATUS_NAMES)
OK, STATE = ids.S_OK, ids.S_STATE
# Stock 1.2.1 os.ldr (dev-menu restore image): the only image UNINSTALL accepts.
STOCK_OS_LDR_BYTES = 2373336
STOCK_OS_LDR_SHA256 = '60358afdaa9b58ed40411602f6d5d4e1e3e02553ff2da7e25038e71dd002b52f'


def status_name(code):
    return STATUS_NAMES[code] if code < len(STATUS_NAMES) else 'status_%d' % code


def answered(switches):
    """The hello cmds bitmap of a build compiled with these build switches (protocol/v1.json)."""
    return sum(1 << t for t, c in ids.COMMANDS.items() if c['switch'] is None or c['switch'] in switches)


STAGE2_LOADED = 'XYF_STAGE2_LOADED'      # the run-time switch of stage2.info


def hello_text(version, image, mode, switches, stage2=0):
    """The hello text such a build sends (the device composes it from usb_proto_ids.hpp): version its release
    version, image its 8 hex digits (None: `unknown`, as compiled); stage2: the loaded stage 2's version (0:
    none; then stage2.info is not in cmds)."""
    bits = answered(set(switches) | ({STAGE2_LOADED} if stage2 else set()))
    dev = any(bits >> t & 1 for t, c in ids.COMMANDS.items() if c['cls'] == 'dev')
    values = dict(release=version, image=image or 'unknown', mode=mode, cmds='%032x' % bits, dev='1' if dev else None,
                  stage2=str(stage2))
    items = [ids.HELLO_PREFIX]
    for name, _, source, text in ids.HELLO_KEYS:
        value = text if source == 'fixed' else values[source]
        if value is not None:
            items.append('%s=%s' % (name, value))
    return ';'.join(items)


class Hello:
    """A hello reply's text. A device without `cmds` is pre-v1 (XY Forever build 153 and earlier):
    it speaks 1.0 and is assumed to answer every 1.0 command."""

    def __init__(self, text):
        self.text = text
        self.keys = dict(item.split('=', 1) for item in text.split(';') if '=' in item)
        self.proto = self.keys.get('proto', '1')             # missing keys: a pre-v1 device
        self.major = self.proto.split('.')[0]
        cmds = self.keys.get('cmds', '')
        self.cmds = int(cmds, 16) if len(cmds) == 32 and all(c in '0123456789abcdef' for c in cmds) else None

    @property
    def pre_v1(self):
        return self.cmds is None

    def answers(self, kind):
        return self.pre_v1 or bool(self.cmds >> kind & 1)

    def describe(self):
        if self.pre_v1:
            return 'protocol %s: pre-v1 device (XY Forever build 153 or earlier; no command list)' % self.proto
        if 'version' not in self.keys:                       # 1.1..1.14: a build number
            return 'protocol %s, build %s (%s)' % (self.proto, self.keys.get('build', '?'), self.keys.get('mode', '?'))
        return 'protocol %s, version %s, image %s (%s)' % (self.proto, self.keys['version'], self.keys.get('image', '?'),
                                                         self.keys.get('mode', '?'))


def encode(kind, seq, payload=b''):
    if len(payload) > MAX_PAYLOAD:
        raise ValueError('payload too long')
    head = b'XF' + bytes((kind & 0xff, seq & 0xff)) + struct.pack('<I', len(payload)) + payload
    return head + struct.pack('<I', zlib.crc32(head) & 0xffffffff)


INTS = {'u8': '<B', 'u16': '<H', 'u32': '<I'}


def pack(fields, values):
    """Payload for a field table (usb_proto_ids COMMANDS request/reply, EVENTS payload; a reply's
    status word is not in it) and {name: value}: int for u8/u16/u32, bytes for bytes/rest, str
    for cstr/text (in the field's encoding). Optional fields are all given or all left out."""
    optional = [f[0] for f in fields if f[3]]
    given = [name for name in optional if name in values]
    if given and given != optional:
        raise ValueError('optional fields are all given or none: %s' % ', '.join(optional))
    out = b''
    for name, kind, size, optional, encoding in fields:
        if optional and not given:
            break
        value = values[name]
        if kind in INTS:
            out += struct.pack(INTS[kind], value)
        elif kind == 'bytes':
            if len(value) != size:
                raise ValueError('%s: %d bytes, not %d' % (name, len(value), size))
            out += bytes(value)
        elif kind in ('cstr', 'text'):
            text = value.encode(encoding)
            if b'\0' in text:
                raise ValueError('%s: NUL in text' % name)
            out += text + (b'\0' if kind == 'cstr' else b'')
        else:
            out += bytes(value)
    return out


def unpack(fields, payload, exact=True):
    """{name: value} of a payload (the inverse of pack); ValueError if it does not fit. exact: a
    request (no bytes after the fields); exact=False for replies and events, whose bytes after the
    known fields are ignored (a later 1.x may add fields at the end)."""
    values, at = {}, 0
    for name, kind, size, optional, encoding in fields:
        if optional and at == len(payload) and not any(f[3] and f[0] in values for f in fields):
            break                                       # optional fields: all present or none
        if kind in INTS or kind == 'bytes':
            size = struct.calcsize(INTS[kind]) if kind in INTS else size
            if at + size > len(payload):
                raise ValueError('%s: payload too short' % name)
            values[name] = struct.unpack_from(INTS[kind], payload, at)[0] if kind in INTS else payload[at:at + size]
            at += size
        elif kind == 'cstr':
            end = payload.find(b'\0', at)
            if end < 0:
                raise ValueError('%s: no NUL' % name)
            values[name], at = payload[at:end].decode(encoding), end + 1
        else:
            rest = payload[at:]
            values[name], at = (rest.decode(encoding) if kind == 'text' else rest), len(payload)
    if exact and at != len(payload):
        raise ValueError('%d bytes after the last field' % (len(payload) - at))
    return values


def decode_reply(kind, payload):
    """(status, {name: value} or None) of a reply payload to request type kind: the fields its
    on_error names for an error, None for the status alone (the common statuses come so)."""
    if len(payload) < 4:
        raise ValueError('reply without a status')
    command, status, body = ids.COMMANDS[kind], struct.unpack_from('<I', payload)[0], payload[4:]
    fields, on_error = command['reply'], command['reply_on_error']
    if status != OK:
        if not body or on_error == 'status':
            return status, None
        fields = fields if on_error == 'full' else tuple(f for f in fields if f[0] in on_error)
    return status, unpack(fields, body, exact=False)


class Parser:
    """Incremental decoder with resynchronisation (same algorithm as the device:
    iterative, drops one byte per failed check)."""

    def __init__(self):
        self.buffer = bytearray()
        self.dropped = 0
        self.bad_crc = 0

    def _settle(self):
        b = self.buffer
        while True:
            if len(b) >= 1 and b[0] != 0x58:
                del b[0]; self.dropped += 1; continue
            if len(b) >= 2 and b[1] != 0x46:
                del b[0]; self.dropped += 1; continue
            if len(b) < 8:
                return None
            length = struct.unpack_from('<I', b, 4)[0]
            if length > MAX_PAYLOAD:
                del b[0]; self.dropped += 1; continue
            total = 12 + length
            if len(b) < total:
                return None
            body = bytes(b[:total - 4])
            if struct.unpack_from('<I', b, total - 4)[0] == zlib.crc32(body) & 0xffffffff:
                del b[:total]
                return (body[2], body[3], body[8:])
            self.bad_crc += 1
            del b[0]

    def feed(self, data):
        """Returns a list of (type, seq, payload) for every complete valid frame."""
        frames = []
        for byte in data:
            self.buffer.append(byte)
            frame = self._settle()
            while frame:
                frames.append(frame)
                frame = self._settle()
        return frames

    def reset(self):
        """Drop a partial frame (the host calls this after a reply timeout)."""
        self.buffer.clear()
