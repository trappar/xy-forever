// The demo page driven in the system Chrome by Playwright, with no human step: Connect, HELLO, list
// /yaffs2/xy-forever, upload a scratch file, download and compare it, delete it, list mod packages,
// disconnect. PASS/FAIL per step, result.json and a screenshot per step in --out; exit status 1 on any
// failure. Never boots, never changes the default, never builds; the scratch file is deleted even
// after a failure (a second connection if the first is gone).
//
//   node scripts/device-browser-test.ts --out DIR --sim PORT     the simulator through the dev server's relay
//   node scripts/device-browser-test.ts --out DIR --serial       the OP-XY over Web Serial (see below)
//   node scripts/device-browser-test.ts --grant                  once, headed: the person picks the OP-XY in Chrome's chooser;
//                                                                waits for the grant, checks it persists (a new launch connects
//                                                                without a chooser), exits; --serial then needs no human
//   options: --headed (show the window), --fail-after-upload (a failing step after the upload: shows the cleanup), --profile DIR (serial: the Chrome profile; default
//            <private>/xy-forever/chrome-device-test; <private> is XYF_PRIVATE, else ../private next to the repository), --vite-port N (default: 5173 with --serial, a grant being per origin; else a free one)
//
// Web Serial and the port chooser (checked with Chrome 157.0.8103.51 and Playwright 1.65.1, 2026-11-15):
// Playwright's grantPermissions refuses 'serial' ("Unknown permission"); CDP Browser.grantPermissions has
// no serial permission; the DevTools DeviceAccess domain enables but never sends deviceRequestPrompted for
// navigator.serial.requestPort (it covers Bluetooth only), headless or headed; Chrome has no
// auto-select switch for serial ports (its switches: tab/screen/desktop capture and certificates only).
// So --serial uses a persistent Chrome profile: the page connects without the chooser when that profile
// holds a grant for the dev server's origin (getPorts(), src/webserial.ts grantedPorts). Without one the
// grant step fails at once with the reason; it never waits for a click. A grant comes from --grant (one
// pick by a person in that same profile, on the same origin) or from the enterprise policy SerialAllowUsbDevicesForUrls (vendor/product ids of USB_FILTERS, origin
// http://127.0.0.1:<vite-port>), which needs a managed preference on macOS (an admin step).
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import type { Browser, BrowserContext, Page } from 'playwright';

const here = fileURLToPath(new URL('..', import.meta.url));
const args = process.argv.slice(2);
const opt = (name: string): string | undefined => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const out = opt('--out');
const sim = opt('--sim');
const grant = args.includes('--grant');
const serial = args.includes('--serial') || grant;
if ((!out && !grant) || (!sim && !serial) || (sim && serial) || (grant && args.includes('--serial'))) {
  console.error('usage: node scripts/device-browser-test.ts (--out DIR (--sim PORT | --serial) | --grant) [--headed] [--profile DIR] [--vite-port N]');
  process.exit(2);
}
if (out) mkdirSync(out, { recursive: true });
const privateRoot = resolve(process.env['XYF_PRIVATE'] ?? join(here, '../../../private'));   // as xy_forever/paths.py
const profile = resolve(opt('--profile') ?? join(privateRoot, 'xy-forever/chrome-device-test'));
const DIR = '/yaffs2/xy-forever';
const scratchName = `xyf-browser-test-${Date.now()}.txt`;
const scratchData = `XY Forever browser test scratch file, ${new Date().toISOString()}\n`;
const STEP_MS = 30_000;

interface Step { name: string; ok: boolean; ms: number; detail: string; screenshot?: string }
const steps: Step[] = [];
let page: Page | null = null;

