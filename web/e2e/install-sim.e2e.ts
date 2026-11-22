// The Install page's end (#178) in the system Chrome (headless) against the device simulator, which runs XY Forever's own
// code and command rules: after Track 5, XY Forever sets itself up on the device (it adopts the stock os.ldr as library
// entry opxy-121, makes it the default, boots it), and the page hands the owner to the Mods page once it finds it.
// te-boot and the dev menu's MTP are the fakes of test/fake-devices.ts; Web Serial is the fake navigator.serial of the
// client's Web Serial tests (clients/ts/test/fake-serial.ts: Chrome's 255-byte default receive buffer, the chooser,
// granted ports, a device that goes away and comes back), reaching the simulator through the relay. Each test starts
// a fresh simulator: a stock OP-XY just after Track 5 (`--fresh`). Skipped with the reason when Chrome or the
// simulator cannot run here. No real-time waits: the device's clock moves by `until` (sim/clock.cpp), the page's
// own timers (its looks, its polls) by Playwright's clock, and the checks wait for the page's events.
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from './cpu.ts';
import type { Browser, Page, Locator } from '@playwright/test';
import { startRelay } from 'xy-forever-client/test/relay.ts';
import type { Relay } from 'xy-forever-client/test/relay.ts';
import { control, findSimulator, randomBase, startAll, until as simUntil } from 'xy-forever-client/test/simulators.ts';
import type { SimInfo } from 'xy-forever-client/test/simulators.ts';
import { Client, tcpOpener } from 'xy-forever-client/node';
import { verifiedBuild } from './needs.ts';
import { protocol as wire } from 'xy-forever-client';

const IMAGE = Buffer.from(Uint8Array.from({ length: 70_000 }, (_, i) => (i * 31 + 7) & 0xff));
const OK_121 = 'mode:bootloader;product:OP-XY;serial:X1;bl_version:1000.0.20+0;os_version:1.2.1+0';
const FAKE_SERIAL = fileURLToPath(import.meta.resolve('xy-forever-client/test/fake-serial.ts'));
const ENTRY = 'xy-forever/firmware/opxy-121';
// A random base (simulator, control; the relay on a free port) and state folder: another checkout may run this test at the same time.
const BASE = parseInt(process.env['XYF_WEB_SIM_PORT'] ?? String(randomBase()), 10);
// The install page's timers (src/install/flow.ts): its look every LOOK_MS at the last stage, its poll while the device sets up.
const LOOK_MS = 3000,
  POLL_MS = 700;

const nav = (page: Page, name: string): Locator =>
  page.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name, exact: true });

const found = findSimulator();
const why: string | null = found.why;
let browser: Browser | undefined; // the project's Chrome (playwright.config.ts: no real Web Serial or WebUSB)

// The pin's image. Only a setup that never starts compares it (finish.ts), so any name will do; the uninstall test pins its build.
let image = 'test-pin';
let running: { sim: SimInfo; relay: Relay; stop: () => Promise<void> } | null = null;

// Each test has its own simulator: they run in parallel (a worker each: its own Chrome and state folder).
test.describe.configure({ mode: 'parallel' });

test.beforeAll(({ browser: projectBrowser }) => {
  browser = projectBrowser;
  if (why) console.warn(`\ninstall simulator test skipped: ${why}\n`);
});
test.afterEach(async () => {
  await running?.stop();
  running = null;
});
test.afterAll(async () => {
  if (!why) rmSync(join(found.sessions!, `web-install-${BASE}`), { recursive: true, force: true });
});

/** A fresh simulator: a stock OP-XY just after Track 5 (stock: its updater's os.ldr; plain: no PLAY at power-on). */
async function powerOn(stock: 'stock' | 'missing', plain: boolean): Promise<SimInfo> {
  const state = join(found.sessions!, `web-install-${BASE}`);
  const started = await startAll(state, BASE, [['sim', 0, ['--fresh', stock, ...(plain ? ['--plain'] : [])]]]);
  const sim = started.infos['sim']!;
  const relay = await startRelay({ target: sim.port, control: sim.control });
  running = {
    sim,
    relay,
    stop: async () => {
      await relay.close();
      await started.stopAll();
    },
  };
  return sim;
}

