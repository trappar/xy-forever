// The Install page (#178) in the system Chrome (headless): every refusal and guidance branch, the dev-menu steps and
// the copy, against fake te-boot MIDI and a fake dev-menu MTP device (test/fake-devices.ts, installed over
// navigator.requestMIDIAccess and navigator.usb before the page uses them). No simulator and no real device: Web Serial
// and WebUSB are switched off in Chrome. The release pin and image are test ones, served in place of release.json and
// release/os.ldr. The page never reads the stock os.ldr (XY Forever adopts it on the device: install-sim.e2e.ts), so a
// stand-in file is enough.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { expect, test } from './cpu.ts';
import type { Browser, Page, Locator } from '@playwright/test';

const IMAGE = Buffer.from(Uint8Array.from({ length: 70_000 }, (_, i) => (i * 31 + 7) & 0xff));
const SHA = createHash('sha256').update(IMAGE).digest('hex');
const PIN = { schema: 1, version: '0.1.0', image: SHA.slice(0, 8), sha256: SHA, bytes: IMAGE.length };
const CANARY_IMAGE = Buffer.from(IMAGE.map((b) => b ^ 0x5a));
const CANARY_SHA = createHash('sha256').update(CANARY_IMAGE).digest('hex');
const CANARY = {
  ...PIN,
  image: CANARY_SHA.slice(0, 8),
  sha256: CANARY_SHA,
  channel: 'canary',
  commit: '0123456789abcdef0123456789abcdef01234567',
  date: '2026-11-23T10:00:00Z',
};
const OK_121 = 'mode:bootloader;product:OP-XY;serial:X1;bl_version:1000.0.20+0;os_version:1.2.1+0';
const STOCK = 'upgrade/firmware/blackfin/bin/os.ldr';
const STOCK_SIZE = 'size:2373336'; // a stand-in of the stock 1.2.1 size: the page never reads it

const nav = (page: Page, name: string): Locator =>
  page.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name, exact: true });

let browser: Browser | undefined; // the project's Chrome (playwright.config.ts: no real Web Serial or WebUSB)
test.beforeAll(({ browser: projectBrowser }) => {
  browser = projectBrowser;
});

/** The app with fake devices: te-boot answering with fields, an MTP device holding files (path -> test URL, size:N or
 * text); with canary, the site also offers the test canary (canary/release.json and canary/os.ldr). */
async function open(
  fields: string,
  files: Record<string, string> = {},
  free = 64 * 1024 * 1024,
  canary = false,
): Promise<Page> {
  const base = process.env['XYF_E2E_URL']!; // the run's dev server (playwright.config.ts webServer)
  const page = await browser!.newPage({ acceptDownloads: true });
  await page.route('**/src/install/release.json*', (route) =>
    route.fulfill({ contentType: 'text/javascript', body: `export default ${JSON.stringify(PIN)};` }),
  );
  await page.route('**/release/os.ldr', (route) =>
    route.fulfill({ contentType: 'application/octet-stream', body: IMAGE }),
  );
  if (canary) {
    await page.route('**/canary/release.json', (route) => route.fulfill({ json: CANARY }));
    await page.route('**/canary/os.ldr', (route) =>
      route.fulfill({ contentType: 'application/octet-stream', body: CANARY_IMAGE }),
    );
  }
  await page.goto(`${base}manage/`); // no install flow yet: it starts with the fakes in place (the Install click below)
  await page.getByRole('heading', { name: 'Connect your OP-XY' }).waitFor(); // its look done before the fakes
  await page.addScriptTag({
    type: 'module',
    content: `
    import { FakeMidi, FakeUsb } from '/test/fake-devices.ts';
    const fields = Object.fromEntries(${JSON.stringify(fields)}.split(';').map((kv) => kv.split(':')));
    const files = {};
    for (const [path, source] of Object.entries(${JSON.stringify(files)})) {
      files[path] = source.startsWith('/') ? new Uint8Array(await (await fetch(source)).arrayBuffer())
        : source.startsWith('size:') ? new Uint8Array(Number(source.slice(5))) : new TextEncoder().encode(source);
    }
    const midi = new FakeMidi(fields), usb = new FakeUsb(files, ${free});
    Object.defineProperty(navigator, 'requestMIDIAccess', { value: midi.requestMIDIAccess, configurable: true });
    Object.defineProperty(navigator, 'usb', { value: usb, configurable: true });
    window.fakes = { midi, usb };
    document.body.dataset.fakes = 'ready';`,
  });
  await page.waitForSelector('body[data-fakes=ready]');
  await nav(page, 'Install').click();
  return page;
}

