// In Chrome (#178): connecting without asking the owner to choose. Chrome listed two entries for one OP-XY at the
// install's "Connect and finish"; here the fake navigator.serial (fake-serial.ts) adds a stale entry with XY
// Forever's USB ids that opens and never answers, next to the simulator's live port.
import { afterEach, describe, expect, inject, test } from 'vitest';
import { userEvent } from 'vitest/browser';
import { CHOOSE_TEXT, RELEASE_FILTERS, USB_FILTERS, connectWebSerial } from '../../src/index.ts';
import type { Client } from '../../src/index.ts';
import { FakeSerial } from './fake-serial.ts';

const web = inject('web');
const sim = web.xyf;

/** Runs work from a real click (Playwright), as the page's button would. */
async function fromClick<T>(work: () => Promise<T>): Promise<T> {
  const button = document.createElement('button');
  button.textContent = 'Connect and finish';
  document.body.append(button);
  let result!: Promise<T>;
  button.addEventListener('click', () => { result = work(); result.catch(() => undefined); });
  try {
    await userEvent.click(button);
    return await result;
  } finally {
    button.remove();
  }
}

describe.skipIf(!sim)(`choosing the port${sim ? '' : ` (skipped: ${web.why})`}`, () => {
  const stock = USB_FILTERS[1];
  let client: Client | null = null;
  afterEach(async () => { await client?.close(); client = null; });

  test('two granted entries: the stale one is passed over, the live one used, no chooser', async () => {
    const serial = new FakeSerial(sim!.relay, { identity: stock, stale: 'granted' });
    expect((await serial.getPorts()).filter((p) => p.getInfo().usbVendorId === stock.usbVendorId)).toHaveLength(2);
    client = await connectWebSerial({ serial, filters: RELEASE_FILTERS, probeMs: 800 });   // no click: a chooser would throw
    expect(client.info.proto).toMatch(/^1\./);
    expect(serial.chooser).toBe(0);
    expect(serial.stale!.opens).toBe(1);                                  // probed once, then passed over
    expect(serial.xyfPorts.at(-1)!.isOpen).toBe(true);
  });

  test('nothing granted: one chooser, filtered to the stock pair alone; the live port answers', async () => {
    const serial = new FakeSerial(sim!.relay, { identity: stock, stale: 'attached' });
    client = await fromClick(() => connectWebSerial({ serial, filters: RELEASE_FILTERS }));
    expect(serial.chooser).toBe(1);
    expect(serial.filtersShown).toEqual([[{ usbVendorId: 0x2367, usbProductId: 0x0021 }]]);
    expect(serial.stale!.opens).toBe(0);
  });

  test('the stale entry chosen: it says which entry to choose', async () => {
    const serial = new FakeSerial(sim!.relay, { identity: stock, stale: 'attached', pick: 'stale' });
    const error = await fromClick(() => connectWebSerial({ serial, filters: RELEASE_FILTERS, timeoutMs: 1500, probeMs: 500 })).catch((e: unknown) => e as Error);
    expect(error).toMatchObject({ code: 'timeout' });
    expect((error as Error).message).toContain(CHOOSE_TEXT);
    expect(serial.chooser).toBe(1);
  });
});
