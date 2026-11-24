// Where the app reads Open-XY: its published kit, fresh on every load (a newer kit's catalog shows without a new
// deployment of this app), and the builder modules of that kit's release (core.mjs with graphics.mjs and
// default_preset.json beside it, in the repository's layout), which check the kit and finish each machine's content.
// Open-XY's site lets the production origin read both (#185). In development the dev server proxies the same paths
// (vite.config.ts); until open-xy.com publishes the modules, development alone falls back to the release tag on
// jsDelivr. When the live kit cannot be read or split, the pinned 0.0.7 kit and its builder come from this site
// (pinned.ts, #183).
import type { Core } from './core.ts';
import { PINNED_VERSION, pinnedKit } from './pinned.ts';
import type { Kit } from './split.ts';

const KIT_URL = import.meta.env.DEV ? '/kit.json.gz' : 'https://open-xy.com/kit.json.gz';
const sdkBases = (version: string): string[] =>
  import.meta.env.DEV
    ? [`/sdk/${version}/`, `https://cdn.jsdelivr.net/gh/etcetc000/open-xy@v${version}/`]
    : [`https://open-xy.com/sdk/${version}/`];

/** Open-XY could not be read: the message says what to do. */
class KitUnavailable extends Error {
  constructor(reason: string) {
    super(
      `Open-XY's catalog could not be downloaded (${reason}). Check the internet connection and try again; ` +
        'packages from a folder can still be added below.',
    );
    this.name = 'KitUnavailable';
  }
}

/** The published kit as JSON (served gzip-compressed, as application/gzip, or already decoded). */
export async function fetchKit(): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(KIT_URL, { cache: 'no-cache' });
  } catch {
    throw new KitUnavailable('no answer from open-xy.com');
  }
  if (!response.ok) throw new KitUnavailable(`open-xy.com answered ${response.status}`);
  let bytes = new Uint8Array(await response.arrayBuffer());
  try {
    if (bytes[0] === 0x1f && bytes[1] === 0x8b)
      bytes = new Uint8Array(
        await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer(),
      );
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch {
    throw new KitUnavailable('the download is damaged');
  }
}

/** Open-XY's builder of the kit's release (its version, 0.0.7: the v0.0.7 tag's core.mjs). */
export async function loadCore(version: unknown): Promise<Core> {
  if (typeof version !== 'string' || !/^\d+\.\d+\.\d+$/.test(version))
    throw new KitUnavailable('the download names no Open-XY release');
  for (const base of sdkBases(version)) {
    try {
      return (await import(/* @vite-ignore */ new URL('builder/core.mjs', new URL(base, location.href)).href)) as Core;
    } catch {
      // the next source, if any
    }
  }
  throw new KitUnavailable(`Open-XY ${version}'s builder could not be loaded`);
}

/** This site's copy of the pinned Open-XY release (scripts/web_open_xy.py stages it). */
const pinnedBase = (): string => new URL(`${import.meta.env.BASE_URL}open-xy/${PINNED_VERSION}/`, location.href).href;

async function download(url: string): Promise<Uint8Array> {
  const response = await fetch(url, { cache: 'no-cache' });
  if (!response.ok) throw new Error(`${url} answered ${response.status}`);
  return new Uint8Array(await response.arrayBuffer());
}

/** The pinned Open-XY 0.0.7 kit, refilled from Open-XY's repository, and that release's builder from this site. */
export async function loadPinned(): Promise<{ kit: Kit; core: Core }> {
  const kit = await pinnedKit(pinnedBase(), download);
  const core = (await import(/* @vite-ignore */ new URL('builder/core.mjs', pinnedBase()).href)) as Core;
  return { kit, core };
}