const stage = (page: Page): Promise<string | null> => page.locator('[data-stage]').getAttribute('data-stage');
/** Once the page shows that stage (a locator check: it waits for the page, not for a polling interval). */
const atStage = (page: Page, name: string): Promise<void> =>
  expect(page.locator('[data-stage]')).toHaveAttribute('data-stage', name);
const sent = (page: Page): Promise<number[]> =>
  page.evaluate(() =>
    (window as unknown as { fakes: { midi: { sent: { type: number }[] } } }).fakes.midi.sent.map((f) => f.type),
  );

async function refusal(page: Page): Promise<{ kind: string | null; text: string }> {
  await atStage(page, 'stop');
  const card = page.locator('[data-verdict]');
  return { kind: await card.getAttribute('data-verdict'), text: (await card.textContent()) ?? '' };
}

const press = (page: Page, name: string | RegExp): Promise<void> =>
  page.getByRole('button', { name, exact: typeof name === 'string' }).click();
const CHECK = /Check my OP/;

/** From te-boot to the copy step: the dev menu already on, then the computer, the dev menu and its transfer mode. */
async function toTransfer(page: Page, computer: 'Mac' | 'Windows'): Promise<void> {
  await press(page, CHECK);
  await atStage(page, 'menu');
  await press(page, 'Yes, I see it');
  await press(page, computer);
  await press(page, 'Dev menu is open');
  expect(await page.locator('[data-stage]').textContent()).toContain(
    computer === 'Mac' ? '2. mtp file transfer (mac)' : '3. mtp file transfer (windows)',
  );
  await press(page, 'I see "mtp mode active"');
  await press(page, computer === 'Mac' ? 'They’re closed · Continue' : 'I see os.ldr, about 2,318 KB');
}

test('refuses a 1.2.5 bootloader and unknown firmware, and guides an older OP-XY to official 1.2.1', async () => {
  let page = await open('mode:bootloader;product:OP-XY;serial:X1;bl_version:1000.0.23+0;os_version:1.2.5+0');
  await press(page, CHECK);
  let r = await refusal(page);
  expect(r).toMatchObject({ kind: 'refuse', text: expect.stringContaining('1.2.5 bootloader') });
  expect(await sent(page)).toEqual([0x09]); // nothing but the identity request
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
  expect(await page.getByRole('link', { name: /Download official OS 1\.2\.1/ }).getAttribute('href')).toBe(
    'https://teenage.engineering/_software/op-xy/opxy_firmware_1_2_1.tfw',
  );
  expect(await page.locator('[data-official]').textContent()).toContain('Never install 1.2.5');
  expect(await sent(page)).toEqual([0x09]);
  await page.close();
});

