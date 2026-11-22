// The call layer against a scripted device in memory: hello and the 1.1 requirement, the cmds
// check, typed errors, timeouts, stale replies, one request at a time, cancellation, events,
// chunk limits. No simulator.
import { describe, expect, test } from 'vitest';
import { Client, XyfError, decodeScreen, parseSlot, SCREEN } from '../src/index.ts';
import type { Transport, TransportSink } from '../src/index.ts';
import * as v1 from '../src/v1.ts';

const PRODUCT_HELLO = v1.GOLDEN.find((g) => g.message === 'hello' && g.direction === 'reply')!.fields['info'] as string;
const PRE_V1_HELLO = 'XYF1;service=usb;role=b;proto=1;install=stock-88a06a7e';

function le32(n: number): Uint8Array {
  return Uint8Array.of(n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff, (n >>> 24) & 0xff);
}
function cat(...parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}
function withoutCommand(text: string, type: number): string {
  const cmds = BigInt('0x' + /cmds=([0-9a-f]{32})/.exec(text)![1]!) & ~(BigInt(1) << BigInt(type));
  return text.replace(/cmds=[0-9a-f]{32}/, 'cmds=' + cmds.toString(16).padStart(32, '0'));
}

type Handler = (f: v1.Frame, dev: FakeDevice) => void;

/** A device in memory: answers hello, hands every other request to its handler. */
class FakeDevice implements Transport {
  sent: v1.Frame[] = [];
  outstanding = 0;
  mostOutstanding = 0;
  ended = false;
  private sink: TransportSink | null = null;
  private readonly parser = new v1.FrameParser();
  private readonly handler: Handler;
  private readonly hello: string | null;

  /** hello null: a device that answers nothing. */
  constructor(handler: Handler = () => undefined, hello: string | null = PRODUCT_HELLO) {
    this.handler = handler;
    this.hello = hello;
  }

  start(sink: TransportSink): void {
    this.sink = sink;
  }

  async write(data: Uint8Array): Promise<void> {
    if (this.ended) throw new Error('closed');
    for (const f of this.parser.feed(data)) {
      this.sent.push(f);
      this.outstanding++;
      this.mostOutstanding = Math.max(this.mostOutstanding, this.outstanding);
      if (f.type === v1.TYPE.hello) {
        if (this.hello !== null) this.reply(f, 0, new TextEncoder().encode(this.hello));
      } else this.handler(f, this);
    }
  }

  async close(): Promise<void> {
    this.end();
  }

  end(): void {
    if (this.ended) return;
    this.ended = true;
    this.sink?.closed();
  }

  /** The reply to f with status and fields, after delayMs (0: on the next turn). */
  reply(f: v1.Frame, status: number, fields: Uint8Array = new Uint8Array(0), delayMs = 0): void {
    setTimeout(() => {
      this.outstanding--;
      this.push(v1.encodeFrame(f.type | v1.REPLY, f.seq, cat(le32(status), fields)));
    }, delayMs);
  }

  push(bytes: Uint8Array): void {
    if (!this.ended) this.sink?.data(bytes);
  }

  types(): number[] {
    return this.sent.map((f) => f.type);
  }
}

async function connected(dev: FakeDevice): Promise<Client> {
  return Client.connect(async () => dev, { timeoutMs: 1000 });
}

describe('connect', () => {
  test('hello gives the protocol, version, image and limits', async () => {
    const client = await connected(new FakeDevice());
    expect(client.info.proto).toBe(v1.VERSION);
    expect(client.info.version).toBe(/;version=([0-9.]+);/.exec(PRODUCT_HELLO)![1]);
    expect(client.info.image).toBe(/;image=([0-9a-f]{8});/.exec(PRODUCT_HELLO)![1]);
    expect(client.info.limits).toEqual({ payload: 4160, read: 1024, stage: 7340032 });
    expect(client.info.dev).toBe(true);
  });

  test('a pre-1.1 device: update XY Forever', async () => {
    const dev = new FakeDevice(() => undefined, PRE_V1_HELLO);
    const error = await connected(dev).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(XyfError);
    expect(error).toMatchObject({ code: 'update_required', message: expect.stringMatching(/update XY Forever/) });
    expect(dev.ended).toBe(true);
  });

  test('another protocol major: update this app', async () => {
    const dev = new FakeDevice(() => undefined, PRODUCT_HELLO.replace(/proto=1\.[0-9]+/, 'proto=2.0'));
    await expect(connected(dev)).rejects.toMatchObject({
      code: 'protocol',
      message: expect.stringMatching(/update this app/),
    });
  });

  test('a port that never answers: timeout, opened again meanwhile', async () => {
    let opens = 0;
    const silent = async (): Promise<Transport> => {
      opens++;
      return new FakeDevice(() => undefined, null);
    };
    const start = Date.now();
    await expect(Client.connect(silent, { timeoutMs: 600, helloEveryMs: 100 })).rejects.toMatchObject({
      code: 'timeout',
    });
    expect(Date.now() - start).toBeLessThan(1500);
    expect(opens).toBeGreaterThanOrEqual(1);
  });
});

