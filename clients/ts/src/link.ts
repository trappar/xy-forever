// The call layer's core: one request at a time over a transport. Picks the seq, waits for the reply
// with the same type and seq (or the timeout, or a cancel), drops stale replies, resets the frame
// parser after a timeout (docs/protocol/rules.md), and hands events (0xD0..0xDF, seq 0) to
// listeners. It sends whatever it is given: hello, the cmds check and typed codecs are client.ts.
import { XyfError } from './errors.ts';
import type { Transport } from './transport.ts';
import { FrameParser, REPLY, encodeFrame } from './v1.ts';
import type { Frame } from './v1.ts';

export interface RequestOptions {
  timeoutMs: number;
  signal?: AbortSignal | undefined;
  /** A fixed seq (conformance cases); otherwise the next of 1..255. */
  seq?: number | undefined;
}

/** Called with every frame received ('<') and every byte block sent ('>'): for debugging. */
export type Trace = (direction: '<' | '>', data: Frame | Uint8Array) => void;

interface Pending {
  type: number;
  seq: number;
  resolve(payload: Uint8Array): void;
  reject(error: Error): void;
}

export interface PipelineOptions {
  /** Requests in flight at most (hello `pipe`); 1 is one at a time. */
  depth: number;
  timeoutMs: number;
  /** The first wait for the oldest reply before that request is sent once more (#306: a lost reply tail). */
  resendMs: number;
  signal?: AbortSignal | undefined;
  /** The next request's payload; null: no more. */
  next(): Uint8Array | null;
  /** Each reply payload (status first), in request order; false: send no more and return. */
  reply(payload: Uint8Array): boolean;
}

interface Flight {
  type: number;
  accept(f: Frame): void;
  /** The link closed. */
  closed(): void;
}

interface Collector {
  frames: Frame[];
  until(frames: readonly Frame[]): boolean;
  done(): void;
}

function isEvent(f: Frame): boolean {
  return f.type >= 0xd0 && f.type <= 0xdf && f.seq === 0;
}

function aborted(signal: AbortSignal | undefined): XyfError {
  return new XyfError('aborted', 'cancelled', { cause: signal?.reason });
}

export class Link {
  readonly transport: Transport;
  /** Frames that matched nothing: stale replies, unknown frames. */
  dropped = 0;
  private readonly parser = new FrameParser();
  private readonly listeners = new Set<(event: Frame) => void>();
  private readonly closeWaiters = new Set<() => void>();
  private pending: Pending | null = null;
  private flight: Flight | null = null;
  private collector: Collector | null = null;
  private queue: Promise<unknown> = Promise.resolve();
  private seq = 0;
  private isClosed = false;
  private trace: Trace | null;

  constructor(transport: Transport, trace: Trace | null = null) {
    this.transport = transport;
    this.trace = trace;
    transport.start({ data: (bytes) => this.receive(bytes), closed: () => this.ended() });
  }

  get closed(): boolean {
    return this.isClosed;
  }

