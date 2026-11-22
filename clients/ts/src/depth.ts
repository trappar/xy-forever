// The depth rule (#124, docs/SAFETY.md; xy_forever/depth.py): no file more than MAX_FOLDERS folders below
// /yaffs2. After the stock dev-menu MTP has walked a file 7 folders deep, the dev menu's Track 5 in that boot
// hangs and the next boot needs a storage repair (8: unreadable; 9: it crashes). XY Forever (protocol 1.16)
// refuses deeper folders and files; these refuse first, with the reason. Reads, listing and deletes work at any depth.
import type { DirEntry } from './client.ts';

export const MAX_FOLDERS = 6;

/** The folders below /yaffs2 that a device path sits in (a folder: counting itself). */
export function foldersOf(path: string, folder = false): number {
  return (
    path
      .slice('/yaffs2'.length)
      .split('/')
      .filter((n) => n !== '').length - (folder ? 0 : 1)
  );
}

/** null when path (a folder: with `below` levels of folders inside it) keeps the rule, else the reason. */
export function depthRefusal(path: string, folder = false, below = 0): string | null {
  if (foldersOf(path, folder) + below <= MAX_FOLDERS) return null;
  return (
    `${path}${below ? ' with the folders inside it' : ''} is more than ${MAX_FOLDERS} folders below /yaffs2 ` +
    "(deeper hangs the dev menu's MTP, then Track 5)"
  );
}

/** Levels of folders inside a device folder (0: none), counting at most `most`. */
export async function foldersBelow(
  listDir: (path: string) => Promise<readonly DirEntry[]>,
  folder: string,
  most = MAX_FOLDERS + 1,
): Promise<number> {
  let deepest = 0;
  for (const e of await listDir(folder)) {
    if (e.kind === 'folder' && most > 0)
      deepest = Math.max(deepest, 1 + (await foldersBelow(listDir, `${folder}/${e.name}`, most - 1)));
  }
  return Math.min(deepest, most);
}

/** Whether a device of this protocol version reads mod packages from mods/<generation>/ (1.16 and later). */
export function readsFlatMods(proto: string): boolean {
  const [major, minor] = proto.split('.').map(Number);
  return major === 1 ? (minor ?? 0) >= 16 : (major ?? 0) > 1;
}
