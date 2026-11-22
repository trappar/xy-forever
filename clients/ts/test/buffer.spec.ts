// Web Serial (#178): how a port is opened decides whether XY Forever's larger replies arrive. The fake port
// (fake-serial.ts) keeps Chrome's receive buffer: bufferSize, 255 bytes unless open() names one, and a burst that
// does not fit is lost. And Chrome on a Mac lists one OP-XY twice (cu. and tty.): two open entries split its replies.
import { afterEach, beforeEach, describe, expect, inject, test, vi } from 'vitest';
import {
  Client,
  RELEASE_FILTERS,
  SERIAL_OPEN,
  USB_FILTERS,
  WebSerialTransport,
  connectWebSerial,
} from '../src/index.ts';
import type { Opener, Transport, TransportSink } from '../src/index.ts';
import { expire, fakeTime, timersUntil } from './clock.ts';
import { FakeSerial } from './fake-serial.ts';
import { simWrite } from './gesture.ts';
import type { FakePort } from './fake-serial.ts';
import { countSim } from './cpu-setup.ts';

const sims = inject('sims');
const sim = countSim(sims.buffer);
const PATH = '/yaffs2/xy-forever/log/web/buffer.bin';

/** A port opened with only a baud rate, as a page that leaves Chrome's defaults would, and read continuously. */
class DefaultsTransport implements Transport {
  private reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  private readonly port: FakePort;
  constructor(port: FakePort) {
    this.port = port;
  }
  static async open(port: FakePort): Promise<DefaultsTransport> {
    await port.open({ baudRate: 115200 });
    return new DefaultsTransport(port);
  }
  start(sink: TransportSink): void {
    const reader = (this.reader = this.port.readable!.getReader());
    void (async () => {
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          if (value) sink.data(value);
        }
      } catch {
        /* lost */
      }
      reader.releaseLock();
      await this.port.close().catch(() => undefined);
      sink.closed();
    })();
  }
  async write(data: Uint8Array): Promise<void> {
    const w = this.port.writable!.getWriter();
    try {
      await w.write(data);
    } finally {
      w.releaseLock();
    }
  }
  async close(): Promise<void> {
    await this.reader?.cancel().catch(() => undefined);
  }
}

describe.skipIf(!sim)(`the port's receive buffer${sim ? '' : ` (skipped: ${sims.why})`}`, () => {
  const stock = USB_FILTERS[1];
  const data = crypto.getRandomValues(new Uint8Array(3000));
  let client: Client | null = null;
  beforeEach(fakeTime);
  afterEach(async () => {
    vi.useRealTimers();
    await client?.close();
    client = null;
  });

  test('every path opens with 64 KiB: a 1 KiB file.read reply arrives whole, nothing dropped', async () => {
    expect(SERIAL_OPEN).toEqual({ baudRate: 115200, bufferSize: 65536 });
    await simWrite(sim!.root, PATH, data);
    const serial = new FakeSerial(sim!.relay, { identity: stock, stale: 'granted' });
    const connecting = connectWebSerial({ serial, filters: RELEASE_FILTERS, probeMs: 800 });
    await timersUntil(() => serial.stale!.opens === 1 && !serial.stale!.isOpen); // probed, then passed over
    client = await connecting;
    expect(client.info.limits.read).toBe(1024);
    expect(await client.readRange(PATH, 0, 1024)).toEqual(data.subarray(0, 1024));
    expect(await client.readFile(PATH)).toEqual(data);
    expect(serial.xyfPorts.at(-1)!.dropped).toBe(0);
  });

  test("Chrome's default buffer (255 bytes) loses the reply: the same read over a port opened with defaults never answers", async () => {
    await simWrite(sim!.root, PATH, data);
    const serial = new FakeSerial(sim!.relay, { identity: stock, granted: true });
    const live = (await serial.getPorts()).at(-1)!;
    const opener: Opener = async () => DefaultsTransport.open(live);
    const failed = await Client.connect(opener, { timeoutMs: 2500 }).then(
      async (c) => {
        client = c; // hello fitted: the 1 KiB reply cannot
        const before = live.dropped;
        const read = c.readRange(PATH, 0, 1024, { timeoutMs: 1500 });
        const lost = (): boolean => live.dropped > before; // the reply came, and did not fit
        return expire(read, lost).then(
          () => null,
          (e: unknown) => e,
        );
      },
      (e: unknown) => e,
    );
    expect(failed).toMatchObject({ code: 'timeout' });
    expect(live.dropped).toBeGreaterThan(0);
  });

  test('the same OP-XY listed twice (cu. and tty.): with one entry open in this page, the other is never opened', async () => {
    await simWrite(sim!.root, PATH, data);
    const serial = new FakeSerial(sim!.relay, { identity: stock, twin: 'granted' });
    client = await connectWebSerial({ serial, filters: RELEASE_FILTERS, probeMs: 800 });
    const live = serial.xyfPorts.at(-1)!;
    await expect(WebSerialTransport.open(serial.twin!)).rejects.toMatchObject({ name: 'InvalidStateError' });
    const second = await connectWebSerial({ serial, filters: RELEASE_FILTERS, probeMs: 800, choose: false }).catch(
      (e: unknown) => e,
    );
    expect(second).toMatchObject({ code: 'aborted' }); // "already connected in this page"
    expect(serial.twin!.opens).toBe(0);
    expect(await client.readFile(PATH)).toEqual(data);
    expect(live.dropped).toBe(0);
  });

  test('why: both entries open split the replies between two readers, and a 1 KiB read gets no reply', async () => {
    await simWrite(sim!.root, PATH, data);
    const serial = new FakeSerial(sim!.relay, { identity: stock, twin: 'granted' });
    client = await connectWebSerial({ serial, filters: RELEASE_FILTERS, probeMs: 800 });
    await serial.twin!.open({ ...SERIAL_OPEN }); // a second reader, past WebSerialTransport's refusal
    const thief = serial.twin!.readable!.getReader();
    let stolen = 0;
    void (async () => {
      try {
        for (;;) {
          const { value, done } = await thief.read();
          if (done) break;
          stolen += value?.length ?? 0;
        }
      } catch {
        /* closed */
      }
    })();
    const read = (): Promise<unknown> =>
      client!.readRange(PATH, 0, 1024, { timeoutMs: 1500 }).then(
        () => 'ok',
        (e: { code?: string }) => e.code,
      );
    const results = [await expire(read(), () => stolen > 0), await read()]; // the first reply went to the thief
    expect(results).toEqual(['timeout', 'ok']); // the bursts take turns: one reply lost, the next one read
    expect(stolen).toBeGreaterThan(0);
    await thief.cancel();
    thief.releaseLock();
    await serial.twin!.close();
  });
});
