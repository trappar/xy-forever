// Development and tests only: a WebSocket-to-TCP relay, so that a page reaches the device simulator
// (python -m xy_forever.sim) as it reaches the OP-XY through Web Serial. Node only; uses `ws`.
//   ws://127.0.0.1:PORT/  each WebSocket is one TCP connection to the simulator's endpoint; binary
//                         frames only, in both directions; either side's close closes the other.
//                         The upgrade is answered only once the TCP connection is up (502 if not).
//   GET /status           {"attached": true|false}: the simulator's USB endpoint listens (its
//                         control port's `status`), what the fake navigator.serial calls attached.
// A test's Tap sees (and may answer or hold back) each message on the relay itself, in Node.
// Bound to 127.0.0.1; pages from other origins than localhost/127.0.0.1 are refused (403).
// Users: the browser tests' global setup, test/websocket.spec.ts. The Vite plugin xyfRelay relays
// on the dev server itself instead: ws://HOST/xyf-relay?sim=PORT to the simulator on PORT (the demo
// page, demo/, and the browser suite's server).
import { createServer } from 'node:http';
import type { IncomingMessage, Server as HttpServer } from 'node:http';
import * as net from 'node:net';
import type { Duplex } from 'node:stream';
import type { Plugin } from 'vite';
import { WebSocket, WebSocketServer } from 'ws';

export interface RelayOptions {
  /** The relay's port on 127.0.0.1 (0, the default: a free one; Relay.url names it). */
  port?: number;
  /** The simulator's endpoint port on 127.0.0.1. */
  target: number;
  /** The simulator's control port, for GET /status (default target + 1). */
  control?: number;
  /** Makes each connection's Tap; Relay.tap changes it later. */
  tap?: () => Tap;
}

/** A test's look at one WebSocket's bytes, on the relay itself (no browser round trip per message). */
export interface Tap {
  /**
   * Each message from the page, before it goes to the simulator; one at a time, in order, each awaited. false keeps
   * it from the simulator: the tap may answer for the device instead (page.send) or end the connection (page.close).
   * device.exchange: the tap's own talk with the simulator first (data sent, then each message back given to until,
   * not to the page, until it returns true).
   */
  up?(
    data: Buffer,
    page: { send(data: Uint8Array): void; close(): void },
    device: { exchange(data: Uint8Array, until: (reply: Buffer) => boolean): Promise<void> },
  ): boolean | void | Promise<boolean | void>;
  /** Each message from the simulator, before it goes to the page; awaited in order with up. */
  down?(data: Buffer): void | Promise<void>;
}

export interface Relay {
  /** ws://127.0.0.1:PORT */
  readonly url: string;
  /** Makes the tap of each connection from now on (startRelay's tap option, or set later); null: bytes pass untouched. */
  tap: (() => Tap) | null;
  /** What a tap threw (its connection was closed then): a test checks it is empty. */
  readonly tapErrors: unknown[];
  close(): Promise<void>;
}

const LOOPBACK_PAGE = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:[0-9]+)?$/;

/** Node clients send no Origin; browsers do, and only local pages may use the relay. */
function allowed(origin: string | undefined): boolean {
  return origin === undefined || LOOPBACK_PAGE.test(origin);
}

/** One line to a simulator's control port; its JSON reply. */
export function control(port: number, line: string, timeoutMs = 3000): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const sock = net.createConnection({ host: '127.0.0.1', port });
    let text = '';
    const timer = setTimeout(() => {
      sock.destroy();
      reject(new Error(`control ${line}: no answer`));
    }, timeoutMs);
    sock.on('connect', () => sock.write(line + '\n'));
    sock.on('data', (d: Buffer) => {
      text += d.toString();
      if (text.endsWith('\n')) sock.end();
    });
    sock.on('error', (e) => {
      clearTimeout(timer);
      reject(e);
    });
    sock.on('close', () => {
      clearTimeout(timer);
      try {
        resolve(JSON.parse(text) as Record<string, unknown>);
      } catch {
        reject(new Error(`control ${line}: ${text}`));
      }
    });
  });
}

