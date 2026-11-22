// Web Serial (#178): connecting without asking the owner to choose. Chrome listed two entries for one OP-XY at the
// install's "Connect and finish"; here the fake navigator.serial (fake-serial.ts) adds a stale entry with XY
// Forever's USB ids that opens and never answers, next to the simulator's live port.
import { afterEach, describe, expect, inject, test } from 'vitest';
import { CHOOSE_TEXT, RELEASE_FILTERS, USB_FILTERS, connectWebSerial } from '../src/index.ts';
import type { Client } from '../src/index.ts';
import { FakeSerial } from './fake-serial.ts';
import { fromClick } from './gesture.ts';

const sims = inject('sims');
const sim = sims.chooser;

describe.skipIf(!sim)(`choosing the port${sim ? '' : ` (skipped: ${sims.why})`}`, () => {
  const stock = USB_FILTERS[1];
  let client: Client | null = null;
  afterEach(async () => {
    await client?.close();
    client = null;
  });

  test('two granted entries: the stale one is passed over, the live one used, no chooser', async () => {
    const serial = new FakeSerial(sim!.relay, { identity: stock, stale: 'granted' });
    expect((await serial.getPorts()).filter((p) => p.getInfo().usbVendorId === stock.usbVendorId)).toHaveLength(2);
    client = await connectWebSerial({ serial, filters: RELEASE_FILTERS, probeMs: 800 }); // no click: a chooser would throw
    expect(client.info.proto).toMatch(/^1\./);
    expect(serial.chooser).toBe(0);
    expect(serial.stale!.opens).toBe(1); // probed once, then passed over
    expect(serial.xyfPorts.at(-1)!.isOpen).toBe(true);
  });

  test('found among granted entries, then a restart: reconnect finds it again (probing ends with the first connect)', async () => {
    const serial = new FakeSerial(sim!.relay, { identity: stock, stale: 'granted' });
    client = await connectWebSerial({ serial, filters: RELEASE_FILTERS, probeMs: 800 });
    const before = serial.xyfPorts.at(-1)!;
    await client.reboot('menu');
    expect(client.connected).toBe(false);
    expect((await client.reconnect({ timeoutMs: 20_000 })).proto).toMatch(/^1\./);
    expect(serial.xyfPorts.at(-1)).not.toBe(before);
    expect(serial.chooser).toBe(0);
  });

  test('nothing granted: one chooser, filtered to the stock pair alone; the live port answers', async () => {
    const serial = new FakeSerial(sim!.relay, { identity: stock, stale: 'attached' });
    client = await fromClick(() => connectWebSerial({ serial, filters: RELEASE_FILTERS }));
    expect(serial.chooser).toBe(1);
    expect(serial.filtersShown).toEqual([[{ usbVendorId: 0x2367, usbProductId: 0x0021 }]]);
    expect(serial.stale!.opens).toBe(0);
  });

  test('the stale entry chosen: it says what to choose', async () => {
    const serial = new FakeSerial(sim!.relay, { identity: stock, stale: 'attached', pick: 'stale' });
    const error = await fromClick(() =>
      connectWebSerial({ serial, filters: RELEASE_FILTERS, timeoutMs: 1500, probeMs: 500 }),
    ).catch((e: unknown) => e as Error);
    expect(error).toMatchObject({ code: 'timeout' });
    expect((error as Error).message).toContain(CHOOSE_TEXT);
    expect(serial.chooser).toBe(1);
  });
});
