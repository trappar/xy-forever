import { TYPE } from 'xy-forever-client';
import type { Client, DeviceInfo } from 'xy-forever-client';
import { channelOf, fetchRelease, releaseName } from './install/release.ts';
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

/**
 * Whether the connected OP-XY can move to target: 'update' (a newer version, or a newer canary of the same version),
 * 'switch' (a channel switch: from the stable image to a canary, or from another image of stable's version to
 * stable), or null. Builds between releases share a version, so the image tells them apart. Never older versions.
 */
export function updateOffer(
  info: DeviceInfo,
  target: ReleasePin,
  stable: ReleasePin | null,
): 'update' | 'switch' | null {
  const installed = releaseCode(info.version);
  const available = releaseCode(target.version);
  if (
    installed === null ||
    available === null ||
    available < installed ||
    info.image === target.image ||
    !info.serial ||
    !info.hello.answers(TYPE['boot.install']) ||
    !info.hello.answers(TYPE['device.reboot'])
  )
    return null;
  if (channelOf(target) === 'canary') return stable && info.image === stable.image ? 'switch' : 'update';
  return available > installed ? 'update' : 'switch';
}

/** An update or channel switch the page may start (switches qualified on an OP-XY, #301). */
export const updateAvailable = (info: DeviceInfo, target: ReleasePin, stable: ReleasePin | null): boolean =>
  updateOffer(info, target, stable) !== null;

type UpdateClient = Pick<Client, 'info' | 'stage' | 'bootInstall' | 'reboot' | 'reconnect'>;

/** Installs the chosen release (Stable or Canary) and confirms its image from HELLO after a restart into the menu. */
export async function updateXyf(
  client: UpdateClient,
  release: ReleasePin,
  stable: ReleasePin | null,
  step: (message: string) => void,
  get?: Parameters<typeof fetchRelease>[1],
): Promise<DeviceInfo> {
  if (!updateAvailable(client.info, release, stable))
    throw new Error('This update is unavailable for the connected OP-XY.');
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
    throw new Error(`The OP-XY restarted, but did not report ${releaseName(release)} (image ${release.image}).`);
  }
  return info;
}
