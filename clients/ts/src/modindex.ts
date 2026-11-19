// The local mod package index (xy_forever/modindex.py, `xyf_package.py index build`), read the way
// `xyf_host.py mods fetch` reads it: loadIndex(url) checks index.json (format xyf-mod-index/1);
// fetchPackages(index, ids) downloads the base and the chosen packages, checks every size and SHA-256 and
// returns an in-memory PackageSource that pushPackages accepts (the generation folder, byte-identical to
// the one the index was built from). Archives are ZIP files with STORED members only; anything else is
// refused: unknown format, bad ids, absolute, traversing or over-long paths, members not in the index,
// duplicate or compressed members, sizes over the limits, a hash that differs. Nothing is published; the
// index's licence and source fields are placeholders (no redistribution decision is made).
import { recordName } from './package-records.ts';
import { sha256 } from './client.ts';
import { MODS_ROOT, PackageError, listPaths } from './mods.ts';
import type { PackageSource } from './mods.ts';

export const INDEX_FORMAT = 'xyf-mod-index/1';
const ID = /^[a-z0-9][a-z0-9._-]{0,30}$/;
const TOKEN = ID;
const SHA = /^[0-9a-f]{64}$/;
const MAX_PATH = 239;
const MAX_INDEX = 16 << 20;
const MAX_FILE = 32 << 20;
const MAX_ARCHIVE = 256 << 20;
const MAX_FILES = 4096;
const KINDS = ['base', 'machine', 'extension'];

export interface IndexFile { readonly path: string; readonly size: number; readonly sha256: string }
export interface IndexPackage {
  readonly id: string; readonly name: string; readonly kind: 'base' | 'machine' | 'extension';
  readonly machine: string; readonly version: string;
  readonly memory: { readonly code: number; readonly data: number; readonly state: number; readonly total: number };
  readonly content_bytes: number; readonly files: readonly IndexFile[];
  readonly archive: { readonly name: string; readonly size: number; readonly sha256: string };
  readonly licence: string; readonly source: string;
}
export interface IndexGeneration { readonly generation: string; readonly base: string; readonly packages: readonly IndexPackage[] }
export interface ModIndex {
  readonly format: string; readonly publish: boolean; readonly notice: string;
  readonly profiles: readonly { readonly profile: string; readonly generations: readonly IndexGeneration[] }[];
  /** Where index.json was read from: archive names resolve against it. */
  readonly url: string;
}
export interface FetchOptions {
  /** '[profile/]generation' when the index holds more than one. */
  readonly generation?: string | undefined;
  readonly fetch?: typeof globalThis.fetch | undefined;
}

