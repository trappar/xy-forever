// The development relay (test/relay.ts) and the WebSocket transport (src/websocket.ts) under Node:
// first against a plain TCP server standing in for the simulator, then the call layer through the
// relay to the ws simulator (test/simulators.ts). Relay ports: XYF_TS_RELAY_PORT + 2 and + 3
// (default 7752, 7753); the stand-in listens on XYF_TS_SIM_PORT + 48 and + 49 (7748, 7749).
import * as net from 'node:net';
import { afterAll, beforeAll, describe, expect, inject, test } from 'vitest';
import { WebSocket as WsClient } from 'ws';
import { control, startRelay } from './relay.ts';
import type { Relay } from './relay.ts';
import { WebSocketTransport, webSocketOpener } from '../src/dev.ts';
import { Client, VERSION, isXyfError } from '../src/index.ts';

const sims = inject('sims');
const sim = sims.ws;
const RELAY = parseInt(process.env['XYF_TS_RELAY_PORT'] ?? '7750', 10);
const STAND_IN = parseInt(process.env['XYF_TS_SIM_PORT'] ?? '7700', 10) + 48;

function listen(port: number, onSocket: (s: net.Socket) => void): Promise<net.Server> {
  return new Promise((resolve, reject) => {
    const server = net.createServer(onSocket);
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => resolve(server));
  });
}
function closeServer(server: net.Server | undefined): Promise<void> {
  return new Promise((done) => { if (!server) { done(); return; } server.close(() => done()); });
}
function sinkOf(t: WebSocketTransport): { bytes: number[]; closed: Promise<Error | undefined> } {
  const bytes: number[] = [];
  const closed = new Promise<Error | undefined>((resolve) => t.start({ data: (b) => bytes.push(...b), closed: resolve }));
  return { bytes, closed };
}
async function until(check: () => boolean, ms = 5000): Promise<void> {
  for (const end = Date.now() + ms; !check(); await new Promise((ok) => setTimeout(ok, 10))) {
    if (Date.now() > end) throw new Error('condition not met in time');
  }
}

describe('the relay, against a TCP stand-in for the simulator', () => {
  let relay: Relay;
  let target: net.Server;
  let controlServer: net.Server;
  let peers: net.Socket[] = [];
  let listening = 1;
  beforeAll(async () => {
    target = await listen(STAND_IN, (s) => { peers.push(s); s.on('data', (d) => s.write(Buffer.from([...(d as Buffer)].reverse()))); });
    controlServer = await listen(STAND_IN + 1, (s) => s.on('data', () => s.end(JSON.stringify({ listening }) + '\n')));
    relay = await startRelay({ port: RELAY + 3, target: STAND_IN });
  });
  afterAll(async () => {
    await relay?.close();
    for (const p of peers) p.destroy();
    await Promise.all([closeServer(target), closeServer(controlServer)]);
  });

  test('bytes both ways; the page closing ends the TCP connection', async () => {
    peers = [];
    const t = await WebSocketTransport.open(relay.url);
    const { bytes, closed } = sinkOf(t);
    await t.write(new Uint8Array([1, 2, 3]));
    await until(() => bytes.length === 3);
    expect(bytes).toEqual([3, 2, 1]);
    const peerGone = new Promise((ok) => peers[0]!.once('close', ok));
    await t.close();
    await peerGone;
    expect(await closed).toBeUndefined();
    await expect(t.write(new Uint8Array([1]))).rejects.toThrow(/closed/);
  });

  test('the simulator closing the port closes the WebSocket cleanly', async () => {
    peers = [];
    const t = await WebSocketTransport.open(relay.url);
    const { closed } = sinkOf(t);
    await until(() => peers.length === 1);
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
    const away = await startRelay({ port: RELAY + 2, target: STAND_IN - 1 });   // nothing listens there
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
    relay = await startRelay({ port: RELAY + 2, target: sim!.port });
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
