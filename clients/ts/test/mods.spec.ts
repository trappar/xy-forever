// Mod packages (src/mods.ts) against the simulator (test/simulators.ts, its own `mods` simulator), with
// the synthetic package set of tests/test_modsend.py: a push leaves the set byte-identical, stored flat, under
// /yaffs2/xy-forever/mods/<generation>/ and verify passes; a second push sends nothing, and
// neither does `xyf_host.py mods push` (the Python tool agrees on the layout); a push cut off after N
// files never leaves a package folder holding files.sha256 without every listed file, and the next
// push sends exactly the rest; only; list (an incomplete package flagged); remove. Checked through the
// simulator's /yaffs2 folder on the Mac. Building on the device (applyMods): beside the pushed set, a
// base entry and catalog as tests/test_sim_mod_job.py seeds them (makeModBase); a build becomes library
// entry mods-a (its os.ldr in firmware/, tmp/job-<id>/ gone), left out of libraryBases, content not installed;
// makeDefault on it runs the content switch and writes the default; refusals and a failed job in plain words; cancel mid-job (A slowed); a
// storage command during the job is the typed busy. Runs in order (one client at a time).
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cpSync, existsSync, mkdtempSync, readFileSync, readdirSync, renameSync, rmSync, statSync, truncateSync, unlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative, sep } from 'node:path';
import { afterAll, beforeAll, beforeEach, describe, expect, inject, test, vi } from 'vitest';
import { Client, JobError, PackageError, XyfError, applyMods, cancelJob, folderSource, infoValues, isXyfError, jobFolder, jobStatus, libraryBases, listPackages,
  listPaths, makeDefault, localSet, pushPackages, removePackage, tcpOpener } from '../src/node.ts';
import { STORE_FILE, STORE_TEXT, storedName } from '../src/modstore.ts';
import type { ApplyRequest, JobStatus, PackageResult } from '../src/node.ts';
import { REPO, control, makeModBase, makeModSet } from './simulators.ts';

const sims = inject('sims');
const sim = sims.mods;

/** Every file below dir: relative path -> bytes. */
function tree(dir: string): Map<string, Buffer> {
  const out = new Map<string, Buffer>();
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir, { recursive: true }) as string[]) {
    const path = join(dir, name);
    if (statSync(path).isFile()) out.set(relative(dir, path).split(sep).join('/'), readFileSync(path));
  }
  return out;
}

