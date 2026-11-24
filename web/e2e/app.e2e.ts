// The app in the system Chrome (headless) against the device simulator: with ?sim=PORT the site opens on Install and
// switches to Manage by itself (#316), shows the Device page, then browses Files. Starts `python -m xy_forever.sim` from this repository's venv
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
const state = process.env['XYF_SIM_STATE'] ?? join(found.sessions, `web-e2e-${process.pid}`);
let stopSim: (() => Promise<void>) | undefined;
let simPort = 0; // the simulator's USB endpoint: a port it holds (simulators.ts)
let browser: Browser | undefined; // the project's Chrome (playwright.config.ts: no real Web Serial)

test.beforeAll(async ({ browser: projectBrowser }) => {
  browser = projectBrowser;
  if (found.why) return;
  const started = await startAll(state, [['web', []]]);
  stopSim = started.stopAll;
  simPort = started.infos['web']!.port;
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
  await page.goto(appUrl(`?sim=${simPort}`));
  expect(await page.evaluate(() => 'serial' in navigator)).toBe(false);
  // no click: XY Forever answers, so Install switches to Manage
  await expect(page.locator('[data-fact=version]')).toHaveText(/^\d+\.\d+\.\d+$/, { timeout: 20_000 });
  expect(new URL(page.url()).pathname).toBe('/manage/');
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
  await page.getByRole('button', { name: 'Disconnect' }).click();
  await expect(page.getByRole('button', { name: 'Connect', exact: true })).toBeVisible(); // and stays: no look
  await page.getByRole('button', { name: 'Bug report' }).click();
  expect(JSON.parse((await json.textContent())!)).toMatchObject({
    connected: false,
    device: connected.device,
    activity: expect.arrayContaining([{ event: 'disconnect', outcome: 'finished' }]),
  });
  await page.close();
});

/** The simulator's verified build as a release pin, with its bytes. */
function simRelease() {
  const imagePath = verifiedBuild(found);
  const image = readFileSync(imagePath);
  const sha256 = createHash('sha256').update(image).digest('hex');
  const { version } = JSON.parse(readFileSync(join(dirname(imagePath), 'manifest.json'), 'utf8')) as {
    version: string;
  };
  return { image, pin: { schema: 1, version, sha256, image: sha256.slice(0, 8), bytes: image.length } };
}

/**
 * Relays the page to the simulator, rewriting each HELLO reply's info (the same simulator executable handles every
 * boot, so the test says which build it reports, given the restart target the page asked for so far, if any);
 * returns that restart target.
 */
async function relayHello(
  page: Page,
  rewrite: (info: string, restart: number | undefined) => string,
): Promise<() => number | undefined> {
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
      for (const frame of replies.feed(Buffer.from(message))) {
        let payload = frame.payload;
        if (frame.type === (v1.TYPE.hello | v1.REPLY)) {
          const reply = v1.decodeHelloReply(payload);
          if (reply.ok)
            payload = v1.encodeHelloReply({ ...reply, fields: { info: rewrite(reply.fields.info, restart) } });
        }
        socket.send(Buffer.from(v1.encodeFrame(frame.type, frame.seq, payload)));
      }
    });
  });
  return () => restart;
}

/** The first boot reports an older build (older(info)); the restarted one, the simulator's own. */
const relayOlderFirstBoot = (page: Page, older: (info: string) => string): Promise<() => number | undefined> =>
  relayHello(page, (info, restart) => (restart === undefined ? older(info) : info));

/**
 * image with one byte of its first data record's payload changed: another build (other bytes, another image ID)
 * that still installs, since the LDR check reads record headers and the cold descriptor, never payloads
 * (native/ldr.cpp).
 */
function otherBuild(image: Buffer): Buffer {
  const copy = Buffer.from(image);
  const descriptor = copy.indexOf(Buffer.from('HCLX', 'latin1')); // its magic, 0x584c4348 little-endian
  for (let offset = 16; offset + 16 < copy.length;) {
    const flags = copy.readUInt32LE(offset) & 0xffff;
    const count = copy.readUInt32LE(offset + 8);
    const payload = offset + 16;
    if (flags === 4 && count > 0 && (payload + count <= descriptor || payload >= descriptor + 28)) {
      copy[payload]! ^= 0xff;
      return copy;
    }
    offset = payload + (flags === 0x104 ? 0 : count);
  }
  throw new Error('no data record outside the cold descriptor');
}

