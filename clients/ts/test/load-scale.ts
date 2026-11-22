// How far this machine stretches the test budgets (test/run-budget.ts, web/e2e/budgets.ts), as the Python suite's
// probe does (tests/conftest.py). The budgets are about twice an unloaded run's times on the reference machine (an
// Apple M5 Max). Slower hardware: a fixed workload (PROBE) takes longer than PROBE_MS. A busy machine: a run beside a
// full Python suite (all cores taken, the 1-minute load average about the core count) took about 2.75x as long, so
// twice the load per core scales them too. Never below 1; the larger of the two, at the run's start or end (the
// larger). XYF_BUDGETS_SCALE=N fixes the scale.
import { createHash } from 'node:crypto';
import { availableParallelism, loadavg } from 'node:os';

const PROBE_MS = 15; // the probe on the reference machine, idle

/** Wall ms of a fixed workload (hashing), its fastest of three (the first pays for the JIT). */
function probeMs(): number {
  let best = Infinity;
  for (let k = 0; k < 3; k++) {
    const start = performance.now();
    let total = 0;
    for (let i = 0; i < 20_000; i++) {
      const d = createHash('sha256').update(`${i}:${total}`).digest();
      total = (total + d[0]! + d.length) & 0xffff;
    }
    best = Math.min(best, performance.now() - start);
  }
  return best;
}

export function loadScale(): number {
  const fixed = process.env['XYF_BUDGETS_SCALE'];
  if (fixed) return Number(fixed);
  return Math.max(1, probeMs() / PROBE_MS, (2 * loadavg()[0]!) / availableParallelism());
}
