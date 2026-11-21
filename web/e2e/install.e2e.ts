// The Install page (#178) in the system Chrome (headless): every refusal and guidance branch, the dev-menu steps and
// the copy, against fake te-boot MIDI and a fake dev-menu MTP device (test/fake-devices.ts, installed over
// navigator.requestMIDIAccess and navigator.usb before the page uses them). No simulator and no real device: Web Serial
// and WebUSB are switched off in Chrome. The release pin and image are test ones, served in place of release.json and
// release/os.ldr. The Mac copy runs to the end only with the private stock 1.2.1 os.ldr (the check needs its SHA-256);
// it is served to the fake device from this machine, never stored. Skipped with the reason when Chrome cannot start.
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import type { Browser, Page } from 'playwright';
import { createServer } from 'vite';
import type { ViteDevServer } from 'vite';
import { afterAll, beforeAll, expect, it } from 'vitest';

const IMAGE = Buffer.from(Uint8Array.from({ length: 70_000 }, (_, i) => (i * 31 + 7) & 0xff));
const SHA = createHash('sha256').update(IMAGE).digest('hex');
const PIN = { schema: 1, version: '0.1.0', image: SHA.slice(0, 8), sha256: SHA, bytes: IMAGE.length };
const STOCK_FILE = join(process.env['XYF_PRIVATE'] ?? fileURLToPath(new URL('../../../private', import.meta.url)), 'firmware/stock-1.2.1/os.ldr');
const STOCK = existsSync(STOCK_FILE) ? readFileSync(STOCK_FILE) : null;
const OK_121 = 'mode:bootloader;product:OP-XY;serial:X1;bl_version:1000.0.20+0;os_version:1.2.1+0';

let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let why: string | null = null;
try {
  browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--disable-blink-features=Serial,WebUSB'] });
} catch (error) {
  why = `the system Chrome cannot start (${(error as Error).message.split('\n')[0]})`;
  console.warn(`\ninstall browser test skipped: ${why}\n`);
}

beforeAll(async () => {
  server = await createServer({ configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)), server: { port: 0, host: '127.0.0.1' }, logLevel: 'error' });
  await server.listen();
});

afterAll(async () => {
  await browser?.close();
  await server?.close();
});

/** The app with fake devices: te-boot answering with fields, an MTP device holding files (path -> test URL or bytes). */
async function open(fields: string, files: Record<string, string> = {}, free = 64 * 1024 * 1024): Promise<Page> {
  const address = server!.httpServer!.address();
  const base = `http://127.0.0.1:${typeof address === 'object' && address ? address.port : 0}/`;
  const page = await browser!.newPage({ acceptDownloads: true });
  await page.route('**/src/install/release.json*', (route) => route.fulfill({ contentType: 'text/javascript', body: `export default ${JSON.stringify(PIN)};` }));
  await page.route('**/release/os.ldr', (route) => route.fulfill({ contentType: 'application/octet-stream', body: IMAGE }));
  if (STOCK) await page.route('**/__test/stock.ldr', (route) => route.fulfill({ body: STOCK }));
  await page.goto(`${base}#connect`);
  await page.addScriptTag({ type: 'module', content: `
    import { FakeMidi, FakeUsb } from '/test/fake-devices.ts';
    const fields = Object.fromEntries(${JSON.stringify(fields)}.split(';').map((kv) => kv.split(':')));
    const files = {};
    for (const [path, source] of Object.entries(${JSON.stringify(files)})) {
      files[path] = source.startsWith('/') ? new Uint8Array(await (await fetch(source)).arrayBuffer()) : new TextEncoder().encode(source);
    }
    const midi = new FakeMidi(fields), usb = new FakeUsb(files, ${free});
    Object.defineProperty(navigator, 'requestMIDIAccess', { value: midi.requestMIDIAccess, configurable: true });
    Object.defineProperty(navigator, 'usb', { value: usb, configurable: true });
    window.fakes = { midi, usb };
    document.body.dataset.fakes = 'ready';` });
  await page.waitForSelector('body[data-fakes=ready]');
  await page.locator('#nav a[data-page=install]').click();
  return page;
}

const stage = (page: Page): Promise<string | null> => page.locator('#page-install').getAttribute('data-stage');
const sent = (page: Page): Promise<number[]> => page.evaluate(() => (window as unknown as { fakes: { midi: { sent: { type: number }[] } } }).fakes.midi.sent.map((f) => f.type));