async function step(name: string, work: () => Promise<string>): Promise<boolean> {
  const start = Date.now();
  let ok = false, detail = '';
  try {
    detail = await Promise.race([work(), new Promise<string>((_, no) => setTimeout(() => no(new Error(`no result in ${STEP_MS / 1000} s`)), STEP_MS))]);
    ok = true;
  } catch (error) {
    detail = (error as Error).message.split('\n')[0] ?? String(error);
  }
  const s: Step = { name, ok, ms: Date.now() - start, detail };
  if (page) {
    const file = `${String(steps.length + 1).padStart(2, '0')}-${name.replace(/[^a-z0-9]+/gi, '-')}.png`;
    try { await page.screenshot({ path: join(out!, file), fullPage: true }); s.screenshot = file; } catch { /* the page is gone */ }
  }
  steps.push(s);
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}: ${detail}`);
  return ok;
}

function freePort(): Promise<number> {
  return new Promise((done, fail) => {
    const srv = createServer();
    srv.once('error', fail);
    srv.listen(0, '127.0.0.1', () => { const p = (srv.address() as { port: number }).port; srv.close(() => done(p)); });
  });
}

/** npm run dev's server (vite demo) on 127.0.0.1:port; resolves once it answers. */
async function startVite(port: number): Promise<ChildProcess> {
  const vite = spawn(process.execPath, [join(here, 'node_modules/vite/bin/vite.js'), 'demo', '--port', String(port), '--strictPort'],
    { cwd: here, stdio: ['ignore', 'pipe', 'pipe'] });
  let log = '';
  vite.stdout!.on('data', (d: Buffer) => { log += d.toString(); });
  vite.stderr!.on('data', (d: Buffer) => { log += d.toString(); });
  for (const until = Date.now() + 30_000; Date.now() < until;) {
    if (vite.exitCode !== null) throw new Error(`the dev server stopped: ${log.trim()}`);
    try { if ((await fetch(`http://127.0.0.1:${port}/`)).ok) return vite; } catch { /* not yet */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  vite.kill();
  throw new Error(`the dev server did not answer in 30 s: ${log.trim()}`);
}

const text = async (id: string): Promise<string> => (await page!.locator(`#${id}`).first().textContent()) ?? '';   // two #build: the first is HELLO's

/** Waits for the page's current action to end; throws with the page's error line. */
async function settled(): Promise<void> {
  await page!.waitForFunction(() => document.getElementById('status')!.textContent !== 'working', null, { timeout: STEP_MS });
  const error = await text('error');
  if (error) throw new Error(error);
}

/** The Files panel's rows: name, kind, size. */
const rows = (): Promise<{ name: string; kind: string; size: number }[]> => page!.$$eval('#files-list tbody tr',
  (trs) => trs.map((tr) => ({ name: (tr as HTMLElement).dataset['name'] ?? '', kind: (tr as HTMLElement).dataset['kind'] ?? '', size: Number((tr as HTMLElement).dataset['size']) })));

async function connect(): Promise<string> {
  await page!.click('#connect');
  await settled();
  if ((await text('status')) !== 'connected') throw new Error(`status ${await text('status')}`);
  return `version ${await text('version')}, protocol ${await text('proto')}, mode ${await text('mode')}`;
}

async function openDir(): Promise<string> {
  await page!.locator('#files-list button.open', { hasText: /^xy-forever\/$/ }).click();
  await settled();
  const crumbs = await text('crumbs');
  if (crumbs.replace(/\s/g, '') !== 'yaffs2/xy-forever') throw new Error(`in ${crumbs}`);
  return (await rows()).map((r) => r.kind === 'folder' ? `${r.name}/` : r.name).join(' ') || '(empty)';
}

async function rowButton(name: string, label: string): Promise<void> {
  await page!.locator(`#files-list tbody tr[data-name="${name}"] button`, { hasText: label }).click();
}

/** Deletes the scratch file through the Files panel (connecting again when needed); true when it is gone. */
async function cleanup(): Promise<string> {
  if (!(await page!.locator('#disconnect').isEnabled())) {
    await page!.reload();
    await connect();
  }
  if ((await text('crumbs')).replace(/\s/g, '') !== 'yaffs2/xy-forever') {
    await page!.locator('#crumbs button', { hasText: 'yaffs2' }).click().catch(() => undefined);
    await settled();
    await openDir();
  } else {
    await page!.click('#files-refresh');
    await settled();
  }
  if (!(await rows()).some((r) => r.name === scratchName)) return 'nothing left';
  await rowButton(scratchName, 'Delete');
  await settled();
  if ((await rows()).some((r) => r.name === scratchName)) throw new Error(`${scratchName} is still there`);
  return `deleted ${DIR}/${scratchName}`;
}