describe('calls', () => {
  test('a command missing from cmds is refused before sending', async () => {
    const dev = new FakeDevice(() => undefined, withoutCommand(PRODUCT_HELLO, v1.TYPE['tfw.check']));
    const client = await connected(dev);
    await expect(client.tfwCheck('/yaffs2/x.tfw')).rejects.toMatchObject({
      code: 'update_required',
      command: 'tfw.check',
    });
    expect(dev.types()).not.toContain(v1.TYPE['tfw.check']);
  });

  test.each([
    ['refused', 14],
    ['busy', 15],
    ['cancelled', 16],
    ['unsupported', 2],
    ['link', 12],
    ['not_found', 8],
    ['unknown_status', 99],
  ] as const)('status %s becomes a typed error', async (code, status) => {
    const client = await connected(new FakeDevice((f, dev) => dev.reply(f, status)));
    await expect(client.readRange('/yaffs2/a', 0, 16)).rejects.toMatchObject({ code, status, command: 'file.read' });
  });

  test('an error reply keeps its on_error fields', async () => {
    const client = await connected(new FakeDevice((f, dev) => dev.reply(f, 14, cat(le32(0), le32(0)))));
    await expect(
      client.call('file.write_staged', { path: '/yaffs2/x', sha256: new Uint8Array(32) }),
    ).rejects.toMatchObject({ code: 'refused', fields: { written: 0, readback_crc: 0 } });
  });

  test("no reply: timeout within the call's timeout; the late reply is dropped", async () => {
    let n = 0;
    const dev = new FakeDevice((f, d) => d.reply(f, 0, Uint8Array.of(n++), n === 1 ? 300 : 0));
    const client = await connected(dev);
    const start = Date.now();
    await expect(client.readRange('/yaffs2/a', 0, 1, { timeoutMs: 100 })).rejects.toMatchObject({ code: 'timeout' });
    expect(Date.now() - start).toBeLessThan(250);
    await new Promise((ok) => setTimeout(ok, 300)); // the late reply arrives now
    expect(await client.readRange('/yaffs2/a', 0, 1)).toEqual(Uint8Array.of(1));
    expect(client.link.dropped).toBe(1);
  });

  test('one request at a time', async () => {
    const dev = new FakeDevice((f, d) => d.reply(f, 0, Uint8Array.of(f.seq), 20));
    const client = await connected(dev);
    const reads = await Promise.all([0, 1, 2, 3].map((i) => client.readRange('/yaffs2/a', i, 1)));
    expect(reads.map((r) => r.length)).toEqual([1, 1, 1, 1]);
    expect(dev.mostOutstanding).toBe(1);
  });

  test('cancel: in flight, before sending, and between chunks', async () => {
    const dev = new FakeDevice((f, d) => d.reply(f, 0, new Uint8Array(1024), 50));
    const client = await connected(dev);
    const inFlight = new AbortController();
    setTimeout(() => inFlight.abort(), 10);
    await expect(client.readRange('/yaffs2/a', 0, 1024, { signal: inFlight.signal })).rejects.toMatchObject({
      code: 'aborted',
    });
    const before = dev.sent.length;
    await expect(client.readRange('/yaffs2/a', 0, 1024, { signal: AbortSignal.abort() })).rejects.toMatchObject({
      code: 'aborted',
    });
    expect(dev.sent.length).toBe(before);
    const between = new AbortController();
    await expect(
      client.readFile('/yaffs2/a', { signal: between.signal, onProgress: () => between.abort() }),
    ).rejects.toMatchObject({ code: 'aborted' });
    expect(dev.sent.length).toBe(before + 1);
  });

  test('the link drops mid-request: disconnected, and later calls too', async () => {
    const client = await connected(new FakeDevice((_, d) => setTimeout(() => d.end(), 10)));
    await expect(client.readRange('/yaffs2/a', 0, 1)).rejects.toMatchObject({ code: 'disconnected' });
    expect(client.connected).toBe(false);
    await expect(client.readRange('/yaffs2/a', 0, 1)).rejects.toMatchObject({ code: 'disconnected' });
  });

  test('log events reach listeners; unknown events are ignored', async () => {
    const dev = new FakeDevice((f, d) => {
      d.push(v1.encodeFrame(0xd5, 0, Uint8Array.of(1, 2, 3)));
      d.push(v1.encodeFrame(v1.TYPE.log, 0, v1.encodeLogEvent({ zero: 0, ms: 5, text: 'xyf install step 1' })));
      d.reply(f, 0, cat(le32(1), le32(1), le32(8)));
    });
    const client = await connected(dev);
    const lines: string[] = [];
    client.onLog((e) => lines.push(e.text));
    expect(await client.bootInstall(new Uint8Array(32))).toEqual({ written: 1, partition: 1, sectors: 8 });
    expect(lines).toEqual(['xyf install step 1']);
  });

  test('#65: waiting is sent again until the device is answered; declined is not allowed', async () => {
    let waits = 2;
    const dev = new FakeDevice((f, d) =>
      d.reply(f, waits-- > 0 ? v1.STATUS.waiting : 0, cat(le32(1), le32(1), le32(8))),
    );
    const client = await connected(dev);
    expect([client.info.ask, client.info.trust]).toEqual([true, 'none']);
    const asks: boolean[] = [];
    client.onAsk((on) => asks.push(on));
    expect(await client.bootInstall(new Uint8Array(32))).toEqual({ written: 1, partition: 1, sectors: 8 });
    expect([asks, dev.types().filter((t) => t === v1.TYPE['boot.install']).length]).toEqual([[true, false], 3]);
    const refused = await connected(new FakeDevice((f, d) => d.reply(f, v1.STATUS.declined)));
    await expect(refused.bootInstall(new Uint8Array(32))).rejects.toMatchObject({
      code: 'declined',
      message: expect.stringContaining('not allowed'),
    });
  });

  test('staging uses the hello limits: chunk size and capacity', async () => {
    const hello = PRODUCT_HELLO.replace('payload=4160', 'payload=600').replace('stage=7340032', 'stage=5000');
    let received = 0;
    const dev = new FakeDevice((f, d) => {
      if (f.type === v1.TYPE['stage.data']) {
        received += f.payload.length - 4;
        d.reply(f, 0, le32(received));
      } else d.reply(f, 0);
    }, hello);
    const client = await connected(dev);
    const data = Uint8Array.from({ length: 2000 }, (_, i) => i & 0xff);
    const staged = await client.stage(data);
    expect(staged.crc32).toBe(v1.crc32(data));
    const sizes = dev.sent.filter((f) => f.type === v1.TYPE['stage.data']).map((f) => f.payload.length - 4);
    expect(sizes).toEqual([596, 596, 596, 212]);
    const sent = dev.sent.length;
    await expect(client.stage(new Uint8Array(5001))).rejects.toMatchObject({ code: 'too_large' });
    expect(dev.sent.length).toBe(sent);
  });
});

describe('helpers', () => {
  test('decodeScreen: columns right to left, RGB565 to RGBA', () => {
    const raw = new Uint8Array(SCREEN.bytes);
    const at = ((SCREEN.width - 1 - 3) * SCREEN.stride + 5) * 2; // pixel x 3, y 5
    raw[at] = 0x04;
    raw[at + 1] = 0x21; // 0x2104
    const { width, height, rgba } = decodeScreen(raw);
    const o = (5 * width + 3) * 4;
    expect([width, height]).toEqual([480, 216]);
    expect([...rgba.subarray(o, o + 4)]).toEqual([33, 32, 33, 255]);
    expect([...rgba.subarray(0, 4)]).toEqual([0, 0, 0, 255]);
  });

  test('parseSlot: the CRC line guards the state file', () => {
    const body = new TextEncoder().encode('default=opxy-121\nseq=3\n');
    const slot = cat(body, new TextEncoder().encode(`crc32=${v1.crc32(body).toString(16).padStart(8, '0')}\n`));
    expect(parseSlot(slot)).toEqual({ seq: 3, fields: { default: 'opxy-121', seq: '3' } });
    slot[0] = 0x44;
    expect(parseSlot(slot)).toBeNull();
  });
});