test('switches the dev menu on, then stops on a missing stock os.ldr and on low storage (Mac)', async () => {
  let page = await open(OK_121, { 'content/op1_factory.json': '{}' });
  await press(page, CHECK);
  await atStage(page, 'menu');
  await press(page, 'No, it’s missing');
  await press(page, 'Enable dev menu');
  await atStage(page, 'refresh-open');
  expect((await sent(page)).at(-1)).toBe(0xac);
  expect(
    await page.evaluate(() => [
      ...(window as unknown as { fakes: { midi: { sent: { payload: Uint8Array }[] } } }).fakes.midi.sent
        .at(-1)!
        .payload.slice(-2),
    ]),
  ).toEqual([0xba, 0xa7]); // on, never off
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

  page = await open(OK_121, { [STOCK]: STOCK_SIZE }, 1024 * 1024);
  await toTransfer(page, 'Mac');
  await press(page, 'Send to OP–XY');
  expect(await refusal(page)).toMatchObject({
    kind: 'space',
    text: expect.stringContaining('Not enough free storage'),
  });
  expect(await sent(page)).not.toContain(0xac); // the dev menu was already there: left alone
  await page.close();

  page = await open(OK_121, { [STOCK]: 'not the stock size' });
  await toTransfer(page, 'Mac');
  await press(page, 'Send to OP–XY');
  expect(await refusal(page)).toMatchObject({
    kind: 'reinstall',
    text: expect.stringContaining("not the size of teenage engineering's OS 1.2.1 (2,318 KB)"),
  });
  expect(
    await page.evaluate(() =>
      (window as unknown as { fakes: { usb: { find: (p: string) => unknown } } }).fakes.usb.find('os.ldr'),
    ),
  ).toBeUndefined();
  await page.close();
});

test('links to no tool that can switch the dev menu off', async () => {
  const page = await open(OK_121);
  await page.getByTestId('connection-help').locator('summary').click();
  expect(await page.getByTestId('connection-help').textContent()).toContain('site settings');
  expect(await page.locator('a[href*="bagofwaffles.github.io"]').count()).toBe(0); // the hosted Ferret has a disable button
  await page.close();
});

test('offers the fresh start with official 1.2.1 when MTP fails', async () => {
  const page = await open(OK_121);
  await toTransfer(page, 'Mac');
  await press(page, 'I’m having trouble connecting to MTP');
  expect(await stage(page)).toBe('mtp-help');
  expect(await page.getByTestId('mtp-faq').textContent()).toContain('MTP troubleshooting FAQ');
  await press(page, 'I understand the data loss · Show format steps');
  await press(page, 'Format finished · Reinstall OS 1.2.1');
  expect(await page.locator('[data-official]').isVisible()).toBe(true);
  await press(page, '1.2.1 reinstalled · Continue');
  expect(await stage(page)).toBe('boot');
  await page.close();
});

test('copies os.ldr over WebUSB, never reading the stock os.ldr, and ends with Track 5 and a power-on (Mac)', async () => {
  const page = await open(OK_121, { [STOCK]: STOCK_SIZE });
  await toTransfer(page, 'Mac');
  await press(page, 'Send to OP–XY');
  await page.getByRole('button', { name: 'File sent · Continue' }).waitFor();
  const fakes = (): Promise<{ copied: number[]; reads: string[] }> =>
    page.evaluate(() => {
      const usb = (
        window as unknown as { fakes: { usb: { find: (p: string) => { data: Uint8Array }; reads: string[] } } }
      ).fakes.usb;
      return { copied: [...usb.find('os.ldr').data], reads: usb.reads };
    });
  const { copied, reads } = await fakes();
  expect(Buffer.from(copied).equals(IMAGE)).toBe(true);
  expect(reads).toEqual(['os.ldr']); // our copy read back; the stock file never
  await press(page, 'File sent · Continue');
  expect(await stage(page)).toBe('exit-transfer');
  await press(page, '← Back'); // back at transfer: the file can be sent again
  await atStage(page, 'transfer');
  await press(page, 'Send to OP–XY');
  await page.getByRole('button', { name: 'File sent · Continue' }).waitFor();
  expect((await fakes()).reads).toEqual(['os.ldr', 'os.ldr']);
  await press(page, 'File sent · Continue');
  await press(page, 'I’m back at the dev menu');
  expect(await page.locator('[data-stage]').textContent()).toContain('5. write os to boot partition');
  await press(page, 'I pressed track 5');
  await press(page, 'I see "firmware written ok!"');
  expect(await stage(page)).toBe('start');
  const text = (await page.locator('[data-stage]').textContent()) ?? '';
  expect(text).toContain('Switch your OP–XY off, then on.');
  expect(text.match(/hold PLAY while switching on/g)).toHaveLength(1); // said once
  expect(text).toContain('Expect the logo to stay up longer than usual');
  expect(text).not.toMatch(/wait \d+ seconds|5 seconds/i);
  await page.close();
});

