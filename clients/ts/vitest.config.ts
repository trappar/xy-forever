// pnpm test: every spec in Node, the simulators started once for the run (test/simulators.ts). Budgets: each test
// and hook, and the whole run (test/run-budget.ts); the run takes about 3.5 s.
import { defineConfig } from 'vitest/config';
import RunBudget from './test/run-budget.ts';

export default defineConfig({
  test: {
    include: ['test/**/*.spec.ts'],
    globalSetup: ['test/simulators.ts'],
    testTimeout: 10_000,
    hookTimeout: 10_000,
    reporters: ['default', new RunBudget(30_000)],
  },
});
