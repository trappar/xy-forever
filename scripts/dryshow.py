import json
import sys

t = open(sys.argv[1]).read()
t = t[t.index('{') :]
d = json.loads(t)
u = d['usb_service']
f = d.get('dry_run_file_boot') or {}
print('  service:', {k: u.get(k) for k in ('present', 'crash_restarts')}, d.get('blocks'))
if f:
    g = f.get('diagnostic', {})
    print(
        '  dry run:',
        {
            k: f.get(k)
            for k in (
                'outcome',
                'committed',
                'declined',
                'load_reason',
                'admit_reason',
                'received_bytes',
                'copy_crc',
                'admit_ms',
                'end_ms',
            )
        },
    )
    print(
        '     link:',
        {k: g.get(k) for k in ('stage', 'result', 'requests', 'responses', 'last_operation', 'elapsed_ms')},
    )
else:
    print('  dry run: no block')
