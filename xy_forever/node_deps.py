"""Whether Node and each TypeScript folder's node_modules (`npm ci`) are ready: setup.py doctor's node rows.
check.py runs `npm test` in clients/ts and `npm test` plus `npm run typecheck` in web/ (which uses clients/ts)."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NEEDS = (('clients/ts', ('tsc',)), ('web', ('tsc', 'vitest')))   # folder, the node_modules/.bin tools it needs


def doctor_rows(root=ROOT):
    """[(label, ok, detail)]: one row per folder, ok when Node is found and its tools are installed."""
    node = shutil.which('node')
    version = subprocess.run([node, '--version'], capture_output=True, text=True).stdout.strip() if node else None
    rows = []
    for rel, tools in NEEDS:
        ready = all((root / rel / 'node_modules/.bin' / tool).exists() for tool in tools)
        rows.append(('node (%s)' % rel, bool(node and ready), '%s; %s' % (
            version or 'not found (Node 22.18+ or 23.6+)', 'node_modules ok' if ready else 'run `npm ci` in ' + rel)))
    return rows
