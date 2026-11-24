// Mod packages on the device, as `xyf-host mods push | list | remove` (xy_forever/modsend.py): the
// host only puts packages on the OP-XY; the device assembles mod firmware from them itself.
// A package set is one generation folder as `modkit split` writes it: base/, <machine>/, ext-<id>/,
// each with info.txt and files.sha256 listing every file of the package. On the device it goes to
// MODS_ROOT/<generation>/<package>/<stored name>, the flat store (modstore.ts; #124: the dev menu's MTP depth
// limit; an older tree lists as older). Only the file methods are used. A push skips a package the device holds
// whole: every stored file with its size and the same files.sha256 (`verify` reads every file back). Within a package files.sha256 is
// written last and deleted first whenever anything in the package changes, so a package folder holding
// files.sha256 has every listed file; stored files it no longer names go after it.
// Building (applyMods, as `xyf-host mods apply --wait | cancel`): the host asks the device to assemble
// mod firmware from the packages it holds (mods.apply), watches the job (job.status) and can stop it
// (job.cancel); the device does all the work in TMP_ROOT/job-<id>/ and commits the result as library entry
// mods-a or mods-b, listed with its content not installed. makeDefault (library.set_default) installs an
// entry's content first when it is not installed (the content switch, a job followed through job.status).
import { sha256 } from './client.ts';
import type { CallOptions, Client, ReplyOf } from './client.ts';
import { depthRefusal, readsFlatMods } from './depth.ts';
import { STORE_FILE, STORE_VERSION, generationVersion, remoteTree, storeRefusal, storedName } from './modstore.ts';
import { XyfError, isXyfError } from './errors.ts';
import { parseFileList, parseManifest } from './package-manifest.ts';
import { NATIVE_MANIFEST, packageValues, recordName } from './package-records.ts';
import { packageTransaction } from './package-store.ts';
import { encodeModsApplyRequest } from './v1.ts';

export const MODS_ROOT = '/yaffs2/xy-forever/mods';
const LIST = 'files.sha256';
const INFO = 'info.txt';
const TOKEN = /^[a-z0-9][a-z0-9._-]{0,30}$/; // info.txt generation, profile
const MAX_PATH = 239; // max_path 240 with the NUL

/** The package set or the device's answer is not what a push or remove can use (modsend.py Refused). */
export class PackageError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PackageError';
  }
}

/** A generation folder: every file's path relative to it, '/'-separated ('base/manifest.json'), and its bytes. */
export interface PackageSource {
  paths(): Promise<readonly string[]> | readonly string[];
  read(path: string): Promise<Uint8Array>;
}

/**
 * The files of a folder chosen in a browser (`<input type="file" webkitdirectory>`): paths from
 * webkitRelativePath, the chosen folder's own name taken off. Choose the generation folder.
 */
export function fileListSource(files: Iterable<File>): PackageSource {
  const byPath = new Map<string, File>();
  for (const file of files) byPath.set(file.webkitRelativePath.split('/').slice(1).join('/'), file);
  return {
    paths: () => [...byPath.keys()],
    read: async (path) => {
      const file = byPath.get(path);
      if (!file) throw new PackageError(`${path}: not in the chosen folder`);
      return new Uint8Array(await file.arrayBuffer());
    },
  };
}

