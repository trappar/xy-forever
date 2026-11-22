// The Install page's end (#178) in the system Chrome (headless) against the device simulator, which runs XY Forever's own
// code and command rules: after Track 5, XY Forever sets itself up on the device (it adopts the stock os.ldr as library
// entry opxy-121, makes it the default, boots it), and the page hands the owner to the Mods page once it finds it.
// te-boot and the dev menu's MTP are the fakes of test/fake-devices.ts; Web Serial is the fake navigator.serial of the
// client's browser tests (clients/ts/test/browser/fake-serial.ts: Chrome's 255-byte default receive buffer, the chooser,
// granted ports, a device that goes away and comes back), reaching the simulator through the relay. Each test starts
// a fresh simulator: a stock OP-XY just after Track 5 (`--fresh`). Skipped with the reason when Chrome or the
// simulator cannot run here.
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, expect, test } from '@playwright/test';
import type { Browser, Page, Locator } from '@playwright/test';
import { createServer } from 'vite';
import type { ViteDevServer } from 'vite';
import { startRelay } from '../node_modules/xy-forever-client/test/relay.ts';
import type { Relay } from '../node_modules/xy-forever-client/test/relay.ts';
import { control, findSimulator, startAll } from '../node_modules/xy-forever-client/test/simulators.ts';
import type { SimInfo } from '../node_modules/xy-forever-client/test/simulators.ts';
import { Client, tcpOpener } from '../node_modules/xy-forever-client/src/node.ts';
import { protocol as wire } from '../node_modules/xy-forever-client/src/index.ts';

const IMAGE = Buffer.from(Uint8Array.from({ length: 70_000 }, (_, i) => (i * 31 + 7) & 0xff));
const OK_121 = 'mode:bootloader;product:OP-XY;serial:X1;bl_version:1000.0.20+0;os_version:1.2.1+0';
const FAKE_SERIAL = fileURLToPath(new URL('../node_modules/xy-forever-client/test/browser/fake-serial.ts', import.meta.url));
const ENTRY = 'xy-forever/firmware/opxy-121';
// A random base (simulator, control, relay at +10) and state folder: another checkout may run this test at the same time.
const BASE = parseInt(process.env['XYF_WEB_SIM_PORT'] ?? String(22000 + 20 * Math.floor(Math.random() * 400)), 10);

const nav = (page: Page, name: string): Locator => page.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name, exact: true });

const found = findSimulator();
let why: string | null = found.why;
let browser: Browser | undefined;
if (!why) {
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--disable-blink-features=Serial,WebUSB'] });
  } catch (error) {
    why = `the system Chrome cannot start (${(error as Error).message.split('\n')[0]})`;
  }
}
if (why) console.warn(`\ninstall simulator test skipped: ${why}\n`);

let server: ViteDevServer | undefined;
// The pin's image. Only a setup that never starts compares it (finish.ts), so any name will do; the uninstall test pins its build.
let image = 'test-pin';
let running: { sim: SimInfo; relay: Relay; stop: () => Promise<void> } | null = null;

test.describe.configure({ timeout: 240_000 });

test.beforeAll(async () => {
  if (why) return;
  server = await createServer({ configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)), server: { port: 0, host: '127.0.0.1' }, logLevel: 'error' });
  await server.listen();
});
test.afterEach(async () => { await running?.stop(); running = null; });
test.afterAll(async () => { await browser?.close(); await server?.close(); if (!why) rmSync(join(found.sessions!, `web-install-${BASE}`), { recursive: true, force: true }); });

/** A fresh simulator: a stock OP-XY just after Track 5 (stock: its updater's os.ldr; plain: no PLAY at power-on). */
async function powerOn(stock: 'stock' | 'missing', plain: boolean): Promise<SimInfo> {
  const state = join(found.sessions!, `web-install-${BASE}`);
  const started = await startAll(state, BASE, [['sim', 0, ['--fresh', stock, ...(plain ? ['--plain'] : [])]]]);
  const sim = started.infos['sim']!;
  const relay = await startRelay({ port: BASE + 10, target: sim.port, control: sim.control });
  running = { sim, relay, stop: async () => { await relay.close(); await started.stopAll(); } };
  return sim;
}

