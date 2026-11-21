// Install from the web app (#178), the last step, over Web Serial once XY Forever runs (power on holding PLAY):
// as `xyf_host.py library-add` then `set-default` do, the device's own stock 1.2.1 os.ldr becomes library entry
// opxy-121, made the default; then a restart (as #207: to the menu, reconnect, check) and the stock OS starts.
// The entry's os.ldr is made with file.copy on the device: its bytes never leave the OP-XY.
import { LIBRARY_ROOT, isXyfError, makeDefault, packageTransaction } from 'xy-forever-client';
import type { Client } from 'xy-forever-client';
import { HEADROOM, STOCK_121, STOCK_PATH, sha256Hex, spaceVerdict, stockVerdict } from './checks.ts';
import { InstallStop } from './installer.ts';
import type { ReleasePin } from './release.ts';

export const ENTRY = 'opxy-121';
export const STOCK_ON_DEVICE = `/yaffs2/${STOCK_PATH}`;
const ENTRY_PATH = `${LIBRARY_ROOT}/${ENTRY}`;

/** The entry's info.txt, as `xyf_host.py library-add opxy-121 OS.LDR --name OP-XY --version 1.2.1` writes it. */
export const INFO_TEXT = `type=firmware\nname=OP-XY\nversion=1.2.1\nkind=official\nbytes=${STOCK_121.bytes}\ncrc32=${STOCK_121.crc32}\nsha256=${STOCK_121.sha256}\n`;

/** The entry's files.sha256 (sorted paths, two spaces), listing the copied os.ldr by its pinned SHA-256. */
export async function entryFiles(): Promise<Map<string, Uint8Array>> {
  const info = new TextEncoder().encode(INFO_TEXT);
  const list = `${await sha256Hex(info)}  info.txt\n${STOCK_121.sha256}  os.ldr\n`;
  return new Map([['files.sha256', new TextEncoder().encode(list)], ['info.txt', info]]);
}

export interface FinishOptions {
  /** The pinned release: the running XY Forever must be its image (Track 5 wrote ours). */
  readonly release: ReleasePin;
  /** The stock os.ldr was already read from the device and hashed in this tab (Mac, over MTP); else it is read here. */
  readonly stockChecked: boolean;
  readonly step?: (text: string, fraction?: number) => void;
  /** Judges the stock os.ldr's SHA-256 (tests replace it: they hold no stock bytes). */
  readonly judge?: typeof stockVerdict;
}

async function free(client: Client): Promise<number> {
  const r = await client.call('storage.space', { version: 1 });
  return r.free_low + r.free_high * 2 ** 32;
}

/** Whether the entry is already there (a finish that was interrupted after publishing): only the stock one counts. */
async function entryPresent(client: Client): Promise<boolean> {
  let info: Record<string, string>;
  try { info = await client.libraryEntry(ENTRY); } catch (error) { if (isXyfError(error, 'not_found')) return false; throw error; }
  if (info['sha256'] !== STOCK_121.sha256) {
    throw new InstallStop({ kind: 'refuse', title: `Library entry ${ENTRY} is not the stock OS 1.2.1`,
      detail: `Your OP-XY already has a library entry named ${ENTRY} holding other firmware. Nothing was changed.` });
  }
  return true;
}

/**
 * Where an install stands on an OP-XY running XY Forever (#178: the page detects it at any stage): 'done' when the
 * stock entry is in the library and is the default, else 'finish' (finishInstall completes it). Reads only.
 * InstallStop: an entry of that name holds other firmware.
 */
export async function installState(client: Client): Promise<'finish' | 'done'> {
  if (!await entryPresent(client)) return 'finish';
  return (await client.readState())?.default === ENTRY ? 'done' : 'finish';
}

/**
 * Adds the stock entry (checked first: the running image, free storage, the stock file), makes it the default,
 * restarts to the menu and reconnects (the default must hold), then starts the stock OS (the link drops).
 * InstallStop: a check refused, nothing changed.
 */
export async function finishInstall(client: Client, options: FinishOptions): Promise<void> {
  const step = options.step ?? (() => {});
  const judge = options.judge ?? stockVerdict;
  const image = client.info.image;
  if (image !== options.release.image) {
    throw new InstallStop({ kind: 'refuse', title: 'Another XY Forever is running',
      detail: `Your OP-XY runs XY Forever image ${image ?? 'unknown'}, not ${options.release.image} from this page. Go back to the dev menu step and copy and write it again (Track 5). Nothing was changed.` });
  }
  if (!await entryPresent(client)) {
    step('Checking free storage...');
    const space = spaceVerdict(await free(client), STOCK_121.bytes + HEADROOM);
    if (space.kind !== 'ok') throw new InstallStop(space);
    step('Checking the official OS file...');
    const listed = (await client.listDir(STOCK_ON_DEVICE.slice(0, STOCK_ON_DEVICE.lastIndexOf('/'))).catch((error: unknown) => {
      if (isXyfError(error, 'not_found')) return [];
      throw error;
    })).find((e) => e.kind === 'file' && e.name === 'os.ldr');
    if (!listed) throw new InstallStop(judge(null));
    if (!options.stockChecked || listed.size !== STOCK_121.bytes) {
      const data = await client.readFile(STOCK_ON_DEVICE, { onProgress: (done) => step('Checking the official OS file...', done / STOCK_121.bytes) });
      const verdict = judge(await sha256Hex(data));
      if (verdict.kind !== 'ok') throw new InstallStop(verdict);
    }
    step('Adding OS 1.2.1 to the library (copied on your OP-XY)...');
    const files = await entryFiles();
    await packageTransaction(client, {}, async (session) => {
      await session.mkdir(LIBRARY_ROOT.slice(0, LIBRARY_ROOT.lastIndexOf('/')));   // a fresh install may have neither folder
      await session.mkdir(LIBRARY_ROOT);
      await session.publish(ENTRY_PATH, files, new Map([['os.ldr', STOCK_ON_DEVICE]]));
    }, { copies: [STOCK_ON_DEVICE] });   // file.copy before the session: inside it the OP-XY answers busy
  }
  step('Making OS 1.2.1 the default...');
  await makeDefault(client, ENTRY);
  step('Restarting your OP-XY...');
  await client.reboot('menu');
  const info = await client.reconnect({ timeoutMs: 20_000 });
  if (info.image !== image) throw new Error('A different XY Forever answered after the restart. Power your OP-XY off and on, then connect again.');
  const state = await client.readState();
  if (state?.default !== ENTRY) throw new Error(`After the restart the default is ${state?.default ?? 'not set'}, not ${ENTRY}. Connect again to finish.`);
  step('Starting OS 1.2.1...');
  await client.continueBoot();
}
