// The file methods against the simulator (test/simulators.ts, its own `files` simulator): list with
// paging, read and write (1 MiB both ways), delete, folders, rename, copy and copyTree, refusals with
// their plain reasons (outside the write area: here, before anything is sent), and library.boot last
// (it leaves the menu). Seeded and checked through the simulator's /yaffs2 folder
// on the Mac. Runs in order (one client at a time).
import { randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { afterAll, beforeAll, describe, expect, inject, test, vi } from 'vitest';
import { Client, XyfError, crc32, isXyfError, sha256 } from '../src/node.ts';
import { simOpener } from './simulators.ts';
import { countSim } from './cpu-setup.ts';

const sims = inject('sims');
const sim = countSim(sims.files);
const DIR = '/yaffs2/content/files-spec';

const host = (path: string): string => join(sim!.root, path.slice('/yaffs2/'.length));
function seed(path: string, data: Uint8Array | string): void {
  mkdirSync(dirname(host(path)), { recursive: true });
  writeFileSync(host(path), data);
}
async function failure(work: Promise<unknown>): Promise<Error> {
  const result = await work.then(
    () => null,
    (e: unknown) => e,
  );
  if (!(result instanceof Error)) throw new Error('expected a failure');
  return result;
}

describe.skipIf(!sim)(`file methods against the simulator${sim ? '' : ` (skipped: ${sims.why})`}`, () => {
  let client: Client;
  beforeAll(async () => {
    client = await Client.connect(simOpener(sim!), { timeoutMs: 5000 });
  });
  afterAll(async () => {
    await client?.close();
  });

  test('list /yaffs2: kinds, sizes and names as on the Mac', async () => {
    const entries = await client.listDir('/yaffs2');
    const version = entries.find((e) => e.name === 'firmware.version');
    expect(version).toMatchObject({ kind: 'file', size: statSync(host('/yaffs2/firmware.version')).size });
    expect(entries.find((e) => e.name === 'xy-forever')).toMatchObject({ kind: 'folder', size: 0 });
    expect(new TextDecoder().decode(version!.nameBytes)).toBe('firmware.version');
  });

  test('a folder of more than 64 entries, paged to the end', async () => {
    const names = Array.from({ length: 150 }, (_, i) => `e${i}-${'x'.repeat(i % 40)}`);
    for (const name of names) seed(`${DIR}/many/${name}`, name);
    seed(`${DIR}/many/sub/inner.txt`, 'inner');
    const call = vi.spyOn(client, 'call');
    try {
      const entries = await client.listDir(`${DIR}/many/`);
      expect(
        entries
          .filter((e) => e.kind === 'file')
          .map((e) => e.name)
          .sort(),
      ).toEqual([...names].sort());
      expect(entries.filter((e) => e.kind === 'folder').map((e) => e.name)).toEqual(['sub']);
      const pages = call.mock.calls.filter(([name]) => name === 'file.list');
      expect(pages.length).toBeGreaterThanOrEqual(3); // 64 entries or 1016 bytes a page
      expect(pages.map(([, request]) => (request as { skip: number }).skip)[1]).toBeGreaterThan(0);
      for (const e of entries.filter((x) => x.kind === 'file')) expect(e.size).toBe(e.name.length);
    } finally {
      call.mockRestore();
    }
  });

  test('a 1 MiB write and read, byte for byte, with progress', async () => {
    const path = `${DIR}/big.bin`;
    const data = new Uint8Array(randomBytes(1 << 20));
    const staged: [number, number | null][] = [];
    expect(
      await client.writeFile(path, data, { onProgress: (done, total) => staged.push([done, total]) }),
    ).toMatchObject({ written: 1 << 20 });
    expect(staged.length).toBe(256); // MAX_DATA pieces (hello pipe)
    expect(staged.at(-1)).toEqual([1 << 20, 1 << 20]);
    expect(new Uint8Array(readFileSync(host(path)))).toEqual(data);
    let read = 0;
    const back = await client.readFile(path, {
      onProgress: (done) => {
        read = done;
      },
    });
    expect(back.length).toBe(data.length);
    expect(Buffer.compare(back, data)).toBe(0);
    expect(read).toBe(1 << 20);
    expect((await client.listDir(DIR)).find((e) => e.name === 'big.bin')).toMatchObject({
      kind: 'file',
      size: 1 << 20,
    });
  });

  test('a second write in the same session replaces the file', async () => {
    const path = `${DIR}/again.txt`;
    await client.writeFile(path, new TextEncoder().encode('first'));
    await client.writeFile(path, new TextEncoder().encode('second'));
    expect(readFileSync(host(path), 'utf-8')).toBe('second');
  });

  test('above the hello stage limit: streamed (1.18, file.write_*) and renamed onto its name', async () => {
    const data = new Uint8Array(client.info.limits.stage + 1).map((_, i) => i % 251);
    expect(await client.writeFile(`${DIR}/huge.bin`, data)).toEqual({ written: data.length, readbackCrc: crc32(data) });
    const tail = await client.readRange(`${DIR}/huge.bin`, data.length - 16, 16);
    expect(tail).toEqual(data.subarray(data.length - 16));
    await client.deleteFile(`${DIR}/huge.bin`);
  }, 120_000);

  test('changes outside the write area: refused here with the reason, nothing sent (nothing staged)', async () => {
    // Since 1.13 all of /yaffs2 is writable by hand except bootloader.dev and anything in upgrade/ (inWriteArea).
    const roots =
      'the OP-XY changes files only under /yaffs2/, never /yaffs2/bootloader.dev or /yaffs2/upgrade (or inside it), ' +
      'at most 6 folders deep';
    const call = vi.spyOn(client, 'call');
    try {
      const write = await failure(client.writeFile('/yaffs2/bootloader.dev', new Uint8Array(16)));
      expect(write).toMatchObject({ code: 'refused', command: 'file.write_staged', status: null });
      expect(write.message).toBe(`file.write_staged /yaffs2/bootloader.dev: ${roots}`);
      await expect(client.writeFile('/yaffs2/BootLoader.DEV', new Uint8Array(16))).rejects.toMatchObject({
        code: 'refused',
      });
      await expect(client.writeFile('/yaffs2/UPGRADE/x.bin', new Uint8Array(16))).rejects.toMatchObject({
        code: 'refused',
      });
      await expect(client.writeFile('/outside.bin', new Uint8Array(16))).rejects.toMatchObject({ code: 'refused' });
      await expect(client.writeFile(`${DIR}/../x.bin`, new Uint8Array(16))).rejects.toMatchObject({ code: 'refused' });
      await expect(client.deleteFile('/yaffs2/bootloader.dev')).rejects.toMatchObject({
        code: 'refused',
        command: 'file.delete',
        status: null,
      });
      await expect(client.makeDir('/yaffs2/upgrade')).rejects.toMatchObject({ code: 'refused', command: 'file.mkdir' });
      await expect(client.makeDir('/yaffs2/content/')).rejects.toMatchObject({
        code: 'refused',
        command: 'file.mkdir',
      });
      await expect(client.removeDir('/yaffs2/upgrade/old')).rejects.toMatchObject({
        code: 'refused',
        command: 'file.rmdir',
      });
      const rename = await failure(client.renamePath('/yaffs2/bootloader.dev', `${DIR}/v.txt`));
      expect(rename.message).toBe(`file.rename /yaffs2/bootloader.dev -> ${DIR}/v.txt: ${roots}`);
      await expect(client.renamePath(`${DIR}/v.txt`, '/yaffs2/upgrade/v.txt')).rejects.toMatchObject({
        code: 'refused',
        command: 'file.rename',
      });
      const copy = await failure(client.copyFile(`${DIR}/v.txt`, '/yaffs2/upgrade/v.txt'));
      expect(copy).toMatchObject({ code: 'refused', command: 'file.copy', status: null });
      expect(copy.message).toBe(
        `file.copy ${DIR}/v.txt -> /yaffs2/upgrade/v.txt: the OP-XY copies only a file it may read, to a path under ` +
          '/yaffs2/, never /yaffs2/bootloader.dev or /yaffs2/upgrade (or inside it)',
      );
      expect(call).not.toHaveBeenCalled();
    } finally {
      call.mockRestore();
    }
    expect(existsSync(host('/yaffs2/bootloader.dev'))).toBe(false);
    expect(existsSync(host('/yaffs2/upgrade'))).toBe(false);
    // The device stays the authority: sent anyway, it answers refused with the same reason.
    const path = '/yaffs2/upgrade/x.bin';
    const device = await failure(
      client.call('file.delete', { path, sha256: await sha256(new TextEncoder().encode(path)) }),
    );
    expect(device).toMatchObject({ code: 'refused', command: 'file.delete', status: 14 });
    await expect(client.listDir('/etc')).rejects.toMatchObject({ code: 'refused', command: 'file.list' });
    await expect(client.copyFile('/etc/passwd', `${DIR}/passwd`, { size: 0 })).rejects.toMatchObject({
      code: 'refused',
      status: 14,
      message: expect.stringMatching(/^file\.copy \/etc\/passwd -> .*: the OP-XY copies only a file it may read/),
    });
  });

  test('a missing file or folder: not_found', async () => {
    const read = await failure(client.readFile(`${DIR}/none.bin`));
    expect(read).toMatchObject({ code: 'not_found', command: 'file.read_stream' });
    expect(read.message).toBe(`file.read_stream ${DIR}/none.bin: no such file`);
    await expect(client.deleteFile(`${DIR}/none.bin`)).rejects.toMatchObject({
      code: 'not_found',
      command: 'file.delete',
    });
    await expect(client.listDir(`${DIR}/none`)).rejects.toMatchObject({ code: 'not_found', command: 'file.list' });
    await expect(client.makeDir(`${DIR}/none/child`)).rejects.toMatchObject({
      code: 'not_found',
      message: expect.stringMatching(/parent folder/),
    });
  });

  test('make a folder, fill it, remove it: not while it holds a file', async () => {
    const dir = `${DIR}/made`;
    await client.makeDir(dir);
    expect(statSync(host(dir)).isDirectory()).toBe(true);
    const twice = await failure(client.makeDir(dir));
    expect(twice).toMatchObject({ code: 'storage', fields: { error: 17 } });
    expect(twice.message).toMatch(/exists already$/);
    await client.writeFile(`${dir}/inside.txt`, new TextEncoder().encode('inside'));
    const full = await failure(client.removeDir(dir));
    expect(full).toMatchObject({ code: 'state', command: 'file.rmdir' });
    expect(full.message).toMatch(/not empty/);
    expect(existsSync(host(`${dir}/inside.txt`))).toBe(true);
    await expect(client.deleteFile(dir)).rejects.toMatchObject({ code: 'storage', command: 'file.delete' });
    await client.deleteFile(`${dir}/inside.txt`);
    expect(existsSync(host(`${dir}/inside.txt`))).toBe(false);
    await client.removeDir(dir);
    expect(existsSync(host(dir))).toBe(false);
    expect((await client.listDir(DIR)).map((e) => e.name)).not.toContain('made');
  });

  test('rename a file, then a folder with what it holds; onto an existing name: state, nothing replaced', async () => {
    const dir = `${DIR}/mv`;
    seed(`${dir}/a.txt`, 'a');
    seed(`${dir}/taken.txt`, 'taken');
    seed(`${dir}/tree/inner/deep.txt`, 'deep');
    await client.renamePath(`${dir}/a.txt`, `${dir}/b.txt`);
    expect(existsSync(host(`${dir}/a.txt`))).toBe(false);
    expect(readFileSync(host(`${dir}/b.txt`), 'utf-8')).toBe('a');
    await client.makeDir(`${dir}/moved`);
    await client.renamePath(`${dir}/tree`, `${dir}/moved/tree2`);
    expect(existsSync(host(`${dir}/tree`))).toBe(false);
    expect(readFileSync(host(`${dir}/moved/tree2/inner/deep.txt`), 'utf-8')).toBe('deep');
    const onto = await failure(client.renamePath(`${dir}/b.txt`, `${dir}/taken.txt`));
    expect(onto).toMatchObject({ code: 'state', command: 'file.rename', status: 3 });
    expect(onto.message).toBe(
      `file.rename ${dir}/b.txt -> ${dir}/taken.txt: the target exists already (nothing replaced)`,
    );
    expect(readFileSync(host(`${dir}/taken.txt`), 'utf-8')).toBe('taken');
    expect(readFileSync(host(`${dir}/b.txt`), 'utf-8')).toBe('a');
    await expect(client.renamePath(`${dir}/none.txt`, `${dir}/c.txt`)).rejects.toMatchObject({
      code: 'not_found',
      command: 'file.rename',
    });
    await client.makeDir(`${dir}/solo`); // no folder inside: within the depth rule
    await expect(client.renamePath(`${dir}/solo`, `${dir}/solo/inside`)).rejects.toMatchObject({
      code: 'storage',
      command: 'file.rename',
      message: expect.stringMatching(/a folder cannot move into itself$/),
    });
  });

  test('copy a file byte for byte; onto an existing name: state; a missing source: not_found', async () => {
    const dir = `${DIR}/cp`;
    const data = new Uint8Array(randomBytes(300_000));
    seed(`${dir}/src.bin`, data);
    seed(`${dir}/taken.bin`, 'taken');
    expect(await client.copyFile(`${dir}/src.bin`, `${dir}/copy.bin`)).toEqual({ bytes: data.length });
    expect(Buffer.compare(readFileSync(host(`${dir}/copy.bin`)), data)).toBe(0);
    expect(Buffer.compare(readFileSync(host(`${dir}/src.bin`)), data)).toBe(0);
    const onto = await failure(client.copyFile(`${dir}/src.bin`, `${dir}/taken.bin`));
    expect(onto).toMatchObject({ code: 'state', command: 'file.copy' });
    expect(onto.message).toBe(
      `file.copy ${dir}/src.bin -> ${dir}/taken.bin: the target exists already (nothing replaced)`,
    );
    expect(readFileSync(host(`${dir}/taken.bin`), 'utf-8')).toBe('taken');
    const missing = await failure(client.copyFile(`${dir}/none.bin`, `${dir}/none-copy.bin`));
    expect(missing).toMatchObject({ code: 'not_found', command: 'file.copy' });
    expect(missing.message).toBe(
      `file.copy ${dir}/none.bin -> ${dir}/none-copy.bin: no such file, or the target's folder does not exist`,
    );
    expect(existsSync(host(`${dir}/none-copy.bin`))).toBe(false);
  });

  test('#178: busy while the write window closes is sent again; busy for a job is not', async () => {
    const busy = (): XyfError =>
      new XyfError('busy', 'file.list: the device answered busy', { command: 'file.list', status: 15 });
    const original = client.call.bind(client);
    let refusals = 2,
      phase = 9;
    const call = vi.spyOn(client, 'call').mockImplementation(((name: string, request: unknown, options: unknown) => {
      if (name === 'file.list' && refusals > 0) {
        refusals--;
        return Promise.reject(busy());
      }
      if (name === 'job.status') return Promise.resolve({ phase });
      return original(name as never, request as never, options as never);
    }) as typeof client.call);
    try {
      expect((await client.listDir('/yaffs2')).length).toBeGreaterThan(0);
      expect(refusals).toBe(0);
      refusals = 1;
      phase = 5; // a mod build in its write phase
      const job = await failure(client.listDir('/yaffs2'));
      expect(job).toMatchObject({ code: 'busy', fields: { busy: 'job' } });
      expect(job.message).toBe(
        'file.list /yaffs2: the OP-XY is running a job (building mod firmware or changing content); try again when it ends, or cancel it',
      );
    } finally {
      call.mockRestore();
    }
  });

  test('copyTree: a folder and everything in it; a target inside the source: RangeError, nothing sent', async () => {
    const dir = `${DIR}/tree`;
    const big = new Uint8Array(randomBytes(70_000));
    seed(`${dir}/src/one.txt`, 'one');
    seed(`${dir}/src/sub/big.bin`, big);
    mkdirSync(host(`${dir}/src/empty`), { recursive: true });
    const progress: number[] = [];
    expect(await client.copyTree(`${dir}/src/`, `${dir}/dst`, { onProgress: (done) => progress.push(done) })).toEqual({
      files: 2,
      bytes: 3 + big.length,
    });
    expect(progress.at(-1)).toBe(3 + big.length);
    expect(readFileSync(host(`${dir}/dst/one.txt`), 'utf-8')).toBe('one');
    expect(Buffer.compare(readFileSync(host(`${dir}/dst/sub/big.bin`)), big)).toBe(0);
    expect(readdirSync(host(`${dir}/dst/empty`))).toEqual([]);
    expect(await client.copyTree(`${dir}/src/one.txt`, `${dir}/one-copy.txt`)).toEqual({ files: 1, bytes: 3 });
    const call = vi.spyOn(client, 'call');
    try {
      await expect(client.copyTree(`${dir}/src`, `${dir}/src/sub/again`)).rejects.toThrow(RangeError);
      expect(call).not.toHaveBeenCalled();
    } finally {
      call.mockRestore();
    }
    await expect(client.copyTree(`${dir}/none`, `${dir}/none-copy`)).rejects.toMatchObject({ code: 'not_found' });
    await expect(client.copyTree(`${dir}/src`, `${dir}/dst`)).rejects.toMatchObject({
      code: 'storage',
      command: 'file.mkdir',
    });
  });

  test('the depth rule (#124): at most 6 folders below /yaffs2; refused here at 7, a folder moved deeper by the device', async () => {
    const W = '/yaffs2/xy-forever/dt',
      six = `${W}/a/b/c/d`; // 2 + 4 = 6 folders
    seed(`${six}/f/old.txt`, 'old'); // 7: made before the rule
    seed(`${W}/t/u/v/x.txt`, 'x');
    const call = vi.spyOn(client, 'call');
    try {
      const deep = await failure(client.writeFile(`${six}/f/w.txt`, new Uint8Array(3)));
      expect(deep).toMatchObject({ code: 'refused', command: 'file.write_staged', status: null });
      expect(deep.message).toContain('is more than 6 folders below /yaffs2');
      await expect(client.makeDir(`${six}/g`)).rejects.toMatchObject({ code: 'refused', status: null });
      await expect(client.copyFile(`${W}/t/u/v/x.txt`, `${six}/f/x.txt`)).rejects.toMatchObject({
        code: 'refused',
        status: null,
      });
      await expect(client.copyTree(`${W}/t/u`, `${W}/a/b/c/u`)).rejects.toMatchObject({
        code: 'refused',
        status: null,
      });
      expect(call.mock.calls.filter(([name]) => name !== 'file.list')).toEqual([]);
    } finally {
      call.mockRestore();
    }
    await client.writeFile(`${six}/w.txt`, new TextEncoder().encode('abc')); // at the limit
    await client.makeDir(`${W}/a/b/c/n`);
    await client.copyTree(`${W}/t/u`, `${W}/a/b/u2`);
    expect(readFileSync(host(`${W}/a/b/u2/v/x.txt`), 'utf8')).toBe('x');
    await expect(client.renamePath(`${W}/t/u`, `${W}/a/b/c/u`)).rejects.toMatchObject({
      code: 'refused',
      command: 'file.rename',
      status: 14,
    });
    await client.renamePath(`${W}/t/u`, `${W}/a/b/u`);
    expect(new TextDecoder().decode(await client.readFile(`${six}/f/old.txt`))).toBe('old'); // older: read and delete
    await client.deleteFile(`${six}/f/old.txt`);
    await client.removeDir(`${six}/f`);
  });

  test('library.boot: only with confirm; a missing entry boots nothing; then the link drops', async () => {
    const call = vi.spyOn(client, 'call');
    // @ts-expect-error confirm is required
    await expect(client.libraryBoot('opxy-121', {})).rejects.toThrow(TypeError);
    expect(call).not.toHaveBeenCalled();
    call.mockRestore();
    await expect(client.libraryBoot('no-such-entry', { confirm: true })).rejects.toMatchObject({
      code: 'not_found',
      command: 'library.boot',
    });
    expect(client.connected).toBe(true);
    await client.libraryBoot('opxy-121', { confirm: true });
    expect(client.connected).toBe(false);
    const after = await failure(client.listDir('/yaffs2'));
    expect(isXyfError(after, 'disconnected')).toBe(true);
  });
});
