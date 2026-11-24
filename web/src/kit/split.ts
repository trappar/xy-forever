// XY Forever packages from an Open-XY kit in the browser: the twin of xy_forever/modkit.py `split` (its host
// reference): for the same kit, the same files byte for byte (tests/test_kit_browser.py compares them). One mod-base
// package for the kit generation, one mod package per machine and one per extension, as a generation folder:
// 'base/manifest.json', '<machine>/objects/...', 'ext-<id>/...'. Objects are copied unchanged. Open-XY's own builder
// (core.mjs, of the kit's release: source.ts loads it) checks the kit (openKit), reads its objects and finishes each
// machine's content (svg, presets); this module refuses only what the packages or the OP-XY cannot carry. Nothing
// here reaches the network or the device.
import type { Core, ReadObject } from './core.ts';
import { KitError, check } from './errors.ts';
import { pyDumps, pyFloat } from './py-json.ts';
import type { Json } from './py-json.ts';

const KIT_FORMAT = 'opxy-browser-kit/1';
const EDIT_KINDS = ['heap', 'table', 'text', 'hook', 'startup', 'limit'];
const MACHINE_ID = /^[a-z][a-z0-9_]{0,14}$/;
const EXTENSION_ID = /^[a-z][a-z0-9_]{0,31}$/;
const RULE_SCALARS = ['engines', 'engine_none', 'pages', 'page_none', 'effect_page'];
const RULE_LISTS = ['stock_engines', 'sample_pages', 'effects', 'players'];
const LIMIT_SERVICES = ['long_samples', 'release_floor'];
const FLOOR_VALUES = Array.from({ length: 47 }, (_, i) => -20 - i);
const MANIFEST_KEYS = [
  'type',
  'name',
  'version',
  'kind',
  'bytes',
  'crc32',
  'sha256',
  'content',
  'needs_os',
  'generation',
  'profile',
  'machine',
  'source',
  'licence',
  'machines',
];
// native_json.py: the typed fields of a package record.
const NUMBERS = new Set(
  'bytes machines module_base module_bytes stock_bytes edits objects extensions code data state runtime_code runtime_data runtime_state catalog_engine_at catalog_fx_at catalog_seq_at stock_records reserved_bytes heap_fill_base heap_fill_bytes expanded_module_base order link presets'.split(
    ' ',
  ),
);
const ADDRESSES = new Set(['module_base', 'heap_fill_base', 'expanded_module_base']);
const OPTIONAL_NUMBERS = new Set(['engine', 'effect', 'player', 'page']);
const LISTS = new Set(['services', 'features', 'claims', 'accessors']);

type Rec = Record<string, unknown>;
interface Edit extends Rec {
  kind: string;
  address: string;
  file_offset: string;
  length: number;
  before: string;
  after?: string;
  symbol?: string;
  call?: boolean;
  service?: string;
  profiling?: boolean;
  setting?: string;
  choices?: Record<string, string>;
}
interface Profile extends Rec {
  id: string;
  patches: Edit[];
  services?: Record<string, string[]>;
  identities: Record<string, unknown>;
  module_base: number;
  reserved_bytes: number;
  table_bytes: number;
  heap_fill_base: number;
  heap_fill_bytes: number;
  stock_sha256: string;
  stock_bytes: number;
  stock_records: number;
  stock_catalog_sha256: string;
  stock_catalog_inserts: Record<string, number>;
}
interface Source extends Rec {
  repository: string;
  tag: string;
  commit: string;
  maintainer: string;
}
interface Machine extends Rec {
  id: string;
  name: string;
  kind: string;
  prefix: string;
  identity: Record<string, number | null>;
  absent: Record<string, string>;
  catalog: string;
  origin: string;
  features?: string[] | null;
  source?: Source | null;
  asset?: string;
  artwork?: string;
  description?: string;
}
interface Processor extends Rec {
  asset: string;
  sha256: string;
  artwork: string;
  state_id?: string;
}
interface Setting extends Rec {
  value: number;
}
interface Extension extends Rec {
  id: string;
  name: string;
  origin: string;
  description?: string;
  claims?: string[];
  claim_labels?: Record<string, string>;
  actions?: string[];
  linked?: boolean;
  drumline_page?: boolean;
  capabilities?: string[];
  settings?: Record<string, Setting>;
}
interface KitObject extends Rec {
  name: string;
  sha256: string;
  bytes: string;
  owner?: string | null;
  service?: string | null;
  runtime?: boolean;
  extension?: string | null;
}
export interface Kit extends Rec {
  format: string;
  version: string;
  profile: Profile;
  profileSource: string;
  definitions: Record<string, number>;
  fixed: Record<string, Record<string, number>>;
  machines: Machine[];
  objects: KitObject[];
  stockCatalog: string;
  screenTemplate: string;
  linked?: string[];
  notices?: string;
  extensions?: Extension[];
  serviceFallbacks?: Record<string, Record<string, string>>;
}
interface ObjectSummary {
  readonly exports: ReadonlySet<string>;
  readonly imports: ReadonlySet<string>;
  readonly absolute: readonly string[];
  readonly code: number;
  readonly data: number;
  readonly state: number;
}
interface Obj extends ObjectSummary {
  order: number;
  name: string;
  owner: string | null;
  service: string | null;
  runtime: boolean;
  extension: string | null;
  blob: Uint8Array;
}
interface Model {
  kit: Kit;
  profile: Profile;
  profileBytes: Uint8Array;
  objects: Obj[];
  generation: string;
}
export interface SplitSet {
  readonly profile: string;
  readonly generation: string;
  /** Every file of the generation folder, by its path in it ('base/manifest.json'). */
  readonly files: ReadonlyMap<string, Uint8Array>;
}