const status = async (sim: SimInfo): Promise<Record<string, unknown>> => control(sim.control, 'status');
async function until(what: string, cond: () => Promise<boolean>, ms = 120_000): Promise<void> {
  const deadline = Date.now() + ms;
  while (!(await cond())) {
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await new Promise((ok) => setTimeout(ok, 100));
  }
}
const inOs = async (sim: SimInfo): Promise<boolean> => { const s = await status(sim); return s['a'] === 'os' && s['b'] === 'os'; };
const listening = async (sim: SimInfo): Promise<boolean> => (await status(sim))['listening'] === 1;

/** The app with fake te-boot, MTP and Web Serial (granted: this site was given the port before; no chooser needed). */
async function open(relay: string, granted: boolean, bytes = IMAGE, prepare?: (page: Page) => Promise<void>): Promise<Page> {
  const address = server!.httpServer!.address();
  const base = `http://127.0.0.1:${typeof address === 'object' && address ? address.port : 0}`;
  const pin = { schema: 1, version: '0.1.0', image, sha256: createHash('sha256').update(bytes).digest('hex'), bytes: bytes.length };
  const page = await browser!.newPage({ acceptDownloads: true });
  await page.route('**/src/install/release.json*', (route) => route.fulfill({ contentType: 'text/javascript', body: `export default ${JSON.stringify(pin)};` }));
  await page.route('**/release/os.ldr', (route) => route.fulfill({ contentType: 'application/octet-stream', body: bytes }));
  await prepare?.(page);
  await page.goto(`${base}/connect/`);
  await page.addScriptTag({ type: 'module', content: `
    import { FakeMidi, FakeUsb } from '/test/fake-devices.ts';
    import { FakeSerial } from '/@fs${FAKE_SERIAL}';
    const fields = Object.fromEntries(${JSON.stringify(OK_121)}.split(';').map((kv) => kv.split(':')));
    const midi = new FakeMidi(fields), usb = new FakeUsb({ 'upgrade/firmware/blackfin/bin/os.ldr': new Uint8Array(2373336) }, 64 * 1024 * 1024);   // the stock size
    const serial = new FakeSerial(${JSON.stringify(relay)}, { identity: { usbVendorId: 0x2367, usbProductId: 0x0021 }, granted: ${granted} });
    Object.defineProperty(navigator, 'requestMIDIAccess', { value: midi.requestMIDIAccess, configurable: true });
    Object.defineProperty(navigator, 'usb', { value: usb, configurable: true });
    serial.install();
    window.fakes = { midi, usb, serial };
    document.body.dataset.fakes = 'ready';` });
  await page.waitForSelector('body[data-fakes=ready]');
  await nav(page, 'Install').click();
  return page;
}

const stage = (page: Page): Promise<string | null> => page.locator('[data-stage]').getAttribute('data-stage');
const press = (page: Page, name: string): Promise<void> => page.getByRole('button', { name, exact: true }).click();

/** te-boot to Track 5's "firmware written ok!": the last stage, "Switch your OP–XY off, then on." */
async function toStart(page: Page, computer: 'Mac' | 'Windows'): Promise<void> {
  await page.getByRole('button', { name: /Check my OP/ }).click();
  await expect.poll(() => stage(page), { timeout: 15_000 }).toBe('menu');
  for (const name of ['Yes, I see it', computer, 'Dev menu is open', 'I see "mtp mode active"']) await press(page, name);
  if (computer === 'Mac') {
    await press(page, 'They’re closed · Continue');
    await press(page, 'Send to OP–XY');
    await expect.poll(() => page.getByRole('button', { name: 'File sent · Continue' }).isVisible(), { timeout: 30_000 }).toBe(true);
    await press(page, 'File sent · Continue');
  } else {
    await press(page, 'I see os.ldr, about 2,318 KB');
    await press(page, 'File copied · Continue');
  }
  for (const name of ['I’m back at the dev menu', 'I pressed track 5', 'I see "firmware written ok!"']) await press(page, name);
  expect(await stage(page)).toBe('start');
  expect(await page.locator('[data-stage]').textContent()).toContain('Switch your OP–XY off, then on.');
}

