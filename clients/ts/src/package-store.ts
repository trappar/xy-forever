// Host package transactions over the existing file service, session ABI 1.
import { sha256 } from './client.ts';
import type { Client, CallOptions } from './client.ts';
import { isXyfError } from './errors.ts';
import { remoteTree } from './modstore.ts';
const ROOT = '/yaffs2/xy-forever',
  TMP = `${ROOT}/tmp`;

export class PackageSession {
  readonly client: Client;
  readonly options: CallOptions;
  readonly token = crypto.getRandomValues(new Uint8Array(32));
  constructor(client: Client, options: CallOptions) {
    this.client = client;
    this.options = options;
  }
  async command(action: number): Promise<void> {
    await this.client.call('package.session', { version: 1, action, token: this.token }, this.options);
  }
  async acquire(): Promise<void> {
    for (let waited = 0; ; waited += 20) {
      try {
        await this.command(0);
        break;
      } catch (error) {
        if (!isXyfError(error, 'busy') || waited >= 60_000) throw error;
        await this.client.pause(20, this.options.signal);
      }
    }
  }
  async sweepScratch(): Promise<void> {
    for (const entry of await this.entries(TMP))
      if (entry.kind === 'folder' && entry.name.startsWith('pkg-v1-')) {
        await this.erase(`${TMP}/${entry.name}`);
      }
  }
  async release(): Promise<void> {
    await this.command(2);
    for (let waited = 0; ; waited += 20) {
      try {
        await this.client.listDir('/yaffs2', this.options);
        return;
      } catch (error) {
        if (!isXyfError(error, 'busy') || waited >= 60_000) throw error;
        await this.client.pause(20, this.options.signal);
      }
    }
  }
  async entries(path: string): ReturnType<Client['listDir']> {
    await this.command(1);
    try {
      return await this.client.listDir(path, this.options);
    } catch (error) {
      if (isXyfError(error, 'not_found')) return [];
      throw error;
    }
  }
  async mkdir(path: string): Promise<void> {
    await this.command(1);
    if (
      (await this.entries(path.substring(0, path.lastIndexOf('/')))).some(
        (entry) => entry.name === path.split('/').at(-1),
      )
    )
      return;
    await this.client.makeDir(path, this.options);
  }
  async scratch(): Promise<string> {
    await this.mkdir(TMP);
    const token = crypto.getRandomValues(new Uint8Array(8));
    const path = `${TMP}/pkg-v1-${[...token].map((value) => value.toString(16).padStart(2, '0')).join('')}`;
    await this.mkdir(path);
    return path;
  }
  async write(path: string, data: Uint8Array): Promise<void> {
    await this.command(1);
    await this.client.writeFile(path, data, this.options);
    const back = await this.client.readFile(path, this.options);
    const expected = await sha256(data),
      actual = await sha256(back);
    if (actual.some((byte, index) => byte !== expected[index])) throw new Error(`package read-back differs: ${path}`);
  }
  async erase(path: string): Promise<void> {
    for (const entry of await this.entries(path)) {
      const child = `${path}/${entry.name}`;
      if (entry.kind === 'folder') await this.erase(child);
      else if (entry.kind === 'file') {
        await this.command(1);
        await this.client.deleteFile(child, this.options);
      } else throw new Error(`package scratch contains a non-file: ${child}`);
    }
    await this.command(1);
    await this.client.removeDir(path, this.options);
  }
  async publish(target: string, files: ReadonlyMap<string, Uint8Array>): Promise<void> {
    const name = target.split('/').at(-1)!;
    if (!/^[\x20-\x7e]{1,127}$/.test(name) || /[\\:]/.test(name) || /[. ]$/.test(name) || /bootloader/i.test(name))
      throw new Error('unsafe package name');
    const scratch = await this.scratch();
    for (const [name, data] of [...files].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      await this.write(`${scratch}/${name}`, data);
    const existing = await remoteTree(this.client, target, this.options);
    await this.command(1);
    if (existing) await this.client.renamePath(target, `${scratch}-old`, this.options);
    await this.command(1);
    await this.client.renamePath(scratch, target, this.options);
    if (existing) await this.erase(`${scratch}-old`);
  }
  async remove(target: string): Promise<void> {
    const scratch = await this.scratch();
    await this.command(1);
    await this.client.removeDir(scratch, this.options);
    await this.command(1);
    await this.client.renamePath(target, scratch, this.options);
    await this.erase(scratch);
  }
}

export async function withFileSession<T>(
  client: Client,
  options: CallOptions,
  run: (session: PackageSession) => Promise<T>,
): Promise<T> {
  const session = new PackageSession(client, options);
  await session.acquire();
  try {
    const result = await run(session);
    await session.release();
    return result;
  } catch (error) {
    // Preserve the operation's original error when its cable has gone away.
    try {
      await session.release();
    } catch {
      /* the ten-second abandoned-owner timeout releases it */
    }
    throw error;
  }
}

export async function packageTransaction<T>(
  client: Client,
  options: CallOptions,
  run: (session: PackageSession) => Promise<T>,
): Promise<T> {
  return withFileSession(client, options, async (session) => {
    await session.sweepScratch();
    return run(session);
  });
}
