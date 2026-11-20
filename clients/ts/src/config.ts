// The saved inputs over USB (#26, protocol 1.24): config.get reads them, config.set saves a selection as the device's
// Mods page does. The device reads its package list again, checks that the selection can be built and saves it (or
// refuses, saying why). Nothing is built then: the next boot of XY Forever converges to what is saved (#197).
import { sha256 } from './client.ts';
import type { CallOptions, Client } from './client.ts';
import { XyfError, isXyfError } from './errors.ts';
import { encodeConfigSetRequest } from './v1.ts';

/** config.get / config.set flags. */
export const CONFIG_FLAGS = { expanded: 1, safe: 2, unverified: 4 } as const;

export interface SavedInputs {
  /** A record is saved (config.txt); false: no mods, the default stock entry boots. */
  readonly saved: boolean;
  /** The selection as saved: machine folders, then ext-<id>. */
  readonly mods: readonly string[];
  /** Mod memory is the 4 MiB layout. */
  readonly expanded: boolean;
  /** Safe mode ("Boot without mods"): the base boots with its official content, the selection kept. */
  readonly safe: boolean;
  /** An unknown base is allowed. */
  readonly unverified: boolean;
  /** 'saved': what boots next; 'writing': a set or a flip on the device waits for its quiet moment; 'failed': its write failed. */
  readonly write: 'saved' | 'writing' | 'failed';
  readonly base: string;
  readonly generation: string;
}

export interface InputsRequest {
  readonly mods: readonly string[];
  readonly expanded?: boolean;
  readonly safe?: boolean;
  readonly unverified?: boolean;
}

/** The saved inputs (config.get). */
export async function configGet(client: Client, options: CallOptions = {}): Promise<SavedInputs> {
  const r = await client.call('config.get', { version: 1 }, options);
  return { saved: r.record === 1, mods: r.mods ? r.mods.split(',') : [], expanded: (r.flags & 1) !== 0, safe: (r.flags & 2) !== 0,
    unverified: (r.flags & 4) !== 0, write: r.saving === 0 ? 'saved' : r.saving === 1 ? 'writing' : 'failed', base: r.base, generation: r.generation };
}

const delay = (ms: number): Promise<void> => new Promise((done) => setTimeout(done, ms));

/**
 * Destructive: saves a selection, Mod memory and safe mode (config.set), as flipping the toggles on the device does; it
 * builds nothing (the next boot does). The device first reads its package list (busy: sent again for up to readMs). A
 * selection it cannot build is an XyfError with code 'refused' and the device's reason as its message; on success it
 * resolves with config.get once the record is written.
 */
export async function configSet(client: Client, request: InputsRequest, options: CallOptions & { readMs?: number; pollMs?: number } = {}): Promise<SavedInputs> {
  const flags = (request.expanded ? 1 : 0) | (request.safe ? 2 : 0) | (request.unverified ? 4 : 0);
  const fields = { version: 1, flags, mods: request.mods.join(',') };
  const body = encodeConfigSetRequest({ ...fields, sha256: new Uint8Array(32) }).subarray(0, -32);
  const confirmed = { ...fields, sha256: await sha256(body) };
  const pollMs = options.pollMs ?? 200, until = Date.now() + (options.readMs ?? 60000);
  for (;;) {
    try {
      await client.call('config.set', confirmed, options);
      break;
    } catch (error) {
      if (isXyfError(error, 'busy') && Date.now() < until) { await delay(pollMs); continue; }
      if (isXyfError(error, 'refused')) {
        const reason = String(error.fields?.['reason'] ?? '') || 'the selection cannot be built';
        throw new XyfError('refused', reason, { command: 'config.set', status: error.status, fields: error.fields, cause: error });
      }
      throw error;
    }
  }
  for (const writeUntil = Date.now() + 30000; ;) {
    const saved = await configGet(client, options).catch((error: unknown) => { if (isXyfError(error, 'busy')) return null; throw error; });
    if (saved && saved.write !== 'writing') return saved;
    if (Date.now() > writeUntil) throw new XyfError('timeout', 'config.set: the selection was not written in time', { command: 'config.set' });
    await delay(pollMs);
  }
}