/** The page handed the owner to the app: the Mods page, connected, the installed notice once. */
async function handedOff(page: Page): Promise<void> {
  await expect.poll(() => new URL(page.url()).pathname, { timeout: 60_000 }).toBe('/mods/');
  expect(await page.getByTestId('installed').isVisible()).toBe(true);
  expect(await page.getByTestId('installed').textContent()).toContain('XY Forever is installed');
  expect(await page.getByTestId('link').textContent()).toMatch(/^Connected/);
  await page.getByRole('heading', { level: 1, name: 'Mods' }).waitFor({ state: 'visible' });
  await page.getByRole('button', { name: 'Dismiss' }).click();
  expect(await page.getByTestId('installed').isVisible()).toBe(false);
}

/** The entry XY Forever made on the device: the stock os.ldr, byte for byte, beside its manifest.json and files.sha256. */
function adopted(sim: SimInfo): void {
  const stock = readFileSync(found.fixtures['stock_os_ldr']!);
  expect(readFileSync(join(sim.root, ENTRY, 'os.ldr')).equals(stock)).toBe(true);
  expect(JSON.parse(readFileSync(join(sim.root, ENTRY, 'manifest.json'), 'utf8'))).toMatchObject({
    schema: 'xy-forever.package', schema_version: 1, type: 'firmware', name: 'OP-XY', version: '1.2.1', kind: 'official',
  });
  expect(readFileSync(join(sim.root, 'xy-forever/state-a.txt'), 'utf8')).toContain('default=opxy-121\n');
}

test('Mac: the first plain power-on sets up OS 1.2.1 on the device and starts it; PLAY then opens the app on Mods', async () => {
  test.skip(why !== null, 'skipped: see the warning above');
  const sim = await powerOn('stock', true);                             // the first plain power-on: the setup, then the OS
  await until('the OS, after the setup', () => inOs(sim));                // (before the page walks: it would find XY Forever mid-flow)
  const page = await open(running!.relay.url, false);
  await toStart(page, 'Mac');
  expect(await page.evaluate(() => (window as unknown as { fakes: { usb: { reads: string[] } } }).fakes.usb.reads)).toEqual(['os.ldr']);
  adopted(sim);
  await control(sim.control, 'power play');                              // the owner holds PLAY: the XY Forever menu
  await until('the menu', () => listening(sim));
  await press(page, 'Find my OP–XY');                                    // never granted on this computer: the chooser, from the click
  await handedOff(page);
  expect(await page.evaluate(() => (window as unknown as { fakes: { serial: { chooser: number; xyfPorts: { dropped: number }[] } } }).fakes.serial))
    .toMatchObject({ chooser: 1 });
  expect(await page.evaluate(() => (window as unknown as { fakes: { serial: { xyfPorts: { dropped: number }[] } } }).fakes.serial.xyfPorts.every((p) => p.dropped === 0))).toBe(true);
  await page.close();
});

test('Windows: copied by hand; the page finds XY Forever by itself after the setup (a port granted before)', async () => {
  test.skip(why !== null, 'skipped: see the warning above');
  const sim = await powerOn('stock', true);
  await until('the OS, after the setup', () => inOs(sim));                // before the page walks, as above
  const page = await open(running!.relay.url, true);
  await toStart(page, 'Windows');
  adopted(sim);
  expect(await stage(page)).toBe('start');                               // the stock OS has no serial port: nothing to find
  await control(sim.control, 'power play');
  await handedOff(page);                                                 // no click: the last stage looks every few seconds
  await page.close();
});

test('at any stage: XY Forever found while it sets up shows the setup, then hands off', async () => {
  test.skip(why !== null, 'skipped: see the warning above');
  const sim = await powerOn('stock', false);                            // PLAY at the first start: the menu stays after the setup
  await control(sim.control, 'set a_slow_ms 10');                        // slow enough to watch
  await until('the menu', () => listening(sim));
  const page = await open(running!.relay.url, true);                     // no click: the page looks on load
  await expect.poll(async () => (await page.locator('[data-stage] [role=status]').textContent().catch(() => '')) ?? '', { timeout: 60_000 })
    .toMatch(/adding OS 1\.2\.1|about to add OS 1\.2\.1/);
  expect(await stage(page)).toBe('start');                               // jumped ahead from the first stage
  await control(sim.control, 'set a_slow_ms 0');                         // seen: the rest at full speed
  await handedOff(page);
  adopted(sim);
  await page.close();
});