const status = async (sim: SimInfo): Promise<Record<string, unknown>> => control(sim.control, 'status');
/** Until cond, the device's clock moved on between looks (it moves only when told: sim/clock.cpp); no host time waited. */
const until = (what: string, cond: () => boolean | Promise<boolean>): Promise<boolean> =>
  simUntil(running!.sim.control, what, cond);
const inOs = async (sim: SimInfo): Promise<boolean> => {
  const s = await status(sim);
  return s['a'] === 'os' && s['b'] === 'os';
};
const listening = async (sim: SimInfo): Promise<boolean> => (await status(sim))['listening'] === 1;

/** The app with fake te-boot, MTP and Web Serial (granted: this site was given the port before; no chooser needed). Its
 * clock is Playwright's: it runs as the wall clock does until a test moves it on (fastForward, runFor). */
async function open(
  relay: string,
  granted: boolean,
  bytes = IMAGE,
  prepare?: (page: Page) => Promise<void>,
): Promise<Page> {
  const base = process.env['XYF_E2E_URL']!.slice(0, -1); // the run's dev server (playwright.config.ts webServer), no trailing /
  const pin = {
    schema: 1,
    version: '0.1.0',
    image,
    sha256: createHash('sha256').update(bytes).digest('hex'),
    bytes: bytes.length,
  };
  const page = await browser!.newPage({ acceptDownloads: true });
  await page.clock.install();
  await page.route('**/src/install/release.json*', (route) =>
    route.fulfill({ contentType: 'text/javascript', body: `export default ${JSON.stringify(pin)};` }),
  );
  await page.route('**/release/os.ldr', (route) =>
    route.fulfill({ contentType: 'application/octet-stream', body: bytes }),
  );
  await prepare?.(page);
  await page.goto(`${base}/connect/`);
  await page.addScriptTag({
    type: 'module',
    content: `
    import { FakeMidi, FakeUsb } from '/test/fake-devices.ts';
    import { FakeSerial } from '/@fs${FAKE_SERIAL}';
    const fields = Object.fromEntries(${JSON.stringify(OK_121)}.split(';').map((kv) => kv.split(':')));
    const midi = new FakeMidi(fields), usb = new FakeUsb({ 'upgrade/firmware/blackfin/bin/os.ldr': new Uint8Array(2373336) }, 64 * 1024 * 1024);   // the stock size
    const serial = new FakeSerial(${JSON.stringify(relay)}, { identity: { usbVendorId: 0x2367, usbProductId: 0x0021 }, granted: ${granted} });
    Object.defineProperty(navigator, 'requestMIDIAccess', { value: midi.requestMIDIAccess, configurable: true });
    Object.defineProperty(navigator, 'usb', { value: usb, configurable: true });
    serial.install();
    window.fakes = { midi, usb, serial };
    document.body.dataset.fakes = 'ready';`,
  });
  await page.waitForSelector('body[data-fakes=ready]');
  await nav(page, 'Install').click();
  return page;
}

const stage = (page: Page): Promise<string | null> => page.locator('[data-stage]').getAttribute('data-stage');
/** Once the page shows that stage (a locator check: it waits for the page, not for a polling interval). */
const atStage = (page: Page, name: string): Promise<void> =>
  expect(page.locator('[data-stage]')).toHaveAttribute('data-stage', name);
const press = (page: Page, name: string): Promise<void> => page.getByRole('button', { name, exact: true }).click();

/** te-boot to Track 5's "firmware written ok!": the last stage, "Switch your OP–XY off, then on." */
async function toStart(page: Page, computer: 'Mac' | 'Windows'): Promise<void> {
  await page.getByRole('button', { name: /Check my OP/ }).click();
  await atStage(page, 'menu');
  for (const name of ['Yes, I see it', computer, 'Dev menu is open', 'I see "mtp mode active"'])
    await press(page, name);
  if (computer === 'Mac') {
    await press(page, 'They’re closed · Continue');
    await press(page, 'Send to OP–XY');
    await press(page, 'File sent · Continue'); // (the click waits for the copy's end)
  } else {
    await press(page, 'I see os.ldr, about 2,318 KB');
    await press(page, 'File copied · Continue');
  }
  for (const name of ['I’m back at the dev menu', 'I pressed track 5', 'I see "firmware written ok!"'])
    await press(page, name);
  expect(await stage(page)).toBe('start');
  expect(await page.locator('[data-stage]').textContent()).toContain('Switch your OP–XY off, then on.');
}

