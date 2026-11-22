// Install from the web app (#178): every refusal and guidance branch, the release pin, te-boot over a fake Web MIDI,
// the Mac copy over a fake WebUSB MTP device, and reading XY Forever's own setup over a fake client. The page never
// reads the stock os.ldr: XY Forever adopts it on the device (e2e/install-sim.e2e.ts runs that against the simulator).
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { RELEASE_FILTERS, STATUS, XyfError, connectWebSerial } from 'xy-forever-client';
import type { Client } from 'xy-forever-client';
import { HEADROOM, OFFICIAL_121_TFW, STOCK_121, STOCK_KB, STOCK_MISSING, STOCK_PATH, STOCK_WRONG, bootVerdict, needBytes, sha256Hex, spaceVerdict } from '../src/install/checks.ts';
import type { Verdict } from '../src/install/checks.ts';
import { ADOPT_DETAIL, ENTRY, installState, otherImage } from '../src/install/finish.ts';
import { InstallStop, TeBoot, copyOverMtp } from '../src/install/installer.ts';
import { fetchRelease, pinned } from '../src/install/release.ts';
import type { ReleasePin } from '../src/install/release.ts';
import { INSTALL_STAGES } from '../src/install/stages.ts';
import { staticPaths } from '../src/routing.ts';

const PAGES = staticPaths().filter((path) => !path.includes('/'));
import { FakeMidi, FakeUsb } from './fake-devices.ts';

const T = 'mode:bootloader;product:OP-XY;serial:X1';
const id = (bl: string, os?: string): Record<string, string> =>
  Object.fromEntries(`${T};bl_version:${bl}${os === undefined ? '' : `;os_version:${os}`}`.split(';').map((kv) => kv.split(':') as [string, string]));
const kinds = (v: Verdict): string => v.kind;

describe('te-boot identity', () => {
  it('accepts the 1.2.1 bootloader with OS 1.2.1', () => {
    expect(kinds(bootVerdict(id('1000.0.20+0', '1.2.1+0@483bb625')))).toBe('ok');
    expect(kinds(bootVerdict(id('1000.0.20', '1.2.1')))).toBe('ok');
  });
  it('refuses the 1.2.5 bootloader in plain words', () => {
    const v = bootVerdict(id('1000.0.23+0', '1.2.5+0'));
    expect(v.kind).toBe('refuse');
    expect(v.title).toContain('1.2.5 bootloader');
    expect(v.detail).toMatch(/cannot be taken back/);
  });
  it('refuses a newer or unknown bootloader', () => {
    expect(kinds(bootVerdict(id('1000.0.21+0', '1.2.1')))).toBe('refuse');
    expect(kinds(bootVerdict(id('2.0.0', '1.2.1')))).toBe('refuse');
    expect(bootVerdict({ product: 'OP-XY' }).title).toBe('Unknown bootloader');
  });
  it('guides an older OP-XY to official 1.2.1, never to 1.2.5', () => {
    for (const v of [bootVerdict(id('1000.0.20+0', '1.1.33+0')), bootVerdict(id('1000.0.15+0', '1.0.9'))]) {
      expect(v.kind).toBe('update');
      expect(v.detail).toContain('1.2.1');
      expect(v.detail).toMatch(/not the newest update.*1\.2\.5 cannot be undone/);
    }
    expect(OFFICIAL_121_TFW).toBe('https://teenage.engineering/_software/op-xy/opxy_firmware_1_2_1.tfw');
  });
  it('refuses unknown firmware on the 1.2.1 bootloader', () => {
    expect(bootVerdict(id('1000.0.20+0', '1.3.0')).title).toBe('Unknown firmware');
    expect(bootVerdict(id('1000.0.20+0')).title).toBe('Unknown firmware');
    expect(kinds(bootVerdict({ ...id('1000.0.20+0'), sw_version: '1.2.1+0' }))).toBe('ok');   // os_version absent: sw_version
  });
});

