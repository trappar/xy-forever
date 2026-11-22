// The app in the system Chrome (headless) against the device simulator: connect with ?sim=PORT,
// see the Device page, then browse Files. Starts `python -m xy_forever.sim` from this repository's venv
// through the client's simulator helpers, with its state under <private sessions>/web-e2e (the
// private folder of xy_forever/paths.py, so XYF_PRIVATE applies; override with XYF_SIM_STATE), and
// removes the state afterwards. Skipped with the reason without the private stock files or clang++.
import { createHash } from 'node:crypto';
import { readFileSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { expect, test } from './cpu.ts';
import type { Browser, Locator, Page } from '@playwright/test';
import { findSimulator, startAll } from 'xy-forever-client/test/simulators.ts';
import { protocol as v1 } from 'xy-forever-client';
import { verifiedBuild } from './needs.ts';

const nav = (page: Page, name: string): Locator =>
  page.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name, exact: true });

const found = findSimulator();
if (found.why) console.warn(`\nweb e2e skipped: ${found.why}\n`);
const port = 21000 + 2 * Math.floor(Math.random() * 5000);
const state = process.env['XYF_SIM_STATE'] ?? join(found.sessions, `web-e2e-${port}`);
let stopSim: (() => Promise<void>) | undefined;
let browser: Browser | undefined; // the project's Chrome (playwright.config.ts: no real Web Serial)

test.beforeAll(async ({ browser: projectBrowser }) => {
  browser = projectBrowser;
  if (!found.why) stopSim = (await startAll(state, port, [['web', 0, []]])).stopAll;
});

test.afterAll(async () => {
  await stopSim?.();
  if (!found.why) rmSync(state, { recursive: true, force: true });
});

/** The run's dev server (playwright.config.ts webServer). */
const appUrl = (query = ''): string => `${process.env['XYF_E2E_URL']!}${query}`;

