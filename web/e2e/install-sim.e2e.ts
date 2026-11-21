// The Install page's end (#178) in the system Chrome (headless) against the device simulator, which runs XY Forever's own
// code and command rules: after Track 5, XY Forever sets itself up on the device (it adopts the stock os.ldr as library
// entry opxy-121, makes it the default, boots it), and the page hands the owner to the Mods page once it finds it.
// te-boot and the dev menu's MTP are the fakes of test/fake-devices.ts; Web Serial is the fake navigator.serial of the
// client's browser tests (clients/ts/test/browser/fake-serial.ts: Chrome's 255-byte default receive buffer, the chooser,
// granted ports, a device that goes away and comes back), reaching the simulator through the relay. Each test starts
// a fresh simulator: a stock OP-XY just after Track 5 (`--fresh`). Skipped with the reason when Chrome or the
// simulator cannot run here.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import type { Browser, Page } from 'playwright';
import { createServer } from 'vite';
import type { ViteDevServer } from 'vite';
import { afterAll, afterEach, beforeAll, expect, it } from 'vitest';
import { startRelay } from '../node_modules/xy-forever-client/test/relay.ts';
import type { Relay } from '../node_modules/xy-forever-client/test/relay.ts';
import { control, findSimulator, startAll } from '../node_modules/xy-forever-client/test/simulators.ts';
import type { SimInfo } from '../node_modules/xy-forever-client/test/simulators.ts';

const IMAGE = Buffer.from(Uint8Array.from({ length: 70_000 }, (_, i) => (i * 31 + 7) & 0xff));
const OK_121 = 'mode:bootloader;product:OP-XY;serial:X1;bl_version:1000.0.20+0;os_version:1.2.1+0';
const FAKE_SERIAL = fileURLToPath(new URL('../node_modules/xy-forever-client/test/browser/fake-serial.ts', import.meta.url));
const ENTRY = 'xy-forever/firmware/opxy-121';
const BASE = parseInt(process.env['XYF_WEB_SIM_PORT'] ?? '7860', 10);

const found = findSimulator();
let why: string | null = found.why;
let browser: Browser | undefined;
if (!why) {
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--disable-blink-features=Serial,WebUSB'] });
  } catch (error) {
    why = `the system Chrome cannot start (${(error as Error).message.split('\n')[0]})`;
  }
}
if (why) console.warn(`\ninstall simulator test skipped: ${why}\n`);

let server: ViteDevServer | undefined;
let image = '';                                                          // the pin's image: the simulator's hello image=
let running: { sim: SimInfo; relay: Relay; stop: () => Promise<void> } | null = null;

beforeAll(async () => {
  if (why) return;
  server = await createServer({ configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)), server: { port: 0, host: '127.0.0.1' }, logLevel: 'error' });
  await server.listen();
});
afterEach(async () => { await running?.stop(); running = null; });
afterAll(async () => { await browser?.close(); await server?.close(); });

/** A fresh simulator: a stock OP-XY just after Track 5 (stock: its updater's os.ldr; plain: no PLAY at power-on). */
async function powerOn(stock: 'stock' | 'missing', plain: boolean): Promise<SimInfo> {
  const state = join(found.sessions!, 'web-install');
  const started = await startAll(state, BASE, [['sim', 0, ['--fresh', stock, ...(plain ? ['--plain'] : [])]]]);
  const sim = started.infos['sim']!;
  const relay = await startRelay({ port: BASE + 10, target: sim.port, control: sim.control });
  running = { sim, relay, stop: async () => { await relay.close(); await started.stopAll(); } };
  return sim;
}

const status = async (sim: SimInfo): Promise<Record<string, unknown>> => control(sim.control, 'status');
async function until(what: string, cond: () => Promise<boolean>, ms = 120_000): Promise<void> {
  const deadline = Date.now() + ms;
  while (!(await cond())) {
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await new Promise((ok) => setTimeout(ok, 100));
  }
}
const inOs = async (sim: SimInfo): Promise<boolean> => { const s = await status(sim); return s['a'] === 'os' && s['b'] === 'os'; };
const listening = async (sim: SimInfo): Promise<boolean> => (await status(sim))['listening'] === 1;

