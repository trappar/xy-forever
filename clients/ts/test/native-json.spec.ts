import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { nativePackageText, nativeConfigText, nativePackageDocument, nativeConfigDocument } from '../src/native-json.ts';
import { REPO } from './simulators.ts';
import { readModsConfig } from '../src/mods-config.ts';
import type { Client } from '../src/client.ts';

test('native JSON agrees with the shared C++ and Python schema fixtures', () => {
  const cases = JSON.parse(readFileSync(join(REPO, 'tests/native-json.json'), 'utf8')) as
    { name: string; kind: string; source: string; text: string | null; canonical?: boolean }[];
  const encoder = new TextEncoder(), decoder = new TextDecoder();
  for (const item of cases) {
    const read = (): string => decoder.decode((item.kind === 'config' ? nativeConfigText : nativePackageText)(encoder.encode(item.source)));
    if (item.text === null) expect(read, item.name).toThrow();
    else {
      expect(read(), item.name).toBe(item.text);
      if (item.canonical) {
        const fields = Object.fromEntries(item.text.trimEnd().split('\n').map((line) => line.split('=')));
        expect(decoder.decode(nativeConfigDocument(fields))).toBe(`${item.source}\n`);
      }
    }
  }
});
test('native JSON writers use lists, numbers, null, booleans and a structured build', () => {
  const decoder = new TextDecoder();
  const manifest = nativePackageDocument({ type: 'mod', name: 'Test', machine: 'synth', generation: 'v1',
    module_base: '0x8d2d0380', services: 'synth,delay', engine: '-', catalog_number: '11,12' });
  const packageRoot = JSON.parse(decoder.decode(manifest)) as Record<string, unknown>;
  expect(packageRoot['services']).toEqual(['synth', 'delay']);
  expect(packageRoot['module_base']).toBe(0x8d2d0380);
  expect(packageRoot['engine']).toBeNull();
  expect(decoder.decode(nativePackageText(manifest))).toContain('catalog_number=11,12\n');
  const config = nativeConfigDocument({ base: 'stock', profile: 'p', generation: 'v1', memory: 'expanded',
    unverified: '1', mods: 'a,ext-b', slot: 'mods-a', stamp: 'a'.repeat(64), built: 'stock v1 00001234 2 expanded' });
  const configRoot = JSON.parse(decoder.decode(config)) as { desired: Record<string, unknown>; built: Record<string, unknown> };
  expect(configRoot.desired['unverified']).toBe(true);
  expect(configRoot.desired['mods']).toEqual(['a', 'ext-b']);
  expect(configRoot.built['set']).toBe(0x1234);
  expect(decoder.decode(nativeConfigText(config))).toContain('built=stock v1 00001234 2 expanded\n');
});

test('saved config uses the first literal record name and reports errors on that name', async () => {
  const native = nativeConfigDocument({ base: 'stock', safe: '1', mods: 'alpha,missing' });
  const legacy = new TextEncoder().encode('base=other\nmods=beta\n');
  const bad = new TextEncoder().encode('{');
  let records: Record<string, Uint8Array | null> = {};
  const client = {
    info: { limits: { read: 37 } },
    listDir: async () => Object.keys(records).map((name) => ({ name })),
    readRange: async (path: string, offset: number, length: number) => {
      const data = records[path.split('/').at(-1)!];
      if (data == null) throw new Error('entry unreadable');
      return data.slice(offset, offset + length);
    },
  } as unknown as Client;
  records = { 'config.json': native, 'config.json.old': bad, 'config.txt': legacy };
  expect(await readModsConfig(client)).toContain('safe=1\nmods=alpha,missing\n');
  records = { 'config.json.part': bad, 'config.json.old': native, 'config.txt': legacy };
  expect(await readModsConfig(client)).toContain('safe=1\nmods=alpha,missing\n');
  records = { 'config.json.part': bad, 'config.old': legacy };
  expect(await readModsConfig(client)).toContain('mods=beta');
  records = { 'config.json.part': bad };
  expect(await readModsConfig(client)).toBeNull();
  records = { 'config.json': bad, 'config.txt': legacy };
  await expect(readModsConfig(client)).rejects.toThrow('config.json:');
  records = { 'config.json.old': null, 'config.txt': legacy };
  await expect(readModsConfig(client)).rejects.toThrow('config.json.old: entry unreadable');
  records = { 'config.txt': new TextEncoder().encode('format=2\n'), 'config.old': legacy };
  await expect(readModsConfig(client)).rejects.toThrow('config.txt: unsupported legacy configuration format');
});