test('connects to the simulator and shows the device and its files', async () => {
  test.skip(found.why !== null, 'skipped: see the warning above');
  const page = await browser!.newPage();
  let deviceRequests = 0;
  page.on('websocket', (socket) => {
    if (new URL(socket.url()).pathname === '/xyf-relay')
      socket.on('framesent', () => {
        deviceRequests++;
      });
  });
  await page.goto(appUrl(`?sim=${port}`));
  expect(await page.evaluate(() => 'serial' in navigator)).toBe(false);
  await page.getByRole('button', { name: 'Connect' }).click();
  await expect(page.locator('[data-fact=version]')).toHaveText(/^\d+\.\d+\.\d+$/, { timeout: 20_000 });
  const requestsBeforeReport = deviceRequests;
  await page.getByRole('button', { name: 'Bug report' }).click();
  const preview = page.getByRole('dialog', { name: 'Bug report preview' });
  const json = preview.locator('pre');
  const connected = JSON.parse((await json.textContent())!);
  expect(connected).toMatchObject({
    connected: true,
    device: {
      firmwareVersion: await page.locator('[data-fact=version]').textContent(),
      protocolVersion: expect.stringMatching(/^1\.\d+$/),
    },
    activity: expect.arrayContaining([{ event: 'connect', outcome: 'finished' }]),
  });
  await preview.getByRole('button', { name: 'Close' }).click();
  expect(deviceRequests).toBe(requestsBeforeReport);
  await nav(page, 'Files').click();
  await expect(page.getByRole('navigation', { name: 'Folder' })).toContainText('yaffs2', { timeout: 10_000 });
  await expect(page.getByTestId('files-rules')).toContainText('never /yaffs2/bootloader.dev or /yaffs2/upgrade');
  await nav(page, 'Mods').click();
  await expect(page.getByRole('heading', { level: 1, name: 'Mods' })).toHaveText('Mods', { timeout: 10_000 });
  await page.getByRole('link', { name: 'Connect' }).click();
  await page.getByRole('button', { name: 'Disconnect' }).click();
  await expect(page.getByRole('button', { name: 'Connect', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Bug report' }).click();
  expect(JSON.parse((await json.textContent())!)).toMatchObject({
    connected: false,
    device: connected.device,
    activity: expect.arrayContaining([{ event: 'disconnect', outcome: 'finished' }]),
  });
  await page.close();
});

test('installs a newer XY Forever and restarts into its menu', async () => {
  const imagePath = verifiedBuild(found);
  const image = readFileSync(imagePath);
  const sha256 = createHash('sha256').update(image).digest('hex');
  const { version } = JSON.parse(readFileSync(join(dirname(imagePath), 'manifest.json'), 'utf8')) as {
    version: string;
  };
  const release = {
    schema: 1,
    version,
    sha256,
    image: sha256.slice(0, 8),
    bytes: image.length,
    notes: 'Faster updates.',
  };
  const page = await browser!.newPage();
  try {
    await page.route('**/src/install/release.json*', (route) =>
      route.fulfill({
        contentType: 'text/javascript',
        body: `export default ${JSON.stringify(release)};`,
      }),
    );
    await page.route('**/release/os.ldr', (route) => route.fulfill({ body: image }));
    let restart: number | undefined;
    await page.routeWebSocket('**/xyf-relay?*', (socket) => {
      const server = socket.connectToServer();
      const requests = new v1.FrameParser();
      const replies = new v1.FrameParser();
      socket.onMessage((message) => {
        for (const frame of requests.feed(Buffer.from(message))) {
          if (frame.type === v1.TYPE['device.reboot']) restart = v1.decodeDeviceRebootRequest(frame.payload).target;
        }
        server.send(message);
      });
      server.onMessage((message) => {
        // The same simulator executable handles both boots. Give the first boot an older release label.
        for (const frame of replies.feed(Buffer.from(message))) {
          let payload = frame.payload;
          if (restart === undefined && frame.type === (v1.TYPE.hello | v1.REPLY)) {
            const reply = v1.decodeHelloReply(payload);
            if (reply.ok) {
              const info = reply.fields.info.replace(/;version=[^;]+/, ';version=191');
              payload = v1.encodeHelloReply({ ...reply, fields: { info } });
            }
          }
          socket.send(Buffer.from(v1.encodeFrame(frame.type, frame.seq, payload)));
        }
      });
    });
    await page.goto(appUrl(`?sim=${port}`));
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    const update = page.getByRole('button', { name: `Update to ${version}`, exact: true });
    await update.waitFor();
    expect(await page.getByText(release.notes).isVisible()).toBe(true);
    await update.click();
    await expect(page.getByTestId('update-status')).toHaveText(`XY Forever ${version} is running in the menu.`, {
      timeout: 30_000,
    });
    expect(restart).toBe(2);
    expect(await page.locator('[data-fact=image]').textContent()).toBe(release.image);
    expect(await update.count()).toBe(0);
  } finally {
    await page.close();
  }
});

test('previews and downloads the same offline snapshot after a connection error', async () => {
  const page = await browser!.newPage();
  const failure = 'synthetic port selection error with unit serial XY-DISCONNECTED-TEST';
  await page.addInitScript((message) => {
    Object.defineProperty(navigator, 'serial', {
      value: {
        getPorts: async () => [],
        requestPort: () => {
          throw new Error(message);
        },
      },
    });
  }, failure);
  await page.goto(appUrl());
  const report = page.getByRole('button', { name: 'Bug report' });
  const preview = page.getByRole('dialog', { name: 'Bug report preview' });
  await report.click();
  expect(JSON.parse((await preview.locator('pre').textContent())!)).toMatchObject({ connected: false, device: null });
  await preview.getByRole('button', { name: 'Close' }).click();

  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByTestId('notice')).toHaveText(failure);
  await report.click();
  const json = (await preview.locator('pre').textContent())!;
  expect(JSON.parse(json)).toMatchObject({
    bundleVersion: 1,
    connected: false,
    device: null,
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
