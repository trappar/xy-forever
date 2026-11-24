// The Blackfin ET_REL subset a kit object may use, read as xy_forever/modkit.py read_object and object_summary do:
// the same checks and the same exports, imports, absolute symbols and code/data/state bytes.
import { KitError, check } from './errors.ts';

const RELOCATIONS = new Set([6, 7, 10, 13, 18]); // LUIMM16, HUIMM16, PCREL24, PCREL24_JUMP_L, BYTE4_DATA
export const SHN_ABS = 0xfff1;
const STT_FILE = 4;
const utf8 = new TextDecoder('utf-8', { fatal: true });

interface Section {
  index: number;
  name: string;
  type: number;
  flags: number;
  offset: number;
  size: number;
  link: number;
  info: number;
  stride: number;
  group: 'rodata' | 'data' | 'bss' | 'text' | null;
}
interface Symbol {
  name: string;
  binding: number;
  type: number;
  section: number;
}

function readObject(data: Uint8Array, name: string): { sections: Section[]; symbols: Symbol[] } {
  check(52 <= data.length && data.length <= 1 << 20, `${name}: object size outside bounds`);
  const magic = [0x7f, 0x45, 0x4c, 0x46, 1, 1];
  check(
    magic.every((b, i) => data[i] === b),
    `${name}: not a Blackfin relocatable object`,
  );
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const u16 = (at: number): number => view.getUint16(at, true),
    u32 = (at: number): number => view.getUint32(at, true);
  check(u16(16) === 1 && u16(18) === 106, `${name}: not a Blackfin relocatable object`);
  const start = u32(32),
    count = u16(48),
    namesIndex = u16(50);
  check(
    u16(46) === 40 && count > 0 && count < 4096 && namesIndex < count && start + count * 40 <= data.length,
    `${name}: invalid section table`,
  );
  const sections: Section[] = [];
  for (let i = 0; i < count; i++) {
    const at = start + i * 40;
    const type = u32(at + 4),
      offset = u32(at + 16),
      size = u32(at + 20);
    check(type === 8 || offset + size <= data.length, `${name}: section exceeds file`);
    sections.push({
      index: i,
      name: String(u32(at)),
      type,
      flags: u32(at + 8),
      offset,
      size,
      link: u32(at + 24),
      info: u32(at + 28),
      stride: u32(at + 36),
      group: null,
    });
  }
  const string = (table: Section, at: number): string => {
    const blob = data.subarray(table.offset, table.offset + table.size);
    const end = blob.indexOf(0, at);
    check(at < blob.length && end >= 0, `${name}: invalid string offset`);
    try {
      return utf8.decode(blob.subarray(at, end));
    } catch {
      throw new KitError(`${name}: invalid string`);
    }
  };
  for (const s of sections) {
    s.name = string(sections[namesIndex]!, Number(s.name));
    if (s.flags & 2) {
      check(!(s.flags & ~7), `${name}: unsupported allocated section flags`);
      const n = s.name;
      s.group = /^(\.rodata(\.|$)|\.data\.rel\.ro(\.|$))/.test(n)
        ? 'rodata'
        : /^(\.data(\.|$)|\.sdata(\.|$))/.test(n)
          ? 'data'
          : /^(\.bss(\.|$)|\.sbss(\.|$))/.test(n)
            ? 'bss'
            : /^\.text(\.|$)/.test(n)
              ? 'text'
              : null;
      check(s.group && s.type === (s.group === 'bss' ? 8 : 1), `${name}: unsupported allocated section ${n}`);
    }
  }
  const tables = sections.filter((s) => s.type === 2);
  check(tables.length === 1, `${name}: expected one symbol table`);
  const table = tables[0]!;
  check(table.stride === 16 && table.size % 16 === 0 && table.link < count, `${name}: invalid symbol table`);
  const symbols: Symbol[] = [];
  for (let at = table.offset; at < table.offset + table.size; at += 16) {
    const shndx = u16(at + 14),
      info = data[at + 12]!;
    check(shndx < count || shndx === SHN_ABS, `${name}: COMMON or extended symbol sections are unsupported`);
    symbols.push({ name: string(sections[table.link]!, u32(at)), binding: info >> 4, type: info & 15, section: shndx });
  }
  for (const s of sections) {
    check(s.type !== 9, `${name}: REL without addends is unsupported`);
    if (s.type !== 4) continue;
    check(
      s.link === table.index && s.info < count && s.stride === 12 && s.size % 12 === 0,
      `${name}: invalid relocation table`,
    );
    for (let at = s.offset; at < s.offset + s.size; at += 12) {
      const info = u32(at + 4);
      check(RELOCATIONS.has(info & 0xff) && info >>> 8 < symbols.length, `${name}: unsupported relocation`);
    }
  }
  return { sections, symbols };
}

export interface ObjectSummary {
  readonly exports: ReadonlySet<string>;
  readonly imports: ReadonlySet<string>;
  readonly absolute: readonly string[];
  readonly code: number;
  readonly data: number;
  readonly state: number;
}

/** Exports, imports, absolute symbols and code/data/state bytes of one object. */
export function objectSummary(data: Uint8Array, name: string): ObjectSummary {
  const o = readObject(data, name);
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