describe.skipIf(!sim)(`mod packages against the simulator${sim ? '' : ` (skipped: ${sims.why})`}`, () => {
  let work: string, gen: string, stored: string, profile: string, generation: string;
  const source = () => folderSource(gen);
  const clients: Client[] = [];
  const connect = async (): Promise<Client> => {
    const client = await Client.connect(tcpOpener(sim!.address), { timeoutMs: 5000 });
    clients.push(client);
    return client;
  };
  /** One push on its own connection, closed afterwards. */
  const push = async (options: Parameters<typeof pushPackages>[2] = {}) => {
    const client = await connect();
    try { return await pushPackages(client, source(), options); } finally { await client.close(); }
  };
  /** A push whose connection drops before write number after + 1 (the device keeps what it wrote). */
  const cutPush = async (after: number): Promise<void> => {
    const client = await connect();
    let left = after;
    const write = client.writeFile.bind(client);
    vi.spyOn(client, 'writeFile').mockImplementation(async (...args) => {
      if (left-- <= 0) { await client.close(); throw new Error('cut'); }
      return write(...args);
    });
    await expect(pushPackages(client, source())).rejects.toThrow('cut');
    await control(sim!.control, 'advance 11000');
  };
  /** What a push leaves (the flat store, #124): store.txt, each package's files under their stored names. */
  const host = (): Map<string, Buffer> => {
    const out = new Map<string, Buffer>([[STORE_FILE, Buffer.from(STORE_TEXT)]]);
    for (const name of readdirSync(gen)) {
      const list = readFileSync(join(gen, name, 'files.sha256'));
      for (const [sha, rel] of listPaths(list)) out.set(`${name}/${storedName(rel, sha)}`, readFileSync(join(gen, name, rel)));
      out.set(`${name}/files.sha256`, list);
    }
    return out;
  };
  /** The package files of host() (store.txt is no package's). */
  const packageFiles = (): Map<string, Buffer> => { const m = host(); m.delete(STORE_FILE); return m; };
  const assertStored = (names?: readonly string[]): void => {
    const want = [...host()].filter(([p]) => !names || p === STORE_FILE || names.includes(p.split('/')[0]!));
    const have = tree(stored);
    expect([...have.keys()].sort()).toEqual(want.map(([p]) => p).sort());
    expect(want.filter(([p, b]) => !have.get(p)!.equals(b)).map(([p]) => p)).toEqual([]);
  };
  /** Every package folder holding files.sha256 has every listed file with its listed SHA-256. */
  const assertNothingFalseComplete = (): void => {
    for (const name of existsSync(stored) ? readdirSync(stored) : []) {
      const list = join(stored, name, 'files.sha256');
      if (!existsSync(list)) continue;
      for (const [sha, rel] of listPaths(readFileSync(list))) {
        const file = join(stored, name, storedName(rel, sha));
        expect(existsSync(file), `${name}/${rel}`).toBe(true);
        expect(createHash('sha256').update(readFileSync(file)).digest('hex'), `${name}/${rel}`).toBe(sha);
      }
    }
  };

  beforeAll(() => {
    work = mkdtempSync(join(tmpdir(), 'xyf-ts-mods-'));
    gen = makeModSet(work);
    ({ profile = '', generation = '' } = infoValues(readFileSync(join(gen, 'base', 'manifest.json')), 'manifest.json'));
    stored = join(sim!.root, 'xy-forever', 'mods', generation);
  });
  afterAll(async () => {
    await Promise.all(clients.map((c) => c.close()));
    rmSync(work, { recursive: true, force: true });
  });

  test('push with verify: byte-identical on the device, progress per package and in bytes', async () => {
    const files = packageFiles(), names = readdirSync(gen).sort();
    const bytes = [...files.values()].reduce((n, b) => n + b.length, 0);
    const seen: PackageResult[] = [];
    const progress: [number, number, string][] = [];
    const summary = await push({ verify: true, onPackage: (r) => seen.push(r), onProgress: (d, t, phase) => progress.push([d, t, phase]) });
    expect(summary).toMatchObject({ profile, generation, sent: names.length, skipped: 0, files: files.size, bytes, verified: files.size });
    expect(seen.map((r) => r.name)).toEqual(['base', ...names.filter((n) => n !== 'base')]);
    expect(seen.every((r) => r.state === 'sent' && r.files === r.of)).toBe(true);
    expect(progress.filter(([, , p]) => p === 'send').at(-1)).toEqual([bytes, bytes, 'send']);
    expect(progress.at(-1)).toEqual([bytes, bytes, 'verify']);
    assertStored();
    // the base's files.sha256 is its last write, after manifest.json
    const order = (await localSet(source())).packages.get('base')!.map((f) => f.path);
    expect(order.slice(-2)).toEqual(['manifest.json', 'files.sha256']);
  });

  test('a second push sends nothing; nor does xyf_host.py mods push (the same layout)', async () => {
    const names = readdirSync(gen);
    const again = await push();
    expect(again).toMatchObject({ sent: 0, skipped: names.length, files: 0, bytes: 0 });
    expect(again.packages.every((r) => r.state === 'skipped' && r.files === 0)).toBe(true);
    const run = spawnSync('python3', ['xyf_host.py', '--sim', sim!.address, 'mods', 'push', gen], { cwd: REPO, encoding: 'utf-8', timeout: 60_000 });
    expect(run.status, run.stdout + run.stderr).toBe(0);
    expect(run.stdout).toContain(`${profile}/${generation}: 0 packages sent, ${names.length} skipped; 0 files, 0 B`);
    assertStored();
  });

  test('a cut push never looks complete, and the next push sends exactly the rest', async () => {
    const files = host().size;                                          // store.txt first, then the packages
    for (const after of [0, 1, 5, Math.floor(files / 2), files - 1]) {
      rmSync(stored, { recursive: true, force: true });
      await cutPush(after);
      assertNothingFalseComplete();
      const resent = (await push()).files;
      expect(resent, `cut after ${after}`).toBeGreaterThan(0);
      expect(resent).toBeLessThanOrEqual(files - 1);
      assertStored();
    }
    // a file damaged in a complete package: the list goes first, then the file and the list again
    const victim = join(stored, 'alpha', 'catalog.txt');
    truncateSync(victim, 5);
    await cutPush(0);
    expect(existsSync(join(stored, 'alpha', 'files.sha256'))).toBe(true);
    expect(await push()).toMatchObject({ sent: 1, files: [...packageFiles().keys()].filter((path) => path.startsWith('alpha/')).length });
    assertStored();
  });

  test('only: the named packages and base; an unknown name refused before anything is sent', async () => {
    rmSync(stored, { recursive: true, force: true });
    expect(await push({ only: ['beta'] })).toMatchObject({ sent: 2, skipped: 0 });
    assertStored(['base', 'beta']);
    await expect(localSet(source(), ['nosuch'])).rejects.toThrow(PackageError);
    const client = await connect();
    const call = vi.spyOn(client, 'call');
    await expect(pushPackages(client, source(), { only: ['nosuch'] })).rejects.toThrow('no such package: nosuch');
    expect(call).not.toHaveBeenCalled();
    await client.close();
  });

  test('a mirrored tree (no store.txt) lists as older and a push refuses it; a newer store as newer', async () => {
    rmSync(stored, { recursive: true, force: true });
    cpSync(gen, stored, { recursive: true });
    const client = await connect();
    expect((await listPackages(client)).map((g) => [g.generation, g.older, g.newer, g.packages.length])).toEqual([[generation, true, false, 0]]);
    await expect(pushPackages(client, source())).rejects.toThrow(/older layout/);
    await removePackage(client, generation);
    await pushPackages(client, source());
    assertStored();
    writeFileSync(join(stored, STORE_FILE), 'version=2\n');
    expect((await listPackages(client)).map((g) => [g.older, g.newer])).toEqual([[false, true]]);
    await expect(pushPackages(client, source())).rejects.toThrow(/store version 2, newer/);
    writeFileSync(join(stored, STORE_FILE), STORE_TEXT);
    await client.close();
  });

  test('list (an interrupted package flagged), then remove a package and a generation', async () => {
    await push();
    unlinkSync(join(stored, 'gamma', 'files.sha256'));                 // as a cut push leaves it
    const client = await connect();
    const held = await listPackages(client);
    expect(held.map((g) => [g.profile, g.generation, g.older, g.newer])).toEqual([[profile, generation, false, false]]);
    const files = packageFiles();
    files.delete('gamma/files.sha256');
    for (const name of readdirSync(gen)) {
      const mine = [...files].filter(([p]) => p.split('/')[0] === name).map(([, b]) => b.length);
      const row = held[0]!.packages.find((p) => p.name === name)!;
      expect([row.files, row.bytes, row.complete], name).toEqual([mine.length, mine.reduce((n, s) => n + s, 0), name !== 'gamma']);
    }
    expect(held[0]!.packages.find((p) => p.name === 'base')!.info['type']).toBe('mod-base');

    const dry = await removePackage(client, 'alpha', { dryRun: true });
    expect(dry).toEqual([{ path: `/yaffs2/xy-forever/mods/${generation}/alpha`, files: tree(join(stored, 'alpha')).size,
      bytes: [...tree(join(stored, 'alpha')).values()].reduce((n, b) => n + b.length, 0) }]);
    expect(existsSync(join(stored, 'alpha'))).toBe(true);
    expect(await removePackage(client, 'alpha', { generation: 'other-gen' })).toEqual([]);
    expect(await removePackage(client, 'alpha')).toEqual(dry);
    expect(existsSync(join(stored, 'alpha'))).toBe(false);
    expect(existsSync(join(stored, 'beta'))).toBe(true);
    expect(await removePackage(client, 'nosuch')).toEqual([]);
    expect((await removePackage(client, generation)).map((r) => r.path)).toEqual([`/yaffs2/xy-forever/mods/${generation}`]);
    expect(existsSync(stored)).toBe(false);
    expect((await listPackages(client)).flatMap((g) => g.packages)).toEqual([]);
    await client.close();
  });
});