test('asks the owner to find the stock file in Explorer, then hands over our os.ldr to copy by hand (Windows)', async () => {
  let page = await open(OK_121);
  await press(page, CHECK);
  await atStage(page, 'menu');
  for (const name of ['Yes, I see it', 'Windows', 'Dev menu is open', 'I see "mtp mode active"'])
    await press(page, name);
  expect(await stage(page)).toBe('check-stock');
  expect(await page.locator('[data-stage]').textContent()).toContain(
    'upgrade › firmware › blackfin › bin. os.ldr must be there, about 2,318 KB',
  );
  await press(page, 'It’s missing or another size');
  expect(await refusal(page)).toMatchObject({
    kind: 'reinstall',
    text: expect.stringContaining('missing or not the size'),
  });
  await page.close();

  page = await open(OK_121);
  expect(await page.getByTestId('channel').count()).toBe(0); // no canary on this site: Stable only
  expect(await page.getByTestId('release-identity').textContent()).toBe(
    `Stable: XY Forever 0.1.0, image ${PIN.image}.`,
  );
  await toTransfer(page, 'Windows');
  expect(await stage(page)).toBe('transfer');
  const download = page.waitForEvent('download');
  await press(page, 'Download os.ldr');
  const file = await download;
  expect(file.suggestedFilename()).toBe('os.ldr');
  expect(readFileSync((await file.path())!).equals(IMAGE)).toBe(true);
  expect(await page.locator('[data-stage]').textContent()).toContain('Yes, copy to the ROOT folder.');
  await press(page, 'File copied · Continue');
  expect(await stage(page)).toBe('exit-transfer');
  expect(
    await page.evaluate(() => (window as unknown as { fakes: { usb: { requested: number } } }).fakes.usb.requested),
  ).toBe(0); // no WebUSB on Windows
  await page.close();
});

test('keeps the stage and the chosen computer over a reload, and goes back and forward', async () => {
  const page = await open(OK_121);
  await toTransfer(page, 'Windows');
  expect(await stage(page)).toBe('transfer');
  expect(new URL(page.url()).pathname).toBe('/install/transfer/');
  await page.reload();
  await atStage(page, 'transfer');
  await page.goBack();
  await atStage(page, 'check-stock');
  await page.goBack();
  await atStage(page, 'start-transfer');
  expect(await page.locator('[data-stage]').textContent()).toContain('3. mtp file transfer (windows)'); // the computer, kept
  await page.goForward();
  await page.goForward();
  await atStage(page, 'transfer');
  expect(await page.getByRole('button', { name: 'Find my OP–XY' }).count()).toBe(0); // no Web Serial here: nothing to look for
  await page.close();
});

test('installs the canary when chosen, and keeps it over a reload and a newer deploy', async () => {
  const page = await open(OK_121, {}, undefined, true);
  const identity = page.getByTestId('release-identity');
  await expect(identity).toHaveText(`Stable: XY Forever 0.1.0, image ${PIN.image}.`); // Stable by default
  await page.getByRole('radio', { name: /Canary/ }).check();
  await expect(identity).toHaveText(
    `Canary (experimental): XY Forever 0.1.0, image ${CANARY.image}, built 2026-11-23 from commit 0123456.`,
  );
  await toTransfer(page, 'Windows');
  await atStage(page, 'transfer');
  await page.unroute('**/canary/release.json'); // a deploy without that canary: the install keeps its image
  await page.route('**/canary/release.json', (route) => route.fulfill({ json: { ...CANARY, commit: 'f'.repeat(40) } }));
  await page.reload();
  await atStage(page, 'transfer');
  await expect(identity).toContainText(`image ${CANARY.image}`);
  const download = page.waitForEvent('download');
  await press(page, 'Download os.ldr');
  expect(readFileSync((await (await download).path())!).equals(CANARY_IMAGE)).toBe(true);
  await page.close();
});