/** The app with fake te-boot, MTP and Web Serial (granted: this site was given the port before; no chooser needed). */
async function open(relay: string, granted: boolean): Promise<Page> {
  const address = server!.httpServer!.address();
  const base = `http://127.0.0.1:${typeof address === 'object' && address ? address.port : 0}`;
  const pin = { schema: 1, version: '0.1.0', image, sha256: createHash('sha256').update(IMAGE).digest('hex'), bytes: IMAGE.length };
  const page = await browser!.newPage({ acceptDownloads: true });
  await page.route('**/src/install/release.json*', (route) => route.fulfill({ contentType: 'text/javascript', body: `export default ${JSON.stringify(pin)};` }));
  await page.route('**/release/os.ldr', (route) => route.fulfill({ contentType: 'application/octet-stream', body: IMAGE }));
  await page.goto(`${base}/connect`);
  await page.addScriptTag({ type: 'module', content: `
    import { FakeMidi, FakeUsb } from '/test/fake-devices.ts';
    import { FakeSerial } from '/@fs${FAKE_SERIAL}';
    const fields = Object.fromEntries(${JSON.stringify(OK_121)}.split(';').map((kv) => kv.split(':')));
    const midi = new FakeMidi(fields), usb = new FakeUsb({ 'upgrade/firmware/blackfin/bin/os.ldr': new Uint8Array(16) }, 64 * 1024 * 1024);
    const serial = new FakeSerial(${JSON.stringify(relay)}, { identity: { usbVendorId: 0x2367, usbProductId: 0x0021 }, granted: ${granted} });
    Object.defineProperty(navigator, 'requestMIDIAccess', { value: midi.requestMIDIAccess, configurable: true });
    Object.defineProperty(navigator, 'usb', { value: usb, configurable: true });
    serial.install();
    window.fakes = { midi, usb, serial };
    document.body.dataset.fakes = 'ready';` });
  await page.waitForSelector('body[data-fakes=ready]');
  await page.locator('#nav a[data-page=install]').click();
  return page;
}

const stage = (page: Page): Promise<string | null> => page.locator('#page-install').getAttribute('data-stage');
const press = (page: Page, name: string): Promise<void> => page.getByRole('button', { name, exact: true }).click();

/** te-boot to Track 5's "firmware written ok!": the last stage, "Switch your OP–XY off, then on." */
async function toStart(page: Page, computer: 'Mac' | 'Windows'): Promise<void> {
  await page.getByRole('button', { name: /Check my OP/ }).click();
  await expect.poll(() => stage(page), { timeout: 15_000 }).toBe('menu');
  for (const name of ['Yes, I see it', computer, 'Dev menu is open', 'I see "mtp mode active"']) await press(page, name);
  if (computer === 'Mac') {
    await press(page, 'They’re closed · Continue');
    await press(page, 'Send to OP–XY');
    await expect.poll(() => page.getByRole('button', { name: 'File sent · Continue' }).isVisible(), { timeout: 30_000 }).toBe(true);
    await press(page, 'File sent · Continue');
  } else {
    await press(page, 'File copied · Continue');
  }
  for (const name of ['I’m back at the dev menu', 'I pressed track 5', 'I see "firmware written ok!"']) await press(page, name);
  expect(await stage(page)).toBe('start');
  expect(await page.locator('#page-install').textContent()).toContain('Switch your OP–XY off, then on.');
}

/** The page handed the owner to the app: the Mods page, connected, the installed notice once. */
async function handedOff(page: Page): Promise<void> {
  await expect.poll(() => new URL(page.url()).pathname, { timeout: 60_000 }).toBe('/mods');
  expect(await page.locator('#installed').isVisible()).toBe(true);
  expect(await page.locator('#installed').textContent()).toContain('XY Forever is installed');
  expect(await page.locator('#link-text').textContent()).toMatch(/^Connected/);
  expect(await page.locator('#page-mods').isVisible()).toBe(true);
  await page.locator('#installed-dismiss').click();
  expect(await page.locator('#installed').isVisible()).toBe(false);
}

/** The entry XY Forever made on the device: the stock os.ldr, byte for byte, beside its info.txt and files.sha256. */
function adopted(sim: SimInfo): void {
  const stock = readFileSync(found.fixtures['stock_os_ldr']!);
  expect(readFileSync(join(sim.root, ENTRY, 'os.ldr')).equals(stock)).toBe(true);
  expect(readFileSync(join(sim.root, ENTRY, 'info.txt'), 'utf8')).toMatch(/^type=firmware\nname=OP-XY\nversion=1\.2\.1\nkind=official\n/);
  expect(readFileSync(join(sim.root, 'xy-forever/state-a.txt'), 'utf8')).toContain('default=opxy-121\n');
}

