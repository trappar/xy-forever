// The pinned Open-XY 0.0.7 kit (#183): the Mods page's catalog when Open-XY's live kit cannot be read (open-xy.com
// sends no CORS header yet) or split (a newer kit). This site serves the published 0.0.7 kit with teenage
// engineering's material removed (scripts/web_open_xy.py: the stock catalog empty, every edit's `before` zeroed),
// beside Open-XY v0.0.7's builder; here both parts come back from Open-XY's public repository at that tag, and Open-XY's
// builder checks them (the profile's SHA-256 in openKit, the catalog's in buildAssets). No Vite: the Pages smoke test
// and the tests' split script run it under Node too.
import type { Kit } from './split.ts';

export const PINNED_VERSION = '0.0.7';
/** The stripped kit's JSON (scripts/web_open_xy.py STRIPPED_SHA256). */
const STRIPPED_SHA256 = '7512f995d1e4f2b2c041c971a1dff1a932f8f853422af011d1fd47ca698e60fe';
const RAW = 'https://raw.githubusercontent.com/etcetc000/open-xy/v0.0.7/';
const PROFILE = {
  url: `${RAW}profiles/opxy-1.2.1.json`,
  sha256: '6bd2a299b7490b01657119315361974ea317c80bcac99c088525b08ff3303181',
};
const CATALOG = {
  url: `${RAW}profiles/opxy-1.2.1/content/op1_factory.json`,
  sha256: '36d14444dcc12cac8f33784c4d0e96079cfa0b5042ac9edfe31d34cc9b303ff0',
};

export type Get = (url: string) => Promise<Uint8Array>;

async function sha256(data: Uint8Array): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', data as Uint8Array<ArrayBuffer>));
  return [...digest].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function encode64(data: Uint8Array): string {
  let text = '';
  for (let at = 0; at < data.length; at += 0x8000) text += String.fromCharCode(...data.subarray(at, at + 0x8000));
  return btoa(text);
}

async function gunzip(data: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([data as Uint8Array<ArrayBuffer>]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/** The stripped kit with Open-XY's profile and stock catalog put back: the published kit's values. */
export function refill(stripped: Kit, profile: Uint8Array, catalog: Uint8Array): Kit {
  return {
    ...stripped,
    profile: JSON.parse(new TextDecoder().decode(profile)) as Kit['profile'],
    profileSource: encode64(profile),
    stockCatalog: encode64(catalog),
  };
}

/** The pinned kit, refilled: kit.json.gz from base (this site's open-xy/0.0.7/), the rest from Open-XY's repository.
 * Throws with the reason when a part is missing or not the pinned one. */
export async function pinnedKit(base: string, get: Get): Promise<Kit> {
  const [packed, profile, catalog] = await Promise.all([
    get(new URL('kit.json.gz', base).href),
    get(PROFILE.url),
    get(CATALOG.url),
  ]);
  const json = packed[0] === 0x1f && packed[1] === 0x8b ? await gunzip(packed) : packed;
  if ((await sha256(json)) !== STRIPPED_SHA256) throw new Error('the pinned kit is not the one this app pins');
  if ((await sha256(profile)) !== PROFILE.sha256 || (await sha256(catalog)) !== CATALOG.sha256)
    throw new Error("Open-XY's v0.0.7 profile or catalog is not the pinned one");
  return refill(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(json)) as Kit, profile, catalog);
}
