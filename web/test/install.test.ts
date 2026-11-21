// Install from the web app (#178): every refusal and guidance branch, the release pin, te-boot over a fake Web MIDI,
// the Mac copy over a fake WebUSB MTP device, and the Web Serial finish over a fake client. No stock bytes: tests
// that need the stock file to pass give their own judge.
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { RELEASE_FILTERS, XyfError, connectWebSerial } from 'xy-forever-client';
import type { Client, DirEntry } from 'xy-forever-client';
import { HEADROOM, OFFICIAL_121_TFW, STOCK_121, STOCK_PATH, bootVerdict, needBytes, sha256Hex, spaceVerdict, stockVerdict } from '../src/install/checks.ts';
import type { Verdict } from '../src/install/checks.ts';
import { ENTRY, INFO_TEXT, STOCK_ON_DEVICE, entryFiles, finishInstall, installState } from '../src/install/finish.ts';
import { InstallStop, TeBoot, checkDroppedStock, copyOverMtp } from '../src/install/installer.ts';
import { fetchRelease, pinned } from '../src/install/release.ts';
import type { ReleasePin } from '../src/install/release.ts';
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
  it('judges the stock file by its SHA-256', () => {
    expect(kinds(stockVerdict(STOCK_121.sha256))).toBe('ok');
    expect(kinds(stockVerdict('0a46d43dbe9258f9a0f79d62e2a709a7b0a3798eea340548d58729630398cc99'))).toBe('update');   // SHA-256 of 1.1.33's
    expect(kinds(stockVerdict('bed98a0011d2c5cb4f7fbd02fe9c6518ed56886586f975f6fce53c1d2c9d4283'))).toBe('refuse');
    expect(stockVerdict('ab'.repeat(32)).title).toBe('Unknown firmware');
  });
  it('asks for an official 1.2.1 reinstall when the stock file is missing', () => {
    const v = stockVerdict(null);
    expect(v.kind).toBe('reinstall');
    expect(v.detail).toMatch(/factory reset/);
    expect(v.detail).toMatch(/Reinstall teenage engineering's OS 1\.2\.1/);
  });
  it('refuses low storage with both numbers', () => {
    expect(needBytes(300_000)).toBe(300_000 + STOCK_121.bytes + HEADROOM);
    const v = spaceVerdict(1_000_000, needBytes(300_000));
    expect(v.kind).toBe('space');
    expect(v.detail).toMatch(/1\.0 MB free.*needs 3\.5 MB/);
    expect(kinds(spaceVerdict(needBytes(300_000), needBytes(300_000)))).toBe('ok');
  });
  it('hashes a dropped file locally', async () => {
    expect(kinds(await checkDroppedStock(new Blob([new Uint8Array([1, 2, 3])])))).toBe('refuse');
  });
  it('pins the same stock hashes as stock.json', () => {
    const stock = JSON.parse(readFileSync(new URL('../../stock.json', import.meta.url), 'utf8'));
    expect(STOCK_121.sha256).toBe(stock.stock['1.2.1']['os.ldr']);
    expect(kinds(stockVerdict(stock.stock['1.2.5']['os.ldr']))).toBe('refuse');
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
  it('ships no way to switch the dev menu off', () => {
    const source = readFileSync(new URL('../src/vendor/open-xy/ferret.mjs', import.meta.url), 'utf8');
    expect(source).not.toMatch(/function disablePayload|async disable\(|setDevMenu\(false\)/);
    expect(readFileSync(new URL('../src/install/installer.ts', import.meta.url), 'utf8')).not.toMatch(/\.disable\(|disablePayload/);
  });
});

const STOCK = new Uint8Array(STOCK_121.bytes).fill(9);   // the stock size, not its bytes
const passStock = async (): Promise<(sha: string | null) => Verdict> => {
  const good = await sha256Hex(STOCK);
  return (sha) => sha === good ? stockVerdict(STOCK_121.sha256) : stockVerdict(sha);
};
const ROOMY = 64 * 1024 * 1024;

describe('the Mac copy over WebUSB MTP', () => {
  it('checks, copies os.ldr to the top level and reads it back', async () => {
    const usb = new FakeUsb({ [STOCK_PATH]: STOCK, 'os.ldr': new Uint8Array(10) }, ROOMY);
    const steps: string[] = [];
    await copyOverMtp(usb, IMAGE, (text) => steps.push(text), await passStock());
    expect(usb.find('os.ldr')!.data).toEqual(IMAGE);
    expect(usb.find(STOCK_PATH)!.data).toEqual(STOCK);
    expect(steps).toContain('Checking the copy...');
  });
  it('stops on a missing stock file before anything is copied', async () => {
    const usb = new FakeUsb({ 'content/op1_factory.json': new Uint8Array(3) }, ROOMY);
    const stop = await copyOverMtp(usb, IMAGE).catch((e: unknown) => e);
    expect((stop as InstallStop).verdict.kind).toBe('reinstall');
    expect(usb.find('os.ldr')).toBeUndefined();
  });
  it('stops on low storage', async () => {
    const usb = new FakeUsb({ [STOCK_PATH]: STOCK }, 2 * 1024 * 1024);
    const stop = await copyOverMtp(usb, IMAGE, () => {}, await passStock()).catch((e: unknown) => e);
    expect((stop as InstallStop).verdict.kind).toBe('space');
    expect(usb.find('os.ldr')).toBeUndefined();
  });
  it('stops on unknown firmware', async () => {
    const usb = new FakeUsb({ [STOCK_PATH]: STOCK }, ROOMY);
    const stop = await copyOverMtp(usb, IMAGE).catch((e: unknown) => e);
    expect((stop as InstallStop).verdict.title).toBe('Unknown firmware');
    expect(usb.find('os.ldr')).toBeUndefined();
  });
});

/**
 * A fake XY Forever over Web Serial: a file system, the calls finishInstall makes, and what it was asked. As the
 * device's job_blocks: while a package session is open, file.copy (not among the commands it permits) is `busy`.
 */
class FakeClient {
  session = false;
  files = new Map<string, Uint8Array>();
  folders = new Set(['/yaffs2', '/yaffs2/upgrade', '/yaffs2/upgrade/firmware', '/yaffs2/upgrade/firmware/blackfin', '/yaffs2/upgrade/firmware/blackfin/bin']);
  free = ROOMY;
  log: string[] = [];
  defaultEntry: string | null = null;
  info = { image: 'abcd1234' };
  connected = true;
  private notFound = (path: string): XyfError => new XyfError('not_found', `${path}: no such file`, { command: 'file.list', status: 3 });
  async call(name: string, request: Record<string, unknown>): Promise<Record<string, number>> {
    this.log.push(name === 'package.session' ? `${name} ${request['action']}` : name);
    if (name === 'package.session' && request['action'] !== 1) this.session = request['action'] === 0;
    if (name === 'storage.space') return { version: 1, free_low: this.free % 2 ** 32, free_high: Math.floor(this.free / 2 ** 32) };
    return {};
  }
  async listDir(path: string): Promise<DirEntry[]> {
    if (!this.folders.has(path)) throw this.notFound(path);
    const kids = (set: Iterable<string>) => [...set].filter((p) => p.slice(0, p.lastIndexOf('/')) === path);
    return [...kids(this.folders).map((p) => ({ name: p.split('/').at(-1)!, kind: 'folder' as const, size: 0 })),
      ...kids(this.files.keys()).map((p) => ({ name: p.split('/').at(-1)!, kind: 'file' as const, size: this.files.get(p)!.length }))]
      .map((e) => ({ ...e, nameBytes: new TextEncoder().encode(e.name) }));
  }
  async readFile(path: string): Promise<Uint8Array> { const d = this.files.get(path); if (!d) throw this.notFound(path); return d; }
  async writeFile(path: string, data: Uint8Array): Promise<void> { this.log.push(`write ${path.split('/').at(-1)}`); this.files.set(path, data.slice()); }
  async copyFile(source: string, target: string): Promise<{ bytes: number }> {
    this.log.push(`copy ${source} -> ${target.split('/').at(-1)!.replace(/[0-9a-f]{16}$/, '*')}`);
    if (this.session) throw new XyfError('busy', 'file.copy: the device answered busy', { command: 'file.copy', status: 15 });
    const data = this.files.get(source); if (!data) throw this.notFound(source);
    this.files.set(target, data.slice()); return { bytes: this.files.get(target)!.length };
  }
  async makeDir(path: string): Promise<void> { this.folders.add(path); }
  async deleteFile(path: string): Promise<void> { this.files.delete(path); }
  async removeDir(path: string): Promise<void> { this.folders.delete(path); }
  async renamePath(source: string, target: string): Promise<void> {
    this.log.push(`rename ${source.split('/').at(-1)} -> ${target}`);
    const file = this.files.get(source);
    if (file) { this.files.delete(source); this.files.set(target, file); return; }
    for (const p of [...this.folders]) if (p === source || p.startsWith(source + '/')) { this.folders.delete(p); this.folders.add(target + p.slice(source.length)); }
    for (const [p, d] of [...this.files]) if (p.startsWith(source + '/')) { this.files.delete(p); this.files.set(target + p.slice(source.length), d); }
  }
  async libraryEntry(entry: string): Promise<Record<string, string>> {
    const text = new TextDecoder().decode(await this.readFile(`/yaffs2/xy-forever/firmware/${entry}/info.txt`));
    return Object.fromEntries(text.trim().split('\n').map((l) => l.split('=') as [string, string]));
  }
  async setDefault(entry: string): Promise<void> { this.log.push(`default ${entry}`); this.defaultEntry = entry; }
  async reboot(to: string): Promise<void> { this.log.push(`reboot ${to}`); }
  async reconnect(): Promise<{ image: string }> { this.log.push('reconnect'); return this.info; }
  async readState(): Promise<{ default: string | null }> { return { default: this.defaultEntry }; }
  async continueBoot(): Promise<void> { this.log.push('continue'); }
}

describe('the Web Serial finish', () => {
  const setup = (): FakeClient => { const c = new FakeClient(); c.files.set(STOCK_ON_DEVICE, STOCK); return c; };
  const finish = async (c: FakeClient, stockChecked = false): Promise<unknown> =>
    finishInstall(c as unknown as Client, { release: { ...(await pinOf(IMAGE)), image: 'abcd1234' }, stockChecked, judge: await passStock() }).catch((e: unknown) => e);

  it('copies the stock entry on the device, makes it the default, restarts and starts it', async () => {
    const c = setup();
    expect(await finish(c)).toBeUndefined();
    const entry = '/yaffs2/xy-forever/firmware/opxy-121';
    expect(c.files.get(`${entry}/os.ldr`)).toEqual(STOCK);
    expect(new TextDecoder().decode(c.files.get(`${entry}/info.txt`))).toBe(INFO_TEXT);
    expect(new TextDecoder().decode(c.files.get(`${entry}/files.sha256`))).toBe(new TextDecoder().decode((await entryFiles()).get('files.sha256')));
    expect(c.log.filter((l) => l.startsWith('copy'))).toEqual([`copy ${STOCK_ON_DEVICE} -> copy-v1-*`]);
    expect(c.log.findIndex((l) => l.startsWith('copy'))).toBeLessThan(c.log.indexOf('package.session 0'));   // outside the session
    expect(c.log.some((l) => l === 'write os.ldr')).toBe(false);          // the stock bytes are never sent over USB
    expect(c.log.slice(c.log.indexOf(`default ${ENTRY}`))).toEqual([`default ${ENTRY}`, 'reboot menu', 'reconnect', 'continue']);
    expect([...c.files.keys()].filter((p) => p.includes('/tmp/'))).toEqual([]);
  });
  it('writes the info.txt library-add writes', () => {
    expect(INFO_TEXT).toBe('type=firmware\nname=OP-XY\nversion=1.2.1\nkind=official\nbytes=2373336\ncrc32=89d08ddd\n' +
      'sha256=60358afdaa9b58ed40411602f6d5d4e1e3e02553ff2da7e25038e71dd002b52f\n');
  });
  it('skips the serial hash when the Mac copy already hashed the file', async () => {
    const c = setup();
    let reads = 0;
    const read = c.readFile.bind(c);
    c.readFile = async (p) => { if (p === STOCK_ON_DEVICE) reads++; return read(p); };
    await finish(c, true); expect(reads).toBe(0);
    const again = setup();
    let fresh = 0;
    const readAgain = again.readFile.bind(again);
    again.readFile = async (p) => { if (p === STOCK_ON_DEVICE) fresh++; return readAgain(p); };
    await finish(again, false); expect(fresh).toBe(1);
  });
  it('refuses another XY Forever image', async () => {
    const c = setup(); c.info = { image: 'ffffffff' };
    expect(((await finish(c)) as InstallStop).verdict.title).toBe('Another XY Forever is running');
    expect(c.log).toEqual([]);
  });
  it('refuses low storage, a missing and an unknown stock file, changing nothing', async () => {
    const low = setup(); low.free = 1000;
    expect(((await finish(low)) as InstallStop).verdict.kind).toBe('space');
    const missing = new FakeClient();
    expect(((await finish(missing)) as InstallStop).verdict.kind).toBe('reinstall');
    const other = setup(); other.files.set(STOCK_ON_DEVICE, new Uint8Array(STOCK_121.bytes).fill(1));
    expect(((await finish(other)) as InstallStop).verdict.title).toBe('Unknown firmware');
    for (const c of [low, missing, other]) expect(c.log.filter((l) => !/^(storage\.space)$/.test(l))).toEqual([]);
  });
  it('finishes an interrupted install without adding the entry again', async () => {
    const c = setup();
    await finish(c);
    c.log = [];
    expect(await finish(c)).toBeUndefined();
    expect(c.log).toEqual([`default ${ENTRY}`, 'reboot menu', 'reconnect', 'continue']);
  });
});

describe('detecting XY Forever at any stage', () => {
  const setup = (): FakeClient => { const c = new FakeClient(); c.files.set(STOCK_ON_DEVICE, STOCK); return c; };
  it('jumps to the finish until the stock entry is the default, then shows done, changing nothing', async () => {
    const c = setup();
    expect(await installState(c as unknown as Client)).toBe('finish');               // no entry, no default
    await finishInstall(c as unknown as Client, { release: { ...(await pinOf(IMAGE)), image: 'abcd1234' }, stockChecked: false, judge: await passStock() });
    c.defaultEntry = null; c.log = [];
    expect(await installState(c as unknown as Client)).toBe('finish');               // the entry, no default
    c.defaultEntry = ENTRY;
    expect(await installState(c as unknown as Client)).toBe('done');
    expect(c.log).toEqual([]);
  });
  it('never opens the chooser when no granted port answers', async () => {
    let asked = 0;
    const serial = { getPorts: async () => [], requestPort: async () => { asked++; throw new Error('chooser'); } };
    const error = await connectWebSerial({ serial, filters: RELEASE_FILTERS, choose: false }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(XyfError);
    expect(asked).toBe(0);
  });
});
