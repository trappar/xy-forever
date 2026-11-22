// pnpm test: every spec in Node, the simulators started once for the run (test/simulators.ts). Budgets (enforced,
// test/run-budget.ts): each test's and the run's, about twice an unloaded run's; the hang bounds: each test and hook
// (testTimeout, hookTimeout) and the run (hangMs).
import { defineConfig } from 'vitest/config';
import RunBudget from './test/run-budget.ts';

export default defineConfig({
  test: {
    include: ['test/**/*.spec.ts'],
    globalSetup: ['test/simulators.ts'],
    testTimeout: 10_000,
    hookTimeout: 10_000,
    reporters: ['default', new RunBudget({ runMs: 8_000, testMs: 2_500, hangMs: 30_000 })],
  },
});