async function refusal(page: Page): Promise<{ kind: string | null; text: string }> {
  await expect.poll(() => stage(page), { timeout: 15_000 }).toBe('stop');
  const card = page.locator('[data-verdict]');
  return { kind: await card.getAttribute('data-verdict'), text: (await card.textContent()) ?? '' };
}

const press = (page: Page, name: string | RegExp): Promise<void> => page.getByRole('button', { name, exact: typeof name === 'string' }).click();
const CHECK = /Check my OP/;

/** From te-boot to the copy step: the dev menu already on, then the computer, the dev menu and its transfer mode. */
async function toTransfer(page: Page, computer: 'Mac' | 'Windows'): Promise<void> {
  await press(page, CHECK);
  await expect.poll(() => stage(page), { timeout: 15_000 }).toBe('menu');
  await press(page, 'Yes, I see it');
  await press(page, computer);
  await press(page, 'Dev menu is open');
  expect(await page.locator('#page-install').textContent()).toContain(computer === 'Mac' ? '2. mtp file transfer (mac)' : '3. mtp file transfer (windows)');
  await press(page, 'I see "mtp mode active"');
  if (computer === 'Mac') await press(page, 'They’re closed · Continue');
}

it.skipIf(why !== null)('refuses a 1.2.5 bootloader and unknown firmware, and guides an older OP-XY to official 1.2.1', async () => {
  let page = await open('mode:bootloader;product:OP-XY;serial:X1;bl_version:1000.0.23+0;os_version:1.2.5+0');
  await press(page, CHECK);
  let r = await refusal(page);
  expect(r).toMatchObject({ kind: 'refuse', text: expect.stringContaining('1.2.5 bootloader') });
  expect(await sent(page)).toEqual([0x09]);                    // nothing but the identity request
  await page.close();

  page = await open('mode:bootloader;product:OP-XY;serial:X1;bl_version:1000.0.20+0;os_version:1.3.0');
  await press(page, CHECK);
  r = await refusal(page);
  expect(r).toMatchObject({ kind: 'refuse', text: expect.stringContaining('Unknown firmware') });
  await page.close();

  page = await open('mode:bootloader;product:OP-XY;serial:X1;bl_version:1000.0.20+0;os_version:1.1.33+0');
  await press(page, CHECK);
  r = await refusal(page);
  expect(r.kind).toBe('update');
  expect(await page.getByRole('link', { name: /Download official OS 1\.2\.1/ }).getAttribute('href'))
    .toBe('https://teenage.engineering/_software/op-xy/opxy_firmware_1_2_1.tfw');
  expect(await page.locator('[data-official]').textContent()).toContain('Never install 1.2.5');
  expect(await sent(page)).toEqual([0x09]);
  await page.close();
});

it.skipIf(why !== null)('switches the dev menu on, then stops on a missing stock os.ldr and on low storage (Mac)', async () => {
  let page = await open(OK_121, { 'content/op1_factory.json': '{}' });
  await press(page, CHECK);
  await expect.poll(() => stage(page), { timeout: 15_000 }).toBe('menu');
  await press(page, 'No, it’s missing');
  await press(page, 'Enable dev menu');
  await expect.poll(() => stage(page)).toBe('refresh-open');
  expect((await sent(page)).at(-1)).toBe(0xac);
  expect(await page.evaluate(() => [...(window as unknown as { fakes: { midi: { sent: { payload: Uint8Array }[] } } }).fakes.midi.sent.at(-1)!.payload.slice(-2)]))
    .toEqual([0xba, 0xa7]);                                     // on, never off
  await press(page, 'System menu is open');
  await press(page, 'I’m back at te-boot');
  await press(page, 'Yes, I see it');
  await press(page, 'Mac');
  await press(page, 'Dev menu is open');
  await press(page, 'I see "mtp mode active"');
  await press(page, 'They’re closed · Continue');
  await press(page, 'Send to OP–XY');
  const r = await refusal(page);
  expect(r).toMatchObject({ kind: 'reinstall', text: expect.stringContaining('factory reset') });
  expect(await page.getByRole('link', { name: /Download official OS 1\.2\.1/ }).isVisible()).toBe(true);
  await page.close();

  page = await open(OK_121, { 'upgrade/firmware/blackfin/bin/os.ldr': 'not the stock file' }, 1024 * 1024);
  await toTransfer(page, 'Mac');
  await press(page, 'Send to OP–XY');
  expect(await refusal(page)).toMatchObject({ kind: 'space', text: expect.stringContaining('Not enough free storage') });
  expect(await sent(page)).not.toContain(0xac);                 // the dev menu was already there: left alone
  await page.close();
});

