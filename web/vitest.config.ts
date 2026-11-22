// pnpm test: the app's unit tests in Node (test/). Budgets: each test and hook, and the whole run (the client's
// run-budget reporter); the run takes under 1 s.
import { defineConfig } from 'vitest/config';
import RunBudget from 'xy-forever-client/test/run-budget.ts';
import { alias } from './vite.config.ts';

export default defineConfig({
  resolve: { alias },
  test: {
    include: ['test/**/*.test.ts'],
    environment: 'node',
    testTimeout: 2_000,
    hookTimeout: 5_000,
    reporters: ['default', new RunBudget(15_000)],
  },
});
