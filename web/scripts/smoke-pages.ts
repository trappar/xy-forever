// Serve the built files as directory indexes, with no app fallback or development relay.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { once } from 'node:events';
import { readFile, stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import { extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';
import type { Browser } from '@playwright/test';
import { staticPaths } from '../src/routing.ts';

const PAGES_BASE = '/xy-forever/';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const types: Record<string, string> = { '.html': 'text/html', '.txt': 'text/plain; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript' };
const server = createServer(async (req, res) => {
  const url = new URL(req.url!, 'http://localhost');
  if (!url.pathname.startsWith(PAGES_BASE)) { res.writeHead(404).end(); return; }
  let file = join(dist, url.pathname.slice(PAGES_BASE.length));
  try {
    if ((await stat(file)).isDirectory()) {
      if (!url.pathname.endsWith('/')) {
        res.writeHead(308, { Location: url.pathname + '/' + url.search }).end();
        return;
      }
      file = join(file, 'index.html');
    }
    const body = await readFile(file);
    res.writeHead(200, { 'Content-Type': types[extname(file)] ?? 'application/octet-stream' }).end(body);
  } catch {
    res.writeHead(404).end();
  }
});

let browser: Browser | undefined;
try {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const address = server.address();
  assert(address && typeof address !== 'string');
  const origin = `http://127.0.0.1:${address.port}`;
  const site = origin + PAGES_BASE;

  browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--disable-blink-features=Serial,WebUSB'] });
  const page = await browser.newPage({ acceptDownloads: true });
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'requestMIDIAccess', { value: undefined });
    // Reloading a refusal page requires the refusal from the current session.
    if (!sessionStorage.getItem('xyf.install')) {
      sessionStorage.setItem('xyf.install', JSON.stringify({
        computer: 'windows', stop: { kind: 'reinstall', title: 'Reinstall OS 1.2.1', detail: 'The original OS file is missing.' },
      }));
    }
  });

  for (const path of ['', ...staticPaths()]) {
    assert((await page.goto(site + path))?.ok(), `Direct navigation must serve ${path || 'the home page'}`);
    const stage = path.split('/')[1] ?? 'boot';
    // Fresh connections are required to show Device, Mods and Files.
    const shown = path.startsWith('install') ? `[data-stage="${stage}"]:visible` : 'h1:text-is("Connect your OP-XY"):visible';
    await page.locator(shown).waitFor();
    assert((await page.reload())?.ok(), `Reload must serve ${path || 'the home page'}`);
    await page.locator(shown).waitFor();
  }
  assert.equal((await page.request.get(site + 'unknown-page/')).status(), 404);
  assert((await page.request.get(site + 'LICENSES.txt')).ok(), 'Serve the notices with the web assets');

  await page.goto(site + 'connect/?source=smoke');
  const connect = page.locator('a[data-page=connect]');
  const install = page.locator('a[data-page=install]');
  assert.equal(await connect.getAttribute('href'), PAGES_BASE + 'connect/?source=smoke');   // the query is kept
  assert.equal(await install.getAttribute('href'), PAGES_BASE + 'install/?source=smoke');
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
  console.log(`Pages smoke passed for ${PAGES_BASE} and release ${pin.version} (${pin.image}).`);
} finally {
  await browser?.close();
  await new Promise<void>((resolve) => server.close(() => resolve()));
}
