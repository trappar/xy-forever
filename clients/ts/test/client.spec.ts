// The call layer against a scripted device in memory: hello and the 1.1 requirement, the cmds
// check, typed errors, timeouts, stale replies, one request at a time, cancellation, events,
// chunk limits. No simulator.
import { afterEach, describe, expect, test, vi } from 'vitest';
import { Client, RESEND_MS, XyfError, decodeScreen, parseSlot, SCREEN } from '../src/index.ts';
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

// Time-outs and pauses run on Vitest's fake clock where a test turns it on: no test waits for wall time.
afterEach(() => {
  vi.useRealTimers();
});
/** work's outcome (a value, or the error it threw), and whether it settled within ms of the fake clock. */
async function within<T>(ms: number, work: Promise<T>): Promise<{ settled: boolean; outcome: Promise<T | unknown> }> {
  let settled = false;
  const outcome = work.then(
    (v) => ((settled = true), v),
    (e: unknown) => ((settled = true), e),
  );
  await vi.advanceTimersByTimeAsync(ms);
  return { settled, outcome };
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
    vi.useFakeTimers();
    const run = await within(600, Client.connect(silent, { timeoutMs: 600, helloEveryMs: 100 }));
    expect(run.settled).toBe(true);
    expect(await run.outcome).toMatchObject({ code: 'timeout' });
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
    vi.useFakeTimers();
    const run = await within(100, client.readRange('/yaffs2/a', 0, 1, { timeoutMs: 100 }));
    expect(run.settled).toBe(true);
    expect(await run.outcome).toMatchObject({ code: 'timeout' });
    await vi.advanceTimersByTimeAsync(200); // the late reply arrives now
    const next = await within(0, client.readRange('/yaffs2/a', 0, 1));
    expect(await next.outcome).toEqual(Uint8Array.of(1));
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
    const dev = new FakeDevice((f, d) => d.reply(f, 0, new Uint8Array(v1.MAX_DATA), 50)); // a whole read_stream piece
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
    vi.useFakeTimers();
    const run = await within(1100, client.bootInstall(new Uint8Array(32))); // sent again after RETRY_MS, twice
    expect(run.settled).toBe(true);
    expect(await run.outcome).toEqual({ written: 1, partition: 1, sectors: 8 });
    vi.useRealTimers();
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

describe('lost and split replies (#306)', () => {
  /** A device whose replies reach the page in pieces of 1..max bytes (seeded), as a serial port's reads may cut them. */
  class SplittingDevice extends FakeDevice {
    private seed = 1;
    max = 97;
    /** Replies whose last `cut` bytes never arrive, by request number (from 1). */
    cuts = new Map<number, number>();
    private n = 0;
    override push(bytes: Uint8Array): void {
      const cut = this.cuts.get(++this.n) ?? 0;
      const kept = bytes.subarray(0, bytes.length - cut);
      for (let at = 0; at < kept.length;) {
        this.seed = (this.seed * 1103515245 + 12345) & 0x7fffffff;
        const take = 1 + (this.seed % this.max);
        super.push(kept.slice(at, at + take));
        at += take;
      }
    }
  }
  const data = (seq: number): Uint8Array => Uint8Array.from({ length: 1024 }, (_, i) => (i * 7 + seq) & 0xff);

  test('replies cut at any byte, many to a read: every one whole, nothing dropped', async () => {
    const dev = new SplittingDevice((f, d) => d.reply(f, 0, data(f.seq)));
    const client = await connected(dev);
    for (const max of [1, 2, 7, 64, 97, 1100]) {
      dev.max = max;
      for (let i = 0; i < 40; i++) {
        const before = dev.sent.length;
        const got = await client.readRange('/yaffs2/a', 0, 1024);
        expect(got).toEqual(data(dev.sent[before]!.seq));
      }
    }
    expect(client.link.dropped).toBe(0);
  });

  test('a read whose reply loses its tail is sent once more after RESEND_MS, and answers', async () => {
    const dev = new SplittingDevice((f, d) => d.reply(f, 0, data(f.seq)));
    const client = await connected(dev);
    dev.cuts.set(2, 90); // hello was the first reply: the first read's reply loses its last 90 bytes
    vi.useFakeTimers();
    const run = await within(RESEND_MS + 10, client.readRange('/yaffs2/a', 0, 1024));
    expect(run.settled).toBe(true);
    expect(await run.outcome).toEqual(data(dev.sent.at(-1)!.seq));
    expect(dev.types().filter((t) => t === v1.TYPE['file.read'])).toHaveLength(2);
  });

  test('a write whose reply is lost is never sent twice: it times out', async () => {
    const dev = new SplittingDevice((f, d) => d.reply(f, 0));
    const client = await connected(dev);
    dev.cuts.set(2, 4);
    vi.useFakeTimers();
    const run = await within(
      RESEND_MS + 10,
      client.call('file.mkdir', { path: '/yaffs2/xy-forever/x', sha256: new Uint8Array(32) }, { timeoutMs: 20_000 }),
    );
    expect(run.settled).toBe(false);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(await run.outcome).toMatchObject({ code: 'timeout' });
    expect(dev.types().filter((t) => t === v1.TYPE['file.mkdir'])).toHaveLength(1);
  });
});

describe('read streams and pipelined writes (1.33, hello pipe)', () => {
  const FILE = Uint8Array.from({ length: 5 * v1.MAX_DATA + 1234 }, (_, i) => (i * 31 + 7) & 0xff);
  const le = (b: Uint8Array, at: number): number => new DataView(b.buffer, b.byteOffset).getUint32(at, true);
  /** A device that streams FILE (file.read_stream, file.read) and takes a streamed write into written. */
  class FileDevice extends FakeDevice {
    written = new Uint8Array(0);
    received = 0;
    cuts = new Map<number, number>(); // reply number (hello is 1) -> bytes lost at its end
    private n = 0;
    constructor(hello: string = PRODUCT_HELLO) {
      super((f, d) => (d as FileDevice).answer(f), hello);
    }
    answer(f: v1.Frame): void {
      const p = f.payload;
      if (f.type === v1.TYPE['file.read_stream'] || f.type === v1.TYPE['file.read']) {
        const offset = le(p, p.length - 8),
          length = le(p, p.length - 4);
        this.reply(f, 0, FILE.subarray(offset, offset + length));
      } else if (f.type === v1.TYPE['file.write_begin'] || f.type === v1.TYPE['stage.begin']) {
        this.written = new Uint8Array(le(p, f.type === v1.TYPE['stage.begin'] ? 0 : p.length - 40));
        this.received = 0;
        this.reply(f, 0);
      } else if (f.type === v1.TYPE['stage.end']) {
        this.reply(f, 0);
      } else if (f.type === v1.TYPE['file.write_data'] || f.type === v1.TYPE['stage.data']) {
        const offset = le(p, 0),
          piece = p.subarray(4);
        if (offset === this.received) {
          this.written.set(piece, offset);
          this.received += piece.length;
        } else if (offset + piece.length > this.received) return this.reply(f, v1.STATUS.range);
        this.reply(f, 0, le32(this.received));
      } else if (f.type === v1.TYPE['file.write_end'])
        this.reply(f, 0, cat(le32(this.received), le32(v1.crc32(this.written)), le32(0)));
      else this.reply(f, v1.STATUS.unsupported);
    }
    override push(bytes: Uint8Array): void {
      const cut = this.cuts.get(++this.n) ?? 0;
      super.push(bytes.subarray(0, bytes.length - cut));
    }
    count(type: number): number {
      return this.sent.filter((f) => f.type === type).length;
    }
  }
  const STREAM = v1.TYPE['file.read_stream'];

  test('a read stream: MAX_DATA pieces, pipe of them in flight, the whole file in order', async () => {
    const dev = new FileDevice();
    const client = await connected(dev);
    expect(client.info.hello.pipe).toBe(v1.MAX_PIPE);
    expect(await client.readFile('/yaffs2/a')).toEqual(FILE);
    expect(dev.mostOutstanding).toBe(v1.MAX_PIPE);
    expect(dev.sent.filter((f) => f.type === STREAM).every((f) => le(f.payload, f.payload.length - 4) === 4096)).toBe(
      true,
    );
    expect(dev.count(v1.TYPE['file.read'])).toBe(0);
  });

  test('older devices: file.read in max_read ranges; pipe=1: read_stream pieces of max_read, one at a time', async () => {
    const old = new FileDevice(withoutCommand(PRODUCT_HELLO.replace(';pipe=4', ''), STREAM));
    expect(await (await connected(old)).readFile('/yaffs2/a')).toEqual(FILE);
    expect([old.count(STREAM), old.count(v1.TYPE['file.read']), old.mostOutstanding]).toEqual([0, 22, 1]);
    const fallback = new FileDevice(PRODUCT_HELLO.replace(';pipe=4', ';pipe=1'));
    const client = await connected(fallback);
    expect(await client.readFile('/yaffs2/a')).toEqual(FILE);
    expect([fallback.count(STREAM), fallback.mostOutstanding]).toEqual([22, 1]);
    await client.writeFile('/yaffs2/b', FILE);
    expect(fallback.written).toEqual(FILE);
    expect([fallback.count(v1.TYPE['file.write_data']), fallback.mostOutstanding]).toEqual([22, 1]);
  });

  test('#306: a reply in flight loses its tail: sent once more as soon as a later one comes, the file whole', async () => {
    const dev = new FileDevice();
    dev.cuts.set(3, 90); // hello, the first piece (sent alone), then the second piece's reply is cut
    const client = await connected(dev);
    expect(await client.readFile('/yaffs2/a')).toEqual(FILE);
    expect(dev.count(STREAM)).toBe(6 + 3 + 1); // the pieces, the three sent past the end, the resend
  });

  test('#306: the last reply in flight loses its tail: sent once more after RESEND_MS', async () => {
    const dev = new FileDevice();
    const client = await connected(dev);
    dev.cuts.set(8, 90); // the reply that ends the file (piece 6), the last of those in flight
    vi.useFakeTimers();
    const run = await within(RESEND_MS + 10, client.readFile('/yaffs2/a'));
    expect(run.settled).toBe(true);
    expect(await run.outcome).toEqual(FILE);
  });

  test('a pipelined write: MAX_DATA pieces in flight; a lost reply is sent again and the device keeps its bytes', async () => {
    const dev = new FileDevice();
    const client = await connected(dev);
    dev.cuts.set(4, 6); // after hello and write_begin: the second piece's reply
    const progress: number[] = [];
    expect(await client.writeFile('/yaffs2/b', FILE, { onProgress: (done) => progress.push(done) })).toMatchObject({
      written: FILE.length,
    });
    expect(dev.written).toEqual(FILE);
    expect(progress).toEqual([4096, 8192, 12288, 16384, 20480, FILE.length]);
    expect(dev.count(v1.TYPE['file.write_data'])).toBe(6 + 1);
    expect(dev.mostOutstanding).toBe(v1.MAX_PIPE);
  });

  test('staging: MAX_DATA pieces, pipe of them in flight', async () => {
    const dev = new FileDevice();
    const client = await connected(dev);
    await client.stage(FILE);
    expect(dev.written).toEqual(FILE);
    expect([dev.count(v1.TYPE['stage.data']), dev.mostOutstanding]).toEqual([6, v1.MAX_PIPE]);
  });

  test('a piece the device refuses stops the write: the error, no write_end', async () => {
    const dev = new FileDevice();
    const client = await connected(dev);
    const answer = dev.answer.bind(dev);
    dev.answer = (f) =>
      f.type === v1.TYPE['file.write_data'] && le(f.payload, 0) === 8192 ? dev.reply(f, v1.STATUS.storage) : answer(f);
    await expect(client.writeFile('/yaffs2/b', FILE)).rejects.toMatchObject({ code: 'storage' });
    expect(dev.count(v1.TYPE['file.write_end'])).toBe(0);
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
