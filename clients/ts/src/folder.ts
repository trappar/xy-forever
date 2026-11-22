// A package set in a folder on disk (Node): pushPackages(client, folderSource(dir)), dir being the
// generation folder modkit split wrote.
import { readFile, readdir } from 'node:fs/promises';
import { sep } from 'node:path';
import type { PackageSource } from './mods.ts';

export function folderSource(dir: string): PackageSource {
  return {
    paths: async () =>
      (await readdir(dir, { recursive: true, withFileTypes: true }))
        .filter((e) => e.isFile())
        .map((e) =>
          `${e.parentPath}${sep}${e.name}`
            .slice(dir.replace(/[\\/]+$/, '').length + 1)
            .split(sep)
            .join('/'),
        ),
    read: async (path) => new Uint8Array(await readFile(`${dir}/${path}`)),
  };
}