function refuse(socket: Duplex, status: string): void {
  socket.end(`HTTP/1.1 ${status}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
}

/** Pairs one WebSocket with one TCP connection until either closes; a tap sees each message on the way. */
function pair(ws: WebSocket, tcp: net.Socket, tap: Tap | null = null, errors: unknown[] = []): void {
  const toPage = (data: Uint8Array): void => {
    if (ws.readyState === WebSocket.OPEN) ws.send(data, { binary: true });
  };
  const page = {
    send: toPage,
    close: () => {
      if (ws.readyState === WebSocket.OPEN) ws.close(1000, 'the device closed the port');
    },
  };
  let order = Promise.resolve(); // a tap's messages: one at a time, in order
  let own: ((data: Buffer) => void) | null = null; // the tap's device.exchange under way: its replies
  const device = {
    exchange: (data: Uint8Array, until: (reply: Buffer) => boolean): Promise<void> =>
      new Promise((done) => {
        own = (reply) => {
          if (until(reply)) {
            own = null;
            done();
          }
        };
        tcp.write(data);
      }),
  };
  const tapped = (work: () => Promise<void> | void): void => {
    order = order.then(work).catch((error: unknown) => {
      errors.push(error);
      ws.close(1011, 'the tap failed');
    });
  };
  tcp.on('data', (data: Buffer) => {
    if (own) {
      own(data);
      return;
    }
    if (!tap?.down) {
      toPage(data);
      return;
    }
    tapped(async () => {
      await tap.down!(data);
      toPage(data);
    });
  });
  tcp.on('error', () => undefined); // 'close' follows
  tcp.on('close', () => (tap ? tapped(page.close) : page.close())); // after the tapped messages before it
  ws.on('message', (data: Buffer, isBinary: boolean) => {
    if (!isBinary) {
      ws.close(1003, 'binary frames only');
      return;
    }
    if (!tap?.up) {
      tcp.write(data);
      return;
    }
    tapped(async () => {
      if ((await tap.up!(data, page, device)) !== false && !tcp.destroyed) tcp.write(data);
    });
  });
  ws.on('error', () => undefined); // 'close' follows
  ws.on('close', () => tcp.destroy());
}

function newServer(): WebSocketServer {
  return new WebSocketServer({ noServer: true, perMessageDeflate: false, maxPayload: 1 << 20 });
}

/** Answers the upgrade once a TCP connection to the simulator on target is up, then pairs the two. */
function upgrade(
  wss: WebSocketServer,
  req: IncomingMessage,
  socket: Duplex,
  head: Buffer,
  target: number,
  tap: (() => Tap) | null = null,
  errors: unknown[] = [],
): void {
  socket.on('error', () => undefined);
  if (!allowed(req.headers.origin)) {
    refuse(socket, '403 Forbidden');
    return;
  }
  const tcp = net.createConnection({ host: '127.0.0.1', port: target });
  socket.once('close', () => tcp.destroy()); // the page gave up, or the pair ended
  tcp.once('error', () => refuse(socket, '502 Bad Gateway')); // the simulator does not listen
  tcp.once('connect', () => {
    tcp.removeAllListeners('error');
    tcp.setNoDelay(true);
    wss.handleUpgrade(req, socket, head, (ws) => pair(ws, tcp, tap?.() ?? null, errors));
  });
}

export function startRelay(options: RelayOptions): Promise<Relay> {
  const controlPort = options.control ?? options.target + 1;
  const wss = newServer();
  const server = createServer((req, res) => {
    const origin = req.headers.origin;
    if (!allowed(origin)) {
      res.writeHead(403).end();
      return;
    }
    const headers: Record<string, string> = { 'content-type': 'application/json', 'cache-control': 'no-store' };
    if (origin) {
      headers['access-control-allow-origin'] = origin;
      headers['vary'] = 'Origin';
    }
    if (req.method !== 'GET' || req.url !== '/status') {
      res.writeHead(404, headers).end('{}');
      return;
    }
    control(controlPort, 'status', 2000)
      .then(
        (s) => s['listening'] === 1,
        () => false,
      )
      .then((attached) => res.writeHead(200, headers).end(JSON.stringify({ attached })));
  });
  const relay = { tap: options.tap ?? null, tapErrors: [] as unknown[] };
  server.on('upgrade', (req: IncomingMessage, socket: Duplex, head: Buffer) =>
    upgrade(wss, req, socket, head, options.target, relay.tap, relay.tapErrors),
  );
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(options.port ?? 0, '127.0.0.1', () => {
      server.off('error', reject);
      resolve(
        Object.assign(relay, {
          url: `ws://127.0.0.1:${(server.address() as net.AddressInfo).port}`,
          close: () =>
            new Promise<void>((done) => {
              for (const ws of wss.clients) ws.terminate();
              wss.close();
              server.close(() => done());
              server.closeAllConnections();
            }),
        }),
      );
    });
  });
}

/** The path of xyfRelay's WebSocket on the dev server. */
export const RELAY_PATH = '/xyf-relay';

/**
 * Vite plugin: the relay on the dev server's own port. ws://HOST/xyf-relay?sim=PORT is one TCP
 * connection to the simulator on 127.0.0.1:PORT (1024-65535); local pages only, as startRelay.
 * Other upgrades (Vite's HMR, Vitest's) are left to their owners.
 */
export function xyfRelay(): Plugin {
  return {
    name: 'xyf-relay',
    apply: 'serve',
    configureServer(server) {
      const http = server.httpServer as HttpServer | null;
      if (!http) return; // middleware mode: no server of its own
      const wss = newServer();
      http.on('upgrade', (req: IncomingMessage, socket: Duplex, head: Buffer) => {
        const url = new URL(req.url ?? '/', 'http://127.0.0.1');
        if (url.pathname !== RELAY_PATH) return;
        const sim = Number(url.searchParams.get('sim'));
        if (!Number.isInteger(sim) || sim < 1024 || sim > 65535) {
          socket.on('error', () => undefined);
          refuse(socket, '400 Bad Request');
          return;
        }
        upgrade(wss, req, socket, head, sim);
      });
      http.once('close', () => {
        for (const ws of wss.clients) ws.terminate();
        wss.close();
      });
    },
  };
}
