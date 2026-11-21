// Our os.ldr, served from this app's own origin (release/os.ldr, staged by scripts/web_release.py) and pinned here
// by its SHA-256 (release.json, written by that script from a verified build; committed in a release pull request).
// The page refuses any other bytes. Builds are deterministic, so anyone can rebuild the pinned image from its commit.
import pin from './release.json';
import { sha256Hex } from './checks.ts';

export interface ReleasePin { readonly schema: number; readonly version: string; readonly image: string; readonly sha256: string; readonly bytes: number }
export const RELEASE_PATH = 'release/os.ldr';

/** The pinned release, or null when this app has none yet (install is then unavailable, and says so). */
export function pinned(value: ReleasePin = pin): ReleasePin | null {
  if (value.schema > 1) throw new Error('release.json is newer than this page reads: update the page');
  return /^[0-9a-f]{64}$/.test(value.sha256) && value.bytes > 0 ? value : null;
}

/** Fetches our os.ldr from this origin and returns it only when its size and SHA-256 equal the pin. */
export async function fetchRelease(release: ReleasePin, get: (url: string) => Promise<Response> = (url) => fetch(url, { cache: 'no-store' }),
  url = RELEASE_PATH): Promise<Uint8Array> {
  let response: Response;
  try { response = await get(url); } catch { throw new Error('Could not download XY Forever from this site. Check your connection and try again.'); }
  if (!response.ok) throw new Error(`Could not download XY Forever from this site (HTTP ${response.status}). Try again later.`);
  const data = new Uint8Array(await response.arrayBuffer());
  const digest = await sha256Hex(data);
  if (data.length !== release.bytes || digest !== release.sha256) {
    throw new Error(`The XY Forever file this site sent is not release ${release.version} (image ${release.image}): its SHA-256 is ${digest.slice(0, 12)}..., ` +
      'not the pinned one. Nothing was copied. Reload the page and try again.');
  }
  return data;
}
