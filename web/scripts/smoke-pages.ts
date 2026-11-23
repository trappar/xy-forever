// Smoke test a production build served by `wrangler pages dev web/dist`.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { chromium } from '@playwright/test';
import { staticPaths } from '../src/routing.ts';

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

  for (const path of ['', ...staticPaths()]) {
    assert((await page.goto(site + path))?.ok(), `Direct navigation must serve ${path || 'the home page'}`);
    const stage = path.split('/')[1] ?? 'boot';
    // Fresh connections are required to show Device, Mods and Files.
    const shown = path.startsWith('install')
      ? `[data-stage="${stage}"]:visible`
      : 'h1:text-is("Connect your OP-XY"):visible';
    await page.locator(shown).waitFor();
    assert((await page.reload())?.ok(), `Reload must serve ${path || 'the home page'}`);
    await page.locator(shown).waitFor();
  }
  for (const path of ['unknown-page/', 'release/missing.ldr']) {
    assert.equal((await page.request.get(site + path)).status(), 404, `Serve a real 404 for ${path}`);
  }
  assert((await page.request.get(site + 'LICENSES.txt')).ok(), 'Serve the notices with the web assets');

  await page.goto(site + 'connect/?source=smoke');
  const connect = page.locator('a[data-page=connect]');
  const install = page.locator('a[data-page=install]');
  assert.equal(await connect.getAttribute('href'), '/connect/?source=smoke'); // the query is kept
  assert.equal(await install.getAttribute('href'), '/install/?source=smoke');
  await page.getByRole('link', { name: 'Install XY Forever', exact: true }).click();
  await page.waitForURL(site + 'install/boot/?source=smoke');
  await page.goBack();
  await page.waitForURL(site + 'connect/?source=smoke');
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
  assert.equal(bytes.length, pin.bytes);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), pin.sha256);
  console.log(`Cloudflare Pages smoke passed for release ${pin.version} (${pin.image}).`);
} finally {
  await browser.close();
}