/** XY Forever ports the page's origin holds a grant for (getPorts, no chooser), and all ports. */
async function grantedHere(p: Page): Promise<{ mine: number; all: number }> {
  const ports = await p.evaluate(async () => {
    const s = (navigator as Navigator & { serial?: { getPorts(): Promise<{ getInfo(): { usbVendorId?: number; usbProductId?: number } }[]> } }).serial;
    if (!s) throw new Error('this Chrome has no Web Serial');
    return (await s.getPorts()).map((x) => x.getInfo());
  });
  const { USB_FILTERS } = await import('../src/v1.ts');
  const ours = (x: { usbVendorId?: number; usbProductId?: number }) =>
    USB_FILTERS.some((f) => x.usbVendorId === f.usbVendorId && x.usbProductId === f.usbProductId);
  return { mine: ports.filter(ours).length, all: ports.length };
}

const GRANT_MS = 300_000;
const PICK = "Click Connect, pick the OP-XY in Chrome's list once, then wait: this window closes by itself.";

/**
 * --grant: the persistent profile headed on the fixed origin, one line telling the person to pick the OP-XY
 * (the demo's Connect opens the chooser), waits up to GRANT_MS for the grant, then closes Chrome, launches the
 * profile again headless and connects without a chooser (HELLO, then disconnect): the grant persisted.
 */
async function grantMode(): Promise<number> {
  const port = opt('--vite-port') ? Number(opt('--vite-port')) : 5173;
  const origin = `http://127.0.0.1:${port}/`;
  const server = await startVite(port);
  let ctx: BrowserContext | null = null;
  try {
    ctx = await chromium.launchPersistentContext(profile, { channel: 'chrome', headless: false });
    let p = ctx.pages()[0] ?? await ctx.newPage();
    await p.goto(origin);
    if ((await grantedHere(p)).mine === 0) {
      await p.evaluate((line) => {
        const bar = Object.assign(document.createElement('div'), { id: 'grant-line', textContent: line });
        bar.style.cssText = 'position:sticky;top:0;z-index:9;padding:10px;background:#ffd84d;color:#000;font:bold 16px sans-serif';
        document.body.prepend(bar);
      }, PICK);
      console.log(PICK);
      const until = Date.now() + GRANT_MS;
      while ((await grantedHere(p)).mine === 0) {
        if (Date.now() > until) throw new Error(`no OP-XY picked in ${GRANT_MS / 1000} s`);
        await new Promise((r) => setTimeout(r, 500));
      }
      console.log(`granted: ${origin} in profile ${profile}`);
    } else {
      console.log(`already granted: ${origin} in profile ${profile}`);
    }
    await ctx.close();                                             // the grant must outlive this Chrome
    ctx = await chromium.launchPersistentContext(profile, { channel: 'chrome', headless: true });
    page = p = ctx.pages()[0] ?? await ctx.newPage();
    await p.goto(origin);
    if ((await grantedHere(p)).mine === 0) throw new Error('the grant did not persist in the profile');
    const hello = await connect();                                 // no chooser: grantedPorts() finds it
    await p.click('#disconnect');
    await settled();
    console.log(`PASS grant persists: connected without a chooser (${hello})`);
    return 0;
  } catch (error) {
    console.log(`FAIL grant: ${(error as Error).message.split('\n')[0]}`);
    return 1;
  } finally {
    await ctx?.close().catch(() => undefined);
    server.kill();
  }
}

if (grant) process.exit(await grantMode());

