// Where the OP-XY allows changes by hand (split out of client.ts; the device checks again: target/file_engine.hpp).

/**
 * Where the device allows changes (file.write_staged, file.delete, file.rmdir, file.mkdir, file.rename, file.copy's
 * target): since 1.13 all of /yaffs2, except the names in PROTECTED_NAMES.
 */
export const WRITE_ROOTS = ['/yaffs2/'] as const;
/** Never changed by hand (any ASCII case): the dev-menu marker, and the stock updater's inbox and all below it. */
export const PROTECTED_NAMES = ['/yaffs2/bootloader.dev', '/yaffs2/upgrade'] as const;

/**
 * Whether the device's write check can pass path (as xy_forever/xyf_host.py in_write_area): below /yaffs2/, no empty, "."
 * or ".." part, not /yaffs2/bootloader.dev, not /yaffs2/upgrade or below it (ASCII case ignored). The device
 * checks again and stays the authority; this only refuses early what it would refuse.
 */
export function inWriteArea(path: string): boolean {
  const root = WRITE_ROOTS.find((r) => path.startsWith(r));
  if (root === undefined) return false;
  const parts = path.slice(root.length).split('/');
  if (!parts.every((p) => p !== '' && p !== '.' && p !== '..')) return false;
  const first = (parts[0] ?? '').toLowerCase();
  return first !== 'upgrade' && !(parts.length === 1 && first === 'bootloader.dev');
}