async function serveRelease(page: Page, stable: object, image?: Buffer): Promise<void> {
  await page.route('**/src/install/release.json*', (route) =>
    route.fulfill({ contentType: 'text/javascript', body: `export default ${JSON.stringify(stable)};` }),
  );
  if (image) await page.route('**/release/os.ldr', (route) => route.fulfill({ body: image }));
}

test('installs a newer XY Forever and restarts into its menu', async () => {
  const { image, pin } = simRelease();
  const release = { ...pin, notes: 'Faster updates.' };
  const { version } = release;
  const page = await browser!.newPage();
  try {
    await serveRelease(page, release, image);
    const restart = await relayOlderFirstBoot(page, (info) => info.replace(/;version=[^;]+/, ';version=191'));
    await page.goto(appUrl(`?sim=${simPort}`)); // Install switches to Manage by itself
    const update = page.getByRole('button', { name: `Update to ${version}`, exact: true });
    await update.waitFor();
    expect(await page.getByText(release.notes).isVisible()).toBe(true);
    expect(await page.getByTestId('channel').count()).toBe(0); // no canary on this site: Stable only
    await update.click();
    await expect(page.getByTestId('update-status')).toHaveText(`XY Forever ${version} is running in the menu.`, {
      timeout: 30_000,
    });
    expect(restart()).toBe(2);
    expect(await page.locator('[data-fact=image]').textContent()).toBe(release.image);
    expect(await update.count()).toBe(0);
  } finally {
    await page.close();
  }
});

test('switches Stable to Canary, then offers Stable back at once: the offer follows the running image', async () => {
  const { image, pin: stable } = simRelease();
  const canaryBytes = otherBuild(image);
  const sha256 = createHash('sha256').update(canaryBytes).digest('hex');
  const canary = {
    ...stable,
    sha256,
    image: sha256.slice(0, 8),
    channel: 'canary',
    commit: '0123456789abcdef0123456789abcdef01234567',
    date: '2026-11-23T10:00:00Z',
  };
  const page = await browser!.newPage();
  try {
    let running = stable.image; // the build the OP-XY reports: the last one the page downloaded to install
    await serveRelease(page, stable, image);
    await page.route('**/canary/release.json', (route) => route.fulfill({ json: canary }));
    await page.route('**/canary/os.ldr', (route) => {
      running = canary.image;
      return route.fulfill({ body: canaryBytes });
    });
    const restart = await relayHello(page, (info) => info.replace(/;image=[^;]+/, `;image=${running}`));
    await page.goto(appUrl(`?sim=${simPort}`)); // Install switches to Manage by itself
    const imageFact = page.locator('[data-fact=image]');
    const none = page.getByTestId('update-none');
    const status = page.getByTestId('update-status');
    const canaryName = 'Canary 2026-11-23 (0123456)';
    const toCanary = page.getByRole('button', { name: `Update to ${canaryName}`, exact: true });
    const choose = (channel: RegExp) => page.getByRole('radio', { name: channel }).check();

    await expect(imageFact).toHaveText(stable.image, { timeout: 20_000 });
    await expect(none).toHaveText(`Your OP-XY runs XY Forever ${stable.version}.`); // Stable chosen: already running
    await choose(/Canary/);
    await toCanary.click();
    await expect(status).toHaveText(`${canaryName} is running in the menu.`, { timeout: 30_000 });
    expect(restart()).toBe(2);
    await expect(imageFact).toHaveText(canary.image); // read again from the restarted OP-XY
    await expect(none).toHaveText(`Your OP-XY runs ${canaryName}.`);
    expect(await toCanary.count()).toBe(0);

    await choose(/Stable/); // no reload: the way back is offered, the last update's outcome cleared
    await expect(page.getByRole('button', { name: `Update to ${stable.version}`, exact: true })).toBeEnabled();
    await expect(status).toHaveText('');
    await choose(/Canary/);
    await expect(none).toHaveText(`Your OP-XY runs ${canaryName}.`);
  } finally {
    await page.evaluate(() => localStorage.removeItem('xyf.channel')).catch(() => {});
    await page.close();
  }
});

test('previews and downloads the same offline snapshot after a connection error', async () => {
  const page = await browser!.newPage();
  const failure = 'synthetic port selection error with unit serial XY-DISCONNECTED-TEST';
  await page.addInitScript((message) => {
    Object.defineProperty(navigator, 'serial', {
      value: {
        addEventListener: () => {},
        removeEventListener: () => {},
        getPorts: async () => [],
        requestPort: () => {
          throw new Error(message);
        },
      },
    });
  }, failure);
  await page.goto(appUrl('manage/'));
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