/** The page handed the owner to the app: the Mods page, connected, the installed notice once. */
async function handedOff(page: Page): Promise<void> {
  await page.waitForURL((url) => url.pathname === '/mods/');
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
    schema: 'xy-forever.package',
    schema_version: 1,
    type: 'firmware',
    name: 'OP-XY',
    version: '1.2.1',
    kind: 'official',
  });
  expect(readFileSync(join(sim.root, 'xy-forever/state-a.txt'), 'utf8')).toContain('default=opxy-121\n');
}

// The longest test first: the others run beside it.
const UPGRADE = 'upgrade/firmware/blackfin/bin/os.ldr';
const CATALOG = 'content/op1_factory.json';
const MOD_FILE = 'content/uninstall-probe.txt';
const SAVED = 'xy-forever/content-official';
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

/**
 * The page looks at a running job every half second of its own time: before its look goes on, the relay looks itself
 * (another seq), the device's clock moved on half a second between looks, until the job ended. The page's look then
 * finds it ended, and the page never waits.
 */
async function jobToEnd(
  sim: SimInfo,
  look: { seq: number; payload: Uint8Array },
  device: { exchange(data: Uint8Array, until: (reply: Buffer) => boolean): Promise<void> },
): Promise<void> {
  for (let n = 0; ; n++) {
    const seq = (look.seq + 128 + (n & 1)) & 0xff,
      parser = new wire.FrameParser();
    let reply: ReturnType<typeof wire.decodeJobStatusReply> | undefined;
    await device.exchange(wire.encodeFrame(wire.TYPE['job.status'], seq, look.payload), (bytes) => {
      for (const frame of parser.feed(bytes)) {
        if (frame.type === (wire.TYPE['job.status'] | wire.REPLY) && frame.seq === seq)
          reply = wire.decodeJobStatusReply(frame.payload);
      }
      return reply !== undefined;
    });
    if (!reply!.ok || reply!.fields.phase >= 8) return; // none, or final (8 done, 9 failed, 10 cancelled)
    await control(sim.control, 'advance 500');
  }
}

/** The stock dev-menu Track 5 action is manual. Use the simulator writer for the image it puts in the partition. */
async function simulatedTrack5(sim: SimInfo, bytes: Uint8Array): Promise<void> {
  const c = await Client.connect(tcpOpener(sim.address), { timeoutMs: 20_000 });
  try {
    const { sha256 } = await c.stage(bytes);
    expect((await c.bootInstall(sha256)).written).toBe(bytes.length);
  } finally {
    await c.close();
  }
  expectActive(sim, bytes);
}

/** An installed XY Forever with a recorded mod and modded content, an owner's files, the app handed off to its Mods
 * page; the page's link to the device checked on the relay as it passes (interrupted: the first boot write's select
 * fails). The stock os.ldr's 2.3 MB moves several times in an uninstall (read, staged, the root's restore and
 * read-backs): each uninstall test does one, the tests run in parallel. */