test('no look while the MTP copy runs; once it ends, XY Forever found mid-flow hands off', async () => {
  test.skip(why !== null, 'skipped: see the warning above');
  const sim = await powerOn('stock', true);                             // set up, then the OS: no serial port yet
  await until('the OS, after the setup', () => inOs(sim));
  const page = await open(running!.relay.url, true, IMAGE, (tab) => tab.clock.install());   // the looks' timers run on demand
  await page.getByRole('button', { name: /Check my OP/ }).click();
  await expect.poll(() => stage(page), { timeout: 15_000 }).toBe('menu');
  for (const name of ['Yes, I see it', 'Mac', 'Dev menu is open', 'I see "mtp mode active"', 'They’re closed · Continue']) await press(page, name);
  expect(await stage(page)).toBe('transfer');
  await control(sim.control, 'power play');                              // XY Forever's menu, with its serial port: no look yet
  await until('the menu', () => listening(sim));                         // (no stage change, no connect event)
  await page.evaluate(() => {                                            // the copy holds mid-session until released
    const w = window as unknown as { fakes: { usb: { requestDevice: (o: unknown) => Promise<{ transferOut: (...a: unknown[]) => Promise<unknown> }> } };
      gate: { held: boolean; release: () => void; open: Promise<void> } };
    const usb = w.fakes.usb, request = usb.requestDevice;
    w.gate = { held: false, release: () => {}, open: Promise.resolve() };
    w.gate.open = new Promise((ok) => { w.gate.release = ok; });
    usb.requestDevice = async (o) => {
      const device = await request(o), out = device.transferOut;
      let n = 0;
      device.transferOut = async (...a) => { if (n++ === 2) { w.gate.held = true; await w.gate.open; } return out(...a); };
      return device;
    };
  });
  await press(page, 'Send to OP–XY');
  await expect.poll(() => page.evaluate(() => (window as unknown as { gate: { held: boolean } }).gate.held), { timeout: 15_000 }).toBe(true);
  await page.evaluate(() => (window as unknown as { fakes: { serial: EventTarget } }).fakes.serial.dispatchEvent(new Event('connect')));
  await page.clock.runFor(6_000);                                        // the looks after a port appears (5 s); MTP's timeout is 20 s
  expect(await page.evaluate(() => (window as unknown as { fakes: { serial: { xyfPorts: { opens: number }[] } } }).fakes.serial.xyfPorts.every((p) => p.opens === 0))).toBe(true);
  expect(await stage(page)).toBe('transfer');
  await page.evaluate(() => (window as unknown as { gate: { release: () => void } }).gate.release());
  await handedOff(page);                                                 // the copy ended, then the look found XY Forever
  expect(await page.evaluate(() => (window as unknown as { fakes: { usb: { reads: string[] } } }).fakes.usb.reads)).toEqual(['os.ldr']);   // read back: the copy finished first
  await page.close();
});

test('the device refuses a missing stock os.ldr: the page shows its words and the official 1.2.1 download', async () => {
  test.skip(why !== null, 'skipped: see the warning above');
  const sim = await powerOn('missing', true);                           // a plain first start: the menu stays, with USB
  await until('the menu', () => listening(sim));
  const page = await open(running!.relay.url, true);                     // no click: the page looks on load
  await expect.poll(() => stage(page), { timeout: 60_000 }).toBe('stop');
  const card = page.locator('[data-verdict]');
  expect(await card.getAttribute('data-verdict')).toBe('reinstall');
  expect(await card.textContent()).toContain('No official OS 1.2.1 found. Reinstall official OS 1.2.1, then start again.');
  expect(await page.getByRole('link', { name: /Download official OS 1\.2\.1/ }).isVisible()).toBe(true);
  expect(new URL(page.url()).pathname).toBe('/install/stop/');
  await page.close();
});

const UPGRADE = 'upgrade/firmware/blackfin/bin/os.ldr';
const CATALOG = 'content/op1_factory.json';
const MOD_FILE = 'content/uninstall-probe.txt';
const SAVED = 'xy-forever/content-official';
const asset = found.fixtures['xyf_build'];
const digestOf = (data: Uint8Array): string => createHash('sha256').update(data).digest('hex');