describe('the stock os.ldr and free storage', () => {
  it('asks for an official 1.2.1 reinstall when the stock file is missing', () => {
    expect(STOCK_MISSING.kind).toBe('reinstall');
    expect(STOCK_MISSING.detail).toMatch(/factory reset/);
    expect(STOCK_MISSING.detail).toMatch(/Reinstall teenage engineering's OS 1\.2\.1/);
  });
  it('refuses low storage with both numbers', () => {
    expect(needBytes(300_000)).toBe(300_000 + STOCK_121.bytes + HEADROOM);
    const v = spaceVerdict(1_000_000, needBytes(300_000));
    expect(v.kind).toBe('space');
    expect(v.detail).toMatch(/1\.0 MB free.*needs 3\.5 MB/);
    expect(kinds(spaceVerdict(needBytes(300_000), needBytes(300_000)))).toBe('ok');
  });
  it('pins the same stock hash as stock.json', () => {
    const stock = JSON.parse(readFileSync(new URL('../../stock.json', import.meta.url), 'utf8'));
    expect(STOCK_121.sha256).toBe(stock.stock['1.2.1']['os.ldr']);
  });
});

const IMAGE = Uint8Array.from({ length: 70_000 }, (_, i) => (i * 31 + 7) & 0xff);
const pinOf = async (data: Uint8Array): Promise<ReleasePin> => ({ schema: 1, version: '0.1.0', image: (await sha256Hex(data)).slice(0, 8), sha256: await sha256Hex(data), bytes: data.length });

describe('the release pin', () => {
  it('serves only the pinned bytes', async () => {
    const pin = await pinOf(IMAGE);
    const get = (body: Uint8Array, status = 200) => async () => new Response(body as BlobPart, { status });
    expect(await fetchRelease(pin, get(IMAGE))).toEqual(IMAGE);
    const other = IMAGE.slice(); other[5]! ^= 1;
    await expect(fetchRelease(pin, get(other))).rejects.toThrow(/not the pinned one\. Nothing was copied/);
    await expect(fetchRelease(pin, get(IMAGE.slice(1)))).rejects.toThrow(/not the pinned one/);
    await expect(fetchRelease(pin, get(IMAGE, 404))).rejects.toThrow(/HTTP 404/);
  });
  it('has no release without a valid pin, and refuses a newer pin format', () => {
    expect(pinned({ schema: 1, version: '', image: '', sha256: '', bytes: 0 })).toBeNull();
    expect(() => pinned({ schema: 2, version: '', image: '', sha256: '', bytes: 0 })).toThrow(/newer/);
  });
  it('the committed pin is well formed', () => {
    const pin = JSON.parse(readFileSync(new URL('../src/install/release.json', import.meta.url), 'utf8')) as ReleasePin;
    expect(pin.schema).toBe(1);
    if (pin.sha256) expect(pin).toMatchObject({ image: pin.sha256.slice(0, 8), version: expect.stringMatching(/^\d+\.\d+\.\d+$/) });
  });
});

describe('te-boot over Web MIDI', () => {
  it('reads the identity and switches the dev menu on (never off)', async () => {
    const midi = new FakeMidi(id('1000.0.20+0', '1.2.1+0'));
    const te = new TeBoot(midi.requestMIDIAccess);
    const { verdict, identity } = await te.check();
    expect(verdict.kind).toBe('ok');
    expect(identity['serial']).toBe('X1');
    expect(midi.sent.map((f) => f.type)).toEqual([0x09]);
    await te.enableDevMenu();
    expect(midi.sent.map((f) => f.type)).toEqual([0x09, 0x09, 0x1f, 0xac]);
    const command = midi.sent.at(-1)!.payload;
    expect([...command.slice(-2)]).toEqual([0xba, 0xa7]);            // the enable selector; 0x00 would switch it off
    te.close();
  });
  it('accepts exactly what a stock 1.2.1 OP-XY\'s te-boot reported (#178 device finding)', async () => {
    const reported = { mode: 'bootloader', os_version: '1.2.1', sw_version: '1.2.1', bl_version: '1000.0.20' };
    expect(bootVerdict(reported).kind).toBe('ok');
    const midi = new FakeMidi({ product: 'OP-XY', serial: 'X1', ...reported });   // Open-XY's ferret also checks these two
    const te = new TeBoot(midi.requestMIDIAccess);
    expect((await te.check()).verdict.kind).toBe('ok');
    te.close();
  });
  it('sends nothing but the identity request to a refused device', async () => {
    for (const fields of [id('1000.0.23+0', '1.2.5'), id('1000.0.20+0', '1.1.33'), id('1000.0.20+0', '9.9.9')]) {
      const midi = new FakeMidi(fields);
      const te = new TeBoot(midi.requestMIDIAccess);
      expect((await te.check()).verdict.kind).not.toBe('ok');
      await expect(te.enableDevMenu()).rejects.toBeInstanceOf(InstallStop);
      expect(midi.sent.every((f) => f.type === 0x09)).toBe(true);
    }
  });
  it('the README names only real routes', () => {
    const readme = readFileSync(new URL('../README.md', import.meta.url), 'utf8');
    const stages = [...readme.matchAll(/`\/install\/([a-z-]+)`/g)].map((m) => m[1]!);
    expect(stages.length).toBeGreaterThan(0);
    for (const stage of stages) expect(INSTALL_STAGES).toContain(stage);
    for (const match of readme.matchAll(/`\/([a-z]+)`/g)) expect(PAGES).toContain(match[1]);
  });
  it('tells the PLAY-at-power-on step once', () => {
    const source = readFileSync(new URL('../src/install/content.ts', import.meta.url), 'utf8');
    expect(source.match(/hold PLAY/g)).toHaveLength(1);
  });
  it('ships no way to switch the dev menu off', () => {
    const source = readFileSync(new URL('../src/vendor/open-xy/ferret.mjs', import.meta.url), 'utf8');
    expect(source).not.toMatch(/function disablePayload|async disable\(|setDevMenu\(false\)/);
    expect(readFileSync(new URL('../src/install/installer.ts', import.meta.url), 'utf8')).not.toMatch(/\.disable\(|disablePayload/);
  });
});

const STOCK = new Uint8Array(STOCK_121.bytes).fill(9);   // the stock size, not its bytes
const ROOMY = 64 * 1024 * 1024;

describe('the Mac copy over WebUSB MTP', () => {
  it('checks that the stock file is there, copies os.ldr to the top level and reads it back; never reads the stock file', async () => {
    const usb = new FakeUsb({ [STOCK_PATH]: STOCK, 'os.ldr': new Uint8Array(10) }, ROOMY);
    const steps: string[] = [];
    await copyOverMtp(usb, IMAGE, (text) => steps.push(text));
    expect(usb.find('os.ldr')!.data).toEqual(IMAGE);
    expect(usb.find(STOCK_PATH)!.data).toEqual(STOCK);
    expect(usb.reads).toEqual(['os.ldr']);                              // our copy, read back; the stock os.ldr never
    expect(steps).toContain('Checking the copy...');
  });
  it('stops on a missing stock file before anything is copied', async () => {
    const usb = new FakeUsb({ 'content/op1_factory.json': new Uint8Array(3) }, ROOMY);
    const stop = await copyOverMtp(usb, IMAGE).catch((e: unknown) => e);
    expect((stop as InstallStop).verdict.kind).toBe('reinstall');
    expect(usb.find('os.ldr')).toBeUndefined();
  });
  it('stops on a stock file of another size before anything is copied', async () => {
    const usb = new FakeUsb({ [STOCK_PATH]: STOCK.subarray(1) }, ROOMY);
    const stop = await copyOverMtp(usb, IMAGE).catch((e: unknown) => e);
    expect((stop as InstallStop).verdict).toBe(STOCK_WRONG);
    expect(STOCK_WRONG.detail).toContain('(2,318 KB)');
    expect(STOCK_KB).toBe('2,318 KB');
    expect(usb.find('os.ldr')).toBeUndefined();
    expect(usb.reads).toEqual([]);
  });
  it('stops on low storage', async () => {
    const usb = new FakeUsb({ [STOCK_PATH]: STOCK }, 2 * 1024 * 1024);
    const stop = await copyOverMtp(usb, IMAGE).catch((e: unknown) => e);
    expect((stop as InstallStop).verdict.kind).toBe('space');
    expect(usb.find('os.ldr')).toBeUndefined();
  });
});

/**
 * A fake XY Forever over Web Serial, as its setup leaves it: job.status (the adoption job: target/usb_service_adopt.ipp),
 * library entry opxy-121's info.txt, the default; while the job runs, file reads answer busy (the device's job_blocks).
 */
class FakeClient {
  job: { phase: number; error: number; code?: number; done: number; total: number; detail: string } | null = null;
  info_txt: string | null = null;
  /** Other library entries (id -> info.txt), as mods-a after a build. */
  others: Record<string, string> = {};
  defaultEntry: string | null = null;
  info = { image: 'abcd1234' };
  connected = true;
  log: string[] = [];
  async call(name: string): Promise<Record<string, unknown>> {
    this.log.push(name);
    if (name !== 'job.status') throw new Error(`unexpected ${name}`);
    if (!this.job) throw new XyfError('not_found', 'job.status: not found', { command: 'job.status', status: 3 });
    const detail = new Uint8Array(32); detail.set(new TextEncoder().encode(this.job.detail));
    return { job: 1, phase: this.job.phase, done: this.job.done, total: this.job.total, error: this.job.error, code: this.job.code ?? 0, elapsed_ms: 0,
      steps: 0, longest_ms: 0, peak: 0, debris: 0, detail };
  }
  private busy(): void {
    if (this.job && this.job.phase < 8) throw new XyfError('busy', 'file.read: busy', { command: 'file.read', status: 15, fields: { busy: 'job' } });
  }
  async libraryEntry(entry: string): Promise<Record<string, string>> {
    this.log.push(`entry ${entry}`); this.busy();
    const text = entry === ENTRY ? this.info_txt : this.others[entry] ?? null;
    if (text === null) throw new XyfError('not_found', 'no such file', { command: 'file.read', status: 3 });
    return Object.fromEntries(text.trim().split('\n').map((l) => l.split('=') as [string, string]));
  }
  async readState(): Promise<{ default: string | null }> { this.busy(); return { default: this.defaultEntry }; }
}

const INFO = `type=firmware\nname=OP-XY\nversion=1.2.1\nkind=official\nbytes=${STOCK_121.bytes}\ncrc32=${STOCK_121.crc32}\nsha256=${STOCK_121.sha256}\n`;

describe('XY Forever\'s own setup, read over Web Serial', () => {
  const state = async (c: FakeClient): Promise<unknown> => installState(c as unknown as Client, { ...(await pinOf(IMAGE)), image: 'abcd1234' }).catch((e: unknown) => e);

  it('installed once the stock entry is in the library and is the default, whatever image runs', async () => {
    const c = new FakeClient(); c.info_txt = INFO; c.defaultEntry = ENTRY; c.job = { phase: 8, error: 0, done: 0, total: 0, detail: 'config' };
    expect(await state(c)).toEqual({ kind: 'installed' });
    c.info = { image: 'ffffffff' }; c.job = null;
    expect(await state(c)).toEqual({ kind: 'installed' });
    expect(c.log.every((l) => l === 'job.status' || l.startsWith('entry'))).toBe(true);   // reads only
  });
  it('installed when the owner runs mods: the stock entry there, the default another library entry (never a reflash)', async () => {
    const c = new FakeClient(); c.info_txt = INFO; c.defaultEntry = 'mods-a'; c.others = { 'mods-a': 'type=firmware\nname=Open-XY\n' };
    expect(await state(c)).toEqual({ kind: 'installed' });
    c.defaultEntry = 'mods-b';                                            // a default naming no entry: not set up
    expect(await state(c)).toEqual({ kind: 'waiting', other: false });
  });
  it('adopting while the job runs (its progress), and while reads answer busy', async () => {
    const c = new FakeClient(); c.job = { phase: 5, error: 0, done: 1_000_000, total: STOCK_121.bytes, detail: ADOPT_DETAIL };
    expect(await state(c)).toEqual({ kind: 'adopting', done: 1_000_000, total: STOCK_121.bytes });
    c.job = { phase: 1, error: 0, done: 0, total: 0, detail: 'something else' };    // another job holds the storage
    expect(await state(c)).toEqual({ kind: 'adopting', done: 0, total: 0 });
  });
  it('refused with the device\'s own words: missing, not 1.2.1, an incomplete entry, any other failure', async () => {
    const refused = async (error: number, code = 0): Promise<Verdict> => {
      const c = new FakeClient(); c.job = { phase: 9, error, code, done: 0, total: 0, detail: ADOPT_DETAIL };
      const s = await state(c) as { kind: string; verdict: Verdict };
      expect(s.kind).toBe('refused');
      return s.verdict;
    };
    expect(await refused(4)).toMatchObject({ kind: 'reinstall', detail: expect.stringContaining('"No official OS 1.2.1 found. Reinstall official OS 1.2.1, then start again."') });
    expect(await refused(10)).toMatchObject({ kind: 'reinstall', detail: expect.stringContaining('"OS file is not official 1.2.1. Reinstall official OS 1.2.1, then start again."') });
    expect(await refused(17, STATUS.state)).toMatchObject({ kind: 'refuse', detail: expect.stringContaining('library entry opxy-121 is incomplete: remove it.') });
    expect((await refused(17, STATUS.storage)).detail).not.toMatch(/remove/i);   // a failed rename: never "remove the entry"
    const unread = await refused(8, STATUS.storage);                      // the library could not be read: no removal advice
    expect(unread).toMatchObject({ kind: 'refuse', title: 'The library could not be read', detail: expect.stringContaining('"Failed: the library could not be read."') });
    expect(unread.detail).not.toMatch(/remove/i);
    expect(await refused(16)).toMatchObject({ kind: 'refuse', title: 'OS 1.2.1 could not be added to the library' });
    expect((await refused(4)).detail).toContain('Use exactly this 1.2.1 file');
  });
  it('waiting while the setup has not started, saying whether another image runs (an older one never starts it)', async () => {
    const c = new FakeClient();
    expect(await state(c)).toEqual({ kind: 'waiting', other: false });
    c.info_txt = INFO;                                                    // the entry, not yet the default
    expect(await state(c)).toEqual({ kind: 'waiting', other: false });
    c.info = { image: 'ffffffff' };
    expect(await state(c)).toEqual({ kind: 'waiting', other: true });
    expect(otherImage('ffffffff', await pinOf(IMAGE)).title).toBe('Another XY Forever is running');
  });
  it('an opxy-121 entry holding other firmware stops', async () => {
    const c = new FakeClient(); c.info_txt = INFO.replace(STOCK_121.sha256, 'ab'.repeat(32));
    expect(((await state(c)) as InstallStop).verdict.title).toBe('Library entry opxy-121 is not the stock OS 1.2.1');
  });
});

describe('looking for XY Forever', () => {
  it('never opens the chooser when no granted port answers', async () => {
    let asked = 0;
    const serial = { getPorts: async () => [], requestPort: async () => { asked++; throw new Error('chooser'); } };
    const error = await connectWebSerial({ serial, filters: RELEASE_FILTERS, choose: false }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(XyfError);
    expect(asked).toBe(0);
  });
  it('never opens a second link: a port this page holds ends the connect, the other entry stays shut', async () => {
    const opened: string[] = [];
    const port = (name: string, fails: boolean) => ({ getInfo: () => ({ ...RELEASE_FILTERS[0] }), close: async () => {}, readable: null, writable: null,
      open: async () => { opened.push(name); if (fails) throw new DOMException('The port is already open.', 'InvalidStateError'); } });
    const held = port('held', true), twin = port('twin', false);       // Chrome listed two entries for one OP-XY
    const serial = { getPorts: async () => [held, twin], requestPort: async () => twin };
    const error = await connectWebSerial({ serial, filters: RELEASE_FILTERS, timeoutMs: 1000 }).catch((e: unknown) => e);
    expect(error).toMatchObject({ code: 'aborted', message: expect.stringContaining('already connected') });
    expect(opened).toEqual(['held']);
  });
});
