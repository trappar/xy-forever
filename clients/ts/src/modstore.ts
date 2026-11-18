// The device's mod package store (#124; xy_forever/modstore.py, docs/storage/mods.md): MODS_ROOT/<generation>/
// store.txt names its version ("version=1"); each package folder holds its files flat, a top-level file under its
// own name, any other under its stored name (its SHA-256's first 12 hex digits plus its extension), so every file
// sits 4 folders below /yaffs2 whatever the package's folders are. files.sha256 maps a path to its stored name;
// the device job reads it that way (native/mod_job.hpp stored_name). No store.txt: an older layout.
import type { CallOptions, Client } from './client.ts';
import { isXyfError } from './errors.ts';

export const STORE_VERSION = 1;
export const STORE_FILE = 'store.txt';
export const STORE_TEXT = new TextEncoder().encode(`version=${STORE_VERSION}\n`);

/** A package file's name in the store: path as it is without a folder, else sha's first 12 hex digits plus the
 * extension of its last part (from its last '.', not the part's first character, then 1-7 of A-Z a-z 0-9). */
export function storedName(path: string, sha: string): string {
  const slash = path.lastIndexOf('/');
  if (slash < 0) return path;
  const last = path.slice(slash + 1), dot = last.lastIndexOf('.');
  const ext = dot > 0 && /^\.[A-Za-z0-9]{1,7}$/.test(last.slice(dot)) ? last.slice(dot) : '';
  return sha.slice(0, 12).toLowerCase() + ext;
}

/** store.txt's version (0 when its first line names none). */
export function storeVersion(data: Uint8Array | null): number {
  const m = /^version=([0-9]{1,9})(?:[\r\n]|$)/.exec(new TextDecoder().decode(data ?? new Uint8Array()));
  return m ? Number(m[1]) : 0;
}

export interface RemoteTree { files: Map<string, number>; dirs: string[] }
/** Every file below folder with its size, and every folder (parents first), relative; null when folder does not exist. */
export async function remoteTree(client: Client, folder: string, options: CallOptions): Promise<RemoteTree | null> {
  const files = new Map<string, number>(), dirs: string[] = [], todo = [''];
  while (todo.length) {
    const rel = todo.shift()!;
    let entries;
    try {
      entries = await client.listDir(rel ? `${folder}/${rel}` : folder, options);
    } catch (error) {
      if (!rel && isXyfError(error, 'not_found')) return null;
      throw error;
    }
    for (const e of entries) {
      const path = rel ? `${rel}/${e.name}` : e.name;
      if (e.kind === 'file') files.set(path, e.size);
      else if (e.kind === 'folder') { dirs.push(path); todo.push(path); }
    }
  }
  return { files, dirs };
}

/** The store version of the generation folder genPath (has: it holds store.txt); 0 without one. */
export async function generationVersion(client: Client, genPath: string, has: boolean, options: CallOptions): Promise<number> {
  return has ? storeVersion(await client.readFile(`${genPath}/${STORE_FILE}`, options)) : 0;
}

/** Why a push into genPath (tree: its remoteTree) is refused (another layout), or null; writes store.txt into a new folder. */
export async function storeRefusal(client: Client, genPath: string, tree: RemoteTree | null, options: CallOptions): Promise<string | null> {
  const version = await generationVersion(client, genPath, !!tree?.files.has(STORE_FILE), options);
  if (version === STORE_VERSION) return null;
  if (version > STORE_VERSION) return `${genPath}: store version ${version}, newer than this client writes: update it`;
  if (tree && (tree.files.size || tree.dirs.length)) {
    return `${genPath} holds an older layout (no ${STORE_FILE}): remove it (removePackage), then push again`;
  }
  await client.writeFile(`${genPath}/${STORE_FILE}`, STORE_TEXT, options);
  return null;
}