/** The simulator's writer models device encryption with an XOR. The active slot must contain every byte. */
function expectActive(sim: SimInfo, expected: Uint8Array): void {
  const raw = readFileSync(join(sim.root, '..', 'bootpart.bin'));
  const partitions = [0, 0x400000].filter((p) => raw[p + 12]);
  expect(partitions).toHaveLength(1);
  const at = partitions[0]!;
  expect(raw.readUInt32LE(at + 4)).toBe(expected.length);
  expect(raw.readUInt32LE(at + 8)).toBe(wire.crc32(expected));
  const plain = raw.subarray(at + 512, at + 512 + expected.length).map((byte, i) => byte ^ ((0xa5 + i * 29) & 255));
  expect(digestOf(plain)).toBe(digestOf(expected));
}

/** The stock dev-menu Track 5 action is manual. Use the simulator writer for the image it puts in the partition. */
async function simulatedTrack5(sim: SimInfo, bytes: Uint8Array): Promise<void> {
  const c = await Client.connect(tcpOpener(sim.address), { timeoutMs: 20_000 });
  try {
    const { sha256 } = await c.stage(bytes);
    expect((await c.bootInstall(sha256)).written).toBe(bytes.length);
  } finally { await c.close(); }
  expectActive(sim, bytes);
}