  /** Listens for events (log today); returns the unsubscribe function. */
  onEvent(listener: (event: Frame) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** One request; resolves with the reply payload (status first), whatever the status. */
  request(type: number, payload: Uint8Array, options: RequestOptions): Promise<Uint8Array> {
    return this.serial(options.signal, async () => {
      const seq = options.seq ?? this.nextSeq();
      const { signal, timeoutMs } = options;
      let timer: ReturnType<typeof setTimeout> | undefined;
      let onAbort: (() => void) | undefined;
      const reply = new Promise<Uint8Array>((resolve, reject) => {
        this.pending = { type, seq, resolve, reject };
        timer = setTimeout(() => {
          this.parser.reset(); // a late reply then cannot complete a frame
          reject(new XyfError('timeout', `no reply to request 0x${type.toString(16)} within ${timeoutMs} ms`));
        }, timeoutMs);
        onAbort = () => {
          this.parser.reset();
          reject(aborted(signal));
        };
        signal?.addEventListener('abort', onAbort, { once: true });
      });
      reply.catch(() => undefined); // settled below, or abandoned when send fails
      try {
        await this.send(encodeFrame(type, seq, payload));
        return await reply;
      } finally {
        clearTimeout(timer);
        if (onAbort) signal?.removeEventListener('abort', onAbort);
        this.pending = null;
      }
    });
  }

  /**
   * Requests of one type with up to options.depth in flight (protocol 1.33, hello `pipe`: file.read_stream and
   * file.write_data only). The device answers in order, so a reply that is missing while a later one came was lost on
   * the way: that request is sent once more (both commands answer a resend), as is the oldest when its reply has not
   * come in resendMs. Stopping early (reply returns false) leaves the requests still out unanswered: their replies are
   * dropped as stale. The caller sends a transfer's first request with request() (the #65 question, the cmds check).
   */
  pipeline(type: number, options: PipelineOptions): Promise<void> {
    return this.serial(options.signal, async () => {
      interface Out {
        seq: number;
        payload: Uint8Array;
        resent: boolean;
      }
      const out: Out[] = [];
      const early = new Map<number, Uint8Array>();
      let wake: (() => void) | null = null;
      let failed: Error | null = null;
      let more = true;
      const send = async (entry: Out): Promise<void> => {
        entry.seq = this.nextSeq();
        await this.send(encodeFrame(type, entry.seq, entry.payload));
      };
      const fill = async (): Promise<void> => {
        while (more && out.length < options.depth) {
          const payload = options.next();
          if (!payload) {
            more = false;
            return;
          }
          const entry = { seq: 0, payload, resent: false };
          out.push(entry);
          await send(entry);
        }
      };
      let resendHead = false;
      this.flight = {
        type,
        accept: (f) => {
          const at = out.findIndex((e) => e.seq === f.seq);
          if (at < 0) {
            this.dropped++;
            return;
          }
          if (f.payload.length < 4) failed = new XyfError('protocol', 'a reply without a status');
          early.set(f.seq, f.payload);
          const head = out[0];
          if (at > 0 && head && !early.has(head.seq) && !head.resent) resendHead = true;
          wake?.();
        },
        closed: () => wake?.(),
      };
      const onAbort = (): void => {
        failed = aborted(options.signal);
        wake?.();
      };
      options.signal?.addEventListener('abort', onAbort, { once: true });
      try {
        await fill();
        while (out.length) {
          if (failed) throw failed;
          if (this.isClosed) throw new XyfError('disconnected', 'the device went away');
          const head = out[0] as Out;
          const got = early.get(head.seq);
          if (got) {
            early.delete(head.seq);
            out.shift();
            if (!options.reply(got)) return;
            await fill();
            continue;
          }
          if (resendHead) {
            resendHead = false;
            head.resent = true;
            await send(head);
            continue;
          }
          const waitMs = head.resent ? options.timeoutMs : Math.min(options.timeoutMs, options.resendMs);
          let timer: ReturnType<typeof setTimeout> | undefined;
          const timedOut = await new Promise<boolean>((resolve) => {
            wake = () => resolve(false);
            timer = setTimeout(() => resolve(true), waitMs);
          });
          clearTimeout(timer);
          wake = null;
          if (!timedOut || early.has(head.seq) || resendHead || failed) continue;
          if (head.resent) {
            this.parser.reset();
            throw new XyfError('timeout', `no reply to request 0x${type.toString(16)} within ${options.timeoutMs} ms`);
          }
          this.parser.reset(); // a cut frame then cannot swallow the next reply
          head.resent = true;
          await send(head);
        }
      } finally {
        this.flight = null;
        options.signal?.removeEventListener('abort', onAbort);
      }
    });
  }

  /**
   * Raw bytes for the frame layer (conformance): sends each chunk (gapMs apart), then collects
   * every frame that is not an event until until(frames) holds or timeoutMs passes.
   */
  exchange(
    chunks: readonly Uint8Array[],
    options: { timeoutMs: number; gapMs?: number; until(frames: readonly Frame[]): boolean },
  ): Promise<Frame[]> {
    return this.serial(undefined, async () => {
      const frames: Frame[] = [];
      let timer: ReturnType<typeof setTimeout> | undefined;
      const finished = new Promise<void>((done) => {
        this.collector = { frames, until: options.until, done };
        timer = setTimeout(done, options.timeoutMs);
      });
      try {
        for (const chunk of chunks) {
          await this.send(chunk);
          if (options.gapMs) await new Promise((ok) => setTimeout(ok, options.gapMs));
        }
        if (!options.until(frames)) await finished;
        return frames;
      } finally {
        clearTimeout(timer);
        this.collector = null;
      }
    });
  }

  /** Whether the link closes within ms (a restart drops it). */
  waitClosed(ms: number): Promise<boolean> {
    if (this.isClosed) return Promise.resolve(true);
    return new Promise((resolve) => {
      const wake = (): void => {
        clearTimeout(timer);
        this.closeWaiters.delete(wake);
        resolve(true);
      };
      const timer = setTimeout(() => {
        this.closeWaiters.delete(wake);
        resolve(this.isClosed);
      }, ms);
      this.closeWaiters.add(wake);
    });
  }

  async close(): Promise<void> {
    if (!this.isClosed) await this.transport.close();
    this.ended();
  }

  private nextSeq(): number {
    this.seq = (this.seq % 255) + 1;
    return this.seq;
  }

  private async send(bytes: Uint8Array): Promise<void> {
    if (this.isClosed) throw new XyfError('disconnected', 'the device is not connected');
    this.trace?.('>', bytes);
    try {
      await this.transport.write(bytes);
    } catch (error) {
      throw new XyfError('disconnected', 'the device is not connected', { cause: error });
    }
  }

  /** Runs fn after every earlier request (one outstanding request at a time). */
  private serial<T>(signal: AbortSignal | undefined, fn: () => Promise<T>): Promise<T> {
    const run = this.queue.then(() => {
      if (signal?.aborted) throw aborted(signal);
      if (this.isClosed) throw new XyfError('disconnected', 'the device is not connected');
      return fn();
    });
    this.queue = run.catch(() => undefined);
    return run;
  }

  private receive(bytes: Uint8Array): void {
    for (const f of this.parser.feed(bytes)) {
      this.trace?.('<', f);
      const p = this.pending;
      if (p && f.type === (p.type | REPLY) && f.seq === p.seq) {
        this.pending = null;
        if (f.payload.length < 4) p.reject(new XyfError('protocol', 'a reply without a status'));
        else p.resolve(f.payload);
      } else if (this.flight && f.type === (this.flight.type | REPLY)) {
        this.flight.accept(f);
      } else if (isEvent(f)) {
        for (const listener of this.listeners) listener(f);
      } else if (this.collector) {
        this.collector.frames.push(f);
        if (this.collector.until(this.collector.frames)) this.collector.done();
      } else {
        this.dropped++; // a stale reply (old seq) or a frame nobody waits for
      }
    }
  }

  private ended(): void {
    if (this.isClosed) return;
    this.isClosed = true;
    this.pending?.reject(new XyfError('disconnected', 'the device went away'));
    this.pending = null;
    this.flight?.closed();
    this.collector?.done();
    for (const wake of [...this.closeWaiters]) wake();
  }
}
