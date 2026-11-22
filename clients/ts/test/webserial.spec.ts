// The Web Serial transport (src/webserial.ts) and call layer against the simulator, through the fake
// navigator.serial (fake-serial.ts) and the relay; then the WebSocket transport. Files on the simulator's side are
// read and seeded in its /yaffs2 folder on the Mac (gesture.ts).
import { afterAll, beforeAll, describe, expect, inject, test } from 'vitest';
import { webSocketOpener } from '../src/dev.ts';
import {
  Client,
  USB_FILTERS,
  VERSION,
  decodeScreen,
  grantedPorts,
  isXyfError,
  requestPort,
  webSerialOpener,
} from '../src/index.ts';
import type { SerialPortLike } from '../src/index.ts';
import { FakeSerial } from './fake-serial.ts';
import { fromClick, simRead, simWrite } from './gesture.ts';
import { control } from './simulators.ts';
import type { FakePort } from './fake-serial.ts';

const sims = inject('sims');
const sim = sims.serial;
const text = (b: Uint8Array): string => new TextDecoder().decode(b);

function randomBytes(n: number): Uint8Array {
  const out = new Uint8Array(n);
  crypto.getRandomValues(out);
  return out;
}

describe.skipIf(!sim)(`Web Serial against the simulator${sim ? '' : ` (skipped: ${sims.why})`}`, () => {
  const serial = new FakeSerial(sim?.relay ?? '');
  let uninstall = (): void => undefined;
  let client: Client;
  beforeAll(() => {
    uninstall = serial.install();
  });
  afterAll(async () => {
    await client?.close();
    uninstall();
  });

  test('the chooser needs a click and offers only XY Forever', async () => {
    expect(await grantedPorts()).toEqual([]); // only the other adapter is granted
    await expect(requestPort()).rejects.toMatchObject({ name: 'SecurityError' });
    await expect(
      fromClick(() =>
        (navigator as unknown as { serial: FakeSerial }).serial.requestPort({ filters: [{ usbVendorId: 0x1234 }] }),
      ),
    ).rejects.toMatchObject({ name: 'NotFoundError' });
    const port: SerialPortLike = await fromClick(() => requestPort());
    expect(port.getInfo()).toEqual({ ...USB_FILTERS[0] }); // the product build's composite
    expect(await grantedPorts()).toEqual([port]);
  });

  test('a device on the stock serial identity (boot-time check failed, or a build before 188) is found too', async () => {
    const stock = new FakeSerial(sim?.relay ?? '', { identity: { ...USB_FILTERS[1] } });
    const undo = stock.install();
    try {
      const port: SerialPortLike = await fromClick(() => requestPort());
      expect(port.getInfo()).toEqual({ usbVendorId: 0x2367, usbProductId: 0x0021 });
      expect(await grantedPorts()).toEqual([port]);
    } finally {
      undo();
      uninstall = serial.install();
    }
  });

  test('connect and hello', async () => {
    client = await Client.connect(webSerialOpener(), { timeoutMs: 10_000 });
    expect(client.info).toMatchObject({ proto: VERSION, mode: 'product', limits: { read: 1024, payload: 4160 } });
    expect(client.info.version).toMatch(/^\d+\.\d+\.\d+$/);
    expect(client.info.image).toMatch(/^([0-9a-f]{8}|unknown)$/);
    expect(serial.xyfPorts[0]!.opens).toBe(1);
  });

  test('a file read matches the simulator', async () => {
    const onMac = simRead(sim!.root, '/yaffs2/firmware.version');
    expect(text(onMac)).toMatch(/^1\.2\.1/);
    expect(await client.readFile('/yaffs2/firmware.version')).toEqual(onMac);
    const data = randomBytes(5000);
    await simWrite(sim!.root, '/yaffs2/xy-forever/log/web/read.bin', data);
    expect(await client.readFile('/yaffs2/xy-forever/log/web/read.bin')).toEqual(data);
  });

  test('a staged write, then read back', async () => {
    const path = '/yaffs2/xy-forever/log/web/written.bin';
    const data = randomBytes(3000);
    expect(await client.writeFile(path, data)).toMatchObject({ written: 3000 });
    expect(await client.readFile(path)).toEqual(data);
    expect(simRead(sim!.root, path)).toEqual(data);
  });

  test('a screenshot decoded to RGBA, as a canvas takes it (ImageData)', async () => {
    const { width, rgba } = decodeScreen(await client.screenshot());
    const pixel = (x: number, y: number): number[] => [...rgba.subarray((y * width + x) * 4, (y * width + x) * 4 + 4)];
    // The simulator's test pattern: 40-pixel squares of 0x2104 and black.
    expect(pixel(0, 0)).toEqual([33, 32, 33, 255]);
    expect(pixel(40, 0)).toEqual([0, 0, 0, 255]);
    expect(pixel(0, 40)).toEqual([0, 0, 0, 255]);
    expect(pixel(479, 215)).toEqual(pixel(479 - 80, 215 - 80));
  });

  test('a device reboot: the port is lost, then found again without the chooser', async () => {
    const chooser = serial.chooser;
    let disconnects = 0;
    serial.addEventListener('disconnect', () => disconnects++);
    const before: FakePort = serial.xyfPorts.at(-1)!;
    await client.reboot('menu');
    expect(client.connected).toBe(false);
    expect(disconnects).toBe(1);
    expect(before.isOpen).toBe(false); // the transport closed the lost port
    await expect(client.readFile('/yaffs2/firmware.version')).rejects.toMatchObject({ code: 'disconnected' });
    expect((await client.reconnect({ timeoutMs: 20_000 })).proto).toBe(VERSION);
    const after = serial.xyfPorts.at(-1)!;
    expect(after).not.toBe(before);
    expect(after.opens).toBe(1);
    expect(serial.chooser).toBe(chooser);
    expect(text(await client.readFile('/yaffs2/firmware.version'))).toMatch(/^1\.2\.1/);
  });

  test('cable pulled mid-read: a typed error within the timeout, then reconnect', async () => {
    await simWrite(sim!.root, '/yaffs2/xy-forever/log/web/pull.bin', randomBytes(8000));
    await control(sim!.control, 'set cable_pull 10'); // 10 bytes of the next reply, then the cable goes
    const started = performance.now();
    const error = await client.readFile('/yaffs2/xy-forever/log/web/pull.bin').catch((e: unknown) => e);
    expect(isXyfError(error, 'disconnected')).toBe(true);
    expect(performance.now() - started).toBeLessThan(15_000); // file.read's timeout
    await client.reconnect({ timeoutMs: 20_000 });
    expect((await client.readFile('/yaffs2/xy-forever/log/web/pull.bin')).length).toBe(8000);
    await client.close();
  });

  test('the WebSocket transport', async () => {
    const direct = await Client.connect(webSocketOpener(sim!.relay), { timeoutMs: 10_000 });
    try {
      expect(direct.info.proto).toBe(VERSION);
      expect(text(await direct.readFile('/yaffs2/firmware.version'))).toMatch(/^1\.2\.1/);
    } finally {
      await direct.close();
    }
  });
});

describe.skipIf(!sims.preV1)(`a pre-1.1 device over Web Serial${sims.preV1 ? '' : ` (skipped: ${sims.why})`}`, () => {
  test('connect: update XY Forever', async () => {
    const uninstall = new FakeSerial(sims.preV1!.relay).install();
    try {
      await fromClick(() => requestPort());
      const error = await Client.connect(webSerialOpener(), { timeoutMs: 10_000 }).catch((e: unknown) => e);
      expect(error).toMatchObject({ code: 'update_required', message: expect.stringMatching(/update XY Forever/) });
    } finally {
      uninstall();
    }
  });
});
