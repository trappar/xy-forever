// The development relay (test/relay.ts) and the WebSocket transport (src/websocket.ts) under Node:
// first against a plain TCP server standing in for the simulator, then the call layer through the
// relay to the ws simulator (test/simulators.ts). The relays and the stand-in listen on free ports the
// system picks.
import * as net from 'node:net';
import { afterAll, beforeAll, describe, expect, inject, test } from 'vitest';
import { WebSocket as WsClient } from 'ws';
import { control, startRelay } from './relay.ts';
import type { Relay } from './relay.ts';
import { WebSocketTransport, webSocketOpener } from '../src/dev.ts';
import { Client, VERSION, isXyfError } from '../src/index.ts';

const sims = inject('sims');
const sim = sims.ws;

function listen(port: number, onSocket: (s: net.Socket) => void): Promise<net.Server> {
  return new Promise((resolve, reject) => {
    const server = net.createServer(onSocket);
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => resolve(server)); // 0: a free port (portOf)
  });
}
const portOf = (server: net.Server): number => (server.address() as net.AddressInfo).port;
function closeServer(server: net.Server | undefined): Promise<void> {
  return new Promise((done) => {
    if (!server) {
      done();
      return;
    }
    server.close(() => done());
  });
}
/** What the transport delivers; got(n): once n bytes have arrived (on the data event, no polling). */
function sinkOf(t: WebSocketTransport): {
  bytes: number[];
  got: (n: number) => Promise<void>;
  closed: Promise<Error | undefined>;
} {
  const bytes: number[] = [];
  const waiting: { n: number; ok: () => void }[] = [];
  const closed = new Promise<Error | undefined>((resolve) =>
    t.start({
      data: (b) => {
        bytes.push(...b);
        for (const w of waiting.filter((w) => bytes.length >= w.n)) w.ok();
      },
      closed: resolve,
    }),
  );
  const got = (n: number): Promise<void> =>
    bytes.length >= n ? Promise.resolve() : new Promise((ok) => waiting.push({ n, ok }));
  return { bytes, got, closed };
}

describe('the relay, against a TCP stand-in for the simulator', () => {
  let relay: Relay;
  let target: net.Server;
  let controlServer: net.Server;
  let peers: net.Socket[] = [];
  let peerArrived: (() => void) | undefined;
  let listening = 1;
  /** The stand-in's next connection (on its connection event). */
  const nextPeer = (): Promise<void> => new Promise((ok) => (peerArrived = ok));
  beforeAll(async () => {
    target = await listen(0, (s) => {
      peers.push(s);
      peerArrived?.();
      s.on('data', (d) => s.write(Buffer.from([...(d as Buffer)].reverse())));
    });
    controlServer = await listen(0, (s) => s.on('data', () => s.end(JSON.stringify({ listening }) + '\n')));
    relay = await startRelay({ target: portOf(target), control: portOf(controlServer) });
  });
  afterAll(async () => {
    await relay?.close();
    for (const p of peers) p.destroy();
    await Promise.all([closeServer(target), closeServer(controlServer)]);
  });

  test('bytes both ways; the page closing ends the TCP connection', async () => {
    peers = [];
    const t = await WebSocketTransport.open(relay.url);
    const { bytes, got, closed } = sinkOf(t);
    await t.write(new Uint8Array([1, 2, 3]));
    await got(3);
    expect(bytes).toEqual([3, 2, 1]);
    const peerGone = new Promise((ok) => peers[0]!.once('close', ok));
    await t.close();
    await peerGone;
    expect(await closed).toBeUndefined();
    await expect(t.write(new Uint8Array([1]))).rejects.toThrow(/closed/);
  });

  test('the simulator closing the port closes the WebSocket cleanly', async () => {
    peers = [];
    const peer = nextPeer();
    const t = await WebSocketTransport.open(relay.url);
    const { closed } = sinkOf(t);
    await peer;
    peers[0]!.end();
    expect(await closed).toBeUndefined();
  });

  test('text frames, other origins and a missing simulator are refused', async () => {
    const text = new WsClient(relay.url);
    await new Promise((ok) => text.once('open', ok));
    text.send('hello');
    expect(await new Promise((ok) => text.once('close', ok))).toBe(1003);
    const foreign = new WsClient(relay.url, { origin: 'https://example.com' });
    expect(await new Promise((ok) => foreign.once('unexpected-response', (_req, res) => ok(res.statusCode)))).toBe(403);
    const local = new WsClient(relay.url, { origin: 'http://localhost:5173' });
    await new Promise((ok) => local.once('open', ok));
    local.close();
    const free = await listen(0, () => undefined);
    const nothing = portOf(free);
    await closeServer(free);
    const away = await startRelay({ target: nothing }); // nothing listens there
    try {
      await expect(WebSocketTransport.open(away.url)).rejects.toThrow(/could not connect|closed/);
    } finally {
      await away.close();
    }
  });

  test('GET /status: the control port says whether the endpoint listens', async () => {
    const url = relay.url.replace('ws:', 'http:') + '/status';
    listening = 1;
    let res = await fetch(url, { headers: { origin: 'http://127.0.0.1:63315' } });
    expect(res.headers.get('access-control-allow-origin')).toBe('http://127.0.0.1:63315');
    expect(await res.json()).toEqual({ attached: true });
    listening = 0;
    expect(await (await fetch(url)).json()).toEqual({ attached: false });
    res = await fetch(url, { headers: { origin: 'https://example.com' } });
    expect(res.status).toBe(403);
  });
});

describe.skipIf(!sim)(`the call layer through the relay${sim ? '' : ` (skipped: ${sims.why})`}`, () => {
  let relay: Relay;
  let client: Client;
  beforeAll(async () => {
    relay = await startRelay({ target: sim!.port });
    client = await Client.connect(webSocketOpener(relay.url), { timeoutMs: 5000 });
  });
  afterAll(async () => {
    await client?.close();
    await relay?.close();
  });

  test('hello and a file read', async () => {
    expect(client.info.proto).toBe(VERSION);
    expect(new TextDecoder().decode(await client.readFile('/yaffs2/firmware.version'))).toBe('1.2.1\n');
  });

  test('reboot and reconnect', async () => {
    await client.reboot('menu');
    expect(client.connected).toBe(false);
    expect((await client.reconnect({ timeoutMs: 15_000 })).proto).toBe(VERSION);
    expect(new TextDecoder().decode(await client.readFile('/yaffs2/firmware.version'))).toBe('1.2.1\n');
  });

  test('cable pulled mid-read: disconnected', async () => {
    await control(sim!.control, 'set cable_pull 10');
    const error = await client.readFile('/yaffs2/firmware.version').catch((e: unknown) => e);
    expect(isXyfError(error, 'disconnected')).toBe(true);
    await client.reconnect({ timeoutMs: 15_000 });
    expect(client.connected).toBe(true);
  });
});
