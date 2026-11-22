import { expect, it, vi } from 'vitest';
import { protocol, XyfError } from 'xy-forever-client';
import type { Client, DirEntry } from 'xy-forever-client';
import { STOCK_121, STOCK_PATH } from '../src/install/checks.ts';
import { uninstallAvailable, uninstallXyf } from '../src/uninstall.ts';

const MOUNT = '/yaffs2',
  MANAGER = `${MOUNT}/xy-forever`,
  ROOT = `${MOUNT}/os.ldr`,
  STOCK = `${MOUNT}/${STOCK_PATH}`;
const RECORD = `${MANAGER}/content-state.txt`;
const LEGACY = 'opxy-121-stock';
const stock = new Uint8Array(STOCK_121.bytes);
const stockDigest = Uint8Array.from(STOCK_121.sha256.match(/../g)!, (pair) => parseInt(pair, 16));
const encode = (text: string): Uint8Array => new TextEncoder().encode(text);
const mockStock = (data: Uint8Array): boolean => data.length === stock.length && data.every((byte) => byte === 0);
const RANGE = 1024;

// Exact official bytes are exercised in Chrome against the simulator. Unit tests stand in only for the hash of those bytes.
vi.mock(import('xy-forever-client'), async (original) => {
  const lib = await original();
  return {
    ...lib,
    sha256: (bytes: Uint8Array) => (mockStock(bytes) ? Promise.resolve(stockDigest) : lib.sha256(bytes)),
  };
});

function fixture() {
  const hello = protocol.parseHello('XYF1;service=usb;role=b;proto=1.26;install=stock-88a06a7e');
  const files = new Map<string, Uint8Array>([
    [STOCK, stock],
    [ROOT, encode('manager')],
    [`${MANAGER}/library`, encode('library')],
  ]);
  const types = new Map<string, DirEntry['kind']>([
    [MOUNT, 'folder'],
    [MANAGER, 'folder'],
    [STOCK.slice(0, STOCK.lastIndexOf('/')), 'folder'],
    ...[...files.keys()].map((path) => [path, 'file'] as const),
  ]);
  const client = {
    info: { hello, proto: '1.26', serial: true, limits: { read: RANGE, stage: 7 * 1024 * 1024 } },
    listDir: vi.fn(async (path: string): Promise<DirEntry[]> => {
      if (types.get(path) !== 'folder') throw new XyfError('not_found', 'no folder');
      const entries: DirEntry[] = [];
      for (const [entry, kind] of types) {
        const last = entry.lastIndexOf('/');
        if (entry.slice(0, last) !== path) continue;
        const name = entry.slice(last + 1);
        entries.push({ name, nameBytes: encode(name), kind, size: files.get(entry)?.length ?? 0 });
      }
      return entries;
    }),
    readRange: vi.fn(async (path: string, offset: number, length: number, _options?: { timeoutMs: number }) =>
      files.get(path)!.slice(offset, offset + length),
    ),
    libraryEntry: vi.fn(async (id: string) => {
      if (id !== LEGACY) throw new XyfError('not_found', 'no entry');
      return { sha256: STOCK_121.sha256 };
    }),
    setDefault: vi.fn(async () => {
      files.set(RECORD, encode('state=official\n'));
    }),
    stage: vi.fn(async (_bytes: Uint8Array) => ({ size: stock.length, sha256: stockDigest, crc32: 0 })),
    bootUninstall: vi.fn(async () => ({
      written: stock.length,
      partition: 1,
      sectors: Math.floor(stock.length / 512) + 1,
    })),
    call: vi.fn(async () => ({})),
    writeFile: vi.fn(async (path: string, bytes: Uint8Array) => {
      files.set(path, bytes);
      types.set(path, 'file');
    }),
    deleteFile: vi.fn(async (path: string) => {
      types.delete(path);
      files.delete(path);
    }),
    removeDir: vi.fn(async (path: string) => {
      types.delete(path);
    }),
    reboot: vi.fn(async () => {}),
  };
  const run = (): Promise<void> => uninstallXyf(client as unknown as Client, () => {});
  return { client, files, types, run };
}

it('retries only the missing range at the same offset before staging the complete stock image', async () => {
  const { client, run } = fixture();
  const read = client.readRange.getMockImplementation()!;
  let drops = 2;
  client.readRange.mockImplementation(async (path, offset, length) => {
    if (path === STOCK && offset === RANGE && drops-- > 0) throw new XyfError('timeout', 'one missing reply');
    return read(path, offset, length);
  });
  await run();
  expect(client.readRange.mock.calls.filter(([path, offset]) => path === STOCK && offset === RANGE)).toEqual([
    [STOCK, RANGE, RANGE, { timeoutMs: 1800 }],
    [STOCK, RANGE, RANGE, { timeoutMs: 1800 }],
    [STOCK, RANGE, RANGE, { timeoutMs: 4000 }],
  ]);
  expect(mockStock(client.stage.mock.lastCall![0])).toBe(true);
  expect(client.bootUninstall).toHaveBeenCalledTimes(1);
});