async function installedWithMods(build: string, interrupted: boolean) {
  // A hang bound, not a speed limit (the CPU budget holds that: e2e/budgets.ts): the real stock OS 1.2.1, whose
  // digest the page and the device check, moves both ways in 1 KB requests (the protocol's limits), about 5 s alone
  // and past the 15 s default on a loaded machine.
  test.setTimeout(60_000);
  const candidate = readFileSync(build);
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
  writeFileSync(
    file('xy-forever/content-state.txt'),
    `state=installed\nentry=mods-a\nfile=uninstall-probe.txt\t${digestOf(mod)}\n`,
  );
  await control(sim.control, 'power play');
  await until('the menu after preparing content', () => listening(sim));

  let bootWrites = 0,
    stockChecked = false,
    serialVisible = true;
  let reboot: number | undefined;
  const commands: string[] = [];
  const relay = running!.relay;
  // The page's link to the device, seen on the relay: each request and reply checked as it passes.
  relay.tap = () => {
    const requests = new wire.FrameParser(),
      replies = new wire.FrameParser();
    return {
      async up(message, toPage, device) {
        for (const frame of requests.feed(message)) {
          if (frame.type === wire.TYPE['boot.uninstall']) {
            bootWrites++;
            expect(readFileSync(file(CATALOG)).equals(catalog)).toBe(true);
            expect(existsSync(file(MOD_FILE))).toBe(false);
            if (bootWrites === 1 && interrupted) await control(sim.control, 'set fail_select 1');
          }
          if (frame.type === wire.TYPE['file.list']) commands.push(wire.decodeFileListRequest(frame.payload).path);
          if (frame.type === wire.TYPE['job.status']) await jobToEnd(sim, frame, device);
          if (frame.type === wire.TYPE['device.reboot']) {
            reboot = wire.decodeDeviceRebootRequest(frame.payload).target;
            expect(stockChecked).toBe(true);
            expect(existsSync(file('xy-forever'))).toBe(false);
            expect(readFileSync(file('os.ldr')).equals(stock)).toBe(true);
            // The simulator executes compiled XY Forever regardless of the installed image. Verify the request, then
            // simulate disappearance of its serial service when real stock starts. Keep the same media for Track 5.
            serialVisible = false;
            const reply = wire.encodeDeviceRebootReply({ ok: true, status: 0, fields: {} });
            toPage.send(wire.encodeFrame(frame.type | wire.REPLY, frame.seq, reply));
            toPage.close();
            return false;
          }
        }
        return true;
      },
      down(message) {
        for (const frame of replies.feed(message)) {
          if (
            frame.type === (wire.TYPE['boot.uninstall'] | wire.REPLY) &&
            wire.decodeBootUninstallReply(frame.payload).ok
          ) {
            expectActive(sim, stock);
            expect(readFileSync(file('os.ldr')).equals(candidate)).toBe(true);
            expect(existsSync(file('xy-forever/firmware'))).toBe(true);
            stockChecked = true;
          }
        }
      },
    };
  };
  const page = await open(relay.url, true, candidate, async (tab) => {
    const relayStatus = relay.url.replace(/^ws/, 'http') + '/status';
    await tab.route(relayStatus, (route) =>
      serialVisible
        ? route.continue()
        : route.fulfill({
            headers: { 'access-control-allow-origin': '*' },
            json: { attached: false },
          }),
    );
  });
  await handedOff(page);
  return {
    sim,
    page,
    relay,
    file,
    candidate,
    stock,
    catalog,
    commands,
    bootWrites: () => bootWrites,
    reboot: () => reboot,
    showSerial: () => (serialVisible = true),
  };
}

test('uninstalls to a verified stock partition, then installs again from the same browser', async () => {
  const { sim, page, relay, file, candidate, stock, commands, reboot, showSerial } = await installedWithMods(
    verifiedBuild(found),
    false,
  );
  try {
    await nav(page, 'Device').click();
    await press(page, 'Uninstall XY Forever');
    await press(page, 'Uninstall');
    // The stock os.ldr's 2.3 MB upload (4,600 requests), the boot partition write and the reboot: /connect/, or the error.
    const failure = page.locator('[data-testid=notice][data-type=error]');
    await Promise.race([
      page.waitForURL((url) => url.pathname === '/connect/'),
      failure.waitFor().then(async () => {
        throw new Error(`uninstall failed: ${await failure.textContent()}`);
      }),
    ]);
    expect(reboot()).toBe(1);
    expect(await page.getByRole('status').textContent()).toContain('Stock OS 1.2.1 was restored');
    expect(readdirSync(sim.root).sort()).toEqual([
      'bootloader.dev',
      'content',
      'firmware.version',
      'os.ldr',
      'upgrade',
      'user',
    ]);
    expect(readFileSync(file('bootloader.dev'), 'utf8')).toBe('recovery marker');
    expect(readFileSync(file('user/project.xy'), 'utf8')).toBe('owner project');
    expect(readFileSync(file(UPGRADE)).equals(stock)).toBe(true);
    expect(commands.some((path) => path.endsWith('/user-alias'))).toBe(false);
    expect((await status(sim))['rw_open']).toBe(0);

    await page.getByRole('link', { name: 'Install XY Forever' }).click();
    expect(await stage(page)).toBe('boot');
    await toStart(page, 'Mac');
    expect(
      await page.evaluate(() => (window as unknown as { fakes: { usb: { reads: string[] } } }).fakes.usb.reads),
    ).toEqual(['os.ldr']);
    writeFileSync(file('os.ldr'), candidate); // the test's fake dev-menu MTP copy
    await simulatedTrack5(sim, candidate);
    await control(sim.control, 'power play');
    showSerial();
    await until('the menu after the new install', () => listening(sim));
    await page.clock.fastForward(LOOK_MS); // the last stage's next look
    await handedOff(page);
    adopted(sim);
    expect(readFileSync(file('user/project.xy'), 'utf8')).toBe('owner project');
    expect(relay.tapErrors).toEqual([]);
  } finally {
    await page.close();
  }
});

