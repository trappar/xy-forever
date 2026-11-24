// The browser's kit split (src/kit/split.ts) on the command line, for the host comparison (tests/test_kit_browser.py).
// Each --kit N (the Nth, from 0) is written as `modkit split --out OUT/N` writes it, OUT/N/mods/<profile>/<generation>/,
// and prints one JSON line: {"generation", "root"}, or {"error"} when the kit is refused. The finished content comes
// from the vendored core.mjs, or from the Nth --assets JSON file ({machine id: {path: base64}}).
//   node web/scripts/kit-split.ts --kit KIT.json[.gz] ... --out OUT [--sdk-source URL@COMMIT] [--assets A.json ...]
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { parseArgs } from 'node:util';
import { gunzipSync } from 'node:zlib';
import { finishedContent } from '../src/kit/assets.ts';
import { KitError } from '../src/kit/errors.ts';
import { decode64, splitKit } from '../src/kit/split.ts';
import type { Finished, Kit } from '../src/kit/split.ts';

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

async function given(path: string): Promise<Finished> {
  const files = JSON.parse(await readFile(path, 'utf8')) as Record<string, Record<string, string>>;
  return Object.fromEntries(
    Object.entries(files).map(([id, own]) => [
      id,
      Object.fromEntries(Object.entries(own).map(([name, data]) => [name, decode64(data)])),
    ]),
  );
}

for (const [n, path] of values.kit.entries()) {
  let raw = await readFile(path);
  if (raw[0] === 0x1f && raw[1] === 0x8b) raw = gunzipSync(raw);
  const assets = values.assets[n];
  try {
    const kit = JSON.parse(raw.toString('utf8')) as Kit;
    const set = await splitKit(kit, assets ? () => given(assets) : finishedContent, values['sdk-source'] ?? null);
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