const hex = (b: Uint8Array): string => [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
const order = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

/** Metadata -> the semantic field view (legacy text or the named native record). */
export function infoValues(data: Uint8Array, name = INFO): Record<string, string> {
  return packageValues(data, name);
}

/** files.sha256 -> [sha256 hex, path] in file order. */
export function listPaths(data: Uint8Array): [string, string][] {
  try {
    return parseFileList(data);
  } catch (error) {
    throw new PackageError((error as Error).message);
  }
}

/** stored: its name in the device's store (modstore.ts). */
export interface LocalFile {
  readonly path: string;
  readonly data: Uint8Array;
  readonly stored: string;
}
export interface LocalSet {
  readonly profile: string;
  readonly generation: string;
  /** Each package's files in the order they are sent: listed files, then info.txt, then files.sha256, one per stored
   * name (files with the same content share it). Base first. */
  readonly packages: ReadonlyMap<string, readonly LocalFile[]>;
}

/**
 * Reads and checks a package set (modsend.py local_set): every listed file against its files.sha256
 * line, none empty, every device path usable. only: package names (base is always included).
 */
export async function localSet(source: PackageSource, only: readonly string[] = []): Promise<LocalSet> {
  const paths = new Set(await source.paths());
  const baseRecord = recordName([...paths].filter((path) => path.startsWith('base/')).map((path) => path.slice(5)));
  if (baseRecord === undefined)
    throw new PackageError('no base package metadata (choose the generation folder modkit split wrote)');
  const base = parseManifest(await source.read(`base/${baseRecord}`), baseRecord);
  const profile = base['profile'] ?? '',
    generation = base['generation'] ?? '';
  if (base['type'] !== 'mod-base' || !TOKEN.test(profile) || !TOKEN.test(generation)) {
    throw new PackageError(`base/${baseRecord}: expected a mod-base with a profile and generation`);
  }
  const names = [...paths]
    .filter((p) => p.split('/').length === 2 && p.endsWith(`/${LIST}`))
    .map((p) => p.split('/')[0]!)
    .sort(order);
  const wanted = only.length ? ['base', ...only.filter((n) => n !== 'base')] : names;
  const unknown = wanted.filter((n) => !names.includes(n));
  if (unknown.length) throw new PackageError(`no such package: ${unknown.join(', ')}`);
  const packages = new Map<string, LocalFile[]>();
  for (const name of [...new Set(wanted)].sort((a, b) => Number(a !== 'base') - Number(b !== 'base') || order(a, b))) {
    const listed = await source.read(`${name}/${LIST}`);
    const entries = listPaths(listed);
    const metadata = recordName(
      [...paths].filter((path) => path.startsWith(`${name}/`)).map((path) => path.slice(name.length + 1)),
    );
    if (metadata === undefined || !entries.some(([, path]) => path === metadata))
      throw new PackageError(`${name}/${LIST} does not list ${metadata ?? 'manifest.json or info.txt'}`);
    const files: LocalFile[] = [],
      shas = new Map<string, string>();
    for (const [sha, path] of entries) {
      const stored = storedName(path, sha),
        device = `${MODS_ROOT}/${generation}/${name}/${stored}`;
      if (
        path === LIST ||
        path.split('/').some((p) => p === '' || p === '.' || p === '..') ||
        new TextEncoder().encode(device).length > MAX_PATH
      ) {
        throw new PackageError(`${name}: unusable path ${JSON.stringify(path)}`);
      }
      const deep = depthRefusal(device);
      if (deep) throw new PackageError(`${name}: ${deep}`);
      const data = paths.has(`${name}/${path}`) ? await source.read(`${name}/${path}`) : null;
      if (data === null || hex(await sha256(data)) !== sha)
        throw new PackageError(`${name}/${path}: missing or differs from its ${LIST} line`);
      if (data.length === 0) throw new PackageError(`${name}/${path}: empty (the device stages 1 byte or more)`);
      if (shas.has(stored) && shas.get(stored) !== sha)
        throw new PackageError(`${name}: ${path} and another file share the stored name ${stored}`);
      if (!shas.has(stored)) files.push({ path, data, stored });
      shas.set(stored, sha);
    }
    if (shas.has(LIST)) throw new PackageError(`${name}: a file shares the stored name ${LIST}`);
    files.sort((a, b) => Number(a.path === metadata) - Number(b.path === metadata)); // selected metadata after payloads
    files.push({ path: LIST, data: listed, stored: LIST }); // files.sha256 last
    const fields = parseManifest(files.find((file) => file.path === metadata)!.data, metadata);
    if (
      fields['generation'] !== generation ||
      (fields['profile'] ?? profile) !== profile ||
      fields['type'] !== (name === 'base' ? 'mod-base' : 'mod')
    )
      throw new PackageError(`${name}: package dependencies or type differ`);
    packages.set(name, files);
  }
  return { profile, generation, packages };
}

export interface PackageResult {
  readonly name: string;
  /** sent: some files went over; skipped: every file was on the device with its size. */
  readonly state: 'sent' | 'skipped';
  /** Files sent, of the package's files. */
  readonly files: number;
  readonly of: number;
  readonly bytes: number;
  readonly seconds: number;
  /** Stored files of an earlier version deleted (no longer named by files.sha256). */
  readonly removed: number;
}
export interface PushSummary {
  readonly profile: string;
  readonly generation: string;
  readonly sent: number;
  readonly skipped: number;
  readonly files: number;
  readonly bytes: number;
  readonly seconds: number;
  readonly packages: readonly PackageResult[];
  /** Files read back identical (verify), else 0. */
  readonly verified: number;
}
export interface PushOptions extends CallOptions {
  /** These packages only (base is always sent). */
  only?: readonly string[] | undefined;
  /** Read every file back and compare (after the push). */
  verify?: boolean | undefined;
  /** After each package. */
  onPackage?: ((result: PackageResult) => void) | undefined;
  /** Bytes sent of the bytes to send (phase 'send'), then bytes read back of all (phase 'verify'). */
  onProgress?: ((done: number, total: number, phase: 'send' | 'verify') => void) | undefined;
}

/**
 * Destructive: puts the package set on the device (modsend.py push). Stops at the first failure: what
 * was written stays, and pushing again completes it. Show the user what goes where and ask first.
 */
export async function pushPackages(
  client: Client,
  source: PackageSource,
  options: PushOptions = {},
): Promise<PushSummary> {
  const call: CallOptions = { signal: options.signal, timeoutMs: options.timeoutMs };
  const { profile, generation, packages } = await localSet(source, options.only);
  flatMods(client, 'mods push');
  const genPath = `${MODS_ROOT}/${generation}`;
  const start = performance.now();
  return packageTransaction(client, call, async (session) => {
    const tree = await remoteTree(client, genPath, call);
    const refused = await storeRefusal(client, genPath, tree, call);
    if (refused) throw new PackageError(refused);
    const results: PackageResult[] = [];
    let done = 0,
      verified = 0;
    const total = [...packages.values()].reduce(
      (size, files) => size + files.reduce((sum, file) => sum + file.data.length, 0),
      0,
    );
    for (const [name, files] of packages) {
      const t0 = performance.now(),
        folder = `${genPath}/${name}`;
      const existing = await remoteTree(client, folder, call);
      // #306: the package is there when every stored file is, with its size, and its files.sha256 is this one's
      // (stored names carry each file's SHA-256, and the list is published last): one read, not every file's
      let same =
        existing !== null &&
        existing.files.size === files.length &&
        files.every((file) => existing.files.get(file.stored) === file.data.length);
      for (const file of same ? files : []) {
        if (!options.verify && file.stored !== LIST) continue;
        await session.command(1);
        const back = await client.readFile(`${folder}/${file.stored}`, call);
        if (back.length !== file.data.length || back.some((byte, index) => byte !== file.data[index])) {
          same = false;
          break;
        }
      }
      const stale = existing
        ? [...existing.files.keys()].filter((path) => !files.some((file) => file.stored === path)).length
        : 0;
      if (!same) {
        await session.publish(folder, new Map(files.map((file) => [file.stored, file.data])));
        done += files.reduce((sum, file) => sum + file.data.length, 0);
        options.onProgress?.(done, total, 'send');
      }
      if (options.verify) verified += files.length;
      const result: PackageResult = {
        name,
        state: same ? 'skipped' : 'sent',
        files: same ? 0 : files.length,
        of: files.length,
        bytes: same ? 0 : files.reduce((sum, file) => sum + file.data.length, 0),
        seconds: (performance.now() - t0) / 1000,
        removed: stale,
      };
      results.push(result);
      options.onPackage?.(result);
    }
    if (options.verify) options.onProgress?.(total, total, 'verify');
    return {
      profile,
      generation,
      sent: results.filter((result) => result.state === 'sent').length,
      skipped: results.filter((result) => result.state === 'skipped').length,
      files: results.reduce((sum, result) => sum + result.files, 0),
      bytes: done,
      seconds: (performance.now() - start) / 1000,
      packages: results,
      verified,
    };
  });
}

export interface HeldPackage {
  readonly name: string;
  /** Its info.txt (empty when absent). */
  readonly info: Readonly<Record<string, string>>;
  readonly files: number;
  readonly bytes: number;
  /** files.sha256 is there and every file it lists is too; false: an interrupted push (push again). */
  readonly complete: boolean;
}
/** older: a folder without store.txt (mods/<profile>/ from before protocol 1.16, or a package tree from before the flat
 * store), newer: a newer store version; either has profile '' and no packages; removePackage(generation) deletes it. */
export interface HeldGeneration {
  readonly profile: string;
  readonly generation: string;
  readonly packages: readonly HeldPackage[];
  readonly older: boolean;
  readonly newer: boolean;
}

/** A device before protocol 1.16 reads mods/<profile>/<generation>/: refused, nothing sent. */
function flatMods(client: Client, what: string): void {
  if (!readsFlatMods(client.info.proto)) {
    throw new XyfError(
      'update_required',
      `${what}: this OP-XY's XY Forever (protocol ${client.info.proto}) reads an older mod package layout: update it first`,
    );
  }
}

/** The generations and packages on the device (modsend.py holdings); [] when there are none. */
export async function listPackages(client: Client, options: CallOptions = {}): Promise<HeldGeneration[]> {
  const tree = await remoteTree(client, MODS_ROOT, options);
  if (!tree) return [];
  const nonfiles = new Set([...tree.dirs, ...tree.other]);
  const out: HeldGeneration[] = [];
  for (const gen of tree.dirs.filter((d) => !d.includes('/')).sort(order)) {
    const version = await generationVersion(
      client,
      `${MODS_ROOT}/${gen}`,
      tree.files.has(`${gen}/${STORE_FILE}`),
      options,
    );
    if (version !== STORE_VERSION) {
      out.push({
        profile: '',
        generation: gen,
        packages: [],
        older: version < STORE_VERSION,
        newer: version > STORE_VERSION,
      });
      continue;
    }
    const packages: HeldPackage[] = [];
    const names = tree.dirs
      .filter((d) => d.startsWith(`${gen}/`) && d.split('/').length === 2)
      .map((d) => d.split('/')[1]!)
      .sort(order);
    for (const name of names) {
      const prefix = `${gen}/${name}/`;
      const mine = new Map(
        [...tree.files].filter(([p]) => p.startsWith(prefix)).map(([p, s]) => [p.slice(prefix.length), s]),
      );
      let info: Record<string, string> = {},
        complete = false;
      const metadata = recordName([NATIVE_MANIFEST, INFO].filter((key) => mine.has(key) || nonfiles.has(prefix + key)));
      if (metadata !== undefined && mine.has(metadata))
        info = infoValues(await client.readFile(`${MODS_ROOT}/${prefix}${metadata}`, options), metadata);
      if (metadata !== undefined && mine.has(LIST)) {
        try {
          const listed = listPaths(await client.readFile(`${MODS_ROOT}/${prefix}${LIST}`, options));
          complete =
            listed.some(([, path]) => path === metadata) && listed.every(([s, p]) => mine.has(storedName(p, s)));
        } catch (error) {
          if (!(error instanceof PackageError)) throw error;
        }
      }
      packages.push({ name, info, files: mine.size, bytes: [...mine.values()].reduce((n, s) => n + s, 0), complete });
    }
    const base = packages.find((p) => p.name === 'base');
    out.push({ profile: base?.info['profile'] ?? '?', generation: gen, packages, older: false, newer: false });
  }
  return out;
}

export interface Removed {
  readonly path: string;
  readonly files: number;
  readonly bytes: number;
}

/**
 * Destructive: deletes a generation folder (what names a generation) or the package what from every
 * generation holding it (modsend.py remove); generation: from that generation only. Per folder:
 * each files.sha256 first, then the files, then the folders, deepest first. dryRun: only what would
 * go (for the confirmation). Returns the folders ([] when nothing is named what). Ask first.
 */
export async function removePackage(
  client: Client,
  what: string,
  options: CallOptions & { generation?: string | undefined; dryRun?: boolean | undefined } = {},
): Promise<Removed[]> {
  const call: CallOptions = { signal: options.signal, timeoutMs: options.timeoutMs };
  if (!options.dryRun)
    return packageTransaction(client, call, async (session) => {
      const targets = await removePackage(client, what, { ...options, dryRun: true });
      for (const target of targets) await session.remove(target.path);
      return targets;
    });
  const tree = await remoteTree(client, MODS_ROOT, call);
  const dirs = tree?.dirs ?? [];
  const inGeneration = (d: string): boolean =>
    options.generation === undefined || d.split('/')[0] === options.generation;
  let found = dirs.filter((d) => d === what && inGeneration(d));
  if (!found.length)
    found = dirs.filter((d) => d.split('/').length === 2 && d.split('/')[1] === what && inGeneration(d));
  const out: Removed[] = [];
  for (const rel of found) {
    const files = [...tree!.files]
      .filter(([p]) => p.startsWith(`${rel}/`))
      .map(([p, s]) => [p.slice(rel.length + 1), s] as const);
    const folder = `${MODS_ROOT}/${rel}`;
    out.push({ path: folder, files: files.length, bytes: files.reduce((n, [, s]) => n + s, 0) });
  }
  return out;
}

// ---- Building on the device (mods.apply, job.status, job.cancel; protocol 1.10) ------------------

export const TMP_ROOT = '/yaffs2/xy-forever/tmp';
export const LIBRARY_ROOT = '/yaffs2/xy-forever/firmware';
/** A job's work folder (a finished build is committed from it into the library; a failed one's debris stays here). */
export const jobFolder = (job: number): string => `${TMP_ROOT}/job-${job}`;

export const JOB_PHASES = [
  'idle',
  'inputs',
  'link',
  'patch',
  'content',
  'write',
  'verify',
  'finish',
  'done',
  'failed',
  'cancelled',
] as const;
export type JobPhase = (typeof JOB_PHASES)[number] | `phase ${number}`;
export const JOB_ERRORS = [
  'none',
  'argument',
  'path',
  'region',
  'open',
  'read',
  'truncated',
  'size',
  'list',
  'unlisted',
  'sha256',
  'info',
  'base',
  'linker',
  'patch',
  'content',
  'write',
  'rename',
  'verify',
  'cancelled',
  'image',
  'link',
] as const;
/** job.status error -> plain words. */
const ERROR_WORDS: Readonly<Record<string, string>> = {
  argument: 'the request was not usable',
  path: 'a path was not usable',
  region: 'the work area was too small',
  open: 'a file could not be opened (is every chosen package on the device?)',
  read: 'a file could not be read',
  truncated: 'a file was shorter than it should be',
  size: 'a file had the wrong size',
  list: "a package's files.sha256 could not be read",
  unlisted: "a file is not in its package's files.sha256",
  sha256: "a file differs from its package's files.sha256 (send the package again)",
  info: "a package's info.txt is not usable",
  base: 'the base is not a known stock firmware (confirm an unknown base to build anyway)',
  linker: 'the machines could not be linked into the firmware',
  patch: 'the firmware could not be patched',
  content: 'the content could not be put together',
  write: 'writing the result failed',
  rename: 'renaming a written file failed',
  verify: 'a written file read back differently',
  cancelled: 'it was cancelled',
  image: "the base's os.ldr is not a usable firmware image",
  link: 'processor A stopped answering',
};
/** mods.apply `missing` (with not_found) -> plain words. */
export const MISSING_WORDS = [
  '',
  'the base entry has no os.ldr (or there is no such entry)',
  "the base entry has no content.sha256 (the firmware's content list)",
  'there is no /yaffs2/content/op1_factory.json (the stock catalog)',
  "the generation's base package metadata is not on the device (send the package set first)",
] as const;
/** mods.apply refusals other than not_found -> plain words. */
const REFUSALS: Readonly<Record<string, string>> = {
  busy: 'the OP-XY is busy: a build is running or an upload is in progress; wait for it, or cancel the build',
  state: 'the OP-XY has no free job id (restart it)',
  storage: 'the OP-XY could not make the output folder',
  bad_request: 'an id, the profile, the generation or the name is not in the allowed form',
  sha: 'the request was damaged on the way; try again',
  link: 'processor A did not answer; try again, or restart the OP-XY',
};

export interface JobStatus {
  readonly job: number;
  readonly phase: JobPhase;
  /** Units of the phase done, of total (0: not known ahead): inputs bytes, link and patch steps, content packages, outputs. */
  readonly done: number;
  readonly total: number;
  /** 'none', or why it failed (JOB_ERRORS); code: the refusing step's own status number. */
  readonly error: string;
  readonly code: number;
  readonly elapsedMs: number;
  readonly steps: number;
  /** The longest menu-loop pass the job took. */
  readonly longestMs: number;
  /** Work-region bytes used at most. */
  readonly peak: number;
  /** A failed or cancelled job's files or folders may remain in its folder. */
  readonly debris: boolean;
  /** Which file, object, edit or check; when done, the library entry id (a build's mods-a or mods-b, or a content switch's target). */
  readonly detail: string;
  /** done, failed or cancelled. */
  readonly final: boolean;
  /** Its work folder in tmp/ (where a failed or cancelled job's debris stays). */
  readonly folder: string;
}

function jobStatusOf(f: ReplyOf<'job.status'>): JobStatus {
  const nul = f.detail.indexOf(0);
  return {
    job: f.job,
    phase: JOB_PHASES[f.phase] ?? `phase ${f.phase}`,
    done: f.done,
    total: f.total,
    error: JOB_ERRORS[f.error] ?? `error ${f.error}`,
    code: f.code,
    elapsedMs: f.elapsed_ms,
    steps: f.steps,
    longestMs: f.longest_ms,
    peak: f.peak,
    debris: f.debris !== 0,
    detail: String.fromCharCode(...(nul < 0 ? f.detail : f.detail.subarray(0, nul))),
    final: f.phase >= 8 && f.phase <= 10,
    folder: jobFolder(f.job),
  };
}

/**
 * A job that ended failed or cancelled; status: its final job.status. what: 'build' (mods.apply; an unknown base
 * unconfirmed: error patch, detail base) or 'content switch' (makeDefault; the default is unchanged).
 */
export class JobError extends Error {
  readonly status: JobStatus;
  readonly what: 'build' | 'content switch';
  constructor(status: JobStatus, what: 'build' | 'content switch' = 'build') {
    const left = status.debris ? `; files may remain in ${status.folder}/` : '';
    super(
      status.phase === 'cancelled'
        ? `the ${what} was cancelled${left}`
        : `the ${what} failed: ${ERROR_WORDS[status.error === 'patch' && status.detail === 'base' ? 'base' : status.error] ?? status.error} (${status.error}${status.code ? ` ${status.code}` : ''}` +
            `${status.detail ? `: ${status.detail}` : ''})${left}`,
    );
    this.name = 'JobError';
    this.status = status;
    this.what = what;
  }
}

/** The running or last job's progress (job 0), or that job's. A job this boot has not started: XyfError `not_found`. */
export async function jobStatus(client: Client, job = 0, options: CallOptions = {}): Promise<JobStatus> {
  return jobStatusOf(await client.call('job.status', { job }, options));
}

/**
 * Stops the running job (job 0) or that one; answers once its files are removed. A job that already ended, a build
 * in its library commit (phase finish, detail commit) or a content switch: XyfError `state`.
 */
export async function cancelJob(client: Client, job = 0, options: CallOptions = {}): Promise<{ debris: boolean }> {
  return { debris: (await client.call('job.cancel', { job }, options)).debris !== 0 };
}

export interface ApplyRequest {
  /** Library entry id whose os.ldr is the stock firmware (with its content.sha256). */
  base: string;
  profile: string;
  generation: string;
  /** 1 to 64 machine ids, in layout order. */
  machines: readonly string[];
  /** 0 to 8 extension ids. */
  extensions?: readonly string[] | undefined;
  /** The entry's name (1-47 printable characters). */
  name: string;
  /** The user confirmed a base that is not a known stock firmware (flags 1). */
  unverified?: boolean | undefined;
}
export interface ApplyOptions {
  /** Every job.status while it runs, and the final one. */
  onProgress?: ((status: JobStatus) => void) | undefined;
  /** Abort: sends job.cancel and waits for the clean-up (JobError, phase cancelled). */
  signal?: AbortSignal | undefined;
  /** Between job.status polls (default 500 ms). */
  pollMs?: number | undefined;
  /** Gives up waiting (XyfError `timeout`, the job keeps running) when its step count has not moved for this long (default 90 s). */
  stallMs?: number | undefined;
}

/** A finished build (applyMods): the library entry it became. */
export interface BuiltEntry {
  /** The library entry id (mods-a or mods-b; the final job.status detail). */
  readonly entry: string;
  /** Listed in the Firmware library but not bootable as it is: makeDefault installs its content. */
  readonly state: 'content not installed';
  /** The final job.status (phase done). */
  readonly status: JobStatus;
}

/**
 * Destructive: asks the device to build mod firmware from the packages it holds (mods.apply), then
 * polls job.status until the job ends. Resolves with the library entry the build became (mods-a or
 * mods-b, replacing the older of them; never the default or the boot-once entry), listed with its
 * content not installed: makeDefault installs it. A refusal is an XyfError with the device's code
 * (busy, not_found with fields.missing, ...) and the reason in plain words; a failed or cancelled job
 * is a JobError. While it runs, other commands that need storage answer `busy`. Ask the user first.
 */
export async function applyMods(
  client: Client,
  request: ApplyRequest,
  options: ApplyOptions = {},
): Promise<BuiltEntry> {
  const extensions = request.extensions ?? [];
  if (request.machines.length < 1 || request.machines.length > 64)
    throw new RangeError('mods.apply: choose 1 to 64 machines');
  if (extensions.length > 8) throw new RangeError('mods.apply: at most 8 extensions');
  flatMods(client, 'mods.apply');
  const fields = {
    base: request.base,
    profile: request.profile,
    generation: request.generation,
    machines: request.machines.join(','),
    extensions: extensions.join(','),
    name: request.name,
    flags: request.unverified ? 1 : 0,
  };
  const signal = options.signal;
  if (signal?.aborted)
    throw new XyfError('aborted', 'mods.apply: cancelled before it started', { command: 'mods.apply' });
  const body = encodeModsApplyRequest({ ...fields, sha256: new Uint8Array(32) }).subarray(0, -32);
  let job: number;
  try {
    ({ job } = await client.call('mods.apply', { ...fields, sha256: await sha256(body) }));
  } catch (error) {
    if (!isXyfError(error) || error.status === null) throw error;
    const missing = Number(error.fields?.['missing'] ?? 0);
    const reason =
      error.code === 'not_found'
        ? MISSING_WORDS[missing] || 'an input is missing'
        : (REFUSALS[error.code] ?? error.message);
    throw new XyfError(error.code, `mods.apply: ${reason}`, {
      command: error.command,
      status: error.status,
      fields: error.fields,
      cause: error,
    });
  }
  const status = await follow(client, job, 'build', options);
  return { entry: status.detail, state: 'content not installed', status };
}

/** Polls job.status until job ends; done: its status, failed or cancelled: JobError. signal: job.cancel (a build only). */
async function follow(
  client: Client,
  job: number,
  what: 'build' | 'content switch',
  options: ApplyOptions,
): Promise<JobStatus> {
  const signal = options.signal;
  const stallMs = options.stallMs ?? 90_000;
  const pollMs = options.pollMs ?? 500;
  let steps = -1,
    still = 0, // paused since the step count last moved (Client.pause: device time on the simulator)
    cancelled = false;
  for (;;) {
    if (signal?.aborted && !cancelled && what === 'build') {
      cancelled = true;
      try {
        await cancelJob(client, job);
      } catch (error) {
        if (!isXyfError(error, 'state')) throw error; // it ended first, or is committing: its status says how
      }
    }
    const status = await jobStatus(client, job);
    options.onProgress?.(status);
    if (status.phase === 'done') return status;
    if (status.final) throw new JobError(status, what);
    if (status.steps !== steps) {
      steps = status.steps;
      still = 0;
    }
    if (still > stallMs) {
      throw new XyfError(
        'timeout',
        `job ${job}: no progress for ${Math.round(stallMs / 1000)} s (${steps} steps); stopped waiting ` +
          '(cancelJob stops it)',
        { command: 'job.status' },
      );
    }
    // A cancel ends the pause at once: the next look sends job.cancel.
    if (!cancelled) {
      await client.pause(pollMs, what === 'build' ? signal : undefined).catch((error: unknown) => {
        if (!isXyfError(error, 'aborted')) throw error;
      });
      still += pollMs;
    }
  }
}

/** What makeDefault did: switched: the entry's content was installed first (status: the switch's final job.status). */
export interface DefaultResult {
  readonly id: string;
  readonly switched: boolean;
  readonly status: JobStatus | null;
}

/**
 * Destructive: makes library entry id the default firmware (library.set_default). An entry whose content is not
 * installed (a build's mods-a or mods-b, or any entry while another one's content is installed) gets its content
 * first: the device answers `started` and runs the content switch as a job, followed here through job.status
 * until it writes the default (done) or fails (JobError, what 'content switch'; the default is unchanged). It
 * cannot be cancelled. Ask the user first: it rewrites files in /yaffs2/content/.
 */
export async function makeDefault(
  client: Client,
  id: string,
  options: Omit<ApplyOptions, 'signal'> = {},
): Promise<DefaultResult> {
  try {
    await client.setDefault(id, { timeoutMs: 75_000 });
    return { id, switched: false, status: null };
  } catch (error) {
    if (!isXyfError(error, 'started')) throw error;
  }
  const status = await follow(client, 0, 'content switch', options);
  return { id, switched: true, status };
}

/** contentList: the entry has content.sha256, which a build needs of its base (`xyf-host library-add --content` writes it). */
export interface BaseEntry {
  readonly id: string;
  readonly name: string;
  readonly contentList: boolean;
}

/**
 * Firmware library entries that hold an os.ldr and are not assembled mod sets (no `generation` in info.txt: a
 * build's mods-a and mods-b are left out), the candidates for ApplyRequest.base, by id; name from info.txt (else the id).
 */
export async function libraryBases(client: Client, options: CallOptions = {}): Promise<BaseEntry[]> {
  let entries;
  try {
    entries = await client.listDir(LIBRARY_ROOT, options);
  } catch (error) {
    if (isXyfError(error, 'not_found')) return [];
    throw error;
  }
  const out: BaseEntry[] = [];
  for (const e of entries.filter((x) => x.kind === 'folder').sort((a, b) => order(a.name, b.name))) {
    const files = await client.listDir(`${LIBRARY_ROOT}/${e.name}`, options);
    if (!files.some((f) => f.kind === 'file' && f.name === 'os.ldr')) continue;
    const metadata = recordName(files.map((file) => file.name));
    const info =
      metadata !== undefined
        ? infoValues(await client.readFile(`${LIBRARY_ROOT}/${e.name}/${metadata}`, options), metadata)
        : {};
    if (info['generation']) continue; // an assembled mod set, not a stock base
    const contentList = files.some((f) => f.kind === 'file' && f.name === 'content.sha256');
    out.push({ id: e.name, name: info['name'] || e.name, contentList });
  }
  return out;
}