const need = (cond: unknown, what: string): void => { if (!cond) throw new PackageError(what); };
const hex = (b: Uint8Array): string => [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
const isInt = (v: unknown, max: number): v is number => Number.isInteger(v) && (v as number) > 0 && (v as number) <= max;

function checkPath(profile: string, generation: string, pkg: string, rel: string): void {
  const parts = rel.split('/');
  need(rel !== '' && !rel.startsWith('/') && !rel.includes('\\') && !parts.some((p) => p === '' || p === '.' || p === '..') &&
       !parts[0]!.includes(':') && !/[\x00-\x1f\x7f]/.test(rel), `${pkg}: unusable path ${JSON.stringify(rel.slice(0, 80))}`);
  const device = new TextEncoder().encode(`${MODS_ROOT}/${profile}/${generation}/${pkg}/${rel}`).length;
  need(device <= MAX_PATH, `${pkg}: path too long for the device (${device} bytes)`);
}

function checkPackage(profile: string, generation: string, pkg: IndexPackage): void {
  const id = pkg?.id;
  need(typeof id === 'string' && ID.test(id), `index: bad package id ${JSON.stringify(String(id).slice(0, 40))}`);
  need(KINDS.includes(pkg.kind), `${id}: bad kind`);
  need(Array.isArray(pkg.files) && pkg.files.length > 0 && pkg.files.length <= MAX_FILES + 1, `${id}: bad file list`);
  const seen = new Set<string>();
  for (const f of pkg.files) {
    need(typeof f?.path === 'string', `${id}: bad file entry`);
    checkPath(profile, generation, id, f.path);
    need(!seen.has(f.path), `${id}: ${f.path} listed twice`);
    seen.add(f.path);
    need(isInt(f.size, MAX_FILE), `${id}/${f.path}: bad or oversize size`);
    need(typeof f.sha256 === 'string' && SHA.test(f.sha256), `${id}/${f.path}: bad sha256`);
  }
  need(seen.has('files.sha256') && recordName(seen) !== undefined, `${id}: no files.sha256 or info.txt`);
  for (const p of seen) {
    const parts = p.split('/');
    for (let i = 1; i < parts.length; i++) need(!seen.has(parts.slice(0, i).join('/')), `${id}: a file and a folder share a name`);
  }
  const arc = pkg.archive;
  need(arc?.name === `${profile}/${generation}/${id}.zip`, `${id}: archive name is not ${profile}/${generation}/${id}.zip`);
  need(isInt(arc.size, MAX_ARCHIVE), `${id}: bad or oversize archive`);
  need(typeof arc.sha256 === 'string' && SHA.test(arc.sha256), `${id}: bad archive sha256`);
}

/** Throws PackageError unless value is a well-formed xyf-mod-index/1. */
export function checkIndex(value: unknown, url: string): ModIndex {
  const index = value as ModIndex;
  need(typeof index === 'object' && index !== null && index.format === INDEX_FORMAT, `not a ${INDEX_FORMAT} index`);
  need(Array.isArray(index.profiles), 'index: no profiles list');
  for (const prof of index.profiles) {
    need(typeof prof?.profile === 'string' && TOKEN.test(prof.profile), 'index: bad profile');
    need(Array.isArray(prof.generations), 'index: no generations list');
    for (const gen of prof.generations) {
      need(typeof gen?.generation === 'string' && TOKEN.test(gen.generation), 'index: bad generation');
      need(Array.isArray(gen.packages), 'index: no packages list');
      const ids = gen.packages.map((p) => p?.id);
      need(new Set(ids).size === ids.length && ids.includes('base'), `index ${gen.generation}: duplicate ids or no base`);
      for (const pkg of gen.packages) checkPackage(prof.profile, gen.generation, pkg);
    }
  }
  return { ...index, url };
}

async function get(url: string, limit: number, fetcher: typeof globalThis.fetch): Promise<Uint8Array> {
  let answer: Response;
  try {
    answer = await fetcher(url);
  } catch (error) {
    throw new PackageError(`cannot read ${url} (${String(error)})`);
  }
  if (!answer.ok) {
    await answer.body?.cancel();
    throw new PackageError(`cannot read ${url} (HTTP ${answer.status})`);
  }
  const reader = answer.body?.getReader();
  const parts: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const chunk = reader ? await reader.read() : { done: true as const, value: undefined };
    if (chunk.done) break;
    size += chunk.value.length;
    if (size > limit) {
      await reader?.cancel();
      throw new PackageError(`${url}: larger than ${limit} bytes`);
    }
    parts.push(chunk.value);
  }
  const out = new Uint8Array(size);
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}

/** index.json at url (of the file, or of its folder ending in '/'). */
export async function loadIndex(url: string, options: { fetch?: typeof globalThis.fetch | undefined } = {}): Promise<ModIndex> {
  const where = url.endsWith('.json') ? url : `${url.replace(/\/+$/, '')}/index.json`;
  const data = await get(where, MAX_INDEX, options.fetch ?? globalThis.fetch);
  let value: unknown;
  try {
    value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(data));
  } catch (error) {
    throw new PackageError(`${where}: not JSON (${String(error)})`);
  }
  return checkIndex(value, where);
}

