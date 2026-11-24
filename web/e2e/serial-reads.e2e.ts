// #306: thousands of file.read requests over the client's Web Serial transport in Chrome, against the simulator through
// the client's fake navigator.serial and the relay (as install-sim.e2e.ts): every reply whole, none resent. The fake
// port delivers what the simulator sends; a device run alone shows whether Chrome's own serial path loses bytes.
import { rmSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from './cpu.ts';
import { startRelay } from 'xy-forever-client/test/relay.ts';
import { findSimulator, startAll } from 'xy-forever-client/test/simulators.ts';

const found = findSimulator();
const STATE = join(found.sessions, `web-serial-reads-${process.pid}`);
const FAKE_SERIAL = fileURLToPath(import.meta.resolve('xy-forever-client/test/fake-serial.ts'));
const CLIENT = fileURLToPath(import.meta.resolve('xy-forever-client'));
const READS = 3000;

test.afterAll(() => {
  if (!found.why) rmSync(STATE, { recursive: true, force: true });
});

test(`${READS} file reads over Web Serial: every reply whole, none resent`, async ({ browser }) => {
  test.skip(found.why !== null, `skipped: ${found.why}`);
  test.setTimeout(60_000);
  const started = await startAll(STATE, [['sim', []]]);
  const sim = started.infos['sim']!;
  const relay = await startRelay({ target: sim.port, control: sim.control });
  try {
    const page = await browser.newPage();
    await page.goto(`${process.env['XYF_E2E_URL']!}manage/`);
    const result = await page.evaluate(
      async ({ relay, fake, client, reads }) => {
        const { FakeSerial } = await import(/* @vite-ignore */ `/@fs${fake}`);
        const { connectWebSerial } = await import(/* @vite-ignore */ `/@fs${client}`);
        const serial = new FakeSerial(relay, { granted: true, requireGesture: false });
        const device = await connectWebSerial({ serial, choose: false, timeoutMs: 10_000 });
        const path = '/yaffs2/xy-forever/serial-reads.bin';
        const data = Uint8Array.from({ length: 16 * 1024 }, (_, i) => (i * 13 + 5) & 0xff);
        await device.writeFile(path, data);
        let sent = 0;
        const request = device.link.request.bind(device.link);
        device.link.request = (...args: Parameters<typeof request>) => (sent++, request(...args));
        let bad = 0;
        const t0 = performance.now();
        for (let i = 0; i < reads; i++) {
          const at = (i % 16) * 1024;
          const got: Uint8Array = await device.readRange(path, at, 1024);
          if (got.length !== 1024 || got.some((b, k) => b !== data[at + k])) bad++;
        }
        const ms = performance.now() - t0;
        const dropped = device.link.dropped;
        await device.close();
        return { bad, sent, dropped, ms };
      },
      { relay: relay.url, fake: FAKE_SERIAL, client: CLIENT, reads: READS },
    );
    console.log(`${READS} reads over Web Serial in ${Math.round(result.ms)} ms`);
    expect(result).toMatchObject({ bad: 0, sent: READS, dropped: 0 });
    await page.close();
  } finally {
    await relay.close();
    await started.stopAll();
  }
});
