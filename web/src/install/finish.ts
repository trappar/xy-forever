// Install from the web app (#178), after Track 5: XY Forever sets itself up at its first start, on the OP-XY. With
// no stock entry in its library it adopts the device's own /yaffs2/upgrade/firmware/blackfin/bin/os.ldr (hashed on
// the device, against the stock 1.2.1 SHA-256), copies it into library entry opxy-121, makes that the default and
// boots it (target/usb_service_adopt.ipp; docs/storage/jobs.md). Right after, a light check removes what Open-XY left in
// the OS content and puts back the official op1_factory.json (#334; docs/storage/content-set.md), and notes what it did
// in xy-forever/cleanup.txt. The page sends and reads no stock bytes: it only reads where that setup stands, over Web
// Serial, then hands the owner to the app.
import { LIBRARY_ROOT, STATUS, isXyfError, jobStatus } from 'xy-forever-client';
import type { Client } from 'xy-forever-client';
import { STOCK_121 } from './checks.ts';
import type { Verdict } from './checks.ts';
import { InstallStop } from './installer.ts';
import type { ReleasePin } from './release.ts';

export const ENTRY = 'opxy-121';
/** job.status detail of the adoption while it runs or after it failed ('opxy-121' once done). */
export const ADOPT_DETAIL = `adopt ${ENTRY}`;
/** job.status detail of the light check after it (#334) while it runs or after it failed. */
export const TIDY_DETAIL = `tidy ${ENTRY}`;
/** What that check removed (files=<n>, catalog=0|1), written only when it removed something. */
export const CLEANUP_FILE = '/yaffs2/xy-forever/cleanup.txt';

/** Where XY Forever's own setup stands. cleaned: what the first start removed of Open-XY's content, in words. */
export type Setup =
  | { readonly kind: 'installed'; readonly cleaned: string | null }
  | { readonly kind: 'adopting'; readonly done: number; readonly total: number }
  | { readonly kind: 'checking' }
  | { readonly kind: 'refused'; readonly verdict: Verdict }
  /** other: the image running is not this page's release (an older one never sets itself up). */
  | { readonly kind: 'waiting'; readonly other: boolean };

const REINSTALL =
  'Reinstall teenage engineering\'s OS 1.2.1 with its own updater (te-boot\'s "1. upload firmware"), then start again here. ' +
  "Use exactly this 1.2.1 file, not the newest update on teenage engineering's site: 1.2.5 cannot be undone. Nothing was changed.";

/**
 * The device's refusal (job.status error and code) as the page shows it, with the words its menu shows. Removing the
 * entry is advised only when the device says it is incomplete (rename with code state: an entry its library lists).
 */
function adoptVerdict(error: string, code: number): Verdict {
  if (error === 'open') {
    return {
      kind: 'reinstall',
      title: 'No official OS 1.2.1 found',
      detail: `Your OP-XY says: "No official OS 1.2.1 found. Reinstall official OS 1.2.1, then start again." It has no upgrade/firmware/blackfin/bin/os.ldr (a factory reset can remove it). ${REINSTALL}`,
    };
  }
  if (error === 'sha256') {
    return {
      kind: 'reinstall',
      title: 'OS file is not official 1.2.1',
      detail: `Your OP-XY says: "OS file is not official 1.2.1. Reinstall official OS 1.2.1, then start again." Its upgrade/firmware/blackfin/bin/os.ldr is not teenage engineering's OS 1.2.1. ${REINSTALL}`,
    };
  }
  if (error === 'list') {
    return {
      kind: 'refuse',
      title: 'The library could not be read',
      detail:
        'Your OP-XY says: "Failed: the library could not be read." Nothing was changed. Switch it off and on to try again; if it fails again, make a bug report.',
    };
  }
  if (error === 'rename' && code === STATUS.state) {
    return {
      kind: 'refuse',
      title: `Library entry ${ENTRY} is incomplete`,
      detail: `Your OP-XY says: "Failed: library entry ${ENTRY} is incomplete: remove it." Remove ${LIBRARY_ROOT}/${ENTRY} (the Files page), then switch your OP-XY off and on.`,
    };
  }
  return {
    kind: 'refuse',
    title: 'OS 1.2.1 could not be added to the library',
    detail: `Your OP-XY could not add OS 1.2.1 to its library (${error}). Switch it off and on to try again; if it fails again, make a bug report.`,
  };
}