describe.skipIf(!sim)(`building mod firmware on the device${sim ? '' : ` (skipped: ${sims.why})`}`, () => {
  let work: string, client: Client, request: ApplyRequest;
  const entry = (...parts: string[]): string => join(sim!.root, 'xy-forever', 'firmware', 'modbase', ...parts);

  beforeAll(async () => {
    work = mkdtempSync(join(tmpdir(), 'xyf-ts-build-'));
    const gen = makeModSet(work);
    const { profile = '', generation = '' } = infoValues(readFileSync(join(gen, 'base', 'manifest.json')), 'manifest.json');
    client = await Client.connect(tcpOpener(sim!.address), { timeoutMs: 5000 });
    await pushPackages(client, folderSource(gen));
    makeModBase(sim!.root, 'modbase');
    request = { base: 'modbase', profile, generation, machines: ['alpha', 'gamma'], extensions: ['cap'], name: 'OP-XY mods', unverified: true };
  });
  /** Storage is served again once the last job's processor B closes (A's write window). */
  const served = () => vi.waitFor(async () => expect((await client.listDir('/yaffs2')).length).toBeGreaterThan(0),
    { timeout: 20_000, interval: 100 });
  beforeEach(served);                                                   // a job's write window outlives the job
  afterAll(async () => {
    await control(sim!.control, 'set a_slow_ms 0').catch(() => undefined);
    await client?.close();
    rmSync(work, { recursive: true, force: true });
  });

  test('the library bases; a build reports progress and becomes library entry mods-a, content not installed', async () => {
    const bases = await libraryBases(client);
    expect(bases.map((b) => b.id)).toEqual(['modbase', 'opxy-121', 'opxy-121-stock']);
    expect(bases[0]!.name).toBe('Mod base');
    expect(bases.map((b) => b.contentList)).toEqual([true, false, false]);   // the seed's stock entries have no content list
    const seen: JobStatus[] = [];
    const built = await applyMods(client, request, { pollMs: 20, onProgress: (s) => seen.push(s) });
    const result = built.status;
    expect(built).toMatchObject({ entry: 'mods-a', state: 'content not installed' });
    expect(result).toMatchObject({ phase: 'done', error: 'none', debris: false, final: true, detail: 'mods-a', folder: jobFolder(result.job) });
    expect(result.job).toBeGreaterThan(0);
    expect(result.steps).toBeGreaterThan(0);
    expect(seen.at(-1)).toEqual(result);
    expect(existsSync(join(sim!.root, 'xy-forever', 'tmp', `job-${result.job}`))).toBe(false);   // committed, not left in tmp/
    const out = join(sim!.root, 'xy-forever', 'firmware', built.entry);
    expect(readdirSync(out)).toContain('os.ldr');
    expect(readdirSync(out)).not.toContain('layout.json');
    expect(infoValues(readFileSync(join(out, 'manifest.json')), 'manifest.json')['machines']).toBe('2');
    expect(infoValues(readFileSync(join(out, 'manifest.json')), 'manifest.json')['name']).toBe('OP-XY mods');
    await served();
    expect((await libraryBases(client)).map((b) => b.id)).toEqual(['modbase', 'opxy-121', 'opxy-121-stock']);   // not a base
    expect(await jobStatus(client)).toEqual(result);                      // job 0: the last job
    await expect(cancelJob(client, result.job)).rejects.toMatchObject({ code: 'state' });   // it ended
    const boot = await client.libraryBoot(built.entry, { confirm: true }).catch((e: unknown) => e);
    expect(isXyfError(boot, 'no_content'), String(boot)).toBe(true);    // listed, not bootable as it is
  });

  test('makeDefault on the built entry runs the content switch, then writes the default', async () => {
    const seen: JobStatus[] = [];
    const made = await makeDefault(client, 'mods-a', { pollMs: 20, onProgress: (s) => seen.push(s) });
    expect(made).toMatchObject({ id: 'mods-a', switched: true, status: { phase: 'done', detail: 'mods-a', error: 'none' } });
    expect(seen.at(-1)).toEqual(made.status);                           // followed to its end (a fast switch may show no middle phase)
    await served();
    expect((await client.readState())?.default).toBe('mods-a');
    expect(await makeDefault(client, 'mods-a')).toEqual({ id: 'mods-a', switched: false, status: null });   // installed already
  });

  test('refusals in plain words: missing inputs; a failed job with its error', async () => {
    const refused = async (change: Partial<ApplyRequest>, missing: number, words: string): Promise<void> => {
      const error = await applyMods(client, { ...request, ...change }).catch((e: unknown) => e);
      expect(isXyfError(error, 'not_found'), String(error)).toBe(true);
      expect((error as XyfError).fields?.['missing']).toBe(missing);
      expect((error as XyfError).message).toContain(words);
    };
    renameSync(entry('content.sha256'), join(work, 'content.sha256'));
    try {
      await refused({}, 2, 'the base entry has no content.sha256');
    } finally {
      renameSync(join(work, 'content.sha256'), entry('content.sha256'));
    }
    await refused({ base: 'nosuch' }, 1, 'the base entry has no os.ldr');
    await refused({ generation: '0.0.0-none' }, 4, "the generation's base package metadata is not on the device");
    await expect(applyMods(client, { ...request, machines: [] })).rejects.toThrow(RangeError);
    const failed = await applyMods(client, { ...request, unverified: false }, { pollMs: 20 }).catch((e: unknown) => e);
    expect(failed).toBeInstanceOf(JobError);
    expect((failed as JobError).status).toMatchObject({ phase: 'failed', error: 'base', code: 18 });   // an unknown base, not confirmed
    expect((failed as JobError).message).toMatch(/^the build failed: the base is not a known stock firmware/);
    expect(existsSync(join(sim!.root, 'xy-forever', 'tmp', `job-${(failed as JobError).status.job}`, 'os.ldr'))).toBe(false);
    expect(existsSync(join(sim!.root, 'xy-forever', 'firmware', 'mods-b'))).toBe(false);   // a failed build commits nothing
  });

  test('during a job storage commands and a second build are busy; cancel removes what it wrote', async () => {
    await control(sim!.control, 'set a_slow_ms 4');
    try {
      const stop = new AbortController();
      const seen: JobStatus[] = [];
      const build = applyMods(client, request, { pollMs: 20, signal: stop.signal, onProgress: (s) => seen.push(s) });
      let refused: unknown;
      build.catch((e: unknown) => { refused = e; });
      await vi.waitFor(() => {
        if (refused) throw new Error(`the build ended early: ${String(refused)}`);
        expect(seen.some((s) => !s.final && s.phase !== 'idle')).toBe(true);
      }, { timeout: 10_000, interval: 20 });
      const busy = await client.listDir('/yaffs2').catch((e: unknown) => e);
      expect(isXyfError(busy, 'busy'), String(busy)).toBe(true);
      expect((busy as XyfError).message).toBe('file.list /yaffs2: the OP-XY is building mod firmware (a job is running); try again when it ends, or cancel it');
      await expect(applyMods(client, request)).rejects.toMatchObject({ code: 'busy', message: expect.stringMatching(/^mods\.apply: the OP-XY is busy/) });
      stop.abort();
      const error = await build.catch((e: unknown) => e);
      expect(error).toBeInstanceOf(JobError);
      expect((error as JobError).status).toMatchObject({ phase: 'cancelled', debris: false });
      expect((error as JobError).message).toBe('the build was cancelled');
      expect(existsSync(join(sim!.root, 'xy-forever', 'tmp', `job-${(error as JobError).status.job}`))).toBe(false);
    } finally {
      await control(sim!.control, 'set a_slow_ms 0');
    }
    await served();
  });
});
