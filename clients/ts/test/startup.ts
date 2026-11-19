// Opt-in clean-startup fixture: HELLO alone does not finish background recovery.
import * as v1 from '../src/v1.ts';

export async function waitStartupReady(hello: string, probe: (remainingMs: number) => Promise<number>,
  timeoutMs = 5000, now = Date.now): Promise<void> {
  const peer = v1.parseHello(hello);
  // Older peers retain the original exchange and assertions, without this optional probe.
  if (peer.preV1 || !peer.answers(v1.TYPE['storage.space'])) return;
  const deadline = now() + timeoutMs;
  for (;;) {
    const remaining = deadline - now();
    if (remaining <= 0) throw new Error('startup readiness: storage.space stayed busy until the deadline');
    const status = await probe(remaining);
    if (status === v1.STATUS.ok) return;
    if (status !== v1.STATUS.busy) throw new Error(`startup readiness: storage.space: ${v1.statusName(status)}`);
  }
}
