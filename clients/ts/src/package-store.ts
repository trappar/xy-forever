// Host package transactions over the existing file service, session ABI 1.
import { sha256 } from './client.ts';
import type { Client, CallOptions } from './client.ts';
import { isXyfError } from './errors.ts';
import { remoteTree } from './modstore.ts';
const ROOT = '/yaffs2/xy-forever', TMP = `${ROOT}/tmp`;
const pause = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 20));
const COPY = 'copy-v1-';           // tmp/copy-v1-<16 hex>: a file.copy made before a session (the sweep leaves it alone)
const token = (bytes: number): string => [...crypto.getRandomValues(new Uint8Array(bytes))].map((v) => v.toString(16).padStart(2, '0')).join('');

export class PackageSession {
  readonly client: Client;
  readonly options: CallOptions;
  readonly token = crypto.getRandomValues(new Uint8Array(32));
  /** Device source path -> its copy in tmp/, made before the session (packageTransaction's copies), until published. */
  readonly copied = new Map<string, string>();
  constructor(client: Client, options: CallOptions) { this.client = client; this.options = options; }
  async command(action: number): Promise<void> {
    await this.client.call('package.session', { version: 1, action, token: this.token }, this.options);
  }
  async acquire(): Promise<void> {
    const until = Date.now() + 60_000;
    for (;;) {
      try { await this.command(0); break; } catch (error) {
        if (!isXyfError(error, 'busy') || Date.now() >= until) throw error;
        await pause();
      }
    }
    const kept = new Set([...this.copied.values()]);
    for (const entry of await this.entries(TMP)) {
      const path = `${TMP}/${entry.name}`;
      if (entry.kind === 'folder' && entry.name.startsWith('pkg-v1-')) await this.erase(path);
      else if (entry.kind === 'file' && entry.name.startsWith(COPY) && !kept.has(path)) { await this.command(1); await this.client.deleteFile(path, this.options); }
    }
  }
  /**
   * Before acquire: copies each source to tmp/copy-v1-* with file.copy (the bytes never cross USB; the device reads
   * the copy back and compares). Not inside the session: file.copy is not among the commands a package session
   * permits, and the device answers it `busy` there (the job exclusion the session holds). A copy left by a cut is
   * removed by the next session's acquire.
   */
  async copyFirst(sources: readonly string[]): Promise<void> {
    if (sources.length === 0) return;
    for (const folder of [ROOT, TMP]) {                                    // a fresh install may have neither
      const parent = folder.slice(0, folder.lastIndexOf('/'));
      if (!(await this.client.listDir(parent, this.options)).some((entry) => entry.name === folder.slice(parent.length + 1))) {
        await this.client.makeDir(folder, this.options);
      }
    }
    for (const source of new Set(sources)) {
      const copy = `${TMP}/${COPY}${token(8)}`;
      await this.client.copyFile(source, copy, this.options);
      this.copied.set(source, copy);
    }
  }
  /** After a failed transaction, outside the session: the copies not published (best effort; else the next acquire). */
  async dropCopies(): Promise<void> {
    for (const copy of this.copied.values()) await this.client.deleteFile(copy, this.options).catch(() => {});
    this.copied.clear();
  }
  async release(): Promise<void> {
    await this.command(2);
    const until = Date.now() + 60_000;
    for (;;) {
      try { await this.client.listDir(ROOT, this.options); return; } catch (error) {
        if (!isXyfError(error, 'busy') || Date.now() >= until) throw error;
        await pause();
      }
    }
  }
  async entries(path: string): ReturnType<Client['listDir']> {
    await this.command(1);
    try { return await this.client.listDir(path, this.options); } catch (error) {
      if (isXyfError(error, 'not_found')) return [];
      throw error;
    }
  }
  async mkdir(path: string): Promise<void> {
    await this.command(1);
    if ((await this.entries(path.substring(0, path.lastIndexOf('/')))).some((entry) => entry.name === path.split('/').at(-1))) return;
    await this.client.makeDir(path, this.options);
  }
  async scratch(): Promise<string> {
    await this.mkdir(TMP);
    const path = `${TMP}/pkg-v1-${token(8)}`;
    await this.mkdir(path); return path;
  }
  async write(path: string, data: Uint8Array): Promise<void> {
    await this.command(1);
    await this.client.writeFile(path, data, this.options);
    const back = await this.client.readFile(path, this.options);
    const expected = await sha256(data), actual = await sha256(back);
    if (actual.some((byte, index) => byte !== expected[index])) throw new Error(`package read-back differs: ${path}`);
  }
  async erase(path: string): Promise<void> {
    for (const entry of await this.entries(path)) {
      const child = `${path}/${entry.name}`;
      if (entry.kind === 'folder') await this.erase(child);
      else if (entry.kind === 'file') { await this.command(1); await this.client.deleteFile(child, this.options); }
      else throw new Error(`package scratch contains a non-file: ${child}`);
    }
    await this.command(1); await this.client.removeDir(path, this.options);
  }
  /**
   * files: written and read back; copies: name -> a file already on the device, whose copy packageTransaction made
   * before the session (its `copies`), moved in by rename. files.sha256 among files must list both.
   */
  async publish(target: string, files: ReadonlyMap<string, Uint8Array>, copies: ReadonlyMap<string, string> = new Map()): Promise<void> {
    const name = target.split('/').at(-1)!;
    if (!/^[\x20-\x7e]{1,127}$/.test(name) || /[\\:]/.test(name) || /[. ]$/.test(name) || /bootloader/i.test(name)) throw new Error('unsafe package name');
    for (const source of copies.values()) if (!this.copied.has(source)) throw new Error(`package copy not made before the session: ${source}`);
    const scratch = await this.scratch();
    for (const [copy, source] of copies) {
      await this.command(1); await this.client.renamePath(this.copied.get(source)!, `${scratch}/${copy}`, this.options);
      this.copied.delete(source);
    }
    for (const [name, data] of [...files].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)) await this.write(`${scratch}/${name}`, data);
    const existing = await remoteTree(this.client, target, this.options);
    await this.command(1);
    if (existing) await this.client.renamePath(target, `${scratch}-old`, this.options);
    await this.command(1); await this.client.renamePath(scratch, target, this.options);
    if (existing) await this.erase(`${scratch}-old`);
  }
  async remove(target: string): Promise<void> {
    const scratch = await this.scratch();
    await this.command(1); await this.client.removeDir(scratch, this.options);
    await this.command(1); await this.client.renamePath(target, scratch, this.options);
    await this.erase(scratch);
  }
}

/** copies: device files that publish takes by source path (`copies` there), copied before the session (copyFirst). */
export async function packageTransaction<T>(client: Client, options: CallOptions, run: (session: PackageSession) => Promise<T>,
  more: { copies?: readonly string[] } = {}): Promise<T> {
  const session = new PackageSession(client, options);
  try {
    await session.copyFirst(more.copies ?? []);
    await session.acquire();
  } catch (error) {
    await session.dropCopies(); throw error;
  }
  try {
    const result = await run(session);
    await session.release(); await session.dropCopies(); return result;
  } catch (error) {
    // Preserve the operation's original error when its cable has gone away.
    try { await session.release(); await session.dropCopies(); } catch { /* the ten-second abandoned-owner timeout releases it */ }
    throw error;
  }
}
