// Smoke test a production build served by `wrangler pages dev web/dist`.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from '@playwright/test';
import { staticPaths } from '../src/routing.ts';
import type { Core } from '../src/kit/core.ts';
import { PINNED_VERSION, pinnedKit } from '../src/kit/pinned.ts';

const site = 'http://127.0.0.1:8788/';
const browser = await chromium.launch({
  channel: 'chrome',
  headless: true,
  args: ['--disable-blink-features=Serial,WebUSB'],
});
try {
  const page = await browser.newPage({ acceptDownloads: true });
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'requestMIDIAccess', { value: undefined });
    // Reloading a refusal page requires the refusal from the current session.
    if (!sessionStorage.getItem('xyf.install')) {
      sessionStorage.setItem(
        'xyf.install',
        JSON.stringify({
          computer: 'windows',
          stop: { kind: 'reinstall', title: 'Reinstall OS 1.2.1', detail: 'The original OS file is missing.' },
        }),
      );
    }
  });

  // The site's two sections (#316): / opens Install; the earlier pages' links lead to their Manage pages.
  const MOVED: Record<string, string> = {
    '': 'install/boot/',
    install: 'install/boot/',
    connect: 'manage/',
    device: 'manage/',
    mods: 'manage/mods/',
    files: 'manage/files/',
  };
  for (const path of ['', ...staticPaths()]) {
    assert((await page.goto(site + path))?.ok(), `Direct navigation must serve ${path || 'the home page'}`);
    const at = MOVED[path] ?? `${path}/`;
    await page.waitForURL(site + at);
    // Device, Mods and Files show Connect in their place without a connection.
    const shown = at.startsWith('install/')
      ? `[data-stage="${at.split('/')[1]}"]:visible`
      : 'h1:text-is("Connect your OP-XY"):visible';
    await page.locator(shown).waitFor();
    assert((await page.reload())?.ok(), `Reload must serve ${path || 'the home page'}`);
    await page.waitForURL(site + at);
    await page.locator(shown).waitFor();
  }
  for (const path of ['unknown-page/', 'release/missing.ldr']) {
    assert.equal((await page.request.get(site + path)).status(), 404, `Serve a real 404 for ${path}`);
  }
  assert((await page.request.get(site + 'LICENSES.txt')).ok(), 'Serve the notices with the web assets');

  await page.goto(site + 'files/?source=smoke');
  await page.waitForURL(site + 'manage/files/?source=smoke'); // an earlier link keeps its query
  const manage = page.locator('a[data-page=manage]');
  const install = page.locator('a[data-page=install]');
  assert.equal(await manage.getAttribute('href'), '/manage/?source=smoke'); // the query is kept
  assert.equal(await install.getAttribute('href'), '/install/?source=smoke');
  assert.equal(await page.locator('a[data-page="manage/mods"]').getAttribute('href'), '/manage/mods/?source=smoke');
  await page.getByRole('link', { name: 'Install XY Forever', exact: true }).click();
  await page.waitForURL(site + 'install/boot/?source=smoke');
  await page.goBack();
  await page.waitForURL(site + 'manage/files/?source=smoke');
  await page.goForward();
  await page.waitForURL(site + 'install/boot/?source=smoke');

  const pin = JSON.parse(await readFile(new URL('../src/install/release.json', import.meta.url), 'utf8'));
  const releaseUrl = site + 'release/os.ldr';
  await page.goto(site + 'install/transfer/');
  const [, file] = await Promise.all([
    page.waitForRequest((request) => request.url() === releaseUrl),
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Download os.ldr', exact: true }).click(),
  ]);
  assert.equal(file.suggestedFilename(), 'os.ldr');
  const bytes = await readFile(await file.path());
  const digest = (data: Buffer): string => createHash('sha256').update(data).digest('hex');
  assert.equal(bytes.length, pin.bytes);
  assert.equal(digest(bytes), pin.sha256);
  if (existsSync(new URL('../dist/release/release.json', import.meta.url)))
    assert.deepEqual(await (await page.request.get(site + 'release/release.json')).json(), pin);

  // The canary (scripts/web_canary.py stage): when this deploy has one, the site serves it and Install offers it.
  const staged = new URL('../dist/canary/release.json', import.meta.url);
  let said = 'no canary';
  if (existsSync(staged)) {
    const canary = JSON.parse(await readFile(staged, 'utf8'));
    assert.deepEqual(await (await page.request.get(site + 'canary/release.json')).json(), canary);
    const image = await (await page.request.get(site + 'canary/os.ldr')).body();
    assert.equal(image.length, canary.bytes);
    assert.equal(digest(image), canary.sha256);
    await page.evaluate(() => sessionStorage.clear());
    await page.goto(site + 'install/boot/');
    await page.getByRole('radio', { name: /Canary/ }).waitFor();
    said = `canary ${canary.commit.slice(0, 7)} (${canary.image})`;
  } else {
    assert.equal((await page.request.get(site + 'canary/release.json')).status(), 404, 'No canary: a real 404');
  }
  // #183: the Mods page's pinned Open-XY kit as the site serves it: refilled from Open-XY's repository, it passes the
  // builder of its release (served here too) and finishes a machine with the stock catalog the builder checks.
  const base = `${site}open-xy/${PINNED_VERSION}/`;
  const get = async (url: string): Promise<Uint8Array> => {
    const response = await page.request.get(url);
    assert(response.ok(), `${url} must be served`);
    return new Uint8Array(await response.body());
  };
  for (const name of ['LICENSE', 'NOTICES.txt', 'SOURCES.txt']) await get(base + name);
  const kit = await pinnedKit(base, get);
  const modules = await mkdtemp(join(tmpdir(), 'xyf-open-xy-'));
  let machines = 0;
  try {
    for (const name of ['builder/core.mjs', 'sdk/browser/graphics.mjs', 'tooling/default_preset.json']) {
      await mkdir(dirname(join(modules, name)), { recursive: true });
      await writeFile(join(modules, name), await get(base + name));
    }
    const core = (await import(pathToFileURL(join(modules, 'builder/core.mjs')).href)) as Core;
    const opened = await core.openKit(kit);
    const first = opened.machines[0]!.id;
    const files = await core.buildAssets(
      opened,
      [first],
      core.decode64(opened.stockCatalog),
      core.assignNumbers(opened, [first], null),
    );
    assert(files['content/op1_factory.json'], 'The pinned kit finishes a machine');
    machines = opened.machines.length;
  } finally {
    await rm(modules, { recursive: true, force: true });
  }
  said += `, the pinned Open-XY ${PINNED_VERSION} kit (${machines} machines)`;
  console.log(`Cloudflare Pages smoke passed for release ${pin.version} (${pin.image}) and ${said}.`);
} finally {
  await browser.close();
}
