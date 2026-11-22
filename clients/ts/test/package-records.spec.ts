import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import type { Client } from '../src/client.ts';
import { sha256 } from '../src/client.ts';
import { XyfError } from '../src/errors.ts';
import { infoValues, localSet } from '../src/mods.ts';
import { nativePackageDocument } from '../src/native-json.ts';
import { parseManifest } from '../src/package-manifest.ts';
import { readLibraryEntry, recordName } from '../src/package-records.ts';
import { REPO } from './simulators.ts';

const encode = (text: string): Uint8Array => new TextEncoder().encode(text);
const hex = (data: Uint8Array): string => [...data].map((n) => n.toString(16).padStart(2, '0')).join('');

test('host metadata selection and admission share native and legacy fixtures', () => {
  const cases = JSON.parse(readFileSync(join(REPO, 'tests/package-records.json'), 'utf8')) as {
    name: string;
    files: Record<string, string>;
    record: string;
    fields: Record<string, string> | null;
  }[];
  for (const item of cases) {
    const name = recordName(Object.keys(item.files));
    expect(name, item.name).toBe(item.record);
    const data = encode(item.files[name!]!),
      before = data.slice();
    if (item.fields === null) expect(() => parseManifest(data, name), item.name).toThrow();
    else expect(parseManifest(data, name), item.name).toEqual(item.fields);
    expect(data).toEqual(before);
  }
  expect(recordName([])).toBeUndefined();
});

test('native link and patch fields preserve their typed semantics in the host view', () => {
  const data = nativePackageDocument({
    type: 'mod',
    name: 'New',
    machine: 'synth',
    generation: 'v1',
    module_base: '0x8d2d0380',
    order: '2',
    services: 'synth,delay',
    engine: '-',
    catalog_number: '11,12',
  });
  expect(infoValues(data, 'manifest.json')).toMatchObject({
    module_base: '0x8d2d0380',
    order: '2',
    services: 'synth,delay',
    engine: '-',
    catalog_number: '11,12',
  });
});

test('package input hashes selected raw JSON and refuses unchecked overrides', async () => {
  const base = nativePackageDocument({ type: 'mod-base', profile: 'p', generation: 'v1', needs_os: 'a'.repeat(64) });
  const mod = nativePackageDocument({ type: 'mod', name: 'New', machine: 'synth', generation: 'v1' });
  const files = new Map<string, Uint8Array>([
    ['base/manifest.json', base],
    ['alpha/manifest.json', mod],
  ]);
  const refresh = async (name: string): Promise<void> => {
    const entries = [...files].filter(([path]) => path.startsWith(`${name}/`) && !path.endsWith('/files.sha256'));
    const lines = await Promise.all(
      entries
        .sort(([a], [b]) => (a < b ? -1 : 1))
        .map(async ([path, data]) => `${hex(await sha256(data))}  ${path.slice(name.length + 1)}\n`),
    );
    files.set(`${name}/files.sha256`, encode(lines.join('')));
  };
  const source = {
    paths: () => [...files.keys()],
    read: async (path: string): Promise<Uint8Array> => files.get(path)!,
  };
  await refresh('base');
  await refresh('alpha');
  const set = await localSet(source);
  expect([set.profile, set.generation]).toEqual(['p', 'v1']);
  expect(set.packages.get('alpha')!.map((file) => file.path)).toEqual(['manifest.json', 'files.sha256']);
  files.set('alpha/info.txt', encode('type=mod\nname=Old\nmachine=synth\ngeneration=v1\n'));
  await refresh('alpha');
  expect(
    infoValues(
      (await localSet(source)).packages.get('alpha')!.find((file) => file.path === 'manifest.json')!.data,
      'manifest.json',
    )['name'],
  ).toBe('New');
  files.delete('alpha/manifest.json');
  await refresh('alpha');
  files.set('alpha/manifest.json', mod);
  await expect(localSet(source)).rejects.toThrow('does not list manifest.json');
  await refresh('alpha');
  files.set('alpha/manifest.json', encode('{'));
  await expect(localSet(source)).rejects.toThrow('differs from its files.sha256');
  await refresh('alpha');
  await expect(localSet(source)).rejects.toThrow();
});

test('library JSON fallback happens only when the native file is absent', async () => {
  const paths: string[] = [];
  let native: Uint8Array | Error = new XyfError('not_found', 'absent');
  const client = {
    readFile: async (path: string): Promise<Uint8Array> => {
      paths.push(path);
      if (path.endsWith('manifest.json')) {
        if (native instanceof Error) throw native;
        return native;
      }
      return encode(' name = Old \nkind=custom\n');
    },
  } as Client;
  expect(await readLibraryEntry(client, 'entry', {})).toEqual({ name: 'Old', kind: 'custom' });
  expect(paths).toHaveLength(2);
  paths.length = 0;
  native = nativePackageDocument({ type: 'firmware', name: 'New' });
  expect((await readLibraryEntry(client, 'entry', {}))['name']).toBe('New');
  expect(paths).toHaveLength(1);
  for (const failure of [
    encode('{'),
    encode('{"schema":"xy-forever.package","schema_version":2}'),
    new XyfError('storage', 'read error'),
  ]) {
    native = failure;
    paths.length = 0;
    await expect(readLibraryEntry(client, 'entry', {})).rejects.toThrow();
    expect(paths).toHaveLength(1);
  }
});
