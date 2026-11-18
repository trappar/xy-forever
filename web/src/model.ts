// The pure parts of the app (no DOM, no device): the saved configuration, mod grouping, path rules,
// and the plain-words summaries. Unit tested in test/model.test.ts.
import { MAX_FOLDERS, PROTECTED_NAMES, WRITE_ROOTS, foldersOf, inWriteArea } from 'xy-forever-client';
import type { HeldPackage } from 'xy-forever-client';

export const CONFIG_FILE = '/yaffs2/xy-forever/config.txt';
export const CONFIG_OLD = '/yaffs2/xy-forever/config.old';
export const CONTENT_STATE = '/yaffs2/xy-forever/content-state.txt';
export const CONTENT_STATE_OLD = '/yaffs2/xy-forever/content-state.old';
export { WRITE_ROOTS };

/** The write rule in plain words (since protocol 1.13): all of /yaffs2 except the protected names. */
export const WRITE_RULE = `under ${WRITE_ROOTS.join(' and ')}, never ${PROTECTED_NAMES.join(' or ')} (or inside it)`;

/** key=value lines (LF or CRLF; # comments; the last of a duplicate key wins). */
export function parseKeyValues(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split('\n')) {
    const line = raw.replace(/\r$/, '').trim();
    const eq = line.indexOf('=');
    if (line.startsWith('#') || eq <= 0) continue;
    out[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
  }
  return out;
}

/** The saved mod configuration (config.txt: base, profile, generation, unverified, slot, mods, ...). */
export interface SavedConfig {
  readonly base: string | null;
  readonly profile: string | null;
  readonly generation: string | null;
  readonly unverified: boolean;
  /** The library slot the mods were built into, null: not built yet. */
  readonly slot: string | null;
  /** The mod ids that are on. */
  readonly mods: readonly string[];
  readonly raw: Readonly<Record<string, string>>;
}

export function parseConfig(text: string): SavedConfig {
  const raw = parseKeyValues(text);
  const opt = (k: string): string | null => (raw[k] ?? '') || null;
  return {
    base: opt('base'), profile: opt('profile'), generation: opt('generation'), unverified: raw['unverified'] === '1',
    slot: opt('slot'), mods: (raw['mods'] ?? '').split(',').map((s) => s.trim()).filter(Boolean), raw,
  };
}

/** The saved configuration in plain words. */
export function configText(config: SavedConfig | null): string {
  if (!config) return 'No mods saved: the OP-XY boots its official firmware setup.';
  const on = config.mods.length ? `${config.mods.length} mod${config.mods.length === 1 ? '' : 's'} on (${config.mods.join(', ')})` : 'all mods off';
  const built = config.slot ? `built into library entry ${config.slot}` : 'not built yet';
  return `${on}, ${built}.`;
}

/** content-state.txt in plain words: no file or official, installed, switching (a cut switch). */
export function contentText(text: string | null, oldExists: boolean): string {
  if (text === null) return oldExists ? 'Needs repair: a content switch did not finish.' : 'Official content.';
  const s = parseKeyValues(text);
  if (s['state'] === 'installed') return `Mod content installed for ${s['entry'] ?? 'an unknown entry'}.`;
  if (s['state'] === 'switching') return `Needs repair: the switch to ${s['target'] ?? 'another entry'} did not finish.`;
  if (s['state'] === 'official' || !s['state']) return 'Official content.';
  return `Unknown content state "${s['state']}".`;
}

/** Package kinds in the device menu's order (GUIDE: synths, drums, samplers, effects, players, extensions). */
export const KINDS = ['synth', 'drum', 'sampler', 'effect', 'player', 'extension'] as const;
export type Kind = (typeof KINDS)[number] | 'other';
export const KIND_LABEL: Readonly<Record<Kind, string>> = {
  synth: 'Synths', drum: 'Drums', sampler: 'Samplers', effect: 'Effects', player: 'Players', extension: 'Extensions', other: 'Other',
};

export interface ModRow {
  /** The folder name on the device (what removePackage takes). */
  readonly folder: string;
  /** The mod id (config.txt's mods=, mods.apply's machines/extensions). */
  readonly id: string;
  readonly name: string;
  readonly kind: Kind;
  readonly extension: boolean;
  readonly order: number;
  readonly version: string;
  readonly bytes: number;
  readonly complete: boolean;
  /** On in the saved configuration; null when it could not be read. */
  readonly on: boolean | null;
}

