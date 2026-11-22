// The call layer for apps: connect (hello, protocol 1.1+ required), the cmds check before every
// command, typed commands with per-command timeouts, status codes as XyfError, cancellation by
// AbortSignal, chunked transfers with progress, reconnect after a restart. Behaviour follows the
// Python host tool (xyf_host.py) and docs/PROTOCOL.md.
import { depthRefusal, foldersBelow } from './depth.ts';
import { XyfError, isXyfError, statusError } from './errors.ts';
import { Link } from './link.ts';
import type { Trace } from './link.ts';
import type { Opener, Transport } from './transport.ts';
import { keyValues, readLibraryEntry } from './package-records.ts';
import { SCREEN } from './screen.ts';
import { writeStream } from './stream.ts';
import { inWriteArea } from './write-area.ts';
import { RETRY_MS, type Trust } from './trust.ts';
import { fileReason } from './file-reason.ts';
import { retryBusy } from './busy.ts';
import * as v1 from './v1.ts';

type Codecs = typeof v1.codecs;
/** The commands an app may send (developer commands: the `dev` entry). */
export type ProductCommand = keyof Codecs;
export type RequestOf<N extends ProductCommand> = Parameters<Codecs[N]['encodeRequest']>[0];
export type ReplyOf<N extends ProductCommand> = Extract<ReturnType<Codecs[N]['decodeReply']>, { ok: true }>['fields'];

export interface AnyCodec {
  encodeRequest(values?: unknown): Uint8Array;
  decodeReply(payload: Uint8Array): v1.Reply<Record<string, v1.Value>, Record<string, v1.Value>>;
}

export interface CallOptions {
  /** Per request; default: the command's timeout from the spec. */
  timeoutMs?: number | undefined;
  signal?: AbortSignal | undefined;
}
export interface TransferOptions extends CallOptions {
  /** After each chunk: bytes done, and the total when known. */
  onProgress?: ((done: number, total: number | null) => void) | undefined;
}
export interface ConnectOptions {
  /** The whole search for the device (open the port, hello every helloEveryMs); default 10 s. */
  timeoutMs?: number | undefined;
  helloEveryMs?: number | undefined;
  signal?: AbortSignal | undefined;
  trace?: Trace | undefined;
}

export interface DeviceInfo {
  readonly hello: v1.Hello;
  /** Protocol version, `1.N`. */
  readonly proto: string;
  /** Release version and image (8 hex digits or `unknown`), from 1.15. */
  readonly version: string | null;
  readonly image: string | null;
  readonly mode: string | null;
  /** From hello: payload (largest payload), read (largest ranged read), stage (largest staged image). */
  readonly limits: { readonly payload: number; readonly read: number; readonly stage: number };
  /** The build answers developer commands. */
  readonly dev: boolean;
  /** From 1.23 (#65): Ask before USB control is on, and its question's state on this connection (else null). */
  readonly ask: boolean;
  readonly trust: Trust | null;
  /** From 1.25 (#191): false when USB mode on the OP-XY is MTP (hello serial=off): every command but hello is refused. */
  readonly serial: boolean;
}

/** A folder entry (file.list): the name decoded as UTF-8 (nameBytes: as the device sent it); size 0 for a folder. */
export interface DirEntry {
  readonly name: string;
  readonly nameBytes: Uint8Array;
  readonly kind: EntryKind;
  readonly size: number;
}
export type EntryKind = 'file' | 'folder' | 'other';
const KINDS: readonly EntryKind[] = ['file', 'folder', 'other'];

/** file.copy: the host waits the command's timeout plus 1 s per this many bytes (xyf_host.py COPY_RATE). */
const COPY_RATE = 10_000;

type ChangeCommand = 'file.write_staged' | 'file.delete' | 'file.rmdir' | 'file.mkdir' | 'file.rename' | 'file.copy';
type FileCommand = 'file.read' | 'file.list' | ChangeCommand;

export interface Staged {
  readonly size: number;
  readonly crc32: number;
  readonly sha256: Uint8Array;
}

