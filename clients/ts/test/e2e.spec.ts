// End to end against the simulator (test/simulators.ts): the public call layer over TCP, as an app
// uses it. Files are seeded and checked through the simulator's /yaffs2 folder on the Mac; faults
// come from its control port. Runs in order (one client at a time).
import { randomBytes } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { afterAll, beforeAll, describe, expect, inject, test } from 'vitest';
import { Client, VERSION, decodeScreen, isXyfError } from '../src/node.ts';
import { tcpOpener } from '../src/node.ts';
import { control } from './simulators.ts';

const sims = inject('sims');
const sim = sims.e2e;

function seed(path: string, data: Uint8Array): void {
  const host = join(sim!.root, path.slice('/yaffs2/'.length));
  mkdirSync(dirname(host), { recursive: true });
  writeFileSync(host, data);
}
function onMac(path: string): Uint8Array {
  return new Uint8Array(readFileSync(join(sim!.root, path.slice('/yaffs2/'.length))));
}
async function timed<T>(work: Promise<T>): Promise<[T | unknown, number]> {
  const start = Date.now();
  const result = await work.catch((e: unknown) => e);
  return [result, Date.now() - start];
}

describe.skipIf(!sim)(`end to end against the simulator${sim ? '' : ` (skipped: ${sims.why})`}`, () => {
  let client: Client;
  beforeAll(async () => { client = await Client.connect(tcpOpener(sim!.address), { timeoutMs: 5000 }); });
  afterAll(async () => { await client?.close(); });

  test('connect and hello', async () => {
    expect(client.info).toMatchObject({ proto: VERSION, mode: 'product', limits: { read: 1024, payload: 4160, stage: 7340032 } });
    expect(client.info.build).toBeGreaterThan(153);
    expect((await client.status()).proto).toBe(VERSION);
  });

  test('read files and compare with what was written', async () => {
    expect(new TextDecoder().decode(await client.readFile('/yaffs2/firmware.version'))).toBe('1.2.1\n');
    const data = new Uint8Array(randomBytes(5000));
    seed('/yaffs2/xy-forever/log/web/read.bin', data);
    const progress: number[] = [];
    expect(await client.readFile('/yaffs2/xy-forever/log/web/read.bin', { onProgress: (done) => progress.push(done) })).toEqual(data);
    expect(progress).toEqual([1024, 2048, 3072, 4096, 5000]);
    expect(await client.readRange('/yaffs2/xy-forever/log/web/read.bin', 4990, 1024)).toEqual(data.subarray(4990));
    await expect(client.readFile('/yaffs2/xy-forever/log/web/none.bin')).rejects.toMatchObject({ code: 'not_found' });
  });

  test('staged write, then read back', async () => {
    const path = '/yaffs2/xy-forever/log/web/written.bin';
    const data = new Uint8Array(randomBytes(3000));
    const staged: number[] = [];
    expect(await client.writeFile(path, data, { onProgress: (done) => staged.push(done) })).toMatchObject({ written: 3000 });
    expect(staged).toEqual([1024, 2048, 3000]);
    expect(await client.readFile(path)).toEqual(data);
    expect(onMac(path)).toEqual(data);
    await expect(client.writeFile('/yaffs2/upgrade/elsewhere.bin', data)).rejects.toMatchObject({ code: 'refused', command: 'file.write_staged' });
  });

  test('a screenshot decoded to pixels', async () => {
    let calls = 0;
    const raw = await client.screenshot({ onProgress: () => calls++ });
    expect(calls).toBe(240);
    const { width, height, rgba } = decodeScreen(raw);
    const pixel = (x: number, y: number): number[] => [...rgba.subarray((y * width + x) * 4, (y * width + x) * 4 + 4)];
    expect([width, height]).toEqual([480, 216]);
    // The simulator's test pattern: 40-pixel squares of 0x2104 and black, stored right to left.
    expect(pixel(0, 0)).toEqual([33, 32, 33, 255]);
    expect(pixel(40, 0)).toEqual([0, 0, 0, 255]);
    expect(pixel(0, 40)).toEqual([0, 0, 0, 255]);
    expect(pixel(40, 40)).toEqual([33, 32, 33, 255]);
  });

  test('library and state queries, set the default', async () => {
    expect(await client.readState()).toMatchObject({ default: 'opxy-121' });
    expect(await client.libraryEntry('opxy-121')).toMatchObject({ name: 'OP-XY 1.2.1', version: '1.2.1', kind: 'official' });
    await client.setDefault('opxy-121-stock');
    expect(await client.readState()).toMatchObject({ default: 'opxy-121-stock' });
    await client.setDefault('opxy-121');
    expect(await client.readState()).toMatchObject({ default: 'opxy-121' });
    expect(await client.tfwCheck('/yaffs2/firmware.version')).toMatchObject({ pass: false, reason: 2 });
    await client.hold(true);
    await client.hold(false);
  });

  test('reboot and reconnect', async () => {
    await client.reboot('menu');
    expect(client.connected).toBe(false);
    await expect(client.readFile('/yaffs2/firmware.version')).rejects.toMatchObject({ code: 'disconnected' });
    const info = await client.reconnect({ timeoutMs: 15_000 });
    expect(info.proto).toBe(VERSION);
    expect(new TextDecoder().decode(await client.readFile('/yaffs2/firmware.version'))).toBe('1.2.1\n');
  });

  test('cable pulled mid-read: disconnected at once, then reconnect', async () => {
    seed('/yaffs2/xy-forever/log/web/pull.bin', new Uint8Array(randomBytes(8000)));
    await client.readRange('/yaffs2/xy-forever/log/web/pull.bin', 0, 16);
    await control(sim!.control, 'set cable_pull 10');           // 10 bytes of the next reply, then the cable goes
    const [error, ms] = await timed(client.readFile('/yaffs2/xy-forever/log/web/pull.bin'));
    expect(isXyfError(error, 'disconnected')).toBe(true);
    expect(ms).toBeLessThan(2000);
    await client.reconnect({ timeoutMs: 15_000 });
    expect((await client.readFile('/yaffs2/xy-forever/log/web/pull.bin')).length).toBe(8000);
  });

  test('processor A silent: a short timeout ends as timeout; the device then answers link', async () => {
    const path = '/yaffs2/firmware.version';
    await client.readFile(path);
    await control(sim!.control, 'set a_silent 1');
    try {
      const [timeout, ms] = await timed(client.readRange(path, 0, 64, { timeoutMs: 1000 }));
      expect(isXyfError(timeout, 'timeout')).toBe(true);
      expect(ms).toBeLessThan(1500);
      // The device gives up on A after 5 s of its own clock: move it on instead of waiting, again
      // until the request ends (a loaded machine may start B's wait after the first move). Its late
      // answer to the timed-out request is dropped (old seq).
      let ended = false;
      const pending = timed(client.readRange(path, 0, 64)).finally(() => { ended = true; });
      const nudge = (async () => {
        while (!ended) {
          await new Promise((r) => setTimeout(r, 300));
          if (!ended) await control(sim!.control, 'advance 6000');
        }
      })();
      const [link, ms2] = await pending;
      await nudge;
      expect(isXyfError(link, 'link')).toBe(true);
      expect(ms2).toBeLessThan(15_000);                             // file.read's timeout
    } finally {
      await control(sim!.control, 'set a_silent 0');
      await control(sim!.control, 'advance 6000');
    }
    await control(sim!.control, 'power');                       // a clean link again: power cycle (PLAY held)
    await client.reconnect({ timeoutMs: 15_000, goneMs: 5000 });
    expect(new TextDecoder().decode(await client.readFile(path))).toBe('1.2.1\n');
  });
});
