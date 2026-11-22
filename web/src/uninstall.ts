import { isXyfError, makeDefault, sha256, TMP_ROOT, TYPE, withFileSession } from 'xy-forever-client';
import type { Client, DeviceInfo, DirEntry } from 'xy-forever-client';
import { hex, STOCK_121, STOCK_PATH } from './install/checks.ts';
import { ENTRY } from './install/finish.ts';
import { CONTENT_STATE, CONTENT_STATE_OLD } from './model.ts';

const MOUNT = '/yaffs2';
const MANAGER = `${MOUNT}/xy-forever`;
const ROOT_IMAGE = `${MOUNT}/os.ldr`;
const STOCK_IMAGE = `${MOUNT}/${STOCK_PATH}`;
const STOCK_UPLOAD = `put-${STOCK_121.bytes.toString(16).padStart(8, '0')}-${STOCK_121.crc32}`;
const OFFICIAL_RECORD = 'state=official\n';
// A missed reply can be retried with a fresh sequence. Keep the combined wait below the idle file-session lease.
const IMAGE_READ_TIMEOUTS = [1800, 1800, 4000] as const;
const COMMANDS = [TYPE['file.read'], TYPE['file.list'], TYPE['library.set_default'], TYPE['job.status'],
  TYPE['stage.begin'], TYPE['stage.data'], TYPE['stage.end'], TYPE['boot.uninstall'], TYPE['file.write_begin'],
  TYPE['file.write_data'], TYPE['file.write_end'], TYPE['file.delete'], TYPE['file.rmdir'], TYPE['package.session'],
  TYPE['device.reboot']];

export const UNINSTALL_PROTOCOL = '1.26';

export function uninstallAvailable(info: DeviceInfo): boolean {
  const minor = /^1\.(\d+)$/.exec(info.proto)?.[1];
  return Number(minor) >= Number(UNINSTALL_PROTOCOL.slice(2)) && info.serial && info.limits.stage >= STOCK_121.bytes &&
    COMMANDS.every((type) => info.hello.answers(type));
}

/** Resolve an entry through its parent's listing, which distinguishes a directory from a symlink to one. */
async function rootEntries(client: Pick<Client, 'listDir'>): Promise<{ manager: boolean; image: DirEntry | undefined }> {
  const entries = await client.listDir(MOUNT);
  const manager = entries.find((entry) => entry.name === 'xy-forever');
  const image = entries.find((entry) => entry.name === 'os.ldr');
  if (manager && manager.kind !== 'folder') throw new Error('The xy-forever entry on your OP-XY is not a directory. Uninstall stopped.');
  if (image && image.kind !== 'file') throw new Error('The os.ldr entry at the root of your OP-XY is not a regular file. Uninstall stopped.');
  return { manager: manager !== undefined, image };
}

/** Streamed writes use a fixed scratch name under the manager folder. Refuse links before opening it. */
async function checkUpload(client: Client, manager: boolean): Promise<void> {
  if (!manager) return;
  const tmp = (await client.listDir(MANAGER)).find((entry) => entry.name === 'tmp');
  if (!tmp) return;
  if (tmp.kind !== 'folder') throw new Error('The XY Forever temporary folder is not a directory. Uninstall stopped.');
  const upload = (await client.listDir(TMP_ROOT)).find((entry) => entry.name === STOCK_UPLOAD);
  if (upload && upload.kind !== 'file') throw new Error('The temporary OS file is not a regular file. Uninstall stopped.');
}

async function readImage(client: Client, path: string, progress?: (done: number) => void): Promise<Uint8Array> {
  const bytes = new Uint8Array(STOCK_121.bytes);
  const length = client.info.limits.read;
  for (let offset = 0; ;) {
    const data = await readImageRange(client, path, offset, length);
    if (data.length > bytes.length - offset) throw new Error('The OS file is larger than official OS 1.2.1.');
    bytes.set(data, offset);
    offset += data.length;
    progress?.(offset);
    if (data.length < length) return bytes.subarray(0, offset);
  }
}

async function readImageRange(client: Client, path: string, offset: number, length: number): Promise<Uint8Array> {
  for (let attempt = 0; ; ++attempt) {
    try {
      return await client.readRange(path, offset, length, { timeoutMs: IMAGE_READ_TIMEOUTS[attempt]! });
    } catch (error) {
      if (!isXyfError(error, 'timeout') || attempt === IMAGE_READ_TIMEOUTS.length - 1) throw error;
    }
  }
}

async function readStock(client: Client, step: (message: string) => void): Promise<{ bytes: Uint8Array; digest: Uint8Array }> {
  const refusal = 'Your OP-XY does not have the exact official OS 1.2.1 in its protected upgrade folder. Nothing was changed.';
  try {
    const entry = (await client.listDir(STOCK_IMAGE.slice(0, STOCK_IMAGE.lastIndexOf('/')))).find((item) => item.name === 'os.ldr');
    if (entry?.kind !== 'file' || entry.size !== STOCK_121.bytes) throw new Error(refusal);
    const bytes = await readImage(client, STOCK_IMAGE, (done) =>
      step(`Reading official OS 1.2.1 from your OP-XY… ${Math.floor(done * 100 / STOCK_121.bytes)}%`));
    const digest = await sha256(bytes);
    if (bytes.length !== STOCK_121.bytes || hex(digest) !== STOCK_121.sha256) throw new Error(refusal);
    return { bytes, digest };
  } catch (error) {
    if (isXyfError(error, 'not_found')) throw new Error(refusal, { cause: error });
    throw error;
  }
}

