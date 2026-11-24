// Open-XY's catalog for the Mods page: the published kit and its release's builder downloaded (source.ts), and the
// kit split into XY Forever packages here (split.ts). A kit this app cannot read is refused with the reason; a
// transfer checks the chosen packages again before it sends anything (pushPackages reads localSet).
import { PackageError, infoValues } from 'xy-forever-client';
import type { HeldPackage, PackageSource } from 'xy-forever-client';
import { fetchKit, loadCore } from './source.ts';
import { splitKit } from './split.ts';
import type { Kit } from './split.ts';

export interface Catalog {
  /** The kit's Open-XY release. */
  readonly version: string;
  readonly generation: string;
  /** Every package of the kit as the device lists a held one (groupMods reads these), base first. */
  readonly packages: readonly HeldPackage[];
  /** The generation folder pushPackages sends from. */
  readonly source: PackageSource;
}

/** A generation folder held in memory. */
export function memorySource(files: ReadonlyMap<string, Uint8Array>): PackageSource {
  return {
    paths: () => [...files.keys()],
    read: async (path) => {
      const data = files.get(path);
      if (!data) throw new PackageError(`${path}: not in the package set`);
      return data;
    },
  };
}

export async function loadCatalog(): Promise<Catalog> {
  const read = (await fetchKit()) as Kit;
  const core = await loadCore((read as Partial<Kit> | null)?.version);
  const set = await splitKit(read, core, `https://github.com/etcetc000/open-xy@v${read.version}`);
  const byPackage = new Map<string, Uint8Array[]>();
  for (const [path, data] of set.files) {
    const name = path.slice(0, path.indexOf('/'));
    byPackage.set(name, [...(byPackage.get(name) ?? []), data]);
  }
  const packages = [...byPackage].map(([name, files]): HeldPackage => ({
    name,
    info: infoValues(set.files.get(`${name}/manifest.json`)!, 'manifest.json'),
    files: files.length,
    bytes: files.reduce((n, f) => n + f.length, 0),
    complete: true,
  }));
  return { version: read.version, generation: set.generation, packages, source: memorySource(set.files) };
}