/** The simulator's hello image= (the page's pin names it, as the release would name the image Track 5 wrote). */
async function simImage(): Promise<string> {
  const sim = await powerOn('missing', false);                           // PLAY, refuses at once: nothing written that matters
  const { Client, tcpOpener } = await import('../node_modules/xy-forever-client/src/node.ts');
  const client = await Client.connect(tcpOpener(sim.address), { timeoutMs: 30_000 });
  const id = client.info.image ?? 'unknown';
  await client.close(); await running!.stop(); running = null;
  return id;
}

it.skipIf(why !== null)('Mac: the first plain power-on sets up OS 1.2.1 on the device and starts it; PLAY then opens the app on Mods', async () => {
  image = await simImage();
  const sim = await powerOn('stock', true);                             // before the page looks: the stock pair is not on USB yet
  const page = await open(running!.relay.url, false);
  await toStart(page, 'Mac');
  expect(await page.evaluate(() => (window as unknown as { fakes: { usb: { reads: string[] } } }).fakes.usb.reads)).toEqual(['os.ldr']);
  await until('the OS, after the setup', () => inOs(sim));
  adopted(sim);
  await control(sim.control, 'power play');                              // the owner holds PLAY: the XY Forever menu
  await until('the menu', () => listening(sim));
  await press(page, 'Find my OP–XY');                                    // never granted on this computer: the chooser, from the click
  await handedOff(page);
  expect(await page.evaluate(() => (window as unknown as { fakes: { serial: { chooser: number; xyfPorts: { dropped: number }[] } } }).fakes.serial))
    .toMatchObject({ chooser: 1 });
  expect(await page.evaluate(() => (window as unknown as { fakes: { serial: { xyfPorts: { dropped: number }[] } } }).fakes.serial.xyfPorts.every((p) => p.dropped === 0))).toBe(true);
  await page.close();
}, 240_000);

it.skipIf(why !== null)('Windows: copied by hand; the page finds XY Forever by itself after the setup (a port granted before)', async () => {
  image ||= await simImage();
  const sim = await powerOn('stock', true);
  const page = await open(running!.relay.url, true);
  await toStart(page, 'Windows');
  await until('the OS, after the setup', () => inOs(sim));
  adopted(sim);
  expect(await stage(page)).toBe('start');                               // the stock OS has no serial port: nothing to find
  await control(sim.control, 'power play');
  await handedOff(page);                                                 // no click: the last stage looks every few seconds
  await page.close();
}, 240_000);

it.skipIf(why !== null)('at any stage: XY Forever found while it sets up shows the setup, then hands off', async () => {
  image ||= await simImage();
  const sim = await powerOn('stock', false);                            // PLAY at the first start: the menu stays after the setup
  await control(sim.control, 'set a_slow_ms 2');                         // slow enough to watch
  const page = await open(running!.relay.url, true);
  expect(await stage(page)).toBe('boot');
  await expect.poll(async () => (await page.locator('#page-install [role=status]').textContent().catch(() => '')) ?? '', { timeout: 60_000 })
    .toMatch(/adding OS 1\.2\.1|about to add OS 1\.2\.1/);
  expect(await stage(page)).toBe('start');
  await handedOff(page);
  adopted(sim);
  await page.close();
}, 240_000);

it.skipIf(why !== null)('the device refuses a missing stock os.ldr: the page shows its words and the official 1.2.1 download', async () => {
  image ||= await simImage();
  await powerOn('missing', true);                                       // a plain first start: the menu stays, with USB
  const page = await open(running!.relay.url, true);
  await expect.poll(() => stage(page), { timeout: 60_000 }).toBe('stop');
  const card = page.locator('[data-verdict]');
  expect(await card.getAttribute('data-verdict')).toBe('reinstall');
  expect(await card.textContent()).toContain('No official OS 1.2.1 found. Reinstall official OS 1.2.1, then start again.');
  expect(await page.getByRole('link', { name: /Download official OS 1\.2\.1/ }).isVisible()).toBe(true);
  expect(new URL(page.url()).pathname).toBe('/install/stop');
  await page.close();
}, 240_000);