async function officialContent(client: Client): Promise<boolean> {
  try {
    const entries = await client.listDir(MANAGER);
    if (entries.some((entry) => entry.name === 'job.txt' || entry.name === 'job.old')) return false;
    const record = entries.find((entry) => entry.name === 'content-state.txt');
    if (record) {
      return record.kind === 'file' && new TextDecoder().decode(await client.readRange(CONTENT_STATE, 0, OFFICIAL_RECORD.length)) === OFFICIAL_RECORD;
    }
    return !entries.some((entry) => `${MANAGER}/${entry.name}` === CONTENT_STATE_OLD);
  } catch (error) {
    if (isXyfError(error, 'not_found')) return false;
    throw error;
  }
}

async function restoreContent(client: Client, manager: boolean): Promise<void> {
  if (!manager || await officialContent(client)) return;  // also true after partial cleanup of an earlier uninstall
  for (const id of [ENTRY, 'opxy-121-stock']) {
    let stock: Record<string, string>;
    try { stock = await client.libraryEntry(id); } catch (error) {
      if (isXyfError(error, 'not_found')) continue;
      throw error;
    }
    if (stock['sha256'] !== STOCK_121.sha256 || stock['generation']) continue;
    await makeDefault(client, id);
    if (!await officialContent(client)) throw new Error('The OP-XY did not confirm that official content was restored.');
    return;
  }
  throw new Error('XY Forever could not find its official OS 1.2.1 library entry to restore content.');
}

async function removeTree(client: Pick<Client, 'listDir' | 'deleteFile' | 'removeDir'>, path: string): Promise<void> {
  const encoder = new TextEncoder();
  const entries = await client.listDir(path);
  // Keep the official record through partial cleanup, past deletion of its stale .old and the library.
  if (path === MANAGER) entries.sort((a, b) => Number(`${path}/${a.name}` === CONTENT_STATE) - Number(`${path}/${b.name}` === CONTENT_STATE));
  for (const entry of entries) {
    const encoded = encoder.encode(entry.name);
    if (!entry.name || /^[.]{1,2}$|[\\/\0]/.test(entry.name) ||
        encoded.length !== entry.nameBytes.length || encoded.some((byte, i) => byte !== entry.nameBytes[i])) {
      throw new Error(`XY Forever could not safely remove a name inside ${path}.`);
    }
    const child = `${path}/${entry.name}`;
    if (entry.kind === 'folder') await removeTree(client, child);
    else await client.deleteFile(child);  // includes links, without taking a directory listing of their targets
  }
  await client.removeDir(path);
}

/** After the verified boot write, finish using the file session that excludes MTP, menu writes and the idle sweep. */
async function finishUninstall(client: Client, bytes: Uint8Array, step: (message: string) => void): Promise<void> {
  await withFileSession(client, {}, async (session) => {
    const { manager, image } = await rootEntries(client);
    step('Checking the OS file at the root of your OP-XY…');
    if (image?.size !== bytes.length || hex(await sha256(await readImage(client, ROOT_IMAGE))) !== STOCK_121.sha256) {
      await checkUpload(client, manager);
      step('Restoring the stock OS file at the root of your OP-XY…');
      await client.writeFile(ROOT_IMAGE, bytes);
      if (hex(await sha256(await readImage(client, ROOT_IMAGE))) !== STOCK_121.sha256) {
        throw new Error('The restored OS file did not match official OS 1.2.1.');
      }
    }
    await session.command(1);
    step('Removing XY Forever files…');
    if ((await rootEntries(client)).manager) await removeTree(client, MANAGER);
    if ((await rootEntries(client)).manager) throw new Error('The XY Forever directory is still present.');
  });
  if ((await rootEntries(client)).manager) throw new Error('The XY Forever directory reappeared after cleanup.');
}

/** Call after the owner's confirmation. A retry on the still-running service repeats the exact stock write safely. */
export async function uninstallXyf(client: Client, step: (message: string) => void): Promise<void> {
  if (!uninstallAvailable(client.info)) throw new Error('Uninstall is unavailable for the connected OP-XY.');
  const { manager, image } = await rootEntries(client);
  if (image?.size !== STOCK_121.bytes) await checkUpload(client, manager);
  step('Checking official OS 1.2.1 on your OP-XY…');
  const { bytes, digest } = await readStock(client, step);
  step('Restoring official content…');
  await restoreContent(client, manager);
  step('Sending official OS 1.2.1 to the boot writer…');
  await client.stage(bytes, { onProgress: (done) => step(`Sending official OS 1.2.1 to the boot writer… ${Math.floor(done * 100 / bytes.length)}%`) });
  step('Writing and checking the stock boot partition. Keep your OP-XY powered and connected.');
  try {
    const { written, partition, sectors } = await client.bootUninstall(digest);
    if (written !== bytes.length || (partition !== 1 && partition !== 2) || sectors !== Math.floor(bytes.length / 512) + 1) {
      throw new Error('The boot writer returned incomplete read-back counters.');
    }
  } catch (error) {
    throw new Error('The OP-XY did not confirm a complete read-back of the stock boot partition. Cleanup has not started. ' +
      'Reconnect in the XY Forever menu to retry.', { cause: error });
  }
  try {
    await finishUninstall(client, bytes, step);
  } catch (error) {
    throw new Error(`The stock boot partition was restored and verified. Cleanup stopped. ${error instanceof Error ? error.message : error} ` +
      'Try Uninstall again while XY Forever is still running. A power cycle starts the stock OS.', { cause: error });
  }
  step('Restarting your OP-XY into stock OS 1.2.1…');
  try {
    await client.reboot('os');
  } catch (error) {
    throw new Error('Stock OS 1.2.1 was restored and cleanup finished. Power your OP-XY off and back on to start it.', { cause: error });
  }
}
