// Where the OP-XY allows changes by hand (split out of client.ts; the device checks again: target/file_engine.hpp).

/**
 * Where the device allows changes (file.write_staged, file.delete, file.rmdir, file.mkdir, file.rename, file.copy's
 * target): since 1.13 all of /yaffs2, except the names in PROTECTED_NAMES.
 */
export const WRITE_ROOTS = ['/yaffs2/'] as const;
/**
 * Never changed by hand (any ASCII case): the dev-menu marker, the stock updater's inbox and all below it, and (#65)
 * the Settings record, whose Confirm on device setting changes only on the device.
 */
export const PROTECTED_NAMES = ['/yaffs2/bootloader.dev', '/yaffs2/upgrade', '/yaffs2/xy-forever/settings.txt',
  '/yaffs2/xy-forever/settings.old'] as const;
const SETTINGS_FILES = ['settings.txt', 'settings.old'];

/**
 * Whether the device's write check can pass path (as xyf_host.py in_write_area): below /yaffs2/, no empty, "."
 * or ".." part, not /yaffs2/bootloader.dev, not /yaffs2/upgrade or below it, not /yaffs2/xy-forever/settings.txt
 * or settings.old (ASCII case ignored). The device checks again and stays the authority; this only refuses early
 * what it would refuse.
 */
export function inWriteArea(path: string): boolean {
  const root = WRITE_ROOTS.find((r) => path.startsWith(r));
  if (root === undefined) return false;
  const parts = path.slice(root.length).split('/');
  if (!parts.every((p) => p !== '' && p !== '.' && p !== '..')) return false;
  const low = parts.map((p) => p.toLowerCase());
  const settings = low.length === 2 && low[0] === 'xy-forever' && SETTINGS_FILES.includes(low[1] ?? '');
  return low[0] !== 'upgrade' && !(low.length === 1 && low[0] === 'bootloader.dev') && !settings;
}