it.skipIf(why !== null)('offers the fresh start with official 1.2.1 when MTP fails', async () => {
  const page = await open(OK_121);
  await toTransfer(page, 'Mac');
  await press(page, 'I’m having trouble connecting to MTP');
  expect(await stage(page)).toBe('mtp-help');
  expect(await page.locator('#mtp-faq').textContent()).toContain('MTP troubleshooting FAQ');
  await press(page, 'I understand the data loss · Show format steps');
  await press(page, 'Format finished · Reinstall OS 1.2.1');
  expect(await page.locator('[data-official]').isVisible()).toBe(true);
  await press(page, '1.2.1 reinstalled · Continue');
  expect(await stage(page)).toBe('boot');
  await page.close();
});

it.skipIf(why !== null || STOCK === null)('copies os.ldr over WebUSB and leads to Track 5 and the PLAY start (Mac)', async () => {
  const page = await open(OK_121, { 'upgrade/firmware/blackfin/bin/os.ldr': '/__test/stock.ldr' });
  await toTransfer(page, 'Mac');
  await press(page, 'Send to OP–XY');
  await expect.poll(() => page.getByRole('button', { name: 'File sent · Continue' }).isVisible(), { timeout: 30_000 }).toBe(true);
  const copied = await page.evaluate(() => [...(window as unknown as { fakes: { usb: { find: (p: string) => { data: Uint8Array } } } }).fakes.usb.find('os.ldr').data]);
  expect(Buffer.from(copied).equals(IMAGE)).toBe(true);
  await press(page, 'File sent · Continue');
  await press(page, 'I’m back at the dev menu');
  expect(await page.locator('#page-install').textContent()).toContain('5. write os to boot partition');
  await press(page, 'I pressed track 5');
  await press(page, 'I see "firmware written ok!"');
  expect(await stage(page)).toBe('finish');
  expect(await page.locator('#page-install').textContent()).toContain('Hold PLAY while switching on');
  await page.close();
});

it.skipIf(why !== null)('checks a dropped stock file and hands over our os.ldr (Windows)', async () => {
  const page = await open(OK_121);
  await toTransfer(page, 'Windows');
  expect(await stage(page)).toBe('stock');
  await page.locator('.drop input[type=file]').setInputFiles({ name: 'os.ldr', mimeType: 'application/octet-stream', buffer: Buffer.from('other') });
  expect(await refusal(page)).toMatchObject({ kind: 'refuse', text: expect.stringContaining('Unknown firmware') });
  await press(page, 'Start again');
  await toTransfer(page, 'Windows');
  await press(page, 'Skip: check it after the first start');
  const download = page.waitForEvent('download');
  await press(page, 'Download os.ldr');
  const file = await download;
  expect(file.suggestedFilename()).toBe('os.ldr');
  expect(readFileSync((await file.path())!).equals(IMAGE)).toBe(true);
  expect(await page.locator('#page-install').textContent()).toContain('Yes, copy to the ROOT folder.');
  await press(page, 'File copied · Continue');
  expect(await stage(page)).toBe('exit-transfer');
  expect(await page.evaluate(() => (window as unknown as { fakes: { usb: { requested: number } } }).fakes.usb.requested)).toBe(0);   // no WebUSB on Windows
  await page.close();
});

it.skipIf(why !== null)('keeps the stage and the chosen computer over a reload, and goes back and forward', async () => {
  const page = await open(OK_121);
  await toTransfer(page, 'Windows');
  expect(await stage(page)).toBe('stock');
  expect(new URL(page.url()).pathname).toBe('/install/stock');
  await page.reload();
  await expect.poll(() => stage(page)).toBe('stock');
  await page.goBack();
  await expect.poll(() => stage(page)).toBe('start-transfer');
  expect(await page.locator('#page-install').textContent()).toContain('3. mtp file transfer (windows)');   // the computer, kept
  await page.goForward();
  await expect.poll(() => stage(page)).toBe('stock');
  expect(await page.locator('#serial-find').count()).toBe(0);      // no Web Serial here: nothing to look for
  await page.close();
});
