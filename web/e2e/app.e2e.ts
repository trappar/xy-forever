// The app in the system Chrome (headless) against the device simulator: connect with ?sim=PORT,
// see the Device page, then browse Files. Starts `python3 -m xy_forever.sim` from this repository
// through the client's simulator helpers, with its state under <private sessions>/web-e2e (the
// private folder of xy_forever/paths.py, so XYF_PRIVATE applies; override with XYF_SIM_STATE), and
// removes the state afterwards. Skipped with the reason when the simulator cannot run here.
import { readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import type { Browser } from 'playwright';
import { createServer } from 'vite';
import type { ViteDevServer } from 'vite';
import { afterAll, beforeAll, expect, it } from 'vitest';
import { findSimulator, startAll } from '../node_modules/xy-forever-client/test/simulators.ts';

const found = findSimulator();
if (found.why) console.warn(`\nweb e2e skipped: ${found.why}\n`);
const port = 21000 + 2 * Math.floor(Math.random() * 5000);
const state = process.env['XYF_SIM_STATE'] ?? join(found.sessions ?? '', `web-e2e-${port}`);
let stopSim: (() => Promise<void>) | undefined;
let server: ViteDevServer | undefined;
let browser: Browser | undefined;
const NO_DEVICE = ['--disable-blink-features=Serial'];   // no real Web Serial: tests may not open a real device

beforeAll(async () => {
  if (!found.why) stopSim = (await startAll(state, port, [['web', 0, []]])).stopAll;
  server = await createServer({ configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)), server: { port: 0, host: '127.0.0.1' }, logLevel: 'error' });
  await server.listen();
  browser = await chromium.launch({ channel: 'chrome', headless: true, args: NO_DEVICE });
});

afterAll(async () => {
  await browser?.close();
  await server?.close();
  await stopSim?.();
  if (!found.why) rmSync(state, { recursive: true, force: true });
});

function appUrl(query = ''): string {
  const address = server!.httpServer!.address();
  const httpPort = typeof address === 'object' && address ? address.port : 0;
  return `http://127.0.0.1:${httpPort}/${query}`;
}

it.skipIf(found.why !== null)('connects to the simulator and shows the device and its files', async () => {
  const page = await browser!.newPage();
  let deviceRequests = 0;
  page.on('websocket', (socket) => {
    if (new URL(socket.url()).pathname === '/xyf-relay') socket.on('framesent', () => { deviceRequests++; });
  });
  await page.goto(appUrl(`?sim=${port}`));
  expect(await page.evaluate(() => 'serial' in navigator)).toBe(false);
  await page.getByRole('button', { name: 'Connect' }).click();
  await expect.poll(() => page.locator('[data-fact=version]').textContent(), { timeout: 20_000 }).toMatch(/^\d+\.\d+\.\d+$/);
  const requestsBeforeReport = deviceRequests;
  await page.getByRole('button', { name: 'Bug report' }).click();
  const preview = page.getByRole('dialog', { name: 'Bug report preview' });
  const json = preview.locator('pre');
  const connected = JSON.parse((await json.textContent())!);
  expect(connected).toMatchObject({
    connected: true,
    device: { firmwareVersion: await page.locator('[data-fact=version]').textContent(), protocolVersion: expect.stringMatching(/^1\.\d+$/) },
    activity: expect.arrayContaining([{ event: 'connect', outcome: 'finished' }]),
  });
  await preview.getByRole('button', { name: 'Close' }).click();
  expect(deviceRequests).toBe(requestsBeforeReport);
  await page.locator('#nav a[data-page=files]').click();
  await expect.poll(() => page.locator('#crumbs').textContent(), { timeout: 10_000 }).toContain('yaffs2');
  await expect.poll(() => page.locator('#files-rules').textContent()).toContain('never /yaffs2/bootloader.dev or /yaffs2/upgrade');
  await page.locator('#nav a[data-page=mods]').click();
  await expect.poll(() => page.locator('#page-mods h1').textContent(), { timeout: 10_000 }).toBe('Mods');
  await page.getByRole('link', { name: 'Connect' }).click();
  await page.getByRole('button', { name: 'Disconnect' }).click();
  await expect.poll(() => page.getByRole('button', { name: 'Connect', exact: true }).isVisible()).toBe(true);
  await page.getByRole('button', { name: 'Bug report' }).click();
  expect(JSON.parse((await json.textContent())!)).toMatchObject({
    connected: false,
    device: connected.device,
    activity: expect.arrayContaining([{ event: 'disconnect', outcome: 'finished' }]),
  });
  await page.close();
});

it('previews and downloads the same offline snapshot after a connection error', async () => {
  const page = await browser!.newPage();
  const failure = 'synthetic port selection error with unit serial XY-DISCONNECTED-TEST';
  await page.addInitScript((message) => {
    Object.defineProperty(navigator, 'serial', {
      value: { getPorts: async () => [], requestPort: () => { throw new Error(message); } },
    });
  }, failure);
  await page.goto(appUrl());
  const report = page.getByRole('button', { name: 'Bug report' });
  const preview = page.getByRole('dialog', { name: 'Bug report preview' });
  await report.click();
  expect(JSON.parse((await preview.locator('pre').textContent())!)).toMatchObject({ connected: false, device: null });
  await preview.getByRole('button', { name: 'Close' }).click();

  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect.poll(() => page.locator('#notice').textContent()).toBe(failure);
  await report.click();
  const json = (await preview.locator('pre').textContent())!;
  expect(JSON.parse(json)).toMatchObject({
    bundleVersion: 1, connected: false, device: null,
    activity: expect.arrayContaining([{ event: 'connect', outcome: 'failed', code: 'app_error' }]),
  });
  expect(json).not.toContain('XY-DISCONNECTED-TEST');

  const pending = page.waitForEvent('download');
  await preview.getByRole('button', { name: 'Download JSON' }).click();
  const download = await pending;
  expect(download.suggestedFilename()).toBe('xy-forever-bug-report.json');
  expect(readFileSync(await download.path())).toEqual(Buffer.from(json, 'utf8'));
  await page.close();
});
