// WebSocket transport: the device simulator through the development relay (test/relay.ts), from a
// browser or Node 22+ (the global WebSocket). For development and tests only (the `./dev` entry):
// an app reaches the OP-XY through Web Serial. One WebSocket carries the simulator's byte stream in
// binary messages; a close by the relay is the device going away (a restart or a pulled cable).
import type { Opener, Transport, TransportSink } from './transport.ts';

export interface WebSocketOptions {
  /** How long opening may take; default 2 s. */
  timeoutMs?: number;
  signal?: AbortSignal | undefined;
}

export class WebSocketTransport implements Transport {
  private readonly socket: WebSocket;
  private readonly gone: Promise<void>;
  private sink: TransportSink | null = null;
  private early: Uint8Array[] = [];
  private ended = false;
  private closing = false;
  private error: Error | undefined;

  /** Opens url (ws://127.0.0.1:PORT); rejects when the relay refuses, the simulator is not there, or on timeout. */
  static open(url: string, options: WebSocketOptions = {}): Promise<WebSocketTransport> {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(url);
      socket.binaryType = 'arraybuffer';
      const done = (): void => {
        clearTimeout(timer);
        options.signal?.removeEventListener('abort', cancel);
        socket.onopen = socket.onerror = socket.onclose = null;
      };
      const fail = (why: string): void => {
        done();
        socket.close();
        reject(new Error(`${url}: ${why}`));
      };
      const cancel = (): void => fail('cancelled');
      const timer = setTimeout(() => fail('timed out'), options.timeoutMs ?? 2000);
      options.signal?.addEventListener('abort', cancel, { once: true });
      socket.onopen = () => {
        done();
        resolve(new WebSocketTransport(socket));
      };
      socket.onerror = () => fail('could not connect');
      socket.onclose = (e) => fail(`closed (${e.code})`);
    });
  }

  private constructor(socket: WebSocket) {
    this.socket = socket;
    this.gone = new Promise((resolve) => {
      socket.onmessage = (e: MessageEvent) => {
        if (!(e.data instanceof ArrayBuffer)) return; // the relay sends binary frames only
        const bytes = new Uint8Array(e.data);
        if (this.sink) this.sink.data(bytes);
        else this.early.push(bytes);
      };
      socket.onerror = () => {
        this.error ??= new Error('the relay connection failed');
      };
      socket.onclose = (e: CloseEvent) => {
        if (!this.closing && e.code !== 1000)
          this.error ??= new Error(`the relay closed the connection (${e.code} ${e.reason})`);
        this.finish();
        resolve();
      };
    });
  }

  start(sink: TransportSink): void {
    this.sink = sink;
    for (const bytes of this.early.splice(0)) sink.data(bytes);
    if (this.ended) sink.closed(this.error);
  }

  async write(data: Uint8Array): Promise<void> {
    if (this.ended || this.socket.readyState !== WebSocket.OPEN) throw new Error('the connection is closed');
    this.socket.send(data as Uint8Array<ArrayBuffer>); // never a SharedArrayBuffer here
  }

  async close(): Promise<void> {
    if (this.ended) return;
    this.closing = true;
    this.socket.close(1000);
    await Promise.race([this.gone, new Promise((ok) => setTimeout(ok, 1000))]);
    this.finish();
  }

  private finish(): void {
    if (this.ended) return;
    this.ended = true;
    this.sink?.closed(this.error);
  }
}

/** An opener for Client.connect: the relay's ws:// URL. */
export function webSocketOpener(url: string, options: { timeoutMs?: number } = {}): Opener {
  return (signal) => WebSocketTransport.open(url, { ...options, signal });
}