/** STORED members of a ZIP: name -> bytes, from the central directory (each local header must agree). */
function readZip(id: string, data: Uint8Array): Map<string, Uint8Array> {
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const names = new TextDecoder('utf-8', { fatal: true });
  let end = -1;
  for (let i = data.length - 22; i >= Math.max(0, data.length - 22 - 0xffff); i--) {
    if (view.getUint32(i, true) === 0x06054b50) { end = i; break; }
  }
  need(end >= 0, `${id}: not a usable archive (no end record)`);
  const count = view.getUint16(end + 10, true);
  let at = view.getUint32(end + 16, true);
  const out = new Map<string, Uint8Array>();
  for (let n = 0; n < count; n++) {
    need(at + 46 <= data.length && view.getUint32(at, true) === 0x02014b50, `${id}: not a usable archive (central directory)`);
    const method = view.getUint16(at + 10, true);
    const packed = view.getUint32(at + 20, true);
    const size = view.getUint32(at + 24, true);
    const nameLen = view.getUint16(at + 28, true);
    const skip = nameLen + view.getUint16(at + 30, true) + view.getUint16(at + 32, true);
    const local = view.getUint32(at + 42, true);
    need(at + 46 + nameLen <= data.length, `${id}: not a usable archive (name)`);
    let name: string;
    try { name = names.decode(data.subarray(at + 46, at + 46 + nameLen)); } catch { throw new PackageError(`${id}: member name is not UTF-8`); }
    need(!out.has(name), `${id}: duplicate archive members`);
    need(method === 0, `${id}/${name}: compressed member`);
    need(packed === size && size <= MAX_FILE, `${id}/${name}: member size ${size}`);
    need(local + 30 <= data.length && view.getUint32(local, true) === 0x04034b50, `${id}/${name}: no local header`);
    const start = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
    need(start + size <= data.length, `${id}/${name}: truncated member`);
    out.set(name, data.subarray(start, start + size));
    at += 46 + skip;
  }
  return out;
}

async function unpack(pkg: IndexPackage, data: Uint8Array): Promise<Map<string, Uint8Array>> {
  const id = pkg.id;
  need(data.length === pkg.archive.size && hex(await sha256(data)) === pkg.archive.sha256, `${id}: archive differs from the index`);
  const members = readZip(id, data);
  const want = new Map(pkg.files.map((f) => [f.path, f]));
  const unknown = [...members.keys()].filter((n) => !want.has(n));
  need(unknown.length === 0, `${id}: archive members not in the index: ${unknown.slice(0, 5).join(', ')}`);
  const missing = [...want.keys()].filter((n) => !members.has(n));
  need(missing.length === 0, `${id}: archive lacks ${missing.slice(0, 5).join(', ')}`);
  for (const [name, body] of members) {
    const f = want.get(name)!;
    need(body.length === f.size, `${id}/${name}: member size ${body.length}, index says ${f.size}`);
    need(hex(await sha256(body)) === f.sha256, `${id}/${name}: differs from the index`);
  }
  const listed = listPaths(members.get('files.sha256')!);
  const others = pkg.files.filter((f) => f.path !== 'files.sha256');
  need(listed.length === others.length && listed.every(([sha, path]) => want.get(path)?.sha256 === sha && path !== 'files.sha256'),
       `${id}: its files.sha256 does not match the index`);
  return members;
}

/**
 * Downloads the base and the packages ids (all of the generation's when ids is empty), checks them and
 * returns the generation folder as a PackageSource for pushPackages.
 */
export async function fetchPackages(index: ModIndex, ids: readonly string[] = [], options: FetchOptions = {}): Promise<PackageSource & { readonly profile: string; readonly generation: string }> {
  let gens = index.profiles.flatMap((p) => p.generations.map((g) => ({ profile: p.profile, gen: g })));
  if (options.generation) {
    const [profile, name] = options.generation.includes('/') ? options.generation.split('/', 2) : [undefined, options.generation];
    gens = gens.filter((g) => g.gen.generation === name && (profile === undefined || g.profile === profile));
  }
  need(gens.length === 1, 'choose one generation (options.generation)');
  const { profile, gen } = gens[0]!;
  const byId = new Map(gen.packages.map((p) => [p.id, p]));
  const unknown = ids.filter((i) => !byId.has(i));
  need(unknown.length === 0, `not in the index: ${unknown.join(', ')}`);
  const chosen = ids.length ? ['base', ...[...new Set(ids)].filter((i) => i !== 'base').sort()] : [...byId.keys()];
  const fetcher = options.fetch ?? globalThis.fetch;
  const files = new Map<string, Uint8Array>();
  for (const id of chosen) {
    const pkg = byId.get(id)!;
    const data = await get(new URL(pkg.archive.name, index.url).href, pkg.archive.size, fetcher);
    for (const [path, body] of await unpack(pkg, data)) files.set(`${id}/${path}`, body);
  }
  return {
    profile, generation: gen.generation,
    paths: () => [...files.keys()],
    read: async (path) => {
      const body = files.get(path);
      if (!body) throw new PackageError(`${path}: not in the fetched packages`);
      return body;
    },
  };
}
