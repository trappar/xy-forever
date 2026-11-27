import { expect, it, vi } from 'vitest';
import { TYPE, XyfError } from 'xy-forever-client';
import type { Client, DeviceInfo } from 'xy-forever-client';
import { stockVisible, uninstallAvailable, uninstallXyf } from '../src/uninstall.ts';

const info = (commands: number[]): DeviceInfo =>
  ({
    serial: true,
    hello: { answers: (type: number) => commands.includes(type) },
  }) as DeviceInfo;
const required = [TYPE['boot.uninstall_start'], TYPE['job.status']];
const status = (phase: number, detail: string) => ({
  phase,
  job: 15,
  done: 1,
  total: 4,
  error: 0,
  detail: new TextEncoder().encode(detail),
});
const receipt = { job: 15, serial: new TextEncoder().encode('testunit\0\0') };
function fixture() {
  const call = vi.fn(),
    step = vi.fn();
  const client = { info: info(required), call, pause: vi.fn() };
  return { call, step, run: () => uninstallXyf(client as unknown as Client, step) };
}

it('offers uninstall only when the device advertises the local job and its progress', () => {
  expect(uninstallAvailable(info(required))).toBe(true);
  expect(uninstallAvailable(info([TYPE['boot.uninstall'], TYPE['job.status']]))).toBe(false);
  expect(uninstallAvailable(info([TYPE['boot.uninstall_start']]))).toBe(false);
  expect(uninstallAvailable({ ...info(required), serial: false })).toBe(false);
});

it('starts one device job, displays its progress and waits for stock boot confirmation', async () => {
  const { call, step, run } = fixture();
  call
    .mockResolvedValueOnce(receipt)
    .mockResolvedValueOnce(status(1, 'Reading official OS'))
    .mockResolvedValueOnce(status(8, 'Complete'));
  await expect(run()).resolves.toEqual({ serial: 'testunit', completed: true });
  expect(call.mock.calls.map(([name]) => name)).toEqual(['boot.uninstall_start', 'job.status', 'job.status']);
  expect(call.mock.calls[0]?.[1]).toEqual({ confirm: 1 });
  expect(call.mock.calls[1]?.[1]).toEqual({ job: 15 });
  expect(step).toHaveBeenCalledWith('Reading official OS (25%)');
});

it('surfaces a refusal or job failure without retrying the uninstall', async () => {
  const { call, run } = fixture();
  call.mockRejectedValueOnce(new XyfError('busy', 'Device is busy'));
  await expect(run()).rejects.toThrow('Device is busy');
  expect(call).toHaveBeenCalledTimes(1);
  call.mockResolvedValueOnce(receipt).mockResolvedValueOnce(status(9, 'Invalid stock image'));
  await expect(run()).rejects.toThrow('Invalid stock image');
});

it('sends no second start when the acknowledgement is lost', async () => {
  const { call, run } = fixture();
  call.mockRejectedValueOnce(new XyfError('timeout', 'reply lost'));
  await expect(run()).rejects.toThrow('before your OP-XY acknowledged');
  expect(call).toHaveBeenCalledTimes(1);
});

it('does not treat losing USB while the job runs as a successful uninstall', async () => {
  const { call, run } = fixture();
  call.mockResolvedValueOnce(receipt).mockRejectedValueOnce(new XyfError('disconnected', 'gone'));
  await expect(run()).resolves.toEqual({ serial: 'testunit', completed: false });
  expect(call).toHaveBeenCalledTimes(2);
});

it('matches the stock OS USB identity and serial of the unit that accepted the uninstall', async () => {
  const seen = { vendorId: 0x2367, productId: 0x8021, serialNumber: 'unit-one' };
  const usb = { getDevices: vi.fn().mockResolvedValue([seen]) };
  expect(await stockVisible('unit-one', usb)).toBe(true);
  expect(await stockVisible('unit-two', usb)).toBe(false);
  expect(await stockVisible('', usb)).toBe(false);
  usb.getDevices.mockResolvedValue([{ ...seen, productId: 0x0021 }]);
  expect(await stockVisible('unit-one', usb)).toBe(false);
  expect(await stockVisible('unit-one', undefined)).toBe(false);
});