export const STATE_FILES = ['/yaffs2/xy-forever/state-a.txt', '/yaffs2/xy-forever/state-b.txt'] as const;

/** tfw.check reasons (protocol/v1.json). */
export const TFW_REASONS = [
  'pass',
  'a read failed',
  'not a package, or its sizes differ from the file',
  'an AES key index other than 0',
  'no key in the OTP',
  'the public key is not on the curve',
  'the signature does not verify',
  "the device's key does not decrypt the package",
  'the ZIP directory is malformed',
] as const;

const EMPTY = new Uint8Array(0);

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', stop);
      resolve();
    }, ms);
    const stop = (): void => {
      clearTimeout(timer);
      reject(new XyfError('aborted', 'cancelled', { cause: signal?.reason }));
    };
    signal?.addEventListener('abort', stop, { once: true });
  });
}

function concat(parts: readonly Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

export async function sha256(data: Uint8Array): Promise<Uint8Array> {
  return new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', data.slice()));
}

function number(text: string | undefined, fallback: number): number {
  return text !== undefined && /^[0-9]+$/.test(text) ? parseInt(text, 10) : fallback;
}

/** Accepts protocol 1.1 and later 1.x; anything else is an error naming what to update. */
function deviceInfo(hello: v1.Hello): DeviceInfo {
  if (hello.major !== '1') {
    throw new XyfError(
      'protocol',
      `the device speaks protocol ${hello.proto}; this client speaks 1.x: update this app`,
    );
  }
  if (hello.preV1 || number(hello.proto.split('.')[1], 0) < 1) {
    throw new XyfError(
      'update_required',
      `this OP-XY runs an old XY Forever (protocol ${hello.proto}, build 153 or earlier): update XY Forever to use this app`,
    );
  }
  const k = hello.keys;
  return {
    hello,
    proto: hello.proto,
    version: k['version'] ?? null,
    image: k['image'] ?? null,
    mode: k['mode'] ?? null,
    dev: k['dev'] === '1',
    ask: k['confirm'] === '1',
    trust: (k['trust'] as Trust | undefined) ?? null,
    serial: k['serial'] !== 'off',
    limits: {
      payload: number(k['payload'], v1.MAX_PAYLOAD),
      read: number(k['read'], v1.MAX_READ),
      stage: number(k['stage'], v1.MAX_STAGE),
    },
  };
}

/** Opens the transport and sends hello every helloEveryMs until the device answers. */
async function handshake(open: Opener, options: ConnectOptions): Promise<[Link, DeviceInfo]> {
  const timeoutMs = options.timeoutMs ?? 10_000,
    every = options.helloEveryMs ?? 500,
    signal = options.signal;
  const deadline = Date.now() + timeoutMs;
  let last: unknown;
  while (Date.now() < deadline) {
    if (signal?.aborted) throw new XyfError('aborted', 'cancelled', { cause: signal.reason });
    let transport: Transport;
    try {
      transport = await open(signal);
    } catch (error) {
      if (isXyfError(error, 'aborted')) throw error;
      last = error; // not there yet (restarting, port not back)
      await sleep(Math.max(0, Math.min(100, deadline - Date.now())), signal);
      continue;
    }
    const link = new Link(transport, options.trace ?? null);
    try {
      while (!link.closed && Date.now() < deadline) {
        let reply: v1.HelloReply;
        try {
          reply = v1.decodeHelloReply(
            await link.request(v1.TYPE.hello, EMPTY, {
              timeoutMs: Math.max(1, Math.min(every, deadline - Date.now())),
              signal,
            }),
          );
        } catch (error) {
          if (!isXyfError(error, 'timeout', 'disconnected')) throw error;
          last = error;
          continue;
        }
        if (reply.ok) return [link, deviceInfo(v1.parseHello(reply.fields.info))];
        last = statusError('hello', reply.status);
        await sleep(every, signal);
      }
    } catch (error) {
      await link.close();
      if (error instanceof XyfError) throw error;
      throw new XyfError('protocol', `hello: unreadable reply: ${(error as Error).message}`, { cause: error });
    }
    await link.close();
  }
  throw new XyfError(
    'timeout',
    `XY Forever did not answer within ${timeoutMs} ms: is the OP-XY in the XY Forever menu?`,
    { cause: last },
  );
}

/** Sends one command after the cmds check; resolves with its ok fields, or throws XyfError. */
export async function invoke(
  client: Client,
  command: v1.Command,
  codec: AnyCodec,
  request: unknown,
  options: CallOptions = {},
): Promise<Record<string, v1.Value>> {
  if (command.name !== 'hello' && !client.info.hello.answers(command.type)) {
    throw new XyfError(
      'update_required',
      `this OP-XY's XY Forever (${client.info.version ?? 'old'}) does not answer ` +
        `${command.name}: update XY Forever`,
      { command: command.name },
    );
  }
  const payload = codec.encodeRequest(request); // a bad argument throws TypeError/RangeError
  let decoded: ReturnType<AnyCodec['decodeReply']>,
    asking = false;
  try {
    for (;;) {
      // #65 (trust.ts): sent again while the device asks
      const reply = await client.link.request(command.type, payload, {
        timeoutMs: options.timeoutMs ?? command.timeoutMs,
        signal: options.signal,
      });
      try {
        decoded = codec.decodeReply(reply);
      } catch (error) {
        throw new XyfError('protocol', `${command.name}: unreadable reply: ${(error as Error).message}`, {
          command: command.name,
          cause: error,
        });
      }
      if (decoded.ok || decoded.status !== v1.STATUS.waiting || command.name === 'hello') break;
      if (!asking) client.asking((asking = true));
      await sleep(RETRY_MS, options.signal);
    }
  } finally {
    if (asking) client.asking(false);
  }
  if (!decoded.ok) throw statusError(command.name, decoded.status, decoded.fields);
  return decoded.fields;
}

export class Client {
  private current: Link;
  private infoNow: DeviceInfo;
  private readonly open: Opener;
  private readonly options: ConnectOptions;
  private readonly logListeners = new Set<(event: v1.LogEvent) => void>();
  private readonly askListeners = new Set<(asking: boolean) => void>();

  /** Finds the device and says hello; throws XyfError `update_required` for a pre-1.1 device. */
  static async connect(open: Opener, options: ConnectOptions = {}): Promise<Client> {
    const [link, info] = await handshake(open, options);
    return new Client(open, options, link, info);
  }

  private constructor(open: Opener, options: ConnectOptions, link: Link, info: DeviceInfo) {
    this.open = open;
    this.options = options;
    this.current = link;
    this.infoNow = info;
    this.attach(link);
  }

  get info(): DeviceInfo {
    return this.infoNow;
  }
  get link(): Link {
    return this.current;
  }
  get connected(): boolean {
    return !this.current.closed;
  }

  /** A pause between polls: ms of the device's time (the transport's pause, the simulator's clock), else of wall
   * time. Rejects with XyfError `aborted` when signal fires first. */
  async pause(ms: number, signal?: AbortSignal): Promise<void> {
    const transport = this.current.transport;
    if (!transport.pause) return sleep(ms, signal);
    if (signal?.aborted) throw new XyfError('aborted', 'cancelled', { cause: signal.reason });
    await transport.pause(ms);
  }

  /** log events (install progress lines); returns the unsubscribe function. */
  onLog(listener: (event: v1.LogEvent) => void): () => void {
    this.logListeners.add(listener);
    return () => this.logListeners.delete(listener);
  }

  /**
   * #65: the device asks "Allow this computer to control your OP-XY?" (true: show ASK_TEXT, the call waits for the
   * answer and sends again) or no longer does for that call (false); returns the unsubscribe function.
   */
  onAsk(listener: (asking: boolean) => void): () => void {
    this.askListeners.add(listener);
    return () => this.askListeners.delete(listener);
  }

  /** @internal invoke's news for onAsk listeners. */
  asking(on: boolean): void {
    for (const listener of this.askListeners) listener(on);
  }

  /** After a restart: waits up to goneMs for the old link to drop, then finds the device again. */
  async reconnect(options: ConnectOptions & { goneMs?: number } = {}): Promise<DeviceInfo> {
    if (!this.current.closed) await this.current.waitClosed(options.goneMs ?? 0);
    await this.current.close();
    const [link, info] = await handshake(this.open, { ...this.options, ...options });
    this.current = link;
    this.infoNow = info;
    this.attach(link);
    return info;
  }

  close(): Promise<void> {
    return this.current.close();
  }

  /** Any product command by name, typed from the spec. */
  async call<N extends ProductCommand>(name: N, request: RequestOf<N>, options: CallOptions = {}): Promise<ReplyOf<N>> {
    return (await invoke(
      this,
      v1.COMMANDS[name],
      v1.codecs[name] as unknown as AnyCodec,
      request,
      options,
    )) as ReplyOf<N>;
  }

  /** hello again: a liveness check that also refreshes info. */
  async status(options: CallOptions = {}): Promise<DeviceInfo> {
    this.infoNow = deviceInfo(v1.parseHello((await this.call('hello', undefined, options)).info));
    return this.infoNow;
  }

  /** One ranged read: up to length (at most info.limits.read) bytes from offset; fewer at the end. */
  async readRange(path: string, offset: number, length: number, options: CallOptions = {}): Promise<Uint8Array> {
    return (await this.fileCall('file.read', { path, offset, length }, path, options)).data;
  }

  /** A whole file of any size, in ranges of info.limits.read bytes until a short one. */
  async readFile(path: string, options: TransferOptions = {}): Promise<Uint8Array> {
    const step = this.infoNow.limits.read;
    const parts: Uint8Array[] = [];
    for (let done = 0; ;) {
      const data = await this.readRange(path, done, step, options);
      parts.push(data);
      done += data.length;
      options.onProgress?.(done, null);
      if (data.length < step) return concat(parts);
    }
  }

  /** The raw framebuffer (SCREEN.bytes); decodeScreen() turns it into pixels. */
  async screenshot(options: TransferOptions = {}): Promise<Uint8Array> {
    const out = new Uint8Array(SCREEN.bytes);
    for (let done = 0; done < SCREEN.bytes;) {
      const { pixels } = await this.call('screen.read', { offset: done }, options);
      if (pixels.length === 0)
        throw new XyfError('protocol', `screen.read at ${done}: no data`, { command: 'screen.read' });
      out.set(pixels.subarray(0, SCREEN.bytes - done), done);
      done += pixels.length;
      options.onProgress?.(Math.min(done, SCREEN.bytes), SCREEN.bytes);
    }
    return out;
  }

  /** Stages data in the device's RAM (stage.begin, stage.data in chunks, stage.end). */
  async stage(data: Uint8Array, options: TransferOptions = {}): Promise<Staged> {
    const limit = this.infoNow.limits.stage;
    if (data.length === 0 || data.length > limit) {
      throw new XyfError(
        'too_large',
        `stage: ${data.length} bytes; this OP-XY takes 1 to ${limit} bytes at a time (hello stage=)`,
        { command: 'stage.begin' },
      );
    }
    const staged: Staged = { size: data.length, crc32: v1.crc32(data), sha256: await sha256(data) };
    await this.call('stage.begin', staged, options);
    const chunk = Math.min(v1.PUT_CHUNK, this.infoNow.limits.payload - 4);
    for (let at = 0; at < data.length; at += chunk) {
      const piece = data.subarray(at, at + chunk);
      const { received } = await this.call('stage.data', { offset: at, data: piece }, options);
      if (received !== at + piece.length) {
        throw new XyfError('protocol', `stage.data: the device has ${received} bytes, expected ${at + piece.length}`, {
          command: 'stage.data',
        });
      }
      options.onProgress?.(at + piece.length, data.length);
    }
    await this.call('stage.end', undefined, options);
    return staged;
  }

  /**
   * Destructive: creates or replaces the file at path (under WRITE_ROOTS; missing folders are created)
   * and checks the device's count and read-back CRC. 1.18: streamed into tmp/ and renamed onto path in
   * one step, any size (stream.ts); before: staged (progress: the staging; above info.limits.stage, or
   * empty: `too_large` here). Outside WRITE_ROOTS: `refused` here (status null), nothing sent. Ask first.
   */
  async writeFile(
    path: string,
    data: Uint8Array,
    options: TransferOptions = {},
  ): Promise<{ written: number; readbackCrc: number }> {
    checkChange('file.write_staged', path, path);
    if (this.infoNow.hello.answers(v1.COMMANDS['file.write_begin'].type)) return writeStream(this, path, data, options);
    const staged = await this.stage(data, options);
    const reply = await this.fileCall('file.write_staged', { path, sha256: staged.sha256 }, path, {
      signal: options.signal,
      timeoutMs: options.timeoutMs ?? 60_000 + Math.ceil(data.length / 20),
    });
    if (reply.written !== data.length || reply.readback_crc !== staged.crc32) {
      throw new XyfError(
        'crc',
        `file.write_staged ${path}: ${reply.written} of ${data.length} bytes written, read-back CRC ` +
          `${reply.readback_crc.toString(16)}, expected ${staged.crc32.toString(16)}`,
        { command: 'file.write_staged' },
      );
    }
    return { written: reply.written, readbackCrc: reply.readback_crc };
  }

  /** Every entry of a folder, in the device's order (file.list, page by page to the end). */
  async listDir(path: string, options: CallOptions = {}): Promise<DirEntry[]> {
    const out: DirEntry[] = [];
    for (;;) {
      const page = await this.fileCall('file.list', { path, skip: out.length }, path, options);
      const entries = parseEntries(page.entries);
      if (entries.length !== page.count) {
        throw new XyfError(
          'protocol',
          `file.list ${path}: ${page.count} entries announced, ${entries.length} decoded`,
          { command: 'file.list' },
        );
      }
      out.push(...entries);
      if (!page.more) return out;
    }
  }

  /** Destructive: deletes one file (under WRITE_ROOTS). Show the user the path and ask first. */
  async deleteFile(path: string, options: CallOptions = {}): Promise<void> {
    await this.changePath('file.delete', path, options);
  }

  /** Destructive: removes one empty folder (under WRITE_ROOTS); one with entries is `state`. Ask first. */
  async removeDir(path: string, options: CallOptions = {}): Promise<void> {
    await this.changePath('file.rmdir', path, options);
  }

  /** Destructive: creates one folder (under WRITE_ROOTS); its parent must exist (`not_found`), the name must not (`storage`). */
  async makeDir(path: string, options: CallOptions = {}): Promise<void> {
    await this.changePath('file.mkdir', path, options);
  }

  /** file.delete, file.rmdir or file.mkdir of path, bound to the path's SHA-256; outside WRITE_ROOTS: `refused` here. */
  private async changePath(
    name: 'file.delete' | 'file.rmdir' | 'file.mkdir',
    path: string,
    options: CallOptions,
  ): Promise<void> {
    checkChange(name, path, path);
    await this.fileCall(name, { path, sha256: await sha256(new TextEncoder().encode(path)) }, path, options);
  }

  /** A file command whose device errors carry a plain reason (fileReason) and the path in the message; `busy` while
   * the write window closes is waited out (busy.ts). */
  private async fileCall<N extends FileCommand>(
    name: N,
    request: RequestOf<N>,
    path: string,
    options: CallOptions,
  ): Promise<ReplyOf<N>> {
    try {
      return await retryBusy(this, () => this.call(name, request, options), options);
    } catch (error) {
      throw plainFileError(error, path);
    }
  }

  /**
   * Destructive: renames or moves a file or a whole folder (file.rename, 1.8). Both paths under
   * WRITE_ROOTS (else `refused` here, nothing sent); the target must not exist (`state`, nothing
   * replaced) and its parent must (`not_found`); a folder into itself is `storage`. Ask first.
   */
  async renamePath(source: string, target: string, options: CallOptions = {}): Promise<void> {
    const label = `${source} -> ${target}`;
    checkChange('file.rename', source, label);
    checkChange('file.rename', target, label);
    await this.fileCall(
      'file.rename',
      { source, target, sha256: await sha256(pathPair(source, target)) },
      label,
      options,
    );
  }

  /**
   * Destructive: copies one file on the device (file.copy, 1.9; the bytes never cross USB): at most
   * info.limits.stage bytes (`too_large`); the target under WRITE_ROOTS (else `refused` here, nothing
   * sent), not existing (`state`), its folder existing (`not_found`). Waits the command's timeout
   * plus 1 s per 10000 bytes of size (default: the source's size from its folder's listing).
   * The copy discards anything staged before. Ask first.
   */
  async copyFile(
    source: string,
    target: string,
    options: CallOptions & { size?: number } = {},
  ): Promise<{ bytes: number }> {
    const label = `${source} -> ${target}`;
    checkChange('file.copy', target, label);
    let timeoutMs = options.timeoutMs;
    if (timeoutMs === undefined) {
      const size = options.size ?? (await this.entryOf(source, options))?.size ?? 0;
      timeoutMs = v1.COMMANDS['file.copy'].timeoutMs + Math.ceil((size * 1000) / COPY_RATE);
    }
    const reply = await this.fileCall(
      'file.copy',
      { source, target, sha256: await sha256(pathPair(source, target)) },
      label,
      { signal: options.signal, timeoutMs },
    );
    return { bytes: reply.bytes };
  }

  /**
   * Destructive: copies a file, or a folder and everything in it, as `xyf_host.py cp -r`: a folder is
   * makeDir of the target, then copyTree of each entry of its listing (an entry neither file nor
   * folder is skipped); a file is copyFile. The target must not exist; one inside the source is a
   * RangeError, nothing sent. Stops at the first failure (what was copied stays). Progress: bytes
   * copied so far. Ask first.
   */
  async copyTree(
    source: string,
    target: string,
    options: TransferOptions = {},
  ): Promise<{ files: number; bytes: number }> {
    source = source.replace(/\/+$/, '');
    target = target.replace(/\/+$/, '');
    if (target === source || target.startsWith(source + '/'))
      throw new RangeError(`copyTree: the target ${target} is inside the source ${source}`);
    const entry = await this.entryOf(source, options);
    if (entry === null)
      throw new XyfError('not_found', `file.list ${source}: no such file or folder`, { command: 'file.list' });
    if (entry.kind === 'folder')
      checkChange('file.mkdir', target, target, await foldersBelow((p) => this.listDir(p, options), source));
    const done = { files: 0, bytes: 0 };
    await this.copyEntry(source, target, entry, done, options);
    return done;
  }

  private async copyEntry(
    source: string,
    target: string,
    entry: DirEntry,
    done: { files: number; bytes: number },
    options: TransferOptions,
  ): Promise<void> {
    const call: CallOptions = { signal: options.signal, timeoutMs: options.timeoutMs };
    if (entry.kind === 'file') {
      done.bytes += (await this.copyFile(source, target, { ...call, size: entry.size })).bytes;
      done.files += 1;
      options.onProgress?.(done.bytes, null);
    } else if (entry.kind === 'folder') {
      await this.makeDir(target, call);
      for (const inner of await this.listDir(source, call)) {
        await this.copyEntry(`${source}/${inner.name}`, `${target}/${inner.name}`, inner, done, options);
      }
    }
  }

  /** The entry for path in its folder's listing, or null when the folder does not hold it. */
  private async entryOf(path: string, options: CallOptions): Promise<DirEntry | null> {
    const at = path.lastIndexOf('/');
    const parent = path.slice(0, at) || '/yaffs2',
      name = path.slice(at + 1);
    const call: CallOptions = { signal: options.signal, timeoutMs: options.timeoutMs };
    return (await this.listDir(parent, call)).find((e) => e.name === name) ?? null;
  }

  /** Destructive: installs the staged XY Forever build (sha256: the staged image's, again). Takes minutes. */
  bootInstall(sha: Uint8Array, options: CallOptions = {}): Promise<ReplyOf<'boot.install'>> {
    return this.call('boot.install', { sha256: sha }, options);
  }

  /** Destructive: puts the staged stock 1.2.1 os.ldr back into the boot partition. */
  bootUninstall(sha: Uint8Array, options: CallOptions = {}): Promise<ReplyOf<'boot.uninstall'>> {
    return this.call('boot.uninstall', { sha256: sha }, options);
  }

  /** Restarts the device (to the menu, or the default firmware) and waits up to goneMs for the link to drop. */
  async reboot(to: 'menu' | 'os' = 'menu', options: CallOptions & { goneMs?: number } = {}): Promise<void> {
    await this.call('device.reboot', { target: to === 'os' ? 1 : 2 }, options);
    if (!(await this.current.waitClosed(options.goneMs ?? 5000))) await this.current.close();
  }

  /** Leaves the menu for the default firmware (the link then drops), or a dry run of that boot. */
  async continueBoot(options: CallOptions & { dryRun?: boolean } = {}): Promise<void> {
    await this.call('menu.continue', { mode: options.dryRun ? 1 : 0 }, options);
  }

  /**
   * Boots library entry id once, as PLAY on it in the library menu (1.7): the menu closes, USB goes
   * away (this waits up to goneMs for the link to drop) and the OS does not answer this client; the
   * default stays. Back to XY Forever needs the user: power off, power on holding PLAY. confirm must
   * be true (as the host tool's --yes-boot); without it nothing is sent.
   */
  async libraryBoot(id: string, options: CallOptions & { confirm: true; goneMs?: number }): Promise<void> {
    if (options?.confirm !== true)
      throw new TypeError('libraryBoot: pass { confirm: true } to boot (the device leaves XY Forever)');
    await this.call('library.boot', { id, sha256: await sha256(new TextEncoder().encode(id)) }, options);
    if (!(await this.current.waitClosed(options.goneMs ?? 5000))) await this.current.close();
  }

  /** Stay in the XY Forever menu across restarts until power-off. */
  async hold(on: boolean, options: CallOptions = {}): Promise<void> {
    await this.call('service.hold', { on: on ? 1 : 0 }, options);
  }

  /** Destructive: makes library entry id the default firmware. */
  async setDefault(id: string, options: CallOptions = {}): Promise<void> {
    const { id_hash } = await this.call('library.set_default', { id }, options);
    if (id_hash !== v1.crc32(new TextEncoder().encode(id))) {
      throw new XyfError('protocol', `library.set_default: the device hashed another id (${id_hash.toString(16)})`, {
        command: 'library.set_default',
      });
    }
  }

  /** Is the .tfw at path official, and do the device's own keys open it? Reads only. */
  async tfwCheck(
    path: string,
    options: CallOptions = {},
  ): Promise<{ pass: boolean; reason: number; why: string; publicKeySha256: Uint8Array }> {
    const r = await this.call('tfw.check', { path }, options);
    return {
      pass: r.verdict === 0,
      reason: r.reason,
      why: TFW_REASONS[r.reason] ?? `reason ${r.reason}`,
      publicKeySha256: r.public_key_sha256,
    };
  }

  /** The default firmware: the valid state file with the higher seq (null: none valid). */
  async readState(
    options: CallOptions = {},
  ): Promise<{ file: string; seq: number; default: string | null; fields: Record<string, string> } | null> {
    let best: { file: string; seq: number; default: string | null; fields: Record<string, string> } | null = null;
    for (const file of STATE_FILES) {
      let data: Uint8Array;
      try {
        data = await this.readFile(file, options);
      } catch (error) {
        if (isXyfError(error, 'not_found')) continue;
        throw error;
      }
      const slot = parseSlot(data);
      if (slot && (best === null || slot.seq > best.seq))
        best = { file, ...slot, default: slot.fields['default'] ?? null };
    }
    return best;
  }

  /** A library entry's metadata, with native JSON taking precedence over legacy text. */
  async libraryEntry(id: string, options: CallOptions = {}): Promise<Record<string, string>> {
    return readLibraryEntry(this, id, options);
  }

  private attach(link: Link): void {
    link.onEvent((f) => {
      if (f.type !== v1.TYPE.log) return; // unknown events are ignored
      let event: v1.LogEvent;
      try {
        event = v1.decodeLogEvent(f.payload);
      } catch {
        return;
      }
      for (const listener of this.logListeners) listener(event);
    });
  }
}

/** file.list entries: {u8 kind, u32 size, u8 name_bytes, name} each, exactly filling the bytes. */
function parseEntries(data: Uint8Array): DirEntry[] {
  const out: DirEntry[] = [];
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const utf8 = new TextDecoder();
  let at = 0;
  while (at + 6 <= data.length) {
    const kind = view.getUint8(at),
      size = view.getUint32(at + 1, true),
      n = view.getUint8(at + 5);
    if (at + 6 + n > data.length) break;
    const nameBytes = data.slice(at + 6, at + 6 + n);
    out.push({ name: utf8.decode(nameBytes), nameBytes, kind: KINDS[kind] ?? 'other', size });
    at += 6 + n;
  }
  if (at !== data.length)
    throw new XyfError('protocol', `file.list: ${data.length - at} bytes after the last whole entry`, {
      command: 'file.list',
    });
  return out;
}

/** The SHA-256 input of file.rename and file.copy: source, a NUL, target. */
function pathPair(source: string, target: string): Uint8Array {
  const e = new TextEncoder();
  return new Uint8Array([...e.encode(source), 0, ...e.encode(target)]);
}

/** Before anything is sent: a change outside the write area is `refused` here (status null), as the device would answer. */
function checkChange(command: ChangeCommand, path: string, label: string, below = 0): void {
  const deep =
    command === 'file.delete' || command === 'file.rmdir' ? null : depthRefusal(path, command === 'file.mkdir', below);
  if (inWriteArea(path) && !deep) return;
  const reason = deep ?? fileReason(new XyfError('refused', '', { command }));
  throw new XyfError('refused', `${command} ${label}: ${reason}`, { command });
}

/** The same error with the path and the plain reason in its message (device errors only). */
function plainFileError(error: unknown, path: string): unknown {
  if (!isXyfError(error) || error.status === null) return error;
  return new XyfError(error.code, `${error.command ?? 'file'} ${path}: ${fileReason(error)}`, {
    command: error.command,
    status: error.status,
    fields: error.fields,
    cause: error,
  });
}

/** A state file (xyf::state::parse_slot): key=value lines, then `crc32=xxxxxxxx` over the lines before it. */
export function parseSlot(data: Uint8Array): { seq: number; fields: Record<string, string> } | null {
  if (data.length < 16 || data.length > 4096 || data[data.length - 1] !== 0x0a) return null;
  const line = data.length - 15;
  const tail = new TextDecoder().decode(data.subarray(line, data.length - 1));
  if (!/^crc32=[0-9a-f]{8}$/.test(tail) || (line > 0 && data[line - 1] !== 0x0a)) return null;
  if (parseInt(tail.slice(6), 16) !== v1.crc32(data.subarray(0, line))) return null;
  const fields = keyValues(new TextDecoder().decode(data.subarray(0, line)));
  const seq = fields['seq'];
  return seq !== undefined && /^[0-9]+$/.test(seq) ? { seq: parseInt(seq, 10), fields } : null;
}
