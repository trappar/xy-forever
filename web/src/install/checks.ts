// Install from the web app (#178): the pure checks, in plain words. No DOM, no device: unit tested in
// test/install.test.ts. The stock pins equal stock.json (tests/test_web.py checks that).

/** The official stock os.ldr every OP-XY keeps, relative to the dev menu's MTP top level (/yaffs2). */
export const STOCK_PATH = 'upgrade/firmware/blackfin/bin/os.ldr';
/** The stock 1.2.1 os.ldr: the only firmware XY Forever installs over, and the library entry it becomes. */
export const STOCK_121 = {
  sha256: '60358afdaa9b58ed40411602f6d5d4e1e3e02553ff2da7e25038e71dd002b52f',
  bytes: 2373336,
  crc32: '89d08ddd',
} as const;
/** te-boot's bl_version of the 1.2.1 bootloader (also shipped with 1.1.33), and of 1.2.5's (never supported). */
const BOOTLOADER_121 = 20;
const BOOTLOADER_125 = 23;
/** teenage engineering's own 1.2.1 update: named exactly, never "the latest" (1.2.5 cannot be undone). */
export const OFFICIAL_121_TFW = 'https://teenage.engineering/_software/op-xy/opxy_firmware_1_2_1.tfw';
export const OFFICIAL_UPDATE_GUIDE = 'https://teenage.engineering/guides/op-xy/te-boot';
/** Room kept free beyond our os.ldr and the stock entry's copy: reports, the boot state, the menu's files. */
export const HEADROOM = 1024 * 1024;

/**
 * What a check decided. ok: go on. refuse: this OP-XY cannot take XY Forever (nothing was changed).
 * update: older than 1.2.1, install official 1.2.1 first. reinstall: the stock os.ldr is missing, reinstall
 * official 1.2.1 first. space: not enough free storage.
 */
type VerdictKind = 'ok' | 'refuse' | 'update' | 'reinstall' | 'space';
export interface Verdict {
  readonly kind: VerdictKind;
  readonly title: string;
  readonly detail: string;
}

const ok = (title: string, detail = ''): Verdict => ({ kind: 'ok', title, detail });
const NOTHING = 'Nothing on your OP-XY was changed.';
const NOT_125 =
  "Use exactly this 1.2.1 file, not the newest update on teenage engineering's site: 1.2.5 cannot be undone, and XY Forever cannot run on it.";

/** major.minor.patch from a version text such as 1.2.1+0@483bb625 or 1000.0.20+0; null when there is none. */
function versionParts(text: string | undefined): [number, number, number] | null {
  const m = /(\d+)\.(\d+)\.(\d+)/.exec(text ?? '');
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

function compare(a: readonly number[], b: readonly number[]): number {
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i]! - b[i]!;
  return 0;
}

/**
 * te-boot's identity (Web MIDI, before anything is changed): its bootloader must be 1.2.1's (1000.0.20) and its
 * firmware 1.2.1. A 1.2.5 bootloader, a newer or unknown one, or unknown firmware: refuse. Older: update to 1.2.1.
 */
export function bootVerdict(identity: Readonly<Record<string, string>>): Verdict {
  const bl = versionParts(identity['bl_version']);
  const blText = identity['bl_version'] ?? 'none';
  if (!bl || bl[0] !== 1000 || bl[1] !== 0) {
    return {
      kind: 'refuse',
      title: 'Unknown bootloader',
      detail: `This OP-XY reports bootloader version ${blText}, which XY Forever does not know. ${NOTHING}`,
    };
  }
  if (bl[2] === BOOTLOADER_125) {
    return {
      kind: 'refuse',
      title: 'This OP-XY has the 1.2.5 bootloader',
      detail: `XY Forever cannot run on it: the 1.2.5 bootloader has no dev menu, and it cannot be taken back to 1.2.1. ${NOTHING}`,
    };
  }
  if (bl[2] > BOOTLOADER_121) {
    return {
      kind: 'refuse',
      title: 'Unknown bootloader',
      detail: `This OP-XY reports bootloader version ${blText}, newer than 1.2.1's. XY Forever supports only 1.2.1. ${NOTHING}`,
    };
  }
  const osText = identity['os_version'] || identity['sw_version'];
  const os = versionParts(osText);
  if (bl[2] < BOOTLOADER_121 || (os && compare(os, [1, 2, 1]) < 0)) {
    return {
      kind: 'update',
      title: 'Update to official OS 1.2.1 first',
      detail: `This OP-XY runs ${os ? os.join('.') : 'an OS'} older than 1.2.1. Install teenage engineering's OS 1.2.1 with its own updater, then start again here. ${NOT_125}`,
    };
  }
  if (!os || compare(os, [1, 2, 1]) !== 0) {
    return {
      kind: 'refuse',
      title: 'Unknown firmware',
      detail: `This OP-XY reports firmware ${osText ?? 'none'}. XY Forever installs only over teenage engineering's OS 1.2.1. ${NOTHING}`,
    };
  }
  return ok('Bootloader 1.2.1 and OS 1.2.1: XY Forever can be installed.');
}

/** The device's own stock os.ldr is missing (the Mac copy looks for it before writing anything). */
export const STOCK_MISSING: Verdict = {
  kind: 'reinstall',
  title: 'The official OS file is missing',
  detail:
    'Your OP-XY has no upgrade/firmware/blackfin/bin/os.ldr (a factory reset can remove it). XY Forever needs it: at its first start it ' +
    `adds that file to its library as OS 1.2.1. Reinstall teenage engineering's OS 1.2.1 with its own updater, then start again here. ${NOT_125} ${NOTHING}`,
};

/** The stock os.ldr as Explorer shows its size (KB, rounded up): "2,318 KB". */
export const STOCK_KB = `${Math.ceil(STOCK_121.bytes / 1024).toLocaleString('en-US')} KB`;

/** The device's own stock os.ldr is missing or not the size of OS 1.2.1 (the Mac copy checks it; on Windows, the owner). */
export const STOCK_WRONG: Verdict = {
  kind: 'reinstall',
  title: 'The official OS file is missing or different',
  detail:
    `Your OP-XY's upgrade/firmware/blackfin/bin/os.ldr is missing or not the size of teenage engineering's OS 1.2.1 (${STOCK_KB}). ` +
    "XY Forever needs that exact file: at its first start it adds it to its library as OS 1.2.1. Reinstall teenage engineering's OS 1.2.1 " +
    `with its own updater, then start again here. ${NOT_125} ${NOTHING}`,
};

/** Free bytes needed: our os.ldr at the top level, the stock entry's copy (made on the device), and HEADROOM. */
export function needBytes(imageBytes: number): number {
  return imageBytes + STOCK_121.bytes + HEADROOM;
}

export function spaceVerdict(free: number, need: number): Verdict {
  if (free >= need) return ok(`${mib(free)} free; XY Forever needs ${mib(need)}.`);
  return {
    kind: 'space',
    title: 'Not enough free storage',
    detail: `Your OP-XY has ${mib(free)} free on its internal storage, and XY Forever needs ${mib(need)}. Remove files you added there (an earlier mod install, for example), then try again. ${NOTHING}`,
  };
}

const mib = (bytes: number): string => `${(bytes / 1048576).toFixed(1)} MB`;

export function hex(bytes: Uint8Array): string {
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export async function sha256Hex(data: Uint8Array): Promise<string> {
  return hex(new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', data.slice())));
}

/** Which computer: decides how our os.ldr reaches the OP-XY (WebUSB on a Mac; Explorer by hand on Windows). */
export type Computer = 'mac' | 'windows';
