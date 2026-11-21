import { describe, expect, it, vi } from 'vitest';
import { protocol } from 'xy-forever-client';
import type { DeviceInfo } from 'xy-forever-client';
import { sha256Hex } from '../src/install/checks.ts';
import type { ReleasePin } from '../src/install/release.ts';
import { updateAvailable, updateXyf } from '../src/update.ts';

const image = new Uint8Array(1024).fill(17);
const sha256 = await sha256Hex(image);
const release: ReleasePin = { schema: 1, version: '0.2.0', image: sha256.slice(0, 8), sha256, bytes: image.length };
const commands = protocol.parseHello('XYF1;service=usb;role=b;proto=1.15;install=stock-88a06a7e');

function device(version: string | null, imageId = 'abcde123'): DeviceInfo {
  return { hello: commands, version, image: imageId, proto: '1.15', mode: 'product',
    limits: { stage: 512 * 1024, payload: 4160, read: 1024 }, dev: false, ask: false, trust: null, serial: true };
}

function fixture() {
  const running = device(release.version, release.image);
  const events: string[] = [];
  const digest = Uint8Array.from(sha256.match(/../g)!, (part) => parseInt(part, 16));
  const client = {
    info: device('0.1.0'),
    stage: vi.fn(async () => { events.push('stage'); return { size: image.length, crc32: 0, sha256: digest }; }),
    bootInstall: vi.fn(async () => { events.push('install'); return { written: image.length, partition: 2, sectors: 3 }; }),
    reboot: vi.fn(async () => { events.push('reboot'); }),
    reconnect: vi.fn(async () => { events.push('reconnect'); return running; }),
  };
  const get = vi.fn(async () => new Response(image.slice()));
  return { client, events, running, digest, get };
}

describe('web update', () => {
  it('offers a newer numeric release and supports older build numbers', () => {
    expect(updateAvailable(device('0.1.12'), release)).toBe(true);
    expect(updateAvailable(device('191'), release)).toBe(true);
    expect(updateAvailable(device('0.10.0'), release)).toBe(false);
    expect(updateAvailable(device(release.version, 'another'), release)).toBe(false);
    expect(updateAvailable(device(null), release)).toBe(false);
    expect(updateAvailable({ ...device('0.1.12'), serial: false }, release)).toBe(false);
  });

  it('stages verified bytes, verifies the installation, and restarts straight into the menu', async () => {
    const { client, events, running, digest, get } = fixture();
    await expect(updateXyf(client, release, () => {}, get)).resolves.toBe(running);
    expect(client.stage).toHaveBeenCalledWith(image, { onProgress: expect.any(Function) });
    expect(client.bootInstall).toHaveBeenCalledWith(digest);
    expect(client.reboot).toHaveBeenCalledWith('menu');
    expect(events).toEqual(['stage', 'install', 'reboot', 'reconnect']);
  });

  it('stops before staging when the download differs from the pin', async () => {
    const { client } = fixture();
    await expect(updateXyf(client, release, () => {}, async () => new Response(new Uint8Array(image.length))))
      .rejects.toThrow(/SHA-256/);
    expect(client.stage).not.toHaveBeenCalled();
  });

  it('stops before restarting if read-back was incomplete', async () => {
    const { client, get } = fixture();
    client.bootInstall.mockResolvedValueOnce({ written: image.length, partition: 1, sectors: 0 });
    await expect(updateXyf(client, release, () => {}, get)).rejects.toThrow(/complete read-back/);
    expect(client.reboot).not.toHaveBeenCalled();
  });

  it('requires the pinned image after reconnecting', async () => {
    const { client, get } = fixture();
    client.reconnect.mockResolvedValueOnce(device(release.version, 'bad00000'));
    await expect(updateXyf(client, release, () => {}, get)).rejects.toThrow(/did not report/);
  });
});
