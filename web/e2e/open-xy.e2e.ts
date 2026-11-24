// Open-XY's catalog on the Mods page against the device simulator, the kit and its release's builder served by the test
// (the builder: the vendored v0.0.7 copy; never the network): a download that fails, and a builder that cannot be
// loaded, say what to do; with the published 0.0.7 kit (private fixtures; skipped without it), an older kit and then
// the newer one offer the newer kit's late entry, and two mods and an extension are sent; and with the live kit
// unavailable, the site's pinned kit (stripped by scripts/web_open_xy.py, refilled from Open-XY's repository: both
// served by the test) gives the catalog and says so (#183).
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { gunzipSync } from 'node:zlib';
import { dirname, extname, join } from 'node:path';
import { expect, test } from './cpu.ts';
import type { Browser, Page } from '@playwright/test';
import { findSimulator, startAll } from 'xy-forever-client/test/simulators.ts';

const found = findSimulator();
const state = join(found.sessions, `web-e2e-open-xy-${process.pid}`);
const kitPath = join(dirname(found.sessions), 'fixtures', 'open-xy-kit-0.0.7.json.gz');
const vendored = new URL('../src/vendor/open-xy/', import.meta.url);
let stopSim: (() => Promise<void>) | undefined;
let simPort = 0;
let browser: Browser | undefined;

test.beforeAll(async ({ browser: projectBrowser }) => {
  browser = projectBrowser;
  if (found.why) return;
  const started = await startAll(state, [['open-xy', []]]);
  stopSim = started.stopAll;
  simPort = started.infos['open-xy']!.port;
});

test.afterAll(async () => {
  await stopSim?.();
  if (!found.why) rmSync(state, { recursive: true, force: true });
});

/** The Mods page of a connected app whose kit downloads answer with serve(); builder: Open-XY 0.0.7's is served. */
interface Pinned {
  kit: Buffer;
  profile: Buffer;
  catalog: Buffer;
}

async function modsPage(serve: () => Buffer | null, builder = true, pinned: Pinned | null = null): Promise<Page> {
  const page = await browser!.newPage();
  await page.route('**/kit.json.gz', (route) => {
    const body = serve();
    return body ? route.fulfill({ body }) : route.abort('internetdisconnected');
  });
  await page.route(/\/sdk\/|cdn\.jsdelivr\.net/, (route) => {
    const path = /\/sdk\/0\.0\.7\/(builder\/core\.mjs|sdk\/browser\/graphics\.mjs|tooling\/default_preset\.json)$/.exec(
      new URL(route.request().url()).pathname,
    )?.[1];
    if (!builder || !path) return route.abort('internetdisconnected');
    const contentType = extname(path) === '.json' ? 'application/json' : 'text/javascript';
    return route.fulfill({ body: readFileSync(new URL(path, vendored)), contentType });
  });
  // (after the routes above: it runs first) the site's pinned kit and builder, and Open-XY's repository (none unless given: the live kit's reason shows)
  await page.route(/\/open-xy\/0\.0\.7\/(.*)$/, (route) => {
    const path = /\/open-xy\/0\.0\.7\/(.*)$/.exec(new URL(route.request().url()).pathname)![1]!;
    if (!pinned) return route.abort('internetdisconnected');
    if (path === 'kit.json.gz') return route.fulfill({ body: pinned.kit });
    const contentType = extname(path) === '.json' ? 'application/json' : 'text/javascript';
    return route.fulfill({ body: readFileSync(new URL(path, vendored)), contentType });
  });
  await page.route(/raw\.githubusercontent\.com/, (route) => {
    const url = route.request().url();
    if (!pinned) return route.abort('internetdisconnected');
    return route.fulfill({ body: url.endsWith('op1_factory.json') ? pinned.catalog : pinned.profile });
  });
  await page.goto(`${process.env['XYF_E2E_URL']!}?sim=${simPort}`); // Install switches to Manage by itself
  await expect(page.locator('[data-fact=version]')).toHaveText(/^\d+\.\d+\.\d+$/, { timeout: 20_000 });
  await page.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name: 'Mods', exact: true }).click();
  return page;
}

