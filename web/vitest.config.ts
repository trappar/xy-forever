// pnpm test: the app's unit tests in Node (test/). Budgets (enforced, the client's test/run-budget.ts, on CPU time):
// each test's and the sum of the run's, about twice an unloaded run's; the hang bounds, on wall time: each test and
// hook, and the run (hangMs).
import { defineConfig } from 'vitest/config';
import RunBudget from 'xy-forever-client/test/run-budget.ts';
import { alias } from './vite.config.ts';

export default defineConfig({
  resolve: { alias },
  test: {
    include: ['test/**/*.test.ts'],
    environment: 'node',
    setupFiles: ['xy-forever-client/test/cpu-setup.ts'],
    testTimeout: 2_000,
    hookTimeout: 5_000,
    reporters: ['default', new RunBudget({ runMs: 700, testMs: 300, hangMs: 15_000 })],
  },
});
