import { TYPE } from 'xy-forever-client';
import type { Client, DeviceInfo } from 'xy-forever-client';
import { fetchRelease } from './install/release.ts';
import type { ReleasePin } from './install/release.ts';

// Three byte-sized components match xy_forever/release.py. Older devices
// reported a single build number, below the first 0.1.0 release.
function releaseCode(version: string | null): number | null {
  if (!version) return null;
  if (/^\d{1,3}$/.test(version) && Number(version) < 256) return Number(version);
  if (!/^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(version)) return null;
  const parts = version.split('.').map(Number);
  if (parts.some((part) => part > 255)) return null;
  return parts.reduce((code, part) => code * 256 + part, 0);
}

export function updateAvailable(info: DeviceInfo, release: ReleasePin): boolean {
  const installed = releaseCode(info.version);
  const available = releaseCode(release.version);
  return (
    installed !== null &&
    available !== null &&
    available > installed &&
    info.serial &&
    info.hello.answers(TYPE['boot.install']) &&
    info.hello.answers(TYPE['device.reboot'])
  );
}

type UpdateClient = Pick<Client, 'info' | 'stage' | 'bootInstall' | 'reboot' | 'reconnect'>;

/** Installs the pinned release and confirms it from HELLO after a restart into the menu. */
export async function updateXyf(
  client: UpdateClient,
  release: ReleasePin,
  step: (message: string) => void,
  get?: Parameters<typeof fetchRelease>[1],
): Promise<DeviceInfo> {
  if (!updateAvailable(client.info, release)) throw new Error('This update is unavailable for the connected OP-XY.');
  step('Downloading and checking the update…');
  const image = await fetchRelease(release, get);
  step('Sending the update to your OP-XY…');
  const staged = await client.stage(image, {
    onProgress: (done) => step(`Sending the update to your OP-XY… ${Math.floor((done * 100) / image.length)}%`),
  });
  step('Installing and reading back the update. Keep your OP-XY powered and connected.');
  const { written, partition, sectors } = await client.bootInstall(staged.sha256);
  if (
    written !== image.length ||
    (partition !== 1 && partition !== 2) ||
    sectors !== Math.floor(image.length / 512) + 1
  ) {
    throw new Error('The OP-XY did not confirm a complete read-back of the update.');
  }

  step('Restarting into the XY Forever menu…');
  let info: DeviceInfo;
  try {
    await client.reboot('menu');
    info = await client.reconnect({ timeoutMs: 20_000 });
  } catch (error) {
    throw new Error(
      'The update was written and checked. Connect to your OP-XY again to check which version is running.',
      { cause: error },
    );
  }
  if (info.image !== release.image || info.version !== release.version) {
    throw new Error(`The OP-XY restarted, but did not report XY Forever ${release.version} (image ${release.image}).`);
  }
  return info;
}
