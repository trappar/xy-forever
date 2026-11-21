// Strict package admission, matching xyf_entry/xyf_manifest. Boot reads remain lenient.
import { LEGACY_MANIFEST, packageText } from './package-records.ts';
const decoder = new TextDecoder('ascii');
const keys = ['type', 'name', 'version', 'kind', 'bytes', 'crc32', 'sha256', 'content', 'needs_os',
  'generation', 'profile', 'machine', 'source', 'licence', 'machines'];
const firmware = ['name', 'version', 'kind', 'bytes', 'crc32', 'sha256', 'content', 'needs_os', 'generation', 'profile', 'source', 'licence', 'machines'];
const allowed: Record<string, readonly string[]> = { '': firmware, firmware,
  'mod-base': ['profile', 'generation', 'needs_os', 'name', 'version', 'source', 'licence', 'machines'],
  mod: ['name', 'machine', 'generation', 'version', 'profile', 'source', 'licence'] };
const required: Record<string, readonly string[]> = { '': [], firmware: ['name', 'bytes', 'crc32', 'sha256'],
  'mod-base': ['profile', 'generation', 'needs_os'], mod: ['name', 'machine', 'generation'] };
const capacities: Record<string, number> = { name: 47, version: 31, content: 31, generation: 31, profile: 31, source: 127, licence: 63 };
const fail = (why: string): never => { throw new Error(`package manifest: ${why}`); };

export function parseManifest(data: Uint8Array, name = LEGACY_MANIFEST): Record<string, string> {
  data = packageText(data, name);
  if (!data.length || data.length > 4096 || data.at(-1) !== 10) fail('empty, oversized or cut text');
  const fields: Record<string, string> = {}, seen = new Set<string>();
  for (let line of decoder.decode(data).slice(0, -1).split('\n')) {
    if (line.endsWith('\r')) line = line.slice(0, -1);
    if (line.length > 255 || /[^\x20-\x7e\t]/.test(line)) fail('invalid line');
    line = line.trimStart();
    if (!line || line.startsWith('#')) continue;
    const match = /^([a-z0-9_]{1,31})[ \t]*=[ \t]*(.*?)[ \t]*$/.exec(line);
    if (!match) fail('invalid key/value');
    const key = match![1]!, value = match![2]!;
    if (seen.has(key) || seen.size === 32 || value.length > 127 || value.includes('\t')) fail('duplicate, oversized or invalid value');
    seen.add(key);
    if (!keys.includes(key)) continue;
    if (!value || value.length > (capacities[key] ?? 127)) fail(`invalid ${key}`);
    if (key === 'type' && !['firmware', 'mod-base', 'mod'].includes(value)) fail('unsupported type');
    if (key === 'kind' && !['official', 'open-xy', 'custom'].includes(value)) fail('unknown kind');
    if (key === 'machine' && !['synth', 'drum', 'sample', 'effect', 'player', 'extension'].includes(value)) fail('unknown machine');
    if (['generation', 'profile'].includes(key) && !/^[a-z0-9][a-z0-9._-]*$/.test(value)) fail('invalid path token');
    if (['bytes', 'machines'].includes(key) && (!/^(0|[1-9][0-9]{0,9})$/.test(value) || Number(value) >= (key === 'bytes' ? 2 ** 32 : 65536))) fail('invalid number');
    if (['crc32', 'sha256', 'needs_os'].includes(key) && !new RegExp(`^[0-9a-f]{${key === 'crc32' ? 8 : 64}}$`).test(value)) fail('invalid hash');
    fields[key] = value;
  }
  if (!seen.size) fail('empty');
  const type = fields['type'] ?? '';
  if (Object.keys(fields).some((key) => key !== 'type' && !allowed[type]!.includes(key))) fail('key is not allowed for this type');
  const needs = [...required[type]!];
  if (['', 'firmware'].includes(type) && (fields['generation'] || fields['needs_os'])) needs.push('generation', 'needs_os');
  if (needs.some((key) => !fields[key])) fail('missing required key');
  return fields;
}

export function parseFileList(data: Uint8Array): [string, string][] {
  if (!data.length || data.at(-1) !== 10) fail('empty or cut file list');
  const lines = decoder.decode(data).slice(0, -1).split('\n');
  if (lines.length > 4096) fail('too many files');
  let previous = '';
  return lines.map((line) => {
    const match = /^([0-9a-f]{64}) [ *]([\x20-\x7e]{1,160})$/.exec(line);
    if (!match) fail('invalid file list line');
    const sha = match![1]!, path = match![2]!;
    if (path <= previous || /[\\:]/.test(path) || /bootloader/i.test(path) || path === 'files.sha256' ||
        path.split('/').some((part) => !part || /[. ]$/.test(part))) fail('unsafe, repeated or unsorted path');
    previous = path;
    return [sha, path];
  });
}
