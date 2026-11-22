// Node TCP transport: the device simulator's port (python3 -m xy_forever.sim; 127.0.0.1:7341 by
// default) carries the same byte stream as the USB CDC port. Node only.
import * as net from 'node:net';
import type { Opener, Transport, TransportSink } from './transport.ts';

export class TcpTransport implements Transport {
  private readonly socket: net.Socket;
  private sink: TransportSink | null = null;
  private ended = false;
  private error: Error | undefined;

  static connect(
    host: string,
    port: number,
    options: { timeoutMs?: number; signal?: AbortSignal | undefined } = {},
  ): Promise<TcpTransport> {
    return new Promise((resolve, reject) => {
      const socket = net.createConnection({ host, port, signal: options.signal });
      const timer = setTimeout(() => {
        socket.destroy();
        reject(new Error(`connect ${host}:${port}: timed out`));
      }, options.timeoutMs ?? 2000);
      socket.once('connect', () => {
        clearTimeout(timer);
        socket.removeAllListeners('error');
        resolve(new TcpTransport(socket));
      });
      socket.once('error', (error) => {
        clearTimeout(timer);
        reject(error);
      });
    });
  }

  private constructor(socket: net.Socket) {
    this.socket = socket;
    socket.setNoDelay(true);
    socket.on('error', (error) => {
      this.error = error;
    });
    socket.on('close', () => this.finish());
  }

  start(sink: TransportSink): void {
    this.sink = sink;
    if (this.ended) {
      sink.closed(this.error);
      return;
    }
    this.socket.on('data', (chunk: Buffer) => sink.data(new Uint8Array(chunk.buffer, chunk.byteOffset, chunk.length)));
  }

  write(data: Uint8Array): Promise<void> {
    if (this.ended) return Promise.reject(new Error('the connection is closed'));
    return new Promise((resolve, reject) => this.socket.write(data, (error) => (error ? reject(error) : resolve())));
  }

  close(): Promise<void> {
    if (this.ended) return Promise.resolve();
    return new Promise((resolve) => {
      this.socket.once('close', () => resolve());
      this.socket.destroy();
    });
  }

  private finish(): void {
    if (this.ended) return;
    this.ended = true;
    this.sink?.closed(this.error);
  }
}

/** An opener for Client.connect: `HOST:PORT`, the simulator's address. */
export function tcpOpener(address: string, options: { timeoutMs?: number } = {}): Opener {
  const m = /^(.+):([0-9]+)$/.exec(address);
  if (!m) throw new RangeError(`not HOST:PORT: ${address}`);
  return (signal) => TcpTransport.connect(m[1]!, parseInt(m[2]!, 10), { ...options, signal });
}
