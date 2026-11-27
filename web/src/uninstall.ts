import { TYPE, isXyfError, jobStatus } from 'xy-forever-client';
import type { Client, DeviceInfo } from 'xy-forever-client';

export const UNINSTALL_PROTOCOL = '1.34';

export function uninstallAvailable(info: DeviceInfo): boolean {
  return (
    info.serial &&
    !info.hello.preV1 &&
    info.hello.answers(TYPE['boot.uninstall_start']) &&
    info.hello.answers(TYPE['job.status'])
  );
}

interface UsbDevice {
  vendorId: number;
  productId: number;
  serialNumber?: string;
}
interface Usb {
  getDevices(): Promise<UsbDevice[]>;
}

/** A serial disconnect alone cannot distinguish a stock reboot from an unplugged cable. Stock OS
 * enumerates as 2367:8021. WebUSB can see it only when the owner has granted access to that identity. */
export async function stockVisible(serial: string, usb: Usb | undefined): Promise<boolean> {
  if (!serial || !usb?.getDevices) return false;
  const devices = await usb.getDevices().catch(() => []);
  return devices.some((d) => d.vendorId === 0x2367 && d.productId === 0x8021 && d.serialNumber === serial);
}

/** Starts the OP-XY's job once, after confirmation. USB only carries its receipt and progress. */
export async function uninstallXyf(
  client: Client,
  step: (message: string) => void,
): Promise<{ serial: string; completed: boolean }> {
  if (!uninstallAvailable(client.info)) throw new Error('Update XY Forever to use on-device uninstall.');
  step('Starting uninstall on your OP-XY…');
  const { job, serial } = await client.call('boot.uninstall_start', { confirm: 1 }).catch((error: unknown) => {
    if (isXyfError(error, 'disconnected', 'timeout'))
      throw new Error(
        'The browser lost contact before your OP-XY acknowledged the uninstall. Check its screen to see whether it started.',
        { cause: error },
      );
    throw error;
  });
  if (!job) throw new Error('The OP-XY did not return an uninstall job.');
  const unit = new TextDecoder().decode(serial).replace(/\0+$/, '');
  for (;;) {
    // The stock writer can occupy B during a poll. A suspended tab can miss its final status before reboot.
    const status = await jobStatus(client, job, { timeoutMs: 900_000 }).catch((error: unknown) => {
      if (isXyfError(error, 'disconnected', 'timeout')) return null;
      throw error;
    });
    if (!status) {
      step('The OP-XY is handling the uninstall. Follow its screen while the browser waits for stock OS.');
      return { serial: unit, completed: false };
    }
    if (status.job !== job) throw new Error('The OP-XY returned progress for another job.');
    if (status.phase === 'done') break;
    if (status.final) throw new Error(`Uninstall ${status.phase}: ${status.detail || status.error}.`);
    const progress = status.total ? ` (${Math.floor((100 * status.done) / status.total)}%)` : '';
    step((status.detail || 'Restoring stock OS 1.2.1') + progress);
    await client.pause(400);
  }
  step('Waiting for your OP-XY to start stock OS 1.2.1…');
  return { serial: unit, completed: true };
}

/** Prefer the stock identity of this unit. A browser without a grant can confirm the physical screen instead. */
export async function confirmStockBoot(serial: string, confirmScreen: () => Promise<boolean>): Promise<boolean> {
  const usb = (navigator as Navigator & { usb?: Usb }).usb;
  if (serial && usb?.getDevices) {
    for (let n = 0; n < 20; n++) {
      if (await stockVisible(serial, usb)) return true;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  return confirmScreen();
}
