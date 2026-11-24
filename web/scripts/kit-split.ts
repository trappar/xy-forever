// The browser's kit split (src/kit/split.ts) on the command line, for the host comparison (tests/test_kit_browser.py),
// with the vendored Open-XY v0.0.7 builder (src/vendor/open-xy/: the tests' and offline reference; the app loads the
// kit's own release at run time). Each --kit N (the Nth, from 0) is written as `modkit split --out OUT/N` writes it,
// OUT/N/mods/<profile>/<generation>/, and prints one JSON line: {"generation", "root"}, or {"error"} when the kit is
// refused. With the Nth --assets JSON file ({machine id: {path: base64}}) a stand-in builder takes the kit as it is
// (its objects read by readObject) and finishes each machine with those files: the synthetic kits of the host tests.
//   node web/scripts/kit-split.ts --kit KIT.json[.gz] ... --out OUT [--sdk-source URL@COMMIT] [--assets A.json ...]
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { parseArgs } from 'node:util';
import { gunzipSync } from 'node:zlib';
import * as vendored from '../src/vendor/open-xy/builder/core.mjs';
import type { Core } from '../src/kit/core.ts';
import { KitError } from '../src/kit/errors.ts';
import { splitKit } from '../src/kit/split.ts';
import type { Kit } from '../src/kit/split.ts';

const { values } = parseArgs({
  options: {
    kit: { type: 'string', multiple: true, default: [] },
    out: { type: 'string' },
    'sdk-source': { type: 'string' },
    assets: { type: 'string', multiple: true, default: [] },
  },
});
const out = values.out;
if (!values.kit.length || !out) throw new Error('usage: kit-split.ts --kit KIT ... --out OUT [--sdk-source S]');

/** The vendored builder with the kit taken as it is and the given finished content. */
async function standIn(path: string): Promise<Core> {
  const given = JSON.parse(await readFile(path, 'utf8')) as Record<string, Record<string, string>>;
  return {
    ...vendored,
    openKit: async <T extends object>(kit: T): Promise<T> => {
      const k = kit as unknown as Kit;
      const objects = k.objects.map((o) => ({ ...o, ...vendored.readObject(vendored.decode64(o.bytes)) }));
      return { ...k, objects } as unknown as T;
    },
    assignNumbers: () => ({}),
    buildAssets: async (_kit, selection) =>
      Object.fromEntries(
        Object.entries(given[selection[0]!] ?? {}).map(([name, data]) => [name, vendored.decode64(data)]),
      ),
  };
}

for (const [n, path] of values.kit.entries()) {
  let raw = await readFile(path);
  if (raw[0] === 0x1f && raw[1] === 0x8b) raw = gunzipSync(raw);
  const assets = values.assets[n];
  try {
    const kit = JSON.parse(raw.toString('utf8')) as Kit;
    const set = await splitKit(kit, assets ? await standIn(assets) : vendored, values['sdk-source'] ?? null);
    const root = join(out, String(n), 'mods', set.profile, set.generation);
    for (const [name, data] of set.files) {
      await mkdir(dirname(join(root, name)), { recursive: true });
      await writeFile(join(root, name), data);
    }
    console.log(JSON.stringify({ generation: set.generation, root }));
  } catch (error) {
    if (!(error instanceof KitError)) throw error;
    console.log(JSON.stringify({ error: error.message }));
  }
}