test("an uninstall cancelled, then stopped at the boot write, keeps XY Forever, its mods and the owner's files", async () => {
  const { sim, page, file, candidate, bootWrites } = await installedWithMods(verifiedBuild(found), true);
  try {
    await nav(page, 'Device').click();
    await press(page, 'Uninstall XY Forever');
    await press(page, 'Cancel');
    expect(bootWrites()).toBe(0);
    expect(existsSync(file(MOD_FILE))).toBe(true);

    await press(page, 'Uninstall XY Forever');
    await press(page, 'Uninstall');
    await expect(page.getByTestId('notice')).toHaveText(/boot\.uninstall|read-back/i);
    expect(bootWrites()).toBe(1);
    expectActive(sim, candidate);
    expect(existsSync(file('xy-forever/firmware'))).toBe(true);
    expect(readFileSync(file('os.ldr')).equals(candidate)).toBe(true);
    expect(readFileSync(file('user/project.xy'), 'utf8')).toBe('owner project');
  } finally {
    await page.close();
  }
});

test('Mac: the first plain power-on sets up OS 1.2.1 on the device and starts it; PLAY then opens the app on Mods', async () => {
  test.skip(why !== null, 'skipped: see the warning above');
  const sim = await powerOn('stock', true); // the first plain power-on: the setup, then the OS
  await until('the OS, after the setup', () => inOs(sim)); // (before the page walks: it would find XY Forever mid-flow)
  const page = await open(running!.relay.url, false);
  await toStart(page, 'Mac');
  expect(
    await page.evaluate(() => (window as unknown as { fakes: { usb: { reads: string[] } } }).fakes.usb.reads),
  ).toEqual(['os.ldr']);
  adopted(sim);
  await control(sim.control, 'power play'); // the owner holds PLAY: the XY Forever menu
  await until('the menu', () => listening(sim));
  await press(page, 'Find my OP–XY'); // never granted on this computer: the chooser, from the click
  await handedOff(page);
  expect(
    await page.evaluate(
      () =>
        (window as unknown as { fakes: { serial: { chooser: number; xyfPorts: { dropped: number }[] } } }).fakes.serial,
    ),
  ).toMatchObject({ chooser: 1 });
  expect(
    await page.evaluate(() =>
      (window as unknown as { fakes: { serial: { xyfPorts: { dropped: number }[] } } }).fakes.serial.xyfPorts.every(
        (p) => p.dropped === 0,
      ),
    ),
  ).toBe(true);
  await page.close();
});

test('Windows: copied by hand; the page finds XY Forever by itself after the setup (a port granted before)', async () => {
  test.skip(why !== null, 'skipped: see the warning above');
  const sim = await powerOn('stock', true);
  await until('the OS, after the setup', () => inOs(sim)); // before the page walks, as above
  const page = await open(running!.relay.url, true);
  await toStart(page, 'Windows');
  adopted(sim);
  expect(await stage(page)).toBe('start'); // the stock OS has no serial port: nothing to find
  await control(sim.control, 'power play');
  await until('the menu', () => listening(sim));
  await page.clock.fastForward(LOOK_MS); // no click: the last stage looks every few seconds
  await handedOff(page);
  await page.close();
});

test('at any stage: XY Forever found while it sets up shows the setup, then hands off', async () => {
  test.skip(why !== null, 'skipped: see the warning above');
  const sim = await powerOn('stock', false); // PLAY at the first start: the menu stays after the setup
  await control(sim.control, 'set a_slow_ms 10'); // slow enough to watch
  await until('the menu', () => listening(sim));
  const page = await open(running!.relay.url, true); // no click: the page looks on load
  await expect(page.locator('[data-stage] [role=status]')).toHaveText(/adding OS 1\.2\.1|about to add OS 1\.2\.1/);
  expect(await stage(page)).toBe('start'); // jumped ahead from the first stage
  await control(sim.control, 'set a_slow_ms 0'); // seen: the rest at full speed
  await until('the setup', () => existsSync(join(sim.root, 'xy-forever/state-a.txt')));
  await page.clock.fastForward(POLL_MS); // the page's next look at the setup
  await handedOff(page);
  adopted(sim);
  await page.close();
});

