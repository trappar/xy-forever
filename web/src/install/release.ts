// Our os.ldr, served from this app's own origin (release/os.ldr, staged by scripts/web_release.py) and pinned here
// by its SHA-256 (release.json, written by that script from a verified build; committed in a release pull request).
// The page refuses any other bytes. Builds are deterministic, so anyone can rebuild the pinned image from its commit.
// Canary (#301): the Pages deploy may add the newest eligible canary prerelease of main (scripts/web_canary.py) as
// canary/os.ldr with its pin, canary/release.json, read at run time; without one the app offers Stable only.
import pin from './release.json';
import { sha256Hex } from './checks.ts';

export interface ReleasePin {
  readonly schema: number;
  readonly version: string;
  readonly image: string;
  readonly sha256: string;
  readonly bytes: number;
  readonly notes?: string;
  /** Set on a canary: its source commit (full SHA) and that commit's date (ISO 8601, UTC). */
  readonly channel?: 'canary';
  readonly commit?: string;
  readonly date?: string;
}
export type Channel = 'stable' | 'canary';
const CANARY_PIN = `${import.meta.env.BASE_URL}canary/release.json`;
const noStore = (url: string): Promise<Response> => fetch(url, { cache: 'no-store' });

export const channelOf = (release: ReleasePin): Channel => (release.channel === 'canary' ? 'canary' : 'stable');
/** Where this origin serves a channel's os.ldr. */
export const releaseUrl = (release: ReleasePin): string =>
  `${import.meta.env.BASE_URL}${channelOf(release) === 'canary' ? 'canary' : 'release'}/os.ldr`;

/** The pin a stable or canary release must have, else null. */
export function validPin(value: unknown): ReleasePin | null {
  const v = value as Partial<ReleasePin> | null;
  if (!v || typeof v !== 'object' || v.schema !== 1 || typeof v.version !== 'string') return null;
  if (typeof v.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(v.sha256) || v.image !== v.sha256.slice(0, 8)) return null;
  if (typeof v.bytes !== 'number' || !(v.bytes > 0)) return null;
  if (v.channel === undefined) return v as ReleasePin;
  const dated = typeof v.date === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(v.date);
  return v.channel === 'canary' && typeof v.commit === 'string' && /^[0-9a-f]{40}$/.test(v.commit) && dated
    ? (v as ReleasePin)
    : null;
}

/** The canary this site offers, or null when it has none (or its pin cannot be read). */
export async function fetchCanary(get: (url: string) => Promise<Response> = noStore): Promise<ReleasePin | null> {
  try {
    const response = await get(CANARY_PIN);
    if (!response.ok) return null;
    const pin = validPin(await response.json());
    return pin && channelOf(pin) === 'canary' ? pin : null;
  } catch {
    return null; // a dev server answers a missing file with the app's HTML
  }
}

/** How the page names a release: "XY Forever 0.1.0" or "Canary 2026-11-23 (abc1234)". */
export const releaseName = (release: ReleasePin): string =>
  channelOf(release) === 'canary'
    ? `Canary ${release.date!.slice(0, 10)} (${release.commit!.slice(0, 7)})`
    : `XY Forever ${release.version}`;

/** The pinned release, or null when this app has none yet (install is then unavailable, and says so). */
export function pinned(value: ReleasePin = pin): ReleasePin | null {
  if (value.schema > 1) throw new Error('release.json is newer than this page reads: update the page');
  return /^[0-9a-f]{64}$/.test(value.sha256) && value.bytes > 0 ? value : null;
}

/** Fetches our os.ldr from this origin and returns it only when its size and SHA-256 equal the pin. */
export async function fetchRelease(
  release: ReleasePin,
  get: (url: string) => Promise<Response> = noStore,
  url = releaseUrl(release),
): Promise<Uint8Array> {
  let response: Response;
  try {
    response = await get(url);
  } catch {
    throw new Error('Could not download XY Forever from this site. Check your connection and try again.');
  }
  if (!response.ok)
    throw new Error(`Could not download XY Forever from this site (HTTP ${response.status}). Try again later.`);
  const data = new Uint8Array(await response.arrayBuffer());
  const digest = await sha256Hex(data);
  if (data.length !== release.bytes || digest !== release.sha256) {
    throw new Error(
      `The XY Forever file this site sent is not ${releaseName(release)} (image ${release.image}): its SHA-256 is ${digest.slice(0, 12)}..., ` +
        'not the pinned one. Nothing was copied. Reload the page and try again.',
    );
  }
  return data;
}
