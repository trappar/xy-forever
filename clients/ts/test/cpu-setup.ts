// Vitest setup file (setupFiles here and in web/): each test's CPU time (test/cpu.ts) in its meta, for
// test/run-budget.ts (TaskMeta there): its process's from its first beforeEach to its last afterEach, plus what
// countSim adds.
import { afterEach, beforeAll, beforeEach } from 'vitest';
import { DrivenCpu, ownCpuMs } from './cpu.ts';
import { control } from './relay.ts';
import type { SimInfo } from './simulators.ts';

let start = 0;
beforeEach(() => {
  start = ownCpuMs();
});
afterEach(({ task }) => {
  task.meta.cpuMs = ownCpuMs() - start + (task.meta.drivenCpuMs ?? 0);
});

/** Counts the simulator's CPU time (its board and processors) in each test of the calling file or describe block;
 * returns sim. */
export function countSim<T extends SimInfo | null>(sim: T): T {
  if (!sim) return sim;
  let board = 0;
  let meter: DrivenCpu | null = null;
  beforeAll(async () => {
    board = Number((await control(sim.control, 'status'))['pid']);
  });
  beforeEach(async () => {
    meter = new DrivenCpu([board], true);
    await meter.start();
  });
  afterEach(async ({ task }) => {
    task.meta.drivenCpuMs = (task.meta.drivenCpuMs ?? 0) + (await meter!.stop());
  });
  return sim;
}
