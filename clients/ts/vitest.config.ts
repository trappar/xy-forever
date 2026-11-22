// pnpm test: every spec in Node, the simulators started once for the run (test/simulators.ts). Budgets (enforced,
// test/run-budget.ts, on CPU time: test/cpu-setup.ts): each test's and the sum of the run's, about twice an unloaded
// run's and at least 1.5x a run's beside `mise run test-all`; the hang bounds, on wall time: each test and hook
// (testTimeout, hookTimeout) and the run (hangMs). The forks pool (Vitest's default): a test's process runs only its
// own file, so its CPU time is that test's.
import { defineConfig } from 'vitest/config';
import RunBudget from './test/run-budget.ts';

export default defineConfig({
  test: {
    include: ['test/**/*.spec.ts'],
    globalSetup: ['test/simulators.ts'],
    setupFiles: ['test/cpu-setup.ts'],
    testTimeout: 10_000,
    hookTimeout: 10_000,
    reporters: ['default', new RunBudget({ runMs: 15_000, testMs: 4_000, hangMs: 30_000 })],
  },
});
