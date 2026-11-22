"""Legacy B and versioned per-processor firmware check CLI."""

import json
import struct

from . import usb_proto as P

REASONS = ('pass', 'read', 'not_package', 'key_index', 'no_otp', 'public_key', 'signature', 'decrypt', 'directory')


def configure(sub):
    parser = sub.add_parser('tfw-check', help="check an official firmware cut with the device's own OTP keys")
    parser.add_argument('path')
    parser.add_argument('--role', choices=('a', 'b'), help='select processor; default: legacy B check')


def command(dev, args, timeout):
    kind = P.ids.TFW_CHECK_ROLE if args.role else P.ids.TFW_CHECK
    payload = (struct.pack('<II', 1, int(args.role == 'b')) if args.role else b'') + args.path.encode('utf-8') + b'\0'
    code, body = dev.call(kind, payload, timeout=timeout)
    _, fields = P.decode_reply(kind, struct.pack('<I', code) + body)
    if args.role and not code and (fields.get('version'), fields.get('role')) != (1, int(args.role == 'b')):
        raise ValueError('tfw.check_role: unsupported reply version or wrong processor')
    report = dict(status=P.status_name(code))
    if args.role:
        report['role'] = args.role
    if fields:
        reason = fields['reason']
        report.update(
            verdict='pass' if fields['verdict'] == 0 else 'fail',
            reason=REASONS[reason] if reason < len(REASONS) else 'reason_%d' % reason,
            public_key_sha256=fields['public_key_sha256'].hex(),
        )
    print(json.dumps(report))
    return code or (0 if report['verdict'] == 'pass' else 1)
