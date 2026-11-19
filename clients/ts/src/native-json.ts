/** Native record schemas. JSON.parse owns syntax; this adapter checks native semantics and bounds. */
export const PACKAGE_SCHEMA = 'xy-forever.package';
export const CONFIG_SCHEMA = 'xy-forever.config';
export const DOCUMENT_BYTES = 8448, PROPERTY_COUNT = 128, FIELD_COUNT = 32, LIST_COUNT = 96, MAX_DEPTH = 4;
const numbers = new Set('bytes machines module_base module_bytes stock_bytes edits objects extensions code data state runtime_code runtime_data runtime_state catalog_engine_at catalog_fx_at catalog_seq_at stock_records reserved_bytes heap_fill_base heap_fill_bytes expanded_module_base order link presets'.split(' '));
const addresses = new Set(['module_base', 'heap_fill_base', 'expanded_module_base']);
const optionalNumbers = new Set(['engine', 'effect', 'player', 'page']);
const lists = new Set(['services', 'features', 'claims', 'accessors']);
const fail = (message: string): never => { throw new Error(message); };
const need = (condition: unknown, message: string): void => { if (!condition) fail(message); };
type ObjectValue = Record<string, unknown>;
function object(value: unknown): ObjectValue {
  need(value !== null && typeof value === 'object' && !Array.isArray(value), 'JSON object required');
  return value as ObjectValue;
}
function uint(value: unknown): number {
  need(typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 0xffffffff,
    'JSON field type or integer range');
  return value as number;
}
function text(value: unknown, maximum = 127): string {
  need(typeof value === 'string' && value.length <= maximum && /^[\x20-\x7e]*$/.test(value), 'JSON field text');
  return value as string;
}
// JSON.parse has already accepted the document. This scan checks duplicate names
// before its last-value-wins object representation can hide them. It does not
// supply a second JSON syntax validator or reject harmless parser permissiveness.
function duplicates(source: string, schema: string): void {
  const scopes: { keys: Set<string> | null; role: string }[] = [];
  let pending = '';
  for (let at = 0; at < source.length; at++) {
    const c = source[at];
    if (c === '{' || c === '[') { scopes.push({ keys: c === '{' ? new Set() : null, role: pending }); pending = ''; }
    else if (c === '}' || c === ']') { scopes.pop(); pending = ''; }
    else if (c === ',') pending = '';
    else if (c === '"') {
      const start = at++;
      while (source[at] !== '"') { if (source[at] === '\\') at++; at++; }
      let next = at + 1;
      while (/\s/.test(source[next] ?? 'x')) next++;
      if (source[next] === ':') {
        const name = JSON.parse(source.slice(start, at + 1)) as string, seen = scopes.at(-1)?.keys;
        need(seen && !seen.has(name), 'duplicate JSON field');
        seen!.add(name);
        pending = name;
      } else pending = '';
    } else if (c !== undefined && /[-0-9]/.test(c)) {
      const start = at;
      while (/[-0-9.eE+]/.test(source[at] ?? 'x')) at++;
      const literal = source.slice(start, at--), scope = scopes.at(-1);
      const integer = (scopes.length === 1 && pending === 'schema_version') ||
        (schema === PACKAGE_SCHEMA && ((scopes.length === 1 && (numbers.has(pending) || optionalNumbers.has(pending))) ||
          (scopes.length === 2 && scope?.role === 'catalog_number'))) ||
        (schema === CONFIG_SCHEMA && scopes.length === 2 && scope?.role === 'built' && ['set', 'count'].includes(pending));
      // JSON numbers have one JS runtime type. Preserve the native integer field
      // representation before JSON.parse can round a fractional value into one.
      if (integer) need(/^(0|[1-9][0-9]*)$/.test(literal), 'JSON integer representation');
      pending = '';
    }
  }
}
export function parseNative(data: Uint8Array, schema: string): ObjectValue {
  need(data.length > 0 && data.length <= DOCUMENT_BYTES, 'JSON document size');
  const source = new TextDecoder('utf-8', { fatal: true }).decode(data);
  let escaped = false;
  for (const c of source) {
    need(c !== '\0', 'JSON syntax');
    need(!(escaped && c === 'u'), 'Unicode escapes unsupported in native JSON');
    escaped = !escaped && c === '\\';
  }
  const root = object(JSON.parse(source));
  duplicates(source, schema);
  let count = 0;
  const pending: [unknown, number][] = [[root, 0]];
  while (pending.length) {
    const [value, depth] = pending.pop()!;
    need(++count <= PROPERTY_COUNT, 'JSON property limit');
    if (typeof value === 'string') text(value);
    if (value !== null && typeof value === 'object') {
      need(depth < MAX_DEPTH, 'JSON nesting limit');
      const values = Object.values(value);
      need(values.length <= (Array.isArray(value) ? LIST_COUNT : FIELD_COUNT + 2), 'JSON capacity');
      if (!Array.isArray(value)) need(Object.keys(value).every((key) => key.length <= 31), 'JSON field name limit');
      pending.push(...values.map((v): [unknown, number] => [v, depth + 1]));
    }
  }
  need(root['schema'] === schema, 'unsupported JSON schema');
  need(root['schema_version'] === 1, 'unsupported JSON schema version');
  return root;
}
function scalar(key: string, value: unknown): string {
  if (numbers.has(key) || optionalNumbers.has(key)) {
    if (value === null && optionalNumbers.has(key)) return '-';
    const n = uint(value);
    return addresses.has(key) ? `0x${n.toString(16).padStart(8, '0')}` : String(n);
  }
  if (lists.has(key)) {
    need(Array.isArray(value) && value.length <= LIST_COUNT, 'JSON list type or limit');
    const items = (value as unknown[]).map((v) => text(v));
    items.forEach((v) => need(v && !v.includes(','), 'JSON list item'));
    return items.join(',') || '-';
  }
  if (key === 'catalog_number') {
    if (value === null) return '-';
    need(Array.isArray(value) && value.length === 2, 'JSON catalog_number pair');
    return (value as unknown[]).map(uint).join(',');
  }
  if (key === 'cpu_peak_percent') {
    need(typeof value === 'number' && Number.isFinite(value), 'JSON CPU percent');
    return String(value);
  }
  return text(value);
}
export function nativePackageText(data: Uint8Array): Uint8Array {
  const root = parseNative(data, PACKAGE_SCHEMA);
  const fields = Object.entries(root).filter(([k]) => !['schema', 'schema_version'].includes(k));
  need(root['type'] !== undefined && fields.length <= FIELD_COUNT, 'JSON package fields');
  const result = fields.map(([key, value]) => {
    need(/^[a-z0-9_]{1,31}$/.test(key), 'JSON field name');
    const rendered = scalar(key, value);
    need(rendered.length <= 127, 'JSON field value limit');
    return `${key}=${rendered}\n`;
  }).join('');
  need(result.length <= 4096, 'JSON capacity');
  return new TextEncoder().encode(result);
}
export function nativePackageDocument(fields: ObjectValue): Uint8Array {
  const result: ObjectValue = { schema: PACKAGE_SCHEMA, schema_version: 1 };
  for (const [key, initial] of Object.entries(fields)) {
    if (initial === undefined || initial === null) continue;
    let value: unknown = initial;
    if (numbers.has(key) || optionalNumbers.has(key)) {
      value = value === '-' && optionalNumbers.has(key) ? null : Number(value);
    } else if (lists.has(key)) value = value === '' || value === '-' ? [] : String(value).split(',');
    else if (key === 'catalog_number') value = value === '-' ? null : String(value).split(',').map(Number);
    else if (key === 'cpu_peak_percent') value = Number(value);
    result[key] = value;
  }
  const data = new TextEncoder().encode(`${JSON.stringify(result)}\n`);
  nativePackageText(data);
  return data;
}
export function nativeConfigText(data: Uint8Array): Uint8Array {
  const root = parseNative(data, CONFIG_SCHEMA), desired = object(root['desired']);
  const lines = ['format=1\n'];
  for (const key of ['base', 'profile', 'generation', 'memory']) {
    const value = text(desired[key], 31);
    if (key !== 'memory') need((key === 'base' ? /^[a-z0-9_-]+$/ : /^(?:[a-z0-9][a-z0-9._-]*)?$/).test(value), 'JSON configuration identifier');
    need(key !== 'memory' || ['standard', 'expanded'].includes(value), 'JSON memory choice');
    lines.push(`${key}=${value}\n`);
  }
  need(typeof desired['unverified'] === 'boolean', 'JSON unverified boolean');
  lines.push(`unverified=${desired['unverified'] ? 1 : 0}\n`);
  need(root['built'] !== undefined, 'JSON built object or null');
  if (root['built'] !== null) {
    const built = object(root['built']);
    for (const key of ['base', 'generation', 'memory', 'slot', 'sha256']) text(built[key], key === 'sha256' ? 64 : 31);
    need(['standard', 'expanded'].includes(String(built['memory'])) && ['mods-a', 'mods-b'].includes(String(built['slot'])), 'JSON built state');
    need(/^[a-z0-9_-]+$/.test(String(built['base'])) && /^[a-z0-9][a-z0-9._-]*$/.test(String(built['generation'])) &&
      /^[0-9a-f]{64}$/.test(String(built['sha256'])), 'JSON generated build identity');
    const count = uint(built['count']), set = uint(built['set']);
    need(count <= LIST_COUNT, 'JSON build selection count');
    lines.push(`built=${String(built['base'])} ${String(built['generation'])} ${set.toString(16).padStart(8, '0')} ${count}${built['memory'] === 'expanded' ? ' expanded' : ''}\nslot=${String(built['slot'])}\nstamp=${String(built['sha256'])}\n`);
  }
  const mods = desired['mods'];
  need(Array.isArray(mods) && mods.length <= LIST_COUNT, 'JSON mods list');
  for (const mod of mods as unknown[]) need(typeof mod === 'string' && /^[a-z0-9_.-]{1,31}$/.test(mod), 'JSON mod identifier');
  need(new Set(mods as string[]).size === (mods as string[]).length, 'duplicate JSON selection');
  lines.push(`mods=${(mods as string[]).join(',')}\n`);
  const result = new TextEncoder().encode(lines.join(''));
  need(result.length <= 2048, 'JSON capacity');
  return result;
}
export function nativeConfigDocument(fields: Record<string, string>): Uint8Array {
  const desired = { base: fields['base'] ?? '', profile: fields['profile'] ?? '', generation: fields['generation'] ?? '',
    memory: fields['memory'] ?? 'standard', unverified: fields['unverified'] === '1', mods: fields['mods']?.split(',').filter(Boolean) ?? [] };
  let built: ObjectValue | null = null;
  if (fields['built'] && ['mods-a', 'mods-b'].includes(fields['slot'] ?? '')) {
    const parts = fields['built'].split(' ');
    need(parts.length === 4 || parts.length === 5, 'legacy built state');
    built = { base: parts[0], generation: parts[1], set: Number.parseInt(parts[2]!, 16), count: Number(parts[3]),
      memory: parts[4] === 'expanded' ? 'expanded' : 'standard', slot: fields['slot'], sha256: fields['stamp'] ?? '' };
  }
  const data = new TextEncoder().encode(`${JSON.stringify({ schema: CONFIG_SCHEMA, schema_version: 1, desired, built })}\n`);
  nativeConfigText(data);
  return data;
}