test('uninstalls to a verified stock partition, then installs again from the same browser', async () => {
  test.skip(why !== null || !asset, 'skipped: see the warning above');
  const candidate = readFileSync(asset!);
  const stock = readFileSync(found.fixtures['stock_os_ldr']!);
  image = digestOf(candidate).slice(0, 8);
  const sim = await powerOn('stock', false);
  await until('the first adoption', async () => existsSync(join(sim.root, 'xy-forever/state-a.txt')));
  await simulatedTrack5(sim, candidate);
  const file = (rel: string): string => join(sim.root, rel);
  const catalog = readFileSync(file(CATALOG));
  const mod = Buffer.from('only this recorded mod file should go away');
  const modEntry = file('xy-forever/firmware/mods-a');
  mkdirSync(modEntry, { recursive: true });
  writeFileSync(join(modEntry, 'info.txt'), 'name=Uninstall test mod\nversion=1.2.1\ngeneration=test\n');
  writeFileSync(join(modEntry, 'files.sha256'), `${digestOf(mod)}  ${MOD_FILE}\n`);
  writeFileSync(join(modEntry, 'os.ldr'), candidate);
  writeFileSync(file('os.ldr'), candidate);
  mkdirSync(file('user'), { recursive: true });
  writeFileSync(file('user/project.xy'), 'owner project');
  writeFileSync(file('bootloader.dev'), 'recovery marker');
  symlinkSync(file('user'), file('xy-forever/user-alias'));
  mkdirSync(file(SAVED), { recursive: true });
  writeFileSync(file(`${SAVED}/op1_factory.json`), catalog);
  writeFileSync(file(`${SAVED}/content.sha256`), `${digestOf(catalog)}  op1_factory.json\n`);
  writeFileSync(file(CATALOG), '{"modded":true}');
  writeFileSync(file(MOD_FILE), mod);
  writeFileSync(file('xy-forever/content-state.txt'), `state=installed\nentry=mods-a\nfile=uninstall-probe.txt\t${digestOf(mod)}\n`);
  await control(sim.control, 'power play');
  await until('the menu after preparing content', () => listening(sim));

  let bootWrites = 0, stockChecked = false, serialVisible = true;
  let reboot: number | undefined;
  const commands: string[] = [];
  const relay = running!.relay.url;
  const page = await open(relay, true, candidate, async (tab) => {
    const relayStatus = relay.replace(/^ws/, 'http') + '/status';
    await tab.route(relayStatus, (route) => serialVisible ? route.continue() : route.fulfill({
      headers: { 'access-control-allow-origin': '*' }, json: { attached: false },
    }));
    await tab.routeWebSocket(relay, (socket) => {
      const server = socket.connectToServer(), requests = new wire.FrameParser(), replies = new wire.FrameParser();
      socket.onMessage(async (message) => {
        for (const frame of requests.feed(Buffer.from(message))) {
          if (frame.type === wire.TYPE['boot.uninstall']) {
            bootWrites++;
            expect(readFileSync(file(CATALOG)).equals(catalog)).toBe(true);
            expect(existsSync(file(MOD_FILE))).toBe(false);
            if (bootWrites === 1) await control(sim.control, 'set fail_select 1');
          }
          if (frame.type === wire.TYPE['file.list']) commands.push(wire.decodeFileListRequest(frame.payload).path);
          if (frame.type === wire.TYPE['device.reboot']) {
            reboot = wire.decodeDeviceRebootRequest(frame.payload).target;
            expect(stockChecked).toBe(true);
            expect(existsSync(file('xy-forever'))).toBe(false);
            expect(readFileSync(file('os.ldr')).equals(stock)).toBe(true);
            // The simulator executes compiled XY Forever regardless of the installed image. Verify the request, then
            // simulate disappearance of its serial service when real stock starts. Keep the same media for Track 5.
            serialVisible = false;
            const reply = wire.encodeDeviceRebootReply({ ok: true, status: 0, fields: {} });
            socket.send(Buffer.from(wire.encodeFrame(frame.type | wire.REPLY, frame.seq, reply)));
            setTimeout(() => socket.close(), 30);
            return;
          }
        }
        server.send(message);
      });
      server.onMessage((message) => {
        for (const frame of replies.feed(Buffer.from(message))) {
          if (frame.type === (wire.TYPE['boot.uninstall'] | wire.REPLY) && wire.decodeBootUninstallReply(frame.payload).ok) {
            expectActive(sim, stock);
            expect(readFileSync(file('os.ldr')).equals(candidate)).toBe(true);
            expect(existsSync(file('xy-forever/firmware'))).toBe(true);
            stockChecked = true;
          }
        }
        socket.send(message);
      });
    });
  });
  try {
    await handedOff(page);
    await nav(page, 'Device').click();
    await press(page, 'Uninstall XY Forever');
    await press(page, 'Cancel');
    expect(bootWrites).toBe(0);
    expect(existsSync(file(MOD_FILE))).toBe(true);

    await press(page, 'Uninstall XY Forever');
    await press(page, 'Uninstall');
    await expect.poll(() => page.getByTestId('notice').textContent(), { timeout: 90_000 }).toMatch(/boot\.uninstall|read-back/i);
    expect(bootWrites).toBe(1);
    expectActive(sim, candidate);
    expect(existsSync(file('xy-forever/firmware'))).toBe(true);
    expect(readFileSync(file('os.ldr')).equals(candidate)).toBe(true);

    // At this boundary the old XY Forever partition is still active. Bring its menu back after the failed select.
    await nav(page, 'Connect').click();
    await press(page, 'Disconnect');
    await control(sim.control, 'power play');
    await until('the original XY Forever menu after the interruption', () => listening(sim));
    await press(page, 'Connect');
    await press(page, 'Uninstall XY Forever');
    await press(page, 'Uninstall');
    await expect.poll(async () => (await page.locator('[data-testid=notice][data-type=error]').textContent({ timeout: 100 }).catch(() => null)) ??
      new URL(page.url()).pathname, { timeout: 90_000 }).toBe('/connect/');
    expect(reboot).toBe(1);
    expect(await page.getByRole('status').textContent()).toContain('Stock OS 1.2.1 was restored');
    expect(readdirSync(sim.root).sort()).toEqual(['bootloader.dev', 'content', 'firmware.version', 'os.ldr', 'upgrade', 'user']);
    expect(readFileSync(file('bootloader.dev'), 'utf8')).toBe('recovery marker');
    expect(readFileSync(file('user/project.xy'), 'utf8')).toBe('owner project');
    expect(readFileSync(file(UPGRADE)).equals(stock)).toBe(true);
    expect(commands.some((path) => path.endsWith('/user-alias'))).toBe(false);
    expect((await status(sim))['rw_open']).toBe(0);

    await page.getByRole('link', { name: 'Install XY Forever' }).click();
    expect(await stage(page)).toBe('boot');
    await toStart(page, 'Mac');
    expect(await page.evaluate(() => (window as unknown as { fakes: { usb: { reads: string[] } } }).fakes.usb.reads)).toEqual(['os.ldr']);
    writeFileSync(file('os.ldr'), candidate);                             // the test's fake dev-menu MTP copy
    await simulatedTrack5(sim, candidate);
    await control(sim.control, 'power play');
    serialVisible = true;
    await handedOff(page);
    adopted(sim);
    expect(readFileSync(file('user/project.xy'), 'utf8')).toBe('owner project');
  } finally { await page.close(); }
});