export interface ModGroup { readonly kind: Kind; readonly label: string; readonly rows: readonly ModRow[] }

/** One held package as a mod row; null for the base package (not a mod). */
export function modRow(p: HeldPackage, config: SavedConfig | null | undefined): ModRow | null {
  const type = p.info['type'];
  if (p.name === 'base' || type === 'base') return null;
  const machine = (p.info['machine'] ?? '').toLowerCase();
  const kind: Kind = (KINDS as readonly string[]).includes(machine) ? machine as Kind : p.name.startsWith('ext-') ? 'extension' : 'other';
  const extension = kind === 'extension';
  const id = p.info['id'] || (extension ? p.name.replace(/^ext-/, '') : p.name);
  return {
    folder: p.name, id, name: p.info['name'] || p.name, kind, extension, order: Number(p.info['order'] ?? 0) || 0,
    version: p.info['version'] ?? '', bytes: p.bytes, complete: p.complete, on: config === undefined ? null : (config?.mods.includes(id) ?? false),
  };
}

/** Groups packages by kind (menu order), each group by name; empty groups left out. */
export function groupMods(packages: readonly HeldPackage[], config: SavedConfig | null | undefined): ModGroup[] {
  const rows = packages.map((p) => modRow(p, config)).filter((r): r is ModRow => r !== null);
  const order: readonly Kind[] = [...KINDS, 'other'];
  return order.map((kind) => ({
    kind, label: KIND_LABEL[kind],
    rows: rows.filter((r) => r.kind === kind).sort((a, b) => a.name.localeCompare(b.name)),
  })).filter((g) => g.rows.length > 0);
}

// ---- Paths ------------------------------------------------------------------------------------------

export const ROOT = '/yaffs2';

/** dir + '/' + name, without doubled slashes. */
export function joinPath(dir: string, name: string): string { return `${dir.replace(/\/+$/, '')}/${name.replace(/^\/+/, '')}`; }

/** The parent folder, never above /yaffs2. */
export function parentOf(path: string): string {
  const cut = path.replace(/\/+$/, '').lastIndexOf('/');
  const up = cut > 0 ? path.slice(0, cut) : ROOT;
  return up.length < ROOT.length ? ROOT : up;
}

/** Whether a folder's contents can be changed (the client's inWriteArea for a name inside it). */
export function folderWritable(dir: string): boolean { return inWriteArea(`${dir.replace(/\/+$/, '')}/x`); }

/**
 * Why the device would refuse a change at path, in plain words; null: allowed. Uses the
 * client's inWriteArea and the depth rule (#124: folder: path is a new folder, else a file); the
 * device checks again and stays the authority.
 */
export function changeRefusal(path: string, folder = false): string | null {
  if (inWriteArea(path)) {
    return foldersOf(path, folder) <= MAX_FOLDERS ? null
      : `That is more than ${MAX_FOLDERS} folders below /yaffs2: deeper hangs the dev menu's file transfer.`;
  }
  if (!WRITE_ROOTS.some((r) => path.startsWith(r))) return `Only files under ${WRITE_ROOTS.join(' and ')} can be changed.`;
  if (inWriteArea(path.replace(/^\/yaffs2\//, '/yaffs2/x/'))) return `${PROTECTED_NAMES.join(' and ')} (and what is inside it) are never changed by hand.`;
  return 'That name is not allowed (empty, "." or "..").';
}

/** A name typed by the user for a new file or folder: one path part, no slash. */
export function nameProblem(name: string): string | null {
  if (!name.trim()) return 'Type a name.';
  if (name.includes('/')) return 'A name cannot contain "/".';
  if (name === '.' || name === '..') return 'That name is not allowed.';
  return null;
}

export function human(n: number): string {
  return n < 1024 ? `${n} B` : n < 1 << 20 ? `${(n / 1024).toFixed(1)} KiB` : `${(n / (1 << 20)).toFixed(1)} MiB`;
}

/** A device mode from hello in plain words. */
export function modeText(mode: string | null): string {
  if (mode === null) return 'Unknown';
  const words: Record<string, string> = { menu: 'XY Forever menu', service: 'USB service', mtp: 'MTP mode' };
  return words[mode] ?? mode;
}
