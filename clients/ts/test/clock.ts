// Fake time for the Web Serial specs (chooser and buffer.spec.ts): a wait that is meant to run out (a silent port's
// probe, a reply that never comes) costs no real time. Only setTimeout and Date are fake; the simulator, the relay
// and the streams run in real time. A spec runs fake time forward only while it knows no real reply is on its way
// (a request timer firing early would resend or fail what the simulator was about to answer).
import { vi } from 'vitest';

export function fakeTime(): void {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
}

/** Runs fake time forward one timer at a time, real I/O running between, until done() holds; only while ready(). */
export async function timersUntil(
  done: () => boolean,
  ready: () => boolean | Promise<boolean> = () => true,
): Promise<void> {
  while (!done()) {
    if (vi.getTimerCount() > 0 && (await ready())) await vi.advanceTimersToNextTimerAsync();
    else await new Promise((ok) => setImmediate(ok));
  }
}

/** Runs fake time forward until work settles (work that ends in a timeout), once ready() holds. */
export async function expire<T>(work: Promise<T>, ready?: () => boolean | Promise<boolean>): Promise<T> {
  let settled = false;
  work.then(
    () => (settled = true),
    () => (settled = true),
  );
  await timersUntil(() => settled, ready);
  return work;
}
