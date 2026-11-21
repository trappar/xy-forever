// A file command answered `busy` (#178): what the OP-XY is busy with, and a bounded wait while that is only its
// write window closing. `busy` is decided before a command runs (the device's job_blocks), so sending it again is safe.
// job.status is answered while busy: `busy` itself while a package session or a change on the Mods page holds the
// storage; a job that has not ended (phase below 8) is a mod build or content job; else (no job, or one that ended) B
// is still closing A's write window (a yaffs checkpoint: 0.3 s to about 17 s on the device), or an idle collection,
// which the command stops, yields.
import type { CallOptions, Client } from './client.ts';
import { XyfError, isXyfError } from './errors.ts';

/** What a `busy` answer meant, in the error's `fields.busy`: fileReason words it. */
export type BusyCause = 'job' | 'session' | 'closing';

/** How long a closing write window is waited out, in all, and the first and longest pause between tries. */
export const BUSY_WAIT = { totalMs: 30_000, firstMs: 50, longestMs: 1_000 } as const;

/** Asks job.status what a `busy` answer meant. */
export async function busyCause(client: Client, options: CallOptions = {}): Promise<BusyCause> {
  try {
    const status = await client.call('job.status', { job: 0 }, { signal: options.signal });
    return status.phase < 8 ? 'job' : 'closing';
  } catch (error) {
    if (isXyfError(error, 'busy')) return 'session';
    if (isXyfError(error, 'not_found')) return 'closing';
    throw error;
  }
}

/**
 * Runs send; while it answers `busy` only because the write window is closing, waits (doubling pauses, BUSY_WAIT)
 * and sends again. Any other `busy` (a job, a package session), or one that outlasts the wait, is thrown with its
 * cause in `fields.busy`.
 */
export async function retryBusy<T>(client: Client, send: () => Promise<T>, options: CallOptions = {}): Promise<T> {
  const until = Date.now() + BUSY_WAIT.totalMs;
  for (let pause: number = BUSY_WAIT.firstMs; ; pause = Math.min(pause * 2, BUSY_WAIT.longestMs)) {
    try {
      return await send();
    } catch (error) {
      if (!isXyfError(error, 'busy') || error.status === null) throw error;
      const cause = await busyCause(client, options);
      if (cause !== 'closing' || Date.now() + pause > until) {
        throw new XyfError('busy', error.message, { command: error.command, status: error.status,
          fields: { ...error.fields, busy: cause }, cause: error });
      }
      await new Promise((resolve) => setTimeout(resolve, pause));
      if (options.signal?.aborted) throw new XyfError('aborted', 'cancelled', { cause: options.signal.reason });
    }
  }
}