test('says what to do when the catalog cannot be downloaded', async () => {
  test.skip(found.why !== null, `skipped: ${found.why}`);
  const page = await modsPage(() => null);
  await page.getByRole('button', { name: 'Show the catalog' }).click();
  await expect(page.locator('#open-xy-status')).toHaveText(
    /could not be downloaded \(no answer from open-xy\.com\)\. Check the internet connection and try again/,
  );
  await page.close();
});

test("says what to do when Open-XY's builder cannot be loaded", async () => {
  test.skip(found.why !== null, `skipped: ${found.why}`);
  const page = await modsPage(() => Buffer.from('{"version": "0.0.7"}'), false);
  await page.getByRole('button', { name: 'Show the catalog' }).click();
  await expect(page.locator('#open-xy-status')).toHaveText(/Open-XY 0\.0\.7's builder could not be loaded\)\. Check/);
  await page.close();
});

test('offers a newer kit at once and sends two mods and an extension', async () => {
  test.skip(found.why !== null, `skipped: ${found.why}`);
  test.skip(!existsSync(kitPath), 'skipped: the published 0.0.7 kit is not in the private fixtures');
  const newer = readFileSync(kitPath);
  const kit = JSON.parse(gunzipSync(newer).toString('utf8'));
  const late = kit.machines.pop();
  kit.objects = kit.objects.filter((o: { owner?: string }) => o.owner !== late.id);
  let served = Buffer.from(JSON.stringify(kit)); // served decoded, as a host that compresses it in transit does
  const page = await modsPage(() => served);
  const status = page.locator('#open-xy-status');
  await page.getByRole('button', { name: 'Show the catalog' }).click();
  await expect(status).toHaveText('Open-XY 0.0.7: 100 mods.', { timeout: 10_000 });
  await expect(page.locator(`[data-mod=${late.id}]`)).toHaveCount(0);
  served = newer;
  await page.getByRole('button', { name: 'Check for new mods' }).click();
  await expect(status).toHaveText('Open-XY 0.0.7: 101 mods.', { timeout: 10_000 });
  await expect(page.locator(`#open-xy [data-mod=${late.id}]`)).toHaveText(late.name);
  for (const mod of ['fm2op', 'chords', 'ext-capture']) await page.locator(`#open-xy [data-mod=${mod}]`).click();
  await page.getByRole('button', { name: 'Send 3 and save' }).click();
  // Sent, then saved as the Mods menu saves (config.set): the client's simulator tests cover the save itself.
  await expect(page.locator('#open-xy-result')).toHaveText(/^Sent 4 packages \(.+\)\. Saving the selection/, {
    timeout: 10_000,
  });
  for (const mod of ['fm2op', 'chords', 'ext-capture']) await expect(page.locator(`tr[data-mod=${mod}]`)).toBeVisible();
  await page.close();
});

test('uses the pinned 0.0.7 kit when the live one is unavailable, and says so', async () => {
  test.skip(found.why !== null, `skipped: ${found.why}`);
  test.skip(!existsSync(kitPath), 'skipped: the published 0.0.7 kit is not in the private fixtures');
  test.setTimeout(60_000); // the 27 MB kit stripped first, then split
  const published = JSON.parse(gunzipSync(readFileSync(kitPath)).toString('utf8'));
  const work = mkdtempSync(join(tmpdir(), 'xyf-pinned-'));
  try {
    const stripped = join(work, 'kit.json.gz');
    const root = new URL('../../', import.meta.url).pathname;
    execFileSync('uv', ['run', 'scripts/web_open_xy.py', 'strip', kitPath, stripped], { cwd: root });
    const pinned = {
      kit: readFileSync(stripped),
      profile: Buffer.from(published.profileSource, 'base64'),
      catalog: Buffer.from(published.stockCatalog, 'base64'),
    };
    const page = await modsPage(() => null, true, pinned);
    await page.getByRole('button', { name: 'Show the catalog' }).click();
    await expect(page.locator('#open-xy-status')).toHaveText('Open-XY 0.0.7 (pinned): 101 mods.', { timeout: 20_000 });
    await page.close();
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
});
