// Each machine's finished content (svg, presets) from Open-XY's own builder, as `modkit split` takes it from
// core.mjs buildAssets: that machine alone, the extended content/op1_factory.json left out. The vendored core.mjs is
// Open-XY v0.0.7's: it opens only kits with its reviewed profile, and refuses any other.
import * as core from '../vendor/open-xy/builder/core.mjs';
import { KitError } from './errors.ts';
import type { Finished, Kit } from './split.ts';

/** The Open-XY release whose builder is vendored: the base's source= for kits of that release. */
export const CORE_RELEASE = {
  version: '0.0.7',
  source: 'https://github.com/etcetc000/open-xy@28dd1a2e36599108c5eecf1a03f3b28601e50b39',
} as const;

export async function finishedContent(kit: Kit): Promise<Finished> {
  const profile = new Uint8Array(await crypto.subtle.digest('SHA-256', core.decode64(kit.profileSource)));
  if (Array.from(profile, (b) => b.toString(16).padStart(2, '0')).join('') !== core.PROFILE_SHA256)
    throw new KitError(
      `Open-XY's kit ${kit.version} is made for another firmware profile than this app reads ` +
        `(Open-XY ${CORE_RELEASE.version}'s). Check for an XY Forever update.`,
    );
  const opened = await core.openKit(kit).catch((error: unknown) => {
    throw new KitError(`Open-XY's builder refused the kit: ${error instanceof Error ? error.message : String(error)}`);
  });
  const catalog = core.decode64(opened.stockCatalog);
  const out: Finished = {};
  for (const m of opened.machines) {
    const numbers = core.assignNumbers(opened, [m.id], null);
    const files = await core.buildAssets(opened, [m.id], catalog, numbers, {}, []);
    delete files['content/op1_factory.json'];
    out[m.id] = files;
  }
  return out;
}
