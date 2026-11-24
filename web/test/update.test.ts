import { describe, expect, it, vi } from 'vitest';
import { protocol } from 'xy-forever-client';
import type { DeviceInfo } from 'xy-forever-client';
import { sha256Hex } from '../src/install/checks.ts';
import type { ReleasePin } from '../src/install/release.ts';
import { updateOffer, updateXyf } from '../src/update.ts';

const image = new Uint8Array(1024).fill(17);
const sha256 = await sha256Hex(image);
const release: ReleasePin = { schema: 1, version: '0.2.0', image: sha256.slice(0, 8), sha256, bytes: image.length };
const canary = (n: number, version = '0.2.0'): ReleasePin => ({
  schema: 1,
  version,
  image: `${n}`.repeat(8),
  sha256: `${n}`.repeat(64),
  bytes: 1024,
  channel: 'canary',
  commit: `${n}`.repeat(40),
  date: '2026-11-23T10:00:00Z',
});
const commands = protocol.parseHello('XYF1;service=usb;role=b;proto=1.15;install=stock-88a06a7e');

function device(version: string | null, imageId = 'abcde123'): DeviceInfo {
  return {
    hello: commands,
    version,
    image: imageId,
    proto: '1.15',
    mode: 'product',
    limits: { stage: 512 * 1024, payload: 4160, read: 1024 },
    dev: false,
    ask: false,
    trust: null,
    serial: true,
  };
}

function fixture() {
  const running = device(release.version, release.image);
  const events: string[] = [];
  const digest = Uint8Array.from(sha256.match(/../g)!, (part) => parseInt(part, 16));
  const client = {
    info: device('0.1.0'),
    stage: vi.fn(async () => {
      events.push('stage');
      return { size: image.length, crc32: 0, sha256: digest };
    }),
    bootInstall: vi.fn(async () => {
      events.push('install');
      return { written: image.length, partition: 2, sectors: 3 };
    }),
    reboot: vi.fn(async () => {
      events.push('reboot');
    }),
    reconnect: vi.fn(async () => {
      events.push('reconnect');
      return running;
    }),
  };
  const get = vi.fn(async () => new Response(image.slice()));
  return { client, events, running, digest, get };
}

describe('web update', () => {
  it('offers a newer numeric release and supports older build numbers', () => {
    expect(updateOffer(device('0.1.12'), release, release)).toBe('update');
    expect(updateOffer(device('191'), release, release)).toBe('update');
    expect(updateOffer(device('0.10.0'), release, release)).toBe(null);
    expect(updateOffer(device(release.version, release.image), release, release)).toBe(null);
    expect(updateOffer(device(null), release, release)).toBe(null);
    expect(updateOffer({ ...device('0.1.12'), serial: false }, release, release)).toBe(null);
  });

  it('tells canaries of one version apart by image, and names channel switches', () => {
    const [first, second] = [canary(1), canary(2)];
    expect(updateOffer(device('0.2.0', first.image), second, release)).toBe('update'); // a newer canary
    expect(updateOffer(device('0.2.0', second.image), second, release)).toBe(null);
    expect(updateOffer(device('0.1.0'), second, release)).toBe('update');
    expect(updateOffer(device('0.2.0', release.image), second, release)).toBe('switch'); // Stable to Canary
    expect(updateOffer(device('0.2.0', first.image), release, release)).toBe('switch'); // Canary back to Stable
    expect(updateOffer(device('0.3.0', first.image), canary(3, '0.2.0'), release)).toBe(null);
  });

  it('stages verified bytes, verifies the installation, and restarts straight into the menu', async () => {
    const { client, events, running, digest, get } = fixture();
    await expect(updateXyf(client, release, release, () => {}, get)).resolves.toBe(running);
    expect(client.stage).toHaveBeenCalledWith(image, { onProgress: expect.any(Function) });
    expect(client.bootInstall).toHaveBeenCalledWith(digest);
    expect(client.reboot).toHaveBeenCalledWith('menu');
    expect(events).toEqual(['stage', 'install', 'reboot', 'reconnect']);
  });

  it('stops before staging when the download differs from the pin', async () => {
    const { client } = fixture();
    await expect(
      updateXyf(
        client,
        release,
        release,
        () => {},
        async () => new Response(new Uint8Array(image.length)),
      ),
    ).rejects.toThrow(/SHA-256/);
    expect(client.stage).not.toHaveBeenCalled();
  });

  it('stops before restarting if read-back was incomplete', async () => {
    const { client, get } = fixture();
    client.bootInstall.mockResolvedValueOnce({ written: image.length, partition: 1, sectors: 0 });
    await expect(updateXyf(client, release, release, () => {}, get)).rejects.toThrow(/complete read-back/);
    expect(client.reboot).not.toHaveBeenCalled();
  });

  it('installs a canary of the running version and requires its image after reconnecting', async () => {
    const { client, get } = fixture();
    const target = { ...canary(4), image: release.image, sha256 }; // same bytes as the fixture's download
    client.info = device('0.2.0', canary(1).image);
    client.reconnect.mockResolvedValueOnce(device('0.2.0', canary(1).image));
    await expect(updateXyf(client, target, release, () => {}, get)).rejects.toThrow(/did not report Canary/);
    client.info = device('0.2.0', canary(1).image);
    await expect(updateXyf(client, target, null, () => {}, get)).resolves.toBeTruthy();
  });

  it('requires the pinned image after reconnecting', async () => {
    const { client, get } = fixture();
    client.reconnect.mockResolvedValueOnce(device(release.version, 'bad00000'));
    await expect(updateXyf(client, release, release, () => {}, get)).rejects.toThrow(/did not report/);
  });
});
