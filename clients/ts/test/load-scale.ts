// How far this machine stretches the test budgets (test/run-budget.ts, web/e2e/budgets.ts), as the Python suite's
// probe does (tests/conftest.py). The budgets are about twice an unloaded run's times on the reference machine (an
// Apple M5 Max). Slower hardware: a fixed workload (the probe) takes longer than PROBE_MS. A busy machine: the share of
// every core's time spent busy over the run, this run's own work included. Beside a full Python suite (every core
// busy) a run took up to 2.75x as long (a test that starts Python, 5x), so the budgets scale by BUSY times that share.
// Never below 1. XYF_BUDGETS_SCALE=N fixes the scale.
import { createHash } from 'node:crypto';
import { cpus } from 'node:os';

const PROBE_MS = 15; // the probe on the reference machine, idle
const BUSY = 3;

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

/** Every core's [busy, total] ms so far. */
function cpuMs(): [number, number] {
  let busy = 0,
    total = 0;
  for (const { times } of cpus()) {
    busy += times.user + times.nice + times.sys + times.irq;
    total += times.user + times.nice + times.sys + times.irq + times.idle;
  }
  return [busy, total];
}

/** Call at the run's start; the function it returns gives the budgets' scale for the run so far. */
export function loadMeter(): () => number {
  const fixed = process.env['XYF_BUDGETS_SCALE'];
  if (fixed) return () => Number(fixed);
  const slower = probeMs() / PROBE_MS;
  const [busy0, total0] = cpuMs();
  return () => {
    const [busy, total] = cpuMs();
    return Math.max(1, slower, total > total0 ? (BUSY * (busy - busy0)) / (total - total0) : 1);
  };
}
