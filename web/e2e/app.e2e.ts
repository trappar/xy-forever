// The app in the system Chrome (headless) against the device simulator: connect with ?sim=PORT,
// see the Device page, then browse Files. Starts `python3 -m xy_forever.sim` from this repository
// through the client's simulator helpers, with its state under <private sessions>/web-e2e (the
// private folder of xy_forever/paths.py, so XYF_PRIVATE applies; override with XYF_SIM_STATE), and
// removes the state afterwards. Skipped with the reason when the simulator cannot run here.
import { rmSync } from 'node:fs';
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

beforeAll(async () => {
  if (found.why) return;
  stopSim = (await startAll(state, port, [['web', 0, []]])).stopAll;
  server = await createServer({ configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)), server: { port: 0, host: '127.0.0.1' }, logLevel: 'error' });
  await server.listen();
  browser = await chromium.launch({ channel: 'chrome', headless: true });
});

afterAll(async () => {
  await browser?.close();
  await server?.close();
  await stopSim?.();
  if (!found.why) rmSync(state, { recursive: true, force: true });
});

it.skipIf(found.why !== null)('connects to the simulator and shows the device and its files', async () => {
  const address = server!.httpServer!.address();
  const httpPort = typeof address === 'object' && address ? address.port : 0;
  const page = await browser!.newPage();
  await page.goto(`http://127.0.0.1:${httpPort}/?sim=${port}`);
  await page.getByRole('button', { name: 'Connect' }).click();
  await expect.poll(() => page.locator('[data-fact=build]').textContent(), { timeout: 20_000 }).toMatch(/\d+/);
  await page.locator('#nav a[data-page=files]').click();
  await expect.poll(() => page.locator('#crumbs').textContent(), { timeout: 10_000 }).toContain('yaffs2');
  await expect.poll(() => page.locator('#files-rules').textContent()).toContain('never /yaffs2/bootloader.dev or /yaffs2/upgrade');
  await page.locator('#nav a[data-page=mods]').click();
  await expect.poll(() => page.locator('#page-mods h1').textContent(), { timeout: 10_000 }).toBe('Mods');
});
