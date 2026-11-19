import { spawnSync } from 'node:child_process';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { parseManifest, parseFileList } from '../src/package-manifest.ts';
import { REPO } from './simulators.ts';

test('strict browser admission agrees with the Python/native manifest fixtures', () => {
  const folder = join(REPO, 'tests/manifests');
  const cases = readdirSync(folder).map((name) => readFileSync(join(folder, name)));
  const valid = new TextEncoder().encode('type=mod\nname=Test\nmachine=synth\ngeneration=v1\n');
  cases.push(Buffer.from(valid), Buffer.from(valid.slice(0, -1)), Buffer.from('type=companion\n'),
    Buffer.from('type=mod\nname=Test\nmachine=unknown\ngeneration=v1\n'),
    Buffer.from('type=mod\nname=Test\nmachine=synth\ngeneration=../v1\n'),
    Buffer.from('type=mod\nname=Test\nname=Again\nmachine=synth\ngeneration=v1\n'));
  const python = spawnSync('python3', ['-c',
    'import json,sys; from xy_forever.xyf_entry import parse_manifest\n' +
    'def read(s):\n try: return {k:str(v) for k,v in parse_manifest(bytes.fromhex(s)).items()}\n except ValueError: return None\n' +
    'print(json.dumps([read(s) for s in json.load(sys.stdin)]))'],
    { cwd: REPO, input: JSON.stringify(cases.map((data) => data.toString('hex'))), encoding: 'utf8' });
  expect(python.status, python.stderr).toBe(0);
  const expected = JSON.parse(python.stdout) as (Record<string, string> | null)[];
  cases.forEach((data, index) => {
    if (expected[index] === null) expect(() => parseManifest(data)).toThrow();
    else expect(parseManifest(data)).toEqual(expected[index]);
  });
});

test('browser file lists preserve sorting, 160-byte paths and strict termination', () => {
  const sha = 'a'.repeat(64), encode = (text: string): Uint8Array => new TextEncoder().encode(text);
  expect(parseFileList(encode(`${sha}  ${'x'.repeat(160)}\n`))).toHaveLength(1);
  for (const text of [`${sha}  ${'x'.repeat(161)}\n`, `${sha}  x`, `${sha}  z\n${sha}  a\n`,
    `${sha}  x\n${sha}  x\n`, `${sha}  ../x\n`, `${sha}  bootloader.dev\n`, `${sha}  files.sha256\n`]) {
    expect(() => parseFileList(encode(text))).toThrow();
  }
});