const utf8 = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });
const has = (o: object, key: string): boolean => Object.hasOwn(o, key);
const get = <T>(o: object, key: string): T | null => (has(o, key) ? ((o as Rec)[key] as T) : null);
const keysOf = (o: object): string[] => Object.keys(o);
const isObject = (v: unknown): v is Rec => v !== null && typeof v === 'object' && !Array.isArray(v);
/** Python truthiness. */
const truthy = (v: unknown): boolean =>
  !(
    v === undefined ||
    v === null ||
    v === false ||
    v === 0 ||
    v === '' ||
    (Array.isArray(v) && !v.length) ||
    (isObject(v) && !keysOf(v).length)
  );
const hex = (bytes: Uint8Array): string => Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
const hex8 = (n: number): string => `0x${n.toString(16).padStart(8, '0')}`;
const list = (items: Iterable<string>): string => `[${[...items].map((s) => `'${s}'`).join(', ')}]`;
const sorted = (items: Iterable<string>): string[] => [...items].sort();

async function sha256(data: Uint8Array): Promise<string> {
  return hex(new Uint8Array(await crypto.subtle.digest('SHA-256', data as Uint8Array<ArrayBuffer>)));
}

function decode64(text: string): Uint8Array {
  let binary: string;
  try {
    binary = atob(text);
  } catch {
    throw new KitError('invalid base64');
  }
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

function fromHex(text: string): Uint8Array {
  check(typeof text === 'string' && /^(?:[0-9a-fA-F]{2})*$/.test(text), 'invalid hex bytes');
  return Uint8Array.from(text.match(/../g) ?? [], (b) => parseInt(b, 16));
}

/** int(text, 16): a hex number, 0x optional. */
function int16(text: unknown): number {
  check(typeof text === 'string' && /^\s*(0[xX])?[0-9a-fA-F]+\s*$/.test(text), 'invalid hex number');
  return parseInt(text.trim().replace(/^0[xX]/, ''), 16);
}

function concat(parts: readonly Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

// ---------------------------------------------------------------- edits.bin (the profile's edits in binary form)

/** Validate the two capability edit forms in Open-XY v0.0.7. */
function limitChoices(edit: Edit, services: readonly string[]): Uint8Array {
  const service = get<unknown>(edit, 'service');
  check(
    typeof service === 'string' && LIMIT_SERVICES.includes(service) && services.includes(service),
    'unsupported limit service',
  );
  check(!['symbol', 'call', 'profiling'].some((k) => has(edit, k)), 'unsupported limit fields');
  const length = edit.length;
  check((service === 'long_samples' ? [8] : [2, 8]).includes(length), 'unsupported limit length');
  for (const key of ['before', 'after']) {
    const value = get<unknown>(edit, key);
    check(typeof value === 'string' && new RegExp(`^[0-9a-f]{${length * 2}}$`).test(value), `invalid limit ${key}`);
  }
  if (service === 'release_floor' && length === 8) {
    const choices = get<unknown>(edit, 'choices');
    check(
      get(edit, 'setting') === service &&
        isObject(choices) &&
        keysOf(choices).length === FLOOR_VALUES.length &&
        FLOOR_VALUES.every((v) => has(choices, String(v))),
      'unsupported release-floor choices',
    );
    check(
      Object.values(choices).every((v) => typeof v === 'string' && /^[0-9a-f]{16}$/.test(v)),
      'invalid release-floor bytes',
    );
    return concat(FLOOR_VALUES.map((v) => fromHex(choices[String(v)] as string)));
  }
  check(!has(edit, 'setting') && !has(edit, 'choices'), 'unsupported fixed limit choices');
  return new Uint8Array();
}

/** The profile's edits as the device reads them (docs/storage/mods.md, "Mod packages"). */
function encodeEdits(profile: Profile): Uint8Array {
  const services = keysOf(profile.services ?? {});
  const strings: number[] = [],
    offsets = new Map<string, number>();
  const text = (value: string): number => {
    if (!offsets.has(value)) {
      offsets.set(value, strings.length);
      strings.push(...utf8.encode(value), 0);
    }
    return offsets.get(value)!;
  };
  const serviceAt = services.map(text);
  const records: Uint8Array[] = [];
  let version = 1;
  for (const edit of profile.patches) {
    const kind = EDIT_KINDS.indexOf(edit.kind) + 1;
    let choices: Uint8Array = new Uint8Array();
    if (edit.kind === 'limit') {
      version = 2;
      choices = limitChoices(edit, services);
    }
    const before = fromHex(edit.before);
    const after = has(edit, 'after') ? fromHex(edit.after!) : new Uint8Array();
    check(before.length === edit.length && (!after.length || after.length === edit.length), 'edit length mismatch');
    const flags = (truthy(edit.call) ? 1 : 0) | (edit.profiling === false ? 2 : 0) | (after.length ? 4 : 0);
    const service = has(edit, 'service') ? services.indexOf(edit.service!) : 0xff;
    check(service >= 0, `edit names an unknown service ${String(edit.service)}`);
    const symbol = has(edit, 'symbol') ? text(edit.symbol!) : 0xffff;
    const head = new Uint8Array(16),
      view = new DataView(head.buffer);
    head.set([kind, flags, service, choices.length ? choices.length / edit.length : 0]);
    view.setUint32(4, int16(edit.address), true);
    view.setUint32(8, int16(edit.file_offset), true);
    view.setUint16(12, edit.length, true);
    view.setUint16(14, symbol, true);
    records.push(head, before, after, choices);
  }
  check(strings.length < 0xffff && services.length < 0xff, 'edit tables too large');
  const head = new Uint8Array(12 + 2 * services.length),
    view = new DataView(head.buffer);
  head.set(utf8.encode('XYFE'));
  [version, profile.patches.length, services.length, strings.length].forEach((n, i) =>
    view.setUint16(4 + 2 * i, n, true),
  );
  serviceAt.forEach((n, i) => view.setUint16(12 + 2 * i, n, true));
  return concat([head, Uint8Array.from(strings), ...records]);
}

// ---------------------------------------------------------------- kit reading and checks

const processorKey = (e: Rec): string | null =>
  has(e, 'master_processor') ? 'master_processor' : has(e, 'master_filter') ? 'master_filter' : null;

/** The kit export's identity: version + digest of everything a link depends on. */
async function generationOf(kit: Kit): Promise<string> {
  const g = <T>(o: object, key: string): T | null => get<T>(o, key) ?? null;
  const canon: Record<string, Json> = {
    version: kit.version,
    profile: await sha256(decode64(kit.profileSource)),
    objects: kit.objects.map((o) => [
      o.name,
      o.sha256,
      g(o, 'owner'),
      g(o, 'service'),
      g(o, 'runtime'),
      g(o, 'extension'),
    ]) as Json,
    machines: kit.machines.map((m) => [m.id, m.kind, m.prefix, m.identity, m.absent, g(m, 'features')]) as Json,
    extensions: (kit.extensions ?? []).map((e) => e.id),
    definitions: kit.definitions,
    fixed: kit.fixed,
    fallbacks: (kit.serviceFallbacks ?? {}) as Json,
  };
  // The v0.0.6 generation stays byte-for-byte stable. From v0.0.7 on, extension
  // metadata can change both service activation and patched firmware bytes.
  const [major, minor, patch] = kit.version.split('.').map(Number) as [number, number, number];
  const newer = major > 0 || minor > 0 || patch >= 7;
  if (
    newer ||
    (kit.extensions ?? []).some((e) => ['linked', 'drumline_page', 'capabilities', 'settings'].some((k) => has(e, k)))
  )
    canon['extensions'] = (kit.extensions ?? []).map((e) => {
      const key = processorKey(e);
      return [
        e.id,
        g(e, 'linked'),
        key,
        key === null ? null : g(e, key),
        g(e, 'claims'),
        g(e, 'drumline_page'),
        g(e, 'capabilities'),
        g(e, 'settings'),
      ];
    }) as Json;
  return `${kit.version}-${(await sha256(utf8.encode(pyDumps(canon, { sortKeys: true })))).slice(0, 12)}`;
}

/** The link-time numbers core.mjs numberSymbols defines for one machine (the only non-SDK imports allowed). */
function ownNumberSymbols(m: Machine): string[] {
  const p = m.prefix;
  if (m.kind === 'player') return [`${p}_player_id`, `${p}_page_id`];
  if (m.kind === 'effect') return [`${p}_fx_id`, `${p}_fx_page_id`];
  if (m.kind === 'sample' && (m.identity['page'] ?? null) === null)
    return [`${p}_engine_id`, `${p}_page_id`, `${p}_screen_page_id`];
  return [`${p}_engine_id`, `${p}_page_id`];
}

const servicesFor = (profile: Profile, users: readonly string[]): string[] =>
  Object.entries(profile.services ?? {})
    .filter(([, wanted]) => users.some((u) => wanted.includes(u)))
    .map(([name]) => name);

/** The capabilities and settings the OP-XY applies (openKit has checked their form against the profile). */
function validateExtensionCapabilities(e: Extension, profile: Profile): void {
  const capabilities = e.capabilities ?? [];
  check(
    capabilities.every((c) => LIMIT_SERVICES.includes(c)),
    `extension ${e.id}: capabilities ${list(capabilities)} the OP-XY does not apply`,
  );
  const settings = e.settings ?? {};
  check(
    keysOf(settings).every((k) => k === 'release_floor'),
    `extension ${e.id}: settings ${list(keysOf(settings))} the OP-XY does not apply`,
  );
  const value = settings['release_floor']?.value;
  check(
    value === undefined ||
      (FLOOR_VALUES.includes(value) && profile.patches.some((edit) => edit.setting === 'release_floor')),
    'invalid extension release floor',
  );
}

const SHN_ABS = 0xfff1,
  STT_FILE = 4;

/** Exports, imports, absolute symbols and code/data/state bytes of one object, from core.mjs readObject's result. */
function objectSummary(o: ReadObject, name: string): ObjectSummary {
  const exports = new Set<string>(),
    imports = new Set<string>(),
    absolute: string[] = [];
  for (const s of o.symbols) {
    if (s.type === STT_FILE || !s.name) continue;
    if (s.section === SHN_ABS) absolute.push(s.name);
    if (!s.binding) continue;
    check(s.binding === 1, `${name}: weak symbol ${s.name} is unsupported`);
    (s.section ? exports : imports).add(s.name);
  }
  const sizes = { code: 0, data: 0, state: 0 };
  for (const s of o.sections)
    if (s.group) sizes[s.group === 'text' ? 'code' : s.group === 'bss' ? 'state' : 'data'] += s.size;
  return { exports, imports, absolute, ...sizes };
}

/** What the packages and the OP-XY need beyond Open-XY's own checks (openKit, done first): edits the device
 * applies, names usable as package folders and files, and links its linker makes. */
async function analyse(kit: Kit, opened: readonly (KitObject & ReadObject)[]): Promise<Model> {
  const profile = kit.profile;
  check(/^[a-z0-9][a-z0-9._-]{0,30}$/.test(String(get(profile, 'id'))), 'invalid profile id');
  const services = keysOf(profile.services ?? {});
  for (const edit of profile.patches) {
    check(EDIT_KINDS.includes(get(edit, 'kind') as string), `unknown edit kind '${String(get(edit, 'kind'))}'`);
    if (edit.kind === 'limit') limitChoices(edit, services);
  }
  const machines = kit.machines,
    extensions = kit.extensions ?? [];
  const ids = machines.map((m) => m.id);
  const twice = (all: string[]): string[] => sorted(new Set(all.filter((i, n) => all.indexOf(i) !== n)));
  check(new Set(ids).size === ids.length, `duplicate machine ids ${list(twice(ids))}`);
  check(
    ids.every((i) => typeof i === 'string' && MACHINE_ID.test(i) && i !== 'base'),
    `invalid machine id (or "base") in ${list(ids)}`,
  );
  const eids = extensions.map((e) => e.id);
  check(new Set(eids).size === eids.length, `duplicate extension ids ${list(twice(eids))}`);
  check(
    eids.every((i) => typeof i === 'string' && EXTENSION_ID.test(i)),
    `invalid extension id in ${list(eids)}`,
  );
  const both = sorted(ids.filter((i) => eids.includes(i)));
  check(!both.length, `ids used by both a machine and an extension: ${list(both)}`);
  const objects: Obj[] = opened.map((o, index) => {
    check(
      typeof o.name === 'string' && /^[A-Za-z0-9_.-]+(\/[A-Za-z0-9_.-]+)*\.o$/.test(o.name) && !o.name.includes('..'),
      `unsafe object name '${o.name}'`,
    );
    return {
      order: index,
      name: o.name,
      owner: get<string>(o, 'owner'),
      service: get<string>(o, 'service'),
      runtime: truthy(get(o, 'runtime')),
      extension: get<string>(o, 'extension'),
      blob: decode64(o.bytes),
      ...objectSummary(o, o.name),
    };
  });
  const sdkExports = new Set(baseObjects(objects).flatMap((o) => [...o.exports]));
  for (const m of machines) {
    const own = objects.filter((o) => o.owner === m.id);
    check(own.length, `machine ${m.id} has no objects`);
    const allowed = new Set([...sdkExports, ...ownNumberSymbols(m), ...own.flatMap((o) => [...o.exports])]);
    for (const o of own) {
      check(
        !o.absolute.length,
        `${o.name} defines absolute symbols ${list(o.absolute)}: addresses belong only to the base`,
      );
      const bad = sorted([...o.imports].filter((s) => !allowed.has(s)));
      check(!bad.length, `machine ${m.id} imports ${list(bad)}, which are not SDK or runtime exports`);
    }
  }
  for (const e of extensions) {
    validateExtensionCapabilities(e, profile);
    const own = objects.filter((o) => o.extension === e.id);
    if (get(e, 'linked') === false) {
      check(!own.length, `settings extension ${e.id} links no code`);
      continue;
    }
    check(own.length === 1, `extension ${e.id} needs exactly one object`);
    const o = own[0]!;
    check(
      !o.absolute.length,
      `${o.name} defines absolute symbols ${list(o.absolute)}: addresses belong only to the base`,
    );
    const bad = sorted([...o.imports].filter((s) => !sdkExports.has(s)));
    check(!bad.length, `extension ${e.id} imports ${list(bad)}, which are not SDK or runtime exports`);
    check(o.exports.has(`sdk_extension_${e.id}_hooks`), `extension ${e.id} lacks its hooks accessor`);
  }
  return { kit, profile, profileBytes: decode64(kit.profileSource), objects, generation: await generationOf(kit) };
}

// ---------------------------------------------------------------- writing packages

function licenceOf(text: string): string {
  for (const [marker, name] of [
    ['GNU AFFERO GENERAL PUBLIC LICENSE', 'AGPL-3.0'],
    ['GNU GENERAL PUBLIC LICENSE', 'GPL-3.0'],
    ['MIT License', 'MIT'],
    ['Apache License', 'Apache-2.0'],
    ['BSD', 'BSD'],
  ] as const)
    if (text.includes(marker)) return name;
  return 'see NOTICE.txt';
}

/** {machine name: notice text} for the machine vendor licences the kit's notices carry. */
function noticeSections(notices: string, names: ReadonlySet<string>): Map<string, string> {
  const out = new Map<string, string>(),
    parts = notices.split('\n\n');
  const starts = parts.flatMap((p, i) => (names.has(p) ? [i] : []));
  starts.forEach((i, n) => {
    const end = n + 1 < starts.length ? starts[n + 1] : parts.length;
    out.set(parts[i]!, parts.slice(i + 1, end).join('\n\n') + '\n');
  });
  return out;
}

const byteOrder = (a: string, b: string): number => {
  const x = utf8.encode(a),
    y = utf8.encode(b);
  for (let i = 0; i < Math.min(x.length, y.length); i++) if (x[i] !== y[i]) return x[i]! - y[i]!;
  return x.length - y.length;
};

/** files.sha256 for the files (sorted by path, bytewise): what xyf_entry.files_sha256_text writes. */
async function withList(files: Map<string, Uint8Array>): Promise<Map<string, Uint8Array>> {
  const lines: string[] = [];
  for (const path of [...files.keys()].sort(byteOrder)) lines.push(`${await sha256(files.get(path)!)}  ${path}\n`);
  files.set('files.sha256', utf8.encode(lines.join('')));
  return files;
}

const table = (header: string, rows: readonly (readonly (string | number)[])[]): Uint8Array =>
  utf8.encode(`# ${header}\n` + rows.map((r) => r.map(String).join(' ') + '\n').join(''));

function dash(value: unknown): string {
  if (value === undefined || value === null || value === '' || (Array.isArray(value) && !value.length)) return '-';
  return Array.isArray(value) ? value.join(',') : String(value);
}

/** base identities.txt: profile.identities, one rule per line (a list comma-separated, - = empty). */
function identitiesTable(identities: Record<string, unknown>): Uint8Array {
  const unknown = keysOf(identities).filter((k) => ![...RULE_SCALARS, ...RULE_LISTS].includes(k));
  check(!unknown.length, `unknown numbering rules ${list(sorted(unknown))}`);
  const int = (v: unknown): number => {
    check(typeof v === 'number' || typeof v === 'boolean', 'invalid numbering rule');
    return Math.trunc(Number(v));
  };
  const rows: (string | number)[][] = RULE_SCALARS.map((k) => {
    check(has(identities, k), `missing numbering rule ${k}`);
    return [k, int(identities[k])];
  });
  for (const k of RULE_LISTS) {
    const values = identities[k];
    rows.push([k, dash(truthy(values) ? (values as unknown[]).map((n) => String(int(n))) : [])]);
  }
  return table('rule value (profile.identities: the numbering rules; lists comma-separated, - = none)', rows);
}

/** quote(text, safe=''): UTF-8, only A-Za-z0-9_.-~ kept. */
const quote = (text: string): string =>
  encodeURIComponent(text).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);

/** extensions.txt labels=: each claim's label percent-encoded, in claim order, comma-separated; - = no claims. */
function claimLabelsField(e: Extension): string {
  const labels = (truthy(get(e, 'claim_labels')) ? e.claim_labels : {}) as Record<string, string>;
  const claims = truthy(get(e, 'claims')) ? e.claims! : [];
  return dash(claims.map((c) => quote(truthy(get(labels, c)) ? labels[c]! : '')));
}

type Field = readonly [string, string | number | null];

/** The package record (manifest.json) for these fields: xyf_entry.manifest_text, then native_json.package_document. */
function manifest(known: Record<string, string | number | null>, extra: readonly Field[]): Uint8Array {
  const fields: Record<string, string | number | null> = { ...known },
    unknown: Field[] = [];
  for (const [key, value] of extra) {
    check(/^[a-z0-9_]{1,31}$/.test(key) && String(value) && !/[\r\n]/.test(String(value)), 'manifest syntax');
    check(!has(fields, key), 'manifest duplicate_key');
    if (MANIFEST_KEYS.includes(key)) fields[key] = value;
    else unknown.push([key, value]);
  }
  const lines: Field[] = [
    ...MANIFEST_KEYS.filter((k) => has(fields, k) && fields[k] !== null).map((k): Field => [k, fields[k]!]),
    ...unknown,
  ];
  // The text view's last-key-wins fields, as package_records.values reads them back.
  const values = new Map<string, string>();
  for (const [key, value] of lines) values.set(key, typeof value === 'number' ? pyNumber(value) : String(value));
  const parts = ['"schema":"xy-forever.package"', '"schema_version":1'];
  for (const [key, value] of values) {
    let typed: string;
    if (NUMBERS.has(key) || OPTIONAL_NUMBERS.has(key))
      typed =
        value === '-' && OPTIONAL_NUMBERS.has(key) ? 'null' : String(ADDRESSES.has(key) ? int16(value) : pyInt(value));
    else if (LISTS.has(key))
      typed = pyDumps(value === '' || value === '-' ? [] : value.split(','), { compact: true, ensureAscii: false });
    else if (key === 'catalog_number') typed = value === '-' ? 'null' : `[${value.split(',').map(pyInt).join(',')}]`;
    else if (key === 'cpu_peak_percent') typed = pyFloat(Number(value));
    else typed = pyDumps(value, { ensureAscii: false });
    parts.push(`${JSON.stringify(key)}:${typed}`);
  }
  return utf8.encode(`{${parts.join(',')}}\n`);
}

/** str(value) of a kit number (an integer, or a non-integral float). */
const pyNumber = (value: number): string => (Number.isInteger(value) ? String(value) : pyFloat(value));
const pyInt = (value: string): number => {
  check(/^\s*[+-]?\d+\s*$/.test(value), `invalid number ${value}`);
  return Number(value);
};

const baseObjects = (objects: readonly Obj[]): Obj[] => objects.filter((o) => o.owner === null && o.extension === null);
const role = (o: Obj): string => (o.runtime ? 'runtime' : o.service ? `service=${o.service}` : 'core');
const sum = (objects: readonly Obj[], key: 'code' | 'data' | 'state'): number =>
  objects.reduce((n, o) => n + o[key], 0);

function writeBase(
  model: Model,
  common: Record<string, string>,
  sdkSource: string | null,
  profileSha: string,
  expanded: number | null,
) {
  const { kit, profile } = model;
  const objects = baseObjects(model.objects);
  const files = new Map<string, Uint8Array>([
    ['profile.json', model.profileBytes],
    ['edits.bin', encodeEdits(profile)],
    ['screen.svg', utf8.encode(kit.screenTemplate)],
  ]);
  const notices = get<string>(kit, 'notices') ?? '';
  if (truthy(notices)) files.set('notices.txt', utf8.encode(notices));
  for (const o of objects) files.set(`objects/${o.name}`, o.blob);
  files.set(
    'objects.txt',
    table(
      'order role path (link order: ascending order across all packages)',
      objects.map((o) => [o.order, role(o), `objects/${o.name}`]),
    ),
  );
  files.set(
    'definitions.txt',
    table(
      'symbol address',
      Object.entries(kit.definitions).map(([k, v]) => [k, hex8(v)]),
    ),
  );
  files.set(
    'fixed.txt',
    table(
      'machine numbers',
      Object.entries(kit.fixed).map(([k, v]) => [
        k,
        Object.entries(v)
          .map(([a, n]) => `${a}=${Math.trunc(n)}`)
          .join(' '),
      ]),
    ),
  );
  files.set(
    'fallbacks.txt',
    table(
      'service symbol fallback (aliased while the service is unused)',
      Object.entries(kit.serviceFallbacks ?? {}).flatMap(([s, pairs]) =>
        Object.entries(pairs).map(([sym, fb]) => [s, sym, fb]),
      ),
    ),
  );
  files.set(
    'services.txt',
    table(
      'service users (machine kinds and features, extension, master_filter) and the bytes its objects add when a selection uses it',
      Object.entries(profile.services ?? {}).map(([name, users]) => {
        const own = objects.filter((o) => o.service === name);
        return [
          name,
          `users=${users.join(',')}`,
          ...(['code', 'data', 'state'] as const).map((k) => `${k}=${sum(own, k)}`),
        ];
      }),
    ),
  );
  if (truthy(get(kit, 'linked')))
    files.set(
      'linked.txt',
      table(
        'machines the kit export links natively',
        kit.linked!.map((i) => [i]),
      ),
    );
  const rows: (string | number)[][] = [],
    absent: string[][] = [];
  kit.machines.forEach((m, order) => {
    const slot = m.kind === 'player' ? 'player' : m.kind === 'effect' ? 'effect' : 'engine';
    const src = (truthy(get(m, 'source')) ? m.source : {}) as Rec;
    rows.push([
      order,
      m.id,
      `kind=${m.kind}`,
      `prefix=${m.prefix}`,
      `${slot}=${dash(get(m.identity, slot))}`,
      `page=${dash(get(m.identity, 'page'))}`,
      `features=${dash(get(m, 'features'))}`,
      `origin=${m.origin}`,
      ...['repository', 'tag', 'commit', 'maintainer'].map((k) => `${k}=${dash(get(src, k))}`),
    ]);
    absent.push(...Object.entries(m.absent).map(([sym, fb]) => [m.id, sym, fb]));
  });
  files.set(
    'machines.txt',
    table('order id kind prefix requested numbers (- = given by the layout) features origin source', rows),
  );
  files.set('absent.txt', table('machine symbol fallback (aliased while the machine is not selected)', absent));
  files.set(
    'extensions.txt',
    table(
      'order id link services claims labels accessors processor state_id asset origin',
      (kit.extensions ?? []).map((e, order) => [order, e.id, ...extensionRow(model, e)]),
    ),
  );
  files.set('identities.txt', identitiesTable(profile.identities));
  const insert = profile.stock_catalog_inserts;
  const core = objects.filter((o) => role(o) === 'core'),
    runtime = objects.filter((o) => o.runtime);
  const licence = notices.includes('GCC arithmetic runtime')
    ? 'MIT AND GPL-3.0-or-later WITH GCC-exception-3.1'
    : 'MIT';
  const known = {
    type: 'mod-base',
    name: 'open-xy SDK base',
    version: kit.version,
    needs_os: profile.stock_sha256,
    source: sdkSource,
    licence,
    ...common,
  };
  const extra: Field[] = [
    ['format', KIT_FORMAT],
    ['profile_sha256', profileSha],
    ['module_base', hex8(profile.module_base)],
    ['module_bytes', profile.reserved_bytes - profile.table_bytes],
    ['stock_bytes', profile.stock_bytes],
    ['edits', profile.patches.length],
    ['objects', objects.length],
    ['machines', kit.machines.length],
    ['extensions', (kit.extensions ?? []).length],
    ['code', sum(core, 'code')],
    ['data', sum(core, 'data')],
    ['state', sum(core, 'state')],
    ['catalog_sha256', profile.stock_catalog_sha256],
    ['catalog_engine_at', insert['engine_types']!],
    ['catalog_fx_at', insert['fx_types']!],
    ['catalog_seq_at', insert['seq_types']!],
    ['stock_records', profile.stock_records],
    ['reserved_bytes', profile.reserved_bytes],
    ['heap_fill_base', hex8(profile.heap_fill_base)],
    ['heap_fill_bytes', profile.heap_fill_bytes],
    ...(['code', 'data', 'state'] as const).map((k): Field => [`runtime_${k}`, sum(runtime, k)]),
  ];
  const x = expanded;
  if (x !== null) extra.push(['expanded_module_base', hex8(x)]); // the 4 MiB option: older readers skip the key
  return { files, known, extra };
}

function extensionRow(model: Model, e: Extension): string[] {
  const o = model.objects.find((x) => x.extension === e.id);
  const key = processorKey(e);
  const accessors = o ? ['hooks'] : [];
  if (key)
    accessors.push(...['processor', 'panel', 'filter'].filter((s) => o!.exports.has(`sdk_extension_${e.id}_${s}`)));
  const users = [...(o ? ['extension'] : []), ...(key ? ['master_filter'] : [])];
  users.push(...(truthy(get(e, 'drumline_page')) ? ['drumline_page'] : []), ...(e.capabilities ?? []));
  const proc = (key ? e[key] : {}) as Rec;
  const fields: string[] = [];
  if (truthy(get(e, 'drumline_page'))) fields.push('drumline_page=1');
  if (truthy(get(e, 'capabilities'))) fields.push(`capabilities=${sorted(e.capabilities!).join(',')}`);
  const setting = (e.settings ?? {})['release_floor'];
  if (truthy(setting)) fields.push(`release_floor=${-setting!.value}`);
  return [
    `link=${o ? o.order : '-'}`,
    `services=${dash(servicesFor(model.profile, users))}`,
    `claims=${dash(get(e, 'claims'))}`,
    `labels=${claimLabelsField(e)}`,
    `accessors=${dash(accessors)}`,
    `processor=${key ?? '-'}`,
    `state_id=${truthy(get(proc, 'state_id')) ? String(proc['state_id']) : '-'}`,
    `asset=${truthy(get(proc, 'asset')) ? String(proc['asset']) : '-'}`,
    ...fields,
    `origin=${e.origin}`,
  ];
}

/** The machine fragment core.mjs buildAssets put before the template's </svg>, or null. */
function artworkFrom(finished: string, template: string): string | null {
  const at = template.indexOf('</svg>');
  const head = template.slice(0, Math.max(at, 0)),
    tail = template.slice(at + 6);
  if (at < 0 || !finished.startsWith(head) || !finished.endsWith(tail) || finished.length < template.length)
    return null;
  const inserted = finished.slice(at, finished.length - tail.length);
  return inserted.endsWith('</svg>') ? inserted.slice(0, -6) : null;
}

/** Start and end (UTF-8 bytes) of the number core.mjs catalogEntry rewrites ("id": N), for effects and players. */
function catalogNumber(m: Machine): string | null {
  if (!['effect', 'player'].includes(m.kind) || !m.catalog) return null;
  const found = /"id":\s*\d+/.exec(m.catalog);
  check(found, `${m.id}: catalog entry has no "id" to number`);
  const start = utf8.encode(m.catalog.slice(0, found.index)).length;
  return `${start},${start + utf8.encode(found[0]).length}`;
}

function writeMachine(
  model: Model,
  m: Machine,
  order: number,
  common: Record<string, string>,
  assets: Record<string, Uint8Array>,
  notice: string | undefined,
) {
  const own = model.objects.filter((o) => o.owner === m.id);
  const files = new Map(Object.entries(assets));
  for (const o of own) files.set(`objects/${o.name}`, o.blob);
  files.set(
    'objects.txt',
    table(
      'order role path',
      own.map((o) => [o.order, 'machine', `objects/${o.name}`]),
    ),
  );
  if (m.catalog) files.set('catalog.txt', utf8.encode(m.catalog));
  const skip = ['id', 'kind', 'prefix', 'identity', 'absent', 'catalog', 'origin', 'source', 'features'];
  const host: Record<string, Json> = Object.fromEntries(Object.entries(m).filter(([k]) => !skip.includes(k))) as Record<
    string,
    Json
  >;
  if (has(host, 'preview'))
    host['preview'] = Object.fromEntries(
      Object.entries(host['preview'] as Record<string, Json>).filter(([k]) => k !== 'wasm'),
    );
  const finished = assets[`content/display/${String(get(m, 'asset'))}`];
  if (
    has(host, 'artwork') &&
    !String(host['artwork']).includes('$') &&
    finished !== undefined &&
    artworkFrom(decoder.decode(finished), model.kit.screenTemplate) === host['artwork']
  )
    delete host['artwork']; // the finished svg holds it (rebuild_kit takes it back out)
  files.set('machine.json', utf8.encode(pyDumps(host, { indent: 1, ensureAscii: false }) + '\n'));
  if (notice) files.set('NOTICE.txt', utf8.encode(notice));
  const src = truthy(get(m, 'source')) ? m.source! : null;
  const known = {
    type: 'mod',
    name: m.name,
    machine: m.kind,
    version: src && src.tag,
    source: src && `${src.repository}@${src.commit}`,
    licence: notice ? licenceOf(notice) : 'unknown',
    ...common,
  };
  check(utf8.encode(m.name).length < 48 && (!src || utf8.encode(src.tag).length < 32), `${m.id}: name or tag too long`);
  const slot = m.kind === 'player' ? 'player' : m.kind === 'effect' ? 'effect' : 'engine';
  const users = [m.kind, ...(truthy(get(m, 'features')) ? m.features! : [])];
  const extra: Field[] = [
    ['id', m.id],
    ['order', order],
    ['prefix', m.prefix],
    ['origin', m.origin],
    ['objects', own.length],
    ['code', sum(own, 'code')],
    ['data', sum(own, 'data')],
    ['state', sum(own, 'state')],
    ['services', dash(servicesFor(model.profile, users))],
    ['features', dash(get(m, 'features'))],
    [slot, dash(get(m.identity, slot))],
    ['page', dash(get(m.identity, 'page'))],
    ['catalog_number', catalogNumber(m) ?? '-'],
    ['asset', truthy(get(m, 'asset')) ? String(m.asset) : '-'],
    ['presets', Object.keys(assets).filter((p) => p.includes('/presets/') || p.includes('/aux_presets/')).length],
  ];
  if (src) extra.push(['maintainer', src.maintainer]);
  const cpu = get<unknown>(m, 'cpu');
  if (isObject(cpu) && (get(cpu, 'peak_percent') ?? null) !== null)
    extra.push(['cpu_peak_percent', cpu['peak_percent'] as number]);
  return { files, known, extra };
}

async function writeExtension(model: Model, e: Extension, order: number, common: Record<string, string>) {
  const o = model.objects.find((x) => x.extension === e.id);
  const rows: (string | number)[][] = [],
    files = new Map<string, Uint8Array>();
  if (o) {
    rows.push([o.order, 'extension', `objects/${o.name}`]);
    files.set(`objects/${o.name}`, o.blob);
  }
  files.set('objects.txt', table('order role path', rows));
  const key = processorKey(e);
  const host: Record<string, Json> = Object.fromEntries(Object.entries(e).filter(([k]) => k !== 'id')) as Record<
    string,
    Json
  >;
  if (key) {
    const proc = e[key] as Processor;
    const art = decode64(proc.artwork);
    check(
      (await sha256(art)) === proc.sha256 && /^[a-z][a-z0-9_]*\.svg$/.test(proc.asset),
      `${e.id}: processor artwork differs from its hash`,
    );
    files.set(`content/display/${proc.asset}`, art);
    host[key] = Object.fromEntries(Object.entries(proc).filter(([k]) => k !== 'artwork')) as Record<string, Json>;
  }
  files.set('extension.json', utf8.encode(pyDumps(host, { indent: 1, ensureAscii: false }) + '\n'));
  const row = extensionRow(model, e);
  const known = {
    type: 'mod',
    name: e.name,
    machine: 'extension',
    version: null,
    source: null,
    licence: 'unknown',
    ...common,
  };
  const extra: Field[] = [
    ['id', e.id],
    ['order', order],
    ['origin', e.origin],
    ['objects', o ? 1 : 0],
    ...(['code', 'data', 'state'] as const).map((k): Field => [k, o ? o[k] : 0]),
    ...row
      .slice(0, -1)
      .filter((f) => f !== 'link=-' && !f.startsWith('labels='))
      .map((f): Field => {
        const at = f.indexOf('=');
        return [f.slice(0, at), f.slice(at + 1)];
      }),
  ];
  return { files, known, extra };
}

/** A refusal of Open-XY's own builder, or a fault in it, said as its own. */
function upstream(what: string, error: unknown): KitError {
  return new KitError(`${what}: ${error instanceof Error ? error.message : String(error)}`);
}

/**
 * The base and every mod package of a kit. core: Open-XY's builder of the kit's release (it checks the kit first);
 * sdkSource: the base's source= (the Open-XY repository@revision of the kit's SDK, when known).
 */
export async function splitKit(kit: Kit, core: Core, sdkSource: string | null = null): Promise<SplitSet> {
  const opened = await core.openKit(kit).catch((error: unknown) => {
    throw upstream("Open-XY's builder refused the kit", error);
  });
  let model: Model;
  try {
    model = await analyse(kit, opened.objects as (KitObject & ReadObject)[]);
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    throw new KitError(
      `This app cannot package Open-XY's kit ${kit.version} for the OP-XY (${reason}). Check for an XY Forever update.`,
    );
  }
  const { profile, generation } = model;
  const catalog = core.decode64(opened.stockCatalog);
  const assets: Record<string, Record<string, Uint8Array>> = {};
  for (const m of opened.machines) {
    const files = await core
      .buildAssets(opened, [m.id], catalog, core.assignNumbers(opened, [m.id], null), {}, [])
      .catch((error: unknown) => {
        throw upstream(`Open-XY's builder could not finish ${m.id}`, error);
      });
    delete files['content/op1_factory.json'];
    assets[m.id] = files;
  }
  const common = { generation, profile: profile.id };
  const expanded = core.offersExpandedMemory(profile)
    ? ((profile['experimental_memory'] as Rec)['module_base'] as number)
    : null;
  const packages: [string, { files: Map<string, Uint8Array>; known: Rec; extra: Field[] }][] = [
    ['base', writeBase(model, common, sdkSource, await sha256(model.profileBytes), expanded)],
  ];
  const notices = noticeSections(kit.notices ?? '', new Set(kit.machines.map((m) => m.name)));
  kit.machines.forEach((m, order) => {
    packages.push([m.id, writeMachine(model, m, order, common, assets[m.id] ?? {}, notices.get(m.name))]);
  });
  for (const [order, e] of (kit.extensions ?? []).entries())
    packages.push([`ext-${e.id}`, await writeExtension(model, e, order, common)]);
  const files = new Map<string, Uint8Array>();
  for (const [name, { files: own, known, extra }] of packages) {
    own.set('manifest.json', manifest(known as Record<string, string | number | null>, extra));
    for (const [path, data] of await withList(own)) files.set(`${name}/${path}`, data);
  }
  return { profile: profile.id, generation, files };
}