it.each([
  ['timeout', 3],
  ['disconnected', 1],
] as const)('stops before all writes when a stock range reports %s', async (code, attempts) => {
  const { client, run } = fixture();
  client.readRange.mockRejectedValue(new XyfError(code, 'read stopped'));
  await expect(run()).rejects.toThrow('read stopped');
  expect(client.readRange).toHaveBeenCalledTimes(attempts);
  expect(client.setDefault).not.toHaveBeenCalled();
  expect(client.stage).not.toHaveBeenCalled();
});

it('leaves cleanup for retry when exact root read-back cannot finish after the stock write', async () => {
  const { client, files, types, run } = fixture();
  const read = client.readRange.getMockImplementation()!;
  client.readRange.mockImplementation(async (path, offset, length) => {
    if (path === ROOT) throw new XyfError('timeout', 'read-back did not answer');
    return read(path, offset, length);
  });
  await expect(run()).rejects.toThrow(/stock boot partition was restored.*read-back did not answer/);
  expect(client.bootUninstall).toHaveBeenCalledTimes(1);
  expect(client.writeFile).toHaveBeenCalledTimes(1);
  expect(mockStock(files.get(ROOT)!)).toBe(true);
  expect(types.has(MANAGER)).toBe(true);
  expect(client.deleteFile).not.toHaveBeenCalled();
  expect(client.reboot).not.toHaveBeenCalled();
  expect(client.readRange.mock.calls.filter(([path]) => path === ROOT)).toHaveLength(3);
});

it('requires post-install file service before it offers web uninstall', () => {
  const { client } = fixture();
  const info = (client as unknown as Client).info;
  expect(uninstallAvailable(info)).toBe(true);
  expect(uninstallAvailable({ ...info, proto: '1.25' })).toBe(false);
  expect(uninstallAvailable({ ...info, proto: '1.100' })).toBe(true);
});

it('refuses a missing stock folder or a same-size non-stock image before any write', async () => {
  const { client, files, types, run } = fixture();
  const parent = STOCK.slice(0, STOCK.lastIndexOf('/'));
  types.delete(parent);
  await expect(run()).rejects.toThrow(/exact official OS 1\.2\.1/);
  types.set(parent, 'folder');
  files.set(STOCK, new Uint8Array(stock.length).fill(42));
  await expect(run()).rejects.toThrow(/exact official OS 1\.2\.1/);
  expect(client.setDefault).not.toHaveBeenCalled();
  expect(client.stage).not.toHaveBeenCalled();
  expect(client.call).not.toHaveBeenCalled();
});

it('refuses a manager-root alias before listing or mutating it', async () => {
  const { client, types, run } = fixture();
  types.set(MANAGER, 'other');
  await expect(run()).rejects.toThrow(/xy-forever.*not a directory/);
  expect(client.listDir.mock.calls).toEqual([[MOUNT]]);
  expect(client.stage).not.toHaveBeenCalled();
});

it('restores content with the legacy stock entry and stops at incomplete boot read-back', async () => {
  const { client, files, types, run } = fixture();
  types.set(RECORD, 'file');
  files.set(RECORD, encode('state=installed\nentry=mods-a\n'));
  client.bootUninstall.mockResolvedValueOnce({ written: stock.length, partition: 1, sectors: 0 });
  await expect(run()).rejects.toThrow(/complete read-back/);
  expect(client.setDefault).toHaveBeenCalledWith(LEGACY, expect.any(Object));
  expect(client.setDefault.mock.invocationCallOrder[0]).toBeLessThan(client.bootUninstall.mock.invocationCallOrder[0]!);
  expect(client.call).not.toHaveBeenCalled();
  expect(client.writeFile).not.toHaveBeenCalled();
  expect(client.deleteFile).not.toHaveBeenCalled();
  expect(client.reboot).not.toHaveBeenCalled();
});

it('retries partial cleanup on the still-running service after the stock partition and root were restored', async () => {
  const { client, files, types, run } = fixture();
  const alias = `${MANAGER}/user-alias`;
  types.set(alias, 'other');
  client.deleteFile.mockImplementationOnce(async (path) => {
    types.delete(path);
    files.delete(path);
  });
  client.deleteFile.mockRejectedValueOnce(new Error('temporary file refusal'));
  await expect(run()).rejects.toThrow(/stock boot partition was restored.*Try Uninstall again/);
  expect(mockStock(files.get(ROOT)!)).toBe(true);
  expect(client.reboot).not.toHaveBeenCalled();
  expect(types.has(`${MANAGER}/library`)).toBe(false);
  expect(types.has(alias)).toBe(true);
  await expect(run()).resolves.toBeUndefined();
  expect(client.writeFile).toHaveBeenCalledTimes(1);
  expect(client.listDir.mock.calls).not.toContainEqual([alias]);
  expect(types.has(MANAGER)).toBe(false);
  expect(client.reboot).toHaveBeenCalledWith('os');
});
