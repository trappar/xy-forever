// Install from the web app (#178), the device steps before XY Forever runs: te-boot over Web MIDI (identity, then
// switching the dev menu on) and the dev menu's Mac MTP over WebUSB (checks, then copying our os.ldr; the stock os.ldr is
// never read here: XY Forever checks and adopts it on the device). Both through
// Open-XY's modules (vendor/open-xy, MIT). The dev menu is only ever switched on, never off: nothing here can remove
// /yaffs2/bootloader.dev (the vendored Ferret copy has no disable command).
import { Session } from '../vendor/open-xy/ferret.mjs';
import type { Identity } from '../vendor/open-xy/ferret.mjs';
import { Device } from '../vendor/open-xy/mtp.mjs';
import { STOCK_MISSING, STOCK_PATH, bootVerdict, needBytes, sha256Hex, spaceVerdict } from './checks.ts';
import type { Verdict } from './checks.ts';

export type MidiAccess = (options: { sysex: boolean }) => Promise<unknown>;

/** A Verdict that stops the install; the page shows its title and detail. */
export class InstallStop extends Error {
  readonly verdict: Verdict;
  constructor(verdict: Verdict) { super(`${verdict.title}: ${verdict.detail}`); this.verdict = verdict; }
}

/** te-boot over Web MIDI: read the identity and judge it; switch the dev menu on only for an accepted device. */
export class TeBoot {
  private session: Session | null = null;
  private identity: Identity | null = null;
  private readonly access: MidiAccess;
  private readonly onProgress: (text: string) => void;
  constructor(access: MidiAccess, onProgress: (text: string) => void = () => {}) { this.access = access; this.onProgress = onProgress; }

  /** Connects (MIDI permission with SysEx), reads te-boot's identity and judges it. Changes nothing. */
  async check(): Promise<{ identity: Identity; verdict: Verdict }> {
    this.close();
    const session = new Session(this.access, () => { this.session = null; }, 2500, { onProgress: this.onProgress });
    const identity = await session.connect();
    const verdict = bootVerdict(identity);
    if (verdict.kind === 'ok') { this.session = session; this.identity = identity; } else session.close();
    return { identity, verdict };
  }

  /**
   * Switches the dev menu on (te-boot's command 0xac; it creates /yaffs2/bootloader.dev). Only when the owner says
   * te-boot shows no "3. dev menu": an existing marker is left as it is. Checks the device again first.
   */
  async enableDevMenu(): Promise<void> {
    if (!this.session || !this.identity) {
      const { verdict } = await this.check();
      if (verdict.kind !== 'ok') throw new InstallStop(verdict);
    }
    await this.session!.enable();
  }

  close(): void { this.session?.close(); this.session = null; this.identity = null; }
}

export interface CopyProgress { (text: string, fraction?: number): void }

/**
 * The Mac path, in the dev menu's "mtp file transfer (mac)" mode: checks that the device's own stock os.ldr is there
 * (its size only: XY Forever reads and checks it on the device at its first start) and the free storage, then writes
 * our os.ldr to the top level and reads it back. Throws InstallStop when a check refuses (nothing copied). Asks the
 * browser for the device (the chooser) first.
 */
export async function copyOverMtp(usb: unknown, image: Uint8Array, progress: CopyProgress = () => {}): Promise<void> {
  const device = await Device.request(usb);
  try {
    await device.open();
    progress('Checking your OP-XY...');
    if (await device.size(STOCK_PATH) === null) throw new InstallStop(STOCK_MISSING);
    const { free } = await device.space();
    const space = spaceVerdict(free + (await device.size('os.ldr') ?? 0), needBytes(image.length));   // an older /os.ldr is replaced
    if (space.kind !== 'ok') throw new InstallStop(space);
    progress('Copying XY Forever to your OP-XY...', 0);
    await device.send('os.ldr', image, { replace: async () => true, onProgress: (f) => progress('Copying XY Forever to your OP-XY...', f) });
    progress('Checking the copy...');
    const back = await device.read('os.ldr', { maxBytes: image.length });
    if (await sha256Hex(back) !== await sha256Hex(image)) throw new Error('The copy on your OP-XY differs from XY Forever\'s file. Try again.');
  } finally {
    await device.close();
  }
}