let vite: ChildProcess | null = null;
let context: BrowserContext | null = null;
let browser: Browser | null = null;
let uploaded = false, deleted = false;
const started = new Date().toISOString();
try {
  const port = opt('--vite-port') ? Number(opt('--vite-port')) : serial ? 5173 : await freePort();   // a grant is per origin: serial keeps one
  await step('dev server', async () => { vite = await startVite(port); return `http://127.0.0.1:${port}/`; });
  if (vite) {
    const launch = { channel: 'chrome', headless: !args.includes('--headed'), acceptDownloads: true };
    if (serial) context = await chromium.launchPersistentContext(profile, launch);
    else context = await (browser = await chromium.launch(launch)).newContext({ acceptDownloads: true });
    page = context.pages()[0] ?? await context.newPage();
    page.on('dialog', (d) => {                                   // confirm(): yes only for the scratch file
      const ok = d.type() === 'confirm' && d.message().includes(scratchName);
      void (ok ? d.accept() : d.dismiss());
    });
    await page.goto(`http://127.0.0.1:${port}/${sim ? `?sim=${encodeURIComponent(sim)}` : ''}`);
    let ready = true;
    if (serial) {
      ready = await step('serial port granted', async () => {
        const { mine, all } = await grantedHere(page!);
        if (mine === 0) throw new Error(`no granted XY Forever port in profile ${profile} for http://127.0.0.1:${port} (run --grant once: Chrome's chooser cannot be answered by automation)`);
        return `${mine} granted port(s), ${all} in all`;
      });
    }
    if (ready && await step('connect and HELLO', connect)) {
      if (await step(`list ${DIR}`, openDir)) {
        const ok = await step('upload scratch file', async () => {
          await page!.setInputFiles('#files-upload', { name: scratchName, mimeType: 'text/plain', buffer: Buffer.from(scratchData) });
          uploaded = true;
          await settled();
          const row = (await rows()).find((r) => r.name === scratchName);
          if (!row) throw new Error(`${scratchName} not listed after the upload`);
          if (row.size !== Buffer.byteLength(scratchData)) throw new Error(`listed with ${row.size} bytes, sent ${Buffer.byteLength(scratchData)}`);
          return `${DIR}/${scratchName}, ${row.size} bytes`;
        });
        if (ok && args.includes('--fail-after-upload')) await step('forced failure (--fail-after-upload)', async () => { throw new Error('as asked'); });
        else if (ok) {
          await step('download and compare', async () => {
            const [download] = await Promise.all([page!.waitForEvent('download', { timeout: STEP_MS }), rowButton(scratchName, 'Download')]);
            await settled();
            const file = join(out!, 'downloaded-scratch.txt');
            await download.saveAs(file);
            const got = readFileSync(file, 'utf8');
            if (got !== scratchData) throw new Error(`downloaded ${got.length} bytes that differ from the ${scratchData.length} sent`);
            return `${got.length} bytes, identical`;
          });
          await step('delete scratch file', async () => {
            await rowButton(scratchName, 'Delete');
            await settled();
            if ((await rows()).some((r) => r.name === scratchName)) throw new Error('still listed');
            deleted = true;
            return `${DIR}/${scratchName} gone`;
          });
        }
      }
      await step('list mod packages', async () => {
        await page!.click('#mods-refresh');
        await settled();
        const generations = await page!.$$eval('#mods-list tr.generation', (trs) => trs.map((t) => t.textContent ?? ''));
        const saved = await text('config-saved'), content = await text('config-content');
        return `${generations.length ? generations.join('; ') : (await text('mods-list')).trim()}; ${saved} ${content}`;
      });
    }
  }
} finally {
  if (uploaded && !deleted && page) await step('cleanup: delete scratch file', cleanup);
  if (page && await page.locator('#disconnect').isEnabled().catch(() => false)) {
    await step('disconnect', async () => { await page!.click('#disconnect'); await settled(); return await text('status'); });
  }
  await context?.close().catch(() => undefined);
  await (browser as Browser | null)?.close().catch(() => undefined);
  (vite as ChildProcess | null)?.kill();
  const pass = steps.length > 0 && steps.every((s) => s.ok);
  writeFileSync(join(out!, 'result.json'), JSON.stringify({
    pass, started, finished: new Date().toISOString(), transport: sim ? `sim:${sim}` : 'webserial', scratch: `${DIR}/${scratchName}`, steps,
  }, null, 2) + '\n');
  console.log(`${pass ? 'PASS' : 'FAIL'}: ${steps.filter((s) => s.ok).length}/${steps.length} steps; ${join(out!, 'result.json')}`);
  process.exitCode = pass ? 0 : 1;
}
