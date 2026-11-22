// The mod package index from TypeScript (src/modindex.ts): the synthetic package set of
// tests/test_modsend.py, indexed by `xyf_package.py index build`, served by a local HTTP server;
// loadIndex + fetchPackages give a PackageSource that localSet (what pushPackages reads first) accepts and
// whose files are byte-identical to the generation folder; refusals for a changed archive, a traversing
// path, an unlisted member, a compressed member, an unknown id and a bad format.
// Needs the project venv (PYTHON: the test modules import pytest); no device.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cpSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join, relative, sep } from 'node:path';
import { deflateRawSync } from 'node:zlib';
import { afterAll, beforeAll, beforeEach, test } from 'vitest';
import { PackageError, fetchPackages, loadIndex, localSet } from '../src/index.ts';
import { PYTHON, REPO } from './simulators.ts';

const MAKE = `
import json, sys
from pathlib import Path
sys.path.insert(0, 'tests')
from xy_forever import modkit as M, modindex as I
from test_modpatch import finish, kit_for, profile_for, stock
w = Path(sys.argv[1])
(w / 'kit.json').write_text(json.dumps(kit_for(profile_for(stock()))))
gen = M.split_kit(w / 'kit.json', w / 'packages', finish=finish, sdk_source='https://github.com/example/sdk@abc')['root']
I.build(gen, w / 'index')
print(gen)
`;

function tree(dir: string): Map<string, Buffer> {
  const out = new Map<string, Buffer>();
  for (const name of readdirSync(dir, { recursive: true }) as string[]) {
    const path = join(dir, name);
    if (statSync(path).isFile()) out.set(relative(dir, path).split(sep).join('/'), readFileSync(path));
  }
  return out;
}

const work = mkdtempSync(join(tmpdir(), 'xyf-modindex-'));
const served = join(work, 'served');
let gen = '';
let url = '';
const server = createServer((req, res) => {
  try {
    const path = join(served, decodeURIComponent(new URL(req.url ?? '/', 'http://x').pathname));
    res.end(readFileSync(path));
  } catch {
    res.statusCode = 404;
    res.end();
  }
});

beforeAll(async () => {
  const run = spawnSync(PYTHON, ['-c', MAKE, work], { cwd: REPO, encoding: 'utf-8' });
  assert.equal(run.status, 0, run.stderr);
  gen = run.stdout.trim();
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  url = `http://127.0.0.1:${(server.address() as AddressInfo).port}/`;
});
afterAll(() => {
  server.close();
  rmSync(work, { recursive: true, force: true });
});

let index: Record<string, any> = {};
beforeEach(() => {
  rmSync(served, { recursive: true, force: true });
  cpSync(join(work, 'index'), served, { recursive: true });
  index = JSON.parse(readFileSync(join(served, 'index.json'), 'utf-8'));
});
const save = (index: unknown): void => writeFileSync(join(served, 'index.json'), JSON.stringify(index));
const pkg = (index: any, id = 'alpha'): any => index.profiles[0].generations[0].packages.find((p: any) => p.id === id);
async function refused(text: string, ids: string[] = []): Promise<void> {
  await assert.rejects(
    async () => fetchPackages(await loadIndex(url), ids),
    (e: unknown) =>
      e instanceof PackageError && e.message.includes(text) ? true : assert.fail(`${String(e)} lacks ${text}`),
  );
}

/** A STORED (or deflated) zip of members, written over pkg's archive with the index's size and hash updated. */
function rearchive(index: any, members: [string, Uint8Array][], deflate = false): void {
  const locals: Buffer[] = [];
  const central: Buffer[] = [];
  let at = 0;
  for (const [name, body] of members) {
    const n = Buffer.from(name);
    const data = deflate ? deflateRawSync(body) : Buffer.from(body);
    const head = Buffer.alloc(30);
    head.writeUInt32LE(0x04034b50, 0);
    head.writeUInt16LE(deflate ? 8 : 0, 8);
    head.writeUInt32LE(data.length, 18);
    head.writeUInt32LE(body.length, 22);
    head.writeUInt16LE(n.length, 26);
    const c = Buffer.alloc(46);
    c.writeUInt32LE(0x02014b50, 0);
    c.writeUInt16LE(deflate ? 8 : 0, 10);
    c.writeUInt32LE(data.length, 20);
    c.writeUInt32LE(body.length, 24);
    c.writeUInt16LE(n.length, 28);
    c.writeUInt32LE(at, 42);
    locals.push(head, n, data);
    central.push(c, n);
    at += 30 + n.length + data.length;
  }
  const cd = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(members.length, 8);
  end.writeUInt16LE(members.length, 10);
  end.writeUInt32LE(cd.length, 12);
  end.writeUInt32LE(at, 16);
  const zip = Buffer.concat([...locals, cd, end]);
  const p = pkg(index);
  writeFileSync(join(served, p.archive.name), zip);
  p.archive.size = zip.length;
  p.archive.sha256 = createHash('sha256').update(zip).digest('hex');
  save(index);
}
const alpha = (): [string, Uint8Array][] => [...tree(join(gen, 'alpha'))].map(([p, b]) => [p, new Uint8Array(b)]);

test('the whole set and --only, byte for byte; localSet accepts it (pushPackages starts there)', async () => {
  const want = tree(gen);
  const loaded = await loadIndex(url);
  for (const ids of [[], ['beta']]) {
    const source = await fetchPackages(loaded, ids);
    const paths = [...(await source.paths())].sort();
    const expect = [...want.keys()].filter((p) => !ids.length || ['base', ...ids].includes(p.split('/')[0]!)).sort();
    assert.deepEqual(paths, expect);
    for (const p of paths) assert.deepEqual(Buffer.from(await source.read(p)), want.get(p), p);
    const set = await localSet(source);
    assert.equal(set.packages.size, ids.length ? 2 : readdirSync(gen).length);
  }
  assert.equal(
    (await fetchPackages(await loadIndex(`${url}index.json`), ['gamma'])).generation,
    loaded.profiles[0]!.generations[0]!.generation,
  );
});

const spoil = (change: (index: any) => void) => () => {
  change(index);
  save(index);
};
test.each<[string, () => void, string[]?]>([
  ['archive differs from the index', spoil((i) => (pkg(i).archive.sha256 = '0'.repeat(64)))],
  ['unusable path', spoil((i) => (pkg(i).files[0].path = '../escape'))],
  ['unusable path', spoil((i) => (pkg(i).files[0].path = '/etc/passwd'))],
  ['path too long', spoil((i) => (pkg(i).files[0].path = 'n/'.repeat(120) + 'x'))],
  ['members not in the index: ../evil', () => rearchive(index, [...alpha(), ['../evil', new Uint8Array([1])]])],
  ['compressed member', () => rearchive(index, alpha(), true)],
  ['oversize size', spoil((i) => (pkg(i).files[0].size = (32 << 20) + 1))],
  ['not in the index: nosuch', () => undefined, ['nosuch']],
  ['not a xyf-mod-index/1 index', spoil((i) => (i['format'] = 'xyf-mod-index/9'))],
])('refuses: %s', async (text, change, ids = []) => {
  change();
  await refused(text, ids);
});