test('no look while the MTP copy runs; once it ends, XY Forever found mid-flow hands off', async () => {
  test.skip(why !== null, 'skipped: see the warning above');
  const sim = await powerOn('stock', true); // set up, then the OS: no serial port yet
  await until('the OS, after the setup', () => inOs(sim));
  const page = await open(running!.relay.url, true); // the looks' timers run on demand
  await page.getByRole('button', { name: /Check my OP/ }).click();
  await atStage(page, 'menu');
  for (const name of [
    'Yes, I see it',
    'Mac',
    'Dev menu is open',
    'I see "mtp mode active"',
    'They’re closed · Continue',
  ])
    await press(page, name);
  expect(await stage(page)).toBe('transfer');
  await control(sim.control, 'power play'); // XY Forever's menu, with its serial port: no look yet
  await until('the menu', () => listening(sim)); // (no stage change, no connect event)
  await page.evaluate(() => {
    // the copy holds mid-session until released
    const w = window as unknown as {
      fakes: {
        usb: { requestDevice: (o: unknown) => Promise<{ transferOut: (...a: unknown[]) => Promise<unknown> }> };
      };
      gate: { held: Promise<void>; hold: () => void; release: () => void; open: Promise<void> };
    };
    const usb = w.fakes.usb,
      request = usb.requestDevice;
    w.gate = { held: Promise.resolve(), hold: () => {}, release: () => {}, open: Promise.resolve() };
    w.gate.open = new Promise((ok) => {
      w.gate.release = ok;
    });
    w.gate.held = new Promise((ok) => {
      w.gate.hold = ok;
    });
    usb.requestDevice = async (o) => {
      const device = await request(o),
        out = device.transferOut;
      let n = 0;
      device.transferOut = async (...a) => {
        if (n++ === 2) {
          w.gate.hold();
          await w.gate.open;
        }
        return out(...a);
      };
      return device;
    };
  });
  await press(page, 'Send to OP–XY');
  await page.evaluate(() => (window as unknown as { gate: { held: Promise<void> } }).gate.held);
  await page.evaluate(() =>
    (window as unknown as { fakes: { serial: EventTarget } }).fakes.serial.dispatchEvent(new Event('connect')),
  );
  await page.clock.runFor(6_000); // the looks after a port appears (5 s); MTP's timeout is 20 s
  expect(
    await page.evaluate(() =>
      (window as unknown as { fakes: { serial: { xyfPorts: { opens: number }[] } } }).fakes.serial.xyfPorts.every(
        (p) => p.opens === 0,
      ),
    ),
  ).toBe(true);
  expect(await stage(page)).toBe('transfer');
  await page.evaluate(() => (window as unknown as { gate: { release: () => void } }).gate.release());
  await handedOff(page); // the copy ended, then the look found XY Forever
  expect(
    await page.evaluate(() => (window as unknown as { fakes: { usb: { reads: string[] } } }).fakes.usb.reads),
  ).toEqual(['os.ldr']); // read back: the copy finished first
  await page.close();
});

test('the device refuses a missing stock os.ldr: the page shows its words and the official 1.2.1 download', async () => {
  test.skip(why !== null, 'skipped: see the warning above');
  const sim = await powerOn('missing', true); // a plain first start: the menu stays, with USB
  await until('the menu', () => listening(sim));
  const page = await open(running!.relay.url, true); // no click: the page looks on load
  await atStage(page, 'stop');
  const card = page.locator('[data-verdict]');
  expect(await card.getAttribute('data-verdict')).toBe('reinstall');
  expect(await card.textContent()).toContain(
    'No official OS 1.2.1 found. Reinstall official OS 1.2.1, then start again.',
  );
  expect(await page.getByRole('link', { name: /Download official OS 1\.2\.1/ }).isVisible()).toBe(true);
  expect(new URL(page.url()).pathname).toBe('/install/stop/');
  await page.close();
});