/** The light check's refusal: the device's own words (#331's), and what is left as it was. */
function tidyVerdict(error: string): Verdict {
  if (error === 'catalog') {
    return {
      kind: 'refuse',
      title: "op1_factory.json can't be repaired",
      detail:
        "Your OP-XY says: \"Failed: op1_factory.json can't be repaired. Copy in OS 1.2.1's own.\" Its op1_factory.json was changed beyond the entries Open-XY adds, so nothing in the OS content was changed. XY Forever is installed. Copy OS 1.2.1's own op1_factory.json into content/ (the Files page) before you build mods: the first build then removes what Open-XY left.",
    };
  }
  return {
    kind: 'refuse',
    title: 'The OS content could not be checked',
    detail: `Your OP-XY could not check its OS 1.2.1 content (${error}). XY Forever is installed. Switch your OP-XY off and on; if this shows again, make a bug report.`,
  };
}

/** cleanup.txt in words ("Removed 82 files from Open-XY, put back OS 1.2.1's op1_factory.json."), or null. */
export function cleanupWords(text: string): string | null {
  const fields = Object.fromEntries(
    text
      .split('\n')
      .filter((line) => line.includes('='))
      .map((line) => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1)]),
  );
  const files = Number(fields['files'] ?? 0),
    catalog = fields['catalog'] === '1';
  if (!Number.isSafeInteger(files) || files < 0 || (!files && !catalog)) return null;
  const removed = files ? `Removed ${files} ${files === 1 ? 'file' : 'files'} from Open-XY` : '';
  if (!catalog) return `${removed}.`;
  return removed ? `${removed}, put back OS 1.2.1's op1_factory.json.` : "Put back OS 1.2.1's op1_factory.json.";
}

/** What the first start removed (CLEANUP_FILE in words), or null when it found nothing to remove. */
async function cleaned(client: Client): Promise<string | null> {
  try {
    return cleanupWords(new TextDecoder().decode(await client.readFile(CLEANUP_FILE)));
  } catch (error) {
    if (isXyfError(error, 'not_found')) return null;
    throw error;
  }
}

/** Whether entry opxy-121 is there; InstallStop when it holds other firmware. `busy` (the setup runs) is thrown. */
async function entryPresent(client: Client): Promise<boolean> {
  let info: Record<string, string>;
  try {
    info = await client.libraryEntry(ENTRY);
  } catch (error) {
    if (isXyfError(error, 'not_found')) return false;
    throw error;
  }
  if (info['sha256'] !== STOCK_121.sha256) {
    throw new InstallStop({
      kind: 'refuse',
      title: `Library entry ${ENTRY} is not the stock OS 1.2.1`,
      detail: `Your OP-XY already has a library entry named ${ENTRY} holding other firmware. Nothing was changed.`,
    });
  }
  return true;
}

/** Whether the default names a library entry (the stock one, or one with mods: mods-a, mods-b). */
async function defaultListed(client: Client): Promise<boolean> {
  const id = (await client.readState())?.default;
  if (!id) return false;
  if (id === ENTRY) return true;
  try {
    await client.libraryEntry(id);
    return true;
  } catch (error) {
    if (isXyfError(error, 'not_found')) return false;
    throw error;
  }
}

/**
 * Where the setup stands on an OP-XY running XY Forever. Reads only. installed: the stock entry is there and the
 * default names a library entry (it, or one with mods; any XY Forever). adopting: the device's setup job runs (file
 * reads answer busy meanwhile). refused: it stopped, with the device's reason. waiting: the setup has not started
 * (yet). InstallStop: an entry of that name holding other firmware.
 */
export async function installState(client: Client, release: ReleasePin): Promise<Setup> {
  const job = await jobStatus(client).catch((error: unknown) => {
    if (isXyfError(error, 'not_found')) return null;
    throw error;
  });
  if (job?.detail === ADOPT_DETAIL) {
    if (!job.final) return { kind: 'adopting', done: job.done, total: job.total };
    if (job.phase === 'failed') return { kind: 'refused', verdict: adoptVerdict(job.error, job.code) };
  }
  if (job?.detail === TIDY_DETAIL) {
    if (!job.final) return { kind: 'checking' };
    if (job.phase === 'failed') return { kind: 'refused', verdict: tidyVerdict(job.error) };
  }
  try {
    if ((await entryPresent(client)) && (await defaultListed(client)))
      return { kind: 'installed', cleaned: await cleaned(client) };
  } catch (error) {
    if (isXyfError(error, 'busy')) return { kind: 'adopting', done: 0, total: 0 };
    throw error;
  }
  return { kind: 'waiting', other: client.info.image !== release.image };
}

/** A setup that never starts under another image: that XY Forever predates it. */
export function otherImage(image: string | null, release: ReleasePin): Verdict {
  return {
    kind: 'refuse',
    title: 'Another XY Forever is running',
    detail: `Your OP-XY runs XY Forever image ${image ?? 'unknown'}, not ${release.image} from this page, and its library has no OS 1.2.1 entry or no default. Go back to the dev menu step, copy and write this page's XY Forever (Track 5), then switch your OP-XY off and on.`,
  };
}
